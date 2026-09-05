#!/bin/sh
# Modo dinamico com 2 workers: valida o particionamento do espaco de blocos.
#
# Alocacao de bloco seria estado compartilhado entre workers. Em vez de lock,
# o espaco e particionado: o worker w so aloca blocos com
# block_idx % n_workers == w. Como o in2out ja manda o assinante para
# sub_idx % n_workers, os dois batem, e o out2in (que resolve por
# block_idx % n_workers) cai no mesmo worker.
set -u
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

vpp -c /src/lab/startup-workers.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done

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

# 4 IPs publicos x 3 blocos = 12 blocos, 6 por worker
vppctl bpa pool din inside 100.64.0.0/22 outside 200.150.0.16/30 block 16384 mode dynamic
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1

echo "=== fatias de bloco por worker (12 blocos, 2 workers) ==="
vppctl show bpa blocks | sed 's/^/  /'

echo
echo "=== 4 assinantes saindo ==="
for sub in 5 6 9 12; do
  vppctl packet-generator new "name s$sub limit 1 node ip4-input interface pg0 size 60-60 \
      data { UDP: 100.64.0.$sub -> 8.8.8.8 \
             UDP: 40000 -> 53 \
             incrementing 8 }"
done
vppctl clear trace >/dev/null; vppctl trace add pg-input 20 >/dev/null
vppctl packet-generator enable >/dev/null; sleep 1
vppctl show trace | grep -E "BPA (handoff|in2out)" | sed 's/^/  /'
for sub in 5 6 9 12; do vppctl packet-generator delete s$sub >/dev/null 2>&1; done

echo
echo "=== distribuicao (assinante par -> worker 0, impar -> worker 1) ==="
vppctl show bpa blocks | sed 's/^/  /'
vppctl show bpa session | sed 's/^/  /'

echo
echo "=== volta: cada resposta tem que achar seu worker ==="
# 40000 nao cabe no bloco 1024-17407, entao o alocador entregou a primeira
# porta livre: 1024 em cada endereco publico.
echo "  esperado: .16->100.64.0.6 (t1)  .17->100.64.0.5 (t2)"
echo "            .18->100.64.0.12 (t1) .19->100.64.0.9 (t2)"
for t in "200.150.0.16 1024" "200.150.0.17 1024" "200.150.0.18 1024" "200.150.0.19 1024"; do
  set -- $t
  vppctl packet-generator new "name r$2$1 limit 1 node ip4-input interface pg1 size 60-60 \
      data { UDP: 8.8.8.8 -> $1 \
             UDP: 53 -> $2 \
             incrementing 8 }" 2>/dev/null
done
vppctl clear trace >/dev/null; vppctl trace add pg-input 20 >/dev/null
vppctl packet-generator enable >/dev/null; sleep 1
vppctl show trace | grep -E "BPA out2in" | sed 's/^/  /'
echo
echo "  pacotes sem sessao (deve ser 0):"
vppctl show errors | grep -iE "sem sessao|bloco nao alocado" | sed 's/^/    /' || echo "    nenhum"
