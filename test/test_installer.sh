#!/bin/sh
# The installer's questions, without a virtual machine.
#
# The install itself is proved by lab/install-iso.sh, which puts the ISO on a
# blank disk and boots it. What that lab cannot cheaply cover is the answers:
# every refusal here is a case where the installer must stop BEFORE it
# partitions anything, and each one costs a full boot cycle to reach in a VM.
#
# HEXA_INSTALL_CHECK=1 collects and validates and writes nothing, which is why
# this runs as an ordinary user in a couple of milliseconds.
INSTALLER=$(cd "$(dirname "$0")/.." && pwd)/appliance/hexarouting-install
fail=0

# A real block device, because check mode deliberately does not relax that:
# a check more permissive than the real run is a check that agrees with itself.
DISK=/dev/$(lsblk -dno NAME 2>/dev/null | head -1)
if [ ! -b "$DISK" ]; then
  echo "  SKIP: no block device on this machine to name as a target"
  exit 0
fi

run() {  # <expected exit 0|1> <description> <env assignments...>
  _want=$1; _desc=$2; shift 2
  out=$(env HEXA_INSTALL_CHECK=1 "$@" sh "$INSTALLER" </dev/null 2>&1)
  rc=$?
  if [ "$_want" = 0 ] && [ "$rc" != 0 ]; then
    echo "  FAIL: $_desc (expected acceptance, got $rc)"; echo "$out" | sed 's/^/        /'
    fail=$((fail + 1)); return
  fi
  if [ "$_want" != 0 ] && [ "$rc" = 0 ]; then
    echo "  FAIL: $_desc (expected refusal, it was accepted)"
    fail=$((fail + 1)); return
  fi
  echo "  [ ok ] $_desc"
  LAST_OUT=$out
}

OK="HEXA_DISK=$DISK HEXA_HOSTNAME=cgnat01 HEXA_TIMEZONE=America/Sao_Paulo"

echo "=== a complete set of answers is accepted ==="
# shellcheck disable=SC2086
run 0 "disk, hostname, timezone and password are enough" $OK \
    HEXA_ROOT_PASSWORD=s3cr3t
case "$LAST_OUT" in
  *"root password  set"*) echo "  [ ok ] the summary says the password was answered" ;;
  *) echo "  FAIL: the summary does not report the password"; fail=$((fail + 1)) ;;
esac
case "$LAST_OUT" in
  *cgnat01*America/Sao_Paulo*) echo "  [ ok ] the summary repeats the answers back" ;;
  *) echo "  FAIL: the summary does not repeat the answers"; fail=$((fail + 1)) ;;
esac
case "$LAST_OUT" in
  *"nothing was written"*) echo "  [ ok ] and it says it wrote nothing" ;;
  *) echo "  FAIL: check mode did not say it wrote nothing"; fail=$((fail + 1)) ;;
esac

echo
echo "=== the refusals, each one a disk that must stay untouched ==="
# The one that matters most. A first boot that never asks for a password is a
# first boot whose password was chosen by whoever built the image - which is
# the defect this whole change exists to remove.
# shellcheck disable=SC2086
run 1 "no root password: refused rather than defaulted" $OK
# stdin is closed above, so this also proves the empty-password retry loop
# terminates instead of spinning on "may not be empty".

run 1 "unknown timezone: refused" HEXA_DISK=$DISK HEXA_HOSTNAME=cgnat01 \
    HEXA_TIMEZONE=Mars/Olympus HEXA_ROOT_PASSWORD=s3cr3t
run 1 "hostname with characters a hostname may not have: refused" \
    HEXA_DISK=$DISK HEXA_HOSTNAME="cgnat 01!" \
    HEXA_TIMEZONE=America/Sao_Paulo HEXA_ROOT_PASSWORD=s3cr3t
run 1 "a target that is not a block device: refused" \
    HEXA_DISK=/tmp/not-a-disk HEXA_HOSTNAME=cgnat01 \
    HEXA_TIMEZONE=America/Sao_Paulo HEXA_ROOT_PASSWORD=s3cr3t

echo
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail + 1)); }

