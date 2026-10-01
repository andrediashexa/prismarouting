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
#   7. And what the box SAYS at creation when the reserve is too small for
#      the workers this box has, which is a capacity problem none of the six
#      above can see. The reserve is sharded by index like every other block,
#      so a reserve of one block on a box with two workers leaves one worker
#      with nothing to give: the subscribers that hash to it fill their block
#      and are refused a second one every time, on a box whose "show bpa pool"
#      reports a reserve. Only reachable WITH WORKERS - see the note below -
#      and the section says so and skips itself where it cannot arise.
#   8. And that the SAME warning reaches an operator who never touches the
#      dataplane console. Items 1 to 7 are typed at `vppctl`; a pool is
#      configured in the VTY and applied over the binary API, and a warning
#      that is printed on one path and not the other has reached nobody the
#      product ships for (#372). Same box, same workers, same run: the only
#      thing that differs between item 7 and item 8 is which way in was used.
#   9. And what the box says to an operator who sets `block-stride` on such a
#      pool, which is the one setting that belongs to the other mode. The
#      extension this file measures comes out of the RESERVE - own address
#      first, then whatever its worker's slice of the reserve has free - and
#      a stride steps along the MAPPED blocks, which that walk never touches,
#      so the setting is never read - it was accepted and inert since 0.67.0,
#      with the pool screen printing the distance it promises and the
#      warning path blaming the worker sharding for it (#375). Refused now,
#      at the dataplane and at the prompt, with the same stride in dynamic
#      mode as the control both times. Unlike items 7 and 8 this one needs
#      no workers and is asserted on every run: it is a fact about the pool
#      rather than about the box.
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
#
# AND IT RUNS WITH WORKERS, which is the run item 7 needs and the reason this
# file is in BOTH_WAYS since #370.
#
# THE ABSENCE HAD NEVER BEEN ARGUED. This file carried no `# NOT-BOTH-WAYS:`
# marker and tools/run-all-labs.sh simply did not name it, so whether its
# arithmetic survives sharding was a question nobody had asked - and it is a
# fair question, because every assertion here is about WHICH block a
# subscriber is given and the free list is sharded per worker. lab/crossaddr.sh
# is the lab where the answer is no, and it says so in a marker.
#
# MEASURED BEFORE THE ENTRY WAS ADDED, not argued into place:
# `LAB_WORKERS=1 sh tools/lab.sh detreserve` on 2026-09-15 was LAB PASSED with
# all fourteen checks green, in 1m47s including the plugin build.
#
# And it holds for a REASON rather than by luck, which is what makes it safe at
# a worker count this machine never ran. Worker w owns the block indices
# congruent to w, a subscriber is handed to sub_idx % n_workers, and under
# `policy interleaved` a subscriber's public address is its index modulo the
# address count. The subscribers this file picks are 100.64.0.0 - index 0,
# whose reserve block is index 0 - and 100.64.8.0 to .3 - indices 0 to 3, on
# addresses 0 to 3, whose reserve blocks are indices 0 to 3. In both cases the
# subscriber and the reserve block it wants land on the same worker by the same
# modulo, at ANY worker count, which is why the extension assertions do not
# depend on the number of workers the box was given.
#
# ONE SENTENCE MEANS SOMETHING ELSE WITH WORKERS, and it is left standing on
# purpose. "The second subscriber found the reserve empty and stayed on one
# block" is true single-threaded because the pool's one reserve block had been
# taken; at two workers it is true because 100.64.0.1 is on the other worker,
# which never held a reserve block at all. Same verdict, different reason - and
# that difference is precisely what item 7's warning exists to tell an operator
# about, in the same run.

set -u
. /src/lab/lib.sh   # startup_conf, use_binary_api
. /src/lab/verdict.sh

# "off" is the control: the same pool with no reserve at all.
MODE=${1:-on}
RESERVE=1
[ "$MODE" = "off" ] && RESERVE=0

mkdir -p /run/vpp /vpp_plugins /var/log/vpp /var/log/prisma-routing
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
# The size of that outside set, which is the third number the creation warning
# prints: "each reserve block per address is %u more" is what one more reserve
# block costs, and a reserve block is held on EVERY public address. A /32 is
# one address, so the number here is 1, and it is written beside the set it
# describes rather than recomputed from the prefix - a lab that parsed a
# netmask would be a second implementation of an address set.
OUT_N=1
RESLINE=""
[ "$RESERVE" -gt 0 ] && RESLINE="reserve $RESERVE"

