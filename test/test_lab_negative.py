#!/usr/bin/env python3
"""
No lab may hold a NEGATIVE assertion whose needle cannot reach its haystack.

`expect_not_in` and `expect_not_said` say "the box must not print X", and they
are the direction that hides. A needle that can never match makes "this must
appear" red on a correct box, which somebody notices, and "this must NOT
appear" green on a broken one, which nobody does - the sentence
test/test_lab_crlf.py opens with, and the reason this file exists beside it.

WHAT WAS MEASURED BEFORE ANY RULE WAS WRITTEN, because the obvious sweep here
is one this repository has already paid for twice.

  * 141 negative assertions in lab/*.sh on 2026-09-15: 128 expect_not_in and
    13 expect_not_said. Beside them lab/ holds four `expect ... sh -c "! ..."`
    in igp.sh, six `expect ... test -z` in esp.sh and pptp.sh, and two
    `expect_one_of` bad-answer needles in install-iso.sh - 153 in all.
  * Screening those needles against the product's own source - the shape
    test/test_doc_copies.py records as tried and rejected - reported 42
    unproducible, the SAME 42 that file counted, and every one of them checked
    by hand was a false positive: a pool name, an address or an interface the
    lab makes at run time, a sentence the installer prints across two echo
    lines, or FRR's own words. So there is no rule here about needles against
    the product, and this file does not try to write one.
  * The haystack half: 29 of the 141 read a variable no other assertion in the
    lab reads. A rule requiring every negative haystack to be pinned non-empty
    would flag all 29, and most are correct - `$FLOOR_OK`, `$QUIET`,
    `$STRIDE_OK`, `$BOOT_GONE` are all "the box said nothing", where an empty
    answer IS the pass. That rule needs an allowlist, so it is not here either.

WHAT IS HERE IS TWO SHAPES, each one a defect that was found by hand and each
one with no hits in lab/ today, so neither costs anybody an exception.

FIRST: a raw-helper needle carrying a NEWLINE. `expect_in` and `expect_not_in`
search the text as it came, so a needle with a newline in it matches only if
the output breaks at that byte with that indentation. Every needle written
that way in this repository has been a prose sentence typed across two source
lines with the closing quote in column 1 - and `$( )` strips the trailing
newline off the haystack, so it matched nothing. lab/routing.sh:950 held

    expect_not_in "and does not claim the whole configuration checks out" \\
      "configuration check succeeds
    " "$CHECKOUT"

and neither sentence the VTY can print could satisfy it. The answer is
`expect_said`/`expect_not_said`, which collapse whitespace on both sides and
exist for exactly this.

SECOND: a negative assertion reading a haystack built by DISCARDING STDERR and
then SELECTING LINES, that no other assertion in the same lab reads. That pair
is the one combination where the haystack goes empty on precisely the box the
check is about: the command refused, said why on stderr, the refusal was
thrown away, and the selection had nothing to pick. lab/vty-ssh.sh:114 was

    EFFECTIVE=$(sshd -T 2>/dev/null | grep -i "^listenaddress" | head -2 ...)
    expect_not_in "and the address it refused is not in effect" \\
      "not:an:address" "${EFFECTIVE:-}"

and `sshd -T` refuses a file it cannot parse rather than printing a partial
one. Driven over two real sshd_config files on OpenSSH 9.2: with the refused
address in the file it exits 255, writes "bad addr or host: not:an:address" on
stderr and prints no configuration, so EFFECTIVE is empty and the check is
green; with the address rolled back EFFECTIVE holds the two default
listenaddress lines and the check is green. It could not fail either way.

The escape is structural rather than a list: another assertion reading the
same haystack says the answer is there, which is what lab/routing.sh's
block_of pair does two lines above its own negative. Measured today: two
negative assertions read such a haystack, lab/handoff.sh:140 and
lab/vty.sh:634, and both are pinned.

It reads the SOURCE and not a run, for the reason the two suites beside it
give: an assertion that cannot fail is invisible on a green box, so no amount
of running the labs will show it.

Found by the negative-needle sweep of 2026-09-15.
"""
import glob
import os
import re
import shlex
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAB = os.path.join(ROOT, "lab")

