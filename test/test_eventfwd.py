#!/usr/bin/env python3
"""
src/prismash/eventfwd.py (#588 phase 3): `system logging`, the remote syslog
destinations of the box's record of events, sent by the host's watcher.

Each behaviour, shown red by a mutation (docs/logging-events.md lists them):

  * a destination read by one rule at the prompt and in the file, written
    back in the same words (mutation: parse_host() taking a name);
  * the wire: RFC 5424, the severity mapped, the structured data escaped,
    TCP framed by octet counting (mutations: the severity not mapped, TCP
    sent unframed);
  * the record followed across its rotation, a line not ended left for the
    next read (mutation: read_new() not reading the rotated file's rest);
  * a destination's minimum severity (mutation: wanted() always True);
  * THE RECORD IS THE SPOOL: a TCP collector away keeps its position, and
    what waited is sent when it is back - none lost, none twice (mutation:
    run() advancing the position on a failed send);
  * a new destination starts at the end, and its position survives a
    restart of the watcher (mutation: run() starting at 0);
  * `show system logging`'s rows from the configuration and the status.

Every time here is pinned; the sockets are local and each read has a
timeout, so nothing waits on the real clock to pass.
"""
import json
import os
import socket
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src", "prismash"))

_root = tempfile.mkdtemp(prefix="eventfwd-")
for k, v in (("PRISMA_EVENTS", "events.log"),
             ("PRISMA_EVENTS_STATE", "events.state"),
             ("PRISMA_DOORS", "no-doors.sock"),
             ("PRISMA_LOGGING_CONF", "logging.conf"),
             ("PRISMA_LOGGING_STATUS", "logging.status"),
             ("PRISMA_LOGGING_FWD_STATE", "events-forward.state")):
    os.environ[k] = os.path.join(_root, v)

import events  # noqa: E402
import eventfwd as F  # noqa: E402
import system  # noqa: E402

fail = 0


def check(c, msg):
    global fail
    print(("  [ ok ] " if c else "  FAIL: ") + msg)
    fail += 0 if c else 1


NOW = 1790836414.25
REC = os.environ["PRISMA_EVENTS"]


def ev(text, severity="notice", t=NOW, **kw):
    return events.make_event(kw.pop("source", "dataplane"), severity, text,
                             now=t, **kw)


print("=== a destination: one rule, the prompt's and the file's ===")
d, why = F.parse_host(["192.0.2.10"])
check(why is None and d == {"address": "192.0.2.10", "port": 514,
                            "protocol": "udp", "severity": "info",
                            "source-address": "", "vrf": ""},
      f"parse_host: an address alone takes the defaults: {d}")
d, why = F.parse_host("2001:db8::0a protocol tcp severity warning "
                      "source-address 2001:db8::1 vrf mgmt".split())
check(why is None and d["address"] == "2001:db8::a" and d["port"] == 601
      and d["protocol"] == "tcp" and d["severity"] == "warning"
      and d["source-address"] == "2001:db8::1" and d["vrf"] == "mgmt",
      f"parse_host: every option, TCP's port 601 by default: {d}")
for words, want in (
        (["syslog.example"], "not an IP address"),
        (["192.0.2.1", "port", "0"], "expected 1-65535"),
        (["192.0.2.1", "protocol", "tls"], "expected udp or tcp"),
        (["192.0.2.1", "severity", "debug"], "severity is one of"),
        (["192.0.2.1", "source-address", "2001:db8::1"], "one family"),
        (["192.0.2.1", "vrf", "a" * 16], "not a device name"),
        (["192.0.2.1", "port", "1", "port", "2"], "given twice"),
        (["192.0.2.1", "port"], "needs a value"),
        (["192.0.2.1", "facility", "3"], "host takes port"),
        ([], "expected an address")):
    d, why = F.parse_host(words)
    check(d is None and why and want in why,
          f"parse_host refuses {' '.join(words) or '(nothing)'}: {why}")
