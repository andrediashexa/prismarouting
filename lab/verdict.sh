# Shared verdict helpers for labs. Source this, do not run it.
#
# A lab that only prints observations makes a human the test oracle. These
# helpers make each lab state its own pass or fail and exit accordingly, so
# "run everything" produces an answer instead of a reading assignment.
VERDICT_FAILURES=0
# Checks whose timing the machine could not keep. Not failures, and not passes
# either: see expect_timed().
VERDICT_NOMEASURE=0

# expect <description> <command...>  - command's exit status is the verdict
expect() {
  _d=$1; shift
  if "$@" >/dev/null 2>&1; then
    echo "  [ ok ] $_d"
  else
    echo "  [FAIL] $_d"
    VERDICT_FAILURES=$((VERDICT_FAILURES + 1))
  fi
}

# expect_in <description> <needle> <haystack>
expect_in() {
  case "$3" in
    *"$2"*) echo "  [ ok ] $1" ;;
    *) echo "  [FAIL] $1 (missing: $2)"
       VERDICT_FAILURES=$((VERDICT_FAILURES + 1)) ;;
  esac
}

# expect_said <description> <sentence> <haystack>
#
# Like expect_in, but both sides are whitespace-collapsed first, so a sentence
# the VTY WRAPPED still matches.
#
# expect_in searches the raw text, which means a needle only matches while the
# message it is in happens to break lines somewhere else. #271 re-wrapped 28
# messages to fit an 80-column console and two needles in lab/routing.sh
# stopped matching: `'systemctl restart frr'` became `'systemctl` at the end of
# one row and `restart frr'` at the start of the next, and `FRR validates them
# at commit` split after `at`. Neither message changed a word. Every unit suite
# was green, because none of them reads a lab.
#
# So the needle has to be about the SENTENCE and not about where the sentence
# happened to fold. This is the shell half of unwrapped() in
# test/test_hexash_bgp.py, which exists for the same reason.
#
# BOTH sides are collapsed, not only the haystack. A caller writing a long
# sentence across two source lines is how these needles are written, and
# collapsing one side alone fails on exactly that.
#
# Use expect_in for anything printed WHOLE - a statement, a path, a
# configuration line, a counter. Those must not wrap, and collapsing the text
# before comparing would hide it if they did. That is a different question
# and it has its own answer.
expect_said() {
  _said=$(printf '%s' "$3" | tr '\n' ' ' | tr -s ' ')
  _want=$(printf '%s' "$2" | tr '\n' ' ' | tr -s ' ')
  case "$_said" in
    *"$_want"*) echo "  [ ok ] $1" ;;
    *) echo "  [FAIL] $1 (not said: $2)"
       VERDICT_FAILURES=$((VERDICT_FAILURES + 1)) ;;
  esac
}

# expect_not_in <description> <needle> <haystack>
expect_not_in() {
  case "$3" in
    *"$2"*) echo "  [FAIL] $1 (unexpected: $2)"
            VERDICT_FAILURES=$((VERDICT_FAILURES + 1)) ;;
    *) echo "  [ ok ] $1" ;;
  esac
}

# expect_timed <description> <window-seconds> <elapsed-seconds> <command...>
#
# A check whose answer is only meaningful if the reading was taken inside a
# window, and which says so instead of blaming the product when it was not.
#
# The case it was written for: lab/igp.sh clears an IS-IS adjacency and then
# asserts it is gone. The hello interval is three seconds, so the adjacency can
# be back before a loaded box gets around to reading the state - and the check
# then reports "clearing it took the adjacency down there and then" as a
# product defect. It did exactly that in one release suite and passed in
# isolation, which cost an investigation.
#
# The margin cannot be made large enough to be safe, because a box that is
# thrashing misses any margin. So what is measured is the READING: on time and
# wrong is a failure, which is this check's whole subject; late is nothing
# learnt. That is the shape test_boot_wait.sh took for the same reason (#218),
# and NOT MEASURED is not a pass - the lab still exits non-zero and the suite
# counts it against itself.
expect_timed() {
  _d=$1; _window=$2; _elapsed=$3; shift 3
  if "$@" >/dev/null 2>&1; then
    echo "  [ ok ] $_d (read ${_elapsed}s after)"
  elif [ "${_elapsed:-0}" -ge "$_window" ]; then
    echo "  [NOT MEASURED] $_d"
    echo "         the reading was taken ${_elapsed}s after the change and the"
    echo "         window is ${_window}s, so this says nothing either way"
    VERDICT_NOMEASURE=$((VERDICT_NOMEASURE + 1))
  else
    echo "  [FAIL] $_d (read ${_elapsed}s after, inside the ${_window}s window)"
    VERDICT_FAILURES=$((VERDICT_FAILURES + 1))
  fi
}

