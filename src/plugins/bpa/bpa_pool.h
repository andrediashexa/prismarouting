/*
 * bpa_pool.h - BPA (Bloco de Portas Alocadas) address/port mapping.
 *
 * Reference implementation and test vectors live in ref/bpa_model.py.
 * Any change here must keep ref/validate.py and `make test` passing.
 *
 * Address sets are built from possibly non-contiguous prefixes: no provider
 * holds its IPv4 in one aligned block, so assuming a single prefix would
 * rule out most deployments. Everything downstream works on a dense index.
 */

#ifndef __included_bpa_pool_h__
#define __included_bpa_pool_h__

/* The mapping core is pure arithmetic, so it builds either inside VPP or
 * standalone against a small shim. Standalone is what the unit test uses:
 * it keeps the algorithm testable in CI without a VPP tree. */
#ifdef BPA_STANDALONE
#include "bpa_shim.h"
#else
#include <vlib/vlib.h>
#include <vnet/ip/ip4_packet.h>
#endif

/* Ranges are inlined in the pool, so no allocation sits on the datapath.
 * A provider with more than this many separate prefixes is rejected with a
 * clear error rather than silently truncated. */
#define BPA_MAX_RANGES 256

typedef enum
{
  BPA_POLICY_INTERLEAVED = 0, /* neighbours spread across public IPs (default) */
  BPA_POLICY_BLOCKED,	      /* neighbours grouped on the same public IP */
} bpa_policy_t;

typedef enum
{
  BPA_MODE_DETERMINISTIC = 0,
  BPA_MODE_DYNAMIC,
} bpa_mode_t;

/*
 * Precomputed reciprocal, so the datapath never issues a hardware divide.
 * Used for the pool policy split and for spreading subscribers over workers.
 * ref/check_magic.py proves it exact for every divisor below BPA_MAX_CAPACITY.
 */
typedef struct
{
  u32 d;
  u64 magic;
  u8 shift;
  u8 log2;
  u8 is_pow2;
} bpa_divisor_t;

void bpa_divisor_init (bpa_divisor_t *dv, u32 d);

static_always_inline u32
bpa_divide (const bpa_divisor_t *dv, u32 n)
{
  if (PREDICT_TRUE (dv->is_pow2))
    return n >> dv->log2;
  return (u32) (((u64) n * dv->magic) >> dv->shift);
}

static_always_inline u32
bpa_modulo (const bpa_divisor_t *dv, u32 n)
{
  return n - bpa_divide (dv, n) * dv->d;
}

typedef struct
{
  u32 base;	 /* first address, host order */
  u32 count;	 /* addresses in this range */
  u32 first_idx; /* dense index of `base` within the set */
} bpa_range_t;

/*
 * A set of addresses presented as a dense index space [0, count).
 * Ranges are sorted by base, which - because first_idx is assigned in that
 * same order - makes them sorted by first_idx too. One binary search serves
 * both directions.
 */
typedef struct
{
  bpa_range_t ranges[BPA_MAX_RANGES];
  u32 n_ranges;
  u32 count;
} bpa_addr_set_t;

typedef struct
{
  u8 *name;

  bpa_addr_set_t inside;
  bpa_addr_set_t outside;

  u16 port_min;
  u16 port_max;
  u16 block_size; /* power of two */
  bpa_policy_t policy;
  bpa_mode_t mode;

  /* derived */
  u32 port_count;
  u32 blocks_per_ip;
  u32 capacity; /* outside.count * blocks_per_ip */

  u8 log2_block; /* block_size is a power of two */

  /*
   * Divisor for the policy split:
   *   INTERLEAVED -> outside.count   (blk_idx = sub / d, pub_idx = sub % d)
   *   BLOCKED     -> blocks_per_ip   (pub_idx = sub / d, blk_idx = sub % d)
   */
  bpa_divisor_t div;
} bpa_pool_t;

typedef struct
{
  u32 sub_idx;
  u32 pub_idx;
  u32 blk_idx;
  u32 ip_pub; /* host order */
  u16 port_lo;
  u16 port_hi;
} bpa_block_t;

clib_error_t *bpa_pool_init (bpa_pool_t *p);
clib_error_t *bpa_addr_set_build (bpa_addr_set_t *s, const u32 *bases,
				  const u32 *counts, u32 n);

/* ------------------------------------------------------------------ */
/* address set lookups                                                */

/* Dense index of `addr`, or -1 when it is not in the set. */
static_always_inline int
bpa_set_index_of (const bpa_addr_set_t *s, u32 addr, u32 *idx)
{
  u32 lo = 0, hi;

  if (PREDICT_FALSE (s->n_ranges == 0))
    return -1;
  hi = s->n_ranges - 1;

  /* the common deployment has a single contiguous range */
  if (PREDICT_TRUE (s->n_ranges == 1))
    {
      u32 off = addr - s->ranges[0].base;
      if (PREDICT_FALSE (off >= s->ranges[0].count))
	return -1;
      *idx = off;
      return 0;
    }

  while (lo <= hi)
    {
      u32 mid = lo + (hi - lo) / 2;
      const bpa_range_t *r = &s->ranges[mid];

      if (addr < r->base)
	{
	  if (mid == 0)
	    break;
	  hi = mid - 1;
	}
      else if (addr - r->base >= r->count)
	lo = mid + 1;
      else
	{
	  *idx = r->first_idx + (addr - r->base);
	  return 0;
	}
    }
  return -1;
}

