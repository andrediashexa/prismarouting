#!/bin/sh
# Deterministic mode exports nothing, which is the whole commercial argument.
#
#   sh tools/lab.sh det-zero
#
# `README.md`'s 5W2H table answers *Why* with it:
#
#   Brazilian regulation (Marco Civil, Law 12.965/2014) requires one year of
#   connection records. Per-flow NAT logging produces terabytes; BPA reduces
#   this to one record per block - or to ZERO in deterministic mode.
#
# `docs/context.md` invariant 1.2 says the same thing from the other side: in
# deterministic mode the configuration IS the log, because the mapping is
# arithmetic and there is no allocation event to record.
#
# An ISP chooses deterministic mode for exactly this, and nothing measured it.
# Every IPFIX lab in this repository runs a DYNAMIC pool: `ipfix`, `ipfix-tcp`
# and `ipfix-stop` all say `mode dynamic` and nothing else ever armed an
# exporter over a deterministic one (#266).
#
# The failure it guards is silent and slow. If deterministic mode began
# emitting a record per block, nothing here would fail; an operator would find
# out from the collector's bill, or from a spill file filling the log volume on
# a box that was meant to export nothing at all.
#
# SECTION 3 IS THE CONTROL AND THE LAB IS WORTHLESS WITHOUT IT. A box whose
# exporter was never armed also emits zero, and would satisfy section 2 while
# proving nothing at all. So the same box, the same exporter and the same
# traffic are pointed at a dynamic pool afterwards, and that MUST produce
# records.
set -u

mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh
use_binary_api
HX="python3 /src/src/prismash/prismash.py"
export PRISMA_CONFIG=/etc/prisma-routing/prisma-routing.conf

# the path to the collector
ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up
ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do
  sleep 1; n=$((n+1))
done
vppctl show version >/dev/null 2>&1 || {
  echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
# The neighbour first: with ARP still incomplete the first records are dropped,
# and a lab counting records would credit that to the mode under test.
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
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1

send() {  # <subscriber last octet>
  vppctl packet-generator new "name s$1 limit 1 node ip4-input interface pg0 \
      size 60-60 data { UDP: 100.64.0.$1 -> 8.8.8.8 \
                        UDP: 40000 -> 53 \
                        incrementing 8 }" >/dev/null
  vppctl packet-generator enable >/dev/null; sleep 1
  vppctl packet-generator delete s$1 >/dev/null 2>&1
}
exported() {  # how many records the box says it has put on the wire
  vppctl show bpa logging \
    | sed -n 's/.*records  *\([0-9][0-9]*\) emitted.*/\1/p' | head -1
}

echo "=========== 1. the exporter, armed through the VTY ==========="
ip netns exec peer python3 /src/tools/ipfix_collector.py 4739 45 > /tmp/collector.out 2>&1 &
sleep 2
vppctl bpa pool det inside 100.64.0.0/22 outside 200.150.0.16/28 block 512
$HX -c "configure" \
    -c "cgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739" \
    -c "cgnat logging enable" -c "commit" 2>&1 | sed 's/^/  /'
vppctl show bpa logging | grep -iE "collector|enabled|records" | sed 's/^/  /'
ARMED=$(vppctl show bpa logging)

echo
echo "=========== 2. traffic through a DETERMINISTIC pool ==========="
vppctl show bpa pool | grep -E "^pool det" | sed 's/^/  /'
BEFORE=$(exported)
for sub in 5 6 9 12; do send $sub; done
sleep 3
DET_AFTER=$(exported)
# Read off the "total: N sessions" line. The first version grepped for
# "in2out", which `show bpa session` does not print at all, so it counted zero
# on a box that had translated four and reported the lab broken.
DET_SESS=$(vppctl show bpa session | sed -n 's/^ *total: \([0-9][0-9]*\) sessions.*/\1/p' | head -1)
echo "  sessions the deterministic pool holds: ${DET_SESS:-0}"
echo "  records the box says it emitted: before $BEFORE, after $DET_AFTER"
vppctl show bpa session | head -3 | sed 's/^/    /'

echo
echo "=========== 3. the control: a DYNAMIC pool on the same box ==========="
# Same exporter, same collector, same traffic. If this emits nothing either,
# the exporter was never working and section 2 measured the exporter rather
# than the mode.
vppctl bpa pool dyn inside 100.66.0.0/22 outside 200.150.0.32/32 block 16384 mode dynamic
for sub in 5 6 9; do
  vppctl packet-generator new "name d$sub limit 1 node ip4-input interface pg0 \
      size 60-60 data { UDP: 100.66.0.$sub -> 8.8.8.8 \
                        UDP: 40000 -> 53 \
                        incrementing 8 }" >/dev/null
  vppctl packet-generator enable >/dev/null; sleep 1
  vppctl packet-generator delete d$sub >/dev/null 2>&1
done
sleep 3
DYN_AFTER=$(exported)
echo "  records after the dynamic pool carried traffic: $DYN_AFTER"
# The collector prints when its window closes, not as each record lands, so a
# grep four seconds later reads an empty file on a box that is working. Wait
# for it to say something rather than for a number of seconds.
n=0
until grep -q "ALLOCATION" /tmp/collector.out 2>/dev/null || [ "$n" -ge 50 ]; do
  sleep 1; n=$((n+1))
done
echo "  waited ${n}s for the collector to report"
echo "  what the collector decoded:"
tail -6 /tmp/collector.out 2>/dev/null | sed 's/^/    /'
COLLECTED=$(grep -c "ALLOCATION" /tmp/collector.out 2>/dev/null)

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

# The exporter really is armed. Without this, zero records below would be the
# answer a box with no collector gives, and this lab would be reporting the
# configuration rather than the mode.
expect_in "the exporter is enabled and points at the collector" \
  "10.0.0.2" "$ARMED"

# The precondition for section 2: the deterministic pool really translated.
# Zero records over zero traffic proves nothing.
expect "the deterministic pool translated the subscribers" \
  test "${DET_SESS:-0}" -ge 1

# The claim.
expect "a deterministic pool exported nothing at all" \
  test "${DET_AFTER:-1}" -eq "${BEFORE:-0}"

# The control, and the lab is worthless without it.
expect "and the same exporter DID export for a dynamic pool" \
  test "${DYN_AFTER:-0}" -gt "${DET_AFTER:-0}"
expect "and a collector on the wire decoded those records" \
  test "${COLLECTED:-0}" -ge 1

verdict
