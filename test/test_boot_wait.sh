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
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }

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
vppctl_says "[ -f $BIN/up ] && exit 0
exit 1"
rm -f "$BIN/up"; ( sleep 2; touch "$BIN/up" ) &
run 10; rc=$?
out=$(cat "$OUTD/out" 2>/dev/null || true)
rm -f "$BIN/up"
[ "$rc" = 0 ] || bad "a short wait must still succeed, got $rc"
if [ -n "$out" ]; then
  bad "a two-second wait was announced: $out"
else
  ok "two seconds passes without a word"
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

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
