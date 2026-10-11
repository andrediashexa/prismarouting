#!/bin/sh
# OFF-RED-AT-LEAST: 16
# (the floor: what the off run of 2026-10-07 measured red - 10 before #559's
# five on bfdSessDown, bfdSessUp, their index and bfdNotificationsEnable -
# and #496's entConfigChange, 16 on 2026-10-11)
# SNMP notifications (#507): the box telling the NMS, rather than waiting to
# be asked.
#
# The oracle is a real snmptrapd receiving what the box's own snmpd sends,
# configured by the VTY as an operator would (`snmp trap-host`), with snmpd
# run as Debian-snmp as the box runs it. Each event is caused on the dataplane
# and looked for at the receiver:
#
#   a card taken down               linkDown, from the agent's IF-MIB
#   a VRRP router starting          mteTriggerFired "vrrpStateChange"
#   a pool at 90% of its blocks     mteTriggerFired "poolNearlyFull"
#   a pool with no block free       mteTriggerFired "poolExhausted" (#588)
#   a module's light below its      mteTriggerFired "transceiverStateChange"
#     warning                         (#588 phase 3), the port's stand-in
#   the module swapped for another  entConfigChange (ENTITY-MIB, #496)
#   a subscriber refused ports      mteTriggerFired "portsRefused"
#   an older dataplane than the box mteTriggerFired "dataplaneNotCurrent"
#   a boot self-test that failed    mteTriggerFired "selfTestFailed" (#524)
#   a BFD neighbour's bfdd killed,   bfdSessDown, then bfdSessUp (RFC 7331,
#     then started again              #559), from the host's watcher of
#                                     events over AgentX, naming the index
#                                     the agent's bfdSessTable has
#
# And bfdNotificationsEnable read through snmpd: true(1) while the watcher
# sends, false(2) once the destination is taken away (#559).
#
# And the half that must NOT happen: the user snmpd queries itself as is not
# an account anybody outside can read the box with.
#
# Off run (OFF=1): no destination configured. Nothing is sent, so every check
# that looks for a notification goes red.
. /src/lab/lib.sh   # startup_conf, wait_vpp
. /src/lab/verdict.sh
OFF=${1:-}
apt-get install -y -qq snmptrapd >/dev/null 2>&1
mkdir -p /run/vpp /etc/prisma-routing /etc/snmp /var/lib/snmp
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
HX="python3 /src/src/prismash/prismash.py"
export PRISMA_CONFIG=/etc/prisma-routing/prisma-routing.conf
install -D -m 0755 /src/appliance/prisma-routing-snmp-agent /usr/lib/prisma-routing/snmp-agent
# The modules' decoder beside it, as the box installs it (#590): the agent
# serves prXcvrState from it.
install -D -m 0644 /src/src/prismash/transceiver.py /usr/lib/prisma-routing/transceiver.py

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp
vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface state pg0 up
vppctl set interface ip address pg0 100.64.3.254/22
# One public address, blocks of 512: 126 blocks, so 114 subscribers is 90%
# and 126 every block (#588 phase 3: poolExhausted) - and only the first 126
# inside addresses have a block by the formula, which is why the refused
# subscriber is 100.64.0.120 and not one further out.
vppctl bpa pool small inside 100.64.0.0/22 outside 200.150.0.16/32 block 512
vppctl set interface bpa in2out pg0
vppctl vrrp vr add pg0 vr_id 7 priority 150 interval 100 100.64.3.1 >/dev/null
# A module in pg0's cage, its light normal until section 2 (#588 phase 3):
# the plugin's stand-in, as lab/transceiver.sh serves one, so prXcvrState
# has a row whose level can move.
hexof() {   # hexof <fixture expression> - its bytes as one hex word
  python3 -c "import sys; sys.path.insert(0, '/src/test'); \
import transceiver_fixtures as F; print(($1).hex())"
}
vppctl bpa transceiver stand-in pg0 type sff-8472 hex "$(hexof 'F.sfp_internal()')"
# The release installed IS the build running, as on a box booted since its
# upgrade; the upgrade comes later, with snmpd up (section 2). Measured: a
# monitor fires when its test BECOMES true, and one already true when snmpd
# starts is evaluated before the agent answers and never fires - which is
# not the box's case, where the stale state begins at an upgrade.
# The self-test's verdict as it writes it (#524), healthy until section 2.
mkdir -p /run/prisma-routing/state
printf 'fail 0\nwarn 0\nforwarding yes\nat %s\n' "$(date +%s)" \
  > /run/prisma-routing/state/selftest
