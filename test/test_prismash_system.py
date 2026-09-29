#!/usr/bin/env python3
"""
`system reboot` and `system shutdown`, driven through the seam the VTY calls
systemd through, asserting on what was PRINTED and on what systemd was ASKED.

WHY BOTH HALVES, AND NEVER THE EXIT STATUS ALONE (#388). The statement's
whole value is the sentence before the ask: an operator who types it has to be
told that every session ends and that the box comes back by itself, and told
BEFORE anything is asked for, because after it there is nobody at the prompt
to read it. The exit status of the process is the same whether that sentence
printed or not. And the ask has to be the plain verb - `systemctl reboot`, no
`--force` - because the argv is what decides whether vpp.service's ExecStop
runs on the way down; the drain that closes the retention log (#217) lives
there and nowhere else. So every box here reads the rows, and every box reads
the argv, and the recorder reads the rows AGAIN at the instant it is asked, so
"said before it asked" is measured and not inferred from the order of the
source. The cost sentences are in that instant's read too: the first version
of this file read them off the whole screen, and moving say_system_cost()
below the ask left every box green.

TWO OF THE COSTS ARE THIS BOX'S, AND THE BOXES ARRANGE THEM. `commit` does not
write to disk and a 'commit confirmed' is reverted by the box on its own, so a
reboot can cost the committed-but-unsaved configuration and can bring the box
back reverted. The product reads both the way `save` reads them - the running
configuration against the file at CONFIG_PATH, the window off the confirm
store - so the boxes give a Shell a running configuration, a file that agrees
or disagrees with it, and a store with or without a window, and read the
warning row at the instant of the ask. Each is also read ABSENT when the fact
is not so, because a warning printed on every reboot is a warning read on
none.

WHY THE SEAM. `Shell.sys` is a `System`, and `System(runner=...)` is the
injection point the product declares - the same one test_ssh_settings.py hands
a recorder. A suite that ran the real thing would reboot the machine running
`make test`, which is the one outcome nothing here may produce. The same seam
carries `sshd -T`, because reading the running configuration reads sshd
through it; the recorder answers that as a box without sshd and snapshots the
screen only at the systemctl asks.

THE REFUSALS ARE HERE TOO, and the reason is the reading in
test/test_prismash_refusals.py: every `self.err` site in the VTY has to have
been watched printing, and the floor is zero. A read-only account is refused
before systemd is asked - asserted on the argv list being EMPTY, not on the
refusal alone, because a refusal printed after the ask would be a box going
down under a sentence saying it may not.
"""
import contextlib
import io
import os
import sys
import tempfile
import time

_root = tempfile.mkdtemp(prefix="prismash-system-")
# The file `save` writes and the store 'commit confirmed' arms, both under
# this suite's own directory, so no box here reads /etc/prisma-routing on the
# machine running it - which on an appliance would be a real configuration.
os.environ["PRISMA_CONFIG"] = os.path.join(_root, "prisma-routing.conf")
os.environ["PRISMA_CONFIRM"] = os.path.join(_root, "confirm-pending")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "prismash"))

import config      # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nat_fixture import parse_nested      # noqa: E402
import prismash      # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")
    else:
        print(f"  [ ok ] {msg}")


class Systemd:
    """
    What the VTY asked through its System seam, and what the screen held at
    the instant systemd was asked.

    `answer` is what `systemctl` comes back with. `asked` is every argv, in
    order - `sshd -T` included, because the unsaved check reads the running
    configuration the way `save` does and System reads sshd through this
    seam; `systemctl` is the systemctl asks alone. `at_ask` is a copy of
    everything the VTY had printed when each systemctl ask arrived, which is
    what the said-before-asked boxes read.
    """

    def __init__(self, rc=0, says=""):
        self.rc, self.says = rc, says
        self.asked = []
        self.at_ask = []
        self.screen = None

    def run(self, argv, **kw):
        self.asked.append(list(argv))
        if argv[:1] != ["systemctl"]:
            # A box without sshd: read_ssh falls back to its defaults.
            return 127, f"{argv[0]}: not found"
        self.at_ask.append(self.screen.getvalue() if self.screen else "")
        return self.rc, self.says

    @property
    def systemctl(self):
        return [a for a in self.asked if a[:1] == ["systemctl"]]


#: A configuration a box would hold, and the same box after one more commit.
#: Through Config, so that both are the spelling `save` writes and `show
#: running-config` prints - a running text is what the product compares.
SAVED = config.Config.parse(
    "!\nuser alice\n password $1$x$abc\n role administrator\n!\n").text()
RUNNING = config.Config.parse(
    "!\nuser alice\n password $1$x$abc\n role administrator\n!\n"
    "user bob\n password $1$x$def\n role administrator\n!\n").text()
#: A fresh appliance, from install until its first `save`: the running
#: configuration read off it holds root with the installer's hash and the
#: sshd drop-in the installer writes, and nothing the dataplane or FRR is
#: asked for. Every one of those lives in /etc and comes back after a boot.
FRESH = config.Config.parse(
    "ssh permit-root-login yes\n"
    "user root\n password $6$x$installer\n exit\n").text()
#: The same box after one pool was committed: the one thing on it that the
#: boot's replay of an empty file does not put back.
FRESH_POOL = parse_nested(
    "ssh permit-root-login yes\n"
    "user root\n password $6$x$installer\n exit\n"
    "cgnat pool p\n inside 100.64.0.0/22\n outside 200.150.0.16/30\n"
    " exit\n").text()
#: What the installer ships at CONFIG_PATH: three comments and no statement.
SHIPPED = "!\n! Prisma Routing\n!\n"
#: The running configuration read OFF THE BOX, rather than a stub: one box
#: proves the measurement goes through the same seam as the ask.
REAL = object()
#: No file at CONFIG_PATH: a box nobody ever saved.
ABSENT = object()


def driven(caps, line, rc=0, says="", running=None, saved=None,
           pending=None, euid=None):
    """
    One statement, typed at the operational prompt.

    Through `feed` and not `do_system` directly, because the dispatch is half
    of #388: typed before this change, `system reboot` fell through to the
    FRR pass-through and was refused in vtysh's words. A box that called the
    method would be green with the statement unreachable from the prompt.

    `running` is what the box holds (SAVED unless said, or REAL to read it
    off this machine), `saved` what the file at CONFIG_PATH holds (the same
    unless said, or ABSENT), `pending` a deadline to arm a window at, and
    `euid` what os.geteuid answers while the statement runs.
    """
    sh = prismash.Shell()
    sh.caps = caps
    sd = Systemd(rc, says)
    sh.sys = prismash.System(runner=sd.run)
    text = SAVED if running is None else running
    if running is not REAL:
        sh.running_config = lambda: config.Config.parse(text)
    on_disk = SAVED if saved is None else saved
    if on_disk is ABSENT:
        if os.path.exists(prismash.CONFIG_PATH):
            os.remove(prismash.CONFIG_PATH)
    else:
        with open(prismash.CONFIG_PATH, "w") as f:
            f.write(on_disk)
    if os.path.exists(prismash.CONFIG_PATH):
        # Back to readable: the unreadable box in section 8 leaves it 0000.
        os.chmod(prismash.CONFIG_PATH, 0o600)
    sh.confirm = prismash.ConfirmPending(prismash.CONFIRM_PATH)
    if os.path.exists(prismash.CONFIRM_PATH):
        os.remove(prismash.CONFIRM_PATH)
    if pending is not None:
        sh.confirm.arm(pending, SAVED, 10)
    out = io.StringIO()
    sd.screen = out
    real_geteuid = prismash.os.geteuid
    if euid is not None:
        prismash.os.geteuid = lambda: euid
    try:
        with contextlib.redirect_stdout(out):
            rv = sh.feed(line)
    finally:
        prismash.os.geteuid = real_geteuid
    return rv, out.getvalue(), sd, sh


