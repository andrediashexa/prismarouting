#!/usr/bin/env python3
"""
PPPoE subscriber termination (#597): src/prismash/pppoe.py, the `pppoe`
block in the configuration model, the VTY's level, apply, show and clear, and
the doors the commit and the screens go through.

Each behaviour, shown red by a mutation (named beside the box):

  * one rule for the prompt and the saved file: stage() takes and refuses
    the same statements both read (mutation: a local user's password not
    checked for a quote, which would split chap-secrets' line);
  * RADIUS is not here since #607: `radius` is refused with where it went
    (mutation: stage() taking it), and test/test_aaa.py has the rest;
  * the block written, read back and nested exactly as typed (mutation:
    body() dropping the rate-limit default);
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
import pppoe as P  # noqa: E402
import aaa as A_mod  # noqa: E402
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
                    "authentication pap chap", "session-limit 4000",
                    "mtu 1480", "tcp-mss 1440", "ac-name edge",
                    "rate-limit default 20M/10M",
                    "local-user lab1 password pw1")
check(whys == [None] * len(whys), f"stage: every statement taken: {whys}")
check(held["interfaces"] == ["host-acc0", "host-acc0.100"]
      and held["gateway"] == "100.64.0.1"
      and held["settings"]["authentication"] == "pap chap"
      and held["settings"]["rate-limit-default"] == "20M/10M"
      and held["users"] == {"lab1": "pw1"},
      "stage: held as typed")
for line, want in (
        ("radius server 192.0.2.1 secret x", "under 'aaa' > 'radius'"),
        ("radius nas-identifier x", "under 'aaa' > 'radius'"),
        ("rate-limit default 20M", "<download>/<upload>"),
        ("rate-limit default 20X/1M", "<download>/<upload>"),
        ("rate-limit default 11G/1M", "at most 10G"),
        ("rate-limit default 0/1M", "at most 10G"),
        ("rate-limit attribute mikrotik-rate-limit", "Prisma-Rate-Limit-Down"),
        ("rate-limit burst 1", "Prisma-Rate-Limit-Down"),
        ("rate-limit default", "rate-limit default <download>/<upload>"),
        ("local-user lab password a\"b", "no space, comma"),
        ("address-pool 100.64.0.0/31", "/30 or shorter"),
        ("address-pool 100.64.0.9-100.64.0.1", "ends before it starts"),
        ("address-pool nonsense", "neither a prefix nor a range"),
        ("gateway 2001:db8::1", "gateway <address>"),
        ("mtu 1501", "576-1500"), ("tcp-mss 100", "536-1460"),
        ("session-limit 0", "1-1000000"),
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
h, _ = staged("rate-limit default 1G/500M")
check(P.stage(h, "no pppoe rate-limit default".split()) is None
      and h == P.empty()
      and P.stage(h, "no pppoe rate-limit default x".split())
      == "no rate-limit default",
      "stage: `no rate-limit` takes each back out")
check(P.rate_kbit("20M") == 20000 and P.rate_kbit("1g") == 1000000
      and P.rate_kbit("1500K") == 1500 and P.rate_kbit("512") == 512
      and P.rate_kbit("10G") == 10000000 and P.rate_kbit("10001M") is None
      and P.rate_kbit("") is None and P.rate_kbit("0") is None,
      "rate_kbit: kbit/s, K, M, G - G a million, not accel-ppp's ten")
check(P.rate_words(20000) == "20M" and P.rate_words(1000000) == "1G"
      and P.rate_words(1500) == "1500K",
      "rate_words: the shortest spelling")
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
check(P.pool_error("100.64.0.0/22") is None
      and P.pool_error("100.64.0.1-100.64.3.254") is None,
      "pool_error: a prefix and a range")
check(P.setting_error("bogus", "1").endswith("not a setting of 'pppoe'"),
      "setting_error: an unknown key")

print("\n=== the block: written, nested, read back ===")
check(P.body(held)[:2] == ["interface host-acc0", "interface host-acc0.100"]
      and "rate-limit default 20M/10M" in P.body(held)
      and "local-user lab1 password pw1" in P.body(held),
      "body: the statements, the rate limits kept")
nested = P.nest(held)
check(nested[0] == "pppoe" and nested[-1] == " exit"
      and all(x.startswith(" ") for x in nested[1:]) and P.nest(P.empty()) == [],
      "nest: the block, and nothing when nothing is set")
check(P.setting(held, "mtu") == "1480" and P.setting(P.empty(), "mtu") ==
      "1492", "setting: the value, or the default")
cfg = Config.parse("\n".join(nested) + "\n")
check(cfg.pppoe == held and not cfg.unknown,
      f"Config.parse: the block read back whole: {cfg.unknown}")
check("\n".join(nested) in cfg.text(),
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

check(Config.parse("pppoe\n session-limit 4000\n exit\n").runtime_interfaces()
      == 4000 and Config.parse("pppoe\n mtu 1480\n exit\n")
      .runtime_interfaces() == P.SESSION_LIMIT_DEFAULT == 16000
      and Config().runtime_interfaces() == 0,
      "runtime_interfaces: the session limit the BNG enforces, which the "
      "tuner sizes the statistics segment for; a block with no limit holds "
      "the default (docs/bng.md 0.4 item 5), no block nothing")

print("\n=== the session ceiling: the statistics segment holds it ===")
check(P.session_limit(P.empty()) == 0
      and P.session_limit(staged("mtu 1480")[0]) == 16000
      and P.session_limit(staged("session-limit 40000")[0]) == 40000,
      "session_limit: none with no PPPoE, the default with no statement, "
      "else the statement")
check(P.stage(P.empty(), "pppoe session-limit 1000000".split()) is None
      and P.stage(P.empty(), "pppoe session-limit 1000001".split()),
      "stage: a saved file's limit up to 0.154.0's million is still READ - "
      "a refusal at boot is a box with no subscribers; a commit refuses it")
SEG = ("Entry: /sys/vector_rate (type 9)\n"
       "base 0x7f5b4a363000, size 930.00m, locked, name 'stat segment'\n"
       "  total: 930.00M, used: 491.09M, free: 438.91M\n")
check(P.segment_mb(SEG) == 930
      and P.segment_mb(SEG.replace("930.00m", "1.50g")) == 1536
      and P.segment_mb(SEG.replace("930.00m", "32.00m")) == 32
      and P.segment_mb("") is None
      and P.segment_mb("base 0x1, size 512m, name 'main heap'") is None,
      "segment_mb: the stat segment's size as VPP mapped it, not the heap's")
THREADS = ("ID     Name                Type        LWP\n"
           "0      vpp_main                        1950\n"
           "1      vpp_wk_0            workers     1953\n"
           "2      vpp_wk_1            workers     1954\n"
           "3      vpp_wk_2            workers     1955\n")
check(P.thread_count(THREADS) == 4 and P.thread_count("") == 0,
      "thread_count: the main thread and every worker")
import tuneadvice  # noqa: E402
T = tuneadvice.load_tuner()
check(T is not None, "the tuner loads from the source tree")
# The tuner's own size for N sessions holds N - the property the clamp
# rests on: a box booted on a segment sized for its limit is held at it.
for n, th in ((16000, 1), (40000, 4), (100000, 4), (7, 8)):
    mb = T.statseg_mb_for(n + 5, th) or T.STATSEG_DEFAULT_MB
    check(P.segment_room(T, mb, th, 5) >= n,
          f"segment_room: the tuner's size for {n} sessions and 5 "
          f"interfaces, {mb} MB on {th} thread(s), holds {n}")
check(P.segment_room(T, 32, 4, 10) == T.statseg_interfaces_for(32, 4) - 10
      < 5000 and P.segment_room(T, 32, 1, 0) < 7500,
      "segment_room: VPP's 32 MB default holds fewer than lab/pppoe-scale.sh "
      "saw it abort at - 5,000 on four threads, 7,500 on one")
check(P.segment_room(None, 32, 1, 0) is None
      and P.segment_room(T, None, 1, 0) is None
      and P.segment_room(T, 32, 0, 0) is None
      and P.segment_room(T, 1, 4, 10000) == 0,
      "segment_room: nothing said without a tuner, a size or the threads; "
      "never below none")
check(P.box_segment_mb(T, 8) == 512 and P.box_segment_mb(T, 64) == 4096
      and P.box_segment_mb(T, 64, 4) == 1024
      and P.box_segment_mb(None, 8) is None and P.box_segment_mb(T, 0) is None,
      "box_segment_mb: a quarter of what the control plane keeps - the "
      "configured figure, else a quarter of the RAM and at least 2 GB")
check(P.limit_error(100000, None, 4, None) is None
      and "more than was measured working, 100000"
      in P.limit_error(100001, None, 4, None)
      and P.limit_error(40000, 55000, 4, 512) is None
      and "holds 50000 on 9 thread(s)" in P.limit_error(60000, 50000, 9, 512),
      "limit_error: past what was measured, or past what this box's RAM "
      "lets the segment hold, refused")

print("\n=== secrets: never on a screen ===")
shown = P.redact(nested + ["pppoe radius server 192.0.2.1 secret s3cr3t! "
                           "auth-port 11812",
                           "pppoe local-user lab1 password pw1"])
check(not any("s3cr3t!" in x or "pw1" in x for x in shown)
      and "pppoe radius server 192.0.2.1 secret <set, not shown> auth-port "
      "11812" in shown,
      "redact: nested and flat, the password blanked - and a 0.154.0 "
      "server's secret, which a rollback revision still carries")
diff = prismash.compare_redact(["- radius server 192.0.2.1 secret old",
                                "+ radius server 192.0.2.1 secret new"])
check(diff == ["- radius server 192.0.2.1 secret <set, not shown>",
               "+ radius server 192.0.2.1 secret <set, not shown>"],
      "compare_redact: a changed secret is two lines, neither carrying it")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    prismash.Shell.print_config(cfg)
check("pw1" not in buf.getvalue()
      and "password <set, not shown>" in buf.getvalue(),
      "print_config: show running-config blanks them")

print("\n=== what the container cannot run, refused at commit ===")
check(P.check(P.empty()) == [], "check: nothing configured is nothing wrong")
h, _ = staged("interface a")
w = P.check(h)
check(len(w) == 3 and any("gateway" in x for x in w)
      and any("authenticate" in x and "'aaa radius'" in x for x in w)
      and any("address-pool" in x for x in w),
      f"check: no gateway, nobody to authenticate, no address: {w}")
h, _ = staged("interface a", "gateway 100.64.0.1")
A = A_mod.empty()
A_mod.stage(A, "aaa radius server 192.0.2.1 secret s".split())
check(P.check(h, A) == [], "check: RADIUS alone may give every address")

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
              "address-pool 100.64.0.0/24", "dns 8.8.8.8", "tcp-mss 1452",
              "session-limit 900", "authentication pap mschap-v2")
acc, _ = P.access(h, vl)
AA = A_mod.empty()
A_mod.stage(AA, "aaa radius server 192.0.2.1 secret s3cr3t!".split())
ac = P.accel_conf(h, acc, AA)
for needle in ("interface=pppoe-cp,vpp-cp=enable",
               "interface=pppoe-cp.100,vpp-cp=enable",
               "server=192.0.2.1,s3cr3t!,auth-port=1812,acct-port=1813",
               "gw-ip-address=100.64.0.1", "100.64.0.0/24", "dns1=8.8.8.8",
               "max-sessions=900", "auth_pap", "auth_mschap_v2", "vyos_vpp",
               "ip-up=/usr/lib/prisma-bng/session", "acct-interim-interval=300",
               "mtu=1492", "\nshaper\n", "[shaper]", "vendor=Prisma",
               "attr-down=Prisma-Rate-Limit-Down",
               "attr-up=Prisma-Rate-Limit-Up", "vpp-policer-type=1r2c",
               "dictionary=/usr/share/prisma-bng/dictionary.prisma",
               "down-burst-factor=0.5", "up-burst-factor=0.5", P.FORMAT_LINE):
    check(needle in ac, f"accel_conf carries {needle!r}")
# The session trail (docs/bng.md 0.7): its module, its file, and each
# access interface by the name the VTY gives it (mutation: the Linux name).
for needle in ("\nprisma_trail\n", "[prisma-trail]",
               "path=/var/log/prisma-routing/pppoe-sessions.log",
               "interface-pppoe-cp=ge0", "interface-pppoe-cp.100=ge0.100"):
    check(needle in ac, f"accel_conf carries the trail's {needle.strip()!r}")
check(P.trail_section([]) == ["[prisma-trail]", f"path={P.TRAIL_PATH}"],
      "trail_section: the path alone with no access interface")
check(ac.index("\nradius\n") < ac.index("\nshaper\n"),
      "accel_conf: the shaper loaded after radius, whose events it takes")
check("rate-limit=" not in ac, "accel_conf: no default rate unless one is set")
h3 = dict(h, settings=dict(h["settings"], **{"rate-limit-default": "20M/1G"}))
ac3 = P.accel_conf(h3, acc, AA)
check("rate-limit=20000/1000000" in ac3 and "Mikrotik" not in ac3
      and "attr=" not in ac3,
      "shaper_section: the default in kbit/s; no other vendor's attribute")
check("auth_chap_md5" not in ac and "[cli]" not in ac
      and "chap-secrets" not in ac,
      "accel_conf: no method not asked for, no CLI (the entrypoint adds it), "
      "no local users when RADIUS is set")
h2, _ = staged("interface ge0", "gateway 100.64.0.1",
               "address-pool 100.64.0.0/24", "local-user a password b")
ac2 = P.accel_conf(h2, P.access(h2, {})[0])
check("max-sessions=16000" in ac2.splitlines()
      and "max-sessions=1200" in P.accel_conf(h, acc, AA, 1200).splitlines(),
      "accel_conf: max-sessions ALWAYS - the default with no statement, the "
      "segment's when it holds fewer")
check(P.modules(h2, A_mod.empty())[-4:-2] == ["ippool", "shaper"]
      and "chap-secrets" in P.modules(h2, A_mod.empty())
      and "radius" in P.modules(h2, AA),
      "modules: RADIUS when 'aaa radius' has a server, local users else")
check("chap-secrets\n" in ac2.replace("chap-secrets=", "x=") or
      "[chap-secrets]" in ac2, "accel_conf: local users when no RADIUS")
check(P.chap_secrets(h2).splitlines()[1] == '"a" * "b" *',
      "chap_secrets: the users, in chap-secrets' format")
env = P.access_env(h, acc)
check("PARENT=ge0" in env and 'VLANS="0 100"' in env and "TCP_MSS=1452" in env
      and "MTU=1492" in env, f"access_env: the plumbing's words: {env!r}")
check('ACCESS="ge0 ge0.100"' in env,
      f"access_env: the access interfaces by the dataplane's names, for "
      f"its PPPoE guard (format 4): {env!r}")
check(P.enforced_limit(os.path.join(_root, "no-such")) is None,
      "enforced_limit: none with no file")
ok, msg = P.write_files(h, acc, aaa=AA)
bng = os.environ["PRISMA_BNG_DIR"]
check(P.file_format() == P.FORMAT and P.file_format("/proc/no") == "",
      "file_format: the release format of the files written")
check("server=192.0.2.1" in open(os.path.join(bng, "accel-ppp.conf")).read(),
      "write_files: the [radius] from the aaa block it is handed")
# #597 on 0.155.9: the directory came out 2750 under root's umask, and an
# administrator that is not root could not write its files. Made 0750 here
# by hand, as that box had it; write_files puts the group's write back.
os.chmod(bng, 0o750)
P.write_files(h, acc, aaa=AA)
check(os.stat(bng).st_mode & 0o070 == 0o070,
      f"write_files: the directory group-writable again: {oct(os.stat(bng).st_mode)}")
os.chmod(bng, 0o750)
check(P.share_dir(bng) and os.stat(bng).st_mode & 0o070 == 0o070
      and not P.share_dir(os.path.join(bng, "no-such")),
      "share_dir: the directory group-writable, False where there is none")
check(ok and all(stat.S_IMODE(os.stat(os.path.join(bng, f)).st_mode) == 0o660
                 for f in ("accel-ppp.conf", "chap-secrets", "access.env"))
      and stat.S_IMODE(os.stat(P.CONF_PATH).st_mode) == 0o660,
      "write_files: written 0660, the configuring group's and never the "
      "world's - they carry the secret")
check(P.read_conf() == h, "read_conf: pppoe.conf read back as the model")
check(P.enforced_limit() == 900, "enforced_limit: the max-sessions in force")
ok, _ = P.write_files(h, acc, aaa=AA, max_sessions=300)
check(ok and P.enforced_limit() == 300,
      "write_files: a lower ceiling handed on, read back as the one in force")
P.write_files(h, acc, aaa=AA)
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
        sid       |     ifname     | username |     ip      |    calling-sid    | state  | uptime-raw | rx-bytes-raw | tx-bytes-raw | rate-limit
------------------+----------------+----------+-------------+-------------------+--------+------------+--------------+--------------+------------
 8aa8eb1ab0657544 | pppoe_session2 | sub2     | 100.64.0.11 | 02:aa:00:00:00:02 | active | 3725       | 10           | 0            |
 8aa8eb1ab0657546 | pppoe_session1 | sub1     | 100.64.0.77 | 02:aa:00:00:00:01 | active | 90061      | 0            | 0            | 20000/10000
"""
r = P.session_rows(TABLE)
check(len(r) == 2 and r[0]["sid"] == "8aa8eb1ab0657544"
      and r[1]["ip"] == "100.64.0.77", "session_rows: accel-cmd's table")
