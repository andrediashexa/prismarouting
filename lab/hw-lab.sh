#!/bin/sh
# Lab em hardware real: duas VMs, trafego de internet de verdade.
#
#   [peer 10.95.200.197]                 [HexaRouting 10.95.200.196]
#     ens19 10.95.172.129/31  ──/31──►    ens19 10.95.172.128/31  (LAN, VPP)
#     assinantes 100.64.0.x                CGNAT -> 198.19.24.0/24
#     ens18 = gerencia                     tap -> Linux -> ens18 -> internet
#
# REGRA: a ens18 NAO vai para o VPP em nenhuma das duas maquinas. E por ela
# que o lab e acessado; entrega-la ao dataplane corta o acesso as duas VMs.
#
# Rode no HexaRouting. Precisa de acesso root ao peer por chave.
set -u
PEER=${PEER:-root@10.95.200.197}
LINK_LOCAL=10.95.172.128
LINK_PEER=10.95.172.129
TRANSIT_VPP=169.254.99.1
TRANSIT_LINUX=169.254.99.2
POOL_IN=100.64.0.0/22
POOL_OUT=198.19.24.0/24

say() { echo "==> $*"; }

say "peer: link /31 e assinantes RFC 6598"
ssh -o BatchMode=yes "$PEER" "
  ip addr flush dev ens19
  ip addr add $LINK_PEER/31 dev ens19
  ip link set ens19 up
  # assinantes: enderecos adicionais, para varios saírem por blocos distintos
  for h in 5 6 7; do ip addr add 100.64.0.\$h/32 dev ens19 2>/dev/null; done
  sysctl -qw net.ipv4.ip_forward=1
"

say "HexaRouting: Linux cede a ens19 ao VPP e encaminha o bloco publico"
ip addr flush dev ens19 2>/dev/null || true
ip link set ens19 up
sysctl -qw net.ipv4.ip_forward=1
sysctl -qw net.ipv4.conf.all.rp_filter=0
sysctl -qw net.ipv4.conf.ens18.rp_filter=0

say "liberando o caminho WAN na FORWARD"
# O Docker instalado nesta maquina poe a politica FORWARD em DROP. O caminho
# WAN do CGNAT (tap -> ens18) morre ali em silencio: o pacote sai do VPP,
# aparece no tap, e some. O contador de DROP da chain e que denuncia.
#
# Regras especificas em vez de trocar a politica global: afrouxar a politica
# desfaria o isolamento que o Docker montou.
for r in "-i vppwan -o ens18" "-i ens18 -o vppwan"; do
  iptables -C FORWARD $r -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 $r -j ACCEPT
done

say "pronto para subir o VPP: sh lab/hw-vpp.sh"
