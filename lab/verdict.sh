# Shared verdict helpers for labs. Source this, do not run it.
#
# A lab that only prints observations makes a human the test oracle. These
# helpers make each lab state its own pass or fail and exit accordingly, so
# "run everything" produces an answer instead of a reading assignment.
VERDICT_FAILURES=0

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

verdict() {
  echo
  if [ "$VERDICT_FAILURES" -eq 0 ]; then
    echo "LAB PASSED"
    exit 0
  fi
  echo "LAB FAILED: $VERDICT_FAILURES check(s)"
  exit 1
}
