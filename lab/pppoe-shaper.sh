#!/bin/sh
# A shaped PPPoE session costs nothing idle, and what it costs held at its plan (#610)
# NO-OFF-RUN: a measurement with bounds, not a feature switched off; lab/pppoe.sh is the shaper's on/off evidence, through real sessions
#
#   sh tools/lab.sh pppoe-shaper                 16,000 sessions, 2,000 busy
#   sh tools/lab.sh pppoe-shaper 40000 4000      those sizes instead
#   LAB_WORKERS=3 sh tools/lab.sh pppoe-shaper   the same with three workers
#
# WHAT IT MEASURES (docs/bng.md 0.9). The per-session shaper is a queue in
# the dataplane, and a queue is where a box goes wrong at scale: memory per
# session, a timer that walks every session, buffers that run out. So, on one
# dataplane:
#
#   1. the control plane's cost: shaping a session (`set bpa pppoe-shaper`,
#      what accel-ppp's hook sends at a session's start and at a CoA) timed
#      in batches at 1,000 and at the full size - the ratio is asserted, as
#      lab/policer-scale.sh asserts the policer's - and the main heap read
#      before and after, against the same sessions given a download policer;
#   2. what an IDLE shaped session costs: with every session shaped and no
#      traffic, the node that sends what waited is not run at all;
#   3. what a session HELD AT ITS PLAN costs: BUSY sessions offered twice
#      their plan by VPP's packet generator - each its own sub-interface, its
#      own queue - for 10 s; the plan delivered in all, the clocks a packet
#      of each node, the buffers held against the box's cap, and the same
#      sessions through policers;
#   4. the pass path: the same sessions offered half their plan, nothing
#      waits - the shaper's clocks a packet against the policer's.
#
# The sessions are sub-interfaces, not PPPoE sessions: the shaper does not
# look at what kind of interface it is on, and a PPPoE session's output is
# the midchain's ip4-output arc, where a sub-interface's is too. lab/pppoe.sh
# is the end-to-end evidence; this is what it cannot be, the sizes.
#
# Every timing is taken on a host other labs may be running on: the ratios
# and bounds are asserted, the clocks are printed.
set -u
mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh
. /src/lab/verdict.sh

SESSIONS=${1:-16000}
BUSY=${2:-2000}
BATCH=500
BOUND=3
# Each busy session's plan, and the packets it is offered: 256 kbit/s is 32
# packets of 1000 bytes a second, so 2,000 sessions are 64,000 a second
# delivered - every session due at the wheel about every 31 ms.
PLAN=256
SIZE=1000

sed -e 's/main-heap-size [0-9]*[MG]/main-heap-size 2G/' "$(startup_conf test)" \
  > /tmp/startup-shaper.conf
printf '\nstatseg {\n  size 1G\n}\n' >> /tmp/startup-shaper.conf
vpp -c /tmp/startup-shaper.conf > /tmp/vpp.log 2>&1 &
wait_vpp
W=$(box_workers)
echo "  $W worker(s)"

now_us() { echo $(($(date +%s%N) / 1000)); }
exec_timed() {
  _t0=$(now_us)
  vppctl exec "$1" > /tmp/exec.out 2>&1
  _t1=$(now_us)
  echo $((_t1 - _t0))
}
heap_kb() {  # the main heap's "used" in kB
  vppctl show memory main-heap 2>&1 | tr -d '\r' | grep -o 'used: [0-9.]*[KMG]' | head -1 \
    | awk '{v=$2; u=substr(v,length(v)); n=substr(v,1,length(v)-1);
            if (u=="G") n*=1048576; else if (u=="M") n*=1024; printf "%d\n", n}'
}
# err <node> <words>: the node's counter whose description starts so, summed
err() {
  vppctl show errors | tr -d '\r' | awk -v n="$1" -v w="$2" '
    $2 == n && index($0, w) { s += $1 } END { print s + 0 }'
}
# clocks <node>: its clocks a vector over every thread, from show runtime
# (Name State Calls Vectors Suspends Clocks Vectors/Call, a block a thread)
clocks() {
  vppctl show runtime | tr -d '\r' | awk -v n="$1" '
    $1 == n { v += $4; k += $6 * $4 }
    END { if (v) printf "%d\n", k / v; else print 0 }'
}
calls() {
  vppctl show runtime | tr -d '\r' | awk -v n="$1" '$1 == n { c += $3 } END { print c + 0 }'
}
shaped() { vppctl show bpa pppoe-shaper | tr -d '\r' | head -1; }

