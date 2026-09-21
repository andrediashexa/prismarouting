#!/usr/bin/env python3
"""
No C harness may assert a comparison the product it tests has already made.

test/test_bpa_rtsp.c held this, forty lines apart from the parser it includes:

    src/plugins/bpa/bpa_rtsp.h    if (hi != lo + 1)
                                    return BPA_RTSP_UNPARSED;

    test/test_bpa_rtsp.c          CHECK (cp.hi == cp.lo + 1,
                                         "%s: %u-%u is not a consecutive pair")

The assertion runs only on messages the parser accepted, and the parser
accepts none whose ports are not consecutive. So it was green for every input
the fixture could hold and green for a parser reporting the wrong pair
entirely: shifting both ports by 100 inside bpa_rtsp.h left it green across
all seven matching cases, and only the single round-trip case at the bottom of
the file noticed. An assertion whose subject is a branch the callee already
took is not a weak check, it is the callee's source quoted back at it.

WHAT IT READS. Each test/test_bpa_*.c is paired with the product files it
#includes. From the product it takes the condition of every guard that ENDS a
path - `if (...) return ...;`, `if (...) break;`, `if (...) continue;` - since
those are the ones that decide which inputs reach the caller at all. From the
harness it takes the first argument of every CHECK. Both sides are reduced to
(left, operator, right) and compared; a CHECK matching a guard's negation is
the offence, because the guard having fired is exactly what makes the CHECK
true.

WHAT IT NORMALISES, AND WHAT IT DELIBERATELY DOES NOT. Casts, whitespace and
one level of struct access are removed, so `cp.hi` and `(u16) hi` and `hi` are
one name - the harness reads through a struct the product fills by field name,
and keeping them apart would miss every real instance. Nothing else is folded.
In particular no arithmetic is evaluated and no identifier is substituted: two
comparisons match only when they are written the same way, which keeps the
suite reporting quotations rather than theorems it has proved about C.

It is therefore a floor and not a ceiling, and the shapes it cannot see are
worth naming rather than discovering later. A guard spread over a boolean
chain (`if (a || b) return`) is read clause by clause, so a CHECK restating
the whole chain is missed. A guard the product expresses as a loop bound, or
through a helper, is out of reach entirely - test_bpa_ports.c held one of
those, where "a pair with only one free port is refused" was answered by
`n_free < 2` before the scan it was written about ever ran, and no source
check finds that. The answer for those is the one it has always been: delete
the product behaviour and watch the assertion stay green.

It reads the SOURCE and not a run, and that is the point rather than a
limitation. An assertion that cannot fail is invisible on a green suite by
construction, which is how this one lived under a comment that correctly
explained what the parser guaranteed - and that guarantee was the reason the
check beneath it had stopped being able to say anything.

Sister to test/test_lab_vacuous.py, which asks the same question of the shell
labs by arithmetic. That one covers `expect ... test A -op B`; this one covers
CHECK. Between them the two assertion idioms this repository has are both
swept.

Found by the C assertion sweep of 2026-09-15.
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST = os.path.join(ROOT, "test")

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


# The relational operators, and what each one becomes when the branch guarded
# by it was NOT taken. A CHECK spelling the second column is a CHECK asserting
# that the guard in the first did not fire, which is the offence.
NEGATION = {
    "==": "!=",
    "!=": "==",
    "<": ">=",
    ">=": "<",
    ">": "<=",
    "<=": ">",
}

# Ordered longest-first so that "<=" is never read as "<".
OPS = ("==", "!=", "<=", ">=", "<", ">")

COMMENT = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT = re.compile(r"//[^\n]*")
CAST = re.compile(r"\((?:const\s+)?[iu](?:8|16|32|64)\s*\*?\)")
# One level of struct or pointer access: the harness reads `cp.hi` out of the
# struct the product filled as `hi`.
MEMBER = re.compile(r"\b[A-Za-z_]\w*\s*(?:->|\.)\s*")


def strip_comments(src):
    """Blank the comments out, keeping every newline.

    The line numbers this suite reports are the ones in the file on disk, so
    a block comment has to leave behind as many lines as it occupied.
    """
    def blank(m):
        return " " + "\n" * m.group(0).count("\n")

    return LINE_COMMENT.sub(" ", COMMENT.sub(blank, src))


def match_paren(src, i):
    """Index just past the ')' closing the '(' at src[i]. -1 if unbalanced."""
    depth = 0
    while i < len(src):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        elif src[i] in "\"'":
            quote, i = src[i], i + 1
            while i < len(src) and src[i] != quote:
                i += 2 if src[i] == "\\" else 1
        i += 1
    return -1


def first_argument(args):
    """The text before the first comma that is not inside brackets or a string."""
    depth = 0
    for i, c in enumerate(args):
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c in "\"'":
            return args  # a format string this early means no condition to read
        elif c == "," and depth == 0:
            return args[:i]
    return args


def unwrap(text):
    """Drop parentheses that enclose the whole expression.

    PREDICT_FALSE (x) leaves `(x)` behind once the macro name is removed, and
    a condition still inside brackets has no operator at depth zero.
    """
    text = text.strip()
    while text.startswith("(") and match_paren(text, 0) == len(text):
        text = text[1:-1].strip()
    return text


def normalise(side):
    side = CAST.sub(" ", side)
    side = MEMBER.sub("", side)
    return re.sub(r"\s+", "", unwrap(side))


def split_comparison(cond):
    """(left, op, right) for a single top-level comparison, else None.

    Anything carrying a top-level && or || is refused rather than guessed at:
    a chain is several claims and this suite only reasons about one.
    """
    depth = 0
    found = None
    i = 0
    while i < len(cond):
        c = cond[i]
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c in "\"'":
            quote, i = c, i + 1
            while i < len(cond) and cond[i] != quote:
                i += 2 if cond[i] == "\\" else 1
        elif depth == 0:
            if cond.startswith("&&", i) or cond.startswith("||", i):
                return None
            for op in OPS:
                if cond.startswith(op, i):
                    # "=" of "==" already consumed; a lone "=" is an assignment.
                    if found is not None:
                        return None  # a < b < c is not a comparison worth reading
                    found = (i, op)
                    i += len(op) - 1
                    break
        i += 1
    if found is None:
        return None
    at, op = found
    left, right = normalise(cond[:at]), normalise(cond[at + len(op):])
    if not left or not right:
        return None
    return (left, op, right)


ENDS_A_PATH = re.compile(r"\A\s*(?:return\b|break\s*;|continue\s*;|goto\b)")


def product_guards(src):
    """(left, op, right) -> the guard text, for every `if (...) <ends a path>`."""
    out = {}
    for m in re.finditer(r"\bif\s*(?=\()", src):
        open_at = src.index("(", m.end() - 1)
        close = match_paren(src, open_at)
        if close < 0:
            continue
        body = src[close:]
        # PREDICT_FALSE (x) wraps the condition and says nothing about it.
        cond = src[open_at + 1:close - 1]
        cond = unwrap(re.sub(r"\bPREDICT_(?:TRUE|FALSE)\s*", "", cond))
        if not ENDS_A_PATH.match(body):
            continue
        parts = split_comparison(cond)
        if parts:
            out.setdefault(parts, cond.strip())
    return out


def harness_checks(src):
    """(line, (left, op, right), text) for every CHECK whose first argument
    is a single comparison."""
    out = []
    for m in re.finditer(r"\bCHECK\s*(?=\()", src):
        open_at = src.index("(", m.end() - 1)
        close = match_paren(src, open_at)
        if close < 0:
            continue
        cond = unwrap(first_argument(src[open_at + 1:close - 1]))
        parts = split_comparison(cond)
        if parts:
            out.append((src.count("\n", 0, open_at) + 1, parts, cond.strip()))
    return out


INCLUDE = re.compile(r'#\s*include\s+"([^"]+)"')

harnesses = sorted(glob.glob(os.path.join(TEST, "test_bpa_*.c")))
# 18 since #397 added the block-identity store's, 17 since #21 added MD5's and
# the RADIUS parser's, 15 with the identity
# table's, 14 since #20 added the syslog line's, and 13 since #390 batch I, which added the PPTP ALG's, the TCP log writer's and
# the audit trail's. The number is written down so that a glob that stops
# matching goes red instead of quietly reading fewer files than the
# repository has - which is what this line is for, and why it moves with the
# harnesses rather than being loosened to a floor.
check(len(harnesses) == 20,
      f"{len(harnesses)} C harnesses found, not the 20 this repository has - "
      f"the glob is probably wrong and this suite is reading too little")

n_checks = 0
n_guards = 0
for path in harnesses:
    name = os.path.basename(path)
    raw = open(path, encoding="utf-8").read()
    src = strip_comments(raw)

    # The product this harness compiles against, as the harness itself names
    # it. A .h included from src/plugins/bpa is read with its .c beside it,
    # because a guard may live in either.
    guards = {}
    for inc in INCLUDE.findall(src):
        if "plugins/bpa" not in inc:
            continue
        base = os.path.normpath(os.path.join(os.path.dirname(path), inc))
        for prod in (base, os.path.splitext(base)[0] + ".c"):
            if os.path.exists(prod):
                guards.update(
                    product_guards(strip_comments(
                        open(prod, encoding="utf-8").read())))
    n_guards += len(guards)

    for ln, (left, op, right), text in harness_checks(src):
        n_checks += 1
        wanted = (left, NEGATION[op], right)
        if wanted in guards:
            check(False,
                  f"test/{name}:{ln} asserts what the product already "
                  f"refused: the guard `if ({guards[wanted]})` ends that "
                  f"path, so no input reaching this line can make it false\n"
                  f"         CHECK ({text})\n"
                  f"         assert what the product COMPUTED, against the "
                  f"fixture, rather than which branch it took")

# A suite that reads nothing passes, so the two floors below are what this
# parser actually reached when it was written - 294 of the 382 CHECK sites
# reduced to a single comparison, and 74 path-ending guards across the product
# files the ten harnesses include - each rounded down to leave room for
# ordinary editing. A parser broken by a refactor drops well below them.
check(n_checks > 250,
      f"only {n_checks} CHECK conditions were read as comparisons across "
      f"{len(harnesses)} harnesses, where 294 were read when this suite was "
      f"written - the parser is probably broken and this suite is passing by "
      f"reading nothing")
check(n_guards > 60,
      f"only {n_guards} product guards were read, where 74 were read when "
      f"this suite was written - the parser is probably broken")

if not fail:
    print(f"  {len(harnesses)} harnesses, {n_checks} CHECK comparisons "
          f"against {n_guards} product guards: none restates one")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
