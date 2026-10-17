#!/bin/sh
# PPPoE subscribers end to end: prisma-bng, RADIUS, CGNAT, rate limits, SNMP (#597, #607).
#
#   sh tools/lab.sh pppoe        -> LAB PASSED
#   sh tools/lab.sh pppoe off    -> LAB FAILED (PPPoE never configured)
#
# OFF-RED-AT-LEAST: 116
#
# WHAT IS REAL HERE. Real PPPoE clients - pppd with rp-pppoe, one network
# namespace each, behind a Linux bridge, one of them on VLAN 100 - dial the
# dataplane. Two real RADIUS servers (freeradius, on 127.0.0.1 and 127.0.0.2)
# authenticate them and receive their accounting, and send them Disconnect
# and CoA with radclient; iperf3 measures what a session's policer lets
# through. The control plane is accel-ppp-ng compiled by
# build/accel-ppp-build.sh, the recipe the prisma-bng image is built with,
# run by appliance/prisma-bng-entrypoint and attached to the CGNAT by
# appliance/prisma-bng-session - and all of it is CONFIGURED THROUGH THE VTY:
# `pppoe` statements committed, the container's files generated from the
# candidate, the container started by appliance/prisma-bng. Nothing below
# types `create pppoe session` or edits accel-ppp.conf.
#
# WHAT IS PLAYED. The lab runs in a container and cannot start the prisma-bng
# image, so lab/pppoe-shim stands in for docker and docker-compose: "start"
# runs the entrypoint under a loop that restarts it when it exits, as
# `restart: unless-stopped` does. tools/build-images.sh `check` starts the
# image itself, and its `compose-check` the service.
#
# WHAT IS ASSERTED, each against the thing itself rather than a counter:
#   * PAP, CHAP and MS-CHAPv2 sessions come up against RADIUS, one of them on
#     a VLAN; a wrong password is refused (Access-Reject) and gets no session;
#   * the address comes from RADIUS (Framed-IP-Address) for one subscriber and
#     from the pool for the others, and the DNS servers reach the CPE;
#   * the subscriber's traffic leaves on the block ref/bpa_model.py predicts,
#     on the captured packet, and its reply comes back through the session;
#   * accounting Start, Interim-Update with the session's real byte count,
#     and Stop reach the RADIUS server;
#   * `show running-config` holds the block and never the secret, and no
#     session interface;
#   * `clear pppoe session` ends one session and the CPE redials;
#   * a restart of the container drops every session, they redial, and a
#     subscriber that is NOT on PPPoE keeps forwarding throughout;
#   * `no pppoe` stops the container, its sessions go, and the dataplane
#     survives the PADIs that keep arriving with nothing to answer them;
# and since #607:
#   * RADIUS is configured under `aaa` > `radius`, never on a screen;
#   * a rate from Prisma-Rate-Limit-Down/-Up (vendor 67033, the
#     dictionary `show aaa radius dictionary` prints) is a VPP policer on the session, each
#     way, that iperf3 measures within 10%; a session with none is not
#     limited; a CoA changes it on the live session (CoA-ACK), a CoA with a
#     rate past 10 Gbit/s, or an attribute nothing acts on - MikroTik's
#     rate among them, which is not read - is NAKed;
#   * a Disconnect-Request by User-Name or by Acct-Session-Id ends the
#     session (Disconnect-ACK, Stop with Admin-Reset); a wrong secret and a
#     source that is not a client get no answer; a CoA Session-Timeout ends
#     the session when it says;
#   * failover: the first server answers everything; killed, a new session
#     still authenticates - on the second - within seconds, and the first is
#     shown failed; back, it is used again once its dead-time is over;
#   * round-robin, by a commit that is a RELOAD (no session drops): sessions
#     authenticate and account on both servers;
#   * the policers are gone with the sessions: VPP's count is back to zero;
# and the SNMP agent's prBng (#597), read by snmpwalk from a real snmpd
# running as Debian-snmp, through the host's doors, as on a box:
#   * the sessions up are the ones the VTY lists, and started minus stopped;
#   * the RADIUS server's Access-Accepts and -Rejects are the ones its own
#     log says it sent, and move with a login and a refused one;
#   * the counters start again with the control plane, and say so;
#   * every CoA and Disconnect answered is counted ACK or NAK, as radclient
#     received it, and the ones answered with nothing as dropped;
#   * the first server's unanswered sends are counted when it dies;
#   * with `no pppoe`, notConfigured, zero, and no server row;
# and the session trail (docs/bng.md 0.7), the box's own record of who held
# which inside address from when to when:
#   * a cleared session's stop and the next one's start, with the user, MAC,
#     interface, address and cause, at the times they happened;
#   * a control plane killed with -9: the sessions it held closed by the next
#     one as Control-Plane-Lost, and nothing written before lost;
#   * a public address and port to the PPPoE user: by the formula at an
#     instant, and by the block log - the pool made dynamic, the
#     BLOCK-ALLOC a syslog collector received for the port caught on the
#     wire, joined to the trail at the log's instant;
#   * the chain verifies, and names the entry an edit changed;
#   * no accounting server answering: the session bound and ended is
#     recorded though no RADIUS server got a Start; both servers down: a
#     stop is recorded, and no start, since nobody can be authenticated;
#   * the head sent off the box: a pass of the host's watcher after
#     sessions writes a `trail` event whose seq and sha256 are `show pppoe
#     history verify`'s head, and the next pass writes none.
# and for docs/bng.md 0.4 items 3 and 4 (#597), read through the
# host's watcher (`prismash --watch-events once`) and `show log`:
#   * every start and exit of the container is an event with its reason:
#     the commit's start, the commit's restart (a stop, then a start the
#     commit asked for), accel-pppd killed with -9 (the exit with signal 9,
#     the restart policy's start), the format upgrade's restart, `no pppoe`;
#   * RADIUS server 1 failed and active again;
#   * a PADI from an interface that is not access gets no PADO, and is
#     counted; one from an access interface does;
#   * sub1's MAC dialling on a second access port (VLAN 100) is refused,
#     counted, while sub1's session lives - sub1's session never changes -
#     and gets the MAC once sub1 hangs up.
# and for docs/bng.md 0.4 item 2 (#597), through the management API over
# mutual TLS, the server the box runs, the stubs generated from the contract:
#   * the sessions, one session with its policers, the summary with the
#     trail's and the refusals' counters, the history and its verify - each
#     held to the VTY's own screen, read around the call;
#   * GetConfig's pppoe and aaa areas, secret_set and no secret in a byte;
#   * Clear by address, by id and by user ends the session, which redials,
#     and the trail closes it; an account without the role is refused.
# and IPv6 on the sessions (#609, docs/bng.md 0.8), section 23, with real
# CPEs dialling with pppd's +ipv6, the kernel doing SLAAC and dhcpcd asking
# for a delegated prefix:
#   * the WAN /64 from `ipv6 prefix-pool` by RA, and a /56 delegated by
#     DHCPv6 from `ipv6 delegated-pool` - or, for sub8, RADIUS's
#     Delegated-IPv6-Prefix - each reached from the internet through VPP and
#     answered back, and still after the API's commits;
#   * the dataplane's guard counted the RS and DHCPv6 it sent to the control
#     plane and the answers it took off it;
#   * the session's 20/10 Mbit/s plan holds over IPv6, iperf3 within 10%;
#   * show pppoe sessions / session / summary, the trail (the start's prefix,
#     the delegate entry), show pppoe history by an IPv6 address - at an
#     instant before the delegation, nobody - and the API, carry the
#     prefixes; RADIUS's Start the WAN prefix and an interim the delegated
#     one;
#   * an IPv4-only session on the interface an IPv6 session left gets IPv6
#     dropped again.
# and per-subscriber SHAPING (#610, docs/bng.md 0.9), section 24, on the
# block with no `rate-limit download` statement - the default; sections 12
# and 13 are the policer's, the block saying `rate-limit download policer`:
#   * the download by the dataplane's shaper on the session's IPv4 and IPv6
#     output, the upload by the policer, from Prisma's attributes and from
#     Cisco-AVPair alike;
#   * one TCP flow holds the plan with few retransmits; a ping under it
#     waits what CoDel keeps, not the whole queue; one flow 200 ms away
#     still gets 75%;
#   * a burst within the queue arrives whole, paced at the plan; UDP at twice
#     the plan delivers exactly the plan, the rest dropped by the queue, and
#     a ping under it waits at most the queue;
#   * show pppoe session shows the queue, what waited and for how long;
#   * a CoA changes the shaped rate on the live session, its counters kept;
#   * a control plane killed with -9 leaves no shaper behind;
#   * through the API: the session's shapers, what enforces each way, and a
#     SetConfig that puts the download policer back; `no pppoe` leaves no
#     shaper in the dataplane.
MODE=${1:-on}
. /src/lab/lib.sh
. /src/lab/verdict.sh
set -u
HX="python3 /src/src/prismash/prismash.py"
L=/var/log/prisma-routing/bng
# NOT $L: an installed box has no /var/log/prisma-routing/bng until the
# container makes it, and the first one did not (#597, 0.153.97).
mkdir -p /run/vpp /var/log/prisma-routing /etc/prisma-routing /etc/ppp/peers
rm -rf "$L"

(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api

echo "=========== 1. THE CONTROL PLANE, BUILT THE WAY THE IMAGE BUILDS IT ==========="
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  $(sh /src/build/accel-ppp-build.sh --deps) ppp pppoe freeradius \
  freeradius-utils iperf3 ethtool dhcpcd-base \
  > /tmp/apt.log 2>&1 || { echo "  apt failed"; tail -5 /tmp/apt.log; }
if sh /src/build/accel-ppp-build.sh /opt/accel-root > /tmp/accel-build.log 2>&1; then
  cp -a /opt/accel-root/. /
  echo "  $(tail -1 /tmp/accel-build.log)"
else
  echo "  the build failed:"; tail -15 /tmp/accel-build.log | sed 's/^/    /'
fi
# The container's filesystem, laid out in this one: the hook and the CLI
# wrapper where the image puts them, the shim where docker would be.
install -D -m 0755 /src/appliance/prisma-bng-session /usr/lib/prisma-bng/session
install -D -m 0755 /src/appliance/prisma-bng-cli /usr/bin/prisma-bng-cli
# Where the image puts it: `prisma-bng reload` runs it in the container.
install -D -m 0755 /src/appliance/prisma-bng-entrypoint \
  /usr/local/bin/prisma-bng-entrypoint
mkdir -p /opt/shim
ln -sf /src/lab/pppoe-shim /opt/shim/docker
ln -sf /src/lab/pppoe-shim /opt/shim/docker-compose
cp /src/appliance/compose.yaml /etc/prisma-routing/compose.yaml
# The stack's environment as load-the-images writes it on a box - the
# release's tag, never :latest - and nothing in the lab's own environment:
# the host's prisma-bng has to find it where the box keeps it.
mkdir -p /run/prisma-routing
printf 'PRISMA_VERSION=%s\nPRISMA_VPP_GID=%s\n' "$(cat /src/VERSION)" \
  "$(getent group vpp | cut -d: -f3)" > /run/prisma-routing/compose.env
unset PRISMA_VERSION
export PRISMA_COMPOSE=/opt/shim/docker-compose PRISMA_DOCKER=/opt/shim/docker
export PRISMA_BNG_CTL=/src/appliance/prisma-bng
# THE SNMP AGENT (#597, prBng), where the box installs it, and the host's
# prisma-bng where the doors run it. The doors run it with their own
# environment, not this lab's, so the stand-in hands it the docker shim the
# VTY is given above; the script it runs is the product's.
install -D -m 0755 /src/appliance/prisma-routing-snmp-agent /usr/lib/prisma-routing/snmp-agent
printf '#!/bin/sh\nPRISMA_COMPOSE=/opt/shim/docker-compose PRISMA_DOCKER=/opt/shim/docker exec sh /src/appliance/prisma-bng "$@"\n' \
  > /usr/lib/prisma-routing/prisma-bng
chmod 0755 /usr/lib/prisma-routing/prisma-bng

echo
echo "=========== 2. TOPOLOGY ==========="
# acc0 is the access card (VPP host-acc0), behind a bridge every CPE is on;
# wan0 the upstream, with the "internet" in netns inet; lan0 a subscriber
# that is NOT on PPPoE, for what a restart of the container must not touch.
# VLAN-AWARE, so that one MAC on two VLANs is two entries of the bridge's
# table and not one that flaps between two ports: section 19 puts sub1's MAC
# on VLAN 100 too, as a cloned CPE on another OLT port would be.
ip link add br0 type bridge vlan_filtering 1; ip link set br0 up
ip link add acc0 type veth peer name accb
ip link set acc0 up; ip link set accb master br0; ip link set accb up
bridge vlan add dev accb vid 100
ip link add wan0 type veth peer name wanp; ip link set wan0 up
ip netns add inet
ip link set wanp netns inet
ip netns exec inet ip link set lo up
ip netns exec inet ip link set wanp up
ip netns exec inet ip addr add 200.150.0.2/24 dev wanp
ip netns exec inet ip route add 200.150.0.16/30 via 200.150.0.1
# The internet's IPv6 (#609): the subscribers' prefixes are routed to the box.
ip netns exec inet ip -6 addr add 2001:db8:ffff::2/64 dev wanp nodad
ip netns exec inet ip -6 route add default via 2001:db8:ffff::1
# No segmentation offload toward the dataplane. The "internet"'s TCP would
# hand VPP's af_packet 64 kB GSO frames, VPP would PPPoE-encapsulate them
# whole, and the kernel cannot segment a PPPoE frame on its way out of
# host-acc0: measured, "tx sendto temporary failure" on host-acc0-tx, its
# TX ring stuck, and every CPE's LCP echoes unanswered until each session
# dropped. A lab artefact - a box's DPDK card takes no GSO frame from a veth.
ip netns exec inet ethtool -K wanp tso off gso off gro off > /dev/null 2>&1
ip link add lan0 type veth peer name lanp; ip link set lan0 up
ip netns add plain
ip link set lanp netns plain
ip netns exec plain ip link set lo up
ip netns exec plain ip link set lanp up
ip netns exec plain ip addr add 100.64.1.2/24 dev lanp
ip netns exec plain ip route add default via 100.64.1.1

# sub1 PAP, address from RADIUS; sub2 CHAP, from the pool; sub3 MS-CHAPv2 on
# VLAN 100, from the pool; sub4 a wrong password. Every CPE probes its link
# with LCP echoes, as a real one does (faster here): a control plane killed
# with -9 sends no PADT, and the echo is how a CPE learns to redial.
# s5 is sub1's MAC again, on VLAN 100 (section 19); s6 only sends PADIs.
# s7 and s8 dial with IPv6 (#609, section 23): sub7 from the pools, sub8 its
# delegated prefix from RADIUS.
for i in 1 2 3 4 5 6 7 8; do
  ip netns add "s$i"
  ip link add "cli$i" type veth peer name "br$i"
  ip link set "br$i" master br0; ip link set "br$i" up
  ip link set "cli$i" netns "s$i"
  ip netns exec "s$i" ip link set "cli$i" address "02:aa:00:00:00:0$i"
  ip netns exec "s$i" ip link set "cli$i" up
  ip netns exec "s$i" ip link set lo up
done
ip netns exec s3 ip link add link cli3 name cli3.100 type vlan id 100
ip netns exec s3 ip link set cli3.100 up
bridge vlan add dev br3 vid 100
ip netns exec s5 ip link set cli5 address 02:aa:00:00:00:01
ip netns exec s5 ip link add link cli5 name cli5.100 type vlan id 100
ip netns exec s5 ip link set cli5.100 up
bridge vlan add dev br5 vid 100
bridge vlan del dev br5 vid 1
peer() {  # <n> <nic> <auth options>
  cat > "/etc/ppp/peers/sub$1" <<P
plugin rp-pppoe.so
nic-$2
user sub$1
noauth
noipdefault
usepeerdns
persist
maxfail 0
holdoff 3
lcp-echo-interval 2
lcp-echo-failure 3
nodetach
$3
P
}
peer 1 cli1 "refuse-chap
refuse-mschap
refuse-mschap-v2"
peer 2 cli2 "refuse-pap
refuse-mschap
refuse-mschap-v2"
peer 3 cli3.100 "refuse-pap
refuse-chap
refuse-mschap"
peer 4 cli4 ""
peer 5 cli5.100 ""
# IPv6CP asked for (#609): pppd's +ipv6. Debian's pppd 2.4.9 negotiates it
# without the word too (measured: sub1 to sub3, dialling with none, had
# SLAAC addresses once the box offered IPv6), so every CPE here is dual-
# stack, as most are in the field; sub8's twin says noipv6, for the
# interface an IPv6 session leaves behind (section 23).
peer 7 cli7 "+ipv6"
peer 8 cli8 "noipv6"
mv /etc/ppp/peers/sub8 /etc/ppp/peers/sub8v4
peer 8 cli8 "+ipv6"
printf '"sub1" * "pw1" *\n"sub2" * "pw2" *\n"sub3" * "pw3" *\n"sub4" * "WRONG" *\n"sub5" * "pw5" *\n"sub7" * "pw7" *\n"sub8" * "pw8" *\n' \
  > /etc/ppp/pap-secrets
cp /etc/ppp/pap-secrets /etc/ppp/chap-secrets

echo "  br0: acc0 (VPP host-acc0, VLAN 100 = host-acc0.100) + four CPEs, s5 (sub1's"
echo "  MAC on VLAN 100) and s6 (PADIs only)"
echo "  wan0: the internet (netns inet); lan0: a subscriber not on PPPoE"

echo
echo "=========== 3. RADIUS ==========="
R=/etc/freeradius/3.0
printf 'client lo {\n  ipaddr = 127.0.0.1\n  secret = testing123\n}\n' > $R/clients.conf
# Prisma's dictionary (#607), installed on the server as an operator is told
# to: `show aaa radius dictionary`'s file, $INCLUDEd. sub1's plan is 20
# Mbit/s down and 10 up, in Prisma's attributes, kbit/s.
cp /src/appliance/dictionary.prisma /usr/share/freeradius/dictionary.prisma
grep -q dictionary.prisma $R/dictionary \
  || echo '$INCLUDE /usr/share/freeradius/dictionary.prisma' >> $R/dictionary
install -D -m 0644 /src/appliance/dictionary.prisma \
  /usr/share/prisma-bng/dictionary.prisma
cat > $R/mods-config/files/authorize <<'U'
sub1 Cleartext-Password := "pw1"
     Framed-IP-Address = 100.64.0.77,
     Prisma-Rate-Limit-Down = 20000,
     Prisma-Rate-Limit-Up = 10000
sub2 Cleartext-Password := "pw2"
sub3 Cleartext-Password := "pw3"
     Cisco-AVPair = "lcp:interface-config#1=rate-limit output 30000000 3000000 6000000 conform-action transmit exceed-action drop",
     Cisco-AVPair += "ip:sub-qos-policy-in=UP15"
sub4 Cleartext-Password := "pw4"
sub5 Cleartext-Password := "pw5"
sub7 Cleartext-Password := "pw7"
     Prisma-Rate-Limit-Down = 20000,
     Prisma-Rate-Limit-Up = 10000
sub8 Cleartext-Password := "pw8"
     Delegated-IPv6-Prefix = 2001:db8:280:100::/56
U
rm -rf /var/log/freeradius/radacct
# Two servers, one configuration copied twice, each listening on its own
# address: what one of them was asked is what its own log says it received.
# Not `-i <address>`: measured here, a listener made by -i runs no virtual
# server, and every Access-Request was rejected "No Auth-Type found". The
# IPv6 listeners go (both would take [::]:1812), and the second copy runs no
# inner-tunnel (both would take 127.0.0.1:18120).
cp -a $R /etc/freeradius/r2
rm -f /etc/freeradius/r2/sites-enabled/inner-tunnel
for n in 1 2; do
  d=$R; [ "$n" = 2 ] && d=/etc/freeradius/r2
  python3 - "$d/sites-available/default" "127.0.0.$n" <<'PY'
import sys
path, addr = sys.argv[1], sys.argv[2]
text, out, i = open(path).read(), [], 0
while True:
    at = text.find("listen {", i)
    if at < 0:
        out.append(text[i:])
        break
    depth, j = 0, at
    while True:
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    block = text[at:j + 1]
    out.append(text[i:at])
    import re
    if not re.search(r"^\s*ipv6addr\s*=", block, re.M):
        out.append(block.replace("ipaddr = *", f"ipaddr = {addr}"))
    i = j + 1
open(path, "w").write("".join(out))
PY
done
# radius1 / radius2 - start the server on 127.0.0.1 / 127.0.0.2
radius1() { freeradius -X -d $R >> /tmp/radius1.log 2>&1 & R1PID=$!; }
radius2() { freeradius -X -d /etc/freeradius/r2 >> /tmp/radius2.log 2>&1 & R2PID=$!; }
radius1; radius2
sleep 2
# heard <n> <code> [<user>] - how many <code> server n has received [for
# user]: -X prints `(7) Received Access-Request Id ...` and then the packet's
# attributes, one a line, `(7)   User-Name = "sub1"`.
heard() {
  awk -v c="Received $2 " -v u="${3:-}" '
    index($0, c) { if (u == "") n++; else inpkt = 1; next }
    inpkt && /^\([0-9]+\)   [A-Za-z0-9-]+ = / {
      if (index($0, "User-Name = \"" u "\"")) { n++; inpkt = 0 }
      next }
    { inpkt = 0 }
    END { print n + 0 }' "/tmp/radius$1.log"
}
echo "  freeradius on 127.0.0.1 and 127.0.0.2: sub1 carries 100.64.0.77 and"
echo "  Prisma-Rate-Limit-Down 20000, -Up 10000 (kbit/s); sub3 its plan in"
echo "  Cisco-AVPair: a rate-limit output of 30 Mbit/s, a policy UP15 for upload"

echo
echo "=========== 4. THE DATAPLANE AND THE CONFIGURATION, THROUGH THE VTY ==========="
# startup_conf, so LAB_WORKERS=3 runs the dataplane with workers as the
# bench does (0.155.1): a policer is bound to the thread that first uses it
# and the others hand their packets over.
vpp -c "$(startup_conf bng)" > /tmp/vpp.log 2>&1 &
wait_vpp /tmp/vpp.log
echo "  dataplane workers: $(box_workers)"
for i in acc0 wan0 lan0; do
  vppctl create host-interface name "$i" > /dev/null
  vppctl set interface state "host-$i" up
done
vppctl create sub-interfaces host-acc0 100 > /dev/null
POL0=$(vppctl show policer pools 2>/dev/null | tr -d '\r' | sed -n 's/.*policers=\([0-9]*\).*/\1/p')
vppctl set interface state host-acc0.100 up

# The configuration as an operator types it, read by the VTY from its input.
# `rate-limit download policer` (#610): sections 12 and 13 are the policer's
# evidence, each way, as an operator keeps it; the default - the download
# by the dataplane's shaper - is section 24's, on the same block without it.
PPPOE_BLOCK='pppoe
interface host-acc0
interface host-acc0.100
gateway 100.64.0.1
address-pool 100.64.0.10-100.64.0.200
dns 8.8.8.8
dns 1.1.1.1
tcp-mss 1452
rate-limit download policer
ipv6 prefix-pool 2001:db8:100::/48
ipv6 delegated-pool 2001:db8:200::/40 length 56
ipv6 dns 2001:4860:4860::8888
exit'
{
  echo configure
  printf 'nat\ncgnat\npool lab\nprefix-inside 100.64.0.0/22\n'
  printf 'prefix-outside 200.150.0.16/30\nblock-size 512\nexit\nexit\nexit\n'
  printf 'interface host-wan0\nipv4 address 200.150.0.1/24\nipv6 address 2001:db8:ffff::1/64\ncgnat outside\nexit\n'
  printf 'interface host-lan0\nipv4 address 100.64.1.1/24\ncgnat inside\nexit\n'
  printf 'interface host-acc0.100\nvlan 100\nexit\n'
  # RADIUS under `aaa` (#607), in both modes: what `off` leaves out is
  # PPPoE. Failover, the defaults' timers but a dead-time of 10 s so that
  # the lab can wait one out; Disconnect and CoA on loopback, from the one
  # client radclient sends from.
  cat <<'V'
aaa
radius
server 127.0.0.1 secret testing123 priority 1
server 127.0.0.2 secret testing123 priority 2
dead-time 10
interim-interval 60
dynamic-authorization listen 127.0.0.1
dynamic-authorization client 127.0.0.1 secret daesecret
rate-profile UP15 down 0 up 15M
exit
exit
V
  if [ "$MODE" != off ]; then
    echo "$PPPOE_BLOCK"
  fi
  # SNMP (#597), in both modes: the agent's prBng answers a box with no
  # PPPoE too, and `off` reads what it says then.
  printf 'snmp enabled yes\nsnmp listen-address 127.0.0.1\nsnmp community hexro\n'
  echo commit
} > /tmp/config.vty
# THE BOX'S RECORD OF EVENTS (#588), from nothing, and the host's watcher's
# first reading taken before PPPoE is configured: a first reading is a
# baseline and says nothing, so the container's first start is a change the
# next one sees. `tick` is one pass of prisma-routing-events-watch.service's
# loop, as lab/show-log-watch.sh drives it.
rm -f /var/log/prisma-routing/events.log /var/log/prisma-routing/events.log.1 \
  /var/log/prisma-routing/events.state /var/log/prisma-routing/events-watch.state
tick() { $HX --watch-events once 2>&1 | tail -1; }
tick > /dev/null
COMMIT=$($HX < /tmp/config.vty 2>&1)
echo "$COMMIT" | tr '#' '\n' | grep -E "pppoe:|aaa:|commit|error" | sed 's/^/  /'
[ "$MODE" = off ] && echo "  OFF: no 'pppoe' block was committed"

# The subscriber not on PPPoE, before anything else: what section 10 counts
# has to be able to arrive at all.
ip netns exec inet tcpdump -ni wanp -c 2 udp port 5353 > /tmp/plaincap.txt 2>&1 &
TPID=$!
sleep 2
ip netns exec plain python3 -c 'import socket,time
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
for _ in range(5):
    s.sendto(b"p",("200.150.0.2",5353)); time.sleep(0.3)'
sleep 1; kill "$TPID" 2>/dev/null; wait "$TPID" 2>/dev/null
PLAIN=$(grep -c "IP 200.150.0" /tmp/plaincap.txt)
echo "  the plain subscriber reaches the internet translated: $PLAIN of 2 caught"
if [ "$PLAIN" -eq 0 ]; then
  vppctl show errors | tr -d '\r' | sed 's/^/    /'
  vppctl show ip neighbors | tr -d '\r' | sed 's/^/    /'
  vppctl show int addr | tr -d '\r' | sed 's/^/    /'
fi

echo
echo "=========== 5. THE SUBSCRIBERS DIAL ==========="
for i in 1 2 3 4; do
  timeout 3600 ip netns exec "s$i" pppd call "sub$i" > "/tmp/pppd$i.log" 2>&1 &
done
# active - how many sessions the VTY lists as up: an id, and an uptime
active() {
  $HX -c 'show pppoe sessions' 2>/dev/null \
    | awk '/^[0-9a-f]+  / && length($1) == 16 && $NF ~ /:/ {n++} END {print n + 0}'
}
# sessions <n> - wait until the VTY lists n sessions up, at most 60 s
sessions() {
  _n=0
  while [ "$_n" -lt 60 ]; do
    [ "$(active < /dev/null)" -ge "$1" ] && return 0
    sleep 1; _n=$((_n + 1))
  done
  return 1
}
# snmp_bng <tag> - prBng walked through snmpd into /tmp/bng-<tag>.walk, the
# way a box answers it: snmpd as Debian-snmp, its agent asking the host's
# doors (root) for `prisma-bng status` and `summary`. Both are started for
# the read and stopped after it, so each read is a fresh agent with nothing
# cached, and the doors listen only while nothing else here asks the host.
PEN=1.3.6.1.4.1.67033.1.22
snmp_bng() {
  pkill -x snmpd 2>/dev/null
  rm -f /run/prisma-routing/doors.sock
  python3 /src/src/prismash/doors.py --serve > /tmp/doors.log 2>&1 &
  _dp=$!
  for _i in 1 2 3 4 5 6 7 8 9 10; do
    [ -S /run/prisma-routing/doors.sock ] && break; sleep 0.5
  done
  snmpd -Lo -f -C -u Debian-snmp -g Debian-snmp -c /etc/snmp/snmpd.conf \
    > "/tmp/snmpd-$1.log" 2>&1 &
  _sp=$!
  for _i in 1 2 3 4 5 6 7 8 9 10; do
    snmpget -v2c -c hexro -t 1 -r 0 127.0.0.1 1.3.6.1.2.1.1.3.0 > /dev/null 2>&1 && break
    sleep 0.5
  done
  date +%s > "/tmp/bng-$1.at"
  snmpwalk -v2c -c hexro -On -Oq -Ot -Oe -t 15 127.0.0.1 "$PEN" > "/tmp/bng-$1.walk" 2>&1
  snmpget -v2c -c hexro -Ovq -t 15 127.0.0.1 "$PEN.3.0" > "/tmp/bng-$1.get" 2>&1
  kill "$_sp" 2>/dev/null; wait "$_sp" 2>/dev/null
  kill "$_dp" 2>/dev/null; wait "$_dp" 2>/dev/null
  rm -f /run/prisma-routing/doors.sock
  sed 's/^/    /' "/tmp/bng-$1.walk"
}
# bv <tag> <arcs after prBng> - one value of that walk, quotes off
bv() {
  awk -v o=".$PEN.$2" '$1 == o { $1 = ""; sub(/^ /, ""); gsub(/"/, ""); print; exit }' "/tmp/bng-$1.walk"
}
# sent <code> - how many <code>s the first RADIUS server says it sent
sent() { grep -c "Sent $1 " /tmp/radius1.log; }

sessions 3
SHOW=$($HX -c "show pppoe sessions" 2>&1)
echo "$SHOW" | sed 's/^/  /'
SUMMARY=$($HX -c "show pppoe summary" 2>&1)
echo "$SUMMARY" | sed 's/^/  /'
UP_VTY=$(active)
echo "  SNMP, prBng ($PEN), with $UP_VTY session(s) up in the VTY:"
ACC_A0=$(sent Access-Accept); REJ_A0=$(sent Access-Reject)
snmp_bng a
ACC_A1=$(sent Access-Accept); REJ_A1=$(sent Access-Reject)
echo "  server 1's log: $ACC_A0-$ACC_A1 Access-Accept(s), $REJ_A0-$REJ_A1 Access-Reject(s) sent around the read"
GET_UP=$(cat /tmp/bng-a.get)
addr() { ip netns exec "s$1" ip -4 -br addr show ppp0 2>/dev/null | awk '{print $3}'; }
A1=$(addr 1); A2=$(addr 2); A3=$(addr 3); A4=$(addr 4)
echo "  sub1 ${A1:-none}  sub2 ${A2:-none}  sub3 ${A3:-none}  sub4 ${A4:-none}"
A1=${A1:-no-address}

echo
echo "=========== 6. THE SUBSCRIBER, TRANSLATED ==========="
ip netns exec s1 ip route replace default dev ppp0 2>/dev/null
cat > /tmp/send.py <<'S'
import socket, sys, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("", int(sys.argv[1])))
for _ in range(int(sys.argv[2])):
    s.sendto(b"x" * 100, ("200.150.0.2", 53))
    time.sleep(float(sys.argv[3]))