echo "=== the log volume: sizes the installer must refuse ==="
# The one question whose wrong answer is not destructive but is permanent: a
# box installed with the records on a 1 GB volume fills it in twelve days at
# the rate docs/logging.md measures, and the fix is a reinstall.
run 1 "a log size that is not a number" $OK HEXA_ROOT_PASSWORD=s3cr3t \
    HEXA_LOG_GB=lots
# The MESSAGE, not just the refusal. Without the shape check the number still
# gets refused - by the shell failing to compare "lots" against 1 - and the
# operator is shown an arithmetic error instead of what they typed wrong.
case "$LAST_OUT" in
  *"whole number of gigabytes"*) ok "and says what a size looks like" ;;
  *) bad "refused with a shell error rather than a reason: $LAST_OUT" ;;
esac
run 1 "a log size of zero" $OK HEXA_ROOT_PASSWORD=s3cr3t HEXA_LOG_GB=0
# 8 TB of records on any disk this test can see leaves the system nothing.
run 1 "a log that leaves the system no room" $OK HEXA_ROOT_PASSWORD=s3cr3t \
    HEXA_LOG_GB=8192

echo "=== the disk menu: a letter, or Enter for the largest ==="
#
# The one question in this program where a wrong answer destroys something.
# Driven against a FAKE lsblk, because the alternative is real block devices -
# and a test that needs those is a test that does not run.
#
fake=$(mktemp -d)
cat > "$fake/lsblk" <<'LSBLK'
#!/bin/sh
# -dnbo NAME,SIZE,TYPE,MODEL : the table disk_table() reads
# -dno SIZE / SIZE,MODEL     : the human size for one disk
# -ndo PKNAME <dev>          : the parent of a partition
for a in "$@"; do case "$a" in
  -dnbo) MODE=table ;;
  -ndo)  MODE=pkname ;;
esac; done
case "${MODE:-human}" in
  table)
    printf 'sda 34359738368 disk KINGSTON SA400
'
    printf 'sdb 1000204886016 disk WD Black 1TB
'
    printf 'sdc 8589934592 disk SanDisk Cruzer
'
    ;;
  pkname) printf '
' ;;
  human)
    # "-dno SIZE" is one column; "-dno SIZE,MODEL" is two. A fake that
    # answers both with two columns glues the model onto the size in the
    # menu - and a fake that renders wrong hides a real formatting bug.
    case "$*" in
      *SIZE,MODEL*) _two=1 ;;
      *) _two=0 ;;
    esac
    case "$*" in
      *sda*) _s=' 32G'; _m='KINGSTON SA400' ;;
      *sdb*) _s='932G'; _m='WD Black 1TB' ;;
      *sdc*) _s='  8G'; _m='SanDisk Cruzer' ;;
      *)     _s='?';    _m='?' ;;
    esac
    if [ "$_two" = 1 ]; then echo "$_s $_m"; else echo "$_s"; fi
    ;;
esac
LSBLK
cat > "$fake/findmnt" <<'FINDMNT'
#!/bin/sh
# The medium this system booted from: the 8 GB stick.
printf '/dev/sdc
'
FINDMNT
chmod +x "$fake/lsblk" "$fake/findmnt"

INST=$(cd "$(dirname "$0")/.." && pwd)/appliance/hexarouting-install
pick() {  # <answer on stdin> -> the chosen path
  printf '%s
' "$1" | (
    PATH="$fake:$PATH"
    HEXA_INSTALL_LIB=1
    export HEXA_INSTALL_LIB
    . "$INST"
    choose_disk 2>/dev/null
  )
}

out=$(pick "")
case "$out" in
  /dev/sdb) ok "Enter alone chose the largest disk: $out" ;;
  *) bad "Enter alone must choose the largest disk, got '$out'" ;;
esac

out=$(pick "a")
case "$out" in
  /dev/sdb) ok "'a' is the first line, which is the largest: $out" ;;
  *) bad "'a' must select the first line, got '$out'" ;;
esac

out=$(pick "b")
case "$out" in
  /dev/sda) ok "'b' selects the second: $out" ;;
  *) bad "'b' must select the second line, got '$out'" ;;
esac

out=$(pick "/dev/sda")
case "$out" in
  /dev/sda) ok "a device path is still accepted: $out" ;;
  *) bad "a device path must still work, got '$out'" ;;
