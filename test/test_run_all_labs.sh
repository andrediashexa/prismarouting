#!/bin/sh
# Does the lab suite tell a defect apart from a machine that could not run it?
#
# A release run reported `handoff FAIL (exit 1)` over a VPP startup backtrace
# and `nat-behaviour+workers FAIL` over a capture in which every vppctl call
# said "connect: Connection refused". Neither lab's source had changed and both
# passed alone afterwards: the machine was at load 12 to 15 with three agents
# running labs beside the suite. The suite spent its verdict on the machine and
# said it in the words it uses for a product defect - the same hole
# lab/install-iso.sh's console_gave_up() exists to close on the console.
#
# Since #253 it covers both halves of that instrument: the classifier here, and
# what a NO VPP verdict CARRIES. The verdict was one line with nothing under it
# while FAIL printed its red checks, so the one state that means "look at the
# machine" was the one with nothing to look at, and a release run lost
# extblocks+workers without being able to say whether the wait had expired or
# VPP had gone.
#
# Driven with STUB labs, not real ones. The state this is about is a VPP that
# did not start, and a test that had to arrange one would need a container, a
# dataplane and a machine under load - so it would never be part of `make
# test`, which is where a classifier like this has to be pinned. The real
# tools/run-all-labs.sh is run here, unmodified, against a repository whose
# lab.sh prints captures taken from the failures above.
set -u
fail=0
ok()  { echo "  $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }
REPO=$(cd "$(dirname "$0")/.." && pwd)

d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT
mkdir -p "$d/tools" "$d/lab"
cp "$REPO/tools/run-all-labs.sh" "$d/tools/"

# The suite builds the plugin before anything runs. Nothing here compiles.
cat > "$d/tools/build.sh" <<'EOF'
#!/bin/sh
exit 0
EOF

# The stub labs, keyed on the name and the mode the suite asks for. Each
# capture is the shape of a real one, cut down to the lines the classifier
# reads.
cat > "$d/tools/lab.sh" <<'EOF'
#!/bin/sh
case "$1 ${2:-}" in
  "aa-good ")
    echo "  [ ok ] something was measured"; echo "LAB PASSED"; exit 0 ;;

  "bb-broken ")
    echo "  [ ok ] something was measured"
    echo "  [FAIL] and something else was not (missing: a thing)"
    echo "LAB FAILED: 1 check(s)"; exit 1 ;;

  # handoff's capture: the dataplane paniced on the way up and the lab never
  # reached an assertion, so it exits non-zero with no verdict of any kind.
  "cc-panic ")
    echo "VPP did not start"
    echo "vpp[4502]: #4  vlib_worker_thread_barrier_release + 0x6b2"
    echo "vpp[4502]: #5  vlib_node_add_next_with_slot + 0xc32"
    echo "vpp[4502]: #6  ethernet_register_input_type + 0xc1"
    echo "vpp[4502]: #7  from /usr/lib/x86_64-linux-gnu/vpp_plugins/lldp_plugin.so"
    exit 1 ;;

  # nat-behaviour+workers' capture: vppctl never reached a socket, so every
  # value an assertion read was empty and every assertion failed. Not one
  # check went green, which is what separates this from a defect.
  "dd-refused ")
    echo "clib_socket_init: connect: Connection refused"
    echo "clib_socket_init: connect: Connection refused"
    echo "clib_socket_init: connect: Connection refused"
    echo "  [FAIL] the mapping test measured a real public port"
    echo "  [FAIL] one internal socket keeps one external port (missing: 200.150.0.17)"
    echo "LAB FAILED: 2 check(s)"; exit 1 ;;

  # The boundary, and it must land on the other side: VPP answered for a
  # while and then went. Green checks above the refusals mean something WAS
  # measured, so this is a failure until somebody looks at it.
  "ff-halfdead ")
    echo "  [ ok ] the pool was created"
    echo "  [ ok ] and the subscriber was translated"
    echo "clib_socket_init: connect: Connection refused"
    echo "  [FAIL] the session expired (missing: total: 0 sessions)"
    echo "LAB FAILED: 1 check(s)"
    # lab/verdict.sh dumps the log whenever a lab ends without passing and
    # vppctl cannot reach VPP, so a half-dead lab carries this marker too. It
    # is evidence and not a verdict: this run measured two things before the
    # box went, and calling it a machine problem would hide a real crash.
    echo "NO DATAPLANE at the verdict: vppctl cannot reach VPP. Last of /tmp/vpp.log:"
    echo "  | vlib_worker_thread_barrier_sync_int: worker thread deadlock"
    exit 1 ;;

  # A lab that reached its verdict with no dataplane under it and no wait
  # guard above it to have said so. Ten labs start VPP without that guard -
  # icmp-tcp among them - so lab/verdict.sh's marker is the only place their
  # log can reach a reader at all.
  "hh-died ")
    echo "connect: Connection refused"
    echo "  [FAIL] the subscriber was translated (missing: 200.150.0.17)"
    echo "LAB FAILED: 1 check(s)"
    echo "NO DATAPLANE at the verdict: vppctl cannot reach VPP. Last of /tmp/vpp.log:"
    echo "  | received signal SIGSEGV, PC 0x7f2a1c0b4e21"
    echo "  | #3 bpa_in2out_node_fn + 0x1a4 from /vpp_plugins/bpa_plugin.so"
    exit 1 ;;

  "ee-silent ")
    exit 0 ;;

  # icmp-tcp's capture from the release suite of 2026-09-13: vppctl was
  # refused on the SECOND line of the run, so nothing was ever measured - and
  # the verdict still carried three greens, because an assertion written as
  # `test "${X:-0}" = "0"` passes on an empty X. Each is guarded by a
  # precondition that fails beside it, which is why the lab reports FAILED;
  # what they are not is evidence that the box answered.
  "ii-deadstart ")
    echo "=========== 1. outbound UDP session ==========="
    echo "connect: Connection refused"
    echo "connect: Connection refused"
    echo ""
    echo "=========== VERDICT ==========="
    echo "  [FAIL] the translated ICMP error passed every checksum"
    echo "  [ ok ] the out2in handoff identified the subscriber"
    echo "  [FAIL] and it really did have to cross a worker to get there"
    echo "  [ ok ] and they expired on the SYN timer, not the transitory one"
    echo "  [FAIL] three half-open sessions were created"
    echo "LAB FAILED: 3 check(s)"; exit 1 ;;

  # igp's capture: the box cleared an adjacency, the reading was taken after
  # the hello interval, and the check says so rather than reporting a product
  # defect. It measured everything else first, which is what separates it from
  # a lab that could not run at all (#218).
  "gg-late ")
    echo "  [ ok ] the adjacency is up before it is cleared"
    echo "  [NOT MEASURED] clearing it took the adjacency down there and then"
    echo "         the reading was taken 5s after the change and the"
    echo "         window is 3s, so this says nothing either way"
    echo "LAB NOT MEASURED: 1 check(s)"; exit 2 ;;

  # An off mode that does what its header says: red with the feature gone.
  "vrf ")    echo "LAB PASSED"; exit 0 ;;
  "vrf off") echo "  [FAIL] the two tenants got different public addresses"
             echo "LAB FAILED: 1 check(s)"; exit 1 ;;

  # An off mode that still fails, and on FEWER checks than it used to. Its
  # verdict is unchanged, which is exactly what makes this invisible without
  # the floor: the lab lost assertions and went on reporting a pass (#151).
  "mss ")    echo "LAB PASSED"; exit 0 ;;
  "mss off") echo "  [FAIL] one of the four that used to go red"
             echo "LAB FAILED: 1 check(s)"; exit 1 ;;

  # An off mode that passes, which is the suite's own defect to catch.
  "audit ")    echo "LAB PASSED"; exit 0 ;;
  "audit off") echo "  [ ok ] the identity travelled"; echo "LAB PASSED"; exit 0 ;;

  # An off mode on a box whose VPP never came up. It fails every check it has,
  # which is exactly what the inverted verdict calls a pass.
  "urpf ")    echo "LAB PASSED"; exit 0 ;;
  "urpf off") echo "VPP did not start"; echo "LAB FAILED: 6 check(s)"; exit 1 ;;

  *) echo "the stub was asked for a lab it does not have: $*" >&2; exit 2 ;;
