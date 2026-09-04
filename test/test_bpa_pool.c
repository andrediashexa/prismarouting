/*
 * test_bpa_pool.c - unit test for the BPA mapping core.
 *
 * Builds standalone (no VPP tree) and checks the C implementation against
 * vectors generated from ref/bpa_model.py, which is the source of truth.
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

static void
load_pool (bpa_pool_t *p, const bpa_vec_pool_t *v)
{
  memset (p, 0, sizeof (*p));
  p->name = (u8 *) v->name;
  p->inside_base = v->inside_base;
  p->outside_base = v->outside_base;
  p->inside_count = v->inside_count;
  p->outside_count = v->outside_count;
  p->port_min = v->port_min;
  p->port_max = v->port_max;
  p->block_size = v->block_size;
  p->policy = (bpa_policy_t) v->policy;
  p->mode = BPA_MODE_DETERMINISTIC;
}

int
main (void)
{
  bpa_pool_t pools[BPA_VEC_N_POOLS];
  unsigned i;

  printf ("=== init dos pools e valores derivados ===\n");
  for (i = 0; i < BPA_VEC_N_POOLS; i++)
    {
      const bpa_vec_pool_t *v = &bpa_vec_pools[i];
      clib_error_t *e;

      load_pool (&pools[i], v);
      e = bpa_pool_init (&pools[i]);
      CHECK (e == 0, "pool '%s' rejeitado", v->name);
      CHECK (pools[i].blocks_per_ip == v->exp_blocks_per_ip,
	     "pool '%s': blocks_per_ip C=%u ref=%u", v->name,
	     pools[i].blocks_per_ip, v->exp_blocks_per_ip);
      CHECK (pools[i].capacity == v->exp_capacity,
	     "pool '%s': capacity C=%u ref=%u", v->name, pools[i].capacity,
	     v->exp_capacity);
      printf ("  %-20s blocos/IP=%-4u capacidade=%-6u %s\n", v->name,
	      pools[i].blocks_per_ip, pools[i].capacity,
	      pools[i].policy == BPA_POLICY_INTERLEAVED ? "interleaved"
							: "blocked");
    }

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
      u32 n = p->capacity < p->inside_count ? p->capacity : p->inside_count;
      u32 sub, port, bad = 0;
      u64 tested = 0;

      for (sub = 0; sub < n; sub++)
	{
	  bpa_block_t b;
	  if (bpa_forward (p, p->inside_base + sub, &b))
	    {
	      bad++;
	      continue;
	    }
	  for (port = b.port_lo; port <= b.port_hi; port++)
	    {
	      u32 back = 0;
	      if (bpa_reverse (p, b.ip_pub, (u16) port, &back)
		  || back != p->inside_base + sub)
		bad++;
	      tested++;
	    }
	}
      CHECK (bad == 0, "pool '%s': %u portas mapearam errado", p->name, bad);
      printf ("  %-20s %llu portas conferidas, %u erros\n", (char *) p->name,
	      (unsigned long long) tested, bad);
    }

  printf ("\n=== rejeicao de entradas invalidas ===\n");
  {
    bpa_pool_t *p = &pools[0];
    u32 dummy;
    bpa_block_t b;

    CHECK (bpa_reverse (p, p->outside_base, p->port_min - 1, &dummy) != 0,
	   "porta abaixo da faixa foi aceita");
    CHECK (bpa_reverse (p, p->outside_base, 0, &dummy) != 0,
	   "porta 0 foi aceita");
    CHECK (bpa_reverse (p, p->outside_base + p->outside_count, 2000, &dummy)
	     != 0,
	   "ip publico fora do pool foi aceito");
    CHECK (bpa_forward (p, p->inside_base - 1, &b) != 0,
	   "ip interno fora do pool foi aceito");
    CHECK (bpa_forward (p, p->inside_base + p->capacity, &b) != 0,
	   "assinante acima da capacidade foi aceito");
    printf ("  5 casos negativos verificados\n");
  }

  printf ("\n=== pools mal configurados devem ser recusados ===\n");
  {
    bpa_pool_t p;
    load_pool (&p, &bpa_vec_pools[0]);
    p.block_size = 500; /* nao e potencia de dois */
    CHECK (bpa_pool_init (&p) != 0, "block_size nao-pot2 foi aceito");

    load_pool (&p, &bpa_vec_pools[0]);
    p.outside_count = 3; /* nao e potencia de dois */
    CHECK (bpa_pool_init (&p) != 0, "prefixo publico nao-pot2 foi aceito");

    load_pool (&p, &bpa_vec_pools[0]);
    p.port_min = 40000;
    p.port_max = 30000; /* invertido */
    CHECK (bpa_pool_init (&p) != 0, "faixa de portas invertida foi aceita");

    load_pool (&p, &bpa_vec_pools[0]);
    p.block_size = 0;
    CHECK (bpa_pool_init (&p) != 0, "block_size zero foi aceito");
    printf ("  4 configuracoes invalidas recusadas\n");
  }

  printf ("\n%s  (%u verificacoes, %d falhas)\n",
	  failures ? "FALHOU" : "TODOS OS TESTES PASSARAM", checks, failures);
  return failures ? 1 : 0;
}
