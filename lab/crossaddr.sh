#!/bin/sh
# Where a subscriber's SECOND block lands today, under `policy interleaved`.
#
# Nobody chose this behaviour. An extension is whatever comes off the free
# list, and under interleaved the next block index is the next PUBLIC ADDRESS
# - so a subscriber that fills its block normally ends up sending from two
# addresses at once. This file measures how often that happens, what the
# retention log then looks like, and what breaks.
#
# It changes no default. It is the reading behind issue #226.
#
# SINGLE-THREADED BY CONSTRUCTION, and that is a limit rather than an
# oversight. Every count here depends on which blocks the free list hands out
# in what order, and with workers the free list is sharded: a subscriber draws
# only from its own worker's slice, so the gap between two blocks is n_workers
# indices rather than one and the subscribers below would not even share a
# slice. Run with LAB_WORKERS it would still print numbers, and they would be
# numbers about a different arrangement than the one it claims to measure -
# which is the defect lab/extblocks.sh had and this file is not going to
# repeat. It is not in tools/run-all-labs.sh's BOTH_WAYS list for that reason.
set -u
. /src/lab/lib.sh
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp /var/log/hexarouting
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

# Four pools of the same shape. Two public addresses counts, two arrival
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

HX="python3 /src/src/hexash/hexash.py"
$HX -c "configure" -c "cgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739" \
    -c "cgnat logging enable" -c "commit" >/dev/null 2>&1
ip netns exec peer python3 /src/tools/ipfix_collector.py 4739 90 \
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

# The two addresses a subscriber ends up on, read off the box.
addrs_of () {
  vppctl show bpa subscriber "$1" \
    | sed -n 's/^  block [0-9]*  \([0-9.]*\)  ports \([0-9]*\)-.*/\1:\2/p'
}

# Runs one arrival pattern over a list of subscribers and counts how many end
# up with their two blocks on two different public addresses.
count_split () {   # <label> <pattern: serial|parallel> <subscribers...>
  _label=$1; _pat=$2; shift 2
  _same=0; _diff=0
  if [ "$_pat" = serial ]; then
    # One subscriber at a time: it fills its block and extends before the next
    # one has asked for anything. A pool filling up outside busy hour.
    for _s in "$@"; do
      for _p in 5001 5002 5003 5004 5005; do send "$_s" $_p; done
    done
  else
    # Everybody arrives first, then everybody fills. Busy hour.
    for _s in "$@"; do send "$_s" 5001; done
    for _s in "$@"; do
      for _p in 5002 5003 5004 5005; do send "$_s" $_p; done
    done
  fi
  echo "  $_label ($_pat):"
  for _s in "$@"; do
    _a=$(addrs_of "$_s" | sed -n 1p | cut -d: -f1)
    _b=$(addrs_of "$_s" | sed -n 2p | cut -d: -f1)
    _pa=$(addrs_of "$_s" | sed -n 1p | cut -d: -f2)
    _pb=$(addrs_of "$_s" | sed -n 2p | cut -d: -f2)
    if [ "$_a" = "$_b" ]; then
      _same=$((_same + 1)); _v="one address"
    else
      _diff=$((_diff + 1)); _v="TWO ADDRESSES"
    fi
    printf '    %-16s %s:%s and %s:%s   %s\n' "$_s" "$_a" "$_pa" "$_b" "$_pb" "$_v"
  done
  echo "    -> $_diff of $((_same + _diff)) subscribers are on two public addresses"
  SPLIT_DIFF=$_diff; SPLIT_SAME=$_same
}

echo "=========== 1. four public addresses ==========="
count_split "pool a4,  4 public addresses" serial \
  100.64.0.5 100.64.0.6 100.64.0.7 100.64.0.8
A4_DIFF=$SPLIT_DIFF; A4_N=$((SPLIT_DIFF + SPLIT_SAME))
echo
count_split "pool b4,  4 public addresses" parallel \
  100.64.0.133 100.64.0.134 100.64.0.135 100.64.0.136
B4_DIFF=$SPLIT_DIFF; B4_N=$((SPLIT_DIFF + SPLIT_SAME))

echo
echo "=========== 2. sixteen public addresses ==========="
count_split "pool a16, 16 public addresses" serial \
  100.64.1.5 100.64.1.6 100.64.1.7 100.64.1.8
