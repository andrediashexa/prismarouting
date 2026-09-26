#!/bin/sh
# OFF-RED-AT-LEAST: 3
# RX workers and CGNAT workers (#466): `bpa { cgnat-workers <list> }` in
# startup.conf makes only those workers own subscribers and translate; the
# others take packets in and hand them over.
#
# Three workers here, worker 0 receiving and workers 1-2 translating. Every
# packet is injected on worker 0, so what is measured is where the
# translation then runs: on threads 2 and 3 and never on thread 1, with the
# dynamic pool sharded into two slices instead of three.
#
# MODE=off starts the same dataplane without the stanza - every worker does
# both, as every box has - and worker 0 then translates what it owns.
MODE=${1:-on}
set -u
. /src/lab/lib.sh   # startup_conf, use_binary_api, wait_vpp, vc
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /etc/prisma-routing /var/log/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api

CONF=$(LAB_WORKERS=3 startup_conf test)
cp "$CONF" /tmp/startup-split.conf
[ "$MODE" = off ] || printf '\nbpa {\n  cgnat-workers 1-2\n}\n' >> /tmp/startup-split.conf
vpp -c /tmp/startup-split.conf > /tmp/vpp.log 2>&1 &
wait_vpp /tmp/vpp.log || exit 1

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up
vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl set ip neighbor pg0 100.64.3.253 02:00:00:00:00:01
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0

vppctl bpa pool din inside 100.64.0.0/22 outside 200.150.0.16/30 block 4096 mode dynamic
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1

echo "=========== the workers ($MODE) ==========="
WORKERS=$(vc show bpa workers)
echo "$WORKERS" | sed 's/^/  /'
echo "  threads:"; vc show threads | sed 's/^/    /'

echo; echo "=========== 24 subscribers, every packet in on worker 0 ==========="
vppctl clear runtime >/dev/null
for sub in $(seq 1 24); do
  vppctl packet-generator new "name s$sub limit 4 node ip4-input interface pg0 \
      worker 0 size 60-60 data { UDP: 100.64.0.$sub -> 8.8.8.8 \
      UDP: 40000 -> 53 incrementing 8 }" >/dev/null
done
vppctl packet-generator enable >/dev/null
sleep 2
RUN=$(vc show runtime)
# The bpa-in2out row of each thread: its name first, then Calls, Vectors.
per_thread() {  # node -> "thread vectors" lines
  echo "$RUN" | awk -v node="$1" '
    /^Thread [0-9]+/ { t = $2 }
    $1 == node { print t, $3 }'
}
IN=$(per_thread bpa-in2out)
HO=$(per_thread bpa-in2out-handoff)
echo "  bpa-in2out vectors by thread:"; echo "$IN" | sed 's/^/    /'
echo "  bpa-in2out-handoff vectors by thread:"; echo "$HO" | sed 's/^/    /'
T1=$(echo "$IN" | awk '$1 == 1 {print $2}')
T23=$(echo "$IN" | awk '$1 == 2 || $1 == 3 {s += $2} END {print s + 0}')
SESS=$(vc show bpa session | grep -o "total: [0-9]*" | awk '{print $2}')
SLICES=$(vc show bpa blocks | sed -n '/^pool din /,/^  total:/p' | grep -c '^  thread ')
echo "  sessions: ${SESS:-0}   slices of pool din: ${SLICES:-0}"

echo; echo "=========== VERDICT ==========="
expect_in "the dataplane took the split from startup.conf" \
  "2 of 3 worker(s) translate" "$WORKERS"
expect "worker 0, which receives, translated nothing" \
  test "${T1:-0}" = 0
expect "  and the translating workers did the translating" \
  test "${T23:-0}" -gt 0
expect "every subscriber got its sessions" test "${SESS:-0}" -ge 24
expect "the dynamic pool is sharded over the two translating workers" \
  test "${SLICES:-0}" = 2
verdict
