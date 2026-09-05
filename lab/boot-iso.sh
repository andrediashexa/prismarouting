#!/bin/sh
# Boots the ISO in QEMU and shows the console self-test.
#
# The CPU model matters twice over. QEMU's default (qemu64) has no SSE4.2 and
# VPP dies with SIGILL in 199ms - a restart loop that does not exist in the
# product. But "-cpu max" is no good either: it emulates up to AVX-512 in
# software and a TCG boot gets slow enough to blow the timeout. Nehalem is the
# minimum model that meets the real requirement (SSE4.2).
#
# BOOT_MEM at 4096: a live system keeps the whole writable root in RAM (an
# overlay over the squashfs). With 2 GB the OOM killer takes VPP - measured,
# with "Out of memory: Killed process (vpp)" on the console - and brings the
# rest down with it. It was not VPP being greedy: it was killed with 19 MB
# resident.
set -u
ISO=${ISO:-/src/hexarouting.iso}
SRC=${SRC:-/src}

# An ISO older than the sources does not test what you changed. It has already
# happened twice: the build died for a missing debootstrap and the next boot
# passed the previous image, with the old strings in it.
if [ "${ALLOW_STALE_ISO:-0}" != 1 ] && [ -f "$ISO" ]; then
  newer=$(find "$SRC/appliance" "$SRC/src" -type f -newer "$ISO" -print 2>/dev/null | head -1)
  if [ -n "$newer" ]; then
    echo "ISO is older than the sources (e.g. $newer)" >&2
    echo "rebuild with appliance/build-iso.sh, or set ALLOW_STALE_ISO=1" >&2
    exit 1
  fi
fi
mkdir -p /mnt/iso /tmp/bf
mount -o loop,ro "$ISO" /mnt/iso
cp /mnt/iso/live/vmlinuz /mnt/iso/live/initrd /tmp/bf/
umount /mnt/iso

# With KVM the boot takes seconds instead of minutes, and "-cpu host" gives
# the real instruction sets - which is what VPP needs. Without KVM it falls
# back to TCG with Nehalem, the minimum meeting the requirement (SSE4.2)
# without the cost of emulating AVX-512 as "-cpu max" would.
if [ -w /dev/kvm ]; then
  ACCEL="-enable-kvm -cpu host"
  echo "accelerating with KVM"
else
  ACCEL="-cpu ${BOOT_CPU:-Nehalem}"
  echo "no KVM: emulating with ${BOOT_CPU:-Nehalem} (slow)"
fi

timeout "${BOOT_SECS:-420}" qemu-system-x86_64 \
  $ACCEL -m "${BOOT_MEM:-4096}" -smp 2 -nographic -no-reboot \
  -cdrom "$ISO" -kernel /tmp/bf/vmlinuz -initrd /tmp/bf/initrd \
  -append "boot=live components console=ttyS0,115200 systemd.show_status=1" \
  > /tmp/boot.log 2>&1

sed 's/\x1b\[[0-9;]*m//g' /tmp/boot.log > /tmp/clean.log

# The log leaves the container: without that, a failed boot leaves nothing to
# look at and the diagnosis becomes guesswork - exactly what this script exists
# to prevent.
cp /tmp/clean.log "${LOGCOPY:-/src/boot-console.log}" 2>/dev/null || true

sed -n '/boot self-test/,/=====$/p' /tmp/clean.log | head -40
# "ready" alone does not prove the ISO carries a version stamp: the self-test
# prints the version line and carries on without it. Demanding both stops a
# build that lost the stamp from passing green.
if grep -q "HexaRouting ready" /tmp/clean.log; then
  if ! grep -qE "\[ ok \] HexaRouting [0-9]" /tmp/clean.log; then
    echo
    echo "=========== READY, BUT NO VERSION STAMP ==========="
    grep -i "version" /tmp/clean.log | tail -5 | sed 's/^/  /'
    echo "--- full console in ${LOGCOPY:-/src/boot-console.log} ---"
    exit 1
  fi
  # Two different things, both required. The self-test line proves hexash
  # STARTS - 0.2.0 shipped with it broken while every other check passed. The
  # prompt proves a login LANDS in it: a box that boots to a bash prompt has
  # the CGNAT running and the front door wrong, and nothing else in the boot
  # log would say so.
  if ! grep -q "\[ ok \] VTY starts" /tmp/clean.log; then
    echo
    echo "=========== READY, BUT THE VTY DID NOT START ==========="
    grep -i "hexash\|VTY" /tmp/clean.log | tail -6 | sed 's/^/  /'
    echo "--- full console in ${LOGCOPY:-/src/boot-console.log} ---"
    exit 1
  fi
  if grep -q "hexarouting>" /tmp/clean.log; then
    exit 0
  fi
  echo
  echo "=========== READY, BUT DID NOT LAND IN THE VTY ==========="
  echo "--- last 15 console lines ---"
  tail -15 /tmp/clean.log | sed 's/^/  /'
  echo "--- full console in ${LOGCOPY:-/src/boot-console.log} ---"
  exit 1
fi

echo
echo "=========== SELF-TEST DID NOT REPORT READY ==========="
echo "--- did the self-test even run? ---"
grep -c "boot self-test" /tmp/clean.log | sed 's/^/  occurrences: /'
echo "--- services ---"
grep -iE "hexarouting|vpp\.service|frr\.service" /tmp/clean.log | tail -10 | sed 's/^/  /'
echo "--- systemd failures ---"
grep -iE "FAILED|Failed to start|Dependency failed" /tmp/clean.log | tail -8 | sed 's/^/  /'
echo "--- last 25 console lines ---"
tail -25 /tmp/clean.log | sed 's/^/  /'
echo "--- full console in ${LOGCOPY:-/src/boot-console.log} ---"
exit 1
