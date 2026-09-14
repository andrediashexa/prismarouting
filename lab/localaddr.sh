#!/bin/sh
# A public pool that contains one of the box's own addresses.
#
#   sh tools/lab.sh localaddr
#
# `docs/routing.md` calls two defences REQUIRED, and the paragraph above them
# states the symptom: `bpa-out2in` runs on the `ip4-unicast` arc BEFORE
# `ip4-lookup`, so it sees the box's own BGP and OSPF packets first. A peering
# address inside a public pool means the box translates its own control plane,
# and the session drops for no apparent reason - a dead BGP session with
# healthy interfaces and a healthy dataplane, which is the worst kind of
# outage to diagnose because nothing is broken.
#
# Both defences were implemented and NEITHER was asserted anywhere. A MUST
# sweep neutralised both with four characters each and all 33 suites stayed
# green, because `bpa_check_local_overlap()` needs `vnet` and the unit suite
# links `bpa_pool.c` alone, and no lab had ever created a pool over one of its
# own interface addresses (#242). This is that lab.
#
# It has to be a lab and not a unit test for the same reason the gap existed:
# the question is what the BOX does when a real interface really carries the
# address, and `vnet`'s interface table is the thing being read.
#
# ---------------------------------------------------------------------------
# THE DOOR THOSE TWO DEFENCES DO NOT COVER, AND IT IS THE UNATTENDED ONE (#296)
#
# Sections 1 to 4 drive vppctl, so what they measure is the plugin on a box
# that already carries the address. The path an operator never watches is the
# other one. `hexash --load` applies POOLS BEFORE ADDRESSES, so at a boot
# `bpa_check_local_overlap()` walks an empty interface table, finds nothing,
# and the pool is created; the address lands a moment later and takes the
# `vlib_log_err` branch instead - a line on the plugin's log class, which is
# not on the console, not in `appliance/selftest`, and not in anything the VTY
# prints.
#
# MEASURED on this build before anything was changed, the same two statements
# in one file:
#
#   typed at a configured box: refused, exit 1, no pool created
#   loaded into an empty box:  exit 0, pool created, address applied, and one
#                              line in `show logging`
#
# So the box that refuses it is the one an operator is watching and the box
# that takes it is the one that came up on its own. Sections 5 to 7 are that
# reading, and they are the reason this lab restarts the dataplane half way
# through: the boot case only exists on a VPP that has nothing on it.
set -u

mkdir -p /run/vpp /vpp_plugins /var/log/hexarouting /etc/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh
# The VTY sections below commit through the binary API, not through vppctl.
use_binary_api
HX="python3 /src/src/hexash/hexash.py"

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
VPP_PID=$!
wait_vpp /tmp/vpp.log

# A real interface carrying a real address, because that is what the defence
# reads. A pg interface is enough: the check walks vnet's interface table and
# does not care how the packets would arrive.
vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface state pg0 up
vppctl set interface ip address pg0 200.150.0.20/28
echo "  the box's own address:"
vppctl show interface addr pg0 | sed 's/^/    /'

echo
echo "=========== 1. a pool whose public set contains it ==========="
# 200.150.0.16/28 is 200.150.0.16 through .31, so it contains .20.
REFUSAL=$(vppctl bpa pool clash inside 100.64.0.0/22 \
          outside 200.150.0.16/28 block 512 2>&1)
printf '%s\n' "$REFUSAL" | sed 's/^/    /'
CLASH_POOLS=$(vppctl show bpa pool | grep -c "^pool clash" || true)
echo "  pools named clash in the dataplane: $CLASH_POOLS"

echo
echo "=========== 2. a pool that clashes with nothing ==========="
# The control. Without it, a box that refused EVERY pool would satisfy the
# section above and this lab would be reporting a defence that does not exist.
CLEAN=$(vppctl bpa pool clean inside 100.64.4.0/22 \
        outside 200.150.9.0/28 block 512 2>&1)
printf '%s\n' "${CLEAN:-  (no output, which is what success looks like)}" | sed 's/^/    /'
CLEAN_POOLS=$(vppctl show bpa pool | grep -c "^pool clean" || true)
echo "  pools named clean in the dataplane: $CLEAN_POOLS"

echo
echo "=========== 3. the address configured AFTER the pool ==========="
# The half that cannot be refused: the pool exists first, and an operator
# numbers an interface into it later. The box cannot say no, so it has to say
# so loudly, in the place operators read.
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg1 up
vppctl clear logging
vppctl set interface ip address pg1 200.150.9.5/28
sleep 1
LOGGED=$(vppctl show logging)
printf '%s\n' "$LOGGED" | grep -i "200.150.9.5" | sed 's/^/    /'
echo "  (nothing above means the box said nothing)"