d, _ = F.parse_host("192.0.2.10 port 6514 protocol tcp vrf red".split())
check(F.host_words(d) == "host 192.0.2.10 port 6514 protocol tcp vrf red"
      and F.host_words(F.parse_host(["192.0.2.10", "port", "514"])[0]) ==
      "host 192.0.2.10",
      "host_words: the statement back, the defaults left out")
check(F.where(F.parse_host(["2001:db8::9"])[0]) == "[2001:db8::9]:514 udp",
      "where: an IPv6 collector bracketed")
conf = os.environ["PRISMA_LOGGING_CONF"]
ok, msg = F.write_conf([F.parse_host(["192.0.2.10"])[0],
                        F.parse_host("192.0.2.11 protocol tcp".split())[0]])
check(ok and msg == "2 remote syslog destination(s)",
      f"write_conf: written, and said: {msg}")
with open(conf, "a") as fh:
    fh.write("host bogus\nnonsense\nhost 192.0.2.10 port 9\n")
check([F.host_words(x) for x in F.read_conf()] ==
      ["host 192.0.2.10", "host 192.0.2.11 protocol tcp"],
      "read_conf: the commit's lines; a line edited in by hand that the "
      "prompt would refuse, or an address twice, is passed over")
check(F.read_conf(os.path.join(_root, "none")) == [],
      "read_conf: no file is no destination")
check(F.write_conf([], "/proc/no/such")[0] is False,
      "write_conf: a file that cannot be written is said")
check(F.conf_text([]).startswith("# Written by prismash"),
      "conf_text: says who writes it")

print("\n=== the wire: RFC 5424, TCP octet counting ===")
e = ev("interface memif1/0: link down", "warning", kind="link",
       subject="memif1/0", reason='a "quoted" ] \\ reason')
msg = F.rfc5424(e, "edge-1")
check(msg.decode() == '<28>1 2026-10-01T06:33:34.250Z edge-1 prisma-routing '
      '- LINK [event@67033 source="dataplane" kind="link" '
      'subject="memif1/0" reason="a \\"quoted\\" \\] \\\\ reason"] '
      'interface memif1/0: link down',
      f"rfc5424: PRI daemon.warning, UTC to the millisecond, the kind as "
      f"MSGID, the fields escaped: {msg!r}")
e = ev("configuration committed", "notice", source="vty", kind="commit",
       who="noc", origin="ssh 192.0.2.7")
check(F.rfc5424(e, "edge 1").decode().endswith(
      'account="noc" origin="ssh 192.0.2.7"] configuration committed (by '
      'noc, from ssh 192.0.2.7)')
      and F.rfc5424(e, "edge 1").startswith(b"<29>1 ")
      and b" edge1 " in F.rfc5424(e, "edge 1"),
      "rfc5424: who and from where, in the data and the sentence; a "
      "hostname with a space made one")
check(F.rfc5424(ev("x", "error"), "h").startswith(b"<27>1 ")
      and F.rfc5424(ev("x", "info"), "h").startswith(b"<30>1 ")
      and b" - - [event@67033" in F.rfc5424(ev("x"), "h"),
      "rfc5424: error 3, info 6; an event with no kind has MSGID -")
check(F.frame(b"<28>1 x", "tcp") == b"7 <28>1 x"
      and F.frame(b"<28>1 x", "udp") == b"<28>1 x",
      "frame: TCP's length in front, UDP's datagram as it is")
check(F._sd_value('a"b]c\\') == 'a\\"b\\]c\\\\', "_sd_value: the three "
      "characters RFC 5424 escapes")

print("\n=== the record: followed, across its rotation ===")
for p in (REC, REC + ".1"):
    if os.path.exists(p):
        os.remove(p)
events.append_event(ev("one"), REC)
got, pos = F.read_new(REC, None)
check(got == [] and pos["off"] == os.path.getsize(REC),
      "read_new: a first reading starts at the end - history is not news")
events.append_event(ev("two"), REC)
events.append_event(ev("three"), REC)
with open(REC, "a") as fh:
    fh.write('{"t": 1, "text": "half')
got, pos2 = F.read_new(REC, pos)
check([x["text"] for x in got] == ["two", "three"],
      f"read_new: what was appended, a line not ended left for later: "
      f"{[x['text'] for x in got]}")
