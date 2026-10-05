#!/bin/sh
# OFF-RED-AT-LEAST: 166
# The management API over gRPC (#532), the way the web interface's backend
# will speak it: mutual TLS, a client certificate whose Common Name is the box
# account the call acts as, the VTY's own model underneath.
#
# Drives the full configuration cycle - open a candidate, edit it, the VTY's
# refusal at `commit check` and at `commit`, a line the VTY refuses at entry,
# compare, `commit confirmed`, confirm, rollback - and reads the result back
# through `prismash -c "show running-config"`, so the API and the prompt are
# held to one configuration. A candidate whose base the running configuration
# moved past is refused with the difference. The typed reads - among them
# the routing policy's (#538): its list, its counters and a route tested
# through it - a Show, a telemetry stream whose pg0 counters move with
# traffic, an account without the role and a client with no certificate are
# each asserted. And the web interface's additions (#533): a RIB of
# LAB_ROUTES routes (200,000) and a VRF paged, filtered and counted, the
# memory against meminfo, a worker busy under a packet generator and idle
# without one, a commit's event against its CommitResponse, the history read
# back by a second server, WhoAmI and the health service.
#
# MODE=off starts the same server trusting a CA that signed none of the
# clients: every call is refused at the handshake, and every check that reads
# an answer from the server goes red. What stays green is what never asked it
# - the stubs, the server's banner, the VTY's own `api` statement.
MODE=${1:-on}
set -u
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /etc/prisma-routing /var/run/frr /etc/frr
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
use_binary_api
export PRISMA_ROLLBACK=/tmp/rollback PRISMA_CONFIG=/tmp/prisma-routing.conf
rm -rf /tmp/rollback

# Two workers, so GetSystem has a worker load to say (#533).
SC=$(startup_conf test workers)
vpp -c "$SC" > /tmp/vpp.log 2>&1 &
wait_vpp
vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface state pg0 up
# A NAME THE VTY WILL READ AS THE HOST'S. A container is named by its id,
# which the VTY takes for a door nobody answered (#526) and reports as no
# name at all - so the host's name the API answers is given here, as a box
# has one.
hostname api-lab
printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
printf 'hostname prisma-routing\n' > /etc/frr/bgpd.conf
chown -R frr:frr /var/run/frr /etc/frr
# ospfd, ospf6d and isisd too: #558's third phase reads their configuration.
pgrep zebra >/dev/null || start_frr bgpd ospfd ospf6d isisd bfdd
sleep 2
HX="python3 /src/src/prismash/prismash.py"
# A box with an address and a pool before the API touches it.
$HX -c configure -c "interface pg0" -c "ipv4 address 100.64.3.254/22" -c exit \
    -c nat -c cgnat -c "pool nb" -c "inside 100.64.0.0/22" \
    -c "outside 200.150.0.16/30" -c top -c commit 2>&1 | tail -1 | sed 's/^/  /'
# A policy applied on a neighbor (#538): what ListPolicies, GetPolicyCounters
# and TestPolicy answer about.
$HX -c configure -c policy-options -c "prefix-list C" -c "203.0.113.0/24" \
    -c exit -c "policy-statement UP" -c "term cust" -c "from prefix-list C" \
    -c "set local-preference 200" -c "then accept" -c exit -c exit -c exit \
    -c "router bgp 65000" -c "neighbor 192.0.2.9 remote-as 65009" \
    -c "address-family ipv4 unicast" -c "neighbor 192.0.2.9 import UP" \
    -c "exit-address-family" -c exit -c commit 2>&1 | tail -1 | sed 's/^/  /'
# THE ROUTING HALF (#558, phase 3): a static route, a BGP router with its
# router-id, a description, a family's network and a statement no field
# takes, OSPF with a network and a stub area, OSPF6, IS-IS, and pg0's OSPF
# settings (staged, below) - what GetConfig's routing areas are held to.
ROUTE3=$($HX -c configure -c "router static" \
    -c "10.66.0.0/16 next-hop 100.64.3.1 distance 150 tag 7" -c exit \
    -c "router bgp 65000" -c "bgp router-id 10.255.0.1" \
    -c "bgp log-neighbor-changes" \
    -c "neighbor 192.0.2.9 description upstream" \
    -c "address-family ipv4 unicast" -c "network 198.18.0.0/24" \
    -c "exit-address-family" -c exit \
    -c "router ospf" -c "ospf router-id 10.255.0.1" \
    -c "network 100.64.0.0/22 area 0" -c "area 1 stub" -c exit \
    -c "router ospf6" -c "ospf6 router-id 10.255.0.1" -c exit \
    -c "router isis HEXA" -c "net 49.0001.0000.0000.0001.00" \
    -c "is-type level-2-only" -c exit \
    -c commit 2>&1)
# pg0's OSPF settings are read from a candidate in the check, not committed:
# pg0 is a packet-generator interface with no Linux end, so FRR has no
# interface to apply `ip ospf area` to and the reload refuses the commit -
# measured here (0.149.20), a property of this lab and not of a box, where
# every dataplane interface has its linux_cp pair.
printf '%s\n' "$ROUTE3" | tail -3 | sed 's/^/  /'
# A FULL-TABLE-SIZED RIB (#533): ListRoutes is built for a million routes,
# and is measured here on LAB_ROUTES of them - kernel routes through a dummy
# interface, which zebra takes into its RIB as it would any - and on a VRF
# beside the default one.
LAB_ROUTES=${LAB_ROUTES:-200000}
ip link add d0 type dummy 2>/dev/null; ip link set d0 up
ip addr add 192.0.2.1/24 dev d0 2>/dev/null
# The VRF through the VTY (#540), not by hand: the rollback below commits a
# revision, and a commit removes a VRF device its configuration does not
# have - the first run of this lab lost a hand-made `red` that way.
$HX -c configure -c "vrf red table 10" -c commit 2>&1 | tail -1 | sed 's/^/  /'
ip link add d1 type dummy 2>/dev/null; ip link set d1 master red; ip link set d1 up
ip addr add 198.51.100.1/24 dev d1 2>/dev/null
ip route add 172.16.0.0/16 via 198.51.100.2 vrf red
vtysh -c "show vrf" | sed 's/^/  zebra: /'
# In batches of 20,000, each waited for: zebra reads the kernel's routes off
# a netlink socket, and the third run of this lab, whose 200,000 arrived in
# one batch right after a commit, still had 36,000 of them missing from
# zebra's RIB five minutes later, where the two runs before had all of them
# in 6 s.
python3 -c "
import sys
n = int(sys.argv[1])
for k in range(0, n, 20000):
    with open(f'/tmp/routes.{k // 20000:03d}.batch', 'w') as f:
        for i in range(k, min(n, k + 20000)):
            f.write(f'route add {10 + i // 65536}.{(i // 256) % 256}.{i % 256}.0/24 via 192.0.2.2 dev d0\\n')
" "$LAB_ROUTES"
zebra_kernel() {
  vtysh -c 'show ip route vrf default summary json' 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)
print(sum(r.get("rib", 0) for r in d.get("routes", []) if r.get("type") == "kernel"))' 2>/dev/null || echo 0
}
T0=$(date +%s); LOADED=0
for b in /tmp/routes.*.batch; do
  ip -batch "$b"
  LOADED=$((LOADED + $(wc -l < "$b")))
  n=0; until [ "$(zebra_kernel)" -ge "$LOADED" ] || [ $n -ge 60 ]; do sleep 1; n=$((n+1)); done
