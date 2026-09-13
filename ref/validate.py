#!/usr/bin/env python3
"""Exhaustive validation of the BPA model, including non-contiguous pools."""

import ipaddress, sys, random
from bpa_model import (PoolConfig, AddrSet, MapPolicy, PoolMode, plan,
                       bitmap_walk, BpaError)

FAIL = 0

def check(cond, msg):
    global FAIL
    if not cond:
        FAIL += 1
        print(f"  FAIL: {msg}")

def roundtrip_full(pool: PoolConfig):
    """Every subscriber -> block -> back to the same subscriber."""
    n = min(pool.capacity, pool.inside.count)
    seen = {}
    errors = 0
    for sub in range(n):
        ip_priv = str(ipaddress.IPv4Address(pool.inside.addr_of(sub)))
        f = pool.forward(ip_priv)

        key = (f["ip_pub"], f["blk_idx"])
        if key in seen:
            errors += 1
            if errors < 4:
                print(f"  COLLISION: {ip_priv} and {seen[key]} -> {key}")
        seen[key] = ip_priv

        for port in (f["port_lo"], f["port_lo"] + pool.block_size // 2, f["port_hi"]):
            if pool.reverse(f["ip_pub"], port) != ip_priv:
                errors += 1
                if errors < 4:
                    print(f"  WRONG REVERSE: {ip_priv} -> {f['ip_pub']}:{port}")

    check(errors == 0, f"{errors} round-trip errors")
    check(len(seen) == n, f"expected {n} distinct blocks, got {len(seen)}")
    if errors == 0:
        print(f"  OK  {n:,} subscribers, {len(seen):,} distinct blocks, 0 collisions")

def roundtrip_all_ports(pool: PoolConfig, sample: int = 50):
    n = min(pool.capacity, pool.inside.count)
    rnd = random.Random(42)
    bad = 0
    for _ in range(min(sample, n)):
        sub = rnd.randrange(n)
        ip_priv = str(ipaddress.IPv4Address(pool.inside.addr_of(sub)))
        f = pool.forward(ip_priv)
        for port in range(f["port_lo"], f["port_hi"] + 1):
            if pool.reverse(f["ip_pub"], port) != ip_priv:
                bad += 1
    check(bad == 0, f"{bad} ports mapped to the wrong subscriber")
    if bad == 0:
        print(f"  OK  {min(sample,n) * pool.block_size:,} ports checked one by one")

def check_addrset_bijection(pool: PoolConfig):
    """address <-> index must be a bijection in both sets."""
    bad = 0
    for s, label in ((pool.inside, "inside"), (pool.outside, "public")):
        for idx in range(s.count):
            a = s.addr_of(idx)
            if a is None or s.index_of(a) != idx:
                bad += 1
        # addresses immediately outside each range must not be accepted
        for base, cnt, _ in s.ranges:
            for probe in (base - 1, base + cnt):
                got = s.index_of(probe)
                if got is not None:
                    inside_another = any(b <= probe < b + c for b, c, _ in s.ranges)
                    if not inside_another:
                        bad += 1
    check(bad == 0, f"{bad} address<->index bijection failures")
    if bad == 0:
        print(f"  OK  address<->index bijection intact in both sets")

def check_guards(pool: PoolConfig):
    lo_pub = pool.outside.addr_of(0)
    cases = [
        ("port below the range", lambda: pool.reverse(lo_pub, pool.port_min - 1)),
        ("port 0", lambda: pool.reverse(lo_pub, 0)),
        ("inside ip out of set", lambda: pool.forward(pool.inside.ranges[0][0] - 1)),
    ]
    for name, fn in cases:
        try:
            fn()
            check(False, f"{name}: should have been rejected")
        except BpaError:
            pass
    print(f"  OK  {len(cases)} invalid inputs rejected")

# ---------------------------------------------------------------- pools

pools = [
    # contiguous, as before: makes sure nothing regressed
    PoolConfig("contiguous", ["100.64.0.0/22"], ["200.150.0.10/31"],
               block_size=512, policy=MapPolicy.INTERLEAVED),
    # three separate ranges, n_pub = 4+2+1 = 7, NOT a power of two
    PoolConfig("fragmented-7", ["100.64.0.0/22"],
               ["200.150.0.8/30", "45.170.16.64/31", "203.0.113.200/32"],
               block_size=512, policy=MapPolicy.INTERLEAVED),
    # the same thing under the blocked policy
    PoolConfig("fragmented-blocked", ["100.64.0.0/22"],
               ["200.150.0.8/30", "45.170.16.64/31", "203.0.113.200/32"],
               block_size=512, policy=MapPolicy.BLOCKED),
    # the inside set fragmented too, mixing CGNAT and RFC1918 space
    PoolConfig("inside-fragmented", ["100.64.0.0/24", "10.20.0.0/24"],
               ["200.150.0.0/29", "45.170.16.0/30"],
               block_size=1024, policy=MapPolicy.INTERLEAVED),
]

for p in pools:
    print("=" * 64)
    print(p.summary())
    print()
    check_addrset_bijection(p)
    roundtrip_full(p)
    roundtrip_all_ports(p)
    check_guards(p)

print("\n" + "=" * 64)
print("INVALID CONFIGURATIONS MUST BE REFUSED")
bad_cases = [
    ("overlapping prefixes", lambda: AddrSet(["200.150.0.0/24", "200.150.0.128/25"])),
    ("identical prefix repeated", lambda: AddrSet(["200.150.0.0/24", "200.150.0.0/24"])),
    ("empty set", lambda: AddrSet([])),
    ("block not a power of two", lambda: PoolConfig("x", ["10.0.0.0/24"], ["1.2.3.0/24"], block_size=500)),
]
for name, fn in bad_cases:
    try:
        fn()
        check(False, f"{name}: should have been refused")
    except BpaError:
        print(f"  OK  refused: {name}")

print("\n" + "=" * 64)
print("MERGING OF ADJACENT RANGES (fewer ranges = faster datapath)")
merged = AddrSet(["200.150.0.0/25", "200.150.0.128/25", "45.170.16.0/24"])
check(len(merged.ranges) == 2, f"expected 2 ranges after merging, got {len(merged.ranges)}")
check(merged.count == 512, f"expected 512 addresses, got {merged.count}")
print(f"  3 prefixes -> {len(merged.ranges)} ranges, {merged.count} addresses: {merged.describe()}")

print("\n" + "=" * 64)
print("BLOCK STRIDE: WHERE A SUBSCRIBER'S SECOND BLOCK GOES")

# The default is today's behaviour, and "today's behaviour" is not a formula:
# an extension is whatever comes off the free list, which depends on what the
# pool has already handed out. The model says so by refusing to predict it,
# and this is the assertion that an upgrade changes nothing - there is no
# stride arithmetic to apply until somebody configures one.
for p in pools:
    none_for_all = all(p.extension_block(b, n) is None
                       for b in range(min(p.capacity, 64)) for n in (1, 2, 3))
    check(none_for_all,
          f"'{p.name}': stride 0 predicted a block; the default must stay "
          f"whatever the allocator hands out next")
print(f"  OK  {len(pools)} pools: with no stride the model predicts nothing, "
      f"which is what the allocator does today")

# THE #23 REQUIREMENT, as arithmetic rather than as a promise: a stride must
# not move an existing subscriber. It cannot, because forward() does not read
# it - and that is only worth stating if it is checked, because the day
# somebody threads the stride into the mapping this is what goes red.
moved = 0
strides_tried = 0
for p in pools:
    base = {sub: p.forward(p.inside.addr_of(sub))
            for sub in range(min(p.capacity, p.inside.count))}
    for stride in range(0, min(p.blocks_per_ip, 8)):
        q = PoolConfig(p.name, p.inside_prefixes, p.outside_prefixes,
                       block_size=p.block_size, policy=p.policy,
                       block_stride=stride)
        strides_tried += 1
        for sub, f in base.items():
            if q.forward(q.inside.addr_of(sub)) != f:
                moved += 1
check(moved == 0,
      f"{moved} subscribers changed public address or port block when only "
      f"the stride changed; that belongs to the capacity-change procedure")
print(f"  OK  {strides_tried} pool/stride combinations move 0 subscribers: "
      f"a stride is not part of the mapping")

# Where the stride says the blocks go, in ports rather than in indices,
# because that is the sentence an operator reads and the log records.
for p in pools:
    bad = 0
    for stride in (1, 2, 5):
        if stride >= p.blocks_per_ip:
            continue
        q = PoolConfig(p.name, p.inside_prefixes, p.outside_prefixes,
                       block_size=p.block_size, policy=p.policy,
                       block_stride=stride)
        for sub in range(min(q.capacity, 200)):
            first = q.forward(q.inside.addr_of(sub))
            for nth in (1, 2, 3):
                blk = q.extension_block(sub, nth)
                if blk is None:
                    # only legal at the end of the public address
                    if first["blk_idx"] + nth * stride < q.blocks_per_ip:
                        bad += 1
                    continue
                pub_idx, blk_idx = q.split(blk)
                want = first["port_lo"] + nth * stride * q.block_size
                if pub_idx != first["pub_idx"]:
                    bad += 1
                if q.port_min + blk_idx * q.block_size != want:
                    bad += 1
    check(bad == 0, f"'{p.name}': {bad} striden blocks landed off their stride")
print(f"  OK  the nth block starts nth*stride*block_size ports above the "
      f"first, on the same public address")

# A stride that cannot place anything is refused rather than accepted and
# silently inert. blocks_per_ip is the first stride whose first extension is
# already past the end of the subscriber's own public address.
for p in pools:
    try:
        PoolConfig(p.name, p.inside_prefixes, p.outside_prefixes,
                   block_size=p.block_size, policy=p.policy,
                   block_stride=p.blocks_per_ip)
        check(False, f"'{p.name}': a stride of blocks_per_ip was accepted")
    except BpaError:
        pass
print(f"  OK  a stride that steps off the public address on its first "
      f"extension is refused")

print("\n" + "=" * 64)
print("BLOCK STRIDE UNDER WORKER SHARDING")
print("  A dynamic pool partitions blocks by block_idx % n_workers and a")
print("  subscriber draws only from its own worker's slice, so a stride whose")
print("  step is not a multiple of the worker count names a block this worker")
print("  may not hand out - for EVERY subscriber, not for some.")
for p in pools[:2]:
    print(f"  {p.name}: policy {p.policy.value}, {p.outside.count} public "
          f"addresses, one stride step = {p.stride_unit} block indices")
    for nw in (1, 2, 3, 4, 8):
        ok = [s for s in range(1, 9)
              if PoolConfig(p.name, p.inside_prefixes, p.outside_prefixes,
                            block_size=p.block_size, policy=p.policy,
                            block_stride=s).stride_reachable(1, nw)]
        print(f"    {nw} worker(s): strides 1-8 that can be honoured: "
              f"{ok if ok else 'none'}")
# The one that must hold whatever the policy: a single-threaded box has one
# slice, so every stride is reachable. If this ever goes red the sharding
# arithmetic has stopped agreeing with "there is only one worker".
for p in pools:
    for s in range(1, min(p.blocks_per_ip, 9)):
        q = PoolConfig(p.name, p.inside_prefixes, p.outside_prefixes,
                       block_size=p.block_size, policy=p.policy,
                       block_stride=s)
        check(q.stride_reachable(1, 1),
              f"'{p.name}': stride {s} unreachable on a single-threaded box")
print("  OK  every stride is reachable with one worker")

print("\n" + "=" * 64)
print("PER-POOL BLOCK-SIZE RANGE: WHAT block_size AND max_blocks ALREADY GIVE")
print("  A subscriber starts at block_size ports and grows one block at a")
print("  time, only when it fills one. The range is already two numbers; what")
print("  the small steps cost is the question, so here it is measured.")
print()
print("   layout                    ports  ranges  grants    probes   probe ns")
for bs, mb, grow, merge in ((4096, 1, 1, False), (1024, 4, 1, False),
                            (256, 16, 1, False), (64, 64, 1, False),
                            (64, 64, 2, False), (64, 64, 2, True)):
    w = bitmap_walk(4096, bs, mb, grow, merge)
    label = f"block {bs} x{mb}" + (f" grow {grow}" if grow > 1 else "")
    label += " merged" if merge else ""
    print(f"   {label:<24} {w['ports']:6d}  {w['ranges']:6d}  {w['grants']:6d}"
          f"  {w['probes']:8,}  {w['probe_ns']:9,.0f}")
# The reading the growth step has to beat. A grant that is not merged into one
# range changes the number of exhaustion events and NOTHING a session pays:
# the walk is over the blocks held, not over how they were handed out.
check(bitmap_walk(4096, 64, 64, 1, False)["probes"]
      == bitmap_walk(4096, 64, 64, 2, False)["probes"],
      "a growth step changed the session walk without merging the grant; "
      "the measurement behind not building one no longer holds")
print()
print(f"  OK  a growth step alone saves 0 of the "
      f"{bitmap_walk(4096, 64, 64)['probes']:,} probes: it hands out the same")
print(f"      blocks in fewer events, and a session walks blocks, not events")

print("\n" + "=" * 64)
print("DETERMINISTIC RESERVE: BLOCKS HELD OUT OF THE FORMULA")

# The default first, and it is the whole safety argument for this feature: a
# box that upgrades and is not reconfigured must map every subscriber onto the
# byte-identical address and port block it had yesterday. Asserted against the
# pool built WITHOUT the parameter, not against a remembered number, so the
# comparison stays true if the pools above are ever changed.
for p in pools:
    q = PoolConfig(p.name, p.inside_prefixes, p.outside_prefixes,
                   block_size=p.block_size, policy=p.policy, block_reserve=0)
    check((q.blocks_per_ip, q.blocks_mapped, q.capacity, q.reserve_count)
          == (p.blocks_per_ip, p.blocks_per_ip, p.capacity, 0),
          f"'{p.name}': block_reserve=0 changed a derived value")
    n = min(p.capacity, p.inside.count)
    same = all(q.forward(q.inside.addr_of(i)) == p.forward(p.inside.addr_of(i))
               for i in range(n))
    check(same, f"'{p.name}': block_reserve=0 moved a subscriber")
print(f"  OK  {len(pools)} pools: a reserve of zero leaves every derived "
      f"value and every mapping exactly as they were")

# The arithmetic the box has to report the same number for. Nothing is lost to
# rounding: the blocks a public address holds are either mapped or reserved.
for p in pools:
    bad = 0
    for r in range(0, min(p.blocks_per_ip, 8)):
        q = PoolConfig(p.name, p.inside_prefixes, p.outside_prefixes,
                       block_size=p.block_size, policy=p.policy,
                       block_reserve=r)
        if q.blocks_mapped != p.blocks_per_ip - r:
            bad += 1
        if q.capacity != q.outside.count * (p.blocks_per_ip - r):
            bad += 1
        if q.reserve_count != q.outside.count * r:
            bad += 1
        if q.capacity + q.reserve_count != q.outside.count * p.blocks_per_ip:
            bad += 1
    check(bad == 0, f"'{p.name}': {bad} capacity identities failed")
print(f"  OK  capacity = public addresses x (blocks per address - reserve), "
      f"and mapped + reserved = every block there is")

# A reserve block must belong to NOBODY the formula names. This is the
# property the whole feature stands on: an extension handed out of the reserve
# is not a block taken from another subscriber.
for p in pools:
    bad = 0
    for r in (1, 2, 5):
        if r >= p.blocks_per_ip:
            continue
        q = PoolConfig(p.name, p.inside_prefixes, p.outside_prefixes,
                       block_size=p.block_size, policy=p.policy,
                       block_reserve=r)
        mapped = set()
        for sub in range(q.capacity):
            f = q.forward(q.inside.addr_of(sub)) if sub < q.inside.count else None
            if f is None:
                pub_idx, blk_idx = q.split(sub)
            else:
                pub_idx, blk_idx = f["pub_idx"], f["blk_idx"]
            mapped.add((pub_idx, blk_idx))
        held = set()
        for res in range(q.reserve_count):
            held.add(q.reserve_split(res))
            if q.reserve_index(*q.reserve_split(res)) != res:
                bad += 1
        if mapped & held:
            bad += len(mapped & held)
        # and together they account for every block of every address
        if len(mapped) + len(held) != q.outside.count * q.blocks_per_ip:
            bad += 1
    check(bad == 0, f"'{p.name}': {bad} reserve/subscriber block conflicts")
print(f"  OK  no reserve block is any subscriber's, the reserve index is a "
      f"bijection, and the two sets together are the whole pool")

# What the return path sees. A port in the reserve is not "nobody's port": it
# is a port the algorithm cannot attribute, which the allocator can and the
# drop path must not.
for p in pools:
    bad = 0
    for r in (1, 3):
        if r >= p.blocks_per_ip:
            continue
        q = PoolConfig(p.name, p.inside_prefixes, p.outside_prefixes,
                       block_size=p.block_size, policy=p.policy,
                       block_reserve=r)
        for res in range(min(q.reserve_count, 64)):
            f = q.reserve_forward(res)
            for port in (f["port_lo"], f["port_hi"]):
                if q.reserve_index_of(f["ip_pub"], port) != res:
                    bad += 1
                try:
                    q.reverse(f["ip_pub"], port)
                    bad += 1  # a reserve port must not name a subscriber
                except BpaError:
                    pass
        for sub in range(min(q.capacity, 64)):
            f = q.forward(q.inside.addr_of(sub))
            for port in (f["port_lo"], f["port_hi"]):
                if q.reserve_index_of(f["ip_pub"], port) is not None:
                    bad += 1
    check(bad == 0, f"'{p.name}': {bad} reserve ports resolved wrongly")
print(f"  OK  every reserve port names its reserve block and no subscriber; "
      f"no subscriber's port is mistaken for one")

# THE #23 MEASUREMENT. A reserve reduces capacity, and reducing capacity is a
# mapping change - but HOW MUCH of one depends on the policy, and the two
# answers are not close. Measured here rather than asserted in prose, because
# this is the number docs/pool-resize.md quotes and the operator reads at
# commit.
print()
print("   what a reserve of 1 block per public address costs, by policy")
print("   pool                      policy       capacity        moved  dropped")
for p in pools:
    q = PoolConfig(p.name, p.inside_prefixes, p.outside_prefixes,
                   block_size=p.block_size, policy=p.policy, block_reserve=1)
    moved = dropped = 0
    for sub in range(min(p.capacity, p.inside.count)):
        a = p.forward(p.inside.addr_of(sub))
        if sub >= q.capacity:
            dropped += 1
            continue
        if q.forward(q.inside.addr_of(sub)) != a:
            moved += 1
    print(f"   {p.name:<24}  {p.policy.value:<11}  {p.capacity:6d}->"
          f"{q.capacity:<6d}  {moved:6d}  {dropped:6d}")
    if p.policy is MapPolicy.INTERLEAVED:
        # Under interleaved the divisor is the public address count, which a
        # reserve does not touch: every subscriber the smaller pool still
        # holds keeps the block it had. The change is a truncation, not a
        # re-mapping - and that is the difference between an outage for
        # everybody and an outage for the tail.
        check(moved == 0,
              f"'{p.name}': interleaved, yet {moved} retained subscribers "
              f"moved when only the reserve changed")
    else:
        # Under blocked the divisor IS blocks_mapped, so a reserve re-maps
        # everybody past the first public address. Asserted so that a future
        # change which quietly made this free would have to say so.
        check(moved > 0,
              f"'{p.name}': blocked, yet a reserve moved nobody; the blocked "
              f"split divides by the mapped blocks and must")
print("  OK  interleaved truncates the tail and moves nobody; blocked "
      "re-divides and moves the rest")

# Worker sharding. The return path can read only the reserve index off a
# packet, so a subscriber may be given a reserve block only from its own
# worker's slice - which means a worker with no reserve block has subscribers
# with no reserve at all.
for p in pools:
    q = PoolConfig(p.name, p.inside_prefixes, p.outside_prefixes,
                   block_size=p.block_size, policy=p.policy, block_reserve=2)
    for nw in (1, 2, 3, 4, 8, 16):
        have = {q.reserve_worker(r, nw) for r in range(q.reserve_count)}
        check((len(have) == nw) == q.reserve_serves_every_worker(nw),
              f"'{p.name}': {nw} workers, reserve of {q.reserve_count} blocks "
              f"reaches {len(have)} of them, yet the model says "
              f"{q.reserve_serves_every_worker(nw)}")
print(f"  OK  a worker holds a reserve block exactly when the reserve is at "
      f"least as large as the worker count")

# Refusals. A reserve that leaves nothing to map, and a reserve on a pool
# whose extensions already come off a free list.
for name, fn in [
    ("a reserve of every block",
     lambda: PoolConfig("x", ["10.0.0.0/24"], ["1.2.3.0/24"], block_size=512,
                        block_reserve=126)),
    ("a reserve larger than the address holds",
     lambda: PoolConfig("x", ["10.0.0.0/24"], ["1.2.3.0/24"], block_size=512,
                        block_reserve=1000)),
    ("a negative reserve",
     lambda: PoolConfig("x", ["10.0.0.0/24"], ["1.2.3.0/24"], block_size=512,
                        block_reserve=-1)),
    ("a reserve on a dynamic pool",
     lambda: PoolConfig("x", ["10.0.0.0/24"], ["1.2.3.0/24"], block_size=512,
                        block_reserve=1, mode=PoolMode.DYNAMIC)),
]:
    try:
        fn()
        check(False, f"{name}: should have been refused")
    except BpaError:
        print(f"  OK  refused: {name}")

print("\n" + "=" * 64)
print(f"RESULT: {'ALL TESTS PASSED' if FAIL == 0 else str(FAIL) + ' FAILURES'}")
sys.exit(1 if FAIL else 0)
