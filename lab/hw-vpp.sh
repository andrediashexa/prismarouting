#!/bin/sh
# Starts the lab VPP on real hardware. Run it inside the build container,
# with --network=host --privileged.
set -u
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
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

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
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
# With the kernel device's MAC, so unicast to it is accepted without relying on
# promiscuous mode. Left to itself VPP picks a fresh 02:fe:.. address and the
# interface then only works because of the promiscuous flag below.
#
# The af_packet version is deliberately NOT forced. An earlier version of this
# script pinned v2 on the belief that v3 received nothing; measured across four
# combinations of version and MAC, all four receive identically, and the real
# cause of "receives nothing" was a missing route on the peer. The v2 evidence
# was noise and pinning it would have been carrying a change nothing supports.
LAN_MAC=$(cat /sys/class/net/ens19/address)
vppctl create host-interface name ens19 hw-addr "$LAN_MAC"
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

# --- and now prove it, rather than printing instructions ---
#
# This used to end by telling the operator what to type. That is how the lab
# came to be broken for a long time without anyone noticing: the instructions
# included a route that hw-lab.sh had just deleted, so following them by hand
# worked and running the lab did not, and nothing ever said which.
. /src/lab/verdict.sh
PEER=${PEER:-root@10.95.172.129}
SSH="ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=8"
SUB=100.64.0.5

# What the specification says this subscriber must get. The model, not the
# plugin: a test that asks the implementation what it should have said agrees
# with any mistake in it.
EXPECT=$(python3 - "$SUB" <<'PY'
import sys
sys.path.insert(0, "/src/ref")
from bpa_model import PoolConfig
p = PoolConfig("prod", ["100.64.0.0/22"], ["198.19.24.0/24"], block_size=512)
f = p.forward(sys.argv[1])
print(f["ip_pub"], f["port_lo"], f["port_hi"])
PY
)
E_IP=$(echo "$EXPECT" | cut -d' ' -f1)
E_LO=$(echo "$EXPECT" | cut -d' ' -f2)
E_HI=$(echo "$EXPECT" | cut -d' ' -f3)

echo
echo "=========== real traffic, real internet ==========="
echo "  the model says $SUB -> $E_IP ports $E_LO-$E_HI"
# Counted before and after, and the difference is what is asserted.
#
# A successful ping on its own proves nothing here: with the peer's route to
# the test destination missing, the traffic leaves through its DEFAULT gateway
# instead, gets translated by something upstream, and comes back. The ping is
# green and this CGNAT never saw a packet. Measured - that is exactly what
# happened while this assertion was being written.
SESS_BEFORE=$(vppctl show bpa session | awk '/total:/{print $2}')
PING=$($SSH "${PEER%@*}@10.95.200.197" "ping -c4 -W2 -I $SUB 1.1.1.1 2>&1 | tail -2" 2>&1)
printf '%s\n' "$PING" | sed 's/^/  /'
CURL=$($SSH "${PEER%@*}@10.95.200.197" \
  "curl -s -m 15 --interface $SUB https://1.1.1.1/ -o /dev/null -w 'http %{http_code}' 2>&1" 2>&1)
echo "  curl through the CGNAT: $CURL"
SUBVIEW=$(vppctl show bpa subscriber $SUB)
printf '%s\n' "$SUBVIEW" | sed 's/^/  /'
SESS=$(vppctl show bpa session | awk '/total:/{print $2}')
echo "  sessions: ${SESS_BEFORE:-0} before the traffic, ${SESS:-0} after"

echo
echo "=========== VERDICT ==========="
expect_in "ICMP comes back" "0% packet loss" "$PING"
expect "and it came back THROUGH this CGNAT, not around it" \
  test "${SESS:-0}" -gt "${SESS_BEFORE:-0}"
# Any status at all, not a specific one. What is being tested is that a TCP
# connection and a TLS handshake crossed the CGNAT and came back - 1.1.1.1
# answers 301, and pinning 200 would have made this fail for a reason that has
# nothing to do with the translation. "http 000" is curl saying nothing came
# back, which is the failure that matters.
expect "and TCP with TLS does too" sh -c "case \"$CURL\" in *'http 000'*|*http\ ) exit 1;; *http\ [1-5]*) exit 0;; *) exit 1;; esac"
expect_in "the public address is the one the model predicts" "$E_IP" "$SUBVIEW"
expect_in "and the port block is too" "$E_LO-$E_HI" "$SUBVIEW"
expect "the dataplane holds sessions for it" test "${SESS:-0}" -ge 1
verdict
