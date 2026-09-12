#!/bin/sh
# Builds the installable HexaRouting ISO.
#
# Needs root and debootstrap; runs inside the build container with
# --privileged. The result is a hybrid ISO (BIOS and UEFI) booting a live
# system with VPP, FRR and HexaRouting already installed.
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=${WORK:-/tmp/hexa-iso}
# The name carries the version, so the file keeps its identity once it is off
# the release page. tools/version-stamp.sh owns the calculation - a second
# copy of it here would drift, and the drift would only show up as an ISO
# whose name disagrees with the version inside it.
OUT=${OUT:-$REPO/$(sh "$REPO/tools/version-stamp.sh" --filename "$REPO")}
SUITE=bookworm
MIRROR=http://deb.debian.org/debian
VPP_VERSION=${VPP_VERSION:-26.06-release}
FRR_VERSION=${FRR_VERSION:-10.7.1-0~deb12u1}

say() { echo "==> $*"; }
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
# medium therefore shipped without the binary API registered, and hexash
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

ROOT=$WORK/chroot
rm -rf "$WORK"
mkdir -p "$ROOT" "$WORK/iso/live" "$WORK/iso/boot/grub"

say "debootstrap $SUITE"
debootstrap --arch=amd64 --variant=minbase \
  --include=systemd-sysv,linux-image-amd64,live-boot,grub2-common,grub-pc-bin,grub-efi-amd64-bin,ca-certificates,curl,gnupg,iproute2,ifupdown,openssh-server,snmpd,python3,less,vim-tiny \
  "$SUITE" "$ROOT" "$MIRROR"

say "VPP and FRR repositories"
chroot "$ROOT" /bin/sh -c "
  curl -s https://packagecloud.io/install/repositories/fdio/release/script.deb.sh | bash
  curl -s https://deb.frrouting.org/frr/keys.gpg -o /usr/share/keyrings/frrouting.gpg
  echo 'deb [signed-by=/usr/share/keyrings/frrouting.gpg] https://deb.frrouting.org/frr bookworm frr-stable' > /etc/apt/sources.list.d/frr.list
"

say "VPP $VPP_VERSION and FRR $FRR_VERSION"
chroot "$ROOT" /bin/sh -c "
  export DEBIAN_FRONTEND=noninteractive VPP_INSTALL_SKIP_SYSCTL=1
  apt-get update -qq
  apt-get install -y --no-install-recommends \
    squashfs-tools dosfstools parted fdisk \
    vpp=$VPP_VERSION vpp-plugin-core=$VPP_VERSION vpp-plugin-dpdk=$VPP_VERSION \
    libvppinfra=$VPP_VERSION \
    frr=$FRR_VERSION frr-pythontools=$FRR_VERSION \
    frr-rpki-rtrlib=$FRR_VERSION
  apt-get clean
"

say "HexaRouting"
mkdir -p "$ROOT/src"
cp -a "$REPO/src" "$REPO/appliance" "$REPO/tools" "$REPO/docs" "$ROOT/src/"
mkdir -p "$ROOT/vpp_plugins" "$ROOT/vat2_plugins"
cp "$PLUGIN_SO" "$ROOT/vpp_plugins/"
cp "$VAT2_SO" "$ROOT/vat2_plugins/"
# The chroot cannot see the host's .git, so the stamp is computed here and
# passed in. Without it every ISO would declare itself "unknown".
if [ -z "${HEXA_VERSION_STAMP:-}" ]; then
  HEXA_VERSION_STAMP=$(sh "$REPO/tools/version-stamp.sh" "$REPO")
fi
say "version: $HEXA_VERSION_STAMP"
chroot "$ROOT" /bin/sh -c "cd /src && HEXA_VERSION_STAMP='$HEXA_VERSION_STAMP' HEXA_VTY_LOGIN=1 sh appliance/install.sh"
rm -rf "$ROOT/src"

# install.sh warns and carries on when it cannot find the vat2 plugin, which is
# right for a source install on a box that may not want the API and wrong for a
# medium. A warning in a build log this long is not a signal; this is.
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
  # hexarouting.service was still waiting for VPP. The first thing anybody does
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
# up: the self-test is ordered after hexarouting.service, whose ExecStartPre
# waits for VPP. A prompt offered before that invites an operator into a VTY
# that cannot read the box (#211).
After=hexarouting-selftest.service hexarouting-installer.service
Wants=hexarouting-selftest.service
EOF

  # Live image only. The installer removes this one and keeps the other.
  cat > "$d/20-autologin.conf" <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear %I \$TERM
EOF
done

say "box identity"
echo hexarouting > "$ROOT/etc/hostname"
printf '127.0.0.1 localhost\n127.0.1.1 hexarouting\n' > "$ROOT/etc/hosts"

# NO CREDENTIALS ON THE LIVE IMAGE.
#
# This used to set 'root:hexarouting' and permit root over SSH. Both travelled
# onto the disk: hexarouting-install unpacks this squashfs, /etc/shadow and
# all, and only asked for a password at the very end - so an install that was
# aborted, that failed late, or that ran from the kernel command line left a
# BOOTABLE disk with a password printed in the release notes.
#
# The account is locked instead. Console autologin does not authenticate, so
# nothing is lost on the live image, and the failure direction is now the safe
# one: an install that dies halfway leaves a disk nobody can log into rather
# than a disk anyone can. hexarouting-install sets the real password, from an
# answer the operator gave before it wrote anything.
say "locking the live image's root account"
chroot "$ROOT" passwd -l root >/dev/null
# Masked, not merely disabled: with a locked account sshd could not
# authenticate anyway, and masking leaves no listener to reason about. The
# installer unmasks it on the target and writes the sshd policy there.
chroot "$ROOT" systemctl mask ssh.service >/dev/null 2>&1 || true

say "squashfs"
chroot "$ROOT" /bin/sh -c "apt-get clean; rm -rf /var/lib/apt/lists/*"
KVER=$(basename "$(ls -d "$ROOT"/lib/modules/* | head -1)")
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
cat > "$WORK/iso/boot/grub/grub.cfg" <<'EOF'
set default=0
set timeout=5
menuentry "HexaRouting installer - start, then type 'install'" {
    linux /live/vmlinuz boot=live components quiet
    initrd /live/initrd
}
menuentry "HexaRouting installer - go straight to the questions" {
    linux /live/vmlinuz boot=live components hexarouting.install
    initrd /live/initrd
}
menuentry "HexaRouting (shell instead of the VTY)" {
    linux /live/vmlinuz boot=live components hexash=off
    initrd /live/initrd
}
menuentry "HexaRouting (recovery: single user, shell)" {
    linux /live/vmlinuz boot=live components single hexash=off
    initrd /live/initrd
}
EOF

say "ISO hibrida"
grub-mkrescue -o "$OUT" "$WORK/iso" -- -volid HEXAROUTING

say "pronto: $OUT ($(du -h "$OUT" | cut -f1))"
