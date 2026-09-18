#!/bin/sh
# The refusals of the tools in tools/ that nothing else drives.
#
# A refusal nobody has watched fire is a refusal that may not fire, and this
# file is the residue of a sweep that measured that tool by tool. Five of
# them ended it with every refusal still unrendered by anything:
#
#   tools/roadmap-state.sh   14 refusals, 0 driven. Only tools/release-gate.sh
#                            runs it at all, and it runs it where `gh` usually
#                            cannot answer - so the only branch of it that has
#                            ever been executed under a test is the one that
#                            says the comparison did not happen.
#   tools/vty.sh              1 refusal, 0 driven. Nothing in test/ names the
#                            file.
#   tools/lab.sh              1 refusal reachable without docker, 0 driven.
#   tools/build.sh            1 refusal, 0 driven.
#   tools/plugin-coverage.sh  2 refusals, 0 driven, and it is in no suite
#                            list: it builds the C harnesses twice. Both of
#                            its refusals are about an EMPTY measurement, and
#                            both are reachable with a Makefile of two lines.
#
# NOTHING HERE REACHES A NETWORK, A TRACKER OR A CONTAINER. `gh` and `docker`
# are stubs on PATH answering from fixtures, which is the same arrangement
# test/test_wiki_roundtrip.sh makes for `curl`. That is not a compromise: what
# is being asserted is what these scripts do with an answer, and the answers
# that matter are the ones a live service gives once a year.
#
# PATH IS PREPENDED, NOT REPLACED, except in the one case that needs `gh` to
# be absent - and that case asserts the script's own output, so a PATH cut so
# far that nothing started cannot read as a pass.
set -u
fail=0
ok()  { echo "  $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }

REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# ---------------------------------------------------------------------------
# tools/roadmap-state.sh
#
# It compares docs/roadmap.md against the issue tracker, and its own header
# states the rule every refusal in it keeps: exit 2 means the comparison did
# not happen, exit 1 means findings, and nothing that could not reach the
# tracker may exit 0. A roadmap nobody compared reads exactly like a roadmap
# that agreed.
# ---------------------------------------------------------------------------
RS="$WORK/rs"
mkdir -p "$RS/tools" "$RS/docs" "$RS/bin" "$RS/gh"
cp "$REPO/tools/roadmap-state.sh" "$RS/tools/"

# The tracker, as far as this script can tell. `gh issue list --json ... --jq`
# prints "<number> <STATE>" a line at a time, which is the only shape the
# script reads.
cat > "$RS/bin/gh" <<'STUB'
#!/bin/sh
case "$1 ${2:-}" in
  "auth status")
    [ -f "$GH_STUB/unauth" ] && exit 1
    echo "github.com: logged in"; exit 0 ;;
  "issue list")
    if [ -f "$GH_STUB/unreachable" ]; then
      echo "could not resolve host: api.github.com" >&2
      exit 1
    fi
    cat "$GH_STUB/issues.txt"; exit 0 ;;
esac
echo "the stub was asked for: $*" >&2
exit 1
STUB
chmod +x "$RS/bin/gh"
export GH_STUB="$RS/gh"

