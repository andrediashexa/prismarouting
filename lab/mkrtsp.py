#!/usr/bin/env python3
"""
Builds an RTSP control segment (IP/TCP/payload) in hex, for the packet
generator.

Lines are given separated by "|" and each is terminated with CRLF; a blank
line closes the header block, unless --no-end is given, which is how a message
split across segments is injected.

The sequence and acknowledgement numbers are arguments because the ALG's
hardest failure is not the rewrite: it is that the rewrite changes the payload
length, and every sequence number after it has to move by the same amount. A
lab that could not choose them could not see that.
"""
import argparse
import ipaddress
import struct

FLAGS = {"fin": 0x01, "syn": 0x02, "rst": 0x04, "psh": 0x08,
         "ack": 0x10, "urg": 0x20}


def csum(b):
    if len(b) % 2:
        b += b"\x00"
    s = sum(struct.unpack(f"!{len(b)//2}H", b))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


ap = argparse.ArgumentParser()
ap.add_argument("src")
ap.add_argument("dst")
ap.add_argument("sport", type=int)
ap.add_argument("dport", type=int)
ap.add_argument("--seq", type=int, default=1000)
ap.add_argument("--ack", type=int, default=2000)
ap.add_argument("--flags", default="psh,ack")
ap.add_argument("--lines", default="", help='RTSP lines separated by "|"')
ap.add_argument("--no-end", action="store_true",
                help="omit the blank line, so the header block is unfinished")
ap.add_argument("--payload-len", action="store_true",
                help="print the payload length instead of the packet")
a = ap.parse_args()

body = ""
for line in a.lines.split("|") if a.lines else []:
    body += line + "\r\n"
if not a.no_end:
    body += "\r\n"
payload = body.encode()

if a.payload_len:
    print(len(payload))
    raise SystemExit(0)

flags = 0
for f in a.flags.split(","):
    flags |= FLAGS[f.strip().lower()]

tcp = struct.pack("!HHIIBBHHH", a.sport, a.dport, a.seq, a.ack, 5 << 4, flags,
                  8192, 0, 0)
pseudo = (ipaddress.IPv4Address(a.src).packed
          + ipaddress.IPv4Address(a.dst).packed
          + struct.pack("!BBH", 0, 6, len(tcp) + len(payload)))
tcp = tcp[:16] + struct.pack("!H", csum(pseudo + tcp + payload)) + tcp[18:]

ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(tcp) + len(payload), 0, 0,
                 64, 6, 0, ipaddress.IPv4Address(a.src).packed,
                 ipaddress.IPv4Address(a.dst).packed)
ip = ip[:10] + struct.pack("!H", csum(ip)) + ip[12:]
print("0x" + (ip + tcp + payload).hex())
