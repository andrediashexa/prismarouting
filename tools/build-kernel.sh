#!/bin/sh
# Prisma Routing's kernel (#449): DEBIAN'S kernel, renamed, with the module
# classes a router has no use for left out.
#
#   sh tools/build-kernel.sh [debian source version]   (default: KERNEL_DEB_VERSION)
#
# NOT A DIFFERENT KERNEL. The source is Debian's own `linux` source package at
# the version the medium already ships, with Debian's patches as the package
# applies them, and the configuration is Debian's own for that build
# (/boot/config-<abi>-amd64 out of the matching linux-image package). Two
# things change and nothing else:
#
#   * the name: LOCALVERSION=-prisma, so `uname -r` says so;
#   * the module set: the classes in CUT below are switched off, each with
#     what it is and why no box forwards on it.
#
# KEPT, BY RULE AND NOT BY LIST: every NIC, storage, virtio, vfio, uio and
# hypervisor driver, and InfiniBand/RDMA, which mlx5's DPDK driver sits on.
# The box installs on hardware nobody here has seen; one missing NIC or disk
# driver is a box that does not come up. 0.132.0 showed what a missing NIC
# driver costs in the initramfs alone (#468).
#
# Built in a debian:bookworm container, so the compiler is Debian's too.
# Cached by source version under /var/cache/prisma/kernel/<version>/, never in
# the tree: the gate clones a fresh tree and a cache inside it would be deleted
# before it was hit (CLAUDE.md).
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
. "$REPO/appliance/kernel.env"
VER=${1:-$KERNEL_DEB_VERSION}
OUT=${KERNEL_CACHE:-/var/cache/prisma/kernel}/$VER
mkdir -p "$OUT"
# One sort order for every list compared below: the container's and the
# host's locales differ, and comm refused the first clean build over it.
export LC_ALL=C

# What is switched off. One class per line: the option, then why.
CUT='SOUND                 sound cards: nothing on this box makes a sound
WLAN                  Wi-Fi adapters: a router here forwards on wire
CFG80211              the Wi-Fi stack under them
MAC80211              and the soft-MAC layer
BT                    Bluetooth
NFC                   near-field communication
MEDIA_SUPPORT         cameras, TV tuners, video capture (V4L, DVB)
INPUT_JOYSTICK        joysticks and game pads
INPUT_TABLET          graphics tablets
INPUT_TOUCHSCREEN     touchscreens
HAMRADIO              amateur radio
CAN                   CAN bus, the automotive and industrial field bus
IEEE802154            low-rate wireless PANs (Zigbee and kin)
WWAN                  cellular modems
FIREWIRE              IEEE 1394
IIO                   industrial sensors: accelerometers, ADCs, light meters
DRM_AMDGPU            AMD discrete GPUs; the console needs none
DRM_RADEON            older AMD/ATI discrete GPUs
DRM_NOUVEAU           NVIDIA discrete GPUs
STAGING               drivers the kernel itself calls not ready'

# BUILT ONLY WHEN ABSENT, CHECKED EVERY TIME. The package lands in the cache
# before the checks below run, so a cache hit that skipped them would ship a
# kernel nothing had checked. The checks cost seconds.
if ! ls "$OUT"/linux-image-*-prisma_*.deb >/dev/null 2>&1; then
  echo "$CUT" > "$OUT/cut.txt"
  # The build itself, in a file of its own: tools/build-kernel-in-container.sh.
  sg docker -c "docker run --rm -v '$OUT:/out' -v '$REPO/tools:/tools:ro' \
      -e VER='$VER' -e ABI='$KERNEL_DEB_ABI' -e OWNER='$(id -u):$(id -g)' \
      debian:bookworm sh /tools/build-kernel-in-container.sh"
else
  echo "cached: $(ls "$OUT"/linux-image-*-prisma_*.deb), checked again below"
fi
sort -o "$OUT/debian-modules.txt" "$OUT/debian-modules.txt"

# THE RULE, AS A REFUSAL. Every module Debian ships under a directory a box
# can need to come up must be in ours, or this build is refused before the
# medium is made from it. The cut classes' own directories are the exception,
# and they are named here rather than matched loosely.
dpkg-deb -c "$OUT"/linux-image-*-prisma_*.deb | awk '{print $NF}' \
  | grep '\.ko' | sed 's|.*/kernel/||; s|\.ko.*||' | sort > "$OUT/prisma-modules.txt"
MUST='^drivers/(net|scsi|nvme|ata|block|md|virtio|vfio|uio|infiniband|hv|xen|pci|usb/storage|usb/host|mmc|firmware|iommu|acpi)/'
# The cut classes' own directories, and one helper only they load:
# cs_dsp is the firmware loader for Cirrus Logic's audio DSPs, pulled in by
# sound codecs alone - measured as the one module of the MUST set missing on
# the first build, with SOUND off.
CUT_DIRS='^drivers/net/(wireless|wwan|can|ieee802154|hamradio)/|^drivers/firmware/cirrus/'
grep -E "$MUST" "$OUT/debian-modules.txt" | grep -Ev "$CUT_DIRS" \
  | comm -23 - "$OUT/prisma-modules.txt" > "$OUT/missing.txt"
if [ -s "$OUT/missing.txt" ]; then
  echo "REFUSED: $(wc -l < "$OUT/missing.txt") module(s) a box may need to come up are not in this kernel:"
  head -40 "$OUT/missing.txt"
  rm -f "$OUT"/linux-image-*-prisma_*.deb
  exit 1
fi
echo "every NIC, storage, virtio, vfio, uio, RDMA and hypervisor module Debian ships is here"
# NO PRIVATE KEY IN THE PACKAGE. The modules are signed at build time with a
# key generated for this build (certs/signing_key.pem); what ships must be the
# signatures, never the key, or anybody holding the medium could sign a module
# this kernel loads.
if dpkg-deb -c "$OUT"/linux-image-*-prisma_*.deb | grep -Eq '\.(pem|priv|key)$|signing_key'; then
  echo "REFUSED: the kernel package carries key material:"
  dpkg-deb -c "$OUT"/linux-image-*-prisma_*.deb | grep -E '\.(pem|priv|key)$|signing_key'
  rm -f "$OUT"/linux-image-*-prisma_*.deb
  exit 1
fi
sed -i '/^prisma:/d' "$OUT/measure.txt"
dpkg-deb -c "$OUT"/linux-image-*-prisma_*.deb | grep -c '\.ko' \
  | sed 's/^/prisma: /; s/$/ modules/' >> "$OUT/measure.txt"
echo "built in $(cat "$OUT/build-seconds")s:"
cat "$OUT/measure.txt"
ls -la "$OUT"/*.deb