# Sourced, not run: they hold the helpers, not assertions about a box.
LIBRARIES = ("lib.sh", "verdict.sh")

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def logical_lines(text):
    """Shell continuations joined, AND quoted strings that span lines.

    A reader that joins only backslash continuations walks straight past the
    defect this file's first rule is about: the needle in lab/routing.sh:950
    was one double-quoted string with a real newline inside it, and neither of
    its two physical lines is an assertion on its own.
    """
    raw = text.split("\n")
    out = []
    i = 0
    while i < len(raw):
        start = i + 1
        line = raw[i]
        while i + 1 < len(raw) and (line.endswith("\\") or _quote_open(line)):
            if line.endswith("\\"):
                line = line[:-1] + " " + raw[i + 1]
            else:
                line = line + "\n" + raw[i + 1]
            i += 1
        out.append((start, line))
        i += 1
    return out


def _quote_open(s):
    """Is a quoted string still open at the end of this line?

    BOTH kinds, and tracked as a state machine rather than by counting. A
    counter over double quotes alone swallowed 14 of the 141 negative
    assertions into the logical line above them: inside single quotes a
    double quote is an ordinary character, and lab/routing.sh's block_of is a
    multi-line awk program in single quotes full of them. A joiner that eats
    assertions makes this suite pass by reading less, which is the failure it
    is here to prevent one layer down.

    Backslash escapes are honoured in double quotes and outside quotes, and
    not in single quotes, where the shell has no escapes.
    """
    st = None
    k = 0
    while k < len(s):
        c = s[k]
        if st is None:
            if c == "\\":
                k += 2
                continue
            # A comment ends the line for quoting purposes, and this is not a
            # nicety: the labs explain themselves in English, apostrophes and
            # all, and "the operator's own" left a single quote open that ate
            # every assertion until the next one. 87 of the 141 went that way.
            if c == "#" and (k == 0 or s[k - 1] in " \t"):
                break
            if c in "'\"":
                st = c
        elif st == '"':
            if c == "\\":
                k += 2
                continue
            if c == '"':
                st = None
        else:                     # inside single quotes: nothing escapes
            if c == "'":
                st = None
        k += 1
    return st is not None


RAW_HELPERS = ("expect_in", "expect_not_in")
NEGATIVES = ("expect_not_in", "expect_not_said")
# Any assertion, so that "something else reads this haystack" counts whatever
# shape that something else took.
ANY_ASSERT = re.compile(r'^\s*expect(?:_in|_said|_not_in|_not_said|_setup'
                        r'|_timed|_one_of)?\s')
NEG_ASSERT = re.compile(r'^\s*(?:' + "|".join(NEGATIVES) + r')\s')
# stderr thrown away, and a stage that can select nothing out of what is left.
# head/tail/cut are not here: they cannot turn a non-empty input into nothing.
DISCARDS_STDERR = re.compile(r'2>\s*/dev/null')
SELECTS_LINES = re.compile(r'\|\s*(?:grep|sed\s+-n|awk)\b')


def parse(line):
    """(helper, args) for an assertion line, or None."""
    s = line.lstrip()
    m = re.match(r'^(expect_in|expect_not_in|expect_said|expect_not_said)\s',
                 s)
    if not m or s.startswith("#"):
        return None
    try:
        args = shlex.split(s[m.end():].replace("\n", " "), posix=True)
    except ValueError:
        return None
    return m.group(1), args


def needle_newlines(lines):
    """Raw-helper assertions whose needle carries a newline."""
    for ln, line in lines:
        if line.lstrip().startswith("#"):
            continue
        m = re.match(r'^\s*(expect_in|expect_not_in)\s', line)
        if not m:
            continue
        # The needle is read off the SOURCE and not through shlex, because
        # shlex is given the line with newlines already flattened - which is
        # the byte this rule is about.
        body = line[m.end():]
        try:
            args = shlex.split(body, posix=True)
        except ValueError:
            continue
        if len(args) >= 2 and "\n" in args[1]:
            yield ln, m.group(1), args[1]


def haystack_var(arg):
    m = re.fullmatch(r'\$\{?(\w+)(?::-[^}]*)?\}?', arg.strip())
    return m.group(1) if m else None


def blind_haystacks(lines):
    """Negatives reading a filtered, stderr-discarding haystack nobody pins."""
    for ln, line in lines:
        if line.lstrip().startswith("#") or not NEG_ASSERT.match(line):
            continue
        p = parse(line)
        if not p or len(p[1]) < 3:
            continue
        hay = p[1][2]
        var = haystack_var(hay)
        if var is None:
            sources = [hay]
            pinned = False
        else:
            sources = [l for _n, l in lines
                       if re.match(r'\s*' + var + r'=', l)
                       and not l.lstrip().startswith("#")]
            pat = re.compile(r'\$\{?' + var + r'\b')
            pinned = any(ANY_ASSERT.match(l) and not NEG_ASSERT.match(l)
                         and pat.search(l) for _n, l in lines)
        if pinned:
            continue
        for src in sources:
            if DISCARDS_STDERR.search(src) and SELECTS_LINES.search(src):
                yield ln, hay, src.strip()
                break


