#!/usr/bin/env python3
"""
`system upgrade`'s download against a releases server that misbehaves (#602).

WHAT WAS SEEN. On 10.34.200.166, 2026-10-11, the one connection the VTY
opened to the releases' front ran at about 50 KB/s for 16 minutes while curl
on the same box had the whole 189 MB in 21 s; the VTY printed nothing the
whole time. Two days earlier the same box downloaded a package that did not
match its sha256 - refused, correctly - and the next try matched.

THE SERVER HERE does what that front did, one connection at a time: each
request for a package takes the next entry of PLAN - throttle, stall, cut the
body, ignore Range, serve a corrupt byte, answer 500 - and records what it
was asked (the Range, the address it was reached on, the bytes it sent). Two
of them listen on the same port of 127.0.0.1 and 127.0.0.2, and the name
`releases.test` resolves to both, which is what a front with several edge
addresses is to a box.

The constants of the download are made small (a window of 1 s, a stall of
1 s), so that each behaviour is seconds and not minutes; what is measured is
the rule, not the numbers. Every box was watched going red against the
mutation named beside it before the mutation was taken back out.
"""
import contextlib
import hashlib
import http.server
import io
import os
import re
import shutil
import socket
import sys
import tarfile
import tempfile
import threading
import time

_root = tempfile.mkdtemp(prefix="upgrade-download-")
import atexit  # noqa: E402
atexit.register(shutil.rmtree, _root, True)
os.environ["PRISMA_CONFIG"] = os.path.join(_root, "prisma-routing.conf")
os.environ["PRISMA_CONFIRM"] = os.path.join(_root, "confirm-pending")
os.environ["PRISMA_ROLLBACK"] = os.path.join(_root, "rollback")
os.environ["PRISMA_VERSION_FILE"] = os.path.join(_root, "VERSION-stamp")
os.environ["PRISMA_EVENTS"] = os.path.join(_root, "events.log")
os.environ["PRISMA_EVENTS_STATE"] = os.path.join(_root, "events.state")
os.environ["PRISMA_DOORS"] = os.path.join(_root, "no-doors.sock")
os.environ["PRISMA_UPGRADE_WORK"] = os.path.join(_root, "work")
os.makedirs(os.environ["PRISMA_UPGRADE_WORK"])
with open(os.environ["PRISMA_VERSION_FILE"], "w") as _f:
    _f.write("0.99.7\n")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src", "prismash"))
import prismash  # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


def driven(fn, *a, **k):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rv = fn(*a, **k)
    return rv, out.getvalue()


# ------------------------------------------------------------------ server

REL = os.path.join(_root, "releases")
os.makedirs(REL)
PLAN = []          # what each next package request does; empty: "ok"
ASKED = []         # (address reached, Range asked, status, bytes sent)
LOCK = threading.Lock()
CHUNK = 32 * 1024


