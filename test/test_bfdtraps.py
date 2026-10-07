#!/usr/bin/env python3
"""
src/prismash/bfdtraps.py and the watcher's Shell.watch_traps() (#559):
BFD-STD-MIB's bfdSessDown and bfdSessUp, from the watcher's own comparison
of bfdd's readings, handed to snmpd over AgentX.

Each behaviour, shown red by a mutation (docs/bfd.md lists them):

  * a session down, up, and down-and-back between two readings (bfdd's
    down counter) - mutation: transitions() without the counter;
  * the notification names the AGENT'S index, bfdd's discriminator, and
    carries the session's current diagnostic as IANAbfdDiagTC - mutation:
    the index taken from the reading's position;
  * bounded per session and never saying what the NMS was last told - a
    flapping session is held after TRAP_MAX, and told its state once the
    window lets it - mutations: plan() without the bound, plan() without
    the catching up;
  * a range of contiguous indexes is one notification (RFC 7331's SHOULD);
  * nothing is sent without a destination in snmpd.conf or without snmpd's
    AgentX socket, and the record is dropped then - mutation: can_send()
    true without a destination;
  * the AgentX PDUs, read back by an independent decoder here and by a
    fake master on a unix socket.

Every time here is pinned: no check compares two readings of the real clock.
"""
import contextlib
import io
import json
import os
import socket
import struct
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src", "prismash")
sys.path.insert(0, SRC)
sys.path.insert(0, HERE)

_root = tempfile.mkdtemp(prefix="bfdtraps-")
for k, v in (("PRISMA_CONFIG", "prisma-routing.conf"),
             ("PRISMA_CONFIRM", "confirm-pending"),
             ("PRISMA_ROLLBACK", "rollback"),
             ("PRISMA_EVENTS", "events.log"),
             ("PRISMA_EVENTS_STATE", "events.state"),
             ("PRISMA_WATCH_STATE", "events-watch.state"),
             ("PRISMA_DOORS", "no-doors.sock"),
             ("PRISMA_FRR_STARTED", "no-frr.pid"),
             ("PRISMA_SNMPD_CONF", "snmpd.conf"),
             ("PRISMA_AGENTX_SOCKET", "agentx.sock"),
             ("PRISMA_BFD_NOTIFY_STATE", "bfd-notifications")):
    os.environ[k] = os.path.join(_root, v)

import bfdtraps as T  # noqa: E402
import eventwatch as W  # noqa: E402

fail = 0


def check(c, msg):
    global fail
    print(("  [ ok ] " if c else "  FAIL: ") + msg)
    fail += 0 if c else 1


NOW = 1790836414.0
UP = T.SESS_UP
DOWN = T.SESS_DOWN
DIAG = T.SESS_DIAG


def sess(up, down=0, diag="ok", index=1, peer="10.0.0.2"):
    """One session of an eventwatch.bfd_reading(), built by hand."""
    return {"up": up, "down": down, "diag": diag, "peer": peer,
            "where": "vppwan0", "admin": False, "index": index}


def st(reading, traps=None, started=None):
    """A watcher state holding `reading` as what it saw of bfdd."""
    s = {"seen": {"bfd": reading}, "started": {"bfd": started}}
    if traps is not None:
        s["bfd_traps"] = traps
    return s


A = "10.0.0.2|10.0.0.1|vppwan0|default|False"
B = "10.0.0.3|10.0.0.1|vppwan0|default|False"

print("=== transitions(): what each session did between two readings ===")
check(T.transitions({A: sess(True)}, {A: sess(False, 1)}) == {A: ["down"]},
      "up to down is a down")
check(T.transitions({A: sess(False, 1)}, {A: sess(True, 1)}) == {A: ["up"]},
      "down to up is an up")
# Mutation shown red: transitions() without the counter - the flap two
# readings 10 s apart never see, at a 300 ms detect time.
check(T.transitions({A: sess(True, 1)}, {A: sess(True, 3)})
      == {A: ["down", "up"]},
      "up and up again, the down counter moved: a down and an up")
