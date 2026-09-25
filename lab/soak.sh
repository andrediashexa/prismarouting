#!/bin/sh
# Hours of session churn: what does not come back is a leak.
#
#   sh tools/lab.sh soak            210 s of churn, about four minutes in all
#   sh tools/lab.sh soak 28800      eight hours of it
#
# THE ARGUMENT IS SECONDS OF CHURN and it is the only one. Everything else -
# how long a cycle is, how many subscribers move in it, how long the box is
# left alone inside each cycle - is derived from it and from the timeouts the
# box is configured with, so a long run and a short one are the same
# experiment at two lengths and their numbers are comparable.
#
# THE RULE THE WHOLE FILE IS WRITTEN AGAINST. The lab never performs the step
# it is measuring. It does not free a session, it does not release a block, it
# does not flush a record. It puts packets in and reads what the box did with
# them, and every number it compares is one the box printed. A soak that
# tidied up after the dataplane would be measuring the lab.
#
# THE SHAPE: EVERY CYCLE FILLS THE TABLE AND EVERY CYCLE EMPTIES IT.
#
#   burst   15 s   32 subscribers, 199 source ports each, at a rate that
#                  fills the whole burst, so sessions are being created while
#                  others are timing out
#   tail    20 s   nothing sent. Longer than the UDP timeout plus the block
#                  hold-down, so the box gives everything back before the next
#                  burst starts
#
# The lab reads the box at the END of each half: a PEAK sample with the table
# full, and a TROUGH sample with it empty. That is what makes the memory
# question answerable, and it is worth saying why at length.
#
#   Every trough is the SAME STATE OF THE BOX: no session, no block held,
#   nothing arriving. So two troughs are directly comparable, and anything the
#   later one holds that the earlier one did not is memory the box did not
#   give back. There is no working set to subtract, no settling to wait out
#   and no allocator wobble to make room for, because both readings are taken
#   with the tables empty.
#
#   That is the whole reason the tail is 20 s and not 3. The first version of
#   this file ran a 12 s burst inside a 15 s cycle against a 20 s timeout, so
#   the tail of one burst was still alive when the next began - and the live
#   session count never settled. Measured over 60 cycles on 2026-09-14, it
#   climbed by about 21 a cycle for 21 cycles, dropped by 511 in one, and
#   began climbing again: a beat between the idle gap and the timeout, with
#   the heap following its high-water mark upward in steps for five and a half
#   minutes. Nothing was wrong with the box. A load that never reaches a
#   steady state cannot be asserted to plateau, and a lab that asserted it
#   anyway would be reporting its own generator.
#
# WHAT THE DEFAULT RUN PROVES AND WHAT ONLY A LONG ONE DOES.
#
#   The 210 s run proves the MECHANISM: that the table empties at every
#   trough, that blocks come back to the pool, that the box's own allocation
#   and release counters end equal, that one natEvent 17 reaches a real
#   collector for every natEvent 16, and that each assertion is wired to
#   something that can move. It is the run to use while editing this file.
#
#   It does NOT prove the product survives a week, and the difference is
#   arithmetic rather than opinion. A leak is a RATE, and what the memory
#   assertion can see is its 1.00 MB margin divided by the sessions the run
#   churned: six cycles at 6368 sessions each is 38208, so the default run
#   cannot see a leak smaller than about 27 bytes per session. Eight hours is
#   822 cycles and 5.2 million sessions, and sees 0.2 bytes. A week sees one
#   byte in 3000 - a leak nothing else in this repository could detect. That
#   factor is bought with wall time and with nothing else, which is the whole
#   reason the duration is the argument.
#
# HOW CHURN IS MADE, and why it is not simply "send traffic". A soak that
# re-sends the same five-tuples refreshes sessions instead of creating them:
# the table fills once and then stops moving, and that box would pass every
# assertion here while leaking on every real network. So the ports rotate
# every cycle, and the subscribers rotate over four groups, which also spreads
# the churn over four times as many subscriber indices as are ever live.
#
# THE RANGE SIZES ARE COPRIME AND THAT IS LOAD-BEARING. VPP's packet generator
# steps each ranged field independently and wraps it, so a stream over 32
# addresses and 256 ports produces 256 distinct pairs, not 8192: the pair
# repeats every lcm(32,256)=256 packets. 199 is prime, so 32 addresses by 199
# ports gives 6368 distinct pairs and every subscriber gets 199 of its own.
#
# NOT IN tools/run-all-labs.sh, deliberately, and NOT_LABS there carries the
# reason as data. The suite's longest lab is about two minutes; this one is
# four at its shortest and is worth running for hours. A suite that waited on
# it would stop being run, which loses the suite rather than the lab.
#
# THE CONTROL RUN. This lab has no "off" mode and cannot have an honest one:
# there is no feature here to switch off, and a mode that made the box leak on
# purpose would be the lab performing the product's step in the other
# direction. So the control is eight runs against a deliberately broken
# product or a deliberately mis-set-up lab, each recorded with the exact edit
# that produced it and the verbatim red it gave. None of them is a mode of
# this file and none of them is left in the tree. They are in the pull request
# for #30, and they are what the green run below rests on. Measured on
# 2026-09-14, at 105 s of churn each:
#
#   sessions never expire (bpa_expire_walk)          8 red
#   blocks never reaped (bpa_dyn_reap)               5 red
#   the natEvent 17 is never exported                2 red, and only those two
#   a kilobyte leaked per translated packet          2 red, and only those two
#   one exported record in a hundred dropped         3 red
#   the traffic misses the pool (lab)                5 red, all preconditions
#   the pool is too small for the churn (lab)        2 red, both preconditions
#   the heap reading is not taken (lab)              its own section, below
#
# THE LAST ONE FOUND A DEFECT IN THIS FILE and is the reason sample() writes
# NA. Breaking the heap parser on purpose - the shape a VPP upgrade that
# renames a field would produce - left the check written to catch exactly that
# GREEN, because the empty column shifted every column after it left. Three
# assertions were answering about the wrong number and the one guarding them
# said yes. It is the defect this repository is organised against, found by a
# control run against the lab's own measurement rather than against the
# product.
#
# WHAT THE COLLECTOR COSTS ON A LONG RUN, since it is the one thing here that
# grows with time on purpose: tools/ipfix_collector.py holds every record it
# decodes, because that is what lets it match each allocation to its own
# release rather than counting two totals. This churn exports about 7 records
# a second, so roughly 9 MB an hour, against the 5 GB tools/lab.sh gives the
# container. A run beyond about a day needs LAB_MEM raised, and that is a
# reading rather than a guess.
set -u
. /src/lab/lib.sh   # startup_conf, use_binary_api, wait_vpp, vc
. /src/lab/verdict.sh

