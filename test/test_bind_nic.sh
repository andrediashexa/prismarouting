#!/bin/sh
# appliance/bind-dataplane-nic, driven against fake boxes.
#
# The binder is the piece that was never written: nothing in this product ever
# bound a NIC to a driver DPDK can use, so an operator who named the interface
# as the self-test told them to still got a dataplane with no interfaces
# (#283). It runs before VPP, as root, on the real PCI bus - so every path it
# reads is a variable, and this drives it against directory trees shaped like
# the /sys it expects.
#
# WHAT IS ASSERTED IS THE DECISION, not the outcome of a kernel call this test
# has no kernel for. A fake tree cannot actually bind a device, so:
#
#   - the branches that decide NOT to touch anything are asserted by their
#     verdict and their exit status, which is all they do;
#   - the branch that binds is asserted by the three writes it makes and by
#     what it writes in them, which is the whole of the action;
#   - the verification after the write is asserted by both of its answers -
#     a device that ends on the wanted driver, and one that does not.
#
# The refusal is the case worth the most care. Binding the interface carrying
# the default route takes the box off the network and leaves a console
# somebody has to walk to, and that is the one mistake here that cannot be
# undone remotely.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }
REPO=$(cd "$(dirname "$0")/.." && pwd)
BIN=$REPO/appliance/bind-dataplane-nic

# The pair of readers at the bottom of this file: no message of this program
# printed by no box, and no line printed by every box. #364 measured the same
# gap here as in the installer - the boxes were already arranged and nothing
# was reading what they made the program say. The reader itself is in
# test/say-coverage.sh, so that it has one spelling rather than one per
# component.
. "$(cd "$(dirname "$0")" && pwd)/say-coverage.sh"
say_init "$BIN"

# <addr> <default-route-if> <iommu|noiommu> [drivers available...]
mkbox() {
  _a=$1; _d=$2; _i=$3; shift 3
  R=$(mktemp -d)
  mkdir -p "$R/etc/vpp" "$R/sys/bus/pci/devices/$_a" "$R/sys/bus/pci/drivers" \
           "$R/sys/class/net/eth9" "$R/proc/net" "$R/sys/kernel"
  printf 'dpdk {\n  dev %s {\n  }\n}\n' "$_a" > "$R/etc/vpp/startup.conf"
  # Three levels up from sys/class/net/<if>/ is sys/, the way a real sysfs
  # netdev points at its device.
  ln -s "../../../bus/pci/devices/$_a" "$R/sys/class/net/eth9/device"
  printf 'Iface\tDestination\n%s\t00000000\n' "$_d" > "$R/proc/net/route"
  [ "$_i" = iommu ] && mkdir -p "$R/sys/kernel/iommu_groups/0"
  for _x in "$@"; do mkdir -p "$R/sys/bus/pci/drivers/$_x"; done
  echo "$R"
}

on_driver() {  # <root> <driver> <addr>
  mkdir -p "$1/sys/bus/pci/drivers/$2"
  ln -sf "../../drivers/$2" "$1/sys/bus/pci/devices/$3/driver"
}

runbind() { OUT=$(HEXA_ROOT="$1" sh "$BIN" 2>&1); RC=$?
             printf '%s\n' "$OUT" | say_box; }

said() { printf '%s\n' "$OUT" | grep -q "$1"; }

echo "=== a device already bound for DPDK is left alone ==="
R=$(mkbox 0000:03:00.0 eth0 iommu vfio-pci); on_driver "$R" vfio-pci 0000:03:00.0
runbind "$R"
[ "$RC" = 0 ] && said "already bound to vfio-pci" \
  && ok "reported as already bound, and nothing is written" \
  || bad "a device already on vfio-pci was not left alone: rc=$RC $OUT"
# Nothing may have been written: an operator who did the binding by hand must
# not have their device torn down and put back by a boot.
[ -e "$R/sys/bus/pci/devices/0000:03:00.0/driver_override" ] \
  && bad "it wrote driver_override to a device that was already bound" \
  || ok "and driver_override was not touched"

