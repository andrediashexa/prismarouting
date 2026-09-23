#!/bin/sh
# Dynamic mode (PBA): block allocated on demand, returned after hold-down.
#
# A deliberately marginal pool: 1024 possible subscribers against 3 blocks.
#   64512 ports / 16384-port block = 3 blocks on a single public IP
# The blocks expected, in the order they are allocated: ports 1024, 17408, 33792.
set -u
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
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

vppctl bpa pool din inside 100.64.0.0/22 outside 200.150.0.16/32 block 16384 mode dynamic
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa block-holddown 5
vppctl show bpa pool | sed 's/^/  /'

pg() { vppctl packet-generator new "$1"; }
send() {
  pg "name s$1 limit 1 node ip4-input interface pg0 size 60-60 \
      data { UDP: 100.64.0.$1 -> 8.8.8.8 \
             UDP: 1234$2 -> 53 \
             incrementing 8 }"
  vppctl clear trace >/dev/null; vppctl trace add pg-input 5 >/dev/null
  vppctl packet-generator enable >/dev/null; sleep 1
  vppctl show trace | grep "BPA in2out" | sed 's/^/    /'
  vppctl packet-generator delete s$1 >/dev/null 2>&1
}

echo
echo "=========== 3 subscribers take the 3 blocks ==========="
for sub in 5 6 9; do echo "  subscriber 100.64.0.$sub:"; send $sub 5; done

echo
echo "=========== the same subscriber must NOT change block ==========="
echo "  100.64.0.5 again, different source port:"
send 5 6

echo
echo "=========== 4th subscriber: no free block ==========="
echo "  100.64.0.12:"
send 12 5
# The counter has to be searched for by the string the plugin emits. This line
# looked for the Portuguese version, which the plugin never emitted: it never
# matched, and a refusal showed up as absent output instead of a counter.
echo "  refusal counter:"
vppctl show errors | grep -i "no free block" | sed 's/^/    /'

echo
echo "=========== block state ==========="
vppctl show bpa blocks | sed 's/^/  /'

echo
echo "=========== coming back from the internet ==========="
# Port preservation kept 12345, which falls inside the first subscriber's
# 1024-17407 block. It is the real port, not the start of the block.
echo "  expected: 200.150.0.16:12345 -> 100.64.0.5:12345"
pg "name r1 limit 1 node ip4-input interface pg1 size 60-60 \
    data { UDP: 8.8.8.8 -> 200.150.0.16 \
           UDP: 53 -> 12345 \
           incrementing 8 }"
vppctl clear trace >/dev/null; vppctl trace add pg-input 5 >/dev/null
vppctl packet-generator enable >/dev/null; sleep 1
vppctl show trace | grep "BPA out2in" | sed 's/^/    /'
vppctl packet-generator delete r1 >/dev/null 2>&1

echo
echo "=========== hold-down: blocks return to the pool ==========="
# Only now do we shorten the timeout: doing it at the start would kill the
# sessions in the middle of the earlier tests.
vppctl bpa timeouts udp 3
echo "  sessions expire in 3s, blocks stay 5s in hold-down"
n=0
until vppctl show bpa blocks | grep -q "total: 0/3" || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
echo "  (${n}s)"
vppctl show bpa blocks | sed 's/^/  /'

echo
echo "=========== now the 4th subscriber succeeds ==========="
echo "  100.64.0.12:"
send 12 5

echo
echo "=========== a block outlives the sessions that took it ==========="
# What an operator is told about a subscriber between its last session and the
# end of the block hold-down. The block is still reserved - nobody else can be
# given it - so "no sessions" and "holds no block" are different answers, and
# the command that collapsed them into one reported a block as free while the
# allocator was still refusing it to everybody else (issue #74).
vppctl bpa block-holddown 300
echo "  100.64.0.6 takes a block, then goes quiet:"
send 6 5
n=0
until [ "$(vppctl show bpa session | sed -n 's/^total: \([0-9]*\) sessions/\1/p')" = "0" ] \
   || [ "$n" -ge 20 ]; do sleep 1; n=$((n+1)); done
