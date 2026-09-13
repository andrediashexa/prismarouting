#!/bin/sh
# Seeing a pool fill, before it has filled.
#
#   sh tools/lab.sh utilisation        with the alarm configured
#   sh tools/lab.sh utilisation off    without it - the alarm checks MUST fail
#
# natEvent 12 reports exhaustion, which is to say it reports the moment a
# customer started losing traffic. By then the support call is in the queue.
# Two things have to be true instead: the alarm fires ONCE when a subscriber
# crosses its threshold, and the histogram shows the shape of the pool rather
# than a mean that hides it.
#
# OFF-RED-AT-LEAST: 7
#
# A FLOOR, not a target, and the number is measured rather than chosen: it is
# what this lab's off run went red on in the release suite of 2026-09-13.
# tools/run-all-labs.sh refuses an off run that goes red on fewer, because a
# lab that used to fail eleven checks and now fails one has lost ten assertions
# without losing its verdict - which is the defect this whole sweep exists to
# find. Gaining assertions is fine and needs no edit here; losing them is what
# has to be deliberate.
set -u
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
MODE=${1:-on}

mkdir -p /run/vpp /vpp_plugins /var/log/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
n=0
until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up
vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
# Blocks of 8 ports, so a subscriber can be filled with a handful of packets
# and the buckets of the histogram are reachable one at a time.
if [ "$MODE" = off ]; then
  vppctl bpa pool lab inside 100.64.0.0/22 outside 200.150.0.16/30 block 8
else
  vppctl bpa pool lab inside 100.64.0.0/22 outside 200.150.0.16/30 block 8 \
      utilisation-alarm 75
fi
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa timeouts udp 600

# fill <subscriber> <first-port> <count>
fill() {
  i=0
  while [ "$i" -lt "$3" ]; do
    vppctl packet-generator new "name f$i limit 1 node ip4-input \
        interface pg0 size 60-60 data { UDP: $1 -> 8.8.8.8 \
                                        UDP: $(( $2 + i )) -> 53 \
                                        incrementing 8 }" >/dev/null
    i=$(( i + 1 ))
  done
  vppctl packet-generator enable >/dev/null
  sleep 1
  i=0
  while [ "$i" -lt "$3" ]; do
    vppctl packet-generator delete "f$i" >/dev/null 2>&1
    i=$(( i + 1 ))
  done
}
# THIS alarm, not the word. "alarm" on its own counts any line of VPP's log
# that happens to carry it, so the counts below would move for a reason that
# has nothing to do with a subscriber filling its block.
alarms() { vppctl show logging 2>/dev/null | grep -c "past the 75% alarm"; }
last_alarm() { vppctl show logging 2>/dev/null | grep "past the 75% alarm" | tail -1; }

echo "=========== 1. a subscriber below the threshold ==========="
echo "100.64.0.5 takes 4 of its 8 ports - 50%, under the 75% alarm."
fill 100.64.0.5 30000 4
LOW=$(alarms)
vppctl show bpa top 3 | sed 's/^/  /'
echo "  alarms so far: $LOW"

echo
echo "=========== 2. and then crosses it ==========="
echo "two more ports takes it to 75%."
fill 100.64.0.5 31000 2
CROSSED=$(alarms)
ALARM_TEXT=$(last_alarm)
echo "  alarms now: $CROSSED"
printf '%s\n' "$ALARM_TEXT" | sed 's/^/  /'

echo
echo "=========== 3. still filling: once per crossing, not per port ==========="
fill 100.64.0.5 32000 2
AGAIN=$(alarms)
echo "  alarms after filling it to the last port: $AGAIN"