esac
EOF
chmod +x "$d/tools/build.sh" "$d/tools/lab.sh"

for n in aa-good bb-broken cc-panic dd-refused ee-silent ff-halfdead \
         gg-late hh-died ii-deadstart vrf audit urpf; do
  echo '#!/bin/sh' > "$d/lab/$n.sh"
done
# The lab with a floor in its header, and a lab file is where the floor lives
# because that is where the number is true: the header of the lab whose off run
# it describes.
printf '#!/bin/sh\n# OFF-RED-AT-LEAST: 4\n' > "$d/lab/mss.sh"

out=$(sh "$d/tools/run-all-labs.sh" 2>&1); rc=$?

echo "=== a lab whose dataplane never came up is NO VPP, not FAIL ==="
case "$out" in
  *"cc-panic           NO VPP"*) ok "a startup backtrace is reported as NO VPP" ;;
  *) bad "the panic was not reported as NO VPP; got:
$out" ;;
esac
case "$out" in
  *"dd-refused         NO VPP"*)
    ok "and so is a capture in which nothing ever answered" ;;
  *) bad "the refused capture was not reported as NO VPP" ;;
esac
case "$out" in
  *"ff-halfdead        FAIL"*)
    ok "a lab that measured something first is still a FAIL" ;;
  *) bad "a half-dead run was excused as a machine problem" ;;
