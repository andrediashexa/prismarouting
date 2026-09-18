#!/bin/sh
# Does a configuration trail entry actually reach the disk?
#
#   sh tools/lab.sh trail-fsync
#
# `docs/logging.md` states it as a MUST and says why in the same sentence:
#
#   Each write MUST be fsynced: a compliance log MUST NOT live only in the
#   page cache.
#
# The `fsync` is in `bpa_audit_entry()`. Removing it left all 33 suites green
# AND `lab/audit.sh` green - 36 assertions on that exact file - because every
# one of them reads the trail back through the same kernel that buffered it
# (#243). A page-cache-only file reads perfectly right up until the box loses
# power, which is the one moment a retention obligation is about.
#
# HOW THIS MEASURES IT WITHOUT A DEBUGGER. The trail is put on an ext4
# filesystem loop-mounted from an image file. Writes to that filesystem land in
# the page cache of the loop device; the loop driver copies a block down to the
# BACKING FILE only when that block is written back, which `fsync` forces and
# the writeback timer otherwise leaves for up to thirty seconds. So reading the
# image file directly, immediately, answers the question the product is being
# asked: is it on the disk, or only in memory?
#
# Section 3 is the control, and without it this lab proves nothing: it writes a
# file to the same filesystem WITHOUT fsync and shows that the instrument can
# tell the difference. A filesystem that happened to write everything through
# would pass section 2 on its own.
set -u

mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do
  sleep 1; n=$((n+1))
done
vppctl show version >/dev/null 2>&1 || {
  echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

IMG=/rec.img
MNT=/records
TRAIL=$MNT/config-history.log

echo "=========== 1. a filesystem whose disk can be read separately ==========="
dd if=/dev/zero of=$IMG bs=1M count=16 2>/dev/null
mkfs.ext4 -q -F $IMG || { echo "LAB NOT MEASURED: no mkfs.ext4"; exit 2; }
mkdir -p $MNT
mount -o loop $IMG $MNT || { echo "LAB NOT MEASURED: cannot loop-mount"; exit 2; }
echo "  $IMG mounted at $MNT, and $IMG is readable on its own"
vppctl bpa audit-path $TRAIL
vppctl show bpa pool history | head -1 | sed 's/^/  /'

echo
echo "=========== 2. a pool change, and the disk right afterwards ==========="
# The needle is the pool's name, which only this entry puts in the file.
vppctl bpa pool syncme inside 100.64.0.0/22 outside 200.150.0.16/28 block 512
# `grep -c` PRINTS the count and EXITS 1 when it is zero, so a `|| echo 0`
# here appends a second line and the comparison below fails with "integer
# expression expected" on a correct box. The first run of this lab did
# exactly that and reported the control as broken.
ON_DISK=$(grep -c "syncme" $IMG 2>/dev/null)
IN_FILE=$(grep -c "syncme" $TRAIL 2>/dev/null)
echo "  entries naming syncme, read through the filesystem: $IN_FILE"
echo "  entries naming syncme, read off the disk image:     $ON_DISK"

echo
echo "=========== 3. the control: the same filesystem, no fsync ==========="
# Written the same way the trail is, minus the one call under test. If this
# needle reached the image too, the instrument cannot tell fsync from no
# fsync and nothing above means anything.
python3 - <<'PY'
f = open("/records/control.log", "w")
f.write("CONTROLNEEDLE unsynced\n")
f.flush()
f.close()
PY
CTRL_ON_DISK=$(grep -c "CONTROLNEEDLE" $IMG 2>/dev/null)
CTRL_IN_FILE=$(grep -c "CONTROLNEEDLE" $MNT/control.log 2>/dev/null)
echo "  the control file, read through the filesystem: $CTRL_IN_FILE"
echo "  the control file, read off the disk image:     $CTRL_ON_DISK"

echo
echo "=========== 4. and it is on the disk because of fsync, not because ==========="
echo "===========    the filesystem was unmounted afterwards            ==========="
# Stated rather than assumed: nothing has unmounted or synced $MNT between
# section 2 and here, so the only thing that could have pushed the trail's
# block down is the fsync in bpa_audit_entry().
mount | grep -F "$MNT" | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

# The precondition: the entry exists at all. Without it the disk read below
# would be looking for something the box never wrote.
expect "the pool change was recorded in the trail" test "${IN_FILE:-0}" -ge 1

# The control first, because it is what makes the next assertion mean
# anything. A write that is not fsynced must NOT be on the disk yet.
expect "an unsynced write to the same filesystem is not on the disk yet" \
  test "${CTRL_ON_DISK:-1}" -eq 0
expect "and it is there through the filesystem, so it really was written" \
  test "${CTRL_IN_FILE:-0}" -ge 1

# The claim.
expect "the trail entry is on the disk, not only in the page cache" \
  test "${ON_DISK:-0}" -ge 1

verdict
