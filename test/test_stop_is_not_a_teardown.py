#!/usr/bin/env python3
"""
What this appliance is allowed to do to itself on the way down.

`docs/appliance.md` states it and `appliance/systemd/hexarouting.service`
repeats it in a comment:

    No `ExecStop` on `hexarouting.service`. The configuration lives in VPP.
    Stopping the service MUST NOT delete production pools; removing a pool is
    the operator's action, through the VTY.

Nothing executable held it. Adding

    ExecStop=/usr/bin/hexash -c "delete nat cgnat ; commit"

to that unit left all 34 suites green (#238). `test/test_install_manifest.py`
asserts the unit is INSTALLED and never reads a byte of it, and the only thing
in the tree that reads an `ExecStop` at all - `lab/ipfix-stop.sh` - reads it out
of a different unit for the opposite purpose.

The cost of the gap is not a stopped box. `hexarouting.service` carries
`PartOf=vpp.service`, so an `ExecStop` there fires on every restart: a tuning
change, a plugin upgrade, a `systemctl restart vpp` typed during an unrelated
diagnosis. The failure would arrive during an ordinary upgrade, on a box that
had been working, and every subscriber behind the deleted pool would lose
translation at once.

WHERE THE LINE IS DRAWN, AND WHY THERE

"No ExecStop anywhere" is the wrong rule, because this product needs one:
`vpp-stop-drain.conf` runs `vppctl bpa stop-drain` before the signal so that
every block the dataplane holds is closed in the retention log while the graph
is still running to carry the records (#217). A rule that forbade it would be
switched off within a week, and the rule that gets switched off protects
nothing.

"Not on hexarouting.service, allowed on vpp.service" is also the wrong rule. It
is a rule about which FILE a line is typed in, so it is silent on the mutation
that matters most: the drain file itself edited into a deletion. The unit that
already has permission to run a command at stop time is the cheapest place to
put one.

So the line is drawn on WHAT THE COMMAND DOES, not on which unit carries it:

    A stop-time command MAY ask a running process to flush state it is already
    holding. It MUST NOT reach the configuration surface.

A drain is idempotent and additive: it writes records for blocks that were
already allocated, and running it twice changes nothing a third time. It takes
nothing away that an operator put there, and starting the unit again finds the
box exactly as the drain left it. A deletion is the opposite in every one of
those directions, and the box cannot tell the difference at the moment it
happens - both are a command that exits 0 while the machine is going down and
nobody is watching the console.

Mechanically that becomes two layers, and the second is the one the file-name
rule was missing:

  1. Every stop-time directive anywhere under `appliance/` is in ALLOWED_STOP
     below, byte for byte, with the reason it is there. Anything else fails and
     is quoted. This catches the sweep's mutation, a new unit, and a directive
     written by `install.sh` rather than by a unit file - and it catches
     `ExecStop=/usr/lib/hexarouting/teardown`, whose body this check cannot
     read, because an unreadable command is not an allowed one.
  2. Every command in ALLOWED_STOP is put through `teardown_reason()` and MUST
     come back clean. Blessing a line in the table is therefore not enough to
     ship it: a maintainer who edits the drop-in AND the table still has to get
     a command past the line above.

And the classifier is itself tested, on the sweep's own mutation, because a
needle that matches nothing reads exactly like a rule that is holding.

WHAT IS NOT ASSERTED HERE. That `bpa stop-drain` actually drains: that claim
needs packets and belongs to `lab/ipfix-stop.sh`, which runs the drain out of
this same file rather than retyping it. And nothing here runs systemd. This is
a check on a shipped text artifact, which is the argument
`test/test_kernel_tuning.py` already makes about the GRUB line.
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

# systemd's stop-time hooks. There is no ExecStopPre; ExecStopPost runs after
# the process is gone and can still run any command at all, so it is in scope
# for exactly the same reason ExecStop is.
STOP_DIRECTIVES = ("ExecStop", "ExecStopPost")

# The programs a unit MAY run while the box is going down.
#
# vppctl talks to the running graph and can ask it to flush what it is already
# holding. hexash is deliberately absent: it IS the configuration surface - the
# candidate, `commit`, the pool definitions and the file under
# /etc/hexarouting - so there is no argument list that makes it a safe thing to
# run unattended at stop time, and listing it here with a careful-looking
# argument is the mutation this check exists to refuse. A shell is absent
# because its body is a string this check cannot read, and neither can the
# person reviewing the diff.
STOP_PROGRAMS = ("/usr/bin/vppctl",)

# And of that program, the verbs that only flush. Each one is a promise that
# running it leaves the configuration exactly as it found it.
STOP_VERBS = ("bpa stop-drain",)

# Words that say the command takes something away. Matched as whole tokens, so
# "stop-drain" is not "drain" and "no" is not the "no" inside a path. This list
# is the informative half: STOP_PROGRAMS and STOP_VERBS would refuse all of
# these anyway, but a refusal that says "names 'delete'" tells the reader which
# half of their line was the problem.
DESTRUCTIVE_WORDS = ("delete", "del", "no", "clear", "commit", "remove", "rm",
                     "purge", "reset", "destroy", "flush", "unset", "shutdown")


def teardown_reason(cmd):
    """None if `cmd` is a drain by the rule above, else why it is not.

    `cmd` is the raw value of the directive, systemd's prefixes included.
    """
    # systemd's execution prefixes. "-" means a non-zero exit is not a failure
    # of the stop, which is right for a drain against a dataplane that is
    # already gone; none of them changes what the command does.
    body = cmd.lstrip("-@:+!")
    words = body.split()
    if not words:
        return "it is empty, so nothing can say what it does"

    named = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]*", body.lower())
             if w in DESTRUCTIVE_WORDS]
    if named:
        return (f"it names {named[0]!r}: a stop is not the place to take "
                f"anything away")
    if "--load" in body:
        return ("it re-applies a configuration file, which is a commit and not "
                "a flush")

    if words[0] not in STOP_PROGRAMS:
        return (f"{words[0]!r} is not one of the programs a unit may run at "
                f"stop time ({', '.join(STOP_PROGRAMS)}); anything else is "
                f"either the configuration surface or a command this check "
                f"cannot read")

    rest = " ".join(words[1:])
    if not any(rest == v or rest.startswith(v + " ") for v in STOP_VERBS):
        return (f"{rest!r} is not one of the stop-time verbs "
                f"({', '.join(repr(v) for v in STOP_VERBS)}); every other "
                f"verb either changes the configuration or changes state the "
                f"box will be asked about later")
    return None


# The stop-time commands this product ships, each with the reason it is not a
# teardown. Keyed by the path in the tree; the installed name is different and
# is `test/test_install_manifest.py`'s business.
ALLOWED_STOP = {
    "appliance/systemd/vpp-stop-drain.conf": [
        ("ExecStop", "-/usr/bin/vppctl bpa stop-drain",
         "#217: in dynamic mode the block allocation log IS the connection "
         "record, and a block still held when the dataplane stops used to "
         "leave its allocation open in that log for ever. The drain closes "
         "them while the graph is still running to carry the records, which "
         "an in-process exit hook cannot do over UDP. It writes records for "
         "blocks that were already allocated and removes nothing."),
    ],
}


print("=== the classifier can tell a drain from a teardown ===")
# First, because everything below is this function applied to files. A needle
# that matches nothing is green in exactly the same way a rule that holds is,
# and that is the trap this whole issue is about.
CASES = [
    ("/usr/bin/vppctl bpa stop-drain", None,
     "the drain this product ships"),
    ("-/usr/bin/vppctl bpa stop-drain", None,
     "the same with systemd's '-' prefix"),
    ('/usr/bin/hexash -c "delete nat cgnat ; commit"', "delete",
     "#238's own mutation, character for character"),
    ('/usr/bin/hexash -c "no cgnat pool subs ; commit"', "no",
     "the same in the vocabulary the VTY actually has"),
    ("/usr/bin/hexash --load /etc/hexarouting/hexarouting.conf", "re-applies",
     "re-applying the configuration file at stop time"),
    ("/usr/bin/hexash --confirm-expired", "hexash",
     "hexash with no destructive word in sight"),
    ("/bin/sh -c '/usr/bin/vppctl bpa stop-drain'", "sh",
     "a shell wrapping the very command that is allowed"),
    ("/usr/lib/hexarouting/teardown", "teardown",
     "a script whose body this check cannot read"),
    ("-/usr/bin/vppctl bpa pool del subs", "del",
     "the allowed program, on the wrong verb"),
    ("/usr/bin/vppctl show bpa pool", "show",
     "even a read: the drain is the only thing with a reason to be here"),
]
for cmd, want, desc in CASES:
    got = teardown_reason(cmd)
    if want is None:
        if got is None:
            ok(f"{desc}: allowed")
        else:
            bad(f"{desc}: refused, and it must not be - {got}")
    elif got is None:
        bad(f"{desc}: ALLOWED by teardown_reason(), so this check would let "
            f"{cmd!r} ship")
    elif want in got:
        ok(f"{desc}: refused, {got.split(':')[0]}")
    else:
        bad(f"{desc}: refused for the wrong reason - expected the refusal to "
            f"turn on {want!r}, got {got!r}")


print("\n=== every stop-time directive this product ships ===")
# The whole of appliance/, not only appliance/systemd/. A drop-in is one way to
# get a command run at stop time; a unit file written by install.sh at install
# time is another, and it would leave appliance/systemd/ untouched.
SKIP_DIRS = {".git", "__pycache__"}
scanned, found = [], []
for root, dirs, files in os.walk(os.path.join(REPO, "appliance")):
    dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
    for name in sorted(files):
        path = os.path.join(root, name)
        rel = os.path.relpath(path, REPO)
        try:
            text = open(path, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        scanned.append(rel)
        # systemd continues a directive across a trailing backslash, so a
        # deletion could be parked on the second line of one.
        for line in text.replace("\\\n", " ").splitlines():
            s = line.strip()
            # A comment is where the rule is WRITTEN DOWN - hexarouting.service
            # explains at length why it has no ExecStop, and refusing that
            # sentence would delete the explanation to satisfy the check.
            if s.startswith("#") or s.startswith(";"):
                continue
            # No \b before the name. It cost a mutation to find out why: a
            # directive written by a script arrives as
            # `printf '[Service]\nExecStop=...'`, and the character in front
            # of it is the "n" of a literal \n, which is a word character. The
            # word boundary was never there and the installer-written unit
            # sailed through green.
            m = re.search("(" + "|".join(STOP_DIRECTIVES) + r")\s*=\s*(.*)", s)
            if m:
                # Cut at a literal \n so a generated line is quoted back as the
                # command it writes rather than as the rest of the printf.
                found.append((rel, m.group(1),
                              m.group(2).split("\\n")[0].strip().rstrip("'\"")))

for rel, directive, value in found:
    entry = next((e for e in ALLOWED_STOP.get(rel, [])
                  if e[0] == directive and e[1] == value), None)
    if entry:
        ok(f"{rel}: {directive}={value} - {entry[2].split('.')[0]}.")
        continue
    why = teardown_reason(value)
    bad(f"{rel} declares {directive}={value!r} and nothing in this repository "
        f"says it may. " +
        (f"{why.capitalize()}. " if why else "") +
        f"A stop-time command runs on every RESTART as well as on a stop, so "
        f"it fires during an ordinary upgrade on a box that was working. If "
        f"this is a drain and not a teardown, say so in ALLOWED_STOP in "
        f"{os.path.relpath(__file__, REPO)} with the reason")

for rel, entries in ALLOWED_STOP.items():
    for directive, value, _ in entries:
        if (rel, directive, value) not in found:
            # The other direction, and it is a real regression: the drain going
            # missing is #217 coming back, and a box that stops without it
            # answers "who held this address at 14:22" with a subscriber who
            # had already been disconnected.
            bad(f"ALLOWED_STOP expects {rel} to declare {directive}={value!r} "
                f"and it does not. Either the drain was removed - which "
                f"reopens #217 - or it was reworded, and this check has been "
                f"blessing a line that is no longer there")


print("\n=== and being in the table is not enough on its own ===")
# The mutation the file-name rule cannot see: the unit that already has
# permission to run something at stop time, edited to run a deletion, with the
# table updated to match.
for rel, entries in ALLOWED_STOP.items():
    for directive, value, reason in entries:
        why = teardown_reason(value)
        if why:
            bad(f"ALLOWED_STOP blesses {rel}: {directive}={value!r}, and it is "
                f"a teardown, not a drain: {why}. The reason given was "
                f"{reason.split('.')[0]!r}")
        else:
            ok(f"{rel}: {value!r} flushes and removes nothing")


print("\n=== the rule is still written where an operator reads it ===")
# A check whose justification has been deleted from the document is a check the
# next reader has no way to argue with.
DOC = os.path.join(REPO, "docs", "appliance.md")
doc = open(DOC, encoding="utf-8").read()
if "No `ExecStop` on `hexarouting.service`" in doc and \
        "MUST NOT** delete production pools" in doc:
    ok("docs/appliance.md still states it, in RFC 2119 keywords")
else:
    bad("docs/appliance.md no longer states that stopping hexarouting.service "
        "MUST NOT delete production pools. That sentence is what this suite "
        "enforces; if the rule changed, this file changes with it")


print("\n=== and the check is reading something ===")
# Without this the whole file passes on an appliance/ that has been emptied,
# on a walk whose filter excluded everything, and on a repository where the
# drain was deleted along with its table entry.
if len(scanned) >= 20 and len(found) >= 1 and len(CASES) >= 8:
    ok(f"{len(scanned)} file(s) under appliance/ read, {len(found)} stop-time "
       f"directive(s) found, {len(CASES)} classifier case(s) exercised")
else:
    bad(f"only {len(scanned)} file(s) read, {len(found)} stop-time "
        f"directive(s) found and {len(CASES)} classifier case(s) exercised, "
        f"so this file is asserting almost nothing")

if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
