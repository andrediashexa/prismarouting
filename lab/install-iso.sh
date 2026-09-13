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
#
# And the installed box's console is DRIVEN, not only read (#189). A role is
# only real if an account the product created on a box somebody RECEIVES can
# use it, and until this existed the only proof of one was lab/vty.sh: in a
# container, against a VPP the lab had started itself, with the memberships
# handed out by the lab. That is the shape of #99, #168, #182 and #184, and it
# cost three shipped defects in two days when the same assumption was made
# about VPP workers (#199, #200, #201).
#
#   sh tools/lab.sh install-iso                  the lab
#   sh tools/lab.sh install-iso wrong-password   the driver typing a password
#                                                the box will refuse
#
# The second is not a mode anybody wants; it is how this lab is shown to FAIL
# rather than hang when the box does not answer, which is the one failure a
# console driver has to get right.
set -u
ISO=${ISO:-/src/$(sh /src/tools/version-stamp.sh --filename /src)}
DISK=${DISK:-/tmp/hexa-disk.qcow2}
# 40G, not 8G. The installer reserves 8 GB for the system before it will give
# any of the disk to connection records (#83), so an 8 GB disk is one the
# installer correctly refuses - it found that out by refusing this lab. qcow2
# is sparse: a 40 GB disk that holds 3 GB costs 3 GB.
#
# It is also closer to honest. An appliance nobody would deploy on 8 GB is not
# a useful thing to prove installs on 8 GB.
SIZE=${DISK_SIZE:-40G}
LOG=${LOGCOPY:-/src/install-console.log}

# The answers the installer must ask for. They are given here rather than typed
# at the prompt because a test that sends keystrokes at the right moment tests
# the timing of the keystrokes - and because the point of the assertions below
# is that these exact values reach the disk.
NEW_HOST=${NEW_HOST:-cgnat-lab}
NEW_TZ=${NEW_TZ:-America/Sao_Paulo}
NEW_PW=${NEW_PW:-lab-secret-42}

# The accounts the VTY is made to create on the installed box, and what the
# driver types to log in as them.
#
# The password is a crypt HASH because that is what the `user` block takes: a
# plaintext one lands in /etc/shadow and simply never matches, so the VTY
# refuses it. The salt is fixed so two runs put the same line on the console
# and a diff between two consoles is a real difference.
OP_USER=${OP_USER:-netadm}
OP_PW=${OP_PW:-op-secret-42}
RO_USER=${RO_USER:-lookonly}
RO_PW=${RO_PW:-ro-secret-42}
crypt_hash() {  # <plaintext> -> md5-crypt hash, as `user ... password` takes
  # md5-crypt and not sha512, which is what the VTY's own refusal suggests. An
  # sha512 hash is about 106 characters: typed at an 80-column serial console
  # it wraps, and readline redraws the line around the wrap, so what the
  # console log holds is the hash with escape sequences through the middle of
  # it. Nothing breaks - the box gets the characters - but the artefact this
  # lab leaves behind stops being readable, and that artefact is the evidence.
  #
  # crypt goes away in Python 3.13; the container is Debian 12 (3.11), the same
  # note the password check below carries.
  python3 -W ignore -c 'import crypt,sys; print(crypt.crypt(sys.argv[1], "$1$hexalab"))' "$1"
}
OP_HASH=$(crypt_hash "$OP_PW")
RO_HASH=$(crypt_hash "$RO_PW")

# The account a "commit confirmed" creates and never confirms (#212). It is an
# account and not a pool because creating one is what `touches_access` means:
# it is the change this VTY turns into a confirmed commit whether or not the
# operator asked, which is how the gap was found in the first place.
TMP_USER=${TMP_USER:-nevercnf}
TMP_HASH=$(crypt_hash "tmp-secret-42")

# The password the driver TYPES at the console, which is the answer the
# installer was given and is deliberately a SECOND name for it. Making them
# differ is the only way to see what this lab does when the box answers and
# refuses - and a console driver whose failure mode is a hang is not finished.
LOGIN_PW=$NEW_PW
case "${1:-}" in
  "") ;;
  wrong-password)
    LOGIN_PW="not-$NEW_PW"
    echo "running with a root password the box will REFUSE: this lab MUST"
    echo "fail, and it must say which wait timed out and why" ;;
  *)
    echo "usage: install-iso.sh [wrong-password]" >&2; exit 1 ;;
esac

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
SHADOW_READ=1
if ! unsquashfs -d /tmp/sq -no-progress /mnt/iso/live/filesystem.squashfs \
       etc/shadow > /tmp/unsq.log 2>&1; then
  echo "  could not read the live image's /etc/shadow:"
  tail -3 /tmp/unsq.log | sed 's/^/    /'
  SHADOW_READ=0
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
           hexarouting.log-gb=2 \
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

# It verified the disk before it said that (#83).
#
# The marker above only proves the installer exited zero, and it would exit
# zero having looked at nothing if the verification were ever dropped or
# short-circuited. The lines it prints are the evidence that it looked - on the
# disk this lab actually installed, which is the only place blkid can answer
# for the UUIDs in the fstab that was just written.
#
# test/test_installer.sh drives the broken trees this cannot produce: an
# install that succeeds cannot demonstrate what a failed verification does.
if grep -q "verifying the disk that was just written" /tmp/install-clean.log; then
  echo "  [ ok ] the installer verified the disk before reporting success"
  grep -E "\[ ok \] (GRUB boots|fstab:|root has a password)" \
    /tmp/install-clean.log | sed 's/^/         /'
else
  echo "  [FAIL] the installer reported success without checking what it wrote:"
  echo "         a box that boots to a grub> prompt reports as an install that"
  echo "         worked, in front of whoever is standing there"
  fail=1
fi

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
  # An unread /etc/shadow is NOT a clean /etc/shadow.
  #
  # This used to accept an empty LIVE_ROOT as proof, and the read above is
  # deliberately tolerant of unsquashfs failing - so on any runner without it,
  # the check that guards a published-password ISO passed without reading a
  # byte of the image. The whole point of this check is the case where nobody
  # is looking.
  if [ "$SHADOW_READ" != "1" ]; then
    echo "  [FAIL] could not read the live image's /etc/shadow, so nothing"
    echo "         here proves the ISO carries no usable root password"
    fail=1
  else
    case "${LIVE_ROOT:-}" in
      "!"*|"*"|"") echo "  [ ok ] the live image itself carries no usable password" ;;
      *) echo "  [FAIL] the live image has a usable root password: an ISO on a"
         echo "         network is rootable by anyone who has read the release"
         fail=1 ;;
    esac
  fi
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
#
# ---------------------------------------------------------- the console driver
#
# A named pipe is qemu's stdin, so this lab can TYPE at the same serial line an
# operator is handed. Everything it types is in drive_console() below.
#
# Nothing here sleeps and hopes. A serial console echoes what is typed, a
# prompt arrives when the box is ready and not when the driver is, and hexash
# is not a shell - so every wait is for a string the BOX prints, with a timeout
# and a report naming the string it gave up on. A driver that cannot tell "the
# box never asked" from "the box asked and refused" is worse than no driver:
# both look like a timeout, and they need opposite work.
CONSOLE_IN=/tmp/console.in
CONSOLE_LOG=/tmp/disk.log
CONSOLE_POS=1
: > "$CONSOLE_LOG"
rm -f "$CONSOLE_IN"
mkfifo "$CONSOLE_IN" || { echo "cannot create the console pipe $CONSOLE_IN"; exit 1; }

# What the console has printed since the last thing the driver recognised.
#
# \r goes, because a serial console ends every line with one and a pattern
# anchored on the start of a line would never match otherwise. The escape
# sequences go with it: readline redraws its prompt with them, so the string
# "hexarouting> " arrives cut in half by a cursor move often enough to matter.
console_since() {
  tail -c "+$CONSOLE_POS" "$CONSOLE_LOG" 2>/dev/null \
    | tr -d '\r' | sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g'
}

send() { printf '%s\n' "$*" >&3; }

