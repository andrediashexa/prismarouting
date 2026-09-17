#!/usr/bin/env python3
"""
What a `hexash -c` batch does with a statement the VTY refused (#376).

THE DEFECT, AS THE OPERATOR SAW IT. A batch that set a block size the VTY
bounds printed its refusal and then `commit complete`, and the pool the box
ended up carrying had the DEFAULT block size. Both sentences were true of
something: the refusal was true of the statement, and `commit complete` was
true of what got committed. Neither was true of what the script asked for.

WHY THIS FILE AND NOT A LAB. The lab is the wrong instrument twice over. The
observation is about the argv of ONE process and the words that come back out
of it, which needs no dataplane; and the half that matters most - what the box
APPLIED - is the absence of a message on the wire, which a lab reads off a
box that has to be running for the reading to mean anything. Here the API is
a stub that records, so "nothing was applied" is a list with nothing in it
rather than a pool that is not there for one of several reasons.

WHAT EVERY BOX ASSERTS, and it is the rule #376 asks for: what was PRINTED and
what was APPLIED, never the exit status on its own. The exit status was
already 1 before this change - a script that tests it was never misled, and
that is exactly why nothing caught this for the life of the product.

THE CONTROL IS NOT OPTIONAL. A `commit` that refused everything would pass
every assertion about the refusal, so the box below it commits the same batch
with a legal value and asserts the pool reached the dataplane. The same
shape guards the other two decisions this change makes: the interactive
session is NOT gated, and `commit`'s own refusal is NOT replaced by this one.
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "hexash"))

_root = tempfile.mkdtemp(prefix="hexash-batch-")
os.environ["HEXA_CONFIG"] = os.path.join(_root, "hexarouting.conf")
os.environ["HEXA_ROLLBACK"] = os.path.join(_root, "rollback")
os.makedirs(os.environ["HEXA_ROLLBACK"])

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
    """The dataplane, recording every message the VTY sends it."""

    def __init__(self):
        self.calls = []

    def available(self):
        return (vppapi.OK, "stub")

    def call(self, message, args):
        self.calls.append((message, dict(args)))
        return vppapi.Reply(vppapi.OK)

    def dump(self, message, args):
        return vppapi.Reply(vppapi.OK, fields={"rows": []})

    def interface_index(self, name):
        return 1


class FakeVpp(hexash.Vpp):
    """
    Derived from hexash.Vpp for the reason test_hexash_apply.py records: a
    stub that enumerates the product's methods stops having whatever the
    product adds next, and the suite then dies of AttributeError before it
    reaches a verdict.
    """

    TRACE = False

    def __init__(self):
        self.commands = []

    def run(self, cmd):
        self.commands.append(cmd)
        return 0, ""

    def running(self):
        return 0, ""


class FakeFrr(hexash.Frr):
    def available(self):
        return True

    def show(self, cmd):
        return (0, "")

    def reload(self, lines, check_only=False):
        return (0, "")


# The seams, set once. `main()` builds its own Shell out of these three names
# and reads the invoking account's real groups, so a suite that left them
# alone would measure a read-only account being refused `configure` and
# nothing else.
hexash.capabilities = lambda: {hexash.CAP_ALL, hexash.CAP_CGNAT}
hexash.Vpp = FakeVpp
hexash.Frr = FakeFrr


def batch(*cmds):
    """
    `hexash -c ... -c ...` as a process is, through the REAL main().

    Through main() and not through Shell.feed_batch() directly, because the
    argv parse and the call into the batch driver are part of what #376 is
    about: the defect was one line in main() and a suite that reached past it
    would have stayed green through the whole of it.

    Returns the exit status, what the box printed, and the dataplane.
    """
    api = FakeApi()
    hexash.vppapi.Vat2 = lambda *a, **k: api
    argv = sys.argv
    out = io.StringIO()
    try:
        sys.argv = ["hexash"] + [a for c in cmds for a in ("-c", c)]
        with contextlib.redirect_stdout(out):
            rc = hexash.main()
    finally:
        sys.argv = argv
    return rc, out.getvalue(), api


def pools_added(api):
    """The pools the dataplane was actually told to create."""
    return [a.get("name") for m, a in api.calls
            if m == "bpa_pool_add_del" and a.get("is_add")]


def block_size_of(api, name):
    for m, a in api.calls:
        if m == "bpa_pool_add_del" and a.get("name") == name:
            return a.get("block_size")
    return None


THE_POOL = ("configure", "cgnat pool p1", "inside 100.64.0.0/22",
            "outside 203.0.113.0/28")

print("=== 1. the batch from the issue: a block size the VTY bounds ===")
rc, said, api = batch(*THE_POOL, "block-size 16", "exit", "commit")
print(said.rstrip())
check("expected 64-32768" in said,
      "the statement itself is still refused, in the words it always used")
check("commit complete" not in said,
      "and the box does NOT say 'commit complete' over it")
check("block-size 16" in said.split("refusing to commit")[-1],
      "the refusal names the statement that was not applied")
check("NOTHING was applied" in said,
      "and says what was not applied, not only what was wrong")
check(pools_added(api) == [],
      f"nothing reached the dataplane: {pools_added(api)}")
check(rc == 1, f"and the exit status is still 1, not {rc}")

print()
print("=== 2. the control: the same batch with a value the VTY takes ===")
# Without this, a `commit` that refused everything passes every assertion
# above. It is also the whole of the decision that `-c` is not simply
# forbidden from committing.
rc, said, api = batch(*THE_POOL, "block-size 1024", "exit", "commit")
print(said.rstrip())
check("commit complete" in said, "an unrefused batch still commits")
check(pools_added(api) == ["p1"],
      f"and the pool reaches the dataplane: {pools_added(api)}")
check(block_size_of(api, "p1") == 1024,
      f"carrying the block size that was asked for, not the default: "
      f"{block_size_of(api, 'p1')}")
check(rc == 0, f"and the exit status is 0, not {rc}")

print()
print("=== 3. every refused statement is named, not only the first ===")
# load() reads the whole file and then names every line it cannot hold, and
# this is the same shape: a batch that stopped at its first refusal would
# report one defect per run, so a twelve-statement batch would be fixed one
# refusal at a time.
rc, said, api = batch(*THE_POOL, "block-size 16", "ports 0", "exit", "commit")
print(said.rstrip())
check("2 statement(s) in this batch were refused" in said,
      "the count is of every statement that was refused")
tail = said.split("refusing to commit")[-1]
check("block-size 16" in tail and "ports 0" in tail,
      "and both are named")
check("expected 1-65535" in said,
      "the batch ran past the first refusal, so the second one printed too")
check(pools_added(api) == [], f"and still nothing was applied: "
                              f"{pools_added(api)}")

print()
print("=== 4. a batch that commits nothing is neither gated nor stopped ===")
# The other half of the answer to "is -c one command or a script". A question
# changes nothing, so there is nothing for it to be half of, and it must not
# acquire a refusal it had no reason to grow. The pair below is also where a
# batch that stopped at its first refusal would be visible: the second
# question is one an operator asked and would not get an answer to.
rc, said, api = batch("show version")
check(hexash.hexarouting_version() in said,
      "a single question is answered as it always was")
check(rc == 0, f"and answers with status 0, not {rc}")
rc, said, api = batch("rollback 99", "show version")
print(said.splitlines()[0] if said else "<nothing>")
check("revision 99 does not exist" in said,
      "a refused statement in a read-only batch is still refused")
check(hexash.hexarouting_version() in said,
      "and the question after it is still asked")
check("refusing to commit" not in said,
      "with nothing gated, because nothing here commits")
check(rc == 1, f"and the exit status still says a statement failed, not {rc}")

print()
print("=== 5. the interactive session is NOT gated ===")
# The line is where load() draws it: on whether anybody is reading the
# refusal as it happens. An operator who typed the bad value, read the
# refusal and typed a good one has answered it, and refusing their commit
# would be the alarm that is always on.
api = FakeApi()
sh = hexash.Shell()
sh.caps = {hexash.CAP_ALL, hexash.CAP_CGNAT}
sh.api = api
sh.dp = FakeVpp()
sh.frr = FakeFrr()
out = io.StringIO()
with contextlib.redirect_stdout(out):
    for line in (*THE_POOL, "block-size 16", "block-size 1024", "exit",
                 "commit"):
        sh.feed(line)
said = out.getvalue()
print(said.rstrip())
check("expected 64-32768" in said,
      "the typed statement is refused, exactly as in a batch")
check("refusing to commit" not in said,
      "and the commit after it is NOT refused for it")
check("commit complete" in said, "the box says the commit completed")
check(block_size_of(api, "p1") == 1024,
      f"and the pool carries the value that was typed second: "
      f"{block_size_of(api, 'p1')}")

print()
print("=== 6. commit's own refusal is kept, not replaced ===")
# lab/ipfix.sh drives this pair on a real box and asserts the second message
# word for word: a `commit check` refused because nothing is configured to
# export to, and the `commit` behind it answering for itself. A refusal from
# `commit` is not a statement that failed to reach the candidate - the
# candidate is exactly what was asked for - so it must not be counted here.
rc, said, api = batch("configure", "cgnat logging enable", "commit check",
                      "commit")
print(said.rstrip())
check("candidate did not pass validation; nothing applied" in said,
      "the commit after a refused check still answers for itself")
check("refusing to commit" not in said,
      "and this change did not take that sentence away from it")
check("UDP export has no collector" in said,
      "and the check that refused is the one that lab drives, still firing")

print()
print("=== 7. a batch that reboots the box is not gated: there is no commit "
      "in it ===")
# `hexash -c "system reboot"` is what a runbook types (#388), and the rule
# above is on the COMMIT, so a batch with none has nothing to be gated on.
# Asserted on what systemd was ASKED and what was printed, never the exit
# status: the status is 0 either way on an accepted reboot, and 1 either
# way when a statement before it was refused. Through main(), as every box
# here is, because the seam is `hexash.System` and main() builds its own.
_asked = []


class FakeSystem(hexash.System):
    def __init__(self, *a, **k):
        super().__init__(runner=self.ask)

    def ask(self, argv, **kw):
        _asked.append(list(argv))
        return 0, ""


_real_system = hexash.System
hexash.System = FakeSystem


def _systemctl():
    """
    The systemctl asks alone. The cost prose measures the running
    configuration the way `save` does, and sshd is read through the same
    seam, so the whole list here is [['sshd', '-T'], ['systemctl', 'reboot']]
    - measured, and the reason the filter exists.
    """
    return [a for a in _asked if a[:1] == ["systemctl"]]


try:
    rc, said, api = batch("system reboot")
    print(said.rstrip())
    check("restarting the box" in said,
          "the box says what it is doing, in a batch as at the prompt")
    check(_systemctl() == [["systemctl", "reboot"]],
          f"and systemd was asked for the reboot: {_systemctl()}")
    check(["sshd", "-T"] in _asked,
          f"after the running configuration was read through the same seam, "
          f"for the unsaved warning: {_asked}")
    check("refusing to commit" not in said,
          "with nothing gated, because nothing here commits")
    check(rc == 0, f"and the exit status is 0, not {rc}")
    check(pools_added(api) == [],
          f"and the dataplane was told nothing: {pools_added(api)}")

    # A refused statement ahead of it does not stop it either. The refusal
    # is still printed and still counted; the reboot is still asked for.
    # That is load()'s shape - the batch runs to its end - and the rule that
    # makes commit different is that commit APPLIES what the refused
    # statement would have been half of. A reboot applies nothing.
    #
    # `clear bgp` with no neighbour, and not the `rollback 99` of box 4: a
    # rollback, refused or not, leaves the session in configuration mode,
    # where `system` is not a statement, so the box would be measuring the
    # mode and not the gate.
    _asked.clear()
    rc, said, api = batch("clear bgp", "system reboot")
    print(said.splitlines()[0] if said else "<nothing>")
    check("usage: clear bgp" in said,
          "the refused statement before it is still refused")
    check(_systemctl() == [["systemctl", "reboot"]],
          f"and the reboot after it is still asked for: {_systemctl()}")
    check("refusing to commit" not in said,
          "with no commit gate in sight")
    check(rc == 1, f"and the exit status still says a statement failed, "
                   f"not {rc}")
finally:
    hexash.System = _real_system
check(hexash.System is _real_system,
      "the System seam was put back for whatever runs after this file")

shutil.rmtree(_root, ignore_errors=True)

print()
if fail:
    print(f"{fail} FAILURES")
    sys.exit(1)
print("ALL TESTS PASSED")
