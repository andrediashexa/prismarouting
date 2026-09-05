/*
 * test_bpa_pool.c - unit test for the BPA mapping core.
 *
 * Builds standalone (no VPP tree) and checks the C implementation against
 * vectors generated from ref/bpa_model.py, which is the source of truth.
 * The pools deliberately include non-contiguous public ranges, because
 * that is what real provider IPv4 allocations look like.
 */

#include <stdio.h>
#include <string.h>
#include "bpa_shim.h"
#include "../src/plugins/bpa/bpa_pool.h"
#include "bpa_vectors.h"

static int failures = 0;
static unsigned checks = 0;

#define CHECK(cond, ...)                                                      \
  do                                                                          \
    {                                                                         \
      checks++;                                                               \
      if (!(cond))                                                            \
	{                                                                     \
	  if (failures < 10)                                                  \
	    {                                                                 \
	      printf ("  FALHA: ");                                           \
	      printf (__VA_ARGS__);                                           \
	      printf ("\n");                                                  \
	    }                                                                 \
	  failures++;                                                         \
	}                                                                     \
    }                                                                         \
  while (0)

static clib_error_t *
load_pool (bpa_pool_t *p, const bpa_vec_pool_t *v)
{
  clib_error_t *e;

  memset (p, 0, sizeof (*p));
  p->name = (u8 *) v->name;
  p->port_min = v->port_min;
  p->port_max = v->port_max;
  p->block_size = v->block_size;
  p->policy = (bpa_policy_t) v->policy;
  p->mode = BPA_MODE_DETERMINISTIC;

  e = bpa_addr_set_build (&p->inside, v->in_bases, v->in_counts, v->n_in);
  if (e)
    return e;
  e = bpa_addr_set_build (&p->outside, v->out_bases, v->out_counts, v->n_out);
  if (e)
    return e;

  return bpa_pool_init (p);
}