# A roadmap that AGREES, and every case below is one edit away from it. Built
# as a function rather than copied about, because a fixture carried from one
# case to the next is how a run stops being about what it says it is.
write_roadmap() {  # <path>
  cat > "$1" <<'DOC'
# Roadmap

## What landed

The pool rework landed under
[#10](https://github.com/andrediashexa/hexarouting/issues/10).

| Item | State |
|---|---|
| [#10](https://github.com/andrediashexa/hexarouting/issues/10) pool rework | **delivered** |

## What is blocked on somebody else, and on what

Kept short and kept honest.

| | |
|---|---|
| [#11](https://github.com/andrediashexa/hexarouting/issues/11) the rename | the maintainer's call |
DOC
}
write_roadmap "$RS/docs/roadmap.md"
printf '10 CLOSED\n11 OPEN\n' > "$RS/gh/issues.txt"

run_rs() {  # [args...] -> $OUT and $RC
  OUT=$(PATH="$RS/bin:$PATH" sh "$RS/tools/roadmap-state.sh" "$@" 2>&1) \
    && RC=0 || RC=$?
}

echo "=== a roadmap that agrees with the tracker is the control ==="
# FIRST, and it is not a formality. Every case below asserts that a refusal
# fires; without one run in which none of them does, a script that refused
# unconditionally would pass the lot.
run_rs
[ "$RC" = 0 ] \
  && ok "exit 0: $(printf '%s\n' "$OUT" | tail -1)" \
  || { bad "the control roadmap was not clean (rc=$RC):"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }

echo
echo "=== a file that is not there is not a roadmap that agreed ==="
run_rs "$RS/docs/no-such-roadmap.md"
[ "$RC" = 2 ] \
  && ok "exit 2, which is 'nothing was compared'" \
  || bad "a missing file exited $RC"
printf '%s\n' "$OUT" | grep -q "no such file" \
  && ok "and it names the file: $(printf '%s\n' "$OUT" | head -1)" \
  || { bad "it refused without naming the file:"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }

echo
echo "=== no gh at all: NOTHING WAS COMPARED, and not a clean run ==="
# The only case that replaces PATH. The script needs mktemp, awk, sed, wc and
# tr, so those are linked in beside the absence - and the assertion is on its
# own sentence, so a PATH so short that nothing ran cannot read as a pass.
mkdir -p "$RS/nogh"
# `sh` and `dirname` are in this list because the first run of this case left
# them out: PATH held no shell at all, the case went red with "sh: not found"
# and nothing about gh had been measured. That is the failure mode a stub set
# has, and the reason the assertion below is on the script's own sentence.
for b in sh dirname mktemp awk sed wc tr rm cat grep; do
  p=$(command -v "$b") && ln -sf "$p" "$RS/nogh/$b"
done
OUT=$(PATH="$RS/nogh" sh "$RS/tools/roadmap-state.sh" \
        "$RS/docs/roadmap.md" 2>&1) && RC=0 || RC=$?
[ "$RC" = 2 ] \
  && ok "exit 2 with no gh on PATH" \
  || bad "a missing gh exited $RC"
printf '%s\n' "$OUT" | grep -q "gh is not installed, so NOTHING WAS COMPARED" \
  && ok "and it says so in those words" \
  || { bad "it failed without saying nothing was compared:"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }
printf '%s\n' "$OUT" | grep -q "there is no offline answer it" \
  && ok "and that there is no offline answer to give instead" \
  || bad "it did not say why there is no fallback"

echo
echo "=== gh present and not logged in is the same refusal, said differently ==="
# A different thing to go and do - `gh auth login` rather than install it - so
# it is a refusal of its own and not one sentence covering both.
: > "$RS/gh/unauth"
run_rs
[ "$RC" = 2 ] \
  && ok "exit 2 for an unauthenticated gh" \
  || bad "an unauthenticated gh exited $RC"
printf '%s\n' "$OUT" | grep -q "gh is not authenticated, so NOTHING WAS COMPARED" \
  && ok "and it names the state, not just the failure" \
  || { bad "it refused without saying gh was unauthenticated:"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }
printf '%s\n' "$OUT" | grep -q "gh auth login" \
  && ok "and it says the command that fixes it" \
  || bad "it refused without saying how to authenticate"
rm -f "$RS/gh/unauth"

echo
echo "=== a tracker that could not be reached keeps gh's own words ==="
# "could not resolve host" and "HTTP 403 rate limit" need different work and
# both look like an empty answer from here, so gh's stderr is passed through
# rather than replaced with one sentence of this script's own.
: > "$RS/gh/unreachable"
run_rs
[ "$RC" = 2 ] \
  && ok "exit 2 when the query failed" \
  || bad "an unreachable tracker exited $RC"
printf '%s\n' "$OUT" | grep -q "the tracker could not be reached, so NOTHING WAS COMPARED" \
  && ok "and it says nothing was compared" \
  || { bad "it refused without saying the comparison did not happen:"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }
printf '%s\n' "$OUT" | grep -q "gh: could not resolve host" \
  && ok "and it repeats what gh said: $(printf '%s\n' "$OUT" | grep 'gh: ')" \
  || { bad "gh's own reason was thrown away, so a DNS failure and a rate limit"\
" read the same:"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }
rm -f "$RS/gh/unreachable"

echo
echo "=== an empty answer is a query that went wrong, not an empty tracker ==="
# gh reporting success over nothing. Read as an empty tracker it would call
# every reference in the file unknown and leave every check below it silent,
# which is a clean-looking run that measured nothing.
: > "$RS/gh/issues.txt"
run_rs
[ "$RC" = 2 ] \
  && ok "exit 2 over an answer with no issues in it" \
  || bad "an empty tracker answer exited $RC"
printf '%s\n' "$OUT" | grep -q "answered with no issues at all" \
  && ok "and it says that cannot be right for this repository" \
  || { bad "an empty answer was read as an empty tracker:"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }
printf '10 CLOSED\n11 OPEN\n' > "$RS/gh/issues.txt"

echo
echo "=== a check that has quietly stopped checking says so ==="
# The queue rule reads the rows of one named section. Rename or delete that
# section and the rule is still there, still green, and reading nothing - a
# worse state than the defect it looks for, because nothing says it stopped.
sed 's/^## What is blocked on somebody else, and on what$/## Waiting/' \
  "$RS/docs/roadmap.md" > "$RS/docs/renamed.md"
run_rs "$RS/docs/renamed.md"
[ "$RC" = 2 ] \
  && ok "exit 2, so a run over a file it cannot check is not a clean run" \
  || bad "a roadmap with no blocked section exited $RC"
printf '%s\n' "$OUT" | grep -q "one of the checks below is reading" \
  && ok "and it says which of its own checks went blind" \
  || { bad "the section was renamed and nothing noticed:"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }

echo
echo "=== a row that says **open** about an issue that is not ==="
# The cheapest true reading in the file: the row has a state column and the
# state column is wrong. An operator reads this file to decide what to do
# next, and a closed issue presented as open is work somebody starts twice.
write_roadmap "$RS/docs/state.md"
sed -i 's/pool rework | \*\*delivered\*\*/pool rework | **open**/' \
  "$RS/docs/state.md"
run_rs "$RS/docs/state.md"
[ "$RC" = 1 ] \
  && ok "exit 1, which is findings rather than 'not compared'" \
  || bad "a row saying **open** about a closed issue exited $RC"
printf '%s\n' "$OUT" | grep -q "STATE.*the row says \*\*open\*\*" \
  && ok "$(printf '%s\n' "$OUT" | grep 'STATE' | sed 's/^ *//')" \
  || { bad "the wrong state column was not reported:"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }

echo
echo "=== a closed issue queued in the blocked table ==="
# The header of that table says the row is work that has NOT happened, so a
# closed issue in it is the table claiming something the tracker contradicts.
write_roadmap "$RS/docs/queue.md"
sed -i 's|\[#11\](\(.*\)/issues/11)|[#10](\1/issues/10)|' "$RS/docs/queue.md"
# And #11 leaves the tracker with it. Left in, it would be an OPEN issue the
# file no longer mentions, so this case would exit 1 for two reasons and the
# queue rule could have stopped working without the exit status moving.
printf '10 CLOSED\n' > "$RS/gh/issues.txt"
run_rs "$RS/docs/queue.md"
[ "$RC" = 1 ] \
  && ok "exit 1 over a closed issue in the queue" \
  || bad "a closed issue queued as pending exited $RC"
printf '%s\n' "$OUT" | grep -q "QUEUE.*queued as pending" \
  && ok "$(printf '%s\n' "$OUT" | grep 'QUEUE' | sed 's/^ *//' | cut -c1-96)" \
  || { bad "a closed issue sitting in the blocked table was not reported:"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }
printf '10 CLOSED\n11 OPEN\n' > "$RS/gh/issues.txt"

echo
echo "=== a sentence that says work remains, about an issue that is closed ==="
# The hard one, and the one the tool's own header spends fifty lines on: the
# needle is matched against a CLAUSE and attributed to the nearest reference
# in it, because a paragraph is not a sentence and this file is hard-wrapped.
write_roadmap "$RS/docs/prose.md"
printf '\n#10 is still open and nobody has picked it up.\n' \
  >> "$RS/docs/prose.md"
run_rs "$RS/docs/prose.md"
[ "$RC" = 1 ] \
  && ok "exit 1 over a sentence about work that is done" \
  || bad "prose claiming a closed issue is open exited $RC"
printf '%s\n' "$OUT" | grep -q 'PROSE.*still open' \
  && ok "$(printf '%s\n' "$OUT" | grep 'PROSE' | sed 's/^ *//' | cut -c1-96)" \
  || { bad "the sentence was not attributed to the issue it names:"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }

echo
echo "=== the other direction: an open issue the file never mentions ==="
# The half that asking about the numbers already in the file cannot find, and
# the half that cost the most: measured on 2026-09-14 the roadmap never
# mentioned seven open issues, one of them a published wiki page that is wrong
# and one boxes sharing an SSH host key.
printf '10 CLOSED\n11 OPEN\n14 OPEN\n' > "$RS/gh/issues.txt"
run_rs
[ "$RC" = 1 ] \
  && ok "exit 1 over an open issue with no home in the file" \
  || bad "an unmentioned open issue exited $RC"
printf '%s\n' "$OUT" | grep -q "open issues the roadmap never mentions" \
  && printf '%s\n' "$OUT" | grep -qE '^ +#14$' \
  && ok "and it names #14" \
  || { bad "the open issue the file never mentions was not reported:"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }

echo
echo "=== a number that is not an issue here is a finding of its own ==="
# Usually a pull request number or a typo, and a reader who follows it lands
# on something that is not the work the sentence claims. Quiet, so it is
# counted rather than printed twice.
printf '10 CLOSED\n11 OPEN\n' > "$RS/gh/issues.txt"
write_roadmap "$RS/docs/dangling.md"
printf '\nSee #4242 for the measurement.\n' >> "$RS/docs/dangling.md"
run_rs "$RS/docs/dangling.md"
[ "$RC" = 1 ] \
  && ok "exit 1 over a reference the tracker has not got" \
  || bad "a dangling reference exited $RC"
printf '%s\n' "$OUT" | grep -q "NOT IN THE TRACKER" \
  && ok "$(printf '%s\n' "$OUT" | grep -m1 'NOT IN THE TRACKER' | sed 's/^ *//')" \
  || { bad "a number that is not an issue here was passed over:"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }
# The per-reference listing above says it and the SUMMARY counts it, and the
# count is the half that makes it a finding rather than a line of a table.
printf '%s\n' "$OUT" | grep -q "1 NOT IN THE TRACKER" \
  && ok "and the summary counts it: $(printf '%s\n' "$OUT" | grep -m1 'named,' | sed 's/^ *//')" \
  || bad "it listed the dangling reference without counting it as a finding"

echo
echo "=== a scratch file it cannot make is exit 2, not a comparison ==="
# The two quietest refusals in the file: `STATES=$(mktemp) || exit 2` and the
# same for ERRS. Both are the tracker's answer and gh's stderr with nowhere to
# land, and on a full /tmp neither would be written - so an exit 0 here would
# be a roadmap reported as agreeing with a tracker whose answer went nowhere.
#
# THEY SAY NOTHING AT ALL. Every other refusal in this script prints a
# sentence with "NOTHING WAS COMPARED" in it; these two print an exit status.
# That is asserted as it stands rather than as it ought to be, and named in
# the report, because a test written against the behaviour somebody wishes for
# is a test that is red on arrival.
MT="$RS/mktemp"; mkdir -p "$MT/bin"
MKTEMP_REAL=$(command -v mktemp)
export MKTEMP_REAL MKTEMP_STUB="$MT"
cat > "$MT/bin/mktemp" <<'STUB'
#!/bin/sh
# The n-th call onwards fails. A counter in a file because each call is a
# fresh process, and the case that uses it zeroes the file first: a counter
# carried from one case into the next is how a fixture stops being the box
# somebody meant.
n=$(cat "$MKTEMP_STUB/count" 2>/dev/null || echo 0)
n=$((n + 1))
echo "$n" > "$MKTEMP_STUB/count"
[ "$n" -ge "$MKTEMP_FAIL_AT" ] && exit 1
exec "$MKTEMP_REAL" "$@"
STUB
chmod +x "$MT/bin/mktemp"

for at in 1 2; do
  echo 0 > "$MT/count"
  OUT=$(MKTEMP_FAIL_AT=$at PATH="$MT/bin:$RS/bin:$PATH" \
        sh "$RS/tools/roadmap-state.sh" "$RS/docs/roadmap.md" 2>&1) \
    && RC=0 || RC=$?
  [ "$RC" = 2 ] \
    && ok "the $at$([ "$at" = 1 ] && echo st || echo nd) mktemp failing exits 2" \
    || { bad "mktemp call $at failing exited $RC, so a tracker answer with"\
" nowhere to land read as a comparison:"
         printf '%s\n' "$OUT" | sed 's/^/    /'; }
  # And it got that far. Without this the case would pass over a PATH so cut
  # that the script never started, which is a different exit 2.
  [ "$(cat "$MT/count")" = "$at" ] \
    && ok "and it stopped ON that call, not before it" \
    || bad "the script made $(cat "$MT/count") mktemp call(s), not $at"
done

# ---------------------------------------------------------------------------
# tools/vty.sh, tools/lab.sh, tools/build.sh
#
# Each has one refusal that fires before anything is asked of a container, and
# none of the three was driven by anything.
# ---------------------------------------------------------------------------
echo
echo "=== the VTY names the commands that MAKE the container it wants ==="
# Naming the wrong command is worse than naming none: the script's own comment
# records that "sh tools/lab.sh hw-lab" was suggested here once, which
# configures addresses on the host and creates no container, so following the
# hint produced the same error again.
#
# The docker stub answers `docker ps` so the sg re-exec at the top is not
# taken, and lists a container that is not the one asked for.
DS="$WORK/docker"; mkdir -p "$DS/bin"
cat > "$DS/bin/docker" <<'STUB'
#!/bin/sh
# Every verb these three scripts use, and each one RECORDS rather than
# reports: what a case asserts is whether a container was asked for at all.
case "$1" in
  ps)
    case "${2:-}" in
      --format) cat "$DOCKER_STUB/names.txt" ;;
      *)        exit 0 ;;
    esac ;;
  exec)  shift; printf '%s\n' "$*" > "$DOCKER_STUB/exec.txt" ;;
  run)   shift; printf '%s\n' "$*" > "$DOCKER_STUB/run.txt" ;;
  build) shift; printf '%s\n' "$*" > "$DOCKER_STUB/build.txt" ;;
