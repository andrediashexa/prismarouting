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
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
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
#
# T is a second tenant on pool B's inside prefix, which a VRF makes legitimate
# and which the overlap check therefore allows. It carries no traffic: it is
# here because it claims 10.0.0.5 and is found BEFORE B, and a command with no
# packet in hand has no VRF with which to pass it over (issue #74).
vppctl bpa pool A inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
vppctl ip table add 1
vppctl bpa pool T vrf 1 inside 10.0.0.0/22 outside 203.0.113.32/30 block 512
vppctl bpa pool B vrf 0 inside 10.0.0.0/22 outside 203.0.113.16/30 block 512
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
echo "=========== 3. the two commands, about the same subscriber ==========="
# The one an operator reaches for during a support call, next to the one that
# counts the table. They must not contradict each other.
SUB=$(vppctl show bpa subscriber 10.0.0.5)
SESS=$(vppctl show bpa session)
printf '%s\n' "$SUB" | sed 's/^/  |/'
printf '%s\n' "$SESS" | sed 's/^/  |/'

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
# Issue #74. The session table holds this subscriber's session; the command an
# operator asks during the support call said it held nothing at all, because it
# looked for that state under the first pool claiming the address rather than
# the one the traffic was translated by.
expect_in "the session table holds a session for each subscriber" \
  "total: 2 sessions" "$SESS"
expect_not_in "show bpa subscriber does not deny state the table holds" \
  "no live state" "$SUB"
expect_in "and counts the same session the table does" "sessions 1" "$SUB"
# The block it names is a court-order answer. Naming the other tenant's would
# be a wrong one given with full confidence.
expect_in "and names the block the subscriber was translated on" \
  "203.0.113.1" "$SUB"
verdict
