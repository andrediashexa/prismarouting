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
set -u
MODE=${1:-on}

mkdir -p /run/vpp /vpp_plugins /var/log/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
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
alarms() { vppctl show logging 2>/dev/null | grep -c "alarm"; }

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
ALARM_TEXT=$(vppctl show logging 2>/dev/null | grep "alarm" | tail -1)
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
HIST_AFTER=$(vppctl show bpa pool | sed -n '/utilisation/,/alarm at/p')
echo "  after 100.64.0.9 takes 7 of 8:"
printf '%s\n' "$HIST_AFTER" | sed 's/^/  /'
TWO_ALARMS=$(alarms)

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
expect_in "and the report names the subscriber and how full it is" \
  "through its 8 ports" "$ALARM_TEXT"
expect_in "and says what it is ahead of" "natEvent 12" "$ALARM_TEXT"
# Once per CROSSING. A line per allocation past the threshold is a log nobody
# can keep, and the subscriber that trips it is the one allocating fastest.
expect "filling further does not report again" test "${AGAIN:-9}" -eq 1
expect "a second subscriber crossing reports once more" \
  test "${TWO_ALARMS:-0}" -eq 2

expect_in "the histogram exists" "utilisation" "$HIST_BEFORE"
# The half that catches a counter read for one subscriber only.
expect "and it moved when a second subscriber filled" \
  test "$HIST_BEFORE" != "$HIST_AFTER"
expect_in "the threshold is in the running configuration" \
  " utilisation-alarm 75" "$CONF"

verdict