echo
echo "=== the device carrying the default route is REFUSED ==="
# The one mistake here that cannot be undone remotely.
R=$(mkbox 0000:03:00.0 eth9 iommu vfio-pci); on_driver "$R" ixgbe 0000:03:00.0
runbind "$R"
[ "$RC" != 0 ] && said "REFUSING" \
  && ok "refused, and the refusal is the verdict" \
  || bad "the box was about to bind its own management NIC: rc=$RC $OUT"
said "eth9" \
  && ok "and it names the interface, which is what the operator recognises" \
  || bad "the refusal does not say which interface it is: $OUT"
[ -e "$R/sys/bus/pci/devices/0000:03:00.0/driver_override" ] \
  && bad "it refused and wrote driver_override anyway" \
  || ok "and nothing was written"

echo
echo "=== a box with ONE card may hand it over, with the way back recorded ==="
# The refusal above is right about every box that has somewhere else to put the
# dataplane. On a box with one card its advice named a card that does not
# exist, and the box could never forward (#303). It steps aside here, and only
# here, and only when tune.env carries the management path the VTY read off
# the card before naming it - because without that record there is nothing to
# put on the pair once the dataplane holds the port, and the box comes up
# forwarding and unreachable.
R=$(mkbox 0000:03:00.0 eth9 iommu vfio-pci); on_driver "$R" ixgbe 0000:03:00.0
mkdir -p "$R/etc/hexarouting"
printf 'HEXA_DATAPLANE_IF=eth9\nHEXA_DATAPLANE_MGMT=eth9 10.95.200.198/24 52:54:00:00:00:01\n' \
  > "$R/etc/hexarouting/tune.env"
runbind "$R"
# NOT on the exit status: this fake sysfs cannot actually move a driver, so
# the verification at the end reports the device on the one it started on and
# the script exits non-zero - which every other bind case here also does and
# reads for what it wrote instead. What is being measured is the refusal, and
# whether it fired.
said "the only card this box has" && ! said "REFUSING" \
  && ok "handed over, and it says that is what it did" \
  || bad "a one-card box with a recorded management path was still refused: $OUT"
said "10.95.200.198/24" \
  && ok "and names the management path the box will come back on" \
  || bad "it did not say what keeps the box reachable: $OUT"
[ -e "$R/sys/bus/pci/devices/0000:03:00.0/driver_override" ] \
  && ok "and the bind actually happened" \
  || bad "it said it was handing the card over and bound nothing: $OUT"

echo
echo "=== with a SECOND card on the box, it is refused even so ==="
# The record says the box knows how to come back on the pair. It does not say
# it should: with a spare card there is one to name instead.
R=$(mkbox 0000:03:00.0 eth9 iommu vfio-pci); on_driver "$R" ixgbe 0000:03:00.0
mkdir -p "$R/etc/hexarouting" "$R/sys/bus/pci/devices/0000:03:00.1" \
         "$R/sys/class/net/eth8"
ln -s "../../../bus/pci/devices/0000:03:00.1" "$R/sys/class/net/eth8/device"
printf 'HEXA_DATAPLANE_MGMT=eth9 10.95.200.198/24 52:54:00:00:00:01\n' \
  > "$R/etc/hexarouting/tune.env"
runbind "$R"
[ "$RC" != 0 ] && said "REFUSING" \
  && ok "refused, because there is another card to name" \
  || bad "the management card went to the dataplane with a spare card beside it: rc=$RC $OUT"
said "eth8" \
  && ok "and the refusal names the card to use instead" \
  || bad "it says no without saying what yes would be: $OUT"

echo
echo "=== a virtio netdev is recognised as its PCI device (#286, here) ==="
# The check resolved `device` by basename, so on a virtio guest - which is
# every VM this product is first tried on - the management card's address
# never matched and this last line of defence was not there at all.
R=$(mkbox 0000:03:00.0 eth9 iommu vfio-pci); on_driver "$R" ixgbe 0000:03:00.0
rm -f "$R/sys/class/net/eth9/device"
mkdir -p "$R/sys/bus/pci/devices/0000:03:00.0/virtio7"
ln -s "../../../bus/pci/devices/0000:03:00.0/virtio7" \
  "$R/sys/class/net/eth9/device"
runbind "$R"
[ "$RC" != 0 ] && said "REFUSING" \
  && ok "the management card is recognised through the virtio bus device" \
  || bad "a virtio management card was about to be unbound: rc=$RC $OUT"

