#!/usr/bin/env python3
"""
A syslog collector, for the lab and for an operator checking a box.

WHAT IT IS FOR. `lab/syslog.sh` needs something on the far side of the wire
that reads what the box sent and counts it, and the only honest way to assert
"one line per event" is to have received the lines. It is also what an
operator reaches for before pointing a real collector at a new box: run it,
allocate a block, and read back what arrived.

RFC 6587 OCTET COUNTING is what this reads, because it is what the box writes:
a decimal length, a space, then exactly that many bytes. Framing by newline
would be a second opinion about the wire, and one that disagrees the first
time a message carries a delimiter.

    python3 tools/syslog_collector.py <port> <seconds> [report-file]

It prints, and writes to the report file when one is named:

    lines      <n>
    BLOCK-ALLOC  <n>
    BLOCK-RELEASE <n>
    BOOT-MARKER  <n>
    malformed  <n>
"""
import collections
import socket
import sys
import time


def frames(buf):
    """
    Every complete octet-counted frame in `buf`, and what is left over.

    Returns `(lines, rest, malformed)`. A frame whose length is not a decimal
    is malformed and the rest of the buffer is abandoned with it: a stream
    whose framing has gone wrong cannot be resynchronised by guessing, and a
    collector that guesses stores records that were never sent.
    """
    out, malformed, i = [], 0, 0
    while True:
        at = buf.find(b" ", i)
        if at < 0:
            break
        head = buf[i:at]
        if not head.isdigit():
            malformed += 1
            return out, b"", malformed
        n = int(head)
        if len(buf) < at + 1 + n:
            break                      # the rest has not arrived yet
        out.append(buf[at + 1:at + 1 + n])
        i = at + 1 + n
    return out, buf[i:], malformed


def msgid(line):
    """The MSGID of an RFC 5424 line, or None when it is not one."""
    parts = line.split(b" ", 6)
    if len(parts) < 6 or not parts[0].startswith(b"<"):
        return None
    return parts[5].decode("ascii", "replace")


def serve(port, secs, report=None):
    counts = collections.Counter()
    lines = 0
    malformed = 0
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", port))
    srv.listen(4)
    srv.settimeout(1.0)
    deadline = time.time() + secs
    conns = []
    buffers = {}

    while time.time() < deadline:
        try:
            c, _ = srv.accept()
            c.settimeout(0.5)
            conns.append(c)
            buffers[c] = b""
        except socket.timeout:
            pass
        except OSError:
            break
        for c in list(conns):
            try:
                data = c.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                data = b""
            if not data:
                conns.remove(c)
                continue
            buffers[c] += data
            got, buffers[c], bad = frames(buffers[c])
            malformed += bad
            for line in got:
                lines += 1
                m = msgid(line)
                counts[m if m else "unparsed"] += 1

    out = [f"lines {lines}"]
    for name, n in sorted(counts.items()):
        out.append(f"{name} {n}")
    out.append(f"malformed {malformed}")
    text = "\n".join(out) + "\n"
    sys.stdout.write(text)
    sys.stdout.flush()
    if report:
        with open(report, "w") as f:
            f.write(text)
    return 0


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 601
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 30
    report = sys.argv[3] if len(sys.argv) > 3 else None
    return serve(port, secs, report)


if __name__ == "__main__":
    sys.exit(main())
