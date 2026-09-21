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
# WHAT THIS DOES NOT REACH. The gate refuses a dozen more times after
# RELEASE_GATE_CHECK_ONLY, and every one of those needs a 321 MB image built,
# booted and installed - including the upgrade from the previous release
# (#398), which needs that release's tree compiled as well. Those stay
# unmeasured here and the count below says so out loud rather than leaving the
# reader to assume this covers the file.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail + 1)); }

# THE NEWEST MINOR TAG, not the newest tag (#410). Every case below breaks one
# thing in a tree and requires the refusal the MEDIUM path makes, and since the
# gate learned the two paths a patch tag takes the other one - where a note
# claiming nothing is correct rather than refused, and where no medium is
# built. Driven from a patch tag, this file measured the wrong procedure and
# said the gate had stopped refusing.
#
# The patch path has cases of its own, at the end.
TAG=$(git -C "$REPO" tag --sort=-v:refname 2>/dev/null \
      | awk -F. '$NF == 0 { print; exit }')
if [ -z "$TAG" ]; then
  echo "  FAIL: this repository has no minor tag, so the gate's medium path"
  echo "        cannot be driven"
  echo; echo "1 FAILURES"; exit 1
fi
PATCH_TAG=$(git -C "$REPO" tag --sort=-v:refname 2>/dev/null \
            | awk -F. '$NF != 0 { print; exit }')

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
  # The gate calls this one too, since #409. A fixture missing a tool the gate
  # invokes makes every run refuse for a reason that is about the fixture -
  # which is what happened the moment this was added and not copied.
  cp "$REPO/tools/release-digits.sh" "$FIX/tools/release-digits.sh"
}

GHBIN=$WORK/ghbin
mkdir -p "$GHBIN"
cat > "$GHBIN/gh" <<'GHSTUB'
#!/bin/sh
case "$*" in
  *"auth status"*)  exit 0 ;;
  *"issue list"*)   : ;;
  # What real gh prints for a tag with no release. The gate tells that
  # apart from an API that could not answer by reading this line, so a
  # stub that exits 1 in silence makes it report the wrong one.
  *"release view"*) echo "release not found" >&2; exit 1 ;;
  *)                exit 0 ;;
esac
GHSTUB
chmod +x "$GHBIN/gh"

n=0
run_gate() {  # <case tag>; -> $OUT and $RC
  n=$((n + 1))
  OUT=$(cd "$FIX" && PATH="$GHBIN:$PATH" \
        RELEASE_WORK="$WORK/w$n" RELEASE_GATE_CHECK_ONLY=1 \
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
OUT=$(cd "$FIX" && PATH="$GHBIN:$PATH" \
      RELEASE_WORK="$WORK/w-notag" RELEASE_GATE_CHECK_ONLY=1 \
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
# The tag BELOW this one, and not merely a different one. Sorted ascending and
# stopped at the fixture's own version: taking the newest other tag picked a
# LATER release once the medium cases moved off the newest tag (#410), and a
# later release has no section in this tree's CHANGELOG - so the case asserted
# on a heading that cannot be there and refused a correct fixture.
PREV=$(git -C "$FIX" tag -l "v*" --sort=v:refname \
       | awk -v t="v$V" '$0 == t { exit } { p = $0 } END { print p }' \
       | sed 's/^v//')
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
echo "=== and the gate reaches the published-title check ==="
# The third document verdict, and the one that cannot be driven to its refusal
# from here: it compares the title of the PUBLISHED release against the
# CHANGELOG heading, and no fixture can publish a release for its own tag.
#
# What is asserted is that the gate gets there. The check sits after two that
# have been red since 2026-09-09 - the wiki is behind - so placed behind them
# it would never execute, which is the defect that pair already had once. Its
# comparison is measured separately, over real releases: v0.74.2 accepted,
# v0.70.0 and v0.69.0 refused, both truncations that drop the second defect
# their heading names (#340).
reset_fixture
V=$(cat "$FIX/VERSION")
retag "v$V"
run_gate "v$V"
case "$OUT" in
  *"the published release title is the CHANGELOG heading"* \
  |*"does not carry the"*|*"has no published release yet"*)
    ok "it reaches the title check rather than stopping at the two before it" ;;
  *) bad "the gate never reached the published-title check, so a third \
document comparison sits behind two that have been red since September" ;;
esac

echo
echo "=== the published title, answered by a gh that has one ==="
# NOT undrivable after all. The comment above said no fixture can publish a
# release for its own tag, and that is true of GitHub and irrelevant here: the
# suite already replaces gh, so the answer a published release would give is
# one the stub can give. The comparison - the thing the check is FOR - was
# being measured only by running the gate against real releases by hand, which
# is a measurement that stops happening.
reset_fixture
V=$(cat "$FIX/VERSION")
HEADLINE=$(grep -m1 "^## $V " "$FIX/CHANGELOG.md" | sed "s/^## $V *- *//")
[ -n "$HEADLINE" ] || bad "the fixture's CHANGELOG heading has no sentence, \
so the three cases below would measure the fixture rather than the gate"
# Answering out of a file rather than out of the stub's own text, so each case
# below changes the title without rewriting the stub - and so the stub cannot
# quietly stop being the thing under test.
cat > "$GHBIN/gh" <<'GHSTUB'
#!/bin/sh
case "$*" in
  *"auth status"*)  exit 0 ;;
  *"issue list"*)   : ;;
  *"release view"*) cat "$(dirname "$0")/title" ;;
  *)                exit 0 ;;
