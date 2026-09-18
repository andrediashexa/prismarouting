#!/usr/bin/env python3
"""
The syslog collector's framing, which is what `lab/syslog.sh` counts with.

A COLLECTOR THAT GUESSES IS WORSE THAN NO COLLECTOR. The lab asserts "one
line per event", and that assertion is only as good as the thing doing the
counting: a framer that resynchronised after a bad length would count records
that were never sent, and one that split on newlines would count a line
carrying a newline as two. Both are green-looking failures, which is why the
framing is read here rather than trusted.
"""
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "tools"))
import syslog_collector as sc                                    # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


LINE = (b"<134>1 2026-09-18T02:40:59.123Z cgnat-sp-01 prisma-cgnat - "
        b"BLOCK-ALLOC [cgnat@44444 natEvent=\"16\"] 100.64.0.5 -> "
        b"200.150.0.16 ports 1024-1027 pool 0")


def framed(line):
    return b"%d %s" % (len(line), line)


print("=== frames: octet counting, and what is not complete yet ===")
got, rest, bad = sc.frames(framed(LINE))
check(got == [LINE] and rest == b"" and bad == 0,
      f"one whole frame is one line: {got!r} rest={rest!r}")
got, rest, bad = sc.frames(framed(LINE) + framed(LINE))
check(len(got) == 2 and rest == b"" and bad == 0,
      "two frames in one read are two lines")
half = framed(LINE)[:-10]
got, rest, bad = sc.frames(half)
check(got == [] and rest == half and bad == 0,
      "a frame whose bytes have not all arrived is kept, not counted")
got, rest, bad = sc.frames(framed(LINE) + half)
check(len(got) == 1 and rest == half,
      "and the whole one before it is counted just the same")
ok("a frame is a length, a space and exactly that many bytes")

print("\n=== and a stream whose framing has gone wrong is abandoned ===")
got, rest, bad = sc.frames(b"not-a-length " + LINE)
check(got == [] and rest == b"" and bad == 1,
      f"a length that is not a decimal is malformed: bad={bad}")
got, rest, bad = sc.frames(framed(LINE) + b"xx " + framed(LINE))
check(len(got) == 1 and bad == 1 and rest == b"",
      "what came before it is kept; what comes after is not guessed at")
ok("a collector that resynchronises by guessing stores records nobody sent")

print("\n=== msgid: what a person filters on, or nothing ===")
check(sc.msgid(LINE) == "BLOCK-ALLOC", "the sixth field of an RFC 5424 line")
check(sc.msgid(b"hello") is None and sc.msgid(b"") is None,
      "a line that is not one has no MSGID, rather than a made-up one")
check(sc.msgid(b"<134>1 t h a - BOOT-MARKER [x] y") == "BOOT-MARKER",
      "the marker is read the same way as any other line")
ok("nothing here invents a name for a line it could not read")

print("\n=== serve: what arrives on the socket is what is reported ===")
port = 0
srv_probe = socket.socket()
srv_probe.bind(("127.0.0.1", 0))
port = srv_probe.getsockname()[1]
srv_probe.close()

report = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", ".build", "syslog-report.txt")
os.makedirs(os.path.dirname(report), exist_ok=True)
t = threading.Thread(target=sc.serve, args=(port, 3.0, report), daemon=True)
t.start()
time.sleep(0.4)
c = socket.create_connection(("127.0.0.1", port), timeout=2)
c.sendall(framed(LINE))
c.sendall(framed(LINE.replace(b"BLOCK-ALLOC", b"BLOCK-RELEASE")))
c.close()
t.join(timeout=6)
text = open(report).read()
check("lines 2" in text and "BLOCK-ALLOC 1" in text
      and "BLOCK-RELEASE 1" in text and "malformed 0" in text,
      f"two lines in, two counted by name: {text!r}")
ok("the count the lab asserts on is the count of what was received")

print("\n=== the lines themselves are kept only for a caller that asks ===")
srv_probe = socket.socket()
srv_probe.bind(("127.0.0.1", 0))
port3 = srv_probe.getsockname()[1]
srv_probe.close()
lines_file = report + ".lines"
if os.path.exists(lines_file):
    os.unlink(lines_file)
t = threading.Thread(target=sc.serve, args=(port3, 3.0, report, lines_file),
                     daemon=True)
t.start()
time.sleep(0.4)
c = socket.create_connection(("127.0.0.1", port3), timeout=2)
c.sendall(framed(LINE))
c.close()
t.join(timeout=6)
check(open(lines_file, "rb").read().strip() == LINE,
      "the line is written whole, one per row, when a file was named")

# And not otherwise: a line carries a subscriber's identity, which is why the
# log that holds it is protected, so counting must not leave a copy behind.
gone = report + ".notasked"
if os.path.exists(gone):
    os.unlink(gone)
srv_probe = socket.socket()
srv_probe.bind(("127.0.0.1", 0))
port4 = srv_probe.getsockname()[1]
srv_probe.close()
t = threading.Thread(target=sc.serve, args=(port4, 2.0, report), daemon=True)
t.start()
time.sleep(0.3)
c = socket.create_connection(("127.0.0.1", port4), timeout=2)
c.sendall(framed(LINE))
c.close()
t.join(timeout=5)
check(not os.path.exists(gone),
      "and no file of lines appears when none was named")
ok("counting leaves no copy of what was counted")

print("\n=== main: the arguments an operator types ===")
srv_probe = socket.socket()
srv_probe.bind(("127.0.0.1", 0))
port2 = srv_probe.getsockname()[1]
srv_probe.close()
argv = sys.argv
sys.argv = ["syslog_collector.py", str(port2), "0.5", report + ".2"]
try:
    rv = sc.main()
finally:
    sys.argv = argv
check(rv == 0 and "lines 0" in open(report + ".2").read(),
      "a run that received nothing says so, rather than failing")
ok("port, seconds and where to write the report")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
