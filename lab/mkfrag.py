#!/usr/bin/env python3
"""
Monta um datagrama UDP partido em dois fragmentos IP, em hex.

The second fragment carries no UDP header: that is why it matches no session
at all unless shallow virtual reassembly has kept the ports from the
first.
"""
import ipaddress, struct, sys

def csum(b):
    if len(b) % 2:
        b += b"\x00"
    s = sum(struct.unpack(f"!{len(b)//2}H", b))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF

def ip4(src, dst, proto, payload, frag_off, mf, ident=0x1234):
    flags_off = (0x2000 if mf else 0) | (frag_off // 8)
    h = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), ident,
                    flags_off, 64, proto, 0,
                    ipaddress.IPv4Address(src).packed,
                    ipaddress.IPv4Address(dst).packed)
    h = h[:10] + struct.pack("!H", csum(h)) + h[12:]
    return h + payload

src, dst = sys.argv[1], sys.argv[2]
sport, dport = int(sys.argv[3]), int(sys.argv[4])
which = sys.argv[5]

data = bytes(range(8)) * 2          # 16 bytes de carga
udp = struct.pack("!HHHH", sport, dport, 8 + len(data), 0)

if which == "first":
    # the UDP header plus the first half of the payload
    pkt = ip4(src, dst, 17, udp + data[:8], 0, mf=1)
else:
    # only the second half: no UDP header at all
    pkt = ip4(src, dst, 17, data[8:], 16, mf=0)
print("0x" + pkt.hex())
