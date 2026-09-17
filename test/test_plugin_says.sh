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
#
# The last four are the SECOND BODY (#368): a sentence that reaches the screen
# as an argument rather than as the format. Each is a case the rule has to
# answer differently, and getting any of them wrong is silent - a screen that
# is read as covered, or a needle hunted for under a string no box prints.
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
  vlib_cli_output (vm, "%s%s", tag,
		   cond ? " - the sentence in the first branch"
			: " - the sentence in the second branch");
  vlib_cli_output (vm, "%s%u", n ? "" : "  ", n);
  vlib_cli_output (vm, "%s", "the floor " rfc " is asked for here");
  vlib_cli_output (vm, "%s", whatever);
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
# FOURTEEN AND NOT TEN: a row is a MESSAGE, and since #368 one call site can
# carry several - its format string and one per branch of every argument that
# has a literal in it. The exact number is pinned rather than a floor because
# both mistakes are silent and they are opposite: a row too few is a screen
# reported as covered by nothing having looked at it, and a row too many is a
# needle nobody can ever find a box for. It is also what pins `whatever` on
# the last line producing NO row - an argument that is a bare variable has no
# words of its own, and putting every variable in the plugin into the
# denominator would make the number unreachable by construction.
[ "$n" -eq 14 ] \
  && ok "14 message(s) over 10 call sites in the stub, and the one inside a \
comment is not a call site" \
  || bad "the stub carries 14 messages over 10 call sites and the extraction \
found $n: a comment is not a call site, an argument that is a bare variable is \
not a message, and every branch that has a literal in it is one"

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

# THE SECOND BODY, branch by branch. Both sides of the conditional come out,
# and they come out APART: no run ever carries two branches of one conditional
# at once, so a needle that joined them would be hunted for under a string no
# box prints - which is the defect that makes a rule go red on correct work.
first=$(printf '%s\n' "$MSGS" | grep -c "^12	 - the sentence in the first branch$")
second=$(printf '%s\n' "$MSGS" | grep -c "^12	 - the sentence in the second branch$")
[ "$first" -eq 1 ] && [ "$second" -eq 1 ] \
  && ok "a sentence handed to a conversion is a message, and each branch of a \
conditional is its own" \
  || bad "the branches of an argument did not come out one row each ($first \
and $second): a sentence in an argument is a screen an operator reads, and \
two branches joined are a needle no box prints"

# The VALUE between two literals of one argument is cut, exactly as a
# conversion in a format string is. `"   BELOW the floor " rfc " asks for"` is
# the shape in the plugin, and a needle that ran straight through the gap
# would be looked for with the name of an RFC in the middle of it.
printf '%s\n' "$MSGS" | grep -q "^16	the floor $(printf '\001') is asked for here$" \
  && ok "adjacent literals of an argument join, and what sits between two of \
them is cut like any other value" \
  || bad "an argument built of two literals with a value between them came out \
whole or in pieces: $(printf '%s\n' "$MSGS" | sed -n '13p' | tr '\001' '~')"

# AND THE FLOOR IS THE RULE THAT DECIDES WHICH ARGUMENTS ARE SENTENCES. A
# separator handed to a "%s" is not a screen - it prints two spaces - and the
# floor already says so, because it is the same thing it says about a format
# string made entirely of conversions. It is a ROW, though, and named as
# unreadable: text that reaches the screen and cannot be recognised is not the
# same as text that is not there.
printf '%s\n' "$MSGS" | grep -q "^15	  $" \
  && ok "an argument under the floor is counted and named as unreadable, not \
demanded as a message and not passed over" \
  || bad "a separator passed as an argument was either demanded as a message \
nothing can print or dropped from the count altogether"

echo
echo "=== a corpus in which every message has a box goes green ==="
mkdir -p "$WORK/full"
# The argument sentences are printed the way the program prints them - inside
# a line, after whatever the conversion before them was handed - because that
# is the only place a needle cut from an argument can be found. One branch per
# box, since no run can carry both.
{ echo "the first message, whole on one line"
  echo "a line every box prints"
  echo "some tag - the sentence in the first branch"; } > "$WORK/full/one.out"
{ echo "a message split over two source lines"
  echo "7 blocks and 9 ports and 3 each"
  echo "other tag - the sentence in the second branch"
  echo "the floor RFC 1122 is asked for here"; } > "$WORK/full/two.out"
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
echo "=== a branch of an argument that no box took is named too ==="
# THE SAME HALF, ASKED OF THE SECOND BODY, and it is the case #368 exists for.
# A conditional in an argument is where the branches are: the line is printed
# on every box either way, and what nothing has ever seen is the OTHER answer.
# So the corpus below prints the first branch on both boxes and the second on
# neither - a state that reads as fully covered to anything keyed on lines, or
# on format strings, and is a sentence no box has ever made the program say.
#
# It is red TWICE over, and both are true of the same defect: no box printed
# the second branch, and every box printed the first. Asserted on the first,
# which is the one that names the sentence.
mkdir -p "$WORK/branch"
cp "$WORK/full/one.out" "$WORK/branch/one.out"
grep -v " - the sentence in the second branch" "$WORK/full/two.out" \
  > "$WORK/branch/two.out"
