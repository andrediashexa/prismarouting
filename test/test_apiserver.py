#!/usr/bin/env python3
"""
The management API's core (#532), driven without gRPC: the VTY's output read
into diagnostics, the lines an API call becomes, the sessions in their own
processes, the commit that refuses a moved base, the readers and the
telemetry arithmetic. lab/api-grpc.sh drives the gRPC half against a box.
"""
import io
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src", "prismash"))
import apiserver as A  # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")
    else:
        print(f"  [ ok ] {msg}")


print("=== parse_output: the VTY's refusals as diagnostics ===")
OUT = ("error: pool 'RFC6598': inside and outside required\n"
       "warning: dynamic, and nothing exports its block events. The exported\n"
       "    record is the only evidence\n"
       "    nat / cgnat / pool RFC6598\n"
       "error: candidate did not pass validation; nothing applied\n"
       "note: snmp: something\n")
d = A.parse_output(OUT)
check([x["severity"] for x in d] == ["error", "warning", "error", "note"],
      f"one diagnostic per error/warning/note line: {[x['severity'] for x in d]}")
check(d[1]["path"] == ["nat", "cgnat", "pool RFC6598"]
      and "record is the only evidence" in d[1]["message"],
      f"an indented path becomes the path, other indented text the message: {d[1]}")
check(d[0]["message"] == "pool 'RFC6598': inside and outside required",
      "the message without its 'error: '")
L = A.parse_output("error: refusing to load this file\n    line 4: frobnicate\n")
check(L[0]["line"] == "frobnicate", f"a 'line N:' row becomes the line: {L}")
check(A.parse_output("nothing to say\n") == [], "plain output has no diagnostics")

print("=== denied, revision_of, config_diff ===")
check(A.denied("error: commit: permission denied for a read-only account")
      and not A.denied("error: pool x"), "the VTY's role refusal is recognised")
check(A.revision_of("a\n") == A.revision_of("a\n") != A.revision_of("b\n")
      and len(A.revision_of("a")) == 16, "equal texts, equal revisions")
dd = A.config_diff("hostname a\n", "hostname b\n")
check("-hostname a" in dd and "+hostname b" in dd and "running now" in dd,
      "the difference a moved base is refused with")

print("=== edit_lines, commit_line, show_allowed ===")
check(A.edit_lines(["nat", "cgnat", "pool P"], ["block-size 256"])
      == ["top", "nat", "cgnat", "pool P", "block-size 256", "top"],
      "an edit enters the path from the top and returns to it")
check(A.commit_line() == "commit", "a plain commit")
check(A.commit_line(comment="via api", confirmed_minutes=5, remap=True)
      == "commit remap confirmed 5 comment via api",
      f"the gate words, then confirmed, then the comment: {A.commit_line(comment='via api', confirmed_minutes=5, remap=True)}")
check(A.commit_line(comment="was confirmed ok") == 'commit comment "was confirmed ok"',
      "a comment with 'confirmed' in it is quoted, as the VTY needs")
check(A.commit_line(cutoff=True, diverge=True) == "commit cutoff diverge",
      "each acknowledgement its own word")
check(A.show_allowed("show bgp summary") and A.show_allowed("ping 1.1.1.1")
      and A.show_allowed("show running-config | include hostname"),
      "reads are allowed, filters too")
check(not A.show_allowed("configure") and not A.show_allowed("start shell")
      and not A.show_allowed("save") and not A.show_allowed("")
      and not A.show_allowed("system reboot")
      and not A.show_allowed("monitor interface pg0"),
      "nothing that changes the box or opens a shell")

print("=== account_from_auth: the client certificate's Common Name ===")
check(A.account_from_auth({"x509_common_name": [b"alice"]}) == "alice",
      "the CN is the account")
check(A.account_from_auth({"x509_common_name": [b"a", b"b"]}) is None
      and A.account_from_auth({}) is None
      and A.account_from_auth({"x509_common_name": [b"-rf"]}) is None
      and A.account_from_auth({"x509_common_name": [b"../etc"]}) is None,
      "none, two, or one no account could be called is no account")

print("=== the readers' parsers ===")
V = A.parse_version("Prisma Routing 0.149.0 (abcd)\n  dataplane: vpp v26.06-release built\n"
                    "  plugin:    0.149.0 (abcd)\n  routing:   FRRouting 10.7.1 (x)\n"
                    "  api:       one connection (vpp_papi)\n")
