#!/usr/bin/env python3
"""
Builds PPTP control messages and enhanced GRE packets, in hex, for the packet
generator.

The packet generator's own syntax cannot express either of these, and neither
can be produced by asking a real PPTP client to cooperate on demand: the whole
point of the test is to choose the Call IDs, including making two subscribers
choose the same one, which is exactly what a real client will not do to order.
"""
import ipaddress, struct, sys

MAGIC = 0x1A2B3C4D
CONTROL = 1


def csum(b):
    if len(b) % 2:
        b += b"\x00"
    s = sum(struct.unpack(f"!{len(b)//2}H", b))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def ip4(src, dst, proto, payload, ident=0):
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), ident, 0, 64,
                     proto, 0, ipaddress.IPv4Address(src).packed,
                     ipaddress.IPv4Address(dst).packed)
    ip = ip[:10] + struct.pack("!H", csum(ip)) + ip[12:]
    return ip + payload


def tcp(src, dst, sport, dport, payload, seq=1000, ack=2000, flags=0x18):
    hdr = struct.pack("!HHIIBBHHH", sport, dport, seq, ack, 5 << 4, flags,
                      8192, 0, 0)
    pseudo = (ipaddress.IPv4Address(src).packed
              + ipaddress.IPv4Address(dst).packed
              + struct.pack("!BBH", 0, 6, len(hdr) + len(payload)))
    c = csum(pseudo + hdr + payload)
    return hdr[:16] + struct.pack("!H", c) + hdr[18:] + payload


def control(msg_type, body):
    """The twelve bytes every PPTP control message starts with, RFC 2637."""
    return (struct.pack("!HHIHH", 12 + len(body), CONTROL, MAGIC, msg_type, 0)
            + body)


def out_call_request(cid, serial=1):
    body = struct.pack("!HHIIII", cid, serial, 2400, 10000000, 3, 3)
    body += struct.pack("!HHHH", 64, 0, 0, 0)[:8]     # window, delay, lengths
    body += b"\x00" * 128                              # phone + subaddress
    return control(7, body)


def out_call_reply(cid, pcid, result=1):
    body = struct.pack("!HHBBHIHHI", cid, pcid, result, 0, 0, 10000000, 64, 0,
                       0)
    return control(8, body)


def call_clear_request(cid):
    return control(12, struct.pack("!HH", cid, 0))


def call_disconnect_notify(cid, result=4):
    body = struct.pack("!HBBHH", cid, result, 0, 0, 0) + b"\x00" * 128
    return control(13, body)


def gre(call_id, version=1, seq=None, payload=b"\x00" * 16):
    """
    Enhanced GRE, RFC 2637 section 4.1. Version 0 produces plain GRE, which is
    the case a CGNAT cannot translate and must say so about.
    """
    flags = 0x20 if version == 1 else 0x00   # K bit
    if seq is not None:
        flags |= 0x10                        # S bit
    hdr = struct.pack("!BBH", flags, version, 0x880B if version else 0x0800)
    hdr += struct.pack("!HH", len(payload), call_id)
    if seq is not None:
        hdr += struct.pack("!I", seq)
    return hdr + payload


def emit(pkt):
    print("0x" + pkt.hex())


cmd = sys.argv[1]

if cmd == "ctrl":
    # ctrl <src> <dst> <sport> <dport> <type> <cid> [<pcid|result>]
    src, dst = sys.argv[2], sys.argv[3]
    sport, dport = int(sys.argv[4]), int(sys.argv[5])
    mtype, cid = int(sys.argv[6]), int(sys.argv[7], 0)
    if mtype == 7:
        msg = out_call_request(cid)
    elif mtype == 8:
        pcid = int(sys.argv[8], 0)
        result = int(sys.argv[9]) if len(sys.argv) > 9 else 1
        msg = out_call_reply(cid, pcid, result)
    elif mtype == 12:
        msg = call_clear_request(cid)
    elif mtype == 13:
        msg = call_disconnect_notify(cid)
    else:
        raise SystemExit(f"no builder for control message type {mtype}")
    emit(ip4(src, dst, 6, tcp(src, dst, sport, dport, msg)))

elif cmd == "syn":
    src, dst = sys.argv[2], sys.argv[3]
    sport, dport = int(sys.argv[4]), int(sys.argv[5])
    emit(ip4(src, dst, 6, tcp(src, dst, sport, dport, b"", flags=0x02)))

elif cmd == "gre":
    # gre <src> <dst> <callid> [version]
    src, dst = sys.argv[2], sys.argv[3]
    cid = int(sys.argv[4], 0)
    version = int(sys.argv[5]) if len(sys.argv) > 5 else 1
    emit(ip4(src, dst, 47, gre(cid, version, seq=1)))

else:
    raise SystemExit(f"unknown command {cmd}")
