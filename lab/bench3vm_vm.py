#!/usr/bin/env python3
"""The two Debian guests of the three-VM bench (#475): the internet and the
subscribers. Copied into each guest by lab/bench3vm.sh and run there.

  bench3vm_vm.py srv <log>              the internet: who-servers on TCP and
                                        UDP 7000, logging what they SEE
  bench3vm_vm.py subd <first> <count>   the subscribers' daemon: one TCP
                                        connection held per subscriber,
                                        driven on 127.0.0.1:9000
  bench3vm_vm.py ctl <command...>       one command to that daemon, its
                                        answer (JSON) on stdout

THE WHO-SERVER is the internet side's half of the evidence. A subscriber
connects from its private address and says which one it is ("SUB
100.64.0.7"); the server writes down the address and port the packet
ARRIVED from - the public one, after the box - and answers with them. So the
log on this guest is "who the internet saw, as whom", taken where the box
cannot touch it, and lab/bench3vm_check.py holds it against the box's own
allocation records.

THE SUBSCRIBERS' DAEMON holds one TCP connection per subscriber that got
through, which is what keeps that subscriber's block allocated: a block is
returned only when its last session is gone and the hold-down has passed.
Each connection is opened with SO_LINGER 0, so a subscriber LEAVING closes
with a reset, and a reset frees the port at once (RFC 5382's transitory
timeout applies to a FIN, not to a RST) - that is what lets a churn round
take seconds instead of four minutes. Each join also sends one UDP datagram
from the same address, so every subscriber that holds a block has used it
for both protocols.
"""
import ipaddress
import json
import random
import socket
import struct
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

SERVER = "198.51.100.1"
WHO_PORT = 7000
CTL = ("127.0.0.1", 9000)


# ------------------------------------------------------------------ the internet

def srv(log):
    lock = threading.Lock()
    out = open(log, "a", buffering=1)

    def note(rec):
        rec["t"] = round(time.time(), 3)
        with lock:
            out.write(json.dumps(rec) + "\n")

    def tcp_client(c, peer):
        try:
            c.settimeout(30)
            line = c.makefile("rb").readline().decode(errors="replace").split()
            if len(line) >= 2 and line[0] == "SUB":
                note({"proto": "tcp", "private": line[1], "pub": peer[0],
                      "port": peer[1]})
                c.sendall(f"SEEN {peer[0]} {peer[1]}\n".encode())
            # Held open, silently, until the subscriber goes: the session is
            # what keeps the block, and that is the subscriber's choice.
            c.settimeout(None)
            while c.recv(4096):
                pass
        except OSError:
            pass
        finally:
            c.close()

    def tcp_loop():
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", WHO_PORT))
        s.listen(1024)
        while True:
            c, peer = s.accept()
            threading.Thread(target=tcp_client, args=(c, peer),
                             daemon=True).start()

    def udp_loop():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("0.0.0.0", WHO_PORT))
        while True:
            data, peer = s.recvfrom(2048)
            words = data.decode(errors="replace").split()
            if len(words) >= 2 and words[0] == "SUB":
                note({"proto": "udp", "private": words[1], "pub": peer[0],
                      "port": peer[1]})
                s.sendto(f"SEEN {peer[0]} {peer[1]}\n".encode(), peer)

    threading.Thread(target=udp_loop, daemon=True).start()
    tcp_loop()


# --------------------------------------------------------------- the subscribers

def sub_addr(first, i):
    """The i-th subscriber's address (1-based), counted from <first>."""
    return str(ipaddress.ip_address(first) + (i - 1))


