#!/usr/bin/env python3
"""
No lab may hold an arithmetic assertion whose answer the lab already decided.

A check like

    STRIDE=$((2 * NW))
    expect "and one this worker count can honour" test "$((STRIDE % NW))" -eq 0

reads a number off the box, does arithmetic on it, and compares the result
with something the same arithmetic already guaranteed. It is green for every
worker count a box can have, so nothing it is about can ever turn it red. It
is not a weak check, it is not a check: whatever the dataplane answered, the
verdict was settled three lines above in the shell.

This is the shape #226 found twice by hand in test/test_bpa_dyn.c - a
four-subscriber scenario over four public addresses that stayed green with the
product's fix deleted because 4 % 4 == 0, and "the block is in this worker's
slice", which is true of local * n_workers + worker whatever the allocator
computed. Both were found by deleting the fix and watching the check stay
green. That needs a container and a dataplane. This suite finds the same shape
in the labs by arithmetic alone, which is the half that does not.

HOW IT READS A LAB. Each `expect <desc> test <A> <-op> <B>` is expanded
through the lab's OWN definitions and then driven over every value the box
could have supplied. An assertion true for all of them cannot fail; one false
for all of them cannot pass, which is the same defect pointing the other way
and is worth catching in the same place.

WHAT IT SUBSTITUTES, AND WHAT IT DELIBERATELY DOES NOT. Only a definition that
is a real arithmetic expression - one with an operator in it - is folded into
the assertion. A bare copy is left alone, and that distinction is what keeps
the suite honest rather than merely strict. lab/pool-del.sh writes

    SMALL=$BEST        ... after timing the small table
    BIG=$BEST          ... after timing the large one

and asserts BIG against SMALL. Those are two readings of one helper taken at
different times, not one value compared with itself, and folding both into
$BEST would report the lab's central measurement as vacuous. Measured over the
216 arithmetic assertions lab/ holds: with bare copies folded this flags
pool-del.sh:263 and nothing else, and that one hit is the false positive; with
them left alone it flags none.

WHAT IT FOUND WHEN IT WAS WRITTEN, which is the only reason to believe it
reads anything. Two, both on a tree where every suite was green:

    lab/extblocks.sh:381   test "$((STRIDE % NW))" -eq 0, three lines under
                           STRIDE=$((2 * NW)). True for every worker count.
    lab/ipfix-outbuf.sh:763  test "${DROPPED_STOP:-0}" -ge 0, over a byte
                           count. True of every number and of the empty
                           string.

Division by zero is skipped rather than counted as a red branch. A lab whose
worker count came back empty divides by zero, dash prints an error and the
check goes red - but it went red because the shell failed, not because
anything asserted on what the box said. A branch reachable only by crashing
the script is not the branch this suite is asking about.

It reads the SOURCE and not a run, and that is the point rather than a
limitation: an assertion that cannot fail is invisible on a green box by
construction, which is exactly how one lived in lab/extblocks.sh underneath a
comment explaining why the fixture had been made careful. The comment was
right about the fixture. The check beneath it had stopped being able to say so.

Scope, stated rather than discovered later: only numeric `test A -op B`
comparisons, and only over variables the lab defines arithmetically. A check
that reaches a value through a pipeline, a file or a lab function is out of
reach of a source check, and so is every string comparison. The answer for
those is the same as it has always been - run the lab with the feature off and
watch it go red, which tools/run-all-labs.sh already requires of the 20 labs
that have an off mode.

Found by the assertion sweep of 2026-09-15.
"""
import glob
import itertools
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAB = os.path.join(ROOT, "lab")

# lib.sh and verdict.sh are sourced, not run: they hold the helpers, not
# assertions about a box.
LIBRARIES = ("lib.sh", "verdict.sh")

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def logical_lines(path):
    """Shell continuations joined, so an assertion broken over three lines is
    one line here. Every `expect` long enough to be worth reading is."""
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


# An assignment anywhere a command can start, not only at the beginning of a
# line. `[ "$x" -ge 1 ] || NW=1` is an assignment, and a reader that only
# looks at column one thinks NW has a single definition.
ASSIGN = re.compile(r'(?:^|[;&|(]|\b(?:then|else|do|fi|done)\b)\s*'
                    r'([A-Za-z_]\w*)=(.*)$')
