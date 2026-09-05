#!/bin/sh
# Exportacao IPFIX de eventos de bloco (RFC 8158), com coletor de verdade
# decodificando o que sai.
#
#   pg0/pg1  trafego de assinante, pool dinamico
#   veth     caminho ate o coletor, que roda no netns "peer"
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

vpp -c /src/lab/startup-lcp.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP nao subiu"; tail -20 /tmp/vpp.log; exit 1; }

# caminho ate o coletor
vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
# Sem vizinho resolvido, os primeiros registros IPFIX sao descartados
# enquanto o ARP nao completa. Para log de compliance isso e perda
# silenciosa, entao o caminho ate o coletor tem que estar pronto antes.
PEERMAC=$(ip netns exec peer cat /sys/class/net/veth-out/address)
vppctl set ip neighbor host-veth-vpp 10.0.0.2 $PEERMAC

# trafego de assinante
vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up
vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1

echo "=== configurando o exportador IPFIX ==="
vppctl set ipfix exporter collector 10.0.0.2 src 10.0.0.1 template-interval 3 port 4739
vppctl show ipfix exporter 2>&1 | head -6 | sed 's/^/  /'

vppctl bpa pool din inside 100.64.0.0/22 outside 200.150.0.16/32 block 16384 mode dynamic
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa block-holddown 4

echo
echo "=== habilitando o log de blocos ==="
vppctl bpa logging enable
vppctl show bpa logging | sed 's/^/  /'

# coletor no outro lado do fio
ip netns exec peer python3 /src/tools/ipfix_collector.py 4739 30 > /tmp/collector.out 2>&1 &
COL=$!
# captura o fio em paralelo: separa "VPP nao enviou" de "coletor perdeu"
ip netns exec peer timeout 28 tcpdump -nni veth-out -c 40 "udp port 4739" > /tmp/tcpdump.out 2>&1 &
sleep 2

echo
echo "=== 3 assinantes: 3 alocacoes de bloco ==="
for sub in 5 6 9; do
  vppctl packet-generator new "name s$sub limit 1 node ip4-input interface pg0 size 60-60 \
      data { UDP: 100.64.0.$sub -> 8.8.8.8 \
             UDP: 40000 -> 53 \
             incrementing 8 }"
done
vppctl packet-generator enable >/dev/null; sleep 1
for sub in 5 6 9; do vppctl packet-generator delete s$sub >/dev/null 2>&1; done

echo "=== 4o assinante: esgotamento (natEvent 12) ==="
vppctl packet-generator new "name s12 limit 1 node ip4-input interface pg0 size 60-60 \
    data { UDP: 100.64.0.12 -> 8.8.8.8 \
           UDP: 40000 -> 53 \
           incrementing 8 }"
vppctl packet-generator enable >/dev/null; sleep 1
vppctl packet-generator delete s12 >/dev/null 2>&1

echo
echo "=== forcando a expiracao para gerar as 3 liberacoes ==="
vppctl bpa timeouts udp 2
n=0
until vppctl show bpa blocks | grep -q "total: 0/3" || [ "$n" -ge 25 ]; do sleep 1; n=$((n+1)); done
echo "  blocos devolvidos em ${n}s"
vppctl show bpa blocks | tail -1 | sed 's/^/  /'

echo
echo "=== contadores do exportador ==="
vppctl show bpa logging | sed 's/^/  /'

wait $COL 2>/dev/null
echo
echo "=========== O QUE PASSOU NO FIO (tcpdump) ==========="
grep -c "IP " /tmp/tcpdump.out 2>/dev/null | sed 's/^/  pacotes udp\/4739: /'
grep "IP " /tmp/tcpdump.out 2>/dev/null | awk '{print $NF}' | sort | uniq -c | sed 's/^/  tamanhos: /'
echo
echo "=========== O QUE O COLETOR RECEBEU E DECODIFICOU ==========="
cat /tmp/collector.out

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
C=$(cat /tmp/collector.out)
expect_in "the collector decoded the template" "TEMPLATE 256" "$C"
expect_in "allocations arrived" "ALOCACAO" "$C"
expect_in "de-allocations arrived" "LIBERACAO" "$C"
expect_in "every allocation had its release" "Toda alocacao teve sua liberacao" "$C"
verdict