echo
echo "=== no device named: this says nothing, because two others already do ==="
R=$(mkbox 0000:03:00.0 eth0 iommu vfio-pci)
printf 'dpdk {\n  no-pci\n}\n' > "$R/etc/vpp/startup.conf"
runbind "$R"
[ "$RC" = 0 ] && [ -z "$OUT" ] \
  && ok "silent and successful, so the boot is not noisier for a known state" \
  || bad "the no-pci case said something: rc=$RC $OUT"

echo
echo "=== FIRST BOOT: the tuner names no device, so the card stays ==="
# THE ANSWER TO #379, and it is the file rather than a second opinion.
#
# In first-boot mode the tuner disables the DPDK plugin, because the EAL fails
# without hugepages and vpp.service carries Restart=always. apply-tuning runs
# the binder on that same boot, and the only thing it asks first is whether
# the dataplane is already up - never whether the configuration just written
# will claim anything. On the face of it that is a card taken off the kernel
# for a dataplane that cannot take it, which on a one-card box is a machine
# nobody can reach.
#
# It does not happen, and the reason is the binder's own design: what it binds
# is what startup.conf NAMES, and the conservative profile carries no `dev`
# line at all - not even `no-pci`, which the case above covers. The guard is
# one hop away from where the bind is decided, so it is asserted here rather
# than left as a property somebody would have to re-derive.
#
# GENERATED BY THE REAL TUNER, not written out here. A conservative profile
# typed into this file would be a second copy of what the box gets, and it
# would go on passing the day the tuner started naming a device in that mode -
# which is the exact change this case exists to catch.
R=$(mkbox 0000:03:00.0 eth9 noiommu uio_pci_generic)
on_driver "$R" virtio-pci 0000:03:00.0
# The box as it is on the boot after the operator's commit: tune.env NAMES the
# card and carries the way back. Without those two lines this case would be a
# box that had never been told to hand anything over, and the assertion below
# would pass over a binder that takes whatever tune.env names.
mkdir -p "$R/etc/hexarouting"
printf 'HEXA_DATAPLANE_IF=eth9\nHEXA_DATAPLANE_MGMT=eth9 10.95.200.198/24 \
52:54:00:00:00:01\n' > "$R/etc/hexarouting/tune.env"
TROOT=$(mktemp -d)
for c in 0 1; do
  mkdir -p "$TROOT/sys/devices/system/cpu/cpu$c/topology"
  echo 0  > "$TROOT/sys/devices/system/cpu/cpu$c/topology/physical_package_id"
  echo $c > "$TROOT/sys/devices/system/cpu/cpu$c/topology/core_id"
  echo $c > "$TROOT/sys/devices/system/cpu/cpu$c/topology/thread_siblings_list"
done
mkdir -p "$TROOT/sys/devices/system/node/node0" \
         "$TROOT/sys/kernel/mm/hugepages/hugepages-1048576kB" \
         "$TROOT/sys/devices/pci0000:00/0000:03:00.0/virtio7" \
         "$TROOT/sys/class/net/eth9" "$TROOT/proc"
echo "0,1" > "$TROOT/sys/devices/system/node/node0/cpulist"
# Nothing reserved and nothing isolated: the boot after an install.
echo 0 > "$TROOT/sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages"
echo "" > "$TROOT/sys/devices/system/cpu/isolated"
echo 0 > "$TROOT/sys/devices/pci0000:00/0000:03:00.0/numa_node"
echo "MemTotal:       8388608 kB" > "$TROOT/proc/meminfo"
ln -s "$TROOT/sys/devices/pci0000:00/0000:03:00.0/virtio7" \
      "$TROOT/sys/class/net/eth9/device"
HEXA_IP=/bin/true python3 "$REPO/appliance/hexarouting-tune" \
  --sysfs-root "$TROOT" --interface eth9 --keep-management \
  --vpp-base /nonexistent --config /nonexistent 2>/dev/null \
  | sed -n '/### stanzas appended/,$p' | tail -n +2 \
  > "$R/etc/vpp/startup.conf"