# Seconds of churn. Everything else follows from it.
CHURN=${1:-210}
case "$CHURN" in
  ''|*[!0-9]*) echo "usage: sh tools/lab.sh soak [seconds-of-churn]"; exit 1 ;;
esac

BURST=15          # seconds of traffic
TAIL=20           # seconds of nothing, and it must outlast the two below
CYCLE=$(( BURST + TAIL ))
WARMUP=1          # cycles read but not compared: the first fills the pools
GROUPS=4          # the subscriber group rotates over this many
SUBS=32           # subscribers per group
PORTS=199         # prime, so 32 x 199 pairs are all distinct
UDP_TIMEOUT=10    # the box's, and it is the box that applies it
HOLDDOWN=5        # the box's, and it is the box that waits it out

NCYCLES=$(( CHURN / CYCLE ))
# Below this there is no warm-up and nothing to compare it against. A lab that
# cannot arrange its conditions says so rather than passing.
if [ "$NCYCLES" -lt 3 ]; then
  echo "soak: $CHURN s is $NCYCLES cycle(s) of ${CYCLE}s, and this needs at"
  echo "  least 3: one to warm up and two troughs to compare. Ask for 105 or"
  echo "  more."
  exit 1
fi

# How much the heap and the resident size may move BETWEEN TWO EMPTY TABLES.
#
# Measured rather than chosen. A 700 s run of this shape on 2026-09-14 - 20
# cycles, 127360 sessions created and 127360 expired, 2532 blocks taken and
# 2532 given back - moved the trough heap by 0.01 MB in all and the trough RSS
# by nothing:
#
#   23837 23837 23837 23837 23837 23837 23837 23837 23837 23837 23838
#   23838 23838 23838 23838 23838 23838 23838 23838 23838
#
# That is one 10 kB step in nineteen comparisons, which is the smallest step
# the reading can show. The margins are set a hundred times above it, not
# because the drift needs the room but because a check that goes red on a
# correct box is worse than no check: an allocator is entitled to keep a
# vector it has already grown, and a later cycle may grow one an earlier one
# did not.
HEAP_MARGIN=100        # hundredths of a megabyte: 1.00 MB
RSS_MARGIN=8192        # kB: 8 MB

