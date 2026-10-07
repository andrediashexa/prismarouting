#!/bin/sh
# show log, phase 2: what the box sees happen, recorded by the host's watcher (#588).
#
#   sh tools/lab.sh show-log-watch       what stands when the watcher starts,
#                                        said once (phase 3); a link down and
#                                        up, a module out and in, BGP and BFD
#                                        sessions dropped, a failed SSH login,
#                                        a pool filled - each read back from
#                                        show log with its reason
#   sh tools/lab.sh show-log-watch off   the record cannot be kept - every
#                                        check on what show log says MUST fail
#
# THE WATCHER IS THE PRODUCT'S, ONE PASS AT A TIME. On a box it is
# prisma-routing-events-watch.service, `prismash --watch-events`, a tick
# every 10 s; here `prismash --watch-events once` is one tick of every kind,
# run after each thing the lab does, so each event is read back from the
# pass that saw it. Same code, same state file, same record.
#
# WHAT IS REAL AND WHAT STANDS IN:
#   * the link is a memif pair inside the one dataplane: memif1/0 (master,
#     with a sub-interface) and memif2/0 (its far end). af_packet and tap
#     ports never report link down in this container (lab/vrrp.sh measured
#     it); a memif's link is its peer's connection, so deleting memif2/0 is
#     the far end going away and memif1/0's link goes down in `show
#     hardware-interfaces`, as a cut fibre does on a card;
#   * the module is the plugin's stand-in (lab/transceiver.sh): bytes from
#     test/transceiver_fixtures.py, served by `show bpa transceiver` as a
#     DPDK port's would be - a blank EEPROM is a module taken out;
#   * BGP and BFD are FRR on both ends of a veth, the box's side configured
#     through the VTY; the peer shuts the session with a message, and its
#     bfdd is killed;
#   * the login is OpenSSH's sshd and ssh, with a wrong password and a right
#     one, read from the wtmp and btmp they write;
#   * the pool is four blocks, and five subscribers from the packet
#     generator;
#   * WHAT STANDS AT START (#588 phase 3, the X520 on 10.40.200.166 whose RX
#     stood below its warning before the watcher ever ran): memif3/0 is a
#     master with no far end at all, its link down from the moment it is
#     made, and the module's stand-in starts with its light low. The first
#     pass says each once, "standing at start: ..."; the second - a new
#     process, a restart of the watcher in the same boot - says nothing,
#     because the state file carries them; and no later pass repeats them.
#
# The off run points the record at a path nothing can write, as
# lab/show-log.sh's does: the box with nowhere to keep it. Every action
# runs as before; the controls (the link really went down, the session
# really dropped, btmp really has the failure, the pool really refused) stay
# green.
#
# OFF-RED-AT-LEAST: 22
#
# A FLOOR, measured on the off run through tools/run-all-labs.sh (#588 phase
# 2, 2026-10-07): 18 of its 24 checks red; phase 3's four on what stands at
# start are red there too (measured below, 2026-10-07). Green in that run and rightly:
# the five controls, and `kind weather` refused, which the VTY refuses
# whether or not anything was kept. See lab/bypass.sh for why it is a floor and not a
# target.
set -u
MODE=${1:-on}

mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api, wait_vpp
. /src/lab/verdict.sh
use_binary_api
export PRISMA_ROLLBACK=/tmp/rollback PRISMA_CONFIG=/tmp/prisma-routing.conf
export PRISMA_FRR_STARTED=/var/run/frr/zebra.pid
export PRISMA_EVENTS=/var/log/prisma-routing/events.log
export PRISMA_EVENTS_STATE=/var/log/prisma-routing/events.state
export PRISMA_WATCH_STATE=/var/log/prisma-routing/events-watch.state
export PRISMA_WTMP=/var/log/wtmp PRISMA_BTMP=/var/log/btmp
# No doors daemon: every write is local, as on a box whose daemon is not
# listening.
export PRISMA_DOORS=/tmp/no-doors.sock
rm -rf /tmp/rollback "$PRISMA_EVENTS" "$PRISMA_EVENTS.1" "$PRISMA_EVENTS_STATE" \
       "$PRISMA_WATCH_STATE"
if [ "$MODE" = off ]; then
  echo "  MODE=off: the record of events points at a path nothing can write."
  export PRISMA_EVENTS=/proc/no-such-dir/events.log
  export PRISMA_WATCH_STATE=/proc/no-such-dir/events-watch.state
fi
HX="python3 /src/src/prismash/prismash.py"
pass_() { $HX --watch-events once 2>&1 | tail -1; }

