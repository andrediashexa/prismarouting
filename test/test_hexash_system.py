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
test/test_hexash_refusals.py: every `self.err` site in the VTY has to have
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

_root = tempfile.mkdtemp(prefix="hexash-system-")
# The file `save` writes and the store 'commit confirmed' arms, both under
# this suite's own directory, so no box here reads /etc/hexarouting on the
# machine running it - which on an appliance would be a real configuration.
os.environ["HEXA_CONFIG"] = os.path.join(_root, "hexarouting.conf")
os.environ["HEXA_CONFIRM"] = os.path.join(_root, "confirm-pending")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "hexash"))

import config      # noqa: E402
import hexash      # noqa: E402

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
FRESH_POOL = config.Config.parse(
    "ssh permit-root-login yes\n"
    "user root\n password $6$x$installer\n exit\n"
    "cgnat pool p\n inside 100.64.0.0/22\n outside 200.150.0.16/30\n"
    " exit\n").text()
#: What the installer ships at CONFIG_PATH: three comments and no statement.
SHIPPED = "!\n! HexaRouting\n!\n"
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
    sh = hexash.Shell()
    sh.caps = caps
    sd = Systemd(rc, says)
    sh.sys = hexash.System(runner=sd.run)
    text = SAVED if running is None else running
    if running is not REAL:
        sh.running_config = lambda: config.Config.parse(text)
    on_disk = SAVED if saved is None else saved
    if on_disk is ABSENT:
        if os.path.exists(hexash.CONFIG_PATH):
            os.remove(hexash.CONFIG_PATH)
    else:
        with open(hexash.CONFIG_PATH, "w") as f:
            f.write(on_disk)
    if os.path.exists(hexash.CONFIG_PATH):
        # Back to readable: the unreadable box in section 8 leaves it 0000.
        os.chmod(hexash.CONFIG_PATH, 0o600)
    sh.confirm = hexash.ConfirmPending(hexash.CONFIRM_PATH)
    if os.path.exists(hexash.CONFIRM_PATH):
        os.remove(hexash.CONFIRM_PATH)
    if pending is not None:
        sh.confirm.arm(pending, SAVED, 10)
    out = io.StringIO()
    sd.screen = out
    real_geteuid = hexash.os.geteuid
    if euid is not None:
        hexash.os.geteuid = lambda: euid
    try:
        with contextlib.redirect_stdout(out):
            rv = sh.feed(line)
    finally:
        hexash.os.geteuid = real_geteuid
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


ADMIN = {hexash.CAP_ALL, hexash.CAP_CGNAT}
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
check(any(hexash.GROUP_ADMIN in r for r in rows(said)),
      "naming the membership that would allow it")
# The WHOLE list, not the systemctl half: the refusal is before the cost is
# measured, so not even the running configuration was read.
check(sd.asked == [], f"and nothing was asked through the seam: {sd.asked}")
check("restarting the box" not in said,
      "and the box was not told it is restarting")
check(rv is True, "the session goes on")

rv, said, sd, sh = driven({hexash.CAP_CGNAT}, "system shutdown")
check("error: system shutdown: permission denied for a "
      f"{hexash.ROLE_CGNAT} account" in rows(said),
      f"a CGNAT operator is refused by the name of their role: "
      f"{rows(said)[:1]}")
check(sd.asked == [], f"and nothing was asked through the seam: {sd.asked}")

print()
print("=== 7. the menu with no verb, or a verb it has not got ===")
for line in ("system", "system halt", "system reboot now"):
    rv, said, sd, sh = driven(ADMIN, line)
    check("error: usage: system reboot | system shutdown" in rows(said),
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

# WHAT AN EMPTY FILE LOSES, AND WHAT IT DOES NOT. `hexash --load` applies
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
    with open(hexash.CONFIG_PATH, "w") as f:
        f.write(SAVED)
    os.chmod(hexash.CONFIG_PATH, 0o000)
    try:
        sh = hexash.Shell()
        sh.caps = ADMIN
        sh.running_config = lambda: config.Config.parse(SAVED)
        sh.confirm = hexash.ConfirmPending(hexash.CONFIRM_PATH)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sh.say_unsaved()
        said = out.getvalue()
    finally:
        os.chmod(hexash.CONFIG_PATH, 0o600)
    check("warning: whether the running configuration is saved could not be "
          "checked" in rows(said) and hexash.CONFIG_PATH in said,
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
if fail:
    print(f"{fail} FAILURES")
    sys.exit(1)
print("ALL TESTS PASSED")
