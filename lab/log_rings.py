#!/usr/bin/env python3
"""Reads the block-event rings' segment from OUTSIDE VPP (#550).

What the log container does, reduced to what a lab asserts: open
/dev/shm/prisma-bpa-log by name, check the header the way
bpa_log_seg_check() does, and print one JSON line - the header, the bytes
the layout adds up to, and per worker ring its head, tail, written and
dropped. It does not consume: VPP drains the worker rings itself.

#606: this file restated the layout's version (3) and event size (52) and
nothing held it to src/plugins/bpa/bpa_log_ring.h, so when #485 made them 4
and 68 in 0.150.0 it called every segment unreadable and lab/ipfix.sh went
red on three checks for every release since, while prisma-logd - compiled
from the header - read the same segment. The constants now come from
src/prismash/logmon.py, the product's own Python reader, whose offsets
test/test_logmon.py compiles the C header to compare; and
test/test_log_rings.py hands this script the segment the plugin's own
bpa_log_seg_layout() lays out, for one, two and five rings.
"""
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(HERE, "..", "src", "prismash"), "/src/src/prismash"):
    if os.path.isfile(os.path.join(p, "logmon.py")):
        sys.path.insert(0, p)
        break
import logmon as M  # noqa: E402

PATH = sys.argv[1] if len(sys.argv) > 1 else M.SEGMENT
HDR = struct.Struct("<8sIIIIIIQI")  # magic version hdr_bytes n_rings slots ev ring gen consumer
XRING = struct.Struct("<IIQ")       # xring_slots xev_bytes xring_offset, at 56
XRING_OFF = 56
XRING_SLOTS = 65536                 # BPA_LOG_XRING_SLOTS


def ring_bytes(slots, ev):
    """bpa_log_ring_bytes()."""
    return M.RING_HDR_BYTES + slots * ev


def check(data):
    """
    bpa_log_seg_check(), in its order: (0, "readable") or a negative reason
    with its words, so an unreadable segment says WHICH question failed.
    """
    if len(data) < M.HDR_BYTES:
        return -1, "shorter than its header"
    magic, ver, hb, n, slots, ev, rb, _gen, _cons = HDR.unpack_from(data, 0)
    if magic != M.MAGIC:
        return -2, "not a block-event segment (magic)"
    if ver != M.VERSION or hb != M.HDR_BYTES:
        return -3, f"layout version {ver}, this reader reads {M.VERSION}"
    if ev != M.EVENT_BYTES:
        return -4, f"events of {ev} bytes, this reader reads {M.EVENT_BYTES}"
    if not slots or slots & (slots - 1) or rb != ring_bytes(slots, ev):
        return -5, "ring sizes that do not add up"
    xoff = M.HDR_BYTES + n * rb
    if xoff + ring_bytes(XRING_SLOTS, M.XEV_BYTES) > len(data):
        return -6, "rings that run past the end of the segment"
    xs, xev, xo = XRING.unpack_from(data, XRING_OFF)
    if xs != XRING_SLOTS or xev != M.XEV_BYTES or xo != xoff:
        return -7, "an export ring this reader does not read"
    return 0, "readable"


def main():
    try:
        data = open(PATH, "rb").read()
    except OSError as e:
        print(json.dumps({"error": f"cannot open {PATH}: {e.strerror}"}))
        return 1
    rv, why = check(data)
    if rv == -1:
        print(json.dumps({"error": why, "bytes": len(data)}))
        return 1
    magic, ver, hb, n, slots, ev, rb, gen, consumer = HDR.unpack_from(data, 0)
    out = {"magic": magic.decode("ascii", "replace"), "version": ver,
           "hdr_bytes": hb, "n_rings": n, "ring_slots": slots,
           "event_bytes": ev, "ring_bytes": rb, "generation": gen,
           "consumer": consumer, "bytes": len(data), "rings": [],
           "readable": rv == 0, "why": why}
    if rv == 0:
        # What the segment adds up to: the header, n worker rings, the export
        # ring, and the watch ring when the writer recorded one (#556) - the
        # sum the issue's reader could not make out of n_rings alone.
        mring, mslots = struct.unpack_from("<QI", data, M.OFF["mring_offset"])
        want = M.HDR_BYTES + n * rb + ring_bytes(XRING_SLOTS, M.XEV_BYTES)
        if mring:
            want += ring_bytes(mslots, M.XEV_BYTES)
        out["layout_bytes"] = want
        out["watch_ring"] = bool(mring)
        for i in range(n):
            off = M.HDR_BYTES + i * rb
            head, = struct.unpack_from("<I", data, off)
            tail, = struct.unpack_from("<I", data, off + 64)
            dropped, written = struct.unpack_from("<QQ", data, off + 128)
            out["rings"].append({"head": head, "tail": tail,
                                 "written": written, "dropped": dropped})
        out["written"] = sum(r["written"] for r in out["rings"])
        out["dropped"] = sum(r["dropped"] for r in out["rings"])
        out["pending"] = sum((r["head"] - r["tail"]) & 0xFFFFFFFF
                             for r in out["rings"])
    print(json.dumps(out))
    return 0 if rv == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
