#!/bin/sh
# Monta a ISO instalavel do HexaRouting.
#
# Precisa de root e de debootstrap; roda dentro do container de build com
# --privileged. O resultado e uma ISO hibrida (BIOS e UEFI) que sobe um
# sistema vivo com VPP, FRR e HexaRouting ja instalados.
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=${WORK:-/tmp/hexa-iso}
OUT=${OUT:-$REPO/hexarouting.iso}
SUITE=bookworm
MIRROR=http://deb.debian.org/debian
VPP_VERSION=${VPP_VERSION:-26.06-release}
FRR_VERSION=${FRR_VERSION:-10.7.1-0~deb12u1}

say() { echo "==> $*"; }
[ "$(id -u)" = 0 ] || { echo "precisa ser root" >&2; exit 1; }

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
if [ -f /vpp_plugins/bpa_plugin.so ]; then
  mkdir -p "$ROOT/vpp_plugins"
  cp /vpp_plugins/bpa_plugin.so "$ROOT/vpp_plugins/"
fi
chroot "$ROOT" /bin/sh -c "cd /src && sh appliance/install.sh"
rm -rf "$ROOT/src"

say "identidade da caixa"
echo hexarouting > "$ROOT/etc/hostname"
printf '127.0.0.1 localhost\n127.0.1.1 hexarouting\n' > "$ROOT/etc/hosts"
chroot "$ROOT" /bin/sh -c "echo 'root:hexarouting' | chpasswd"
echo "PermitRootLogin yes" > "$ROOT/etc/ssh/sshd_config.d/hexarouting.conf"

say "squashfs"
chroot "$ROOT" /bin/sh -c "apt-get clean; rm -rf /var/lib/apt/lists/*"
KVER=$(basename "$(ls -d "$ROOT"/lib/modules/* | head -1)")
cp "$ROOT/boot/vmlinuz-$KVER"  "$WORK/iso/live/vmlinuz"
cp "$ROOT/boot/initrd.img-$KVER" "$WORK/iso/live/initrd"
mksquashfs "$ROOT" "$WORK/iso/live/filesystem.squashfs" -e boot -comp xz -no-progress

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
grub-mkrescue -o "$OUT" "$WORK/iso" -- -volid HEXAROUTING 2>/dev/null

say "pronto: $OUT ($(du -h "$OUT" | cut -f1))"