scr = P.sessions_screen(r)
check(scr[1].startswith("8aa8eb1ab0657546  sub1") and scr[-1] ==
      "2 session(s)" and "1d+01:01:01" in scr[1]
      and P.sessions_screen([]) == ["no PPPoE session"],
      "sessions_screen: by user, uptime as a duration, and a count")
check(" 20M/10M " in scr[1] and " - " in scr[2] and "limit down/up" in scr[0],
      f"sessions_screen: each session's limit, download first: {scr}")
check(P.limit_words("20000/0") == "20M/none" and P.limit_words("") == "-"
      and P.limit_words("0/0") == "-" and P.limit_words("x/1") == "-",
      "limit_words: one direction unlimited says so")
POL = ('Name "vyos_7_20000_2000000_down" type 1r2c cir 20000 ...\n'
       'Policer at index 0: single rate, not color-aware\n'
       'conform 100 packets, 150000 bytes\nexceed 7 packets, 9000 bytes\n'
       'violate 1 packets, 1500 bytes\n-----------\n'
       'Name "vyos_7_10000_1000000_up" type 1r2c\n'
       'conform 50 packets, 5000 bytes\nexceed 0 packets, 0 bytes\n'
       'violate 0 packets, 0 bytes\n')
pols = P.policers(POL)
check(pols == {"vyos_7_20000_2000000_down": {"conform": 100, "dropped": 8},
               "vyos_7_10000_1000000_up": {"conform": 50, "dropped": 0}},
      f"policers: passed and dropped, by name: {pols}")
