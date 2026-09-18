#!/bin/sh
# Static port forwarding: an inbound port pinned to one subscriber's socket.
#
#   sh tools/lab.sh forward        with the forwards configured
#   sh tools/lab.sh forward off    without them - the assertions MUST fail
#
# The second form is the point. Sixteen of the checks below pass by construction
# unless a forward actually changes what the dataplane does, and running with
# the feature removed is the only way to know they can fail at all.
#
# Section 11 is where that matters most. A dynamic block stays put for the
# length of the hold-down whether or not anything pinned it, so an assertion
# taken a few seconds after the last packet would be measuring the hold-down
# and calling it a pin. It waits six times the hold-down, and keeps two
# unpinned blocks as a control: if those are still held, nothing was reaped and
# the section proved nothing.
#
# OFF-RED-AT-LEAST: 26
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

mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing
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
EMPTY=$(vc show bpa session | tail -1)
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
  # The same public PORT, on two different public ADDRESSES of one pool.
  # 100.64.0.5 sends from 200.150.0.17 and 100.64.0.6 from 200.150.0.18, so
  # 1650 on one of them is not 1650 on the other: they are two sockets, and
  # the pool has already given them to two customers. A pool with more than
  # one public address is the ordinary case, so a port that can only be sold
  # once across the whole pool is a port that can be sold to one customer in
  # four here (#214).
  R_SHARED_A=$(vppctl bpa forward lab tcp 1650 to 100.64.0.5 8080 2>&1)
  R_SHARED_B=$(vppctl bpa forward lab tcp 1650 to 100.64.0.6 8081 2>&1)
  echo "  the same port on each of two public addresses:"
  echo "  .17: ${R_SHARED_A:-accepted}"
  echo "  .18: ${R_SHARED_B:-accepted}"
fi
SHARED=$(vppctl show bpa forward lab)

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
echo "=========== 3c. one public port, two public addresses ==========="
echo "expected: 200.150.0.17:1650 -> 100.64.0.5:8080"
tcp 8.8.8.8 200.150.0.17 33000 1650 syn pg1
SHARED_A=$(trace)
echo "$SHARED_A" | sed 's/^/  /'
echo "expected: 200.150.0.18:1650 -> 100.64.0.6:8081"
echo "on its own address, and reaching its own subscriber - the packet is"
echo "what says the return path kept the address in the key rather than"
echo "answering with whichever forward happened to hold the port."
tcp 8.8.8.8 200.150.0.18 33000 1650 syn pg1
SHARED_B=$(trace)
echo "$SHARED_B" | sed 's/^/  /'

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
R_OTHER=$(vppctl bpa forward lab tcp 2048 to 100.64.0.5 80 2>&1)
R_RANGE=$(vppctl bpa forward lab tcp 80 to 100.64.0.5 80 2>&1)
R_NOSUB=$(vppctl bpa forward lab tcp 1601 to 100.64.99.5 80 2>&1)
R_DUP=$(vppctl bpa forward lab tcp 1600 to 100.64.0.5 81 2>&1)
R_MISS=$(vppctl bpa forward lab tcp 4444 del 2>&1)
for r in "$R_OTHER" "$R_RANGE" "$R_NOSUB" "$R_DUP" "$R_MISS"; do
  echo "  $r"
done

echo
echo "=========== 9. the configuration and the trail ==========="
CONF=$(vppctl show bpa config)
echo "$CONF" | grep -E "forward|cgnat pool" | sed 's/^/  /'
TRAIL=$(cat /var/log/prisma-routing/config-history.log 2>/dev/null)
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
echo "=========== 11. a dynamic pool: the block is what gets pinned ==========="
# A dynamic pool hands blocks out on the first flow and takes them back after
# the hold-down, so a forwarded port would follow its block to whichever
# subscriber got it next. The forward pins the block instead, and the block it
# pins is the one the pool's arithmetic gives that subscriber - the same one a
# deterministic pool would have given it.
#
#   10.0.0.0/24 over 203.0.113.16/30, block 64, ports 1024-65535
#   -> 1008 blocks per address, 4032 in the pool, interleaved:
#      subscriber s -> 203.0.113.(16 + s mod 4), block s/4
#
#   10.0.0.1  -> 203.0.113.17 ports 1024-1087   (block index 1)
#   10.0.0.5  -> 203.0.113.17 ports 1088-1151   (block index 5)
#
# The allocator hands out free blocks in increasing index order, so the first
# two subscribers to send take block 0 and block 1 - and block 1 is the one
# 10.0.0.1 would need.
vppctl bpa pool dyn inside 10.0.0.0/24 outside 203.0.113.16/30 block 64 \
    mode dynamic >/dev/null
vppctl ip route add 10.0.0.0/24 via 100.64.3.253 pg0

echo "  two subscribers take the first two blocks:"
tcp 10.0.0.20 8.8.8.8 40000 443 syn pg0
tcp 10.0.0.21 8.8.8.8 40001 443 syn pg0
vppctl show bpa blocks | sed 's/^/  /'

