/* bpa_dyn.c - dynamic (PBA) block allocator. */

#include "bpa_dyn.h"

/* block_idx <-> (pub_idx, blk_idx) reuses the pool policy split, so a
 * dynamic pool still spreads consecutive allocations across public
 * addresses when the policy is interleaved. Ver bpa_dyn_block_addr(). */

static void bpa_dyn_unfree (bpa_dyn_t *d, u32 local);

clib_error_t *
bpa_dyn_init (bpa_dyn_t *d, const bpa_pool_t *p, u32 worker, u32 n_workers,
	      int reserve)
{
  u32 i, n, total = reserve ? p->reserve_count : p->capacity;

  clib_memset (d, 0, sizeof (*d));
  d->reserve = reserve ? 1 : 0;

  /* indices in [0, total) whose value is congruent to worker */
  n = total / n_workers;
  if (worker < total % n_workers)
    n++;

  /*
   * A dynamic pool whose capacity does not reach this worker cannot translate
   * for the subscribers that hash to it at all, so it is refused.
   *
   * A RESERVE that does not reach this worker is refused nowhere, and that is
   * a decision rather than an omission. The worker count is a startup setting,
   * not part of the pool: refusing here would mean a configuration accepted on
   * a single-threaded box stops the pool from coming up after the same box is
   * given four workers - every subscriber on it losing traffic, to enforce a
   * property about extensions. So the slice is created empty instead. Every
   * extension it is asked for is refused and counted, which is what
   * "show bpa blocks" reports and what bpa_reserve_warn() says at creation
   * time. Same argument as bpa_stride_warn(), same conclusion.
   */
  if (n == 0 && !reserve)
    return clib_error_return (
      0, "pool '%v': capacity %u does not reach worker %u; reduce the worker "
	 "count or enlarge the pool",
      p->name, total, worker);

  d->n_local = n;
  if (n == 0)
    {
      d->by_sub = hash_create (0, sizeof (uword));
      d->active = 1;
      return 0;
    }

  vec_validate (d->owner, n - 1);
  vec_validate (d->idle_since, n - 1);
  vec_validate (d->pinned, n - 1);
  for (i = 0; i < n; i++)
    {
      d->owner[i] = BPA_NO_BLOCK;
      d->idle_since[i] = 0;
      d->pinned[i] = 0;
    }

  /* pushed high to low so popping hands out increasing block indices */
  vec_validate (d->free_list, n - 1);
  for (i = 0; i < n; i++)
    d->free_list[i] = n - 1 - i;
  vec_set_len (d->free_list, n);

  d->by_sub = hash_create (0, sizeof (uword));
  d->active = 1;
  return 0;
}

void
bpa_dyn_free (bpa_dyn_t *d)
{
  vec_free (d->owner);
  vec_free (d->idle_since);
  vec_free (d->pinned);
  vec_free (d->free_list);
  vec_free (d->extras);
  hash_free (d->by_sub);
  clib_memset (d, 0, sizeof (*d));
}

int
bpa_dyn_acquire (bpa_dyn_t *d, const bpa_pool_t *p, u32 sub_idx, u32 worker,
		 u32 n_workers, bpa_block_t *b, int *is_new)
{
  u32 local, block_idx;
  uword *q;

  *is_new = 0;
  q = hash_get (d->by_sub, sub_idx);
  if (PREDICT_TRUE (q != 0))
    {
      local = (u32) (q[0] - 1);
      d->idle_since[local] = 0; /* still in use */
      goto found;
    }

  if (PREDICT_FALSE (vec_len (d->free_list) == 0))
    {
      d->n_exhausted++;
      return -1;
    }

  local = vec_pop (d->free_list);
  d->owner[local] = sub_idx;
  d->idle_since[local] = 0;
  hash_set (d->by_sub, sub_idx, (uword) local + 1);
  d->n_alloc++;
  d->n_allocations++;
  *is_new = 1;

found:
  block_idx = local * n_workers + worker;
  b->sub_idx = sub_idx;
  if (PREDICT_FALSE (bpa_dyn_addr (d, p, block_idx, b)))
    return -1;
  return 0;
}