# CAPTURED AND NOT ONLY PRINTED. The box says something at CREATION that no
# later "show" repeats - whether this reserve reaches every worker this box has
# - and a lab that lets that scroll past has read the pool and not the warning.
# It is still printed, whole, so the transcript is what it was.
DET_SAY=$(vppctl bpa pool det inside 100.64.0.0/22 outside $OUT \
    ports $PORTS block $BSZ mode deterministic max-blocks 2 $RESLINE 2>&1)
printf '%s\n' "$DET_SAY" | sed 's/^/  /'
# Read off the box rather than assumed from LAB_WORKERS: the arithmetic below
# is about the workers the DATAPLANE came up with.
WK=$(box_workers)
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
HX="python3 /src/src/prismash/prismash.py"
$HX -c "configure" \
    -c "nat" -c "cgnat" -c "logging udp 10.0.0.2 src 10.0.0.1 port 4739" \
    -c "logging enable" \
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
# The question a court order asks, about a port of that reserve block (#483).
# The formula names nobody for a reserve port, and this screen used to say
# "nothing was ever translated here" - about a port that just was.
TRANS_RES=$(vppctl show bpa translation "${B2_IP:-0.0.0.0}" "${B2_LO:-0}" 2>&1 | tr -d '\r')
echo "  show bpa translation ${B2_IP:--} ${B2_LO:--}:"
printf '%s\n' "$TRANS_RES" | sed 's/^/    /'

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
# A /30 is FOUR public addresses, so this pool holds one reserve block on each
# of four - four in all, where `det` above holds one. Captured for the same
# reason `det` is, and it is the CONTROL for item 7: at the worker counts this
# number covers the box must say nothing at all about this pool's reserve.
PAIR_ADDRS=4
PAIR_SAY=$(vppctl bpa pool pair inside 100.64.8.0/28 outside 200.150.4.16/30 \
    ports $PORTS block $BSZ mode deterministic max-blocks 2 $RESLINE 2>&1)
printf '%s\n' "$PAIR_SAY" | sed 's/^/  /'
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
echo "=========== the reserve against this box's workers ==========="
# WHAT THE BOX SAID WHEN THE POOL WAS CREATED, read back here where its three
# numbers can be compared with the box this run is on.
#
# The reserve is sharded by index - worker w hands out the reserve indices
# congruent to w, because out2in resolves the thread from the index it reads
# off the packet - so a reserve smaller than the worker count leaves whole
# workers holding nothing. Their subscribers fill their block and are refused
# a second one EVERY time, and from outside that is indistinguishable from a
# reserve that is merely busy: the customer stays full while "show bpa pool"
# reports a reserve. That is a support call whose cause is invisible without
# this warning, which is why the numbers in it are asserted below and not just
# its presence.
#
# COLLAPSED FIRST, the way expect_said does it: these are sentences, and a
# needle that depends on where one folded is a needle that stops matching the
# next time the message is re-wrapped (#271).
DET_FLAT=$(printf '%s' "$DET_SAY" | tr '\n' ' ' | tr -s ' ')
NOHOLD=$(printf '%s' "$DET_FLAT" \
  | sed -n 's/.*workers: \([0-9][0-9]*\) of them hold no reserve.*/\1/p')
RAISE=$(printf '%s' "$DET_FLAT" \
  | sed -n 's/.*raise the reserve to at least \([0-9][0-9]*\).*/\1/p')
PERADDR=$(printf '%s' "$DET_FLAT" \
  | sed -n 's/.*each reserve block per address is \([0-9][0-9]*\) more.*/\1/p')
# The blocks this pool holds out of the formula: one per public address, times
# the addresses. The same product bpa_pool_init() forms, and it is the
# arrangement THIS FILE MADE rather than a number read back off the box - the
# whole point is to compare what the box says with what was asked for.
RES_BLOCKS=$((OUT_N * RESERVE))
echo "  this box has ${WK:-0} worker(s); pool det holds $RES_BLOCKS reserve block(s)"
echo "  the box said: ${NOHOLD:--} worker(s) hold none, raise to ${RAISE:--},"
echo "                one more reserve block per address costs ${PERADDR:--}"

