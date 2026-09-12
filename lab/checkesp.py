#!/usr/bin/env python3
"""
Decodes what actually left the box, from a pcap.

Reading the trace would be easier and would prove less. ESP passthrough makes
two promises and the trace can confirm neither: that the bytes below the IP
header are untouched - the SPI in particular, which RFC 4303 puts inside the
integrity check, so a rewritten one is discarded by the receiver in silence -
and that the IP checksum still covers the address that was rewritten. Only the
bytes on the wire settle both.

Prints one line per packet, meant to be read by the shell:

  esp  <src> <dst> <spi> <seq> <payload sha-ish> <ipcsum>
  udp  <src> <dst> <sport> <dport> <ipcsum>
  icmp <src> <dst> <inner-src> <inner-dst> <ipcsum> <icmpcsum>

The icmp line carries the EMBEDDED addresses because that is the half that
has to be translated: a subscriber's stack matches an ICMP error against its
own socket by the packet quoted inside it, so an error delivered to the right
host still tells it nothing if the embedded header still names the public
address. And <icmpcsum>, because the ICMP checksum covers the whole embedded
packet - rewriting an address in there without it is a packet the subscriber's
kernel drops without a word.

<ipcsum> is "ok" or "BAD". A translated packet whose IP checksum no longer
covers it is dropped by the first router that looks at it, and this box's own
counter still says it translated.

The payload column is a plain sum of the bytes after the ESP header: it is
there so that "the ciphertext came through unaltered" is an assertion a shell
can make, rather than something a reader has to eyeball.
"""
import ipaddress, struct, sys


def csum(b):
    if len(b) % 2:
        b += b"\x00"
    s = sum(struct.unpack(f"!{len(b)//2}H", b))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def read_pcap(path):
    with open(path, "rb") as f:
        data = f.read()
    magic = data[:4]
    if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
        endian = "<"
    elif magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
        endian = ">"
    else:
        raise SystemExit(f"pcap with unknown magic: {magic.hex()}")
    link = struct.unpack(endian + "I", data[20:24])[0]
    off, pkts = 24, []
    while off + 16 <= len(data):
        _, _, caplen, _ = struct.unpack(endian + "IIII", data[off:off + 16])
        off += 16
        pkts.append(data[off:off + caplen])
        off += caplen
    return link, pkts


link, pkts = read_pcap(sys.argv[1])

for raw in pkts:
    p = raw[14:] if link == 1 else raw
    if len(p) < 20 or (p[0] >> 4) != 4:
        continue
    hlen = (p[0] & 0x0F) * 4
    proto = p[9]
    src = str(ipaddress.IPv4Address(p[12:16]))
    dst = str(ipaddress.IPv4Address(p[16:20]))
    ck = "ok" if csum(p[:hlen]) == 0 else "BAD"
    l4 = p[hlen:]

    if proto == 50:
        if len(l4) < 8:
            continue
        spi, seq = struct.unpack("!II", l4[0:8])
        print(f"esp {src} {dst} {spi} {seq} {sum(l4[8:])} {ck}")
    elif proto == 17 and len(l4) >= 8:
        sport, dport = struct.unpack("!HH", l4[0:4])
        print(f"udp {src} {dst} {sport} {dport} {ck}")
    elif proto == 1 and len(l4) >= 8 + 20:
        icmp_ck = "ok" if csum(l4) == 0 else "BAD"
        inner = l4[8:]
        isrc = str(ipaddress.IPv4Address(inner[12:16]))
        idst = str(ipaddress.IPv4Address(inner[16:20]))
        print(f"icmp {src} {dst} {isrc} {idst} {ck} {icmp_ck}")