# The whole console from the top, for the one question `console_since` cannot
# answer: what order two strings arrived in. Everything else here reads from
# the last thing recognised forward, deliberately - but "the box said it was
# ready BEFORE it offered a login prompt" is a claim about the transcript, not
# about what has happened since.
console_all() {
  tr -d '\r' < "$CONSOLE_LOG" 2>/dev/null | sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g'
}

# Did <first> appear before <second> in everything printed so far?
#
# Byte offsets and not line numbers: the login prompt has no line ending of its
# own, so whatever the box printed last shares a line with it and a line
# comparison puts the two in the wrong order exactly when it matters.
printed_before() {  # <first> <second>
  console_all | awk -v a="$1" -v b="$2" '
    BEGIN { pa = -1; pb = -1; off = 0 }
    {
      if (pa < 0 && index($0, a)) pa = off + index($0, a)
      if (pb < 0 && index($0, b)) pb = off + index($0, b)
      off += length($0) + 1
    }
    END { exit !(pa > 0 && pb > 0 && pa < pb) }'
}

console_gave_up() {  # <string> <what the driver was doing> <how long>
  echo
  echo "=========== THE CONSOLE DRIVER GAVE UP ==========="
  echo "  waiting for: '$1'"
  echo "  which was:   $2"
  echo "  it waited:   $3"
  echo "  what the console printed since the last thing the driver"
  echo "  recognised:"
  # The newline is not decoration: the console's last line has no line ending
  # of its own - it is a half-drawn prompt - so without this the diagnosis
  # below is printed onto the end of it and reads as the box's own words.
  console_since | tail -25 | sed 's/^/    | /'
  echo
  # The whole reason this function exists rather than an "expect timed out".
  # These four are different failures needing different work, and reporting
  # them as one timeout sends whoever reads it to the timing when the answer
  # is already on the screen.
  if console_since | grep -q "Login incorrect"; then
    echo "  DIAGNOSIS: the box ANSWERED and REFUSED the credentials. Nothing"
    echo "  here is slow: the password the installer was given and the one the"
    echo "  driver typed are not the same password."
  elif console_since | grep -qi "permission denied"; then
    echo "  DIAGNOSIS: the box ANSWERED and REFUSED the command. The account"
    echo "  does not have the reach its role implies - which is the defect"
    echo "  this lab exists to catch, and not a fault in the driver."
  elif [ -z "$(console_since | tr -d ' \t\n')" ]; then
    echo "  DIAGNOSIS: the box printed NOTHING at all since then. It is not"
    echo "  refusing anything - the console went silent, so the question is"
    echo "  whether the machine is still running, not what was typed at it."
  else
    echo "  DIAGNOSIS: the box is talking and never said the string above."
    echo "  Two different things end here and the tail tells them apart: the"
    echo "  box ANSWERED SOMETHING ELSE, which is this lab's assertion failing"
    echo "  and the answer is printed above - or a prompt's wording changed,"
    echo "  which fails identically and means nothing is wrong with the box."
  fi
  echo "  --- full console in $LOG ---"
}

wait_for() {  # <string> <seconds> <what the driver was doing>
  _s=$1; _t=$2; _why=$3; _n=0
  while :; do
    if console_since | grep -qF "$_s"; then
      # Everything up to here has been recognised, so the next wait starts
      # after it. Without this a prompt printed once satisfies every later
      # wait for a prompt, and the driver races ahead of the box saying yes
      # to itself.
      CONSOLE_POS=$(( $(wc -c < "$CONSOLE_LOG") + 1 ))
      return 0
    fi
    if ! kill -0 "$QEMU_PID" 2>/dev/null; then
      console_gave_up "$_s" "$_why" "${_n}s, and then the machine exited"
      return 1
    fi
    if [ "$_n" -ge "$_t" ]; then
      console_gave_up "$_s" "$_why" "${_t}s"
      return 1
    fi
    sleep 1
    _n=$((_n + 1))
  done
}

# Wait until the box prints ONE of two strings, and say which.
#
# `wait_for` is the right shape when there is one right answer and everything
# else is a timeout. It is the wrong shape when the box has two answers and
# one of them is the defect: waiting for the good one turns a refusal the box
# printed in a second into a five-minute wait, and the report then blames the
# timing for something the screen said at once. Answers 0 for the first
# string, 1 for the second, 2 for neither.
expect_one_of() {  # <good> <bad> <seconds> <what the driver was doing>
  _g=$1; _b=$2; _t=$3; _why=$4; _n=0
  while :; do
    if console_since | grep -qF "$_g"; then
      CONSOLE_POS=$(( $(wc -c < "$CONSOLE_LOG") + 1 )); return 0
    fi
    if console_since | grep -qF "$_b"; then
      CONSOLE_POS=$(( $(wc -c < "$CONSOLE_LOG") + 1 )); return 1
    fi
    if ! kill -0 "$QEMU_PID" 2>/dev/null; then
      console_gave_up "$_g' or '$_b" "$_why" "${_n}s, and then the machine exited"
      return 2
    fi
    if [ "$_n" -ge "$_t" ]; then
      console_gave_up "$_g' or '$_b" "$_why" "${_t}s"
      return 2
    fi
    sleep 1
    _n=$((_n + 1))
  done
}