check(V == {"release": "0.149.0", "plugin_build": "0.149.0 (abcd)",
            "vpp_version": "v26.06-release", "frr_version": "10.7.1",
            "api_transport": "vpp_papi", "dataplane_current": True},
      f"show version read into VersionInfo: {V}")
check(A.parse_version("Prisma Routing 0.149.0 (a)\n  plugin: 0.148.2 (b)\n")
      ["dataplane_current"] is False, "a plugin from another release is not current")
C = A.parse_cpu("configured...\nthis boot:\n  VPP main thread       core 2\n"
                "  VPP workers           3-7\n  control plane held to 0-1\n")
check(C == {"control_plane_cpus": "0-1", "main_thread_cpu": "2",
            "worker_cpus": "3-7", "workers": 5}, f"show system cpu's this boot: {C}")
check(A.cpu_count("0-1,7") == 3 and A.cpu_count("") == 0 and A.cpu_count("x") == 0,
      "a CPU list counted")
check(A.subscriber_blocks("blocks: 200.150.0.17 1024-1535, 200.150.0.17:1536-2047")
      == [{"public_address": "200.150.0.17", "first_port": 1024, "last_port": 1535},
          {"public_address": "200.150.0.17", "first_port": 1536, "last_port": 2047}],
      "the port blocks in a subscriber's screen")


class R:
    def __init__(self, rows):
        self.ok = True
        self.fields = {"rows": rows}


class Cfg:
    def __init__(self):
        self.addresses = {"pg0": ["100.64.3.254/22"]}
        self.descriptions = {"pg0": "subs"}

        class P:
            mode, policy, block_size = "dynamic", "interleaved", "256"
            inside, outside = ["100.64.0.0/22"], ["200.150.0.16/30"]
        self.pools = {"nb": P()}

    def text(self):
        return "hostname x\n"


class FakeShell:
    """The parts of prismash.Shell the core asks for."""

    def __init__(self):
        self.failed = 0
        self.mode = "operational"
        self.candidate = None
        self.remap, self.cutoff, self.diverge = ["s1"], [], []
        self.fed = []
        self.cfg = Cfg()

        class Api:
            def dump(_, msg, args):
                return {"sw_interface_dump": R([
                    {"interface_name": "local0", "flags": ""},
                    {"interface_name": "tap4096", "flags": "IF_STATUS_API_FLAG_ADMIN_UP"},
                    {"interface_name": "pg0", "sw_if_index": 1,
                     "flags": "IF_STATUS_API_FLAG_ADMIN_UP|IF_STATUS_API_FLAG_LINK_UP",
                     "mtu": [9000, 0, 0, 0], "l2_address": "02:fe:00:00:00:01"}]),
                    "bpa_pool_dump": R([{"name": "nb", "capacity": 6, "guaranteed": 3}]),
                    "sw_bond_interface_dump": R([{"interface_name": "BondEthernet0",
                                                  "sw_if_index": 3,
                                                  "mode": "BOND_API_MODE_LACP"}]),
                    "sw_member_interface_dump": R([{"interface_name": "m1", "is_active": True},
                                                   {"interface_name": "m2", "is_active": False}]),
                }[msg]
        self.api = Api()

        class Frr:
            def show(_, cmd):
                import json
                return 0, json.dumps({
                    "show bgp vrf all summary json": {"default": {"ipv4Unicast": {"peers": {
                        "10.0.0.2": {"remoteAs": 65001, "state": "Established",
                                     "peerUptimeMsec": 61000, "pfxRcd": 3, "desc": "up"}}},
                        "ipv6Unicast": {"peers": {"10.0.0.2": {}}}}},
                    "show ip ospf neighbor json": {"neighbors": {"10.0.0.2": [
                        {"ifaceAddress": "10.0.0.2", "ifaceName": "pg0", "nbrState": "Full/DR"}]}},
                    "show ipv6 ospf6 neighbor json": {"neighbors": [
                        {"neighborId": "2.2.2.2", "interfaceName": "pg1", "state": "Full"}]},
                    "show isis neighbor json": {"areas": [{"circuits": [
                        {"adj": "upstream", "interface": {"name": "pg0"},
                         "state": "Up", "level": 2}]}]},
                }.get(cmd, {}))
        self.frr = Frr()

        class Rb:
            def list(_):
                return [(0, "2026-10-01T00:00:00Z", "via api")]
        self.rb = Rb()

        class Confirm:
            def read(_):
                return {"deadline": 1700000000}
        self.confirm = Confirm()

    def feed(self, line):
        self.fed.append(line)
        print(f"fed {line}")
        if line == "bad":
            print("error: not a statement")
            self.failed += 1
        if line == "configure":
            self.mode = "config"
            self.candidate = self.cfg

    def running_config(self):
        return self.cfg

    def device_linux_names(self):
        return {"pg0": "pg0-linux"}

    def device_vrrp_state(self):
        return [{"interface": "pg0", "vr_id": 7, "advertising": 150,
                 "state": "master", "addresses": ["100.64.3.1"]}]