# The collector has to outlive the whole run and the verdict that reads it.
#
# It was given CHURN + 40 seconds, and that is not a number anything can know
# before the run: 822 cycles of vppctl and packet-generator calls drift about
# 80 seconds past the arithmetic of 822 x 35s, so the budget expired INSIDE
# the final cycle. It had heard every allocation and none of that cycle's
# releases, and reported 46 allocations nobody ever released - which is what a
# leak looks like, on a box whose own counters read 104227 allocations against
# 104227 releases and zero blocks in use (#30).
#
# So this is a CEILING and not the intended end. The lab stops the collector
# itself, once the box has given every block back, through COL_STOP. The
# ceiling still scales with the run, because the drift does.
COL_SECS=$(( CHURN + CHURN / 20 + 120 ))
COL_STOP=/tmp/collector.stop
rm -f "$COL_STOP"

mkdir -p /run/vpp /vpp_plugins /var/log/vpp /var/log/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api

# The collector lives in its own namespace on the far side of a veth, the same
# arrangement lab/extblocks.sh and lab/detreserve.sh use: a record that never
# left the box is not a record anybody can be attributed by.
ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up
ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
wait_vpp

vppctl create host-interface name veth-vpp >/dev/null
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

# A dynamic pool, because a deterministic one never gives a block back: its
# mapping is arithmetic and there is nothing to return. Blocks returning to
# the pool is half of what this lab is for.
#
# The public range starts at .16 so it does not contain pg1's own address. The
# box refuses that overlap in as many words, and a pool the box refused is a
# lab measuring an unconfigured dataplane.
vppctl bpa pool soak inside 100.64.0.0/22 outside 200.150.0.16/28 \
    ports 10000-20000 block 64 mode dynamic max-blocks 8
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
# The box's timeouts, set once and never touched again. Nothing below expires
# a session or releases a block; these two values and the box are what do it.
vppctl bpa timeouts udp $UDP_TIMEOUT >/dev/null
vppctl bpa block-holddown $HOLDDOWN >/dev/null

echo "=========== the box under test ==========="
vc show bpa pool | sed 's/^/  /'
echo "  udp timeout ${UDP_TIMEOUT}s, block hold-down ${HOLDDOWN}s"
echo "  churn ${CHURN}s = $NCYCLES cycles of ${CYCLE}s (${BURST}s of traffic,"
echo "  then ${TAIL}s of nothing), $WARMUP of them warm-up"

# The exporter is configured THROUGH THE VTY and never from this file. A lab
# that performs the product's own step proves nothing about the product, which
# is the rule lab/ipfix.sh records after UDP export shipped for the life of
# the project with a green lab beside it.
echo
echo "=========== the log's far side ==========="
HX="python3 /src/src/prismash/prismash.py"
$HX -c "configure" \
    -c "nat" -c "cgnat" -c "logging udp 10.0.0.2 src 10.0.0.1 port 4739" \
    -c "logging enable" \
    -c "commit" 2>&1 | sed 's/^/  /'
ip netns exec peer python3 /src/tools/ipfix_collector.py 4739 $COL_SECS \
  "$COL_STOP" > /tmp/collector.out 2>&1 &
COL_PID=$!
sleep 2

