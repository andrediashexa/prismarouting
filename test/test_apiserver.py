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
                     "mtu": [9000, 0, 0, 0], "l2_address": "02:fe:00:00:00:01",
                     "link_speed": 10000000}]),
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

        class State:
            def read(_):
                return {"confirm_pending": "yes", "confirm_deadline": "1700000000",
                        "commit_by": "root", "commit_at": "1699999700",
                        "comment": "via api"}
        self.state = State()

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
check(A.link_speed_bps(0xFFFFFFFF) == 0 and A.link_speed_bps(None) == 0
      and A.link_speed_bps("x") == 0 and A.link_speed_bps(1_000_000) == 10 ** 9,
      "link_speed_bps: VPP's unknown speed (~0 kbps) and garbage are 0, a known one in bits (#545)")
check(I[0]["speed_bps"] == 10_000_000_000,
      f"the link speed in bits a second, from the dataplane's kbps (#533): {I[0]['speed_bps']}")
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
# Routing policy (#538): the Shell's own data methods, by name, with what
# the call carried - the policy, the request - handed through as it came.
_was = {k: getattr(sh, k, None) for k in ("policies_data",
                                          "policy_counters_data",
                                          "test_policy_data")}
sh.policies_data = lambda: [{"name": "UP"}]
sh.policy_counters_data = lambda name: {"asked": name}
sh.test_policy_data = lambda req: {"req": req}
check(A.handle(sh, "policies", None) == [{"name": "UP"}]
      and A.handle(sh, "policy_counters", "UP") == {"asked": "UP"}
      and A.handle(sh, "policy_counters", None) == {"asked": ""}
      and A.handle(sh, "test_policy", {"policy": "UP"})
      == {"req": {"policy": "UP"}}
      and A.handle(sh, "test_policy", None) == {"req": {}},
      "the policy readers: ListPolicies, GetPolicyCounters, TestPolicy")
for _k, _v in _was.items():
    if _v is None:
        delattr(sh, _k)
    else:
        setattr(sh, _k, _v)
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


check(A.start_method(None) == "spawn" and A.start_method(lambda: None) == "fork",
      "the box spawns an account's process (a fork of the gRPC server can "
      "inherit a held lock); a test's stand-in shell forks")


def factory():
    return FakeShell()


# #564: a detached run's log is this run's only, and a run that never wrote
# fails fast instead of waiting out the deadline.
_rd = tempfile.mkdtemp()
_rl = os.path.join(_rd, "dataplane.log")
with open(_rl, "w") as _f:
    _f.write("an old run\nprisma-api-exit 0\n")
os.utime(_rl, (100.0, 100.0))
check(A.run_since(_rl, 1000.0) is None and A.run_since(_rl, 50.0)["done"],
      "a previous run's finished log is not this run's result")
check(A.run_silent(None, 1000.0, 1021.0) and not A.run_silent(None, 1000.0, 1010.0)
      and not A.run_silent({"done": False}, 1000.0, 5000.0),
      "silent: no log past FIRST_WRITE_WAIT; a log, however slow, is not")
check("prisma-routing-api-dataplane" in A.silent_reason("prisma-routing-api-dataplane")
      and "journalctl -u" in A.silent_reason("x"),
      "and the reason names the unit and where to read why")


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

# #565: an account's workers follow the account. account_key() is its uid,
# gid and groups read now; a worker started under another key is replaced
# on the next call, and a candidate opened under it reads as expired.
class _Pw:
    pw_uid, pw_gid = 1000, 1000
check(A.account_key("x", getpwnam=lambda a: _Pw(),
                    getgrouplist=lambda a, g: [1000, 994, 997, 994])
      == (1000, 1000, (994, 997, 1000)),
      "the key: uid, gid and the sorted set of groups")
check(A.account_key("x", getpwnam=lambda a: (_ for _ in ()).throw(KeyError(a)))
      is None, "an account that does not exist has no key")
_S5 = A.Sessions(worker=W, idle=60, ops_idle=60)
_w1 = _S5.ops_worker(me)
check(_S5.ops_worker(me) is _w1 and _S5.current(_w1, me),
      "an unchanged account keeps its worker, which Sessions.current() says is current")
_c5, _, _, _ = _S5.open(me)
_real_key = A.account_key
A.account_key = lambda a, **k: ("changed",)
try:
    _w2 = _S5.ops_worker(me)
    check(_w2 is not _w1 and not _w1.proc.is_alive(),
          "a changed account gets a new worker, the old one closed (#565)")
    try:
        _S5.get(me, _c5)
        check(False, "a candidate of a changed account is not handed out")
    except KeyError:
        check(_S5.was_expired(me, _c5),
              "a candidate opened before the change reads as expired")
finally:
    A.account_key = _real_key
for _w in list(_S5.ops.values()):
    _w.close()

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
rw = S.route_worker(me)
check(S.route_worker(me) is rw and rw not in (ow, cw),
      "ListRoutes has a process of its own too (#533): a snapshot's reading "
      "does not share the operational one's interpreter")
S.close(me, cid)
try:
    S.get(me, cid)
    check(False, "a closed candidate is gone")
except KeyError:
    check(True, "a closed candidate is gone")
S.idle = S.ops_idle = S.route_idle = 0
cid2, *_ = S.open(me)
time.sleep(0.05)
S.reap()
check(cid2 not in S.candidates and me not in S.ops and me not in S.consoles
      and me not in S.routes,
      "reap closes what nobody used lately, the console's and the routes' "
      "processes included")


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
                   {"commits": "2", "confirm_pending": "yes", "commit_by": "root",
                    "confirm_deadline": "1700000000", "confirm_reverts": "1",
                    "revision": "0123456789abcdef", "comment": "via api"})
check([e["kind"] for e in E] == ["commit", "confirm-pending", "rolled-back"]
      and E[0]["account"] == "root", f"the events between two readings: {E}")
check(E[0]["revision"] == "" and E[0]["comment"] == "via api",
      "an event carries the comment the box recorded, and leaves the revision "
      "to the server, which reads it as the subscriber's account (#533)")
check(A.state_events({"commits": "1"}, {"commits": "2", "last_commit_by": "x"})[0]["account"] == ""
      and A.state_events({}, {"reverts": "3"}) == [],
      "read with the keys the VTY writes - commit_by, confirm_reverts - not the "
      "ones nothing wrote")
check(A.state_events({"commits": "2"}, {"commits": "2"}) == [], "nothing changed, nothing said")
with tempfile.NamedTemporaryFile("w", delete=False) as f:
    f.write("commits 3\nlast_commit_by root\n")
check(A.read_state(f.name) == {"commits": "3", "last_commit_by": "root"}
      and A.read_state("/nonexistent/state") == {}, "the state file read, or {} without one")
os.unlink(f.name)


print("=== #533: a commit's comment, recorded for the API's events ===")
import prismash as P  # noqa: E402



with tempfile.TemporaryDirectory() as _d:
    _st = os.path.join(_d, "config")
    _was_cfg = P.CONFIG_PATH
    P.CONFIG_PATH = os.path.join(_d, "none.conf")

    class _Cfg:
        def text(self):
            return "hostname r\n"
    _sh = type("S", (), {"state": P.ConfigState(_st)})()
    P.Shell.record_applied(_sh, "alice", _Cfg(), comment="two\nlines  here")
    _got = A.read_state(_st)
    P.CONFIG_PATH = _was_cfg
check(_got.get("comment") == "two lines here" and _got.get("commit_by") == "alice"
      and "revision" not in _got,
      f"record_applied writes the comment, on one line; the revision is the "
      f"server's to read: {_got}")
