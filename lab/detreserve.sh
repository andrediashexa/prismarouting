#!/bin/sh
# Deterministic reserve: the spare blocks a formula-mapped pool can give away.
#
# A deterministic pool has no spare block by construction. bpa_forward()
# assigns every block to some subscriber, so a subscriber that fills its block
# is dropped and the customer's internet stops while the box is healthy. A
# reserve is blocks held OUT of that formula, which is why it is paid for in
# capacity and why enabling it re-maps subscribers (issue #39).
#
# What this lab measures, and the order matters:
#
#   1. The capacity the BOX reports is the capacity the MODEL predicts. Not
#      recomputed here - ref/bpa_model.py is run, in the container, and its
#      number is compared with the one vppctl prints. A lab that did the
#      arithmetic itself would be a second implementation agreeing with
#      neither.
#   2. A subscriber that fills its block is given a reserve block, and the
#      block it gets is one no subscriber's formula produces.
#   3. Traffic comes BACK on that block. This is the half a counter cannot
#      show: the reverse direction of a deterministic pool is arithmetic, and
#      the arithmetic cannot name the holder of a reserve port - so a reply is
#      either resolved through the allocator or it is dropped.
#   4. Every reserve block is its own RFC 8158 natEvent 16 with its own port
#      range, and its release is its own natEvent 17, at a real collector on
#      the far side of a wire.
#   5. Running out of reserve is counted and visible, and is a different
#      number from a subscriber reaching max-blocks.
#   6. The extension lands on the SUBSCRIBER'S OWN public address, measured on
#      a pool where it could land somewhere else. Items 2 to 5 run on a pool
#      with one public address, where "its own address" is the only answer
#      there is and the check cannot fail. The second pool has four. A third
#      one reports, without asserting, what the same preference does for a
#      subscriber one block further along its address, which is where the
#      worker sharding starts to answer differently; that number is read from
#      issue #226 and the section says why it is not a check.
#
# AND IT RUNS WITH THE FEATURE OFF. `sh tools/lab.sh detreserve off` builds
# the same pool with `reserve 0`, which is what every configuration written
# before this existed means, and the extension assertions MUST then go red.
# That argument is the only thing that makes the green run worth anything.
# OFF-RED-AT-LEAST: 10
#
# A FLOOR, not a target, and measured rather than chosen: `sh tools/lab.sh
# detreserve off` on 2026-09-14 went red on exactly these ten, which are the
# ten that depend on a block the formula does not produce:
#
#   the subscriber that filled its block was given a second one
#   and it is the block the model holds out of the formula
#   on the subscriber's own public address, so it is still one host
#   a reply addressed to the reserve block was translated back
#   and the box counted the refusal as a reserve exhaustion
#   which is a different number from the extensions it did hand out
#   the reserve block arrived at the collector as its own natEvent 16
#   and its release as its own natEvent 17, at the same port range
#   one allocation record per block, not one for the pair
#   and every reserve block landed on its holder's own address
#
# It was nine until item 6 was added. The tenth is the one that goes red for
# the RIGHT reason where the third goes red for a free one: with no reserve
# nobody extends at all, so both fail, and only the tenth would still fail if
# the extension happened and landed on the wrong address.
#
# The three that stay green are the ones asserting what the box does INSTEAD,
# and two more - "the second subscriber found the reserve empty and stayed on
# one block", which is true for free when there is no reserve, and "the four
# holders really are on four different public addresses", which is about the
# pool's shape and not about the reserve. They are left in rather than
# counted: an assertion that cannot discriminate in this mode is not evidence,
# and pretending it is would put the floor too high.
#
# tools/run-all-labs.sh refuses an off run that goes red on fewer, because a
# lab that used to fail nine checks and now fails one has lost eight
# assertions without losing its verdict. Gaining assertions needs no edit
# here; losing them has to be deliberate.

set -u
. /src/lab/lib.sh   # startup_conf, use_binary_api
. /src/lab/verdict.sh

# "off" is the control: the same pool with no reserve at all.
MODE=${1:-on}
RESERVE=1
[ "$MODE" = "off" ] && RESERVE=0