VS = ("[0] sw-if-index 7 client-ip 0.0.0.0 session-id 3 encap-if-index 1"
      " decap-fib-index 0\n    local-mac 02:fe:00:00:00:01  client-mac "
      "02:aa:00:00:00:01\n[1] sw-if-index 9 client-ip 0.0.0.0 session-id 4 "
      "encap-if-index 1 decap-fib-index 0\n    local-mac 02:fe:00:00:00:01  "
      "client-mac 02:aa:00:00:00:02\n")
check(P.session_index(VS, "02:aa:00:00:00:01") == "7"
      and P.session_index(VS, "02:AA:00:00:00:02") == "9"
      and P.session_index(VS, "02:aa:00:00:00:03") == ""
      and P.session_index(VS, "") == "",
      "session_index: the sw_if_index by the client's MAC - VPP holds "
      "0.0.0.0 for every session's address")
one = P.session_screen(r[1], "7", pols)
check(any(x.startswith("mac") and "02:aa:00:00:00:01" in x for x in one)
      and any("20M down, 10M up" in x for x in one)
      and any(x.startswith("policer down") and "8 dropped" in x for x in one)
      and any(x.startswith("policer up") and "50 packets" in x for x in one)
      and all(len(x) <= 79 for x in one),
      f"session_screen: the MAC, the limit and its policers: {one}")
