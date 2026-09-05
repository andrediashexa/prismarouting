#!/bin/sh
# VTY do HexaRouting: uma CLI so para roteamento e CGNAT.
set -u
mkdir -p /run/vpp /vpp_plugins /etc/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
HX="python3 /src/src/hexash/hexash.py"
export HEXA_CONFIG=/etc/hexarouting/hexarouting.conf

ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up
ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out
ip netns exec peer ip link add dum type dummy
ip netns exec peer ip addr add 203.0.113.1/24 dev dum
ip netns exec peer ip link set dum up

vpp -c /src/lab/startup-lcp.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl create host-interface name veth-vpp >/dev/null
vppctl set interface state host-veth-vpp up
vppctl lcp create host-veth-vpp host-if vppwan0
vppctl set interface mtu 1500 host-veth-vpp
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface state pg0 up
vppctl set interface ip address pg0 100.64.3.254/22

mkdir -p /var/run/frr /etc/frr
printf 'hostname hexarouting\n' > /etc/frr/zebra.conf
printf 'hostname hexarouting\n' > /etc/frr/bgpd.conf
chown -R frr:frr /var/run/frr /etc/frr
/usr/lib/frr/zebra -d -A 127.0.0.1 -f /etc/frr/zebra.conf
/usr/lib/frr/bgpd  -d -A 127.0.0.1 -f /etc/frr/bgpd.conf

mkdir -p /var/run/frr/peer /etc/frr/peer
printf 'hostname upstream\n' > /etc/frr/peer/zebra.conf
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
chown -R frr:frr /var/run/frr/peer /etc/frr/peer
ip netns exec peer /usr/lib/frr/zebra -d -N peer -A 127.0.0.1 -f /etc/frr/peer/zebra.conf
ip netns exec peer /usr/lib/frr/bgpd  -d -N peer -A 127.0.0.1 -f /etc/frr/peer/bgpd.conf
sleep 2

echo "=========== CGNAT pela VTY ==========="
# Under the candidate model nothing reaches the device until "commit".
# This lab used to end at "exit", which now discards the candidate - the
# check that caught it is the reason each lab states its own verdict.
$HX -c "configure" \
    -c "cgnat pool provedor-a" \
    -c "inside 100.64.0.0/22" \
    -c "outside 200.150.0.0/24" \
    -c "outside 45.170.16.0/26" \
    -c "block-size 512" \
    -c "policy interleaved" \
    -c "mode deterministic" \
    -c "exit" \
    -c "cgnat interface pg0 inside" \
    -c "cgnat timeout udp 300" \
    -c "commit"
echo "  -- o dataplane recebeu?"
vppctl show bpa pool | sed 's/^/    /'

echo
echo "=========== ROTEAMENTO pela MESMA VTY ==========="
$HX -c "configure" \
    -c "router bgp 65000" \
    -c "bgp router-id 10.0.0.1" \
    -c "no bgp ebgp-requires-policy" \
    -c "neighbor 10.0.0.2 remote-as 65001" \
    -c "commit"
echo "  -- o FRR recebeu?"
vtysh -c "show run" 2>/dev/null | grep -A4 "router bgp" | sed 's/^/    /'

echo
echo "=========== sessao BGP sobe e a rota chega na FIB do VPP ==========="
n=0
until vppctl show ip fib 203.0.113.0/24 2>/dev/null | grep -q "^203.0.113.0/24" || [ "$n" -ge 40 ]; do sleep 1; n=$((n+1)); done
echo "  (${n}s)"
vppctl show ip fib 203.0.113.0/24 | grep -oE "ipv4 via [0-9.]+ [a-z0-9-]+" | head -1 | sed 's/^/    /'

echo
echo "=========== show running-config: os dois mundos ==========="
$HX -c "show running-config" 2>/dev/null | grep -vE "^(Building|Current|frr version|frr defaults|hostname|log |service |!$)" | head -22 | sed 's/^/  /'

echo
echo "=========== pool invalido tem que ser recusado e NAO entrar na config ==========="
$HX -c "configure" \
    -c "cgnat pool ruim" \
    -c "inside 100.64.0.0/22" \
    -c "outside 10.0.0.0/24" \
    -c "exit" \
    -c "commit" 2>&1 | sed 's/^/  /'
echo "  -- 'ruim' aparece na running-config? (nao deve)"
$HX -c "show running-config" 2>/dev/null | grep -c "cgnat pool ruim" | sed 's/^/    ocorrencias: /'

echo
echo "=========== write memory e recarga ==========="
$HX -c "save" 2>&1 | head -2 | sed 's/^/  /'
echo "  -- arquivo gravado:"
cat "$HEXA_CONFIG" | sed 's/^/    /'
echo "  -- apagando o pool do dataplane e recarregando do arquivo"
vppctl bpa pool provedor-a del
vppctl show bpa pool | head -1 | sed 's/^/    antes: /'
$HX --load "$HEXA_CONFIG"
vppctl show bpa pool | head -2 | sed 's/^/    depois: /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
R=$($HX -c "show running-config" 2>/dev/null)
expect_in "CGNAT is in the running config" "cgnat pool" "$R"
expect_in "routing is in the same running config" "router bgp 65000" "$R"
expect_not_in "the refused pool stayed out" "cgnat pool ruim" "$R"
expect "the BGP route reached the VPP FIB" sh -c "vppctl show ip fib 203.0.113.0/24 | grep -q '^203.0.113.0/24'"
verdict