esac
exit 0
STUB
chmod +x "$DS/bin/docker"
export DOCKER_STUB="$DS"
printf 'some-other-container\n' > "$DS/names.txt"
rm -f "$DS/exec.txt"
out=$(PATH="$DS/bin:$PATH" sh "$REPO/tools/vty.sh" -c "show version" 2>&1) \
  && rc=0 || rc=$?
[ "$rc" = 1 ] \
  && ok "refused with exit 1 when prisma-vpp is not running" \
  || { bad "tools/vty.sh exited $rc with no container to talk to:"
       printf '%s\n' "$out" | sed 's/^/    /'; }
printf '%s\n' "$out" | grep -q "container 'prisma-vpp' is not running" \
  && ok "and it names the container it looked for" \
  || { bad "it refused without naming the container:"
       printf '%s\n' "$out" | sed 's/^/    /'; }
for m in "make hw-up" "make hw-vpp" "make hw-down"; do
  printf '%s\n' "$out" | grep -q "$m" \
    && ok "and it names '$m'" \
    || bad "the refusal does not name '$m', which is how the box gets one"
done
[ -f "$DS/exec.txt" ] \
  && bad "it refused and ran a command in a container anyway"

echo
echo "=== and it does NOT refuse when the container is there ==="
# The other direction on the same site, because a refusal that fires whatever
# the machine looks like is a script nobody can use.
printf 'prisma-vpp\nsome-other-container\n' > "$DS/names.txt"
rm -f "$DS/exec.txt"
PATH="$DS/bin:$PATH" sh "$REPO/tools/vty.sh" -c "show version" \
  > "$WORK/vty-ok.log" 2>&1 \
  && ok "it ran" \
  || { bad "tools/vty.sh refused a container that is running:"
       sed 's/^/    /' "$WORK/vty-ok.log"; }