esac

# The medium the system booted from must not be offered at all. Overwriting it
# fails halfway through and leaves neither system usable - and it is exactly
# the disk somebody in a hurry picks, because it is the one they just plugged
# in.
menu=$(printf '
' | (PATH="$fake:$PATH"; HEXA_INSTALL_LIB=1; export HEXA_INSTALL_LIB;
                      . "$INST"; choose_disk 2>&1 >/dev/null))
case "$menu" in
  *sdc*) bad "the boot medium was offered in the menu:
$menu" ;;
  *) ok "the boot medium is not in the menu" ;;
esac
case "$menu" in
  *"default, the largest"*) ok "the menu says which one is the default and why" ;;
  *) bad "the menu must mark the default" ;;
esac

# The size is its own column. This is here because the first version of the
# fake lsblk above answered "-dno SIZE" with two columns, which glued the
# model onto the size - and a menu whose columns run together is a menu an
# operator misreads at the one prompt where misreading costs a disk.
case "$menu" in
  *"932G  WD Black 1TB"*) ok "size and model are separate columns" ;;
  *) bad "the menu columns run together:
$menu" ;;
esac

# A letter nobody offered must not silently become a disk.
out=$(pick "z
b")
case "$out" in
  /dev/sda) ok "an unoffered letter re-asks rather than guessing: $out" ;;
  *) bad "'z' then 'b' must end up at the second disk, got '$out'" ;;
esac

rm -rf "$fake"

echo "=== 'install' exists only on the installation medium ==="
# A command that overwrites the disk must not be one TAB away from "interface"
# on a router carrying traffic. The VTY decides that from whether the box is
# running FROM the medium, so both answers are worth asserting.
REPO=$(cd "$(dirname "$0")/.." && pwd)
probe() {  # <squashfs path> <installer path>
  HEXA_LIVE_SQUASHFS="$1" HEXA_INSTALLER="$2" python3 - "$REPO" <<'PY'
import sys, os
sys.path.insert(0, os.path.join(sys.argv[1], "src", "hexash"))
import hexash
print("yes" if "install" in hexash.TREE["operational"] else "no")
PY
}
SQ=$(mktemp); IN=$(mktemp); chmod +x "$IN"
[ "$(probe "$SQ" "$IN")" = yes ]   && echo "  [ ok ] on the live medium the VTY offers 'install'"   || { echo "  FAIL: the installer is unreachable from the VTY on the medium"
       fail=$((fail + 1)); }
[ "$(probe /nonexistent/squashfs "$IN")" = no ]   && echo "  [ ok ] on an installed box it is not in the command tree"   || { echo "  FAIL: an installed box offers to overwrite itself"
       fail=$((fail + 1)); }
rm -f "$SQ" "$IN"

echo
echo "=== the boot medium is still refused when it is named outright ==="
# A standing behaviour, asserted rather than assumed. The menu already hides
# the medium (above), but a device path typed by hand or given by a script
# bypasses the menu entirely - and overwriting the medium being read fails
# halfway through and leaves neither system usable.
med=$(mktemp -d)
cat > "$med/findmnt" <<FINDMNT
#!/bin/sh
# /run/live/medium is on the disk this test names as the target.
printf '%s\n' "$DISK"
FINDMNT
chmod +x "$med/findmnt"
out=$(env PATH="$med:$PATH" HEXA_INSTALL_CHECK=1 HEXA_DISK="$DISK" \
      HEXA_HOSTNAME=cgnat01 HEXA_TIMEZONE=America/Sao_Paulo \
      HEXA_ROOT_PASSWORD=s3cr3t sh "$INSTALLER" </dev/null 2>&1)
if [ $? = 0 ]; then
  bad "the medium this system booted from was accepted as the target"
else
  case "$out" in
    *"medium this system booted from"*) ok "the boot medium named outright is refused, and says why" ;;
    *) bad "refused, but not for being the boot medium: $out" ;;
  esac
fi
rm -rf "$med"

