#!/bin/sh
# Builds the installable Prisma Routing ISO.
#
# Needs root and debootstrap; runs inside the build container with
# --privileged. The result is a hybrid ISO (BIOS and UEFI) booting a live
# system with VPP, FRR and Prisma Routing already installed.
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=${WORK:-/tmp/prisma-iso}
# The name carries the version, so the file keeps its identity once it is off
# the release page. tools/version-stamp.sh owns the calculation - a second
# copy of it here would drift, and the drift would only show up as an ISO
# whose name disagrees with the version inside it.
OUT=${OUT:-$REPO/$(sh "$REPO/tools/version-stamp.sh" --filename "$REPO")}
SUITE=bookworm
MIRROR=http://deb.debian.org/debian
VPP_VERSION=${VPP_VERSION:-26.06-release}
FRR_VERSION=${FRR_VERSION:-10.7.1-0~deb12u1}

# THE CLOCK ON EVERY PHASE (#408). docs/build-timing.md measured the WHOLE at
# 36 minutes, six releases in a row, tag to the gate's last line - and could
# not measure the inside of this script: debootstrap, the VPP and FRR install
# and mksquashfs are one ten-minute block with no clock on any of them. That
# is exactly where the first optimisation is aimed, and optimising against an
# estimate is how the wrong thing gets made faster.
#
# SECONDS SINCE THE PREVIOUS say(), not since the start: what a reader wants
# is which phase cost what, and a running total makes them subtract. The first
# say() has no previous one and prints none rather than printing zero, because
# a zero there would read as a phase that took no time.
_SAY_LAST=""
_SAY_NAME=""
say() {
  _now=$(date +%s)
  # THE DURATION UNDER THE NAME THAT SPENT IT, for the reason
  # tools/release-gate.sh's step() gives at length: a phase is announced
  # BEFORE its work runs, so seconds printed on the new phase's line belong to
  # the previous one. The gate's first run said `building the medium (120s)`
  # about a step that had not started.
  if [ -n "$_SAY_NAME" ]; then
    printf '    %s: %ss\n' "$_SAY_NAME" "$((_now - _SAY_LAST))"
  fi
  printf '==> %s\n' "$*"
  _SAY_LAST=$_now
  _SAY_NAME=$*
}

# The last phase has nothing after it to print its time.
say_done() {
  [ -n "$_SAY_NAME" ] || return 0
  printf '    %s: %ss\n' "$_SAY_NAME" "$(( $(date +%s) - _SAY_LAST ))"
  _SAY_NAME=""
}
[ "$(id -u)" = 0 ] || { echo "must run as root" >&2; exit 1; }

# Without this check a missing tool only shows up much later, as a
# "not found" in the middle of the log - and whoever is testing boots the old
# ISO believing they tested the new one.
missing=""
for t in debootstrap xorriso mksquashfs mkfs.vfat mcopy grub-mkrescue; do
  command -v "$t" >/dev/null 2>&1 || missing="$missing $t"
done
[ -z "$missing" ] || {
  echo "missing tools:$missing" >&2
  echo "install with: apt-get install -y debootstrap xorriso squashfs-tools dosfstools mtools grub-common grub-pc-bin grub-efi-amd64-bin" >&2
  exit 1
}

# The plugin is not compiled here: if it is missing, the failure only shows
# up after the whole debootstrap, with ten minutes already spent. And
# resolving the path ONCE, here, avoids the failure that already happened: the
# check found the .so on the host while the copy looked only in the
# container's /vpp_plugins.
if [ -f "$REPO/.build-vpp/lib/vpp_plugins/bpa_plugin.so" ]; then
  PLUGIN_SO="$REPO/.build-vpp/lib/vpp_plugins/bpa_plugin.so"
elif [ -f /vpp_plugins/bpa_plugin.so ]; then
  PLUGIN_SO=/vpp_plugins/bpa_plugin.so
else
  echo "bpa_plugin.so not found; run tools/build.sh plugin first" >&2
  exit 1
fi

