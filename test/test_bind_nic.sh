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
# WHAT IT NO LONGER DECIDES is the case worth the most care. The binder used
# to refuse the card carrying the default route (#123) and let the last card
# through only beside a way back (#303, #378); both went with #385. The tuner
# names no card whose way back it could not read, so the one fact the refusal
# needed is decided before startup.conf is written - and a binder with an
# opinion of its own about which card may go is the second copy of that
# decision this file's header argues against. What is asserted below is that
# it binds what it is named, whichever route the card carries, and reads
# nothing else to decide it.
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

runbind() { OUT=$(PRISMA_ROOT="$1" sh "$BIN" 2>&1); RC=$?
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
echo "=== EVERY device startup.conf names is bound, not the first (#378) ==="
# Since the box claims every card it has but a reserve, startup.conf carries
# one `dev` stanza per claimed card. This read the first of them with `head
# -1`, so a three-card box came up with one card on vfio-pci and two still
# held by their kernel drivers - which DPDK cannot take a device through. The
# dataplane would then hold one port in three and the self-test would call it
# healthy, because a device named in a file is not a device the dataplane has
# (#283).
#
# THE FIXTURE HAS THREE, because a case with two would pass against `head -1`
# the moment the loop was written wrong in the other direction - taking the
# last instead of the first is also one device, and one device out of two is
# half the boxes.
R=$(mkbox 0000:03:00.0 eth0 iommu vfio-pci)
printf 'dpdk {\n  dev 0000:03:00.0 {\n  }\n  dev 0000:03:00.1 {\n  }\n\
  dev 0000:03:00.2 {\n  }\n}\n' > "$R/etc/vpp/startup.conf"
for _a in 0000:03:00.1 0000:03:00.2; do
  mkdir -p "$R/sys/bus/pci/devices/$_a"
  on_driver "$R" ixgbe "$_a"
done
on_driver "$R" ixgbe 0000:03:00.0
runbind "$R"
_bound=0
for _a in 0000:03:00.0 0000:03:00.1 0000:03:00.2; do
  # driver_override is what the binder WRITES, and it is the only evidence
  # here that the device was acted on: this fake sysfs has no kernel to
  # complete the probe, so the driver link does not change by itself.
  [ -e "$R/sys/bus/pci/devices/$_a/driver_override" ] && _bound=$((_bound + 1))
done
[ "$_bound" = 3 ] \
  && ok "all three named devices were offered to a DPDK driver" \
  || bad "$_bound of 3 named devices were bound: a box that claims three \
cards came up forwarding on $_bound of them, and nothing said so"
# AND EACH BY NAME. A count alone would pass a binder that wrote the first
# device's override three times, which is the shape `head -1` fails in.
for _a in 0000:03:00.1 0000:03:00.2; do
  said "$_a" && ok "and it says what it did to $_a" \
    || bad "nothing on the console names $_a, so an operator reading a box \
that forwards on one port has no line to go to: $OUT"
done

echo
echo "=== the card carrying the default route is bound like any other (#385) ==="
# IT USED TO BE REFUSED HERE, as the last line of defence for a startup.conf
# somebody edited by hand, and the refusal went with #385: a box is configured
# at its console when it is new, so a guard on the card it is reached over
# guards a step nobody takes over the network. The tuner decides which cards
# go and never names one whose way back it could not read (#378), so a card
# named in startup.conf is a card that may go, and this program takes it.
#
# The fixture is the box the refusal was written for: one card, and it
# carries the default route. Nothing in /run, because the way back is no
# longer this program's question - the tuner answered it before it wrote the
# file this reads.
R=$(mkbox 0000:03:00.0 eth9 iommu vfio-pci); on_driver "$R" ixgbe 0000:03:00.0
runbind "$R"
said "REFUSING" \
  && bad "the card carrying the default route was refused, which is the \
refusal #385 removed: $OUT" \
  || ok "not refused: the card the tuner named is the card that goes"
[ -e "$R/sys/bus/pci/devices/0000:03:00.0/driver_override" ] \
  && ok "and the bind actually happened" \
  || bad "nothing was written, so the box came up holding no card: $OUT"
said "eth9" \
  && ok "and it names the interface, which is what the operator recognises" \
  || bad "the bind line does not say which interface it is: $OUT"
# AND NO ADVICE. The refusal told a one-card box to type the card the tuner
# had just named, which was a loop, and a two-card box to name the other card,
# which was the decision the maintainer took away. A program that decides
# nothing about which card has nothing to advise.
said "dataplane interface" \
  && bad "it still tells the operator what to type, over a decision it no \
longer makes: $OUT" \
  || ok "and it offers no advice on which card, because that is not its call"

echo
echo "=== with a SECOND card on the box, the named one still goes and the other stays ==="
# The refusal was strongest here: with a spare card there was one to name
# instead (#123). It is gone here too and not narrowed to the one-card box,
# because narrowing it would be this program keeping a rule the tuner no
# longer has. What still holds is the older rule: it binds what startup.conf
# names and nothing else, so the spare card is untouched.
R=$(mkbox 0000:03:00.0 eth9 iommu vfio-pci); on_driver "$R" ixgbe 0000:03:00.0
mkdir -p "$R/sys/bus/pci/devices/0000:03:00.1" "$R/sys/class/net/eth8"
ln -s "../../../bus/pci/devices/0000:03:00.1" "$R/sys/class/net/eth8/device"
on_driver "$R" ixgbe 0000:03:00.1
runbind "$R"
said "REFUSING" \
  && bad "the management card was refused because a spare card sits beside \
it, which is the half of the refusal #385 removed too: $OUT" \
  || ok "not refused with a spare card either"
[ -e "$R/sys/bus/pci/devices/0000:03:00.0/driver_override" ] \
  && ok "the named card was bound" \
  || bad "the named card was not bound: $OUT"
[ -e "$R/sys/bus/pci/devices/0000:03:00.1/driver_override" ] \
  && bad "the spare card was bound too, and startup.conf never named it" \
  || ok "and the spare card, which startup.conf does not name, was left alone"

echo
echo "=== and the way back in /run is not this program's to read ==="
# The tuner leaves the way back it computed in /run for the VTY to put on the
# pair (#378). The binder used to read it as permission to take the last card;
# now it reads only startup.conf, so a record present or absent changes
# nothing here. Driven with the record PRESENT, because the case above already
# holds the absent half and a binder that still read the file would show it
# on this box by printing the record.
R=$(mkbox 0000:03:00.0 eth9 iommu vfio-pci); on_driver "$R" ixgbe 0000:03:00.0
mkdir -p "$R/run/prisma-routing"
printf 'eth9 10.95.200.198/24 52:54:00:00:00:01 10.95.200.1\n' \
  > "$R/run/prisma-routing/dataplane-management"
runbind "$R"
said "10.95.200.198" \
  && bad "the binder still reads the way back and prints it, which is a \
second voice for a record the tuner already put in the journal: $OUT" \
  || ok "the record is not read here: the tuner wrote it and the VTY reads it"
[ -e "$R/sys/bus/pci/devices/0000:03:00.0/driver_override" ] \
  && ok "and the card was bound, the same as without it" \
  || bad "with the record present the card was not bound: $OUT"

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
# card. Without that line this case would be a box that had never been told
# to hand anything over, and the assertion below would pass over a binder that
# takes whatever tune.env names.
mkdir -p "$R/etc/prisma-routing"
printf 'PRISMA_DATAPLANE_IF=eth9\n' > "$R/etc/prisma-routing/tune.env"
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
PRISMA_IP=/bin/true python3 "$REPO/appliance/prisma-routing-tune" \
  --sysfs-root "$TROOT" --interface eth9 \
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
# pci_addr_of() resolved `device` by basename once, so on a virtio guest -
# which is every VM this product is tried on - the one word in this line an
# operator recognises was missing (#286). It is the only reader of that
# function left now that the refusal has gone, so this is the case that holds
# it.
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
  _promised=$(PRISMA_ROOT="$R" sh "$BIN" --which-driver 2>&1 | awk '{print $1}')
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
  _w=$(PRISMA_ROOT="$R" sh "$BIN" --which-driver 2>&1)
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
OUT=$(PRISMA_ROOT="$R" timeout 20 sh "$BIN" 2>&1); RC=$?
printf '%s\n' "$OUT" | say_box
wait "$_probe" 2>/dev/null || true
[ "$RC" = 0 ] && said "is now on vfio-pci" \
  && ok "a device that DID move is reported as bound, and the run succeeds" \
  || bad "a device the bus bound was not reported as bound: rc=$RC
$OUT"
said "did NOT bind" && bad "it said both that the device moved and that it \
did not: $OUT"
rm -rf "$R"

# TWO CARDS THAT BOTH MOVE (#451). The box above claims one card, and one was
# all the success path had ever been driven with - so `exit 0` at the end of
# bind_one() looked right: it ended the one bind there was. On a box claiming
# two it ended the SCRIPT after the first, every boot, and the maintainer's
# box, given a second card, came up with the dataplane down. Same fifo
# arrangement, one reader serving both writes in the order they come.
R=$(mkbox 0000:03:00.0 eth0 iommu vfio-pci); on_driver "$R" ixgbe 0000:03:00.0
mkdir -p "$R/sys/bus/pci/devices/0000:03:00.1"
on_driver "$R" ixgbe 0000:03:00.1
printf 'dpdk {\n  dev 0000:03:00.0 {\n  }\n  dev 0000:03:00.1 {\n  }\n}\n' \
  > "$R/etc/vpp/startup.conf"
rm -f "$R/sys/bus/pci/drivers_probe"
mkfifo "$R/sys/bus/pci/drivers_probe"
( for _a in 0000:03:00.0 0000:03:00.1; do
    _n=0
    while [ ! -s "$R/sys/bus/pci/devices/$_a/driver_override" ] \
          && [ "$_n" -lt 200 ]; do sleep 0.05; _n=$((_n + 1)); done
    [ -s "$R/sys/bus/pci/devices/$_a/driver_override" ] || exit 0
    ln -sfn "../../drivers/vfio-pci" "$R/sys/bus/pci/devices/$_a/driver"
    cat < "$R/sys/bus/pci/drivers_probe" >/dev/null 2>&1
  done ) &
_probe=$!
OUT=$(PRISMA_ROOT="$R" timeout 20 sh "$BIN" 2>&1); RC=$?
printf '%s\n' "$OUT" | say_box
wait "$_probe" 2>/dev/null || true
[ "$RC" = 0 ] && said "0000:03:00.0 is now on vfio-pci" \
  && said "0000:03:00.1 is now on vfio-pci" \
  && ok "two claimed cards that both move are both bound, not the first" \
  || bad "a box claiming two cards did not bind both: rc=$RC
$OUT"
[ "$(cat "$R/sys/bus/pci/devices/0000:03:00.1/driver_override" 2>/dev/null)" \
    = "vfio-pci" ] \
  && ok "and the second card was asked for by name" \
  || bad "the second card never had driver_override written: $OUT"

# And the mode binds nothing: it is called from a VTY that an operator may be
# typing into while the box is forwarding.
R=$(mkbox 0000:03:00.0 eth0 iommu vfio-pci); on_driver "$R" ixgbe 0000:03:00.0
PRISMA_ROOT="$R" sh "$BIN" --which-driver >/dev/null 2>&1
[ -e "$R/sys/bus/pci/devices/0000:03:00.0/driver_override" ] \
  && bad "--which-driver wrote driver_override: it is not supposed to touch \
the box at all" \
  || ok "and it touched nothing while answering"

# ---------------------------------------------------------------------------
# appliance/give-the-card-back, the other half of the same decision.
#
# The bind above hands the card to a DPDK driver before VPP starts. If VPP
# never starts, the card belongs to neither the kernel nor the dataplane and
# the box is reachable only from a console. An operator reported that on
# 0.75.0 (#379). This script is what systemd runs when vpp.service reaches
# `failed`, and what is asserted here is what is asserted above: the DECISION
# and the WRITES, because a fake tree has no kernel to move a device with.
# ---------------------------------------------------------------------------
GIVE=$REPO/appliance/give-the-card-back
DEVDIR=sys/bus/pci/devices/0000:03:00.0

echo
echo "=== a card on a DPDK driver is taken off it ==="
R=$(mkbox 0000:03:00.0 eth9 iommu uio_pci_generic virtio-pci)
on_driver "$R" uio_pci_generic 0000:03:00.0
OUT=$(PRISMA_ROOT="$R" sh "$GIVE" 2>&1)
# THE OVERRIDE FIRST, AND EMPTY. Left set, the unbind hands the device straight
# back to the same DPDK driver and the command looks like it did nothing.
if [ -e "$R/$DEVDIR/driver_override" ]; then
  if [ -n "$(tr -d '[:space:]' < "$R/$DEVDIR/driver_override")" ]; then
    bad "it wrote a driver name into driver_override instead of clearing it"
  else
    ok "it cleared driver_override, so the kernel's driver can win the probe"
  fi
else
  bad "it never touched driver_override, so the DPDK driver still wins the \
probe and the unbind gives the card straight back to it"
fi
[ -s "$R/sys/bus/pci/drivers_probe" ] \
  && ok "and asked the bus to probe for a driver again" \
  || bad "it never asked the bus to probe, so nothing can claim the device"
rm -rf "$R"

echo
echo "=== a card already on a kernel driver is left alone, and says so ==="
# This unit fires on a FAILURE, so "the card was not the problem" is a verdict
# an operator reading the journal needs, not a silence.
R=$(mkbox 0000:03:00.0 eth9 iommu virtio-pci); on_driver "$R" virtio-pci 0000:03:00.0
OUT=$(PRISMA_ROOT="$R" sh "$GIVE" 2>&1); RC=$?
[ "$RC" = 0 ] \
  && ok "exit 0: there is nothing to undo" \
  || bad "it exited $RC on a card the kernel already holds"
printf '%s\n' "$OUT" | grep -q "already where the kernel can use it" \
  && ok "and it says so rather than saying nothing" \
  || { bad "it left the card alone and did not say so:"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }
[ -e "$R/$DEVDIR/driver_override" ] \
  && bad "it touched driver_override on a card it was not going to move" \
  || ok "and driver_override was not touched"
rm -rf "$R"

echo
echo "=== a startup.conf that names no device has nothing to give back ==="
R=$(mkbox 0000:03:00.0 eth9 iommu virtio-pci)
printf 'dpdk {\n  no-pci\n}\n' > "$R/etc/vpp/startup.conf"
OUT=$(PRISMA_ROOT="$R" sh "$GIVE" 2>&1); RC=$?
[ "$RC" = 0 ] \
  && ok "exit 0 on a box that never took a card" \
  || bad "it exited $RC where no card was ever taken"
printf '%s\n' "$OUT" | grep -q "nothing to give back" \
  && ok "and says why, which is the FIRST BOOT profile" \
  || bad "it said nothing about why there was nothing to do"
rm -rf "$R"

echo
echo "=== a device that ends with NO driver is the loudest failure here ==="
# The outcome this file exists to end, and a fake tree cannot let the kernel
# reclaim a device - which is exactly the state being asserted: the override
# cleared, the bus asked, and nothing holding the card.
R=$(mkbox 0000:03:00.0 eth9 iommu uio_pci_generic virtio-pci)
OUT=$(PRISMA_ROOT="$R" sh "$GIVE" 2>&1); RC=$?
[ "$RC" = 1 ] \
  && ok "exit 1, because a box with no network interface is a failure" \
  || bad "a device left with no driver exited $RC"
printf '%s\n' "$OUT" | grep -q "no network interface" \
  && ok "and says the box has none, in those words" \
  || { bad "it ended with the card on nothing and did not say so:"
       printf '%s\n' "$OUT" | sed 's/^/    /'; }
printf '%s\n' "$OUT" | grep -q "reachable only from" \
  && ok "and says how the box can still be reached" \
  || bad "it did not say how the box can still be reached"
rm -rf "$R"

echo
echo "=== the drop-in arms it on the failure, not on every restart ==="
# vpp.service carries Restart=always, so a dataplane that crashes once is
# restarted and that is not a failure. OnFailure is raised only when the unit
# reaches `failed`, after the restart limit - the state the reporting box was
# in: "Failed with result 'start-limit-hit'".
D=$REPO/appliance/systemd/vpp-give-card-back.conf
grep -q "^OnFailure=prisma-routing-give-card-back.service\$" "$D" \
  && ok "the drop-in names the unit that gives the card back" \
  || bad "the drop-in does not arm prisma-routing-give-card-back.service"
grep -q "^\[Unit\]" "$D" \
  && ok "and puts it under [Unit], where systemd reads OnFailure" \
  || bad "OnFailure is not under [Unit], so systemd ignores it"
U=$REPO/appliance/systemd/prisma-routing-give-card-back.service
grep -q "^ExecStart=/usr/lib/prisma-routing/give-the-card-back\$" "$U" \
  && ok "and the unit runs the script these cases have been driving" \
  || bad "the unit does not run appliance/give-the-card-back"
grep -q "give-the-card-back" "$REPO/appliance/install.sh" \
  && ok "and install.sh puts that script on the box" \
  || bad "the script is never installed, so the unit names a path no \
appliance has"
# AND THE BINARY IT CALLS AFTERWARDS IS ON THE IMAGE. The kernel reclaiming
# the device creates the netdev and puts no address on it; `ifup -a` is what
# reads /etc/network/interfaces.d/prisma-routing, which the installer wrote. A
# unit naming a binary the image does not carry would give the card back and
# leave the box without an address, which is most of the way to the state this
# is undoing - and it would do it silently, because the call is prefixed `-`.
grep -q "^ExecStartPost=-/sbin/ifup -a\$" "$U" \
  && ok "and it brings the interface back up afterwards" \
  || bad "nothing puts the address back after the card returns"
grep -q "ifupdown" "$REPO/appliance/build-iso.sh" \
  && ok "and the image carries ifupdown, so /sbin/ifup is there to run" \
  || bad "the unit calls /sbin/ifup and the image does not install ifupdown"

echo
echo "=== the device nodes a container has to be handed (#396) ==="
# A dataplane in a container reaches its NIC through a device node, and WHICH
# node follows from the driver this box can use - the rule this file's subject
# is the one holder of. The mode is driven here against both boxes, because a
# compose file promising /dev/vfio on a box that binds uio_pci_generic is #328
# wearing a container.

# The IOMMU box: vfio-pci, so the container device plus the card's group.
R=$(mkbox 0000:3b:00.0 eth9 iommu vfio-pci uio_pci_generic)
mkdir -p "$R/sys/kernel/iommu_groups/7"
ln -s "../../../kernel/iommu_groups/7" "$R/sys/bus/pci/devices/0000:3b:00.0/iommu_group"
on_driver "$R" vfio-pci 0000:3b:00.0
OUT=$(PRISMA_ROOT=$R sh "$BIN" --compose-devices 2>&1)
printf '%s\n' "$OUT" | say_box
case "$OUT" in
  *"/dev/vfio/vfio"*"/dev/vfio/7"*)
    ok "a box with an IOMMU is handed the container device and its group" ;;
  *) bad "the vfio box was handed '$OUT'" ;;
