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
# netns names in routing, bgp-policy, lcp, ipfix, ipfix-tcp and vty do not
# collide with each other - and routing and bgp-policy use the SAME ones,
# veth-out and the `peer` namespace among them, which is only safe because of
# that separate namespace. Only hw-vpp uses host networking, and it is already
# excluded.
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

# Labs that assert a packet LEAVING the box, which is the only kind of lab for
# which the number of workers can change the answer. Each of these is run a
# SECOND time with workers.
#
# This list exists because of #199 and #200. Every appliance with more than one
# core runs workers, and until this list every lab that asserted forwarding ran
# single-threaded: #199 (every translated packet blackholed) and #200 (every
# cross-worker hairpin dropped) were both invisible for the life of the product
# because of it. Neither is exotic - between them they are "the box forwards"
# and "two neighbours can reach each other".
#
# Not every lab, and the reason is worth stating rather than left as a
# selection: a lab that asserts what the VTY prints, or what a counter says
# about a refusal, tests the same code whatever the thread count, and running
# it twice would double the suite to re-prove things that cannot differ. These
# eleven touch the packet.
WORKER_LABS="bypass esp frag ftp-alg hairpin handoff icmp-tcp mss
             nat-behaviour pptp rtsp two-pools"

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
    out=$(timeout 900 sh "$REPO/tools/lab.sh" "$n" 2>&1); rc=$?
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

# The second pass: the same labs, with workers. Reported under their own names
# so a failure says which configuration it was in - "hairpin" and
# "hairpin+workers" are two different claims about the box and an operator
# reading the summary needs to know which one broke.
if [ -z "$ONLY" ] || printf '%s' "$WORKER_LABS" | tr ' \n' '\n\n' | grep -qx "$ONLY"; then
  echo
  echo "the same labs again, with VPP workers:"
  BATCH=""
  running=0
  for n in $WORKER_LABS; do
    [ -n "$ONLY" ] && [ "$ONLY" != "$n" ] && continue
    (
      out=$(LAB_WORKERS=1 timeout 900 sh "$REPO/tools/lab.sh" "$n" 2>&1); rc=$?
      printf '%s\n' "$out" > "$OUT/$n+workers.out"
      echo "$rc" > "$OUT/$n+workers.rc"
    ) &
    BATCH="$BATCH $n+workers"
    running=$((running + 1))
    if [ "$running" -ge "$JOBS" ]; then
      wait
      judge_batch
      running=0
    fi
  done
  wait
  judge_batch
fi

echo
echo "  pass=$pass  fail=$fail  ran=$ran"
[ "$fail" -eq 0 ]
