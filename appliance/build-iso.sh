#!/bin/sh
# Builds the installable HexaRouting ISO.
#
# Needs root and debootstrap; runs inside the build container with
# --privileged. The result is a hybrid ISO (BIOS and UEFI) booting a live
# system with VPP, FRR and HexaRouting already installed.
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=${WORK:-/tmp/hexa-iso}
OUT=${OUT:-$REPO/hexarouting.iso}
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

ROOT=$WORK/chroot
rm -rf "$WORK"
mkdir -p "$ROOT" "$WORK/iso/live" "$WORK/iso/boot/grub"

say "debootstrap $SUITE"
debootstrap --arch=amd64 --variant=minbase \
  --include=systemd-sysv,linux-image-amd64,live-boot,grub2-common,grub-pc-bin,grub-efi-amd64-bin,ca-certificates,curl,gnupg,iproute2,ifupdown,openssh-server,python3,less,vim-tiny \
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
    frr=$FRR_VERSION frr-pythontools=$FRR_VERSION
  apt-get clean
"

say "HexaRouting"
mkdir -p "$ROOT/src"
cp -a "$REPO/src" "$REPO/appliance" "$REPO/tools" "$REPO/docs" "$ROOT/src/"
mkdir -p "$ROOT/vpp_plugins"
cp "$PLUGIN_SO" "$ROOT/vpp_plugins/"
# The chroot cannot see the host's .git, so the stamp is computed here and
# passed in. Without it every ISO would declare itself "unknown".
if [ -z "${HEXA_VERSION_STAMP:-}" ]; then
  HEXA_VERSION_STAMP=$(sh "$REPO/tools/version-stamp.sh" "$REPO")
fi
say "version: $HEXA_VERSION_STAMP"
chroot "$ROOT" /bin/sh -c "cd /src && HEXA_VERSION_STAMP='$HEXA_VERSION_STAMP' HEXA_VTY_LOGIN=1 sh appliance/install.sh"
rm -rf "$ROOT/src"

# Autologin on the console, for the LIVE image only. A racked appliance is
# reached by serial console or IPMI, and a live lab image that stops at a login
# prompt does not show what it is. On an INSTALLED box this would mean physical
# or IPMI access equals full access with no password, which is a decision for
# the operator, not a default - so install.sh never does this.
say "console autologin (live image only)"
for tty in ttyS0 tty1; do
  d="$ROOT/etc/systemd/system/serial-getty@$tty.service.d"
  [ "$tty" = tty1 ] && d="$ROOT/etc/systemd/system/getty@$tty.service.d"
  mkdir -p "$d"
  cat > "$d/autologin.conf" <<EOF
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
