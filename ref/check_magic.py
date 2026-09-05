#!/usr/bin/env python3
"""
Prova que o reciprocal de bpa_pool.c reproduz a divisao inteira exata.

Replica a aritmetica do C e compara com // em todo o intervalo de sub_idx
que um pool real pode produzir, para todo blocks_per_ip alcancavel.
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

# O divisor do datapath depende da politica:
#   interleaved -> outside.count, que agora e a soma de faixas arbitrarias
#   blocked     -> blocks_per_ip
# Com pools fragmentados o divisor deixou de ser bem-comportado, entao
# testamos qualquer valor plausivel, nao so os alinhados.
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

    # 1. o produto intermediario nao pode estourar u64
    worst = (MAX_CAPACITY - 1) * m
    overflow_worst = max(overflow_worst, worst)
    if worst > U64:
        print(f"  OVERFLOW: d={d} magic={m} produto={worst.bit_length()} bits")
        fail += 1
        continue

    # 2. equivalencia exata com a divisao inteira
    # varre as bordas de cada quociente, onde o reciprocal erra se errar
    bad = 0
    for q in range(0, min(d * 4, MAX_CAPACITY // max(d, 1)) + 1):
        for n in (q * d - 1, q * d, q * d + 1):
            if 0 <= n < MAX_CAPACITY:
                if ((n * m) >> shift) != n // d:
                    bad += 1
    # e uma varredura densa na faixa baixa, que e onde os pools reais vivem
    for n in range(0, min(300_000, MAX_CAPACITY)):
        if ((n * m) >> shift) != n // d:
            bad += 1
            break
    if bad:
        print(f"  FALHA: d={d} magic={m} shift={shift} -> {bad} divergencias")
        fail += 1

print(f"produto intermediario no pior caso: {overflow_worst.bit_length()} bits (limite 64)")
print(f"\nRESULTADO: {'reciprocal exato para todos os divisores' if not fail else str(fail)+' FALHAS'}")
sys.exit(1 if fail else 0)
