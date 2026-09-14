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
# The self-test, because the installer writes one file the self-test reads back
# and the two have to name it identically (#303).
SELFTEST=$(cd "$(dirname "$0")/.." && pwd)/appliance/selftest
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
echo "=== the management network: the answers, and what they refuse (#303) ==="
#
# A box installed from the medium up to and including 0.72.0 came back from
# every reboot with its card DOWN and no address: the installer wrote nothing
# whatever under /etc/network, on an image whose networking.service is enabled
# and whose /etc/network/interfaces.d is empty. The box was reachable at its
# console and nowhere else, and what an operator typed there did not survive
# the next reboot (#303).
#
# Everything below is check mode, so each case costs milliseconds instead of a
# boot cycle - and each one is an install that must stop BEFORE it partitions
# anything, because an answer refused after the disk is written is an answer
# refused too late.
#
# HEXA_NET_SYS is what lets a machine with one card be asked the questions a
# machine with two would be. It is set by nothing on a real box.
netsys=$(mktemp -d)
mkdir -p "$netsys/ens18/device" "$netsys/ens19/device" "$netsys/lo"
echo up > "$netsys/ens18/operstate"
echo down > "$netsys/ens19/operstate"

NOK="HEXA_DISK=$DISK HEXA_HOSTNAME=cgnat01 HEXA_TIMEZONE=America/Sao_Paulo"
# shellcheck disable=SC2086
run 0 "a static address, its gateway and a resolver are accepted" $NOK \
    HEXA_ROOT_PASSWORD=s3cr3t HEXA_NET_SYS="$netsys" \
    HEXA_MGMT_INTERFACE=ens18 HEXA_MGMT_ADDRESS=192.0.2.10/24 \
    HEXA_MGMT_GATEWAY=192.0.2.1 HEXA_MGMT_DNS=192.0.2.53,198.51.100.53
case "$LAST_OUT" in
  *"management     ens18, 192.0.2.10/24 via 192.0.2.1"*)
    ok "and the summary repeats the whole answer back" ;;
  *) bad "the summary does not repeat the management network back:
$LAST_OUT" ;;
esac

# shellcheck disable=SC2086
run 0 "DHCP on a named card is accepted" $NOK HEXA_ROOT_PASSWORD=s3cr3t \
    HEXA_NET_SYS="$netsys" HEXA_MGMT_INTERFACE=ens19 HEXA_MGMT_ADDRESS=dhcp
case "$LAST_OUT" in
  *"management     ens19, by DHCP"*) ok "and the summary says which card" ;;
  *) bad "the summary does not name the card DHCP was asked for:
$LAST_OUT" ;;
esac

# The answer that means "this box is reached at its console". It has to be
# possible, or an operator with an out-of-band management path is forced to
# write an address the box does not need - and it has to be DISTINGUISHABLE
# from the absent answer, which is what the file written for it is for.
# shellcheck disable=SC2086
run 0 "'none' is an answer, not an omission" $NOK HEXA_ROOT_PASSWORD=s3cr3t \
    HEXA_NET_SYS="$netsys" HEXA_MGMT_ADDRESS=none
case "$LAST_OUT" in
  *"management     none: this box is reached at its console only"*)
    ok "and the summary says so in as many words" ;;
  *) bad "'none' did not reach the summary:
$LAST_OUT" ;;
esac

# THE REFUSAL THAT MATTERS MOST. A gateway outside the address's own subnet is
# wrong on the arithmetic alone: ifup refuses the route when the interface
# comes up and carries on, so the box boots with an address, no default route,
# and is reachable from its own subnet only. From anywhere else that is
# indistinguishable from a box that did not boot - and the operator has driven
# to the site by the time anybody finds out.
# shellcheck disable=SC2086
run 1 "a gateway that is not on the address's subnet: refused" $NOK \
    HEXA_ROOT_PASSWORD=s3cr3t HEXA_NET_SYS="$netsys" \
    HEXA_MGMT_INTERFACE=ens18 HEXA_MGMT_ADDRESS=192.0.2.10/24 \
    HEXA_MGMT_GATEWAY=198.51.100.1
case "$LAST_OUT" in
  *"is not on 192.0.2.10/24's subnet"*)
    ok "and says which two answers disagree" ;;
  *) bad "refused without naming the address it disagrees with: $LAST_OUT" ;;
esac
# And the other side of that arithmetic, because a check that refuses
# everything is not a check: a gateway that IS on the subnet has to pass, and
# the two cases differ by one octet.
# shellcheck disable=SC2086
run 0 "and a gateway that IS on it is accepted" $NOK \
    HEXA_ROOT_PASSWORD=s3cr3t HEXA_NET_SYS="$netsys" \
    HEXA_MGMT_INTERFACE=ens18 HEXA_MGMT_ADDRESS=192.0.2.10/24 \
    HEXA_MGMT_GATEWAY=192.0.2.254

# shellcheck disable=SC2086
run 1 "a card this machine has not got: refused" $NOK \
    HEXA_ROOT_PASSWORD=s3cr3t HEXA_NET_SYS="$netsys" \
    HEXA_MGMT_INTERFACE=ens99 HEXA_MGMT_ADDRESS=dhcp
