#!/bin/sh
# The plugin message reader, driven: both halves, both floors, and the
# extraction it all stands on.
#
# WHY THIS FILE DRIVES A READER INSTEAD OF ARRANGING BOXES. The other four of
# this shape - test_selftest.sh, test_installer.sh, test_bind_nic.sh,
# test_apply_tuning.sh - arrange their own boxes, because a box there is the
# program run with an answer fed to it. tools/plugin-says.sh reads
# src/plugins/bpa/bpa_cli.c, where a box is a DATAPLANE STATE: "no subscriber
# holds a block right now" needs a pool with nobody on it, and "rate-cap
# refusals" needs a host opening sessions faster than the pool allows. Both
# need VPP, and `make test` runs with no root, no VPP and no network - a
# property TESTING.md defends, and this is not the change that takes it away.
#
# So the real corpus is the lab suite's, kept with LAB_OUTPUT (#366), and what
# is pinned HERE is the reader itself: that it goes green when every message
# has a box, red when one has none, red when a line is on every box, and red
# rather than green when it was handed nothing to read. A reader nobody has
# watched fail is a reader that reports whatever it was pointed at.
#
# Driven with a STUB SOURCE, for the reason test/test_run_all_labs.sh drives
# the lab runner with stub labs: a corpus in which exactly one message is
# missing is not something a real run hands you on request, and a rule whose
# red branch is only ever seen by accident is not a rule.
#
# And pinned against the REAL bpa_cli.c as well, in its own section. A stub
# proves the reader reads the language; only the product proves it still reads
# the product. The C the plugin is written in is not the C in a stub: one
# sentence over four source lines, "%Lu" beside "%llu", a message inside a
# macro body, and eleven hundred lines of comment with quotation marks in
# them.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
READER=$REPO/tools/plugin-says.sh
PLUGIN=$REPO/src/plugins/bpa/bpa_cli.c
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail + 1)); }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# A stub with one message of each kind the rule has to tell apart.
cat > "$WORK/stub.c" <<'STUB'
/* A comment with vlib_cli_output (vm, "this is not a call site"); in it. */
static void
stub (vlib_main_t *vm)
{
  vlib_cli_output (vm, "the first message, whole on one line");
  vlib_cli_output (vm, "a message split over "
		       "two source lines");
  vlib_cli_output (vm, "%Lu blocks and %llu ports and %u each", a, b, c);
  vlib_cli_output (vm, "%v", s);
  vlib_cli_output (vm, fmt, s);
  vlib_cli_output (vm, "a line every box prints");
}
STUB

# NOT `out=$(run ...)`. A command substitution is a subshell and the status
# read back after one would be the subshell's, so RC would be whatever the
# last `cat` returned and every red run below would read as green. This is the
# same trap test/say-coverage.sh records about counting boxes in a variable.
run() {   # <source> <corpus...> -> $out, $RC
  RC=0
  "$READER" "$@" > "$WORK/out" 2>&1 || RC=$?
  out=$(cat "$WORK/out")
}

echo "=== the extraction reads C, and reads it the way the rule says ==="
# Read once, from the stub, and asserted piece by piece. Each of these is a
# thing the shell extraction next door does not have to do and this one does.
. "$REPO/test/say-coverage.sh"
SAY_SRC=$WORK/stub.c
MSGS=$(say_messages_c vlib_cli_output)
n=$(printf '%s\n' "$MSGS" | grep -c .)
[ "$n" -eq 6 ] \
  && ok "6 call site(s) in the stub, and the one inside a comment is not one" \
  || bad "the stub has 6 call sites and the extraction found $n: a comment is \
not a call site and a string inside one is not a message"

# Adjacent literals joined. clang-format puts one sentence on four lines and
# each piece alone is a needle no box ever printed whole.
printf '%s\n' "$MSGS" | grep -q "a message split over two source lines" \
  && ok "adjacent string literals are joined into one message" \
  || bad "a message split over two source lines came out in pieces: $(printf \
'%s\n' "$MSGS" | sed -n '2p')"

