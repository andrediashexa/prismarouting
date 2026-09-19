#!/bin/sh
# tools/release-titles.sh, driven over the titles it must refuse.
#
# WHY IT NEEDED ONE. That tool reads the published releases, and until #402
# the only way to watch a refusal of its fire was to publish a release with
# the defect in it. One was: 0.101.0 went out titled with the sentence alone,
# "the words a configuration level owns", where all 104 before it carry the
# version as well. The tool passed it - it STRIPS the version before
# comparing, so a title with none normalises to exactly the same string as one
# that has it - and the maintainer found it on the releases page.
#
# A reader nothing drives is a reader nobody has seen refuse. So this hands it
# a table of releases instead of GitHub and requires the refusal, case by
# case, against a CHANGELOG this file writes itself.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail + 1)); }

# A tree of this file's own: the tool reads CHANGELOG.md out of its repository
# root, so the headings it compares against are written here rather than taken
# from the real file. A case anchored on a real release would go red the day
# that release is re-titled for a reason of its own.
mkdir -p "$WORK/tools"
cp "$REPO/tools/release-titles.sh" "$WORK/tools/"
cat > "$WORK/CHANGELOG.md" <<'EOF'
# Changelog

## 9.3.0 - the words a level owns

## 9.2.0 - the gate takes the upgrade an operator takes

## 9.1.0 - foundations
EOF

run() {   # <table rows on stdin>; prints the tool's output, sets RC
  printf '%s\n' "$1" > "$WORK/titles.tsv"
  set +e
  OUT=$(cd "$WORK" && PRISMA_RELEASE_TITLES="$WORK/titles.tsv" \
        sh tools/release-titles.sh 2>&1)
  RC=$?
  set -e
}

TAB=$(printf '\t')

echo "=== every shape the convention allows is accepted ==="
run "v9.3.0${TAB}9.3.0 - the words a level owns
v9.2.0${TAB}v9.2.0 — the gate takes the upgrade an operator takes
v9.1.0${TAB}Prisma Routing 9.1.0 - foundations"
if [ "$RC" = 0 ]; then
  ok "the version bare, with a v, and behind the product name"
else
  bad "a table of correct titles was refused: $OUT"
fi
case "$OUT" in
  *"3 published release(s)"*) ok "and all three were read, not skipped" ;;
  *) bad "the count is not three: $OUT" ;;
esac

echo
echo "=== a title that is only the sentence is refused (#402) ==="
# THE ONE THAT SHIPPED. Before #402 this case passed.
run "v9.3.0${TAB}the words a level owns"
if [ "$RC" != 0 ]; then
  ok "a title carrying no version is a defect, not a style"
else
  bad "a title with no version was accepted: $OUT"
fi
case "$OUT" in
  *"does not begin with the version"*) ok "and the reason names the shape" ;;
  *) bad "the refusal does not say what is wrong: $OUT" ;;
esac
case "$OUT" in
  *"the words a level owns"*) ok "and quotes the title as published" ;;
  *) bad "the refusal does not quote the title: $OUT" ;;
esac

echo
echo "=== a title shorter than its heading is still refused ==="
# The check this file has always done, kept under the new one: a truncated
# title announces less than the release fixed.
run "v9.2.0${TAB}9.2.0 - the gate"
if [ "$RC" != 0 ]; then
  ok "a truncated sentence is refused with the version in place"
else
  bad "a truncated title was accepted: $OUT"
fi

echo
echo "=== a title that EXTENDS its heading is accepted ==="
# 0.2.0 is why: "foundations (superseded: VTY does not start)" is a fact about
# that release the CHANGELOG does not carry.
run "v9.1.0${TAB}9.1.0 - foundations (superseded: the VTY does not start)"
if [ "$RC" = 0 ]; then
  ok "a title that says MORE than the heading is not a defect"
else
  bad "an extended title was refused: $OUT"
fi

echo
echo "=== a tag with no heading is skipped, and not counted ==="
# A release published from a tag this CHANGELOG has never named. Skipped on
# purpose - the tool has nothing to compare - but it must not be counted as
# one that agrees, which would be the count growing while the reading does not.
run "v8.0.0${TAB}whatever this was
v9.1.0${TAB}9.1.0 - foundations"
case "$OUT" in
  *"1 published release(s)"*) ok "one read, one skipped" ;;
  *) bad "a tag with no heading changed the count: $OUT" ;;
esac

echo
echo "=== a table it cannot read is an error, not a pass ==="
set +e
OUT=$(cd "$WORK" && PRISMA_RELEASE_TITLES="$WORK/nothing-here.tsv" \
      sh tools/release-titles.sh 2>&1)
RC=$?
set -e
if [ "$RC" = 2 ]; then
  ok "a missing table exits 2, the way a missing gh does"
else
  bad "a missing table did not exit 2 (rc=$RC): $OUT"
fi

echo
if [ "$fail" = 0 ]; then
  echo "ALL TESTS PASSED"
else
  echo "$fail FAILURES"
  exit 1
fi