echo "=========== the box: a dataplane, FRR, a peer, sshd ==========="
vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp
vppctl create memif socket id 1 filename /run/vpp/lab-memif.sock
vppctl create interface memif id 0 socket-id 1 master
vppctl create memif socket id 2 filename /run/vpp/lab-memif.sock
vppctl create interface memif id 0 socket-id 2 slave
vppctl set interface state memif1/0 up
vppctl set interface state memif2/0 up
vppctl create sub-interfaces memif1/0 100
vppctl set interface state memif1/0.100 up
# A port whose far end never came: down before anything watched (phase 3).
vppctl create memif socket id 3 filename /run/vpp/lab-memif-alone.sock
vppctl create interface memif id 0 socket-id 3 master
vppctl set interface state memif3/0 up
hexof() {   # hexof <fixture expression> - its bytes as one hex word
  python3 -c "import sys; sys.path.insert(0, '/src/test'); \
import transceiver_fixtures as F; print(($1).hex())"
}
# Its light already low when the watcher first reads it (phase 3).
vppctl bpa transceiver stand-in memif1/0 type sff-8472 hex "$(hexof 'F.sfp_low_rx()')"

# The small pool: 16 ports in 4-port blocks, one subscriber a block.
vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface state pg0 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl bpa pool small inside 100.64.0.0/22 outside 198.19.24.16/32 \
    ports 1024-1039 block 4 mode dynamic max-blocks 1
vppctl set interface bpa in2out pg0
send() {   # one UDP packet from subscriber 100.64.0.<n>
  vppctl packet-generator delete s >/dev/null 2>&1
  vppctl packet-generator new "name s limit 1 node ip4-input interface pg0 \
      size 60-60 data { UDP: 100.64.0.$1 -> 8.8.8.8 UDP: 5000 -> 53 incrementing 8 }"
  vppctl packet-generator enable >/dev/null; sleep 0.4
  vppctl packet-generator delete s >/dev/null 2>&1
}

# BGP and BFD over a veth, FRR on both ends.
ip link add veth-a type veth peer name veth-b 2>/dev/null
ip netns add peer 2>/dev/null
ip link set veth-b netns peer
ip addr add 10.9.0.1/24 dev veth-a; ip link set veth-a up
ip netns exec peer ip addr add 10.9.0.2/24 dev veth-b
ip netns exec peer ip link set veth-b up
ip netns exec peer ip link set lo up
mkdir -p /var/run/frr /etc/frr /var/run/frr/peer /etc/frr/peer
for d in zebra bgpd bfdd; do printf 'hostname prisma-routing\n' > /etc/frr/$d.conf; done
: > /etc/frr/peer/vtysh.conf
printf 'hostname upstream\n' > /etc/frr/peer/zebra.conf
printf 'hostname upstream\n' > /etc/frr/peer/bfdd.conf
cat > /etc/frr/peer/bgpd.conf <<'EOF'
hostname upstream
router bgp 65001
 bgp router-id 10.9.0.2
 no bgp ebgp-requires-policy
 neighbor 10.9.0.1 remote-as 65000
 neighbor 10.9.0.1 timers connect 5
 neighbor 10.9.0.1 bfd
exit
EOF
chown -R frr:frr /var/run/frr /etc/frr
start_frr bgpd bfdd
for d in zebra bfdd bgpd; do
  ip netns exec peer /usr/lib/frr/$d -d -N peer -A 127.0.0.1 -f /etc/frr/peer/$d.conf 2>/dev/null
done
CFG=$($HX -c configure -c "router bgp 65000" -c "bgp router-id 10.9.0.1" \
  -c "no bgp ebgp-requires-policy" -c "neighbor 10.9.0.2 remote-as 65001" \
  -c "neighbor 10.9.0.2 timers connect 5" -c "neighbor 10.9.0.2 bfd" \
  -c exit -c commit 2>&1)
echo "$CFG" | sed 's/^/  /'

# sshd, and an account made through the VTY with a password.
install -D -m 0755 /src/appliance/prismash-login /usr/bin/prismash-login
mkdir -p /run/sshd /etc/ssh/sshd_config.d
ssh-keygen -A >/dev/null 2>&1
printf 'PasswordAuthentication yes\nListenAddress 127.0.0.1\n' \
  > /etc/ssh/sshd_config.d/lab-show-log.conf
touch /var/log/wtmp /var/log/btmp
HASH=$(openssl passwd -6 lab-noc-secret)
$HX -c configure -c "user noc" -c "password $HASH" -c exit -c commit 2>&1 \
  | sed 's/^/  /'