# The vat2 plugin, resolved the same way and refused just as hard.
#
# It was never copied in at all. install.sh looked for it in two places, found
# neither inside the chroot, printed a warning and carried on - and that
# warning has been in the build log of every release since the beginning. The
# medium therefore shipped without the binary API registered, and prismash
# creates a pool over that API: the appliance could not be configured to do
# the one thing it exists for.
#
# Nothing caught it because every lab that drives the API calls
# use_binary_api(), which places this file by hand and describes itself as
# doing "what install.sh does on a real box". It was not what install.sh was
# doing on a real box. https://github.com/andrediashexa/hexarouting/issues/119
if [ -f "$REPO/.build-vpp/lib/vat2_plugins/bpa_test_plugin_bpa_plugin.so" ]; then
  VAT2_SO="$REPO/.build-vpp/lib/vat2_plugins/bpa_test_plugin_bpa_plugin.so"
elif [ -f /vat2_plugins/bpa_test_plugin_bpa_plugin.so ]; then
  VAT2_SO=/vat2_plugins/bpa_test_plugin_bpa_plugin.so
else
  echo "bpa_test_plugin_bpa_plugin.so not found; run tools/build.sh plugin" >&2
  echo "first. Without it the binary API is unreachable and no pool can be" >&2
  echo "configured on the installed box." >&2
  exit 1
fi
# THE PLUGIN'S API DEFINITIONS (#527), built by the same ninja run as the two
# plugins above. install.sh refuses a tree without them, and it runs in the
# chroot below, which sees only what is copied into it - so the medium of
# 0.148.0's first cut failed there, at install.sh's refusal, with the file
# sitting in the build tree out here. Refused here too, before anything is
# built, and copied in beside the tree install.sh reads.
API_JSON="$REPO/.build-vpp/src/plugins/bpa/bpa.api.json"
if [ ! -f "$API_JSON" ]; then
  echo "bpa.api.json not found under .build-vpp; run tools/build.sh plugin" >&2
  echo "first. Without it the VTY reaches the CGNAT through a vat2 process" >&2
  echo "per message on every box installed from this medium." >&2
  exit 1
fi

# Optimised, or not shipped (#490): every release to 0.140.2 carried the
# plugin at -O0, 3.6x the clocks a packet, and nothing said so.
sh "$REPO/tools/plugin-optimised.sh" "$PLUGIN_SO" >&2 || exit 1

ROOT=$WORK/chroot
rm -rf "$WORK"
mkdir -p "$ROOT" "$WORK/iso/live" "$WORK/iso/boot/grub"

# isc-dhcp-client is NAMED here, and it is not a package anybody would think
# to name: ifupdown only RECOMMENDS a DHCP client, and debootstrap installs no
# Recommends at all. Measured on a bookworm root built the way this one is:
#
#   # apt-get install -y --no-install-recommends ifupdown iproute2
#   dhclient: NONE   udhcpc: NONE   dhcpcd: NONE
#
# and the same three absences were read back out of the 0.73.0 squashfs. The
# installer's default answer for the management network is DHCP (#303), so
# without this the file it writes is a stanza with nothing on the box able to
# carry it out - a box that boots, says nothing, and has no address, which is
# precisely the defect being fixed wearing a different hat.
#
# prisma-routing-install's verify_installed() refuses such a disk before it
# reports success, so this is asserted on the box and not only decided here.
say "debootstrap $SUITE"
debootstrap --arch=amd64 --variant=minbase \
  --include=systemd-sysv,initramfs-tools,kmod,live-boot,grub2-common,grub-pc-bin,grub-efi-amd64-bin,ca-certificates,curl,gnupg,zstd,iproute2,ifupdown,isc-dhcp-client,openssh-server,snmpd,python3,less,vim-tiny \
  "$SUITE" "$ROOT" "$MIRROR"

# THE KERNEL IS PRISMA ROUTING'S (#449): Debian's source at the version in
# appliance/kernel.env, Debian's configuration, renamed -prisma and with the
# module classes no router uses left out (tools/build-kernel.sh says which,
# and refuses a build missing any NIC, storage, virtio, vfio, uio, RDMA or
# hypervisor module Debian ships). linux-image-amd64 is not installed, so apt
# on the box never brings Debian's back beside it. `make iso` builds or takes
# it from the cache and puts it at the top of the tree; with none there the
# medium would boot nothing, so this stops.
say "the kernel"
[ -f "$REPO/prisma-kernel.deb" ] || {
  echo "error: no prisma-kernel.deb at the top of the tree; 'make kernel' builds it" >&2
  exit 1; }