# The markers the box is made to print are split in the command - echo
# "HX""LAB" - so that what comes back is the box's ANSWER and never the
# driver's own keystrokes echoed back at it. Every assertion below anchors on
# the start of a line for the same reason.
drive_console() {
  # ------------------------------------- the FIRST prompt the box ever offers
  #
  # This lab used to start at "HexaRouting ready." and then ask for a prompt,
  # and the comment here said why: the getty drew "cgnat-lab login:" while
  # hexarouting.service was still waiting for the dataplane, so `configure`
  # typed at that prompt built its candidate out of vppctl's "Could not find
  # VPP CLI socket" and every commit in the session was then refused. Starting
  # later was the right call for a lab about ROLES - and it left the defect
  # untested, described in a comment, on a box somebody receives (#211).
  #
  # So the lab now starts where the operator does. The first thing anybody
  # does with a new router is log in at the first prompt it shows them, and
  # what happens next is the product's answer, not the timing of this driver.
  #
  # The fix has two halves and this exercises both. The getty is ordered after
  # the self-test, which is ordered after hexarouting.service, whose
  # ExecStartPre waits for VPP - so a prompt offered at all means the dataplane
  # is up. And the VTY refuses to build a candidate from a dataplane that is
  # not answering, rather than building one out of the error text.
  #
  # 420s because this is a cold boot of an installed disk, not a wait for any
  # particular ordering: the prompt arrives when the box has finished starting.
  wait_for "$NEW_HOST login:" 420 \
    "the installed box to boot and offer its first login prompt" || return 1

  # Half one, read off the transcript: the readiness line came FIRST.
  #
  # Not a proxy for it. "HexaRouting ready." is printed by the self-test, which
  # is ordered after hexarouting.service, so its position in the transcript is
  # the ordering itself. Before the drop-in was split in two, the installer
  # deleted the file carrying this ordering along with the autologin it was
  # sharing a file with, and an installed box had no ordering at all.
  if printed_before "HexaRouting ready." "$NEW_HOST login:"; then
    echo "  [ ok ] the box reports itself ready before it offers a login"
  else
    echo "  [FAIL] the box offered a login prompt before it was ready"
    echo "         the getty reached the console ahead of the self-test, so"
    echo "         the prompt is drawn while hexarouting.service is still"
    echo "         waiting for VPP. An operator who logs in there is in a VTY"
    echo "         that cannot read the box (#211). On the live image the"
    echo "         ordering is in 10-after-selftest.conf; on an installed box"
    echo "         it is the same file, and the installer must delete only"
    echo "         20-autologin.conf beside it."
    fail=1
  fi

  send "root"
  wait_for "Password:" 60 \
    "the first login to ask for root's password" || return 1
  send "$LOGIN_PW"
  wait_for "HexaRouting VTY" 180 \
    "root's first login to land in the VTY" || return 1

  # Half two: what that session can actually do.
  #
  # `configure` and `commit`, with nothing typed in between. It is the smallest
  # thing an operator does and it is the whole defect: the candidate is built
  # at `configure` time out of what the box reads back, so a dataplane that is
  # not answering puts its error text in the candidate as configuration lines,
  # and the refusal lands on the FIRST REAL COMMIT of the session - by which
  # time the operator has typed a pool and has every reason to think the pool
  # is what was refused.
  echo "  -- the first session: configure and commit, with nothing typed"
  send "configure"
  expect_one_of "entering configuration mode" "the dataplane is not answering" \
    90 "the first session to enter configuration mode"
  case $? in
    0) echo "  [ ok ] the first session can enter configuration mode" ;;
    1) echo "  [FAIL] the VTY refused to build a candidate: the dataplane was"
       echo "         not answering at the prompt the box had just offered."
       echo "         The VTY is right to refuse - this is the getty half of"
       echo "         #211, and the ordering drop-in did not survive the"
       echo "         install."
       fail=1; return 1 ;;
    *) return 1 ;;
  esac

  send "commit"
  expect_one_of "no changes to commit" "does not understand" \
    300 "the first session's empty commit to be answered"
  case $? in
    0) echo "  [ ok ] an empty commit is answered with nothing to do" ;;
    1) echo "  [FAIL] the commit was refused for a line nobody typed"
       echo "         the candidate was built out of the dataplane's error"
       echo "         text and carries it as configuration. Every commit in"
       echo "         this session is refused from here on, including the"
       echo "         operator's first real one (#211)."
       fail=1; return 1 ;;
    *) return 1 ;;
  esac
  send "exit"
  wait_for "hexarouting> " 60 \
    "the first session to leave configuration mode" || return 1
  send "exit"
  wait_for "$NEW_HOST login:" 120 \
    "the first session to log out and the getty to come back" || return 1

  # ------------------------------------------ the roles, from here on as ever
  send "root"
  wait_for "Password:" 60 "login to ask for root's password" || return 1
  send "$LOGIN_PW"
  # The banner, not a prompt. It is the one string that proves the login
  # SUCCEEDED - and that root's login shell on a shipped box is this VTY. A
  # driver that waited for a prompt would type its next command at the login
  # prompt that follows a refusal and run the whole lab against it, which is
  # the single most likely way for this to go green proving nothing.
  wait_for "HexaRouting VTY" 180 "root's login to land in the VTY" || return 1
  echo "  -- root is in the VTY; the VTY creates the accounts and grants a role"

  send "configure"
  wait_for "entering configuration mode" 60 \
    "the VTY to enter configuration mode" || return 1
  send "user $OP_USER"
  wait_for "[edit user $OP_USER]" 30 "the user block to open" || return 1
  send "password $OP_HASH"
  send "role administrator"
  send "exit"
  wait_for "[edit]" 30 "the user block to close" || return 1
  send "user $RO_USER"
  wait_for "[edit user $RO_USER]" 30 \
    "the second user block to open" || return 1
  # No role at all: the half a lab that only grants things never checks. An
  # account with no role must be able to do nothing, or the boundary the box
  # reports is decoration.
  send "password $RO_HASH"
  send "exit"
  wait_for "[edit]" 30 "the second user block to close" || return 1
  send "commit"
  wait_for "commit complete" 300 "root's commit to apply the accounts" || return 1
  send "exit"
  wait_for "hexarouting> " 30 "the VTY to leave configuration mode" || return 1

  echo "  -- what the BOX says those accounts got, read off the box"
  send "start shell"
  wait_for "entering /bin" 60 "root's shell escape to open" || return 1
  send 'echo "HX""LAB whoami=$(id -un)"'
  wait_for "HXLAB whoami=root" 60 "the shell to answer" || return 1
  send 'echo "HX""LAB groups-op=$(id -nG '"$OP_USER"')"'
  wait_for "HXLAB groups-op=" 30 \
    "the box to list the administrator's memberships" || return 1
  send 'echo "HX""LAB groups-ro=$(id -nG '"$RO_USER"')"'
  wait_for "HXLAB groups-ro=" 30 \
    "the box to list the read-only account's memberships" || return 1
  send 'echo "HX""LAB shell-op=$(getent passwd '"$OP_USER"' | cut -d: -f7)"'
  wait_for "HXLAB shell-op=" 30 \
    "the box to say what the account logs into" || return 1
  send 'echo "HX""LAB modes=$(stat -c %a:%G /etc/hexarouting) $(stat -c %a:%G /etc/hexarouting/rollback)"'
  wait_for "HXLAB modes=" 30 \
    "the box to state the configuration directory's mode" || return 1
  # What systemd will do when this box is stopped, asked of SYSTEMD and not of
  # the drop-in file. In dynamic mode the block log is the connection record
  # the Marco Civil requires, and a block held when the dataplane stops leaves
  # an allocation open in it for ever (#217). The dataplane closes them when
  # asked, and what asks is an ExecStop in a vpp.service drop-in - so a drop-in
  # that was installed and that systemd parsed and refused reads exactly like
  # one that works. There is no systemd in a lab container; this is the only
  # place in the repository that has one.
  send 'echo "HX""LAB stopdrain=$(systemctl show vpp.service -p ExecStop 2>&1 | grep -c "bpa stop-drain")"'
  wait_for "HXLAB stopdrain=" 30 \
    "systemd to say what it runs before stopping the dataplane" || return 1

  # The bounds the control plane carries, asked of SYSTEMD rather than of the
  # unit file (#172). hexarouting.service ships MemoryMax=512M against a
  # measured 13 MB peak, and frr.service deliberately ships none because one
  # full IPv4 table costs FRR 1.37 GB and 37 MB on a box taking default routes
  # only - one number cannot serve both. Neither of those has ever been read
  # back on a box: a unit systemd parsed and REFUSED reports exactly like one
  # that took, and this is the only place in the repository with a real
  # systemd. Both halves are asserted, because the absence is as deliberate as
  # the presence and a MemoryMax that appeared on frr.service by accident
  # would be a five-second kill loop on the box with the most to lose.
  send 'echo "HX""LAB memmax=hx:$(systemctl show hexarouting.service -p MemoryMax --value) frr:$(systemctl show frr.service -p MemoryMax --value)"'
  wait_for "HXLAB memmax=" 30 \
    "systemd to say what memory bounds it applied" || return 1

  # And the CPU half, measured rather than assumed. isolcpus keeps the
  # scheduler from PLACING work on a worker's core; the open question is
  # whether it also narrows what a control-plane process is ALLOWED to ask
  # for, because if it does not then a slice with AllowedCPUs= buys something
  # isolcpus does not, and if it does then it buys nothing. bgpd's own mask is
  # one half; the other is whether taskset onto an isolated core is accepted.
  # Recorded, not asserted: what the kernel does with isolcpus is not this
  # repository's to change, and a check nothing here can make fail is not a
  # check. The reading is what the refusal in docs/appliance.md rests on.
  send 'echo "HX""LAB cpu-iso=$(cat /sys/devices/system/cpu/isolated) online=$(cat /sys/devices/system/cpu/online)"'
  wait_for "HXLAB cpu-iso=" 30 "the box to say which cores it isolates" || return 1
  send 'echo "HX""LAB cpu-bgpd=$(grep Cpus_allowed_list /proc/$(pidof bgpd)/status | tr -d " \t")"'
  wait_for "HXLAB cpu-bgpd=" 30 "the box to say where bgpd may run" || return 1
  send 'echo "HX""LAB cpu-escape=$(taskset -c $(cat /sys/devices/system/cpu/isolated) grep Cpus_allowed_list /proc/self/status | tr -d " \t")"'
  wait_for "HXLAB cpu-escape=" 30 \
    "the box to say whether an isolated core can be asked for" || return 1

  # Where the inherited mask comes from, and whether bgpd is still wearing it.
  #
  # Two runs of this lab on 2026-09-13 read cpu-bgpd as 0 and as 0-1 on boxes
  # that both reported isolcpus=1, so "the kernel already confines the control
  # plane" is a claim one reading supports and another refuses. These three
  # lines are what tells them apart and none of them was being read: pid 1's
  # own mask is what every service inherits, bgpd's parent and start time say
  # whether this bgpd is the one systemd started at boot or one frr-reload
  # started later from a different parent, and the isolcpus SPELLING decides
  # the behaviour - a bare list restricts pid 1's affinity and "domain," alone
  # does not.
  send 'echo "HX""LAB cpu-init=$(grep Cpus_allowed_list /proc/1/status | tr -d " \t")"'
  wait_for "HXLAB cpu-init=" 30 "the box to say what pid 1 is allowed" || return 1
  send 'echo "HX""LAB bgpd-from=pid:$(pidof bgpd) ppid:$(awk "{print \$4}" /proc/$(pidof bgpd)/stat) starttime:$(awk "{print \$22}" /proc/$(pidof bgpd)/stat)"'
  wait_for "HXLAB bgpd-from=" 30 "the box to say which bgpd this is" || return 1
  send 'echo "HX""LAB isolspell=$(tr " " "\n" < /proc/cmdline | grep isolcpus)"'
  wait_for "HXLAB isolspell=" 30 "the box to say how isolcpus is spelled" || return 1

  # The self-test again, now that the box has an account. At boot it said it
  # had none; this is the same program, on the same console, with the only
  # difference being the thing the VTY just did.
  echo "  -- the boot self-test, run again now that an account has a role"
  send 'echo "HX""LAB selftest-rerun"'
  wait_for "HXLAB selftest-rerun" 30 "the marker before the re-run" || return 1
  send "/usr/lib/hexarouting/selftest"
  wait_for "====================" 240 "the self-test to finish" || return 1

  send 'echo "HX""LAB section=op"'
  wait_for "HXLAB section=op" 30 "the marker that ends root's session" || return 1
  send "exit"
  wait_for "back in the HexaRouting VTY" 60 \
    "the shell escape to return to the VTY" || return 1
  send "exit"
  wait_for "$NEW_HOST login:" 120 \
    "the VTY to log root out and the getty to come back" || return 1

  # --------------------------------------------------- the account, as itself
  echo "  -- the account the VTY made logs in and uses the role it was given"
  send "$OP_USER"
  wait_for "Password:" 60 "login to ask for the account's password" || return 1
  send "$OP_PW"
  wait_for "HexaRouting VTY" 180 \
    "the account the VTY created to log in and land in the VTY" || return 1
  send "configure"
  wait_for "entering configuration mode" 60 \
    "an administrator to be let into configuration mode" || return 1
  # Routing, which is the half a cgnat-operator is refused and the half that
  # was broken for a non-root administrator: frr-reload.py writes its log into
  # a directory the frr package ships read-only to group frr, so this account
  # could `commit check` a change and not commit it (#186).
  send "router bgp 65000"
  wait_for "[edit router bgp 65000]" 30 "the BGP block to open" || return 1
  send "neighbor 10.0.0.2 remote-as 65001"
  send "neighbor 10.0.0.2 description NETADM-ON-AN-INSTALLED-BOX"
  send "exit"
  wait_for "[edit]" 30 "the BGP block to close" || return 1
  # And CGNAT, which goes over the BINARY API. That is the call that fails for
  # everybody but root unless the drop-in on vpp.service group-owns
  # /dev/shm/global_vm - a drop-in that runs under systemd, which no other lab
  # in this repository has (#76).
  send "cgnat pool lab-a"
  wait_for "[edit cgnat pool lab-a]" 30 "the pool block to open" || return 1
  send "inside 100.64.0.0/22"
  send "outside 198.51.100.64/30"
  send "exit"
  wait_for "[edit]" 30 "the pool block to close" || return 1
  send "commit"
  wait_for "commit complete" 300 \
    "an administrator's commit to be applied on the installed box" || return 1
  send "exit"
  wait_for "hexarouting> " 30 "the VTY to leave configuration mode" || return 1

  # Read back from FRR and from the dataplane, by the account itself. "commit
  # complete" is this VTY's own sentence; whether the routing daemon and the
  # dataplane took a change from an account that is not root is a question
  # only they can answer.
  send "start shell"
  wait_for "entering /bin" 60 "the administrator's shell escape to open" || return 1
  send 'echo "HX""LAB frr=$(vtysh -c "show run" | grep -c NETADM-ON-AN-INSTALLED-BOX)"'
  wait_for "HXLAB frr=" 60 "FRR to answer the account" || return 1
  send 'echo "HX""LAB pool=$(vppctl show bpa pool | grep -c lab-a)"'
  wait_for "HXLAB pool=" 60 "the dataplane to answer the account" || return 1
  # A commit whose rollback revision could not be written still says "commit
  # complete" and warns one line above it, so an operator whose changes could
  # not be undone went unnoticed until #188 was filed.
  send 'echo "HX""LAB rollback=$(ls /etc/hexarouting/rollback | wc -l) $(stat -c %a:%G /etc/hexarouting/rollback)"'
  wait_for "HXLAB rollback=" 30 "the rollback store to be listed" || return 1
  # WHO owns the newest revision, and not how many there are. root's commit
  # above wrote one, so a count proves nothing about the operator's - a store
  # holding somebody else's revision is exactly the box #188 describes, where
  # the operator's own change is the one that cannot be undone.
  send 'echo "HX""LAB newest-rev=$(stat -c %U $(ls -dt /etc/hexarouting/rollback/* | head -1))"'
  wait_for "HXLAB newest-rev=" 30 \
    "the box to say who owns the newest rollback revision" || return 1
  send 'echo "HX""LAB section=ro"'
  wait_for "HXLAB section=ro" 30 \
    "the marker that ends the administrator's session" || return 1
  send "exit"
  wait_for "back in the HexaRouting VTY" 60 \
    "the shell escape to return to the VTY" || return 1
  send "exit"
  wait_for "$NEW_HOST login:" 120 \
    "the getty to come back after the administrator logs out" || return 1

  # ------------------------------------------- the account with no role at all
  echo "  -- and the account with no role is held to it"
  send "$RO_USER"
  wait_for "Password:" 60 \
    "login to ask the read-only account for its password" || return 1
  send "$RO_PW"
  wait_for "HexaRouting VTY" 180 \
    "the read-only account to log in and land in the VTY" || return 1
  send "configure"
  wait_for "configure: permission denied for a read-only account" 60 \
    "the box to refuse configuration to an account with no role" || return 1
  send "start shell"
  wait_for "start shell: permission denied for a read-only account" 60 \
    "the box to refuse the shell escape to an account with no role" || return 1
  # The other half: a role that refuses everything is not a role, it is a
  # broken VTY. Reading needs the dataplane socket and FRR's, both group-owned
  # - so this is where the memberships every account gets, role or none, are
  # proved on an installed box.
  send "show running-config"
  # Rewound between the two, deliberately. They are two halves of ONE answer
  # and they arrive in an order this driver must not depend on: waiting for
  # whichever the VTY happens to print first would step the reader past the
  # other and time out on an answer that was already on the screen.
  _cfg_at=$CONSOLE_POS
  wait_for "router bgp 65000" 120 \
    "the read-only account to read the routing configuration back from FRR" \
    || return 1
  CONSOLE_POS=$_cfg_at
  wait_for "cgnat pool lab-a" 60 \
    "the read-only account to read the pool back from the dataplane" || return 1
  send "exit"
  wait_for "$NEW_HOST login:" 120 \
    "the getty to come back after the read-only account logs out" || return 1

  # ------------------- a 'commit confirmed' with nobody logged in to enforce it
  #
  # The one case the feature exists for, and the one it did not cover (#212).
  # The deadline used to live in the hexash process that made the commit, so
  # the operator logging out cancelled it - and the failure it guards against
  # is precisely the one that ends that session.
  #
  # Nothing below prompts the box. Between the logout and the box's own
  # sentence there is no hexash on this machine at all, which is the whole
  # point: a lab that had to go and trigger the revert would be performing the
  # step the product is missing.
  echo "  -- a commit nobody confirms, made by a session that then ends"
  send "root"
  wait_for "Password:" 60 "login to ask for root's password again" || return 1
  send "$LOGIN_PW"
  wait_for "HexaRouting VTY" 180 "root to land in the VTY again" || return 1
  send "start shell"
  wait_for "entering /bin" 60 "root's shell escape to open" || return 1
  send 'echo "HX""LAB section=confirm"'
  wait_for "HXLAB section=confirm" 30 \
    "the marker that separates this from the read-only session" || return 1
  send "exit"
  wait_for "back in the HexaRouting VTY" 60 \
    "the shell escape to return to the VTY" || return 1

  send "configure"
  wait_for "entering configuration mode" 60 \
    "the VTY to enter configuration mode" || return 1
  send "user $TMP_USER"
  wait_for "[edit user $TMP_USER]" 30 \
    "the block for the account nobody will confirm to open" || return 1
  send "password $TMP_HASH"
  send "role administrator"
  send "exit"
  wait_for "[edit]" 30 "that user block to close" || return 1
  # One minute, so the lab can wait it out. The mechanism is the same one a
  # ten-minute window uses; only the number differs.
  # Rewound after the wait below, deliberately, and for the reason the
  # read-only account's two halves are: the VTY prints "commit complete" and
  # what it promises about the revert in one go, so by the time a one-second
  # poll notices the first the second is already in the buffer - and wait_for
  # moves the reader past everything it has seen. Waiting for them in order
  # times out on a sentence that was on the screen before the wait started.
  _commit_at=$CONSOLE_POS
  send "commit confirmed 1"
  wait_for "commit complete" 300 "the confirmed commit to apply" || return 1
  CONSOLE_POS=$_commit_at
  # What the box PROMISES, in its own words, before anything here tests
  # whether it keeps it. The wording IS half the defect: the old one described
  # a revert that outlived the session over a product that implemented one
  # dying with it, so an operator reading it was less likely to keep a second
  # session open.
  #
  # Not a return, for the same reason the wait below is not: a box that makes
  # the wrong promise is a box this lab has to go on and catch breaking it.
  if wait_for "armed on the box" 30 \
       "the box to say where the revert it just promised lives"
  then
    CONFIRM_PROMISED=1
  else
    CONFIRM_PROMISED=0
    CONSOLE_POS=$(( $(wc -c < "$CONSOLE_LOG") + 1 ))
  fi
  send "exit"
  wait_for "hexarouting> " 30 "the VTY to leave configuration mode" || return 1
  send "start shell"
  wait_for "entering /bin" 60 "root's shell escape to open" || return 1
  # Out of /etc/passwd, not out of what the VTY said. The account has to be
  # there now, or "it is gone later" proves nothing.
  send 'echo "HX""LAB confirm-before=$(getent passwd '"$TMP_USER"' | cut -d: -f1)"'
  wait_for "HXLAB confirm-before=" 30 \
    "the box to say the account was really created" || return 1
  # And whether the deadline reached the DISK, asked while the window is open.
  # This is the whole of #212 in one stat: a deadline that exists only inside
  # the process that made the commit leaves nothing here, and the assertion
  # about the account being gone later would otherwise be the first place that
  # shows.
  send 'echo "HX""LAB confirm-armed=$(ls /etc/hexarouting/confirm-pending 2>/dev/null | wc -l)"'
  wait_for "HXLAB confirm-armed=" 30 \
    "the box to say whether the deadline reached the disk" || return 1
  send "exit"
  wait_for "back in the HexaRouting VTY" 60 \
    "the shell escape to return to the VTY" || return 1
  send "exit"
  wait_for "$NEW_HOST login:" 120 \
    "the VTY to log root out, ending the session that made the commit" || return 1

  # From here nothing is typed. The box is alone with a change nobody
  # confirmed, and it either says this on its own or it does not.
  echo "  -- nobody is logged in; waiting for the box to act on its own"
  if wait_for "put the previous configuration back" 180 \
       "the box to roll back a commit nobody confirmed, with nobody logged in"
  then
    CONFIRM_ANNOUNCED=1
  else
    # NOT a return. The console driver has not failed - the product has, and
    # the assertion that says so needs the session that comes next. Returning
    # here would report this as "nothing was proved" and take every other
    # assertion in this lab down with it.
    CONFIRM_ANNOUNCED=0
    CONSOLE_POS=$(( $(wc -c < "$CONSOLE_LOG") + 1 ))
  fi

  send ""
  wait_for "$NEW_HOST login:" 120 \
    "the getty to draw a login prompt after the wait" || return 1
  send "root"
  wait_for "Password:" 60 "login to ask for root's password" || return 1
  send "$LOGIN_PW"
  wait_for "HexaRouting VTY" 180 \
    "root to log back in after the window has passed" || return 1
  send "start shell"
  wait_for "entering /bin" 60 "root's shell escape to open" || return 1
  send 'echo "HX""LAB confirm-after=$(getent passwd '"$TMP_USER"' | cut -d: -f1)"'
  wait_for "HXLAB confirm-after=" 30 \
    "the box to say whether the account is still there" || return 1
  # What systemd thinks it is running, asked of systemd. A unit that was
  # installed and never enabled is indistinguishable from one that works, on
  # a feature nobody notices working.
  send 'echo "HX""LAB confirm-unit=$(systemctl is-enabled hexarouting-confirm.timer 2>&1) $(systemctl is-active hexarouting-confirm.timer 2>&1)"'
  wait_for "HXLAB confirm-unit=" 30 \
    "systemd to say whether the timer is enabled and running" || return 1
  send 'echo "HX""LAB confirm-left=$(ls /etc/hexarouting/confirm-pending 2>/dev/null | wc -l)"'
  wait_for "HXLAB confirm-left=" 30 \
    "the box to say whether anything is still pending" || return 1
  return 0
}