sh = FakeShell()
print("=== feed_lines and screen: the VTY fed, stopped at its first refusal ===")
r = A.feed_lines(sh, ["configure", "bad", "never"])
check(r["failed"] and r["at"] == 1 and "never" not in sh.fed
      and "error: not a statement" in r["output"],
      f"a refused line stops the rest: {r}")
check(A.feed_lines(sh, ["top"]) == {"output": "fed top\n", "failed": False, "at": -1},
      "an accepted line")
check(A.screen(sh, "show version") == "fed show version\n", "a screen is what was printed")

print("=== the readers, over the VTY's own readers ===")
I = A.interfaces_of(sh)
check(len(I) == 1 and I[0]["name"] == "pg0" and I[0]["admin_up"] and I[0]["link_up"]
      and I[0]["mtu"] == 9000 and I[0]["addresses"] == ["100.64.3.254/22"]
      and I[0]["linux_name"] == "pg0-linux" and I[0]["description"] == "subs",
      f"an interface: the dataplane's state, the configuration's addresses: {I}")
check(A.interfaces_of(sh, ["other"]) == [], "only the interfaces asked for")
check(A.is_lcp_tap("tap4096") and not A.is_lcp_tap("pg0") and not A.is_lcp_tap("tapestry"),
      "is_lcp_tap: the VTY's own predicate for a linux_cp TAP")
check(A.cfg_addresses(sh.cfg, "pg0") == ["100.64.3.254/22"] and A.cfg_addresses(sh.cfg, "x") == [],
      "an interface's configured addresses")
P = A.pools_of(sh)
check(P == [{"name": "nb", "mode": "dynamic", "policy": "interleaved", "block_size": 256,
             "prefixes_inside": ["100.64.0.0/22"], "prefixes_outside": ["200.150.0.16/30"],
             "capacity": 6, "guaranteed": 3}], f"a pool with the dataplane's capacity: {P}")
B = A.bgp_of(sh)
check(B == [{"vrf": "default", "peer": "10.0.0.2", "remote_as": 65001, "state": "Established",
             "uptime_seconds": 61, "prefixes_received": 3, "description": "up"}],
      f"a BGP peer once, though two families list it: {B}")
O = A.ospf_of(sh)
check([o["protocol"] for o in O] == ["ospf", "ospf6"] and O[0]["state"] == "Full/DR",
      f"OSPF and OSPFv3 neighbours: {O}")
check(A.isis_of(sh) == [{"system_id": "upstream", "interface": "pg0", "state": "Up",
                         "level": "2"}], "an IS-IS adjacency")
check(A.frr_json(sh, "show nothing json") == {}, "an FRR screen that says nothing is {}")
check(A.vrrp_of(sh) == [{"interface": "pg0", "vr_id": 7, "family": 4, "state": "master",
                         "priority": 150, "addresses": ["100.64.3.1"]}],
      "a VRRP router, with the priority it advertises")
check(A.bundles_of(sh) == [{"name": "BondEthernet0", "mode": "lacp",
                            "members": ["m1", "m2"], "active_members": ["m1"]}],
      "a bundle and which members are active")

print("=== uptime_fields: GetSystem's clocks (#534) ===")
_f = A.uptime_fields({"boot": 100, "dataplane": 700, "control_plane": None, "vty": 90,
                      "release_installed": 50, "boot_reason": "system reboot by root",
                      "dataplane_restarted": True}, lambda e: ("ts", e))
check(_f == {"boot_time": ("ts", 100), "dataplane_started": ("ts", 700),
             "vty_started": ("ts", 90), "release_installed_at": ("ts", 50),
             "last_boot_reason": "system reboot by root", "dataplane_restarted": True},
      f"a Timestamp for each clock read, none for one that was not: {_f}")
