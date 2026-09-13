#!/usr/bin/env python3
"""
Every BPA feature node on the ip4-unicast arc declares runs_after on
ip4-sv-reassembly-feature.

docs/architecture.md says the nodes MUST declare it: shallow virtual
reassembly is what puts the first fragment's ports into
vnet_buffer(b)->ip.reass, and a BPA node placed ahead of it reads fields that
have not been written yet. The cost is one phantom session and one burnt port
per fragment, and it is silent.

Why a source check, when reading the running box is normally the better
instrument: the box cannot tell the difference. Measured on VPP 26.06, with
all four declarations deleted, an interface carrying both features still
reports

    ip4-unicast:
      ip4-sv-reassembly-feature
      bpa-in2out

because VPP's arc sort falls back to registration order when no constraint
names the pair, and registration order happens to agree today. The order is
therefore right by accident, and every dataplane assertion in lab/frag.sh
stays green with the guarantee gone. What the deletion removes is not today's
order, it is the promise that it survives a VPP upgrade, a link order change
or another plugin's constraints. Only the source says whether that promise is
still made, so this is where it is checked.

lab/frag.sh checks the other half, which this cannot see: that the order the
box ACTUALLY computed puts reassembly first. A declaration that is present and
ineffective is red there and green here.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "src", "plugins", "bpa")
REASS = "ip4-sv-reassembly-feature"

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


# VNET_FEATURE_INIT (name, static) = { ... };
BLOCK = re.compile(r"VNET_FEATURE_INIT\s*\(\s*(\w+)[^)]*\)\s*=\s*\{(.*?)\n\};",
                   re.S)


def field(body, name):
    """The value of one initializer entry, quotes and all."""
    m = re.search(r"\.%s\s*=\s*(.*?)[,;]?\s*$" % name, body, re.M)
    return m.group(1) if m else None


def feature_list(body, name):
    """The names inside .<name> = VNET_FEATURES (...), which MAY wrap over
    several lines. Read line by line it would miss every name but the
    first."""
    m = re.search(r"\.%s\s*=\s*VNET_FEATURES\s*\((.*?)\)" % name, body, re.S)
    return re.findall(r'"([^"]+)"', m.group(1)) if m else []


found = []
for name in sorted(os.listdir(PLUGIN)):
    if not name.endswith(".c"):
        continue
    path = os.path.join(PLUGIN, name)
    src = open(path).read()
    for m in BLOCK.finditer(src):
        sym, body = m.group(1), m.group(2)
        arc = field(body, "arc_name")
        node = (field(body, "node_name") or "").strip('"')
        if arc is None or '"ip4-unicast"' not in arc:
            continue
        found.append((name, sym, node))
        after = feature_list(body, "runs_after")
        check(REASS in after,
              f"{name}: {sym} ({node}) sits on ip4-unicast and does not "
              f"declare .runs_after = VNET_FEATURES (\"{REASS}\"). A fragment "
              f"can reach it before its ports have been recovered, which "
              f"burns one port of the subscriber's block per fragment")

# A parser that stopped matching would report every node compliant by finding
# none of them, which is the failure mode this project keeps hitting. Four
# nodes exist today; a fifth is welcome and must carry the declaration, so the
# floor is what is asserted, not the exact count.
check(len(found) >= 4,
      f"only {len(found)} BPA feature node(s) on ip4-unicast were parsed out "
      f"of {PLUGIN}; there are four, so the extraction is broken and this "
      f"test is passing by finding nothing")

# The requirement itself has to stay written down. If somebody decides the
# ordering is no longer needed, that is a decision to argue in the document,
# not one this test should keep enforcing silently.
doc = open(os.path.join(ROOT, "docs", "architecture.md")).read()
check(REASS in doc and "runs_after" in doc,
      f"docs/architecture.md no longer states the runs_after requirement on "
      f"{REASS}, so this test is enforcing a rule the project stopped making")

if not fail:
    for name, sym, node in found:
        print(f"  {node} ({name}) runs after {REASS}")
    print(f"  {len(found)} BPA nodes on ip4-unicast, all ordered behind "
          f"reassembly")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
