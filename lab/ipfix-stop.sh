#!/bin/sh
# A box that is stopped, and a box that dies: what the retention log says after.
#
#   sh tools/lab.sh ipfix-stop            -> LAB PASSED
#   sh tools/lab.sh ipfix-stop no-marker  -> LAB FAILED
#
# The collector runs for the whole lab, over UDP and over TCP at once, and
# survives two restarts of the box. Three states are produced deliberately,
# because the whole of #217 is that a collector must be able to tell them
# apart and until now could not:
#
#   A  a block held by a box that was STOPPED  -> allocation, release
#   B  a block held by a box that was KILLED   -> allocation, no release
#   C  a block held by a box still RUNNING     -> allocation, no release
#
# B and C are byte for byte the same shape in the log and are opposite answers
# to "who held this public port at this instant". What separates them is the
# boot marker: the box announces when this incarnation of the dataplane
# started (IE 160, systemInitTimeMilliseconds), so an allocation with a newer
# init time announced after it ended at that restart, and one with no newer
# init time is still in force.
#
# NOTHING HERE TYPES THE STOP DRAIN. The command an appliance runs before the
# signal is read out of the shipped drop-in,
# appliance/systemd/vpp-stop-drain.conf, and run as it is written there - so a
# drop-in that loses its ExecStop takes this lab red with it. A lab that
# performed the product's missing step is the shape of #99, #168, #182, #184
# and #209. That the DROP-IN IS INSTALLED and that systemd really runs it is
# asserted where a real systemd exists, in lab/install-iso.sh; a container has
# none.
#
# OFF-RED-AT-LEAST: 5
#
# A FLOOR, not a target, and the number is measured rather than chosen: it is
# what this lab's off run went red on in the release suite of 2026-09-13.
# tools/run-all-labs.sh refuses an off run that goes red on fewer, because a
# lab that used to fail eleven checks and now fails one has lost ten assertions
# without losing its verdict - which is the defect this whole sweep exists to
# find. Gaining assertions is fine and needs no edit here; losing them is what
# has to be deliberate.
set -u
. /src/lab/lib.sh   # startup_conf, use_binary_api

# "no-marker" runs everything below with the collector's marker decoding
# disabled, which is the state every shipped box was in: the records still
# arrive, the three states still happen, and the collector cannot tell B from
# C. The assertions that separate them must go red - that is the only way to
# know they can.
MODE=${1:-with-marker}
HX="python3 /src/src/hexash/hexash.py"
COLLECTOR=/src/tools/ipfix_collector.py

mkdir -p /run/vpp /vpp_plugins /var/log/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api

if [ "$MODE" = no-marker ]; then
  # The marker records still go out; the collector is made blind to them, the
  # way one that had never been told about IE 160 is.
  COLLECTOR=/tmp/ipfix_collector_blind.py
  sed 's/^    160: .*$/    160: ("ie160", "u32"),/' \
      /src/tools/ipfix_collector.py > "$COLLECTOR"
fi

ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up
ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out
PEERMAC=$(ip netns exec peer cat /sys/class/net/veth-out/address)

