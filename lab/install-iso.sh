#!/bin/sh
# Installs the ISO onto a blank disk, then boots that disk with the ISO gone.
#
# The second boot is the whole point. Everything before it only proves the
# installer ran; booting the disk with no ISO attached proves it produced a
# system that stands on its own - which is the thing the live image cannot do
# and the reason #33 exists.
#
# It also proves the part the live image can never reach: the installed system
# applies isolcpus and hugepages, so its self-test comes up OUT of first-boot
# mode. On the live ISO that path is dead code by construction.
set -u
ISO=${ISO:-/src/hexarouting.iso}
DISK=${DISK:-/tmp/hexa-disk.qcow2}
SIZE=${DISK_SIZE:-8G}
LOG=${LOGCOPY:-/src/install-console.log}

[ -f "$ISO" ] || { echo "no ISO at $ISO"; exit 1; }

if [ -w /dev/kvm ]; then
  ACCEL="-enable-kvm -cpu host"
  echo "accelerating with KVM"
else
  ACCEL="-cpu ${BOOT_CPU:-Nehalem}"
  echo "no KVM: emulating with ${BOOT_CPU:-Nehalem} (slow)"
fi

mkdir -p /mnt/iso /tmp/bf
mount -o loop,ro "$ISO" /mnt/iso
cp /mnt/iso/live/vmlinuz /mnt/iso/live/initrd /tmp/bf/
umount /mnt/iso

rm -f "$DISK"
qemu-img create -f qcow2 "$DISK" "$SIZE" >/dev/null

echo "=========== PHASE 1: install onto a blank disk ==========="
# hexarouting.yes runs it unattended. An interactive installer driven by
# keystrokes timed against a boot would be testing the timing.
timeout "${INSTALL_SECS:-600}" qemu-system-x86_64 \
  $ACCEL -m "${BOOT_MEM:-8192}" -smp 2 -nographic -no-reboot \
  -cdrom "$ISO" -kernel /tmp/bf/vmlinuz -initrd /tmp/bf/initrd \
  -drive file="$DISK",format=qcow2,if=virtio \
  -append "boot=live components console=ttyS0,115200 systemd.show_status=1 \
           hexarouting.install=/dev/vda hexarouting.yes" \
  > /tmp/install.log 2>&1

sed 's/\x1b\[[0-9;]*m//g' /tmp/install.log > /tmp/install-clean.log
cp /tmp/install-clean.log "$LOG" 2>/dev/null || true
sed -n '/install to disk/,/installed on/p' /tmp/install-clean.log | head -30

if ! grep -q "HexaRouting installed to /dev/vda" /tmp/install-clean.log; then
  echo
  echo "=========== THE INSTALL DID NOT FINISH ==========="
  # systemd skipping the unit looks nothing like the installer failing, and
  # the first version of this test could not tell them apart: both produced an
  # empty section and the same message. It cost a full build cycle.
  if grep -q "Ordering cycle found" /tmp/install-clean.log; then
    echo "  systemd dropped the installer unit to break an ordering cycle:"
    grep -i "ordering cycle" /tmp/install-clean.log | tail -3 | sed 's/^/    /'
  elif grep -qE "hexarouting-installer|install to disk" /tmp/install-clean.log; then
    echo "  the unit ran; what it printed:"
    grep -A6 "install to disk" /tmp/install-clean.log | tail -12 | sed 's/^/    /'
  else
    echo "  the installer unit never started at all - check"
    echo "  ConditionKernelCommandLine against the -append line."
  fi
  if grep -q "HexaRouting inst" /tmp/install-clean.log; then
    echo "  NOTE: the marker line is present but truncated, so the install"
    echo "  finished and the machine was killed before it could say so."
  fi
  grep -iE "error:|failed|not confirmed|not a block" /tmp/install-clean.log \
    | tail -8 | sed 's/^/  /'
  echo "--- full console in $LOG ---"
  exit 1
fi
echo "  install reported success"

echo
echo "=========== PHASE 2: boot the disk, ISO detached ==========="
# The SAME memory as phase 1, deliberately.
#
# Booting the installed disk with less was an optimisation that fabricated a
# failure: the tuning is computed during the install, from the RAM the machine
# has then, and a box does not shrink between being installed and being turned
# on. Giving phase 2 less produced hugepages reserved for 4 GB on a 3 GB boot,
# VPP segfaulting in a restart loop, and an afternoon spent reading it as a
# product bug.
# No -cdrom and no -kernel: if the disk cannot boot itself, nothing here will
# boot it for it.
timeout "${BOOT_SECS:-300}" qemu-system-x86_64 \
  $ACCEL -m "${DISK_BOOT_MEM:-8192}" -smp 2 -nographic -no-reboot \
  -drive file="$DISK",format=qcow2,if=virtio \
  > /tmp/disk.log 2>&1

