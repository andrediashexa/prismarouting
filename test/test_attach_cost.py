#!/usr/bin/env python3
"""
Attaching the CGNAT to one interface does not walk the box.

WHAT THIS DEFENDS. `set interface bpa in2out` cost 4.3 ms per call at 5,000
interfaces and 8.3 ms at 10,000 - 83 seconds to attach ten thousand - because
`bpa_interface_feature_enable_disable()` called `bpa_auto_mss_update()`, which
walked every interface on the box asking the feature arc four times per
interface whether the CGNAT was on it. N attaches were N walks. The controls
measured in the same session say the feature arc is not to blame:
`set interface mtu` 32 us, `policer add` 22 us, `policer input`, which uses
the same arc, 42 us (#287).

WHY THIS IS A SOURCE CHECK AND NOT A TIMING ONE. Two reasons, and the first
is the one that matters.

  * A stopwatch measures the machine. This repository runs a release lab suite
    on the same box as `make test`, and a wall-clock assertion under that load
    goes red for a reason that has nothing to do with the code being asserted.
    A red check that is about the neighbours is worse than no check: it trains
    whoever reads it to re-run rather than to look.

  * The ALGORITHM is asserted next door, by counting rather than by timing.
    test/test_bpa_auto_mss.c attaches 8,192 interfaces and asserts that the
    last one reads exactly as many slots as the first, and that the whole
    sweep reads 8,192 slots rather than the 33 million a rescan-per-attach
    would. That covers the data structure completely and cannot see ONE
    thing: whether the attach path still uses it. Putting `bpa_auto_mss_update
    ()` back on that path restores the quadratic with every unit assertion
    still green, because the structure it was asserted against is no longer
    what the attach calls.

So this file asserts the wiring, which is the half a unit test cannot reach,
and it is deliberately a small, dumb, literal check of the source: the cost of
this defect was measured once, on hardware, and nobody should have to measure
it again to find out that it came back.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BPA_C = os.path.join(ROOT, "src", "plugins", "bpa", "bpa.c")

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


def ok_unless_red(before, msg):
    """The green line only when nothing above it went red.

    A `[ ok ]` printed directly under a FAIL of its own block reads as though
    the block passed, which is how a red run gets skimmed past.
    """
    if fail == before:
        ok(msg)


def body_of(text, name):
    """The braces of one C function definition, or None.

    Matches a definition and not a call: the name must sit at the start of a
    line, which is where this tree's style puts it and where a call never is.
    """
    m = re.search(r"^" + re.escape(name) + r" \(", text, re.M)
    if not m:
        return None
    open_brace = text.find("{", m.end())
    if open_brace < 0:
        return None
    depth = 0
    for i in range(open_brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace:i + 1]
    return None


TEXT = open(BPA_C, encoding="utf-8").read()

print("=== attaching one interface is about that interface ===")

attach = body_of(TEXT, "bpa_interface_feature_enable_disable")
per_if = body_of(TEXT, "bpa_auto_mss_interface_update")
sweep = body_of(TEXT, "bpa_auto_mss_update")

# The extraction first. A parser that stopped matching would report the tree
# compliant by finding nothing, which is the failure mode this project keeps
# hitting, so it is asserted before anything is concluded from it.
check(attach is not None,
      "bpa_interface_feature_enable_disable() was not found in bpa.c, so this "
      "test is passing by finding nothing")
check(per_if is not None,
      "bpa_auto_mss_interface_update() was not found in bpa.c, so the attach "
      "path has nothing constant to call")
check(sweep is not None,
      "bpa_auto_mss_update() was not found in bpa.c, so the once-a-second "
      "reconcile this test distinguishes from the attach path is gone")

if attach is not None:
    before = fail
    # The call itself, and not merely the word: `bpa_auto_mss_interface_update`
    # contains `bpa_auto_mss_update` as a substring, so the boundary matters.
    check(not re.search(r"(?<![_a-zA-Z])bpa_auto_mss_update\s*\(", attach),
          "bpa_interface_feature_enable_disable() calls bpa_auto_mss_update(), "
          "which is the whole-box pass. That is #287 exactly: one attach then "
          "costs a walk of every interface on the box, and attaching N of them "
          "costs N walks - 83 seconds for ten thousand. It must call "
          "bpa_auto_mss_interface_update(sw_if_index) instead")
    check(re.search(r"bpa_auto_mss_interface_update\s*\(\s*sw_if_index\s*\)",
                    attach),
          "bpa_interface_feature_enable_disable() does not tell the auto-MSS "
          "bookkeeping which interface changed, so the clamp is left "
          "describing a set of interfaces that no longer exists (#183)")
    ok_unless_red(before, "the attach path updates one interface, not every interface")

if per_if is not None:
    before = fail
    # What "proportional to the box" looks like in this file: a walk of VPP's
    # interface pool, or a walk of the plugin's own per-interface array.
    for needle, what in (
        ("pool_foreach", "a walk of a VPP pool"),
        ("sw_interfaces", "a walk of vnm->interface_main.sw_interfaces"),
        ("auto_mss_mtu)", "a loop over the whole per-interface array"),
        ("for (", "a loop"),
        ("while (", "a loop"),
    ):
        check(needle not in per_if,
              f"bpa_auto_mss_interface_update() contains {what}. It is what "
              f"one attach calls, so anything in it that grows with the number "
              f"of interfaces is the quadratic coming back")
    ok_unless_red(before, "the per-interface update contains no loop at all")

if sweep is not None:
    before = fail
    # The once-a-second reconcile is allowed a pass, and is allowed it only
    # over the interfaces the CGNAT is on. Over the BOX is what was removed.
    check("sw_interfaces" not in sweep and "pool_foreach" not in sweep,
          "bpa_auto_mss_update() walks VPP's interface pool again. An "
          "interface the CGNAT is not attached to cannot change the clamp, so "
          "the pass belongs over bm->auto_mss_mtu")
    check("vnet_sw_interface_is_valid" in sweep,
          "bpa_auto_mss_update() no longer checks that a slot's interface "
          "still exists. An interface deleted while the CGNAT was on it does "
          "not come through bpa_interface_feature_enable_disable(), so its "
          "slot survives, and reading the MTU of a freed pool entry reads "
          "whatever is there now")
    ok_unless_red(before, "the once-a-second reconcile is over the CGNAT interfaces, not the box")

# And the counting assertion must still be in the build. A suite that is
# written and not run is a suite that is not there.
suites = open(os.path.join(ROOT, "test", "suites.list"), encoding="utf-8").read()
check("test_bpa_auto_mss" in suites,
      "test/suites.list no longer runs test_bpa_auto_mss, which is where the "
      "cost of an attach is counted")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