_reads = iter([{"commits": "4"}, {"commits": "5"}])
check(A.event_revision(lambda: "hostname r\n", {"commits": "4"}, lambda: next(_reads))
      == A.revision_of("hostname r\n")
      and A.event_revision(lambda: "x", {"commits": "4"}, lambda: next(_reads)) == "",
      "an event's revision is the running configuration's id as read - none "
      "when another commit landed while it was read")

print("=== #533: who am I ===")
sh.caps = {P.CAP_CGNAT}
W0 = A.whoami(sh)
check(W0["role"] == "cgnat-operator" and W0["can_configure_cgnat"]
      and not W0["can_configure"] and W0["account"] == me,
      f"the account and the VTY's role for it: {W0}")
sh.caps = set()
check(A.handle(sh, "whoami", None)["role"] == "read-only", "no capability is read-only")
sh.caps = {P.CAP_ALL, P.CAP_CGNAT}
check(A.whoami(sh)["can_configure"] and A.whoami(sh)["role"] == "administrator",
      "an administrator configures everything")

print("=== #533: FRR's route JSON, read as it streams ===")


def reader(text, size):
    data = text.encode()
    pos = [0]

    def read(n):
        chunk = data[pos[0]:pos[0] + min(n, size)]
        pos[0] += len(chunk)
        return chunk
    return read


ROUTES = ('{"10.0.0.0/24":[{"protocol":"bgp","selected":true,"distance":20,"metric":7,'
          '"installed":true,"vrfName":"default","prefix":"10.0.0.0/24","table":254,'
          '"uptime":"01:02:03","nexthops":[{"ip":"192.0.2.2","interfaceName":"d0",'
          '"active":true,"fib":true,"weight":1}]},{"protocol":"ospf","selected":false,'
          '"distance":110,"metric":20,"prefix":"10.0.0.0/24","nexthops":[]}]\n'
          ',"10.0.1.0/24":[{"protocol":"connected","prefix":"10.0.1.0/24",'
          '"selected":true,"nexthops":[{"directlyConnected":true,"interfaceName":"d0",'
          '"vrf":"red"}]}]\n,"\\u00e9":[1234567]\n}')
for size in (1, 7, 65536):
    items = list(A.json_object_items(reader(ROUTES, size), chunk=size))
    check([k for k, _ in items] == ["10.0.0.0/24", "10.0.1.0/24", "\u00e9"]
          and items[2][1] == [1234567] and len(items[0][1]) == 2,
          f"every key and value, read {size} byte(s) at a time")
check(list(A.json_object_items(reader("{}", 10))) == [], "an empty table")
try:
    list(A.json_object_items(reader("% Unknown command: show ip route vrf x json", 10)))
    check(False, "what is not a JSON object is refused")
except ValueError as e:
    check("Unknown command" in str(e), f"what is not a JSON object is refused, said: {e}")
try:
    list(A.json_object_items(reader('{"a":[1,2', 4)))
    check(False, "a cut answer is refused")
except ValueError:
    check(True, "an answer cut in a value is refused, not read as a short table")
check(A.frr_age("01:02:03") == 3723 and A.frr_age("1d02h03m") == 93780
      and A.frr_age("02w3d04h") == 1483200 and A.frr_age("never") == 0
      and A.frr_age(None) == 0, "FRR's uptime words in seconds")
R0 = A.route_row("default", 4, items[0][1][0])
check(R0 == {"vrf": "default", "prefix": "10.0.0.0/24", "family": 4, "protocol": "bgp",
             "distance": 20, "metric": 7, "selected": True, "installed": True,
             "next_hops": [{"address": "192.0.2.2", "interface": "d0", "active": True,
                            "fib": True, "weight": 1, "directly_connected": False,
                            "vrf": "", "blackhole": ""}],
             "table": 254, "age_seconds": 3723}, f"a route as ListRoutes sends it: {R0}")
R1 = A.route_row("default", 4, items[1][1][0])
check(R1["next_hops"][0]["vrf"] == "red" and R1["next_hops"][0]["directly_connected"],
      "a next hop in another VRF says which")
check(A.route_row("x", 6, {"nexthops": [{"blackhole": True}, {"reject": True}]})
      ["next_hops"][1]["blackhole"] == "reject", "a blackhole and a reject next hop")

print("=== #533: a ListRoutes request checked before vtysh sees it ===")
F, why = A.route_request({"prefix": "10.1.2.3/16", "protocol": "bgp", "page_size": 99999})
check(why is None and F["prefix"] == "10.1.0.0/16" and F["families"] == [4]
      and F["page_size"] == A.ROUTE_PAGE_MAX,
      f"a prefix sets the family and is normalised; the page is capped: {F}")
for bad in ({"vrf": "red; conf t"}, {"family": 5}, {"prefix": "nope"},
            {"prefix": "10.0.0.0/8", "family": 6}, {"protocol": "bgp json"}):
    check(A.route_request(bad)[0] is None, f"refused: {bad} ({A.route_request(bad)[1]})")
check(A.route_request({})[0]["families"] == [4, 6], "both families by default")
check(A.route_command("red", 6, "2001:db8::/32", "bgp")
      == "show ipv6 route vrf red 2001:db8::/32 longer-prefixes bgp json"
      and A.route_command("default", 4) == "show ip route vrf default json",
      "the vtysh command for one VRF and family")
F2, _ = A.route_request({"prefix": "10.0.0.0/8", "selected_only": True, "protocol": "bgp"})
check(A.route_keeps(F2, R0) and not A.route_keeps(F2, dict(R0, selected=False))
      and not A.route_keeps(F2, dict(R0, protocol="ospf"))
      and not A.route_keeps(F2, dict(R0, prefix="11.0.0.0/24"))
      and not A.route_keeps(F2, dict(R0, prefix="2001:db8::/32")),
      "the filters: selected, protocol, inside the prefix")
SUM = {"default": {"routes": [{"type": "bgp", "rib": 9, "fib": 8}]},
       "red": {"routes": [{"type": "connected", "rib": 1, "fib": 1}]}}
check(A.summary_counts(SUM, 4) == [
    {"vrf": "default", "family": 4, "protocol": "bgp", "rib": 9, "fib": 8},
    {"vrf": "red", "family": 4, "protocol": "connected", "rib": 1, "fib": 1}]
    and A.summary_counts({"routes": [{"type": "kernel", "rib": 2}]}, 6)[0]["vrf"] == "default",
    "zebra's summary per VRF, and one VRF's answer unwrapped")


class StreamProc:
    """vtysh's answer, as a process whose output is read."""
    def __init__(self, text, size=50):
        self.stdout = type("Out", (), {"read": staticmethod(reader(text, size)),
                                       "close": lambda self: None})()
        self.killed = False

    def wait(self):
        return 0

    def poll(self):
        return 0

    def kill(self):
        self.killed = True


def big_table(n, vrf="default"):
    return "{" + "\n,".join(
        f'"10.{i // 256}.{i % 256}.0/24":[{{"protocol":"{"bgp" if i % 2 else "static"}",'
        f'"selected":true,"prefix":"10.{i // 256}.{i % 256}.0/24","vrfName":"{vrf}",'
        f'"nexthops":[{{"ip":"192.0.2.2","interfaceName":"d0"}}]}}]'
        for i in range(n)) + "}"


asked = []
sh.frr_summary = {"show ip route vrf all summary json": SUM,
                  "show ipv6 route vrf all summary json": {}}
_frr_show = sh.frr.show
sh.frr.show = lambda cmd: (0, __import__("json").dumps(sh.frr_summary.get(cmd, {})))
sh.vtysh_stream = lambda cmd: (asked.append(cmd), StreamProc(
    big_table(2500) if "vrf default" in cmd and "ipv6" not in cmd
    else big_table(3, "red") if "vrf red" in cmd and "ipv6" not in cmd else "{}"))[1]
