#!/bin/sh
# appliance/selftest's helpers, each called on its own.
#
# test/test_selftest.sh drives the self-test whole, against fake roots, and
# reads what the box said. What it cannot say is WHICH helper produced a wrong
# line: cpulist, norm, count and disp are the arithmetic every core check
# stands on, and say_list is the fold that keeps a 32-core failure readable on
# an 80-column console (#285). A fold that cut a cpu number in half would leave
# test_selftest.sh green and an operator reading "1" where the box said "17".
#
# THE FUNCTIONS ARE CUT OUT OF THE SCRIPT AND RUN ALONE, the way
# test/test_run_all_labs.sh cuts _jobs_by_mem out of the lab suite.
# appliance/selftest starts checking the box on line 70, so sourcing it is
# running it, and a guard on the product to make it sourceable is a change to
# the boot path for the benefit of a test. Nothing here reads this machine:
# OUT is a file in a temporary directory, or the subshell's own stdout, and
# every list is a literal.
#
# The helpers here are good/red rather than ok/bad, because ok and bad are two
# of the functions under test and test/test_every_function.py refuses a file
# that defines a name as that name's witness.
set -u
fail=0
good() { echo "  [ ok ] $*"; }
red()  { echo "  FAIL: $*"; fail=$((fail+1)); }

REPO=$(cd "$(dirname "$0")/.." && pwd)
ST=$REPO/appliance/selftest
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

unit_of() {  # <name> -> its definition out of appliance/selftest, nothing else
  # A definition that closes on its own line is taken whole by grep; sed's
  # range would run on to the NEXT function's closing brace and take it too.
  if grep -q "^$1() .*}[[:space:]]*$" "$ST"; then grep -m1 "^$1() " "$ST"
  else sed -n "/^$1() *{/,/^}/p" "$ST"; fi
}
U=$WORK/units.sh
: > "$U"
for n in say ok bad warn say_list cpulist norm count disp; do
  unit_of "$n" >> "$U"
  grep -q "^$n() " "$U" \
    || red "$n() was not found in appliance/selftest, so its cases below run nothing"
done

echo "=== say: the console, and stdout when there is no console ==="
got=$(. "$U"; OUT=$WORK/console; say "hello console"; cat "$WORK/console")
[ "$got" = "hello console" ] \
  && good "say writes the line to \$OUT" \
  || red "say with OUT set to a file gave '$got'"
got=$(. "$U"; OUT=$WORK/no/such/dir/console; say "hello anyway" 2>/dev/null)
[ "$got" = "hello anyway" ] \
  && good "and falls back to stdout when \$OUT cannot be opened, so a box \
with no console still reports" \
  || red "say with an unwritable OUT printed '$got' instead of the line"

echo "=== ok, bad and warn: the row, and what each one counts ==="
got=$(. "$U"; OUT=/dev/stdout; fail=0; warns=0
      ok "the thing"
      echo "fail=$fail warns=$warns")
[ "$got" = "  [ ok ] the thing
fail=0 warns=0" ] \
  && good "ok prints the green row and counts nothing" \
  || red "ok gave:
$got"
got=$(. "$U"; OUT=/dev/stdout; fail=0; warns=0
      bad "the other"
      bad "and again"
      echo "fail=$fail warns=$warns")
[ "$got" = "  [FAIL] the other
  [FAIL] and again
fail=2 warns=0" ] \
  && good "bad prints the red row and counts one failure per call" \
  || red "bad gave:
$got"
got=$(. "$U"; OUT=/dev/stdout; fail=0; warns=0
      warn "the third"
      echo "fail=$fail warns=$warns")
[ "$got" = "  [warn] the third
fail=0 warns=1" ] \
  && good "warn prints the warning row, counts a warning and NOT a failure, \
which is what lets a box with a chosen trade-off still come up ready" \
  || red "warn gave:
$got"

echo "=== say_list folds a list to the console without cutting a number ==="
list=$(seq -s ' ' 0 39)
got=$(. "$U"; OUT=/dev/stdout
      say_list "    " "$list")
rows=$(printf '%s\n' "$got" | wc -l)
widest=$(printf '%s\n' "$got" | awk '{ if (length($0) > w) w = length($0) } END { print w+0 }')
unindented=$(printf '%s\n' "$got" | grep -c -v '^    [^ ]')
trailing=$(printf '%s\n' "$got" | grep -c ' $')
joined=$(printf '%s\n' "$got" | sed 's/^    //' | tr '\n' ' ' | sed 's/ $//')
[ "$rows" -gt 1 ] && [ "$widest" -le 79 ] \
  && good "a 109-column list came out as $rows rows, the widest $widest \
columns, on an 80-column console" \
  || red "the list came out as $rows row(s), the widest $widest columns"
[ "$unindented" -eq 0 ] && [ "$trailing" -eq 0 ] \
  && good "every row carries the indent and none ends in the space it folded on" \
  || red "$unindented row(s) without the indent, $trailing with a trailing space:
$got"
[ "$joined" = "$list" ] \
  && good "and read back together the rows are the list: no number was cut in half" \
  || red "the rows do not read back as the list:
$got"

echo "=== cpulist: the kernel's ranges as a list of cpus ==="
got=$(. "$U"; cpulist "1-3,8")
[ "$got" = " 1 2 3 8" ] \
  && good "1-3,8 is 1 2 3 8" \
  || red "cpulist 1-3,8 gave '$got'"
got=$(. "$U"; cpulist "0,4-4,x,7-a,,12")
[ "$got" = " 0 4 12" ] \
  && good "a one-cpu range is that cpu, and what is not a number is skipped" \
  || red "cpulist 0,4-4,x,7-a,,12 gave '$got'"
got=$(. "$U"; cpulist "")
[ "$got" = "" ] \
  && good "and nothing is nothing, which is what a box with no isolcpus reads" \
  || red "cpulist of nothing gave '$got'"

echo "=== norm, count and disp: one set, three spellings ==="
got=$(. "$U"; norm "8 1 3 1 10 3")
[ "$got" = " 1 3 8 10 " ] \
  && good "norm sorts by number, drops the duplicates and pads both ends, so \
a membership test can look for ' 3 ' and not match 30" \
  || red "norm gave '$got'"
got=$(. "$U"; norm "")
[ "$got" = "  " ] \
  && good "and an empty list is just the padding" \
  || red "norm of nothing gave '$got'"
got=$(. "$U"; count " 1 3 8 10 ")
[ "$got" = "4" ] \
  && good "count of four cpus is 4" \
  || red "count gave '$got'"
got=$(. "$U"; count "")
[ "$got" = "0" ] \
  && good "and of none is 0" \
  || red "count of nothing gave '$got'"
got=$(. "$U"; disp " 8 1 3 ")
[ "$got" = "1 3 8" ] \
  && good "disp is the same set, sorted, with the padding off for printing" \
  || red "disp gave '$got'"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