case "$LAST_OUT" in
  *"no card called 'ens99'"*) ok "and names the card it was asked for" ;;
  *) bad "refused without naming the card: $LAST_OUT" ;;
esac
# The cards it DOES have, printed with the refusal. An operator at a console
# being told the name they typed is wrong, without being told the right ones,
# has to go and find them with a command they do not have a prompt for.
case "$LAST_OUT" in
  *ens18*ens19*) ok "and lists the cards this machine does have" ;;
  *) bad "the refusal did not list the cards: $LAST_OUT" ;;
esac

# shellcheck disable=SC2086
run 1 "an address with no prefix: refused" $NOK HEXA_ROOT_PASSWORD=s3cr3t \
    HEXA_NET_SYS="$netsys" HEXA_MGMT_INTERFACE=ens18 \
    HEXA_MGMT_ADDRESS=192.0.2.10
case "$LAST_OUT" in
  *"address with a prefix"*) ok "and says what an address looks like" ;;
  *) bad "refused with no shape to compare against: $LAST_OUT" ;;
esac
# shellcheck disable=SC2086
run 1 "an address that is not one: refused" $NOK HEXA_ROOT_PASSWORD=s3cr3t \
    HEXA_NET_SYS="$netsys" HEXA_MGMT_INTERFACE=ens18 \
    HEXA_MGMT_ADDRESS=192.0.2.300/24
# A prefix outside 1..32 is an address nobody can be reached at, and /0 is the
# one that reads as a typo for /8 rather than as nonsense.
# shellcheck disable=SC2086
run 1 "a prefix outside 1..32: refused" $NOK HEXA_ROOT_PASSWORD=s3cr3t \
    HEXA_NET_SYS="$netsys" HEXA_MGMT_INTERFACE=ens18 \
    HEXA_MGMT_ADDRESS=192.0.2.10/33
# shellcheck disable=SC2086
run 1 "a gateway given with the DHCP answer: refused" $NOK \
    HEXA_ROOT_PASSWORD=s3cr3t HEXA_NET_SYS="$netsys" \
    HEXA_MGMT_INTERFACE=ens18 HEXA_MGMT_ADDRESS=dhcp \
    HEXA_MGMT_GATEWAY=192.0.2.1
case "$LAST_OUT" in
  *"DHCP carries its own"*) ok "and says why the two disagree" ;;
  *) bad "refused without saying which answer wins: $LAST_OUT" ;;
esac
# shellcheck disable=SC2086
run 1 "a DNS server that is not an address: refused" $NOK \
    HEXA_ROOT_PASSWORD=s3cr3t HEXA_NET_SYS="$netsys" \
    HEXA_MGMT_INTERFACE=ens18 HEXA_MGMT_ADDRESS=192.0.2.10/24 \
    HEXA_MGMT_DNS=192.0.2.53,nameserver

# The unattended path, which is how the release gate installs a box. Nothing
# about the management network may be REQUIRED of it: every existing caller
# passes none of these answers, and an installer that started asking would
# stop them dead at a prompt with no console to answer from.
# shellcheck disable=SC2086
run 0 "an unattended install that says nothing about the network still runs" \
    $NOK HEXA_ROOT_PASSWORD=s3cr3t HEXA_NET_SYS="$netsys" HEXA_INSTALL_YES=1
case "$LAST_OUT" in
  *"management network: ens18 by DHCP (unattended)"*)
    ok "and takes DHCP on the default card, saying that it did" ;;
  *) bad "the unattended default was taken in silence, or not at all:
$LAST_OUT" ;;
esac

# Every line of that screen on the console it is read at. The installer prints
# to a serial console on a racked machine, and a question folded by the
# terminal is a question whose default nobody can see.
#
# Up to the summary and no further. What follows it in CHECK MODE is the list
# of reasons this machine could not run the install - "missing tools: sfdisk
# mkfs.ext4 ..." - which is a line composed from whatever this particular
# machine lacks, is printed on a developer box and never on an install
# console, and would make this check measure the runner rather than the
# screen.
WIDE=$(printf '%s\n' "$LAST_OUT" | sed '/could not run the install/,$d' \
       | awk 'length > 79 {print length": "$0}')
[ -z "$WIDE" ] && ok "and no line of the install screen is over 79 columns" \
  || bad "the installer printed lines wider than the console:
$WIDE"

echo
echo "=== the file the answer becomes, written into a root (#303) ==="
#
# THE CENTRAL FACT, shown against a directory rather than a disk. Before this
# existed the whole of an install left nothing under /etc/network at all:
#
#   $ grep -c 'etc/network' appliance/hexarouting-install    ->  0
#
# so the checks below are the ones that would have been red for the life of
# the product. They drive write_network_config() at the library seam, which is
# there for the reason verify_installed() is: the alternative is a boot cycle
# per case, and the cases that matter are the unusual answers.
wnc() {  # <root> <interface> <address> <gateway> <dns>
  ( HEXA_INSTALL_LIB=1
    export HEXA_INSTALL_LIB
    . "$INSTALLER"
    write_network_config "$@" )
}
NETF=/etc/network/interfaces.d/hexarouting