esac
printf '%s
' "$OUT" | grep -q "devices:" \
  && ok "and it is a compose fragment, not a list to be reassembled" \
  || bad "the fragment has no devices: key: $OUT"
# AND IT BOUND NOTHING. This mode runs on a box that is already forwarding.
[ -z "$(cat "$R/sys/bus/pci/drivers/vfio-pci/bind" 2>/dev/null)" ] \
  && ok "and it bound nothing on the way" \
  || bad "the mode wrote to a driver's bind file"
rm -rf "$R"

# The same box with the card in no group: vfio-pci cannot be what it is on.
R=$(mkbox 0000:3b:00.0 eth9 iommu vfio-pci)
OUT=$(PRISMA_ROOT=$R sh "$BIN" --compose-devices 2>&1); RC=$?
printf '%s\n' "$OUT" | say_box
# LEFT OUT AND SAID, NOT FATAL (#451): this was an exit 1, and the unit that
# merges the fragment failed with it - the whole dataplane down for one card.
[ "$RC" = 0 ] && printf '%s\n' "$OUT" | grep -q "no IOMMU group" \
  && printf '%s\n' "$OUT" | grep -q "devices: \[\]" \
  && ok "a card in no group is left out and said, and the stack still starts" \
  || bad "a card with no group produced (rc=$RC): $OUT"
