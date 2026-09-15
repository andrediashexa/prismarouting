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

# THE MEDIUM THIS MACHINE BOOTED FROM, answered from a fixture rather than
# from the machine.
#
# The installer asks `findmnt -no SOURCE /run/live/medium` twice, and nothing
# stubbed it: 49 of those in one run went to the machine underneath. Every
# case here names that machine's own first block device as its target, so on a
# machine that IS live-booted - which is the ISO this product ships, and the
# box an operator is most likely to run this on - findmnt answers with the
# target and the installer refuses every single case as "the medium this
# system booted from". Measured with a findmnt answering the way such a box
# does: 34 FAILURES, over a product nobody changed.
#
# Empty is a machine that is not live-booted, which is what every case here
# but the one about the medium means. The section that arranges a medium of
# its own writes it here now; the disk menus further down put a findmnt of
# their own earlier on PATH and still win.
GLOBALBIN=$(mktemp -d)
BOOT_MEDIUM=$GLOBALBIN/medium
: > "$BOOT_MEDIUM"
cat > "$GLOBALBIN/findmnt" <<FINDMNT
#!/bin/sh
case "\$*" in
  *"/run/live/medium"*) cat "$BOOT_MEDIUM" ;;
  *) exec /usr/bin/findmnt "\$@" ;;
esac
FINDMNT
chmod +x "$GLOBALBIN/findmnt"

# AND THE ROUTING TABLE, for the same reason and everywhere rather than in one
# section. The installer asks `ip` what each card carries and which one the
# medium is reaching the network through; with nothing standing in, 68 of
# those reads in one run were answered by the machine, about cards that only
# exist in a fixture. Silence is a card with nothing on it and a box with no
# default route, which is what every fake box here is unless it says
# otherwise - the management section below puts its own `ip` earlier on PATH
# and arranges both answers.
cat > "$GLOBALBIN/ip" <<'IPSTUB'
#!/bin/sh
exit 0
IPSTUB
chmod +x "$GLOBALBIN/ip"
export PATH="$GLOBALBIN:$PATH"

run() {  # <expected exit 0|1> <description> <env assignments...>
  _want=$1; _desc=$2; shift 2
  out=$(env HEXA_INSTALL_CHECK=1 "$@" sh "$INSTALLER" </dev/null 2>&1)
  rc=$?
  LAST_OUT=$out
  if [ "$_want" = 0 ] && [ "$rc" != 0 ]; then
    echo "  FAIL: $_desc (expected acceptance, got $rc)"; echo "$out" | sed 's/^/        /'
    fail=$((fail + 1)); return
  fi
  if [ "$_want" != 0 ] && [ "$rc" = 0 ]; then
    echo "  FAIL: $_desc (expected refusal, it was accepted)"
    echo "$out" | sed 's/^/        /'
    fail=$((fail + 1)); return
  fi
  echo "  [ ok ] $_desc"
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

echo "=== the password is asked twice, and BOTH answers can run out ==="
#
# The case above closes stdin before the first question, so the first
# ask_secret is the one that ends at end of input. The SECOND one has its own
# call and its own refusal, and it is the one a half-finished unattended
# install reaches: a driver that types a password and then loses the console,
# a heredoc with one line in it, an answer file whose password key was
# misspelt so the prompt appears with one line of input left behind it.
#
# Both of them return non-zero at end of input for the same reason - the retry
# loop below rejects an empty password and asks again, so a stdin that has
# gone away would spin on "may not be empty" for ever instead of failing - and
# only one of them had ever been run.
#
# HEXA_INSTALL_YES=1 so that the password prompt is the ONLY prompt: it takes
# the defaults for the log volume, the mirror and the management network,
# which would otherwise eat the lines typed here and make this measure the
# question they were eaten by. The password loop does not look at it.
pwrun() {  # <what is typed at the prompts> -> $PWOUT, $PWRC
  PWRC=0
  PWOUT=$(printf '%s' "$1" | env HEXA_INSTALL_CHECK=1 HEXA_INSTALL_YES=1 \
      HEXA_DISK="$DISK" HEXA_HOSTNAME=cgnat01 \
      HEXA_TIMEZONE=America/Sao_Paulo HEXA_LOG_GB=1 \
      sh "$INSTALLER" 2>&1) || PWRC=$?
}

pwrun 'onlyonce
'
if [ "$PWRC" = 0 ]; then
  bad "a password typed once and never repeated was accepted: an install that
lost its console between the two prompts would have partitioned the disk
$PWOUT"
else
  case "$PWOUT" in
    *"no root password given"*)
      ok "one answer and then end of input: refused, and says the box would
         be one nobody can log into" ;;
    *) bad "refused, but not for the password running out: $PWOUT" ;;
  esac
fi
# AND IT STOPS AT THE PROMPT THAT RAN OUT, which is the only part of this the
# second ask_secret's own refusal can be seen to own. Measured with that
# refusal replaced by `|| true`: the empty SECRET then falls into the
# comparison, the two "differ", and the operator is told
#
#   the two do not match
#
# before the loop asks again and the FIRST prompt refuses the same input. The
# install stops either way - so what the second refusal is worth is the
# diagnosis. An operator whose console went away is sent to re-type a password
# they got right, instead of to the thing that went away.
case "$PWOUT" in
  *"the two do not match"*)
    bad "an input that ran out at the repeat was reported as two passwords
that differ, which is a mistake the operator did not make: $PWOUT" ;;
  *) ok "and it does not accuse them of mistyping a repeat they never gave" ;;
esac

# And the two not matching, which is the path that sends the loop round again.
# It must come back to the SAME refusal and not to a third state: this is a
# console, the operator has mistyped, and what they get is another pair of
# prompts until the input runs out.
pwrun 'first
second
'
case "$PWOUT" in
  *"the two do not match"*) ok "two answers that differ are not accepted" ;;
  *) bad "a mistyped repeat was taken as agreement: $PWOUT" ;;