def rows(text):
    return text.splitlines()


def prose(text):
    """
    The screen with the console's folds taken back out.

    The cost rows go through print_prose, which wraps them at the console's
    width, so a sentence can arrive as two rows with the fold wherever the
    width put it. A needle for a SENTENCE is read here; a needle for a
    verdict or warning row is read from rows(), because such a row is one
    row by construction and its width is part of what is being asserted.
    """
    return " ".join(r.strip() for r in text.splitlines())


def widest(text):
    return max((len(r) for r in rows(text)), default=0)


def at_ask(sd):
    """
    The screen at the first systemctl ask, or nothing when there was none.

    Nothing rather than an IndexError: under a mutation that never asks, the
    box has to end in a FAIL row and a verdict, not a traceback.
    """
    return sd.at_ask[0] if sd.at_ask else ""


ADMIN = {prismash.CAP_ALL, prismash.CAP_CGNAT}
COST_SESSIONS = "every session on this box ends, including this one"
COST_DATAPLANE = "the dataplane stops; systemd runs its stop-drain first"
UNSAVED = "warning: the running configuration has NOT been saved"
UNCONFIRMED = "warning: the last commit has NOT been confirmed"

print("=== 1. system reboot, by an administrator, and systemd takes it ===")
rv, said, sd, sh = driven(ADMIN, "system reboot")
print(said.rstrip())
check(rows(said)[:1] == ["restarting the box"],
      f"the first row says what is happening: {rows(said)[:1]}")
check(COST_SESSIONS in prose(said),
      "and that every session on the box ends, this one included")
check(COST_DATAPLANE in prose(said),
      "and that the dataplane stops, drained first")
check("it comes back by itself, claiming its dataplane card at boot" in prose(said),
      "and that it comes back claiming its card with nothing typed (#378)")
check(sd.systemctl == [["systemctl", "reboot"]],
      f"systemd was asked for exactly 'systemctl reboot': {sd.systemctl}")
# The screen AT THE ASK, and every sentence in it: moving say_system_cost()
# below the ask leaves the whole-screen reads above green and turns these
# red, which is the difference between "printed" and "printed first".
check("restarting the box" in at_ask(sd)
      and "claiming its dataplane card" in prose(at_ask(sd)),
      "the row and the return were on the screen BEFORE it was asked")
check(COST_SESSIONS in prose(at_ask(sd)),
      "and so was the cost every session pays")
check(COST_DATAPLANE in prose(at_ask(sd)),
      "and so was the dataplane stopping")
check(sh.failed == 0 and rv is True,
      f"nothing was refused (failed={sh.failed}) and the session is left to "
      f"systemd to end (rv={rv})")
check("error:" not in said, "and no verdict was printed over a reboot that "
                            "systemd accepted")
check(not [r for r in rows(said) if r.startswith("warning:")],
      "and a box that is saved and confirmed is warned of nothing")

print()
print("=== 2. system shutdown: the other verb, the other unit, its own cost ===")
rv, said, sd, sh = driven(ADMIN, "system shutdown")
print(said.rstrip())
check(rows(said)[:1] == ["shutting the box down"],
      f"the first row says what is happening: {rows(said)[:1]}")
check(COST_SESSIONS in prose(said),
      "the cost every session pays is said for this verb too")
check("stays off until something powers it on again" in prose(said),
      "and what makes it different from a reboot: it does not come back")
check("comes back claiming its card by itself" in prose(said),
      "and what happens when somebody does power it on (#378)")
check(sd.systemctl == [["systemctl", "poweroff"]],
      f"systemd was asked for exactly 'systemctl poweroff': {sd.systemctl}")
check("stays off until" in prose(at_ask(sd)),
      "said before it was asked")
check(COST_SESSIONS in prose(at_ask(sd))
      and COST_DATAPLANE in prose(at_ask(sd)),
      "and both costs were on the screen at that instant too")
check(sh.failed == 0, f"nothing was refused: failed={sh.failed}")

print()
print("=== 3. the argv is the plain verb, so the ExecStop drain is on its "
      "path ===")
# `systemctl reboot --force` skips every unit's ExecStop, and `-ff` skips
# systemd itself. The drain that closes the retention log is vpp.service's
# ExecStop (appliance/systemd/vpp-stop-drain.conf), so the argv is the whole
# of whether #217's promise survives a reboot typed here. do_system() carries
# the systemd measurement.
for verb, unit in (("reboot", "reboot"), ("shutdown", "poweroff")):
    _, _, sd, _ = driven(ADMIN, f"system {verb}")
    check(len(sd.systemctl) == 1 and sd.systemctl[0] == ["systemctl", unit],
          f"'system {verb}' asks ['systemctl', '{unit}'] and adds no flag: "
          f"{sd.systemctl}")

print()
print("=== 4. systemd refuses, and the refusal is a verdict row with "
      "systemd's rows under it ===")
# The three-line refusal the reboot verb really prints to a non-root caller
# on a box with polkit and no rule for the account: logind's row, the
# target's row, and the pointer to the logs. It has to reach the operator
# whole, because the alternative is somebody who believes the box is going
# down and walks away from one that is not - and it has to reach them inside
# the console. Joined onto the verdict row this measured 145 columns with a
# two-line example and would pass 200 with these three; the verdict is one
# row and systemd's words are wrapped under it (#355's split).
THREE_LINES = (
    "Failed to reboot system via logind: Interactive authentication "
    "required.\n"
    "Failed to start reboot.target: Interactive authentication required.\n"
    "See system logs and 'systemctl status reboot.target' for details.\n")
rv, said, sd, sh = driven(ADMIN, "system reboot", rc=1, says=THREE_LINES)
print(said.rstrip())
check("error: the box could not be restarted" in rows(said),
      "the verdict is one row of its own, naming what did not happen")
for line in THREE_LINES.strip().splitlines():
    check(line in prose(said), f"and systemd's own row is under it: {line!r}")
check(widest(said) <= config.CONSOLE_COLUMNS,
      f"every row fits the console: the widest is {widest(said)} of "
      f"{config.CONSOLE_COLUMNS} columns")
