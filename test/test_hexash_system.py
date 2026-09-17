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
source.

WHY THE SEAM. `Shell.sys` is a `System`, and `System(runner=...)` is the
injection point the product declares - the same one test_ssh_settings.py hands
a recorder. A suite that ran the real thing would reboot the machine running
`make test`, which is the one outcome nothing here may produce.

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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "hexash"))

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
    What the VTY asked systemd for, and what the screen held at that instant.

    `answer` is what `systemctl` comes back with. `at_ask` is a copy of
    everything the VTY had printed when the ask arrived, which is what the
    said-before-asked boxes read.
    """

    def __init__(self, rc=0, says=""):
        self.rc, self.says = rc, says
        self.asked = []
        self.at_ask = []
        self.screen = None

    def run(self, argv, **kw):
        self.asked.append(list(argv))
        self.at_ask.append(self.screen.getvalue() if self.screen else "")
        return self.rc, self.says


def driven(caps, line, rc=0, says=""):
    """
    One statement, typed at the operational prompt.

    Through `feed` and not `do_system` directly, because the dispatch is half
    of #388: typed before this change, `system reboot` fell through to the
    FRR pass-through and was refused in vtysh's words. A box that called the
    method would be green with the statement unreachable from the prompt.
    """
    sh = hexash.Shell()
    sh.caps = caps
    sd = Systemd(rc, says)
    sh.sys = hexash.System(runner=sd.run)
    out = io.StringIO()
    sd.screen = out
    with contextlib.redirect_stdout(out):
        rv = sh.feed(line)
    return rv, out.getvalue(), sd, sh


def rows(text):
    return text.splitlines()


def prose(text):
    """
    The screen with the console's folds taken back out.

    The cost rows go through print_prose, which wraps them at the console's
    width, so a sentence can arrive as two rows with the fold wherever the
    width put it. A needle for a SENTENCE is read here; a needle for a
    verdict row is read from rows(), because a verdict is one row by
    construction and its width is part of what is being asserted.
    """
    return " ".join(r.strip() for r in text.splitlines())


ADMIN = {hexash.CAP_ALL, hexash.CAP_CGNAT}

print("=== 1. system reboot, by an administrator, and systemd takes it ===")
rv, said, sd, sh = driven(ADMIN, "system reboot")
print(said.rstrip())
check(rows(said)[:1] == ["restarting the box"],
      f"the first row says what is happening: {rows(said)[:1]}")
check("every session on this box ends, including this one" in prose(said),
      "and that every session on the box ends, this one included")
check("the dataplane stops; systemd runs its stop-drain first" in prose(said),
      "and that the dataplane stops, drained first")
check("it comes back by itself, claiming its dataplane card at boot" in prose(said),
      "and that it comes back claiming its card with nothing typed (#378)")
check(sd.asked == [["systemctl", "reboot"]],
      f"systemd was asked for exactly 'systemctl reboot': {sd.asked}")
check(sd.at_ask and "restarting the box" in sd.at_ask[0]
      and "claiming its dataplane card" in prose(sd.at_ask[0]),
      "and every one of those rows was on the screen BEFORE it was asked")
check(sh.failed == 0 and rv is True,
      f"nothing was refused (failed={sh.failed}) and the session is left to "
      f"systemd to end (rv={rv})")
check("error:" not in said, "and no verdict was printed over a reboot that "
                            "systemd accepted")

print()
print("=== 2. system shutdown: the other verb, the other unit, its own cost ===")
rv, said, sd, sh = driven(ADMIN, "system shutdown")
print(said.rstrip())
check(rows(said)[:1] == ["shutting the box down"],
      f"the first row says what is happening: {rows(said)[:1]}")
check("every session on this box ends, including this one" in prose(said),
      "the cost every session pays is said for this verb too")
check("stays off until something powers it on again" in prose(said),
      "and what makes it different from a reboot: it does not come back")
check("comes back claiming its card by itself" in prose(said),
      "and what happens when somebody does power it on (#378)")
check(sd.asked == [["systemctl", "poweroff"]],
      f"systemd was asked for exactly 'systemctl poweroff': {sd.asked}")
check(sd.at_ask and "stays off until" in prose(sd.at_ask[0]),
      "said before it was asked")
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
    check(len(sd.asked) == 1 and sd.asked[0] == ["systemctl", unit],
          f"'system {verb}' asks ['systemctl', '{unit}'] and adds no flag: "
          f"{sd.asked}")

print()
print("=== 4. systemd refuses, and the refusal is rendered in its words ===")
# The two-line refusal systemd really prints. It has to reach the operator on
# the verdict row, whole, because the alternative is somebody who believes
# the box is going down and walks away from one that is not.
TWO_LINES = ("Failed to reboot: Interactive authentication required.\n"
             "See system logs and 'systemctl status' for details.\n")
rv, said, sd, sh = driven(ADMIN, "system reboot", rc=1, says=TWO_LINES)
print(said.rstrip())
check("error: the box could not be restarted: Failed to reboot: Interactive "
      "authentication required. See system logs and 'systemctl status' for "
      "details." in rows(said),
      "the verdict names the failure in systemd's own words, on one row")
check(rows(said)[:1] == ["restarting the box"],
      "and the rows before the ask were still printed: the operator can see "
      "what was attempted")
check(sh.failed == 1, f"counted as one refusal: failed={sh.failed}")
check(sd.asked == [["systemctl", "reboot"]],
      f"and it was asked once, not retried: {sd.asked}")

rv, said, sd, sh = driven(ADMIN, "system shutdown", rc=1,
                          says="Failed to power off: unit not found")
check("error: the box could not be shut down: Failed to power off: unit not "
      "found" in rows(said),
      f"the shutdown refusal has its own verb: {rows(said)[-1:]}")

print()
print("=== 5. no systemctl at all is a refusal, not 'written and not run' ===")
# rc 127 is what System._subprocess answers when the binary is not there,
# which is every developer box. apply_dataplane_if treats that as "stored,
# the tuner is not on this machine" because something WAS written; a reboot
# that did not happen has no half that did.
rv, said, sd, sh = driven(ADMIN, "system reboot", rc=127,
                          says="systemctl: not found")
check("error: the box could not be restarted: systemctl: not found"
      in rows(said), f"named as a refusal: {rows(said)[-1:]}")
check(sh.failed == 1, f"and counted: failed={sh.failed}")

rv, said, sd, sh = driven(ADMIN, "system reboot", rc=1, says="")
check("error: the box could not be restarted: systemctl exited 1 and said "
      "nothing" in rows(said),
      f"a silent failure is still a refusal that ends in words: "
      f"{rows(said)[-1:]}")

print()
print("=== 6. a read-only account is refused BEFORE systemd is asked ===")
rv, said, sd, sh = driven(set(), "system reboot")
print(said.rstrip())
check("error: system reboot: permission denied for a read-only account"
      in rows(said),
      "the same rendered refusal every privileged statement gives")
check(any(hexash.GROUP_ADMIN in r for r in rows(said)),
      "naming the membership that would allow it")
check(sd.asked == [], f"and systemd was never asked: {sd.asked}")
check("restarting the box" not in said,
      "and the box was not told it is restarting")
check(rv is True, "the session goes on")

rv, said, sd, sh = driven({hexash.CAP_CGNAT}, "system shutdown")
check("error: system shutdown: permission denied for a "
      f"{hexash.ROLE_CGNAT} account" in rows(said),
      f"a CGNAT operator is refused by the name of their role: "
      f"{rows(said)[:1]}")
check(sd.asked == [], f"and systemd was never asked: {sd.asked}")

print()
print("=== 7. the menu with no verb, or a verb it has not got ===")
for line in ("system", "system halt", "system reboot now"):
    rv, said, sd, sh = driven(ADMIN, line)
    check("error: usage: system reboot | system shutdown" in rows(said),
          f"{line!r} is answered with the usage: {rows(said)[:1]}")
    check(sd.asked == [], f"and systemd was not asked: {sd.asked}")

print()
if fail:
    print(f"{fail} FAILURES")
    sys.exit(1)
print("ALL TESTS PASSED")
