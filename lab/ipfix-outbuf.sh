#!/bin/sh
# A box stopped while the TCP exporter still had bytes of its own to send.
#
#   sh tools/lab.sh ipfix-outbuf           -> LAB PASSED
#   sh tools/lab.sh ipfix-outbuf no-stall  -> LAB FAILED, exactly 1 check
#
# THE QUESTION. The TCP exporter's socket is non-blocking and its spill to disk
# only triggers above 1 MB, so between "the collector stopped reading" and
# "1 MB has piled up" the records live in one place only: bpa_log_tcp.outbuf,
# in the VPP process. The stop drain writes the natEvent 17 for every block
# still held into that same buffer. If the process then leaves without doing
# anything else with it, those bytes go with it - and in dynamic mode the 17
# that is lost is the line saying when the subscriber stopped holding the
# public ports (#219).
#
# WHY THE COLLECTOR HAS TO STALL RATHER THAN DIE. A collector that is gone
# makes send() fail and the exporter disconnect, and from there the spill file
# already covers it. A collector that is CONNECTED and not reading closes the
# receive window instead: send() returns EAGAIN, the exporter keeps the bytes,
# and nothing on the box says they are at risk.
#
# THE TWO PIECES, IN ONE FILE FOR THE FIRST TIME. lab/ipfix-tcp.sh knows how to
# take the collector away from a running exporter; lab/ipfix-stop.sh knows how
# to stop the box with a block still held. Neither on its own can see this:
# ipfix-tcp.sh never stops the box, so its buffer is always drained in the end,
# and ipfix-stop.sh's collector reads as fast as the box writes, so its outbuf
# is empty at the stop.
#
# THE STALL IS HOW THE CONDITION IS MADE ON PURPOSE, NOT THE ONLY WAY IT
# HAPPENS. The "no-stall" run, with a collector reading as fast as the box
# writes, measured 0 bytes pending before the stop and 70020 bytes still in the
# buffer at the exit - because the drain writes one record per block held, 4001
# of them there, and services the socket once. So a box with enough subscribers
# reaches this with nothing wrong at the collector at all. The stall is here
# because it makes the buffer measurable BEFORE the stop, which is what lets
# this file assert its own precondition instead of assuming it.
#
# WHY THE BOX COMES BACK UP AT THE END. "Not lost" is the requirement, not
# "arrives before the stop": a release that was written to the spill file and
# replayed on the next connection closes the allocation in the retention log
# just as well, only later. So the verdict is taken after a second boot, with
# the same collector still listening, which is also the only way to tell a
# record that was spilled from one that was dropped.
#
# NOTHING HERE TYPES THE STOP DRAIN. The command an appliance runs before the
# signal is read out of the shipped drop-in, appliance/systemd/vpp-stop-drain.conf,
# and run as it is written there. A lab that performed the product's own step
# is the shape of #99, #168, #182, #184, #209 and #215.
set -u
. /src/lab/lib.sh   # startup_conf, use_binary_api

# "no-stall" runs everything below with the collector reading throughout. The
# exporter's buffer is then empty when the box stops, the release arrives on
# the spot, and the PRECONDITION assertion - that there were bytes of the
# exporter's own still to send - is the one that must go red. It is here so
# that a lab which quietly stopped producing its own condition cannot report a
# pass for a box it never put under the condition.
MODE=${1:-stall}
HX="python3 /src/src/hexash/hexash.py"
COLLECTOR=/src/tools/ipfix_collector.py
STALL_FILE=/tmp/ipfix-stall
SPILL=/var/log/hexarouting/ipfix-spill.bin

mkdir -p /run/vpp /vpp_plugins /var/log/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api

# A spill left by an earlier run would be replayed into this one and counted as
# a record this box exported.
rm -f "$SPILL" "$STALL_FILE"

ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up
ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out
PEERMAC=$(ip netns exec peer cat /sys/class/net/veth-out/address)

