#!/usr/bin/env python3
"""
Every recipe this Makefile hands to a shell can be parsed by that shell.

`$(call ISO_RUN,<body>)` splits its argument on the first unescaped COMMA,
and `test-install`'s body had one in an ordinary English sentence:

    echo "        refused login, so this lab could run its whole session"

so the argument ended at `refused login`, inside a quoted string, and the
expanded recipe was:

    ... sh -c 'set -e; ...; echo "        refused login'
    /bin/sh: 1: Syntax error: Unterminated quoted string

`make test-install` therefore could not run at all - and `tools/release-gate.sh`
calls it, so the gate reported "the medium installed and the installed box
failed its checks" for a release whose medium had never been installed. The
message named the box. The fault was a comma in a Makefile (#281).

Nothing could have caught it earlier than a release. The recipe only runs
after an ISO has been built, which takes half an hour, so the syntax error was
never reached by anything anyone runs day to day - and a syntax error in a
shell command is found when the shell reads it, not when the work it does
fails.

So this asks the shell. `make -n` expands the recipe without running it, and
`sh -n` parses it without running it either, which is the whole check: no
container, no ISO, no dataplane.
"""
import os
import re
import subprocess
import sys
import tempfile

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAKEFILE = open(os.path.join(REPO, "Makefile"), encoding="utf-8").read()

# The targets, read off the Makefile rather than listed here: a target added
# later is covered without anybody remembering this file exists, which is the
# difference between a check and a check that was true once.
targets = sorted(set(re.findall(r"^([A-Za-z0-9_-]+):[^=\n]*$", MAKEFILE,
                                re.M)))
# `clean` and `iso` have no shell quoting worth the ten seconds, but running
# them costs nothing and excluding anything by hand is how a list goes stale.
print(f"=== every recipe expands to something a shell can parse ===")

checked = 0
for t in targets:
    p = subprocess.run(["make", "-n", t], cwd=REPO,
                       capture_output=True, text=True)
    if p.returncode != 0:
        # `make -n` refusing is its own answer and not this file's business -
        # a missing prerequisite, for instance. What matters is that nothing
        # is silently skipped, so it is reported rather than passed over.
        print(f"  [note] make -n {t}: {p.stderr.strip().splitlines()[-1] if p.stderr.strip() else 'refused'}")
        continue
    body = p.stdout
    if not body.strip():
        continue
    checked += 1
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write(body)
        path = fh.name
    try:
        q = subprocess.run(["sh", "-n", path], capture_output=True, text=True)
        check(q.returncode == 0,
              f"the recipe for '{t}' is not something a shell can parse, so "
              f"the target cannot run at all: "
              f"{(q.stderr.strip() or 'sh -n failed').splitlines()[-1]}. "
              f"$(call ISO_RUN,...) ends its argument at the first unescaped "
              f"comma, and a comma in an English sentence truncates the body "
              f"mid-string; put the body in a script under tools/ rather than "
              f"escaping the comma")
    finally:
        os.unlink(path)

if checked:
    ok(f"{checked} recipe(s)")

# And the check is reading something. Without this it passes on a Makefile
# whose targets this file can no longer find, which is the shape #151 spent an
# issue on.
check(checked >= 5,
      f"only {checked} recipe(s) were expanded, so this file is asserting "
      f"almost nothing. The target regex no longer matches this Makefile")

print()
print("=== a harness rebuilds when the file it tests changes ===")
# FOUND BY A MUTATION THAT STAYED GREEN. test/test_bpa_identity.c includes
# bpa_identity_table.c, and that file was not a prerequisite of the binary -
# so editing the file under test left the target "up to date" and the suite
# measured the build from before the change. Three mutations in a row came
# back green, which is the worst answer a mutation can give: it reads as the
# code being right.
#
# The rule is mechanical, so it is read mechanically: whatever a harness
# INCLUDES as a .c has to be named in the recipe that builds it.
import glob                                                  # noqa: E402

rules = dict(re.findall(r"^\$\(BUILD\)/(test_[a-z_0-9]+):"
                        r"((?:[^\n]*\\\n)*[^\n]*)", MAKEFILE, re.M))
missing = []
n_harness = 0
for f in sorted(glob.glob(os.path.join(REPO, "test", "test_*.c"))):
    src = open(f, encoding="utf-8").read()
    incs = re.findall(r'#include "(?:\.\./src/plugins/bpa/)?(bpa_[a-z_0-9]+\.c)"',
                      src)
    if not incs:
        continue
    n_harness += 1
    base = os.path.basename(f)
    tgt = [t for t, b in rules.items() if base in b]
    if not tgt:
        missing.append(f"{base}: no recipe in the Makefile names it")
        continue
    body = rules[tgt[0]]
    for inc in incs:
        if inc not in body:
            missing.append(f"{base} includes {inc} and the recipe for "
                           f"{tgt[0]} does not name it")
check(not missing,
      "these harnesses do not rebuild when the file they test changes, so a "
      "change to that file is measured against the binary from before it: "
      + "; ".join(missing))
check(n_harness >= 10,
      f"only {n_harness} harness(es) include a .c, so this check is reading "
      f"almost nothing")
if not missing:
    ok(f"{n_harness} harness(es), each naming the source it includes")

if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
