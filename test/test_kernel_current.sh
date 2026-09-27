#!/bin/sh
# tools/kernel-current.sh (#449): current, behind, ahead, and unasked, by the
# seam that stands in for Debian's answer. Mutation shown red: the comparison
# made `-ge`-blind (any mismatch reported current).
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
. "$REPO/appliance/kernel.env"
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }
run() { KERNEL_CANDIDATE="$1" sh "$REPO/tools/kernel-current.sh" > /dev/null 2>&1; echo $?; }
[ "$(run "$KERNEL_DEB_VERSION")" = 0 ] && ok "the same version is current" || bad "the same version was not current"
[ "$(run "9.9.999-1")" = 1 ] && ok "a newer Debian kernel is a refusal" || bad "a newer Debian kernel passed"
[ "$(run "6.1.1-1")" = 1 ] && ok "an older candidate is refused too: not a version the archive serves" || bad "an older candidate passed"
[ "$(run "(none)")" = 2 ] && ok "no answer is 'not checked', never a pass" || bad "no answer was not 2"
out=$(KERNEL_CANDIDATE=9.9.999-1 sh "$REPO/tools/kernel-current.sh" 2>&1)
case "$out" in *"Move appliance/kernel.env to 9.9.999-1"*) ok "and it says what to change" ;; *) bad "the refusal did not say what to change: $out" ;; esac
echo
[ "$fail" = 0 ] && echo "ALL TESTS PASSED" || echo "$fail FAILURES"
[ "$fail" = 0 ]