# Everything the box needs to be the box again, after a restart. The topology
# is vppctl because the topology is not what is under test; the EXPORT goes
# through the VTY, for the reason lab/ipfix.sh states at length - a lab that
# configures the box under test cannot notice the box failing to configure
# itself (#209).
boot_vpp() {
  vpp -c "$(startup_conf lcp)" > "/tmp/vpp-$1.log" 2>&1 &
  # The pid of this shell's own child, not pgrep's answer: this container has
  # no pgrep, and taking its empty output as "vpp is already gone" meant the
  # box was never stopped while the lab reported on the stop path.
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
  # which is how a real box has that path. It is also the half of this lab that
  # can still deliver a record after the graph has stopped.
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
  vppctl set interface ip address pg0 100.64.3.254/22
  vppctl set interface ip address pg1 200.150.0.1/24
  vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
  vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1

  vppctl bpa pool din inside 100.64.0.0/22 outside 200.150.0.16/32 \
      block 16384 mode dynamic
  vppctl set interface bpa in2out pg0
  vppctl set interface bpa out2in pg1
  # Nothing may expire underneath this lab. A block that came back on its own
  # produces a release from the ordinary path, and every assertion below would
  # then be measuring the path this is not about.
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

# Waits until the box says the allocation has actually left.
#
# The first version of this lab stopped the box three seconds after the block
# was taken, and no allocation reached the collector at all: a queued event is
# held back until the first template has gone out, and the template interval
# is 20 seconds. The lab was then measuring the queue rather than the stop
# path, and a drain that worked perfectly would still have shown nothing.
wait_exported() {
  n=0
  until [ "$(vppctl show bpa logging | \
             sed -n 's/.*records  *\([0-9][0-9]*\) emitted.*/\1/p')" -ge "$1" ] \
        || [ "$n" -ge 45 ]; do
    sleep 1; n=$((n+1))
  done
  echo "  $1 record(s) on the wire after ${n}s"
}

# The stop, as an appliance performs it: what the shipped drop-in puts in
# ExecStop, then the signal systemd sends afterwards.
#
# Read out of the file rather than retyped here. A lab that typed the drain
# itself would be performing the product's step and would stay green on a box
# whose unit never asks for it, which is the defect shape this repository has
# shipped five times.
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

# And the box that does not see it coming. No ExecStop, no signal it can
# handle: this is the power loss, and nothing the box does can close these
# allocations.
kill_vpp() {
  PID=$VPP_PID
  [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null || { echo "  no vpp to kill"; exit 1; }
  echo "  SIGKILL -> vpp pid $PID"
  kill -9 "$PID" 2>/dev/null
  n=0; while kill -0 "$PID" 2>/dev/null && [ "$n" -lt 20 ]; do sleep 1; n=$((n+1)); done
  echo "  gone after ${n}s"
  rm -f /dev/shm/db /dev/shm/global_vm /dev/shm/vpe-api
  sleep 1
}

ip netns exec peer python3 "$COLLECTOR" 4739 300 > /tmp/collector.out 2>&1 &
COL=$!
ip netns exec peer python3 "$COLLECTOR" tcp 4740 300 > /tmp/collector-tcp.out 2>&1 &
COLT=$!
sleep 2

echo
echo "=== boot 1, and a block held by 100.64.0.5 ==="
boot_vpp 1
hold_block 5
wait_exported 1
BLOCKS_AT_STOP=$(vppctl show bpa blocks | tail -1 | tr -d '\r')
echo "  held at the stop: $BLOCKS_AT_STOP"
vppctl show bpa logging | grep -i "boot marker" | sed 's/^/  /'

echo
echo "=== STOPPING the box with that block still held ==="
stop_vpp
echo "  --- what the box said on the way out ---"
grep -iE "bpa|SIGTERM" /tmp/vpp-1.log | tail -6 | sed 's/^/    /'

echo
echo "=== boot 2, and a block held by 100.64.0.7 ==="
boot_vpp 2
hold_block 7
wait_exported 1
BLOCKS_AT_KILL=$(vppctl show bpa blocks | tail -1 | tr -d '\r')
echo "  held at the kill: $BLOCKS_AT_KILL"

echo
echo "=== KILLING the box with that block still held ==="
kill_vpp

echo
echo "=== boot 3, and a block held by 100.64.0.9, and the box stays up ==="
boot_vpp 3
hold_block 9
wait_exported 1
BLOCKS_RUNNING=$(vppctl show bpa blocks | tail -1 | tr -d '\r')
echo "  held while the box is running: $BLOCKS_RUNNING"
vppctl show bpa logging | sed 's/^/  /'

wait $COL 2>/dev/null
wait $COLT 2>/dev/null

echo
echo "=========== WHAT THE COLLECTOR RECEIVED OVER UDP ==========="
cat /tmp/collector.out

echo
echo "=========== AND OVER TCP ==========="
cat /tmp/collector-tcp.out

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
C=$(cat /tmp/collector.out)
T=$(cat /tmp/collector-tcp.out)

ev_count() { printf '%s\n' "$3" | grep -cE "  *$1 +$2 -> "; }

# --- the preconditions ---
#
# Without a block actually held at each instant there is nothing for any of
# this to be about, and every assertion below would pass on a box that emitted
# nothing at all.
expect_in "a block was held when the box was stopped" "1/3 in use" "$BLOCKS_AT_STOP"
expect_in "a block was held when the box was killed" "1/3 in use" "$BLOCKS_AT_KILL"
expect_in "a block was held while the box went on running" \
  "1/3 in use" "$BLOCKS_RUNNING"
# The template by its SHAPE, not by its number. A template set carrying a
# different element list still announces itself as 256, so an assertion on the
# id alone is satisfied by a box exporting something else entirely.
expect_in "the collector decoded the block template" \
  "TEMPLATE 256: 7 fields, 25 bytes" "$C"
expect_in "and the boot marker's own template beside it" \
  "TEMPLATE 257: 2 fields, 16 bytes" "$C"
expect_in "the box says which template carries the marker" \
  "boot marker template 257" "$(vppctl show bpa logging 2>/dev/null)"

# The drain is asked for by the product, not by this file. Asserted on the
# drop-in and on this lab's own text, because both mistakes are invisible in
# any output: a drop-in with no ExecStop, and a lab that types the drain
# itself and then reports the product working.
expect "the shipped drop-in asks for the drain (${EXECSTOP:-none})" \
  test -n "$EXECSTOP"
expect "and nothing in this lab types the drain itself" \
  sh -c "! sed 's/#.*//' /src/lab/ipfix-stop.sh \
         | grep -qE 'vppctl[[:space:]]+bpa[[:space:]]+stop-drain'"

# --- A: the box that was stopped ---
A5=$(ev_count ALLOCATION 100.64.0.5 "$C")
D5=$(ev_count DEALLOCATION 100.64.0.5 "$C")
echo "  100.64.0.5 (stopped): ${A5} allocations, ${D5} releases over UDP"
expect "the stopped box announced its block (got ${A5})" test "${A5:-0}" -eq 1
expect "and released it on the way out (got ${D5})" test "${D5:-0}" -eq 1

# The same release on the transport that cannot lose a record. It arrives by a
# different route - the TCP exporter owns a kernel socket, so the in-process
# exit hook can still deliver after the graph has stopped - and a fix that
# worked on only one of the two would look identical from the other side.
TD5=$(ev_count DEALLOCATION 100.64.0.5 "$T")
expect "and the same release arrived over TCP (got ${TD5})" test "${TD5:-0}" -eq 1

# --- B: the box that was killed ---
A7=$(ev_count ALLOCATION 100.64.0.7 "$C")
D7=$(ev_count DEALLOCATION 100.64.0.7 "$C")
echo "  100.64.0.7 (killed):  ${A7} allocations, ${D7} releases over UDP"
expect "the killed box announced its block (got ${A7})" test "${A7:-0}" -eq 1
# NOT a defect, and the reason the drain alone is not the fix. A box that is
# killed emits nothing; there is no instant at which it could have.
expect "and could not release it, because it was killed (got ${D7})" \
  test "${D7:-0}" -eq 0

# --- C: the box that is still running ---
A9=$(ev_count ALLOCATION 100.64.0.9 "$C")
D9=$(ev_count DEALLOCATION 100.64.0.9 "$C")
echo "  100.64.0.9 (running): ${A9} allocations, ${D9} releases over UDP"
expect "the running box announced its block (got ${A9})" test "${A9:-0}" -eq 1
expect "and has not released it, because it still holds it (got ${D9})" \
  test "${D9:-0}" -eq 0

# --- the marker, and the only thing it is for ---
#
# B and C are identical above: one allocation, no release, on both. Every
# assertion from here is about telling them apart.
MARKERS=$(printf '%s\n' "$C" | grep -c "RESTART-MARKER")
expect "the box announced when this incarnation started (got ${MARKERS})" \
  test "${MARKERS:-0}" -ge 3
INITS=$(printf '%s\n' "$C" | sed -n 's/.*RESTART-MARKER *exporter init *//p' \
        | sort -u | wc -l)
echo "  distinct exporter init times announced: ${INITS}"
# THREE boots, three init times. A marker whose value did not change would be
# decoration: the collector's whole rule is that the value differs across an
# incarnation, and a constant would classify every crash as "still in force".
expect "and it changed on every restart (got ${INITS} distinct values)" \
  test "${INITS:-0}" -ge 3

# How the collector reads each still-open allocation, by subscriber. The port
# range is NOT written here: the dynamic allocator decides which block a
# subscriber gets, and a lab that hardcoded one would be asserting a range the
# box may never have used - and would still be green the day the report started
# naming somebody else's block.
open_state() {
  printf '%s\n' "$C" | sed -n '/^OPEN ALLOCATIONS/,$p' \
    | sed -n "s|.* $1 -> [0-9.]* portas [0-9-]*: \([A-Z][A-Z ]*[A-Z]\).*|\1|p" \
    | head -1
}
# And the range that report names, against the range the allocation record
# named. A verdict about the wrong block is worse than no verdict.
open_range() {
  printf '%s\n' "$C" | sed -n '/^OPEN ALLOCATIONS/,$p' \
    | sed -n "s|.* $1 -> [0-9.]* portas \([0-9-]*\):.*|\1|p" | head -1
}
alloc_range() {
  printf '%s\n' "$C" | awk -v s="$1" '$2 == "ALLOCATION" && $3 == s { print $7 }' \
    | head -1
}

OPEN7=$(open_state 100.64.0.7)
OPEN9=$(open_state 100.64.0.9)
OPEN5=$(open_state 100.64.0.5)
expect "the killed box's allocation is read as ended at the restart (got ${OPEN7:-none})" \
  test "${OPEN7:-none}" = "ENDED AT RESTART"
expect "the running box's allocation is read as still in force (got ${OPEN9:-none})" \
  test "${OPEN9:-none}" = "STILL IN FORCE"
OR7=$(open_range 100.64.0.7)
AR7=$(alloc_range 100.64.0.7)
# Defaults that DIFFER, so two empty strings are a failure rather than a match.
# With the same default on both sides this passed on a box that had announced
# nothing at all - found by mutating the plugin to emit no allocations, where
# it stayed green with "open , allocated ". lab/ipfix.sh makes the same point
# about its own pair.
expect "and it is the block the box said it allocated (open ${OR7:-none}, allocated ${AR7:-none})" \
  test "${OR7:-open nothing}" = "${AR7:-allocated nothing}"
# The issue's own acceptance, in its own words: no allocation spans a restart
# without either a release or a marker.
expect_not_in "no open allocation is left undecidable" "UNDECIDABLE" "$C"
# And the stopped box's block is not in the list at all - it has its release.
expect "the stopped box's block is not left open at all (got ${OPEN5:-none})" \
  test -z "$OPEN5"

# The record's own timestamp against the box's clock, for the marker as much as
# for the block records: a marker stamped from VPP's boot clock reads as a
# plausible time of day and is not one (#117).
worst_skew() {
  printf '%s\n' "$1" | sed -n 's/.*skew=\([+-]*[0-9]*\)s.*/\1/p' |
    tr -d '+-' | sort -n | tail -1
}
SKEW=$(worst_skew "$C")
expect "every record carries the time of day, not time since boot (worst ${SKEW:-none}s)" \
  test "${SKEW:-99999}" -lt 120
verdict
