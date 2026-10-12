#!/bin/sh
# Attaching the CGNAT to one interface costs the same at 50,000 interfaces as at 1,000
# NO-OFF-RUN: a cost, not a feature that can be switched off; its control is the same sweep run against the plugin before #597, which this lab fails (docs/bng.md 3.4)
#
#   sh tools/lab.sh attach-scale                    1,000, 10,000 and 50,000
#   sh tools/lab.sh attach-scale 1000 10000         those sizes instead
#
# WHAT IT DEFENDS. A BNG attaches the CGNAT to every subscriber session as it
# comes up, so `set interface bpa in2out` runs once per session, under the
# barrier, every time an OLT reboots. #287 measured it at 8.3 ms per call on a
# box with 10,000 interfaces - 83 seconds to attach them all - because the
# attach walked every interface on the box to recompute "tcp-mss auto". #287
# removed that walk. #485 then put a second one on the same path: on every
# INSIDE attach, bpa_nat64_interfaces_update() walked the box again, asking
# the feature arc whether each interface was inside, to give each its IPv6
# half - on boxes with no NAT64 pool as well, since the walk ran before the
# question of whether there was one. #597 found it, and this lab is what a
# unit test cannot be: the attach as VPP runs it, at the sizes a BNG reaches.
#
# HOW. Dot1q sub-interfaces of loopbacks, made by make_interfaces in
# lab/lib.sh, which says why not loopbacks alone; the attach does not look at
# what kind of interface it is given. At each size N the newest BATCH of them
# are attached in one `vppctl exec`, so the clock measures the dataplane and
# not vppctl's start-up, then detached the same way. The statistics segment is set to 1 GB
# so that the interface count is limited by nothing but this lab's own sizes:
# lab/statseg-scale.sh is where the segment is the subject.
#
# THE ASSERTION IS A RATIO, NOT A TIME. A per-call figure is a fact about the
# machine, and this one runs the release lab suite beside it. A ratio between
# two sizes on the same dataplane, minutes apart, is a fact about the
# algorithm: on the plugin before #597 the cost at 50,000 was 13.1 times the
# cost at 1,000 (1,145 us to 15,068 us an attach), and 1.5 times after it;
# the bound here is three. Every figure is printed, for the
# documents, and every one of them is a ceiling for the reason docs/bng.md
# gives: the machine was busy.
#
# What "tcp-mss auto" resolves to is asserted at the largest size, attaching
# and detaching an interface whose MTU is the smallest on the box: the
# bookkeeping that replaced the walk has to give the walk's answer.
set -u
mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh
. /src/lab/verdict.sh

SCALES=${*:-1000 10000 50000}
BATCH=500
# The bound on cost(largest) / cost(smallest). A walk grows it towards N1/N0,
# fifty between 1,000 and 50,000, from below: about 1 ms of every attach does
# not depend on N at all (the configuration trail's fsync), so the walk
# measured 13.1x. A cost that does not depend on N gives about one - 1.5x
# measured. Three leaves room for a busy machine and none for a walk.
BOUND=3

# 1 GB of main heap: 50,000 interfaces took 352 MB of it, measured here.
# 4k pages, so the container is charged for what is touched.
sed -e 's/main-heap-size [0-9]*[MG]/main-heap-size 1G/' lab/startup-test.conf \
  > /tmp/startup-attach.conf
printf '\nstatseg {\n  size 1G\n}\n' >> /tmp/startup-attach.conf
vpp -c /tmp/startup-attach.conf > /tmp/vpp.log 2>&1 &
wait_vpp

now_us() { echo $(($(date +%s%N) / 1000)); }

# exec_timed <file>: runs a CLI script, prints the microseconds it took
exec_timed() {
  _t0=$(now_us)
  vppctl exec "$1" > /tmp/exec.out 2>&1
  _t1=$(now_us)
  echo $((_t1 - _t0))
}

# A pool clamping to "auto", so the attach has the bookkeeping to do.
vppctl bpa pool lab inside 100.64.0.0/16 outside 203.0.113.0/24 block 512 \
  >/dev/null
vppctl bpa tcp-mss lab auto >/dev/null 2>&1
vppctl show bpa pool | grep -q "pool lab" \
  || { echo "the pool was refused; nothing below would mean anything"; exit 1; }

HAVE=0
: > /tmp/names.txt
grow_to() {  # create interfaces until the box holds $1 of them
  : > /tmp/create.txt
  make_interfaces $(($1 - HAVE)) /tmp/create.txt /tmp/names.txt
  HAVE=$1
  _us=$(exec_timed /tmp/create.txt)
  echo "  created up to $1 interfaces ($((_us / 1000)) ms)"
}

