#!/bin/sh
# Runs every lab and reports pass or fail.
#
# A lab "fails" when it prints a failure marker or exits non-zero. Labs that
# only print observations for a human to read are reported as "ran" - claiming
# they passed would be asserting something nobody checked.
#
# THE TREE IS READ AS EACH LAB STARTS, NOT ONCE AT THE BEGINNING. tools/lab.sh
# bind-mounts the repository into every container, so a lab that starts in
# minute twenty runs whatever is on disk in minute twenty. Merging into the
# branch this suite is reading, while it is reading it, produces a verdict
# about no single tree: the labs before the merge measured one product and the
# labs after it measured another, and the summary line at the end adds the two
# together as though they were one run.
#
# Nothing here can stop that, and a lock is the wrong answer: it would have to
# be taken by a person with a branch to merge and a green suite in front of
# them, which is the moment a lock is most tempting to work around. So: while
# this is running, work in a worktree and merge afterwards. That is also what
# the labs-do-not-compete rule is about from the other side, where the cost is
# a false NO VPP rather than a verdict about two products.
#
# WHAT IS GUARDED IS THE VERDICT, not the merge. This run records the tree it
# started on and compares it at the end, and a tree that moved turns a clean
# summary into one that says it is about no single tree. The sentence above was
# here, and the rule was in the maintainer's notes, and the merge happened
# anyway - eight of them in forty minutes on 2026-09-14. Nothing in the output
# looked wrong, which is what makes it worse than a false NO VPP: the number
# was clean and meant nothing.
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
# netns names in routing, bgp-policy, lcp, ipfix, ipfix-tcp, ipfix-stop, vty
# and one-nic do not collide with each other - and routing and bgp-policy use the SAME
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
# How many labs at once, and it is bounded by MEMORY as well as by cores.
#
# Each lab is a container with --memory=5g (tools/lab.sh), and each runs a VPP
# that reserves hugepages up front. Sixteen of those is 80 GB of limits on a
# box with 62.8 GB, and the way that fails is not a clean refusal: one
# container's VPP does not start, its lab runs every check against a dataplane
# that is not there, and the suite reported eight product failures - one of
# them a checksum failure, on the run right after a checksum change landed
# (#151). The same lab alone, on an idle box, passes eleven checks.
#
# So the default is the smaller of the core-based number and what the memory
# will hold. LAB_JOBS still overrides it: a box with more memory per core
# should use it, and the point here is that the DEFAULT stops over-subscribing
# a machine it was never measured on.
#
# HEXA_MEMINFO is empty on every real run and is set only by
# test/test_run_all_labs.sh, for the same reason HEXA_SELFTEST_ROOT exists:
# a machine cannot be made to have 8 GB, or 512 GB, without being a different
# machine, and the arithmetic is the whole of what is being checked.
_jobs_by_mem() {
  _kb=$(awk '/^MemTotal:/ {print $2}' "${HEXA_MEMINFO:-/proc/meminfo}" 2>/dev/null)
  [ -n "${_kb:-}" ] || { echo 16; return; }
  # LAB_MEM is a limit rather than a reservation, so a lab does not hold all
  # of it all the time; two thirds is what this box was measured surviving.
  _n=$(( _kb * 2 / 3 / 1048576 / 5 ))
  [ "$_n" -lt 1 ] && _n=1
  [ "$_n" -gt 16 ] && _n=16
  echo "$_n"
}
JOBS=${LAB_JOBS:-$(_jobs_by_mem)}
pass=0; fail=0; ran=0; novpp=0; nomeasure=0