check(not [r for r in rows(said)
           if r.startswith("error:") and "Failed to" in r],
      "and no row carries the verdict and systemd's words together")
check(rows(said)[:1] == ["restarting the box"],
      "and the rows before the ask were still printed: the operator can see "
      "what was attempted")
check(sh.failed == 1, f"counted as one refusal: failed={sh.failed}")
check(sd.systemctl == [["systemctl", "reboot"]],
      f"and it was asked once, not retried: {sd.systemctl}")

rv, said, sd, sh = driven(ADMIN, "system shutdown", rc=1,
                          says="Failed to power off: unit not found")
check("error: the box could not be shut down" in rows(said)
      and "Failed to power off: unit not found" in prose(said),
      f"the shutdown refusal has its own verb: {rows(said)[-2:]}")

print()
print("=== 5. no systemctl at all is a refusal, not 'written and not run' ===")
# rc 127 is what System._subprocess answers when the binary is not there,
# which is every developer box. apply_dataplane_if treats that as "stored,
# the tuner is not on this machine" because something WAS written; a reboot
# that did not happen has no half that did.
rv, said, sd, sh = driven(ADMIN, "system reboot", rc=127,
                          says="systemctl: not found")
check("error: the box could not be restarted" in rows(said)
      and "systemctl: not found" in prose(said),
      f"named as a refusal: {rows(said)[-2:]}")
check(sh.failed == 1, f"and counted: failed={sh.failed}")

rv, said, sd, sh = driven(ADMIN, "system reboot", rc=1, says="")
check("error: the box could not be restarted" in rows(said)
      and "systemctl exited 1 and said nothing" in prose(said),
      f"a silent failure is still a refusal that ends in words: "
      f"{rows(said)[-2:]}")

print()
print("=== 6. a read-only account is refused BEFORE systemd is asked ===")
rv, said, sd, sh = driven(set(), "system reboot")
print(said.rstrip())
check("error: system reboot: permission denied for a read-only account"
      in rows(said),
      "the same rendered refusal every privileged statement gives")
check(any(prismash.GROUP_ADMIN in r for r in rows(said)),
      "naming the membership that would allow it")
# The WHOLE list, not the systemctl half: the refusal is before the cost is
# measured, so not even the running configuration was read.
check(sd.asked == [], f"and nothing was asked through the seam: {sd.asked}")
check("restarting the box" not in said,
      "and the box was not told it is restarting")
check(rv is True, "the session goes on")

rv, said, sd, sh = driven({prismash.CAP_CGNAT}, "system shutdown")
check("error: system shutdown: permission denied for a "
      f"{prismash.ROLE_CGNAT} account" in rows(said),
      f"a CGNAT operator is refused by the name of their role: "
      f"{rows(said)[:1]}")
check(sd.asked == [], f"and nothing was asked through the seam: {sd.asked}")

print()
print("=== 7. the menu with no verb, or a verb it has not got ===")
for line in ("system", "system halt", "system reboot now"):
    rv, said, sd, sh = driven(ADMIN, line)
    check("error: usage: system reboot | shutdown | upgrade | dataplane restart" in rows(said),
          f"{line!r} is answered with the usage: {rows(said)[:1]}")
    check(sd.asked == [], f"and systemd was not asked: {sd.asked}")

print()
print("=== 8. what is committed and not saved is lost at boot, and the box "
      "says so first ===")
# The running configuration is RUNNING, the file holds SAVED - one commit
# behind - which is a box after `commit` and before `save`. The warning has
# to be on the screen at the ask, like every other cost, and it has to name
# the word that keeps the change.
rv, said, sd, sh = driven(ADMIN, "system reboot", running=RUNNING,
                          saved=SAVED)
print(said.rstrip())
check(UNSAVED in rows(said),
      f"the warning row, in the shape `save` uses: {rows(said)}")
check("'save'" in prose(said) and "lost" in prose(said),
      "and the prose under it says what is lost and that 'save' keeps it")
check(UNSAVED in rows(at_ask(sd)),
      "and it was on the screen BEFORE systemd was asked")
check(sd.systemctl == [["systemctl", "reboot"]] and sh.failed == 0,
      f"a warning, not a refusal: the reboot was still asked for "
      f"({sd.systemctl}) and nothing counted as failed ({sh.failed})")

rv, said, sd, sh = driven(ADMIN, "system reboot", running=SAVED, saved=SAVED)
check(UNSAVED not in rows(said),
      "a box whose file holds what is running is not warned")

# WHAT AN EMPTY FILE LOSES, AND WHAT IT DOES NOT. `prismash --load` applies
# nothing from a file with no statements (the installer's three comments,
# or no file at all), so the boot of such a box keeps every account in
# /etc/passwd, the sshd drop-in and snmpd, and loses only what the
# dataplane and FRR held. A fresh appliance, from install until its first
# `save`, is exactly that box: root with a hash and `permit-root-login yes`
# are in its running configuration and in no file, and nothing of it is
# lost. It was warned that "what was committed and not saved is lost", on
# the box lab's own reboot, and that sentence was false of it.
for on_disk, what in ((SHIPPED, "the installer's three comments"),
                      (ABSENT, "no file at all")):
    rv, said, sd, sh = driven(ADMIN, "system reboot", running=FRESH,
                              saved=on_disk)
    check(UNSAVED not in rows(said),
          f"a fresh appliance with {what} is not warned: root and the sshd "
          f"drop-in come back after the boot")
    check("lost" not in prose(said),
          f"and is told nothing is lost, with {what}")
    rv, said, sd, sh = driven(ADMIN, "system reboot", running=FRESH_POOL,
                              saved=on_disk)
    check(UNSAVED in rows(said),
          f"the same box with one pool committed and {what} is warned: "
          f"the boot does not put the pool back")
    check("'save'" in prose(said) and "lost" in prose(said),
          f"in the same shape, naming 'save', with {what}")

rv, said, sd, sh = driven(ADMIN, "system reboot",
                          running=config.Config().text(), saved=ABSENT)
check(UNSAVED not in rows(said),
      "and a box with no file and nothing configured is not: nothing is lost")

# The spelling is not the difference. The file as an operator may have left
# it - a comment, a blank line - against the running text `save` would
# write, so a file that is up to date is not called unsaved over a comment.
rv, said, sd, sh = driven(ADMIN, "system reboot", running=SAVED,
                          saved="! saved by hand\n\n" + SAVED)
check(UNSAVED not in rows(said),
      "a file that differs from the running text only in spelling is saved")

# The running configuration read off the box, through the product's own
# path: sshd is read through the same seam systemd is asked through, and it
# is read BEFORE the ask, which is the order the warning depends on.
rv, said, sd, sh = driven(ADMIN, "system reboot", running=REAL, saved=ABSENT)
check(sd.asked[:1] == [["sshd", "-T"]] and sd.asked[-1:] == [["systemctl", "reboot"]],
      f"the running configuration is read the way `save` reads it, through "
      f"the seam, before the ask: {sd.asked}")