esac
GHSTUB
chmod +x "$GHBIN/gh"
# The em dash and the version in front of it are what GitHub actually carries:
# this is also the assertion that the normalising on both sides works, because
# the CHANGELOG heading has neither.
printf '%s \342\200\224 %s\n' "$V" "$HEADLINE" > "$GHBIN/title"
retag "v$V"
run_gate "v$V"
printf '%s\n' "$OUT" | grep -q "the published release title is the CHANGELOG heading" \
  && ok "a title that is the heading is accepted, dash and version and all" \
  || bad "the gate refused a published title that IS its CHANGELOG heading, \
so the normalising is about punctuation rather than about the sentence: $OUT"

echo
echo "=== a published title that stops at the comma ==="
# The defect this check was written for: four of the eight titles measured on
# #340 dropped everything after the comma, which is where the second defect
# that release fixed is named.
#
# ON A HEADING GIVEN A COMMA HERE, not on whatever the newest release's
# heading happens to be. The cut was taken on that heading, and 0.76.1's has
# no comma: the cut title was the whole heading, the gate accepted it because
# it was right to, and this case went red measuring the shape of a sentence
# somebody wrote rather than the gate - on every branch cut after that tag,
# found by the make test of #375. The fixture's heading is rewritten the way
# the next case rewrites it, so the cut drops something on every release,
# and the guard says so if it ever does not.
reset_fixture
V=$(cat "$FIX/VERSION")
COMMA_HEAD="one thing the box said, and the check that held it"
sed -i "s/^## $V .*/## $V - $COMMA_HEAD/" "$FIX/CHANGELOG.md"
[ "${COMMA_HEAD%%,*}" != "$COMMA_HEAD" ] \
  || bad "the cut dropped nothing, so this measures the heading, not the gate"
printf '%s \342\200\224 %s\n' "$V" "${COMMA_HEAD%%,*}" > "$GHBIN/title"
retag "v$V"
run_gate "v$V"
[ "$RC" != 0 ] && printf '%s\n' "$OUT" | grep -q "does not carry the" \
  && printf '%s\n' "$OUT" | grep -q "gh release edit v$V --title" \
  && ok "refused, and it says which release and what to type to fix it" \
  || bad "a published title that drops half its heading was accepted, or was \
refused without saying what to do about it (rc=$RC): $OUT"

echo
echo "=== a heading that is a version and nothing else ==="
# This passed. The comparison was guarded on the heading being non-empty, so a
# heading with no sentence left nothing to compare and the gate printed the
# green line having compared nothing - the same defect the '## VER' section
# extraction refuses in words further up, in the same file.
reset_fixture
V=$(cat "$FIX/VERSION")
sed -i "s/^## $V .*/## $V/" "$FIX/CHANGELOG.md"
printf '%s \342\200\224 %s\n' "$V" "$HEADLINE" > "$GHBIN/title"
retag "v$V"
run_gate "v$V"
[ "$RC" != 0 ] && printf '%s\n' "$OUT" | grep -q "carries no" \
  && ok "refused, and it says the heading has no sentence to compare against" \
  || bad "a heading with no sentence at all was accepted, so the check passed \
having compared nothing (rc=$RC): $OUT"