grep -q "FIRST BOOT MODE" "$R/etc/vpp/startup.conf" \
  && ok "the tuner really produced the conservative profile" \
  || bad "this case is not measuring first-boot mode at all"
runbind "$R"
[ "$RC" = 0 ] \
  && ok "the binder exits 0 on a profile that claims nothing" \
  || bad "the conservative profile made the binder fail: rc=$RC $OUT"
[ -e "$R/sys/bus/pci/devices/0000:03:00.0/driver_override" ] \
  && bad "the card was handed to DPDK on a boot whose startup.conf turns the \
DPDK plugin off, so nothing can claim it and this box has no interface left" \
  || ok "and nothing was written: the card is not taken"
# Silent, for the reason the no-pci case below is silent: two other components
# already say this box has no dataplane interface yet, and a third voice on
# every first boot teaches an operator to read past the console.
[ -z "$OUT" ] \
  && ok "and it says nothing, because two other components already do" \
  || bad "the conservative profile made the binder talk: $OUT"
rm -rf "$TROOT"

echo
echo "=== a named device this box has not got ==="
R=$(mkbox 0000:03:00.0 eth0 iommu vfio-pci)
rm -rf "$R/sys/bus/pci/devices/0000:03:00.0"
runbind "$R"
[ "$RC" != 0 ] && said "no such PCI device" \
  && ok "reported, rather than a bind that quietly does nothing" \
  || bad "a device that is not there was not reported: rc=$RC $OUT"

echo
echo "=== the bind itself: what it writes, and where ==="
R=$(mkbox 0000:03:00.0 eth0 iommu vfio-pci); on_driver "$R" ixgbe 0000:03:00.0
D=$R/sys/bus/pci/devices/0000:03:00.0
runbind "$R"
[ "$(cat "$D/driver_override" 2>/dev/null)" = "vfio-pci" ] \
  && ok "driver_override names the driver it wants" \
  || bad "driver_override is '$(cat "$D/driver_override" 2>/dev/null)': $OUT"
# driver_override and NOT new_id. new_id matches by vendor and device, so it
# claims every card of that model in the box - including the management port
# of a two-port card, which is the ordinary case.
[ "$(cat "$R/sys/bus/pci/drivers/ixgbe/unbind" 2>/dev/null)" = "0000:03:00.0" ] \
  && ok "and it unbinds that one address from the driver holding it" \
  || bad "the unbind wrote '$(cat "$R/sys/bus/pci/drivers/ixgbe/unbind" 2>/dev/null)'"
[ "$(cat "$R/sys/bus/pci/drivers_probe" 2>/dev/null)" = "0000:03:00.0" ] \
  && ok "and asks the bus to probe that address again" \
  || bad "drivers_probe wrote '$(cat "$R/sys/bus/pci/drivers_probe" 2>/dev/null)'"
said "from ixgbe to vfio-pci" \
  && ok "and says what it changed, from what to what" \
  || bad "the bind did not say what it was doing: $OUT"

echo
echo "=== the verification after the write, both answers ==="
# The fake tree has no kernel, so the device does not actually move and the
# binder must say so rather than assume. That is the assertion: it CHECKS.
[ "$RC" != 0 ] && said "did NOT bind" \
  && ok "a device that did not move is reported as not moved" \
  || bad "it reported success over a device still on its old driver: $OUT"
said "no driver at all" && bad "it said 'no driver at all' about a device on ixgbe: $OUT"
# AND THE ADVICE UNDER IT ASKS WHICH BOX IT IS ON.
#
# It was one sentence for both drivers: "on a box with an IOMMU the usual
# cause is intel_iommu=on or amd_iommu=on missing from the kernel command
# line". False of each of the two boxes that can reach it, in opposite
# directions. This box has IOMMU groups - which is the only way the binder
# chose vfio-pci at all - so the kernel command line cannot be what is
# missing, and an operator whose appliance came up not forwarding was sent to
# check something already right, on the one message they get.
said "intel_iommu=on or amd_iommu=on missing" \
  && bad "the box with IOMMU groups was told its kernel command line is \
missing the parameter that put those groups there: $OUT"
said "the kernel command line is not the" \
  && ok "a box with IOMMU groups is told the command line is not the cause" \
  || bad "the advice does not say what this box's failure cannot be: $OUT"