NUMOP = {"-eq": "==", "-ne": "!=", "-lt": "<",
         "-le": "<=", "-gt": ">", "-ge": ">="}


def has_cmdsub(s):
    """$(...) but not $((...)). A command substitution is where a value comes
    off the box; an arithmetic expansion is where the lab does sums on it."""
    if "`" in s:
        return True
    i = 0
    while True:
        j = s.find("$(", i)
        if j < 0:
            return False
        if s[j:j + 3] == "$((":
            i = j + 3
            continue
        return True


def to_expr(word):
    """A shell word as a python expression, variables named V_<name>.

    $((...)) becomes (...) and every identifier inside it is a variable, which
    is the half a reader written for $X alone misses: `$((STRIDE % NW))` names
    two variables and mentions neither with a dollar sign in front of it.
    Returns None for a word that is not arithmetic at all."""
    out = []
    i = 0
    while i < len(word):
        if word.startswith("$((", i):
            depth = 0
            j = i + 1
            while j < len(word):
                if word[j] == "(":
                    depth += 1
                elif word[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j >= len(word):
                return None
            inner = word[i + 3:j - 1]
            inner = re.sub(r'\$\{?(\w+)(?::-[^}]*)?\}?', r'\1', inner)
            inner = re.sub(r'\b([A-Za-z_]\w*)\b', r'V_\1', inner)
            out.append("(" + inner + ")")
            i = j + 1
        else:
            out.append(word[i])
            i += 1
    s = "".join(out)
    s = re.sub(r'\$\{(\w+)(?::-[^}]*)?\}', r'V_\1', s)
    s = re.sub(r'\$(\w+)', r'V_\1', s)
    if not re.fullmatch(r'[\sV_0-9A-Za-z()+\-*/%]+', s):
        return None
    return s


# The values a box can hand a lab: counts, worker numbers, port numbers, byte
# and microsecond figures, and zero, which is what an empty variable is worth
# in shell arithmetic.
#
# NON-NEGATIVE, and that is a statement about the labs rather than about shell
# arithmetic. Every one of these numbers is a count, a size, an index or an
# elapsed time read off a box; none of them can come back below zero. So
# `test "$N" -ge 0` really is a check that cannot fail, and probing negatives
# to make it look falsifiable would be inventing an input no box can produce.
PROBES = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 16, 31, 32, 64, 100, 127, 128,
          255, 512, 1000, 1023, 1024, 4096, 23302, 50000, 65535, 1000000]


def probes_for(expr):
    """The grid, plus the numbers this assertion itself names.

    Without this, every `test "$SPI" -eq 286331153` reads as false for every
    probe and gets reported as an assertion that cannot pass - which says
    nothing about the lab and everything about the grid not containing the
    answer. Each literal goes in with its neighbours, so a comparison can be
    driven to both sides of its own threshold, and one above the largest, so a
    `-lt` against a number bigger than anything in the base set is not called
    constant for want of a value above it."""
    out = set(PROBES)
    for lit in re.findall(r'\b\d+\b', expr):
        n = int(lit)
        out.update({n, n + 1, 2 * n + 1})
        if n:
            out.add(n - 1)
    return sorted(out)


def constant_verdict(expr, free):
    """True/False if the assertion has that answer for every probe, else None.

    Returns the witness values too, because "it is always true" is a claim
    about arithmetic and the reader is owed the numbers behind it."""
    seen = {}
    grid = probes_for(expr)
    for combo in (itertools.product(grid, repeat=len(free)) if free
                  else [()]):
        env = dict(zip(free, combo))
        try:
            v = bool(eval(expr, {"__builtins__": {}}, env))
        except ZeroDivisionError:
            # Reachable only by crashing the shell. Not a red branch.
            continue
        except Exception:
            return None, None
        seen.setdefault(v, dict(env))
        if len(seen) == 2:
            return None, None
    if len(seen) != 1:
        return None, None
    v = next(iter(seen))
    return v, seen[v]


def definitions(path):
    """Every variable the lab assigns, and what it assigns."""
    asg = {}
    for _, line in logical_lines(path):
        if line.lstrip().startswith("#"):
            continue
        for m in ASSIGN.finditer(line):
            asg.setdefault(m.group(1), []).append(m.group(2).strip())
    return asg