SUB6=$(vppctl show bpa subscriber 100.64.0.6)
SESS6=$(vppctl show bpa session)
HELD=$(vppctl show bpa blocks)
printf '%s\n' "$SUB6" | sed 's/^/  |/'
printf '%s\n' "$SESS6" | sed -n '/total:/p' | sed 's/^/  |/'
printf '%s\n' "$HELD" | sed -n '/total:/p' | sed 's/^/  |/'

echo
echo "=========== what the pool can serve whatever the addresses are ==========="
# `usable` is a ceiling nobody is promised once there are workers: block
# allocation is sharded so it needs no lock, a subscriber draws only from its
# own worker's slice, and a subscriber index is the offset of its address
# within the inside set rather than a counter - so on any real address plan the
# two do not balance. This pool has three blocks; with two workers the slices
# are two and one, and three subscribers whose offsets land 1, 0, 1 leave one
# refused with a block free on the other worker (#210).
#
# The mechanism is not fixed here. What is fixed is the box claiming a number
# it cannot deliver, so the assertion is on what `show bpa pool` SAYS.
#
# Scoped to `din` by name because the section below adds two more pools and
# "show bpa pool" prints all of them: an unscoped needle would be satisfied by
# whichever pool happened to carry the words, which is an assertion about the
# output rather than about this pool.
stanza() { printf '%s\n' "$2" | awk -v n="$1" '/^pool /{ok=($2==n)} ok'; }
POOLCAP=$(stanza din "$(vppctl show bpa pool)" | grep -A1 "capacity")
printf '%s\n' "$POOLCAP" | sed 's/^/  /'

echo
echo "=========== every subscriber the claim covers gets a block ==========="
#
# The acceptance of #210. Whatever number the box prints, that many subscribers
# have to actually receive a block - asserted on traffic, because the claim is
# about what the dataplane does and nothing above it can stand in for that.
#
# Two pools, created here so that neither inherits the blocks the sections
# above are still holding:
#
#   tight  3 blocks in front of 1024 inside addresses. Every worker has more
#          addresses mapping to it than it has blocks, so the claim falls to
#          the smallest slice and the gap to `capacity` is the loss #210 is
#          about. This is `din`'s shape, with a clean pool behind it.
#   roomy  12 blocks in front of 8 inside addresses. No worker can be handed
#          more subscribers than it has blocks for, so the sharding costs
#          nothing and the claim is the whole inside set. Not a hypothetical: a
#          pool bought for growth looks exactly like this on the day it goes in,
#          and a claim of capacity/workers would understate it by half.
#
# The subscribers are chosen adversarially, because "whatever the addresses
# are" is a worst case and a lucky address set would prove nothing about it.
# Which thread holds the thinnest slice, and which thread an address lands on,
# are both ASKED OF THE BOX: a lab that recomputes the sharding rule ends up
# agreeing with itself rather than with the product.
vppctl bpa pool tight inside 100.65.0.0/22 outside 200.150.0.20/32 block 16384 mode dynamic
vppctl bpa pool roomy inside 100.66.0.8/29  outside 200.150.0.24/30 block 16384 mode dynamic
# A POOL OF THIS SECTION'S OWN, for the loan case at the bottom of this file.
#
# It needs a slice that can be filled while ANOTHER slice still has a block
# standing free, which the pools above cannot give it: by the time that
# section runs, `tight` is 3/3 in use - its blocks are held by subscribers of
# the sections before, under a 300 s hold-down - so a refusal there is honest
# exhaustion and the right answer is no loan at all. Measured, by reading the
# blocks at the moment of the refusal rather than assuming the pool was free.
#
# Four blocks over two addresses: with two workers the slices are two and
# two, so two subscribers of one worker fill their slice while two blocks
# stand free next door.
vppctl bpa pool lend  inside 100.67.0.0/24  outside 200.150.0.30/31 block 32768 mode dynamic