# BOOT_SECS is the ceiling on the whole driven session now - the boot, five
# logins, a re-run of the self-test and one minute in which nobody is logged
# in at all - and not a schedule. Every wait above has its own timeout and the
# machine is killed the moment the last answer is in.
#
# 1200 and not 900: the window a "commit confirmed 1" opens has to be waited
# out with no session on the box, and shortening it would test something else.
# A minute of that is the product's own deadline and the rest is two more
# logins around it.
timeout "${BOOT_SECS:-1200}" qemu-system-x86_64 \
  $ACCEL -m "${DISK_BOOT_MEM:-8192}" -smp 2 -nographic -no-reboot \
  -drive file="$DISK",format=qcow2,if=virtio \
  < "$CONSOLE_IN" > "$CONSOLE_LOG" 2>&1 &
QEMU_PID=$!
# Held open for the whole session. A writer that opens the pipe for each line
# and closes it hands qemu an end-of-file on the serial console in between,
# and the box stops reading.
exec 3> "$CONSOLE_IN"

CONSOLE_DRIVEN=1
# 0 until the box says, unprompted, that it put the configuration back. It is
# set here so that a driver which never reaches that point reports a silence
# rather than an unset variable.
CONFIRM_ANNOUNCED=0
# 0 until the box states, at the moment of the commit, where the revert it is
# promising actually lives.
CONFIRM_PROMISED=0
if ! drive_console; then
  CONSOLE_DRIVEN=0
  fail=1
