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
if [ "$fail" = 0 ]; then echo "ALL TESTS PASSED"; exit 0; fi
echo "$fail FAILURES"
exit 1