chmod 0644 /run/prisma-routing/state/selftest
mkdir -p /usr/share/prisma-routing
vppctl show plugins | tr -d '\r' | awk '/bpa_plugin.so/ { print $3; exit }' \
  > /usr/share/prisma-routing/VERSION

# BFD (#559): bfdd on the box and on a neighbour across a veth, FRR as the
# box runs it, so the watcher has a session to see go down. Started here,
# well over eventwatch.SETTLE before section 3: within a minute of the
# routing daemons' start the watcher compares nothing.
ip link add veth-a type veth peer name veth-b 2>/dev/null
ip netns add peer 2>/dev/null
ip link set veth-b netns peer
ip addr add 10.9.0.1/24 dev veth-a; ip link set veth-a up
ip netns exec peer ip addr add 10.9.0.2/24 dev veth-b
ip netns exec peer ip link set veth-b up
ip netns exec peer ip link set lo up
mkdir -p /var/run/frr /etc/frr /var/run/frr/peer /etc/frr/peer
for d in zebra mgmtd staticd bfdd; do printf 'hostname prisma-routing\n' > "/etc/frr/$d.conf"; done
: > /etc/frr/peer/vtysh.conf
printf 'hostname upstream\n' > /etc/frr/peer/zebra.conf
printf 'hostname upstream\nbfd\n peer 10.9.0.1 interface veth-b\n  receive-interval 100\n  transmit-interval 100\n exit\nexit\n' \
  > /etc/frr/peer/bfdd.conf
chown -R frr:frr /var/run/frr /etc/frr
start_frr bfdd
peer_bfdd() { ip netns exec peer /usr/lib/frr/bfdd -d -N peer -A 127.0.0.1 \
  -f /etc/frr/peer/bfdd.conf 2>/dev/null; }
ip netns exec peer /usr/lib/frr/zebra -d -N peer -A 127.0.0.1 \
  -f /etc/frr/peer/zebra.conf 2>/dev/null
peer_bfdd
# The agent reads bfdd through the host's doors as Debian-snmp (#571), as
# on the box, where root's prisma-routing-doors.service answers.
python3 /src/src/prismash/doors.py --serve > /tmp/doors.log 2>&1 &
for _i in 1 2 3 4 5 6 7 8 9 10; do
  [ -S /run/prisma-routing/doors.sock ] && break; sleep 0.5
done

echo "=========== 1. a destination, from the VTY ==========="
# One statement as ONE argument: a variable holding "-c snmp trap-host ..."
# is split by the shell into words, and the first run of this lab configured
# no destination at all.
TRAPHOST="snmp trap-host 127.0.0.1 community tr4pc"
[ "$OFF" = off ] && TRAPHOST="snmp community hexro"
$HX -c "configure" -c "snmp enabled yes" -c "snmp listen-address 127.0.0.1" \
    -c "snmp enterprise-number 99999" -c "snmp community hexro" \
    -c "$TRAPHOST" -c "bfd" -c "peer 10.9.0.2 interface veth-a" \
    -c "receive-interval 100" -c "transmit-interval 100" -c "exit" -c "exit" \
    -c "commit" 2>&1 | sed 's/^/  /'
RUNNING=$($HX -c "show running-config" 2>&1 | grep "trap-host")
echo "  running: ${RUNNING:-(no destination)}"
# The receiver: on 162, where the box sends.
echo "disableAuthorization yes" > /etc/snmp/snmptrapd.conf
snmptrapd -f -Lf /tmp/traps.log -On udp:127.0.0.1:162 &
chgrp vpp /run/vpp/cli.sock /run/vpp/api.sock /run/vpp/stats.sock 2>/dev/null
chown Debian-snmp:Debian-snmp /var/lib/snmp
gpasswd -a Debian-snmp vpp >/dev/null
snmpd -Lo -f -C -u Debian-snmp -g Debian-snmp -c /etc/snmp/snmpd.conf \
  > /tmp/snmpd.log 2>&1 &