# --- what the box says, read one way only --------------------------------
sessions_now () { vc show bpa session | sed -n 's/^total: \([0-9]*\) sessions.*/\1/p'; }
expired_now  () { vc show bpa session | awk '/^thread /{s+=$(NF-1)} END{print s+0}'; }
created_now  () { vc show errors | awk '/bpa-in2out/ && /sessions created/{print $1; exit}'; }
refused_now  () { vc show errors | awk '/bpa-in2out/ && /port block exhausted/{print $1; exit}'; }
blocks_used  () { vc show bpa blocks | sed -n 's/^ *total: \([0-9]*\)\/.*/\1/p'; }
blocks_alloc () { vc show bpa blocks | sed -n 's/^ *total: .* in use, \([0-9]*\) allocations.*/\1/p'; }
blocks_rel   () { vc show bpa blocks | sed -n 's/^ *total: .*), \([0-9]*\) releases.*/\1/p'; }
# Hundredths of a megabyte, because that is the resolution VPP prints and its
# smallest visible step is 10 kB. The unit is required to be M: at a 512 MB
# heap with 240 MB in use it always is, and a reading in any other unit comes
# back empty - which the conditions catch, rather than the verdict silently
# comparing kilobytes with megabytes.
#
# VPP's own figure for what the allocator has handed out, not RSS. The
# allocator is the only thing that knows what it has been given back: a heap
# that frees a megabyte does not return the pages, so RSS would show a healthy
# box's high-water mark and call it growth. RSS is read as well, and asserted
# with a margin of its own, because it is what the OOM killer reads and
# because it is the only number that can see a leak OUTSIDE the main heap.
heap_cent    () {
  vc show memory main-heap \
    | awk -F'used: ' '/used: /{split($2,a,"M,"); printf "%d\n", a[1]*100; exit}'
}
rss_kb       () { awk '/^VmRSS:/{print $2}' /proc/$(pidof vpp)/status; }

SAMPLES=/tmp/soak.samples
: > $SAMPLES
# NA FOR A READING THAT CAME BACK EMPTY, and it is not a nicety.
#
# awk splits on runs of whitespace, so an empty field does not survive
# printf: it disappears and every column after it shifts left by one. Found by
# the control run that breaks the heap parser on purpose (the "usedx" run):
# with the heap reading empty, column 7 became the RSS value, the condition
# that exists to catch a missing reading passed because column 7 was not
# empty, the heap check compared RSS with RSS and passed, and the only thing
# that went red was the RSS check - comparing two session counts. Three
# assertions answering about the wrong number, and the one written to catch
# exactly this saying yes.
#
# A placeholder keeps the columns aligned and fails every arithmetic use of
# the value, which is what the condition and the guarded deltas below then
# read.
sample () {   # <phase> <cycle>
  _s=$(sessions_now); _b=$(blocks_used); _a=$(blocks_alloc)
  _r=$(blocks_rel);   _h=$(heap_cent);   _m=$(rss_kb)
  _c=$(created_now);  _e=$(expired_now)
  printf '%s %s %s %s %s %s %s %s %s %s\n' "$1" "$2" \
    "${_s:-NA}" "${_b:-NA}" "${_a:-NA}" "${_r:-NA}" \
    "${_h:-NA}" "${_m:-NA}" "${_c:-NA}" "${_e:-NA}" >> $SAMPLES
}
show_last () {
  awk 'END{printf "  %-6s %5s  sessions %6s  blocks %4s  %7s alloc %7s rel  heap %6.2fM  rss %8s kB  created %9s  expired %9s\n",
                  $1, $2, $3, $4, $5, $6, $7/100, $8, $9, $10}' $SAMPLES
}

# A baseline that is READ, not assumed. "Returns to baseline" is a claim about
# two readings of the same box, and the first of them has to be taken before
# any packet is sent.
BASE_SESS=$(sessions_now)
BASE_BLOCKS=$(blocks_used)
echo
echo "=========== baseline, before a single packet ==========="
echo "  sessions $BASE_SESS   blocks in use $BASE_BLOCKS   heap $(heap_cent)   rss $(rss_kb) kB"

# How often a cycle is printed. Every one for a short run; for an eight-hour
# run that would be sixteen hundred lines, and a capture nobody reads is the
# same as no capture. Every sample is recorded either way.
PRINT_EVERY=$(( NCYCLES / 40 ))
[ "$PRINT_EVERY" -lt 1 ] && PRINT_EVERY=1