cp "$REPO/prisma-kernel.deb" "$ROOT/tmp/prisma-kernel.deb"
chroot "$ROOT" dpkg -i /tmp/prisma-kernel.deb >/dev/null
rm -f "$ROOT/tmp/prisma-kernel.deb"
say "  $(ls "$ROOT"/boot/vmlinuz-* | sed 's|.*/vmlinuz-||')"

say "VPP and FRR repositories"
chroot "$ROOT" /bin/sh -c "
  curl -s https://packagecloud.io/install/repositories/fdio/release/script.deb.sh | bash
  curl -s https://deb.frrouting.org/frr/keys.gpg -o /usr/share/keyrings/frrouting.gpg
  echo 'deb [signed-by=/usr/share/keyrings/frrouting.gpg] https://deb.frrouting.org/frr bookworm frr-stable' > /etc/apt/sources.list.d/frr.list
"

# python3-vpp-api is the VTY's one connection to the dataplane (#527), at
# VPP's own version: the boot's `--load` runs prismash on the host, and
# without it every message of that load is a vat2 process of its own.
#
# iputils-ping, traceroute and mtr-tiny are the three the VTY's own `ping`,
# `traceroute` and `mtr` statements run (#395). They are in THIS list and not
# the debootstrap one above because they are the product's, not the base
# system's: prismash refuses each statement by naming its package, and the boot
# self-test reads the same table back, so a tool dropped here is a tool the
# box says is missing rather than a statement that fails in the tool's own
# words.
say "VPP $VPP_VERSION and FRR $FRR_VERSION"
chroot "$ROOT" /bin/sh -c "
  export DEBIAN_FRONTEND=noninteractive VPP_INSTALL_SKIP_SYSCTL=1
  apt-get update -qq
  apt-get install -y --no-install-recommends \
    squashfs-tools dosfstools parted fdisk mdadm \
    iputils-ping traceroute mtr-tiny \
    vpp=$VPP_VERSION vpp-plugin-core=$VPP_VERSION vpp-plugin-dpdk=$VPP_VERSION \
    libvppinfra=$VPP_VERSION python3-vpp-api=$VPP_VERSION \
    frr=$FRR_VERSION frr-pythontools=$FRR_VERSION \
    frr-rpki-rtrlib=$FRR_VERSION frr-snmp=$FRR_VERSION \
    docker.io docker-compose apparmor
  apt-get clean
"

# THE CONTAINER IMAGES, INSIDE THE MEDIUM (#396).
#
# An appliance installs from an ISO with no network guaranteed, so an image
# the box needs at boot has to be in the file it was installed from - the
# same reason VPP and FRR are apt-installed into the root rather than pulled
# on first start.
#
# Debian's OWN docker.io and docker-compose, and not Docker's repository: the
# argument is the one docs/appliance.md makes about the kernel, which is that
# this product inherits security updates by doing nothing and a package from
# somebody else's repository is one this project would have to track. Measured
# on bookworm: docker.io 20.10.24 and docker-compose 1.29.2, and that compose
# reads appliance/compose.yaml as it ships.
#
# AND `apparmor`, WHICH IS NOT OPTIONAL ON THIS KERNEL. Debian's kernel has
# AppArmor enabled, so docker asks apparmor_parser to load its `docker-
# default` profile before it starts a container - and with the parser absent
# the start fails outright:
#
#   error: exec: "apparmor_parser": executable file not found in $PATH
#
# Measured on an installed box by lab/install-iso.sh's PHASE 6: the unit
# failed, no container ran, and the only clue was that one line in the
# journal. The package is Debian's own and 2 MB, and what it buys is a
# dataplane that runs CONFINED: `docker-default (enforce)`, which the build
# host has had all along - which is why every compose-check in this
# repository passed while a real box could not start a container at all.
#
# Built here rather than expected: the tarball is a product of this tree's
# own plugin, so a medium built from a tag carries the images that tag
# compiles.
say "the container images"
IMAGES=$REPO/prisma-routing-images.tar.gz
# BUILT BEFORE THIS RUNS, and not here. build-iso.sh executes inside a
# privileged container with no docker daemon of its own - `make iso` is what
# puts it there - so a `docker build` at this point talks to nothing and the
# medium silently carries no images. Measured: the first version warned and
# shipped a 400 MB ISO with none. The Makefile's `iso` target depends on
# `images`, which runs tools/build-images.sh on the host where the daemon is.
if [ "${PRISMA_SKIP_IMAGES:-0}" = 1 ]; then
  say "skipped: PRISMA_SKIP_IMAGES=1, and the medium will carry none"