echo
echo "=========== 1. shaping a session, by how many the box holds ==========="
: > /tmp/create.txt; : > /tmp/names.txt
make_interfaces "$SESSIONS" /tmp/create.txt /tmp/names.txt
exec_timed /tmp/create.txt > /dev/null
H0=$(heap_kb)
awk '{print "set bpa pppoe-shaper " $1 " down 20000"}' /tmp/names.txt > /tmp/shape-all.txt
head -n 1000 /tmp/shape-all.txt > /tmp/shape-1k.txt
tail -n +1001 /tmp/shape-all.txt > /tmp/shape-rest.txt
exec_timed /tmp/shape-1k.txt > /dev/null
# the newest BATCH again, off then on: the cost of a session's end and start
tail -n "$BATCH" /tmp/shape-1k.txt > /tmp/b-on.txt
sed 's/ down 20000$/ down 0/' /tmp/b-on.txt > /tmp/b-off.txt
OFF1=$(exec_timed /tmp/b-off.txt); ON1=$(exec_timed /tmp/b-on.txt)
exec_timed /tmp/shape-rest.txt > /dev/null
tail -n "$BATCH" /tmp/shape-all.txt > /tmp/b-on.txt
sed 's/ down 20000$/ down 0/' /tmp/b-on.txt > /tmp/b-off.txt
OFFN=$(exec_timed /tmp/b-off.txt); ONN=$(exec_timed /tmp/b-on.txt)
if grep -qi "error\|unknown" /tmp/exec.out; then
  echo "  refused:"; head -3 /tmp/exec.out | sed 's/^/    /'
  VERDICT_FAILURES=$((VERDICT_FAILURES + 1))
fi
H1=$(heap_kb)
echo "  $(shaped)"
printf '  %-10s %-14s %-14s\n' "sessions" "shape/session" "remove/session"
printf '  %-10s %-14s %-14s\n' 1000 "$((ON1 / BATCH)) us" "$((OFF1 / BATCH)) us"
printf '  %-10s %-14s %-14s\n' "$SESSIONS" "$((ONN / BATCH)) us" "$((OFFN / BATCH)) us"
SH_B=$(( (H1 - H0) * 1024 / SESSIONS ))
echo "  main heap: ${H0} kB before, ${H1} kB with $SESSIONS shaped: $SH_B bytes a session"
base_on=$((ON1 > BATCH ? ON1 : BATCH)); base_off=$((OFF1 > BATCH ? OFF1 : BATCH))
ron=$((ONN * 10 / base_on)); roff=$((OFFN * 10 / base_off))
echo "  at $SESSIONS / at 1000: shape $((ron / 10)).$((ron % 10))x, remove $((roff / 10)).$((roff % 10))x (bound ${BOUND}x)"
expect "shaping a session at $SESSIONS costs under ${BOUND}x what it costs at 1000" \
  test "$ron" -lt $((BOUND * 10))
expect "and removing one" test "$roff" -lt $((BOUND * 10))
expect "every session is shaped" sh -c "vppctl show bpa pppoe-shaper | tr -d '\r' | head -1 | grep -q '^pppoe-shaper: $SESSIONS down'"
expect "a shaped session costs under 1 KiB of the main heap ($SH_B bytes)" \
  test "$SH_B" -lt 1024

echo
echo "=========== 2. $SESSIONS shaped sessions, idle ==========="
vppctl clear runtime > /dev/null
sleep 5
IDLE_CALLS=$(calls bpa-pppoe-shaper-tx)
echo "  bpa-pppoe-shaper-tx calls in 5 s with nothing queued: $IDLE_CALLS"
expect "an idle shaped session costs no node: the sender never ran" \
  test "$IDLE_CALLS" -eq 0