echo
echo "=========== 4. and an address that belongs to no pool is not reported ==========="
# The control for the second defence, and it is the one that keeps the message
# worth reading: a box that logged an error for every address configured
# anywhere would pass section 3 and teach operators to skip the line.
vppctl create packet-generator interface pg2 >/dev/null
vppctl set interface state pg2 up
vppctl clear logging
vppctl set interface ip address pg2 10.77.0.1/24
sleep 1
QUIET=$(vppctl show logging)
printf '%s\n' "$QUIET" | grep -i "10.77.0.1" | sed 's/^/    /'
echo "  (nothing above is what this section wants)"

echo
echo "=========== a dataplane with nothing on it ==========="
# THE BOX A BOOT PRODUCES, and the reason this lab restarts VPP rather than
# carrying on. Sections 1 to 4 left three interfaces carrying addresses, which
# is precisely what makes the plugin's walk able to see anything - so measuring
# the boot case on that box would be measuring the case that already works.
#
# Nothing here is a step the product is missing. A real boot starts a VPP that
# has just come up with no address on it and hands hexarouting.conf to
# `hexash --load`; this stops the dataplane and starts another one, which
# leaves the box with LESS than a real boot has and never more. The rollback
# store goes too, for the same reason: a box that has never been configured has
# no revisions, and leaving four sections' worth behind would be the fixture
# arranging a state a first boot does not have.
kill -TERM "$VPP_PID" 2>/dev/null
n=0
while kill -0 "$VPP_PID" 2>/dev/null && [ "$n" -lt 30 ]; do sleep 1; n=$((n+1)); done
kill -9 "$VPP_PID" 2>/dev/null
# The SOCKET as well as the process: a VPP that has gone leaves
# /run/vpp/cli.sock behind for a moment, and the next one exits with "Active
# listener on /run/vpp/stats.sock" - at which point the lab is reading a
# dataplane that is not there.
n=0
while vppctl show version >/dev/null 2>&1 && [ "$n" -lt 15 ]; do sleep 1; n=$((n+1)); done
rm -rf /etc/hexarouting/rollback
vpp -c "$(startup_conf test)" > /tmp/vpp-boot.log 2>&1 &
wait_vpp /tmp/vpp-boot.log
vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface state pg0 up
# READ, not assumed. A restart that left an address behind would make section 5
# green for the boot nobody is worried about, which is the whole reason this is
# an expect_setup at the verdict and not a comment.
EMPTY_L3=$(vppctl show interface addr | grep -c "L3" || true)
EMPTY_POOLS=$(vppctl show bpa pool | grep -c "^pool " || true)
echo "  L3 addresses on the new dataplane: $EMPTY_L3"
echo "  pools on the new dataplane:        $EMPTY_POOLS"

echo
echo "=========== 5. the same two statements, loaded at a boot ==========="
# One file holding both, which is what `save` writes and what
# hexarouting.service hands back at every boot.
cat > /tmp/localaddr-boot.conf <<'EOF'
interface pg0
 ipv4 address 200.150.0.20/28
 exit
cgnat pool boot
 inside 100.64.0.0/22
 outside 200.150.0.16/28
 block-size 512
 exit
EOF
sed 's/^/    /' /tmp/localaddr-boot.conf
vppctl clear logging
BOOTLOAD=$($HX --load /tmp/localaddr-boot.conf 2>&1); BOOTLOAD_RC=$?
printf '%s\n' "$BOOTLOAD" | sed 's/^/    /'
echo "  exit status: $BOOTLOAD_RC"
BOOT_POOLS=$(vppctl show bpa pool | grep -c "^pool boot" || true)
BOOT_L3=$(vppctl show interface addr | grep -c "200.150.0.20" || true)
echo "  pools named boot in the dataplane: $BOOT_POOLS"
echo "  the colliding address on an interface: $BOOT_L3"
echo "  and what the dataplane logged about it:"
vppctl show logging | grep -i "200.150.0.20" | sed 's/^/    /'
echo "  (nothing above is what this section wants: the refusal has to come"
echo "   BEFORE anything is applied, not as a log line afterwards)"

echo
echo "=========== 6. and typed at a box that already carries it ==========="
# The door an operator uses, on the box this lab started by measuring. The
# address goes on in its own commit, so the second commit meets a box that is
# already carrying it - which is the case the plugin can also see, and both
# halves firing on it is the point rather than a duplication to tidy away.
$HX -c "configure" -c "interface pg0" -c "ipv4 address 200.150.0.20/28" \
    -c "exit" -c "commit comment \"the uplink address\"" 2>&1 | sed 's/^/    /'
