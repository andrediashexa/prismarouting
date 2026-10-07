#!/usr/bin/env python3
"""A remote syslog collector for lab/system-logging.sh (#588 phase 3).

  syslog_sink.py udp|tcp <bind address> <port> <out file>

Appends every message it receives to <out file>, one a line: a UDP datagram
as it came, a TCP stream cut by RFC 6587 octet counting - the length, a
space, then that many bytes - which is how the box frames it. A TCP frame
whose length does not parse is written as "BAD FRAME <bytes>", so a lab sees
framing it did not expect instead of a line glued to the next. Runs until it
is killed; one TCP connection at a time, accepted again when it closes.
"""
import socket
import sys

proto, bind, port, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]


def put(msg):
    with open(out, "ab") as fh:
        fh.write(msg.replace(b"\n", b"\\n") + b"\n")


if proto == "udp":
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((bind, port))
    while True:
        put(s.recv(65535))
else:
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((bind, port))
    s.listen(1)
    while True:
        c, _ = s.accept()
        buf = b""
        while True:
            chunk = c.recv(65536)
            if not chunk:
                break
            buf += chunk
            while b" " in buf:
                n, _, rest = buf.partition(b" ")
                if not n.isdigit():
                    put(b"BAD FRAME " + buf)
                    buf = b""
                    break
                if len(rest) < int(n):
                    break
                put(rest[:int(n)])
                buf = rest[int(n):]
        c.close()
