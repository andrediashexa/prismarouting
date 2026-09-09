#!/bin/sh
# Proves that the handoff puts both directions of the same subscriber on the
# same worker. Without it, the reply would land on a worker with no session and
# the packet would die in "no session for destination".
set -u
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

vpp -c /src/lab/startup-workers.conf > /tmp/vpp.log 2>&1 &
n=0
until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

echo "=== threads ==="
vppctl show threads | head -5

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up
vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl set ip neighbor pg0 100.64.3.253 02:00:00:00:00:01
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0

vppctl bpa pool lab inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1

echo
echo "=== feature active on the interface (must be the handoff node) ==="
vppctl show interface features pg0 | grep -A2 "ip4-unicast" | grep bpa
vppctl show interface features pg1 | grep -A2 "ip4-unicast" | grep bpa

pg() { vppctl packet-generator new "$1"; }

echo
echo "=== IN2OUT: 3 assinantes ==="
for sub in 5 6 9; do
  pg "name s$sub limit 1 node ip4-input interface pg0 size 60-60 \
      data { UDP: 100.64.0.$sub -> 8.8.8.8 \
             UDP: 12345 -> 53 \
             incrementing 8 }"
done
vppctl clear trace >/dev/null
vppctl trace add pg-input 20 >/dev/null
vppctl packet-generator enable >/dev/null
sleep 1
vppctl show trace | grep -E "BPA (handoff|in2out)" | sed 's/^/  /'
for sub in 5 6 9; do vppctl packet-generator delete s$sub; done

echo
echo "=== session distribution per thread ==="
echo "predicted: thread 1 -> 1 session (sub 6) ; thread 2 -> 2 sessions (subs 5 and 9)"
vppctl show bpa session

echo
echo "=== OUT2IN: replies for all three ==="
pg "name r5 limit 1 node ip4-input interface pg1 size 60-60 \
    data { UDP: 8.8.8.8 -> 200.150.0.17 \
           UDP: 53 -> 1536 \
           incrementing 8 }"
pg "name r6 limit 1 node ip4-input interface pg1 size 60-60 \
    data { UDP: 8.8.8.8 -> 200.150.0.18 \
           UDP: 53 -> 1536 \
           incrementing 8 }"
pg "name r9 limit 1 node ip4-input interface pg1 size 60-60 \
    data { UDP: 8.8.8.8 -> 200.150.0.17 \
           UDP: 53 -> 2048 \
           incrementing 8 }"
vppctl clear trace >/dev/null
vppctl trace add pg-input 20 >/dev/null
vppctl packet-generator enable >/dev/null
sleep 1
vppctl show trace | grep -E "BPA (handoff|out2in)" | sed 's/^/  /'

echo
echo "=== contadores ==="
vppctl show errors | grep -iE "bpa-" | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
ERR=$(vppctl show errors)
expect_in "in2out handed packets to other workers" "packets handed to another worker" "$ERR"
expect_not_in "every reply found its session" "no session for destination" "$ERR"
# bpa_errors.h declares "packets translated" TWICE - once for in2out and
# once for out2in - so this was satisfied by the forward direction alone and
# passed on a box whose return path translated nothing. Asserted on the
# out2in node's own counters.
expect "out2in translated" \
  sh -c "vppctl show errors | grep -i 'out2in' | grep -q 'packets translated'"
verdict
