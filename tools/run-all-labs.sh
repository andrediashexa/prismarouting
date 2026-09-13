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
# Measured on 36 labs. On 8 cores: LAB_JOBS=4 took 1568 s, LAB_JOBS=8 took
# 1334 s - and that second run was competing with an ISO build, so it was under
# MORE load and still faster. On 32 cores: LAB_JOBS=16 took 940 s with peak
# load 3.09, and LAB_JOBS=32 took 826 s but LOST A LAB - `routing` hit the
# per-lab cap, because 32 containers each starting VPP make the slowest lab
# slower. 12% of wall clock is not worth a lab that did not run, so 16 it is.
#
# Load 3.09 out of 32 says what the rest of the clock is: waiting, not
# computing. The floor is the slowest single lab, and that lab was `routing`:
# six subjects in one file, measured alone on the 32-core machine at 507 s. No
# number of cores moved it, so it was split (#167) into `routing` - the punt
# path for BGP, OSPF and IS-IS, and the interface and route work under it - and
# `bgp-policy` - route-maps, AS-path lists, communities and RPKI. Measured
# alone on the same machine straight afterwards: 304 s and 243 s, with 159
# checks between them against the 158 the single file produced.
#
# The floor is therefore 304 s rather than 507 s, and it cost a second VPP and
# a second FRR topology in total CPU - which on a machine sitting at load 3 of
# 32 is bought with something there is plenty of.
#
# What makes it safe: tools/lab.sh runs each lab in its own container WITHOUT
# --network=host, so each has its own network namespace. The fixed veth and
# netns names in routing, bgp-policy, lcp, ipfix, ipfix-tcp, ipfix-stop and
# vty do not collide with each other - and routing and bgp-policy use the SAME
# ones, veth-out and the `peer` namespace among them, which is only safe
# because of that separate namespace. Only hw-vpp uses host networking, and it
# is already excluded.
#
# What is NOT safe, and is why the build happens first: every lab runs "ninja"
# in the shared /src/.build-vpp, which is bind-mounted from the repository.
# Concurrent builds in one build directory race.
#
# Output is captured per lab and printed whole, in a fixed order. Interleaved
# output from several labs at once is not a report.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
ONLY=${1:-}
JOBS=${LAB_JOBS:-16}
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

# Labs that are run a SECOND time with workers.
#
# hexarouting-tune gives every appliance with more than one core at least two
# workers, and a box too small for that everything on the main thread. Both are
# configurations the product ships and neither proves the other: #199 was a
# translation that was correct single-threaded and blackholed with workers,
# #200 a hairpin that worked only when both subscribers landed on one worker,
# and #201 an ICMP error translated single-threaded and dropped as malformed
# with workers. All three were found by running the labs with LAB_WORKERS=1,
# because the suite had only ever run one way - which is why the second run
# lives here rather than in the labs. A lab that hardcoded workers would move
# the blind spot to the small box instead of closing it.
#
# Every lab here has been MEASURED both ways. A lab does not go in because it
# looks as though it forwards something; putting one here untested turns the
# suite red for a defect nobody is holding.
#
# Not every lab, either: one that asserts what the VTY prints, or what a
# counter says about a refusal, exercises the same code whatever the thread
# count, and a second run of it would double the suite to re-prove what cannot
# differ. These fourteen touch the packet.
#
# handoff, dynamic-workers and pool-del are absent because they ask for workers
# THEMSELVES - a "+workers" run of those would be the same run twice.
#
# forward joined the list when a forward became possible on a dynamic pool: the
# block a pin holds has to be in the slice of the same worker the subscriber's
# own traffic lands on, and that is a sentence only a worker run can check.
#
# ipfix-stop is here for a reason that is not the packet path: the stop drain
# walks EVERY thread's allocator to find the blocks still held, and
# single-threaded that loop has one iteration. Measured both ways - with two
# workers the subscriber lands on a different shard and the released block is a
# different range, which is the part a one-thread run cannot see.
BOTH_WAYS="bypass esp extblocks forward frag ftp-alg hairpin icmp-tcp
           ipfix-stop mss nat-behaviour pptp rtsp sip two-pools"
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
  case " $BOTH_WAYS " in *" $n "*) LABS="$LABS $n+workers" ;; esac
done

