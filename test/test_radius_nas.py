#!/usr/bin/env python3
"""
The NAS this lab speaks with, and the C parser it speaks to (#21).

TWO IMPLEMENTATIONS OF ONE RFC, WHICH IS THE POINT. The box builds and checks
accounting packets in C; `tools/radius_nas.py` builds them in Python from RFC
2866. A test that checked either against itself would pass for a pair that are
wrong in the same way, so this file hands the Python packets to the C parser -
the real one, compiled by the same Makefile the product uses - and asserts it
takes them.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools"))
import radius_nas as nas                                         # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


print("=== the packet this NAS builds, as RFC 2866 describes it ===")
pkt, auth = nas.acct_request(7, "s3cr3t", 1, "100.64.0.5", "joao@isp")
check(pkt[0] == 4 and pkt[1] == 7, "code 4, the identifier given")
check(int.from_bytes(pkt[2:4], "big") == len(pkt),
      f"the length field is the packet: {int.from_bytes(pkt[2:4], 'big')} "
      f"vs {len(pkt)}")
check(pkt[4:20] == auth, "the authenticator is where the RFC puts it")
check(pkt[20] == 1 and pkt[21] == 10,
      f"User-Name carries its own two header bytes in its length: "
      f"{pkt[21]}")
ok("a NAS's packet, built from the RFC and not from the box's code")

print("\n=== an attribute value longer than RADIUS allows is refused ===")
try:
    nas.attr(1, b"x" * 254)
    check(False, "254 bytes fitted where 253 is the limit")
except ValueError:
    ok("the builder refuses it rather than writing a length that wraps")

print("\n=== and the C parser takes what this Python built ===")
# THE REAL PARSER, compiled here the way the harness is: a fixture written
# beside the code it checks agrees with that code's mistakes.
src = r'''
#include <stdio.h>
#include <string.h>
#include "bpa_shim.h"
#include "bpa_md5.c"
#include "bpa_radius.c"
int main (int argc, char **argv)
{
  u8 pkt[4096];
  u32 n = 0;
  bpa_radius_acct_t a;
  int rc;
  char *hex = argv[1];

  while (hex[n * 2] && hex[n * 2 + 1])
    {
      unsigned v;
      sscanf (hex + n * 2, "%2x", &v);
      pkt[n] = (u8) v;
      n++;
    }
  rc = bpa_radius_acct_parse (pkt, n, argv[2], &a);
  printf ("%d %u %08x %s\n", rc, a.status, a.framed_ip, a.user);
  return 0;
}
'''
with tempfile.TemporaryDirectory() as d:
    c = os.path.join(d, "probe.c")
    exe = os.path.join(d, "probe")
    open(c, "w").write(src)
    build = subprocess.run(
        ["cc", "-O1", "-DBPA_STANDALONE", "-I", os.path.join(REPO, "test"),
         "-I", os.path.join(REPO, "src", "plugins", "bpa"), "-o", exe, c],
        capture_output=True, text=True)
    if build.returncode != 0:
        check(False, f"the probe did not build: {build.stderr[:400]}")
    else:
        out = subprocess.run([exe, pkt.hex(), "s3cr3t"],
                             capture_output=True, text=True).stdout.split()
        check(out[:4] == ["0", "1", "64400005", "joao@isp"],
              f"the C parser reads the Python packet: {out}")
        # And the other way round: the same packet with another secret must
        # be BAD_AUTH (2), not accepted and not called malformed.
        out = subprocess.run([exe, pkt.hex(), "another-secret"],
                             capture_output=True, text=True).stdout.split()
        check(out[0] == "2",
              f"and refuses it under a secret that is not the one it was "
              f"signed with: {out}")
        ok("two implementations of RFC 2866 that agree, which is evidence")

print("\n=== the response check refuses what is not this request's answer ===")
import hashlib                                                   # noqa: E402
good = bytes([5, 7, 0, 20]) + b"\0" * 16
good = good[:4] + hashlib.md5(good[:4] + auth + b"s3cr3t").digest()
check(nas.response_is_valid(good, auth, "s3cr3t", 7),
      "the box's own answer is accepted")
check(not nas.response_is_valid(good, auth, "s3cr3t", 8),
      "an answer to another request is not")
check(not nas.response_is_valid(good, bytes(16), "s3cr3t", 7),
      "and neither is one signed over zeros rather than this request")
check(not nas.response_is_valid(b"", auth, "s3cr3t", 7),
      "nothing at all is not an answer either")
ok("a box that signs its reply wrong is caught here, not reported as working")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
