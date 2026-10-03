#!/bin/sh
# OFF-RED-AT-LEAST: 69
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
pgrep zebra >/dev/null || start_frr bgpd
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
  vppctl packet-generator enable >/dev/null 2>&1; sleep 1
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
expect "  GetSchema: each area's grammar, ranges and forms, what <name> may be" t2 schema
expect "  ValidateLines: each line accepted or refused with its reason, nothing committed" t2 validate-lines
expect "  an account without the role reads the configuration typed" t2 readonly-reads-typed
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