# Run them, at most $JOBS at a time, each into its own file.
#
# Judged BATCH BY BATCH rather than all at the end. Judging everything after
# the last lab finished meant twelve minutes in which the suite printed
# nothing at all - and a long-running thing that says nothing is
# indistinguishable from one that has hung, which is the same complaint #97
# fixed on the boot console. It was introduced here three hours after being
# fixed there.
#
# Order is still fixed: batches run in list order and each batch is judged in
# list order, so the summary reads the same however the labs finish.
# One lab's verdict. Called as each batch finishes, so the suite shows progress
# instead of going quiet for twelve minutes.
report() {
  _n=$1; _out=$2; _rc=$3
  # The lab's exit status is the verdict. Grepping its output for the word
  # "error" made FRR's startup chatter look like a failing test.
  if [ "$_rc" -eq 0 ] && printf '%s' "$_out" | grep -q "LAB PASSED"; then
    printf '  %-18s pass\n' "$_n"; pass=$((pass+1))
  elif printf '%s' "$_out" | grep -q "LAB FAILED"; then
    printf '  %-18s FAIL\n' "$_n"
    printf '%s' "$_out" | grep -E "\[FAIL\]" | head -3 | sed 's/^/     /'
    fail=$((fail+1))
  elif [ "$_rc" -ne 0 ]; then
    printf '  %-18s FAIL (exit %s)\n' "$_n" "$_rc"; fail=$((fail+1))
  else
    printf '  %-18s ran (no verdict yet)\n' "$_n"; ran=$((ran+1))
  fi
}

judge_batch() {
  for n in $BATCH; do
    out=$(cat "$OUT/$n.out" 2>/dev/null || true)
    rc=$(cat "$OUT/$n.rc" 2>/dev/null || echo 1)
    report "$n" "$out" "$rc"
  done
  BATCH=""
}

BATCH=""
running=0
for n in $LABS; do
  (
    # rc captured IMMEDIATELY. Taken after the printf below it is the
    # PRINTF's status, so every lab records 0 and one that exits non-zero
    # without printing a failure marker is reported as passing - which is the
    # exact defect this suite exists to catch, in the suite itself.
    # 900, and for the first time in this file that is a number taken from a
    # measurement rather than from a lab having been killed. It was 300, then
    # 600, then 1200 within one day, raised each time because `routing` had
    # grown another subject - which is treating the symptom. #167 was the fix:
    # that lab is now `routing` and `bgp-policy`, measured alone at 304 s and
    # 243 s.
    #
    # 900 is three times the slowest of those, and the factor is what
    # parallelism has actually cost here: a 329 s lab survived a 600 s cap at
    # LAB_JOBS=16 and was killed by it at LAB_JOBS=32. Lower than this would be
    # budgeting how long a lab may honestly take, which is not what the cap is
    # for - `routing` measures router advertisements in three states, and each
    # state needs a window wider than the interval VPP schedules its initial
    # advertisements in. Killed at 300, it was reported as a failure, which is
    # the cap telling the reader something false about the lab.
    # "<lab>+workers" is the second pass of a lab in BOTH_WAYS. The suffix is
    # carried in the name so the two runs have separate output files and
    # separate lines in the summary - a lab that passes one way and fails the
    # other has to be readable as exactly that.
    case "$n" in
      *+workers) lab=${n%+workers}; workers=1 ;;
      *)	 lab=$n; workers=0 ;;
    esac
    out=$(LAB_WORKERS=$workers timeout 900 sh "$REPO/tools/lab.sh" "$lab" 2>&1); rc=$?
    printf '%s\n' "$out" > "$OUT/$n.out"
    echo "$rc" > "$OUT/$n.rc"
  ) &
  BATCH="$BATCH $n"
  running=$((running + 1))
  if [ "$running" -ge "$JOBS" ]; then
    wait
    judge_batch
    running=0
  fi
done
wait
judge_batch


echo
echo "  pass=$pass  fail=$fail  ran=$ran"
# "ran (no verdict yet)" counts against the suite too. The comment forty lines
# up worries about a number that never reaches zero, and the fix it got was to
# stop counting the two libraries - which left the number able to reach zero
# and still unable to fail anything. A lab that exits 0 having printed neither
# LAB PASSED nor LAB FAILED produced no verdict, and a suite that reports
# success over a lab that said nothing is the shape this suite exists to find.
# Every lab in the list ends in `verdict`, so this is zero today; the day one
# loses it, the suite says so instead of reading the way it always did.
[ "$fail" -eq 0 ] && [ "$ran" -eq 0 ]
