#!/usr/bin/env python3
"""
What this appliance is allowed to do to itself on the way down.

`docs/appliance.md` states it and `appliance/systemd/prisma-routing.service`
repeats it in a comment:

    No `ExecStop` on `prisma-routing.service`. The configuration lives in VPP.
    Stopping the service MUST NOT delete production pools; removing a pool is
    the operator's action, through the VTY.

Nothing executable held it. Adding

    ExecStop=/usr/bin/prismash -c "delete nat cgnat ; commit"

to that unit left all 34 suites green (#238). `test/test_install_manifest.py`
asserts the unit is INSTALLED and never reads a byte of it, and the only thing
in the tree that reads an `ExecStop` at all - `lab/ipfix-stop.sh` - reads it out
of a different unit for the opposite purpose.

The cost of the gap is not a stopped box. `prisma-routing.service` carries
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

"Not on prisma-routing.service, allowed on vpp.service" is also the wrong rule. It
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
     `ExecStop=/usr/lib/prisma-routing/teardown`, whose body this check cannot
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
# holding. prismash is deliberately absent: it IS the configuration surface - the
# candidate, `commit`, the pool definitions and the file under
# /etc/prisma-routing - so there is no argument list that makes it a safe thing to
# run unattended at stop time, and listing it here with a careful-looking
# argument is the mutation this check exists to refuse. A shell is absent
# because its body is a string this check cannot read, and neither can the
# person reviewing the diff.
#
# docker-compose joins it for one verb and one reason (#396). With the
# dataplane in a container, stopping it IS stopping its container, and the
# program that does that is compose. What matters for this rule is what the
# verb leaves behind: `down` removes containers and networks, and volumes
# ONLY with -v, which is refused below. The retention log, the configuration
# and the audit trail are bind mounts of directories on the box, so they are
# exactly where they were - the same as `systemctl stop vpp`, which is what
# this replaces.
STOP_PROGRAMS = ("/usr/bin/vppctl", "/usr/bin/docker-compose")

# And of that program, the verbs that only flush. Each one is a promise that
# running it leaves the configuration exactly as it found it.
STOP_VERBS = ("bpa stop-drain", "down")

#: The words that make a stop-time compose command destructive, whatever else
#: is on the line. `-v` takes the volumes with it, and a box whose retention
#: log lived in one would lose a year of connection records on an ordinary
#: restart; `--rmi` deletes the images the medium carried, leaving a box that
#: cannot start until somebody reinstalls it.
COMPOSE_FORBIDDEN = ("-v", "--volumes", "--rmi")

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

    # THE VERB, AFTER THE OPTIONS THAT NAME A FILE. vppctl's line is the verb
    # and nothing else; compose's is `-f <path> down -t 30`, so taking
    # everything after the program would compare a path against a verb list
    # and refuse a command this table blesses. Only `-f <path>` is skipped -
    # an option this check does not know stays in `rest` and is refused,
    # which is the direction to be wrong in.
    words = list(words)
    if words[0].endswith("docker-compose"):
        i = 1
        while i + 1 < len(words) and words[i] in ("-f", "--file"):
            i += 2
        words = [words[0]] + words[i:]

    rest = " ".join(words[1:])
    # And the flags AFTER the verb, which for `down -t 30` is a timeout: a
    # number is not a verb and refusing it would refuse the drain's own
    # window. Anything that is not a flag-and-value pair stays.
    if words[0].endswith("docker-compose") and len(words) > 2:
        tail = words[2:]
        if len(tail) == 2 and tail[0] in ("-t", "--timeout") and \
           tail[1].isdigit():
            rest = words[1]

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
    "appliance/systemd/prisma-routing-containers.service": [
        ("ExecStop",
         "/usr/bin/docker-compose -f /etc/prisma-routing/compose.yaml "
         "-f /run/prisma-routing/compose-devices.yaml down -t 30",
         "#396: with the dataplane in a container, stopping it is stopping "
         "the container, and compose is what does that. `down` removes "
         "containers and networks; volumes need -v, which COMPOSE_FORBIDDEN "
         "refuses. The retention log, /etc/prisma-routing and the audit "
         "trail are bind mounts of directories on the box and are left "
         "exactly as they were. The -t 30 is the drain's: compose.yaml gives "
         "the dataplane 30 seconds to stop, and a shorter timeout here would "
         "cut off the stop-drain that writes the release records. The "
         "SECOND -f is the fragment the unit generated at start, naming the "
         "card's device node: compose computes the project from the files "
         "it is given, so `down` with only the base file would be `down` "
         "against a different definition of the same project."),
    ],
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
    ('/usr/bin/prismash -c "delete nat cgnat ; commit"', "delete",
     "#238's own mutation, character for character"),
    ('/usr/bin/prismash -c "no cgnat pool subs ; commit"', "no",
     "the same in the vocabulary the VTY actually has"),
    ("/usr/bin/prismash --load /etc/prisma-routing/prisma-routing.conf", "re-applies",
     "re-applying the configuration file at stop time"),
    ("/usr/bin/prismash --confirm-expired", "prismash",
     "prismash with no destructive word in sight"),
    ("/bin/sh -c '/usr/bin/vppctl bpa stop-drain'", "sh",
     "a shell wrapping the very command that is allowed"),
    ("/usr/lib/prisma-routing/teardown", "teardown",
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
            # A comment is where the rule is WRITTEN DOWN - prisma-routing.service
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
            # A READER OF THE DIRECTIVE IS NOT A DECLARATION OF ONE, and
            # this is not hypothetical: appliance/vpp-entrypoint carries
            # `sed -n 's/^ExecStop=-\{0,1\}//p'` because it reads the drain
            # out of the shipped drop-in instead of retyping it, and this
            # check read that pattern as a unit declaring
            # `ExecStop='-\{0,1\}//p'`. Refusing it would push the wrapper
            # into retyping the command, which is the one thing the drop-in
            # exists to prevent. lab/ipfix-stop.sh reads it the same way.
            #
            # Narrow on purpose: the line must be RUNNING a text tool. A unit
            # file has no sed in it, so nothing this rule is about can hide
            # behind this.
            if re.search(r"\b(sed|grep|awk)\b", s.split("ExecStop")[0]):
                continue
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


print("\n=== the container's stop path is judged by the same rule ===")
# THE RULE HAD TO FOLLOW THE DRAIN (#396). Everything above reads ExecStop
# lines in unit files, and under a container supervisor there are none: the
# ordering moves into appliance/vpp-entrypoint, because Compose has no
# pre-stop hook. A rule that only reads unit files would have gone quiet at
# exactly the moment the thing it guards became OUR code instead of
# systemd's - and a trap handler can grow a second command far more easily
# than a declarative line can.
ENTRY = os.path.join(REPO, "appliance", "vpp-entrypoint")
if not os.path.exists(ENTRY):
    bad("appliance/vpp-entrypoint is gone, and with it the only thing that "
        "runs the drain before a container's SIGTERM reaches VPP")
else:
    entry = open(ENTRY, encoding="utf-8").read()
    body = [l for l in entry.splitlines() if not l.lstrip().startswith("#")]

    # IT RUNS WHAT THE DROP-IN SAYS, and nothing it typed itself. A literal
    # command here is a second statement of what stopping means, which can be
    # a teardown while the drop-in is a drain - and this file would be
    # reading the wrong one.
    typed = [l.strip() for l in body
             if re.search(r"\b(vppctl|prismash|vtysh)\b", l)
             and "VPP_BIN" not in l]
    if typed:
        bad(f"appliance/vpp-entrypoint runs a command of its own at stop "
            f"time: {typed}. It reads the drain out of the drop-in so that "
            f"the two supervisors cannot disagree about what stopping means, "
            f"and a line typed here is judged by nobody")
    else:
        ok("the wrapper types no stop-time command of its own")

    # AND IT WAITS FOR IT. A drain that is backgrounded is the defect
    # lab/ipfix-stop.sh no-wait measures: the graph stops before the release
    # record can be sent over UDP, and the stopped box reads exactly like one
    # that fell over (#217).
    drains = [l.strip() for l in body if re.search(r"\$DRAIN\b", l)]
    ran = [l for l in drains if not l.startswith("if") and "-n" not in l]
    if not ran:
        bad("appliance/vpp-entrypoint never runs $DRAIN, so the drain it "
            "read out of the drop-in is not what stops the box")
    elif any(l.rstrip().endswith("&") for l in ran):
        bad(f"appliance/vpp-entrypoint backgrounds the drain: {ran}. The "
            f"signal then reaches VPP while the drain is still running, the "
            f"release record goes into a graph that has stopped, and a "
            f"stopped box becomes indistinguishable from one that fell over")
    else:
        ok(f"and it waits for the drain it read: {ran[0]}")

    # AND THE ORDER IS DRIVEN SOMEWHERE. This file reads text; only
    # test/test_vpp_entrypoint.sh can watch the ordering happen.
    if not os.path.exists(os.path.join(REPO, "test", "test_vpp_entrypoint.sh")):
        bad("test/test_vpp_entrypoint.sh is gone, and nothing then watches "
            "the wrapper's ordering with the drain actually running")
    else:
        ok("and the ordering itself is driven in test_vpp_entrypoint.sh")


print("\n=== a compose stop that would take data with it ===")
# THE WORDS, not the program. `down` is a stop; `down -v` is a box that lost
# its volumes on an ordinary restart. Nothing in this tree writes one today,
# and this is what keeps it that way - including in the table above, which
# is where somebody would put it if they meant it.
for _rel, _entries in ALLOWED_STOP.items():
    for _directive, _value, _reason in _entries:
        for _word in COMPOSE_FORBIDDEN:
            if "docker-compose" in _value and \
               (f" {_word} " in f" {_value} " or _value.endswith(" " + _word)):
                bad(f"ALLOWED_STOP blesses {_rel}: {_directive}={_value!r}, "
                    f"and {_word} makes it destructive")
for _rel, _directive, _value in found:
    for _word in COMPOSE_FORBIDDEN:
        if "docker-compose" in _value and \
           (f" {_word} " in f" {_value} " or _value.endswith(" " + _word)):
            bad(f"{_rel} runs compose with {_word} at stop time: {_value!r}")
ok(f"no stop-time compose command carries any of {', '.join(COMPOSE_FORBIDDEN)}")

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
if "No `ExecStop` on `prisma-routing.service`" in doc and \
        "MUST NOT** delete production pools" in doc:
    ok("docs/appliance.md still states it, in RFC 2119 keywords")
else:
    bad("docs/appliance.md no longer states that stopping prisma-routing.service "
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
