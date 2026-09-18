#!/bin/sh
# Build the release medium from the TAG, boot it, and refuse anything else.
#
#   sh tools/release-gate.sh              the tag at HEAD
#   sh tools/release-gate.sh v0.26.0      a named tag
#
# CONTRIBUTING.md section 5 requires every release to ship an installation
# medium that has been built AND booted. This is that procedure as a command,
# because as a sequence somebody retypes it gets retyped wrong: in one evening
# the same person got it wrong three times, and every one was caught by a rule
# rather than by attention.
#
#   1. Built from the WORKING TREE, so the image carried uncommitted work from
#      the next issue and would have shipped as the release.
#   2. Built without the plugin, because a clean tree has no .build-vpp.
#   3. Built from a git WORKTREE, whose .git is a pointer to a repository the
#      build container does not have mounted - so git inside answered "not a
#      git tree" and the image came out stamped "unverified".
#
# A clone is what works: self-contained, so the container sees a whole
# repository and the version stamp can verify it.
set -eu


# The sentences the self-test can end with. See that file: two readers of
# them were copied by hand and refused a correct medium twice.
. "$(dirname "$0")/selftest-verdict.sh"
TAG=${1:-}
REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=${RELEASE_WORK:-/tmp/prisma-release}

if [ -z "$TAG" ]; then
  TAG=$(git -C "$REPO" describe --exact-match --tags HEAD 2>/dev/null || true)
  [ -n "$TAG" ] || {
    echo "error: HEAD is not on a tag, and a release is built from one." >&2
    echo "       Pass one: sh tools/release-gate.sh v0.26.0" >&2
    exit 1
  }
fi

echo "==> release gate for $TAG"

# --no-local so the clone is a real copy rather than hardlinks into the
# original .git, which is the same trap the worktree fell into.
rm -rf "$WORK"
git clone -q --no-local "$REPO" "$WORK"
git -C "$WORK" checkout -q "$TAG" 2>/dev/null || {
  echo "error: $TAG is not a tag in this repository." >&2
  echo "       Tag the release first, and push it." >&2
  exit 1
}

STAMP=$(sh "$WORK/tools/version-stamp.sh" "$WORK")
NAME=$(sh "$WORK/tools/version-stamp.sh" --filename "$WORK")
echo "    stamp: $STAMP"
echo "    image: $NAME"

# The refusal that makes this a gate rather than a script. A release image
# whose name carries a commit, "dirty" or "unverified" is by definition not
# the tagged tree - and the naming rule (#91) is what caught all three
# mistakes above, so it is what this checks.
case "$NAME" in
  *-dirty.iso|*-unverified.iso)
    echo "error: the tree does not verify as $TAG: $STAMP" >&2
    exit 1 ;;
esac
# The TAG against VERSION, not the name against VERSION.
#
# The first version of this check compared the image name with VERSION - and
# the name is BUILT from VERSION, so it agreed with itself and could never
# fail. A check that cannot fail is not a check, which is the defect this
# project keeps finding in its own tests, this time in the very script written
# to stop a release procedure going wrong.
#
# What is worth checking is that the tag and the file agree: a v0.27.0 tag on
# a tree whose VERSION says 0.26.0 produces an image called 0.26.0, published
# under a release called 0.27.0.
VER=$(tr -d '[:space:]' < "$WORK/VERSION")
if [ "$TAG" != "v$VER" ]; then
  echo "error: tag $TAG is on a tree whose VERSION is $VER." >&2
  echo "       The image would be called prisma-routing-$VER.iso and published" >&2
  echo "       under $TAG. Fix VERSION or the tag before releasing." >&2
  exit 1
fi

# The version must have an entry in the CHANGELOG of the tree being released.
#
# v0.61.0, v0.61.1 and v0.61.2 were each tagged, built, installed and booted
# with no entry at all: the release commits bumped VERSION and touched nothing
# else, so the newest heading in the file stayed 0.60.0 and the work three
# shipped versions were running sat under "Unreleased". An operator on 0.61.2
# looking up what they run would conclude their box was on something
# unreleased - which is worse than an empty file (#195).
#
# The rule was already written down, in CONTRIBUTING.md and in the CHANGELOG's
# own TL;DR, and being written down did not enforce it three times in a row.
# This script is the only thing in this project that has ever caught a release
# mistake, so the rule belongs here.
#
# The heading, not just the string: "0.61.2" appears in a tag message, in a URL
# and in prose, and a grep for the bare number would pass on any of them.
if ! grep -qE "^## v?$VER( |\$)" "$WORK/CHANGELOG.md"; then
  echo "error: CHANGELOG.md in $TAG has no '## $VER' heading." >&2
  echo "       Every release carries an entry - an operator reporting a fault" >&2
  echo "       says which version they run, and without one that number means" >&2
  echo "       nothing to anybody. Write it before tagging, not afterwards" >&2
  echo "       from memory." >&2
  grep -n "^## " "$WORK/CHANGELOG.md" | sed -n '3,5p' | sed 's/^/       newest headings: /' >&2
  exit 1
