#!/bin/sh
# Sessao BGP real sobre o TAP do linux_cp, com a rota aprendida chegando
# na FIB do VPP.
#
# Topologia, tudo num container:
#
#   [netns peer]  veth-out 10.0.0.2  AS 65001  anuncia 203.0.113.0/24
#         |
#      (veth)
#         |
#   [default]     veth-vpp -> VPP host-interface -> lcp -> vppctl0 10.0.0.1
#                 FRR AS 65000 faz peering pelo TAP
set -u
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up

# lado do "provedor upstream", num namespace separado
ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out
ip netns exec peer ip route add 203.0.113.0/24 dev lo

vpp -c /src/lab/startup-lcp.conf > /tmp/vpp.log 2>&1 &
n=0
until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP nao subiu"; tail -25 /tmp/vpp.log; exit 1; }

vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
vppctl lcp create host-veth-vpp host-if vppwan0
vppctl set interface ip address host-veth-vpp 10.0.0.1/24

# ---------------- FRR do HexaRouting (default netns) ----------------
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
chown -R frr:frr /var/run/frr /etc/frr
/usr/lib/frr/zebra -d -A 127.0.0.1 -f /etc/frr/zebra.conf
/usr/lib/frr/bgpd  -d -A 127.0.0.1 -f /etc/frr/bgpd.conf

# ---------------- FRR do upstream (netns peer) ----------------
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
chown -R frr:frr /var/run/frr/peer /etc/frr/peer
ip netns exec peer /usr/lib/frr/zebra -d -N peer -A 127.0.0.1 -f /etc/frr/peer/zebra.conf
ip netns exec peer /usr/lib/frr/bgpd  -d -N peer -A 127.0.0.1 -f /etc/frr/peer/bgpd.conf

echo "=== aguardando a sessao BGP estabelecer ==="
n=0
until vtysh -c "show bgp ipv4 unicast summary" 2>/dev/null | grep -q "Total number of neighbors" \
      && vtysh -c "show bgp ipv4 unicast" 2>/dev/null | grep -q "203.0.113.0/24" || [ "$n" -ge 40 ]; do
  sleep 1; n=$((n+1))
done
echo "(${n}s)"
vtysh -c "show bgp ipv4 unicast summary" 2>&1 | tail -6 | sed 's/^/  /'

echo
echo "=== rota aprendida por BGP ==="
vtysh -c "show bgp ipv4 unicast" 2>&1 | tail -5 | sed 's/^/  /'

echo
echo "=== chegou na FIB do Linux? ==="
ip route show 203.0.113.0/24 | sed 's/^/  /'

echo
echo "=== chegou na FIB do VPP? (o que importa) ==="
sleep 2
vppctl show ip fib 203.0.113.0/24 | grep -E "203.0.113|via|unicast-ip4" | head -6 | sed 's/^/  /'

echo
echo "=========== A ARMADILHA: pool que engole o proprio roteamento ==========="
echo "10.0.0.1 e o endereco de peering BGP desta caixa."
echo
echo "-- pool cujo conjunto publico contem 10.0.0.1 (deve ser RECUSADO):"
vppctl bpa pool ruim inside 100.64.0.0/22 outside 10.0.0.0/24 2>&1 | sed 's/^/  /'
echo
echo "-- pool em faixa que nao colide (deve ser ACEITO):"
vppctl bpa pool bom inside 100.64.0.0/22 outside 200.150.0.16/30 2>&1 | sed 's/^/  /'
vppctl show bpa pool | head -3 | sed 's/^/  /'
echo
echo "-- e o inverso: endereco de interface criado DEPOIS do pool"
vppctl bpa pool tardio inside 100.64.1.0/24 outside 198.51.100.0/28 2>&1 | sed 's/^/  /'
vppctl create loopback interface >/dev/null 2>&1
vppctl set interface state loop0 up
vppctl set interface ip address loop0 198.51.100.5/32
echo "  aviso emitido pelo VPP:"
vppctl show logging 2>/dev/null | grep -i "198.51.100.5" | tail -2 | sed 's/^/  /'
echo
echo "=========== sessao BGP continua de pe apos tudo isso ==========="
vtysh -c "show bgp ipv4 unicast summary" 2>&1 | grep -E "^10\.0\.0\.2" | sed 's/^/  /'
