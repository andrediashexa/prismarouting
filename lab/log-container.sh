#!/bin/sh
# Log export costs the dataplane no packet: the TCP streams in the log
# container (#550).
#
#   sh tools/lab.sh log-container        -> LAB PASSED
#   sh tools/lab.sh log-container off    -> LAB FAILED (VPP carries them)
#
# OFF-RED-AT-LEAST: 5
#
# THE CASE THAT STILL FAILED after #547, measured on the legacy bench on
# 0.149.8: a 30k pps burst through the box lost 11.7% at rx while a 573 MB
# syslog spill was replayed to a collector, and 27% with a monitoring poll
# (vppctl and prismash every 3 s) running at the same time - the replay ran on
# VPP's main thread. The maintainer's rule: log export must never affect
# forwarding or translation.
#
# So subscriber traffic goes into the dataplane at a fixed rate through an
# af_packet interface - a real receive ring that overflows when nobody polls
# it - and what the dataplane RECEIVED is compared with what was sent, first
# with no logging at all (the lab's own loss, which must be none), then while
# a 240 MB spill is replayed to a collector that reads, with and without a
# monitoring poll, then with a collector that accepts and never reads, and
# with none at all. In this lab VPP has no workers: its main thread is the
# one that polls the interface, which is the worst case for anything that
# runs on it.
#
# With the log container (prisma-logd, compiled from this tree) running, VPP
# only copies events into the export ring and the container does the rest:
# the dataplane must receive everything in every phase. MODE=off runs no
# container, so VPP carries the streams as before #550 and the replay phases
# lose packets - which is what this lab exists to catch.
MODE=${1:-on}
. /src/lab/lib.sh
. /src/lab/verdict.sh
set -u
HX="python3 /src/src/prismash/prismash.py"
PPS=${LAB_PPS:-20000}
WIN=${LAB_WINDOW:-20}

mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api

# The collector's side, as lab/syslog-stall-heap.sh has it: reached through a
# linux_cp pair, the way a box reaches its collector.
ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up
ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out
# The subscribers' side: a second veth into an af_packet interface.
ip link add veth-sub type veth peer name veth-subv 2>/dev/null
ip link set veth-subv up
ip netns add subs 2>/dev/null
ip link set veth-sub netns subs
ip netns exec subs ip link set lo up
ip netns exec subs ip link set veth-sub up
ip netns exec subs ip addr add 100.64.3.1/22 dev veth-sub
i=10; while [ $i -lt 30 ]; do
  ip netns exec subs ip addr add 100.64.0.$i/22 dev veth-sub; i=$((i+1)); done
ip netns exec subs ip route add default via 100.64.3.254

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

vppctl create host-interface name veth-vpp >/dev/null
vppctl set interface state host-veth-vpp up
$HX -c "configure" -c "interface host-veth-vpp" -c "linux-name vppmgmt0" \
    -c "exit" -c "commit" 2>&1 | tail -1 | sed 's/^/  pair: /'
vppctl set interface mtu 1500 host-veth-vpp
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
vppctl set ip neighbor host-veth-vpp 10.0.0.2 $(ip netns exec peer cat /sys/class/net/veth-out/address)

vppctl create host-interface name veth-subv >/dev/null
vppctl set interface state host-veth-subv up
vppctl set interface ip address host-veth-subv 100.64.3.254/22
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg1 up
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl bpa pool din inside 100.64.0.0/22 outside 200.150.0.64/26 block 2048 mode dynamic
vppctl set interface bpa in2out host-veth-subv
vppctl set interface bpa out2in pg1

rx() { vppctl show interface host-veth-subv | tr -d '\r' | awk '/rx packets/{print $NF}'; }
heap_mb() {
  vppctl show memory main-heap 2>&1 | tr -d '\r' | grep -o 'used: [0-9.]*[KMG]' | head -1 \
    | awk '{v=$2; u=substr(v,length(v)); n=substr(v,1,length(v)-1);
            if (u=="G") n*=1024; else if (u=="K") n/=1024; printf "%d\n", n}'
}
carrier() { vppctl show bpa logging | tr -d '\r' | grep -m1 "TCP streams carried by"; }
POLL=
poll_start() {  # the monitoring poll the bench ran: vppctl and prismash every 3 s
  ( while :; do vppctl show interface >/dev/null 2>&1
                $HX -c "show cgnat logging" >/dev/null 2>&1; sleep 3; done ) &
  POLL=$!
}
poll_stop() { [ -n "$POLL" ] && kill $POLL 2>/dev/null; wait $POLL 2>/dev/null; POLL=; }
window() {  # window <name>: traffic for $WIN s; prints and stores the loss
  r0=$(rx)
  ip netns exec subs python3 /src/lab/log_sender.py 8.8.8.8 $PPS $WIN 100.64.0.10 20 /tmp/sent.$1
  sleep 1
  r1=$(rx)
  sent=$(cat /tmp/sent.$1)
  got=$(( r1 - r0 ))
  lost=$(( sent - got )); [ $lost -lt 0 ] && lost=0
  echo "$1 $sent $got $lost" >> /tmp/windows
  echo "  $1: sent $sent, the dataplane received $got, lost $lost"
}
: > /tmp/windows

