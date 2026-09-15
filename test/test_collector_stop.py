#!/usr/bin/env python3
"""
When the collector stops, and whether it says which of the two ended it.

For eight hours on 2026-09-15 the soak's two hardest checks went red on a box
that had done nothing wrong. tools/ipfix_collector.py was given a budget of
`CHURN + 40` seconds, fixed before the run started, and 822 cycles of vppctl
and packet-generator calls drift about 80 seconds past 822 x 35s - so the
budget expired INSIDE the final cycle. It had heard that cycle's allocations
and none of its releases, and reported 104,227 allocations against 104,181
deallocations with 46 open. The box's own counters read 104,227 against
104,227 with zero blocks in use.

A collector outlived by its exporter reports the records it did not wait for
as allocations nobody ever released, which is indistinguishable from a leak in
the log an operator is legally obliged to keep. So the caller now says when
there is nothing more to hear, and the collector says which of the two ended
it - because a run that hit the ceiling anyway has to be readable as such
rather than as a box that lost records.

Driven against a real collector on a real socket, not against its internals: a
stop that works in the function and not on the process would leave the soak
exactly where it was.
"""
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECTOR = os.path.join(REPO, "tools", "ipfix_collector.py")


def _ipfix(*sets):
    body = b"".join(sets)
    return struct.pack("!HHIII", 10, 16 + len(body), 0, 0, 0) + body


def _template(tid, ies):
    fields = b"".join(struct.pack("!HH", ie, ln) for ie, ln in ies)
    return struct.pack("!HH", 2, 8 + len(fields)) + \
        struct.pack("!HH", tid, len(ies)) + fields


def _data(tid, body):
    return struct.pack("!HH", tid, 4 + len(body)) + body


# natEvent(230, 1), sourceIPv4Address(8, 4), portRangeStart/End and the
# observation time: what report_open() needs to pair an allocation with its
# release, which is the pairing the soak's verdict rests on.
_IES = [(230, 1), (8, 4), (225, 4), (361, 2), (362, 2), (323, 8)]


def _rec(event, sub, ports, when):
    return struct.pack("!B", event) + bytes([100, 64, 0, sub]) + \
        bytes([200, 150, 0, 17]) + struct.pack("!HH", ports, ports + 63) + \
        struct.pack("!Q", int(when * 1000))


# The boot marker, on a template of its own, because that is how the exporter
# sends it. Without one the collector cannot tell an allocation still in force
# from one the box died holding, and says UNDECIDABLE rather than guessing -
# so a fixture with no marker would be measuring a different report from the
# one the soak reads.
_MARKER_IES = [(230, 1), (160, 8), (323, 8)]


def _marker(init, when):
    return struct.pack("!B", 0) + struct.pack("!Q", int(init * 1000)) + \
        struct.pack("!Q", int(when * 1000))


def _boot(send, init):
    send(_ipfix(_template(257, _MARKER_IES)))
    send(_ipfix(_data(257, _marker(init, time.time()))))


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def run(secs, stop_file, script):
    """Start a collector, run `script(send)` against it, return its output."""
    port = free_port()
    # -u because the collector's output is read while it runs, to know when it
    # has bound the socket. Block buffering would hold that line until exit,
    # which is after everything this needs it for.
    argv = [sys.executable, "-u", COLLECTOR, str(port), str(secs)]
    if stop_file is not None:
        argv.append(stop_file)
    # To a file rather than a pipe: reading a pipe while the process runs and
    # then calling communicate() returns nothing, because the two readers do
    # not share the stream's state. That cost an hour and made every check
    # here red against a collector that was working.
    log = tempfile.NamedTemporaryFile("w+", suffix=".out", delete=False)
    p = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, text=True)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def said():
        with open(log.name) as f:
            return f.read()

    # The collector binds before it prints, so the line is the signal that the
    # socket is there to receive. A datagram sent before the bind is gone.
    _deadline = time.time() + 15
    while "listening" not in said():
        if time.time() > _deadline or p.poll() is not None:
            break
        time.sleep(0.05)
    t0 = time.time()

    def send(pkt):
        s.sendto(pkt, ("127.0.0.1", port))

    try:
        script(send)
    finally:
        p.wait(timeout=secs + 30)
        s.close()
        out = said()
        log.close()
        os.unlink(log.name)
    return out, time.time() - t0


print("=== the caller says when there is nothing more to hear ===")
with tempfile.TemporaryDirectory() as d:
    stop = os.path.join(d, "collector.stop")

    N = 20

    def script(send):
        send(_ipfix(_template(256, _IES)))
        _boot(send, time.time() - 600)
        for i in range(N):
            send(_ipfix(_data(256, _rec(16, i + 1, 10000 + 64 * i,
                                        time.time()))))
        time.sleep(1.0)
        # A BURST, not one datagram, and then the stop file with no pause.
        # The collector handles one datagram per turn of its loop and checks
        # the file between turns, so one release racing the file is picked up
        # before the file is ever looked at: it would pass with no drain at
        # all. What only the drain can save is the rest of the burst, already
        # in the receive buffer when the loop breaks - which is the shape of
        # the real thing, where a cycle's worth of releases leaves together.
        for i in range(N):
            send(_ipfix(_data(256, _rec(17, i + 1, 10000 + 64 * i,
                                        time.time()))))
        open(stop, "w").close()

    # A budget far longer than the test, so anything that ends it is the file,
    # and short enough that a stop that does not work is a red check rather
    # than a run nobody waits for.
    out_caller, took = run(20, stop, script)