echo
echo "=========== 3. $BUSY sessions held at their plan, $PLAN kbit/s each ==========="
vppctl create packet-generator interface pg0 > /dev/null
vppctl create packet-generator interface pg1 > /dev/null
vppctl set interface state pg0 up
vppctl set interface state pg1 up
vppctl set interface ip address pg0 192.0.2.1/24
# one sub-interface of pg1 a session: a /30 each, a neighbour, and a /32 of
# 10.200/16 routed to it, so one stream's incrementing destination is BUSY
# sessions
awk -v n="$BUSY" 'BEGIN { for (k = 1; k <= n; k++) {
    a = int((k - 1) / 64); b = ((k - 1) % 64) * 4
    print "create sub-interfaces pg1 " k
    print "set interface state pg1." k " up"
    print "set interface ip address pg1." k " 10.64." a "." (b + 1) "/30"
    print "set ip neighbor pg1." k " 10.64." a "." (b + 2) " 02:00:00:00:00:02"
    print "ip route add 10.200." int((k - 1) / 256) "." ((k - 1) % 256) "/32 via 10.64." a "." (b + 2) " pg1." k
  } }' > /tmp/busy.txt
exec_timed /tmp/busy.txt > /dev/null
LAST="10.200.$(( (BUSY - 1) / 256 )).$(( (BUSY - 1) % 256 ))"
awk -v n="$BUSY" -v p="$PLAN" 'BEGIN { for (k = 1; k <= n; k++) print "set bpa pppoe-shaper pg1." k " down " p }' > /tmp/busy-shape.txt
awk -v n="$BUSY" 'BEGIN { for (k = 1; k <= n; k++) print "set bpa pppoe-shaper pg1." k " down 0" }' > /tmp/busy-unshape.txt
awk -v n="$BUSY" -v p="$PLAN" 'BEGIN { for (k = 1; k <= n; k++) {
    print "policer add name sp" k " type 1r2c cir " p " cb " p * 1000 / 16 " rate kbps conform-action transmit exceed-action drop"
    print "policer output name sp" k " pg1." k } }' > /tmp/busy-police.txt
awk -v n="$BUSY" 'BEGIN { for (k = 1; k <= n; k++) {
    print "policer output unapply name sp" k " pg1." k
    print "policer del name sp" k } }' > /tmp/busy-unpolice.txt
exec_timed /tmp/busy-shape.txt > /dev/null

# streams: <pps> - two on two workers when there are workers, so a
# session's packets arrive on both and one hands them to the other
PPS_PLAN=$(( BUSY * PLAN * 1000 / 8 / SIZE ))
streams() {
  vppctl packet-generator delete s0 > /dev/null 2>&1
  vppctl packet-generator delete s1 > /dev/null 2>&1
  if [ "$W" -ge 2 ]; then
    for i in 0 1; do
      vppctl packet-generator new "name s$i limit 0 rate $(( $1 / 2 )) node ip4-input \
        interface pg0 size $SIZE-$SIZE worker $i data { UDP: 192.0.2.2 -> 10.200.0.0 - $LAST \
        UDP: 1234 -> 5678 incrementing $((SIZE - 28)) }" > /dev/null
    done
  else
    vppctl packet-generator new "name s0 limit 0 rate $1 node ip4-input \
      interface pg0 size $SIZE-$SIZE data { UDP: 192.0.2.2 -> 10.200.0.0 - $LAST \
      UDP: 1234 -> 5678 incrementing $((SIZE - 28)) }" > /dev/null
  fi
}
# run <seconds>: the streams for that long, the counters cleared first
run() {
  vppctl clear errors > /dev/null; vppctl clear runtime > /dev/null
  vppctl clear interfaces > /dev/null
  vppctl packet-generator enable-stream > /dev/null
  sleep "$1"
  vppctl packet-generator disable-stream > /dev/null
  sleep 1
}
pg1_tx() {  # packets pg1's sub-interfaces sent: "tx packets <n>" in each
  vppctl show interface | tr -d '\r' | awk '/^pg1\./ {s = 1} /^[^ ]/ && !/^pg1\./ {s = 0}
    s { for (i = 1; i < NF - 1; i++) if ($i == "tx" && $(i + 1) == "packets") t += $(i + 2) }
    END {print t + 0}'
}

