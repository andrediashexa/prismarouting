#!/bin/sh
# Two pools, and a subscriber at the same index in each.
#
# Every other lab configures exactly one pool. With one pool the subscriber key
# is unique by accident, and everything works. With two, subscriber 5 of pool A
# and subscriber 5 of pool B hashed to the same record and shared one set of
# port bitmaps: a subscriber of B left on A's public address, and the RFC 8158
# log named A's block for it - a wrong answer to a court order, produced with
# full confidence (issue #57).
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
vppctl set interface ip address pg1 200.150.0.1/24
vppctl ip route add 10.0.0.0/22 via 100.64.3.253 pg0
vppctl set ip neighbor pg0 100.64.3.253 02:00:00:00:00:01
vppctl set ip neighbor pg0 100.64.0.5 02:00:00:00:01:05
vppctl set ip neighbor pg0 10.0.0.5 02:00:00:00:01:06

# 100.64.0.5 is index 5 of pool A. 10.0.0.5 is index 5 of pool B.
vppctl bpa pool A inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
vppctl bpa pool B inside 10.0.0.0/22   outside 203.0.113.16/30 block 512
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1

out() {   # <source address>
  vppctl clear trace >/dev/null; vppctl trace add pg-input 3 >/dev/null
  vppctl packet-generator delete o >/dev/null 2>&1
  vppctl packet-generator new "name o limit 1 node ip4-input interface pg0 \
      size 60-60 data { UDP: $1 -> 8.8.8.8 UDP: 40000 -> 53 incrementing 8 }"
  vppctl packet-generator enable >/dev/null; sleep 0.4
  vppctl packet-generator delete o >/dev/null 2>&1
  vppctl show trace | grep -m1 "BPA in2out"
}

echo "=========== 1. one subscriber from each pool, same index ==========="
A=$(out 100.64.0.5); echo "  A: $A"
B=$(out 10.0.0.5);   echo "  B: $B"
A_PUB=$(printf '%s' "$A" | grep -oE "> [0-9.]+:[0-9]+" | tail -1 | cut -d' ' -f2)
B_PUB=$(printf '%s' "$B" | grep -oE "> [0-9.]+:[0-9]+" | tail -1 | cut -d' ' -f2)

echo
echo "=========== 2. the return path for pool B ==========="
# End-to-end usefulness, not the discriminator. Measured: this one passes on
# the broken code too, because the session recorded the right private address
# even though it was built on the wrong pool's block - so the return packet
# arrived correctly at an address that should never have been reachable that
# way. Said here so a reader does not mistake it for the proof. The proof is
# the public range each subscriber left on.
rm -f /tmp/b.pcap
vppctl packet-generator capture pg0 pcap /tmp/b.pcap count 1 >/dev/null
vppctl packet-generator delete i >/dev/null 2>&1
vppctl packet-generator new "name i limit 1 node ip4-input interface pg1 \
    size 60-60 data { UDP: 8.8.8.8 -> ${B_PUB%%:*} UDP: 53 -> ${B_PUB##*:} \
    incrementing 8 }"
vppctl packet-generator enable >/dev/null; sleep 0.5
vppctl packet-generator delete i >/dev/null 2>&1
BACK=$(tcpdump -nr /tmp/b.pcap 2>/dev/null | head -1)
echo "  delivered to: ${BACK:-nothing arrived}"

echo
echo "=========== VERDICT ==========="
expect "pool A's subscriber was translated" test -n "${A_PUB:-}"
expect "pool B's subscriber was translated" test -n "${B_PUB:-}"
expect_in "pool A leaves on pool A's public range" "200.150.0." "${A_PUB:-}"
expect_in "pool B leaves on POOL B's public range, not pool A's" \
  "203.0.113." "${B_PUB:-}"
# Each block starts at the same offset, so two subscribers that do not share a
# bitmap both get its first port. Sharing one showed up as 1536 and 1537.
expect "each subscriber got the first port of its own block" \
  test "${A_PUB##*:}" = "${B_PUB##*:}"
expect_in "and pool B's return traffic reaches pool B's subscriber" \
  "10.0.0.5.40000" "${BACK:-}"
verdict