echo
echo "=========== $NCYCLES cycles: ${BURST}s of churn, ${TAIL}s of nothing ==========="
c=0
while [ "$c" -lt "$NCYCLES" ]; do
  G=$(( c % GROUPS ))
  PB=$(( 1024 + (c * PORTS) % 40000 ))
  PE=$(( PB + PORTS - 1 ))
  N=$(( SUBS * PORTS ))
  # The rate makes the stream fill the whole burst. "Continuous session
  # creation and expiry" is the acceptance criterion, and a stream with no
  # rate satisfies it in one instant per cycle.
  RATE=$(( N / BURST ))
  vppctl packet-generator delete s >/dev/null 2>&1
  vppctl packet-generator new "name s limit $N rate $RATE node ip4-input \
      interface pg0 size 60-60 \
      data { UDP: 100.64.$G.1-100.64.$G.$SUBS -> 8.8.8.8 \
             UDP: $PB-$PE -> 53 incrementing 8 }" >/dev/null
  vppctl packet-generator enable >/dev/null
  sleep "$BURST"
  sample peak "$c"
  _show=0
  [ $(( c % PRINT_EVERY )) -eq 0 ] && _show=1
  [ "$c" -eq $(( NCYCLES - 1 )) ] && _show=1
  if [ "$_show" -eq 1 ]; then
    printf '  group 100.64.%s.1-%s ports %s-%s\n' "$G" "$SUBS" "$PB" "$PE"
    show_last
  fi
  vppctl packet-generator disable >/dev/null 2>&1
  vppctl packet-generator delete s >/dev/null 2>&1
  # Nothing is expired here and nothing is released here. The box is left
  # alone for longer than its own timeout and hold-down together, and then
  # read. Whether it came back to where it started is the box's answer.
  sleep "$TAIL"
  sample trough "$c"
  [ "$_show" -eq 1 ] && show_last
  c=$(( c + 1 ))
done

# --- the box finishes, and only then is the far side closed ---------------
#
# WAITING FOR THE BOX, not doing anything on its behalf: what is read here is
# the box's own count of blocks in use, and the lab does nothing but let the
# hold-down and the UDP timer run out. A collector closed while the box is
# still releasing hears the allocations and not the releases, and that is the
# defect this whole section exists to stop (#30).
SETTLE_WAITED=0
SETTLE_LIMIT=$(( UDP_TIMEOUT + HOLDDOWN + 30 ))
while [ "$(blocks_used)" -gt 0 ] && [ "$SETTLE_WAITED" -lt "$SETTLE_LIMIT" ]; do
  sleep 1
  SETTLE_WAITED=$(( SETTLE_WAITED + 1 ))
done
SETTLE_BLOCKS=$(blocks_used)
# And a moment for the records of those last releases to cross the wire. The
# collector drains what is already queued when it sees the file, so this only
# has to cover flight time.
sleep 5
: > "$COL_STOP"
echo
echo "  the box settled in ${SETTLE_WAITED}s with $SETTLE_BLOCKS block(s) still"
echo "  in use, and the collector was then told to stop"

echo
echo "=========== what the box ended holding ==========="
vc show bpa session | sed 's/^/  /'
vc show bpa blocks | sed 's/^/  /'
vc show bpa logging | sed -n '1,9p' | sed 's/^/  /'
vc show errors | grep -E "bpa-in2out|bpa-out2in" | sed 's/^/  /'

END_SESS=$(sessions_now)
END_BLOCKS=$(blocks_used)
END_ALLOC=$(blocks_alloc)
END_REL=$(blocks_rel)
CREATED=$(created_now)
EXPIRED=$(expired_now)
REFUSED=$(refused_now)
DROPPED=$(vc show bpa logging | sed -n 's/^ *records *[0-9]* emitted, \([0-9]*\) dropped.*/\1/p')

# Every trough after the warm-up, counted rather than sampled: a table that
# emptied at the end says nothing about the four hours before it.
N_TROUGH=$(awk -v w=$WARMUP '$1=="trough" && $2>=w' $SAMPLES | wc -l)
BAD_SESS=$(awk -v w=$WARMUP -v b="$BASE_SESS" \
             '$1=="trough" && $2>=w && $3!=b' $SAMPLES | wc -l)
BAD_BLK=$(awk -v w=$WARMUP -v b="$BASE_BLOCKS" \
            '$1=="trough" && $2>=w && $4!=b' $SAMPLES | wc -l)