# WHAT ELSE THE MACHINE WAS DOING, read rather than advised about.
#
# This suite already tells a reader to "look at the machine's load and free
# memory first" when a lab could not be run - and then does not look. The
# advice has been there since the release run that reported `handoff FAIL`
# over a VPP startup backtrace and `nat-behaviour+workers FAIL` over a capture
# of connection refusals, at load 12 to 15 on 8 cores with three agents
# running labs beside the suite. Neither lab's source had changed and both
# passed alone afterwards.
#
# It happened again on 2026-09-15: detreserve, extblocks and extblocks+workers
# went red on "the record arrived at the collector", against collector windows
# of 25 and 30 seconds, while an agent ran the full test suite under gcov
# three times over and left three spinning processes on a core for an hour.
# All three passed alone on a quiet machine.
#
# So the reading is taken here and at every batch, and printed beside the
# verdict. NOT a threshold and not a refusal: the suite's own measurements say
# LAB_JOBS=16 peaked at load 3.09 on 32 cores and was the fastest clean run
# there is, so a number that refused would refuse correct work. What the
# reader gets is the evidence for the question they are about to ask, which is
# whether to believe a red lab or re-run it alone.
_load_now() { cut -d' ' -f1 /proc/loadavg 2>/dev/null || echo "?"; }
NCPU=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo "?")
LOAD_START=$(_load_now)
LOAD_PEAK=$LOAD_START
_load_note() {
  _l=$(_load_now)
  # Compared with awk: these are decimals and `[ ]` is integers only, so a
  # shell comparison would read 10.5 as smaller than 9.2 and the peak would
  # be whatever happened to have the largest first digit.
  LOAD_PEAK=$(awk -v a="$LOAD_PEAK" -v b="$_l" \
                  'BEGIN{print (b+0 > a+0) ? b : a}')
}
# WHERE EACH LAB'S OWN OUTPUT GOES, and why it is thrown away.
#
# The summary below keeps the verdicts and, under a failure, the red lines.
# Everything else a lab said - every `show bpa` screen, every vppctl answer,
# every packet counted - lives in $OUT/<name>.out for as long as the run and
# is deleted with it. That is right for the ordinary case and the trap stays:
# 87 labs times three passes is a run's worth of transcripts, tens of
# megabytes of them, and a directory that grows by that on every release is a
# directory nobody empties. A suite that quietly fills a disk is a suite
# people stop running.
#
# What it is NOT right for is a question ABOUT the corpus rather than about
# one lab. "Which of the plugin CLI's messages did any box in this repository
# ever print" is such a question, and it could not be asked here: the evidence
# that would answer it did not survive the run that produced it (#366). The
# first attempt at that measurement read the SUMMARY logs instead and reported
# every message as unseen - an artefact of the corpus, stated as a fact about
# the product.
#
# So the corpus is kept when it is asked for, by name, and not otherwise:
#
#   LAB_OUTPUT=/tmp/labs sh tools/run-all-labs.sh
#   sh tools/plugin-says.sh src/plugins/bpa/bpa_cli.c /tmp/labs
#
# Named rather than a flag, because whoever wants it wants to say where it
# goes - and because a directory the suite chose would be one more thing the
# next run has to decide whether to empty.
if [ -n "${LAB_OUTPUT:-}" ]; then
  OUT=$LAB_OUTPUT
  mkdir -p "$OUT"
  echo "LAB_OUTPUT is set: each lab's own output is kept in $OUT"
  echo
else
  OUT=$(mktemp -d)
  trap 'rm -rf "$OUT"' EXIT
fi

# WHICH TREE THIS RUN IS ABOUT, recorded before the first lab reads it.
#
# The header above says nothing here can stop a merge landing mid-run, and
# that is still true. What it can do is stop the verdict being read as though
# one had not: compared again at the end, a tree that moved turns a clean
# summary into a summary that says it is about no single tree.
#
# Written after doing exactly that on 2026-09-14 - suite started, eight merges
# landed into master over the next forty minutes, three of them touching
# lab/boot-iso.sh, lab/install-iso.sh and appliance/selftest, and the run
# reported 85 pass / 1 fail / 1 novpp. Nothing in that output looked wrong,
# which is what makes it worse than a false NO VPP: the number was clean and
# meant nothing. It was caught by comparing commit times against the log by
# hand, before cutting a release on it.
#
# Not a lock, for the reason the header gives: a lock is removed by whoever
# has the branch ready and the green suite in front of them. A verdict that
# reports its own validity is not something anybody has to remember.
TREE_AT_START=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)
DIRTY_AT_START=$(git -C "$REPO" status --porcelain 2>/dev/null | md5sum \
                 | cut -d' ' -f1)

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
#
# dynamic joined it for #210. The single-threaded run of that lab cannot see
# the defect at all: with one slice the partition and the subscriber hash agree
# by accident, and the pool's whole capacity is reachable from any address. The
# worker run is the one where a subscriber is refused with blocks standing free
# somewhere else, and it is the only run in which the pool's capacity claim is
# a claim about anything.
# ipfix-outbuf is deliberately NOT here, and that is the honest answer rather
# than the cheap one. It shares the drain with ipfix-stop, and the drain's
# per-thread walk is already measured both ways there. What #219 added - the
# flush at VLIB_MAIN_LOOP_EXIT and the tracking of a half-sent message - is
# main-thread state in one exporter, so a worker run exercises the same code
# with the same inputs. That is an argument and not a measurement; if somebody
# takes the measurement and it says otherwise, this is the line to change.
BOTH_WAYS="bypass dynamic esp extblocks forward frag ftp-alg hairpin
           icmp-tcp ipfix-stop mss nat-behaviour pptp rtsp sip
           two-pools"