echo "=== the subscribers' sessions, made before anything is measured ==="
ip netns exec subs python3 /src/lab/log_sender.py 8.8.8.8 2000 2 100.64.0.10 20 /tmp/sent.warm
vppctl show bpa session 2>&1 | tr -d '\r' | tail -1 | sed 's/^/  /'

echo "=== baseline: no logging at all ==="
window base
H0=$(heap_mb)

SPILL=/var/log/prisma-routing/syslog-spill.bin
plant() {  # 240 MB of whole lines: under the spill's 256 MB ceiling, so a
           # record written behind them is kept, not refused
rm -f "$SPILL" "$SPILL.off"
python3 - <<'PY'
n = 0
with open("/var/log/prisma-routing/syslog-spill.bin", "wb") as f:
    chunk, size = [], 0
    while size < 240 * 1024 * 1024:
        m = (b"<134>1 2026-09-28T18:20:00.000Z prisma-bench prisma-cgnat - "
             b"BLOCK-ALLOC [cgnat@32473 subscriber=\"100.64.0.1\" public=\"203.0.113.1\" "
             b"portStart=\"1024\" portEnd=\"3071\"] planted-%08d" % n)
        rec = b"%d " % len(m) + m
        chunk.append(rec); size += len(rec); n += 1
        if len(chunk) == 10000:
            f.write(b"".join(chunk)); chunk = []
    f.write(b"".join(chunk))
PY
echo "  planted: $(stat -c %s $SPILL) bytes"
}
echo "=== a 240 MB syslog spill, as a long collector outage leaves one ==="
plant

LOGD=
if [ "$MODE" != off ]; then
  echo "=== the log container's process, from this tree ==="
  cc -O2 -Wall -Wextra -Werror -DBPA_STANDALONE -I /src/test -I /src/src/plugins/bpa \
     -o /tmp/prisma-logd /src/src/logger/prisma_logd.c \
     /src/src/plugins/bpa/bpa_log_syslog.c /src/src/plugins/bpa/bpa_identity.c -lrt
  /tmp/prisma-logd > /tmp/logd.log 2>&1 &
  LOGD=$!
  sleep 2
else
  echo "=== off: no log container, VPP carries the streams as before #550 ==="
fi
echo "  $(carrier)"
CARRIER_UP=$(carrier)

# A collector that reads everything and keeps nothing, counting whole frames.
sink() {  # sink <seconds> <out>
  ip netns exec peer python3 -c '
import socket, sys, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("10.0.0.2", 601)); s.listen(4); s.settimeout(float(sys.argv[1]))
total = 0; end = time.time() + float(sys.argv[1])
try:
    c, _ = s.accept(); c.settimeout(1)
    while time.time() < end:
        try:
            d = c.recv(1 << 20)
        except socket.timeout:
            continue
        if not d: break
        total += len(d)
except socket.timeout:
    pass
open(sys.argv[2], "w").write("%d\n" % total)
' "$1" "$2" &
}

echo "=== the spill replayed to a collector that reads, with a monitoring poll ==="
sink $((WIN + 8)) /tmp/got.replay_poll
SINK=$!
sleep 1
$HX -c "configure" -c "nat" -c "cgnat" \
    -c "logging syslog 10.0.0.2 enterprise 44444 port 601" -c "commit" >/dev/null 2>&1
sleep 2
poll_start
window replay_poll
poll_stop
wait $SINK 2>/dev/null
echo "  the collector took $(( $(cat /tmp/got.replay_poll) / 1048576 )) MB meanwhile"
HM1=$(heap_mb)