rm -rf "$R"

# Two cards, one bound and one still on its kernel driver (#451): the bound
# one is handed over and the other is left out and said. Naming the second
# group anyway was the vfio half of the defect - docker refused the whole
# stack over a /dev/vfio node that did not exist.
R=$(mkbox 0000:3b:00.0 eth9 iommu vfio-pci)
mkdir -p "$R/sys/kernel/iommu_groups/7" "$R/sys/kernel/iommu_groups/8" \
         "$R/sys/bus/pci/devices/0000:3b:00.1"
ln -s "../../../kernel/iommu_groups/7" "$R/sys/bus/pci/devices/0000:3b:00.0/iommu_group"
ln -s "../../../kernel/iommu_groups/8" "$R/sys/bus/pci/devices/0000:3b:00.1/iommu_group"
on_driver "$R" vfio-pci 0000:3b:00.0
on_driver "$R" virtio-pci 0000:3b:00.1
printf 'dpdk {\n  dev 0000:3b:00.0 {\n  }\n  dev 0000:3b:00.1 {\n  }\n}\n' \
  > "$R/etc/vpp/startup.conf"
OUT=$(PRISMA_ROOT=$R sh "$BIN" --compose-devices 2>&1); RC=$?
printf '%s\n' "$OUT" | say_box
[ "$RC" = 0 ] && printf '%s\n' "$OUT" | grep -q -- "- /dev/vfio/7" \
  && ! printf '%s\n' "$OUT" | grep -q "/dev/vfio/8" \
  && printf '%s\n' "$OUT" | grep -q "0000:3b:00.1 is not on vfio-pci" \
  && ok "one card bound and one not: the bound one is handed over, the other said" \
  || bad "a half-bound box produced (rc=$RC): $OUT"
