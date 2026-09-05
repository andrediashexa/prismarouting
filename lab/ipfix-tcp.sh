#!/bin/sh
# Transporte TCP do log: o teste que importa e derrubar o coletor no meio.
#
# Em UDP, os eventos gerados enquanto o coletor esta fora somem em silencio -
# e perder um natEvent 17 recria a lacuna que faz um oficio apontar o
# assinante errado. Aqui eles tem que ir para disco e voltar na reconexao.
set -u
mkdir -p /run/vpp /vpp_plugins /var/log/hexarouting
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

vpp -c /src/lab/startup-lcp.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl create host-interface name veth-vpp >/dev/null
vppctl set interface state host-veth-vpp up
# O exportador TCP usa socket do kernel, nao o dataplane do VPP: o coletor
# tem que ser alcancavel pela pilha do Linux. Numa caixa real quem da esse
# caminho e o linux_cp, espelhando a interface como TAP.
vppctl lcp create host-veth-vpp host-if vppmgmt0
vppctl set interface mtu 1500 host-veth-vpp
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
PEERMAC=$(ip netns exec peer cat /sys/class/net/veth-out/address)
vppctl set ip neighbor host-veth-vpp 10.0.0.2 $PEERMAC

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up; vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl bpa pool din inside 100.64.0.0/22 outside 200.150.0.16/32 block 16384 mode dynamic
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa block-holddown 3
vppctl bpa logging tcp 10.0.0.2 port 4740

burst() {
  for sub in "$@"; do
    vppctl packet-generator new "name b$sub limit 1 node ip4-input interface pg0 size 60-60 \
        data { UDP: 100.64.0.$sub -> 8.8.8.8 UDP: 40000 -> 53 incrementing 8 }"
  done
  vppctl packet-generator enable >/dev/null; sleep 1
  for sub in "$@"; do vppctl packet-generator delete b$sub >/dev/null 2>&1; done
}

echo "=========== FASE 1: coletor no ar ==========="
ip netns exec peer python3 /src/tools/ipfix_collector.py tcp 4740 12 > /tmp/c1.out 2>&1 &
C1=$!
sleep 3
burst 5
sleep 3
vppctl show bpa logging | grep -A5 "transporte TCP" | sed 's/^/  /'
wait $C1
echo "  --- recebido (saida crua do coletor) ---"
cat /tmp/c1.out | sed 's/^/    /'

echo
echo "=========== FASE 2: coletor DERRUBADO, eventos continuam ==========="
echo "  (sem coletor: os eventos tem que ir para o transbordo em disco)"
vppctl bpa timeouts udp 2
burst 6 9
sleep 8
vppctl show bpa logging | grep -E "conectado|transbordo|pendentes" | sed 's/^/  /'
ls -l /var/log/hexarouting/ipfix-spill.bin 2>/dev/null | sed 's/^/  /' || echo "  (nada em disco ainda: buffer em memoria ainda cabe)"

echo
echo "=========== FASE 3: coletor volta ==========="
ip netns exec peer python3 /src/tools/ipfix_collector.py tcp 4740 15 > /tmp/c2.out 2>&1 &
C2=$!
wait $C2
echo "  --- recebido apos a reconexao (saida crua) ---"
cat /tmp/c2.out | sed 's/^/    /'
echo
vppctl show bpa logging | grep -A5 "transporte TCP" | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
C2=$(cat /tmp/c2.out)
expect_in "records arrived after the collector came back" "LIBERACAO" "$C2"
expect_in "the template was resent on reconnect" "TEMPLATE 256" "$C2"
expect_not_in "nothing was left pending" "270 pendentes" "$(vppctl show bpa logging)"
verdict