# The subscriber the verdict is about. Its block is taken while the collector
# is still reading, so its ALLOCATION is on the collector's disk before
# anything is stalled - and a missing release below cannot be a missing
# allocation.
SUB=5
# Small blocks, so that each of the churn subscribers below gets a block of its
# own: block events are per BLOCK, and a lab that gave three thousand
# subscribers one block between them would produce one record.
BLOCK=64
# A /20 inside, because the number of records needed is a property of the
# kernel and not of this lab: the receive window can be pinned small from the
# collector, but the exporter's own send buffer grows on the first ACK - 45 kB
# went into it on this machine before send() first refused. Four thousand
# subscribers is room to pass that on a kernel that buffers more.
INSIDE=100.64.0.0/20
PG0=100.64.15.254/20

boot_vpp() {
  vpp -c "$(startup_conf lcp)" > "/tmp/vpp-$1.log" 2>&1 &
  # This shell's own child, not pgrep's answer: the container has no pgrep, and
  # lab/ipfix-stop.sh records what taking its empty output for "already gone"
  # cost.
  VPP_PID=$!
  n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do
    sleep 1; n=$((n+1))
  done
  vppctl show version >/dev/null 2>&1 || {
    echo "VPP did not start"; tail -20 "/tmp/vpp-$1.log"; exit 1; }

  vppctl create host-interface name veth-vpp >/dev/null
  vppctl set interface state host-veth-vpp up
  # The TCP exporter writes to a KERNEL socket, so the collector has to be
  # reachable through the Linux stack; linux_cp mirrors the interface as a TAP,
  # which is how a real box has that path.
  vppctl lcp create host-veth-vpp host-if vppmgmt0 >/dev/null
  vppctl set interface mtu 1500 host-veth-vpp
  vppctl set interface ip address host-veth-vpp 10.0.0.1/24
  # Resolved beforehand: with ARP still incomplete the first records are
  # dropped, and for a compliance log that is silent loss.
  vppctl set ip neighbor host-veth-vpp 10.0.0.2 $PEERMAC

  vppctl create packet-generator interface pg0 >/dev/null
  vppctl create packet-generator interface pg1 >/dev/null
  vppctl set interface state pg0 up
  vppctl set interface state pg1 up
  vppctl set interface ip address pg0 $PG0
  vppctl set interface ip address pg1 200.150.0.1/24
  vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
  vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1

  vppctl bpa pool din inside $INSIDE outside 200.150.1.0/24 \
      block $BLOCK mode dynamic
  vppctl set interface bpa in2out pg0
  vppctl set interface bpa out2in pg1
  # Nothing may come back on its own. A block released by the ordinary expiry
  # path would put a natEvent 17 in the log that the stop drain did not write,
  # and every assertion below would then be about the wrong path.
  vppctl bpa timeouts udp 3600
  vppctl bpa block-holddown 3600

  $HX -c "configure" \
      -c "cgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739" \
      -c "cgnat logging tcp 10.0.0.2 port 4740" \
      -c "cgnat logging enable" \
      -c "commit" > "/tmp/vty-$1.log" 2>&1
  sed 's/^/  /' "/tmp/vty-$1.log"
}

# One block, held by one subscriber, by traffic rather than by configuration.
hold_block() {
  vppctl packet-generator new "name h$1 limit 1 node ip4-input interface pg0 \
      size 60-60 data { UDP: 100.64.0.$1 -> 8.8.8.8 \
                        UDP: 40000 -> 53 incrementing 8 }" >/dev/null
  vppctl packet-generator enable >/dev/null
  sleep 1
  vppctl packet-generator delete "h$1" >/dev/null 2>&1
}

# churn <first> <count>: <count> subscribers, one packet each, each taking a
# block of its own - which is <count> natEvent 16 for the exporter to write.
#
# One stream per subscriber, built as a file and fed to vppctl in one go, the
# way lab/pool-del.sh does it: five hundred separate vppctl invocations is half
# a minute of process starts.
churn() {
  rm -f /tmp/pg-churn.txt /tmp/pg-churn-del.txt
  _i=$1
  _end=$(( $1 + $2 ))
  while [ "$_i" -lt "$_end" ]; do
    _a=$(( _i / 256 )); _b=$(( _i % 256 ))
    echo "packet-generator new { name c$_i limit 1 node ip4-input \
      interface pg0 size 60-60 data { UDP: 100.64.$_a.$_b -> 8.8.8.8 \
      UDP: 40000 -> 53 incrementing 8 } }" >> /tmp/pg-churn.txt
    echo "packet-generator delete c$_i" >> /tmp/pg-churn-del.txt
    _i=$(( _i + 1 ))
  done
  vppctl exec /tmp/pg-churn.txt > /dev/null
  vppctl packet-generator enable > /dev/null
  sleep 3
  vppctl exec /tmp/pg-churn-del.txt > /dev/null 2>&1
}