esac
[ "$PWRC" != 0 ] \
  && ok "and the install still stops rather than looping on a dead console" \
  || bad "the installer accepted a password it was never given twice"

echo
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

# A MACHINE WITH NOTHING TO INSTALL ONTO, which is a live medium booted on a
# diskless box or one whose controller the live kernel has no driver for. The
# menu has no lines, and the question then has no answer: ask() substitutes
# the default "a" for a bare Enter, "a" selects line one, and line one does
# not exist. A menu that kept asking would be a console the operator cannot
# leave; one that took the empty answer would hand the install an empty $DISK.
#
# It has to be an EMPTY table and not an empty machine: the disk this fake
# offers is TYPE=disk and is the medium, so the one thing it has is the one
# thing that may never be the target. That is the shape of the real case.
cat > "$fake/lsblk" <<'NONE'
#!/bin/sh
for a in "$@"; do case "$a" in -dnbo) MODE=table ;; -ndo) MODE=pkname ;; esac; done
case "${MODE:-human}" in
  table) printf 'sdc 8589934592 disk SanDisk Cruzer\n' ;;
  pkname) printf '\n' ;;
  human) echo '  8G' ;;
esac
NONE
chmod +x "$fake/lsblk"
nodisk=$(printf '\n' | (PATH="$fake:$PATH"; HEXA_INSTALL_LIB=1
                        export HEXA_INSTALL_LIB; . "$INST"
                        choose_disk 2>&1 >/dev/null); echo "rc=$?")
case "$nodisk" in
  *"rc=0") bad "a machine with no disk but the medium was given one anyway:
$nodisk" ;;
  *"No disk to install onto."*)
    ok "a machine with nothing but the medium in it is told so, and the menu
         does not ask a question with no answer" ;;
  *) bad "choose_disk refused an empty machine without saying why:
$nodisk" ;;
esac
# And what to DO about it, which on a diskless box is the only actionable
# half: the operator is standing in front of a machine, and "attach a disk"
# is the sentence that gets them to the next step rather than to a reinstall
# of the medium.
case "$nodisk" in
  *"Attach a disk"*) ok "and it says what would make the install possible" ;;
  *) bad "it refused without telling the operator what to do next:
$nodisk" ;;
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
# The fixture at the top of this file, set to say that /run/live/medium is on
# the disk every case here names as its target - which is the arrangement the
# machine underneath used to make for itself, at random, depending on how that
# machine happened to be booted.
#
# A PARTITION of it, not the disk, because that is what findmnt answers for a
# stick: the refusal matches on the prefix, and a check narrowed to the whole
# disk would let the medium through.
printf '%s1\n' "$DISK" > "$BOOT_MEDIUM"
out=$(env HEXA_INSTALL_CHECK=1 HEXA_DISK="$DISK" \
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
# And the same disk accepted again the moment nothing says it is the medium.
# Both arms off one fixture, because until it existed only one of them was
# ever arranged and which one depended on the machine.
: > "$BOOT_MEDIUM"
# shellcheck disable=SC2086
run 0 "and the same disk is accepted on a box that is not live-booted" $OK \
    HEXA_ROOT_PASSWORD=s3cr3t

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

# A file that is THERE and cannot be read. Distinguished from the absent one
# on purpose: a medium mounted with the wrong permissions, or an answer file
# written 0600 by root and read by an installer that is not root yet, is a
# file the operator can see in a listing - so "no answer file at" would send
# them looking for a file that is in front of them.
#
# Only where this suite is not root, because root reads anything and the check
# would then measure the runner rather than the product.
if [ "$(id -u)" = 0 ]; then
  echo "  SKIP: running as root, which can read a 0000 answer file"
else
  printf 'hostname = cgnat01\n' > "$adir/unreadable"
  chmod 000 "$adir/unreadable"
  run 1 "an answer file that cannot be read: refused" \
      HEXA_DISK="$DISK" HEXA_HOSTNAME=cgnat01 \
      HEXA_TIMEZONE=America/Sao_Paulo HEXA_ROOT_PASSWORD=s3cr3t \
      HEXA_ANSWERS="$adir/unreadable"
  case "$LAST_OUT" in
    *"cannot be read"*) ok "and says it was found and not readable, which is"\
" a different thing to go and look at than a file that is not there" ;;
    *) bad "an unreadable answer file was refused as an absent one: $LAST_OUT" ;;
  esac
  chmod 600 "$adir/unreadable"
fi

# A line that is not "key = value" at all. It is refused rather than skipped
# for the reason the unknown key is: an install that arrives at a question
# with no answer, on a machine with no console to answer from, presents as a
# hang hours later and not as the typo it is.
printf 'hostname cgnat01\n' > "$adir/noequals"
run 1 "a line with no '=' in it: refused, not skipped" \
    HEXA_DISK="$DISK" HEXA_TIMEZONE=America/Sao_Paulo \
    HEXA_ROOT_PASSWORD=s3cr3t HEXA_ANSWERS="$adir/noequals"
case "$LAST_OUT" in
  *"expected 'key = value'"*) ok "and says what a line looks like" ;;
  *) bad "refused without saying what was wrong with the line: $LAST_OUT" ;;
esac
# And the LINE NUMBER, because the file is not echoed back: the one thing this
# parser may tell the operator about a line it could not read is where it is.
case "$LAST_OUT" in
  *"line 1:"*) ok "and which line it was" ;;
  *) bad "the refusal does not say which line: $LAST_OUT" ;;
esac

# "yes" is the one key with a vocabulary of its own, and it is the key that
# decides whether the install stops at a prompt. A value it does not know
# must not fall through to either answer: taken as yes it installs a box
# nobody confirmed, taken as no it hangs the unattended install that asked
# for it.
printf 'hostname = cgnat01\nyes = maybe\n' > "$adir/maybe"
run 1 "'yes = maybe': refused rather than read as one of the two" \
    HEXA_DISK="$DISK" HEXA_TIMEZONE=America/Sao_Paulo \
    HEXA_ROOT_PASSWORD=s3cr3t HEXA_ANSWERS="$adir/maybe"