if [ -f "$DS/exec.txt" ]; then
  grep -q "prismash.py" "$DS/exec.txt" \
    && grep -q -- "-c show version" "$DS/exec.txt" \
    && ok "and it handed the VTY the command: docker exec $(cat "$DS/exec.txt")" \
    || { bad "it ran something other than the VTY with that command:"
         sed 's/^/    /' "$DS/exec.txt"; }
else
  bad "it exited 0 and started nothing, so the case above asserted about a"\
" script that never got past its own first line"
fi

echo
echo "=== a lab that does not exist is refused before any container starts ==="
# Reachable with no docker on the machine at all: the check sits above the
# socket test on purpose, so a typo costs a sentence rather than a 4 GB image
# and an apt-get.
# Run against the docker stub even though the refusal is above the socket
# test. Not belt and braces: the assertion below is that NO container was
# asked for, and that is only an assertion if something was there to ask.
rm -f "$DS/run.txt"
out=$(PATH="$DS/bin:$PATH" sh "$REPO/tools/lab.sh" no-such-lab 2>&1) \
  && rc=0 || rc=$?
[ "$rc" = 1 ] \
  && ok "refused with exit 1" \
  || bad "tools/lab.sh exited $rc for a lab that is not there: $out"
printf '%s\n' "$out" | grep -q "lab 'no-such-lab' does not exist" \
  && ok "and it names what it was asked for: $out" \
  || { bad "it refused without naming the lab:"
       printf '%s\n' "$out" | sed 's/^/    /'; }
