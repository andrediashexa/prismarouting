#!/usr/bin/env python3
"""
The modes the VTY leaves behind, and the log path it hands frr-reload.

Both are about a non-root operator holding a role and being unable to do the
job the role says it may. Neither is visible from a developer's own machine,
where everything runs as one account that owns everything it touches - which
is how both survived: the labs that would have caught them ran as root too,
and the one that did not repaired the modes by hand first.

Here rather than only in lab/vty.sh because the lab needs docker, VPP and FRR
and takes minutes, while the rule itself - the VTY never restates a mode the
installer stated, and never leaves a file carrying password hashes readable by
everybody - is decidable in milliseconds and needs no root.
"""
import contextlib
import io
import os
import shutil
import stat
import sys
import tempfile

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


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def save_as_vty(path):
    """`save`, with the message it prints kept out of this file's output."""
    hexash.CONFIG_PATH = path
    sh = hexash.Shell()
    sh.running_config = lambda: config.Config.parse("!\n")
    with contextlib.redirect_stdout(io.StringIO()):
        sh.save_running()


# --------------------------------------------------------------- #188
#
# What appliance/install.sh states: 0770 on the directory, 0660 on the file,
# group hexa-cgnat. An account that may configure has to WRITE both, and both
# hold every password hash on the box.

print("=== the installer's mode survives a save, whoever runs it ===")
d = tempfile.mkdtemp()
try:
    store = os.path.join(d, "hexarouting")
    os.mkdir(store, 0o770)
    os.chmod(store, 0o770)                 # mkdir is masked by the umask
    conf = os.path.join(store, "hexarouting.conf")
    open(conf, "w").close()
    os.chmod(conf, 0o660)

    save_as_vty(conf)

    # The defect: save chmod'd these to 0700/0600, so the first save by
    # anybody - root at boot included - took the group's access away and every
    # later non-root save, commit and rollback failed (#188).
    check(mode(store) == 0o770,
          f"save leaves the directory as the installer set it: {mode(store):04o}")
    check(mode(conf) == 0o660,
          f"and the file: {mode(conf):04o}")
finally:
    shutil.rmtree(d, ignore_errors=True)

print("\n=== and what the VTY has to create itself is group-usable ===")
d = tempfile.mkdtemp()
try:
    store = os.path.join(d, "hexarouting")
    conf = os.path.join(store, "hexarouting.conf")
    save_as_vty(conf)
    # Created under a umask rather than chmod'd afterwards: the mode has one
    # owner and it is the installer, so the VTY states only the half that must
    # never slip.
    check(mode(store) == 0o770,
          f"a directory the VTY creates is 0770: {mode(store):04o}")
    check(mode(conf) == 0o660, f"and the file 0660: {mode(conf):04o}")
    # The constraint that outranks all of it: this file carries the password
    # hash of every local account, so a world-readable copy is an offline
    # cracking target handed to everybody with a login.
    check(not mode(conf) & 0o007, "and no world access to the hashes in it")
finally:
    shutil.rmtree(d, ignore_errors=True)

print("\n=== the rollback store, which a commit writes on the operator's behalf ===")
d = tempfile.mkdtemp()
try:
    store = os.path.join(d, "rollback")
    os.mkdir(store, 0o770)
    os.chmod(store, 0o770)
    rb = hexash.Rollback(path=store, keep=3)
    rb.push("!\n", "first")
    check(mode(store) == 0o770,
          f"push leaves the store as the installer set it: {mode(store):04o}")
    rev = os.path.join(store, "0.conf")
    check(mode(rev) == 0o660, f"and writes the revision 0660: {mode(rev):04o}")
    check(not mode(rev) & 0o007, "with no world access to it either")
    # A revision root wrote at boot must be one the operator can read back,
    # or "rollback 1" is a command that only works for whoever made the
    # revision - which is the same defect one step later.
    check(os.stat(rev).st_mode & stat.S_IRGRP,
          "and group-readable, or a rollback made by root could not be undone")

    store2 = os.path.join(d, "made-here")
    hexash.Rollback(path=store2, keep=3).push("!\n", "first")
    check(mode(store2) == 0o770,
          f"a store the VTY creates is 0770 too: {mode(store2):04o}")
