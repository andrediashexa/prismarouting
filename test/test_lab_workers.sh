#!/bin/sh
# How a lab decides whether the box it starts has workers, and how many.
#
# LAB_WORKERS was a 0/1 flag with the name of a count. Anything that was not
# exactly `1` fell through to "no workers", so `LAB_WORKERS=2` started a
# single-threaded box and the lab measured it happily. That is the shape this
# project keeps finding: a thing that looks like it did what you asked and did
# the opposite, with no line saying so.
#
# It cost a run on the day lab/extblocks.sh was repaired, verifying the very
# case the repair was for:
#
#   $ LAB_WORKERS=2 sh tools/lab.sh extblocks
#     the box has 1 worker slice(s), so:
#     subscriber 100.64.1.8   pool strided, index 8, worker thread 0
#   LAB PASSED
#
# No container here. `startup_conf` takes its source directory from LAB_SRC,
# which is /src everywhere a lab actually runs, so a rule about how an argument
# is read is checked by reading the file it writes (#229).
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }

REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# startup_conf writes into /tmp, and reads from $LAB_SRC/lab/.
LAB_SRC=$REPO
export LAB_SRC
. "$REPO/lab/lib.sh"

workers_in() {  # <path printed by startup_conf> -> the count, or "none"
  case "$1" in
    "$REPO"/*) echo none; return ;;
  esac
  sed -n 's/^ *workers  *\([0-9][0-9]*\).*/\1/p' "$1" | head -1
}

echo "=== 0 and unset are no workers, which every plain lab depends on ==="
unset LAB_WORKERS
got=$(workers_in "$(startup_conf test)")
[ "$got" = none ] && ok "unset gives a box with no workers" \
  || bad "unset gave $got worker(s)"
LAB_WORKERS=0
got=$(workers_in "$(startup_conf test)")
[ "$got" = none ] && ok "and so does 0, which is what the suite passes" \
  || bad "0 gave $got worker(s)"

echo
echo "=== 1 is the default count, which is what the suite passes for +workers ==="
# Unchanged on purpose: tools/run-all-labs.sh passes 0 and 1 and nothing else,
# so a change here would have to be a change there on the same day.
LAB_WORKERS=1
got=$(workers_in "$(startup_conf test)")
[ "$got" = 2 ] && ok "1 gives the default of two workers" \
  || bad "1 gave $got worker(s), not the documented default of 2"
LAB_WORKERS=1 LAB_WORKER_COUNT=3
got=$(workers_in "$(startup_conf test)")
[ "$got" = 3 ] && ok "and LAB_WORKER_COUNT still overrides it" \
  || bad "LAB_WORKER_COUNT=3 gave $got"
unset LAB_WORKER_COUNT

echo
echo "=== above 1 the number IS the count, which is what the name says ==="
for n in 2 4 8; do
  LAB_WORKERS=$n
  got=$(workers_in "$(startup_conf test)")
  [ "$got" = "$n" ] && ok "LAB_WORKERS=$n starts $n workers" \
    || bad "LAB_WORKERS=$n started $got worker(s), and the old code started none"
done

echo
echo "=== the positional form is untouched ==="
# Six labs call startup_conf with "workers" as the second argument, and they
# must not start caring what LAB_WORKERS says.
unset LAB_WORKERS
got=$(workers_in "$(startup_conf test workers)")
[ "$got" = 2 ] && ok "startup_conf test workers still gives two" \
  || bad "the positional form gave $got"

echo
echo "=== and a value that is not a count is refused, not read as off ==="
# The whole defect in one line: the old code read every one of these as "no
# workers" and said nothing.
for bad_value in yes on true 2.5 -1; do
  LAB_WORKERS=$bad_value
  if out=$(startup_conf test 2>&1); then
    bad "LAB_WORKERS=$bad_value was accepted and gave '$out'"
  else
    case "$out" in
      *"is not a number of workers"*) : ;;
      *) bad "LAB_WORKERS=$bad_value was refused without saying why: $out" ;;
    esac
  fi
done
ok "yes, on, true, 2.5 and -1 are each refused with what to pass instead"

echo
echo "=== and no lab decides from the request instead of from the box ==="
# The same defect one layer up, and the one this fix would otherwise have
# created: three labs gated their worker sections on `LAB_WORKERS = 1`, so now
# that 4 really means four workers, a box WITH workers would have skipped every
# section that is about workers. A lab asks the box, with box_workers.
#
# Comment lines are dropped first: three of these files explain the variable in
# prose, and a check that went red on an explanation would be the worst kind.
gates=$(grep -n 'LAB_WORKERS' "$REPO"/lab/*.sh \
        | grep -v '^[^:]*/lib\.sh:' \
        | sed 's/^\([^:]*:[0-9]*\):[[:space:]]*#.*/\1:<comment>/' \
        | grep -v '<comment>' || true)
if [ -z "$gates" ]; then
  ok "no lab reads LAB_WORKERS in code; they read box_workers"
else
  bad "these labs still decide from the request rather than from the box:
$gates"
fi
grep -q '^box_workers() {' "$REPO/lab/lib.sh" \
  && ok "and box_workers is there for them to read it with" \
  || bad "lab/lib.sh has no box_workers, so the line above is asserting nothing"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
