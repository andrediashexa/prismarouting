#!/usr/bin/env python3
"""
`monitor cgnat logging` (#556): the segment read by a second reader that
steals nothing, the events in words, the filters, the wire forms, and what
the export did per stream.

Every offset logmon.py restates is held to bpa_log_ring.h by compiling it
(the last block), so a layout change there turns this red rather than a
monitor that reads the wrong bytes. The ring tests write a segment the way
the writer does - slot first, head after - and read it as the monitor would:
behind, lapped, and torn mid-copy.
"""
import os
import struct
import subprocess
import sys
import tempfile
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src", "prismash"))
import logmon as M  # noqa: E402
import logstate  # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")
    else:
        print(f"  [ ok ] {msg}")


def ipn(s):
    a, b, c, d = (int(x) for x in s.split("."))
    return a << 24 | b << 16 | c << 8 | d


def segment(n_rings=2, slots=8, syslog=True, boot=1_790_000_000.25, gen=7,
            mslots=0):
    """
    A segment as bpa_log_seg_init() lays it out, in a bytearray; with mslots,
    a watch ring after it as bpa_log_seg_watch() records one (the export
    ring is left out: this reader never looks at it).
    """
    ring_bytes = M.RING_HDR_BYTES + slots * M.EVENT_BYTES
    base_end = M.HDR_BYTES + n_rings * ring_bytes + 64
    buf = bytearray(base_end + (M.RING_HDR_BYTES + mslots * M.XEV_BYTES
                                if mslots else 0))
    if mslots:
        struct.pack_into("<QI", buf, M.OFF["mring_offset"], base_end, mslots)
    buf[0:8] = M.MAGIC
    struct.pack_into("<IIIIII", buf, 8, M.VERSION, M.HDR_BYTES, n_rings, slots,
                     M.EVENT_BYTES, ring_bytes)
    struct.pack_into("<Q", buf, M.OFF["generation"], gen)
    struct.pack_into("<d", buf, M.OFF["boot_wall"], boot)
    c = M.OFF["cfg"] + M.STREAM_SYSLOG * M.CFG_BYTES
    buf[c + 4] = 1 if syslog else 0
    struct.pack_into("<HIII", buf, c + 6, 601, ipn("198.51.100.3"), 67033, 134)
    buf[c + 20:c + 20 + len(b"prisma-routing")] = b"prisma-routing"
    return buf


def push(buf, ring, ev=16, priv="100.64.0.5", pub="200.150.0.16", lo=1024,
         hi=17407, pool=0, who=b""):
    """The writer: the slot, then head (bpa_log_ring_publish)."""
    h = M.header(buf)
    base = M.ring_base(h, ring)
    head = struct.unpack_from("<I", buf, base)[0]
    off = base + M.RING_HDR_BYTES + (head & (h["slots"] - 1)) * M.EVENT_BYTES
    struct.pack_into("<IIIHHB", buf, off, ipn(priv), ipn(pub), pool, lo, hi, ev)
    buf[off + 17:off + 17 + M.IDENTITY_MAX] = who.ljust(M.IDENTITY_MAX, b"\0")
    struct.pack_into("<I", buf, base, (head + 1) & 0xFFFFFFFF)


print("=== the header ===")
seg = segment()
h = M.header(seg)
check(h and h["n_rings"] == 2 and h["slots"] == 8 and h["generation"] == 7,
      "a segment as the writer lays it out is read: rings, slots, generation")
check(h["syslog"] == {"enabled": True, "port": 601, "pen": 67033, "pri": 134,
                      "hostname": "prisma-routing"},
      "the syslog stream's PEN, PRI and hostname come from the segment")
check(abs(h["boot_wall"] - 1_790_000_000.25) < 1e-6, "the dataplane's start")
check(M.ring_base(h, 1) == M.HDR_BYTES + h["ring_bytes"], "ring 1 follows ring 0")
check(M.ring_head(seg, h, 0) == 0, "a new ring's head is 0")
for what, bad in (("the magic", lambda b: b.__setitem__(slice(0, 8), b"XXXXXXXX")),
                  ("the version", lambda b: struct.pack_into("<I", b, 8, 2)),
                  ("the event size", lambda b: struct.pack_into("<I", b, 24, 48)),
                  ("slots not a power of two",
                   lambda b: struct.pack_into("<I", b, 20, 6))):
    b = segment()
    bad(b)
    check(M.header(b) is None, f"a segment with another {what} is not read")
