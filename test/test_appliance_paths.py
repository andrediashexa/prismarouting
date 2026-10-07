#!/usr/bin/env python3
"""
Paths the APPLIANCE prints, against the box an operator is holding.

`test/doc_paths.py` answers a different question: which backticked string in a
document is a path into THIS repository. The scripts under `appliance/` print
to somebody who has the box and not the repository, so a path in their output
has to exist on the box - and `docs/` does not exist on a box this product
installs.

That is [#348](https://github.com/andrediashexa/hexarouting/issues/348): the
installer's connection-records question said "see docs/logging.md" while
`appliance/install.sh` puts that file under `/usr/share/doc/prisma-routing/`. The
public installation page quotes that screen verbatim, because the rule there is
that box output is what the box prints, so the path an operator cannot follow
reached a reader who had not installed anything yet.

TWO RULES, and neither needs an allowlist:

  1. No appliance script prints a repository-relative path. The test is the
     first component being a top-level directory of this repository, which is
     the rule doc_paths.py already established and measured - it replaced an
     allowlist rather than being a shortcut past one.
  2. A path under /usr/share/doc/prisma-routing/ must be a file install.sh puts
     there. That list is read out of install.sh rather than copied here, so a
     document added or dropped moves both sides at once.
"""
import os
import re
import sys

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPLIANCE = os.path.join(REPO, "appliance")

#: This repository's own top-level directories, which is what makes a string
#: repository-relative rather than a path on a box. Read from the tree, so a
#: directory added tomorrow is covered without anybody remembering this file.
TOPS = sorted(d for d in os.listdir(REPO)
              if os.path.isdir(os.path.join(REPO, d)) and not d.startswith("."))

#: Only what is PRINTED. A comment naming docs/logging.md is a note to whoever
#: reads the source, and they do have the repository; the defect is a path
#: reaching the screen. Both `echo` and python's print are here because the
#: appliance is half shell and half python.
_SAYS = re.compile(r"""(?:^\s*(?:echo|say|warn|print|die)\b|print\s*\()""")
_PATH = re.compile(r"(?<![\w.-])(/?(?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_.-]+\.[a-z]+)")

#: THE SCRIPTS THAT RUN ON THE BOX, and the list is install.sh's own.
#:
#: appliance/ holds both halves of this product: the files that go onto a box
#: and the files that build one. build-iso.sh, build-upgrade.sh and install.sh
#: itself run in a checkout, by somebody who HAS the repository, and a
#: repository-relative path printed by those is right. An allowlist here would
#: be a second list to keep true, so the question is asked of install.sh: a
#: script it copies into $DESTDIR is a script that runs where docs/ does not
#: exist.
_INST = open(os.path.join(APPLIANCE, "install.sh"), encoding="utf-8").read()
_ON_BOX = sorted(set(re.findall(r"\$REPO/appliance/([A-Za-z0-9_-]+)\"?\s",
                                _INST)))
check(len(_ON_BOX) > 5,
      f"install.sh was read as putting {len(_ON_BOX)} appliance script(s) on "
      f"the box, which is too few to be the real list: {_ON_BOX}")

scripts = []
for name in sorted(os.listdir(APPLIANCE)):
    p = os.path.join(APPLIANCE, name)
    if os.path.isfile(p) and name in _ON_BOX:
        scripts.append((name, p))
check(len(scripts) > 5,
      f"only {len(scripts)} of the scripts install.sh puts on a box were "
      f"found under appliance/, so whatever this reports it is not reporting "
      f"on the appliance")

printed = []      # (script, line number, path)
for name, p in scripts:
    for i, line in enumerate(open(p, encoding="utf-8",
                                  errors="replace").read().splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        if not _SAYS.search(line):
            continue
        for m in _PATH.finditer(line):
            printed.append((name, i, m.group(1)))

check(printed, "no printed path was found in any appliance script, so the "
               "two rules below are being applied to an empty list")
print(f"  {len(scripts)} script(s) that run on a box, "
      f"{len(printed)} printed path(s)")

print()
print("=== no appliance script sends an operator into the source tree ===")
_repo_rel = [(n, i, q) for n, i, q in printed if q.split("/")[0] in TOPS]
check(not _repo_rel,
      f"these are printed to somebody holding the box, and name a directory "
      f"only this repository has: {_repo_rel}")
if not _repo_rel:
    ok(f"none of {len(printed)} printed paths starts with one of this "
       f"repository's {len(TOPS)} top-level directories")

print()
print("=== and a document it names is one install.sh installs ===")
# The list install.sh actually installs, read from the loop that installs it.
_ins = open(os.path.join(APPLIANCE, "install.sh"), encoding="utf-8").read()
# A hyphen too since #588 phase 2 shipped logging-events.md: the reader
# took "[a-z ]+" and stopped finding the loop at all.
_m = re.search(r"for d in ([a-z -]+); do\n\s*install -D[^\n]*"
               r"usr/share/doc/prisma-routing", _ins)
check(_m, "the loop in install.sh that installs the documents could not be "
          "read, so the list below would be empty and this check vacuous")
_installed = set(_m.group(1).split()) if _m else set()
check(_installed, "install.sh installs no document, which is either true and "
                  "a defect, or this reader is looking in the wrong place")
_named = [(n, i, q) for n, i, q in printed
          if "/usr/share/doc/prisma-routing/" in q]
_missing = [(n, i, q) for n, i, q in _named
            if os.path.basename(q)[:-3] not in _installed]
check(not _missing,
      f"these name a document under /usr/share/doc/prisma-routing that "
      f"install.sh does not put there: {_missing}, which installs "
      f"{sorted(_installed)}")
if _named and not _missing:
    ok(f"{len(_named)} path(s) under /usr/share/doc/prisma-routing, all of them "
       f"in install.sh's list: {sorted(_installed)}")
elif not _named:
    ok(f"no appliance script names a document under "
       f"/usr/share/doc/prisma-routing; install.sh installs {sorted(_installed)}")

print()
if fail:
    print(f"{fail} FAILURES")
    sys.exit(1)
print("ALL TESTS PASSED")
