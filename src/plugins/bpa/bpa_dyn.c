/* bpa_dyn.c - dynamic (PBA) block allocator. */

#include "bpa_dyn.h"

/* block_idx <-> (pub_idx, blk_idx) reuses the pool policy split, so a
 * dynamic pool still spreads consecutive allocations across public
 * addresses when the policy is interleaved. Ver bpa_dyn_block_addr(). */

static void bpa_dyn_unfree (bpa_dyn_t *d, u32 local);
static void bpa_dyn_push (bpa_dyn_t *d, u32 local);
static u32 bpa_dyn_pop (bpa_dyn_t *d);

/*
 * Block `blk` of public address `pub_idx`, taken for the caller when it is
 * free. *local is left alone when it is not, so a walk is a sequence of these
 * and the caller's stopping test is the same one as its loop condition.
 *
 * No worker test, and that is an invariant rather than an omission: the only
 * caller steps by bpa_pair_step() from a block this worker already holds, and
 * that step is the smallest one whose block-index distance is a multiple of
 * the worker count. Every candidate is therefore congruent to the primary,
 * which is congruent to this worker.
 *
 * Worth saying what a broken caller would produce, because it is not what the
 * shape of this function suggests. The division TRUNCATES, so a candidate that
 * is not congruent resolves to a block that IS in this worker's slice and is
 * simply not the one asked for - on another public address. Nothing here can
 * see that, and a check written as "the block is in my slice" cannot either:
 * local * n_workers + worker is congruent to worker for every local there is.
 * The step is asserted against ref/bpa_model.py in test/test_bpa_pool.c, and
 * what a wrong one produces - a subscriber on two addresses - in
 * test/test_bpa_dyn.c.
 */
