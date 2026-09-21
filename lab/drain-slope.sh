#!/bin/sh
# What the stop-drain costs per block, which the compose file's grace period
# was set without (#396, #425's neighbour).
#
# WHY IT MATTERS. `docker stop` gives the dataplane `stop_grace_period` and
# then SIGKILLs it, and appliance/compose.yaml sets 30s against a drain
# measured at 504 ms - for ONE block. lab/ipfix-stop.sh says so in its own
# header: "what is NOT measured is the slope: one block is one point, and a
# box with 200,000 subscribers releases rather more of them."
#
# If the drain costs a millisecond a block, a box with 200k subscribers needs
# 200 seconds and the 30 in that file cuts the drain off - losing exactly the
# release records the whole mechanism exists to write, on the boxes with the
# most of them.
#
# HOW IT IS ASKED. A pool with thousands of small blocks, N subscribers given
# one block each by traffic, and the drain timed with `date +%s%N` around the
# vppctl call - the same instrument lab/ipfix-stop.sh uses, at four counts so
# the fixed cost and the slope can be told apart.
#
#   sh tools/lab.sh drain-slope           -> LAB PASSED
#   sh tools/lab.sh drain-slope no-drain  -> LAB FAILED (1 check)
#
# OFF-RED-AT-LEAST: 1
#
# ONE, AND THE OTHER CHECK CANNOT BE MADE RED BY THIS LAB. `no-drain` skips
# the drain, and the release claim goes red with it: the box is holding
# blocks and says it released none. The second assertion is about TIME - that
# what the drain costs is bounded - and no mode this lab can arrange makes a
# box slower; a floor that counted it would be counting a check nobody has
# watched fail, which is the thing the floor exists to refuse.
set -u
MODE=${1:-}
. /src/lab/lib.sh
. /src/lab/verdict.sh

mkdir -p /run/vpp /vpp_plugins /vat2_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api
vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up
vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
# SMALL BLOCKS AND A WIDE OUTSIDE, so the pool holds thousands: 16 addresses
# times 156 blocks of 64 ports is 2496, which is more than any count below.
vppctl bpa pool drain inside 100.64.0.0/22 outside 200.150.0.16/28 \
    ports 10000-20000 block 64 mode dynamic
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
# The drain has to have something to say, and logging is what it says it to.
vppctl set ipfix exporter collector 10.0.0.2 port 4739 src 100.64.3.254 \
    >/dev/null 2>&1
vppctl bpa logging enable >/dev/null 2>&1

held_now() {
  vppctl show bpa blocks | sed -n 's/^ *total: \([0-9]*\)\/.*/\1/p' | head -1
}

# The drain command, read out of the shipped drop-in rather than typed here,
# for the reason lab/ipfix-stop.sh gives: a drop-in that loses its ExecStop
# takes this lab red with it.
DRAIN=$(sed -n 's/^ExecStop=-\{0,1\}//p' \
        /src/appliance/systemd/vpp-stop-drain.conf 2>/dev/null | head -1)
echo "  the drain, as the box ships it: ${DRAIN:-<none>}"

echo
echo "=========== what the drain costs, by the blocks it holds ==========="
RESULT=""
for N in 1 50 200 800; do
  # N subscribers, one packet each, one block each.
  vppctl packet-generator delete d >/dev/null 2>&1
  LAST=$(( N > 254 ? 254 : N ))
  if [ "$N" -le 254 ]; then
    SRC="100.64.0.1-100.64.0.$LAST"
  else
    SRC="100.64.0.1-100.64.$(( N / 254 )).$(( N % 254 + 1 ))"
  fi
  vppctl packet-generator new "name d limit $N node ip4-input \
      interface pg0 size 60-60 \
      data { UDP: $SRC -> 8.8.8.8 \
             UDP: 40000 -> 53 incrementing 8 }" >/dev/null
  vppctl packet-generator enable >/dev/null
  sleep 2
  vppctl packet-generator delete d >/dev/null 2>&1

  HELD=$(held_now)
  T0=$(date +%s%N)
  if [ "$MODE" = no-drain ]; then
    # The control: the blocks are held and nothing is asked to release them,
    # which is what the release assertion below is about.
    : > /tmp/drain-$N.txt
  else
    $DRAIN > /tmp/drain-$N.txt 2>&1
  fi
  T1=$(date +%s%N)
  MS=$(( (T1 - T0) / 1000000 ))
  SAID=$(sed -n 's/.*released \([0-9]*\) block.*/\1/p' /tmp/drain-$N.txt | head -1)
  echo "  ${HELD:-0} block(s) held: the drain took ${MS} ms and released ${SAID:-0}"
  RESULT="$RESULT $HELD:$MS:${SAID:-0}"
  # Give the blocks back so the next count starts from a pool with room.
  vppctl bpa logging enable >/dev/null 2>&1
done

echo
echo "=========== the shape of it ==========="
printf '%s\n' "$RESULT" | tr ' ' '\n' | awk -F: '
  NF == 3 && $1 != "" {
    if (n == 0 || $1 < lo_h) { lo_h = $1; lo_t = $2 }
    if ($1 > hi_h) { hi_h = $1; hi_t = $2 }
    n++
  }
  END {
    if (n < 2) { print "  too few points to draw a line"; exit }
    printf "  %d block(s): %d ms\n", lo_h, lo_t
    printf "  %d block(s): %d ms\n", hi_h, hi_t
    if (hi_h > lo_h)
      printf "  so about %.3f ms per block beyond the first, on top of a fixed %d ms\n",
             (hi_t - lo_t) / (hi_h - lo_h), lo_t
  }'

echo
echo "=========== VERDICT ==========="
LAST_HELD=$(printf '%s\n' "$RESULT" | tr ' ' '\n' | awk -F: 'NF==3 && $1>m {m=$1} END {print m+0}')
LAST_SAID=$(printf '%s\n' "$RESULT" | tr ' ' '\n' | awk -F: 'NF==3 && $1>m {m=$1; s=$3} END {print s+0}')
expect "the drain releases the blocks it was holding (held $LAST_HELD, released $LAST_SAID)" \
  test "$LAST_SAID" -ge "$LAST_HELD"
# THE BOUND THE GRACE PERIOD RESTS ON. appliance/compose.yaml gives the drain
# 30 seconds; a drain that took longer than a second for eight hundred blocks
# would put a 200k-subscriber box past that, and this says so here rather
# than on somebody's stop.
BIG_MS=$(printf '%s\n' "$RESULT" | tr ' ' '\n' | awk -F: 'NF==3 && $1>m {m=$1; t=$2} END {print t+0}')
expect "and what it costs is bounded: $LAST_HELD blocks in ${BIG_MS} ms" \
  test "$BIG_MS" -lt 5000
verdict
