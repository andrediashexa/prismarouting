#!/usr/bin/env python3
"""Builds a TCP packet with chosen flags, in hex, for the packet generator."""
import ipaddress, struct, sys

FLAGS = {"fin": 0x01, "syn": 0x02, "rst": 0x04, "psh": 0x08,
         "ack": 0x10, "urg": 0x20}

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

tcp = struct.pack("!HHIIBBHHH", sport, dport, 1000, 2000, 5 << 4, flags,
                  8192, 0, 0)
pseudo = (ipaddress.IPv4Address(src).packed + ipaddress.IPv4Address(dst).packed
          + struct.pack("!BBH", 0, 6, len(tcp)))
tcp = tcp[:16] + struct.pack("!H", csum(pseudo + tcp)) + tcp[18:]

ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(tcp), 0, 0, 64, 6, 0,
                 ipaddress.IPv4Address(src).packed,
                 ipaddress.IPv4Address(dst).packed)
ip = ip[:10] + struct.pack("!H", csum(ip)) + ip[12:]
print("0x" + (ip + tcp).hex())
