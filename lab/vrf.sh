#!/bin/sh
# Two tenants, one private address range, two pools.
#
#   sh tools/lab.sh vrf        with the pools bound to VRFs
#   sh tools/lab.sh vrf off    without - and the failure it prevents is shown
#
# The failure this exists to stop is silent: two subscribers with the same
# private address in different VRFs are given the same public address, and one
# customer's traffic is recorded against the other's mapping. Nothing logs it
# and nothing counts it. So "off" does not merely fail a check - it prints the
# wrong answer, which is the only way to show what "right" is worth.
#
# OFF-RED-AT-LEAST: 2
#
# A FLOOR, not a target, and the number is measured rather than chosen: it is
# what this lab's off run went red on in the release suite of 2026-09-13.
# tools/run-all-labs.sh refuses an off run that goes red on fewer, because a
# lab that used to fail eleven checks and now fails one has lost ten assertions
# without losing its verdict - which is the defect this whole sweep exists to
# find. Gaining assertions is fine and needs no edit here; losing them is what
# has to be deliberate.
set -u
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
MODE=${1:-on}

mkdir -p /run/vpp /vpp_plugins /var/log/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
n=0
until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

for i in 0 1 2; do
  vppctl create packet-generator interface pg$i >/dev/null
  vppctl set interface state pg$i up
done

# Two tenants. pg0 is tenant A, pg2 is tenant B, and both use 100.64.0.0/22 -
# which is the whole point: a provider does not get to choose what private
# range its customers' CPE hands out.
vppctl ip table add 1 >/dev/null
vppctl ip table add 2 >/dev/null
vppctl set interface ip table pg0 1
vppctl set interface ip table pg2 2
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg2 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
# The tenants' own tables need the route out, or the translated packet is
# dropped for having nowhere to go and the lab measures the route.
vppctl ip route add table 1 8.8.8.8/32 via 200.150.0.2 pg1
vppctl ip route add table 2 8.8.8.8/32 via 200.150.0.2 pg1

echo "=========== 1. two pools, one inside range ==========="
if [ "$MODE" = off ]; then
  echo "  MODE=off: one pool, bound to no VRF, serving both tenants - which is"
  echo "  what this box did before a pool could name a VRF."
  vppctl bpa pool tenant-a inside 100.64.0.0/22 outside 200.150.0.16/30 \
      block 512
else
  vppctl bpa pool tenant-a inside 100.64.0.0/22 outside 200.150.0.16/30 \
      block 512 vrf 1
  vppctl bpa pool tenant-b inside 100.64.0.0/22 outside 200.150.0.32/30 \
      block 512 vrf 2
fi
vppctl show bpa pool | grep -E "^pool|vrf|outside " | sed 's/^/  /'
vppctl set interface bpa in2out pg0
vppctl set interface bpa in2out pg2
vppctl set interface bpa out2in pg1

send() { # send <interface> <sport>
  vppctl packet-generator new "name s limit 1 node ip4-input interface $1 \
      size 60-60 data { UDP: 100.64.0.5 -> 8.8.8.8 \
                        UDP: $2 -> 53 \
                        incrementing 8 }" >/dev/null
  vppctl clear trace >/dev/null
  vppctl trace add pg-input 5 >/dev/null
  vppctl packet-generator enable >/dev/null
  sleep 1
  vppctl packet-generator delete s >/dev/null 2>&1
}
pub_of() { vppctl show trace | grep -o "100.64.0.5:$1 -> [0-9.]*" | head -1 |
             sed 's/.*-> //'; }

echo
echo "=========== 2. the same address AND the same port, from each ==========="
echo "the same source port on purpose: with different ports the two flows have"
echo "different keys anyway, and the collision this prevents would not show."
send pg0 40001
A=$(pub_of 40001)
vppctl show trace | grep "BPA in2out" | sed 's/^/  A: /'
send pg2 40001
B=$(pub_of 40001)
vppctl show trace | grep "BPA in2out" | sed 's/^/  B: /'
echo "  tenant A left on $A"
echo "  tenant B left on $B"

echo
echo "=========== 3. and they are two sessions, not one ==========="
vppctl show bpa session | tail -1 | sed 's/^/  /'
SESSIONS=$(vppctl show bpa session | grep -o "total: [0-9]*" | awk '{print $2}')