# Labs that are run a THIRD time with the feature they test switched OFF.
#
# Seventeen labs document such a run in their own header, in the words "the
# checks below MUST fail" - and until this list existed nothing ever performed
# one. A lab whose off mode passes is an assertion that cannot fail, which is
# this repository's most common defect sitting inside the machinery built to
# catch it. Every finding in #151 is one of those, and two of them - the PPTP
# ALG's own switch and lab/utilisation.sh's histogram - were green in exactly
# the run their own file says must be red.
#
# The verdict is INVERTED here, in report_off(): "LAB FAILED" is what a pass
# looks like. An off run that exits 0 with LAB PASSED is a failure OF THE
# SUITE, not of the box.
#
# A third state matters as much as those two and is easy to lose: an off run
# that produced no verdict at all - VPP did not start, the container died, the
# cap killed it - has not shown anything about the assertions either. It is
# counted as "ran", which the exit test at the bottom already refuses.
#
# The value after the colon is what the lab is given. Most take "off"; two
# spell their own mode, because the opposite of what they test is not "off" -
# ipfix runs with no exporter, nat-behaviour selects the other filtering mode.
# A value containing "=" is set in the ENVIRONMENT instead of being passed as
# an argument: lab/rtsp.sh's switch is on the pool line rather than a mode of
# the lab, and its header spells the run exactly that way.
#
# Measured one at a time on this machine, with two other agents and a lab
# suite sharing the same docker: the seventeen took between 56 s and 125 s
# each, 1263 s in series, and every one of them FAILED - which is the answer
# their headers ask for. The two labs whose header states a NUMBER, urpf ("six
# checks MUST fail") and sip ("exactly 7"), produced exactly that. oom's
# header is the one that says what must keep passing, and its section B was
# green in the off run as it requires.
#
# bng is absent. Its header documents an off mode, but the lab clones and
# builds accel-ppp-ng from GitHub, so a second run of it costs the network and
# a compile rather than a VPP start, and #170 calls the whole file a spike
# rather than a feature.
# ipfix-outbuf's off mode is a collector that does NOT stall, which is the
# lab's own precondition rather than a feature switch: with the collector
# reading as fast as the box writes there are no bytes pending BEFORE the stop,
# so the checks that assert the condition was made must go red. It was one
# check until #259, #262 and #263 were asserted there; it is eight now, because
# the spill-failure phase cannot be produced without stalling the collector
# either, and the header says so.
OFF_MODES="audit:off detreserve:off ipfix-outbuf:no-stall ipfix-stop:no-marker bypass:off esp:off forward:off ftp-alg:off
           in-place:off
           ipfix:no-exporter limits:off mss:off
           nat-behaviour:address-dependent oom:off pptp:off
           rtsp:BPA_RTSP_OFF=1 sip:off urpf:off utilisation:off vrf:off"

