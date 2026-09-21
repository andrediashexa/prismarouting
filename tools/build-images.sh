#!/bin/sh
# Builds the two runtime images and, given `save`, writes the file a medium
# would carry.
#
#   sh tools/build-images.sh          -> prisma-vpp:<version>, prisma-frr:<version>
#   sh tools/build-images.sh save     -> prisma-routing-images.tar.gz
#   sh tools/build-images.sh check    -> starts them and asks what they hold
#
# WHAT THIS IS FOR, stated so it is not mistaken for the appliance changing
# shape: #396 phase 1 asks for the images to exist and to be shippable. The
# box still runs VPP and FRR as systemd units, and nothing here is started by
# anything. Phase 2 is what gives them a job.
#
# The sizes are printed because the size IS the phase-1 result. Measured on
# 0.111.2: 211 MB for the dataplane image, 216 MB for the control plane, and
# 118 MB for the two of them saved and gzipped - against a 325 MB medium that
# already installs both sets of packages into its own root. Shipping them
# inside it is a 36% bigger medium carrying a second copy of what it has.
set -e

REPO=$(cd "$(dirname "$0")/.." && pwd)
VERSION=$(cat "$REPO/VERSION")
TARGET=${1:-build}

# The same re-exec tools/build.sh does: on this VM the docker group only
# takes effect in a new login.
if ! docker ps >/dev/null 2>&1; then
  exec sg docker -c "'$0' '$TARGET'"
fi

# The pins come from appliance/build-iso.sh and from nowhere else, so the
# images and the medium cannot drift apart. build-upgrade.sh reads the same
# two lines for the same reason.
pin() {
  grep -m1 "^$1=" "$REPO/appliance/build-iso.sh" \
    | sed 's/.*:-//; s/}.*//'
}
VPP_VERSION=$(pin VPP_VERSION)
FRR_VERSION=$(pin FRR_VERSION)
if [ -z "$VPP_VERSION" ] || [ -z "$FRR_VERSION" ]; then
  echo "error: could not read the VPP and FRR pins out of" >&2
  echo "       appliance/build-iso.sh: got vpp '$VPP_VERSION'," >&2
  echo "       frr '$FRR_VERSION'. The images would then be built" >&2
  echo "       against whatever the repositories hold today, which is" >&2
  echo "       the drift the pins exist to stop." >&2
  exit 1
fi

# THE PLUGIN THIS REPOSITORY COMPILED, not one the image builds for itself.
# A plugin built inside the dataplane image would be a second compile against
# a second apt resolution, and the whole reason the version is pinned is that
# a plugin and a VPP that disagree do not load.
PLUGIN_SO=$REPO/.build-vpp/lib/vpp_plugins/bpa_plugin.so
if [ ! -f "$PLUGIN_SO" ]; then
  echo "error: $PLUGIN_SO is not there, so the dataplane image would" >&2
  echo "       carry VPP and no CGNAT at all - which starts and forwards" >&2
  echo "       nothing this product exists for." >&2
  echo "       Build it:  sh tools/build.sh plugin" >&2
  exit 1
fi

CTX=$(mktemp -d)
trap 'rm -rf "$CTX"' EXIT
mkdir -p "$CTX/vpp_plugins"
cp "$PLUGIN_SO" "$CTX/vpp_plugins/"

echo "==> dataplane image, VPP $VPP_VERSION with this tree's plugin"
docker build -q -f "$REPO/build/Dockerfile.vpp" \
  --build-arg "VPP_VERSION=$VPP_VERSION" \
  -t "prisma-vpp:$VERSION" "$CTX" > /dev/null

echo "==> control-plane image, FRR $FRR_VERSION"
docker build -q -f "$REPO/build/Dockerfile.frr" \
  --build-arg "FRR_VERSION=$FRR_VERSION" \
  -t "prisma-frr:$VERSION" "$CTX" > /dev/null

# BYTES, and the megabyte is left to the one place that prints it. A size()
# that formatted its own answer would give docs/appliance.md's measured table
# a second opinion about what a megabyte is.
size() {
  docker image inspect "$1" --format '{{.Size}}'
}
printf '    prisma-vpp:%s  %s MB\n' "$VERSION" "$(( $(size "prisma-vpp:$VERSION") / 1048576 ))"
printf '    prisma-frr:%s  %s MB\n' "$VERSION" "$(( $(size "prisma-frr:$VERSION") / 1048576 ))"