echo "=== and without the poll, from another 240 MB spill ==="
$HX -c "configure" -c "nat" -c "cgnat" -c "logging syslog disable" -c "commit" >/dev/null 2>&1
sleep 1
plant
sink $((WIN + 8)) /tmp/got.replay
SINK=$!
sleep 1
$HX -c "configure" -c "nat" -c "cgnat" \
    -c "logging syslog 10.0.0.2 enterprise 44444 port 601" -c "commit" >/dev/null 2>&1
sleep 2
window replay
wait $SINK 2>/dev/null
echo "  the collector took $(( $(cat /tmp/got.replay) / 1048576 )) MB meanwhile"
HM2=$(heap_mb)

echo "=== a collector that accepts and never reads ==="
$HX -c "configure" -c "nat" -c "cgnat" -c "logging syslog disable" -c "commit" >/dev/null 2>&1
ip netns exec peer python3 -c '
import socket, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("10.0.0.2", 601)); s.listen(4); c, _ = s.accept(); time.sleep(60)
' &
STALL=$!
sleep 1
$HX -c "configure" -c "nat" -c "cgnat" \
    -c "logging syslog 10.0.0.2 enterprise 44444 port 601" -c "commit" >/dev/null 2>&1
sleep 2
window stalled
kill $STALL 2>/dev/null; wait $STALL 2>/dev/null

echo "=== no collector at all ==="
sleep 2
window absent
HM3=$(heap_mb)
LOGGING=$(vppctl show bpa logging | tr -d '\r' | sed -n '/TCP streams carried/p;/syslog transport/,$p')
printf '%s\n' "$LOGGING" | sed 's/^/  | /'

TAKEN_BACK=
if [ -n "$LOGD" ]; then
  echo "=== the log container stops: VPP carries the streams again ==="
  kill -TERM $LOGD; wait $LOGD 2>/dev/null
  sleep 2
  TAKEN_BACK=$(carrier)
  echo "  $TAKEN_BACK"
  tail -2 /tmp/logd.log | sed 's/^/  logd: /'
fi

loss() { awk -v w="$1" '$1 == w { print $4 }' /tmp/windows; }
sent_of() { awk -v w="$1" '$1 == w { print $2 }' /tmp/windows; }
# The tolerance: one packet in a thousand, and never more than the baseline's
# own loss plus that - the lab's host is shared, and the baseline says what it
# costs without any logging.
tol() { echo $(( $(sent_of "$1") / 1000 + $(loss base) )); }

echo
echo "=========== VERDICT ==========="
expect_setup "the baseline, with no logging, lost nothing: $(loss base) of $(sent_of base)" \
  test "$(loss base)" -le $(( $(sent_of base) / 1000 ))
expect_in "the TCP streams were carried by the log container" \
  "carried by the log container" "$CARRIER_UP"
expect "a 240 MB spill replayed with a monitoring poll cost no packet: lost $(loss replay_poll) of $(sent_of replay_poll) (bench, before: 27%)" \
  test "$(loss replay_poll)" -le "$(tol replay_poll)"
expect "replayed without the poll, no packet either: lost $(loss replay) of $(sent_of replay) (bench, before: 11.7%)" \
  test "$(loss replay)" -le "$(tol replay)"
expect "and both replays really ran meanwhile: the collector took $(( $(cat /tmp/got.replay_poll) / 1048576 )) and $(( $(cat /tmp/got.replay) / 1048576 )) MB" \
  test $(cat /tmp/got.replay_poll) -gt $(( 20 * 1048576 )) -a $(cat /tmp/got.replay) -gt $(( 20 * 1048576 ))
expect_in "and no record was refused by the spill's ceiling" \
  "0 dropped over it" "$LOGGING"
expect "a collector that never reads cost no packet: lost $(loss stalled) of $(sent_of stalled)" \
  test "$(loss stalled)" -le "$(tol stalled)"
expect "no collector at all cost no packet: lost $(loss absent) of $(sent_of absent)" \
  test "$(loss absent)" -le "$(tol absent)"
expect "the dataplane's heap stayed flat: $H0 -> $HM1 -> $HM2 -> $HM3 MB" \
  test $(( HM3 - H0 )) -lt 16 -a $(( HM1 - H0 )) -lt 16
expect_in "stopped, the log container gave the streams back to the dataplane" \
  "carried by the dataplane" "${TAKEN_BACK:-<no container ran>}"
verdict
