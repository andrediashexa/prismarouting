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
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
use_binary_api

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
n=0
until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface state pg0 up

HX="python3 /src/src/prismash/prismash.py"

echo "=========== what ? says a name can create (#406) ==========="
# THE SHAPE, ON THE SCREEN AN OPERATOR IS LOOKING AT WHILE THEY TYPE. Typing a
# name the box has not got is how a sub-interface is made, it has worked since
# #131, and `?` offered only the interfaces that already existed - so the list
# read as the complete set of what may be typed, and a maintainer asked for
# sub-interfaces to be implemented on a box that had had them for releases.
#
# Driven through the completer itself, not through a copy of the text: the
# hint and the note are what `?` prints, and a lab asserting a constant it
# wrote itself would assert nothing about the box.
HINT=$(python3 - <<'PYEOF'
import io, contextlib, sys
sys.path.insert(0, "/src/src/prismash")
import prismash
sh = prismash.Shell()
sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT}
with contextlib.redirect_stdout(io.StringIO()):
    sh.feed("configure")
opts, hint, _ = sh._options("interface ", False)
print("NAMES:", " ".join(sorted(opts)))
print("HINT:", hint)
print("NOTE:", prismash.HINT_NOTES.get(hint, ""))
PYEOF
)
printf '%s\n' "$HINT" | sed 's/^/  /'

echo "=========== creating a sub-interface through the VTY ==========="
$HX -c "configure" -c "interface pg0.100" -c "vlan 100" \
    -c "ipv4 address 10.100.0.1/24" -c "exit" -c "commit" 2>&1 | sed 's/^/  /'

vppctl set interface state pg0.100 up 2>/dev/null

echo "  interfaces on the dataplane now:"
vc show interface | awk 'NR>1 {print "    " $1}' | grep -v '^    $'

. /src/lab/verdict.sh
IFACES=$(vppctl show interface | awk '{print $1}')
# #406, before the sub-interface below exists: the screen has to say the name
# can be typed BEFORE there is one to complete.
expect_in "? offers the interfaces the box has" "pg0" "$HINT"
expect_in "and says a name can create one" "or a name that creates one" "$HINT"
expect_in "naming the shape that makes a sub-interface" "<parent>.<tag>" "$HINT"
expect_in "and the one that makes a loopback" "loop<n>" "$HINT"
expect_in "and that the parent has to exist" "its parent has to exist" "$HINT"

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
count_of() { vc show interface "$1" | awk '/rx packets/ {print $NF}' | head -1; }

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
echo "=========== the detailed view of one interface ==========="
# Traffic has already been through pg0 by now, so the counters are not zero.
# A report that only ever shows zeros is a report nobody can use during a
# fault, and it passes every test that does not create a packet first.
DETAIL=$($HX -c "show interface pg0")
echo "$DETAIL" | sed 's/^/  /'
# The VALUE beside the label, which is the only part of this view an operator
# reads during a fault. "vrf", "urpf" and "cgnat" are labels that
# show_one_interface prints on every interface whatever the state is, so all
# three passed on a box reporting pg0 in VRF 1, with uRPF strict and CGNAT
# attached in2out - and pg0 here is in the default table, has no uRPF at all
# and has nothing attached. Being told anti-spoofing is on where there is none
# is the reading that stops somebody looking for why a spoofed source was
# translated.
row_of() { printf '%s\n' "$2" | sed -n "s/^ *$1  *//p" | head -1; }
VRF_ROW=$(row_of vrf "$DETAIL")
URPF_ROW=$(row_of urpf "$DETAIL")
CGNAT_ROW=$(row_of cgnat "$DETAIL")
echo "  rows read back: vrf='${VRF_ROW:-<none>}' urpf='${URPF_ROW:-<none>}'" \
     "cgnat='${CGNAT_ROW:-<none>}'"
expect_in "naming one interface gives the detailed view, not the table" \
  "counters" "$DETAIL"
expect "it reports the table this interface is really in" \
  test "$VRF_ROW" = "0 (default)"
expect "and CGNAT as not attached, which is what pg0 is here" \
  test "$CGNAT_ROW" = "not attached"
expect "and the uRPF mode the dataplane really has" test "$URPF_ROW" = "off"
# ...and "off" is read from the device, not printed unconditionally. A view
# that always said "off" would pass the line above, and it would be wrong in
# the direction that matters more: an operator told uRPF is off turns it on
# again on a box that already has it, which is a commit and a risk for nothing.
$HX -c "configure" -c "interface pg0" -c "urpf loose" -c "exit" -c "commit" \
    >/dev/null 2>&1
URPF_ON=$(row_of urpf "$($HX -c 'show interface pg0')")
$HX -c "configure" -c "interface pg0" -c "no urpf" -c "exit" -c "commit" \
    >/dev/null 2>&1
URPF_OFF=$(row_of urpf "$($HX -c 'show interface pg0')")
echo "  urpf row with it on: '${URPF_ON:-<none>}', after 'no urpf': '${URPF_OFF:-<none>}'"
expect "the uRPF row follows the device: loose when it is loose" \
  test "$URPF_ON" = "loose"