case "$LAST_OUT" in
  *"'yes' takes yes or no, got 'maybe'"*)
    ok "and names the value it would not take" ;;
  *) bad "refused without saying what 'yes' accepts: $LAST_OUT" ;;
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

# AND THE OTHER HALF OF THAT SEAM, which is the half that only runs when
# something is wrong. A file the parser refuses must come back to the caller
# as a RETURN and not as an exit: install-from-cmdline sources this script to
# learn whether the install is unattended, and an exit taken inside its own
# shell takes that script down before it can say what happened or power the
# machine off - which is how an unattended install becomes a machine sitting
# at a prompt until a harness kills it.
#
# The caller prints a word AFTER the source. An exit never reaches it.
cat > "$adir/source-bad" <<'LIB'
HEXA_INSTALL_LIB=1 . "$1" || echo "returned"
echo "still here"
LIB
printf 'passwrod = x\n' > "$adir/badkey"
got=$(HEXA_ANSWERS="$adir/badkey" sh "$adir/source-bad" "$INSTALLER" 2>/dev/null)
case "$got" in
  *returned*"still here"*)
    ok "and a file it refuses RETURNS to the caller instead of exiting" ;;
  *) bad "sourcing the installer over a bad answer file took the caller down"\
" with it: install-from-cmdline would never reach the line that says what"\
" happened, and an unattended install would sit at a prompt. Got: '$got'" ;;
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

# THE ROUTING TABLE, answered from the fake box. HEXA_NET_SYS stands in for
# /sys/class/net, and nothing stood in for `ip` - so the addresses the
# installer printed for these two cards, and the card it takes as the default,
# came from the machine running the suite.
#
# That is not theoretical. This suite asserts that an unattended install takes
# ens18, and it passed because the developer box these fixtures were written
# on routes out of a card of that name. On a machine whose default route is on
# eno1 the installer picks eno1, finds no such card under HEXA_NET_SYS, and
# refuses the install - so the suite goes red over a product nobody changed.
# And on every machine that has a default route, which is every machine, the
# fallback at hexarouting-install:1170 - "no default route, so take the first
# card", which is the one-NIC box #303 is about - had never run at all.
netbin=$(mktemp -d)
cat > "$netbin/ip" <<'IPSTUB'
#!/bin/sh
# Only the two questions the installer asks. Everything else is silence, which
# is what a card with nothing on it answers.
case "$*" in
  *"route list default"*) cat "$netbin/default_route" 2>/dev/null ;;
  *"addr show dev"*)      for a in "$@"; do _n=$a; done
                          cat "$netbin/addr.$_n" 2>/dev/null ;;
  *) : ;;
esac
exit 0
IPSTUB
chmod +x "$netbin/ip"
: > "$netbin/default_route"

NOK="HEXA_DISK=$DISK HEXA_HOSTNAME=cgnat01 HEXA_TIMEZONE=America/Sao_Paulo \
PATH=$netbin:$PATH netbin=$netbin"
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

# And a gateway that is not an address AT ALL, which is a different mistake
# and reaches a different line. What is asserted is the DIAGNOSIS and not the
# refusal: measured with the shape check bypassed, "gw.example.net" is still
# refused, by the subnet arithmetic below it -
#
#   error: the gateway gw.example.net is not on 192.0.2.10/24's subnet: this
#   box would come up with an address, refuse the default route, and be
#   reachable from its own subnet only
#
# because same_subnet() splits on "." and reads every field it cannot parse as
# zero. That message tells an operator at a console to go and check which of
# two subnets is wrong, about a value that is not a subnet and not an address.
# The check that runs first is what names the thing they actually typed.
# shellcheck disable=SC2086
run 1 "a gateway that is not an address at all: refused" $NOK \
    HEXA_ROOT_PASSWORD=s3cr3t HEXA_NET_SYS="$netsys" \
    HEXA_MGMT_INTERFACE=ens18 HEXA_MGMT_ADDRESS=192.0.2.10/24 \
    HEXA_MGMT_GATEWAY=gw.example.net
case "$LAST_OUT" in
  *"the gateway 'gw.example.net' is not an address"*)
    ok "and quotes back what was given for it" ;;
  *) bad "refused, but not for the gateway being unreadable: $LAST_OUT" ;;
esac

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
# THE DEFAULT IS THE CARD THE MEDIUM IS REACHING THE NETWORK THROUGH, when
# there is one - a PXE boot, or somebody who gave the medium an address by
# hand to install over the network. It is the card they are using right now,
# and taking a different one takes the box off the network they are on.
#
# The case above is the box with no default route, which the script's own
# comment calls the usual one: an ordinary live boot has no management
# configuration either, so the fallback to the first card is what runs. Both
# arms, because until the stub above existed the answer came from the machine
# and only ever one of them ran - whichever that machine happened to be.
printf 'default via 10.0.0.1 dev ens19 proto dhcp metric 100\n' \
  > "$netbin/default_route"
# shellcheck disable=SC2086
run 0 "and on a medium that IS on the network, the card it is on" \
    $NOK HEXA_ROOT_PASSWORD=s3cr3t HEXA_NET_SYS="$netsys" HEXA_INSTALL_YES=1
case "$LAST_OUT" in
  *"management network: ens19 by DHCP (unattended)"*)
    ok "the default is the card the medium is reaching the network through" ;;
  *) bad "an unattended install ignored the card the medium is on and took \
another one, which is the box off the network the operator is installing it \
over:
$LAST_OUT" ;;
esac
: > "$netbin/default_route"
# And the card list the installer prints carries what each card HAS, read from
# the box rather than from the machine running the suite. Until the stub
# existed this column was the developer's own address, printed against a card
# that only exists in a fixture.
printf '2: ens18    inet 192.0.2.77/24 brd 192.0.2.255 scope global ens18\n' \
  > "$netbin/addr.ens18"
