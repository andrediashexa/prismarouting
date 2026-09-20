#!/usr/bin/env python3
"""
The prompt and a saved file, asked the same question, compared answer by
answer.

WHAT THIS IS FOR. Every finding in #411 was a validator on one door and not
the other, and five statements meant two different things depending on how
they arrived: `mtu 999999` was refused at the prompt and taken from a file,
`ports 9000 100` the same, and a description with a control character was
silently stripped at one door and kept at the other. A file is what
`prisma-routing.service` replays at every boot, so the door with no guard on
it is the one nobody is watching.

Those five are fixed. This is what stops the sixth, and it is a READER rather
than a list: it walks the grammar the VTY already has, builds hostile values
out of each statement's own hint, drives BOTH doors with them, and fails when
the two answers differ. A statement added tomorrow is compared by nobody's
decision.

WHAT IT CANNOT SEE, stated rather than left to be discovered:

  * Statements this VTY hands to FRR unread. The two doors agree about those
    by both forwarding them, and an interface's FRR half does not even reach
    the file's interface parser - it is written in the routing section. The
    scope is therefore `prismash.INTERFACE_DATAPLANE`, which is the product's
    own one place for "which of these statements are this box's" (#224), and
    the pool block, which is entirely this box's.
  * The `cgnat` globals. Their file door is not a parser at all - the line
    goes into `Config.globals` and is APPLIED at commit - so there is no
    second answer here to compare against. That is a third door and its own
    piece of work.
  * Whether either answer is RIGHT. This compares two answers; two identical
    wrong answers pass. The five sub-issues of #411 are what made the answers
    right, and this is what keeps them in step.

THE DECLARED DIFFERENCE. One class of statement is meant to differ, and it is
declared in the product as `config.DOORS_DIFFER` rather than as an allowlist
in here - for the reason `NOT_LABS` holds its reasons as data: an exception
nobody can read is one nobody re-examines. Anything else that differs is a
failure, and there is no ceiling to raise.
"""
import contextlib
import io
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src",
                                "prismash"))
import config                                                  # noqa: E402
import prismash                                                # noqa: E402
from config import Config, Pool                                # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


# --------------------------------------------------------------- the walk

def value_leaves(node, path=()):
    """Every statement in `node` that takes a value, with its hint."""
    out = []
    for word, entry in node.items():
        if not isinstance(entry, tuple) or len(entry) < 2:
            continue
        arg = entry[1]
        if isinstance(arg, dict):
            out += value_leaves(arg, path + (word,))
        elif isinstance(arg, str):
            out.append((path + (word,), arg))
    return out


RANGE = re.compile(r"<(\d+)-(\d+)>")


def hostile(hint):
    """
    Values the hint's own shape says MUST be refused, or None if unread.

    FROM THE HINT and not from a table, which is what makes this discovered
    rather than listed. A hint this cannot read is NAMED on every run rather
    than skipped in silence: a statement nobody is comparing has to be
    visible, which is the idiom test_every_function.py already uses.
    """
    m = RANGE.search(hint)
    if m:
        lo, hi = (int(x) for x in m.groups())
        out = ["abc", "²", str(hi + 1)]
        if lo > 0:
            out.append(str(lo - 1))
        return out
    if "A.B.C.D/M" in hint:
        return ["potato", "999.1.1.1/24", "10.0.0.1"]
    if "A.B.C.D" in hint:
        return ["potato", "999.1.1.1"]
    if "xx:xx:xx" in hint:
        return ["zz:zz:zz:zz:zz:zz", "ff:ff:ff:ff:ff:ff"]
    if hint in ("<text>",):
        return ["A" * 4000, "up\x07link"]
    if hint in ("<name>", "<pool>"):
        return ["A" * 4000, "na\x07me"]
    return None


# --------------------------------------------------------------- the doors

class Dp(prismash.Vpp):
    """A dataplane that answers nothing. See test_prismash_refusals.py."""

    TRACE = False

    def __init__(self):
        self.commands = []

    def run(self, cmd):
        self.commands.append(cmd)
        return 0, ""

    def running(self):
        return 0, ""


def a_shell():
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT}
    sh.dp = Dp()
    sh.frr = type("F", (prismash.Frr,), {
        "available": lambda self: False,
        "show": lambda self, cmd: (1, ""),
        "reload": lambda self, lines, check_only=False: (0, "")})()
    sh.mode = "config"
    sh.frr_ctx = []
    sh.candidate = Config()
    return sh


def at_the_prompt(where, stmt):
    """True when the prompt refuses `stmt`, or a string naming a crash."""
    sh = a_shell()
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            if where == "interface":
                sh.do_config("interface pg0")
                sh.do_config(stmt)
            else:
                sh.mode, sh.pool = "pool", Pool("p")
                sh.do_pool(stmt)
    except Exception as e:                                      # noqa: BLE001
        return f"CRASH {type(e).__name__}: {e}"
    return "error" in out.getvalue()


def in_a_file(where, stmt):
    """True when a saved file refuses `stmt`, or a string naming a crash."""
    text = (f"interface pg0\n {stmt}\n" if where == "interface"
            else f"cgnat pool p\n {stmt}\n")
    try:
        cfg = Config.parse(text)
    except Exception as e:                                      # noqa: BLE001
        return f"CRASH {type(e).__name__}: {e}"
    return bool(cfg.unknown)


# ------------------------------------------------------------- the compare

