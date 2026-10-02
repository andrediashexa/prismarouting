#!/usr/bin/env python3
"""Reads the block-event rings' segment from OUTSIDE VPP (#550).

What the log container will do, reduced to what a lab asserts: open
/dev/shm/prisma-bpa-log by name, check the header the way
bpa_log_seg_check() does, and print one JSON line - the header, and per ring
its head, tail, written and dropped. It does not consume: VPP still drains
the rings until the log container takes them over.

The offsets are src/plugins/bpa/bpa_log_ring.h's, and that file's
_Static_asserts are what keep them from moving silently.
"""
import json
import struct
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "/dev/shm/prisma-bpa-log"
HDR = struct.Struct("<8sIIIIIIQI")  # magic version hdr_bytes n_rings slots ev ring gen consumer
HDR_BYTES = 256
RING_HDR = 192
EVENT_BYTES = 52


def main():
    try:
        data = open(PATH, "rb").read()
    except OSError as e:
        print(json.dumps({"error": f"cannot open {PATH}: {e.strerror}"}))
        return 1
    if len(data) < HDR_BYTES:
        print(json.dumps({"error": "shorter than its header"}))
        return 1
    magic, ver, hb, n, slots, ev, rb, gen, consumer = HDR.unpack_from(data, 0)
    out = {"magic": magic.decode("ascii", "replace"), "version": ver,
           "hdr_bytes": hb, "n_rings": n, "ring_slots": slots,
           "event_bytes": ev, "ring_bytes": rb, "generation": gen,
           "consumer": consumer, "bytes": len(data), "rings": []}
    ok = (magic == b"PRBALOG1" and ver == 1 and hb == HDR_BYTES
          and ev == EVENT_BYTES and slots and not slots & (slots - 1)
          and rb == RING_HDR + slots * ev and HDR_BYTES + n * rb <= len(data))
    out["readable"] = bool(ok)
    if ok:
        for i in range(n):
            off = HDR_BYTES + i * rb
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
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