echo "=========== 1. attach and detach cost, by interface count ==========="
printf '  %-10s %-14s %-14s\n' "interfaces" "attach/call" "detach/call"
FIRST_A=""; LAST_A=""; FIRST_D=""; LAST_D=""; LAST_N=""
for N in $SCALES; do
  grow_to "$N"
  # What the box holds, read back: a step that VPP refused part of would
  # otherwise be measured as the size it was asked to be.
  HELD=$(vppctl show interface | tr -d '\r' | grep -c "^ *loop")
  [ "$HELD" -ge "$N" ] || {
    echo "  [FAIL] asked for $N interfaces and the box holds $HELD"
    VERDICT_FAILURES=$((VERDICT_FAILURES + 1)); }
  # The newest BATCH, the ones created last.
  tail -n "$BATCH" /tmp/names.txt > /tmp/batch.txt
  sed 's/^/set interface bpa in2out /' /tmp/batch.txt > /tmp/attach.txt
  sed 's/^/set interface bpa in2out /; s/$/ del/' /tmp/batch.txt > /tmp/detach.txt
  LASTIF=$(tail -n 1 /tmp/names.txt)
  A=$(exec_timed /tmp/attach.txt)
  if grep -qi "error\|unknown" /tmp/exec.out; then
    echo "  the attach was refused at $N:"; head -3 /tmp/exec.out | sed 's/^/    /'
    VERDICT_FAILURES=$((VERDICT_FAILURES + 1))
  fi
  ON=$(vppctl show interface features "$LASTIF" 2>&1 | grep -c "bpa-in2out")
  D=$(exec_timed /tmp/detach.txt)
  OFF=$(vppctl show interface features "$LASTIF" 2>&1 | grep -c "bpa-in2out")
  printf '  %-10s %-14s %-14s\n' "$N" "$((A / BATCH)) us" "$((D / BATCH)) us"
  [ "$ON" -ge 1 ] && [ "$OFF" -eq 0 ] || {
    echo "  [FAIL] $LASTIF did not carry bpa-in2out while attached and"
    echo "         lose it when detached (attached: $ON, detached: $OFF)"
    VERDICT_FAILURES=$((VERDICT_FAILURES + 1)); }
  [ -z "$FIRST_A" ] && { FIRST_A=$A; FIRST_D=$D; FIRST_N=$N; }
  LAST_A=$A; LAST_D=$D; LAST_N=$N
done

echo "  main heap: $(vppctl show memory main-heap 2>&1 | tr -d '\r' | grep -m1 -o 'used: [^,]*'), statistics segment: $(vppctl show statistics segment /sys/vector_rate verbose 2>/dev/null | tr -d '\r' | grep -m1 -o 'used: [^,]*')"

echo
echo "=========== 2. the cost does not grow with the box ==========="
# Integer arithmetic in tenths, with a floor of 1 us a call on the base so a
# box too fast to read does not divide by zero.
base_a=$((FIRST_A > BATCH ? FIRST_A : BATCH)); base_d=$((FIRST_D > BATCH ? FIRST_D : BATCH))
ra=$((LAST_A * 10 / base_a)); rd=$((LAST_D * 10 / base_d))
echo "  attach at $LAST_N / attach at $FIRST_N: $((ra / 10)).$((ra % 10))x (bound ${BOUND}x)"
echo "  detach at $LAST_N / detach at $FIRST_N: $((rd / 10)).$((rd % 10))x (bound ${BOUND}x)"
expect "attaching at $LAST_N interfaces costs under ${BOUND}x what it costs at $FIRST_N" \
  test "$ra" -lt $((BOUND * 10))
expect "detaching at $LAST_N interfaces costs under ${BOUND}x what it costs at $FIRST_N" \
  test "$rd" -lt $((BOUND * 10))

echo
echo "=========== 3. a full sweep, the reconnect storm ==========="
# Ten thousand interfaces attached and then detached on the box holding all
# of them, which is the 83 seconds docs/bng.md measured before #287. Ten
# thousand and not every one, to keep the lab to a few minutes: the cost of
# one call is what sections 1 and 2 are about.
SWEEP=$((HAVE < 10000 ? HAVE : 10000))
head -n "$SWEEP" /tmp/names.txt | sed 's/^/set interface bpa in2out /' > /tmp/all-a.txt
head -n "$SWEEP" /tmp/names.txt | sed 's/^/set interface bpa in2out /; s/$/ del/' > /tmp/all-d.txt
SA=$(exec_timed /tmp/all-a.txt)
NIN=$(vppctl show interface features loop0.1 2>&1 | grep -c "bpa-in2out")
echo "  attaching $SWEEP of the $HAVE: $((SA / 1000)) ms, $((SA / SWEEP)) us a call"