[ -f "$DS/run.txt" ] \
  && bad "it refused and started a container anyway: $(cat "$DS/run.txt")"

echo
echo "=== and build.sh refuses a target it has not got ==="
# Two targets and a usage line. Without the refusal the case statement falls
# through and the script exits 0 having built nothing, which is the shape a
# release script would read as a successful build.
rm -f "$DS/run.txt" "$DS/build.txt"
out=$(PATH="$DS/bin:$PATH" sh "$REPO/tools/build.sh" no-such-target 2>&1) \
  && rc=0 || rc=$?
[ "$rc" = 1 ] \
  && ok "refused with exit 1" \
  || bad "tools/build.sh exited $rc for a target it has not got: $out"
printf '%s\n' "$out" | grep -q "uso: .*\[image|plugin\]" \
  && ok "and it says which two there are: $(printf '%s\n' "$out" | tail -1)" \
  || { bad "it refused without naming the targets it takes:"
       printf '%s\n' "$out" | sed 's/^/    /'; }
[ -f "$DS/run.txt" ] || [ -f "$DS/build.txt" ] \
  && bad "it refused a target it has not got and built something anyway"

# ---------------------------------------------------------------------------
# tools/plugin-coverage.sh
#
# Two refusals, neither driven by anything. It is not in test/suites.list and
# never will be - it builds the C harnesses twice - but both of its refusals
# are about the MEASUREMENT being empty, and an empty measurement reported as
# a number is the defect its own header describes: the first version built
# into a directory of its own, the instrumented binaries were never the ones
# that ran, and it printed a count over counters nobody had written.
#
# Driven against a repository shaped like this one with a Makefile of two
# lines. Nothing is compiled and no coverage is taken; what is asserted is
# what it does when there is nothing to read.
# ---------------------------------------------------------------------------
echo
echo "=== an instrumented build that did not pass is not a coverage figure ==="
PC="$WORK/pc"
mkdir -p "$PC/tools" "$PC/src/plugins/bpa"
cp "$REPO/tools/plugin-coverage.sh" "$PC/tools/"
printf 'test:\n\t@exit 1\n' > "$PC/Makefile"
out=$(sh "$PC/tools/plugin-coverage.sh" "$PC/run.log" 2>&1) && rc=0 || rc=$?
[ "$rc" = 1 ] \
  && ok "refused with exit 1" \
  || bad "a failed instrumented build exited $rc: $out"