done
echo "  $(zebra_kernel) of $LAB_ROUTES kernel routes in zebra's RIB after $(( $(date +%s) - T0 ))s"
# The account without a role: no prisma-admin, no prisma-cgnat.
id netview >/dev/null 2>&1 || useradd -M -s /usr/sbin/nologin netview
# #563: an account with no role that holds the system actions - prisma-operator,
# which the lab creates the way the VTY's first `operator` does on a box.
getent group prisma-operator >/dev/null 2>&1 || groupadd --system prisma-operator
id opsman >/dev/null 2>&1 || useradd -M -s /usr/sbin/nologin -G prisma-operator opsman
chmod 0755 /tmp/rollback 2>/dev/null; chmod 0644 /tmp/rollback/* 2>/dev/null
chmod 0644 "$PRISMA_CONFIG" 2>/dev/null

echo "=========== the certificates: a CA, the server, two accounts ==========="
C=/tmp/api; rm -rf $C; mkdir -p $C
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=lab clients CA" \
  -keyout $C/ca.key -out $C/ca.crt 2>/dev/null
sign() {  # sign <name> <CN> [server]
  openssl req -newkey rsa:2048 -nodes -subj "/CN=$2" -keyout $C/$1.key -out $C/$1.csr 2>/dev/null
  if [ "${3:-}" = server ]; then
    printf 'subjectAltName=DNS:prisma-api,IP:127.0.0.1\n' > $C/ext
  else
    printf 'extendedKeyUsage=clientAuth\n' > $C/ext
  fi
  openssl x509 -req -in $C/$1.csr -CA $C/ca.crt -CAkey $C/ca.key -CAcreateserial \
    -days 2 -extfile $C/ext -out $C/$1.crt 2>/dev/null
}
sign server prisma-api server
sign root root
sign netview netview
sign opsman opsman
# The layout the box reads: server.key, server.crt, clients-ca.crt.
mkdir -p $C/box
cp $C/server.key $C/server.crt $C/box/; cp $C/ca.crt $C/box/clients-ca.crt
if [ "$MODE" = off ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=nobody's CA" \
    -keyout $C/other.key -out $C/box/clients-ca.crt 2>/dev/null
  echo "  MODE=off: the server trusts a CA that signed none of the clients"
fi
ls $C/box | sed 's/^/  /'

echo "=========== the stubs, generated from the contract ==========="
GEN=$(sh tools/gen-api.sh /tmp/apistubs 2>&1 && echo generated)
echo "  $GEN"

echo "=========== the server ==========="
# Its stdin held open by a sleep, as prisma-routing-api.service holds it: the
# end of this lab ends the sleep and the server has to stop by itself.
# The history sampled once a second rather than a minute, into a file the
# second server below reads back (#533).
export PRISMA_API_HISTORY=/tmp/api-history.json PRISMA_API_HISTORY_INTERVAL=1
# The log export read every second, a stall after 4 s rather than a minute
# (#555), so the lab sees the rule turn both ways in seconds.
export PRISMA_API_LOG_INTERVAL=1 PRISMA_API_LOG_STALL=4
export PRISMA_STARTUP_CONF=$SC
rm -f $PRISMA_API_HISTORY
sleep 900 | python3 src/prismash/apiserver.py --listen 127.0.0.1:50551 --dir $C/box \
  --stubs /tmp/apistubs --stop-on-eof > /tmp/apiserver.log 2>&1 &
n=0; until grep -q "prisma.v1 on" /tmp/apiserver.log || [ $n -ge 30 ]; do sleep 0.5; n=$((n+1)); done
sed 's/^/  /' /tmp/apiserver.log

# Traffic through pg0 while the stream is read.
# Traffic through pg0, sent by the client once the stream has its first
# sample, so the counters it reads move under it.
PGNEW=$(vppctl packet-generator new "name t limit 2000 node ip4-input interface pg0 size 60-60 data { UDP: 100.64.0.5 -> 8.8.8.8 UDP: 4000 -> 53 incrementing 8 }" 2>&1)
[ -z "$PGNEW" ] || echo "  pg: $PGNEW"

echo "=========== the client ==========="
OUT=$(python3 lab/api_grpc_check.py /tmp/apistubs $C 127.0.0.1:50551 "$LAB_ROUTES" 2>&1)
printf '%s\n' "$OUT" | sed 's/^/  /'
echo "=========== the CGNAT log export over the API (#555) ==========="
# A syslog collector in a namespace of its own, the box's syslog to it, and
# subscribers allocating blocks - each allocation is a line. 192.168.209/24
# and not 10.9.0/24: the RIB above is 10.0.0.0-13.x.x.0/24 via d0, and the
# first run sent the collector's lines into that dummy. Then the
# collector is killed while lines keep coming, and started again: GetLogging,
# the health service's prisma.v1.LogExport and the event stream must each
# say so, and GetLogging's fields must be the VTY's own screen.
LC="python3 lab/api_logging_check.py /tmp/apistubs $C 127.0.0.1:50551"
ip netns add logc 2>/dev/null
ip link add vlog type veth peer name vlogc 2>/dev/null
ip link set vlogc netns logc
ip addr add 192.168.209.1/24 dev vlog 2>/dev/null; ip link set vlog up
ip netns exec logc ip addr add 192.168.209.2/24 dev vlogc
ip netns exec logc ip link set vlogc up
ip netns exec logc ip link set lo up
collector_up() {
  ip netns exec logc python3 -c '
import socket, threading
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("192.168.209.2", 601)); s.listen(8)
def rd(c):
    while c.recv(65536):
        pass
while True:
    c, _ = s.accept(); threading.Thread(target=rd, args=(c,), daemon=True).start()
' > /tmp/logc.log 2>&1 &
  echo $! > /tmp/logc.pid
  sleep 1
}
allocate() {  # allocate <third octet>: subscribers 100.64.<o>.1-20 of pool lg, a block and a line each
  for h in $(seq 1 20); do
    vppctl packet-generator new "name a$1x$h limit 1 node ip4-input interface pg0 size 60-60 \
        data { UDP: 100.64.$1.$h -> 8.8.8.8 UDP: 40000 -> 53 incrementing 8 }" >/dev/null 2>&1
  done
  # Each stream by name: a bare `enable` restarts every stream the lab ever
  # made, the client's unlimited load1 among them, and pg0 then carried
  # traffic for the rest of the run - Clear's counters measured non-zero.
  for h in $(seq 1 20); do vppctl packet-generator enable-stream a$1x$h >/dev/null 2>&1; done
  sleep 1
  for h in $(seq 1 20); do vppctl packet-generator delete a$1x$h >/dev/null 2>&1; done
}
collector_up
# The way out lab/syslog.sh gives its subscribers: pg1 outside, 8.8.8.8 behind it.
vppctl create packet-generator interface pg1 >/dev/null 2>&1
vppctl set interface state pg1 up
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl set interface bpa in2out pg0 >/dev/null 2>&1
vppctl set interface bpa out2in pg1 >/dev/null 2>&1
# A DYNAMIC pool for the lines: `nb` is deterministic, which allocates
# nothing on demand and so writes no line (the second run of this section
# had one, the boot marker, and nothing to wait for).
$HX -c configure -c nat -c cgnat -c "pool lg" -c "prefix-inside 100.64.8.0/22" \
    -c "prefix-outside 200.150.0.32/30" -c "mode dynamic" -c top -c commit 2>&1 \
    | tail -1 | sed 's/^/  pool: /'
$HX -c configure -c nat -c cgnat -c "logging syslog 192.168.209.2 port 601 enterprise 32473" \
    -c top -c commit 2>&1 | tail -1 | sed 's/^/  /'
vppctl show bpa pool 2>&1 | grep '^pool ' | sed 's/^/  /'
allocate 8
sleep 4
LOG1=$($LC state 2>&1); H1=$($LC health 2>&1)
printf '%s\n%s\n' "$LOG1" "$H1" | sed 's/^/  up:      /'
$LC events 40 > /tmp/logevents.txt 2>&1 &
EVPID=$!
sleep 2
kill "$(cat /tmp/logc.pid)" 2>/dev/null
sleep 1
allocate 9
sleep 9
LOG2=$($LC state 2>&1); H2=$($LC health 2>&1)
printf '%s\n%s\n' "$LOG2" "$H2" | sed 's/^/  down:    /'
collector_up
sleep 12
LOG3=$($LC state 2>&1); H3=$($LC health 2>&1)
printf '%s\n%s\n' "$LOG3" "$H3" | sed 's/^/  back:    /'
wait $EVPID 2>/dev/null
EVENTS=$(cat /tmp/logevents.txt)
printf '%s\n' "$EVENTS" | sed 's/^/  stream:  /'
kill "$(cat /tmp/logc.pid)" 2>/dev/null

echo "=========== the VTY's own statement ==========="
APINO=$($HX -c configure -c "api enabled yes" -c "commit check" 2>&1)
printf '%s\n' "$APINO" | tail -2 | sed 's/^/  /'
APIOK=$($HX -c configure -c "api enabled yes" -c "api listen-address 127.0.0.1" \
            -c "api port 50551" -c commit 2>&1)
printf '%s\n' "$APIOK" | tail -2 | sed 's/^/  /'
APIENV=$(cat /etc/prisma-routing/api.env 2>&1)
APIRUN=$($HX -c "show running-config" 2>&1 | grep '^api ')
printf '%s\n' "$APIRUN" | sed 's/^/  /'
echo "=========== #558, phase 2: the configuration as fields ==========="
# Before phase 1, which leaves a commit-confirmed window open for the second
# server to read: every commit here would confirm it. Each typed edit goes the
# whole road - SetConfig, Commit, GetConfig of the running configuration - and
# the typed read is held to GetRunning's text, which is the VTY's.
T2=$(python3 lab/api_phase2_check.py /tmp/apistubs $C 127.0.0.1:50551 2>&1)
printf '%s\n' "$T2" | sed 's/^/  /'
echo "=========== #558, phase 3: the routing configuration as fields ==========="
T3=$(python3 lab/api_phase3_check.py /tmp/apistubs $C 127.0.0.1:50551 2>&1)
printf '%s\n' "$T3" | sed 's/^/  /'
echo "=========== #558, phase 4: system, accounts, policy and the rest as fields ==========="
# The running configuration's policy, VRF and accounts held to GetRunning;
# the rest staged in a candidate and held to ShowCandidate, never committed.
T4=$(python3 lab/api_phase4_check.py /tmp/apistubs $C 127.0.0.1:50551 2>&1)
printf '%s\n' "$T4" | sed 's/^/  /'
echo "=========== #558, phase 5: the clears, the log stream, the box itself ==========="
# A subscriber of pool lg with sessions for Clear to end.
vppctl packet-generator new "name c558 limit 5 node ip4-input interface pg0 size 60-60 data { UDP: 100.64.11.5 -> 8.8.8.8 UDP: 4100 -> 53 incrementing 4 }" >/dev/null 2>&1
vppctl packet-generator enable-stream c558 >/dev/null 2>&1
sleep 1
T5C=$(python3 lab/api_phase5_check.py /tmp/apistubs $C 127.0.0.1:50551 clear 2>&1)
printf '%s\n' "$T5C" | sed 's/^/  /'
# WatchLogging against the VTY's own monitor, both reading while subscribers
# of pool lg allocate: the same events, kind and subscriber, in both.
python3 lab/api_phase5_check.py /tmp/apistubs $C 127.0.0.1:50551 watch 14 \
  > /tmp/watch5.txt 2>&1 &
W5=$!
$HX -c "monitor cgnat logging seconds 14" > /tmp/monitor5.txt 2>&1 &
M5=$!
sleep 3
allocate 10
wait $W5 $M5
T5W=$(cat /tmp/watch5.txt)
EVKINDS="ALLOC RELEASE EXHAUSTED"
API_EV=$(printf '%s\n' "$T5W" | awk -v k="$EVKINDS" 'BEGIN{split(k,a," ");for(i in a)w[a[i]]=1}
  $1=="EVENT" && ($2 in w) {print $2, $3}' | sort | tr '\n' ';')
VTY_EV=$(awk -v k="$EVKINDS" 'BEGIN{split(k,a," ");for(i in a)w[a[i]]=1}
  ($2 in w) {print $2, $3}' /tmp/monitor5.txt | sort | tr '\n' ';')
printf '%s\n' "$T5W" | grep -E "^EVENT (STARTED|ALLOC|DELIVERY)" | head -6 | sed 's/^/  /'
printf '%s\n' "$T5W" | grep "^RESULT" | sed 's/^/  /'
# The VTY's own refusal of that account, which the API's is held to.
VTY_NOREAD=$(runuser -u netview -- $HX -c "monitor cgnat logging seconds 1" 2>&1)
echo "  the VTY as netview: $(printf '%s' "$VTY_NOREAD" | head -1)"
echo "  API events: $(printf '%s' "$API_EV" | tr ';' '\n' | grep -c .), VTY lines: $(printf '%s' "$VTY_EV" | tr ';' '\n' | grep -c .)"
# SystemService against a server whose systemctl and systemd-run are
# stand-ins: a container has no box to restart, and a real reboot of the
# machine this lab runs on would be the lab's own end. They record what they
# were asked, and the systemd-run one writes the log a detached run writes on
# a box, a line at a time. The real thing is measured on a box.
mkdir -p /tmp/fakebin /run/prisma-routing/api
cat > /tmp/fakebin/systemctl <<'SH'
#!/bin/sh
echo "$*" >> /tmp/fake-systemctl.argv
case "$1" in reboot|poweroff) exit 0 ;; esac
exit 1
SH
cat > /tmp/fakebin/systemd-run <<'SH'
#!/bin/sh
echo "$*" >> /tmp/fake-systemd-run.argv
last=""
for a in "$@"; do last=$a; done
# The unit's own shell makes the log's directory and redirects itself (#564);
# the stand-in does what that shell would.
log=$(printf '%s' "$last" | sed -n 's/.*exec >\([^ ]*\) 2>&1;.*/\1/p')
mkdir -p "$(dirname "$log")"
: > "$log"
case "$last" in
  *"system upgrade "*)
    v=$(printf '%s' "$last" | sed -n "s/.*system upgrade \([0-9.]*\)'.*/\1/p")
    ( sleep 1; echo "downloading https://prismarouting.com.br/releases/$v/upgrade" >> "$log"
      sleep 1; printf 'the package matches its published sha256:\n    abc\n' >> "$log"
      sleep 1; echo "installing the images" >> "$log"
      echo "upgrade: refresh-the-vty is scheduled: the VTY restarts on the new release in a few seconds" >> "$log"
      echo "upgraded to $v" >> "$log"
      echo "    the VTY restarts on $v in a few seconds: this session ends then, and" >> "$log"
      echo "prisma-api-exit 0" >> "$log" ) > /dev/null 2>&1 &
    ;;
  *"system dataplane restart"*)
    ( sleep 1; echo "restarting the dataplane" >> "$log"
      echo "prisma-api-exit 0" >> "$log" ) > /dev/null 2>&1 & ;;