said "iommu_group" \
  && ok "and is pointed at the file that decides whether the device may go" \
  || bad "it named no place to look on a box whose command line is right: $OUT"
said "it is on ixgbe now" \
  && ok "and names the driver it is on instead, which is the state to act on" \
  || bad "it did not say where the device ended up: $OUT"

echo
echo "=== and it names the card, on a virtio box too ==="
# The same comparison as the refusal above, thirty lines further down in the
# binder and used only for the message. It failed quietly rather than
# dangerously: on every VM this product is tried on, the one word in this line
# an operator recognises was missing. A fact read two ways with only one of
# them corrected is the shape that cost two release-gate refusals.
R=$(mkbox 0000:04:00.0 eth0 iommu vfio-pci); on_driver "$R" virtio-pci 0000:04:00.0
rm -f "$R/sys/class/net/eth9/device"
mkdir -p "$R/sys/bus/pci/devices/0000:04:00.0/virtio7"
ln -s "../../../bus/pci/devices/0000:04:00.0/virtio7" \
  "$R/sys/class/net/eth9/device"
runbind "$R"
said "0000:04:00.0 (eth9) from" \
  && ok "the bind line carries the interface name on a virtio device" \
  || bad "the bind line lost the name of the card it is taking: $OUT"

echo
echo "=== a box with no IOMMU falls back, and says what that costs ==="
R=$(mkbox 0000:03:00.0 eth0 noiommu uio_pci_generic)
on_driver "$R" ixgbe 0000:03:00.0
runbind "$R"
said "no IOMMU groups" \
  && ok "the fallback is announced rather than silent" \
  || bad "it fell back to uio_pci_generic without saying why: $OUT"
said "no IOMMU protection" \
  && ok "and what is given up is said in the same breath" \
  || bad "the cost of the fallback is not stated: $OUT"
# AND IT FITS THE CONSOLE IT IS PRINTED ON (#346). `say` joins its arguments
# with a space onto ONE line, so a continuation in the source is not a line
# break in the output: the fallback announcement was 121 columns on an
# 80-column console, which is the widest thing this appliance prints and it
# is on the path a one-card VM takes.
WIDE=$(printf '%s\n' "$OUT" | awk 'length > 79 {print length": "$0}')
[ -z "$WIDE" ] \
  && ok "every line of the fallback fits the 79-column console" \
  || bad "lines wider than the console an operator reads this on:
$WIDE"
[ "$(cat "$R/sys/bus/pci/devices/0000:03:00.0/driver_override" 2>/dev/null)" \
  = "uio_pci_generic" ] \
  && ok "and it is uio_pci_generic that is asked for" \
  || bad "it still asked for vfio-pci on a box with no IOMMU"
# THE OTHER HALF OF THE ADVICE, and the fake tree cannot move a device, so
# this run also reaches the failure message. A box with no IOMMU groups used
# to be handed a sentence opening "on a box with an IOMMU" - about a
# different machine - naming a kernel parameter that would not have helped,
# because uio_pci_generic does not use an IOMMU at all.
said "on a box with an IOMMU" \
  && bad "a box with no IOMMU groups was given advice about a box that has \
one: $OUT"
said "needs a legacy INTx interrupt" \
  && ok "it is told what uio_pci_generic actually refuses a device for" \
  || bad "the fallback's failure names no cause this box could have: $OUT"
said "makes vfio-pci" \
  && ok "and what would make the protected driver available instead" \
  || bad "nothing said how this box could get an IOMMU: $OUT"

echo
echo "=== no driver DPDK can use, anywhere on the box ==="
R=$(mkbox 0000:03:00.0 eth0 iommu); on_driver "$R" ixgbe 0000:03:00.0
runbind "$R"
[ "$RC" != 0 ] && said "is not available on this box" \
  && ok "reported, instead of a bind that cannot happen" \
  || bad "a box with no DPDK driver was not told: rc=$RC $OUT"
[ -e "$R/sys/bus/pci/devices/0000:03:00.0/driver_override" ] \
  && bad "it wrote driver_override with no driver to bind to" \
  || ok "and it did not tear the device off its working driver first"

