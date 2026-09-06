#!/usr/bin/env python3
"""
Checks a rewritten RTSP control packet, read from a pcap.

Rewriting the Transport header is not enough, and this is the same lesson
lab/checkicmp.py exists for. The payload changed length, so the IP total
length, the IP checksum and the TCP checksum all changed with it, and every
sequence number after the rewrite moved. Each of those failures is silent: the
ALG counter reports a rewrite either way, the peer's stack drops the segment
without a word, and the subscriber sees a control connection that stalls or a
stream that never starts. Only the captured packet shows which happened.

Every invocation validates the packet itself - lengths and both checksums -
before it looks at anything the caller asked about.
"""
import argparse
import ipaddress
import re
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
    # A missing file means the packet never left: the capture is only written
    # when something arrives. Said plainly, because a traceback here reads
    # like a broken lab rather than a dropped packet.
    try:
        with open(path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        print(f"  FAIL: nothing was captured in {path}; the packet was dropped")
        sys.exit(1)
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


ap = argparse.ArgumentParser()
ap.add_argument("pcap")
ap.add_argument("--index", type=int, default=0,
                help="which TCP packet in the capture to check")
ap.add_argument("--client-port", help="expected client_port, exactly")
ap.add_argument("--client-port-in", metavar="LO-HI",
                help="client_port must be an even-first consecutive pair "
                     "inside this port range")
ap.add_argument("--contains", action="append", default=[])
ap.add_argument("--not-contains", action="append", default=[])
ap.add_argument("--seq", type=int)
ap.add_argument("--ack", type=int)
ap.add_argument("--print-client-port", action="store_true")
ap.add_argument("--print-sport", action="store_true",
                help="print the source port the packet left with")
ap.add_argument("--udp-to", metavar="IP:PORT",
                help="instead of a control segment, demand a UDP datagram "
                     "delivered to this address and port")
a = ap.parse_args()

fail = 0
out = []


def bad(msg):
    global fail
    fail += 1
    out.append(f"  FAIL: {msg}")


link, pkts = read_pcap(a.pcap)

if a.udp_to:
    # The media itself. The point of announcing a port pair is that what
    # arrives on it reaches the subscriber, so this reads the delivered
    # datagram rather than a counter that only says a packet was seen.
    want_ip, want_port = a.udp_to.split(":")
    want_port = int(want_port)
    seen = []
    for raw in pkts:
        p = raw[14:] if link == 1 else raw
        if len(p) < 20 or (p[0] >> 4) != 4 or p[9] != 17:
            continue
        ihl = (p[0] & 0xF) * 4
        total = struct.unpack("!H", p[2:4])[0]
        dst = str(ipaddress.IPv4Address(p[16:20]))
        udp = p[ihl:total]
        dport = struct.unpack("!H", udp[2:4])[0]
        seen.append(f"{dst}:{dport}")
        if dst != want_ip or dport != want_port:
            continue
        if csum(p[:ihl]) != 0:
            bad("invalid IP header checksum on the media datagram")
        # A zero UDP checksum means "not computed" and is legal.
        if struct.unpack("!H", udp[6:8])[0] != 0:
            pseudo = p[12:20] + struct.pack("!BBH", 0, 17, total - ihl)
            if csum(pseudo + udp) != 0:
                bad("invalid UDP checksum on the media datagram")
        out.append(f"  media delivered to {dst}:{dport}, checksums valid")
        print("\n".join(out))
        sys.exit(1 if fail else 0)
    print(f"  FAIL: nothing delivered to {a.udp_to}; the capture holds "
          f"{seen or 'no UDP'}")
    sys.exit(1)

tcp_pkts = []
for raw in pkts:
    p = raw[14:] if link == 1 else raw          # strip Ethernet if present
    if len(p) < 20 or (p[0] >> 4) != 4 or p[9] != 6:
        continue
    tcp_pkts.append(p)

if a.index >= len(tcp_pkts):
    print(f"  FAIL: capture holds {len(tcp_pkts)} TCP packets, "
          f"wanted index {a.index}")
    sys.exit(1)

p = tcp_pkts[a.index]
ihl = (p[0] & 0xF) * 4
total = struct.unpack("!H", p[2:4])[0]
src = str(ipaddress.IPv4Address(p[12:16]))
dst = str(ipaddress.IPv4Address(p[16:20]))
tcp = p[ihl:total]
thl = (tcp[12] >> 4) * 4
payload = tcp[thl:]
seq, ack = struct.unpack("!II", tcp[4:12])
sport, dport = struct.unpack("!HH", tcp[0:4])

out.append(f"  {src}:{sport} -> {dst}:{dport}  seq {seq} ack {ack}  "
           f"{len(payload)} bytes of payload")

# The length the header claims has to be the length that arrived. A rewrite
# that grew the payload and left ip.length alone produces a packet the
# receiver truncates, which looks like a corrupt header rather than a NAT bug.
if total != len(p):
    bad(f"IP total length says {total}, the packet is {len(p)} bytes")
if csum(p[:ihl]) != 0:
    bad("invalid IP header checksum")
pseudo = p[12:20] + struct.pack("!BBH", 0, 6, total - ihl)
if csum(pseudo + tcp) != 0:
    bad("invalid TCP checksum (the peer would drop the segment silently)")
else:
    out.append("    IP and TCP checksums: valid")

text = payload.decode("latin-1")
m = re.search(r"client_port=(\d+)-(\d+)", text)
found = f"{m.group(1)}-{m.group(2)}" if m else None
if found:
    out.append(f"    client_port={found}")

if a.print_sport:
    print(sport)
    sys.exit(0)

if a.print_client_port:
    print(found or "")
    sys.exit(0 if found else 1)

if a.client_port is not None:
    if found != a.client_port:
        bad(f"client_port={found}, expected {a.client_port}")

if a.client_port_in is not None:
    lo_b, hi_b = (int(x) for x in a.client_port_in.split("-"))
    if not m:
        bad("no client_port in the payload")
    else:
        lo, hi = int(m.group(1)), int(m.group(2))
        if not (lo_b <= lo <= hi_b and lo_b <= hi <= hi_b):
            bad(f"client_port {lo}-{hi} is outside the block {lo_b}-{hi_b}")
        if hi != lo + 1:
            bad(f"client_port {lo}-{hi} is not a consecutive pair")
        # RFC 3550 s11: RTP on the even port, RTCP on the one above. Camera
        # firmware assumes it, and when it is broken the symptom is "no
        # video", which nobody attributes to the NAT.
        if lo % 2:
            bad(f"the RTP port {lo} is odd")
        if not fail:
            out.append(f"    {lo}-{hi}: consecutive, RTP even, inside the "
                       f"subscriber's block")

for s in a.contains:
    if s not in text:
        bad(f"payload does not contain {s!r}")
for s in a.not_contains:
    if s in text:
        bad(f"payload still contains {s!r}")

if a.seq is not None and seq != a.seq:
    bad(f"sequence number {seq}, expected {a.seq}")
if a.ack is not None and ack != a.ack:
    bad(f"acknowledgement {ack}, expected {a.ack}")

print("\n".join(out))
sys.exit(1 if fail else 0)
