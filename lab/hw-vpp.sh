#!/bin/sh
# Sobe o VPP do lab em hardware real. Rode dentro do container de build,
# com --network=host --privileged.
set -u
LINK_LOCAL=10.95.172.128
LINK_PEER=10.95.172.129
TRANSIT_VPP=169.254.99.1
TRANSIT_LINUX=169.254.99.2

mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}

vpp -c /src/lab/startup-lcp.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP nao subiu"; tail -20 /tmp/vpp.log; exit 1; }

# --- LAN: o VPP assume a ens19 ---
vppctl create host-interface name ens19
vppctl set interface state host-ens19 up
vppctl set interface ip address host-ens19 $LINK_LOCAL/31
vppctl set interface promiscuous on host-ens19

# --- WAN: tap ate o Linux, que ja tem o caminho para a internet ---
vppctl create tap id 0 host-if-name vppwan host-ip4-addr $TRANSIT_LINUX/30
vppctl set interface state tap0 up
vppctl set interface ip address tap0 $TRANSIT_VPP/30
vppctl ip route add 0.0.0.0/0 via $TRANSIT_LINUX tap0

# volta para os assinantes
vppctl ip route add 100.64.0.0/22 via $LINK_PEER host-ens19

# --- Linux encaminha o bloco publico para dentro do VPP ---
ip link set vppwan up
ip route replace 198.19.24.0/24 via $TRANSIT_VPP dev vppwan

# --- CGNAT ---
vppctl bpa pool prod inside 100.64.0.0/22 outside 198.19.24.0/24 block 512
vppctl set interface bpa in2out host-ens19
vppctl set interface bpa out2in tap0
vppctl show bpa pool
