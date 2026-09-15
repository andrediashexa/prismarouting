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

echo
echo "=== and the report says which harnesses it did NOT read ==="
# The check above is a gate over test/ only, and appliance/ is where the two
# harnesses were. appliance/test_startup_conf.sh and appliance/test_tuned_conf.sh
# run only from `make test-appliance`, so everything they assert sat outside
# "59 suites: 59 passed" and the reader of that line had no way to know they
# existed (#338). They cannot join the list - both need a real VPP - so the
# runner has to say so instead.
#
# ARRANGED, not read off the real tree. What is asserted is the rule, and a
# rule is only shown to work on a tree where this file decided the answer: one
# harness the list names, one it does not, one compiled suite the list names
# only by its binary, and the binary itself.
mkdir -p "$d/test" "$d/.build"
cat > "$d/test/test_in_the_list.sh" <<'EOF'
#!/bin/sh
echo; echo "ALL TESTS PASSED"
EOF
: > "$d/test/test_nobody_runs.sh"
: > "$d/test/test_compiled.c"
cat > "$d/.build/test_widget" <<'EOF'
#!/bin/sh
echo; echo "ALL TESTS PASSED"
EOF
chmod +x "$d/.build/test_widget"
# A build output the list does NOT name, which is what a renamed or deleted
# suite leaves behind in .build. Without it the prune cannot be shown to do
# anything: .build/test_widget is in the list, so a report that mistook it for
# a harness would find it in the read set and stay quiet about it. This one is
# the stale binary that would be reported as a harness nobody runs, sending
# somebody to look for a suite that no longer exists.
: > "$d/.build/test_stale"
# The indirection the report has to follow: the list names a binary, and the
# source it is built from carries none of that name.
cat > "$d/Makefile" <<'EOF'
$(BUILD)/test_widget: test/test_compiled.c src/widget.c \
                      test/widget_shim.h
	$(CC) -o $@ test/test_compiled.c
EOF
{ echo "sh test/test_in_the_list.sh"; echo ".build/test_widget"; } > "$d/harness.list"
outh=$(sh "$d/tools/run-tests.sh" "$d/harness.list" 2>&1)

# What the REPORT named, and not what the capture contains anywhere - for the
# same reason verdict_for() exists sixty lines up. The runner streams each
# suite's output and prints "=== [2/2] test_widget ===" of its own, so the
# first draft of the build-output check below matched the runner announcing
# the suite and called the report wrong while it was right.
named_by() {  # <capture> -> the paths its report said were not read
  printf '%s\n' "$1" | sed -n '/harness file(s) in the tree/,/^$/p' \
    | sed -n 's/^    //p'
}
names_h=$(named_by "$outh")
has() { printf '%s\n' "$2" | grep -qxF -- "$1"; }

if has "test/test_nobody_runs.sh" "$names_h"; then
  ok "a harness the list does not name is named by the report"
else
  bad "a harness nothing ran went unnamed, which is the whole defect; the \
report named: ${names_h:-<nothing at all>}"
fi
if has "test/test_in_the_list.sh" "$names_h"; then
  bad "it reported a suite the list DID run as unread. A report that accuses \
a green suite is one people learn to skip"
else
  ok "and a harness the list does name is not accused of being unread"
fi
# The case that is not a name match. .build/test_bpa is what test_bpa_pool.c
# becomes; guessing that the two names agree would put a compiled suite in
# this report on every green run.
if has "test/test_compiled.c" "$names_h"; then
  bad "a compiled suite the list runs was reported unread, so the report did \
not ask the Makefile which source builds the binary the list names"
else
  ok "and neither is the source of a binary the list names"
fi
if has ".build/test_stale" "$names_h"; then
  bad "a leftover binary in .build was counted as a harness of its own, so \
the report sends somebody looking for a suite that does not exist"
else
  ok "and a build output is not a harness"
fi
case "$outh" in
  *"3 harness file(s) in the tree, and this run read 2."*)
    ok "and the two numbers are the two numbers" ;;
  *) bad "the count is not what the tree holds; got: $(printf '%s\n' "$outh" \
       | grep 'harness file' || echo '<no count line at all>')" ;;
esac

# Nothing unread is a thing to SAY, not a thing to leave out. A report that
# goes silent cannot be told apart from a report that stopped working, and
# this one had no way of going red on its own.
rm -f "$d/test/test_nobody_runs.sh"
{ echo "sh test/test_in_the_list.sh"; echo ".build/test_widget"; } > "$d/harness.list"
outg=$(sh "$d/tools/run-tests.sh" "$d/harness.list" 2>&1)
case "$outg" in
  *"2 harness file(s) in the tree, and this run read every one."*)
    ok "a run that read every harness in its tree says that too" ;;
  *) bad "a complete run said nothing about being complete; got: \
$(printf '%s\n' "$outg" | grep 'harness file' || echo '<no count line at all>')" ;;
esac