echo
echo "=========== 4. tcp-mss auto still gives the walk's answer ==========="
# With ten thousand attached at the same MTU, one with a smaller MTU is
# attached and detached: the minimum has to follow it down and come back.
AUTO0=$(vppctl show bpa pool | tr -d '\r' | sed -n 's/.*tcp-mss *auto -> \([0-9]*\).*/\1/p')
vppctl create loopback interface instance 16000 > /dev/null
SMALL=loop16000
vppctl set interface mtu packet 1400 "$SMALL" >/dev/null 2>&1 \
  || vppctl set interface mtu 1400 "$SMALL" >/dev/null 2>&1
vppctl set interface bpa in2out "$SMALL"
AUTO1=$(vppctl show bpa pool | tr -d '\r' | sed -n 's/.*tcp-mss *auto -> \([0-9]*\).*/\1/p')
vppctl set interface bpa in2out "$SMALL" del
AUTO2=$(vppctl show bpa pool | tr -d '\r' | sed -n 's/.*tcp-mss *auto -> \([0-9]*\).*/\1/p')
echo "  auto with $SWEEP attached: ${AUTO0:-?}; with $SMALL at 1400 too: ${AUTO1:-?}; after it left: ${AUTO2:-?}"
expect "with $SWEEP attached, the clamp is their MTU less 40" \
  test -n "$AUTO0" -a "${AUTO0:-0}" -gt 1360
expect "attaching an interface at MTU 1400 lowers the clamp to 1360" \
  test "${AUTO1:-0}" = 1360
expect "detaching it gives the old clamp back" test "${AUTO2:-0}" = "${AUTO0:-x}"

SD=$(exec_timed /tmp/all-d.txt)
NOUT=$(vppctl show interface features loop0.1 2>&1 | grep -c "bpa-in2out")
echo "  detaching them: $((SD / 1000)) ms, $((SD / SWEEP)) us a call"
expect "loop0.1 carried bpa-in2out after the sweep attached it" test "$NIN" -ge 1
expect "and not after the sweep detached it" test "$NOUT" -eq 0

echo
echo "=========== 5. the IPv6 half follows the attach, one interface at a time ==========="
# A NAT64 pool puts bpa-nat64-in2out on every inside interface (#485). That
# used to be done by walking the box on every attach; it is now done for the
# interface being attached, and for all of them when the first NAT64 pool
# arrives or the last one goes - which has to still be true.
vppctl bpa pool v6 inside 2001:db8:1200::/112 subscriber-length 128 \
  nat64-prefix 64:ff9b::/96 outside 198.51.100.0/24 block 512 >/dev/null 2>&1
expect "the NAT64 pool was created" sh -c 'vppctl show bpa pool | grep -q "pool v6"'
vppctl set interface bpa in2out loop0.1
vppctl set interface bpa out2in loop0.2
F1=$(vppctl show interface features loop0.1 2>&1 | grep -c "bpa-nat64-in2out")
F2=$(vppctl show interface features loop0.2 2>&1 | grep -c "bpa-nat64-in2out")
vppctl set interface bpa in2out loop0.1 del
F1D=$(vppctl show interface features loop0.1 2>&1 | grep -c "bpa-nat64-in2out")
vppctl set interface bpa in2out loop0.3
vppctl bpa pool v6 del >/dev/null 2>&1
F3=$(vppctl show interface features loop0.3 2>&1 | grep -c "bpa-nat64-in2out")
vppctl bpa pool v6 inside 2001:db8:1200::/112 subscriber-length 128 \
  nat64-prefix 64:ff9b::/96 outside 198.51.100.0/24 block 512 >/dev/null 2>&1
F3B=$(vppctl show interface features loop0.3 2>&1 | grep -c "bpa-nat64-in2out")
expect "an inside interface attached under a NAT64 pool takes its IPv6 half" test "$F1" -ge 1
expect "an outside one does not" test "$F2" -eq 0
expect "detaching the inside interface takes the IPv6 half away" test "$F1D" -eq 0
expect "deleting the last NAT64 pool takes it off the inside interfaces" test "$F3" -eq 0
expect "a NAT64 pool created later gives it back to them" test "$F3B" -ge 1

echo
verdict
