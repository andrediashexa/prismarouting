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
. /src/tools/selftest-verdict.sh
ISO=${ISO:-/src/$(sh /src/tools/version-stamp.sh --filename /src)}
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

# A WRITER ON THE CONSOLE, so that the medium can be ASKED something.
#
# This lab used to boot the medium and read. That is why #305 shipped: the one
# sentence that was wrong on a medium was the one nothing asked for. The gate
# read the self-test's line, which is correct there and says nothing about
# rebooting, and never typed the command whose answer told the operator to
# reboot a machine where nothing survives one.
#
# A fifo and not a timed `printf`, for the reason lab/install-iso.sh gives
# where it does the same: a command typed on a guess about when the prompt
# arrives is a test of the guess. The writer waits for the prompt to appear in
# the log and then sends, so it cannot type into a boot.
rm -f /tmp/boot-in
mkfifo /tmp/boot-in || { echo "cannot create the console pipe"; exit 1; }
(
  # Wait for the VTY, then ask. The medium autologins into it, so the prompt
  # appearing means there is something to ask.
  _n=0
  while [ "$_n" -lt "${BOOT_SECS:-420}" ]; do
    grep -q "^hexarouting>" /tmp/boot.log 2>/dev/null && break
    sleep 1; _n=$((_n + 1))
  done
  # Held open to the end: a writer that closes hands qemu an end of file on
  # the serial console, and the box then has no console for the rest of the
  # run.
  printf 'show interface\n'
  sleep "${BOOT_SECS:-420}"
) > /tmp/boot-in &
BOOT_WRITER=$!

timeout "${BOOT_SECS:-420}" qemu-system-x86_64 \
  $ACCEL -m "${BOOT_MEM:-4096}" -smp 2 -nographic -no-reboot \
  -cdrom "$ISO" -kernel /tmp/bf/vmlinuz -initrd /tmp/bf/initrd \
  -append "boot=live components console=ttyS0,115200 systemd.show_status=1" \
  < /tmp/boot-in > /tmp/boot.log 2>&1
kill "$BOOT_WRITER" 2>/dev/null || true

sed 's/\x1b\[[0-9;]*m//g' /tmp/boot.log > /tmp/clean.log

# The log leaves the container: without that, a failed boot leaves nothing to
# look at and the diagnosis becomes guesswork - exactly what this script exists
# to prevent.
cp /tmp/clean.log "${LOGCOPY:-/src/boot-console.log}" 2>/dev/null || true

sed -n '/boot self-test/,/=====$/p' /tmp/clean.log | head -40
# "ready" alone does not prove the ISO carries a version stamp: the self-test
# prints the version line and carries on without it. Demanding both stops a
# build that lost the stamp from passing green.
# WHAT THE MEDIUM TELLS AN OPERATOR TO DO, which is the half nothing asked
# for until #305. The banner two lines above this command says nothing here
# survives a reboot; the command used to answer "Reboot once the tuning has
# been applied", which on a medium throws the session away and comes back to
# the same screen.
#
# Read from the console rather than from the source, because the defect was
# that the product decided from a startup file that is identical on a medium
# and on an installed box's first boot. Only a medium can answer this.
if grep -q "show interface" /tmp/clean.log; then
  if grep -qiE "Reboot once the tuning" /tmp/clean.log; then
    echo "  [FAIL] the medium tells the operator to reboot, and nothing here"
    echo "         survives one: the reboot comes back to this same screen"
    grep -A 3 "no interface to forward on" /tmp/clean.log | head -4 | sed 's/^/         /'
    exit 1
  fi
  if grep -qiE "installation medium|'install'" /tmp/clean.log; then
    echo "  [ ok ] the medium says a reboot is not the answer there, and what is"
  else
    echo "  [FAIL] the medium says nothing about what does work there"
    grep -A 3 "no interface to forward on" /tmp/clean.log | head -4 | sed 's/^/         /'
    exit 1
  fi
else
  echo "  [FAIL] 'show interface' was never asked on the medium, so what it"
  echo "         tells an operator there is still untested - which is how"
  echo "         #305 shipped"
  exit 1
fi

if said_a_verdict /tmp/clean.log; then
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
  # ANCHORED. "hexarouting>" unanchored also matches systemd's own line
  # "Hostname set to <hexarouting>." - so this check, which is the one that
  # proves a login LANDS in the VTY, could be satisfied by a boot that never
  # reached the VTY at all. It has been able to since it was written.
  if grep -q "^hexarouting>" /tmp/clean.log; then
    #
    # ORDER, not merely presence. Both of these were on the console before
    # #93 and the boot still "passed": the getty drew the VTY prompt first and
    # the self-test printed on top of it, so what the operator saw was a wall
    # of text ending in "=====" and no prompt. The box was working and waiting
    # for input; it looked like it had stopped, and the only way to find out
    # was to press Enter on a router you have just been handed.
    #
    # A check that both strings appear cannot tell those two boots apart.
    #
    # The FIRST prompt and the LAST line of the report. Taking the last of
    # both would pass a boot where the prompt was drawn early and again at the
    # end - which is the shape of the bug, not the shape of a fix.
    _sel=$(grep -n "boot self-test" /tmp/clean.log | head -1 | cut -d: -f1)
    _end=$(grep -nE "$VERDICT_RE" /tmp/clean.log | tail -1 | cut -d: -f1)
    _pmt=$(grep -n "^hexarouting>" /tmp/clean.log | head -1 | cut -d: -f1)
    echo
    echo "=========== console order ==========="
    echo "  self-test starts at line $_sel, ends at $_end; the prompt is at $_pmt"
    if [ -n "$_end" ] && [ -n "$_pmt" ] && [ "$_pmt" -lt "$_end" ]; then
      echo
      echo "=========== THE PROMPT WAS BURIED ==========="
      echo "  The VTY prompt was drawn at line $_pmt and the self-test was"
      echo "  still printing at line $_end. An operator sees a wall of text"
      echo "  and no prompt, on a box that is waiting for them."
      echo "--- full console in ${LOGCOPY:-/src/boot-console.log} ---"
      exit 1
    fi
    echo "  the report finishes before the prompt is drawn"

    # The boot wait (#97). systemd prints "(22s / no limit)" while
    # hexarouting.service waits for VPP's CLI socket, and "no limit" reads as
    # "this is not going to finish" to somebody watching their first boot.
    echo
    echo "=========== the wait, explained or not ==========="
    if grep -q "waiting for the dataplane" /tmp/clean.log; then
      grep -E "waiting for the dataplane|still waiting|dataplane answered" \
        /tmp/clean.log | head -4 | sed 's/^/  /'
      if grep -q "dataplane answered after" /tmp/clean.log; then
        echo "  the wait said what it was for AND that it ended"
      else
        echo
        echo "=========== THE WAIT NEVER SAID IT ENDED ==========="
        echo "  It announced itself and then went quiet, which on a console"
        echo "  is indistinguishable from having hung."
        exit 1
      fi
    else
      echo "  the dataplane answered before anyone could notice: nothing said"
    fi
    # An expected wait reported as a problem is how operators learn to ignore
    # a console.
    if grep -q "VPP did not answer within" /tmp/clean.log; then
      echo
      echo "=========== THE DATAPLANE NEVER ANSWERED ==========="
      grep -A2 "VPP did not answer" /tmp/clean.log | head -3 | sed 's/^/  /'
      exit 1
    fi
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
