/*
 * bpa_pool.h - BPA (Bloco de Portas Alocadas) address/port mapping.
 *
 * Reference implementation and test vectors live in ref/bpa_model.py.
 * Any change here must keep ref/validate.py passing.
 */

#ifndef __included_bpa_pool_h__
#define __included_bpa_pool_h__

#include <vlib/vlib.h>
#include <vnet/ip/ip4_packet.h>

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

typedef struct
{
  u8 *name;

  /* configuration */
  u32 inside_base;  /* host order */
  u32 outside_base; /* host order */
  u32 inside_count;
  u32 outside_count; /* always a power of two: it is a prefix */
  u16 port_min;
  u16 port_max;
  u16 block_size; /* power of two */
  bpa_policy_t policy;
  bpa_mode_t mode;

  /* derived */
  u32 port_count;
  u32 blocks_per_ip;
  u32 capacity; /* outside_count * blocks_per_ip */

  /* precomputed shifts; valid because outside_count and block_size
   * are both powers of two. This is what keeps the datapath free of
   * integer division in the INTERLEAVED policy. */
  u8 log2_outside;
  u8 log2_block;
  u32 outside_mask; /* outside_count - 1 */

  /* reciprocal for BLOCKED policy, where blocks_per_ip is not a power
   * of two (e.g. 126, 63). Avoids a hardware divide in the datapath. */
  u64 bpi_magic;
  u8 bpi_shift;
} bpa_pool_t;

/* Result of an inside->outside lookup. */
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

/* Fast unsigned divide by pool->blocks_per_ip without a hardware divide. */
static_always_inline u32
bpa_div_bpi (const bpa_pool_t *p, u32 n)
{
  return (u32) (((u64) n * p->bpi_magic) >> p->bpi_shift);
}

/*
 * inside -> outside. Pure arithmetic, no state, no memory access.
 * Returns 0 on success, non-zero if the subscriber is outside the pool.
 */
static_always_inline int
bpa_forward (const bpa_pool_t *p, u32 ip_priv, bpa_block_t *b)
{
  u32 sub_idx = ip_priv - p->inside_base;

  if (PREDICT_FALSE (sub_idx >= p->inside_count || sub_idx >= p->capacity))
    return -1;

  if (PREDICT_TRUE (p->policy == BPA_POLICY_INTERLEAVED))
    {
      /* shift-only: outside_count is a power of two */
      b->pub_idx = sub_idx & p->outside_mask;
      b->blk_idx = sub_idx >> p->log2_outside;
    }
  else
    {
      b->pub_idx = bpa_div_bpi (p, sub_idx);
      b->blk_idx = sub_idx - b->pub_idx * p->blocks_per_ip;
    }

  b->sub_idx = sub_idx;
  b->ip_pub = p->outside_base + b->pub_idx;
  b->port_lo = p->port_min + b->blk_idx * p->block_size;
  b->port_hi = b->port_lo + p->block_size - 1;
  return 0;
}

/*
 * outside -> inside. Also pure arithmetic: this is why the deterministic
 * mode needs no session lookup on the return path, and why it can run
 * with zero allocation logging.
 *
 * Returns 0 on success and writes the private address (host order).
 */
static_always_inline int
bpa_reverse (const bpa_pool_t *p, u32 ip_pub, u16 port, u32 *ip_priv)
{
  u32 pub_idx = ip_pub - p->outside_base;
  u32 blk_idx, sub_idx;

  if (PREDICT_FALSE (pub_idx >= p->outside_count))
    return -1;
  if (PREDICT_FALSE (port < p->port_min || port > p->port_max))
    return -1;

  blk_idx = (u32) (port - p->port_min) >> p->log2_block;
  if (PREDICT_FALSE (blk_idx >= p->blocks_per_ip))
    return -1;

  if (PREDICT_TRUE (p->policy == BPA_POLICY_INTERLEAVED))
    sub_idx = (blk_idx << p->log2_outside) | pub_idx;
  else
    sub_idx = pub_idx * p->blocks_per_ip + blk_idx;

  if (PREDICT_FALSE (sub_idx >= p->inside_count))
    return -1;

  *ip_priv = p->inside_base + sub_idx;
  return 0;
}

/*
 * Worker that owns a subscriber. Both directions must agree on this,
 * otherwise the per-subscriber port bitmap becomes shared state.
 */
static_always_inline u32
bpa_owner_thread (u32 sub_idx, u32 n_workers)
{
  return sub_idx & (n_workers - 1); /* n_workers forced to a power of two */
}

#endif /* __included_bpa_pool_h__ */
