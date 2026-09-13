#!/bin/sh
# IPFIX export of block events (RFC 8158) over UDP, with a real collector.
#
#   sh tools/lab.sh ipfix               -> LAB PASSED
#   sh tools/lab.sh ipfix no-exporter   -> LAB FAILED
#
#   pg0/pg1  subscriber traffic, dynamic pool
#   veth     path to the collector, which runs in the "peer" netns
#
# THE EXPORTER IS CONFIGURED THROUGH THE VTY, and that is the point of this
# file. It used to be configured here, with `vppctl set ipfix exporter`,
# because the export does not run without one and the product had no statement
# that reached it - so this lab performed the product's missing step and then
# reported that the product worked. UDP export had therefore never run on an
# installed box, for the life of the project, with a green lab beside it
# (#209). That is the shape of #99, #168, #182 and #184, and the rule it
# broke is the one that matters here: a lab that configures the box under
# test cannot notice the box failing to configure itself.
#
# So every line below that sets up the EXPORT goes through hexash, exactly as
# an operator types it. The topology - interfaces, routes, the pool - is still
# vppctl, because the topology is not what is under test.
#
# OFF-RED-AT-LEAST: 18
#
# A FLOOR, not a target, and the number is measured rather than chosen: it is
# what this lab's off run went red on in the release suite of 2026-09-13.
# tools/run-all-labs.sh refuses an off run that goes red on fewer, because a
# lab that used to fail eleven checks and now fails one has lost ten assertions
# without losing its verdict - which is the defect this whole sweep exists to
# find. Gaining assertions is fine and needs no edit here; losing them is what
# has to be deliberate.
set -u
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api

# "no-exporter" runs everything below with the exporter statement left out,
# which is the state every shipped box was in. The export then cannot run and
# the assertions at the bottom must go red - that is the only way to know they
# can.
MODE=${1:-with-exporter}
HX="python3 /src/src/hexash/hexash.py"

mkdir -p /run/vpp /vpp_plugins /var/log/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api

ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up
ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

# path to the collector
vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
# With no resolved neighbour, the first IPFIX records are dropped while ARP is
# still incomplete. For a compliance log that is silent loss, so the path to
# the collector has to be ready beforehand.
PEERMAC=$(ip netns exec peer cat /sys/class/net/veth-out/address)
vppctl set ip neighbor host-veth-vpp 10.0.0.2 $PEERMAC

# subscriber traffic
vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up
vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1

vppctl bpa pool din inside 100.64.0.0/22 outside 200.150.0.16/32 block 16384 mode dynamic
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa block-holddown 4

echo
echo "=== the box as it ships: 'logging enable' with no exporter ==="
# Asserted on a box nothing has configured yet, which is the state the
# appliance boots in. The refusal has to come from `commit check`, BEFORE
# anything is applied: the dataplane's own refusal arrives half way through
# the apply, with the pools already changed, and until #76 the VTY threw it
# away and printed "commit complete".
NO_EXPORTER=$($HX -c "configure" \
    -c "cgnat logging enable" \
    -c "commit check" \
    -c "commit" 2>&1)
printf '%s\n' "$NO_EXPORTER" | sed 's/^/  /'

if [ "$MODE" != no-exporter ]; then
  echo
  echo "=== configuring the exporter and the export, through the VTY ==="
  VTY_OUT=$($HX -c "configure" \
      -c "cgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739" \
      -c "cgnat logging enable" \
      -c "commit" 2>&1)
  printf '%s\n' "$VTY_OUT" | sed 's/^/  /'
else
  echo
  echo "=== no-exporter: the export state is asked for and nothing else ==="
  VTY_OUT=$($HX -c "configure" -c "cgnat logging enable" -c "commit" 2>&1)
  printf '%s\n' "$VTY_OUT" | sed 's/^/  /'
fi

echo
echo "=== what the dataplane has, after the VTY ==="
vppctl show bpa logging | sed 's/^/  /'
BPA_CONFIG=$(vppctl show bpa config | tr -d '\r')
echo "  -- the running configuration it reads back:"
printf '%s\n' "$BPA_CONFIG" | grep '^cgnat logging' | sed 's/^/    /'

# collector on the far side of the wire
ip netns exec peer python3 /src/tools/ipfix_collector.py 4739 60 > /tmp/collector.out 2>&1 &
COL=$!
# captures the wire in parallel: separates "VPP did not send" from "the
# collector lost it"
ip netns exec peer timeout 58 tcpdump -nni veth-out -c 80 "udp port 4739" > /tmp/tcpdump.out 2>&1 &
sleep 2