mkdir -p /run/vpp /vpp_plugins /var/log/vpp /var/log/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api

# The collector lives in its own namespace on the far side of a veth, exactly
# as lab/extblocks.sh arranges it: a record that never left the box is not a
# record anybody can be attributed by.
ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up
ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
PEERMAC=$(ip netns exec peer cat /sys/class/net/veth-out/address)
vppctl set ip neighbor host-veth-vpp 10.0.0.2 $PEERMAC

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up; vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0

# 16 ports, 4-port blocks: four blocks per public address, on one address.
# With a reserve of 1 that is three mapped blocks and one held back, so the
# capacity moves from 4 to 3 and there is exactly one block to give away.
# Small on purpose - a subscriber fills a four-port block with five flows.
PORTS=1024-1039
BSZ=4
OUT=200.150.0.16/32
RESLINE=""
[ "$RESERVE" -gt 0 ] && RESLINE="reserve $RESERVE"

vppctl bpa pool det inside 100.64.0.0/22 outside $OUT \
    ports $PORTS block $BSZ mode deterministic max-blocks 2 $RESLINE
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa timeouts udp 600 >/dev/null

echo "=========== the pool, with reserve $RESERVE ==========="
vppctl show bpa pool | sed 's/^/  /'

echo
echo "=========== what the box says, and what the model says ==========="
# THE MODEL IS THE SOURCE OF TRUTH AND IS ASKED DIRECTLY. The pool definition
# is retyped here in the model's own terms, which is the only duplication this
# file contains, and it is the point: two independent computations of one
# number that must agree.
MODEL=$(python3 - "$RESERVE" <<'PY'
import sys
sys.path.insert(0, "/src/ref")
from bpa_model import PoolConfig, MapPolicy
p = PoolConfig("det", ["100.64.0.0/22"], ["200.150.0.16/32"],
               block_size=4, port_min=1024, port_max=1039,
               policy=MapPolicy.INTERLEAVED, block_reserve=int(sys.argv[1]))
print(f"{p.capacity} {p.blocks_per_ip} {p.blocks_mapped} {p.reserve_count}")
for r in range(p.reserve_count):
    f = p.reserve_forward(r)
    print(f"reserve {r} {f['ip_pub']} {f['port_lo']} {f['port_hi']}")
PY
)
MODEL_CAP=$(printf '%s\n' "$MODEL" | sed -n 1p | cut -d' ' -f1)
MODEL_MAPPED=$(printf '%s\n' "$MODEL" | sed -n 1p | cut -d' ' -f3)
MODEL_RES_LO=$(printf '%s\n' "$MODEL" | sed -n 's/^reserve 0 [0-9.]* \([0-9]*\) .*/\1/p')
MODEL_RES_HI=$(printf '%s\n' "$MODEL" | sed -n 's/^reserve 0 [0-9.]* [0-9]* \([0-9]*\)$/\1/p')
BOX_CAP=$(vppctl show bpa pool | sed -n 's/^ *capacity *\([0-9]*\) subscribers.*/\1/p')
printf '%s\n' "$MODEL" | sed 's/^/  model: /'
echo "  box capacity: ${BOX_CAP:-?}    model capacity: ${MODEL_CAP:-?}"
echo "  the reserve the model places: ports ${MODEL_RES_LO:--}-${MODEL_RES_HI:--}"

echo
echo "=========== a subscriber fills its block ==========="
send() {   # inside address, source port
  vppctl packet-generator delete s >/dev/null 2>&1
  vppctl packet-generator new "name s limit 1 node ip4-input interface pg0 \
      size 60-60 data { UDP: $1 -> 8.8.8.8 UDP: $2 -> 53 incrementing 8 }"
  vppctl packet-generator enable >/dev/null; sleep 0.4
  vppctl packet-generator delete s >/dev/null 2>&1
}