S
timeout 8 ip netns exec inet tcpdump -ni wanp -c 3 udp port 53 > /tmp/cap.txt 2>&1 &
TPID=$!
sleep 2
ip netns exec s1 python3 /tmp/send.py 12345 3 0.3
# The instant, for section 17: this packet's public port, asked at it.
T6=$(date -u +%Y-%m-%dT%H:%M:%SZ)
# tcpdump writes to a file in blocks; what it caught is there once it ends.
wait "$TPID"
CAP=$(cat /tmp/cap.txt)
PREDICTED=$(python3 - "$A1" <<'P'
import sys
sys.path.insert(0, "/src/ref")
from bpa_model import PoolConfig
p = PoolConfig("lab", ["100.64.0.0/22"], ["200.150.0.16/30"], block_size=512)
try:
    m = p.forward(sys.argv[1])
    print(f"{m['ip_pub']}.{m['port_lo']}")
except Exception:
    print("no-subscriber")
P
)
echo "  ref/bpa_model.py: $A1 leaves as $PREDICTED"
echo "$CAP" | grep "IP " | sed 's/^/  on the wire: /'
PING=$(ip netns exec s1 ping -c 3 -i 0.3 -W 2 200.150.0.2 2>&1)
echo "$PING" | tail -2 | sed 's/^/  /'
SIF=$(vppctl show interface | tr -d '\r' | awk '/^pppoe_session/{print $1; exit}')
HOOK=$(vppctl show interface features "${SIF:-none}" 2>/dev/null)

echo
echo "=========== 7. ACCOUNTING ==========="
# The interim is the one that needs the stats patch (build/accel-ppp-ng.patch):
# without it accel-ppp could not read a VPP session's counters and ended the
# session at its first interim. Sixty seconds is the shortest the VTY takes.
# interims - how many Interim-Update records RADIUS has for sub1
interims() {
  cat /var/log/freeradius/radacct/127.0.0.1/detail-* 2>/dev/null \
    | awk 'BEGIN { RS = "" } /Interim-Update/ && /User-Name = "sub1"/ {n++}
           END { print n + 0 }'
}
SEEN=$(interims)
ip netns exec s1 python3 /tmp/send.py 12346 20 0.05
echo "  waiting for sub1's next interim update, after 2 kB it sent"
n=0
until [ "$(interims)" -gt "$SEEN" ] || [ "$n" -ge 75 ]; do
  sleep 1; n=$((n + 1)); done
ACCT=$(cat /var/log/freeradius/radacct/127.0.0.1/detail-* 2>/dev/null)
INTERIM_IN=$(echo "$ACCT" | awk 'BEGIN { RS = "" }
  /Interim-Update/ && /User-Name = "sub1"/ {
    n = split($0, l, "\n")
    for (k = 1; k <= n; k++) if (l[k] ~ /Acct-Input-Octets/) { split(l[k], w, " = "); v = w[2] }
  } END { print v }')
echo "  starts: $(echo "$ACCT" | grep -c 'Acct-Status-Type = Start'), interim: $(echo "$ACCT" | grep -c 'Interim-Update'), sub1's interim Acct-Input-Octets: ${INTERIM_IN:-none}"

echo
echo "=========== 8. THE CONFIGURATION, AS THE BOX SHOWS IT ==========="
RUNNING=$($HX -c "show running-config" 2>&1)
echo "$RUNNING" | sed -n '/^aaa$/,/^ exit$/p' | sed 's/^/  /'
echo "$RUNNING" | sed -n '/^pppoe$/,/^ exit$/p' | sed 's/^/  /'

echo
echo "=========== 9. clear pppoe session ==========="
SID2=$(echo "$SHOW" | awk '$2 == "sub2" {print $1}')
CLEAR=$($HX -c "clear pppoe session ${SID2:-0000000000000000}" 2>&1)
echo "  $CLEAR"
sleep 2
AFTER_CLEAR=$($HX -c "show pppoe sessions" 2>&1)
STOP2=$(cat /var/log/freeradius/radacct/127.0.0.1/detail-* 2>/dev/null \
        | grep -c "Acct-Session-Id = \"${SID2:-none}\"")
sessions 3
REDIAL=$($HX -c "show pppoe sessions" 2>&1)
echo "$REDIAL" | grep sub2 | sed 's/^/  sub2 again: /'
echo "  SNMP after sub2 was cleared and redialled:"
ACC_B0=$(sent Access-Accept); REJ_B0=$(sent Access-Reject)
snmp_bng b
ACC_B1=$(sent Access-Accept); REJ_B1=$(sent Access-Reject)
echo "  server 1's log: $ACC_B0-$ACC_B1 Access-Accept(s), $REJ_B0-$REJ_B1 Access-Reject(s) sent around the read"

echo
echo "=========== 10. THE CONTAINER RESTARTS; EVERYBODY ELSE FORWARDS ==========="
# A subscriber that is not on PPPoE sends 400 datagrams over 20 s while the
# container is restarted twice: once by a commit (a change restarts it), once
# by accel-pppd being killed (the restart policy brings it back).
cat > /tmp/count.py <<'S'
import socket, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("200.150.0.2", 5353))
s.settimeout(1)
n = 0
end = time.time() + 28
while time.time() < end:
    try:
        s.recv(2048)
        n += 1
    except socket.timeout:
        pass
print(n)
S
ip netns exec inet python3 /tmp/count.py > /tmp/count.txt &
CPID=$!
sleep 1
cat > /tmp/plain.py <<'S'
import socket, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
for _ in range(400):
    s.sendto(b"y" * 100, ("200.150.0.2", 5353))
    time.sleep(0.05)
S
ip netns exec plain python3 /tmp/plain.py &
PPID_=$!
BEFORE=$(echo "$REDIAL" | awk '/^[0-9a-f]+  / && length($1) == 16 && $NF ~ /:/ {print $1}' | sort | tr '\n' ' ')
sleep 3
printf 'configure\npppoe\nno dns 1.1.1.1\nexit\ncommit\n' | $HX > /tmp/restart.txt 2>&1
grep -o "pppoe: [a-z, ]*" /tmp/restart.txt | sed 's/^/  commit: /'
sleep 6
APID=$(cat /run/prisma-routing/bng/accel-pppd.pid 2>/dev/null)
[ -n "$APID" ] && kill -9 "$APID" 2>/dev/null
echo "  accel-pppd ${APID:-?} killed"
wait "$PPID_"
wait "$CPID"
GOT=$(cat /tmp/count.txt)
echo "  the plain subscriber: ${GOT:-0} of 400 datagrams arrived"
sessions 3
AFTER=$($HX -c "show pppoe sessions" 2>&1)
NOW=$(echo "$AFTER" | awk '/^[0-9a-f]+  / && length($1) == 16 && $NF ~ /:/ {print $1}' | sort | tr '\n' ' ')
echo "  sessions before: $BEFORE"
echo "  sessions after:  $NOW"
grep -c "== start" /var/log/pppoe-shim.log | sed 's/^/  container starts: /'
# The watcher's reading after both restarts: one pass, as a box's loop would
# take 10 s after them - the container's own record of its runs is what
# carries the two exits a state compared every 10 s would never see.
echo "  the host's watcher: $(tick)"

# THE SESSIONS BY USER (#607): sid_of <user> - the id of its active session
sid_of() {
  $HX -c 'show pppoe sessions' 2>/dev/null \
    | awk -v u="$1" '$2 == u && length($1) == 16 && $NF ~ /:/ {print $1; exit}'
}
# wait_user <user> [<seconds>] - seconds until it has an active session
wait_user() {
  _n=0
  while [ "$_n" -lt "${2:-60}" ]; do
    [ -n "$(sid_of "$1" < /dev/null)" ] && { echo "$_n"; return 0; }
    sleep 1; _n=$((_n + 1))
  done
  echo none; return 1
}
# bw <n> [-R] - Mbit/s through sub<n>'s session, upload, or download with -R
# The CPE's PPP device is the one with an address: a redial can make it ppp1.
bw() {
  _ppp=$(ip netns exec "s$1" ip -o -4 addr show 2>/dev/null | awk '$2 ~ /^ppp/ {print $2; exit}')
  ip netns exec "s$1" ip route replace default dev "${_ppp:-ppp0}" 2>/dev/null
  # A server of its own each time, one test, and a deadline: a test that
  # hangs must not take the next one with it.
  ip netns exec inet iperf3 -s -1 -B 200.150.0.2 > /dev/null 2>&1 &
  _isp=$!
  sleep 1
  # As a customer's speedtest measures: ONE TCP flow, nothing omitted -
  # `-O 1` dropped the first second, the one the bucket empties in, and
  # hid a burst eight times too large (0.155.1). 15 s, so the bucket's
  # half second is at most 3% of what is counted.
  timeout 45 ip netns exec "s$1" iperf3 -c 200.150.0.2 -t 15 -J ${2:-} 2>/dev/null \
    | python3 -c 'import json, sys
try:
    print(round(json.load(sys.stdin)["end"]["sum_received"]["bits_per_second"] / 1e6, 1))
except Exception:
    print(0)'
  kill "$_isp" 2>/dev/null; wait "$_isp" 2>/dev/null
}
# udp_bw <n> <Mbit/s> - what sub<n> RECEIVES of a UDP download sent at
# that rate: the policer's own exactness, with no TCP in it
udp_bw() {
  ip netns exec inet iperf3 -s -1 -B 200.150.0.2 > /dev/null 2>&1 &
  _isp=$!
  sleep 1
  timeout 45 ip netns exec "s$1" iperf3 -c 200.150.0.2 -u -b "${2}M" -l 1400 \
    -t 15 -R -J 2>/dev/null | python3 -c 'import json, sys
try:
    print(round(json.load(sys.stdin)["end"]["sum_received"]["bits_per_second"] / 1e6, 1))
except Exception:
    print(0)'
  kill "$_isp" 2>/dev/null; wait "$_isp" 2>/dev/null
}
# within <measured> <target> - from 10% under the target to 5% over it: TCP
# through a policer settles under the rate, and over it is the bucket (half
# a second is 3.3% of a 15 s test)
within() {
  python3 -c 'import sys; v, t = map(float, sys.argv[1:]); sys.exit(not (0.9 * t <= v <= 1.05 * t))' "$1" "$2"
}
# cb <name-pattern> - the committed burst, bytes, of the policer whose name
# matches: the bucket checked against its rule, not inferred from a rate
cb() {
  vppctl show policer | tr -d '\r' | awk -v p="$1" '/^Name/ && $2 ~ p {for (i = 1; i <= NF; i++) if ($i == "cb") print $(i + 1); exit}'
}
# dae <coa|disconnect> <secret> <attribute lines> - radclient's answer
dae() {
  printf '%b' "$3" | radclient -x -r 1 -t 3 127.0.0.1:3799 "$1" "$2" 2>&1
}
pols() { vppctl show policer pools 2>/dev/null | tr -d '\r' | sed -n 's/.*policers=\([0-9]*\).*/\1/p'; }