echo "other tag - the sentence in the first branch" >> "$WORK/branch/two.out"
run "$WORK/stub.c" "$WORK/branch"
if [ "$RC" -ne 0 ] \
   && printf '%s\n' "$out" | grep -q -- "- the sentence in the second branch"; then
  ok "a branch of a conditional argument that no box took is named, with its \
line, and the run is red"
else
  bad "a sentence that only one branch of an argument prints was reported as \
covered because the other branch printed on the same line:
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
echo "=== a branch printed INSIDE the row its own format string printed ==="
# #369, and it is the defect that made this reader name a branch two labs were
# printing. bpa_audit.c prints
#
#   vlib_cli_output (vm, "    the sequence jumps from %llu to %llu: %llu "
#                        "entr%s removed from the middle", ...,
#                    n == 1 ? "y was" : "ies were");
#
# so the branch reaches the screen in the MIDDLE of the row its own format
# string printed. The format is the more specific message and wins the row;
# what it used to take with it was its SPAN, which runs from the first literal
# fragment to the last and swallows every value poured in between - the branch
# among them. `ies were` is eight characters, exactly at SAY_MIN, and
# bpa_audit.c:560 and :647 were both named as branches nothing had ever run
# while lab/audit.sh was printing them into the corpus.
#
# FOUR INDENTATIONS, because the corpus is the lab's rendering of the output
# and not the output: `printf '%s\n' "$X" | sed 's/^/  /'` is the house idiom
# for showing a screen inside a lab and a good many of them use it. The four
# are EXTRA columns in front of the row the box prints, and the four spaces
# the format string itself carries stay on it - those are part of the message,
# and a row stripped of them is not a row any box ever printed. Zero extra is
# the plugin as it runs; two, four and six are three labs being tidy.
cat > "$WORK/plural.c" <<'PLURAL'
static void
tamper (vlib_main_t *vm)
{
  vlib_cli_output (vm, "    the sequence jumps from %llu to %llu: %llu "
		       "entr%s removed from the middle",
		   a, b, c, n == 1 ? "y was" : "ies were");
  vlib_cli_output (vm, "the audit trail verifies from end to end");
}
PLURAL
ROW='    the sequence jumps from 2 to 5: 2 entries were removed from the middle'
OTHER='the audit trail verifies from end to end'
mkdir -p "$WORK/plural"
for extra in 0 2 4 6; do
  pad=""; pn=$extra
  while [ "$pn" -gt 0 ]; do pad="$pad "; pn=$((pn - 1)); done
  printf '%s%s\n' "$pad" "$ROW" > "$WORK/plural/one.out"
  printf '%s\n' "$OTHER" > "$WORK/plural/two.out"
  run "$WORK/plural.c" "$WORK/plural"
  if [ "$RC" -eq 0 ]; then
    ok "the branch in the middle of its own row has a box, with the lab \
indenting the screen by $extra extra column(s)"
  else
    bad "with the screen indented by $extra extra column(s) the reader named a \
message the corpus holds - a branch interpolated into the row its own format \
printed is lost when the format is spent on the row:
$(printf '%s\n' "$out" | sed 's/^/    /')"
  fi
done

echo
echo "=== and a row the box did not print is still attributed to nothing ==="
# THE OTHER FAILURE, and it is the expensive one. A matcher that ignores more
# is a matcher that matches more, and the whole argument for this reader is
# that it refuses to call a branch covered when nothing ran it. So: a real
# message, with the corpus row altered, and the reader must still name it.
cat > "$WORK/guard.c" <<'GUARD'
static void
guard (vlib_main_t *vm)
{
  vlib_cli_output (vm, "    the sequence jumps from %llu to %llu: %llu "
		       "entr%s removed from the middle",
		   a, b, c, n == 1 ? "y was" : "ies were");
  vlib_cli_output (vm, "verify: nothing follows the tamper report");
  vlib_cli_output (vm, "the audit trail verifies from end to end");
}
GUARD
mkdir -p "$WORK/altered"
printf '%s\n' "$OTHER" > "$WORK/altered/two.out"
# One word changed in the middle of the row, `removed` for `deleted`. The
# branch is genuinely on this row and must still be attributed; the format
# string is not, and no amount of the rest of it lining up may buy it a box.
printf '%s\n' \
  '    the sequence jumps from 2 to 5: 2 entries were deleted from the middle' \
  > "$WORK/altered/one.out"