# WHAT A TURN OF THE SENDER COSTS WITH NOTHING DUE: one session parked at
# 8 kbit/s (1,000 bytes a second) with seven packets queued behind its
# bucket, for 5 s. The sender polls while its thread holds a queue - a turn
# with nothing due is a clock read and a compare - so its clocks a packet in
# the busy run below are mostly these turns; this is what one costs. (A
# sender woken by VPP's own timer wheel instead ran 20 times in these 5 s,
# and gave one TCP flow 7.8 of its 20 Mbit/s: bpa_pppoe_shaper.c says why.)
vppctl set bpa pppoe-shaper pg1.1 down 8 > /dev/null
_w=""; [ "$W" -ge 2 ] && _w="worker 0"
vppctl packet-generator new "name park limit 10 node ip4-input interface pg0 \
  size $SIZE-$SIZE $_w data { UDP: 192.0.2.2 -> 10.200.0.0 \
  UDP: 1234 -> 5678 incrementing $((SIZE - 28)) }" > /dev/null
vppctl packet-generator enable-stream park > /dev/null
sleep 1
vppctl clear runtime > /dev/null
sleep 5
PARK_CALLS=$(calls bpa-pppoe-shaper-tx)
PARK_CLK=$(vppctl show runtime | tr -d '\r' | awk '$1 == "bpa-pppoe-shaper-tx" { k += $6 * ($4 > 0 ? $4 : 1) } END { printf "%d\n", k }')
C_EMPTY=$(( PARK_CALLS > 0 ? PARK_CLK / PARK_CALLS : 0 ))
echo "  a session parked for 5 s, a packet due each second: $PARK_CALLS turns, about $C_EMPTY clocks each"
expect "a turn with nothing due costs under 400 clocks ($C_EMPTY)" test "$C_EMPTY" -lt 400
vppctl packet-generator delete park > /dev/null
vppctl set bpa pppoe-shaper pg1.1 down 0 > /dev/null
vppctl set bpa pppoe-shaper pg1.1 down $PLAN > /dev/null

streams $((PPS_PLAN * 2))
run 10
SENT_S=$(pg1_tx)
TX_CALLS=$(calls bpa-pppoe-shaper-tx)
TX_VEC=$(vppctl show runtime | tr -d '\r' | awk '$1 == "bpa-pppoe-shaper-tx" { v += $4 } END { print v + 0 }')
MBIT_S=$(( SENT_S * SIZE * 8 / 10 / 1000000 ))
PLAN_MBIT=$(( BUSY * PLAN / 1000 ))
C_DOWN=$(clocks bpa-pppoe-shaper-down); C_TX=$(clocks bpa-pppoe-shaper-tx)

C_OWN=$(clocks bpa-pppoe-shaper-down-owner)
HELD=$(vppctl show bpa pppoe-shaper | tr -d '\r' | head -1)
QFULL=$(err bpa-pppoe-shaper-down "packets dropped, the session's queue")
CODEL=$(err bpa-pppoe-shaper-down "queued packets dropped by CoDel")
BOXD=$(err bpa-pppoe-shaper-down "packets dropped, the box's")
HAND=$(err bpa-pppoe-shaper-down "packets handed")
echo "  offered $((PPS_PLAN * 2)) packets/s ($((PLAN_MBIT * 2)) Mbit/s), plan $PLAN_MBIT Mbit/s in all"
echo "  delivered $MBIT_S Mbit/s ($SENT_S packets in 10 s)"
echo "  clocks a packet: bpa-pppoe-shaper-down $C_DOWN, -down-owner $C_OWN, -tx $C_TX"
C_TX_NET=$(( TX_VEC > 0 ? (C_TX * TX_VEC - TX_CALLS * C_EMPTY) / TX_VEC : 0 ))
[ "$C_TX_NET" -lt 0 ] && C_TX_NET=0
echo "  the sender: $TX_CALLS turns for $TX_VEC packets; less its turns at the empty turn's cost, about $C_TX_NET clocks a packet"
echo "  dropped: queue full $QFULL, CoDel $CODEL, box's buffers $BOXD; handed to the owner $HAND"
echo "  just after: $HELD"
vppctl show bpa pppoe-shaper pg1.1 | tr -d '\r' | sed 's/^/    /'
expect "the $BUSY sessions together got their plan, within 10% ($MBIT_S of $PLAN_MBIT Mbit/s)" \
  python3 -c "import sys; sys.exit(not (0.9 * $PLAN_MBIT <= $MBIT_S <= 1.05 * $PLAN_MBIT))"
