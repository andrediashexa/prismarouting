#!/bin/sh
# Is there an installation medium here that is worth installing?
#
# Exit 0 when the image this tree would build is already present and is newer
# than everything that goes into it. Exit non-zero, with the reason, when there
# is none or when the tree has moved since it was built.
#
# WHY THIS EXISTS. `make test-install` used to depend on the phony `iso`
# target, so it built a medium every time. tools/release-gate.sh builds the
# medium, boots it, and then calls `make test-install` - which built a SECOND
# one over the same path and installed that. Measured on the v0.74.0 gate: the
# boot finished at 18:02:23 and the file was rewritten at 18:07:51 (#307).
#
# The gate's whole argument for the install phase is that booting a medium and
# installing it are different claims about THE SAME MEDIUM. They were claims
# about two different builds, and the sha256 the gate publishes described the
# one it never booted.
#
# STALENESS IS THE THING TO GET RIGHT, not absence. A developer who edits
# appliance/ and runs `make test-install` must not silently test yesterday's
# image; that would trade one wrong medium for another. So this asks whether
# anything the image is made of is newer than the image, and says so.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
NAME=$(sh "$REPO/tools/version-stamp.sh" --filename "$REPO")
ISO="$REPO/$NAME"

[ -f "$ISO" ] || { echo "no $NAME here yet" >&2; exit 1; }

# The directories an image is built out of. `test/` and `docs/` are not in it:
# the medium carries three documents, and they are installed from appliance/
# by a line this list would have to guess at. A file newer than the image in a
# directory that is not here is a miss, and it is a cheaper miss than a list
# that is wrong the other way and rebuilds on every run.
newer=$(find "$REPO/appliance" "$REPO/src" "$REPO/tools" "$REPO/VERSION" \
             -newer "$ISO" -type f -print 2>/dev/null | head -3)
if [ -n "$newer" ]; then
  echo "$NAME is older than this tree:" >&2
  printf '  %s\n' $newer >&2
  exit 1
fi
echo "using the $NAME that is already here"
