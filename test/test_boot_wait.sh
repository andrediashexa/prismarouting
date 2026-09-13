#!/bin/sh
# The boot wait, and the three things it must tell apart.
#
# The wait itself is legitimate: systemd considers vpp.service started as soon
# as the process exists, while the CLI socket and the plugins take longer.
# What was not legitimate is the screen. systemd prints "(22s / no limit)",
# and on a box somebody is watching for the first time that reads as "this is
# not going to finish" (#97).
set -u
fail=0
skip=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }
# Neither a pass nor a failure, and it must not be reported as either. A check
# whose timing the machine could not keep has said nothing about the product,
# and this is the same third state tools/run-all-labs.sh gives a lab whose
# dataplane never came up: reporting it green would be a claim nothing
# measured, and reporting it red would blame the product for the scheduler.
nomeasure() { echo "  NOT MEASURED: $*"; skip=$((skip+1)); }

REPO=$(cd "$(dirname "$0")/.." && pwd)
WAIT="$REPO/appliance/wait-for-vpp"
BIN=$(mktemp -d); OUTD=$(mktemp -d)
trap 'rm -rf "$BIN" "$OUTD"' EXIT

vppctl_says() { printf '#!/bin/sh\n%s\n' "$1" > "$BIN/vppctl"; chmod +x "$BIN/vppctl"; }
run() {  # <timeout> [quiet] -> writes $OUTD/out, returns the exit code
  #
  # An empty <quiet> uses the SHIPPED default. The first case below must use
  # it: overriding the setting under test is how a bad default passes - and
  # the first version of this file did exactly that, so a quiet period of zero
  # went uncaught.
  rm -f "$OUTD/out"
  if [ -n "${2:-}" ]; then
    PATH="$BIN:$PATH" HEXA_WAIT_OUT="$OUTD/out" HEXA_WAIT_QUIET="$2" \
      sh "$WAIT" "$1" >/dev/null 2>&1
  else
    PATH="$BIN:$PATH" HEXA_WAIT_OUT="$OUTD/out" \
      sh "$WAIT" "$1" >/dev/null 2>&1
  fi
}

echo "=== a wait that does not happen says nothing ==="
# A message about a wait nobody noticed teaches the reader that these messages
# are noise, and then the one that matters is noise too.
vppctl_says "exit 0"
run 10; rc=$?
[ "$rc" = 0 ] || bad "an answering dataplane must succeed, got $rc"
if [ -s "$OUTD/out" ]; then
  bad "it said something about a wait that did not happen: $(cat "$OUTD/out")"
else
  ok "silent, and exit 0"
fi

echo "=== a SHORT wait is still silent ==="
# The case the quiet period is for, and the one the previous check cannot see:
# with no wait at all, silence is true whatever the setting. A boot where the
# dataplane answers in two seconds must still say nothing, or the banner
# appears on every boot and stops meaning anything.
#
# UP_AT seconds against a shipped quiet period of three leaves MARGIN seconds,
# and that margin is the whole assertion. It used to be nothing: the wait
# counted polls as seconds, so the banner fell at 2.009 s measured against a
# stimulus at 2.000 s, and which of the two won was decided by how long a fork
# took. Under a release lab suite with sixteen containers up that lost about
# one run in three (#218), and the only record kept was a count of summary
# lines, so the check doing it was never named.
#
# The margin cannot be made large enough to be safe on any machine, because a
# box that is thrashing will miss any margin at all. So what gets measured is
# not the product but the STIMULUS: the moment the fake dataplane actually
# started answering. That is the one number that separates the two stories.
#
#   the dataplane answered on time and the banner still appeared
#       -> the quiet period is too short, which is this check's whole subject
#   the dataplane answered a margin late
#       -> nothing was asked of the quiet period, because the wait the banner
#          described really did happen
#
# Both can be true at once on a loaded box, and then this says NOT MEASURED
# over a defect. That is the safe direction only because NOT MEASURED is not a
# pass: the suite still exits non-zero and says the run proved nothing, so a
# quiet period that shrank cannot reach green by waiting for a busy machine.
UP_AT=2
MARGIN=1
vppctl_says "[ -f $BIN/up ] && exit 0
exit 1"
rm -f "$BIN/up" "$OUTD/up_at"
t0=$(date +%s%N)
( sleep "$UP_AT"; date +%s%N > "$OUTD/up_at"; touch "$BIN/up" ) &
run 10; rc=$?
# Empty when the stimulus never got as far as recording itself, which is a box
# that could not run the subshell at all. Reading that as "answered in 1970"
# would send it to the branch that blames the product.
up_ns=$(cat "$OUTD/up_at" 2>/dev/null || true)
if [ -n "$up_ns" ]; then
  up_ms=$(( (up_ns - t0) / 1000000 )); when="${up_ms}ms"
