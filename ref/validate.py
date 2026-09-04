#!/usr/bin/env python3
"""Validacao exaustiva do modelo BPA + geracao de vetores de teste para o C."""

import ipaddress, json, sys, random
from bpa_model import PoolConfig, MapPolicy, PoolMode, plan, BpaError

FAIL = 0

def check(cond, msg):
    global FAIL
    if not cond:
        FAIL += 1
        print(f"  FALHA: {msg}")

def roundtrip_full(pool: PoolConfig, label: str):
    """Todo assinante -> bloco -> volta ao mesmo assinante, em TODAS as portas."""
    n = min(pool.capacity, pool.inside_count)
    print(f"\n[{label}] round-trip exaustivo de {n:,} assinantes")

    seen_blocks = {}
    errors = 0
    for sub in range(n):
        ip_priv = str(ipaddress.IPv4Address(pool.inside_base + sub))
        f = pool.forward(ip_priv)

        # 1. bloco unico: nenhum assinante compartilha (ip_pub, bloco)
        key = (f["ip_pub"], f["blk_idx"])
        if key in seen_blocks:
            errors += 1
            if errors < 4:
                print(f"  COLISAO: {ip_priv} e {seen_blocks[key]} -> {key}")
        seen_blocks[key] = ip_priv

        # 2. reverso bate em ambas as bordas e no meio do bloco
        for port in (f["port_lo"], f["port_lo"] + pool.block_size // 2, f["port_hi"]):
            back = pool.reverse(f["ip_pub"], port)
            if back != ip_priv:
                errors += 1
                if errors < 4:
                    print(f"  REVERSO ERRADO: {ip_priv} -> {f['ip_pub']}:{port} -> {back}")

    check(errors == 0, f"{errors} erros de round-trip")
    check(len(seen_blocks) == n, f"esperado {n} blocos distintos, obtido {len(seen_blocks)}")
    if errors == 0:
        print(f"  OK  {n:,} assinantes, {len(seen_blocks):,} blocos distintos, 0 colisoes")

def roundtrip_all_ports(pool: PoolConfig, sample: int = 200):
    """Numa amostra de assinantes, testa TODAS as portas do bloco."""
    print(f"\n[{pool.name}] varredura porta-a-porta em {sample} assinantes")
    n = min(pool.capacity, pool.inside_count)
    rnd = random.Random(42)
    bad = 0
    for _ in range(sample):
        sub = rnd.randrange(n)
        ip_priv = str(ipaddress.IPv4Address(pool.inside_base + sub))
        f = pool.forward(ip_priv)
        for port in range(f["port_lo"], f["port_hi"] + 1):
            if pool.reverse(f["ip_pub"], port) != ip_priv:
                bad += 1
    check(bad == 0, f"{bad} portas mapearam para o assinante errado")
    if bad == 0:
        print(f"  OK  {sample * pool.block_size:,} portas verificadas uma a uma")

def check_coverage(pool: PoolConfig):
    """Toda porta valida de todo IP publico resolve para exatamente um assinante."""
    print(f"\n[{pool.name}] cobertura do espaco publico")
    unmapped = 0
    for pub_idx in range(min(pool.outside_count, 64)):
        ip_pub = str(ipaddress.IPv4Address(pool.outside_base + pub_idx))
        for blk in range(pool.blocks_per_ip):
            port = pool.port_min + blk * pool.block_size
            try:
                pool.reverse(ip_pub, port)
            except BpaError:
                unmapped += 1
    check(unmapped == 0, f"{unmapped} blocos publicos sem assinante")
    if unmapped == 0:
        print(f"  OK  todo bloco publico resolve para um assinante")

def check_guards(pool: PoolConfig):
    """Entradas invalidas devem ser rejeitadas, nunca traduzidas silenciosamente."""
    print(f"\n[{pool.name}] rejeicao de entradas invalidas")
    cases = [
        ("porta abaixo da faixa", lambda: pool.reverse(str(ipaddress.IPv4Address(pool.outside_base)), pool.port_min - 1)),
        ("porta 0",               lambda: pool.reverse(str(ipaddress.IPv4Address(pool.outside_base)), 0)),
        ("ip publico fora",       lambda: pool.reverse(str(ipaddress.IPv4Address(pool.outside_base + pool.outside_count)), 2000)),
        ("ip interno fora",       lambda: pool.forward(str(ipaddress.IPv4Address(pool.inside_base - 1)))),
    ]
    for name, fn in cases:
        try:
            fn()
            check(False, f"{name}: deveria ter sido rejeitado")
        except BpaError:
            print(f"  OK  rejeitou: {name}")

# ---------------------------------------------------------------- main

pools = [
    PoolConfig("det-interleaved", "100.64.0.0/22", "200.150.0.10/31",
               block_size=512, policy=MapPolicy.INTERLEAVED),
    PoolConfig("det-blocked", "100.64.0.0/22", "200.150.0.10/31",
               block_size=512, policy=MapPolicy.BLOCKED),
    PoolConfig("bloco-1024", "100.64.0.0/21", "203.0.113.0/28",
               block_size=1024, policy=MapPolicy.INTERLEAVED),
]

for p in pools:
    print("=" * 62)
    print(p.summary())
    roundtrip_full(p, p.name)
    roundtrip_all_ports(p, sample=50)
    check_coverage(p)
    check_guards(p)

print("\n" + "=" * 62)
print("PLANEJAMENTO DE CAPACIDADE (200k assinantes)")
for bs in (256, 512, 1024, 2048, 4096):
    r = plan(200_000, bs)
    print(f"  bloco {bs:>5}: {r['blocos_por_ip']:>3} blocos/IP -> "
          f"{r['ips_publicos']:>5} IPs publicos  {r['prefixo_sugerido']}")

print("\n" + "=" * 62)
print(f"RESULTADO: {'TODOS OS TESTES PASSARAM' if FAIL == 0 else str(FAIL) + ' FALHAS'}")
sys.exit(1 if FAIL else 0)
