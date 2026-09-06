#!/usr/bin/env python3
"""
Builds one TCP segment with an arbitrary payload, in hex, for VPP's packet
generator.

Separate from mktcp.py because the FTP ALG is about the payload and about the
sequence numbers: this one takes both, so a lab can send a PORT command and
then the command that follows it in the same stream and check that the second
one was renumbered.

  mkftp.py <src> <dst> <sport> <dport> <seq> <ack> <flags> [payload]

The payload accepts backslash escapes, so "PORT 100,64,0,5,195,80\\r\\n" is
written the way RFC 959 describes it. A malformed payload is written the same
way on purpose: the ALG has to leave it alone, and a lab that cannot express
one cannot check that.
"""
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
seq, ack = int(sys.argv[5]), int(sys.argv[6])
flags = 0
for f in sys.argv[7].split(","):
    flags |= FLAGS[f.strip().lower()]
payload = b""
if len(sys.argv) > 8:
    payload = sys.argv[8].encode("latin1").decode("unicode_escape").encode("latin1")

tcp = struct.pack("!HHIIBBHHH", sport, dport, seq, ack, 5 << 4, flags,
                  8192, 0, 0) + payload
pseudo = (ipaddress.IPv4Address(src).packed + ipaddress.IPv4Address(dst).packed
          + struct.pack("!BBH", 0, 6, len(tcp)))
tcp = tcp[:16] + struct.pack("!H", csum(pseudo + tcp)) + tcp[18:]

ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(tcp), 0, 0, 64, 6, 0,
                 ipaddress.IPv4Address(src).packed,
                 ipaddress.IPv4Address(dst).packed)
ip = ip[:10] + struct.pack("!H", csum(ip)) + ip[12:]
print("0x" + (ip + tcp).hex())
