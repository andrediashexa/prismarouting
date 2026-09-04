#!/usr/bin/env python3
"""
Gera test/bpa_vectors.h a partir do modelo de referencia.

O teste em C consome estes vetores, entao qualquer divergencia entre o
C e o Python vira falha de build. O modelo Python e a fonte da verdade.
"""
import ipaddress, sys, os
from bpa_model import PoolConfig, MapPolicy

POOLS = [
    PoolConfig("det-interleaved", "100.64.0.0/22", "200.150.0.10/31",
               block_size=512, policy=MapPolicy.INTERLEAVED),
    PoolConfig("det-blocked", "100.64.0.0/22", "200.150.0.10/31",
               block_size=512, policy=MapPolicy.BLOCKED),
    PoolConfig("bloco-1024", "100.64.0.0/21", "203.0.113.0/28",
               block_size=1024, policy=MapPolicy.INTERLEAVED),
    PoolConfig("bloco-2048-blocked", "100.64.0.0/20", "203.0.113.0/27",
               block_size=2048, policy=MapPolicy.BLOCKED),
]

out = ["/* GERADO por ref/gen_vectors.py - NAO EDITAR A MAO. */",
       "#ifndef __included_bpa_vectors_h__",
       "#define __included_bpa_vectors_h__", "",
       "typedef struct {",
       "  const char *name;",
       "  u32 inside_base, outside_base, inside_count, outside_count;",
       "  u16 port_min, port_max, block_size;",
       "  int policy;",
       "  u32 exp_blocks_per_ip, exp_capacity;",
       "} bpa_vec_pool_t;", "",
       "typedef struct { u32 pool, ip_priv, ip_pub; u16 port_lo, port_hi; } bpa_vec_fwd_t;",
       "typedef struct { u32 pool, ip_pub; u16 port; u32 ip_priv; } bpa_vec_rev_t;", ""]

out.append("static const bpa_vec_pool_t bpa_vec_pools[] = {")
for p in POOLS:
    pol = "BPA_POLICY_INTERLEAVED" if p.policy is MapPolicy.INTERLEAVED else "BPA_POLICY_BLOCKED"
    out.append(f'  {{ "{p.name}", {p.inside_base}u, {p.outside_base}u, '
               f'{p.inside_count}u, {p.outside_count}u, {p.port_min}, {p.port_max}, '
               f'{p.block_size}, {pol}, {p.blocks_per_ip}u, {p.capacity}u }},')
out.append("};")
out.append(f"#define BPA_VEC_N_POOLS {len(POOLS)}\n")

fwd, rev = [], []
for pi, p in enumerate(POOLS):
    n = min(p.capacity, p.inside_count)
    for sub in range(n):
        ip_priv = p.inside_base + sub
        f = p.forward(ip_priv)
        pub = int(ipaddress.IPv4Address(f["ip_pub"]))
        fwd.append(f'  {{ {pi}u, {ip_priv}u, {pub}u, {f["port_lo"]}, {f["port_hi"]} }},')
        # bordas e meio do bloco no sentido reverso
        for port in (f["port_lo"], f["port_lo"] + p.block_size // 2, f["port_hi"]):
            back = int(ipaddress.IPv4Address(p.reverse(f["ip_pub"], port)))
            rev.append(f'  {{ {pi}u, {pub}u, {port}, {back}u }},')

out.append("static const bpa_vec_fwd_t bpa_vec_fwd[] = {")
out += fwd
out.append("};")
out.append(f"#define BPA_VEC_N_FWD {len(fwd)}\n")

out.append("static const bpa_vec_rev_t bpa_vec_rev[] = {")
out += rev
out.append("};")
out.append(f"#define BPA_VEC_N_REV {len(rev)}\n")
out.append("#endif")

dest = os.path.join(os.path.dirname(__file__), "..", "test", "bpa_vectors.h")
open(dest, "w").write("\n".join(out) + "\n")
print(f"gerado {os.path.normpath(dest)}: {len(POOLS)} pools, "
      f"{len(fwd)} vetores forward, {len(rev)} vetores reverse")