check(T.transitions({A: sess(False, 1)}, {A: sess(False, 2)})
      == {A: ["up", "down"]},
      "down and down again, the counter moved: it came up and went down")
check(T.transitions({A: sess(True, 4)}, {A: sess(True, 0)}, restarted=True)
      == {} and T.transitions({A: sess(True, 0)}, {A: sess(True, 2)},
                              restarted=True) == {},
      "after a restart of the routing daemons the counters are not compared")
check(T.transitions({}, {A: sess(True), B: sess(False, index=2)})
      == {A: ["up"]},
      "a session that appears up came up; one that appears down did nothing")
check(T.transitions({A: sess(True)}, {}) == {},
      "a session gone has no row left to name")
check(T.transitions({A: sess(False)}, {A: dict(sess(False), admin=True)})
      == {}, "down to administratively down is no change")

print("\n=== plan(): what one tick sends ===")
notes, rec = T.plan(st(None), st({A: sess(True)}), NOW)
check(notes == [] and rec == {A: {"sent": [], "told": "up"}},
      f"the first reading is told what it is, and sends nothing: {rec}")
notes, rec = T.plan(st({A: sess(True)}, rec),
                    st({A: sess(False, 1, "control detection time expired",
                                index=7)}, rec), NOW + 10)
# Mutation shown red: the index taken from the reading's position (1)
# rather than bfdd's discriminator the agent indexes bfdSessTable by.
check(notes == [("down", 7, 1)],
      f"a session down: bfdSessDown, bfdd's index, IANAbfdDiagTC 1: {notes}")
check(rec[A]["told"] == "down" and rec[A]["sent"] == [NOW + 10],
      "and it is recorded as told")
notes, rec = T.plan(st({A: sess(False, 1)}, rec),
                    st({A: sess(True, 1, index=7)}, rec), NOW + 20)
check(notes == [("up", 7, 0)], f"and up again: bfdSessUp: {notes}")
notes, rec2 = T.plan(st({A: sess(True, 1)}, rec),
                     st({A: sess(True, 2, "neighbor signaled session down",
                                 index=7)}, rec), NOW + 30)
check(notes == [("down", 7, 3), ("up", 7, 3)],
      f"down and back between readings: both, in order: {notes}")
check(T.plan(st({A: sess(True, 1)}, rec), st({A: sess(True, 1, index=7)},
                                             rec), NOW + 30)[0] == [],
      "nothing changed: nothing sent")

# The bound: TRAP_MAX per TRAP_WINDOW for one session.
rec = {A: {"sent": [], "told": "up"}}
sent, prev, up = [], sess(True, 0, index=7), True
for n in range(1, 8):
    up = not up
    cur = sess(up, prev["down"] + (0 if up else 1), index=7)
    notes, rec = T.plan(st({A: prev}, rec), st({A: cur}, rec), NOW + 10 * n)
    sent += notes
    prev = cur
# Mutation shown red: plan() without the bound - seven sent.
check(len(sent) == T.TRAP_MAX,
      f"a session changing 7 times in 70 s: {T.TRAP_MAX} sent, the rest "
      f"held: {len(sent)}")
check(rec[A]["told"] == "up" and prev["up"] is False,
      "and the NMS was last told up while the session is down")
notes, rec = T.plan(st({A: prev}, rec), st({A: prev}, rec), NOW + 100)
check(notes == [], "within the window the hold stays")
notes, rec = T.plan(st({A: prev}, rec), st({A: prev}, rec),
                    NOW + 10 + T.TRAP_WINDOW)
# Mutation shown red: plan() without the catching up - the NMS would
# believe up for ever.
check(notes == [("down", 7, 0)],
      f"once the window lets it, the session is told its state: {notes}")
notes, rec = T.plan(st({A: prev}, rec), st({A: prev}, rec),
                    NOW + 20 + T.TRAP_WINDOW)
