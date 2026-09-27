#!/bin/sh
# A subscriber reaches the box itself, through the CGNAT (#470).
#
#  [netns assinante] 100.64.0.5 --- ens18 inside [box] ens19 outside --- [netns upstream]
#                                    100.64.0.1        198.51.100.2
#
# Reported on a 0.135.0 box: a VM behind `cgnat inside` could not reach the
# box, and could once `cgnat inside` was removed. The CGNAT translated traffic
# addressed to the box's own addresses, and ip4-local dropped it (the pool is
# a blackhole, so the translated source does not resolve).
#
# Asserted from the subscriber's side: a ping to the gateway (the inside
# address) and to the box's outside address are answered; the internet is
# still reached translated, which is the control that the CGNAT is on; and
# the node says how many packets it left alone as the box's own.
#
# No off mode: the fix is not a switch. Measured red once against the tree
# before it (see #470): both pings lost, and the counter absent.
set -u
. /src/lab/lib.sh
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja >/dev/null
}
cd /src
ip route del default 2>/dev/null

ip link add vin type veth peer name sub0
ip link add vout type veth peer name up0
ip link set vin up; ip link set vout up
ip netns add assinante; ip netns add upstream
ip link set sub0 netns assinante; ip link set up0 netns upstream
ip netns exec assinante sh -c 'ip link set lo up; ip link set sub0 up
  ip addr add 100.64.0.5/24 dev sub0; ip route add default via 100.64.0.1'
ip netns exec upstream sh -c 'ip link set lo up; ip link set up0 up
  ip addr add 198.51.100.1/30 dev up0
  ip link add srv type dummy; ip addr add 192.0.2.10/24 dev srv; ip link set srv up
  ip route add 203.0.113.0/28 via 198.51.100.2'

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
wait_vpp /tmp/vpp.log || exit 1
use_binary_api
vppctl create host-interface name vin >/dev/null
vppctl create host-interface name vout >/dev/null
vppctl set interface name host-vin ens18
vppctl set interface name host-vout ens19
HX="python3 /src/src/prismash/prismash.py"
$HX -c "configure" \
  -c "interface ens18" -c "ipv4 address 100.64.0.1/24" -c "no shutdown" -c "cgnat inside" -c "exit" \
  -c "interface ens19" -c "ipv4 address 198.51.100.2/30" -c "no shutdown" -c "cgnat outside" -c "exit" \
  -c "nat" -c "cgnat" -c "pool residencial" -c "inside 100.64.0.0/24" \
  -c "outside 203.0.113.0/28" -c "block-size 512" -c "exit" -c "exit" -c "exit" \
  -c "commit" 2>&1 | sed 's/^/    /' | tail -5
vppctl ip route add 0.0.0.0/0 via 198.51.100.1 ens19 >/dev/null
# THE POOL BLACKHOLED, as the reporting box had it and as the wiki tells every
# box to. Without it the defect hides: measured on the tree before the fix,
# the translated ping to the box left through the upstream, came back to the
# pool, was translated back and was answered - 0% loss over a path that
# should never have been taken. With it, ip4-local drops the translated
# source, which is what the maintainer's VM saw.
vppctl ip route add 203.0.113.0/28 via drop >/dev/null

GW=$(ip netns exec assinante ping -c3 -W1 100.64.0.1 2>&1 | grep "packet loss")
UPLINK=$(ip netns exec assinante ping -c3 -W1 198.51.100.2 2>&1 | grep "packet loss")
NET=$(ip netns exec assinante ping -c3 -W1 192.0.2.10 2>&1 | grep "packet loss")
COUNT=$(vppctl show errors 2>&1 | grep "packets to this box itself" | awk '{s+=$1} END{print s+0}')
TRANSLATED=$(vppctl show errors 2>&1 | grep "packets translated" | awk '{s+=$1} END{print s+0}')
echo "  gateway:  $GW"
echo "  uplink:   $UPLINK"
echo "  internet: $NET"
echo "  left untranslated as the box's own: $COUNT; translated: $TRANSLATED"

expect_in "the subscriber reaches its gateway, the box's inside address" " 0% packet loss" "$GW"
expect_in "and the box's outside address" " 0% packet loss" "$UPLINK"
expect_in "the internet is still reached (the CGNAT is on)" " 0% packet loss" "$NET"
[ "$TRANSLATED" -gt 0 ] \
  && echo "  [ ok ] and that traffic was translated: $TRANSLATED packet(s)" \
  || { echo "  [FAIL] nothing was translated, so the CGNAT is not on the path"
       VERDICT_FAILURES=$((VERDICT_FAILURES + 1)); }
# At least one ping's worth. Measured: 5 of the 6 echo requests counted here;
# all 6 were answered, so the sixth reached ip4-local by another way.
[ "$COUNT" -ge 3 ] \
  && echo "  [ ok ] the node counted the box's own packets: $COUNT" \
  || { echo "  [FAIL] the box's own packets were not counted as such: $COUNT"
       VERDICT_FAILURES=$((VERDICT_FAILURES + 1)); }
verdict