check(not any("policer" in x for x in P.session_screen(r[1], "8", pols))
      and any("none" in x for x in P.session_screen(r[0], "", {})),
      "session_screen: another session's policers are not this one's")
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
s = P.summary_screen(h, "running", {"active": 3}, AA, {"192.0.2.1": "failed"})
check(s[0].endswith("running") and "3 active" in s[1]
      and any("RADIUS" in x for x in s)
      and any("192.0.2.1" in x and "failed" in x for x in s)
      and any("rate limit" in x and "Prisma-Rate-Limit" in x for x in s)
      and P.summary_screen(P.empty(), "x", {}) == ["PPPoE is not configured"]
      and "NOT running" in P.summary_screen(h, "stopped", {})[0],
      "summary_screen: the control plane, the sessions, the configuration")
_lim = lambda rows: [x for x in rows if x.startswith("session limit")]
check(_lim(s) == [f"{'session limit':<22}900"]
      and _lim(P.summary_screen(h, "running", {}, AA, enforced=900))
      == [f"{'session limit':<22}900"]
      and _lim(P.summary_screen(h2, "running", {}, enforced=3400))
      == [f"{'session limit':<22}16000, the default; 3400 until the next "
          f"reboot"]
      and max(len(x) for x in P.summary_screen(
          staged("session-limit 1000000")[0], "running", {},
          enforced=999999)) <= 79,
      "summary_screen: the limit always, the default said, and a ceiling "
      "the running segment holds lower than it, at most 79 columns")
