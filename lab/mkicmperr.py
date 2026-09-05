#!/usr/bin/env python3
"""
Builds an ICMP "fragmentation needed" carrying the original packet embedded,
in hex, for VPP's packet generator.

This is the packet PMTUD depends on. Without translating it, the subscriber's
TCP connection hangs and the symptom that reaches support is "some sites do
not open".
"""
import ipaddress, struct, sys

def csum(b):
    if len(b) % 2:
        b += b"\x00"
    s = sum(struct.unpack(f"!{len(b)//2}H", b))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF

def ip4(src, dst, proto, payload_len, total=None):
    tot = total if total is not None else 20 + payload_len
    h = struct.pack("!BBHHHBBH4s4s", 0x45, 0, tot, 0, 0, 64, proto, 0,
                    ipaddress.IPv4Address(src).packed,
                    ipaddress.IPv4Address(dst).packed)
    c = csum(h)
    return h[:10] + struct.pack("!H", c) + h[12:]

router, pub, remote = sys.argv[1], sys.argv[2], sys.argv[3]
pub_port, remote_port, mtu = int(sys.argv[4]), int(sys.argv[5]), 1500

# the original packet the router is reporting on: subscriber -> remote
inner_udp = struct.pack("!HHHH", pub_port, remote_port, 8, 0)
inner_ip = ip4(pub, remote, 17, len(inner_udp), total=1500)

# ICMP body: type 3 code 4, carrying the next-hop MTU
icmp_body = struct.pack("!HH", 0, mtu) + inner_ip + inner_udp
icmp = struct.pack("!BBH", 3, 4, 0) + icmp_body
icmp = icmp[:2] + struct.pack("!H", csum(icmp)) + icmp[4:]

pkt = ip4(router, pub, 1, len(icmp)) + icmp
print("0x" + pkt.hex())
