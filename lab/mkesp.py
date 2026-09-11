#!/usr/bin/env python3
"""
Builds IPsec ESP packets, in hex, for the packet generator.

A real IPsec stack cannot be asked to do what this test needs. The whole claim
under test is that two subscribers sharing one public address each get their
own tunnel back, and that the SPI the subscriber's own stack chose arrives
unchanged - which means choosing both SPIs by hand, including the inbound one
that this box has never seen announced anywhere. IKE is encrypted; nothing
else in the exchange would tell the box what it is, and nothing would let a
test pick it.

The payload is not real ciphertext and does not need to be: ESP has no
checksum of its own, and the only thing a CGNAT may touch is the IP header. A
packet whose bytes below the IP header come out byte-identical is the
assertion.
"""
import ipaddress, struct, sys


def csum(b):
    if len(b) % 2:
        b += b"\x00"
    s = sum(struct.unpack(f"!{len(b)//2}H", b))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def ip4(src, dst, proto, payload, ident=0, frag=0):
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), ident, frag,
                     64, proto, 0, ipaddress.IPv4Address(src).packed,
                     ipaddress.IPv4Address(dst).packed)
    ip = ip[:10] + struct.pack("!H", csum(ip)) + ip[12:]
    return ip + payload


def esp(spi, seq, nbytes=16):
    """RFC 4303 section 2: SPI, then the sequence number, then the payload."""
    body = bytes((i * 7 + 3) & 0xFF for i in range(nbytes))
    return struct.pack("!II", spi, seq) + body


def emit(pkt):
    print("0x" + pkt.hex())


cmd = sys.argv[1]

if cmd == "esp":
    # esp <src> <dst> <spi> [<seq>]
    src, dst = sys.argv[2], sys.argv[3]
    spi = int(sys.argv[4], 0)
    seq = int(sys.argv[5], 0) if len(sys.argv) > 5 else 1
    emit(ip4(src, dst, 50, esp(spi, seq)))

elif cmd == "udp":
    # udp <src> <dst> <sport> <dport> - an ordinary flow, used only to prove a
    # subscriber is reachable at all before anything is claimed about its
    # tunnel.
    src, dst = sys.argv[2], sys.argv[3]
    sport, dport = int(sys.argv[4]), int(sys.argv[5])
    body = b"hello"
    hdr = struct.pack("!HHHH", sport, dport, 8 + len(body), 0)
    emit(ip4(src, dst, 17, hdr + body))

else:
    raise SystemExit(f"unknown command {cmd}")
