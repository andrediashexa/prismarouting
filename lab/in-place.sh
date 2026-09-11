#!/bin/sh
# Changing a per-pool setting must not recreate the pool (#69).
#
#   sh tools/lab.sh in-place        the settings that can change in place
#   sh tools/lab.sh in-place off    ...
#
# Two directions, and the second is what makes the first mean anything. A
# setting that can change in place keeps every session; a setting that changes
# the MAPPING must rebuild the pool and take the sessions with it. The same
# measurement, required to come out both ways.
set -u
MODE=${1:-on}

mkdir -p /run/vpp /vpp_plugins /var/log/hexarouting /etc/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh
use_binary_api
HX="python3 /src/src/hexash/hexash.py"

mkdir -p /var/run/frr /etc/frr
printf 'hostname hexarouting\n' > /etc/frr/zebra.conf
chown -R frr:frr /var/run/frr /etc/frr 2>/dev/null
/usr/lib/frr/zebra -d -A 127.0.0.1 -f /etc/frr/zebra.conf 2>/dev/null

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
vppctl bpa pool live inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
# Long enough that nothing expires while the lab runs: this is about what a
# commit does to sessions, not about lifetimes.
vppctl bpa timeouts udp 600

TRAIL=/var/log/hexarouting/config-history.log
sess() { vppctl show bpa session | grep -o "total: [0-9]*" | awk '{print $2}'; }

traffic() { # traffic <first-port> <count>
  i=0
  while [ "$i" -lt "$2" ]; do
    vppctl packet-generator new "name t$i limit 1 node ip4-input \
        interface pg0 size 60-60 data { UDP: 100.64.0.5 -> 8.8.8.8 \
                                        UDP: $(( $1 + i )) -> 53 \
                                        incrementing 8 }" >/dev/null
    i=$(( i + 1 ))
  done
  vppctl packet-generator enable >/dev/null
  sleep 1
  i=0
  while [ "$i" -lt "$2" ]; do
    vppctl packet-generator delete "t$i" >/dev/null 2>&1
    i=$(( i + 1 ))
  done
}

echo "=========== 1. a pool carrying traffic ==========="
traffic 41000 5
BEFORE=$(sess)
vppctl show bpa subscriber 100.64.0.5 | head -4 | sed 's/^/  /'

echo
echo "=========== 2. five settings, one commit, none of them the mapping ==="
echo "an ALG, a timeout, max-blocks, the filtering mode and the MSS clamp."
echo "Every one of these used to delete the pool and build it again."
MARK=$(wc -l < $TRAIL 2>/dev/null || echo 0)
if [ "$MODE" = off ]; then
  echo "  MODE=off: nothing is changed, so nothing is kept and nothing is"
  echo "  applied. The checks that claim either MUST fail."
else
  $HX -c "configure" -c "cgnat pool live" \
      -c "alg rtsp off" -c "timeout udp 900" -c "max-blocks 2" \
      -c "filter address-dependent" -c "tcp-mss 1400" \
      -c "exit" -c "commit" 2>&1 | sed 's/^/  /'
fi
AFTER=$(sess)
IN_TRAIL=$(tail -n +$((MARK + 1)) $TRAIL 2>/dev/null)
CONF=$(vppctl show bpa config)
echo "  sessions before: $BEFORE, after: $AFTER"
printf '%s\n' "$CONF" | sed -n '/cgnat pool live/,/exit/p' | sed 's/^/    /'

echo
echo "=========== 3. and one that IS the mapping ==========="
echo "block-size changes what every port means, so every session on the pool"
echo "is invalid the moment it changes. The same measurement, and it has to"
echo "come out the other way - otherwise section 2 was measuring nothing."
MARK2=$(wc -l < $TRAIL 2>/dev/null || echo 0)
MAP_BEFORE=$(sess)
# Since #23 this is refused until it is asked for by name. The refusal is
# asserted below rather than stepped over: a control that quietly stopped
# applying its own change would leave section 2 passing on a box that never
# dropped a session for any reason at all.
REFUSED=$($HX -c "configure" -c "cgnat pool live" -c "block-size 1024" \
              -c "exit" -c "commit" 2>&1)
printf '%s\n' "$REFUSED" | tail -4 | sed 's/^/  /'
REFUSED_AFTER=$(sess)
$HX -c "configure" -c "cgnat pool live" -c "block-size 1024" \
    -c "exit" -c "commit remap" 2>&1 | tail -2 | sed 's/^/  /'
MAP_AFTER=$(sess)
MAP_TRAIL=$(tail -n +$((MARK2 + 1)) $TRAIL 2>/dev/null)
echo "  sessions before: $MAP_BEFORE, after: $MAP_AFTER"
printf '%s\n' "$MAP_TRAIL" | grep -o "^[^ ]* [a-z]* pool live" | sed 's/^/    /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

expect "the pool was carrying sessions to begin with" test "${BEFORE:-0}" -ge 5

# Both of these were GREEN in `sh tools/lab.sh in-place off` - the run this
# file's own header says they MUST fail in. "Kept every session" is true of a
# box that changed nothing, and "did not recreate the pool" is true of a trail
# with nothing in it. Neither says anything until the change is known to have
# happened, so both are predicated on the trail's record of it instead of
# standing beside it. What they guard is #69: a pool rebuilt behind a setting
# change drops every session on it, which is every subscriber behind it.
case "$IN_TRAIL" in *"settings pool live"*) APPLIED=yes ;; *) APPLIED=no ;; esac
applied_and() { [ "$APPLIED" = yes ] && "$@"; }
DEL_IN=$(printf '%s\n' "$IN_TRAIL" | grep -c "del pool live" || true)
echo "  the trail's record of the in-place change:" \
     "$(printf '%s\n' "$IN_TRAIL" | grep -o '^[^ ]* [a-z-]* pool live' | tr '\n' ' ')"
expect "the in-place change was applied at all, and recorded" \
  test "$APPLIED" = yes
expect "an in-place change kept every session" \
  applied_and test "${AFTER:-0}" -eq "${BEFORE:-99}"
expect "and did not recreate the pool" applied_and test "${DEL_IN:-1}" -eq 0
expect_in "the ALG switch reached the dataplane" " alg rtsp off" "$CONF"
expect_in "the timeout did too" " timeout udp 900" "$CONF"
expect_in "and max-blocks" " max-blocks 2" "$CONF"
expect_in "and the filtering mode" " filter address-dependent" "$CONF"
expect_in "and the MSS clamp" " tcp-mss 1400" "$CONF"

# The other direction. Without it, "kept every session" would also pass on a
# box that never drops a session for any reason at all.
expect_in "a mapping change is refused until it is named" \
  "re-maps subscribers" "$REFUSED"
expect "and until then it drops nothing" \
  test "${REFUSED_AFTER:-0}" -eq "${MAP_BEFORE:-99}"
expect "a mapping change does drop them" \
  test "${MAP_AFTER:-99}" -lt "${MAP_BEFORE:-0}"
expect_in "and does recreate the pool" "del pool live" "$MAP_TRAIL"

verdict
