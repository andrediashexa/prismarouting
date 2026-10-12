#!/bin/sh
# Block events reach IPFIX/TCP and syslog collectors through the log container (#606).
#
#   sh tools/lab.sh log-container-streams        -> LAB PASSED
#   sh tools/lab.sh log-container-streams off    -> LAB FAILED (no container)
#   LAB_WORKERS=1 sh tools/lab.sh log-container-streams   with two workers
#
# OFF-RED-AT-LEAST: 6
#
# WHY THIS LAB. #606 found lab/ipfix.sh red for every release since 0.150.0
# on the rings' segment, and the question that mattered was whether a box's
# log container could read it - because the container (prisma-logd, #550) is
# what carries the TCP streams, the retention trail, on every appliance. It
# could: the lab's own reader was stale. But the lab that would have said so
# did not exist. lab/log-container.sh measures packet loss under a syslog
# spill replay and UDP handovers, lab/nat64.sh proves syslog through the
# container for NAT64 subscribers; neither had an IPv4 subscriber's block
# events cross the IPFIX/TCP stream through the container, nor read the
# segment from outside with the worker count a box has.
#
# So: VPP from this tree with no workers or with LAB_WORKERS, prisma-logd
# compiled from this tree, both TCP streams configured through the VTY to
# collectors behind a linux_cp pair (the way a box reaches its collector),
# twelve subscribers each given a block and each block released by the
# hold-down. Every allocation and every release must reach both collectors,
# named by subscriber, with the container carrying them; and the segment the
# container read must be readable from outside, one ring per vlib main, its
# layout adding up to its size.
#
# OFF runs no container: VPP carries the streams itself, and every check
# that says "through the log container" must go red.
MODE=${1:-on}
. /src/lab/lib.sh
. /src/lab/verdict.sh
set -u
HX="python3 /src/src/prismash/prismash.py"
N=12

mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api

ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up
ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
wait_vpp
WORKERS=$(box_workers)
echo "  workers: $WORKERS"

vppctl create host-interface name veth-vpp >/dev/null
vppctl set interface state host-veth-vpp up
$HX -c "configure" -c "interface host-veth-vpp" -c "linux-name vppmgmt0" \
    -c "exit" -c "commit" 2>&1 | tail -1 | sed 's/^/  pair: /'
vppctl set interface mtu 1500 host-veth-vpp
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
vppctl set ip neighbor host-veth-vpp 10.0.0.2 $(ip netns exec peer cat /sys/class/net/veth-out/address)

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up
vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl bpa pool din inside 100.64.0.0/22 outside 200.150.0.16/30 block 2048 mode dynamic
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa block-holddown 3
vppctl bpa timeouts udp 2

carrier() { vppctl show bpa logging | tr -d '\r' | grep -m1 "TCP streams carried by"; }

# The collectors listen FIRST: one that joins late cannot tell a box that
# sent early from itself (lab/ipfix.sh, about templates).
ip netns exec peer python3 /src/tools/ipfix_collector.py tcp 4740 45 \
  > /tmp/ipfix-tcp.out 2>&1 &
COL_IPFIX=$!
ip netns exec peer python3 /src/tools/syslog_collector.py 601 45 \
  /tmp/syslog.rep /tmp/syslog.lines > /tmp/syslog.out 2>&1 &
COL_SYSLOG=$!
sleep 2

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
  echo "=== off: no log container, the dataplane carries the streams ==="
fi

echo "=== both TCP streams, through the VTY ==="
$HX -c "configure" -c "nat" -c "cgnat" \
    -c "logging tcp 10.0.0.2 port 4740" \
    -c "logging syslog 10.0.0.2 enterprise 44444 port 601" \
    -c "commit" 2>&1 | tail -1 | sed 's/^/  /'
sleep 3
CARRIER=$(carrier)
echo "  $CARRIER"

echo "=== $N subscribers, one block each ==="
i=10; while [ $i -lt $((10 + N)) ]; do
  vppctl packet-generator new "name s$i limit 1 node ip4-input interface pg0 size 60-60 \
      data { UDP: 100.64.0.$i -> 8.8.8.8 UDP: 40000 -> 53 incrementing 8 }" >/dev/null
  i=$((i+1)); done
vppctl packet-generator enable >/dev/null
sleep 1
i=10; while [ $i -lt $((10 + N)) ]; do
  vppctl packet-generator delete s$i >/dev/null 2>&1; i=$((i+1)); done
vppctl show bpa session 2>&1 | tr -d '\r' | tail -1 | sed 's/^/  /'