w=$(mktemp -d); mkdir -p "$w/etc/network/interfaces.d"
printf 'source /etc/network/interfaces.d/*\n' > "$w/etc/network/interfaces"
if wnc "$w" ens18 dhcp "" ""; then
  if grep -q "^auto ens18\$" "$w$NETF" && \
     grep -q "^iface ens18 inet dhcp\$" "$w$NETF"; then
    ok "the DHCP answer becomes an ifupdown stanza for that card"
  else
    bad "the DHCP answer did not produce a stanza:
$(cat "$w$NETF" 2>&1)"
  fi
else
  bad "write_network_config refused an ordinary DHCP answer"
fi
# The name has no dot in it, and that is load-bearing rather than taste.
# "source-directory", which an operator may move to and which other Debian
# images ship, applies run-parts naming rules and silently SKIPS a file with a
# dot in its name - and a management network that is skipped is this defect
# again, arriving through an edit somebody made to a neighbouring line.
case "${NETF##*/}" in
  *.*) bad "the management stanza is named '$NETF': a dot in the name is"\
" skipped by source-directory" ;;
  *) ok "and its name carries no dot, so source-directory would read it too" ;;
esac
rm -rf "$w"

w=$(mktemp -d); mkdir -p "$w/etc/network/interfaces.d"
printf 'source /etc/network/interfaces.d/*\n' > "$w/etc/network/interfaces"
wnc "$w" ens18 192.0.2.10/24 192.0.2.1 "192.0.2.53, 198.51.100.53"
if grep -q "^iface ens18 inet static\$" "$w$NETF" && \
   grep -q "^    address 192.0.2.10/24\$" "$w$NETF" && \
   grep -q "^    gateway 192.0.2.1\$" "$w$NETF"; then
  ok "the static answer becomes an address and a gateway"
else
  bad "the static answer did not reach the file:
$(cat "$w$NETF" 2>&1)"
fi
# The resolver is written to /etc/resolv.conf and NOT as "dns-nameservers" in
# the stanza, because that option is implemented by resolvconf's if-up hook
# and this image has no resolvconf: measured on the 0.73.0 squashfs, which has
# no /sbin/resolvconf. The line would be read by nothing at all.
if grep -q "^nameserver 192.0.2.53\$" "$w/etc/resolv.conf" && \
   grep -q "^nameserver 198.51.100.53\$" "$w/etc/resolv.conf"; then
  ok "and the resolver reaches /etc/resolv.conf, one server per line"
else
  bad "the DNS answer did not reach the resolver:
$(cat "$w/etc/resolv.conf" 2>&1)"
fi
if grep -q "dns-nameservers" "$w$NETF"; then
  bad "the stanza carries dns-nameservers, which needs resolvconf - a"\
" package this image does not have, so the line would do nothing"
else
  ok "and the stanza does not use dns-nameservers, which nothing here reads"
fi
rm -rf "$w"

# DHCP must not write a resolver: dhclient writes that file itself when
# resolvconf is absent, and two writers of /etc/resolv.conf is one of them
# losing.
w=$(mktemp -d); mkdir -p "$w/etc/network/interfaces.d"
printf 'source /etc/network/interfaces.d/*\n' > "$w/etc/network/interfaces"
wnc "$w" ens18 dhcp "" "192.0.2.53"
if [ -f "$w/etc/resolv.conf" ]; then
  bad "a DHCP install wrote /etc/resolv.conf, which dhclient also writes"
else
  ok "a DHCP install leaves /etc/resolv.conf to dhclient"
fi
rm -rf "$w"

# The answer 'none' still writes the file. Without it, a box whose operator
# chose no management network and a box installed by a medium that never asked
# are the same box on disk - and the second of those is every appliance
# installed before this existed, which is the one the self-test must shout
# about.
w=$(mktemp -d); mkdir -p "$w/etc/network/interfaces.d"
printf 'source /etc/network/interfaces.d/*\n' > "$w/etc/network/interfaces"
wnc "$w" none none "" ""
if [ -f "$w$NETF" ] && ! grep -q "^iface " "$w$NETF"; then
  ok "'none' writes the file and no stanza, so the answer is on the disk"
else
  bad "'none' left nothing to tell a chosen absence from a forgotten one:
$(cat "$w$NETF" 2>&1)"
fi
rm -rf "$w"

# And the file that has to READ the stanza. The image already sources
# interfaces.d, so this repairs nothing on a box built from it - which is why
# it is tested: a stanza written into a directory nobody sources produces the
# identical unreachable box, with a perfect file on it saying it should not be.
w=$(mktemp -d); mkdir -p "$w/etc/network"
printf '# nothing here reads interfaces.d\nauto lo\niface lo inet loopback\n' \
  > "$w/etc/network/interfaces"
wnc "$w" ens18 dhcp "" ""
if grep -q "^source /etc/network/interfaces.d/\*\$" "$w/etc/network/interfaces"
then
  ok "an /etc/network/interfaces that read nothing is made to read the stanza"
else
  bad "the stanza was written where nothing sources it:
$(cat "$w/etc/network/interfaces")"
fi
# And it is not added twice to a file that already had it, which over
# reinstalls would grow a list of identical source lines.
before=$(grep -c "^source " "$w/etc/network/interfaces")
wnc "$w" ens18 dhcp "" ""
after=$(grep -c "^source " "$w/etc/network/interfaces")
[ "$before" = "$after" ] \
  && ok "and a second install does not add the line again" \
  || bad "the source line was added twice ($before then $after)"