/* Address at dense index `idx`, or -1 when out of range. */
static_always_inline int
bpa_set_addr_of (const bpa_addr_set_t *s, u32 idx, u32 *addr)
{
  u32 lo = 0, hi = s->n_ranges - 1;

  if (PREDICT_FALSE (idx >= s->count))
    return -1;

  if (PREDICT_TRUE (s->n_ranges == 1))
    {
      *addr = s->ranges[0].base + idx;
      return 0;
    }

  while (lo <= hi)
    {
      u32 mid = lo + (hi - lo) / 2;
      const bpa_range_t *r = &s->ranges[mid];

      if (idx < r->first_idx)
	{
	  if (mid == 0)
	    break;
	  hi = mid - 1;
	}
      else if (idx - r->first_idx >= r->count)
	lo = mid + 1;
      else
	{
	  *addr = r->base + (idx - r->first_idx);
	  return 0;
	}
    }
  return -1;
}

/* ------------------------------------------------------------------ */
/* policy split                                                        */

static_always_inline void
bpa_split (const bpa_pool_t *p, u32 sub_idx, u32 *pub_idx, u32 *blk_idx)
{
  u32 q = bpa_divide (&p->div, sub_idx);
  u32 r = sub_idx - q * p->div.d;

  if (PREDICT_TRUE (p->policy == BPA_POLICY_INTERLEAVED))
    {
      *blk_idx = q; /* divisor is outside.count */
      *pub_idx = r;
    }
  else
    {
      *pub_idx = q; /* divisor is blocks_per_ip */
      *blk_idx = r;
    }
}

static_always_inline u32
bpa_join (const bpa_pool_t *p, u32 pub_idx, u32 blk_idx)
{
  if (PREDICT_TRUE (p->policy == BPA_POLICY_INTERLEAVED))
    return blk_idx * p->div.d + pub_idx;
  return pub_idx * p->div.d + blk_idx;
}

/* ------------------------------------------------------------------ */
/* translation                                                         */

/* inside -> outside. Returns 0 on success. */
static_always_inline int
bpa_forward (const bpa_pool_t *p, u32 ip_priv, bpa_block_t *b)
{
  u32 sub_idx;

  if (PREDICT_FALSE (bpa_set_index_of (&p->inside, ip_priv, &sub_idx)))
    return -1;
  if (PREDICT_FALSE (sub_idx >= p->capacity))
    return -1;

  bpa_split (p, sub_idx, &b->pub_idx, &b->blk_idx);

  if (PREDICT_FALSE (bpa_set_addr_of (&p->outside, b->pub_idx, &b->ip_pub)))
    return -1;

  b->sub_idx = sub_idx;
  b->port_lo = p->port_min + b->blk_idx * p->block_size;
  b->port_hi = b->port_lo + p->block_size - 1;
  return 0;
}

/*
 * outside -> inside, stopping at the dense subscriber index.
 * The handoff node needs only this: resolving the index to an address
 * would cost a second binary search it has no use for.
 */
static_always_inline int
bpa_reverse_sub (const bpa_pool_t *p, u32 ip_pub, u16 port, u32 *sub_idx)
{
  u32 pub_idx, blk_idx;

  if (PREDICT_FALSE (bpa_set_index_of (&p->outside, ip_pub, &pub_idx)))
    return -1;
  if (PREDICT_FALSE (port < p->port_min || port > p->port_max))
    return -1;

  blk_idx = (u32) (port - p->port_min) >> p->log2_block;
  if (PREDICT_FALSE (blk_idx >= p->blocks_per_ip))
    return -1;

  *sub_idx = bpa_join (p, pub_idx, blk_idx);
  return 0;
}

/*
 * outside -> inside. Arithmetic plus two binary searches over small arrays,
 * with no session lookup at all. This is why the deterministic mode needs
 * no allocation logging and why the return path shards cleanly.
 */
static_always_inline int
bpa_reverse (const bpa_pool_t *p, u32 ip_pub, u16 port, u32 *ip_priv)
{
  u32 sub_idx;

  if (PREDICT_FALSE (bpa_reverse_sub (p, ip_pub, port, &sub_idx)))
    return -1;

  return bpa_set_addr_of (&p->inside, sub_idx, ip_priv);
}

/*
 * Worker that owns a subscriber. Both directions must agree on this,
 * otherwise the per-subscriber port bitmap becomes shared state.
 *
 * The worker count is whatever the operator configured, so this uses the
 * same reciprocal as the pool split rather than requiring a power of two.
 */
static_always_inline u32
bpa_owner_worker (const bpa_divisor_t *workers, u32 sub_idx)
{
  return bpa_modulo (workers, sub_idx);
}

#endif /* __included_bpa_pool_h__ */