check(notes == [], "and only once")
# De-duplicated: a change to what the NMS was last told is not said again.
rec = {A: {"sent": [], "told": "down"}}
notes, _ = T.plan(st({A: sess(True, 1)}, rec),
                  st({A: sess(True, 2, index=7)}, rec), NOW)
check(notes == [("up", 7, 0)],
      f"a flap of a session the NMS believes down sends only the up: {notes}")
notes, rec = T.plan(st({A: sess(True), B: sess(True, index=2)},
                       {A: {"sent": [], "told": "up"},
                        B: {"sent": [], "told": "up"}}),
                    st({A: sess(True)}, {A: {"sent": [], "told": "up"},
                                         B: {"sent": [], "told": "up"}}),
                    NOW)
check(notes == [] and set(rec) == {A}, "a session gone leaves the record")
notes, rec = T.plan(st({A: sess(True, 3)}, started=NOW - 600),
                    st({A: sess(False, 0, index=40)}, {A: {"sent": [],
                                                           "told": "up"}},
                       started=NOW - 5), NOW)
check(notes == [("down", 40, 0)],
      f"after a restart of bfdd: down under its new index: {notes}")
check(T.plan(st({A: sess(True)}), st({A: sess(False, 1, index=None)},
                                     {A: {"sent": [], "told": "up"}}),
             NOW)[0] == [],
      "a session bfdd gave no id is not named by a guessed one")
check(T.plan(st({A: sess(True)}), {"seen": {}}, NOW) == ([], {}),
      "no reading of bfdd: nothing")
notes, _ = T.plan(st({A: sess(True)}),
                  st({A: sess(True, 0, index=3)},
                     {A: {"sent": [], "told": None}}), NOW)
check(notes == [("up", 3, 0)],
      "a session never told (snmpd took nothing last time) is told its state")

print("\n=== ranges(): the notifications, RFC 7331's varbinds ===")
one = T.ranges([("down", 7, 1)])
check(one == [[(T.SNMP_TRAP_OID, T.AX_OID, DOWN), (f"{DIAG}.7", T.AX_INTEGER,
                                                    1),
               (f"{DIAG}.7", T.AX_INTEGER, 1)]],
      "one session: snmpTrapOID.0, then bfdSessDiag.7 twice")
many = T.ranges([("down", 5, 1), ("down", 3, 1), ("down", 4, 3),
                 ("down", 9, 1), ("up", 2, 0)])
check([[v[0] for v in n] for n in many] ==
      [[T.SNMP_TRAP_OID, f"{DIAG}.3", f"{DIAG}.5"],
       [T.SNMP_TRAP_OID, f"{DIAG}.9", f"{DIAG}.9"],
       [T.SNMP_TRAP_OID, f"{DIAG}.2", f"{DIAG}.2"]],
      "3, 4 and 5 down together are one range; 9 is its own; the up apart")
check(T._notification("up", [(2, 0)])[0][2] == UP and UP.endswith(".0.1")
      and DOWN.endswith(".0.2"),
      "_notification: bfdSessUp is { bfdNotifications 1 }, Down 2 (RFC 7331)")

print("\n=== whether they are sent ===")
check(T.destinations("trap2sink 10.0.0.9 c\ninformsink udp6:[2001:db8::9]:162"
                     " c\n# trap2sink no\n\n") == 2,
      "destinations: trap2sink and informsink lines")
conf, sock = os.environ["PRISMA_SNMPD_CONF"], os.environ["PRISMA_AGENTX_SOCKET"]
check(T.can_send() == (False, 0), "can_send: no snmpd.conf, nothing sent")
with open(conf, "w") as fh:
    fh.write("agentAddress udp:127.0.0.1:161\nmaster agentx\n")
open(sock, "w").close()
# Mutation shown red: can_send() true without a destination.
check(T.can_send() == (False, 0),
      "can_send: snmpd with no destination sends nothing")
with open(conf, "a") as fh:
    fh.write("trap2sink 10.0.0.9 s3cret\n")