sed 's/\x1b\[[0-9;]*m//g' /tmp/disk.log > /tmp/disk-clean.log
cat /tmp/disk-clean.log >> "$LOG" 2>/dev/null || true
sed -n '/boot self-test/,/=====$/p' /tmp/disk-clean.log | head -40

fail=0
up=1
# "Came up and failed its checks" and "never said anything" are different
# failures needing different work, and reporting the first as the second sent
# the diagnosis to the bootloader while the real problem was VPP segfaulting
# in a restart loop, plainly printed on the same console.
if grep -q "The box is NOT ready" /tmp/disk-clean.log; then
  echo
  echo "=========== THE INSTALLED SYSTEM CAME UP AND FAILED ITS CHECKS ==========="
  sed -n '/boot self-test/,/=====$/p' /tmp/disk-clean.log \
    | grep -E "\[FAIL\]|\[warn\]" | sed 's/^/  /'
  echo "  --- what the tuner decided, and from what ---"
  grep -E "hexarouting-tune|1G hugepages|WARNING|cmdline hugepages|reserved 1G" \
    /tmp/disk-clean.log | tail -8 | sed 's/^/  /'
  echo "  --- what the dataplane said ---"
  grep -iE "vpp.service|SEGV|Killed|Out of memory" /tmp/disk-clean.log \
    | tail -6 | sed 's/^/  /'
  fail=1
elif ! grep -q "HexaRouting ready" /tmp/disk-clean.log; then
  echo
  echo "=========== THE INSTALLED SYSTEM DID NOT COME UP ==========="
  if grep -q "grub>" /tmp/disk-clean.log; then
    echo "  GRUB reached its own prompt, so the bootloader installed correctly"
    echo "  and then found nothing to boot: no kernel, no initrd, or no"
    echo "  grub.cfg on the disk. Check that /boot survived the copy."
  elif grep -q "Welcome to GRUB" /tmp/disk-clean.log; then
    echo "  GRUB loaded and the console then went silent. Usually the installed"
    echo "  system has no serial console configured, not that it failed to boot."
  fi
  tail -20 /tmp/disk-clean.log | sed 's/^/  /'
  up=0
  fail=1
fi
if ! grep -q "\[ ok \] VTY starts" /tmp/disk-clean.log; then
  echo "  [FAIL] the VTY did not start on the installed system"
  fail=1
fi
# Did the tuning survive the install?
#
# The evidence is the KERNEL COMMAND LINE, not the profile. isolcpus and the
# C-state parameters are written by the installer and can only be there if the
# tuning ran and update-grub picked it up - and a live system never has them,
# so their presence is proof the installed system is not the live one.
#
# The profile is deliberately NOT the test. Whether the box leaves first-boot
# mode depends on whether it has the RAM for a hugepage pool, and a small box
# staying conservative is the tuner working, not failing. An earlier version of
# this check demanded the tuned profile and failed a box that was behaving
# exactly as designed.
#
# Guarded by "up": absence of a string proves nothing when the console produced
# nothing at all. An even earlier version reported the tuning as applied on a
# boot that had shown only "Welcome to GRUB!".
if [ "$up" = 0 ]; then
  echo "  [skip] tuning: cannot be judged, the system said nothing"
else
  if grep -qE "Kernel command line:.*isolcpus=" /tmp/disk-clean.log; then
    echo "  [ ok ] the tuning reached the installed kernel command line"
  else
    echo "  [FAIL] no isolcpus on the installed kernel command line:"
    echo "         the tuning did not survive the install"
    fail=1
  fi
  if grep -qE "Kernel command line:.*max_cstate=" /tmp/disk-clean.log; then
    echo "  [ ok ] and so did the latency parameters"
  else
    echo "  [FAIL] the low-latency parameters did not survive the install"
    fail=1
  fi
  if grep -q "FIRST BOOT mode" /tmp/disk-clean.log; then
    hp=$(grep -oE "hugepages=[0-9]+" /tmp/disk-clean.log | head -1)
    echo "  [info] conservative profile (${hp:-hugepages=?}): correct for this"
    echo "         box, which has too little RAM for a 1G pool"
  else
    echo "  [ ok ] fully tuned profile in effect"
  fi
fi

echo
if [ "$fail" = 0 ]; then
  echo "LAB PASSED"
  exit 0
fi
echo "LAB FAILED"
echo "--- full console in $LOG ---"
exit 1
