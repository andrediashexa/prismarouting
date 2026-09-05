#!/bin/sh
# Prova que o startup.conf gerado e valido e que o VPP fica de pe com ele.
#
# Este teste faltava. Sem ele, o tuning escrevia num startup.conf.d que o VPP
# nao le - e nada indicava isso: a caixa subia, encaminhava, e rendia metade.
set -u
fail=0
say() { echo "  $*"; }
bad() { echo "  FALHA: $*"; fail=$((fail+1)); }

mkdir -p /run/vpp /vpp_plugins /var/log/vpp
(cd /src/.build-vpp && ninja >/dev/null 2>&1)
sh /src/appliance/install.sh >/dev/null 2>&1

echo "=== o tuning grava onde o VPP le? ==="
rm -f /etc/vpp/startup.conf
HEXA_SUBSCRIBERS=200000 /usr/lib/hexarouting/apply-tuning >/dev/null 2>&1
[ -s /etc/vpp/startup.conf ] && say "/etc/vpp/startup.conf gerado" \
  || bad "/etc/vpp/startup.conf nao foi gerado"
grep -q "GERADO por hexarouting-tune" /etc/vpp/startup.conf \
  && say "com marca de gerado" || bad "sem marca de gerado"
# Confere algo que SO existe na base. A stanza plugins e gerada, entao
# procura-la aqui daria um falso positivo de "base concatenada".
grep -q "cli-listen /run/vpp/cli.sock" /etc/vpp/startup.conf \
  && say "base do operador concatenada (unix/cli-listen)" \
  || bad "base nao foi concatenada"
grep -q "bpa_plugin.so { enable }" /etc/vpp/startup.conf \
  && say "plugin do CGNAT habilitado na config gerada" \
  || bad "plugin do CGNAT ausente"
n=$(grep -c "^plugins {" /etc/vpp/startup.conf)
[ "$n" = 1 ] && say "uma unica stanza plugins" || bad "plugins{} aparece $n vezes"
grep -qE "^(cpu|buffers|memory) \{" /etc/vpp/startup.conf \
  && say "stanzas calculadas presentes" || bad "stanzas calculadas ausentes"
n=$(grep -c "^cpu {" /etc/vpp/startup.conf)
[ "$n" = 1 ] && say "sem stanza duplicada" || bad "cpu{} aparece $n vezes"

echo
echo "=== o VPP sobe com esse arquivo? ==="
vpp -c /etc/vpp/startup.conf > /tmp/vppstart.log 2>&1 &
VPPPID=$!
n=0
until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
if vppctl show version >/dev/null 2>&1; then
  say "subiu em ${n}s: $(vppctl show version | head -1 | cut -c1-40)"
else
  bad "nao subiu"
  tail -12 /tmp/vppstart.log | sed 's/^/    /'
fi

echo
echo "=== o plugin do CGNAT carregou? ==="
vppctl show plugins 2>/dev/null | grep -q bpa_plugin \
  && say "bpa_plugin carregado" || bad "bpa_plugin nao carregou"
vppctl show plugins 2>/dev/null | grep -q linux_cp \
  && say "linux_cp carregado" || bad "linux_cp nao carregou"

echo
echo "=== e FICA de pe? (o ciclo de reinicio da ISO era aqui) ==="
sleep 5
if kill -0 $VPPPID 2>/dev/null && vppctl show version >/dev/null 2>&1; then
  say "vivo apos 5s"
else
  bad "morreu; ultimas linhas do log:"
  tail -12 /tmp/vppstart.log | sed 's/^/    /'
fi

echo
echo "=== a config de CGNAT e aplicavel nele? ==="
vppctl bpa pool teste inside 100.64.0.0/22 outside 200.150.0.16/30 >/dev/null 2>&1
vppctl show bpa pool | head -1 | grep -q "pool teste" \
  && say "pool criado no VPP gerado" || bad "nao consegui criar pool"

echo
[ "$fail" = 0 ] && echo "TODOS OS TESTES PASSARAM" || echo "$fail FALHAS"
exit $fail
