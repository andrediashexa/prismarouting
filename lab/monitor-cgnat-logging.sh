#!/bin/sh
# monitor cgnat logging: the block events, live, in words (#556).
#
#   sh tools/lab.sh monitor-cgnat-logging       -> LAB PASSED
#   sh tools/lab.sh monitor-cgnat-logging off   -> LAB FAILED
#
# OFF-RED-AT-LEAST: 8
#
# The box exports its block events over IPFIX to a collector in the "peer"
# netns, and three monitors watch while the same events are made: one with
# no filter, one filtered to a subscriber, one `raw event alloc`. The
# collector is the oracle: every allocation, release and exhaustion it
# decoded must be on the monitor's screen with the same subscriber, public
# address and port range, and nothing the collector did not receive may be.
#
# The events: three subscribers take the pool's three blocks, a fourth is
# refused 31 times (one exhaustion line, #549's aggregation, the rest counted
# on the delivery line), then the three blocks expire and are released.
#
# OFF MODE points the monitors at a segment that does not exist, which is the
# box this command would meet with its rings in the heap: they refuse, print
# no event, and every check that needs the screen goes red.
set -u
. /src/lab/lib.sh   # startup_conf, use_binary_api

MODE=${1:-on}
HX="python3 /src/src/prismash/prismash.py"

mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing
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

vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
PEERMAC=$(ip netns exec peer cat /sys/class/net/veth-out/address)
vppctl set ip neighbor host-veth-vpp 10.0.0.2 $PEERMAC

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up
vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1

# A pool created and deleted first, so `din` is NOT pool 0: the name must
# come from the pool's index, not from its row in `show bpa pool`.
vppctl bpa pool scratch inside 100.80.0.0/24 outside 200.150.0.40/32 block 16384 mode dynamic
vppctl bpa pool din inside 100.64.0.0/22 outside 200.150.0.16/32 block 16384 mode dynamic
vppctl bpa pool scratch del
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa block-holddown 2
echo "=== the pools by index, as the monitor reads them ==="
vppctl show bpa pool index | tr -d '\r' | sed 's/^/  /'

COL_STOP=/tmp/collector.stop
rm -f "$COL_STOP"
ip netns exec peer python3 /src/tools/ipfix_collector.py 4739 300 "$COL_STOP" \
  > /tmp/collector.out 2>&1 &
sleep 1

$HX -c "configure" -c "nat" -c "cgnat" \
    -c "logging udp 10.0.0.2 src 10.0.0.1 port 4739" -c "logging enable" \
    -c "commit" > /tmp/vty.out 2>&1
sed 's/^/  /' /tmp/vty.out

SEG=/dev/shm/prisma-bpa-log
[ "$MODE" = off ] && SEG=/dev/shm/no-such-segment
echo
echo "=== three monitors, started before the events ==="
PRISMA_LOG_SEGMENT=$SEG $HX -c "monitor cgnat logging seconds 30" > /tmp/mon-all.out 2>&1 &
M1=$!
PRISMA_LOG_SEGMENT=$SEG $HX -c "monitor cgnat logging subscriber 100.64.0.6 seconds 30" \
  > /tmp/mon-sub.out 2>&1 &
M2=$!
PRISMA_LOG_SEGMENT=$SEG $HX -c "monitor cgnat logging raw event alloc seconds 30" \
  > /tmp/mon-raw.out 2>&1 &
M3=$!
sleep 3

echo "=== 3 subscribers: 3 blocks ==="
for sub in 5 6 9; do
  vppctl packet-generator new "name s$sub limit 1 node ip4-input interface pg0 size 60-60 \
      data { UDP: 100.64.0.$sub -> 8.8.8.8 \
             UDP: 40000 -> 53 \
             incrementing 8 }"
done
vppctl packet-generator enable >/dev/null; sleep 1
for sub in 5 6 9; do vppctl packet-generator delete s$sub >/dev/null 2>&1; done