# shellcheck disable=SC2086
run 1 "a card this machine has not got, with the cards it has beside it" \
    $NOK HEXA_ROOT_PASSWORD=s3cr3t HEXA_NET_SYS="$netsys" \
    HEXA_MGMT_INTERFACE=ens99 HEXA_MGMT_ADDRESS=dhcp
case "$LAST_OUT" in
  *"192.0.2.77/24"*)
    ok "and the list says what each card already carries" ;;
  *) bad "the card list gave no address for a card that has one, so an \
operator choosing between them is choosing between two names:
$LAST_OUT" ;;
esac
rm -f "$netbin/addr.ens18"

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
# And NO fstab at all, which is a different branch and a different box: the
# empty one above is a file the install wrote and filled with nothing, this is
# the redirection never having run. The disk boots into an initramfs prompt
# either way, and the report has to say which of the two it found - a check
# that read an absent file as an empty one would report "names no UUID" about
# a file nobody can go and look at.
vcase "no fstab was written at all" \
      "/etc/fstab was not written" 'rm -f etc/fstab'
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

# THE MEDIUM, OFFERED AS THE MIRROR. The menu already leaves it out (further
# down), and a device path typed by hand or given on the kernel command line
# never goes near the menu - which is the same argument the target makes about
# the same disk, and here it had never been run on either arm. Overwriting
# what is being read fails halfway through, and this one fails AFTER the
# system disk has been partitioned: the operator is left with neither the old
# box nor the new one and no medium to install from.
#
# The fixture at the top of this file, set to a PARTITION of the disk named as
# the mirror, because that is what findmnt answers for a stick. The refusal
# matches on the prefix.
printf '/dev/loop0p1\n' > "$BOOT_MEDIUM"
run 1 "the medium this system booted from, named as the mirror: refused" $OK \
    HEXA_ROOT_PASSWORD=s3cr3t HEXA_MIRROR=/dev/loop0
case "$LAST_OUT" in
  *"/dev/loop0 is the medium this system booted from"*)
    ok "and says it is the medium, not that it is the wrong size or shape" ;;
  *) bad "refused, but not for being the medium being read: $LAST_OUT" ;;
esac
: > "$BOOT_MEDIUM"
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
echo "=== the ssh host keys: what the medium handed over does not reach the disk (#248) ==="
#
# Measured on the published hexarouting-0.37.0-ab23bd7-dirty.iso, which is the
# shape every medium up to 0.66.0 has: its squashfs carries
# ssh_host_{rsa,ecdsa,ed25519}_key, the installer's own `unsquashfs -f -d`
# line restores them onto the target, and `ssh-keygen -A` over that tree
# printed nothing, exited 0 and left all three fingerprints as the medium had
# them - ed25519 SHA256:hwhc4Nfxh3lzwZNoIlON9ehRISKN1BBeo1wcEQi/DJg, comment
# root@a3bcac8dc76e, the build container. `-A` generates only what is MISSING.
# So the install step had no opinion of its own: the keys were this box's only
# because build-iso.sh had emptied the image first.
#
# WHY THE FIXTURE CANNOT BE WHAT IS PASSING HERE. It writes no key. It puts
# three files of marker text where unsquashfs would have put the medium's
# keys, which is the medium's half and nothing else, and every assertion below
# reads what install_ssh_host_keys() left behind. The removal and the
# generation are both the product's. With the removal taken out, `ssh-keygen
# -A` sees three files that already exist, does nothing, and the marker text
# is still on the disk - which is this section going red.
#
# lab/install-iso.sh cannot reach this. It installs from the ISO it just
# built, and that ISO carries no host keys, so the only medium the lab can
# offer the installer is the one this case is not about.
if ! command -v ssh-keygen >/dev/null 2>&1; then
  echo "  SKIP: no ssh-keygen on this machine to generate keys with"
else
  kwork=$(mktemp -d)
  kfake=$kwork/bin; mkdir -p "$kfake"
  # The installer's line is `chroot <root> ssh-keygen -A`. `ssh-keygen -A -f
  # <root>` is that same call without needing root: it writes
  # <root>/etc/ssh/ssh_host_*_key exactly where the chroot would. Anything
  # else the installer might chroot for is not this function's business and
  # exits 0.
  cat > "$kfake/chroot" <<CHROOT
#!/bin/sh
_r=\$1; shift
if [ -f "$kwork/chroot_fails" ]; then exit 1; fi
# Success with nothing written, which is what `ssh-keygen -A` really does
# when it believes the keys are already there.
if [ -f "$kwork/chroot_silent" ]; then exit 0; fi
case "\$*" in
  "ssh-keygen -A") exec ssh-keygen -A -f "\$_r" ;;
