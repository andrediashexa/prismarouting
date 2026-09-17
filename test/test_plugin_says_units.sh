#!/bin/sh
# tools/plugin-says.sh's two rows, each called on its own.
#
# test/test_plugin_says.sh drives the tool whole against stub corpora and reads
# its verdict. The verdict is made of these two lines: ok prints a green row,
# bad prints a red one and counts it, and the count is what the tool exits on.
# A bad that printed and did not count would leave the tool exiting 0 over a
# message no box ever printed, which is the defect #366 built it to find.
#
# THE TWO ARE CUT OUT OF THE SCRIPT AND RUN ALONE, as test/test_run_all_labs.sh
# does with _jobs_by_mem: the script refuses to run without a source and a
# corpus on the line before they are defined, so sourcing it is running it.
#
# The helpers here are good/red rather than ok/bad, because ok and bad are the
# functions under test and test/test_every_function.py refuses a file that
# defines a name as that name's witness.
set -u
fail=0
good() { echo "  [ ok ] $*"; }
red()  { echo "  FAIL: $*"; fail=$((fail+1)); }

REPO=$(cd "$(dirname "$0")/.." && pwd)
PS=$REPO/tools/plugin-says.sh
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

U=$WORK/units.sh
grep -E '^(ok|bad)\(\) ' "$PS" > "$U"
[ "$(wc -l < "$U")" -eq 2 ] \
  || red "ok() and bad() were not both found on one line each in tools/plugin-says.sh"

echo "=== ok: a green row, nothing counted ==="
got=$(. "$U"; fail=0
      ok "printed by every box"
      echo "fail=$fail")
[ "$got" = "  [ ok ] printed by every box
fail=0" ] \
  && good "ok prints '  [ ok ] ...' and leaves the count alone" \
  || red "ok gave:
$got"

echo "=== bad: a red row, and one failure counted per call ==="
got=$(. "$U"; fail=0
      bad "printed by no box"
      bad "and this one"
      echo "fail=$fail")
[ "$got" = "  FAIL: printed by no box
  FAIL: and this one
fail=2" ] \
  && good "bad prints '  FAIL: ...' and counts each call, which is what the \
tool's exit status is made of" \
  || red "bad gave:
$got"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
