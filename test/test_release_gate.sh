#!/bin/sh
# The release gate's refusals, driven one at a time.
#
# tools/release-gate.sh carries eighteen refusals and its own header says it is
# "the only thing in this project that has ever caught a release mistake". Two
# of them had assertions - the two added on 2026-09-15 - and the rest had
# never been seen to fire by anything except a real release going wrong.
#
# A refusal nobody has watched fire is a refusal that may not fire. That is the
# same finding as #335, where a stub answering a constant left the check
# written for #119 unreachable on all 122 boxes its suite arranged, and the
# same as #222 twice before it. A gate is the worst place to have one.
#
# HOW: clone the newest tag into a fixture, BREAK EXACTLY ONE THING, re-tag,
# and require that one refusal. Starting from a tree that passes is what makes
# each case about its own break rather than about the scaffolding - a fixture
# built from nothing has to satisfy every other check by hand, and one that
# drifts from the gate measures the fixture.
#
# WHAT THIS DOES NOT REACH. The gate refuses eleven more times after
# RELEASE_GATE_CHECK_ONLY, and every one of those needs a 321 MB image built,
# booted and installed. Those stay unmeasured here and the count below says so
# out loud rather than leaving the reader to assume this covers the file.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail + 1)); }

TAG=$(git -C "$REPO" tag --sort=-v:refname 2>/dev/null | head -1)
if [ -z "$TAG" ]; then
  echo "  FAIL: this repository has no tag, so the gate cannot be driven"
  echo; echo "1 FAILURES"; exit 1
fi

# One clone, reused. Each case resets it to the tag, breaks one thing, and
# re-tags under a name of its own so the gate is asked about that tree.
FIX=$WORK/fixture
git clone -q --no-local "$REPO" "$FIX"
git -C "$FIX" config user.email t@t
git -C "$FIX" config user.name t
git -C "$FIX" config commit.gpgsign false
git -C "$FIX" config tag.gpgsign false

# The note every release tag carries. Taken from the tag being copied rather
# than written here: a note this file spelled out would pass while the gate
# wanted something else, which is the two-copies problem the gate itself
# refuses elsewhere.
NOTE=$(git -C "$FIX" tag -l --format='%(contents)' "$TAG")

# The COMMIT, captured once. Each case force-updates the tag to point at its
# own broken tree, so resetting to the tag name would carry the previous
# case's damage forward and every case after the first would be measuring the
# one before it. That happened while this was being written: case 3 rewrote
# the CHANGELOG and cases 4, 5 and 6 all refused on it.
BASE=$(git -C "$FIX" rev-parse "$TAG^{commit}")

# The tags the clone arrived with. Cases make their own - v9.9.8 for the
# VERSION mismatch, and each retag of v<version> - and without removing them
# the next case picks one of those as "the previous release" and compares a
# section against a tag that never shipped anything. That happened: the
# released-section case read v9.9.8 and stopped on an assertion of its own.
ORIG_TAGS=$WORK/orig-tags
git -C "$FIX" tag -l | sort > "$ORIG_TAGS"

# THE GATE BEING EDITED, NOT THE ONE LAST COMMITTED. git clone takes the
# committed state, so without this the suite drives whatever tools/ was at the
# last commit and an edit to the gate cannot change a single result here. It
# was written that way first, and every mutation of a refusal came back green
# - a suite that measures a file nobody is changing.
#
# Copied after each reset, because reset_fixture puts the tag's own copy back.
install_gate() {
  cp "$REPO/tools/release-gate.sh" "$FIX/tools/release-gate.sh"
  cp "$REPO/tools/wiki-status.sh" "$FIX/tools/wiki-status.sh"
  cp "$REPO/tools/roadmap-state.sh" "$FIX/tools/roadmap-state.sh"
}

n=0
run_gate() {  # <case tag>; -> $OUT and $RC
  n=$((n + 1))
  OUT=$(cd "$FIX" && RELEASE_WORK="$WORK/w$n" RELEASE_GATE_CHECK_ONLY=1 \
        sh tools/release-gate.sh "$1" 2>&1) && RC=0 || RC=$?
}
reset_fixture() {
  git -C "$FIX" checkout -q -B gatecase "$BASE"
  git -C "$FIX" clean -qfd
  git -C "$FIX" tag -l | sort | comm -13 "$ORIG_TAGS" - \
    | while IFS= read -r _t; do
        [ -n "$_t" ] && git -C "$FIX" tag -d "$_t" >/dev/null 2>&1
      done
  install_gate
}
retag() {  # <name>
  git -C "$FIX" add -A
  git -C "$FIX" commit -q -m "one thing broken" --allow-empty
  git -C "$FIX" tag -f -a "$1" -m "$NOTE" >/dev/null 2>&1
}

echo "=== a tag this repository has not got ==="
# The first refusal, and the only one that needs no fixture at all.
reset_fixture
run_gate v0.0.0-not-a-tag
[ "$RC" != 0 ] && printf '%s\n' "$OUT" | grep -q "is not a tag in this repository" \
  && ok "refused, naming the tag" \
  || bad "the gate accepted a tag that does not exist (rc=$RC): $OUT"