echo
echo "=== a write into sysfs that is refused is reported, not stepped over ==="
# Both of these run as root on a real box, so on a real box they fail only
# when something is wrong: a device that has gone away between the readlink
# and the write, a driver that is unbinding it at the same moment, a sysfs
# mounted read-only in a container the tuner is being run inside. What they
# must NOT do is carry on, because the two writes are a pair and the state
# between them is a device the kernel still holds with an override on it
# telling the bus to give it away at the next probe.
#
# Arranged by taking the write permission off the directory, which is the one
# way a suite that is not root can make a write into sysfs fail. It is the
# same errno the kernel returns.
#
# Skipped for root, which writes into a directory whatever its mode says - the
# case would then measure the runner and pass on a binder that ignores both.
if [ "$(id -u)" = 0 ]; then
  echo "  SKIP: running as root, which writes into a 0500 directory anyway"
else
  R=$(mkbox 0000:03:00.0 eth0 iommu vfio-pci); on_driver "$R" ixgbe 0000:03:00.0
  chmod 500 "$R/sys/bus/pci/devices/0000:03:00.0"
  runbind "$R"
  chmod 700 "$R/sys/bus/pci/devices/0000:03:00.0"
  [ "$RC" != 0 ] && said "cannot write driver_override" \
    && ok "a driver_override that cannot be written stops the bind, by name" \
    || bad "the binder could not write driver_override and did not say so: \
rc=$RC $OUT"
  # And it must not have gone on to the unbind. The override is what tells the
  # bus which driver to hand the device to; without it, unbinding the card
  # from ixgbe leaves a NIC that belongs to neither stack - which is the state
  # the last message in this script calls the worst one to be quiet about.
  [ -e "$R/sys/bus/pci/drivers/ixgbe/unbind" ] \
    && bad "it failed to claim the device and unbound it from ixgbe anyway, \
which leaves the card belonging to neither the kernel nor the dataplane" \
    || ok "and the card was left on the driver that was holding it"

  R=$(mkbox 0000:03:00.0 eth0 iommu vfio-pci); on_driver "$R" ixgbe 0000:03:00.0
  chmod 500 "$R/sys/bus/pci/drivers/ixgbe"
  runbind "$R"
  chmod 700 "$R/sys/bus/pci/drivers/ixgbe"
  [ "$RC" != 0 ] && said "cannot unbind 0000:03:00.0 from ixgbe" \
    && ok "an unbind that is refused stops the bind too, naming both" \
    || bad "the card stayed on ixgbe with an override on it and the binder \
reported nothing: rc=$RC $OUT"
  # This is the half that would otherwise pass unnoticed. The device ends up
  # on its old driver either way, so the verification at the end of the script
  # says "did NOT bind" and exits non-zero whether this refusal fired or not -
  # the exit status cannot tell the two apart, and the message is the only
  # thing that can.
  said "did NOT bind" \
    && bad "it fell through to the verification, so the console says the \
device refused the driver when what happened is that the unbind was refused: \
$OUT" \
    || ok "and it says the unbind was refused rather than blaming the driver"
fi

echo
echo "=== --which-driver promises what the bind actually writes ==="
# Compared against THE OTHER PLACE THE ANSWER IS WRITTEN, not against a needle
# this test carries. `show hardware` prints whatever this mode says, so if the
# mode and the bind ever disagree the screen promises an operator a driver
# their box will not use, and nothing anywhere would notice. The rule now
# lives in dpdk_driver(); this is the check that keeps it living there (#328).
for _shape in iommu noiommu; do
  R=$(mkbox 0000:03:00.0 eth0 "$_shape" vfio-pci uio_pci_generic)
  on_driver "$R" ixgbe 0000:03:00.0
  _promised=$(HEXA_ROOT="$R" sh "$BIN" --which-driver 2>&1 | awk '{print $1}')
  runbind "$R"
  _wrote=$(cat "$R/sys/bus/pci/devices/0000:03:00.0/driver_override" \
           2>/dev/null)
  [ -n "$_promised" ] && [ "$_promised" = "$_wrote" ] \
    && ok "on a $_shape box both say $_wrote" \
    || bad "on a $_shape box the screen would promise '$_promised' and the \
bind asked for '$_wrote'"
done
# AND THE SENTENCE IT PRINTS, on both boxes, which is what an operator reads
# off `show hardware`. The loop above takes the first word of it and compares
# that; the rest of the line is the half that says WHY - and on a box with no
# IOMMU groups it is the difference between a card the kernel protects and one
# it does not. Neither sentence had been produced by anything (#364).
for _shape in iommu noiommu; do
  R=$(mkbox 0000:03:00.0 eth0 "$_shape" vfio-pci uio_pci_generic)
  _w=$(HEXA_ROOT="$R" sh "$BIN" --which-driver 2>&1)
  printf '%s\n' "$_w" | say_box
  case "$_shape:$_w" in
    "iommu:vfio-pci through the IOMMU"*)
      ok "a box with IOMMU groups is told the card goes through the IOMMU" ;;
    "noiommu:uio_pci_generic without IOMMU protection"*)
      ok "and a box without them is told the protection is not there" ;;
    *) bad "--which-driver on a $_shape box said: $_w" ;;
  esac