esac

echo "=== and a NO VPP verdict carries the reason, not just the verdict ==="
# The verdict tells the reader to look at the machine. Which part of the
# machine is a different question, and it has two answers that need opposite
# work: a box too slow to bring a dataplane up, and a dataplane that died. The
# capture already held both and the report threw them away (#253).
#
# Read as the BLOCK under one lab's verdict line, never grepped out of the
# whole run. Three labs here are NO VPP, so a sentence found anywhere would
# pass while the report printed it for a different one - which is exactly what
# the first mutation of this section did: with report() no longer printing any
# reason at all, "a wait that expired" stayed green on the line report_off()
# had printed for urpf+off.
lab_block() {  # <lab> -> its verdict line and everything indented under it
  printf '%s\n' "$out" | awk -v n="$1" '
    index($0, "  " n " ") == 1 { take = 1; print; next }
    take && /^     / { print; next }
    take { exit }
  '
}

b=$(lab_block cc-panic)
case "$b" in
  *"the wait for the dataplane expired. VPP never answered:"*)
    ok "a wait that expired is reported as a wait that expired" ;;
  *) bad "the NO VPP line did not say the wait expired; got:
$b" ;;
esac
case "$b" in
  *"lldp_plugin.so"*)
    ok "and VPP's own log comes with it, down to the plugin that faulted" ;;
  *) bad "the startup backtrace did not reach the report; got:
$b" ;;
esac

b=$(lab_block hh-died)
case "$b" in
  *"the dataplane was gone by the verdict. VPP's log:"*)
    ok "a dataplane that went after answering is reported as that instead" ;;
  *) bad "a lab whose VPP died mid-run was not told apart from one that never
started; got:
$b" ;;
esac
case "$b" in
  *"bpa_in2out_node_fn"*)
    ok "and its log reaches the report the same way" ;;
  *) bad "lab/verdict.sh printed a log and the suite dropped it; got:
$b" ;;
esac

# The third answer, and it is an answer. A capture with neither marker means
# nothing printed a log, which points at the 900 s cap or the cgroup rather
# than at VPP - and saying so beats an empty line under the verdict.
b=$(lab_block dd-refused)
case "$b" in
  *"the capture carries no VPP log"*)
    ok "and a capture with no log at all says that, rather than nothing" ;;
  *) bad "a NO VPP with no log in it printed nothing under the verdict; got:
$b" ;;
esac
# The marker is evidence and not a verdict. lab/verdict.sh prints it whenever a
# lab ends without passing and vppctl cannot reach VPP, so the half-dead lab
# carries one too - and that lab measured two things before the box went. If
# the marker were read as a classifier it would become a machine problem and a
# real crash would be excused.
case "$out" in
  *"ff-halfdead        FAIL"*)
    ok "a half-dead lab carrying the marker is still a FAIL" ;;
  *) bad "the NO DATAPLANE marker was read as a verdict and excused a crash" ;;
esac

echo "=== an off run that could not start VPP is not a pass ==="
# This is the one the inverted verdict gets wrong on its own: every check red
# is what an off run is supposed to look like.
case "$out" in
  *"urpf+off           NO VPP"*) ok "urpf+off is NO VPP, not a pass" ;;
  *) bad "an off run with no dataplane was counted as proving something" ;;
esac
case "$out" in
  *"vrf+off            pass"*) ok "an off run that really went red still passes" ;;
  *) bad "a genuine off failure stopped counting as a pass" ;;
esac
case "$out" in
  *"audit+off          FAIL - PASSED with the feature off"*)
    ok "and an off run that passed is the suite's own failure" ;;
  *) bad "an off mode that passed was not caught" ;;
esac

echo "=== green AFTER the first refusal is not evidence the box answered ==="
# The boundary between this and ff-halfdead, and it is the whole of why
# lab_no_vpp reads the ORDER rather than the presence of a green check. A lab
# whose dataplane never came up reaches its verdict with every value empty, and
# a guarded assertion passes on an empty value - correctly, with its
# precondition failing beside it. Those greens are not a measurement.
case "$out" in
  *"ii-deadstart       NO VPP"*)
    ok "a run refused before it measured anything is NO VPP, greens and all" ;;
  *) bad "a lab that never reached the dataplane was called something else; got:
$(printf '%s' "$out" | grep ii-deadstart)" ;;
esac
case "$out" in
  *"ff-halfdead        FAIL"*)
    ok "and one that measured something first is still a FAIL" ;;
  *) bad "the half-dead case moved with it, which is the case this must not take" ;;
esac

echo "=== an off run that lost assertions is caught by its own floor ==="
# The verdict is unchanged - still LAB FAILED, still red - so nothing about the
# pass or fail of the lab can see this. What sees it is the COUNT against the
# number its header declares.
case "$out" in
  *"mss+off            FAIL - 1 check(s) red, and its header floor is 4"*)
    ok "an off run red on fewer checks than its floor is a failure" ;;
  *) bad "a lab that lost three assertions still reported a pass; got:
$(printf '%s' "$out" | grep mss)" ;;
esac
case "$out" in
  *"lost assertions without losing its verdict"*)
    ok "and the report says what that means" ;;
  *) bad "nothing explained what a thinned off run is" ;;
esac
# And the line for a lab that meets its floor says what was compared, rather
# than claiming a header was read when it was not.
case "$out" in
  *"urpf+off           pass (6 check(s) red, floor 6)"*)
    ok "a lab that meets its floor says both numbers" ;;
  *"urpf+off           NO VPP"*)
    ok "urpf+off is NO VPP here, which its own case above already asserts" ;;
  *) bad "the floor was not reported beside the count; got:
$(printf '%s' "$out" | grep 'urpf+off')" ;;
esac

echo "=== a check whose timing the machine could not keep is its own state ==="
# Not a failure: the lab ran and measured everything else. Not a pass either:
# the thing it came to measure was not measured. This is the state that cost an
# investigation when lab/igp.sh reported it as a product defect in a release
# suite and passed in isolation (#218).
case "$out" in
  *"gg-late            NOT MEASURED"*)
    ok "a lab that could not take a timed reading is NOT MEASURED" ;;
  *) bad "a timed reading the machine missed was counted as something else" ;;
esac
case "$out" in
  *"the reading was taken 5s after the change"*)
    ok "and the report says which reading and by how much" ;;
  *) bad "nothing said which check could not be measured" ;;
esac
case "$out" in
  *"gg-late            FAIL"*)
    bad "a reading the machine missed was reported as a product defect" ;;
  *) ok "and it is not reported as a failure" ;;
esac

echo "=== the summary counts them apart, and the suite still exits non-zero ==="
case "$out" in
  *"pass=7  fail=4  notmeasured=1  novpp=5  ran=1"*) ok "the five states are counted apart" ;;
  *) bad "the summary line is not what was counted; got:
$(printf '%s' "$out" | tail -12)" ;;
esac
case "$out" in
  *"could not be run"*) ok "and the summary says what to check" ;;
  *) bad "nothing told the reader where to look" ;;
esac
[ "$rc" -ne 0 ] && ok "a suite with labs it could not run does not exit 0" \
                || bad "the suite exited 0 over labs it never ran"

