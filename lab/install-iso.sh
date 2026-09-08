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
ISO=${ISO:-/src/$(sh /src/tools/version-stamp.sh --filename /src)}
DISK=${DISK:-/tmp/hexa-disk.qcow2}
SIZE=${DISK_SIZE:-8G}
LOG=${LOGCOPY:-/src/install-console.log}

# The answers the installer must ask for. They are given here rather than typed
# at the prompt because a test that sends keystrokes at the right moment tests
# the timing of the keystrokes - and because the point of the assertions below
# is that these exact values reach the disk.
NEW_HOST=${NEW_HOST:-cgnat-lab}
NEW_TZ=${NEW_TZ:-America/Sao_Paulo}
NEW_PW=${NEW_PW:-lab-secret-42}

[ -f "$ISO" ] || { echo "no ISO at $ISO"; exit 1; }
fail=0

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
# The live image must carry no usable credentials. Read its /etc/shadow out of
# the squashfs before anything else touches it - this is the file that used to
# travel onto the disk with a published password in it.
rm -rf /tmp/sq
if ! unsquashfs -d /tmp/sq -no-progress /mnt/iso/live/filesystem.squashfs \
       etc/shadow > /tmp/unsq.log 2>&1; then
  echo "  could not read the live image's /etc/shadow:"
  tail -3 /tmp/unsq.log | sed 's/^/    /'
fi
LIVE_ROOT=$(grep "^root:" /tmp/sq/etc/shadow 2>/dev/null | cut -d: -f2)
umount /mnt/iso
echo "live image root entry: '${LIVE_ROOT:-<unreadable>}'"

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
           hexarouting.install=/dev/vda hexarouting.yes \
           hexarouting.hostname=$NEW_HOST hexarouting.timezone=$NEW_TZ \
           hexarouting.password=$NEW_PW" \
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
  elif grep -qE "hexarouting-inst|install to disk" /tmp/install-clean.log; then
    # "hexarouting-inst", not "hexarouting-installer": systemd truncates a unit
    # description on the status line - "Starting hexarouting-insta...". Looking
    # for the full name never matched, so a unit that HAD started was reported
    # as never having started, and the next person to read that went and
    # checked ConditionKernelCommandLine, which was correct all along.
    if grep -q "install to disk" /tmp/install-clean.log; then
      echo "  the unit ran; what it printed:"
      grep -A6 "install to disk" /tmp/install-clean.log | tail -12 | sed 's/^/    /'
    else
      echo "  the unit STARTED and printed nothing."
      echo "  Something else owns the console: the installer writes to"
      echo "  /dev/console, and a getty that reached it first takes the"
      echo "  screen with a login prompt. Check the ordering between"
      echo "  getty@/serial-getty@ and hexarouting-installer.service."
      grep -nE "Started (serial-)?getty|Starting hexarouting-inst" \
        /tmp/install-clean.log | tail -4 | sed 's/^/    /'
    fi
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
echo "=========== WHAT LANDED ON THE DISK ==========="
# Read the installed filesystem directly. Booting it and logging in would be
# the other way to prove the password, and it would mean sending keystrokes at
# a login prompt at the right moment - the thing this lab exists not to do.
DISK_FAIL=0
# Mounted by byte offset, not through /dev/loop0p2.
#
# "losetup -P" attaches the image and the partition nodes never appear: the
# kernel creates them, udev publishes them, and there is no udev in this
# container. The first version of this check used them, could not read
# anything back, and reported it as though the install had gone wrong - so
# every step below says what happened rather than swallowing it.
# The tools this verification needs, checked BY NAME before it starts.
#
# Without this the missing tool surfaced as "could not read the installed
# filesystem back" - which blames the install for a tool that was never
# installed in the container, and sends whoever reads it to look at the
# installer. The same lab passed under "make test-install", whose target
# happens to apt-get fdisk, and failed under "tools/lab.sh install-iso",
# whose apt line did not: the same lab, two answers, depending on which
# command ran it.
for _t in qemu-img sfdisk; do
  command -v "$_t" >/dev/null 2>&1 || {
    echo "  [FAIL] $_t is not installed in this container."
    echo "         This step reads the installed disk back; it cannot run"
    echo "         without it, and its absence says nothing about the install."
    exit 1
  }
done

qemu-img convert -O raw "$DISK" /tmp/hexa-disk.raw 2>&1 | sed 's/^/  qemu-img: /'
ls -l /tmp/hexa-disk.raw 2>&1 | sed 's/^/  /'
sfdisk -J /tmp/hexa-disk.raw > /tmp/parts.json 2>/tmp/sfdisk-j.log \
  || sed 's/^/  sfdisk: /' /tmp/sfdisk-j.log
