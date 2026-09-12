#!/bin/sh
# Dynamic mode (PBA): block allocated on demand, returned after hold-down.
#
# Pool proposital de borda: 1024 assinantes possiveis contra 3 blocos.
#   64512 ports / 16384-port block = 3 blocks on a single public IP
# Blocos previstos, em ordem de alocacao: portas 1024, 17408, 33792.
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
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
expect_in "the subscriber's sessions are gone" "total: 0 sessions" "$SESS6"
expect_not_in "and it is not reported as holding no block while it holds one" \
  "holds no block" "$SUB6"
expect_in "the block it still holds is named" "200.150.0.16" "$SUB6"
B=$(vppctl show bpa blocks)
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
verdict