esac
exit 0
SH
chmod +x /tmp/fakebin/systemctl /tmp/fakebin/systemd-run
rm -f /tmp/fake-systemctl.argv /tmp/fake-systemd-run.argv /run/prisma-routing/api/*.log
# #564: a box after a reboot has no /run/prisma-routing/api until the API
# saves its history; the system actions must not depend on it.
rm -rf /run/prisma-routing/api
# Made by root, writable by all (#563): opsman, not root, now runs the
# stand-ins too, and a file it created in sticky /tmp is one root may not
# open for writing (fs.protected_regular) - the root checks after it then
# read a list their own calls never reached.
for _f in /tmp/fake-systemctl.argv /tmp/fake-systemd-run.argv; do : > $_f; chmod 0666 $_f; done
# THE REAL DOORS DAEMON (#564), in front of these system actions. With
# nothing listening on doors.sock the VTY runs a command itself, so no lab
# ever sent a system action through the door - and 0.149.27, .28 and .29 each
# met, on a box, a door that refused or broke what the API built. Here the
# daemon is the box's own (doors.serve), its far end the stand-ins above: a
# shape the door does not accept is refused here as on a box. MODE=nodoors
# runs without it, the old way, for comparison.
DOORS_PID=""
if [ "$MODE" != nodoors ]; then
  rm -f /run/prisma-routing/doors.sock
  PATH=/tmp/fakebin:$PATH python3 -c "import sys; sys.path.insert(0, 'src/prismash'); import doors; doors.serve()" \
    > /tmp/doors5.log 2>&1 &
  DOORS_PID=$!
  n=0; until [ -S /run/prisma-routing/doors.sock ] || [ $n -ge 20 ]; do sleep 0.25; n=$((n+1)); done
  echo "  the doors daemon: $( [ -S /run/prisma-routing/doors.sock ] && echo listening || echo ABSENT)"
fi
rm -f /tmp/api5.fifo; mkfifo /tmp/api5.fifo
PATH=/tmp/fakebin:$PATH python3 src/prismash/apiserver.py --listen 127.0.0.1:50553 \
  --dir $C/box --stubs /tmp/apistubs --stop-on-eof < /tmp/api5.fifo > /tmp/apiserver5.log 2>&1 &
exec 9> /tmp/api5.fifo
n=0; until grep -q "prisma.v1 on" /tmp/apiserver5.log || [ $n -ge 30 ]; do sleep 0.5; n=$((n+1)); done
T5S=$(python3 lab/api_phase5_check.py /tmp/apistubs $C 127.0.0.1:50553 system 2>&1)
printf '%s\n' "$T5S" | sed 's/^/  /'
exec 9>&-
if [ -n "$DOORS_PID" ]; then
  kill $DOORS_PID 2>/dev/null; wait $DOORS_PID 2>/dev/null
  rm -f /run/prisma-routing/doors.sock
  DOORS_RUNS=$(grep -c "systemd-run --collect --unit=prisma-routing-api-.* -> 0" /tmp/doors5.log)
  DOORS_REFUSED=$(grep -c "systemd-run.* -> 126" /tmp/doors5.log)
  DOORS_BOX=$(grep -cE "systemctl (reboot|poweroff) -> 0" /tmp/doors5.log)
  echo "  through the door: $DOORS_RUNS detached run(s) started, $DOORS_BOX reboot/poweroff, $DOORS_REFUSED systemd-run refused"
  grep -E "systemd-run|systemctl (reboot|poweroff)" /tmp/doors5.log | cut -c1-150 | sed 's/^/    /'
fi
echo "=========== #558, phase 1: why refused, the confirm window, the candidates ==========="
# Mappings in pool nb for the re-map gate to be about: a few packets from a
# subscriber, just before.
vppctl packet-generator new "name p558 limit 20 node ip4-input interface pg0 size 60-60 data { UDP: 100.64.0.7 -> 8.8.8.8 UDP: 4000 -> 53 }" >/dev/null 2>&1
vppctl packet-generator enable-stream p558 >/dev/null 2>&1
sleep 1
P1=$(python3 lab/api_phase1_check.py /tmp/apistubs $C 127.0.0.1:50551 before /tmp/p558.json 2>&1)
printf '%s\n' "$P1" | sed 's/^/  /'

# A SECOND SERVER ON THE SAME PORT IS REFUSED (#532): gRPC's default
# SO_REUSEPORT let one bind beside the first, and the X520 bench (0.148.4) was
# found with two listeners on the backplane port after restarts.
timeout 20 python3 src/prismash/apiserver.py --listen 127.0.0.1:50551 --dir $C/box \
  --stubs /tmp/apistubs > /tmp/apiserver2.log 2>&1 < /dev/null
SECOND=$?
echo "  a second server on 127.0.0.1:50551: exit $SECOND, $(tail -1 /tmp/apiserver2.log)"
pkill -f "^sleep 900$"
n=0; while pgrep -f "apiserver.py --listen 127.0.0.1:50551" >/dev/null && [ $n -lt 30 ]; do sleep 0.5; n=$((n+1)); done
STOPPED=$(pgrep -f "apiserver.py --listen 127.0.0.1:50551" >/dev/null && echo running || echo stopped)
echo "  after its stdin closed: $STOPPED"
# THE HISTORY OUTLIVES THE SERVER (#533): the one that stopped wrote it, and
# a new one - an upgrade restarts the server - reads it back, so a client's
# graphs go on. Asked of the new server for samples older than it.
HSAVED=$(python3 -c "import json; print(len(json.load(open('$PRISMA_API_HISTORY'))['times']))" 2>&1)
H2START=$(date +%s)
# Its candidates expire after 6 s rather than 30 minutes, so phase 1's
# client sees one expire (#558).
sleep 900 | PRISMA_API_CANDIDATE_IDLE=6 python3 src/prismash/apiserver.py --listen 127.0.0.1:50552 --dir $C/box \
  --stubs /tmp/apistubs --stop-on-eof > /tmp/apiserver3.log 2>&1 &
n=0; until grep -q "prisma.v1 on" /tmp/apiserver3.log || [ $n -ge 30 ]; do sleep 0.5; n=$((n+1)); done
HAFTER=$(python3 - /tmp/apistubs $C 127.0.0.1:50552 "$H2START" <<'PY' 2>&1
import sys
sys.path.insert(0, sys.argv[1])
import grpc
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc
c = sys.argv[2]
creds = grpc.ssl_channel_credentials(open(f"{c}/ca.crt", "rb").read(),
                                     open(f"{c}/root.key", "rb").read(),
                                     open(f"{c}/root.crt", "rb").read())
ch = grpc.secure_channel(sys.argv[3], creds,
                         options=(("grpc.ssl_target_name_override", "prisma-api"),))
h = list(rpc.TelemetryServiceStub(ch).GetHistory(pb.GetHistoryRequest(), timeout=10))
older = [s for s in h if s.at.seconds < int(sys.argv[4])]
print(f"{len(h)} samples, {len(older)} from before this server started")
PY
)
echo "  a new server on the history file: $HAFTER ($HSAVED saved)"
# The confirm window phase 1 left open, read by this new server; then a
# candidate left to expire on its 6 s.
P2=$(python3 lab/api_phase1_check.py /tmp/apistubs $C 127.0.0.1:50552 after /tmp/p558.json 2>&1)
printf '%s\n' "$P2" | sed 's/^/  /'
pkill -f "^sleep 900$"
echo "=========== #558, phase 6: the CGNAT's screens, typed ==========="
# Last, and on a server of its own, so nothing the phases above count is
# moved by these pools. A DYNAMIC pool of SEVENTEEN letters - show bpa top's
# pool column cut at twelve - with a forward and an ALG turned off, and a
# NAT64 pool (lab/nat64.sh's) with FTP64 on and a forward to an IPv6 host.
$HX -c configure -c nat -c cgnat -c "pool residential-cgnat" \
    -c "prefix-inside 100.64.16.0/22" -c "prefix-outside 200.150.0.40/30" \
    -c "mode dynamic" -c "alg ftp on" -c "alg rtsp off" \
    -c "forward tcp 1600 to 100.64.16.5 80" -c exit \
    -c "pool v6" -c "prefix-inside 2001:db8:1200::/48" -c "subscriber-length 56" \
    -c "nat64-prefix 64:ff9b:1::/48" -c "prefix-outside 200.150.0.48/29" \
    -c "block-size 1024" -c "alg ftp on" \
    -c "forward tcp 22600 to 2001:db8:1200:ab00::10 8080" -c exit \
    -c top -c commit 2>&1 | grep -vE "^\s*$" | tail -6 | sed 's/^/  pools: /'
# Five subscribers of it allocate a block each, and the traffic stops before
# anything is read: the screens and the rpcs are then of one box at rest.
for h in 1 2 3 4 5; do
  vppctl packet-generator new "name r6x$h limit 3 node ip4-input interface pg0 size 60-60 \
      data { UDP: 100.64.16.$h -> 8.8.8.8 UDP: 41000 -> 53 incrementing 8 }" >/dev/null 2>&1
  vppctl packet-generator enable-stream r6x$h >/dev/null 2>&1
done
sleep 2
for h in 1 2 3 4 5; do vppctl packet-generator delete r6x$h >/dev/null 2>&1; done
# The VTY's own screens, which the rpcs' text is held to.
S6=/tmp/screens6; rm -rf $S6; mkdir -p $S6
$HX -c "show cgnat translation 200.150.0.16 5000" > $S6/translation-nb 2>&1
$HX -c "show cgnat translation 200.150.0.48 5000" > $S6/translation-v6 2>&1
$HX -c "show cgnat top" > $S6/top 2>&1
$HX -c "show cgnat drops" > $S6/drops 2>&1
$HX -c "show cgnat memory" > $S6/memory 2>&1
$HX -c "show cgnat blocks" > $S6/blocks 2>&1
$HX -c "show cgnat identity" > $S6/identity 2>&1
$HX -c "show cgnat history verify" > $S6/history 2>&1
$HX -c "show cgnat alg" > $S6/alg 2>&1
$HX -c "show cgnat forward" > $S6/forward 2>&1
$HX -c "show cgnat forward v6" > $S6/forward-v6 2>&1
$HX -c "show cgnat ha" > $S6/ha 2>&1
# And what the VTY gives an account with no role, which the API's answer to
# that account is held to.
runuser -u netview -- $HX -c "show cgnat alg" > $S6/alg-netview 2>&1
sed 's/^/  vty: /' $S6/top
sed 's/^/  vty as netview: /' $S6/alg-netview | head -3
sleep 900 | python3 src/prismash/apiserver.py --listen 127.0.0.1:50554 --dir $C/box \
  --stubs /tmp/apistubs --stop-on-eof > /tmp/apiserver6.log 2>&1 &
n=0; until grep -q "prisma.v1 on" /tmp/apiserver6.log || [ $n -ge 30 ]; do sleep 0.5; n=$((n+1)); done
T6=$(python3 lab/api_phase6_check.py /tmp/apistubs $C 127.0.0.1:50554 $S6 2>&1)
printf '%s\n' "$T6" | sed 's/^/  /'
echo "=========== #558, phase 7: the rest of the screens, typed, and the probes ==========="
# OSPF and an IS-IS circuit on d0 - a Linux interface with no dataplane end,
# the management port's shape - and FRR's logging set, all straight into FRR:
# pg0 has no Linux end for either protocol (see phase 3), and `log` is FRR's
# own, which the VTY's `show logging` passes through. The MIB and the
# template carry the enterprise number the box serves under, the product's
# own (67033) when nobody gave it another: a box has no state without one, so
# the refusal for it is the unit suite's (test_opshow.py), not this lab's.
vtysh -c "conf t" -c "router ospf" -c "network 192.0.2.0/24 area 0" -c exit \
  -c "interface d0" -c "ip router isis HEXA" -c exit \
  -c "log syslog warnings" -c "log file /tmp/frr-api7.log informational" \
  2>&1 | sed 's/^/  frr: /'
export PRISMA_SNMP_MIB=/src/appliance/PRISMA-ROUTING-MIB.in
export PRISMA_ZABBIX_TEMPLATE=/src/appliance/prisma-routing-zabbix.json.in
sleep 3
# The VTY's own screens, which the rpcs' text is held to.
S7=/tmp/screens7; rm -rf $S7; mkdir -p $S7
$HX -c "show ospf interface" > $S7/ospf-interface 2>&1
$HX -c "show isis interface" > $S7/isis-interface 2>&1
$HX -c "show hardware" > $S7/hardware 2>&1
$HX -c "show logging" > $S7/logging 2>&1
$HX -c "show snmp mib" > $S7/snmp-mib 2>&1
$HX -c "show snmp zabbix-template" > $S7/snmp-zabbix 2>&1
$HX -c "ping 127.0.0.1 count 3" > $S7/ping 2>&1
runuser -u netview -- $HX -c "show logging" > $S7/logging-netview 2>&1
runuser -u netview -- $HX -c "ping 127.0.0.1 count 1" > $S7/ping-netview 2>&1
sed 's/^/  vty: /' $S7/ospf-interface
sed 's/^/  vty: /' $S7/isis-interface | head -4
sed 's/^/  vty as netview: /' $S7/ping-netview | head -3
sleep 900 | python3 src/prismash/apiserver.py --listen 127.0.0.1:50555 --dir $C/box \
  --stubs /tmp/apistubs --stop-on-eof > /tmp/apiserver7.log 2>&1 &
n=0; until grep -q "prisma.v1 on" /tmp/apiserver7.log || [ $n -ge 30 ]; do sleep 0.5; n=$((n+1)); done
T7=$(python3 lab/api_phase7_check.py /tmp/apistubs $C 127.0.0.1:50555 $S7 reads 2>&1)
printf '%s\n' "$T7" | sed 's/^/  /'
# traceroute and mtr, installed only now: Mtr above was refused for want of
# it, as the VTY refuses `mtr` on a box without the package.
apt-get install -y -qq traceroute mtr-tiny >/dev/null 2>&1
echo "  probes now: $(ls /usr/bin/traceroute /usr/bin/mtr 2>&1 | tr '\n' ' ')"
T7T=$(python3 lab/api_phase7_check.py /tmp/apistubs $C 127.0.0.1:50555 $S7 tools 2>&1)
printf '%s\n' "$T7T" | sed 's/^/  /'
# #559 phase 2: BFD's sessions typed, and their changes streamed. One `peer`
# on d0, a dummy interface nothing answers BFD on: the session is down, a
# state this lab knows. The VTY's screens first, which the rpcs' text is
# held to; api_phase8_check.py then adds and removes a second peer under a
# running WatchBfd.
vtysh -c "conf t" -c "bfd" -c "peer 192.0.2.77 interface d0" -c exit -c exit \
  2>&1 | sed 's/^/  frr: /'
sleep 2
S8=/tmp/screens8; rm -rf $S8; mkdir -p $S8
$HX -c "show bfd peers" > $S8/bfd-peers 2>&1
$HX -c "show bfd peer 192.0.2.77" > $S8/bfd-peer 2>&1
runuser -u netview -- $HX -c "show bfd peers" > $S8/bfd-netview 2>&1
sed 's/^/  vty: /' $S8/bfd-peers
sleep 900 | python3 src/prismash/apiserver.py --listen 127.0.0.1:50556 --dir $C/box \
  --stubs /tmp/apistubs --stop-on-eof > /tmp/apiserver8.log 2>&1 &
n=0; until grep -q "prisma.v1 on" /tmp/apiserver8.log || [ $n -ge 30 ]; do sleep 0.5; n=$((n+1)); done
T8=$(python3 lab/api_phase8_check.py /tmp/apistubs $C 127.0.0.1:50556 $S8 2>&1)
printf '%s\n' "$T8" | sed 's/^/  /'
# #559 phase 3: the `bfd` block as fields, on the same server - a profile, a
# session, a neighbour's and a static route's BFD, each committed and read
# back as the VTY prints it, then removed: a session, a profile, and the
# whole block last (#570). Phase 8's session on d0 goes first, through vtysh
# as it came: d0 is a dummy no dataplane holds, and the commit check of every
# phase 9 commit - which reads the whole configuration - refuses it ("this
# box has no such interface"), measured on this lab's first run. And phase
# 7's IS-IS circuit and OSPF network on d0 with it, for the same reason
# (measured on the second): nothing of d0 is left for a commit to refuse.
vtysh -c "conf t" -c "bfd" -c "no peer 192.0.2.77 interface d0" -c exit \
  -c exit -c "interface d0" -c "no ip router isis HEXA" -c exit \
  -c "router ospf" -c "no network 192.0.2.0/24 area 0" -c exit \
  2>&1 | sed 's/^/  frr: /'
vtysh -c "show running-config" 2>/dev/null | grep -n "d0" | sed 's/^/  left on d0: /'
T9=$(python3 lab/api_phase9_check.py /tmp/apistubs $C 127.0.0.1:50556 2>&1)
printf '%s\n' "$T9" | sed 's/^/  /'
pkill -f "^sleep 900$"
TAIL=$(tail -5 /tmp/apiserver.log)

res() { printf '%s\n' "$OUT" | grep -q "^RESULT $1 ok"; }
echo; echo "=========== VERDICT ==========="
expect_in "the stubs come out of the contract" "generated" "$GEN"
expect_in "the server serves prisma.v1 over mutual TLS" "mutual TLS" "$(cat /tmp/apiserver.log)"
expect "a candidate opens on the running configuration's revision" res open
expect "commit check returns the VTY's refusal of an incomplete pool" res check-refuses
expect "and commit refuses it, FAILED_PRECONDITION with the VTY's words in the trailer" res commit-refused
expect "a line the VTY refuses at entry is INVALID_ARGUMENT" res edit-refused
expect "compare shows the pool the candidate adds" res compare
expect "the completed candidate passes the check" res check-passes
expect "commit confirmed answers its deadline" res commit-confirmed
expect "confirm closes the window, in the VTY's words" res confirm
expect "GetRunning has the pool" res running-has-pool
expect "and so does the prompt's show running-config" res prompt-has-pool
expect "the revisions list the API's commit with its comment" res revisions
expect "a candidate whose base moved is refused with the difference" res moved-base
expect "rollback loads the revision before, and the commit removes the pool" res rollback
expect "GetVersion answers the release and the transport" res version
expect "GetVersion and GetSystem answer the host's name, as the prompt reads it" res hostname
expect "a long Show does not hold GetVersion behind it" res console-apart
expect "ListInterfaces has pg0, up, with its address" res interfaces
expect "ListPools has nb with the dataplane's capacity" res pools
expect "ListPolicies has UP, its term, its text and where it is applied" res policies
expect "GetPolicyCounters counts UP's terms in bgpd's map" res policy-counters
expect "  and a policy that does not exist is NOT_FOUND" res policy-counters-unknown
expect "TestPolicy decides a described route by UP's term, with its set" res test-policy
expect "  and a use it does not know is INVALID_ARGUMENT" res test-policy-refused
expect "Show returns the VTY's screen" res show
expect "and refuses anything that is not a read" res show-refuses-config
expect "the telemetry stream's pg0 counters move with the traffic" res telemetry
expect "an account without the role is refused a candidate, PERMISSION_DENIED" res unprivileged-refused
expect "and still reads the running configuration" res unprivileged-reads
expect "a client with no certificate is refused at the handshake" res no-client-cert
expect "WhoAmI: the certificate's account and the VTY's role for it (#533)" res whoami
expect "GetSystem's memory is the kernel's, in bytes" res memory
expect "speed_bps is the dataplane's link speed, in bits" res speed
expect "a commit's Event carries its CommitResponse's revision and its comment" res event-revision
expect "  and that revision is GetRunning's" res event-revision-running
expect "grpc.health.v1 answers SERVING, and NOT_FOUND for no such service" res health
expect "a worker taking packets reads busy, with vectors per call; one waiting reads idle" res workers
expect "GetHistory streams the samples, oldest first, pg0's counters moving" res history
expect "  since a time, and only what was asked" res history-since
expect "GetRouteSummary counts the table this lab loaded, the VRF beside it too" res route-summary
expect "ListRoutes pages the whole table once, the total known at the end" res routes-all
expect "  its first page comes while the rest is still being read" res routes-first-page
expect "  and no process of the server holds the table" res routes-memory
expect "a route's fields: family, VRF, protocol, selected, its next hop, its table" res route-fields
expect "ListRoutes filters by prefix and protocol" res routes-filter
expect "  and by VRF" res routes-vrf
expect "  and refuses an unknown token, protocol or VRF, each with its status" res routes-refused
expect_in "the history outlives the server: a new one reads it back" \
  "from before this server started" "$HAFTER"
expect "  with samples in it" test "$(printf '%s' "$HAFTER" | sed -n 's/.*samples, \([0-9]*\) from before.*/\1/p')" -gt 10 2>/dev/null
expect_in "GetLogging's stream fields are the screen's, field for field (#555)" \
  "RESULT logging-fields ok" "$LOG1"