# The number the box promises for a pool: the guaranteed figure when it prints
# one, and the ceiling when it does not, because a pool that prints no caveat
# is promising the ceiling.
claim_of() {
  _c=$(printf '%s\n' "$1" \
       | sed -n 's/^ *\([0-9][0-9]*\) whatever the addresses are.*/\1/p')
  [ -n "$_c" ] || _c=$(printf '%s\n' "$1" \
       | sed -n 's/^ *capacity .*(usable \([0-9][0-9]*\)).*/\1/p')
  printf '%s' "$_c"
}
usable_of() {
  printf '%s\n' "$1" | sed -n 's/^ *capacity .*(usable \([0-9][0-9]*\)).*/\1/p'
}
thinnest() {
  printf '%s\n' "$1" \
    | sed -n 's|^ *thread \([0-9][0-9]*\): [0-9][0-9]*/\([0-9][0-9]*\) blocks.*|\2 \1|p' \
    | sort -n | head -1 | sed 's/.* //'
}
thread_of() {
  vppctl show bpa subscriber "$1" | sed -n 's/.*worker thread \([0-9][0-9]*\).*/\1/p'
}

# Sets CLAIM, USABLE, MISSING and REFUSED for one pool.
claim_check() {
  _pool=$1; shift
  _cap=$(stanza "$_pool" "$(vppctl show bpa pool)")
  CLAIM=$(claim_of "$_cap")
  USABLE=$(usable_of "$_cap")
  _t=$(thinnest "$(stanza "$_pool" "$(vppctl show bpa blocks)")")
  echo "  pool $_pool:"
  printf '%s\n' "$_cap" | grep -E "capacity|whatever" | sed 's/^ */    /'
  echo "    claims $CLAIM of $USABLE; thinnest slice is on thread $_t"

  # On that thread first, the rest after, and the claim's worth of them.
  _on=""; _off=""
  for _a in "$@"; do
    if [ "$(thread_of $_a)" = "$_t" ]; then _on="$_on $_a"; else _off="$_off $_a"; fi
  done
  SET=""; _i=0
  for _a in $_on $_off; do
    _i=$((_i+1)); [ "$_i" -le "$CLAIM" ] || break
    SET="$SET $_a"
  done
  echo "    sending:$SET"

  _i=0
  for _a in $SET; do
    _i=$((_i+1))
    vppctl packet-generator new "name k$_i limit 1 node ip4-input interface pg0 \
        size 60-60 \
        data { UDP: $_a -> 8.8.8.8 \
               UDP: 12345 -> 53 \
               incrementing 8 }" >/dev/null
  done
  vppctl clear trace >/dev/null; vppctl trace add pg-input 60 >/dev/null
  vppctl packet-generator enable >/dev/null; sleep 2
  TR=$(vppctl show trace | grep "BPA in2out")
  _j=0
  while [ "$_j" -lt "$_i" ]; do
    _j=$((_j+1)); vppctl packet-generator delete "k$_j" >/dev/null 2>&1
  done
  printf '%s\n' "$TR" | sed 's/^ */    /'

  # "no trace line at all" and "translated to 0.0.0.0:0" are separated on
  # purpose: the first is a lab that did not deliver its own packet and proves
  # nothing about the box, the second is the box refusing a subscriber it
  # claimed it could serve.
  MISSING=""; REFUSED=""
  FIRST_REFUSED=""
  for _a in $SET; do
    _l=$(printf '%s\n' "$TR" | grep "BPA in2out $_a:" | head -1)
    if [ -z "$_l" ]; then
      MISSING="$MISSING $_a"
    else
      case "$_l" in *"-> 0.0.0.0:0"*) REFUSED="$REFUSED $_a" ;; esac
    fi
  done
  FIRST_REFUSED=$REFUSED

  # AND AGAIN, AFTER A TICK (#210).
  #
  # A subscriber whose slice was empty is refused and the box then moves a
  # block to that slice, under the barrier bpa_expire_process takes once a
  # second. So "was this subscriber served" cannot be answered by the packet
  # that caused the loan - it can only be answered by the next one, which on
  # a real box is milliseconds later and on this lab has to be sent on
  # purpose.
  #
  # Sending it ALWAYS, not only when something was refused: a second pass
  # that runs on some boxes and not others is a lab whose numbers mean
  # different things on different runs. On a box that refused nobody this is
  # the same subscribers translating again, which is what they would do.
  [ -n "$REFUSED" ] && echo "    refused on the first pass:$REFUSED"
  sleep 3
  _i=0
  for _a in $SET; do
    _i=$((_i+1))
    vppctl packet-generator new "name r$_i limit 1 node ip4-input interface pg0 \
        size 60-60 \
        data { UDP: $_a -> 8.8.8.8 \
               UDP: 12345 -> 53 \
               incrementing 8 }" >/dev/null
  done
  vppctl clear trace >/dev/null; vppctl trace add pg-input 60 >/dev/null
  vppctl packet-generator enable >/dev/null; sleep 2
  TR=$(vppctl show trace | grep "BPA in2out")
  _j=0
  while [ "$_j" -lt "$_i" ]; do
    _j=$((_j+1)); vppctl packet-generator delete "r$_j" >/dev/null 2>&1
  done

  MISSING=""; REFUSED=""
  for _a in $SET; do
    _l=$(printf '%s\n' "$TR" | grep "BPA in2out $_a:" | head -1)
    if [ -z "$_l" ]; then
      MISSING="$MISSING $_a"
    else
      case "$_l" in *"-> 0.0.0.0:0"*) REFUSED="$REFUSED $_a" ;; esac
    fi
  done
  printf '%s\n' "$TR" | sed 's/^ */    /'
}

