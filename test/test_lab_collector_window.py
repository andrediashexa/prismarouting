#!/usr/bin/env python3
"""
A lab that waits on the collector must tell it when to stop.

`tools/ipfix_collector.py` takes a number of seconds. A lab that picks one and
then waits it out has decided, when it was written, how long the box would
take - and on a machine carrying something else the box is slower, its records
arrive after the window has closed, and the lab reports that the log is
missing them. That is a product defect the product has not got.

It is not hypothetical and it is not rare. The eight-hour soak reported
104,227 allocations against 104,181 with 46 open, on a box whose own counters
read 104,227 against 104,227 and zero blocks in use, because 822 cycles drift
about eighty seconds past their own arithmetic and the collector's budget
expired inside the final cycle (#342). On 2026-09-15 `detreserve`, `extblocks`
and `extblocks+workers` went red on "the record arrived at the collector"
while the same machine ran the whole test suite under gcov three times over;
all three passed alone.

THE RULE, and it needs no list of exceptions:

  a lab that starts the UDP collector AND waits on it must either pass a stop
  file, or give it a budget generous enough not to be the thing that expires.

Two spellings of the same idea, and both are in the tree already: the labs
fixed here tell the collector when the box has finished, and `ipfix-stop` and
`ipfix-outbuf` give it 300 seconds and synchronise on the box's own exported
record count. What is refused is the third spelling - a number small enough to
be reached, waited out whatever the box did. 120 seconds is the line, which is
four times the longest lab in the suite.

Everything outside that is decided by reading the lab, not by naming it. A lab
that never waits reads the collector's output while it runs and is not about
to be surprised by the window. The TCP form is a different program path with
its own stall file. And a lab the suite does not run at all is named in
`tools/run-all-labs.sh`'s own NOT_LABS, which is read here rather than copied.
"""
import os
import re
import sys

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAB = os.path.join(REPO, "lab")
SUITE = os.path.join(REPO, "tools", "run-all-labs.sh")

#: The labs the suite does not run, read from the suite's own table. A lab
#: nothing runs cannot report a defect to anybody, and NOT_LABS carries the
#: reason each one is out - so this is the suite's decision, not a second one.
_not = re.search(r'NOT_LABS="\n(.*?)\n"', open(SUITE, encoding="utf-8").read(),
                 re.S)
check(_not, "NOT_LABS was not found in tools/run-all-labs.sh, so every lab "
            "below is being judged as though the suite ran it")
SKIP = {l.split(":", 1)[0] for l in (_not.group(1).splitlines() if _not else [])
        if l.strip()}
check(len(SKIP) > 3,
      f"only {len(SKIP)} lab(s) were read out of NOT_LABS, which is too few "
      f"to be that table: {sorted(SKIP)}")

#: Starting the UDP collector: the program, with a port rather than the word
#: `tcp` as its first argument.
_UDP = re.compile(r"ipfix_collector\.py\s+(?!tcp\b)\S")
_WAIT = re.compile(r"^\s*wait\s+\$?\{?COL", re.M)

labs = sorted(f[:-3] for f in os.listdir(LAB) if f.endswith(".sh"))
check(len(labs) > 20,
      f"only {len(labs)} file(s) were read out of lab/, so whatever this "
      f"reports it is not reporting on the labs")

waiters, missing = [], []
for name in labs:
    text = open(os.path.join(LAB, f"{name}.sh"), encoding="utf-8").read()
    if not _UDP.search(text) or not _WAIT.search(text):
        continue
    if name in SKIP:
        continue
    waiters.append(name)
    if "COL_STOP" not in text:
        missing.append(name)

check(waiters,
      "no lab was found that starts the UDP collector and waits on it, so "
      "the rule below is being applied to an empty list")
# THE BUDGET, whether or not there is a stop file behind it. A stop file with
# thirty seconds behind it is the same defect wearing a seatbelt: the budget
# still expires first on the box this is for. 120 seconds is the line and it
# is not arbitrary - the longest lab in the suite runs about two minutes, so a
# collector that outlives four of those is not the thing that gave up.
CEILING = 120
guesses = []
for name in waiters:
    text = open(os.path.join(LAB, f"{name}.sh"), encoding="utf-8").read()
    for m in re.finditer(r"ipfix_collector\.py[\"$A-Za-z_{}/ ]*?"
                         r"\s\d+\s+(\d+)", text):
        if int(m.group(1)) < CEILING:
            guesses.append((name, int(m.group(1))))
check(not guesses,
      f"these wait out a number small enough to be reached, so a machine "
      f"carrying something else makes them report a defect the box has not "
      f"got: {guesses}")
if waiters and not guesses:
    ok(f"{len(waiters)} lab(s) wait on the UDP collector, and none of them "
       f"can be outlived by the box: {', '.join(waiters)}")
    # Said rather than asserted. Telling the collector when to stop is the
    # stronger of the two spellings - it cannot expire at all - but a lab
    # whose budget is four times the longest run in the suite is not the thing
    # that gave up either, and refusing it would refuse correct work.
    _told = [n for n in waiters if n not in missing]
    print(f"  {len(_told)} of them tell it when to stop, which is the "
          f"stronger half: {', '.join(_told) if _told else 'none'}")

print()
if fail:
    print(f"{fail} FAILURES")
    sys.exit(1)
print("ALL TESTS PASSED")
