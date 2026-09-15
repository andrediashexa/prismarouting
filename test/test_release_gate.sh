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
echo "=== and this suite reaches only the half that needs no image ==="
# Said as a number rather than left to be assumed. The gate refuses eighteen
# times; everything after RELEASE_GATE_CHECK_ONLY needs a 321 MB image built,
# booted and installed, and is measured by running a real release.
REFUSALS=$(grep -c 'echo "error:' "$REPO/tools/release-gate.sh")
BEFORE=$(awk '/RELEASE_GATE_CHECK_ONLY:-0/ {exit} /echo "error:/ {n++} END {print n+0}' \
         "$REPO/tools/release-gate.sh")
[ "$BEFORE" -ge 6 ] \
  && ok "$BEFORE of the gate's $REFUSALS refusals are reachable without an \
image, and this file drives 6 of them" \
  || bad "the gate's checks moved: only $BEFORE refusals now come before the \
check-only seam, so this suite is measuring less than it says"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
