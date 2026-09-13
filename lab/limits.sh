#!/bin/sh
# Per-subscriber bounds: a cap on concurrent sessions and on how fast new ones
# may be opened.
#
#   sh tools/lab.sh limits        with the bounds configured
#   sh tools/lab.sh limits off    without them - the cap checks MUST fail
#
# The property worth proving is ISOLATION: one subscriber past its cap is
# refused while a second subscriber sharing the same public address carries on.
# A cap that bounds the pool instead of the customer would pass every other
# check here and fail that one.
#
# OFF-RED-AT-LEAST: 14
#
# A FLOOR, not a target, and the number is measured rather than chosen: it is
# what this lab's off run went red on in the release suite of 2026-09-13.
# tools/run-all-labs.sh refuses an off run that goes red on fewer, because a
# lab that used to fail eleven checks and now fails one has lost ten assertions
# without losing its verdict - which is the defect this whole sweep exists to
# find. Gaining assertions is fine and needs no edit here; losing them is what
# has to be deliberate.
set -u
MODE=${1:-on}

mkdir -p /run/vpp /vpp_plugins /var/log/hexarouting /etc/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
use_binary_api
HX="python3 /src/src/hexash/hexash.py"

mkdir -p /var/run/frr /etc/frr
printf 'hostname hexarouting\n' > /etc/frr/zebra.conf
chown -R frr:frr /var/run/frr /etc/frr 2>/dev/null
start_frr

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
vppctl bpa pool lab inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
# Long enough that nothing expires while the lab runs: this is about refusal,
# not about lifetimes.
vppctl bpa timeouts udp 600

# 100.64.0.5 and 100.64.0.9 BOTH map to 200.150.0.17 - subscriber 5 gets block
# 1536-2047 and subscriber 9 gets 2048-2559. That shared address is what makes
# section 4 a test of isolation rather than of arithmetic.

# burst <source> <first-port> <count>
burst() {
  i=0
  while [ "$i" -lt "$3" ]; do
    vppctl packet-generator new "name b$i limit 1 node ip4-input \
        interface pg0 size 60-60 data { UDP: $1 -> 8.8.8.8 \
                                        UDP: $(( $2 + i )) -> 53 \
                                        incrementing 8 }" >/dev/null
    i=$(( i + 1 ))
  done
  vppctl packet-generator enable >/dev/null
  sleep 1
  i=0
  while [ "$i" -lt "$3" ]; do
    vppctl packet-generator delete "b$i" >/dev/null 2>&1
    i=$(( i + 1 ))
  done
}
sess_of() { vppctl show bpa subscriber "$1" | grep -o "sessions [0-9]*" | head -1 | awk '{print $2}'; }
refusals() { vppctl show bpa subscriber "$1" | grep -o "refusals: .*"; }

echo "=========== 1. before the cap: six sessions, six sessions ==========="
burst 100.64.0.5 30000 6
BEFORE=$(sess_of 100.64.0.5)
echo "  subscriber 100.64.0.5 holds $BEFORE sessions"
vppctl bpa session-table >/dev/null

echo
echo "=========== 2. capping ==========="
if [ "$MODE" = off ]; then
  echo "  MODE=off: no bounds are configured. Every check that claims a"
  echo "  refusal MUST fail."
else
  vppctl bpa limits lab max-sessions 4 | sed 's/^/  /'
  vppctl show bpa pool | grep -i "per subscriber" | sed 's/^/  /'
fi

echo
echo "=========== 3. a fresh subscriber runs into it ==========="
echo "100.64.0.6 opens ten, and must end up holding four."
burst 100.64.0.6 31000 10
CAPPED=$(sess_of 100.64.0.6)
echo "  100.64.0.6 holds $CAPPED sessions"
refusals 100.64.0.6 | sed 's/^/  /'
SUB6=$(vppctl show bpa subscriber 100.64.0.6)

echo
echo "=========== 4. the neighbour on the SAME public address ==========="
echo "100.64.0.9 shares 200.150.0.17 with 100.64.0.5. A cap that bounded the"
echo "pool rather than the customer would refuse it too."
burst 100.64.0.9 32000 3
NEIGHBOUR=$(sess_of 100.64.0.9)
echo "  100.64.0.9 holds $NEIGHBOUR sessions"

echo
echo "=========== 5. the rate cap ==========="
if [ "$MODE" != off ]; then
  vppctl bpa limits lab max-session-rate 3 | sed 's/^/  /'
fi
echo "100.64.0.7 opens ten inside one second; at three per second the bucket"
echo "starts full and holds one second's worth, so it must not get ten."
burst 100.64.0.7 33000 10
RATED=$(sess_of 100.64.0.7)
echo "  100.64.0.7 holds $RATED sessions"
refusals 100.64.0.7 | sed 's/^/  /'
SUB7=$(vppctl show bpa subscriber 100.64.0.7)
echo "  waiting a second for the bucket to refill, then one more:"
sleep 2
burst 100.64.0.7 34000 1
REFILL=$(sess_of 100.64.0.7)
echo "  100.64.0.7 now holds $REFILL sessions"