rm -rf "$w"

# The installer WRITES this path and the boot self-test READS it. They are two
# files that have to agree about one name, which in this repository is a thing
# with a history: the rule is that one holds it and the other reads it, and
# where that is impossible - a path is a filesystem contract, not a message -
# the agreement is asserted.
if grep -q "interfaces\.d/hexarouting" "$SELFTEST" 2>/dev/null; then
  ok "the self-test reads the same path the installer writes"
else
  bad "appliance/selftest does not read $NETF, so the box would be installed"\
" with a management network nothing reports on"
fi

# And the OTHER pair that has to agree: the default answer to the management
# question is DHCP, and verify_installed() refuses a disk that answered DHCP
# with no client on it. That refusal is only ever reached on a real install,
# which is a ten-minute boot cycle away - so the thing it depends on is
# asserted here, in milliseconds.
#
# ifupdown merely RECOMMENDS a DHCP client and debootstrap installs no
# Recommends: the 0.73.0 squashfs carried no dhclient, no udhcpc and no
# dhcpcd at all, so this is a package that has to be NAMED and would be
# dropped by anybody tidying the --include list.
#
# The --include LINE and not the file. The comment above that line explains at
# length why the package is named, and it names it - so a grep over the file
# passes on a build that stopped installing it and kept the paragraph saying
# it does. Measured: removing the package from the list left this check green.
BUILDISO=$(cd "$(dirname "$0")/.." && pwd)/appliance/build-iso.sh
INCLUDE_LINE=$(grep -m1 -- "--include=" "$BUILDISO")
case "$INCLUDE_LINE" in
  *isc-dhcp-client*|*dhcpcd-base*|*udhcpc*) _dhcpok=1 ;;
  *) _dhcpok=0 ;;
esac
if [ "$_dhcpok" = 1 ]; then
  ok "and the image names a DHCP client, which the default answer needs"
else
  bad "appliance/build-iso.sh installs no DHCP client, and DHCP is the"\
" default answer to the management question: every box installed with it"\
" would write a perfect stanza that nothing on the disk can carry out"
fi
rm -rf "$netsys"

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
  mkdir -p "$1/boot/grub" "$1/etc/network/interfaces.d" "$1/sbin"
  # The management network, which a good install now leaves behind too (#303):
  # the stanza, an /etc/network/interfaces that reads the directory it is in,
  # and something on the disk able to carry out a DHCP answer.
  printf 'auto ens18\niface ens18 inet dhcp\n' \
    > "$1/etc/network/interfaces.d/hexarouting"
  printf 'source /etc/network/interfaces.d/*\n' > "$1/etc/network/interfaces"
  : > "$1/sbin/dhclient"; chmod +x "$1/sbin/dhclient"
  : > "$1/boot/vmlinuz-6.1.0-test"
  printf 'menuentry "HexaRouting" {\n\tlinux\t/boot/vmlinuz-6.1.0-test root=UUID=11111111-1111-1111-1111-111111111111 ro\n\tinitrd\t/boot/initrd.img-6.1.0-test\n}\n' \
    > "$1/boot/grub/grub.cfg"
  printf 'UUID=11111111-1111-1111-1111-111111111111 / ext4 errors=remount-ro 0 1\n' \
    > "$1/etc/fstab"
  printf 'root:$6$saltsalt$hashhashhash:19000:0:99999:7:::\ndaemon:*:19000:0:99999:7:::\n' \
    > "$1/etc/shadow"
}

# The cards the "machine being installed" has, so that a management network
# naming a card that is not there can be arranged without unplugging one.
vnet=$(mktemp -d); mkdir -p "$vnet/ens18/device"; echo up > "$vnet/ens18/operstate"

verify() {  # <root> -> output on stdout, status in $?
  ( PATH="$vbin:$PATH"
    HEXA_INSTALL_LIB=1
    HEXA_NET_SYS=$vnet
    export HEXA_INSTALL_LIB HEXA_NET_SYS
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

# The management network (#303). Every one of these is a disk that boots
# perfectly and cannot be reached, which is why they are checked here: while
# the operator is still standing in front of the machine with the medium in
# the drive, rather than after the drive to the site.
vcase "no management network was written at all" \
      "was not written" 'rm -f etc/network/interfaces.d/hexarouting'
vcase "the stanza was written where nothing sources it" \
      "does not read interfaces.d" \
      'printf "auto lo\n" > etc/network/interfaces'
vcase "the management card is not on this machine" \
      "is not on this machine" \
      'printf "auto ens99\niface ens99 inet dhcp\n" > etc/network/interfaces.d/hexarouting'
# THE ONE THAT WAS TRUE OF THE PUBLISHED MEDIUM. ifupdown only RECOMMENDS a
# DHCP client and debootstrap installs no Recommends, so the 0.73.0 squashfs
# carried no dhclient, no udhcpc and no dhcpcd - and "iface ens18 inet dhcp"
# on that disk is a file that reads perfectly and does nothing. The default
# answer to the management question is DHCP, so a default that quietly does
# nothing would be worse than no question at all.
# The needle is a whole line of the message and not a phrase that straddles
# the fold. A needle written across a hand-wrapped message matches only while
# the message goes on folding where it folds today, which this repository has
# paid for seven times.
vcase "DHCP is asked for and the disk has no DHCP client" \
      "DHCP client, so nothing on it can ask for an address" \
      'rm -f sbin/dhclient'

# And the answer that is not a fault: 'none' verifies, because an operator who
# said the box is reached at its console said something and the installer
# wrote it down.
vnone=$(mktemp -d); mkroot "$vnone"
printf '# no management network was asked for\n' \
  > "$vnone/etc/network/interfaces.d/hexarouting"
out=$(verify "$vnone"); rc=$?
if [ "$rc" = 0 ]; then
  case "$out" in
    *"no management network was asked for"*)
      ok "'none' verifies, and the report says which answer it was" ;;
    *) bad "'none' verified silently, so a chosen absence and a forgotten one