claim_check tight 100.65.0.1 100.65.0.2 100.65.0.3 100.65.0.4 100.65.0.5 \
                  100.65.0.6 100.65.0.7 100.65.0.8 100.65.0.9 100.65.0.10 \
                  100.65.0.11 100.65.0.12
TIGHT_CLAIM=$CLAIM; TIGHT_USABLE=$USABLE
TIGHT_MISSING=$MISSING; TIGHT_REFUSED=$REFUSED
echo
claim_check roomy 100.66.0.8 100.66.0.9 100.66.0.10 100.66.0.11 \
                  100.66.0.12 100.66.0.13 100.66.0.14 100.66.0.15
ROOMY_CLAIM=$CLAIM; ROOMY_USABLE=$USABLE
ROOMY_MISSING=$MISSING; ROOMY_REFUSED=$REFUSED

# =========================================================================
# A subscriber refused with blocks free next door, and what happens next.
#
# THIS IS #210's OWN CASE, and until the block could move it had no answer: a
# dynamic pool's blocks are partitioned per worker and a subscriber lands on
# one by hash, so a slice can be empty while another has blocks standing free
# - and the subscriber was refused for ever, not for a second.
#
# The box moves the block under the barrier bpa_expire_process takes once a
# second, so the packet that CAUSES the loan is still refused. What this
# section measures is the one after it, which on a real box is milliseconds
# later.
#
# Two addresses on ONE worker are what makes a slice empty while the other
# has room; they are found by asking the box rather than by arithmetic here,
# because which worker an address lands on is bpa_owner_worker()'s to say.
echo
echo "=========== a refused subscriber, one tick later ==========="
LEND_NW=$(box_workers)
if [ "${LEND_NW:-0}" -lt 2 ]; then
  echo "  one worker: there is no second slice for a block to come from,"
  echo "  and nothing here is about a box that cannot lend"
  LEND_STATE=skipped
