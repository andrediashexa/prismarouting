"""
BFD-STD-MIB's notifications (RFC 7331), bfdSessUp and bfdSessDown, sent by
the host's watcher of events (#559): the BFD half of the issue's "a trap on
session down/up".

WHO SENDS THEM, AND WHY THIS PROCESS. FRR 10.7.1 ships no bfdd_snmp, so
bfdd raises no notification of its own, and the agent that serves the table
(appliance/prisma-routing-snmp-agent --bfd) is a pass_persist, which answers
polls and cannot raise one. prisma-routing-events-watch.service (#588 phase
2) already reads bfdd's sessions every eventwatch.TICK, as root, on the host,
and compares each reading with the one before - a state change, or a down
counter that moved between two readings. The notifications are that same
comparison said to the NMS, so they are sent from there and not from a
unit of their own: one reader of bfdd, not two that could disagree.

HOW THEY ARE SENT: AS AN AgentX SUBAGENT, TO snmpd. docs/bfd.md designed
them for net-snmp's `snmptrap` / `snmpinform`, which are in Debian's `snmp`
package: the medium installs `snmpd` and not `snmp`
(appliance/build-iso.sh), so the box has no snmptrap, and adding it is a
medium change. snmpd is already the AgentX master FRR's daemons send their
BGP and OSPF notifications through (#502), and it already holds the
destinations `snmp trap-host` writes (trap2sink / informsink, #507). An
AgentX Notify-PDU (RFC 2741 6.2.10) hands it the notification and snmpd
sends it to each destination the way that destination asked - a trap, or an
inform it resends until acknowledged - so this process never reads a
community and never retries a datagram.

WHAT IS IN ONE. RFC 7331's OBJECTS clause: bfdSessDiag twice, the low and
the high index of a range of sessions that changed together, with their
CURRENT diagnostics (erratum 8327, verified in 2025: the RFC's "set equal to
this new state" put a state in a diagnostic). The index is bfdd's local
discriminator, which is what the agent indexes bfdSessTable by - the same
number, so an NMS reads the row the notification names.

BOUNDED PER SESSION, AND NOT SAID TWICE (docs/snmp-plan.md: "every one
rate-limited and de-duplicated"). Each session gets eventwatch.FLAP_EVENTS
notifications per eventwatch.FLAP_WINDOW, the bound the record keeps for
the same session; a notification saying what the NMS was last told is not
sent; and a session whose bound held a change back is told its state once
the window lets it, so the NMS never ends a flap believing the wrong one.

bfdNotificationsEnable. RFC 7331: true enables the notifications. The agent
serves it true when, and only when, they are being sent: this process
writes STATE_PATH every tick - whether snmpd holds a destination and its
AgentX socket is there, and its own pid - and the agent (Debian-snmp, who
cannot read snmpd.conf) reads it, true only from a running pid and a
fresh file.
"""
import os
import socket
import struct

import events
import eventwatch

BFD_MIB = "1.3.6.1.2.1.222"
#: RFC 7331 section 5: bfdNotifications is { bfdMIB 0 }, bfdSessUp 1 and
#: bfdSessDown 2.
SESS_UP = BFD_MIB + ".0.1"
SESS_DOWN = BFD_MIB + ".0.2"
#: bfdSessDiag, { bfdSessEntry 13 }: the column the agent serves (col 13).
SESS_DIAG = BFD_MIB + ".1.2.1.13"
#: SNMPv2-MIB's snmpTrapOID.0, the notification's own name.
SNMP_TRAP_OID = "1.3.6.1.6.3.1.1.4.1.0"
#: IANA-BFD-TC-STD-MIB's IANAbfdDiagTC from bfdd's words, as the agent's
#: BFD_DIAG serves bfdSessDiag: held equal by test/test_bfdtraps.py.
DIAG = {"ok": 0, "control detection time expired": 1,
        "echo function failed": 2, "neighbor signaled session down": 3,
        "forwarding plane reset": 4, "path down": 5,
        "concatenated path down": 6, "administratively down": 7,
        "reverse concatenated path down": 8}

#: snmpd's AgentX master socket (system.AGENTX_SOCKET) and its
#: configuration (system.SNMPD_CONF), both the host's and both root's.
AGENTX_SOCKET = os.environ.get("PRISMA_AGENTX_SOCKET", "/var/agentx/master")
SNMPD_CONF = os.environ.get("PRISMA_SNMPD_CONF", "/etc/snmp/snmpd.conf")
#: What the agent reads bfdNotificationsEnable from: a few lines, nothing
#: secret, 0644 in the directory the agent's other state files are in.
STATE_PATH = os.environ.get("PRISMA_BFD_NOTIFY_STATE",
                            "/run/prisma-routing/state/bfd-notifications")
#: Notifications per session, at most, in a window: the record's own bound
#: for one subject (eventwatch.flap_admit()).
TRAP_MAX = eventwatch.FLAP_EVENTS
TRAP_WINDOW = eventwatch.FLAP_WINDOW
#: Seconds snmpd is given to answer each AgentX PDU.
AGENTX_TIMEOUT = 5

