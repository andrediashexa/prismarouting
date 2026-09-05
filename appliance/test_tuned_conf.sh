#!/bin/sh
# O startup.conf TUNADO passa pelo parser do VPP?
#
# O test_startup_conf.sh so exercita o modo de primeiro boot, porque o
# container nao tem hugepage nem isolcpus - e primeiro boot e justamente o
# perfil que NAO tem cpu{}, dpdk{} nem page-size default-hugepage. Ou seja: as
# stanzas que so aparecem em producao nunca viam um parser. Aqui a gente
# reserva hugepage de verdade, gera o perfil completo e sobe o VPP com ele.
set -u
FAILED=0
ok()  { echo "  $1"; }
bad() { echo "  FALHA: $1"; FAILED=$((FAILED + 1)); }

PAGES=${PAGES:-1400}            # 2 MB cada: ~2,8 GB, cobre main-heap 2G
NR=/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages

[ -w "$NR" ] || { echo "SKIP: sem $NR gravavel (precisa de --privileged)"; exit 0; }
before=$(cat "$NR")
echo "$PAGES" > "$NR" 2>/dev/null || true
got=$(cat "$NR")
if [ "$got" -lt 1200 ]; then
  echo "SKIP: so consegui $got hugepages de 2M (queria $PAGES); memoria do host"
  echo "$before" > "$NR" 2>/dev/null || true
  exit 0
fi
ok "reservadas $got hugepages de 2M"
mkdir -p /dev/hugepages
mountpoint -q /dev/hugepages || mount -t hugetlbfs none /dev/hugepages 2>/dev/null

# --- topologia sintetica que faz o tuner acreditar que o kernel esta tunado
ROOT=$(mktemp -d)
mk() { mkdir -p "$(dirname "$1")"; printf '%s\n' "$2" > "$1"; }
for n in 0 1 2; do
  mk "$ROOT/sys/devices/system/cpu/cpu$n/topology/physical_package_id" 0
  mk "$ROOT/sys/devices/system/cpu/cpu$n/topology/core_id" "$n"
  mk "$ROOT/sys/devices/system/cpu/cpu$n/topology/thread_siblings_list" "$n"
done
mk "$ROOT/sys/devices/system/node/node0/cpulist" "0-2"
mk "$ROOT/sys/devices/system/cpu/isolated" "1-2"
mk "$ROOT/sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages" 4
mk "$ROOT/sys/devices/pci0000:00/0000:3b:00.0/numa_node" 0
mkdir -p "$ROOT/sys/class/net/eth0"
ln -s "$ROOT/sys/devices/pci0000:00/0000:3b:00.0" "$ROOT/sys/class/net/eth0/device"

# Instala como o produto instala: o plugin precisa estar no caminho que o
# VPP varre, senao "bpa pool" volta como "unknown input" e o teste culpa o
# startup.conf por um problema que nao e dele.
mkdir -p /run/vpp /vpp_plugins /var/log/vpp /etc/hexarouting
sh /src/appliance/install.sh >/dev/null 2>&1 || bad "install.sh falhou"
cp /src/appliance/etc/vpp-base.conf /etc/hexarouting/vpp-base.conf
python3 /src/appliance/hexarouting-tune --sysfs-root "$ROOT" --interface eth0 \
  --subscribers 10000 --write >/dev/null 2>&1 || bad "o tuner saiu com erro"

CONF=/etc/vpp/startup.conf
grep -q "FIRST BOOT MODE" "$CONF" && bad "caiu em primeiro boot: o teste nao testa nada assim"
grep -q "corelist-workers 1-2" "$CONF" || bad "sem pinning: perfil errado"
grep -q "dev 0000:3b:00.0"     "$CONF" || bad "sem o device PCI: perfil errado"
grep -q "num-rx-queues 2"      "$CONF" || bad "sem fila por worker: perfil errado"
grep -q "page-size default-hugepage" "$CONF" || bad "sem hugepage: perfil errado"
ok "perfil gerado e o completo (pinning + dpdk + hugepage)"

echo "=== o VPP aceita esse arquivo? ==="
mkdir -p /var/log/vpp /run/vpp
(vpp -c "$CONF" > /tmp/vpp-tuned.log 2>&1 &) 
i=0
while [ $i -lt 30 ]; do
  vppctl -s /run/vpp/cli.sock show version >/dev/null 2>&1 && break
  i=$((i + 1)); sleep 1
done
if vppctl -s /run/vpp/cli.sock show version >/dev/null 2>&1; then
  ok "subiu em ${i}s: $(vppctl -s /run/vpp/cli.sock show version | head -1 | cut -c1-40)"
else
  bad "nao subiu; log:"
  tail -20 /tmp/vpp-tuned.log | sed 's/^/    /'
fi

# Um erro de sintaxe no startup.conf sai como "unknown input" - e o VPP pode
# ate subir ignorando o resto do arquivo, entao nao basta ele estar vivo.
if grep -qi "unknown input\|unknown parameter\|parse error" /tmp/vpp-tuned.log; then
  bad "o parser recusou algo:"
  grep -i "unknown input\|unknown parameter\|parse error" /tmp/vpp-tuned.log | sed 's/^/    /'
else
  ok "nenhuma stanza recusada pelo parser"
fi

threads=$(vppctl -s /run/vpp/cli.sock show threads 2>/dev/null | grep -c "vpp_wk_")
if [ "${threads:-0}" -eq 2 ]; then
  ok "2 workers de fato criados e pinados"
else
  bad "esperava 2 workers, o VPP reporta ${threads:-0}"
  vppctl -s /run/vpp/cli.sock show threads 2>/dev/null | sed 's/^/    /'
fi

vppctl -s /run/vpp/cli.sock bpa pool tuned inside 100.64.0.0/22 \
    outside 200.150.0.16/30 block 512 2>&1 | sed "s/^/    pool: /"
if vppctl -s /run/vpp/cli.sock show bpa pool 2>/dev/null | grep -q "pool tuned"; then
  ok "pool de CGNAT criado no perfil tunado"
else
  bad "nao consegui criar pool no perfil tunado"
fi

pkill -f "vpp -c $CONF" 2>/dev/null
echo "$before" > "$NR" 2>/dev/null || true
rm -rf "$ROOT"

if [ "$FAILED" -eq 0 ]; then
  echo; echo "TODOS OS TESTES PASSARAM"; exit 0
fi
echo; echo "$FAILED FALHAS"; exit 1