# The TCP exporter's own numbers, each read on its own so that the UDP section
# above it cannot answer for it: BOTH sections print "records N emitted".
tcp_stat() {
  vppctl show bpa logging | tr -d '\r' | sed -n '/^TCP transport/,$p' \
    | sed -n "s/$1/\1/p" | head -1
}
tcp_pending() { tcp_stat '.*, \([0-9][0-9]*\) pending.*'; }
tcp_sent()    { tcp_stat '.*emitted, \([0-9][0-9]*\) bytes sent.*'; }
tcp_spilled() { tcp_stat '.*spill *\([0-9][0-9]*\) bytes on disk.*'; }
tcp_replayed(){ tcp_stat '.*on disk, \([0-9][0-9]*\) replayed.*'; }

udp_records() {
  vppctl show bpa logging | tr -d '\r' \
    | sed -n '/^IPFIX export/,/^TCP transport/p' \
    | sed -n 's/.*records *\([0-9][0-9]*\) emitted.*/\1/p' | head -1
}

blocks_in_use() {
  vppctl show bpa blocks | tr -d '\r' \
    | sed -n 's/.*total: \([0-9][0-9]*\)\/[0-9]* in use.*/\1/p' | head -1
}

# How the collector prints one event. The natEvent name is padded to eleven
# columns, so "ALLOCATION 100.64.0.5" matches nothing at all - a grep written
# that way waited sixty seconds for a record that was already on disk.
ev_re() { printf '  *%s +%s -> ' "$1" "$2"; }

# Waits until the collector's own file says it decoded the allocation. The
# collector is the oracle here and not the box: "the box emitted a record" and
# "a collector read one" are the two different claims this whole file is about.
wait_collected() {
  _n=0
  until grep -qE "$1" /tmp/collector-tcp.out 2>/dev/null || [ "$_n" -ge 60 ]; do
    sleep 1; _n=$((_n+1))
  done
  grep -qE "$1" /tmp/collector-tcp.out 2>/dev/null \
    && echo "  the collector decoded it after ${_n}s" \
    || echo "  the collector never decoded it (${_n}s)"
}

# The stop, as an appliance performs it: what the shipped drop-in puts in
# ExecStop, then the signal systemd sends afterwards. Read out of the file
# rather than retyped here.
EXECSTOP=$(sed -n 's/^ExecStop=-\{0,1\}//p' \
           /src/appliance/systemd/vpp-stop-drain.conf)

