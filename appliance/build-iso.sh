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

say "repositorios de VPP e FRR"
chroot "$ROOT" /bin/sh -c "
  curl -s https://packagecloud.io/install/repositories/fdio/release/script.deb.sh | bash
  curl -s https://deb.frrouting.org/frr/keys.gpg -o /usr/share/keyrings/frrouting.gpg
  echo 'deb [signed-by=/usr/share/keyrings/frrouting.gpg] https://deb.frrouting.org/frr bookworm frr-stable' > /etc/apt/sources.list.d/frr.list
"

say "VPP $VPP_VERSION e FRR $FRR_VERSION"
chroot "$ROOT" /bin/sh -c "
  export DEBIAN_FRONTEND=noninteractive VPP_INSTALL_SKIP_SYSCTL=1
  apt-get update -qq
  apt-get install -y --no-install-recommends \
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
chroot "$ROOT" /bin/sh -c "cd /src && sh appliance/install.sh"
rm -rf "$ROOT/src"

say "box identity"
echo hexarouting > "$ROOT/etc/hostname"
printf '127.0.0.1 localhost\n127.0.1.1 hexarouting\n' > "$ROOT/etc/hosts"
chroot "$ROOT" /bin/sh -c "echo 'root:hexarouting' | chpasswd"
echo "PermitRootLogin yes" > "$ROOT/etc/ssh/sshd_config.d/hexarouting.conf"

say "squashfs"
chroot "$ROOT" /bin/sh -c "apt-get clean; rm -rf /var/lib/apt/lists/*"
KVER=$(basename "$(ls -d "$ROOT"/lib/modules/* | head -1)")
cp "$ROOT/boot/vmlinuz-$KVER"  "$WORK/iso/live/vmlinuz"
cp "$ROOT/boot/initrd.img-$KVER" "$WORK/iso/live/initrd"
# -mem and -processors bound mksquashfs: with xz and no limit it consumes
# memory proportional to the parallelism and takes the build machine down. The
# cost is a few extra seconds; the benefit is a build that fits in a modest
# VM.
mksquashfs "$ROOT" "$WORK/iso/live/filesystem.squashfs" \
  -e boot -comp xz -no-progress \
  -processors "${SQUASH_PROCS:-2}" -mem "${SQUASH_MEM:-768M}"

say "bootloader"
cat > "$WORK/iso/boot/grub/grub.cfg" <<'EOF'
set default=0
set timeout=5
menuentry "HexaRouting" {
    linux /live/vmlinuz boot=live components quiet
    initrd /live/initrd
}
menuentry "HexaRouting (modo de recuperacao)" {
    linux /live/vmlinuz boot=live components single
    initrd /live/initrd
}
EOF

say "ISO hibrida"
grub-mkrescue -o "$OUT" "$WORK/iso" -- -volid HEXAROUTING

say "pronto: $OUT ($(du -h "$OUT" | cut -f1))"