echo
echo "=== gh cannot answer, and gh answering an error, are not the same ==="
# Both used to arrive as the empty string, because the call ended in `|| true`.
# One of them was then ANNOUNCED as "no published release yet", which is a
# false statement about a release that exists, made at the moment somebody is
# deciding whether to publish. The other printed nothing at all.
reset_fixture
V=$(cat "$FIX/VERSION")
retag "v$V"
cat > "$GHBIN/gh" <<'GHSTUB'
#!/bin/sh
exit 1
GHSTUB
chmod +x "$GHBIN/gh"
run_gate "v$V"
printf '%s\n' "$OUT" | grep -q "release title was NOT checked: gh cannot answer" \
  && ok "gh that will not authenticate is said out loud, not passed in silence" \
  || bad "with gh unable to answer the title check printed nothing, so its \
verdict cannot be told from the run where it passed: $OUT"

reset_fixture
V=$(cat "$FIX/VERSION")
retag "v$V"
cat > "$GHBIN/gh" <<'GHSTUB'
#!/bin/sh
case "$*" in
  *"auth status"*)  exit 0 ;;
  *"issue list"*)   : ;;
  *"release view"*) echo "error connecting to api.github.com" >&2; exit 1 ;;
  *)                exit 0 ;;
esac
GHSTUB
chmod +x "$GHBIN/gh"
run_gate "v$V"
printf '%s\n' "$OUT" | grep -q "could not read v$V" \
  && ! printf '%s\n' "$OUT" | grep -q "has no published release yet" \
  && ok "an API that could not answer is not reported as nothing published" \
  || bad "an API failure was announced as 'no published release yet', which \
is false about a release that exists: $OUT"

# Back to the stub the rest of the file runs under. "release not found" on
# stderr is what real gh prints for a tag with no release, and it is what
# tells that apart from an API that could not answer - a stub that exits 1
# saying nothing makes the gate report the wrong one of the two.
cat > "$GHBIN/gh" <<'GHSTUB'
#!/bin/sh
case "$*" in
  *"auth status"*)  exit 0 ;;
  *"issue list"*)   : ;;
  *"release view"*) echo "release not found" >&2; exit 1 ;;
  *)                exit 0 ;;
esac
GHSTUB
chmod +x "$GHBIN/gh"

echo
echo "=== a refused release says it was refused, and by how many ==="
# The three document checks print their refusals on stderr and their passes on
# stdout, so a refused run read through its stdout alone - a log, a pipe, a
# tail - used to end on "==> the published release title is the CHANGELOG
# heading". A green line under a release that was refused.
#
# Driven through the tracker rather than the wiki, because an open issue the
# roadmap never mentions is one line of stub output, and because it exercises
# the count: the flag this replaced was set to 1 by each of three checks, so a
# run that failed all three and a run that failed one exited identically.
reset_fixture
V=$(cat "$FIX/VERSION")
retag "v$V"
cat > "$GHBIN/gh" <<'GHSTUB'
#!/bin/sh
case "$*" in
  *"auth status"*)  exit 0 ;;
  *"issue list"*)   echo "9999 OPEN" ;;   # a roadmap that never names it
  *"release view"*) echo "release not found" >&2; exit 1 ;;
  *)                exit 0 ;;
esac
GHSTUB
chmod +x "$GHBIN/gh"
# THE COUNT IS READ OFF THE RUN, not written here. It was `2`, on the argument
# that this fixture is not a git checkout whose history records a wiki pull so
# the wiki check is red "whatever else happens" - and that stopped being true
# the day the wiki was published, which is the day the product got better. The
# case then failed on a gate that was working, saying it had refused and never
# said so when it had said so about a different number.
#
# That is the shape test/test_wiki_roundtrip.sh was repaired for in this same
# release: a check whose green depends on a product defect still being there.
# What this case is actually about is that the SUMMARY AGREES WITH THE RUN, so
# it counts the document checks that really refused - each prints its own
# `error:` line - and demands the summary say that number. It goes red if the
# gate miscounts in either direction, and it does not care which checks the
# fixture happens to make red.
run_gate "v$V"
BASE_SAID=$OUT
# Every document refusal opens with `error: ` at column 0; so does the summary,
# which is excluded by name. `grep -c` over the whole run would also catch the
# indented continuation lines, so the anchor matters.
_reds=$(printf '%s\n' "$OUT" \
  | grep -c "^error: " || true)
