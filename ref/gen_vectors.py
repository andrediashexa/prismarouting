#!/usr/bin/env python3
"""
Gera test/bpa_vectors.h a partir do modelo de referencia.

Os prefixos vao crus, sem fusao: o C precisa ordenar, recusar sobreposicao
e fundir adjacentes por conta propria, e o resultado e conferido contra o
que o modelo Python produziu.
"""
import ipaddress, os
from bpa_model import PoolConfig, MapPolicy

POOLS = [
    ("contiguo", ["100.64.0.0/22"], ["200.150.0.10/31"], 512, MapPolicy.INTERLEAVED),
    ("frag-interleaved", ["100.64.0.0/22"],
     ["200.150.0.8/30", "45.170.16.64/31", "203.0.113.200/32"], 512, MapPolicy.INTERLEAVED),
    ("frag-blocked", ["100.64.0.0/22"],
     ["200.150.0.8/30", "45.170.16.64/31", "203.0.113.200/32"], 512, MapPolicy.BLOCKED),
    ("interno-frag", ["100.64.0.0/24", "10.20.0.0/24"],
     ["200.150.0.0/29", "45.170.16.0/30"], 1024, MapPolicy.INTERLEAVED),
    # prefixos adjacentes e fora de ordem: exercita fusao e ordenacao no C
    ("adjacentes", ["100.64.0.0/23"],
     ["200.150.0.128/25", "200.150.0.0/25"], 2048, MapPolicy.BLOCKED),
]

def raw(prefixes):
    out = []
    for p in prefixes:
        n = ipaddress.IPv4Network(p, strict=True)
        out.append((int(n.network_address), n.num_addresses))
    return out

L = ["/* GERADO por ref/gen_vectors.py - NAO EDITAR A MAO. */",
     "#ifndef __included_bpa_vectors_h__",
     "#define __included_bpa_vectors_h__", "",
     "typedef struct {",
     "  const char *name;",
     "  const u32 *in_bases, *in_counts; u32 n_in;",
     "  const u32 *out_bases, *out_counts; u32 n_out;",
     "  u16 port_min, port_max, block_size;",
     "  int policy;",
     "  u32 exp_blocks_per_ip, exp_capacity;",
     "  u32 exp_inside_count, exp_outside_count;",
     "  u32 exp_in_ranges, exp_out_ranges;",
     "} bpa_vec_pool_t;", "",
     "typedef struct { u32 pool, ip_priv, ip_pub; u16 port_lo, port_hi; } bpa_vec_fwd_t;",
     "typedef struct { u32 pool, ip_pub; u16 port; u32 ip_priv; } bpa_vec_rev_t;", ""]

built, fwd, rev = [], [], []
for pi, (name, ins, outs, bs, pol) in enumerate(POOLS):
    p = PoolConfig(name, ins, outs, block_size=bs, policy=pol)
    ri, ro = raw(ins), raw(outs)

    L.append(f"static const u32 bpa_in_b_{pi}[] = {{ {', '.join(str(b)+'u' for b,_ in ri)} }};")
    L.append(f"static const u32 bpa_in_c_{pi}[] = {{ {', '.join(str(c)+'u' for _,c in ri)} }};")
    L.append(f"static const u32 bpa_out_b_{pi}[] = {{ {', '.join(str(b)+'u' for b,_ in ro)} }};")
    L.append(f"static const u32 bpa_out_c_{pi}[] = {{ {', '.join(str(c)+'u' for _,c in ro)} }};")

    polc = "BPA_POLICY_INTERLEAVED" if pol is MapPolicy.INTERLEAVED else "BPA_POLICY_BLOCKED"
    built.append(f'  {{ "{name}", bpa_in_b_{pi}, bpa_in_c_{pi}, {len(ri)}u, '
                 f'bpa_out_b_{pi}, bpa_out_c_{pi}, {len(ro)}u, '
                 f'{p.port_min}, {p.port_max}, {bs}, {polc}, '
                 f'{p.blocks_per_ip}u, {p.capacity}u, {p.inside.count}u, '
                 f'{p.outside.count}u, {len(p.inside.ranges)}u, {len(p.outside.ranges)}u }},')

    n = min(p.capacity, p.inside.count)
    for sub in range(n):
        ip_priv = p.inside.addr_of(sub)
        f = p.forward(ip_priv)
        pub = int(ipaddress.IPv4Address(f["ip_pub"]))
        fwd.append(f'  {{ {pi}u, {ip_priv}u, {pub}u, {f["port_lo"]}, {f["port_hi"]} }},')
        for port in (f["port_lo"], f["port_lo"] + bs // 2, f["port_hi"]):
            back = int(ipaddress.IPv4Address(p.reverse(f["ip_pub"], port)))
            rev.append(f'  {{ {pi}u, {pub}u, {port}, {back}u }},')

L.append("")
L.append("static const bpa_vec_pool_t bpa_vec_pools[] = {")
L += built
L.append("};")
L.append(f"#define BPA_VEC_N_POOLS {len(POOLS)}\n")
L.append("static const bpa_vec_fwd_t bpa_vec_fwd[] = {")
L += fwd
L.append("};")
L.append(f"#define BPA_VEC_N_FWD {len(fwd)}\n")
L.append("static const bpa_vec_rev_t bpa_vec_rev[] = {")
L += rev
L.append("};")
L.append(f"#define BPA_VEC_N_REV {len(rev)}\n")
L.append("#endif")

dest = os.path.join(os.path.dirname(__file__), "..", "test", "bpa_vectors.h")
open(dest, "w").write("\n".join(L) + "\n")
print(f"gerado: {len(POOLS)} pools, {len(fwd)} forward, {len(rev)} reverse")
