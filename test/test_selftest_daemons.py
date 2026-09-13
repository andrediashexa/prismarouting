#!/usr/bin/env python3
"""
The routing daemons `appliance/selftest` looks for, against the ones the VTY
offers to configure.

Two copies of one truth, in two languages. The selftest has the list written
into a shell loop; the VTY derives it from `TREE["config"]["router"]`, which
is the grammar an operator is actually offered. Nothing compared them.

THE LIST HAS ALREADY BEEN WRONG THIS WAY. #187: the VTY accepted `router
ospf6`, the installer enabled every daemon but ospf6d, and OSPFv3 became the
one protocol a box could be configured for and could never run. The comment
above that loop says so in words. A comment is the repair for the time it
happened, not a check against the next time.

Both directions cost something:

  A PROTOCOL THE VTY OFFERS AND THE SELFTEST DOES NOT LOOK FOR. The #187
  shape. A fresh box passes its own self-test and cannot run what somebody is
  about to configure on it, and the self-test is the one thing that was meant
  to catch that before they did.

  A DAEMON THE SELFTEST LOOKS FOR AND THE VTY CANNOT CONFIGURE. A warning
  about a daemon nobody on this box has a use for. Harmless once; it is how a
  self-test becomes something operators learn to scroll past.

The comparison is here rather than in the shell deliberately. The selftest
runs on a box, early, and must not depend on the VTY being importable - the
same reason `test/test_install_manifest.py` compares `appliance/install.sh`
against the repository from here rather than making the installer introspect.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "hexash"))
import hexash      # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELFTEST = open(os.path.join(REPO, "appliance", "selftest"),
                encoding="utf-8").read()

print("=== the daemons the selftest looks for are the ones the VTY offers ===")

# The loop is found by the variable it fills, not by its position: `missing` is
# what the block below it reports, so a loop that stopped filling `missing`
# would be a different defect and this check would rightly not find the list.
m = re.search(r"for\s+d\s+in\s+([a-z0-9 ]+);\s*do\s*\n"
              r"\s*case\s+\" \$daemons \"[^\n]*missing=\"\$missing \$d\"",
              SELFTEST)
check(m is not None,
      "the daemon loop in appliance/selftest is not where this check looks for "
      "it, so nothing below is comparing anything. It is the `for d in ...` "
      "whose case arm appends to $missing")

if m:
    looked_for = sorted(m.group(1).split())
    # ALWAYS_STARTED is excluded on both sides. frrinit.sh starts zebra, mgmtd
    # and staticd itself, and the selftest checks those separately - "FRR
    # responding" is that check. Requiring them in this loop would fail on a
    # correct selftest.
    offered = sorted(d for d in hexash.configurable_daemons()
                     if d not in hexash.ALWAYS_STARTED)

    missing = [d for d in offered if d not in looked_for]
    for d in missing:
        check(False,
              f"the VTY offers a protocol that commits to {d}, and "
              f"appliance/selftest does not look for it: a fresh box passes "
              f"its own self-test and cannot run what it was configured for. "
              f"That is #187 exactly, and the comment above that loop is about "
              f"the last time it happened")

    extra = [d for d in looked_for if d not in offered]
    for d in extra:
        check(False,
              f"appliance/selftest warns that {d} is not running, and this VTY "
              f"has no grammar that commits to it: a warning about a daemon "
              f"nobody on this box has a use for is how a self-test becomes "
              f"something operators scroll past")

    if not missing and not extra:
        ok(f"the same {len(offered)} daemon(s), both ways: "
           f"{' '.join(offered)}")

    # And the check is reading something. Without this it passes on a loop
    # that has been emptied, which is the shape #151 spent an issue on.
    check(len(looked_for) >= 3,
          f"the loop yielded {looked_for}, which is too few for this file to "
          f"be asserting anything")
    check(len(offered) >= 3,
          f"configurable_daemons() came back with {offered}, so the grammar "
          f"side of the comparison is empty")

if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