static_always_inline void
bpa_dyn_take_free (bpa_dyn_t *d, const bpa_pool_t *p, u32 pub_idx, u32 blk,
		   u32 n_workers, u32 *local)
{
  u32 cand = bpa_join (p, pub_idx, blk) / n_workers;

  if (cand >= d->n_local || d->owner[cand] != BPA_NO_BLOCK)
    return;
  bpa_dyn_unfree (d, cand);
  *local = cand;
}

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
  vec_validate (d->free_pos, n - 1);
  for (i = 0; i < n; i++)
    {
      d->free_list[i] = n - 1 - i;
      d->free_pos[n - 1 - i] = i;
    }
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
  vec_free (d->free_pos);
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

  local = bpa_dyn_pop (d);
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
      /* Counted here as well as on the dynamic walk below, because it is the
       * same question about the same property and an operator reading one
       * number should not have to know which allocator answered it. */
      if (local == BPA_NO_BLOCK)
	d->n_paired_missed++;
      else
	d->n_paired_placed++;
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

  /*
   * A free block on the subscriber's OWN public address, when nothing above
   * has already chosen one.
   *
   * RFC 6888 REQ-2 makes paired address pooling the required DEFAULT for a
   * CGN, and this is the path that decides whether this box has it. Without
   * it an extension is whatever comes off the free stack, and under
   * `policy interleaved` the next block index is the next public address - so
   * the ordinary case, a subscriber that fills its block and is given another,
   * puts one customer on two public addresses at once. It is not a corner:
   * measured at 0 of 8 extensions paired over four public addresses, 0 of 40000
   * over sixteen, and it gets WORSE as the pool grows, because alignment needs
   * the gap between two blocks to be a multiple of the address count (#226).
   *
   * What it costs the customer is not cosmetic. bpa_alg_reserve_pair() will
   * only take a media pair from a block on the SAME public address as the
   * signalling session, because the header carries only ports and the peer
   * takes the address from the connection it answers - so a split subscriber's
   * SIP and RTSP get BPA_ALG_NO_PORTS with ports free, the SDP leaves carrying
   * the private address, the call connects and there is no audio.
   *
   * Not the stride, and after it. A stride is the operator saying WHERE the
   * second block goes; this says only WHICH ADDRESS it goes on, which is the
   * conformance statement rather than a layout. Where both apply the
   * operator's number wins, and this catches the strides bpa_stride_block()
   * could not place, which is why the two pairs of counters stay meaningful
   * side by side.
   *
   * TWO WALKS, because they find different blocks and the measurements say
   * both are needed. Candidates are blk_idx +/- m*bpa_pair_step(), which is
   * every block of this address that lands in this worker's slice.
   *
   *   - outwards from the subscriber's own block. Cheap, and the block just
   *     above the primary is the one whose ports ABUT it, which is the range
   *     an investigator reads as one holding rather than as two rows that look
   *     like a duplicate.
   *   - upwards from wherever the free stack is about to hand blocks out.
   *     A pool that filled from the bottom has no free block anywhere near the
   *     primary, and the first walk would have to cross the whole allocated
   *     region to find that out. The top of the stack says where the free ones
   *     start, and the arithmetic jumps there.
   *
   * BOUNDED, at BPA_PAIR_WALK candidates each, and the bound is where the
   * honesty is. Measured over three arrival patterns - one subscriber at a
   * time, everybody-then-everybody, and a steady state with three quarters of
   * the subscribers reaped - across 1 to 256 public addresses and 1 to 8
   * workers: 100%, 99.6% and 99.2% of extensions paired at the worst cell of
   * each, against 0.0%, 0.0% and 0.2% on the allocator this replaces.
   * test/test_bpa_dyn.c is where those numbers are asserted.
   *
   * A PREFERENCE and not a rule, for the reason the reserve above gives:
   * refusing the extension rather than taking a block elsewhere would turn a
   * property about layout into a customer losing traffic. n_paired_placed and
   * n_paired_missed are what an operator reads instead of the claim.
   */
  if (PREDICT_FALSE (local == BPA_NO_BLOCK && !d->reserve
		     && primary != BPA_NO_BLOCK))
    {
      u32 pub_idx, blk_idx, step = bpa_pair_step (p, n_workers);
      u32 tries, up, dn, j;

      bpa_split (p, primary, &pub_idx, &blk_idx);
      up = blk_idx;
      dn = blk_idx;
      for (tries = 0; tries < BPA_PAIR_WALK && local == BPA_NO_BLOCK; tries++)
	{
	  j = BPA_NO_BLOCK;
	  if ((tries & 1) == 0 && up + step < p->blocks_mapped)
	    j = (up += step);
	  else if (dn >= step)
	    j = (dn -= step);
	  else if (up + step < p->blocks_mapped)
	    j = (up += step);
	  if (j == BPA_NO_BLOCK)
	    break; /* the address has no candidate left in either direction */
	  bpa_dyn_take_free (d, p, pub_idx, j, n_workers, &local);
	}

      /*
       * The second walk, from the stack rather than from the subscriber.
       *
       * `top` is the block the next bpa_dyn_pop() would hand out. The first
       * candidate at or above it is primary + m*step*unit for the smallest m
       * that reaches it, and from there the walk steps along the address as
       * before. Guarded on the stack being non-empty because the walk above
       * may have taken the last free block.
       */
      if (local == BPA_NO_BLOCK && vec_len (d->free_list) != 0)
	{
	  u64 span = (u64) step * bpa_stride_unit (p);
	  u32 top = d->free_list[vec_len (d->free_list) - 1] * n_workers
		    + worker;

	  /* u64 because the span is a worker count times the whole public set,
	   * which a pool of one block per address can take past 2^32 - and a
	   * wrapped span would name a j the walk below would then discard,
	   * turning a cheap jump into a wasted one for no visible reason. */
	  j = blk_idx + step;
	  if (top > primary)
	    {
	      u64 m = ((u64) (top - primary) + span - 1) / span;

	      if (m > 1 && m * step < p->blocks_mapped)
		j = blk_idx + (u32) m * step;
	    }
	  for (tries = 0; tries < BPA_PAIR_WALK && local == BPA_NO_BLOCK
			  && j < p->blocks_mapped;
	       tries++, j += step)
	    bpa_dyn_take_free (d, p, pub_idx, j, n_workers, &local);
	}

      if (local == BPA_NO_BLOCK)
	d->n_paired_missed++;
      else
	d->n_paired_placed++;
    }

  if (local == BPA_NO_BLOCK)
    local = bpa_dyn_pop (d);
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

/* ------------------------------------------------------------------ */
/* the free stack                                                      */

/*
 * Three operations, and free_pos[] is what makes all three constant time.
 * It is the inverse of free_list[]: every write to one writes the other, and
 * what a reader can check is that free_list[free_pos[x]] == x for every free
 * x, and that free_pos[x] is BPA_NO_BLOCK for every held one.
 *
 * bpa_dyn_unfree() used to search, with a guess in front of it derived from
 * how bpa_dyn_init() fills the stack and from vec_del1() displacing exactly
 * one entry. The guess was right for every block nothing had displaced, which
 * made it right on the path that pins many blocks at once and wrong on any
 * path that names blocks in an order of its own. Measured at 72684 free-list
 * entries examined for ONE extension on a 16-address pool at 3 workers, once
 * the paired-address preference below started naming blocks - and, before
 * that, at about 0.8 ms per pin on a 258048-block slice against 0.16 ms on a
 * small one, all of the difference being the walk (#226). An inverse index
 * costs four bytes a block and has no bad order.
 */

