#!/bin/sh
# How often a subscriber's SECOND block lands on its OWN public address (#226)
#
# Under `policy interleaved` the next block index is the next PUBLIC ADDRESS.
# An extension taken off the free stack therefore puts one customer on two
# public addresses at once in the ordinary case - a household that fills its
# block and is given another - and that is what this file measured when it was
# written: 4 of 4 subscribers split, at four public addresses and at sixteen.
#
# #226 changed the allocator. bpa_dyn_acquire_extra() now walks the blocks of
# the subscriber's own public address before it pops the free stack, so the
# thing this file was written to record is a defect the box no longer has.
# What it measures now is the REPAIR, and it measures it as a RATE.
#
# WHY A RATE AND NOT A GUARANTEE, because that is the whole design of the
# check. The product's claim is a PREFERENCE: the allocator looks for a free
# block on the subscriber's address, and when that address has nothing free it
# falls back to the stack rather than refusing the extension. That fall-back is
# deliberate and the alternative was measured - turning the preference into a
# rule costs a customer their traffic instead of costing them a second address.
# So a lab asserting 100% would go red the first time a pool ran tight, on a
# box doing exactly what it was designed to do, and a red that means "the pool
# is busy" is a red nobody can act on.
#
# The assertions below are therefore FLOORS, each against ONE STATED ARRIVAL
# PATTERN on a pool of a stated shape, and every floor is a number this file
# was seen to produce on a running box rather than one computed from the
# allocator's arithmetic. An expected value computed by the function under test
# is the fourth way a mutation lies (TESTING.md), and the arithmetic here
# - pairing needs a gap that is a multiple of the address count - is exactly
# the arithmetic that would be doing the computing.
#
# WHAT MAKES THE FLOORS WORTH READING, and it is the guard beside each one.
# "The extension landed on the subscriber's own address" is true for free on a
# pool with one public address, and nearly free on a pool where every
# subscriber sits on the same one. It is also true for free when there was no
# extension at all. So each pool asserts three things and not one:
#
#   - every subscriber actually received a second block, so the rate has a
#     denominator that is not zero;
#   - the subscribers' PRIMARY blocks are spread over more than one public
#     address, so the box had somewhere else to put the extension and had to
#     choose;
#   - and then the rate.
#
# The same shape lab/detreserve.sh carries, for the same reason: its own
# one-address pool made the preference unfalsifiable and the check stayed green
# with the preference deleted.
#
# TWO ARRIVAL PATTERNS AND TWO POOL SIZES, because the defect was a function of
# both. Serial - one subscriber fills its block before the next arrives - never
# paired before, at any address count. Parallel - everybody arrives, then
# everybody fills - is the busy-hour shape, and the one that has to be arranged
# with care: four subscribers arriving over four public addresses realign onto
# their own address by accident, and this file's own parallel pools passed with
# the preference deleted until six arrived and four extended. count_pair()
# carries that measurement where the arrangement is made.
#
# SINGLE-THREADED BY CONSTRUCTION, and that is a limit rather than an
# oversight. Every count here depends on which blocks the free list hands out
# in what order, and with workers the free list is sharded: a subscriber draws
# only from its own worker's slice, so the gap between two blocks is n_workers
# indices rather than one and the subscribers below would not even share a
# slice. Run with LAB_WORKERS it would still print numbers, and they would be
# numbers about a different arrangement than the one it claims to measure -
# which is the defect lab/extblocks.sh had and this file is not going to
# repeat. The worker counts are covered where they can be swept rather than
# sampled: test/test_bpa_dyn.c drives the real allocator over 1 to 8 workers.
#
# The line below is the machine-readable half of that, because the paragraph
# above is a sentence and the list it talks about is in another file. Nothing
# compared the two, so adding this lab to BOTH_WAYS would have left a comment
# saying it was not there. test/test_run_all_labs.sh reads the marker.
# NOT-BOTH-WAYS: the free list is sharded per worker, so every gap this lab counts would be about a different arrangement
#
# NO OFF MODE, and therefore no OFF-RED-AT-LEAST floor. An off run needs a
# switch the operator has - `reserve 0`, `BPA_RTSP_OFF=1`, an ALG that can be
# turned off - and the preference is not one: it is how the allocator places a
# block, with no configuration reaching it. A mode argument invented for this
# file would be a switch in the PRODUCT that exists only so a lab can flip it,
# which is a worse thing to ship than a lab with no off run.
#
# NO-OFF-RUN: the preference is how the allocator places a block, with no configuration reaching it, so an off mode would be a switch in the product that exists only for this lab to flip; the control is the mutation recorded below
#
# So the control is a MUTATION and not a mode, and it was run rather than
# described. `bpa_dyn_acquire_extra()`'s preference block reduced to `if (0)`,
# rebuilt in the container, this file re-run:
#
#   a4  (serial,   4 addresses)   0 of 4 paired
#   b4  (parallel, 4 addresses)   0 of 4 paired
#   a16 (serial,  16 addresses)   0 of 4 paired
#   b16 (parallel,16 addresses)   0 of 4 paired
#   over all four pools           0 of 16, against 16 of 16 with it in
#   LAB FAILED: 14 check(s)
#
# The fourteen that stayed green are the nine guards, "the log still records
# each block as its own allocation" - which was never the complaint and is
# true either way - and the two strided-control checks. None of them is about
# the preference, and counting them as evidence is how a floor gets set too
# high. It is also how the accidental alignment above was found: `if (0)` is
# only a control if the arrangement makes the box choose.
#
# AND IT IS IN THE SUITE. It was in `NOT_LABS` for as long as it pinned the
# allocator this replaced, which was the right place for a file that was red by
# construction: a lab nobody can run is neither a regression test nor a record.
# It now measures the box as it is, its green has been seen, and it goes red
# for the mechanism rather than for the weather. 104 seconds on the machine it
# was rewritten on - it was 162 before the collector was told when to stop
# instead of being waited out - which puts it among the labs the suite already
# runs rather than near the one kept out of it for taking four minutes.
set -u
. /src/lab/lib.sh
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp /var/log/prisma-routing
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
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl set ip neighbor pg0 100.64.3.253 02:00:00:00:00:01
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa timeouts udp 600 tcp-syn 600 >/dev/null

