#!/usr/bin/env python3
"""
PPPoE subscriber termination (#597): src/prismash/pppoe.py, the `pppoe`
block in the configuration model, the VTY's level, apply, show and clear, and
the doors the commit and the screens go through.

Each behaviour, shown red by a mutation (named beside the box):

  * one rule for the prompt and the saved file: stage() takes and refuses
    the same statements both read (mutation: the secret not checked for a
    comma, which would split accel-ppp's `server=` line);
  * the block written, read back and nested exactly as typed (mutation:
    body() dropping a non-default RADIUS port);
  * a secret and a password never on a screen (mutation: redact() matching
    only the nested line, so `show configuration compare`'s flat diff leaks);
  * what makes a configuration one the container cannot run, refused at
    commit (mutation: check() accepting a block with no gateway);
  * one card, one VLAN tag (mutation: access() not refusing QinQ);
  * the files the container runs, generated (mutation: accel_conf() leaving
    out the VLAN interface accel-ppp must bind);
  * A SESSION IS NOT CONFIGURATION: the session interfaces, their CGNAT and
    the control-plane TAP out of the running configuration (mutation:
    drop_pppoe_sessions() leaving the attachments);
  * the doors take exactly the terminate words clear_words() builds
    (mutation: a user name with a shell character accepted).
"""
import contextlib
import io
import os
import shutil
import stat
import sys
import tempfile

_root = tempfile.mkdtemp(prefix="pppoe-")
import atexit  # noqa: E402
atexit.register(shutil.rmtree, _root, True)
for k, v in (("PRISMA_PPPOE_CONF", "pppoe.conf"), ("PRISMA_BNG_DIR", "bng"),
             ("PRISMA_CONFIG", "prisma-routing.conf"),
             ("PRISMA_CONFIRM", "confirm-pending"),
             ("PRISMA_ROLLBACK", "rollback"),
             ("PRISMA_EVENTS", "events.log"),
             ("PRISMA_EVENTS_STATE", "events.state"),
             ("PRISMA_DOORS", "no-doors.sock"),
             ("PRISMA_LOGGING_CONF", "logging.conf"),
             ("PRISMA_LOGGING_STATUS", "logging.status")):
    os.environ[k] = os.path.join(_root, v)
os.environ["PRISMA_BNG_CTL"] = os.path.join(_root, "prisma-bng")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src", "prismash"))
import pppoe as P  # noqa: E402
import config  # noqa: E402
import doors  # noqa: E402
import prismash  # noqa: E402
import system  # noqa: E402
from config import Config  # noqa: E402

fail = 0


def check(c, msg):
    global fail
    print(("  [ ok ] " if c else "  FAIL: ") + msg)
    fail += 0 if c else 1


def staged(*lines):
    held = P.empty()
    whys = [P.stage(held, ["pppoe"] + ln.split()) for ln in lines]
    return held, whys


print("=== the statements: one rule, the prompt's and the file's ===")
held, whys = staged("interface host-acc0", "interface host-acc0.100",
                    "gateway 100.64.0.1", "address-pool 100.64.0.0/24",
                    "address-pool 100.64.1.10-100.64.1.200", "dns 8.8.8.8",
                    "radius server 192.0.2.1 secret s3cr3t! auth-port 11812",
                    "radius nas-identifier bng-1",
                    "radius interim-interval 600",
                    "authentication pap chap", "session-limit 4000",
                    "mtu 1480", "tcp-mss 1440", "ac-name edge",
                    "local-user lab1 password pw1")
check(whys == [None] * len(whys), f"stage: every statement taken: {whys}")
check(held["interfaces"] == ["host-acc0", "host-acc0.100"]
      and held["gateway"] == "100.64.0.1"
      and held["radius"] == [{"address": "192.0.2.1", "secret": "s3cr3t!",
                              "auth-port": "11812", "acct-port": "1813"}]
      and held["settings"]["authentication"] == "pap chap"
      and held["users"] == {"lab1": "pw1"},
      "stage: held as typed, the RADIUS ports defaulted")