check(A.uptime_fields({}, lambda e: e) == {"last_boot_reason": "not recorded",
                                           "dataplane_restarted": False},
      "nothing read: not recorded, not restarted")

print("=== handle: what a worker answers ===")
check(A.handle(sh, "candidate", None) is sh.cfg.text() or A.handle(sh, "candidate", None) == "hostname x\n",
      "the candidate's text")
check(A.handle(sh, "running", None) == "hostname x\n", "the running text")
check(A.handle(sh, "gates", None) == {"remap": ["s1"], "cutoff": [], "diverge": []},
      "the gated words a check filled")
check(A.handle(sh, "revisions", None)[0][2] == "via api", "the revisions")
check(A.handle(sh, "confirm", None)["deadline"] == 1700000000, "the confirm window")
check(A.handle(sh, "pools", None)[0]["name"] == "nb", "a reader by name")
# The host's name through the VTY's own reader (#516): one door question, where
# `show running-config | include ^hostname` answered nothing on the X520 bench.
_sys_was = getattr(sh, "sys", None)
sh.sys = type("FakeSys", (), {"read_hostname": lambda self: "edge1"})()
check(A.handle(sh, "hostname", None) == "edge1",
      "the hostname reader answers the host's name, as the prompt reads it")
sh.sys = _sys_was
try:
    A.handle(sh, "rm -rf", None)
    check(False, "an unknown operation is refused")
except ValueError:
    check(True, "an unknown operation is refused")

print("=== Worker and Sessions: each account's own process ===")
me = __import__("pwd").getpwuid(os.getuid()).pw_name
A.become(me)
check(os.environ.get("USER") == me, "become() as this account sets its environment")


def factory():
    return FakeShell()


class W(A.Worker):
    def __init__(self, account):
        super().__init__(account, shell_factory=factory, timeout=20)


w = W(me)
check(w.ask("running") == "hostname x\n", "a worker answers from its own Shell")
check(w.ask("feed", ["configure"])["failed"] is False, "and keeps its state between calls")
check(w.ask("candidate") == "hostname x\n", "the candidate the configure made")
try:
    w.ask("nonsense")
    check(False, "a request the worker cannot answer is a WorkerError")
except A.WorkerError:
    check(True, "a request the worker cannot answer is a WorkerError")
w.close()
check(not w.proc.is_alive(), "close() ends the process")
try:
    A.Worker("no-such-account-532", shell_factory=factory, timeout=5)
    check(False, "an account the box does not have starts no session")
except A.WorkerError as e:
    check("no-such-account-532" in str(e), f"an account the box does not have starts no session: {e}")

S = A.Sessions(worker=W, idle=60, ops_idle=60)
cid, base, text, out = S.open(me)
check(base == "hostname x\n" and text == "hostname x\n" and "fed configure" in out,
      "open: the base, the candidate and what configure printed")
check(S.get(me, cid)["account"] == me, "get: the account's own candidate")
try:
    S.get("somebody-else", cid)
    check(False, "nobody else's")
except KeyError:
    check(True, "a candidate is nobody else's")
r = A.commit(S, me, cid, comment="via api")
check(r["ok"] and r["moved"] == "" and "fed commit comment via api" in r["output"]
      and r["confirm"]["deadline"] == 1700000000, f"commit runs the VTY's commit: {r}")
S.get(me, cid)["base"] = "hostname old\n"
r = A.commit(S, me, cid)
check(not r["ok"] and "-hostname old" in r["moved"],
      "a candidate whose base the running configuration moved past is refused with the difference")
ow = S.ops_worker(me)
check(S.ops_worker(me) is ow, "one operational process per account, reused")
cw = S.console_worker(me)
check(S.console_worker(me) is cw and cw is not ow,
      "the console (Show) has a process of its own, reused, apart from the "
      "operational one - a long ping no longer holds GetVersion behind it")
S.close(me, cid)
try:
    S.get(me, cid)
    check(False, "a closed candidate is gone")
except KeyError:
    check(True, "a closed candidate is gone")
S.idle = S.ops_idle = 0
cid2, *_ = S.open(me)
time.sleep(0.05)
S.reap()
check(cid2 not in S.candidates and me not in S.ops and me not in S.consoles,
      "reap closes what nobody used lately, the console's process included")


