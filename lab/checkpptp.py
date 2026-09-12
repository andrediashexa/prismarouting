#!/usr/bin/env python3
"""
Decodes what actually left the box, from a pcap.

Reading the trace would be easier and would prove less: the trace prints the
fields the node believed it wrote, and the whole risk with an ALG is a rewrite
that lands in the wrong place or leaves a checksum wrong. Only the bytes on the
wire settle that.

Prints one line per packet, meant to be read by the shell:

  pptp <src> <dst> <sport> <dport> <type> <cid@12> <cid@14> <csum>
  gre  <src> <dst> <version> <callid>
  icmp <src> <dst> <inner-src> <inner-dst> <inner-callid> <ipcsum> <icmpcsum>
  tcp  <src> <dst> <sport> <dport> <csum>

The icmp line carries the EMBEDDED addresses and the embedded Call ID, and all
three are the point. A subscriber's stack matches an ICMP error against its own
socket by the packet quoted INSIDE it, so an error delivered to the right host
still tells it nothing if the embedded header names the public address. The
embedded Call ID is printed because it must NOT have changed: it is the Call ID
the SERVER chose, which this box never translated - the one it did translate is
the subscriber's own, and that one is not in an outbound packet at all. A box
that "helpfully" rewrote it would still deliver the error and still pass every
address assertion.

<csum> is "ok" or "BAD": a rewritten segment whose checksum no longer covers it
is dropped by the peer's stack, and the CGNAT's own counter still says it
translated.
"""
import ipaddress, struct, sys

MAGIC = 0x1A2B3C4D


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
    l4 = p[hlen:]
    # The outer IP checksum, needed by every branch below: a translated packet
    # whose checksum no longer covers it is dropped by the first router that
    # looks at it, while this box's own counter still says it translated.
    ck = "ok" if csum(p[:hlen]) == 0 else "BAD"

    if proto == 1:
        if len(l4) < 8 + 20:
            continue
        icmp_ck = "ok" if csum(l4) == 0 else "BAD"
        inner = l4[8:]
        isrc = str(ipaddress.IPv4Address(inner[12:16]))
        idst = str(ipaddress.IPv4Address(inner[16:20]))
        ihlen = (inner[0] & 0x0F) * 4
        g = inner[ihlen:]
        # -1 and not 0 when the quote stops short: 0 is a Call ID a box could
        # plausibly have written, so it would read as an answer rather than as
        # "the packet did not carry one".
        icid = struct.unpack("!H", g[6:8])[0] if len(g) >= 8 else -1
        print(f"icmp {src} {dst} {isrc} {idst} {icid} {ck} {icmp_ck}")
        continue

    if proto == 47:
        if len(l4) < 8:
            continue
        version = l4[1] & 0x07
        call_id = struct.unpack("!H", l4[6:8])[0]
        print(f"gre {src} {dst} {version} {call_id}")
        continue

    if proto != 6 or len(l4) < 20:
        continue
    sport, dport = struct.unpack("!HH", l4[0:4])
    thlen = (l4[12] >> 4) * 4
    pseudo = (p[12:20] + struct.pack("!BBH", 0, 6, len(l4)))
    ck = "ok" if csum(pseudo + l4) == 0 else "BAD"
    pay = l4[thlen:]

    if len(pay) >= 12 and struct.unpack("!I", pay[4:8])[0] == MAGIC:
        mtype = struct.unpack("!H", pay[8:10])[0]
        c12 = struct.unpack("!H", pay[12:14])[0] if len(pay) >= 14 else -1
        c14 = struct.unpack("!H", pay[14:16])[0] if len(pay) >= 16 else -1
        print(f"pptp {src} {dst} {sport} {dport} {mtype} {c12} {c14} {ck}")
    else:
        print(f"tcp {src} {dst} {sport} {dport} {ck}")