for line, want in (
        ("radius server 192.0.2.1 secret a,b", "no space, comma"),
        ("radius server 192.0.2.1 secret \"q\"", "no space, comma"),
        ("radius server bng.example secret x", "not an IPv4 address"),
        ("radius server 192.0.2.1 secret x auth-port 0", "1-65535"),
        ("radius server 192.0.2.1", "secret <secret>"),
        ("address-pool 100.64.0.0/31", "/30 or shorter"),
        ("address-pool 100.64.0.9-100.64.0.1", "ends before it starts"),
        ("address-pool nonsense", "neither a prefix nor a range"),
        ("gateway 2001:db8::1", "gateway <address>"),
        ("mtu 1501", "576-1500"), ("tcp-mss 100", "536-1460"),
        ("session-limit 0", "1-1000000"),
        ("radius interim-interval 30", "0 (none) or 60-86400"),
        ("authentication pap pap", "one or more of"),
        ("authentication eap", "one or more of"),
        ("ac-name a/b", "letters, digits"),
        ("local-user a;b password x", "a name of 1-64"),
        ("local-user lab password", "<name> password <password>"),
        ("interface bad name", "interface <name>"),
        ("shaping 10m", "under 'pppoe'")):
    h = P.empty()
    why = P.stage(h, ["pppoe"] + line.split())
    check(why and want in why and h == P.empty(),
          f"stage refuses '{line}': {why}")
h, _ = staged("dns 1.1.1.1", "dns 8.8.8.8")
check(P.stage(h, "pppoe dns 9.9.9.9".split()).startswith("at most 2"),
      "stage: IPCP carries two resolvers, a third is refused")
h, _ = staged("radius server 192.0.2.1 secret a")
P.stage(h, "pppoe radius server 192.0.2.1 secret b".split())
check([r["secret"] for r in h["radius"]] == ["b"],
      "stage: a server typed again replaces itself")
check(P.stage(h, "no pppoe radius server 192.0.2.9".split())
      .endswith("is not a server here")
      and P.stage(h, "no pppoe radius server 192.0.2.1".split()) is None
      and h["radius"] == [],
      "stage: `no radius server` takes the one named, and says when none is")
h, _ = staged("mtu 1480", "interface x")
P.stage(h, "no pppoe mtu".split())
P.stage(h, "no pppoe interface x".split())
check(h == P.empty() and P.is_empty(h), "stage: `no` takes each back out")
h, _ = staged("mtu 1480", "interface x")
check(P.stage(h, ["no", "pppoe"]) is None and P.is_empty(h),
      "stage: `no pppoe` takes the whole block")
check(P.stage(P.empty(), ["nat"]) == P.SAYS,
      "stage: a line that is not pppoe's is said to be")
check(P._addr4("100.64.0.001") is None and P._addr4("100.64.0.1") ==
      "100.64.0.1", "_addr4: dotted IPv4 only")
check(P._number("12", 1, 20) == 12 and P._number("21", 1, 20) is None
      and P._number("x", 1, 20) is None, "_number: within its bounds")
check(P._radius_server(["192.0.2.1", "secret", "x", "acct-port"])[0] is None,
      "_radius_server: a port with no value is refused")
check(P.pool_error("100.64.0.0/22") is None
      and P.pool_error("100.64.0.1-100.64.3.254") is None,
      "pool_error: a prefix and a range")
check(P.setting_error("bogus", "1").endswith("not a setting of 'pppoe'"),
      "setting_error: an unknown key")

print("\n=== the block: written, nested, read back ===")
check(P.body(held)[:2] == ["interface host-acc0", "interface host-acc0.100"]
      and "radius server 192.0.2.1 secret s3cr3t! auth-port 11812"
      in P.body(held)
      and "local-user lab1 password pw1" in P.body(held),
      "body: the statements, a non-default port kept")
nested = P.nest(held)
check(nested[0] == "pppoe" and nested[-1] == " exit"
      and all(x.startswith(" ") for x in nested[1:]) and P.nest(P.empty()) == [],
      "nest: the block, and nothing when nothing is set")
check(P.setting(held, "mtu") == "1480" and P.setting(P.empty(), "mtu") ==
      "1492", "setting: the value, or the default")
cfg = Config.parse("\n".join(nested) + "\n")
check(cfg.pppoe == held and not cfg.unknown,
      f"Config.parse: the block read back whole: {cfg.unknown}")
check([x for x in cfg.text().splitlines() if x.startswith(("pppoe", " "))
       and ("radius" in x or x == "pppoe")][:1] == ["pppoe"]
      and "\n".join(nested) in cfg.text(),
      "Config.text: the block written where the box writes it")
bad = Config.parse("pppoe\n mtu 9000\n exit\n")
check(bad.unknown and "576-1500" in bad.unknown[0][2],
      "Config.parse: a saved line the prompt would refuse is not taken")
out, why = config.denest_pppoe(list(enumerate(
    ["pppoe", " dns 8.8.8.8", " no mtu", " exit", "hostname x"], 1)))
