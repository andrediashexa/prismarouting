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
echo "=========== does a translated packet actually LEAVE? ==========="
#
# The assertion this lab did not have, and the reason #199 survived: every
# check below it is a COUNTER, and a counter says what a node decided, not what
# came out of the box. On a box with workers every packet was translated,
# counted as translated, and then blackholed - "packets translated" was true
# and nothing left. This lab would have passed on it, and did.
#
# Every other lab that asserts a packet leaving uses startup-test.conf, which
# has NO workers. hexarouting-tune configures two or more on every appliance,
# so the forwarding path was proved only in a configuration the product never
# ships in.
#
# Captured off the far interface, in BOTH directions, because out2in has the
# same shape and a fix to one is not a fix to the other.
cap() {
  _inj=$1; _cap=$2; _pcap=$3; _src=$4; _dst=$5; _sp=$6; _dp=$7
  rm -f "$_pcap"
  vppctl packet-generator delete c >/dev/null 2>&1
  # More than one, and filtered below: the box also sends ARP requests for
  # subscribers an earlier section touched, and a capture of exactly one packet
  # catches whichever arrives first. A lab that fails because it captured an
  # ARP is a lab nobody trusts the next time it fails.
  vppctl packet-generator capture "$_cap" pcap "$_pcap" count 4 >/dev/null
  vppctl packet-generator new "name c limit 1 node ip4-input interface $_inj \
      size 60-60 data { UDP: $_src -> $_dst UDP: $_sp -> $_dp incrementing 8 }" \
      >/dev/null
  vppctl packet-generator enable >/dev/null; sleep 1
  vppctl packet-generator delete c >/dev/null 2>&1
}

vppctl set ip neighbor pg0 100.64.0.5 02:00:00:00:00:05 >/dev/null 2>&1
cap pg0 pg1 /tmp/handoff-out.pcap 100.64.0.5 8.8.8.8 12345 53
OUTCAP=$(tcpdump -nr /tmp/handoff-out.pcap 2>/dev/null | grep -v ARP | head -1)
echo "  in2out, captured on pg1: ${OUTCAP:-<nothing left the box>}"

cap pg1 pg0 /tmp/handoff-in.pcap 8.8.8.8 200.150.0.17 53 1536
INCAP=$(tcpdump -nr /tmp/handoff-in.pcap 2>/dev/null | grep -v ARP | head -1)
echo "  out2in, captured on pg0: ${INCAP:-<nothing left the box>}"

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
# On the captured packet, not on a counter. With the worker path sending the
# packet to error-drop these two are the only checks in this file that fail.
expect "a translated packet leaves the box on a worker build" \
  sh -c "[ -n \"$OUTCAP\" ]"
expect_in "carrying the subscriber's public address, not its private one" \
  "200.150.0.17" "$OUTCAP"
# This one passes when NOTHING leaves, and on the unfixed build it did. It is a
# "must not" guard rather than coverage, and saying so here is cheaper than
# somebody counting it later as a fifth check that caught the defect.
expect_not_in "and the private address does not leave" "100.64.0.5" "$OUTCAP"
expect "a reply reaches the subscriber on a worker build" \
  sh -c "[ -n \"$INCAP\" ]"
expect_in "translated back to the private address" "100.64.0.5" "$INCAP"
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