fi
# And the work must not ALSO still be sitting under Unreleased, which is what
# it looks like when the heading was added by hand and the section below it was
# never moved.
if awk '/^## Unreleased/{u=1;next} /^## /{u=0} u && /^[*-] /{n++} END{exit !(n>0)}' \
     "$WORK/CHANGELOG.md"; then
  echo "error: CHANGELOG.md in $TAG still has entries under '## Unreleased'." >&2
  echo "       Those describe behaviour this medium HAS. Move them under" >&2
  echo "       '## $VER' or the release ships work it does not admit to." >&2
  exit 1
fi

# The release note has to say the medium was built, booted, installed and
# booted again.
#
# CONTRIBUTING.md section 5 requires it, and nothing held it: this script
# PERFORMS both boots and never read the note. The rule decayed across two
# releases without a word - v0.64.0 carries the sentence, v0.65.0 has one of
# the words, and v0.66.0 says nothing about either (#260).
#
# It is the NOTE and not the steps, because the steps are already refused
# below if they fail. The note is what an operator who does not clone this
# repository reads, and section 5 says so in the line above: a tag is a
# pointer, a release is where they read what changed. A note that does not say
# the medium was installed and booted leaves them to assume it, and #119 and
# #121 are what that assumption would have been wrong about.
#
# All four words in one PARAGRAPH, and the difference between that and one
# line is what this gate refused 0.72.0 over. Its note said
#
#   Installation medium built from this tag, booted, installed to a disk and
#   booted again from that disk.
#
# which is the sentence this asks for, wrapped at a sensible width. Matching
# per line reads that as two lines with two words each and refuses a release
# whose note is correct.
#
# That is the needle trap this repository has now met six times (#279, #285,
# and three of the messages in 0.72.0 itself), and it is worst here: every
# other instance costs a false green, and this one costs a false RED on the
# one gate that has caught every release mistake this project has made. A gate
# that refuses correct work is a gate somebody learns to argue with.
#
# A paragraph and not the whole note, because the objection in the sentence
# this replaces is still right: matching across the whole thing would pass on
# a note that mentions an installed box three paragraphs away for some other
# reason, which every note about this product eventually does. Blank lines
# separate the claim from everything else, and a claim is one paragraph.
note=$(git -C "$WORK" tag -l --format="%(contents)" "$TAG")
# Paragraphs joined onto single lines: awk prints a newline only where a blank
# line was, and spaces elsewhere.
folded=$(printf '%s\n' "$note" | awk 'BEGIN{s=""} /^[[:space:]]*$/{if(s!=""){print s; s=""}; next} {s = (s=="" ? $0 : s " " $0)} END{if(s!="")print s}')
if ! printf '%s\n' "$folded" | grep -qiE "built.*booted.*installed.*(again|second)"; then
  echo "error: the release note for $TAG does not record that the medium was" >&2
  echo "       built, booted, installed and booted again." >&2
  echo "       CONTRIBUTING.md section 5 requires the note to say it, because" >&2
  echo "       the note is what an operator who does not clone this repository" >&2
  echo "       reads. This gate is about to do all four; say so in the tag." >&2
  echo "       v0.64.0 put it in one sentence, and it may wrap:" >&2
  echo "         Built, booted, installed to a disk and booted again." >&2
  echo "       All four have to be in ONE PARAGRAPH: a note that mentions an" >&2
  echo "       installed box somewhere else, for some other reason, is not" >&2
  echo "       this claim." >&2
  exit 1
fi

# No conflict marker may survive a merge, anywhere in the tree.
#
# This is here because it HAPPENED, and because the checks that were being run
# could not see it. After each merge the checks were "no duplicate bullets" and
# "make test passes", and six markers sat in CHANGELOG.md across two merges
# while both stayed green: a marker is not a duplicate, and no test reads that
# file. The released tag was clean, which is luck rather than process - the
# markers went in after it.
#
# The whole tree and not only the changelog: a marker in a shell script is a
# syntax error somebody notices, but a marker in Markdown, in a comment, or in
# a data file is invisible until a reader meets it.
#
# Only the angle brackets are searched for. A bare '=======' is also a
# Markdown heading underline, and this tree already carries one such row of
# '=' (test/test_prismash_complete.py, a copy of frr-reload's own report), so a
# pattern that counted it would refuse a release over a quoted report. Nothing
# is lost by leaving it out: git writes all three markers together, so a file
# that really carries a conflict is named by the brackets alone.
marks=$(grep -rlE "^(<<<<<<< |>>>>>>> )" "$WORK" \
        --exclude-dir=.git 2>/dev/null || true)
if [ -n "$marks" ]; then
  echo "error: $TAG carries unresolved conflict markers:" >&2
  # Named relative to the tree: "$WORK" is a throwaway clone whose path means
  # nothing to the reader, who has to find the file in the repository.
  echo "$marks" | sed "s|^$WORK/|       |" >&2
  echo "       A merge was committed with its conflicts in it. The fix is to" >&2
  echo "       resolve them, not to tag over them." >&2
  exit 1
fi

