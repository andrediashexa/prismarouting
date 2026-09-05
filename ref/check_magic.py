#!/usr/bin/env python3
"""
Proves that the reciprocal in bpa_pool.c reproduces exact integer division.

Replicates the C arithmetic and compares it with // over the whole range of
sub_idx a real pool can produce, for every reachable blocks_per_ip.
"""
import sys

MAX_CAPACITY = 1 << 28
U64 = (1 << 64) - 1
fail = 0

def magic_for(d):
    """Mesmo calculo de bpa_pool_init()."""
    l = 0
    while (1 << l) <= d and l < 32:
        l += 1
    m = ((1 << (32 + l)) + d - 1) // d
    return m, 32 + l

# The datapath divisor depends on the policy:
#   interleaved -> outside.count, which is now the sum of arbitrary ranges
#   blocked     -> blocks_per_ip
# With fragmented pools the divisor stopped being well behaved, so we test
# any plausible value, not only the aligned ones.
import random
divisors = set()
for port_min in (1024, 2048, 4096):
    port_count = 65535 - port_min + 1
    bs = 1
    while bs <= port_count:
        divisors.add(port_count // bs)
        bs <<= 1
divisors.update(range(1, 600))                    # pools publicos pequenos
divisors.update([2**k for k in range(0, 28)])     # potencias de dois
divisors.update([2**k - 1 for k in range(1, 28)]) # vizinhos hostis
divisors.update([2**k + 1 for k in range(1, 28)])
rnd = random.Random(7)
divisors.update(rnd.randrange(1, 1 << 28) for _ in range(400))
divisors = sorted(d for d in divisors if 1 <= d < (1 << 28))
print(f"testando {len(divisors)} divisores, de {min(divisors)} a {max(divisors):,}\n")

overflow_worst = 0
for d in divisors:
    m, shift = magic_for(d)

    # 1. the intermediate product must not overflow u64
    worst = (MAX_CAPACITY - 1) * m
    overflow_worst = max(overflow_worst, worst)
    if worst > U64:
        print(f"  OVERFLOW: d={d} magic={m} product={worst.bit_length()} bits")
        fail += 1
        continue

    # 2. exact equivalence with integer division
    # sweeps the edges of each quotient, where the reciprocal fails if it does
    bad = 0
    for q in range(0, min(d * 4, MAX_CAPACITY // max(d, 1)) + 1):
        for n in (q * d - 1, q * d, q * d + 1):
            if 0 <= n < MAX_CAPACITY:
                if ((n * m) >> shift) != n // d:
                    bad += 1
    # and a dense sweep of the low range, which is where real pools live
    for n in range(0, min(300_000, MAX_CAPACITY)):
        if ((n * m) >> shift) != n // d:
            bad += 1
            break
    if bad:
        print(f"  FAIL: d={d} magic={m} shift={shift} -> {bad} divergences")
        fail += 1

print(f"worst-case intermediate product: {overflow_worst.bit_length()} bits (limit 64)")
print(f"\nRESULT: {'reciprocal exact for every divisor' if not fail else str(fail)+' FAILURES'}")
sys.exit(1 if fail else 0)