A16_DIFF=$SPLIT_DIFF; A16_N=$((SPLIT_DIFF + SPLIT_SAME))
echo
count_split "pool b16, 16 public addresses" parallel \
  100.64.1.133 100.64.1.134 100.64.1.135 100.64.1.136
B16_DIFF=$SPLIT_DIFF; B16_N=$((SPLIT_DIFF + SPLIT_SAME))

echo
echo "=========== 3. what the retention log records ==========="
# Two natEvent 16 for one subscriber. Under interleaved the second block has
# the SAME port range on a DIFFERENT address, which is the shape an
# investigator has to hold in their head.
wait $COL_PID 2>/dev/null
COL=$(cat /tmp/collector.out)
printf '%s\n' "$COL" | grep -E "ALLOCATION +100\.64\.0\.5 " | sed 's/^/  /'
LOG_ADDRS=$(printf '%s\n' "$COL" | grep -E "ALLOCATION +100\.64\.0\.5 " \
            | sed -n 's/.*-> \([0-9.]*\) portas.*/\1/p' | sort -u | wc -l)
LOG_RECS=$(printf '%s\n' "$COL" | grep -cE "ALLOCATION +100\.64\.0\.5 ")
echo "  $LOG_RECS records naming $LOG_ADDRS distinct public addresses"

echo
echo "=========== 4. what breaks: a media pair the ALG cannot open ==========="
# bpa_alg_reserve_pair() will only take a media pair from a block on the SAME
# public address as the signalling session: the SDP carries ports and the peer
# takes the address from the connection it is answering, so a pair on another
# address would be announced and never reachable. It therefore SKIPS an
# extension on another address - and a subscriber with free ports gets no
# audio.
#
# Two pools, identical but for the stride, driven through the same sequence.
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
echo "  no stride:"
vppctl show bpa subscriber 100.64.2.5 | sed 's/^/    /'
echo "  stride 1:"
vppctl show bpa subscriber 100.64.2.133 | sed 's/^/    /'

# One port back on the FIRST block, and only one: enough for the signalling
# session, not enough for a media pair. That is the state in which the two
# pools answer differently.
vppctl bpa timeouts tcp-syn 2 >/dev/null
sleep 5
vppctl bpa timeouts tcp-syn 600 >/dev/null

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
echo "  refusals of a media pair: before $BEFORE, after the split subscriber"
echo "  $AFTER_A, after the strided one $AFTER_B"
echo "  the INVITE the split subscriber's phone sent, as it left the box:"
python3 /src/lab/checksip.py /tmp/invite_a.pcap --contains 'c=IN IP4' \
  2>&1 | sed 's/^/    /'
SPLIT_LEAKS=0
python3 /src/lab/checksip.py /tmp/invite_a.pcap \
  --contains 'c=IN IP4 100.64.2.5' >/dev/null 2>&1 && SPLIT_LEAKS=1
STRIDE_LEAKS=0
python3 /src/lab/checksip.py /tmp/invite_b.pcap \
  --contains 'c=IN IP4 100.64.2.133' >/dev/null 2>&1 && STRIDE_LEAKS=1
echo "  private address still in the SDP: split=$SPLIT_LEAKS strided=$STRIDE_LEAKS"

echo "=========== VERDICT ==========="
# This lab exists to MEASURE a behaviour nobody chose, so its checks state what
# was measured rather than what ought to be. They go red if the behaviour
# changes, which is the point: the trade has to be decided, not drifted into.
expect "a subscriber filling its block right after taking it lands on a second address" \
  test "${A4_DIFF:-0}" -eq "${A4_N:-0}"
expect "and so does one at sixteen public addresses" \
  test "${A16_DIFF:-0}" -eq "${A16_N:-0}"
expect "the log records the two blocks under two different public addresses" \
  test "${LOG_ADDRS:-0}" -eq 2
expect "and it is two records, so nothing is unattributable" \
  test "${LOG_RECS:-0}" -eq 2
expect "the split subscriber's media pair is refused" \
  test "${AFTER_A:-0}" -gt "${BEFORE:-0}"
expect "so its phone announces its private address and gets no audio" \
  test "${SPLIT_LEAKS:-0}" -eq 1
expect "the same sequence with block-stride 1 refuses nothing" \
  test "${AFTER_B:-0}" -eq "${AFTER_A:-0}"
expect "and that phone's SDP carries no private address at all" \
  test "${STRIDE_LEAKS:-0}" -eq 0
verdict
