#!/bin/sh
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api, wait_vpp
# OFF-RED-AT-LEAST: 2
# A routing policy exported into OSPF (#458 step 2), asserted on the
# neighbour's own OSPF table - not on the text of a configuration.
#
#  [default] the box                      [netns peer] an OSPF neighbour
#   host-veth-vpp 10.0.0.1/24  ------------  veth-out 10.0.0.2/24
#   static 10.99.1.0/24, 10.99.2.0/24 (blackhole)
#
# The box has two static routes and exports into OSPF with a policy that
# accepts only the first, from protocol static, and sets its metric to 77.
# Junos exports into an IGP nothing a policy did not accept, so the second
# route must not reach the neighbour, and neither must the box's connected
# networks.
#
# MODE=off commits the same box with FRR's plain `redistribute static`
# instead of the export: both routes reach the neighbour at the default
# metric, and every check that the policy chose something goes red.
MODE=${1:-on}
set -u
mkdir -p /run/vpp /vpp_plugins /etc/prisma-routing
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
wait_vpp /tmp/vpp.log || exit 1
use_binary_api
HX="python3 /src/src/prismash/prismash.py"

vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
vppctl set interface mtu 1500 host-veth-vpp

# ---------------- the box's FRR ----------------
mkdir -p /var/run/frr /etc/frr
printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
chown -R frr:frr /var/run/frr /etc/frr
start_frr ospfd isisd

# ---------------- the neighbour ----------------
mkdir -p /var/run/frr/peer /etc/frr/peer
cat > /etc/frr/peer/zebra.conf <<'EOF'
hostname neighbour
EOF
cat > /etc/frr/peer/ospfd.conf <<'EOF'
hostname neighbour
interface veth-out
 ip ospf network point-to-point
 ip ospf hello-interval 1
 ip ospf dead-interval 4
router ospf
 ospf router-id 10.0.0.2
 network 10.0.0.0/24 area 0
EOF
chown -R frr:frr /var/run/frr/peer /etc/frr/peer
for d in zebra ospfd; do
  ip netns exec peer /usr/lib/frr/$d -d -N peer -A 127.0.0.1 \
    -f /etc/frr/peer/$d.conf
done

. /src/lab/verdict.sh

# ---------------- the box, configured through the VTY ----------------
#
# Setup, not an assertion: the address, the adjacency and the two routes. The
# adjacency has to be loud when it fails - an export into an OSPF that never
# came up moves nothing, which reads exactly like a policy that exports
# nothing.
echo "=========== the adjacency and two static routes, through the VTY ==========="
SETUP=$($HX -c "configure" \
        -c "interface host-veth-vpp" -c "ipv4 address 10.0.0.1/24" \
        -c "ip ospf network point-to-point" -c "ip ospf hello-interval 1" \
        -c "ip ospf dead-interval 4" -c "exit" \
        -c "router static" \
        -c "10.99.1.0/24 next-hop blackhole" \
        -c "10.99.2.0/24 next-hop blackhole" -c "exit" \
        -c "router ospf" -c "ospf router-id 10.0.0.1" \
        -c "network 10.0.0.0/24 area 0" -c "exit" -c "commit" 2>&1)
echo "$SETUP" | tail -2 | sed 's/^/  /'
printf '%s' "$SETUP" | grep -q "commit complete" || {
  echo "the box's OSPF did not commit - nothing below would mean anything"
  printf '%s\n' "$SETUP" | tail -20 | sed 's/^/  /'
  exit 1
}
n=0
until ip netns exec peer vtysh -N peer -c "show ip ospf neighbor" 2>/dev/null \
        | grep -q "Full" || [ "$n" -ge 40 ]; do sleep 1; n=$((n + 1)); done
ADJ=$(ip netns exec peer vtysh -N peer -c "show ip ospf neighbor" 2>&1)
echo "$ADJ" | sed 's/^/  /'
printf '%s' "$ADJ" | grep -q "Full" || {
  echo "the adjacency never reached Full - nothing below would mean anything"
  exit 1
}

echo
echo "=========== the export ($MODE) ==========="
if [ "$MODE" = off ]; then
  EXP=$($HX -c "configure" -c "router ospf" -c "redistribute static" \
        -c "exit" -c "commit" 2>&1)
else
  EXP=$($HX -c "configure" -c "policy-options" \
        -c "prefix-list ONE" -c "10.99.1.0/24" -c "exit" \
        -c "policy-statement EXT" -c "term 1" \
        -c "from protocol static" -c "from prefix-list ONE" \
        -c "set metric 77" -c "then accept" -c "exit" -c "exit" -c "exit" \
        -c "router ospf" -c "export EXT" -c "exit" -c "commit" 2>&1)
fi
echo "$EXP" | tail -3 | sed 's/^/  /'
expect_in "the export commits" "commit complete" "$EXP"

# The neighbour's table, once the externals have had time to flood.
n=0
until ip netns exec peer vtysh -N peer -c "show ip ospf route" 2>/dev/null \
        | grep -q "10.99." || [ "$n" -ge 30 ]; do sleep 1; n=$((n + 1)); done
sleep 3
ROUTES=$(ip netns exec peer vtysh -N peer -c "show ip ospf route" 2>&1)
echo "  the neighbour's OSPF external routes:"
echo "$ROUTES" | sed -n '/External routing table/,$p' | sed 's/^/    /'
ONE=$(echo "$ROUTES" | grep "10.99.1.0/24")
echo "  10.99.1.0/24: ${ONE:-absent}"

echo
echo "=========== IS-IS takes the same export ==========="
# isisd is running and in no area, so this asserts that FRR accepts every
# statement an IS-IS export stands for - both families, both levels, ospf6
# for IPv6 - which is where a wrong keyword would surface.
ISIS=$($HX -c "configure" -c "router isis CORE" \
       -c "net 49.0001.0100.0000.0001.00" -c "export EXT" -c "exit" \
       -c "commit" 2>&1)
echo "$ISIS" | tail -2 | sed 's/^/  /'

echo; echo "=========== VERDICT ==========="
expect "the accepted static route reaches the neighbour" \
  test -n "$ONE"
expect "  with the metric the policy set" \
  sh -c "echo '$ONE' | grep -q 'cost 77\|/77\|77'"
expect_not_in "the static route no term accepted does not" "10.99.2.0/24" \
  "$ROUTES"
expect_not_in "and neither does a connected network, which no term names" \
  "E2 10.0.0.0/24" "$ROUTES"
if [ "$MODE" != off ]; then
  expect_in "an IS-IS export is taken by FRR as well" "commit complete" \
    "$ISIS"
fi
verdict