check(T.can_send() == (True, 1), "can_send: a destination and the socket")
os.remove(sock)
check(T.can_send() == (False, 1), "can_send: no AgentX socket, no snmpd")
check(T.state_text(True, NOW, 42, 1) ==
      f"sending yes\nat {int(NOW)}\npid 42\ndestinations 1\n",
      "state_text: the agent's `key value` lines")
check(T.write_state(True, NOW, 1) and open(os.environ[
    "PRISMA_BFD_NOTIFY_STATE"]).read().startswith("sending yes\n")
      and oct(os.stat(os.environ["PRISMA_BFD_NOTIFY_STATE"]).st_mode)[-3:]
      == "644", "write_state: written whole, 0644, for Debian-snmp")
check(T.write_state(True, NOW, 1, "/proc/no/such/file") is False,
      "write_state: a path it cannot write is False, not a raise")
check("s3cret" not in open(os.environ["PRISMA_BFD_NOTIFY_STATE"]).read(),
      "and nothing of the community in it")

print("\n=== AgentX, RFC 2741, read back by a decoder of this test's ===")


def dec_oid(b, at):
    n, prefix, include, _ = struct.unpack(">BBBB", b[at:at + 4])
    subs = list(struct.unpack(f">{n}I", b[at + 4:at + 4 + 4 * n]))
    if prefix:
        subs = [1, 3, 6, 1, prefix] + subs
    return ".".join(map(str, subs)), at + 4 + 4 * n


def dec_varbinds(b, at):
    out = []
    while at < len(b):
        typ, _ = struct.unpack(">HH", b[at:at + 4])
        name, at = dec_oid(b, at + 4)
        if typ == 2:
            val = struct.unpack(">i", b[at:at + 4])[0]
            at += 4
        else:
            val, at = dec_oid(b, at)
        out.append((name, typ, val))
    return out


check(T.ax_oid("1.3.6.1.2.1.222.0.2") ==
      struct.pack(">BBBB", 9, 0, 0, 0) + struct.pack(">9I", 1, 3, 6, 1, 2, 1,
                                                     222, 0, 2),
      "ax_oid: nine sub-identifiers, no prefix")
check(T.ax_oid("") == b"\0\0\0\0", "ax_oid: the null OID")
check(T.ax_octets(b"abcde") == struct.pack(">I", 5) + b"abcde\0\0\0",
      "ax_octets: padded to four")
check(dec_varbinds(T.ax_varbind(f"{DIAG}.7", T.AX_INTEGER, 1)
                   + T.ax_varbind(T.SNMP_TRAP_OID, T.AX_OID, DOWN), 0)
      == [(f"{DIAG}.7", 2, 1), (T.SNMP_TRAP_OID, 6, DOWN)],
      "ax_varbind: an INTEGER and an OBJECT IDENTIFIER")
try:
    T.ax_varbind("1.2", 4, b"x")
    check(False, "ax_varbind: a type it does not send is refused")
except ValueError:
    check(True, "ax_varbind: a type it does not send is refused")
pdu = T.ax_pdu(T.AX_NOTIFY, 9, 3, b"abcd")
check(struct.unpack(">BBBBIIII", pdu[:20]) == (1, 12, 0x10, 0, 9, 0, 3, 4)
      and pdu[20:] == b"abcd", "ax_pdu: version 1, network byte order")
op = T.ax_open(1)
check(op[1] == T.AX_OPEN and struct.unpack(">I", op[16:20])[0] == len(op) - 20
      and b"prisma-routing bfd notifications" in op,
      "ax_open: an Open-PDU naming what it is")
nt = T.ax_notify(9, 2, one[0])
check(nt[1] == T.AX_NOTIFY and struct.unpack(">I", nt[4:8])[0] == 9
      and dec_varbinds(nt, 20) == [(T.SNMP_TRAP_OID, 6, DOWN),
                                   (f"{DIAG}.7", 2, 1), (f"{DIAG}.7", 2, 1)],
      "ax_notify: on the session, snmpTrapOID.0 first")
cl = T.ax_close(9, 4)
check(cl[1] == T.AX_CLOSE and cl[20] == 5, "ax_close: reasonShutdown")
resp = struct.pack(">BBBBIIII", 1, 18, 0x10, 0, 77, 0, 1, 8) + \
    struct.pack(">IHH", 0, 0, 0)