run "$WORK/guard.c" "$WORK/altered"
if [ "$RC" -ne 0 ] \
   && printf '%s\n' "$out" | grep -q "the sequence jumps from" \
   && ! printf '%s\n' "$out" | grep -q "ies were"; then
  ok "a word altered in the middle of a row leaves the message unattributed, \
and the branch that IS on that row is still found"
else
  bad "a row the box never printed bought a message a box, or the branch that \
was on it lost one - the first is this reader clearing a branch nothing ran:
$(printf '%s\n' "$out" | sed 's/^/    /')"
fi

# AND THE SEAM, which is what the span cut used to open. Striking out a
# message leaves the row the length it was; CUTTING it glued what stood in
# front of the message to what stood behind it, and a needle could then match
# across the join - a sentence assembled out of two pieces the console never
# showed side by side. This row carries the tamper report with a word in front
# of it and a word behind, and the message made of those two words alone was
# printed by nothing.
mkdir -p "$WORK/seam"
printf '%s\n' "$OTHER" > "$WORK/seam/two.out"
printf '%s%s%s\n' 'verify: nothing ' "$ROW" ' follows the tamper report' \
  > "$WORK/seam/one.out"
run "$WORK/guard.c" "$WORK/seam"
if [ "$RC" -ne 0 ] \
   && printf '%s\n' "$out" | grep -q "verify: nothing follows the tamper report" \
   && ! printf '%s\n' "$out" | grep -q "ies were" \
   && ! printf '%s\n' "$out" | grep -q "the sequence jumps from"; then
  ok "a sentence that exists on the row only once another message is taken out \
of the middle of it is attributed to nothing"
else
  bad "the words in front of a message were joined to the words behind it and \
the sentence that made was read as printed, which is this reader inventing a \
box:
$(printf '%s\n' "$out" | sed 's/^/    /')"
fi

echo
echo "=== a ceiling that is holding rows names them on the green run ==="
# NOT THE MESSAGE READER but the width half beside it (#367), and it is here
# because this is the file that drives the reader with stub sources and
# corpora it writes itself - the three appliance suites bring real boxes and
# cannot arrange a ceiling to look at.
#
# SAY_UNSAFE_AT_MOST=48 is what test_installer.sh holds, and until now the
# forty-nine rows under it could only be seen by lowering the variable to
# provoke a red run. Nobody lowers a variable whose contents they cannot see,
# and a ceiling nobody reads is an allowlist with a number in front of it.
#
# In a SUBSHELL with its own ok/bad, because say_report states its verdict
# through the caller's pair and this case is about what it PRINTS. A red run
# in there would be lost, so the absence of FAIL is asserted as well.
cat > "$WORK/ceiling.sh" <<'CEIL'
#!/bin/sh
say() { printf '%s\n' "$*"; }
count () {
  say "the pool has $1 blocks free on this box right now"
}
refuse_disk () {
  say "the disk $1 carries no partition table that this program can read"
}
refuse_card () {
  say "the card $1 is already bound to a driver and will not be taken"
}
CEIL
cout=$(
  ok()  { echo "  [ ok ] $*"; }
  bad() { echo "  FAIL: $*"; }
  say_init "$WORK/ceiling.sh" sh
  printf '%s\n' "the pool has 4096 blocks free on this box right now" | say_box
  printf '%s\n' "the disk wobble carries no partition table that this program \
can read" | say_box
  printf '%s\n' "the card wobbly is already bound to a driver and will not be \
taken" | say_box
  SAY_UNSAFE_AT_MOST=2 say_report ceiling.sh
)
if ! printf '%s\n' "$cout" | grep -q "FAIL" \
   && printf '%s\n' "$cout" | grep -q "2 row(s) carry a value with no widest" \
   && printf '%s\n' "$cout" | grep -q "^    ceiling.sh:7  up to .* columns before the value" \
   && printf '%s\n' "$cout" | grep -q "^    ceiling.sh:10  up to .* columns before the value"; then
  ok "a ceiling that is holding two rows names both of them on a green run, \
with the line and the columns before the value"
else
  bad "a green ceiling printed its count and not its rows, so the only way to \
read what it is holding is to lower it and provoke a red run:
$(printf '%s\n' "$cout" | sed 's/^/    /')"
fi
# AND NOTHING UNDER A CEILING OF ZERO. An empty heading reads as a listing
# that failed to print, which is the opposite of the point.
zout=$(
  ok()  { echo "  [ ok ] $*"; }
  bad() { echo "  FAIL: $*"; }
  say_ceiling 0 0 "row(s) of nothing" ""
)
printf '%s\n' "$zout" | grep -q "^  \[ ok \] 0 row(s) of nothing, at the ceiling of 0$" \
  && [ "$(printf '%s\n' "$zout" | grep -c "row(s) of nothing")" = 1 ] \
  && ok "a ceiling of zero states its count and lists nothing under it" \
  || bad "a ceiling with nothing to hold printed a heading with no rows under \
it:
$(printf '%s\n' "$zout" | sed 's/^/    /')"