_reds=$((_reds - 1))
case "$OUT" in
  *"$_reds of the four document checks above refused this"*)
    ok "it says how many refused it, and the number is the one the run \
produced: $_reds" ;;
  *) bad "the gate refused and never said so, or said a number the run does \
not support ($_reds refusal(s) printed); it ended on:
$(printf '%s' "$OUT" | grep -A1 'document checks above' | head -2)" ;;
esac
[ "$_reds" -ge 1 ] \
  && ok "and at least one document check really did refuse, so this case is \
not asserting about a clean run" \
  || bad "no document check refused at all, so the summary above was measured \
against nothing - the fixture no longer breaks anything"
[ "$RC" != 0 ] \
  && ok "and the refusal is the exit status too" \
  || bad "the gate exited 0 with a document check red (rc=$RC)"

echo
echo "=== a tracker that answered nothing is a SKIP, not a refusal ==="
# THE DIFFERENCE BETWEEN 1 AND 2, which this gate read as the same thing until
# a second holder was added beside the first. Both holders exit 1 for findings
# and 2 for "nothing was compared", and both say in their own headers that a
# document nobody compared reads exactly like one that agreed - which is why
# neither may exit 0 without an answer. The gate read any non-zero as
# disagreement, so a tracker that answered with an empty list REFUSED THE
# RELEASE: the outcome the gate's own comment argues against, three lines from
# where it is argued.
#
# Driven with a stub that answers `issue list` with nothing at all. That is
# not a contrived state: it is what an expired token, a repository rename and
# a rate limit answered through a wrapper all look like from here.
reset_fixture
V=$(cat "$FIX/VERSION")
retag "v$V"
cat > "$GHBIN/gh" <<'GHSTUB'
#!/bin/sh
case "$*" in
  *"auth status"*)  exit 0 ;;
  *"issue list"*)   : ;;                  # authenticated, and answers nothing
  *"release view"*) echo "release not found" >&2; exit 1 ;;
  *)                exit 0 ;;
esac
GHSTUB
chmod +x "$GHBIN/gh"
run_gate "v$V"
printf '%s\n' "$OUT" | grep -q "the roadmap was NOT checked against the tracker, and it says why" \
  && ok "the roadmap check says it did not happen" \
  || { bad "an empty tracker answer was not reported as a comparison that \
did not happen:"
     printf '%s\n' "$OUT" | sed 's/^/    /' | head -12; }
printf '%s\n' "$OUT" | grep -q "the CHANGELOG was NOT checked against the tracker, and it says why" \
  && ok "and so does the CHANGELOG check" \
  || bad "the CHANGELOG check did not say the comparison did not happen"
# THE ASSERTION IS THAT NEITHER SKIP WAS COUNTED, and it is written so it does
# not depend on how many OTHER checks this fixture happens to break. It used to
# read "1 of the four", on the same assumption as the case above - that the
# wiki check is red here whatever happens - and that assumption died the day
# the wiki was published.
#
# Two skips fired. So the summary must name exactly the number of checks that
# printed a refusal of their own, and if none did there is no summary at all,
# which is the same statement said by its absence. Either way a skip counted as
# a refusal makes the number one too many and this goes red.
_reds=$(printf '%s\n' "$OUT" | grep -c "^error: " || true)
_said=$(printf '%s\n' "$OUT" | grep -c "document checks above refused" || true)
if [ "$_said" = 0 ]; then
  [ "$_reds" = 0 ] \
    && ok "no document check refused, so there is no summary and neither skip \
was counted" \
    || bad "$_reds refusal(s) were printed and the gate summarised none"
