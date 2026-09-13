#!/bin/sh
# Does `make test` tell a failed assertion from a suite that never reached one?
#
# It did not, and #218 is what that cost: `make test` went red twice under a
# release lab suite, the only record kept was a count of summary lines, and
# nobody could say which check it was. Ten summary lines instead of nineteen
# means a run stopped part way; it does not say where it stopped, and it reads
# the same whether a check went red or the process was killed between two of
# them.
#
# Driven with STUB suites, not real ones. The state this is about is a suite
# that dies mid-run, and arranging a real one would mean killing a real test
# from outside - so the real tools/run-tests.sh is run here, unmodified,
# against a list of suites written to end in each way it must tell apart.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }
REPO=$(cd "$(dirname "$0")/.." && pwd)

d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT
mkdir -p "$d/tools" "$d/stub"
cp "$REPO/tools/run-tests.sh" "$d/tools/"

stub() { cat > "$d/stub/$1.sh"; chmod +x "$d/stub/$1.sh"; }

stub aa-good <<'EOF'
#!/bin/sh
echo "  [ ok ] something was measured"
echo
echo "ALL TESTS PASSED"
EOF

stub bb-red <<'EOF'
#!/bin/sh
echo "  [ ok ] something was measured"
echo "  FAIL: and something else was not"
echo
echo "2 FAILURES"
exit 1
EOF

stub cc-untimed <<'EOF'
#!/bin/sh
echo "  NOT MEASURED: the box could not keep a one-second sleep"
echo
echo "1 NOT MEASURED"
exit 1
EOF

# The case the old Makefile could not see. Six lines in, the process is gone:
# no verdict, and an exit status a failed assertion also produces.
stub dd-killed <<'EOF'
#!/bin/sh
echo "=== the first group ==="
echo "  [ ok ] one"
echo "  [ ok ] two"
echo "=== the group it died in ==="
echo "  [ ok ] three"
kill -9 $$
EOF

# A suite that could not start at all. Nothing ran, so nothing failed.
stub ee-missing <<'EOF'
#!/bin/sh
exec /nonexistent/interpreter
EOF

# Says it passed and then exits non-zero. The exit status is the half nobody
# reads, and "ALL TESTS PASSED" is the half everybody does.
stub ff-liar <<'EOF'
#!/bin/sh
echo
echo "ALL TESTS PASSED"
exit 3
EOF

list="$d/all.list"
for n in aa-good bb-red cc-untimed dd-killed ee-missing ff-liar; do
  echo "sh stub/$n.sh"
done > "$list"

out=$(sh "$d/tools/run-tests.sh" "$list" 2>&1); rc=$?

# The report line for one suite, and nothing else. Matching against the whole
# capture is how the first draft of this file said a killed suite had been
# called a failure: the streamed output of the OTHER stubs is in there too,
# and "dd-killed ... FAIL" matched across four lines of somebody else's.
verdict_for() { printf '%s\n' "$out" | grep -E "^  $1[[:space:]]"; }

echo "=== a suite that never reached a verdict is not a failed assertion ==="
case "$(verdict_for dd-killed)" in
  *"NO VERDICT"*) ok "a killed suite is reported NO VERDICT" ;;
  *) bad "a killed suite was not separated out; got: $(verdict_for dd-killed)" ;;
esac
case "$(verdict_for dd-killed)" in
  *FAIL*) bad "a killed suite was reported as a failed assertion" ;;
  *) ok "and not as a failed assertion" ;;
esac
case "$(verdict_for ee-missing)" in
  *"NO VERDICT"*) ok "and so is one that could not start" ;;
  *) bad "a suite that never started was not reported NO VERDICT; got: $(verdict_for ee-missing)" ;;
esac

echo "=== and it says how far it got, which is the question it leaves ==="
case "$(verdict_for dd-killed)" in
  *"NO VERDICT - exit 137 after 6 lines, last: [ ok ] three|Killed"*)
    ok "the status it died on, the line count, and the check it died after" ;;
  *) bad "a stopped suite did not say where it stopped; got: $(verdict_for dd-killed)" ;;
esac
case "$out" in
  *"[4/6] dd-killed"*) ok "and which suite of how many it was" ;;
  *) bad "the suites are not numbered, so 'how far did it get' has no answer" ;;
esac

echo "=== the other two states keep their own names ==="
case "$(verdict_for bb-red)" in
  *"FAIL - 2 FAILURES"*) ok "a red suite carries its own count" ;;
  *) bad "a red suite was not reported with its verdict line; got: $(verdict_for bb-red)" ;;
esac
case "$(verdict_for cc-untimed)" in
  *"NOT MEASURED - 1 NOT MEASURED"*)
    ok "a suite the machine could not time says so" ;;
  *) bad "NOT MEASURED was not carried through; got: $(verdict_for cc-untimed)" ;;
esac
case "$(verdict_for ff-liar)" in
  *"said it passed and exited 3"*)
    ok "a suite that printed a pass and exited non-zero is not a pass" ;;
  *) bad "the exit status was ignored once the summary line looked right; got: $(verdict_for ff-liar)" ;;
esac

echo "=== the summary counts the four apart, and the run is not green ==="
case "$out" in
  *"6 suites: 1 passed, 2 failed, 1 not measured, 2 with no verdict"*)
    ok "the four states are counted apart" ;;
  *) bad "the summary is not what was counted; got:
$(printf '%s' "$out" | grep 'suites:')" ;;
esac
[ "$rc" -ne 0 ] && ok "a run with a suite that stopped does not exit 0" \
                || bad "the runner exited 0 over a suite that never finished"

echo "=== each state on its own is enough, and an all-green run is green ==="
# NO VERDICT alone especially: a suite that ran nothing has cleared nothing,
# and that is the reading `make test` used to be unable to give.
echo "sh stub/dd-killed.sh" > "$d/one.list"
out2=$(sh "$d/tools/run-tests.sh" "$d/one.list" 2>&1); rc2=$?
case "$out2" in
  *"1 suites: 0 passed, 0 failed, 0 not measured, 1 with no verdict"*)
    ok "NO VERDICT alone is counted alone" ;;
  *) bad "NO VERDICT alone was miscounted; got:
$(printf '%s' "$out2" | grep 'suites:')" ;;
esac
[ "$rc2" -ne 0 ] && ok "and is enough on its own to fail the run" \
                 || bad "a run whose only suite never finished exited 0"

echo "sh stub/cc-untimed.sh" > "$d/one.list"
out3=$(sh "$d/tools/run-tests.sh" "$d/one.list" 2>&1); rc3=$?
[ "$rc3" -ne 0 ] && ok "NOT MEASURED alone is enough too: it is not a pass" \
                 || bad "a run that measured nothing exited 0"

echo "sh stub/aa-good.sh" > "$d/one.list"
out4=$(sh "$d/tools/run-tests.sh" "$d/one.list" 2>&1); rc4=$?
case "$out4" in
  *"1 suites: 1 passed, 0 failed, 0 not measured, 0 with no verdict"*)
    [ "$rc4" -eq 0 ] && ok "and a suite that really passed still passes" \
                     || bad "a green suite exited $rc4" ;;
  *) bad "a green run was not counted as one; got:
$(printf '%s' "$out4" | grep 'suites:')" ;;
esac
case "$out4" in
  *NO\ VERDICT*|*NOT\ MEASURED*)
    bad "a green run printed the explanations that belong to the other states" ;;
  *) ok "and says nothing about states it did not reach" ;;
esac

echo
echo "=== every check in test/ is a check that runs ==="
# Two lists name what runs, and nothing compared them with the directory.
# test/suites.list holds the shell and Python suites; the Makefile's `test:`
# target names the C ones, one by one, on three lines.
#
# Write test/test_bpa_tcp.c, forget the Makefile line, and it is never compiled
# and never run - and `make test` prints the same "N suites, N passed" as
# before, because N is what the lists produce and not what the directory holds.
# The author believes the checks they wrote are guarding the plugin and nothing
# is. That is #233 one directory over, and worse: there the thing not running
# was a lab, here it is a check, and every other guard in this repository rests
# on a file in test/ being a file that runs (#235).
#
# Only that each file is named somewhere. Not the contents, not the order.
SL=$REPO/test/suites.list
MK=$REPO/Makefile
mk_target=$(sed -n '/^test:/,/[^\\]$/p' "$MK")
n_sl=$(grep -c "test/test_" "$SL" || true)
# grep -o with -c counts LINES that match, not matches, and the target is three
# lines carrying eight names. The first version of this reported 3.
n_mk=$(printf '%s' "$mk_target" | grep -o '$(BUILD)/test_[a-z_0-9]*' | wc -l)
if [ "${n_sl:-0}" -ge 10 ] && [ "${n_mk:-0}" -ge 5 ]; then
  ok "$n_sl suite(s) listed and $n_mk C binary(s) named by the Makefile target"
else
  bad "one of the two lists came back almost empty: suites.list has ${n_sl:-0} "\
"and the Makefile test: target names ${n_mk:-0} binaries. This is asserting "\
"almost nothing"
fi

# For a C file, the thing that makes it run is its BINARY appearing in the
# `test:` target. Matching the file name anywhere in the Makefile is not that:
# a binary dropped from the target keeps its build rule, so the file is still
# named, still compiles when asked, and never runs. The first version of this
# check passed on exactly that mutation and reported only a confusing count.
binary_for() {  # <test/foo.c> -> the $(BUILD)/... target whose recipe uses it
  awk -v f="$1" '
    /^\$\(BUILD\)\/test_[a-z_0-9]*:/ { tgt=$1; sub(":", "", tgt) }
    tgt != "" && index($0, f) { print tgt; exit }
  ' "$MK"
}

unrun=""
for f in "$REPO"/test/test_*; do
  b=$(basename "$f")
  grep -q "test/$b" "$SL" && continue
  case "$b" in
    *.c)
      tgt=$(binary_for "test/$b")
      if [ -n "$tgt" ]; then
        case "$mk_target" in *"$tgt"*) continue ;; esac
      fi ;;
    *)
      # A header or fixture under test/ is not a suite. Only files that name
      # themselves test_* and are not C sources reach here, and all of those
      # belong in suites.list.
      : ;;
  esac
  unrun="$unrun $b"
done
[ -z "$unrun" ] && ok "and every file in test/ is named by one of them" \
  || bad "these are in test/ and nothing runs them, so the checks in them "\
"guard nothing while make test still says every suite passed:$unrun"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
