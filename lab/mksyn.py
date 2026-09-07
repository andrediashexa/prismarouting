#!/usr/bin/env python3
"""
Builds a TCP segment carrying TCP options, in hex, for the packet generator.

mktcp.py already makes a segment with chosen flags, and deliberately has no
options: it was written for the TCP state machine, where the header is the
whole point. MSS clamping is about the option field, so it needs a header with
options in it - and, to be worth anything as a test, options that are not just
one MSS sitting alone at a convenient offset.

  mksyn.py <src> <dst> <sport> <dport> <flags> <mss|none> [pad]

`pad` puts NOP, NOP and SACK-permitted before the MSS option, which is what a
real Linux SYN looks like and what makes the option walk do some walking. A
clamp that only finds an MSS at offset 0 works on a test and not on traffic.
"""
import ipaddress
import struct
import sys

FLAGS = {"fin": 0x01, "syn": 0x02, "rst": 0x04, "psh": 0x08,
         "ack": 0x10, "urg": 0x20}

TCP_OPT_EOL, TCP_OPT_NOP, TCP_OPT_MSS, TCP_OPT_SACK_PERM = 0, 1, 2, 4


def csum(b):
    if len(b) % 2:
        b += b"\x00"
    s = sum(struct.unpack(f"!{len(b)//2}H", b))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


src, dst = sys.argv[1], sys.argv[2]
sport, dport = int(sys.argv[3]), int(sys.argv[4])
flags = 0
for f in sys.argv[5].split(","):
    flags |= FLAGS[f.strip().lower()]
mss = sys.argv[6]
pad = len(sys.argv) > 7 and sys.argv[7] == "pad"

opts = b""
if pad:
    opts += bytes([TCP_OPT_NOP, TCP_OPT_NOP, TCP_OPT_SACK_PERM, 2])
if mss != "none":
    opts += struct.pack("!BBH", TCP_OPT_MSS, 4, int(mss))
# The data offset counts 32-bit words, so the options are padded to a
# multiple of four with EOL. A header length that does not match the options
# is a different test; this one is about finding the MSS in a valid header.
while len(opts) % 4:
    opts += bytes([TCP_OPT_EOL])

offset = (20 + len(opts)) // 4
tcp = struct.pack("!HHIIBBHHH", sport, dport, 1000, 2000, offset << 4, flags,
                  8192, 0, 0) + opts
pseudo = (ipaddress.IPv4Address(src).packed + ipaddress.IPv4Address(dst).packed
          + struct.pack("!BBH", 0, 6, len(tcp)))
tcp = tcp[:16] + struct.pack("!H", csum(pseudo + tcp)) + tcp[18:]

ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(tcp), 0, 0, 64, 6, 0,
                 ipaddress.IPv4Address(src).packed,
                 ipaddress.IPv4Address(dst).packed)
ip = ip[:10] + struct.pack("!H", csum(ip)) + ip[12:]
print("0x" + (ip + tcp).hex())