# The exporter is configured THROUGH THE VTY and never from this file. A lab
# that performs the product's own step proves nothing about the product, which
# is the rule lab/ipfix.sh records after UDP export shipped for the life of
# the project with a green lab beside it.
HX="python3 /src/src/hexash/hexash.py"
$HX -c "configure" \
    -c "cgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739" \
    -c "cgnat logging enable" \
    -c "commit" 2>&1 | sed 's/^/    /'
# THE COLLECTOR'S WINDOW IS NOT A GUESS ANY MORE (#342's fix, applied here).
#
# This was a fixed number of seconds chosen when the lab was written, and the
# lab then waited it out whatever the box did. On a machine carrying something
# else the box is slower, its records arrive after the window has closed, and
# the lab reports that the log is missing them - which is a product defect it
# has not got. That happened on 2026-09-15: this lab and two others went red on
# "the record arrived at the collector" while the same machine ran the whole
# test suite under gcov three times over, and all of them passed alone.
#
# So the number is a CEILING for a run nobody is watching, and the lab tells
# the collector when the box has finished. The collector drains what is
# already in its receive buffer before it leaves, so nothing in flight is lost.
COL_STOP=/tmp/collector.stop
rm -f "$COL_STOP"
ip netns exec peer python3 /src/tools/ipfix_collector.py 4739 180 \
  "$COL_STOP" \
  > /tmp/collector.out 2>&1 &
COL_PID=$!
sleep 2

S1=100.64.0.0
for sp in 5001 5002 5003 5004; do send $S1 $sp; done
echo "  four flows in, the block is full:"
vppctl show bpa subscriber $S1 | sed 's/^/    /'

send $S1 5005
SUB=$(vppctl show bpa subscriber $S1)
echo "  after the fifth:"
printf '%s\n' "$SUB" | sed 's/^/    /'
NBLK=$(printf '%s\n' "$SUB" | grep -c '^  block ')
blocks_of () { printf '%s\n' "$1" | sed -n 's/^  block [0-9]*  \([0-9.]*\)  ports \([0-9]*\)-\([0-9]*\).*/\1 \2 \3/p'; }
B1_IP=$(blocks_of "$SUB" | sed -n 1p | cut -d' ' -f1)
B2_IP=$(blocks_of "$SUB" | sed -n 2p | cut -d' ' -f1)
B2_LO=$(blocks_of "$SUB" | sed -n 2p | cut -d' ' -f2)
B2_HI=$(blocks_of "$SUB" | sed -n 2p | cut -d' ' -f3)
echo "  blocks held: $NBLK   second block: ${B2_IP:--} ${B2_LO:--}-${B2_HI:--}"

echo
echo "=========== the reply comes back on the reserve block ==========="
# The half no counter can show. The reverse direction of a deterministic pool
# is arithmetic, and the arithmetic refuses a reserve port on purpose - under
# the blocked policy it would name a real and different customer. So a reply
# on this port is resolved through the allocator or it is dropped, and the
# session table is where the answer shows up.
# Read off the node counter rather than off a session dump: the claim is that
# the packet was TRANSLATED, and a session existing says only that the
# outbound direction created one.
o2i_translated() {
  vppctl show errors \
    | awk '/bpa-out2in/ && /packets translated/ { print $1; exit }'
}
OUT2IN_BEFORE=$(o2i_translated)
if [ -n "${B2_LO:-}" ]; then
  vppctl packet-generator delete r >/dev/null 2>&1
  vppctl packet-generator new "name r limit 1 node ip4-input interface pg1 \
      size 60-60 data { UDP: 8.8.8.8 -> ${B1_IP} UDP: 53 -> ${B2_LO} incrementing 8 }"
  vppctl packet-generator enable >/dev/null; sleep 0.5
  vppctl packet-generator delete r >/dev/null 2>&1
fi
OUT2IN_AFTER=$(o2i_translated)
vppctl show errors | grep -i "bpa-out2in" | sed 's/^/    /'
echo "  out2in translated: ${OUT2IN_BEFORE:-0} before, ${OUT2IN_AFTER:-0} after"