# What a lab is given for its off run, or nothing when it has none.
#
# The same loop shape as not_lab_reason below, and correct here: every value in
# OFF_MODES is a single word (`off`, `no-marker`, `BPA_RTSP_OFF=1`), so word
# splitting cannot cut one in half. It was putting PROSE in such a list that
# broke the other one.
off_arg() {
  for _m in $OFF_MODES; do
    case "$_m" in "$1:"*) printf '%s' "${_m#*:}"; return 0 ;; esac
  done
  return 1
}
# What is NOT a lab in this suite, and why, as data rather than as a comment.
#
# Each entry is "<name>:<reason>". It is read twice: once to skip the file, and
# once to PRINT what was skipped before the first result. A lab that does not
# run has shown nothing, which is the rule this file already states about a lab
# whose dataplane failed to come up - and it applied that rule to the accidents
# and not to the exclusions. Five lab files were silently absent from every
# run, and the summary said fail=0 (#233).
#
# Holding it as data is also what stops the list growing quietly:
# test/test_run_all_labs.sh reads these names and reasons, so adding one costs
# whoever adds it a sentence.
NOT_LABS="
lib:sourced by the labs, not run as one
verdict:sourced by the labs, not run as one
hw-lab:needs a real NIC bound to vfio-pci
hw-vpp:needs a real NIC, and uses host networking
boot-iso:the release gate runs this, against a tag
install-iso:the release gate runs this, against a tag
crossaddr:it pinned what the allocator did BEFORE #226 and is now red by construction: the box prefers the subscriber's own public address for an extension, and every check in this file asserts that it does not. It has not been re-run since that landed, because doing so needs a container - so it is neither a regression test nor a record of the box, and rewriting it against a running box is the open work on #226
ha-failover:the acceptance test for HA, written before HA exists (#9), so it is red because the feature is MISSING and not because anything is broken
soak:hours of churn is the point of it (#30), and four minutes is its SHORTEST run. The longest lab here is about two minutes, and a suite that waited on this one would stop being run - which is how a suite loses the assertion instead of the lab losing it. Run it deliberately: sh tools/lab.sh soak, or sh tools/lab.sh soak 28800 for the eight hours it is for
"

not_lab_reason() {  # <name> -> its reason, or nothing if it is a lab
  # Read a LINE at a time. `for _e in $NOT_LABS` splits on every space, so each
  # WORD of a reason became its own iteration and the first one matched: the
  # announcement printed "boot-iso: the" and stopped. The table was right and
  # the line an operator reads was not, which is the half a check that reads
  # the table cannot see.
  printf '%s\n' "$NOT_LABS" | while IFS= read -r _e; do
    case "$_e" in
      "$1:"*) printf '%s' "${_e#*:}"; exit 0 ;;
    esac
  done | grep . || return 1
}

# Said before the first result, so the exclusions are in the artefact a release
# decision is read from and not only in this source file. A library is not
# news; a lab file that exists and is not being run is.
_skipped=""
for f in "$REPO"/lab/*.sh; do
  n=$(basename "$f" .sh)
  case "$n" in lib|verdict) continue ;; esac
  _r=$(not_lab_reason "$n") && _skipped="$_skipped
  $n: $_r"
done
if [ -n "$_skipped" ] && [ -z "$ONLY" ]; then
  echo "not running, each for its own reason:$_skipped"
  echo
fi

# AND WHAT THIS RUN NEEDS BEYOND THE CONTAINER.
#
# TESTING.md's levels read as no network, container, real Internet - level 3 is
# the one whose heading says "real Internet", and the contrast makes level 2
# read as not needing it. lab/bng.sh does: it apt-gets five -dev packages and
# clones accel-ppp-ng from GitHub, every run, because the container is --rm and
# nothing caches. On a box with no route out it takes the suite red, and a
# release is refused over a lab that was never about the product - #170 calls
# that whole file a spike.
#
# Said here rather than only in TESTING.md because this is the artefact a
# release decision is read from, and because the reason belongs beside the run
# that pays for it. A LIST, in the same shape as NOT_LABS, so a second lab that
# grows a network dependency is named by adding one line and not by somebody
# remembering this paragraph.
NEEDS_NETWORK="
bng:apt-get for five -dev packages, and a git clone of accel-ppp-ng from GitHub, on every run
"
# No pipe into `while`: that runs in a subshell and the list would come back
# empty, which is this section saying nothing on a run that needs the network.
# The same trap the `_skipped` loop above avoids by not having one.
_netlabs=""
_oifs=$IFS
IFS='
'
for _e in $NEEDS_NETWORK; do
  IFS=$_oifs
  _n=${_e%%:*}
  [ -f "$REPO/lab/$_n.sh" ] || { IFS='
'; continue; }
  if not_lab_reason "$_n" >/dev/null; then IFS='
'; continue; fi
  if [ -n "$ONLY" ] && [ "$ONLY" != "$_n" ]; then IFS='
'; continue; fi
  _netlabs="$_netlabs
  $_n: ${_e#*:}"
  IFS='
'
done
IFS=$_oifs
if [ -n "$_netlabs" ] && [ -z "$ONLY" ]; then
  echo "this run needs the network, for these and nothing else:$_netlabs"
  echo
fi

# The list, in the order the summary will print.
LABS=""
for f in "$REPO"/lab/*.sh; do
  n=$(basename "$f" .sh)
  not_lab_reason "$n" >/dev/null && continue
  [ -n "$ONLY" ] && [ "$ONLY" != "$n" ] && continue
  LABS="$LABS $n"
  case " $BOTH_WAYS " in *" $n "*) LABS="$LABS $n+workers" ;; esac
  off_arg "$n" >/dev/null && LABS="$LABS $n+off"
done

# HOW MANY THIS RUN IS ABOUT, counted before anything runs. The four counters
# below are internally consistent with each other whatever happens: if a lab
# never reaches judge_batch - dropped from a batch, its output file missing,
# a name that produced no file - the totals simply come out smaller and the
# suite still says everything it ran passed. That is a verdict about fewer
# labs than exist, and nothing anywhere would say so.
#
# This is the rule test/test_doc_copies.py already applies to documents: a
# counted list says how many it has. It is cheaper here, because the list is
# built ten lines up and cannot be argued with.
EXPECTED=$(printf '%s\n' $LABS | grep -c .)

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
#
# Did this lab measure anything, or did the dataplane under it never come up?
#
# Two failures that read the same and need opposite work. A release run
# reported `handoff FAIL (exit 1)` with no [FAIL] line at all - its capture
# ends in a VPP startup backtrace out of lldp_plugin.so - and
# `nat-behaviour+workers FAIL` with six of them, every vppctl call in it
# answered "connect: Connection refused" and every assertion then reading an
# empty value. Neither lab's source had changed, both passed in two earlier
# suites the same day, and handoff run alone three times afterwards was LAB
# PASSED every time. Load average during the suite was 12 to 15 with three
# agents running labs beside it. The suite spent its verdict on the machine
# and said it in the words it uses for a product defect.
#
# lab/install-iso.sh's console_gave_up() already draws this line and says why:
# a driver that cannot tell "the box never answered" from "the box answered
# and refused" sends whoever reads it to the wrong place, because both look
# like a timeout and only one is about the product.
#
# The two signals, and the boundary between them:
#
#   "VPP did not start" is printed by the labs themselves, from the loop that
#   waits for the socket. It is unambiguous.
#
#   "connect: Connection refused" is vppctl with no socket to talk to, and it
#   is NOT unambiguous on its own - lab/oom.sh has the kernel kill VPP on
#   purpose and reads that line as an answer. So it counts only when the
#   capture holds not one single "[ ok ]": a lab that never got one check
#   green measured nothing, and the [FAIL] lines under it describe empty
#   strings rather than the box.
#
# The deliberately conservative direction: a lab whose VPP died HALFWAY has
# green checks above the refusals and is still reported FAIL. That case is
# genuinely ambiguous, and calling it a machine problem would hide a real
# crash - which is the failure this whole file exists to avoid.
lab_no_vpp() {
  printf '%s' "$1" | grep -q "VPP did not start" && return 0
  printf '%s' "$1" | grep -q "connect: Connection refused" || return 1
  # A green check BEFORE the first refusal means something really was
  # measured, and the box then went: that is the half-dead case, and it stays
  # FAIL until somebody looks at it.
  #
  # Green checks AFTER it are a different thing. A lab whose dataplane never
  # came up reaches its verdict with every value empty, and an assertion
  # written as `test "${X:-0}" = "0"` passes on an empty X - correctly guarded
  # by a precondition that fails beside it, which is why the lab still reports
  # FAILED. But those greens made this function answer "something was
  # measured" when the first line of the run was already a refusal.
  #
  # Measured: lab/icmp-tcp.sh in the release suite of 2026-09-13 refused on
  # line 2 of its output, printed three guarded greens at the verdict, and was
  # reported as a product failure. What it was is a lab that could not be run.
  _before=$(printf '%s' "$1" | sed -n '1,/connect: Connection refused/p')
  printf '%s' "$_before" | grep -q "\[ ok \]" && return 1
  return 0
}

# Why the dataplane was not there, in the lab's own words.
#
# A NO VPP verdict used to be one line with nothing under it, while FAIL prints
# the red checks and NOT MEASURED prints the reading it missed. So the one
# state whose whole point is "look at the machine, not at the product" was the
# one that gave the reader nothing to look at. #253 is that bill: a release run
# lost extblocks+workers to NO VPP and could not say whether the 30 s wait had
# expired or VPP had gone, which are opposite jobs - a box too slow to start a
# dataplane, against a dataplane that died.
#
# The capture holds the answer, and which marker is in it IS the answer:
#
#   "VPP did not start"   the wait at the top of the lab expired. The lab tails
#                         /tmp/vpp.log underneath it.
#   "NO DATAPLANE"        lab/verdict.sh, at the end of a lab that did not
#                         pass and whose vppctl can no longer reach VPP. Ten
#                         labs have no wait guard at all, so for those this is
#                         the only marker there can be.
#
# Neither is a third answer and not a gap: nothing printed a log, so the lab
# was killed - the 900 s cap, the cgroup - before it could. Saying that is the
# honest report of it, and it points at a different place again.
#
# Twenty-one lines because that is what the labs write: "VPP did not start"
# plus tail -20, and vpp_gone_report's marker plus tail -20. Cutting it shorter
# would drop the end of a backtrace, which is the half that names the plugin.
no_vpp_reason() {
  _mark=""
  printf '%s' "$1" | grep -q "VPP did not start" && _mark="VPP did not start"
  if [ -z "$_mark" ] && printf '%s' "$1" | grep -q "NO DATAPLANE"; then
    _mark="NO DATAPLANE"
  fi
  if [ -z "$_mark" ]; then
    echo "     and the capture carries no VPP log: the lab was killed, or it"
    echo "     died, before it could print one. Run it alone to get one."
    return
  fi
  if [ "$_mark" = "VPP did not start" ]; then
    echo "     the wait for the dataplane expired. VPP never answered:"
  else
    echo "     the dataplane was gone by the verdict. VPP's log:"
  fi
  # '%s\n' and not '%s': the capture comes out of a command substitution, so
  # its last line has no newline, and a range that reaches the end of it ran
  # the last log line into the NEXT lab's verdict line. Found by a mutation,
  # which then could not find the lab whose block it had just broken.
  printf '%s\n' "$1" | sed -n "/$_mark/,\$p" | head -21 | sed 's/^/       /'
}

# One lab's verdict. Called as each batch finishes, so the suite shows progress
# instead of going quiet for twelve minutes.
report() {
  _n=$1; _out=$2; _rc=$3
  # Before the verdict: did the box come up at all? A lab that could not be
  # run has neither passed nor failed, and saying either is a claim about the
  # product that nothing measured.
  if lab_no_vpp "$_out"; then
    printf '  %-18s NO VPP - the dataplane never came up\n' "$_n"
    no_vpp_reason "$_out"
    novpp=$((novpp+1))
    return
  fi
  # The lab's exit status is the verdict. Grepping its output for the word
  # "error" made FRR's startup chatter look like a failing test.
  if [ "$_rc" -eq 0 ] && printf '%s' "$_out" | grep -q "LAB PASSED"; then
    printf '  %-18s pass\n' "$_n"; pass=$((pass+1))
  elif printf '%s' "$_out" | grep -q "LAB NOT MEASURED"; then
    # A check whose timing the machine could not keep. Not a failure and not a
    # pass: the lab ran, and the thing it came to measure was not measured. Its
    # own bucket for the same reason NO VPP has one - "nothing found a defect,
    # one check could not be taken" has to be readable as that sentence rather
    # than as a failure (#218).
    printf '  %-18s NOT MEASURED\n' "$_n"
    # The marker line AND what follows it: the lab writes which reading it
    # was and by how much it missed on the next lines, and the marker alone
    # tells a reader nothing they can act on.
    printf '%s' "$_out" | grep -A 2 -E "\[NOT MEASURED\]" | head -4 \
      | sed 's/^/     /'
    nomeasure=$((nomeasure+1))
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

# The same verdict, read backwards. The lab was run with the feature it tests
# switched off, so LAB FAILED is the result its own header demands and
# LAB PASSED is the suite catching an assertion that cannot fail.
#
# The failure line is printed for a pass here, not for a fail: when an off run
# does go red it is worth seeing WHICH checks noticed, because a lab that used
# to fail eleven of them and now fails one has lost ten assertions without
# losing its verdict.
report_off() {
  _n=$1; _out=$2; _rc=$3
  # This one matters more here than above. An off run whose VPP never started
  # fails every check it has, which is exactly the shape the inverted verdict
  # calls a pass - so without this line a suite on a loaded machine would
  # report the off modes green precisely when they proved nothing.
  if lab_no_vpp "$_out"; then
    printf '  %-18s NO VPP - the dataplane never came up\n' "$_n"
    no_vpp_reason "$_out"
    novpp=$((novpp+1))
    return
  fi
  if printf '%s' "$_out" | grep -q "LAB FAILED"; then
    _red=$(printf '%s' "$_out" | grep -c '\[FAIL\]')
    # "as the header says" used to be printed without anybody having read the
    # header, which is a claim the suite was making and not checking. The
    # comment above already names the hazard - a lab that used to fail eleven
    # checks and now fails one has lost ten assertions without losing its
    # verdict - and the count was printed beside it without being compared to
    # anything (#151).
    #
    # A FLOOR rather than an exact number, deliberately. An exact one has to be
    # edited every time a lab legitimately grows, which is a check that reports
    # "you added a test" and not "you broke something", and this repository has
    # already decided that trade once (the interface block's options are
    # asserted as a property for the same reason).
    _floor=$(sed -n 's/^# OFF-RED-AT-LEAST:[[:space:]]*\([0-9][0-9]*\).*/\1/p' \
             "$REPO/lab/${_n%%+*}.sh" 2>/dev/null | head -1)
    if [ -n "$_floor" ] && [ "$_red" -lt "$_floor" ]; then
      printf '  %-18s FAIL - %s check(s) red, and its header floor is %s\n' \
        "$_n" "$_red" "$_floor"
      printf '     an off run that goes red on fewer checks than it used to has\n'
      printf '     lost assertions without losing its verdict. Raise the floor\n'
      printf '     only when the loss is deliberate.\n'
      fail=$((fail+1))
      return
    fi
    if [ -n "$_floor" ]; then
      printf '  %-18s pass (%s check(s) red, floor %s)\n' "$_n" "$_red" "$_floor"
      # A floor BELOW what the off run measures is not a failure - the lab
      # grew, which is the good direction - but it is silent, and silence is
      # how floors go stale. lab/ipfix-outbuf.sh sat at 8 for two days while
      # every run measured 9, because the run that repaired the ninth
      # assertion wrote "the floor is unchanged by this" into the header and
      # nothing ever asked the off run whether that was still true. Until the
      # floor is raised, losing that ninth assertion again costs nothing.
      if [ "$_red" -gt "$_floor" ]; then
        printf '     %s more than its header floor, so the floor is behind the\n' \
          "$(( _red - _floor ))"
        printf '     lab. Raising it to %s is what keeps those from being lost\n' \
          "$_red"
        printf '     quietly later.\n'
      fi
    else
      # No floor declared, so nothing was compared and the line says only what
      # was seen.
      printf '  %-18s pass (%s check(s) red)\n' "$_n" "$_red"
    fi
    pass=$((pass+1))
  elif [ "$_rc" -eq 0 ] && printf '%s' "$_out" | grep -q "LAB PASSED"; then
    printf '  %-18s FAIL - PASSED with the feature off\n' "$_n"
    fail=$((fail+1))
  else
    # No verdict either way. The lab did not run, so it has shown nothing
    # about whether its assertions can fail - which is not a pass.
    printf '  %-18s ran (no verdict yet)\n' "$_n"
    ran=$((ran+1))
  fi
}