esac
exit 0
CHROOT
  chmod +x "$kfake/chroot"

  MEDIUM_KEY='PRIVATE KEY THE MEDIUM HANDED OVER'
  # dsa is in the fixture and not in the list below it, deliberately. Measured
  # here: `ssh-keygen -A` on OpenSSH_9.2p1 Debian-2+deb12u10 prints
  # "generating new host keys: RSA ECDSA ED25519" and writes those three. A
  # type the medium carried that -A does not make is the one file that can
  # outlive the generation, which is the case the .pub half of the removal
  # exists for: without it a public half from the build container stays on the
  # disk for the life of the box, naming a fingerprint the box does not have.
  MEDIUM_TYPES='rsa ecdsa ed25519 dsa'
  OWN_TYPES='rsa ecdsa ed25519'
  handed_root() {  # <dir>; a target as unsquashfs leaves it, medium keys and all
    mkdir -p "$1/etc/ssh"
    for _t in $MEDIUM_TYPES; do
      printf '%s\n' "$MEDIUM_KEY" > "$1/etc/ssh/ssh_host_${_t}_key"
      printf 'ssh-%s AAAAmedium root@a3bcac8dc76e\n' "$_t" \
        > "$1/etc/ssh/ssh_host_${_t}_key.pub"
    done
  }
  drive_keys() {  # <target>; output in $KOUT, exit status in $KRC
    KRC=0
    KOUT=$( ( PATH="$kfake:$PATH"
              HEXA_INSTALL_LIB=1
              export HEXA_INSTALL_LIB
              . "$INSTALLER"
              install_ssh_host_keys "$1" ) 2>&1 ) || KRC=$?
  }

  T=$kwork/from-old-medium
  handed_root "$T"
  drive_keys "$T"
  [ "$KRC" = 0 ] \
    && ok "a target carrying the medium's keys is accepted" \
    || bad "install_ssh_host_keys refused a target it must fix: exit $KRC"

  # THE ONE THAT MATTERS. Not "three keys exist" - three existed before the
  # function ran - but that not one of them is still the file the medium put
  # there.
  left=""
  for _t in $MEDIUM_TYPES; do
    if grep -q "$MEDIUM_KEY" "$T/etc/ssh/ssh_host_${_t}_key" 2>/dev/null; then
      left="$left ssh_host_${_t}_key"
    fi
  done
  [ -z "$left" ] \
    && ok "none of the medium's private keys is on the target" \
    || bad "the medium's private key is still on the disk that was just "\
"installed, so this box answers with the key in the published download:$left"

  # And the public halves, which are how anybody reads a fingerprint off the
  # disk. A .pub the medium wrote beside a private key this box made names a
  # fingerprint the box does not have.
  left=""
  for _t in $MEDIUM_TYPES; do
    if grep -q 'AAAAmedium' "$T/etc/ssh/ssh_host_${_t}_key.pub" 2>/dev/null; then
      left="$left ssh_host_${_t}_key.pub"
    fi
  done
  [ -z "$left" ] \
    && ok "and neither is any of their public halves" \
    || bad "a public half from the medium was left beside a key this box "\
"made, so a fingerprint read off this disk is not the box's:$left"

  # A key that is not the medium's is not yet a key. Without this the whole
  # section passes on a function that only deletes.
  made=0; unreadable=""
  for _t in $OWN_TYPES; do
    if ssh-keygen -lf "$T/etc/ssh/ssh_host_${_t}_key" >/dev/null 2>&1; then
      made=$((made + 1))
    else
      unreadable="$unreadable ssh_host_${_t}_key"
    fi
  done
  [ "$made" = 3 ] \
    && ok "and this box has three host keys of its own that ssh-keygen reads" \
    || bad "the target has no usable host key, so sshd will not start and the "\
"box is reachable at its console only:$unreadable"

  # The operator is holding a medium every box was installed from, and those
  # boxes are still answering with this key. Saying so is the only moment
  # anybody is standing in front of it.
  case "$KOUT" in
    *"this medium carries ssh host keys"*)
      ok "and the console says the medium carried host keys" ;;
    *) bad "the install said nothing about a medium that carries host keys:
$KOUT" ;;
  esac
  case "$KOUT" in
    *ssh_host_ed25519_key*) ok "and names them" ;;
    *) bad "it did not name the keys the medium carried:
$KOUT" ;;
  esac

  # A current medium carries none (build-iso.sh removes them), and an install
  # from one must not accuse it. A warning printed on every healthy install is
  # a warning nobody reads on the one install it is about.
  T2=$kwork/from-current-medium
  mkdir -p "$T2/etc/ssh"
  drive_keys "$T2"
  KOUT2=$KOUT
  [ "$KRC" = 0 ] \
    && ok "a medium that carries no host keys installs" \
    || bad "install_ssh_host_keys failed on a clean medium: exit $KRC"
  made=0
  for _t in $OWN_TYPES; do
    ssh-keygen -lf "$T2/etc/ssh/ssh_host_${_t}_key" >/dev/null 2>&1 \
      && made=$((made + 1))
  done
  [ "$made" = 3 ] \
    && ok "and the box still gets its own three keys" \
    || bad "a box installed from a clean medium got $made host keys"
  case "$KOUT2" in
    *"this medium carries ssh host keys"*)
      bad "a medium carrying no host keys was reported as carrying them:
$KOUT2" ;;
    *) ok "and a clean medium is not accused of carrying keys" ;;
  esac

  # The install must stop here rather than finish. sshd does not start without
  # a host key, and that sshd is the management path to a racked box.
  T3=$kwork/keygen-broken
  handed_root "$T3"
  : > "$kwork/chroot_fails"
  drive_keys "$T3"
  rm -f "$kwork/chroot_fails"
  [ "$KRC" != 0 ] \
    && ok "a target where the keys cannot be made fails the install" \
    || bad "keygen failed and the installer carried on: it would report a "\
"finished install with no host key and no way in over the network"

  # KEYGEN THAT SUCCEEDS AND WRITES NOTHING, which is not a hypothetical: it
  # is exactly what `ssh-keygen -A` does when it thinks the keys are there,
  # and it is how this defect worked in the first place. An install that takes
  # the exit status for the answer reports a finished box with no host key,
  # and sshd is the only way back to a racked one.
  T4=$kwork/keygen-silent
  handed_root "$T4"
  : > "$kwork/chroot_silent"
  drive_keys "$T4"
  rm -f "$kwork/chroot_silent"
  [ "$KRC" != 0 ] \
    && ok "keygen that exits 0 and writes no key fails the install too" \
    || bad "the installer read the exit status and not the effect: it would "\
"report a finished install over an /etc/ssh with no host key in it"

  # The argument, because the rm in this function is a glob. Sourced with
  # nothing to work on it would read /etc/ssh/ssh_host_*_key, which on a
  # developer box is that machine's own keys.
  drive_keys ""
  [ "$KRC" != 0 ] \
    && ok "and no target at all is refused rather than globbed" \
    || bad "install_ssh_host_keys accepted an empty root"

  rm -rf "$kwork"
