#!/usr/bin/env python3
"""
Builds a SIP datagram (IP/UDP/payload, or IP/TCP/payload) in hex, for the
packet generator.

Header lines are given separated by "|", the SDP body the same way, and each
line is terminated with CRLF. Content-Type and Content-Length are appended by
this script from the body it was given, because a lab that had to keep them in
step by hand would eventually not - and a wrong Content-Length is refused by
the ALG, so the lab would be asserting the refusal path while believing it was
asserting the rewrite.

Which is also why --clen exists: forcing a WRONG Content-Length is how the
refusal path is reached on purpose.

--emit-payload writes the payload bytes to a file. That is what lets a check
demand a captured payload be byte-for-byte what was sent, which is the only
honest way to assert "left untouched": comparing a few fields proves nothing
about the fields nobody thought to compare.
"""
import argparse
import ipaddress
import struct
import sys


def csum(b):
    if len(b) % 2:
        b += b"\x00"
    s = sum(struct.unpack(f"!{len(b)//2}H", b))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


ap = argparse.ArgumentParser()
ap.add_argument("src")
ap.add_argument("dst")
ap.add_argument("sport", type=int)
ap.add_argument("dport", type=int)
ap.add_argument("--start", required=True, help="the request or status line")
ap.add_argument("--headers", default="", help='header lines separated by "|"')
ap.add_argument("--body", default="", help='SDP lines separated by "|"')
ap.add_argument("--ctype", default="application/sdp")
ap.add_argument("--no-ctype", action="store_true",
                help="omit Content-Type even though there is a body")
ap.add_argument("--no-clen", action="store_true",
                help="omit Content-Length even though there is a body")
ap.add_argument("--clen", type=int,
                help="force this Content-Length instead of the real one")
ap.add_argument("--tcp", action="store_true",
                help="carry it over TCP, which this ALG must never touch")
ap.add_argument("--emit-payload", metavar="FILE",
                help="write the payload bytes there as well")
ap.add_argument("--print", dest="show", action="store_true",
                help="print the message instead of the packet")
a = ap.parse_args()

body = "".join(line + "\r\n" for line in a.body.split("|")) if a.body else ""
hdrs = [a.start]
if a.headers:
    hdrs += a.headers.split("|")
if body and not a.no_ctype:
    hdrs.append(f"Content-Type: {a.ctype}")
if not a.no_clen:
    n = a.clen if a.clen is not None else len(body.encode())
    hdrs.append(f"Content-Length: {n}")

msg = "".join(h + "\r\n" for h in hdrs) + "\r\n" + body
payload = msg.encode()

if a.emit_payload:
    with open(a.emit_payload, "wb") as f:
        f.write(payload)

if a.show:
    sys.stdout.write(msg)
    raise SystemExit(0)

src = ipaddress.IPv4Address(a.src).packed
dst = ipaddress.IPv4Address(a.dst).packed

if a.tcp:
    proto = 6
    l4 = struct.pack("!HHIIBBHHH", a.sport, a.dport, 1000, 2000, 5 << 4,
                     0x18, 8192, 0, 0)
    pseudo = src + dst + struct.pack("!BBH", 0, proto, len(l4) + len(payload))
    l4 = l4[:16] + struct.pack("!H", csum(pseudo + l4 + payload)) + l4[18:]
else:
    proto = 17
    l4 = struct.pack("!HHHH", a.sport, a.dport, 8 + len(payload), 0)
    pseudo = src + dst + struct.pack("!BBH", 0, proto, len(l4) + len(payload))
    c = csum(pseudo + l4 + payload)
    l4 = l4[:6] + struct.pack("!H", c if c else 0xFFFF)

ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(l4) + len(payload), 0, 0,
                 64, proto, 0, src, dst)
ip = ip[:10] + struct.pack("!H", csum(ip)) + ip[12:]
print("0x" + (ip + l4 + payload).hex())