if os.geteuid() == 0:
    print("  [skip] running as root, which reads a file no mode can deny it; "
          "the unreadable case is not decided here")
else:
    with open(prismash.CONFIG_PATH, "w") as f:
        f.write(SAVED)
    os.chmod(prismash.CONFIG_PATH, 0o000)
    try:
        sh = prismash.Shell()
        sh.caps = ADMIN
        sh.running_config = lambda: config.Config.parse(SAVED)
        sh.confirm = prismash.ConfirmPending(prismash.CONFIRM_PATH)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sh.say_unsaved()
        said = out.getvalue()
    finally:
        os.chmod(prismash.CONFIG_PATH, 0o600)
    check("warning: whether the running configuration is saved could not be "
          "checked" in rows(said) and prismash.CONFIG_PATH in said,
          f"a file this account cannot read is said as unchecked, never as "
          f"saved: {rows(said)}")
    check(widest(said) <= config.CONSOLE_COLUMNS,
          f"inside the console: {widest(said)} columns at the widest")

print()
print("=== 9. a 'commit confirmed' nobody confirmed comes back reverted, "
      "and the box says so first ===")
deadline = int(time.time()) + 600
rv, said, sd, sh = driven(ADMIN, "system reboot", pending=deadline)
print(said.rstrip())
when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(deadline))
check(UNCONFIRMED in rows(said),
      f"the warning row, in the shape `save` uses inside a window: "
      f"{rows(said)}")
check(when in prose(said) and "'commit'" in prose(said),
      f"and the prose names the deadline ({when}) and the word that keeps "
      f"the change")
check("restarted or not" in prose(said),
      "and says the restart does not cancel the window")
check(UNCONFIRMED in rows(at_ask(sd)),
      "and it was on the screen BEFORE systemd was asked")
check(sd.systemctl == [["systemctl", "reboot"]] and sh.failed == 0,
      f"a warning, not a refusal: still asked for ({sd.systemctl}), nothing "
      f"failed ({sh.failed})")
check(widest(said) <= config.CONSOLE_COLUMNS,
      f"every row fits the console: the widest is {widest(said)} of "
      f"{config.CONSOLE_COLUMNS} columns")

rv, said, sd, sh = driven(ADMIN, "system shutdown", pending=deadline)
check(UNCONFIRMED in rows(at_ask(sd)),
      "a shutdown says it too, before the ask")

rv, said, sd, sh = driven(ADMIN, "system reboot")
check(UNCONFIRMED not in rows(said),
      "and a box with nothing pending is not warned of a window it is not in")

# Both at once: one commit unsaved AND unconfirmed is two rows, each true.
rv, said, sd, sh = driven(ADMIN, "system reboot", running=RUNNING,
                          saved=SAVED, pending=deadline)
check(UNSAVED in rows(at_ask(sd)) and UNCONFIRMED in rows(at_ask(sd)),
      "unsaved and unconfirmed are two warnings, both before the ask")

print()
print("=== 10. no privilege path: the argv is the same for every uid, and a "
      "non-root administrator gets systemd's refusal ===")
# The appliance installs neither sudo nor polkit (do_system carries the
# measurement over appliance/build-iso.sh's package lists), so there is no
# `sudo -n systemctl` and no pkexec for a uid that is not 0: the argv is the
# plain verb whoever typed it, and systemd decides. Pinned so that a
# privilege path added later without the package it needs is a red row here
# and not a refusal on a box.
argvs = {}
for uid in (0, 1000):
    _, _, sd, _ = driven(ADMIN, "system reboot", euid=uid)
    argvs[uid] = sd.systemctl
check(argvs[0] == [["systemctl", "reboot"]],
      f"root asks ['systemctl', 'reboot']: {argvs[0]}")
check(argvs[1000] == [["systemctl", "reboot"]],
      f"and uid 1000 asks exactly the same, with nothing in front of it: "
      f"{argvs[1000]}")
# What systemd 252 says to that uid on a box with dbus and no polkit,
# measured with `systemctl start <unit>` as uid 1000 on this systemd
# (exit 4); the reboot verb adds logind's row in front. Rendered as the
# same paragraph as box 4.
DENIED = ("Failed to reboot system via logind: Access denied\n"
          "Failed to start reboot.target: Access denied\n"
          "See system logs and 'systemctl status reboot.target' for "
          "details.\n")
rv, said, sd, sh = driven(ADMIN, "system reboot", rc=4, says=DENIED,
                          euid=1000)
print(said.rstrip())
check("error: the box could not be restarted" in rows(said)
      and "Failed to reboot system via logind: Access denied" in prose(said),
      "the refusal is rendered as the verdict and systemd's words under it")
check(COST_SESSIONS in prose(at_ask(sd)),
      "after the cost prose, which that account reads too")
check(sh.failed == 1 and widest(said) <= config.CONSOLE_COLUMNS,
      f"one refusal ({sh.failed}), inside the console ({widest(said)} "
      f"columns at the widest)")

print()
print("=== 11. the units of system.py, each called on its own (#390) ===")
# Every function below is called directly with the input it exists for,
# over a root of this suite's own and a runner that records, so nothing
# here reads /etc or runs a command on the machine running it. Each box
# was watched going red against a mutation of the rule it reads.
import stat      # noqa: E402
import system    # noqa: E402


class Box:
    """
    A System over a root of this suite's own, whose commands are recorded
    and answered from a table keyed by the argv's prefix, 0 and "" unless
    the table says otherwise.
    """

    def __init__(self, answers=None):
        self.root = tempfile.mkdtemp(prefix="unit-", dir=_root)
        self.asked = []
        self.answers = answers or {}
        self.sys = system.System(runner=self.run, root=self.root)

    def run(self, argv, stdin=None, timeout=30):
        self.asked.append(list(argv))
        for prefix, answer in self.answers.items():
            if tuple(argv[:len(prefix)]) == prefix:
                return answer
        return 0, ""

    def write(self, rel, text):
        path = self.root + rel
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def read(self, rel):
        try:
            with open(self.root + rel) as f:
                return f.read()
        except OSError:
            return None

    def mode(self, rel):
        return stat.S_IMODE(os.stat(self.root + rel).st_mode)


def account(name, keys=(), password="", role=""):
    u = system.User(name)
    u.keys = list(keys)
    u.password = password
    u.role = role
    return u


print("--- role_from_groups: the role a set of memberships amounts to")
check(system.role_from_groups({system.GROUP_ADMIN, system.GROUP_CGNAT,
                               "vpp"}) == system.ROLE_ADMIN,
      "both role groups is an administrator: prisma-admin sits on top of "
      "prisma-cgnat")
check(system.role_from_groups({system.GROUP_CGNAT, "vpp", "frr"})
      == system.ROLE_CGNAT, "prisma-cgnat alone is a cgnat operator")
check(system.role_from_groups({"vpp", "frr", "frrvty", "sudo"}) == "",
      "the reach groups and sudo are not a role: read-only")
