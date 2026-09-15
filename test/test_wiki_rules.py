#!/usr/bin/env python3
"""
The rules the PUBLIC wiki is written under, with something that reads them.

`wiki/` is a mirror of <https://hexarouting.hexanetworks.com.br>. It is on the
open internet and it is written for somebody operating a box - a customer, a
partner, an integrator. `PADRAO-DOCUMENTACAO.md` section 0 says what may not
appear on such a page and gives the reason for each: an issue number and a
GitHub link belong to the issue, a source file name belongs to the code, and a
command that drops out of the VTY into the dataplane's own shell contradicts
the argument this product is sold on - that the operator never has to.

UNTIL NOW NOTHING READ THAT RULE. It was four sentences in a standard nobody
runs, and the sweep of 2026-09-15 found four pages naming the dataplane's text
CLI: two teaching it as an alternative to a `show` command, one as a
diagnostic trap to know about, one in the architecture sketch. None of them
was added maliciously and all four read as helpful, which is the whole
difficulty: a rule with no reader is a rule that erodes one reasonable-looking
sentence at a time.

WHOLE PAGES, FENCES INCLUDED, and that was measured rather than assumed. The
first version of this file exempted the fenced blocks, on the argument that a
page MUST quote what the box prints verbatim and what the box prints is the
box's business - `appliance/hexarouting-install` really does say "see
docs/logging.md" at the connection-records question, which is a defect in the
installer and not in the page that quotes it honestly. Measured over all 32
pages: with the fences read too, nothing here goes red. That installer line is
not backticked, and a backtick is what `doc_paths.repo_paths` looks for, so
the exemption was protecting a case that does not exist - and the check
written to prove the exemption worked could not fail, because its own sample
had no backticks in it either. An exemption with a vacuous guard under it is
two defects, so both are gone. The day a box message really does print a
backticked path into this tree, this file goes red and the repair is in the
message.

`wiki/README.md` describes the mirror and is never published -
`tools/wiki-status.sh` excludes it by name and so does this.
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doc_paths                                                # noqa: E402

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
WIKI = os.path.join(REPO, "wiki")

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def pages():
    """Every page that gets published, newest state on disk.

    Sorted so a failing run names the same page first every time: a report
    whose order depends on the filesystem is one nobody can diff.
    """
    out = []
    for dirpath, _dirs, files in os.walk(WIKI):
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, REPO).replace(os.sep, "/")
            if rel == "wiki/README.md":
                continue
            out.append((rel, io.open(path, encoding="utf-8").read()))
    return sorted(out)


PAGES = pages()
check(len(PAGES) >= 30,
      f"only {len(PAGES)} page(s) were read out of {WIKI}, so whatever this "
      f"file reports it is not reporting on the wiki")
print(f"=== {len(PAGES)} published page(s) ===")

# --- the dataplane's own shell --------------------------------------------
#
# Nothing the box prints contains this name - measured, zero occurrences in
# src/ - so a fenced block carrying it is a transcript nobody produced by
# running anything, and prose carrying it is the page teaching the way down.
print("\n=== no page sends an operator to the dataplane's text CLI ===")
_named = [rel for rel, text in PAGES if "vppctl" in text]
check(not _named,
      f"these pages name vppctl: {_named}. The product exists so that the "
      f"operator does not leave the VTY, and a public page that teaches the "
      f"way down says the opposite in the place a customer reads it")
print(f"  checked {len(PAGES)} page(s)")

# --- the issue tracker ------------------------------------------------------
#
# The tracker is private to the people building this; a page that cites it
# sends a reader to a door that does not open. The needle is '#' followed by
# digits, which a quoted configuration comment could in principle carry - so
# it is measured rather than assumed, and today no page has one.
print("\n=== no page cites the issue tracker ===")
_ISSUE = re.compile(r"github\.com|(?<![\w&])#\d{1,4}\b|\bissue #?\d+", re.I)
_cited = []
for rel, text in PAGES:
    hits = sorted({m.group(0) for m in _ISSUE.finditer(text)})
    if hits:
        _cited.append((rel, hits))
check(not _cited,
      f"these pages cite the tracker or GitHub: {_cited}. The reasoning "
      f"belongs on the page in its own words; the issue number belongs to "
      f"the issue")
print(f"  checked {len(PAGES)} page(s)")

# --- this repository's own files -------------------------------------------
#
# doc_paths.repo_paths is the holder of "what is a path into this repository",
# and it is reused rather than restated: test/test_doc_commands.py asks the
# same question of docs/ and the two disagreeing is how the last copy of this
# rule went wrong. It matches a BACKTICKED name whose first component is one
# of this tree's own directories, which is exactly the shape prose uses when
# it tells a reader to go and read a file. A path on the appliance -
# /etc/hexarouting/tune.env - is absolute and never matches, which is right:
# that one IS the operator's file. It also requires an EXTENSION, so a script
# named without one - `appliance/selftest` - is outside what this can see.
# Inherited from the holder deliberately: widening it here would be the second
# copy of that rule this repository has already had two of.
print("\n=== no page names a file in this repository ===")
_sent = []
for rel, text in PAGES:
    found = doc_paths.repo_paths(text)
    if found:
        _sent.append((rel, sorted(set(found))))
check(not _sent,
      f"these pages name a source file: {_sent}. The reader has the product, "
      f"not the tree it is built from")
print(f"  checked {len(PAGES)} page(s) against "
      f"{len(doc_paths.OWN_DIRS)} own director(ies)")

# --- and the rule itself has not been quietly emptied ----------------------
#
# Every check above is "this list is empty", and a list that is empty because
# nothing was read is the same green. The three readers are driven here over a
# page that breaks all three, so a run that reports nothing has been seen to
# be capable of reporting something.
print("\n=== the three readers are driven over a page that breaks them ===")
_BAD = ("Rode `vppctil` na caixa.\n"      # spelled so this file has one
        "Veja o #123 no github.com/x/y.\n"
        "Está em `src/hexash/hexash.py`.\n").replace("vppctil", "vppctl")
check("vppctl" in _BAD, "the sample page does not carry the name")
check(bool(_ISSUE.findall(_BAD)),
      f"the tracker reader matches nothing in a page that cites it twice: "
      f"{_BAD!r}")
check(doc_paths.repo_paths(_BAD) == ["src/hexash/hexash.py"],
      f"the path reader does not see a source file named on a page: "
      f"{doc_paths.repo_paths(_BAD)!r}")
print("  each of the three readers sees the page that breaks it")

print()
if fail:
    print(f"{fail} FAILURES")
    sys.exit(1)
print("ALL TESTS PASSED")
