#!/bin/sh
# TCP transport for the log: the test that matters is killing the collector.
#
# Over UDP, events produced while the collector is down vanish silently - and
# losing one natEvent 17 recreates the gap that makes a court order name the
# wrong subscriber. Here they have to go to disk and come back on reconnect.
set -u
mkdir -p /run/vpp /vpp_plugins /var/log/hexarouting
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

vpp -c /src/lab/startup-lcp.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl create host-interface name veth-vpp >/dev/null
vppctl set interface state host-veth-vpp up
# The TCP exporter uses a kernel socket, not VPP's dataplane: the collector has
# to be reachable through the Linux stack. On a real box linux_cp provides that
# path, mirroring the interface as a TAP.
vppctl lcp create host-veth-vpp host-if vppmgmt0
vppctl set interface mtu 1500 host-veth-vpp
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
PEERMAC=$(ip netns exec peer cat /sys/class/net/veth-out/address)
vppctl set ip neighbor host-veth-vpp 10.0.0.2 $PEERMAC

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up; vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl bpa pool din inside 100.64.0.0/22 outside 200.150.0.16/32 block 16384 mode dynamic
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa block-holddown 3
vppctl bpa logging tcp 10.0.0.2 port 4740

burst() {
  for sub in "$@"; do
    vppctl packet-generator new "name b$sub limit 1 node ip4-input interface pg0 size 60-60 \
        data { UDP: 100.64.0.$sub -> 8.8.8.8 UDP: 40000 -> 53 incrementing 8 }"
  done
  vppctl packet-generator enable >/dev/null; sleep 1
  for sub in "$@"; do vppctl packet-generator delete b$sub >/dev/null 2>&1; done
}

echo "=========== PHASE 1: collector up ==========="
ip netns exec peer python3 /src/tools/ipfix_collector.py tcp 4740 12 > /tmp/c1.out 2>&1 &
C1=$!
sleep 3
burst 5
sleep 3
vppctl show bpa logging | grep -A5 "transporte TCP" | sed 's/^/  /'
wait $C1
echo "  --- received (raw collector output) ---"
cat /tmp/c1.out | sed 's/^/    /'

echo
echo "=========== PHASE 2: collector KILLED, events keep coming ==========="
echo "  (with no collector: the events have to go to the spill file)"
vppctl bpa timeouts udp 2
burst 6 9
sleep 8
# The needles were in Portuguese while the plugin prints in English: this line
# never showed anything, so the diagnosis came out empty in exactly the phase
# that kills the collector.
vppctl show bpa logging | grep -E "collector|spill|pending" | sed 's/^/  /'
ls -l /var/log/hexarouting/ipfix-spill.bin 2>/dev/null | sed 's/^/  /' || echo "  (nada em disco ainda: buffer em memoria ainda cabe)"

echo
echo "=========== PHASE 3: collector comes back ==========="
ip netns exec peer python3 /src/tools/ipfix_collector.py tcp 4740 15 > /tmp/c2.out 2>&1 &
C2=$!
wait $C2
echo "  --- recebido apos a reconexao (saida crua) ---"
cat /tmp/c2.out | sed 's/^/    /'
echo
vppctl show bpa logging | grep -A5 "transporte TCP" | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
C2=$(cat /tmp/c2.out)
expect_in "records arrived after the collector came back" "DEALLOCATION" "$C2"
expect_in "the template was resent on reconnect" "TEMPLATE 256" "$C2"
expect_not_in "nothing was left pending" "270 pending" "$(vppctl show bpa logging)"
verdict