if [ "$TARGET" = check ]; then
  # WHAT THE IMAGE HOLDS, asked of the image rather than of the Dockerfile.
  # test/test_image_pins.py reads the file and can say what was written; only
  # a started container can say what apt resolved, what the plugin loads
  # against and whether a compiler came along for the ride.
  fails=0
  bad() { echo "  [FAIL] $*"; fails=$((fails + 1)); }
  ok()  { echo "  [ ok ] $*"; }

  echo "==> the dataplane image, started"
  out=$(docker run --rm --privileged "prisma-vpp:$VERSION" sh -c '
    mkdir -p /run/vpp
    cat > /tmp/check.conf <<CONF
unix { nodaemon cli-listen /run/vpp/cli.sock }
api-segment { gid 0 }
plugins { plugin default { disable } plugin bpa_plugin.so { enable } }
CONF
    vpp -c /tmp/check.conf >/dev/null 2>&1 &
    sleep 6
    vppctl show plugins 2>/dev/null | grep -i bpa_plugin
    vppctl show version 2>/dev/null | head -1' 2>/dev/null)

  # THE PLUGIN LOADS, which is the whole point of the image. An image with
  # VPP and no CGNAT starts, listens, forwards, and is not this product.
  if echo "$out" | grep -q "bpa_plugin.so"; then
    ok "the bpa plugin is loaded"
  else
    bad "the image started VPP and the bpa plugin is not among its plugins"
  fi

  # AND IT IS THIS TREE'S PLUGIN. The stamp is the version and the commit,
  # and an image built around a plugin from some other build is an image
  # whose CGNAT is not the one in this checkout.
  # ONE WORD PER HOLE. The stamp is "<version> (<commit>)" and printing it
  # whole puts three words inside one slot of the row - which is how a page
  # quoting this screen keeps a word the tool stopped printing, and what
  # test/test_wiki_screens.py counts and refuses.
  # WITHOUT THE -dirty SUFFIX, and this is not a loosening. The stamp is
  # `<version> (<commit>)`, and version-stamp.sh appends `-dirty` when the
  # working tree has uncommitted changes - which is every moment somebody is
  # working. Requiring it to match would make this check red on the developer
  # who just ran it and green only on a clean checkout, so the tree's state
  # is SAID on its own row instead of failing a row that is about the image.
  stamp=$(sh "$REPO/tools/version-stamp.sh" "$REPO")
  sver=${stamp%% *}
  scommit=$(echo "$stamp" | sed -n 's/.*(\(.*\))/\1/p')
  clean=${scommit%-dirty}
  if echo "$out" | grep -q "$sver ($clean"; then
    ok "and it is this tree's plugin: $sver, commit $clean"
  else
    got=$(echo "$out" | sed -n 's/.*bpa_plugin.so  *\([^ ]*\) *(\([^)]*\)).*/\1 \2/p' | head -1)
    bad "the plugin in the image is stamped '$got' and this tree is '$sver $clean'"
  fi
  if [ "$scommit" != "$clean" ]; then
    ok "the tree has uncommitted changes, so only the commit is compared"
  fi

  if echo "$out" | grep -q "vpp v$VPP_VERSION"; then
    ok "VPP is the pinned $VPP_VERSION"
  else
    bad "the image resolved a VPP that is not the pin: $(echo "$out" | grep -m1 '^vpp v')"
  fi

  # NO COMPILER, and this is not tidiness. build/Dockerfile.dev exists to
  # build things and carries gcc, cmake, ninja and vpp-dev; an appliance
  # image that carries them ships a build environment to every customer, and
  # the first person to notice will be the one asking why the dataplane can
  # compile.
  for tool in gcc cc cmake ninja; do
    if docker run --rm "prisma-vpp:$VERSION" sh -c "command -v $tool" >/dev/null 2>&1; then
      bad "the dataplane image carries $tool: it is a runtime image and this is build/Dockerfile.dev's job"
    fi
  done
  [ "$fails" -eq 0 ] && ok "and no compiler came with it"

  echo "==> the control-plane image, asked its version"
  # /usr/lib/frr, not the PATH: FRR installs its daemons there and puts none
  # of them on the PATH, so `bgpd --version` is "executable file not found"
  # and reads like an image without FRR in it.
  frr=$(docker run --rm "prisma-frr:$VERSION" /usr/lib/frr/bgpd --version 2>&1 | head -1)
  # The package pin carries a Debian revision (10.7.1-0~deb12u1) and the
  # daemon prints the upstream version (10.7.1), so the comparison is against
  # the part they share rather than against the whole string.
  if echo "$frr" | grep -q "${FRR_VERSION%%-*}"; then
    ok "FRR is the pinned ${FRR_VERSION%%-*}"
  else
    bad "the image resolved an FRR that is not the pin: $frr"
  fi

  if [ "$fails" -eq 0 ]; then
    echo "IMAGES PASSED"
  else
    echo "IMAGES FAILED: $fails check(s)"
    exit 1
  fi
  exit 0
fi

if [ "$TARGET" = save ]; then
  OUT=${OUT:-$REPO/prisma-routing-images.tar.gz}
  echo "==> saving both, which is what a medium would carry"
  # ONE save for the two images: they share the debian:12 base, and saving
  # them separately writes it twice. Measured: 84 MB for the dataplane alone
  # and 118 MB for both, not 84 + 90.
  docker save "prisma-vpp:$VERSION" "prisma-frr:$VERSION" | gzip -1 > "$OUT"
  printf '    %s  %s MB\n' "$OUT" "$(( $(wc -c < "$OUT") / 1048576 ))"
fi
