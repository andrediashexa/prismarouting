#!/bin/sh
# Extended blocks: a subscriber that fills its block gets another.
#
# The behaviour this replaces is correct and terrible: natEvent 12 is logged
# and the packet is dropped, so one customer's internet stops while the box is
# healthy and the neighbours are fine.
#
# Only dynamic mode. A deterministic pool has no spare block by construction -
# the formula assigns every block to some subscriber - so extending there means
# taking a block from somebody else and breaking their mapping silently. That
# needs a reserve carved out of capacity, which is issue #39.
#
# The second half is the STRIDE: where those extra blocks land.
#
# It is measured and not computed. This file never works out which block a
# subscriber ought to get - that would be a second implementation of the
# allocator, checking its own arithmetic and agreeing with itself. It runs the
# SAME traffic against two pools that differ only in the stride, asks the box
# where the blocks went, and reads the difference. A build that ignored
# block-stride gives the same answer twice and the assertions go red.
#
# And it runs a real IPFIX collector, because "each block produces its own
# natEvent 16" is the requirement the whole feature exists under: a block the
# log does not record cannot be attributed to anybody. A counter saying two
# blocks were allocated does not say that two RECORDS left the box naming the
# two port ranges the subscriber actually holds.
set -u
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp /var/log/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api

# The path to the IPFIX collector, which runs in its own netns on the far side
# of a veth. The "lcp" configuration and not "test" only because that is the
# one with af_packet enabled, which is what host-interface needs.
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
# With no resolved neighbour the first records are dropped while ARP is still
# incomplete, which for a compliance log is silent loss.
PEERMAC=$(ip netns exec peer cat /sys/class/net/veth-out/address)
vppctl set ip neighbor host-veth-vpp 10.0.0.2 $PEERMAC

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up; vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0

# Tiny blocks so a subscriber fills one with a handful of packets. Four blocks
# on one public address, and a cap of two per subscriber.
vppctl bpa pool ext inside 100.64.0.0/22 outside 200.150.0.16/32 \
    ports 1024-1039 block 4 mode dynamic max-blocks 2
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa timeouts udp 600 >/dev/null

echo "=========== pool: 16 ports / 4-port blocks = 4 blocks, cap 2 ==========="
vppctl show bpa pool | sed 's/^/  /'

send() {   # subscriber, source port
  vppctl packet-generator delete s >/dev/null 2>&1
  vppctl packet-generator new "name s limit 1 node ip4-input interface pg0 \
      size 60-60 data { UDP: 100.64.0.$1 -> 8.8.8.8 UDP: $2 -> 53 incrementing 8 }"
  vppctl packet-generator enable >/dev/null; sleep 0.4
  vppctl packet-generator delete s >/dev/null 2>&1
}

echo
echo "=========== one subscriber, five flows into a four-port block ==========="
for sp in 5001 5002 5003 5004; do send 5 $sp; done
echo "  after four flows: $(vppctl show bpa blocks | grep total | sed 's/^ *//')"

send 5 5005
echo "  after the fifth:  $(vppctl show bpa blocks | grep total | sed 's/^ *//')"
# The total line, not the first line that matches. "show bpa blocks" prints a
# row per thread and then the total, so "head -1" read a PER-THREAD row - and
# with workers the subscriber's blocks are all on its owner, which is not
# thread 0. The lab then reported 0 extensions while the total said 1 and the
# box was behaving correctly. It only ever worked because a single-threaded
# box has one thread row and it equals the total.
EXTS=$(vppctl show bpa blocks \
       | sed -n 's/^ *total: .* allocations (\([0-9][0-9]*\) extensions).*/\1/p')
# Read here and not in the verdict, because by then this pool has been
# replaced by the two the stride section needs and "total:" names three pools.
ALLOCS=$(vppctl show bpa blocks \
         | sed -n 's/^ *total: .* in use, \([0-9][0-9]*\) allocations.*/\1/p')
SESS=$(vppctl show bpa session | grep -o "total: [0-9]*" | awk '{print $2}')
echo "  extensions: ${EXTS:-0}   sessions: ${SESS:-0}"

echo
echo "=========== the cap holds: a ninth flow must be refused ==========="
for sp in 5006 5007 5008; do send 5 $sp; done
send 5 5009
REFUSED=$(vppctl show errors | grep -i "port block exhausted" | awk '{print $1}' | head -1)
SESS2=$(vppctl show bpa session | grep -o "total: [0-9]*" | awk '{print $2}')
echo "  sessions now: ${SESS2:-0}   refusals: ${REFUSED:-0}"
vppctl show bpa blocks | sed 's/^/  /'

