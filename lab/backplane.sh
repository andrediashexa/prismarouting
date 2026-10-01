# OFF-RED-AT-LEAST: 5
# The containers' backplane on 128.0.0.0/8, in a table of its own (#447).
#
# 128.0.0.0/8 is real public space. The property this lab exists for is that
# putting the backplane on it takes NOTHING from the real 128.x: a subscriber
# behind this box still reaches a server at 128.66.0.10 through the CGNAT,
# the box itself still reaches it, and nothing on the backplane can be reached
# from outside or reach out. The dataplane is where that would break, because
# linux_nl copies Linux routes into VPP's FIB - so a 128/8 in the main table
# would reach every subscriber's packets.
#
#   [netns assinante] 100.64.0.5 --- ens18 [box] ens19 --- [netns upstream]
#                                     |                     198.51.100.1
#                                    bp0 (vrf prisma-bp)    128.66.0.10 (a server
#                                     |                      on real 128 space)
#                               [netns mgmt] 128.0.0.2
#
# MODE=off builds the same backplane WITHOUT the VRF - the bridge and its
# address in the main table - and the checks that say a real 128.x is still
# reachable must go red.
. /src/lab/lib.sh
. /src/lab/verdict.sh
set -u
MODE=${1:-on}
mkdir -p /run/vpp /vpp_plugins /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja >/dev/null
}
cd /src
ip route del default 2>/dev/null   # docker's own, which would outrank the static one
sysctl -qw net.ipv4.conf.all.arp_ignore=1 net.ipv4.conf.all.arp_announce=2

ip link add vin type veth peer name sub0
ip link add vout type veth peer name up0
ip link set vin up; ip link set vout up
ip netns add assinante; ip netns add upstream; ip netns add mgmt
ip link set sub0 netns assinante; ip link set up0 netns upstream
ip netns exec assinante sh -c 'ip link set lo up; ip link set sub0 up
  ip addr add 100.64.0.5/22 dev sub0; ip route add default via 100.64.0.1'
ip netns exec upstream sh -c 'ip link set lo up; ip link set up0 up
  ip addr add 198.51.100.1/30 dev up0
  ip link add srv type dummy; ip addr add 128.66.0.10/24 dev srv; ip link set srv up
  ip route add 203.0.113.0/28 via 198.51.100.2
  ip route add 128.0.0.0/8 via 198.51.100.2'
ip netns exec upstream sh -c 'nohup python3 -m http.server 80 --bind 128.66.0.10 >/dev/null 2>&1 &'

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
wait_vpp /tmp/vpp.log || exit 1
use_binary_api
vppctl create host-interface name vin >/dev/null
vppctl create host-interface name vout >/dev/null
vppctl set interface name host-vin ens18
vppctl set interface name host-vout ens19
mkdir -p /var/run/frr /etc/frr
echo "hostname prisma-routing" > /etc/frr/zebra.conf
chown -R frr:frr /var/run/frr /etc/frr
start_frr
HX="python3 /src/src/prismash/prismash.py"
$HX -c "configure" \
  -c "interface ens18" -c "linux-name ens18" -c "ipv4 address 100.64.0.1/22" -c "no shutdown" -c "exit" \
  -c "interface ens19" -c "linux-name ens19" -c "ipv4 address 198.51.100.2/30" -c "no shutdown" -c "exit" \
  -c "router static" -c "0.0.0.0/0 next-hop 198.51.100.1" -c "exit" \
  -c "nat" -c "cgnat" -c "pool residencial" -c "prefix-inside 100.64.0.0/22" \
  -c "prefix-outside 203.0.113.0/28" -c "block-size 512" -c "exit" \
  -c "interface ens18 inside" -c "interface ens19 outside" \
  -c "commit" >/tmp/setup.log 2>&1 || { cat /tmp/setup.log; }

echo "=========== the backplane, MODE=$MODE ==========="
if [ "$MODE" = off ]; then
  ip link add bp0 type bridge; ip addr add 128.0.0.1/8 dev bp0; ip link set bp0 up
else
  sh /src/appliance/prisma-backplane up
fi
ip link add bp-mgmt type veth peer name eth0-mgmt
ip link set bp-mgmt master bp0; ip link set bp-mgmt up
ip link set eth0-mgmt netns mgmt
ip netns exec mgmt sh -c 'ip link set lo up; ip link set eth0-mgmt up
  ip addr add 128.0.0.2/8 dev eth0-mgmt; ip route add default via 128.0.0.1'
