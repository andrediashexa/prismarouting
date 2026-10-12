#!/usr/bin/env python3
"""
The subscriber and the upstream host for lab/ha-failover.sh.

Why the far side reports the address, and not the box: what the lab has to
establish is the source address a subscriber's traffic is SEEN with on the
internet, and a counter or a `show` command on the translating box is that
box's own account of itself. So the upstream echoes back the (address, port)
it received the datagram from, and the subscriber learns its public identity
from the reply. The evidence is the packet, and it has crossed the translation
in both directions before anything is asserted.

That also makes the probe a round trip rather than a send: a failover that
carries in2out and loses out2in would pass a one-way check and strand every
subscriber behind it.

Modes:

  udp-echo   <bind-ip> <port> <log>
  udp-probe  <src-ip> <src-port> <dst-ip> <dst-port> <count> <timeout>
  tcp-echo   <bind-ip> <port> <log>
  tcp-probe  <src-ip> <src-port> <dst-ip> <dst-port> <timeout>
  tcp-hold   <src-ip> <src-port> <dst-ip> <dst-port> <flag> <maxwait> <timeout>

The probes print one `result ...` line, which is what the lab reads, and exit
non-zero when they got nothing. tcp-hold opens a connection, announces the
public address it was given, waits for the lab to touch <flag> (which the lab
does after it has caused the failure) and then reports whether that SAME
connection still carries data.
"""

import os
import socket
import sys
import time


def family(address):
    """AF_INET6 for an IPv6 address, AF_INET otherwise: the subscriber of
    lab/ha-nat64.sh is IPv6-only, and the upstream answers in IPv4 (#485)."""
    return socket.AF_INET6 if ":" in address else socket.AF_INET


def log_line(path, text):
    with open(path, "a") as fh:
        fh.write("%.3f %s\n" % (time.time(), text))
        fh.flush()


def udp_echo(bind_ip, port, log):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((bind_ip, port))
    while True:
        data, peer = s.recvfrom(2048)
        seen = "%s:%d" % (peer[0], peer[1])
        log_line(log, "udp %s %s" % (seen, data[:32].decode("ascii", "replace")))
        s.sendto(seen.encode(), peer)


def udp_probe(src_ip, src_port, dst_ip, dst_port, count, timeout):
    ok = 0
    seen = "-"
    for i in range(count):
        s = socket.socket(family(src_ip), socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # One fixed source socket across the whole lab, because the public port
        # a subscriber is given is a property of its private socket and the
        # question is whether that survives. A fresh ephemeral port each time
        # would ask a different question every second.
        s.bind((src_ip, src_port))
        s.settimeout(timeout)
        try:
            s.sendto(b"probe-%d" % i, (dst_ip, dst_port))
            reply, _ = s.recvfrom(2048)
            seen = reply.decode("ascii", "replace").strip()
            ok += 1
        except OSError:
            pass
        finally:
            s.close()
        if i + 1 < count:
            time.sleep(0.2)
    print("result %d/%d %s" % (ok, count, seen))
    return 0 if ok else 1


def tcp_echo(bind_ip, port, log):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((bind_ip, port))
    s.listen(8)
    while True:
        conn, peer = s.accept()
        seen = "%s:%d" % (peer[0], peer[1])
        log_line(log, "tcp-accept %s" % seen)
        if os.fork() == 0:
            s.close()
            try:
                conn.sendall((seen + "\n").encode())
                while True:
                    data = conn.recv(1024)
                    if not data:
                        break
                    conn.sendall(data)
            except OSError:
                pass
            finally:
                conn.close()
            os._exit(0)
        conn.close()


def _connect(src_ip, src_port, dst_ip, dst_port, timeout):
    s = socket.socket(family(src_ip), socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((src_ip, src_port))
    s.settimeout(timeout)
    s.connect((dst_ip, dst_port))
    greeting = s.recv(128).decode("ascii", "replace").strip()
    return s, greeting


def tcp_probe(src_ip, src_port, dst_ip, dst_port, timeout):
    try:
        s, greeting = _connect(src_ip, src_port, dst_ip, dst_port, timeout)
    except OSError as exc:
        print("result fail %s" % exc)
        return 1
    s.close()
    print("result ok %s" % greeting)
    return 0


def tcp_hold(src_ip, src_port, dst_ip, dst_port, flag, maxwait, timeout):
    try:
        s, greeting = _connect(src_ip, src_port, dst_ip, dst_port, timeout)
    except OSError as exc:
        print("public - (could not connect: %s)" % exc)
        print("survived no (there was never a connection)")
        return 1
    print("public %s" % greeting)
    sys.stdout.flush()
    waited = 0.0
    while not os.path.exists(flag) and waited < maxwait:
        time.sleep(0.25)
        waited += 0.25
    if not os.path.exists(flag):
        print("survived unknown (the lab never released the hold)")
        s.close()
        return 1
    try:
        s.settimeout(timeout)
        s.sendall(b"still-there\n")
        data = s.recv(128)
        if data:
            print("survived yes (%s)" % data.decode("ascii", "replace").strip())
            rc = 0
        else:
            print("survived no (the far end closed it)")
            rc = 0
    except OSError as exc:
        print("survived no (%s)" % exc)
        rc = 0
    s.close()
    return rc


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    mode = argv[1]
    a = argv[2:]
    if mode == "udp-echo":
        return udp_echo(a[0], int(a[1]), a[2])
    if mode == "udp-probe":
        return udp_probe(a[0], int(a[1]), a[2], int(a[3]), int(a[4]),
                         float(a[5]))
    if mode == "tcp-echo":
        return tcp_echo(a[0], int(a[1]), a[2])
    if mode == "tcp-probe":
        return tcp_probe(a[0], int(a[1]), a[2], int(a[3]), float(a[4]))
    if mode == "tcp-hold":
        return tcp_hold(a[0], int(a[1]), a[2], int(a[3]), a[4], float(a[5]),
                        float(a[6]))
    print("unknown mode: %s" % mode)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