class Releases(http.server.BaseHTTPRequestHandler):
    """A releases directory whose packages misbehave as PLAN says."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = os.path.join(REL, self.path.lstrip("/"))
        if not os.path.isfile(path):
            self.send_error(404)
            return
        with open(path, "rb") as f:
            body = f.read()
        if path.endswith(".sha256"):
            self.send(200, body, {})
            return
        with LOCK:
            how = PLAN.pop(0) if PLAN else "ok"
        word, _, arg = how.partition(":")
        rng = self.headers.get("Range")
        where = self.connection.getsockname()[0]
        if word == "500":
            ASKED.append([where, rng, 500, 0])
            self.send(500, b"busy", {})
            return
        if word == "corrupt":
            body = bytes([body[0] ^ 0xFF]) + body[1:]
        start = 0
        m = re.match(r"bytes=(\d+)-$", rng or "")
        if m and word != "norange":
            start = int(m.group(1))
        part = body[start:]
        status = 206 if start else 200
        headers = {"ETag": '"pkg-1"'}
        if start:
            headers["Content-Range"] = f"bytes {start}-{len(body) - 1}/{len(body)}"
        rec = [where, rng, status, 0]
        ASKED.append(rec)      # before the body: a stalled one is seen too
        self.send(status, part, headers, how=word, arg=int(arg or 0), rec=rec)

    def send(self, status, body, headers, how="ok", arg=0, rec=None):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        sent = 0
        try:
            while sent < len(body):
                if how == "cut" and sent >= arg:
                    break
                if how == "stall" and sent >= arg:
                    time.sleep(3)
                    break
                slow = how in ("slow", "trickle") and sent >= arg
                if slow:
                    time.sleep(0.25)    # 16 KB a quarter second: 64 KB/s
                end = sent + (CHUNK // 2 if slow else CHUNK)
                if how != "ok" and sent < arg:
                    end = min(end, arg)
                piece = body[sent:end]
                self.wfile.write(piece)
                self.wfile.flush()
                sent += len(piece)
                if rec:
                    rec[3] = sent
        except OSError:
            pass
        if sent < len(body):
            self.close_connection = True
            with contextlib.suppress(OSError):
                self.connection.shutdown(socket.SHUT_RDWR)
        return sent


def serve(address, port=0):
    srv = http.server.ThreadingHTTPServer((address, port), Releases)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


_one = serve("127.0.0.1")
PORT = _one.server_address[1]
_two = serve("127.0.0.2", PORT)


def both(host, port, family=0, kind=0, *rest):
    """releases.test: two edge addresses, 127.0.0.1 first, as a resolver
    that hands the same answer every time does."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port))
            for a in ("127.0.0.1", "127.0.0.2")]


URL = f"http://releases.test:{PORT}"
SIZE = 3 * (1 << 20) + 12345
with open(os.path.join(REL, "big.bin"), "wb") as _f:
    _f.write(os.urandom(SIZE))
with open(os.path.join(REL, "big.bin"), "rb") as _f:
    WANT = hashlib.sha256(_f.read()).hexdigest()

prismash.DOWNLOAD_WINDOW = 1
prismash.DOWNLOAD_FLOOR = 200000
prismash.DOWNLOAD_PROGRESS_EVERY = 0.3
prismash.DOWNLOAD_PAUSE = 0


def fetch(plan, url=URL + "/big.bin", connections=6, stall=1):
    """download_to() of `url` with PLAN set: (sum, bytes, why, lines said,
    requests made, seconds)."""
    PLAN[:] = plan
    ASKED.clear()
    prismash.DOWNLOAD_CONNECTIONS = connections
    said = []
    t0 = time.monotonic()
    got = prismash.download_to(url, os.path.join(_root, "got"), stall,
                               say=said.append, resolve=both)
    return got + (said, list(ASKED), time.monotonic() - t0)


def sent_total(asked):
    return sum(a[3] for a in asked)


# ---------------------------------------------------------------- helpers

print("=== the download's own lines ===")
check(prismash.rate_text(3.14e6) == "3.1 MB/s" and prismash.rate_text(52300)
      == "52 KB/s", f"rate_text: {prismash.rate_text(3.14e6)!r} "
      f"{prismash.rate_text(52300)!r}")
_l = prismash.download_line(117e6, 189e6, 50e3)
check(_l == "  downloaded 117.0 of 189.0 MB at 50 KB/s, about 24 min left",
      f"download_line, a crawl: {_l!r}")
_l = prismash.download_line(45.2e6, 189e6, 9.7e6)
check(_l == "  downloaded 45.2 of 189.0 MB at 9.7 MB/s, about 15 s left",
      f"download_line, as curl did: {_l!r}")
check(prismash.download_line(1e6, None, 2e6) == "  downloaded 1.0 MB at 2.0 MB/s",
      "download_line: a size the server did not say is not invented")
check(len(prismash.download_line(99999e6, 99999e6 * 2, 1)) <= 79,
      "download_line fits the console at its widest")
_out = io.StringIO()
with contextlib.redirect_stdout(_out):
    prismash.say_now("x")
check(_out.getvalue() == "x\n", "say_now prints the line")
ok("progress lines: bytes, of how many, rate, and the time left")

# release_connect: the address the dropped connection was on, tried last.
# Mutation shown red: the sort's key removed - the resolver's order wins,
# and the resume goes back to 127.0.0.1.
_st = {"resolve": both, "avoid": {"127.0.0.1"}, "stall": 2}
_s = prismash.release_connect("releases.test", PORT, _st)
check(_s.getpeername()[0] == "127.0.0.2" and _st["used"] == ["127.0.0.2"],
      f"release_connect avoids the dropped address: {_st}")