sh.route_page_wait = 20
check(A.route_summary(sh) == A.summary_counts(SUM, 4) and A.route_summary(sh, "red")[0]["vrf"] == "red",
      "GetRouteSummary: both families, every VRF or the one asked")

print("=== #533: ListRoutes pages one snapshot ===")
seen, token, pages, first = [], "", 0, None
while True:
    got = A.routes_page(sh, {"page_size": 1000, "page_token": token})
    check("error" not in got, f"page {pages}: {got.get('error', 'answered')}")
    if "error" in got:
        break
    first = first or got
    seen += [r["prefix"] + "@" + r["vrf"] for r in got["rows"]]
    pages += 1
    token = got["next"]
    if not token:
        break
check(len(seen) == 2503 and len(set(seen)) == 2503 and pages == 3,
      f"every route once, default's and red's, in {pages} pages: {len(seen)}")
check(got["total_known"] and got["total"] == 2503, "the last page knows the total")
check(asked[:2] == ["show ip route vrf default json", "show ipv6 route vrf default json"],
      f"default first, then the other VRFs, each family: {asked}")
asked.clear()
got = A.routes_page(sh, {"vrf": "red", "family": 4, "protocol": "bgp", "page_size": 10})
check([r["prefix"] for r in got["rows"]] == ["10.0.1.0/24"]
      and asked == ["show ip route vrf red bgp json"] and got["next"] == "",
      f"one VRF, one family, one protocol: {[r['prefix'] for r in got['rows']]} {asked}")
check(A.routes_page(sh, {"vrf": "blue"})["code"] == "not_found", "a VRF FRR has not is NOT_FOUND")
check(A.routes_page(sh, {"prefix": "x"})["code"] == "invalid", "a bad filter is refused")
check(A.routes_page(sh, {"page_token": "0123456789ab:0:0"})["code"] == "expired"
      and A.routes_page(sh, {"page_token": "garbage"})["code"] == "expired",
      "a token naming no snapshot this box holds")
snaps = A.route_snapshots(sh)
check(len(snaps) <= A.ROUTE_SNAPSHOTS, f"at most {A.ROUTE_SNAPSHOTS} snapshots held: {len(snaps)}")
_old = A.ROUTE_IDLE
A.ROUTE_IDLE = -1
check(A.route_snapshots(sh) == {}, "a snapshot nobody paged lately is dropped")
A.ROUTE_IDLE = _old

sh.vtysh_stream = lambda cmd: StreamProc('{"10.0.0.0/24":[{"prefix":"10.0.0.0/24",'
                                         '"protocol":"bgp"}]\n,"10.0.1.0/24":[{"pre')
got = A.routes_page(sh, {"page_size": 50})
nxt = A.routes_page(sh, {"page_token": got["next"]}) if got.get("next") else {}
check(len(got.get("rows", [])) >= 1 and got["next"] and nxt.get("code") == "unavailable",
      f"FRR stopping mid-table: the rows read, then the reason - never a short "
      f"table as the whole one: {got.get('next')!r} {nxt}")
sh.vtysh_stream = lambda cmd: StreamProc("% vtysh: zebra is not running")
check(A.routes_page(sh, {"page_size": 5}).get("code") == "unavailable",
      "vtysh refusing is UNAVAILABLE")

snap = A.RouteSnapshot([("default", 4, "x")], lambda r: True,
                       run=lambda cmd: StreamProc(big_table(450), 7))
rows, ni, no, more = snap.page(0, 0, 100, wait=20)
check(len(rows) == 100 and ni == 100 and more, "a page as soon as the snapshot has it")
rows2, ni2, no2, more2 = snap.page(ni, no, 1000, wait=20)
check(len(rows2) == 350 and not more2 and rows2[0]["prefix"] == "10.0.100.0/24",
      f"the next page from the byte the last ended at: {rows2[0]['prefix']}")
snap._write(["{}"])
check(snap.rows == 451, "_write appends rows and counts them")
snap.close()
check(snap.file.closed, "close() drops the snapshot's file")
_vt = P.VTYSH
P.VTYSH = "echo"
_p = A.vtysh_stream("show ip route json")
check(_p.stdout.read().decode().strip() == "-c show ip route json", "vtysh_stream runs vtysh -c")
_p.wait()
P.VTYSH = _vt
sh.frr.show = _frr_show

print("=== #533: memory, the workers' load ===")
with tempfile.NamedTemporaryFile("w", delete=False) as f:
    f.write("MemTotal:       16000000 kB\nMemAvailable:    6000000 kB\n"
            "HugePages_Total:       4\nHugepagesize:    1048576 kB\n")
M = A.memory_of(f.name)
check(M == {"memory_total_bytes": 16000000 * 1024, "memory_used_bytes": 10000000 * 1024,
            "memory_hugepages_bytes": 4 * 1048576 * 1024},
      f"meminfo in bytes, used as total less available: {M}")
os.unlink(f.name)
check(A.memory_of("/nonexistent") == {"memory_total_bytes": 0, "memory_used_bytes": 0,
                                       "memory_hugepages_bytes": 0}, "nothing read, zeros")
check(A.cpu_list("3-5,9") == [3, 4, 5, 9] and A.cpu_list("") == [] and A.cpu_list("x") == [],
      "a CPU list in order")
with tempfile.NamedTemporaryFile("w", delete=False) as f:
    f.write("cpu {\n  main-core 2\n  corelist-workers 3-7\n}\n")
check(A.startup_worker_cpus(f.name) == "3-7" and A.startup_worker_cpus("/none") == "",
      "the workers' cores from startup.conf")
os.unlink(f.name)
WS = {"/sys/num_worker_threads": 2, "/sys/vector_rate_per_worker": [[0, 12, 3]],
      "/sys/loops_per_worker": [[0, 3000000, 6000000]]}
check(A.per_worker(WS, "/sys/loops_per_worker", 2) == [3000000.0, 6000000.0]
      and A.per_worker(WS, "/sys/none", 2) == [0, 0],
      "a per-thread vector, the main thread left out")
RT = """Thread 0 vpp_main (lcore 12)
Time 3.3, 10.000000 sec internal node vector rate 0.00 loops/sec 423047.02
             Name                 State         Calls          Vectors        Suspends      Packet-Clocks   Vectors/Call
api-rx-from-ring               "any wait"                0               0               2          1.19e7            0.00
ip4-input                        active               10            1000               0          5.00e1          100.00
---------------
Thread 1 vpp_wk_0 (lcore 1)
Time 34.8, 10.000000 sec internal node vector rate 5.61 loops/sec 522628.90
             Name                 State         Calls          Vectors        Suspends      Packet-Clocks   Vectors/Call
dpdk-input                       polling           1000             500               0          4.00e2            0.50
ip4-input                        active             100             500               0          2.00e2            5.00
ip4-drop                         active             100             500               0          1.00e2            5.00
dhcp6-pd-reply-publisher-proce"event wait"               0               0               1          7.44e3            0.00
---------------
Thread 2 vpp_wk_1 (lcore 2)
             Name                 State         Calls          Vectors        Suspends      Packet-Clocks   Vectors/Call
dpdk-input                       polling           2000               0               0          1.00e2            0.00
"""
C0 = A.runtime_clocks(RT)
check(C0 == {0: {"active": 150000.0, "all": 350000.0}, 1: {"active": 0.0, "all": 200000.0}},
      f"the workers' clocks: per vector with vectors, per call without; a "
      f"process's row and the main thread left out of the graph: {C0}")
C1 = {0: {"active": 450000.0, "all": 950000.0}, 1: {"active": 0.0, "all": 600000.0}}
check(A.busy_between(C0, C1) == {0: 0.5, 1: 0.0}
      and A.busy_between(C1, C1) == {0: 0.0, 1: 0.0}
      and A.busy_between({}, {2: {"active": 1.0, "all": 4.0}}) == {2: 0.25},
      "the graph's share of the node time between two readings")