fi

# The box has answered everything it is going to be asked. Killing it is not a
# shutdown test - nothing here asserts anything about how it goes down - and
# waiting out BOOT_SECS for a machine that has finished costs ten minutes of
# every run. The pause is so that the last thing it said reaches the log.
exec 3>&-
sleep 2
kill "$QEMU_PID" 2>/dev/null || true
sleep 3
pkill -9 -f qemu-system-x86_64 2>/dev/null || true
wait "$QEMU_PID" 2>/dev/null || true

# \r as well as the colours, now that assertions anchor on the start of a line.
sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' /tmp/disk.log | tr -d '\r' > /tmp/disk-clean.log
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

# The connection records must be on their OWN filesystem (#83).
#
# The assertion is not that a partition exists - it is that the records and the
# root are DIFFERENT filesystems, which is the property that matters: a log
# that fills must not take down the box that is writing it.
if [ "$up" = 1 ]; then
  if grep -q "records on their own filesystem" /tmp/disk-clean.log; then
    echo "  [ ok ] connection records are on their own filesystem"
    grep -o "records on their own filesystem.*" /tmp/disk-clean.log | head -1 \
      | sed 's/^/         /'
  else
    echo "  [FAIL] connection records share a filesystem with the root"
    grep -A2 "records are on the ROOT" /tmp/disk-clean.log | head -3 \
      | sed 's/^/         /'
    fail=1
  fi
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
# The binary API, asserted on the INSTALLED box (#119).
#
# hexash creates a pool over that API. The ISO never carried the vat2 plugin
# that registers it, so an installed appliance could route and answer and never
# be configured to do CGNAT at all. Ten labs hid this by placing the plugin by
# hand, describing that as "what install.sh does on a real box".
#
# Asserted here, on the disk that was actually installed, for the same reason
# the routing daemons are.
if [ "$up" = 1 ]; then
  if grep -q "binary API registered" /tmp/disk-clean.log; then
    echo "  [ ok ] the installed box can configure a pool"
  else
    echo "  [FAIL] the installed box cannot configure a pool: the binary API"
    echo "         is not registered"
    grep -A2 "binary API NOT registered" /tmp/disk-clean.log | head -3 \
      | sed 's/^/         /'
    fail=1
  fi
