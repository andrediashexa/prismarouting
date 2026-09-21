#!/bin/sh
# appliance/share-the-api-segment, driven against a fake /dev/shm.
#
# WHAT THIS PIECE IS. VPP's `api-segment { gid vpp }` group-owns
# /dev/shm/vpe-api and NOT /dev/shm/global_vm, and a client maps both - so an
# operator account in group vpp gets "svm_region_map(mmap open): Permission
# denied" and the VTY reports a timeout it cannot explain (#76). The fix was
# a line of shell inside a drop-in on vpp.service until #426, when the
# dataplane in a container needed the same thing done to the same file: the
# container shares the host's /dev/shm (`ipc: host`), so there are two units
# and one rule, and this is the file that holds it.
#
# WHAT IS ASSERTED. Not the mode bits on a real box - appliance/selftest does
# that by MAKING AN API CALL as a real account, which is the end rather than
# the means (#184). What is asserted here is the decision: which files it
# touches, which group it asks for, that it waits, and that it never fails
# the unit it is a post-start of, because a dataplane that stops because a
# permission could not be set is worse than one an operator must be root to
# configure.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }
REPO=$(cd "$(dirname "$0")/.." && pwd)
BIN=$REPO/appliance/share-the-api-segment

# The group this test can actually chgrp to. Asking for "vpp" would make the
# result depend on whether the machine running the suite is an appliance,
# which is the one thing a unit test must not do: a suite that is green on a
# box with a vpp group and red on a laptop is measuring the laptop.
GRP=$(id -gn)

echo "=== the segment is given to the group, and the mode is set ==="
D=$(mktemp -d)
: > "$D/global_vm"; : > "$D/vpe-api"
chmod 600 "$D/global_vm" "$D/vpe-api"
OUT=$(PRISMA_SHM_DIR=$D PRISMA_API_GROUP=$GRP sh "$BIN" 2>&1); RC=$?
[ "$RC" = 0 ] && ok "it exits 0" || bad "it exited $RC: $OUT"
for s in global_vm vpe-api; do
  M=$(stat -c '%a' "$D/$s")
  [ "$M" = 660 ] && ok "$s is mode 660" || bad "$s is mode $M, not 660"
  G=$(stat -c '%G' "$D/$s")
  [ "$G" = "$GRP" ] && ok "$s is group $GRP" || bad "$s is group $G"
done
rm -rf "$D"

# BOTH SEGMENTS AND NOT ONLY THE ONE #76 NAMED. vpe-api is already group-owned
# by VPP itself on a box whose startup.conf names the group - and on a box
# whose startup.conf does not, it is not, and the client maps it just the
# same. Doing it twice costs nothing; reading which box this is would be a
# second copy of a decision startup.conf already made.
echo
echo "=== a box whose dataplane never came up ==="
# The wait gives up rather than blocking: a dataplane that is forwarding must
# not be stopped over a permission. What it costs is that the VTY needs root,
# and the self-test is what reports that, by making the call.
D=$(mktemp -d)
START=$(date +%s)
OUT=$(PRISMA_SHM_DIR=$D PRISMA_API_GROUP=$GRP sh "$BIN" 2>&1); RC=$?
ELAPSED=$(( $(date +%s) - START ))
[ "$RC" = 0 ] \
  && ok "it still exits 0, so an ExecStartPost cannot stop the dataplane" \
  || bad "it exited $RC on a box with no segment, which would fail the unit"
printf '%s\n' "$OUT" | grep -q "never appeared" \
  && ok "and it says the API stays root-only rather than saying nothing" \
  || bad "it was silent about a segment that never appeared: $OUT"
[ "$ELAPSED" -ge 8 ] && [ "$ELAPSED" -le 30 ] \
  && ok "and it waited ${ELAPSED}s for it, which is the 10s the drop-in waited" \
  || bad "it waited ${ELAPSED}s, and the segment is created while VPP starts"
rm -rf "$D"

echo
echo "=== a group that does not exist ==="
D=$(mktemp -d)
: > "$D/global_vm"
OUT=$(PRISMA_SHM_DIR=$D PRISMA_API_GROUP=no-such-group-here sh "$BIN" 2>&1)
RC=$?
[ "$RC" = 0 ] && ok "it exits 0 rather than failing the unit" \
  || bad "it exited $RC"
printf '%s\n' "$OUT" | grep -q "cannot give" \
  && ok "and it names the file and the group it could not use" \
  || bad "it said nothing about a chgrp that failed: $OUT"
rm -rf "$D"

echo
echo "=== both units run it, and neither carries a copy of the shell ==="
# The whole reason this file exists: two units, one rule. A unit that grew its
# own copy back would be corrected in one place and not the other.
for u in vpp-api-access.conf prisma-routing-containers.service; do
  U=$REPO/appliance/systemd/$u
  grep -q "ExecStartPost=/usr/lib/prisma-routing/share-the-api-segment" "$U" \
    && ok "$u runs the script after start" \
    || bad "$u does not run /usr/lib/prisma-routing/share-the-api-segment"
  # Comments out first: the unit EXPLAINS that the chgrp moved into the
  # script, and the sentence saying so contains the word. Measured - this
  # check went red on its own documentation, the same way the privileged
  # reader in tools/build-images.sh did.
  grep -v '^[[:space:]]*#' "$U" | grep -q "chgrp" \
    && bad "$u carries its own chgrp: that is the second copy this file removed" \
    || ok "and carries no chgrp of its own"
done
# And the installer puts it where the units name it.
grep -q "share-the-api-segment\" \"\$DESTDIR/usr/lib/prisma-routing/share-the-api-segment\"" \
  "$REPO/appliance/install.sh" \
  && ok "and install.sh puts it at the path both units name" \
  || bad "install.sh does not install share-the-api-segment there"

echo
echo "=== the container gets the host's /dev/shm, or there is no API ==="
# Measured on 2026-09-21: started without `ipc: host`, the two segments appear
# INSIDE the container and the host's /dev/shm stays empty - so prismash, a
# host tool, has no binary API and every commit fails. With it they appear on
# the host, 660 root:root, which is what the script above is then for.
grep -v '^[[:space:]]*#' "$REPO/appliance/compose.yaml" | grep -q "ipc: host" \
  && ok "appliance/compose.yaml shares the host's IPC namespace" \
  || bad "compose.yaml has no 'ipc: host', so the dataplane would create the API segment inside the container and the VTY on the host would have none"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
