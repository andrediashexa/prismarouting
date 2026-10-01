#!/bin/sh
# Dynamic mode with 2 workers: validates the partitioning of the block space.
#
# Block allocation would be state shared between workers. Instead of a lock,
# the space is partitioned: worker w only allocates blocks with
# block_idx % n_workers == w. Since in2out already sends the subscriber to
# sub_idx % n_workers, the two agree, and out2in (which resolves by
# block_idx % n_workers) lands on the same worker.
#
# That partition also BOUNDS what an operator may ask for, and the section at
# the end is the box saying so at configuration time: a stride whose step
# leaves the slice cannot be honoured for anybody, and on a pool small enough
# there is no honourable stride to name instead either. Both sentences carry
# the worker count, so neither can be produced by a lab that has no workers -
# which is why they are here rather than beside the allocator's own labs.
#
# AND THE LAST SECTION ASKS THE SAME BOX THE SAME QUESTION THROUGH THE VTY,
# which is the only way in an operator has. The two sections above are typed at
# `vppctl`, the dataplane's own console; a warning printed there and nowhere
# else has reached nobody the product ships for (#373). Same box, same two
# workers, same run: the only thing that differs is which way in was used.
set -u
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
mkdir -p /run/vpp /vpp_plugins /etc/prisma-routing /var/log/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
# The last section commits a pool through the VTY, which reaches the dataplane
# over the binary API and not over vppctl.
use_binary_api

vpp -c "$(startup_conf test workers)" > /tmp/vpp.log 2>&1 &
wait_vpp

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

# 4 public IPs x 3 blocks = 12 blocks, 6 per worker
vppctl bpa pool din inside 100.64.0.0/22 outside 200.150.0.16/30 block 16384 mode dynamic
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1

echo "=== block slices per worker (12 blocks, 2 workers) ==="
vppctl show bpa blocks | sed 's/^/  /'

echo
echo "=== 4 subscribers going out ==="
for sub in 5 6 9 12; do
  vppctl packet-generator new "name s$sub limit 1 node ip4-input interface pg0 size 60-60 \
      data { UDP: 100.64.0.$sub -> 8.8.8.8 \
             UDP: 40000 -> 53 \
             incrementing 8 }"
done
vppctl clear trace >/dev/null; vppctl trace add pg-input 20 >/dev/null
vppctl packet-generator enable >/dev/null; sleep 1
vppctl show trace | grep -E "BPA (handoff|in2out)" | sed 's/^/  /'
for sub in 5 6 9 12; do vppctl packet-generator delete s$sub >/dev/null 2>&1; done

echo
echo "=== distribution (even subscriber -> worker 0, odd -> worker 1) ==="
vppctl show bpa blocks | sed 's/^/  /'
vppctl show bpa session | sed 's/^/  /'

echo
echo "=== return path: every reply has to find its worker ==="
# 40000 does not fit in the 1024-17407 block, so the allocator handed out the
# first free port: 1024 on each public address.
echo "  esperado: .16->100.64.0.6 (t1)  .17->100.64.0.5 (t2)"
echo "            .18->100.64.0.12 (t1) .19->100.64.0.9 (t2)"
for t in "200.150.0.16 1024" "200.150.0.17 1024" "200.150.0.18 1024" "200.150.0.19 1024"; do
  set -- $t
  vppctl packet-generator new "name r$2$1 limit 1 node ip4-input interface pg1 size 60-60 \
      data { UDP: 8.8.8.8 -> $1 \
             UDP: 53 -> $2 \
             incrementing 8 }" 2>/dev/null
done
vppctl clear trace >/dev/null; vppctl trace add pg-input 20 >/dev/null
vppctl packet-generator enable >/dev/null; sleep 1
vppctl show trace | grep -E "BPA out2in" | sed 's/^/  /'
echo
# These are the plugin's error-counter strings. They used to be searched for in
# Portuguese, which the plugin never emitted: the grep never matched and the
# line printed "none" regardless of what had actually happened.
echo "  packets with no session (must be 0):"
vppctl show errors | grep -iE "no session for destination|no free block" | sed 's/^/    /' || echo "    none"