def join_one(addr, timeout):
    """One subscriber arriving: a TCP connection held open, and a UDP
    datagram, both from its own address. Returns (record, socket or None)."""
    rec = {"private": addr, "ok": False}
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    s.bind((addr, 0))
    s.settimeout(timeout)
    try:
        s.connect((SERVER, WHO_PORT))
        s.sendall(f"SUB {addr}\n".encode())
        words = s.makefile("rb").readline().decode().split()
        if len(words) == 3 and words[0] == "SEEN":
            rec.update(ok=True, local_port=s.getsockname()[1], pub=words[1],
                       port=int(words[2]))
    except OSError as e:
        rec["error"] = type(e).__name__
    if not rec["ok"]:
        s.close()
        return rec, None
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.bind((addr, 0))
    u.settimeout(2)
    for _ in range(3):
        try:
            u.sendto(f"SUB {addr}\n".encode(), (SERVER, WHO_PORT))
            words = u.recv(256).decode().split()
            if len(words) == 3 and words[0] == "SEEN":
                rec.update(udp_pub=words[1], udp_port=int(words[2]))
                break
        except OSError:
            pass
    u.close()
    rec["t"] = round(time.time(), 3)
    return rec, s


def subd(first, count):
    count = int(count)
    held = {}            # address -> (socket, record)
    lock = threading.Lock()
    pool = ThreadPoolExecutor(max_workers=64)

    def pick(words):
        """Subscriber numbers from "1-160 200 7" style arguments."""
        out = []
        for w in words:
            if "-" in w:
                a, b = w.split("-")
                out += range(int(a), int(b) + 1)
            else:
                out.append(int(w))
        return [sub_addr(first, i) for i in out if 1 <= i <= count]

    def do(cmd):
        if not cmd:
            return {"error": "empty command"}
        verb, args = cmd[0], cmd[1:]
        if verb == "join":
            want = [a for a in pick(args) if a not in held]
            res = list(pool.map(lambda a: join_one(a, 8), want))
            with lock:
                for rec, s in res:
                    if s is not None:
                        held[rec["private"]] = (s, rec)
            return {"joined": [r for r, _ in res]}
        if verb == "leave":
            gone = []
            with lock:
                for a in pick(args):
                    if a in held:
                        held.pop(a)[0].close()
                        gone.append(a)
            return {"left": gone}
        if verb == "state":
            with lock:
                return {"held": [r for _, r in held.values()]}
        if verb == "churn":
            # <leave fraction> <arrivals multiple> <seed>: a random part of
            # the holders leaves; the caller waits for the blocks to come
            # back, then calls "arrive" with the same seed.
            frac, seed = float(args[0]), int(args[1])
            rnd = random.Random(seed)
            with lock:
                names = sorted(held)
            n = max(1, int(len(names) * frac))
            leaving = rnd.sample(names, min(n, len(names)))
            with lock:
                for a in leaving:
                    held.pop(a)[0].close()
            return {"left": leaving}
        if verb == "arrive":
            n, seed = int(args[0]), int(args[1])
            rnd = random.Random(seed)
            with lock:
                idle = [sub_addr(first, i) for i in range(1, count + 1)
                        if sub_addr(first, i) not in held]
            want = rnd.sample(idle, min(n, len(idle)))
            res = list(pool.map(lambda a: join_one(a, 8), want))
            with lock:
                for rec, s in res:
                    if s is not None:
                        held[rec["private"]] = (s, rec)
            return {"joined": [r for r, _ in res]}
        return {"error": f"unknown command {verb}"}

    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(CTL)
    s.listen(4)
    while True:
        c, _ = s.accept()
        with c:
            line = c.makefile("rb").readline().decode().split()
            c.sendall((json.dumps(do(line)) + "\n").encode())


def ctl(words):
    c = socket.create_connection(CTL)
    c.sendall((" ".join(words) + "\n").encode())
    data = b""
    while True:
        b = c.recv(65536)
        if not b:
            break
        data += b
    sys.stdout.write(data.decode())
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    verb = sys.argv[1]
    if verb == "srv":
        srv(sys.argv[2])
    elif verb == "subd":
        subd(sys.argv[2], sys.argv[3])
    elif verb == "ctl":
        return ctl(sys.argv[2:])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