/usr/sbin/sshd
printf '#!/bin/sh\necho not-the-password\n' > /tmp/ask-bad
printf '#!/bin/sh\necho lab-noc-secret\n' > /tmp/ask-good
chmod +x /tmp/ask-bad /tmp/ask-good
ssh_as() {   # ssh_as <askpass> [ssh args] - one password login to noc@127.0.0.1
  _ask=$1; shift
  SSH_ASKPASS=$_ask SSH_ASKPASS_REQUIRE=force DISPLAY=lab setsid -w \
    ssh -F /dev/null -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -o PreferredAuthentications=password -o NumberOfPasswordPrompts=1 \
    "$@" noc@127.0.0.1 "show version" < /dev/null > /dev/null 2>&1
}

bgp_state() { vtysh -c "show bgp neighbor 10.9.0.2 json" 2>/dev/null \
  | sed -n 's/.*"bgpState":"\([A-Za-z]*\)".*/\1/p' | head -1; }
bfd_status() { vtysh -c "show bfd peers json" 2>/dev/null \
  | sed -n 's/.*"status":"\([a-z-]*\)".*/\1/p' | head -1; }
link_of() { vppctl show hardware-interfaces brief 2>/dev/null | tr -d '\r' \
  | awk -v n="$1" '$1 == n { print $3 }'; }
wait_for() {  # <seconds> <cmd...>
  _s=$1; shift; _n=0
  until "$@" || [ "$_n" -ge "$((_s * 2))" ]; do sleep 0.5; _n=$((_n + 1)); done
  "$@"
}
est() { [ "$(bgp_state)" = Established ]; }
not_est() { [ "$(bgp_state)" != Established ]; }
bfd_up() { [ "$(bfd_status)" = up ]; }
bfd_down() { [ "$(bfd_status)" = down ]; }
wait_for 60 est
wait_for 30 bfd_up
echo "  BGP: $(bgp_state), BFD: $(bfd_status), memif1/0: $(link_of memif1/0)"

# PAST THE SETTLE. Within 60 s of the dataplane's or the routing daemons'
# start the watcher compares nothing (eventwatch.SETTLE): a restart is an
# event of its own, and every link coming up after it is the restart. This
# lab started both a moment ago, as a boot does. And what stands is said only
# once each has run 180 s (eventwatch.STANDING_SETTLE): at a boot a BGP
# session is still connecting a minute in.
VPP_UP=$(vppctl show clock 2>/dev/null | sed -n 's/.*Time now \([0-9]*\).*/\1/p' | head -1)
FRR_AGE=$(( $(date +%s) - $(stat -c %Y "$PRISMA_FRR_STARTED") ))
_wait=$(( 186 - ${VPP_UP:-0} )); [ $(( 186 - FRR_AGE )) -gt "$_wait" ] && _wait=$(( 186 - FRR_AGE ))
[ "$_wait" -gt 0 ] && { echo "  waiting ${_wait}s past the settle"; sleep "$_wait"; }

echo
echo "=========== 1. the first pass: what stands is said, once ==========="
P0=$(pass_); echo "  $P0"
STAND=$($HX -c "show log last 1000" 2>&1 | grep -A2 "standing at start")
echo "$STAND" | sed 's/^/  /'
# A new process, as a restart of the watcher is: the state file carries what
# was said, so nothing again.
P1=$(pass_); echo "  a restart of the watcher: $P1"
# The light back to normal: a change, said as one.
vppctl bpa transceiver stand-in memif1/0 type sff-8472 hex "$(hexof 'F.sfp_internal()')"
echo "  $(pass_)"

echo
echo "=========== 2. a link: the far end goes, and comes back ==========="
vppctl delete interface memif memif2/0
wait_for 10 sh -c "[ \"\$(vppctl show hardware-interfaces brief | tr -d '\r' | awk '\$1 == \"memif1/0\" {print \$3}')\" = down ]"
LINK_DOWN=$(link_of memif1/0); echo "  memif1/0's link: $LINK_DOWN"
echo "  $(pass_)"
vppctl create interface memif id 0 socket-id 2 slave
vppctl set interface state memif2/0 up
wait_for 10 sh -c "[ \"\$(vppctl show hardware-interfaces brief | tr -d '\r' | awk '\$1 == \"memif1/0\" {print \$3}')\" = up ]"
echo "  memif1/0's link: $(link_of memif1/0)"
echo "  $(pass_)"
vppctl set interface state memif1/0.100 down
echo "  $(pass_)"

