#!/bin/sh
# Builds the upgrade artifact: the file an operator takes a new version from.
#
#   sh appliance/build-upgrade.sh     -> prisma-routing-<version>-upgrade.tar.gz
#
# Needs the plugin built (tools/build.sh plugin) and nothing else: no root, no
# debootstrap, no container. That is the whole difference between this and
# build-iso.sh, and it is why this can be built and verified on every release
# instead of on the ones somebody had half an hour for.
#
# WHAT IT IS. A tarball whose contents are a Prisma Routing source tree with the
# build already in it, so that appliance/install.sh - the script this
# repository has always called "for updating a box in production without
# reinstalling it" - can run out of it on a box that has no repository and no
# toolchain. Measured on a real appliance: neither is there (#295), which is
# why that path served the release gate and lab/upgrade.sh and nobody else.
#
# WHY IT IS THE TREE AND NOT A LIST OF THE THIRTY FILES install.sh INSTALLS.
# Because that list already exists twice and must not exist a third time.
# install.sh has no manifest of its own for exactly this reason - "it would be
# right on the day it was written and wrong the first time somebody adds an
# install line without thinking about it, and it would be wrong SILENTLY" -
# and build-iso.sh copies four whole directories into its chroot for the same
# reason. This copies the SAME FOUR, so there is one rule about what the
# install path is made of and one place to change it. The cost is measured and
# stated: about 3.5 MB of tree where the files install.sh actually copies come
# to 631 KB. Both numbers are noise beside the 325 MB medium this replaces.
#
# WHY IT INSTALLS WHAT IT HAS JUST PACKED, at the bottom of this file. An
# artifact that is missing one path is an artifact whose failure happens on an
# operator's box, at the hour they chose to upgrade, having already overwritten
# part of it - install.sh's preflight can refuse a missing plugin but nothing
# can refuse a tree that has no src/prismash/config.py until the line that copies
# it runs. That is #119's shape again: a component missing from the medium,
# invisible until an installed box needed it. So every artifact this script
# produces has been unpacked and installed once, into a throwaway directory,
# before this script will say it is ready.
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)

# The name, derived from the one that owns the identity rules.
#
# tools/version-stamp.sh --filename decides what a build of this tree may be
# called: the bare name only for a clean tree standing on the tag for its own
# VERSION, the commit otherwise, "-dirty" when it is not clean and
# "-unverified" when git cannot answer. tools/release-gate.sh refuses a release
# on those suffixes, and it has caught every release mistake this project has
# made. The artifact and the medium are two builds of one tree and must not be
# able to disagree about which tree, so the name comes from there and only the
# extension is this file's business.
ISO_NAME=$(sh "$REPO/tools/version-stamp.sh" --filename "$REPO")
DIR=${ISO_NAME%.iso}-upgrade
NAME=$DIR.tar.gz
OUT=${OUT:-$REPO/$NAME}

# The stamp the box will carry, computed here where there is a .git and written
# into the artifact, because there is none inside a tarball. build-iso.sh
# passes it into its chroot for the same reason.
STAMP=${PRISMA_VERSION_STAMP:-$(sh "$REPO/tools/version-stamp.sh" "$REPO")}

say() { echo "==> $*"; }

# THE PLUGINS, resolved and refused before anything is copied, in the two
# places install.sh and build-iso.sh both look. Both, and not one: they are one
# build product, tools/build.sh compiles them in a single ninja run, and a tree
# with one of them is a build that did not finish. #119 is what a medium
# without the vat2 plugin cost - the binary API unregistered on every box, so
# no CGNAT pool could be configured on the one thing the appliance exists for -
# and an artifact without it would do the same to a box that was working this
# morning.
if [ -f "$REPO/.build-vpp/lib/vpp_plugins/bpa_plugin.so" ]; then
  SO="$REPO/.build-vpp/lib/vpp_plugins/bpa_plugin.so"
elif [ -f /vpp_plugins/bpa_plugin.so ]; then
  SO=/vpp_plugins/bpa_plugin.so
else
  echo "bpa_plugin.so not found; run tools/build.sh plugin first" >&2
  exit 1
fi
# Optimised, or not shipped (#490): every release to 0.140.2 carried the
# plugin at -O0, 3.6x the clocks a packet, and nothing said so.
sh "$REPO/tools/plugin-optimised.sh" "$SO" >&2 || exit 1
if [ -f "$REPO/.build-vpp/lib/vat2_plugins/bpa_test_plugin_bpa_plugin.so" ]; then
  VSO="$REPO/.build-vpp/lib/vat2_plugins/bpa_test_plugin_bpa_plugin.so"
