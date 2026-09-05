#!/usr/bin/env python3
"""Validacao exaustiva do modelo BPA, incluindo pools de faixas nao contiguas."""

import ipaddress, sys, random
from bpa_model import PoolConfig, AddrSet, MapPolicy, PoolMode, plan, BpaError

FAIL = 0

def check(cond, msg):
    global FAIL
    if not cond:
        FAIL += 1
        print(f"  FALHA: {msg}")

def roundtrip_full(pool: PoolConfig):
    """Todo assinante -> bloco -> volta ao mesmo assinante."""
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
                print(f"  COLISAO: {ip_priv} e {seen[key]} -> {key}")
        seen[key] = ip_priv

        for port in (f["port_lo"], f["port_lo"] + pool.block_size // 2, f["port_hi"]):
            if pool.reverse(f["ip_pub"], port) != ip_priv:
                errors += 1
                if errors < 4:
                    print(f"  REVERSO ERRADO: {ip_priv} -> {f['ip_pub']}:{port}")

    check(errors == 0, f"{errors} erros de round-trip")
    check(len(seen) == n, f"esperado {n} blocos distintos, obtido {len(seen)}")
    if errors == 0:
        print(f"  OK  {n:,} assinantes, {len(seen):,} blocos distintos, 0 colisoes")

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
    check(bad == 0, f"{bad} portas mapearam para o assinante errado")
    if bad == 0:
        print(f"  OK  {min(sample,n) * pool.block_size:,} portas verificadas uma a uma")

def check_addrset_bijection(pool: PoolConfig):
    """endereco <-> indice tem que ser bijecao nos dois conjuntos."""
    bad = 0
    for s, label in ((pool.inside, "interno"), (pool.outside, "publico")):
        for idx in range(s.count):
            a = s.addr_of(idx)
            if a is None or s.index_of(a) != idx:
                bad += 1
        # enderecos imediatamente fora de cada faixa nao podem ser aceitos
        for base, cnt, _ in s.ranges:
            for probe in (base - 1, base + cnt):
                got = s.index_of(probe)
                if got is not None:
                    inside_another = any(b <= probe < b + c for b, c, _ in s.ranges)
                    if not inside_another:
                        bad += 1
    check(bad == 0, f"{bad} falhas de bijecao endereco<->indice")
    if bad == 0:
        print(f"  OK  bijecao endereco<->indice integra nos dois conjuntos")

def check_guards(pool: PoolConfig):
    lo_pub = pool.outside.addr_of(0)
    cases = [
        ("porta abaixo da faixa", lambda: pool.reverse(lo_pub, pool.port_min - 1)),
        ("porta 0", lambda: pool.reverse(lo_pub, 0)),
        ("ip interno fora", lambda: pool.forward(pool.inside.ranges[0][0] - 1)),
    ]
    for name, fn in cases:
        try:
            fn()
            check(False, f"{name}: deveria ter sido rejeitado")
        except BpaError:
            pass
    print(f"  OK  {len(cases)} entradas invalidas rejeitadas")

# ---------------------------------------------------------------- pools

pools = [
    # contiguo, como antes: garante que nada regrediu
    PoolConfig("contiguo", ["100.64.0.0/22"], ["200.150.0.10/31"],
               block_size=512, policy=MapPolicy.INTERLEAVED),
    # tres faixas separadas, n_pub = 4+2+1 = 7, NAO e potencia de dois
    PoolConfig("fragmentado-7", ["100.64.0.0/22"],
               ["200.150.0.8/30", "45.170.16.64/31", "203.0.113.200/32"],
               block_size=512, policy=MapPolicy.INTERLEAVED),
    # mesma coisa na politica blocked
    PoolConfig("fragmentado-blocked", ["100.64.0.0/22"],
               ["200.150.0.8/30", "45.170.16.64/31", "203.0.113.200/32"],
               block_size=512, policy=MapPolicy.BLOCKED),
    # interno tambem fragmentado, misturando CGNAT e RFC1918
    PoolConfig("interno-fragmentado", ["100.64.0.0/24", "10.20.0.0/24"],
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
print("CONFIGURACOES INVALIDAS DEVEM SER RECUSADAS")
bad_cases = [
    ("prefixos sobrepostos", lambda: AddrSet(["200.150.0.0/24", "200.150.0.128/25"])),
    ("prefixo identico repetido", lambda: AddrSet(["200.150.0.0/24", "200.150.0.0/24"])),
    ("conjunto vazio", lambda: AddrSet([])),
    ("bloco nao potencia de 2", lambda: PoolConfig("x", ["10.0.0.0/24"], ["1.2.3.0/24"], block_size=500)),
]
for name, fn in bad_cases:
    try:
        fn()
        check(False, f"{name}: deveria ter sido recusado")
    except BpaError:
        print(f"  OK  recusou: {name}")

print("\n" + "=" * 64)
print("FUSAO DE FAIXAS ADJACENTES (menos faixas = datapath mais rapido)")
merged = AddrSet(["200.150.0.0/25", "200.150.0.128/25", "45.170.16.0/24"])
check(len(merged.ranges) == 2, f"esperado 2 faixas apos fusao, obtido {len(merged.ranges)}")
check(merged.count == 512, f"esperado 512 enderecos, obtido {merged.count}")
print(f"  3 prefixos -> {len(merged.ranges)} faixas, {merged.count} enderecos: {merged.describe()}")

print("\n" + "=" * 64)
print(f"RESULTADO: {'TODOS OS TESTES PASSARAM' if FAIL == 0 else str(FAIL) + ' FALHAS'}")
sys.exit(1 if FAIL else 0)
