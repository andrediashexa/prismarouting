#!/bin/sh
# Two interface/read defects found by #558 phase 2's round-trip lab (#560,
# #561), measured on a real dataplane.
#
#   sh tools/lab.sh mtu-and-readonly       -> LAB PASSED
#
# #560: `no mtu` printed a note and left the dataplane at the last committed
# MTU; the running configuration, which reads the device, went on saying it.
# Here: an MTU is committed, then removed, and the dataplane, the running
# configuration and the next commit are read back. Since #592 the box's
# default is 1500, on the stanza the tuner renders, and pg0 starts there.
#
# #561: an account with no role took about 90 s for any read of the running
# configuration. Here: the same `show running-config` timed as root and as an
# account with no role, and the second held to a few seconds.
#
# NO-OFF-RUN: both are defects with no switch in the product, and one added
# only for this lab to flip would be a second code path to keep; the control
# is this lab run against v0.149.20, where steps 2-3 and 5 fail (recorded in
# the #560/#561 commit).
set -u
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /etc/prisma-routing /var/run/frr /etc/frr
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh
use_binary_api
export PRISMA_ROLLBACK=/tmp/rollback PRISMA_CONFIG=/tmp/prisma-routing.conf
rm -rf /tmp/rollback

# THE BOX'S DEFAULT, as the tuner renders it (#592): an interface with no
# mtu comes up at 1500, not at VPP's own 9000, and `no mtu` goes back there.
# The VTY reads the default off the file the dataplane started on (#560).
STANZA=$(python3 -c "import importlib.machinery as m
t = m.SourceFileLoader('tune', '/src/appliance/prisma-routing-tune').load_module()
print(t.render_ethernet({'cards': []}))")
{ cat "$(startup_conf test)"; printf '\n%s\n' "$STANZA"; } > /tmp/startup-mtu.conf
export PRISMA_STARTUP_CONF=/tmp/startup-mtu.conf
vpp -c /tmp/startup-mtu.conf > /tmp/vpp.log 2>&1 &
wait_vpp
vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface state pg0 up
printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
chown -R frr:frr /var/run/frr /etc/frr
pgrep zebra >/dev/null || start_frr
sleep 2

HX="python3 /src/src/prismash/prismash.py"
l3mtu() {   # the L3 MTU the dataplane holds for pg0
  vppctl show interface pg0 | awk '$1=="pg0"{print $4}' | cut -d/ -f1
}
running_mtu() {
  $HX -c "show running-config" 2>/dev/null | awk '/^interface pg0/{f=1} f&&/^ mtu /{print $2; exit} f&&/^ exit/{exit}'
}

echo "=========== 1. the MTU the box starts pg0 at ==========="
DEFAULT=$(l3mtu)
echo "  dataplane L3 MTU before anything: $DEFAULT"

echo
echo "=========== 2. mtu 1400, committed ==========="
$HX -c configure -c "interface pg0" -c "mtu 1400" -c exit -c commit 2>&1 | sed 's/^/  /'
SET=$(l3mtu); RUN_SET=$(running_mtu)
echo "  dataplane $SET, running config says $RUN_SET"

echo
echo "=========== 3. no mtu, committed ==========="
$HX -c configure -c "interface pg0" -c "no mtu" -c exit -c commit 2>&1 | sed 's/^/  /'
BACK=$(l3mtu); RUN_BACK=$(running_mtu)
echo "  dataplane $BACK, running config says $RUN_BACK"
NEXT=$($HX -c configure -c "commit check" 2>&1)
echo "  a commit check right after:"; printf '%s\n' "$NEXT" | sed 's/^/    /'

echo
echo "=========== 4. the running configuration as root ==========="
T0=$(date +%s%N); $HX -c "show running-config" > /tmp/run-root.txt 2>&1
ROOT_MS=$(( ($(date +%s%N) - T0) / 1000000 ))
echo "  $(wc -l < /tmp/run-root.txt) lines in ${ROOT_MS} ms"

echo
echo "=========== 5. the same, as an account with no role ==========="
id viewer >/dev/null 2>&1 || useradd -M -s /bin/sh viewer
chmod 0644 "$PRISMA_CONFIG" 2>/dev/null; chmod 0755 /tmp/rollback 2>/dev/null
chmod 0644 /tmp/rollback/* 2>/dev/null
T0=$(date +%s%N)
su viewer -s /bin/sh -c "PRISMA_ROLLBACK=$PRISMA_ROLLBACK PRISMA_CONFIG=$PRISMA_CONFIG timeout 300 $HX -c 'show running-config'" > /tmp/run-viewer.txt 2>&1
VIEW_MS=$(( ($(date +%s%N) - T0) / 1000000 ))
echo "  $(wc -l < /tmp/run-viewer.txt) lines in ${VIEW_MS} ms"
head -5 /tmp/run-viewer.txt | sed 's/^/    /'

expect "pg0 started at the box's default of 1500, not VPP's 9000 (got $DEFAULT)" \
  test "$DEFAULT" = 1500
expect "mtu 1400 reached the dataplane (got $SET)" test "$SET" = 1400
expect "and the running configuration (got $RUN_SET)" test "$RUN_SET" = 1400
expect "no mtu put the dataplane back to the box's default $DEFAULT (got $BACK)" \
  test "$BACK" = "$DEFAULT"
expect "and the running configuration says the default (got $RUN_BACK)" \
  test "$RUN_BACK" = "$DEFAULT"
expect "a commit after it has nothing left to do for pg0's MTU" \
  sh -c "! printf '%s' \"\$1\" | grep -q 'mtu'" _ "$NEXT"
expect "the account with no role reads the running configuration in under 10 s (${VIEW_MS} ms; root ${ROOT_MS} ms)" \
  test "$VIEW_MS" -lt 10000
expect "and gets one (it names the hostname)" grep -q "^hostname" /tmp/run-viewer.txt
verdict