finally:
    shutil.rmtree(d, ignore_errors=True)

print("\n=== a revision this account cannot read is named, not raised ===")
# What a box upgraded from the narrowing VTY carries: a store the installer has
# just put back to the role group, holding up to twenty revisions written
# root-only. They are reachable now, and unreadable.
if os.geteuid() == 0:
    print("  SKIP: running as root, which can read every one of them")
else:
    d = tempfile.mkdtemp()
    try:
        store = os.path.join(d, "rollback")
        rb = hexash.Rollback(path=store, keep=3)
        rb.push("!\n", "written by somebody else")
        os.chmod(os.path.join(store, "0.conf"), 0o000)
        listed = rb.list()
        check(len(listed) == 1 and "cannot be read" in listed[0][2],
              f"'show rollback' lists it and says why: {listed}")
        try:
            rb.load(0)
            raised = "nothing"
        except hexash.RollbackUnavailable as e:
            raised = "RollbackUnavailable"
        except OSError as e:
            raised = type(e).__name__
        check(raised == "RollbackUnavailable",
              f"and loading it is an error the VTY words, not a traceback: {raised}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------- #186
#
# On every --reload, frr-reload writes to two directories that belong to FRR:
# --logfile (/var/log/frr, drwxr-x--- frr:frr from the package) and --rundir
# (/var/run/frr, 0710 frr:frr from frrcommon.sh), where it stages the delta for
# vtysh. Group frr may read the first and traverse the second and write
# neither, so a non-root administrator could check a routing change and not
# apply one.

print("\n=== frr-reload only writes where the account running it can ===")
FAKE = '''import os, sys
argv = sys.argv[1:]
with open(os.environ["HEXA_TEST_ARGV"], "w") as f:
    f.write("\\n".join(argv))
# What FRR 10.7.1 does on --reload, in the order it does it: the log file is
# opened whatever --stdout says, and the delta is staged under --rundir. An
# unwritable path fails here and the reload never happens.
if "--reload" in argv:
    lf = (argv[argv.index("--logfile") + 1] if "--logfile" in argv
          else "/var/log/frr/frr-reload.log")
    open(lf, "a").close()
    rd = (argv[argv.index("--rundir") + 1] if "--rundir" in argv
          else "/var/run/frr")
    open(os.path.join(rd, "reload-ABC123.txt"), "w").close()
print("nothing to do")
'''
d = tempfile.mkdtemp()
try:
    fake = os.path.join(d, "frr-reload.py")
    with open(fake, "w") as f:
        f.write(FAKE)
    seen = os.path.join(d, "argv")
    os.environ["HEXA_TEST_ARGV"] = seen
    hexash.FRR_RELOAD = fake
    frr = hexash.Frr()
    frr.available = lambda: True
    rc, out = frr.reload(["hostname test"])
    argv = open(seen).read().split("\n") if os.path.exists(seen) else []
    def named(opt):
        return argv[argv.index(opt) + 1] if opt in argv else None

    log, rundir = named("--logfile"), named("--rundir")
    print(f"  frr-reload was given: {' '.join(argv)}")
    # One assertion per path, and each says all three halves at once: the path
    # is named, it is not the one FRR owns, and the account that ran frr-reload
    # got through the write - rc is 0 only if both opens succeeded.
    check(log is not None and not log.startswith("/var/log/frr") and rc == 0,
          f"--reload logs somewhere of ours: {log!r}, rc={rc}")
    check(rundir is not None and not rundir.startswith("/var/run/frr")
          and rc == 0,
          f"and stages the delta somewhere of ours: {rundir!r}, rc={rc}")
    # The candidate and the log live together in a directory of ours, so
    # neither is left behind and neither can be a symlink somebody planted -
    # this runs as root at boot.
    conf = [a for a in argv if a.endswith(".conf")]
    check(bool(conf) and not os.path.exists(os.path.dirname(conf[0])),
          "in a directory of ours that is gone afterwards")
finally:
    os.environ.pop("HEXA_TEST_ARGV", None)
    shutil.rmtree(d, ignore_errors=True)

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
