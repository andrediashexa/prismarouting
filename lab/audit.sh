#!/bin/sh
# Trilha de configuracao datada: a "prova" do modo deterministico.
set -u
mkdir -p /run/vpp /vpp_plugins /var/log/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done

echo "=== estado inicial ==="
vppctl show bpa pool history | sed 's/^/  /'

echo
echo "=== operacoes de configuracao ==="
vppctl bpa pool provedor-a inside 100.64.0.0/22 outside 200.150.0.0/24 outside 45.170.16.0/26 block 512 >/dev/null
echo "  criado provedor-a"
sleep 1
vppctl bpa pool provedor-b inside 10.50.0.0/16 outside 177.85.32.0/24 block 2048 policy blocked >/dev/null
echo "  criado provedor-b"
sleep 1
vppctl bpa pool provedor-a del
echo "  removido provedor-a"
sleep 1
vppctl bpa pool provedor-a inside 100.64.0.0/22 outside 200.150.0.0/24 block 1024 >/dev/null
echo "  recriado provedor-a com bloco diferente"

echo
echo "=========== A TRILHA ==========="
vppctl show bpa pool history | sed 's/^/  /'

echo
echo "=== o arquivo em disco (append-only, com fsync) ==="
ls -l /var/log/hexarouting/config-history.log | sed 's/^/  /'
echo
echo "=== pergunta que a trilha responde ==="
echo "  'qual era o bloco do provedor-a antes da ultima mudanca?'"
grep "add pool provedor-a" /var/log/hexarouting/config-history.log \
  | head -1 | grep -oE "block [0-9]+" | sed 's/^/    /'
echo "  'e agora?'"
grep "add pool provedor-a" /var/log/hexarouting/config-history.log \
  | tail -1 | grep -oE "block [0-9]+" | sed 's/^/    /'
echo
echo "  Sem a trilha, a formula atual responderia com bloco 1024 para uma"
echo "  data em que o pool ainda usava 512 - e apontaria o assinante errado."

echo
echo "=== tambem foi para o log do VPP (pode ir a syslog remoto) ==="
vppctl show logging | grep -c "pool provedor" | sed 's/^/  linhas: /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
H=$(vppctl show bpa pool history)
expect_in "an add was recorded" "add pool provedor-a" "$H"
expect_in "a del was recorded" "del pool provedor-a" "$H"
expect_in "entries are dated in UTC" "Z add" "$H"
expect "the trail survives on disk" test -s /var/log/hexarouting/config-history.log
verdict