fi

# Roles are only real if somebody other than root can reach the dataplane (#76).
#
# The appliance ships a drop-in on vpp.service that group-owns
# /dev/shm/global_vm, because "api-segment { gid vpp }" covers /dev/shm/vpe-api
# and not that one, and a client maps both. lab/vty.sh has been doing this by
# hand since roles were built - which is the same shape as #119, a lab
# arranging an environment the product never produced.
#
# Asserted here because the drop-in runs under systemd, and the labs start VPP
# directly: this is the only place the unit file is exercised at all.
if [ "$up" = 1 ]; then
  # What this can and cannot assert, stated rather than implied.
  #
  # It used to grep for "binary API works for a non-root account", a line the
  # self-test printed after SYNTHESISING the group with setpriv. #184 replaced
  # that with a check on the box's real accounts - and this assertion then
  # failed on a correct box, because a freshly installed one has no operator
  # account at all. The release gate refused two tags for it before the cause
  # was read out of the console rather than guessed at.
  #
  # Both halves are asserted now, and they are told apart by WHEN the
  # self-test said them: the one at boot is a box nobody has configured, the
  # one after the marker is the same program on the same box after the VTY
  # gave an account a role. A single grep over the whole console would find
  # either and could not say which - and "no accounts" being mistaken for "the
  # accounts are fine" is the entire reason that line exists.
  BOOT_ST=/tmp/selftest-boot.log
  RERUN_ST=/tmp/selftest-rerun.log
  sed -n '1,/^HXLAB selftest-rerun/p' /tmp/disk-clean.log > "$BOOT_ST"
  sed -n '/^HXLAB selftest-rerun/,$p' /tmp/disk-clean.log > "$RERUN_ST"
  if grep -q "no account on this box is in group vpp" /tmp/disk-clean.log \
     || grep -q "the binary API does not work for" /tmp/disk-clean.log; then
    echo "  [FAIL] the binary API is root-only on the installed box: every"
    echo "         operator would have to be root and roles would be decorative"
    grep -A2 "in group vpp\|does not work for" /tmp/disk-clean.log | head -3 \
      | sed 's/^/         /'
    fail=1
  else
    if grep -q "no operator account exists yet" "$BOOT_ST"; then
      echo "  [ ok ] a fresh box says it has no operator account yet, rather"
      echo "         than reporting reach nobody has"
    else
      echo "  [FAIL] the box that had just been installed said nothing about"
      echo "         having no operator account: a box with no accounts and a"
      echo "         box whose accounts are fine would read the same"
      fail=1
    fi
    # WHICH account it names, rather than that it named one. The self-test
    # proves the API with the first account it finds in group vpp, and the
    # only accounts on this box are the two the VTY made a minute ago - so
    # naming anything else would mean it found reach somewhere this lab did
    # not put it.
    API_FOR=$(sed -n 's/.*the binary API works for \([^,]*\),.*/\1/p' \
      "$RERUN_ST" | head -1)
    if [ "$API_FOR" = "$OP_USER" ] || [ "$API_FOR" = "$RO_USER" ]; then
      echo "  [ ok ] and after the VTY gave an account a role, the same"
      echo "         self-test names one of the accounts it made:"
      grep -o "the binary API works for.*" "$RERUN_ST" | head -1 \
        | sed 's/^/         /'
    elif [ "$CONSOLE_DRIVEN" = 0 ]; then
      echo "  [FAIL] and the self-test was never re-run: the driver did not"
      echo "         get as far as giving an account a role"
    else
      echo "  [FAIL] the self-test still does not name an account that reaches"
      echo "         the binary API, after the VTY created one and gave it a"
      echo "         role on this box. Either the account did not get the"
      echo "         membership, or the membership does not reach the API:"
      grep -E "no operator account|in group vpp|does not work for|\[warn\] $OP_USER" \
        "$RERUN_ST" | head -3 | sed 's/^/         /'
      fail=1
    fi
  fi
fi

# --- the role, on a box somebody receives (#189) ---
#
# Every answer below was read OFF the installed box - out of /etc/group, out of
# FRR, out of the dataplane - and not out of what the VTY printed, which is the
# lesson #161 cost: a VTY whose belief and the box's answer never met is
# exactly what #184 was.
#
# The accounts were created BY THE PRODUCT, at the console, through the VTY.
# There is no useradd, no usermod and no writing to the disk image anywhere in
# this lab. A lab that performs the product's missing step cannot notice the
# product missing it.
echo
echo "=========== THE ROLE, ON THE BOX THAT WAS JUST INSTALLED ==========="
if [ "$CONSOLE_DRIVEN" = 0 ]; then
  echo "  [FAIL] nothing about roles was proved on this box: the console"
  echo "         driver did not finish its session, and every assertion"
  echo "         below it went untested rather than passing"
  fail=1
