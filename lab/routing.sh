#!/bin/sh
# Plano de controle completo: BGP, OSPF e IS-IS sobre o TAP do linux_cp,
# with all three learned routes reaching the VPP FIB.
#
#   [netns peer] AS 65001 / OSPF / IS-IS       [default] HexaRouting
#     dum-bgp   203.0.113.0/24  -- BGP  -->
#     dum-ospf  198.18.0.0/24   -- OSPF -->    FRR -> kernel -> linux_nl
#     dum-isis  198.19.0.0/24   -- ISIS -->                  -> FIB do VPP
#     veth-out 10.0.0.2 ------(veth)------ veth-vpp -> VPP -> vppwan0 10.0.0.1
#
# IS-IS is the test that matters for punt: it does not run over IP, it uses CLNS
# direto sobre o enlace. Se o caminho de punt so entendesse IPv4/IPv6, BGP e
# OSPF subiriam e o IS-IS ficaria mudo.
set -u
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
# one prefix per protocol, so there is no doubt about which brought which
for d in bgp:203.0.113.1/24 ospf:198.18.0.1/24 isis:198.19.0.1/24; do
  n=${d%%:*}; a=${d##*:}
  ip netns exec peer ip link add dum-$n type dummy
  ip netns exec peer ip addr add $a dev dum-$n
  ip netns exec peer ip link set dum-$n up
done

vpp -c /src/lab/startup-lcp.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -25 /tmp/vpp.log; exit 1; }

vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
vppctl lcp create host-veth-vpp host-if vppwan0

# The TAP inherits MTU 9000 while the wire is at 1500. OSPF compares MTU in DD
# packets, so the adjacency stalls in Exchange without saying why. MTU has to
# be explicit, never inherited.
vppctl set interface mtu 1500 host-veth-vpp

# IS-IS does not run over IP: it is OSI NLPID 0x83 straight over the link.
# Without this the frame reaches VPP, lands in osi-input as "unknown osi
# protocol" and is dropped - BGP and OSPF come up fine and only IS-IS is mute.
vppctl lcp osi-proto enable 0x83

vppctl set interface ip address host-veth-vpp 10.0.0.1/24

# ---------------- HexaRouting ----------------
mkdir -p /var/run/frr /etc/frr
cat > /etc/frr/zebra.conf <<'EOF'
hostname hexarouting
EOF
cat > /etc/frr/bgpd.conf <<'EOF'
hostname hexarouting
router bgp 65000
 bgp router-id 10.0.0.1
 no bgp ebgp-requires-policy
 neighbor 10.0.0.2 remote-as 65001
 address-family ipv4 unicast
  neighbor 10.0.0.2 activate
 exit-address-family
EOF
cat > /etc/frr/ospfd.conf <<'EOF'
hostname hexarouting
router ospf
 ospf router-id 10.0.0.1
 network 10.0.0.0/24 area 0
EOF
cat > /etc/frr/isisd.conf <<'EOF'
hostname hexarouting
interface vppwan0
 ip router isis HEXA
exit
router isis HEXA
 net 49.0001.0000.0000.0001.00
 is-type level-2-only
EOF
chown -R frr:frr /var/run/frr /etc/frr
for d in zebra bgpd ospfd isisd; do
  /usr/lib/frr/$d -d -A 127.0.0.1 -f /etc/frr/$d.conf
done

# ---------------- upstream ----------------
mkdir -p /var/run/frr/peer /etc/frr/peer
cat > /etc/frr/peer/zebra.conf <<'EOF'
hostname upstream
EOF
cat > /etc/frr/peer/bgpd.conf <<'EOF'
hostname upstream
router bgp 65001
 bgp router-id 10.0.0.2
 no bgp ebgp-requires-policy
 neighbor 10.0.0.1 remote-as 65000
 address-family ipv4 unicast
  network 203.0.113.0/24
  neighbor 10.0.0.1 activate
 exit-address-family
EOF
cat > /etc/frr/peer/ospfd.conf <<'EOF'
hostname upstream
router ospf
 ospf router-id 10.0.0.2
 network 10.0.0.0/24 area 0
 network 198.18.0.0/24 area 0
