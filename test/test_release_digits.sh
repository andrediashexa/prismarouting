#!/bin/sh
# tools/release-digits.sh, driven over the shapes it has to refuse.
#
# WHY THIS FILE. The reader asks GitHub what is published, so the only way to
# watch a refusal fire is to publish a release with the defect - which is
# exactly the trap its sibling was caught in: tools/release-titles.sh went a
# whole release cycle with a rule nothing had ever driven, and the release it
# failed to catch (#402) is the one that shipped. PRISMA_RELEASE_DIGITS is the
# way in, and this file is what uses it.
#
# The rows are "<tag>\t<true|false>", which is what `gh release list` gives.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
fail=0
ok()  { echo "  [ ok ] $1"; }
bad() { echo "  FAIL: $1"; fail=$((fail + 1)); }

run() {  # <rows> -> prints, sets RC
  printf '%s\n' "$1" > "$WORK/rows.tsv"
  OUT=$(PRISMA_RELEASE_DIGITS="$WORK/rows.tsv" \
        sh "$REPO/tools/release-digits.sh" 2>&1)
  RC=$?
}

echo "=== a patch published as a full release is refused ==="
run "v0.110.1	false"
[ "$RC" != 0 ] && ok "refused, exit $RC" || bad "accepted: $OUT"
case "$OUT" in
  *"v0.110.1"*"FULL release"*) ok "and it names the tag and what it was" ;;
  *) bad "the refusal does not name it: $OUT" ;;
esac
case "$OUT" in
  *"never booted"*) ok "and says what it costs an operator, not just that it is wrong" ;;
  *) bad "the refusal says no consequence: $OUT" ;;
esac

echo
echo "=== and a minor published as a pre-release, which is the other way ==="
# The mirror image, and it is not hypothetical: it leaves Latest on an older
# medium, so the page an operator lands on offers the wrong ISO.
run "v0.111.0	true"
[ "$RC" != 0 ] && ok "refused, exit $RC" || bad "accepted: $OUT"
case "$OUT" in
  *"PRE-RELEASE"*) ok "and names which direction it went wrong" ;;
  *) bad "the refusal does not distinguish the two: $OUT" ;;
esac

echo
echo "=== the shapes that are RIGHT are not refused ==="
run "v0.111.0	false
v0.110.3	true
v0.110.0	false
v0.109.1	true"
[ "$RC" = 0 ] && ok "four correct releases pass" || bad "refused correct ones: $OUT"
case "$OUT" in
  *"4 release(s) are bound"*) ok "and it says how many it judged" ;;
  *) bad "it does not say how many: $OUT" ;;
esac

echo
echo "=== history is counted, never refused ==="
# The rule was decided on 2026-09-20 and shipped in 0.109.0. Nine patches
# before it were published as full releases and none of them broke a rule that
# did not exist. A check that fails on history is a check somebody turns off.
run "v0.77.1	false
v0.2.1	false
v0.111.0	false"
[ "$RC" = 0 ] && ok "two pre-rule patches do not fail the run" \
              || bad "history was refused: $OUT"
case "$OUT" in
  *"2 patch release(s) before it"*) ok "and they are counted out loud" ;;
  *) bad "history is invisible: $OUT" ;;
esac

echo
echo "=== a tag it cannot parse is one it says nothing about ==="
# Not a guess and not a refusal: this repository has tags that are not
# versions, and a reader that judged them would be inventing a rule.
run "not-a-version	false
v0.111.0	false"
[ "$RC" = 0 ] && ok "an unparseable tag is skipped rather than judged" \
              || bad "it judged a tag it cannot read: $OUT"
case "$OUT" in
  *"1 release(s) are bound"*) ok "and it is not counted among the judged" ;;
  *) bad "the count includes something it did not read: $OUT" ;;
esac

echo
echo "=== gh answering nothing is a SKIP, not a refusal ==="
# The same rule tools/release-titles.sh and the gate's own doc checks follow:
# a release page this cannot read is not one it can judge, and refusing the
# release because GitHub was down is a gate somebody learns to argue with.
run ""
[ "$RC" = 0 ] && ok "nothing to read is not a failure" || bad "refused on silence: $OUT"
case "$OUT" in
  *"NOT checked"*) ok "and it says so, rather than passing quietly" ;;
  *) bad "a silent pass is indistinguishable from a real one: $OUT" ;;
esac

echo
echo "=== rows: the escape hatch, on its own ==="
# Cut out and called alone, the way test_build_iso_preflight.sh cuts say() out
# of build-iso.sh. It is the one function in this reader that decides WHERE
# the answer comes from, and everything above depends on it choosing the file
# over the network - a rows() that asked GitHub anyway would make every box in
# this file measure the real release page instead of the fixture.
sed -n '/^rows() {/,/^}/p' "$REPO/tools/release-digits.sh" > "$WORK/rows.sh"
printf 'v0.9.9\ttrue\n' > "$WORK/given.tsv"
got=$(. "$WORK/rows.sh"; export PRISMA_RELEASE_DIGITS="$WORK/given.tsv"; rows)
[ "$got" = "$(printf 'v0.9.9\ttrue')" ] \
  && ok "with PRISMA_RELEASE_DIGITS set, rows reads the file and not GitHub" \
  || bad "rows gave '$got'"
# And with it unset it reaches for gh. Asserted by the COMMAND it runs rather
# than by the answer, because a machine with no gh and a machine with an empty
# release page are the same thing to this reader - which is deliberate, and is
# what the skip box above is about.
grep -q 'gh release list' "$WORK/rows.sh" \
  && ok "and without it, gh release list is what it asks" \
  || bad "rows does not reach for gh at all: $(cat "$WORK/rows.sh")"

echo
if [ "$fail" = 0 ]; then echo "ALL TESTS PASSED"; exit 0; fi
echo "$fail FAILURES"; exit 1