int
bpa_dyn_acquire_extra (bpa_dyn_t *d, const bpa_pool_t *p, u32 sub_idx,
		       u32 worker, u32 n_workers, u32 primary, u32 nth,
		       bpa_block_t *b, int *is_new)
{
  bpa_dyn_extra_t *e;
  u32 local, block_idx, want;

  *is_new = 0;
  if (PREDICT_FALSE (vec_len (d->free_list) == 0))
    {
      d->n_exhausted++;
      return -1;
    }

  local = BPA_NO_BLOCK;

  /*
   * A reserve block on the subscriber's OWN public address, when there is
   * one free in this worker's slice.
   *
   * RFC 4787 REQ-2, paired address pooling: a subscriber should send from one
   * public address, because a peer that sees two is looking at what appears
   * to be two hosts. The deterministic mode has that property by construction
   * for the block the formula gives, and a reserve block taken off the top of
   * the free stack would be the first thing in this mode to break it.
   *
   * A PREFERENCE and not a rule, exactly like the stride below and for the
   * same reason. The reserve is sharded by index, so the blocks on the
   * subscriber's own address are not all in its worker's slice, and the ones
   * that are may be held by somebody else. Refusing the extension rather than
   * taking a block elsewhere would turn a property about layout into a
   * customer losing traffic.
   *
   * The loop is over block_reserve entries - a handful, and only on the
   * packet that found no free port anywhere.
   */
  if (PREDICT_FALSE (d->reserve != 0))
    {
      u32 pub_idx, blk_idx, off;

      bpa_split (p, sub_idx, &pub_idx, &blk_idx);
      for (off = 0; off < p->block_reserve; off++)
	{
	  u32 r = bpa_reserve_index (p, pub_idx, p->blocks_mapped + off);
	  u32 cand;

	  if (r % n_workers != worker)
	    continue;
	  cand = r / n_workers;
	  if (cand >= d->n_local || d->owner[cand] != BPA_NO_BLOCK)
	    continue;
	  bpa_dyn_unfree (d, cand);
	  local = cand;
	  break;
	}
    }

  /*
   * No stride over a reserve. bpa_stride_block() steps along the MAPPED
   * blocks of a public address, and a reserve index is a different space
   * entirely: applying one here would name an index that is somebody else's
   * reserve block, or none at all.
   */
  if (PREDICT_FALSE (p->block_stride != 0 && !d->reserve))
    {
      u32 cand = BPA_NO_BLOCK;

      /*
       * Three ways this comes to nothing, and all three end in the same
       * fall-back below.
       *
       * A subscriber whose primary was reaped while an extension outlived it
       * has nothing to measure a stride from, and the caller says so with
       * BPA_NO_BLOCK rather than with a block index that means zero.
       *
       * The stride can step off the end of the subscriber's own public
       * address, which bpa_stride_block() refuses rather than wrapping.
       *
       * And the block it names may belong to another worker's slice. Handing
       * that one out would put the subscriber's replies on a thread that
       * holds no session for them, because out2in resolves the thread from
       * the block index.
       */
      if (primary != BPA_NO_BLOCK
	  && bpa_stride_block (p, primary, nth, &want) == 0
	  && want % n_workers == worker)
	cand = want / n_workers;

      if (cand != BPA_NO_BLOCK && cand < d->n_local
	  && d->owner[cand] == BPA_NO_BLOCK)
	{
	  bpa_dyn_unfree (d, cand);
	  local = cand;
	  d->n_stride_placed++;
	}
      else
	d->n_stride_missed++;
    }

  if (local == BPA_NO_BLOCK)
    local = vec_pop (d->free_list);
  d->owner[local] = sub_idx;
  d->idle_since[local] = 0;
  /* by_sub is deliberately NOT touched: it names the subscriber's first
   * block, and bpa_dyn_acquire must keep returning that one. */
  vec_add2 (d->extras, e, 1);
  e->sub_idx = sub_idx;
  e->local = local;
  d->n_alloc++;
  d->n_allocations++;
  d->n_extensions++;
  *is_new = 1;

  block_idx = local * n_workers + worker;
  b->sub_idx = sub_idx;
  if (PREDICT_FALSE (bpa_dyn_addr (d, p, block_idx, b)))
    return -1;
  return 0;
}

