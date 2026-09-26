# OFF-RED-AT-LEAST: 4
# A dynamic pool's first allocation larger than its step (#462).
#
# Asked for by the maintainer as "a first allocation of 512, then steps of
# 256": block 256 with initial-blocks 2. Here the blocks are 4 ports so a
# subscriber fills them with a handful of packets, and the shape is the same -
# a first allocation of two blocks, growing one at a time to a cap of three.
#
# Measured and not computed, as lab/extblocks.sh is: the box is asked how many
# blocks the subscriber holds after ONE packet, and after the flows that fill
# them. MODE=off builds the same pool without initial-blocks, which is every
# pool before this, and the subscriber must then hold one block after its
# first packet.
. /src/lab/lib.sh
. /src/lab/verdict.sh
set -u
MODE=${1:-on}
mkdir -p /run/vpp /vpp_plugins /var/log/vpp
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja >/dev/null
}
cd /src
vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp /tmp/vpp.log || exit 1

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up; vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0

INITIAL="initial-blocks 2"
[ "$MODE" = off ] && INITIAL=""
# Sixteen ports on one address: four 4-port blocks. Cap 3, first allocation 2.
vc bpa pool first inside 100.64.0.0/22 outside 200.150.0.16/32 \
    ports 1024-1039 block 4 mode dynamic max-blocks 3 $INITIAL
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa timeouts udp 600 >/dev/null

echo "=========== the pool ($MODE) ==========="
vc show bpa pool | sed 's/^/  /'
PERSUB=$(vc show bpa pool | grep "per sub")

send() {   # subscriber, source port
  vppctl packet-generator delete s >/dev/null 2>&1
  vppctl packet-generator new "name s limit 1 node ip4-input interface pg0 \
      size 60-60 data { UDP: 100.64.0.$1 -> 8.8.8.8 UDP: $2 -> 53 incrementing 8 }"
  vppctl packet-generator enable >/dev/null; sleep 0.4
  vppctl packet-generator delete s >/dev/null 2>&1
}
in_use() {   # blocks held across the box, from the total row
  vc show bpa blocks | sed -n 's/^ *total: \([0-9][0-9]*\)\/.*/\1/p'
}
allocs() {
  vc show bpa blocks \
    | sed -n 's/^ *total: .* in use, \([0-9][0-9]*\) allocations.*/\1/p'
}

echo; echo "=========== one packet from a new subscriber ==========="
send 5 5001
FIRST=$(in_use); FIRST_ALLOCS=$(allocs)
echo "  blocks held after the first packet: ${FIRST:-?} (${FIRST_ALLOCS:-?} allocations)"
vc show bpa subscriber 100.64.0.5 | sed 's/^/  /'

echo; echo "=========== eight flows: the first allocation holds them ==========="
for sp in 5002 5003 5004 5005 5006 5007 5008; do send 5 $sp; done
EIGHT=$(in_use)
echo "  blocks held after eight flows: ${EIGHT:-?}"

echo; echo "=========== the ninth grows it by one block, to the cap ==========="
send 5 5009
NINE=$(in_use)
echo "  blocks held after nine flows: ${NINE:-?}"

echo; echo "=========== read back, as prismash reads it ==========="
CONF=$(vc show bpa config)
echo "$CONF" | sed -n '/^cgnat pool first/,/^cgnat pool\|^cgnat interface/p' | sed 's/^/  /'

echo; echo "=========== refused where it cannot mean anything ==========="
DET=$(vc bpa pool det inside 100.64.8.0/24 outside 200.150.1.0/30 \
      block 512 mode deterministic initial-blocks 2 2>&1)
echo "  $DET"
OVER=$(vc bpa pool over inside 100.64.9.0/24 outside 200.150.2.0/30 \
       block 512 mode dynamic max-blocks 2 initial-blocks 3 2>&1)
echo "  $OVER"

echo; echo "=========== VERDICT ==========="
expect "a new subscriber holds two blocks after its first packet" \
  test "${FIRST:-0}" = 2
expect "  both taken as allocations the log records" \
  test "${FIRST_ALLOCS:-0}" = 2
expect "show bpa pool gives the range from the first allocation" \
  sh -c "echo '$PERSUB' | grep -q '8-12 ports (starts at 2 blocks'"
expect "eight flows fit the first allocation without growing it" \
  test "${EIGHT:-0}" = 2
expect "the ninth grows it one block, to the cap of three" \
  test "${NINE:-0}" = 3
expect_in "show bpa config prints it for prismash to read back" \
  " initial-blocks 2" "$CONF"
expect_in "a deterministic pool refuses it" "dynamic pools only" "$DET"
expect_in "a first allocation above max-blocks is refused" \
  "above max-blocks 2" "$OVER"
verdict