echo
echo "=== and over this repository, what it names is really not run ==="
# The half above is arranged. This one is about the real tree, and it is the
# half that cannot go stale: whatever the report names has to be a file that
# exists and that test/suites.list really does not name.
#
# Deliberately NOT "these two files must be named". That would be a hand-kept
# roster of the excused, which is the thing the report exists to replace - move
# a harness into suites.list and this still passes, as it should. The price of
# that is a check that can pass by having nothing to look at, and the floor
# below is what stops "it named nothing" from being the whole answer.
#
# Driven over the REAL test/suites.list, with every command turned into `true`
# followed by the path it would have run. The report is computed from the last
# word of each line, so the read set is exactly the one `make test` produces,
# while nothing is executed - running 59 real suites a second time to read six
# lines of report is not a price worth paying. A list of one `true` would not
# do: then every harness in the repository is honestly unread, and the check
# below would be asserting that suites.list names none of them.
sed 's/#.*//' "$REPO/test/suites.list" | sed '/^[[:space:]]*$/d' \
  | sed 's/^/true /' > "$d/asif.list"
real=$(sh "$REPO/tools/run-tests.sh" "$d/asif.list" 2>&1 || true)
named=$(named_by "$real")
bogus=""
for f in $named; do
  [ -f "$REPO/$f" ] || bogus="$bogus
    $f  (no such file)"
  grep -qF -- "$f" "$REPO/test/suites.list" \
    && bogus="$bogus
    $f  (test/suites.list names it, so make test does run it)"
done
[ -z "$bogus" ] \
  && ok "the report named $(printf '%s\n' "$named" | grep -c . || true) \
harness(es) here, and each is a real file suites.list does not run" \
  || bad "the report is wrong about a file anyone can check, which is a false \
alarm and a check people run around:$bogus"

# The discovery itself, held to a floor. It is a find(1) with a prune in it,
# and the first draft of that prune matched the starting directory and returned
# nothing at all - a report that then says "0 harness files in the tree, and
# this run read every one" over a repository holding sixty-one of them. The
# list's own entries are the independent count: every file suites.list names
# has to have been found.
n_stated=$(printf '%s\n' "$real" | sed -n 's/^  \([0-9][0-9]*\) harness file.*/\1/p')
n_listed=$(grep -oE '[a-z]+/test_[a-z_0-9]+\.(sh|py)' "$REPO/test/suites.list" \
             | sort -u | wc -l)
if [ "${n_stated:-0}" -ge "${n_listed:-0}" ] && [ "${n_listed:-0}" -ge 10 ]; then
  ok "$n_stated harness file(s) found, and suites.list's own $n_listed are among them"
else
  bad "the discovery found ${n_stated:-<nothing>} harness file(s) while \
suites.list alone names $n_listed. It is not looking where the harnesses are, \
and a report that finds none reads exactly like a repository with none"
fi

echo
echo "=== and no suite reaches the network ==="
# TESTING.md says `make test` "Requires no root, no VPP and no network", and
# for one night it was false: two suites drove the real release gate, which
# runs `gh auth status`, queries the tracker and looks up the published
# release. Two live calls each, on a suite that runs at every commit - flaky
# by construction, and simply broken on a machine with no route out.
#
# A RECORDING gh ON THE PATH, not a read of the source. A suite that shells
# out through a helper, or a tool that grows a call tomorrow, is invisible to
# grep and caught here. curl is recorded too: tools/wiki-push.sh and
# wiki-pull.sh are the other way out.
_nb=$(mktemp -d)
for _c in gh curl; do
  printf '#!/bin/sh\necho "%s $*" >> "%s/used"\nexit 1\n' "$_c" "$_nb" \
    > "$_nb/$_c"
  chmod +x "$_nb/$_c"
done
: > "$_nb/used"
for _s in test/test_wiki_roundtrip.sh test/test_release_gate.sh; do
  [ -f "$REPO/$_s" ] || continue
  ( cd "$REPO" && PATH="$_nb:$PATH" sh "$_s" ) >/dev/null 2>&1 || true
done
_used=$(wc -l < "$_nb/used" 2>/dev/null | tr -d ' ')
[ -n "$_used" ] || _used=0
if [ "$_used" -eq 0 ]; then
  ok "the two suites that drive the release gate made no gh or curl call"
else
  bad "a suite reached the network $_used time(s), and TESTING.md says this \
needs none: $(sort -u "$_nb/used" | tr '\n' ' ')"
fi
rm -rf "$_nb"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