sleep 10
# THE HOST'S WATCHER OF EVENTS (#588 phase 2), as prisma-routing-events-
# watch.service runs it: resident, a tick every 10 s, root. It sends the
# BFD notifications (#559). Its record in a directory of its own.
mkdir -p /var/log/prisma-routing
touch /tmp/wtmp /tmp/btmp
PRISMA_FRR_STARTED=/var/run/frr/zebra.pid PRISMA_DOORS=/tmp/no-doors.sock \
  PRISMA_WTMP=/tmp/wtmp PRISMA_BTMP=/tmp/btmp \
  $HX --watch-events > /tmp/watch.log 2>&1 &
WATCHER=$!

echo
echo "=========== 2. the events ==========="
vppctl vrrp proto start pg0 vr_id 7 >/dev/null
# A TCP collector that takes the records and then goes away (#509): the
# transport connecting and dropping are two changes of prLogTransportConnected.
python3 -c "
import socket
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', 4740)); s.listen(1)
c, _ = s.accept()
while c.recv(65536): pass
" > /dev/null 2>&1 &
COLLECTOR=$!
vppctl bpa logging tcp 127.0.0.1 port 4740 >/dev/null
# The first 126 inside addresses are 100.64.0.0 to .125: the formula's, and
# the 126 blocks - the first run sent .1 to .126 and held 125.
vppctl packet-generator new "name subs limit 126 rate 1000 node ip4-input \
  interface pg0 size 60-60 data { UDP: 100.64.0.0 - 100.64.0.125 -> 8.8.8.8 \
  UDP: 40000 -> 53 incrementing 1 }" >/dev/null
vppctl packet-generator new "name full limit 520 rate 2000 node ip4-input \
  interface pg0 size 60-60 data { UDP: 100.64.0.120 -> 8.8.8.8 \
  UDP: 41000 - 41519 -> 53 incrementing 1 }" >/dev/null
vppctl packet-generator enable >/dev/null
sleep 3
echo "  $(vppctl show bpa pool | grep -E 'utilisation') ; $(vppctl show bpa top 1 | sed -n 2p)"
echo "  the refused subscriber: $(vppctl show bpa top 200 | grep '100.64.0.120 ')"
# The upgrade: a newer release on disk, the old build still forwarding.
echo "9.9.9" > /usr/share/prisma-routing/VERSION
# And a self-test that found two failures, as the next boot's would write it.
printf 'fail 2\nwarn 0\nforwarding yes\nat %s\n' "$(date +%s)" \
  > /run/prisma-routing/state/selftest
echo "  prPortRefusals now: $(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.4.1.99999.1.5.0 2>&1)"
echo "  $(vpp_get_stats socket-name /run/vpp/stats.sock dump '^/bpa/pool/refused$' | tr '\n' ' ')"
echo "  TCP transport: $(vppctl show bpa logging | tr -d '\r' | grep -iA2 '^TCP' | tr '\n' ' ')"
sleep 40
kill "$COLLECTOR" 2>/dev/null
sleep 30
vppctl set interface state pg0 down
# The light past its low warning, a minute after the monitor's first
# reading of it: -D fires on the change (#588 phase 3). And a module of
# ANOTHER serial number: the one in the cage swapped for one whose light is
# low, so ENTITY-MIB's entLastChangeTime moves and entConfigChange is sent
# (#496 phase 5) - after the agent's 30 s cache and the monitor's minute,
# hence the longer wait.
ENTBEFORE=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.47.1.4.1.0 2>&1)
vppctl bpa transceiver stand-in pg0 type sff-8472 hex "$(hexof 'F.sfp_swapped()')"
echo "  pg0's module: $(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.4.1.99999.1.20.1.1.18 2>&1 | head -1)"
sleep 100
ENTAFTER=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.47.1.4.1.0 2>&1)
echo "  entLastChangeTime: $ENTBEFORE before the swap, $ENTAFTER after"
TRAPS=$(cat /tmp/traps.log 2>/dev/null)
NAMES=$(printf '%s\n' "$TRAPS" | grep -oE 'STRING: "[A-Za-z]+"' | sort | uniq -c)
printf '%s\n' "$NAMES" | sed 's/^/  /'
LINKDOWN=$(printf '%s\n' "$TRAPS" | grep -c "OID: .1.3.6.1.6.3.1.1.5.3")
echo "  linkDown notifications: $LINKDOWN"
ENTCHANGE=$(printf '%s\n' "$TRAPS" | grep -c "OID: .1.3.6.1.2.1.47.2.0.1\b")
echo "  entConfigChange notifications: $ENTCHANGE"

