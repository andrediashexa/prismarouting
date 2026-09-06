#!/usr/bin/env python3
"""
Reads the TCP segments out of a pcap and prints one line per segment, after
checking everything that a counter cannot see.

This exists for the same reason lab/checkicmp.py does. A rewritten payload
with a wrong checksum passes every counter in the dataplane and is discarded
by the receiver's stack; the CGNAT reports a successful rewrite and the
subscriber's transfer hangs. Only the packet on the wire settles it.

The FTP ALG adds a second thing a counter cannot see: rewriting an ASCII
address changes the LENGTH of the payload, so the IP total length has to
change with it. A packet whose header claims a length the payload does not
have is either truncated or padded by whatever reads it next.

  checkftp.py <pcap>

Exit status is 0 only when every segment in the capture passes every check,
so a lab can use it directly as an assertion.

Output, one line per segment:

  pkt 0 src=... dst=... sport=.. dport=.. seq=.. ack=.. flags=PA \\
      ipsum=ok tcpsum=ok iplen=ok paylen=24 payload='PORT 200,150,0,17,4,210\\r\\n'
"""
import ipaddress, struct, sys

FLAG_NAMES = [(0x01, "F"), (0x02, "S"), (0x04, "R"), (0x08, "P"),
              (0x10, "A"), (0x20, "U")]


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


def show(b):
    """Payload as a single printable token, escapes and all."""
    out = []
    for c in b:
        if c == 0x0D:
            out.append("\\r")
        elif c == 0x0A:
            out.append("\\n")
        elif 0x20 <= c < 0x7F:
            out.append(chr(c))
        else:
            out.append(f"\\x{c:02x}")
    return "".join(out)


link, pkts = read_pcap(sys.argv[1])
fail, seen = 0, 0

for i, raw in enumerate(pkts):
    p = raw[14:] if link == 1 else raw
    if len(p) < 20 or (p[0] >> 4) != 4 or p[9] != 6:
        continue
    hlen = (p[0] & 0xF) * 4
    total = struct.unpack("!H", p[2:4])[0]
    tcp = p[hlen:total] if total <= len(p) else p[hlen:]
    if len(tcp) < 20:
        continue
    seen += 1

    doff = (tcp[12] >> 4) * 4
    payload = tcp[doff:]
    flags = "".join(n for bit, n in FLAG_NAMES if tcp[13] & bit)

    ipsum = "ok" if csum(p[:hlen]) == 0 else "BAD"
    # The pseudo-header is what makes a TCP checksum depend on the addresses
    # the CGNAT just rewrote, which is exactly the part an incremental update
    # gets wrong.
    pseudo = p[12:20] + struct.pack("!BBH", 0, 6, len(tcp))
    tcpsum = "ok" if csum(pseudo + tcp) == 0 else "BAD"
    # A rewrite that changes the payload length and not this is a packet that
    # claims to be a size it is not.
    iplen = "ok" if total == len(p) else f"BAD({total}!={len(p)})"

    for name, value in (("ipsum", ipsum), ("tcpsum", tcpsum), ("iplen", iplen)):
        if value != "ok":
            fail += 1
            print(f"  FAIL: packet {i} {name}={value}")

    print(f"  pkt {i} src={ipaddress.IPv4Address(p[12:16])} "
          f"dst={ipaddress.IPv4Address(p[16:20])} "
          f"sport={struct.unpack('!H', tcp[0:2])[0]} "
          f"dport={struct.unpack('!H', tcp[2:4])[0]} "
          f"seq={struct.unpack('!I', tcp[4:8])[0]} "
          f"ack={struct.unpack('!I', tcp[8:12])[0]} "
          f"flags={flags} ipsum={ipsum} tcpsum={tcpsum} iplen={iplen} "
          f"paylen={len(payload)} payload='{show(payload)}'")

if not seen:
    fail += 1
    print("  FAIL: no TCP segment in the capture")

sys.exit(1 if fail else 0)