else
  _reds=$((_reds - 1))
  case "$OUT" in
    *"$_reds of the four document checks above refused this"*)
      ok "neither skip is counted among the refusals: $_reds refused, $_reds \
summarised" ;;
    *) bad "a skip was counted as a refusal; $_reds check(s) printed one and \
the gate said:
$(printf '%s' "$OUT" | grep 'document checks above' | head -1)" ;;
  esac
fi
# TWO, and the number is the point. This replaced a flag that each of three
# checks set to 1, so a run with all three red and a run with one red exited
# identically. Two is what this fixture produces - its history records no wiki
# pull, and its roadmap does not name the open issue the stub invents - and a
# `docs_bad=1` in place of the increment takes this case red while leaving
# every other case in this file green.
#
# Back to the stub the rest of the file runs under.
cat > "$GHBIN/gh" <<'GHSTUB'
#!/bin/sh
case "$*" in
  *"auth status"*)  exit 0 ;;
  *"issue list"*)   : ;;
  *"release view"*) echo "release not found" >&2; exit 1 ;;
  *)                exit 0 ;;
esac
GHSTUB
chmod +x "$GHBIN/gh"

echo
echo "=== a patch release: the two refusals the other path cannot make ==="
# THE RULE HAS A READER NOW (#410). A Z publishes the artifact and no medium,
# and the two ways of getting that wrong are both invisible afterwards: a note
# that claims a boot nobody performed, and a patch carrying a change the
# artifact cannot deliver. The first is the likelier of the two, because a note
# is written by copying the last one.
#
# The fixture's patch tag is built on top of the minor one, so the gate sees
# the same shape a real patch release has: the same minor, a higher last digit.
reset_fixture
PV=$(cat "$FIX/VERSION")
PATCHV="${PV%.*}.$(( ${PV##*.} + 1 ))"
printf '%s\n' "$PATCHV" > "$FIX/VERSION"
python3 - "$FIX/CHANGELOG.md" "$PATCHV" <<'PYE'
import sys
p, ver = sys.argv[1], sys.argv[2]
t = open(p, encoding="utf-8").read()
# The WHOLE open block, not its heading: leaving the Added and Fixed that
# follow Unreleased in place puts them inside the new section, which is a
# section saying Fixed twice - and the gate refuses that, correctly, for a
# reason this case is not about.
head = "## Unreleased\n\n### Added\n\n### Fixed\n"
assert head in t, "the fixture has no open Unreleased block to work from"
t = t.replace(head, head + "\n## " + ver + " - a patch of this suite's own"
              + "\n\n### Fixed\n\n* a line, so the section is not empty\n",
              1)
open(p, "w", encoding="utf-8").write(t)
PYE
git -C "$FIX" add -A
git -C "$FIX" commit -q -m "a patch release"
# The note a correct patch release carries: it claims the suites and nothing
# about a medium.
git -C "$FIX" tag -f -a "v$PATCHV" -m "a patch of this suite's own

make test: green. No box was installed." >/dev/null 2>&1
run_gate "v$PATCHV"
[ "$RC" = 0 ] \
  && ok "a correct patch release is accepted, with no medium claimed" \
  || bad "a correct patch release was refused (rc=$RC): $OUT"
printf '%s\n' "$OUT" | grep -q "moves only the patch digit" \
  && ok "and the gate says which path it took" \
  || bad "the gate did not say it was on the patch path: $OUT"

# THE NOTE THAT CLAIMS A BOOT. Same tree, same version, a note copied from a
# minor release.
git -C "$FIX" tag -f -a "v$PATCHV" -m "a patch of this suite's own

Built, booted, installed to a disk and booted again." >/dev/null 2>&1
run_gate "v$PATCHV"
[ "$RC" != 0 ] \
  && printf '%s\n' "$OUT" | grep -q "claims the medium was built" \
  && ok "a patch whose note claims a boot is refused" \
  || bad "a patch release claimed a boot nobody performed and the gate took \
it (rc=$RC): $OUT"