expect_in "  and that screen is the VTY's show cgnat logging" "RESULT logging-is-vty ok" "$LOG1"
expect_in "  syslog to a reading collector: connected, carried by the dataplane, delivering" \
  "configured=True connection=connected carrier=dataplane" "$LOG1"
expect "  with lines sent and the health service SERVING" sh -c \
  'printf "%s" "$1" | grep -q "delivering=True" && printf "%s" "$2" | grep -q "HEALTH SERVING"' \
  _ "$LOG1" "$H1"
expect "the collector killed while lines come: not delivering, said why, NOT_SERVING" sh -c \
  'printf "%s" "$1" | grep -q "delivering=False reason=.nothing delivered" && printf "%s" "$2" | grep -q "HEALTH NOT_SERVING"' \
  _ "$LOG2" "$H2"
expect "  and back: delivering again, SERVING" sh -c \
  'printf "%s" "$1" | grep -q "connection=connected.*delivering=True" && printf "%s" "$2" | grep -q "HEALTH SERVING"' \
  _ "$LOG3" "$H3"
expect "the event stream told each turn: lost, stalled, connected, delivering" sh -c \
  'for k in log-disconnected log-stalled log-connected log-delivering; do printf "%s" "$1" | grep -q "EVENT $k syslog" || exit 1; done' \
  _ "$EVENTS"
