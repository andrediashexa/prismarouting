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
# this shape - appliance/selftest, prisma-routing-install, bind-dataplane-nic and
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
# WHICH PRINTERS, and the THREE BODIES of text the plugin prints from.
#
# #366 read one of them, and the other two together are bigger. Measured on
# bpa_cli.c:
#
#   vlib_cli_output format strings      87 call sites, 67 readable, 66 distinct
#   literals passed as an ARGUMENT      45 branches with a literal, 22 readable
#   format() format strings             88 call sites, 67 readable
#
# The second is the one with the branches in it. `cond ? " - PINNED by a
# forward, so no hold-down will take it" : ""` says why a block an operator
# expected back has not come back, and `"   BELOW the floor " rfc " asks for"`
# is the warning that stops somebody setting a timeout that breaks the
# protocol. Both reach the screen as a value handed to a "%s", so #366's rule
# - which is written on the format string, and cuts values out - could not see
# either of them. test/say-coverage.sh reads them now, for whichever printers
# are named here, and argues the rule where the code is.
#
# PLUGIN_PRINTERS names the printers and defaults to all three bodies:
# `vlib_cli_output format`. `format` is in the default since #368 because
# `show bpa pool` and `show bpa config` are built into a vector and printed by
# one call with "%U" or "%v" at the end - the two screens an operator looks at
# first are written entirely that way, and a reader that left them out was
# clearing them by never having gone to look.
#
#   PLUGIN_PRINTERS=vlib_cli_output sh tools/plugin-says.sh ...
#
# narrows it back to #366's body alone.
#
# WHY BOTH HALVES MOVED AT ONCE, which is the decision and not a flag nobody
# chose. #366 left `format` out with a reason that still holds: a denominator
# that moved without the evidence moving is how a measurement stops meaning
# anything, and `36 of 66` becoming `36 of 200` tells the truth about a bigger
# thing while ceasing to be a number anybody can act on. So the evidence moved
# in the same change: #366's corpus was 13 labs, and the one this was measured
# against is the WHOLE suite. That is the largest body of `show bpa` output
# this repository can produce, and what is left unaccounted for after it is a
# list of screens that need a lab rather than an estimate of one.
#
# Doing it in two steps would have moved the denominator twice for one
# decision, with the same corpus behind both moves: the argument-literal rule
# is a single rule and it applies to whichever printers are named, so reading
# it at `vlib_cli_output` sites and not at `format()` ones would have been the
# same rule run over half the file - and the half skipped is where eighteen of
# the forty-five branches are: twelve in the formatters `show bpa pool` is
# built out of, six in the configuration `show bpa config` emits.
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
say_init "$SRC" c "${PLUGIN_PRINTERS:-vlib_cli_output format}"
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
