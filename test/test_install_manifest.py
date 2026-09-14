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

FILES THE INSTALLER READS AND THE MEDIUM DOES NOT CARRY. The third direction,
and the one with the ugliest symptom. `appliance/build-iso.sh` copies four top
level directories into the chroot the installer then runs inside. A path the
installer names outside those four exists in the repository, so both directions
above stay green, and the ISO build fails from inside a debootstrap with that
path buried in the log - which is the exact case the first paragraph says is a
second's work to catch here (#234). Worse when the installer guards the read
with `[ -f ... ]`: the medium builds, the box installs, and whatever that file
configured is simply absent.

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

print("\n=== every directory the installer reads is on the medium ===")
# Compared, rather than left as a coincidence that has held. The two lists
# agreed on the day this was written and nothing had ever checked that they
# did.
ISO = open(os.path.join(REPO, "appliance", "build-iso.sh"),
           encoding="utf-8").read()
m = re.search(r"cp -a ((?:\"\$REPO/[^\"]+\"\s*)+)\"\$ROOT/src/\"", ISO)
carried = set(re.findall(r'\"\$REPO/([^\"/]+)\"', m.group(1))) if m else set()

# The top level directory of every path the installer names. .build-vpp is the
# compiled plugin, which build-iso.sh copies by itself as $PLUGIN_SO and
# $VAT2_SO rather than as part of the tree, so it is not expected in the list
# above and its absence is not a defect.
reads = {p.split("/")[0] for p in named} - {".build-vpp"}

if not carried or not reads:
    bad(f"this check could not read one of its two lists: build-iso.sh copies "
        f"{sorted(carried)} and install.sh reads from {sorted(reads)}. It is "
        f"asserting nothing")
else:
    absent = sorted(reads - carried)
    if absent:
        for d in absent:
            # No trailing slash added: a path with no directory part, such
            # as VERSION, is a file at the top of the tree and calling it a
            # directory sends the reader looking for one.
            bad(f"appliance/install.sh reads {d} and "
                f"appliance/build-iso.sh does not put it on the medium: the "
                f"ISO build fails inside a debootstrap with the path buried in "
                f"the log, and an installer that guards the read instead "
                f"produces a box missing whatever that file configured")
    else:
        ok(f"all {len(reads)} of them: {sorted(reads)}, out of the "
           f"{len(carried)} the medium carries")

print("\n=== nothing is written before the installer can still refuse ===")
# THE PROPERTY test/test_install_says.sh CANNOT HOLD. That file RUNS the
# installer and reads what it did to one root, so it proves that the three
# files it checksums did not move. It cannot prove that no OTHER write happens
# before the refusal, because a write it does not know about is a write it does
# not look at. This is a property of the text and belongs here.
#
# WHY IT MATTERS. install.sh refuses when it cannot find one of the two plugins
# the build produces, and a refusal is only worth taking while it still costs
# nothing - which is true only while nothing has been written. Both refusals
# used to sit forty lines down, past the whole of src/hexash: a tree with no
# dataplane plugin left a box carrying a NEW VTY over an OLD dataplane, and a
# tree with no vat2 plugin left one carrying a new dataplane behind the
# PREVIOUS version's vat2 plugin with the new version stamped on it (#301).
# Both are the same shape: the run got far enough that stopping no longer put
# the box back. An install line added above the marker brings that shape back,
# and it would come back silently, because a half-installed root still boots.
#
# HOW A WRITE IS RECOGNISED, and it is deliberately generous. The verbs that
# put bytes somewhere, plus any redirection into $DESTDIR. groupadd, chsh and
# `systemctl enable` are in the list although they never name $DESTDIR: they
# write on the live box, and a refusal after root's login shell has been
# changed is exactly as expensive as one after a file copy. Reads are not
# writes: the PREV_STAMP `cat` and the `grep` over /etc/frr/daemons name
# $DESTDIR and stay out of the list, which is why the verb has to be there.
MARKER = "# END OF THE PREFLIGHT."
WRITE = re.compile(r"\binstall\s+-|\bln\s+-s|\bchmod\s|\bchgrp\s|"
                   r"\bsed\s+-i\s|\bgroupadd\s|\bchsh\s|"
                   r"\bsystemctl\s+enable\b|\bsystemctl\s+daemon-reload\b|"
                   r'>>?\s*"\$DESTDIR')

# Backslash continuations joined first. Nine of the install lines in this file
# put the source on one line and the destination on the next, and a check that
# read physical lines would call `install -D -m 0644 "$REPO/..." \` a write and
# miss nothing - but it would also fail to see the `sed -i` whose $DESTDIR
# argument is on the following line. The line number kept is the first physical
# one, which is what a reader would go and look at.
logical, buf, start = [], "", None
for n, raw in enumerate(SCRIPT.split("\n"), 1):
    if start is None:
        start = n
    if raw.endswith("\\"):
        buf += raw[:-1] + " "
        continue
    logical.append((start, buf + raw))
    buf, start = "", None

marker_at = [n for n, t in logical if t.lstrip().startswith(MARKER)]
writes = [(n, t.strip()) for n, t in logical
          if not t.lstrip().startswith("#") and WRITE.search(t)]

if len(marker_at) != 1:
    bad(f"appliance/install.sh has {len(marker_at)} line(s) beginning "
        f"{MARKER!r} and this check needs exactly one. It cannot tell where "
        f"the preflight ends, so it is asserting nothing about where the "
        f"installer can still refuse for free")
