#!/bin/sh
# The console driver's waits, measured without qemu.
#
# lab/install-iso.sh types at a serial console and waits for strings the BOX
# prints. Those waits have never been tested: every one of them has only ever
# been exercised by booting a machine, which costs minutes and cannot arrange
# the case where the box answers something else.
#
# That gap has a price already paid. PHASE 4 waited the full 600 seconds for
# "commit complete" on a commit that had printed "commit failed" seconds in,
# and what diagnosed it was the console dump the timeout prints rather than the
# wait itself (#332). Ten minutes per run, and a verdict reading "the box never
# answered" about a box that answered immediately.
#
# THE FUNCTIONS ARE READ OUT OF THE LAB, not copied here. A second copy of a
# wait would pass while the real one did whatever it does: this extracts the
# definitions from lab/install-iso.sh and runs those.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
LAB=$REPO/lab/install-iso.sh
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail + 1)); }

# The four the waits need, in the order the file defines them. Taken by name so
# a renamed function is a missing one here rather than a silent skip.
for _fn in console_since console_gave_up wait_for wait_for_either; do
  if ! grep -q "^$_fn() {" "$LAB"; then
    bad "lab/install-iso.sh no longer defines $_fn(), so this suite is \
measuring nothing"
    echo; echo "$fail FAILURES"; exit 1
  fi
done
sed -n '/^console_since() {/,/^}/p;/^console_gave_up() {/,/^}/p;/^wait_for() {/,/^}/p;/^wait_for_either() {/,/^}/p' \
  "$LAB" > "$WORK/waits.sh"

CONSOLE_LOG=$WORK/console
QEMU_PID=$$          # alive, so the waits do not take the "machine exited" exit
LOG=$WORK/lab.log
. "$WORK/waits.sh"

echo "=== a box that answers the first string ==="
CONSOLE_POS=1
printf 'commit complete\n' > "$CONSOLE_LOG"
wait_for_either "commit complete" "commit failed" 3 "a commit" \
  > "$WORK/o" 2>&1 && _rc=0 || _rc=$?
case $_rc in
  0) ok "the success is 0, immediately" ;;
  *) bad "a box that said 'commit complete' was not read as having said it" ;;
esac

echo
echo "=== and a box that answers the OTHER one, which is the point ==="
# Before wait_for_either this spent the whole timeout and then reported that
# the box had never answered - about a box that had answered at once.
CONSOLE_POS=1
printf 'error: commit failed; rolling back to the previous state\n' \
  > "$CONSOLE_LOG"
_t0=$(date +%s)
wait_for_either "commit complete" "commit failed" 30 "a commit" \
  > "$WORK/o" 2>&1 && _rc=0 || _rc=$?
_el=$(( $(date +%s) - _t0 ))
case $_rc in
  2) ok "the refusal is its own answer (2), not a timeout" ;;
  0) bad "a refused commit was read as a completed one, which is the false \
green this exists to prevent" ;;
  *) bad "a refused commit was read as no answer at all (rc=$_rc)" ;;
esac
[ "$_el" -lt 5 ] \
  && ok "and it is answered at once, not after the timeout (${_el}s of 30)" \
  || bad "it took ${_el}s to notice a string that was already on the console"

echo
echo "=== a box that says neither is still a timeout ==="
# The distinction has to cost nothing: a driver that reported a refusal for a
# box that said nothing would turn every hang into a wrong diagnosis.
CONSOLE_POS=1
printf 'prisma# [edit] \n' > "$CONSOLE_LOG"
wait_for_either "commit complete" "commit failed" 2 "a commit" \
  > "$WORK/o" 2>&1 && _rc=0 || _rc=$?
case $_rc in
  1) ok "neither string is 1, after the timeout" ;;
  *) bad "a box that said neither was given one of the two answers" ;;
esac
grep -q "commit complete or commit failed" "$WORK/o" \
  && ok "and the report names both strings it gave up on" \
  || bad "the timeout does not say what it was waiting for: $(cat "$WORK/o")"

echo
echo "=== and the position advances, so one line does not answer twice ==="
# Without this a prompt printed once satisfies every later wait and the driver
# races ahead of the box saying yes to itself. wait_for carries the same
# advance and the same comment; this is the check neither of them had.
CONSOLE_POS=1
printf 'commit complete\n' > "$CONSOLE_LOG"
wait_for_either "commit complete" "commit failed" 3 "a commit" \
  > "$WORK/o" 2>&1 && : || :
wait_for_either "commit complete" "commit failed" 2 "a second commit" \
  > "$WORK/o" 2>&1 && _rc=0 || _rc=$?
case $_rc in
  1) ok "the same line does not answer the next wait" ;;
  *) bad "one 'commit complete' answered two waits, so the driver can race \
ahead of the box" ;;
esac

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