/*
 * Takes one named block off the free list. The list is a stack, so the entry
 * has to be found rather than popped.
 *
 * Guessed at before it is searched for. bpa_dyn_init() fills the stack with
 * n-1 down to 0, so block `local` starts at index n-1-local, and vec_del1()
 * displaces exactly one entry per removal - the one that was last. The guess
 * is therefore right for every block nothing has displaced, which on the path
 * that pins many blocks at once - a pool coming back with the forwards its
 * configuration carries - is all of them.
 *
 * The guess is worth its two lines because the search is not free and is paid
 * under the worker barrier. Measured, 64 forwards one at a time: with the
 * search alone, 202 ms on a 258048-block slice against 149 ms on a 4032-block
 * one and 158 ms on a deterministic pool, which pins nothing - about 0.8 ms
 * per pin, all of it the walk, and a pool created with a few hundred forwards
 * pays every one of them inside ONE barrier acquisition. With the guess the
 * same three read 161, 166 and 162 ms: the cost no longer follows the size of
 * the slice.
 */
static void
bpa_dyn_unfree (bpa_dyn_t *d, u32 local)
{
  u32 i = d->n_local - 1 - local;

  if (i < vec_len (d->free_list) && d->free_list[i] == local)
    {
      vec_del1 (d->free_list, i);
      return;
    }

  for (i = 0; i < vec_len (d->free_list); i++)
    if (d->free_list[i] == local)
      {
	vec_del1 (d->free_list, i);
	return;
      }
}

int
bpa_dyn_pin (bpa_dyn_t *d, u32 block_idx, u32 sub_idx, u32 n_workers, f64 now,
	     u32 *held_by, int *is_new)
{
  u32 local = block_idx / n_workers, k;
  uword *q;

  *is_new = 0;
  if (!d->active || local >= d->n_local)
    return -2;

  if (d->owner[local] != BPA_NO_BLOCK && d->owner[local] != sub_idx)
    {
      *held_by = d->owner[local];
      return -1;
    }

  if (d->owner[local] == BPA_NO_BLOCK)
    {
      bpa_dyn_unfree (d, local);
      d->owner[local] = sub_idx;
      d->n_alloc++;
      d->n_allocations++;
      /* Idle from the first instant, and deliberately: nothing is using the
       * block yet. The pin is what keeps it, and the moment the pin goes the
       * ordinary lifetime applies with no special case to remember. */
      d->idle_since[local] = now;
      *is_new = 1;
    }

  /*
   * The pinned block becomes the subscriber's primary, and whatever it was
   * holding becomes an extension.
   *
   * Not cosmetic. bpa_dyn_acquire() answers with the primary, and the
   * forwarded socket lives in the pinned block - so a subscriber left on its
   * old primary would have its own outbound traffic translated out of a block
   * that does not contain the operator's forward, and the forward would be
   * contradicted by the customer's own packets. The old block keeps its
   * sessions and leaves by the hold-down like any other extension.
   */
  q = hash_get (d->by_sub, sub_idx);
  if (q && (u32) (q[0] - 1) != local)
    {
      bpa_dyn_extra_t *e;

      vec_add2 (d->extras, e, 1);
      e->sub_idx = sub_idx;
      e->local = (u32) (q[0] - 1);
    }

  /*
   * And unconditionally off the extras, because the block being pinned may
   * have been one: the two lists are exclusive - the reap picks a block's list
   * by asking by_sub first - and an entry left behind names a local index that
   * is handed to somebody else later, so the old subscriber going idle would
   * mark THEIR block. Unconditional and not inside the branch above: a
   * subscriber can hold an extension with no primary at all, for as long as it
   * takes the reap to come back for it after its budget ran out mid-pass.
   */
  for (k = 0; k < vec_len (d->extras); k++)
    if (d->extras[k].local == local)
      {
	vec_del1 (d->extras, k);
	break;
      }
  hash_set (d->by_sub, sub_idx, (uword) local + 1);

  if (!d->pinned[local])
    {
      d->pinned[local] = 1;
      d->n_pinned++;
    }
  return 0;
}