_s.close()
_st = {"resolve": both, "stall": 2}
_s = prismash.release_connect("releases.test", PORT, _st)
check(_s.getpeername()[0] == "127.0.0.1", "and takes the first one otherwise")
_s.close()
_dead = {"resolve": lambda h, p, *a: [(socket.AF_INET, socket.SOCK_STREAM, 6,
                                        "", ("127.0.0.1", 1))], "stall": 2}
try:
    prismash.release_connect("x", 1, _dead)
    check(False, "release_connect to nothing raises")
except OSError:
    pass
ok("a new connection resolves again and lands elsewhere when it can")

# download_open / download_attempt / download_body / download_restart, each
# on its own.
_st = {"n": 1000, "tag": '"pkg-1"', "resolve": both, "stall": 2, "used": []}
PLAN[:] = []
ASKED.clear()
_c, _r = prismash.download_open(URL + "/big.bin", _st)
check(_r.status == 206 and _r.getheader("Content-Range")
      == f"bytes 1000-{SIZE - 1}/{SIZE}", f"download_open asks the rest: "
      f"{_r.status} {_r.getheader('Content-Range')}")
_c.close()
check(ASKED[-1][1] == "bytes=1000-", f"with a Range: {ASKED[-1]}")
_f = open(os.path.join(_root, "restart"), "w+b")
_f.write(b"abc")
_st = {"f": _f, "h": hashlib.sha256(b"abc"), "n": 3, "total": 9, "tag": "x"}
prismash.download_restart(_st)
check(_f.tell() == 0 and os.path.getsize(_f.name) == 0 and _st["n"] == 0
      and _st["h"].hexdigest() == hashlib.sha256().hexdigest()
      and _st["total"] is None and _st["tag"] is None,
      f"download_restart empties the file, the sum and the count: {_st}")


class _Gone:
    def read1(self, n):
        raise ConnectionResetError(104, "Connection reset by peer")


_st.update(stall=1)
check(prismash.download_body(_Gone(), _st, 0, print)[0] == "cut",
      "download_body: a reset is a cut")
_st.update(n=0, total=None)
_pkt = [b"a" * 10]
check(prismash.download_body(type("R", (), {"read1": lambda s, n: _pkt.pop()
                                            if _pkt else b""})(), _st, 0,
                             print) == ("end", "") and _st["n"] == 10,
      f"download_body: a body with no length ends at its end: {_st['n']}")
_f.close()
_st = {"n": 0, "h": hashlib.sha256(), "total": None, "tag": None, "stall": 1,
       "resolve": both, "used": [],
       "f": open(os.path.join(_root, "attempt"), "wb")}
check(prismash.download_attempt(URL + "/none", _st, 0, print)
      == ("refused", f"{URL}/none: HTTP 404"),
      "download_attempt: a 404 is refused, not retried")
check(prismash.download_attempt("ftp://x/y", _st, 0, print)[0] == "refused",
      "download_attempt: a URL it does not speak is refused")
check(prismash.download_attempt(URL + "/big.bin", _st, 0, print)
      == ("end", "") and _st["n"] == SIZE
      and _st["h"].hexdigest() == WANT, "download_attempt: a whole file")
_st["f"].close()
ok("each step on its own")

# ------------------------------------------------------------- behaviours

print("=== what the front did, and what the download does about it ===")
h, n, why, said, asked, secs = fetch([])
check((h, n, why) == (WANT, SIZE, "") and len(asked) == 1,
      f"a good server: one connection, the whole file: {n} {why!r} {asked}")

# CUT MID-BODY. Mutation shown red: the Range header not sent - the server
# sends the whole file again and the bytes sent go past one file.
h, n, why, said, asked, secs = fetch(["cut:1048576"])
check((h, n, why) == (WANT, SIZE, ""),
      f"a connection cut at 1 MB: the file is whole: {n} {why!r}")
check([a[1] for a in asked] == [None, "bytes=1048576-"] and asked[1][2] == 206
      and sent_total(asked) == SIZE,
      f"resumed with a Range, and nothing sent twice: {asked}")