check([t for _, t in out] == ["pppoe dns 8.8.8.8", "no pppoe mtu",
                              "hostname x"] and why == [],
      "denest_pppoe: the block's lines as the model's")
a = Config.parse("\n".join(nested))
b = Config.parse("\n".join(nested))
check(a.delta_to(b)["pppoe_changed"] is False, "diff: the same block, unchanged")
P.stage(b.pppoe, "pppoe dns 1.1.1.1".split())
check(a.delta_to(b)["pppoe_changed"] is True, "diff: one resolver more, changed")

print("\n=== secrets: never on a screen ===")
shown = P.redact(nested + ["pppoe radius server 192.0.2.1 secret s3cr3t!",
                           "pppoe local-user lab1 password pw1"])
check(not any("s3cr3t!" in x or "pw1" in x for x in shown)
      and " radius server 192.0.2.1 secret <set, not shown> auth-port 11812"
      in shown,
      "redact: nested and flat, the secret and the password blanked, the "
      "port kept")
diff = prismash.compare_redact(["- radius server 192.0.2.1 secret old",
                                "+ radius server 192.0.2.1 secret new"])
check(diff == ["- radius server 192.0.2.1 secret <set, not shown>",
               "+ radius server 192.0.2.1 secret <set, not shown>"],
      "compare_redact: a changed secret is two lines, neither carrying it")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    prismash.Shell.print_config(cfg)
check("s3cr3t!" not in buf.getvalue() and "pw1" not in buf.getvalue()
      and "secret <set, not shown>" in buf.getvalue(),
      "print_config: show running-config blanks them")

print("\n=== what the container cannot run, refused at commit ===")
check(P.check(P.empty()) == [], "check: nothing configured is nothing wrong")
h, _ = staged("interface a")
w = P.check(h)
check(len(w) == 3 and any("gateway" in x for x in w)
      and any("authenticate" in x for x in w)
      and any("address-pool" in x for x in w),
      f"check: no gateway, nobody to authenticate, no address: {w}")
h, _ = staged("interface a", "gateway 100.64.0.1",
              "radius server 192.0.2.1 secret s")
check(P.check(h) == [], "check: RADIUS alone may give every address")

print("\n=== one card, one tag ===")
vl = {"ge0.100": {"outer": 100, "inner": None},
      "ge0.200": {"outer": 200, "inner": 7},
      "ge1.100": {"outer": 100, "inner": None}}
h, _ = staged("interface ge0", "interface ge0.100")
acc, why = P.access(h, vl)
check(why is None and acc == [("ge0", "ge0", 0), ("ge0.100", "ge0", 100)],
      f"access: a card and its VLAN: {acc}")
for ifs, want in ((["ge0.200"], "two tags"), (["ge0.300"], "no 'vlan <id>'"),
                  (["ge0.100", "ge1.100"], "on one card")):
    h, _ = staged(*[f"interface {i}" for i in ifs])
    _, why = P.access(h, vl)
    check(why and want in why, f"access refuses {ifs}: {why}")
check(P.cp_name(0) == "pppoe-cp" and P.cp_name(100) == "pppoe-cp.100",
      "cp_name: the Linux device accel-ppp binds")

print("\n=== the files the container runs ===")
h, _ = staged("interface ge0", "interface ge0.100", "gateway 100.64.0.1",
              "address-pool 100.64.0.0/24", "dns 8.8.8.8",
              "radius server 192.0.2.1 secret s3cr3t!", "tcp-mss 1452",
              "session-limit 900", "authentication pap mschap-v2")
acc, _ = P.access(h, vl)
ac = P.accel_conf(h, acc)
for needle in ("interface=pppoe-cp,vpp-cp=enable",
               "interface=pppoe-cp.100,vpp-cp=enable",
               "server=192.0.2.1,s3cr3t!,auth-port=1812,acct-port=1813",
               "gw-ip-address=100.64.0.1", "100.64.0.0/24", "dns1=8.8.8.8",
               "max-sessions=900", "auth_pap", "auth_mschap_v2", "vyos_vpp",
               "ip-up=/usr/lib/prisma-bng/session", "acct-interim-interval=300",
               "mtu=1492"):
    check(needle in ac, f"accel_conf carries {needle}")
check("auth_chap_md5" not in ac and "[cli]" not in ac
      and "chap-secrets" not in ac,
      "accel_conf: no method not asked for, no CLI (the entrypoint adds it), "
      "no local users when RADIUS is set")