else
  # Every address of `tight` that shares a worker, in the order the box
  # reports them, so the pair below is two subscribers of one slice.
  # ENOUGH OF THEM TO FILL THE SLICE AND ONE MORE. The thickest slice of
  # `tight` holds two blocks, so two subscribers fill it and the third is the
  # one with nowhere to go - which is the state this section is about. Asking
  # for two and hoping is what the first version did, and the box served the
  # second from the block that was still free: the control below caught it.
  LEND_T=$(thinnest "$(stanza lend "$(vppctl show bpa blocks)")")
  LEND_FILL=""; LEND_B=""; LEND_ON=""
  for _a in 100.67.0.1 100.67.0.2 100.67.0.3 100.67.0.4 100.67.0.5 \
            100.67.0.6 100.67.0.7 100.67.0.8 100.67.0.9 100.67.0.10; do
    _t=$(thread_of $_a)
    [ -n "$_t" ] || continue
    [ "$_t" = "$LEND_T" ] && continue   # the thin slice has nothing to spare
    LEND_ON="$LEND_ON $_a"
  done
  # All but the last fill the slice; the last is the subscriber with nowhere
  # to go. The count comes off the box's own row rather than from a 2 written
  # here, so a fixture with a different shape does not quietly stop testing
  # the thing this section is named after.
  LEND_SIZE=$(stanza lend "$(vppctl show bpa blocks)" \
              | sed -n "s/^ *thread [0-9]*: [0-9]*\/\([0-9]*\) blocks.*/\1/p" \
              | sort -rn | head -1)
  _n=0
  for _a in $LEND_ON; do
    _n=$((_n + 1))
    if [ "$_n" -le "${LEND_SIZE:-2}" ]; then LEND_FILL="$LEND_FILL $_a"
    elif [ -z "$LEND_B" ]; then LEND_B=$_a
    fi
  done
  # ON FOR THIS SECTION ONLY. The mechanism ships off - lending a block
  # serves a refused subscriber and can cost the lender's own next one, and
  # which side an operator wants is their decision - so the lab turns it on
  # where it measures it and off again after, and every other section of
  # this file measures the box as it ships.
  vppctl bpa rebalance enable | sed 's/^/    /'
  echo "  filling a ${LEND_SIZE:-?}-block slice with:$LEND_FILL"
  echo "  and then asking for:$LEND_B"

  send_one() {   # <address>; prints the trace line
    vppctl packet-generator new "name L limit 1 node ip4-input interface pg0 \
        size 60-60 \
        data { UDP: $1 -> 8.8.8.8 \
               UDP: 12345 -> 53 \
               incrementing 8 }" >/dev/null
    vppctl clear trace >/dev/null; vppctl trace add pg-input 20 >/dev/null
    vppctl packet-generator enable >/dev/null; sleep 1
    vppctl packet-generator delete L >/dev/null 2>&1
    vppctl show trace | grep "BPA in2out $1:" | head -1
  }

  LEND_FIRST=""
  for _a in $LEND_FILL; do
    LEND_FIRST=$(send_one "$_a")
    echo "    fill:   $(printf '%s' "$LEND_FIRST" | sed 's/^ *//')"
  done
  LEND_SECOND=$(send_one "$LEND_B")
  echo "    second: $(printf '%s' "$LEND_SECOND" | sed 's/^ *//')"
  # ONE TICK, not three, and the difference is the mechanism's own shape.
  # The tick after the refusal is the one that lends; the loan is then held
  # for the subscriber that caused it, and a loan still unused a tick later
  # goes home - which is what stops a stale demand costing the lender a live
  # allocation. Measured: waiting three ticks here found the block already
  # returned and read as "still refused", which is the box working exactly as
  # designed and a lab asking the question too late. A real subscriber
  # retries in milliseconds; a DNS client or a TCP SYN is back inside one
  # second.
  sleep 1
  # The slice state at the moment of the retry, because "still refused" has
  # two causes and they are opposite: a pool with nothing free anywhere is
  # honest exhaustion, and a pool with a block standing free next door is
  # #210. The first version of this section read the second as the first.
  echo "    --- blocks after the refusal, one tick on ---"
  stanza lend "$(vppctl show bpa blocks)" | sed 's/^/      /'
  LEND_RETRY=$(send_one "$LEND_B")
  echo "    retry:  $(printf '%s' "$LEND_RETRY" | sed 's/^ *//')"
  vppctl bpa rebalance disable | sed 's/^/    /'
  LEND_STATE=measured
fi