elif [ -f "$IMAGES" ]; then
  # Into the tree install.sh runs from below, not onto the root: install.sh
  # places them, exactly as it does out of an upgrade artifact (#427), so the
  # manifest names them on a box from a medium and on an upgraded box alike.
  mkdir -p "$ROOT/src"
  cp "$IMAGES" "$ROOT/src/prisma-routing-images.tar.gz"
  say "carried: $(( $(wc -c < "$IMAGES") / 1048576 )) MB of images"
else
  # LOUD, AND NOT FATAL. A medium without the images still installs and still
  # boots the systemd units, which is what every box does today; what it
  # cannot do is start the containers. Refusing here would make a docker
  # failure on the build host into a release that cannot be cut at all.
  say "WARNING: no $IMAGES, so this medium carries no container images"
  say "         build them first: sh tools/build-images.sh save"
fi

say "Prisma Routing"
mkdir -p "$ROOT/src"
cp -a "$REPO/src" "$REPO/appliance" "$REPO/tools" "$REPO/docs" "$ROOT/src/"
mkdir -p "$ROOT/vpp_plugins" "$ROOT/vat2_plugins"
cp "$PLUGIN_SO" "$ROOT/vpp_plugins/"
cp "$VAT2_SO" "$ROOT/vat2_plugins/"
mkdir -p "$ROOT/src/.build-vpp/src/plugins/bpa"
cp "$API_JSON" "$ROOT/src/.build-vpp/src/plugins/bpa/"
# The chroot cannot see the host's .git, so the stamp is computed here and
# passed in. Without it every ISO would declare itself "unknown".
if [ -z "${PRISMA_VERSION_STAMP:-}" ]; then
  PRISMA_VERSION_STAMP=$(sh "$REPO/tools/version-stamp.sh" "$REPO")
fi
say "version: $PRISMA_VERSION_STAMP"
chroot "$ROOT" /bin/sh -c "cd /src && PRISMA_VERSION_STAMP='$PRISMA_VERSION_STAMP' PRISMA_VTY_LOGIN=1 sh appliance/install.sh"
rm -rf "$ROOT/src"

# install.sh REFUSES a tree with only one of the two plugins now, before it
# writes anything (#301), so the run above cannot get this far without having
# placed it. This sentence used to say install.sh "warns and carries on ...
# which is right for a source install on a box that may not want the API", and
# both halves of that turned out to be wrong: there is no such box, since the
# vat2 plugin is how prismash learns that a configuration was REFUSED rather
# than applied, and #119 is that warning having already cost every ISO the
# binary API.
#
# Kept anyway, and not because the refusal might be removed. It asserts on a
# different thing: install.sh refuses on what the BUILD TREE has, and this
# checks what reached the IMAGE. A copy step that stops copying, a chroot that
# silently did nothing, a path that moved - none of those is a missing plugin
# in the tree, and none of them would reach install.sh's refusal at all.
#
# The assertion is on the file install.sh was supposed to place, not on the
# copy staged above, so it fails if the copy is there and the install step
# stops putting it in place.
VAT2_INSTALLED="$ROOT/usr/lib/x86_64-linux-gnu/vat2_plugins/bpa_test_plugin_bpa_plugin.so"
[ -f "$VAT2_INSTALLED" ] || {
  echo "the vat2 plugin did not reach the image. The binary API would not be" >&2
  echo "registered and no CGNAT pool could be configured on the installed" >&2
  echo "box (#119)." >&2
  exit 1
}
say "binary API: vat2 plugin in place"