echo
echo "=== answers from a file: the unattended path that publishes nothing ==="
#
# hexarouting.password= on the kernel command line is world-readable in
# /proc/cmdline for the life of the live medium. The file does the same job
# without that (#83), so what has to be asserted is not only that the answers
# arrive but that the secret does not come back out.
#
adir=$(mktemp -d)
SECRET=s3cr3t-from-the-file
cat > "$adir/answers" <<ANS
# HexaRouting install answers
hostname = cgnat-file
timezone = America/Sao_Paulo
log-gb   = 2
password = $SECRET
ANS

run 0 "a file answers every question, the password among them" \
    HEXA_DISK="$DISK" HEXA_ANSWERS="$adir/answers"
case "$LAST_OUT" in
  *cgnat-file*) ok "the answers in the file reached the summary" ;;
  *) bad "the file's hostname never arrived: $LAST_OUT" ;;
esac
# THE assertion of this section. Everything else about the answer file can work
# and it is still the wrong feature if the password comes back out on a console
# that is logged, or in an error message nobody thought to redact.
case "$LAST_OUT" in
  *"$SECRET"*) bad "the installer printed the root password it was given" ;;
  *) ok "and the password itself was never printed" ;;
esac

# What is typed for THIS boot beats what travels with the medium for every
# install it does - otherwise a medium could not be reused for a second box
# without rewriting it.
run 0 "an answer given on the command line overrides the file" \
    HEXA_DISK="$DISK" HEXA_ANSWERS="$adir/answers" HEXA_HOSTNAME=cgnat-typed
case "$LAST_OUT" in
  *cgnat-typed*) ok "the typed hostname won" ;;
  *) bad "the file overrode an answer that was already given: $LAST_OUT" ;;
esac

# A misspelt key is an install that arrives at a question with no answer, on a
# machine with no console to answer from: it would present as a hang, hours
# later, and not as a typo.
printf 'passwrod = %s\n' "$SECRET" > "$adir/typo"
run 1 "a key the installer does not know: refused, not ignored" \
    HEXA_DISK="$DISK" HEXA_HOSTNAME=cgnat01 \
    HEXA_TIMEZONE=America/Sao_Paulo HEXA_ANSWERS="$adir/typo"
case "$LAST_OUT" in
  *"'passwrod' is not an answer"*) ok "and it names the key it could not use" ;;
  *) bad "refused without saying which key was wrong: $LAST_OUT" ;;
esac
case "$LAST_OUT" in
  *"$SECRET"*) bad "the error message quoted the line, secret and all" ;;
  *) ok "and the refusal did not quote the value back" ;;
esac

# A file that is not there must stop the install. Falling through to the
# prompts is the same hang by another route.
run 1 "an answer file that is not on the medium: refused" \
    HEXA_DISK="$DISK" HEXA_HOSTNAME=cgnat01 \
    HEXA_TIMEZONE=America/Sao_Paulo HEXA_ROOT_PASSWORD=s3cr3t \
    HEXA_ANSWERS="$adir/absent"
case "$LAST_OUT" in
  *"no answer file at"*) ok "and says which file it looked for" ;;
  *) bad "refused without naming the file: $LAST_OUT" ;;
esac

# The mechanism install-from-cmdline depends on: it sources this installer at
# its library seam to learn whether the install is unattended, because
# "unattended" is one of the answers in the file. A second parser over there
# would be a second thing to disagree about the password.
cat > "$adir/unattended" <<ANS
hostname = cgnat-file
yes      = yes
ANS
cat > "$adir/source-lib" <<'LIB'
HEXA_INSTALL_LIB=1 . "$1"
unset HEXA_INSTALL_LIB
printf '%s|%s|%s\n' "${HEXA_HOSTNAME:-}" "${HEXA_INSTALL_YES:-}" \
    "${HEXA_ROOT_PASSWORD:+set}"
LIB
got=$(HEXA_ANSWERS="$adir/unattended" sh "$adir/source-lib" "$INSTALLER" 2>/dev/null | tail -1)
case "$got" in
  "cgnat-file|1|") ok "sourcing the installer hands the answers to the caller" ;;
  *) bad "install-from-cmdline would not learn the answers: got '$got'" ;;
esac
rm -rf "$adir"

