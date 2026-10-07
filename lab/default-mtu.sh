# OFF-RED-AT-LEAST: 10
# The default MTU is 1500, and a box upgraded onto it keeps 9000 (#592).
#
#   sh tools/lab.sh default-mtu        -> LAB PASSED
#   sh tools/lab.sh default-mtu off    the box before #592: the checks of the
#                                      new default and of the pin go red
#
# Until 0.152.1 an interface with no `mtu` statement came up at VPP's own
# 9000, and on 10.40.200.166 `tcp-mss auto` resolved to 8960 - a clamp that
# clamped nothing for subscribers whose path is 1500. Measured here, on a real
# dataplane started on the startup.conf stanza the tuner really renders:
#
#   1. a fresh box: a port comes up at 1500, a VLAN the VTY creates comes up
#      at 1500 (VPP alone forwards an unset sub-interface at 9000), `tcp-mss
#      auto` on that VLAN resolves to 1460, and `no mtu` goes back to 1500;
#   2. an "upgraded" box: a saved configuration written by an older release,
#      with no mtu anywhere, is pinned by the upgrade's own program
#      (src/prismash/mtupin.py) - `mtu 9000` on each interface, the event
#      `show log` reads - and loaded on this 1500 dataplane its interfaces
#      come up at 9000; an operator's `no mtu` afterwards is not pinned again.
#
# MODE=off is the build before #592: the tuner's default is VPP's 9000 (it
# renders no stanza), the VTY's `no mtu` goes back to VPP's 9000
# (lab/default_mtu_hook.py), and no pin runs, since there was none.
#
# The OFF-RED floor is measured, not chosen: the off run of 2026-10-06 went
# red on 10 of the 15 checks. The five that hold either way are `mtu 1400`
# reaching the port, the tuner rendering no stanza for a box that keeps 9000,
# and the pinned file loading at 9000 - on a box that starts at 9000 anyway.
MODE=${1:-on}
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
rm -rf /tmp/rollback /tmp/box

# THE STANZA THE TUNER RENDERS, not one typed here: a lab that wrote
# `default-mtu 1500` itself would prove VPP takes the number and nothing about
# the box. The off run renders it with the default before #592.
TUNE="import importlib.machinery as m, os, sys
t = m.SourceFileLoader('tune', '/src/appliance/prisma-routing-tune').load_module()
if sys.argv[1] == 'off':
    t.BOX_DEFAULT_MTU = t.ETHERNET_DEFAULT_MTU
print(t.render_ethernet({'cards': [], 'mtu_kept': os.path.exists(sys.argv[2])}))"
STANZA=$(python3 -c "$TUNE" "$MODE" /nonexistent)
STARTUP=/tmp/startup-default-mtu.conf
# The lcp base: a loopback a file names is paired (#404), and its TAP is the
# other half `mtu` sets.
{ cat "$(startup_conf lcp)"; printf '\n%s\n' "$STANZA"; } > "$STARTUP"
# What the VTY reads for `no mtu` (#560): the file the dataplane started on.
export PRISMA_STARTUP_CONF=$STARTUP
echo "=========== the ethernet stanza the tuner renders for a fresh box ==========="
printf '%s\n' "${STANZA:-(none)}" | sed 's/^/  /'

vpp -c "$STARTUP" > /tmp/vpp.log 2>&1 &
wait_vpp
vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up
vppctl set interface state pg1 up
printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
chown -R frr:frr /var/run/frr /etc/frr
pgrep zebra >/dev/null || start_frr
sleep 2

HX="python3 /src/src/prismash/prismash.py"
[ "$MODE" = off ] && HX="python3 /src/lab/default_mtu_hook.py"
l3mtu() {   # the L3 MTU the dataplane holds for <interface>
  vppctl show interface "$1" | awk -v n="$1" '$1==n{print $4}' | cut -d/ -f1
}

echo
echo "=========== 1. a fresh box ==========="
PORT=$(l3mtu pg0)
echo "  pg0 comes up at $PORT"
cat > /tmp/fresh.conf <<'EOF'
interface pg0
 ipv4 address 192.0.2.1/24
 exit
interface pg0.100
 vlan 100
 ipv4 address 100.64.0.1/22
 cgnat inside
 exit
interface pg1
 ipv4 address 203.0.113.1/24
 cgnat outside
 exit
nat
 cgnat
  pool lab
   prefix-inside 100.64.0.0/22
   prefix-outside 203.0.113.16/30
   block-size 512
   tcp-mss auto
   exit
  exit
 exit
EOF
$HX --load /tmp/fresh.conf 2>&1 | sed 's/^/  load: /'
VLAN=$(l3mtu pg0.100)
echo "  pg0.100, created by the load with no mtu: $VLAN"
# The resolved clamp is recomputed by the plugin's process node: a tick.
sleep 2
MSS=$(vppctl show bpa pool | grep -i "tcp-mss")
echo "  $MSS"
$HX -c configure -c "interface pg0" -c "mtu 1400" -c exit -c commit >/dev/null 2>&1
SET=$(l3mtu pg0)
$HX -c configure -c "interface pg0" -c "no mtu" -c exit -c commit 2>&1 | sed 's/^/  /'
BACK=$(l3mtu pg0)
echo "  pg0: mtu 1400 -> $SET, then no mtu -> $BACK"