elif [ -f /vat2_plugins/bpa_test_plugin_bpa_plugin.so ]; then
  VSO=/vat2_plugins/bpa_test_plugin_bpa_plugin.so
else
  echo "bpa_test_plugin_bpa_plugin.so not found; run tools/build.sh plugin" >&2
  echo "first. Without it the binary API is unreachable and no pool can be" >&2
  echo "configured on the box this artifact would upgrade (#119)." >&2
  exit 1
fi

# THE PLATFORM PINS, READ OUT OF build-iso.sh rather than restated here.
#
# They are the versions of VPP and FRR the medium installs, so they are the
# versions the plugin in this artifact was compiled against, and appliance/
# upgrade refuses a box that runs anything else. Two copies of a number whose
# whole job is to be compared would eventually disagree, and the disagreement
# would read as an operator's box being wrong.
pin() {  # <variable> - the version appliance/build-iso.sh pins for it
  grep -m1 "^$1=" "$REPO/appliance/build-iso.sh" \
    | sed -e 's/^[A-Z_]*=//' -e 's/^${[A-Z_]*:-//' -e 's/}$//'
}
VPP_PIN=$(pin VPP_VERSION)
FRR_PIN=$(pin FRR_VERSION)
# An extraction that quietly comes back empty, or with the shell syntax still
# in it, would write a RELEASE nothing can compare against - and appliance/
# upgrade would then refuse every box, or accept every box, depending on which
# half broke. Renaming the variable in build-iso.sh has to fail HERE, where
# whoever renamed it is standing.
for p in "$VPP_PIN" "$FRR_PIN"; do
  case $p in
    ""|*'$'*|*'{'*|*'}'*)
      echo "could not read the VPP and FRR versions out of" >&2
      echo "appliance/build-iso.sh: got vpp '$VPP_PIN', frr '$FRR_PIN'." >&2
      echo "They are what the plugin in this artifact was compiled against," >&2
      echo "and without them appliance/upgrade cannot refuse a box running" >&2
      echo "another platform." >&2
      exit 1 ;;
  esac
done

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
STAGE=$WORK/$DIR
mkdir -p "$STAGE"

say "$STAMP"
say "platform: vpp $VPP_PIN, frr $FRR_PIN"

# THE SAME FOUR DIRECTORIES build-iso.sh copies into its chroot before running
# install.sh in it. test/test_upgrade_artifact.sh compares the two lists, so
# one of them moving without the other is red rather than an artifact that is
# missing whatever the new install line reads.
cp -a "$REPO/src" "$REPO/appliance" "$REPO/tools" "$REPO/docs" "$STAGE/"

# Where install.sh's FIRST branch looks. Putting them here rather than teaching
# install.sh a third location is the point: the script that upgrades an
# operator's box is byte for byte the script the release gate and
# lab/upgrade.sh have been driving, not a variant of it with a new branch that
# only ever runs on a machine nothing in this repository can reach.
mkdir -p "$STAGE/.build-vpp/lib/vpp_plugins" "$STAGE/.build-vpp/lib/vat2_plugins"
cp "$SO"  "$STAGE/.build-vpp/lib/vpp_plugins/bpa_plugin.so"
cp "$VSO" "$STAGE/.build-vpp/lib/vat2_plugins/bpa_test_plugin_bpa_plugin.so"

# THE CONTAINER IMAGES (#427), at the path install.sh looks for them in the
# tree it runs from - the same name tools/build-images.sh writes, so a source
# tree and an artifact are one case to install.sh rather than two.
#
# The maintainer's decision of 2026-09-24: the artifact carries them. Without
# them an upgrade could not move a box that runs the systemd units onto the
# containers, and a box ALREADY on the containers went on starting the
# previous release's dataplane image - whose plugin is the previous
# release's - under this release's VTY, told only by load-the-images that the
# images and the code came from different builds. The cost is the size: about
# 5 MB of artifact becomes about 120, on every release.
#
# REFUSED WITHOUT THEM, and not warned: build-iso.sh can afford to warn,
# because a medium's first boot is somebody watching it, and an upgrade is a
# box in service. PRISMA_SKIP_IMAGES=1 is the same deliberate knob
# build-iso.sh reads, for a build that knows it carries none.
IMAGES=$REPO/prisma-routing-images.tar.gz
if [ -f "$IMAGES" ]; then
  cp "$IMAGES" "$STAGE/prisma-routing-images.tar.gz"
  say "container images: $(( $(wc -c < "$IMAGES") / 1048576 )) MB"
