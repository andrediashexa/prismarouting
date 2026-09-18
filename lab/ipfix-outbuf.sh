#!/bin/sh
# A box stopped while the TCP exporter still had bytes of its own to send.
#
#   sh tools/lab.sh ipfix-outbuf           -> LAB PASSED
#   sh tools/lab.sh ipfix-outbuf no-stall  -> LAB FAILED, exactly 8 checks
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
#
# THREE MORE RULES THAT LIVED ONLY IN PROSE. docs/logging.md states them of the
# TCP exporter, and each was mutated in src/plugins/bpa/bpa_log_tcp.c with all
# 38 suites and both IPFIX labs green:
#
#   #262  the spill MUST be prepended on reconnect, not appended. Nothing in
#         this repository asserted the ORDER of two records, which for a log a
#         court order is read from is the assertion that matters most.
#   #259  if the spill itself fails, the discard MUST be counted, never silent.
#         Nothing ever made the spill fail, so both counters could be deleted.
#   #263  the exit path MUST NOT wait for the socket. A ten second poll() added
#         to it cost ten seconds of every stop and sent not one byte.
#
# They are here rather than in a lab of their own because this file already
# produces every condition all three need: a collector that is connected and
# not reading, a spill file written at a stop, and a second boot that replays
# it. What they needed was for somebody to look at what came back.
#
# OFF-RED-AT-LEAST: 9
#
# A FLOOR, not a target, and the number is measured rather than chosen: it is
# what this lab's off run went red on, measured on 2026-09-13 at 8 and again
# on 2026-09-15 at 9. It was 1 before the three checks below were added; the
# other seven are the spill-failure
# phase, which "no-stall" cannot produce because it cannot stall the collector,
# so its preconditions and its verdicts all go red together.
# tools/run-all-labs.sh refuses an off run that goes red on fewer, because a
# lab that used to fail eleven checks and now fails one has lost ten assertions
# without losing its verdict - which is the defect this whole sweep exists to
# find. Gaining assertions is fine and needs no edit here; losing them is what
# has to be deliberate.
set -u
. /src/lab/lib.sh   # startup_conf, use_binary_api

# "no-stall" runs everything below with the collector reading throughout. The
# exporter's buffer is then empty when the box stops, the release arrives on
# the spot, and the PRECONDITION assertion - that there were bytes of the
# exporter's own still to send - is the one that must go red. It is here so
# that a lab which quietly stopped producing its own condition cannot report a
# pass for a box it never put under the condition.
MODE=${1:-stall}
HX="python3 /src/src/prismash/prismash.py"
COLLECTOR=/src/tools/ipfix_collector.py
STALL_FILE=/tmp/ipfix-stall
SPILL=/var/log/prisma-routing/ipfix-spill.bin

mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api

# A spill left by an earlier run would be replayed into this one and counted as
# a record this box exported.
# -r as well: the phase that makes the spill path unopenable leaves a DIRECTORY
# there if this file is interrupted, and `rm -f` on a directory does nothing.
rm -rf "$SPILL"
rm -f "$STALL_FILE"

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
# The inside prefix is sized by the two thresholds this file has to cross, and
# neither is a property of the lab:
#
#   the kernel's. The receive window can be pinned small from the collector,
#   but the exporter's own send buffer grows on the first ACK - 45 kB went into
#   it on this machine before send() first refused. A few thousand subscribers
#   is room to pass that on a kernel that buffers more.
#
#   the product's. The spill only triggers above 1 MB (BPA_TCP_SPILL_BYTES),
#   and a record on this wire is 45 bytes - 82935 bytes came out of 1843 of
#   them. So a phase that has to make the spill itself FAIL needs 23302
#   records, which is 23302 first packets, which is 23302 subscribers. A /20
#   holds 4096 and was measured against exactly that wall.
#
# /14 is inside 100.64.0.0/10, the shared address space this product exists to
# translate, and holds 262144.
INSIDE=100.64.0.0/14
PG0=100.67.255.254/14

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

# An index in 100.64.0.0/14, as an address.
ip_of() {
  echo "100.$(( 64 + $1 / 65536 )).$(( ( $1 / 256 ) % 256 )).$(( $1 % 256 ))"
}

