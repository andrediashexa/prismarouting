#!/bin/sh
# Sub-interfaces and VLAN tags (#131).
#
# The test that matters is not that the sub-interface exists - it is that a
# frame with tag 100 lands on .100 and a frame with tag 200 does not. Counting
# configuration proves the VTY talked to VPP; counting packets proves the box
# does what the operator asked.
set -u
mkdir -p /run/vpp /vpp_plugins /vat2_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh
use_binary_api

vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
n=0
until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface state pg0 up

HX="python3 /src/src/hexash/hexash.py"

echo "=========== creating a sub-interface through the VTY ==========="
$HX -c "configure" -c "interface pg0.100" -c "vlan 100" \
    -c "ipv4 address 10.100.0.1/24" -c "exit" -c "commit" 2>&1 | sed 's/^/  /'

vppctl set interface state pg0.100 up 2>/dev/null

echo "  interfaces on the dataplane now:"
vppctl show interface | awk 'NR>1 {print "    " $1}' | grep -v '^    $'

. /src/lab/verdict.sh
IFACES=$(vppctl show interface | awk '{print $1}')
expect_in "the sub-interface exists on the dataplane" "pg0.100" "$IFACES"
expect_in "with the address it was given" "10.100.0.1/24" \
  "$(vppctl show interface addr pg0.100)"
expect_in "and the VTY reads its tag back from the box" "vlan 100" \
  "$($HX -c 'show running-config' | sed -n '/^interface pg0.100$/,/^ exit$/p')"

# --- the part that is about packets ---
#
# A dot1q frame is built by hand: destination and source MAC, 0x8100, the tag,
# 0x0800, then a minimal IPv4 header. The pg is given ethernet-input rather
# than ip4-input, because the whole question is whether the L2 tag steers the
# frame onto the right sub-interface, and injecting at L3 skips exactly that.
frame() {  # $1 = vlan id in hex, 4 digits
  printf '02fe000000010200000000028100%s08004500002e0001000040110000' "$1"
  printf '0a6400020a640001'          # 10.100.0.2 -> 10.100.0.1
  printf '04d204d2001a0000'          # udp 1234 -> 1234
  printf '00000000000000000000000000000000'
}
# The count is the LAST field of the "rx packets" line, whether that line
# carries the interface name or is a continuation. Reading a fixed column
# gave "up" - the State - and the assertion then compared two strings that
# were never numbers.
count_of() { vppctl show interface "$1" | awk '/rx packets/ {print $NF}' | head -1; }

echo
echo "=========== a frame tagged 100 ==========="
BEFORE=$(count_of pg0.100)
vppctl packet-generator new "name v100 limit 1 node ethernet-input \
    interface pg0 size 60-60 data { hex 0x$(frame 0064) }" >/dev/null
vppctl packet-generator enable >/dev/null; sleep 1
vppctl packet-generator delete v100 >/dev/null 2>&1
AFTER=$(count_of pg0.100)
echo "  pg0.100 rx: ${BEFORE:-0} -> ${AFTER:-0}"
expect "the tagged frame landed on the sub-interface" \
  test "${AFTER:-0}" -gt "${BEFORE:-0}"

echo
echo "=========== a frame tagged 200, which has no sub-interface ==========="
BEFORE=$(count_of pg0.100)
vppctl packet-generator new "name v200 limit 1 node ethernet-input \
    interface pg0 size 60-60 data { hex 0x$(frame 00c8) }" >/dev/null
vppctl packet-generator enable >/dev/null; sleep 1
vppctl packet-generator delete v200 >/dev/null 2>&1
AFTER=$(count_of pg0.100)
echo "  pg0.100 rx: ${BEFORE:-0} -> ${AFTER:-0}"
# This is the assertion the whole lab exists for. Without it, a sub-interface
# that swallowed every frame regardless of tag would pass everything above.
expect "a frame with a different tag did NOT land on it" \
  test "${AFTER:-0}" -eq "${BEFORE:-0}"

echo
echo "=========== removing it through the VTY ==========="
$HX -c "configure" -c "no interface pg0.100" -c "commit" 2>&1 | sed 's/^/  /'
IFACES=$(vppctl show interface | awk '{print $1}')
expect_not_in "the sub-interface is gone from the dataplane" "pg0.100" "$IFACES"
expect_in "and its parent is untouched" "pg0" "$IFACES"

verdict
