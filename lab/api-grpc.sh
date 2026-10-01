#!/bin/sh
# OFF-RED-AT-LEAST: 20
# The management API over gRPC (#532), the way the web interface's backend
# will speak it: mutual TLS, a client certificate whose Common Name is the box
# account the call acts as, the VTY's own model underneath.
#
# Drives the full configuration cycle - open a candidate, edit it, the VTY's
# refusal at `commit check` and at `commit`, a line the VTY refuses at entry,
# compare, `commit confirmed`, confirm, rollback - and reads the result back
# through `prismash -c "show running-config"`, so the API and the prompt are
# held to one configuration. A candidate whose base the running configuration
# moved past is refused with the difference. The typed reads, a Show, a
# telemetry stream whose pg0 counters move with traffic, an account without
# the role and a client with no certificate are each asserted.
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

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp
vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface state pg0 up
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
OUT=$(python3 lab/api_grpc_check.py /tmp/apistubs $C 127.0.0.1:50551 2>&1)
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
pkill -f "^sleep 900$"
n=0; while pgrep -f "apiserver.py --listen 127.0.0.1:50551" >/dev/null && [ $n -lt 30 ]; do sleep 0.5; n=$((n+1)); done
STOPPED=$(pgrep -f "apiserver.py --listen 127.0.0.1:50551" >/dev/null && echo running || echo stopped)
echo "  after its stdin closed: $STOPPED"
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
expect "ListInterfaces has pg0, up, with its address" res interfaces
expect "ListPools has nb with the dataplane's capacity" res pools
expect "Show returns the VTY's screen" res show
expect "and refuses anything that is not a read" res show-refuses-config
expect "the telemetry stream's pg0 counters move with the traffic" res telemetry
expect "an account without the role is refused a candidate, PERMISSION_DENIED" res unprivileged-refused
expect "and still reads the running configuration" res unprivileged-reads
expect "a client with no certificate is refused at the handshake" res no-client-cert
expect_not_in "the server raised nothing" "Traceback" "$TAIL"
expect "closing the server's stdin stops it, as the host unit's stop does" \
  test "$STOPPED" = "stopped"
expect_in "and it says why" "stdin closed, stopping" "$(cat /tmp/apiserver.log)"
expect_in "enabling the API with no address is refused at the check" \
  "api enabled yes needs 'api listen-address" "$APINO"
expect_in "with one, the commit writes api.env for the host" "API_LISTEN=127.0.0.1" "$APIENV"
expect_in "and the running configuration reads it back" "api listen-address 127.0.0.1" "$APIRUN"
expect_in "a box with no service manager says when it applies" "next boot" "$APIOK"
verdict
