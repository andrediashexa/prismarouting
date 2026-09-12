#!/bin/sh
# Extended blocks: a subscriber that fills its block gets another.
#
# The behaviour this replaces is correct and terrible: natEvent 12 is logged
# and the packet is dropped, so one customer's internet stops while the box is
# healthy and the neighbours are fine.
#
# Only dynamic mode. A deterministic pool has no spare block by construction -
# the formula assigns every block to some subscriber - so extending there means
# taking a block from somebody else and breaking their mapping silently. That
# needs a reserve carved out of capacity, which is issue #39.
set -u
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up; vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0

# Tiny blocks so a subscriber fills one with a handful of packets. Four blocks
# on one public address, and a cap of two per subscriber.
vppctl bpa pool ext inside 100.64.0.0/22 outside 200.150.0.16/32 \
    ports 1024-1039 block 4 mode dynamic max-blocks 2
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa timeouts udp 600 >/dev/null

echo "=========== pool: 16 ports / 4-port blocks = 4 blocks, cap 2 ==========="
vppctl show bpa pool | sed 's/^/  /'

send() {   # subscriber, source port
  vppctl packet-generator delete s >/dev/null 2>&1
  vppctl packet-generator new "name s limit 1 node ip4-input interface pg0 \
      size 60-60 data { UDP: 100.64.0.$1 -> 8.8.8.8 UDP: $2 -> 53 incrementing 8 }"
  vppctl packet-generator enable >/dev/null; sleep 0.4
  vppctl packet-generator delete s >/dev/null 2>&1
}

echo
echo "=========== one subscriber, five flows into a four-port block ==========="
for sp in 5001 5002 5003 5004; do send 5 $sp; done
echo "  after four flows: $(vppctl show bpa blocks | grep total | sed 's/^ *//')"

send 5 5005
echo "  after the fifth:  $(vppctl show bpa blocks | grep total | sed 's/^ *//')"
# The total line, not the first line that matches. "show bpa blocks" prints a
# row per thread and then the total, so "head -1" read a PER-THREAD row - and
# with workers the subscriber's blocks are all on its owner, which is not
# thread 0. The lab then reported 0 extensions while the total said 1 and the
# box was behaving correctly. It only ever worked because a single-threaded
# box has one thread row and it equals the total.
EXTS=$(vppctl show bpa blocks \
       | sed -n 's/^ *total: .* allocations (\([0-9][0-9]*\) extensions).*/\1/p')
SESS=$(vppctl show bpa session | grep -o "total: [0-9]*" | awk '{print $2}')
echo "  extensions: ${EXTS:-0}   sessions: ${SESS:-0}"

echo
echo "=========== the cap holds: a ninth flow must be refused ==========="
for sp in 5006 5007 5008; do send 5 $sp; done
send 5 5009
REFUSED=$(vppctl show errors | grep -i "port block exhausted" | awk '{print $1}' | head -1)
SESS2=$(vppctl show bpa session | grep -o "total: [0-9]*" | awk '{print $2}')
echo "  sessions now: ${SESS2:-0}   refusals: ${REFUSED:-0}"
vppctl show bpa blocks | sed 's/^/  /'

echo
echo
echo "=========== an extension the subscriber stopped needing goes back ==="
# #216 asked whether bpa_dyn_active() - declared, defined, documented, and
# called by nothing - is a missing call or a dead function. This section is
# the answer, measured on the box rather than read off the source.
#
# What bpa_dyn_active() would do is clear the idle stamp on EVERY block a
# subscriber holds, extensions included. bpa_dyn_idle() sets those stamps, and
# it is called at one moment only: when the subscriber's session count reaches
# ZERO. At that moment every block it holds is empty, extension and primary
# alike.
#
# So a subscriber that goes quiet and comes back with ONE session needs ONE
# block. Its extension is empty and should go back to the pool - and it does,
# because bpa_dyn_acquire() clears the stamp on the PRIMARY only. Calling
# bpa_dyn_active() there would clear the extension's stamp too and keep an
# empty block owned forever, which is exactly the leak the comment above
# bpa_dyn_idle() exists to warn about.
#
# That makes the function not merely unused but WRONG to call, and this
# assertion is what would go red if somebody wired it in "to fix the gap".
vppctl bpa block-holddown 5
vppctl bpa timeouts udp 3 >/dev/null
EXT_BEFORE=$(vppctl show bpa blocks \
             | sed -n 's/^ *total: \([0-9][0-9]*\)\/.*/\1/p')
