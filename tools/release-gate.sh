#!/bin/sh
# Build the release medium from the TAG, boot it, and refuse anything else.
#
#   sh tools/release-gate.sh              the tag at HEAD
#   sh tools/release-gate.sh v0.26.0      a named tag
#
# CONTRIBUTING.md section 5 requires every release to ship an installation
# medium that has been built AND booted. This is that procedure as a command,
# because as a sequence somebody retypes it gets retyped wrong: in one evening
# the same person got it wrong three times, and every one was caught by a rule
# rather than by attention.
#
#   1. Built from the WORKING TREE, so the image carried uncommitted work from
#      the next issue and would have shipped as the release.
#   2. Built without the plugin, because a clean tree has no .build-vpp.
#   3. Built from a git WORKTREE, whose .git is a pointer to a repository the
#      build container does not have mounted - so git inside answered "not a
#      git tree" and the image came out stamped "unverified".
#
# A clone is what works: self-contained, so the container sees a whole
# repository and the version stamp can verify it.
set -eu

TAG=${1:-}
REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=${RELEASE_WORK:-/tmp/hexa-release}

if [ -z "$TAG" ]; then
  TAG=$(git -C "$REPO" describe --exact-match --tags HEAD 2>/dev/null || true)
  [ -n "$TAG" ] || {
    echo "error: HEAD is not on a tag, and a release is built from one." >&2
    echo "       Pass one: sh tools/release-gate.sh v0.26.0" >&2
    exit 1
  }
fi

echo "==> release gate for $TAG"

# --no-local so the clone is a real copy rather than hardlinks into the
# original .git, which is the same trap the worktree fell into.
rm -rf "$WORK"
git clone -q --no-local "$REPO" "$WORK"
git -C "$WORK" checkout -q "$TAG" 2>/dev/null || {
  echo "error: $TAG is not a tag in this repository." >&2
  echo "       Tag the release first, and push it." >&2
  exit 1
}

STAMP=$(sh "$WORK/tools/version-stamp.sh" "$WORK")
NAME=$(sh "$WORK/tools/version-stamp.sh" --filename "$WORK")
echo "    stamp: $STAMP"
echo "    image: $NAME"

# The refusal that makes this a gate rather than a script. A release image
# whose name carries a commit, "dirty" or "unverified" is by definition not
# the tagged tree - and the naming rule (#91) is what caught all three
# mistakes above, so it is what this checks.
case "$NAME" in
  *-dirty.iso|*-unverified.iso)
    echo "error: the tree does not verify as $TAG: $STAMP" >&2
    exit 1 ;;
esac
# The TAG against VERSION, not the name against VERSION.
#
# The first version of this check compared the image name with VERSION - and
# the name is BUILT from VERSION, so it agreed with itself and could never
# fail. A check that cannot fail is not a check, which is the defect this
# project keeps finding in its own tests, this time in the very script written
# to stop a release procedure going wrong.
#
# What is worth checking is that the tag and the file agree: a v0.27.0 tag on
# a tree whose VERSION says 0.26.0 produces an image called 0.26.0, published
# under a release called 0.27.0.
VER=$(tr -d '[:space:]' < "$WORK/VERSION")
if [ "$TAG" != "v$VER" ]; then
  echo "error: tag $TAG is on a tree whose VERSION is $VER." >&2
  echo "       The image would be called hexarouting-$VER.iso and published" >&2
  echo "       under $TAG. Fix VERSION or the tag before releasing." >&2
  exit 1
fi

# The same seam the installer has, and for the same reason: the checks above
# are what make this a gate, and a check that can only be exercised by building
# a 321 MB image is a check nobody exercises.
if [ "${RELEASE_GATE_CHECK_ONLY:-0}" = 1 ]; then
  echo "==> check only: $NAME verifies as $TAG, nothing was built"
  exit 0
fi

echo "==> building the plugin"
sh "$WORK/tools/build.sh" plugin >/dev/null

echo "==> building the medium"
( cd "$WORK" && make iso >/dev/null )
[ -f "$WORK/$NAME" ] || { echo "error: $NAME was not produced" >&2; exit 1; }

echo "==> booting it"
( cd "$WORK" && sh tools/lab.sh boot-iso ) > "$WORK/boot.log" 2>&1 || {
  echo "error: the medium did not boot. Last of the console:" >&2
  tail -20 "$WORK/boot.log" >&2
  exit 1
}
grep -q "HexaRouting ready" "$WORK/boot.log" || {
  echo "error: the medium booted and did not report ready." >&2
  grep -E "\[FAIL\]|\[warn\]" "$WORK/boot.log" | head -10 >&2
  exit 1
}

# The live image booting proves the medium is bootable. It does not prove the
# medium produces a working appliance, and those are different claims: the live
# image never runs the installed-box path.
#
# Two defects lived in that gap for the life of the project. The ISO never
# carried the vat2 plugin, so an installed box had no binary API and could not
# be configured to do CGNAT at all (#119). Behind it, hexarouting.service
# failed on the first boot of every install (#121). Neither was visible from a
# live boot, and both were found the first time this ran.
#
# So the gate installs to a disk and boots THAT, with the medium detached. The
# second boot is the assertion; a live image cannot make it.
echo "==> installing it to a disk and booting the disk"
( cd "$WORK" && make test-install ) > "$WORK/install.log" 2>&1 || {
  echo "error: the medium installed and the installed box failed its checks." >&2
  grep -E "\[FAIL\]" "$WORK/install.log" | head -10 >&2
  echo "       full console: $WORK/install.log" >&2
  exit 1
}

echo
echo "==> $NAME is ready to publish"
echo "    $WORK/$NAME"
echo "    sha256 $(sha256sum "$WORK/$NAME" | cut -d' ' -f1)"
grep -E "^\s+\[ ok \] HexaRouting|HexaRouting ready" "$WORK/boot.log" | sed 's/^/    /'
grep -E "\[ ok \] the installed box can configure a pool" "$WORK/install.log" \
  | sed 's/^/    /'