check(system.role_from_groups(set()) == "", "no group at all is read-only")

print("--- can_anyone_log_in: who still reaches the box over ssh")
users = {"root": account("root", password="$6$r"),
         "alice": account("alice", password="$6$a"),
         "bob": account("bob", keys=["ssh-ed25519 B"]),
         "carol": account("carol")}
check(sorted(system.can_anyone_log_in({}, users)) == ["alice", "bob"],
      "under the defaults a password or a key logs in, and root's password "
      "does not under prohibit-password")
check(system.can_anyone_log_in({"permit-root-login": "yes"}, users)
      == ["root", "alice", "bob"],
      "permit-root-login yes lets root's password in")
check(system.can_anyone_log_in({"password-authentication": "no",
                                "permit-root-login": "yes"}, users)
      == ["bob"], "password-authentication no leaves only the key")
users["root"] = account("root", keys=["ssh-ed25519 R"])
check("root" in system.can_anyone_log_in({}, users),
      "root with a key logs in under prohibit-password")
check("root" not in system.can_anyone_log_in({"permit-root-login": "no"},
                                             users),
      "and not under permit-root-login no, key or not")
check(system.can_anyone_log_in({}, {"carol": account("carol")}) == [],
      "an account with nothing to log in with is a lockout: an empty list")

print("--- can_anyone_configure: who reaches it AND may change it")
users = {"root": account("root", keys=["R"]),
         "alice": account("alice", keys=["A"], role=system.ROLE_ADMIN),
         "bob": account("bob", keys=["B"], role=system.ROLE_CGNAT),
         "carol": account("carol", keys=["C"]),
         "dave": account("dave", password="$6$d", role=system.ROLE_ADMIN)}
check(sorted(system.can_anyone_configure({"permit-root-login": "no"}, users))
      == ["alice", "dave"], "the administrators who can log in, not the "
      "cgnat operator, not the read-only account")
check(sorted(system.can_anyone_configure({}, users))
      == ["alice", "dave", "root"],
      "root counts whenever root can log in: root is every capability")
check(system.can_anyone_configure({"permit-root-login": "no",
                                   "password-authentication": "no"}, users)
      == ["alice"], "an administrator who cannot log in cannot configure")

print("--- validate_snmp: the settings refused before anything is written")
check(system.validate_snmp({"enabled": "yes", "listen-address": "0.0.0.0",
                            "port": "161", "enterprise-number": "70000"})
      is None, "a PEN above 65535 is accepted: IANA assigns them (#351)")
check(system.validate_snmp({"enterprise-number": "4294967295"}) is None,
      "and so is the top of the arc")
why = system.validate_snmp({"enterprise-number": "4294967296"})
check(why == "snmp enterprise-number '4294967296': expected 1-4294967295",
      f"one past it is refused with the range: {why!r}")
check(system.validate_snmp({"enterprise-number": "0"}) is not None,
      "0 is not a PEN")
check(system.validate_snmp({"enterprise-number": ""}) is None,
      "and no PEN at all is allowed: the enterprise objects stay dark")
for port in ("0", "65536", "abc"):
    check(system.validate_snmp({"port": port})
          == f"snmp port '{port}': expected 1-65535",
          f"port {port!r} is refused with the range")
check(system.validate_snmp({"enabled": "maybe"})
      == "snmp enabled 'maybe': expected yes or no", "enabled is yes or no")
check(system.validate_snmp({"community": "public"})
      == "'community' is not an snmp setting", "an unknown key is named")

print("--- snmp_redact: communities and passphrases are passwords on the wire")
shown = system.snmp_redact([
    "snmp enabled yes", "snmp community s3cret", "snmp-user bob",
    " auth sha pa55", " priv aes cr7pt", " exit", " snmp community indented",
    " auth sha", "snmp listen-address 0.0.0.0"])
check(shown == [
    "snmp enabled yes", "snmp community <set, not shown>", "snmp-user bob",
    " auth sha <set, not shown>", " priv aes <set, not shown>", " exit",
    " snmp community <set, not shown>", " auth sha",
    "snmp listen-address 0.0.0.0"],
    f"the secret is replaced, the protocol and everything else kept: {shown}")
check(not any(s in " ".join(shown) for s in ("s3cret", "pa55", "cr7pt",
                                             "indented")),
      "and no secret survives on the screen")

print("--- identity_redact: the RADIUS secret is a password on the wire")
shown = system.identity_redact([
    "cgnat identity radius secret s3cr3t port 1813",
    "cgnat identity radius secret onlythesecret",
    "cgnat identity file /etc/prisma-routing/subscribers.map",
    "cgnat pool residential"])
check(shown == [
    "cgnat identity radius secret <set, not shown> port 1813",
    "cgnat identity radius secret <set, not shown>",
    "cgnat identity file /etc/prisma-routing/subscribers.map",
    "cgnat pool residential"],
    f"the secret goes and the port stays, because a port is not a secret: "
    f"{shown}")
# And as the configuration is written since #446: indented under `cgnat`,
# with no `cgnat` on the line. Red with the match left on the model's form.
shown_nested = system.identity_redact([
    "  identity radius secret s3cr3t port 1813",
    "  identity file /etc/prisma-routing/subscribers.map"])
check(shown_nested == ["  identity radius secret <set, not shown> port 1813",
                       "  identity file /etc/prisma-routing/subscribers.map"],
      f"the secret goes in the block too, and the indent stays: "
      f"{shown_nested}")
check(not any(x in " ".join(shown) for x in ("s3cr3t", "onlythesecret")),
      "and no secret survives on the screen")
# THE FILE KEEPS THE REAL VALUE. This is a display transform, and a box that
# redacted its own saved configuration could not start its listener after a
# reboot (#21).
check(system.identity_redact(["cgnat identity radius secret keepme"])[0]
      != "cgnat identity radius secret keepme"
      and "keepme" not in
      system.identity_redact(["cgnat identity radius secret keepme"])[0],
      "the transform is on the way to the screen and nowhere else")

print("--- _snmpd_body: the snmpd.conf this VTY owns, generated whole")
body = system._snmpd_body(
    {"listen-address": "10.0.0.1", "port": "1161",
     "enterprise-number": "70000"},
    ["public", "ops"], {"zed": system.SnmpUser("zed"),
                        "amy": system.SnmpUser("amy")}, agent="/x/agent",
    release="0.141.2")
check(body.splitlines()[1:] == [
    "agentAddress udp:10.0.0.1:1161",
    "view hexa included .1.3.6.1.2.1.1",
    "view hexa included .1.3.6.1.2.1.25",
    "view hexa included .1.3.6.1.4.1.2021",
    "view hexa included .1.3.6.1.2.1.123",
    "view hexa included .1.3.6.1.4.1.70000",
    "sysdescr Prisma Routing 0.141.2",
    "disk / 10%",
    "disk /var/log/prisma-routing 15%",
    "rocommunity public default -V hexa",
    "rocommunity ops default -V hexa",
    "rouser amy priv -V hexa",
    "rouser zed priv -V hexa",
    "pass_persist .1.3.6.1.2.1.123 /x/agent",
    "pass_persist .1.3.6.1.4.1.70000 /x/agent"],
    f"address, both views, every community, users sorted, both agents: "
    f"{body.splitlines()}")