# THE TWO SCREENS AN OPERATOR READS WHEN A PORT IS ALL THEY HAVE.
#
# A complaint arrives as "this public address and this port did something",
# and `show bpa translation <public-address> <port>` is the only way back to
# who. Two of
# its answers are not a subscriber at all, and both were named by nothing:
# the port is outside anything this pool ever hands out, or it is inside a
# block that nobody is holding at this instant. An operator who cannot tell
# those two apart cannot tell "my records are incomplete" from "there is
# nothing to find".
#
# `din` is 200.150.0.16/32 with 16384-port blocks, so its three blocks are
# 1024-17407, 17408-33791 and 33792-50175. Port 100 is below all of them and
# 60000 is above.
echo
echo "=== a port, and nobody behind it: the two screens that are not a subscriber ==="
REV_LOW=$(vppctl show bpa translation 200.150.0.16 100)
printf '%s\n' "$REV_LOW" | sed 's/^/  /'
REV_HIGH=$(vppctl show bpa translation 200.150.0.16 60000)
printf '%s\n' "$REV_HIGH" | sed 's/^/  /'
REV_FREE=$(vppctl show bpa translation 200.150.0.16 20000)
printf '%s\n' "$REV_FREE" | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
DYN_NW=$(box_workers)
# Asked of the box and not of LAB_WORKERS, which is a request and not an answer
# (#229), and the number is used rather than assumed: a dynamic pool's blocks
# are partitioned by block_idx % n_workers, so what the pool can serve whatever
# the addresses are is the SMALLEST slice, and three blocks over DYN_NW workers
# makes that 3 / DYN_NW. At the two counts the suite runs - none, and one
# meaning the default of two - this is the same assertion it has always made.
if [ "$DYN_NW" -ge 1 ]; then
  expect_in "the pool reports what it can serve whatever the addresses are" \
    "whatever the addresses are" "$POOLCAP"
  expect_in "and that number is the smallest slice, not the total" \
    "$((3 / DYN_NW)) whatever" "$POOLCAP"
else
  # Single-threaded there is one slice and it is the whole pool, so the line
  # must NOT appear: a caveat printed where it does not apply is a caveat
  # people learn to skip.
  expect_not_in "with one thread there is no shard, and no caveat printed" \
    "whatever the addresses are" "$POOLCAP"
fi
# THE TWO ANSWERS THAT ARE NOT A SUBSCRIBER, held whole: each is a sentence
# an operator reads at the worst moment, with a port and a complaint and
# nothing else, and neither was named by any lab, suite, wiki page or
# document before this.
# ONE VARIABLE AND NOT TWO ADJACENT STRINGS. `"a" "b"` is two arguments in
# sh, not one literal - expect_in would take the first half as the needle and
# the second as the haystack, and pass on a box that says neither.
NO_BLOCK="but to no block: the port is outside the pool's usable range, so nothing was ever translated here"
expect_in "a port below everything the pool hands out names the pool and refuses the block" \
  "$NO_BLOCK" "$REV_LOW"
expect_in "and a port above them says the same thing rather than a different one" \
  "$NO_BLOCK" "$REV_HIGH"
expect_in "a port in a block nobody holds answers nobody, rather than naming a subscriber" \
  "->  nobody, right now" "$REV_FREE"
expect_in "and says which pool and block, and that the block is dynamic" \
  ", DYNAMIC: no subscriber holds this block at the moment" "$REV_FREE"
# AND THE CAVEAT UNDER IT, which is the load-bearing sentence on that screen.
# Without it "nobody, right now" reads as "nobody, ever", and an operator
# holding a complaint about last Tuesday closes it as unfounded. The screen
# sends them to the RFC 8158 log instead, which is the thing that can answer
# for a past instant.
expect_in "and sends an operator asking about a past instant to the log" \
  "this answers for NOW ONLY" "$REV_FREE"
expect_in "naming what the log records" \
  "when each block was assigned and released" "$REV_FREE"

expect_in "the subscriber's sessions are gone" "total: 0 sessions" "$SESS6"
expect_not_in "and it is not reported as holding no block while it holds one" \
  "holds no block" "$SUB6"
expect_in "the block it still holds is named" "200.150.0.16" "$SUB6"
# `din`'s rows only: the section above left two more dynamic pools in the box,
# and every needle below is a statement about this pool's counters.
B=$(stanza din "$(vppctl show bpa blocks)")
# "allocations" is a column label "show bpa blocks" prints on every dynamic
# pool, so this passed on a pool that had allocated nothing. The count, off
# the total line - the per-thread line above it carries the same words.
ALLOCS=$(printf '%s\n' "$B" \
         | sed -n 's/^ *total: .* in use, \([0-9][0-9]*\) allocations.*/\1/p')
echo "  allocations counted: ${ALLOCS:-<none>}"
expect "a block was allocated for each of the three subscribers" \
  test "${ALLOCS:-0}" -ge 3
