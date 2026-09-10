#!/bin/sh
# Injects packets with the packet generator and checks the translation against
# what ref/bpa_model.py predicts. Needs neither a NIC nor DPDK.
#
# We inject into ip4-input rather than ethernet-input: with no L2 there is no
# MAC to match, and the ip4-unicast arc (where the bpa nodes live) runs the same.
set -u
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
n=0
until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

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

pg() { vppctl packet-generator new "$1"; }

echo "=========== IN2OUT: three subscribers going out ==========="
echo "predicted by the model:"
echo "  100.64.0.5 -> 200.150.0.17 port 1536   (block 1536-2047)"
echo "  100.64.0.6 -> 200.150.0.18 port 1536   (block 1536-2047)"
echo "  100.64.0.9 -> 200.150.0.17 port 2048   (block 2048-2559)"
echo "got:"
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
vppctl show trace | grep "BPA in2out" | sed 's/^/  /'

for sub in 5 6 9; do vppctl packet-generator delete s$sub; done

echo
echo "=========== OUT2IN: legitimate reply and unsolicited packet ==========="
echo "expected: 200.150.0.17:1536 -> 100.64.0.5:12345 ; port 9999 has no session"
echo "got:"
pg "name r1 limit 1 node ip4-input interface pg1 size 60-60 \
    data { UDP: 8.8.8.8 -> 200.150.0.17 \
           UDP: 53 -> 1536 \
           incrementing 8 }"
pg "name r2 limit 1 node ip4-input interface pg1 size 60-60 \
    data { UDP: 8.8.8.8 -> 200.150.0.17 \
           UDP: 53 -> 9999 \
           incrementing 8 }"
vppctl clear trace >/dev/null
vppctl trace add pg-input 20 >/dev/null
vppctl packet-generator enable >/dev/null
sleep 1
vppctl show trace | grep "BPA out2in" | sed 's/^/  /'

echo
echo "=========== counters ==========="
vppctl show errors | grep -iE "bpa-(in2out|out2in)" | sed 's/^/  /'
echo
vppctl show bpa session

echo
echo "=========== EXPIRY: ports have to come back ==========="
vppctl bpa timeouts udp 2
echo "udp timeout lowered to 2s; waiting for the expiry process..."
n=0
# "sessions", not "sessoes" - the plugin prints English. This loop never
# matched, so it always ran its full 15 seconds and never confirmed the
# sessions had expired; the assertion after it was reading whatever the box
# happened to be doing at second 15.
until vppctl show bpa session | grep -q "^total: 0 sessions" || [ "$n" -ge 15 ]; do
  sleep 1; n=$((n+1))
done
vppctl show bpa session
echo "(took ${n}s)"

echo
echo "=========== reuse after expiry ==========="
pg "name again limit 1 node ip4-input interface pg0 size 60-60 \
    data { UDP: 100.64.0.5 -> 8.8.8.8 \
           UDP: 12345 -> 53 \
           incrementing 8 }"
vppctl clear trace >/dev/null
vppctl trace add pg-input 5 >/dev/null
vppctl packet-generator enable >/dev/null
sleep 1
echo "expected: the same port 1536 returned to the pool and handed out again"
vppctl show trace | grep "BPA in2out" | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
ERR=$(vppctl show errors)
# bpa_errors.h declares "packets translated" TWICE - once for in2out and once
# for out2in - so "in both directions" was satisfied by the forward direction
# alone, on a box whose return path translated nothing. Each direction is
# asserted against its own node's counter row.
expect "packets were translated on the way out" \
  sh -c "vppctl show errors | grep -i 'bpa-in2out' | grep -q 'packets translated'"
expect "and on the way back, so both directions really ran" \
  sh -c "vppctl show errors | grep -i 'bpa-out2in' | grep -q 'packets translated'"
expect_in "an unsolicited inbound packet was refused" "no session for destination" "$ERR"
expect_in "sessions were created" "sessions created" "$ERR"
# after expiry the port went back to the pool and was handed out again
expect_in "the freed port was reallocated" "200.150.0.17:1536" "$(vppctl show trace)"
verdict