elif [ "${PRISMA_SKIP_IMAGES:-0}" = 1 ]; then
  say "container images: skipped, PRISMA_SKIP_IMAGES=1"
else
  echo "no $IMAGES, so this artifact would carry no container images:" >&2
  echo "a box on the containers would go on starting the previous release's" >&2
  echo "dataplane under this release's code (#427). Build them first with" >&2
  echo "'sh tools/build-images.sh save', or set PRISMA_SKIP_IMAGES=1 to cut" >&2
  echo "an artifact that says it carries none." >&2
  exit 1
fi

{ printf 'stamp=%s\n' "$STAMP"
  printf 'vpp=%s\n'   "$VPP_PIN"
  printf 'frr=%s\n'   "$FRR_PIN"; } > "$STAGE/RELEASE"

# AND THE ENTRY POINT AT THE ROOT, where somebody who has just unpacked a
# tarball will look for it. A symlink and not a copy: the implementation stays
# in one file, and appliance/upgrade resolves the artifact by finding RELEASE
# rather than by counting directories up from itself, so it works through
# either name. Without this the wiki had to tell an operator to type a path
# into this product's source layout in order to upgrade their own box.
ln -s appliance/upgrade "$STAGE/upgrade"

# THE PROOF THAT THIS ARTIFACT IS COMPLETE, and it is an install and not a
# list. A list of what a tarball should contain is the two-lists problem again;
# running the real install.sh out of the real staged tree asks the only
# question that matters, which is whether every path it reaches for is there.
# A missing one stops it under `set -e` with the path in the message.
#
# Into a throwaway directory and as whoever is building: DESTDIR is what makes
# install.sh write into a root instead of onto this machine, and with it set
# the run creates no groups, enables no units and changes nobody's login shell.
# test/test_manifest.sh has driven it this way since it was written.
#
# appliance/upgrade is deliberately NOT what runs here. Its job is to refuse a
# box whose platform is not this one, and this machine is not a box; what is
# being asserted is the completeness of the tree, which is install.sh's
# question. test/test_upgrade_artifact.sh drives the other half.
say "installing it into a throwaway root to prove it is complete"
PROOF=$WORK/proof
# /etc/frr is the frr package's directory and install.sh edits the file in it
# in place rather than creating it - on a box it is there, put by the package
# the medium installed. Without it the run stops on "cannot create
# /etc/frr/daemons: Directory nonexistent", which is this fixture being
# incomplete rather than the artifact.
mkdir -p "$PROOF/etc/frr"
if ! env DESTDIR="$PROOF" PRISMA_VERSION_STAMP="$STAMP" \
     sh "$STAGE/appliance/install.sh" > "$WORK/proof.log" 2>&1; then
  echo "the artifact is incomplete: installing it failed." >&2
  tail -20 "$WORK/proof.log" >&2
  exit 1
fi
# The plugin, asserted on the installed root rather than on the staged copy,
# for the reason build-iso.sh gives for the same assertion: a copy step that
# stops copying and a chroot that silently did nothing are not missing files in
# the tree, and neither would reach install.sh's own refusal.
for f in usr/lib/x86_64-linux-gnu/vpp_plugins/bpa_plugin.so \
         usr/lib/x86_64-linux-gnu/vat2_plugins/bpa_test_plugin_bpa_plugin.so \
         usr/share/prisma-routing/VERSION usr/share/prisma-routing/MANIFEST; do
  [ -f "$PROOF/$f" ] || {
    echo "the install from this artifact left no /$f, so a box taking it" >&2
    echo "would not be the build this artifact claims to be." >&2
    exit 1
  }
done
[ "$(cat "$PROOF/usr/share/prisma-routing/VERSION")" = "$STAMP" ] || {
  echo "the install from this artifact stamped the box" >&2
  echo "'$(cat "$PROOF/usr/share/prisma-routing/VERSION")' and this artifact is $STAMP." >&2
  exit 1
}
say "  $(grep -c '^[^#]' "$PROOF/usr/share/prisma-routing/MANIFEST") file(s) installed, stamped $STAMP"

tar -czf "$OUT" -C "$WORK" "$DIR"

say "ready: $OUT ($(du -h "$OUT" | cut -f1))"
echo "    sha256 $(sha256sum "$OUT" | cut -d' ' -f1)"
echo
echo "On the box:"
echo "    tar xzf $NAME && cd $DIR && sudo sh upgrade"