# Examined under #204 and deliberately left as it is.
#
# The worry was #201's: with workers "show bpa blocks" prints a row per thread
# AND a total, and this needle matches a per-thread row as readily as the
# total line. It cannot pass wrongly all the same, and the reason is
# arithmetic rather than luck - the total is the SUM of the rows above it,
# summed from the same values in the same pass, so a row carrying a refusal
# count of 1 puts the total at 1 or more. What this asserts is "at least one
# refusal was counted", and a match anywhere in the block is exactly that. A
# box that refused nothing prints "0 refusals" on every row and on the total,
# and this needle is in none of it.
#
# No box could be constructed on which it passes wrongly, so it was NOT
# anchored on the total: that would have made it "exactly one", and the
# number of refusals this section produces is not one on every box. Measured:
# LAB_WORKERS=1 gives one refusal (2 workers, blocks split 2/1), and
# LAB_WORKER_COUNT=3 gives three, because a dynamic pool's blocks are
# partitioned per worker and four subscribers hashing onto three one-block
# workers collide. Both are the box doing what it is built to do; it is the
# section's premise - three blocks reachable by any subscriber - that a
# worker count of three takes away. Anchoring would have reported the first
# of those as broken, which is #201 in reverse.
expect_in "exhaustion was counted, not silent" "1 refusals" "$B"
# The hold-down is proved by the release counter, not by an instantaneous
# reading: the lab reallocates a block right after, so "0 in use" is true only
# for a moment. Asserting a transient snapshot is how this check first failed.
expect_in "hold-down released every block it took" "3 releases" "$B"

# The acceptance of #210: the claim is a promise, and these are the subscribers
# it was made about.
#
# Separated from the refusal check because they answer different questions. A
# packet that never reached the translator says the LAB failed to deliver it,
# and reading that as "the box refused" is how a broken lab reports a green
# box as red - or, once somebody "fixes" it, the other way round.
expect "every subscriber 'tight' claims capacity for reached the translator" \
  test -z "$TIGHT_MISSING"
expect "every subscriber 'tight' claims capacity for got a block" \
  test -z "$TIGHT_REFUSED"
expect "every subscriber 'roomy' claims capacity for reached the translator" \
  test -z "$ROOMY_MISSING"
expect "every subscriber 'roomy' claims capacity for got a block" \
  test -z "$ROOMY_REFUSED"
# And the claim is not honest merely by being small. `roomy` has more blocks
# than inside addresses on every worker, so no subscriber of it can ever be
# refused for want of a block in its own slice, and the number to size against
# is the whole inside set. A pool that understates is still a pool an operator
# sizes wrongly; it just sends them to buy addresses instead of losing them.
echo "  roomy claims $ROOMY_CLAIM of $ROOMY_USABLE"
# DIFFERENT DEFAULTS on the two sides, and that is the assertion rather than
# housekeeping. Written as test "$A" = "$B", a box that printed NOTHING at all
# compares "" with "" and satisfies this - which is what happened the first
# time it met a build whose capacity line was missing: it read
# "roomy claims  of " and went green while seven other checks went red.
# lab/ipfix.sh's own pair carries the same rule for the same reason.
expect "a pool with a block for every inside address claims all of them (claims ${ROOMY_CLAIM:-none}, usable ${ROOMY_USABLE:-none})" \
  test "${ROOMY_CLAIM:-claimed nothing}" = "${ROOMY_USABLE:-usable nothing}"

# #210's acceptance, in the only shape a barrier-driven mechanism can have:
# the packet that causes the loan is refused, and the one after it is not.
#
# THE FIRST TWO ASSERTIONS ARE THE CONTROL, and they are what stops this
# section passing on a box that lends nothing: if the second subscriber was
# served straight away, the slice was not empty and the retry proves nothing.
# A box with one worker skips all three and says so.
if [ "${LEND_STATE:-skipped}" = measured ]; then
  expect "the subscribers that fill the slice are served" \
    test "${LEND_FIRST#*-> 0.0.0.0:0}" = "$LEND_FIRST"
  expect "and the second is refused, which is the defect #210 is about" \
    test "${LEND_SECOND#*-> 0.0.0.0:0}" != "$LEND_SECOND"
  expect "and one tick later the same subscriber is served, from a block \
another worker lent" \
    test "${LEND_RETRY#*-> 0.0.0.0:0}" = "$LEND_RETRY"
else
  echo "  [skip] one worker: no slice to lend from"
fi
verdict
