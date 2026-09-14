#!/bin/sh
# lab/verdict.sh: the assertions every lab's verdict is made of.
#
# Nothing tested these. Each lab is judged by them, the suite is judged by the
# labs, and a release is judged by the suite - so a helper that silently
# always passed would make every lab using it green for the wrong reason, and
# the failure would be invisible at every level above it.
#
# It is not hypothetical. THREE needles broke in one day because a message was
# reflowed and `expect_in` searches raw text (#279), and one of them was an
# `expect_not_in` that then passed whether the box said the thing or not - a
# check that had stopped being able to fail, over the box warning about the
# wrong interface. `expect_said` and `expect_not_said` exist for that, and
# until now nothing held them to it either.
#
# WHAT IS ASSERTED IS THE COUNTER, not the words. A helper that prints
# "[FAIL]" and forgets to raise VERDICT_FAILURES is a lab that prints red
# checks and exits 0, which is worse than one that says nothing.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }
REPO=$(cd "$(dirname "$0")/.." && pwd)

# Each case runs in its own shell, because the counters are globals and a
# leaked one would make the next case pass on the previous one's result.
drive() {  # <shell code using the helpers> -> "<failures> <nomeasure>"
  sh -c ". $REPO/lab/verdict.sh >/dev/null 2>&1
         $1
         echo \"\$VERDICT_FAILURES \$VERDICT_NOMEASURE\"" 2>/dev/null | tail -1
}

says() {  # <shell code> -> what it printed
  sh -c ". $REPO/lab/verdict.sh >/dev/null 2>&1
         $1" 2>/dev/null
}

echo "=== expect: the plain one, both ways ==="
[ "$(drive 'expect "x" test 1 -eq 1')" = "0 0" ] \
  && ok "a true check counts nothing" \
  || bad "a true check was counted: $(drive 'expect "x" test 1 -eq 1')"
[ "$(drive 'expect "x" test 1 -eq 2')" = "1 0" ] \
  && ok "and a false one is a failure" \
  || bad "a false check was not counted: $(drive 'expect "x" test 1 -eq 2')"

echo
echo "=== expect_said: the sentence, not where it folded ==="
# The whole reason it exists. The VTY wraps prose to 79 columns, so a needle
# written when the message was one line straddles the break.
WRAPPED='warning: speaks OSPF and its addresses are inside a CGNAT pool: it is facing
    subscribers, and it is not passive'
[ "$(drive "expect_said 'x' 'it is facing subscribers, and it is not passive' '$WRAPPED'")" = "0 0" ] \
  && ok "a sentence the box wrapped still matches" \
  || bad "expect_said did not match across a fold"
[ "$(drive "expect_in 'x' 'it is facing subscribers, and it is not passive' '$WRAPPED'")" = "1 0" ] \
  && ok "and expect_in does not, which is why the two are different" \
  || bad "expect_in matched across a fold, so expect_said has no reason to exist"
[ "$(drive "expect_said 'x' 'a sentence the box never said' '$WRAPPED'")" = "1 0" ] \
  && ok "a sentence the box did not say is still a failure" \
  || bad "expect_said passed on a sentence that is not there"

echo
echo "=== expect_not_said: the direction that stops being able to fail ==="
# expect_not_in on a wrapped message reports the sentence absent whether the
# box said it or not. That is the one that goes green for the wrong reason,
# and it is the direction that guards against the box warning about the wrong
# thing.
[ "$(drive "expect_not_in 'x' 'it is facing subscribers, and it is not passive' '$WRAPPED'")" = "0 0" ] \
  && ok "expect_not_in reports a wrapped sentence absent, which is the defect" \
  || bad "expect_not_in found the wrapped sentence, so the premise is wrong"
[ "$(drive "expect_not_said 'x' 'it is facing subscribers, and it is not passive' '$WRAPPED'")" = "1 0" ] \
  && ok "and expect_not_said catches it" \
  || bad "expect_not_said missed a sentence the box DID say, so a check using "\
"it cannot fail"
[ "$(drive "expect_not_said 'x' 'a sentence the box never said' '$WRAPPED'")" = "0 0" ] \
  && ok "and a box that really is silent passes" \
  || bad "expect_not_said failed on a box that said nothing of the sort"

echo
echo "=== expect_setup: a fixture that could not be built is not a defect ==="
# A precondition the box could not meet says nothing about the product. The
# release suite reported lab/pool-del.sh's as [FAIL] on a loaded machine and
# it passed alone minutes later.
[ "$(drive 'expect_setup "x" test 10000 -ge 9000')" = "0 0" ] \
  && ok "a fixture that was built counts nothing" \
  || bad "a built fixture was counted against the lab"
[ "$(drive 'expect_setup "x" test 12000 -ge 40000')" = "0 1" ] \
  && ok "and one that was not is NOT MEASURED, not a failure" \
  || bad "an unbuildable fixture was reported as a product failure: "\
"$(drive 'expect_setup "x" test 12000 -ge 40000')"
case "$(says 'expect_setup "x" test 1 -ge 2')" in
  *"NOT MEASURED"*) ok "and it says so in the words the suite classifies on" ;;
  *) bad "expect_setup did not print NOT MEASURED, so the suite reads the "\
"lab as passing: $(says 'expect_setup "x" test 1 -ge 2')" ;;
esac

echo
echo "=== and NOT MEASURED is not a pass ==="
# The suite refuses a run with any of them, and the lab still exits non-zero.
# Without this, "not measured" would be a way to make a red check quiet.
# `verdict` calls exit, so the status has to come from the SHELL and not from
# an echo after it - the first version of this case put `echo $?` on the next
# line and never reached it, then reported the product as exiting 0.
sh -c ". $REPO/lab/verdict.sh >/dev/null 2>&1
       expect_setup 'x' test 1 -ge 2 >/dev/null 2>&1
       verdict" >/dev/null 2>&1
rc=$?
[ "$rc" != "0" ] \
  && ok "a lab with one exits non-zero ($rc), so the suite counts it" \
  || bad "a lab whose only check was NOT MEASURED exited 0, which reads as a pass"

echo
echo "=== and the helpers are all there ==="
# Without this the file passes on a verdict.sh whose helpers were renamed:
# every `drive` above would fail to find them and report the shell's own
# non-zero, which is not the same as the helper deciding anything.
for h in expect expect_in expect_not_in expect_said expect_not_said \
         expect_setup expect_timed verdict; do
  sh -c ". $REPO/lab/verdict.sh >/dev/null 2>&1; command -v $h >/dev/null" \
    || bad "lab/verdict.sh no longer defines $h, and every case above that "\
"used it was reporting the shell's error rather than the helper's answer"
done
ok "all eight"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
