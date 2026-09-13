#!/bin/sh
# Runs every unit suite, and says which one stopped and how far it got.
#
# `make test` used to be twenty-two recipe lines. make stops at the first one
# that exits non-zero and prints
#
#   make: *** [Makefile:38: test] Error 1
#
# which names a line in the Makefile rather than a suite, says nothing about
# the twenty-one others, and - the part that mattered - cannot tell a suite
# that FAILED an assertion from a suite that never reached one. Both are a
# non-zero exit under a pile of output.
#
# #218 was two `make test` failures under a release lab suite that nobody
# could name, for exactly that reason. The only evidence kept was "summary
# lines=10 instead of 19", which says a run stopped somewhere without saying
# where, and the line naming the check was thrown away by the tool that
# noticed it. Counting is not reading.
#
# So this reports the states tools/run-all-labs.sh already separates for the
# labs, and for the same reason:
#
#   pass          ran to its end, every check green
#   FAIL          ran to its end, named checks red
#   NOT MEASURED  ran to its end, and said the machine could not time it
#   NO VERDICT    never reached its end: killed, crashed, or could not start
#
# NO VERDICT is the one that did not exist. A suite reaches its end by
# printing a verdict line; a suite that exits without one has not been read,
# it has been interrupted, and the report says which one and how far it got
# rather than leaving the reader to count summary lines.
#
# Every suite runs. A run that stopped at the first red told the reader
# nothing about the nineteen behind it, and on a machine under load the
# question "what else did this cost" is the whole question.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
LIST=${1:-$REPO/test/suites.list}

STDBUF=$(command -v stdbuf >/dev/null 2>&1 && echo "stdbuf -oL" || true)

pass=0; fail=0; nomeasure=0; noverdict=0
OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT

# Comments and blank lines out, so the list can explain itself.
sed 's/#.*//' "$LIST" | sed '/^[[:space:]]*$/d' > "$OUT/list"
total=$(wc -l < "$OUT/list")

i=0
while IFS= read -r cmd; do
  i=$((i + 1))
  # The name is the last word's basename without its extension: the list is
  # commands, not a table, so adding a suite stays one line.
  for w in $cmd; do last=$w; done
  name=$(basename "$last"); name=${name%.*}

  printf '=== [%d/%d] %s ===\n' "$i" "$total" "$name"
  # rc through a file because the left side of a pipe is a subshell. tee so a
  # suite that hangs is still readable while it hangs - the capture alone
  # would show nothing until it ended, and a suite that never ends is one of
  # the states this file exists to report.
  #
  # $cmd is deliberately unquoted: the list holds "python3 test/x.py", which
  # has to split into a command and its argument.
  #
  # Unbuffered, and that is not cosmetic. stdout is a pipe here, so libc and
  # CPython both switch to block buffering, and a suite killed part way loses
  # everything it had printed - the report then says "exit 137 after 1 lines"
  # over a suite that had run four hundred checks. "How far did it get" is the
  # whole answer this file exists to give, so it has to be true. stdbuf covers
  # the C suites, PYTHONUNBUFFERED the python ones, which CPython buffers
  # itself and stdbuf cannot reach.
  ( cd "$REPO" && PYTHONUNBUFFERED=1 $STDBUF $cmd 2>&1; echo "$?" > "$OUT/rc" ) \
    | tee "$OUT/out"
  rc=$(cat "$OUT/rc")
  echo

  # The last verdict line the suite printed. Last rather than first: a suite
  # that prints the word inside a check's description has not ended there.
  verdict=$(grep -E '^(ALL TESTS PASSED|[0-9]+ FAILURES|[0-9]+ NOT MEASURED)[[:space:]]*$' \
              "$OUT/out" | tail -1)
  case "$verdict" in
    "ALL TESTS PASSED")
      if [ "$rc" -eq 0 ]; then
        printf 'pass\n%s\n' "$name" >> "$OUT/verdicts"; pass=$((pass + 1))
      else
        # It said it passed and then did not. Whatever that is, it is not a
        # pass, and the exit status is the half a reader would never see.
        printf 'FAIL - said it passed and exited %s\n%s\n' "$rc" "$name" \
          >> "$OUT/verdicts"; fail=$((fail + 1))
      fi ;;
    *FAILURES)
      printf 'FAIL - %s\n%s\n' "$verdict" "$name" >> "$OUT/verdicts"
      fail=$((fail + 1)) ;;
    *"NOT MEASURED")
      printf 'NOT MEASURED - %s\n%s\n' "$verdict" "$name" >> "$OUT/verdicts"
      nomeasure=$((nomeasure + 1)) ;;
    *)
      # How far it got, because that is the question a stopped run leaves.
      #
      # The last TWO lines, not one: when a shell reaps a killed child it adds
      # "Killed" of its own, and that would be the whole answer to "where did
      # it stop" while the line that says where is the one above it.
      lines=$(wc -l < "$OUT/out")
      tail_lines=$(sed '/^[[:space:]]*$/d' "$OUT/out" | tail -2 \
                     | sed 's/^[[:space:]]*//' | paste -sd'|' -)
      printf 'NO VERDICT - exit %s after %s lines, last: %s\n%s\n' \
        "$rc" "$lines" "$tail_lines" "$name" >> "$OUT/verdicts"
      noverdict=$((noverdict + 1)) ;;
  esac
done < "$OUT/list"

echo "=============================================================="
printf '  %s suites: %s passed, %s failed, %s not measured, %s with no verdict\n' \
  "$total" "$pass" "$fail" "$nomeasure" "$noverdict"

# Only what was not a pass, and each with the line that says why. A report
# that repeats twenty green names buries the one that is not.
if [ $((fail + nomeasure + noverdict)) -gt 0 ]; then
  echo
  while IFS= read -r state && IFS= read -r name; do
    case "$state" in
      pass) continue ;;
    esac
    printf '  %-22s %s\n' "$name" "$state"
  done < "$OUT/verdicts"
fi

if [ "$noverdict" -gt 0 ]; then
  echo
  echo "  A suite with NO VERDICT never printed one of the three lines it ends"
  echo "  with, so it did not fail a check - it stopped before reaching one."
  echo "  That is a killed process, a crash, or a suite that could not start,"
  echo "  and it is a different thing to look at from a red assertion."
fi
if [ "$nomeasure" -gt 0 ]; then
  echo
  echo "  NOT MEASURED is a check that timed something on a machine that could"
  echo "  not keep the time. It is not a pass - nothing was learned about the"
  echo "  product - and it is not a defect. Run it again on a box that is not"
  echo "  doing something else at the same time."
fi

[ "$fail" -eq 0 ] && [ "$nomeasure" -eq 0 ] && [ "$noverdict" -eq 0 ]