n_before = fail
check("STOPPED BY: the caller" in out_caller,
      f"the collector did not say the caller ended it: {out_caller!r}")
check(took < 15,
      f"the collector ran {took:.0f}s against a 20s budget, so the stop file "
      f"did not end it")
check(f"({N} allocations, {N} deallocations" in out_caller,
      f"the releases already in the receive buffer when the stop file appeared "
      f"were not counted, so the drain loses exactly what the stop file exists "
      f"to save: {out_caller!r}")
check("Every allocation had its matching deallocation." in out_caller,
      f"the books did not balance on a run where they must: {out_caller!r}")
if fail == n_before:
    ok(f"a stop file ends it in {took:.0f}s, and the release racing it is "
       f"still counted: the whole burst in the buffer")

print()
print("=== and says so when its own budget is what ended it ===")
with tempfile.TemporaryDirectory() as d:
    stop = os.path.join(d, "collector.stop")   # never created

    def script(send):
        send(_ipfix(_template(256, _IES)))
        _boot(send, time.time() - 600)
        send(_ipfix(_data(256, _rec(16, 11, 10000, time.time()))))
        # and the release never arrives, because the exporter outlived it

    out_budget, took = run(3, stop, script)

n_before = fail
check("STOPPED BY: its own budget" in out_budget,
      f"a collector that ran out of budget did not say so: {out_budget!r}")
check("(1 allocations, 0 deallocations" in out_budget,
      f"the budget case did not produce the books it is here to produce: "
      f"{out_budget!r}")
check("WARNING:" in out_budget and "records lost" in out_budget,
      f"nothing warned that the books do not balance: {out_budget!r}")
check("STILL IN FORCE" in out_budget,
      f"the allocation the collector never heard the release for was not "
      f"listed as open: {out_budget!r}")
if fail == n_before:
    ok("an allocation whose release arrived after the budget reads as open, "
       "which is what 46 of them did for eight hours")

print()
print("=== which is the whole point: the two are not the same run ===")
# Same traffic, same collector, one ended by the caller and one by its budget,
# and the books differ. If the two ever printed the same thing, the soak could
# not tell a box that lost records from a far side that stopped listening.
with tempfile.TemporaryDirectory() as d:
    stop = os.path.join(d, "collector.stop")

    def late(send):
        send(_ipfix(_template(256, _IES)))
        _boot(send, time.time() - 600)
        send(_ipfix(_data(256, _rec(16, 11, 10000, time.time()))))
        time.sleep(4.0)
        send(_ipfix(_data(256, _rec(17, 11, 10000, time.time()))))
        open(stop, "w").close()

    out_late, _ = run(2, None, late)

n_before = fail
check("STOPPED BY: its own budget" in out_late,
      f"the no-stop-file form no longer reports its own budget: {out_late!r}")
check("0 deallocations" in out_late,
      f"the release that arrived after the budget was counted anyway, so the "
      f"budget is not really the end: {out_late!r}")
if fail == n_before:
    ok("a release that arrives after the budget is lost, and the report names "
       "the budget as the reason")

print()
print("=== and the soak reads that line, on all three of those runs ===")
# The collector saying which of the two ended it is worth nothing if the lab
# does not ask. This runs THE LAB'S OWN reader and check - cut out of
# lab/soak.sh rather than copied here, so a copy cannot drift - against the
# three reports above.
SOAK = os.path.join(REPO, "lab", "soak.sh")
VERDICT = os.path.join(REPO, "lab", "verdict.sh")


def cut(first, last):
    """The lines from the one matching `first` to the one matching `last`.

    Empty when the end anchor is never found, because `sed -n '/a/,/b/p'`
    prints to the end of the file instead and the caller would then be
    measuring the whole lab.
    """
    out, taking = [], False
    for line in open(SOAK):
        if not taking and first in line:
            taking = True
        if taking:
            out.append(line)
            if last in line:
                return "".join(out)
    return ""


READER = cut("COL_STOPPED_BY=", "COL_STOPPED_BY=")
CHECK = cut('expect_in "and the collector was stopped by this lab',
            "COL_STOPPED_BY:-")
n_before = fail
check(READER != "" and CHECK != "",
      f"the reader or the check was not found in lab/soak.sh, so this section "
      f"would have measured an empty script: {READER!r} {CHECK!r}")


def reds(report):
    """What lab/soak.sh's own check makes of this collector report."""
    script = ('. "%s" >/dev/null 2>&1\nCOL=$(cat)\n%s%s\n'
              'echo "$VERDICT_FAILURES"' % (VERDICT, READER, CHECK))
    r = subprocess.run(["sh", "-c", script], input=report,
                       capture_output=True, text=True)
    lines = r.stdout.strip().splitlines()
    return lines[-1] if lines else "?"


if fail == n_before:
    check(reds(out_caller) == "0",
          f"the lab called a run the caller ended a failure: "
          f"{reds(out_caller)} red")
    check(reds(out_budget) == "1",
          f"the lab read a collector stopped by its own budget as a clean run, "
          f"which is the eight hours this is all about: {reds(out_budget)} red")
    check(reds(out_late) == "1",
          f"same, on the run with no stop file at all: {reds(out_late)} red")
    if fail == n_before:
        ok("the lab passes the run it ended itself and refuses the two that "
           "ran out of budget")

print()
# The verdict lines tools/run-tests.sh reads. A suite that ends without one is
# NO VERDICT, which is a killed process and not a pass - so the words matter
# and "all checks passed" is not one of them.
if fail:
    print(f"{fail} FAILURES")
    sys.exit(1)
print("ALL TESTS PASSED")
