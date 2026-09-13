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
  /*
   * local idx -> the configuration says this block is one subscriber's.
   *
   * Parallel to owner[] rather than a flag inside it because the two answer
   * different questions: owner[] says who is holding the block now, pinned[]
   * says why it may not be taken away. A block can be pinned and idle at the
   * same time - that is the whole point of the pin - and a sentinel owner
   * would have had to encode both in one word.
   */
  u8 *pinned;
  u32 *free_list;   /* stack of free local idx */
  uword *by_sub;    /* sub_idx -> local idx + 1, so 0 means "none" */
  u32 n_local;	    /* blocks in this worker's slice */
  u32 n_alloc;	    /* currently held */
  u32 n_pinned;	    /* of those, held because a forward says so */
  u64 n_allocations;
  u64 n_extensions;   /* blocks handed out as extensions *//* lifetime; becomes natEvent 16 count */
  /*
   * Extensions the pool's stride placed, and extensions it could not.
   *
   * Two counters because a stride that is configured and never honoured looks
   * from outside exactly like no stride at all - the subscriber still gets a
   * block and still translates - and the operator would be reading a setting
   * the box is not applying. A miss is not a failure: the block the stride
   * named was taken by somebody else, or the subscriber sits at the end of
   * its public address, and falling back to the free list is what keeps the
   * customer working. It is only a failure when it is every one of them.
   */
  u64 n_stride_placed;
  u64 n_stride_missed;
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
  /*
   * This slice indexes the pool's RESERVE, not the blocks the formula hands
   * out.
   *
   * A deterministic pool has no free list - every block is somebody's by
   * arithmetic - so the only blocks it can give away are the ones its reserve
   * holds out of the formula. Those live in an index space of their own
   * (bpa_reserve_index), and the ONLY difference between allocating from it
   * and allocating a dynamic block is how an index becomes an address and a
   * port range. Everything else this allocator does - the free stack, the
   * hold-down, the extras list, the reap, the counters that become natEvent
   * 16 and 17 - is the same work for the same reason, so it is the same code
   * with a flag rather than a second copy that has to be kept in step.
   */
  u8 reserve;
} bpa_dyn_t;

/*
 * `reserve` chooses which index space this slice covers: the pool's reserve
 * (deterministic mode) or its whole block range (dynamic mode).
 */
clib_error_t *bpa_dyn_init (bpa_dyn_t *d, const bpa_pool_t *p, u32 worker,
			    u32 n_workers, int reserve);
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
 *
 * `primary` is the block the subscriber started with and `nth` is how many it
 * already holds, which together are what the pool's stride is measured from:
 * the nth block sits nth*stride blocks along the primary's own public
 * address. Both are ignored when no stride is configured, which is the
 * default and today's behaviour - whatever the free list hands out next.
 *
 * A stride the allocator cannot honour - the block is taken, or it is not in
 * this worker's slice - falls back to the free list and is counted. Refusing
 * the extension instead would turn a preference about layout into a customer
 * losing traffic.
 */
int bpa_dyn_acquire_extra (bpa_dyn_t *d, const bpa_pool_t *p, u32 sub_idx,
			   u32 worker, u32 n_workers, u32 primary, u32 nth,
			   bpa_block_t *b, int *is_new);

/*
 * Holds one block for one subscriber until the configuration says otherwise.
 *
 * This is what makes a static port forward possible on a dynamic pool: the
 * unit an operator sells is an inbound port, and the unit this allocator moves
 * is a block, so the block is what has to stop moving. A pinned block is never
 * handed to anybody else and never reaped, whatever the hold-down says.
 *
 * Returns 0 when the block is this subscriber's afterwards, -1 when another
 * subscriber is holding it right now - its index goes in *held_by, because
 * "refused" is only useful to an operator who is told whose sessions the pin
 * would have broken - and -2 when the block is not in this worker's slice,
 * which is a caller that resolved the wrong worker rather than anything an
 * operator can act on.
 *
 * *is_new is set only when the block came off the free list, which is the
 * moment that is an RFC 8158 natEvent 16. Pinning a block the subscriber is
 * already holding allocates nothing, and a second allocation record with no
 * release between them is exactly the gap docs/logging.md exists to close.
 */
int bpa_dyn_pin (bpa_dyn_t *d, u32 block_idx, u32 sub_idx, u32 n_workers,
		 f64 now, u32 *held_by, int *is_new);

/*
 * The mirror: the last forward in the block is gone, so the block is an
 * ordinary dynamic block again.
 *
 * *released is set when the block went straight back to the free list, which
 * is a natEvent 17 the caller has to emit. That happens when nothing is using
 * it - the pin was the only reason it was held - and does not when the
 * subscriber still has sessions on it, which then leave by the hold-down like
 * any other block.
 */
void bpa_dyn_unpin (bpa_dyn_t *d, u32 block_idx, u32 n_workers, int *released);

/* Is this block held by configuration rather than by traffic? */
static_always_inline int
bpa_dyn_is_pinned (const bpa_dyn_t *d, u32 block_idx, u32 n_workers)
{
  u32 local = block_idx / n_workers;

  if (PREDICT_FALSE (!d->active || local >= d->n_local))
    return 0;
  return d->pinned[local] != 0;
}

/* Subscriber's last session ended: start the hold-down. */
void bpa_dyn_idle (bpa_dyn_t *d, u32 sub_idx, f64 now);

/*
 * There was a bpa_dyn_active() here - declared, defined, documented
 * "subscriber became active again before hold-down expired", and called by
 * nothing. #216 asked whether that was a missing call or a dead function. It
 * is neither: it would have been WRONG to call, and it is gone.
 *
 * What it did was clear the idle stamp on every block a subscriber holds,
 * extensions included. bpa_dyn_idle() sets those stamps at one moment only -
 * when the subscriber's session count reaches ZERO - and at that moment every
 * block it holds is empty, extension and primary alike. A subscriber that
 * comes back with one session needs one block, so its extension SHOULD go
 * back to the pool, and it does, because bpa_dyn_acquire() clears the stamp on
 * the primary alone.
 *
 * Wiring bpa_dyn_active() in would have kept the empty extension owned - the
 * leak the comment on bpa_dyn_idle() exists to warn about, arriving through
 * the function that looked like its counterpart.
 *
 * Measured rather than argued: lab/extblocks.sh takes a subscriber to two
 * blocks, lets it go quiet, brings it back with ONE flow and waits out the
 * hold-down. It ends holding one block, its own, still translating. With
 * bpa_dyn_active() called from bpa_dyn_acquire() it ends holding two.
 */

/*
 * Release blocks idle for longer than holddown. Fills `out` with what was
 * released so the caller can emit natEvent 17, and returns how many.
 *
 * A pinned block is never among them, however long it has been idle. That is
 * the half of the pin that the operator actually sold: the forwarded port has
 * to still be the subscriber's after a night with no traffic, and the
 * hold-down is measured in minutes.
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

/*
 * The same, for whichever index space this slice covers. Returns 0 on
 * success.
 *
 * The one place the two allocators differ. Everything else about a held block
 * - who has it, when it went idle, when it goes back - is the same question
 * whether the block came off a free list or out of a reserve, and the record
 * they produce is the same record.
 */
static_always_inline int
bpa_dyn_addr (const bpa_dyn_t *d, const bpa_pool_t *p, u32 idx,
	      bpa_block_t *b)
{
  if (PREDICT_FALSE (d->reserve != 0))
    return bpa_reserve_addr (p, idx, b);
  bpa_dyn_block_addr (p, idx, b);
  return 0;
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