# --- the checker has to be able to see both violations -----------------------
#
# Without this the suite passes on a parser that matches nothing, which is the
# defect class it exists to catch one layer up. Both samples are the real
# lines, copied out of the tree they were fixed in.
ROUTING_950 = '''expect_in "commit check now refuses the bad range itself" \\
  "prefix range refused" "$CHECKOUT"
expect_not_in "and does not claim the whole configuration checks out" \\
  "configuration check succeeds
" "$CHECKOUT"
'''
check([w for w in needle_newlines(logical_lines(ROUTING_950))],
      "the checker does not see a needle written across two source lines, "
      "which is lab/routing.sh:950 and the whole of its first rule")
check(not [w for w in needle_newlines(logical_lines(
          'expect_not_in "x" "configuration check succeeds" "$C"\n'))],
      "the checker flags a needle with no newline in it, which would push the "
      "next author into an exception instead of the fix")
check(not [w for w in needle_newlines(logical_lines(
          'expect_not_said "x" "a sentence written\\n  across two lines" "$C"\n'))],
      "the checker flags expect_not_said, which is the helper it steers "
      "people towards")

VTY_SSH_114 = '''EFFECTIVE=$(sshd -T 2>/dev/null | grep -i "^listenaddress" \\
            | head -2 | tr "\\n" " ")
expect_not_in "and the address it refused is not in effect" \\
  "not:an:address" "${EFFECTIVE:-}"
'''
check([w for w in blind_haystacks(logical_lines(VTY_SSH_114))],
      "the checker does not see a negative reading a haystack whose stderr "
      "was discarded and then filtered, which is lab/vty-ssh.sh:114")
check(not [w for w in blind_haystacks(logical_lines(
          VTY_SSH_114 + 'expect_in "sshd answered" "listenaddress" "$EFFECTIVE"\n'))],
      "the checker flags a haystack another assertion DOES read, which is the "
      "repair and must not be reported as the defect")
check(not [w for w in blind_haystacks(logical_lines(
          'X=$(sshd -T 2>&1 | grep -i "^listen")\n'
          'expect_not_in "x" "bad" "$X"\n'))],
      "the checker flags a haystack that KEEPS stderr, where the refusal is "
      "in the text and the needle can reach it")
check(not [w for w in blind_haystacks(logical_lines(
          'X=$(sshd -T 2>/dev/null | head -2)\n'
          'expect_not_in "x" "bad" "$X"\n'))],
      "the checker flags head/tail, which cannot turn a non-empty answer "
      "into an empty one")

# --- the labs ----------------------------------------------------------------
labs = sorted(p for p in glob.glob(os.path.join(LAB, "*.sh"))
              if os.path.basename(p) not in LIBRARIES)
check(len(labs) > 30,
      f"only {len(labs)} lab scripts found, so the sweep below is reading "
      f"almost nothing")

negatives = 0
for path in labs:
    name = os.path.basename(path)
    lines = logical_lines(open(path).read())
    for ln, line in lines:
        if NEG_ASSERT.match(line) and not line.lstrip().startswith("#"):
            negatives += 1
    for ln, helper, needle in needle_newlines(lines):
        check(False,
              f"lab/{name}:{ln} gives {helper} a needle with a NEWLINE in it, "
              f"so it matches only where the output happens to break\n"
              f"         {needle[:100]!r}\n"
              f"         use expect_said/expect_not_said, which collapse "
              f"whitespace on both sides")
    for ln, hay, src in blind_haystacks(lines):
        check(False,
              f"lab/{name}:{ln} asserts something is ABSENT from {hay}, which "
              f"throws stderr away and then selects lines - so it is empty on "
              f"a box that refused, and the check is green there\n"
              f"         {src[:120]}\n"
              f"         keep stderr, or have another assertion say the "
              f"answer is there")

# A suite that reads nothing passes, which is the defect this file is about.
# The floor is under the 141 measured on 2026-09-15 with room for ordinary
# work, and well above what a broken matcher returns.
check(negatives > 100,
      f"only {negatives} negative assertions were read, which is far fewer "
      f"than lab/ contains - the matcher is broken and this suite is passing "
      f"by reading nothing")

if not fail:
    print(f"  {len(labs)} labs, {negatives} negative assertions: none carries "
          f"a needle the output cannot break for, none reads a haystack that "
          f"goes quiet on the box it is about")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
