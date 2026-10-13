#!/bin/sh
# A session's rate limit costs the same at 10,000 limited sessions as at 1,000
# NO-OFF-RUN: a cost, not a feature that can be switched off; its control is the ratio itself, which a policer table walked per call would push past the bound
#
#   sh tools/lab.sh policer-scale                 1,000 and 10,000
#   sh tools/lab.sh policer-scale 1000 20000      those sizes instead
#
# WHAT IT DEFENDS (#607). A rate-limited PPPoE session is two VPP policers,
# one on the session interface's input (upload) and one on its output
# (download): accel-ppp-ng's VPP hooks (session_hooks/vyos_vpp/vpppolicer.c)
# call policer_add_del twice and policer_input and policer_output once each
# when the session comes up, and the reverse when it ends - so every session
# setup at an OLT reboot pays them, on a box already holding a policer pair
# for every other subscriber. lab/pppoe.sh proves the policers do what they
# say; this lab is what it cannot be: the cost at the sizes a BNG reaches.
#
# HOW. Dot1q sub-interfaces of loopbacks stand in for the sessions, made by
# make_interfaces in lab/lib.sh; the policer does not look at what kind of
# interface it is given. At each size N, every interface up to N has its
# pair; then the newest BATCH get theirs again in one `vppctl exec`, so the
# clock measures the dataplane and not vppctl's start-up - the same four
# operations the hooks send, by the CLI that calls the same functions
# (policer_add, policer_input). Then they are taken off the same way.
#
# THE ASSERTION IS A RATIO, NOT A TIME, for attach-scale's reason: a per-call
# figure is a fact about a busy machine, a ratio between two sizes minutes
# apart on one dataplane is a fact about the algorithm. And the policers are
# all gone at the end: VPP's count back to where it started.
set -u
mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh
. /src/lab/verdict.sh

SCALES=${*:-1000 10000}
BATCH=500
BOUND=3

sed -e 's/main-heap-size [0-9]*[MG]/main-heap-size 1G/' lab/startup-test.conf \
  > /tmp/startup-policer.conf
printf '\nstatseg {\n  size 1G\n}\n' >> /tmp/startup-policer.conf
vpp -c /tmp/startup-policer.conf > /tmp/vpp.log 2>&1 &
wait_vpp

now_us() { echo $(($(date +%s%N) / 1000)); }
# exec_timed <file>: runs a CLI script, prints the microseconds it took
exec_timed() {
  _t0=$(now_us)
  vppctl exec "$1" > /tmp/exec.out 2>&1
  _t1=$(now_us)
  echo $((_t1 - _t0))
}
# pairs <names> <out> - each interface's pair added and applied, as a session's
pairs() {
  awk '{ n = $1; gsub(/[.\/]/, "_", n)
         print "policer add name d_" n " type 1r2c cir 20000 cb 2000000 rate kbps conform-action transmit exceed-action drop"
         print "policer output name d_" n " " $1
         print "policer add name u_" n " type 1r2c cir 10000 cb 1000000 rate kbps conform-action transmit exceed-action drop"
         print "policer input name u_" n " " $1 }' "$1" > "$2"
}
# unpairs <names> <out> - and taken off, as a session's end does
unpairs() {
  awk '{ n = $1; gsub(/[.\/]/, "_", n)
         print "policer output unapply name d_" n " " $1
         print "policer del name d_" n
         print "policer input unapply name u_" n " " $1
         print "policer del name u_" n }' "$1" > "$2"
}
pols() { vppctl show policer pools | tr -d '\r' | sed -n 's/.*policers=\([0-9]*\).*/\1/p'; }

P0=$(pols)
HAVE=0
: > /tmp/names.txt
echo "=========== 1. a session's pair, by how many the box holds ==========="
printf '  %-10s %-16s %-16s\n' "sessions" "up/session" "down/session"
FIRST_U=""; LAST_U=""; FIRST_D=""; LAST_D=""
for N in $SCALES; do
  : > /tmp/create.txt; : > /tmp/new.txt
  make_interfaces $((N - HAVE)) /tmp/create.txt /tmp/new.txt
  exec_timed /tmp/create.txt > /dev/null
  pairs /tmp/new.txt /tmp/fill.txt
  exec_timed /tmp/fill.txt > /dev/null
  cat /tmp/new.txt >> /tmp/names.txt
  HAVE=$N
  HELD=$(pols)
  [ "$HELD" -ge $((2 * N)) ] || {
    echo "  [FAIL] asked for $((2 * N)) policers and VPP holds $HELD"
    VERDICT_FAILURES=$((VERDICT_FAILURES + 1)); }
  tail -n "$BATCH" /tmp/names.txt > /tmp/batch.txt
  unpairs /tmp/batch.txt /tmp/down.txt
  pairs /tmp/batch.txt /tmp/up.txt
  D=$(exec_timed /tmp/down.txt)
  U=$(exec_timed /tmp/up.txt)
  if grep -qi "error\|unknown" /tmp/exec.out; then
    echo "  refused at $N:"; head -3 /tmp/exec.out | sed 's/^/    /'
    VERDICT_FAILURES=$((VERDICT_FAILURES + 1))
  fi
  LASTIF=$(tail -n 1 /tmp/names.txt)
  FEAT=$(vppctl show interface features "$LASTIF" 2>&1 | tr -d '\r')
  printf '  %-10s %-16s %-16s\n' "$N" "$((U / BATCH)) us" "$((D / BATCH)) us"
  case "$FEAT" in *policer-input*policer-output*|*policer-output*policer-input*) ;;
    *) echo "  [FAIL] $LASTIF does not carry both policers"
       VERDICT_FAILURES=$((VERDICT_FAILURES + 1)) ;;
  esac
  [ -z "$FIRST_U" ] && { FIRST_U=$U; FIRST_D=$D; FIRST_N=$N; }
  LAST_U=$U; LAST_D=$D; LAST_N=$N
done
echo "  main heap: $(vppctl show memory main-heap 2>&1 | tr -d '\r' | grep -m1 -o 'used: [^,]*') with $(pols) policers"

echo
echo "=========== 2. the cost does not grow with the box ==========="
base_u=$((FIRST_U > BATCH ? FIRST_U : BATCH)); base_d=$((FIRST_D > BATCH ? FIRST_D : BATCH))
ru=$((LAST_U * 10 / base_u)); rd=$((LAST_D * 10 / base_d))
echo "  up at $LAST_N / up at $FIRST_N: $((ru / 10)).$((ru % 10))x (bound ${BOUND}x)"
echo "  down at $LAST_N / down at $FIRST_N: $((rd / 10)).$((rd % 10))x (bound ${BOUND}x)"
expect "a session's policers at $LAST_N cost under ${BOUND}x what they cost at $FIRST_N" \
  test "$ru" -lt $((BOUND * 10))
expect "taking them off at $LAST_N costs under ${BOUND}x what it costs at $FIRST_N" \
  test "$rd" -lt $((BOUND * 10))

echo
echo "=========== 3. every session ends: nothing left behind ==========="
unpairs /tmp/names.txt /tmp/all-down.txt
SD=$(exec_timed /tmp/all-down.txt)
P1=$(pols)
echo "  $HAVE sessions' policers taken off in $((SD / 1000)) ms; VPP holds $P1 (it held $P0 before)"
expect "VPP's policer count is back where it started" test "$P1" = "$P0"
expect "and the last interface carries no policer" \
  sh -c "! vppctl show interface features $(tail -n 1 /tmp/names.txt) | grep -q policer"

verdict