echo
echo "=========== 4. a second subscriber moves the histogram ==========="
echo "this is what catches a counter that is only ever read for one"
echo "subscriber: the shape has to change when somebody else fills."
HIST_BEFORE=$(vppctl show bpa pool | sed -n '/utilisation/,/alarm at/p')
printf '%s\n' "$HIST_BEFORE" | sed 's/^/  /'
fill 100.64.0.9 33000 7
ALARM2_TEXT=$(last_alarm)
HIST_AFTER=$(vppctl show bpa pool | sed -n '/utilisation/,/alarm at/p')
echo "  after 100.64.0.9 takes 7 of 8:"
printf '%s\n' "$HIST_AFTER" | sed 's/^/  /'
TWO_ALARMS=$(alarms)
printf '%s\n' "$ALARM2_TEXT" | sed 's/^/  /'

echo
echo "=========== 5. the mean would have hidden both ==========="
echo "the pool has 126 blocks per public address across 4 addresses; two"
echo "subscribers at their limit are a rounding error in any average of it."
vppctl show bpa pool | grep -E "capacity|utilisation|100%|90-99" | sed 's/^/  /'

echo
echo "=========== 6. in the running configuration ==========="
CONF=$(vppctl show bpa config)
printf '%s\n' "$CONF" | grep -E "utilisation-alarm|cgnat pool" | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

expect "nothing is reported below the threshold" test "${LOW:-1}" -eq 0
expect "crossing it reports once" test "${CROSSED:-0}" -eq 1
# WHICH subscriber, and not only how full. The sentence claims the report
# names the subscriber; "through its 8 ports" says nothing about who, so an
# alarm that named the wrong subscriber every time passed this line - and
# naming the wrong subscriber is the whole of what this alarm is for.
# 100.64.0.5 is subscriber index 5 of a pool whose inside starts at 100.64.0.0.
expect_in "and the report names the subscriber and how full it is" \
  "subscriber 5 of pool lab is 75% through its 8 ports" "$ALARM_TEXT"
expect_in "and says what it is ahead of" "natEvent 12" "$ALARM_TEXT"
# Once per CROSSING. A line per allocation past the threshold is a log nobody
# can keep, and the subscriber that trips it is the one allocating fastest.
expect "filling further does not report again" test "${AGAIN:-9}" -eq 1
expect "a second subscriber crossing reports once more" \
  test "${TWO_ALARMS:-0}" -eq 2
# And that the one more is about the subscriber that crossed. A count of two
# is also what a box produces when it reports 100.64.0.5 a second time and
# never notices 100.64.0.9 at all, which is the failure this section exists
# to catch: a counter read for one subscriber only. The alarm fires on the
# CROSSING, so the percentage on it is 75 - the sixth of eight ports - and not
# the 87% the subscriber ends the section holding.
expect_in "and the second report is about the second subscriber" \
  "subscriber 9 of pool lab is 75% through its 8 ports" "$ALARM2_TEXT"

# The BUCKET, not the heading. "utilisation" is the string the sed above used
# to cut this text out of "show bpa pool", so matching it back reports nothing
# but that the extract is non-empty - and the heading line is printed for every
# dynamic pool whether or not a single bucket follows it. A box with no
# histogram at all passed "the histogram exists", and passed "it moved" too,
# because the subscriber count on the heading moves on its own.
expect_in "the histogram counts the subscriber holding a block" \
  "utilisation  1 subscriber(s) holding a block" "$HIST_BEFORE"
expect_in "and puts it in the bucket its usage falls in" "100%  1" "$HIST_BEFORE"
# The half that catches a counter read for one subscriber only: the SHAPE has
# to change, not just the total. 100.64.0.9 holds 7 of 8, which is 87%.
expect_in "a second subscriber is counted too" \
  "utilisation  2 subscriber(s) holding a block" "$HIST_AFTER"
expect_in "and lands in its own bucket, which is what a mean would hide" \
  "75-89%  1" "$HIST_AFTER"
expect_in "with the first one still where it was" "100%  1" "$HIST_AFTER"
expect_in "the threshold is in the running configuration" \
  " utilisation-alarm 75" "$CONF"

verdict
