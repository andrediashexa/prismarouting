#!/bin/sh
# Static port forwarding: an inbound port pinned to one subscriber's socket.
#
#   sh tools/lab.sh forward        with the forwards configured
#   sh tools/lab.sh forward off    without them - the assertions MUST fail
#
# The second form is the point. Four of the checks below pass by construction
# unless a forward actually changes what the dataplane does, and running with
# the feature removed is the only way to know they can fail at all.
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
vppctl set ip neighbor pg0 100.64.3.253 02:00:00:00:00:01
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0

vppctl bpa pool lab inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1

# The mapping every number below is derived from, so it is stated once:
#
#   100.64.0.5 is subscriber 5 -> 200.150.0.17, block 1536-2047
#   100.64.0.6 is subscriber 6 -> 200.150.0.18, block 1536-2047
#   100.64.0.9 is subscriber 9 -> 200.150.0.17, block 2048-2559
#
# so 1600 is forwardable for .5, 1700 for .6, and 2048 belongs to .9.

tcp() { # tcp <src> <dst> <sport> <dport> <flags> <interface>
  H=$(python3 /src/lab/mktcp.py "$1" "$2" "$3" "$4" "$5")
  L=$(( ${#H} / 2 - 1 ))
  vppctl packet-generator new "name p limit 1 node ip4-input interface $6 \
      size ${L}-${L} data { hex $H }" >/dev/null
  vppctl clear trace >/dev/null
  vppctl trace add pg-input 5 >/dev/null
  vppctl packet-generator enable >/dev/null
  sleep 1
  vppctl packet-generator delete p >/dev/null 2>&1
}

udp() { # udp <src> <dst> <sport> <dport> <interface>
  vppctl packet-generator new "name p limit 1 node ip4-input interface $5 \
      size 60-60 data { UDP: $1 -> $2 \
                        UDP: $3 -> $4 \
                        incrementing 8 }" >/dev/null
  vppctl clear trace >/dev/null
  vppctl trace add pg-input 5 >/dev/null
  vppctl packet-generator enable >/dev/null
  sleep 1
  vppctl packet-generator delete p >/dev/null 2>&1
}

trace() { vppctl show trace | grep -E "BPA (in2out|out2in)"; }
sess() { vppctl show bpa session | grep -o "total: [0-9]*" | awk '{print $2}'; }

# A released port goes straight back to the allocator instead of waiting out
# its hold-down, so section 1 can hand the port back before section 2 claims
# it. Without that, section 7 would find port 1600 busy for a reason that has
# nothing to do with the forward, and would pass on a box that has no
# forwarding at all - which is how an assertion comes to test nothing.
vppctl bpa timeouts port-holddown 0

echo "=========== 1. before the forward: the port is ordinary ==========="
echo "the subscriber sends from source port 1600, and port preservation"
echo "hands it public port 1600 because nothing has claimed it:"
tcp 100.64.0.5 8.8.8.8 1600 443 syn pg0
BEFORE=$(trace)
echo "$BEFORE" | sed 's/^/  /'
echo "and then closes it, so the port is free again and section 7 is"
echo "measuring the reservation rather than this session:"
tcp 100.64.0.5 8.8.8.8 1600 443 rst pg0
n=0
until [ "$(sess)" = "0" ] || [ "$n" -ge 20 ]; do sleep 1; n=$((n+1)); done
EMPTY=$(vppctl show bpa session | tail -1)
echo "  $EMPTY (after ${n}s)"

echo
echo "=========== 2. configuring the forward ==========="
if [ "$MODE" = off ]; then
  echo "  MODE=off: no forward is configured. Every behaviour check below"
  echo "  MUST fail; if any of them passes it was never testing anything."
else
  vppctl bpa forward lab tcp 1600 to 100.64.0.5 80
  vppctl bpa forward lab udp 1700 to 100.64.0.6 53
  vppctl show bpa forward | sed 's/^/  /'
  # Nothing below means anything if the forward was refused, so this is a
  # guard and not a check: it stops the run rather than reporting a failure
  # in the assertions that follow.
  vppctl show bpa forward | grep -q "200.150.0.17:1600 -> 100.64.0.5:80" || {
    echo "the forward was refused; nothing below would mean anything"
    vppctl show bpa forward
    exit 1
  }
  echo "  the session that held public port 1600 was taken off it:"
  vppctl show bpa session | sed 's/^/  /'
  # The other way to state a forward: as part of the pool definition, which is
  # the shape a candidate configuration takes and the only shape a commit
  # check can validate. It has to reach the dataplane as a working mapping and
  # not merely as a line in the configuration.
  #
  #   172.16.0.5 is subscriber 5 of this pool -> 198.51.100.17, block 1088-1151
  vppctl bpa pool inline inside 172.16.0.0/24 outside 198.51.100.16/30 \
      block 64 forward tcp 1100 to 172.16.0.5 80
  vppctl ip route add 172.16.0.0/24 via 100.64.3.253 pg0
fi

echo
echo "=========== 3. unsolicited inbound reaches the subscriber ==========="
echo "expected: 200.150.0.17:1600 -> 100.64.0.5:80"
tcp 8.8.8.8 200.150.0.17 33000 1600 syn pg1
INBOUND=$(trace)
echo "$INBOUND" | sed 's/^/  /'

echo
echo "=========== 3b. the same, for a forward given with the pool ==========="
echo "expected: 198.51.100.17:1100 -> 172.16.0.5:80"
tcp 8.8.8.8 198.51.100.17 33000 1100 syn pg1
INLINE=$(trace)
echo "$INLINE" | sed 's/^/  /'

echo
echo "=========== 4. the port next to it still does not ==========="
echo "expected: no session. A forward that opens its neighbours is a hole,"
echo "not a forward."
tcp 8.8.8.8 200.150.0.17 33000 1601 syn pg1
NEIGHBOUR=$(trace)
echo "$NEIGHBOUR" | sed 's/^/  /'
ERR_NEIGHBOUR=$(vppctl show errors | grep "no session for destination")

echo
echo "=========== 5. the subscriber answers on the forwarded port ==========="
echo "expected: 100.64.0.5:80 -> 200.150.0.17:1600"
tcp 100.64.0.5 8.8.8.8 80 33000 "syn,ack" pg0
ANSWER=$(trace)
echo "$ANSWER" | sed 's/^/  /'

echo
echo "=========== 6. the subscriber speaking first, on UDP ==========="
echo "expected: 100.64.0.6:53 -> 200.150.0.18:1700, before anything has"
echo "arrived for it - the forward has to be found on the way out too,"
echo "otherwise the customer's own traffic contradicts the configuration."
udp 100.64.0.6 8.8.8.8 53 33000 pg0
OUTFIRST=$(trace)
echo "$OUTFIRST" | sed 's/^/  /'

echo
echo "=========== 7. the forwarded port is reserved ==========="
echo "the same request as section 1 - source port 1600, so port preservation"
echo "asks for public port 1600 - and it must be refused this time. On UDP,"
echo "because the bitmap has no protocol dimension: a port forwarded for TCP"
echo "is a port, and handing it out for UDP would put two mappings on it."
udp 100.64.0.5 8.8.8.8 1600 8443 pg0
RESERVED=$(trace)
echo "$RESERVED" | sed 's/^/  /'

echo
echo "=========== 8. what has to be refused, and why ==========="
vppctl bpa pool dyn inside 10.0.0.0/24 outside 203.0.113.16/30 block 64 mode dynamic >/dev/null
R_OTHER=$(vppctl bpa forward lab tcp 2048 to 100.64.0.5 80 2>&1)
R_RANGE=$(vppctl bpa forward lab tcp 80 to 100.64.0.5 80 2>&1)
R_NOSUB=$(vppctl bpa forward lab tcp 1601 to 100.64.99.5 80 2>&1)
R_DYN=$(vppctl bpa forward dyn tcp 2000 to 10.0.0.5 80 2>&1)
R_DUP=$(vppctl bpa forward lab tcp 1600 to 100.64.0.5 81 2>&1)
R_MISS=$(vppctl bpa forward lab tcp 4444 del 2>&1)
for r in "$R_OTHER" "$R_RANGE" "$R_NOSUB" "$R_DYN" "$R_DUP" "$R_MISS"; do
  echo "  $r"
done

echo
echo "=========== 9. the configuration and the trail ==========="
CONF=$(vppctl show bpa config)
echo "$CONF" | grep -E "forward|cgnat pool" | sed 's/^/  /'
TRAIL=$(cat /var/log/hexarouting/config-history.log 2>/dev/null)
echo "$TRAIL" | grep "forward" | tail -2 | sed 's/^/  /'

echo
echo "=========== 10. removing it gives the port back ==========="
if [ "$MODE" != off ]; then
  vppctl bpa forward lab tcp 1600 del
fi
tcp 100.64.0.5 8.8.8.8 1600 9443 syn pg0
GIVEN_BACK=$(trace)
echo "$GIVEN_BACK" | sed 's/^/  /'

echo
echo "=========== counters ==========="
ERR=$(vppctl show errors)
echo "$ERR" | grep -iE "bpa-(in2out|out2in)" | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

# The precondition. Without it section 7 proves nothing: a subscriber that
# never gets port 1600 in the first place would pass the reservation check on
# a box with no forwarding at all.
expect_in "port 1600 was available before the forward existed" \
  "100.64.0.5:1600 -> 200.150.0.17:1600" "$BEFORE"
expect_in "and was free again when the forward was configured" \
  "total: 0" "$EMPTY"

expect_in "an unsolicited inbound packet reached the subscriber" \
  "200.150.0.17:1600 -> 100.64.0.5:80" "$INBOUND"
expect_in "a forward given on the pool line works, not just shows" \
  "198.51.100.17:1100 -> 172.16.0.5:80" "$INLINE"
expect_not_in "the neighbouring port was not translated" \
  "-> 100.64.0.5" "$NEIGHBOUR"
expect_in "the neighbouring port was counted as having no session" \
  "no session for destination" "$ERR_NEIGHBOUR"
expect_in "the subscriber's reply left on the forwarded port" \
  "100.64.0.5:80 -> 200.150.0.17:1600" "$ANSWER"
expect_in "a forward is found on the way out as well" \
  "100.64.0.6:53 -> 200.150.0.18:1700" "$OUTFIRST"
expect_not_in "the forwarded port was not handed to another flow" \
  "-> 200.150.0.17:1600" "$RESERVED"
expect_in "the reserved request still got a port" \
  "100.64.0.5:1600 -> 200.150.0.17:" "$RESERVED"
# "show errors" truncates the description, so the assertion matches what it
# actually prints rather than the string the counter is declared with.
expect_in "sessions opened from a forward were counted" \
  "sessions opened from a static port" "$ERR"

expect_in "a port in another subscriber's block names its owner" \
  "100.64.0.9" "$R_OTHER"
expect_in "a port outside the pool's range is refused" \
  "outside pool" "$R_RANGE"
expect_in "an address that is not a subscriber is refused" \
  "not a subscriber" "$R_NOSUB"
expect_in "a dynamic pool is refused, with the reason" \
  "dynamic mode" "$R_DYN"
expect_in "a second forward on the same public port is refused" \
  "already forwarded" "$R_DUP"
expect_in "removing a forward that does not exist is refused" \
  "tcp port 4444 is not forwarded" "$R_MISS"

expect_in "the forward is in the running configuration" \
  " forward tcp 1600 to 100.64.0.5 80" "$CONF"
expect_in "the forward is in the configuration trail" \
  "forward tcp/1600->100.64.0.5:80" "$TRAIL"
expect_in "removing the forward gave the port back to the allocator" \
  "100.64.0.5:1600 -> 200.150.0.17:1600" "$GIVEN_BACK"

verdict