echo
echo "=========== 12. RATE LIMITS: A VPP POLICER ON THE SESSION (#607) ==========="

wait_user sub1 > /dev/null; wait_user sub2 > /dev/null
RSHOW=$($HX -c "show pppoe sessions" 2>&1)
echo "$RSHOW" | sed 's/^/  /'
SID1=$(sid_of sub1)
DETAIL1=$($HX -c "show pppoe session ${SID1:-0}" 2>&1)
# Read up to three times: one read in 0.155.1's lab came back empty with the
# session there, and the three checks below are about the dataplane, not
# about one vppctl.
for _try in 1 2 3; do
  SIF1=$(vppctl show pppoe session < /dev/null | tr -d '\r' \
         | awk '/sw-if-index/ {for (i = 1; i <= NF; i++) if ($i == "sw-if-index") idx = $(i + 1)}
                /client-mac 02:aa:00:00:00:01/ {print idx; exit}')
  # A name at the left edge: `$2 == 9` alone matched a counter row
  # ("drops  9") under another interface, measured with workers.
  SNAME1=$(vppctl show interface < /dev/null | tr -d '\r' | awk -v i="${SIF1:-x}" '/^[^ ]/ && $2 == i {print $1; exit}')
  FEAT1=$(vppctl show interface features "${SNAME1:-none}" < /dev/null 2>/dev/null | tr -d '\r')
  case "$FEAT1" in *policer-input*) break ;; esac
  sleep 1
done
echo "  sub1 is sw_if_index ${SIF1:-?}, ${SNAME1:-?}"
# What the dataplane holds for each session, printed: a policer feature
# enabled twice meters every packet twice, and halves the plan.
vppctl show pppoe session | tr -d '\r' | grep -E "sw-if|client-mac" | sed 's/^/    /'
for _i in $(vppctl show interface | tr -d '\r' | awk '/^pppoe_session/ {print $1}'); do
  echo "    $_i: $(vppctl show interface features "$_i" | tr -d '\r' | grep -c policer-input) policer-input, $(vppctl show interface features "$_i" | tr -d '\r' | grep -c policer-output) policer-output"
done
vppctl show policer | tr -d '\r' | grep '^Name' | sed 's/^/    /'
echo "  the session's ip4-unicast arc, in order:"
echo "$FEAT1" | sed -n '/^ip4-unicast:/,/^[a-z0-9-]*:$/p' | sed -n '2,8p' | sed 's/^/    /'
UP1=$(bw 1); DOWN1=$(bw 1 -R)
echo "  sub1, plan 20M down / 10M up: measured ${DOWN1} down, ${UP1} up (Mbit/s)"
DOWN2=$(bw 2 -R)
UDP1=$(udp_bw 1 40)
echo "  sub1, UDP sent at 40 Mbit/s through its 20M plan: ${UDP1} received"
# ONE TCP FLOW OVER A LONG PATH (0.155.2). The bucket a TCP flow needs grows
# with its round trip, and this lab's is a few milliseconds of veth: there a
# tenth of a second of bucket measured 19.1 of 20 Mbit/s down and 9.7 of 10
# up, so 0.155.1's passed here - and gave a single flow HALF its plan on
# prisma-bench (7 workers, a real NIC: 25 Mbit/s through 50). The lab cannot
# make the bench's conditions; it makes the one under which a small bucket
# shows: 200 ms more to the "internet". Measured, 15 s each: half a second
# 18.1 and 18.0 down single-threaded, 17.5 with three workers (87-90%); a
# tenth 14.9 and 14.8 (74%). The upload is printed and not asserted: over
# this path it read 9.2 and 7.9 with half a second and 6.5 and 7.7 with a
# tenth - it does not tell the two apart.
ip netns exec inet tc qdisc replace dev wanp root netem delay 200ms
LONG1D=$(bw 1 -R); LONG1U=$(bw 1)
ip netns exec inet tc qdisc del dev wanp root 2>/dev/null
echo "  sub1 over a 200 ms longer path, one TCP flow: ${LONG1D} down, ${LONG1U} up"
echo "  sub2, no plan: measured ${DOWN2} down"
UP3=$(bw 3); DOWN3=$(bw 3 -R)
echo "  sub3, Cisco-AVPair 30M down (output), profile UP15 up (input): ${DOWN3} down, ${UP3} up"
DETAIL1B=$($HX -c "show pppoe session ${SID1:-0}" 2>&1)
echo "$DETAIL1B" | sed 's/^/  /'
POL_LIVE=$(pols)
# THE BUCKET, read off VPP: half a second of the rate, in bytes (0.155.2),
# whichever attribute gave the rate - not Cisco's own burst numbers, and
# not eight times either (the hook multiplied bytes by 8 until 0.155.1).
CB1D=$(cb "_20000_.*_down"); CB1U=$(cb "_10000_.*_up")
CB3D=$(cb "_30000_.*_down"); CB3U=$(cb "_15000_.*_up")
echo "  buckets: sub1 ${CB1D:-?}/${CB1U:-?} bytes, sub3 (Cisco) ${CB3D:-?}/${CB3U:-?} bytes"
# THE DOWNLOAD GOES TO ITS OWN CPE, after section 10 killed accel-pppd with
# -9: each session's /32 is rewritten with its own client's MAC. Before
# #607 the recycled interfaces kept the dead daemon's adjacencies and sent
# one subscriber's traffic to another (docs/bng.md 0.6).
CROSSED=0
# Only the sessions that are up: one still authenticating (sub4, refused and
# redialling for ever) is listed with '-' for an address, and was read as an
# address with nobody's MAC - the check went red on a session with no route.
for ip in $(echo "$RSHOW" | awk '/^[0-9a-f]+  / && length($1) == 16 && $NF ~ /:/ {print $3}'); do
  mac=unknown
  for n in 1 2 3; do
    a=$(ip netns exec "s$n" ip -o -4 addr show 2>/dev/null | awk '$2 ~ /^ppp/ {split($4, x, "/"); print x[1]; exit}')
    [ "$a" = "$ip" ] && mac="02aa0000000$n"
  done
  rw=$(vppctl show ip fib "$ip/32" | tr -d '\r' | grep -m1 -o '\] [0-9a-f]\{12\}' | awk '{print $2}')
  case "$rw" in "$mac"*) ;; *) CROSSED=$((CROSSED + 1)); echo "  $ip goes to ${rw:-nowhere}, its CPE is $mac" ;; esac
done
echo "  sessions whose download goes to another CPE: $CROSSED"
VPP_SESS=$(vppctl show pppoe session | tr -d '\r' | grep -c sw-if-index)
echo "  sessions VPP holds: $VPP_SESS (the killed daemon's removed by the next one)"
echo "  VPP holds ${POL_LIVE:-?} policer(s) for the one limited session"

echo "  SNMP before any CoA, after the restarts of section 10:"
snmp_bng c

echo
echo "=========== 13. CoA: THE RATE CHANGED ON THE LIVE SESSION ==========="
COA_OK=$(dae coa daesecret 'User-Name = "sub1"\nPrisma-Rate-Limit-Down = 50000\nPrisma-Rate-Limit-Up = 20000\n')
echo "$COA_OK" | grep -E "Received|No reply" | sed 's/^/  /'
sleep 1
SID1_AFTER=$(sid_of sub1)
UP1C=$(bw 1); DOWN1C=$(bw 1 -R)
echo "  sub1 after the CoA: ${DOWN1C} down, ${UP1C} up; session ${SID1_AFTER:-gone} (was ${SID1:-?})"
COA_BAD=$(dae coa daesecret 'User-Name = "sub1"\nPrisma-Rate-Limit-Down = 20000000\n')
COA_UNKNOWN=$(dae coa daesecret 'User-Name = "sub1"\nFilter-Id = "gold"\n')
COA_MKT=$(dae coa daesecret 'User-Name = "sub1"\nMikrotik-Rate-Limit = "1M/1M"\n')
echo "$COA_BAD" | grep -E "Received" | sed 's/^/  20 Gbit\/s, past what a policer takes: /'
echo "$COA_MKT" | grep -E "Received" | sed "s/^/  MikroTik's rate, not read: /"
echo "$COA_UNKNOWN" | grep -E "Received|Error-Cause" | sed 's/^/  an attribute nothing acts on: /'
LIMIT_KEPT=$($HX -c "show pppoe sessions" 2>&1 | awk '$2 == "sub1"')
CB1C=$(cb "_50000_.*_down")
# A gigabit plan, read back from VPP: no unit error at a rate where an int
# of bytes or bits would overflow.
COA_G=$(dae coa daesecret 'User-Name = "sub1"\nPrisma-Rate-Limit-Down = 1000000\nPrisma-Rate-Limit-Up = 1000000\n')
GPOL=$(vppctl show policer | tr -d '\r' | grep '^Name.*_1000000_')
echo "  1 Gbit/s each way: $(echo "$GPOL" | tr '\n' ' ')"
# Cisco's form in a CoA: `input` is the subscriber's upload.
COA_CISCO=$(dae coa daesecret 'User-Name = "sub3"\nCisco-AVPair = "lcp:interface-config=rate-limit input 5000000 500000 1000000 conform-action transmit exceed-action drop"\n')
COA_CISCO_BAD=$(dae coa daesecret 'User-Name = "sub3"\nCisco-AVPair = "lcp:interface-config=rate-limit input 5000000"\n')
COA_CISCO_NAME=$(dae coa daesecret 'User-Name = "sub3"\nCisco-AVPair = "ip:sub-qos-policy-out=NOSUCH"\n')
LIMIT3=$($HX -c "show pppoe sessions" 2>&1 | awk '$2 == "sub3"')
echo "$COA_CISCO" | grep -E "Received" | sed 's/^/  Cisco-AVPair rate-limit input 5 Mbit\/s for sub3: /'
echo "  sub3 now: $LIMIT3"
SID3=$(sid_of sub3)
COA_ST=$(dae coa daesecret 'User-Name = "sub3"\nSession-Timeout = 12\n')
echo "$COA_ST" | grep -E "Received" | sed 's/^/  Session-Timeout 12 for sub3: /'
sleep 6
ALIVE3=$($HX -c "show pppoe sessions" 2>&1 | grep -c "${SID3:-none}")
sleep 10
GONE3=$($HX -c "show pppoe sessions" 2>&1 | grep -c "${SID3:-none}")
echo "  sub3's session ${SID3:-?}: at 6 s ${ALIVE3} listed, at 16 s ${GONE3}"

echo
echo "=========== 14. DISCONNECT ==========="
SID2=$(sid_of sub2)
DM_WRONG=$(dae disconnect wrongsecret 'User-Name = "sub2"\n')
cat > /tmp/dae.py <<'S'
import hashlib, socket, struct, sys
src, secret, user = sys.argv[1], sys.argv[2].encode(), sys.argv[3].encode()
attrs = bytes([1, 2 + len(user)]) + user
hdr = struct.pack("!BBH", 40, 77, 20 + len(attrs))
pkt = hdr + hashlib.md5(hdr + b"\0" * 16 + attrs + secret).digest() + attrs
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind((src, 0))
s.settimeout(3)
s.sendto(pkt, ("127.0.0.1", 3799))
try:
    print("reply code", s.recvfrom(4096)[0][0])
except socket.timeout:
    print("no reply")
S
DM_STRANGER=$(python3 /tmp/dae.py 127.0.0.2 daesecret sub2)
STILL2=$($HX -c "show pppoe sessions" 2>&1 | grep -c "${SID2:-none}")
echo "  wrong secret: $(echo "$DM_WRONG" | grep -cE 'Received') answer(s); from 127.0.0.2, not a client, the right secret: $DM_STRANGER; sub2 still listed: $STILL2"
DM_USER=$(dae disconnect daesecret 'User-Name = "sub2"\n')
echo "$DM_USER" | grep -E "Received" | sed 's/^/  by User-Name sub2: /'
sleep 2
AFTER_DM2=$($HX -c "show pppoe sessions" 2>&1)
SID1=$(sid_of sub1)
DM_SID=$(dae disconnect daesecret "Acct-Session-Id = \"${SID1:-none}\"\n")
echo "$DM_SID" | grep -E "Received" | sed "s/^/  by Acct-Session-Id ${SID1:-?}: /"
sleep 2
AFTER_DM1=$($HX -c "show pppoe sessions" 2>&1)
# stop_cause <sid> - the Acct-Terminate-Cause RADIUS was sent for it
stop_cause() {
  cat /var/log/freeradius/radacct/127.0.0.1/detail-* 2>/dev/null \
    | awk -v s="$1" 'BEGIN { RS = "" } index($0, "Acct-Session-Id = \"" s "\"") && /Stop/ {
        n = split($0, l, "\n")
        for (k = 1; k <= n; k++) if (l[k] ~ /Acct-Terminate-Cause/) { split(l[k], w, " = "); c = w[2] } }
      END { print c }'
}
CAUSE2=$(stop_cause "${SID2:-none}"); CAUSE1=$(stop_cause "${SID1:-none}")
CAUSE3=$(stop_cause "${SID3:-none}")
echo "  Acct-Terminate-Cause: sub2 ${CAUSE2:-none}, sub1 ${CAUSE1:-none}, sub3 ${CAUSE3:-none}"
# They redial (persist), and sub1 comes back with the plan RADIUS gives.
wait_user sub1 > /dev/null
REDIAL1=$($HX -c "show pppoe sessions" 2>&1 | awk '$2 == "sub1"')
echo "  sub1 again: $REDIAL1"
echo "  SNMP after the CoAs and the Disconnects:"
snmp_bng d

echo
echo "=========== 15. FAILOVER: THE FIRST SERVER DIES, AND COMES BACK ==========="
wait_user sub2 > /dev/null; wait_user sub3 > /dev/null
B1=$(heard 2 Access-Request)
echo "  so far, server 2 has been asked $B1 Access-Request(s): failover sends to 1"
kill "$R1PID" 2>/dev/null; wait "$R1PID" 2>/dev/null
echo "  server 1 (127.0.0.1) killed"
A2_0=$(heard 2 Access-Request sub2); S2_0=$(heard 2 Accounting-Request sub2)
T0=$(date +%s)
$HX -c "clear pppoe session user sub2" > /dev/null 2>&1
FO_SECS=$(wait_user sub2 60)
echo "  sub2 cleared, and back in ${FO_SECS} s: authenticated by server 2 ($(heard 2 Access-Request sub2) asks for it there)"
FO_SUMMARY=$($HX -c "show pppoe summary" 2>&1)
echo "$FO_SUMMARY" | grep -E "radius|127\.0\.0" | sed 's/^/  /'
echo "  the host's watcher, server 1 failed: $(tick)"
# The next one goes straight to server 2: server 1 is marked down.
$HX -c "clear pppoe session user sub3" > /dev/null 2>&1
T1=$(date +%s)
NEXT_SECS=$(wait_user sub3 60)
A2_1=$(heard 2 Access-Request sub2); S2_1=$(heard 2 Accounting-Request sub2)
echo "  sub3 cleared and back in ${NEXT_SECS} s, server 1 left alone"
radius1
sleep 12
A1_0=$(heard 1 Access-Request sub2)
$HX -c "clear pppoe session user sub2" > /dev/null 2>&1
BACK_SECS=$(wait_user sub2 60)
A1_1=$(heard 1 Access-Request sub2)
echo "  server 1 back, its dead-time (10 s) over: sub2 again in ${BACK_SECS} s, asked of server 1 ($A1_0 -> $A1_1)"
echo "  the host's watcher, server 1 back: $(tick)"
echo "  SNMP after the failover:"
snmp_bng e

echo
echo "=========== 16. ROUND-ROBIN, BY A RELOAD ==========="
wait_user sub1 > /dev/null; wait_user sub3 > /dev/null
RR_BEFORE=$($HX -c "show pppoe sessions" 2>&1 \
  | awk '/^[0-9a-f]+  / && length($1) == 16 && $NF ~ /:/ {print $1}' | sort | tr '\n' ' ')
printf 'configure\naaa\nradius\nmode round-robin\nexit\nexit\ncommit\n' \
  | $HX > /tmp/rr.txt 2>&1
grep -o "pppoe: [a-z, ]*" /tmp/rr.txt | sed 's/^/  commit: /'
sleep 2
RR_AFTER=$($HX -c "show pppoe sessions" 2>&1 \
  | awk '/^[0-9a-f]+  / && length($1) == 16 && $NF ~ /:/ {print $1}' | sort | tr '\n' ' ')
echo "  sessions before: $RR_BEFORE"
echo "  sessions after:  $RR_AFTER"
Q1=$(heard 1 Access-Request); Q2=$(heard 2 Access-Request)
C1=$(heard 1 Accounting-Request); C2=$(heard 2 Accounting-Request)
$HX -c "clear pppoe session all" > /dev/null 2>&1
sleep 3
sessions 3
sleep 2
RQ1=$(( $(heard 1 Access-Request) - Q1 )); RQ2=$(( $(heard 2 Access-Request) - Q2 ))
RC1=$(( $(heard 1 Accounting-Request) - C1 )); RC2=$(( $(heard 2 Accounting-Request) - C2 ))
echo "  every session cleared and redialled: Access-Requests $RQ1 to server 1, $RQ2 to server 2;"
echo "  Accounting-Requests $RC1 and $RC2"
RR_SUMMARY=$($HX -c "show pppoe summary" 2>&1)
# SEQUENTIAL LOGINS (0.155.1): one subscriber dialling six times, one session
# at a time - prisma-bench measured 7 Access-Requests to one server and 1 to
# the other this way, least-outstanding with nothing outstanding. A rotation
# alternates them.
# sub4 (a wrong password, redialling for ever) is stopped first: its Access-
# Requests take turns in the same rotation, and one subscriber's six can then
# land on one server by parity - the bench test had one subscriber dialling.
for _p in $(pgrep -x pppd); do
  tr '\0' ' ' < "/proc/$_p/cmdline" 2>/dev/null | grep -q 'call sub4' && kill "$_p"
done
sleep 2
S1=$(heard 1 Access-Request); S2=$(heard 2 Access-Request)
for _k in 1 2 3 4 5 6; do
  $HX -c "clear pppoe session user sub2" > /dev/null 2>&1
  sleep 1
  wait_user sub2 30 > /dev/null
done
SQ1=$(( $(heard 1 Access-Request) - S1 )); SQ2=$(( $(heard 2 Access-Request) - S2 ))
echo "  six logins one after the other: $SQ1 Access-Requests to server 1, $SQ2 to server 2"