echo
echo "=========== the same reserve, created through the VTY ==========="
# THE PATH AN OPERATOR ACTUALLY TAKES, and the reason this section exists
# beside the one above rather than instead of it.
#
# Everything above was typed at `vppctl`, which is the dataplane's own console
# and not the interface this product ships. An operator configures a pool in
# the VTY, the VTY applies it over the binary API, and the warning has to
# arrive THERE - at the prompt, in the commit that created the pool - or it has
# not arrived at all. The box above and the box here are the same box with the
# same workers in the same run, so the only thing that differs between the two
# sections is which way in was used.
#
# A POOL OF ITS OWN, not `det` re-committed. Committing a change to `det` would
# make prismash rebuild a pool this file has already measured, and every
# assertion above it would then be about a pool that had been taken down and
# put back. This one is built at the end, carries nothing but its reserve, and
# nothing else reads it.
VTY_IN=100.64.12.0/28
VTY_OUT=200.150.8.16/32
# One public address, so one reserve block per `reserve` - the same arithmetic
# OUT_N states for `det`, written beside the set it describes for the same
# reason.
VTY_OUT_N=1
# NOT $BSZ, and the difference is the product's and not this file's. The VTY
# refuses a block smaller than 64 ports, so the 4-port blocks every section
# above uses cannot be typed at the prompt at all - the first attempt at this
# section was refused for exactly that and the pool was never created, which
# the guard below caught as "the pool was not created" rather than as a
# missing warning. The shape is the same as `det`: a port range holding four
# blocks, one of them held back.
VTY_BSZ=64
VTY_PORTS="1024 1279"
VTY_RES_BLOCKS=$((VTY_OUT_N * RESERVE))
VTY_RESLINE=""
[ "$RESERVE" -gt 0 ] && VTY_RESLINE="reserve $RESERVE"
VTY_SAY=$($HX -c "configure" \
    -c "nat" -c "cgnat" -c "pool vtyres" \
    -c "inside $VTY_IN" \
    -c "outside $VTY_OUT" \
    -c "block-size $VTY_BSZ" \
    -c "ports $VTY_PORTS" \
    -c "policy interleaved" \
    -c "mode deterministic" \
    -c "max-blocks 2" \
    ${VTY_RESLINE:+-c "$VTY_RESLINE"} \
    -c "exit" \
    -c "commit" 2>&1)
printf '%s\n' "$VTY_SAY" | sed 's/^/  /'

# THE CONTROL, and it goes through the VTY too. Four public addresses hold four
# reserve blocks, which reaches every worker this box has, so the prompt must
# say nothing at all about this one. Without it the checks below pass on a box
# that warns about every reserve there is, and a warning that fires always is a
# warning an operator learns to scroll past.
VTY_PAIR_ADDRS=4
VTY_QUIET=$($HX -c "configure" \
    -c "nat" -c "cgnat" -c "pool vtyres4" \
    -c "prefix-inside 100.64.13.0/28" \
    -c "prefix-outside 200.150.9.16/30" \
    -c "block-size $VTY_BSZ" \
    -c "ports $VTY_PORTS" \
    -c "policy interleaved" \
    -c "mode deterministic" \
    -c "max-blocks 2" \
    ${VTY_RESLINE:+-c "$VTY_RESLINE"} \
    -c "exit" \
    -c "commit" 2>&1)
printf '%s\n' "$VTY_QUIET" | sed 's/^/  | /'

# Collapsed before anything is cut out of it, for the reason DET_FLAT is: these
# are sentences, and a needle that depends on where one folded stops matching
# the next time the message is re-wrapped (#271).
VTY_FLAT=$(printf '%s' "$VTY_SAY" | tr '\n' ' ' | tr -s ' ')
VTY_NOHOLD=$(printf '%s' "$VTY_FLAT" \
  | sed -n 's/.*workers: \([0-9][0-9]*\) of them hold no reserve.*/\1/p')
VTY_RAISE=$(printf '%s' "$VTY_FLAT" \
  | sed -n 's/.*raise the reserve to at least \([0-9][0-9]*\).*/\1/p')
