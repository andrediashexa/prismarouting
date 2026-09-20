#!/bin/sh
#
# OFF-RED-AT-LEAST: 4
#
# A FLOOR, and the number is measured rather than chosen: it is what the
# control run went red on when it was first written, on 2026-09-20. The first
# assertion stays GREEN in that run and must - the translation before the kill
# happens in both modes, and a floor of five would be claiming this lab tests
# something it does not.
#
# Kills the control plane and then sends a packet.
#
# THE CLAIM THIS EXISTS FOR. docs/appliance.md and #396 both say a management
# failure MUST NOT stop forwarding, and both support it with the MECHANISM:
# the systemd units are independent, VPP carries OOMScoreAdjust=-1000, the
# control plane carries MemoryMax=512M. None of that is a measurement of the
# effect. Until this lab, nothing in this repository killed the control plane
# and then sent a packet (#421).
#
# That gap matters twice. An operator's box is expected to keep carrying
# subscribers while FRR restarts or the agent dies; and #396 offers the
# property as already-satisfied when deciding whether containers buy a failure
# boundary this product does not already have.
#
# lab/routing.sh kills frr/mgmtd already and asserts that a COMMIT is refused
# with advice an operator can act on. That is the control plane's own
# behaviour, and it sends no packet afterwards.
#
# THE OFF MODE, which is what makes this a test rather than a ritual. Killing
# nothing would leave every assertion passing, so that is not a control. The
# control is to kill VPP INSTEAD: forwarding stops, the second translation
# never happens, and this lab MUST go red. A run that passes with the
# dataplane dead is a lab asserting nothing.
#
#   sh tools/lab.sh control-plane-dies            forwarding survives
#   sh tools/lab.sh control-plane-dies kill-vpp   the control: MUST fail
set -u
. /src/lab/lib.sh
MODE=${1:-normal}
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
n=0
until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

# The same fixture datapath.sh uses, because the question here is not whether
# translation works - that lab owns it - but whether it still works with the
# control plane gone.
vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up
vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl set ip neighbor pg0 100.64.3.253 02:00:00:00:00:01
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0
vppctl bpa pool lab inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1

start_frr

# One subscriber, two packets, DIFFERENT source ports - so the second cannot
# be the first one's session answering. Both land in the same block, which is
# what ref/bpa_model.py predicts for this pool.
shoot() {
  vppctl packet-generator new "name $1 limit 1 node ip4-input interface pg0 \
      size 60-60 data { UDP: 100.64.0.5 -> 8.8.8.8 \
                        UDP: $2 -> 53 \
                        incrementing 8 }" >/dev/null
  vppctl clear trace >/dev/null
  vppctl trace add pg-input 10 >/dev/null
  vppctl packet-generator enable >/dev/null
  sleep 1
  vppctl show trace
  vppctl packet-generator delete "$1" >/dev/null 2>&1
}

echo "=========== BEFORE: the box translates ==========="
echo "predicted by the model: 100.64.0.5 -> 200.150.0.17, in block 1536-2047"
BEFORE=$(shoot first 12345)
echo "$BEFORE" | grep -i "BPA in2out" | sed 's/^/  /'

echo
echo "=========== THE CONTROL PLANE DIES ==========="
if [ "$MODE" = "kill-vpp" ]; then
  # THE CONTROL. Killing the dataplane instead must take every assertion
  # below with it. If this run passes, the assertions are wired to nothing.
  echo "  control run: killing the DATAPLANE instead, so the checks below"
  echo "  MUST fail. A green run here is a failure of this lab."
  pkill -f "vpp -c" 2>/dev/null
  sleep 2
else
  echo "  killing frr/mgmtd, zebra and staticd"
  pkill -f 'frr/mgmtd' 2>/dev/null
  pkill -f 'frr/zebra' 2>/dev/null
  pkill -f 'frr/staticd' 2>/dev/null
  sleep 2
fi
echo "  frr processes still alive: $(pgrep -fc 'frr/' 2>/dev/null || echo 0)"

echo
echo "=========== AFTER: the box still translates ==========="
echo "predicted by the model: the same public address, a DIFFERENT port in the"
echo "same block - the second packet is a new session, not the first answering"
AFTER=$(shoot second 12346 2>/dev/null)
echo "$AFTER" | grep -i "BPA in2out" | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
# ON THE CAPTURED PACKET and not on a counter, which is the rule this
# repository states: a counter increments whether or not the rewrite was
# right. The trace carries what the packet became.
expect_in "the box translated before the control plane died" \
  "200.150.0.17" "$BEFORE"
expect_in "and it translated AFTER, with the control plane gone" \
  "200.150.0.17" "$AFTER"
expect_in "the second packet is its own session, not the first one's" \
  "12346" "$AFTER"
# And the box has to still be a box: a dataplane that answers nothing would
# satisfy nothing above by printing nothing, and expect_in on an empty
# haystack is the shape lab/vty-ssh.sh was caught in.
expect "the dataplane is still answering at the end" \
  sh -c "vppctl show version >/dev/null 2>&1"
expect "and the pool is still in it, so nothing was reconfigured behind us" \
  sh -c "vppctl show bpa config | grep -q 'cgnat pool lab'"
verdict