# And no bullet may be written twice.
#
# This is a mechanical hazard, not carelessness, and it cost two merges to
# learn: a branch cut BEFORE a release rename still calls the section
# "Unreleased", master has renamed the same section to "## <VERSION>", and git
# sees the branch's copy of every bullet as an ADDITION rather than a
# duplicate. The only conflict it raises is over the heading. So the released
# section quietly grows a second copy of entries it already had, and the merge
# looks clean.
#
# Compared on the first line of each bullet, which is where the claim is. A
# reworded second copy of the same fix is a different defect and this will not
# catch it - the two that were found by hand were reworded - but an exact
# repeat is what the merge produces, and that is what this is for.
# A section for a version that has already been tagged must be what that tag
# had, byte for byte.
#
# The comment above the duplicate-bullet check below describes half of this: a
# branch cut before a release rename still says "Unreleased", master has
# renamed that section, and git files the branch's bullets INSIDE the released
# one. That check catches the copies. It does not catch a branch carrying a NEW
# entry, which lands under the released version and credits it with work that
# is not in it - measured on two real branches after 0.67.0 was cut, and there
# are eighteen of them waiting (#269).
#
# An honest correction to a shipped section is the case worth naming: this rule
# forbids it, deliberately. CHANGELOG.md's own TL;DR says a reconstruction that
# hides being one is a second, quieter defect, so a correction belongs in the
# CURRENT section saying what it corrects, where a reader of the old version
# has a chance of seeing it.
# ONLY the previous section is compared, and that is a known gap (#274).
# Drift happens on the section a branch was cut against, which is the previous
# one, so this catches the case that occurs. It does not catch older drift:
# `release-gate.sh v0.67.0` refuses today because entries were filed into the
# tagged 0.66.0 section after v0.66.0 shipped, and releasing 0.68.0 never looks
# at 0.66.0.
#
# Widening it to every tagged section was written and NOT kept, because it
# refuses on that 0.66.0 drift and the only repair is editing a shipped
# section, which the rule below forbids. That would be a gate nobody can pass,
# and a gate nobody can pass is one somebody switches off. What to do about
# 0.66.0 is the maintainer's call, recorded in #274.
prev=$(git -C "$WORK" tag -l "v*" --sort=-v:refname | grep -v "^$TAG\$" | head -1)
if [ -n "$prev" ]; then
  prev_ver=${prev#v}
  cur_sect=$(awk -v v="## $prev_ver" '
    index($0, v) == 1 { p = 1; print; next }
    /^## / { p = 0 }
    p { print }
  ' "$WORK/CHANGELOG.md")
  was_sect=$(git -C "$WORK" show "$prev:CHANGELOG.md" 2>/dev/null | awk -v v="## $prev_ver" '
    index($0, v) == 1 { p = 1; print; next }
    /^## / { p = 0 }
    p { print }
  ')
  if [ -n "$was_sect" ] && [ "$cur_sect" != "$was_sect" ]; then
    echo "error: the '## $prev_ver' section of CHANGELOG.md is not what $prev" >&2
    echo "       shipped. A section for a version that has already been tagged" >&2
    echo "       describes a medium somebody is running; editing it changes" >&2
    echo "       what that box is said to do." >&2
    echo "       Most likely a branch cut before the rename filed its entry" >&2
    echo "       there. Move it under '## Unreleased'. If it is a correction," >&2
    echo "       put it in the current section and say what it corrects." >&2
    printf '%s\n' "$cur_sect" > /tmp/prisma-sect-now
    printf '%s\n' "$was_sect" > /tmp/prisma-sect-was
    diff -u /tmp/prisma-sect-was /tmp/prisma-sect-now 2>/dev/null | head -20 >&2
    exit 1
  fi
fi

# And no heading may appear twice inside THE SECTION BEING RELEASED.
#
# The duplicate-bullet check below compares BULLETS, and a duplicated heading
# is the same accident one level up: merging fourteen branches that each added
# an entry under "### Fixed" gives git two additions at the same anchor, the
# resolution keeps both sides, and each side brings its own heading. 0.67.0
# shipped with "### Fixed" three times and "### Added" twice, and nobody
# writing it did that (#267).
#
# ONLY the section being released, and that is the whole of #274. The first
# version of this read the entire file, and the entire file is where the six
# duplicates already are - three in 0.67.0, two in 0.66.0, one in 0.14.0, all
# of them in sections that have shipped. Clearing them means editing a tagged
# section, which the check above refuses and which CHANGELOG.md's TL;DR
# forbids in words. So the gate could not be passed at all, and a gate that
# cannot be passed is one somebody switches off. Measured on a dry run of
# 0.68.0: refused, on nothing anyone was allowed to touch.
#
# The tagged sections are not going unchecked. They are held by the rule
# above, which is the stronger one: not "has no duplicate headings" but "is
# byte-for-byte what its tag shipped", duplicates and all. A duplicate that
# shipped is part of the medium somebody is running.
rel_sect=$(awk -v v="## $VER" '
  index($0, v) == 1 { p = 1; next }
  /^## / { p = 0 }
  p { print }
' "$WORK/CHANGELOG.md")
# If that found nothing the check has quietly stopped checking, which is worse
# than the defect it looks for. A release whose version has no heading is
# already refused further up, so reaching here with an empty section means the
# extraction broke rather than the file being wrong.
if [ -z "$rel_sect" ]; then
  echo "error: the '## $VER' section of CHANGELOG.md came out empty, so the" >&2
  echo "       duplicate-heading check read nothing at all. The heading is" >&2
  echo "       there - it is checked above - so this is the extraction, not" >&2
  echo "       the file." >&2
  exit 1
fi
dup_head=$(printf '%s\n' "$rel_sect" | awk '
  /^### / {
    if ($0 in seen) printf "  %s (line %d of the section)\n", $0, NR
    seen[$0] = 1
  }
')
if [ -n "$dup_head" ]; then
  echo "error: CHANGELOG.md repeats a heading inside the $VER section:" >&2
  printf '%s\n' "$dup_head" >&2
  echo "       CONTRIBUTING.md section 5 says the entry goes in the order a" >&2
  echo "       reader cares about, and a section that says Fixed three times" >&2
  echo "       has no order. It comes from merging branches that each added" >&2
  echo "       under the same heading: keep one and move the bullets under it." >&2
  exit 1
fi

dups=$(grep -E "^\* \*\*" "$WORK/CHANGELOG.md" | sort | uniq -d)
if [ -n "$dups" ]; then
  echo "error: CHANGELOG.md in $TAG says the same thing twice." >&2
  echo "$dups" | sed 's/^/       /' >&2
  echo "       A branch cut before a release rename re-adds the whole" >&2
  echo "       section on merge. Keep one copy, under the version that" >&2
  echo "       actually shipped it." >&2
  exit 1
fi

# WHAT THE OPERATORS CAN READ, against what this release says.
#
# A page on the published wiki that disagrees with the product is the product
# lying to somebody who is holding a screen and deciding what state their box
# is in. One of the pages sitting uncorrected told an operator that a refused
# `commit` applies nothing, which stopped being true when the FRR read-back
# check became a refusal that leaves the box half-configured - and it stayed
# up for days because nothing asked.
#
# ASKED HERE because this is the moment somebody with the token is standing in
# front of the release. `make test` would go red on every run and be learned
# around; the gate goes red once, at the act it belongs to, and the person who
# can fix it is the person who typed the command.
#
# tools/wiki-status.sh is the holder: it reads the git history, compares every
# page against the commit that last wrote wiki/.pulled, and splits pages that
# need a push from pages that do not exist on the wiki at all and have to be
# created first. Nothing is re-derived here and no page content is parsed -
# this reads its exit status and prints what it said.
#
# Before RELEASE_GATE_CHECK_ONLY on purpose, so it can be exercised without
# building a 321 MB image.
# BOTH VERDICTS BEFORE EITHER REFUSAL. Refusing on the first one found means
# the maintainer fixes the wiki, runs the gate again, and only then learns the
# roadmap disagrees too - and a check that lives behind a check which is red
# today is a check nobody has seen run. Measured: the wiki has been behind
# since 2026-09-09, so the roadmap check placed after it would never have
# executed once.
docs_bad=0
if ! wiki_out=$(sh "$WORK/tools/wiki-status.sh" 2>&1); then
  echo "error: this release would ship with the published wiki behind it." >&2
  printf '%s\n' "$wiki_out" | sed 's/^/       /' >&2
  echo "       A page that disagrees with the product is read by somebody" >&2
  echo "       deciding what state their box is in, so it is a defect and" >&2
  echo "       not a chore. Publish them, then run this again." >&2
  docs_bad=$((docs_bad + 1))
else
  echo "==> the published wiki matches this tree"
fi

# AND THE FILE SOMEBODY READS TO DECIDE WHAT TO DO NEXT.
#
# tools/roadmap-state.sh compares docs/roadmap.md against the tracker: a closed
# issue the file still presents as work that remains, and an open issue the
# file never mentions. Both mislead the same reader in opposite directions -
# one sends somebody to do work that is done, the other hides work that is not.
#
# It has never had a caller. It needs the network to know an issue's state, so
# it cannot live in `make test`, which runs offline and on every commit - and a
# reader nobody calls is a rule that has quietly stopped applying, which is
# what test/test_selftest.sh refuses of the verdict holder's readers for the
# same reason. The gate is where it belongs: this is the moment somebody is
# deciding that the tree is worth a version number, and it is the one command
# that already has a network.
#
# Skipped, loudly, when gh cannot answer. A release refused because GitHub was
# unreachable is a gate somebody learns to argue with, and this check is about
# a document rather than about the medium.
#
# TWO DIFFERENT FAILURES, and this told them apart only by accident of exit
# status until it did not. Both holders exit 1 for FINDINGS and 2 for "nothing
# was compared", and their headers say so - a roadmap nobody compared reads
# exactly like a roadmap that agreed, so neither may exit 0 without an answer.
# This read any non-zero as disagreement, which turns a tracker that answered
# with nothing into a refused release: the exact outcome the paragraph above
# argues against, three lines from where it is argued. Found when a second
# holder was added beside this one and the gate refused a release over a stub
# that answered an empty list.
if ! command -v gh >/dev/null 2>&1 || ! gh auth status >/dev/null 2>&1; then
  echo "==> the roadmap was NOT checked against the tracker: gh cannot answer"
else
  rm_out=$(sh "$WORK/tools/roadmap-state.sh" 2>&1) && rm_rc=0 || rm_rc=$?
fi
if [ "${rm_rc:-0}" = 2 ]; then
  echo "==> the roadmap was NOT checked against the tracker, and it says why:"
  printf '%s\n' "$rm_out" | sed -n '1,3p' | sed 's/^/    /'
elif [ "${rm_rc:-0}" != 0 ]; then
  echo "error: docs/roadmap.md and the tracker disagree." >&2
  printf '%s\n' "$rm_out" | sed -n '/^==> closed issues/,$p' | sed 's/^/       /' >&2
  echo "       That file is what somebody reads to decide what to do next," >&2
  echo "       so a closed issue presented as pending sends them to do work" >&2
  echo "       that is done, and an open issue it never names hides work" >&2
  echo "       that is not. Fix the file, then run this again." >&2
  docs_bad=$((docs_bad + 1))
else
  echo "==> the roadmap and the tracker agree"
fi

# tools/changelog-state.sh asks the other half of the same question, of the
# other document: an issue closed as COMPLETED that CHANGELOG.md names nowhere
# is work that shipped with no record a reader can find. CONTRIBUTING.md
# section 5 rule 3 makes the entry part of the merge, and nothing had ever
# checked. Three had gone missing by the time anybody looked, over a tracker of
# 293 closed issues, and all three were found by hand while writing a note.
#
# HERE AND NOT IN `make test` for the reason its own header gives: whether an
# issue is closed is written down nowhere in the tree, and every suite has to
# pass offline (#317). Beside the roadmap check and not behind it - #340 is on
# this file for exactly that mistake, a check placed after one that had been
# red for days and so never ran once.
#
# Skipped loudly when gh cannot answer, on the same argument as above: a
# release refused because GitHub was unreachable is a gate somebody learns to
# argue with.
# Exit 2 is a loud skip and not a refusal, for the reason written against the
# roadmap check above: this holder cannot tell an unreachable tracker from a
# clean one except by refusing to answer, and a release stopped because GitHub
# was down is a gate somebody learns to argue with.
if ! command -v gh >/dev/null 2>&1 || ! gh auth status >/dev/null 2>&1; then
  echo "==> the CHANGELOG was NOT checked against the tracker: gh cannot answer"
else
  cl_out=$(sh "$WORK/tools/changelog-state.sh" "$WORK/CHANGELOG.md" 2>&1) \
    && cl_rc=0 || cl_rc=$?
fi
if [ "${cl_rc:-0}" = 2 ]; then
  echo "==> the CHANGELOG was NOT checked against the tracker, and it says why:"
  printf '%s\n' "$cl_out" | sed -n '1,3p' | sed 's/^/    /'
elif [ "${cl_rc:-0}" != 0 ]; then
  echo "error: work has shipped that CHANGELOG.md does not record." >&2
  printf '%s\n' "$cl_out" | sed -n '/^==> completed issues/,$p' | sed 's/^/       /' >&2
  echo "       The CHANGELOG is what an operator who does not clone this" >&2
  echo "       repository reads to find out what changed for them. Write the" >&2
  echo "       entry in the CURRENT section, naming the release that carried" >&2
  echo "       the work if it was an older one." >&2
  docs_bad=$((docs_bad + 1))
else
  echo "==> the CHANGELOG records every completed issue"
fi

# AND THE TITLE THIS RELEASE WILL CARRY WHERE PEOPLE ACTUALLY READ IT.
#
# The note on the tag is checked above, with the argument that it is what an
# operator who does not clone this repository reads. The release page is the
# same audience with a wider reach: it is what somebody lands on from a search,
# from a link in a ticket, or from the repository's own releases list, and the
# title is the first thing they read when deciding whether a release fixes the
# thing biting them.
#
# Measured on 2026-09-15 over all 74 published releases: 66 titles are the
# CHANGELOG heading and 8 are not. Four of those are TRUNCATIONS - 0.69.0,
# 0.70.0, 0.71.0 and 0.46.0 each drop the second half of their heading, which
# is where the second defect that release fixed is named (#340).
#
# Only the release being cut can be checked here, and only when it has already
# been published - which is the normal shape for a re-run of the gate over a
# tag that went out. A tag with no release yet is the ordinary case and says so
# rather than refusing: the publishing has not happened.
# NOT behind docs_bad. Guarding this on the two checks above having passed puts
# it behind a check that has been red since 2026-09-09, which is the defect the
# wiki and roadmap pair had and which was fixed two hours before this was
# written. All three verdicts are taken, then one refusal.
#
# THREE ANSWERS, NOT TWO. `gh release view` failing meant "not published
# yet" here, because the call ended in `|| true` and every failure arrived
# as the same empty string. Driven with a gh that authenticates and cannot
# reach the API: the gate printed "v0.74.2 has no published release yet"
# about a release published on 2026-09-15T01:30:53Z, and passed. A
# gate that states something false at the moment somebody is deciding
# whether to publish is worse than one that says it could not look.
if ! command -v gh >/dev/null 2>&1 || ! gh auth status >/dev/null 2>&1; then
  # LOUDLY, for the reason the roadmap check above is loud about the same
  # condition. Driven with a gh that exits 1: this block printed nothing at
  # all, so its verdict was indistinguishable from the run where it passed.
  echo "==> the release title was NOT checked: gh cannot answer"
else
  gh_err=/tmp/prisma-release-title-err
  if rel_title=$(gh release view "$TAG" --json name -q .name 2>"$gh_err"); then
    rel_state=ok
  elif grep -qi "release not found" "$gh_err"; then
    rel_state=none
  else
    rel_state=unreadable
  fi
  case "$rel_state" in
  none)
    echo "==> $TAG has no published release yet, so its title is not checked"
    ;;
  unreadable)
    echo "==> the release title was NOT checked: gh could not read $TAG"
    sed 's/^/    /' "$gh_err"
    ;;
  ok)
    # The heading, read out of the tagged tree rather than out of the working
    # one: the release is about what the tag carries.
    head_title=$(awk -v v="## $VER" '
      index($0, v) == 1 {
        sub(/^## [0-9.]+[[:space:]]*[-\xe2\x80\x94]*[[:space:]]*/, "")
        print; exit
      }
    ' "$WORK/CHANGELOG.md")
    # Both sides normalised for an optional product name, an optional "v",
    # the version and the dash, so this is about the sentence and not about
    # punctuation somebody typed into a web form. BOTH SPELLINGS OF THE NAME,
    # and the old one is not decoration: every release up to 0.78.0 was
    # published as "HexaRouting", and #202's sweep rewrote this line's two
    # spellings into one - which would have read an old release's title as
    # carrying a product name this check no longer strips.
    norm_title=$(printf '%s' "$rel_title" \
      | sed -e 's/^HexaRouting[[:space:]]*//' -e 's/^Prisma Routing[[:space:]]*//' \
            -e "s/^v\{0,1\}$VER[[:space:]]*[-\xe2\x80\x94]*[[:space:]]*//")
    if [ -z "$head_title" ]; then
      # An empty heading used to satisfy this check rather than fail it: the
      # comparison was guarded on `[ -n "$head_title" ]`, so nothing was
      # compared and the green line was printed anyway. That is the same
      # defect the '## VER' section extraction 150 lines above refuses in
      # words - an extraction that found nothing has quietly stopped
      # checking. Driven: with the heading cut to a bare "## 0.74.2" the
      # gate announced the published title was the heading while the two
      # shared not one word.
      echo "error: the '## $VER' heading in CHANGELOG.md carries no" >&2
      echo "       sentence, so the published release title was compared" >&2
      echo "       against nothing and this check passed having checked" >&2
      echo "       nothing. Give the heading the sentence the release" >&2
      echo "       note carries: '## $VER - what changed'." >&2
      docs_bad=$((docs_bad + 1))
    elif [ "$norm_title" != "$head_title" ]; then
      echo "error: the published release for $TAG does not carry the" >&2
      echo "       CHANGELOG heading as its title." >&2
      echo "         heading:   $head_title" >&2
      echo "         published: $norm_title" >&2
      echo "       That page is what somebody who does not clone this" >&2
      echo "       repository reads, and the title is what they decide on." >&2
      echo "       A shorter one announces less than the release fixed." >&2
      echo "       To fix it:" >&2
      echo "         gh release edit $TAG --title \"$VER - $head_title\"" >&2
      docs_bad=$((docs_bad + 1))
    else
      echo "==> the published release title is the CHANGELOG heading"
    fi
    ;;
  esac
  rm -f "$gh_err"
fi
# AND IT SAYS SO ON THE WAY OUT.
#
# This was a bare `exit 1`. The three checks above print their refusals on
# stderr and their passes on stdout, so a refused run whose stdout is read on
# its own - a log, a pipe, `| tail -1` - ended on "==> the published release
# title is the CHANGELOG heading", which is a green line under a release that
# was refused. Every other verdict in this repository says how many and what
# it means; this one said nothing at all.
#
# A COUNT rather than a flag, because two of these being red is a different
# morning from one, and because the flag could not tell them apart: docs_bad
# was set to 1 by each of three checks, so a run that failed all three and a
# run that failed one exited identically.
if [ "$docs_bad" != 0 ]; then
  echo "error: $docs_bad of the four document checks above refused this" >&2
  echo "       release: what the published wiki says, what docs/roadmap.md" >&2
  echo "       says, what CHANGELOG.md records of the work that closed, and" >&2
  echo "       what the published release is titled. Nothing was built and" >&2
  echo "       nothing was published." >&2
  exit 1
fi

# The same seam the installer has, and for the same reason: the checks above
# are what make this a gate, and a check that can only be exercised by building
# a 321 MB image is a check nobody exercises.
if [ "${RELEASE_GATE_CHECK_ONLY:-0}" = 1 ]; then
  echo "==> check only: $NAME verifies as $TAG, nothing was built"
  exit 0
fi

echo "==> building the plugin"
sh "$WORK/tools/build.sh" plugin >/dev/null

# BEFORE the 325 MB image, because it costs two minutes and it refuses the
# release for a reason the image cannot show.
#
# Everything below this line boots a box, and every box this gate boots comes
# up in FIRST BOOT mode: the kernel has not reserved hugepages yet, so the
# generated startup.conf has no `dpdk` stanza at all. The TUNED profile - the
# one an operator's box runs from its second boot onwards - was never given to
# a real VPP by anything here. 0.75.0 shipped a `dev { mtu ... }` line that
# this VPP answers with "unknown input", so VPP exited, Restart=always made it
# a loop, and the box forwarded nothing (#377). The gate passed it.
echo "==> the generated startup.conf, read by the VPP that reads it"
( cd "$WORK" && sg docker -c "docker run --rm --privileged \
    -v $WORK:/src -w /src prisma-routing-dev:26.06 \
    sh /src/appliance/test_startup_conf_shapes.sh" ) \
  > "$WORK/shapes.log" 2>&1 || {
  echo "error: VPP refuses a startup.conf this tuner can generate. An" >&2
  echo "       installed box that reaches that shape does not start its" >&2
  echo "       dataplane at all." >&2
  grep -E "FAIL|unknown input|NOT MEASURED" "$WORK/shapes.log" | head -10 >&2
  echo "       full run: $WORK/shapes.log" >&2
  exit 1
}
# NOT MEASURED is not a pass, and it is the state this check falls into on a
# machine that cannot reserve hugepages. Publishing on the strength of a run
# that learned nothing is the shape this whole file exists against.
grep -q "NOT MEASURED" "$WORK/shapes.log" && {
  echo "error: the startup.conf shapes were NOT MEASURED on this machine," >&2
  echo "       so nothing here knows whether VPP accepts what the tuner" >&2
  echo "       writes. Cut the release where hugepages can be reserved." >&2
  grep "NOT MEASURED" "$WORK/shapes.log" | head -4 >&2
  exit 1
}
echo "    every shape the tuner can write is one this VPP accepts"

echo "==> building the medium"
( cd "$WORK" && make iso >/dev/null )
[ -f "$WORK/$NAME" ] || { echo "error: $NAME was not produced" >&2; exit 1; }

# ONE MEDIUM, and this is what says so.
#
# The argument for the install phase is that booting a medium and installing it
# are different claims about THE SAME MEDIUM. For the life of this script they
# were claims about two builds: `make test-install` depended on the phony `iso`
# target and rebuilt the image over this path, five and a half minutes after
# the boot that had just passed, and the sha256 printed at the end described
# the build nobody booted (#307).
#
# `make test-install` no longer rebuilds. This is the assertion that keeps it
# that way, and it holds whatever the cause: anything that replaces the file
# between here and the end is caught, not just a make prerequisite.
MEDIUM_SHA=$(sha256sum "$WORK/$NAME" | cut -d" " -f1)

echo "==> booting it"
( cd "$WORK" && sh tools/lab.sh boot-iso ) > "$WORK/boot.log" 2>&1 || {
  echo "error: the medium did not boot. Last of the console:" >&2
  tail -20 "$WORK/boot.log" >&2
  exit 1
}
# THE BOX'S OWN CONSOLE, NOT THE LAB'S RENDERING OF IT.
#
# boot.log is what lab/boot-iso.sh PRINTED, and what it prints of the self-test
# is `sed -n '/boot self-test/,/=====$/p' /tmp/clean.log | head -40` - capped,
# so an operator reading a lab run is not handed the whole report twice. The
# verdict is the LAST line of that report. The report grew past forty lines in
# 0.75.0, the cap swallowed the verdict, and this gate refused a medium that
# had booted and said "Prisma Routing ready, with 3 warning(s) above." on line 704
# of its console. The refusal was not true of the box.
#
# That is the shape #369 is about, in the gate rather than in a reader: the
# corpus was the lab's rendering of the box's output and not the box's output.
# lab/boot-iso.sh asks /tmp/clean.log for the same verdict and was right all
# along; only this caller was reading second-hand.
#
# The console copy is the guest's log, written out by the lab. Its ABSENCE is a
# refusal and not a skip: a gate that shrugs when the console is missing is a
# gate that publishes a medium nobody watched boot.
BOOTLOG=$WORK/boot-console.log
[ -s "$BOOTLOG" ] || {
  echo "error: the medium booted and left no console log at $BOOTLOG, so" >&2
  echo "       nothing here knows what it said. lab/boot-iso.sh copies the" >&2
  echo "       guest's console there; if that stopped happening, the verdict" >&2
  echo "       below would be read off the lab's own summary, which is capped" >&2
  echo "       and drops the last line of the report." >&2
  exit 1
}
said_a_verdict "$BOOTLOG" || {
  echo "error: the medium booted and its self-test reached no verdict." >&2
  grep -E "\[FAIL\]|\[warn\]" "$BOOTLOG" | head -10 >&2
  exit 1
}
# AND THAT VERDICT HAS TO BE A GOOD ONE (#311). Reaching a verdict is true of a
# box that failed its own checks: "N check(s) failed. The box is NOT ready." is
# one of the four. So this gate accepted such a medium and reprinted that line
# under its own "is ready to publish", because the failure it was written for
# was the ABSENCE of a verdict, which is not the dangerous case.
#
# said_a_good_verdict() has been in tools/selftest-verdict.sh since that file
# was written and nothing called it.
said_a_good_verdict "$BOOTLOG" || {
  echo "error: the medium booted and FAILED ITS OWN SELF-TEST. This is the" >&2
  echo "       image an operator is handed, so a failed check here is a" >&2
  echo "       defect that ships." >&2
  grep -E "\[FAIL\]" "$BOOTLOG" | head -10 >&2
  exit 1
}

# The live image booting proves the medium is bootable. It does not prove the
# medium produces a working appliance, and those are different claims: the live
# image never runs the installed-box path.
#
# Two defects lived in that gap for the life of the project. The ISO never
# carried the vat2 plugin, so an installed box had no binary API and could not
# be configured to do CGNAT at all (#119). Behind it, prisma-routing.service
# failed on the first boot of every install (#121). Neither was visible from a
# live boot, and both were found the first time this ran.
#
# So the gate installs to a disk and boots THAT, with the medium detached. The
# second boot is the assertion; a live image cannot make it.
echo "==> installing it to a disk and booting the disk"
( cd "$WORK" && make test-install ) > "$WORK/install.log" 2>&1 || {
  echo "error: the medium installed and the installed box failed its checks." >&2
  grep -E "\[FAIL\]" "$WORK/install.log" | head -10 >&2
  echo "       full console: $WORK/install.log" >&2
  exit 1
}

# AND THE ARTIFACT THE BOXES ALREADY IN THE FIELD TAKE.
#
# A release that publishes only a medium publishes only a route that erases the
# disk. Every box installed from a previous one has a year of connection
# records on /var/log/prisma-routing and no way to keep them across a version
# change, which is #295: the in-place path existed for this script and for
# lab/upgrade.sh, both of which have a build tree, and for nobody who bought
# the product.
#
# BUILT HERE because an artifact nobody installs in the gate is an artifact
# whose first installation is somebody's production box. build-upgrade.sh
# unpacks what it has just packed and runs install.sh out of it into a
# throwaway root, so reaching this line at all means this release's artifact
# has been installed once, from its own tarball, on the machine that built it.
#
# WHAT THAT DOES NOT PROVE, said out loud: nothing here takes a BOOTED box from
# the previous release to this one. That is lab/upgrade.sh's question and it
# still asks it out of a source tree. The claim this gate can make about the
# artifact is that it is complete and that it installs; the claim it cannot
# make is that the box it upgraded still forwards.
#
# The name is derived rather than read back. It is the medium's with the
# extension swapped, which is build-upgrade.sh's own rule, so this is an
# assertion that the two builds of this tree still agree about which tree they
# are - not a second calculation of the identity, which tools/version-stamp.sh
# owns and this gate refuses "-dirty" and "-unverified" on above.
echo "==> building the upgrade artifact"
( cd "$WORK" && make upgrade-artifact ) > "$WORK/upgrade.log" 2>&1 || {
  echo "error: the upgrade artifact for $TAG could not be built, or could not" >&2
  echo "       be installed out of its own tarball. Publishing the medium" >&2
  echo "       alone leaves every box already in the field with no route to" >&2
  echo "       this version but a reinstall, which erases their records." >&2
  tail -20 "$WORK/upgrade.log" >&2
  exit 1
}
UPGRADE=${NAME%.iso}-upgrade.tar.gz
[ -f "$WORK/$UPGRADE" ] || {
  echo "error: the artifact build succeeded and $UPGRADE is not here, so the" >&2
  echo "       medium and the artifact disagree about which tree they are." >&2
  ls "$WORK"/*-upgrade.tar.gz 2>/dev/null | sed 's|^|       found: |' >&2
  exit 1
}

now=$(sha256sum "$WORK/$NAME" | cut -d" " -f1)
[ "$now" = "$MEDIUM_SHA" ] || {
  echo "error: the medium was rebuilt between the boot and now, so this gate" >&2
  echo "       booted one image and installed another. Nothing here says the" >&2
  echo "       one it installed would have booted." >&2
  echo "       booted:    $MEDIUM_SHA" >&2
  echo "       installed: $now" >&2
  exit 1
}

echo
echo "==> $NAME is ready to publish"
echo "    $WORK/$NAME"
echo "    sha256 $(sha256sum "$WORK/$NAME" | cut -d' ' -f1)"
# Beside it, and named in the same block, because a release page that carries
# only the medium is a release that only people with an empty disk can take.
echo "    $WORK/$UPGRADE"
echo "    sha256 $(sha256sum "$WORK/$UPGRADE" | cut -d' ' -f1)"
grep -E "^\s+\[ ok \] Prisma Routing|$VERDICT_RE" "$BOOTLOG" | sed 's/^/    /'
grep -E "\[ ok \] the installed box can configure a pool" "$WORK/install.log" \
  | sed 's/^/    /'