# And NO VPP on its own, with nothing else wrong, must still be non-zero: a
# suite that ran nothing has cleared nothing.
rm -f "$d"/lab/*.sh
echo '#!/bin/sh' > "$d/lab/cc-panic.sh"
out2=$(sh "$d/tools/run-all-labs.sh" 2>&1); rc2=$?
case "$out2" in
  *"pass=0  fail=0  notmeasured=0  novpp=1  ran=0"*) ok "NO VPP alone is counted alone" ;;
  *) bad "NO VPP alone was miscounted; got:
$(printf '%s' "$out2" | tail -6)" ;;
esac
[ "$rc2" -ne 0 ] && ok "and is enough on its own to fail the suite" \
                 || bad "a suite that ran nothing at all exited 0"

# And NOT MEASURED on its own, with everything else green, must also be
# non-zero. A suite that reads "one check could not be taken" as success is the
# shape this file exists to find.
rm -f "$d"/lab/*.sh
echo '#!/bin/sh' > "$d/lab/gg-late.sh"
echo '#!/bin/sh' > "$d/lab/aa-good.sh"
out3=$(sh "$d/tools/run-all-labs.sh" 2>&1); rc3=$?
case "$out3" in
  *"pass=1  fail=0  notmeasured=1  novpp=0  ran=0"*)
    ok "NOT MEASURED beside a pass is counted apart from it" ;;
  *) bad "NOT MEASURED beside a pass was miscounted; got:
$(printf '%s' "$out3" | tail -8)" ;;
esac
case "$out3" in
  *"could not measure a check they came to measure"*)
    ok "and the summary says what it means and what to do" ;;
  *) bad "the summary said nothing about the check that was not taken" ;;
esac
[ "$rc3" -ne 0 ] && ok "and a suite with one unmeasured check does not exit 0" \
                 || bad "a suite that could not measure a check exited 0"

echo "=== how many labs at once is bounded by memory, not only by cores ==="
# Each lab is a container with --memory=5g and a VPP that reserves hugepages up
# front. Sixteen of those is 80 GB of limits, and on the 62.8 GB box this
# project runs on the way that failed was not a clean refusal: one container's
# VPP did not start, its lab ran every check against a dataplane that was not
# there, and the suite reported eight product failures - one of them a checksum
# failure, on the run right after a checksum change landed.
#
# The arithmetic is read out of the script itself rather than copied here,
# because a second copy of it is the thing this repository keeps finding.
sed -n '/^_jobs_by_mem()/,/^}/p' "$REPO/tools/run-all-labs.sh" > "$d/jobs.sh"
jobs_at() {  # <MemTotal in kB> -> the default job count
  printf 'MemTotal: %s kB\n' "$1" > "$d/meminfo"
  HEXA_MEMINFO="$d/meminfo" sh -c ". $d/jobs.sh; _jobs_by_mem"
}
got=$(jobs_at 8388608)
[ "$got" = "1" ] && ok "an 8 GB box runs one lab at a time" \
  || bad "an 8 GB box was given $got jobs, which is 5 GB each on 8 GB"
got=$(jobs_at 65970176)
[ "$got" = "8" ] && ok "and this box's 62 GB gives 8, which is what its clean runs used" \
  || bad "62 GB gave $got jobs; 16 of them is 80 GB of limits on 62"
got=$(jobs_at 536870912)
[ "$got" = "16" ] && ok "and a 512 GB box is still capped at 16, because cores run out too" \
  || bad "a 512 GB box was given $got jobs, with no core-based cap"
# And the arithmetic has to be what the suite actually uses. The three checks
# above read _jobs_by_mem straight out of the file, so they stay green on a
# script that computes the right number and then ignores it - which is exactly
# what putting the constant back does, and the first mutation of this section
# proved by giving zero red. This is the line that connects the two.
if grep -q 'JOBS=${LAB_JOBS:-$(_jobs_by_mem)}' "$REPO/tools/run-all-labs.sh"; then
  ok "and the suite's own default is that function rather than a constant"
else
  bad "run-all-labs.sh computes a job count from memory and does not use it: "\
"$(grep -n '^JOBS=' "$REPO/tools/run-all-labs.sh")"
fi

# And the override has to keep working: a box with more memory per core should
# be able to say so.
got=$(LAB_JOBS=3 sh -c 'JOBS=${LAB_JOBS:-99}; echo $JOBS')
[ "$got" = "3" ] && ok "and LAB_JOBS still overrides the default" \
  || bad "LAB_JOBS no longer overrides"

echo
echo "=== what the suite does NOT run is data, with a reason, and is announced ==="
# Five lab files were excluded by a `case` pattern and no run ever mentioned
# them. The suite's own rule, eleven sections below that line, is that a lab
# which did not run has shown nothing - applied to the accidents and not to the
# exclusions, so a name added to that pattern removed a lab from every future
# run with fail=0 still printed (#233).
RA=$REPO/tools/run-all-labs.sh
NL=$(sed -n '/^NOT_LABS="/,/^"$/p' "$RA" | grep ':' || true)
n_nl=$(printf '%s\n' "$NL" | grep -c ':' || true)
if [ "${n_nl:-0}" -ge 5 ]; then
  ok "$n_nl name(s) are excluded as data rather than as a shell pattern"
else
  bad "only ${n_nl:-0} exclusion(s) were found in NOT_LABS, so nothing below "\
"is comparing anything"
fi

# Every one carries a reason. This is the whole mechanism: the cost of hiding a
# lab is having to write down why, where a reviewer reads it.
noreason=$(printf '%s\n' "$NL" | awk -F: 'NF<2 || length($2)<10 {print $1}')
[ -z "$noreason" ] && ok "and each one says why, in a sentence" \
  || bad "these are excluded with no reason given: $noreason"

# And every excluded name is a lab file that exists. A stale entry is an
# exclusion nobody can evaluate, and it hides the next lab that takes the name.
ghosts=""
for _n in $(printf '%s\n' "$NL" | cut -d: -f1); do
  [ -f "$REPO/lab/$_n.sh" ] || ghosts="$ghosts $_n"
done
[ -z "$ghosts" ] && ok "and every one names a file in lab/" \
  || bad "NOT_LABS excludes names with no lab file:$ghosts"

# The loop must use that data and not a pattern of its own. Without this the
# three checks above stay green on a script that carries the table and then
# skips by `case` anyway, which is the shape the job-count section above was
# caught by.
if grep -q 'not_lab_reason "$n" >/dev/null && continue' "$RA"; then
  ok "and the loop skips by that table rather than by a pattern of its own"
else
  # Named rather than grepped for. The obvious message here would print every
  # `case "$n" in` in the file, and the announcement loop legitimately has one,
  # so a reader would be sent to the wrong line.
  bad "run-all-labs.sh carries NOT_LABS and does not build its lab list from "\
"it. The lab loop must skip with: not_lab_reason \"\$n\" >/dev/null && continue"
fi

# Announced before the first result, so it is in the artefact a release
# decision is read from rather than only in the source.
if grep -q 'echo "not running, each for its own reason' "$RA"; then
  ok "and the run says so before the first result"
else
  bad "the suite excludes labs and never prints that it did"
fi

echo
echo "=== and the lab half: the log has to survive the container at all ==="
# The other half of the same instrument, and the half the report above cannot
# invent. tools/lab.sh gives each lab a container it throws away, so
# /tmp/vpp.log dies with it and stdout is the only channel out. Every lab ends
# in verdict(), which is why the dump lives there rather than in 36 labs -
# and ten of those labs have no wait guard at all, so it is the only place
# their log can reach a reader.
#
# No container here either. verdict() is sourced and called directly, with a
# vppctl on PATH that answers or refuses on demand, the way test_lab_workers.sh
# reads startup_conf's output without a box (#229).
V=$(mktemp -d); trap 'rm -rf "$d" "$V"' EXIT
mkdir -p "$V/bin" "$V/tmp"
cat > "$V/bin/vppctl" <<'EOF'
#!/bin/sh
[ -f "$LAB_TMP/answering" ] && exit 0
exit 1
EOF
chmod +x "$V/bin/vppctl"
printf 'vlib_worker_thread_barrier_sync_int: worker thread deadlock
vpp exited
' \
  > "$V/tmp/vpp.log"

run_verdict() {  # <failures> <notmeasured> <answering?> -> what it prints
  rm -f "$V/tmp/answering"
  [ "$3" = answering ] && : > "$V/tmp/answering"
  PATH="$V/bin:$PATH" LAB_TMP="$V/tmp" sh -c \
    ". $REPO/lab/verdict.sh
     VERDICT_FAILURES=$1; VERDICT_NOMEASURE=$2; verdict" 2>&1
}

got=$(run_verdict 1 0 gone)
case "$got" in
  *"NO DATAPLANE at the verdict"*"worker thread deadlock"*)
    ok "a failing lab with no dataplane prints VPP's log at its verdict" ;;
  *) bad "the container was thrown away with the only account of why; got:
$got" ;;
esac

# Not on a pass. ipfix-stop, ipfix-outbuf, oom and snmp each kill VPP on
# purpose and assert what happens next, so an unconditional dump puts a page of
# log underneath a green run.
got=$(run_verdict 0 0 gone)
case "$got" in
  *"NO DATAPLANE"*) bad "a lab that stops VPP on purpose got a log dump under
its pass; got:
$got" ;;
  *"LAB PASSED"*) ok "and a lab that killed VPP itself and passed does not" ;;
  *) bad "the passing verdict changed shape; got:
$got" ;;
esac

# And not when VPP is there. A lab that fails for a product reason is answered
# by its red checks, not by a page of a healthy box's log.
got=$(run_verdict 1 0 answering)
case "$got" in
  *"NO DATAPLANE"*) bad "a live dataplane's log was dumped under a product
failure; got:
$got" ;;
  *"LAB FAILED"*) ok "and neither does a red lab whose dataplane is answering" ;;
  *) bad "the failing verdict changed shape; got:
$got" ;;
esac

# NOT MEASURED is the other verdict that is not a pass, and it has the same
# question under it: a check whose timing was missed on a box whose dataplane
# has gone is not a box that was slow, it is a box that is not there.
got=$(run_verdict 0 1 gone)
case "$got" in
  *"LAB NOT MEASURED"*"NO DATAPLANE at the verdict"*)
    ok "and a NOT MEASURED verdict with no dataplane carries the log too" ;;
  *) bad "NOT MEASURED was left with nothing to say about a missing box; got:
$got" ;;
esac

# And the reason reaches the LINE, not only the table.
#
# The first version of this section read NOT_LABS and stopped there, so it was
# green while every announcement printed one word: `for _e in $NOT_LABS` splits
# on every space, each WORD of a reason became its own iteration, and the first
# one matched. A release run printed
#
#   not running, each for its own reason:
#     boot-iso: the
#     crossaddr: pins
#
# A check that reads the data a message is built from cannot see the message.
# This one runs the lookup the suite uses and asks what comes back.
_probe=$(mktemp)
sed -n '/^NOT_LABS="/,/^"$/p' "$RA" > "$_probe"
sed -n '/^not_lab_reason() {/,/^}/p' "$RA" >> "$_probe"
printf 'for _n in $(sed -n "/^NOT_LABS=/,/^\"$/p" "%s" | grep ":" | cut -d: -f1); do\n' "$RA" >> "$_probe"
printf '  not_lab_reason "$_n"; echo\ndone\n' >> "$_probe"
short=""
for _r in $(sh "$_probe" | grep . | tr ' ' '_'); do
  case "$_r" in
    *_*) : ;;
    *) short="$short $_r" ;;
  esac
done
rm -f "$_probe"
[ -z "$short" ] \
  && ok "and every reason comes back whole, not cut at its first space" \
  || bad "these reasons come back as a single word, so the announcement prints "\
"a fragment:$short"

echo
echo "=== a lab that says it is single-threaded is not run with workers ==="
# A lab whose counts depend on the order the free list hands blocks out cannot
# be run with workers: the list is sharded per worker, so the same lab measures
# a different arrangement and still prints numbers. lab/crossaddr.sh says so in
# its header, and the list it talks about is in another file, with nothing
# comparing the two - which is the shape this repository keeps finding.
#
# The marker is the comparable half. A lab carrying it MUST NOT be in
# BOTH_WAYS.
_both=$(sed -n '/^BOTH_WAYS="/,/"$/p' "$RA" | tr -d '"' | sed 's/^BOTH_WAYS=//')
_marked=""
_wrong=""
for _f in "$REPO"/lab/*.sh; do
  grep -q '^# NOT-BOTH-WAYS:' "$_f" || continue
  _n=$(basename "$_f" .sh)
  _marked="$_marked $_n"
  case " $_both " in *" $_n "*) _wrong="$_wrong $_n" ;; esac
done
[ -z "$_wrong" ] \
  && ok "none of the marked lab(s) is in BOTH_WAYS:$_marked" \
  || bad "these labs say they are single-threaded by construction and the "\
"suite runs them with workers anyway, where every count they print is about "\
"an arrangement they are not measuring:$_wrong"

# And the check is reading something. Without this it passes on a marker that
# has been renamed, which is how a check stops checking without going red.
if [ -n "$_marked" ]; then
  ok "the marker was found, so the comparison above happened"
else
  bad "no lab carries '# NOT-BOTH-WAYS:', so the check above compared nothing. "\
"Either the marker was renamed or the last lab using it was deleted"
fi

# The reason has to be there too. A bare marker is a rule with no why, and the
# next person reads the list rather than the argument.
_bare=""
for _n in $_marked; do
  _r=$(sed -n 's/^# NOT-BOTH-WAYS:[[:space:]]*//p' "$REPO/lab/$_n.sh")
  case "$_r" in
    ?*" "?*) : ;;
    *) _bare="$_bare $_n" ;;
  esac