printf '%s\n' "$out" | grep -q "the instrumented run did not pass; see $PC/run.log" \
  && ok "and it names the log to read: $(printf '%s\n' "$out" | tail -1)" \
  || { bad "it refused without naming where the build's output went:"
       printf '%s\n' "$out" | sed 's/^/    /'; }

echo
echo "=== no counters at all reads exactly like a plugin with no code in it ==="
# The refusal the tool's own header was written around. A count of zero is
# indistinguishable from a plugin nobody executed, so it stops rather than
# printing one - and it stops BEFORE the uninstrumented rebuild, which would
# otherwise make the empty reading unrepeatable.
printf 'test:\n\t@true\n' > "$PC/Makefile"
out=$(sh "$PC/tools/plugin-coverage.sh" "$PC/run.log" 2>&1) && rc=0 || rc=$?
[ "$rc" = 1 ] \
  && ok "refused with exit 1" \
  || bad "a run that read no counters exited $rc: $out"
printf '%s\n' "$out" | grep -q "no counters were read at all, so this measured nothing" \
  && ok "and it says it measured nothing rather than printing a zero" \
  || { bad "an empty measurement was reported as a coverage figure:"
       printf '%s\n' "$out" | sed 's/^/    /'; }
printf '%s\n' "$out" | grep -q "reads exactly like a plugin with no code in it" \
  && ok "and it says why a zero here cannot be read" \
  || bad "it refused without saying what a zero would have meant"