else
  # Three sessions in one console log, told apart by a marker each one printed
  # before it logged out. Without this a refusal printed by one account would
  # satisfy an assertion about another, which is the console equivalent of
  # asserting on what the VTY printed.
  sed -n '/^HXLAB section=op/,/^HXLAB section=ro/p' /tmp/disk-clean.log \
    > /tmp/sec-op.log
  sed -n '/^HXLAB section=ro/,/^HXLAB section=confirm/p' /tmp/disk-clean.log \
    > /tmp/sec-ro.log
  field() { sed -n "s/^HXLAB $1=//p" /tmp/disk-clean.log | head -1; }
  OP_GROUPS=" $(field groups-op) "
  RO_GROUPS=" $(field groups-ro) "
  OP_SHELL=$(field shell-op)
  CONF_MODES=$(field modes)
  FRR_HITS=$(field frr)
  POOL_HITS=$(field pool)
  RB_COUNT=$(field rollback | awk '{print $1}')
  RB_MODE=$(field rollback | awk '{print $2}')
  NEWEST_REV=$(field newest-rev)
  has() { case "$2" in *" $1 "*) return 0 ;; *) return 1 ;; esac; }
  echo "  $OP_USER (role administrator) is in:$OP_GROUPS"
  echo "  $RO_USER (no role) is in:$RO_GROUPS"

  # The role groups. hexa-admin is additive on top of hexa-cgnat, because the
  # configuration directory and the rollback store are group hexa-cgnat and an
  # administrator that cannot write them cannot record the revision for the
  # change it just made.
  for g in hexa-admin hexa-cgnat; do
    if has "$g" "$OP_GROUPS"; then
      echo "  [ ok ] the account the VTY made is in $g"
    else
      echo "  [FAIL] the account the VTY made is NOT in $g: it was granted a"
      echo "         role the box will not honour, and every command it issues"
      echo "         will be refused as though it were an authorization"
      fail=1
    fi
    if has "$g" "$RO_GROUPS"; then
      echo "  [FAIL] the account with NO role is in $g: the box grants rights"
      echo "         nobody asked it to grant, and the boundary the VTY reports"
      echo "         is decoration"
      fail=1
    fi
  done
  # The reach every account needs before authorization is even the question.
  # VPP creates its sockets srwxrwxr-x root:vpp and /etc/frr is drwxr-x---
  # frr:frr, so an account outside these is refused by the SOCKET on every
  # command, the read-only ones included.
  for g in vpp frr frrvty; do
    if has "$g" "$OP_GROUPS" && has "$g" "$RO_GROUPS"; then
      echo "  [ ok ] both accounts reach $g, role or none"
    else
      echo "  [FAIL] an account the VTY created is outside $g, so it cannot"
      echo "         open the socket its role is about - which reads as an"
      echo "         authorization refusal and is not one"
      fail=1
    fi
  done
  if [ "$OP_SHELL" = /usr/bin/hexash-login ]; then
    echo "  [ ok ] and it logs into the VTY, not into a shell"
  else
    echo "  [FAIL] the account's login shell is '$OP_SHELL', so the box hands"
    echo "         whoever logs in a shell instead of the VTY"
    fail=1
  fi

  # #188, on an installed box: the installer states these modes and the VTY
  # must not narrow them. The first commit here was ROOT's, which is the case
  # that broke every later one.
  if [ "$CONF_MODES" = "2770:hexa-cgnat 2770:hexa-cgnat" ]; then
    echo "  [ ok ] root's commit left the configuration directory and the"
    echo "         rollback store 2770 group hexa-cgnat"
  else
    echo "  [FAIL] after root's commit the configuration directory and the"
    echo "         rollback store are '$CONF_MODES', not '2770:hexa-cgnat"
    echo "         2770:hexa-cgnat': every non-root operation on them fails"
    fail=1
  fi

  # The account, as itself. "commit complete" is the VTY's own sentence; these
  # two are the daemons' answers.
  if [ "${FRR_HITS:-0}" -ge 1 ] 2>/dev/null; then
    echo "  [ ok ] FRR holds the routing statement that account committed"
  else
    echo "  [FAIL] FRR holds nothing from that account: an administrator that"
    echo "         can enter a routing change and not apply it is #186, found"
    echo "         in the maintenance window after the check has said yes"
    grep -E "permission denied|frr-reload|reached FRR" /tmp/sec-op.log 2>/dev/null \
      | head -3 | sed 's/^/         /'
    fail=1
  fi
  if [ "${POOL_HITS:-0}" -ge 1 ] 2>/dev/null; then
    echo "  [ ok ] and the dataplane holds the pool it created over the"
    echo "         binary API, which is the call that is root-only unless the"
    echo "         drop-in on vpp.service ran"
  else
    echo "  [FAIL] the dataplane holds no pool from that account: on an"
    echo "         installed box the binary API is reached through a drop-in"
    echo "         that only systemd runs, and this is the only lab that has one"
    fail=1
  fi
  if [ "$NEWEST_REV" = "$OP_USER" ] && [ "$RB_MODE" = 2770:hexa-cgnat ] \
     && ! grep -q "no rollback revision could be recorded" /tmp/sec-op.log; then
    echo "  [ ok ] and the commit it made can be undone: the newest of the"
    echo "         $RB_COUNT revision(s) belongs to $NEWEST_REV, in a store"
    echo "         that is still $RB_MODE"
  else
    echo "  [FAIL] that account's commit did not record a rollback revision of"
    echo "         its own: the newest belongs to '$NEWEST_REV', not to"
    echo "         $OP_USER, and the store is '$RB_MODE'. The commit still says"
    echo "         'commit complete' and warns one line above it, so an"
    echo "         operator whose changes cannot be undone does not find out"
    grep "no rollback revision could be recorded" /tmp/sec-op.log | head -1 \
      | sed 's/^/         /'
    fail=1
  fi

  # The negative half. A role that refuses nothing is not a role.
  if grep -q "configure: permission denied for a read-only account" \
       /tmp/sec-ro.log 2>/dev/null; then
    echo "  [ ok ] the account with no role is refused configuration mode"
  else
    echo "  [FAIL] an account the VTY gave no role was let into configuration"
    echo "         mode on the installed box"
    fail=1
  fi
  if grep -q "start shell: permission denied for a read-only account" \
       /tmp/sec-ro.log 2>/dev/null; then
    echo "  [ ok ] and refused the shell escape"
  else
    echo "  [FAIL] an account with no role reached a root shell through the"
    echo "         VTY, which is every boundary above it at once"
    fail=1
  fi
  # And can still read, through the two sockets it is in the groups for.
  if grep -q "router bgp 65000" /tmp/sec-ro.log 2>/dev/null \
     && grep -q "cgnat pool lab-a" /tmp/sec-ro.log 2>/dev/null; then
    echo "  [ ok ] and can still read the box: the routing half from FRR and"
    echo "         the CGNAT half from the dataplane"
  else
    echo "  [FAIL] the account with no role cannot read the box either, so it"
    echo "         is not read-only - it is locked out, and the operator will"
    echo "         conclude the box is broken"
    fail=1
  fi

  # --- the revert that has to outlive the session that armed it (#212) ---
  #
  # Every answer here was read out of /etc/passwd and out of systemd, on the
  # installed box, in a session that did not exist when the commit was made.
  echo
  echo "  --- 'commit confirmed' with nobody logged in ---"
  CONF_BEFORE=$(field confirm-before)
  CONF_AFTER=$(field confirm-after)
  CONF_UNIT=$(field confirm-unit)
  CONF_ARMED=$(field confirm-armed)
  CONF_LEFT=$(field confirm-left)
  if [ "$CONF_BEFORE" = "$TMP_USER" ]; then
    echo "  [ ok ] the confirmed commit really applied: $TMP_USER is in"
    echo "         /etc/passwd on this box"
  else
    echo "  [FAIL] the commit did not create $TMP_USER at all, so nothing"
    echo "         below is about a revert - it is about a commit that did"
    echo "         not happen. /etc/passwd said: '${CONF_BEFORE:-<nothing>}'"
    fail=1
  fi
  if [ "${CONF_ARMED:-0}" = 1 ]; then
    echo "  [ ok ] and the deadline reached the disk: while the window was"
    echo "         open, /etc/hexarouting/confirm-pending was on this box"
  else
    echo "  [FAIL] nothing was written to /etc/hexarouting/confirm-pending"
    echo "         while the window was open, so the deadline exists only"
    echo "         inside the hexash process that made the commit - and that"
    echo "         process is what the change this guards against destroys"
    fail=1
  fi
  if [ "$CONFIRM_PROMISED" = 1 ]; then
    echo "  [ ok ] and it said where the revert lives, at the moment it made"
    echo "         the promise:"
    grep -o "the revert is armed on the box.*" /tmp/disk-clean.log | head -1 \
      | sed 's/^/         /'
  else
    echo "  [FAIL] the box never said where the revert it promised lives. The"
    echo "         wording is half of #212: a message describing a revert that"
    echo "         outlives the session, over a product whose revert dies with"
    echo "         it, makes an operator LESS likely to keep a second session"
    echo "         open. What it said instead:"
    grep -o "rollback .*confirm.*" /tmp/disk-clean.log | head -2 \
      | sed 's/^/         /'
    fail=1
  fi
  if [ "$CONFIRM_ANNOUNCED" = 1 ]; then
    echo "  [ ok ] and the box said, on a console nobody was logged into,"
    echo "         that it had put the previous configuration back:"
    grep -o "nobody confirmed it within.*" /tmp/disk-clean.log | head -1 \
      | sed 's/^/         /'
  else
    echo "  [FAIL] with nobody logged in, this box said NOTHING and did"
    echo "         nothing. That is #212: the deadline lived in the hexash"
    echo "         process that made the commit, so logging out cancelled it"
    echo "         - and the change it guards against is the one that ends"
    echo "         that session. It reverts in every case where it is not"
    echo "         needed and in none of the cases where it is."
    fail=1
  fi
  if [ -z "$CONF_AFTER" ]; then
    echo "  [ ok ] and the account is gone from the box that was logged back"
    echo "         into: the revert outlived the session that armed it"
  else
    echo "  [FAIL] '$CONF_AFTER' is still in /etc/passwd after the window"
    echo "         passed with nobody logged in. An operator was told this"
    echo "         box would come back on its own and it did not - which is"
    echo "         worse than not having the feature, because they are less"
    echo "         likely to have kept a second session open"
    fail=1
  fi
  case "$CONF_UNIT" in
    "enabled active")
      echo "  [ ok ] and systemd is what enforces it: hexarouting-confirm.timer"
      echo "         is enabled and active on this box" ;;
    *)
      echo "  [FAIL] hexarouting-confirm.timer is '${CONF_UNIT:-<no answer>}',"
      echo "         not 'enabled active'. A unit that was installed and never"
      echo "         enabled reads exactly like one that works, on a feature"
      echo "         whose whole job is to do nothing most of the time"
      fail=1 ;;
  esac
  if [ "${CONF_LEFT:-1}" = 0 ]; then
    echo "  [ ok ] and nothing is left pending, so the next boot reverts"
    echo "         nothing a second time"
  else
    echo "  [FAIL] /etc/hexarouting/confirm-pending is still on the box after"
    echo "         the revert ran, so the box would do it again at every boot"
    fail=1
  fi

  # --- the retention log is closed when the box stops (#217) ---
  echo
  echo "  --- what systemd runs before it stops the dataplane ---"
  STOP_DRAIN=$(field stopdrain)
  if [ "${STOP_DRAIN:-0}" -ge 1 ] 2>/dev/null; then
    echo "  [ ok ] vpp.service carries the drain in ExecStop, so a planned"
    echo "         reboot or an upgrade closes every block this box holds"
    echo "         instead of leaving the allocations open in the retention"
    echo "         log for as long as it is kept"
  else
    echo "  [FAIL] systemd has no 'bpa stop-drain' in vpp.service's ExecStop"
    echo "         (grep counted '${STOP_DRAIN:-<no answer>}'). Every planned"
    echo "         reboot and every upgrade then leaves one open allocation"
    echo "         per block held, and the box answers a court order with a"
    echo "         subscriber who had already been disconnected (#217)"
    fail=1
  fi
