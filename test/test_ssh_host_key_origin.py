#!/usr/bin/env python3
"""
Does this box tell the operator it is answering with somebody else's ssh host
key, and does it tell only the boxes that are (#248)?

WHAT WAS ALREADY THERE AND WHAT WAS NOT. Every medium this project published
up to and including 0.66.0 carried the host keys its build container made,
private halves included, so every box installed from one of them answers with
the same key and the private half of it is in the download. #245 fixed the
image and the installer, which fixes boxes installed from now on. The boot
self-test learned to recognise a handed-down key and say so, which is the
finding. It writes that finding to /dev/console - `appliance/selftest`'s say()
falls back to stdout only when the write to the console FAILS, and the unit
sends stdout to the journal, so on a box whose console write succeeds the
journal gets nothing and `journalctl` cannot bring the report back either.

A racked appliance is reached over ssh. The operator sitting in that session
is exactly the operator whose session can be sat in the middle of, and until
this the box had no way to tell them: the sentence existed and was printed
once, at boot, onto a serial port nobody is attached to.

So the VTY banner is the second reader, and a second reader is the thing this
repository has been bitten by four times (tools/selftest-verdict.sh's header
counts them). The rule, the key names and the three commands that repair it
therefore live in ONE file, `appliance/ssh-host-key-origin`, and both the boot
report and the banner render what it answers. This suite holds that: the
holder's own contract, the banner that reads it, the silence on a box that is
not affected, and the refusal of a second copy of the commands.

THE FIXTURE NEVER WRITES A KEY. The whole rule is two numbers `stat` answers,
so a case here is a pair of numbers and a `stat` that answers them. That keeps
the filesystem the temporary directory happens to be on - one that records a
birth time, or one that does not - from deciding a case instead of the
fixture, and it keeps key material out of the tree, which is what #245 was
about in the first place.
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src",
                                "hexash"))

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HOLDER = os.path.join(REPO, "appliance", "ssh-host-key-origin")

fail = 0


def ok(msg):
    print(f"  {msg}")


def bad(msg):
    global fail
    fail += 1
    print(f"  FAIL: {msg}")


# --- a box, as two numbers per key ------------------------------------------

WORK = tempfile.mkdtemp()
BIN = os.path.join(WORK, "bin")
os.makedirs(BIN)
KEYTIMES = os.path.join(WORK, "keytimes")

# stat, for the host keys only; everything else goes through to the real one.
# A key with no row in the fixture is a key stat could not answer for, which is
# one of the cases below.
with open(os.path.join(BIN, "stat"), "w") as fh:
    fh.write(f'''#!/bin/sh
for a in "$@"; do p=$a; done
case "$p" in
  */etc/ssh/ssh_host_*_key)
    if [ -f "{KEYTIMES}" ]; then
      sed -n "s|^${{p##*/}} ||p" "{KEYTIMES}"
      exit 0
    fi ;;
esac
exec /usr/bin/stat "$@"
''')
os.chmod(os.path.join(BIN, "stat"), 0o755)

ROOT = os.path.join(WORK, "root")

# mtime 1757349810 is the published 0.37.0 medium's own: its host keys are
# stamped 2026-09-08 13:43, three minutes before its squashfs was made. The
# birth time is the install, six days later.
FROM_IMAGE = 1757349810
INSTALLED = 1789000000


def box(times):
    """A fake /etc/ssh. `times` maps a key name to (mtime, birth), or None."""
    keydir = os.path.join(ROOT, "etc", "ssh")
    if os.path.isdir(keydir):
        for f in os.listdir(keydir):
            os.unlink(os.path.join(keydir, f))
    os.makedirs(keydir, exist_ok=True)
    rows = []
    for name, t in times.items():
        open(os.path.join(keydir, name), "w").close()
        if t is not None:
            rows.append(f"{name} {t[0]} {t[1]}\n")
    with open(KEYTIMES, "w") as fh:
        fh.writelines(rows)


def ask(root=ROOT):
    """Run the holder over that box and return its rows as a dict."""
    env = dict(os.environ, PATH=BIN + os.pathsep + os.environ["PATH"])
    out = subprocess.run([HOLDER, root], stdout=subprocess.PIPE, env=env,
                         universal_newlines=True).stdout
    answer = {"handed": [], "unreadable": [], "own": None, "fix": [],
              "generate": [], "rows": []}
    for line in out.splitlines():
        word, _, rest = line.partition(" ")
        answer["rows"].append(word)
        if word in ("fix", "generate"):
            answer[word].append(rest)
        elif word == "own":
            answer["own"] = rest
        elif word in ("handed", "unreadable"):
            answer[word] = rest.split()
    return answer


THREE = ["ssh_host_ecdsa_key", "ssh_host_ed25519_key", "ssh_host_rsa_key"]

print("=== keys that came in with the image ===")
box({k: (FROM_IMAGE, INSTALLED) for k in THREE})
a = ask()
if a["handed"] == THREE:
    ok("all three are reported as handed to this box, by name")
else:
    bad(f"a key older than the filesystem it sits on was not reported as "
        f"handed down: {a}")
if a["own"] == "0":
    ok("and none of them is counted as the box's own")
else:
    bad(f"a handed-down key was counted among the box's own: own={a['own']}")

print("\n=== keys the box made itself ===")
# THE DIRECTION THAT MATTERS MOST. A check that accuses a healthy box is worse
# than no check: it is read once, disbelieved, and then every real finding
# under it is read past. ssh-keygen creates the file and then writes it, so a
# key's own mtime is its birth time or a second past it when the write
# straddles a second boundary.
for mtime in (INSTALLED, INSTALLED + 1):
    box({k: (mtime, INSTALLED) for k in THREE})
    a = ask()
    if a["handed"]:
        bad(f"a box whose keys are its own was accused (mtime {mtime}): "
            f"{a['handed']}")
    elif a["own"] != "3":
        bad(f"its own keys were not counted as its own (mtime {mtime}): "
            f"own={a['own']}")
    else:
        ok(f"not accused, and counted as its own (mtime {mtime}, birth "
           f"{INSTALLED})")

print("\n=== one key of three came in with the image ===")
# `ssh-keygen -A` makes only the types that are MISSING, so a key left behind
# by a partial cleanup survives one. The box is still impersonable over
# whichever type the client picks.
mixed = {k: (INSTALLED, INSTALLED) for k in THREE}
mixed["ssh_host_rsa_key"] = (FROM_IMAGE, INSTALLED)
box(mixed)
a = ask()
if a["handed"] == ["ssh_host_rsa_key"] and a["own"] == "2":
    ok("the one key is named and the other two are still its own")
else:
    bad(f"a mixed box was not reported key by key: {a}")

print("\n=== a key whose timestamps cannot be had ===")
# Two ways in: a filesystem that records no birth time, where stat answers 0,
# and a file stat could not answer for at all. Neither is a pass. A check that
# cannot see must not read the same as a check that agreed.
box({k: (INSTALLED, 0) for k in THREE})
a = ask()
if a["unreadable"] == THREE and a["own"] == "0":
    ok("no birth time is reported as unreadable, not as the box's own")
else:
    bad(f"a key with no birth time was not reported as unreadable: {a}")
silent = {k: (INSTALLED, INSTALLED) for k in THREE}
silent["ssh_host_rsa_key"] = None
box(silent)
a = ask()
if a["unreadable"] == ["ssh_host_rsa_key"] and a["own"] == "2":
    ok("and a key stat answers nothing for is reported the same way")
else:
    bad(f"a key stat could not answer for was not reported as unreadable: {a}")

print("\n=== a box with no host keys at all ===")
# Every row is printed even when its set is empty, so that a reader can tell
# "nothing was handed to this box" from "the rule never ran". The self-test
# fails a box with no keys for a different reason - sshd will not start - and
# that branch is only reachable because `own 0` arrives rather than nothing.
box({})
a = ask()
# Read off the ROWS and not off the parsed sets: a reader that fills an empty
# list in when a row is absent cannot tell a row that said "nothing" from a
# row that was never printed, and that is the whole distinction the self-test
# uses to decide between "this box has no host keys" and "nothing here could
# be measured".
if (a["rows"].count("handed") == 1 and a["rows"].count("unreadable") == 1
        and a["own"] == "0" and a["handed"] == [] and a["unreadable"] == []):
    ok("every row is still answered, with nothing after the word")
else:
    bad(f"a box with no keys did not answer every row: rows={a['rows']}")

print("\n=== the repair, which is the holder's other half ===")
FIXES = a["fix"]
if FIXES == ["rm /etc/ssh/ssh_host_*_key /etc/ssh/ssh_host_*_key.pub",
             "ssh-keygen -A",
             "systemctl restart ssh"]:
    ok("three commands, in order, typeable as they stand")
else:
    bad(f"the holder does not give the repair as typeable commands: {FIXES}")
# The other repair, for the box that has no keys at all. Its sshd will not
# start, so it is reachable at its console and nowhere else, and there is
# nothing to remove: an operator typing a removal into that console is
# deleting from a box they cannot get back into.
if a["generate"] == ["ssh-keygen -A", "systemctl restart ssh"]:
    ok("and the shorter one for a box with no keys, which removes nothing")
else:
    bad(f"the holder does not give the no-keys repair, so the other reader "
        f"has to write it out itself: {a['generate']}")

print("\n=== and none of it is decided on the key's comment ===")
# The measurement behind the choice, kept as an assertion because the comment
# is the obvious signal and reads as the right one. `chroot "$MNT" ssh-keygen
# -A` stamps the LIVE MEDIUM's hostname into a key generated on the target,
# because gethostname() is not a filesystem property: a correctly installed
# box named "cgnat-rj-01" carries "root@hexarouting", measured. Reading the
# comment would report it, with hexarouting-install manufacturing the
# evidence. A list of published fingerprints was rejected for a second reason
# on top of that one: it is key material back in the tree, which is what #245
# removed.
#
# Comments and printed rows are dropped: the repair the holder PRINTS names
# the .pub files, because an operator has to delete those too. What must not
# appear is a .pub, a fingerprint or a hostname being READ to decide.
reads = []
for n, line in enumerate(open(HOLDER).read().splitlines(), 1):
    s = line.strip()
    if s.startswith("#") or s.startswith("echo "):
        continue
    if any(w in s for w in (".pub", "ssh-keygen -l", "/etc/hostname",
                            "gethostname", "hostname")):
        reads.append(f"{n}: {s}")
if not reads:
    ok("no .pub, no fingerprint and no hostname is read to decide it")
else:
    bad("it decides on the key comment or the hostname, which a correctly "
        "installed box does not match: " + "; ".join(reads))

# --- the second reader: the VTY an operator reaches this box through --------

import hexash                                                   # noqa: E402

# A wrapper, because the holder takes the root as an argument and the VTY
# calls it with none - a real box's root is /. The wrapper is also where the
# stubbed stat goes on the path, so the answer comes from the fixture rather
# than from whatever filesystem the temporary directory is on.
WRAPPER = os.path.join(WORK, "origin-over-the-fixture")
with open(WRAPPER, "w") as fh:
    fh.write(f'#!/bin/sh\nPATH="{BIN}:$PATH" exec "{HOLDER}" "{ROOT}"\n')
os.chmod(WRAPPER, 0o755)
hexash.SSH_HOST_KEY_ORIGIN = WRAPPER


def login():
    """What a login draws, which is the banner and nothing after it."""
    sh = hexash.Shell()
    sh.feed = lambda line: False
    buf = io.StringIO()
    real = sys.stdin
    try:
        sys.stdin = type("T", (), {"isatty": lambda s: True,
                                   "readline": lambda s: ""})()
        with contextlib.redirect_stdout(buf):
            sh.repl()
    finally:
        sys.stdin = real
    return buf.getvalue()


print("\n=== an operator logging in to an affected box is told ===")
box({k: (FROM_IMAGE, INSTALLED) for k in THREE})
said = login()
if "ssh host keys were not generated on it" in said:
    ok("the banner says the keys were not made on this box")
else:
    bad(f"a login to an affected box said nothing about its host keys: "
        f"{said[-400:]!r}")
if all(k in said for k in THREE):
    ok("and it names the key files, so the operator knows which to delete")
else:
    bad("it did not name the keys the box was handed")
flat = " ".join(said.split())
if "private half of it is in the published download" in flat:
    ok("and says the private half is in the download")
else:
    bad("it did not say the private half is published")
if ("This ssh session can be impersonated and your client will not object" in
        flat):
    ok("and that THIS session can be impersonated with nothing to notice")
else:
    bad("it did not say the session being read in can be impersonated")
rows = [ln.strip() for ln in said.splitlines()]
if all(c in rows for c in FIXES):
    ok("and gives the three commands that fix it, one per row")
else:
    bad(f"the banner did not give the repair as typeable rows: "
        f"{[r for r in rows if 'ssh' in r]}")
if "refuse to connect and report that the host key changed" in flat:
    ok("and warns that every client pinning the old fingerprint will object")
else:
    bad("it did not warn that clients will refuse the next connection")
# THE WIDTH, because this screen is read on the 80-column serial console of a
# box somebody is standing next to, and it was over it: three key types hung
# off the end of the headline measured 106 columns. Wrapping by eye cannot be
# re-measured after an edit (#252), so it is measured here instead.
block = said.splitlines()
block = block[next(i for i, ln in enumerate(block)
                   if ln.startswith("This box's ssh host keys")):]
wide = [ln for ln in block if len(ln) > 79]
if not wide:
    ok(f"and every row of it fits the console: widest "
       f"{max(len(ln) for ln in block)} columns")
else:
    bad(f"{len(wide)} row(s) run past the 79-column console: "
        + "; ".join(f"{len(ln)}: {ln}" for ln in wide))

print("\n=== and a box whose keys are its own is told nothing ===")
# THE DIRECTION A BANNER GETS WRONG. A line drawn on every login of every
# healthy box is a line read once and skipped for ever, including on the box
# where it means something - and a box remediated by hand must stop being
# warned at the very next login, which it does because the answer is measured
# per login rather than remembered from the boot before it.
box({k: (INSTALLED, INSTALLED) for k in THREE})
said = login()
if "host key" in said.lower():
    bad(f"a box whose keys are its own was warned at login: {said[-400:]!r}")
else:
    ok("nothing about host keys is drawn on a box that made its own")

print("\n=== a banner that cannot ask says nothing, rather than guessing ===")
# The self-test does the opposite with the same failure, deliberately: its job
# is to report on the box, so a check it could not run is a finding. A banner
# that announced a weakness it had not measured would be teaching operators to
# read past it.
hexash.SSH_HOST_KEY_ORIGIN = os.path.join(WORK, "no-such-program")
box({k: (FROM_IMAGE, INSTALLED) for k in THREE})
said = login()
hexash.SSH_HOST_KEY_ORIGIN = WRAPPER
if "host key" in said.lower():
    bad(f"the banner accused a box it could not measure: {said[-400:]!r}")
else:
    ok("no holder, no claim")

print("\n=== the commands are written in one place and read from it ===")
# The shape tools/selftest-verdict.sh exists to end. Three files print these
# rows on two different screens; a file that spells one itself is a file that
# can go on printing the old repair after the holder's is corrected.
spelled = []
seen = set()
for rel in ("appliance/selftest", "src/hexash/hexash.py"):
    for n, line in enumerate(open(os.path.join(REPO, rel)).read().splitlines(),
                             1):
        s = line.strip()
        if s.startswith("#"):
            continue
        for c in FIXES + a["generate"]:
            if c in s and f"{rel}:{n}" not in seen:
                seen.add(f"{rel}:{n}")
                spelled.append(f"{rel}:{n}: {s}")
if not spelled:
    ok("neither reader spells the repair: both render what the holder gives")
else:
    bad("a reader spells the repair instead of rendering the holder's: "
        + "; ".join(spelled))

print("\n=== and the holder reaches the box ===")
# A banner pointing at a file the installer does not put on the disk is a
# banner that says nothing on every box, and the self-test beside it would
# report that it could not ask. The installer has no list - a file is in the
# manifest because the line that installs it RAN - so the line is what there
# is to check.
installed = [ln for ln in open(os.path.join(REPO, "appliance/install.sh"))
             if "ssh-host-key-origin" in ln and not ln.strip().startswith("#")]
if any("/usr/lib/hexarouting/ssh-host-key-origin" in ln for ln in installed):
    ok("appliance/install.sh puts it in /usr/lib/hexarouting")
else:
    bad("nothing installs appliance/ssh-host-key-origin, so the banner and "
        "the boot report would both be pointing at a file the box lacks")

if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
