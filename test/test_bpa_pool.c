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

/*
 * The capacity arithmetic, simulated rather than restated.
 *
 * bpa_pool_guaranteed() answers with a closed form, and a test that wrote the
 * same closed form a second time would agree with itself. What is modelled
 * here instead is the MECHANISM: bpa_dyn_init() cuts the blocks into slices,
 * bpa_owner_worker() decides which slice a subscriber may draw from, and
 * bpa_dyn_acquire() hands a slice out first-come until it is empty. From
 * those three, by walking every way n subscribers can fall across the
 * workers, comes the largest n that is always served.
 */

/* One worker's slice of the blocks, the way bpa_dyn_init() cuts it. */
static u32
slice_of (u32 capacity, u32 nw, u32 w)
{
  return capacity / nw + (w < capacity % nw ? 1 : 0);
}

/* Inside addresses that can only draw from that slice, via bpa_owner_worker. */
static u32
subs_of (u32 inside, u32 nw, u32 w)
{
  return inside / nw + (w < inside % nw ? 1 : 0);
}

/*
 * Is there a choice of subscribers, `left` of them still to place from worker
 * `w` on, that leaves one of them without a block? `over` carries whether some
 * worker already visited was handed more than its slice holds.
 *
 * Whole distributions only: a branch that runs out of workers with subscribers
 * still to place is not a set of that size at all, and counting it would let
 * the test refuse a pool for a crowd it cannot assemble.
 */
static int
refusable (u32 capacity, u32 inside, u32 nw, u32 w, u32 left, int over)
{
  u32 here, max;

  if (w == nw)
    return left == 0 && over;

  max = subs_of (inside, nw, w);
  if (left < max)
    max = left;
  for (here = 0; here <= max; here++)
    if (refusable (capacity, inside, nw, w + 1, left - here,
		   over || here > slice_of (capacity, nw, w)))
      return 1;
  return 0;
}

/* The largest n the mechanism serves whatever addresses the n subscribers
 * have: one below the smallest crowd it can be made to refuse. */
static u32
simulated_floor (u32 capacity, u32 inside, u32 nw)
{
  u32 usable = capacity < inside ? capacity : inside, n;

  for (n = 1; n <= usable; n++)
    if (refusable (capacity, inside, nw, 0, n, 0))
      return n - 1;
  return usable;
}

/* A pool with nothing set but the three fields the capacity figures read. */
static u32
floor_of (u32 capacity, u32 inside, u32 nw)
{
  bpa_pool_t p;

  memset (&p, 0, sizeof (p));
  p.mode = BPA_MODE_DYNAMIC;
  p.capacity = capacity;
  p.inside.count = inside;
  return bpa_pool_guaranteed (&p, nw);
}