# The session trail's block (docs/bng.md 0.7), and the row it gives the
# summary: what it could not write said in capitals (mutation: failed read
# as written).
TSTAT = STAT + "prisma-trail:\n  written: 41\n  failed: 2\n  seq: 41\n" \
    "radius(1, 192.0.2.1):\n  state: active\n"
check(P.trail_counts(TSTAT) == {"written": 41, "failed": 2, "seq": 41}
      and P.trail_counts(STAT) == {},
      "trail_counts: the trail's block, {} from a control plane without one")
st = P.summary_screen(h, "running", {"active": 3}, AA, {}, {"written": 41,
                                                              "failed": 2})
st0 = P.summary_screen(h, "running", {"active": 3}, AA, {}, {})
check(any(x.startswith("session trail") and "41 entries written, 2 NOT "
          "written" in x for x in st)
      and any(x.startswith("session trail") and "NOT recorded" in x
              for x in st0)
      and not any(x.startswith("session trail") for x in s),
      "summary_screen: the trail's row, and none when it was not read")
GUARD = ("pppoe-access: host-acc0 host-acc0.100\nhold 60 s, macs held 3\n"
         "discovery passed 156 not-access 5 held 6\n"
         "session passed 910132 not-access 5 held 0\n")
check(P.guard_counts(GUARD) == {"not-access": 10, "held": 6}
      and P.guard_counts("show bpa: unknown input `pppoe-access'") is None
      and P.guard_counts("pppoe-access: not set; VPP's pppoe plugin takes "
                         "PPPoE from every interface") is None,
      "guard_counts: the dataplane's refusals, discovery and session "
      "together; none from a plugin without the guard")
g = P.summary_screen(h, "running", {"active": 3}, AA, {},
                     guard={"not-access": 2 ** 64 - 1, "held": 2 ** 64 - 1})
check(any(x.startswith("refused") and "from outside the access "
          "interfaces" in x for x in g)
      and any("for a MAC another access port holds" in x for x in g)
      and all(len(x) <= 79 for x in g)
      and not any(x.startswith("refused") for x in s),
      "summary_screen: what the guard refused, at its widest a console's "
      "width; nothing without it")
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
                   ((CTL, "reload"), "reload the PPPoE"),
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
tgt = Config.parse("interface host-acc0.100\n vlan 100\n exit\n"
                   "aaa\n radius\n  server 192.0.2.1 secret s\n  exit\n"
                   " exit\npppoe\n"
                   " interface host-acc0\n interface host-acc0.100\n"
                   " gateway 100.64.0.1\n address-pool 100.64.0.0/24\n"
                   " exit\n")
check(not tgt.unknown, f"the target parses: {tgt.unknown}")
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
# THE CEILING, against a running segment (docs/bng.md 0.4 item 5). A box of
# 8 GB and four threads: its control plane keeps 2 GB, the tuner gives the
# segment at most 512 MB.
_mi = os.path.join(_root, "meminfo")
with open(_mi, "w") as f:
    f.write("MemTotal:        8388608 kB\n")


def roomy(seg_mb, booting=False):
    sh = shell(Dp({"show interface": IFS, "show threads": (0, THREADS),
                   "show statistics segment": (
                       0, SEG.replace("930.00m", f"{seg_mb}.00m"))}),
               booting=booting)
    sh.MEMINFO = _mi
    sh.TUNE_ENV = os.path.join(_root, "no-tune.env")
    return sh


r = roomy(32).pppoe_room(tgt)
check(r["threads"] == 4 and r["live_mb"] == 32 and r["box_mb"] == 512
      and r["live_room"] == T.statseg_interfaces_for(32, 4)
      - tgt.dataplane_interfaces() + tgt.runtime_interfaces()
      and r["box_room"] > 50000,
      f"pppoe_room: the running segment's and the box's, by the tuner: {r}")
log = ctl(0, "started")
rv, out = said(lambda: roomy(32).apply_pppoe(tgt))
check(rv is True and P.enforced_limit() == r["live_room"] < 16000
      and "at most" in out and "until the next reboot" in out
      and "sizes it for 16000" in out,
      f"apply_pppoe: on VPP's 32 MB a box with no limit is held at what the "
      f"segment holds, and told: {out!r}")
