#!/bin/sh
# OFF-RED-AT-LEAST: 5
# LACP (#455): a bundle of two dataplane ports against a Linux 802.3ad bond.
#
#  [default] VPP bond ae0 10.9.0.1/24     [netns peer] bond0 10.9.0.2/24
#    member host-m1  -------- veth ------- m1p
#    member host-m2  -------- veth ------- m2p
#
# Asserted on the partner's own view (the Linux bond's 802.3ad state) and on
# packets across the bundle, with one member cut. MODE=off builds the same
# box with the address on one port and no bundle: the partner never gets an
# LACP partner, and the checks that say so go red.
MODE=${1:-on}
set -u
. /src/lab/lib.sh
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja >/dev/null
}
cd /src

ip netns add peer
for i in 1 2; do
  ip link add m$i type veth peer name m${i}p
  ip link set m$i up
  ip link set m${i}p netns peer
done
ip netns exec peer ip link add bond0 type bond mode 802.3ad lacp_rate fast \
  xmit_hash_policy layer3+4 miimon 100
for i in 1 2; do
  ip netns exec peer ip link set m${i}p down
  ip netns exec peer ip link set m${i}p master bond0
done
ip netns exec peer ip link set bond0 up
ip netns exec peer ip addr add 10.9.0.2/24 dev bond0

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
wait_vpp /tmp/vpp.log || exit 1
for i in 1 2; do
  vppctl create host-interface name m$i >/dev/null
  vppctl set interface state host-m$i up
done
HX="python3 /src/src/prismash/prismash.py"
use_binary_api
mkdir -p /var/run/frr /etc/frr /etc/prisma-routing
printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
chown -R frr:frr /var/run/frr /etc/frr
start_frr
# Configured through the VTY, the way an operator does: the bundle is named,
# its members given, and the address put on it. MODE=off configures the same
# address on the first port alone and no bundle.
if [ "$MODE" = off ]; then
  CFG=$($HX -c configure -c "interface host-m1" \
        -c "ipv4 address 10.9.0.1/24" -c exit -c commit 2>&1)
else
  CFG=$($HX -c configure -c "interface ae0" -c "member host-m1" \
        -c "member host-m2" -c "lacp rate fast" \
        -c "ipv4 address 10.9.0.1/24" -c exit -c commit 2>&1)
fi
echo "$CFG" | tail -3 | sed 's/^/  /'
expect_in "the commit completes" "commit complete" "$CFG"
sleep 8

echo "=========== the bundle ($MODE) ==========="
vc show bond details | sed 's/^/  /'
LACP=$($HX -c "show lacp" 2>&1)
echo "$LACP" | sed 's/^/  /'
PART=$(ip netns exec peer cat /proc/net/bonding/bond0)
echo "$PART" | sed -n '/802.3ad info/,/^$/p;/Slave Interface/,/Partner Mac/p' | sed 's/^/  /' | head -40
PING=$(ip netns exec peer ping -c 5 -i 0.2 -W 1 10.9.0.1 2>&1 | tail -2)
echo "  ping: $PING"

echo; echo "=========== one member cut ==========="
ip link set m1 down
sleep 4
PING2=$(ip netns exec peer ping -c 5 -i 0.2 -W 1 10.9.0.1 2>&1 | tail -2)
echo "  ping: $PING2"
vc show bond | sed 's/^/  /'
RUN=$($HX -c "show running-config" 2>&1)

echo; echo "=========== VERDICT ==========="
expect "the partner sees an LACP partner on both members" \
  sh -c "echo '$PART' | grep -c 'Partner Mac Address: 00:00:00:00:00:00' | grep -qx 0 && echo '$PART' | grep -q 'Partner Mac Address'"
expect_in "the partner's aggregator has both ports" "Number of ports: 2" "$PART"
expect_in "the box's own show lacp says both members distribute" \
  "MUX-state: COLLECTING_DISTRIBUTING" "$LACP"
expect_in "packets cross the bundle" " 0% packet loss" "$PING"
expect_in "and still cross it with one member cut" " 0% packet loss" "$PING2"
expect_said "the running configuration reads the bundle back" \
  "interface ae0 member host-m1 member host-m2" "$RUN"
verdict
