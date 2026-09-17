#!/bin/sh
# appliance/build-iso.sh, up to the point where it starts spending ten minutes.
#
# NOTHING HAD EVER RUN THIS SCRIPT. Every reader of it in this repository reads
# it as TEXT: test/test_installer.sh greps its --include= line for a DHCP
# client, test/test_kernel_tuning.py reads the kernel it pins,
# test/test_upgrade_artifact.sh extracts the VPP and FRR versions out of it,
# test/test_iso_ready.sh touches a file with its name. So the eight places it
# refuses had been executed by nobody, and four of them are the preflight -
# the block whose whole argument is that a missing tool or a missing plugin
# must be found BEFORE the debootstrap rather than in the middle of the log,
# where "whoever is testing boots the old ISO believing they tested the new
# one".
#
# THE FOUR THIS FILE CAN REACH, and why it stops there. The preflight ends at
# `rm -rf "$WORK"`, and everything past that line wants a real debootstrap:
# the vat2 plugin reaching the image, mdadm reaching the image, and the two
# calls to appliance/no-credentials-in-image are assertions about a chroot
# that took ten minutes and root to build. Those belong to the medium build
# itself; appliance/no-credentials-in-image is separately driven against fake
# roots by test/test_no_credentials.sh.
#
# HOW A SUITE THAT IS NOT ROOT DRIVES A SCRIPT THAT REFUSES ANYONE ELSE. The
# same arrangement test/test_installer.sh uses for the installer's own
# confirmation: `unshare -r` makes `id -u` answer 0 inside a namespace that
# holds no privilege over anything outside it, and the six tools the preflight
# looks for are stubs on a PATH of this suite's own. The script is run out of
# a STAND-IN TREE - symlinks into this repository, with no .build-vpp beside
# them - because it resolves its own $REPO from its path and the point of two
# of these cases is a tree with no compiled plugin in it.
#
# AND THE THING THAT MAKES THEM WORTH ASSERTING RATHER THAN READING: $WORK is
# handed a directory with a file in it. `rm -rf "$WORK"` is the first
# destructive line in the script and it is below all four of these, so the
# file surviving is the evidence that the refusal came first. A refusal that
# fires after the working tree has been wiped has already cost something,
# which is the whole argument the preflight is made of.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }

REPO=$(cd "$(dirname "$0")/.." && pwd)
BISO=appliance/build-iso.sh

# Under /var/tmp and not /tmp for the reason test/test_installer.sh gives:
# nothing here mounts over /tmp, but the two rigs are the same shape and a
# directory that is safe for one is safe for the other.
WORK=$(mktemp -d -p /var/tmp hexa-iso-preflight.XXXXXX)
trap 'rm -rf "$WORK"' EXIT

TREE=$WORK/tree
mkdir -p "$TREE" "$WORK/bin" "$WORK/barebin" "$WORK/scratch"
for e in src appliance tools docs VERSION; do
  [ -e "$REPO/$e" ] && ln -s "$REPO/$e" "$TREE/$e"
done
# The file that says whether anything destructive ran.
: > "$WORK/scratch/untouched"

# Two PATHs, and NOTHING ELSE ON EITHER. `command -v debootstrap` would
# otherwise find whatever is installed on the machine running this suite, and
# the missing-tools case would pass or fail depending on that machine. sh, id
# and dirname are what the script itself reaches for before the checks: `env`
# resolves the interpreter, the root check runs `id`, and $REPO comes from
# `dirname`. Anything else being absent would be this fixture failing.
#
# rm and mkdir are on the list for the opposite reason: they are what the
# script runs IMMEDIATELY BELOW the preflight - "rm -rf $WORK" - and without
# them a build whose refusal had been removed would stop on "rm: not found"
# with the working tree still intact, which is this suite agreeing with itself
# about the last section.
for d in bin barebin; do
  for t in sh id dirname rm mkdir; do
    _p=$(command -v "$t") && ln -sf "$_p" "$WORK/$d/$t"
  done