_sized = T.statseg_mb_for(tgt.dataplane_interfaces(), 4)
rv, out = said(lambda: roomy(_sized).apply_pppoe(tgt))
check(rv is True and P.enforced_limit() == 16000 and "at most" not in out,
      f"apply_pppoe: on the segment the tuner sized for it ({_sized} MB), "
      f"the limit itself and nothing said")
big = Config.parse(tgt.text().replace(" gateway 100.64.0.1\n",
                                      " gateway 100.64.0.1\n"
                                      " session-limit 90000\n"))
check(P.session_limit(big.pppoe) == 90000, "the 90,000 target parses")
log = ctl(0, "started")
rv, out = said(lambda: roomy(512).apply_pppoe(big))
check(rv is False and "not applied" in out and "holds" in out
      and not os.path.exists(log),
      f"apply_pppoe: more sessions than this box's RAM lets the segment "
      f"hold is refused at a commit, the host asked nothing: {out!r}")
rv, out = said(lambda: roomy(512, booting=True).apply_pppoe(big))
check(rv is True and P.enforced_limit() == P.segment_room(T, 512, 4, 2)
      and P.enforced_limit() < 90000,
      "apply_pppoe: at boot the same file is loaded, held at what the "
      "segment holds")
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

# By address (docs/bng.md 0.4 item 2): the list read, the session ended by
# its id - prisma-bng terminates by id, user or all.
log = ctl(0, TABLE)
rv, out = said(lambda: shell().clear_pppoe(["session", "address",
                                            "100.64.0.77"]))
check("cleared: sid 8aa8eb1ab0657546" in out and open(log).read().split()
      == ["sessions", "terminate", "sid", "8aa8eb1ab0657546"],
      f"clear_pppoe: by address, the session's id terminated: {out!r}")
log = ctl(0, TABLE)
rv, out = said(lambda: shell().clear_pppoe(["session", "address",
                                            "100.64.0.99"]))
check("no session holds 100.64.0.99" in out
      and open(log).read().split() == ["sessions"],
      "clear_pppoe: an address no session holds, nothing terminated")
log = ctl(3, "not running")
rv, out = said(lambda: shell().clear_pppoe(["session", "address",
                                            "100.64.0.77"]))
check("PPPoE is not running" in out, "clear_pppoe: by address, not running")
log = ctl(1, "no answer")
rv, out = said(lambda: shell().clear_pppoe(["session", "address",
                                            "100.64.0.77"]))
check("did not answer" in out, "clear_pppoe: by address, no answer")
check(P.clear_words(["address", "100.64.0.7"]) == (["address", "100.64.0.7"],
                                                    None),
      "clear_words: address <A.B.C.D>")
check(P.clear_words(["address", "fe80::1"])[0] is None,
      "clear_words: an inside address is IPv4")

log = ctl(0, TABLE)
f = shell().pppoe_sessions_facts()
check(f["running"] and f["error"] is None and len(f["rows"]) == 2
      and open(log).read().split() == ["sessions"],
      "pppoe_sessions_facts: the control plane asked once")
check("2 session(s)" in said(lambda: shell().show_pppoe_sessions(f))[1],
      "show_pppoe_sessions: drawn from that reading")
log = ctl(1, "the door did not answer")
f = shell().pppoe_sessions_facts()
check(f["running"] and f["error"] == "the door did not answer"
      and "did not answer" in said(lambda: shell().show_pppoe_sessions(f))[1],
      "pppoe_sessions_facts: an answer that did not come")
log = ctl(0, TABLE)
dp = Dp({"show pppoe session": (0, "[0] sw-if-index 7 client-ip 0.0.0.0\n"
                                   "  local-mac x client-mac "
                                   "02:aa:00:00:00:01\n"),
         "show policer": (0, 'Name "vyos_7_20000_1250000_down"\n'
                             'conform 10 packets, 1 bytes\n'
                             'exceed 2 packets, 1 bytes\n')})
f = shell(dp).pppoe_session_facts("8AA8EB1AB0657546")
check(f["row"]["username"] == "sub1" and f["sw_if_index"] == "7"
      and f["policers"]["vyos_7_20000_1250000_down"] == {"conform": 10,
                                                         "dropped": 2},
      "pppoe_session_facts: the row, its index by MAC, the policers")
out = said(lambda: shell(dp).show_pppoe_session("8aa8eb1ab0657546", f))[1]
check("02:aa:00:00:00:01" in out and "10 packets passed, 2 dropped" in out,
      f"show_pppoe_session: drawn from that reading: {out!r}")
f = shell(dp).pppoe_session_facts("0000000000000000")
check(f["row"] is None and "no session 0000000000000000" in said(
          lambda: shell().show_pppoe_session("0000000000000000", f))[1],
      "pppoe_session_facts: an id no session has")
log = ctl(0, "running")
sh = shell(Dp({"show bpa pppoe-access": (
    0, "discovery passed 5 not-access 3 held 1\n")}))
sh.running_config = lambda: tgt
f = sh.pppoe_summary_facts()
check(f["status"] == "running" and f["guard"] == {"not-access": 3, "held": 1}
      and f["held"]["gateway"] == "100.64.0.1",
      "pppoe_summary_facts: the status, the guard, the blocks it runs on")