int
main (void)
{
  bpa_pool_t pools[BPA_VEC_N_POOLS];
  unsigned i;

  printf ("=== construcao dos conjuntos e valores derivados ===\n");
  for (i = 0; i < BPA_VEC_N_POOLS; i++)
    {
      const bpa_vec_pool_t *v = &bpa_vec_pools[i];
      clib_error_t *e = load_pool (&pools[i], v);

      CHECK (e == 0, "pool '%s' rejeitado", v->name);
      if (e)
	continue;

      CHECK (pools[i].inside.count == v->exp_inside_count,
	     "'%s': inside.count C=%u ref=%u", v->name, pools[i].inside.count,
	     v->exp_inside_count);
      CHECK (pools[i].outside.count == v->exp_outside_count,
	     "'%s': outside.count C=%u ref=%u", v->name,
	     pools[i].outside.count, v->exp_outside_count);
      CHECK (pools[i].inside.n_ranges == v->exp_in_ranges,
	     "'%s': faixas internas C=%u ref=%u (fusao divergiu)", v->name,
	     pools[i].inside.n_ranges, v->exp_in_ranges);
      CHECK (pools[i].outside.n_ranges == v->exp_out_ranges,
	     "'%s': faixas publicas C=%u ref=%u (fusao divergiu)", v->name,
	     pools[i].outside.n_ranges, v->exp_out_ranges);
      CHECK (pools[i].blocks_per_ip == v->exp_blocks_per_ip,
	     "'%s': blocks_per_ip C=%u ref=%u", v->name,
	     pools[i].blocks_per_ip, v->exp_blocks_per_ip);
      CHECK (pools[i].capacity == v->exp_capacity, "'%s': capacity C=%u ref=%u",
	     v->name, pools[i].capacity, v->exp_capacity);

      printf ("  %-18s in=%u addr/%u faixa  out=%u addr/%u faixa  "
	      "blocos/IP=%-4u cap=%-6u div=%u%s\n",
	      v->name, pools[i].inside.count, pools[i].inside.n_ranges,
	      pools[i].outside.count, pools[i].outside.n_ranges,
	      pools[i].blocks_per_ip, pools[i].capacity, pools[i].div.d,
	      pools[i].div.is_pow2 ? " (shift)" : " (reciprocal)");
    }

  printf ("\n=== bijecao endereco <-> indice ===\n");
  for (i = 0; i < BPA_VEC_N_POOLS; i++)
    {
      bpa_pool_t *p = &pools[i];
      bpa_addr_set_t *sets[2] = { &p->inside, &p->outside };
      u32 bad = 0, s, j;

      for (s = 0; s < 2; s++)
	{
	  bpa_addr_set_t *set = sets[s];
	  for (j = 0; j < set->count; j++)
	    {
	      u32 addr = 0, back = 0;
	      if (bpa_set_addr_of (set, j, &addr)
		  || bpa_set_index_of (set, addr, &back) || back != j)
		bad++;
	    }
	  /* enderecos logo fora de cada faixa nao podem ser aceitos */
	  for (j = 0; j < set->n_ranges; j++)
	    {
	      u32 probe[2] = { set->ranges[j].base - 1,
			       set->ranges[j].base + set->ranges[j].count };
	      u32 k, idx;
	      for (k = 0; k < 2; k++)
		{
		  u32 m, inside_other = 0;
		  for (m = 0; m < set->n_ranges; m++)
		    if (probe[k] - set->ranges[m].base < set->ranges[m].count)
		      inside_other = 1;
		  if (!inside_other
		      && bpa_set_index_of (set, probe[k], &idx) == 0)
		    bad++;
		}
	    }
	}
      CHECK (bad == 0, "'%s': %u falhas de bijecao", (char *) p->name, bad);
    }
  printf ("  OK  ida e volta integra, e enderecos fora das faixas recusados\n");

  printf ("\n=== %u vetores forward (C vs modelo Python) ===\n",
	  BPA_VEC_N_FWD);
  for (i = 0; i < BPA_VEC_N_FWD; i++)
    {
      const bpa_vec_fwd_t *v = &bpa_vec_fwd[i];
      bpa_block_t b;
      int rv = bpa_forward (&pools[v->pool], v->ip_priv, &b);

      CHECK (rv == 0, "forward %u rejeitou %u", i, v->ip_priv);
      if (rv)
	continue;
      CHECK (b.ip_pub == v->ip_pub, "vetor %u: ip_pub C=%u ref=%u", i,
	     b.ip_pub, v->ip_pub);
      CHECK (b.port_lo == v->port_lo, "vetor %u: port_lo C=%u ref=%u", i,
	     b.port_lo, v->port_lo);
      CHECK (b.port_hi == v->port_hi, "vetor %u: port_hi C=%u ref=%u", i,
	     b.port_hi, v->port_hi);
    }
  printf ("  %u comparacoes\n", BPA_VEC_N_FWD * 3);

  printf ("\n=== %u vetores reverse (C vs modelo Python) ===\n",
	  BPA_VEC_N_REV);
  for (i = 0; i < BPA_VEC_N_REV; i++)
    {
      const bpa_vec_rev_t *v = &bpa_vec_rev[i];
      u32 got = 0;
      int rv = bpa_reverse (&pools[v->pool], v->ip_pub, v->port, &got);

      CHECK (rv == 0, "reverse %u rejeitou %u:%u", i, v->ip_pub, v->port);
      if (rv)
	continue;
      CHECK (got == v->ip_priv, "vetor %u: ip_priv C=%u ref=%u", i, got,
	     v->ip_priv);
    }
  printf ("  %u comparacoes\n", BPA_VEC_N_REV);

  printf ("\n=== round-trip exaustivo em C, porta a porta ===\n");
  for (i = 0; i < BPA_VEC_N_POOLS; i++)
    {
      bpa_pool_t *p = &pools[i];
      u32 n = p->capacity < p->inside.count ? p->capacity : p->inside.count;
      u32 sub, port, bad = 0;
      u64 tested = 0;

      for (sub = 0; sub < n; sub++)
	{
	  bpa_block_t b;
	  u32 ip_priv = 0;

	  if (bpa_set_addr_of (&p->inside, sub, &ip_priv)
	      || bpa_forward (p, ip_priv, &b))
	    {
	      bad++;
	      continue;
	    }
	  for (port = b.port_lo; port <= b.port_hi; port++)
	    {
	      u32 back = 0;
	      if (bpa_reverse (p, b.ip_pub, (u16) port, &back)
		  || back != ip_priv)
		bad++;
	      tested++;
	    }
	}
      CHECK (bad == 0, "pool '%s': %u portas mapearam errado", (char *) p->name,
	     bad);
      printf ("  %-18s %llu portas conferidas, %u erros\n", (char *) p->name,
	      (unsigned long long) tested, bad);
    }

  printf ("\n=== rejeicao de entradas invalidas ===\n");
  {
    bpa_pool_t *p = &pools[0];
    u32 dummy, lo_pub = 0;
    bpa_block_t b;

    bpa_set_addr_of (&p->outside, 0, &lo_pub);
    CHECK (bpa_reverse (p, lo_pub, p->port_min - 1, &dummy) != 0,
	   "porta abaixo da faixa foi aceita");
    CHECK (bpa_reverse (p, lo_pub, 0, &dummy) != 0, "porta 0 foi aceita");
    CHECK (bpa_reverse (p, p->outside.ranges[0].base - 1, 2000, &dummy) != 0,
	   "ip publico fora do conjunto foi aceito");
    CHECK (bpa_forward (p, p->inside.ranges[0].base - 1, &b) != 0,
	   "ip interno fora do conjunto foi aceito");
    printf ("  4 casos negativos verificados\n");
  }

  printf ("\n=== conjuntos e pools mal configurados devem ser recusados ===\n");
  {
    bpa_addr_set_t s;
    bpa_pool_t p;
    u32 b1[2], c1[2];

    /* sobreposicao quebraria a bijecao: um assinante viraria outro */
    b1[0] = 0xC0A80000u; c1[0] = 256;
    b1[1] = 0xC0A80080u; c1[1] = 128;
    CHECK (bpa_addr_set_build (&s, b1, c1, 2) != 0,
	   "prefixos sobrepostos foram aceitos");

    b1[0] = 0xC0A80000u; c1[0] = 256;
    b1[1] = 0xC0A80000u; c1[1] = 256;
    CHECK (bpa_addr_set_build (&s, b1, c1, 2) != 0,
	   "prefixo duplicado foi aceito");

    CHECK (bpa_addr_set_build (&s, b1, c1, 0) != 0,
	   "conjunto vazio foi aceito");

    b1[0] = 0xFFFFFF00u; c1[0] = 512; /* passa do fim do espaco v4 */
    CHECK (bpa_addr_set_build (&s, b1, c1, 1) != 0,
	   "faixa que estoura 255.255.255.255 foi aceita");

    load_pool (&p, &bpa_vec_pools[0]);
    p.block_size = 500;
    CHECK (bpa_pool_init (&p) != 0, "block_size nao-pot2 foi aceito");

    load_pool (&p, &bpa_vec_pools[0]);
    p.port_min = 40000; p.port_max = 30000;
    CHECK (bpa_pool_init (&p) != 0, "faixa de portas invertida foi aceita");

    load_pool (&p, &bpa_vec_pools[0]);
    p.block_size = 0;
    CHECK (bpa_pool_init (&p) != 0, "block_size zero foi aceito");
    printf ("  7 configuracoes invalidas recusadas\n");
  }

  printf ("\n=== fusao de faixas adjacentes ===\n");
  {
    bpa_addr_set_t s;
    u32 b[3] = { 0xC8960080u, 0xC8960000u, 0x2DAA1000u }; /* fora de ordem */
    u32 c[3] = { 128, 128, 256 };

    CHECK (bpa_addr_set_build (&s, b, c, 3) == 0, "fusao rejeitou entrada valida");
    CHECK (s.n_ranges == 2, "esperado 2 faixas apos fusao, obtido %u",
	   s.n_ranges);
    CHECK (s.count == 512, "esperado 512 enderecos, obtido %u", s.count);
    printf ("  3 prefixos fora de ordem -> %u faixas, %u enderecos\n",
	    s.n_ranges, s.count);
  }

  printf ("\n%s  (%u verificacoes, %d falhas)\n",
	  failures ? "FALHOU" : "TODOS OS TESTES PASSARAM", checks, failures);
  return failures ? 1 : 0;
}