read the same:
$out" ;;
  esac
else
  bad "a box installed with no management network on purpose was reported
broken:
$out"
fi
rm -rf "$vnone"

rm -rf "$vgood" "$vbin" "$vnet"

echo
echo "=== the records mirror: the answer, and what it refuses (#83) ==="
#
# With one disk a single failure loses the RECORDS, which no reinstall puts
# back. The second disk is destroyed by the same "yes" that destroys the
# first, so every refusal here is a disk that must stay untouched - and the
# acceptance matters as much, because a mirror question that refuses
# everything is a box that silently never gets a mirror.
#
# Check mode, so this costs milliseconds instead of a boot cycle per case.

# A partition, not a whole disk. The mirror takes the whole disk because
# everything on it is destroyed, and /sys/block has an entry only for whole
# disks - which is the same question as "can its size be read".
PART=$(lsblk -rno NAME,TYPE 2>/dev/null | awk '$2 == "part" { print $1; exit }')
run 1 "the mirror named as the disk being installed: refused" $OK \
    HEXA_ROOT_PASSWORD=s3cr3t HEXA_MIRROR="$DISK"
case "$LAST_OUT" in
  *"has to be a SECOND disk"*) ok "and says a mirror needs a second disk" ;;
  *) bad "refused without saying why: $LAST_OUT" ;;
esac
run 1 "a mirror that is not a block device: refused" $OK \
    HEXA_ROOT_PASSWORD=s3cr3t HEXA_MIRROR=/tmp/not-a-disk
if [ -n "$PART" ]; then
  run 1 "a mirror that is a partition rather than a whole disk: refused" $OK \
      HEXA_ROOT_PASSWORD=s3cr3t HEXA_MIRROR="/dev/$PART"
  case "$LAST_OUT" in
    *"not a whole disk"*) ok "and says the mirror takes a whole disk" ;;
    *) bad "refused, but not for being a partition: $LAST_OUT" ;;
  esac
else
  echo "  SKIP: no partition on this machine to offer as a mirror"
fi

# Too small for the records it is supposed to hold. md sizes a mirror by its
# SMALLEST member, so a short leg is an array smaller than the answer - and
# nobody would find that out until the volume filled early, a year in.
#
# An unbound loop device is zero bytes and every Linux box has one, which is
# what makes this case runnable anywhere. The refusal is the same one a 500 GB
# leg gets under a 1 TB answer.
if [ -b /dev/loop0 ] && [ "$(cat /sys/block/loop0/size 2>/dev/null || echo 1)" = 0 ]; then
  run 1 "a mirror disk too small for the records: refused" $OK \
      HEXA_ROOT_PASSWORD=s3cr3t HEXA_LOG_GB=2 HEXA_MIRROR=/dev/loop0
  case "$LAST_OUT" in
    *"a mirror is as big as its smallest disk"*)
      ok "and says why a short disk is not a mirror" ;;
    *) bad "refused without the reason: $LAST_OUT" ;;
  esac
else
  echo "  SKIP: no zero-sized loop device to offer as a short mirror"
fi

# The acceptance. Two whole disks where the machine has them; where it has
# one, the unbound loop device stands in as the TARGET - the installer already
# tolerates a target whose size it cannot read, and says so - so that the disk
# this machine does have can be the mirror. Without this case every refusal
# above is satisfied by an installer that refuses every mirror there is.
DISK2=$(lsblk -dno NAME,TYPE 2>/dev/null | awk '$2 == "disk" { print "/dev/"$1 }' | sed -n 2p)
if [ -n "$DISK2" ]; then
  M_TARGET=$DISK; M_MIRROR=$DISK2
elif [ -b /dev/loop0 ]; then
  M_TARGET=/dev/loop0; M_MIRROR=$DISK
else
  M_TARGET=""; M_MIRROR=""
fi
if [ -n "$M_MIRROR" ]; then
  run 0 "a second whole disk is accepted as the records mirror" \
      HEXA_DISK="$M_TARGET" HEXA_HOSTNAME=cgnat01 \
      HEXA_TIMEZONE=America/Sao_Paulo HEXA_ROOT_PASSWORD=s3cr3t \
      HEXA_LOG_GB=1 HEXA_MIRROR="$M_MIRROR"
  case "$LAST_OUT" in
    *"records mirror $M_MIRROR"*)
      ok "and the summary repeats back the disk that will be destroyed too" ;;
    *) bad "the summary does not name the mirror disk: $LAST_OUT" ;;
  esac
else
  echo "  SKIP: this machine has no second device to mirror onto"
fi