# Four pools of the same shape. Two public address counts, two arrival
# patterns, and nothing else different between them.
#   1024-1039 / block 4 -> 4 blocks per address
vppctl bpa pool a4  inside 100.64.0.0/25   outside 200.150.0.16/30 \
    ports 1024-1039 block 4 mode dynamic max-blocks 2
vppctl bpa pool b4  inside 100.64.0.128/25 outside 200.150.0.32/30 \
    ports 1024-1039 block 4 mode dynamic max-blocks 2
vppctl bpa pool a16 inside 100.64.1.0/25   outside 200.150.1.0/28 \
    ports 1024-1039 block 4 mode dynamic max-blocks 2
vppctl bpa pool b16 inside 100.64.1.128/25 outside 200.150.2.0/28 \
    ports 1024-1039 block 4 mode dynamic max-blocks 2

HX="python3 /src/src/prismash/prismash.py"
$HX -c "configure" -c "nat" -c "cgnat" -c "logging udp 10.0.0.2 src 10.0.0.1 port 4739" \
    -c "logging enable" -c "commit" >/dev/null 2>&1
# THE COLLECTOR IS TOLD WHEN TO STOP, and the number behind it is a ceiling
# for a run nobody is watching rather than a guess at how long the box takes.
# A budget small enough to be reached expires on a loaded machine, the records
# arrive after the window closed, and section 4 reports a log that is missing
# them - a product defect the product has not got. That is #342, and on
# 2026-09-15 it took three labs red while the same machine ran the suite under
# gcov. test/test_lab_collector_window.py holds the rule.
COL_STOP=/tmp/collector.stop
rm -f "$COL_STOP"
ip netns exec peer python3 /src/tools/ipfix_collector.py 4739 300 \
  "$COL_STOP" \
  > /tmp/collector.out 2>&1 &
COL_PID=$!
sleep 2

send() {   # inside address, source port
  vppctl packet-generator delete c >/dev/null 2>&1
  vppctl packet-generator new "name c limit 1 node ip4-input interface pg0 \
      size 60-60 data { UDP: $1 -> 8.8.8.8 UDP: $2 -> 53 incrementing 8 }"
  vppctl packet-generator enable >/dev/null; sleep 0.3
  vppctl packet-generator delete c >/dev/null 2>&1
}

# Every block a subscriber holds, as "<public address> <first port> <last port>",
# read off the box rather than worked out here.
blocks_of () {
  printf '%s\n' "$1" \
    | sed -n 's/^  block [0-9]*  \([0-9.]*\)  ports \([0-9]*\)-\([0-9]*\).*/\1 \2 \3/p'
}