echo
echo "=========== running out of reserve ==========="
# The pool holds exactly one reserve block and it is taken. A second
# subscriber that fills its own block therefore has nowhere to go, and that is
# a DIFFERENT refusal from reaching max-blocks: the answer to one is a larger
# reserve, and to the other a larger cap.
S2=100.64.0.1
for sp in 6001 6002 6003 6004 6005; do send $S2 $sp; done
SUB2=$(vppctl show bpa subscriber $S2)
printf '%s\n' "$SUB2" | sed 's/^/    /'
NBLK2=$(printf '%s\n' "$SUB2" | grep -c '^  block ')
BLOCKS=$(vppctl show bpa blocks)
printf '%s\n' "$BLOCKS" | sed 's/^/    /'
EXH=$(printf '%s\n' "$BLOCKS" | sed -n 's/^ *reserve exhausted \([0-9][0-9]*\) time(s).*/\1/p')
EXTS=$(printf '%s\n' "$BLOCKS" \
       | sed -n 's/^ *total: .* allocations (\([0-9][0-9]*\) extensions).*/\1/p')

echo
echo "=========== the block goes back, and the log says so ==========="
vppctl bpa block-holddown 3 >/dev/null
vppctl bpa timeouts udp 2 >/dev/null
sleep 12
vppctl show bpa blocks | sed 's/^/    /'

# The box has done everything this lab asks of it, so there is nothing more to
# hear. A moment first for the last records to cross the wire: the collector
# drains its buffer, so this only has to cover flight time.
sleep 2
: > "$COL_STOP"
wait $COL_PID 2>/dev/null
COL=$(cat /tmp/collector.out)
printf '%s\n' "$COL" | grep -E "$S1 " | sed 's/^/    /'
# One record per block, naming that block's own ports. A block the log does
# not record cannot be attributed to anybody, which is the whole reason a
# deterministic pool - which logs nothing per subscriber - has to log this one.
# THE SPACE BEFORE "ALLOCATION" IS LOAD-BEARING. "DEALLOCATION" contains
# "ALLOCATION", so an unanchored pattern counts every release as an allocation
# as well - which is what the first run of this file did: "one record per
# block" went red while the two checks above passed for the wrong reason.
ALLOC_RES=$(printf '%s\n' "$COL" \
  | grep -cE " ALLOCATION +$S1 -> ${B2_IP:-none} portas ${B2_LO:--}-${B2_HI:--}")
DEALLOC_RES=$(printf '%s\n' "$COL" \
  | grep -cE " DEALLOCATION +$S1 -> ${B2_IP:-none} portas ${B2_LO:--}-${B2_HI:--}")
ALLOC_ANY=$(printf '%s\n' "$COL" | grep -cE " ALLOCATION +$S1 ")

echo
echo "=========== the preference, on a pool where it can be seen ==========="
# THE ASSERTION ABOVE CANNOT FAIL. Pool `det` has ONE public address, so "the
# extension landed on the subscriber's own address" is true whatever the
# allocator does: there is no other address for it to land on. The preference
# in bpa_dyn_acquire_extra() could be deleted and that line would stay green,
# which is how a property stops being held with nothing going red. Measured
# rather than argued: the loop was deleted and the run below says what each of
# the two checks did (#226).
#
# This pool has FOUR public addresses and four reserve blocks, one per
# address. The subscribers extend in descending index order while the free
# list hands blocks out in ascending order, so the block waiting on top of the
# stack is never the one the subscriber's own address holds. The box has to
# choose between the two, and nothing here names a block for it.
#
# Worth knowing before reading a red one: the reserve is sharded by index like
# every other block, so which subscribers the preference can be honoured for
# depends on the worker count as well as on the pool. The section after this
# one reports that half rather than asserting it, and says why.
vppctl bpa timeouts udp 600 >/dev/null   # the section above shortened it to 2
vppctl bpa pool pair inside 100.64.8.0/28 outside 200.150.4.16/30 \
    ports $PORTS block $BSZ mode deterministic max-blocks 2 $RESLINE
vppctl show bpa pool | sed 's/^/  /'