echo
echo "  a forward whose block somebody else is holding, at commit check:"
R_HELD=$(vppctl bpa forward dyn tcp 1030 to 10.0.0.1 80 check 2>&1)
echo "  $R_HELD"

if [ "$MODE" = off ]; then
  echo "  MODE=off: no forward on the dynamic pool either, so its block is"
  echo "  nobody's and the hold-down takes it like any other."
else
  vppctl bpa forward dyn tcp 1100 to 10.0.0.5 80
  # The #214 measurement itself, on the pool it was taken on. 10.0.0.4 is
  # subscriber 4 of this pool and sends from 203.0.113.16; 10.0.0.5 is
  # subscriber 5 and sends from 203.0.113.17. Both are given port 1100, which
  # sits in block 1088-1151 of each of their own addresses. A dynamic pool is
  # where an operator has many public addresses, so this is the case that
  # decides how many customers can be sold an inbound port.
  R_DYN_SHARED=$(vppctl bpa forward dyn tcp 1100 to 10.0.0.4 8080 2>&1)
  echo "  the same port on the other public address: ${R_DYN_SHARED:-accepted}"
fi
DYN_FWD=$(vppctl show bpa forward dyn)
echo "$DYN_FWD" | sed 's/^/  /'

echo
echo "  unsolicited inbound, expected: 203.0.113.17:1100 -> 10.0.0.5:80"
tcp 8.8.8.8 203.0.113.17 33000 1100 syn pg1
DYN_IN=$(trace)
echo "$DYN_IN" | sed 's/^/  /'
echo "  and on the other address, expected: 203.0.113.16:1100 -> 10.0.0.4:8080"
tcp 8.8.8.8 203.0.113.16 33000 1100 syn pg1
DYN_IN_B=$(trace)
echo "$DYN_IN_B" | sed 's/^/  /'
DYN_TRAIL=$(grep "forward" /var/log/prisma-routing/config-history.log 2>/dev/null | tail -1)
echo "  trail: $DYN_TRAIL"

echo
echo "  --- and now the part the hold-down must not be allowed to prove ---"
# The trap this section exists to avoid: a block stays put for the length of
# the hold-down whether or not anything pinned it, so an idle subscriber that
# is only idle for a few seconds proves nothing at all. The hold-down is set
# to 5s and the subscriber is then left alone for 30 - six times it - and the
# two unpinned blocks taken above are the control: if they are still held, the
# hold-down never ran and this section is measuring nothing.
vppctl bpa block-holddown 5
vppctl bpa timeouts udp 3 tcp-syn 3 tcp-transitory 3 tcp-established 3 \
    >/dev/null
T0=$(date +%s)
echo "  hold-down 5s, sessions expire in 3s, idle for 30s:"
while [ "$(date +%s)" -lt $((T0 + 30)) ]; do sleep 1; done
IDLE=$(( $(date +%s) - T0 ))
echo "  ${IDLE}s with no traffic from any of them"
CTL_IDLE=$(vppctl show bpa subscriber 10.0.0.20)
PIN_IDLE=$(vppctl show bpa subscriber 10.0.0.5)
PIN_IDLE_B=$(vppctl show bpa subscriber 10.0.0.4)
BLOCKS=$(vppctl show bpa blocks)
printf '%s\n' "$CTL_IDLE" | sed 's/^/  |/'
printf '%s\n' "$PIN_IDLE" | sed 's/^/  |/'
printf '%s\n' "$BLOCKS" | sed 's/^/  |/'

echo
echo "  the packet, after all that idling:"
echo "  expected: 203.0.113.17:1100 -> 10.0.0.5:80"
tcp 8.8.8.8 203.0.113.17 33000 1100 syn pg1
DYN_AFTER=$(trace)
echo "$DYN_AFTER" | sed 's/^/  /'
echo "  expected: 203.0.113.16:1100 -> 10.0.0.4:8080"
tcp 8.8.8.8 203.0.113.16 33000 1100 syn pg1
DYN_AFTER_B=$(trace)
echo "$DYN_AFTER_B" | sed 's/^/  /'

echo
echo "  removing the forward gives the block back:"
# Removing by public port alone can no longer name ONE forward, because the
# port is now two of them on two addresses. Asked here, after everything else
# has been measured, because on a box that still keys the port to the pool
# this removes a forward that was never named and the sections below would
# then be measuring the removal.
R_AMBIG=$(vppctl bpa forward dyn tcp 1100 del 2>&1)
echo "  by port alone: ${R_AMBIG:-removed something without being told which}"
if [ "$MODE" != off ]; then
  vppctl bpa forward dyn tcp 1100 to 10.0.0.5 80 del
  vppctl bpa forward dyn tcp 1100 to 10.0.0.4 8080 del