check(M.header(bytearray(100)) is None, "nor one shorter than its header")

print("=== a second reader that steals nothing ===")
seg = segment()
h = M.header(seg)
push(seg, 0)                     # before the monitor starts: not shown
r = M.RingReader(seg, h)
push(seg, 0, priv="100.64.0.6", who=b"cliente-6")
push(seg, 1, ev=17, priv="100.64.0.9")
evs, lost = r.poll(seg)
check(lost == 0 and [e["priv"] for e in evs] == ["100.64.0.6", "100.64.0.9"],
      "what was published since it attached, from every ring, nothing lost")
check(evs[0]["identity"] == "cliente-6" and evs[0]["lo"] == 1024
      and evs[0]["hi"] == 17407 and evs[0]["pub"] == "200.150.0.16",
      "an event decodes: identity, port range, public address")
tails = [struct.unpack_from("<I", seg, M.ring_base(h, i) + 64)[0] for i in (0, 1)]
check(tails == [0, 0], "the reader never wrote the rings' tails: VPP's drain "
      "still owns them")
check(r.poll(seg) == ([], 0), "a second poll with nothing new returns nothing")
for k in range(11):              # 11 into 8 slots: 3 overwritten unread
    push(seg, 0, priv=f"100.64.1.{k}")
evs, lost = r.poll(seg)
check(lost == 4 and len(evs) == 7 and evs[0]["priv"] == "100.64.1.4",
      "a reader 11 behind an 8-slot ring shows the 7 certainly whole and counts "
      "4 lost: the slot the writer fills next is never trusted")


class Lapping(bytearray):
    """A segment whose writer laps the reader while it copies."""
    armed = False

    def __getitem__(self, k):
        out = super().__getitem__(k)
        if self.armed and isinstance(k, slice) and k.stop - k.start == M.EVENT_BYTES:
            self.armed = False
            for _ in range(9):
                push(self, 1, priv="100.64.2.1")
        return out


seg = Lapping(segment())
h = M.header(seg)
r = M.RingReader(seg, h)
push(seg, 1, priv="100.64.3.3")
seg.armed = True
evs, lost = r.poll(seg)
check(lost >= 1 and all(e["priv"] != "100.64.3.3" for e in evs),
      "a slot the writer lapped while it was copied is counted, not printed "
      "torn")

raw = bytearray(52)
struct.pack_into("<IIIHHB", raw, 0, ipn("100.64.0.7"), ipn("200.150.0.17"), 3,
                 2048, 4095, 17)
raw[17:17 + 6] = b"joao-7"
check(M.decode_event(bytes(raw)) == {"event": 17, "priv": "100.64.0.7",
                                     "pub": "200.150.0.17", "pool": 3, "lo": 2048,
                                     "hi": 4095, "identity": "joao-7"},
      "one 52-byte event decodes field by field")
check(M.lost_line(t0 := 1_790_000_000, 5).endswith("5 event(s) not shown: this "
                                                   "monitor fell behind")
      and len(M.lost_line(t0, 4294967295)) <= 79, "what was lost is said, in 79")
check(M.stopped_line(t0).endswith(" stopped"), "the last line")

print("=== the watch ring (#556) ===")


def wpush(buf, kind=1, ev=17, priv="100.64.0.5", wall=1_790_000_050.5, who=b""):
    """The main thread's write: the slot, then head; no tail, no refusal."""
    h = M.header(buf)
    base = h["mring"]
    head = struct.unpack_from("<I", buf, base)[0]
    off = base + M.RING_HDR_BYTES + (head & (h["mslots"] - 1)) * M.XEV_BYTES
    struct.pack_into("<IIIHHB", buf, off, ipn(priv), ipn("200.150.0.16"), 1,
                     2048, 4095, ev)
    buf[off + 17:off + 17 + M.IDENTITY_MAX] = who.ljust(M.IDENTITY_MAX, b"\0")
    buf[off + 52] = kind
    struct.pack_into("<d", buf, off + 56, wall)
    struct.pack_into("<I", buf, base, (head + 1) & 0xFFFFFFFF)


seg = segment(mslots=16)
h = M.header(seg)
check(h["mring"] > 0 and h["mslots"] == 16, "a segment with a watch ring says so")
check(isinstance(M.reader(seg, h), M.WatchReader)
      and isinstance(M.reader(segment(), M.header(segment())), M.RingReader),
      "the watch ring is read when there is one, the worker rings when not")