# What the BOX says about the same property, per pool: the two counters
# bpa_dyn_acquire_extra() keeps and `show bpa pool` prints. Read alongside what
# this file counts off the subscribers, because they are two different readings
# of one fact and a disagreement between them is worth more than either.
pool_paired () {   # <pool> -> "<paired> <extensions>"
  printf '%s\n' "$BLOCKS" | awk -v p="pool $1 " '
    index($0, p) == 1 { inp = 1; next }
    /^pool /          { inp = 0 }
    inp && /RFC 4787 REQ-2 paired:/ {
      for (i = 1; i <= NF; i++)
        if ($i == "paired:") { print $(i+1), $(i+3); exit }
    }'
}

# Runs one arrival pattern over a list of subscribers and counts how many ended
# up with their extension on the SAME public address as their primary.
#
# It arranges subscribers and it sends traffic. It does not choose a block, a
# public address or an order for the allocator: the only thing it decides is
# WHEN each subscriber's fifth flow arrives, which is the one fact the arrival
# pattern is about.
count_pair () {   # <label> <pattern: serial|parallel> <subscribers...>
  # PAIR_ARRIVE_ONLY: subscribers that take a primary block and never fill it.
  # Only the parallel pattern uses them; see the comment there for why.
  _label=$1; _pat=$2; shift 2
  _own=0; _ext=0; _n=0; _prim=""
  if [ "$_pat" = serial ]; then
    # One subscriber at a time: it fills its block and extends before the next
    # one has asked for anything. A pool filling up outside busy hour, and the
    # case that never paired before #226 at any address count - the gap between
    # the two blocks is 1, and 1 is a multiple of no address count above one.
    for _s in "$@"; do
      for _p in 5001 5002 5003 5004 5005; do send "$_s" $_p; done
    done
  else
    # Everybody arrives first, then everybody fills. Busy hour, and the pattern
    # whose gap is the number of ARRIVALS - which is why the caller names two
    # subscribers in PAIR_ARRIVE_ONLY that arrive and never fill.
    #
    # THAT IS NOT DECORATION, AND IT WAS MEASURED. With four arrivals over four
    # public addresses the gap is four, four is a multiple of the address
    # count, and every extension realigns onto its own address BY ACCIDENT -
    # so this pool paired 4 of 4 with the preference deleted from
    # bpa_dyn_acquire_extra() and its floor stayed green. A check that passes
    # with the mechanism removed is not evidence of the mechanism. It is the
    # same trap issue #226 found in its own `b4` pool and in one of
    # test/test_bpa_dyn.c's scenarios, arriving here a third time.
    #
    # Six arrive and four extend, so the gap is six at both pool sizes and the
    # box has to choose rather than be handed the right answer. The mutation
    # run says it works: 0 of 4 here with the preference gone.
    for _s in "$@" $PAIR_ARRIVE_ONLY; do send "$_s" 5001; done
    for _s in "$@"; do
      for _p in 5002 5003 5004 5005; do send "$_s" $_p; done
    done
  fi
  echo "  $_label ($_pat):"
  for _s in "$@"; do
    _sub=$(vppctl show bpa subscriber "$_s")
    _nb=$(printf '%s\n' "$_sub" | grep -c '^  block ')
    _a=$(blocks_of "$_sub" | sed -n 1p | cut -d' ' -f1)
    _b=$(blocks_of "$_sub" | sed -n 2p | cut -d' ' -f1)
    _pa=$(blocks_of "$_sub" | sed -n 1p | cut -d' ' -f2)
    _pb=$(blocks_of "$_sub" | sed -n 2p | cut -d' ' -f2)
    _n=$((_n + 1)); _prim="$_prim $_a"
    if [ "$_nb" -lt 2 ]; then
      _v="NO EXTENSION AT ALL"
    else
      _ext=$((_ext + 1))
      if [ "$_a" = "$_b" ]; then
        _own=$((_own + 1)); _v="own address"
      else
        _v="TWO ADDRESSES"
      fi
    fi
    printf '    %-16s %s:%s and %s:%s   %s\n' \
      "$_s" "${_a:--}" "${_pa:--}" "${_b:--}" "${_pb:--}" "$_v"
  done
  PAIR_OWN=$_own; PAIR_EXT=$_ext; PAIR_N=$_n
  PAIR_ADDRS=$(printf '%s\n' $_prim | sort -u | grep -c .)
  PAIR_RATE=0
  [ "$_ext" -gt 0 ] && PAIR_RATE=$((_own * 100 / _ext))
  echo "    -> $_own of $_ext extension(s) on the subscriber's own public" \
       "address ($PAIR_RATE%), over $PAIR_ADDRS public address(es) in use"
}