PAIR_OWN=0; PAIR_N=0; PAIR_PRIM=""
for i in 3 2 1 0; do
  for sp in 7001 7002 7003 7004 7005; do send 100.64.8.$i $sp; done
done
for i in 3 2 1 0; do
  PSUB=$(vppctl show bpa subscriber 100.64.8.$i)
  P_A=$(blocks_of "$PSUB" | sed -n 1p | cut -d' ' -f1)
  P_B=$(blocks_of "$PSUB" | sed -n 2p | cut -d' ' -f1)
  P_N=$(printf '%s\n' "$PSUB" | grep -c '^  block ')
  PAIR_N=$((PAIR_N + 1))
  PAIR_PRIM="$PAIR_PRIM $P_A"
  if [ "$P_N" -eq 2 ] && [ -n "$P_A" ] && [ "$P_A" = "$P_B" ]; then
    PAIR_OWN=$((PAIR_OWN + 1)); P_V="own address"
  else
    P_V="ELSEWHERE"
  fi
  printf '    %-14s primary %-14s extension %-14s %s\n' \
    "100.64.8.$i" "${P_A:--}" "${P_B:--}" "$P_V"
done
PAIR_DISTINCT=$(printf '%s\n' $PAIR_PRIM | sort -u | grep -c .)
echo "  $PAIR_OWN of $PAIR_N extensions landed on the holder's own public address"
echo "  and the four holders sit on $PAIR_DISTINCT distinct public addresses"
echo "  workers on this box: $(box_workers)"

# And the same measurement one block further along the address, which is where
# the SHARDING starts to answer differently.
#
# PRINTED AND NOT ASSERTED, deliberately. A subscriber's reserve block sits at
# reserve index off*addresses + pub while its own traffic is on block index
# blk*addresses + pub, and the two are the same number only when off can equal
# blk. With one reserve block per address, off is 0 and these subscribers are
# on blk 1, so whether the preference can be honoured at all depends on the
# worker count - and working out which worker count honours it would put a
# second copy of the allocator's arithmetic in this file, which is the thing
# item 1 above exists to avoid. So the number is measured and reported, and
# #226 is where it is read.
vppctl bpa pool pair2 inside 100.64.9.0/28 outside 200.150.5.16/30 \
    ports $PORTS block $BSZ mode deterministic max-blocks 2 $RESLINE
FAR_OWN=0; FAR_N=0; FAR_EXT=0
for i in 7 6 5 4; do
  for sp in 7001 7002 7003 7004 7005; do send 100.64.9.$i $sp; done
done
for i in 7 6 5 4; do
  FSUB=$(vppctl show bpa subscriber 100.64.9.$i)
  F_A=$(blocks_of "$FSUB" | sed -n 1p | cut -d' ' -f1)
  F_B=$(blocks_of "$FSUB" | sed -n 2p | cut -d' ' -f1)
  F_N=$(printf '%s\n' "$FSUB" | grep -c '^  block ')
  FAR_N=$((FAR_N + 1))
  [ "$F_N" -eq 2 ] && FAR_EXT=$((FAR_EXT + 1))
  if [ "$F_N" -eq 2 ] && [ -n "$F_A" ] && [ "$F_A" = "$F_B" ]; then
    FAR_OWN=$((FAR_OWN + 1)); F_V="own address"
  elif [ "$F_N" -eq 2 ]; then
    F_V="ELSEWHERE"
  else
    F_V="no extension at all"
  fi
  printf '    %-14s primary %-14s extension %-14s %s\n' \
    "100.64.9.$i" "${F_A:--}" "${F_B:--}" "$F_V"
done
echo "  one block further along: $FAR_OWN of $FAR_N on the holder's own address,"
echo "  $FAR_EXT of $FAR_N got an extension at all, at $(box_workers) worker(s)"

echo
echo "=========== VERDICT ==========="
echo "  run mode: $MODE (reserve $RESERVE)"

# 1. The two computations of capacity. This one holds in BOTH runs, and that
#    is deliberate: with no reserve the model must predict today's capacity
#    exactly, which is the assertion that an upgrade changes nothing.
expect "the box and the model agree on the capacity" \
  test -n "${BOX_CAP:-}" -a "${BOX_CAP:-x}" = "${MODEL_CAP:-y}"