# And the box with ONE disk, which is most of them: no mirror, no question,
# no difference. The summary says so rather than saying nothing, because the
# operator is confirming an install that will keep a year of records on a
# single disk and that is worth one line.
# shellcheck disable=SC2086
run 0 "an install with no mirror answer is accepted exactly as before" $OK \
    HEXA_ROOT_PASSWORD=s3cr3t
case "$LAST_OUT" in
  *"records mirror none (one disk"*)
    ok "and the summary says the records will be on one disk" ;;
  *) bad "a one-disk install does not say it has no mirror: $LAST_OUT" ;;
esac

# The mirror as an ANSWER IN THE FILE, which is the unattended path. A key the
# installer does not know is refused rather than ignored, so a medium written
# with "mirror" would have hung every unattended install if the parser had not
# learned it.
mdir=$(mktemp -d)
printf 'hostname = cgnat-file\nmirror   = %s\n' "$DISK" > "$mdir/answers"
run 1 "the mirror can be answered from the file, and is still validated" \
    HEXA_DISK="$DISK" HEXA_TIMEZONE=America/Sao_Paulo \
    HEXA_ROOT_PASSWORD=s3cr3t HEXA_ANSWERS="$mdir/answers"
case "$LAST_OUT" in
  *"not an answer this installer knows"*)
    bad "the answer file does not know the mirror key at all" ;;
  *"has to be a SECOND disk"*)
    ok "the file's mirror answer arrived and was checked like any other" ;;
  *) bad "the mirror from the file was not used: $LAST_OUT" ;;
esac
rm -rf "$mdir"

echo
echo "=== the mirror menu: a letter, a path, or none ==="
#
# The same fake lsblk the disk menu is driven with. "none" is the DEFAULT and
# a bare Enter takes it: a second disk in a machine is not consent to destroy
# what is on it.
fake=$(mktemp -d)
cat > "$fake/lsblk" <<'LSBLK'
#!/bin/sh
for a in "$@"; do case "$a" in
  -dnbo) MODE=table ;;
  -ndo)  MODE=pkname ;;
esac; done
case "${MODE:-human}" in
  table)
    printf 'sda 34359738368 disk KINGSTON SA400\n'
    printf 'sdb 1000204886016 disk WD Black 1TB\n'
    printf 'sdc 8589934592 disk SanDisk Cruzer\n'
    ;;
  pkname) printf '\n' ;;
  human)
    case "$*" in *SIZE,MODEL*) _two=1 ;; *) _two=0 ;; esac
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
printf '/dev/sdc\n'
FINDMNT
chmod +x "$fake/lsblk" "$fake/findmnt"

mpick() {  # <answer on stdin> <target disk> -> the chosen mirror, or nothing
  printf '%s\n' "$1" | (
    PATH="$fake:$PATH"
    HEXA_INSTALL_LIB=1
    export HEXA_INSTALL_LIB
    . "$INSTALLER"
    choose_mirror "$2" 2>/dev/null
  )
}

out=$(mpick "" /dev/sdb)
case "$out" in
  "") ok "Enter alone takes no mirror: a second disk is not consent" ;;
  *) bad "Enter alone chose a disk to destroy: '$out'" ;;
esac
out=$(mpick none /dev/sdb)
case "$out" in
  "") ok "'none' is an answer" ;;
  *) bad "'none' chose '$out'" ;;
esac
out=$(mpick a /dev/sdb)
case "$out" in
  /dev/sda) ok "a letter picks from the list, with the target left out: $out" ;;
  *) bad "'a' must be the first disk that is not the target, got '$out'" ;;
esac
out=$(mpick /dev/sda /dev/sdb)
case "$out" in
  /dev/sda) ok "a device path is accepted here too: $out" ;;
  *) bad "a device path must work, got '$out'" ;;
esac
# An answer that is neither must RE-ASK. Taken as a device path it becomes a
# refusal several steps later, blamed on a disk the operator never named -
# and the one word somebody is most likely to type at a question about
# destroying a second disk is "yes".
out=$(mpick "yes
a" /dev/sdb)
case "$out" in
  /dev/sda) ok "a word that is not an answer re-asks rather than guessing: $out" ;;
  *) bad "'yes' then 'a' must end at the first offered disk, got '$out'" ;;
esac
# The target must not be offerable as its own mirror, and neither must the
# medium: both are already destroyed or already being read.
menu=$(printf '\n' | (PATH="$fake:$PATH"; HEXA_INSTALL_LIB=1; export HEXA_INSTALL_LIB;
                      . "$INSTALLER"; choose_mirror /dev/sdb 2>&1 >/dev/null))
case "$menu" in
  *sdb*) bad "the disk being installed was offered as its own mirror:
$menu" ;;
  *) ok "the disk being installed is not in the mirror list" ;;
esac
case "$menu" in
  *sdc*) bad "the boot medium was offered as a mirror:
$menu" ;;
  *) ok "and neither is the medium this system booted from" ;;
esac
case "$menu" in
  *"EVERYTHING ON THE DISK YOU CHOOSE IS DESTROYED"*)
    ok "and the list says what choosing one costs" ;;
  *) bad "the mirror list does not say the disk is destroyed:
$menu" ;;
esac
# A live kernel with the brd module carries /dev/ram0 to /dev/ram15, and lsblk
# calls every one of them TYPE=disk. They are RAM: empty again at the next
# boot, and not somewhere to put a router or a year of records.
#
# This is not hypothetical. The one-disk run of lab/install-iso.sh installed a
# box whose only disk was the target, and the installer said "records: not
# mirrored ... for the second disk" - a line about a second disk on a machine
# that has none. The same table feeds the install MENU, so whatever it counted
# was also being offered as an install target.
cat > "$fake/lsblk" <<'VIRT'
#!/bin/sh
for a in "$@"; do case "$a" in -dnbo) MODE=table ;; -ndo) MODE=pkname ;; esac; done
case "${MODE:-human}" in
  table)
    printf 'ram0 67108864 disk \n'
    printf 'loop0 1234 loop \n'
    printf 'sdb 1000204886016 disk WD Black 1TB\n'
    printf 'zram0 8589934592 disk \n'
    ;;
  pkname) printf '\n' ;;
  human) echo '932G' ;;
esac
VIRT
chmod +x "$fake/lsblk"
menu=$(printf '\n' | (PATH="$fake:$PATH"; HEXA_INSTALL_LIB=1; export HEXA_INSTALL_LIB;
                      . "$INSTALLER"; choose_mirror /dev/sdb 2>&1))
if [ -z "$menu" ]; then
  ok "RAM and zram devices are not a second disk, so nothing is asked"
else
  bad "a machine whose only other block devices are RAM was asked to mirror
onto one:
$menu"
fi
tbl=$(PATH="$fake:$PATH" sh -c 'HEXA_INSTALL_LIB=1; export HEXA_INSTALL_LIB
                                . "'"$INSTALLER"'"; disk_table')
case "$tbl" in
  *ram0*|*zram0*|*loop0*)
    bad "the install menu offers a RAM disk as somewhere to put a router:
$tbl" ;;
  *sdb*) ok "and the disk menu itself offers only the real disk" ;;
  *) bad "the disk menu lost the real disk: '$tbl'" ;;
esac

# A machine with nothing else in it is not asked at all.
cat > "$fake/lsblk" <<'ONE'
#!/bin/sh
for a in "$@"; do case "$a" in -dnbo) MODE=table ;; -ndo) MODE=pkname ;; esac; done
case "${MODE:-human}" in
  table) printf 'sdb 1000204886016 disk WD Black 1TB\n' ;;
  pkname) printf '\n' ;;
  human) echo '932G' ;;
esac
ONE
chmod +x "$fake/lsblk"
menu=$(printf '\n' | (PATH="$fake:$PATH"; HEXA_INSTALL_LIB=1; export HEXA_INSTALL_LIB;
                      . "$INSTALLER"; choose_mirror /dev/sdb 2>&1))
if [ -z "$menu" ]; then
  ok "a machine with one disk is asked nothing about mirrors"
else
  bad "a one-disk machine was asked a question it cannot answer:
$menu"
fi
rm -rf "$fake"

echo
echo "=== the mirror is verified before the install reports success ==="
#
# "mdadm --create" exits ZERO having built a degraded array, so its status
# answers "did mdadm run" and not "are the records on two disks". A mirror
# that was born with one leg is the exact failure it exists to prevent,
# arriving at the moment the operator is told the install worked.
#
# Driven against a written-out /proc/mdstat, because the only other way to
# produce these is a machine with a disk that has just died.
mstat=$(mktemp -d)
vmirror() {  # <mdstat file> <device> -> output on stdout, status in $?
  ( HEXA_INSTALL_LIB=1
    export HEXA_INSTALL_LIB
    . "$INSTALLER"
    verify_mirror "$1" "$2" 2>&1 )
}
cat > "$mstat/good" <<'MD'
Personalities : [raid1]
md0 : active raid1 vdb1[1] vda3[0]
      2094080 blocks super 1.2 [2/2] [UU]

unused devices: <none>
MD
out=$(vmirror "$mstat/good" /dev/md0); rc=$?
if [ "$rc" = 0 ]; then
  case "$out" in
    *"2 of 2 disks present"*) ok "a mirror with both legs verifies, and says so" ;;
    *) bad "it passed without saying what it found: $out" ;;
  esac
else
  bad "a healthy mirror was reported broken: $out"
fi

cat > "$mstat/degraded" <<'MD'
Personalities : [raid1]
md0 : active raid1 vda3[0]
      2094080 blocks super 1.2 [2/1] [U_]

unused devices: <none>
MD
out=$(vmirror "$mstat/degraded" /dev/md0); rc=$?
if [ "$rc" = 0 ]; then
  bad "an array created with ONE leg was reported as a mirror: $out"
else
  case "$out" in
    *"created"*"DEGRADED"*) ok "a mirror born degraded is refused, and named" ;;
    *) bad "refused without saying which check failed: $out" ;;
  esac
fi

# Another array, degraded, and not this one. A check that grepped the file for
# "[2/1]" would refuse an install over somebody else's disk.
cat > "$mstat/other" <<'MD'
Personalities : [raid1]
md1 : active raid1 vdd1[0]
      1046528 blocks super 1.2 [2/1] [U_]

md0 : active raid1 vdb1[1] vda3[0]
      2094080 blocks super 1.2 [2/2] [UU]

