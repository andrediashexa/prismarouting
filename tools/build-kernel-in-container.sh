#!/bin/sh
# The half of tools/build-kernel.sh that runs inside debian:bookworm (#449).
# A file of its own and not a quoted string handed to `sh -c` inside a quoted
# string handed to `sg -c`: two builds were lost to that nesting, once to an
# apostrophe in a comment that closed the quote.
#
# Environment: VER (Debian source version), ABI (Debian's ABI name for it),
# OWNER (uid:gid the results are handed back to). Writes into /out; reads the
# classes to cut from /out/cut.txt.
set -eu
export DEBIAN_FRONTEND=noninteractive
sed -i "s/^Types: deb$/Types: deb deb-src/" /etc/apt/sources.list.d/debian.sources
apt-get update -qq
apt-get install -y -qq build-essential bc bison flex libssl-dev libelf-dev \
    dpkg-dev rsync kmod cpio python3 dwarves debhelper >/dev/null
cd /tmp
apt-get source -qq "linux=$VER" >/dev/null
apt-get download -qq "linux-image-$ABI-amd64-unsigned" 2>/dev/null \
  || apt-get download -qq "linux-image-$ABI-amd64"
dpkg-deb -x linux-image-"$ABI"-amd64*.deb img
find /tmp/img/lib/modules -name "*.ko*" | sed "s|.*/kernel/||; s|\.ko.*||" \
  | sort > /out/debian-modules.txt
echo "debian: $(wc -l < /out/debian-modules.txt) modules, $(du -sm /tmp/img/lib/modules | cut -f1) MB" \
  > /out/measure.txt

cd linux-*/
cp "/tmp/img/boot/config-$ABI-amd64" .config
# Debian signs its modules with its own key, which is not here. A key of
# this build is generated instead; the image is not Secure Boot signed and
# never was on this medium.
scripts/config --set-str MODULE_SIG_KEY certs/signing_key.pem \
               --set-str SYSTEM_TRUSTED_KEYS "" \
               --set-str SYSTEM_REVOCATION_KEYS "" \
               --disable DEBUG_INFO_BTF --enable DEBUG_INFO_NONE \
               --disable DEBUG_INFO_DWARF5 \
               --disable DEBUG_INFO_DWARF_TOOLCHAIN_DEFAULT
while read -r opt why; do scripts/config --disable "$opt"; done < /out/cut.txt
make olddefconfig >/dev/null
cp .config /out/config

start=$(date +%s)
make -j"$(nproc)" bindeb-pkg LOCALVERSION=-prisma KDEB_PKGVERSION="$VER.prisma1" \
    > /out/build.log 2>&1 || { tail -40 /out/build.log; exit 1; }
echo $(( $(date +%s) - start )) > /out/build-seconds
cp /tmp/linux-image-*-prisma_*.deb /out/
du -sm /tmp/linux-image-*-prisma_*.deb | cut -f1 > /out/deb-mb
chown -R "$OWNER" /out