echo
echo
echo "=========== an extension the subscriber stopped needing goes back ==="
# #216 asked whether bpa_dyn_active() - declared, defined, documented, and
# called by nothing - is a missing call or a dead function. This section is
# the answer, measured on the box rather than read off the source.
#
# What bpa_dyn_active() would do is clear the idle stamp on EVERY block a
# subscriber holds, extensions included. bpa_dyn_idle() sets those stamps, and
# it is called at one moment only: when the subscriber's session count reaches
# ZERO. At that moment every block it holds is empty, extension and primary
# alike.
#
# So a subscriber that goes quiet and comes back with ONE session needs ONE
# block. Its extension is empty and should go back to the pool - and it does,
# because bpa_dyn_acquire() clears the stamp on the PRIMARY only. Calling
# bpa_dyn_active() there would clear the extension's stamp too and keep an
# empty block owned forever, which is exactly the leak the comment above
# bpa_dyn_idle() exists to warn about.
#
# That makes the function not merely unused but WRONG to call, and this
# assertion is what would go red if somebody wired it in "to fix the gap".
vppctl bpa block-holddown 5
vppctl bpa timeouts udp 3 >/dev/null
EXT_BEFORE=$(vppctl show bpa blocks \
             | sed -n 's/^ *total: \([0-9][0-9]*\)\/.*/\1/p')
echo "  blocks in use with the extension live: ${EXT_BEFORE:-?}"

# Everything expires, so the subscriber's session count reaches zero and BOTH
# of its blocks are stamped. That is the only moment bpa_dyn_idle() runs.
sleep 6

# THE TIMEOUT GOES BACK UP BEFORE THE FLOW THAT BRINGS THE SUBSCRIBER BACK,
# and the order is the measurement rather than housekeeping. The first version
# of this section left it at 3 seconds, so the returning session expired after
# three, the subscriber reached zero sessions again, bpa_dyn_idle() stamped
# both blocks a second time, and the hold-down took them BOTH - 0 in use, which
# says nothing about extensions at all.
#
# What has to be true for the question to be asked is that the subscriber is
# still ACTIVE on its primary while the extension's stale stamp expires. A
# long-lived session is what makes that window exist.
vppctl bpa timeouts udp 600 >/dev/null
send 5 30000
sleep 1
echo "  one flow back from the same subscriber:"
vppctl show bpa blocks | sed 's/^/  /'

# Longer than the hold-down, so the stamp that was NOT cleared expires while
# the session that cleared the primary's is still alive.
sleep 8
EXT_AFTER=$(vppctl show bpa blocks \
            | sed -n 's/^ *total: \([0-9][0-9]*\)\/.*/\1/p')
SUB_AFTER=$(vppctl show bpa subscriber 100.64.0.5)
echo "  blocks in use afterwards: ${EXT_AFTER:-?}"
echo "$SUB_AFTER" | sed 's/^/  /'
vppctl bpa block-holddown 300 >/dev/null

echo
echo "=========== the stride: where a subscriber's extra blocks land ==========="
# Two pools, identical but for one number, and the same traffic on each. The
# lab does not decide where a block belongs - it asks the box, twice, and
# reads the difference between the two answers.
#
# The first pool is deleted first: pools may not claim overlapping inside
# prefixes, and everything the sections above measured is already captured in
# the shell variables above.
vppctl bpa pool ext del >/dev/null
vppctl bpa pool nostride inside 100.64.0.0/24 outside 200.150.0.32/32 \
    ports 1024-1055 block 4 mode dynamic max-blocks 3
vppctl bpa pool stride2 inside 100.64.1.0/24 outside 200.150.0.33/32 \
    ports 1024-1055 block 4 mode dynamic max-blocks 3 block-stride 2
echo "  two pools of eight four-port blocks, one with a stride of two:"
vppctl show bpa pool | grep -E "^pool (nostride|stride2)|per sub|stride " \
  | sed 's/^/    /'

# THE EXPORTER IS CONFIGURED THROUGH THE VTY, and never from here. A lab that
# performs the product's own step proves nothing about the product: the rule
# lab/ipfix.sh records after UDP export shipped for the life of the project
# with a green lab beside it.
HX="python3 /src/src/hexash/hexash.py"
$HX -c "configure" \
    -c "cgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739" \
    -c "cgnat logging enable" \
    -c "commit" 2>&1 | sed 's/^/    /'
# 25 seconds, and waited for below rather than slept over: python block-buffers
# stdout into a file, so the records appear when the collector ENDS. Reading
# the file while it is still running reads an empty buffer and says the box
# exported nothing.
ip netns exec peer python3 /src/tools/ipfix_collector.py 4739 25 \
  > /tmp/collector.out 2>&1 &
