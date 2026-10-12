#!/usr/bin/env python3
"""
The block-event segment's layout, as a contract between its writer and every
reader of it (#606).

The writer is the plugin's own bpa_log_seg_size() and bpa_log_seg_layout()
(bpa_log.h), which bpa_log_seg_create() calls, compiled here against the
plugin's headers: the segment a box with 0, 1 and 4 workers makes (1, 2 and
5 vlib mains). Each one is written to a file with events in its rings and
handed to every reader there is:

  bpa_log_seg_check()   what prisma-logd runs before it trusts an offset
  logmon.header()       `monitor cgnat logging` and the API's stream
  lab/log_rings.py      what lab/ipfix.sh reads the segment with

and the arithmetic is asked of the segment itself: the header's fields against
the file's size and the ring count, every ring where the next one starts, the
export ring right after the last worker ring, the watch ring ending exactly at
the end of the file.

WHY. lab/log_rings.py restated the version (3) and the event size (52) and
nothing held it to the header, so #485's version 4 and 68-byte events in
0.150.0 made it call every segment unreadable: lab/ipfix.sh red on three
checks for 40 releases while prisma-logd, compiled from the header, read the
same segment. A pre-#485 segment (version 3, 52-byte events) is also built
and every reader must refuse it - a reader that accepts anything is the same
defect the other way round.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src", "prismash"))
import logmon as M  # noqa: E402

READER = os.path.join(ROOT, "lab", "log_rings.py")
fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")
    else:
        print(f"  [ ok ] {msg}")


# The writer, and bpa_log_seg_check() as a reader of a file. Ring i gets
# 3 + i events, the last one of them left unconsumed, and one more is dropped
# by a ring made to look full - so a reader that reads the wrong ring's
# counters, or one ring too few, gives other numbers.
PROG = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include "bpa_log.h"

int
main (int argc, char **argv)
{
  if (argc == 4 && !strcmp (argv[1], "write"))
    {
      u32 n = atoi (argv[2]);
      u64 size = bpa_log_seg_size (n);
      u8 *m = calloc (1, size);
      bpa_log_seg_hdr_t *h = (bpa_log_seg_hdr_t *) m;
      if (bpa_log_seg_layout (m, n, 1234))
        return 2;
      for (u32 i = 0; i < n; i++)
        {
          bpa_log_ring_t *r = bpa_log_seg_ring (m, i);
          for (u32 k = 0; k < 3 + i; k++)
            {
              bpa_log_event_t *e = bpa_log_ring_reserve (
                r, BPA_LOG_QUEUE_MAX, sizeof (bpa_log_event_t));
              e->event = BPA_NAT_EVENT_BLOCK_ALLOC;
              bpa_log_ring_publish (r);
            }
          bpa_log_ring_consume (r, 2 + i);
          r->tail -= BPA_LOG_QUEUE_MAX;  /* full: the next reserve drops */
          bpa_log_ring_reserve (r, BPA_LOG_QUEUE_MAX, sizeof (bpa_log_event_t));
          r->tail += BPA_LOG_QUEUE_MAX;
        }
      FILE *f = fopen (argv[3], "wb");
      fwrite (m, 1, size, f);
      fclose (f);
      bpa_log_ring_t *x = bpa_log_seg_xring (m), *w = bpa_log_seg_mring (m);
      printf ("%llu %u %u %u %llu %llu %llu %llu %llu %u\n",
              (unsigned long long) size, h->n_rings, h->ring_slots,
              h->ring_bytes, (unsigned long long) h->xring_offset,
              (unsigned long long) ((u8 *) x - m),
              (unsigned long long) h->mring_offset,
              (unsigned long long) ((u8 *) w - m),
              (unsigned long long) ((u8 *) bpa_log_seg_ring (m, n ? n - 1 : 0)
                                    - m),
              h->mring_slots);
      return 0;
    }
  if (argc == 3 && !strcmp (argv[1], "check"))
    {
      struct stat st;
      FILE *f = fopen (argv[2], "rb");
      if (!f || stat (argv[2], &st))
        return 2;
      u8 *m = malloc (st.st_size);
      if (fread (m, 1, st.st_size, f) != (size_t) st.st_size)
        return 2;
      printf ("%d\n", bpa_log_seg_check (m, st.st_size,
                                         sizeof (bpa_log_event_t)));
      return 0;
    }
  return 2;
}
'''


def run(*cmd):
    p = subprocess.run(list(cmd), capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def ring_bytes(slots, ev):
    return M.RING_HDR_BYTES + slots * ev


with tempfile.TemporaryDirectory() as tmp:
    src = os.path.join(tmp, "w.c")
    exe = os.path.join(tmp, "w")
    open(src, "w").write(PROG)
    rc, _, err = run("cc", "-O2", "-Wall", "-DBPA_STANDALONE",
                     "-I", os.path.join(ROOT, "test"),
                     "-I", os.path.join(ROOT, "src", "plugins", "bpa"),
                     "-o", exe, src)
    check(rc == 0, f"the plugin's writer compiles here: {err[:300]}")
    if rc:
        print(f"\n{fail} FAILURES")
        sys.exit(1)

    for workers in (0, 1, 4):
        n = workers + 1
        print(f"=== {workers} worker(s): {n} vlib main(s), {n} ring(s) ===")
        seg = os.path.join(tmp, f"seg{n}")
        rc, out, _ = run(exe, "write", str(n), seg)
        check(rc == 0, "the plugin lays the segment out")
        size, hn, slots, rb, xoff, xptr, moff, mptr, last, mslots = \
            (int(v) for v in out.split())
        xbytes = ring_bytes(65536, M.XEV_BYTES)
        mbytes = ring_bytes(mslots, M.XEV_BYTES)
        check(os.path.getsize(seg) == size,
              f"the file is the size bpa_log_seg_size() gives: {size}")
        check(hn == n, f"the header counts the {n} ring(s) asked for: {hn}")
        check(rb == ring_bytes(slots, M.EVENT_BYTES),
              f"a ring is its header and {slots} slots of {M.EVENT_BYTES} "
              f"bytes: {rb}")
        check(last == M.HDR_BYTES + (n - 1) * rb,
              "the last worker ring starts where n-1 rings after the header end")
        check(xoff == xptr == M.HDR_BYTES + n * rb,
              f"the export ring starts right after the last worker ring: {xoff}")
        check(moff == mptr == xoff + xbytes,
              f"the watch ring starts right after the export ring: {moff}")
        check(size == M.HDR_BYTES + n * rb + xbytes + mbytes,
              f"header + {n} x {rb} + export {xbytes} + watch {mbytes} "
              f"is the whole segment, nothing before or after: {size}")

        rc, out, _ = run(exe, "check", seg)
        check(out == "0", f"bpa_log_seg_check() - prisma-logd's - reads it: {out}")

        data = open(seg, "rb").read()
        h = M.header(data)
        check(h is not None and h["n_rings"] == n and h["ring_bytes"] == rb
              and h["mring"] == moff and h["mslots"] == mslots,
              "logmon reads it: the rings, their size and the watch ring")

        rc, out, _ = run(sys.executable, READER, seg)
        try:
            d = json.loads(out)
        except ValueError:
            d = {}
        check(rc == 0 and d.get("readable") is True,
              f"lab/log_rings.py reads it: {d.get('why', out[:200])}")
        check(d.get("n_rings") == n and len(d.get("rings", [])) == n,
              f"  and finds {n} ring(s): {len(d.get('rings', []))}")
        check(d.get("layout_bytes") == size == d.get("bytes"),
              f"  and says the layout adds up to the file: "
              f"{d.get('layout_bytes')} of {d.get('bytes')}")
        want = [{"head": 3 + i, "tail": 2 + i, "written": 3 + i, "dropped": 1}
                for i in range(n)]
        check(d.get("rings") == want,
              f"  and reads each ring's own head, tail, written and dropped: "
              f"{d.get('rings')}")
        check(d.get("written") == sum(3 + i for i in range(n))
              and d.get("pending") == n and d.get("dropped") == n,
              f"  and their sums: written {d.get('written')}, pending "
              f"{d.get('pending')}, dropped {d.get('dropped')}")

        # Shorter by one byte: the watch ring no longer fits. logmon drops
        # the watch ring; the C check and the lab's reader still read the
        # rings, whose end the export ring bounds - one byte short of THAT
        # is refused by both.
        cut = os.path.join(tmp, f"cut{n}")
        open(cut, "wb").write(data[:xoff + xbytes - 1])
        rc, out, _ = run(exe, "check", cut)
        check(out == "-6", f"one byte short of the export ring: the C check "
              f"refuses it (-6): {out}")
        rc, out, _ = run(sys.executable, READER, cut)
        check(rc != 0 and '"readable": false' in out,
              "  and so does lab/log_rings.py")

    print("=== a segment of the layout before #485: version 3, 52-byte events ===")
    old = bytearray(open(os.path.join(tmp, "seg1"), "rb").read())
    struct.pack_into("<I", old, M.OFF["version"], 3)
    struct.pack_into("<I", old, M.OFF["event_bytes"], 52)
    struct.pack_into("<I", old, M.OFF["ring_bytes"], ring_bytes(8192, 52))
    p = os.path.join(tmp, "old")
    open(p, "wb").write(old)
    rc, out, _ = run(exe, "check", p)
    check(out == "-3", f"bpa_log_seg_check() refuses another version: {out}")
    check(M.header(old) is None, "logmon refuses it")
    rc, out, _ = run(sys.executable, READER, p)
    check(rc != 0 and '"readable": false' in out and "version 3" in out,
          f"lab/log_rings.py refuses it, naming the version: {out[-90:]}")

print()
print("ALL TESTS PASSED" if not fail else f"{fail} FAILURES")
sys.exit(1 if fail else 0)