judge_batch() {
  for n in $BATCH; do
    out=$(cat "$OUT/$n.out" 2>/dev/null || true)
    rc=$(cat "$OUT/$n.rc" 2>/dev/null || echo 1)
    case "$n" in
      *+off) report_off "$n" "$out" "$rc" ;;
      *)     report "$n" "$out" "$rc" ;;
    esac
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
    #
    # "<lab>+off" is the third pass: the same lab with the feature it tests
    # switched off, judged by report_off() with the verdict inverted.
    argv=""; env_set=""
    case "$n" in
      *+workers) lab=${n%+workers}; workers=1 ;;
      *+off)
	lab=${n%+off}; workers=0
	argv=$(off_arg "$lab")
	# An off mode carried in the environment rather than in argv.
	case "$argv" in *=*) env_set=$argv; argv="" ;; esac
	;;
      *)	 lab=$n; workers=0 ;;
    esac
    out=$(env LAB_WORKERS=$workers $env_set \
	    timeout 900 sh "$REPO/tools/lab.sh" "$lab" $argv 2>&1); rc=$?
    printf '%s\n' "$out" > "$OUT/$n.out"
    echo "$rc" > "$OUT/$n.rc"
  ) &
  BATCH="$BATCH $n"
  running=$((running + 1))
  if [ "$running" -ge "$JOBS" ]; then
    wait
    _load_note
    judge_batch
    running=0
  fi