expect "and the model was actually asked" test -n "${MODEL_CAP:-}"

# THE EXTENSION ASSERTIONS RUN IN BOTH MODES, and that is what makes the off
# run a control rather than a second scenario.
#
# They used to be inside `if [ "$RESERVE" -gt 0 ]`, with three different
# assertions in the else branch. That off run passed, with nothing red - so
# tools/run-all-labs.sh, which INVERTS an off run's verdict, would have counted
# it as a failure of the suite, and the header's claim that "the extension
# assertions MUST then go red" was describing a run nobody performed. A lab
# whose off mode passes is an assertion that cannot fail, which is #151.
#
# With reserve 0 every one of them goes red, because every one depends on a
# block the formula does not produce. That is the argument the green run rests
# on, and now it is made rather than asserted.

# 2. The extension itself, and where it landed.
expect "the subscriber that filled its block was given a second one" \
  test "${NBLK:-0}" -eq 2
# The block it got is the one the MODEL places, not merely some block. A
# box that handed out a mapped block would also report two blocks, and that
# is the failure this whole feature exists to prevent: the block would be
# somebody else's by formula and their mapping would break in silence.
expect "and it is the block the model holds out of the formula" \
  test "${B2_LO:-x}" = "${MODEL_RES_LO:-y}" -a "${B2_HI:-x}" = "${MODEL_RES_HI:-y}"
# RFC 4787 REQ-2. The reserve is preferred on the subscriber's own public
# address, so a peer still sees one host.
expect "on the subscriber's own public address, so it is still one host" \
  test -n "${B1_IP:-}" -a "${B1_IP:-x}" = "${B2_IP:-y}"
# 3. The return path. Without the allocator lookup the arithmetic refuses
#    this port and the reply is counted as out of every block.
expect "a reply addressed to the reserve block was translated back" \
  test "${OUT2IN_AFTER:-0}" -gt "${OUT2IN_BEFORE:-0}"
# 5. Exhaustion, counted and named.
expect "the second subscriber found the reserve empty and stayed on one block" \
  test "${NBLK2:-0}" -eq 1
expect "and the box counted the refusal as a reserve exhaustion" \
  test "${EXH:-0}" -ge 1
expect "which is a different number from the extensions it did hand out" \
  test "${EXTS:-0}" -ge 1
# 4. The log. Both halves, because an allocation with no release names a
#    subscriber who still holds those ports for as long as the log is kept.
expect "the reserve block arrived at the collector as its own natEvent 16" \
  test "${ALLOC_RES:-0}" -ge 1
expect "and its release as its own natEvent 17, at the same port range" \
  test "${DEALLOC_RES:-0}" -ge 1
expect "one allocation record per block, not one for the pair" \
  test "${ALLOC_ANY:-0}" -eq 1

# 6. The preference the one-address pool above cannot see. The first of these
#    two is what makes the second worth reading: if the pool ever stops
#    offering somewhere else for the block to go, the measurement goes back to
#    being free and this says so instead of passing.
expect "the four holders really are on four different public addresses" \
  test "${PAIR_DISTINCT:-0}" -eq 4
expect "and every reserve block landed on its holder's own address" \
  test "${PAIR_N:-0}" -eq 4 -a "${PAIR_OWN:-0}" -eq "${PAIR_N:-0}"

# And what the box does INSTEAD with no reserve, which the assertions above
# cannot say: it must do what it did before this feature existed rather than
# fail in some new way. Only meaningful on the off run - with a reserve these
# are false by design.
if [ "$RESERVE" -eq 0 ]; then
  expect "with no reserve the subscriber stays on one block" \
    test "${NBLK:-0}" -eq 1
  expect "and nothing was handed out of a reserve that does not exist" \
    test -z "${EXTS:-}" -o "${EXTS:-0}" -eq 0
  expect "and the collector saw no block record at all for it" \
    test "${ALLOC_ANY:-0}" -eq 0
fi
verdict