with open(REC, "a") as fh:
    fh.write(' written"}\nnot json\n')
got, pos3 = F.read_new(REC, pos2)
check([x["text"] for x in got] == ["half written"] and
      F.read_new(REC, pos3)[0] == [],
      "read_new: and read once it is whole; a line that is not an event "
      "passed over")
events.append_event(ev("four"), REC)
cap = os.path.getsize(REC) + 10
events.append_event(ev("five, in the new file"), REC, cap=cap)
got, pos4 = F.read_new(REC, pos3)
check([x["text"] for x in got] == ["four", "five, in the new file"],
      f"read_new: the rotated file's rest, then the new one: "
      f"{[x['text'] for x in got]}")
got, _ = F.read_new(REC, {"ino": -1, "off": 99})
check([x["text"] for x in got] == ["five, in the new file"],
      "read_new: a position in neither file starts the new one")
got, p5 = F.read_new(REC, pos3, most=1)
got2, _ = F.read_new(REC, p5, most=5)
check([x["text"] for x in got + got2] == ["four", "five, in the new file"],
      "read_new: a batch cut short in the rotated file goes on from there")
check(F.read_new(os.path.join(_root, "none"), pos)[1] == pos,
      "read_new: no record, no move")
check(F._read_lines(os.path.join(_root, "none"), 0, 9) == ([], 0),
      "_read_lines: an unreadable file is nothing")
d = F.parse_host("192.0.2.1 severity warning".split())[0]
check(F.wanted(ev("x", "error"), d) and F.wanted(ev("x", "warning"), d)
      and not F.wanted(ev("x", "notice"), d),
      "wanted: the destination's severity and above")

print("\n=== the sender: a real collector, UDP and TCP ===")
for p in (REC, REC + ".1", os.environ["PRISMA_LOGGING_FWD_STATE"]):
    if os.path.exists(p):
        os.remove(p)
udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp.bind(("127.0.0.1", 0))
udp.settimeout(5)
tcp = socket.socket()
tcp.bind(("127.0.0.1", 0))
tcp.listen(1)
tcp.settimeout(5)
uport, tport = udp.getsockname()[1], tcp.getsockname()[1]
F.write_conf([F.parse_host(f"127.0.0.1 port {uport}".split())[0]])
events.append_event(ev("before the destination"), REC)
fw = F.Forwarder(hostname="edge-1")
check(fw.run(NOW) == 0, "run: a new destination starts at the end")
events.append_event(ev("link down", "warning", kind="link"), REC)
events.append_event(ev("a logout", "info", kind="login"), REC)
check(fw.run(NOW + 1) == 2, "run: what was appended is sent")
got = [udp.recv(4096), udp.recv(4096)]
check(got[0].startswith(b"<28>1 ") and got[0].endswith(b"link down")
      and got[1].endswith(b"a logout"),
      f"Forwarder, open_socket: one RFC 5424 datagram an event: {got}")
st = F.read_status()
check(st["t"] == NOW + 1 and st["destinations"][0]["sent"] == 2
      and st["destinations"][0]["state"] == "sending",
      f"write_status: what each destination did: {st['destinations']}")
check(F.Forwarder(hostname="x").pos == fw.pos and fw.pos,
      "save, _load: the position survives a restart of the watcher")
fw.close("host 127.0.0.1")
F.write_conf([F.parse_host(f"127.0.0.1 port {tport} protocol tcp "
                           f"severity warning".split())[0]])
os.utime(conf, ns=(1, 1))
check(fw.reload() and [F.host_words(x) for x in fw.dests] ==
      [f"host 127.0.0.1 port {tport} protocol tcp severity warning"]
      and list(fw.pos) == [], "reload: the old destination forgotten")
fw.run(NOW + 2)
events.append_event(ev("not wanted", "notice"), REC)
events.append_event(ev("bgp down", "warning", kind="bgp"), REC)
check(fw.run(NOW + 3) == 1, "run: only the destination's severity")
c, _ = tcp.accept()
c.settimeout(5)
buf = b""
while not buf.endswith(b"bgp down"):
    buf += c.recv(4096)