# The length modifier goes with the conversion. "%Lu" cut as "%L" alone leaves
# a "u" glued to the next word, and "u blocks and" is a needle no run holds.
printf '%s\n' "$MSGS" | sed -n '3p' | grep -q "^[0-9]*	 blocks and " \
  && ok "%Lu and %llu are cut whole, so no stray length modifier survives" \
  || bad "a length modifier survived the cut: $(printf '%s\n' "$MSGS" \
| sed -n '3p' | tr '\001' '~')"

# A call with no literal left is still a row, and so is one whose format is a
# variable. A site that vanished would be a branch reported as covered because
# nothing looked at it.
printf '%s\n' "$MSGS" | sed -n '4p' | grep -q '	$' \
  && printf '%s\n' "$MSGS" | sed -n '5p' | grep -q '	$' \
  && ok "a format with nothing literal in it, and one that is a variable, are \
still counted as call sites with nothing to read" \
  || bad "a call site with no readable needle was dropped from the count \
instead of being named as unreadable"

echo
echo "=== a corpus in which every message has a box goes green ==="
mkdir -p "$WORK/full"
{ echo "the first message, whole on one line"
  echo "a line every box prints"; } > "$WORK/full/one.out"
{ echo "a message split over two source lines"
  echo "7 blocks and 9 ports and 3 each"; } > "$WORK/full/two.out"
run "$WORK/stub.c" "$WORK/full"
if [ "$RC" -eq 0 ]; then
  ok "every message the stub can print has a box, and no line is on every box"
else
  bad "a corpus covering every message went red:
$(printf '%s\n' "$out" | sed 's/^/    /')"
fi

echo
echo "=== a message no box printed is named, and is a failure ==="
# THE HALF THAT FINDS A BRANCH NOTHING HAS RUN. One message removed from the
# corpus above, nothing else changed.
mkdir -p "$WORK/gap"
cp "$WORK/full/one.out" "$WORK/gap/one.out"
grep -v "^a message split over" "$WORK/full/two.out" > "$WORK/gap/two.out"
run "$WORK/stub.c" "$WORK/gap"
if [ "$RC" -ne 0 ] \
   && printf '%s\n' "$out" | grep -q "a message split over two source lines"; then
  ok "the message with no box is named, with its line, and the run is red"
else
  bad "a message no box printed did not go red, or went red without naming \
it - which is a reader whose gap is only visible to somebody already looking:
$(printf '%s\n' "$out" | sed 's/^/    /')"
fi

echo
echo "=== a message every box printed is named, and is a failure ==="
# THE OTHER HALF, and it is not the same thing. The one above refuses a
# message printed by NO box, which is a branch nothing has run. This refuses a
# line printed by EVERY box, which is a branch whose other form nothing has
# seen: whatever decided it answered the same way every time. In
# test_selftest.sh that found three stubs answering a constant; here it is a
# corpus whose boxes are all the same box.
mkdir -p "$WORK/same"
cp "$WORK/full/one.out" "$WORK/same/one.out"
{ cat "$WORK/full/two.out"; echo "a line every box prints"; } > "$WORK/same/two.out"
run "$WORK/stub.c" "$WORK/same"
if [ "$RC" -ne 0 ] \
   && printf '%s\n' "$out" | grep -q "^    a line every box prints$"; then
  ok "the message every box printed is named, and the run is red"
else
  bad "a message on every box did not go red, or went red without naming it:
$(printf '%s\n' "$out" | sed 's/^/    /')"
fi

# AND THE HARNESS'S OWN LINES ARE NOT THAT. A box here is a lab transcript,
# not a run of the program: every lab in a green suite ends with "LAB PASSED"
# and the verdict banner, and reading those as "a branch whose other form
# nothing has seen" would turn this red on every correct run - which is a
# check people learn to run around, #333. The question asked of the program is
# whether a MESSAGE is on every box.
mkdir -p "$WORK/boiler"
for b in one two; do
  { echo "=========== VERDICT ==========="
    cat "$WORK/full/$b.out"
    echo "LAB PASSED"; } > "$WORK/boiler/$b.out"