expect_in "  and the fields of a stall are still the screen's" "RESULT logging-fields ok" "$LOG2"
t2() { printf '%s\n' "$T2" | grep -q "^RESULT $1 ok"; }
expect "#558 phase 2: GetConfig reads the running interfaces and pools, as GetRunning's text says" t2 config-read-running
expect "  SetConfig on an interface: the lines staged, committed, read back typed and in the VTY's text" t2 set-interface-roundtrip
expect "  and set back to nothing: no description, no mtu" t2 set-interface-removes
expect "  SetConfig on a pool: timeouts, alarm and caps, round trip" t2 set-pool-roundtrip
expect "  and removed again" t2 set-pool-removes
expect "  SetConfig on the log export: a UDP collector and enable, round trip" t2 set-logging-roundtrip
expect "  and disabled again" t2 set-logging-disables
expect "  a candidate read typed, and DeleteConfig removes a pool from it" t2 candidate-read-and-delete
expect "  a typed edit the VTY refuses: INVALID_ARGUMENT with its reason, nothing staged" t2 typed-edit-refused
expect "  an unknown field: INVALID_ARGUMENT, USAGE" t2 typed-edit-usage
expect "  GetSchema: every mode's grammar, ranges, forms, sources, versioned" t2 schema
expect "  and, asked, what each dynamic source holds on the box now" t2 schema-values
expect "  ValidateLines: each line accepted or refused with its reason, nothing committed" t2 validate-lines
expect "  an account without the role reads the configuration typed" t2 readonly-reads-typed
t3() { printf '%s\n' "$T3" | grep -q "^RESULT $1 ok"; }
expect "#558 phase 3: GetConfig reads static, BGP, OSPF, OSPF6 and IS-IS, as GetRunning's text says" t3 routing-read
expect "  and what no field takes is kept, verbatim, in its block's other" t3 routing-read-keeps-rest
expect "  an interface's IGP settings, staged, read off the candidate" t3 interface-routing-read
expect "  SetConfig on a static route: router static's line, committed, read back typed and in the VTY's text" t3 set-static-roundtrip
expect "  one field changed, the rest of the route kept" t3 set-static-one-field
expect "  DeleteConfig removes it" t3 delete-static
expect "  a VRF's route is not set here: INVALID_ARGUMENT, USAGE" t3 static-vrf-refused
expect "  SetConfig on a BGP neighbour, with a family: round trip" t3 set-neighbor-roundtrip
expect "  its password is set, and its value is read back nowhere" t3 neighbor-password-never-read
expect "  one field changed, a password not given kept" t3 set-neighbor-one-field
expect "  DeleteConfig removes the neighbour, statement by statement" t3 delete-neighbor
expect "  and its reply masks the password it removed" t3 delete-neighbor-masks-password
expect "  a candidate's routing read typed, apart from the running one" t3 routing-candidate-read
t4() { printf '%s\n' "$T4" | grep -q "^RESULT $1 ok"; }
expect "#558 phase 4: GetConfig reads the policy, the VRF devices, the accounts and the API as GetRunning's text says" t4 phase4-running-read
expect "  a candidate's route-map, FRR lists, attachment, SNMP user, OSPF knob and CGNAT globals, read typed as its text says" t4 phase4-candidate-read
expect "  no SNMP community or passphrase appears in the typed answer" t4 phase4-no-secret
expect "  SetConfig system and vrf_device: staged, read back typed and in the text; DeleteConfig vrfs" t4 phase4-typed-edits
expect "  an SNMP community is never a typed field: INVALID_ARGUMENT" t4 phase4-secret-field-refused
expect "  GetSchema: router bgp read as fields; hostname written as one, a community not" t4 phase4-schema
expect "  GetSchema: every word literal, no choice holding a placeholder" t4 phase4-schema-literal-words
expect "  and every line of GetRunning matches a GetSchema statement, order-free parts included" t4 phase4-schema-covers-running
t5() { printf '%s\n' "$T5C" "$T5W" "$T5S" | grep -q "^RESULT $1 ok"; }
expect "#558 phase 5: Clear pg0's counters: the dataplane's go back to zero" t5 clear-interface-one
expect "  and every interface's" t5 clear-interface-all
expect "  a CGNAT subscriber's sessions ended, its block left to the hold-down" t5 clear-subscriber
t5both() { t5 clear-bgp && t5 clear-ospf; }
expect "  a BGP neighbour soft out, and OSPF's process, as the VTY's clear" t5both
expect "  an account without the role: PERMISSION_DENIED, REASON_PERMISSION" t5 clear-permission
expect "  a name that is two words: INVALID_ARGUMENT, REASON_USAGE" t5 clear-usage
expect "  an address in no pool: the dataplane's refusal, FAILED_PRECONDITION" t5 clear-usage-dataplane
expect "WatchLogging: the same block events the VTY's monitor printed, kind and subscriber" \
  sh -c 'test -n "$0" && test "$0" = "$1"' "$API_EV" "$VTY_EV"