echo
echo "=========== 2. a box upgraded from 0.151.17 ==========="
mkdir -p /tmp/box/etc/prisma-routing/rollback /tmp/box/var/log/prisma-routing
# What an older release saved: no mtu anywhere, as a hand-written file or an
# API's has it, and a sub-interface, which never had one.
cat > /tmp/box/etc/prisma-routing/prisma-routing.conf <<'EOF'
!
! Prisma Routing
!
interface pg1
 ipv4 address 198.51.100.1/24
 exit
interface pg1.200
 vlan 200
 ipv4 address 10.20.0.1/24
 exit
interface loop7
 ipv4 address 192.0.2.77/32
 exit
EOF
if [ "$MODE" != off ]; then
  PIN=$(python3 /src/src/prismash/mtupin.py --root /tmp/box \
          --previous "0.151.17 (d3044fe7)" 2>&1)
fi
printf '%s\n' "${PIN:-(no pin ran)}" | sed 's/^/  upgrade: /'
PINNED=$(grep -c '^ mtu 9000$' /tmp/box/etc/prisma-routing/prisma-routing.conf)
LOG=$(PRISMA_EVENTS=/tmp/box/var/log/prisma-routing/events.log $HX -c "show log" 2>&1)
printf '%s\n' "$LOG" | sed 's/^/  show log: /'
# The screen folds a long event at the console's width; the sentence is read
# with its folds undone.
LOG1=$(printf '%s\n' "$LOG" | tr -s ' \n' '  ')
KEPT_STANZA=$(python3 -c "$TUNE" "$MODE" /tmp/box/etc/prisma-routing/mtu-9000-kept)
echo "  the tuner on that box renders: ${KEPT_STANZA:-(no ethernet stanza)}"

export PRISMA_CONFIG=/tmp/box/etc/prisma-routing/prisma-routing.conf
$HX --load "$PRISMA_CONFIG" 2>&1 | sed 's/^/  load: /'
UP_PORT=$(l3mtu pg1); UP_VLAN=$(l3mtu pg1.200); UP_LOOP=$(l3mtu loop7)
echo "  loaded on this box: pg1 $UP_PORT, pg1.200 $UP_VLAN, loop7 $UP_LOOP"

# The operator takes the pin off one interface, saves, and the upgrade runs
# again from the same old stamp - a re-run: it is not pinned back. (`save`
# writes the device's MTU for every interface, so the block then says
# `mtu 1500`: the question is that it does not say 9000.)
$HX -c configure -c "interface loop7" -c "no mtu" -c exit -c commit >/dev/null 2>&1
$HX -c save >/dev/null 2>&1
NOPIN=$(l3mtu loop7)
if [ "$MODE" != off ]; then
  AGAIN=$(python3 /src/src/prismash/mtupin.py --root /tmp/box \
            --previous "0.151.17 (d3044fe7)" 2>&1)
fi
LOOP_BLOCK=$(sed -n '/^interface loop7/,/^ exit/p' "$PRISMA_CONFIG")
echo "  loop7 after 'no mtu': $NOPIN; the re-run said: ${AGAIN:-(nothing)}"
printf '%s\n' "$LOOP_BLOCK" | sed 's/^/  saved: /'

echo
expect "1. the tuner renders the box's default for a fresh box" \
  sh -c "printf '%s' \"\$1\" | grep -q 'default-mtu 1500'" _ "$STANZA"
expect "1. a port with no mtu comes up at 1500 (got $PORT)" test "$PORT" = 1500
expect "1. a VLAN the VTY creates with no mtu comes up at 1500 (got $VLAN)" \
  test "$VLAN" = 1500
expect_in "1. tcp-mss auto on that VLAN resolves to 1460" "auto -> 1460" "$MSS"
expect "1. mtu 1400 reached the port (got $SET)" test "$SET" = 1400
expect "1. no mtu goes back to 1500 (got $BACK)" test "$BACK" = 1500
expect "2. the upgrade pinned mtu 9000 on all three interfaces (got $PINNED)" \
  test "$PINNED" = 3
expect_in "2. and said so in its output" "pinned on 3 interface(s)" "${PIN:-}"
expect_in "2. show log carries the event" \
  "interfaces with no mtu pinned at 9000, the default before 0.152.1: loop7, pg1, pg1.200" \
  "$LOG1"
expect "2. the tuner keeps VPP's 9000 on that box: no ethernet stanza" \
  test -z "$KEPT_STANZA"
expect "2. on a 1500 dataplane the pinned port comes up at 9000 (got $UP_PORT)" \
  test "$UP_PORT" = 9000
expect "2. and the pinned VLAN (got $UP_VLAN)" test "$UP_VLAN" = 9000
expect "2. and the pinned loopback (got $UP_LOOP)" test "$UP_LOOP" = 9000
expect "2. no mtu on a pinned interface goes back to 1500 (got $NOPIN)" \
  test "$NOPIN" = 1500
expect "2. and the upgrade run again does not pin it back" \
  sh -c "! printf '%s' \"\$1\" | grep -q 'mtu 9000'" _ "$LOOP_BLOCK"
verdict
