#!/bin/sh
# OFF-RED-AT-LEAST: 48
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
echo "=========== the VTY's own statement ==========="
APINO=$($HX -c configure -c "api enabled yes" -c "commit check" 2>&1)
printf '%s\n' "$APINO" | tail -2 | sed 's/^/  /'
APIOK=$($HX -c configure -c "api enabled yes" -c "api listen-address 127.0.0.1" \
            -c "api port 50551" -c commit 2>&1)
printf '%s\n' "$APIOK" | tail -2 | sed 's/^/  /'
APIENV=$(cat /etc/prisma-routing/api.env 2>&1)
APIRUN=$($HX -c "show running-config" 2>&1 | grep '^api ')
printf '%s\n' "$APIRUN" | sed 's/^/  /'
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
sleep 900 | python3 src/prismash/apiserver.py --listen 127.0.0.1:50552 --dir $C/box \
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