VTY_PERADDR=$(printf '%s' "$VTY_FLAT" \
  | sed -n 's/.*each reserve block per address is \([0-9][0-9]*\) more.*/\1/p')
# AND WHAT THE BOX WROTE DOWN, which is the other half of the same fix and the
# half nobody is watching at the time. A pool applied over the API is created
# with no terminal on the far end, so the dataplane keeps the record itself -
# and it is the only trace left for whoever reads the box hours later, after
# the customer that stayed full has already called.
VTY_LOG=$(vppctl show logging 2>/dev/null)
echo "  pool vtyres holds $VTY_RES_BLOCKS reserve block(s) against ${WK:-0} worker(s)"
echo "  the prompt said: ${VTY_NOHOLD:--} worker(s) hold none, raise to ${VTY_RAISE:--},"
echo "                   one more reserve block per address costs ${VTY_PERADDR:--}"

# 9. THE SETTING THAT DOES NOT BELONG ON THIS POOL AT ALL, at both ways in.
#
# `block-stride` says how far along the MAPPED blocks of a public address a
# subscriber's SECOND block sits, counted from the first. A deterministic
# pool's extension never comes from those: every mapped block belongs to
# somebody by formula, and the extra block comes out of the reserve this
# whole file is about - a free reserve block on the subscriber's own address
# when the worker has one, and otherwise whatever that worker's slice of the
# reserve has free, a preference and not a rule exactly as it is on a dynamic
# pool. Neither step reads the stride, so the allocator never reads the
# setting there, at any worker count and on any box.
#
# It was accepted anyway since 0.67.0, the pool screen printed the
# distance it promises, and nothing ever produced it. Since the sharding
# warning reached the prompt, such a pool was also told the stride "cannot be
# honoured: blocks are sharded N ways and a stride step moves by M, so every
# block the stride names belongs to another worker" and given "the nearest
# stride that can be" - a sentence that is false twice over here, because the
# sharding is not the reason and no stride works on this pool at any worker
# count. An operator following that advice changes a number and gets the same
# nothing.
#
# A SIZE THAT FITS, on purpose. 1 is a legal stride for this pool - four
# blocks per public address - so the only thing left to refuse it for is the
# mode. A stride too large for the pool is refused by a different sentence,
# and a section that used one would be green with the mode never looked at.
STRIDE=1
# Fresh prefixes, because every pool above is still standing and this file's
# earlier measurements are not to be disturbed by it.
DET_STRIDE_SAY=$(vppctl bpa pool detstride inside 100.64.14.0/28 \
    outside 200.150.10.16/32 ports $PORTS block $BSZ mode deterministic \
    max-blocks 2 block-stride $STRIDE 2>&1 | tr -d '\r')
printf '%s\n' "$DET_STRIDE_SAY" | sed 's/^/  /'
# THE CONTROL, and it is the same pool with one word different. Without it
# every check below passes on a box that refuses every stride there is, which
# would take the feature away from the pools it was written for.
DYN_STRIDE_SAY=$(vppctl bpa pool dynstride inside 100.64.15.0/28 \
    outside 200.150.11.16/32 ports $PORTS block $BSZ mode dynamic \
    max-blocks 2 block-stride $STRIDE 2>&1 | tr -d '\r')
printf '%s\n' "$DYN_STRIDE_SAY" | sed 's/^/  | /'

# The pool screen, per pool rather than whole: the row that promises the
# layout is printed under the pool it belongs to, and a grep over every pool
# on the box cannot tell which one made it.
POOLS_NOW=$(vppctl show bpa pool 2>&1 | tr -d '\r')
stanza() { printf '%s\n' "$POOLS_NOW" | awk -v n="$1" '$1=="pool"{f=($2==n)} f'; }
DYN_STRIDE_ROWS=$(stanza dynstride)
# Which pools the screen makes the layout promise about, and in which mode.
# The header carries the mode in brackets - "pool dynstride [dynamic /
# interleaved]" - so the pool and its mode are read off the same screen that
# made the promise, rather than out of what this file asked for.
STRIDE_ROWS=$(printf '%s\n' "$POOLS_NOW" \
  | awk '$1=="pool"{n=$2; m=$3} /^ *stride /{print n, m}')