# The medium has to be able to BUILD the records mirror, not merely to offer
# the question. An installer that asks for a second disk and then finds no
# mdadm refuses at the one moment the operator is standing in front of the
# rack with the disks already in it - and the refusal would come from the
# medium, not from anything they did. Asserted here for the same reason the
# vat2 plugin is: a warning in a build log this long is not a signal.
[ -x "$ROOT/sbin/mdadm" ] || [ -x "$ROOT/usr/sbin/mdadm" ] || {
  echo "mdadm did not reach the image, so the installer could not mirror the" >&2
  echo "connection records onto a second disk (#83)." >&2
  exit 1
}
say "records mirror: mdadm in place"

# Autologin on the console, for the LIVE image only. A racked appliance is
# reached by serial console or IPMI, and a live lab image that stops at a login
# prompt does not show what it is. On an INSTALLED box this would mean physical
# or IPMI access equals full access with no password, which is a decision for
# the operator, not a default - so install.sh never does this.
say "console ordering (both) and autologin (live image only)"
for tty in ttyS0 tty1; do
  d="$ROOT/etc/systemd/system/serial-getty@$tty.service.d"
  [ "$tty" = tty1 ] && d="$ROOT/etc/systemd/system/getty@$tty.service.d"
  mkdir -p "$d"

  # TWO files, and the split is the whole point.
  #
  # These used to be one, and the installer deletes what it finds here because
  # an installed box must not log anybody in without a password. So it took the
  # ORDERING with the autologin, and an installed box drew a login prompt while
  # prisma-routing.service was still waiting for VPP. The first thing anybody does
  # with a new box is log in at that prompt, and `configure` there built a
  # candidate out of vppctl's error text that refused every later commit in the
  # session (#211).
  #
  # The ordering belongs to every box; the autologin belongs to the live image
  # alone. Separate files say which is which to the installer, and to whoever
  # reads the directory next.
  cat > "$d/10-after-selftest.conf" <<EOF
[Unit]
# The self-test writes to /dev/console and this getty draws the VTY prompt on
# the same screen. Nothing ordered them, so the prompt was drawn first and the
# report printed on top of it: what the operator saw was a wall of text ending
# in "=====" and no prompt, on a machine that was working and waiting for
# input. It looked like it had stopped, and the only way to find out otherwise
# was to press Enter on a router you have just been handed (#93).
#
# And after the INSTALLER for the same reason one step on: an unattended
# install writes its progress to the console too. Ordering the getty after the
# self-test alone made the getty win deterministically where it used to race,
# so the install ran behind a login prompt and its output was never seen -
# the lab reported "the installer unit never started" for a unit that had.
# No Wants= for it: the unit carries a ConditionKernelCommandLine and must
# stay skipped on a boot that did not ask to install.
#
# On an INSTALLED box this is what keeps the prompt back until the dataplane is
# up: the self-test is ordered after prisma-routing.service, whose ExecStartPre
# waits for VPP. A prompt offered before that invites an operator into a VTY
# that cannot read the box (#211).
After=prisma-routing-selftest.service prisma-routing-installer.service
Wants=prisma-routing-selftest.service
EOF

  # Live image only. The installer removes this one and keeps the other.
  cat > "$d/20-autologin.conf" <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear %I \$TERM
EOF
done

say "box identity"
# The hostname stays "prisma-routing" through the rename to Prisma Routing (#202):
# it is what the installer offers as the default and what every box installed
# so far answers to, and a hostname is a name on a disk, not a string a
# person reads on the banner.
echo prisma-routing > "$ROOT/etc/hostname"
printf '127.0.0.1 localhost\n127.0.1.1 prisma-routing\n' > "$ROOT/etc/hosts"