done
for t in debootstrap xorriso mksquashfs mkfs.vfat mcopy grub-mkrescue; do
  printf '#!/bin/sh\nexit 0\n' > "$WORK/bin/$t"
  chmod +x "$WORK/bin/$t"
done

# OUT is passed in so that tools/version-stamp.sh is never called: it is the
# first thing the script does, it reads git, and under `set -e` a failure
# there would end the run before any of these refusals - which would make
# every case below pass for the wrong reason.
biso() {  # <PATH to run with> -> $BOUT, $BRC
  BRC=0
  BOUT=$(unshare -r env -i PATH="$1" OUT="$WORK/out.iso" \
         WORK="$WORK/scratch" sh "$TREE/$BISO" 2>&1) || BRC=$?
}

if ! unshare -r true 2>/dev/null; then
  echo "  SKIP: no unprivileged user namespaces, so 'must run as root' cannot"
  echo "        be satisfied and none of the preflight can be reached"
  echo
  echo "0 NOT MEASURED"
  exit 0
fi

echo "=== it refuses anyone who is not root, before anything else ==="
# The first line of the script after the name is computed. A build that got
# some way in as an ordinary user would leave a half-made chroot under $WORK
# and a log full of permission errors to read backwards.
if [ "$(id -u)" = 0 ]; then
  echo "  SKIP: this suite is running as root"
else
  BRC=0
  BOUT=$(env PATH="$WORK/bin" OUT="$WORK/out.iso" WORK="$WORK/scratch" \
         sh "$TREE/$BISO" 2>&1) || BRC=$?
  [ "$BRC" != 0 ] \
    && ok "an ordinary user cannot build a medium" \
    || bad "appliance/build-iso.sh ran as an ordinary user"
  case "$BOUT" in
    *"must run as root"*) ok "and is told why" ;;
    *) bad "refused for some other reason: $BOUT" ;;
  esac
fi

echo
echo "=== a machine without the tools is told which ones, and how to get them ==="
# "Without this check a missing tool only shows up much later, as a 'not
# found' in the middle of the log - and whoever is testing boots the old ISO
# believing they tested the new one." That sentence is the whole reason the
# block exists, and until now it had never been read by anything but a person.
biso "$WORK/barebin"
[ "$BRC" != 0 ] \
  && ok "a machine missing the build tools does not start a build" \
  || bad "the build started on a machine with no debootstrap, no xorriso and \
no mksquashfs on it"
case "$BOUT" in
  *"missing tools:"*debootstrap*xorriso*mksquashfs*)
    ok "and every one of them is named, not just the first" ;;
  *) bad "the refusal does not name the tools that are missing: $BOUT" ;;
esac
# The apt-get line. This is read by somebody who has just cloned the tree on a
# machine they have not built on before, and the package names are not the
# command names: squashfs-tools, dosfstools, mtools, grub-pc-bin. Without it
# they are looking up six packages by hand.
case "$BOUT" in
  *"apt-get install"*squashfs-tools*)
    ok "and the command that installs them, by package name" ;;
  *) bad "the refusal names the missing commands and not the packages that \
carry them: $BOUT" ;;
esac

echo
echo "=== a tree with no compiled plugin does not become a medium ==="
# TEN MINUTES, which is what this refusal is measured in. The plugin is not
# compiled by this script, so a tree without it is a build that fails after
# the whole debootstrap has run - and the medium it would produce is the
# defect #119 named: the binary API unregistered, and no CGNAT pool
# configurable on any box installed from it.
if [ -f /vpp_plugins/bpa_plugin.so ]; then
  echo "  SKIP: this machine has /vpp_plugins/bpa_plugin.so, which is the"
  echo "        build container's copy and the second place the script looks"