# THE CHANGE THE ARTIFACT CANNOT CARRY. The installer only runs from the
# medium, so a box that took this patch would not have it and nothing would
# say so.
printf '\n# a change only the medium carries\n' >> "$FIX/appliance/build-iso.sh"
git -C "$FIX" add -A
git -C "$FIX" commit -q -m "a patch that touches the medium"
git -C "$FIX" tag -f -a "v$PATCHV" -m "a patch of this suite's own

make test: green. No box was installed." >/dev/null 2>&1
run_gate "v$PATCHV"
[ "$RC" != 0 ] \
  && printf '%s\n' "$OUT" | grep -q "exist only on the medium" \
  && ok "a patch touching the ISO build is refused, and named" \
  || bad "a patch changed the medium and the gate took it (rc=$RC): $OUT"
reset_fixture

echo
echo "=== and this suite reaches only the half that needs no image ===""
# Said as a number rather than left to be assumed, and the number is COUNTED
# three lines down rather than written here: it was "the gate refuses eighteen
# times" sitting directly above the code that computes it, and the gate has
# refused twenty-four times since. A count in prose beside a count in code is
# the second copy this repository keeps paying for, and this one had the two
# touching.
#
# Everything after RELEASE_GATE_CHECK_ONLY needs a 321 MB image built, booted
# and installed, and is measured by running a real release.
REFUSALS=$(grep -c 'echo "error:' "$REPO/tools/release-gate.sh")
BEFORE=$(awk '/RELEASE_GATE_CHECK_ONLY:-0/ {exit} /echo "error:/ {n++} END {print n+0}' \
         "$REPO/tools/release-gate.sh")
[ "$BEFORE" -ge 6 ] \
  && ok "$BEFORE of the gate's $REFUSALS refusals are reachable without an \
image, and this file drives 16 of them" \
  || bad "the gate's checks moved: only $BEFORE refusals now come before the \
check-only seam, so this suite is measuring less than it says"

echo
echo "=== step: the clock the gate's log is read as a table by (#408) ==="
# Cut out of the script and called alone, the way
# test/test_build_iso_preflight.sh cuts say() out of build-iso.sh: everything
# after the first line of the gate refuses anyone who is not standing on a
# tag, so the function cannot be reached by running it.
#
# THE FIRST STEP PRINTS NO TIME and the ones after it do. A zero on the first
# would read as a step that took none, when what it means is that there is
# nothing to subtract from yet - and docs/build-timing.md is read as a table,
# where a wrong zero in the first row is a wrong total.
sed -n '/^_STEP_LAST=/,/^}/p; /^step_done()/,/^}/p' \
  "$REPO/tools/release-gate.sh" > "$WORK/step.sh"
first=$(. "$WORK/step.sh"; step "building the medium")
[ "$first" = "==> building the medium" ] \
  && ok "the first step prints the step and no seconds" \
  || bad "step gave '$first' for the first call"
# THE DURATION UNDER THE NAME THAT SPENT IT, which is the whole value of this
# clock. A step is announced BEFORE its work runs, so the first version - which
# printed the seconds on the new step's line - charged every number to the
# label after the one it belonged to. The gate's own first run said
# `building the medium (120s)` when 120s was the startup.conf check and the
# medium had not started, and docs/build-timing.md is a table somebody reads
# to decide what to optimise.
later=$(. "$WORK/step.sh"; step "one"; sleep 1; step "two")
case "$later" in
  *"one: 1s"*|*"one: 2s"*)
    ok "the seconds are printed under the step that spent them" ;;
  *) bad "step gave '$later' for two steps a second apart" ;;
esac
case "$later" in
  *"two: "*) bad "step charged time to a step that had not run: '$later'" ;;
  *) ok "and the step that has not run yet is charged nothing" ;;
esac
last=$(. "$WORK/step.sh"; step "only"; sleep 1; step_done)
case "$last" in
  *"only: 1s"*|*"only: 2s"*) ok "and the last step is timed by step_done" ;;
  *) bad "step_done gave '$last'" ;;
esac
# NOT RECURSIVE, which is not a hypothetical: the change that added this
# function rewrote every `echo "==> ` in the file, including the one INSIDE
# it, and the suite found a step() that called itself.
grep -q 'printf' "$WORK/step.sh" \
  && ok "and it prints rather than calling itself, which the first version did" \
  || bad "step does not print: $(cat "$WORK/step.sh")"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
