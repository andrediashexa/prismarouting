#!/bin/sh
# Starts the lab VPP on real hardware. Run it inside the build container,
# with --network=host --privileged.
set -u
LINK_LOCAL=10.95.172.128
LINK_PEER=10.95.172.129
TRANSIT_VPP=169.254.99.1
TRANSIT_LINUX=169.254.99.2

# Any previous instance goes first. Without this a second run adds the CGNAT
# feature to the SAME arc a second time - "show interface features" then lists
# bpa-in2out twice - and every "create" that already exists quietly fails, so
# a change to how the interface is created never takes effect while the script
# still reports success.
pkill -f "vpp -c" 2>/dev/null || true
sleep 1
rm -f /run/vpp/cli.sock

mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}

vpp -c /src/lab/startup-lcp.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

# --- LAN: VPP takes ens19 ---
#
# With the KERNEL device's MAC, not one VPP invents.
#
# AF_PACKET reads frames the kernel device accepted, and the kernel accepts
# frames addressed to its own MAC. Left to itself VPP gives the interface a
# fresh 02:fe:.. address, answers ARP with it, and the peer then sends every
# frame to a MAC the NIC drops. Nothing appears anywhere: VPP shows zero
# packets, the peer shows a full ARP entry, and the link looks fine from both
# ends.
# v2, not the v3 VPP picks by default.
#
# Measured, not preferred: with v3 the af-packet-input node runs, every block
# comes back as "timed out block", and the interface's rx counter never moves -
# while tcpdump on the SAME interface in the SAME namespace shows the frames
# arriving, correctly addressed. With v2 the counter moves. The rest of the
# path is still broken (see the issue), but v3 receiving nothing at all is a
# separate fact and this is where it was found.
vppctl create host-interface v2 name ens19
vppctl set interface state host-ens19 up
vppctl set interface ip address host-ens19 $LINK_LOCAL/31
# Belt and braces: promiscuous on both sides, so a peer that still has the old
# ARP entry cached is heard rather than silently dropped.
ip link set ens19 promisc on
vppctl set interface promiscuous on host-ens19

# --- WAN: tap to Linux, which already has the path to the internet ---
vppctl create tap id 0 host-if-name vppwan host-ip4-addr $TRANSIT_LINUX/30
vppctl set interface state tap0 up
vppctl set interface ip address tap0 $TRANSIT_VPP/30
vppctl ip route add 0.0.0.0/0 via $TRANSIT_LINUX tap0

# back to the subscribers
vppctl ip route add 100.64.0.0/22 via $LINK_PEER host-ens19

# --- Linux forwards the public block into VPP ---
ip link set vppwan up
ip route replace 198.19.24.0/24 via $TRANSIT_VPP dev vppwan

# --- CGNAT ---
vppctl bpa pool prod inside 100.64.0.0/22 outside 198.19.24.0/24 block 512
vppctl set interface bpa in2out host-ens19
vppctl set interface bpa out2in tap0
vppctl show bpa pool

cat <<'EOF'

Teste, a partir do peer:
  ip route replace 1.1.1.1/32 via 10.95.172.128 dev ens19
  ping -c4 -I 100.64.0.5 1.1.1.1
  curl --interface 100.64.0.5 https://1.1.1.1/

Esperado (conferido contra ref/bpa_model.py):
  100.64.0.5 -> 198.19.24.5   portas 1024-1535
  100.64.0.6 -> 198.19.24.6   portas 1024-1535
  100.64.0.7 -> 198.19.24.7   portas 1024-1535
EOF