void
bpa_dyn_unpin (bpa_dyn_t *d, u32 block_idx, u32 n_workers, int *released)
{
  u32 local = block_idx / n_workers;

  *released = 0;
  if (!d->active || local >= d->n_local || !d->pinned[local])
    return;

  d->pinned[local] = 0;
  d->n_pinned--;

  /*
   * In use: it is an ordinary dynamic block from here on, and the hold-down
   * that has been suspended for the life of the forward takes it back the
   * usual way - with the usual natEvent 17 from the reap.
   */
  if (d->idle_since[local] == 0)
    return;

  /*
   * Not in use: the pin was the only thing holding it, so it goes back now
   * rather than sitting out a hold-down that is measuring nothing. The caller
   * emits the release.
   */
  if (hash_get (d->by_sub, d->owner[local])
      && hash_get (d->by_sub, d->owner[local])[0] == (uword) local + 1)
    hash_unset (d->by_sub, d->owner[local]);
  else
    {
      u32 k;
      for (k = 0; k < vec_len (d->extras); k++)
	if (d->extras[k].local == local)
	  {
	    vec_del1 (d->extras, k);
	    break;
	  }
    }
  d->owner[local] = BPA_NO_BLOCK;
  d->idle_since[local] = 0;
  vec_add1 (d->free_list, local);
  d->n_alloc--;
  d->n_releases++;
  *released = 1;
}

void
bpa_dyn_idle (bpa_dyn_t *d, u32 sub_idx, f64 now)
{
  uword *q = hash_get (d->by_sub, sub_idx);
  bpa_dyn_extra_t *e;

  if (q)
    d->idle_since[q[0] - 1] = now;
  /* Extensions go idle with the subscriber that holds them. Missing this
   * leaks a block per extension: owned forever, never reaped, and visible
   * only as a pool that slowly stops having free blocks. */
  vec_foreach (e, d->extras)
    if (e->sub_idx == sub_idx)
      d->idle_since[e->local] = now;
}

u32
bpa_dyn_reap (bpa_dyn_t *d, f64 now, u32 holddown, u32 worker, u32 n_workers,
	      bpa_dyn_release_t *out, u32 max_out)
{
  u32 i, freed = 0;

  if (!d->active)
    return 0;

  for (i = 0; i < d->n_local; i++)
    {
      if (freed >= max_out)
	break;
      if (d->owner[i] == BPA_NO_BLOCK)
	continue;
      /* Held by the configuration, not by traffic. Reaping it would give the
       * block to the next subscriber that asks and leave the operator's
       * forward pointing at them - the failure that kept forwards off dynamic
       * pools until the pin existed. */
      if (d->pinned[i])
	continue;
      if (d->idle_since[i] == 0)
	continue;
      if (now - d->idle_since[i] <= (f64) holddown)
	continue;

      out[freed].sub_idx = d->owner[i];
      out[freed].block_idx = i * n_workers + worker;

      /* Only the primary lives in by_sub; an extension has an entry in
       * extras instead, and removing the wrong one would strand the other. */
      if (hash_get (d->by_sub, d->owner[i])
	  && hash_get (d->by_sub, d->owner[i])[0] == (uword) i + 1)
	hash_unset (d->by_sub, d->owner[i]);
      else
	{
	  u32 k;
	  for (k = 0; k < vec_len (d->extras); k++)
	    if (d->extras[k].local == i)
	      {
		vec_del1 (d->extras, k);
		break;
	      }
	}
      d->owner[i] = BPA_NO_BLOCK;
      d->idle_since[i] = 0;
      vec_add1 (d->free_list, i);
      d->n_alloc--;
      d->n_releases++;
      freed++;
    }

  return freed;
}
