#!/bin/sh
# Etapa 1 do plano de controle: provar a cola entre VPP e Linux.
#
#   linux_cp: interface do VPP vira TAP no Linux, com endereco sincronizado
#   linux_nl: rota instalada no Linux aparece na FIB do VPP
#
# Se isto funciona, o FRR funciona: ele so enxerga netdev normal.
set -u
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

# par veth: um lado fica no Linux "de fora", o outro entra no VPP
ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-out up
ip link set veth-vpp up
ip addr add 10.0.0.2/24 dev veth-out 2>/dev/null

vpp -c /src/lab/startup-lcp.conf > /tmp/vpp.log 2>&1 &
n=0
until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP nao subiu"; tail -25 /tmp/vpp.log; exit 1; }

echo "=== plugins de plano de controle carregados ==="
vppctl show plugins | grep -iE "linux_cp|linux_nl" | sed 's/^/  /'

echo
echo "=== VPP absorve o veth e ganha um par no Linux ==="
vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
vppctl lcp create host-veth-vpp host-if vppctl0
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
vppctl show lcp | sed 's/^/  /'

echo
echo "=== o TAP existe no Linux, com o endereco sincronizado? ==="
echo "esperado: vppctl0 com 10.0.0.1/24, propagado pelo lcp-sync"
ip -4 addr show vppctl0 2>&1 | grep -E "vppctl0|inet" | sed 's/^/  /'

echo
echo "=== o TAP conversa: ping do Linux para o outro lado do veth ==="
ping -c 2 -W 2 10.0.0.2 2>&1 | tail -3 | sed 's/^/  /'

echo
echo "=== linux_nl: rota instalada no Linux tem que chegar na FIB do VPP ==="
ip route add 10.99.0.0/24 via 10.0.0.2 dev vppctl0 2>&1
echo "rota no Linux:"; ip route show 10.99.0.0/24 | sed 's/^/  /'
sleep 2
echo "FIB do VPP:"; vppctl show ip fib 10.99.0.0/24 | grep -E "10.99|via|unicast" | head -6 | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
expect "the TAP exists in Linux" ip link show vppctl0
expect "lcp-sync propagated the address" sh -c "ip -4 addr show vppctl0 | grep -q 10.0.0.1"
expect "linux_nl carried the route into the VPP FIB" sh -c "vppctl show ip fib 10.99.0.0/24 | grep -q '^10.99.0.0/24'"
verdict