TYPED=$($HX -c "configure" -c "cgnat pool typed" \
        -c "inside 100.64.0.0/22" -c "outside 200.150.0.16/28" \
        -c "block-size 512" -c "exit" -c "commit" 2>&1); TYPED_RC=$?
printf '%s\n' "$TYPED" | sed 's/^/    /'
echo "  exit status: $TYPED_RC"
TYPED_POOLS=$(vppctl show bpa pool | grep -c "^pool typed" || true)
echo "  pools named typed in the dataplane: $TYPED_POOLS"

echo
echo "=========== 7. a pool the VTY has no reason to refuse ==========="
# The control for sections 5 and 6, and it is the one that matters most here.
# A VTY that refused every pool would satisfy both of them, and the refusal
# would have been a blanket one nobody could configure a box through.
VTY_CLEAN=$($HX -c "configure" -c "cgnat pool vtyclean" \
            -c "inside 100.64.4.0/22" -c "outside 198.51.100.0/28" \
            -c "block-size 512" -c "exit" -c "commit" 2>&1); VTY_CLEAN_RC=$?
printf '%s\n' "$VTY_CLEAN" | sed 's/^/    /'
VTY_CLEAN_POOLS=$(vppctl show bpa pool | grep -c "^pool vtyclean" || true)
echo "  pools named vtyclean in the dataplane: $VTY_CLEAN_POOLS"

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

expect_in "a pool whose public set holds a local address is refused" \
  "is configured on" "$REFUSAL"
expect_in "and the refusal names the address" "200.150.0.20" "$REFUSAL"
expect_in "and the interface it is configured on" "pg0" "$REFUSAL"
expect "and the pool does not exist" test "${CLASH_POOLS:-1}" -eq 0

expect "a pool that clashes with nothing is created" \
  test "${CLEAN_POOLS:-0}" -eq 1

expect_in "an address configured into an existing pool is reported" \
  "belongs to public pool" "$LOGGED"
expect_in "at error level, which is what show logging shows an operator" \
  "err" "$LOGGED"
expect_in "and it names the pool" "clean" "$LOGGED"

expect_not_in "an address belonging to no pool is not reported" \
  "belongs to public pool" "$QUIET"

# The boot the sections above cannot be read without. A restart that left an
# address or a pool behind measures the case that already worked, so it is a
# PRECONDITION and its failure is NOT MEASURED rather than a product defect.
expect_setup "the second dataplane started with no address on it" \
  test "${EMPTY_L3:-1}" -eq 0
expect_setup "and with no pool on it" test "${EMPTY_POOLS:-1}" -eq 0

# #296. The refusal has to happen at CHECK time, before anything is applied,
# or the two paths do not agree about what is legal.
expect "a file whose pool holds an address it also configures is refused" \
  test "${BOOTLOAD_RC:-0}" -ne 0
expect_said "and the refusal names the address and the interface" \
  "public address 200.150.0.20 is configured on pg0" "$BOOTLOAD"
expect_said "and says what CGNAT would do to the box's own traffic" \
  "translate the box's own control-plane traffic on that address" "$BOOTLOAD"
expect_said "and what the operator can do about it" \
  "take the address off the interface, or narrow the pool's public range" \
  "$BOOTLOAD"
# The statements printed WHOLE, because deleting one of them is the next move
# and it is grepped for in `show configuration` (#252). expect_in and not
# expect_said on purpose: a statement that WRAPPED would satisfy a collapsed
# comparison, and that is the thing being checked here.
expect_in "and quotes the address statement whole" \
  "    interface pg0 / ipv4 address 200.150.0.20/28" "$BOOTLOAD"
expect_in "and the pool statement whole" \
  "    cgnat pool boot / outside 200.150.0.16/28" "$BOOTLOAD"
expect "and NOTHING was applied: no pool" test "${BOOT_POOLS:-1}" -eq 0
expect "and no address" test "${BOOT_L3:-1}" -eq 0

expect "the same pool typed at a box already carrying it is refused" \
  test "${TYPED_RC:-0}" -ne 0
expect_said "and the VTY says so in the same words the plugin uses" \
  "public address 200.150.0.20 is configured on pg0" "$TYPED"
expect "and that pool does not exist either" test "${TYPED_POOLS:-1}" -eq 0

# The control. Without it every assertion above is satisfied by a VTY that
# refuses all pools, which is a box nobody can configure.
expect "a pool the VTY has no reason to refuse still commits" \
  test "${VTY_CLEAN_RC:-1}" -eq 0
expect "and reaches the dataplane" test "${VTY_CLEAN_POOLS:-0}" -eq 1

verdict
