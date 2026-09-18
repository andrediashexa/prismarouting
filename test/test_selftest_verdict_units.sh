#!/bin/sh
# The four readers in tools/selftest-verdict.sh, each asked about each verdict.
#
# That file exists because four readers of the self-test's last line went
# stale one at a time and the release gate refused a correct medium twice
# (#297), then accepted a medium whose self-test had FAILED because the reader
# that could tell was defined and called by nobody for two releases (#311).
# test/test_selftest.sh holds that no reader spells the sentences by hand;
# nothing held what the four functions answer. A reader that says yes to the
# wrong verdict is the #311 defect again with the function in place.
#
# The fragment is sourced as its readers source it: it defines four functions
# and four patterns and runs nothing. The consoles are four files of this
# suite's own, one per verdict the self-test can end with and one with none,
# and the sentences are the ones appliance/selftest prints, so that a change
# to a sentence there goes red here as well as in test_selftest.sh.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }

REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
. "$REPO/tools/selftest-verdict.sh"

printf '  [ ok ] dataplane forwarding on 0000:01:00.0\n  Prisma Routing ready.\n' > "$WORK/ready"
printf '  [ ok ] records volume\n  Prisma Routing ready, with 2 warning(s) above.\n' > "$WORK/warned"
printf '  Prisma Routing is installed and healthy, and has no NIC to forward on:\n' > "$WORK/nofwd"
printf '  [FAIL] vpp.service\n  2 check(s) failed. The box is NOT ready.\n' > "$WORK/failed"
printf '  [ ok ] something\nprisma-routing login: \n' > "$WORK/none"

# What each reader answers, as yes/no, on its own line so the call stands in
# command position for test/test_every_function.py.
echo "=== said_a_verdict: any of the four, and not a console that says none ==="
for f in ready warned nofwd failed; do
  said_a_verdict "$WORK/$f" \
    && ok "a console ending '$f' reached a verdict" \
    || bad "the '$f' verdict was not read as a verdict at all"
done
said_a_verdict "$WORK/none"; rc=$?
[ "$rc" -ne 0 ] \
  && ok "and a console with a login prompt and no verdict is no verdict" \
  || bad "a console that never reached a verdict was read as one"

echo "=== said_a_good_verdict: fit to hand over, which the failed box is not ==="
for f in ready warned nofwd; do
  said_a_good_verdict "$WORK/$f" \
    && ok "'$f' is a box that can be handed over" \
    || bad "'$f' was not read as a good verdict"
done
said_a_good_verdict "$WORK/failed"; rc=$?
[ "$rc" -ne 0 ] \
  && ok "and 'N check(s) failed' is NOT good, which is the reader #311 found missing" \
  || bad "a box that failed its own checks was read as fit to hand over"
said_a_good_verdict "$WORK/none"; rc=$?
[ "$rc" -ne 0 ] \
  && ok "nor is a console with no verdict" \
  || bad "no verdict at all was read as a good one"

echo "=== said_a_no_forward_verdict: the third verdict alone ==="
said_a_no_forward_verdict "$WORK/nofwd" \
  && ok "'installed and healthy, and has no NIC' is the no-forward verdict" \
  || bad "the no-forward verdict was not recognised"
for f in ready warned failed none; do
  said_a_no_forward_verdict "$WORK/$f"; rc=$?
  [ "$rc" -ne 0 ] \
    && ok "and '$f' is not it" \
    || bad "'$f' was read as the no-forward verdict"
done

echo "=== said_a_failed_verdict: the fourth alone ==="
said_a_failed_verdict "$WORK/failed" \
  && ok "'N check(s) failed. The box is NOT ready.' is the failed verdict" \
  || bad "the failed verdict was not recognised"
for f in ready warned nofwd none; do
  said_a_failed_verdict "$WORK/$f"; rc=$?
  [ "$rc" -ne 0 ] \
    && ok "and '$f' is not it" \
    || bad "'$f' was read as the failed verdict"
done

echo "=== and the sentences are the self-test's own ==="
# Each fixture above is checked against appliance/selftest's text, so that a
# reworded sentence there does not leave these five consoles asserting a
# product that no longer exists.
for s in "Prisma Routing ready." "Prisma Routing is installed and healthy, and has" \
         "check(s) failed. The box is NOT ready."; do
  grep -qF "$s" "$REPO/appliance/selftest" \
    && ok "appliance/selftest still says '$s'" \
    || bad "appliance/selftest no longer says '$s', so the consoles above are fiction"
done

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