h2, _ = staged("interface ge0", "gateway 100.64.0.1",
               "address-pool 100.64.0.0/24", "local-user a password b")
ac2 = P.accel_conf(h2, P.access(h2, {})[0])
check("chap-secrets\n" in ac2.replace("chap-secrets=", "x=") or
      "[chap-secrets]" in ac2, "accel_conf: local users when no RADIUS")
check(P.chap_secrets(h2).splitlines()[1] == '"a" * "b" *',
      "chap_secrets: the users, in chap-secrets' format")
env = P.access_env(h, acc)
check("PARENT=ge0" in env and 'VLANS="0 100"' in env and "TCP_MSS=1452" in env
      and "MTU=1492" in env, f"access_env: the plumbing's words: {env!r}")
ok, msg = P.write_files(h, acc)
bng = os.environ["PRISMA_BNG_DIR"]
check(ok and all(stat.S_IMODE(os.stat(os.path.join(bng, f)).st_mode) == 0o660
                 for f in ("accel-ppp.conf", "chap-secrets", "access.env"))
      and stat.S_IMODE(os.stat(P.CONF_PATH).st_mode) == 0o660,
      "write_files: written 0660, the configuring group's and never the "
      "world's - they carry the secret")
check(P.read_conf() == h, "read_conf: pppoe.conf read back as the model")
ok, msg = P.write_files(P.empty(), [])
check(ok and msg == "not configured" and not os.path.exists(P.CONF_PATH)
      and not os.path.exists(os.path.join(bng, "accel-ppp.conf")),
      "write_files: nothing configured removes them")
check(P.read_conf() == P.empty(), "read_conf: no file is no PPPoE")
check(P.write_files(h, acc, conf="/proc/no/such")[0] is False,
      "write_files: a file that cannot be written is said")
P._write(os.path.join(_root, "w"), "x")
check(open(os.path.join(_root, "w")).read() == "x", "_write: through a rename")

print("\n=== the screens ===")
TABLE = """\
        sid       |     ifname     | username |     ip      |    calling-sid    | state  | uptime-raw | rx-bytes-raw | tx-bytes-raw
------------------+----------------+----------+-------------+-------------------+--------+------------+--------------+--------------
 8aa8eb1ab0657544 | pppoe_session2 | sub2     | 100.64.0.11 | 02:aa:00:00:00:02 | active | 3725       | 10           | 0
 8aa8eb1ab0657546 | pppoe_session1 | sub1     | 100.64.0.77 | 02:aa:00:00:00:01 | active | 90061      | 0            | 0
"""
r = P.session_rows(TABLE)
check(len(r) == 2 and r[0]["sid"] == "8aa8eb1ab0657544"
      and r[1]["ip"] == "100.64.0.77", "session_rows: accel-cmd's table")
scr = P.sessions_screen(r)
check(scr[1].startswith("8aa8eb1ab0657546  sub1") and scr[-1] ==
      "2 session(s)" and "1d+01:01:01" in scr[1]
      and P.sessions_screen([]) == ["no PPPoE session"],
      "sessions_screen: by user, uptime as a duration, and a count")
r2 = P.session_rows(TABLE.replace("| active | 3725", "| start  | 3725"))
check(P.sessions_screen(r2)[2].endswith("      start"),
      "sessions_screen: a session not active says what it is")
check(P._uptime("61") == "00:01:01" and P._uptime("") == "-",
      "_uptime: seconds as a duration")
check(all(len(x) <= 79 for x in scr), "sessions_screen: fits a console")
STAT = "uptime: 0.00:00:13\nsessions:\n  starting: 1\n  active: 3\n" \
    "  finishing: 0\npppoe:\n  starting: 0\n"
check(P.stat_counts(STAT) == {"starting": 1, "active": 3, "finishing": 0},
      "stat_counts: the sessions block, and not pppoe's below it")
s = P.summary_screen(h, "running", {"active": 3})
check(s[0].endswith("running") and "3 active" in s[1]
      and any("RADIUS: 192.0.2.1" in x for x in s)
      and P.summary_screen(P.empty(), "x", {}) == ["PPPoE is not configured"]
      and "NOT running" in P.summary_screen(h, "stopped", {})[0],
      "summary_screen: the control plane, the sessions, the configuration")
check(P.cp_taps('Interface: tap1863 (ifindex 2)\n  name "pppoe-cp"\n'
                'Interface: tap0 (ifindex 3)\n  name "vppwan"\n') ==
      {"tap1863"}, "cp_taps: the TAP by its Linux name")
