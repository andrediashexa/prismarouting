#!/bin/sh
# Extended blocks: a subscriber that fills its block gets another.
#
# The behaviour this replaces is correct and terrible: natEvent 12 is logged
# and the packet is dropped, so one customer's internet stops while the box is
# healthy and the neighbours are fine.
#
# Only dynamic mode. A deterministic pool has no spare block by construction -
# the formula assigns every block to some subscriber - so extending there means
# taking a block from somebody else and breaking their mapping silently. That
# needs a reserve carved out of capacity, which is issue #39.
set -u
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up; vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0

# Tiny blocks so a subscriber fills one with a handful of packets. Four blocks
# on one public address, and a cap of two per subscriber.
vppctl bpa pool ext inside 100.64.0.0/22 outside 200.150.0.16/32 \
    ports 1024-1039 block 4 mode dynamic max-blocks 2
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa timeouts udp 600 >/dev/null

echo "=========== pool: 16 ports / 4-port blocks = 4 blocks, cap 2 ==========="
vppctl show bpa pool | sed 's/^/  /'

send() {   # subscriber, source port
  vppctl packet-generator delete s >/dev/null 2>&1
  vppctl packet-generator new "name s limit 1 node ip4-input interface pg0 \
      size 60-60 data { UDP: 100.64.0.$1 -> 8.8.8.8 UDP: $2 -> 53 incrementing 8 }"
  vppctl packet-generator enable >/dev/null; sleep 0.4
  vppctl packet-generator delete s >/dev/null 2>&1
}

echo
echo "=========== one subscriber, five flows into a four-port block ==========="
for sp in 5001 5002 5003 5004; do send 5 $sp; done
FIRST=$(vppctl show bpa blocks | grep -o "total: [0-9]*" | awk '{print $2}')
echo "  after four flows: $(vppctl show bpa blocks | grep total | sed 's/^ *//')"

send 5 5005
echo "  after the fifth:  $(vppctl show bpa blocks | grep total | sed 's/^ *//')"
EXTS=$(vppctl show bpa blocks | grep -o "([0-9]* extensions)" | grep -o "[0-9]*" | head -1)
SESS=$(vppctl show bpa session | grep -o "total: [0-9]*" | awk '{print $2}')
echo "  extensions: ${EXTS:-0}   sessions: ${SESS:-0}"

echo
echo "=========== the cap holds: a ninth flow must be refused ==========="
for sp in 5006 5007 5008; do send 5 $sp; done
send 5 5009
REFUSED=$(vppctl show errors | grep -i "port block exhausted" | awk '{print $1}' | head -1)
SESS2=$(vppctl show bpa session | grep -o "total: [0-9]*" | awk '{print $2}')
echo "  sessions now: ${SESS2:-0}   refusals: ${REFUSED:-0}"
vppctl show bpa blocks | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
expect "the subscriber filled its first block and got a second" \
  test "${EXTS:-0}" -ge 1
expect "eight sessions fit in two four-port blocks" test "${SESS2:-0}" = "8"
expect "the ninth was refused, so the cap is a cap" test -n "${REFUSED:-}"
expect_in "the extension was logged as an allocation" "allocations" \
  "$(vppctl show bpa blocks)"
verdict
