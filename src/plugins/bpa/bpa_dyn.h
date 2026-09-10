/*
 * bpa_dyn.h - dynamic (PBA) block allocator.
 *
 * In deterministic mode a subscriber's block is a formula. In dynamic mode
 * the block is allocated on the first flow and released after the subscriber
 * goes idle, which lets a provider serve more subscribers than
 * blocks-times-addresses would allow. The cost is that every allocation and
 * release is an event that must be logged (RFC 8158 natEvent 16 and 17).
 *
 * Sharding: block allocation would be shared state across workers, which
 * would reintroduce the lock the rest of the design avoids. Instead the
 * block space is partitioned: worker w owns every block_idx where
 * block_idx % n_workers == w, so allocation is worker-local.
 *
 * That partition is what makes the handoff work unchanged in both modes:
 *
 *   in2out  worker = sub_idx   % n_workers
 *   out2in  worker = block_idx % n_workers
 *
 * A worker only ever hands a subscriber a block from its own slice, so the
 * two agree. And in deterministic mode block_idx == sub_idx, so the same
 * line of code serves both.
 */

#ifndef __included_bpa_dyn_h__
#define __included_bpa_dyn_h__

#include <vlib/vlib.h>

#include "bpa_pool.h"

/*
 * How long a block stays reserved after its last session ends.
 *
 * This is not tidiness: without hold-down, a subscriber that goes quiet for
 * a second and comes back churns a release and an allocation, and every one
 * of those is a log record. Hold-down is the main lever on log volume.
 */
#define BPA_BLOCK_HOLDDOWN 120

#define BPA_NO_BLOCK ((u32) ~0)

/* A subscriber's extension block: which local block, and whose it is. */
typedef struct
{
  u32 sub_idx;
  u32 local;
} bpa_dyn_extra_t;

typedef struct
{
  u32 *owner;	    /* local idx -> sub_idx, BPA_NO_BLOCK when free */
  f64 *idle_since;  /* local idx -> when it went idle; 0 while in use */
  u32 *free_list;   /* stack of free local idx */
  uword *by_sub;    /* sub_idx -> local idx + 1, so 0 means "none" */
  u32 n_local;	    /* blocks in this worker's slice */
  u32 n_alloc;	    /* currently held */
  u64 n_allocations;
  u64 n_extensions;   /* blocks handed out as extensions *//* lifetime; becomes natEvent 16 count */
  u64 n_releases;   /* lifetime; becomes natEvent 17 count */
  u64 n_exhausted;  /* allocation refused; becomes natEvent 12 */
  u8 active;
  /*
   * Extension blocks: (subscriber, local block) pairs beyond the one in
   * by_sub. Kept as a list rather than widening by_sub because extensions are
   * rare - only subscribers that filled a block have one - and because
   * releasing them has to be driven from the same reap that releases primary
   * blocks, which scans owner[] anyway.
   *
   * Without this, an extension would be owned but never marked idle, and
   * never released: a leak that only shows up as a pool that slowly stops
   * having free blocks.
   */
  bpa_dyn_extra_t *extras;
} bpa_dyn_t;

clib_error_t *bpa_dyn_init (bpa_dyn_t *d, const bpa_pool_t *p, u32 worker,
			    u32 n_workers);
void bpa_dyn_free (bpa_dyn_t *d);

/* A block returned to the pool, so the caller can log natEvent 17. */
typedef struct
{
  u32 sub_idx;
  u32 block_idx;
} bpa_dyn_release_t;

/*
 * Block currently held by this subscriber, allocating one if needed.
 * Returns 0 on success, -1 when the slice is exhausted.
 * *is_new is set when a block was actually allocated, which is the moment
 * that has to be logged as RFC 8158 natEvent 16.
 */
int bpa_dyn_acquire (bpa_dyn_t *d, const bpa_pool_t *p, u32 sub_idx,
		     u32 worker, u32 n_workers, bpa_block_t *b, int *is_new);

/*
 * Another block for a subscriber that already has one. Separate from
 * bpa_dyn_acquire because that one returns the existing block when the
 * subscriber is known, which is exactly the wrong answer here.
 */
int bpa_dyn_acquire_extra (bpa_dyn_t *d, const bpa_pool_t *p, u32 sub_idx,
			   u32 worker, u32 n_workers, bpa_block_t *b,
			   int *is_new);

/* Subscriber's last session ended: start the hold-down. */
void bpa_dyn_idle (bpa_dyn_t *d, u32 sub_idx, f64 now);

/* Subscriber became active again before hold-down expired. */
void bpa_dyn_active (bpa_dyn_t *d, u32 sub_idx);

/*
 * Release blocks idle for longer than holddown. Fills `out` with what was
 * released so the caller can emit natEvent 17, and returns how many.
 */
u32 bpa_dyn_reap (bpa_dyn_t *d, f64 now, u32 holddown, u32 worker,
		  u32 n_workers, bpa_dyn_release_t *out, u32 max_out);

/* block_idx -> (public address, port range). */
static_always_inline void
bpa_dyn_block_addr (const bpa_pool_t *p, u32 block_idx, bpa_block_t *b)
{
  bpa_split (p, block_idx, &b->pub_idx, &b->blk_idx);
  bpa_set_addr_of (&p->outside, b->pub_idx, &b->ip_pub);
  b->port_lo = p->port_min + b->blk_idx * p->block_size;
  b->port_hi = b->port_lo + p->block_size - 1;
}

/* Which subscriber currently holds this block. -1 when it is free. */
static_always_inline int
bpa_dyn_owner (const bpa_dyn_t *d, u32 block_idx, u32 n_workers, u32 *sub_idx)
{
  u32 local = block_idx / n_workers;

  if (PREDICT_FALSE (!d->active || local >= d->n_local))
    return -1;
  if (PREDICT_FALSE (d->owner[local] == BPA_NO_BLOCK))
    return -1;

  *sub_idx = d->owner[local];
  return 0;
}

/*
 * The mirror: which block this subscriber holds. -1 when it holds none.
 *
 * Asked by "show bpa subscriber", because the per-subscriber session record
 * cannot answer it: that record dies with the subscriber's last session while
 * the block stays reserved here for the whole hold-down. Only the primary
 * block, which is the one whose absence means the subscriber holds nothing.
 */
static_always_inline int
bpa_dyn_block_of (bpa_dyn_t *d, u32 sub_idx, u32 worker, u32 n_workers,
		  u32 *block_idx)
{
  uword *q;

  if (PREDICT_FALSE (!d->active))
    return -1;
  q = hash_get (d->by_sub, sub_idx);
  if (!q)
    return -1;

  *block_idx = (u32) (q[0] - 1) * n_workers + worker;
  return 0;
}

#endif /* __included_bpa_dyn_h__ */