echo "=== a 4th subscriber, refused 31 times: one exhaustion line ==="
vppctl packet-generator new "name s12 limit 31 node ip4-input interface pg0 size 60-60 \
    data { UDP: 100.64.0.12 -> 8.8.8.8 \
           UDP: 40000 -> 53 \
           incrementing 8 }"
vppctl packet-generator enable >/dev/null; sleep 1
vppctl packet-generator delete s12 >/dev/null 2>&1

echo "=== the three blocks expire: three releases ==="
vppctl bpa timeouts udp 2
n=0
until vppctl show bpa blocks | grep -q "total: 0/" || [ "$n" -ge 20 ]; do sleep 1; n=$((n+1)); done
echo "  released in ${n}s"

wait $M1 $M2 $M3
sleep 2
: > "$COL_STOP"
sleep 2

echo
echo "=== the monitor, no filter ==="
sed 's/^/  /' /tmp/mon-all.out
echo "=== the monitor, subscriber 100.64.0.6 ==="
sed 's/^/  /' /tmp/mon-sub.out
echo "=== the monitor, raw event alloc ==="
sed 's/^/  /' /tmp/mon-raw.out
echo "=== the collector ==="
grep -E "ALLOCATION|DEALLOCATION|EXHAUSTION" /tmp/collector.out | sed 's/^/  /'

# The collector's records as "EVENT priv pub lo-hi", and the monitor's lines
# the same way, so the two are compared as sets of events.
col() {
  sed -nE "s/.* $1 +([0-9.]+) -> ([0-9.]+) portas ([0-9]+-[0-9]+) pool .*/\1 \2 \3/p" \
    /tmp/collector.out | sort
}
mon() {
  sed -nE "s/.* $1 +([0-9.]+) +> +([0-9.]+) +([0-9]+-[0-9]+).*/\1 \2 \3/p" \
    /tmp/mon-all.out | sort
}
COL_ALLOC=$(col ALLOCATION); MON_ALLOC=$(mon ALLOC)
COL_REL=$(col DEALLOCATION); MON_REL=$(mon RELEASE)
COL_EXH=$(grep -c " EXHAUSTION " /tmp/collector.out)
MON_EXH=$(grep -c "EXHAUSTED 100.64.0.12 *no free block in pool din" /tmp/mon-all.out)

. /src/lab/verdict.sh
expect "the collector received 3 allocations (got $(echo "$COL_ALLOC" | grep -c .))" \
  test "$(echo "$COL_ALLOC" | grep -c .)" = 3
expect "the monitor showed the same 3 allocations: subscriber, public, ports ($(echo "$MON_ALLOC" | grep -c .))" \
  test -n "$MON_ALLOC" -a "$MON_ALLOC" = "$COL_ALLOC"
expect "and the same 3 releases, which the main thread emits itself ($(echo "$MON_REL" | grep -c .))" \
  test -n "$MON_REL" -a "$MON_REL" = "$COL_REL"
expect "one exhaustion line for 31 refusals, as the collector got one record ($COL_EXH)" \
  test "$MON_EXH" = 1 -a "$COL_EXH" = 1
expect_in "the pool is named din although it is not pool 0" "pool din" "$(cat /tmp/mon-all.out)"
expect_in "the dataplane's start is said" "BOOT" "$(cat /tmp/mon-all.out)"
expect_in "what the export sent is said per stream" "-- ipfix-udp 10.0.0.2:4739" \
  "$(cat /tmp/mon-all.out)"
expect "the subscriber filter showed 100.64.0.6 and no other subscriber" \
  sh -c "grep -q 'ALLOC.*100.64.0.6 ' /tmp/mon-sub.out && ! grep -qE '100.64.0.(5|9|12) ' /tmp/mon-sub.out"
expect "raw event alloc: three IPFIX records, natEvent 16 only, by IE number" \
  sh -c "test \$(grep -c 'IPFIX 323=[0-9]* 230=16 8=100.64.0' /tmp/mon-raw.out) = 3 && ! grep -q '230=17' /tmp/mon-raw.out"
expect "every line of the plain monitor fits 79 columns" \
  sh -c "! awk 'length > 79' /tmp/mon-all.out | grep -q ."
verdict
