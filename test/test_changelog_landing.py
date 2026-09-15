#!/usr/bin/env python3
"""
CHANGELOG.md has somewhere for the next entry to land.

Between one release and the next there must be a `## Unreleased` heading, and
it must be the newest section in the file. Empty is fine and is the ordinary
state right after a release; what must not happen is for it to be absent.

WHY THIS IS A CHECK AND NOT A CONVENTION. A branch is cut while master says
`## Unreleased` and adds its entry under that heading. By the time it merges,
the release has renamed the heading to `## 0.69.0`. Git has no `## Unreleased`
left to match, so it anchors on the nearest context it still recognises - the
`### Fixed` of whatever section is now at the top - and files the entry inside
a section that has already shipped. Cleanly, with no conflict, and nothing in
the branch was wrong.

It happened to FIVE branches in one day after 0.68.0 was cut (#280, #281, #39
into 0.68.0, #145 into 0.69.0, #163 into 0.66.0, which shipped in February).
tools/release-gate.sh caught every one, which is the only reason none shipped
- but it catches them at the tag, after the version bump and after the merge,
which is where a release stops rather than where it is cheap to fix.

Leaving the heading in place gives git the anchor it was looking for. The gate
already tolerates an empty one: its own check refuses `## Unreleased` that
still has ENTRIES under it, not one with nothing there.

This belongs in `make test` and not in the gate, because by the time the gate
runs the merges have already gone wherever they were going to go.
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


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doc_paths                                     # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEXT = open(os.path.join(REPO, "CHANGELOG.md"), encoding="utf-8").read()

print("=== there is somewhere for the next entry to land ===")

# The "## " headings that are sections rather than the file's own front
# matter. TL;DR and 5W2H are headings too and are not releases.
heads = [h for h in re.findall(r"^## (.+)$", TEXT, re.M)
         if h not in ("TL;DR", "5W2H")]
check(bool(heads), "CHANGELOG.md has no sections at all, so this file is "
                   "comparing nothing")

if heads:
    check(heads[0] == "Unreleased",
          f"the newest section is {heads[0]!r} and not 'Unreleased'. A branch "
          f"cut before the last release rename has nowhere to put its entry, "
          f"so git files it inside {heads[0]!r} - a section that has already "
          f"been tagged and describes a medium somebody is running. Leave an "
          f"empty '## Unreleased' above it")
    if heads[0] == "Unreleased":
        ok("'## Unreleased' is the newest section")

# And it must be ONE heading. Two would mean a release rename left the old one
# behind, which puts entries in the older of the two at random.
check(heads.count("Unreleased") <= 1,
      f"there are {heads.count('Unreleased')} '## Unreleased' sections, so "
      f"which one a merge lands in is whichever git matched first")

# Every other section is a version, in descending order. A section that is
# neither 'Unreleased' nor a version is a heading somebody added by hand, and
# entries anchored near it go somewhere nobody will look for them.
vers = []
for h in heads[1:]:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", h)
    check(m is not None,
          f"{h!r} is a section that is neither 'Unreleased' nor a version")
    if m:
        vers.append(tuple(int(x) for x in m.groups()))
check(vers == sorted(vers, reverse=True),
      "the released sections are not in descending order, so 'the newest "
      "section' is not the one at the top and every rule written in terms of "
      "it is about the wrong section")
if vers and vers == sorted(vers, reverse=True):
    ok(f"and the {len(vers)} released section(s) below it are newest first")

# And the check is reading something. Without this it passes on a CHANGELOG
# this file can no longer parse, which is the shape #151 spent an issue on.
check(len(heads) >= 3,
      f"only {len(heads)} section(s) were found, so this file is asserting "
      f"almost nothing. The heading pattern no longer matches this CHANGELOG")

print("\n=== the paths the unreleased entries name are still in the tree ===")
# ONLY the unreleased section, and that is the whole design.
#
# A tagged section describes a medium somebody is running and MUST NOT be
# edited, so a file renamed after 0.62.0 leaves that section pointing at a name
# the tree no longer has and there is nothing to be done about it. The section
# being WRITTEN is different: it is read by whoever cuts the release and by the
# operator who takes it, and a path in it that does not exist is either a typo
# or a rename that happened between the branch and the merge.
#
# The holder is the filesystem, which is the whole reason this is sound where
# comparing a message against the code that prints it is not: a path either
# resolves or it does not, and it cannot be reworded into passing.
#
# WHICH STRINGS COUNT AS A PATH is decided by test/doc_paths.py, which
# test/test_doc_commands.py reads too. Written out here as well, this copy went
# red on an entry naming `vnet/error.h` - VPP's header, not ours - which is
# exactly the distinction that entry was about. One holder, two readers.
_UNREL = re.search(r"^## Unreleased$(.*?)^## \d", TEXT, re.M | re.S)
if not _UNREL:
    check(False, "the unreleased section could not be sliced, so the paths in "
                 "it are not being checked at all")
else:
    _body = _UNREL.group(1)
    _named, _missing, _build = set(), [], set()
    for _rel in doc_paths.repo_paths(_body):
        _named.add(_rel)
        if os.path.exists(os.path.join(REPO, _rel)):
            continue
        # A build product is not a file that is gone, and the holder answers
        # which is which so this reader and test_doc_commands.py cannot come
        # apart about it. See doc_paths.built: the verdict used to depend on
        # whether somebody had built, and a clone is what the release gate
        # makes.
        if doc_paths.built(_rel):
            _build.add(_rel)
        else:
            _missing.append(_rel)
    for _r in sorted(set(_missing)):
        check(False, f"an unreleased entry names {_r!r} and the tree does not "
                     f"have it: a reader sent to that path finds nothing, and "
                     f"a tagged section cannot be corrected afterwards")
    for _r in sorted(_build):
        print(f"  [info] an unreleased entry names {_r}, which .gitignore "
              f"lists: the build makes it, so it is absent in a clone and "
              f"that is not a stale entry")
    if not _missing:
        if len(_named) >= 3:
            ok(f"all {len(_named)} of them")
        else:
            # An empty unreleased section is the ordinary state right after a
            # release and names nothing. Saying so is better than an [ ok ]
            # that reads as though something was checked.
            ok(f"{len(_named)} path(s) named, so there is little here to check")

print("\n=== every merged issue has an entry to be read in ===")
# The other half of "there is somewhere for it to land": that something
# actually landed. CONTRIBUTING.md's release procedure makes a CHANGELOG entry
# listing the issues the merge closes one of six MUSTs, and nothing compared
# the result against it.
#
# It has already been missed. #293 and #294 were merged into master on
# 2026-09-14 from branches that carried no entry, and the gap was found by
# reading the section rather than by anything failing. A reader of a release
# note is then told about four of the six things that changed, and the two
# they are not told about are the two nobody wrote down.
#
# The comparison is offline and uses nothing but git: a merge commit's SUBJECT
# names the issue it closes - "Merge #294: ..." - and the entry under
# `## Unreleased` links it. Merges that name no issue are not a finding: a
# chore branch legitimately closes nothing, and demanding a number would teach
# people to invent one.
#
# Deliberately compared against the merge SUBJECT and not against the tracker.
# make test has to work with no network, which is the same line
# tools/roadmap-state.sh sits on the other side of.
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(*args):
    try:
        return subprocess.run(("git", "-C", REPO) + args, check=True,
                              capture_output=True, text=True).stdout
    except Exception:
        return None


last_tag = git("describe", "--tags", "--abbrev=0")
subjects = git("log", "--merges", "--format=%s",
               f"{last_tag.strip()}..HEAD") if last_tag else None
if subjects is None:
    # Not a failure. A tarball of this tree has no git history, and a check
    # that turned that into a red light would be one people learn to ignore.
    print("  [skip] no git history here, so no merge subjects to compare")
else:
    # The issue a merge CLOSES is the one its subject opens with - "Merge
    # #294: ..." or "Merge #291 item 1: ..." - which is the form this
    # repository already uses when a branch closes something. An issue named
    # anywhere else in a subject is being referred to, not closed, and
    # counting those was measured wrong on the first run after this check
    # shipped: "Merge: the roadmap carries #295 and #296" gives #296 a home in
    # a document and closes nothing, and the check demanded an entry for it.
    #
    # The cost is a merge that closes an issue and does not say so in that
    # form, which this check will not see. That is the right way round: a miss
    # is a check that did not fire, and a false finding is a check people turn
    # off.
    merged = set()
    for line in subjects.split("\n"):
        m = re.match(r"Merge #(\d+)\b", line.strip())
        if m:
            merged.add(m.group(1))
    # Where an entry is allowed to be, and it is not only `## Unreleased`.
    #
    # A release is cut in two steps: the heading is renamed to the new version
    # and the tag is created after it. In between, `git describe` still
    # answers the PREVIOUS tag, so every merge since it is legitimately filed
    # under the new version's heading and `## Unreleased` is correctly empty.
    # Reading only `## Unreleased` reported all twelve of 0.72.0's issues as
    # unwritten, at the one moment somebody is looking at this file hardest.
    #
    # So: `## Unreleased`, plus the section named by VERSION when that is not
    # the tag `git describe` answers. Both, not either, because a branch may
    # have merged after the rename and its entry belongs in the open section.
    # Anything older than those two is a released section, and an entry
    # landing there is what #284 is about and what the check above catches.
    def section(head):
        mm = re.search(r"^" + re.escape(head) + r"\s*$", TEXT, re.M)
        if not mm:
            return ""
        nxt = re.search(r"^## \S", TEXT[mm.end():], re.M)
        return TEXT[mm.end():mm.end() + nxt.start()] if nxt else TEXT[mm.end():]

    open_text = section("## Unreleased")
    version = open(os.path.join(REPO, "VERSION"), encoding="utf-8").read().strip()
    if last_tag.strip().lstrip("v") != version:
        # The heading carries a title after the number, so it is matched by
        # its opening rather than whole.
        mm = re.search(r"^## " + re.escape(version) + r"\b.*$", TEXT, re.M)
        if mm:
            open_text += section(mm.group(0))
    written = set(re.findall(r"/hexarouting/issues/(\d+)\)", open_text))
    missing = sorted(merged - written, key=int)
    if missing:
        for n in missing:
            check(False,
                  f"a merge since {last_tag.strip()} closes #{n} and no "
                  f"entry above the last released section mentions it. The "
                  f"release note is written from there, so #{n} is a change "
                  f"the reader is not told about")
    elif merged:
        print(f"  [ ok ] {len(merged)} issue(s) closed by merges since "
              f"{last_tag.strip()}, all of them written down")
    else:
        print(f"  [ ok ] no merge since {last_tag.strip()} names an issue, "
              f"so there is nothing this check can be wrong about")

print("\n=== and the section a release will rename says each heading once ===")
# THE GATE ALREADY REFUSES THIS, AND ONLY AT THE RELEASE. Its message names the
# cause exactly - "a branch cut before a release rename re-adds the whole
# section on merge", except here it is simpler: seventeen branches each add
# their entry under `### Fixed`, git resolves none of it because they are
# different lines, and the section ends up saying Fixed twelve times.
#
# Measured on 2026-09-15: `## Unreleased` carried 12 `### Fixed` and 4
# `### Added` over 37 entries. That section becomes `## <version>` the moment a
# release is cut, so the gate would have refused - at the one moment when
# somebody is holding a tag and a built image and wants to publish, over
# something every merge could have caught for free.
#
# CONTRIBUTING.md section 5 says the entry goes in the order a reader cares
# about, and a section that says Fixed twelve times has no order.
_u = re.search(r"^## Unreleased$(.*?)(?=^## )", TEXT, re.M | re.S)
if not _u:
    check(False, "there is no '## Unreleased' section to read, so this check "
                 "is about nothing")
else:
    _h = re.findall(r"^(### .+)$", _u.group(1), re.M)
    _dup = sorted({h for h in _h if _h.count(h) > 1})
    check(not _dup,
          f"'## Unreleased' repeats {', '.join(_dup)}. The release gate "
          f"refuses this once the section is renamed to a version, so it is "
          f"found with a tag in hand instead of at the merge that caused it. "
          f"Keep one of each and move the bullets under it: "
          f"{[(h, _h.count(h)) for h in _dup]}")
    check(len(_h) >= 1,
          f"'## Unreleased' has no '###' heading at all while carrying "
          f"{_u.group(1).count(chr(10) + '* **')} entries, so this check read "
          f"nothing")

if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