STRIDE_ROW_N=$(printf '%s\n' "$STRIDE_ROWS" | grep -c '[^ ]')
STRIDE_ROW_NOT_DYN=$(printf '%s\n' "$STRIDE_ROWS" | grep '[^ ]' \
  | grep -vc '\[dynamic')
echo "  the pool screen promises a stride on: ${STRIDE_ROWS:-(nothing)}"

# AND THE SAME TWO POOLS AT THE PROMPT, which is where an operator is. The
# pool screen is a readback; this is the commit itself, and a refusal that
# reaches the dataplane console and not the prompt has reached nobody.
#
# `mode` and `block-stride` are typed in that order here and the opposite
# order would be just as correct, which is why the answer comes at the commit
# and not at either line.
VTY_STRIDE_SAY=$($HX -c "configure" \
    -c "nat" -c "cgnat" -c "pool vtystride" \
    -c "prefix-inside 100.64.16.0/28" \
    -c "prefix-outside 200.150.12.16/32" \
    -c "block-size $VTY_BSZ" \
    -c "ports $VTY_PORTS" \
    -c "policy interleaved" \
    -c "mode deterministic" \
    -c "max-blocks 2" \
    -c "block-stride $STRIDE" \
    -c "exit" \
    -c "commit" 2>&1)
printf '%s\n' "$VTY_STRIDE_SAY" | sed 's/^/  /'
VTY_DYNSTRIDE_SAY=$($HX -c "configure" \
    -c "nat" -c "cgnat" -c "pool vtydynstride" \
    -c "prefix-inside 100.64.17.0/28" \
    -c "prefix-outside 200.150.13.16/32" \
    -c "block-size $VTY_BSZ" \
    -c "ports $VTY_PORTS" \
    -c "policy interleaved" \
    -c "mode dynamic" \
    -c "max-blocks 2" \
    -c "block-stride $STRIDE" \
    -c "exit" \
    -c "commit" 2>&1)
printf '%s\n' "$VTY_DYNSTRIDE_SAY" | sed 's/^/  | /'
POOLS_AFTER=$(vppctl show bpa pool 2>&1 | tr -d '\r')

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
# 2b. And the translation screen answers for that port with its holder, as
#     the reserve it is, rather than "nothing was ever translated here".
expect "the translation screen names the reserve port's holder" \
  test -n "$(printf '%s\n' "$TRANS_RES" | grep -F -e "->  $S1")"
expect "  and says the answer is the reserve's, not the formula's" \
  test -n "$(printf '%s\n' "$TRANS_RES" | grep -F "DETERMINISTIC RESERVE")"
# 3. The return path. Without the allocator lookup the arithmetic refuses
#    this port and the reply is counted as out of every block.
expect "a reply addressed to the reserve block was translated back" \
  test "${OUT2IN_AFTER:-0}" -gt "${OUT2IN_BEFORE:-0}"
# 5. Exhaustion, counted and named.
expect "the second subscriber found the reserve empty and stayed on one block" \
  test "${NBLK2:-0}" -eq 1
expect "and the box counted the refusal as a reserve exhaustion" \
  test "${EXH:-0}" -ge 1
# THE SENTENCE THAT COUNT COMES IN, held whole. The assertion above reads a
# number this lab cut out with sed, which says nothing about the words
# around it - and the words are what an operator reads. `reserve exhausted
# N time(s)` on its own is a number with no cause; the run below is the box
# saying which of the three ways to run out this was: the subscriber filled
# its block, it was still UNDER max-blocks so it was allowed more, and the
# reserve had nothing to give. A different exhaustion is a different fix.
expect_in "and says which kind of exhaustion it was, not just how many" \
  "time(s): a subscriber filled its block, was under max-blocks" "$BLOCKS"
expect_in "and that the reserve, not the cap, is what ran out" \
  ", and the reserve had nothing left" "$BLOCKS"
# AND THE HEADER THE COUNT SITS UNDER. A deterministic pool's line is the
# only place the box states the reserve's shape - how many blocks each
# public address carries and how many that is in all - and an operator
# sizing a reserve reads it before anything else on the screen.
expect_in "the deterministic pool names the shape of its reserve" \
  " block(s) per public address, " "$BLOCKS"
expect_in "and says it is a reserve rather than an ordinary pool" \
  " (deterministic reserve, " "$BLOCKS"