COL_PID=$!
sleep 2

sendip() {  # inside address, source port
  vppctl packet-generator delete t >/dev/null 2>&1
  vppctl packet-generator new "name t limit 1 node ip4-input interface pg0 \
      size 60-60 data { UDP: $1 -> 8.8.8.8 UDP: $2 -> 53 incrementing 8 }"
  vppctl packet-generator enable >/dev/null; sleep 0.4
  vppctl packet-generator delete t >/dev/null 2>&1
}

echo
echo "  -- no stride: the extra block is whatever the allocator had free --"
for sp in 6001 6002 6003 6004 6005; do sendip 100.64.0.10 $sp; done
SUB_PLAIN=$(vppctl show bpa subscriber 100.64.0.10)
printf '%s\n' "$SUB_PLAIN" | sed 's/^/    /'

echo
echo "  -- stride 2: the same five flows, on the pool that names a distance --"
for sp in 6001 6002 6003 6004 6005; do sendip 100.64.1.10 $sp; done
SUB_STRIDE=$(vppctl show bpa subscriber 100.64.1.10)
printf '%s\n' "$SUB_STRIDE" | sed 's/^/    /'

# Both gaps read off the box. The block size is read off it too - the first
# block's own range - so the only number this file supplies is the stride the
# operator typed.
blocks_of () { printf '%s\n' "$1" | sed -n 's/^  block [0-9]*  \([0-9.]*\)  ports \([0-9]*\)-\([0-9]*\).*/\1 \2 \3/p'; }
PLAIN_LO1=$(blocks_of "$SUB_PLAIN"  | sed -n 1p | cut -d' ' -f2)
PLAIN_LO2=$(blocks_of "$SUB_PLAIN"  | sed -n 2p | cut -d' ' -f2)
PLAIN_HI1=$(blocks_of "$SUB_PLAIN"  | sed -n 1p | cut -d' ' -f3)
STR_IP1=$(blocks_of "$SUB_STRIDE"   | sed -n 1p | cut -d' ' -f1)
STR_IP2=$(blocks_of "$SUB_STRIDE"   | sed -n 2p | cut -d' ' -f1)
STR_LO1=$(blocks_of "$SUB_STRIDE"   | sed -n 1p | cut -d' ' -f2)
STR_LO2=$(blocks_of "$SUB_STRIDE"   | sed -n 2p | cut -d' ' -f2)
STR_HI1=$(blocks_of "$SUB_STRIDE"   | sed -n 1p | cut -d' ' -f3)
BLOCKSZ=$((STR_HI1 - STR_LO1 + 1))
GAP_PLAIN=$((PLAIN_LO2 - PLAIN_LO1))
GAP_STRIDE=$((STR_LO2 - STR_LO1))
echo
echo "    block size the box reports: ${BLOCKSZ} ports"
echo "    gap between the two blocks, no stride: ${GAP_PLAIN} ports"
echo "    gap between the two blocks, stride 2:  ${GAP_STRIDE} ports"

echo
echo "  -- a stride that cannot be honoured falls back, and is counted --"
# Two more subscribers take the blocks either side of the free list, so that
# the second one's stride lands on a block somebody else is already holding.
# The block it gets instead keeps the customer working, which is the whole
# reason a stride is a preference and not a rule.
sendip 100.64.1.20 7001
sendip 100.64.1.30 7001
for sp in 7002 7003 7004 7005; do sendip 100.64.1.20 $sp; done
SUB_FELL=$(vppctl show bpa subscriber 100.64.1.20)
printf '%s\n' "$SUB_FELL" | sed 's/^/    /'
BLOCKS_OUT=$(vppctl show bpa blocks)
printf '%s\n' "$BLOCKS_OUT" | sed 's/^/    /'
STRIDE_LINE=$(printf '%s\n' "$BLOCKS_OUT" | grep "stride 2:")
PLACED=$(printf '%s\n' "$STRIDE_LINE" | sed -n 's/.*stride 2: \([0-9]*\) extensions placed.*/\1/p')
FELL=$(printf '%s\n' "$STRIDE_LINE" | sed -n 's/.*placed where it says, \([0-9]*\) fell back.*/\1/p')

echo
echo "  -- and every block the subscriber holds is in the log --"
# The records have to leave the box; the collector is on the other side of a
# wire and decodes what arrived.
wait $COL_PID 2>/dev/null
vppctl show bpa logging | sed -n '1,6p' | sed 's/^/    /'
COL=$(cat /tmp/collector.out)
printf '%s\n' "$COL" | grep -E "ALLOCATION +100\.64\.1\.10" | sed 's/^/    /'
ALLOC_1=$(printf '%s\n' "$COL" \
  | grep -cE "ALLOCATION +100\.64\.1\.10 -> ${STR_IP1} portas ${STR_LO1}-${STR_HI1}")
