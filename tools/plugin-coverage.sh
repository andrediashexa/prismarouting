#!/bin/sh
# Which lines of the BPA plugin the docker-free suites actually execute.
#
#   sh tools/plugin-coverage.sh
#
# Nothing measured this. The ten C harnesses run 65,376 checks and their
# verdict is a count of checks, not of product reached - so a guard nobody
# enters looks exactly like one everybody enters, and the first measurement
# found twelve such lines in bpa_dyn.c alone: the whole stride placement arm,
# with both of its counters, behind `p->block_stride != 0`. Every pool those
# harnesses built had stride 0, because the one function that makes them
# memsets and never sets one.
#
# IT IS NOT A PASS OR A FAIL. A line nobody executes is a question - is it
# measured somewhere more expensive, is it reachable at all, is it dead - and
# the answers differ per line. lab/extblocks.sh and lab/dynamic-workers.sh do
# reach that stride arm, so it was measured only where measuring costs a
# container and a release run. Printing the count and leaving the judgement to
# a reader is the honest shape; a threshold here would be a number nobody
# could defend.
#
# IT BUILDS INTO .build, and puts it back. The first version built into a
# directory of its own so as not to disturb anything - but test/suites.list
# names `.build/test_bpa` literally, so the instrumented binaries were never
# the ones that ran and the counters came back empty. An isolation that cannot
# be had is worse than none: it measured nothing and said so as a number.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
OUT=$REPO/.build
LOG=${1:-$REPO/.coverage-run.log}

echo "==> building the C harnesses with coverage, into .build"
rm -f "$OUT"/*.gcda "$OUT"/*.gcno "$REPO"/*.gcov
( cd "$REPO" && rm -rf "$OUT" && \
  make CFLAGS="-O0 -g --coverage -Wall -Wextra -DBPA_STANDALONE" \
  test >"$LOG" 2>&1 ) || {
    echo "the instrumented run did not pass; see $LOG" >&2; exit 1; }

echo "==> reading the counters"
( cd "$REPO" && for g in "$OUT"/*.gcda; do
    [ -e "$g" ] || continue
    gcov -o "$OUT" "$g" >/dev/null 2>&1 || true
  done )

echo
printf '%-18s %10s %10s\n' "file" "executed" "never"
total=0; never=0
for f in "$REPO"/src/plugins/bpa/*.c "$REPO"/src/plugins/bpa/*.h; do
  b=$(basename "$f")
  g="$REPO/$b.gcov"
  [ -f "$g" ] || continue
  t=$(grep -cE '^ *[0-9#-]+:' "$g" || true)
  n=$(grep -cE '^ *#####:' "$g" || true)
  [ "$t" -gt 0 ] || continue
  printf '%-18s %10d %10d\n' "$b" "$((t - n))" "$n"
  total=$((total + t)); never=$((never + n))
done
if [ "$total" -eq 0 ]; then
  echo
  echo "error: no counters were read at all, so this measured nothing." >&2
  echo "       The instrumented build put its .gcda somewhere this did not" >&2
  echo "       look, or gcov could not match them to a source. A count of" >&2
  echo "       zero here reads exactly like a plugin with no code in it." >&2
  exit 1
fi
echo
echo "  $((total - never)) of $total executable lines reached, $never never executed."
echo "  A line nobody executes is a question, not a verdict: it may be"
echo "  measured by a lab, unreachable outside VPP, or a gap. Read them."
echo
echo "  the lines themselves are in $REPO/*.gcov"
echo
echo "==> rebuilding .build without coverage"
# Left instrumented, the next `make test` is slower and writes .gcda over
# these counters, so a second reading would be about two runs at once.
( cd "$REPO" && rm -rf "$OUT" && make test >/dev/null 2>&1 ) || {
    echo "the ordinary rebuild did not pass, so .build is left" >&2
    echo "instrumented: run 'rm -rf .build && make test'" >&2
    exit 1; }
echo "    done"