# AND THE HOLD-DOWN, which is on both kinds of pool header and is the
# number that decides how long a released block is unusable. A lab that
# never reads it is a lab that would not notice the box printing somebody
# else's default.
expect_in "and how long a released block is held down before reuse" \
  ", hold-down " "$BLOCKS"
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

# 7. THE WARNING AN OPERATOR GETS WHEN THE RESERVE DOES NOT REACH EVERY
#    WORKER, and every number in it.
#
# WHY IT IS GUARDED, and guarded on the box rather than on LAB_WORKERS. The
# warning is said only while the reserve holds FEWER blocks than the box has
# workers, and bpa_reserve_warn() reads `n_workers ? n_workers : 1`, so a
# worker-less box compares one against one and says nothing at all. On the off
# run there is no reserve either. In both of those "the warning did not appear"
# is true for a reason that has nothing to do with the product, and asserting
# it there would be the shape lab/commit.sh guards against: a check that
# measured nothing and reported a pass. So the condition below is the guard in
# the plugin, written in the arrangement's own terms, and the else branch says
# which run this was.
#
# NOT THE PRODUCT'S STEP. Nothing here works out where a reserve block goes -
# that is item 2, and it is measured by sending packets. This reads three
# numbers off a sentence the box printed at creation and compares them with the
# pool that was asked for and the worker count `show threads` reports.
if [ "$RES_BLOCKS" -lt "${WK:-0}" ]; then
  # The two ways the section could be green having measured nothing: a box
  # that never created the pool, and a box with no workers to shard it over.
  # Both are asserted rather than assumed, because the three checks after them
  # read a message that would simply be absent in either case.
  expect_in "the pool the warning is about was created" "pool det" \
    "$(vppctl show bpa pool)"
  expect "and the box has the workers the numbers are about" \
    test "${WK:-0}" -ge 2
  expect_said "a reserve that does not reach every worker is called out" \
    "the reserve on pool det is $RES_BLOCKS block(s) and this box has $WK workers" \
    "$DET_SAY"
  expect_said "and it says what those subscribers lose" \
    "can never be given a second block" "$DET_SAY"
  # The three numbers, which are what an operator acts on. A check for the
  # sentence alone goes green on a warning whose arithmetic is wrong.
  expect "the workers holding no reserve at all are the ones the reserve does not reach" \
    test "${NOHOLD:-x}" -eq "$((WK - RES_BLOCKS))"
  expect "and the reserve it says to raise to reaches every worker" \
    test "${RAISE:-x}" -eq "${WK:-0}"
  expect "and one more reserve block per address costs one per public address" \
    test "${PERADDR:-x}" -eq "$OUT_N"
  expect_said "and the pool is created anyway, because the worker count is a startup setting" \
    "this is a warning, not a refusal" "$DET_SAY"
  # THE CONTROL, and it is a pool this file already builds rather than one
  # arranged to be quiet. `pair` carries the same reserve of one block per
  # address over four addresses, so its reserve reaches four workers - and
  # while this box has no more than that, the box must say nothing about it.
  # Without this the checks above pass on a build that warns about every
  # reserve there is, which is a caution nobody reads.
  if [ "$((PAIR_ADDRS * RESERVE))" -ge "${WK:-0}" ]; then
    expect_not_said "and a reserve that does reach every worker draws no warning" \
      "hold no reserve at all" "$PAIR_SAY"
  else
    echo "  (no control: pair's $((PAIR_ADDRS * RESERVE)) reserve blocks do not"
    echo "   reach $WK workers either, so silence is not what it would mean)"
  fi
else
  echo "  [ -- ] the reserve warning cannot arise on this run: $RES_BLOCKS"
  echo "         reserve block(s) against ${WK:-0} worker(s), and it is said"
  echo "         only while the reserve reaches fewer workers than the box has"
fi