EOF
cat > /etc/frr/peer/isisd.conf <<'EOF'
hostname upstream
interface veth-out
 ip router isis HEXA
exit
interface dum-isis
 ip router isis HEXA
exit
router isis HEXA
 net 49.0001.0000.0000.0002.00
 is-type level-2-only
EOF
chown -R frr:frr /var/run/frr/peer /etc/frr/peer
for d in zebra bgpd ospfd isisd; do
  ip netns exec peer /usr/lib/frr/$d -d -N peer -A 127.0.0.1 -f /etc/frr/peer/$d.conf
done

echo "=== aguardando as tres rotas chegarem na FIB do VPP ==="
n=0
while [ "$n" -lt 90 ]; do
  ok=0
  for pfx in 203.0.113.0/24 198.18.0.0/24 198.19.0.0/24; do
    vppctl show ip fib "$pfx" 2>/dev/null | grep -q "^$pfx" && ok=$((ok+1))
  done
  [ "$ok" -eq 3 ] && break
  sleep 1; n=$((n+1))
done
echo "(${n}s, $ok de 3)"

echo
echo "=== adjacencias ==="
printf 'BGP   : '; vtysh -c "show bgp ipv4 unicast summary" 2>/dev/null | grep -E "^10\.0\.0\.2" | awk '{print "peer "$1" AS "$3" up "$9" prefixos "$10}'
printf 'OSPF  : '; vtysh -c "show ip ospf neighbor" 2>/dev/null | grep -E "^10\.0\.0\.2" | awk '{print "neighbour "$1" state "$3}'
printf 'IS-IS : '; vtysh -c "show isis neighbor" 2>/dev/null | grep -iE "upstream" | awk '{print "neighbour "$1" state "$4" interface "$2}'

echo
echo "=== routes in the Linux FIB (installed by FRR) ==="
ip route show | grep -E "203.0.113|198.18|198.19" | sed 's/^/  /'

echo
echo "=== routes in the VPP FIB (what actually matters) ==="
for pfx in 203.0.113.0/24 198.18.0.0/24 198.19.0.0/24; do
  printf '  %-16s ' "$pfx"
  vppctl show ip fib "$pfx" 2>/dev/null | grep -oE "ipv4 via [0-9.]+ [a-z0-9-]+" | head -1 || echo "AUSENTE"
done

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
for pfx in 203.0.113.0/24 198.18.0.0/24 198.19.0.0/24; do
  expect "$pfx reached the VPP FIB" sh -c "vppctl show ip fib $pfx | grep -q '^$pfx'"
done
expect "IS-IS adjacency is up" sh -c "vtysh -c 'show isis neighbor' 2>/dev/null | grep -qi up"

# --- MTU committed through the VTY reaches BOTH sides ---
# The lab sets 1500 by hand at the top, which is what makes OSPF come up here.
# This proves the operator can now do the same through the configuration, and
# that it lands on the VPP interface AND its TAP. Setting only the TAP leaves
# the packet path untouched, which is the half that matters and the half a
# quick test would miss.
HX="python3 /src/src/hexash/hexash.py"
$HX -c "configure" -c "interface host-veth-vpp" -c "mtu 1400" -c "exit" \
    -c "commit" > /tmp/mtu.log 2>&1 || true
tail -3 /tmp/mtu.log | sed 's/^/  commit: /'
expect "the dataplane interface took the committed MTU"   sh -c "vppctl show interface host-veth-vpp | grep -q 1400"
expect "and so did its TAP" sh -c "ip link show vppwan0 | grep -q 'mtu 1400'"
expect "the MTU is in the running configuration"   sh -c "$HX -c 'show running-config' | grep -q 'mtu 1400'"

# Put it back, or the OSPF assertions above would be checking a link this test
# just broke.
$HX -c "configure" -c "interface host-veth-vpp" -c "mtu 1500" -c "exit" \
    -c "commit" >/dev/null 2>&1 || true
verdict
