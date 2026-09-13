#!/usr/bin/env python3
"""
What `appliance/install.sh` puts on the box, against what the repository has.

Two directions, and each has already cost this project something.

FILES THE INSTALLER NAMES AND THE REPOSITORY DOES NOT HAVE. A rename here
fails the ISO build, which is the good case, but it fails it from inside a
container half way through a debootstrap with the path buried in the log. It
is a second's work to say so here.

FILES THE REPOSITORY HAS AND THE INSTALLER DOES NOT NAME. This is the
dangerous direction, and `appliance/systemd/` is where it bites: a drop-in
that exists in the tree and never reaches the box is a setting that silently
does not apply. #172 is exactly that shape one step later - a drop-in systemd
parsed and REFUSED reads identically to one that is working - and #182 is the
same again for a `linux_cp` pair the product never created. A drop-in nobody
installs has no symptom at all.

Only `appliance/systemd/` is checked in that direction. The rest of
`appliance/` legitimately holds things the installer does not copy - the
installer itself is run from the medium, `etc/` files are templates - and a
rule that demanded everything would be a rule somebody switches off.
"""
import os
import re
import sys

fail = 0


def bad(msg):
    global fail
    fail += 1
    print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = open(os.path.join(REPO, "appliance", "install.sh"),
              encoding="utf-8").read()


def loop_values(var, before):
    """The words the `for <var> in ...` loop enclosing offset `before` walks.

    The NEAREST such loop, not the first in the file. install.sh uses `d`
    twice - once over the FRR daemons and once over the three documents - and
    resolving by name alone took the daemon list for the documents and
    reported four files missing that nothing ever installs. The check was
    wrong, not the installer.
    """
    best = None
    for m in re.finditer(r"for\s+" + re.escape(var) + r"\s+in\s+(.*?);\s*do",
                         SCRIPT, re.S):
        if m.start() < before:
            best = m
    if best is None:
        return []
    # The list may be wrapped with a backslash at the end of each line.
    return best.group(1).replace("\\\n", " ").split()


print("=== every file the installer names is in the repository ===")
# The two loops are expanded rather than skipped: a unit or a document dropped
# from the tree and left in the loop is the same defect as a misspelled path,
# and skipping them would leave nine of the files unchecked.
named, missing, unresolved = [], [], []
for m in re.finditer(r'"\$REPO/([^"]+)"', SCRIPT):
    path = m.group(1)
    variants = [path]
    if path.endswith("$u"):
        variants = [path[:-2] + v for v in loop_values("u", m.start())]
    elif path.endswith("$d.md"):
        variants = [path[:-5] + v + ".md"
                    for v in loop_values("d", m.start())]
    if any("$" in v for v in variants):
        unresolved.append(path)
        continue
    for v in variants:
        named.append(v)
        # .build-vpp holds the compiled plugin, which exists only after a
        # build. Its absence here is not a defect in the installer.
        if v.startswith(".build-vpp/"):
            continue
        if not os.path.exists(os.path.join(REPO, v)) and v not in missing:
            missing.append(v)
if unresolved:
    bad(f"these installer paths carry a variable this check cannot expand, so "
        f"they are not being checked at all: {unresolved}")
if missing:
    for m in missing:
        bad(f"appliance/install.sh installs {m!r} and the repository does not "
            f"have it: the ISO build fails half way through a debootstrap with "
            f"that path buried in the log")
else:
    ok(f"all {len(named)} of them, the two loops expanded")

print("\n=== every systemd file in the tree is installed ===")
# The direction with no symptom. A drop-in in the tree that the installer does
# not copy is a setting nobody applied, on a box that boots and runs and says
# nothing.
units = sorted(os.listdir(os.path.join(REPO, "appliance", "systemd")))
uninstalled = [u for u in units if u not in SCRIPT]
if uninstalled:
    for u in uninstalled:
        bad(f"appliance/systemd/{u} is in the tree and appliance/install.sh "
            f"never names it: a drop-in that does not reach the box has no "
            f"symptom at all")
else:
    ok(f"all {len(units)} unit(s) and drop-in(s) are named by the installer")

print("\n=== and the check is reading something ===")
# Without this the whole file passes on an install.sh that has been emptied,
# which is the shape #151 spent an issue on.
if len(named) >= 15 and len(units) >= 5:
    ok(f"{len(named)} installed path(s) and {len(units)} systemd file(s) were "
       f"compared")
else:
    bad(f"only {len(named)} installed path(s) and {len(units)} systemd file(s) "
        f"were found, so this file is asserting almost nothing")

if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