# Blocks given back while the run was still going, read off the last trough
# before the final one. A box that only released at the end would look the
# same in the closing numbers and be broken for the whole run.
REL_MID=$(awk '$1=="trough"' $SAMPLES | awk 'NR==1{print $6+0}')
PEAK_SESS=$(awk '$1=="peak"{if($3>m)m=$3} END{print m+0}' $SAMPLES)

# The two empty tables. Both readings are taken with no session, no block held
# and nothing arriving, so the difference between them is memory the box did
# not give back - not a working set, and not a settling curve.
BASE_HEAP=$(awk -v w=$(( WARMUP - 1 )) '$1=="trough" && $2==w {print $7}' $SAMPLES)
BASE_RSSK=$(awk -v w=$(( WARMUP - 1 )) '$1=="trough" && $2==w {print $8}' $SAMPLES)
PEAK_HEAP=$(awk -v w=$WARMUP '$1=="trough" && $2>=w {if($7>m)m=$7} END{print m+0}' $SAMPLES)
PEAK_RSSK=$(awk -v w=$WARMUP '$1=="trough" && $2>=w {if($8>m)m=$8} END{print m+0}' $SAMPLES)

# A reading that is not a positive number is not a reading. The sentinel is
# larger than any margin, so a missing one takes the check red instead of
# comparing two zeroes.
ungrown () {   # <base> <peak> -> the growth, or a number no margin allows
  awk -v b="$1" -v p="$2" 'BEGIN{
    if (b+0 <= 0 || p+0 <= 0) print 2147483647; else print p - b }'
}
HEAP_DELTA=$(ungrown "$BASE_HEAP" "$PEAK_HEAP")
RSS_DELTA=$(ungrown "$BASE_RSSK" "$PEAK_RSSK")

echo
echo "=========== the shape of the run ==========="
awk -v b="$BASE_HEAP" -v p="$PEAK_HEAP" -v m=$HEAP_MARGIN \
    -v br="$BASE_RSSK" -v pr="$PEAK_RSSK" -v mr=$RSS_MARGIN 'BEGIN{
  printf "  at the warm-up trough:  heap %.2fM, rss %d kB\n", b/100, br
  printf "  highest later trough:   heap %.2fM (%+.2fM), rss %d kB (%+d kB)\n",
         p/100, (p-b)/100, pr, pr-br
  printf "  margins allowed:        heap %.2fM, rss %d kB\n", m/100, mr }'
echo "  troughs compared: $N_TROUGH, of which $BAD_SESS held a session and $BAD_BLK held a block"
echo "  most sessions live at once: $PEAK_SESS"
# Every trough's heap, so a reader of a long run sees the curve and not only
# its ends. Four to a line.
echo "  heap at each trough, in hundredths of a megabyte:"
awk '$1=="trough"{printf " %s", $7} END{print ""}' $SAMPLES \
  | fold -s -w 70 | sed 's/^/   /'

echo
echo "=========== the far side of the log ==========="
wait $COL_PID 2>/dev/null
COL=$(cat /tmp/collector.out)
printf '%s\n' "$COL" | grep -E "^SUMMARY|^Every allocation|^WARNING|^EARLY" | sed 's/^/  /'
printf '%s\n' "$COL" | sed -n '/^OPEN ALLOCATIONS/,$p' | head -6 | sed 's/^/  /'
COL_ALLOC=$(printf '%s\n' "$COL" | sed -n 's/^SUMMARY: .* (\([0-9]*\) allocations.*/\1/p')
COL_REL=$(printf '%s\n' "$COL" | sed -n 's/^SUMMARY: .*, \([0-9]*\) deallocations.*/\1/p')
COL_OPEN=$(printf '%s\n' "$COL" | sed -n '/^OPEN ALLOCATIONS/,$p' | sed -n 2p)
# Which of the two ended the collector. The two checks about its books are
# only about the box if the box finished first, and a collector that ran out
# of budget reports the releases it did not wait for as allocations nobody
# ever released.
COL_STOPPED_BY=$(printf '%s\n' "$COL" | sed -n 's/^STOPPED BY: //p')

echo
echo "=========== VERDICT ==========="
echo "  churn ${CHURN}s, $NCYCLES cycles, $CREATED sessions created"