# flood <count>: one first packet from each of <count> subscribers that have
# never been seen, which is <count> natEvent 16 for the exporter to write.
#
# ONE packet-generator stream per chunk, with the source address as a RANGE.
# churn() builds one stream per subscriber and that is the right shape for a
# few hundred; for the twenty-three thousand records the 1 MB spill threshold
# needs it is tens of thousands of CLI invocations, and minutes of them.
#
# IN CHUNKS, because the per-thread queue between the dataplane and the
# exporter holds BPA_LOG_QUEUE_MAX events and counts anything beyond that as
# dropped BEFORE the exporter ever sees it. That is a different silent loss
# from the one this file is about, and it would stop the buffer reaching the
# threshold at all.
FLOOD_NEXT=4096
flood() {
  _want=$1
  _done=0
  while [ "$_done" -lt "$_want" ]; do
    _this=6000
    [ $(( _done + _this )) -gt "$_want" ] && _this=$(( _want - _done ))
    _lo=$FLOOD_NEXT
    _hi=$(( _lo + _this - 1 ))
    vppctl packet-generator new "name flood limit $_this node ip4-input \
      interface pg0 size 60-60 data { UDP: $(ip_of $_lo) - $(ip_of $_hi) \
      -> 8.8.8.8 UDP: 40000 -> 53 incrementing 8 }" > /dev/null
    vppctl packet-generator enable > /dev/null
    sleep 3
    vppctl packet-generator delete flood > /dev/null 2>&1
    FLOOD_NEXT=$(( _hi + 1 ))
    _done=$(( _done + _this ))
  done
  sleep 2
}

