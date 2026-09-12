#!/bin/sh
# IPFIX export of block events (RFC 8158), with a real collector decoding what
# goes out.
#
#   pg0/pg1  subscriber traffic, dynamic pool
#   veth     path to the collector, which runs in the "peer" netns
set -u
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
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
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

# path to the collector
vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
# With no resolved neighbour, the first IPFIX records are dropped while ARP is
# still incomplete. For a compliance log that is silent loss, so the path to
# the collector has to be ready beforehand.
PEERMAC=$(ip netns exec peer cat /sys/class/net/veth-out/address)
vppctl set ip neighbor host-veth-vpp 10.0.0.2 $PEERMAC

# subscriber traffic
vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up
vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1

echo "=== configurando o exportador IPFIX ==="
vppctl set ipfix exporter collector 10.0.0.2 src 10.0.0.1 template-interval 3 port 4739
vppctl show ipfix exporter 2>&1 | head -6 | sed 's/^/  /'

vppctl bpa pool din inside 100.64.0.0/22 outside 200.150.0.16/32 block 16384 mode dynamic
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa block-holddown 4

echo
echo "=== habilitando o log de blocos ==="
vppctl bpa logging enable
vppctl show bpa logging | sed 's/^/  /'

# collector on the far side of the wire
ip netns exec peer python3 /src/tools/ipfix_collector.py 4739 30 > /tmp/collector.out 2>&1 &
COL=$!
# captures the wire in parallel: separates "VPP did not send" from "the
# collector lost it"
ip netns exec peer timeout 28 tcpdump -nni veth-out -c 40 "udp port 4739" > /tmp/tcpdump.out 2>&1 &
sleep 2

echo
echo "=== 3 subscribers: 3 block allocations ==="
for sub in 5 6 9; do
  vppctl packet-generator new "name s$sub limit 1 node ip4-input interface pg0 size 60-60 \
      data { UDP: 100.64.0.$sub -> 8.8.8.8 \
             UDP: 40000 -> 53 \
             incrementing 8 }"
done
vppctl packet-generator enable >/dev/null; sleep 1
for sub in 5 6 9; do vppctl packet-generator delete s$sub >/dev/null 2>&1; done

echo "=== 4th subscriber: exhaustion (natEvent 12) ==="
vppctl packet-generator new "name s12 limit 1 node ip4-input interface pg0 size 60-60 \
    data { UDP: 100.64.0.12 -> 8.8.8.8 \
           UDP: 40000 -> 53 \
           incrementing 8 }"
vppctl packet-generator enable >/dev/null; sleep 1
vppctl packet-generator delete s12 >/dev/null 2>&1

echo
echo "=== forcing expiry to produce the 3 releases ==="
vppctl bpa timeouts udp 2
n=0
until vppctl show bpa blocks | grep -q "total: 0/3" || [ "$n" -ge 25 ]; do sleep 1; n=$((n+1)); done
echo "  blocos devolvidos em ${n}s"
vppctl show bpa blocks | tail -1 | sed 's/^/  /'

echo
echo "=== contadores do exportador ==="
vppctl show bpa logging | sed 's/^/  /'

wait $COL 2>/dev/null
echo
echo "=========== O QUE PASSOU NO FIO (tcpdump) ==========="
grep -c "IP " /tmp/tcpdump.out 2>/dev/null | sed 's/^/  udp\/4739 packets: /'
grep "IP " /tmp/tcpdump.out 2>/dev/null | awk '{print $NF}' | sort | uniq -c | sed 's/^/  tamanhos: /'
echo
echo "=========== O QUE O COLETOR RECEBEU E DECODIFICOU ==========="
cat /tmp/collector.out

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
C=$(cat /tmp/collector.out)
expect_in "the collector decoded the template" "TEMPLATE 256" "$C"
expect_in "allocations arrived" "ALLOCATION" "$C"
expect_in "de-allocations arrived" "DEALLOCATION" "$C"
expect_in "every allocation had its release" "Every allocation had its matching" "$C"

# The record's own timestamp, against the box's clock.
#
# A record stamped from VPP's boot clock reads as a plausible time of day and
# is not one, so counting records will never catch it. The skew grows with the
# machine's uptime, which is what makes this assertion impossible to satisfy by
# accident: it fails harder the longer the box has been running.
# https://github.com/andrediashexa/hexarouting/issues/117
worst_skew() {
  printf '%s\n' "$1" | sed -n 's/.*skew=\([+-]*[0-9]*\)s.*/\1/p' |
    tr -d '+-' | sort -n | tail -1
}
SKEW=$(worst_skew "$C")
expect "the record carries the time of day, not time since boot (worst skew ${SKEW:-none}s)" \
  test "${SKEW:-99999}" -lt 120
verdict