sleep 2
CHECK=$(sh /src/appliance/prisma-backplane check 2>&1); CHECK_RC=$?
printf '%s\n' "$CHECK" | sed 's/^/  /'
MAIN=$(ip route show table main)
printf '%s\n' "$MAIN" | sed 's/^/  main: /'
ROUTE_GET=$(ip route get 128.66.0.10 2>&1)
echo "  ip route get 128.66.0.10: $ROUTE_GET"
VPP_FIB=$(vppctl show ip fib 128.66.0.10 2>&1)
printf '%s\n' "$VPP_FIB" | sed -n '1,8p' | sed 's/^/  vpp: /'

echo; echo "=========== a subscriber reaches a real 128.x through the CGNAT ==========="
SUB_HTTP=$(ip netns exec assinante python3 -c "
import socket;s=socket.create_connection(('128.66.0.10',80),5);s.send(b'GET / HTTP/1.0\r\n\r\n');print(s.recv(20))" 2>&1 | tail -1)
echo "  subscriber -> 128.66.0.10: $SUB_HTTP"
SUBSCRIBER=$($HX -c "show cgnat subscriber 100.64.0.5" 2>&1)
printf '%s\n' "$SUBSCRIBER" | sed 's/^/  /'

echo; echo "=========== the box itself reaches it ==========="
BOX_PING=$(ping -c 2 -W 2 128.66.0.10 2>&1 | tail -2)
echo "  $BOX_PING"

echo; echo "=========== the backplane talks inside itself, and only there ==========="
BP_PING=$(ip vrf exec prisma-bp ping -c 2 -W 2 128.0.0.2 2>&1 | tail -2)
[ "$MODE" = off ] && BP_PING=$(ping -c 2 -W 2 128.0.0.2 2>&1 | tail -2)
echo "  host -> 128.0.0.2 in the backplane: $BP_PING"
# Watched from the far side, because "no reply" is not "nothing left": a
# packet out of the backplane has no way back (nothing routes 128/8 to it), so
# a ping fails whether or not the packet escaped. The capture asks the
# question that matters - did anything from 128.0.0.2 reach the upstream.
( ip netns exec upstream timeout 8 tcpdump -lni up0 -c 5 src 128.0.0.2 \
    > /tmp/leak.cap 2>/dev/null & )
sleep 1
MGMT_OUT=$(ip netns exec mgmt ping -c 2 -W 2 128.66.0.10 2>&1 | tail -2)
echo "  mgmt -> 128.66.0.10 (must fail): $MGMT_OUT"
MGMT_UP=$(ip netns exec mgmt ping -c 2 -W 2 198.51.100.1 2>&1 | tail -2)
echo "  mgmt -> the upstream 198.51.100.1 (must fail): $MGMT_UP"
sleep 6
LEAKED=$(grep -c "IP 128.0.0.2" /tmp/leak.cap 2>/dev/null || true)
echo "  packets from 128.0.0.2 seen by the upstream: ${LEAKED:-0}"
IN_BP=$(ip netns exec upstream ping -c 2 -W 2 128.0.0.2 2>&1 | tail -2)
echo "  upstream -> 128.0.0.2 through the box (must fail): $IN_BP"

echo; echo "=========== the script says the same, and is idempotent ==========="
if [ "$MODE" != off ]; then
  AGAIN=$(sh /src/appliance/prisma-backplane up 2>&1); AGAIN_RC=$?
  echo "  second up: rc $AGAIN_RC"
else
  AGAIN_RC=0
fi

echo; echo "=========== VERDICT ==========="
expect "the backplane check finds no leak" test "$CHECK_RC" -eq 0
expect "the main table has no route through the backplane" \
  sh -c "! printf '%s\n' \"\$1\" | grep -q ' dev bp0'" _ "$MAIN"
expect_in "the box routes a real 128.x out of its real interface" "dev ens19" "$ROUTE_GET"
expect_in "a subscriber reaches a server on real 128 space, translated" \
  "HTTP/1.0 200" "$SUB_HTTP"
expect_in "  and the CGNAT carried it" "sessions" "$SUBSCRIBER"
expect_in "the box itself reaches it" " 0% packet loss" "$BOX_PING"
expect_in "the host reaches the management container on the backplane" \
  " 0% packet loss" "$BP_PING"
expect_in "the management container cannot reach out of the backplane" \
  "100% packet loss" "$MGMT_OUT"
expect_in "  nor the box's own upstream" "100% packet loss" "$MGMT_UP"
expect "  and nothing from it even left the box" test "${LEAKED:-0}" -eq 0
expect_in "nothing outside reaches the backplane through the box" \
  "100% packet loss" "$IN_BP"
expect "bringing it up twice changes nothing and fails nothing" \
  test "$AGAIN_RC" -eq 0
verdict
