#!/usr/bin/env python3
"""Exhaustive validation of the BPA model, including non-contiguous pools."""

import ipaddress, sys, random
from bpa_model import PoolConfig, AddrSet, MapPolicy, PoolMode, plan, BpaError

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
print(f"RESULT: {'ALL TESTS PASSED' if FAIL == 0 else str(FAIL) + ' FAILURES'}")
sys.exit(1 if FAIL else 0)
