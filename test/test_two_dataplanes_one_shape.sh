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
echo "=== nothing pulls in the dataplane this box is not running ==="
# Requires= and Wants= START the unit they name. prisma-routing.service
# carried Requires=vpp.service, which on a container box would have started
# vpp.service - and vpp.service Conflicts with the containers unit, so systemd
# would stop the containers to start it, or refuse, and which of those happens
# is decided nowhere. The loader would fight its own dataplane at every boot.
#
# After= is the ordering and is kept: it is not a dependency and starts
# nothing. PartOf is kept too, naming both, because PartOf a unit nobody
# started is inert.
pullers=""
for u in "$SYSD"/*.service "$SYSD"/*.timer "$SYSD"/*.conf; do
  [ -f "$u" ] || continue
  n=$(basename "$u")
  case "$n" in
    # The dataplane units themselves, which are allowed to name what they
    # replace: the containers unit Conflicts with vpp.service and frr.service
    # on purpose, and Conflicts starts nothing.
    prisma-routing-containers.service) continue ;;
  esac
  # frr.service is in this list too, and for the same reason one release
  # later: the control plane is a container on that path, and a host FRR
  # pulled in beside it is two daemons holding /etc/frr and /var/run/frr.
  if body "$u" | grep -qE "^(Requires|Wants|BindsTo)=.*(vpp\.service|frr\.service|prisma-routing-containers\.service)"; then
    pullers="$pullers $n"
  fi
done
[ -z "$pullers" ] \
  && ok "no unit pulls in a dataplane: After= orders, and starts nothing" \
  || bad "these units would START a dataplane they only mean to run after:$pullers - on a box running the other one, that is systemd being asked to run both and no file saying which wins"
body "$SYSD/prisma-routing.service" | grep -q "^PartOf=.*prisma-routing-containers.service" \
  && ok "and the configuration loader is PartOf both, so it follows whichever runs" \
  || bad "prisma-routing.service is not PartOf prisma-routing-containers.service: on a container box a dataplane restart would leave the CGNAT configuration unapplied and nothing would say so"

echo
echo "=== the medium carries what docker needs to start a container ==="
# NOT A PACKAGING DETAIL. Debian's kernel has AppArmor enabled, so docker
# asks apparmor_parser to load its docker-default profile before it starts
# anything, and without the parser the start fails with
# `exec: "apparmor_parser": executable file not found in $PATH`. On an
# installed box that was the whole difference between a container stack and
# none - and every compose-check in this repository passed while it was true,
# because the build host has had the package all along.
for pkg in docker.io docker-compose apparmor; do
  grep -qE "^ *docker\.io.*$pkg|^ *$pkg|[[:space:]]$pkg([[:space:]]|$)" \
    "$REPO/appliance/build-iso.sh" \
    && ok "the medium installs $pkg" \
    || bad "appliance/build-iso.sh does not install $pkg, and without it a box cannot start the containers it carries"
done

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

echo "=== and what root has for free, the container has to be given ==="
# THE SAME SHAPE AS EVERY OTHER ROW HERE, one level down. vpp.service runs
# as root with every capability, so nothing on the unit path ever had to
# name one. The container names three - and on a box whose hugepages are 2M
# it needs a fourth.
#
# The dataplane there addresses its card by physical address (`iova-mode
# pa`, written by appliance/prisma-routing-tune), and DPDK reads those out
# of /proc/self/pagemap. Since Linux 4.0 the kernel zeroes the PFN for a
# reader without CAP_SYS_ADMIN; DPDK cannot tell that from "no physical
# addresses available" and rte_eal_init returns EINVAL. Measured on
# 2026-09-23, one variable changed at a time, on a 2M host:
#
#   SYS_NICE NET_ADMIN IPC_LOCK     rte_eal_init failed: Invalid argument
#   ... + PERFMON                   the same
#   ... + SYS_ADMIN                 the dataplane stayed up
#
# PERFMON is the narrower capability and was tried first (#428).
if body "$COMPOSE" | sed -n "/^  vpp:/,/^  [a-z]/p" | grep -q "SYS_ADMIN"; then
  ok "the dataplane container is given SYS_ADMIN, which is what lets it read a physical address"
else
  bad "the dataplane container has no SYS_ADMIN: on a box whose pages are 2M, rte_eal_init fails with EINVAL and the box never forwards (#428)"
fi
# AND THE PORT A LEGACY CARD SPEAKS THROUGH. 1af4:1000 - the virtio-net
# every qemu and Proxmox guest gets unless told otherwise - keeps its
# registers in an I/O BAR. uio_pci_generic creates no
# /sys/class/uio/uioX/portio, measured on the box, so DPDK falls back to
# iopl(), and iopl() needs CAP_SYS_RAWIO.
#
# Without it the dataplane STARTS and takes no port, which is worse than a
# refusal: `show version` answers and `show interface` has only local0.
# Measured on one installed box, one boot, the stack recreated twice so the
# recreate itself is the control:
#
#   [IPC_LOCK NET_ADMIN SYS_ADMIN SYS_NICE]            local0 only
#   [IPC_LOCK NET_ADMIN SYS_ADMIN SYS_NICE SYS_RAWIO]  ens9 up, rx/tx
if body "$COMPOSE" | sed -n "/^  vpp:/,/^  [a-z]/p" | grep -q "SYS_RAWIO"; then
  ok "and SYS_RAWIO, which is how it reaches a legacy card's I/O ports"
else
  bad "the dataplane container has no SYS_RAWIO: a legacy virtio card on uio_pci_generic is left untaken, and the box says nothing (#428)"
fi

# AND THE UNIT PATH HAS IT WITHOUT SAYING SO, which is the whole reason this
# row is easy to miss: there is no line to compare it against.
if body "$SYSD"/vpp-*.conf | grep -q "CapabilityBoundingSet"; then
  bad "vpp.service now bounds its capabilities, so root no longer has SYS_ADMIN for free and the two paths have to be compared line for line here"
else
  ok "and vpp.service bounds no capabilities, so root has it there without a line"
fi

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