check(P.interface_names("  Name  Idx\nge0  1  up\n  rx\nge0.100  2  up\n")
      == {"ge0", "ge0.100"}, "interface_names: a sub-interface included")

print("\n=== clear, and the doors ===")
check(P.clear_words(["all"]) == (["all"], None)
      and P.clear_words(["8AA8EB1AB0657544"])[0] == ["sid", "8aa8eb1ab0657544"]
      and P.clear_words(["user", "sub1"])[0] == ["username", "sub1"],
      "clear_words: an id, a user, all")
for words in (["12"], ["user"], ["user", "a;b"], ["all", "now"], []):
    check(P.clear_words(words)[0] is None, f"clear_words refuses {words}")
CTL = P.BNG_CTL_HOST
for argv, want in (((CTL, "apply"), "start or restart"),
                   ((CTL, "stop"), "stop the PPPoE"),
                   ((CTL, "terminate", "sid", "8aa8eb1ab0657544"), "end PPPoE"),
                   ((CTL, "terminate", "username", "sub1"), "end PPPoE"),
                   ((CTL, "terminate", "all"), "end PPPoE"),
                   ((CTL, "terminate", "username", "a;b"), None),
                   ((CTL, "terminate", "sid", "12"), None),
                   ((CTL, "terminate", "all", "x"), None),
                   ((CTL, "sessions"), None)):
    v = doors.verb_of(list(argv))
    check((v is None and want is None) or (want and v and want in v),
          f"verb_of {argv[1:]}: {v}")
check(all((CTL, q) in doors.QUERIES for q in ("status", "sessions", "summary")),
      "QUERIES: any account may read the state and the sessions")
check(doors._clear_back(["username", "x"]) == ["user", "x"]
      and doors._clear_back(["sid", "y"]) == ["y"],
      "_clear_back: terminate's words as clear's")

print("\n=== a session is not configuration ===")
c = Config.parse("interface ge0\n mtu 9000\n exit\n")
c.mtus.update({"pppoe_session0": 1492, "pppoe_session12": 1492,
               "tap1863": 1500, "tap0": 1500})
c.interfaces.update({"pppoe_session0": "inside", "ge0": "inside"})
c.drop_pppoe_sessions({"tap1863"})
check(sorted(c.mtus) == ["ge0", "tap0"] and c.interfaces == {"ge0": "inside"},
      "drop_pppoe_sessions: sessions, their CGNAT and the cp TAP out; an "
      "operator's TAP and attachment kept")


print("\n=== the VTY: the level, apply, show, clear ===")


class Dp(prismash.Vpp):
    TRACE = False

    def __init__(self, answers=None):
        self.answers = dict(answers or {})
        self.commands = []

    def run(self, cmd):
        self.commands.append(cmd)
        for k, v in self.answers.items():
            if cmd.startswith(k):
                return v
        return 0, ""


def shell(dp=None, booting=False):
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT, prismash.CAP_OPERATOR}
    sh.dp = dp or Dp()
    sh.sys = system.System(runner=lambda argv, stdin=None, timeout=30: (0, ""))
    sh.booting = booting
    sh.running_config = lambda: Config.parse("")
    return sh


def ctl(rc=0, says="started"):
    """A stand-in prisma-bng, recording its argv."""
    p = os.environ["PRISMA_BNG_CTL"]
    with open(p, "w") as f:
        f.write(f"#!/bin/sh\necho \"$@\" >> '{p}.argv'\necho '{says}'\n"
                f"exit {rc}\n")
    os.chmod(p, 0o755)
    if os.path.exists(p + ".argv"):
        os.unlink(p + ".argv")
    return p + ".argv"


def said(fn):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rv = fn()
    return rv, out.getvalue()


sh = shell()
check(sh.pppoe_rewrite("pppoe") == "" and sh.nat_ctx == ["pppoe"],
      "pppoe_rewrite: `pppoe` enters the level")
check(sh.pppoe_rewrite("dns 8.8.8.8") == "pppoe dns 8.8.8.8"
      and sh.pppoe_rewrite("no mtu") == "no pppoe mtu",
      "pppoe_rewrite: what is typed there, in the model's words")
check(sh.pppoe_rewrite("exit") == "" and sh.nat_ctx == []
      and sh.pppoe_rewrite("no pppoe") == "no pppoe",
      "pppoe_rewrite: `exit` leaves; `no pppoe` at the top goes on")