check(T.ax_response(resp) == (77, 1, 0), "ax_response: session and error")
check(T.ax_response(struct.pack("<BBBBIIII", 1, 18, 0, 0, 77, 0, 1, 8)
                    + struct.pack("<IHH", 0, 262, 0)) == (77, 1, 262),
      "ax_response: a master in its own byte order is read in it")
check(T.ax_response(b"short") is None and T.ax_response(nt) is None,
      "ax_response: what is not a response is None")
a, b = socket.socketpair()
a.sendall(resp + b"extra")
check(T._recv_pdu(b) == resp, "_recv_pdu: one PDU, what follows left")
a.close()
b.close()


class Master(threading.Thread):
    """A fake AgentX master: answers every PDU, records what came."""

    def __init__(self, path, refuse_notify=False):
        super().__init__(daemon=True)
        self.path, self.refuse, self.got = path, refuse_notify, []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(path)
        self.srv.listen(1)

    def run(self):
        c, _ = self.srv.accept()
        while True:
            pdu = T._recv_pdu(c)
            if len(pdu) < 20:
                break
            self.got.append(pdu)
            err = 266 if self.refuse and pdu[1] == T.AX_NOTIFY else 0
            c.sendall(struct.pack(">BBBBIIII", 1, 18, 0x10, 0, 77, 0,
                                  struct.unpack(">I", pdu[12:16])[0], 8)
                      + struct.pack(">IHH", 0, err, 0))
            if pdu[1] == T.AX_CLOSE:
                break
        c.close()
        self.srv.close()


m = Master(os.path.join(_root, "m1.sock"))
m.start()
n = T.agentx_send(many, os.path.join(_root, "m1.sock"))
m.join(5)
check(n == 3 and [p[1] for p in m.got] == [1, 12, 12, 12, 2]
      and all(struct.unpack(">I", p[4:8])[0] == 77 for p in m.got[1:]),
      f"agentx_send: open, three notifies on the master's session, close: "
      f"{n} {[p[1] for p in m.got]}")
m = Master(os.path.join(_root, "m2.sock"), refuse_notify=True)
m.start()
check(T.agentx_send(one, os.path.join(_root, "m2.sock")) == 0,
      "agentx_send: a notify the master refused is not counted")
m.join(5)
check(T.agentx_send(one, os.path.join(_root, "nobody.sock")) == 0,
      "agentx_send: no master, nothing sent, no raise")

print("\n=== the agent's numbers are these ===")
import importlib.machinery  # noqa: E402
import importlib.util  # noqa: E402
_p = os.path.join(HERE, "..", "appliance", "prisma-routing-snmp-agent")
_spec = importlib.util.spec_from_loader(
    "agent_for_bfdtraps", importlib.machinery.SourceFileLoader(
        "agent_for_bfdtraps", _p))
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)
check(T.DIAG == agent.BFD_DIAG,
      "IANAbfdDiagTC: the agent's bfdSessDiag and the notification's agree")
rows = agent.bfd_rows([{"id": 7, "status": "down", "peer": "10.0.0.2",
                        "local": "10.0.0.1",
                        "diagnostic": "control detection time expired"}])
check((f"{DIAG}.7", "integer", 1) in rows,
      "the agent serves bfdSessDiag.7 = 1, the instance a bfdSessDown names")
check(os.path.basename(T.STATE_PATH) == agent.BFD_NOTIFY_FILE,
      "the file the watcher writes is the one the agent reads")
import system  # noqa: E402
_text = open(os.path.join(SRC, "bfdtraps.py")).read()
check(f'"PRISMA_AGENTX_SOCKET", "{system.AGENTX_SOCKET}"' in _text,
      "snmpd's AgentX socket is the one snmpd.conf names (system.py)")
check(f'"PRISMA_SNMPD_CONF", "{system.SNMPD_CONF}"' in _text,
      "and its configuration the one the VTY writes")