# RFC 2741 section 6.1: the PDU types used here, the header's flag saying
# the integers are in network byte order, and the varbind types.
AX_OPEN, AX_CLOSE, AX_NOTIFY, AX_RESPONSE = 1, 2, 12, 18
AX_NETWORK_BYTE_ORDER = 0x10
AX_INTEGER, AX_OID = 2, 6


# -------------------------------------------------------- what is sent

def transitions(prev, cur, restarted=False):
    """
    What each session did between two eventwatch.bfd_reading()s, as
    {key: ["down", "up", ...]} in the order it happened. A state change is
    one; a down counter that moved with the state unchanged is two - up and
    up again is a down and an up the readings never saw, down and down
    again an up and a down. Down is RFC 7331's down(2) or adminDown(1),
    anything not up. A session that appears up came up; one that goes away
    has no row left to name. After a restart of the routing daemons the
    counters start again and are not compared (eventwatch.bfd_changes()).
    """
    out = {}
    for key in sorted(cur):
        c, p = cur[key], prev.get(key)
        if p is None:
            if c["up"]:
                out[key] = ["up"]
            continue
        moved = not restarted and c["down"] > p["down"]
        if p["up"] and not c["up"]:
            out[key] = ["down"]
        elif c["up"] and not p["up"]:
            out[key] = ["up"]
        elif moved:
            out[key] = ["down", "up"] if c["up"] else ["up", "down"]
    return out


def plan(before, after, now):
    """
    The notifications one tick of the watcher sends: (notes, record).
    `before` and `after` are the watcher's state either side of
    eventwatch.tick() - their `seen` bfd readings are what was compared -
    and after["bfd_traps"] is the record of what each session was told
    ({key: {"sent": [times], "told": "up"|"down"}}), returned updated.
    A note is (state, index, diagnostic): bfdd's discriminator and
    IANAbfdDiagTC.

    A session with no record and no change is recorded as told what it is,
    unsent: the watcher's first reading, a destination just configured, a
    session that just appeared down. Then, per session, its changes and -
    when nothing changed and it was told otherwise - its state, each sent
    only if it is not what it was last told and only while fewer than
    TRAP_MAX went in the last TRAP_WINDOW.
    """
    prev = (before.get("seen") or {}).get("bfd")
    cur = (after.get("seen") or {}).get("bfd")
    record = {k: dict(v) for k, v in (after.get("bfd_traps") or {}).items()}
    if cur is None:
        return [], record
    s0 = (before.get("started") or {}).get("bfd")
    s1 = (after.get("started") or {}).get("bfd")
    restarted = s0 is not None and s1 is not None and \
        abs(s1 - s0) > events.START_SLACK
    moved = transitions(prev, cur, restarted) \
        if prev is not None and prev != cur else {}
    notes = []
    for key in list(record):
        if key not in cur:
            del record[key]
    for key in sorted(cur):
        c = cur[key]
        state = "up" if c["up"] else "down"
        r = record.get(key)
        if r is None and key not in moved:
            record[key] = {"sent": [], "told": state}
            continue
        r = r or {"sent": [], "told": None}
        r["sent"] = [t for t in r["sent"] if 0 <= now - t < TRAP_WINDOW]
        index = c.get("index")
        for st in moved.get(key) or [state]:
            if st == r["told"]:
                continue
            if len(r["sent"]) >= TRAP_MAX or not isinstance(index, int):
                break
            r["sent"].append(now)
            r["told"] = st
            notes.append((st, index, DIAG.get(str(c["diag"]).lower(), 0)))
        record[key] = r
    return notes, record


def ranges(notes):
    """
    The notes of one tick as notifications, each a list of varbinds
    (oid, AgentX type, value): snmpTrapOID.0, then bfdSessDiag of the
    lowest and of the highest index of a run of contiguous indexes that
    made the same change. RFC 7331: a range SHOULD be one notification,
    and a single session's two bfdSessDiag MUST be the same instance.
    """
    out = []
    for st in ("down", "up"):
        run = []
        for _, index, diag in sorted((n for n in notes if n[0] == st),
                                     key=lambda n: n[1]):
            if run and index != run[-1][0] + 1:
                out.append(_notification(st, run))
                run = []
            run.append((index, diag))
        if run:
            out.append(_notification(st, run))
    return out


def _notification(state, run):
    """One bfdSessUp or bfdSessDown for a run of (index, diagnostic)."""
    (lo, dlo), (hi, dhi) = run[0], run[-1]
    return [(SNMP_TRAP_OID, AX_OID, SESS_UP if state == "up" else SESS_DOWN),
            (f"{SESS_DIAG}.{lo}", AX_INTEGER, dlo),
            (f"{SESS_DIAG}.{hi}", AX_INTEGER, dhi)]


# ------------------------------------------------- whether they are sent

def destinations(text):
    """How many notification destinations an snmpd.conf names."""
    return sum(1 for line in (text or "").splitlines()
               if line.split()[:1] in (["trap2sink"], ["informsink"]))