echo
echo "=========== 17. THE SESSION TRAIL: WHO HELD WHICH ADDRESS, WHEN (docs/bng.md 0.7) ==========="
# The box's own record, written by accel-ppp's prisma_trail module on the log
# volume: a start when the session's address is bound (before RADIUS is sent
# its Accounting-Start), a stop before the address goes back to the pool,
# appended, fsynced, hash-chained.
TR=/var/log/prisma-routing/pppoe-sessions.log
# tline <session id> <kind> - the trail's <kind> line(s) for that session
tline() { cat "$TR".* "$TR" 2>/dev/null | grep " $2 session $1 "; }
# fld <line> <key> - the value after <key> in a trail line
fld() { echo "$1" | awk -v k="$2" '{for (i = 2; i < NF; i++) if ($i == k) {print $(i + 1); exit}}'; }
# epoch <trail instant> - as seconds, through the reader the VTY uses
epoch() { python3 -c 'import sys; sys.path.insert(0, "/src/src/prismash")
import pppoetrail as T
v = T.parse_stamp(sys.argv[1]); print(v if v is not None else 0)' "$1"; }
# between <a> <x> <b> - a <= x <= b, as numbers
between() { python3 -c 'import sys; a, x, b = map(float, sys.argv[1:]); sys.exit(not a <= x <= b)' "$1" "$2" "$3"; }
# since_t <epoch> <user> <kind> - that user's <kind> lines written after then
since_t() {
  cat "$TR".* "$TR" 2>/dev/null | python3 -c 'import sys
sys.path.insert(0, "/src/src/prismash")
import pppoetrail as T
t, user, kind = float(sys.argv[1]), sys.argv[2], sys.argv[3]
for line in sys.stdin:
    r = T.parse_line(line)
    if r and r["kind"] == kind and r["t"] > t and r["fields"].get("user") == user:
        print(line.rstrip())' "$1" "$2" "$3"
}
echo "  $TR: $(cat "$TR" 2>/dev/null | wc -l) entries so far, $(grep -c ' restart closed ' "$TR" 2>/dev/null) control-plane start(s)"

# A. One subscriber cleared and back: the stop and the start, with the
# user, the address, the MAC, the interface and the times.
wait_user sub1 > /dev/null
OLD1=$(sid_of sub1)
C0=$(date +%s.%N)
$HX -c "clear pppoe session user sub1" > /dev/null 2>&1
C1=$(date +%s.%N)
wait_user sub1 > /dev/null
NEW1=$(sid_of sub1)
STOP_OLD=$(tline "${OLD1:-none}" stop)
START_NEW=$(tline "${NEW1:-none}" start)
echo "  sub1's session ${OLD1:-?} cleared, ${NEW1:-?} its next:"
echo "$STOP_OLD" | sed 's/ seq .*//; s/^/    /'
echo "$START_NEW" | sed 's/ seq .*//; s/^/    /'
# What RADIUS was told for the same stop: `clear pppoe session` is accel-ppp's
# `terminate ... hard`, which it calls NAS-Request.
RAD_CAUSE1=$(stop_cause "${OLD1:-none}" | tr -d '" ')
T_STOP=$(epoch "$(echo "$STOP_OLD" | awk '{print $1}')")
T_START=$(epoch "$(echo "$START_NEW" | awk '{print $1}')")
HIST1=$($HX -c "show pppoe history user sub1 last 2" 2>&1)
echo "$HIST1" | sed 's/^/  /'

# B. The control plane killed with -9 (the shim restarts it, as the
# container's restart policy does): nothing was written for the sessions it
# held, so the next one closes them before its first session, and every
# entry from before is still there and still chained.
sessions 3
UP_SIDS=$($HX -c 'show pppoe sessions' 2>/dev/null \
  | awk '/^[0-9a-f]+  / && length($1) == 16 && $NF ~ /:/ {print $1}')
LINES_B0=$(cat "$TR".* "$TR" 2>/dev/null | wc -l)
FIRST_B0=$(head -1 "$TR")
APID=$(cat /run/prisma-routing/bng/accel-pppd.pid 2>/dev/null)
[ -n "$APID" ] && kill -9 "$APID" 2>/dev/null
echo "  accel-pppd ${APID:-?} killed with -9, holding: $(echo $UP_SIDS)"
sleep 3
sessions 3
LOST=0
for s in $UP_SIDS; do
  tline "$s" stop | grep -q " cause Control-Plane-Lost " && LOST=$((LOST + 1))
done
RESTART_B=$(grep ' restart closed ' "$TR" | tail -1 | sed 's/ seq .*//')
echo "  back: ${LOST} of $(echo $UP_SIDS | wc -w) closed as Control-Plane-Lost; ${RESTART_B}"
LINES_B1=$(cat "$TR".* "$TR" 2>/dev/null | wc -l)

# C1. The formula: sub1's packet of section 6, by its public address and
# port and the instant it was sent, to the PPPoE user.
PUB6=${PREDICTED%.*}; PORT6=${PREDICTED##*.}
XLT6=$($HX -c "show cgnat translation $PUB6 $PORT6 at $T6" 2>&1)
echo "  show cgnat translation $PUB6 $PORT6 at $T6"
echo "$XLT6" | sed 's/^/    /'

# C2. The block log: the pool made dynamic and its log sent to a syslog
# collector. sub2's packet is caught on the wire with its public address and
# port; the collector's BLOCK-ALLOC whose range holds that port gives the
# inside address and the instant - the court order's first step - and the
# trail the session that held that inside address then.
TCOLR=/tmp/trail-syslog-report.txt; TCOLL=/tmp/trail-syslog-lines.txt
rm -f "$TCOLR" "$TCOLL"
python3 /src/tools/syslog_collector.py 601 30 "$TCOLR" "$TCOLL" > /tmp/trail-collector.out 2>&1 &
TCOLP=$!
sleep 1
printf 'configure\nnat\ncgnat\npool lab\nmode bpa\nexit\nlogging syslog 127.0.0.1 enterprise 44444 port 601\nexit\nexit\ncommit\n' \
  | $HX > /tmp/trail-dyn.txt 2>&1
tr '#' '\n' < /tmp/trail-dyn.txt | grep -iE "error|commit" | sed 's/^ */  pool made dynamic (mode bpa): /'
wait_user sub2 > /dev/null
A2D=$($HX -c 'show pppoe sessions' 2>/dev/null | awk '$2 == "sub2" {print $3; exit}')
_ppp2=$(ip netns exec s2 ip -o -4 addr show 2>/dev/null | awk '$2 ~ /^ppp/ {print $2; exit}')
ip netns exec s2 ip route replace default dev "${_ppp2:-ppp0}" 2>/dev/null
timeout 10 ip netns exec inet tcpdump -ni wanp -c 1 'udp and dst port 53 and src net 200.150.0.16/30' > /tmp/trail-cap.txt 2>&1 &
TPID=$!
sleep 2
ip netns exec s2 python3 /tmp/send.py 23456 3 0.3
wait "$TPID"
WIRE=$(grep -o 'IP 200\.150\.0\.[0-9]*\.[0-9]*' /tmp/trail-cap.txt | head -1 | awk '{print $2}')
PUBD=${WIRE%.*}; PORTD=${WIRE##*.}
echo "  sub2 (${A2D:-?}) on the wire as ${PUBD:-?}:${PORTD:-?}"
wait "$TCOLP"
FOUND=$(python3 - "${PUBD:-x}" "${PORTD:-0}" "$TCOLL" <<'P'
import re, sys
pub, port, path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
try:
    lines = open(path, errors="replace").read().splitlines()
except OSError:
    lines = []
for line in lines:
    if " BLOCK-ALLOC " not in line:
        continue
    f = dict(re.findall(r'(\w+)="([^"]*)"', line))
    if f.get("public") == pub and int(f.get("portStart", -1)) <= port <= int(f.get("portEnd", -1)):
        print(line.split()[1], f.get("subscriber", ""))
        break
P
)
TS_LOG=${FOUND% *}; SUB_LOG=${FOUND#* }
echo "  the block log: ${FOUND:-no BLOCK-ALLOC holds that port} ($(grep -c BLOCK-ALLOC "$TCOLL" 2>/dev/null || echo 0) allocation(s) received)"
TRACE=$($HX -c "show pppoe history address ${SUB_LOG:-0.0.0.0} at ${TS_LOG:-x}" 2>&1)
echo "  show pppoe history address ${SUB_LOG:-?} at ${TS_LOG:-?}"
echo "$TRACE" | sed 's/^/    /'
XLTD=$($HX -c "show cgnat translation ${PUBD:-0.0.0.0} ${PORTD:-0}" 2>&1)
echo "  show cgnat translation ${PUBD:-?} ${PORTD:-?}"
echo "$XLTD" | sed 's/^/    /'
XLTD_AT=$($HX -c "show cgnat translation ${PUBD:-0.0.0.0} ${PORTD:-0} at ${TS_LOG:-x}" 2>&1)
# Before sub2's session began, the trail names nobody - not the holder now.
S2START=$(tline "$(sid_of sub2)" start | awk '{print $1}')
BEFORE_S2=$(python3 -c 'import sys,time; sys.path.insert(0, "/src/src/prismash")
import pppoetrail as T; print(T.stamp(T.parse_stamp(sys.argv[1]) - 0.5))' "${S2START:-2026-01-01T00:00:00.000Z}")
TRACE_BEFORE=$($HX -c "show pppoe history address ${SUB_LOG:-0.0.0.0} at $BEFORE_S2" 2>&1)
echo "  half a second before that session began: $(echo "$TRACE_BEFORE" | head -1)"

# D. The chain: intact, and red when an entry is edited.
VERIFY=$($HX -c "show pppoe history verify" 2>&1)
echo "$VERIFY" | sed 's/^/  /'
cp -p "$TR" /tmp/trail-backup.log
EDIT_LINE=$(grep -n ' start session ' "$TR" | sed -n 2p | cut -d: -f1)
sed -i "${EDIT_LINE:-2}s/ address 100\.64\.0\./ address 100.64.9./" "$TR"
TAMPERED=$($HX -c "show pppoe history verify" 2>&1)
cp -p /tmp/trail-backup.log "$TR"
echo "  line ${EDIT_LINE:-?} given another address:"
echo "$TAMPERED" | sed 's/^/    /'
VERIFY_BACK=$($HX -c "show pppoe history verify" 2>&1)

# E1. No accounting server answering, authentication answering: the session
# is bound, accel-ppp sends its Accounting-Start to nobody, and ends it
# (NAS-Error) - the trail has both, written by the box, while no RADIUS
# server received a Start.
printf 'configure\naaa\nradius\nserver 127.0.0.1 secret testing123 priority 1 role auth\nno server 127.0.0.2\nserver 127.0.0.3 secret testing123 priority 2 role acct\nserver 127.0.0.4 secret testing123 priority 3 role acct\nexit\nexit\ncommit\n' \
  | $HX > /tmp/trail-acct.txt 2>&1
grep -o "pppoe: [a-z, ]*" /tmp/trail-acct.txt | sed 's/^/  accounting to two servers that are not there: /'
wait_user sub3 > /dev/null
AD0=$(date +%s.%N)
ACCT1_0=$(heard 1 Accounting-Request sub3); ACCT2_0=$(heard 2 Accounting-Request sub3)
$HX -c "clear pppoe session user sub3" > /dev/null 2>&1
n=0
until [ -n "$(since_t "$AD0" sub3 stop | grep ' cause NAS-Error ')" ] || [ "$n" -ge 60 ]; do
  sleep 1; n=$((n + 1)); done
AD_START=$(since_t "$AD0" sub3 start | head -1)
AD_STOP=$(since_t "$AD0" sub3 stop | grep ' cause NAS-Error ' | head -1)
ACCT1_1=$(heard 1 Accounting-Request sub3); ACCT2_1=$(heard 2 Accounting-Request sub3)
echo "  sub3 redialled with no accounting server, ${n} s:"
echo "$AD_START" | sed 's/ seq .*//; s/^/    /'
echo "$AD_STOP" | sed 's/ seq .*//; s/^/    /'
echo "  Accounting-Requests for sub3 at the two real servers meanwhile: $((ACCT1_1 - ACCT1_0)) and $((ACCT2_1 - ACCT2_0))"
printf 'configure\naaa\nradius\nno server 127.0.0.3\nno server 127.0.0.4\nserver 127.0.0.1 secret testing123 priority 1\nserver 127.0.0.2 secret testing123 priority 2\nexit\nexit\ncommit\n' \
  | $HX > /tmp/trail-acct-back.txt 2>&1

# E2. Both servers down: a session that ends is recorded - RADIUS cannot be
# sent its Stop - and none can start, since nobody can authenticate it, so
# the trail has no start for it: nothing was held.
wait_user sub2 > /dev/null
kill "$R1PID" "$R2PID" 2>/dev/null; wait "$R1PID" "$R2PID" 2>/dev/null
DN0=$(date +%s.%N)
SID2D=$(sid_of sub2)
$HX -c "clear pppoe session user sub2" > /dev/null 2>&1
sleep 15
DN_STOP=$(tline "${SID2D:-none}" stop)
DN_STARTS=$(since_t "$DN0" sub2 start | wc -l)
DN_LISTED=$($HX -c 'show pppoe sessions' 2>/dev/null | awk '$2 == "sub2"' | wc -l)
echo "  both RADIUS servers killed, sub2's ${SID2D:-?} cleared:"
echo "$DN_STOP" | sed 's/ seq .*//; s/^/    /'
echo "  15 s later: $DN_STARTS start(s) for sub2 recorded, $DN_LISTED session(s) listed"
radius1; radius2
sleep 2
DN_BACK=$(wait_user sub2 60)
echo "  RADIUS back: sub2 again in ${DN_BACK} s"
VERIFY_END=$($HX -c "show pppoe history verify" 2>&1)
SUMMARY_T=$($HX -c "show pppoe summary" 2>&1)
echo "$SUMMARY_T" | grep "session trail" | sed 's/^/  /'

# F. The head, sent off the box (docs/bng.md 0.7): the host's watcher writes
# it as a `trail` event, into the record the remote syslog and WatchEvents
# carry. Each `tick` is a new run of the watcher, which says a head that
# moved since the last one said at once (eventwatch.trail_changes); the
# second, nothing written between, says none. The heads are not passed
# through the flapping rule, so the ticks before this one cannot hold it.
trail_events() {
  cat /var/log/prisma-routing/events.log 2>/dev/null | python3 -c 'import json, sys
for line in sys.stdin:
    try:
        e = json.loads(line)
    except ValueError:
        continue
    if e.get("kind") == "trail":
        print(e["severity"], e.get("component", "-"), e["text"])'
}
# A CPE redialling between the two ticks moves the head, and the second
# tick then says it - rightly: in a run of this lab on 2026-10-14 a redial
# wrote seq 65 a second after the first tick said 64. So the pair is
# taken while the trail stands still - sub3 back, no line for 5 s - and taken
# again, up to three times, when a line was written during it anyway.
trail_quiet() {
  _n=0; _a=$(wc -l < "$TR")
  while [ "$_n" -lt 30 ]; do
    sleep 5; _b=$(wc -l < "$TR")
    [ "$_a" = "$_b" ] && return 0
    _a=$_b; _n=$((_n + 1))
  done
  return 1
}
wait_user sub3 60 > /dev/null
TRAIL_N0=$(trail_events | wc -l)
TRAIL_TRY=0
while [ "$TRAIL_TRY" -lt 3 ]; do
  TRAIL_TRY=$((TRAIL_TRY + 1))
  trail_quiet
  TR_L0=$(wc -l < "$TR")
  tick > /dev/null
  VERIFY_H=$($HX -c "show pppoe history verify" 2>&1)
  TRAIL_NB=$(trail_events | wc -l)
  # The newest head said, after this tick: the one the first tick wrote, or
  # on a retry the one the last tick wrote - the head either way.
  TRAIL_LAST=$(trail_events | grep "trail's head: " | tail -1)
  [ "$TRAIL_TRY" = 1 ] && TRAIL_EV1=$(trail_events | tail -n +$((TRAIL_N0 + 1)))
  tick > /dev/null
  TRAIL_NC=$(trail_events | wc -l)
  TR_L1=$(wc -l < "$TR")
  [ "$TR_L0" = "$TR_L1" ] && break
  echo "  the trail moved during the two ticks ($TR_L0 -> $TR_L1 lines): again"
done
TRAIL_FIRST=$(echo "$TRAIL_EV1" | grep -c .)
H_SEQ=$(echo "$VERIFY_H" | sed -n 's/^  head  seq \([0-9]*\)$/\1/p')
H_SHA=$(echo "$VERIFY_H" | sed -n 's/^  sha256 \([0-9a-f]*\)$/\1/p')
EV_SEQ=$(echo "$TRAIL_LAST" | sed -n "s/.*trail's head: seq \([0-9]*\), sha256 .*/\1/p")
EV_SHA=$(echo "$TRAIL_LAST" | sed -n "s/.*trail's head: seq [0-9]*, sha256 \([0-9a-f]*\), .*/\1/p")
echo "  the host's watcher, sessions made since its last head: $TRAIL_FIRST trail event(s)"
echo "$TRAIL_EV1" | sed 's/^/    /'
echo "  show pppoe history verify's head (pass $TRAIL_TRY): seq ${H_SEQ:-?} sha256 ${H_SHA:-?}"
echo "  and a second tick, the trail still ($TR_L0 -> $TR_L1 lines): $((TRAIL_NC - TRAIL_NB)) trail event(s)"
$HX -c "show log kind trail last 2" 2>&1 | sed 's/^/  /'
echo "=========== 18. PPPoE ONLY ON THE ACCESS INTERFACES ==========="
# padi.py <iface> <count> [<src mac>] - PADIs sent from <iface>, one a
# second; prints how many came back with a PADO for them (Host-Uniq)
cat > /tmp/padi.py <<'S'
import os, socket, struct, sys, time
ifc, count = sys.argv[1], int(sys.argv[2])
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x8863))
s.bind((ifc, 0))
mac = bytes.fromhex(sys.argv[3].replace(":", "")) if len(sys.argv) > 3 \
    else s.getsockname()[4][:6]
got = 0
for _ in range(count):
    uniq = os.urandom(4)
    tags = struct.pack("!HH", 0x0101, 0) + struct.pack("!HH", 0x0103, 4) + uniq
    s.send(b"\xff" * 6 + mac + struct.pack("!HBBHH", 0x8863, 0x11, 0x09, 0,
                                           len(tags)) + tags)
    end = time.time() + 1
    s.settimeout(0.2)
    while time.time() < end:
        try:
            f = s.recv(2048)
        except socket.timeout:
            continue
        if len(f) > 20 and f[15] == 0x07 and uniq in f[20:]:
            got += 1
            break
print(got)
S
# lcp.py <iface> <src mac> <session id> <count> - LCP Echo-Requests in a
# session frame, as a CPE sends them, from somebody else's MAC
cat > /tmp/lcp.py <<'S'
import socket, struct, sys, time
ifc, mac, sid, count = sys.argv[1], sys.argv[2], int(sys.argv[3], 16), int(sys.argv[4])
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
s.bind((ifc, 0))
src = bytes.fromhex(mac.replace(":", ""))
lcp = struct.pack("!BBHI", 9, 1, 8, 0)
for _ in range(count):
    s.send(b"\xff" * 6 + src + struct.pack("!HBBHHH", 0x8864, 0x11, 0, sid,
                                           len(lcp) + 2, 0xc021) + lcp)
    time.sleep(0.2)
S
# guard <field> - one number of `show bpa pppoe-access`
guard() {
  vppctl show bpa pppoe-access 2>/dev/null | tr -d '\r' | awk -v f="$1" '
    { for (i = 1; i < NF; i++) if ($1 " " $i == f) { print $(i + 1); exit } }'
}
vppctl show bpa pppoe-access 2>&1 | tr -d '\r' | sed 's/^/  /'
NA0=$(guard "discovery not-access"); SNA0=$(guard "session not-access")
# From the CGNAT inside port (host-lan0), a routed interface that is not
# access: what a CPE on the wrong side of the box sends.
WRONG=$(ip netns exec plain python3 /tmp/padi.py lanp 5)
# The same from a CPE on the access bridge: the control is that a PADI is
# answered at all.
RIGHT=$(ip netns exec s6 python3 /tmp/padi.py cli6 5)
# And sub1's MAC in a session frame from the upstream side, with its session
# id: without the guard this frame moves sub1's control plane to host-lan0.
SESS1=$(vppctl show pppoe session | tr -d '\r' | awk '
  /session-id/ { for (i = 1; i <= NF; i++) if ($i == "session-id") s = $(i + 1) }
  /client-mac 02:aa:00:00:00:01/ { print s; exit }')
ip netns exec plain python3 /tmp/lcp.py lanp 02:aa:00:00:00:01 "$(printf '%x' "${SESS1:-1}")" 5
sleep 1
NA1=$(guard "discovery not-access"); SNA1=$(guard "session not-access")
echo "  5 PADIs from host-lan0 (not access): ${WRONG:-?} PADO(s) back"
echo "  5 PADIs from a CPE on host-acc0 (access): ${RIGHT:-?} PADO(s) back"
echo "  refused, not an access interface: discovery ${NA0:-?} -> ${NA1:-?}, session ${SNA0:-?} -> ${SNA1:-?}"
SUM17=$($HX -c "show pppoe summary" 2>&1)
echo "$SUM17" | grep -A1 "^refused" | sed 's/^/  /'
vppctl show runtime | tr -d '\r' | grep -E "bpa-pppoe-(discovery|session)-guard|pppoe-input |pppoe-cp-dispatch" | sed 's/^/  runtime: /'

echo
echo "=========== 19. ONE MAC ON TWO ACCESS PORTS ==========="
# s5 is sub1's MAC on VLAN 100 - host-acc0.100, another access interface -
# dialling as sub5, as a cloned CPE on another OLT port would. sub1 keeps
# its session on host-acc0 while s5 dials for 40 s; then sub1 hangs up and
# s5 may have the MAC.
# sniff.py <iface> <seconds> <ethertype> <in|out> [<session id>|-] [<code>]
# - how many PPPoE frames of that ethertype (hex), session id and code (hex)
# cross <iface> in that direction
cat > /tmp/sniff.py <<'S'
import socket, struct, sys, time
ifc, secs, et, d = sys.argv[1], float(sys.argv[2]), int(sys.argv[3], 16), sys.argv[4]
sid = int(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] != "-" else None
code = int(sys.argv[6], 16) if len(sys.argv) > 6 else None
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(et))
s.bind((ifc, 0))
s.settimeout(0.3)
n, end = 0, time.time() + secs
while time.time() < end:
    try:
        f, a = s.recvfrom(2048)
    except socket.timeout:
        continue
    if (a[2] == socket.PACKET_OUTGOING) != (d == "out") or len(f) < 20:
        continue
    if sid is not None and struct.unpack("!H", f[16:18])[0] != sid:
        continue
    if code is not None and f[15] != code:
        continue
    n += 1
print(n)
S
# padiflood.py <iface> <seconds> - a PADI every 20 ms, answered or not
cat > /tmp/padiflood.py <<'S'
import socket, struct, sys, time
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
s.bind((sys.argv[1], 0))
mac = s.getsockname()[4][:6]
tags = struct.pack("!HH", 0x0101, 0)
end = time.time() + float(sys.argv[2])
while time.time() < end:
    s.send(b"\xff" * 6 + mac + struct.pack("!HBBHH", 0x8863, 0x11, 0x09, 0,
                                           len(tags)) + tags)
    time.sleep(0.02)
S
# psid <mac> - the PPPoE session id the dataplane holds for a client MAC
psid() {
  vppctl show pppoe session | tr -d '\r' | awk -v m="$1" '
    /session-id/ { for (i = 1; i <= NF; i++) if ($i == "session-id") s = $(i + 1) }
    $0 ~ "client-mac " m { print s; exit }'
}
# padt.py <iface> <dst mac> <session id> - the PADT a CPE sends when it
# hangs up
cat > /tmp/padt.py <<'S'
import socket, struct, sys
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
s.bind((sys.argv[1], 0))
mac = s.getsockname()[4][:6]
dst = bytes.fromhex(sys.argv[2].replace(":", ""))
s.send(dst + mac + struct.pack("!HBBHH", 0x8863, 0x11, 0xa7,
                               int(sys.argv[3]), 0))
S
ACC_MAC=$(vppctl show hardware-interfaces host-acc0 | tr -d '\r' \
  | sed -n 's/.*Ethernet address \([0-9a-f:]*\).*/\1/p' | head -1)
H0=$(guard "discovery held")
# sub1 DIALS WHILE ITS MAC IS HEARD ON THE OTHER PORT. The plugin takes a
# session's way down - its interface, VLAN tag and all - from where its MAC
# was heard last, at the moment the session is added (pppoe.c, the link
# table lookup in vnet_pppoe_add_del_session). s5 sends PADIs with sub1's
# MAC on VLAN 100 every 20 ms while sub1's session is cleared and sub1
# redials; then where do sub1's downloads go?
wait_user sub1 > /dev/null
SID1_R=$(sid_of sub1)
ip netns exec s5 python3 /tmp/padiflood.py cli5.100 25 &
PF=$!
sleep 1
$HX -c "clear pppoe session ${SID1_R:-0000000000000000}" > /dev/null 2>&1
sleep 2
REDIAL_R=$(wait_user sub1 20)
PSID1_R=$(psid 02:aa:00:00:00:01)
ENCAP=$(vppctl show pppoe session | tr -d '\r' | awk '
  /encap-if-index/ { for (i = 1; i <= NF; i++) if ($i == "encap-if-index") e = $(i + 1) }
  /client-mac 02:aa:00:00:00:01/ { print e; exit }')
ENCAP_NAME=$(vppctl show interface | tr -d '\r' | awk -v i="${ENCAP:-x}" '/^[^ ]/ && $2 == i {print $1; exit}')
_ppp=$(ip netns exec s1 ip -o -4 addr show 2>/dev/null | awk '$2 ~ /^ppp/ {print $2; exit}')
ip netns exec s1 ip route replace default dev "${_ppp:-ppp0}" 2>/dev/null
ip netns exec s5 python3 /tmp/sniff.py cli5.100 5 8864 in "${PSID1_R:-0}" > /tmp/stolen5.txt &
SN=$!
PING_R=$(ip netns exec s1 ping -c 5 -i 0.3 -W 1 200.150.0.2 2>&1 | grep -o '[0-9]*% packet loss')
wait "$SN"
STOLEN=$(cat /tmp/stolen5.txt)
kill "$PF" 2>/dev/null; wait "$PF" 2>/dev/null
H1=$(guard "discovery held")
echo "  sub1 redialled in ${REDIAL_R} s while its MAC was heard on VLAN 100 every 20 ms:"
echo "  the dataplane sends its session's frames out of ${ENCAP_NAME:-?} (encap-if-index ${ENCAP:-?})"
echo "  sub1 pinging the internet: ${PING_R:-no answer}; its session's frames reaching the CPE on VLAN 100: ${STOLEN:-?}"
echo "  refused, MAC held by another access interface: discovery ${H0:-?} -> ${H1:-?}"

# A CPE WITH sub1's MAC DIALLING ON THE OTHER PORT, as sub5, for 40 s.
wait_user sub1 > /dev/null
SID1_18=$(sid_of sub1)
H2=$(guard "discovery held"); HS2=$(guard "session held")
timeout 3600 ip netns exec s5 pppd call sub5 > /tmp/pppd5.log 2>&1 &
CHANGES=0; LAST=$SID1_18; SEEN5=""
for _k in $(seq 1 20); do
  sleep 2
  _cur=$(sid_of sub1)
  [ "$_cur" != "$LAST" ] && CHANGES=$((CHANGES + 1))
  LAST=$_cur
  [ -n "$(sid_of sub5)" ] && SEEN5=yes
done
H3=$(guard "discovery held"); HS3=$(guard "session held")
echo "  sub1's session ${SID1_18:-?}: changed $CHANGES time(s) in 40 s while sub5 dialled, now ${LAST:-none}"
echo "  sub5 (sub1's MAC on VLAN 100) had a session at some point: ${SEEN5:-no}"
echo "  refused, MAC held by another access interface: discovery ${H2:-?} -> ${H3:-?}, session ${HS2:-?} -> ${HS3:-?}"
# sub1 hangs up where its session is, and the MAC is the other port's at
# once. pppd killed sends LCP's Terminate-Request and NO PADT (measured
# here: none left the CPE's interface), so the PADT a CPE's hang-up sends
# is sent for it; then the other port is asked with PADIs of the lab's own,
# sub5's pppd stopped first - after 40 s of refusals it is in its discovery
# back-off, and what it took to dial in the first cut of this section was
# that back-off, not the box.
for _p in $(pgrep -x pppd); do
  tr '\0' ' ' < "/proc/$_p/cmdline" 2>/dev/null | grep -q 'call sub5' && kill "$_p"
done
PSID1_H=$(psid 02:aa:00:00:00:01)
ip netns exec s1 python3 /tmp/sniff.py cli1 4 8863 out - a7 > /tmp/padtout.txt &
PO=$!
for _p in $(pgrep -x pppd); do
  tr '\0' ' ' < "/proc/$_p/cmdline" 2>/dev/null | grep -q 'call sub1' && kill "$_p"
done
wait "$PO"
PADTOUT=$(cat /tmp/padtout.txt)
NOTFREE=$(ip netns exec s5 python3 /tmp/padi.py cli5.100 2)
ip netns exec s1 python3 /tmp/padt.py cli1 "${ACC_MAC:-ff:ff:ff:ff:ff:ff}" "${PSID1_H:-0}"
FREED=$(ip netns exec s5 python3 /tmp/padi.py cli5.100 3)
timeout 3600 ip netns exec s5 pppd call sub5 >> /tmp/pppd5.log 2>&1 &
MOVED=$(wait_user sub5 60)
echo "  sub1's pppd killed: ${PADTOUT:-?} PADT(s) left its CPE; the other port's PADIs answered: ${NOTFREE:-?} of 2"
echo "  a PADT from sub1's port for its session, then: ${FREED:-?} of 3 answered; sub5 has a session ${MOVED} s later"

echo
echo "=========== 20. A FORMAT UPGRADE RESTARTS THE CONTAINER, AND SAYS SO ==========="
# What a box upgraded from 0.155.3 finds at its next load: the container's
# files in an older format. The load writes them again and restarts it.
# The load replays the SAVED file, which only `save` writes.
$HX -c save > /dev/null 2>&1
sed -i 's/^# prisma-bng format .*/# prisma-bng format 3/' /etc/prisma-routing/bng/accel-ppp.conf
FMT_LOAD=$($HX --load 2>&1)
echo "$FMT_LOAD" | grep -iE "pppoe|older|error" | sed 's/^/  load: /'
FMT_NOW=$(sed -n 's/^# prisma-bng format //p' /etc/prisma-routing/bng/accel-ppp.conf)
sleep 4
echo "  the files are format ${FMT_NOW:-?} now; the host's watcher: $(tick)"

echo
echo "=========== 23. IPv6 ON THE SESSIONS (#609, docs/bng.md 0.8) ==========="
# Two CPEs dial with IPv6CP: sub7, its WAN /64 and its delegated /56 from the
# pools, with a 20/10 Mbit/s plan; sub8, its delegated prefix from RADIUS's
# Delegated-IPv6-Prefix. The WAN prefix by RA - the CPE's kernel does SLAAC -
# and the delegated one by DHCPv6 - dhcpcd, as a CPE router asks for it.
# Both reach the control plane only because the dataplane's PPPoE guard
# sends a session's RS and DHCPv6 up the TAP and takes the answers off it.
P6=/var/log/prisma-routing/pppoe-sessions.log
TRAIL6_L0=$(wc -l < "$P6" 2>/dev/null || echo 0)
for i in 7 8; do
  timeout 3600 ip netns exec "s$i" pppd call "sub$i" > "/tmp/pppd$i.log" 2>&1 &
done
W7=$(wait_user sub7 90); W8=$(wait_user sub8 90)
echo "  sub7 up after $W7 s, sub8 after $W8 s"
# slaac <n> - the global address sub<n>'s kernel made from the RA, or ""
slaac() {
  _a=""
  for _k in $(seq 1 20); do
    _a=$(ip netns exec "s$1" ip -6 -o addr show scope global 2>/dev/null \
         | awk '$2 ~ /^ppp/ {sub(/\/.*/, "", $4); print $4; exit}')
    [ -n "$_a" ] && break
    sleep 1
  done
  echo "$_a"
}
# pd <n> - dhcpcd on sub<n>'s PPP device: the delegated prefix's first /64
# numbered on lo, as a CPE numbers its LAN; prints the lo address, or ""
pd() {
  _ppp=$(ip netns exec "s$1" ip -o link show 2>/dev/null | awk -F': ' '$2 ~ /^ppp/ {print $2; exit}')
  printf 'ipv6only\nnoipv6rs\nnohook resolv.conf\nia_pd 1 lo/0/64/1\n' > /tmp/dhcpcd$1.conf
  ip netns exec "s$1" timeout 40 dhcpcd -f /tmp/dhcpcd$1.conf -6 -1 -B -t 30 \
    "${_ppp:-ppp0}" > /tmp/dhcpcd$1.log 2>&1
  ip netns exec "s$1" ip -6 -o addr show dev lo scope global 2>/dev/null \
    | awk '{sub(/\/.*/, "", $4); print $4; exit}'
}
SL7=$(slaac 7); SL8=$(slaac 8)
echo "  SLAAC: sub7 ${SL7:-none}, sub8 ${SL8:-none}"
ip netns exec s7 ip -6 route | sed 's/^/    s7: /'
PDA7=$(pd 7); PDA8=$(pd 8)
grep -iE "delegated prefix|ADV|REPLY6|error|timed out" /tmp/dhcpcd7.log /tmp/dhcpcd8.log | sed 's/^/    /'
echo "  delegated, numbered on lo: sub7 ${PDA7:-none}, sub8 ${PDA8:-none}"
PD7=$(sed -n 's/.*delegated prefix \([0-9a-f:]*\/[0-9]*\).*/\1/p' /tmp/dhcpcd7.log | head -1)
PD8=$(sed -n 's/.*delegated prefix \([0-9a-f:]*\/[0-9]*\).*/\1/p' /tmp/dhcpcd8.log | head -1)
echo "  sub7 delegated ${PD7:-none} (the pool 2001:db8:200::/40), sub8 ${PD8:-none} (RADIUS: 2001:db8:280:100::/56)"
# Across the dataplane, both ways: the internet pings an address in each
# delegated prefix and in sub7's WAN prefix, and sees the reply.
P6_7=$(ip netns exec inet ping -6 -c 3 -i 0.3 -W 2 "${PDA7:-::1}" 2>&1 | grep -o '[0-9]*% packet loss')
P6_8=$(ip netns exec inet ping -6 -c 3 -i 0.3 -W 2 "${PDA8:-::1}" 2>&1 | grep -o '[0-9]*% packet loss')
P6_W=$(ip netns exec inet ping -6 -c 3 -i 0.3 -W 2 "${SL7:-::1}" 2>&1 | grep -o '[0-9]*% packet loss')
echo "  the internet to sub7's delegated ${P6_7:-?}, sub8's ${P6_8:-?}, sub7's SLAAC ${P6_W:-?}"
FIB7=$(vppctl show ip6 fib "${PD7:-::/128}" 2>/dev/null | tr -d '\r')
echo "$FIB7" | grep -E "^[0-9a-f:]+/|pppoe_session" | head -3 | sed 's/^/    /'
# The guard's own count of what it sent up and took down.
G6=$(vppctl show bpa pppoe-access 2>/dev/null | tr -d '\r')
echo "$G6" | grep -E "^ip6|^control" | sed 's/^/  guard: /'
G6_UP=$(echo "$G6" | sed -n 's/^ip6 to the control plane \([0-9]*\) from it \([0-9]*\)$/\1/p')
G6_DN=$(echo "$G6" | sed -n 's/^ip6 to the control plane \([0-9]*\) from it \([0-9]*\)$/\2/p')
# The plan applies to IPv6 too: VPP's policer is on both families' arcs.
# bw6 <n> [-R] - Mbit/s over IPv6 through sub<n>'s session, as bw() over IPv4
bw6() {
  ip netns exec inet iperf3 -s -1 -B 2001:db8:ffff::2 > /dev/null 2>&1 &
  _isp=$!
  sleep 1
  timeout 45 ip netns exec "s$1" iperf3 -6 -c 2001:db8:ffff::2 -t 15 -J ${2:-} 2>/dev/null \
    | python3 -c 'import json, sys
try:
    print(round(json.load(sys.stdin)["end"]["sum_received"]["bits_per_second"] / 1e6, 1))
except Exception:
    print(0)'
  kill "$_isp" 2>/dev/null; wait "$_isp" 2>/dev/null
}
DOWN6=$(bw6 7 -R); UP6=$(bw6 7)
echo "  sub7, plan 20M down / 10M up, over IPv6: ${DOWN6} down, ${UP6} up (Mbit/s)"
SHOW6=$($HX -c "show pppoe sessions" 2>&1)
echo "$SHOW6" | sed 's/^/  /'
SID7=$(sid_of sub7)
DETAIL7=$($HX -c "show pppoe session ${SID7:-0}" 2>&1)
echo "$DETAIL7" | grep -E "^(ipv6|delegated|address)" | sed 's/^/  /'
SUM6=$($HX -c "show pppoe summary" 2>&1)
echo "$SUM6" | grep '^ipv6' | sed 's/^/  /'
# The session trail: sub7's start with its WAN prefix, then a delegate entry
# when DHCPv6 gave it the /56; who held an IPv6 address, from the trail.
TRAIL6=$(tail -n "+$((TRAIL6_L0 + 1))" "$P6" 2>/dev/null)
echo "$TRAIL6" | grep -E " (start|delegate) session .* user sub[78] " | cut -c1-220 | sed 's/^/  trail: /'
START7=$(echo "$TRAIL6" | grep " start session .* user sub7 " | tail -1)
DELEG7=$(echo "$TRAIL6" | grep " delegate session .* user sub7 " | tail -1)
# sub8's prefix came from RADIUS, on the session from its Access-Accept:
# accel-ppp's dhcpv6 never took it from a pool, which is where it fired the
# event the delegate entry is written on - on 0.155.12 the bench's RADIUS
# prefix was routed, and no trail entry and no history named its subscriber.
DELEG8=$(echo "$TRAIL6" | grep " delegate session .* user sub8 " | tail -1)
T_START7=$(echo "$START7" | cut -d' ' -f1)
NOW6=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
HIST6=$($HX -c "show pppoe history address ${PDA7:-::1} at $NOW6" 2>&1)
HIST6_W=$($HX -c "show pppoe history address ${SL7:-::1} at $NOW6" 2>&1)
HIST6_EARLY=$($HX -c "show pppoe history address ${PDA7:-::1} at ${T_START7:-2000-01-01T00:00:00Z}" 2>&1)
HIST8=$($HX -c "show pppoe history address ${PDA8:-::1} at $NOW6" 2>&1)
echo "$HIST8" | sed 's/^/  history, the RADIUS-delegated address of sub8 now: /'
echo "$HIST6" | sed 's/^/  history, the delegated address now: /'
echo "$HIST6_EARLY" | sed 's/^/  history, the same at the session start: /'
# RADIUS accounting: the WAN prefix in the Start, the delegated one in the
# interim accel-ppp sends the moment it delegates (EV_FORCE_INTERIM_UPDATE).
sleep 2
ACCT6=$(cat /var/log/freeradius/radacct/127.0.0.1/detail-* 2>/dev/null)
A6_START=$(echo "$ACCT6" | awk 'BEGIN { RS = "" } /Acct-Status-Type = Start/ && /User-Name = "sub7"/ {r = $0} END {print r}')
A6_INT=$(echo "$ACCT6" | awk 'BEGIN { RS = "" } /Interim-Update/ && /User-Name = "sub7"/ && /Delegated-IPv6-Prefix/ {r = $0} END {print r}')
echo "$A6_START" | grep -E "Framed-IPv6-Prefix|Framed-Interface-Id" | sed 's/^/  acct Start:/'
echo "$A6_INT" | grep -E "Delegated-IPv6-Prefix" | sed 's/^/  acct Interim:/'
A8_INT=$(echo "$ACCT6" | awk 'BEGIN { RS = "" } /User-Name = "sub8"/ && /Delegated-IPv6-Prefix/ {r = $0} END {print r}')
# THE INTERFACE AN IPv6 SESSION LEAVES BEHIND. accel-ppp's VPP hook lets
# IPv6 in on a session that has it (ip6-not-enabled off) and never shuts it
# again, and VPP's plugin hands the interface to the next session: sub8
# hangs up and dials again IPv4-only, and must get the IPv6 drop back.
I8=$(vppctl show pppoe session | tr -d '\r' | awk '/sw-if-index/ {for (i = 1; i <= NF; i++) if ($i == "sw-if-index") idx = $(i + 1)} /client-mac 02:aa:00:00:00:08/ {print idx; exit}')
for _p in $(pgrep -x pppd); do
  case "$(tr '\0' ' ' < "/proc/$_p/cmdline" 2>/dev/null)" in
    *"call sub8 "*) kill -TERM "$_p" ;;
  esac
done
_n=0; while [ -n "$(sid_of sub8)" ] && [ $_n -lt 30 ]; do sleep 1; _n=$((_n + 1)); done
timeout 3600 ip netns exec s8 pppd call sub8v4 > /tmp/pppd8v4.log 2>&1 &
W8B=$(wait_user sub8 90)
I8B=$(vppctl show pppoe session | tr -d '\r' | awk '/sw-if-index/ {for (i = 1; i <= NF; i++) if ($i == "sw-if-index") idx = $(i + 1)} /client-mac 02:aa:00:00:00:08/ {print idx; exit}')
N8B=$(vppctl show interface | tr -d '\r' | awk -v i="${I8B:-x}" '/^[^ ]/ && $2 == i {print $1; exit}')
F8B=$(vppctl show interface features "${N8B:-none}" 2>/dev/null | tr -d '\r' | sed -n '/^ip6-unicast:/,/^[a-z0-9-]*:$/p')
N7=$(vppctl show interface | tr -d '\r' | awk -v i="$(vppctl show pppoe session | tr -d '\r' | awk '/sw-if-index/ {for (i = 1; i <= NF; i++) if ($i == "sw-if-index") idx = $(i + 1)} /client-mac 02:aa:00:00:00:07/ {print idx; exit}')" '/^[^ ]/ && $2 == i {print $1; exit}')
F7=$(vppctl show interface features "${N7:-none}" 2>/dev/null | tr -d '\r' | sed -n '/^ip6-unicast:/,/^[a-z0-9-]*:$/p')
echo "  sub8 was sw_if_index ${I8:-?}; IPv4-only after $W8B s, ${I8B:-?} (${N8B:-?}); its ip6-unicast arc:"
echo "$F8B" | sed 's/^/    /'
echo "  sub7's (IPv6) ip6-unicast arc:"
echo "$F7" | sed 's/^/    /'

echo
echo "=========== 22. THE TYPED API (docs/bng.md 0.4 item 2) ==========="
# The management API on these sessions, as prisma-web will read them: mutual
# TLS, root's certificate and a read-only account's, the stubs generated from
# the contract, the server the box runs. Every read is held to the VTY's own
# screen of the same command; the clears end real sessions, which redial.
C=/tmp/api; rm -rf $C; mkdir -p $C/box
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=lab clients CA" \
  -keyout $C/ca.key -out $C/ca.crt 2>/dev/null
for _who in server:prisma-api root:root netview:netview labadmin:labadmin; do
  _n=${_who%%:*}; _cn=${_who#*:}
  openssl req -newkey rsa:2048 -nodes -subj "/CN=$_cn" -keyout $C/$_n.key \
    -out $C/$_n.csr 2>/dev/null
  if [ "$_n" = server ]; then
    printf 'subjectAltName=DNS:prisma-api,IP:127.0.0.1\n' > $C/ext
  else
    printf 'extendedKeyUsage=clientAuth\n' > $C/ext
  fi
  openssl x509 -req -in $C/$_n.csr -CA $C/ca.crt -CAkey $C/ca.key \
    -CAcreateserial -days 2 -extfile $C/ext -out $C/$_n.crt 2>/dev/null
done
cp $C/server.key $C/server.crt $C/box/; cp $C/ca.crt $C/box/clients-ca.crt
id netview > /dev/null 2>&1 || useradd -M -s /usr/sbin/nologin netview
# An administrator that is not root (#597): the API's clears and a commit
# go as it. With the groups `role administrator` writes, and the three
# every account the VTY owns is given (docs/routing.md, Roles) - without
# vpp the dataplane's API is closed to it and no candidate can be opened.
for _g in prisma-admin prisma-cgnat vpp frr frrvty; do
  getent group $_g > /dev/null 2>&1 || groupadd --system $_g
done
id labadmin > /dev/null 2>&1 || useradd -M -s /usr/sbin/nologin \
  -G prisma-admin,prisma-cgnat,vpp,frr,frrvty labadmin
# And /etc/prisma-routing as appliance/install.sh leaves it on a box: setgid
# prisma-cgnat, 2770, its files the group's. Not the bng directory's mode,
# which is the VTY's to get right (pppoe.share_dir) and what this checks.
mkdir -p /etc/prisma-routing/rollback
chgrp -R prisma-cgnat /etc/prisma-routing
chmod 2770 /etc/prisma-routing /etc/prisma-routing/rollback
for _f in /etc/prisma-routing/*.conf; do chmod 0660 "$_f"; done
# The host's doors, as on a box: each account's worker asks the host for
# the sessions and the trail through them, and a read-only account is
# answered what its VTY would be.
# The VTY and the API server name the host's prisma-bng where a box has it
# (section 1 put the stand-in there): the doors run only that path, and the
# rest of this lab, which asks with no doors, keeps naming the script.
rm -f /run/prisma-routing/doors.sock
python3 /src/src/prismash/doors.py --serve > /tmp/doors22.log 2>&1 &
DOORS22=$!
PRISMA_BNG_CTL_LAB=$PRISMA_BNG_CTL
export PRISMA_BNG_CTL=/usr/lib/prisma-routing/prisma-bng
for _i in 1 2 3 4 5 6 7 8 9 10; do
  [ -S /run/prisma-routing/doors.sock ] && break; sleep 0.5
done
GEN=$(sh tools/gen-api.sh /tmp/apistubs 2>&1 && echo generated)
echo "  stubs: $GEN"
sleep 900 | python3 src/prismash/apiserver.py --listen 127.0.0.1:50557 \
  --dir $C/box --stubs /tmp/apistubs --stop-on-eof > /tmp/apiserver.log 2>&1 &
_n=0; until grep -q "prisma.v1 on" /tmp/apiserver.log || [ $_n -ge 30 ]; do
  sleep 0.5; _n=$((_n + 1)); done
sed 's/^/  /' /tmp/apiserver.log
# The sessions the format upgrade above restarted, back: sub2, sub3 and sub5
# (sub1 hung up in section 19).
if [ "$MODE" != off ]; then
  for _u in sub2 sub3 sub5; do echo "  $_u up after $(wait_user $_u 90) s"; done
fi
API22=$(python3 lab/api_pppoe_check.py /tmp/apistubs $C 127.0.0.1:50557 "$HX" 2>&1)
printf '%s\n' "$API22" | sed 's/^/  /'
APILOG22=$(cat /tmp/apiserver.log)
for _p in $(pgrep -x sleep); do
  [ "$(tr '\0' ' ' < "/proc/$_p/cmdline" 2>/dev/null)" = "sleep 900 " ] && kill "$_p"
done
kill "$DOORS22" 2>/dev/null; wait "$DOORS22" 2>/dev/null
rm -f /run/prisma-routing/doors.sock
export PRISMA_BNG_CTL=$PRISMA_BNG_CTL_LAB

# After the API's commits and clears: sub7's delegated prefix still routed.
P6_AFTER=$(ip netns exec inet ping -6 -c 3 -i 0.3 -W 2 "${PDA7:-::1}" 2>&1 | grep -o '[0-9]*% packet loss')
echo "  sub7's delegated prefix after section 22's commits: ${P6_AFTER:-?}"

echo
echo "=========== 11. no pppoe ==========="
printf 'configure\nno pppoe\ncommit\n' | $HX > /tmp/nopppoe.txt 2>&1
grep -o "pppoe: [a-z, ]*" /tmp/nopppoe.txt | sed 's/^/  commit: /'
# The CPEs go on sending PADI (persist): the dataplane must survive them with
# no control plane behind the TAP - which it does NOT if the plumbing is taken
# out (appliance/prisma-bng-entrypoint, unplumb(), has the crash).
sleep 8
GONE_SESS=$(vppctl show pppoe session 2>/dev/null | tr -d '\r' | grep -c sw-if-index)
ALIVE=$(vppctl show version 2>/dev/null | head -1)
STATUS=$(sh /src/appliance/prisma-bng status 2>&1)
echo "  sessions left in the dataplane: $GONE_SESS; the container: $STATUS"
echo "  the dataplane, 8 s of PADIs later: ${ALIVE:-NOT ANSWERING}"
NOCFG=$($HX -c "show running-config" 2>&1)
POL_END=$(pols)
echo "  VPP's policers: ${POL0:-?} before any session, ${POL_END:-?} now"
echo "  SNMP with PPPoE gone:"
snmp_bng f
echo "  the host's watcher, PPPoE gone: $(tick)"

echo
echo "=========== 21. show log: THE CONTROL PLANE'S RUNS, AND RADIUS ==========="
LOG_ALL=$($HX -c "show log last 300" 2>&1)
LOG_START=$($HX -c "show log kind start last 50" 2>&1)
LOG_EXIT=$($HX -c "show log kind exit last 50" 2>&1)
LOG_RADIUS=$($HX -c "show log kind radius last 50" 2>&1)
LOG_ASKED=$($HX -c "show log kind asked last 50" 2>&1)
echo "$LOG_START" | sed 's/^/  /'
echo "$LOG_EXIT" | sed 's/^/  /'
echo "$LOG_RADIUS" | sed 's/^/  /'
echo "$LOG_ASKED" | sed 's/^/  /'
cat /run/prisma-routing/bng/runs 2>/dev/null | sed 's/^/  runs: /'
# What the record is compared with, counted when it was read: section 24's
# restarts come after it.
RUNS21=$(wc -l < /run/prisma-routing/bng/runs 2>/dev/null || echo 0)
# A sentence as the record holds it, unfolded: `show log` folds at 79.
REC=$(python3 -c 'import json, sys
for l in open("/var/log/prisma-routing/events.log"):
    e = json.loads(l)
    print(e.get("kind", ""), e.get("component") or e.get("affects", ""),
          e.get("cause", ""), "|", e["text"])' 2>/dev/null)
echo "$REC" | grep -E "^(start|exit|radius|asked)" | sed 's/^/  record: /'

echo
echo "=========== 24. SHAPING: A QUEUE PER SESSION, THE DEFAULT (#610, docs/bng.md 0.9) ==========="
# Sections 12 and 13 ran each plan as VPP's policer - the block said
# `rate-limit download policer`. Here it says nothing, which is the default:
# the download by the dataplane's shaper (src/plugins/bpa/bpa_pppoe_shaper.c,
# a queue per session), the upload by the policer. At the end of the lab,
# after `no pppoe` (section 11) and the log's reading (section 21): its
# restarts would otherwise be among the control plane's runs those count.
# The CPEs never stopped dialling and come back. sub7 (Prisma's attributes,
# 20/10, dual-stack) is the subscriber; sub3 has its plan in Cisco-AVPair.
if [ "$MODE" != off ]; then
  { echo configure; echo "$PPPOE_BLOCK" | grep -v '^rate-limit download'; echo commit; } \
    | $HX > /tmp/shape-commit.txt 2>&1
fi
grep -o "pppoe: [a-z, ]*" /tmp/shape-commit.txt | sed 's/^/  commit: /'
W7S=$(wait_user sub7 90); W3S=$(wait_user sub3 90); wait_user sub2 60 > /dev/null
echo "  sub7 up after $W7S s, sub3 after $W3S s"
LIMITERS=$(grep -E '^vpp-(down|up)-limiter=|^vpp-shaper-queue=' /etc/prisma-routing/bng/accel-ppp.conf | tr '\n' ' ')
echo "  accel-ppp.conf: $LIMITERS"
# sess_if <mac> - "<sw_if_index> <name>" of the VPP session of that client
sess_if() {
  _i=$(vppctl show pppoe session < /dev/null | tr -d '\r' \
       | awk -v m="$1" '/sw-if-index/ {for (i = 1; i <= NF; i++) if ($i == "sw-if-index") idx = $(i + 1)}
                      index($0, "client-mac " m) {print idx; exit}')
  _n=$(vppctl show interface < /dev/null | tr -d '\r' | awk -v i="${_i:-x}" '/^[^ ]/ && $2 == i {print $1; exit}')
  echo "${_i:-none} ${_n:-none}"
}
set -- $(sess_if 02:aa:00:00:00:07); SIF7=$1; SNAME7=$2
set -- $(sess_if 02:aa:00:00:00:03); SIF3=$1
FEAT7=$(vppctl show interface features "$SNAME7" 2>/dev/null | tr -d '\r')
SH7=$(vppctl show bpa pppoe-shaper sw-if-index "$SIF7" | tr -d '\r')
SH3=$(vppctl show bpa pppoe-shaper sw-if-index "$SIF3" | tr -d '\r')
echo "  sub7 is $SNAME7 (sw_if_index $SIF7); its shaper:"
echo "$SH7" | sed 's/^/    /'
echo "  sub3 (Cisco-AVPair): $(echo "$SH3" | grep -o 'down rate [0-9]* kbit')"
OUT7=$(echo "$FEAT7" | sed -n '/^ip4-output:/,/^[a-z0-9-]*:$/p')
OUT76=$(echo "$FEAT7" | sed -n '/^ip6-output:/,/^[a-z0-9-]*:$/p')
IN7=$(echo "$FEAT7" | sed -n '/^ip4-unicast:/,/^[a-z0-9-]*:$/p')
# bwr <n> [-R] - "<Mbit/s> <retransmits>" of one TCP flow, as bw()
bwr() {
  _ppp=$(ip netns exec "s$1" ip -o -4 addr show 2>/dev/null | awk '$2 ~ /^ppp/ {print $2; exit}')
  ip netns exec "s$1" ip route replace default dev "${_ppp:-ppp0}" 2>/dev/null
  ip netns exec inet iperf3 -s -1 -B 200.150.0.2 > /dev/null 2>&1 &
  _isp=$!
  sleep 1
  timeout 45 ip netns exec "s$1" iperf3 -c 200.150.0.2 -t 15 -J ${2:-} 2>/dev/null \
    | python3 -c 'import json, sys
try:
    e = json.load(sys.stdin)["end"]
    print(round(e["sum_received"]["bits_per_second"] / 1e6, 1), e["sum_sent"].get("retransmits", "?"))
except Exception:
    print(0, "?")'
  kill "$_isp" 2>/dev/null; wait "$_isp" 2>/dev/null
}
# under <n> <what> - sub<n>'s ping to the internet's average, ms, while
# <what> runs from sub<n>: "tcp" one TCP download, "udp <Mbit/s>" a UDP one
under() {
  ip netns exec inet iperf3 -s -1 -B 200.150.0.2 > /dev/null 2>&1 &
  _isp=$!
  sleep 1
  if [ "$2" = tcp ]; then
    timeout 30 ip netns exec "s$1" iperf3 -c 200.150.0.2 -t 10 -R > /dev/null 2>&1 &
  else
    timeout 30 ip netns exec "s$1" iperf3 -c 200.150.0.2 -u -b "${3}M" -l 1400 -t 10 -R > /dev/null 2>&1 &
  fi
  _ip=$!
  sleep 4
  ip netns exec "s$1" ping -c 30 -i 0.1 200.150.0.2 2>/dev/null | tail -1 | awk -F/ '{print int($5)}'
  wait "$_ip" 2>/dev/null
  kill "$_isp" 2>/dev/null; wait "$_isp" 2>/dev/null
}
set -- $(bwr 7 -R); DOWN7S=$1; RETX7S=$2
set -- $(bwr 7); UP7S=$1
echo "  sub7, 20M down by the shaper, 10M up by the policer: ${DOWN7S} down (${RETX7S} retransmits), ${UP7S} up"
LAT_TCP=$(under 7 tcp)
echo "  sub7's ping to the internet under its own TCP download: ${LAT_TCP:-?} ms"
ip netns exec inet tc qdisc replace dev wanp root netem delay 200ms
LONG7=$(bw 7 -R)
ip netns exec inet tc qdisc del dev wanp root 2>/dev/null
echo "  sub7 over a 200 ms longer path, one TCP flow down: ${LONG7}"
# A BURST WITHIN THE QUEUE: 120 datagrams of 1400 bytes (168,000 of the
# queue's 250,000 bytes) sent back to back down sub7's session. A queue
# takes them all and sends them at the plan - the last ~67 ms after the
# first; a policer with an empty bucket would drop past it.
cat > /tmp/burst-srv.py <<'S'
import socket, sys, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("200.150.0.2", 7777)); s.settimeout(20)
_, peer = s.recvfrom(64)
time.sleep(0.5)
for i in range(120):
    s.sendto(i.to_bytes(4, "big") + b"b" * 1396, peer)
S
cat > /tmp/burst-cli.py <<'S'
import socket, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(3)
s.sendto(b"go", ("200.150.0.2", 7777))
got, first, last = 0, 0, 0
try:
    while True:
        s.recv(2048)
        now = time.time()
        first = first or now
        last = now
        got += 1
except socket.timeout:
    pass
print(got, int((last - first) * 1000) if got else 0)
S
ip netns exec inet python3 /tmp/burst-srv.py &
_bs=$!
sleep 1
BEFORE7=$(vppctl show bpa pppoe-shaper sw-if-index "$SIF7" | tr -d '\r' | sed -n 's/.* delayed \([0-9]*\) .*/\1/p')
set -- $(ip netns exec s7 python3 /tmp/burst-cli.py); BURST_GOT=$1; BURST_MS=$2
wait "$_bs" 2>/dev/null
AFTER7=$(vppctl show bpa pppoe-shaper sw-if-index "$SIF7" | tr -d '\r' | sed -n 's/.* delayed \([0-9]*\) .*/\1/p')
echo "  a burst of 120 datagrams: ${BURST_GOT:-?} arrived, over ${BURST_MS:-?} ms; delayed ${BEFORE7:-?} -> ${AFTER7:-?}"
UDP7=$(udp_bw 7 40)
LAT_UDP=$(under 7 udp 40)
SH7U=$(vppctl show bpa pppoe-shaper sw-if-index "$SIF7" | tr -d '\r')
echo "  UDP at 40 Mbit/s down through the 20M shaper: ${UDP7} received; ping under it ${LAT_UDP:-?} ms"
echo "$SH7U" | sed 's/^/    /'
DQ7=$(echo "$SH7U" | sed -n 's/.* dropped-queue \([0-9]*\) dropped-codel \([0-9]*\) .*/\1 \2/p')
DETAIL7S=$($HX -c "show pppoe session $(sid_of sub7)" 2>&1)
echo "$DETAIL7S" | sed 's/^/  /'
DOWN76=$(bw6 7 -R)
echo "  sub7 over IPv6, through the same shaper: ${DOWN76} down"
SID7S=$(sid_of sub7)
DLY_BEFORE=$(echo "$SH7U" | sed -n 's/.* delayed \([0-9]*\) .*/\1/p')
COA_S=$(dae coa daesecret 'User-Name = "sub7"\nPrisma-Rate-Limit-Down = 50000\nPrisma-Rate-Limit-Up = 20000\n')
echo "$COA_S" | grep -E "Received|No reply" | sed 's/^/  CoA to 50M\/20M: /'
sleep 1
SH7C=$(vppctl show bpa pppoe-shaper sw-if-index "$SIF7" | tr -d '\r')
SID7C=$(sid_of sub7)
DOWN7C=$(bw 7 -R)
DLY_AFTER=$(vppctl show bpa pppoe-shaper sw-if-index "$SIF7" | tr -d '\r' | sed -n 's/.* delayed \([0-9]*\) .*/\1/p')
echo "  after it: $(echo "$SH7C" | grep -o 'down rate [0-9]* kbit'); ${DOWN7C} down; session ${SID7C:-gone} (was ${SID7S:-?}); delayed ${DLY_BEFORE:-?} -> ${DLY_AFTER:-?}"
DOWN3S=$(bw 3 -R)
echo "  sub3, Cisco-AVPair's 30M by the shaper: ${DOWN3S} down"
# A CONTROL PLANE KILLED WITH -9 LEAVES NO SHAPER: the next one deletes the
# dead one's sessions, VPP's pppoe plugin sets each interface down before it
# hands it to the next session, and the shaper goes with it.
APID=$(cat /run/prisma-routing/bng/accel-pppd.pid 2>/dev/null)
[ -n "$APID" ] && kill -9 "$APID" 2>/dev/null
echo "  accel-pppd ${APID:-?} killed"
sleep 5
wait_user sub7 90 > /dev/null; wait_user sub3 90 > /dev/null; wait_user sub2 60 > /dev/null
sleep 3
SHAPED_N=$(vppctl show bpa pppoe-shaper | tr -d '\r' | head -1 | awk '{print $2}')
LIMITED_N=$($HX -c "show pppoe sessions" 2>&1 | awk 'length($1) == 16 && $NF ~ /:/ && $4 ~ /\// && $4 !~ /^none\// {n++} END {print n + 0}')
echo "  after the restart: $SHAPED_N session(s) shaped, $LIMITED_N with a download limit"
# The API: the queue's counters as the screen's, what enforces each way, and
# the policer back by a SetConfig (lab/api_pppoe_check.py `shaper`).
python3 /src/src/prismash/doors.py --serve > /tmp/doors24.log 2>&1 &
DOORS24=$!
export PRISMA_BNG_CTL=/usr/lib/prisma-routing/prisma-bng
for _i in 1 2 3 4 5 6 7 8 9 10; do
  [ -S /run/prisma-routing/doors.sock ] && break; sleep 0.5
done
sleep 900 | python3 src/prismash/apiserver.py --listen 127.0.0.1:50557 \
  --dir $C/box --stubs /tmp/apistubs --stop-on-eof > /tmp/apiserver24.log 2>&1 &
_n=0; until grep -q "prisma.v1 on" /tmp/apiserver24.log || [ $_n -ge 30 ]; do
  sleep 0.5; _n=$((_n + 1)); done
API24=$(python3 lab/api_pppoe_check.py /tmp/apistubs $C 127.0.0.1:50557 "$HX" shaper 2>&1)
printf '%s\n' "$API24" | sed 's/^/  /'
for _p in $(pgrep -x sleep); do
  [ "$(tr '\0' ' ' < "/proc/$_p/cmdline" 2>/dev/null)" = "sleep 900 " ] && kill "$_p"
done
kill "$DOORS24" 2>/dev/null; wait "$DOORS24" 2>/dev/null
rm -f /run/prisma-routing/doors.sock
export PRISMA_BNG_CTL=$PRISMA_BNG_CTL_LAB
set -- $(sess_if 02:aa:00:00:00:07); SIF7P=$1; SNAME7P=$2
FEAT7P=$(vppctl show interface features "$SNAME7P" 2>/dev/null | tr -d '\r')
DOWN7P=$(bw 7 -R)
echo "  the download policed again (by the API's commit): ${DOWN7P} down"
printf 'configure\nno pppoe\ncommit\n' | $HX > /dev/null 2>&1
sleep 5
SHAPE_END=$(vppctl show bpa pppoe-shaper | tr -d '\r' | head -1)
echo "  no pppoe: $SHAPE_END"

echo
cat /tmp/radius1.log /tmp/radius2.log > /tmp/radius.log
echo "=========== VERDICT ==========="
a22() { printf '%s\n' "$API22" | grep -q "^RESULT $1 ok"; }
expect "API: ListPppoeSessions lists the sessions the VTY lists, id, user, address" a22 sessions-are-vty
expect "  its text is show pppoe sessions" a22 sessions-text-is-vty
expect "  each with its MAC (show pppoe session's), state, uptime, interface" a22 sessions-fields
expect "API: GetPppoeSession is show pppoe session, the policers too" a22 session-is-vty
expect "  an id that is not 16 hex digits: INVALID_ARGUMENT" a22 session-bad-id
expect "  an id no session has: NOT_FOUND" a22 session-unknown
expect "API: GetPppoeSummary: running, sessions, the trail's and refusals' counters as the VTY's" a22 summary-is-vty
expect "  the RADIUS servers by priority, with accel-ppp's state; the listener" a22 summary-radius
expect "  its text is show pppoe summary" a22 summary-text-is-vty
expect "API: ListPppoeHistory is show pppoe history user <u> last 3, to the millisecond" a22 history-is-vty
expect "  a filter the VTY refuses: INVALID_ARGUMENT" a22 history-refused
expect "API: VerifyPppoeHistory is show pppoe history verify: intact, the same head" a22 verify-is-vty
expect "API: GetConfig reads pppoe and aaa as fields, secret_set and no secret" a22 config-read
expect "  and no secret anywhere in the answer's bytes" a22 config-no-secret
expect "API: Clear by address ends the session holding it; it redials" a22 clear-address
expect "  and the trail closes that session, with its cause" a22 clear-address-history
expect "API: Clear by id ends that session; it redials" a22 clear-session
expect "API: Clear by user ends that user's session" a22 clear-user
expect "  an address no session holds: FAILED_PRECONDITION, nothing ended" a22 clear-address-nobody
expect "  an account without the role: PERMISSION_DENIED, the session kept" a22 clear-no-role
expect "  and it still reads the sessions" a22 reads-any-account
expect "API: an administrator that is not root commits aaa; accel-ppp.conf is written and RADIUS read again" a22 commit-as-administrator
expect_not_in "  and the API server raised nothing" "Traceback" "$APILOG22"
expect_in "PAP: sub1 has a session" " sub1 " "$SHOW"
expect_in "CHAP: sub2 has a session" " sub2 " "$SHOW"
expect_in "MS-CHAPv2 on VLAN 100: sub3 has a session" " sub3 " "$SHOW"
expect_in "  and it was MS-CHAPv2 that authenticated it, at RADIUS" \
  "MS-CHAP2-Response" "$(grep -A12 'User-Name = "sub3"' /tmp/radius.log)"
expect_not_in "a wrong password is refused: sub4 has no session" " sub4 " "$SHOW"
expect_in "  RADIUS said Access-Reject to it" "Access-Reject" \
  "$(grep -B30 'Access-Reject' /tmp/radius.log | grep -A30 'sub4')"
expect "the address came from RADIUS: sub1 is 100.64.0.77" \
  test "$A1" = 100.64.0.77
expect "the others from the pool" \
  sh -c "echo '$A2 $A3' | grep -Eq '^100\.64\.0\.[0-9]+ 100\.64\.0\.[0-9]+$'"
expect_in "the DNS servers reached the CPE" "primary   DNS address 8.8.8.8" \
  "$(cat /tmp/pppd1.log)"
expect_in "the summary says the control plane runs" "running" "$SUMMARY"
expect "  it made its own log directory, which a box does not have" \
  test -s "$L/accel-ppp.log"
expect_in "sub1 leaves on the block the model predicted" \
  "IP $PREDICTED > 200.150.0.2.53" "$CAP"
expect_not_in "  and its private address is not on the wire" "IP $A1." "$CAP"
expect_in "the reply came back through the session" " 0% packet loss" "$PING"
expect_in "the session hook clamped the MSS" "tcp-mss-clamping" "$HOOK"
expect "accounting: Start received for every session" \
  sh -c "test $(echo "$ACCT" | grep -c 'Acct-Status-Type = Start') -ge 3"
expect "accounting: an interim carried sub1's real input bytes" \
  sh -c "test '${INTERIM_IN:-0}' -gt 2000"
expect_in "the configuration holds the aaa block (#607)" \
  "  server 127.0.0.1 secret <set, not shown> priority 1" "$RUNNING"
expect_in "  and the second server after it" \
  "  server 127.0.0.2 secret <set, not shown> priority 2" "$RUNNING"
expect_not_in "  and never the secret" "testing123" "$RUNNING"
expect_not_in "  nor the Disconnect/CoA client's" "daesecret" "$RUNNING"
expect_not_in "  and no RADIUS left in the pppoe block" " radius " \
  "$(echo "$RUNNING" | sed -n '/^pppoe$/,/^ exit$/p')"
expect_not_in "  and no session interface" "pppoe_session" "$RUNNING"
expect_in "clear pppoe session cleared sub2's" "cleared: sid" "$CLEAR"
expect_not_in "  it was gone" "${SID2:-none}" "$AFTER_CLEAR"
expect "  RADIUS had its Start and its Stop" test "$STOP2" -ge 2
expect_in "  and sub2 redialled" " sub2 " "$REDIAL"
expect "a restart dropped every session" sh -c \
  "test -n '$NOW' || exit 1; for s in $BEFORE; do echo '$NOW' | grep -qw \$s && exit 1; done; exit 0"
expect "  and all three came back" \
  test "$(echo "$AFTER" | awk '/^[0-9a-f]+  / && length($1) == 16 && $NF ~ /:/' | wc -l)" -ge 3
expect "  while the subscriber not on PPPoE lost nothing" \
  test "${GOT:-0}" -ge 396
expect_in "rate limit: sub1 listed with its plan from Prisma's attributes, download first" " 20M/10M " "$RSHOW"
expect "  and sub2, with none, listed as unlimited" \
  sh -c "echo '$RSHOW' | awk '\$2 == \"sub2\"' | grep -q ' - '"
expect_in "  a policer on the session's input" "policer-input" "$FEAT1"
expect_in "  and on its output" "policer-output" "$FEAT1"
expect_in "  beside the CGNAT it is attached to" "bpa-in2out" "$FEAT1"
expect "  download measured within 10% of 20M (${DOWN1})" within "$DOWN1" 20
expect "  upload measured within 10% of 10M (${UP1})" within "$UP1" 10
expect "  a session with no plan is not limited (${DOWN2} > 60)" \
  python3 -c "import sys; sys.exit(float('${DOWN2:-0}') <= 60)"
expect_in "  show pppoe session: its limit" "20M down, 10M up" "$DETAIL1"
expect "  and its download policer dropped what was past the plan" \
  sh -c "echo '$DETAIL1B' | grep '^policer down' | grep -qv ' 0 dropped'"
expect "  two policers for each of the two limited sessions" test "${POL_LIVE:-0}" -eq 4
expect "  each bucket half a second of its rate, in bytes: 1250000/625000 (${CB1D}/${CB1U})" \
  test "${CB1D:-0}/${CB1U:-0}" = 1250000/625000
expect "  and Cisco's the same rule, not its own 3,000,000-byte burst: 1875000/937500 (${CB3D}/${CB3U})" \
  test "${CB3D:-0}/${CB3U:-0}" = 1875000/937500
expect "  UDP through the 20M policer: the policer itself exact, within 10%/5% (${UDP1})" \
  within "$UDP1" 20
expect "  one TCP flow over a 200 ms longer path still gets 80% of the plan down (${LONG1D} of 20)" \
  python3 -c "import sys; sys.exit(float('${LONG1D:-0}') < 16)"
expect "after accel-pppd was killed, every session's download goes to its own CPE" \
  test "$CROSSED" -eq 0
expect "  and VPP holds no session the killed daemon left" test "$VPP_SESS" -eq 3
expect_in "Cisco-AVPair: sub3 listed with its plan" " 30M/15M " "$RSHOW"
expect "  rate-limit output is the download: within 10% of 30M (${DOWN3})" within "$DOWN3" 30
expect "  the UP15 policy, its rate-profile's upload: within 10% of 15M (${UP3})" within "$UP3" 15
expect_in "  a CoA with Cisco's rate-limit input is ACKed" "Received CoA-ACK" "$COA_CISCO"
expect_in "    and changes the upload only" " 30M/5M " "$LIMIT3"
expect_in "  a malformed Cisco rate in a CoA is NAKed" "Received CoA-NAK" "$COA_CISCO_BAD"
expect_in "  a policy with no rate-profile in a CoA is NAKed" "Received CoA-NAK" "$COA_CISCO_NAME"
expect_in "CoA: a new rate is ACKed" "Received CoA-ACK" "$COA_OK"
expect "  on the same session, no redial" test "${SID1_AFTER:-x}" = "${SID1:-y}"
expect "  download now within 10% of 50M (${DOWN1C})" within "$DOWN1C" 50
expect "  upload now within 10% of 20M (${UP1C})" within "$UP1C" 20
expect "  the new bucket follows the new rate: 3125000 (${CB1C})" test "${CB1C:-0}" = 3125000
expect_in "  a 1 Gbit/s plan is ACKed" "Received CoA-ACK" "$COA_G"
expect "  and VPP holds it as 1000000 kbit/s with a 62500000-byte bucket" \
  sh -c "echo '$GPOL' | grep -q 'cir 1000000 eir 0 cb 62500000 ' && test \$(echo '$GPOL' | grep -c 'cb 62500000 ') -eq 2"
expect_in "  a rate past 10 Gbit/s is NAKed" "Received CoA-NAK" "$COA_BAD"
expect_in "  MikroTik's rate attribute is not read: NAKed" "Received CoA-NAK" "$COA_MKT"
expect_in "  an attribute nothing acts on is NAKed, Unsupported-Attribute" \
  "Received CoA-NAK" "$COA_UNKNOWN"
expect_in "    with the cause" "Unsupported-Attribute" "$COA_UNKNOWN"
expect_in "  and neither changed the limit" " 50M/20M " "$LIMIT_KEPT"
expect_in "  a Session-Timeout is ACKed" "Received CoA-ACK" "$COA_ST"
expect "  the session lives until then" test "$ALIVE3" -eq 1
expect "  and ends when it says" test "$GONE3" -eq 0
expect_in "    with Acct-Terminate-Cause Session-Timeout" "Session-Timeout" "$CAUSE3"
expect "Disconnect: a wrong secret gets no answer" \
  sh -c "! echo '$DM_WRONG' | grep -q Received"
expect_in "  a source that is not a client gets none, even with the secret" \
  "no reply" "$DM_STRANGER"
expect "  and neither ended the session" test "$STILL2" -eq 1
expect_in "  by User-Name: Disconnect-ACK" "Received Disconnect-ACK" "$DM_USER"
expect_not_in "    and the session is gone" "${SID2:-none}" "$AFTER_DM2"
expect_in "    its Stop says Admin-Reset" "Admin-Reset" "$CAUSE2"
expect_in "  by Acct-Session-Id: Disconnect-ACK" "Received Disconnect-ACK" "$DM_SID"
expect_not_in "    and the session is gone" "${SID1:-none}" "$AFTER_DM1"
expect_in "    its Stop says Admin-Reset" "Admin-Reset" "$CAUSE1"
expect_in "  sub1 redials with its plan from the Access-Accept" " 20M/10M " "$REDIAL1"
expect "failover: while server 1 lived, server 2 was asked nothing" test "$B1" -eq 0
expect "  server 1 killed: a new session authenticates within 20 s (${FO_SECS} s)" \
  sh -c "test '$FO_SECS' != none && test '$FO_SECS' -le 20"
expect "    on server 2" test "$A2_1" -gt "$A2_0"
expect "    and accounts there" test "$S2_1" -gt "$S2_0"
expect_in "  server 1 is shown failed" "failed" \
  "$(echo "$FO_SUMMARY" | grep '127.0.0.1')"
expect "  the next session does not wait for it (${NEXT_SECS} s)" \
  sh -c "test '$NEXT_SECS' != none && test '$NEXT_SECS' -le 8"
expect "  server 1 back, after its dead-time it is asked again" \
  test "$A1_1" -gt "$A1_0"
expect_in "round-robin: the change is a reload" "pppoe: reloaded" "$(cat /tmp/rr.txt)"
expect "  and no session dropped" test "$RR_BEFORE" = "$RR_AFTER"
expect "  sessions authenticate on server 1" test "$RQ1" -ge 1
expect "  and on server 2" test "$RQ2" -ge 1
expect "  accounting goes to both as well" sh -c "test $RC1 -ge 1 && test $RC2 -ge 1"
expect_in "  the summary says round-robin" "round-robin" "$RR_SUMMARY"
expect "  six sequential logins alternate between the servers, within one ($SQ1/$SQ2)" \
  sh -c "test $((SQ1 + SQ2)) -eq 6 && test $((SQ1 - SQ2)) -le 1 && test $((SQ2 - SQ1)) -le 1"
expect_in "no pppoe stopped the container" "pppoe: stopped" "$(cat /tmp/nopppoe.txt)"
expect "  every policer gone with the sessions (${POL0:-?} -> ${POL_END:-?})" \
  test "${POL_END:-x}" = "${POL0:-y}"
expect "  every session gone from the dataplane" test "$GONE_SESS" -eq 0
expect_in "  the container is not running" "not-configured" "$STATUS"
expect_in "  and the dataplane lives through the PADIs that keep coming" \
  "vpp v" "$ALIVE"
expect_not_in "  and the configuration holds no pppoe block" "pppoe" "$NOCFG"

# prBng (#597): the counters, against the things they count.
delta() { python3 -c 'import sys; print(int(sys.argv[1] or 0) - int(sys.argv[2] or 0))' "$1" "$2" 2>/dev/null || echo x; }
expect "SNMP: prBngState running(1) with sessions up ($(bv a 1.0))" test "$(bv a 1.0)" = 1
expect "  prBngSessionsActive is the VTY's count of sessions up (${GET_UP} by snmpget, $(bv a 3.0) walked, ${UP_VTY} listed)" \
  sh -c "test '${GET_UP}' = '${UP_VTY}' && test '$(bv a 3.0)' = '${UP_VTY}' && test '${UP_VTY}' -eq 3"
expect "  and it is started minus stopped ($(bv a 5.0) - $(bv a 6.0))" \
  test "$(delta "$(bv a 5.0)" "$(bv a 6.0)")" = "$(bv a 3.0)"
expect "  server 1's Access-Accepts are the ones it sent ($ACC_A0 <= $(bv a 20.1.5.127.0.0.1) <= $ACC_A1)" \
  sh -c "test '$(bv a 20.1.5.127.0.0.1)' -ge $((ACC_A0 > 0 ? ACC_A0 - 1 : 0)) && test '$(bv a 20.1.5.127.0.0.1)' -le $ACC_A1 && test $ACC_A1 -ge 3"
expect "  its Access-Rejects too, sub4's wrong password ($REJ_A0 <= $(bv a 20.1.6.127.0.0.1) <= $REJ_A1)" \
  sh -c "test '$(bv a 20.1.6.127.0.0.1)' -ge $((REJ_A0 > 0 ? REJ_A0 - 1 : 0)) && test '$(bv a 20.1.6.127.0.0.1)' -le $REJ_A1 && test $REJ_A1 -ge 1"
expect "  server 2, the failover, has accepted nobody ($(bv a 20.1.5.127.0.0.2))" \
  test "$(bv a 20.1.5.127.0.0.2)" = 0
expect "  a login moves the accepts: sub2's redial ($(bv a 20.1.5.127.0.0.1) -> $(bv b 20.1.5.127.0.0.1))" \
  sh -c "test $(delta "$(bv b 20.1.5.127.0.0.1)" "$(bv a 20.1.5.127.0.0.1)") -ge 1"
expect "  a refused one the rejects ($(bv a 20.1.6.127.0.0.1) -> $(bv b 20.1.6.127.0.0.1)), as server 1's log ($REJ_B0 <= x <= $REJ_B1)" \
  sh -c "test $(delta "$(bv b 20.1.6.127.0.0.1)" "$(bv a 20.1.6.127.0.0.1)") -ge 1 && test '$(bv b 20.1.6.127.0.0.1)' -ge $((REJ_B0 > 0 ? REJ_B0 - 1 : 0)) && test '$(bv b 20.1.6.127.0.0.1)' -le $REJ_B1"
expect "  the clear counted as a stop and the redial as a start ($(bv a 6.0) -> $(bv b 6.0), $(bv a 5.0) -> $(bv b 5.0))" \
  sh -c "test $(delta "$(bv b 6.0)" "$(bv a 6.0)") -ge 1 && test $(delta "$(bv b 5.0)" "$(bv a 5.0)") -ge 1"
expect "  and still three up, started minus stopped ($(bv b 3.0))" \
  sh -c "test '$(bv b 3.0)' = 3 && test $(delta "$(bv b 5.0)" "$(bv b 6.0)") -eq 3"
B2C=$(delta "$(cat /tmp/bng-c.at 2>/dev/null)" "$(cat /tmp/bng-b.at 2>/dev/null)")
expect "the restarts of section 10 started the counters again: prBngUptime $(bv c 2.0) cs, younger than the ${B2C} s since the read before them" \
  sh -c "test -n '$(bv c 2.0)' && test $(( $(bv c 2.0 | grep -E '^[0-9]+$' || echo 999999999) / 100 )) -lt ${B2C:-0}"
expect "the CoAs answered: four ACKs ($(bv c 9.0) -> $(bv d 9.0))" \
  test "$(delta "$(bv d 9.0)" "$(bv c 9.0)")" = 4
expect "  and five NAKs, the refused ones ($(bv c 10.0) -> $(bv d 10.0))" \
  test "$(delta "$(bv d 10.0)" "$(bv c 10.0)")" = 5
expect "the Disconnects: two ACKs ($(bv c 7.0) -> $(bv d 7.0)), no NAK ($(bv d 8.0))" \
  sh -c "test $(delta "$(bv d 7.0)" "$(bv c 7.0)") -eq 2 && test $(delta "$(bv d 8.0)" "$(bv c 8.0)") -eq 0"
expect "  the wrong secret and the stranger counted as answered with nothing ($(bv c 11.0) -> $(bv d 11.0))" \
  sh -c "test $(delta "$(bv d 11.0)" "$(bv c 11.0)") -ge 2"
expect "failover: server 1's unanswered Access-Requests counted ($(bv d 20.1.7.127.0.0.1) -> $(bv e 20.1.7.127.0.0.1))" \
  sh -c "test $(delta "$(bv e 20.1.7.127.0.0.1)" "$(bv d 20.1.7.127.0.0.1)") -ge 1"
expect "  server 2's accepts ($(bv d 20.1.5.127.0.0.2) -> $(bv e 20.1.5.127.0.0.2))" \
  sh -c "test $(delta "$(bv e 20.1.5.127.0.0.2)" "$(bv d 20.1.5.127.0.0.2)") -ge 1"
expect "  and server 1, back, is active again ($(bv e 20.1.3.127.0.0.1))" \
  test "$(bv e 20.1.3.127.0.0.1)" = 1
expect "no pppoe: prBngState notConfigured(3) ($(bv f 1.0)), zero up ($(bv f 3.0)), zero CoAs ($(bv f 9.0))" \
  test "$(bv f 1.0)/$(bv f 3.0)/$(bv f 9.0)" = 3/0/0
expect_not_in "  and no server row" \
  ".22.20.1" "$(cat /tmp/bng-f.walk)"
expect "  and the uptime is absent ($(bv f 2.0))" test -z "$(bv f 2.0)"

# The session trail (docs/bng.md 0.7): the box's own record of who held
# which inside address from when to when.
expect "trail: sub1's cleared session has its stop, user, MAC, interface, address and cause" \
  sh -c "echo '$STOP_OLD' | grep -q ' user sub1 mac 02:aa:00:00:00:01 interface host-acc0 address 100.64.0.77\( prefix [0-9a-f:]*\/[0-9]*\)\? since .* cause NAS-Request '"
expect "  the cause RADIUS was sent for it, word for word (${RAD_CAUSE1:-none})" \
  test "${RAD_CAUSE1:-none}" = NAS-Request
expect "  written at the clear (${C0} <= ${T_STOP} <= ${C1} + 2)" \
  between "$C0" "${T_STOP:-0}" "$(python3 -c "print(${C1:-0} + 2)")"
expect "  the next session's start: sub1 on 100.64.0.77 again, after that stop" \
  sh -c "echo '$START_NEW' | grep -q ' user sub1 mac 02:aa:00:00:00:01 interface host-acc0 address 100.64.0.77\( prefix [0-9a-f:]*\/[0-9]*\)\? seq ' && python3 -c 'import sys; sys.exit(not ${T_START:-0} >= ${T_STOP:-1})'"
expect_in "  show pppoe history lists both" "sub1 held 100.64.0.77" "$HIST1"
expect "a control plane killed with -9: every session it held closed as Control-Plane-Lost (${LOST})" \
  sh -c "test '$LOST' -ge 3 && test '$LOST' -eq $(echo $UP_SIDS | wc -w)"
expect_in "  by the next one, before its first session" "restart closed" "$RESTART_B"
expect "  and nothing before it lost: the same first entry, the file only grew (${LINES_B0} -> ${LINES_B1})" \
  sh -c "test '$(head -1 "$TR")' = '$FIRST_B0' && test '${LINES_B1:-0}' -gt '${LINES_B0:-0}'"
expect_in "formula: sub1's packet of section 6, by its public port and instant, names sub1" \
  "sub1 held 100.64.0.77" "$XLT6"
expect "the block log: the BLOCK-ALLOC holding sub2's public port names its inside address (${SUB_LOG:-none})" \
  sh -c "test -n '$A2D' && test '${SUB_LOG:-x}' = '$A2D'"
expect_in "  and the trail, at the log's instant, names sub2" "sub2 held ${A2D:-none}" "$TRACE"
expect_in "  show cgnat translation names sub2, now" "sub2 held ${A2D:-none}" "$XLTD"
expect_in "  and refuses 'at' for a dynamic block" "'at' needs a formula" "$XLTD_AT"
expect_not_in "  half a second before sub2's session began, not sub2" "sub2 held" "$TRACE_BEFORE"
expect_in "verify: the chain intact" "chain intact" "$VERIFY"
expect_in "  an entry given another address: BROKEN at that entry" "BROKEN at entry" "$TAMPERED"
expect_in "  put back, intact again" "chain intact" "$VERIFY_BACK"
expect "no accounting server answering: the session's start recorded all the same" \
  sh -c "echo '$AD_START' | grep -q ' start session .* user sub3 '"
expect "  and its stop, NAS-Error, when accel-ppp gave up on accounting" test -n "$AD_STOP"
expect "  while no RADIUS server was sent an Accounting-Request for it" \
  test "$((ACCT1_1 - ACCT1_0 + ACCT2_1 - ACCT2_0))" -eq 0
expect "both RADIUS servers down: a session cleared has its stop recorded" \
  sh -c "echo '$DN_STOP' | grep -q ' cause NAS-Request '"
expect "  and no start: nobody could be authenticated, so nothing was held" \
  sh -c "test '$DN_STARTS' -eq 0 && test '$DN_LISTED' -eq 0"
expect_in "the chain verifies at the end, across every restart" "chain intact" "$VERIFY_END"
expect "the head sent off the box: a tick after sessions writes one trail event (bng, info) ($TRAIL_FIRST)" \
  sh -c "test '$TRAIL_FIRST' -eq 1 && echo \"\$1\" | grep -q '^info bng '" _ "$TRAIL_EV1"
expect "  its seq and sha256 are show pppoe history verify's head (${EV_SEQ:-none} ${EV_SHA:-none})" \
  sh -c "test -n '$EV_SEQ' && test '$EV_SEQ' = '$H_SEQ' && test -n '$EV_SHA' && test '$EV_SHA' = '$H_SHA'"
expect "  and an immediate second tick, the trail still ($TR_L0 -> $TR_L1 lines), writes none ($((TRAIL_NC - TRAIL_NB)))" \
  sh -c "test '$TR_L0' = '$TR_L1' && test '$((TRAIL_NC - TRAIL_NB))' -eq 0"
expect_in "  verify points at it" "show log kind trail" "$VERIFY_H"
expect_in "show pppoe summary: the trail's entries" "entries written" \
  "$(echo "$SUMMARY_T" | grep 'session trail')"
expect_not_in "  and none it could not write" "NOT" "$(echo "$SUMMARY_T" | grep 'session trail')"
# #597 0.4 item 3: the container's runs in `show log`, each with its reason.
expect_in "show log: the container's first start, by the commit of section 4" \
  "start bng operator | the PPPoE control plane (prisma-bng) started: PPPoE's configuration committed" "$REC"
expect_in "  its stop at section 10's commit, said as a stop" \
  "exit bng  | the PPPoE control plane (prisma-bng) stopped: stopped (SIGTERM)" "$REC"
expect_in "  and the start after it, the commit's" \
  "start bng operator | the PPPoE control plane (prisma-bng) started again: PPPoE's configuration committed" "$REC"
expect_in "  accel-pppd killed with -9: the exit, with the signal" \
  "exit bng  | the PPPoE control plane (prisma-bng) exited with code 1: accel-pppd was killed by signal 9 (KILL)" "$REC"
expect_in "  and the restart the restart policy made of it, a warning with the reason" \
  "start bng unexpected | the PPPoE control plane (prisma-bng) started again, by its restart policy, after it exited: accel-pppd was killed by signal 9 (KILL)" "$REC"
expect_in "  the format upgrade's restart, asked by the load" \
  "start bng upgrade | the PPPoE control plane (prisma-bng) started again: PPPoE's files from an older release (format 3) written again in format" "$REC"
expect_in "  and no pppoe: the commit asked, the container stopped" \
  "asked bng operator | PPPoE removed by a commit" "$REC"
expect "  each start and exit the container recorded is in the record once" \
  test "$(echo "$REC" | grep -cE '^(start|exit) bng')" -eq "${RUNS21:-0}"
expect_in "  show log kind exit lists them" "exited with code 1" "$LOG_EXIT"
expect_in "RADIUS: server 1 failed, in show log" \
  "radius   | RADIUS server 127.0.0.1 failed" "$REC"
# Its dead-time is 10 s: a reading can find it failed, or already back with
# its fail count moved - either is the failure said once.
expect "  and in use again" sh -c "echo \"\$1\" | grep -qE 'RADIUS server 127.0.0.1 (is active again|failed and was active again)'" _ "$REC"
expect_not_in "  and server 2 never said failed" "RADIUS server 127.0.0.2 failed" "$REC"
# #597 0.4 item 4: PPPoE only on the access interfaces.
expect "a PADI from an interface that is not access gets no PADO (${WRONG})" \
  test "${WRONG:-x}" = 0
expect "  and it was counted, refused where it arrived (${NA0} -> ${NA1})" \
  test "$(( ${NA1:-0} - ${NA0:-0} ))" -ge 5
expect "  sub1's MAC in a session frame from there, refused and counted (${SNA0} -> ${SNA1})" \
  test "$(( ${SNA1:-0} - ${SNA0:-0} ))" -ge 5
expect "  and show pppoe summary says so" sh -c \
  "echo \"\$1\" | awk '/^refused/ && \$2 >= 10 {f = 1} END {exit !f}'" _ "$SUM17"
expect "a PADI from an access interface is answered (${RIGHT} of 5)" \
  test "${RIGHT:-0}" -ge 4
expect "one MAC on two access ports: sub1 dialling while its MAC is heard on VLAN 100 keeps its downloads on its own port (${ENCAP_NAME})" \
  test "${ENCAP_NAME:-x}" = host-acc0
expect "  sub1 reaches the internet (${PING_R})" test "${PING_R:-x}" = "0% packet loss"
expect "  and none of its session's frames reach the other port's CPE (${STOLEN})" \
  test "${STOLEN:-x}" = 0
expect "  the PADIs on VLAN 100 were refused, counted (${H0} -> ${H1})" \
  test "$(( ${H1:-0} - ${H0:-0} ))" -ge 100
expect "a CPE with sub1's MAC dialling on the other port: sub1's session never changed ($CHANGES)" \
  test "$CHANGES" -eq 0
expect "  the other port got no session while sub1's lived" test -z "$SEEN5"
expect "  its discovery was refused, counted (${H2} -> ${H3})" \
  test "$(( ${H3:-0} - ${H2:-0} ))" -ge 1
expect "  sub1's pppd gone without a PADT, the MAC is still held (${NOTFREE} of 2 answered)" \
  test "${NOTFREE:-x}" = 0
expect "  a PADT from sub1's port frees it at once (${FREED} of 3 answered)" \
  test "${FREED:-0}" -ge 2
expect "  and its CPE, dialling again, has the MAC's session (${MOVED} s)" \
  sh -c "test '$MOVED' != none && test '$MOVED' -le 20"
expect "the format upgrade wrote the files again (format ${FMT_NOW})" \
  sh -c "test -n '${FMT_NOW}' && test '${FMT_NOW}' != 3"
# #609: IPv6 on the sessions.
in6() { python3 -c 'import ipaddress, sys
try:
    sys.exit(not ipaddress.IPv6Address(sys.argv[1]) in ipaddress.IPv6Network(sys.argv[2]))
except ValueError:
    sys.exit(1)' "$1" "$2"; }
expect "IPv6: sub7 dialled with IPv6CP and its kernel made a SLAAC address in the pool's /64 (${SL7:-none})" \
  in6 "${SL7:-x}" 2001:db8:100::/48
expect "  and sub8 too (${SL8:-none})" in6 "${SL8:-x}" 2001:db8:100::/48
sub6() { python3 -c 'import ipaddress, sys
try:
    n = ipaddress.IPv6Network(sys.argv[1])
    sys.exit(not (n.prefixlen == int(sys.argv[3]) and n.subnet_of(ipaddress.IPv6Network(sys.argv[2]))))
except ValueError:
    sys.exit(1)' "$1" "$2" "$3"; }
expect "  sub7 was delegated a /56 of the delegated pool by DHCPv6 (${PD7:-none})" \
  sub6 "${PD7:-x}" 2001:db8:200::/40 56
expect "  sub8's is RADIUS's Delegated-IPv6-Prefix, not the pool's (${PD8:-none})" \
  test "${PD8:-x}" = 2001:db8:280:100::/56
expect "  the internet reaches an address in sub7's delegated prefix through VPP, and the reply comes back (${P6_7:-none})" \
  test "${P6_7:-x}" = "0% packet loss"
expect "  and in sub8's, RADIUS's (${P6_8:-none})" test "${P6_8:-x}" = "0% packet loss"
expect "  and sub7's SLAAC address (${P6_W:-none})" test "${P6_W:-x}" = "0% packet loss"
expect "  the delegated prefix is a route through the session interface in VPP" \
  sh -c "echo \"\$1\" | grep -q 'pppoe_session'" _ "$FIB7"
expect "  the dataplane sent the CPEs' RS and DHCPv6 to the control plane (${G6_UP:-?}) and its answers to them (${G6_DN:-?})" \
  sh -c "test '${G6_UP:-0}' -ge 4 && test '${G6_DN:-0}' -ge 4"
expect "  still routed after the API's commits and clears (${P6_AFTER:-none})" \
  test "${P6_AFTER:-x}" = "0% packet loss"
expect "  sub7's 20/10 Mbit/s plan holds over IPv6: ${DOWN6} down" within "${DOWN6:-0}" 20
expect "    ${UP6} up" within "${UP6:-0}" 10
expect "  show pppoe sessions: sub7's WAN prefix and its delegated one under it" \
  sh -c "echo \"\$1\" | grep -qE '^ +ipv6 2001:db8:100:[0-9a-f:]*/64\$' && echo \"\$1\" | grep -qx ' *delegated ${PD7:-x}'" _ "$SHOW6"
expect "  show pppoe session <id>: both prefixes" \
  sh -c "echo \"\$1\" | grep -q '^ipv6 prefix  *2001:db8:100:' && echo \"\$1\" | grep -q '^delegated  *${PD7:-x}'" _ "$DETAIL7"
expect_in "  show pppoe summary: where each comes from" \
  "2001:db8:200::/40, a /56 each by DHCPv6" "$SUM6"
expect "  the trail: sub7's start carries its WAN prefix" \
  sh -c "echo '$START7' | grep -q ' prefix 2001:db8:100:[0-9a-f:]*/64 seq '"
expect "  and a delegate entry its delegated prefix, from when" \
  sh -c "echo '$DELEG7' | grep -q ' delegated ${PD7:-x} delegated-since 20[0-9-]*T[0-9:.]*Z since $T_START7 '"
expect_in "  show pppoe history address <an address of it> at now names sub7" "sub7 held" "$HIST6"
expect_in "  and with its prefixes" "delegated ${PD7:-x} from" "$HIST6"
expect_in "  the SLAAC address names sub7 too" "sub7 held" "$HIST6_W"
expect_in "  at the session's start, before DHCPv6 delegated it, nobody held the delegated prefix" \
  "no PPPoE session matches" "$HIST6_EARLY"
expect "  RADIUS: the Start carries Framed-IPv6-Prefix and Framed-Interface-Id" \
  sh -c "echo \"\$1\" | grep -q 'Framed-IPv6-Prefix = 2001:db8:100:' && echo \"\$1\" | grep -q 'Framed-Interface-Id'" _ "$A6_START"
expect "  and an interim the delegated prefix, sent when it was delegated" \
  sh -c "echo \"\$1\" | grep -q 'Delegated-IPv6-Prefix = ${PD7:-x}'" _ "$A6_INT"
expect "  sub8's accounting its RADIUS prefix" \
  sh -c "echo \"\$1\" | grep -q 'Delegated-IPv6-Prefix = 2001:db8:280:100::/56'" _ "$A8_INT"
expect "  sub8's RADIUS prefix has its delegate entry in the trail too" \
  sh -c "echo \"\$1\" | grep -q ' delegated 2001:db8:280:100::/56 delegated-since '" _ "$DELEG8"
expect_in "  and show pppoe history address <an address of it> names sub8" "sub8 held" "$HIST8"
expect "  an IPv6 session's interface lets IPv6 in (no ip6-not-enabled)" \
  sh -c "echo \"\$1\" | grep -q '^ip6-unicast:' && ! echo \"\$1\" | grep -q ip6-not-enabled" _ "$F7"
expect "  sub8, IPv4-only on the interface its IPv6 session left (${I8:-?} -> ${I8B:-?}), has IPv6 dropped again" \
  sh -c "test -n '${I8:-}' && test '${I8:-x}' = '${I8B:-y}' && echo \"\$1\" | grep -q ip6-not-enabled" _ "$F8B"
expect "API: ListPppoeSessions carries sub7's IPv6 prefixes as the screen shows them" a22 sessions-ipv6
expect "  GetConfig and GetPppoeSummary the IPv6 pools" a22 config-ipv6
expect "  ListPppoeHistory names sub7 for an address of its delegated prefix, with when" a22 history-ipv6
a24() { printf '%s\n' "$API24" | grep -q "^RESULT $1 ok"; }
expect_in "SHAPING (#610): the block's default is the download shaped, the upload policed" \
  "vpp-down-limiter=shaper vpp-up-limiter=policer vpp-shaper-queue=100" "$LIMITERS"
expect_in "  sub7's download by the shaper, on its IPv4 output" "bpa-pppoe-shaper-down" "$OUT7"
expect_not_in "    and no download policer there" "policer-output" "$OUT7"
expect_in "    and on its IPv6 output" "bpa-pppoe-shaper-down" "$OUT76"
expect_in "    its upload still a policer" "policer-input" "$IN7"
expect_in "  the dataplane holds its plan: 20000 kbit, a 100 ms queue" "down rate 20000 kbit queue 100 ms" "$SH7"
expect_in "  sub3's Cisco-AVPair plan is shaped too: 30000 kbit" "down rate 30000 kbit" "$SH3"
expect "  one TCP flow down holds the plan, within 10% of 20M (${DOWN7S})" within "${DOWN7S:-0}" 20
expect "    with few retransmits: what waits is not dropped (${RETX7S})" \
  sh -c "test '${RETX7S:-x}' -ge 0 2>/dev/null && test '${RETX7S:-x}' -lt 200"
expect "    and the upload, policed, within 10% of 10M (${UP7S})" within "${UP7S:-0}" 10
expect "  a ping under that download waits the queue CoDel keeps, not the queue's 100 ms (${LAT_TCP:-?} ms)" \
  sh -c "test '${LAT_TCP:-999}' -le 60"
expect "  one TCP flow over a 200 ms longer path gets 75% of the plan down (${LONG7} of 20)" \
  python3 -c "import sys; sys.exit(not float('${LONG7:-0}') >= 15)"
expect "  a burst of 120 datagrams within the queue arrives whole (${BURST_GOT:-0} of 120)" \
  test "${BURST_GOT:-0}" -eq 120
expect "    paced at the plan, not at once: over ${BURST_MS:-0} ms (168,000 bytes at 20M: about 67)" \
  test "${BURST_MS:-0}" -ge 40
expect "    delayed, as the shaper counts it (${BEFORE7:-?} -> ${AFTER7:-?})" \
  sh -c "test \$(( ${AFTER7:-0} - ${BEFORE7:-0} )) -ge 100"
expect "  UDP at twice the plan: exactly the plan arrives, within 10% (${UDP7})" within "${UDP7:-0}" 20
expect "    what is past it dropped by the queue (full, or CoDel), not by a bucket (${DQ7:-?})" \
  sh -c "set -- ${DQ7:-0 0}; test \$((\$1 + \$2)) -gt 0"
expect "    and a ping under it waits at most the queue, 100 ms (${LAT_UDP:-?} ms)" \
  sh -c "test '${LAT_UDP:-999}' -le 150"
expect "  show pppoe session: the shaper's plan and queue" \
  sh -c "echo \"\$1\" | grep -q '^shaper down  *20M, a queue of 100 ms (download)'" _ "$DETAIL7S"
expect "    and what waited, and for how long" \
  sh -c "echo \"\$1\" | grep -qE '^ +passed at once [0-9]+, delayed [1-9][0-9]*\$' && echo \"\$1\" | grep -qE '^ +waited [0-9.]+ ms on average\$'" _ "$DETAIL7S"
expect "  over IPv6, the same plan: within 10% of 20M (${DOWN76})" within "${DOWN76:-0}" 20
expect_in "  a CoA is ACKed" "Received CoA-ACK" "$COA_S"
expect_in "    and changes the shaped rate: 50000 kbit" "down rate 50000 kbit" "$SH7C"
expect "    on the same session, no redial" test "${SID7C:-x}" = "${SID7S:-y}"
expect "    keeping its counters (${DLY_BEFORE:-?} -> ${DLY_AFTER:-?})" \
  sh -c "test '${DLY_AFTER:-0}' -ge '${DLY_BEFORE:-1}'"
expect "    download now within 10% of 50M (${DOWN7C})" within "${DOWN7C:-0}" 50
expect "  sub3's Cisco plan holds, within 10% of 30M (${DOWN3S})" within "${DOWN3S:-0}" 30
expect "  accel-pppd killed with -9: no shaper left on an interface but the limited sessions' ($SHAPED_N shaped, $LIMITED_N limited)" \
  sh -c "test '${SHAPED_N:-x}' = '${LIMITED_N:-y}' && test '${LIMITED_N:-0}' -ge 2"
expect "API: GetPppoeSession's shapers are show pppoe session's, its policers the upload's" a24 session-shapers
expect "  GetPppoeSummary says what enforces each way, as the screen does" a24 summary-limiters
expect "  GetConfig reads the default as no statement" a24 config-limiters-default
expect "  SetConfig rate_limit_download policer, committed: the session redials policed each way" a24 switch-to-policer
expect_in "  the download policer back on sub7's output" "policer-output" "$FEAT7P"
expect_not_in "    and no shaper" "bpa-pppoe-shaper" "$FEAT7P"
expect "    within 10% of 20M (${DOWN7P})" within "${DOWN7P:-0}" 20
expect_in "  no pppoe: no shaper left in the dataplane" \
  "pppoe-shaper: 0 down 0 up shaped, 0 with a queue, buffers held 0" "$SHAPE_END"
expect_not_in "  and the API server raised nothing" "Traceback" "$(cat /tmp/apiserver24.log 2>/dev/null)"

verdict