fi

echo
echo "=== the confirmation, on a run that would really write (#83) ==="
#
# THE ONE ANSWER BETWEEN AN OPERATOR AND A DESTROYED DISK, and nothing had
# ever run it on either arm. Every case above this line is
# HEXA_INSTALL_CHECK=1, and check mode exits at the summary - forty lines
# before the prompt. The prompt is reached only by a run that means it: root,
# the tools present, a live filesystem to unpack. So the one question in this
# program whose wrong answer cannot be undone had no test at all, and neither
# did anything after it: "sfdisk failed", "sfdisk timed out", "partitioning
# did not produce" are the messages an operator reads with the disk already
# half written, and every one of them was unrun.
#
# lab/install-iso.sh cannot reach them either. It drives an install that
# SUCCEEDS, unattended, with HEXA_INSTALL_YES=1 - which skips this prompt by
# design - and a passing install cannot demonstrate a refusal.
#
# HOW THIS RUN IS ARRANGED, in full, because it is a test that drives a disk
# installer. THREE things have to be true at once for it to reach a real disk,
# and each one alone is enough to stop it:
#
#   1. /dev/loop0, UNBOUND. The installer wants a real block device and check
#      mode deliberately does not relax that, so a path under /tmp will not
#      do. An unbound loop device is a block device backed by NOTHING and
#      reporting zero bytes - there is no disk behind it to lose. The case is
#      skipped where /sys/block/loop0/size is not 0, because a loop device
#      that is bound is somebody's file.
#   2. every tool STUBBED on PATH ahead of the real one, sfdisk first. The
#      partitioner this run can reach is a shell script that appends a line to
#      a file and exits with a status this suite chooses. If one of these
#      refusals is ever removed the run carries on into THAT, and the
#      assertion is that the file is still empty.
#   3. `unshare -r` for the uid, which gives this process no privilege over
#      anything outside its own namespace. /dev/loop0 is root:disk 0660 and
#      the namespace's root is not that root, so even the write the stubs are
#      standing in for would be refused by the kernel.
#
# `unshare -m` and a /tmp of its own come with it: the installer writes
# /tmp/sfdisk.log and unpacks into /tmp/hexa-install, and both belong in this
# suite's directory rather than on the machine running it. It also means the
# log can be READ BACK, which the sfdisk case below does - the installer
# quotes that file to the console, and a refusal that names a log it did not
# write is a refusal that sends the operator to an empty file.
if ! unshare -r true 2>/dev/null; then
  echo "  SKIP: no unprivileged user namespaces on this machine, so the"
  echo "        installer's own 'must run as root' cannot be satisfied"
elif [ ! -b /dev/loop0 ] \
     || [ "$(cat /sys/block/loop0/size 2>/dev/null || echo 1)" != 0 ]; then
  echo "  SKIP: /dev/loop0 is not an unbound (zero-byte) loop device, and"
  echo "        nothing else on this machine is safe to name as a target"
else
  # UNDER /var/tmp AND NOT /tmp, which is not fussiness. The run below replaces
  # /tmp with a directory of its own, and a rig that lived under /tmp would be
  # hidden by its own mount: the stubs would be gone from PATH the moment the
  # namespace started, and the installer would refuse for "missing tools"
  # while every case here read as a refusal that fired.
  rwork=$(mktemp -d -p /var/tmp hexa-install-rig.XXXXXX)
  rbin=$rwork/bin
  mkdir -p "$rbin" "$rwork/tmp" "$rwork/net/ens18/device"
  echo up > "$rwork/net/ens18/operstate"
  : > "$rwork/filesystem.squashfs"
  # Everything the installer's tool list names, plus the ones it calls without
  # checking first. They do nothing and exit 0: what is measured is which of
  # them RUNS, and in the two refusals below the answer is none of them.
  #
  # mdadm is here although this box has no mirror. Without it release_disk()
  # takes its "no mdadm" early return, and that return says the disk is still
  # held - so the installer prints four attempts and a copy of /proc/mdstat
  # before every case here. With the stub present the real path runs and finds
  # no array on loop0, which is the answer a box gives.
  for t in mkfs.ext4 mkfs.vfat unsquashfs blkid chpasswd grub-install \
           update-grub partprobe findmnt udevadm mdadm; do
    printf '#!/bin/sh\nexit 0\n' > "$rbin/$t"
    chmod +x "$rbin/$t"
  done
  # The partitioner. It records that it ran, writes something for the log to
  # carry, and exits with whatever this suite asked for.
  cat > "$rbin/sfdisk" <<SFDISK
#!/bin/sh
echo ran >> "$rwork/sfdisk.ran"
echo "the disk is in use by something this stub invented"
exit \$(cat "$rwork/sfdisk.rc" 2>/dev/null || echo 0)
SFDISK
  chmod +x "$rbin/sfdisk"
  # A machine with one disk and nothing else, so the mirror question is not
  # asked and no answer here is eaten by it.
  cat > "$rbin/lsblk" <<'RLSBLK'
#!/bin/sh
for a in "$@"; do case "$a" in -dnbo) MODE=table ;; -ndo) MODE=pkname ;; esac; done
case "${MODE:-human}" in
  table|pkname) : ;;
  human) echo '0B' ;;
