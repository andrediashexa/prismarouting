#!/bin/sh
# Where BFD runs, measured: FRR's bfdd behind linux_cp against VPP's own BFD (#559).
#
# The decision in docs/bfd.md rests on these numbers, so they are a lab and not
# a note: anyone can run it again on another machine and read the same table.
#
#   [netns peer] FRR bfdd (the far end, the same in every row)
#     veth-out 10.0.0.2 ------(veth)------ veth-vpp -> VPP -> vppwan0 10.0.0.1
#                                                       |        |
#                                   VPP's BFD (row B) --+        +-- bfdd (row A)
#
# For each transmit/receive interval, with detect-multiplier 3, the session is
# left up for MEASURE_SECS and the far end's "Session down events" counter is
# read: a session that flapped once at an interval is not stable at it. Every
# interval is measured idle and again with the dataplane's main thread busy
# forwarding a UDP flood the peer sends through it to a blackhole, because the
# punt path (row A) shares that thread with the traffic and VPP's BFD (row B)
# does not leave it.
#
# NOT in the suite: a full sweep is several minutes of waiting by design.
#   sh tools/lab.sh bfd-measure          the whole table
#   MEASURE_SECS=10 sh tools/lab.sh bfd-measure
set -u
. /src/lab/lib.sh
MEASURE_SECS=${MEASURE_SECS:-30}
INTERVALS=${INTERVALS:-300 100 50 20 10}
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up
ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
wait_vpp || exit 1
use_binary_api
HX="python3 /src/src/prismash/prismash.py"
vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
$HX -c "configure" -c "interface host-veth-vpp" -c "linux-name vppwan0" \
    -c "exit" -c "commit" 2>&1 | tail -1 | sed 's/^/  pair: /'
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
# A blackhole for the load: the peer's flood is routed by VPP to nowhere, so
# every packet costs the dataplane a lookup and a drop and none reaches Linux.
vppctl ip route add 192.0.2.0/24 via drop

mkdir -p /var/run/frr /etc/frr /var/run/frr/peer /etc/frr/peer
for d in zebra bfdd; do printf 'hostname box\n' > /etc/frr/$d.conf; done
for d in zebra bfdd; do printf 'hostname peer\n' > /etc/frr/peer/$d.conf; done
chown -R frr:frr /var/run/frr /etc/frr
start_frr bfdd
for d in zebra bfdd; do
  ip netns exec peer /usr/lib/frr/$d -d -N peer -A 127.0.0.1 -f /etc/frr/peer/$d.conf
done
ip netns exec peer ip route add 192.0.2.0/24 via 10.0.0.1
sleep 2