check(shell(Dp({"show tap": (0, 'Interface: tap1863 (ifindex 2)\n'
                                '  name "pppoe-cp"\n')}))
      .device_pppoe_taps() == {"tap1863"}
      and shell(Dp({"show tap": (1, "")})).device_pppoe_taps() == set(),
      "device_pppoe_taps: read off the box")

IFS = (0, "  Name  Idx\nhost-acc0  1  up\nhost-acc0.100  2  up\n")
tgt = Config.parse("interface host-acc0.100\n vlan 100\n exit\npppoe\n"
                   " interface host-acc0\n interface host-acc0.100\n"
                   " gateway 100.64.0.1\n address-pool 100.64.0.0/24\n"
                   " radius server 192.0.2.1 secret s\n exit\n")
log = ctl(0, "started")
rv, out = said(lambda: shell(Dp({"show interface": IFS})).apply_pppoe(tgt))
check(rv is True and "pppoe: started" in out
      and open(log).read().split() == ["apply"]
      and os.path.exists(os.path.join(bng, "accel-ppp.conf")),
      f"apply_pppoe: the files written, the host asked to apply: {out!r}")
log = ctl(1, "could not start bng")
rv, out = said(lambda: shell(Dp({"show interface": IFS})).apply_pppoe(tgt))
check(rv is False and "did not start" in out and "could not start bng" in out,
      "apply_pppoe: a container that does not start refuses the commit")
rv, out = said(lambda: shell(Dp({"show interface": IFS}), booting=True)
               .apply_pppoe(tgt))
check(rv is True and out.startswith("warning:"),
      "apply_pppoe: at boot the same is a warning, the box comes up")
log = ctl(0, "started")
_conf = P.CONF_PATH
P.CONF_PATH = "/proc/no/such/pppoe.conf"
rv, out = said(lambda: shell(Dp({"show interface": IFS})).apply_pppoe(tgt))
P.CONF_PATH = _conf
check(rv is False and "error: pppoe: could not write" in out
      and not os.path.exists(log),
      "apply_pppoe: files that cannot be written refuse the commit, and the "
      "host is asked nothing")
log = ctl(0, "stopped")
rv, out = said(lambda: shell(Dp({"show interface": IFS})).apply_pppoe(Config()))
check(rv is True and open(log).read().split() == ["stop"]
      and not os.path.exists(os.path.join(bng, "accel-ppp.conf")),
      "apply_pppoe: nothing configured stops it and removes the files")
log = ctl(0, "started")
rv, out = said(lambda: shell(Dp({"show interface": (0, "host-acc0 1 up\n")}))
               .apply_pppoe(tgt))
check(rv is False and "no interface host-acc0.100" in out
      and not os.path.exists(log),
      "apply_pppoe: an access interface the dataplane lacks is refused, "
      "nothing asked of the host")

log = ctl(0, "")
rv, out = said(lambda: shell().clear_pppoe(["session", "all"]))
check("cleared: all" in out and open(log).read().split() ==
      ["terminate", "all"], f"clear_pppoe: all: {out!r}")
log = ctl(3, "not running")
rv, out = said(lambda: shell().clear_pppoe(["session", "user", "sub1"]))
check("PPPoE is not running" in out, "clear_pppoe: not running, said")
log = ctl(0, "")
rv, out = said(lambda: shell().clear_pppoe(["session", "user"]))
check("nothing cleared" in out and not os.path.exists(log),
      "clear_pppoe: a malformed target asks nothing of the host")
rv, out = said(lambda: shell().clear_pppoe(["nonsense"]))
check(out.startswith("error: usage"), "clear_pppoe: usage")
sh = shell()
sh.caps = set()
log = ctl(0, "")
rv, out = said(lambda: sh.clear_pppoe(["session", "all"]))
check("permission denied" in out and not os.path.exists(log),
      "clear_pppoe: an account without the role is refused")

log = ctl(0, TABLE)
rv, out = said(lambda: shell().show_pppoe(["sessions"]))
check("sub1" in out and "2 session(s)" in out, "show_pppoe sessions")
log = ctl(3, "not running")
rv, out = said(lambda: shell().show_pppoe(["sessions"]))
check("PPPoE is not running" in out, "show_pppoe sessions: not running")
log = ctl(0, "running")
rv, out = said(lambda: shell().show_pppoe(["summary"]))
check("PPPoE is not configured" in out, "show_pppoe summary: nothing set")
rv, out = said(lambda: shell().show_pppoe(["nonsense"]))
check("usage: show pppoe" in out, "show_pppoe: usage")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