echo
echo "=========== a stride the sharding cannot honour ==========="
# The partition this whole lab is about is also what bounds which strides
# exist. A subscriber draws only from its own worker's slice, so the block a
# stride names is reachable only when the distance to it is a multiple of the
# worker count - and when it is not, NO subscriber can be honoured rather than
# some.
#
# Measured on `check`, which creates nothing: the point is what the box says
# before an operator commits, and the pool it would refuse to place blocks for
# does not have to exist to be told about.
#
# Under `policy blocked` one stride step is one block index, so a stride of one
# is exactly the unreachable case at two workers.
STRIDE_WARN=$(vppctl bpa pool stridden inside 10.20.0.0/24 \
    outside 45.170.16.0/30 ports 1024-65535 block 512 policy blocked \
    mode dynamic max-blocks 2 block-stride 1 check 2>&1)
printf '%s\n' "$STRIDE_WARN" | sed 's/^/  /'
echo
echo "  and the stride it named instead, on the same pool:"
STRIDE_OK=$(vppctl bpa pool stridden inside 10.20.0.0/24 \
    outside 45.170.16.0/30 ports 1024-65535 block 512 policy blocked \
    mode dynamic max-blocks 2 block-stride 2 check 2>&1)
printf '%s\n' "$STRIDE_OK" | sed 's/^/  /'

echo
echo "  and a pool where there is no stride to name at all:"
# THE OTHER BRANCH OF THAT ADVICE LINE, and it needs a different pool rather
# than a different stride. Above, the box could name a stride that works,
# because a public address there holds 126 mappable blocks and every second
# one of them is back in the subscriber's own slice at two workers. A pool
# whose public address holds only TWO mappable blocks has no such candidate:
# the only stride that fits inside the address is 1, and 1 is exactly the
# unreachable one. The box then has nothing to offer, and it has to say so
# rather than name a number the operator would be refused for typing -
# bpa_pool_init() refuses a stride at or past the mappable blocks.
#
# 2048 ports in blocks of 1024 is two blocks per address, and that is the
# shape of a box given a handful of public addresses and large blocks per
# subscriber, not a corner invented here.
#
# The worker count is read OFF THE BOX and not written down: the sentence
# names it, and a lab that hardcoded 2 would stop measuring the day this lab
# is run with LAB_WORKER_COUNT set to anything else.
WK=$(box_workers)
NOFIT_WARN=$(vppctl bpa pool nofit inside 10.20.2.0/29 \
    outside 45.170.18.0/30 ports 1024-3071 block 1024 policy blocked \
    mode dynamic max-blocks 2 block-stride 1 check 2>&1)
printf '%s\n' "$NOFIT_WARN" | sed 's/^/  /'
echo "  workers on this box: $WK"

echo
echo "=========== the same stride, applied through the VTY ==========="
# THE PATH AN OPERATOR ACTUALLY TAKES, and the reason this section exists
# beside the two above rather than instead of them.
#
# Everything above is typed at `vppctl`, which is the dataplane's own console
# and not the interface this product ships. An operator configures a pool in
# the VTY, the VTY applies it over the binary API, and the warning has to
# arrive THERE - at the prompt, in the commit that applied the stride - or it
# has not arrived at all. The box above and the box here are the same box with
# the same workers in the same run, so the only thing that differs between the
# sections is which way in was used.
#
# BOTH WAYS IN, because a stride reaches the dataplane by two different
# messages and neither covers the other. A pool created with a stride carries
# it on the creation message; a stride changed on a pool that is already
# carrying traffic is an in-place setting and goes on its own message, because
# the stride only decides where the NEXT extension goes. So this section
# creates one and changes another.
#
# NOT `block 512` OUT OF NOWHERE: the shape is the one the `check` above uses,
# a public address holding 126 mappable blocks under `policy blocked`, where a
# stride step is one block index and a stride of 1 is exactly the unreachable
# case at two workers. The VTY also refuses a block smaller than 64 ports, so
# the 4-port blocks other labs use cannot be typed at the prompt at all.
HX="python3 /src/src/prismash/prismash.py"
VTY_STRIDE=1
VTY_SAY=$($HX -c "configure" \
    -c "nat" -c "cgnat" -c "pool vtystride" \
    -c "prefix-inside 10.30.0.0/24" \
    -c "prefix-outside 45.170.20.0/30" \
    -c "block-size 512" \
    -c "ports 1024 65535" \
    -c "policy blocked" \
    -c "mode dynamic" \
    -c "max-blocks 2" \
    -c "block-stride $VTY_STRIDE" \
    -c "exit" \
    -c "commit" 2>&1)
printf '%s\n' "$VTY_SAY" | sed 's/^/  /'