CAP=$(echo "$HELD" | sed -n 's/.*, \([0-9]*\) a thread at most.*/\1/p')
expect "the queues never held more than the box's share (cap ${CAP:-?} a thread)" \
  sh -c "vppctl show bpa pppoe-shaper | tr -d '\r' | head -1 | awk '{for (i=1;i<=NF;i++) if (\$i==\"held\") h=\$(i+1)} END {exit !(h+0 <= ${CAP:-0} * ($W > 0 ? $W : 1))}'"
sleep 2
expect "and drained: no session holds a queue once the traffic stops" \
  sh -c "vppctl show bpa pppoe-shaper | tr -d '\r' | head -1 | grep -q ', 0 with a queue, buffers held 0,'"

echo
echo "  the same $BUSY sessions through a download policer each (cb half a second, the product's):"
exec_timed /tmp/busy-unshape.txt > /dev/null
exec_timed /tmp/busy-police.txt > /dev/null
run 10
SENT_P=$(pg1_tx)
MBIT_P=$(( SENT_P * SIZE * 8 / 10 / 1000000 ))
C_POL=$(clocks policer-output)
echo "  delivered $MBIT_P Mbit/s; clocks a packet: policer-output $C_POL"

echo
echo "=========== 4. the pass path: half the plan offered, nothing waits ==========="
streams $((PPS_PLAN / 2))
run 10
C_POL_PASS=$(clocks policer-output)
exec_timed /tmp/busy-unpolice.txt > /dev/null
exec_timed /tmp/busy-shape.txt > /dev/null
run 10
C_PASS=$(clocks bpa-pppoe-shaper-down)
Q_PASS=$(err bpa-pppoe-shaper-down "packets past the plan, queued")
P_PASS=$(err bpa-pppoe-shaper-down "packets within the plan")
echo "  clocks a packet: bpa-pppoe-shaper-down $C_PASS, policer-output $C_POL_PASS"
echo "  passed at once $P_PASS, queued $Q_PASS"
expect "under the plan nearly nothing waits (queued $Q_PASS of $((P_PASS + Q_PASS)))" \
  test "$Q_PASS" -le $(( (P_PASS + Q_PASS) / 20 ))

echo
echo "=========== 5. every session ends: nothing left behind ==========="
vppctl packet-generator disable-stream > /dev/null 2>&1
sed 's/ down 20000$/ down 0/' /tmp/shape-all.txt > /tmp/unshape-all.txt
exec_timed /tmp/busy-unshape.txt > /dev/null
SD=$(exec_timed /tmp/unshape-all.txt)
echo "  $SESSIONS + $BUSY shapers removed, the last $SESSIONS in $((SD / 1000)) ms"
echo "  $(shaped)"
expect "no session is shaped" sh -c "vppctl show bpa pppoe-shaper | tr -d '\r' | head -1 | grep -q '^pppoe-shaper: 0 down 0 up shaped, 0 with a queue, buffers held 0'"
expect "and the last interface carries no shaper" \
  sh -c "! vppctl show interface features $(tail -n 1 /tmp/names.txt) | grep -q shaper"
expect "the dataplane is alive" sh -c "vppctl show version | grep -q vpp"

echo
echo "SUMMARY: workers $W; shape ${ON1}/${ONN} us a batch of $BATCH at 1000/$SESSIONS; $SH_B bytes a session; idle sender calls $IDLE_CALLS; busy $BUSY x $PLAN kbit/s: $MBIT_S of $PLAN_MBIT Mbit/s (policer $MBIT_P), clocks down/owner/tx $C_DOWN/$C_OWN/$C_TX (net of empty turns $C_TX_NET, an empty turn $C_EMPTY) vs policer $C_POL; pass $C_PASS vs policer $C_POL_PASS"
verdict