expect "  the stream ends when its seconds ran out" t5 watch-ended
expect "  a filter the monitor does not take: INVALID_ARGUMENT" t5 watch-bad-filter
t5noread() { t5 watch-unreadable && printf '%s' "$VTY_NOREAD" | grep -q "may not read /dev/shm/prisma-bpa-log"; }
expect "  an account that may not read the segment: refused, as the VTY's monitor" t5noread
expect "SystemService.Reboot with the configuration unsaved: REASON_UNSAVED, nothing asked" t5 system-reboot-unsaved
expect "  by an account without the role: PERMISSION_DENIED" t5 system-reboot-permission
expect "  with force: the VTY's reboot, systemctl reboot asked" t5 system-reboot-force
expect "  Shutdown with force: systemctl poweroff asked" t5 system-shutdown-force
expect "RestartDataplane: run on the host detached, its output when it finished" t5 system-dataplane-restart
expect "Upgrade streams STARTED, DOWNLOAD, VERIFY, INSTALL, INSTALLED, API_RESTART; GetUpgradeStatus reads it back" t5 system-upgrade-stream
expect "  the last message says how long the API is away, and ends the stream" t5 system-upgrade-away
# #564: the system actions above went through the real doors daemon, not
# around it - a door that refused the API's argv turns these red.
expect "the system actions went through the host's door: detached runs started there" \
  sh -c 'test "${0:-0}" -ge 2' "$DOORS_RUNS"