echo
echo "=== a tagged tree whose VERSION says something else ==="
# The tag against VERSION, which is the comparison that means anything: the
# image NAME is built from VERSION, so comparing those two would agree with
# itself and could never fail. The gate's own comment says this.
reset_fixture
printf '9.9.9\n' > "$FIX/VERSION"
retag v9.9.8
run_gate v9.9.8
[ "$RC" != 0 ] && printf '%s\n' "$OUT" | grep -q "whose VERSION is" \
  && ok "refused, and it names both numbers" \
  || bad "a tag disagreeing with VERSION was accepted (rc=$RC): $OUT"

echo
echo "=== a release with no entry in the CHANGELOG ==="
# An operator reporting a fault says which version they run. Without an entry
# that number means nothing to anybody.
reset_fixture
V=$(cat "$FIX/VERSION")
python3 - "$FIX/CHANGELOG.md" "$V" <<'PY'
import sys
p, v = sys.argv[1], sys.argv[2]
t = open(p, encoding="utf-8").read()
t = t.replace("## " + v, "## " + v + "-renamed-away", 1)
open(p, "w", encoding="utf-8").write(t)
PY
retag "v$V"
run_gate "v$V"
[ "$RC" != 0 ] && printf '%s\n' "$OUT" | grep -q "has no '## " \
  && ok "refused, and it lists the headings it did find" \
  || bad "a release with no CHANGELOG entry was accepted (rc=$RC): $OUT"

echo
echo "=== a tag whose note does not record what the gate is about to do ==="
# The note is what somebody who does not clone this repository reads. The
# claim is that the medium was built, booted, installed to a disk and booted
# again, and all four have to be in one paragraph.
reset_fixture
V=$(cat "$FIX/VERSION")
git -C "$FIX" add -A
git -C "$FIX" commit -q -m "note case" --allow-empty
git -C "$FIX" tag -f -a "v$V" -m "a note that claims nothing" >/dev/null
run_gate "v$V"
[ "$RC" != 0 ] && printf '%s\n' "$OUT" | grep -q "does not record that the medium was" \
  && ok "refused, and it quotes the sentence that would do" \
  || bad "a tag whose note claims nothing was accepted (rc=$RC): $OUT"

echo
echo "=== a tagged tree carrying conflict markers ==="
# A merge somebody resolved by committing both sides. It reaches the medium.
reset_fixture
printf '<<<<<<< HEAD\na\n=======\nb\n>>>>>>> other\n' >> "$FIX/README.md"
V=$(cat "$FIX/VERSION")
retag "v$V"
run_gate "v$V"
[ "$RC" != 0 ] && printf '%s\n' "$OUT" | grep -q "unresolved conflict markers" \
  && ok "refused, and it names the file" \
  || bad "a tree with conflict markers was accepted (rc=$RC): $OUT"

echo
echo "=== a CHANGELOG section that says the same heading twice ==="
# Two branches each adding under '### Fixed' and git keeping both. A section
# that says Fixed three times has no order, which is what the entry is for.
reset_fixture
V=$(cat "$FIX/VERSION")
python3 - "$FIX/CHANGELOG.md" "$V" <<'PY'
import sys
p, v = sys.argv[1], sys.argv[2]
t = open(p, encoding="utf-8").read()
i = t.index("## " + v)
j = t.index("\n", i)
t = t[:j] + "\n\n### Fixed\n\n* **One.** Text.\n\n### Fixed\n\n* **Two.** Text.\n" + t[j:]
open(p, "w", encoding="utf-8").write(t)
PY
retag "v$V"
run_gate "v$V"
[ "$RC" != 0 ] && printf '%s\n' "$OUT" | grep -q "repeats a heading inside" \
  && ok "refused, and it says which heading and where" \
  || bad "a section repeating a heading was accepted (rc=$RC): $OUT"

echo
echo "=== HEAD not on a tag at all, with no tag named ==="
# The only refusal that needs no fixture and the only one reached by running
# the gate with no argument: a release is built from a tag, and the commit
# somebody happens to be sitting on is not one.
reset_fixture
OUT=$(cd "$FIX" && RELEASE_WORK="$WORK/w-notag" RELEASE_GATE_CHECK_ONLY=1 \
      sh tools/release-gate.sh 2>&1) && RC=0 || RC=$?
[ "$RC" != 0 ] && printf '%s\n' "$OUT" | grep -q "HEAD is not on a tag" \
  && ok "refused, and it says how to pass one" \
  || bad "the gate built a release from a commit that is not a tag (rc=$RC): $OUT"

echo
echo "=== a release tag whose Unreleased section still has entries ==="
# Renaming the section to a version is what cuts a release. A tag carrying
# both means the entries under Unreleased are work this release contains and
# does not claim, so an operator reading the entry for their version gets a
# shorter list than the box they are running.
reset_fixture
V=$(cat "$FIX/VERSION")
python3 - "$FIX/CHANGELOG.md" <<'PYE'
import sys, re
p = sys.argv[1]
t = open(p, encoding="utf-8").read()
m = re.search(r"^## Unreleased$\n", t, re.M)
t = t[:m.end()] + "\n### Fixed\n\n* **Left behind.** Text.\n" + t[m.end():]
open(p, "w", encoding="utf-8").write(t)
PYE
retag "v$V"
run_gate "v$V"
[ "$RC" != 0 ] && printf '%s\n' "$OUT" | grep -q "still has entries under" \
  && ok "refused, and it names the entries left behind" \
  || bad "a tag carrying both a version and an unreleased entry was accepted \
(rc=$RC): $OUT"

