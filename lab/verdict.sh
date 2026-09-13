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

verdict() {
  echo
  if [ "$VERDICT_FAILURES" -gt 0 ]; then
    echo "LAB FAILED: $VERDICT_FAILURES check(s)"
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
    exit 2
  fi
  echo "LAB PASSED"
  exit 0
}