echo "  blocks in use with the extension live: ${EXT_BEFORE:-?}"

# Everything expires, so the subscriber's session count reaches zero and BOTH
# of its blocks are stamped. That is the only moment bpa_dyn_idle() runs.
sleep 6

# THE TIMEOUT GOES BACK UP BEFORE THE FLOW THAT BRINGS THE SUBSCRIBER BACK,
# and the order is the measurement rather than housekeeping. The first version
# of this section left it at 3 seconds, so the returning session expired after
# three, the subscriber reached zero sessions again, bpa_dyn_idle() stamped
# both blocks a second time, and the hold-down took them BOTH - 0 in use, which
# says nothing about extensions at all.
#
# What has to be true for the question to be asked is that the subscriber is
# still ACTIVE on its primary while the extension's stale stamp expires. A
# long-lived session is what makes that window exist.
vppctl bpa timeouts udp 600 >/dev/null
send 5 30000
sleep 1
echo "  one flow back from the same subscriber:"
vppctl show bpa blocks | sed 's/^/  /'

# Longer than the hold-down, so the stamp that was NOT cleared expires while
# the session that cleared the primary's is still alive.
sleep 8
EXT_AFTER=$(vppctl show bpa blocks \
            | sed -n 's/^ *total: \([0-9][0-9]*\)\/.*/\1/p')
SUB_AFTER=$(vppctl show bpa subscriber 100.64.0.5)
echo "  blocks in use afterwards: ${EXT_AFTER:-?}"
echo "$SUB_AFTER" | sed 's/^/  /'
vppctl bpa block-holddown 300 >/dev/null

echo "=========== VERDICT ==========="
expect "the subscriber filled its first block and got a second" \
  test "${EXTS:-0}" -ge 1
expect "eight sessions fit in two four-port blocks" test "${SESS2:-0}" = "8"
expect "the ninth was refused, so the cap is a cap" test -n "${REFUSED:-}"
# "allocations" is a column label "show bpa blocks" prints whatever the pool
# did, so this passed on a pool that had allocated nothing at all - let alone
# extended anything. The count, off the total line: a subscriber that got an
# extension has TWO allocations, its first block and the extension.
ALLOCS=$(vppctl show bpa blocks \
         | sed -n 's/^ *total: .* in use, \([0-9][0-9]*\) allocations.*/\1/p')
echo "  allocations counted: ${ALLOCS:-<none>}   extensions: ${EXTS:-0}"
expect "the extension counted as an allocation of its own" \
  test "${ALLOCS:-0}" -ge 2
# The precondition. Without it "one block afterwards" is also what a box that
# never handed out an extension at all would say, and the assertion below
# would be measuring nothing.
expect "the subscriber really was holding two blocks first" \
  test "${EXT_BEFORE:-0}" -ge 2
# The answer to #216. One block, not two: the extension went back while the
# subscriber was ACTIVE on its primary, which is what makes bpa_dyn_active()
# wrong to call rather than merely unused.
expect "and holds one after coming back with a single flow" \
  test "${EXT_AFTER:-0}" -eq 1
# And it is still translating, so the block that stayed is the right one. A
# subscriber whose primary had been reaped too would also report one block:
# the next one it was given.
expect_in "the block it kept is its own, and it is still translating" \
  "200.150.0.16" "$SUB_AFTER"
# The session outliving the hold-down is what makes the window real. Without
# this the run above could have reached "one block" by the subscriber going
# quiet a second time and being given a fresh one, which is a different box.
expect_not_in "and it never went quiet again while we were watching" \
  "no live state" "$SUB_AFTER"
verdict
