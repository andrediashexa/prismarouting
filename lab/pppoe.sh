#!/bin/sh
# PPPoE subscribers end to end: prisma-bng, RADIUS, CGNAT (#597).
#
#   sh tools/lab.sh pppoe        -> LAB PASSED
#   sh tools/lab.sh pppoe off    -> LAB FAILED (PPPoE never configured)
#
# OFF-RED-AT-LEAST: 22
#
# WHAT IS REAL HERE. Real PPPoE clients - pppd with rp-pppoe, one network
# namespace each, behind a Linux bridge, one of them on VLAN 100 - dial the
# dataplane. A real RADIUS server (freeradius) authenticates them and receives
# their accounting. The control plane is accel-ppp-ng compiled by
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
#     survives the PADIs that keep arriving with nothing to answer them.
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
cat > $R/mods-config/files/authorize <<'U'
sub1 Cleartext-Password := "pw1"
     Framed-IP-Address = 100.64.0.77
sub2 Cleartext-Password := "pw2"
sub3 Cleartext-Password := "pw3"
sub4 Cleartext-Password := "pw4"
U
rm -rf /var/log/freeradius/radacct
freeradius -X > /tmp/radius.log 2>&1 &
sleep 2
echo "  freeradius on 127.0.0.1: sub1 carries Framed-IP-Address 100.64.0.77"

echo
echo "=========== 4. THE DATAPLANE AND THE CONFIGURATION, THROUGH THE VTY ==========="
vpp -c /src/lab/startup-bng.conf > /tmp/vpp.log 2>&1 &
wait_vpp /tmp/vpp.log
for i in acc0 wan0 lan0; do
  vppctl create host-interface name "$i" > /dev/null
  vppctl set interface state "host-$i" up
done
vppctl create sub-interfaces host-acc0 100 > /dev/null
vppctl set interface state host-acc0.100 up

# The configuration as an operator types it, read by the VTY from its input.
{
  echo configure
  printf 'nat\ncgnat\npool lab\nprefix-inside 100.64.0.0/22\n'
  printf 'prefix-outside 200.150.0.16/30\nblock-size 512\nexit\nexit\nexit\n'
  printf 'interface host-wan0\nipv4 address 200.150.0.1/24\ncgnat outside\nexit\n'
  printf 'interface host-lan0\nipv4 address 100.64.1.1/24\ncgnat inside\nexit\n'
  printf 'interface host-acc0.100\nvlan 100\nexit\n'
  if [ "$MODE" != off ]; then
    cat <<'V'
pppoe
interface host-acc0
interface host-acc0.100
gateway 100.64.0.1
address-pool 100.64.0.10-100.64.0.200
dns 8.8.8.8
dns 1.1.1.1
radius server 127.0.0.1 secret testing123
radius interim-interval 60
tcp-mss 1452
exit
V
  fi
  echo commit
} > /tmp/config.vty
COMMIT=$($HX < /tmp/config.vty 2>&1)
echo "$COMMIT" | tr '#' '\n' | grep -E "pppoe:|commit|error" | sed 's/^/  /'
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
  timeout 900 ip netns exec "s$i" pppd call "sub$i" > "/tmp/pppd$i.log" 2>&1 &
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

echo
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
expect_in "the configuration holds the block" "radius server 127.0.0.1 secret <set, not shown>" "$RUNNING"
expect_not_in "  and never the secret" "testing123" "$RUNNING"
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
expect_in "no pppoe stopped the container" "pppoe: stopped" "$(cat /tmp/nopppoe.txt)"
expect "  every session gone from the dataplane" test "$GONE_SESS" -eq 0
expect_in "  the container is not running" "not-configured" "$STATUS"
expect_in "  and the dataplane lives through the PADIs that keep coming" \
  "vpp v" "$ALIVE"
expect_not_in "  and the configuration holds no pppoe block" "pppoe" "$NOCFG"

verdict
