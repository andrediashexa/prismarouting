#!/bin/sh
# The syslog stream survives its collector going away, on a message boundary
# (#492).
#
#   sh tools/lab.sh syslog-reconnect   -> LAB PASSED
#
# The exporter drops from the front of its buffer the messages a collector
# has already taken, and found each one's length by reading bytes 2-3 as an
# IPFIX header whatever the wire was. On the syslog wire "211 <134>1" reads as
# 12576, so once that much had gone out it cut the buffer mid-message, and the
# stream after the next reconnection - and the spill file - began in the
# middle of a line. Measured on the #475 bench (0.140.2): a collector restarted
# after half an hour of boot markers received `34Z" observedAt=...` as its
# first bytes, and every RFC 6587 frame after it was misread.
#
# So: collector A takes more than 12576 bytes (a hundred subscribers, each an
# allocation and, after the hold-down, a release), goes away, and collector B,
# listening on the same port, must frame every byte it is given - the replay
# and the events after it - with nothing malformed. tools/syslog_collector.py
# abandons the stream at the first frame it cannot read and counts it, which
# is the reading a real collector makes.
. /src/lab/lib.sh
. /src/lab/verdict.sh
set -u
HX="python3 /src/src/prismash/prismash.py"

mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api

ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up
ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

vppctl create host-interface name veth-vpp >/dev/null
vppctl set interface state host-veth-vpp up
# THE STREAM IS A KERNEL SOCKET, not a packet the dataplane forwards, so the
# collector has to be reachable from the VPP PROCESS - which means a linux_cp
# pair mirroring this interface as a TAP. Made through the VTY, the way the
# box makes it, for the reason lab/ipfix-tcp.sh records: with `vppctl lcp
# create` here, the lab proved a transport that worked only on a box somebody
# had paired by hand (#182).
$HX -c "configure" -c "interface host-veth-vpp" -c "linux-name vppmgmt0" \
    -c "exit" -c "commit" 2>&1 | tail -1 | sed 's/^/  pair: /'
vppctl set interface mtu 1500 host-veth-vpp
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
PEERMAC=$(ip netns exec peer cat /sys/class/net/veth-out/address)
vppctl set ip neighbor host-veth-vpp 10.0.0.2 $PEERMAC

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up
vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1

vppctl bpa pool din inside 100.64.0.0/22 outside 200.150.0.64/26 block 16384 mode dynamic
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa block-holddown 4

vppctl bpa block-holddown 2 >/dev/null

$HX -c "configure" -c "nat" -c "cgnat" \
    -c "logging syslog 10.0.0.2 enterprise 44444 port 601" -c "commit" >/dev/null 2>&1

burst() {  # burst <name> <first> <last>: one allocation per subscriber
  vppctl packet-generator new "name $1 limit $(( $3 - $2 + 1 )) node ip4-input \
      interface pg0 size 60-60 data { UDP: 100.64.0.$2 - 100.64.0.$3 -> 8.8.8.8 \
      UDP: 40000 -> 53 incrementing 8 }" >/dev/null
  vppctl packet-generator enable-stream $1 >/dev/null
}

echo "=== collector A: more than 12576 bytes of syslog ==="
ip netns exec peer python3 /src/tools/syslog_collector.py 601 25 /tmp/a.txt \
  > /tmp/a.out 2>&1 &
A=$!
sleep 2
burst a1 10 109; sleep 6          # a hundred allocations, then their releases
burst a2 10 109; sleep 6          # and again
wait $A
cat /tmp/a.txt | sed 's/^/  A: /'
A_LINES=$(awk '$1=="lines"{print $2}' /tmp/a.txt)
A_BAD=$(awk '$1=="malformed"{print $2}' /tmp/a.txt)
SENT=$(vppctl show bpa log 2>/dev/null | tr -d '\r' | grep -i -m3 -E "syslog|sent" | sed 's/^/  /')
printf '%s\n' "$SENT"

echo
echo "=== collector A is gone; collector B takes the stream ==="
ip netns exec peer python3 /src/tools/syslog_collector.py 601 40 /tmp/b.txt \
  > /tmp/b.out 2>&1 &
B=$!
sleep 1
burst b1 110 159; sleep 8         # the exporter notices, spills, reconnects
burst b2 110 159; sleep 8
wait $B
cat /tmp/b.txt | sed 's/^/  B: /'
echo "  -- the exporter's counters, and the blocks held:"
vppctl show bpa log 2>&1 | tr -d '\r' | sed -n '/syslog/,$p' | head -12 | sed 's/^/  | /'
vppctl show bpa pool 2>&1 | tr -d '\r' | grep -i -E "blocks|held|alloc" | head -4 | sed 's/^/  | /'
B_LINES=$(awk '$1=="lines"{print $2}' /tmp/b.txt)
B_BAD=$(awk '$1=="malformed"{print $2}' /tmp/b.txt)

echo
echo "=========== VERDICT ==========="
expect "collector A took more than 12576 bytes' worth of lines (${A_LINES:-0})" \
  test "${A_LINES:-0}" -gt 60
expect "  and framed all of them" test "${A_BAD:-1}" -eq 0
expect "collector B received the stream after A went away (${B_LINES:-0} lines)" \
  test "${B_LINES:-0}" -gt 0
expect "  and every frame it was given was whole: nothing began mid-message" \
  test "${B_BAD:-1}" -eq 0
verdict