done

# THE BIND THAT WORKS, which is the one answer of the two that had never been
# produced. The tree here has no kernel in it, so a device does not move
# because something was written to sysfs - and every case above reads the
# refusal that follows from that.
#
# ARRANGED WITH A FIFO WHERE drivers_probe IS. Writing to one blocks until
# something opens it for reading, so a reader that makes the driver symlink
# BEFORE it opens the fifo is a kernel that has bound the device by the time
# the write returns. That is the ordering the real bus gives and nothing else
# here can: a background process racing the binder would pass or fail on
# timing, which is the one thing a test may not do.
R=$(mkbox 0000:03:00.0 eth0 iommu vfio-pci); on_driver "$R" ixgbe 0000:03:00.0
rm -f "$R/sys/bus/pci/drivers_probe"
mkfifo "$R/sys/bus/pci/drivers_probe"
( # NOT AT ONCE: the binder reads which driver holds the device BEFORE it
  # writes driver_override, and a symlink moved ahead of that read is a
  # device that was already bound - which is a different branch and the one
  # this box is not about. driver_override appearing is the signal that the
  # read has happened.
  _n=0
  while [ ! -s "$R/sys/bus/pci/devices/0000:03:00.0/driver_override" ] \
        && [ "$_n" -lt 200 ]; do sleep 0.05; _n=$((_n + 1)); done
  ln -sfn "../../drivers/vfio-pci" \
       "$R/sys/bus/pci/devices/0000:03:00.0/driver"
  cat < "$R/sys/bus/pci/drivers_probe" >/dev/null 2>&1 ) &
_probe=$!
# A bound on the wait, because a fifo nobody reads is a suite that hangs
# rather than one that fails.
OUT=$(HEXA_ROOT="$R" timeout 20 sh "$BIN" 2>&1); RC=$?
printf '%s\n' "$OUT" | say_box
wait "$_probe" 2>/dev/null || true
[ "$RC" = 0 ] && said "is now on vfio-pci" \
  && ok "a device that DID move is reported as bound, and the run succeeds" \
  || bad "a device the bus bound was not reported as bound: rc=$RC
$OUT"
said "did NOT bind" && bad "it said both that the device moved and that it \
did not: $OUT"

# And the mode binds nothing: it is called from a VTY that an operator may be
# typing into while the box is forwarding.
R=$(mkbox 0000:03:00.0 eth0 iommu vfio-pci); on_driver "$R" ixgbe 0000:03:00.0
HEXA_ROOT="$R" sh "$BIN" --which-driver >/dev/null 2>&1
[ -e "$R/sys/bus/pci/devices/0000:03:00.0/driver_override" ] \
  && bad "--which-driver wrote driver_override: it is not supposed to touch \
the box at all" \
  || ok "and it touched nothing while answering"

# WHAT THE WIDTH READER MAY STILL FIND, and it may only go down.
#
# 6 rows here carry an interface name or a driver name the kernel handed back,
# with the words of the program behind them. Named on every run rather than
# excused: an allowlist is read once and grown forever.
SAY_UNSAFE_AT_MOST=6
say_report appliance/bind-dataplane-nic
rm -rf "$SAY_DIR"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