# Block events in waves, until the exporter is holding bytes the socket will
# not take. Leaves the count in PENDING.
#
# In waves, stopping as soon as the buffer is not empty: how many records it
# takes is a property of the kernel's buffers, and a fixed number would be too
# few on one machine and a minute wasted on another.
#
# The subscriber numbering restarts on every call, because every call is either
# a fresh box or a box whose blocks the drain has just released: a subscriber
# with no block takes a new one, and that is the natEvent 16 this wants.
fill_outbuf() {
  WAVE=0
  NEXT=16
  PENDING=0
  until [ "${PENDING:-0}" -gt 0 ] || [ "$WAVE" -ge 8 ]; do
    churn "$NEXT" 500
    NEXT=$(( NEXT + 500 ))
    WAVE=$(( WAVE + 1 ))
    sleep 3
    PENDING=$(tcp_pending)
    echo "  after wave $WAVE: $(blocks_in_use) blocks in use, \
${PENDING:-?} bytes pending, $(tcp_sent) bytes accepted by the socket"
  done
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
tcp_dropped() { tcp_stat '.*replayed, \([0-9][0-9]*\) dropped.*'; }
tcp_records() { tcp_stat '.*records *\([0-9][0-9]*\) emitted.*'; }

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
  # The drain RELEASES the blocks; it does not flush the exporter. Only the
  # exit hook does that, which is why the counters below read zero here and why
  # the spill's own failure has to be produced another way entirely (#259).
  echo "  after the drain: $(tcp_spilled) byte(s) spilled, $(tcp_dropped) dropped"
  echo "  SIGTERM -> vpp pid $PID"
  kill -TERM "$PID" 2>/dev/null
  n=0; while kill -0 "$PID" 2>/dev/null && [ "$n" -lt 30 ]; do sleep 1; n=$((n+1)); done
  kill -0 "$PID" 2>/dev/null && { echo "  still alive after ${n}s, SIGKILL"; kill -9 "$PID"; }
  echo "  gone after ${n}s"
  # How long the signal took, kept for the verdict. The exit path writes one
  # file and must not wait on the socket, so this is the number #263 is about.
  STOP_SECS=$n
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
fill_outbuf

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
STOP1_SECS=$STOP_SECS
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

# WHICH MARKER IN THE COLLECTOR'S LOG IS THIS BOX'S, asked of the box itself.
# `show bpa logging` reports the init time it stamps into IE 160 and the
# collector prints the same instant, so the two can be matched. Boot 1's
# markers are in the collector's log too, and with the spill appended instead
# of prepended they are the ones that come LAST - so the boundary cannot be
# found by position (#262).
BOOT2_INIT=$(vppctl show bpa logging | tr -d '\r' \
             | sed -n 's/.*init [0-9-]*T\([0-9:]*\)Z.*/\1/p' | head -1)
echo "  this incarnation announces init ${BOOT2_INIT:-none}"
# The order verdict is taken on a COPY, frozen here. Everything after this
# point puts more block events on the wire, and a check about where the replay
# landed must not be able to read them.
cp /tmp/collector-tcp.out /tmp/collector-replay.out
echo "  the reconnection, as the collector read it:"
grep -nE "connection accepted|RESTART-MARKER" /tmp/collector-replay.out \
  | tail -4 | sed 's/^/    /'

wait $COL 2>/dev/null
wait $COLT 2>/dev/null

# ===========================================================================
# The spill's own failure, which nothing in this tree had ever produced (#259).
#
# docs/logging.md: pending bytes MUST spill to disk when the collector is
# unreachable, and if even that fails the discard MUST be counted, never
# silent. Both increments in bpa_tcp_spill_now() could be deleted with all 38
# suites and both IPFIX labs green, because no test ever made the open() or the
# write() fail. The counter is the only thing that tells "the collector will
# get these later" from "these are gone": records N emitted still counts them,
# pending drops back to zero because the buffer really was emptied, and an
# operator reading `show bpa logging` after a disk filled up sees an exporter
# that has caught up.
#
# HERE, AT THE END, BECAUSE OF WHAT IT COSTS. The only live way into that code
# is the 1 MB threshold - `bpa stop-drain` does NOT flush the exporter, only
# the exit hook does, and a counter in a process that is leaving cannot be
# read. 1 MB is 23302 records at 45 bytes each, so each of the three cases
# below floods the box with more subscribers than every other phase of this
# file put together. It runs after both collectors have finished so that none
# of it reaches their files, and with no collector left the exporter is
# disconnected, which is the state the spill exists for.
#
# THREE CASES, BECAUSE THERE ARE THREE OUTCOMES AND TWO INCREMENTS:
#
#   writable       the bytes are counted under `spilled` and none under
#                  `dropped`. Without this a build that counted everything as
#                  lost would pass the other two.
#   a directory    where the file goes: open() fails with EISDIR.
#   no room left   open() succeeds and write() does not.
#
# The two failures are ordinary on an appliance - a full /var/log, a read-only
# remount after a media error, a directory something left behind - and neither
# changes anything else about the box.
if [ "$MODE" != no-stall ]; then
  # Above 23302, with room for anything the box loses on the way.
  FLOOD=26000

  echo
  echo "=== past the 1 MB threshold, with the spill path writable ==="
  rm -rf "$SPILL"
  REC_A=$(tcp_records); SPILLED_A=$(tcp_spilled); DROPPED_A=$(tcp_dropped)
  flood $FLOOD
  REC_B=$(tcp_records); SPILLED_B=$(tcp_spilled); DROPPED_B=$(tcp_dropped)
  echo "  records ${REC_A:-?} -> ${REC_B:-?}, spilled ${SPILLED_A:-?} -> \
${SPILLED_B:-?}, dropped ${DROPPED_A:-none} -> ${DROPPED_B:-none}"
  echo "  spill file on disk: $(stat -c %s "$SPILL" 2>/dev/null || echo 0) byte(s)"

  echo
  echo "=== and again, with a DIRECTORY where the spill file goes ==="
  rm -rf "$SPILL"
  mkdir -p "$SPILL"
  REC_C=$(tcp_records); SPILLED_C=$(tcp_spilled); DROPPED_C=$(tcp_dropped)
  flood $FLOOD
  REC_D=$(tcp_records); SPILLED_D=$(tcp_spilled); DROPPED_D=$(tcp_dropped)
  echo "  records ${REC_C:-?} -> ${REC_D:-?}, spilled ${SPILLED_C:-?} -> \
${SPILLED_D:-?}, dropped ${DROPPED_C:-none} -> ${DROPPED_D:-none}"
  rmdir "$SPILL" 2>/dev/null

  echo
  echo "=== and again, on a filesystem with no room left ==="
  # nr_inodes as well as size: open() has to SUCCEED here, or this is the case
  # above over again under a different name. Whether the file was created is
  # read back below and asserted, rather than assumed.
  mount -t tmpfs -o size=64k,nr_inodes=64 tmpfs /var/log/prisma-routing 2>/dev/null \
    && echo "  /var/log/prisma-routing is a 64k tmpfs now" \
    || echo "  could not mount the tmpfs"
  dd if=/dev/zero of=/var/log/prisma-routing/filler bs=1k count=128 2>/dev/null
  echo "  free on it: $(df -k /var/log/prisma-routing | tail -1 | awk '{print $4}') kB"
  REC_E=$(tcp_records); SPILLED_E=$(tcp_spilled); DROPPED_E=$(tcp_dropped)
  flood $FLOOD
  REC_F=$(tcp_records); SPILLED_F=$(tcp_spilled); DROPPED_F=$(tcp_dropped)
  SPILL_EXISTS=no
  [ -f "$SPILL" ] && SPILL_EXISTS=yes
  echo "  records ${REC_E:-?} -> ${REC_F:-?}, spilled ${SPILLED_E:-?} -> \
${SPILLED_F:-?}, dropped ${DROPPED_E:-none} -> ${DROPPED_F:-none}"
  echo "  the spill file was created: $SPILL_EXISTS"
  vppctl show bpa logging | tr -d '\r' | sed -n '/^TCP transport/,$p' | sed 's/^/  /'

  echo
  echo "=== and the STOP, with nowhere left to write it ==="
  # The one path where counting is not saying. bpa_stop_drain() runs from
  # VLIB_MAIN_LOOP_EXIT, so the counter it increments dies with the process
  # moments later: `show bpa logging` cannot be asked afterwards because there
  # is nothing left to ask. journalctl is the only place this can still be read
  # after the box has stopped (#270).
  #
  # The filesystem is left full on purpose, so the flush at exit fails the same
  # way the flood above did. The bytes still in the buffer are the ones the
  # collector never took.
  echo "  buffered at the stop: $(tcp_pending 2>/dev/null || echo '?') byte(s)"
  DROPPED_STOP=$(tcp_dropped)
  stop_vpp
  umount /var/log/prisma-routing 2>/dev/null
  echo "  what the process said on its way out:"
  EXIT_SAID=$(grep -iE "could NOT be written|went to the spill file" \
              /tmp/vpp-2.log 2>/dev/null | tail -3)
  printf '%s\n' "${EXIT_SAID:-  (nothing)}" | sed 's/^/    /'

  # And back up, because the verdict below reads the box. A lab that ends with
  # no dataplane is reported NO VPP by the suite whatever it measured, which is
  # right in general and wrong here: this one stopped the box ON PURPOSE and
  # has its answer already.
  boot_vpp 3
else
  echo
  echo "=== the spill's own failure is not produced in this mode ==="
fi

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

# --- the exit path did not wait for the socket (#263) ---
#
# docs/logging.md: the exit path MUST NOT wait for the socket, because the
# collector that leaves bytes there is the one that has stopped reading - its
# window is shut, so a wait expires having sent nothing - and a wait in a stop
# path is how a reboot becomes a power cycle. The comment in
# bpa_log_tcp_flush_exit() restates it down to the budget, systemd's
# DefaultTimeoutStopSec of 90 s, and nothing executable held any of it: a ten
# second poll(POLLOUT) added there made the stop take 11s instead of 1s, put
# THE SAME 82935 bytes in the file, and left all 38 suites and this lab green.
#
# READ TOGETHER WITH THE PRECONDITION ABOVE, that there really were bytes
# pending: a stop with an empty buffer is fast on any build, and this number
# would then say nothing. The collector is stalled here, which is where a
# waiting exit path is at its slowest and a correct one is unchanged - it does
# one write() whatever the collector is doing.
#
# The bound is not the 90 s systemd would allow. Anything a human waiting at a
# console would notice is already the defect, and the measured value is 1s.
expect "the stop did not wait for the socket (gone after ${STOP1_SECS:-?}s)" \
  test "${STOP1_SECS:-99}" -le 5

# --- the spill was replayed IN FRONT of what boot 2 had written (#262) ---
#
# docs/logging.md: the spill MUST be prepended on reconnect, not appended, or
# the collector reconstructs an impossible history - a release that arrives
# before its allocation matches nothing and is discarded, and the allocation
# behind it is then held open for ever. Replacing vec_insert (outbuf, n, 0)
# with vec_add left all 38 suites and this file green, with 82935 bytes of
# boot 1's log delivered behind a record boot 2 wrote twenty seconds later.
# Nothing anywhere asserted the order of two records.
#
# THE BOUNDARY IS BOOT 2'S OWN MARKER, and it is in the buffer when the replay
# happens: bpa_log_drain() writes the marker and calls bpa_log_tcp_service() in
# the same call, and the connect cannot complete before that first service
# call. So the spill is inserted into a buffer that is never empty, and the two
# can be ordered against each other.
#
# LINE NUMBERS ARE THE WIRE ORDER HERE, and that had to be checked before
# anything was asserted on them. tools/ipfix_collector.py has exactly one way
# of printing out of order: a data set that arrives before its template is HELD
# and reprocessed when the template comes, several lines late. That forgiveness
# is what hid #258 - but it cannot happen on this file's stream, because the
# collector learnt both templates on boot 1's connection and a template it has
# been taught is never forgotten, not even when the connection is replaced. So
# there is no assertion about it here: a check that cannot fail is not a check,
# and the case it would guard is asserted in lab/ipfix.sh, where the collector
# is started before the exporter and has been taught nothing.
EVRE='^ *[0-9][0-9]:[0-9][0-9]:[0-9][0-9] (ALLOCATION|DEALLOCATION) '
MARK2=$(grep -n "RESTART-MARKER exporter init ${BOOT2_INIT:-nothing}" \
        /tmp/collector-replay.out 2>/dev/null | head -1 | cut -d: -f1)
if [ -n "${MARK2:-}" ]; then
  # grep -c prints 0 AND exits 1, so the status is discarded on purpose here.
  BEFORE2=$(head -n $(( MARK2 - 1 )) /tmp/collector-replay.out | grep -cE "$EVRE")
  AFTER2=$(tail -n +$(( MARK2 + 1 )) /tmp/collector-replay.out | grep -cE "$EVRE")
else
  BEFORE2=0
  AFTER2=-1
fi
echo "  boot 2's marker is line ${MARK2:-none} of the collector's log: \
${BEFORE2} block event(s) ahead of it, ${AFTER2} behind it"
expect "the spill really was replayed on this boot (got ${REPLAYED:-0} bytes)" \
  test "${REPLAYED:-0}" -gt 0
expect "boot 2's own marker reached the collector (init ${BOOT2_INIT:-none})" \
  test -n "${MARK2:-}"
expect "block events reached the collector ahead of it (got ${BEFORE2})" \
  test "${BEFORE2:-0}" -gt 0
expect "and not one arrived behind it: the spill went in front (got ${AFTER2})" \
  test "${AFTER2:-1}" -eq 0

# --- a discard is counted, never silent (#259) ---
#
# Read the three phases as one statement about the counters: what a writable
# path does with the bytes, and what each of the two ways the spill can fail
# does with them. Every one of them needs its own precondition, because a
# flood that never crossed the 1 MB threshold produces exactly the silence a
# build with no counters produces.
#
# THE MEGABYTE IS THE PRECONDITION. It is read as records emitted rather than
# as bytes pending, because pending is zero after a spill whether the bytes
# were written or thrown away - which is the whole difficulty. 45 bytes a
# record, 23302 records to the megabyte.
NEED=23302
FLOODED_OK=$(( ${REC_B:-0} - ${REC_A:-0} ))
FLOODED_DIR=$(( ${REC_D:-0} - ${REC_C:-0} ))
FLOODED_FULL=$(( ${REC_F:-0} - ${REC_E:-0} ))

# The counterpart first: a writable path puts the bytes under `spilled` and
# none under `dropped`. Without it a build that counted every spill as lost
# would pass the two cases below.
expect "the box wrote more than a megabyte with the path writable (got ${FLOODED_OK} records)" \
  test "${FLOODED_OK}" -ge "$NEED"
expect "and they were counted as spilled (${SPILLED_A:-none} -> ${SPILLED_B:-none})" \
  test "${SPILLED_B:-0}" -gt "${SPILLED_A:-0}"
expect "with none of them counted as dropped (${DROPPED_A:-none} -> ${DROPPED_B:-none})" \
  test "${DROPPED_B:-0}" -eq "${DROPPED_A:-0}"

# A path that cannot be opened at all: the fd < 0 branch.
expect "the box wrote more than a megabyte with the path unopenable (got ${FLOODED_DIR} records)" \
  test "${FLOODED_DIR}" -ge "$NEED"
expect "a spill that cannot be opened is counted (${DROPPED_C:-none} -> ${DROPPED_D:-none})" \
  test "${DROPPED_D:-0}" -gt "${DROPPED_C:-0}"
expect "and not counted as spilled (${SPILLED_C:-none} -> ${SPILLED_D:-none})" \
  test "${SPILLED_D:-0}" -eq "${SPILLED_C:-0}"

# A filesystem with no room on it: open() succeeds and write() does not, which
# is the other increment. The file having been created is what says so.
expect "the box wrote more than a megabyte with no room on the filesystem (got ${FLOODED_FULL} records)" \
  test "${FLOODED_FULL}" -ge "$NEED"
expect "the spill file was created there, so this is the write() and not the open()" \
  test "${SPILL_EXISTS:-no}" = yes
expect "a spill that cannot be written is counted (${DROPPED_E:-none} -> ${DROPPED_F:-none})" \
  test "${DROPPED_F:-0}" -gt "${DROPPED_E:-0}"
expect "and not counted as spilled either (${SPILLED_E:-none} -> ${SPILLED_F:-none})" \
  test "${SPILLED_F:-0}" -eq "${SPILLED_E:-0}"

# The exit path, where the counter cannot be read afterwards.
#
# Every other discard happens on a box that keeps running: `show bpa logging`
# reads the count back. This one increments a counter in a process that is
# ending, so the only thing that survives is what it SAID. docs/logging.md
# calls the discard MUST-be-counted; on this path counted is not enough (#270).
# "the stop had bytes it could not write" used to be asserted here as
# `test "${DROPPED_STOP:-0}" -ge 0`, and a byte count is never negative, so it
# was true of every number the box can hold and of the empty string as well.
# Driven under dash over '', 0, 1 and 999999: [ ok ] on all four.
#
# It is DELETED rather than repaired, because its subject is already held twice
# over and neither of those can be written a third time without saying less.
# That the exporter had bytes left at the stop is the precondition above, which
# is the one "no-stall" takes red; that they could not be written is the line
# below, and bpa_stop_drain() prints that sentence only under `if (lost)`
# (bpa.c:1993), so the phrase IS the number being non-zero.
#
# The OFF-RED-AT-LEAST floor WAS changed by this, and the note that said it
# was not is why floors go stale. An assertion true for every value is green
# in the off run too, so this one was never among the eight; repairing it made
# it the ninth. Two off runs since have gone red on nine:
#
#   the exporter still had bytes of its own to send at the stop
#   the box wrote more than a megabyte with the path writable
#   and they were counted as spilled
#   the box wrote more than a megabyte with the path unopenable
#   a spill that cannot be opened is counted
#   the box wrote more than a megabyte with no room on the filesystem
#   the spill file was created there, so this is the write() and not the open()
#   a spill that cannot be written is counted
#   and the process said how many were lost, where it can still be read
#
# The last of those is this check. A floor left at eight would let it be lost
# again without the suite saying anything, which is the whole point of having
# one.
expect_in "and the process said how many were lost, where it can still be read" \
  "could NOT be written" "${EXIT_SAID:-}"

verdict