echo "=========== 1. four public addresses ==========="
PAIR_ARRIVE_ONLY=""
count_pair "pool a4,  4 public addresses" serial \
  100.64.0.5 100.64.0.6 100.64.0.7 100.64.0.8
A4_OWN=$PAIR_OWN; A4_EXT=$PAIR_EXT; A4_N=$PAIR_N; A4_ADDRS=$PAIR_ADDRS
A4_RATE=$PAIR_RATE
echo
PAIR_ARRIVE_ONLY="100.64.0.137 100.64.0.138"
count_pair "pool b4,  4 public addresses" parallel \
  100.64.0.133 100.64.0.134 100.64.0.135 100.64.0.136
B4_OWN=$PAIR_OWN; B4_EXT=$PAIR_EXT; B4_N=$PAIR_N; B4_ADDRS=$PAIR_ADDRS
B4_RATE=$PAIR_RATE

echo
echo "=========== 2. sixteen public addresses ==========="
PAIR_ARRIVE_ONLY=""
count_pair "pool a16, 16 public addresses" serial \
  100.64.1.5 100.64.1.6 100.64.1.7 100.64.1.8
A16_OWN=$PAIR_OWN; A16_EXT=$PAIR_EXT; A16_N=$PAIR_N; A16_ADDRS=$PAIR_ADDRS
A16_RATE=$PAIR_RATE
echo
PAIR_ARRIVE_ONLY="100.64.1.137 100.64.1.138"
count_pair "pool b16, 16 public addresses" parallel \
  100.64.1.133 100.64.1.134 100.64.1.135 100.64.1.136
B16_OWN=$PAIR_OWN; B16_EXT=$PAIR_EXT; B16_N=$PAIR_N; B16_ADDRS=$PAIR_ADDRS
B16_RATE=$PAIR_RATE

TOT_OWN=$((A4_OWN + B4_OWN + A16_OWN + B16_OWN))
TOT_EXT=$((A4_EXT + B4_EXT + A16_EXT + B16_EXT))
TOT_N=$((A4_N + B4_N + A16_N + B16_N))
TOT_RATE=0
[ "$TOT_EXT" -gt 0 ] && TOT_RATE=$((TOT_OWN * 100 / TOT_EXT))
echo
echo "  over all four pools: $TOT_OWN of $TOT_EXT extension(s) paired" \
     "($TOT_RATE%), $TOT_N subscriber(s)"

echo
echo "=========== 3. the same property, counted by the BOX ==========="
# bpa_dyn_acquire_extra() keeps its own two counters and `show bpa pool` prints
# them as "RFC 4787 REQ-2 paired: X of Y". They are what an operator reads
# instead of the claim, so a lab that measured the property off the subscriber
# dump and never looked at the number the operator is given would be leaving
# the operator-facing half unchecked - and a counter nothing reads is a counter
# that can drift from the thing it counts.
# Read ONCE and parsed from the one text, so every pool's number below is
# from the same instant. Printed whole as well: it is what an operator is
# actually given, and a lab that parsed it without showing it would leave the
# reader unable to check the parse.
BLOCKS=$(vppctl show bpa blocks)
printf '%s\n' "$BLOCKS" | sed 's/^/  /'
echo
BOX_OWN=0; BOX_EXT=0
for _p in a4 b4 a16 b16; do
  _r=$(pool_paired "$_p")
  _o=$(printf '%s' "$_r" | cut -d' ' -f1); _e=$(printf '%s' "$_r" | cut -d' ' -f2)
  printf '  pool %-4s the box says %s of %s extension(s) paired\n' \
    "$_p" "${_o:--}" "${_e:--}"
  BOX_OWN=$((BOX_OWN + ${_o:-0})); BOX_EXT=$((BOX_EXT + ${_e:-0}))
done
echo "  box total: $BOX_OWN of $BOX_EXT; this lab counted $TOT_OWN of $TOT_EXT"

echo
echo "=========== 4. what the retention log now records ==========="
# Two natEvent 16 for one subscriber, as before - a block the log does not
# record cannot be attributed to anybody, and that was never the complaint.
# What changed is the SHAPE. Before #226 the second record had the same port
# numbers on a different address, which is two rows an investigator reads as a
# duplicate. Paired, the two records name ONE address and two ranges that abut,
# which reads as one holding.
#
# The box has done everything sections 1 and 2 ask of it, so there is nothing
# more to hear. A moment first for the last records to cross the wire: the
# collector drains its receive buffer before it leaves, so this only has to
# cover flight time.
sleep 2
: > "$COL_STOP"
wait $COL_PID 2>/dev/null
COL=$(cat /tmp/collector.out)
printf '%s\n' "$COL" | grep -E " ALLOCATION +100\.64\.0\.5 " | sed 's/^/  /'
LOG_ADDRS=$(printf '%s\n' "$COL" | grep -E " ALLOCATION +100\.64\.0\.5 " \
            | sed -n 's/.*-> \([0-9.]*\) portas.*/\1/p' | sort -u | grep -c .)