_vc = os.environ.get("PRISMA_VPPCTL")
os.environ["PRISMA_VPPCTL"] = "echo"
check(A.vpp_runtime().strip() == "show runtime", "vpp_runtime asks vppctl for show runtime")
os.environ["PRISMA_VPPCTL"] = "/nonexistent/vppctl"
check(A.vpp_runtime() == "", "a dataplane that does not answer is no reading")
os.environ["PRISMA_VPPCTL"] = "echo"
check(A.vpp_logging().strip() == "show bpa logging",
      "#555: vpp_logging asks vppctl for the screen show cgnat logging prints")
os.environ["PRISMA_VPPCTL"] = "/nonexistent/vppctl"
check(A.vpp_logging() == "", "  and a dataplane that does not answer is no screen")
if _vc is None:
    del os.environ["PRISMA_VPPCTL"]
else:
    os.environ["PRISMA_VPPCTL"] = _vc
_t = [100.0]
_texts = iter([RT, RT.replace("2.00e2", "8.00e2"), RT.replace("2.00e2", "8.00e2")])
_slept = []
BM = A.BusyMeter(read=lambda: next(_texts), clock=lambda: _t[0],
                 sleep=lambda d: (_slept.append(d), _t.__setitem__(0, _t[0] + d)))
B1 = BM.busy()
# ip4-input's 500 vectors at 800 clocks rather than 200: 300000 more in the
# graph, out of 300000 more node time - all of it.
check(_slept == [1.0] and B1 == {0: (1.0, 1000), 1: (0.0, 1000)},
      f"a first reading waits a window for the second: {B1} slept {_slept}")
_t[0] += 30
B2 = BM.busy()
check(_slept == [1.0] and B2[0] == (0.0, 30000),
      f"a later one measures since the reading before, nothing slept: {B2}")
L = A.worker_load(WS, "3-4", {0: (0.5, 1000)})
check(L[0] == {"worker": 0, "cpu": 3, "vectors_per_call": 12.0, "busy_ratio": 0.5,
               "loops_per_second": 3000000.0, "busy_window_ms": 1000}
      and L[1]["busy_ratio"] == 0.0 and L[1]["cpu"] == 4 and L[1]["busy_window_ms"] == 0,
      f"the dataplane's rates and the meter's busy ratio, per worker: {L}")
check(A.worker_load({}, "", {}) == [], "no statistics, no workers")
_sl = A.worker_load({"/sys/num_worker_threads": 1, "/sys/vector_rate_per_worker": [[0, 5]],
                     "/sys/loops_per_worker": [[0, 98]]}, "", {0: (0.7, 1000)})
check(_sl[0]["busy_ratio"] == 0.0 and _sl[0]["vectors_per_call"] == 0.0,
      f"a worker waiting for interrupts (98 loops/s) is not busy, and its stale "
      f"vector rate is not reported: {_sl}")
check(A.worker_load({"/sys/num_worker_threads": 1, "/sys/loops_per_worker": [[0, 5000]]},
                    "", {})[0]["cpu"] == 0, "no core list: the worker's number")

print("=== #533: the counter history ===")
H = A.History(keep=3, max_interfaces=1)
for t in (1.0, 2.0, 3.0, 4.0):
    H.add(t, [{"name": "pg0", "counters": {"rx_bytes": int(t * 10), "tx_bytes": 1}},
              {"name": "pg1", "counters": {"rx_bytes": 5, "tx_bytes": 5}}],
          {"sessions": int(t)}, L, memory_used=100)
H.add(5.0)
smp = list(H.samples())
check([x["at"] for x in smp] == [3.0, 4.0, 5.0], f"the last `keep` samples: {[x['at'] for x in smp]}")
check(smp[0]["interfaces"] == [{"name": "pg0", "counters": {"rx_bytes": 30, "tx_bytes": 1}}],
      f"an interface's counters; past the interface cap, none: {smp[0]['interfaces']}")
check(smp[2]["interfaces"] == [] and smp[2]["cgnat"] is None and smp[2]["workers"] == []
      and smp[2]["memory_used_bytes"] is None, "a sample that read nothing has nothing")
check(smp[1]["cgnat"] == {"sessions": 4} and smp[1]["memory_used_bytes"] == 100
      and smp[1]["workers"][0]["busy_ratio"] == 0.5 and smp[1]["workers"][0]["cpu"] == 3,
      "the CGNAT, the workers and the memory")
check([x["at"] for x in H.samples(since=3.5)] == [4.0, 5.0], "since a time")
only = list(H.samples(interfaces=["nope"], cgnat=False, workers=False, memory=False))
check(only[0]["interfaces"] == [] and only[0]["cgnat"] is None and only[0]["workers"] == [],
      "only what is asked")
H._put(H.counters, "Q", ("if", "pg9", "rx_bytes"), 1)
check(len(H.counters[("if", "pg9", "rx_bytes")]) == len(H.times),
      "_put pads a new series to the samples before it")
del H.counters[("if", "pg9", "rx_bytes")]
D = H.dump()
H2 = A.History(keep=3)
check(H2.load(D, now=6.0, interval=60) and list(H2.times) == [3.0, 4.0, 5.0]
      and [x["interfaces"] for x in H2.samples()] == [x["interfaces"] for x in smp],
      "dump() read back by load()")
check(not A.History(keep=3).load(D, now=10000.0, interval=1),
      "samples older than the span kept are not read back")
check(not A.History().load({"version": 9}) and not A.History().load([]),
      "a file this does not understand is not read")
check(A.history_key("wk\t0\tbusy_ratio") == ("wk", 0, "busy_ratio")
      and A.history_key("if\t10\trx_bytes") == ("if", "10", "rx_bytes"),
      "a series key read back: a worker's number is a number, an interface named 10 is not")
with tempfile.TemporaryDirectory() as _d:
    _hp = os.path.join(_d, "api", "history.json")
    check(H.save(_hp) and os.path.exists(_hp), "save() writes the file, its directory too")
    H3 = A.History(keep=3)
    _now_was = A.time.time
    A.time.time = lambda: 6.0
    check(H3.restore(_hp) and len(H3.times) == 3, "restore() reads it back")
    A.time.time = _now_was
    check(not A.History().restore(os.path.join(_d, "none")), "no file, nothing restored")
H4 = A.History()
with tempfile.NamedTemporaryFile("w", delete=False) as f:
    f.write("MemTotal: 1000 kB\nMemAvailable: 400 kB\n")
A.take_sample(H4, dict(STATS, **WS), "3-4", meminfo=f.name, now=9.0,
              meter=type("M", (), {"busy": lambda self: {0: (0.25, 60000)}})())
os.unlink(f.name)
s9 = next(H4.samples())
check(s9["at"] == 9.0 and s9["interfaces"][0]["name"] == "pg0"
      and s9["cgnat"]["sessions"] == 40 and len(s9["workers"]) == 2
      and s9["workers"][0]["busy_ratio"] == 0.25 and s9["workers"][0]["busy_window_ms"] == 60000
      and s9["memory_used_bytes"] == 600 * 1024,
      f"take_sample reads every part: {s9}")
A.take_sample(H4, {}, "", meminfo="/none", now=10.0)
s10 = list(H4.samples())[-1]
check(s10["interfaces"] == [] and s10["cgnat"] is None and s10["memory_used_bytes"] is None,
      "a dataplane that answers nothing is a sample of nothing, not of zeros")