# NO CREDENTIALS ON THE LIVE IMAGE.
#
# This used to set 'root:prisma-routing' and permit root over SSH. Both travelled
# onto the disk: prisma-routing-install unpacks this squashfs, /etc/shadow and
# all, and only asked for a password at the very end - so an install that was
# aborted, that failed late, or that ran from the kernel command line left a
# BOOTABLE disk with a password printed in the release notes.
#
# The account is locked instead. Console autologin does not authenticate, so
# nothing is lost on the live image, and the failure direction is now the safe
# one: an install that dies halfway leaves a disk nobody can log into rather
# than a disk anyone can. prisma-routing-install sets the real password, from an
# answer the operator gave before it wrote anything.
say "locking the live image's root account"
chroot "$ROOT" passwd -l root >/dev/null
# Masked, not merely disabled: with a locked account sshd could not
# authenticate anyway, and masking leaves no listener to reason about. The
# installer unmasks it on the target and writes the sshd policy there.
chroot "$ROOT" systemctl mask ssh.service >/dev/null 2>&1 || true

# AND THE SSH HOST KEYS, which are a credential in exactly the same sense and
# shipped in every ISO this project has published (#245).
#
# openssh-server's postinst generates them inside the build container, so the
# private halves were squashed into the image with everything else. The
# installer unpacks that squashfs onto the disk and then enables sshd with
# PermitRootLogin yes: every box installed from one release therefore answered
# with the SAME host key, and its private half was in the download. Anybody who
# fetched the ISO could impersonate any of those boxes on the management
# channel, and the operator's client would say nothing, because the key it was
# pinned to is the key it was handed.
#
# Nothing is lost on the live image: sshd is masked above. prisma-routing-install
# generates a fresh set on the target, per box, before it enables the service.
say "removing the live image's ssh host keys"
rm -f "$ROOT"/etc/ssh/ssh_host_*_key "$ROOT"/etc/ssh/ssh_host_*_key.pub

# The refusal. Everything above this line is what the image is ALLOWED to
# carry; this says what it must not, and it runs on the root while it is still
# a directory - after which the only way to look inside is to unsquash it.
#
# docs/appliance.md: "No answer file is shipped in the ISO, and the build MUST
# NOT put one there." Nothing held that rule. Writing an answer file into this
# root left all 34 suites green, and an answer file carries the root password
# (#245).
#
# The same script is driven against fake roots by test/test_no_credentials.sh,
# so the refusal is proved in seconds instead of in a ten-minute build that
# needs root.
say "no credentials in the image"
sh "$REPO/appliance/no-credentials-in-image" "$ROOT" || exit 1

# THE INITRAMFS, trimmed and compressed as asked (#450). The hook drops the
# driver classes no root on this box sits behind; zstd is installed because
# initramfs.conf has always said COMPRESS=zstd and mkinitramfs, with no zstd
# to run, fell back to gzip without a word - 0.126.0's initrd was gzip. The
# hook stays in the image, so an installed box keeps it across kernel
# updates.
say "initramfs"
KVER=$(basename "$(ls -d "$ROOT"/lib/modules/* | head -1)")
install -D -m 0755 "$REPO/appliance/initramfs/prisma-trim" \
  "$ROOT/etc/initramfs-tools/hooks/prisma-trim"
chroot "$ROOT" update-initramfs -u -k "$KVER" >/dev/null
# The first bytes name the compression; `file` is not in the build image.
_magic=$(od -An -tx1 -N4 "$ROOT/boot/initrd.img-$KVER" | tr -d ' ')
say "  $(du -h "$ROOT/boot/initrd.img-$KVER" | cut -f1) initrd, magic $_magic (28b52ffd is zstd, 1f8b is gzip)"

say "squashfs"
chroot "$ROOT" /bin/sh -c "apt-get clean; rm -rf /var/lib/apt/lists/*"
cp "$ROOT/boot/vmlinuz-$KVER"  "$WORK/iso/live/vmlinuz"
cp "$ROOT/boot/initrd.img-$KVER" "$WORK/iso/live/initrd"
# /boot is KEPT in the squashfs, though the live boot does not need it: the
# kernel and initrd are copied to the ISO separately, above.
#
# It is kept because the installer unpacks this squashfs onto a disk, and a
# system with no /boot has no kernel, no initrd and no GRUB modules. GRUB
# installs fine, finds nothing to boot, and drops to a "grub>" prompt - which
# looks exactly like a broken bootloader and is not. Excluding it saves about
# 80 MB of ISO and costs the product its ability to be installed.
#
# -mem and -processors bound mksquashfs: with xz and no limit it consumes
# memory proportional to the parallelism and takes the build machine down. The
# cost is a few extra seconds; the benefit is a build that fits in a modest
# VM.
mksquashfs "$ROOT" "$WORK/iso/live/filesystem.squashfs" \
  -comp xz -no-progress \
  -processors "${SQUASH_PROCS:-2}" -mem "${SQUASH_MEM:-768M}"

