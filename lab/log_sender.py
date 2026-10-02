#!/usr/bin/env python3
"""Subscriber traffic at a fixed rate, for lab/log-container.sh (#550).

  log_sender.py <dst> <pps> <seconds> <first source> <n sources> <out file>

UDP from n source addresses (already on the interface) to dst port 53, the
source port cycling over 64 ports per source so the box keeps a fixed set of
sessions - what is measured is whether the dataplane RECEIVES every packet
while something else happens, not session churn. Paced in batches of 100;
writes the packets actually sent to the out file.
"""
import ipaddress
import socket
import sys
import time

dst, pps, secs = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
first, n, out = ipaddress.ip_address(sys.argv[4]), int(sys.argv[5]), sys.argv[6]
socks = []
for i in range(n):
    for p in range(64):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((str(first + i), 40000 + p))
        socks.append(s)
payload = b"q" * 18
sent = 0
t0 = time.time()
end = t0 + secs
k = 0
while time.time() < end:
    for _ in range(100):
        socks[k].sendto(payload, (dst, 53))
        k = (k + 1) % len(socks)
        sent += 1
    ahead = sent / pps - (time.time() - t0)
    if ahead > 0:
        time.sleep(ahead)
open(out, "w").write("%d\n" % sent)