fi

echo
# Which NIC the dataplane was allowed to claim (#123).
#
# An installed box used to generate a DPDK stanza with no device allowlist,
# because the shipped HEXA_DATAPLANE_IF named an interface that does not exist
# under predictable names. VPP then took whatever it found - the management
# interface - and the box left the network the moment the dataplane started.
#
# Either it names a device or it claims none. An open probe is neither.
if [ "$up" = 1 ]; then
  if grep -q "no dataplane interface" /tmp/disk-clean.log \
     || grep -q "dataplane NIC: 0000:" /tmp/disk-clean.log \
     || grep -q "DPDK is off until hugepages exist" /tmp/disk-clean.log; then
    echo "  [ ok ] the dataplane claims a named NIC, or none at all"
  else
    echo "  [FAIL] the dataplane may claim any NIC it finds, including the"
    echo "         management interface this box is reached over"
    grep -A2 "may claim any NIC" /tmp/disk-clean.log | head -3 \
      | sed 's/^/         /'
    fail=1
  fi
fi

# Is the dataplane out of the OOM killer's selection on the INSTALLED box?
#
# The protection is a drop-in on vpp.service, and a drop-in that was never
# installed - or that systemd parsed and refused - is indistinguishable from
# one that is working. lab/oom.sh shows the kernel making that choice both
# ways, but it has to apply the value itself: there is no systemd in a lab
# container. Whether the unit file arrived and took effect can only be seen
# where there is one, and this is the only place in the repository that boots
# a real installed box under real systemd (#172).
#
# What is read back is the self-test's line, which it prints from
# /proc/<vpp pid>/oom_score_adj and not from the drop-in file.
if [ "$up" = 1 ]; then
  if grep -q "out of the OOM killer's selection" /tmp/disk-clean.log; then
    echo "  [ ok ] the installed dataplane is out of the OOM killer's selection"
    grep -o "dataplane out of the OOM killer's selection.*" /tmp/disk-clean.log \
      | head -1 | sed 's/^/         /'
  else
    echo "  [FAIL] the drop-in did not take effect on the installed box: the"
    echo "         dataplane can still be chosen as the victim of somebody"
    echo "         else's allocation, which is how it was killed at 19 MB"
    grep -iE "oom_score_adj|vpp.service owns no process" /tmp/disk-clean.log \
      | head -3 | sed 's/^/         /'
    fail=1
  fi
fi

# Are the dataplane's workers on cores the kernel is actually isolating, and
# what is left over for the control plane? (#172)
#
# "full tuning active: corelist-workers ..." has been on this console since the
# tuner existed, and it reports the FILE. The lines read back here report what
# the dataplane GOT: the self-test compares /etc/vpp/startup.conf against
# /proc/<vpp pid>/task/*/status and /sys/devices/system/cpu/isolated, three
# places and none of them the one that made the decision. test_selftest.sh
# shows every one of those states going red against a synthetic root; this is
# the reading with nothing synthetic, on a box that really booted with
# isolcpus on its command line - which the live ISO never does.
if [ "$up" = 1 ]; then
  if grep -q "dataplane workers hold the cores the profile names" /tmp/disk-clean.log \
     && grep -q "the kernel is isolating every one of them" /tmp/disk-clean.log; then
    echo "  [ ok ] the installed dataplane holds the cores its profile names,"
    echo "         and the kernel is isolating them"
    grep -oE "(dataplane workers hold|and the kernel is isolating).*" /tmp/disk-clean.log \
      | head -2 | sed 's/^/         /'
  else
    echo "  [FAIL] the installed box did not confirm its worker cores: either a"
    echo "         worker is on a core the kernel is not isolating, or the"
    echo "         dataplane is older than the profile it is judged against."
    echo "         Both forward and both cost throughput silently"
    grep -iE "worker core|pins workers|owns no process" /tmp/disk-clean.log \
      | head -3 | sed 's/^/         /'
    fail=1
  fi
  if grep -qE "control plane has [0-9]+ cpu|control plane has ONE cpu" /tmp/disk-clean.log; then
    echo "  [ ok ] and it says what the control plane is left with"
    grep -oE "control plane has .*" /tmp/disk-clean.log | head -1 | sed 's/^/         /'
  else
    echo "  [FAIL] the installed box never said how many cpus the control plane"
    echo "         has. That number is the whole of what this product can"
    echo "         honestly say about CPU bounds, and it said nothing"
    fail=1
  fi
fi

# The memory bound the control plane carries, read back from systemd (#172).
#
# hexarouting.service ships MemoryMax=512M against a measured 13 MB peak and
# frr.service ships none against a measured 1.37 GB full table. Both halves are
# asserted: a MemoryMax that arrived on frr.service would turn a slow box into
# a five-second restart loop, and one that systemd parsed and refused on
# hexarouting.service reads exactly like one that took.
mm=$(sed -n 's/^HXLAB memmax=//p' /tmp/disk-clean.log | head -1 | tr -d '\r')
case "$mm" in
  "") if [ "$up" = 1 ]; then
        echo "  [FAIL] systemd was never asked what memory bounds it applied"
        fail=1
      fi ;;
  *"hx:536870912"*"frr:infinity"*)
    echo "  [ ok ] systemd applied MemoryMax to the control plane and not to FRR"
    echo "         $mm" ;;
  *)
    echo "  [FAIL] the memory bounds systemd applied are not the shipped ones:"
    echo "         $mm"
    echo "         expected hx:536870912 (512M, hexarouting.service) and"
    echo "         frr:infinity (deliberately unbounded - one full table costs"
    echo "         FRR 1.37 GB and a bound guessed low kills rather than"
    echo "         degrades; docs/appliance.md carries the measurement)"
    fail=1 ;;
esac

# What isolcpus does and does not do to the control plane, recorded.
#
# Not a pass or a fail: this is the kernel's behaviour, not this repository's,
# and a check nothing here can make fail is not a check. It is printed because
# it is the evidence behind the decision in docs/appliance.md not to ship a
# cpuset for the control plane.
for k in cpu-iso cpu-bgpd cpu-escape cpu-init bgpd-from isolspell; do
  v=$(sed -n "s/^HXLAB $k=//p" /tmp/disk-clean.log | head -1 | tr -d '\r')
  [ -n "$v" ] && echo "  [meas] $k: $v"
done

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

