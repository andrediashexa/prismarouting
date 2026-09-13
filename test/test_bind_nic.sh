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

runbind() { OUT=$(HEXA_ROOT="$1" sh "$BIN" 2>&1); RC=$?; }

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
echo "=== no device named: this says nothing, because two others already do ==="
R=$(mkbox 0000:03:00.0 eth0 iommu vfio-pci)
printf 'dpdk {\n  no-pci\n}\n' > "$R/etc/vpp/startup.conf"
runbind "$R"
[ "$RC" = 0 ] && [ -z "$OUT" ] \
  && ok "silent and successful, so the boot is not noisier for a known state" \
  || bad "the no-pci case said something: rc=$RC $OUT"

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
said "it is on ixgbe now" \
  && ok "and names the driver it is on instead, which is the state to act on" \
  || bad "it did not say where the device ended up: $OUT"

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
[ "$(cat "$R/sys/bus/pci/devices/0000:03:00.0/driver_override" 2>/dev/null)" \
  = "uio_pci_generic" ] \
  && ok "and it is uio_pci_generic that is asked for" \
  || bad "it still asked for vfio-pci on a box with no IOMMU"

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

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