LOG_RECS=$(printf '%s\n' "$COL" | grep -cE " ALLOCATION +100\.64\.0\.5 ")
echo "  $LOG_RECS records naming $LOG_ADDRS distinct public address(es)"
# Adjacency read off the records rather than off the block size: the two ranges
# abut when the second starts one port after the first ends.
LOG_HI1=$(printf '%s\n' "$COL" | grep -E " ALLOCATION +100\.64\.0\.5 " \
          | sed -n 's/.*portas [0-9]*-\([0-9]*\).*/\1/p' | sort -n | sed -n 1p)
LOG_LO2=$(printf '%s\n' "$COL" | grep -E " ALLOCATION +100\.64\.0\.5 " \
          | sed -n 's/.*portas \([0-9]*\)-[0-9]*.*/\1/p' | sort -n | sed -n 2p)
LOG_ABUT=0
[ -n "${LOG_HI1:-}" ] && [ -n "${LOG_LO2:-}" ] \
  && [ "$LOG_LO2" -eq $((LOG_HI1 + 1)) ] && LOG_ABUT=1
echo "  the second range starts at ${LOG_LO2:--}, the first ends at ${LOG_HI1:--}:" \
     "adjacent=$LOG_ABUT"

echo
echo "=========== 5. what the pairing buys: a media pair the ALG can open ==========="
# bpa_alg_reserve_pair() will only take a media pair from a block on the SAME
# public address as the signalling session: the SDP carries ports and the peer
# takes the address from the connection it answers, so a pair on another
# address would be announced and never reachable.
#
#   if (sb->blk.ip_pub != want_ip_pub)
#     continue;
#
# That is why the split was expensive rather than cosmetic, and it is why this
# section is the end-to-end half of the measurement above: a rate is a number,
# and this is the customer the number is about. A subscriber whose first block
# is full but for one port needs the PAIR to come from its extension - and the
# ALG can only use that extension if the extension is on its address.
#
# Two pools, identical but for the stride, driven through the same sequence.
# The strided one is the control from the other direction: `block-stride 1` is
# the operator saying WHERE the second block goes, and it reached the same
# outcome before #226 existed. Both must now be clean, and if the strided pool
# ever went red while the plain one passed, the fault would be in the ALG or in
# the sequence rather than in the preference.
vppctl bpa pool sipa inside 100.64.2.0/25 outside 200.150.3.16/30 \
    ports 1024-1039 block 4 mode dynamic max-blocks 2
vppctl bpa pool sipb inside 100.64.2.128/25 outside 200.150.3.32/30 \
    ports 1024-1039 block 4 mode dynamic max-blocks 2 block-stride 1
vppctl ip route add 100.64.2.0/24 via 100.64.3.253 pg0 2>/dev/null
vppctl set ip neighbor pg0 100.64.2.5 02:00:00:00:02:05
vppctl set ip neighbor pg0 100.64.2.133 02:00:00:00:02:06

