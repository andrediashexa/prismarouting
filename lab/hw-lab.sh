#!/bin/sh
# Lab on real hardware: two VMs, real internet traffic.
#
#   [peer 10.95.200.197]                 [HexaRouting 10.95.200.196]
#     ens19 10.95.172.129/31  ──/31──►    ens19 10.95.172.128/31  (LAN, VPP)
#     subscribers 100.64.0.x               CGNAT -> 198.19.24.0/24
#     ens18 = management                   tap -> Linux -> ens18 -> internet
#
# RULE: ens18 does NOT go to VPP on either machine. It is how the lab is
# reached; handing it to the dataplane cuts access to both VMs.
#
# Run this on the HexaRouting box. Needs key-based root access to the peer.
# set -e: without it the script ran to the end printing "ready" even with ssh
# and iptables missing. A script that declares success after failing is worse
# than no script at all.
set -eu
PEER=${PEER:-root@10.95.200.197}

# A missing tool is an environment error, not a configuration one: naming
# which one is missing saves the hunt.
for t in ip sysctl ssh iptables; do
  command -v "$t" >/dev/null 2>&1 || {
    echo "ERROR: '$t' not found. Install it before running." >&2
    exit 1
  }
done
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
  # subscribers: extra addresses, so several leave through distinct blocks
  for h in 5 6 7; do ip addr add 100.64.0.\$h/32 dev ens19 2>/dev/null; done
  sysctl -qw net.ipv4.ip_forward=1
"

say "HexaRouting: Linux hands ens19 to VPP and forwards the public block"
ip addr flush dev ens19 2>/dev/null || true
ip link set ens19 up
sysctl -qw net.ipv4.ip_forward=1
sysctl -qw net.ipv4.conf.all.rp_filter=0
sysctl -qw net.ipv4.conf.ens18.rp_filter=0

say "opening the WAN path in FORWARD"
# Docker, installed on this machine, sets the FORWARD policy to DROP. The
# CGNAT WAN path (tap -> ens18) dies there silently: the packet leaves VPP,
# appears on the tap, and vanishes. The chain's DROP counter is what gives it
# away.
#
# Specific rules rather than changing the global policy: loosening the policy
# would undo the isolation Docker set up.
for r in "-i vppwan -o ens18" "-i ens18 -o vppwan"; do
  iptables -C FORWARD $r -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 $r -j ACCEPT
done

say "ready to start VPP: sh lab/hw-vpp.sh"
