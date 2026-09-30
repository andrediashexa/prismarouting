#!/bin/sh
# Which dataplane an installed box is left running, and why it is not always
# the same one.
#
# THE UPGRADE ARTIFACT CARRIES NO IMAGES. They ride the squashfs, so a box
# INSTALLED from a medium has them and a box UPGRADED from an older release
# has whatever its own medium carried - which for anything before 0.113.0 is
# nothing at all. Enabling the container stack there hands the box a dataplane
# it cannot start: the unit fails, the card watcher gives the card back two
# minutes later, and what an operator sees is a box that routes and does not
# forward.
#
# That is what a maintainer's box did on 0.117.0, and no lab could see it -
# a lab always installs from a medium that carries the images.
#
# So the default follows the images, and this drives that decision and the
# three places in the installer that act on it. The installer itself is not
# run here: it copies binaries this harness has no build of, and what is
# being measured is the choice, not the copy.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }
REPO=$(cd "$(dirname "$0")/.." && pwd)
INSTALL=$REPO/appliance/install.sh
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Cut the decision out and call it, the way test/test_selftest.sh cuts the
# self-test's readers out: everything above it in that file needs a build.
sed -n '/^container_default() {/,/^}/p' "$INSTALL" > "$WORK/fn.sh"
[ -s "$WORK/fn.sh" ] || { echo "  FAIL: no container_default() in $INSTALL"; exit 1; }
. "$WORK/fn.sh"

echo "=== a root with the images, and one without ==="
mkdir -p "$WORK/with/usr/lib/prisma-routing" "$WORK/without/usr/lib/prisma-routing"
: > "$WORK/with/usr/lib/prisma-routing/images.tar.gz"
container_default "$WORK/with"
rc_with=$?
if [ "$rc_with" = 0 ]; then
  ok "a root that carries the images runs the containers"
else
  bad "a root with $WORK/with/usr/lib/prisma-routing/images.tar.gz was left on the units, so a fresh install would not take the default at all"
fi
if container_default "$WORK/without"; then
  bad "a root with NO images was moved to the containers: that box has a dataplane it cannot start, and the card watcher hands its card back two minutes into every boot"
else
  ok "and a root without them keeps vpp.service and frr.service"
fi
# An EMPTY root, which is what a DESTDIR is before anything is copied into
# it, and what `container_default ""` reads on a live box with no medium.
if container_default ""; then
  bad "an empty root answered yes"
else
  ok "and so does a root that has nothing at all"
fi

echo
echo "=== and the installer acts on it in every place that matters ==="
# Four: the two units the containers replace are disabled only where the
# containers are enabled; the unit and its timer are linked only there. A
# branch that forgot one of these would leave a box with both dataplanes
# enabled, which is the state the unit's Conflicts= exists to prevent.
for what in \
  "systemctl enable prisma-routing-containers.service" \
  "systemctl disable vpp.service frr.service" \
  "multi-user.target.wants/prisma-routing-containers.service" \
  "timers.target.wants/prisma-routing-card-watch.timer"
do
  # The line has to be inside a CONTAINER_DEFAULT branch: awk keeps the
  # depth of the nearest `if [ "$CONTAINER_DEFAULT" = 1 ]`.
  guarded=$(awk -v needle="$what" '
    /if \[ "\$CONTAINER_DEFAULT" = 1 \]/ { depth = 1 }
    depth && /^  *fi$/ { depth = 0 }
    depth && index($0, needle) { print "yes"; exit }
  ' "$INSTALL")
  if [ "$guarded" = yes ]; then
    ok "'$what' happens only on a box that has the images"
  else
    bad "'$what' is not inside a CONTAINER_DEFAULT branch: a box with no images would be moved to a dataplane it cannot start"
  fi
done

echo
echo "=== and it says so, where somebody is reading ==="
# An upgrade that quietly did nothing is indistinguishable from one that
# worked. The box is not moved AND the operator is told which dataplane it
# is on and what would move it.
grep -q "no container images on this box" "$INSTALL" \
  && ok "the installer says a box was left on the units" \
  || bad "a box left on the units is told nothing"
# BOTH ARMS, and they are different sentences because they are different
# things: one is a live box being upgraded, the other a root being written
# into. lab/upgrade.sh reads the second, because a container has no systemd
# and the installer takes the offline arm there.
grep -q "no container images in this root" "$INSTALL" \
  && ok "and says it on the offline arm too, in that arm's own terms" \
  || bad "the offline arm writes a root that keeps the units and says nothing"
grep -q "install from a medium of this" "$INSTALL" \
  && ok "and says what would move it" \
  || bad "the message does not say how to get the containers"

echo
echo "=== tun and vhost-net are loaded before the dataplane starts (#510) ==="
# A device the container is given before its module is loaded has no
# /dev/char link, systemd leaves it out, and the first daemon-reload took it
# from the dataplane: every card pair after that failed, and a box whose
# management card is in the dataplane came back from a reboot unreachable.
U="$REPO/appliance/systemd/prisma-routing-containers.service"
_mod=$(grep -n '^ExecStartPre=-/sbin/modprobe -a tun vhost_net$' "$U" | cut -d: -f1)
_settle=$(grep -n '^ExecStartPre=-/bin/udevadm settle' "$U" | cut -d: -f1)
_up=$(grep -n '^ExecStart=/usr/bin/docker-compose .* up -d$' "$U" | cut -d: -f1)
[ -n "$_mod" ] && [ -n "$_settle" ] && [ -n "$_up" ] \
  && [ "$_mod" -lt "$_settle" ] && [ "$_settle" -lt "$_up" ] \
  && ok "the containers unit loads both, waits for their links, then starts the containers" \
  || bad "modprobe ($_mod), settle ($_settle) and compose up ($_up) are not in that order"
grep -q 'prisma-routing-dataplane.modules' "$INSTALL" \
  && [ "$(grep -v '^#' "$REPO/appliance/prisma-routing-dataplane.modules" | tr '\n' ' ')" = "tun vhost_net " ] \
  && ok "and the installer loads them at every boot" \
  || bad "the installer writes no modules-load.d file for tun and vhost_net"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