ALLOC_2=$(printf '%s\n' "$COL" \
  | grep -cE "ALLOCATION +100\.64\.1\.10 -> ${STR_IP2} portas ${STR_LO2}-")
ALLOC_ANY=$(printf '%s\n' "$COL" | grep -cE "ALLOCATION +100\.64\.1\.10")

echo "=========== VERDICT ==========="
expect "the subscriber filled its first block and got a second" \
  test "${EXTS:-0}" -ge 1
expect "eight sessions fit in two four-port blocks" test "${SESS2:-0}" = "8"
expect "the ninth was refused, so the cap is a cap" test -n "${REFUSED:-}"
# "allocations" is a column label "show bpa blocks" prints whatever the pool
# did, so this passed on a pool that had allocated nothing at all - let alone
# extended anything. The count, off the total line: a subscriber that got an
# extension has TWO allocations, its first block and the extension.
echo "  allocations counted: ${ALLOCS:-<none>}   extensions: ${EXTS:-0}"
expect "the extension counted as an allocation of its own" \
  test "${ALLOCS:-0}" -ge 2
# The precondition. Without it "one block afterwards" is also what a box that
# never handed out an extension at all would say, and the assertion below
# would be measuring nothing.
expect "the subscriber really was holding two blocks first" \
  test "${EXT_BEFORE:-0}" -ge 2
# The answer to #216. One block, not two: the extension went back while the
# subscriber was ACTIVE on its primary, which is what makes bpa_dyn_active()
# wrong to call rather than merely unused.
expect "and holds one after coming back with a single flow" \
  test "${EXT_AFTER:-0}" -eq 1
# And it is still translating, so the block that stayed is the right one. A
# subscriber whose primary had been reaped too would also report one block:
# the next one it was given.
expect_in "the block it kept is its own, and it is still translating" \
  "200.150.0.16" "$SUB_AFTER"
# The session outliving the hold-down is what makes the window real. Without
# this the run above could have reached "one block" by the subscriber going
# quiet a second time and being given a fresh one, which is a different box.
expect_not_in "and it never went quiet again while we were watching" \
  "no live state" "$SUB_AFTER"

# --- the stride ---
# The precondition for everything below: two blocks on each pool. Without it
# the gaps are empty strings and every comparison below is comparing nothing.
expect "the subscriber on the plain pool holds two blocks" \
  test "$(printf '%s\n' "$SUB_PLAIN" | grep -c '^  block ')" -eq 2
expect "the subscriber on the strided pool holds two blocks" \
  test "$(printf '%s\n' "$SUB_STRIDE" | grep -c '^  block ')" -eq 2
# The control. A build that ignored block-stride puts the second block in the
# same place on both pools, and this is the assertion that would still be
# green while the one below went red - which is how we know the one below is
# measuring the setting and not the allocator's habits.
expect "with no stride the box put the second block next to the first" \
  test "${GAP_PLAIN:-0}" -eq "${BLOCKSZ:-0}"
# The promise, in the operator's own words: n blocks along. The stride is the
# only number this file supplies; the block size and both port ranges come
# off the box.
expect "with stride 2 it put the second block two blocks along instead" \
  test "${GAP_STRIDE:-0}" -eq "$((2 * ${BLOCKSZ:-0}))"
expect "and on the same public address, so the subscriber is one range" \
  test "${STR_IP1:-a}" = "${STR_IP2:-b}"
# A stride is a preference, not a rule: the block it names was taken, and the
# customer got a working block anyway. Both halves are asserted, because a box
# that refused the extension instead would also report a fall-back of zero.
expect "the stride placed a block, and the box says so" \
  test "${PLACED:-0}" -ge 1
expect "and counted the one it could not place" test "${FELL:-0}" -ge 1
expect "the subscriber whose stride was taken still got a block" \
  test "$(printf '%s\n' "$SUB_FELL" | grep -c '^  block ')" -eq 2
# THE REQUIREMENT THE FEATURE EXISTS UNDER. A block the log does not record
# cannot be attributed to anybody, so each of the two blocks has to arrive at
# the collector as its own natEvent 16 naming its own port range - the strided
# one included, which is the one a wrong implementation would log as the
# range the allocator would have given.
expect "the first block arrived at the collector as its own record" \
  test "${ALLOC_1:-0}" -ge 1
expect "the strided block arrived as its own record, at its own ports" \
  test "${ALLOC_2:-0}" -ge 1
expect "and that is one record per block, not one for the pair" \
  test "${ALLOC_ANY:-0}" -eq 2
verdict
