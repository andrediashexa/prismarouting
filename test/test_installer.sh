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
