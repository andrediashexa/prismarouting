#!/bin/sh
# PPPoE subscribers end to end: prisma-bng, RADIUS, CGNAT, rate limits (#597, #607).
#
#   sh tools/lab.sh pppoe        -> LAB PASSED
#   sh tools/lab.sh pppoe off    -> LAB FAILED (PPPoE never configured)
#
# OFF-RED-AT-LEAST: 22
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
#   * the policers are gone with the sessions: VPP's count is back to zero.
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
  freeradius-utils iperf3 ethtool \
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

echo
echo "=========== 2. TOPOLOGY ==========="
# acc0 is the access card (VPP host-acc0), behind a bridge every CPE is on;
# wan0 the upstream, with the "internet" in netns inet; lan0 a subscriber
# that is NOT on PPPoE, for what a restart of the container must not touch.
ip link add br0 type bridge; ip link set br0 up
ip link add acc0 type veth peer name accb
ip link set acc0 up; ip link set accb master br0; ip link set accb up
ip link add wan0 type veth peer name wanp; ip link set wan0 up
ip netns add inet
ip link set wanp netns inet
ip netns exec inet ip link set lo up
ip netns exec inet ip link set wanp up
ip netns exec inet ip addr add 200.150.0.2/24 dev wanp
ip netns exec inet ip route add 200.150.0.16/30 via 200.150.0.1
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
for i in 1 2 3 4; do
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
printf '"sub1" * "pw1" *\n"sub2" * "pw2" *\n"sub3" * "pw3" *\n"sub4" * "WRONG" *\n' \
  > /etc/ppp/pap-secrets
cp /etc/ppp/pap-secrets /etc/ppp/chap-secrets

echo "  br0: acc0 (VPP host-acc0, VLAN 100 = host-acc0.100) + four CPEs"
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
vpp -c /src/lab/startup-bng.conf > /tmp/vpp.log 2>&1 &
wait_vpp /tmp/vpp.log
for i in acc0 wan0 lan0; do
  vppctl create host-interface name "$i" > /dev/null
  vppctl set interface state "host-$i" up
done
vppctl create sub-interfaces host-acc0 100 > /dev/null
POL0=$(vppctl show policer pools 2>/dev/null | tr -d '\r' | sed -n 's/.*policers=\([0-9]*\).*/\1/p')
vppctl set interface state host-acc0.100 up

# The configuration as an operator types it, read by the VTY from its input.
{
  echo configure
  printf 'nat\ncgnat\npool lab\nprefix-inside 100.64.0.0/22\n'
  printf 'prefix-outside 200.150.0.16/30\nblock-size 512\nexit\nexit\nexit\n'
  printf 'interface host-wan0\nipv4 address 200.150.0.1/24\ncgnat outside\nexit\n'
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
    cat <<'V'
pppoe
interface host-acc0
interface host-acc0.100
gateway 100.64.0.1
address-pool 100.64.0.10-100.64.0.200
dns 8.8.8.8
dns 1.1.1.1
tcp-mss 1452
exit
V
  fi
  echo commit
} > /tmp/config.vty
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
sessions 3
SHOW=$($HX -c "show pppoe sessions" 2>&1)
echo "$SHOW" | sed 's/^/  /'
SUMMARY=$($HX -c "show pppoe summary" 2>&1)
echo "$SUMMARY" | sed 's/^/  /'
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
  timeout 30 ip netns exec "s$1" iperf3 -c 200.150.0.2 -t 6 -O 1 -J ${2:-} 2>/dev/null \
    | python3 -c 'import json, sys
try:
    print(round(json.load(sys.stdin)["end"]["sum_received"]["bits_per_second"] / 1e6, 1))
except Exception:
    print(0)'
  kill "$_isp" 2>/dev/null; wait "$_isp" 2>/dev/null
}
# within <measured> <target> - within 10% of the target
within() {
  python3 -c 'import sys; v, t = map(float, sys.argv[1:]); sys.exit(abs(v - t) > 0.1 * t)' "$1" "$2"
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
SIF1=$(vppctl show pppoe session | tr -d '\r' \
       | awk '/sw-if-index/ {for (i = 1; i <= NF; i++) if ($i == "sw-if-index") idx = $(i + 1)}
              /client-mac 02:aa:00:00:00:01/ {print idx; exit}')
SNAME1=$(vppctl show interface | tr -d '\r' | awk -v i="${SIF1:-x}" '$2 == i {print $1; exit}')
FEAT1=$(vppctl show interface features "${SNAME1:-none}" 2>/dev/null | tr -d '\r')
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
echo "  sub2, no plan: measured ${DOWN2} down"
UP3=$(bw 3); DOWN3=$(bw 3 -R)
echo "  sub3, Cisco-AVPair 30M down (output), profile UP15 up (input): ${DOWN3} down, ${UP3} up"
DETAIL1B=$($HX -c "show pppoe session ${SID1:-0}" 2>&1)
echo "$DETAIL1B" | sed 's/^/  /'
POL_LIVE=$(pols)
# THE DOWNLOAD GOES TO ITS OWN CPE, after section 10 killed accel-pppd with
# -9: each session's /32 is rewritten with its own client's MAC. Before
# #607 the recycled interfaces kept the dead daemon's adjacencies and sent
# one subscriber's traffic to another (docs/bng.md 0.6).
CROSSED=0
for ip in $(echo "$RSHOW" | awk '/^[0-9a-f]+  / && length($1) == 16 {print $3}'); do
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

echo
cat /tmp/radius1.log /tmp/radius2.log > /tmp/radius.log
echo "=========== VERDICT ==========="
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
expect_in "no pppoe stopped the container" "pppoe: stopped" "$(cat /tmp/nopppoe.txt)"
expect "  every policer gone with the sessions (${POL0:-?} -> ${POL_END:-?})" \
  test "${POL_END:-x}" = "${POL0:-y}"
expect "  every session gone from the dataplane" test "$GONE_SESS" -eq 0
expect_in "  the container is not running" "not-configured" "$STATUS"
expect_in "  and the dataplane lives through the PADIs that keep coming" \
  "vpp v" "$ALIVE"
expect_not_in "  and the configuration holds no pppoe block" "pppoe" "$NOCFG"

verdict