echo
echo "  and a stride this box CAN honour, committed the same way:"
# THE CONTROL, and it goes through the VTY too. Without it the checks below
# pass on a box that warns about every stride there is, and a warning that
# fires whatever the operator did is one they learn to scroll past.
#
# It is also the control for the OTHER half. `vtystride` is still on this box
# with a stride it cannot honour, and the device says so every time it is
# asked - so a relay that simply forwarded what it read would print that
# paragraph here, on a commit that had nothing to do with it.
VTY_QUIET=$($HX -c "configure" \
    -c "nat" -c "cgnat" -c "pool vtyokstride" \
    -c "prefix-inside 10.30.1.0/24" \
    -c "prefix-outside 45.170.21.0/30" \
    -c "block-size 512" \
    -c "ports 1024 65535" \
    -c "policy blocked" \
    -c "mode dynamic" \
    -c "max-blocks 2" \
    -c "block-stride 2" \
    -c "exit" \
    -c "commit" 2>&1)
printf '%s\n' "$VTY_QUIET" | sed 's/^/  | /'

echo
echo "  and the same stride lowered on the pool that was already running:"
# THE IN-PLACE PATH, which is the one the operator reaches on the day they
# change their mind rather than the day they build the pool. Nothing here is
# recreated: the stride decides where the next extension goes, so the device
# takes it on a live pool and no subscriber moves.
VTY_CHANGE=$($HX -c "configure" \
    -c "nat" -c "cgnat" -c "pool vtyokstride" \
    -c "block-stride 1" \
    -c "exit" \
    -c "commit" 2>&1)
printf '%s\n' "$VTY_CHANGE" | sed 's/^/  /'

# Collapsed before anything is cut out of it: these are sentences, and a needle
# that depends on where one folded stops matching the next time the message is
# re-wrapped (#271).
VTY_FLAT=$(printf '%s' "$VTY_SAY" | tr '\n' ' ' | tr -s ' ')
VTY_WAYS=$(printf '%s' "$VTY_FLAT" \
  | sed -n 's/.*blocks are sharded \([0-9][0-9]*\) ways.*/\1/p')
VTY_STEP=$(printf '%s' "$VTY_FLAT" \
  | sed -n 's/.*a stride step moves by \([0-9][0-9]*\).*/\1/p')
VTY_NEAR=$(printf '%s' "$VTY_FLAT" \
  | sed -n 's/.*the nearest stride that can be: \([0-9][0-9]*\).*/\1/p')
# AND WHAT THE BOX WROTE DOWN, which is the other half of the same fix and the
# half nobody is watching at the time. A pool applied over the API is created
# with no terminal on the far end, so the dataplane keeps the record itself -
# and it is the only trace left for whoever reads the box hours later, after
# the layout they expected turned out not to be the layout they got.
VTY_LOG=$(vppctl show logging 2>/dev/null)
echo "  workers on this box: $WK"
echo "  the prompt said: sharded ${VTY_WAYS:--} ways, a step moves by"
echo "                   ${VTY_STEP:--}, nearest honourable stride ${VTY_NEAR:--}"

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
ERR=$(vppctl show errors)
expect_not_in "every reply found its owning worker" "no session for destination" "$ERR"
expect_not_in "no block was left unallocated on return" "block not allocated" "$ERR"
expect_in "blocks came from both worker slices" "thread 2" "$(vppctl show bpa blocks)"
# The stride warning, on a box that really has the workers that make it true.
# Said at the moment the operator can act on it, with the number of ways the
# blocks are actually sharded rather than a general caution.
expect_in "a stride outside the slice is called out" \
  "cannot be honoured" "$STRIDE_WARN"
expect_in "and says how the blocks are actually sharded" \
  "sharded 2 ways" "$STRIDE_WARN"
expect_in "and names one that works, so the operator has something to type" \
  "the nearest stride that can be: 2" "$STRIDE_WARN"
# A warning and not a refusal, deliberately: the worker count is a startup
# setting, so refusing would leave a pool that came up yesterday unable to come
# up after a restart - every subscriber on it down, for a preference about
# layout.
expect_in "it is a warning, and says so" "not a refusal" "$STRIDE_WARN"
expect_in "and the pool is still valid" "is valid" "$STRIDE_WARN"
# The control. Without it the assertions above pass on a box that warns about
# every stride, which is a caution nobody reads rather than a measurement.
expect_not_in "the stride it recommended draws no warning at all" \
  "cannot be honoured" "$STRIDE_OK"