print("=== #533: grpc.health.v1 ===")
check(A.health_status("", True) == "SERVING"
      and A.health_status("prisma.v1.ConfigService", False) == "SERVING"
      and A.health_status("prisma.v1.TelemetryService", False) == "NOT_SERVING"
      and A.health_status("prisma.v1.OperationalService", True) == "SERVING"
      and A.health_status("prisma.v1.Nope", True) is None,
      "SERVING; NOT_SERVING for what needs the dataplane when it does not answer; "
      "None for a service not served")
check(A.health_status(A.LOG_HEALTH, True, "NOT_SERVING") == "NOT_SERVING"
      and A.health_status(A.LOG_HEALTH, True, "SERVING") == "SERVING"
      and A.health_status(A.LOG_HEALTH, True) is None
      and A.health_status("", True, "NOT_SERVING") == "SERVING"
      and A.health_status("prisma.v1.OperationalService", True, "NOT_SERVING") == "SERVING",
      "#555: the log export answers under a name of its own, and its stall "
      "reaches neither the server's \"\" nor OperationalService")
check(A.stats_ok(lambda: {"/sys/heartbeat": 1}) and not A.stats_ok(lambda: {})
      and not A.stats_ok(lambda: 1 / 0), "the statistics segment answers, or does not")


class PB:
    class Diagnostic:
        SEVERITY_UNSPECIFIED, SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_NOTE = 0, 1, 2, 3

        def __init__(self, **kw):
            self.__dict__.update(kw)


dg = A._diag_pb(PB, {"severity": "warning", "message": "m", "path": ["a"], "line": ""})
check(dg.severity == 2 and dg.message == "m" and dg.path == ["a"],
      "_diag_pb maps a diagnostic onto the message")


print("=== #555: GetLogging's fields from a reading ===")
import logstate as LS  # noqa: E402
_scr = """IPFIX export of block events (RFC 8158)
  state       enabled
  exporter    198.51.100.3:4739 src 198.51.100.2 (path mtu 512, template every 20s)
  records     0 emitted, 0 dropped
  sender      the log container: 9 records, 3 packets, 2 templates, 0 send failures, 0 dropped
  exhaustion  one record per subscriber per 10s: 5 repeats not reported
  queue       1 event(s) dropped with a worker's queue full (8192 each)
  rings       /dev/shm/prisma-bpa-log, 7604928 bytes, outside the main heap
  export      2 event(s) dropped with the log container's ring full (65536)
  transmit    ens20: 4 packet(s) the interface failed to send, from every sender on it
TCP streams carried by the log container (pid 7)
TCP transport: disabled
syslog transport (RFC 5424 over TCP)
  collector   198.51.100.3:601  connected
  lines       6 written, 600 bytes sent, 0 pending
  spill       0 bytes on disk, 0 replayed, 0 dropped
  file        0 bytes now of 256 MB at most, 0 dropped over it, 0 resynced, 0 stalls
"""
_w = LS.LogWatch(stall=60)
_w.observe(_scr, now=500)
_f = A.logging_fields(*_w.snapshot()[:4])
check([x["kind"] for x in _f["streams"]] == ["syslog", "ipfix-tcp", "ipfix-udp"]
      and set(_f["streams"][0]) == set(A.LOG_STREAM_FIELDS),
      "three streams, each with exactly LogStream's fields")
check((_f["carrier"], _f["logger_pid"], _f["export_ring_dropped"], _f["worker_queue_dropped"],
       _f["exhaustion_repeats_not_reported"], _f["transmit_failed"], _f["transmit_interface"],
       _f["rings_shared"], _f["at"]) == ("log-container", 7, 2, 1, 5, 4, "ens20", True, 500),
      f"the box-wide fields, as the screen says them: {_f['carrier']} {_f['logger_pid']}")
check(_f["streams"][0]["bytes_sent"] == 600 and _f["streams"][0]["delivering"]
      and _f["streams"][2]["packets_sent"] == 3 and _f["text"] == _scr,
      "a stream's counters and verdict, and the screen itself, verbatim")

print("=== #558: why a change was refused, the confirm window, the candidates ===")
R = A.refusal_reason
check(A.refusal_reason("", moved_to="abc")["reason"] == "stale_base"
      and R("", moved_to="abc")["running_revision"] == "abc",
      "a base that moved: stale_base, with the revision it moved to")
check(R("", expired=True)["reason"] == "candidate_expired"
      and R("", expired=False)["reason"] == "candidate_unknown",
      "a candidate the server closed for idleness, and one it never held")
check(R("error: configure: permission denied for a read-only account")["reason"]
      == "permission", "the role's refusal: permission")
_lim = R("error: cgnat identity file: 256 bytes, and the limit is 255\n"
         "    the field it travels in is bpa.api", stage="edit")
check(_lim["reason"] == "limit" and _lim["limit_field"] == "cgnat identity file"
      and _lim["limit"] == 255 and _lim["limit_size"] == 256,
      f"config.text_error's verdict: limit, its field, limit and size: {_lim}")
import config as _C  # noqa: E402
_v, _ = _C.text_error("cgnat identity file", "/" + "a" * 255, True)
check(A.LIMIT_RE.search("error: " + _v) is not None,
      f"LIMIT_RE reads the verdict config.text_error really writes: {_v!r}")
for _phrase, _want in (("    if that is what you mean, type: commit remap", "gate_remap"),
                       ("    if that is what you mean, type: commit diverge", "gate_diverge"),
                       ("    if that is what you mean, type: commit cutoff", "gate_cutoff"),
                       ("error: the box is still on the unconfirmed configuration",
                        "confirm_pending"),
                       ("error: refusing to commit: 2 statement(s) in this batch were refused",
                        "grammar"),
                       ("error: candidate did not pass validation; nothing applied",
                        "validation"),
                       ("error: commit failed; rolling back to the previous state",
                        "apply_failed"),
                       ("error: usage: commit confirmed <minutes> [comment <text>]", "usage")):
    check(R("error: x\n" + _phrase)["reason"] == _want, f"{_want}: {_phrase.strip()}")
check(R("error: a static route needs 'next-hop <next-hop>'", stage="edit")["reason"]
      == "grammar" and R("error: pool 'P': inside and outside required")["reason"]
      == "validation",
      "a refusal no sentence names: grammar at entry, validation at a commit")
check(R("commit complete\n")["reason"] == "other", "no error at all is not a known refusal")
# THE TABLE IS HELD TO THE VTY'S OWN SOURCE: a sentence reworded there would
# leave the reason quietly "other" here.
_src = open(os.path.join(os.path.dirname(A.__file__), "prismash.py")).read() + \
    open(os.path.join(os.path.dirname(A.__file__), "config.py")).read()
_missing = [ph for _, ph in A.REFUSAL_TEXTS
            if ph.strip() not in _src and ph != "% Unknown command"]
check(not _missing, f"every sentence REFUSAL_TEXTS reads is one the VTY prints: {_missing}")
check(set(r for r, _ in A.REFUSAL_TEXTS) <= set(A.REASONS),
      "every reason the table gives is one prisma.v1 names")
check(A.check_reason({"failed": False, "output": ""}, {"remap": ["s1"], "diverge": [],
                                                       "cutoff": []}) == "gate_remap"
      and A.check_reason({"failed": False, "output": ""}, {"remap": [], "diverge": [],
                                                           "cutoff": []}) == ""
      and A.check_reason({"failed": True, "output": "error: candidate did not pass "
                          "validation; nothing applied"}, {}) == "validation",
      "a check: the gate a Commit would meet, its refusal, or nothing")

_st = {"confirm_pending": "yes", "confirm_deadline": "1700000000", "commit_by": "web",
       "commit_at": "1699999700", "comment": "via api"}
_p = A.pending_confirm({"deadline": 1700000000.0, "minutes": 5, "attempts": 0}, _st,
                       "hostname x\n", now=1699999800)
