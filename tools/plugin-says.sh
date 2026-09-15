#!/bin/sh
# Which of a plugin's messages any box in a corpus ever printed, and which
# line every box printed.
#
#   sh tools/plugin-says.sh src/plugins/bpa/bpa_cli.c <corpus> [<corpus> ...]
#
# A corpus is a directory of files or a file. ONE FILE IS ONE BOX. For the
# lab suite that is one lab's whole transcript, kept with
# `LAB_OUTPUT=<dir> sh tools/run-all-labs.sh`.
#
# WHY THIS IS A TOOL AND NOT A SUITE IN `make test`. The other four readers of
# this shape - appliance/selftest, hexarouting-install, bind-dataplane-nic and
# apply-tuning - arrange their own boxes, because a box there is the program
# run with an answer fed to it. A box here is a DATAPLANE STATE: reaching
# "no subscriber holds a block right now" means a pool with no subscriber on
# it, and reaching "rate-cap refusals" means a host opening sessions faster
# than the pool allows. Neither is a prompt; both need VPP, and `make test`
# runs on a developer account with no root, no VPP and no network, which is a
# property TESTING.md defends and this must not be the change that takes it
# away.
#
# So the boxes come from where they already exist - the lab suite prints a
# great deal of `show bpa` - and the reader is pointed at them. What IS in
# `make test` is test/test_plugin_says.sh, which drives this file against a
# stub source and corpora it writes itself, so that both halves and both
# floors have a green box and a red one. That is the bargain
# test/test_run_all_labs.sh already makes: arranging a real VPP that fails to
# start is not something `make test` can do either.
#
# IT IS A PASS OR A FAIL, unlike tools/plugin-coverage.sh next door. That file
# counts lines nobody executed and leaves the judgement to a reader, because a
# line has several honest answers - measured elsewhere, unreachable outside
# VPP, dead. A MESSAGE has one: either some box made the program print it or
# no box did, and "no box did" is a branch that has never run anywhere. Where
# arranging one needs hardware nobody has, that is a finding to take to the
# issue, not an exception to add here.
#
# WHICH PRINTER, and the half of the plugin this does not read.
#
# PLUGIN_PRINTERS names them and defaults to `vlib_cli_output`, which is the
# body #366 measured: 87 call sites in bpa_cli.c, 67 of them with a needle to
# hunt for. There is a second body the same size beside it - 88 `format()`
# sites, 67 readable - because `show bpa pool` and `show bpa config` are built
# into a vector and printed by one call with "%U" or "%v" at the end. Those
# are screens too, and
#
#   PLUGIN_PRINTERS="vlib_cli_output format" sh tools/plugin-says.sh ...
#
# reads them. It is not the default because the number then answers a
# different question from the one the issue asked, and a denominator that
# moved without the evidence moving is how a measurement stops meaning
# anything. Widening it is a decision, not a flag nobody chose.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)

[ $# -ge 2 ] || {
  echo "usage: sh tools/plugin-says.sh <source.c> <corpus> [<corpus> ...]" >&2
  echo "  <corpus> is a directory of box outputs, or one file per box." >&2
  exit 2; }

SRC=$1
shift
[ -f "$SRC" ] || { echo "no such source: $SRC" >&2; exit 2; }

fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail + 1)); }

# The reader itself is test/say-coverage.sh, once, for the reason that file
# gives at the top of itself.
. "$REPO/test/say-coverage.sh"
say_init "$SRC" c "${PLUGIN_PRINTERS:-vlib_cli_output}"
# A BOX HERE IS A TRANSCRIPT, not a run of the program. One lab is eight
# hundred lines of container build, vppctl and packet counts with a dozen
# `show bpa` screens in it, so the "printed by every box" half is asked of the
# program's MESSAGES rather than of the raw lines - otherwise it names
# "LAB PASSED" on every all-green suite and says nothing about the plugin.
# test/say-coverage.sh argues it where the two forms are.
SAY_BOX_IS=transcript

boxes=0
for c in "$@"; do
  if [ -d "$c" ]; then
    for f in "$c"/*; do
      [ -f "$f" ] || continue
      # .rc is a status, not a screen, and run-all-labs writes one beside
      # every transcript. Counting it as a box would add 87 boxes of one digit
      # each, and the "printed by every box" half below would then be about
      # nothing: no line is printed by a box whose whole output is "0".
      case $f in *.rc) continue ;; esac
      say_box < "$f"
      boxes=$((boxes + 1))
    done
  elif [ -f "$c" ]; then
    say_box < "$c"
    boxes=$((boxes + 1))
  else
    bad "no such corpus: $c"
  fi
done

echo "=== the corpus was read at all ==="
# BEFORE the halves and not instead of the floor inside them. say_report
# refuses a corpus that kept no output; this refuses one whose FILES were
# never opened, which reads identically from in there and is a different
# mistake - a path typed wrong, or a run whose trap fired before anybody
# looked.
if [ "$boxes" -eq 0 ]; then
  bad "not one box was read out of: $*"
else
  ok "$boxes box(es) read"
fi

say_report "$SRC"

echo
if [ "$fail" -eq 0 ]; then echo "ALL TESTS PASSED"; exit 0; fi
echo "$fail FAILURES"
exit 1
