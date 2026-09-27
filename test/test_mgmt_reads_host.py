"""
The management plane reads the host's facts, not its own (#448, phase C).

The VTY reads the box's NICs, PCI bus, CPUs, cgroups, routes, live medium and
startup.conf straight off the filesystem. In the management plane's container
those paths are the container's own - measured on 0.137.0's first gate, where
the VTY in there told the live medium's operator to reboot - so compose.yaml
mounts the host's subtrees read-only under /host and points each read at them.

Read off the source, so a read added to prismash.py without its line in
compose.yaml fails here and not on a box:

  * every `os.environ.get("PRISMA_*", "<host path>")` whose default is under
    /sys, /proc, /run/live, /etc/vpp, /etc/network or /usr/sbin is set in the
    mgmt service's environment, to /host + the default (the routes: the host
    init's own, /host/proc/1/net/route);
  * the /host subtree each points into is mounted, read-only;
  * no such path is written as a bare literal, where no environment reaches.

Mutation shown red: PRISMA_PCI_DEVICES removed from compose.yaml.
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..")
HOSTY = re.compile(r"^/(sys|proc|run/live|etc/vpp|etc/network|usr/sbin)/")
fail = 0


def check(c, msg):
    global fail
    print(("  [ ok ] " if c else "  FAIL: ") + msg)
    fail += 0 if c else 1


tree = ast.parse(open(os.path.join(REPO, "src", "prismash", "prismash.py")).read())
reads, defaults = {}, set()
for n in ast.walk(tree):
    if isinstance(n, ast.Call) and ast.unparse(n.func) == "os.environ.get" \
            and len(n.args) == 2 and isinstance(n.args[1], ast.Constant) \
            and isinstance(n.args[1].value, str):
        defaults.add(id(n.args[1]))
        if HOSTY.match(n.args[1].value):
            reads[n.args[0].value] = n.args[1].value
bare = [(n.lineno, n.value) for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and HOSTY.match(n.value) and id(n) not in defaults]

# The mgmt service, read as text: the file has no YAML reader in this image,
# and the two lists it is held to are one line per entry.
compose = open(os.path.join(REPO, "appliance", "compose.yaml")).read()
mgmt = compose[compose.index("\n  mgmt:"):]
env = dict(re.findall(r"^\s+- (PRISMA_[A-Z_]+)=(\S+)$", mgmt, re.M))
mounts = re.findall(r"^\s+- (/\S+):(/host/\S+):ro$", mgmt, re.M)

print("=== every host read in prismash.py is pointed at /host ===")
check(len(reads) >= 12, f"{len(reads)} host read(s) found in prismash.py")
for name, default in sorted(reads.items()):
    want = "/host/proc/1/net/route" if name == "PRISMA_PROC_NET_ROUTE" \
        else "/host" + default
    check(env.get(name) == want,
          f"{name}: {default} is read at {env.get(name, '<not set>')} "
          f"in the container (want {want})")

print("\n=== and each is under a /host subtree that is mounted read-only ===")
for name, path in sorted(env.items()):
    if not path.startswith("/host/"):
        continue
    under = [m for m in mounts if path.startswith(m[1] + "/")]
    check(bool(under) and path.startswith(under[0][1]),
          f"{name}: {path} is under {under[0][1] if under else '<no mount>'}")
check(all(m[0] == m[1][len("/host"):] for m in mounts),
      f"every /host mount is the same path on the host: {mounts}")
check(not any(m[0] in ("/", "/run") for m in mounts),
      "neither / nor /run is mounted: /run/docker.sock would come with it")

print("\n=== no host path is a bare literal no environment reaches ===")
check(not bare, f"bare host paths in prismash.py: {bare}")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
