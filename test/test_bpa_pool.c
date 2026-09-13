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
	      printf ("  FAIL: ");                                           \
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

  printf ("=== address sets and derived values ===\n");
  for (i = 0; i < BPA_VEC_N_POOLS; i++)
    {
      const bpa_vec_pool_t *v = &bpa_vec_pools[i];
      clib_error_t *e = load_pool (&pools[i], v);

      CHECK (e == 0, "pool '%s' rejected", v->name);
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

      printf ("  %-18s in=%u addr/%u range  out=%u addr/%u range  "
	      "blocks/IP=%-4u cap=%-6u div=%u%s\n",
	      v->name, pools[i].inside.count, pools[i].inside.n_ranges,
	      pools[i].outside.count, pools[i].outside.n_ranges,
	      pools[i].blocks_per_ip, pools[i].capacity, pools[i].div.d,
	      pools[i].div.is_pow2 ? " (shift)" : " (reciprocal)");
    }

  printf ("\n=== address <-> index bijection ===\n");
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
	  /* addresses just outside each range must not be accepted */
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
      CHECK (bad == 0, "'%s': %u bijection failures", (char *) p->name, bad);
    }
  printf ("  OK  round trip intact, addresses outside the ranges refused\n");

  printf ("\n=== %u forward vectors (C vs Python model) ===\n",
	  BPA_VEC_N_FWD);
  for (i = 0; i < BPA_VEC_N_FWD; i++)
    {
      const bpa_vec_fwd_t *v = &bpa_vec_fwd[i];
      bpa_block_t b;
      int rv = bpa_forward (&pools[v->pool], v->ip_priv, &b);

      CHECK (rv == 0, "forward %u rejected %u", i, v->ip_priv);
      if (rv)
	continue;
      CHECK (b.ip_pub == v->ip_pub, "vector %u: ip_pub C=%u ref=%u", i,
	     b.ip_pub, v->ip_pub);
      CHECK (b.port_lo == v->port_lo, "vector %u: port_lo C=%u ref=%u", i,
	     b.port_lo, v->port_lo);
      CHECK (b.port_hi == v->port_hi, "vector %u: port_hi C=%u ref=%u", i,
	     b.port_hi, v->port_hi);
    }
  printf ("  %u comparisons\n", BPA_VEC_N_FWD * 3);

  printf ("\n=== %u reverse vectors (C vs Python model) ===\n",
	  BPA_VEC_N_REV);
  for (i = 0; i < BPA_VEC_N_REV; i++)
    {
      const bpa_vec_rev_t *v = &bpa_vec_rev[i];
      u32 got = 0;
      int rv = bpa_reverse (&pools[v->pool], v->ip_pub, v->port, &got);

      CHECK (rv == 0, "reverse %u rejected %u:%u", i, v->ip_pub, v->port);
      if (rv)
	continue;
      CHECK (got == v->ip_priv, "vector %u: ip_priv C=%u ref=%u", i, got,
	     v->ip_priv);
    }
  printf ("  %u comparisons\n", BPA_VEC_N_REV);

  printf ("\n=== exhaustive round trip in C, port by port ===\n");
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
      CHECK (bad == 0, "pool '%s': %u ports mapped wrong", (char *) p->name,
	     bad);
      printf ("  %-18s %llu ports checked, %u errors\n", (char *) p->name,
	      (unsigned long long) tested, bad);
    }

  printf ("\n=== invalid inputs are refused ===\n");
  {
    bpa_pool_t *p = &pools[0];
    u32 dummy, lo_pub = 0;
    bpa_block_t b;

    bpa_set_addr_of (&p->outside, 0, &lo_pub);
    CHECK (bpa_reverse (p, lo_pub, p->port_min - 1, &dummy) != 0,
	   "a port below the range was accepted");
    CHECK (bpa_reverse (p, lo_pub, 0, &dummy) != 0, "port 0 was accepted");
    CHECK (bpa_reverse (p, p->outside.ranges[0].base - 1, 2000, &dummy) != 0,
	   "ip publico fora do conjunto foi aceito");
    CHECK (bpa_forward (p, p->inside.ranges[0].base - 1, &b) != 0,
	   "ip interno fora do conjunto foi aceito");
    printf ("  4 negative cases checked\n");
  }

  /*
   * A port INSIDE the configured range that belongs to no block.
   *
   * blocks_per_ip is a floor: port_count / block_size. When the division is
   * not exact the last partial block is not handed to anybody, and the ports
   * in it are still between port_min and port_max. Only bpa_reverse_sub()'s
   * "blk_idx >= blocks_per_ip" refuses them, and accepting one attributes an
   * out2in packet to a subscriber index past the pool's own capacity - a
   * different customer's traffic, silently.
   *
   * The two port checks above cannot see that bound. A port below port_min
   * underflows the u32 subtraction into a huge block index and a port above
   * port_max lands past the last block, so BOTH are answered by the block
   * bound rather than by the port-range check they name - which is why that
   * check could be deleted whole with this file still green.
   */
  {
    u32 with_tail = 0;

    for (i = 0; i < BPA_VEC_N_POOLS; i++)
      {
	bpa_pool_t *p = &pools[i];
	u32 pub = 0, port, first_tail;

	first_tail = (u32) p->port_min + p->blocks_per_ip * p->block_size;
	if (first_tail > p->port_max)
	  continue; /* the division was exact: this pool has no tail */
	with_tail++;

	if (bpa_set_addr_of (&p->outside, 0, &pub))
	  continue;
	for (port = first_tail; port <= p->port_max; port++)
	  {
	    u32 owner = 0;
	    CHECK (bpa_reverse (p, pub, (u16) port, &owner) != 0,
		   "'%s': port %u is past the last block and was accepted "
		   "(owner %u)",
		   (char *) p->name, port, owner);
	  }
	printf ("  %-18s ports %u-%u belong to no block and are refused\n",
		(char *) p->name, first_tail, p->port_max);
      }

    /* Without a pool whose port count is not a multiple of its block size
     * the loop above asserts nothing, and would go on reading green after
     * the vectors changed. */
    CHECK (with_tail > 0,
	   "no vector pool has a partial last block, so the block bound was "
	   "never exercised");
  }

  printf ("\n=== misconfigured sets and pools must be refused ===\n");
  {
    bpa_addr_set_t s;
    bpa_pool_t p;
    u32 b1[2], c1[2];

    /* an overlap would break the bijection: one subscriber becomes another */
    b1[0] = 0xC0A80000u; c1[0] = 256;
    b1[1] = 0xC0A80080u; c1[1] = 128;
    CHECK (bpa_addr_set_build (&s, b1, c1, 2) != 0,
	   "overlapping prefixes were accepted");

    b1[0] = 0xC0A80000u; c1[0] = 256;
    b1[1] = 0xC0A80000u; c1[1] = 256;
    CHECK (bpa_addr_set_build (&s, b1, c1, 2) != 0,
	   "a duplicate prefix was accepted");

    CHECK (bpa_addr_set_build (&s, b1, c1, 0) != 0,
	   "an empty set was accepted");

    b1[0] = 0xFFFFFF00u; c1[0] = 512; /* runs past the end of the v4 space */
    CHECK (bpa_addr_set_build (&s, b1, c1, 1) != 0,
	   "a range overflowing 255.255.255.255 was accepted");

    load_pool (&p, &bpa_vec_pools[0]);
    p.block_size = 500;
    CHECK (bpa_pool_init (&p) != 0, "a non-power-of-two block_size was accepted");

    load_pool (&p, &bpa_vec_pools[0]);
    p.port_min = 40000; p.port_max = 30000;
    CHECK (bpa_pool_init (&p) != 0, "an inverted port range was accepted");

    load_pool (&p, &bpa_vec_pools[0]);
    p.block_size = 0;
    CHECK (bpa_pool_init (&p) != 0, "a zero block_size was accepted");
    printf ("  7 invalid configurations refused\n");
  }

  printf ("\n=== merging of adjacent ranges ===\n");
  {
    bpa_addr_set_t s;
    u32 b[3] = { 0xC8960080u, 0xC8960000u, 0x2DAA1000u }; /* out of order */
    u32 c[3] = { 128, 128, 256 };

    CHECK (bpa_addr_set_build (&s, b, c, 3) == 0, "merging rejected a valid input");
    CHECK (s.n_ranges == 2, "expected 2 ranges after merging, got %u",
	   s.n_ranges);
    CHECK (s.count == 512, "expected 512 addresses, got %u", s.count);
    printf ("  3 out-of-order prefixes -> %u ranges, %u addresses\n",
	    s.n_ranges, s.count);
  }

  printf ("\n=== block stride: where the blocks after the first go ===\n");
  {
    unsigned k, placed = 0, refused = 0;

    for (i = 0; i < BPA_VEC_N_POOLS; i++)
      CHECK (bpa_stride_unit (&pools[i]) == bpa_vec_pools[i].exp_stride_unit,
	     "'%s': stride unit C=%u ref=%u", bpa_vec_pools[i].name,
	     bpa_stride_unit (&pools[i]), bpa_vec_pools[i].exp_stride_unit);

    for (k = 0; k < BPA_VEC_N_STRIDE; k++)
      {
	const bpa_vec_stride_t *v = &bpa_vec_stride[k];
	bpa_pool_t *p = &pools[v->pool];
	u32 got = 0, pub_idx = 0, blk_idx = 0, ip_pub = 0, port_lo;
	int rc;

	p->block_stride = v->stride;
	rc = bpa_stride_block (p, v->primary_block, v->nth, &got);
	p->block_stride = 0;

	if (!v->has)
	  {
	    /* The stride ran off the end of the subscriber's own public
	     * address. Wrapping instead of refusing would hand the subscriber
	     * a block on a DIFFERENT address, whose index need not be in its
	     * own worker's slice - and then its replies arrive on a thread
	     * that knows nothing about the session. */
	    CHECK (rc != 0,
		   "'%s': stride %u placed block %u for primary %u nth %u, "
		   "which the model says runs off the public address",
		   (char *) p->name, v->stride, got, v->primary_block, v->nth);
	    refused++;
	    continue;
	  }

	CHECK (rc == 0, "'%s': stride %u placed nothing for primary %u nth %u",
	       (char *) p->name, v->stride, v->primary_block, v->nth);
	if (rc)
	  continue;
	CHECK (got == v->block, "'%s': stride %u primary %u nth %u: block "
			       "C=%u ref=%u",
	       (char *) p->name, v->stride, v->primary_block, v->nth, got,
	       v->block);

	/* And in the units the log records and an operator reads, because a
	 * block index agreeing is not the same claim as a port range
	 * agreeing. */
	bpa_split (p, got, &pub_idx, &blk_idx);
	CHECK (bpa_set_addr_of (&p->outside, pub_idx, &ip_pub) == 0,
	       "'%s': stride block %u has no public address", (char *) p->name,
	       got);
	port_lo = p->port_min + blk_idx * p->block_size;
	CHECK (ip_pub == v->ip_pub, "'%s': stride %u primary %u nth %u: "
				   "public C=%u ref=%u",
	       (char *) p->name, v->stride, v->primary_block, v->nth, ip_pub,
	       v->ip_pub);
	CHECK (port_lo == v->port_lo, "'%s': stride %u primary %u nth %u: "
				     "port_lo C=%u ref=%u",
	       (char *) p->name, v->stride, v->primary_block, v->nth, port_lo,
	       v->port_lo);
	placed++;
      }

    printf ("  %u vectors: %u blocks placed, %u refused at the end of an "
	    "address\n",
	    (unsigned) BPA_VEC_N_STRIDE, placed, refused);

    /* Without a refusal among the vectors the branch above asserts nothing
     * and would read green after a plugin started wrapping. */
    CHECK (refused > 0,
	   "no vector reaches the end of a public address, so the stride "
	   "bound was never exercised");
    CHECK (placed > 0, "no vector placed a block at all");
  }

  printf ("\n=== the stride is not part of the mapping ===\n");
  {
    unsigned moved = 0, tried = 0;

    /* The #23 requirement as arithmetic. A stride decides where a
     * subscriber's SECOND block goes; if it ever moved the first, changing it
     * would disconnect every subscriber on a deterministic pool and would
     * belong to the capacity-change procedure instead of being a setting. */
    for (i = 0; i < BPA_VEC_N_POOLS; i++)
      {
	bpa_pool_t *p = &pools[i];
	u32 sub, stride;

	for (stride = 0; stride < 8 && stride < p->blocks_per_ip; stride++)
	  {
	    tried++;
	    for (sub = 0; sub < p->inside.count && sub < p->capacity;
		 sub += 17)
	      {
		bpa_block_t a, b;
		u32 ip_priv;

		if (bpa_set_addr_of (&p->inside, sub, &ip_priv))
		  continue;
		p->block_stride = 0;
		if (bpa_forward (p, ip_priv, &a))
		  continue;
		p->block_stride = (u16) stride;
		if (bpa_forward (p, ip_priv, &b))
		  continue;
		p->block_stride = 0;
		if (a.ip_pub != b.ip_pub || a.port_lo != b.port_lo)
		  moved++;
	      }
	  }
      }
    CHECK (moved == 0,
	   "%u subscribers changed public address or port block when only the "
	   "stride changed",
	   moved);
    printf ("  %u pool/stride combinations move 0 subscribers\n", tried);
  }

  printf ("\n=== a stride the sharding cannot honour ===\n");
  {
    bpa_pool_t *p = &pools[0]; /* interleaved, 2 public addresses */

    /* Blocks are partitioned by block_idx %% n_workers and a subscriber draws
     * only from its own worker's slice, so a stride whose step is not a
     * multiple of the worker count names a block this worker may not hand
     * out - for every subscriber, which is why it is refused rather than
     * counted. */
    p->block_stride = 1;
    CHECK (bpa_stride_reachable (p, 1), "stride 1 unreachable on one worker");
    CHECK (bpa_stride_reachable (p, 2),
	   "stride 1 over 2 public addresses unreachable at 2 workers");
    CHECK (!bpa_stride_reachable (p, 4),
	   "stride 1 over 2 public addresses was called reachable at 4 "
	   "workers, where it steps 2 and the slice repeats every 4");
    CHECK (bpa_stride_next_ok (p, 1, 4) == 2,
	   "the advice for stride 1 at 4 workers was %u, not 2",
	   bpa_stride_next_ok (p, 1, 4));
    p->block_stride = 0;

    p = &pools[2]; /* blocked: one stride step is one block index */
    p->block_stride = 3;
    CHECK (!bpa_stride_reachable (p, 2), "stride 3 blocked at 2 workers");
    CHECK (bpa_stride_next_ok (p, 3, 2) == 4,
	   "the advice for stride 3 at 2 workers was %u, not 4",
	   bpa_stride_next_ok (p, 3, 2));
    p->block_stride = 0;
    printf ("  a stride outside the slice is refused, with the nearest one "
	    "that is not\n");
  }

  printf ("\n=== a stride that can never place a block is refused ===\n");
  {
    bpa_pool_t p;

    load_pool (&p, &bpa_vec_pools[0]);
    p.block_stride = (u16) p.blocks_per_ip;
    CHECK (bpa_pool_init (&p) != 0,
	   "a stride of blocks_per_ip was accepted, and would place nothing");

    load_pool (&p, &bpa_vec_pools[0]);
    p.block_stride = (u16) (p.blocks_per_ip - 1);
    CHECK (bpa_pool_init (&p) == 0,
	   "the largest stride that fits was refused");
    printf ("  the first stride that cannot fit is refused; the one below it "
	    "is not\n");
  }

  return bpa_test_verdict (checks, failures);
}