print("\n=== bfd_reading(): bfdd's id beside each session ===")
import opshow  # noqa: E402
import prismash  # noqa: E402
peers = [{"peer": "10.0.0.2", "local": "10.0.0.1", "vrf": "default",
          "interface": "vppwan0", "id": 3121, "status": "up",
          "diagnostic": "ok", "multihop": False},
         {"peer": "10.0.0.3", "local": "10.0.0.1", "vrf": "default",
          "interface": "vppwan0", "id": 15, "status": "down",
          "diagnostic": "ok", "multihop": False}]
facts = {"answered": True, "garbled": False,
         "sessions": prismash.bfd_sessions(peers, [])}
r = W.bfd_reading(opshow.bfd_fields(facts),
                  [s["id"] for s in facts["sessions"]])
check({v["index"] for v in r.values()} == {15, 3121}
      and r[A]["index"] == 3121,
      f"each session carries bfdd's own id: {[v['index'] for v in r.values()]}")
check(all(v["index"] is None for v in
          W.bfd_reading(opshow.bfd_fields(facts)).values()),
      "and none when it was not given")

print("\n=== Shell.watch_traps(): the watcher's tick ===")
sh = prismash.Shell()
before = st({A: sess(True, index=7)}, {A: {"sent": [], "told": "up"}})
after = st({A: sess(False, 1, "control detection time expired", index=7)},
           {A: {"sent": [], "told": "up"}})
os.remove(conf)
handed = []
out = sh.watch_traps(before, after, NOW, send=lambda n: handed.append(n) or 1)
check(handed == [] and "bfd_traps" not in out,
      "no destination: nothing handed to snmpd, and the record dropped")
check(open(os.environ["PRISMA_BFD_NOTIFY_STATE"]).read().startswith(
    "sending no\n"), "and the agent's file says it is not sending")
with open(conf, "w") as fh:
    fh.write("trap2sink 10.0.0.9 c\n")
open(sock, "w").close()
out = sh.watch_traps(before, after, NOW, send=lambda n: handed.append(n)
                     or len(n))
check(handed == [T.ranges([("down", 7, 1)])] and out["bfd_traps"][A]["told"]
      == "down", f"a destination: bfdSessDown handed to snmpd: {handed}")
check(open(os.environ["PRISMA_BFD_NOTIFY_STATE"]).read().startswith(
    "sending yes\n"), "and the agent's file says it is sending")
check(after["bfd_traps"][A]["told"] == "up",
      "watch_traps: `after` is not changed under the caller")
_err = io.StringIO()
with contextlib.redirect_stderr(_err):
    out = sh.watch_traps(before, after, NOW, send=lambda n: 0)
check(out["bfd_traps"][A]["told"] == "up" and "took 0 of 1" in
      _err.getvalue(), "snmpd took nothing: not recorded as told, and said")
with contextlib.redirect_stderr(io.StringIO()):
    _o = sh.watch_traps(st({A: sess(True, index=7)}, {A: {"sent": [],
                                                          "told": "up"}}),
                        st({A: sess(False, 1, index=7),
                            B: sess(False, index=9)},
                           {A: {"sent": [], "told": "up"}}),
                        NOW, send=lambda n: 0)
check(_o["bfd_traps"][B] == {"sent": [], "told": "down"},
      "and a session only recorded that tick, nothing sent for it, keeps "
      "its record")
out2 = sh.watch_traps(after, dict(after, bfd_traps=out["bfd_traps"]),
                      NOW + 10, send=lambda n: handed.append(n) or len(n))
check(handed[-1] == T.ranges([("down", 7, 1)]),
      "so the next tick tells it")
_err = io.StringIO()
with contextlib.redirect_stderr(_err):
    sh.watch_traps(before, after, NOW, send=lambda n: 1 / 0)
    sh.watch_traps(before, after, NOW, send=lambda n: 1 / 0)
check(_err.getvalue().count("BFD notifications failed") == 1,
      "a failure is no notification, said once, and the watcher goes on")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