echo
echo "=========== 4. what has to be refused ==========="
# A third pool claiming the same range with no VRF would answer for whichever
# tenant reached it first, and the other pool would translate nothing.
R_SAME=$(vppctl bpa pool greedy inside 100.64.0.0/22 outside 198.51.100.0/28 block 512 2>&1)
# Two pools sharing a public address: the return path keys on it and would
# have two answers.
R_PUB=$(vppctl bpa pool clash inside 172.16.0.0/24 outside 200.150.0.16/30 block 512 vrf 1 2>&1)
echo "  $R_SAME"
echo "  $R_PUB"

echo
echo "=========== 5. a VRF that does not exist ==========="
R_NOVRF=$(vppctl bpa pool ghost inside 10.0.0.0/24 outside 203.0.113.16/30 block 64 vrf 99 2>&1)
echo "  $R_NOVRF"

echo
echo "=========== 6. in the running configuration ==========="
CONF=$(vppctl show bpa config)
printf '%s\n' "$CONF" | grep -E "cgnat pool|vrf" | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

expect "tenant A was translated" test -n "$A"
expect "tenant B was translated" test -n "$B"
# THE assertion. Off, both come out on the same public address and one
# subscriber's traffic is recorded against the other's mapping.
expect "the two tenants got different public addresses" test "$A" != "$B"
# Two sessions for one (address, port, protocol). That is only possible
# because the key carries the VRF: without it both flows key identically, the
# second lookup HITS the first session, and tenant B is translated onto tenant
# A's mapping - one session, one record, the wrong customer.
#
# Note what "off" does and does not reach. It removes the pool's VRF BINDING,
# so the check above flips. It does not remove the VRF from the KEY, which is
# not a runtime switch - falsifying this one means building without it. Said
# here rather than left for a reader to assume the argument covers both.
expect "and they are two sessions, not one shared key" \
  test "${SESSIONS:-0}" -eq 2

expect_in "a pool claiming a served range with no VRF is refused" \
  "already served by pool" "$R_SAME"
expect_in "and the refusal names the pool it collides with" \
  "tenant-a" "$R_SAME"
expect_in "two pools cannot share a public address" \
  "already in pool" "$R_PUB"
expect_in "a VRF that does not exist is refused" "does not exist" "$R_NOVRF"
expect_in "the VRF is in the running configuration" " vrf 1" "$CONF"

# --- an interface in a VRF, through the VTY (#132) ---
#
# Pools have carried a VRF since v0.18.0 and there was no way to put an
# INTERFACE in one, so the feature was half-built: the pool named a table
# nothing could be bound to.
#
# The sequence matters. VPP REFUSES to move an interface that still holds
# addresses - retval -114, not a silent clear - so the VTY has to remove,
# move, and re-add. The second assertion below is the one that catches a
# commit that does the move without the addresses: it fails by construction
# if nothing re-applies them.
use_binary_api
HX="python3 /src/src/hexash/hexash.py"
vppctl create packet-generator interface pg9 >/dev/null
vppctl set interface state pg9 up

echo
echo "=========== an interface in a VRF ==========="
$HX -c "configure" -c "interface pg9" -c "vrf 7"     -c "ipv4 address 10.7.0.1/24" -c "exit" -c "commit" 2>&1 | sed 's/^/  /'
echo "  table 7:"; vppctl show ip fib table 7 10.7.0.0/24 2>&1 | head -2 | sed 's/^/    /'
expect_in "the address is in the VRF's own table" "10.7.0.0/24"   "$(vppctl show ip fib table 7 10.7.0.0/24 2>&1)"
expect_not_in "and not in the default table" "10.7.0.0/24"   "$(vppctl show ip fib table 0 10.7.0.0/24 2>&1)"
expect_in "the VTY reads the interface's VRF back from the box" "vrf 7"   "$($HX -c 'show running-config' | sed -n '/^interface pg9$/,/^ exit$/p')"

echo
echo "=========== moving it to another VRF ==========="
# The address must FOLLOW. A commit that moves the interface and leaves its
# addresses behind produces a port in the right table with nothing on it, and
# reports success.
$HX -c "configure" -c "interface pg9" -c "vrf 8" -c "exit" -c "commit"     2>&1 | sed 's/^/  /'
expect_in "the address followed the interface into the new table"   "10.7.0.0/24" "$(vppctl show ip fib table 8 10.7.0.0/24 2>&1)"
expect_not_in "and left the old one" "10.7.0.0/24"   "$(vppctl show ip fib table 7 10.7.0.0/24 2>&1)"

verdict
