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
#
# ONE GCOV RUN PER OBJECT, MERGED, and both halves of that had to be repaired
# before the number above meant anything.
#
#   gcov writes `bpa_pool.h.gcov` into the directory it runs in, so running it
#   once per .gcda in one directory leaves the LAST object's counters standing
#   for every source built into more than one harness. bpa_pool.c is compiled
#   into test_bpa and into test_bpa_dyn, test_bpa_dyn sorts last, and this
#   reported bpa_pool.c's coverage as test_bpa_dyn's alone - listing all
#   fourteen of bpa_addr_set_build() and bpa_pool_init()'s refusals as never
#   executed. Ten of them are driven by test/test_bpa_pool.c and have been for
#   as long as that file has existed. Each object's output now goes to a
#   directory of its own and a line is "never" only when NO object ran it.
#
#   And gcov emits one ROW PER INLINED INSTANCE of a line in a header, marking
#   a count with `*` when some instance did not run it. Counting rows made a
#   1248-line header report 1251 executable lines, and counting a `*` row as
#   never-executed made lines that ran in nine harnesses and not the tenth look
#   dead. Lines are counted once, by number, and a `*` count is a count.
#
#   gcov RUNS FROM THE REPOSITORY ROOT, and must. The .gcno records the source
#   path as it was given to the compiler, which is relative, so gcov started
#   anywhere else cannot find the source and writes a four-line stub with no
#   counters in it - which reads exactly like a file nothing executed.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
OUT=$REPO/.build
LOG=${1:-$REPO/.coverage-run.log}

echo "==> building the C harnesses with coverage, into .build"
rm -f "$OUT"/*.gcda "$OUT"/*.gcno "$REPO"/*.gcov "$REPO"/*.never
( cd "$REPO" && rm -rf "$OUT" && \
  make CFLAGS="-O0 -g --coverage -Wall -Wextra -DBPA_STANDALONE" \
  test >"$LOG" 2>&1 ) || {
    echo "the instrumented run did not pass; see $LOG" >&2; exit 1; }

echo "==> reading the counters"
GCOV=$REPO/.coverage-gcov
rm -rf "$GCOV"; mkdir -p "$GCOV"
i=0
for g in "$OUT"/*.gcda; do
  [ -e "$g" ] || continue
  i=$((i + 1))
  mkdir -p "$GCOV/$i"
  ( cd "$REPO" && gcov -o "$OUT" "$g" >/dev/null 2>&1 || true )
  mv "$REPO"/*.gcov "$GCOV/$i"/ 2>/dev/null || true
done

echo
printf '%-18s %10s %10s\n' "file" "executed" "never"
total=0; never=0
for f in "$REPO"/src/plugins/bpa/*.c "$REPO"/src/plugins/bpa/*.h; do
  b=$(basename "$f")
  set -- "$GCOV"/*/"$b.gcov"
  [ -f "$1" ] || continue
  # One line per source line, across every object that compiled it: `seen` is
  # the union, `hit` the lines some object ran. The per-file list of the rest
  # goes beside the counts, because the number is only the way in.
  read -r e n <<EOF
$(awk -F: -v out="$REPO/$b.never" '
    { ln = $2 + 0; if (ln <= 0) next
      c = $1; gsub(/[ \t]/, "", c)
      sub(/\*$/, "", c)          # a partially-inlined line still ran
      if (c == "-") next
      if (c !~ /^([0-9]+[kMG]?|#####|=====|\$\$\$\$\$)$/) next
      seen[ln] = 1
      if (c ~ /^[0-9]/) hit[ln] = 1 }
    END {
      k = 0
      for (l in seen) if (!(l in hit)) a[k++] = l + 0
      for (x = 0; x < k; x++)
        for (y = x + 1; y < k; y++)
          if (a[y] < a[x]) { t = a[x]; a[x] = a[y]; a[y] = t }
      for (x = 0; x < k; x++) print a[x] > out
      m = 0; for (l in seen) m++
      print m - k, k }' "$@")
EOF
  [ "$((e + n))" -gt 0 ] || continue
  printf '%-18s %10d %10d\n' "$b" "$e" "$n"
  total=$((total + e + n)); never=$((never + n))
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
echo "  the line NUMBERS are in $REPO/<file>.never, one per line, and the"
echo "  annotated sources in $GCOV/<n>/, one directory per object."
echo
echo "==> rebuilding .build without coverage"
# Left instrumented, the next `make test` is slower and writes .gcda over
# these counters, so a second reading would be about two runs at once.
( cd "$REPO" && rm -rf "$OUT" && make test >/dev/null 2>&1 ) || {
    echo "the ordinary rebuild did not pass, so .build is left" >&2
    echo "instrumented: run 'rm -rf .build && make test'" >&2
    exit 1; }
echo "    done"