static clib_error_t *
load_pool (bpa_pool_t *p, const bpa_vec_pool_t *v)
{
  clib_error_t *e;

  memset (p, 0, sizeof (*p));
  p->name = (u8 *) v->name;
  p->port_min = v->port_min;
  p->port_max = v->port_max;
  p->block_size = v->block_size;
  p->block_reserve = v->reserve;
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
      CHECK (pools[i].blocks_mapped == v->exp_blocks_mapped,
	     "'%s': blocks_mapped C=%u ref=%u", v->name,
	     pools[i].blocks_mapped, v->exp_blocks_mapped);
      CHECK (pools[i].reserve_count == v->exp_reserve_count,
	     "'%s': reserve_count C=%u ref=%u", v->name,
	     pools[i].reserve_count, v->exp_reserve_count);
      /*
       * The number the box reports and the number the model predicts are the
       * same number, and this is where that is enforced. A reserve is paid
       * for in capacity, so a plugin that carved one out and went on
       * reporting the old capacity would tell an operator the pool serves
       * subscribers it will drop.
       */
      CHECK (pools[i].capacity == v->exp_capacity, "'%s': capacity C=%u ref=%u",
	     v->name, pools[i].capacity, v->exp_capacity);

      printf ("  %-18s in=%u addr/%u range  out=%u addr/%u range  "
	      "blocks/IP=%-4u cap=%-6u div=%u%s\n",
	      v->name, pools[i].inside.count, pools[i].inside.n_ranges,
	      pools[i].outside.count, pools[i].outside.n_ranges,
	      pools[i].blocks_per_ip, pools[i].capacity, pools[i].div.d,
	      pools[i].div.is_pow2 ? " (shift)" : " (reciprocal)");
      if (v->reserve)
	printf ("  %-18s reserve %u block(s)/IP -> %u mapped, %u held\n", "",
		v->reserve, pools[i].blocks_mapped, pools[i].reserve_count);
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

    /*
     * The capacity bound, which docs/architecture.md calls a MUST NOT and
     * nothing held: removing the guard left every suite green (#240). It is
     * not a matter of taste. The mapping divides by the pool's capacity with
     * a precomputed reciprocal, and the proof that the reciprocal is exact -
     * ref/check_magic.py - only covers divisors below this number, because
     * above it the multiplication leaves u64. A pool past the bound does not
     * fail loudly: it maps subscribers to the wrong public address.
     *
     * Both sides, and the boundary itself, because a guard written `>=` by
     * mistake would refuse a pool that is exactly at the documented limit and
     * the test above would not notice.
     *
     * 4096 usable ports with block_size 1 gives 4096 blocks per address, so
     * 65536 addresses is exactly 2^28 and one more address is one block over.
     */
    {
      u32 base[1], cnt[1];
      u64 want = (u64) 1 << 28;

      base[0] = 0x0A000000u; cnt[0] = 65536;   /* 10.0.0.0/16 */
      load_pool (&p, &bpa_vec_pools[0]);
      CHECK (bpa_addr_set_build (&p.outside, base, cnt, 1) == 0,
	     "a /16 public set was refused, so the bound below is untested");
      p.block_size = 1;
      p.port_min = 1024;
      p.port_max = 1024 + 4096 - 1;
      CHECK (bpa_pool_init (&p) == 0,
	     "a pool of exactly %llu subscribers was refused, and that is the "
	     "documented limit rather than one past it",
	     (unsigned long long) want);
      CHECK (p.capacity == want,
	     "the pool at the limit reports capacity %llu, not %llu, so this "
	     "section is not measuring what it says",
	     (unsigned long long) p.capacity, (unsigned long long) want);

      cnt[0] = 65537;                          /* one address more */
      load_pool (&p, &bpa_vec_pools[0]);
      CHECK (bpa_addr_set_build (&p.outside, base, cnt, 1) == 0,
	     "a 65537 address public set was refused for another reason");
      p.block_size = 1;
      p.port_min = 1024;
      p.port_max = 1024 + 4096 - 1;
      CHECK (bpa_pool_init (&p) != 0,
	     "a pool of %llu subscribers was accepted, past the 2^28 bound "
	     "that keeps the reciprocal multiplication inside u64",
	     (unsigned long long) want + 4096);
    }
    printf ("  10 invalid configurations refused, and the capacity bound "
	    "holds on both sides of 2^28\n");
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

  printf ("\n=== the reserve: blocks held out of the formula ===\n");
  {
    unsigned k, n_reserved = 0;

    for (k = 0; k < BPA_VEC_N_RESERVE; k++)
      {
	const bpa_vec_reserve_t *v = &bpa_vec_reserve[k];
	bpa_pool_t *p = &pools[v->pool];
	bpa_block_t b = { 0 };
	u32 back = 0, got = 0;

	CHECK (bpa_reserve_addr (p, v->res, &b) == 0,
	       "'%s': reserve block %u has no address", (char *) p->name,
	       v->res);
	CHECK (b.pub_idx == v->pub_idx && b.blk_idx == v->blk_idx,
	       "'%s': reserve %u lands at (pub %u, block %u), ref says "
	       "(pub %u, block %u)",
	       (char *) p->name, v->res, b.pub_idx, b.blk_idx, v->pub_idx,
	       v->blk_idx);
	/* In the units the RFC 8158 record carries and an operator reads. A
	 * block index agreeing is not the same claim as a port range
	 * agreeing, and the port range is what the log has to attribute. */
	CHECK (b.ip_pub == v->ip_pub && b.port_lo == v->port_lo
		 && b.port_hi == v->port_hi,
	       "'%s': reserve %u is %u:%u-%u, ref says %u:%u-%u",
	       (char *) p->name, v->res, b.ip_pub, b.port_lo, b.port_hi,
	       v->ip_pub, v->port_lo, v->port_hi);

	back = bpa_reserve_index (p, v->pub_idx, v->blk_idx);
	CHECK (back == v->res,
	       "'%s': (pub %u, block %u) is reserve index %u, not %u",
	       (char *) p->name, v->pub_idx, v->blk_idx, back, v->res);
	CHECK (bpa_is_reserve_block (p, v->blk_idx),
	       "'%s': block %u is in the reserve and was not recognised",
	       (char *) p->name, v->blk_idx);

	/*
	 * What the return path does with a packet addressed to a reserve
	 * port. bpa_reverse_sub() MUST refuse it: under the blocked policy
	 * the arithmetic there would produce a real and different
	 * subscriber, and that customer would receive somebody else's
	 * traffic. Only the allocator knows whose the block is.
	 */
	CHECK (bpa_reserve_index_of (p, v->ip_pub, v->port_lo, &got) == 0
		 && got == v->res,
	       "'%s': %u:%u did not resolve to reserve index %u",
	       (char *) p->name, v->ip_pub, v->port_lo, v->res);
	CHECK (bpa_reserve_index_of (p, v->ip_pub, v->port_hi, &got) == 0
		 && got == v->res,
	       "'%s': %u:%u did not resolve to reserve index %u",
	       (char *) p->name, v->ip_pub, v->port_hi, v->res);
	CHECK (bpa_reverse_sub (p, v->ip_pub, v->port_lo, &got) != 0,
	       "'%s': %u:%u is a reserve port and bpa_reverse_sub() named "
	       "subscriber %u for it",
	       (char *) p->name, v->ip_pub, v->port_lo, got);
	CHECK (bpa_reverse_sub (p, v->ip_pub, v->port_hi, &got) != 0,
	       "'%s': %u:%u is a reserve port and bpa_reverse_sub() named "
	       "subscriber %u for it",
	       (char *) p->name, v->ip_pub, v->port_hi, got);
	n_reserved++;
      }

    /* An empty vector table would leave every assertion above unexecuted and
     * the section reading green. */
    CHECK (n_reserved > 0, "no reserve vectors at all");
    printf ("  %u reserve blocks: each one addressable, indexable both ways, "
	    "and refused by the subscriber arithmetic\n",
	    n_reserved);
  }

  printf ("\n=== no reserve block is any subscriber's ===\n");
  {
    unsigned clash = 0, checked = 0;

    /*
     * The property the whole feature rests on. An extension handed out of the
     * reserve must not be a block the formula gives to somebody else, because
     * that subscriber's mapping would break with no error anywhere - the
     * failure the deterministic proof exists to prevent.
     */
    for (i = 0; i < BPA_VEC_N_POOLS; i++)
      {
	bpa_pool_t *p = &pools[i];
	u32 sub, res;

	if (p->block_reserve == 0)
	  continue;
	for (sub = 0; sub < p->capacity; sub++)
	  {
	    u32 pub_idx, blk_idx;

	    bpa_split (p, sub, &pub_idx, &blk_idx);
	    checked++;
	    if (bpa_is_reserve_block (p, blk_idx))
	      clash++;
	  }
	/* And the other direction: every reserve block resolves to a block
	 * index the formula never produces. */
	for (res = 0; res < p->reserve_count; res++)
	  {
	    bpa_block_t b;
	    u32 sub2;

	    if (bpa_reserve_addr (p, res, &b))
	      {
		clash++;
		continue;
	      }
	    checked++;
	    if (bpa_reverse_sub (p, b.ip_pub, b.port_lo, &sub2) == 0)
	      clash++;
	  }
      }
    CHECK (clash == 0, "%u blocks are claimed by both the formula and the "
		       "reserve",
	   clash);
    CHECK (checked > 0, "no pool in the vectors carries a reserve");
    printf ("  %u block claims checked, %u conflicts\n", checked, clash);
  }

  printf ("\n=== a reserve of zero changes nothing ===\n");
  {
    unsigned inert = 0;

    /*
     * The upgrade guarantee, asserted rather than promised: a box that
     * upgrades and is not reconfigured must map every subscriber onto the
     * byte-identical address and port block it had before this feature
     * existed. Every pool below carries no reserve, so the reserve machinery
     * has to be invisible to all of them.
     */
    for (i = 0; i < BPA_VEC_N_POOLS; i++)
      {
	bpa_pool_t *p = &pools[i];
	u32 res = 0;
	int port;

	if (p->block_reserve != 0)
	  continue;
	CHECK (p->blocks_mapped == p->blocks_per_ip,
	       "'%s': no reserve, yet %u of %u blocks are unmapped",
	       (char *) p->name, p->blocks_per_ip - p->blocks_mapped,
	       p->blocks_per_ip);
	CHECK (p->reserve_count == 0, "'%s': no reserve, yet %u blocks held",
	       (char *) p->name, p->reserve_count);
	/* Including the very last port of the range, which is the one a
	 * reserve would have taken first. */
	for (port = (int) p->port_max; port > (int) p->port_max - 4; port--)
	  {
	    u32 pub0 = 0;

	    if (bpa_set_addr_of (&p->outside, 0, &pub0))
	      continue;
	    CHECK (bpa_reserve_index_of (p, pub0, (u16) port, &res) != 0,
		   "'%s': port %u was called a reserve port on a pool with no "
		   "reserve",
		   (char *) p->name, port);
	  }
	inert++;
      }
    CHECK (inert > 0, "every pool in the vectors carries a reserve, so the "
		      "default was never exercised");
    printf ("  %u pools with no reserve: nothing held, no port reserved\n",
	    inert);
  }

  printf ("\n=== what a reserve costs, by policy ===\n");
  {
    /*
     * The #23 measurement, in C, against the same pair of definitions the
     * model measures. Under interleaved the divisor is the public address
     * count, which a reserve does not touch, so every subscriber the smaller
     * pool still holds keeps the block it had: the change is a truncation of
     * the tail. Under blocked the divisor IS the mapped block count, so a
     * reserve re-divides and moves everybody past the first public address.
     *
     * Both numbers go to the operator at commit. Asserting them here is what
     * keeps the two policies from being described by one sentence.
     */
    struct
    {
      const char *before, *after;
      int expect_moved;
    } pairs[2] = { { "frag-interleaved", "reserved-interleaved", 0 },
		   { "frag-blocked", "reserved-blocked", 1 } };
    unsigned pi;

    for (pi = 0; pi < 2; pi++)
      {
	bpa_pool_t *a = 0, *b = 0;
	u32 sub, moved = 0, dropped = 0, kept = 0;

	for (i = 0; i < BPA_VEC_N_POOLS; i++)
	  {
	    if (!strcmp (bpa_vec_pools[i].name, pairs[pi].before))
	      a = &pools[i];
	    if (!strcmp (bpa_vec_pools[i].name, pairs[pi].after))
	      b = &pools[i];
	  }
	CHECK (a != 0 && b != 0, "pair %s/%s missing from the vectors",
	       pairs[pi].before, pairs[pi].after);
	if (!a || !b)
	  continue;

	for (sub = 0; sub < a->capacity && sub < a->inside.count; sub++)
	  {
	    bpa_block_t x, y;
	    u32 ip_priv;

	    if (bpa_set_addr_of (&a->inside, sub, &ip_priv)
		|| bpa_forward (a, ip_priv, &x))
	      continue;
	    if (bpa_forward (b, ip_priv, &y))
	      {
		dropped++;
		continue;
	      }
	    if (x.ip_pub != y.ip_pub || x.port_lo != y.port_lo)
	      moved++;
	    else
	      kept++;
	  }

	printf ("  %-20s -> %-20s  capacity %u -> %u, %u moved, %u dropped, "
		"%u kept\n",
		pairs[pi].before, pairs[pi].after, a->capacity, b->capacity,
		moved, dropped, kept);
	CHECK (b->capacity < a->capacity,
	       "'%s': a reserve did not reduce the capacity", (char *) b->name);
	CHECK (dropped == a->capacity - b->capacity,
	       "'%s': capacity fell by %u and %u subscribers lost their block",
	       (char *) b->name, a->capacity - b->capacity, dropped);
	if (pairs[pi].expect_moved)
	  CHECK (moved > 0,
		 "'%s': blocked policy and a reserve moved nobody; the split "
		 "divides by the mapped blocks and must",
		 (char *) b->name);
	else
	  CHECK (moved == 0,
		 "'%s': interleaved policy, yet %u retained subscribers moved "
		 "when only the reserve changed",
		 (char *) b->name, moved);
      }
  }

  printf ("\n=== a reserve that maps nobody is refused ===\n");
  {
    bpa_pool_t p;

    load_pool (&p, &bpa_vec_pools[0]);
    p.block_reserve = (u16) p.blocks_per_ip;
    CHECK (bpa_pool_init (&p) != 0,
	   "a reserve of every block was accepted, leaving a pool that maps "
	   "nobody");

    load_pool (&p, &bpa_vec_pools[0]);
    p.block_reserve = (u16) (p.blocks_per_ip - 1);
    CHECK (bpa_pool_init (&p) == 0, "the largest reserve that fits was "
				    "refused");
    CHECK (p.capacity == p.outside.count,
	   "one mapped block per address should give a capacity of %u, got %u",
	   p.outside.count, p.capacity);

    /* A dynamic pool's extension already comes off the free list, so a
     * reserve there removes blocks from the very list it is drawn from. */
    load_pool (&p, &bpa_vec_pools[0]);
    p.block_reserve = 1;
    p.mode = BPA_MODE_DYNAMIC;
    CHECK (bpa_pool_init (&p) != 0, "a reserve on a dynamic pool was accepted");
    printf ("  a reserve that leaves no mapped block, and one on a dynamic "
	    "pool, are both refused\n");
  }

  /*
   * The number an operator buys addresses against.
   *
   * Measured, and this is the defect it comes from: a three-block pool in
   * front of a /22, two workers. Subscribers at offsets 5, 6 and 9 land on
   * workers 1, 0 and 1; the slices hold two blocks and one; the third
   * subscriber is refused with a block standing free on the other worker -
   * and "show bpa pool" said "capacity 3 subscribers (usable 3)".
   *
   * Asserted here rather than only in lab/dynamic.sh because that lab needs a
   * container. Until this section existed, the central capacity claim of the
   * product was reachable by no check that runs without docker, so a
   * regression in it would have shipped with the suite green.
   */
  printf ("\n=== the capacity a pool can promise ===\n");
  {
    static const struct
    {
      u32 capacity, inside, nw, expect;
      const char *why;
    } cases[] = {
      /* the measured pool, at the worker counts it was measured at */
      { 3, 1024, 1, 3, "one slice, and it is the whole pool" },
      { 3, 1024, 2, 1, "two of three subscribers, with a block free next door" },
      { 3, 1024, 3, 1, "two of four subscribers refused" },
      /* more blocks than inside addresses: the shard costs nothing, and
       * capacity/workers would send an operator to buy what they own */
      { 12, 8, 2, 8, "no worker can be handed more subscribers than it has "
		     "blocks for" },
      { 12, 8, 3, 8, "the same at three" },
      { 12, 8, 8, 8, "and at one worker per block and a half" },
      /* the sizing table in the operator documentation */
      { 12, 1048576, 2, 6, "wiki: 12 blocks, 2 workers" },
      { 48, 1048576, 4, 12, "wiki: 48 blocks, 4 workers" },
      { 192, 1048576, 4, 48, "wiki: 192 blocks, 4 workers" },
      { 768, 1048576, 8, 96, "wiki: 768 blocks, 8 workers" },
      { 7936, 1048576, 8, 992, "wiki: 7936 blocks, 8 workers" },
      { 12, 1048576, 8, 1, "wiki: a small pool on many workers guarantees one" },
    };
    unsigned c;

    for (c = 0; c < sizeof (cases) / sizeof (cases[0]); c++)
      {
	u32 got = floor_of (cases[c].capacity, cases[c].inside, cases[c].nw);

	CHECK (got == cases[c].expect,
	       "%u blocks, %u inside addresses, %u worker(s): promised %u, "
	       "should be %u - %s",
	       cases[c].capacity, cases[c].inside, cases[c].nw, got,
	       cases[c].expect, cases[c].why);
      }
    printf ("  %u sized cases, the measured pool and the documented table "
	    "among them\n",
	    (unsigned) (sizeof (cases) / sizeof (cases[0])));
  }

  /*
   * Where it must NOT appear. A block is derived rather than allocated in
   * deterministic mode, so there is no slice to run out of, and a single
   * worker owns every block. A caveat printed where it does not apply is a
   * caveat people learn to skip - which is how the one that matters gets
   * skipped too - so the two modes that lose nothing must come back equal to
   * the ceiling, which is what makes every reader print nothing.
   */
  printf ("\n=== and nothing to say where there is no shard ===\n");
  for (i = 0; i < BPA_VEC_N_POOLS; i++)
    {
      bpa_pool_t *p = &pools[i];
      u32 usable = p->capacity < p->inside.count ? p->capacity
						 : p->inside.count;
      u32 nw;

      CHECK (bpa_pool_usable (p) == usable, "'%s': usable %u, expected %u",
	     (char *) p->name, bpa_pool_usable (p), usable);

      p->mode = BPA_MODE_DYNAMIC;
      CHECK (bpa_pool_guaranteed (p, 1) == usable,
	     "'%s': one worker promised %u of %u; there is one slice and it "
	     "is the whole pool",
	     (char *) p->name, bpa_pool_guaranteed (p, 1), usable);
      /* Zero is what bpa_main.n_workers reads on a single-threaded box, and
       * it reaches here from the API handler on every such box. */
      CHECK (bpa_pool_guaranteed (p, 0) == usable,
	     "'%s': a box with no worker threads promised %u of %u",
	     (char *) p->name, bpa_pool_guaranteed (p, 0), usable);

      p->mode = BPA_MODE_DETERMINISTIC;
      for (nw = 1; nw <= 8; nw++)
	CHECK (bpa_pool_guaranteed (p, nw) == usable,
	       "'%s': deterministic at %u worker(s) promised %u of %u; "
	       "its blocks are a formula and cannot run out",
	       (char *) p->name, nw, bpa_pool_guaranteed (p, nw), usable);
      p->mode = BPA_MODE_DYNAMIC;
      for (nw = 2; nw <= 8; nw++)
	{
	  /* The floor is a floor. A figure above the ceiling is the #210
	   * over-claim wearing the other number's clothes, and on a pool with
	   * far more blocks than inside addresses - which is what a pool bought
	   * for growth looks like on the day it goes in - capacity/workers is
	   * above it. */
	  CHECK (bpa_pool_guaranteed (p, nw) <= usable,
		 "'%s': %u worker(s) promised %u of a ceiling of %u",
		 (char *) p->name, nw, bpa_pool_guaranteed (p, nw), usable);
	}
      printf ("  %-18s cap=%-6u usable=%-6u dynamic floor at 2/4/8 workers: "
	      "%u / %u / %u\n",
	      (char *) p->name, p->capacity, usable,
	      bpa_pool_guaranteed (p, 2), bpa_pool_guaranteed (p, 4),
	      bpa_pool_guaranteed (p, 8));
      p->mode = BPA_MODE_DETERMINISTIC;
    }

  /*
   * And the claim against the mechanism, which is what #210 asks for: every
   * subscriber the pool claims capacity for gets a block, and one more than
   * that can be refused. Swept over every small pool rather than argued,
   * because a floor that is merely never exceeded is not a floor - it also
   * has to be the LARGEST one, or the box is understating and the operator
   * buys addresses they already own.
   */
  printf ("\n=== the promise holds against the shard, and is not loose ===\n");
  {
    u32 capacity, inside, nw;
    u32 swept = 0, tight = 0;

    for (nw = 1; nw <= 4; nw++)
      for (capacity = 1; capacity <= 12; capacity++)
	for (inside = 1; inside <= 14; inside++)
	  {
	    u32 usable = capacity < inside ? capacity : inside;
	    u32 g = floor_of (capacity, inside, nw);

	    swept++;
	    CHECK (g == simulated_floor (capacity, inside, nw),
		   "%u blocks, %u inside, %u worker(s): promised %u, the "
		   "shard serves %u whatever the addresses are",
		   capacity, inside, nw, g,
		   simulated_floor (capacity, inside, nw));
	    CHECK (g <= usable,
		   "%u blocks, %u inside, %u worker(s): promised %u of a "
		   "ceiling of %u",
		   capacity, inside, nw, g, usable);
	    CHECK (!refusable (capacity, inside, nw, 0, g, 0),
		   "%u blocks, %u inside, %u worker(s): %u were claimed and "
		   "one of them can be refused",
		   capacity, inside, nw, g);
	    if (g < usable)
	      {
		tight++;
		CHECK (refusable (capacity, inside, nw, 0, g + 1, 0),
		       "%u blocks, %u inside, %u worker(s): %u claimed, yet "
		       "%u are always served - the claim is understated",
		       capacity, inside, nw, g, g + 1);
	      }
	  }
    printf ("  %u pools swept, %u of them losing something to the shard\n",
	    swept, tight);
  }

  return bpa_test_verdict (checks, failures);
}