START=$(python3 -c '
import json, sys
t = json.load(open("/tmp/parts.json"))["partitiontable"]
print(max(t["partitions"], key=lambda p: p["size"])["start"])
' 2>/tmp/parse.log) || sed 's/^/  parse: /' /tmp/parse.log
echo "  root partition starts at sector ${START:-?}"
mkdir -p /tmp/target
if [ -n "$START" ] \
   && mount -o loop,ro,offset=$((START * 512)) /tmp/hexa-disk.raw /tmp/target \
        2>/tmp/mount.log; then
  INST_ROOT=$(grep "^root:" /tmp/target/etc/shadow 2>/dev/null | cut -d: -f2)
  INST_HOST=$(cat /tmp/target/etc/hostname 2>/dev/null || true)
  INST_TZ=$(cat /tmp/target/etc/timezone 2>/dev/null || true)
  echo "  hostname  ${INST_HOST:-<none>}"
  echo "  timezone  ${INST_TZ:-<none>}"
  echo "  root      $(printf '%s' "${INST_ROOT:-<none>}" | cut -c1-12)..."
  PW_OK=$(python3 -W ignore - "$INST_ROOT" "$NEW_PW" <<'PY'
# crypt goes away in Python 3.13; the container is Debian 12 (3.11). When the
# base image moves, this becomes passlib or a call to "openssl passwd".
import crypt, sys
h, pw = sys.argv[1], sys.argv[2]
print("yes" if h.startswith("$") and crypt.crypt(pw, h) == h else "no")
PY
)
  umount /tmp/target
else
  echo "  [FAIL] could not read the installed filesystem back"
  echo "         root partition starts at sector '${START:-?}'"
  sed 's/^/         /' /tmp/mount.log 2>/dev/null | head -3
  DISK_FAIL=1
fi

if [ "$DISK_FAIL" = 0 ]; then
  # THE assertion. Everything else about this install can be right and the box
  # can still ship with the password whoever built the image chose.
  if [ "${PW_OK:-no}" = yes ]; then
    echo "  [ ok ] the root password on disk is the one the installer was given"
  else
    echo "  [FAIL] the installed root password is NOT the answer given"
    fail=1
  fi
  if [ -n "${INST_ROOT:-}" ] && [ "${INST_ROOT:-}" != "${LIVE_ROOT:-x}" ]; then
    echo "  [ ok ] and it is not the entry the live image carried"
  else
    echo "  [FAIL] the installed shadow entry is the live image's"
    fail=1
  fi
  case "${LIVE_ROOT:-}" in
    ""|"!"*|"*") echo "  [ ok ] the live image itself carries no usable password" ;;
    *) echo "  [FAIL] the live image has a usable root password: an ISO on a"
       echo "         network is rootable by anyone who has read the release"
       fail=1 ;;
  esac
  if [ "${INST_HOST:-}" = "$NEW_HOST" ]; then
    echo "  [ ok ] the hostname answer reached the disk"
  else
    echo "  [FAIL] hostname is '${INST_HOST:-}', expected '$NEW_HOST'"
    fail=1
  fi
  if [ "${INST_TZ:-}" = "$NEW_TZ" ]; then
    echo "  [ ok ] the timezone answer reached the disk"
  else
    echo "  [FAIL] timezone is '${INST_TZ:-}', expected '$NEW_TZ'"
    fail=1
  fi
else
  fail=1
fi

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

# The routing daemons the VTY OFFERS, asserted on the INSTALLED box.
#
# This is the assertion that was missing, and its absence is what let #99 ship:
# lab/routing.sh proves BGP, OSPF and IS-IS by starting those daemons BY HAND,
# so the lab and the product disagreed about what runs and the lab was the
# optimistic one. Debian's /etc/frr/daemons has all three as "no", and
# frrinit.sh starts only zebra, mgmtd and staticd - so an installed box ran the
# dataplane, the VTY and static routes, and not one routing protocol.
#
# Checked here, on the disk that was actually installed, because that is the
# only place the question means anything.
if [ "$up" = 1 ]; then
  if grep -q "routing daemons up:" /tmp/disk-clean.log; then
    echo "  [ ok ] the installed box runs the daemons the VTY offers"
    grep -o "routing daemons up:.*" /tmp/disk-clean.log | head -1 | sed 's/^/         /'
  else
    echo "  [FAIL] the installed box is missing routing daemons the VTY offers"
    grep -A2 "routing daemons not running" /tmp/disk-clean.log | head -3 \
      | sed 's/^/         /'
    fail=1
  fi
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
