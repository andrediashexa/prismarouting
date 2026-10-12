#!/bin/sh
# Put a release where an operator can download it (#439).
#
#   sh tools/publish-release.sh vX.Y.Z [directory]
#
# WHY THIS EXISTS. This repository is private, so a GitHub release is a page
# no operator can open, and the wiki told them to download the upgrade
# package "from the release page". The public copy is
# https://prismarouting.com.br/releases/<version>/, served read-only
# from the wiki's host, with `latest/` offering the newest upgrade package and
# the newest ISO. This
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
# MUST NOT move every operator who follows `latest` back to it. And it offers
# a medium after a patch release too (#604): see the end of this file.
#
# NO HOST LIVES HERE, for the reason no token does: RELEASES_TARGET, or the
# one line in ~/.releases-target, names the upload account and host as
# user@host:/path. The account on the other side can write that directory
# and nothing else, and it is reached by key.
set -eu

TAG=${1:-}
SRC=${2:-/tmp/prisma-release}
URL=${RELEASES_URL:-https://prismarouting.com.br/releases}
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
# The package is read back whole, and BY THE PRODUCT'S OWN DOWNLOADER, which
# is what `system upgrade` on a box uses (#456). Read back with curl, every
# release since 0.125 passed this check while every box got HTTP 403: the
# server's front refuses Python's default User-Agent and lets curl through.
# The ISO is hundreds of megabytes, so its size is compared instead.
got=$(PRISMA_RB_URL="$URL/$VER/$ART" PRISMA_RB_TMP="$STAGE/readback" \
      python3 -c '
import os, sys
sys.path.insert(0, sys.argv[1])
import prismash
s, n, why = prismash.download_to(os.environ["PRISMA_RB_URL"],
                                 os.environ["PRISMA_RB_TMP"])
print(s or why)' "$(dirname "$0")/../src/prismash" 2>&1 | tail -1)
rm -f "$STAGE/readback"
if [ "$got" = "$(cut -d" " -f1 "$STAGE/$VER/$ART.sha256")" ]; then
  echo "  $URL/$VER/$ART downloads and verifies, fetched the way a box fetches it"
else
  echo "error: $URL/$VER/$ART does not download, fetched the way a box" >&2
  echo "       fetches it: ${got:-nothing}" >&2
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

# `latest` IS A DIRECTORY, NOT A LINK TO ONE (#604). It used to be a symlink
# to the newest version's directory, and after a patch release that directory
# has no ISO: 0.153.1 moved `latest` to itself, and anybody who took "the
# latest ISO" from latest/ for a new install found none, while the newest
# medium sat in 0.153.0/. So latest/ holds links to TWO releases' files, each
# beside its sha256: the newest upgrade package, and the newest ISO, which
# after a patch release is the last X or Y release's. Both only ever move
# forward, each on its own.
#
# What is newest is read off what latest/ already offers, so publishing an
# old version again cannot move either back. When latest/ offers no ISO - the
# old symlink, pointed at a patch release, or a site that never had one - the
# newest version directory that has one is taken, once; from then on it is
# carried forward.
state=$(ssh -o BatchMode=yes "$HOSTPART" "cd '$DIR' && ls -1 latest/ 2>/dev/null; for v in [0-9]*.[0-9]*.[0-9]*; do [ -f \"\$v/prisma-routing-\$v.iso\" ] && echo \"medium \$v\"; done; true" 2>/dev/null || true)
cur_up=$(printf '%s\n' "$state" | sed -n 's/^prisma-routing-\([0-9][0-9.]*[0-9]\)-upgrade\.tar\.gz$/\1/p' | sort -V | tail -1)
cur_iso=$(printf '%s\n' "$state" | sed -n 's/^prisma-routing-\([0-9][0-9.]*[0-9]\)\.iso$/\1/p' | sort -V | tail -1)
[ -n "$cur_iso" ] || cur_iso=$(printf '%s\n' "$state" | sed -n 's/^medium //p' | sort -V | tail -1)
newer() {  # is $1 newer than $2 (or $2 empty)? `sort -V` orders 0.122.10
           # after 0.122.9, which a string comparison does not.
  [ -z "$2" ] && return 0
  [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" = "$1" ]
}
up=$cur_up; iso=$cur_iso
newer "$VER" "$cur_up" && up=$VER
[ -f "$SRC/$ISO" ] && newer "$VER" "$cur_iso" && iso=$VER
links=""
for f in "prisma-routing-$up-upgrade.tar.gz" "prisma-routing-$up-upgrade.tar.gz.sha256"; do
  links="$links ln -s '../$up/$f' latest.new/'$f' &&"
done
if [ -n "$iso" ]; then
  for f in "prisma-routing-$iso.iso" "prisma-routing-$iso.iso.sha256"; do
    links="$links ln -s '../$iso/$f' latest.new/'$f' &&"
  done
fi
# Built beside and swapped in, so a reader never sees half of it; `rm -rf` on
# the old symlink removes the link, not the release it pointed at.
ssh -o BatchMode=yes "$HOSTPART" "cd '$DIR' && rm -rf latest.new && mkdir -m 755 latest.new &&$links rm -rf latest && mv latest.new latest"
if [ "$up" = "$cur_up" ]; then
  echo "  latest upgrade stays at ${cur_up:-none}: $VER is not newer"
else
  echo "  latest upgrade: ${cur_up:-none} -> $up"
fi
echo "  latest medium: ${iso:-none}$([ "$iso" != "$cur_iso" ] && echo " (was ${cur_iso:-none})")"
# And read back through latest/, the way an operator following it reads it:
# every link has to land on the file it names.
for pair in "$up:prisma-routing-$up-upgrade.tar.gz" ${iso:+"$iso:prisma-routing-$iso.iso"}; do
  v=${pair%%:*}; f=${pair#*:}
  want=$(curl -fsL --max-time 30 "$URL/$v/$f.sha256" | cut -d" " -f1) || want=""
  got=$(curl -fsL --max-time 30 "$URL/latest/$f.sha256" | cut -d" " -f1) || got=""
  if [ -n "$want" ] && [ "$got" = "$want" ]; then
    echo "  $URL/latest/$f.sha256 matches $v/"
  else
    echo "error: $URL/latest/$f.sha256 says '${got:-nothing}', not '${want:-?}'" >&2
    bad=1
  fi
done
if [ "$bad" = 1 ]; then
  echo "error: latest/ does not read back; fix it before anybody follows it." >&2
  exit 1
fi
echo "published: $URL/$VER/"