echo
echo "=========== 3. BFD: the neighbour's bfdd dies, and comes back (#559) ==========="
bfd_status() { vtysh -c "show bfd peers json" 2>/dev/null \
  | sed -n 's/.*"status":"\([a-z-]*\)".*/\1/p' | head -1; }
wait_for() {  # <seconds> <cmd...>
  _s=$1; shift; _n=0
  until "$@" || [ "$_n" -ge "$((_s * 2))" ]; do sleep 0.5; _n=$((_n + 1)); done
  "$@"
}
bfd_up() { [ "$(bfd_status)" = up ]; }
bfd_down() { [ "$(bfd_status)" = down ]; }
trap_count() { grep -c "OID: .1.3.6.1.2.1.222.0.$1\b" /tmp/traps.log 2>/dev/null; }
wait_for 30 bfd_up
BFD_BEFORE=$(bfd_status)
BFDIX=$(snmpwalk -v2c -c hexro -On 127.0.0.1 1.3.6.1.2.1.222.1.2.1.11 2>&1 \
  | awk -F'[ .]' '/^\.1\.3\.6\.1\.2\.1\.222\.1\.2\.1\.11\./ {print $13; exit}')
NOTIF_ON=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.222.1.1.3.0 2>&1)
echo "  BFD: $BFD_BEFORE, bfdSessIndex $BFDIX, bfdNotificationsEnable $NOTIF_ON"
echo "  the watcher's file: $(tr '\n' ' ' < /run/prisma-routing/state/bfd-notifications 2>&1)"
# Killed, not stopped: a bfdd told to stop sends AdminDown first; killed it
# falls silent and the detection time is what notices (lab/show-log-watch.sh).
for p in $(ip netns exec peer pgrep -f '^/usr/lib/frr/bfdd -d -N peer'); do kill -9 "$p"; done
wait_for 20 bfd_down
T_DOWN=$(date +%s.%N)
BFD_DOWN=$(bfd_status)
echo "  BFD: $BFD_DOWN"
# What the watcher's tick costs the notification, measured: from bfdd
# saying down to snmptrapd holding bfdSessDown.
_n=0; until [ "$(trap_count 2)" -ge 1 ] || [ $_n -ge 60 ]; do sleep 0.5; _n=$((_n + 1)); done
T_TRAP=$(date +%s.%N)
if [ "$(trap_count 2)" -ge 1 ]; then
  echo "  bfdSessDown received $(python3 -c "print(round($T_TRAP - $T_DOWN, 1))") s after bfdd said down"
else
  echo "  no bfdSessDown within 30 s of bfdd saying down"
fi
# The agent's row, read after the notification: the agent keeps bfdd's
# answer for BFD_CACHE_SECONDS, and the first run read the diagnostic of
# the walk above, before the session went down.
sleep 3
DIAG_NOW=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.222.1.2.1.13.${BFDIX:-0} 2>&1)
echo "  the agent serves bfdSessDiag.$BFDIX = $DIAG_NOW"
peer_bfdd
wait_for 30 bfd_up
T_UP=$(date +%s.%N)
_n=0; until [ "$(trap_count 1)" -ge 1 ] || [ $_n -ge 60 ]; do sleep 0.5; _n=$((_n + 1)); done
T_TRAP2=$(date +%s.%N)
if [ "$(trap_count 1)" -ge 1 ]; then
  echo "  BFD: $(bfd_status); bfdSessUp received $(python3 -c "print(round($T_TRAP2 - $T_UP, 1))") s after bfdd said up"
else
  echo "  BFD: $(bfd_status); no bfdSessUp within 30 s of bfdd saying up"
fi
BFDTRAPS=$(awk '/\.1\.3\.6\.1\.2\.1\.222\.0\.[12]/' /tmp/traps.log)
printf '%s\n' "$BFDTRAPS" | sed 's/^/  | /'
DOWN_N=$(trap_count 2); UP_N=$(trap_count 1)
# snmptrapd prints a notification's varbinds on one line: bfdSessDiag of
# the session's index, twice (RFC 7331's low and high of a range of one).
DOWN_VB=$(grep "OID: .1.3.6.1.2.1.222.0.2" /tmp/traps.log | head -1 \
  | grep -o "\.1\.3\.6\.1\.2\.1\.222\.1\.2\.1\.13\.${BFDIX:-none} = INTEGER: 1" | wc -l)