say "bootloader"
# THE MEDIUM SPEAKS ON THE SERIAL PORT, AND ON THE SCREEN (#600).
#
# Every entry used to be `linux /live/vmlinuz boot=live components quiet`,
# with no console= at all, so the kernel console, the self-test and the
# autologin VTY reached tty0 and nothing else. Found installing 0.152.0 on a
# Proxmox VM driven over its serial port: ttyS0 stayed silent for seven
# minutes, until the GRUB line was edited by hand. Nothing in this repository
# had ever booted the medium through its own boot loader - every lab handed
# qemu -kernel and an -append line carrying console=ttyS0 - so the defect
# was invisible to all of them. lab/install-iso.sh now boots this file's
# default entry as it stands and reads the VTY off the serial port.
#
# The kernel arguments are the installed box's, from prisma-routing-install,
# in the same order: ttyS0 LAST, because the last console= is /dev/console,
# and /dev/console is where the self-test report and an unattended install
# write (appliance/selftest, appliance/install-from-cmdline). tty0 still gets
# the kernel's messages and getty@tty1 its VTY, so a screen shows what it
# always showed.
#
# GRUB itself on both as well, so the menu can be chosen without a screen.
# Inside an `if`: on a machine with no UART `serial` fails, and the terminals
# then stay the screen's instead of erroring on a port that is not there.
cat > "$WORK/iso/boot/grub/grub.cfg" <<'EOF'
if serial --unit=0 --speed=115200 --word=8 --parity=no --stop=1; then
    terminal_input console serial
    terminal_output console serial
fi
set default=0
set timeout=5
menuentry "Prisma Routing installer - start, then type 'install'" {
    linux /live/vmlinuz boot=live components quiet console=tty0 console=ttyS0,115200
    initrd /live/initrd
}
menuentry "Prisma Routing installer - go straight to the questions" {
    linux /live/vmlinuz boot=live components prisma-routing.install console=tty0 console=ttyS0,115200
    initrd /live/initrd
}
menuentry "Prisma Routing (shell instead of the VTY)" {
    linux /live/vmlinuz boot=live components prismash=off console=tty0 console=ttyS0,115200
    initrd /live/initrd
}
menuentry "Prisma Routing (recovery: single user, shell)" {
    linux /live/vmlinuz boot=live components single prismash=off console=tty0 console=ttyS0,115200
    initrd /live/initrd
}
EOF

# And again on the ISO tree, because the root is not the only thing that ends
# up on the medium. /run/live/medium/prisma-routing.answers - the path
# "prisma-routing.answers" reads with no argument - is the ROOT OF THE MEDIUM,
# which is this directory and not the squashfs. A check that looked only at the
# root would miss the one file the installer picks up without being asked.
say "no credentials on the medium"
sh "$REPO/appliance/no-credentials-in-image" "$WORK/iso" || exit 1

say "hybrid ISO"
# The volume label is what lsblk, blkid and a desktop file manager show for
# the stick this medium is written to, so it carries the product's name for
# the reason the filename does: the name on the stick reads as the name on
# the banner (#202). ISO 9660 spells a label in A-Z, 0-9 and _, 32 at most,
# so the two words take an underscore.
#
# It missed the rename once. The scope was measured with case-sensitive
# greps for Prisma Routing and prisma-routing, and this was the one line in the
# tree spelt in capitals, so a medium called prisma-routing-<v>.iso mounted
# as HEXAROUTING. Nothing on a disk reads the label and the medium is rebuilt
# for every release, so it moves with the filename: test/test_iso_ready.sh
# derives the label it expects from tools/version-stamp.sh --filename, the
# filename's one owner, and reads this line.
grub-mkrescue -o "$OUT" "$WORK/iso" -- -volid PRISMA_ROUTING

say "ready: $OUT ($(du -h "$OUT" | cut -f1))"
say_done