w = M.WatchReader(seg, h)
wpush(seg, kind=2, ev=0, priv="0.0.0.0", wall=1_790_000_040.0)
wpush(seg, ev=17, who=b"ana")
evs, lost = w.poll(seg)
check(lost == 0 and [e["kind"] for e in evs] == [2, 1] and evs[1]["event"] == 17
      and evs[1]["identity"] == "ana" and evs[1]["wall"] == 1_790_000_050.5,
      "a marker and a release, with the wall clock the record carries")
raw = bytearray(64)
struct.pack_into("<IIIHHB", raw, 0, ipn("100.64.0.8"), ipn("200.150.0.18"), 2,
                 1, 2, 16)
raw[52] = 1
struct.pack_into("<d", raw, 56, 12.5)
check(M.decode_xev(bytes(raw))["wall"] == 12.5 and M.decode_xev(bytes(raw))["priv"]
      == "100.64.0.8", "one 64-byte slot decodes")
for k in range(20):
    wpush(seg, priv=f"100.64.4.{k}")
evs, lost = w.poll(seg)
check(lost == 5 and len(evs) == 15 and evs[-1]["priv"] == "100.64.4.19",
      "20 written into 16 slots behind it: 15 shown, 5 lost and counted")
base = M.parse_args([])[0]
ln = M.lines_for(dict(evs[-1]), 0, h, {1: "din"}, base)
check(len(ln) == 1 and "RELEASE" in ln[0] and "pool din" in ln[0]
      and ln[0].startswith(M.clock(1_790_000_050.5)),
      f"an event at the time its record carries: {ln}")
mk = {"kind": 2, "event": 0, "priv": "0.0.0.0", "pub": "0.0.0.0", "pool": 0,
      "lo": 0, "hi": 0, "identity": "", "wall": 1_790_000_040.0}
check(M.lines_for(mk, 0, h, {}, base)[0].split()[1] == "BOOT"
      and "boot marker exported" in M.lines_for(mk, 0, h, {}, base)[0]
      and M.lines_for(mk, 0, h, {}, M.parse_args(["event", "alloc"])[0]) == []
      and M.lines_for(mk, 0, h, {}, M.parse_args(["subscriber", "1.2.3.4"])[0]) == [],
      "a boot marker is the BOOT line, and a filter on events or addresses hides it")
check(M.lines_for(dict(evs[-1]), 0, h, {}, M.parse_args(["event", "alloc"])[0]) == [],
      "a filter drops what it does not want")

print("=== the words after the command ===")
o, why = M.parse_args([])
check(why is None and o["raw"] is False and o["seconds"] is None,
      "no words: everything, until Ctrl-C")
o, why = M.parse_args(["raw", "subscriber", "100.64.0.5", "public",
                       "200.150.0.16:2000", "event", "alloc", "pool", "din",
                       "seconds", "30"])
check(why is None and o["raw"] and o["subscriber"] == "100.64.0.5"
      and o["public"] == "200.150.0.16" and o["port"] == 2000
      and o["event"] == M.EVENT_ALLOC and o["pool"] == "din"
      and o["seconds"] == 30, "every option at once")
for words, want in ((["sideways"], "does not take"),
                    (["subscriber"], "needs a value"),
                    (["subscriber", "100.64.0"], "IPv4 address"),
                    (["public", "200.150.0.16:70000"], "1 to 65535"),
                    (["event", "all"], "alloc, release"),
                    (["seconds", "0"], "seconds is 1 to")):
    o, why = M.parse_args(words)
    check(o is None and want in why and len(why) <= 79,
          f"{' '.join(words)!r} is refused: {why}")

print("=== which events pass ===")
pools = M.parse_pool_index("0 din\n2 rfc6598\n")
check(pools == {0: "din", 2: "rfc6598"}, "`show bpa pool index`, hole and all")
check(M.parse_pool_index("unknown input `index'\n") == {},
      "a dataplane that does not know the command gives no names")
check(M.pool_name(2, pools) == "rfc6598" and M.pool_name(1, pools) == "#1",
      "a pool by name, or by number where the dataplane did not say")
ev = {"event": 16, "priv": "100.64.0.5", "pub": "200.150.0.16", "pool": 2,
      "lo": 1024, "hi": 1279, "identity": "cliente-5"}