echo "=== the segment, read from outside VPP, while the container reads it ==="
RINGS=$(python3 /src/lab/log_rings.py)
printf '%s\n' "$RINGS" | sed 's/^/  /'
ring() { printf '%s' "$RINGS" | python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
RING_OK=$(ring '"yes" if d.get("readable") else "no"')
RING_N=$(ring 'd.get("n_rings")')
RING_LAYOUT=$(ring '"%s of %s" % (d.get("layout_bytes"), d.get("bytes"))')
RING_WRITTEN=$(ring 'd.get("written", 0)')

echo "=== the hold-down: every block released ==="
sleep 12
LOGGING=$(vppctl show bpa logging | tr -d '\r' | sed -n '/TCP streams carried/,$p')
printf '%s\n' "$LOGGING" | sed 's/^/  | /'
CARRIER_END=$(carrier)
wait $COL_IPFIX 2>/dev/null
wait $COL_SYSLOG 2>/dev/null
[ -n "$LOGD" ] && { kill -TERM $LOGD 2>/dev/null; wait $LOGD 2>/dev/null; }
tail -3 /tmp/logd.log 2>/dev/null | sed 's/^/  logd: /'
grep -E "ALLOCATION|TEMPLATE|connection" /tmp/ipfix-tcp.out | sed 's/^/  ipfix: /'
cut -c1-200 /tmp/syslog.lines 2>/dev/null | sed 's/^/  syslog: /'

# Per subscriber, so a record that names the wrong one cannot stand in for
# the right one.
subs_with() {  # <file> <pattern with SUB for the address>: how many of the N
  _n=0; i=10
  while [ $i -lt $((10 + N)) ]; do
    _p=$(printf '%s' "$2" | sed "s/SUB/100\\\\.64\\\\.0\\\\.$i/")
    [ "$(grep -cE "$_p" "$1" 2>/dev/null)" = 1 ] && _n=$((_n+1))
    i=$((i+1))
  done
  echo $_n
}
IPFIX_ALLOC=$(subs_with /tmp/ipfix-tcp.out ' ALLOCATION +SUB -> ')
IPFIX_FREE=$(subs_with /tmp/ipfix-tcp.out ' DEALLOCATION +SUB -> ')
SYS_ALLOC=$(subs_with /tmp/syslog.lines 'BLOCK-ALLOC .*subscriber="SUB"')
SYS_FREE=$(subs_with /tmp/syslog.lines 'BLOCK-RELEASE .*subscriber="SUB"')
BY_LOGD=no
case "$CARRIER$CARRIER_END" in
  *"log container (pid ${LOGD:-none})"*"log container (pid ${LOGD:-none})"*) BY_LOGD=yes ;;
esac
echo "  carried by prisma-logd (pid ${LOGD:-none}) from start to end: $BY_LOGD"
PENDING=$(printf '%s\n' "$LOGGING" | sed -n 's/.* \([0-9][0-9]*\) pending.*/\1/p' | sort -n | tail -1)

echo
echo "=========== VERDICT ==========="
expect "the TCP streams were carried by the log container this lab started, start to end" \
  test "$BY_LOGD" = yes
expect "IPFIX/TCP through the log container: all $N allocations, one per subscriber (got $IPFIX_ALLOC)" \
  test "$BY_LOGD" = yes -a "$IPFIX_ALLOC" = $N
expect "IPFIX/TCP through the log container: all $N releases (got $IPFIX_FREE)" \
  test "$BY_LOGD" = yes -a "$IPFIX_FREE" = $N
expect_in "  and the block template reached the collector" "TEMPLATE 258" "$(cat /tmp/ipfix-tcp.out)"
expect "syslog through the log container: $N BLOCK-ALLOC lines, one per subscriber (got $SYS_ALLOC)" \
  test "$BY_LOGD" = yes -a "$SYS_ALLOC" = $N
expect "syslog through the log container: $N BLOCK-RELEASE lines (got $SYS_FREE)" \
  test "$BY_LOGD" = yes -a "$SYS_FREE" = $N
expect "and the container left nothing pending on either stream (${PENDING:-none})" \
  test "$BY_LOGD" = yes -a "${PENDING:-x}" = 0
expect "the segment is readable from outside VPP (#606): $(ring 'd.get("why")')" \
  test "$RING_OK" = yes
expect "  with one ring per vlib main: $RING_N for $WORKERS worker(s)" \
  test "$RING_N" = $((WORKERS + 1))
expect "  and its layout adds up to its size: $RING_LAYOUT bytes" \
  sh -c "printf '%s' '$RING_LAYOUT' | awk '\$1 == \$3 && \$1 > 0 { ok = 1 } END { exit !ok }'"
expect "  and the rings carried this run's $N allocations at least (written $RING_WRITTEN)" \
  test "${RING_WRITTEN:-0}" -ge $N
verdict
