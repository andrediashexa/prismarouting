#!/usr/bin/env python3
"""
Every repository path named in docs/context.md exists.

A map that names files which are no longer there is worse than no map: it is
confidently wrong, and a reader who follows it loses more time than one who
started from `ls`. That is the half of a context document that rots silently -
the prose goes stale visibly, in the sense that somebody reading it can tell,
while a moved file leaves a link that looks exactly as authoritative as it did
the day it was written.

This checks the half a program can check. Nothing can check the other half.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "context.md")

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


text = open(DOC).read()

# Paths appear two ways: as `code spans` and as markdown links. Both are
# followed by a reader, so both are checked.
spans = re.findall(r"`([^`]+)`", text)
links = re.findall(r"\]\(([^)]+)\)", text)

# A code span is a path when it looks like one. `commit remap` and
# `Pool.lines(in_place=)` are prose about the code, not references to a file.
def is_path(s):
    if s.startswith(("http://", "https://", "#")):
        return False
    if " " in s:
        return False
    return "/" in s or re.search(r"\.(c|h|py|sh|md|api)$", s)


candidates = []
for s in spans:
    if is_path(s):
        candidates.append((s, s))
for l in links:
    if l.startswith(("http://", "https://", "#")):
        continue
    # Links in docs/context.md are relative to docs/.
    candidates.append((l, os.path.join("docs", l.split("#", 1)[0])))

check(len(candidates) > 30,
      f"only {len(candidates)} paths found; the extraction is probably broken, "
      "which would make this test pass by finding nothing")

# Prose names a file the way a person says it - `bpa_pool.h`, not the full
# path - and forcing every mention to be fully qualified would make the
# document worse to read in order to make it easier to check. So a bare
# basename is resolved by looking for it in the tree: it still fails when the
# file is deleted or renamed, which is what this test is for.
index = {}
for base, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs
               if d not in (".git", ".build", ".build-vpp", "__pycache__")]
    for f in files:
        index.setdefault(f, os.path.join(base, f))

missing = []
for shown, rel in candidates:
    p = os.path.normpath(os.path.join(ROOT, rel))
    if os.path.exists(p):
        continue
    if "/" not in shown and shown in index:
        continue
    missing.append(shown)

check(not missing,
      "docs/context.md names paths that do not exist: " + ", ".join(
          sorted(set(missing))))

print(f"  {len(set(p for p, _ in candidates))} distinct paths named, "
      f"{len(set(missing))} missing")

# The document exists to be reached. A page nobody links to is a page nobody
# reads, and this one is worth nothing unless it is the first thing found.
readme = open(os.path.join(ROOT, "README.md")).read()
check("docs/context.md" in readme,
      "README.md must link to docs/context.md, or the map is unreachable")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