echo
echo "=== the disk that was just written is verified before success ==="
#
# hexarouting-selftest runs on the LIVE MEDIUM, before the installer, so until
# now nothing had ever looked at the result of an install. The failure mode is
# a box that reports success and then boots to a "grub>" prompt in front of
# whoever is standing there, with the medium already back in its box (#83).
#
# Driven against a fake installed root, because the alternative is a full boot
# cycle in a virtual machine per case - and the case that matters most is the
# broken install, which no passing install can produce.
#
vbin=$(mktemp -d)
cat > "$vbin/blkid" <<'BLKID'
#!/bin/sh
# "blkid -U <uuid>" answers with the device carrying it, and exits non-zero
# when nothing does. Only the UUID the good fstab names exists here.
[ "${1:-}" = -U ] || exit 1
case "${2:-}" in
  11111111-1111-1111-1111-111111111111) echo /dev/vdz2 ;;
  *) exit 2 ;;
esac
BLKID
chmod +x "$vbin/blkid"

mkroot() {  # <dir>: what a good install leaves behind
  mkdir -p "$1/boot/grub" "$1/etc"
  : > "$1/boot/vmlinuz-6.1.0-test"
  printf 'menuentry "HexaRouting" {\n\tlinux\t/boot/vmlinuz-6.1.0-test root=UUID=11111111-1111-1111-1111-111111111111 ro\n\tinitrd\t/boot/initrd.img-6.1.0-test\n}\n' \
    > "$1/boot/grub/grub.cfg"
  printf 'UUID=11111111-1111-1111-1111-111111111111 / ext4 errors=remount-ro 0 1\n' \
    > "$1/etc/fstab"
  printf 'root:$6$saltsalt$hashhashhash:19000:0:99999:7:::\ndaemon:*:19000:0:99999:7:::\n' \
    > "$1/etc/shadow"
}

verify() {  # <root> -> output on stdout, status in $?
  ( PATH="$vbin:$PATH"
    HEXA_INSTALL_LIB=1
    export HEXA_INSTALL_LIB
    . "$INSTALLER"
    verify_installed "$1" 2>&1 )
}

vgood=$(mktemp -d); mkroot "$vgood"
out=$(verify "$vgood"); rc=$?
if [ "$rc" = 0 ]; then ok "a complete install verifies"
else bad "a complete install was reported broken:
$out"; fi

vcase() {  # <description> <expected fragment> <shell that breaks the tree>
  _d=$(mktemp -d); mkroot "$_d"
  ( cd "$_d" && eval "$3" )
  _o=$(verify "$_d"); _rc=$?
  if [ "$_rc" = 0 ]; then
    bad "$1: reported a working system"
  else
    case "$_o" in
      *"$2"*) ok "$1, and the report names the check" ;;
      *) bad "$1: refused without saying which check failed:
$_o" ;;
    esac
  fi
  rm -rf "$_d"
}

# Each of these has been seen on this product, or is one step from something
# that has: a squashfs built without /boot, a stale UUID in fstab, and a late
# failure that leaves the live image's LOCKED root account on the disk.
vcase "GRUB boots a kernel that is not on the disk" \
      "not on the disk" 'rm -f boot/vmlinuz-6.1.0-test'
vcase "there is no GRUB menu at all" \
      "no /boot/grub/grub.cfg" 'rm -f boot/grub/grub.cfg'
vcase "the menu loads no kernel" \
      "no entry that loads a kernel" 'printf "set timeout=5\n" > boot/grub/grub.cfg'
vcase "fstab mounts a UUID no device has" \
      "no device has it" 'printf "UUID=22222222-2222-2222-2222-222222222222 / ext4 defaults 0 1\n" > etc/fstab'
# An fstab with no UUID in it passes a loop over its UUIDs. That is the check
# agreeing with itself, on the one fstab that is certainly wrong.
vcase "fstab identifies nothing" \
      "names no UUID at all" ': > etc/fstab'
vcase "the root account is still the live image's locked one" \
      "still LOCKED" 'printf "root:!:19000:0:99999:7:::\n" > etc/shadow'
vcase "there is no root account" \
      "no root line" 'printf "daemon:*:19000:0:99999:7:::\n" > etc/shadow'

rm -rf "$vgood" "$vbin"
echo
if [ "$fail" = 0 ]; then echo "ALL TESTS PASSED"; exit 0; fi
echo "$fail FAILURES"
exit 1
