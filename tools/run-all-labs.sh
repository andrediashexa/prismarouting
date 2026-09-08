#!/bin/sh
# Runs every lab and reports pass or fail.
#
# A lab "fails" when it prints a failure marker or exits non-zero. Labs that
# only print observations for a human to read are reported as "ran" - claiming
# they passed would be asserting something nobody checked.
#
# Labs run several at a time. Measured before this existed: 35 labs in series
# took ~35 minutes at load 1.18 across 8 cores, with an ISO build running
# alongside. The suite is not compute-bound and never was - each lab starts
# VPP, sleeps for packets to cross, and waits on timeouts. Thirty-five of those
# in series is a queue, not a workload (#100).
#
# What makes it safe: tools/lab.sh runs each lab in its own container WITHOUT
# --network=host, so each has its own network namespace. The fixed veth and
# netns names in routing, lcp, ipfix, ipfix-tcp and vty do not collide with
# each other. Only hw-vpp uses host networking, and it is already excluded.
#
# What is NOT safe, and is why the build happens first: 36 labs run "ninja" in
# the shared /src/.build-vpp, which is bind-mounted from the repository.
# Concurrent builds in one build directory race.
#
# Output is captured per lab and printed whole, in a fixed order. Interleaved
# output from several labs at once is not a report.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
ONLY=${1:-}
JOBS=${LAB_JOBS:-4}
pass=0; fail=0; ran=0
OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT

# Built once, before anything runs. No lab may be the one that happens to
# build it: that is the only shared writable state between them.
printf 'building the plugin once, so no lab races another to do it... '
if sh "$REPO/tools/build.sh" plugin >"$OUT/build.log" 2>&1; then
  echo "ok"
else
  echo "FAILED"
  tail -20 "$OUT/build.log" | sed 's/^/  /'
  exit 1
fi
echo

# The list, in the order the summary will print.
LABS=""
for f in "$REPO"/lab/*.sh; do
  n=$(basename "$f" .sh)
  # verdict.sh is not a lab, it is the assertion library the labs source.
  # Counted as "ran", it leaves the summary with a number that never reaches
  # zero - and the day a real lab loses its verdict, nobody notices.
  # lib.sh and verdict.sh are sourced by the labs, not run as labs. Counting a
  # library as "ran (no verdict yet)" leaves a number in the summary that never
  # reaches zero, and the day a real lab loses its verdict the line reads the
  # same as it always did.
  case "$n" in hw-lab|hw-vpp|boot-iso|install-iso|verdict|lib) continue ;; esac
  [ -n "$ONLY" ] && [ "$ONLY" != "$n" ] && continue
  LABS="$LABS $n"
done

# Run them, at most $JOBS at a time, each into its own file.
running=0
for n in $LABS; do
  (
    # rc captured IMMEDIATELY. Taken after the printf below it is the
    # PRINTF's status, so every lab records 0 and one that exits non-zero
    # without printing a failure marker is reported as passing - which is the
    # exact defect this suite exists to catch, in the suite itself.
    out=$(timeout 300 sh "$REPO/tools/lab.sh" "$n" 2>&1); rc=$?
    printf '%s\n' "$out" > "$OUT/$n.out"
    echo "$rc" > "$OUT/$n.rc"
  ) &
  running=$((running + 1))
  if [ "$running" -ge "$JOBS" ]; then
    wait
    running=0
  fi
done
wait

# Judged afterwards, in list order, so the summary reads the same however the
# labs happened to finish.
for n in $LABS; do
  out=$(cat "$OUT/$n.out" 2>/dev/null || true)
  rc=$(cat "$OUT/$n.rc" 2>/dev/null || echo 1)
  # The lab's exit status is the verdict. Grepping its output for the word
  # "error" made FRR's startup chatter look like a failing test.
  if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q "LAB PASSED"; then
    printf '  %-18s pass\n' "$n"; pass=$((pass+1))
  elif printf '%s' "$out" | grep -q "LAB FAILED"; then
    printf '  %-18s FAIL\n' "$n"
    printf '%s' "$out" | grep -E "\[FAIL\]" | head -3 | sed 's/^/     /'
    fail=$((fail+1))
  elif [ "$rc" -ne 0 ]; then
    printf '  %-18s FAIL (exit %s)\n' "$n" "$rc"; fail=$((fail+1))
  else
    printf '  %-18s ran (no verdict yet)\n' "$n"; ran=$((ran+1))
  fi
done
echo
echo "  pass=$pass  fail=$fail  ran=$ran"
[ "$fail" -eq 0 ]