esac
RLSBLK
  chmod +x "$rbin/lsblk"

  real() {  # <what is typed> <sfdisk exit status> [extra env...]
    : > "$rwork/sfdisk.ran"
    echo "$2" > "$rwork/sfdisk.rc"
    _typed=$1; shift 2
    RROUT=$(printf '%s\n' "$_typed" | unshare -rm sh -c '
        mount --bind "$1" /tmp || exit 99
        shift
        exec env "$@"' _ "$rwork/tmp" \
        PATH="$rbin:$PATH" \
        HEXA_INSTALL_SRC="$rwork/filesystem.squashfs" \
        HEXA_DISK=/dev/loop0 HEXA_HOSTNAME=cgnat01 \
        HEXA_TIMEZONE=America/Sao_Paulo HEXA_ROOT_PASSWORD=s3cr3t \
        HEXA_LOG_GB=1 HEXA_NET_SYS="$rwork/net" HEXA_MGMT_ADDRESS=none \
        "$@" sh "$INSTALLER" 2>&1)
    RRRC=$?
    RRSFDISK=$(wc -l < "$rwork/sfdisk.ran" | tr -d ' ')
  }

  echo
  echo "=== a box that cannot be asked whether an array holds the disk ==="
  # release_disk() returned 0 when it meant "I could not look", and the caller
  # reads non-zero as "free". So four rounds later the installer said the disk
  # was still held by an array and dumped /proc/mdstat, about a box with no
  # arrays at all, and partitioned it anyway (#339).
  #
  # The stub set above includes mdadm, so it is taken away for this case
  # alone: with it there the box can be asked, and the case would have
  # measured the ordinary path while saying it measured this one.
  mv "$rbin/mdadm" "$rwork/mdadm.away"
  real yes 0
  mv "$rwork/mdadm.away" "$rbin/mdadm"
  case "$RROUT" in
    *"still held by an array"*)
      bad "a box with no mdadm was told an array holds its disk:
$RROUT" ;;
    *"cannot tell whether an array holds"*)
      ok "it says it cannot tell, rather than claiming the disk is held" ;;
    *) bad "it said nothing either way about arrays, so a box that cannot be
asked reads exactly like one that answered: $RROUT" ;;
  esac
  [ "$RRSFDISK" -ge 1 ] \
    && ok "and the install goes on, because nothing here could hold it" \
    || bad "the install stopped over an array question nobody could answer"

  echo
  echo "=== and a disk an array will not let go of is not partitioned ==="
  # release_disk_until_free ended in `return 1` and BOTH call sites discarded
  # it, so the refusal its own comment describes was a message only.
  #
  # Arranged with an mdadm that always succeeds and an /proc/mdstat naming the
  # target: release_disk keeps finding something to stop, four times, and then
  # gives up.
  cat > "$rbin/mdadm" <<'RMDADM'
#!/bin/sh
exit 0
RMDADM
  chmod +x "$rbin/mdadm"
  printf 'Personalities : [raid1]
md0 : active raid1 loop0p1[0]
      1 blocks
' \
    > "$rwork/mdstat"
  : > "$rwork/sfdisk.ran"
  echo 0 > "$rwork/sfdisk.rc"
  RROUT=$(printf 'yes
' | unshare -rm sh -c '
      mount --bind "$1" /tmp || exit 99
      mount --bind "$2" /proc/mdstat || exit 98
      shift 2
      exec env "$@"' _ "$rwork/tmp" "$rwork/mdstat" \
      PATH="$rbin:$PATH" \
      HEXA_INSTALL_SRC="$rwork/filesystem.squashfs" \
      HEXA_DISK=/dev/loop0 HEXA_HOSTNAME=cgnat01 \
      HEXA_TIMEZONE=America/Sao_Paulo HEXA_ROOT_PASSWORD=s3cr3t \
      HEXA_LOG_GB=1 HEXA_NET_SYS="$rwork/net" HEXA_MGMT_ADDRESS=none \
      sh "$INSTALLER" 2>&1)
  RRRC=$?
  RRSFDISK=$(wc -l < "$rwork/sfdisk.ran" | tr -d ' ')
  rm -f "$rbin/mdadm"
  if [ "$RRRC" = 0 ]; then
    bad "a disk an array would not release was installed onto anyway:
$RROUT"
  else
    case "$RROUT" in
      *"held by an array that will not release"*)
        ok "refused, and it says partitioning would write over a member" ;;
      *) bad "it stopped, but not over the array: $RROUT" ;;
    esac
  fi
  [ "$RRSFDISK" = 0 ] \
    && ok "and the partitioner never ran" \
    || bad "it refused and the disk was partitioned $RRSFDISK time(s) anyway"

  # THE REFUSAL. "no" is the answer somebody gives when they have just read
  # the size and the model back and it is not the disk they meant.
  real no 0
  if [ "$RRRC" = 0 ]; then
    bad "the installer went ahead after 'no':
