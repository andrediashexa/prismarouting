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
set -u
REPO=${1:-$(cd "$(dirname "$0")/.." && pwd)}

version=$(cat "$REPO/VERSION" 2>/dev/null || echo unknown)

if ! command -v git >/dev/null 2>&1; then
  printf '%s (no git at build time)\n' "$version"
  exit 0
fi

git config --global --add safe.directory "$REPO" >/dev/null 2>&1 || true

if ! git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
  printf '%s (not a git tree)\n' "$version"
  exit 0
fi

commit=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo "")
if [ -z "$commit" ]; then
  printf '%s (unverified)\n' "$version"
  exit 0
fi

if git -C "$REPO" diff --quiet HEAD 2>/dev/null; then
  printf '%s (%s)\n' "$version" "$commit"
else
  printf '%s (%s-dirty)\n' "$version" "$commit"
fi
