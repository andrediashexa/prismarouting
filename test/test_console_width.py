#!/usr/bin/env python3
"""
No literal row this VTY prints is wider than the console it prints on.

WHY THIS IS A SWEEP OF THE FILE AND NOT A LIST OF SCREENS. The same defect
has now been closed four times - #252, #271, #349 and #355 - and each of those
fixed the rows it had MEASURED. #355 split seven refusals of exactly this
shape; a static sweep afterwards found fourteen more literal rows still at 82
to 135 columns against a console of 79, none of them covered by anything, plus
a fifteenth that the sweep could not even see because it was an f-string
(#359). A check that drives fifteen screens is a check the sixteenth walks
past. So the rule is written on the FILE: every fully literal row the source
prints, measured, with nothing to keep in step and no screen to remember to
add.

WHAT IT MEASURES, exactly. A `print()` call whose single argument is a string
with NO value interpolated into it - a plain constant, or an f-string that
turned out to hold only literal pieces. Each row of it (these strings carry
embedded newlines in places) is measured against `config.CONSOLE_COLUMNS`,
read from the module rather than written here, because the budget and the
product must move together.

WHAT IT CANNOT SEE, and the number is printed on every run so nobody reads
this rule as covering more than it does. Most of what the VTY prints carries
an interpolated value, and the width of those rows is not a property of the
source: an IPv6 neighbour is 39 characters where an IPv4 one is 15, and a
route-map name is whatever the operator called it. Bounding those needs the
widest value each site can hold, which is a separate and harder problem -
#276 did it for `say_finding` by DRIVING the sites with real input, which is
the only honest way. #359 says so explicitly and leaves that half open.

The second thing it cannot see is a refusal's VERDICT row. `err` prints
`f"error: {msg}"`, so the row on the console is seven characters wider than
the string the source shows, and `msg` is interpolated at most call sites.
Driven rather than read - `configure` on a box whose dataplane is silent -
that verdict is 84 columns while every prose row under it now wraps. Same
problem as the interpolated rows, same open half; the count is printed too.

Rows that already go through `print_prose` are measured here too and cost
nothing: `console_prose` wraps to the same budget, so a row it produced is
inside it by construction. What this catches is the row that never went
through it.
"""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src", "hexash"))
import config      # noqa: E402

fail = 0


def bad(msg):
    global fail
    fail += 1
    print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


#: Every module of the VTY, not only the one that prints today. hexash.py is
#: the only one with a `print()` in it at the moment - measured, all four of
#: the others are at zero - and a rule scoped to the file that happens to have
#: the defect is a rule the next module that grows a screen walks past.
SOURCES = ("hexash.py", "config.py", "routes.py", "system.py", "vppapi.py")


def literal(node):
    """The string this argument always is, or None if a value goes into it."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        # An f-string whose pieces are all constants. `f"a" f"b"` is one of
        # these, and several sites in the VTY are written that way because an
        # adjacent row does interpolate.
        out = []
        for piece in node.values:
            if not isinstance(piece, ast.Constant):
                return None
            out.append(piece.value)
        return "".join(out)
    return None


print("=== every literal row the VTY prints fits the console (#359) ===")
print(f"  the budget is config.CONSOLE_COLUMNS = {config.CONSOLE_COLUMNS}, "
      f"read from the module")

measured = valued = blank = 0
for name in SOURCES:
    path = os.path.join(REPO, "src", "hexash", name)
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = getattr(node.func, "id", None) or getattr(node.func, "attr",
                                                           None)
        if called != "print":
            continue
        if not node.args:
            # `print()` on its own: a blank row, which fits any console.
            blank += 1
            continue
        text = literal(node.args[0]) if len(node.args) == 1 else None
        if text is None:
            # Either a value goes into it, or it is a multi-argument print
            # whose rendered width depends on what the arguments turn out to
            # be. Both are the half this suite does not claim.
            valued += 1
            continue
        for row in text.split("\n"):
            measured += 1
            if len(row) > config.CONSOLE_COLUMNS:
                bad(f"src/hexash/{name}:{node.lineno}: {len(row)} columns on "
                    f"a console of {config.CONSOLE_COLUMNS} - {row.strip()!r}")

# A sweep that parsed nothing passes, and would have passed on the day the
# fourteen were there. The floor is what says the parse still finds the rows:
# it was 64 when this was written, and it is a floor rather than an equality
# so that deleting a screen is not a failure.
FLOOR = 50
if measured < FLOOR:
    bad(f"only {measured} literal row(s) found in {len(SOURCES)} module(s); "
        f"the walk has stopped seeing what it is supposed to measure")
elif fail == 0:
    ok(f"{measured} literal row(s), all inside {config.CONSOLE_COLUMNS} "
       f"columns")

print(f"  NOT MEASURED HERE: {valued} print() call(s) carry an interpolated "
      f"value")
print("  their width depends on what is interpolated - an IPv6 neighbour is "
      "39")
print("  characters where an IPv4 one is 15 - and bounding them needs the "
      "widest")
print("  value each site can hold, which #359 leaves open. Driven, not read: "
      "the")
print("  ones that have been bounded are bounded by driving them "
      "(#276, #349, #355).")
print(f"  ({blank} bare print() call(s), which are blank rows.)")

# The other half this rule does not claim, and the reason it is a number here
# rather than an assertion. A refusal's verdict goes through `err`, which
# prints `f"error: {msg}"` - so the row on the console is seven characters
# wider than the string in the source, and `msg` is itself interpolated at
# most call sites. Measured by DRIVING `configure` on a box whose dataplane is
# silent, that verdict reaches the console at 84 columns while every prose row
# under it now wraps. Bounding these is the same problem as the interpolated
# rows above and belongs with them, not smuggled in here.
verdicts = 0
for name in SOURCES:
    path = os.path.join(REPO, "src", "hexash", name)
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if getattr(node.func, "attr", None) in ("err", "err_prose",
                                                "err_dataplane"):
            verdicts += 1
print(f"  NOT MEASURED HERE EITHER: {verdicts} refusal verdict row(s), which "
      f"reach")
print("  the console seven characters wider than the source shows, through "
      "err().")

if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