# 8. AND THE SAME WARNING AT THE VTY PROMPT, which is where an operator is.
#
# Item 7 asserts what the dataplane's own console printed. This asserts what
# the product printed, on the path prismash uses - and the two are different
# claims about different code, which is the whole reason both are here. A box
# that tells the person at `vppctl` and says nothing to the person at the
# prompt has told nobody: the VTY exists so that nobody has to drop to the
# dataplane console, and every operator is on this path.
#
# GUARDED THE SAME WAY and on the same numbers. With no reserve, or with a
# reserve that already reaches every worker, silence at the prompt is correct
# and asserting against it would be a check that measured nothing.
if [ "$VTY_RES_BLOCKS" -lt "${WK:-0}" ]; then
  # The two ways this could be green having measured nothing: a commit that
  # never created the pool - which is every way a commit can fail, and it
  # fails silently as far as a grep for an absent warning is concerned - and a
  # box with no workers to shard the reserve over.
  expect_in "the pool the warning is about was created through the VTY" \
    "pool vtyres" "$(vppctl show bpa pool)"
  expect "and the box has the workers the numbers are about" \
    test "${WK:-0}" -ge 2
  expect_said "the prompt says a reserve that does not reach every worker" \
    "the reserve on pool vtyres is $VTY_RES_BLOCKS block(s) and this box has $WK workers" \
    "$VTY_SAY"
  expect_said "and says what those subscribers lose" \
    "can never be given a second block" "$VTY_SAY"
  # The three numbers again, because they are what an operator acts on and a
  # relay that carried the sentence with somebody else's arithmetic in it
  # would be worse than none.
  expect "the workers holding no reserve are the ones the reserve does not reach" \
    test "${VTY_NOHOLD:-x}" -eq "$((WK - VTY_RES_BLOCKS))"
  expect "and the reserve the prompt says to raise to reaches every worker" \
    test "${VTY_RAISE:-x}" -eq "${WK:-0}"
  expect "and one more reserve block per address costs one per public address" \
    test "${VTY_PERADDR:-x}" -eq "$VTY_OUT_N"
  expect_said "and the commit went through, because this is a warning and not a refusal" \
    "commit complete" "$VTY_SAY"
  # AND ONLY ABOUT THE POOL THAT WAS COMMITTED. `det` has a short reserve on
  # this same box and the device says so whenever it is asked, so a relay that
  # simply forwarded everything it read would print this paragraph about a pool
  # nobody touched on every commit that went near a reserve. A warning that
  # fires whatever the operator did is one they learn to scroll past, and then
  # the commit where it IS about their change is scrolled past too.
  expect_not_said "and says nothing about a pool this commit did not touch" \
    "the reserve on pool det" "$VTY_SAY"
  expect_said "and the box wrote the record itself, for whoever reads it later" \
    "pool vtyres: reserve of $VTY_RES_BLOCKS blocks does not reach all $WK workers" \
    "$VTY_LOG"
  if [ "$((VTY_PAIR_ADDRS * RESERVE))" -ge "${WK:-0}" ]; then
    expect_in "the control pool was created too" "pool vtyres4" \
      "$(vppctl show bpa pool)"
    expect_not_said "and a reserve that does reach every worker draws no warning at the prompt" \
      "hold no reserve at all" "$VTY_QUIET"
  else
    echo "  (no control: vtyres4's $((VTY_PAIR_ADDRS * RESERVE)) reserve blocks do"
    echo "   not reach $WK workers either, so silence is not what it would mean)"
  fi
else
  echo "  [ -- ] the warning cannot arise at the prompt on this run either:"
  echo "         $VTY_RES_BLOCKS reserve block(s) against ${WK:-0} worker(s)"
fi

# 9. THE STRIDE THAT DOES NOT APPLY TO THIS POOL, refused at both ways in.
#
# UNGUARDED, unlike items 7 and 8, and that is the difference between the two
# kinds of statement. Those two are about the workers this box happened to
# start with, so they say nothing on a box that has none. This one is about
# the pool: it holds at every worker count, on every box and after every
# restart, which is why it is a refusal and why there is no arrangement under
# which "the box said nothing" would be the right answer here.
expect_said "the dataplane refuses a stride on a deterministic pool" \
  "block-stride applies to dynamic pools only" "$DET_STRIDE_SAY"
# The reason, and not only the verdict. An operator who is told a setting does
# not apply and not WHY goes looking for the box's mood; this one names the
# reserve, which is the thing that hands out their second block, and where in
# it the block comes from - which is the preference the rest of this file
# measures, and not a place anything fixes.
expect_said "and says where a deterministic extension comes from instead" \
  "comes out of this worker's reserve instead, own address first" \
  "$DET_STRIDE_SAY"