shoot() {   # <capture-iface> <pcap> <hex>
  rm -f "$2"
  vppctl packet-generator delete p >/dev/null 2>&1
  vppctl packet-generator capture "$1" pcap "$2" count 1 >/dev/null
  L=$(( ${#3} / 2 - 1 ))
  IN=pg0; [ "$1" = pg0 ] && IN=pg1
  vppctl packet-generator new "name p limit 1 node ip4-input interface $IN \
      size ${L}-${L} data { hex $3 }"
  vppctl packet-generator enable >/dev/null; sleep 1
  vppctl packet-generator delete p >/dev/null 2>&1
}
sendtcp() {   # inside address, source port - a SYN, so it leaves on tcp-syn
  vppctl packet-generator delete c >/dev/null 2>&1
  vppctl packet-generator new "name c limit 1 node ip4-input interface pg0 \
      size 60-60 data { TCP: $1 -> 8.8.8.8 TCP: $2 -> 80 incrementing 8 }"
  vppctl packet-generator enable >/dev/null; sleep 0.3
  vppctl packet-generator delete c >/dev/null 2>&1
}
errc() {
  _v=$(vppctl show errors | grep -F "$1" | awk '{print $1}' | head -1)
  echo "${_v:-0}"
}

# The private ports are inside the pool's own port range, so port preservation
# puts each session on a known offset and the block fills in a known order.
# Offset 1 is a TCP SYN, which is the one that will be let go.
sip_seq () {   # <subscriber>
  send "$1" 1024
  sendtcp "$1" 1025
  send "$1" 1026
  send "$1" 1027
  send "$1" 3000       # the block is full: this one takes the extension
}
sip_seq 100.64.2.5
sip_seq 100.64.2.133
SIPA_SUB=$(vppctl show bpa subscriber 100.64.2.5)
SIPB_SUB=$(vppctl show bpa subscriber 100.64.2.133)
echo "  no stride:"
printf '%s\n' "$SIPA_SUB" | sed 's/^/    /'
echo "  stride 1:"
printf '%s\n' "$SIPB_SUB" | sed 's/^/    /'
SIPA_NBLK=$(printf '%s\n' "$SIPA_SUB" | grep -c '^  block ')
SIPA_IP1=$(blocks_of "$SIPA_SUB" | sed -n 1p | cut -d' ' -f1)
SIPA_IP2=$(blocks_of "$SIPA_SUB" | sed -n 2p | cut -d' ' -f1)
SIPA_LO2=$(blocks_of "$SIPA_SUB" | sed -n 2p | cut -d' ' -f2)
SIPA_HI2=$(blocks_of "$SIPA_SUB" | sed -n 2p | cut -d' ' -f3)
SIPA_PAIRED=0
[ "$SIPA_NBLK" -ge 2 ] && [ "$SIPA_IP1" = "$SIPA_IP2" ] && SIPA_PAIRED=1

# One port back on the FIRST block, and only one: enough for the signalling
# session, not enough for a media pair. That is the state in which the ALG has
# to reach into the extension, and therefore the state that answers whether the
# extension is usable at all.
vppctl bpa timeouts tcp-syn 2 >/dev/null
sleep 5
vppctl bpa timeouts tcp-syn 600 >/dev/null
# Read back rather than assumed: if the SYN did not expire, the first block
# still has no free port, the INVITE's own session is refused, and the section
# below would be measuring a different thing entirely.
SIPA_FREE1=$(printf '%s\n' "$(vppctl show bpa subscriber 100.64.2.5)" \
             | sed -n 's/^  block 0 .*, \([0-9]*\) free.*/\1/p')
echo "  after the SYN expired, the first block has ${SIPA_FREE1:--} free port(s)"

SDP_A='v=0|o=- 1 1 IN IP4 100.64.2.5|s=-|c=IN IP4 100.64.2.5|t=0 0|m=audio 8000 RTP/AVP 0|a=rtpmap:0 PCMU/8000'
HDR_A='Via: SIP/2.0/UDP 100.64.2.5:5060;branch=z9hG4bKnashds8|Max-Forwards: 70|From: "Ana" <sip:1000@8.8.8.8>;tag=1|To: <sip:2000@8.8.8.8>|Call-ID: a@100.64.2.5|CSeq: 1 INVITE|Contact: <sip:1000@100.64.2.5:5060>'
SDP_B=$(printf '%s' "$SDP_A" | sed 's/100\.64\.2\.5/100.64.2.133/g')
HDR_B=$(printf '%s' "$HDR_A" | sed 's/100\.64\.2\.5/100.64.2.133/g')

BEFORE=$(errc "SIP media port pair unavailable")
HEX=$(python3 /src/lab/mksip.py 100.64.2.5 8.8.8.8 5060 5060 \
        --start 'INVITE sip:2000@8.8.8.8 SIP/2.0' --headers "$HDR_A" --body "$SDP_A")
shoot pg1 /tmp/invite_a.pcap "$HEX"
AFTER_A=$(errc "SIP media port pair unavailable")

HEX=$(python3 /src/lab/mksip.py 100.64.2.133 8.8.8.8 5060 5060 \
        --start 'INVITE sip:2000@8.8.8.8 SIP/2.0' --headers "$HDR_B" --body "$SDP_B")
shoot pg1 /tmp/invite_b.pcap "$HEX"
AFTER_B=$(errc "SIP media port pair unavailable")

echo
echo "  refusals of a media pair: before $BEFORE, after the paired subscriber"
echo "  $AFTER_A, after the strided one $AFTER_B"
echo "  the INVITE the paired subscriber's phone sent, as it left the box:"
INV_A=$(python3 /src/lab/checksip.py /tmp/invite_a.pcap --contains 'c=IN IP4' 2>&1)
printf '%s\n' "$INV_A" | sed 's/^/    /'
# The media port the box announced, read out of the packet that left. Compared
# below against the EXTENSION block's range as the box reports it - two
# readings of the box, and no arithmetic of this lab's own. It is the only
# statement here that says the pair came from the extension rather than from
# somewhere the ALG happened to find room.
SDP_MEDIA=$(printf '%s\n' "$INV_A" | sed -n 's/.*SDP m= port: *\([0-9]*\).*/\1/p' | head -1)
MEDIA_IN_EXT=0
[ -n "${SDP_MEDIA:-}" ] && [ -n "${SIPA_LO2:-}" ] && [ -n "${SIPA_HI2:-}" ] \
  && [ "$SDP_MEDIA" -ge "$SIPA_LO2" ] && [ "$SDP_MEDIA" -le "$SIPA_HI2" ] \
  && MEDIA_IN_EXT=1
echo "  the SDP announced media port ${SDP_MEDIA:--}, and the extension block" \
     "is ${SIPA_IP2:--} ${SIPA_LO2:--}-${SIPA_HI2:--}: inside=$MEDIA_IN_EXT"
# The positive statement, and it is the one that matters: the SDP must carry
# the subscriber's PUBLIC address. "No private address in it" is true of a
# packet that never arrived.
PAIRED_PUBLIC=0
python3 /src/lab/checksip.py /tmp/invite_a.pcap \
  --contains "c=IN IP4 ${SIPA_IP1:-none}" >/dev/null 2>&1 && PAIRED_PUBLIC=1
PAIRED_LEAKS=0
python3 /src/lab/checksip.py /tmp/invite_a.pcap \
  --contains 'c=IN IP4 100.64.2.5' >/dev/null 2>&1 && PAIRED_LEAKS=1
STRIDE_LEAKS=0
python3 /src/lab/checksip.py /tmp/invite_b.pcap \
  --contains 'c=IN IP4 100.64.2.133' >/dev/null 2>&1 && STRIDE_LEAKS=1
echo "  the SDP names the subscriber's public address ${SIPA_IP1:--}:" \
     "$PAIRED_PUBLIC"
echo "  private address still in the SDP: paired=$PAIRED_LEAKS" \
     "strided=$STRIDE_LEAKS"

echo "=========== VERDICT ==========="
# EVERY NUMBER BELOW WAS SEEN, and the run that produced it is named here so a
# reader does not have to take that on trust: `sh tools/lab.sh crossaddr` on
# 2026-09-15, single-threaded, on the tree this file is committed with. It
# printed 4 of 4 extensions paired in EVERY one of the four pools, 16 of 16
# over all of them, two log records naming one address with adjacent ranges,
# and no media pair refused.
#
# THE FLOORS SIT ONE BELOW WHAT WAS MEASURED, and that is the judgement in this
# file rather than a measurement. The product's claim is a preference with a
# deliberate fall-back, so a run that paired 3 of these 4 subscribers is a box
# working as designed and a run that paired none is a box that lost the
# mechanism. One below is where those two stop being the same verdict.
#
# TWO LEVELS, because one would miss a real regression either way. A per-pool
# floor alone lets all four pools drop one each - four split customers, and
# every check green. An aggregate floor alone lets one arrival pattern fail
# completely while the other three carry it. Together: each pattern must pair
# at least 3 of its 4, AND the run as a whole may lose at most one.
#
# What a 100% floor would have cost is the reason there is a floor at all: it
# goes red the first time a pool runs tight, which is a red saying "the pool is
# busy" and not "the box is wrong", and a check nobody can act on is a check
# that gets deleted.

# 1. The rate, per arrival pattern, each behind the two guards that make it
#    mean anything. The guards are not decoration: "the extension is on the
#    subscriber's own address" is true for free when there was no extension,
#    and nearly free when every subscriber sits on one address.
expect "a4: every subscriber that filled its block was given a second one" \
  test "${A4_EXT:-0}" -eq "${A4_N:-0}" -a "${A4_N:-0}" -eq 4
expect "a4: and their primaries are spread over more than one public address" \
  test "${A4_ADDRS:-0}" -ge 2
expect "a4 (serial, 4 addresses): at least 3 of 4 extensions paired" \
  test "${A4_OWN:-0}" -ge 3

expect "b4: every subscriber that filled its block was given a second one" \
  test "${B4_EXT:-0}" -eq "${B4_N:-0}" -a "${B4_N:-0}" -eq 4
expect "b4: and their primaries are spread over more than one public address" \
  test "${B4_ADDRS:-0}" -ge 2
expect "b4 (parallel, 4 addresses): at least 3 of 4 extensions paired" \
  test "${B4_OWN:-0}" -ge 3

expect "a16: every subscriber that filled its block was given a second one" \
  test "${A16_EXT:-0}" -eq "${A16_N:-0}" -a "${A16_N:-0}" -eq 4
expect "a16: and their primaries are spread over more than one public address" \
  test "${A16_ADDRS:-0}" -ge 2
# The one the old allocator could never get right by luck: the serial gap is 1,
# and 1 is a multiple of no address count above one. A bigger pool made it
# worse rather than better, so sixteen addresses is where a repair has to be
# shown and not only four.
expect "a16 (serial, 16 addresses): at least 3 of 4 extensions paired" \
  test "${A16_OWN:-0}" -ge 3

expect "b16: every subscriber that filled its block was given a second one" \
  test "${B16_EXT:-0}" -eq "${B16_N:-0}" -a "${B16_N:-0}" -eq 4
expect "b16: and their primaries are spread over more than one public address" \
  test "${B16_ADDRS:-0}" -ge 2
expect "b16 (parallel, 16 addresses): at least 3 of 4 extensions paired" \
  test "${B16_OWN:-0}" -ge 3

# 2. The run as a whole. 16 extensions were handed out and 16 paired; the floor
#    allows one to fall back, which is the behaviour the product keeps on
#    purpose, and refuses two.
expect "sixteen extensions were handed out across the four pools" \
  test "${TOT_EXT:-0}" -eq 16
expect "and at least fifteen of them landed on the holder's own address" \
  test "${TOT_OWN:-0}" -ge 15

# 3. The counter the OPERATOR reads says the same thing this lab counted off
#    the subscriber dump. Two readings of one fact: if they ever disagree, an
#    operator sizing against `show bpa blocks` is being told something the
#    blocks do not say, and that is worth more than either number alone.
expect "the box counted extensions of its own, so there is something to compare" \
  test "${BOX_EXT:-0}" -gt 0
expect "and its paired counter agrees with the blocks the subscribers hold" \
  test "${BOX_OWN:-0}" -eq "${TOT_OWN:-0}" -a "${BOX_EXT:-0}" -eq "${TOT_EXT:-0}"

# 4. The retention log. Two records was never the complaint and still is not -
#    a block the log does not record cannot be attributed to anybody. What
#    changed is that they name ONE address and two ranges that abut, which is
#    one holding rather than two rows an investigator reads as a duplicate.
expect "the log still records each block as its own allocation" \
  test "${LOG_RECS:-0}" -eq 2
expect "and both name the same public address" \
  test "${LOG_ADDRS:-0}" -eq 1
expect "and the second port range abuts the first, so it reads as one holding" \
  test "${LOG_ABUT:-0}" -eq 1

# 5. What the pairing buys, end to end. The rate above is a number; this is the
#    customer it is about. Guarded twice, because both guards are ways this
#    could pass while proving nothing: with no extension there is nothing for
#    the ALG to reach into, and with a first block that is not nearly full the
#    pair never has to come from the extension at all.
expect "the SIP subscriber was given a second block" \
  test "${SIPA_NBLK:-0}" -eq 2
expect "and its first block has exactly one free port, so a PAIR cannot fit there" \
  test "${SIPA_FREE1:-0}" -eq 1
expect "the extension is on the subscriber's own public address" \
  test "${SIPA_PAIRED:-0}" -eq 1
expect "so no media pair was refused" \
  test "${AFTER_A:-1}" -eq "${BEFORE:-0}"
expect "and the media port announced came out of the extension block" \
  test "${MEDIA_IN_EXT:-0}" -eq 1
expect "the SDP that left names the subscriber's public address" \
  test "${PAIRED_PUBLIC:-0}" -eq 1
expect "and carries no private address at all" \
  test "${PAIRED_LEAKS:-1}" -eq 0
# The control from the other direction: `block-stride 1` is the operator saying
# WHERE the second block goes, and it reached this outcome before the
# preference existed. It must still. A red here beside a green above would put
# the fault in the ALG or in the sequence rather than in the preference.
expect "the same sequence with block-stride 1 refuses nothing either" \
  test "${AFTER_B:-1}" -eq "${AFTER_A:-0}"
expect "and that phone's SDP carries no private address either" \
  test "${STRIDE_LEAKS:-1}" -eq 0
verdict
