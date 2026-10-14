#!/usr/bin/env python3
"""
src/prismash/eventwatch.py and the host's watcher in prismash.py (#588
phase 2): what the box sees happen, compared reading with reading.

Each behaviour, shown red by a mutation (docs/logging-events.md lists them):

  * a link: physical or sub-interface, administrative or the wire, and a
    parent's link counting the sub-interfaces it took (mutation:
    link_changes() not telling administrative from link);
  * a module in, out, past its RX power threshold and back (mutation:
    module_changes() comparing nothing but presence);
  * BGP leaving Established with FRR's reason, and a flap between two
    readings seen by FRR's counter (mutation: bgp_changes() without the
    counter);
  * BFD down with bfdd's diagnostic (mutation: the diagnostic left out);
  * a pool out of blocks once, not every reading, and with blocks again
    (mutation: pool_changes() not carrying `exhausted`);
  * the log export's collector lost and found;
  * logins, logouts and failed logins from wtmp and btmp records, a first
    reading saying nothing of history (mutation: read_utmp() starting at 0);
  * FLAPPING held: a subject changing more than FLAP_EVENTS times in a
    window is said once per window with its count (mutation: flap_admit()
    always True);
  * a restart is not a change of everything: within SETTLE of a start
    nothing is compared (mutation: tick() without the settle);
  * a restart of the watcher says nothing again: the state file (mutation:
    watch_events() not reading it).

Every time here is pinned: no check compares two readings of the real clock.
"""
import contextlib
import io
import json
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src", "prismash")
sys.path.insert(0, SRC)
sys.path.insert(0, HERE)

_root = tempfile.mkdtemp(prefix="eventwatch-")
for k, v in (("PRISMA_CONFIG", "prisma-routing.conf"),
             ("PRISMA_CONFIRM", "confirm-pending"),
             ("PRISMA_ROLLBACK", "rollback"),
             ("PRISMA_EVENTS", "events.log"),
             ("PRISMA_EVENTS_STATE", "events.state"),
             ("PRISMA_WATCH_STATE", "events-watch.state"),
             ("PRISMA_DOORS", "no-doors.sock"),
             ("PRISMA_WTMP", "wtmp"), ("PRISMA_BTMP", "btmp"),
             ("PRISMA_FRR_STARTED", "no-frr.pid"),
             ("PRISMA_LOGGING_CONF", "logging.conf"),
             ("PRISMA_LOGGING_STATUS", "logging.status"),
             ("PRISMA_LOGGING_FWD_STATE", "events-forward.state"),
             # The BFD notifications the loop sends (#559): this machine's
             # own snmpd is not the box's (test/test_bfdtraps.py).
             ("PRISMA_SNMPD_CONF", "snmpd.conf"),
             ("PRISMA_AGENTX_SOCKET", "agentx.sock"),
             ("PRISMA_BFD_NOTIFY_STATE", "bfd-notifications"),
             # The cards Linux holds (#588): this machine's own are not the
             # box's; the tests that read some build a sysfs of their own.
             ("PRISMA_SYS_CLASS_NET", "no-sys-class-net"),
             # The PPPoE control plane's record of its runs (#597).
             ("PRISMA_BNG_RUNS", "bng-runs"),
             # The PPPoE session trail (#597, docs/bng.md 0.7): none here
             # but the one the trail tests write.
             ("PRISMA_PPPOE_TRAIL", "trail/pppoe-sessions.log")):
    os.environ[k] = os.path.join(_root, v)
os.environ.pop("PRISMA_WATCH_NETDEVS", None)

import events  # noqa: E402
import eventwatch as W  # noqa: E402
import transceiver as T  # noqa: E402
import transceiver_fixtures as F  # noqa: E402

fail = 0


def check(c, msg):
    global fail
    print(("  [ ok ] " if c else "  FAIL: ") + msg)
    fail += 0 if c else 1


NOW = 1790836414.0
W.SHELL_OF = lambda user: {"noc": "/usr/bin/prismash-login",
                           "root": "/bin/bash"}.get(user, "")


def texts(evs):
    return [e["text"] for e in evs]


def run(state, readings, starts=None, now=NOW, logins=None):
    return W.tick(state, readings, starts or {"dataplane": None,
                                              "routing": None}, now, logins)