echo
echo "=== a released section that is not what its own tag shipped ==="
# A branch cut before a release rename lands its bullets INSIDE the released
# version, crediting it with work it did not carry. The section for a version
# already tagged must be, byte for byte, what that tag had.
reset_fixture
V=$(cat "$FIX/VERSION")
PREV=$(git -C "$FIX" tag -l "v*" --sort=-v:refname \
       | grep -v "^v$V\$" | head -1 | sed 's/^v//')
if [ -z "$PREV" ]; then
  bad "the fixture has no previous release section, so this case is about \
nothing"
else
  python3 - "$FIX/CHANGELOG.md" "$PREV" <<'PYE'
import sys, re
p, prev = sys.argv[1], sys.argv[2]
t = open(p, encoding="utf-8").read()
m = re.search(r"^## " + re.escape(prev) + r"\b.*$", t, re.M)
assert m, "no '## " + prev + "' heading at the start of a line"
t = t[:m.end()] + "\n\n* **Added to a shipped release.** Text.\n" + t[m.end():]
open(p, "w", encoding="utf-8").write(t)
PYE
  retag "v$V"
  run_gate "v$V"
  [ "$RC" != 0 ] && printf '%s\n' "$OUT" | grep -q "the .## $PREV. section of CHANGELOG.md is not what" \
    && ok "refused, and it names the version whose section moved" \
    || bad "a shipped release's section was edited and the gate accepted it \
(rc=$RC): $OUT"
fi

echo
echo "=== and the guard for a check that has stopped checking ==="
# The duplicate-heading check extracts the version's section first. An
# extraction that comes back empty would make that check pass over anything,
# so the gate refuses the empty extraction itself rather than the file - and
# says which of the two it is, because the heading is verified above.
reset_fixture
V=$(cat "$FIX/VERSION")
python3 - "$FIX/CHANGELOG.md" "$V" <<'PYE'
import sys, re
p, v = sys.argv[1], sys.argv[2]
t = open(p, encoding="utf-8").read()
mh = re.search(r"^## " + re.escape(v) + r"\b.*$", t, re.M)
assert mh, "no version heading at the start of a line"
i = mh.start()
j = t.index("\n## ", mh.end())
# The heading stays and its body goes, which is the shape that makes the
# extraction empty while every check above it still passes.
t = t[:t.index("\n", i) + 1] + t[j + 1:]
open(p, "w", encoding="utf-8").write(t)
PYE
retag "v$V"
run_gate "v$V"
[ "$RC" != 0 ] && printf '%s\n' "$OUT" | grep -q "came out empty" \
  && ok "refused, and it says the extraction broke rather than the file" \
  || bad "a version section that extracted to nothing was accepted, so the \
duplicate-heading check passed over it (rc=$RC): $OUT"

echo
echo "=== a CHANGELOG that says the same thing twice ==="
# A branch cut before a release rename re-adds its own bullet under the
# renamed heading, so one entry appears under two versions and an operator
# cannot tell which release carried the fix.
reset_fixture
V=$(cat "$FIX/VERSION")
python3 - "$FIX/CHANGELOG.md" <<'PYE'
import sys
p = sys.argv[1]
lines = open(p, encoding="utf-8").read().split("\n")
i = next(k for k, l in enumerate(lines) if l.startswith("* **"))
lines.insert(i + 1, lines[i])
open(p, "w", encoding="utf-8").write("\n".join(lines))
PYE
retag "v$V"
run_gate "v$V"
[ "$RC" != 0 ] && printf '%s\n' "$OUT" | grep -q "says the same thing twice" \
  && ok "refused, and it quotes the line that appears twice" \
  || bad "a CHANGELOG carrying one entry twice was accepted (rc=$RC): $OUT"

echo
echo "=== and this suite reaches only the half that needs no image ==="
# Said as a number rather than left to be assumed. The gate refuses eighteen
# times; everything after RELEASE_GATE_CHECK_ONLY needs a 321 MB image built,
# booted and installed, and is measured by running a real release.
REFUSALS=$(grep -c 'echo "error:' "$REPO/tools/release-gate.sh")
BEFORE=$(awk '/RELEASE_GATE_CHECK_ONLY:-0/ {exit} /echo "error:/ {n++} END {print n+0}' \
         "$REPO/tools/release-gate.sh")
[ "$BEFORE" -ge 6 ] \
  && ok "$BEFORE of the gate's $REFUSALS refusals are reachable without an \
image, and this file drives 11 of them" \
  || bad "the gate's checks moved: only $BEFORE refusals now come before the \
check-only seam, so this suite is measuring less than it says"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