echo
echo "=========== 6. the counters are three, not one ==========="
# "show errors" truncates the description, so these match what it prints.
ERR=$(vppctl show errors | grep -iE "subscriber|port block exhausted")
echo "$ERR" | sed 's/^/  /'

echo
echo "=========== 7. unlimited means unlimited ==========="
if [ "$MODE" != off ]; then
  vppctl bpa limits lab none | sed 's/^/  /'
fi
burst 100.64.0.8 35000 8
UNCAPPED=$(sess_of 100.64.0.8)
echo "  100.64.0.8 holds $UNCAPPED sessions with no bounds in force"

echo
echo "=========== 8. through the VTY, without recreating the pool ==========="
TRAIL=/var/log/hexarouting/config-history.log
MARK=$(wc -l < $TRAIL 2>/dev/null || echo 0)
BEFORE_VTY=$(vppctl show bpa session | grep -o "total: [0-9]*" | awk '{print $2}')
if [ "$MODE" != off ]; then
  $HX -c "configure" -c "cgnat pool lab" -c "max-sessions 64" \
      -c "max-session-rate 20" -c "exit" -c "commit" 2>&1 | sed 's/^/  /'
fi
VTY_CONF=$(vppctl show bpa config)
NEW=$(tail -n +$((MARK + 1)) $TRAIL 2>/dev/null)
AFTER_VTY=$(vppctl show bpa session | grep -o "total: [0-9]*" | awk '{print $2}')
echo "$VTY_CONF" | grep -E "max-session" | sed 's/^/  /'
echo "  sessions before the commit: $BEFORE_VTY, after: $AFTER_VTY"
echo "  what the trail recorded:"
printf '%s\n' "$NEW" | grep -o "limits sessions [0-9]* rate [0-9]*" | sed 's/^/    /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

# The precondition: six sessions must be possible at all, or "four" below
# would be a limit nobody imposed.
expect "six sessions were possible before any cap" test "${BEFORE:-0}" -ge 6

expect "a capped subscriber stops at its cap" test "${CAPPED:-99}" -eq 4
# The COUNT, not the word: the line reads "session cap 0" when nothing was
# refused, so matching the label alone passes on a box with no cap at all.
expect "and the refusal is counted against that subscriber" \
  sh -c 'vppctl show bpa subscriber 100.64.0.6 | grep -q "session cap [1-9]"' 
expect_in "with an explanation the operator can act on" \
  "the cap is doing its job" "$SUB6"

# THE property. A pool-wide cap would pass everything above and fail here.
expect "the neighbour on the same public address is untouched" \
  test "${NEIGHBOUR:-0}" -eq 3

expect "the rate cap refused part of a burst" test "${RATED:-99}" -lt 10
expect "and let some of it through" test "${RATED:-0}" -ge 3
expect "rate refusals are counted apart from cap refusals" \
  sh -c 'vppctl show bpa subscriber 100.64.0.7 | grep -q "rate cap [1-9]"' 
expect_in "and say what they usually mean" "usually an infection" "$SUB7"
expect "the bucket refilled and the next session was allowed" \
  test "${REFILL:-0}" -gt "${RATED:-0}"

expect_in "the concurrent cap has its own counter" \
  "concurrent sessio" "$ERR"
expect_in "and so does the rate cap" "opening sessions faster" "$ERR"

expect "with no bounds, eight sessions are eight sessions" \
  test "${UNCAPPED:-0}" -eq 8

expect_in "the VTY set the bounds on the dataplane" " max-sessions 64" "$VTY_CONF"
expect_in "including the rate" " max-session-rate 20" "$VTY_CONF"
expect_in "and the trail recorded them" "limits sessions 64 rate 20" "$NEW"
# Both of these were GREEN in `sh tools/lab.sh limits off`, where the commit
# above never runs: an empty trail contains no "del pool lab", and a box that
# changed nothing dropped nothing. They are about what the CHANGE did, so they
# are predicated on the trail's record that there was one.
case "$NEW" in *"limits pool lab"*) APPLIED=yes ;; *) APPLIED=no ;; esac
applied_and() { [ "$APPLIED" = yes ] && "$@"; }
DEL_IN=$(printf '%s\n' "$NEW" | grep -c "del pool lab" || true)
expect "the bounds change was applied at all, and recorded" \
  test "$APPLIED" = yes
expect "without recreating the pool" applied_and test "${DEL_IN:-1}" -eq 0
# The reason the pool must not be recreated, measured rather than argued.
expect "and without dropping the sessions it held" \
  applied_and test "${AFTER_VTY:-0}" -ge "${BEFORE_VTY:-0}"

verdict