# THAT IT IS REFUSED FOR THE MODE AND NOT FOR THE SIZE is asserted by the
# control below and not by a check here. "the box did not also say it does not
# fit" cannot fail: the mode is looked at first, so a stride too large for the
# pool draws the mode sentence anyway. What proves the mode was the reason is
# the SAME stride being accepted one pool over in dynamic mode.
# THE SENTENCE THAT MUST NEVER BE SAID HERE. It is the one the box printed at
# the prompt before this: the stride cannot be honoured BECAUSE blocks are
# sharded N ways, with the nearest stride that can be. Both halves are false
# on this pool - the sharding is not the reason, and no stride works at any
# worker count - and the second half is advice that costs an operator a
# configuration change and buys them nothing.
expect_not_said "and never blames the worker sharding, which is not the reason" \
  "cannot be honoured" "$DET_STRIDE_SAY"
expect_not_said "and never names a nearer stride, because none would work" \
  "the nearest stride that can be" "$DET_STRIDE_SAY"
# Refused means not created. A box that printed the refusal and built the pool
# anyway would pass every check above.
expect_not_in "and the pool was not created" "pool detstride" "$POOLS_NOW"
expect_in "while the pool the refusal is about was reached at all" \
  "pool det " "$POOLS_NOW"
# THE CONTROL. The same pool with `mode dynamic` is the configuration this
# setting exists for, and it MUST still be built - with the row that promises
# the layout printed under it, because there the box does produce it.
expect_in "the same stride in dynamic mode is accepted" "pool dynstride" \
  "$POOLS_NOW"
expect_in "and that pool carries the layout the stride promises" \
  "stride       $STRIDE (" "$DYN_STRIDE_ROWS"
# AND THE PROMISE IS ONLY EVER MADE BY A POOL THAT KEEPS IT, read off the
# whole screen rather than off one pool. This is the row the issue is about -
# "stride 4 (2048 ports between a subscriber's blocks, on its own public
# address)" printed under a pool whose allocator never places one - and the
# check is written as a property of every pool on the box so that the next
# pool to carry a stride is measured by it without anybody adding a line.
expect "some pool on this box makes the promise, so this measured something" \
  test "${STRIDE_ROW_N:-0}" -ge 1
expect "and every pool that makes it is a dynamic one" \
  test "${STRIDE_ROW_NOT_DYN:-1}" -eq 0

# AND THE SAME TWO AT THE PROMPT, which is where an operator types it. Item 9
# above asserts what the dataplane's own console printed; this asserts what
# the product printed on the path prismash uses, and they are different code.
expect_said "the prompt refuses it too, before anything is applied" \
  "error: block-stride applies to dynamic pools only, not to pool vtystride" \
  "$VTY_STRIDE_SAY"
# The two statements whole and on their own rows. Either one resolves it and
# which to change is the operator's decision, so the box shows both rather
# than choosing for them.
expect_in "and shows the two statements that disagree, mode first" \
  "    mode deterministic" "$VTY_STRIDE_SAY"
expect_in "and the stride under it, as typed" \
  "    block-stride $STRIDE" "$VTY_STRIDE_SAY"
expect_said "and says what to do about it" \
  "drop the block-stride, or put the pool in dynamic mode" "$VTY_STRIDE_SAY"
# ONCE, not twice. The dataplane refuses this pool as well, and the commit
# check asks it about every pool it is about to build - so a commit that went
# on to ask would print a second verdict and a second paragraph for one fault,
# and leave the operator working out whether they are the same one.
expect_not_said "and says it once, not again in the dataplane's words" \
  "would be refused" "$VTY_STRIDE_SAY"
expect_not_said "and the commit did not go through" "commit complete" \
  "$VTY_STRIDE_SAY"
expect_not_in "so the prompt created no pool either" "pool vtystride" \
  "$POOLS_AFTER"
expect_not_said "and the prompt never blames the worker sharding" \
  "cannot be honoured" "$VTY_STRIDE_SAY"
# THE CONTROL AT THE PROMPT. Same file, same box, same run: the only thing
# that differs is the mode.
expect_said "the same stride in dynamic mode commits at the prompt" \
  "commit complete" "$VTY_DYNSTRIDE_SAY"
expect_in "and that pool is in the dataplane" "pool vtydynstride" \
  "$POOLS_AFTER"

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