done
wait
_load_note
judge_batch


echo
echo "  pass=$pass  fail=$fail  notmeasured=$nomeasure  novpp=$novpp  ran=$ran"
# One echo, not two arguments on a continued line: `echo a b` puts a space
# between them and the continuation already had one, so the row came out with
# "at  the" in the middle of it.
# Two rows rather than one: with two-digit loads - which is the case this
# exists for - a single row reaches 82 columns, and this is read in a
# terminal beside the verdict.
echo "  machine: $NCPU cpu(s)"
echo "  load $LOAD_START at the start, $LOAD_PEAK at the highest batch boundary"
# Said only when there is something to explain. On a clean run the numbers
# above are a record; here they are the first thing to check before reading a
# red lab as a product defect, because this suite has twice reported one that
# was not.
if [ "$((fail + novpp + nomeasure + ran))" -gt 0 ]; then
  echo "  a lab that went red on a busy machine is a lab to run again alone:"
  echo "  'sh tools/lab.sh <name>'. Both times this suite reported a defect"
  echo "  that was not one, the machine was carrying something else."
fi
_accounted=$((pass + fail + nomeasure + novpp + ran))
if [ "$_accounted" -ne "$EXPECTED" ]; then
  echo
  echo "  THE SUITE LOST A LAB. It set out to run $EXPECTED and accounted for"
  echo "  $_accounted. The counts above are consistent with each other and"
  echo "  they are about fewer labs than this repository has, so a green"
  echo "  verdict here would have cleared something nobody measured."
  lost=1
