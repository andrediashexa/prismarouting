#!/bin/sh
# The two dataplanes this product can run, and the protections that must be on
# both of them.
#
# WHY THIS FILE EXISTS. Until #396 there was one dataplane - vpp.service, with
# three drop-ins this product installs on it - and every protection was a line
# in one of those files. There are two now: the same VPP as a container, under
# appliance/compose.yaml. A protection that exists on one path and not the
# other is the worst shape available, because the box looks the same from the
# VTY either way and nothing says which one is carrying the subscribers.
#
# WHAT IS ASSERTED. That each protection is present on BOTH paths, and that
# where both name a value, the values agree. Not what the protection does -
# the drop-ins carry that, with their measurements - and not that it reaches
# the kernel, which is tools/build-images.sh compose-check's job against a
# running container and lab/oom.sh's against a running box.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }
REPO=$(cd "$(dirname "$0")/.." && pwd)
COMPOSE=$REPO/appliance/compose.yaml
SYSD=$REPO/appliance/systemd

# Comments out of the way first, on every read below. Both files EXPLAIN what
# they do in prose that contains the words being looked for, and a reader that
# does not strip them is one that goes green on its own documentation - which
# has now happened twice in this repository (tools/build-images.sh, and the
# chgrp reader in test/test_share_api_segment.sh).
body() { grep -v '^[[:space:]]*#' "$1"; }

echo "=== the dataplane is out of the OOM killer's choice on both paths ==="
# vpp.service's value is the one with the measurements behind it: a 2 GB box
# killed VPP while it held 19 MB resident, because on a box where nothing else
# is resident the dataplane is the biggest thing there.
UNIT_OOM=$(body "$SYSD/vpp-oom-protect.conf" \
  | sed -n 's/^OOMScoreAdjust=\(.*\)$/\1/p' | tr -d ' ')
COMP_OOM=$(body "$COMPOSE" \
  | sed -n 's/^[[:space:]]*oom_score_adj:[[:space:]]*\(.*\)$/\1/p' | tr -d ' ')
[ -n "$UNIT_OOM" ] \
  && ok "vpp.service's drop-in sets OOMScoreAdjust=$UNIT_OOM" \
  || bad "no OOMScoreAdjust in $SYSD/vpp-oom-protect.conf: the unit path lost its protection"
[ -n "$COMP_OOM" ] \
  && ok "and the container sets oom_score_adj=$COMP_OOM" \
  || bad "appliance/compose.yaml sets no oom_score_adj, so a box booted on the containers has a dataplane the kernel may choose for somebody else's allocation"
if [ -n "$UNIT_OOM" ] && [ -n "$COMP_OOM" ]; then
  [ "$UNIT_OOM" = "$COMP_OOM" ] \
    && ok "and the two agree, so the protection does not depend on which path the box took" \
    || bad "the unit path says $UNIT_OOM and the container says $COMP_OOM: the same box is protected differently depending on how its dataplane was started"
fi
# -1000 AND NOT MERELY LOW, which is the decision the drop-in argues: it
# removes VPP from selection rather than lowering it, because the trade is not
# close - a box that lost bgpd still forwards on what it learned.
[ "$COMP_OOM" = "-1000" ] \
  && ok "and it is -1000: out of selection, not merely unlikely" \
  || bad "the container's oom_score_adj is '$COMP_OOM' and the argument in vpp-oom-protect.conf is for -1000"

echo
echo "=== the API segment is shared on both paths ==="
# The other half of #426's first piece, asserted here as a pair rather than
# only inside test_share_api_segment.sh: what makes it a pair is that both
# units run the same script.
for u in vpp-api-access.conf prisma-routing-containers.service; do
  body "$SYSD/$u" | grep -q "ExecStartPost=/usr/lib/prisma-routing/share-the-api-segment" \
    && ok "$u shares the segment after start" \
    || bad "$u does not run share-the-api-segment: the VTY would need root on that path"
done
body "$COMPOSE" | grep -q "ipc: host" \
  && ok "and the container is in the host's IPC namespace, where the VTY looks" \
  || bad "compose.yaml has no ipc: host, so the segments would exist only inside the container"

echo
echo "=== a dataplane that will not start gives the card back on both paths ==="
# #379: the card is bound to a DPDK driver before the dataplane starts, so a
# box whose dataplane never comes up has no interface at all and the only way
# in is a console somebody walks to. On the unit path systemd raises
# OnFailure= after the restart limit. A container reaches no failed state -
# the containers unit is a oneshot that exited successfully long before - so
# Docker's policy has to GIVE UP (on-failure, not unless-stopped) and
# something has to notice that it did.
body "$SYSD/vpp-give-card-back.conf" | grep -q "OnFailure=" \
  && ok "vpp.service raises OnFailure when it gives up" \
  || bad "no OnFailure on vpp.service: #379 is reopened on the unit path"
body "$COMPOSE" | grep -q 'restart: "on-failure' \
  && ok "and the container's policy gives up rather than retrying for ever" \
  || bad "the dataplane container's restart policy never gives up, so a box that cannot start it stays stranded for ever - which is worse than the unit path on the failure the unit path was built for"
[ -x "$REPO/appliance/watch-the-dataplane" ] \
  && ok "and something watches for the container that gave up" \
  || bad "appliance/watch-the-dataplane is missing or not executable: nothing would notice a dataplane container that stopped, because Docker tells nobody"

echo
echo "=== the self-test reads the dataplane this box is actually running ==="
# The dangerous answer is the CORRECT one on the wrong path: `systemctl show
# vpp.service -p NRestarts` on a container box answers 0, about a unit nobody
# started, and 0 reads as "stable". A box in a restart loop would be reported
# healthy by the check that exists to catch exactly that.
grep -q "is-active --quiet prisma-routing-containers.service" \
  "$REPO/appliance/selftest" \
  && ok "the self-test asks which path the box is on" \
  || bad "appliance/selftest never asks whether this box runs the containers, so on a container box it reports a unit nobody started"
for reader in dataplane_restarts dataplane_pid dataplane_log; do
  grep -q "^$reader()" "$REPO/appliance/selftest" \
    && ok "and $reader answers for whichever path that is" \
    || bad "appliance/selftest has no $reader: one of the three readers went back to asking systemd directly"
done
body "$COMPOSE" | grep -q "pid: host" \
  && ok "and the container's pid is a pid on this box, so /proc can be read" \
  || bad "compose.yaml dropped pid: host, so the container's PID is from another namespace and the OOM check would read /proc of some other process, or none"

echo
echo "=== the drain runs before the dataplane dies on both paths ==="
# #217: in dynamic mode the block allocation log IS the connection record, and
# a block still held when the dataplane stops leaves its allocation open in
# that log for ever. On the unit path a drop-in runs `vppctl bpa stop-drain`;
# in the container the entrypoint does it on SIGTERM, because Compose has no
# pre-stop hook - and stop_grace_period is what gives it time.
body "$SYSD/vpp-stop-drain.conf" | grep -q "bpa stop-drain" \
  && ok "vpp.service drains on stop" \
  || bad "no stop-drain on vpp.service: #217 is reopened on the unit path"
body "$REPO/appliance/vpp-entrypoint" | grep -q "DRAIN" \
  && ok "and the container's PID 1 drains before it forwards the signal" \
  || bad "appliance/vpp-entrypoint does not drain: Compose has no pre-stop hook, so nothing else would"
body "$COMPOSE" | grep -q "stop_grace_period" \
  && ok "and compose gives it a grace period to do it in" \
  || bad "compose.yaml sets no stop_grace_period, so docker stop's default 10s would cut the drain off"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