check("  the connection ended at 1.0 of 3.2 MB" in said
      and "  resuming at 1.0 MB on a new connection, 2 of 6" in said,
      f"and it says so: {said}")
ok("a connection cut mid-body is resumed where it stopped")

# STALL. Mutation shown red: the socket's timeout not set (st['stall']
# unused) - the read waits out the server's 3 s and the cut, and no line
# names a stall.
h, n, why, said, asked, secs = fetch(["stall:524288"])
check((h, n, why) == (WANT, SIZE, "") and "  nothing arrived for 1 s" in said
      and asked[1][1] == "bytes=524288-" and asked[1][0] == "127.0.0.2",
      f"a stalled connection is dropped after 1 s and resumed on the other "
      f"address: {said} {asked}")
ok("a stall is dropped and resumed")

# SLOW. The first connection gives 256 KB and then 64 KB/s, under the floor
# of 200 KB/s. Mutation shown red: the floor check removed - one connection,
# ~25 s; and the avoid set not filled - the resume lands on 127.0.0.1 again.
h, n, why, said, asked, secs = fetch(["slow:262144"])
check((h, n, why) == (WANT, SIZE, "") and len(asked) == 2
      and asked[0][0] == "127.0.0.1" and asked[1][0] == "127.0.0.2"
      and asked[1][1] and asked[1][2] == 206 and secs < 10,
      f"a crawling connection is dropped and the rest asked of another edge "
      f"address: {asked} in {secs:.1f} s")
check(any("under the floor of 200 KB/s" in s for s in said),
      f"and the rate is said: {said}")
check(any(s.startswith("  downloaded ") and " at " in s for s in said),
      f"with progress lines while it crawled: {said}")
ok("a crawl is dropped and resumed on another address")

# THE LAST CONNECTION IS KEPT however slow: a box whose own link is under
# the floor still finishes. Mutation shown red: the last attempt given the
# floor too - every connection is dropped and the download refused.
_small = os.path.join(REL, "small.bin")
with open(_small, "wb") as _f:
    _f.write(os.urandom(1 << 19))
with open(_small, "rb") as _f:
    _swant = hashlib.sha256(_f.read()).hexdigest()
h, n, why, said, asked, secs = fetch(["trickle:0"] * 3,
                                     url=URL + "/small.bin", connections=3)
check(h == _swant and n == 1 << 19 and len(asked) == 3
      and "  the last one is kept however slow it is" in said,
      f"three crawling connections: the third is kept and finishes: {n} "
      f"{why!r} {said}")
ok("a link slower than the floor is not refused")

# IGNORES RANGE. Mutation shown red: download_restart() not called on a 200
# to a resume - the whole file appended to the first megabyte, a sum that
# is nobody's.
h, n, why, said, asked, secs = fetch(["cut:1048576", "norange"])
check((h, n, why) == (WANT, SIZE, "") and asked[1][1] == "bytes=1048576-"
      and asked[1][2] == 200,
      f"a server that ignores the Range: started again from 0, correctly: "
      f"{n} {asked}")
check("  the server sent the whole file instead of the rest: starting "
      "again from 0" in said, f"and said: {said}")
ok("a server that ignores Range is restarted from zero")

# A 500, then a good answer: retried. And a cut every time: bounded.
# Mutation shown red: the bound one further (range and `last` both) - 7
# requests asked, and the sixth connection dropped for being slow.
h, n, why, said, asked, secs = fetch(["500"])
check(h == WANT and len(asked) == 2, f"a 500 is retried: {asked}")
h, n, why, said, asked, secs = fetch(["cut:100000"] * 10)
check(h is None and n == 0 and len(asked) == 6
      and why.endswith("6 connections, and the last: the connection ended "
                       "at 0.6 of 3.2 MB"),
      f"a cut on every connection: six, and refused, saying why: {why!r} "
      f"{len(asked)}")
check([a[1] for a in asked][1:3] == ["bytes=100000-", "bytes=200000-"],
      f"each one resuming from the last: {[a[1] for a in asked]}")