fi
if [ "$novpp" -gt 0 ]; then
  echo
  echo "  $novpp lab(s) could not be run: vppctl never reached a dataplane"
  echo "  under them. That is not a product defect and it is not a suite that"
  echo "  cleared them - look at the machine's load and free memory first, then"
  echo "  run those labs one at a time. A lab reported this way has said"
  echo "  nothing about the product either way."
fi
# "ran (no verdict yet)" counts against the suite too. The comment forty lines
# up worries about a number that never reaches zero, and the fix it got was to
# stop counting the two libraries - which left the number able to reach zero
# and still unable to fail anything. A lab that exits 0 having printed neither
# LAB PASSED nor LAB FAILED produced no verdict, and a suite that reports
# success over a lab that said nothing is the shape this suite exists to find.
# Every lab in the list ends in `verdict`, so this is zero today; the day one
# loses it, the suite says so instead of reading the way it always did.
# novpp counts against the suite for the same reason "ran" does, and for one
# more: those labs were not measured at all, so the next change to them lands
# on a suite that has never seen them run. It is reported separately so that
# "nothing found a defect, two labs could not be run" is readable as that
# sentence rather than as two failures.
if [ "$nomeasure" -gt 0 ]; then
  echo
  echo "  $nomeasure lab(s) could not measure a check they came to measure."
  echo "  That is not a product defect and it is not a suite that cleared"
  echo "  them: the machine could not keep the timing those checks need. Run"
  echo "  those labs one at a time before reading anything into it."
