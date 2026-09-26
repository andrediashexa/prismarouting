#!/bin/sh
# appliance/initramfs/prisma-trim against a fake initramfs tree (#450): what
# it drops and, the half that matters, what it keeps. The size and the boot
# are measured on the medium; this is the list.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0
ok()  { echo "  [ ok ] $1"; }
bad() { echo "  FAIL: $1"; fail=$((fail + 1)); }
V=6.1.0-50-amd64
K=$T/usr/lib/modules/$V/kernel
for d in drivers/net/ethernet/intel drivers/gpu/drm drivers/infiniband \
         fs/xfs fs/btrfs fs/nfs \
         drivers/ata drivers/scsi drivers/nvme drivers/block drivers/md \
         drivers/usb/storage drivers/hid drivers/input drivers/virtio \
         fs/ext4 fs/squashfs fs/overlayfs fs/isofs fs/fat; do
  mkdir -p "$K/$d"; : > "$K/$d/x.ko"
done
echo "=== prereqs, as initramfs-tools asks every hook ==="
[ -z "$(sh "$REPO/appliance/initramfs/prisma-trim" prereqs)" ] \
  && ok "no prerequisites" || bad "prereqs printed something"
echo "=== what goes, and what stays ==="
DESTDIR=$T version=$V sh "$REPO/appliance/initramfs/prisma-trim"; RC=$?
[ $RC = 0 ] && ok "the hook exits 0" || bad "the hook exited $RC"
for gone in drivers/net drivers/gpu drivers/infiniband fs/xfs fs/btrfs fs/nfs; do
  [ -e "$K/$gone" ] && bad "$gone was kept" || ok "$gone dropped"
done
for kept in drivers/ata drivers/scsi drivers/nvme drivers/block drivers/md \
            drivers/usb/storage drivers/hid drivers/input drivers/virtio \
            fs/ext4 fs/squashfs fs/overlayfs fs/isofs fs/fat; do
  [ -e "$K/$kept/x.ko" ] && ok "$kept kept" || bad "$kept was dropped: a root behind it would not be found"
done
echo "=== an initramfs with no module tree is left alone ==="
DESTDIR=$T/empty version=$V sh "$REPO/appliance/initramfs/prisma-trim"; RC=$?
[ $RC = 0 ] && ok "no tree, nothing done, exit 0" || bad "exited $RC with no tree"
echo
[ "$fail" = 0 ] && echo "ALL TESTS PASSED" || echo "$fail FAILURES"
[ "$fail" = 0 ]
