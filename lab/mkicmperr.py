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

# The embedded protocol. UDP unless asked otherwise; "esp" builds the error a
# router sends about an outbound IPsec packet, which is the only PMTUD signal
# a tunnel has - there is no MSS in an ESP packet to clamp.
kind = sys.argv[6] if len(sys.argv) > 6 else "udp"

if kind == "esp":
    # SPI and sequence number: the eight bytes RFC 792 asks an error to quote.
    # Neither is read by this box - the tunnel is identified by the two
    # ADDRESSES in the embedded IP header - and they are here so the packet is
    # the one a real router would send rather than the smallest one that works.
    inner_l4 = struct.pack("!II", 0x11111111, 7)
    inner_ip = ip4(pub, remote, 50, len(inner_l4), total=1500)
else:
    inner_l4 = struct.pack("!HHHH", pub_port, remote_port, 8, 0)
    inner_ip = ip4(pub, remote, 17, len(inner_l4), total=1500)
inner_udp = inner_l4

# ICMP body: type 3 code 4, carrying the next-hop MTU
icmp_body = struct.pack("!HH", 0, mtu) + inner_ip + inner_udp
icmp = struct.pack("!BBH", 3, 4, 0) + icmp_body
icmp = icmp[:2] + struct.pack("!H", csum(icmp)) + icmp[4:]

pkt = ip4(router, pub, 1, len(icmp)) + icmp
print("0x" + pkt.hex())
