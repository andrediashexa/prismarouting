#!/usr/bin/env python3
"""
A NAS that sends accounting, for the lab and for an operator checking a box.

WHAT IT IS FOR. `lab/syslog.sh` needs something that speaks RFC 2866 at the
box the way a BNG does, and an operator pointing this product at a new
accounting stream wants to know the secret is right before they wait for a
subscriber to connect. It sends one Accounting-Request and reports what came
back.

    python3 tools/radius_nas.py <host> <port> <secret> start <ip> <user>
    python3 tools/radius_nas.py <host> <port> <secret> stop  <ip> [user]

<ip> may be an IPv6 prefix, 2001:db8:1200:ab00::/56: it is then sent as
Delegated-IPv6-Prefix (RFC 4818) instead of Framed-IP-Address, which is how a
BNG names an IPv6-only subscriber of a NAT64 pool (#485).

IT IS A SECOND IMPLEMENTATION ON PURPOSE. The box builds and checks these
packets in C; this builds them in Python, from the RFC rather than from that
code. Two implementations of one rule that agree are evidence; one
implementation checked against itself is not.
"""
import hashlib
import ipaddress
import socket
import sys

ACCT_REQUEST = 4
ACCT_RESPONSE = 5
STATUS = {"start": 1, "stop": 2, "interim": 3}


def attr(kind, value):
    """One RADIUS attribute: type, length INCLUDING these two bytes, value."""
    if len(value) > 253:
        raise ValueError("an attribute value is at most 253 bytes")
    return bytes([kind, len(value) + 2]) + value


def acct_request(ident, secret, status, ip=None, user=None):
    """
    An Accounting-Request, signed the way RFC 2866 section 3 says:
    MD5(Code + Identifier + Length + 16 zero octets + Attributes + Secret).
    """
    attrs = b""
    if user is not None:
        attrs += attr(1, user.encode())
    if ip is not None and ":" in ip:
        # Delegated-IPv6-Prefix: Reserved, Prefix-Length, and only the
        # octets the length needs (RFC 4818 section 3).
        net = ipaddress.IPv6Network(ip, strict=True)
        n = (net.prefixlen + 7) // 8
        attrs += attr(123, bytes([0, net.prefixlen])
                      + net.network_address.packed[:n])
    elif ip is not None:
        attrs += attr(8, socket.inet_aton(ip))
    attrs += attr(40, int(status).to_bytes(4, "big"))
    length = 20 + len(attrs)
    head = bytes([ACCT_REQUEST, ident]) + length.to_bytes(2, "big")
    body = head + bytes(16) + attrs
    auth = hashlib.md5(body + secret.encode()).digest()
    return head + auth + attrs, auth


def response_is_valid(pkt, request_auth, secret, ident):
    """
    Is this the box's answer to that request?

    The Response Authenticator is MD5 over the response with the REQUEST's
    authenticator in its place, plus the secret - so a box that signed its
    reply over its own zeros, or with another secret, is caught here rather
    than reported as a working accounting server.
    """
    if len(pkt) < 20 or pkt[0] != ACCT_RESPONSE or pkt[1] != ident:
        return False
    want = hashlib.md5(pkt[:4] + request_auth + pkt[20:]
                       + secret.encode()).digest()
    return pkt[4:20] == want


def send(host, port, secret, status, ip, user, timeout=3.0):
    ident = 7
    pkt, auth = acct_request(ident, secret, status, ip, user)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    s.sendto(pkt, (host, port))
    try:
        data, _ = s.recvfrom(4096)
    except socket.timeout:
        print("no response: the box did not answer within "
              f"{timeout:g}s (a wrong secret is answered with silence)")
        return 2
    if response_is_valid(data, auth, secret, ident):
        print(f"accounting-response for id {ident}, signed with this secret")
        return 0
    print("a response arrived and it is not this request's: "
          f"{data[:20].hex()}")
    return 1


def main():
    if len(sys.argv) < 6:
        print(__doc__.strip())
        return 2
    host, port, secret, what, ip = sys.argv[1:6]
    user = sys.argv[6] if len(sys.argv) > 6 else None
    if what not in STATUS:
        print(f"'{what}' is not one of: {', '.join(sorted(STATUS))}")
        return 2
    return send(host, int(port), secret, STATUS[what], ip, user)


if __name__ == "__main__":
    sys.exit(main())
