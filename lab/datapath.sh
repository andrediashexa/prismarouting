#!/bin/sh
# Injeta pacotes com o packet-generator e confere a traducao contra o que
# ref/bpa_model.py preve. Nao precisa de NIC nem de DPDK.
#
# Injetamos em ip4-input em vez de ethernet-input: sem L2 nao ha MAC para
# bater, e o arco ip4-unicast (onde os nodes do bpa vivem) roda igual.
set -u
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
n=0
until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP nao subiu"; tail -20 /tmp/vpp.log; exit 1; }

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up
vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl set ip neighbor pg0 100.64.3.253 02:00:00:00:00:01
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0

vppctl bpa pool lab inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1

pg() { vppctl packet-generator new "$1"; }

echo "=========== IN2OUT: tres assinantes saindo ==========="
echo "esperado pelo modelo:"
echo "  100.64.0.5 -> 200.150.0.17 porta 1536   (bloco 1536-2047)"
echo "  100.64.0.6 -> 200.150.0.18 porta 1536   (bloco 1536-2047)"
echo "  100.64.0.9 -> 200.150.0.17 porta 2048   (bloco 2048-2559)"
echo "obtido:"
for sub in 5 6 9; do
  pg "name s$sub limit 1 node ip4-input interface pg0 size 60-60 \
      data { UDP: 100.64.0.$sub -> 8.8.8.8 \
             UDP: 12345 -> 53 \
             incrementing 8 }"
done
vppctl clear trace >/dev/null
vppctl trace add pg-input 20 >/dev/null
vppctl packet-generator enable >/dev/null
sleep 1
vppctl show trace | grep "BPA in2out" | sed 's/^/  /'

for sub in 5 6 9; do vppctl packet-generator delete s$sub; done

echo
echo "=========== OUT2IN: resposta legitima e pacote nao solicitado ==========="
echo "esperado: 200.150.0.17:1536 -> 100.64.0.5:12345 ; porta 9999 sem sessao"
echo "obtido:"
pg "name r1 limit 1 node ip4-input interface pg1 size 60-60 \
    data { UDP: 8.8.8.8 -> 200.150.0.17 \
           UDP: 53 -> 1536 \
           incrementing 8 }"
pg "name r2 limit 1 node ip4-input interface pg1 size 60-60 \
    data { UDP: 8.8.8.8 -> 200.150.0.17 \
           UDP: 53 -> 9999 \
           incrementing 8 }"
vppctl clear trace >/dev/null
vppctl trace add pg-input 20 >/dev/null
vppctl packet-generator enable >/dev/null
sleep 1
vppctl show trace | grep "BPA out2in" | sed 's/^/  /'

echo
echo "=========== contadores ==========="
vppctl show errors | grep -iE "bpa-(in2out|out2in)" | sed 's/^/  /'
echo
vppctl show bpa session

echo
echo "=========== EXPIRACAO: portas tem que voltar ==========="
vppctl bpa timeouts udp 2
echo "timeout udp reduzido para 2s; aguardando o processo de expiracao..."
n=0
until vppctl show bpa session | grep -q "^total: 0 sessoes" || [ "$n" -ge 15 ]; do
  sleep 1; n=$((n+1))
done
vppctl show bpa session
echo "(levou ${n}s)"

echo
echo "=========== reuso apos expiracao ==========="
pg "name again limit 1 node ip4-input interface pg0 size 60-60 \
    data { UDP: 100.64.0.5 -> 8.8.8.8 \
           UDP: 12345 -> 53 \
           incrementing 8 }"
vppctl clear trace >/dev/null
vppctl trace add pg-input 5 >/dev/null
vppctl packet-generator enable >/dev/null
sleep 1
echo "esperado: mesma porta 1536 devolvida ao pool e realocada"
vppctl show trace | grep "BPA in2out" | sed 's/^/  /'