check(_p["pending"] and _p["account"] == "web" and _p["comment"] == "via api"
      and _p["deadline"] == 1700000000.0 and _p["minutes"] == 5 and not _p["overdue"]
      and _p["revision"] == A.revision_of("hostname x\n") and _p["committed_at"] == 1699999700,
      f"the window from the box: who, why, when, and the revision it reverts from: {_p}")
_p = A.pending_confirm(None, _st, "", now=1700000100)
check(_p["pending"] and _p["deadline"] == 1700000000 and _p["minutes"] == 0 and _p["overdue"],
      "an account that cannot read the window file still reads it from the state "
      "file, deadline and all; past it, overdue")
check(A.pending_confirm(None, {"confirm_pending": "no"}, "") == {"pending": False}
      and A.pending_confirm(None, {}, "") == {"pending": False}, "no window, nothing else")
check(A.staged_lines("a\nb\n", "a\nc\nd\n") == 3 and A.staged_lines("a\n", "a\n") == 0,
      "staged lines: what the candidate adds and removes against its base")
check(abs(A.wall_of(100.0, now_mono=110.0, now_wall=1000.0) - 990.0) < 1e-9,
      "a monotonic reading as wall-clock time")

S2 = A.Sessions(worker=W, idle=60, ops_idle=60)
_c, *_ = S2.open(me)
S2.get(me, _c)["base"] = "hostname old\n"
_before = S2.get(me, _c)["worker"].used
_rows = S2.listing(me)
check(len(_rows) == 1 and _rows[0]["id"] == _c and _rows[0]["staged"] == 2
      and abs(_rows[0]["expires_at"] - _rows[0]["last_activity"] - 60) < 1e-6,
      f"ListCandidates: this account's candidate, its staged lines, its expiry: {_rows}")
check(S2.get(me, _c)["worker"].used == _before,
      "listing does not count as using the candidate: it would never expire")
check(S2.listing("somebody-else") == [], "nobody else's candidates")
_t = S2.timing(S2.get(me, _c))
check(_t["expires_at"] - _t["last_activity"] == 60 and _t["created"] <= _t["last_activity"] + 1,
      f"Sessions.timing: created, last activity, and expiry the idle rule after it: {_t}")


class _PB:
    class Diagnostics:
        REASON_OTHER, REASON_LIMIT, REASON_STALE_BASE = 14, 5, 1


check(A._reason_pb(_PB, "limit") == 5 and A._reason_pb(_PB, "stale_base") == 1
      and A._reason_pb(_PB, "no-such") == 14,
      "_reason_pb: a reason as its enum value, an unknown one as OTHER")
S2.idle = 0
time.sleep(0.05)
S2.reap()
check(S2.was_expired(me, _c) and not S2.was_expired("somebody-else", _c)
      and not S2.was_expired(me, "never-opened"),
      "a candidate reap() closed is remembered as expired, for its own account")
_w = S2.ops_worker(me)
_pc = _w.ask("pending_confirm")
check(_pc["pending"] and _pc["account"] == "root", f"the worker reads the window: {_pc}")
for _w in list(S2.ops.values()):
    _w.close()

check(A.status_message("warning: pool 'lg': nothing here announces X\n"
                       "error: hostname: not allowed\n") == "hostname: not allowed"
      and A.status_message("warning: w\n") == "w" and A.status_message("", "said") == "said"
      and A.status_message("plain\n") == "plain",
      "a refusal's status message is its first error, not a warning before it")

# ----------------------------------------------- #558 phase 2: typed config

from config import Config as _Cfg  # noqa: E402

_TXT = """nat
 cgnat
  pool A
   prefix-inside 100.64.0.0/10
   prefix-outside 200.0.0.0/24
   block-size 256
   policy interleaved
   mode dynamic
   exit
  logging syslog 10.0.0.2 port 601 enterprise 67033 facility 16 severity 6
  exit
 exit
interface ens18
 description up link
 ipv4 address 10.0.0.1/24
 mtu 1500
 exit
"""


class _TSh:
    """A Shell as the typed operations read it: a candidate and the running."""

    def __init__(self, cand=True):
        self.candidate = _Cfg.parse(_TXT) if cand else None
        self._run = _Cfg.parse(_TXT)
        self.dp = None

    def running_config(self):
        return self._run


_tsh = _TSh()
_tsh.candidate.mtus["ens18"] = 9000
_tr = A.typed_config(_tsh, {"source": "running", "areas": ["interfaces"]})
_tc = A.typed_config(_tsh, {"source": "candidate"})
check(_tr["interfaces"][0]["mtu"] == 1500 and _tc["interfaces"][0]["mtu"] == 9000
      and sorted(_tc) == sorted(list(A.typedcfg.AREAS) + ["box_owned", "revision"])
      and sorted(_tr) == ["box_owned", "interfaces", "revision"]
      and _tr["revision"] == A.revision_of(_tsh.running_config().text())
      and _tc["revision"] == "",
      "typed_config: the running or the candidate Config itself, the areas asked for")
try:
    A.typed_config(_TSh(cand=False), {"source": "candidate"})
    check(False, "typed_config with no candidate refuses")
except ValueError:
    check(True, "typed_config with no candidate in the session refuses")
_tl = A.typed_lines(_tsh, {"area": "pools", "object": {"name": "A", "max_blocks": 4},
                           "fields": ["max_blocks"]})
check(_tl == {"path": ["nat", "cgnat", "pool A"], "lines": ["max-blocks 4"]},
      f"typed_lines: the lines and where they go, against the candidate: {_tl}")
_ts = A.typed_schema(_tsh, {"modes": ["interface (port)", "pool"]})
check([m["mode"] for m in _ts["modes"]] == ["pool", "interface (port)"]
      and len(_ts["digest"]) == 64 and _ts["release"] and _ts["values"] == {}
      and len(_ts["box_owned"]) == 4,
      "typed_schema: the modes asked for, versioned by release and digest, no values unasked")


# typed_delete (#558 phase 3): read off the candidate - a BGP neighbour by
# the statements it has there, and refused with no candidate open.
_dsh = _TSh()
_dsh.candidate.routing += ["router bgp 65000", " neighbor 10.0.0.2 remote-as 65001",
                           " neighbor 10.0.0.2 description up", "exit"]
_td = A.typed_delete(_dsh, {"area": "bgp", "name": "10.0.0.2", "asn": "65000"})
check(_td == {"path": ["router bgp 65000"],
              "lines": ["no neighbor 10.0.0.2 description up",
                        "no neighbor 10.0.0.2 remote-as 65001"]},
      f"typed_delete: a neighbour removed by its own statements, remote-as last: {_td}")
try:
    A.typed_delete(_TSh(cand=False), {"area": "static", "prefix": "1.0.0.0/8",
                                      "next_hop": "10.0.0.1"})
    check(False, "typed_delete with no candidate refuses")
except ValueError:
    check(True, "typed_delete with no candidate refuses")


class _DSh(_TSh):
    def dynamic_options(self, hint, prefix):
        return {"ens19": "", "ens18": ""} if hint == "<interface>" else {}


_tv = A.typed_schema(_DSh(), {"modes": ["pool"], "with_values": True})
check(_tv["values"].get("interfaces") == ["ens18", "ens19"] and "pools" in _tv["values"],
      f"typed_schema with_values: each dynamic source's values now: {_tv['values']}")
check(A.handle(_tsh, "typed", {"source": "running", "areas": ["pools"]})["pools"][0]["name"] == "A"
      and A.handle(_tsh, "typed_lines", {"area": "logging", "object": {
          "enabled": True}, "fields": ["enabled"]})["lines"] == ["logging enable"],
      "handle answers the typed operations")