done
[ -z "$_bare" ] \
  && ok "and each one says why" \
  || bad "these carry the marker with no reason after it:$_bare"

echo
echo "=== a lab that takes an off mode is given one by the suite ==="
# The off run is what makes a green lab worth anything: seventeen labs said in
# their own header that their checks MUST fail with the feature off, and until
# OFF_MODES existed nothing ever performed one. #151 is a list of assertions
# that could not fail, found that way.
#
# OFF_MODES is a second copy of a fact each lab already states by taking a mode
# argument, and nothing compared the two. lab/detreserve.sh was written with an
# off run described in its header, in the words "the extension assertions MUST
# then go red", and was not in the list: the control never ran.
#
# The signal is `MODE=${1:-...}` at the top of the lab, which is how every lab
# that takes one spells it. ONE DIRECTION ONLY: three labs in OFF_MODES do not
# have that shape because the opposite of what they test is not a mode of the
# lab - rtsp switches on an environment variable, ftp-alg and sip spell theirs
# differently - so requiring the shape of everything in the list would go red
# on three labs that are right.
_off_names=$(sed -n '/^OFF_MODES="/,/"$/p' "$RA" | tr ' ' '\n' \
             | sed 's/^OFF_MODES="//' | grep ':' | cut -d: -f1)
_ungiven=""
for _f in "$REPO"/lab/*.sh; do
  grep -qE '^[[:space:]]*MODE=\$\{1:-' "$_f" || continue
  _n=$(basename "$_f" .sh)
  printf '%s\n' "$_off_names" | grep -qx "$_n" || _ungiven="$_ungiven $_n"
done
[ -z "$_ungiven" ] \
  && ok "every lab that takes a mode argument is in OFF_MODES" \
  || bad "these labs take a mode argument and the suite never gives them one, "\
"so the run that is supposed to go red never happens and their green run "\
"proves nothing:$_ungiven"

# And OFF_MODES may not name a lab that is not there. A typo would silently
# stop giving a real lab its off run, which is the same defect spelled the
# other way.
_ghost=""
for _n in $_off_names; do
  [ -f "$REPO/lab/$_n.sh" ] || _ghost="$_ghost $_n"
done
[ -z "$_ghost" ] \
  && ok "and every name in OFF_MODES is a lab that exists" \
  || bad "OFF_MODES names these and there is no such lab, so whatever it was "\
"meant to be is getting no off run:$_ghost"

echo
echo "=== a run whose tree moved under it says so, and does not pass ==="
# THE DEFECT THIS HOLDS. The suite reads the tree as each lab STARTS, so a
# merge landing mid-run gives a verdict about no single tree: the labs before
# it measured one product and the labs after it measured another, and the
# summary adds the two together. Done on 2026-09-14 - eight merges over forty
# minutes, three of them touching the very labs being run - and the output was
# 85 pass / 1 fail / 1 novpp with nothing in it that looked wrong. That is
# worse than a false NO VPP: the number was clean and meant nothing.
#
# Not a lock, deliberately. The script's own header explains why: a lock is
# removed by whoever has the branch ready and the green suite in front of them.
# A verdict that reports its own validity is not something anybody has to
# remember.
#
# Driven by making a STUB LAB commit. That is the product's own path: the
# suite takes its two readings around the labs, and what moves the tree between
# them is not this test's business.
g=$(mktemp -d)
mkdir -p "$g/tools" "$g/lab"
cp "$REPO/tools/run-all-labs.sh" "$g/tools/"
cp "$d/tools/build.sh" "$g/tools/"
git -C "$g" init -q
git -C "$g" -c user.email=t@t -c user.name=t -c commit.gpgsign=false \
  add -A >/dev/null 2>&1
git -C "$g" -c user.email=t@t -c user.name=t -c commit.gpgsign=false \
  commit -q -m "the tree the suite starts on"

cat > "$g/tools/lab.sh" <<'LABEOF'
#!/bin/sh
# One lab lands a commit while the suite is running, which is the whole case.
if [ "$1" = "zz-merger" ]; then
  R=$(cd "$(dirname "$0")/.." && pwd)
  date > "$R/landed-mid-run"
  git -C "$R" -c user.email=t@t -c user.name=t -c commit.gpgsign=false     add -A >/dev/null 2>&1
  git -C "$R" -c user.email=t@t -c user.name=t -c commit.gpgsign=false     commit -q -m "a merge that landed while the suite was reading the tree"
fi
echo "  [ ok ] something was measured"
echo "LAB PASSED"
exit 0
LABEOF
chmod +x "$g/tools/lab.sh"
for n in aa-good zz-merger; do echo '#!/bin/sh' > "$g/lab/$n.sh"; done
git -C "$g" -c user.email=t@t -c user.name=t -c commit.gpgsign=false \
  add -A >/dev/null 2>&1
git -C "$g" -c user.email=t@t -c user.name=t -c commit.gpgsign=false \
  commit -q -m "the labs"

mout=$(sh "$g/tools/run-all-labs.sh" 2>&1); mrc=$?
case "$mout" in
  *"THIS RUN IS ABOUT NO SINGLE TREE"*)
    ok "a tree that moved mid-run is reported, not summed" ;;
  *) bad "the suite summed two trees and said nothing; got:
$mout" ;;
esac
[ "$mrc" -ne 0 ] \
  && ok "and the run does not pass, because the counts are about nothing" \
  || bad "every lab passed and the suite exited 0 over a verdict about two trees"
case "$mout" in
  *"a merge that landed while the suite was reading the tree"*)
    ok "and it names what landed, so the reader knows what changed" ;;
  *) bad "it said the tree moved without saying to what" ;;
esac
case "$mout" in
  *"pass=2"*) ok "the counts are still printed, above the line that voids them" ;;
  *) bad "the counts went missing, which is a different report" ;;
esac
rm -rf "$g"

echo
echo "=== a suite that lost a lab does not report on the ones it kept ==="
# The four counters are internally consistent with each other whatever
# happens. A lab that never reaches judge_batch just makes the totals smaller,
# and the suite still says everything it ran passed - a verdict about fewer
# labs than the repository has, and nothing anywhere saying so.
#
# Arranged by making the RUN LOOP skip one that is already in the list, which
# is the shape of the real fault: a batch that lost a file, a name that
# produced none. The counters are what this is about and are not touched -
# editing them would arrange the symptom rather than the cause.
l=$(mktemp -d)
mkdir -p "$l/tools" "$l/lab"
cp "$REPO/tools/run-all-labs.sh" "$l/tools/"
cp "$d/tools/build.sh" "$d/tools/lab.sh" "$l/tools/"
cp "$d/lab/aa-good.sh" "$l/lab/" 2>/dev/null || printf '#!/bin/sh\n' > "$l/lab/aa-good.sh"
printf '#!/bin/sh\n' > "$l/lab/zz-dropped.sh"
sed -i 's|^for n in \$LABS; do$|for n in $LABS; do\n  [ "$n" = zz-dropped ] \&\& continue|' \
  "$l/tools/run-all-labs.sh"
grep -q 'zz-dropped.*continue' "$l/tools/run-all-labs.sh" \
  || bad "the run loop could not be arranged to drop a lab, so the case below
is not about what it says"
lout=$(cd "$l" && sh tools/run-all-labs.sh 2>&1); lrc=$?
case "$lout" in
  *"THE SUITE LOST A LAB"*)
    ok "it says so, rather than reporting on the ones it kept" ;;
  *) bad "a suite that accounted for fewer labs than it set out to run said
nothing about it; got:
$lout" ;;
esac
[ "$lrc" != 0 ] \
  && ok "and the run does not pass" \
  || bad "the suite lost a lab and still exited 0, which is a green verdict
over something nobody measured"
rm -rf "$l"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