static void
bpa_dyn_push (bpa_dyn_t *d, u32 local)
{
  d->free_pos[local] = vec_len (d->free_list);
  vec_add1 (d->free_list, local);
}

static u32
bpa_dyn_pop (bpa_dyn_t *d)
{
  u32 local = vec_pop (d->free_list);

  d->free_pos[local] = BPA_NO_BLOCK;
  return local;
}

/*
 * Takes one NAMED block off the stack. vec_del1() moves the last entry into
 * the hole, so exactly one other block changes position and it is the one
 * that was on top - which is the whole of the bookkeeping.
 */
static void
bpa_dyn_unfree (bpa_dyn_t *d, u32 local)
{
  u32 i = d->free_pos[local], last;

  if (i >= vec_len (d->free_list))
    return;
  last = vec_len (d->free_list) - 1;
  d->free_pos[local] = BPA_NO_BLOCK;
  if (i < last)
    d->free_pos[d->free_list[last]] = i;
  vec_del1 (d->free_list, i);
}

u32
bpa_dyn_lend (bpa_dyn_t *d, u32 worker, u32 n_workers, u32 want, u32 *out)
{
  u32 n = 0;

  if (!d->active || !out)
    return 0;

  while (n < want && vec_len (d->free_list) > 0)
    {
      u32 local = bpa_dyn_pop (d);

      /*
       * The pop already took it off the stack and cleared free_pos, so the
       * only thing left is to say WHY it is not there. A block whose owner
       * is BPA_LENT is skipped by the reap, refused by the pin, and invisible
       * to every candidate search, because all of them ask whether the owner
       * is BPA_NO_BLOCK.
       */
      d->owner[local] = BPA_LENT;
      d->idle_since[local] = 0;
      d->n_lent++;
      out[n++] = local * n_workers + worker;
    }

  return n;
}

int
bpa_dyn_take_back (bpa_dyn_t *d, u32 block_idx, u32 n_workers)
{
  u32 local = block_idx / n_workers;

  if (!d->active || local >= d->n_local)
    return -1;
  /*
   * A block that is not lent is not this function's to move. Taking a HELD
   * block back would push a subscriber's block onto the free list and hand
   * it to the next one that asks - two subscribers on one public port range,
   * which is the defect the whole allocation accounting exists to prevent.
   */
  if (d->owner[local] != BPA_LENT)
    return -1;

  d->owner[local] = BPA_NO_BLOCK;
  d->idle_since[local] = 0;
  d->n_lent--;
  bpa_dyn_push (d, local);
  return 0;
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

  /*
   * LENT, so this slice cannot give it to anybody: the borrower is holding
   * it for one of its own subscribers. Reported as held by nobody this box
   * can name, because the alternative is printing ~1 as a subscriber index -
   * a number that would be looked up, would miss, and would read to an
   * operator as a forward colliding with a subscriber that does not exist.
   */
  if (d->owner[local] == BPA_LENT)
    {
      *held_by = BPA_NO_BLOCK;
      return -1;
    }
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
  bpa_dyn_push (d, local);
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
      /*
       * Lent to another worker, which is holding it for a subscriber this
       * slice knows nothing about (#210). Reaping it would push it onto this
       * free list while the borrower is still translating out of it, and the
       * release record would name BPA_LENT as the subscriber.
       *
       * REDUNDANT TODAY, and kept: `bpa_dyn_lend` leaves idle_since at 0 and
       * the line below already skips those, so removing this changes
       * nothing - measured, as a mutation that no check could catch. What it
       * costs is one comparison on a walk that runs once a second; what it
       * buys is that the day somebody gives a lent block an idle time - to
       * expire a loan, say - the reap does not start releasing other
       * workers' subscribers. test_bpa_dyn.c asserts the zero this leans on,
       * so the redundancy is a fact rather than a hope.
       */
      if (d->owner[i] == BPA_LENT)
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
      bpa_dyn_push (d, i);
      d->n_alloc--;
      d->n_releases++;
      freed++;
    }

  return freed;
}
