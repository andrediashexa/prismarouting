#!/usr/bin/env python3
"""
How the shipped collector reads an allocation that has no release.

`appliance/install.sh` puts `tools/ipfix_collector.py` on the box as
`/usr/lib/hexarouting/ipfix-collector`, so this is not lab scaffolding: it is
the program that reads the record a court order asks for. An open allocation
has two opposite meanings - the subscriber holds those ports now, or the box
was replaced while holding them and never observed the end - and the boot
marker is the only thing that tells them apart.

The timeline below is the one from the capture in #223, to the second, rather
than a tidy one written to make a point. `lab/ipfix-stop.sh` exercises the same
rule against three real boxes, and it caught this defect once in a release
suite and passed the other times: the window is the marker interval, so whether
the lab sees it depends on when a periodic marker lands. A fixed timeline fails
on the rule instead of on the machine's mood.
"""
import contextlib
import io
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import importlib.util                                           # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "ipfix_collector",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools",
                 "ipfix_collector.py"))
col = importlib.util.module_from_spec(_spec)
# Timed, because this import is also an assertion. The file ended with a bare
# `main()` call, so loading it BOUND udp/4739 and listened for 25 seconds -
# inside `make test`, on a box where a lab may already hold that port. The
# collector's own rule went three days unguarded for want of a test, and a
# module that cannot be read without being run is a module nothing can assert
# about. A guarded import is milliseconds; an unguarded one is the collector's
# whole run.
_t0 = time.monotonic()
_spec.loader.exec_module(col)
_import_secs = time.monotonic() - _t0

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


def at(hhmmss):
    """Seconds since midnight, which is all these comparisons need."""
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return (h * 3600 + m * 60 + s) * 1000.0


def marker(observed, init):
    return {"observationTimeMilliseconds": at(observed),
            "systemInitTimeMilliseconds": at(init)}


def alloc(observed, sub):
    return {"observationTimeMilliseconds": at(observed), "natEvent": 16,
            "sourceIPv4Address": sub, "postNATSourceIPv4Address": "200.150.0.16",
            "portRangeStart": 1024, "portRangeEnd": 17407}


def report(rs):
    col.records[:] = rs
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        col.report_open()
    return out.getvalue()


def verdict(text, sub):
    for line in text.split("\n"):
        if sub in line:
            return line.split(": ", 1)[1].split(" (")[0] if ": " in line else line
    return "<no line for that subscriber>"


# The capture from #223. Three boxes share one collector. The markers are
# periodic, and the one that matters is the gap: the exporter reinitialised at
# 05:18:17 and its first marker did not land until 05:18:52, while 100.64.0.9
# was given its block at 05:18:28 - inside that gap, by the new incarnation.
CAPTURE = [
    marker("05:16:40", "05:16:27"),
    marker("05:17:10", "05:16:27"),
    alloc("05:17:35", "100.64.0.7"),          # killed while holding it
    marker("05:17:59", "05:17:24"),
    alloc("05:18:28", "100.64.0.9"),          # in the gap, and still running
    marker("05:18:52", "05:18:17"),
    marker("05:19:22", "05:18:17"),
    marker("05:19:52", "05:18:17"),
]

print("=== importing the collector does not start one (#223) ===")
check(_import_secs < 5.0,
      f"importing tools/ipfix_collector.py took {_import_secs:.1f}s, which is "
      f"a collector running: the file must call main() only under "
      f"__name__ == '__main__', or every reader of it becomes a listener")
if _import_secs < 5.0:
    ok(f"the module loads in {_import_secs * 1000:.0f}ms and binds nothing")

print("=== an allocation made after the last restart is still in force (#223) ===")
text = report(CAPTURE)
got = verdict(text, "100.64.0.9")
check(got == "STILL IN FORCE",
      f"an allocation made 11s AFTER the exporter reinitialised was read as "
      f"{got!r}. The box holding it was never replaced, and this is the report "
      f"a court order asks for")
if got == "STILL IN FORCE":
    ok("a block given out after the last restart is read as still in force")

print("\n=== and one the box really was replaced while holding is not ===")
# The other half, and it has to be asserted here rather than assumed: a rule
# that answered STILL IN FORCE for everything would pass the check above.
got7 = verdict(text, "100.64.0.7")
check(got7 == "ENDED AT RESTART",
      f"an allocation the exporter was restarted after was read as {got7!r}, "
      f"which names a subscriber who had already gone")
if got7 == "ENDED AT RESTART":
    ok("a block the box was replaced while holding is read as ended at the restart")

print("\n=== the marker cadence does not decide the answer ===")
# The same timeline with a marker landing between the restart and the
# allocation, which is the case that passed before. Both must read the same:
# the cadence of a periodic message is not a fact about the subscriber.
WITH_MARKER = CAPTURE[:4] + [marker("05:18:22", "05:18:17")] + CAPTURE[4:]
text2 = report(WITH_MARKER)
check(verdict(text2, "100.64.0.9") == "STILL IN FORCE",
      "the same allocation reads differently when a marker happens to land "
      "before it, so the verdict is about the message schedule and not about "
      "the box")
check(verdict(text2, "100.64.0.7") == "ENDED AT RESTART",
      "the killed box's verdict changed with the marker cadence too")
ok("a marker landing before the allocation changes neither verdict")

print("\n=== with no marker at all, nothing is claimed ===")
# The state #217 exists for: without a boot marker the two meanings cannot be
# told apart, and the collector must say so rather than default to the reading
# that names a subscriber who had already gone.
text3 = report([alloc("05:18:28", "100.64.0.9")])
check("UNDECIDABLE" in text3,
      f"with no boot marker the collector still gave a verdict: {text3.strip()!r}")
ok("an allocation with no boot marker anywhere is undecidable, and says so")

print("\n=== a release closes the allocation and it is not reported at all ===")
# Guards the half that is not about markers: if the pairing broke, every
# verdict above would be about a block nobody is holding.
closed = CAPTURE + [dict(alloc("05:19:00", "100.64.0.9"), natEvent=17)]
text4 = report(closed)
check("100.64.0.9" not in text4,
      f"a released allocation was still reported as open: {text4.strip()!r}")
check("100.64.0.7" in text4,
      "the other subscriber's open allocation went missing with it")
ok("an allocation with a matching release is not an open allocation")

if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