echo
echo "=== 3 subscribers: 3 block allocations ==="
for sub in 5 6 9; do
  vppctl packet-generator new "name s$sub limit 1 node ip4-input interface pg0 size 60-60 \
      data { UDP: 100.64.0.$sub -> 8.8.8.8 \
             UDP: 40000 -> 53 \
             incrementing 8 }"
done
vppctl packet-generator enable >/dev/null; sleep 1
for sub in 5 6 9; do vppctl packet-generator delete s$sub >/dev/null 2>&1; done

echo "=== 4th subscriber: exhaustion (natEvent 12) ==="
vppctl packet-generator new "name s12 limit 1 node ip4-input interface pg0 size 60-60 \
    data { UDP: 100.64.0.12 -> 8.8.8.8 \
           UDP: 40000 -> 53 \
           incrementing 8 }"
vppctl packet-generator enable >/dev/null; sleep 1
vppctl packet-generator delete s12 >/dev/null 2>&1

echo
echo "=== forcing expiry to produce the 3 releases ==="
vppctl bpa timeouts udp 2
n=0
until vppctl show bpa blocks | grep -q "total: 0/3" || [ "$n" -ge 25 ]; do sleep 1; n=$((n+1)); done
echo "  blocks handed back in ${n}s"
vc show bpa blocks | tail -1 | sed 's/^/  /'

echo
echo "=== a block held by the configuration is an allocation too ==="
# A static port forward on a dynamic pool pins the subscriber's block: it is
# held for as long as the forward exists and no hold-down takes it back. That
# is a block held by an operator's decision rather than by a packet, and in
# dynamic mode the log is the ONLY record of who held a block - so it has to
# produce the same two events as any other block, or the pin is a mapping
# nobody can attribute later.
#
#   100.64.0.1 is subscriber 1 -> 200.150.0.16, block 17408-33791
vppctl bpa forward din tcp 20000 to 100.64.0.1 80
sleep 2
vc show bpa blocks | tail -1 | sed 's/^/  /'
vppctl bpa forward din tcp 20000 del
sleep 3

echo
echo "=== a block pinned while the log was off is announced when it comes on ==="
# The order a configuration is applied in: the pools and their forwards first,
# the export afterwards. A pin made in that window is a block held from then
# on with nothing anywhere saying whose it is - and unlike an ordinary dynamic
# block, it never comes up for release and gets a second chance at a record.
#
#   100.64.0.2 is subscriber 2 -> 200.150.0.16, block 33792-50175
vppctl bpa logging disable
vppctl bpa forward din tcp 40000 to 100.64.0.2 80
vc show bpa blocks | tail -1 | sed 's/^/  pinned with the log off: /'
vppctl bpa logging enable
sleep 4
vppctl bpa forward din tcp 40000 del
sleep 3

echo
echo "=== deleting the pool releases every block it was still holding ==="
# A block held at the instant its pool is deleted is freed by bpa_pool_del()
# itself and never reaches the reap, so the ordinary release path never runs
# for it. Without a record of its own that allocation stays OPEN in the
# retention log for as long as the log is kept - and in dynamic mode that log
# IS the connection record, so "who held 200.150.0.16:1030 at 14:22" is
# answered with a subscriber who had already been disconnected (#215).
#
# Nothing may expire underneath this. The blocks have to still be held when
# the delete frees them, or the reap has already emitted their releases and
# this section is measuring the path it is not about.
vppctl bpa timeouts udp 3600
vppctl bpa block-holddown 3600
# The pin goes first: it takes the block the pool's arithmetic gives the
# subscriber, and a dynamic allocation that got there first would refuse it.
# Subscriber 0 because blocks 1 and 2 are the ones the sections above pin.
#
#   100.64.0.0 is subscriber 0 -> 200.150.0.16, block 1024-17407, and the
#   forwarded public port has to be inside that block: a forward is a port of
#   the subscriber's OWN block, and 20002 is subscriber 1's.
vppctl bpa forward din tcp 5000 to 100.64.0.0 80
# Two more blocks held by traffic rather than by configuration, because the
# two are freed by the same line of the delete and a fix that only covered
# pinned blocks would look identical from here.
for sub in 7 8; do
  vppctl packet-generator new "name d$sub limit 1 node ip4-input interface pg0 size 60-60 \
      data { UDP: 100.64.0.$sub -> 8.8.8.8 \
             UDP: 41000 -> 53 \
             incrementing 8 }"