elif not writes:
    bad("no line in appliance/install.sh was recognised as writing anything, "
        "so this check would stay green on an installer that refuses after "
        "installing the whole product")
else:
    early = [(n, t) for n, t in writes if n < marker_at[0]]
    if early:
        for n, t in early:
            bad(f"appliance/install.sh line {n} writes before the preflight "
                f"ends at line {marker_at[0]}: {t!r}. A refusal below it is a "
                f"refusal on a root that has already been changed, which is "
                f"the half-installed box #301 is about")
    else:
        ok(f"all {len(writes)} write(s) are below line {marker_at[0]}, so "
           f"every refusal above them leaves the root untouched")

# And that there is something up there to refuse WITH. The marker on its own
# would keep the check green on a preflight that resolves both plugins and
# shrugs, which is the build this replaced.
head = SCRIPT.split(MARKER)[0] if marker_at else ""
_missing = [so for so in ("bpa_plugin.so", "bpa_test_plugin_bpa_plugin.so")
            if not re.search(re.escape(so) + r'[^"]*not found', head)]
if marker_at and not _missing:
    ok("and both plugins are refused by name above it")
elif marker_at:
    bad(f"the preflight of appliance/install.sh does not refuse {_missing} by "
        f"name. A component resolved and not refused is one the installer "
        f"carries on without, which is #301 exactly: it warned on stderr, "
        f"installed the rest and stamped the box with a build it had half of")

print("\n=== the file list the box keeps is written after the last write ===")
# WHY THIS IS A PROPERTY OF THE TEXT. install.sh writes
# /usr/share/hexarouting/MANIFEST, which is what the box compares itself
# against at every boot (#302), and it can only be a complete list of what
# this run installed if it is written after this run has installed it. An
# install line added below that block puts a file on the box that the manifest
# does not name - and a file the manifest does not name is a file nothing will
# ever check, which is the two-lists defect the manifest exists to avoid,
# arriving by the back door.
#
# It cannot be measured by running the installer: test/test_manifest.sh does
# run it and compares the manifest against the root, and that comparison is
# what proves the mechanism works TODAY, on the lines that exist today. A line
# added below the block would have to be added to that suite's list of
# expected exceptions to go green, which is a thing somebody does while looking
# at the failure. This is the half that goes red without being negotiated
# with. Same division of labour as the preflight marker above.
MANIFEST_AT = 'say "manifest"'
manifest_at = [n for n, t in logical if t.strip() == MANIFEST_AT]
if len(manifest_at) != 1:
    bad(f"appliance/install.sh has {len(manifest_at)} line(s) reading "
        f"{MANIFEST_AT!r} and this check needs exactly one to know where the "
        f"installing stops. It is asserting nothing about what the manifest "
        f"is a list of")
else:
    # The manifest's own two lines are the only writes allowed below it, and
    # they are recognised by naming it: a rule that counted lines would go
    # green on any two writes at all.
    after = [(n, t.strip()) for n, t in writes
             if n > manifest_at[0] and "MANIFEST" not in t]
    own = [n for n, t in writes if n > manifest_at[0] and "MANIFEST" in t]
    if after:
        for n, t in after:
            bad(f"appliance/install.sh line {n} writes below the manifest at "
                f"line {manifest_at[0]}: {t!r}. The manifest is the list this "
                f"box checks itself against, and a file installed after it is "
                f"written is a file nothing on the box will ever check")
    elif len(own) < 2:
        bad(f"only {len(own)} line(s) below line {manifest_at[0]} write the "
            f"manifest at all, so this check would stay green on an installer "
            f"that has stopped writing one")
    else:
        ok(f"the {len(own)} line(s) that write it are the last writes in the "
           f"file")

print("\n=== and a file the operator owns is guarded in exactly one place ===")
# THE DECISION THE MANIFEST RESTS ON. install.sh writes two kinds of file:
# those it overwrites on every run, which ARE the build, and those it writes
# once and never touches again because the operator edits them - tune.env,
# vpp-base.conf and hexarouting.conf. The boot self-test reports the first kind
# when it does not match and says nothing about the second, because an edited
# tune.env is the file working rather than the file broken, and a check that
# shouted about it is a check somebody switches off - taking the file that
# really IS from another release quiet with it (#302).
#
# That only holds while the guard and the class are the SAME statement. A
# fourth file guarded by hand with `[ -f ... ] ||` would be a file this
# installer will not overwrite and the manifest would call part of the build,
# so the first operator to edit it gets a failed self-test on every boot. So
# there is one guard in this file, it is inside keep(), and keep() is what
# records the class.
guards = [(n, t.strip()) for n, t in logical
          if not t.lstrip().startswith("#")
          and re.search(r"\[\s+-f\s.*\]\s*\|\|", t)]
if len(guards) == 1 and "command install" in guards[0][1]:
    ok(f"line {guards[0][0]} is the only one, and it is keep()'s")
elif not guards:
    bad("no `[ -f ... ] ||` guard is left in appliance/install.sh, so nothing "
        "there writes a file once and leaves it to the operator - or keep() "
        "has stopped guarding, and an upgrade now overwrites an edited "
        "tune.env")
else:
    for n, t in guards:
        if "command install" not in t:
            bad(f"appliance/install.sh line {n} guards a write by hand: {t!r}. "
                f"A file this installer will not overwrite has to go through "
                f"keep(), which is what puts it in the manifest as the "
                f"operator's - otherwise the first edit to it is reported as a "
                f"box running a build it is not")

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
