#!/usr/bin/env python3
"""
The lab collector (lab/collector/prisma_collector.py, #491), function by
function, on what the box actually sends.

THE TEMPLATES ARE THE BOX'S OWN, captured on the #475 bench (0.140.2) with
tcpdump: template 256 is the RFC 8158 allocation record in exactly the field
order docs/logging.md gives it, template 257 the boot marker, and the third
message a boot-marker record. The allocation records are built here against
that captured template, because the bench had allocated nothing new while
the capture ran; a record laid out by hand against a template also written by
hand would prove only that this file agrees with itself.

The question the collector exists for - who held public X, port Y, at T - is
asked over every reading docs/logging.md defines: held, released before T,
ended by a restart, and nothing at all.
"""
import importlib.machinery
import importlib.util
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def load_collector():
    """The collector as a module, loaded by path because lab/ is no package."""
    path = os.path.join(REPO, "lab", "collector", "prisma_collector.py")
    loader = importlib.machinery.SourceFileLoader("prisma_collector", path)
    spec = importlib.util.spec_from_loader("prisma_collector", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


pc = load_collector()
fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


# Captured from 198.51.100.2 (the bench box) on 2026-09-28.
T256 = bytes.fromhex("000a00346abacf8c0000000000000001000200240100000701430008"
                     "00e600010008000400e1000401690002016a0002011b0004")
T257 = bytes.fromhex("000a00206abacf8c000000000000000100020010010100020143"
                     "000800a00008")
BOOT = bytes.fromhex("000a00246abacf93000000000000000101010014000001a0e9bad9bb"
                     "000001a0e93e2286")
BOX = "198.51.100.2"


def alloc_msg(ts_ms, event, sub, pub, start, end, pool=0):
    """One IPFIX message with one record in the box's template 256."""
    ip = lambda a: bytes(int(x) for x in a.split("."))
    rec = struct.pack("!QB", ts_ms, event) + ip(sub) + ip(pub) + struct.pack(
        "!HHI", start, end, pool)
    body = struct.pack("!HH", 256, 4 + len(rec)) + rec
    return struct.pack("!HHIII", 10, 16 + len(body), ts_ms // 1000, 9, 1) + body


print("=== the box's IPFIX, decoded ===")
tpl = {}
check(pc.parse_ipfix(T256, tpl, BOX) == [] and (BOX, 1, 256) in tpl,
      f"the box's template 256 was not learnt: {list(tpl)}")
check([f[0] for f in tpl[(BOX, 1, 256)]] == [323, 230, 8, 225, 361, 362, 283],
      f"template 256 is not docs/logging.md's record: {tpl[(BOX, 1, 256)]}")
pc.parse_ipfix(T257, tpl, BOX)
recs = pc.parse_ipfix(BOOT, tpl, BOX)
check(recs == [{"observationTimeMilliseconds": 1790627731899,
                "systemInitTimeMilliseconds": 1790619558534}],
      f"the box's boot marker decoded as {recs}")
ev = pc.event_from_ipfix(recs[0], BOX)
check(ev and ev["kind"] == "BOOT-MARKER" and ev["init_ms"] == 1790619558534,
      f"the boot marker is not a BOOT-MARKER event: {ev}")
a = pc.parse_ipfix(alloc_msg(1790628000000, 16, "100.64.0.11", "203.0.113.1",
                             1024, 17407), tpl, BOX)
ev = pc.event_from_ipfix(a[0], BOX)
check(ev == {"ts_ms": 1790628000000, "via": "ipfix", "exporter": BOX,
             "kind": "BLOCK-ALLOC", "subscriber": "100.64.0.11",
             "public": "203.0.113.1", "port_start": 1024, "port_end": 17407,
             "pool": 0},
      f"an allocation in the box's template decoded as {ev}")
check(pc.parse_ipfix(alloc_msg(1, 16, "1.1.1.1", "2.2.2.2", 1, 2), {}, BOX) == [],
      "a data set with no template was guessed at")
check(pc.parse_ipfix(b"\x00\x09" + T256[2:], {}, BOX) == [],
      "a NetFlow v9 message was read as IPFIX")
check(pc.parse_ipfix(T256[:10], {}, BOX) == [], "a truncated header was read")
check(pc.event_from_ipfix({"natEvent": 99, "observationTimeMilliseconds": 1},
                          BOX) is None, "an unknown natEvent was stored")
check(pc.event_from_ipfix({"natEvent": 16}, BOX) is None,
      "a record with no time was stored")
print("  templates 256 and 257 as the box sends them; an allocation; no "
      "guessing without a template")

print("\n=== element values and the template sets ===")
check(pc.ipfix_value(8, bytes([100, 64, 0, 5])) == "100.64.0.5",
      "an IPv4 element is not a dotted quad")
check(pc.ipfix_value(361, b"\x04\x00") == 1024, "a port is not an integer")
check(pc.ipfix_value(-1, b"sub-7", text_first=True) == "sub-7"
      and pc.ipfix_value(-1, b"subscriber-7") == "subscriber-7",
      "a printable enterprise string was not kept as text")
check(pc.ipfix_value(-1, bytes(range(9))) == bytes(range(9)).hex(),
      "unprintable bytes were not kept as hex")
t = {}
opt = struct.pack("!HHH", 300, 2, 1) + struct.pack("!HH", 149, 4) + \
    struct.pack("!HHI", 0x8000 | 5, 2, 32473)
pc.parse_template_set(opt, 3, t, (BOX, 1))
check(t.get((BOX, 1, 300)) == [(149, 4, 0), (5, 2, 32473)],
      f"an options template with an enterprise field read as {t}")
recs = pc.parse_data_set(struct.pack("!I", 7) + b"\x03abc",
                         [(1, 4, 0), (2, 0xFFFF, 0)])
check(recs == [{1: 7, 2: "abc"}], f"a variable-length field read as {recs}")
print("  addresses, ports, text, hex; an options template; a variable field")

print("\n=== the box's syslog, framed and parsed ===")
LINE = (b'<134>1 2026-09-28T20:40:00.123Z prisma-bench prisma-cgnat - '
        b'BLOCK-ALLOC [cgnat@32473 natEvent="16" subscriber="100.64.0.11" '
        b'public="203.0.113.1" portStart="1024" portEnd="17407" pool="0"] '
        b'100.64.0.11 -> 203.0.113.1 ports 1024-17407 pool 0')
framed = str(len(LINE)).encode() + b" " + LINE
msgs, rest, skip = pc.syslog_frames(framed + framed[:30])
check(msgs == [LINE] and rest == framed[:30] and skip == 0,
      f"octet counting: {len(msgs)} message(s), {len(rest)} byte(s) left")
msgs, rest, skip = pc.syslog_frames(b"<13>1 - - - - - - one\n<13>1 - - - - - - two\npart")
check(len(msgs) == 2 and rest == b"part", "newline framing was not read")
# The stream every release to 0.140.2 could send after 12576 bytes (#492):
# the tail of a line, then whole frames. The tail is skipped and counted.
tail = b'34Z" observedAt="2026-09-28T20:15:31.736Z"] this box has been up'
msgs, rest, skip = pc.syslog_frames(tail + framed + framed)
check(msgs == [LINE, LINE] and skip == len(tail),
      f"a stream that began mid-message: {len(msgs)} message(s), {skip} "
      f"skipped, not 2 and {len(tail)}")
p = pc.parse_syslog(LINE)
check(p and p["msgid"] == "BLOCK-ALLOC" and p["params"]["public"] == "203.0.113.1"
      and p["text"].startswith("100.64.0.11 ->"),
      f"the box's allocation line parsed as {p}")
check(pc.parse_syslog(b"no priority here") is None,
      "a line that is not RFC 5424 was parsed")
check(pc.sd_params('x@1 a="1" b="q\\"t" c="]"') == {"a": "1", "b": 'q"t', "c": "]"},
      "structured-data escapes were not undone")
check(pc.rfc3339_ms("2026-09-28T20:40:00.123Z") == 1790628000123,
      "the box's timestamp is not the epoch ms it names")
ev = pc.event_from_syslog(p, BOX)
check(ev["kind"] == "BLOCK-ALLOC" and ev["port_start"] == 1024
      and ev["port_end"] == 17407 and ev["subscriber"] == "100.64.0.11"
      and ev["ts_ms"] == 1790628000123,
      f"the syslog allocation became {ev}")
boot = pc.parse_syslog(b'<134>1 2026-09-28T20:37:01.901Z b prisma-cgnat - '
                       b'BOOT-MARKER [cgnat@32473 bootTime="2026-09-28T18:19:18.534Z"'
                       b' observedAt="2026-09-28T20:37:01.901Z"] up')
check(pc.event_from_syslog(boot, BOX)["init_ms"] == 1790619558534,
      "the syslog boot marker lost its boot time")
check(pc.event_from_syslog(pc.parse_syslog(b"<13>1 2026-09-28T00:00:00Z h a - "
                                          b"OTHER - x"), BOX) is None,
      "a message id the box does not send was stored")
print("  octet counting and newlines; the box's allocation and boot marker")

print("\n=== who held an address, in every reading ===")
db = pc.open_store(":memory:")
T0 = 1790628000000
for e in (
    {"ts_ms": T0, "kind": "BLOCK-ALLOC", "subscriber": "100.64.0.11",
     "public": "203.0.113.1", "port_start": 1024, "port_end": 17407,
     "exporter": BOX, "via": "ipfix"},
    {"ts_ms": T0 + 60000, "kind": "BLOCK-RELEASE", "subscriber": "100.64.0.11",
     "public": "203.0.113.1", "port_start": 1024, "port_end": 17407,
     "exporter": BOX, "via": "ipfix"},
    {"ts_ms": T0 + 120000, "kind": "BLOCK-ALLOC", "subscriber": "100.64.0.12",
     "public": "203.0.113.1", "port_start": 1024, "port_end": 17407,
     "exporter": BOX, "via": "syslog"},
    {"ts_ms": T0 + 300000, "kind": "BOOT-MARKER", "init_ms": T0 + 250000,
     "exporter": BOX, "via": "ipfix"},
):
    pc.store_event(db, e)
r = pc.who_held(db, "203.0.113.1", 5000, T0 + 30000)
check(r["status"] == "held" and r["alloc"]["subscriber"] == "100.64.0.11",
      f"inside the first holding: {r}")
r = pc.who_held(db, "203.0.113.1", 5000, T0 + 90000)
check(r["status"] == "released" and r["end"]["ts_ms"] == T0 + 60000,
      f"between the release and the next allocation: {r}")
r = pc.who_held(db, "203.0.113.1", 5000, T0 + 200000)
check(r["status"] == "held" and r["alloc"]["subscriber"] == "100.64.0.12",
      f"inside the second holding: {r}")
r = pc.who_held(db, "203.0.113.1", 5000, T0 + 400000)
check(r["status"] == "ended-at-restart" and r["end"]["init_ms"] == T0 + 250000,
      f"after a restart with no release: {r}")
check(pc.who_held(db, "203.0.113.1", 20000, T0 + 30000)["status"] == "none",
      "a port outside every block had a holder")
check(pc.who_held(db, "203.0.113.2", 5000, T0 + 30000)["status"] == "none",
      "another public address had a holder")
check(pc.who_held(db, "203.0.113.1", 5000, T0 - 1)["status"] == "none",
      "a time before any allocation had a holder")
ev = pc.recent(db, 10)
check([e["ts_ms"] for e in ev] == [T0 + 300000, T0 + 120000, T0 + 60000, T0],
      "the latest events are not newest first")
check(len(pc.recent(db, 10, subscriber="100.64.0.11")) == 2
      and len(pc.recent(db, 10, public="203.0.113.1", kinds=["BLOCK-ALLOC"])) == 2,
      "the filters by subscriber, address and kind")
print("  held, released, ended at a restart, none - and newest first")

print("\n=== the page ===")
check(pc.fmt_ms(T0, "UTC") == "2026-09-28 20:40:00.000 +0000",
      f"a time rendered as {pc.fmt_ms(T0, 'UTC')!r}")
check(pc.fmt_ms(None) == "", "no time rendered as something")
check(pc.parse_local("2026-09-28 17:40:00", "America/Sao_Paulo") == T0,
      "a local time was not read in its zone")
check(pc.parse_local("2026-09-28T20:40:00+00:00") == T0,
      "a time with an offset was not taken as given")
page = pc.event_rows(pc.recent(db, 10))
check("up since" in page and "100.64.0.12" in page and "<script" not in page,
      "the events table")
check("&lt;x&gt;" in pc.event_rows([{"ts_ms": 1, "kind": "<x>", "subscriber": None,
                                    "public": None, "port_start": None,
                                    "port_end": None, "pool": None,
                                    "init_ms": None, "via": "t"}]),
      "a value was put on the page unescaped")
r = pc.who_held(db, "203.0.113.1", 5000, T0 + 30000)
check("100.64.0.11" in pc.answer_html(r, "203.0.113.1", 5000, T0 + 30000),
      "the answer does not name the holder")
check("No allocation" in pc.answer_html({"status": "none"}, "203.0.113.9", 1,
                                         T0), "the answer to nobody")
html = pc.render(db, "/who", "public=203.0.113.1&port=5000&at=2026-09-28+17%3A40%3A30")
check("100.64.0.11</b> held" in html, "the who page does not answer")
check("Cannot read" in pc.render(db, "/who", "public=bad&port=x"),
      "a question it cannot read was not said to be one")
check("Events for 100.64.0.12" in pc.render(db, "/subscriber", "ip=100.64.0.12"),
      "the subscriber page")
for k in range(3):   # the same boot, repeated every 30 s
    pc.store_event(db, {"ts_ms": T0 + 330000 + 30000 * k, "kind": "BOOT-MARKER",
                        "init_ms": T0 + 250000, "exporter": BOX, "via": "syslog"})
b = pc.boots(db)
check(len(b) == 1 and b[0]["markers"] == 4 and b[0]["init_ms"] == T0 + 250000
      and b[0]["first_ms"] == T0 + 300000,
      f"four markers of one boot are not one row of four: {b}")
check("4" in pc.boot_rows(b), "the boots table does not count the markers")
front = pc.render(db, "/", "")
check("refresh" in front and "Boots seen" in front, "the latest-events page")
check(front.split("Boots seen")[0].count("BOOT-MARKER") == 0,
      "the latest events still list every repeated boot marker")
print("  local time, escaping, the answer, and the three pages")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
