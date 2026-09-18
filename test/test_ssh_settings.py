#!/usr/bin/env python3
"""
What the VTY reads back from sshd, against what the VTY will accept.

THE DEFECT THIS HOLDS (#304). OpenSSH takes `prohibit-password` and
`without-password` as two names for one PermitRootLogin setting, and `sshd -T`
- which is how src/prismash/system.py reads the effective configuration -
canonicalises to the OLD one. Measured on OpenSSH 9.2: a config file saying
`prohibit-password` reads back as `permitrootlogin without-password`.

So the VTY offered `prohibit-password` in its completion tree, wrote it,
read back a word it had never heard of, and refused its own setting:

    ssh permit-root-login 'without-password': expected one of yes, no,
    prohibit-password, forced-commands-only

A box configured that way then fails to load its saved configuration at boot,
which is a box that comes up unconfigured on a machine nobody is watching.

THE CONTRACT, and it is more than the one word: anything read_ssh() returns
MUST pass validate_ssh(). The VTY builds its candidate from the running
configuration, so a value the box reads off itself and then refuses is a box
that cannot commit a change to anything else either.

THE OTHER READER OF THIS VALUE was checked by hand and is not asserted here,
because the assertion would have been one that cannot go red.
can_anyone_log_in() decides whether a configuration locks everybody out, and it
compares this setting against "no" and against "yes". The alias is neither, so
a root holding a key is counted under both spellings and a root holding only a
password is counted under neither - which is the right answer for
prohibit-password anyway. Measured, and written down so the next reader does
not re-derive it and so nobody adds a green check there believing it guards
something.

WHAT THIS CANNOT SEE, said out loud rather than left as a gap somebody finds
later: sshd is not run here. The spellings below are OpenSSH's documented ones
and the alias was measured by hand against the daemon on the build machine, but
a future OpenSSH that canonicalises to a fifth word would pass this file and
fail on a box. The holder for that is the daemon itself, and a test that ran it
would be a test that says NOT MEASURED on every machine without one.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src",
                                "prismash"))
import system                                       # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

fail = 0


def bad(msg):
    global fail
    fail += 1
    print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


def reader(text):
    """A System whose `sshd -T` answers with exactly this."""
    def run(argv, stdin=None, timeout=30):
        if argv[:2] == ["sshd", "-T"]:
            return 0, text
        return 127, "not this test's business"
    # dropin is pointed at a path that does not exist, so the fallback reader
    # cannot quietly supply the answer the sshd branch was supposed to give.
    return system.System(runner=run, root="", dropin="/nonexistent/prisma-304")


# Every value OpenSSH accepts for PermitRootLogin, and what sshd -T answers
# with for each. `without-password` is the answer for BOTH spellings of the
# key-only setting, which is the whole of the defect.
SSHD_T_ANSWERS = ("yes", "no", "without-password", "forced-commands-only")

print("=== whatever sshd answers, the VTY accepts ===")
for answer in SSHD_T_ANSWERS:
    s = reader(f"port 22\nlistenaddress 0.0.0.0:22\n"
               f"permitrootlogin {answer}\npasswordauthentication yes\n"
               ).read_ssh()
    why = system.validate_ssh(s)
    if why:
        bad(f"sshd -T said 'permitrootlogin {answer}' and the VTY refuses what "
            f"it read back: {why}")
    else:
        ok(f"'{answer}' reads back as {s['permit-root-login']!r}")

print("\n=== and the old spelling means the setting it means ===")
s = reader("permitrootlogin without-password\n").read_ssh()
if s["permit-root-login"] != "prohibit-password":
    bad(f"'without-password' and 'prohibit-password' are one setting; the VTY "
        f"read it as {s['permit-root-login']!r}")
else:
    ok("without-password is prohibit-password")

print("\n=== the VTY offers exactly what it accepts ===")
# Two copies of one truth: the completion tree an operator sees with '?' and
# the table that refuses a value. They were never compared, and the fix for
# #304 touches neither - so this is here to keep the fix from being undone by
# widening one of them alone.
import prismash                                       # noqa: E402

tree = prismash.TREE["config"]["ssh"][1]["permit-root-login"][1]
offered = set(tree)
accepted = set(system._VALID["permit-root-login"])
if offered != accepted:
    bad(f"the VTY offers {sorted(offered)} and accepts {sorted(accepted)}: an "
        f"operator can be offered a value the box refuses, or refused a value "
        f"'?' told them to type")
else:
    ok(f"all {len(offered)} of them, in both places")

print("\n=== and the check is reading something ===")
if len(SSHD_T_ANSWERS) >= 4 and len(accepted) >= 4:
    ok(f"{len(SSHD_T_ANSWERS)} daemon answer(s) against {len(accepted)} "
       f"accepted value(s)")
else:
    bad("this file is asserting almost nothing")

if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