fi
UNPINNED=$(vppctl show bpa blocks)
printf '%s\n' "$UNPINNED" | sed 's/^/  |/'

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
# One public port on two public addresses of one pool. The refusal these
# replace named a customer the operator had never heard of, because the port
# was being treated as the pool's rather than the address's (#214).
expect_in "the same public port was forwarded on the first public address" \
  "tcp 200.150.0.17:1650 -> 100.64.0.5:8080" "$SHARED"
expect_in "and on the second, which is a different socket" \
  "tcp 200.150.0.18:1650 -> 100.64.0.6:8081" "$SHARED"
expect_in "the first address reached its own subscriber" \
  "200.150.0.17:1650 -> 100.64.0.5:8080" "$SHARED_A"
# The assertion that fails if the address was dropped from the key on the way
# back in: the port alone would answer with whichever forward holds it.
expect_in "the second address reached its own subscriber" \
  "200.150.0.18:1650 -> 100.64.0.6:8081" "$SHARED_B"
expect_not_in "and not the subscriber on the other address" \
  "-> 100.64.0.5" "$SHARED_B"
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
expect_in "a second forward on the same public port is refused" \
  "already forwarded" "$R_DUP"
# The reason the refusal exists is that somebody already has the socket, so
# the refusal has to say who. "Refused" on its own sends the operator to the
# configuration to find out what they collided with.
expect_in "and it names the subscriber socket that holds it" \
  "100.64.0.5:80" "$R_DUP"
expect_in "removing a forward that does not exist is refused" \
  "tcp port 4444 is not forwarded" "$R_MISS"

expect_in "the forward is in the running configuration" \
  " forward tcp 1600 to 100.64.0.5 80" "$CONF"
expect_in "the forward is in the configuration trail" \
  "forward tcp/1600->100.64.0.5:80" "$TRAIL"
expect_in "removing the forward gave the port back to the allocator" \
  "100.64.0.5:1600 -> 200.150.0.17:1600" "$GIVEN_BACK"

# The dynamic pool.
expect_in "a forward on a dynamic pool was accepted" \
  "tcp 203.0.113.17:1100 -> 10.0.0.5:80" "$DYN_FWD"
expect_in "a block another subscriber holds is refused at commit check" \
  "which 10.0.0.21 is holding now" "$R_HELD"
expect_in "an unsolicited inbound packet reached the pinned subscriber" \
  "203.0.113.17:1100 -> 10.0.0.5:80" "$DYN_IN"
# The dynamic half of #214, on the pool the issue measured. A dynamic pool is
# where the operator has many public addresses, so this is the case that
# decides how many customers can be sold one inbound port.
expect_in "the same port was forwarded on another address of a dynamic pool" \
  "tcp 203.0.113.16:1100 -> 10.0.0.4:8080" "$DYN_FWD"
expect_in "and an inbound packet on that address reached ITS subscriber" \
  "203.0.113.16:1100 -> 10.0.0.4:8080" "$DYN_IN_B"
expect_not_in "and not the one on the neighbouring address" \
  "-> 10.0.0.5" "$DYN_IN_B"
expect_in "removing by public port alone is refused while it names two forwards" \
  "10.0.0.4" "$R_AMBIG"
expect_in "the pin is in the configuration trail, as the forward it derives from" \
  "forward tcp/1100->10.0.0.5:80" "$DYN_TRAIL"
# The control. Without it the assertion below passes on a box whose hold-down
# simply never ran, which is the one way this section could prove nothing.
expect_in "the hold-down did run: an unpinned block went back to the pool" \
  "no live state" "$CTL_IDLE"
# The two sentences whole, not their keywords: they are what an operator reads
# to learn why one block came back and the other did not, and
# test/test_plugin_cli_says.py counts a message as named only when something
# carries it whole (#366).
expect_in "and the screen says so in the sentence the operator reads" \
  "no live state: this subscriber holds no block and has no sessions on this box" "$CTL_IDLE"
expect_in "the pinned block is still the subscriber's after ${IDLE}s idle" \
  "PINNED by a forward" "$PIN_IDLE"
expect_in "and the screen says why it stayed, in the sentence the operator reads" \
  ", and has no sessions on this box - PINNED by a forward, so no hold-down will take it" "$PIN_IDLE"
expect_in "so is the block of the subscriber on the other public address" \
  "PINNED by a forward" "$PIN_IDLE_B"
expect_in "and the pool says so" "2 pinned" "$BLOCKS"
# The claim is about a packet, so it is asserted on the packet.
expect_in "an inbound packet on the forwarded port still reaches them" \
  "203.0.113.17:1100 -> 10.0.0.5:80" "$DYN_AFTER"
expect_in "and the same port on the other address still reaches the other" \
  "203.0.113.16:1100 -> 10.0.0.4:8080" "$DYN_AFTER_B"
expect_in "removing the last forward unpinned the block" \
  "0 pinned" "$UNPINNED"

verdict