base = M.parse_args([])[0]
check(M.matches(ev, base, pools), "no filter passes everything")
for words, want in ((["subscriber", "100.64.0.5"], True),
                    (["subscriber", "100.64.0.6"], False),
                    (["public", "200.150.0.16:1100"], True),
                    (["public", "200.150.0.16:2000"], False),
                    (["event", "release"], False),
                    (["pool", "rfc6598"], True), (["pool", "din"], False)):
    check(M.matches(ev, M.parse_args(words)[0], pools) is want,
          f"{' '.join(words)}: {'passes' if want else 'filtered out'}")

print("=== in words ===")
t = 1_790_000_000.123
check(len(M.clock(t)) == 8 and M.clock(t).count(":") == 2,
      "HH:MM:SS, no millisecond the 0.2 s poll could not stand behind")
check(M.ip4(0x6440_0005) == "100.64.0.5", "a host-order address")
lines = M.event_lines(ev, t, pools)
check(len(lines) == 2 and "ALLOC" in lines[0] and "100.64.0.5" in lines[0]
      and "200.150.0.16" in lines[0] and "1024-1279" in lines[0]
      and "pool rfc6598" in lines[0] and lines[1].strip() == "subscriber cliente-5"
      and all(len(x) <= 79 for x in lines),
      f"an allocation, the pool on its line, the identity under it: {lines}")
check(M.fit("x" * 70, ["abcdefghij", "k"]) == ["x" * 70, " " * 14 + "abcdefghij  k"],
      "what does not fit goes under it, indented, never cut")
long = dict(ev, identity="a" * 31)
lines = M.event_lines(long, t, pools)
check(len(lines) == 2 and lines[1].strip().endswith("subscriber " + "a" * 31)
      and all(len(x) <= 79 for x in lines),
      "an identity that does not fit goes on its own line, within 79")
ex = M.event_lines(dict(ev, event=12, identity="unknown"), t, pools)
check(len(ex) == 1 and "EXHAUSTED" in ex[0] and "no free block in pool rfc6598"
      in ex[0], f"an exhaustion, and 'unknown' is not printed: {ex[0]}")
rel = M.event_lines(dict(ev, event=17, identity=""), t, pools)
check("RELEASE" in rel[0], "a release")
check("BOOT" in M.boot_line(1_790_000_000) and "up since" in M.boot_line(0),
      "the dataplane's start")
check(M.syslog_escape('a"b]c\\') == 'a\\"b\\]c\\\\', "RFC 5424 escaping")

print("=== the wire forms ===")
h = M.header(segment())
raw = M.raw_lines(dict(ev, identity='x"y'), t, h)
check(len(raw) == 2 and raw[0].split("IPFIX ")[1].startswith("323=1790000000123 "
      "230=16 8=100.64.0.5 225=200.150.0.16 361=1024 362=1279 283=2 371=x\"y"),
      f"the IPFIX record by IE number: {raw[0]}")
check('<134>1 2026-01-' not in raw[1] and "prisma-routing prisma-cgnat - "
      "BLOCK-ALLOC [cgnat@67033 natEvent=\"16\" subscriber=\"100.64.0.5\"" in raw[1]
      and 'subscriberId="x\\"y"]' in raw[1] and raw[1].strip().startswith("<134>1 "),
      "the syslog line, with the stream's PRI, hostname and PEN, escaped")
h2 = M.header(segment(syslog=False))
check(len(M.raw_lines(ev, t, h2)) == 1, "no syslog line when syslog is off")
check("released" in M.raw_lines(dict(ev, event=17), t, h)[1], "a release says so")

print("=== what the export did ===")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
S1 = """IPFIX export of block events (RFC 8158)
  state       enabled
  exporter    10.0.0.2:4739 src 10.0.0.1 (path mtu 512, template every 20s)
  records     0 emitted, 0 dropped
  sender      the log container: 10 records, 3 packets, 2 templates, 0 send failures, 0 dropped
  exhaustion  one record per subscriber per 10s: 4 repeats not reported
  queue       0 event(s) dropped with a worker's queue full (8192 each)
  export      0 event(s) dropped with the log container's ring full (65536)
  transmit    ens20: 0 packet(s) the interface failed to send, from every sender on it
TCP streams carried by the log container (pid 7)
TCP transport: disabled
syslog transport (RFC 5424 over TCP)
  collector   10.0.0.2:601  connected
  lines       10 written, 2000 bytes sent, 0 pending
  file        0 bytes now of 256 MB at most, 0 dropped over it, 0 resynced, 0 stalls
"""
S2 = (S1.replace("10 records, 3 packets", "13 records, 4 packets")
      .replace("4 repeats", "9 repeats")
      .replace("10 written, 2000", "13 written, 2600"))