def can_send(conf=None, sock=None):
    """
    (sending, destinations): whether a notification handed to snmpd now
    reaches anybody - snmpd holds a destination and its AgentX socket is
    there. The configuration is root's (0600), which this process is.
    """
    try:
        with open(conf or SNMPD_CONF) as fh:
            n = destinations(fh.read())
    except OSError:
        n = 0
    return n > 0 and os.path.exists(sock or AGENTX_SOCKET), n


def state_text(sending, now, pid, n):
    """STATE_PATH's lines: `key value`, as the agent's parse_state_file()
    reads every file of that directory."""
    return (f"sending {'yes' if sending else 'no'}\nat {int(now)}\n"
            f"pid {pid}\ndestinations {n}\n")


def write_state(sending, now, n, path=None):
    """STATE_PATH, whole and by rename, 0644. False when it could not be."""
    path = path or STATE_PATH
    tmp = path + ".new"
    try:
        with open(tmp, "w") as fh:
            fh.write(state_text(sending, now, os.getpid(), n))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except OSError:
        return False
    return True


# ------------------------------------------------------- AgentX, RFC 2741

def ax_oid(oid):
    """An OID as AgentX encodes one (6.2.1): no prefix, not `include`."""
    subs = [int(x) for x in oid.strip(".").split(".")] if oid else []
    return struct.pack(">BBBB", len(subs), 0, 0, 0) + \
        b"".join(struct.pack(">I", s) for s in subs)


def ax_octets(data):
    """An Octet String (6.2.3): its length, then itself padded to 4."""
    return struct.pack(">I", len(data)) + data + b"\0" * (-len(data) % 4)


def ax_varbind(oid, typ, value):
    """One VarBind (6.2.2): an INTEGER or an OBJECT IDENTIFIER here."""
    head = struct.pack(">HH", typ, 0) + ax_oid(oid)
    if typ == AX_INTEGER:
        return head + struct.pack(">i", value)
    if typ == AX_OID:
        return head + ax_oid(value)
    raise ValueError(f"varbind type {typ} is not sent here")


def ax_pdu(kind, session, packet, payload):
    """A PDU: the 20-byte header (6.1), network byte order, and payload."""
    return struct.pack(">BBBBIIII", 1, kind, AX_NETWORK_BYTE_ORDER, 0,
                       session, 0, packet, len(payload)) + payload


def ax_open(packet, descr="prisma-routing bfd notifications"):
    """An Open-PDU (6.2.1): a timeout of the master's, no subagent id."""
    return ax_pdu(AX_OPEN, 0, packet, struct.pack(">BBBB", 0, 0, 0, 0)
                  + ax_oid("") + ax_octets(descr.encode()))


def ax_notify(session, packet, varbinds):
    """A Notify-PDU (6.2.10) of `varbinds`, snmpTrapOID.0 first."""
    return ax_pdu(AX_NOTIFY, session, packet,
                  b"".join(ax_varbind(*v) for v in varbinds))


def ax_close(session, packet):
    """A Close-PDU (6.2.2), reasonShutdown."""
    return ax_pdu(AX_CLOSE, session, packet, struct.pack(">BBBB", 5, 0, 0, 0))


def ax_response(data):
    """(session, packet, error) of a Response-PDU (6.2.16), or None."""
    if len(data) < 28:
        return None
    order = ">" if data[2] & AX_NETWORK_BYTE_ORDER else "<"
    _, kind, _, _, session, _, packet, _ = struct.unpack(order + "BBBBIIII",
                                                         data[:20])
    if kind != AX_RESPONSE:
        return None
    return session, packet, struct.unpack(order + "H", data[24:26])[0]


def _recv_pdu(s):
    """One whole PDU off the socket: its header, then what it says follows."""
    buf = b""
    while len(buf) < 20:
        chunk = s.recv(20 - len(buf))
        if not chunk:
            return buf
        buf += chunk
    order = ">" if buf[2] & AX_NETWORK_BYTE_ORDER else "<"
    need = struct.unpack(order + "I", buf[16:20])[0]
    while len(buf) < 20 + need:
        chunk = s.recv(20 + need - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


def agentx_send(notifications, path=None, timeout=AGENTX_TIMEOUT):
    """
    Each notification (ranges()) handed to snmpd as a Notify-PDU on one
    AgentX session: opened, used and closed in one call, as the watcher
    sends once a tick at most. How many snmpd took; 0 when nothing
    answered at `path`.
    """
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sent = 0
    try:
        s.settimeout(timeout)
        s.connect(path or AGENTX_SOCKET)
        s.sendall(ax_open(1))
        got = ax_response(_recv_pdu(s))
        if got is None or got[2] != 0:
            return 0
        session = got[0]
        for n, varbinds in enumerate(notifications, start=2):
            s.sendall(ax_notify(session, n, varbinds))
            got = ax_response(_recv_pdu(s))
            if got is not None and got[2] == 0:
                sent += 1
        s.sendall(ax_close(session, len(notifications) + 2))
        _recv_pdu(s)
    except (OSError, struct.error):
        pass
    finally:
        s.close()
    return sent