class _FPB:
    """Message stand-ins: a constructor that keeps its fields."""

    class _M:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    InterfaceConfig = PortForwardConfig = PoolConfig = LogCollectorConfig = \
        LoggingConfig = _M


_ip = A.interface_pb(_FPB, _tc["interfaces"][0])
check(_ip.name == "ens18" and _ip.mtu == 9000 and _ip.ipv4 == ["10.0.0.1/24"],
      "interface_pb: every field of the dict")
_pp = A.pool_pb(_FPB, _tc["pools"][0])
check(_pp.name == "A" and not hasattr(_pp, "vrf") and _pp.forwards == [],
      "pool_pb: an unset VRF stays unset - every VRF, not VRF 0")
_lp = A.logging_pb(_FPB, _tc["logging"])
_sy = [c for c in _lp.collectors if c.transport == "syslog"][0]
_ud = [c for c in _lp.collectors if c.transport == "udp"][0]
check(_sy.facility == 16 and not hasattr(_ud, "facility"),
      "logging_pb: facility/severity only where stated")


class _Msg:
    def __init__(self, has=(), **kw):
        self.__dict__.update(kw)
        self._has = set(has)

    def HasField(self, f):
        return f in self._has


_pm = _Msg(has={"vrf"}, timeouts={"udp": 5}, algs={}, forwards=[], vrf=3, outside_vrf=0,
           **{k: 0 for k in A.POOL_SCALARS}, **{k: [] for k in A.POOL_LISTS})
_pd = A.pool_dict(_pm)
check(_pd["vrf"] == 3 and _pd["outside_vrf"] is None and _pd["timeouts"] == {"udp": 5},
      "pool_dict: a set VRF, an unset one None")
check(A._has(_pm, "vrf") and not A._has(_pm, "outside_vrf"), "_has")
_im = _Msg(**{k: "" for k in A.INTERFACE_KEYS})
_im.ipv4, _im.ipv6, _im.name = ["10.0.0.9/24"], [], "x"
check(A.interface_dict(_im)["ipv4"] == ["10.0.0.9/24"] and A.interface_dict(_im)["name"] == "x",
      "interface_dict")
_cm = _Msg(has={"severity"}, transport="syslog", configured=True, collector="c", port=0,
           source="", enterprise=67033, facility=0, severity=6)
_ld = A.logging_dict(_Msg(enabled=True, collectors=[_cm]))
check(_ld["collectors"][0]["facility"] is None and _ld["collectors"][0]["severity"] == 6,
      "logging_dict: an unset facility is None, a set severity its value")


class _Rep(list):
    def __init__(self, make):
        super().__init__()
        self.make = make

    def add(self, **kw):
        x = self.make(**kw)
        self.append(x)
        return x


class _SPB:
    class ConfigSchema:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            self.modes = _Rep(_SPB._Mode)
            self.values = {}
            self.box_owned = _Rep(dict)

    class _Mode:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            self.statements = _Rep(_SPB._Stmt)

    class _Stmt:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            self.forms = _Rep(_SPB._Form)

    class _Form:
        def __init__(self, **kw):
            self.arguments = _Rep(dict)


class _VL:
    def __init__(self):
        self.values = []


_sp_in = dict(_ts, values={"pools": ["A"]})
_spo = _SPB.ConfigSchema
_SPB.ConfigSchema = type("CS", (_spo,), {"__init__": lambda self, **kw: (
    _spo.__init__(self, **kw), setattr(self, "values", __import__("collections").defaultdict(_VL)))[0]})
_sp = A.schema_pb(_SPB, _sp_in)
check(len(_sp.modes) == 2 and _sp.release == _ts["release"] and _sp.digest == _ts["digest"]
      and any(st.words == ["block-size"] for st in _sp.modes[0].statements)
      and _sp.values["pools"].values == ["A"] and len(_sp.box_owned) == 4,
      "schema_pb: each mode, statement, form and argument, the release, digest, values, "
      "and the box-owned statements")
_vv = A.validate_verdicts(["mtu 9000", "mtu 1"], [
    {"output": "", "failed": False, "at": -1},
    {"output": "error: mtu: 1 is outside 576-9216\n", "failed": True, "at": 0}])
check(_vv[0]["ok"] and not _vv[1]["ok"] and _vv[1]["reason"] == "grammar"
      and "576-9216" in _vv[1]["message"],
      f"validate_verdicts: a line accepted, a line refused with its reason: {_vv}")
check(A.refusal_reason("error: x\n", stage="usage")["reason"] == "usage",
      "refusal_reason: a typed request's own words are a usage refusal")

print("\n=== #558 phase 5: the clears, the box, the upgrade, the log stream ===")
check(A.health_status("prisma.v1.SystemService", True) == "SERVING",
      "health: SystemService is served (#558 phase 5)")

check(A.clear_line("interface_counters") == "clear interface counters"
      and A.clear_line("interface_counters", "ens19") == "clear interface counters ens19"
      and A.clear_line("cgnat_subscriber", "100.64.0.9") == "clear cgnat subscriber 100.64.0.9"
      and A.clear_line("bgp", "10.0.0.2", "soft_in") == "clear bgp 10.0.0.2 soft in"
      and A.clear_line("bgp", "10.0.0.2") == "clear bgp 10.0.0.2"
      and A.clear_line("ospf_process") == "clear ospf process"
      and A.clear_line("isis_adjacency") == "clear isis adjacency",
      "clear_line: each target as the VTY line it is")
check(A.clear_line("bgp") is None and A.clear_line("cgnat_subscriber") is None
      and A.clear_line("bgp", "10.0.0.2; system reboot") is None
      and A.clear_line("bgp", "10.0.0.2 soft") is None
      and A.clear_line("nothing", "x") is None and A.clear_line("bgp", "x", "fast") is None,
      "clear_line: no line for a missing name, a second word, or a target it has not got")


class _CostShell:
    def __init__(self, unsaved, pending):
        self.u, self.p = unsaved, pending

    def say_unsaved(self):
        if self.u:
            print("warning: the running configuration has NOT been saved")

    def say_unconfirmed(self):
        if self.p:
            print("warning: the last commit has NOT been confirmed")


_c = A.system_cost(_CostShell(True, False))
check(_c["unsaved"] and not _c["confirm_pending"] and "NOT been saved" in _c["said"],
      f"system_cost: what the VTY warns of, as flags: {_c}")
_c = A.system_cost(_CostShell(False, True))
check(not _c["unsaved"] and _c["confirm_pending"], "system_cost: a confirm window")
check(not any(A.system_cost(_CostShell(False, False))[k]
              for k in ("unsaved", "confirm_pending")), "system_cost: nothing to lose")
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src",
                         "prismash", "prismash.py")).read()
check("has NOT been saved" in _src and "has NOT been confirmed" in _src,
      "system_cost reads sentences the VTY prints")

_UP = """downloading https://prismarouting.com.br/releases/0.149.25/x
the package matches its published sha256:
    abcd
installing the images
  loaded prisma-vpp
upgraded to 0.149.25
upgrade: refresh-the-vty is scheduled: the VTY restarts on the new release in a few seconds
    the VTY restarts on 0.149.25 in a few seconds: this session ends then, and
prisma-api-exit 0
"""
_ph = A.upgrade_phases(_UP)
check([x["phase"] for x in _ph] == ["download", "verify", "install", "install",
                                     "installed", "api_restart"]
      and _ph[4]["version"] == "0.149.25",
      f"upgrade_phases: download, verify, each install row, installed, the API's restart: "
      f"{[x['phase'] for x in _ph]}")
_ph = A.upgrade_phases("downloading x\nerror: system upgrade: it does not match its sha256\n"
                       "prisma-api-exit 1\n")