UP_VB=$(grep "OID: .1.3.6.1.2.1.222.0.1" /tmp/traps.log | head -1 \
  | grep -o "\.1\.3\.6\.1\.2\.1\.222\.1\.2\.1\.13\.${BFDIX:-none} = INTEGER: [0-9]*" | wc -l)
# The destination taken away: the watcher's next tick says it is not
# sending, and the agent reads false.
$HX -c "configure" -c "no snmp trap-host 127.0.0.1" -c "commit" 2>&1 | sed 's/^/  /'
sleep 12
NOTIF_OFF=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.222.1.1.3.0 2>&1)
echo "  with no destination, bfdNotificationsEnable $NOTIF_OFF; the file: $(tr '\n' ' ' < /run/prisma-routing/state/bfd-notifications 2>&1)"
echo "  the watcher said: $(grep -v '^$' /tmp/watch.log | tail -3 | tr '\n' ' ')"
kill "$WATCHER" 2>/dev/null

echo
echo "=========== 4. the internal user is not a way in ==========="
INSIDE=$(snmpget -v3 -l noAuthNoPriv -u prismaInternal -Ovq 127.0.0.1 \
         1.3.6.1.2.1.1.1.0 2>&1)
echo "  read as prismaInternal from outside: $INSIDE"
REDACTED=$($HX -c "show running-config" 2>&1 | grep "trap-host")

echo
echo "=========== VERDICT ==========="
expect_in "the destination is in the running configuration" "trap-host 127.0.0.1" "$RUNNING"
expect_not_in "without its community on the screen" "tr4pc" "$REDACTED"
expect "a card taken down arrives as linkDown" test "${LINKDOWN:-0}" -ge 1
expect_in "the VRRP router starting arrives as vrrpStateChange" "vrrpStateChange" "$NAMES"
expect_in "a pool at 90% arrives as poolNearlyFull" "poolNearlyFull" "$NAMES"
expect_in "a subscriber refused ports arrives as portsRefused" "portsRefused" "$NAMES"
expect_in "a failed self-test arrives as selfTestFailed (#524)" \
  "selfTestFailed" "$NAMES"
expect_in "a pool with every block held arrives as poolExhausted (#588)" \
  "poolExhausted" "$NAMES"
expect_in "a module's light past its warning arrives as transceiverStateChange (#588)" \
  "transceiverStateChange" "$NAMES"
expect "a module swapped arrives as ENTITY-MIB's own entConfigChange (#496)" \
  test "${ENTCHANGE:-0}" -ge 1
expect_in "a dataplane older than the release arrives as dataplaneNotCurrent" \
  "dataplaneNotCurrent" "$NAMES"
expect_in "a records transport connecting and dropping arrives as recordsTransportChange" \
  "recordsTransportChange" "$NAMES"
# BFD (#559). The controls first: what the off run keeps green.
expect_in "control: the BFD session was up" "up" "$BFD_BEFORE"
expect_in "control: the BFD session really went down" "down" "$BFD_DOWN"
expect "a session down arrives as bfdSessDown, once" test "${DOWN_N:-0}" -eq 1
expect "and up again as bfdSessUp, once" test "${UP_N:-0}" -eq 1
expect "bfdSessDown names the agent's bfdSessIndex ($BFDIX) with diagnostic 1, as bfdSessDiag twice" \
  test "${DOWN_VB:-0}" -eq 2
expect "bfdSessUp names the same index, twice" test "${UP_VB:-0}" -eq 2
expect "with bfdd's diagnostic, control detection time expired (1), as the agent serves it" \
  test "$DIAG_NOW" = "1"
expect_in "bfdNotificationsEnable true(1) while the watcher sends" "1" "$NOTIF_ON"
expect_in "control: false(2) with no destination" "2" "$NOTIF_OFF"
expect "and nobody reads the box as the internal user" \
  sh -c "case \"$INSIDE\" in *Prisma*) exit 1;; *) exit 0;; esac"
verdict
