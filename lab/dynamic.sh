#!/bin/sh
# Modo dinamico (PBA): bloco alocado sob demanda, devolvido apos hold-down.
#
# Pool proposital de borda: 1024 assinantes possiveis contra 3 blocos.
#   64512 portas / bloco de 16384 = 3 blocos num unico IP publico
# Blocos previstos, em ordem de alocacao: portas 1024, 17408, 33792.
set -u
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
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

vppctl bpa pool din inside 100.64.0.0/22 outside 200.150.0.16/32 block 16384 mode dynamic
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa block-holddown 5
vppctl show bpa pool | sed 's/^/  /'

pg() { vppctl packet-generator new "$1"; }
send() {
  pg "name s$1 limit 1 node ip4-input interface pg0 size 60-60 \
      data { UDP: 100.64.0.$1 -> 8.8.8.8 \
             UDP: 1234$2 -> 53 \
             incrementing 8 }"
  vppctl clear trace >/dev/null; vppctl trace add pg-input 5 >/dev/null
  vppctl packet-generator enable >/dev/null; sleep 1
  vppctl show trace | grep "BPA in2out" | sed 's/^/    /'
  vppctl packet-generator delete s$1 >/dev/null 2>&1
}

echo
echo "=========== 3 assinantes pegam os 3 blocos ==========="
for sub in 5 6 9; do echo "  assinante 100.64.0.$sub:"; send $sub 5; done

echo
echo "=========== o mesmo assinante NAO deve trocar de bloco ==========="
echo "  100.64.0.5 de novo, porta de origem diferente:"
send 5 6

echo
echo "=========== 4o assinante: nao ha bloco livre ==========="
echo "  100.64.0.12:"
send 12 5
echo "  contador de recusa:"
vppctl show errors | grep -i "sem bloco livre" | sed 's/^/    /'

echo
echo "=========== estado dos blocos ==========="
vppctl show bpa blocks | sed 's/^/  /'

echo
echo "=========== volta da internet ==========="
# A preservacao de porta manteve 12345, que cai dentro do bloco 1024-17407
# do primeiro assinante. E a porta real, nao o inicio do bloco.
echo "  esperado: 200.150.0.16:12345 -> 100.64.0.5:12345"
pg "name r1 limit 1 node ip4-input interface pg1 size 60-60 \
    data { UDP: 8.8.8.8 -> 200.150.0.16 \
           UDP: 53 -> 12345 \
           incrementing 8 }"
vppctl clear trace >/dev/null; vppctl trace add pg-input 5 >/dev/null
vppctl packet-generator enable >/dev/null; sleep 1
vppctl show trace | grep "BPA out2in" | sed 's/^/    /'
vppctl packet-generator delete r1 >/dev/null 2>&1

echo
echo "=========== hold-down: blocos voltam para o pool ==========="
# So agora encurtamos o timeout: se fosse no inicio, as sessoes morreriam
# no meio dos testes anteriores.
vppctl bpa timeouts udp 3
echo "  sessoes expiram em 3s, blocos ficam 5s em hold-down"
n=0
until vppctl show bpa blocks | grep -q "total: 0/3" || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
echo "  (${n}s)"
vppctl show bpa blocks | sed 's/^/  /'

echo
echo "=========== agora o 4o assinante consegue ==========="
echo "  100.64.0.12:"
send 12 5