expect "  and off again when it is taken off" test "$URPF_OFF" = "off"
# The property, not a counter name. Asserting "rx packets" tied the test to
# which counters VPP happens to have moved off zero - this box had "rx bytes"
# and "punt" - and a test that names one counter fails when the box is working
# and reports a different one.
expect_not_in "and the counters are real, not all zero" \
  "all zero" "$DETAIL"
expect_in "with at least one of them shown" "rx bytes" "$DETAIL"

echo
echo "=========== a QinQ sub-interface, and what it really matches ==========="
# #136. The VTY can only send SUB_IF_API_FLAG_TWO_TAGS, because vat2 renders a
# bitfield as ONE enum name and crashes on every other spelling. The flag it
# cannot send is EXACT_MATCH - and this lab is where it was established that
# sending it would change nothing observable anyway: a sub-interface created
# by the VPP CLI *with* exact-match took the three-tag frame below just the
# same. VPP's eth_input_tag_lookup() reads "three or more tags" as two.
#
# So the assertions here are the honest ones: the QinQ sub-interface carries
# what it was asked to carry, the VTY says out loud what else it accepts, and
# the over-match is asserted rather than described - if a later VPP stops
# taking the three-tag frame, this lab fails and the warning comes out.
QQ=$($HX -c "configure" -c "interface pg0.700" -c "vlan 700 inner 900" \
         -c "exit" -c "commit" 2>&1)
echo "$QQ" | sed 's/^/  /'
vppctl set interface state pg0.700 up 2>/dev/null

expect_in "the QinQ sub-interface exists on the dataplane" "pg0.700" \
  "$(vppctl show interface | awk '{print $1}')"
expect_in "and the VTY reads both tags back from the box" "vlan 700 inner 900" \
  "$($HX -c 'show running-config' | sed -n '/^interface pg0.700$/,/^ exit$/p')"
expect_in "creating one says what it will accept" "more tags" "$QQ"

# Same recipe as frame() above, with as many tags as asked for.
qframe() {  # $@ = vlan ids in hex, 4 digits each
  printf '02fe00000001020000000002'
  for t in "$@"; do printf '8100%s' "$t"; done
  printf '08004500002e0001000040110000'
  printf '0a6400020a640001'
  printf '04d204d2001a0000'
  printf '00000000000000000000000000000000'
}
qshoot() {  # $1 = size, $2 = hex
  vppctl packet-generator new "name qq limit 1 node ethernet-input \
      interface pg0 size $1-$1 data { hex 0x$2 }" >/dev/null
  vppctl packet-generator enable >/dev/null; sleep 1
  vppctl packet-generator delete qq >/dev/null 2>&1
}

BEFORE=$(count_of pg0.700)
qshoot 64 "$(qframe 02bc 0384)"
AFTER=$(count_of pg0.700)
echo "  two tags 700/900:   ${BEFORE:-0} -> ${AFTER:-0}"
expect "a frame tagged 700 outer, 900 inner lands on it" \
  test "${AFTER:-0}" -gt "${BEFORE:-0}"

BEFORE=$(count_of pg0.700)
qshoot 64 "$(qframe 02bd 0384)"
AFTER=$(count_of pg0.700)
echo "  two tags 701/900:   ${BEFORE:-0} -> ${AFTER:-0}"
expect "a different outer tag does NOT land on it" \
  test "${AFTER:-0}" -eq "${BEFORE:-0}"

BEFORE=$(count_of pg0.700)
qshoot 68 "$(qframe 02bc 0384 0007)"
AFTER=$(count_of pg0.700)
echo "  three tags 700/900/7: ${BEFORE:-0} -> ${AFTER:-0}"
expect "a THIRD tag lands on it too, which is the limitation warned about" \
  test "${AFTER:-0}" -gt "${BEFORE:-0}"

echo
echo "=========== a MAC set through the VTY ==========="
$HX -c "configure" -c "interface pg0" -c "mac-address 02:aa:bb:cc:dd:ee" \
    -c "exit" -c "commit" > /tmp/mac.log 2>&1 || true
tail -2 /tmp/mac.log | sed 's/^/  commit: /'
expect_in "the dataplane answers to the new MAC" "02:aa:bb:cc:dd:ee" \
  "$($HX -c 'show interface pg0')"
expect_in "and the VTY reads it back from the box" "02:aa:bb:cc:dd:ee" \
  "$($HX -c 'show running-config' | sed -n '/^interface pg0$/,/^ exit$/p')"

echo
echo "=========== removing it through the VTY ==========="
$HX -c "configure" -c "no interface pg0.100" -c "no interface pg0.700" \
    -c "commit" 2>&1 | sed 's/^/  /'
IFACES=$(vppctl show interface | awk '{print $1}')
expect_not_in "the sub-interface is gone from the dataplane" "pg0.100" "$IFACES"
expect_not_in "the QinQ one too" "pg0.700" "$IFACES"
expect_in "and its parent is untouched" "pg0" "$IFACES"

verdict
