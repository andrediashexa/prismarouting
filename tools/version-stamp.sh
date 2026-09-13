#!/bin/sh
# Prints the version stamp of this tree.
#
# One copy of this calculation in build-iso.sh and another in install.sh would
# diverge, and the divergence would only surface in an ISO that lies about its
# own version. So there is one place.
#
# The delicate part is git refusing the repository: inside the build container
# it answers "detected dubious ownership" because the bind mount belongs to
# another user. Swallowing that error makes a DIRTY tree come out stamped as a
# clean release - the opposite of what this file exists to guarantee. So the
# failure becomes "unverified", which is visible.
#
# With --filename it prints the name an installation medium built from this
# tree MUST carry. The two live together because they are one calculation with
# two spellings, and the reason the file says "so there is one place".
set -u
MODE=stamp
if [ "${1:-}" = "--filename" ]; then
  MODE=filename
  shift
fi
REPO=${1:-$(cd "$(dirname "$0")/.." && pwd)}

version=$(cat "$REPO/VERSION" 2>/dev/null || echo unknown)

# The name of an image built from this tree.
#
# An ISO called "hexarouting.iso" on a USB stick, or in somebody's downloads,
# says nothing about what it is - and two of them are indistinguishable. The
# version goes in the name so that the file keeps its identity after it leaves
# the release page.
#
# A build that is NOT the released tree carries its commit as well, and a
# dirty one says so. That is the same argument one step further: two images of
# 0.23.0 built from different working trees would otherwise share a name, and
# the one an operator must never mistake for a release is precisely the one
# somebody built from uncommitted changes.
iso_name() {
  _v=$1
  _extra=$2
  if [ -n "$_extra" ]; then
    printf 'hexarouting-%s-%s.iso\n' "$_v" "$_extra"
  else
    printf 'hexarouting-%s.iso\n' "$_v"
  fi
}

if ! command -v git >/dev/null 2>&1; then
  if [ "$MODE" = filename ]; then iso_name "$version" "unverified"; exit 0; fi
  printf '%s (no git at build time)\n' "$version"
  exit 0
fi

# Only when it is not already there. "--add" appends unconditionally, and this
# script runs on every build, every release gate, every lab that stamps an
# image and every make test - so the entry landed once per run, for ever, in a
# file this product does not own. Measured before the fix: 2325 copies of the
# same line in a ~/.gitconfig with four other lines in it, and an earlier
# reading found 12710 entries for clones that no longer exist (#220). git
# parses the whole configuration on every invocation in every repository, and
# a list of allowed directories nobody can read is one nobody audits.
#
# --get-all rather than --get: with duplicates already present, --get fails
# with "multiple values" on some versions, which would read as "not there" and
# add another.
if ! git config --global --get-all safe.directory 2>/dev/null \
     | grep -qxF "$REPO"; then
  git config --global --add safe.directory "$REPO" >/dev/null 2>&1 || true
fi

if ! git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
  if [ "$MODE" = filename ]; then iso_name "$version" "unverified"; exit 0; fi
  printf '%s (not a git tree)\n' "$version"
  exit 0
fi

commit=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo "")
if [ -z "$commit" ]; then
  if [ "$MODE" = filename ]; then iso_name "$version" "unverified"; exit 0; fi
  printf '%s (unverified)\n' "$version"
  exit 0
fi

if git -C "$REPO" diff --quiet HEAD 2>/dev/null; then
  clean=1
else
  clean=0
fi

if [ "$MODE" = filename ]; then
  # The bare name is reserved for the tree that IS the release: clean, and
  # standing exactly on the tag for the version it claims. Anything else says
  # which tree it came from, because anything else is not that release.
  tag=$(git -C "$REPO" describe --exact-match --tags HEAD 2>/dev/null || echo "")
  if [ "$clean" = 1 ] && [ "$tag" = "v$version" ]; then
    iso_name "$version" ""
  elif [ "$clean" = 1 ]; then
    iso_name "$version" "$commit"
  else
    iso_name "$version" "$commit-dirty"
  fi
  exit 0
fi

if [ "$clean" = 1 ]; then
  printf '%s (%s)\n' "$version" "$commit"
else
  printf '%s (%s-dirty)\n' "$version" "$commit"
fi
