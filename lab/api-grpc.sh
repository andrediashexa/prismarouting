#!/bin/sh
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
ls $C/box | sed 's/^/  /'

echo "=========== the stubs, generated from the contract ==========="
GEN=$(sh tools/gen-api.sh /tmp/apistubs 2>&1 && echo generated)
echo "  $GEN"

echo "=========== the server ==========="
python3 src/prismash/apiserver.py --listen 127.0.0.1:50551 --dir $C/box \
  --stubs /tmp/apistubs > /tmp/apiserver.log 2>&1 &
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
verdict