rm -rf "$R"

# The box with no IOMMU: uio_pci_generic, and the node the bind produced.
R=$(mkbox 0000:3b:00.0 eth9 noiommu uio_pci_generic)
mkdir -p "$R/sys/bus/pci/devices/0000:3b:00.0/uio/uio0"
OUT=$(PRISMA_ROOT=$R sh "$BIN" --compose-devices 2>&1)
printf '%s\n' "$OUT" | say_box
case "$OUT" in
  *"/dev/uio0"*) ok "a box with no IOMMU is handed its uio node" ;;
  *) bad "the uio box was handed '$OUT'" ;;
esac
printf '%s
' "$OUT" | grep -q "/dev/vfio" \
  && bad "a box with no IOMMU was promised a /dev/vfio node: $OUT" \
  || ok "and no /dev/vfio node it has not got"
rm -rf "$R"

# The same box before the bind: the node does not exist yet, and this mode
# reads what the bind produced rather than predicting it.
R=$(mkbox 0000:3b:00.0 eth9 noiommu uio_pci_generic)
OUT=$(PRISMA_ROOT=$R sh "$BIN" --compose-devices 2>&1); RC=$?
printf '%s\n' "$OUT" | say_box
[ "$RC" = 0 ] && printf '%s\n' "$OUT" | grep -q "not bound to" \
  && ! printf '%s\n' "$OUT" | grep -q "/dev/uio" \
  && ok "and before the bind it says so rather than naming a node" \
  || bad "an unbound card produced (rc=$RC): $OUT"