a, b = logstate.parse_logging(S1), logstate.parse_logging(S2)
d = M.delivery_lines(a, b, t)
check(any("ipfix-udp 10.0.0.2:4739  by the container:  +3 sent" in x for x in d)
      and any("syslog 10.0.0.2:601  by the container, connected:  +3 sent" in x
              for x in d) and any("+5 exhaustion repeats held back" in x for x in d)
      and all(len(x) <= 79 for x in d), "per stream, what went out since the last "
      "line, and the repeats held back")
check(M.delivery_lines(b, b, t) == [], "a quiet export prints nothing")
first = M.delivery_lines(None, a, t)
check(len(first) == 2, "the first reading says each configured stream once")
lost = logstate.parse_logging(S2.replace("13 written, 2600 bytes sent, 0 pending",
                                         "13 written, 2600 bytes sent, 900 pending")
                              .replace("0 dropped over it", "40 dropped over it"))
d = M.delivery_lines(b, lost, t)
check(any("900 bytes waiting" in x for x in d) and any("40 LOST" in x for x in d)
      and all(len(x) <= 79 for x in d),
      "what waits and what was lost are said")

print("=== the segment's file ===")
with tempfile.TemporaryDirectory() as tmp:
    buf, why = M.open_segment(os.path.join(tmp, "none"))
    check(buf is None and why[0].startswith("no event segment at ")
      and "its own heap" in why[1], "no segment: the verdict, then why")
    p = os.path.join(tmp, "short")
    open(p, "wb").write(b"x" * 10)
    buf, why = M.open_segment(p)
    check(buf is None and "shorter" in why[0], "a file shorter than the header")
    p = os.path.join(tmp, "seg")
    open(p, "wb").write(bytes(segment()))
    buf, why = M.open_segment(p)
    check(why is None and M.header(buf)["n_rings"] == 2, "mapped and read")
    try:
        buf[0] = 1
        check(False, "the mapping is read-only")
    except TypeError:
        check(True, "the mapping is read-only: this reader cannot write it")

print("=== the offsets are the C header's ===")
src = os.path.join(ROOT, "src", "plugins", "bpa")
prog = r'''
#include <stdio.h>
#include <stddef.h>
#include <stdint.h>
#include "bpa_shim.h"
#include "bpa_log_ring.h"
int main (void) {
  printf ("%zu %zu %zu %zu %zu %zu %zu %zu %zu %zu %zu %zu %zu\n",
    offsetof (bpa_log_seg_hdr_t, version), offsetof (bpa_log_seg_hdr_t, n_rings),
    offsetof (bpa_log_seg_hdr_t, generation), offsetof (bpa_log_seg_hdr_t, boot_wall),
    offsetof (bpa_log_seg_hdr_t, cfg), sizeof (bpa_log_seg_cfg_t),
    offsetof (bpa_log_seg_cfg_t, enabled), offsetof (bpa_log_seg_cfg_t, port),
    offsetof (bpa_log_seg_cfg_t, pen), offsetof (bpa_log_seg_cfg_t, hostname),
    offsetof (bpa_log_ring_t, head), sizeof (bpa_log_ring_t),
    (size_t) BPA_LOG_SEG_VERSION);
  return 0; }
'''
with tempfile.TemporaryDirectory() as tmp:
    c = os.path.join(tmp, "o.c")
    open(c, "w").write(prog)
    exe = os.path.join(tmp, "o")
    rc = subprocess.run(["cc", "-I", os.path.join(ROOT, "test"), "-I", src, "-o",
                         exe, c], capture_output=True, text=True)
    if rc.returncode:
        check(False, f"the C header compiles for the comparison: {rc.stderr[:200]}")
    else:
        got = [int(x) for x in subprocess.run([exe], capture_output=True,
                                              text=True).stdout.split()]
        want = [M.OFF["version"], M.OFF["n_rings"], M.OFF["generation"],
                M.OFF["boot_wall"], M.OFF["cfg"], M.CFG_BYTES, 4, 6, 12, 20,
                M.OFF["ring_head"], M.RING_HDR_BYTES, M.VERSION]
        check(got == want, f"every offset logmon reads is bpa_log_ring.h's: {got}")