PLAN.clear()
h, n, why, said, asked, secs = fetch([], url=URL + "/none")
check(h is None and "HTTP 404" in why and len(asked) == 0,
      f"a 404 is not asked six times: {why!r}")
ok("bounded, and a refusal no new connection changes is not retried")

# ------------------------------------------------------ the whole upgrade

print("=== system upgrade over it ===")


def package(version):
    name = f"prisma-routing-{version}-upgrade"
    src = os.path.join(_root, "pkgsrc", name)
    os.makedirs(src, exist_ok=True)
    with open(os.path.join(src, "upgrade"), "w") as f:
        f.write(f"#!/bin/sh\necho UPGRADE-RAN {version}\n")
    with open(os.path.join(src, "payload"), "wb") as f:
        f.write(os.urandom(64 * 1024))
    d = os.path.join(REL, version)
    os.makedirs(d, exist_ok=True)
    tgz = os.path.join(d, name + ".tar.gz")
    with tarfile.open(tgz, "w:gz") as t:
        t.add(src, arcname=name)
    with open(tgz, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    with open(tgz + ".sha256", "w") as f:
        f.write(f"{digest}  {name}.tar.gz\n")


package("0.100.0")
prismash.RELEASES_URL = f"http://127.0.0.1:{PORT}"
prismash.UPGRADE_WORK = os.environ["PRISMA_UPGRADE_WORK"]
_sh = prismash.Shell()
_sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT, prismash.CAP_OPERATOR}
_sh.sys._run = lambda argv, stdin=None, timeout=30: (0, "")
prismash.DOWNLOAD_CONNECTIONS = 6

# CORRUPT ONCE. Mutation shown red: the second download removed - refused
# on the first mismatch, as 0.153.1 did.
PLAN[:] = ["corrupt"]
ASKED.clear()
rv, text = driven(lambda: _sh.run_upgrade("0.100.0"))
check("does not match its published sha256" in text
      and "downloading it again, once, from the start" in text
      and "UPGRADE-RAN 0.100.0" in text and "upgraded to 0.100.0" in text
      and len(ASKED) == 2 and ASKED[1][1] is None,
      f"a corrupt download is downloaded again, whole, and then it is "
      f"installed: {ASKED} {text!r}")
check(os.listdir(prismash.UPGRADE_WORK) == [], "and nothing is left behind")

# CORRUPT TWICE. Mutation shown red: the retry made a loop - asked a third
# time.
PLAN[:] = ["corrupt", "corrupt"]
ASKED.clear()
rv, text = driven(lambda: _sh.run_upgrade("0.100.0"))
_t = " ".join(text.split())
check("error: system upgrade: it does not match its sha256" in text
      and "downloaded twice" in _t and "nothing was changed" in _t
      and "UPGRADE-RAN" not in text and len(ASKED) == 2
      and os.listdir(prismash.UPGRADE_WORK) == [],
      f"corrupt twice: refused, nothing unpacked, nothing left: {ASKED} "
      f"{text!r}")

# A CUT, through the VTY: the operator sees it and the upgrade goes on.
PLAN[:] = ["cut:200"]
rv, text = driven(lambda: _sh.run_upgrade("0.100.0"))
check("resuming at 0.0 MB on a new connection, 2 of 6" in text
      and "upgraded to 0.100.0" in text,
      f"a cut through the VTY: said, resumed, installed: {text!r}")

# EVERY CONNECTION CUT: nothing was changed, and the work directory is gone.
PLAN[:] = ["cut:100"] * 6
rv, text = driven(lambda: _sh.run_upgrade("0.100.0"))
_t = " ".join(text.split())
check("could not be downloaded; nothing was changed" in _t
      and "6 connections, and the last" in _t and "UPGRADE-RAN" not in text
      and os.listdir(prismash.UPGRADE_WORK) == [],
      f"six cuts: refused, nothing changed, nothing left: {text!r}")
for _row in text.splitlines():
    # The URL line is the URL, printed whole to be copied (#252).
    check(len(_row) <= 79 or _row.startswith("downloading "),
          f"every row fits the console: {_row!r}")
ok("system upgrade resumes, retries once on a bad sum, and refuses clean")

_one.shutdown()
_two.shutdown()
print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
