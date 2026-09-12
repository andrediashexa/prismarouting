#!/usr/bin/env python3
"""
Whether "commit confirmed" outlives the session that made it (#212).

The promise is about the one change that can cut off the session the operator
is typing in, so the session is exactly what is not there when the revert is
needed. Every check below is therefore made from a Shell that did NOT make the
commit - a second object, standing in for the second process - because a test
that asks the committing Shell whether it still intends to revert is asking
the half that the failure removes.

The end of it is proved on a real box by lab/install-iso.sh, which logs in,
commits, logs OUT and comes back to find the change gone. This file is what
makes the pieces of it decidable in milliseconds: the file that is written,
the confirmation that removes it, the reboot that must not revert a confirmed
change, and the words the box prints about all three.
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "hexash"))
import config      # noqa: E402
import hexash      # noqa: E402
import vppapi      # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")
    else:
        print(f"  [ ok ] {msg}")


class FakeApi:
    def available(self):
        return (vppapi.OK, "stub")

    def call(self, message, args):
        return vppapi.Reply(vppapi.OK)


class FakeVpp:
    TRACE = False

    def run(self, cmd):
        return 0, ""

    def running(self):
        return 0, ""


class Box:
    """
    One appliance, and as many hexash processes over it as a test needs.

    The running configuration is state on the BOX and not on the Shell,
    because that is the arrangement the issue is about: the configuration
    survives the session and the deadline did not.
    """

    def __init__(self, root, running):
        self.root = root
        self.running = running
        self.confirm_path = os.path.join(root, "confirm-pending")

    def shell(self):
        sh = hexash.Shell()
        sh.confirm = hexash.ConfirmPending(self.confirm_path)
        sh.caps = {hexash.CAP_ALL, hexash.CAP_CGNAT}
        sh.api = FakeApi()
        sh.dp = FakeVpp()
        sh.frr = type("F", (), {
            "available": lambda s: False,
            "reload": lambda s, lines, check_only=False: (0, "")})()
        sh.rb = type("R", (), {"push": lambda s, text, comment="": None})()
        sh.running_config = lambda: config.Config.parse(self.running)

        def apply(cfg, check_only=False):
            if not check_only:
                self.running = cfg.text()
            return True
        sh.apply = apply
        return sh

    def commit(self, text, args=()):
        """A whole session: it opens, it commits, and it is gone."""
        sh = self.shell()
        sh.candidate = config.Config.parse(text)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with contextlib.redirect_stderr(buf):
                sh.do_commit(list(args))
        return buf.getvalue()

    def expire(self):
        """What hexarouting-confirm.service runs, in a process of its own."""
        sh = self.shell()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with contextlib.redirect_stderr(buf):
                rc = sh.confirm_expired()
        return rc, buf.getvalue()

    def pending(self):
        return hexash.ConfirmPending(self.confirm_path).read()

    def rewind(self, seconds):
        """
        Move the deadline into the past, as waiting would.

        A box with nothing armed is left alone rather than raised over: that
        is a real state - it is what a confirmed window looks like - and the
        check that follows every call here is the one that should speak.
        """
        p = self.pending()
        if p is None:
            return
        hexash.ConfirmPending(self.confirm_path).arm(
            p["deadline"] - seconds, p["config"], p["minutes"], p["attempts"])


BEFORE = "!\nuser alice\n password $1$x$abc\n role administrator\n!\n"
AFTER = BEFORE + "user bob\n password $1$x$def\n role administrator\n!\n"


def box():
    d = tempfile.mkdtemp()
    return Box(d, BEFORE)


print("=== the session that commits is gone, and the revert still happens ===")
# The issue, in four lines. The Shell that armed the window is never used
# again: the commit, the wait and the revert are three different objects over
# one box, which is what "the operator logged out" means here.
b = box()
said = b.commit(AFTER, ["confirmed", "1"])
check("user bob" in b.running, "the change is applied first")
check(b.pending() is not None,
      "the deadline is recorded on the box, not only in the session")
b.rewind(61)
rc, out = b.expire()
check("user bob" not in b.running,
      "and a process that never saw that session rolls it back")
check(rc == 0, f"reporting success, not a failure: rc={rc}")
print("  the box said: " + (out.strip().splitlines() or ["<nothing>"])[0])

print("\n=== confirming is a thing an operator can actually do ===")
# It was not. `commit` with nothing left to change returned at "no changes to
# commit" with the deadline still armed, and nothing anywhere else cleared it
# - so the box reverted changes that HAD been confirmed and there was no way
# to stop it.
b = box()
b.commit(AFTER, ["confirmed", "1"])
said = b.commit(b.running)
check(b.pending() is None, "a second 'commit' removes the pending revert")
check("confirmed" in said, f"and says so: {said.strip()!r}")
# There is nothing left for the deadline to arrive AT. That is the point: the
# record is removed at the moment of the confirmation and not at the end of
# the window, so no later run of anything can revert a confirmed change.
b.rewind(61)
rc, out = b.expire()
check("user bob" in b.running,
      "so the confirmed change is still there once the window has passed")
check(out.strip() == "", f"and the box says nothing: {out.strip()!r}")

print("\n=== a reboot inside the window ===")
# The distinction the issue asks for, and the reason the record is the FILE
# and not the end of the window: a box restarted after a confirmation has
# nothing to revert, and one restarted before its deadline still has the
# deadline it was given. "Rebooted" here is what a reboot leaves behind - a
# box with its configuration and its /etc, and no hexash process at all.
b = box()
b.commit(AFTER, ["confirmed", "1"])
b.rewind(61)          # the box was off for longer than the window
rc, out = b.expire()  # the first run of the unit after the reboot
check("user bob" not in b.running,
      "a box rebooted before the deadline reverts the unconfirmed change")

b = box()
b.commit(AFTER, ["confirmed", "1"])
b.commit(b.running)   # confirmed, and then the box is rebooted
b.rewind(61)          # and the window it was given has since gone by
rc, out = b.expire()
check("user bob" in b.running,
      "a box rebooted after the confirmation keeps the confirmed change")

print("\n=== the deadline is a moment, not an amount of uptime ===")
# Stored absolute, so a box that was switched off past its own deadline comes
# back and reverts at once. Nobody confirmed; the window expired while it was
# down, and pretending it starts again at boot would hold an unconfirmed
# change for another ten minutes on a box nobody is watching.
b = box()
b.commit(AFTER, ["confirmed", "60"])
p = b.pending()
check(p is not None and abs(p["deadline"] - (time.time() + 3600)) < 5,
      f"the file holds an absolute deadline: {p and p['deadline']}")

print("\n=== the window is not open before it is due ===")
b = box()
b.commit(AFTER, ["confirmed", "1"])
rc, out = b.expire()
check("user bob" in b.running,
      "a revert before the deadline does nothing")
check(b.pending() is not None, "and leaves the window armed")

print("\n=== what the box PRINTS matches what it does ===")
# The defect the issue names. The old wording described a revert that survived
# the session and the product implemented one that did not, so an operator was
# LESS likely to keep a second session open because of what they were told.
b = box()
said = b.commit(AFTER)
check("commit confirmed" in said,
      "a change to accounts is still applied as 'commit confirmed'")
check("cut off the session" in said, "and still says why")
check("armed on the box" in said,
      f"and says where the revert lives: {said!r}")
check("logging out" in said and "does not cancel it" in said,
      "and that logging out does not cancel it")
check("session that works" not in said,
      "and no longer asks for a session the failure it guards against removes")

print("\n=== a promise the box cannot keep is not made ===")
# The other half of "the message must match". A store that cannot be written
# leaves the old behaviour - a timer inside this process - and that is a
# weaker promise, so it is stated as one rather than dressed in the sentence
# above.
b = box()
os.chmod(b.root, 0o500)
try:
    said = b.commit(AFTER, ["confirmed", "1"])
finally:
    os.chmod(b.root, 0o700)
check("warning: the revert could NOT be armed on the box" in said,
      f"the box says the revert is not armed: {said!r}")
check("IF THIS SESSION LASTS" in said,
      "and what it is worth instead")
check("armed on the box, not in this session" not in said,
      "and does not also claim the strong promise")

print("\n=== and the operator who stays logged in still gets the revert ===")
# Unchanged on purpose: the case that worked is the case that must keep
# working, on the same timer. This is the in-session path with no file behind
# it at all - every lab container, and every developer's tree.
b = box()
sh = b.shell()
sh.candidate = config.Config.parse(AFTER)
with contextlib.redirect_stdout(io.StringIO()):
    sh.do_commit(["confirmed", "1"])
check("user bob" in b.running, "the change is applied")
sh.confirm_deadline = time.time() - 1
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.check_confirm_timeout()
check("user bob" not in b.running,
      "and the session that armed it reverts when the deadline passes")
check("was not confirmed" in buf.getvalue(),
      f"with the same sentence as before: {buf.getvalue().strip()!r}")

print("\n=== a session does not revert what another session confirmed ===")
# Two sessions, one box. The operator confirms from the one that still works,
# which is the whole point of the feature - and the session that armed the
# window must not then undo it on its own copy of the deadline.
b = box()
armed = b.shell()
armed.candidate = config.Config.parse(AFTER)
with contextlib.redirect_stdout(io.StringIO()):
    armed.do_commit(["confirmed", "1"])
b.commit(b.running)                      # confirmed from somewhere else
armed.confirm_deadline = time.time() - 1
with contextlib.redirect_stdout(io.StringIO()):
    armed.check_confirm_timeout()
check("user bob" in b.running,
      "the session that armed it leaves a confirmed change alone")

print("\n=== a revert that keeps failing stops, loudly ===")
# A unit firing every few seconds must not print the same failure onto the
# console forever: that is the same as printing nothing. What it must not do
# either is give up in silence, holding a change nobody confirmed.
b = box()
b.commit(AFTER, ["confirmed", "1"])
b.rewind(61)
outs = []
for _ in range(hexash.CONFIRM_ATTEMPTS + 1):
    sh = b.shell()
    sh.apply = lambda cfg, check_only=False: check_only
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.redirect_stderr(buf):
            rc = sh.confirm_expired()
    outs.append(buf.getvalue())
    if not os.path.exists(b.confirm_path):
        break
check(not os.path.exists(b.confirm_path),
      f"it stops trying after {hexash.CONFIRM_ATTEMPTS} attempts")
check("giving up" in outs[-1] and "never confirmed" in outs[-1],
      f"and says the box is holding an unconfirmed change: {outs[-1]!r}")
check(os.path.exists(b.confirm_path + ".failed"),
      "keeping the configuration somebody now has to put back by hand")
rc, out = b.expire()
check(out.strip() == "" and rc == 0,
      f"and then it is quiet: {out.strip()!r}")

print("\n=== the file holds hashes, so it is not world-readable ===")
b = box()
b.commit(AFTER, ["confirmed", "1"])
if not os.path.exists(b.confirm_path):
    check(False, "there is no pending file at all to state a mode for")
else:
    mode = os.stat(b.confirm_path).st_mode & 0o777
    check(mode & 0o007 == 0, f"mode is {mode:04o}")
    check("$1$x$abc" in open(b.confirm_path).read(),
          "and it does hold them, or the revert could not restore a password")

print("\n=== an account that cannot READ the store still gets a VTY ===")
# Found by lab/install-iso.sh on the box it installs, which is the only place
# it could be: /etc/hexarouting is 2770 group hexa-cgnat, so an account with
# no role gets EACCES here - and the banner draws on EVERY login. Raising made
# hexash exit 1 on that login and hexash-login handed out a bash prompt to keep
# the box reachable, replacing the whole VTY with a shell for every read-only
# account on the box, over a window that may not even be open.
b = box()
b.commit(AFTER, ["confirmed", "1"])
if os.geteuid() == 0:
    # Stated rather than quietly passed. root reads a mode 0000 file, so this
    # cannot be decided here at all - the box it is about has non-root
    # accounts, and lab/install-iso.sh is where it is decided on one.
    print("  [skip] running as root, which reads a file no mode can deny it")
else:
    os.chmod(b.confirm_path, 0o000)
    try:
        unreadable = hexash.ConfirmPending(b.confirm_path).read()
        crashed = False
    except Exception as e:                               # noqa: BLE001
        unreadable, crashed = None, e
    finally:
        os.chmod(b.confirm_path, 0o660)
    check(crashed is False,
          f"reading a store this account cannot open must not raise: "
          f"{crashed!r}")
    check(unreadable is None, "it answers 'nothing pending' instead")

print("\n=== saving inside an open window is not silently contradictory ===")
# "commit confirmed" puts the RUNNING configuration back and `save` decides
# what the box boots into. Saving inside the window writes the unconfirmed
# change into the file the next boot reads, and the revert does not rewrite
# it - so the box reverts, and the change comes back at the next reboot.
b = box()
b.commit(AFTER, ["confirmed", "1"])
sh = b.shell()
hexash.CONFIG_PATH = os.path.join(b.root, "hexarouting.conf")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    with contextlib.redirect_stderr(buf):
        sh.save_running()
said = buf.getvalue()
check("warning: what you just saved has NOT been confirmed" in said,
      f"the box says so: {said!r}")
check("reboot" in said, "and names the reboot it would come back on")

b2 = box()
sh = b2.shell()
hexash.CONFIG_PATH = os.path.join(b2.root, "hexarouting.conf")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    with contextlib.redirect_stderr(buf):
        sh.save_running()
check("NOT been confirmed" not in buf.getvalue(),
      "and says nothing when no window is open")

print("\n=== somebody logging back in is told a window is open ===")
# The operator this exists for is the one who just lost a session. Nothing
# else on that screen says the box is about to undo a change under them, or
# that one word keeps it.
b = box()
b.commit(AFTER, ["confirmed", "1"])
sh = b.shell()
sh.feed = lambda line: False
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    with contextlib.redirect_stderr(buf):
        real_stdin_isatty = sys.stdin.isatty
        try:
            sys.stdin = type("T", (), {"isatty": lambda s: True,
                                       "readline": lambda s: ""})()
            sh.repl()
        finally:
            sys.stdin = sys.__stdin__
banner = buf.getvalue()
check("commit confirmed" in banner and "rolls the last change back" in banner,
      f"the banner names the pending revert: {banner[-300:]!r}")

for d in (b.root,):
    shutil.rmtree(d, ignore_errors=True)

print()
if fail:
    print(f"FAILED: {fail} check(s)")
    sys.exit(1)
print("commit confirmed outlives the session that made it")
