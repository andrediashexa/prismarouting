#!/bin/sh
# Put a release where an operator can download it (#439).
#
#   sh tools/publish-release.sh vX.Y.Z [directory]
#
# WHY THIS EXISTS. This repository is private, so a GitHub release is a page
# no operator can open, and the wiki told them to download the upgrade
# package "from the release page". The public copy is
# https://hexarouting.hexanetworks.com.br/releases/<version>/, served read-only
# from the wiki's host, with `latest` pointing at the newest version. This
# puts one release there, and it is step 7 of the release procedure in
# CLAUDE.md, because a directory nobody updates stops being true at the next
# release.
#
# WHAT IT UPLOADS is what tools/release-gate.sh left in the directory (by
# default /tmp/prisma-release): the upgrade package, which every release has,
# and the ISO when there is one. Each goes up with a sha256 computed HERE,
# from the file being uploaded, and each is then READ BACK from the public URL
# and compared. A copy that was never checked from the reader's side is a
# claim, not a download.
#
# `latest` only moves forward. Publishing an old version again, to fill a gap,
# MUST NOT move every operator who follows `latest` back to it.
#
# NO HOST LIVES HERE, for the reason no token does: RELEASES_TARGET, or the
# one line in ~/.releases-target, names the upload account and host as
# user@host:/path. The account on the other side can write that directory
# and nothing else, and it is reached by key.
set -eu

TAG=${1:-}
SRC=${2:-/tmp/prisma-release}
URL=${RELEASES_URL:-https://hexarouting.hexanetworks.com.br/releases}
case "$TAG" in
  v[0-9]*.[0-9]*.[0-9]*) ;;
  *) echo "usage: sh tools/publish-release.sh vX.Y.Z [directory]" >&2; exit 2 ;;
esac
VER=${TAG#v}

TARGET=${RELEASES_TARGET:-}
if [ -z "$TARGET" ] && [ -r "$HOME/.releases-target" ]; then
  TARGET=$(head -1 "$HOME/.releases-target")
fi
case "$TARGET" in
  *@*:/*) ;;
  *)
    echo "error: no upload target, so NOTHING was published." >&2
    echo "       Set RELEASES_TARGET=user@host:/path, or put that one line" >&2
    echo "       in ~/.releases-target." >&2
    exit 2 ;;
esac
HOSTPART=${TARGET%%:*}
DIR=${TARGET#*:}

ART="prisma-routing-$VER-upgrade.tar.gz"
ISO="prisma-routing-$VER.iso"
if [ ! -f "$SRC/$ART" ]; then
  echo "error: $SRC/$ART is not there, so NOTHING was published." >&2
  echo "       Every release has an upgrade package; run the gate first:" >&2
  echo "       sh tools/release-gate.sh $TAG" >&2
  exit 2
fi
FILES=$ART
[ -f "$SRC/$ISO" ] && FILES="$FILES $ISO"

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir "$STAGE/$VER"
for f in $FILES; do
  cp "$SRC/$f" "$STAGE/$VER/"
  (cd "$STAGE/$VER" && sha256sum "$f" > "$f.sha256")
done

echo "publishing $VER: $FILES"
rsync -a --chmod=D755,F644 "$STAGE/$VER" "$TARGET/"

# Read back from where an operator reads, not from the upload's side.
bad=0
for f in $FILES; do
  want=$(cut -d" " -f1 "$STAGE/$VER/$f.sha256")
  got=$(curl -fsL --max-time 30 "$URL/$VER/$f.sha256" | cut -d" " -f1) || got=""
  if [ "$got" = "$want" ]; then
    echo "  $URL/$VER/$f.sha256 matches"
  else
    echo "error: $URL/$VER/$f.sha256 says '${got:-nothing}', not '$want'" >&2
    bad=1
  fi
done
# The package itself is a few megabytes, so it is read back whole. The ISO
# is hundreds, so its size is compared instead.
got=$(curl -fsL --max-time 120 "$URL/$VER/$ART" | sha256sum | cut -d" " -f1)
if [ "$got" = "$(cut -d" " -f1 "$STAGE/$VER/$ART.sha256")" ]; then
  echo "  $URL/$VER/$ART downloads and verifies"
else
  echo "error: $URL/$VER/$ART does not download as the file uploaded" >&2
  bad=1
fi
if [ -f "$SRC/$ISO" ]; then
  size=$(curl -fsIL --max-time 30 "$URL/$VER/$ISO" \
         | tr -d '\r' | awk 'tolower($1)=="content-length:"{v=$2} END{print v}')
  if [ "${size:-0}" = "$(wc -c < "$SRC/$ISO" | tr -d ' ')" ]; then
    echo "  $URL/$VER/$ISO is served at its full size"
  else
    echo "error: $URL/$VER/$ISO is served at ${size:-no} bytes" >&2
    bad=1
  fi
fi
if [ "$bad" = 1 ]; then
  echo "error: $VER is on the host but does not read back; latest NOT moved." >&2
  exit 1
fi

# Forward only. `sort -V` orders 0.122.10 after 0.122.9, which a string
# comparison does not.
cur=$(ssh -o BatchMode=yes "$HOSTPART" "readlink '$DIR/latest'" 2>/dev/null || true)
top=$(printf '%s\n%s\n' "${cur:-0.0.0}" "$VER" | sort -V | tail -1)
if [ "$top" = "$VER" ] && [ "$cur" != "$VER" ]; then
  ssh -o BatchMode=yes "$HOSTPART" "ln -sfn '$VER' '$DIR/latest'"
  echo "  latest: ${cur:-none} -> $VER"
else
  echo "  latest stays at ${cur:-none}: $VER is not newer"
fi
echo "published: $URL/$VER/"