# --- the conditions the measurement needs --------------------------------
#
# Everything under this heading is about the RUN and not about the product,
# and each one is a way for this lab to pass having measured nothing. A soak
# whose traffic missed the pool empties an empty table, gives back no block it
# never took and balances a log with no records in it - and reports a green
# run after eight hours. These four are what stop that reading like a healthy
# box.
expect "the churn ran: the box created sessions in every cycle" \
  test "${CREATED:-0}" -ge $(( SUBS * PORTS * NCYCLES ))
expect "the pool was large enough for it, so nothing was refused a block" \
  test "${REFUSED:-0}" -eq 0
expect "the collector was there and decoded records" \
  test "${COL_ALLOC:-0}" -ge 1
expect "and the memory readings the two checks below rest on were taken" \
  test -n "${BASE_HEAP:-}" -a "${PEAK_HEAP:-0}" -gt 0 -a "$N_TROUGH" -ge 2

# --- what the soak is for -------------------------------------------------

# A table that never empties is a leak, and the baseline is the reading taken
# before the first packet rather than the number zero. Asserted at EVERY
# trough: a table that emptied once at the end says nothing about the hours
# before it.
expect "the session table came back to its baseline at every trough" \
  test "${BAD_SESS:-1}" -eq 0
# The snapshot above is satisfied by a box that never created the sessions it
# should have. This one is not: it is the box's own two counters, and they are
# equal only if every session it made it also ended.
expect "and every session the box created, the box expired" \
  test "${CREATED:-0}" -eq "${EXPIRED:-1}" -a "${CREATED:-0}" -gt 0
# Blocks are the allocation that outlives the session, which is why an
# allocator leak is invisible in a session count.
expect "every block came back to the pool at every trough" \
  test "${BAD_BLK:-1}" -eq 0
expect "and the box's own allocations and releases end equal" \
  test "${END_ALLOC:-0}" -eq "${END_REL:-1}" -a "${END_ALLOC:-0}" -gt 0
# The half a run that only releases at the end cannot see: the hold-down has
# to do its work while the run is still going.
expect "blocks were already being given back at the first trough" \
  test "${REL_MID:-0}" -ge 1

# The two empty tables.
# The deltas are computed in awk with a guard rather than by the shell,
# because a reading that came back NA must make these RED and not zero. Under
# dash "$(( NA - NA ))" is 0, which is inside any margin: the two checks that
# answer the memory question would pass on a run that never read the memory.
expect "the heap between two empty tables did not grow" \
  test "$HEAP_DELTA" -le "$HEAP_MARGIN"
expect "nor did resident memory, which is what the OOM killer reads" \
  test "$RSS_DELTA" -le "$RSS_MARGIN"

# The log, at a real collector on the far side of a wire. An allocation with
# no release names a subscriber who still holds those ports for as long as the
# log is kept, which is as long as the retention obligation lasts.
#
# THE TWO CHECKS BELOW ARE ONLY ABOUT THE BOX IF THE BOX FINISHED FIRST, and
# for eight hours on 2026-09-15 they were not. The collector had a budget of
# CHURN + 40 seconds fixed before the run; 822 cycles drift about 80 seconds
# past 822 x 35s, so it stopped listening inside the final cycle, having heard
# that cycle's allocations and none of its releases. It reported 104227
# allocations against 104181 deallocations and 46 open, and both checks went
# red - on a box whose own counters read 104227 against 104227 with zero
# blocks in use. The assertions were right. What was missing was anybody
# saying the far side had been given the chance to hear the end.
#
# These two say it, and they come first because they are what makes the other
# two mean anything.
expect "the box had given every block back before the far side was closed" \
  test "${SETTLE_BLOCKS:-1}" -eq 0
expect_in "and the collector was stopped by this lab, not by its own budget" \
  "the caller, through" "${COL_STOPPED_BY:-}"
expect "the collector counted one natEvent 17 for every natEvent 16" \
  test "${COL_ALLOC:-0}" -eq "${COL_REL:-1}"
# Counts alone would let a box release a block it never allocated and balance
# the books. This matches each allocation to a release of ITS OWN ports.
expect_in "with no allocation left open at the end" "none" "${COL_OPEN:-}"
expect "and the box dropped no record on the way out" \
  test "${DROPPED:-1}" -eq 0

verdict
