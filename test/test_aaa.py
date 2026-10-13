#!/usr/bin/env python3
"""
RADIUS under `aaa` > `radius` (#607): src/prismash/aaa.py, the block in the
configuration model, the VTY's levels, what accel-ppp is given, the move of
0.154.0's `pppoe radius ...`, and the commit's restart-or-reload decision.

Each behaviour, shown red by a mutation (named beside the box):

  * one rule for the prompt and the saved file (mutation: a secret with a
    comma taken, which would split accel-ppp's `server=` line);
  * a server typed with no priority goes after the others, and typed again
    keeps its place (mutation: _put_server() giving every new server 1);
  * the failover order is the priority order, as accel-ppp levels
    (mutation: _levels() returning the typed order's index, so a server
    typed first with priority 9 is tried first);
  * round-robin is one level (mutation: _levels() ignoring the mode);
  * a server marked down for dead-time, max-fail = max-try = retries + 1
    (mutation: fail-time=0, 0.154.0's line, back);
  * a session is never ended for an unanswered interim (mutation:
    acct-timeout=0 left out);
  * a role is the other port as 0 (mutation: role acct writing the auth
    port);
  * 0.154.0's `pppoe radius` lines read as `aaa radius`, secret, ports and
    order kept, and said (mutation: migrate_pppoe() dropping the ports);
  * no secret on a screen: nested, flat, in a compare, in the API's echo
    (mutation: redact() matching only the nested `server` line);
  * a RADIUS-only change is a reload, never a restart, unless what changed
    is read at start only (mutation: the [modules] comparison left out, so a
    first server - the radius module appearing - is a reload).
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile

_root = tempfile.mkdtemp(prefix="aaa-")
import atexit  # noqa: E402
atexit.register(shutil.rmtree, _root, True)
for k, v in (("PRISMA_PPPOE_CONF", "pppoe.conf"), ("PRISMA_BNG_DIR", "bng"),
             ("PRISMA_AAA_CONF", "aaa.conf"),
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
import aaa as A  # noqa: E402
import config  # noqa: E402
import pppoe as P  # noqa: E402
import prismash  # noqa: E402
import system  # noqa: E402
import typedcfg  # noqa: E402
from config import Config  # noqa: E402

fail = 0


def check(c, msg):
    global fail
    print(("  [ ok ] " if c else "  FAIL: ") + msg)
    fail += 0 if c else 1


def staged(*lines):
    held = A.empty()
    whys = [A.stage(held, ["aaa", "radius"] + ln.split()) for ln in lines]
    return held, whys


print("=== the statements: one rule, the prompt's and the file's ===")
held, whys = staged(
    "server 192.0.2.1 secret s3cr3t! auth-port 11812",
    "server 192.0.2.2 secret two role auth priority 5",
    "server 192.0.2.3 secret three role acct acct-port 11813",
    "mode failover", "timeout 2", "retries 2", "dead-time 10",
    "nas-identifier bng-1", "interim-interval 600",
    "dynamic-authorization listen 10.0.0.1 port 3800",
    "dynamic-authorization client 10.0.0.9 secret dae9",
    "dynamic-authorization client 10.0.0.8 secret dae8")
check(whys == [None] * len(whys), f"stage: every statement taken: {whys}")
check([(r["address"], r["priority"], r["role"]) for r in held["servers"]]
      == [("192.0.2.1", "1", "both"), ("192.0.2.2", "5", "auth"),
          ("192.0.2.3", "6", "acct")]
      and held["servers"][0]["auth-port"] == "11812"
      and held["servers"][0]["acct-port"] == "1813",
      "stage: a server with no priority goes after the highest, the ports "
      "defaulted")
check(held["settings"] == {"mode": "failover", "timeout": "2",
                           "retries": "2", "dead-time": "10",
                           "nas-identifier": "bng-1",
                           "interim-interval": "600"}
      and held["dae"] == {"listen": "10.0.0.1", "port": "3800",
                          "clients": {"10.0.0.9": "dae9",
                                      "10.0.0.8": "dae8"}},
      "stage: the settings and the listener as typed")
for line, want in (
        ("server 192.0.2.1 secret a,b", "no space, comma"),
        ("server 192.0.2.1 secret a#b", "no space, comma"),
        ("server radius.example secret x", "not an IPv4 address"),
        ("server 192.0.2.1 secret x auth-port 0", "auth-port 1-65535"),
        ("server 192.0.2.1 secret x priority 0", "priority 1-255"),
        ("server 192.0.2.1 secret x priority 256", "priority 1-255"),
        ("server 192.0.2.1 secret x role all", "role auth, acct or both"),
        ("server 192.0.2.1 secret x weight 2", "server <address> secret"),
        ("server 192.0.2.1 secret x role", "server <address> secret"),
        ("server 192.0.2.1", "server <address> secret"),
        ("mode random", "failover or round-robin"),
        ("timeout 0", "1-60 seconds"), ("timeout 61", "1-60 seconds"),
        ("retries 11", "0-10 sends"), ("dead-time 0", "1-3600 seconds"),
        ("interim-interval 30", "0 (none) or 60-86400"),
        ("nas-identifier a/b", "letters, digits"),
        ("timeout", "timeout <value>"),
        ("dynamic-authorization listen x", "listen <address> [port <n>]"),
        ("dynamic-authorization listen 10.0.0.1 port 0", "[port <n>]"),
        ("dynamic-authorization listen 10.0.0.1 pot 1", "[port <n>]"),
        ("dynamic-authorization client 10.0.0.9", "client <address> secret"),
        ("dynamic-authorization client 10.0.0.9 secret a,b",
         "no space, comma"),
        ("dynamic-authorization peer 1", "dynamic-authorization listen"),
        ("accounting on", "under 'radius'")):
    h = A.empty()
    why = A.stage(h, ["aaa", "radius"] + line.split())
    check(why and want in why and h == A.empty(),
          f"stage refuses '{line}': {why}")
check(A.stage(A.empty(), ["aaa", "tacacs"]) == A.LEVELS_SAY
      and A.stage(A.empty(), ["nat"]) == A.LEVELS_SAY
      and A.stage(A.empty(), ["aaa", "radius"]) == A.SAYS,
      "stage: what is not radius's is said to be")
h, _ = staged("server 192.0.2.1 secret a priority 7", "server 192.0.2.2 "
              "secret b")
A.stage(h, "aaa radius server 192.0.2.1 secret c".split())
check([(r["secret"], r["priority"]) for r in h["servers"]]
      == [("c", "7"), ("b", "8")],
      "stage: a server typed again replaces itself and keeps its priority")
A.stage(h, "aaa radius server 192.0.2.1 secret c priority 9".split())
check(h["servers"][0]["priority"] == "9",
      "stage: unless it is given another")
h, _ = staged(*[f"server 192.0.2.{i} secret s" for i in range(1, 9)])
check(A.stage(h, "aaa radius server 192.0.2.9 secret s".split())
      == "at most 8 RADIUS servers"
      and A.stage(h, "aaa radius server 192.0.2.8 secret t".split()) is None,
      "stage: at most 8, and the eighth may still be changed")
h, _ = staged("server 192.0.2.1 secret a priority 255")
check("no priority left" in A.stage(h, "aaa radius server 192.0.2.2 secret "
                                       "b".split()),
      "stage: no priority after 255 to give, and said how to give one")
h, _ = staged("server 192.0.2.1 secret a", "timeout 9",
              "dynamic-authorization listen 10.0.0.1",
              "dynamic-authorization client 10.0.0.9 secret x")
check(A.stage(h, "no aaa radius server 192.0.2.9".split())
      .endswith("is not a server here")
      and A.stage(h, "no aaa radius server".split()) == "no server <address>"
      and A.stage(h, "no aaa radius timeout 3".split()) == "no timeout"
      and A.stage(h, "no aaa radius dynamic-authorization client 10.0.0.1"
                     .split()).endswith("is not a client here")
      and A.stage(h, "no aaa radius dynamic-authorization peer".split())
      .startswith("no dynamic-authorization"),
      "stage: a `no` for what is not there is said")
A.stage(h, "no aaa radius server 192.0.2.1".split())
A.stage(h, "no aaa radius timeout".split())
A.stage(h, "no aaa radius dynamic-authorization listen".split())
A.stage(h, "no aaa radius dynamic-authorization client 10.0.0.9".split())
check(h == A.empty() and A.is_empty(h), "stage: `no` takes each back out")
h, _ = staged("server 192.0.2.1 secret a", "dynamic-authorization listen "
              "10.0.0.1", "dynamic-authorization client 10.0.0.9 secret x")
A.stage(h, "no aaa radius dynamic-authorization".split())
check(h["dae"] == A.empty()["dae"] and h["servers"],
      "stage: `no dynamic-authorization` takes the listener and its clients")
check(A.stage(h, ["no", "aaa"]) is None and A.is_empty(h),
      "stage: `no aaa` takes the whole block")
h, _ = staged("server 192.0.2.1 secret a")
check(A.stage(h, ["no", "aaa", "radius"]) is None and A.is_empty(h),
      "stage: and so does `no radius` under `aaa`")
check(A._addr4("192.0.2.001") is None and A._number("x", 1, 2) is None
      and A._number("3", 1, 2) is None and A._number("2", 1, 2) == 2,
      "_addr4, _number: dotted IPv4, within bounds")
check(A.setting(A.empty(), "mode") == "failover"
      and A.setting(held, "timeout") == "2"
      and A.setting_error("bogus", "1").endswith("not a setting of 'radius'"),
      "setting, setting_error: the value or the default; an unknown key")
check(A.server_words(["192.0.2.1", "secret", "x", "priority", "007"])[0]
      ["priority"] == "7", "server_words: a priority held as a number")
h = A.empty()
srv, _ = A.server_words(["192.0.2.5", "secret", "x"])
check(A._put_server(h, srv) is None and srv["priority"] == "1"
      and A.server_line(srv) == "server 192.0.2.5 secret x priority 1",
      "_put_server, server_line: the first server is priority 1, written "
      "with no default port or role")
check(A._dae(h, False, ["listen", "10.0.0.1"]) is None
      and h["dae"]["port"] == "3799"
      and A._dae(h, True, ["listen"]) is None and h["dae"]["listen"] == "",
      "_dae: a listener on 3799 when no port is given, and taken out")
check(P.uses_radius(h) and not P.uses_radius(A.empty()),
      "pppoe.uses_radius: whenever 'aaa radius' has a server")

print("\n=== the block: written, nested, read back ===")
b = A.body(held)
check(b[:3] == ["server 192.0.2.1 secret s3cr3t! auth-port 11812 priority 1",
                "server 192.0.2.2 secret two role auth priority 5",
                "server 192.0.2.3 secret three acct-port 11813 role acct "
                "priority 6"]
      and "dynamic-authorization listen 10.0.0.1 port 3800" in b
      and b[-2:] == ["dynamic-authorization client 10.0.0.8 secret dae8",
                     "dynamic-authorization client 10.0.0.9 secret dae9"],
      f"body: by priority, every priority written, clients by address: {b}")
h, _ = staged("server 192.0.2.9 secret z priority 2",
              "server 192.0.2.8 secret y priority 1")
check([r["address"] for r in A.ordered(h)] == ["192.0.2.8", "192.0.2.9"]
      and A.body(h)[0].startswith("server 192.0.2.8"),
      "ordered: priority first, whatever was typed first")
nested = A.nest(held)
check(nested[:2] == ["aaa", " radius"] and nested[-2:] == ["  exit", " exit"]
      and all(x.startswith("  ") for x in nested[2:-1])
      and A.nest(A.empty()) == [],
      "nest: two levels, and nothing when nothing is set")
cfg = Config.parse("\n".join(nested) + "\n")
check(cfg.aaa == held and not cfg.unknown,
      f"Config.parse: the block read back whole: {cfg.unknown}")
text = cfg.text()
check("\n".join(nested) in text, "Config.text: the block, as nest() wrote it")
both = Config.parse("\n".join(nested) + "\npppoe\n mtu 1480\n exit\n")
check(both.text().index("aaa\n") < both.text().index("pppoe\n"),
      "Config.text: `aaa` before the `pppoe` that uses it")
out, bad = config.denest_aaa(list(enumerate(
    ["aaa", " radius", "  timeout 5", "  no mode", "  exit", " tacacs",
     " exit", "hostname x"], 1)))
check([t for _, t in out] == ["aaa radius timeout 5", "no aaa radius mode",
                              "hostname x"]
      and bad == [(6, "tacacs", A.LEVELS_SAY)],
      f"denest_aaa: the block's lines as the model's; a level that is not "
      f"radius refused: {bad}")
bad = Config.parse("aaa\n radius\n  timeout 99\n  exit\n exit\n")
check(bad.unknown and "1-60" in bad.unknown[0][2],
      "Config.parse: a saved line the prompt would refuse is not taken")
a = Config.parse(text)
b2 = Config.parse(text)
check(a.delta_to(b2)["aaa_changed"] is False, "diff: the same block, unchanged")
A.stage(b2.aaa, "aaa radius dead-time 60".split())
d = a.delta_to(b2)
check(d["aaa_changed"] is True and d["pppoe_changed"] is False,
      "diff: a timer, changed - RADIUS's and not PPPoE's")
check("aaa_changed" in prismash.DELTA_ADMIN,
      "DELTA_ADMIN: an administrator's change")

print("\n=== 0.154.0's `pppoe radius`, read as `aaa radius` ===")
OLD = ("pppoe\n interface host-acc0\n gateway 100.64.0.1\n"
       " radius server 192.0.2.7 secret old7 auth-port 11812\n"
       " radius server 192.0.2.6 secret old6\n"
       " radius nas-identifier edge-1\n radius interim-interval 120\n"
       " local-user lab password pw\n exit\n")
c = Config.parse(OLD)
check(not c.unknown and c.pppoe["interfaces"] == ["host-acc0"]
      and c.pppoe["users"] == {"lab": "pw"},
      f"Config.parse: a 0.154.0 file loads: {c.unknown}")
check([(r["address"], r["secret"], r["auth-port"], r["priority"])
       for r in A.ordered(c.aaa)]
      == [("192.0.2.7", "old7", "11812", "1"), ("192.0.2.6", "old6", "1812",
                                                 "2")]
      and c.aaa["settings"] == {"nas-identifier": "edge-1",
                                "interim-interval": "120"},
      "migrate: the secret, the port and the file's order kept")
check([n for n, _, _ in c.moved] == [4, 5, 6, 7]
      and c.moved[0][2] == "aaa radius server 192.0.2.7 secret old7 "
                           "auth-port 11812"
      and "radius" not in "\n".join(P.nest(c.pppoe)),
      "Config.moved: each line and what it is now; the pppoe block without "
      "them")
w = Config.parse(c.text())
check(w.aaa == c.aaa and w.pppoe == c.pppoe and not w.moved,
      "the file written from it reads back with nothing left to move")
check(Config.parse("pppoe\n radius bogus 1\n exit\n").unknown,
      "a `pppoe radius` 0.154.0 never wrote is refused, not guessed")
check(A.migrate_pppoe(A.empty(), ["mtu", "1"]) is None
      and A.migrate_pppoe(A.empty(), ["radius", "server", "x"]) is None,
      "migrate_pppoe: only what it can read")
legacy = os.path.join(_root, "legacy-pppoe.conf")
with open(legacy, "w") as f:
    f.write(OLD)
r = A.read_conf(os.path.join(_root, "absent"), legacy)
check([s["address"] for s in A.ordered(r)] == ["192.0.2.7", "192.0.2.6"]
      and r["settings"]["nas-identifier"] == "edge-1",
      "read_conf: no aaa.conf yet, so a 0.154.0 pppoe.conf's servers")
check(A.read_conf(os.path.join(_root, "absent"), None) == A.empty(),
      "read_conf: neither file, no RADIUS")

print("\n=== secrets: never on a screen ===")
lines = nested + ["aaa radius server 192.0.2.1 secret s3cr3t!",
                  "no aaa radius server 192.0.2.1",
                  "server 192.0.2.4 secret s3cr3t! priority 2"]
shown = A.redact(lines)
check(not any(x in s for s in shown
              for x in ("s3cr3t!", "two", "three", "dae9", "dae8"))
      and "  server 192.0.2.1 secret <set, not shown> auth-port 11812 "
          "priority 1" in shown
      and "  dynamic-authorization client 10.0.0.9 secret <set, not shown>"
      in shown,
      "redact: every server's and every client's, nested and flat, the "
      "rest of the line kept")
check(A.redact(shown) == shown, "redact: a blanked line is left as it is")
diff = prismash.compare_redact(["-  server 192.0.2.1 secret old priority 1",
                                "+  server 192.0.2.1 secret new priority 1"])
check(diff == ["-  server 192.0.2.1 secret <set, not shown> priority 1",
               "+  server 192.0.2.1 secret <set, not shown> priority 1"],
      f"compare_redact: a changed secret is two lines, neither with it")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    prismash.Shell.print_config(cfg)
check("s3cr3t!" not in buf.getvalue() and "dae9" not in buf.getvalue()
      and "secret <set, not shown>" in buf.getvalue(),
      "print_config: show running-config blanks them")
check(typedcfg.shown_lines(["aaa radius server 192.0.2.1 secret s3cr3t!"])
      == ["aaa radius server 192.0.2.1 secret <set, not shown>"],
      "shown_lines: the API's echo of a staged line blanks it too")

print("\n=== what accel-ppp cannot use, refused at commit ===")
check(A.check(A.empty()) == [] and A.check(held) == [],
      "check: nothing, or a whole block, is nothing wrong")
h, _ = staged("dynamic-authorization listen 10.0.0.1")
check(any("no client" in x for x in A.check(h)), "check: a listener, no client")
h, _ = staged("dynamic-authorization client 10.0.0.9 secret x")
check(any("nowhere to listen" in x for x in A.check(h)),
      "check: clients, no listener")
h, _ = staged("server 192.0.2.1 secret x role acct")
check(any("no server takes authentication" in x for x in A.check(h))
      and A.auth_servers(h) == [] and len(A.acct_servers(h)) == 1,
      "check: accounting servers and none for authentication")
pp = P.empty()
P.stage(pp, "pppoe interface ge0".split())
P.stage(pp, "pppoe gateway 100.64.0.1".split())
check(any("'aaa radius'" in x for x in P.check(pp, A.empty())),
      "pppoe.check: no local user and no 'aaa radius' server, named")

print("\n=== what accel-ppp is given ===")
fo = "\n".join(A.accel_radius(held, "100.64.0.1"))
for needle in ("[radius]", "nas-identifier=bng-1", "gw-ip-address=100.64.0.1",
               "timeout=2", "max-try=3", "acct-timeout=0",
               "acct-interim-interval=600",
               "server=192.0.2.1,s3cr3t!,auth-port=11812,acct-port=1813,"
               "req-limit=0,fail-time=10,max-fail=3\n",
               "server=192.0.2.2,two,auth-port=1812,acct-port=0,req-limit=0,"
               "fail-time=10,max-fail=3,backup=1\n",
               "server=192.0.2.3,three,auth-port=0,acct-port=11813,"
               "req-limit=0,fail-time=10,max-fail=3,backup=2",
               "dae-server=10.0.0.1:3800,dae8",
               "dae-client=10.0.0.8,dae8", "dae-client=10.0.0.9,dae9"):
    check(needle in fo + "\n", f"accel_radius (failover) carries {needle!r}")
check("fail-time=0" not in fo, "accel_radius: no server never marked down")
rr = dict(held, settings=dict(held["settings"], mode="round-robin"))
rrt = "\n".join(A.accel_radius(rr, "100.64.0.1"))
check("backup" not in rrt and rrt.count("\nserver=") == 3,
      "accel_radius (round-robin): every server on one level")
check(A._levels(rr) == {"192.0.2.1": 0, "192.0.2.2": 0, "192.0.2.3": 0}
      and A._levels(held) == {"192.0.2.1": 0, "192.0.2.2": 1, "192.0.2.3": 2},
      "_levels: one level, or the rank of each priority")
h, _ = staged("server 192.0.2.1 secret a priority 10",
              "server 192.0.2.2 secret b priority 10",
              "server 192.0.2.3 secret c priority 20")
check(A._levels(h) == {"192.0.2.1": 0, "192.0.2.2": 0, "192.0.2.3": 1},
      "_levels: one priority shares a level; the next priority is the next "
      "level, whatever its number")
dflt = "\n".join(A.accel_radius(staged("server 192.0.2.1 secret a")[0],
                                "100.64.0.1"))
check("timeout=3" in dflt and "max-try=2" in dflt and "fail-time=30" in dflt
      and "max-fail=2" in dflt and "nas-identifier=prisma-routing" in dflt
      and "acct-interim-interval=300" in dflt and "dae-" not in dflt,
      "accel_radius: the defaults - a dead server costs 3 s x 2, then is "
      "left alone 30 s")
check(A.accel_radius(A.empty(), "x") == [], "accel_radius: no server, nothing")
check(A.dae_words(held) == ("10.0.0.1", "3800",
                            (("10.0.0.8", "dae8"), ("10.0.0.9", "dae9"))),
      "dae_words: the listener, for the restart-or-reload decision")
pp2 = P.empty()
for l in ("interface ge0", "gateway 100.64.0.1"):
    P.stage(pp2, f"pppoe {l}".split())
whole = P.accel_conf(pp2, [("ge0", "ge0", 0)], held)
check("[radius]" in whole and "server=192.0.2.1" in whole
      and "\nradius\n" in whole and "chap-secrets" not in whole,
      "pppoe.accel_conf: the [radius] the aaa block gives")

print("\n=== aaa.conf ===")
ok, msg = A.write_conf(held)
check(ok and A.read_conf() == held
      and oct(os.stat(A.CONF_PATH).st_mode & 0o777) == "0o660",
      "write_conf, read_conf: written 0660 and read back as the model")
ok, msg = A.write_conf(A.empty())
check(ok and msg == "not configured" and not os.path.exists(A.CONF_PATH),
      "write_conf: nothing configured removes it")
check(A.write_conf(held, "/proc/no/such")[0] is False,
      "write_conf: a file that cannot be written is said")
A._write(os.path.join(_root, "w"), "x")
check(open(os.path.join(_root, "w")).read() == "x", "_write: by a rename")

print("\n=== the screens ===")
STAT = ("uptime: 0.00:01:00\nsessions:\n  active: 2\n"
        "radius(1, 192.0.2.1):\n  state: failed\n  fail count: 1\n"
        "radius(2, 192.0.2.2):\n  state: active\n")
check(A.server_states(STAT) == {"192.0.2.1": "failed", "192.0.2.2": "active"},
      "server_states: accel-ppp's own word for each server")
rows = A.summary_rows(held, A.server_states(STAT))
check(rows[0].startswith("radius") and "failover" in rows[0]
      and "dead-time 10 s" in rows[0]
      and any("192.0.2.1" in x and "failed" in x and "priority 1" in x
              for x in rows)
      and any("192.0.2.3" in x and "acct" in x for x in rows)
      and any("disconnect/CoA" in x and "2 client(s)" in x for x in rows)
      and not any(x.startswith("accounting") for x in rows)
      and all(len(x) <= 79 for x in rows),
      f"summary_rows: the mode, each server, its state: {rows}")
h, _ = staged("server 192.0.2.1 secret a role auth")
check(any("accounting" in x and "off" in x for x in A.summary_rows(h))
      and A.summary_rows(A.empty()) == [],
      "summary_rows: no accounting server says accounting is off")

print("\n=== the VTY: the levels, the move, show this ===")


class Dp(prismash.Vpp):
    TRACE = False

    def __init__(self, answers=None):
        self.answers = dict(answers or {})

    def run(self, cmd):
        for k, v in self.answers.items():
            if cmd.startswith(k):
                return v
        return 0, ""


def shell(dp=None, running=None):
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT, prismash.CAP_OPERATOR}
    sh.dp = dp or Dp()
    sh.sys = system.System(runner=lambda argv, stdin=None, timeout=30: (0, ""))
    sh.booting = False
    sh.running_config = lambda: running or Config.parse("")
    return sh


def said(fn):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rv = fn()
    return rv, out.getvalue()


sh = shell()
check(sh.aaa_rewrite("aaa") == "" and sh.nat_ctx == ["aaa"],
      "aaa_rewrite: `aaa` enters the level")
_, out = said(lambda: sh.aaa_rewrite("server 1.2.3.4 secret x"))
check("under 'aaa' there is 'radius'" in out and sh.nat_ctx == ["aaa"],
      "aaa_rewrite: under `aaa`, only `radius`")
check(sh.aaa_rewrite("radius") == "" and sh.nat_ctx == ["aaa", "radius"],
      "aaa_rewrite: `radius` enters its level")
sh.mode = "config"
check(sh.grammar_mode() == "radius"
      and sh.aaa_rewrite("timeout 5") == "aaa radius timeout 5"
      and sh.aaa_rewrite("no server 1.2.3.4") == "no aaa radius server 1.2.3.4",
      "aaa_rewrite: what is typed there, in the model's words, `?` from "
      "radius's tree")
check(sh.aaa_rewrite("exit") == "" and sh.nat_ctx == ["aaa"]
      and sh.aaa_rewrite("no radius") == "no aaa radius"
      and sh.aaa_rewrite("exit") == "" and sh.nat_ctx == []
      and sh.aaa_rewrite("no aaa") == "no aaa",
      "aaa_rewrite: `exit` leaves each; `no radius`, `no aaa` go on")
check(sh.aaa_rewrite("interface ge0") == "interface ge0",
      "aaa_rewrite: a line that is not its own goes on untouched")
check(set(prismash.TREE["radius"]) >= {"server", "mode", "timeout", "retries",
                                       "dead-time", "dynamic-authorization",
                                       "no", "exit"}
      and "radius" not in prismash.TREE["pppoe"]
      and "rate-limit" in prismash.TREE["pppoe"]
      and "aaa" in prismash.TREE["config"],
      "TREE: `aaa` at the top, `radius` under it, and none in `pppoe`")

sh = shell()
sh.mode = "config"
sh.candidate = Config.parse("")
for line in ("aaa", "radius", "server 192.0.2.1 secret s3cr3t!",
             "mode round-robin", "exit", "exit"):
    sh.feed(line)
check([r["address"] for r in sh.candidate.aaa["servers"]] == ["192.0.2.1"]
      and A.setting(sh.candidate.aaa, "mode") == "round-robin",
      "typed at the prompt, staged into the candidate's block")
_, out = said(lambda: (sh.feed("pppoe"),
                       sh.feed("radius server 192.0.2.2 secret x"),
                       sh.feed("exit")))
check("under 'aaa' > 'radius'" in out
      and len(sh.candidate.aaa["servers"]) == 1,
      f"`radius` under `pppoe` is refused with where it went: {out!r}")
for line in ("aaa", "radius"):
    sh.feed(line)
_, out = said(lambda: sh.show_this())
check("server 192.0.2.1 secret <set, not shown> priority 1" in out
      and "s3cr3t!" not in out and "mode round-robin" in out,
      f"show this, in `radius`: the block, the secret blanked: {out!r}")
sh.feed("exit")
_, out = said(lambda: sh.show_this())
check(out.startswith("radius\n") and "s3cr3t!" not in out,
      f"show this, in `aaa`: `radius` and what is in it: {out!r}")
sh.feed("exit")
sh.feed("pppoe")
sh.feed("mtu 1480")
_, out = said(lambda: sh.show_this())
check(out.strip() == "mtu 1480",
      f"show this, in `pppoe`: the pppoe block, not the CGNAT's: {out!r}")
sh.feed("exit")
sh2 = shell()
sh2.caps = set()
sh2.mode = "config"
sh2.candidate = Config.parse("")
_, out = said(lambda: (sh2.feed("aaa"), sh2.feed("radius"),
                       sh2.feed("timeout 5")))
check("permission denied" in out and sh2.candidate.aaa == A.empty(),
      "an account without the role is refused")

print("\n=== rate profiles: what a Cisco-AVPair policy name means ===")
h, whys = staged("rate-profile PLANO_100M down 100M up 50M",
                 "rate-profile free down 0 up 512")
check(whys == [None, None]
      and h["profiles"] == {"PLANO_100M": {"down": "100M", "up": "50M"},
                            "free": {"down": "0", "up": "512"}},
      "stage: a profile by name, its two rates")
for line, want in (("rate-profile x down 1M", "rate-profile <name> down"),
                   ("rate-profile a/b down 1M up 1M", "rate-profile <name>"),
                   ("rate-profile x down 11G up 1M", "rate-profile <name>"),
                   ("rate-profile x up 1M down 1M", "rate-profile <name>")):
    hh = A.empty()
    why = A.stage(hh, ["aaa", "radius"] + line.split())
    check(why and want in why and hh == A.empty(), f"stage refuses '{line}'")
check(A.body(h)[-2:] == ["rate-profile PLANO_100M down 100M up 50M",
                         "rate-profile free down 0 up 512"]
      and A.shaper_profiles(h) == ["cisco-profile=PLANO_100M,100000,50000",
                                   "cisco-profile=free,0,512"],
      "body, shaper_profiles: written by name; to the shaper in kbit/s")
hp = A.empty()
check(A._profile(hp, False, ["gold", "down", "1G", "up", "500M"]) is None
      and A._profile(hp, True, ["gold"]) is None and hp["profiles"] == {}
      and A._profile(hp, True, []) == "no rate-profile <name>",
      "_profile: one taken and taken back out")
check("cisco-profile=gold,1000000,500000" in P.shaper_section(
    P.empty(), A.stage(hp, "aaa radius rate-profile gold down 1G up 500M"
                       .split()) or hp),
      "pppoe.shaper_section: the profiles the aaa block holds")
check(A.profile_rate("1G") == 1000000 and A.profile_rate("0") == 0
      and A.profile_rate("x") is None and A.profile_rate("10001M") is None,
      "profile_rate: kbit/s, 0 allowed")
check(A.stage(h, "no aaa radius rate-profile free".split()) is None
      and "free" not in h["profiles"]
      and A.stage(h, "no aaa radius rate-profile free".split())
      .endswith("not a profile here"),
      "stage: `no rate-profile` takes one out, and says when none is")
pph = P.empty()
for l in ("interface ge0", "gateway 100.64.0.1"):
    P.stage(pph, f"pppoe {l}".split())
A.stage(h, "aaa radius server 192.0.2.1 secret s".split())
check("cisco-profile=PLANO_100M,100000,50000" in P.accel_conf(
    pph, [("ge0", "ge0", 0)], h),
      "pppoe.accel_conf: the profiles in [shaper]")

print("\n=== Prisma's RADIUS dictionary, vendor 67033 ===")
DICT = os.path.join(HERE, "..", "appliance", "dictionary.prisma")
dtext = open(DICT).read()
words = [ln.split() for ln in dtext.splitlines()
         if ln.strip() and not ln.startswith("#")]
check(["VENDOR", "Prisma", "67033"] in words
      and ["ATTRIBUTE", P.RATE_DOWN, "1", "integer"] in words
      and ["ATTRIBUTE", P.RATE_UP, "2", "integer"] in words
      and P.RATE_VENDOR == "Prisma",
      "dictionary.prisma: vendor 67033, the two rates integers, named as the "
      "shaper is told to read them")
check(prismash.RADIUS_DICTIONARY.endswith("/dictionary.prisma")
      and P.PRISMA_DICTIONARY == "/usr/share/prisma-bng/dictionary.prisma",
      "where the box and the image put it")
_saved = prismash.RADIUS_DICTIONARY
prismash.RADIUS_DICTIONARY = DICT
_, out = said(lambda: shell().show_radius_dictionary())
prismash.RADIUS_DICTIONARY = "/proc/no/such"
_, out2 = said(lambda: shell().show_radius_dictionary())
prismash.RADIUS_DICTIONARY = _saved
check(out == dtext and "not on this box" in out2,
      "show aaa radius dictionary: the file whole; missing, said")

print("\n=== the commit: restart, reload, or neither ===")


def ctl(rcs):
    """A stand-in prisma-bng: rc by verb, its argv recorded."""
    p = os.environ["PRISMA_BNG_CTL"]
    body = "".join(f'[ "$1" = {v} ] && {{ echo "{s}"; exit {rc}; }}\n'
                   for v, (rc, s) in rcs.items())
    with open(p, "w") as f:
        f.write(f"#!/bin/sh\necho \"$@\" >> '{p}.argv'\n{body}exit 9\n")
    os.chmod(p, 0o755)
    if os.path.exists(p + ".argv"):
        os.unlink(p + ".argv")
    return p + ".argv"


def argv(log):
    return open(log).read().split() if os.path.exists(log) else []


IFS = (0, "  Name  Idx\nhost-acc0  1  up\n")
BASE = ("aaa\n radius\n  server 192.0.2.1 secret s\n  exit\n exit\n"
        "pppoe\n interface host-acc0\n gateway 100.64.0.1\n exit\n")
run = Config.parse(BASE)
OK = {"apply": (0, "restarted"), "reload": (0, "reloaded"),
      "stop": (0, "stopped")}


def commit(tgt, rcs=OK, running=run):
    log = ctl(rcs)
    sh = shell(Dp({"show interface": IFS}), running)
    rv, out = said(lambda: sh.apply_pppoe(tgt, running,
                                          running.delta_to(tgt)))
    return rv, out, argv(log)


tgt = Config.parse(BASE)
A.stage(tgt.aaa, "aaa radius server 192.0.2.2 secret t".split())
rv, out, asked = commit(tgt)
check(rv and asked == ["reload"] and "no session dropped" in out,
      f"a second server is a reload: nobody redials: {asked} {out!r}")
check("server=192.0.2.2" in open(os.path.join(os.environ["PRISMA_BNG_DIR"],
                                              "accel-ppp.conf")).read()
      and A.read_conf()["servers"][1]["address"] == "192.0.2.2",
      "  the files written first, aaa.conf with them")
tgt2 = Config.parse(BASE)
A.stage(tgt2.aaa, "aaa radius rate-profile gold down 100M up 50M".split())
rv, out, asked = commit(tgt2)
check(asked == ["reload"], "a rate profile is a reload too")
rv, out, asked = commit(tgt, dict(OK, reload=(3, "not running")))
check(rv and asked == ["reload", "apply"],
      "a reload of a container that is not running starts it")
rv, out, asked = commit(tgt, dict(OK, reload=(1, "accel-pppd refused")))
check(rv is False and "did not reload" in out and "refused" in out,
      "a reload refused refuses the commit, and says why")
tgt = Config.parse(BASE)
P.stage(tgt.pppoe, "pppoe mtu 1480".split())
A.stage(tgt.aaa, "aaa radius timeout 5".split())
rv, out, asked = commit(tgt)
check(asked == ["apply"] and "every session redials" in out,
      "a PPPoE change with it is a restart, as before")
tgt = Config.parse(BASE)
A.stage(tgt.aaa, "aaa radius dynamic-authorization listen 10.0.0.1".split())
A.stage(tgt.aaa, "aaa radius dynamic-authorization client 10.0.0.9 secret "
                 "d".split())
rv, out, asked = commit(tgt)
check(asked == ["apply"],
      "the Disconnect/CoA listener is opened at start only: a restart")
local = Config.parse("pppoe\n interface host-acc0\n gateway 100.64.0.1\n"
                     " address-pool 100.64.0.0/24\n local-user a password b\n"
                     " exit\n")
tgt = Config.parse(local.text())
A.stage(tgt.aaa, "aaa radius server 192.0.2.1 secret s".split())
rv, out, asked = commit(tgt, running=local)
check(asked == ["apply"],
      "the first server loads the radius module: a restart, not a reload")
none = Config.parse("")
tgt = Config.parse("")
A.stage(tgt.aaa, "aaa radius server 192.0.2.1 secret s".split())
rv, out, asked = commit(tgt, running=none)
check(rv and asked == [] and "aaa: written" in out
      and A.read_conf() == tgt.aaa,
      "RADIUS with no PPPoE: aaa.conf written, the host asked nothing")
tgt = Config.parse(BASE)
A.stage(tgt.aaa, "aaa radius server 192.0.2.1 secret s role acct".split())
rv, out, asked = commit(tgt)
check(rv is False and "no server takes authentication" in out
      and asked == [],
      "no authentication server is refused before anything is written")
_conf = A.CONF_PATH
A.CONF_PATH = "/proc/no/such/aaa.conf"
log = ctl(OK)
rv, out = said(lambda: shell(Dp({"show interface": IFS})).apply_pppoe(
    Config.parse(BASE), run, run.delta_to(Config.parse(BASE))))
A.CONF_PATH = _conf
check(rv is False and "error: aaa: could not write" in out and argv(log) == [],
      "aaa.conf that cannot be written refuses the commit, nothing asked")
rv, out = said(lambda: shell(Dp({"show interface": IFS})).apply_pppoe(
    Config.parse(BASE), refresh=True))
check(rv, "apply_pppoe(refresh): the files of an older release written again")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