echo
echo "=== and it still reads the plugin itself ==="
# THE STUB PROVES THE READER, THE PRODUCT PROVES IT STILL READS THE PRODUCT.
# These numbers are a floor and not an equality: a message added to bpa_cli.c
# must not be able to lower them, and pinning the exact count would turn every
# new `show bpa` line into a red check in a file that is not about it.
SAY_SRC=$PLUGIN
# BOTH PRINTERS, because both are read by default since #368 and a floor on
# one of them says nothing about the other. The `format` body is the whole of
# `show bpa pool` and `show bpa config`, and it is the half that would go
# quiet without a word if the extraction stopped seeing it.
nread() {   # rows on stdin -> how many carry SAY_MIN literal characters
  awk -F'\t' '
    { n = split($2, f, "\001"); t = 0
      for (i = 1; i <= n; i++) t += length(f[i])
      if (t >= 8) c++ }
    END { print c + 0 }'
}

PMSGS=$(say_messages_c vlib_cli_output)
sites=$(printf '%s\n' "$PMSGS" | grep -c .)
[ "$sites" -ge 114 ] \
  && ok "$sites message(s) read out of the vlib_cli_output() calls in \
bpa_cli.c" \
  || bad "only $sites message(s) were read out of the vlib_cli_output() calls \
in bpa_cli.c, which had 114 over 87 call sites when this was written: the \
extraction has stopped seeing the product"

readable=$(printf '%s\n' "$PMSGS" | nread)
[ "$readable" -ge 82 ] \
  && ok "$readable of them carry a needle of 8 literal characters or more" \
  || bad "only $readable message(s) have a needle to hunt for, against 82 when \
this was written"

BMSGS=$(say_messages_c "vlib_cli_output format")
bsites=$(printf '%s\n' "$BMSGS" | grep -c .)
breadable=$(printf '%s\n' "$BMSGS" | nread)
{ [ "$bsites" -ge 220 ] && [ "$breadable" -ge 156 ]; } \
  && ok "$bsites message(s) over both printers, $breadable of them with a \
needle: show bpa pool and show bpa config are read too" \
  || bad "only $bsites message(s) ($breadable readable) over both printers, \
against 220 and 156 when this was written: the format() body is the whole of \
show bpa pool and show bpa config and it has gone quiet"

# Seven sentences, each one an operator reads during an incident, each pinned
# WHOLE. Whole because the failure this guards against is silent: an
# extraction that truncated at the first source line break would still find
# every call site and would hunt for half of every message.
#
# The last three are the second body (#368) and they are the reason it was
# widened. The first of them says why a block an operator expected back has
# not come back; the second is the RFC-floor warning that stops somebody
# setting a timeout that breaks the protocol, and it is pinned in two pieces
# because the name of the RFC is interpolated between them by a macro.
missing=""
for want in \
  "remap: every session on this pool ends when it is applied." \
  "this answers for NOW ONLY. The block belonged to somebody else before" \
  "port refusals are not a box fault: this subscriber asked for more ports" \
  "note: no CGNAT interface has an MTU yet, so 'auto' clamps nothing" \
  " - PINNED by a forward, so no hold-down will take it" \
  "   BELOW the floor $(printf '\001') asks for" \
  " (no CGNAT interface has an MTU)"
do
  printf '%s\n' "$BMSGS" | grep -qF "$want" || missing="$missing
    $(printf '%s' "$want" | tr '\001' '~')"
done
[ -z "$missing" ] \
  && ok "the sentences an operator reads during an incident come out whole, \
from the format string and from the argument alike" \
  || bad "these came out of bpa_cli.c truncated or not at all, so the reader \
would hunt for a string no box prints:$missing"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