#: The interface statements this box owns, from the product's own one place
#: for that list. Everything else in the block is FRR's, is handed over word
#: for word, and is written in the routing section rather than here - so a
#: file's interface parser never sees it and there is no second answer.
OURS = {" ".join(stmt) for stmt in prismash.INTERFACE_DATAPLANE}

#: The one declared difference, expanded from what the product says.
DECLARED = set()
for _key, (_stmts, _why) in config.DOORS_DIFFER.items():
    DECLARED |= {("pool", s) for s in _stmts}
# `timeout` is one row of Pool.RANGES and eight statements in the grammar.
DECLARED |= {("pool", f"timeout {name}") for name in Pool.TIMEOUTS}

print("=== the grammar this reader walks ===")
CONTEXTS = (("interface", prismash.INTERFACE_BLOCK),
            ("pool", prismash.CGNAT_POOL))
walked, unread, compared = [], [], 0
for where, tree in CONTEXTS:
    for path, hint in value_leaves(tree):
        if path[0] == "no":
            continue
        statement = " ".join(path)
        if where == "interface" and path[0] not in OURS \
                and statement not in OURS:
            continue
        values = hostile(hint)
        if values is None:
            unread.append((where, statement, hint))
            continue
        walked.append((where, statement, values))
check(len(walked) >= 20,
      f"only {len(walked)} statements walked; the grammar cannot have shrunk "
      f"that far without something else being wrong")
ok(f"{len(walked)} statement(s) this box owns, in {len(CONTEXTS)} contexts")

print("\n=== and the hints it could not read, named rather than skipped ===")
# A hint this cannot build a hostile value from is a statement nobody is
# comparing. Named on every run, and counted, the way test_every_function.py
# names what it cannot classify - because the alternative is a silent gap that
# grows.
for where, statement, hint in unread:
    print(f"    {where:10} {statement:28} {hint}")
ok(f"{len(unread)} hint(s) this reader cannot build a value from")

print("\n=== the same value, through both doors ===")
differ = []
for where, statement, values in walked:
    for value in values:
        stmt = f"{statement} {value}"
        prompt, saved = at_the_prompt(where, stmt), in_a_file(where, stmt)
        if isinstance(prompt, str) or isinstance(saved, str):
            differ.append((where, statement, value, prompt, saved))
            continue
        compared += 1
        if prompt == saved:
            continue
        if (where, statement) in DECLARED:
            continue
        differ.append((where, statement, value, prompt, saved))
for where, statement, value, prompt, saved in differ:
    print(f"    {where:10} {statement:26} {value[:18]:20} "
          f"prompt={prompt} file={saved}")
check(not differ,
      f"{len(differ)} statement(s) answer differently at the two doors, and "
      f"are not declared in config.DOORS_DIFFER. Fix the door that is wrong, "
      f"or declare the difference IN THE PRODUCT with its reason - there is "
      f"no allowlist here to add to")
ok(f"{compared} value(s) driven through both doors, "
   f"{len(DECLARED)} statement(s) declared to differ")

print("\n=== and the declaration is real, not a name nobody reads ===")
# Mutation shown red: DOORS_DIFFER emptied, which turns every pool number
# into a failure - so the declaration is load-bearing rather than decorative.
check(config.DOORS_DIFFER,
      "config.DOORS_DIFFER is empty; the exception it holds is the #357 one "
      "and removing it silently would make this check refuse a correct box")
for _key, (_stmts, _why) in config.DOORS_DIFFER.items():
    check(_stmts, f"{_key}: declared with no statements")
    check(len(_why) > 40 and "#" in _why,
          f"{_key}: declared with no reason and no issue behind it: {_why!r}")
check(("pool", "block-size") in DECLARED,
      "the #357 class really covers block-size, which is the statement that "
      "produced it")
ok(f"{len(config.DOORS_DIFFER)} declared difference(s), each with its reason")

print("\n=== and the generator itself, over a hint it cannot read ===")
# The "named and counted" half, exercised rather than assumed. A grammar
# fixture with three shapes: one this reader knows, one it does not, and one
# whose sub-tree it has to walk into to find either.
_FIXTURE = {
    "known": ("a bounded number", "<1-99>"),
    "unknown": ("a shape with no hostile value", "<on|off>"),
    "nested": ("a sub-tree", {"inner": ("an address", "<A.B.C.D>")}),
    "noargument": ("a statement with no value at all", None),
}
_leaves = dict((" ".join(p), h) for p, h in value_leaves(_FIXTURE))
check(set(_leaves) == {"known", "unknown", "nested inner"},
      f"the walk finds value-taking leaves and nothing else: "
      f"{sorted(_leaves)!r}")
check(hostile(_leaves["known"]) and "100" in hostile(_leaves["known"]),
      "a range yields one past its top")
check("0" in hostile(_leaves["known"]),
      "and one below its bottom, where the bottom is not zero")
check(hostile(_leaves["nested inner"]) == ["potato", "999.1.1.1"],
      "an address yields a word and a bad octet")
check(hostile(_leaves["unknown"]) is None,
      "and a shape it cannot read answers None, which is what puts it in the "
      "named list rather than quietly out of scope")
check(hostile("<0-7>") and "0" not in hostile("<0-7>"),
      "a range whose bottom IS zero does not get a negative it cannot "
      "spell - the generator would otherwise report a false disagreement on "
      "every statement that takes 0")
ok("the generator, over a grammar written to have all three shapes")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