class Denied(A.Worker):
    def __init__(self, account):
        super().__init__(account, shell_factory=self.make, timeout=20)

    @staticmethod
    def make():
        s = FakeShell()
        orig = s.feed

        def feed(line):
            if line == "configure":
                print("error: configure: permission denied for a read-only account")
                s.failed += 1
                return
            orig(line)
        s.feed = feed
        return s


try:
    A.Sessions(worker=Denied).open(me)
    check(False, "an account without the role is refused configure")
except PermissionError as e:
    check("permission denied" in str(e), "an account without the role is refused configure, in the VTY's words")

print("=== telemetry: counters summed over threads ===")
check(A.sum_simple([[1, 2], [10, 20]], 1) == 22 and A.sum_simple([[1]], 3) == 0,
      "a simple counter summed over threads")
check(A.sum_combined([[{"packets": 1, "bytes": 64}], [{"packets": 2, "bytes": 128}]], 0, "bytes")
      == 192, "a combined counter's field summed over threads")


class Combined:
    """vpp_papi's combined counter: subscriptable, printed like a dict, not one."""

    def __init__(self, packets, bytes_):
        self._d = {"packets": packets, "bytes": bytes_}

    def __getitem__(self, k):
        return self._d[k]


check(A.sum_combined([[Combined(1, 64), Combined(2000, 120000)]], 1, "packets") == 2000,
      "a counter that is not a dict is read by subscript")


class Attr:
    packets, bytes = 3, 9


check(A.sum_combined([[Attr()]], 0, "bytes") == 9, "and one with attributes by name")
STATS = {"/if/names": ["local0", "pg0", "tap4096"],
         "/if/rx": [[{"packets": 0, "bytes": 0}, {"packets": 5, "bytes": 300},
                     {"packets": 9, "bytes": 900}]],
         "/if/tx": [[{"packets": 0, "bytes": 0}, {"packets": 1, "bytes": 60},
                     {"packets": 9, "bytes": 900}]],
         "/if/rx-error": [[0, 2, 0]], "/if/tx-error": [[0, 0, 0]], "/if/drops": [[0, 1, 0]],
         "/bpa/pool/names": ["nb", ""], "/bpa/pool/subscribers": [[3, 9], [1, 0]],
         "/bpa/pool/ports": [[700, 0]], "/bpa/pool/sessions": [[40, 0]],
         "/bpa/pool/refused": [[2, 0]]}
S1 = A.interface_samples(STATS)
check(S1 == [{"name": "pg0", "counters": {"rx_bytes": 300, "tx_bytes": 60, "rx_packets": 5,
                                          "tx_packets": 1, "rx_errors": 2, "tx_errors": 0,
                                          "drops": 1}}], f"one sample per interface, local0 and the taps left out: {S1}")
check(A.interface_samples(STATS, ["other"]) == [], "only the interfaces asked for")
tot, pools = A.cgnat_totals(STATS)
check(tot == {"subscribers": 4, "ports_in_use": 700, "sessions": 40, "refusals": 2}
      and list(pools) == ["nb"], f"the CGNAT's totals, a deleted pool's index left out: {tot}")
E = A.state_events({"commits": "1", "confirm_pending": "no"},
                   {"commits": "2", "confirm_pending": "yes", "last_commit_by": "root",
                    "confirm_deadline": "1700000000", "reverts": "1"})
check([e["kind"] for e in E] == ["commit", "confirm-pending", "rolled-back"]
      and E[0]["account"] == "root", f"the events between two readings: {E}")
check(A.state_events({"commits": "2"}, {"commits": "2"}) == [], "nothing changed, nothing said")
with tempfile.NamedTemporaryFile("w", delete=False) as f:
    f.write("commits 3\nlast_commit_by root\n")
check(A.read_state(f.name) == {"commits": "3", "last_commit_by": "root"}
      and A.read_state("/nonexistent/state") == {}, "the state file read, or {} without one")
os.unlink(f.name)


class PB:
    class Diagnostic:
        SEVERITY_UNSPECIFIED, SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_NOTE = 0, 1, 2, 3

        def __init__(self, **kw):
            self.__dict__.update(kw)


dg = A._diag_pb(PB, {"severity": "warning", "message": "m", "path": ["a"], "line": ""})
check(dg.severity == 2 and dg.message == "m" and dg.path == ["a"],
      "_diag_pb maps a diagnostic onto the message")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