stop_vpp() {
  PID=$VPP_PID
  [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null || { echo "  no vpp to stop"; exit 1; }
  echo "  ExecStop: ${EXECSTOP:-(none in the drop-in)}"
  [ -n "$EXECSTOP" ] && $EXECSTOP 2>&1 | sed 's/^/    /'
  echo "  SIGTERM -> vpp pid $PID"
  kill -TERM "$PID" 2>/dev/null
  n=0; while kill -0 "$PID" 2>/dev/null && [ "$n" -lt 30 ]; do sleep 1; n=$((n+1)); done
  kill -0 "$PID" 2>/dev/null && { echo "  still alive after ${n}s, SIGKILL"; kill -9 "$PID"; }
  echo "  gone after ${n}s"
  rm -f /dev/shm/db /dev/shm/global_vm /dev/shm/vpe-api
  sleep 1
}

# -u on the TCP collector because this lab READS its file while it runs, to
# know the allocation arrived before anything is stalled.
ip netns exec peer python3 "$COLLECTOR" 4739 300 > /tmp/collector.out 2>&1 &
COL=$!
if [ "$MODE" = no-stall ]; then
  ip netns exec peer python3 -u "$COLLECTOR" tcp 4740 300 \
      > /tmp/collector-tcp.out 2>&1 &
else
  ip netns exec peer python3 -u "$COLLECTOR" tcp 4740 300 "$STALL_FILE" \
      > /tmp/collector-tcp.out 2>&1 &
fi
COLT=$!
sleep 2

echo
echo "=== boot 1, and a block held by 100.64.0.$SUB with the collector reading ==="
boot_vpp 1
hold_block $SUB
wait_collected "$(ev_re ALLOCATION "100.64.0.$SUB")"

echo
echo "=== the collector stops reading (mode: $MODE) ==="
if [ "$MODE" = no-stall ]; then
  echo "  not stalling: the collector reads throughout"
else
  : > "$STALL_FILE"
  sleep 2
fi
grep -E "stalling while|STALLED" /tmp/collector-tcp.out | tail -2 | sed 's/^/  /'

echo
echo "=== block events, until the exporter has bytes it cannot hand over ==="
# In waves, stopping as soon as the buffer is not empty: how many records it
# takes is a property of the kernel's buffers, and a fixed number would be too
# few on one machine and a minute wasted on another.
WAVE=0
NEXT=16
until [ "${PENDING:-0}" -gt 0 ] || [ "$WAVE" -ge 8 ]; do
  churn "$NEXT" 500
  NEXT=$(( NEXT + 500 ))
  WAVE=$(( WAVE + 1 ))
  sleep 3
  PENDING=$(tcp_pending)
  echo "  after wave $WAVE: $(blocks_in_use) blocks in use, \
${PENDING:-?} bytes pending, $(tcp_sent) bytes accepted by the socket"
done

PENDING_BEFORE_STOP=$(tcp_pending)
SPILL_BEFORE_STOP=$(tcp_spilled)
BLOCKS_AT_STOP=$(blocks_in_use)
UDP_BEFORE_STOP=$(udp_records)
echo "  held at the stop:         ${BLOCKS_AT_STOP:-?} block(s)"
echo "  in the exporter's buffer: ${PENDING_BEFORE_STOP:-?} byte(s)"
echo "  already spilled to disk:  ${SPILL_BEFORE_STOP:-?} byte(s)"

echo
echo "=== STOPPING the box with those bytes still in the exporter ==="
stop_vpp
echo "  --- what the box said on the way out ---"
grep -iE "bpa|SIGTERM" /tmp/vpp-1.log | tail -8 | sed 's/^/    /'
SPILL_ON_DISK=$(stat -c %s "$SPILL" 2>/dev/null || echo 0)
echo "  spill file on disk after the stop: ${SPILL_ON_DISK} byte(s)"

echo
echo "=== the collector reads again, and the box comes back ==="
# The collector first, so that whatever the stopped box did manage to put on
# the wire is read before the new incarnation connects.
rm -f "$STALL_FILE"
sleep 5
boot_vpp 2
# Long enough for the exporter to connect, replay the spill and empty it. The
# retry interval is 5 s and the reconnect is immediate, so this is generous.
sleep 12
REPLAYED=$(tcp_replayed)
echo "  replayed from the spill file on this boot: ${REPLAYED:-0} byte(s)"
vppctl show bpa logging | tr -d '\r' | sed -n '/^TCP transport/,$p' | sed 's/^/  /'
sleep 3

wait $COL 2>/dev/null
wait $COLT 2>/dev/null

echo
echo "=========== WHAT THE UDP COLLECTOR RECEIVED ==========="
grep -E "$(ev_re ALLOCATION "100.64.0.$SUB")|$(ev_re DEALLOCATION "100.64.0.$SUB")|^SUMMARY" \
  /tmp/collector.out | sed 's/^/  /'

echo
echo "=========== AND THE TCP ONE ==========="
grep -E "$(ev_re ALLOCATION "100.64.0.$SUB")|$(ev_re DEALLOCATION "100.64.0.$SUB")|STALLED|reading again|^RESUMO|connection" \
  /tmp/collector-tcp.out | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
C=$(cat /tmp/collector.out)
T=$(cat /tmp/collector-tcp.out)

ev_count() { printf '%s\n' "$3" | grep -cE "$(ev_re "$1" "$2")"; }

# --- the preconditions ---
#
# Every one of these has to hold before the verdict below means anything: a box
# that held no block, or an exporter whose buffer was empty, produces exactly
# the same silence the defect does.
expect "a block was held when the box was stopped (got ${BLOCKS_AT_STOP:-none})" \
  test "${BLOCKS_AT_STOP:-0}" -ge 1
expect "the UDP side had records to compare against (got ${UDP_BEFORE_STOP:-none})" \
  test "${UDP_BEFORE_STOP:-0}" -ge 1
# The drain is asked for by the product, not by this file.
expect "the shipped drop-in asks for the drain (${EXECSTOP:-none})" \
  test -n "$EXECSTOP"
expect "and nothing in this lab types the drain itself" \
  sh -c "! sed 's/#.*//' /src/lab/ipfix-outbuf.sh \
         | grep -qE 'vppctl[[:space:]]+bpa[[:space:]]+stop-drain'"
# Below the spill threshold, which is the whole window this is about: above it
# the bytes were already on disk and come back on the next connection anyway.
expect "the buffered bytes were below the 1 MB spill threshold (${PENDING_BEFORE_STOP:-none})" \
  test "${PENDING_BEFORE_STOP:-0}" -lt 1048576
# THE PRECONDITION THIS LAB EXISTS TO PRODUCE, and the one "no-stall" takes
# red - which is how it is known to be capable of going red at all.
expect "the exporter still had bytes of its own to send at the stop (${PENDING_BEFORE_STOP:-none})" \
  test "${PENDING_BEFORE_STOP:-0}" -gt 0
# And the subscriber's ALLOCATION really is on the collector's disk, so that a
# missing release below is a missing release and not a missing pair.
TA=$(ev_count ALLOCATION "100.64.0.$SUB" "$T")
expect "the collector has the allocation for 100.64.0.$SUB over TCP (got ${TA})" \
  test "${TA:-0}" -eq 1

# --- the control: the drain did write the release ---
#
# Over UDP the record leaves on the ordinary dataplane path while the graph is
# still running, which is what the ExecStop is for. If this is red the box
# never wrote the release at all, and the TCP result below would be about
# something else entirely.
CA=$(ev_count ALLOCATION "100.64.0.$SUB" "$C")
CD=$(ev_count DEALLOCATION "100.64.0.$SUB" "$C")
echo "  100.64.0.$SUB over UDP: ${CA} allocations, ${CD} releases"
expect "the stop drain released the block, and UDP carried it (got ${CD})" \
  test "${CD:-0}" -eq 1

# --- the verdict ---
#
# The same release, on the transport docs/logging.md calls the one that cannot
# lose a record. Late is allowed - the spill file replays on the next
# connection - and lost is not.
TD=$(ev_count DEALLOCATION "100.64.0.$SUB" "$T")
echo "  100.64.0.$SUB over TCP: ${TA} allocations, ${TD} releases"
expect "and the TCP exporter delivered it too, on the stop or on the replay (got ${TD})" \
  test "${TD:-0}" -eq 1

# The collector's own reading of the log, in the words a court request is
# answered in. Counting records says the bytes arrived; this says the log can
# be read.
#
# IT IS TRUE OF A BOX THAT ANNOUNCED NOTHING, and that is not an oversight it
# is safe to leave unsaid: an absent allocation leaves nothing open either.
# What stops it from carrying a pass on its own is the pairing precondition
# above - measured, by running this file against a box given no traffic at all,
# where this check stayed green and six others went red.
expect_not_in "no allocation for 100.64.0.$SUB is left open in the TCP log" \
  "100.64.0.$SUB -> " "$(printf '%s\n' "$T" | sed -n '/^OPEN ALLOCATIONS/,$p')"

echo "  spill: ${SPILL_BEFORE_STOP:-?} bytes written before the stop, \
${SPILL_ON_DISK} on disk after it, ${REPLAYED:-0} replayed on boot 2"
verdict