def foldable(asg, var):
    """The arithmetic this variable stands for, or None to leave it free.

    Three ways a variable stays free, and each is a decision rather than a gap.
    Assigned more than once, because which assignment reached the assertion is
    a question about control flow that a source check must not guess. Assigned
    out of a command substitution, because that is a value from the box and is
    precisely what the assertion is supposed to depend on. And assigned a bare
    copy of another variable, because two copies taken at different moments are
    two readings - see lab/pool-del.sh in the header."""
    rhs = asg.get(var)
    if not rhs or len(rhs) != 1:
        return None
    r = rhs[0]
    if has_cmdsub(r) or "$?" in r or "$@" in r or re.search(r'\$\d', r):
        return None
    e = to_expr(r)
    if e is None or not re.search(r'[-+*/%]', e):
        return None
    return e


def fold(expr, asg):
    """Substitute definitions until nothing more can be."""
    for _ in range(16):
        new = re.sub(r'\bV_(\w+)\b',
                     lambda m: (lambda d: "(" + d + ")" if d else m.group(0))(
                         foldable(asg, m.group(1))),
                     expr)
        if new == expr:
            return expr
        expr = new
    return expr


def assertions(path):
    """Every `expect`/`expect_setup` numeric comparison in one lab."""
    asg = definitions(path)
    for ln, line in logical_lines(path):
        s = line.lstrip()
        if s.startswith("#"):
            continue
        while True:
            m = re.match(r'^(applied_and|if|then|else|elif|do|&&|\|\||;|!)\s+',
                         s)
            if not m:
                break
            s = s[m.end():]
        m = re.match(r'^(expect|expect_setup)\s', s)
        if not m:
            continue
        rest = s[m.end():]
        if has_cmdsub(rest):
            continue
        try:
            import shlex
            args = shlex.split(rest, posix=True)
        except ValueError:
            continue
        if len(args) < 2:
            continue
        a = args[1:]
        if a and a[0] in ("test", "["):
            a = a[1:]
        if a and a[-1] == "]":
            a = a[:-1]
        if len(a) != 3 or a[1] not in NUMOP:
            continue
        L, R = to_expr(a[0]), to_expr(a[2])
        if L is None or R is None:
            continue
        L, R = fold(L, asg), fold(R, asg)
        # " " and not "": the two sides are separate expressions, and joining
        # them without one merges the tail of the left with the head of the
        # right. `V_DROPPED_STOP` against `0` read as the single name
        # `V_DROPPED_STOP0`, which is in no environment, so eval raised and the
        # assertion was skipped instead of judged. It hid a real one in
        # lab/ipfix-outbuf.sh and 20-odd others besides.
        free = sorted(set(re.findall(r'\bV_\w+', L + " " + R)))
        if len(free) > 3:
            # The probe grid is a product. Beyond three the cost stops being
            # worth the reach, and an assertion over four independent box
            # values is not the shape this is looking for anyway.
            continue
        yield ln, args[0], line.strip(), f"({L}) {NUMOP[a[1]]} ({R})", free


labs = sorted(p for p in glob.glob(os.path.join(LAB, "*.sh"))
              if os.path.basename(p) not in LIBRARIES)

considered = 0
for path in labs:
    name = os.path.basename(path)
    for ln, desc, src, expr, free in assertions(path):
        considered += 1
        verdict, witness = constant_verdict(expr, free)
        if verdict is None:
            continue
        shown = ", ".join(f"{k[2:]}={v}" for k, v in witness.items()) or "no"
        check(False,
              f"lab/{name}:{ln} asserts something the lab already decided: it "
              f"is {str(verdict).upper()} for every value the box can supply, "
              f"so it can never go {'red' if verdict else 'green'}\n"
              f"         {src[:140]}\n"
              f"         expanded through the lab's own definitions: {expr}\n"
              f"         ({shown} is one of the values it was driven over)\n"
              f"         assert what a WRONG answer produces, or read the "
              f"value off the box instead of recomputing it")

# A suite that reads nothing passes, which is the defect this file is about.
# The floor is well under the 221 measured on 2026-09-15, so ordinary work on
# the labs does not trip it and a matcher that has quietly stopped matching
# does.
check(considered > 150,
      f"only {considered} arithmetic assertions were read, which is far fewer "
      f"than lab/ contains - the matcher is broken and this suite is passing "
      f"by reading nothing")

if not fail:
    print(f"  {len(labs)} labs, {considered} arithmetic assertions: every one "
          f"can be turned by something the box says")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