unused devices: <none>
MD
out=$(vmirror "$mstat/other" /dev/md0); rc=$?
if [ "$rc" = 0 ]; then
  ok "a different array being degraded is not this install's answer"
else
  bad "an unrelated degraded array failed the install: $out"
fi

printf 'Personalities : [raid1]\nunused devices: <none>\n' > "$mstat/none"
out=$(vmirror "$mstat/none" /dev/md0); rc=$?
if [ "$rc" = 0 ]; then
  bad "an array the kernel does not have was reported as a mirror: $out"
else
  case "$out" in
    *"no array"*) ok "an array that was never created is refused" ;;
    *) bad "refused without saying the array is missing: $out" ;;
  esac
fi

out=$(vmirror "$mstat/absent" /dev/md0); rc=$?
if [ "$rc" = 0 ]; then
  bad "an unreadable /proc/mdstat passed as a verified mirror: $out"
else
  ok "and a /proc/mdstat that cannot be read is not a pass either"
fi
rm -rf "$mstat"

echo
echo "=== the control that keeps the console driver honest is invoked ==="
# lab/install-iso.sh has a wrong-password mode, TESTING.md calls it "how the
# driver is kept honest", and nothing ran it: not the suite, which excludes
# install-iso, not the Makefile, and not the release gate (#256). A control
# that is optional is a control nobody runs.
#
# The control is EXTRACTED AND RUN, not grepped for.
#
# The first version of this section grepped for the phrases the control prints,
# and it passed on one whose inversion had been replaced by `if false` - the
# strings were all still there, inside a branch that could not be reached. A
# check that reads the text of a decision cannot see the decision. So the three
# branches are driven against a stub that behaves each of the three ways, and
# what is asserted is the verdict.
#
# It reads tools/test-install.sh, which is where the body lives since #281 -
# it was inline in the Makefile, and `$(call)` ended it at the first comma in
# an English sentence, so `make test-install` could not run at all. The check
# got simpler with it: a file needs no un-tabbing, no backslash stripping and
# no closing paren removed, so what is driven is the real thing rather than a
# reconstruction of it.
CTL=$REPO/tools/test-install.sh
if ! grep -q "install-iso.sh wrong-password" "$CTL"; then
  bad "lab/install-iso.sh has a wrong-password mode that nothing invokes: "\
"TESTING.md calls it how the driver is kept honest, and a driver that stopped "\
"noticing a refused login would let the install lab run its whole session "\
"against a login prompt and report a pass"
else
  work=$(mktemp -d)
  # From the control's banner to the end of the file, with the lab replaced by
  # a stub and the log by one this can read.
  sed -n '/=========== THE CONTROL/,$p' "$CTL" \
    | sed 's|sh /src/lab/install-iso.sh wrong-password|sh "$STUB"|' \
    | sed 's|/tmp/wrong-password.log|"$LOG"|g' > "$work/control.sh"

  drive() {  # <exit code> <what the stub prints> -> the recipe's exit status
    printf '#!/bin/sh\nprintf %%s "$2"\nexit %s\n' "$1" > "$work/stub"
    printf '%s\n' "$2" > "$work/stub.out"
    cat > "$work/stub" <<STUBEOF
#!/bin/sh
cat "$work/stub.out"
exit $1
STUBEOF
    chmod +x "$work/stub"
    ( STUB=$work/stub LOG=$work/log; . "$work/control.sh" ) >/dev/null 2>&1
    echo $?
  }

  got=$(drive 1 "  DIAGNOSIS: the box ANSWERED and REFUSED the credentials.")
  [ "$got" = 0 ] \
    && ok "a control that fails at the login is what the recipe wants" \
    || bad "the recipe rejected a control that failed at the login, which is "\
"the case it exists to accept: exit $got"

  got=$(drive 0 "LAB PASSED")
  [ "$got" != 0 ] \
    && ok "and a control that PASSES fails the recipe" \
    || bad "the recipe accepted a wrong-password run that PASSED. The driver "\
"would no longer notice a refused login and this lab could run its whole "\
"session against a login prompt and report a pass"

  # The case where ONLY the inversion can catch it: exit 0 with the refusal
  # diagnosis on screen. Without this, deleting the inversion still fails the
  # section above - the diagnosis check rejects a run that printed LAB PASSED -
  # and the mutation reads as harmless. It is not: a run that exits 0 has not
  # been refused whatever else is on the screen, and the exit status is the
  # only thing that says so.
  got=$(drive 0 "  DIAGNOSIS: the box ANSWERED and REFUSED the credentials.")
  [ "$got" != 0 ] \
    && ok "even when it exits 0 with the refusal still on the screen, which is "\
"the only case the inversion alone can catch" \
    || bad "the recipe read a run that EXITED 0 as a refusal because the words "\
"were in its output. The exit status is what says the box refused"

  got=$(drive 1 "  DIAGNOSIS: the box printed NOTHING at all since then.")
  [ "$got" != 0 ] \
    && ok "and one that fails by hanging fails it too, because where it "\
"failed is the point" \
    || bad "the recipe accepted a control that failed somewhere other than the "\
"login: a console driver whose failure mode is a hang proves nothing"

  rm -rf "$work"
fi

echo
if [ "$fail" = 0 ]; then echo "ALL TESTS PASSED"; exit 0; fi
echo "$fail FAILURES"
exit 1