ev_h = open(os.path.join(src, "bpa_log.h")).read()
check("sizeof (bpa_log_event_t) == 52" in ev_h and "identity) == 17" in ev_h
      and M.EVENT_BYTES == 52, "the event is 52 bytes, identity at 17, as bpa_log.h "
      "pins it")
check(time.time() > 0 and M.POLL <= 0.5, "polled often enough to say 'within 0.2 s'")

print("=== the API's stream (#558, phase 5) ===")
rec = M.event_record({"event": 16, "priv": "100.64.0.5", "pub": "200.150.0.16",
                      "pool": 1, "lo": 1024, "hi": 2047, "identity": "ana"},
                     1_790_000_050.5, {1: "din"}, M.header(segment()))
check(rec["kind"] == "alloc" and rec["public"] == "200.150.0.16"
      and rec["port_first"] == 1024 and rec["pool"] == "din" and rec["identity"] == "ana"
      and "100.64.0.5" in rec["text"], "event_record: an allocation's fields and its line")
rec = M.event_record({"event": 12, "priv": "100.64.0.6", "pub": "0.0.0.0", "pool": 9,
                      "lo": 0, "hi": 0, "identity": "unknown"}, 1.0, {},
                     M.header(segment()))
check(rec["kind"] == "exhausted" and rec["public"] == "" and rec["port_first"] == 0
      and rec["pool"] == "#9" and rec["identity"] == "",
      "event_record: an exhaustion has no block; an unknown pool is by number")
dr = M.delivery_records(a, b, t)
byk = {x["stream"]: x for x in dr}
check(byk["ipfix-udp"]["sent"] == 3 and byk["syslog"]["sent"] == 3
      and byk["syslog"]["connection"] == "connected"
      and byk["exhaustion_repeats"]["lost"] == 5
      and all(x["kind"] == "delivery" for x in dr)
      and "+3 sent" in byk["ipfix-udp"]["text"],
      f"delivery_records: per stream what went out, and the repeats held back: "
      f"{sorted(byk)}")
check(M.delivery_records(b, b, t) == [], "delivery_records: a quiet export, nothing")

seg = segment(mslots=16)
clock = [1_790_000_100.0]


def fake_now():
    return clock[0]


def fake_sleep(dt):
    clock[0] += 1.0
    if clock[0] == 1_790_000_102.0:
        wpush(seg, ev=16, priv="100.64.0.7", wall=1_790_000_101.5)
        wpush(seg, ev=17, priv="100.64.0.8", wall=1_790_000_101.6)


opts, _ = M.parse_args(["seconds", "15"])
reads = iter([a, b])
got = list(M.watch(opts, seg, {1: "din"}, lambda: next(reads, b), now=fake_now,
                   sleep=fake_sleep))
kinds = [g["kind"] for g in got]
check(kinds[:2] == ["started", "boot"] and kinds.count("alloc") == 1
      and kinds.count("release") == 1 and "delivery" in kinds,
      f"watch: started, the boot, each event once, then what was delivered: {kinds}")
check(got[0]["from_watch_ring"] and got[0]["pools_named"],
      "watch: says whether it reads the watch ring and knows the pools' names")
opts, _ = M.parse_args(["subscriber", "100.64.0.8", "seconds", "15"])
opts["no_deliveries"] = True
seg = segment(mslots=16)
clock[0] = 1_790_000_100.0
got = list(M.watch(opts, seg, {}, lambda: a, now=fake_now, sleep=fake_sleep))
check([g["kind"] for g in got] == ["started", "boot", "release"],
      f"watch: the filters drop the rest - the opening BOOT stays, as the VTY's "
      f"monitor prints it - and no deliveries when asked: "
      f"{[g['kind'] for g in got]}")
stop = iter([True, False])
got = list(M.watch(M.parse_args([])[0], segment(mslots=16), {}, lambda: None,
                   now=fake_now, sleep=lambda dt: None, active=lambda: next(stop)))
check([g["kind"] for g in got] == ["started", "boot"],
      "watch: stops when the client goes")
check(list(M.watch(M.parse_args([])[0], bytearray(100), {}, lambda: None)) == [],
      "watch: a segment it cannot read yields nothing")

print()
print("ALL TESTS PASSED" if not fail else f"{fail} FAILURES")
sys.exit(1 if fail else 0)