rm -rf "$R"

# A box the tuner named no card on: an EMPTY LIST, and not an empty file.
#
# This assertion used to read the opposite - rc=0 and no output at all - and
# that was right while nothing consumed the fragment. It is wrong now that
# prisma-routing-containers.service writes it to a file and hands it to
# `docker-compose -f`: compose refuses an empty file with "Top level object
# needs to be a mapping", so the quiet answer would leave a no-pci box with
# a container stack that will not start, for the one condition the tuner and
# the self-test both treat as ordinary.
#
# `devices: []` and not a stanza left out: measured on docker-compose 1.29.2,
# the version the medium carries, an empty list APPENDS NOTHING and leaves
# the base file's /dev/net/tun and /dev/vhost-net as they are.
R=$(mkbox 0000:3b:00.0 eth9 iommu vfio-pci)
printf 'dpdk {\n}\n' > "$R/etc/vpp/startup.conf"
OUT=$(PRISMA_ROOT=$R sh "$BIN" --compose-devices 2>&1); RC=$?
printf '%s\n' "$OUT" | say_box
[ "$RC" = 0 ] && printf '%s\n' "$OUT" | grep -q "devices: \[\]" \
  && ok "a box claiming no card is handed an empty list, which is a document" \
  || bad "a box with no card produced (rc=$RC): $OUT"
