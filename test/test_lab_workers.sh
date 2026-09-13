#!/bin/sh
# The machinery in lab/lib.sh that every lab depends on and that no container
# is needed to check: how a lab decides whether the box it starts has workers,
# and whether it checks that the box started at all.
#
# Both halves are the same defect twice. A lab asked for something, did not
# read the answer, and measured whatever it got.
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

echo
echo "=== and no lab starts VPP without checking that it arrived ==="
# The other thing lab/lib.sh knows on every lab's behalf, and the one with a
# reader on the far end of it. tools/run-all-labs.sh classifies a capture by
# grepping it for "VPP did not start": a lab that prints it is counted NO VPP,
# which tells whoever reads the run to look at the machine. A lab that does not
# print it walks on with no dataplane, every vppctl is refused, and it reaches
# its verdict with every value empty - which the suite reports as FAIL, sending
# that reader to the product instead.
#
# Measured before this was written (#261): lab/frag.sh under a startup.conf VPP
# refuses ended "LAB FAILED: 14 check(s)", and lab_no_vpp() run verbatim over
# that capture agreed it was a product failure. Ten labs were in that state.
#
# What is asserted is the GUARANTEE and not the spelling. Forty start sites
# print the line themselves and ten now reach wait_vpp, and either is a guarded
# start; converting the forty would move the input of every lab in the suite to
# change no lab's verdict. A start site is unguarded when neither appears
# between it and the next one.
#
# Comment lines are dropped first, for the reason the block above drops them:
# lab/lib.sh explains wait_vpp with a worked example of the very line this
# looks for, and a check that went red on its own documentation would be the
# worst kind.
unguarded=$(awk '
  function flush() {
    if (pending) out = out sprintf("    %s line %d\n", pendfile, pendline)
    pending = 0
  }
  FNR == 1 { flush() }
  { line = $0; sub(/^[ \t]*/, "", line) }
  line ~ /^#/ { next }
  # pkill -f "vpp -c" is how a lab STOPS one, and snmp, api and throughput each
  # do it on purpose to assert what happens next.
  line ~ /vpp -c/ && line !~ /pkill/ {
    flush()
    pending = 1; pendfile = FILENAME; pendline = FNR
    next
  }
  pending && (line ~ /wait_vpp/ || line ~ /VPP did not start/) { pending = 0 }
  END { flush(); printf "%s", out }
' "$REPO"/lab/*.sh)
if [ -z "$unguarded" ]; then
  ok "every lab that starts VPP guards the start"
else
  bad "these start VPP and carry on whether or not it came up, so the suite
  reads a machine that never started a dataplane as a defect in the product:
$unguarded"
fi

# No container. wait_vpp is called directly with a vppctl on PATH that answers
# or refuses on demand, and LAB_VPP_WAIT standing in for the thirty seconds -
# the arrangement LAB_SRC already has above, and for the same reason: a rule
# about what happens when a wait runs out should not need a box and half a
# minute to check.
VB=$(mktemp -d)
trap 'rm -rf "$WORK" "$VB"' EXIT
mkdir -p "$VB/bin"
cat > "$VB/bin/vppctl" <<'STUB'
#!/bin/sh
[ -f "$VB/answering" ] && exit 0
exit 1
STUB
chmod +x "$VB/bin/vppctl"
printf 'unix_config: unknown input `this-option-does-not-exist cli...%s\nvpp exited\n' "'" \
  > "$VB/vpp.log"

run_wait_vpp() {  # <answering|gone> -> what wait_vpp prints, and its status
  rm -f "$VB/answering"
  [ "$1" = answering ] && : > "$VB/answering"
  VB=$VB PATH="$VB/bin:$PATH" LAB_VPP_WAIT=1 sh -c \
    ". $REPO/lab/lib.sh
     wait_vpp $VB/vpp.log
     echo REACHED-THE-LAB" 2>&1
  printf 'status=%s\n' "$?"
}

got=$(run_wait_vpp gone)
case "$got" in
  *"VPP did not start"*)
    ok "a wait that runs out prints the line the suite classifies on" ;;
  *) bad "the wait ran out and said nothing the suite can read; got:
$got" ;;
esac
case "$got" in
  *"unknown input"*)
    ok "and VPP's own account of why comes with it" ;;
  *) bad "the log was there and never reached the reader; got:
$got" ;;
esac
# It has to STOP the lab, not merely report. A lab that goes on without a
# dataplane spends its run building red checks out of empty strings, which is
# the report this whole block exists to prevent.
case "$got" in
  *REACHED-THE-LAB*) bad "the lab carried on with no dataplane under it; got:
$got" ;;
  *"status=0"*) bad "a lab with no dataplane exited zero; got:
$got" ;;
  *) ok "and it stops the lab there rather than letting it build a verdict" ;;
esac

got=$(run_wait_vpp answering)
case "$got" in
  *REACHED-THE-LAB*"status=0"*)
    ok "and a dataplane that did come up is waited for and nothing more" ;;
  *) bad "wait_vpp did not return to a lab whose VPP is answering; got:
$got" ;;
esac

# The coupling, read off the message rather than retyped here. wait_vpp's line
# is only worth printing because tools/run-all-labs.sh looks for that exact
# string; rewording either half alone leaves the labs saying something the
# suite does not classify, and nothing would notice.
marker=$(printf '%s\n' "$(run_wait_vpp gone)" | grep -m1 'VPP did not')
if grep -q "grep -q \"$marker\"" "$REPO/tools/run-all-labs.sh"; then
  ok "and that line is the one tools/run-all-labs.sh classifies NO VPP on"
else
  bad "the labs print '$marker' and the suite's classifier does not look for it"
fi

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
