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
    # `.build-old` for the reason test_doc_copies.py gives at its own walk: it
    # is a released tree extracted by lab/upgrade.sh, so a file name indexed
    # from there resolves to another release's copy of it.
    dirs[:] = [d for d in dirs
               if d not in (".git", ".build", ".build-vpp", ".build-old",
                            "__pycache__")]
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

# How many invariants the page says it has, against how many it has.
#
# Section 5 of that page says this test "does not check that the prose is still
# true - nothing can. What it catches is the half that rots silently." A count
# of the file's own subsections is not prose: it is arithmetic over the
# headings, so it is that half, and it was not being caught. Measured on the
# day this was written, the page said "Eleven invariants" in its TL;DR, "The
# nine things" in the heading eleven lines below, and the README said "the nine
# facts" - with 1.10 and 1.11 sitting under a heading that excluded them (#232).
#
# Written in words rather than digits throughout the prose, so the words are
# what is read.
WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
         7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
         12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen"}

n = len(re.findall(r"^### 1\.\d+ ", text, re.M))
word = WORDS.get(n)
check(n >= 5,
      f"only {n} invariant heading(s) matched '### 1.N', so the count below "
      f"is comparing nothing")
if word:
    tldr = re.search(r"^(\w+) invariants,", text, re.M)
    check(tldr is not None and tldr.group(1).lower() == word,
          f"docs/context.md has {n} invariants and its TL;DR says "
          f"{tldr.group(1) if tldr else '<no count at all>'}")
    head = re.search(r"^## 1\. The (\w+) things", text, re.M)
    check(head is not None and head.group(1).lower() == word,
          f"docs/context.md has {n} invariants and the heading over them says "
          f"{head.group(1) if head else '<no count at all>'}. The ones past "
          f"that number sit under a heading that excludes them")
    rd = re.search(r"the (\w+) facts that make", readme)
    check(rd is not None and rd.group(1).lower() == word,
          f"docs/context.md has {n} invariants and README.md sends the reader "
          f"to {rd.group(1) if rd else '<no count at all>'} of them")
    if not fail:
        print(f"  {n} invariants, and the page, its heading and the README all "
              f"say {word}")
else:
    check(False, f"there are {n} invariants and this check has no word for "
                 f"that number, so it cannot read the prose")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