# ---------------------------------------------------------------------------
# tools/changelog-state.sh
#
# The same contract as roadmap-state.sh above and for the same reason: exit 2
# means the comparison did not happen, exit 1 means findings, and nothing that
# could not reach the tracker may exit 0. A CHANGELOG nobody compared reads
# exactly like a CHANGELOG that agreed.
#
# Its own stub, and not a second case on the one above, because the two tools
# read DIFFERENT gh queries - `--json number,state` against
# `--json number,stateReason` - and a shared stub would answer both from one
# file, which is how a fixture stops being the thing under test.
# ---------------------------------------------------------------------------
echo
CS="$WORK/cs"
mkdir -p "$CS/tools" "$CS/bin" "$CS/gh" "$CS/nogh"
cp "$REPO/tools/changelog-state.sh" "$CS/tools/"

# `gh issue list --state closed --json number,stateReason --jq` prints
# "<number> <REASON>" a line at a time, and `gh issue view N --json title -q`
# prints one title. Those two are the whole surface the script uses.
cat > "$CS/bin/gh" <<'STUB'
#!/bin/sh
case "$1 ${2:-}" in
  "auth status")
    [ -f "$GH_STUB/unauth" ] && exit 1
    echo "github.com: logged in"; exit 0 ;;
  "issue list")
    if [ -f "$GH_STUB/unreachable" ]; then
      echo "could not resolve host: api.github.com" >&2
      exit 1
    fi
    cat "$GH_STUB/closed.txt"; exit 0 ;;
  "issue view")
    echo "the title of #$3" ; exit 0 ;;
esac
echo "the stub was asked for: $*" >&2
exit 1
STUB
chmod +x "$CS/bin/gh"