echo
echo "=========== 3. a module: its light low, taken out, put back ==========="
vppctl bpa transceiver stand-in memif1/0 type sff-8472 hex "$(hexof 'F.sfp_low_rx()')"
echo "  $(pass_)"
vppctl bpa transceiver stand-in memif1/0 type sff-8472 hex "$(python3 -c "print('00' * 512)")"
echo "  $(pass_)"
vppctl bpa transceiver stand-in memif1/0 type sff-8472 hex "$(hexof 'F.sfp_internal()')"
echo "  $(pass_)"

echo
echo "=========== 4. BGP: the peer shuts the session, then lets it back ==========="
ip netns exec peer vtysh -N peer -c "configure terminal" -c "router bgp 65001" \
  -c "neighbor 10.9.0.1 shutdown message maintenance window" >/dev/null 2>&1
wait_for 20 not_est
BGP_DOWN=$(bgp_state); echo "  BGP: $BGP_DOWN"
echo "  $(pass_)"
ip netns exec peer vtysh -N peer -c "configure terminal" -c "router bgp 65001" \
  -c "no neighbor 10.9.0.1 shutdown" >/dev/null 2>&1
wait_for 60 est
echo "  BGP: $(bgp_state)"
wait_for 30 bfd_up
echo "  $(pass_)"

echo
echo "=========== 5. BFD: the peer's bfdd dies ==========="
# Killed, not stopped: a bfdd told to stop sends AdminDown first, and the
# box's side says "neighbor signaled session down" (the first run of this
# lab). Killed, it falls silent, and the detection time is what notices.
for p in $(ip netns exec peer pgrep -f '^/usr/lib/frr/bfdd -d -N peer'); do kill -9 "$p"; done
wait_for 20 bfd_down
BFD_DOWN=$(bfd_status); echo "  BFD: $BFD_DOWN, BGP: $(bgp_state)"
echo "  $(pass_)"

echo
echo "=========== 6. logins: a wrong password, then the right one ==========="
ssh_as /tmp/ask-bad; echo "  ssh with a wrong password exited $?"
ssh_as /tmp/ask-good -tt; echo "  ssh with the right one exited $?"
sleep 1
BTMP_N=$(( $(stat -c %s /var/log/btmp) / 384 ))
echo "  btmp holds $BTMP_N record(s); wtmp, as read:"
python3 -c "import sys; sys.path.insert(0, '/src/src/prismash'); import eventwatch as W
for r in W.parse_utmp(open('/var/log/wtmp', 'rb').read()):
    print('   ', r['type'], r['line'], r['user'] or '-', r['host'] or '-')"
echo "  $(pass_)"

echo
echo "=========== 7. the pool: five subscribers, four blocks ==========="
for n in 1 2 3 4 5; do send $n; done
BLOCKS=$(vppctl show bpa blocks | tr -d '\r' | sed -n 's/^ *total: //p')
echo "  total: $BLOCKS"
echo "  $(pass_)"

echo
echo "=========== 8. nothing happens: nothing is said ==========="
N_BEFORE=$($HX -c "show log last 1000" 2>/dev/null | grep -c '^[0-9][0-9][0-9][0-9]-')
for _ in 1 2 3; do pass_ >/dev/null; done
N_AFTER=$($HX -c "show log last 1000" 2>/dev/null | grep -c '^[0-9][0-9][0-9][0-9]-')
echo "  events before and after three idle passes: $N_BEFORE $N_AFTER"

echo
echo "=========== 9. a link that flaps is held, not written each time ==========="
for _ in 1 2 3 4 5 6; do
  vppctl delete interface memif memif2/0
  wait_for 10 sh -c "[ \"\$(vppctl show hardware-interfaces brief | tr -d '\r' | awk '\$1 == \"memif1/0\" {print \$3}')\" = down ]"
  pass_ >/dev/null
  vppctl create interface memif id 0 socket-id 2 slave
  vppctl set interface state memif2/0 up
  wait_for 10 sh -c "[ \"\$(vppctl show hardware-interfaces brief | tr -d '\r' | awk '\$1 == \"memif1/0\" {print \$3}')\" = up ]"
  pass_ >/dev/null
done
FLAPS=$($HX -c "show log kind link last 1000" 2>/dev/null \
  | grep '^[0-9][0-9][0-9][0-9]-' | grep -c 'interface memif1/0: link')
echo "  memif1/0's link changed 14 times; show log has $FLAPS of them one by one"