else
  up_ms=""; when="a moment it never recorded"
fi
out=$(cat "$OUTD/out" 2>/dev/null || true)
rm -f "$BIN/up"
[ "$rc" = 0 ] || bad "a short wait must still succeed, got $rc"
if [ -z "$out" ]; then
  ok "${UP_AT} seconds passes without a word (answered at $when)"
elif [ -z "$up_ms" ]; then
  nomeasure "the fake dataplane never recorded when it answered, so there is nothing to compare the banner against"
elif [ "$up_ms" -gt $(( (UP_AT + MARGIN) * 1000 )) ]; then
  nomeasure "the fake dataplane was asked to answer at ${UP_AT}000ms and answered at ${up_ms}ms, past the ${MARGIN}s margin: the banner describes a wait that really happened, so this says nothing about the quiet period"
else
  # It answered on time and the banner appeared anyway: the quiet period is
  # shorter than the wait it is supposed to cover.
  bad "a ${UP_AT}-second wait that answered at ${up_ms}ms was announced: $out"
fi

echo "=== a wait that ends WELL says what it was waiting for, and that it ended ==="
vppctl_says "[ -f $BIN/up ] && exit 0
exit 1"
rm -f "$BIN/up"; ( sleep 5; touch "$BIN/up" ) &
run 20 2; rc=$?
out=$(cat "$OUTD/out" 2>/dev/null || true)
rm -f "$BIN/up"
[ "$rc" = 0 ] || bad "it must succeed when the dataplane answers, got $rc"
case "$out" in
  *"waiting for the dataplane"*) ok "it says what is being waited for" ;;
  *) bad "it waited in silence: $out" ;;
esac
case "$out" in
  *"the dataplane answered after"*) ok "and that the wait ended" ;;
  *) bad "it never said the wait ended: $out" ;;
esac
# An expected wait reported as a problem is how operators learn to ignore a
# console.
case "$out" in
  *FAIL*|*error*|*ERROR*) bad "a normal wait left a warning behind: $out" ;;
  *) ok "and leaves no warning behind" ;;
esac

echo "=== a wait that TIMES OUT is a different answer ==="
vppctl_says "exit 1"
run 4 1; rc=$?
out=$(cat "$OUTD/out" 2>/dev/null || true)
[ "$rc" = 1 ] || bad "a timeout must fail, got $rc"
case "$out" in
  *"did not answer within"*) ok "it says it gave up" ;;
  *) bad "a timeout must say so: $out" ;;
esac
# The consequence, not just the event. "It timed out" leaves the operator to
# work out what that cost them.
case "$out" in
  *"has NOT been applied"*) ok "and what that means for the configuration" ;;
  *) bad "it must say the configuration was not applied: $out" ;;
esac
case "$out" in
  *"the dataplane answered"*) bad "a timeout must not read like a success" ;;
  *) ok "and cannot be mistaken for a success" ;;
esac

if [ "$fail" -gt 0 ]; then echo; echo "$fail FAILURES"; exit 1; fi
if [ "$skip" -gt 0 ]; then
  echo
  echo "$skip NOT MEASURED"
  echo "  The machine could not keep the timing those checks need, so they"
  echo "  said nothing about the product either way. That is not a pass, and"
  echo "  it is not a defect: run it again on a box that is not doing"
  echo "  something else at the same time."
  exit 1
fi
echo; echo "ALL TESTS PASSED"; exit 0