# A CHANGELOG that RECORDS everything closed, and every case below is one edit
# away from it. A function and not a copied fixture, for the reason the roadmap
# rig gives above.
write_changelog() {  # <path>
  cat > "$1" <<'DOC'
# Changelog

## Unreleased

### Fixed

* **The pool rework**
  ([#10](https://github.com/andrediashexa/hexarouting/issues/10)). What it did.
DOC
}
write_changelog "$CS/CHANGELOG.md"
# #11 is NOT_PLANNED, so a file that never names it is correct. It is in the
# control on purpose: without it, a script that required nothing at all would
# pass this section, and the exemption would be asserted by no case.
printf '10 COMPLETED\n11 NOT_PLANNED\n' > "$CS/gh/closed.txt"

run_cs() {  # [args...] -> $OUT and $RC
  OUT=$(GH_STUB="$CS/gh" PATH="$CS/bin:$PATH" \
        sh "$CS/tools/changelog-state.sh" "$@" 2>&1) && RC=0 || RC=$?
}

echo "=== a CHANGELOG that records every completed issue is the control ==="
# FIRST, and not a formality: without a run in which no refusal fires, a script
# that refused unconditionally would pass every case below it.
run_cs "$CS/CHANGELOG.md"
[ "$RC" = 0 ] \
  && ok "exit 0: $(printf '%s\n' "$OUT" | tail -1)" \
  || { bad "the control CHANGELOG was not clean (rc=$RC):"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }
printf '%s\n' "$OUT" | grep -q "exempt: 1" \
  && ok "and the NOT_PLANNED issue is counted as exempt, not as a finding" \
  || { bad "the exemption was not reported, so nothing says it applied:"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }

echo
echo "=== a completed issue the file names nowhere is a finding ==="
printf '10 COMPLETED\n11 NOT_PLANNED\n12 COMPLETED\n' > "$CS/gh/closed.txt"
run_cs "$CS/CHANGELOG.md"
[ "$RC" = 1 ] \
  && ok "exit 1, which is 'findings'" \
  || bad "an unrecorded completed issue exited $RC"
printf '%s\n' "$OUT" | grep -q "#12" \
  && ok "and it names the issue: $(printf '%s\n' "$OUT" | grep '#12' | head -1)" \
  || { bad "it refused without naming which issue is missing:"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }
printf '10 COMPLETED\n11 NOT_PLANNED\n' > "$CS/gh/closed.txt"

echo
echo "=== a bare #10 in prose is not a record, and the link is ==="
# The tool reads links only, and says so. Asserted rather than left in a
# comment, because a reader who trusts the sentence would file entries that
# satisfy nothing.
write_changelog "$CS/CHANGELOG.md"
sed -i 's|\[#10\](https://github.com/andrediashexa/hexarouting/issues/10)|#10|' \
  "$CS/CHANGELOG.md"
run_cs "$CS/CHANGELOG.md"
[ "$RC" = 1 ] \
  && ok "exit 1 with the link demoted to prose" \
  || bad "a bare number was accepted as a record (rc=$RC)"
write_changelog "$CS/CHANGELOG.md"

echo
echo "=== a file that is not there is not a CHANGELOG that agreed ==="
run_cs "$CS/no-such-changelog.md"
[ "$RC" = 2 ] \
  && ok "exit 2, which is 'nothing was compared'" \
  || bad "a missing file exited $RC"
printf '%s\n' "$OUT" | grep -q "no such file" \
  && ok "and it names the file: $(printf '%s\n' "$OUT" | head -1)" \
  || bad "it refused without naming the file"

echo
echo "=== no gh at all: NOTHING WAS COMPARED, and not a clean run ==="
# The only case that replaces PATH. Everything the script calls is linked in
# beside the absence, so a PATH so short that nothing ran cannot read as a
# pass - the failure this exact case had on the roadmap tool above.
for b in sh dirname mktemp awk sed wc tr rm cat grep cut sort chmod; do
  p=$(command -v "$b") && ln -sf "$p" "$CS/nogh/$b"
done
OUT=$(PATH="$CS/nogh" sh "$CS/tools/changelog-state.sh" \
        "$CS/CHANGELOG.md" 2>&1) && RC=0 || RC=$?
[ "$RC" = 2 ] \
  && ok "exit 2 with no gh on PATH" \
  || bad "a missing gh exited $RC"
printf '%s\n' "$OUT" | grep -q "gh is not installed, so NOTHING WAS COMPARED" \
  && ok "and it says so in those words" \
  || { bad "it failed without saying nothing was compared:"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }

echo
echo "=== gh that cannot log in is not a CHANGELOG that agreed ==="
: > "$CS/gh/unauth"
run_cs "$CS/CHANGELOG.md"
rm -f "$CS/gh/unauth"
[ "$RC" = 2 ] \
  && ok "exit 2 when gh is not authenticated" \
  || bad "an unauthenticated gh exited $RC"
printf '%s\n' "$OUT" | grep -q "never asked" \
  && ok "and it says the tracker was never asked" \
  || bad "it refused without saying the comparison did not happen"

echo
echo "=== a tracker that cannot be reached keeps gh's own words ==="
: > "$CS/gh/unreachable"
run_cs "$CS/CHANGELOG.md"
rm -f "$CS/gh/unreachable"
[ "$RC" = 2 ] \
  && ok "exit 2 when the tracker is unreachable" \
  || bad "an unreachable tracker exited $RC"
printf '%s\n' "$OUT" | grep -q "could not resolve host" \
  && ok "and gh's own text survives: a DNS failure and a rate limit need \
different work and both look like an empty answer from here" \
  || { bad "gh's text was swallowed, so the reader cannot tell which it was:"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }

echo
echo "=== an empty answer is not a tracker with no closed issues ==="
: > "$CS/gh/closed.txt"
run_cs "$CS/CHANGELOG.md"
printf '10 COMPLETED\n11 NOT_PLANNED\n' > "$CS/gh/closed.txt"
[ "$RC" = 2 ] \
  && ok "exit 2 on an answer holding nothing" \
  || bad "an empty tracker answer exited $RC, which would report agreement \
over a list nobody read"

echo
echo "=== a truncated answer is the one that reports agreement falsely ==="
# The dangerous direction, and the reason the count is compared to the limit
# rather than trusted: gh cuts at --limit in silence, and every issue past the
# cut is never looked for at all.
sed 's/^LIMIT=5000$/LIMIT=2/' "$REPO/tools/changelog-state.sh" \
  > "$CS/tools/truncating.sh"
OUT=$(GH_STUB="$CS/gh" PATH="$CS/bin:$PATH" \
      sh "$CS/tools/truncating.sh" "$CS/CHANGELOG.md" 2>&1) && RC=0 || RC=$?
[ "$RC" = 2 ] \
  && ok "exit 2 when the answer is exactly the limit" \
  || bad "a truncated answer exited $RC"
printf '%s\n' "$OUT" | grep -q "never" \
  && ok "and it says what was not looked for: \
$(printf '%s\n' "$OUT" | sed -n '2p' | sed 's/^ *//')" \
  || bad "it refused without saying the rest was never read"

echo
if [ "$fail" -eq 0 ]; then echo "ALL TESTS PASSED"; exit 0; fi
echo "$fail FAILURES"
exit 1