print("=== links: physical, sub-interface, administrative or the wire ===")
IFACES = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)
GigabitEthernet0/8/0              1      up          9000/0/0/0
GigabitEthernet0/8/0.100          4      up          9000/0/0/0
GigabitEthernet0/8/0.200          5      up          9000/0/0/0
GigabitEthernet0/9/0              2      up          9000/0/0/0
tap4096                           6      up          9000/0/0/0
local0                            0     down          0/0/0/0
"""
HW = """              Name                Idx   Link  Hardware
GigabitEthernet0/8/0               1     {a}   GigabitEthernet0/8/0
GigabitEthernet0/9/0               2     up    GigabitEthernet0/9/0
tap4096                            6     up    tap4096
"""
import routes  # noqa: E402


def links(a="up", ifaces=IFACES):
    return W.link_reading(routes.parse_interfaces(ifaces),
                          routes.parse_hardware(HW.format(a=a)))


up, down = links("up"), links("down")
check(set(up) == {"GigabitEthernet0/8/0", "GigabitEthernet0/8/0.100",
                  "GigabitEthernet0/8/0.200", "GigabitEthernet0/9/0"},
      f"link_reading: the operator's interfaces, not local0 nor a TAP: "
      f"{sorted(up)}")
check(up["GigabitEthernet0/8/0.100"] == {"admin": "up", "link": "up",
                                         "parent": "GigabitEthernet0/8/0",
                                         "ups": None, "downs": None},
      "link_reading: a sub-interface's link is its parent's")
check(down["GigabitEthernet0/8/0.100"]["link"] == "down",
      "link_reading: and goes down with it")
ch = W.link_changes(up, down)
check(len(ch) == 1 and ch[0]["subject"] == "GigabitEthernet0/8/0"
      and ch[0]["severity"] == "warning" and ch[0]["reason"] == "link"
      and "link down, administratively up" in ch[0]["text"]
      and "its 2 sub-interface(s) with it" in ch[0]["text"],
      f"link_changes: one event for the port and its sub-interfaces: "
      f"{texts(ch)}")
ch = W.link_changes(down, up)
check(len(ch) == 1 and ch[0]["severity"] == "notice"
      and ch[0]["text"].startswith("interface GigabitEthernet0/8/0: link up"),
      f"link_changes: and back up: {texts(ch)}")
shut = IFACES.replace("GigabitEthernet0/8/0.100          4      up",
                      "GigabitEthernet0/8/0.100          4     down")
ch = W.link_changes(up, links("up", shut))
check(len(ch) == 1 and ch[0]["text"] == "sub-interface GigabitEthernet0/8/0"
      ".100 is administratively down: shut down by configuration"
      and ch[0]["reason"] == "administrative",
      f"link_changes: a sub-interface shut down says it is administrative: "
      f"{texts(ch)}")
shut = IFACES.replace("GigabitEthernet0/9/0              2      up",
                      "GigabitEthernet0/9/0              2     down")
ch = W.link_changes(up, links("up", shut))
check(len(ch) == 1 and "interface GigabitEthernet0/9/0 is administratively "
      "down" in ch[0]["text"],
      f"link_changes: so does a port: {texts(ch)}")
check(W.link_changes(up, up) == [],
      "link_changes: nothing moved, nothing said")
gone = {k: v for k, v in up.items() if k != "GigabitEthernet0/9/0"}
check(W.link_changes(up, gone) == [] and W.link_changes(gone, up) == [],
      "link_changes: an interface deleted or created is a commit's, not news")

print("\n=== modules: in, out, past a threshold, back ===")


def module(data, typ="sff-8472", name="GigabitEthernet0/8/0"):
    return T.decode(T.parse_dump(F.dump(name, "dpdk", typ, data,
                                        F.SFF8472_READ))[name])


absent = T.decode({"name": "GigabitEthernet0/8/0", "unread": "no-module",
                   "why": "the card answered -5 (Input/output error)"})
m_ok = W.module_reading([module(F.sfp_internal())])
m_low = W.module_reading([module(F.sfp_low_rx())])
m_out = W.module_reading([absent])
p = m_ok["GigabitEthernet0/8/0"]
check(p["present"] and "Rei do SFP" in p["module"] and p["rx"] == "normal"
      and p["temp"] == "normal",
      f"module_reading: the module, its rx and temperature levels: {p}")
check(m_out["GigabitEthernet0/8/0"]["present"] is False,
      "module_reading: an empty cage is a module out")
check(W.module_reading([T.decode({"name": "x", "unread": "unavailable",
                                  "why": "?"})]) == {},
      "module_reading: a port that could not be read is neither in nor out")
ch = W.module_changes(m_out, m_ok)
check(len(ch) == 1 and ch[0]["text"].startswith(
      "module inserted in GigabitEthernet0/8/0: Rei do SFP"),
      f"module_changes: inserted, with what: {texts(ch)}")
ch = W.module_changes(m_ok, m_out)
check(len(ch) == 1 and ch[0]["severity"] == "warning"
      and "module removed from GigabitEthernet0/8/0 (it was Rei do SFP" in
      ch[0]["text"], f"module_changes: removed: {texts(ch)}")
ch = W.module_changes(m_ok, m_low)
check(len(ch) == 1 and ch[0]["severity"] == "warning"
      and "rx power -19.00 dBm, below its low warning (-18.01 dBm)" in
      ch[0]["text"] and ch[0]["reason"] == "rx power low-warning",
      f"module_changes: rx power past its warning: {texts(ch)}")
ch = W.module_changes(m_low, m_ok)
check(len(ch) == 1 and ch[0]["severity"] == "notice"
      and "rx power back within its thresholds (-6.54 dBm)" in ch[0]["text"],
      f"module_changes: and back: {texts(ch)}")
dark = W.module_reading([module(F.sfp_dark())])
ch = W.module_changes(m_ok, dark)
check(len(ch) == 1 and ch[0]["severity"] == "error"
      and "rx power no light, below its low alarm" in ch[0]["text"],
      f"module_changes: no light is an alarm, said as no light: {texts(ch)}")
check(W._worst([None, {"level": "normal"}, {"level": "low-alarm"},
                {"level": "high-warning"}]) == {"level": "low-alarm"}
      and W._worst([None]) is None, "_worst: the worst lane wins")
check(W._bound({"level": "high-warning", "thresholds": {"high_warning": 2}},
               str) == "its high warning (2)"
      and W._bound({"level": "normal"}, str) == "",
      "_bound: the threshold a level is past")
other = W.module_reading([T.decode(T.parse_dump(F.dump(
    "GigabitEthernet0/8/0", "dpdk", "sff-8472", F.sfp_external(),
    F.SFF8472_READ))["GigabitEthernet0/8/0"])])
ch = W.module_changes(m_ok, other)
check(len(ch) == 1 and "replaced: FINISAR CORP." in ch[0]["text"],
      f"module_changes: a module swapped between readings: {texts(ch)}")

print("\n=== BGP: Established, left with FRR's reason, flapped between ===")


def nbr(state, up, dropped, **kw):
    d = {"bgpState": state, "remoteAs": 65001, "connectionsEstablished": up,
         "connectionsDropped": dropped,
         "lastResetDueTo": "No path to specified Neighbor"}
    d.update(kw)
    return {"default": {"vrfId": 0, "vrfName": "default", "10.0.0.2": d},
            "red": {"vrfId": 7, "vrfName": "red"}}


NOTE = dict(lastResetDueTo="BGP Notification received",
            lastNotificationReason="Cease/Administrative Shutdown",
            lastShutdownDescription="maintenance")
b_up = W.bgp_reading(nbr("Established", 1, 0))
b_down = W.bgp_reading(nbr("Active", 1, 1, **NOTE))
check(list(b_up) == ["default 10.0.0.2"] and b_up["default 10.0.0.2"]["as"]
      == 65001, f"bgp_reading: one session per vrf and peer: {b_up}")
check(W.bgp_reason(nbr("Active", 1, 1, **NOTE)["default"]["10.0.0.2"]) ==
      "BGP Notification received: Cease/Administrative Shutdown "
      "('maintenance')", "bgp_reason: FRR's reset, its NOTIFICATION, the "
      "peer's message")
check(W._n(3) == 3 and W._n(None) == 0 and W._n(True) == 0 and W._n(-1) == 0,
      "_n: a count or 0")
check(W._bgp_what("red 10.1.1.1", {"as": 65009}) ==
      "BGP session with 10.1.1.1 (AS 65009, vrf red)",
      "_bgp_what: the vrf when it is not the default")
ch = W.bgp_changes(b_up, b_down)
check(len(ch) == 1 and ch[0]["text"] == "BGP session with 10.0.0.2 (AS 65001)"
      " left Established: BGP Notification received: Cease/Administrative "
      "Shutdown ('maintenance')" and ch[0]["severity"] == "warning",
      f"bgp_changes: left Established, with the reason: {texts(ch)}")
ch = W.bgp_changes(b_down, W.bgp_reading(nbr("Established", 2, 1, **NOTE)))
check(len(ch) == 1 and ch[0]["text"].endswith("is Established"),
      f"bgp_changes: Established again: {texts(ch)}")
flap = W.bgp_reading(nbr("Established", 3, 2, **NOTE))
ch = W.bgp_changes(W.bgp_reading(nbr("Established", 2, 1, **NOTE)), flap)
check(len(ch) == 1 and "went down and came back between two readings (1 "
      "time(s))" in ch[0]["text"],
      f"bgp_changes: a flap between two readings, by FRR's counter: "
      f"{texts(ch)}")
check(W.bgp_changes(W.bgp_reading(nbr("Established", 9, 9)), flap,
                    restarted=True) == [],
      "bgp_changes: across a restart of FRR the counters are a baseline")

print("\n=== BFD: down with bfdd's diagnostic ===")
import opshow  # noqa: E402


def bfd(status, down, diag="ok"):
    peers = [{"peer": "10.0.0.2", "local": "10.0.0.1", "vrf": "default",
              "interface": "vppwan0", "id": 1, "status": status,
              "diagnostic": diag, "multihop": False}]
    counters = [{"peer": "10.0.0.2", "id": 1, "session-down": down}]
    import prismash
    return W.bfd_reading(opshow.bfd_fields(
        {"answered": True, "garbled": False,
         "sessions": prismash.bfd_sessions(peers, counters)}))


check(W.bfd_reading({"answered": False}) is None,
      "bfd_reading: bfdd not answering is no reading, not no sessions")
ch = W.bfd_changes(bfd("up", 0), bfd("down", 1,
                                     "control detection time expired"))
check(len(ch) == 1 and ch[0]["text"] == "BFD session with 10.0.0.2 (on "
      "vppwan0) down: control detection time expired"
      and ch[0]["reason"] == "control detection time expired",
      f"bfd_changes: down, with the diagnostic: {texts(ch)}")
ch = W.bfd_changes(bfd("down", 1), bfd("up", 1))
check(len(ch) == 1 and ch[0]["text"].endswith("up"),
      f"bfd_changes: up: {texts(ch)}")
ch = W.bfd_changes(bfd("up", 1), bfd("up", 3, "neighbor signaled session "
                                     "down"))
check(len(ch) == 1 and "(2 time(s))" in ch[0]["text"],
      f"bfd_changes: down and back between readings: {texts(ch)}")
check(W.bfd_changes(bfd("down", 0), bfd("init", 0)) == [],
      "bfd_changes: down to init is not up")

print("\n=== CGNAT pools: out of blocks once, and with blocks again ===")
import cgnatshow  # noqa: E402


def blocks(in_use, refusals, total=4):
    return W.pool_reading(cgnatshow.parse_blocks(
        "pool small (hold-down 0s)\n"
        f"  thread 0: {in_use}/{total} blocks in use, 9 allocations (0 "
        f"extensions), 0 releases, {refusals} refusals, 0 pinned\n"
        f"  total: {in_use}/{total} in use, 9 allocations (0 extensions), "
        f"0 releases, {refusals} refusals, 0 pinned\n"))


r0 = blocks(4, 0)
check(r0 == {"small": {"refusals": 0, "in_use": 4, "blocks": 4,
                       "reserve": False, "exhausted": False}},
      f"pool_reading: a pool's totals: {r0}")
s, evs = {}, []
for reading in (blocks(4, 0), blocks(4, 1), blocks(4, 5), blocks(4, 9),
                blocks(3, 9), blocks(3, 9)):
    got, s = run(s, {"pool": reading})
    evs += got
check(texts(evs) == ["CGNAT pool small ran out of blocks: 1 request(s) for "
                     "a block refused since the last reading, 4 of 4 blocks "
                     "in use",
                     "CGNAT pool small has free blocks again: 3 of 4 blocks "
                     "in use"],
      f"pool_changes: out once while it keeps refusing, then free: "
      f"{texts(evs)}")
check(W.pool_reading_carry({"a": {"exhausted": False}},
                           {"a": {"exhausted": True}}) ==
      {"a": {"exhausted": True}}, "pool_reading_carry: the verdict carried")
c = blocks(0, 0)
c = W.pool_reading_carry(c, {"small": {"exhausted": True}})
check(W.pool_changes(blocks(4, 9), c) == [] and not c["small"]["exhausted"],
      "pool_changes: a count that went down is a restarted dataplane: a "
      "baseline")

print("\n=== the log export's collector: lost and found ===")


def logscreen(conn):
    return ("IPFIX export of block events (RFC 8158)\n"
            "  state       disabled\n"
            "  exporter    none: nothing is sent over UDP\n"
            "TCP streams carried by the log container (pid 7)\n"
            "TCP transport: disabled\n"
            "syslog transport (RFC 5424 over TCP)\n"
            f"  collector   198.51.100.3:601  {conn}\n"
            "  lines       3089 written, 1000 bytes sent, 0 pending\n")


import logstate  # noqa: E402


def coll(conn):
    lw = logstate.LogWatch()
    lw.observe(logscreen(conn), now=NOW)
    return W.collector_reading(lw.snapshot()[1])


c_ok, c_lost = coll("connected"), coll("connecting")
check(c_ok["syslog"] == {"configured": True, "collector": "198.51.100.3:601",
                         "ok": True, "why": "connected"},
      f"collector_reading: a TCP stream's collector, by its connection: "
      f"{c_ok['syslog']}")
ch = W.collector_changes(c_ok, c_lost)
check(len(ch) == 1 and ch[0]["text"] == "the CGNAT log export (syslog) lost "
      "its collector 198.51.100.3:601: connecting",
      f"collector_changes: lost: {texts(ch)}")
ch = W.collector_changes(c_lost, c_ok)
check(len(ch) == 1 and "reached its collector 198.51.100.3:601 again" in
      ch[0]["text"], f"collector_changes: found: {texts(ch)}")
check(W.collector_changes(c_ok, dict(c_lost, syslog=dict(
    c_lost["syslog"], collector="192.0.2.1:601"))) == [],
      "collector_changes: pointed elsewhere is a commit's")

print("\n=== logins: wtmp and btmp, never a log line ===")


def utmp(typ, line, user, host, t, pid=100):
    return W.UTMP.pack(typ, pid, line.encode(), b"", user.encode(),
                       host.encode(), 0, 0, 0, int(t), 0, b"", b"")


wtmp, btmp = os.environ["PRISMA_WTMP"], os.environ["PRISMA_BTMP"]
with open(wtmp, "wb") as fh:
    fh.write(utmp(W.USER_PROCESS, "pts/0", "old", "192.0.2.1", NOW - 99999))
open(btmp, "wb").close()
recs, pos = W.read_utmp(wtmp, None)
check(recs == [] and pos["off"] == W.UTMP.size,
      "read_utmp: a first reading starts at the end - history is not news")
with open(wtmp, "ab") as fh:
    fh.write(utmp(W.USER_PROCESS, "pts/1", "noc", "192.0.2.7", NOW + 1))
    fh.write(utmp(W.DEAD_PROCESS, "pts/1", "", "", NOW + 301))
    fh.write(b"\0" * 10)                          # a record being written
recs, pos2 = W.read_utmp(wtmp, pos)
check([r["user"] for r in recs] == ["noc", ""] and pos2["off"] ==
      3 * W.UTMP.size, f"read_utmp: whole records only: {recs}")
check(W.read_utmp(wtmp, pos2)[0] == [], "read_utmp: and each one once")
os.replace(wtmp, wtmp + ".1")
with open(wtmp, "wb") as fh:
    fh.write(utmp(W.USER_PROCESS, "tty1", "root", "", NOW + 400))
recs, _ = W.read_utmp(wtmp, pos2)
check([r["user"] for r in recs] == ["root"],
      "read_utmp: a rotated file is read from its start")
check(W.parse_utmp(utmp(W.USER_PROCESS, "pts/2", "a\x1bb", "h", NOW))[0][
    "user"] == "ab", "parse_utmp, _text: a control character typed at the "
      "login is not written")
check(W._text(b"no\x1bc\0garbage") == "noc", "_text: up to the NUL, no "
      "control characters")
check(W._origin({"host": "192.0.2.7", "line": "pts/0"}) == "ssh 192.0.2.7"
      and W._origin({"host": "", "line": "tty1"}) == "the console (tty1)",
      "_origin: ssh and the console")
opened = {}
ch = W.login_changes(
    [{"type": W.USER_PROCESS, "line": "pts/1", "user": "noc",
      "host": "192.0.2.7", "t": NOW + 1, "pid": 1},
     {"type": W.DEAD_PROCESS, "line": "pts/1", "user": "", "host": "",
      "t": NOW + 301, "pid": 1},
     {"type": W.DEAD_PROCESS, "line": "pts/9", "user": "", "host": "",
      "t": NOW + 302, "pid": 1}],
    [{"type": 6, "line": "ssh:notty", "user": "admin", "host": "203.0.113.9",
      "t": NOW + 5, "pid": 2}], opened, W.SHELL_OF)
check([(c["text"], c["from"], c["severity"]) for c in ch] ==
      [("noc logged in to the VTY", "ssh 192.0.2.7", "notice"),
       ("noc logged out after 5 min", "ssh 192.0.2.7", "info"),
       ("failed login as admin", "ssh 203.0.113.9", "warning")]
      and opened == {},
      f"login_changes: a login, its logout, a failed one; a logout with no "
      f"login is said nothing about: {[c['text'] for c in ch]}")
check(W._shell_of("no-such-account-here") == "",
      "_shell_of: an account the host does not have has no shell")

print("\n=== flapping: held, counted, said once a window ===")
s, said = {}, []
t = NOW
for i in range(60):
    reading = up if i % 2 == 0 else down
    got, s = run(s, {"link": reading}, now=t)
    said += got
    t += 7
check(len([e for e in said if "flapped" not in e["text"]]) ==
      W.FLAP_EVENTS,
      f"flap_admit: {W.FLAP_EVENTS} changes written one by one, "
      f"then held: {len(said)} events for 59 changes")
summ = [e for e in said if "flapped" in e["text"]]
check(len(summ) == 1 and summ[0]["text"].startswith(
      "interface GigabitEthernet0/8/0 flapped ") and "times in 5 min, not each"
      " recorded; it is now link " in summ[0]["text"]
      and summ[0]["kind"] == "link" and summ[0]["severity"] == "warning",
      f"flap_summaries: one summary for the window: {texts(summ)}")
got, s = run(s, {"link": up}, now=t + 2000)
got2, s = run(s, {"link": up}, now=t + 2400)
got3, s = run(s, {"link": down}, now=t + 2500)
check([e["text"] for e in got3] and "link down" in got3[0]["text"]
      and "flapped" not in got3[0]["text"],
      f"flap_summaries: a quiet window ends the hold; the next change is "
      f"written: {texts(got + got2 + got3)}")
fl = {}
c = {"key": "login-failed:ssh 203.0.113.9", "what": "failed logins from "
     "ssh 203.0.113.9", "state": "failing", "source": "host",
     "kind": "login", "subject": "admin"}
admitted = [W.flap_admit(fl, c, NOW + i) for i in range(30)]
check(admitted.count(True) == W.FLAP_EVENTS,
      f"flap_admit: a brute force from one address is held after "
      f"{W.FLAP_EVENTS}")
check(W.flap_sentence({"kind": "bgp", "what": "BGP session with 10.0.0.2 "
                       "(AS 65001)", "count": 9, "now": "Established"}, 5) ==
      "BGP session with 10.0.0.2 (AS 65001) flapped 9 times in 5 min, not "
      "each recorded; it is now Established", "flap_sentence: a session's")
sm = W.flap_summaries(fl, NOW + 400)
check(texts(sm) == ["failed logins from ssh 203.0.113.9: 30 in 7 min, not "
                    "each recorded"],
      f"flap_sentence: a burst of failed logins in one line: {texts(sm)}")

print("\n=== a start is not a change of everything ===")
_, s = run({}, {"link": up}, now=NOW)
got, s2 = run(s, {"link": down}, starts={"dataplane": NOW + 70,
                                          "routing": None}, now=NOW + 100)
check(got == [] and s2["seen"]["link"] == up,
      "tick: within SETTLE of the dataplane's start nothing is compared, "
      "and the reading before it is kept")
got, s2 = run(s2, {"link": down}, starts={"dataplane": NOW + 70,
                                           "routing": None}, now=NOW + 200)
check(len(got) == 1 and "link down" in got[0]["text"],
      f"tick: after it, a link still down is said: {texts(got)}")
got, _ = run(s2, {"link": None}, now=NOW + 300)
check(got == [], "tick: a dataplane that did not answer is not every link "
      "down")
_, s = run({}, {"bgp": b_up}, now=NOW)
check(run(s, {"bgp": b_down}, starts={"dataplane": None,
                                       "routing": NOW + 10},
          now=NOW + 20)[0] == [],
      "tick: BGP is not compared within SETTLE of the routing daemons' start")
check(W.tick({}, {"link": up}, {"dataplane": None, "routing": None},
             NOW)[0] == [], "tick: a first reading is a baseline")

print("\n=== standing at start: said once a boot (#588 phase 3) ===")
# A port up and dark, a module below its RX warning, a session not up, a
# pool out of blocks, a collector not reached - each said, in the present
# tense, from one reading; and the healthy ones not.
st = W.link_standing(down)
check(texts(st) == ["interface GigabitEthernet0/8/0: link down, "
                    "administratively up: the wire or the far end; its 2 "
                    "sub-interface(s) with it"] and st[0]["key"] ==
      "link:GigabitEthernet0/8/0",
      f"link_standing: a port administratively up whose link is down, with "
      f"its sub-interfaces: {texts(st)}")
shut9 = links("up", IFACES.replace("GigabitEthernet0/9/0              2   "
                                   "   up", "GigabitEthernet0/9/0           "
                                   "   2     down"))
check(W.link_standing(up) == [] and W.link_standing(
    {k: dict(v, link="down") for k, v in shut9.items()
     if k == "GigabitEthernet0/9/0"}) == [],
      "link_standing: a link up, and a port shut by configuration, are not "
      "said")
st = W.module_standing(m_low)
check(texts(st) == ["GigabitEthernet0/8/0: rx power -19.00 dBm, below its "
                    "low warning (-18.01 dBm)"]
      and st[0]["severity"] == "warning"
      and st[0]["reason"] == "rx power low-warning",
      f"module_standing: rx power standing past its warning: {texts(st)}")
check(W.module_standing(m_ok) == [] and W.module_standing(m_out) == [],
      "module_standing: a module within its thresholds, or no module, is "
      "not said")
check(W.module_standing(dark)[0]["severity"] == "error",
      "module_standing: no light is an alarm")
check(W.level_text("x", {"temp": "high-alarm", "temp_c": "91.0 C",
                         "temp_bound": "its high alarm (90.0 C)"}, "temp")
      == ("x: temperature 91.0 C, above its high alarm (90.0 C)", "error"),
      "level_text: a temperature above its alarm is an error")
st = W.bgp_standing(b_down)
check(texts(st) == ["BGP session with 10.0.0.2 (AS 65001) is not "
                    "Established: Active; FRR's last reset: BGP Notification "
                    "received: Cease/Administrative Shutdown "
                    "('maintenance')"],
      f"bgp_standing: a session not Established, with FRR's state and last "
      f"reset: {texts(st)}")
check(W.bgp_standing(b_up) == [] and W.bgp_standing(W.bgp_reading(
    nbr("Idle", 0, 0, adminShutDown=True))) == [],
      "bgp_standing: Established, or shut by the box's configuration, is "
      "not said")
st = W.bfd_standing(bfd("down", 1, "control detection time expired"))
check(texts(st) == ["BFD session with 10.0.0.2 (on vppwan0) is down: control "
                    "detection time expired"],
      f"bfd_standing: a session down, with bfdd's diagnostic: {texts(st)}")
check(W.bfd_standing(bfd("up", 0)) == [] and W.bfd_standing(
    bfd("shutdown", 0)) == [] and W.bfd_standing(None) == [],
      "bfd_standing: up, or shut by configuration, is not said")
full = blocks(4, 3)
st = W.pool_standing(full)
check(texts(st) == ["CGNAT pool small is out of blocks: 3 request(s) for a "
                    "block refused since the dataplane started, 4 of 4 "
                    "blocks in use"] and full["small"]["exhausted"],
      f"pool_standing: none free and refusing, its verdict set: {texts(st)}")
check(W.pool_standing(blocks(4, 0)) == [] and W.pool_standing(
    blocks(3, 9)) == [],
      "pool_standing: full and refusing nobody, or with a block free, is not "
      "said")
st = W.collector_standing(c_lost)
check(texts(st) == ["the CGNAT log export (syslog) is not reaching its "
                    "collector 198.51.100.3:601: connecting"],
      f"collector_standing: a stream not reaching its collector: "
      f"{texts(st)}")
check(W.collector_standing(c_ok) == [], "collector_standing: connected is "
      "not said")
check(W.standing_due({}, "link", NOW - 9000, None, NOW)
      and not W.standing_due({"link": round(NOW - 9000)}, "link",
                             NOW - 9000.4, None, NOW)
      and W.standing_due({"link": round(NOW - 90000)}, "link", NOW - 9000,
                         None, NOW),
      "standing_due: once a boot - a state that carries this boot says "
      "nothing again, an older boot's does")
check(not W.standing_due({}, "bgp", NOW - 9000, NOW - 100, NOW)
      and W.standing_due({}, "bgp", NOW - 9000, NOW - 200, NOW),
      "standing_due: not before the component has run STANDING_SETTLE")
many = {f"memif{i}/0": {"admin": "up", "link": "down", "parent": ""}
        for i in range(W.STANDING_MAX + 5)}
st = W.standing_changes("link", many)
check(len(st) == W.STANDING_MAX + 1 and all(
    t.startswith(W.STANDING) for t in texts(st))
      and st[-1]["text"] == "standing at start: and 5 more link condition(s) "
      "like these, not each recorded",
      f"standing_changes: bounded, and the rest counted: {texts(st)[-1:]}")
check(W.standing_changes("link", down, {"link:GigabitEthernet0/8/0"}) == [],
      "standing_changes: a condition this tick's change already said is not "
      "said twice")
BOOT = NOW - 86400
s0, said = {}, []
for t in (NOW, NOW + 10, NOW + 20):
    got, s0 = W.tick(s0, {"link": down, "transceiver": m_low},
                     {"dataplane": None, "routing": None, "boot": BOOT}, t)
    said.append(texts(got))
check(said[0] == ["standing at start: interface GigabitEthernet0/8/0: link "
                  "down, administratively up: the wire or the far end; its "
                  "2 sub-interface(s) with it",
                  "standing at start: GigabitEthernet0/8/0: rx power -19.00 "
                  "dBm, below its low warning (-18.01 dBm)"]
      and said[1:] == [[], []],
      f"tick: what stands at the first reading is said once, and not at the "
      f"next: {said}")
got, _ = W.tick(s0, {"link": down}, {"dataplane": None, "routing": None,
                                     "boot": BOOT + 9000}, NOW + 9100)
check(len(got) == 1 and got[0]["text"].startswith(W.STANDING),
      f"tick: after a boot it stands again, and is said again: {texts(got)}")
got, s1 = W.tick({}, {"link": up}, {"dataplane": None, "routing": None,
                                    "boot": BOOT}, NOW)
check(got == [] and s1["stood"]["link"] == round(BOOT),
      "tick: a healthy box announces nothing, and the boot is carried")
_, s1 = W.tick({}, {"link": up}, {"dataplane": None, "routing": None},
               NOW)
del s1["stood"]
got, _ = W.tick(s1, {"link": down}, {"dataplane": None, "routing": None,
                                     "boot": BOOT}, NOW + 10)
check(texts(got) == ["interface GigabitEthernet0/8/0: link down, "
                     "administratively up: the wire or the far end; its 2 "
                     "sub-interface(s) with it"],
      f"tick: a state from 0.152 - no `stood` - and a link that went down "
      f"meanwhile: said once, as the change: {texts(got)}")
evs = [W.tick({}, {"link": down}, {"dataplane": None, "routing": None,
                                   "boot": BOOT}, NOW)[0][0]]
check(evs[0]["severity"] == "warning" and evs[0]["kind"] == "link"
      and evs[0]["subject"] == "GigabitEthernet0/8/0"
      and events.event_error(evs[0]) is None,
      "tick: a standing event is one the record takes, with its kind and "
      "subject")

print("\n=== the state: cadence, written when it moved ===")
check(W.due({}, "transceiver", NOW) and not W.due(
    {"last": {"transceiver": NOW - 30}}, "transceiver", NOW)
      and W.due({"last": {"transceiver": NOW - 61}}, "transceiver", NOW)
      and W.due({"last": {"transceiver": NOW + 99}}, "transceiver", NOW),
      "due: each kind at its cadence; a clock gone back reads again")
check(W.due({"last": {"link": NOW}}, "link", NOW, force=True),
      "due: force reads everything")
check(not W.state_moved({"last": {"a": 1}, "seen": {}},
                        {"last": {"a": 2}, "seen": {}})
      and W.state_moved({"seen": {}}, {"seen": {"link": {}}}),
      "state_moved: the reading times alone do not write the file")
p = os.environ["PRISMA_WATCH_STATE"]
check(W.write_state({"seen": {"x": 1}}, p) and W.read_state(p) ==
      {"seen": {"x": 1}}, "write_state, read_state: round trip")
check(W.read_state(os.path.join(_root, "none")) == {},
      "read_state: none is {}")
check(not W.write_state({}, "/proc/no/such"), "write_state: False when not "
      "written")

print("\n=== the Shell's watcher: readings, a tick, the loop ===")
import prismash  # noqa: E402


class Dp(prismash.Vpp):
    TRACE = False

    def __init__(self, answers):
        self.answers = answers
        self.commands = []

    def run(self, cmd):
        self.commands.append(cmd)
        a = self.answers.get(cmd, (1, ""))
        return (0, a) if isinstance(a, str) else a


BGPJSON = json.dumps(nbr("Established", 1, 0))
ANSWERS = {
    "show interface": IFACES,
    "show hardware-interfaces brief": HW.format(a="up"),
    "show bpa transceiver": F.dump("GigabitEthernet0/8/0", "dpdk", "sff-8472",
                                   F.sfp_internal(), F.SFF8472_READ)
    + "interface tap4096 unread not-dpdk not a DPDK port\n",
    "show bpa blocks": "no pool on this box allocates blocks: no dynamic "
                       "pool, and no deterministic pool with a reserve",
    "show bpa logging": logscreen("connected"),
    "show clock": "Time now 120.50, Mon, 6 Oct 2026 10:00:00 GMT",
}


def frr(bgp=BGPJSON, bfd_up=True):
    shows = {"show bgp vrf all neighbors json": (0, bgp),
             "show bfd peers json": (0, json.dumps([
                 {"peer": "10.0.0.2", "local": "10.0.0.1", "vrf": "default",
                  "interface": "vppwan0", "id": 1, "multihop": False,
                  "status": "up" if bfd_up else "down",
                  "diagnostic": "ok" if bfd_up else
                  "control detection time expired"}])),
             "show bfd peers counters json": (0, json.dumps(
                 [{"peer": "10.0.0.2", "id": 1,
                   "session-down": 0 if bfd_up else 1}]))}
    return type("F", (prismash.Frr,), {
        "show": lambda self, cmd: shows.get(cmd, (1, "not running"))})()


sh = prismash.Shell()
sh.dp = Dp(dict(ANSWERS))
sh.frr = frr()
r = {k: sh.watch_read(k) for k in W.CADENCE if k != "login"}
check(set(r["link"]) == set(up) and r["transceiver"]["GigabitEthernet0/8/0"]
      ["present"] and "tap4096" not in r["transceiver"]
      and r["bgp"] == b_up and r["bfd"] and r["pool"] == {}
      and r["collector"]["syslog"]["ok"],
      f"watch_read: every kind from the box's own screens: "
      f"{sorted(k for k, v in r.items() if v is not None)}")
sh.dp = Dp({})
sh.frr = type("F", (prismash.Frr,), {"show": lambda self, c: (1, "")})()
check(all(sh.watch_read(k) is None for k in W.CADENCE
          if k not in ("login", "trail") + W.BNG_KINDS),
      "watch_read: nothing answering is None for every kind, not empty")
check(sh.watch_read("trail") == {"file": "", "seq": 0, "sha": "",
                                 "closed": []},
      "watch_read: no PPPoE session trail is a reading - none written")
check(sh.watch_read("bng") == {"runs": []}
      and sh.watch_read("radius") == {"started": None, "servers": {}},
      "watch_read: no record of the PPPoE control plane's runs is a box "
      "where it has not run - a reading, with no RADIUS server asked")
_boom = sh.watch_read
sh.watch_read = lambda kind: 1 / 0
_err = io.StringIO()
with contextlib.redirect_stderr(_err):
    _r1, _r2 = sh.watch_read_safe("bgp"), sh.watch_read_safe("bgp")
check(_r1 is None and _r2 is None and _err.getvalue().count("reading bgp "
      "failed") == 1,
      f"watch_read_safe: a reading that raised is no reading, said once: "
      f"{_err.getvalue()!r}")
sh.watch_read = _boom
sh.dp = Dp({"show bpa transceiver": "show bpa: unknown input `transceiver'"})
check(sh.watch_read("transceiver") is None,
      "watch_read: a plugin older than #590 is no reading")
sh.dp = Dp(dict(ANSWERS))
import time as _time  # noqa: E402
st = sh.watch_starts()
check(st["routing"] is None and abs(st["dataplane"] - (_time.time() - 120.5))
      < 60, f"watch_starts: the dataplane's start from its clock: {st}")
check(st.get("boot") == prismash.read_btime(),
      "watch_starts: and the kernel's boot, what stands is said once for "
      "(#588 phase 3)")
sh.watch_starts = lambda: {"dataplane": None, "routing": None}
sh.frr = frr()
got, s = sh.watch_tick({}, NOW)
check(got == [] and set(s["seen"]) == {"link", "transceiver", "bgp", "bfd",
                                       "pool", "collector", "bng", "radius"}
      and "logins" in s, f"watch_tick: a first tick is a baseline of every "
      f"kind: {sorted(s['seen'])}")
got, s1 = sh.watch_tick(s, NOW + 3)
check(got == [] and s1["last"]["transceiver"] == NOW,
      "watch_tick: a kind is read only when its cadence has come")
sh.dp.answers["show hardware-interfaces brief"] = HW.format(a="down")
sh.frr = frr(json.dumps(nbr("Active", 1, 1, **NOTE)), bfd_up=False)
with open(wtmp, "ab") as fh:
    fh.write(utmp(W.USER_PROCESS, "pts/3", "noc", "192.0.2.8", NOW + 11))
with open(btmp, "ab") as fh:
    fh.write(utmp(6, "ssh:notty", "root", "203.0.113.5", NOW + 12))
got, s2 = sh.watch_tick(s, NOW + 11)
check(sorted(e["kind"] for e in got) == ["bfd", "bgp", "link", "login",
                                         "login"],
      f"watch_tick: a link, BGP, BFD, a login and a failed one: "
      f"{texts(got)}")
check(all(events.event_error(e) is None for e in got),
      "watch_tick: every event is one the record takes")
check(all(events.event_error(e, privileged=False) for e in got),
      "and none of them is one an account may write")

# The loop, once: through the record and the state file, and a second run
# - a restart of the watcher - says nothing again.
for f in (os.environ["PRISMA_WATCH_STATE"], os.environ["PRISMA_EVENTS"]):
    if os.path.exists(f):
        os.remove(f)
sh.dp = Dp(dict(ANSWERS))
sh.frr = frr()
with contextlib.redirect_stdout(io.StringIO()):
    first = sh.watch_events(once=True)
check(first == [] and W.read_state().get("seen", {}).get("link") == up,
      "watch_events: the first pass is the baseline, kept in the state file")
sh.dp.answers["show hardware-interfaces brief"] = HW.format(a="down")
second = sh.watch_events(once=True)
third = sh.watch_events(once=True)
rec = events.read_events()
check(len(second) == 1 and third == [] and len(rec) == 1
      and rec[0]["kind"] == "link" and rec[0]["subject"] ==
      "GigabitEthernet0/8/0",
      f"watch_events: a change recorded once, and not again by the next "
      f"run: {texts(rec)}")
# What stands when the watcher first runs (#588 phase 3): said by the first
# pass, kept in the state file, and not again by a restart of the watcher in
# the same boot - the X520 below its RX warning on 10.40.200.166.
for f in (os.environ["PRISMA_WATCH_STATE"], os.environ["PRISMA_EVENTS"]):
    if os.path.exists(f):
        os.remove(f)
sh.watch_starts = lambda: {"dataplane": None, "routing": None,
                           "boot": NOW - 86400}
sh.dp = Dp(dict(ANSWERS, **{
    "show hardware-interfaces brief": HW.format(a="down"),
    "show bpa transceiver": F.dump("GigabitEthernet0/8/0", "dpdk",
                                   "sff-8472", F.sfp_low_rx(),
                                   F.SFF8472_READ)}))
sh.frr = frr()
first = sh.watch_events(once=True)
again = sh.watch_events(once=True)
rec = events.read_events()
check(sorted(e["kind"] for e in first) == ["link", "transceiver"]
      and all(e["text"].startswith(W.STANDING) for e in first)
      and again == [] and len(rec) == 2
      and W.read_state()["stood"]["link"] == round(NOW - 86400),
      f"watch_events: what stands is said by the first pass, and not by a "
      f"restart of the watcher in the same boot: {texts(rec)}")
sh.watch_starts = lambda: {"dataplane": None, "routing": None}
slept = []


class Stop(Exception):
    pass


forwarded = []
_fwd = prismash.eventfwd.Forwarder.run
prismash.eventfwd.Forwarder.run = lambda self, now=None: forwarded.append(1)


def sleeper(n):
    slept.append(n)
    if len(slept) == 3:
        raise Stop


try:
    sh.watch_events(sleep=sleeper)
except Stop:
    pass
prismash.eventfwd.Forwarder.run = _fwd
check(slept and all(0.5 <= n <= prismash.eventfwd.FORWARD_EVERY
                    for n in slept) and len(forwarded) == 3,
      f"watch_events: the loop sleeps its tick in slices, forwarding the "
      f"record after the tick and each slice (#588 phase 3): {slept} "
      f"{len(forwarded)}")


class Boom:
    def run(self):
        raise ValueError("a line nobody expected")


_err = io.StringIO()
with contextlib.redirect_stderr(_err):
    _f1, _f2 = sh.watch_forward(Boom()), sh.watch_forward(Boom())
check(_f1 == 0 and _f2 == 0 and _err.getvalue().count("forwarding failed")
      == 1, f"watch_forward: a pass that raised is no pass, said once: "
      f"{_err.getvalue()!r}")

print("\n=== a flap between two readings, counted (#588) ===")
# The plugin's count (bpa_linkcount.c) and Linux's carrier counts make a
# link that went down and came back between two readings an event. Mutations
# shown red: link_changes() skipping a port whose state did not move (the
# flap said nothing); link_transitions() without the backwards check (a
# memif made again said 3 flaps); link_transitions() ignoring `restarted`.
_csrc = open(os.path.join(HERE, "..", "src", "plugins", "bpa",
                          "bpa_linkcount.c")).read()
check('"interface %v link %s ups %u downs %u"' in _csrc,
      "parse_link_changes reads bpa_linkcount.c's own format string")
LC = ("interface GigabitEthernet0/8/0 link up ups {u} downs {d}\n"
      "interface GigabitEthernet0/9/0 link up ups 1 downs 0\n"
      "interface tap4096 link up ups 1 downs 0\n")
check(W.parse_link_changes(LC.format(u=4, d=3)) == {
          "GigabitEthernet0/8/0": {"link": "up", "ups": 4, "downs": 3},
          "GigabitEthernet0/9/0": {"link": "up", "ups": 1, "downs": 0},
          "tap4096": {"link": "up", "ups": 1, "downs": 0}},
      "parse_link_changes: each port's ups and downs")
check(W.parse_link_changes("show bpa: unknown input `link-changes'") == {}
      and W.parse_link_changes("") == {},
      "a plugin older than the count is no count, not zeros")


def clinks(a="up", u=1, d=0):
    return W.link_reading(routes.parse_interfaces(IFACES),
                          routes.parse_hardware(HW.format(a=a)),
                          W.parse_link_changes(LC.format(u=u, d=d)))


c0 = clinks()
check(c0["GigabitEthernet0/8/0"]["downs"] == 0
      and c0["GigabitEthernet0/8/0.100"]["downs"] is None,
      "link_reading: a port carries its count; a sub-interface none of its own")
ch = W.link_changes(c0, clinks("up", 4, 3))
check(len(ch) == 1 and ch[0]["severity"] == "warning"
      and ch[0]["reason"] == "flap"
      and ch[0]["text"] == "interface GigabitEthernet0/8/0: link went down and "
      "came back between two readings (3 time(s)): the wire or the far end; "
      "its 2 sub-interface(s) with it",
      f"link_changes: up twice, three downs between: {texts(ch)}")
ch = W.link_changes(clinks("down", 1, 1), clinks("down", 3, 3))
check(len(ch) == 1 and "came up and went down again between two readings "
      "(2 time(s)); it is down" in ch[0]["text"],
      f"down twice, two ups between: {texts(ch)}")
ch = W.link_changes(c0, clinks("down", 2, 2))
check(len(ch) == 1 and ch[0]["text"].startswith(
          "interface GigabitEthernet0/8/0: link down, administratively up")
      and ch[0]["text"].endswith("(3 changes since the last reading)"),
      f"a change of state that took three: {texts(ch)}")
ch = W.link_changes(c0, clinks("down", 1, 1))
check(len(ch) == 1 and "changes since" not in ch[0]["text"],
      "one that took one says no count")
check(W.link_changes(c0, clinks()) == [], "nothing moved, nothing said")
check(W.link_changes(clinks("up", 4, 3), clinks("up", 1, 0)) == [],
      "a count that went backwards (the port made again) is a baseline")
check(W.link_changes(c0, clinks("up", 4, 3), restarted=True) == [],
      "and so is a reading across a dataplane start")
check(W.link_changes(links("up"), clinks("up", 4, 3)) == [],
      "a reading with no count (an older plugin) says by state alone")

# The cards Linux holds: a sysfs of the attributes the reader opens.
_sys = os.path.join(_root, "sysfs")


def card(name, flags="0x1003", carrier="1", ups=1, downs=0, counts=True):
    d = os.path.join(_sys, name)
    os.makedirs(d, exist_ok=True)
    for f in ("carrier", "carrier_up_count", "carrier_down_count"):
        if os.path.exists(os.path.join(d, f)):
            os.remove(os.path.join(d, f))
    with open(os.path.join(d, "flags"), "w") as fh:
        fh.write(flags + "\n")
    if carrier is not None:
        with open(os.path.join(d, "carrier"), "w") as fh:
            fh.write(carrier + "\n")
    if counts:
        for f, v in (("carrier_up_count", ups), ("carrier_down_count", downs)):
            with open(os.path.join(d, f), "w") as fh:
                fh.write(f"{v}\n")


card("eno1")
card("eno2", flags="0x1002", carrier=None)        # down: carrier is EINVAL
card("eno3", counts=False)                         # a kernel before 4.16
h0 = W.linux_link_reading(["eno1", "eno2", "eno3", "gone0"], _sys)
check(h0 == {"eno1": {"admin": "up", "link": "up", "parent": "", "host": True,
                      "ups": 1, "downs": 0},
             "eno2": {"admin": "down", "link": "down", "parent": "",
                      "host": True, "ups": 1, "downs": 0},
             "eno3": {"admin": "up", "link": "up", "parent": "", "host": True,
                      "ups": None, "downs": None}},
      f"linux_link_reading: IFF_UP, the carrier, the kernel's counts; a card "
      f"not there left out: {h0}")
card("eno1", ups=3, downs=2)
h1 = W.linux_link_reading(["eno1", "eno2", "eno3"], _sys)
ch = W.link_changes(h0, h1, restarted=True)
check(len(ch) == 1 and ch[0]["source"] == "host" and ch[0]["text"] ==
      "interface eno1 (held by Linux): link went down and came back between "
      "two readings (2 time(s)): the wire or the far end",
      f"a Linux card's flap, by the kernel's counts, across a dataplane "
      f"start too: {texts(ch)}")
card("eno1", carrier="0", ups=3, downs=3)
h2 = W.linux_link_reading(["eno1"], _sys)
st = W.link_standing(h2)
check([c["text"] for c in st] == ["interface eno1 (held by Linux): link "
                                  "down, administratively up: the wire or "
                                  "the far end"]
      and st[0]["source"] == "host",
      f"and a Linux card down stands as a port does: {st}")

check(W._sysfs_word(os.path.join(_sys, "eno1", "flags")) == "0x1003"
      and W._sysfs_word(os.path.join(_sys, "eno2", "carrier")) is None,
      "_sysfs_word: an attribute's word, None for one that cannot be read")
check(W._count("12") == 12 and W._count("\u00b2") is None
      and W._count(None) is None and W._count("-1") is None,
      "_count: ASCII digits only")
check(W._link_what("eno1", {"host": True}) == "interface eno1 (held by Linux)"
      and W._link_what("ge0.7", {"parent": "ge0"}) == "sub-interface ge0.7"
      and W._link_what("ge0", {"parent": ""}) == "interface ge0",
      "_link_what: a Linux card, a sub-interface, a port")
_a = {"ups": 1, "downs": 0}
check(W.link_transitions(_a, {"ups": 3, "downs": 2}) == (2, 2)
      and W.link_transitions(_a, {"ups": 0, "downs": 0}) is None
      and W.link_transitions(_a, {"ups": None, "downs": None}) is None
      and W.link_transitions(_a, {"ups": 3, "downs": 2}, True) is None
      and W.link_transitions(_a, {"ups": 3, "downs": 2, "host": True},
                             True) == (2, 2),
      "link_transitions: the moves; none backwards, unknown, or across a "
      "dataplane start for its own ports")

print("\n=== the Shell reads both, and the modules of Linux's cards ===")
os.environ["PRISMA_WATCH_NETDEVS"] = "eno1 GigabitEthernet0/9/0"
sh = prismash.Shell()
sh.SYS_CLASS_NET = _sys
sh.host_nics = lambda: [{"name": "eno2"}]
check(sh.watch_linux_cards({"GigabitEthernet0/9/0": {}}) == ["eno2", "eno1"],
      "watch_linux_cards: the PCI cards and the named netdevs, less the "
      "dataplane's")
check(sh.watch_linux_cards({"host-eno2": {}}) == ["eno1",
                                                  "GigabitEthernet0/9/0"],
      "and less a card the dataplane holds as host-<card>")
card("eno1", carrier="1", ups=3, downs=2)
sh.dp = Dp(dict(ANSWERS, **{"show bpa link-changes": LC.format(u=1, d=0)}))
sh.frr = frr()
r0 = sh.watch_read("link")
check(r0["GigabitEthernet0/8/0"]["ups"] == 1
      and (r0.get("eno1") or {}).get("host")
      and (r0.get("eno2") or {}).get("admin") == "down",
      f"watch_read link: the plugin's counts and Linux's cards: "
      f"{sorted(r0)}")
sh.dp.answers["show bpa link-changes"] = LC.format(u=2, d=1)
card("eno1", carrier="1", ups=4, downs=3)
ch = W.link_changes(r0, sh.watch_read("link"))
check(sorted(c["subject"] for c in ch) == ["GigabitEthernet0/8/0", "eno1"]
      and all("between two readings (1 time(s))" in c["text"] for c in ch),
      f"a flap of each between two of the watcher's readings: {texts(ch)}")
sh.dp.answers["show bpa link-changes"] = "show bpa: unknown input `link-changes'"
check(sh.watch_read("link")["GigabitEthernet0/8/0"]["ups"] is None,
      "an older plugin: the ports by state alone")

# A module on a card Linux holds, read in this process with the kernel's
# ioctl - a stand-in here. Mutation shown red: watch_read() reading the
# plugin's ports only (as before #588's last phase): eno1 never read.
_mod = {"eno1": F.sfp_low_rx(), "eno2": None}


def kioctl(dev, req):
    import struct as _st
    cmd = _st.unpack_from("=I", req)[0]
    have = _mod.get(dev, "virtio")
    if have == "virtio":
        raise OSError(95, "Operation not supported")
    if have is None:
        raise OSError(5, "Input/output error")    # an empty cage (ixgbe)
    if have == "gone":
        raise OSError(19, "No such device")
    if cmd == T.ETHTOOL_GMODULEINFO:
        return _st.pack("=III32x", cmd, 2, 512)
    _, _, off, n = _st.unpack_from("=IIII", req)
    return req[:16] + have[off:off + n]


sh.linux_module_ioctl = kioctl
os.environ["PRISMA_WATCH_NETDEVS"] = "eno1 veth9"
sh.dp = Dp(dict(ANSWERS))
_doors = []
prismash.doors.runner = lambda *a, **k: (
    lambda argv, timeout=30: _doors.append(argv) or (1, ""))
m0 = sh.watch_read("transceiver")
_e1, _e2 = m0.get("eno1") or {}, m0.get("eno2") or {"present": True}
check(_e1.get("present") and _e1.get("host") and _e1.get("rx")
      == "low-warning" and not _e2["present"] and "veth9" not in m0
      and m0["GigabitEthernet0/8/0"]["present"] and not _doors,
      f"watch_read transceiver: the Linux cards' modules beside the DPDK "
      f"port's, read here, no door; a virtio-like card left out: "
      f"{sorted(m0)}")
st = W.standing_changes("transceiver", m0)
check([c["text"] for c in st] == ["standing at start: eno1: rx power "
                                  "-19.00 dBm, below its low warning "
                                  "(-18.01 dBm)"]
      and st[0]["source"] == "host",
      f"what stands on a Linux card is said as on a dataplane port: "
      f"{[c['text'] for c in st]}")
_mod.update(eno1=None, eno2=F.sfp_internal())
m1 = sh.watch_read("transceiver")
ch = W.module_changes(m0, m1)
check(sorted(c["text"] for c in ch) == [
          "module inserted in eno2: Rei do SFP RSMPP8510GL-03D (serial "
          "FP23H150378)",
          "module removed from eno1 (it was Rei do SFP RSMPP8510GL-03D "
          "(serial FP23H150378))"]
      and all(c["source"] == "host" for c in ch),
      f"a module taken out of one Linux card and put in another: "
      f"{[c['text'] for c in ch]}")
_mod.update(eno1="gone")
m2 = sh.watch_read("transceiver")
check("eno1" not in m2 and W.module_changes(m1, m2) == [],
      "a card that left Linux (handed to the dataplane) is not a module "
      "removed")
# A virtio card, read every minute for a day: nothing, ever.
_mod.clear()
os.environ["PRISMA_WATCH_NETDEVS"] = "ens18"
sh.host_nics = lambda: []
vst, vev = {}, []
for i in range(3):
    ev, vst = W.tick(vst, {"transceiver": sh.watch_read("transceiver")},
                     {"dataplane": None, "routing": None, "boot": NOW - 900},
                     NOW + 60 * i)
    vev += ev
check(not [e for e in vev if "ens18" in e["text"]],
      f"a virtio card (EOPNOTSUPP) is quiet: {texts(vev)}")
os.environ.pop("PRISMA_WATCH_NETDEVS", None)

print("\n=== settings: applied and refused, by the commit's session ===")
if os.path.exists(os.environ["PRISMA_EVENTS"]):
    os.remove(os.environ["PRISMA_EVENTS"])
sh = prismash.Shell()
check(sh.record_settings("SNMP", True, "snmpd restarted"),
      "record_settings: a commit's apply is written")
check(sh.record_settings("API", False, "port 50051 is taken"),
      "record_settings: and a refusal")
sh.booting = True
check(sh.record_settings("SNMP", True, "") is False,
      "record_settings: the boot's apply of the same settings is not news")
check(sh.record_settings("SNMP", False, "snmpd did not start"),
      "record_settings: the boot's refusal is")
check(sh.record_settings("SNMP", True, "", retried=True),
      "record_settings: and the retry after the interfaces")
rec = events.read_events()
check([(e["text"], e["severity"], e["source"], e["subject"]) for e in rec] ==
      [("the SNMP settings were applied: snmpd restarted", "notice", "vty",
        "snmp"),
       ("the API settings were refused: port 50051 is taken", "warning",
        "vty", "api"),
       ("the SNMP settings were refused: snmpd did not start", "warning",
        "host", "snmp"),
       ("the SNMP settings were applied once the interfaces were up",
        "notice", "host", "snmp")],
      f"record_settings: {[e['text'] for e in rec]}")

print("\n=== the PPPoE control plane: its runs, and RADIUS (#597) ===")
RUNS = ("1 1000 start\n"
        "2 1100 exit 0 stopped (SIGTERM): a restart, an upgrade or no pppoe\n"
        "3 1102 start\n"
        "4 1200 exit 1 accel-pppd was killed by signal 9 (KILL)\n"
        "5 1201 start\n"
        "garbage line\n6 x start\n")
runs = W.parse_bng_runs(RUNS)
check([(r["n"], r["what"], r["code"]) for r in runs]
      == [(1, "start", 0), (2, "exit", 0), (3, "start", 0), (4, "exit", 1),
          (5, "start", 0)]
      and runs[3]["reason"] == "accel-pppd was killed by signal 9 (KILL)"
      and runs[3]["t"] == 1200,
      f"parse_bng_runs: starts and exits, a torn line passed over: {runs}")
check(W.bng_reading("") == {"runs": []} and W.bng_running(W.bng_reading(RUNS))
      and not W.bng_running(W.bng_reading("1 5 start\n2 6 exit 0 x\n"))
      and not W.bng_running(None),
      "bng_reading, bng_running: running when its last line is a start")
first = W.bng_reading("1 1000 start\n")
check(W._bng_new(first, W.bng_reading(RUNS)) == runs[1:]
      and W._bng_new({"runs": []}, first) == first["runs"]
      and W._bng_new(W.bng_reading(RUNS), first) == first["runs"],
      "_bng_new: past the last seen; all of it when the numbering went "
      "back (a new boot's /run)")
commit_asked = events.make_event("vty", "notice",
                                 "PPPoE's configuration committed",
                                 who="ana", origin="ssh 192.0.2.7", now=1099,
                                 kind="asked", cause="operator",
                                 affects="bng")
ch = W.bng_changes(first, W.bng_reading(RUNS), [commit_asked], boot=None)
check([(c["kind"], c.get("cause"), c["severity"]) for c in ch]
      == [("exit", None, "notice"), ("start", "operator", "notice"),
          ("exit", None, "warning"), ("start", "unexpected", "warning")],
      f"bng_changes: a commit's restart and a crash: "
      f"{[(c['kind'], c.get('cause'), c['severity']) for c in ch]}")
check(ch[1]["text"] == "the PPPoE control plane (prisma-bng) started again: "
      "PPPoE's configuration committed by ana from ssh 192.0.2.7"
      and ch[2]["text"] == "the PPPoE control plane (prisma-bng) exited with "
      "code 1: accel-pppd was killed by signal 9 (KILL)"
      and ch[3]["text"] == "the PPPoE control plane (prisma-bng) started "
      "again, by its restart policy, after it exited: accel-pppd was killed "
      "by signal 9 (KILL)" and ch[3]["t"] == 1201,
      f"bng_changes: the sentences, each with its own time: {texts(ch)}")
check(all(c["component"] == "bng" and c["subject"] == "prisma-bng"
          for c in ch) and ch[0]["reason"].startswith("stopped (SIGTERM)"),
      "bng_changes: about the container, the reason the entrypoint gave")
# One request is one start's cause: the crash's start, after the commit's,
# is not the commit's (cause_of's `after`). Mutation shown red: `after`
# dropped from bng_changes().
check(W.bng_changes(W.bng_reading("1 1000 start\n2 1100 exit 0 x\n"
                                  "3 1102 start\n"),
                    W.bng_reading(RUNS), [commit_asked])[-1]["cause"]
      == "unexpected", "bng_changes: an asked is one start's cause, not two")
killed = W.bng_changes(first, W.bng_reading("1 1000 start\n2 1300 start\n"))
check(killed[0]["severity"] == "warning" and "recorded no exit" in
      killed[0]["text"],
      f"bng_changes: a run with no exit recorded was killed from outside: "
      f"{texts(killed)}")
check(W.bng_changes({"runs": []}, first, [], boot=990)[0]["text"]
      == "the PPPoE control plane (prisma-bng) started with the boot"
      and W.bng_changes({"runs": []}, first, [])[0]["text"]
      == "the PPPoE control plane (prisma-bng) started",
      "bng_changes: the first start, the boot's or not")
upg = events.make_event("host", "notice", "PPPoE's files from an older "
                        "release (format 3) written again in format 4",
                        now=1300, kind="asked", cause="upgrade",
                        affects="bng")
up_ch = W.bng_changes(W.bng_reading(RUNS), W.bng_reading(
    RUNS + "7 1301 exit 0 stopped (SIGTERM): x\n8 1303 start\n"), [upg])
check([c.get("cause") for c in up_ch] == [None, "upgrade"]
      and "written again in format 4" in up_ch[1]["text"],
      f"bng_changes: a format upgrade's restart says so: {texts(up_ch)}")
check(W.bng_start_text("unexpected", "", None)
      == ("the PPPoE control plane (prisma-bng) started", "notice")
      and W.bng_start_text("unexpected", "", {"what": "exit", "code": 0,
                                              "reason": "x"})[1] == "notice",
      "bng_start_text: a start after a clean stop, nobody asking, is not a "
      "warning")

STAT = """uptime: 0.00:10:05
radius(1, 127.0.0.1):
  state: {s1}
  fail count: {f1}
  request count: 0
