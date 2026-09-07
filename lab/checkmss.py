#!/usr/bin/env python3
"""
Reads the MSS option off a captured packet, and checks both checksums.

Reading the clamp out of the trace would prove that the node believes it
clamped. What decides whether the connection works is the bytes on the wire and
the checksum over them: a clamp with a wrong TCP checksum fails silently - the
peer drops the segment and the connection hangs exactly as it did before,
except that now a counter says the problem was fixed.

  checkmss.py <pcap> <expected-mss|none>

Prints one line per TCP packet and exits non-zero if any packet has the wrong
MSS or a bad checksum.
"""
import struct
import sys


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


def mss_of(opts):
    """The MSS option in a TCP option field, or None. Walks it properly."""
    i = 0
    while i < len(opts):
        kind = opts[i]
        if kind == 0:               # EOL
            return None
        if kind == 1:               # NOP
            i += 1
            continue
        if i + 1 >= len(opts):
            return None
        ln = opts[i + 1]
        if ln < 2 or i + ln > len(opts):
            return None
        if kind == 2 and ln == 4:
            return struct.unpack("!H", opts[i + 2:i + 4])[0]
        i += ln
    return None


want = sys.argv[2]
link, pkts = read_pcap(sys.argv[1])
fail, seen = 0, 0

for n, pkt in enumerate(pkts, 1):
    ip = pkt[14:] if link == 1 else pkt
    if len(ip) < 20 or (ip[0] >> 4) != 4:
        continue
    ihl = (ip[0] & 0xF) * 4
    if ip[9] != 6:                  # not TCP
        continue
    total = struct.unpack("!H", ip[2:4])[0]
    ip = ip[:total]
    tcp = ip[ihl:]
    if len(tcp) < 20:
        continue
    seen += 1

    ip_ok = csum(ip[:ihl]) == 0
    pseudo = ip[12:20] + struct.pack("!BBH", 0, 6, len(tcp))
    tcp_ok = csum(pseudo + tcp) == 0

    doff = (tcp[12] >> 4) * 4
    got = mss_of(tcp[20:doff]) if doff >= 20 else None
    flags = tcp[13]
    names = "".join(c for c, b in (("F", 1), ("S", 2), ("R", 4), ("P", 8),
                                   ("A", 16)) if flags & b)

    ok = (got is None and want == "none") or (got is not None
                                              and str(got) == want)
    print(f"packet {n}: {'.'.join(str(b) for b in ip[12:16])}:"
          f"{struct.unpack('!H', tcp[0:2])[0]} -> "
          f"{'.'.join(str(b) for b in ip[16:20])}:"
          f"{struct.unpack('!H', tcp[2:4])[0]} "
          f"flags={names or '-'} mss={got} (want {want}) "
          f"ipsum={'OK' if ip_ok else 'BAD'} "
          f"tcpsum={'OK' if tcp_ok else 'BAD'}")
    if not (ok and ip_ok and tcp_ok):
        fail += 1

if seen == 0:
    print("no TCP packet in the capture")
    sys.exit(1)
sys.exit(1 if fail else 0)
