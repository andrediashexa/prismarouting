#!/usr/bin/env python3
"""The installer creates every group the VTY's authorization names (#563).

src/prismash/system.py names the role groups and the operator capability as
GROUP_* constants; appliance/install.sh creates the groups at install. A
group the VTY checks and the installer does not create is a capability that
silently does nothing on a fresh box - prisma-operator was exactly that
between 0.149.26 and 0.150.0, made only by the first commit that granted it.
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = 0


def check(cond, msg):
    global failures
    if cond:
        print(f"  [ ok ] {msg}")
    else:
        print(f"  FAIL: {msg}")
        failures += 1


system = open(os.path.join(REPO, "src/prismash/system.py")).read()
groups = re.findall(r'^GROUP_[A-Z_]+ = "([a-z-]+)"', system, re.M)
check(set(groups) >= {"prisma-admin", "prisma-cgnat", "prisma-operator"},
      f"system.py names the three groups: {groups}")

install = open(os.path.join(REPO, "appliance/install.sh")).read()
loop = re.search(r"^for g in ([a-z -]+); do\n(?:.*\n){0,3}?.*groupadd --system", install, re.M)
check(loop is not None, "install.sh has its group-creating loop")
made = loop.group(1).split() if loop else []
for g in groups:
    check(g in made, f"install.sh creates {g}")

print()
if failures:
    print(f"{failures} FAILURES")
    sys.exit(1)
print("ALL TESTS PASSED")