done
run "$WORK/stub.c" "$WORK/boiler"
if [ "$RC" -eq 0 ]; then
  ok "a line every transcript carries that the program never printed is not \
a branch: the harness's own banner does not turn this red"
else
  bad "the harness's own lines were read as the program's, so a green lab \
suite would be red here for having passed:
$(printf '%s\n' "$out" | sed 's/^/    /')"
fi

echo
echo "=== a reader that was handed nothing goes red, not green ==="
# THE FLOOR, and it is the check that decides whether any of the above means
# anything. Every rule in this file reads green on an empty file: no message
# is missing from a corpus with no messages in it, and no line is on every box
# when there are no boxes. Three ways to arrive there, because they are three
# different mistakes.
mkdir -p "$WORK/empty"
run "$WORK/stub.c" "$WORK/empty"
[ "$RC" -ne 0 ] \
  && ok "a corpus directory with no files in it is refused" \
  || bad "an empty corpus read as a pass, so this reader would clear a plugin \
by never having looked at it"

: > "$WORK/empty/silent.out"
run "$WORK/stub.c" "$WORK/empty"
if [ "$RC" -ne 0 ] \
   && printf '%s\n' "$out" | grep -q "asked nothing"; then
  ok "a run that printed nothing is not a box"
else
  bad "a box whose output is empty was counted as a box, which would take the \
'printed by every box' half down with it - no line is printed by every box \
once one of them prints none:
$(printf '%s\n' "$out" | sed 's/^/    /')"
fi

run "$WORK/stub.c" "$WORK/nowhere"
[ "$RC" -ne 0 ] \
  && ok "a corpus path that does not exist is refused" \
  || bad "a corpus that was never opened read as a pass"

# And the extraction's own floor: a printer name that finds nothing.
echo "int main (void) { return 0; }" > "$WORK/nothing.c"
run "$WORK/nothing.c" "$WORK/full"
if [ "$RC" -ne 0 ] \
   && printf '%s\n' "$out" | grep -q "no message could be read"; then
  ok "a source no message could be read out of is refused, not cleared"
else
  bad "a source the extraction found nothing in reported every branch of it \
as covered:
$(printf '%s\n' "$out" | sed 's/^/    /')"
fi

echo
echo "=== and it still reads the plugin itself ==="
# THE STUB PROVES THE READER, THE PRODUCT PROVES IT STILL READS THE PRODUCT.
# These numbers are a floor and not an equality: a message added to bpa_cli.c
# must not be able to lower them, and pinning the exact count would turn every
# new `show bpa` line into a red check in a file that is not about it.
SAY_SRC=$PLUGIN
PMSGS=$(say_messages_c vlib_cli_output)
sites=$(printf '%s\n' "$PMSGS" | grep -c .)
[ "$sites" -ge 87 ] \
  && ok "$sites vlib_cli_output() call site(s) read out of bpa_cli.c" \
  || bad "only $sites call site(s) were read out of bpa_cli.c, which had 87 \
when this was written: the extraction has stopped seeing the product"

readable=$(printf '%s\n' "$PMSGS" | awk -F'\t' '
  { n = split($2, f, "\001"); t = 0
    for (i = 1; i <= n; i++) t += length(f[i])
    if (t >= 8) c++ }
  END { print c + 0 }')
[ "$readable" -ge 60 ] \
  && ok "$readable of them carry a needle of 8 literal characters or more" \
  || bad "only $readable call site(s) have a needle to hunt for, against 67 \
when this was written"

# Four sentences, each named in #366 as one an operator reads during an
# incident, each pinned WHOLE. Whole because the failure this guards against
# is silent: an extraction that truncated at the first source line break would
# still find 87 sites and would hunt for half of every message.
missing=""
for want in \
  "remap: every session on this pool ends when it is applied." \
  "this answers for NOW ONLY. The block belonged to somebody else before" \
  "port refusals are not a box fault: this subscriber asked for more ports" \
  "note: no CGNAT interface has an MTU yet, so 'auto' clamps nothing"
do
  printf '%s\n' "$PMSGS" | grep -qF "$want" || missing="$missing
    $want"
done
[ -z "$missing" ] \
  && ok "the sentences an operator reads during an incident come out whole" \
  || bad "these came out of bpa_cli.c truncated or not at all, so the reader \
would hunt for a string no box prints:$missing"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
