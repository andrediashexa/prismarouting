#!/bin/sh
# Where BFD runs, measured: FRR's bfdd behind linux_cp against VPP's own BFD (#559).
#
# The decision in docs/bfd.md rests on these numbers, so they are a lab and not
# a note: anyone can run it again on another machine and read the same table.
#
#   [netns peer] FRR bfdd (the far end, the same in every row)
#     veth-out 10.0.0.2 ------(veth)------ veth-vpp -> VPP -> vppwan0 10.0.0.1
#                                                       |        |
#                                   VPP's BFD (row B) --+        +-- bfdd (row A)
#
# For each transmit/receive interval, with detect-multiplier 3, the session is
# left up for MEASURE_SECS and the far end's "Session down events" counter is
# read: a session that flapped once at an interval is not stable at it. Every
# interval is measured idle and again with the dataplane's main thread busy
# forwarding a UDP flood the peer sends through it to a blackhole, because the
# punt path (row A) shares that thread with the traffic and VPP's BFD (row B)
# does not leave it.
#
# NOT in the suite: a full sweep is several minutes of waiting by design.
#   sh tools/lab.sh bfd-measure          the whole table
#   sh tools/lab.sh bfd-measure 10 300 50     10 s per row, two intervals
set -u
. /src/lab/lib.sh
# Arguments, because tools/lab.sh passes its argv and not the environment:
#   sh tools/lab.sh bfd-measure [<seconds per row> [<ms> ...]]
MEASURE_SECS=${1:-30}
[ $# -gt 0 ] && shift
INTERVALS=${*:-300 100 50 20 10}
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up
ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
wait_vpp || exit 1
use_binary_api
HX="python3 /src/src/prismash/prismash.py"
vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
$HX -c "configure" -c "interface host-veth-vpp" -c "linux-name vppwan0" \
    -c "exit" -c "commit" 2>&1 | tail -1 | sed 's/^/  pair: /'
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
# A blackhole for the load: the peer's flood is routed by VPP to nowhere, so
# every packet costs the dataplane a lookup and a drop and none reaches Linux.
vppctl ip route add 192.0.2.0/24 via drop

mkdir -p /var/run/frr /etc/frr /var/run/frr/peer /etc/frr/peer
for d in zebra bfdd; do printf 'hostname box\n' > /etc/frr/$d.conf; done
for d in zebra bfdd; do printf 'hostname peer\n' > /etc/frr/peer/$d.conf; done
: > /etc/frr/peer/vtysh.conf
chown -R frr:frr /var/run/frr /etc/frr
start_frr bfdd
for d in zebra bfdd; do
  ip netns exec peer /usr/lib/frr/$d -d -N peer -A 127.0.0.1 -f /etc/frr/peer/$d.conf
done
ip netns exec peer ip route add 192.0.2.0/24 via 10.0.0.1
sleep 2

pv() { ip netns exec peer vtysh -N peer "$@"; }

# The far end's counters for its one session, as numbers.
peer_downs() {
  pv -c 'show bfd peer 10.0.0.1 counters json' 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("session-down",-1))' 2>/dev/null \
    || echo -1
}
peer_state() {
  pv -c 'show bfd peer 10.0.0.1 json' 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status","?"))' 2>/dev/null \
    || echo "?"
}
set_peer() {  # <ms>
  pv -c "configure terminal" -c "bfd" -c "peer 10.0.0.1 interface veth-out" \
     -c "detect-multiplier 3" -c "receive-interval $1" -c "transmit-interval $1" \
     -c "exit" -c "exit" >/dev/null 2>&1
}
set_box_bfdd() {  # <ms>
  vtysh -c "configure terminal" -c "bfd" -c "peer 10.0.0.2 interface vppwan0" \
     -c "detect-multiplier 3" -c "receive-interval $1" -c "transmit-interval $1" \
     -c "exit" -c "exit" >/dev/null 2>&1
}
no_box_bfdd() {
  vtysh -c "configure terminal" -c "bfd" -c "no peer 10.0.0.2 interface vppwan0" \
     -c "exit" >/dev/null 2>&1
}
set_vpp_bfd() {  # <ms>
  us=$(( $1 * 1000 ))
  vppctl bfd udp session del interface host-veth-vpp local-addr 10.0.0.1 \
    peer-addr 10.0.0.2 >/dev/null 2>&1
  vppctl bfd udp session add interface host-veth-vpp local-addr 10.0.0.1 \
    peer-addr 10.0.0.2 desired-min-tx "$us" required-min-rx "$us" detect-mult 3
}
no_vpp_bfd() {
  vppctl bfd udp session del interface host-veth-vpp local-addr 10.0.0.1 \
    peer-addr 10.0.0.2 >/dev/null 2>&1
}
# The load: a UDP flood from the peer, through the dataplane, to the blackhole.
FLOOD_PID=""
load_on() {
  ip netns exec peer python3 - <<'EOF' >/dev/null 2>&1 &
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
b = b"x" * 64
while True:
    for p in range(1024, 2048):
        s.sendto(b, ("192.0.2.1", p))
EOF
  FLOOD_PID=$!
}
load_off() { [ -n "$FLOOD_PID" ] && kill "$FLOOD_PID" 2>/dev/null; FLOOD_PID=""; }
drops() {
  vppctl show interface host-veth-vpp \
    | awk '{for (i = 1; i < NF; i++) if ($i == "rx" && $(i+1) == "packets") {print $(i+2); exit}}'
}