echo
echo "=========== 10. show log, and its kinds ==========="
ALL=$($HX -c "show log" 2>&1)
echo "$ALL" | sed 's/^/  /'
K_LINK=$($HX -c "show log kind link" 2>&1)
K_BGP=$($HX -c "show log kind bgp" 2>&1)
K_LOGIN=$($HX -c "show log kind login" 2>&1)
S_ROUTING=$($HX -c "show log source routing" 2>&1)
K_BAD=$($HX -c "show log kind weather" 2>&1)
echo "  -- kind bgp"; echo "$K_BGP" | sed 's/^/    /'
echo "  -- kind weather (refused)"; echo "$K_BAD" | sed 's/^/    /'

echo
echo "=========== VERDICT ==========="
# The controls: what the off run keeps green.
expect_in "control: memif1/0's link really went down" "down" "$LINK_DOWN"
expect "control: the BGP session really left Established while the peer held it shut" \
  sh -c "[ -n '$BGP_DOWN' ] && [ '$BGP_DOWN' != Established ]"
expect_in "control: the BFD session really went down" "down" "$BFD_DOWN"
expect "control: sshd wrote the wrong password to btmp" \
  sh -c "[ '$BTMP_N' -ge 1 ]"
expect_in "control: the pool refused a fifth subscriber" "1 refusals" "$BLOCKS"
# What show log says.
expect_said "the link down, a port's, and not administrative" \
  "interface memif1/0: link down, administratively up: the wire or the far end" "$ALL"
expect_said "with the sub-interface it took with it" \
  "its 1 sub-interface(s) with it" "$ALL"
expect_said "the link up again" "interface memif1/0: link up" "$K_LINK"
expect_said "a sub-interface shut down, said as administrative" \
  "sub-interface memif1/0.100 is administratively down: shut down by configuration" "$K_LINK"
expect_said "the module's light below its warning, with the level" \
  "dataplane memif1/0: rx power -19.00 dBm, below its low warning (-18.01 dBm)" \
  "$($HX -c "show log last 1000" 2>&1)"
expect_said "the module taken out" "module removed from memif1/0" "$ALL"
expect_said "and put back, with what it is" "module inserted in memif1/0: Rei do SFP" "$ALL"
expect_said "BGP leaving Established, with FRR's reason and the peer's message" \
  "BGP session with 10.9.0.2 (AS 65001) left Established: BGP Notification received: Cease/Administrative Shutdown ('maintenance window')" "$K_BGP"
expect_said "BGP Established again" "BGP session with 10.9.0.2 (AS 65001) is Established" "$K_BGP"
expect_said "BFD down, with bfdd's diagnostic" \
  "BFD session with 10.9.0.2 (on veth-a) down: control detection time expired" "$S_ROUTING"
expect_said "the failed login, the account typed and where from" \
  "failed login as noc (from ssh 127.0.0.1)" "$K_LOGIN"
expect_said "the login, to the VTY" "noc logged in to the VTY (by noc, from ssh 127.0.0.1)" "$K_LOGIN"
expect_said "the pool out of blocks" \
  "CGNAT pool small ran out of blocks: 1 request(s) for a block refused since the last reading, 4 of 4 blocks in use" "$ALL"
expect "the idle passes wrote nothing" \
  sh -c "[ \"$N_BEFORE\" -gt 0 ] && [ \"$N_BEFORE\" = \"$N_AFTER\" ]"
expect "a flapping link is held: 4 changes one by one, not 14" \
  sh -c "[ \"$FLAPS\" -ge 1 ] && [ \"$FLAPS\" -le 4 ]"
expect "the watcher's state is kept, so a restart of it says nothing again" \
  sh -c "[ -s \"$PRISMA_WATCH_STATE\" ]"
# What stood when the watcher started (#588 phase 3).
expect_said "standing at start: the port whose far end never came" \
  "standing at start: interface memif3/0: link down, administratively up: the wire or the far end" "$STAND"
expect_said "standing at start: the module's light already below its warning" \
  "standing at start: memif1/0: rx power -19.00 dBm, below its low warning (-18.01 dBm)" "$STAND"
expect_in "a restart of the watcher in the same boot says them not again" \
  "watch-events: 0 event(s)" "$P1"
expect "each said once in the whole run, and nothing healthy announced" \
  sh -c "[ \"\$(grep -c 'standing at start' \"$PRISMA_EVENTS\")\" = 2 ]"
expect "source routing is BGP and BFD, nothing of the dataplane's" \
  sh -c "printf '%s' \"\$1\" | grep -q 'BFD session' && ! printf '%s' \"\$1\" | grep -q memif" _ "$S_ROUTING"
expect_in "kind weather: refused, with the kinds there are" "kind is one of" "$K_BAD"
expect "and no door call or timer run reached the record" \
  sh -c "! grep -q 'door:' \"$PRISMA_EVENTS\" && [ -s \"$PRISMA_EVENTS\" ]"
verdict