done
vppctl packet-generator enable >/dev/null; sleep 1
for sub in 7 8; do vppctl packet-generator delete d$sub >/dev/null 2>&1; done
BLOCKS_HELD=$(vppctl show bpa blocks | tail -1 | tr -d '\r')
echo "  held when the delete runs: $BLOCKS_HELD"
vppctl bpa pool din del
# The delete queues its records where every other block event is queued; the
# expire process is what puts them on the wire, once a second.
sleep 3
vppctl show bpa blocks | sed 's/^/  after the delete: /'

echo
echo "=== contadores do exportador ==="
vppctl show bpa logging | sed 's/^/  /'

wait $COL 2>/dev/null
echo
echo "=========== WHAT WENT OVER THE WIRE (tcpdump) ==========="
grep -c "IP " /tmp/tcpdump.out 2>/dev/null | sed 's/^/  udp\/4739 packets: /'
grep "IP " /tmp/tcpdump.out 2>/dev/null | awk '{print $NF}' | sort | uniq -c | sed 's/^/  tamanhos: /'
echo
echo "=========== WHAT THE COLLECTOR RECEIVED AND DECODED ==========="
cat /tmp/collector.out

echo
echo "=========== THE SAVED CONFIGURATION, RELOADED ==========="
# The round trip, which is what makes the statement a configuration setting
# rather than a command. The exporter used to be set by hand with vppctl on a
# box that could not read it back, so saving the configuration and reloading
# it produced a box that looked identical and exported nothing (#116 is the
# same defect on the TCP collector).
#
# The ORDER is half the assertion. A saved file is written sorted, and
# "cgnat logging enable" sorts before "cgnat logging udp ..." - so a
# configuration written in alphabetical order refuses its own second line on
# reload, on the one path nobody types by hand: the boot.
printf '%s\n' "$BPA_CONFIG" | grep '^cgnat logging' > /tmp/logging.conf
echo "  the file as the box writes it:"
sed 's/^/    /' /tmp/logging.conf
$HX -c "configure" \
    -c "cgnat logging disable" \
    -c "cgnat logging udp disable" \
    -c "commit" > /tmp/off.log 2>&1
sed 's/^/    off: /' /tmp/off.log
LOG_OFF=$(vppctl show bpa logging)
$HX --load /tmp/logging.conf > /tmp/reload.log 2>&1
sed 's/^/    reload: /' /tmp/reload.log
LOG_BACK=$(vppctl show bpa logging)
printf '%s\n' "$LOG_BACK" | sed 's/^/    /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
C=$(cat /tmp/collector.out)

# --- the product configures the exporter, and refuses to pretend otherwise ---
expect_in "commit check refuses 'logging enable' with no exporter" \
  "UDP export has no collector" "$NO_EXPORTER"
expect_in "and the commit that follows applies nothing at all" \
  "candidate did not pass validation; nothing applied" "$NO_EXPORTER"
# Nothing in this file may configure the exporter behind the VTY's back. The
# assertion is on the file, because that is the mistake that survived five
# issues and it is invisible in any output.
# Comments stripped first: the header above names the command precisely so
# that whoever reads this file next knows what must not come back, and an
# assertion that could not tell that sentence from a live line would have to
# choose between saying it and checking it.
expect "no vppctl configures the exporter in this lab" \
  sh -c "! sed 's/#.*//' /src/lab/ipfix.sh \
         | grep -qE 'vppctl[[:space:]]+set[[:space:]]+ipfix'"

expect_in "the collector decoded the template" "TEMPLATE 256" "$C"
expect_in "allocations arrived" "ALLOCATION" "$C"
expect_in "de-allocations arrived" "DEALLOCATION" "$C"
expect_in "every allocation had its release" "Every allocation had its matching" "$C"

# --- the exporter is a configuration setting, not a command ---
expect_in "the exporter is in the running configuration" \
  "cgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739" "$BPA_CONFIG"
expect "and it is written BEFORE the line that needs it" \
  sh -c "[ \"\$(grep -n 'cgnat logging' /tmp/logging.conf | head -1 | cut -d: -f2-)\" \
         = 'cgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739' ]"
expect_not_in "the exporter really went away first" "10.0.0.2" "$LOG_OFF"
expect_in "reloading the saved configuration brought the exporter back" \
  "10.0.0.2:4739 src 10.0.0.1" "$LOG_BACK"
expect_in "and the export state with it" "state       enabled" "$LOG_BACK"