printf '%s\n' "$OUT" | grep -q "^services:" \
  && ok "and the fragment compose is given is a mapping at its top level" \
  || bad "the no-card fragment has no services: key: $OUT"
printf '%s\n' "$OUT" | grep -q "/dev/" \
  && bad "a box with no card was handed a device node: $OUT" \
  || ok "and it names no device node, because this box has no card to name"
rm -rf "$R"

# AND THE UNIT ASKS FOR IT THE WAY THIS MODE ANSWERS. Two files that have to
# agree and are edited apart: a unit that wrote the fragment and did not pass
# it to `up` would start a dataplane with no card and look healthy doing it.
U=$(dirname "$BIN")/systemd/prisma-routing-containers.service
grep -q -- "--compose-devices > /run/prisma-routing/compose-devices.yaml" "$U" \
  && ok "the unit generates the fragment where it says it does" \
  || bad "no ExecStartPre in $U generates the compose fragment"
for verb in "up -d" "down -t 30"; do
  grep -q -- "-f /run/prisma-routing/compose-devices.yaml $verb" "$U" \
    && ok "and '$verb' is given the fragment as well as the base file" \
    || bad "'$verb' in $U does not carry -f the generated fragment"
done


# WHAT THE WIDTH READER MAY STILL FIND, and it may only go down.
#
# 2 rows here carry a driver name the kernel handed back, with the words of
# the program behind them. It was 5 until #385 took the refusal out, with the
# interface name, the other cards and the way back it interpolated. Named on
# every run rather than excused: an allowlist is read once and grown forever.
SAY_UNSAFE_AT_MOST=2
say_report appliance/bind-dataplane-nic
rm -rf "$SAY_DIR"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