fi
# The corpus, if one was asked for, and what it is good for. Said here rather
# than only at the top because the run is over and the directory is now full:
# whoever reads this line has the evidence in front of them and forty minutes
# between them and another chance to make it (#366).
if [ -n "${LAB_OUTPUT:-}" ]; then
  echo
  echo "  each lab's own output is in $LAB_OUTPUT, one file per lab. Which of"
  echo "  the plugin CLI's messages any of them printed:"
  echo "    sh tools/plugin-says.sh src/plugins/bpa/bpa_cli.c $LAB_OUTPUT"
fi

# And whether this run is about one tree at all. Last, because it is the only
# line here that can invalidate every line above it.
TREE_AT_END=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)
DIRTY_AT_END=$(git -C "$REPO" status --porcelain 2>/dev/null | md5sum \
               | cut -d' ' -f1)
MOVED=0
if [ "$TREE_AT_START" != "$TREE_AT_END" ]; then
  MOVED=1
  echo
  echo "  THIS RUN IS ABOUT NO SINGLE TREE. The repository moved while it was"
  echo "  running: every lab reads the tree as IT starts, so the labs before"
  echo "  the change measured one product and the labs after it measured"
  echo "  another, and the counts above add the two together."
  echo "    at the start: $TREE_AT_START"
  echo "    at the end:   $TREE_AT_END"
  git -C "$REPO" log --oneline "$TREE_AT_START..$TREE_AT_END" 2>/dev/null \
    | sed 's/^/    /'
  echo "  Run it again on a tree that stays still. Nothing above this line"
  echo "  says anything about the product."
elif [ "$DIRTY_AT_START" != "$DIRTY_AT_END" ]; then
  MOVED=1
  echo
  echo "  THIS RUN IS ABOUT NO SINGLE TREE. HEAD did not move, but the working"
  echo "  tree did: an edit landed under the labs while they were reading it."
  echo "  Run it again on a tree that stays still."
fi
[ "${lost:-0}" -eq 0 ] && [ "$fail" -eq 0 ] && [ "$ran" -eq 0 ] \
  && [ "$novpp" -eq 0 ] \
  && [ "$nomeasure" -eq 0 ] && [ "$MOVED" -eq 0 ]