else
  biso "$WORK/bin"
  [ "$BRC" != 0 ] \
    && ok "a tree with no bpa_plugin.so is refused" \
    || bad "the build ran on a tree with no dataplane plugin in it"
  # THE NEEDLE IS NARROWED, and it has to be. The vat2 plugin's own message
  # is "bpa_test_plugin_bpa_plugin.so not found", which ENDS in the words this
  # case is looking for - so a run that fell through to the next refusal would
  # satisfy a match on that phrase alone. Measured: with this refusal removed
  # the check passed on the message about the other plugin.
  case "$BOUT" in
    *bpa_test_plugin*)
      bad "the tree has no dataplane plugin and the refusal is about the vat2 plugin instead, so the reader is sent to look at the wrong half: $BOUT" ;;
    *"bpa_plugin.so not found"*) ok "and the plugin is named" ;;
    *) bad "refused without naming the plugin: $BOUT" ;;
  esac
  case "$BOUT" in
    *"tools/build.sh plugin"*) ok "and the command that produces it" ;;
    *) bad "the refusal does not say how to build the plugin: $BOUT" ;;
  esac

  # AND THE HALF-FINISHED BUILD, which is the case that cost #119. The two
  # plugins come out of one ninja run, so a tree with one of them is a build
  # that did not finish - and the medium built from it boots, routes, answers
  # ssh and refuses every pool an operator tries to configure on it.
  mkdir -p "$TREE/.build-vpp/lib/vpp_plugins"
  echo "a stand-in for a compiled bpa_plugin.so" \
    > "$TREE/.build-vpp/lib/vpp_plugins/bpa_plugin.so"
  if [ -f /vat2_plugins/bpa_test_plugin_bpa_plugin.so ]; then
    echo "  SKIP: this machine has /vat2_plugins/bpa_test_plugin_bpa_plugin.so"
  else
    biso "$WORK/bin"
    [ "$BRC" != 0 ] \
      && ok "and a tree with the dataplane plugin and not the vat2 plugin is \
refused just as hard" \
      || bad "a medium was built from half a plugin build: every box \
installed from it would have the binary API unregistered and no CGNAT pool \
could be configured on it (#119)"
    case "$BOUT" in
      *"bpa_test_plugin_bpa_plugin.so not found"*)
        ok "naming the half that is missing" ;;
      *) bad "refused without naming the vat2 plugin: $BOUT" ;;
    esac
    # WHAT IT COSTS, said at the moment somebody can act on it. "Not found" on
    # its own reads as a build step to re-run; this says what the medium
    # would have been.
    case "$BOUT" in
      *"no pool can be"*)
        ok "and says what a medium without it cannot do, which is the one \
thing the appliance exists for" ;;
      *) bad "the refusal does not say what the missing plugin costs: $BOUT" ;;
    esac
  fi
fi

echo
echo "=== and not one of them had touched the working tree ==="
# `rm -rf "$WORK"` is the first destructive line in the script and it is below
# every refusal above. The file put there before any of this ran is the
# evidence that they all fired first - and a refusal that fires after the
# working tree has been wiped is a refusal that has already cost something.
[ -f "$WORK/scratch/untouched" ] \
  && ok "the directory the build would have been assembled in is as it was" \
  || bad "a refusal in the preflight ran after 'rm -rf \$WORK' had already \
emptied the working tree"
# And no medium, which is the file somebody would publish.
[ ! -f "$WORK/out.iso" ] \
  && ok "and no ISO was written" \
  || bad "a refused build left an ISO at $WORK/out.iso"

echo "=== say, on its own ==="
# The line after say is defined refuses anyone who is not root, so the
# function is cut out of the script and called alone, the way
# test/test_run_all_labs.sh cuts _jobs_by_mem out of the lab suite. It is one
# line, and every step the build reports goes through it.
sed -n '/^say() /p' "$REPO/$BISO" > "$WORK/say.sh"
got=$(. "$WORK/say.sh"; say "VPP 26.06 and FRR 10.7")
[ "$got" = "==> VPP 26.06 and FRR 10.7" ] \
  && ok "say prints the step behind the '==>' the build log is read by" \
  || bad "say gave '$got'"

echo
if [ "$fail" = 0 ]; then echo "ALL TESTS PASSED"; exit 0; fi
echo "$fail FAILURES"
exit 1