out = said(lambda: sh.show_pppoe_summary(f))[1]
check("running" in out and "3 from outside" in out,
      f"show_pppoe_summary: drawn from that reading: {out!r}")
f = shell().pppoe_summary_facts()
check(f["status"] == "" and "not configured" in said(
          lambda: shell().show_pppoe_summary(f))[1],
      "pppoe_summary_facts: no PPPoE, nothing asked")

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

print("\n=== IPv6 on the sessions (#609) ===")
h6, whys = staged("interface host-acc0", "gateway 100.64.0.1",
                  "address-pool 100.64.0.0/24", "local-user a password b",
                  "ipv6 prefix-pool 2001:db8:100::/48",
                  "ipv6 delegated-pool 2001:db8:200::/40 length 56",
                  "ipv6 dns 2001:4860:4860::8888")
check(whys == [None] * len(whys) and P.ipv6_on(h6)
      and P.v6(h6) == {"enable": False, "pools": ["2001:db8:100::/48"],
                       "delegated": [["2001:db8:200::/40", "56"]],
                       "dns": ["2001:4860:4860::8888"]},
      f"stage_ipv6: the pools and a resolver taken: {whys} {P.v6(h6)}")
h = {"interfaces": []}
check(P.stage_ipv6(h, ["dns", "2001:db8::53"], False) is None
      and h["ipv6"]["dns"] == ["2001:db8::53"]
      and P.stage_ipv6(h, [], True) is None and h["ipv6"] == P.empty_ipv6(),
      "stage_ipv6: on a model built before #609, its ipv6 made; `no ipv6` "
      "empties it")
check(not P.ipv6_on(P.empty()) and P.v6({"interfaces": []}) ==
      P.empty_ipv6() and P.empty()["ipv6"] == P.empty_ipv6(),
      "ipv6_on / v6 / empty_ipv6: nothing IPv6 is IPv6 off, and a model "
      "built before #609 reads as none")
for line, want in (("ipv6 prefix-pool 2001:db8:100::1/48", "host bits"),
                   ("ipv6 prefix-pool 2001:db8::/80", "/16 to /64"),
                   ("ipv6 prefix-pool 100.64.0.0/24", "not an IPv6 prefix"),
                   ("ipv6 delegated-pool 2001:db8:200::/56 length 48",
                    "cannot be cut"),
                   ("ipv6 delegated-pool 2001:db8:200::/40", "length <n>"),
                   ("ipv6 delegated-pool 2001:db8:200::/40 length 72",
                    "length <16-64>"),
                   ("ipv6 dns 8.8.8.8", "IPv6 resolver"),
                   ("ipv6 bogus", "ipv6 enable | prefix-pool"),
                   ("ipv6", "ipv6 enable | prefix-pool")):
    h = P.empty()
    why = P.stage(h, ["pppoe"] + line.split())
    check(why and want in why and P.is_empty(h),
          f"stage_ipv6: '{line}' refused, nothing staged: {why}")
check(P.ipv6_pool_error("prefix-pool", "2001:db8:100::/48") is None
      and P.ipv6_pool_error("delegated-pool", "2001:db8::/40", "56") is None
      and "cannot be cut" in P.ipv6_pool_error("delegated-pool",
                                                "2001:db8::/60", "56"),
      "ipv6_pool_error: a pool and a delegated length")
check(str(P.prefix6("2001:db8::/32")) == "2001:db8::/32"
      and P.prefix6("2001:db8::1/32") is None and P.prefix6("2001:db8::")
      is None and P._addr6("2001:0db8::0001") == "2001:db8::1"
      and P._addr6("1.2.3.4") is None,
      "prefix6 / _addr6: a prefix with its host bits clear; an address")
h = P.empty()
P.stage(h, "pppoe ipv6 delegated-pool 2001:db8:200::/40 length 56".split())
P.stage(h, "pppoe ipv6 delegated-pool 2001:db8:200::/40 length 60".split())
check(P.v6(h)["delegated"] == [["2001:db8:200::/40", "60"]],
      "stage_ipv6: a delegated pool given again takes the new length")
check(P.stage(h, "no pppoe ipv6 delegated-pool 2001:db8:200::/40".split())
      is None and P.is_empty(h),
      "stage_ipv6: no delegated-pool by its prefix alone")
P.stage(h, "pppoe ipv6 enable".split())
check(P.ipv6_on(h) and P.body(h) == ["ipv6 enable"]
      and P.stage(h, ["no", "pppoe", "ipv6"]) is None and P.is_empty(h),
      "stage_ipv6: enable alone is IPv6 on; `no ipv6` takes all of it")
check(P.body(h6)[-3:] == ["ipv6 prefix-pool 2001:db8:100::/48",
                          "ipv6 delegated-pool 2001:db8:200::/40 length 56",
                          "ipv6 dns 2001:4860:4860::8888"],
      f"body: the IPv6 statements, as typed: {P.body(h6)}")
cfg6 = Config.parse("\n".join(P.nest(h6)) + "\n")
check(cfg6.pppoe["ipv6"] == h6["ipv6"] and not cfg6.unknown,
      "the saved file reads the IPv6 statements back")

check(P.check(h6) == [], f"check: a pool each way is servable: {P.check(h6)}")
h = staged("interface host-acc0", "gateway 100.64.0.1",
           "address-pool 100.64.0.0/24", "local-user a password b",
           "ipv6 delegated-pool 2001:db8:200::/40 length 56")[0]
