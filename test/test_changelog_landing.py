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
    merged = set()
    for line in subjects.split("\n"):
        merged |= set(re.findall(r"#(\d+)", line))
    # The section as it stands, which is where an entry has to be.
    m = re.search(r"^## Unreleased\n", TEXT, re.M)
    end = re.search(r"^## \S", TEXT[m.end():], re.M) if m else None
    unrel = TEXT[m.end():m.end() + end.start()] if m and end else ""
    written = set(re.findall(r"/hexarouting/issues/(\d+)\)", unrel))
    missing = sorted(merged - written, key=int)
    if missing:
        for n in missing:
            check(False,
                  f"a merge since {last_tag.strip()} closes #{n} and nothing "
                  f"under '## Unreleased' mentions it. The release note is "
                  f"written from that section, so #{n} is a change the reader "
                  f"is not told about")
    elif merged:
        print(f"  [ ok ] {len(merged)} issue(s) closed by merges since "
              f"{last_tag.strip()}, all of them written down")
    else:
        print(f"  [ ok ] no merge since {last_tag.strip()} names an issue, "
              f"so there is nothing this check can be wrong about")

if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