n, _, rest = buf.partition(b" ")
check(int(n) == len(rest) and rest.startswith(b"<28>1 "),
      f"Forwarder over TCP: octet counting, the line whole: {buf!r}")
check(fw.write_status(NOW) and F.Forwarder(
    status="/proc/no/such").write_status(NOW) is False,
      "write_status: False when it cannot be written")
check(F.read_status(os.path.join(_root, "none")) is None,
      "read_status: none is None")
c.close()
tcp.close()
udp.close()
fw.close(F.host_words(fw.dests[0]))
fw.close("never-opened")

print("\n=== THE RECORD IS THE SPOOL: an outage loses nothing ===")


class Down(Exception):
    pass


sent, up = [], [False]


class Sock:
    def sendall(self, b):
        if not up[0]:
            raise ConnectionResetError(104, "Connection reset by peer")
        sent.append(b)

    def close(self):
        pass


def connect(d):
    if not up[0]:
        raise ConnectionRefusedError(111, "Connection refused")
    return Sock()


F.write_conf([F.parse_host("192.0.2.50 protocol tcp".split())[0]])
fw = F.Forwarder(connect=connect, hostname="h",
                 state=os.path.join(_root, "spool.state"))
fw.run(NOW)
for i in range(3):
    events.append_event(ev(f"while away {i}"), REC)
check(fw.run(NOW + 1) == 0 and fw.status["host 192.0.2.50 protocol tcp"][
    "state"] == "not reached" and "connection refused" in
      fw.status["host 192.0.2.50 protocol tcp"]["error"],
      "run: a collector that refuses is said not reached, with why")
up[0] = True
check(fw.run(NOW + 5) == 0, "run: and not asked again before RETRY")
check(fw.run(NOW + 1 + F.RETRY) == 3 and [s.split(b"] ")[-1] for s in sent]
      == [b"while away 0", b"while away 1", b"while away 2"],
      f"run: back, it gets what waited, in order: {sent}")
check(fw.run(NOW + 100) == 0, "run: and nothing twice")
rows = F.status_rows(fw.dests, F.read_status(), NOW + 100,
                     lambda t: "T")
check(rows[0] == "192.0.2.50:601 tcp  info and above" and rows[1] ==
      "  sending" and rows[2] == "  sent 3 event(s) since the watcher "
      "started, the last at T", f"status_rows: {rows}")
up[0] = False
events.append_event(ev("again"), REC)
fw.run(NOW + 200)
rows = F.status_rows(fw.dests, F.read_status(), NOW + 200, str)
check(rows[1] == "  not reached: connection reset by peer; tried again "
      f"every {F.RETRY} s", f"status_rows: not reached, and why: {rows}")
check(F.status_rows(fw.dests, F.read_status(), NOW + 900, str)[1] ==
      "  not forwarding yet: the watcher (prisma-routing-events-watch) "
      "has not said", "status_rows: a status gone stale is not believed")
check(F.status_rows([], None, NOW, str) == [
    "no remote syslog destination: the record stays on the box ('show "
    "log')"], "status_rows: none configured")
d = F.parse_host("192.0.2.9 source-address 192.0.2.1 vrf red".split())[0]
check(F.status_rows([d], None, NOW, str)[0] ==
      "192.0.2.9:514 udp  info and above  from 192.0.2.1  vrf red",
      "status_rows: the source and the VRF")
check(F._why(OSError(113, "No route to host")) == "no route to host"
      and F._why(ValueError("x")) == "x", "_why: the kernel's words")
try:
    F.open_socket(F.parse_host("127.0.0.1 port 9 protocol tcp "
                               "source-address 192.0.2.77".split())[0])
    bound = "connected"
except OSError as e:
    bound = F._why(e)
check(bound == "cannot assign requested address",
      f"open_socket: bound to its source address before it connects: "
      f"{bound}")
check(F.SD_ID == f"event@{system.PRODUCT_PEN}",
      "SD_ID: under the product's own enterprise number")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