check(body.endswith("\n") and "rw" not in body,
      "ends in a newline and grants nothing writable")
body = system._snmpd_body({}, ["public"], {}, release="")
check("agentAddress udp:127.0.0.1:161" in body
      and "pass_persist .1.3.6.1.4.1." not in body
      and [l for l in body.splitlines() if ".1.3.6.1.4.1." in l]
      == ["view hexa included .1.3.6.1.4.1.2021"]
      and "sysdescr Prisma Routing\n" in body
      and f"pass_persist .1.3.6.1.2.1.123 {system.SNMP_AGENT}" in body,
      f"defaults: loopback 161, no operator subtree, UCD's own, a sysDescr "
      f"with no release it cannot read, the shipped agent: {body.splitlines()}")
# THE VIEW NOW CARRIES AN ENTERPRISE OID THAT IS NOT THE OPERATOR'S (#497):
# UCD-SNMP-MIB's 2021. read() took the operator's number from the first view
# line under .1.3.6.1.4.1, which would now answer 2021; it reads the agent's
# registration instead.
import tempfile as _tf
with _tf.TemporaryDirectory() as _d:
    _conf = os.path.join(_d, "snmpd.conf")
    with open(_conf, "w") as _f:
        _f.write(system._snmpd_body({"enterprise-number": "70000"}, ["p"], {},
                                    release="x"))
    _sn = system.SnmpDaemon(runner=lambda a, stdin=None, timeout=30: (1, ""),
                            root="", conf=_conf)
    _settings = _sn.read()[0]
    check(_settings.get("enterprise-number") == "70000",
          f"read back the operator's number, not UCD's: {_settings}")
    with open(_conf, "w") as _f:
        _f.write(system._snmpd_body({}, ["p"], {}, release="x"))
    _settings = system.SnmpDaemon(runner=lambda a, stdin=None, timeout=30: (1, ""),
                                  root="", conf=_conf).read()[0]
    check(_settings.get("enterprise-number", "") == "",
          f"and no number where the operator set none: {_settings}")
print("  the view answers the box's identity, CPU, memory and disks")
print("--- _release: the release sysDescr names")
_old = system.SNMP_VERSION_FILE
with _tf.TemporaryDirectory() as _d:
    system.SNMP_VERSION_FILE = os.path.join(_d, "VERSION")
    check(system._release() == "", "no VERSION file is no release, not a crash")
    with open(system.SNMP_VERSION_FILE, "w") as _f:
        _f.write("0.141.2\n")
    check(system._release() == "0.141.2", "the release read from VERSION")
system.SNMP_VERSION_FILE = _old

print("--- User.__eq__: two accounts are equal by what they would write")
a = account("alice", keys=["k1"], password="$6$h", role=system.ROLE_ADMIN)
b = account("alice", keys=["k1"], password="$6$h", role=system.ROLE_ADMIN)
check(a.__eq__(b) is True and a == b, "same name, keys, hash and role")
check(account("alice", keys=["k2"], password="$6$h",
              role=system.ROLE_ADMIN).__eq__(a) is False,
      "a different key is a different account")
check(account("alice", keys=["k1"], password="$6$h").__eq__(a) is False,
      "and so is a different role")
check(a.__eq__("alice") is False, "a string is not an account")

print("--- System._subprocess: status and both streams, 127 and 124")
rc, out = system.System._subprocess(
    [sys.executable, "-c",
     "import sys; sys.stdout.write(sys.stdin.read().upper()); "
     "sys.stderr.write(' and stderr'); sys.exit(5)"],
    stdin="alice:hash\n")
check(rc == 5 and out == "ALICE:HASH\n and stderr",
      f"stdin reaches the process, stdout and stderr come back as one text "
      f"with the status: {(rc, out)}")
rc, out = system.System._subprocess(["/nonexistent/prisma-390-useradd", "x"])
check(rc == 127 and out == "/nonexistent/prisma-390-useradd: not found",
      f"a binary that is not there is 127: {(rc, out)}")
