#!/bin/sh
# Dynamic mode with 2 workers: validates the partitioning of the block space.
#
# Block allocation would be state shared between workers. Instead of a lock,
# the space is partitioned: worker w only allocates blocks with
# block_idx % n_workers == w. Since in2out already sends the subscriber to
# sub_idx % n_workers, the two agree, and out2in (which resolves by
# block_idx % n_workers) lands on the same worker.
set -u
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

vpp -c "$(startup_conf test workers)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done

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
echo "=== 4 assinantes saindo ==="
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
verdict