# Up within 30 s, or not at all: a new session starts at one packet a second
# (RFC 5880 6.8.3) and was measured taking 14 s to reach Up.
wait_up() {
  _n=0
  until [ "$(peer_state)" = up ] || [ "$_n" -ge 30 ]; do sleep 1; _n=$((_n + 1)); done
  [ "$(peer_state)" = up ]
}

measure() {  # <row> <ms> <load: idle|loaded>
  [ "$3" = loaded ] && load_on
  r0=$(drops)
  if ! wait_up; then
    load_off
    printf '  %-6s %5s ms x3  %-7s never came up\n' "$1" "$2" "$3"
    return
  fi
  d0=$(peer_downs)
  sleep "$MEASURE_SECS"
  d1=$(peer_downs); st=$(peer_state); r1=$(drops)
  load_off
  pps=$(( (r1 - r0) / (MEASURE_SECS + 1) ))
  printf '  %-6s %5s ms x3  %-7s %s down event(s) in %ss, state %s, ~%s pps through the dataplane\n' \
    "$1" "$2" "$3" "$((d1 - d0))" "$MEASURE_SECS" "$st" "$pps"
}

echo "=========== does BFD reach FRR's bfdd through linux_cp at all ==========="
set_peer 300
set_box_bfdd 300
if wait_up; then
  echo "  [ ok ] a bfdd session over the linux_cp pair comes up: $(peer_state)"
else
  echo "  [FAIL] bfdd over linux_cp never came up: $(peer_state)"
  echo "  -- the dataplane's errors:"
  vppctl show errors 2>/dev/null | grep -iE "bfd|udp|punt" | sed 's/^/    /'
  vtysh -c 'show bfd peers' 2>/dev/null | sed 's/^/    /'
fi
echo "  -- the dataplane's own BFD state while bfdd owns the session:"
vppctl show bfd 2>/dev/null | head -5 | sed 's/^/    /'

echo
echo "=========== row A: bfdd behind linux_cp ==========="
for ms in $INTERVALS; do
  set_peer "$ms"; set_box_bfdd "$ms"
  measure bfdd "$ms" idle
  measure bfdd "$ms" loaded
done
no_box_bfdd
sleep 2

echo
echo "=========== row B: VPP's own BFD ==========="
for ms in $INTERVALS; do
  set_peer "$ms"; set_vpp_bfd "$ms"
  measure vpp "$ms" idle
  measure vpp "$ms" loaded
done
no_vpp_bfd
echo "  -- what VPP said about its session:"
vppctl show bfd sessions 2>/dev/null | head -8 | sed 's/^/    /'
echo
echo "MEASURE DONE"
