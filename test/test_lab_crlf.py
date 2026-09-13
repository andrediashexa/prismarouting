#!/usr/bin/env python3
"""
No lab may take a value out of `vppctl` in a shape that keeps the carriage
return.

vppctl ends every line it prints with CR+LF. awk's field splitting and sh's
IFS count only space, tab and newline as whitespace, so the CR stays attached
to the LAST FIELD of every line: a value read off the end of a line is
"600<CR>" while the terminal shows "600".

Which half of a lab survives that is measured, not guessed. Under dash, which
is the shell tools/lab.sh runs the labs with:

    [ "600<CR>" -eq 600 ]     true     the number parser calls isspace(),
                                       and isspace('\\r') is true
    [ "600<CR>" = "600" ]     FALSE
    case 600<CR> in 600)      no match
    grep -x 600               no match

So arithmetic survives it and every string comparison does not - and it fails
in the direction that hides. A needle that can never match makes "this must
appear" red on a correct box, which somebody notices, and "this must NOT
appear" green on a broken one, which nobody does. lab/snmp.sh had one of those
for the life of the file: a `grep -x 600` over vppctl output, with `|| echo
600` behind it, so the report said "vppctl says 600" whatever the dataplane
held (#249).

This is a check on the SOURCE, not on a box, and that is deliberate: the
defect is invisible on a running box by construction. It cannot see whether an
extracted value is later compared as a string, so it does not try - it refuses
the extraction shapes that carry a CR at all. They are a closed list, and the
labs pass it today with none of them unstripped, so the rule costs nothing to
keep and there is no allowlist for anybody to grow.

It reads two steps, not one. A lab can park a whole output in a variable on
one line and extract from it on another, and the second line never says
"vppctl" - lab/icmp-tcp.sh did exactly that. So the file is read first for
variables holding unstripped vppctl output, and an extraction shape applied to
one of them is the same offence as one applied to the pipeline.

What it does NOT follow, stated rather than discovered later: a value that
reaches a FILE and is read back from it, one that goes through a lab function
returning it, or one built by string arithmetic the rule does not model. Those
are out of reach of a source check, and the answer for them is the same one as
here - call vc().

The strip belongs at the extraction and not at the pipe. That was decided by
counting: of the 280 assertion sites in lab/*.sh that read a vppctl-derived
value, the needle is a literal string in all but a handful, and a literal
needle has no CR to lose - it matches inside "...bpa-in2out<CR>" like any
other substring. Wrapping every vppctl call would move the input of every lab
to change no lab's verdict.

Found by #239 in lab/frag.sh, swept and pinned by #249.
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAB = os.path.join(ROOT, "lab")

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


# The shapes that carry a carriage return out of a pipeline. Every one of them
# ends up holding the last field or the whole line, which is exactly where the
# CR sits.
SHAPES = [
    (re.compile(r"\$NF\b"),
     "awk $NF: the last field is where the CR is"),
    (re.compile(r"\|\s*tail\s+-1\b"),
     "tail -1: a whole line ends with the CR"),
    (re.compile(r"sed\s+-n\s+'s[/|][^/|]*[/|][/|]p'"),
     "sed s///p with no capture group: it prints the rest of the line"),
    (re.compile(r"grep\s+(?:-\S+\s+)*-\w*x"),
     "grep -x: it compares WHOLE LINES, and the line ends with the CR"),
    (re.compile(r"tr\s+'? '?\s+'?\\n'?"),
     "tr ' ' '\\n': the last token of each line keeps the CR"),
    (re.compile(r"grep\s+(?:-\S+\s+)*['\"][^'\"]*\$['\"]"),
     "a regex anchored at the end of the line, which the CR now occupies"),
]

STRIPPED = re.compile(r"tr\s+-d\s+['\"]\\r['\"]")
# A pipeline that begins at vc() is already stripped: lab/lib.sh does it there.
CALLS_VPPCTL = re.compile(r"(^|[^\w./-])vppctl\s")
ASSIGNED_FROM_VPPCTL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)=\$\((.*)")


def logical_lines(path):
    """Shell continuations joined, so a pipeline broken over four lines is
    one line here. A checker that reads physical lines misses exactly the
    pipelines long enough to be worth writing."""
    raw = open(path).read().split("\n")
    out = []
    i = 0
    while i < len(raw):
        start = i + 1
        line = raw[i]
        while line.endswith("\\") and i + 1 < len(raw):
            line = line[:-1] + " " + raw[i + 1]
            i += 1
        out.append((start, line))
        i += 1
    return out


def offences(line, raw_vars=()):
    """The shapes this line writes over unstripped vppctl output.

    `raw_vars` closes the gap a one-line rule leaves open: a lab can park the
    whole output in a variable on one line and extract from it on another, and
    the second line never says "vppctl". Those are passed in by the caller,
    which reads the file first.
    """
    s = line.strip()
    if s.startswith("#"):
        return []
    reads_raw = CALLS_VPPCTL.search(s) is not None
    if not reads_raw:
        for v in raw_vars:
            if re.search(r"\$\{?" + re.escape(v) + r"\b", s):
                reads_raw = True
                break
    if not reads_raw:
        return []
    if STRIPPED.search(s):
        return []
    return [why for rx, why in SHAPES if rx.search(s)]


def raw_vppctl_vars(lines):
    """Variables holding vppctl output that still has its carriage returns."""
    out = set()
    for _ln, line in lines:
        s = line.strip()
        if s.startswith("#"):
            continue
        for m in ASSIGNED_FROM_VPPCTL.finditer(s):
            body = m.group(2)
            if CALLS_VPPCTL.search(body) and not STRIPPED.search(body):
                out.add(m.group(1))
    return out


# --- the checker has to be able to see a violation ---------------------------
#
# Without this the suite passes on a parser that matches nothing, which is the
# defect class it exists to catch, one layer up.
SAMPLES = [
    ("X=$(vppctl show interface pg0 | awk '/rx packets/{print $NF}')",
     "X=$(vppctl show interface pg0 | tr -d '\\r' | awk '{print $NF}')"),
    ("X=$(vppctl show bpa session | tail -1)",
     "X=$(vppctl show bpa session | tr -d '\\r' | tail -1)"),
    ("X=$(vppctl show hardware acc0 | sed -n 's/.*Ethernet address //p')",
     "X=$(vppctl show hardware acc0 | tr -d '\\r' "
     "| sed -n 's/.*Ethernet address //p')"),
    ("vppctl show bpa timeouts | tr ' ' '\\n' | grep -x 600",
     "vppctl show bpa timeouts | tr -d '\\r' | tr ' ' '\\n' | grep -x 600"),
    ("vppctl show interface features pg0 | grep -q 'bpa-in2out$'",
     "vppctl show interface features pg0 | tr -d '\\r' | grep -q 'bpa-in2out$'"),
]
for bad, good in SAMPLES:
    check(offences(bad) != [],
          f"the checker does not recognise a known-bad line, so it would pass "
          f"a lab that wrote one: {bad}")
    check(offences(good) == [],
          f"the checker flags a line that DOES strip, which would push the "
          f"next author into an allowlist instead of the fix: {good}")
check(offences("X=$(vc show interface pg0 | awk '{print $NF}')") == [],
      "the checker flags vc(), which is the helper it is meant to steer "
      "people towards")
# The two-line shape: the extraction never says "vppctl", so a rule that reads
# one line at a time would walk straight past it.
TWO_LINE = [(1, 'RAW=$(vppctl show interface pg0)'),
            (2, 'X=$(printf "%s\\n" "$RAW" | awk \'{print $NF}\')')]
check(raw_vppctl_vars(TWO_LINE) == {"RAW"},
      "the checker does not notice a variable holding unstripped vppctl "
      "output, so an extraction on a later line would go unread")
check(offences(TWO_LINE[1][1], raw_vppctl_vars(TWO_LINE)) != [],
      "the checker misses an extraction taken from a variable rather than "
      "from the pipeline, which is the same defect one line down")
check(raw_vppctl_vars([(1, "RAW=$(vppctl show bpa pool | tr -d '\\r')")]) == set(),
      "the checker treats an already-stripped variable as raw, which would "
      "flag correct labs and teach the next author to add an exception")

# --- lab/lib.sh has to carry the helper --------------------------------------
lib = open(os.path.join(LAB, "lib.sh")).read()
check(re.search(r"^vc\(\)\s*\{.*vppctl.*tr\s+-d\s+'\\r'", lib, re.M) is not None,
      "lab/lib.sh has no vc() that pipes vppctl through tr -d '\\r', so the "
      "labs steered at it have nothing to call")

# --- the labs ----------------------------------------------------------------
labs = sorted(glob.glob(os.path.join(LAB, "*.sh")))
check(len(labs) > 30,
      f"only {len(labs)} lab scripts found, so the sweep below is comparing "
      f"almost nothing")

read_vppctl = 0
scanned = 0
for path in labs:
    name = os.path.basename(path)
    lines = logical_lines(path)
    raw_vars = raw_vppctl_vars(lines)
    for ln, line in lines:
        scanned += 1
        if CALLS_VPPCTL.search(line) and not line.strip().startswith("#"):
            read_vppctl += 1
        for why in offences(line, raw_vars):
            check(False,
                  f"lab/{name}:{ln} takes a value out of vppctl without "
                  f"removing the carriage return - {why}\n"
                  f"         {line.strip()[:120]}\n"
                  f"         use vc() from lab/lib.sh, or pipe through "
                  f"tr -d '\\r'")

check(read_vppctl > 200,
      f"only {read_vppctl} lines call vppctl, which is far fewer than the "
      f"labs actually contain - the matcher is probably broken and this "
      f"suite is passing by reading nothing")

if not fail:
    print(f"  {len(labs)} labs, {scanned} lines, {read_vppctl} vppctl calls: "
          f"no unstripped extraction")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