rc, out = system.System._subprocess(
    [sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
check(rc == 124 and "timed out" in out, f"a hang is 124: {(rc, out)}")

print("--- System._read_groups: member lists and primary groups, apart")
box = Box()
box.write("/etc/group", "root:x:0:\nprisma-admin:x:1001:alice,bob\n"
                        "prisma-cgnat:x:1002:\nstaff:x:50:alice\nbroken\n")
members, gid_group = box.sys._read_groups()
check(members == {"alice": {"prisma-admin", "staff"}, "bob": {"prisma-admin"}},
      f"name to the groups listing it, empty lists adding nobody: {members}")
check(gid_group == {"0": "root", "1001": "prisma-admin", "1002": "prisma-cgnat",
                    "50": "staff"},
      f"gid to name, the broken line skipped: {gid_group}")
check(Box().sys._read_groups() == ({}, {}),
      "no /etc/group is nobody in nothing, not an exception")

print("--- System._read_keys: authorized_keys, comments and blanks out")
box = Box()
box.write("/home/alice/.ssh/authorized_keys",
          "# a comment\n\nssh-ed25519 AAAA one\n  ssh-rsa BBBB two  \n")
keys = box.sys._read_keys("/home/alice")
check(keys == ["ssh-ed25519 AAAA one", "ssh-rsa BBBB two"],
      f"the keys, stripped, and nothing else: {keys}")
check(box.sys._read_keys("/home/nobody") == [], "no file is no keys")

print("--- System.write_ssh: written, validated, put back on refusal")
WANT = {"port": "2222", "listen-address": "10.0.0.1",
        "permit-root-login": "no", "password-authentication": "no"}
box = Box()
rv = box.sys.write_ssh(WANT)
check(rv == (True, ""), f"valid and reloaded: {rv}")
first = box.read(system.SSHD_DROPIN)
check(first == "# Generated by prismash. Edit through the VTY, not here.\n"
               "Port 2222\nListenAddress 10.0.0.1\nPermitRootLogin no\n"
               "PasswordAuthentication no\n",
      f"the drop-in holds the four settings: {first!r}")
check(box.asked == [["sshd", "-t"], ["systemctl", "reload", "ssh"]],
      f"sshd was asked to check it, then to reload: {box.asked}")
box.answers[("sshd", "-t")] = (255, "bad directive line 3")
del box.asked[:]
rv = box.sys.write_ssh(dict(WANT, port="2223"))
check(rv[0] is False and "nothing was applied" in rv[1]
      and "bad directive" in rv[1],
      f"sshd -t refusing it is a refusal that quotes sshd: {rv}")
check(box.read(system.SSHD_DROPIN) == first,
      "and the previous drop-in is back, byte for byte")
check(["systemctl", "reload", "ssh"] not in box.asked,
      f"and nothing was reloaded: {box.asked}")
fresh = Box({("sshd", "-t"): (255, "bad")})
fresh.sys.write_ssh(WANT)
check(fresh.read(system.SSHD_DROPIN) is None,
      "a box that had no drop-in has none after a refusal")
box = Box({("sshd", "-t"): (127, "sshd: not found")})
rv = box.sys.write_ssh(WANT)
check(rv == (True, "sshd is not installed: written but not validated")
      and box.read(system.SSHD_DROPIN) == first
      and ["systemctl", "reload", "ssh"] not in box.asked,
      f"no sshd: written, said so, no reload: {rv} {box.asked}")
box = Box({("systemctl", "reload"): (1, "Job for ssh.service failed")})
rv = box.sys.write_ssh(WANT)
check(rv[0] is True and "did not reload" in rv[1] and "ssh.service" in rv[1],
      f"a reload that failed is written and valid, and says so: {rv}")
box = Box()
rv = box.sys.write_ssh({"port": "99999"})
check(rv == (False, "ssh port '99999': expected 1-65535")
      and box.asked == [] and box.read(system.SSHD_DROPIN) is None,
      f"an invalid setting: refused before anything is written or asked: "
      f"{rv} {box.asked}")

print("--- System._apply_groups: the memberships a role implies, both ways")
GROUPS = ("prisma-admin:x:1001:alice\nprisma-cgnat:x:1002:alice\nvpp:x:1003:\n"
          "frr:x:1004:alice\nfrrvty:x:1005:alice\nsudo:x:27:alice\n")
box = Box()
box.write("/etc/group", GROUPS)
rv = box.sys._apply_groups("alice", system.ROLE_CGNAT)
check(rv[:2] == (True, ""), f"a demotion to cgnat operator: {rv}")
check(sorted(box.asked) == [["gpasswd", "-a", "alice", "vpp"],
                            ["gpasswd", "-d", "alice", "prisma-admin"]],
      f"leaves prisma-admin, joins the reach group it lacked, and touches "
      f"neither sudo nor what it already holds: {box.asked}")
check(sorted(rv[2]) == ["alice: added to group vpp",
                        "alice: removed from group prisma-admin"],
      f"and says both: {rv[2]}")
box = Box()
box.write("/etc/group", GROUPS)
rv = box.sys._apply_groups("alice", "")
check(sorted(a[1:] for a in box.asked)
      == [["-a", "alice", "vpp"], ["-d", "alice", "prisma-admin"],
          ["-d", "alice", "prisma-cgnat"]],
      f"read-only: both role groups left, the reach groups kept: {box.asked}")
box = Box()
box.write("/etc/group", GROUPS)
rv = box.sys._apply_groups("bob", system.ROLE_ADMIN)
check(box.asked == [["gpasswd", "-a", "bob", g]
                    for g in system.MANAGED_GROUPS] and rv[0] is True,
      f"a new administrator joins every managed group, in order: "
      f"{box.asked}")
box = Box({("gpasswd", "-a", "bob", "prisma-admin"):
           (1, "gpasswd: group 'prisma-admin' does not exist")})
box.write("/etc/group", GROUPS)
rv = box.sys._apply_groups("bob", system.ROLE_ADMIN)
check(rv[0] is False and "cannot join group prisma-admin" in rv[1]
      and "does not exist" in rv[1],
      f"a role group that cannot be joined fails the change: {rv}")
box = Box({("gpasswd", "-a", "bob", "frrvty"):
           (1, "gpasswd: group 'frrvty' does not exist")})
box.write("/etc/group", GROUPS)
rv = box.sys._apply_groups("bob", system.ROLE_CGNAT)
check(rv[0] is True and any("frrvty" in m and "cannot use" in m
                            for m in rv[2]),
      f"a reach group that cannot be joined is said and does not stop the "
      f"account: {rv}")

print("--- System._write_keys: the file, its mode, and the home it goes to")
box = Box()
rv = box.sys._write_keys("alice", ["ssh-ed25519 A one", "ssh-rsa B two"])
check(rv == (True, "") and box.read("/home/alice/.ssh/authorized_keys")
      == "ssh-ed25519 A one\nssh-rsa B two\n",
      f"one key per line under the account's home: {rv}")
check(box.mode("/home/alice/.ssh/authorized_keys") == 0o600
      and box.mode("/home/alice/.ssh") == 0o700,
      "0600 on the file, 0700 on the directory, or sshd ignores them")
box.sys._write_keys("root", ["ssh-ed25519 R"])
check(box.read("/root/.ssh/authorized_keys") == "ssh-ed25519 R\n",
      "root's home is /root, not /home/root")
box.sys._write_keys("alice", [])
check(box.read("/home/alice/.ssh/authorized_keys") == "",
      "no keys writes an empty file: a removal, not a leftover")
blocked = os.path.join(box.root, "not-a-dir")
with open(blocked, "w") as f:
    f.write("")
rv = system.System(runner=box.run, root=blocked)._write_keys("alice", ["k"])
check(rv[0] is False and rv[1].startswith("authorized_keys for alice: "),
      f"a home that cannot be made is a refusal naming the account: {rv}")

print("--- SnmpUser.__init__ and __eq__: a v3 user, equal by its lines")
u = system.SnmpUser("bob")
check(u.name == "bob" and u.auth == "" and u.priv == ""
      and u.lines() == ["snmp-user bob", " exit"],
      f"a new user has a name and no protocols: {u.lines()}")
v = system.SnmpUser("bob")
check(u.__eq__(v) is True and u == v, "two bare users of one name are equal")
v.priv = "aes cr7pt"
check(u.__eq__(v) is False, "a priv passphrase makes them differ")
check(u.__eq__(system.SnmpUser("bab")) is False, "and so does the name")
check(u.__eq__("bob") is False, "a string is not a user")

print("--- SnmpDaemon.__init__: the conf under the root, the runner injected")
box = Box()
d = system.SnmpDaemon(runner=box.run, root=box.root)
check(d.root == box.root and d.conf == box.root + system.SNMPD_CONF
      and d.agent == system.SNMP_AGENT,
      f"conf under the root, the shipped agent: {d.conf}")
d._run(["systemctl", "is-active", "snmpd"])
check(box.asked == [["systemctl", "is-active", "snmpd"]],
      f"commands go through the injected runner: {box.asked}")
d = system.SnmpDaemon(runner=box.run, root=box.root, conf="/etc/snmp/o.conf",
                      agent="/x/agent")
check(d.conf == box.root + "/etc/snmp/o.conf" and d.agent == "/x/agent",
      "a conf and an agent named are taken, the conf still under the root")

print("--- SnmpDaemon._create_v3_users: net-snmp's store, appended once")
box = Box()
d = system.SnmpDaemon(runner=box.run, root=box.root)
bob = system.SnmpUser("bob")
bob.auth, bob.priv = "sha pw1", "aes pw2"
carol = system.SnmpUser("carol")
carol.auth = "sha p"
dan = system.SnmpUser("dan")
STORE = "/var/lib/snmp/snmpd.conf"
d._create_v3_users({"dan": dan, "carol": carol, "bob": bob})
check(box.read(STORE) == 'createUser bob SHA "pw1" AES "pw2"\n'
                         'createUser carol SHA "p"\n',
      f"a createUser per user with an auth passphrase, sorted, priv when "
      f"set, and none for a user with no auth: {box.read(STORE)!r}")
check(box.mode(STORE) == 0o600, "0600: the passphrases are in it")
d._create_v3_users({"bob": bob, "carol": carol})
check(box.read(STORE).count("createUser bob ") == 1,
      "a second apply does not create the user twice")
box.write(STORE, 'usmUser 1 3 0x80001f88 "bob" "bob" NULL .1.3.6.1.6.3.10\n')
eve = system.SnmpUser("eve")
eve.auth = "sha e"
d._create_v3_users({"bob": bob, "eve": eve})
check(box.read(STORE) == 'usmUser 1 3 0x80001f88 "bob" "bob" NULL '
                         '.1.3.6.1.6.3.10\ncreateUser eve SHA "e"\n',
      f"a user net-snmp already rewrote is skipped, the new one appended: "
      f"{box.read(STORE)!r}")

print()
print("\n=== dns_error: a resolver is an address (#437) ===")
# Mutation shown red: the scope test removed - `fe80::1%eth0` is taken,
# because Python's ipaddress accepts a scope and this is the one place that
# says it must not.
for _v in ("1.1.1.1", "2606:4700::1111"):
    check(system.dns_error(_v) is None, f"{_v} is a resolver")
for _v, _needle in (("dns.google", "expected an address"),
                    ("1.1.1", "expected an address"),
                    ("fe80::1%eth0", "without a scope")):
    _why = system.dns_error(_v) or ()
    check(_needle in " ".join(_why), f"{_v!r} is refused: {_why!r}")

print("\n=== write_dns / read_dns: the VTY takes resolv.conf over, and gives "
      "it back (#437) ===")
# Mutations shown red, one at a time: the copy into RESOLV_BEFORE removed
# (giving the file back leaves it empty, and a DHCP box has no resolver until
# the next renewal); read_dns() without the first-line test (a lease's
# resolvers are reported as statements, and the next save pins them).
_r = tempfile.mkdtemp(prefix="dns-root-")
os.makedirs(os.path.join(_r, "etc", "prisma-routing"))
_rc = os.path.join(_r, "etc", "resolv.conf")
_bf = os.path.join(_r, "etc", "prisma-routing", "resolv.conf.before")
with open(_rc, "w") as _f:
    _f.write("# from dhclient\nnameserver 192.0.2.53\n")
_sy = system.System(root=_r)
check(_sy.read_dns() == [],
      "a resolv.conf DHCP wrote is not the VTY's, so it reports no servers")
_ok, _msg = _sy.write_dns(["1.1.1.1", "2606:4700::1111"])
check(_ok and _sy.read_dns() == ["1.1.1.1", "2606:4700::1111"],
      f"the VTY's servers, read back out of the file, in order: "
      f"{_ok} {_msg!r} {_sy.read_dns()!r}")
check(open(_rc).readline().rstrip("\n") == system.DNS_OWNED,
      "and the file says on its first line who owns it")
check(open(_bf).read() == "# from dhclient\nnameserver 192.0.2.53\n",
      "what was there before is kept")
check(oct(os.stat(_rc).st_mode & 0o777) == "0o644",
      "readable by every account, as resolv.conf has to be")
_sy.write_dns(["9.9.9.9"])
check(open(_bf).read() == "# from dhclient\nnameserver 192.0.2.53\n",
      "a second change does not overwrite the keep with the VTY's own file")
_ok, _msg = _sy.write_dns([])
check(_ok and open(_rc).read() == "# from dhclient\nnameserver 192.0.2.53\n"
      and not os.path.exists(_bf),
      "the last server removed puts the old file back and drops the keep")
check(_sy.read_dns() == [], "and the VTY no longer reports any")
_ok, _ = _sy.write_dns([])
check(_ok and open(_rc).read() == "# from dhclient\nnameserver 192.0.2.53\n",
      "removing servers from a file the VTY does not own changes nothing")
os.unlink(_rc)
os.symlink("/run/somebody/resolv.conf", _rc)
_ok, _msg = _sy.write_dns(["1.1.1.1"])
check(not _ok and "something else writes it" in _msg
      and os.path.islink(_rc),
      f"a resolv.conf that is a link is somebody else's, and is left alone: "
      f"{_msg!r}")
check(system.System._owned(_rc) is False,
      "_owned answers False for a file it cannot read")
os.unlink(_rc)
with open(_rc, "w") as _f:
    _f.write(system.DNS_OWNED + "\nnameserver 1.1.1.1\nnameserver banana\n"
             "nameserver 8.8.8.8\nnameserver 9.9.9.9\nnameserver 4.4.4.4\n")
check(_sy.read_dns() == ["1.1.1.1", "8.8.8.8", "9.9.9.9"],
      f"a hand-edited file reads back as what glibc asks, and so what the "
      f"boot's --load accepts: {_sy.read_dns()!r}")
check(not config.Config.parse(
          "".join(f"dns server {a}\n" for a in _sy.read_dns())).unknown,
      "which is a file the parser takes whole")
_sy._replace(_rc, "x\n")
check(open(_rc).read() == "x\n" and not os.path.exists(_rc + ".prisma-new"),
      "_replace leaves the file whole and nothing beside it")

print("\n=== the DHCP hook stands down make_resolv_conf only while the VTY "
      "owns the file (#437) ===")
# Sourced the way dhclient-script sources it, after a make_resolv_conf of its
# own. Mutation shown red: the first-line test dropped from the hook - a box
# with DHCP resolvers and no `dns server` then never gets a resolver again.
_hook = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                     "appliance", "dhclient-prisma-routing-dns")
os.unlink(_rc)


def _dhclient(first_line):
    with open(_rc, "w") as _f:
        _f.write(first_line + "\nnameserver 1.1.1.1\n")
    import subprocess as _sp
    return _sp.run(["sh", "-c", 'make_resolv_conf() { echo DHCP-WROTE; }; '
                    '. "$1"; make_resolv_conf', "_", _hook],
                   capture_output=True, text=True,
                   env=dict(os.environ, PRISMA_RESOLV_CONF=_rc)).stdout.strip()


check(_dhclient(system.DNS_OWNED) == "",
      "while the VTY owns resolv.conf, a lease does not rewrite it")
check(_dhclient("# from dhclient") == "DHCP-WROTE",
      "and when it does not, DHCP writes the file as it always did")

if fail:
    print(f"{fail} FAILURES")
    sys.exit(1)
print("ALL TESTS PASSED")
