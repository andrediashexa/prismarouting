#!/usr/bin/env python3
"""
lab/nat64.sh's client for OperationalService.ListTop with a NAT64 subscriber
proven live (#485, item 4): until this, only unit fixtures held that a NAT64
subscriber reaches ListTop as its delegated prefix.

The subscriber sent real IPv6 traffic through the pool in the sections
before. Here the lab asks ListTop, opens FLOWS more UDP flows from the
subscriber, from source ports it has not used, and asks again. Each check
is held against something the API did not say:

  * the row's subscriber is the /56 the lab's own address is in, and its
    pool is the NAT64 pool the lab configured - never the index the
    dataplane keys it by, which as an IPv4 address (0.0.0.<n>) names nobody;
  * the row's ports grew by exactly FLOWS, the number of flows the lab
    opened between the two calls, inside a block of the size the lab wrote;
  * `text` is the VTY's own `show cgnat top`, asked right after.

Arguments: <stubs dir> <certs dir> <host:port>. One RESULT line per check.
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, sys.argv[1])
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402

CERTS, TARGET = sys.argv[2], sys.argv[3]
PREFIX = "2001:db8:1200:ab00::/56"
HOST = "2001:db8:1200:ab00::10"
DST = "64:ff9b:1:c000:2:200::"
FLOWS = 12
T = 30


def channel(who):
    """A mutual-TLS channel as the account `who`."""
    ca = open(f"{CERTS}/ca.crt", "rb").read()
    creds = grpc.ssl_channel_credentials(ca, open(f"{CERTS}/{who}.key", "rb").read(),
                                         open(f"{CERTS}/{who}.crt", "rb").read())
    return grpc.secure_channel(TARGET, creds,
                               options=(("grpc.ssl_target_name_override", "prisma-api"),))


def result(name, ok, detail=""):
    """One verdict line, the shape lab/nat64.sh greps."""
    print(f"RESULT {name} {'ok' if ok else 'FAIL'} {detail}".rstrip(), flush=True)


def row_of(resp):
    """The NAT64 subscriber's row, or None."""
    for s in resp.subscribers:
        if s.subscriber == PREFIX:
            return s
    return None


def open_flows(n):
    """`n` UDP flows from the subscriber, each from a port not used before."""
    code = (
        "import socket, sys\n"
        "for p in range(47100, 47100 + int(sys.argv[1])):\n"
        "    s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)\n"
        f"    s.bind(('{HOST}', p)); s.sendto(b'top', ('{DST}', 7)); s.close()\n")
    subprocess.run(["ip", "netns", "exec", "sub", "python3", "-c", code, str(n)],
                   check=True, timeout=30)


def vty_top():
    """The VTY's screen, as an operator gets it."""
    return subprocess.run(["python3", "/src/src/prismash/prismash.py", "-c",
                           "show cgnat top 1000"], capture_output=True,
                          text=True, timeout=60).stdout


def main():
    """Ask, open flows, ask again, and hold each answer to what the lab knows."""
    op = rpc.OperationalServiceStub(channel("root"))
    try:
        before = op.ListTop(pb.ListTopRequest(count=1000), timeout=T)
    except grpc.RpcError as e:
        for name in ("top64-row", "top64-not-index", "top64-ports", "top64-text"):
            result(name, False, f"{e.code().name} {e.details()[:120]}")
        return
    r0 = row_of(before)
    result("top64-row", r0 is not None and r0.pool == "v6"
           and r0.ports_total == 2048 and r0.blocks == 1,
           f"{[(s.subscriber, s.pool, s.ports_used, s.ports_total, s.blocks) for s in before.subscribers]}")
    result("top64-not-index", r0 is not None
           and not any(s.subscriber.startswith("0.0.0.") for s in before.subscribers),
           "the /56 is listed, and no row names a NAT64 subscriber by its index")
    open_flows(FLOWS)
    # The datagrams are sent when open_flows returns, not yet translated:
    # measured, a ListTop asked at once read 7 -> 7 on one run in two. So
    # ListTop is asked until the count stops moving, up to ten seconds - and
    # the check is then on the number it settled at, not on the wait.
    after, last = None, None
    for _ in range(20):
        time.sleep(0.5)
        after = op.ListTop(pb.ListTopRequest(count=1000), timeout=T)
        now = row_of(after).ports_used if row_of(after) else None
        if now is not None and now == last and now != (r0.ports_used if r0 else None):
            break
        last = now
    screen = vty_top()
    r1 = row_of(after)
    grew = (r1.ports_used - r0.ports_used) if (r0 and r1) else None
    result("top64-ports", grew == FLOWS and r1.percent_full
           == r1.ports_used * 100 // r1.ports_total,
           f"{FLOWS} flows opened, ports_used {r0.ports_used if r0 else '-'} -> "
           f"{r1.ports_used if r1 else '-'}")
    # Held to the screen asked between two ListTops, either of them: a
    # session that times out in the instant between is not the API's fault.
    again = op.ListTop(pb.ListTopRequest(count=1000), timeout=T)
    result("top64-text", screen.rstrip() in (after.text.rstrip(),
                                             again.text.rstrip())
           and f"\n{PREFIX} " in "\n" + after.text,
           f"{len(after.text)} bytes against the VTY's {len(screen)}")
    print(after.text)


if __name__ == "__main__":
    main()