# What the dataplane said on its way out, when it is not there at the verdict.
#
# #253: a "+workers" lab in a release suite created its host interface and then
# every vppctl call in it was refused. The suite reported NO VPP, which was the
# right verdict, and had nothing to say WHY - because tools/lab.sh gives each
# lab a container it throws away, so /tmp/vpp.log dies with it. The question
# the run could not answer is whether the 30 s wait at the top of the lab
# expired or VPP answered and then went, and those two need opposite work.
#
# Printed here rather than copied out of the container. stdout is the only
# channel that outlives a "docker run --rm", and tools/run-all-labs.sh already
# captures it per lab and prints it whole. Copying the file to /src would put
# every lab's copy of one name into the shared bind mount, where concurrent
# labs overwrite each other and a leftover from the last run reads as this
# one's.
#
# Only on a verdict that is not a pass, and only when vppctl cannot reach a
# dataplane. Labs stop VPP on purpose - ipfix-stop, ipfix-outbuf, oom and snmp
# each kill it and assert what happens next - so an unconditional dump puts a
# page of log underneath a pass.
#
# It says what it saw and nothing more. Ten labs - among them icmp-tcp, which
# is the one tools/run-all-labs.sh names for refusing on line 2 of its own
# output - start VPP with no wait guard at all, so for those this is the ONLY
# place the log can reach a reader, and "it answered and then went" would be a
# claim nothing here checked. Which of the two it was is read off the capture
# instead: "VPP did not start" is the wait expiring, this marker alone is not.
#
# THE MARKER IS EVIDENCE AND NOT A VERDICT. tools/run-all-labs.sh deliberately
# does NOT read it as "the dataplane never came up": a lab that measured
# something and then lost VPP stays a FAIL, because calling that a machine
# problem hides a real crash. All this does is put the log where the reader is.
vpp_gone_report() {
  vppctl show version >/dev/null 2>&1 && return 0
  # Both names the labs use. Most write /tmp/vpp.log; ipfix-stop and
  # ipfix-outbuf start several boxes in one run and number them. Newest first,
  # because the one that just went is the one being explained.
  #
  # LAB_TMP for the same reason lib.sh takes LAB_SRC: it is /tmp everywhere a
  # lab actually runs, and a rule about which file gets read should not need a
  # container to check. test/test_run_all_labs.sh drives this function with it.
  _log=$(ls -t "${LAB_TMP:-/tmp}"/vpp.log "${LAB_TMP:-/tmp}"/vpp-*.log \
         2>/dev/null | head -1)
  [ -n "${_log:-}" ] && [ -r "$_log" ] || return 0
  echo
  echo "NO DATAPLANE at the verdict: vppctl cannot reach VPP. Last of $_log:"
  tail -20 "$_log" | sed 's/^/  | /'
}

verdict() {
  echo
  if [ "$VERDICT_FAILURES" -gt 0 ]; then
    echo "LAB FAILED: $VERDICT_FAILURES check(s)"
    vpp_gone_report
    exit 1
  fi
  # Reported before PASSED and exiting non-zero, because a run that could not
  # measure what it came to measure has not cleared anything - and a suite that
  # reads it as a pass is the failure this whole file exists to prevent.
  if [ "$VERDICT_NOMEASURE" -gt 0 ]; then
    echo "LAB NOT MEASURED: $VERDICT_NOMEASURE check(s)"
    echo "  The machine could not keep the timing those checks need, so they"
    echo "  said nothing about the product either way. Run this lab on a box"
    echo "  that is not doing something else at the same time."
    vpp_gone_report
    exit 2
  fi
  echo "LAB PASSED"
  exit 0
}