check([x["phase"] for x in _ph] == ["download", "failed"],
      f"upgrade_phases: a refusal is one FAILED, the exit adds none: {_ph}")
check([x["phase"] for x in A.upgrade_phases("prisma-api-exit 2\n")] == ["failed"],
      "upgrade_phases: an exit that is not 0 with nothing said is a FAILED")
_ph = A.upgrade_phases("this box runs: 0.149.24\nnewest published: 0.149.25\n")
check(_ph == [{"phase": "checked", "message": "newest published: 0.149.25",
               "newest": "0.149.25"}], f"upgrade_phases: no version, the newest named: {_ph}")
for _phrase in ("downloading ", "the package matches its published sha256",
                "upgraded to ", "the VTY restarts on", "newest published: "):
    check(_phrase in _src, f"upgrade_phases reads a line the VTY prints: {_phrase!r}")

with tempfile.TemporaryDirectory() as _d:
    _p = os.path.join(_d, "upgrade.log")
    check(A.run_log(_p) is None and not A.run_busy(_p), "run_log: no log, nothing running")
    with open(_p, "w") as _f:
        _f.write("downloading x\n")
    _r = A.run_log(_p)
    check(not _r["done"] and _r["status"] is None and A.run_busy(_p),
          "run_log: a log with no exit line is a run still going")
    check(not A.run_busy(_p, now=time.time() + 3600),
          "run_busy: a run silent for half an hour is a run that died")
    with open(_p, "a") as _f:
        _f.write("prisma-api-exit 3\n")
    _r = A.run_log(_p)
    check(_r["done"] and _r["status"] == 3 and not A.run_busy(_p),
          f"run_log: the exit line ends it, with its status: {_r['status']}")
    check(A.segment_readable(_p) == (True, ""), "segment_readable: a file this account reads")
    _ok, _why = A.segment_readable(os.path.join(_d, "none"))
    check(not _ok and "no event segment" in _why, f"segment_readable: none there: {_why}")


class _HostSys:
    def __init__(self, rc=0, out=""):
        self.rc, self.out, self.asked = rc, out, []

    def _run(self, argv):
        self.asked.append(argv)
        return self.rc, self.out


class _HostShell:
    def __init__(self, allowed=True, rc=0, out=""):
        self.allowed, self.sys = allowed, _HostSys(rc, out)

    def require(self, cap, what):
        if not self.allowed:
            print(f"error: {what}: permission denied for a read-only account")
        return self.allowed


_h = _HostShell()
_r = A.hostrun(_h, "upgrade", "0.149.25")
check(_r["ok"] and _h.sys.asked and _h.sys.asked[0][0] == "systemd-run"
      and "system upgrade 0.149.25" in _h.sys.asked[0][-1],
      f"hostrun: the door that starts the upgrade, detached: {_h.sys.asked}")
_h = _HostShell(allowed=False)
_r = A.hostrun(_h, "dataplane")
check(not _r["ok"] and not _h.sys.asked and "permission denied" in _r["output"],
      "hostrun: the VTY's role first, nothing asked of the host")
_h = _HostShell(rc=126, out="the host's door: start 'system upgrade' for the API: only root may")
_r = A.hostrun(_h, "upgrade", "0.149.25")
check(not _r["ok"] and A.action_reason(_r["output"]) == "permission",
      f"hostrun: the host's root-only door, as a permission: {_r}")
check(not A.hostrun(_HostShell(), "upgrade", "1.2")["ok"],
      "hostrun: a version that is not a release starts nothing")
check(A.action_reason("error: usage: clear interface counters [<interface>]\n") == "usage"
      and A.action_reason("error: x\n") == "other",
      "action_reason: a usage line, and an action's other refusals")


class _SC:
    PERMISSION_DENIED, INVALID_ARGUMENT, FAILED_PRECONDITION = "pd", "ia", "fp"


class _Grpc:
    StatusCode = _SC


check(A.action_code(_Grpc, "permission") == "pd" and A.action_code(_Grpc, "usage") == "ia"
      and A.action_code(_Grpc, "busy") == "fp", "action_code: the status for a reason")


class _WReq:
    def __init__(self, **kw):
        self.__dict__.update(dict(raw=False, subscriber="", public_address="", port=0,
                                  event="", pool="", seconds=0), **kw)


check(A.watch_words(_WReq()) == [] and A.watch_words(_WReq(
    raw=True, subscriber="100.64.0.9", public_address="200.0.0.5", port=1500,
    event="alloc", pool="p", seconds=30)) == [
    "raw", "subscriber", "100.64.0.9", "public", "200.0.0.5:1500", "event", "alloc",
    "pool", "p", "seconds", "30"],
    "watch_words: the request's filters as the monitor's own words")
_old = os.environ.get("PRISMA_VPPCTL")
with tempfile.TemporaryDirectory() as _d:
    _v = os.path.join(_d, "vppctl")
    with open(_v, "w") as _f:
        _f.write("#!/bin/sh\necho \"asked: $*\"\n")
    os.chmod(_v, 0o755)
    os.environ["PRISMA_VPPCTL"] = _v
    check(A.vpp_show("bpa", "pool", "index").strip() == "asked: show bpa pool index",
          "vpp_show: `show <words>` through vppctl")
    os.environ["PRISMA_VPPCTL"] = os.path.join(_d, "absent")
    check(A.vpp_show("x") == "", "vpp_show: no dataplane is nothing")
if _old is None:
    os.environ.pop("PRISMA_VPPCTL", None)
else:
    os.environ["PRISMA_VPPCTL"] = _old


class _TS:
    def FromNanoseconds(self, n):
        self.n = n


class _Kind:
    @staticmethod
    def Value(name):
        return name


class _LE:
    Kind = _Kind

    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.at, self.boot_time = _TS(), _TS()


class _LPB:
    LogEvent = _LE


_e = A.log_event_pb(_LPB, {"kind": "alloc", "at": 10.5, "subscriber": "100.64.0.9",
                           "public": "200.0.0.5", "port_first": 1024, "port_last": 1279,
                           "pool": "p", "pool_id": 1, "identity": "", "text": "line"})
check(_e.kind == "ALLOC" and _e.public_address == "200.0.0.5" and _e.at.n == 10500000000
      and _e.pool == "p" and not hasattr(_e, "identity"),
      "log_event_pb: a watch dict as LogEvent, empty fields left unset")
_e = A.log_event_pb(_LPB, {"kind": "boot", "at": 1, "boot_wall": 2.0, "text": "b"})
check(_e.kind == "BOOT" and _e.boot_time.n == 2000000000, "log_event_pb: a boot, its time")
_t = _TS()
A._stamp(_t, 1.25)
check(_t.n == 1250000000, "_stamp: seconds to the nanosecond")


class _Phase:
    @staticmethod
    def Value(name):
        return name


class _UPm:
    Phase = _Phase

    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.started_at, self.diag = _TS(), None

    @property
    def diagnostics(self):
        me = self

        class _D:
            def CopyFrom(self, d):
                me.diag = d
        return _D()


class _UPB:
    UpgradeProgress = _UPm

    class Diagnostics:
        REASON_OTHER = 14

        def __init__(self, **kw):
            self.__dict__.update(kw)


_u = A.progress_pb(_UPB, {"phase": "api_restart", "message": "m", "api_away_seconds": 60},
                   started=5)
check(_u.phase == "API_RESTART" and _u.api_away_seconds == 60 and _u.started_at.n == 5e9,
      "progress_pb: a step as UpgradeProgress, with when the run started")
_u = A.progress_pb(_UPB, {"phase": "failed", "message": "error: x"}, output="error: x\n")
check(_u.phase == "FAILED" and _u.diag is not None and _u.diag.reason == 14,
      "progress_pb: a FAILED carries its diagnostics")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