check(any("no 'ipv6 prefix-pool'" in w for w in P.check(h)),
      "ipv6_check: a delegated pool and no WAN prefix from anywhere: DHCPv6 "
      "would never answer")
check(P.ipv6_check(h, AA) == [],
      "ipv6_check: with RADIUS, the WAN prefix may come from it")
P.stage(h, "pppoe ipv6 prefix-pool 2001:db8:200::/48".split())
check(any("overlap" in w for w in P.ipv6_check(h, A_mod.empty())),
      "ipv6_check: two pools that overlap are refused")
check(P.ipv6_check(P.empty(), A_mod.empty()) == [],
      "ipv6_check: no IPv6, nothing to say")

acc6, _ = P.access(h6, {})
conf6 = P.accel_conf(h6, acc6)
check("ipv6=allow" in conf6 and "ipv6-intf-id=0:0:0:1" in conf6
      and "\nipv6pool\nipv6_nd\nipv6_dhcp\n" in conf6
      and "2001:db8:100::/48,64" in conf6
      and "delegate=2001:db8:200::/40,56" in conf6
      and "AdvManagedFlag=0" in conf6 and "AdvAutonomousFlag=1" in conf6
      and "dns=2001:4860:4860::8888" in conf6,
      "accel_conf: IPv6CP allowed, the pools, SLAAC on and M off, DHCPv6")
conf4 = P.accel_conf(held, P.access(held, {"host-acc0.100":
                                           {"outer": "100"}})[0])
check("ipv6=deny" in conf4 and "ipv6_nd" not in conf4
      and "[ipv6-" not in conf4,
      "accel_conf: no IPv6 statement, ipv6=deny and nothing IPv6 - what "
      "every release before wrote")
sec = P.ipv6_sections(staged("ipv6 enable")[0])
check(sec[0] == "[ipv6-nd]" and "[ipv6-pool]" not in sec
      and "[ipv6-dns]" not in sec,
      "ipv6_sections: RADIUS only, no pool section")
check("ipv6pool" not in P.modules(staged("ipv6 enable")[0], AA)
      and P.modules(h6, A_mod.empty()).index("ipv6pool") >
      P.modules(h6, A_mod.empty()).index("chap-secrets"),
      "modules: ipv6pool only with a pool, after the authentication source")

T6 = TABLE.replace("rate-limit\n", "rate-limit | ip6 | ip6-dp\n").replace(
    "|            \n", "|            |      |      \n")
T6 = T6.replace(
    "| 20000/10000\n",
    "| 20000/10000 | 2001:db8:100::dc6a:c745:4ec:fd66/64 | "
    "2001:db8:200::/56\n").replace(
    "| 0            |\n", "| 0            | | |\n")
r6 = P.session_rows(T6)
check(len(r6) == 2 and r6[1]["ip6"].startswith("2001:db8:100::dc6a")
      and r6[1]["ip6-dp"] == "2001:db8:200::/56" and r6[0]["ip6"] == "",
      f"session_rows: the twelve columns since #609: {r6}")
check(P.session_rows(TABLE)[1]["ip6"] == "" and len(P.session_rows(TABLE))
      == 2, "session_rows: an older host's ten columns still read")
check(P.wan_prefix("2001:db8:100::dc6a:c745:4ec:fd66/64") ==
      "2001:db8:100::/64" and P.wan_prefix("") == ""
      and P.wan_prefix("junk") == "",
      "wan_prefix: the CPE's address as the prefix it was given")
check(P.delegated_prefix("2001:db8:200::/56") == "2001:db8:200::/56"
      and P.delegated_prefix("x") == "",
      "delegated_prefix: the delegated prefix, or nothing")
scr6 = P.sessions_screen(r6)
check(scr6[2].strip() == "ipv6 2001:db8:100::/64"
      and scr6[3].strip() == "delegated 2001:db8:200::/56"
      and scr6[1].startswith("8aa8eb1ab0657546")
      and scr6[-1] == "2 session(s)" and all(len(x) <= 79 for x in scr6),
      f"sessions_screen: a session's prefixes on lines of their own: {scr6}")
one6 = P.session_screen(r6[1], "", {})
check(any(x.startswith("ipv6 prefix") and "2001:db8:100::/64" in x
          for x in one6)
      and any(x.startswith("delegated") and "/56" in x for x in one6)
      and not any("ipv6" in x for x in P.session_screen(r6[0], "", {})),
      f"session_screen: the prefixes, none on an IPv4-only one: {one6}")
rows = P.ipv6_summary_rows(h6)
check(rows == ["ipv6                  2001:db8:100::/48, a /64 each by RA",
               "ipv6 delegated        2001:db8:200::/40, a /56 each by "
               "DHCPv6"]
      and P.ipv6_summary_rows(P.empty()) == ["ipv6                  off"]
      and "RADIUS only" in P.ipv6_summary_rows(staged("ipv6 enable")[0])[0],
      f"ipv6_summary_rows: where each prefix comes from: {rows}")
s6 = P.summary_screen(h6, "stopped", {})
check(any(x.startswith("ipv6 ") for x in s6) and all(len(x) <= 79
                                                      for x in s6),
      "summary_screen: the IPv6 rows")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