radius(2, 127.0.0.2):
  state: active
  fail count: 0
sessions:
  active: 3
"""


def rad(s1="active", f1=0, now=NOW):
    return W.radius_reading(STAT.format(s1=s1, f1=f1), now)


r0 = rad()
check(r0 == {"started": round(NOW - 605), "servers": {
    "127.0.0.1": {"state": "active", "fails": 0},
    "127.0.0.2": {"state": "active", "fails": 0}}},
      f"radius_reading: accel-pppd's start and each server: {r0}")
fl = W.radius_changes(r0, rad("failed", 1, NOW + 3))
check(len(fl) == 1 and fl[0]["severity"] == "warning"
      and fl[0]["text"].startswith("RADIUS server 127.0.0.1 failed")
      and fl[0]["subject"] == "127.0.0.1" and fl[0]["kind"] == "radius",
      f"radius_changes: active to failed: {texts(fl)}")
bk = W.radius_changes(rad("failed", 1), rad("active", 1, NOW + 1))
check(len(bk) == 1 and bk[0]["severity"] == "notice"
      and "active again" in bk[0]["text"],
      f"radius_changes: failed to active: {texts(bk)}")
bt = W.radius_changes(r0, rad("active", 2, NOW + 2))
check(len(bt) == 1 and "between two readings (2 time(s))" in bt[0]["text"],
      f"radius_changes: failed and back between two readings: {texts(bt)}")
check(W.radius_changes(rad("failed", 3), rad("active", 0, NOW + 400)) == []
      and W.radius_changes({"started": None, "servers": {}}, r0) == [],
      "radius_changes: across a start of accel-pppd, or from not running, "
      "a baseline")
check(texts(W.radius_standing(rad("failed", 1))) ==
      ["RADIUS server 127.0.0.1 is failed: it did not answer, and accel-ppp "
       "uses the others for its dead-time"]
      and W.radius_standing(r0) == [] and W.bng_standing({"runs": []}) == [],
      "radius_standing: a server failed when the watcher starts")
# The whole tick: a bng reading's changes become events the record takes,
# carrying their component and cause.
_st = {"seen": {"bng": first, "radius": r0}, "last": {}}
evs, _st2 = W.tick(_st, {"bng": W.bng_reading(RUNS),
                         "radius": rad("failed", 1, NOW)},
                   {"dataplane": None, "routing": None}, NOW,
                   record=[commit_asked])
check([(e["kind"], e.get("component"), e.get("cause")) for e in evs]
      == [("exit", "bng", None), ("start", "bng", "operator"),
          ("exit", "bng", None), ("start", "bng", "unexpected"),
          ("radius", None, None)]
      and all(events.event_error(e) is None for e in evs)
      and _st2["seen"]["bng"]["runs"][-1]["n"] == 5,
      f"tick: the runs and RADIUS, as events the record takes: "
      f"{[(e['kind'], e.get('component'), e.get('cause')) for e in evs]}")
check(events.make_event("host", "warning", "x", kind="exit",
                        component="bng") and
      events.event_error(events.make_event("host", "notice", "x",
                                           kind="exit", component="bng"))
      is None,
      "events: `exit` is a kind, `bng` a component")
# The Shell's readings, from a record on disk and a stand-in prisma-bng.
with open(os.environ["PRISMA_BNG_RUNS"], "w") as fh:
    fh.write(RUNS)
sh = prismash.Shell()
check(sh.watch_read("bng") == W.bng_reading(RUNS),
      "watch_read bng: the container's record, read off /run")
_ctl = os.path.join(_root, "prisma-bng")
with open(_ctl, "w") as fh:
    fh.write("#!/bin/sh\necho \"$@\" >> '%s.argv'\n"
             "[ \"$1\" = summary ] && cat <<'E'\n%s\nE\n" %
             (_ctl, STAT.format(s1="failed", f1=1)))
os.chmod(_ctl, 0o755)
_old_ctl, _old_conf = prismash.pppoe_mod.BNG_CTL, prismash.aaa_mod.read_conf
prismash.pppoe_mod.BNG_CTL = _ctl
prismash.aaa_mod.read_conf = lambda *a, **k: {"servers": []}
check(sh.watch_radius() == {"started": None, "servers": {}}
      and not os.path.exists(_ctl + ".argv"),
      "watch_radius: no RADIUS server configured, nothing asked")
prismash.aaa_mod.read_conf = lambda *a, **k: {"servers": [{"address": "x"}]}
got_r = sh.watch_radius()
check(got_r and got_r["servers"]["127.0.0.1"]["state"] == "failed"
      and open(_ctl + ".argv").read().split() == ["summary"],
      f"watch_radius: running and configured, accel-ppp's summary: {got_r}")
with open(os.environ["PRISMA_BNG_RUNS"], "w") as fh:
    fh.write("1 1000 start\n2 1001 exit 1 x\n")
os.remove(_ctl + ".argv")
check(sh.watch_radius() == {"started": None, "servers": {}}
      and not os.path.exists(_ctl + ".argv"),
      "watch_radius: a container whose last run exited is not asked")
prismash.pppoe_mod.BNG_CTL = "/proc/no/such/prisma-bng"
with open(os.environ["PRISMA_BNG_RUNS"], "w") as fh:
    fh.write("1 1000 start\n")
check(sh.watch_radius() is None,
      "watch_radius: a summary that cannot be had is no reading")
prismash.pppoe_mod.BNG_CTL, prismash.aaa_mod.read_conf = _old_ctl, _old_conf
os.remove(os.environ["PRISMA_BNG_RUNS"])

print("\n=== the PPPoE session trail's head, sent off the box (#597) ===")
# docs/bng.md 0.7: an event carrying the head is the copy off the box, by the
# record's own remote syslog and WatchEvents. Mutations shown red:
# trail_changes() without HEAD_EVERY (a head every reading), without the
# run (a restart waits an hour), without the closing heads (a month's end
# unsaid), and tick() passing the heads through flap_admit() (held).
SHA = ["%064x" % i for i in range(10)]


def thead(seq, sha, closed=()):
    return {"file": "pppoe-sessions.log", "seq": seq, "sha": sha,
            "closed": [{"file": f, "seq": n, "sha": h} for f, n, h in closed]}


ch, sd = W.trail_changes({}, {"file": "", "seq": 0, "sha": "", "closed": []},
                         NOW, run=1)
check(ch == [] and "seq" not in sd,
      "trail_changes: no trail - PPPoE never wrote one - says nothing")
ch, sd = W.trail_changes({}, thead(1234, SHA[1]), NOW, run=1)
check(texts(ch) == [f"the PPPoE session trail's head: seq 1234, sha256 "
                    f"{SHA[1]}, in pppoe-sessions.log; the first head said"]
      and ch[0]["severity"] == "info" and ch[0]["kind"] == "trail"
      and ch[0]["component"] == "bng" and ch[0]["key"] == "trail:head"
      and ch[0]["subject"] == "pppoe-sessions.log"
      and sd == {"seq": 1234, "sha": SHA[1], "file": "pppoe-sessions.log",
                 "t": NOW, "run": 1},
      f"trail_changes: the first head is said, and kept as said: {texts(ch)}")
first_sd = sd
ch, sd = W.trail_changes(first_sd, thead(1234, SHA[1]), NOW + 7200, run=2)
check(ch == [] and sd["t"] == NOW and sd["run"] == 2,
      "trail_changes: a head that has not moved is not said again - an hour "
      "on, or by a new run")
ch, sd = W.trail_changes(first_sd, thead(1251, SHA[2]),
                         NOW + W.HEAD_EVERY - 1, run=1)
check(ch == [] and sd["seq"] == 1234,
      "trail_changes: moved, but within HEAD_EVERY of the last one said: "
      "not yet")
ch, sd = W.trail_changes(first_sd, thead(1251, SHA[2]),
                         NOW + W.HEAD_EVERY, run=1)
check(texts(ch) == [f"the PPPoE session trail's head: seq 1251, sha256 "
                    f"{SHA[2]}, in pppoe-sessions.log; 17 entries since the "
                    f"last head said"]
      and sd["t"] == NOW + W.HEAD_EVERY and sd["seq"] == 1251,
      f"trail_changes: moved, and HEAD_EVERY since: said, with how far it "
      f"moved: {texts(ch)}")
ch, _ = W.trail_changes(first_sd, thead(1235, SHA[2]), NOW + 60, run=2)
check(len(ch) == 1 and "1 entry since the last head said" in ch[0]["text"],
      "trail_changes: a restart of the watcher (a new run) says a moved "
      "head at once, not an hour on")
ch, _ = W.trail_changes(first_sd, thead(1251, SHA[2]), NOW - 600, run=1)
check(len(ch) == 1, "trail_changes: a clock that went back says it")
ch, _ = W.trail_changes(first_sd, thead(3, SHA[3]), NOW + 60, run=2)
check(len(ch) == 1 and ch[0]["severity"] == "warning"
      and "the last head said was seq 1234: the chain went back" in
      ch[0]["text"],
      f"trail_changes: a head behind the last one said is a warning: "
      f"{texts(ch)}")
# The month turns between two readings: the file moved aside closed at
# 1260, the live one has the carries after it.
rot = thead(1263, SHA[5], [("pppoe-sessions.log.2026-10", 1260, SHA[4])])
ch, sd = W.trail_changes(first_sd, rot, NOW + 600, run=1)
check(texts(ch) == [f"the PPPoE session trail's file pppoe-sessions.log."
                    f"2026-10 closed: seq 1260, sha256 {SHA[4]}; 26 entries "
                    f"since the last head said"]
      and ch[0]["key"] == "trail:closed"
      and ch[0]["subject"] == "pppoe-sessions.log.2026-10"
      and sd["seq"] == 1260 and sd["t"] == NOW
      and sd["closed"] == "pppoe-sessions.log.2026-10",
      f"trail_changes: a file moved aside has its closing head said at "
      f"once; the live head waits its hour: {texts(ch)}")
ch2, sd2 = W.trail_changes(sd, rot, NOW + W.HEAD_EVERY, run=1)
check(texts(ch2) == [f"the PPPoE session trail's head: seq 1263, sha256 "
                     f"{SHA[5]}, in pppoe-sessions.log; 3 entries since the "
                     f"last head said"],
      "  and the hour on, the live head, counted from the closing one")
ch, _ = W.trail_changes(sd2, rot, NOW + 2 * W.HEAD_EVERY, run=1)
check(ch == [], "  and neither again")
ch, sd = W.trail_changes(first_sd, thead(1300, SHA[6], [
    ("pppoe-sessions.log.2026-10", 1234, SHA[1])]), NOW + W.HEAD_EVERY, run=1)
check([c["key"] for c in ch] == ["trail:head"],
      "trail_changes: a closing head that IS the last one said is not said "
      "twice")
two = thead(1400, SHA[9], [("pppoe-sessions.log.2026-09", 1234, SHA[1]),
                           ("pppoe-sessions.log.2026-10", 1300, SHA[7]),
                           ("pppoe-sessions.log.2026-11", 1350, SHA[8])])
ch, sd = W.trail_changes(dict(first_sd, closed="pppoe-sessions.log.2026-09"),
                         two, NOW + 60, run=1)
check([c["subject"] for c in ch] == ["pppoe-sessions.log.2026-10",
                                     "pppoe-sessions.log.2026-11"]
      and "50 entries since" in ch[1]["text"],
      "trail_changes: two months turned while the watcher was away: each "
      "closing head, in order")
ch, sd = W.trail_changes({}, two, NOW, run=1)
check([c["key"] for c in ch] == ["trail:head"]
      and sd["closed"] == "pppoe-sessions.log.2026-11",
      "trail_changes: the first reading takes the files already moved aside "
      "as known, and says the head")
check(W.trail_text({"file": "f", "seq": 5, "sha": "x"}, {})[0].endswith(
      "the first head said") and W.trail_text(
      {"file": "f", "seq": 5, "sha": "x"}, {"seq": 5}, closed=True)[1]
      == "warning",
      "trail_text: the first head; the same seq again is not the same chain")
# Through tick(): the heads are events the record takes, and never held by
# the flapping rule - five restarts of the watcher in a minute say five.
st, said5 = {}, []
for i in range(6):
    evs, st = W.tick(st, {"trail": thead(10 + i, SHA[i])},
                     {"dataplane": None, "routing": None}, NOW + i, run=i)
    said5 += evs
check(len(said5) == 6 and all(events.event_error(e) is None
                              for e in said5)
      and all(events.event_error(e, privileged=False) for e in said5)
      and said5[0]["kind"] == "trail" and said5[0]["component"] == "bng"
      and said5[0]["source"] == "host" and st["trail"]["seq"] == 15
      and "trail" not in st.get("seen", {}),
      f"tick: each head an event the record takes, no account may write, "
      f"not held: {len(said5)}")
evs, st2 = W.tick(st, {"trail": None}, {"dataplane": None, "routing": None},
                  NOW + 9, run=9)
check(evs == [] and st2["trail"] == st["trail"],
      "tick: a trail that could not be read is no reading")
# The Shell: the trail read as root off the host, and the loop - a pass says
# the head, the next pass (a restart) not again, and one after sessions did.
os.makedirs(os.path.dirname(os.environ["PRISMA_PPPOE_TRAIL"]), exist_ok=True)
import pppoetrail  # noqa: E402
_prev = b"\0" * 32
_seq = 0


def tput(body):
    global _prev, _seq
    import hashlib
    _seq += 1
    content = f"{body} seq {_seq}"
    _prev = hashlib.sha256(_prev + content.encode()).digest()
    with open(os.environ["PRISMA_PPPOE_TRAIL"], "a") as fh:
        fh.write(f"{content} sha256 {_prev.hex()}\n")


tput("2026-10-14T10:00:00.000Z restart closed 0")
tput("2026-10-14T10:00:01.000Z start session 01 user a mac - interface x "
     "address 100.64.0.1")
check(sh.watch_read("trail") == pppoetrail.head()
      and sh.watch_read("trail")["seq"] == 2,
      "watch_read trail: pppoetrail.head(), off the host")
for f in (os.environ["PRISMA_WATCH_STATE"], os.environ["PRISMA_EVENTS"]):
    if os.path.exists(f):
        os.remove(f)
sh = prismash.Shell()
sh.dp = Dp(dict(ANSWERS))
sh.frr = frr()
sh.watch_starts = lambda: {"dataplane": None, "routing": None}
with contextlib.redirect_stdout(io.StringIO()):
    p1 = sh.watch_events(once=True)
    p2 = sh.watch_events(once=True)
    tput("2026-10-14T10:05:00.000Z stop session 01 user a mac - interface x "
         "address 100.64.0.1 since 2026-10-14T10:00:01.000Z cause "
         "User-Request")
    p3 = sh.watch_events(once=True)
trec = [e for e in events.read_events() if e.get("kind") == "trail"]
check([e["kind"] for e in p1] == ["trail"] and p2 == []
      and [e["kind"] for e in p3] == ["trail"] and len(trec) == 2
      and f"seq 3, sha256 {_prev.hex()}" in trec[-1]["text"]
      and "1 entry since the last head said" in trec[-1]["text"]
      and W.read_state()["trail"]["seq"] == 3,
      f"watch_events: the first pass says the head, a restart with it "
      f"unmoved nothing, a restart after it moved says it: {texts(trec)}")
os.remove(os.environ["PRISMA_PPPOE_TRAIL"])

print("\n=== show log kind ===")
f, why = events.parse_show_args(["kind", "link"])
check(f == {"kind": "link", "last": events.SHOW_LAST} and why is None,
      "parse_show_args: kind <kind>")
f, why = events.parse_show_args(["kind", "nonsense"])
check(f is None and why.startswith("kind is one of"),
      f"parse_show_args: a kind the box does not have is refused: {why}")
check(events.select_events(rec + [events.make_event(
    "dataplane", "warning", "x", kind="link", now=NOW)], kind="settings")
      == rec, "select_events: by kind")
ev = events.make_event("dataplane", "notice", "x", kind="link",
                       subject="a" * 65)
check(events.event_error(ev) == "subject is one line of at most 64 "
      "characters", "event_error: a subject is short")
check(events.event_error(events.make_event("vty", "notice", "x",
                                           kind="link"), privileged=False)
      == "an account writes what it did, not what the box did",
      "event_error: no account writes that a link went down")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