# Pinning a block for a forward, and giving it back. The subscriber and the
# port range are asserted, not just the event: a record that names the wrong
# block is worse than no record.
PIN_A=$(printf '%s\n' "$C" | grep -cE " ALLOCATION +100\.64\.0\.1 -> 200\.150\.0\.16 portas 17408-33791")
PIN_D=$(printf '%s\n' "$C" | grep -cE " DEALLOCATION +100\.64\.0\.1 -> 200\.150\.0\.16 portas 17408-33791")
expect "pinning a block for a forward was logged as one allocation (got ${PIN_A})" \
  test "${PIN_A:-0}" -eq 1
expect "and unpinning it as one release (got ${PIN_D})" \
  test "${PIN_D:-0}" -eq 1

# And the pin that was made while nothing was exporting. Without the replay
# this block is held with no record at all, which is the state a rebooted box
# would be in for every forward it carries.
OFF_A=$(printf '%s\n' "$C" | grep -cE " ALLOCATION +100\.64\.0\.2 -> 200\.150\.0\.16 portas 33792-50175")
OFF_D=$(printf '%s\n' "$C" | grep -cE " DEALLOCATION +100\.64\.0\.2 -> 200\.150\.0\.16 portas 33792-50175")
expect "a block pinned while the log was off is announced when it comes on (got ${OFF_A})" \
  test "${OFF_A:-0}" -eq 1
expect "and its release still arrives (got ${OFF_D})" \
  test "${OFF_D:-0}" -eq 1

# --- a pool deleted while it was holding blocks (#215) ---
#
# The precondition first. With no block held at the instant of the delete
# there is nothing for the delete to release, and every assertion below would
# pass on a box that never emitted a thing.
expect_in "the pool was holding all three of its blocks when it was deleted" \
  "total: 3/3 in use" "$BLOCKS_HELD"

# Every allocation in the capture against its release, paired by SUBSCRIBER
# and PORT RANGE rather than counted. Counts match while the records name
# different blocks, and a release that names the wrong block is worse than no
# release at all: it closes somebody else's allocation and leaves this one
# open, so the log now answers two questions wrongly instead of one.
unmatched() {
  printf '%s\n' "$C" | awk '
    $2 == "ALLOCATION"   { a[$3 " " $5 " " $7]++; next }
    $2 == "DEALLOCATION" { d[$3 " " $5 " " $7]++; next }
    END { for (k in a) if (a[k] != d[k] + 0)
            printf "%s allocated %d released %d\n", k, a[k], d[k] + 0 }'
}
UNMATCHED=$(unmatched)
expect "every allocation has its release, for the same subscriber and range" \
  test -z "$UNMATCHED"
[ -n "$UNMATCHED" ] && printf '%s\n' "$UNMATCHED" | sed 's/^/         still open: /'

# And named, block by block. The range is read back out of the allocation
# record rather than written here, because the dynamic allocator decides which
# block a subscriber gets: a lab that hardcoded one would be asserting a range
# the box may never have used, and would still be green the day the release
# started naming a block that was somebody else's.
rec_block() {
  printf '%s\n' "$C" |
    awk -v ev="$1" -v s="$2" '$2 == ev && $3 == s { print $5, $7 }'
}
for sub in 100.64.0.0 100.64.0.7 100.64.0.8; do
  DEL_A=$(rec_block ALLOCATION "$sub")
  DEL_D=$(rec_block DEALLOCATION "$sub")
  expect "$sub was recorded holding a block at the delete (${DEL_A:-none})" \
    test -n "$DEL_A"
  # Defaults that differ, so two empty strings are a failure rather than a
  # match: a subscriber with no record at all would otherwise satisfy this.
  expect "and the delete released that same block (held ${DEL_A:-none}, released ${DEL_D:-none})" \
    test "${DEL_A:-held nothing}" = "${DEL_D:-released nothing}"
done

# The record's own timestamp, against the box's clock.
#
# A record stamped from VPP's boot clock reads as a plausible time of day and
# is not one, so counting records will never catch it. The skew grows with the
# machine's uptime, which is what makes this assertion impossible to satisfy by
# accident: it fails harder the longer the box has been running.
# https://github.com/andrediashexa/hexarouting/issues/117
worst_skew() {
  printf '%s\n' "$1" | sed -n 's/.*skew=\([+-]*[0-9]*\)s.*/\1/p' |
    tr -d '+-' | sort -n | tail -1
}
SKEW=$(worst_skew "$C")
expect "the record carries the time of day, not time since boot (worst skew ${SKEW:-none}s)" \
  test "${SKEW:-99999}" -lt 120
verdict