# And the pool where the advice line has nothing to advise. Guarded first on
# the box having workers at all: with none, bpa_stride_warn() reads
# `n_workers ? n_workers : 1`, every stride is reachable at one worker and the
# whole screen is absent - so "it did not say so" would be true for a reason
# that is not about the product. This lab starts its own workers, which is
# what makes the guard an assertion rather than a skip.
expect "the box has the workers every sentence above is about" \
  test "${WK:-0}" -ge 2
expect_in "a pool with no honourable stride at all is told exactly that" \
  "no stride fits this pool at $WK workers" "$NOFIT_WARN"
# The half that keeps the line above from being decoration: this pool must NOT
# get the advice the first one got. The two branches are exclusive in
# bpa_stride_warn(), so a build that always named a nearest stride would fail
# here and nowhere else.
expect_not_in "and it is not given a nearest stride it does not have" \
  "the nearest stride that can be" "$NOFIT_WARN"
expect_in "and that pool is valid too, so this is a warning and not a refusal" \
  "is valid" "$NOFIT_WARN"

# AND THE SAME WARNING AT THE VTY PROMPT, which is where an operator is.
#
# The checks above assert what the dataplane's own console printed. These
# assert what the PRODUCT printed, on the path prismash uses - and the two are
# different claims about different code, which is the whole reason both are
# here. A box that tells the person at `vppctl` and says nothing to the person
# at the prompt has told nobody: the VTY exists so that nobody has to drop to
# the dataplane console, and every operator is on this path.
#
# The two ways these could be green having measured nothing: a commit that
# never created the pool - which is every way a commit can fail, and it fails
# silently as far as a grep for an absent warning is concerned - and a box with
# no workers to shard the blocks over. Both are guarded, and the second is an
# assertion rather than a skip because this lab starts its own workers.
expect_in "the pool the warning is about was created through the VTY" \
  "pool vtystride" "$(vppctl show bpa pool)"
expect "and the box has the workers every sentence here is about" \
  test "${WK:-0}" -ge 2
expect_said "the prompt says a stride this box cannot honour" \
  "block-stride $VTY_STRIDE on pool vtystride cannot be honoured" "$VTY_SAY"
expect_said "and says what the subscriber loses by it" \
  "extensions fall back to the free list" "$VTY_SAY"
# The three numbers, because they are what an operator acts on and a relay
# that carried the sentence with somebody else's arithmetic in it would be
# worse than none.
expect "the sharding the prompt names is the sharding this box has" \
  test "${VTY_WAYS:-x}" -eq "$WK"
expect "and a stride step moves by one block index under policy blocked" \
  test "${VTY_STEP:-x}" -eq 1
# Two readings of one fact, taken from different places: the stride the plugin
# arrived at by walking candidates, and the worker count read off the box. With
# a step of one block index the first honourable stride IS the worker count,
# and a build that named any other number has nothing to do with the sharding
# it just quoted.
expect "and the nearest honourable stride it names is this box's worker count" \
  test "${VTY_NEAR:-x}" -eq "$WK"
expect_said "and the commit went through, because this is a warning and not a refusal" \
  "commit complete" "$VTY_SAY"
expect_said "and the box wrote the record itself, for whoever reads it later" \
  "pool vtystride: block-stride $VTY_STRIDE unreachable at $WK workers" \
  "$VTY_LOG"
# THE CONTROL, both halves of it. A stride the box can honour must draw
# nothing at all, and a commit must say nothing about a pool it did not touch -
# `vtystride` is still on this box with a stride it cannot honour, and the
# device repeats that whenever it is asked.
expect_in "the control pool was created too" "pool vtyokstride" \
  "$(vppctl show bpa pool)"
expect_not_said "a stride this box can honour draws no warning at the prompt" \
  "cannot be honoured" "$VTY_QUIET"
expect_not_said "and a commit says nothing about a pool it did not touch" \
  "on pool vtystride" "$VTY_QUIET"
# AND THE STRIDE CHANGED ON A LIVE POOL, which is a different message to the
# dataplane and was the only path that reached the log at all before this.
expect_said "lowering a stride on a running pool warns at the prompt too" \
  "block-stride 1 on pool vtyokstride cannot be honoured" "$VTY_CHANGE"
expect_said "and names the nearest stride that can be, so there is something to type" \
  "the nearest stride that can be" "$VTY_CHANGE"
expect_not_said "and that commit says nothing about the other pool either" \
  "on pool vtystride" "$VTY_CHANGE"
verdict
