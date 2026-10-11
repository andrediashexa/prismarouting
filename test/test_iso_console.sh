#!/bin/sh
# The medium's boot loader puts the console on the serial port (#600).
#
# THE DEFECT. Every entry in the grub.cfg appliance/build-iso.sh writes was
# `linux /live/vmlinuz boot=live components ...` with no console= at all, so
# the kernel console, the self-test report and the autologin VTY reached tty0
# and nothing else. Found installing 0.152.0 on a Proxmox VM driven over its
# serial port: seven minutes of silence, until the GRUB line was edited by
# hand. Every lab boots the medium with -kernel and an -append of its own that
# carries console=ttyS0, so all of them were green over it.
#
# lab/install-iso.sh now boots the medium through its own GRUB and reads the
# VTY off the serial port, which is the proof. This is the seconds-long half:
# it reads the grub.cfg out of build-iso.sh, as text, and refuses an entry
# that would boot silent on a serial-only box - so the next entry added
# without console= goes red here and not twenty minutes into a lab.
#
# What is asserted, and why each:
#   * every `linux` line carries console=tty0 and console=ttyS0,115200, with
#     ttyS0 LAST - the last console= is /dev/console, where the self-test and
#     an unattended install write;
#   * the same console= words the INSTALLED box gets from
#     prisma-routing-install, in the same order, so the medium and the box it
#     installs are reached the same way;
#   * GRUB on serial too (serial, terminal_input, terminal_output), so the
#     menu can be chosen without a screen;
#   * and the lab boots the medium with no -kernel, which is what makes it a
#     proof rather than another copy of the command line.
#
# The checker is driven against broken copies as well, so a check that cannot
# fail is caught here and not discovered later.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }
REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# The heredoc build-iso.sh writes to the medium's boot/grub/grub.cfg.
sed -n '/^cat > "\$WORK\/iso\/boot\/grub\/grub.cfg" <<'"'"'EOF'"'"'$/,/^EOF$/p' \
  "$REPO/appliance/build-iso.sh" | sed '1d;$d' > "$WORK/grub.cfg"

# The console= words the installer gives the installed box, in order.
INSTALLED=$(sed -n 's/^GRUB_CMDLINE_LINUX="\$GRUB_CMDLINE_LINUX \(.*\)"$/\1/p' \
  "$REPO/appliance/prisma-routing-install" | tr ' ' '\n' | grep '^console=' \
  | paste -sd' ' -)

# Prints one line per problem with <grub.cfg>; silent when it is right.
problems() {  # <grub.cfg>
  _n=$(grep -c '^[ \t]*linux ' "$1")
  [ "$_n" -gt 0 ] || echo "no entry loads a kernel"
  grep '^[ \t]*linux ' "$1" | while IFS= read -r _l; do
    _c=$(printf '%s\n' "$_l" | tr ' \t' '\n\n' | grep '^console=' | paste -sd' ' -)
    [ "$_c" = "$INSTALLED" ] \
      || echo "entry '$(printf '%s' "$_l" | sed 's/^[ \t]*//')' has console='${_c:-<none>}', not '$INSTALLED'"
  done
  grep -Eq '^[ \t]*(if )?serial --unit=0 --speed=115200' "$1" \
    || echo "GRUB does not open the serial port at 115200"
  for _t in terminal_input terminal_output; do
    grep -Eq "^[ \t]*$_t( .*)? console( |$)" "$1" \
      && grep -Eq "^[ \t]*$_t( .*)? serial( |$)" "$1" \
      || echo "GRUB's $_t is not both console and serial"
  done
}

echo "=== the installed box's console words were found ==="
case "$INSTALLED" in
  *"console=ttyS0,115200") ok "prisma-routing-install gives: $INSTALLED" ;;
  *) bad "could not read console=...ttyS0,115200 out of prisma-routing-install \
('$INSTALLED'); this test compares the medium against it" ;;
esac

echo "=== the medium's grub.cfg, as build-iso.sh writes it ==="
if [ ! -s "$WORK/grub.cfg" ]; then
  bad "could not find the grub.cfg heredoc in appliance/build-iso.sh"
else
  ok "$(grep -c '^[ \t]*menuentry ' "$WORK/grub.cfg") menu entries read"
  P=$(problems "$WORK/grub.cfg")
  if [ -z "$P" ]; then
    ok "every entry boots with '$INSTALLED', and GRUB is on serial too"
  else
    printf '%s\n' "$P" | while IFS= read -r _p; do echo "  FAIL: $_p"; done
    fail=$((fail + $(printf '%s\n' "$P" | wc -l)))
  fi
fi

echo "=== and the check refuses what #600 shipped ==="
# One broken copy per assertion. Each must produce a problem, or the check
# above is reading nothing.
mutant() {  # <what> <sed expression>
  sed "$2" "$WORK/grub.cfg" > "$WORK/m.cfg"
  if [ -n "$(problems "$WORK/m.cfg")" ]; then
    ok "refused: $1"
  else
    bad "a grub.cfg with $1 passed the check"
  fi
}
mutant "no console= at all (0.152.0)"  's/ console=[^ ]*//g'
mutant "ttyS0 first, so /dev/console is the screen" \
  's/console=tty0 console=ttyS0,115200/console=ttyS0,115200 console=tty0/'
mutant "one entry without console=" '0,/ console=tty0 console=ttyS0,115200/s///'
mutant "no tty0, so the screen loses the kernel" 's/ console=tty0//'
mutant "GRUB not on serial" '/serial --unit=0/d'
mutant "GRUB's output on the screen only" 's/^\([ \t]*terminal_output\).*/\1 console/'
mutant "GRUB's input on the screen only" 's/^\([ \t]*terminal_input\).*/\1 console/'

echo "=== the lab boots the medium through its own boot loader ==="
# The proof is lab/install-iso.sh's boot of the medium with NO -kernel: with
# one, qemu skips GRUB and the -append decides the console, which is how every
# lab stayed green over #600.
L=$(grep -n 'boot_vm "${MEDIUM_SECS' "$REPO/lab/install-iso.sh")
case "$L" in
  *'-cdrom "$ISO"'*-kernel*|*-append*) bad "the lab's medium boot passes -kernel or -append: $L" ;;
  *'-cdrom "$ISO"'*) ok "lab/install-iso.sh boots -cdrom with no -kernel" ;;
  *) bad "lab/install-iso.sh has no boot of the medium through its own GRUB" ;;
esac

echo
if [ "$fail" = 0 ]; then echo "ALL TESTS PASSED"; exit 0; fi
echo "$fail FAILURES"
exit 1
