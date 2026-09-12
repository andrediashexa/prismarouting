#!/usr/bin/env python3
"""
Checks a SIP datagram read from a pcap.

The same lesson lab/checkicmp.py and lab/checkrtsp.py exist for, with one more
failure a SIP ALG can produce that neither of those can. Rewriting the payload
changes its length, so:

  * the IP total length and the UDP length both have to move with it, and a
    receiver that reads the UDP length gets a message that stops early;
  * the UDP checksum covers the payload, so it has to be recomputed, and a
    wrong one is DROPPED SILENTLY by the far end's stack while every counter
    in the dataplane reports a rewrite;
  * Content-Length is a third length over the same bytes, invisible to IP and
    to UDP, and a body that grew under a Content-Length that did not is a 400
    Bad Request from a proxy or silence from a phone.

All three are checked on every invocation, before anything the caller asked
about, because all three are silent.
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
ap.add_argument("--tcp", action="store_true",
                help="expect TCP rather than UDP")
ap.add_argument("--from", dest="src", metavar="IP:PORT")
ap.add_argument("--to", dest="dst", metavar="IP:PORT")
ap.add_argument("--via", metavar="HOST:PORT",
                help="the topmost Via sent-by, exactly")
ap.add_argument("--contact", metavar="HOST:PORT",
                help="the Contact URI host, exactly")
ap.add_argument("--sdp-conn", metavar="IP", help="the SDP c= address")
ap.add_argument("--sdp-media", metavar="PORT", help="the first SDP m= port")
ap.add_argument("--contains", action="append", default=[])
ap.add_argument("--not-contains", action="append", default=[])
ap.add_argument("--payload-equals", metavar="FILE",
                help="the payload must be byte for byte this file")
ap.add_argument("--clen-lies", action="store_true",
                help="the message was BUILT with a wrong Content-Length; "
                     "demand it is still wrong, i.e. nothing tried to fix it")
a = ap.parse_args()

fail = 0
out = []


def bad(msg):
    global fail
    fail += 1
    out.append(f"  FAIL: {msg}")


link, pkts = read_pcap(a.pcap)
want_proto = 6 if a.tcp else 17

hits = []
for raw in pkts:
    p = raw[14:] if link == 1 else raw          # strip Ethernet if present
    if len(p) < 20 or (p[0] >> 4) != 4 or p[9] != want_proto:
        continue
    hits.append(p)

if not hits:
    print(f"  FAIL: the capture holds no IPv4 "
          f"{'TCP' if a.tcp else 'UDP'} packet")
    sys.exit(1)

p = hits[0]
ihl = (p[0] & 0xF) * 4
total = struct.unpack("!H", p[2:4])[0]
src = str(ipaddress.IPv4Address(p[12:16]))
dst = str(ipaddress.IPv4Address(p[16:20]))

# The length the header claims has to be the length that arrived. A rewrite
# that grew the payload and left ip.length alone produces a packet the
# receiver truncates, which looks like a corrupt message rather than a NAT bug.
if total != len(p):
    bad(f"IP total length says {total}, the packet is {len(p)} bytes")
if csum(p[:ihl]) != 0:
    bad("invalid IP header checksum")

l4 = p[ihl:total]
sport, dport = struct.unpack("!HH", l4[0:4])

if a.tcp:
    thl = (l4[12] >> 4) * 4
    payload = l4[thl:]
    pseudo = p[12:20] + struct.pack("!BBH", 0, 6, total - ihl)
    if csum(pseudo + l4) != 0:
        bad("invalid TCP checksum")
    else:
        out.append("    IP and TCP checksums: valid")
else:
    ulen = struct.unpack("!H", l4[4:6])[0]
    if ulen != total - ihl:
        bad(f"UDP length says {ulen}, the datagram carries {total - ihl}")
    payload = l4[8:ulen]
    # A zero checksum means "not computed" and is legal IPv4 UDP; this ALG
    # always computes one, so a zero here after a rewrite is worth seeing.
    if struct.unpack("!H", l4[6:8])[0] == 0:
        out.append("    UDP checksum: absent (legal, not computed)")
    else:
        pseudo = p[12:20] + struct.pack("!BBH", 0, 17, total - ihl)
        if csum(pseudo + l4) != 0:
            bad("invalid UDP checksum "
                "(the peer's stack would drop it without a word)")
        else:
            out.append("    IP and UDP checksums: valid")

out.insert(0, f"  {src}:{sport} -> {dst}:{dport}  "
              f"{len(payload)} bytes of payload")

text = payload.decode("latin-1")

# Content-Length, the third length over the same bytes. Checked whenever the
# message declares one, on every invocation.
#
# --clen-lies inverts it, and is not a way of skipping the check. A message
# injected with a Content-Length that disagrees with its body is one the ALG
# must REFUSE, and a refusal that quietly corrected the header on the way past
# would be indistinguishable from one that left it alone if this simply looked
# the other way.
m = re.search(r"\r\n(?:Content-Length|l)\s*:\s*(\d+)\r\n", text, re.I)
if m:
    body = text.split("\r\n\r\n", 1)
    if len(body) == 2:
        agrees = int(m.group(1)) == len(body[1])
        if a.clen_lies and agrees:
            bad(f"Content-Length {m.group(1)} now matches the {len(body[1])}"
                "-byte body; it was injected wrong and must have stayed wrong")
        elif not a.clen_lies and not agrees:
            bad(f"Content-Length says {m.group(1)}, the body is "
                f"{len(body[1])} bytes")
        else:
            out.append(f"    Content-Length {m.group(1)}: "
                       f"{'still disagrees with' if a.clen_lies else 'matches'}"
                       f" the {len(body[1])}-byte body")

via = re.search(r"\r\n(?:Via|v)\s*:\s*SIP/2\.0/UDP\s+([^;,\s]+)", text, re.I)
contact = re.search(r"\r\n(?:Contact|m)\s*:[^\r\n]*?sips?:(?:[^@\r\n]*@)?"
                    r"([^;,>?\r\n\s]+)", text, re.I)
conn = re.search(r"\r\nc=IN IP4 ([^\r\n]+)", text)
media = re.search(r"\r\nm=\S+ (\d+)", text)
for label, mm in (("Via sent-by", via), ("Contact host", contact),
                  ("SDP c=", conn), ("SDP m= port", media)):
    if mm:
        out.append(f"    {label}: {mm.group(1)}")


def want(label, mm, expected):
    got = mm.group(1) if mm else None
    if got != expected:
        bad(f"{label} is {got!r}, expected {expected!r}")


if a.src:
    ip_, port_ = a.src.rsplit(":", 1)
    if src != ip_ or sport != int(port_):
        bad(f"came from {src}:{sport}, expected {a.src}")
if a.dst:
    ip_, port_ = a.dst.rsplit(":", 1)
    if dst != ip_ or dport != int(port_):
        bad(f"went to {dst}:{dport}, expected {a.dst}")
if a.via is not None:
    want("the topmost Via sent-by", via, a.via)
if a.contact is not None:
    want("the Contact URI host", contact, a.contact)
if a.sdp_conn is not None:
    want("the SDP connection address", conn, a.sdp_conn)
if a.sdp_media is not None:
    want("the SDP media port", media, a.sdp_media)

for s in a.contains:
    if s not in text:
        bad(f"payload does not contain {s!r}")
for s in a.not_contains:
    if s in text:
        bad(f"payload still contains {s!r}")

if a.payload_equals:
    with open(a.payload_equals, "rb") as f:
        expected = f.read()
    if payload != expected:
        bad(f"the payload is not what was sent: {len(payload)} bytes out, "
            f"{len(expected)} in")
    else:
        out.append(f"    payload identical to what was sent "
                   f"({len(payload)} bytes)")

print("\n".join(out))
sys.exit(1 if fail else 0)