$RROUT"
  else
    case "$RROUT" in
      *"not confirmed (got 'no')"*)
        ok "'no' at the confirmation refuses, and repeats back what was typed" ;;
      *) bad "it stopped, but not at the confirmation: $RROUT" ;;
    esac
  fi
  # AND THE DISK WAS NOT TOUCHED, which is the whole claim. The message is the
  # easy half; a refusal that still ran the partitioner would be this defect
  # with an apology printed over it.
  [ "$RRSFDISK" = 0 ] \
    && ok "and the partitioner never ran" \
    || bad "the disk was partitioned after the operator said no"

  # A BARE ENTER IS NOT AN ANSWER. This is the property the old prompt bought
  # by making the operator re-type the device path, and it is the one that has
  # to survive the shorter question: a disk must not be destroyed by holding
  # down the return key, which is what a console does when somebody leans on
  # it or a driver sends one newline too many.
  real "" 0
  [ "$RRRC" != 0 ] && [ "$RRSFDISK" = 0 ] \
    && ok "a bare Enter is not an answer either, and nothing was partitioned" \
    || bad "the return key alone was enough to erase a disk: rc=$RRRC, the \
partitioner ran $RRSFDISK time(s)"
  case "$RROUT" in
    *"not confirmed (got '')"*)
      ok "and it says the answer it got was nothing at all" ;;
    *) bad "an empty answer was refused without saying so: $RROUT" ;;
  esac
  # And "y", which is what a yes/no prompt teaches people to type and what
  # this one must not take.
  real y 0
  [ "$RRRC" != 0 ] && [ "$RRSFDISK" = 0 ] \
    && ok "and neither is 'y': the word is 'yes'" \
    || bad "'y' erased a disk: rc=$RRRC, the partitioner ran $RRSFDISK time(s)"

  # THE CONTROL. Every case above is satisfied by an installer that refuses
  # everything, which would be a product that cannot install a box. "yes" has
  # to get past this prompt and reach the partitioner.
  real yes 0
  [ "$RRSFDISK" = 1 ] \
    && ok "'yes' is taken, and the partitioner is reached" \
    || bad "the word 'yes' did not get past the confirmation, so nothing \
above this line means anything: $RROUT"
  # And what the run does when the partition table is written and the
  # partitions are not there. The stub sfdisk exits 0 having created nothing,
  # which is the shape of a partitioner that succeeded on a device the kernel
  # then did not re-read: everything after this point would still run, and the
  # box would be built on a partition that is not there.
  case "$RROUT" in
    *"partitioning did not produce /dev/loop02"*)
      ok "and a table that produced no partitions stops the install by name" ;;
    *) bad "sfdisk exited 0 having made no partitions and the install carried \
on: $RROUT" ;;
  esac

  # THE PARTITIONER FAILING, which is the refusal an operator reads with the
  # disk already opened for writing. It has to name the disk and hand over
  # what sfdisk said, because "sfdisk failed" on its own sends them to the
  # partitioner instead of to the thing holding the device.
  real yes 1
  [ "$RRRC" != 0 ] \
    && ok "an sfdisk that fails fails the install" \
    || bad "the install carried on after the partitioner failed"
  case "$RROUT" in
    *"sfdisk failed on /dev/loop0"*) ok "and names the disk it failed on" ;;
    *) bad "the refusal does not name the disk: $RROUT" ;;
  esac
  # The LOG, quoted onto the console. It is written to /tmp/sfdisk.log, which
  # on a box that is about to be rebooted is a file nobody will read again.
  case "$RROUT" in
    *"the disk is in use by something this stub invented"*)
      ok "and hands over what the partitioner itself said" ;;
    *) bad "the refusal quoted none of /tmp/sfdisk.log, so the operator is \
told the install failed and not what stopped it: $RROUT" ;;
  esac

  # AND THE PARTITIONER HANGING, which is the worse of the two: sfdisk waiting
  # on a lock prints nothing, so the install looks hung rather than blocked
  # and nobody knows whether the disk was touched. 124 is what `timeout`
  # exits with when it kills the command.
  real yes 124
  [ "$RRRC" != 0 ] \
    && ok "an sfdisk that times out fails the install too" \
    || bad "the install carried on after the partitioner was killed"
  case "$RROUT" in
    *"timed out on /dev/loop0 after 90s"*)
      ok "and says which disk, and how long it waited" ;;
    *) bad "the timeout was not reported as one: $RROUT" ;;
  esac
  # THE SENTENCE THAT DECIDES WHAT THE OPERATOR DOES NEXT. A disk that may be
  # half written is not a disk to boot; one that was never touched is. The
  # refusal has to say which it is looking at, and it cannot know, so it says
  # that.
  case "$RROUT" in
    *"may be"*"partially written"*)
      ok "and that the disk may be half written, which is what decides \
whether it can be booted" ;;
    *) bad "a timeout mid-partition said nothing about the state of the \
disk: $RROUT" ;;
  esac

  # THE UNATTENDED PATH, which is how the release gate and lab/install-iso.sh
  # install a box: HEXA_INSTALL_YES=1 skips this prompt, and it is the only
  # thing that may. Without this the refusals above are satisfied by an
  # installer that has stopped being able to install unattended at all.
  real "" 0 HEXA_INSTALL_YES=1
  [ "$RRSFDISK" = 1 ] \
    && ok "and an unattended install is not asked, so it does not hang on a \
console that is not there" \
    || bad "HEXA_INSTALL_YES=1 stopped at the confirmation: an unattended \
install would sit at a prompt with nobody to answer it: $RROUT"

  rm -rf "$rwork"
fi

echo
echo "=== a real run that is not root refuses before it asks anything ==="
# `id -u` is the installer's first check and it is a `fatal`: a note in check
# mode, a refusal in a real one. Every other case in this file is check mode,
# so the refusing half had never run - and it is the half that matters, because
# an install that gets some way in as an ordinary user and then stops on a
# permission error has already answered the questions and may have written.
#
# It refuses before the banner, so nothing is asked and nothing is read.
if [ "$(id -u)" = 0 ]; then
  echo "  SKIP: this suite is running as root"
else
  nrout=$(env HEXA_DISK=/tmp/not-a-disk HEXA_HOSTNAME=cgnat01 \
      HEXA_TIMEZONE=America/Sao_Paulo HEXA_ROOT_PASSWORD=s3cr3t \
      sh "$INSTALLER" </dev/null 2>&1)
  nrrc=$?
  [ "$nrrc" != 0 ] \
    && ok "a real run as an ordinary user is refused" \
    || bad "the installer ran as an ordinary user"
  case "$nrout" in
    *"must run as root"*) ok "and says why" ;;
    *) bad "refused for some other reason: $nrout" ;;
  esac
  # BEFORE THE FIRST QUESTION. The banner is printed after the tool checks, so
  # its absence is the evidence that nothing was collected and nothing was
  # read - and the target named above is deliberately one the installer would
  # also refuse, so an installer that checked root LAST would still refuse and
  # this case would pass on it.
  case "$nrout" in
    *"install to disk"*)
      bad "it printed the banner and asked its questions before noticing it \
could not write anything: $nrout" ;;
    *) ok "and it stops before the banner, so no question was asked" ;;
  esac
fi

echo
if [ "$fail" = 0 ]; then echo "ALL TESTS PASSED"; exit 0; fi
echo "$fail FAILURES"
exit 1