expect "  and reboot/poweroff too, and no systemd-run the door refused" \
  sh -c 'test "${0:-0}" -ge 2 && test "${1:-1}" -eq 0' "$DOORS_BOX" "$DOORS_REFUSED"
expect "  a version that is not a release: INVALID_ARGUMENT" t5 system-upgrade-bad-version
expect "  an upgrade still running: REASON_BUSY" t5 system-upgrade-busy
expect "  by an account without the role: PERMISSION_DENIED" t5 system-upgrade-permission
expect "#563: an account in prisma-operator says can_operate in WhoAmI, one without it does not" t5 operator-whoami
expect "  it reboots with no role at all: the stand-in systemctl reboot asked" t5 operator-reboot
expect "  the upgrade CHECK (no version) is every account's: not refused for permission" t5 upgrade-check-anyone
expect "  the upgrade itself is not: PERMISSION for an account without operator" t5 upgrade-permission
p1() { printf '%s\n' "$P1" "$P2" | grep -q "^RESULT $1 ok"; }
expect "#558: a line the prompt does not take refuses with reason GRAMMAR" p1 reason-grammar
expect "  a pool without its prefixes: VALIDATION, from the commit and the check" p1 reason-validation
expect "  a 256-character identity path: LIMIT, its field, 255 and 256" p1 reason-limit
expect "  a base another commit moved: STALE_BASE, with GetRunning's revision" p1 reason-stale-base
expect "  an account without the role: PERMISSION" p1 reason-permission
expect "  a candidate never opened: NOT_FOUND, CANDIDATE_UNKNOWN" p1 reason-unknown
expect "  a pool change that re-maps subscribers: GATE_REMAP, with what it is about" p1 reason-gate-remap
expect "ListCandidates: the candidate, its staged lines, its expiry; listing does not use it" p1 candidates-listed
expect "GetPendingConfirm: the window a commit confirmed opened, its deadline, who and why" p1 pending-confirm
expect "  read by an account without the role as well" p1 pending-confirm-any-account
expect "  and by a server started afterwards: the box's state, not the server's" p1 pending-confirm-restart
expect "  Confirm closes it" p1 pending-confirm-closed
expect "a candidate unused past the idle time: NOT_FOUND, CANDIDATE_EXPIRED, and not listed" p1 candidate-expired
t6() { printf '%s\n' "$T6" | grep -q "^RESULT $1 ok"; }
expect "#558 phase 6: LookupTranslation on a deterministic pool: the formula, for any instant, the VTY's screen" t6 translation-formula
expect "  on a NAT64 pool: the subscriber's /56, inside the pool's prefix" t6 translation-nat64
expect "GetSubscriber reads a subscriber's blocks off the plugin's screen" t6 subscriber-blocks
expect "  a dynamic block GetSubscriber names, looked up back to its holder, for now only" t6 translation-dynamic
expect "  an address no pool owns: NOT_FOUND, in the dataplane's words" t6 translation-not-found
expect "  an address that is not one: INVALID_ARGUMENT, before the VTY" t6 translation-refused
expect "ListTop: the screen, every subscriber holding a block" t6 top
expect "  a seventeen-letter pool read whole, not cut at show bpa top's twelve" t6 top-long-pool
expect "  count limits the rows, not the total" t6 top-count
expect "GetDrops: both nodes, packets translated counted and not a drop, the screen" t6 drops
expect "GetCgnatMemory: the heap and the sessions the screen prints, in bytes and counts" t6 memory
expect "ListBlocks: the dynamic pool's blocks, per worker summing to its total, the screen" t6 blocks
expect "GetIdentity: no table, said as one, the screen" t6 identity
expect "VerifyHistory: the verdict the screen gives, with its head digest" t6 history
expect "ListAlgs: each pool's ALGs as configured, the NAT64 pool's FTP64 on" t6 algs
expect "ListForwards: the IPv4 forward and the NAT64 pool's to an IPv6 host, the screen" t6 forwards
expect "  and one pool's alone" t6 forwards-pool
expect "GetHaStatus: this box read, no peer copy said so, and no agreement claimed" t6 ha
expect "  an account with no role gets what the VTY gives it, screen or refusal" t6 netview-reads
TAIL6=$(tail -5 /tmp/apiserver6.log)
expect_not_in "  and that server raised nothing" "Traceback" "$TAIL6"
t7() { printf '%s\n' "$T7" "$T7T" | grep -q "^RESULT $1 ok"; }
expect "#558 phase 7: ListOspfInterfaces: d0 in area 0, its MTU, no dataplane MTU for it, the screen" t7 ospf-interfaces
expect "ListIsisInterfaces: d0's circuit in HEXA, the punt read, the screen" t7 isis-interfaces
expect "GetHardware: what the dataplane holds, pg0 and not local0, the screen" t7 hardware
expect "GetRoutingLogging: zebra's syslog level and log file as set in FRR, every daemon, the screen" t7 routing-logging
expect "GetSnmpFile: the MIB with the box's enterprise number in it, exactly the screen" t7 snmp-mib
expect "  the Zabbix template, JSON, exactly the screen" t7 snmp-zabbix
expect "  no kind: INVALID_ARGUMENT" t7 snmp-kind-refused
expect "Ping streams each reply, then the summary, as many lines as the VTY's ping" t7 ping
expect "  the replies arrive as ping prints them, not at the end" t7 ping-streams
expect "  a host that does not answer: 100% loss, no round trip, a non-zero status" t7 ping-no-reply
expect "  a host, count or source the VTY refuses: INVALID_ARGUMENT, in its words" t7 probe-refused
expect "  a tool the box lacks: FAILED_PRECONDITION, naming the package" t7 probe-absent
expect "  cancelled: the box's ping stops, and the next probe answers" t7 probe-cancel
expect "  a probe does not hold GetVersion behind it" t7 probe-apart
expect "  an account with no role gets the probe and the screen the VTY gives it" t7 netview-probes
expect "Traceroute streams its hops, 127.0.0.1 at hop 1" t7 traceroute
expect "Mtr streams its report's hops, sent and loss read" t7 mtr
TAIL7=$(tail -5 /tmp/apiserver7.log)
expect_not_in "  and that server raised nothing" "Traceback" "$TAIL7"
t8() { printf '%s\n' "$T8" | grep -q "^RESULT $1 ok"; }
expect "#559 phase 2: ListBfdSessions: the peer on d0, down, configured, its timers, the screen" t8 sessions
expect "  one peer by address, the screen of show bfd peer; another, none" t8 one-peer
expect "  a peer that is not an address: INVALID_ARGUMENT" t8 peer-refused
expect "  an account with no role gets the screen the VTY gives it" t8 netview-reads
expect "WatchBfd: the session as it is when the call starts" t8 watch-initial
expect "  a peer added under it arrives as APPEARED" t8 watch-appeared
expect "  and removed, as GONE, with the state it had" t8 watch-gone
expect "  an interval outside 1-60: INVALID_ARGUMENT" t8 interval-refused
t9() { printf '%s\n' "$T9" | grep -q "^RESULT $1 ok"; }
expect "#559 phase 3: SetConfig bfd_profile: committed, GetConfig and the VTY read its timers" t9 set-profile
expect "  SetConfig bfd_peer: its profile and a timer, read back by key" t9 set-peer
expect "  one timer named: only that line moves, the profile kept" t9 set-peer-one-field
expect "  a BGP neighbour's bfd profile is a field, written where FRR reads it" t9 neighbor-bfd-profile
expect "  and the neighbour, BFD and all, is removed" t9 neighbor-with-bfd-deleted
expect "  a static route's BFD as fields, after its tag" t9 static-bfd
expect "  and the route removed" t9 static-bfd-deleted
expect "  DeleteConfig a session by its key" t9 delete-peer
expect "  DeleteConfig a profile by its name" t9 delete-profile
expect "  DeleteConfig the whole block, body and all, the static routes kept (#570)" t9 delete-block
expect "  a session that is not there: INVALID_ARGUMENT" t9 delete-unknown-refused
expect "  GetSchema knows the bfd peer mode, its statements typed" t9 schema-bfd
TAIL8=$(tail -5 /tmp/apiserver8.log)
expect_not_in "  and that server raised nothing" "Traceback" "$TAIL8"
expect_not_in "the server raised nothing" "Traceback" "$TAIL"
expect "a second server on the same port is refused at the bind ($SECOND)" \
  test "$SECOND" = 1
expect_in "and says so" "cannot listen on 127.0.0.1:50551" "$(cat /tmp/apiserver2.log)"
expect "closing the server's stdin stops it, as the host unit's stop does" \
  test "$STOPPED" = "stopped"
expect_in "and it says why" "stdin closed, stopping" "$(cat /tmp/apiserver.log)"
expect_in "enabling the API with no address is refused at the check" \
  "api enabled yes needs 'api listen-address" "$APINO"
expect_in "with one, the commit writes api.env for the host" "API_LISTEN=127.0.0.1" "$APIENV"
expect_in "and the running configuration reads it back" "api listen-address 127.0.0.1" "$APIRUN"
expect_in "a box with no service manager says when it applies" "next boot" "$APIOK"
verdict
