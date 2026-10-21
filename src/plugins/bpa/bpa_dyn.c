/* bpa_dyn.c - dynamic (PBA) block allocator. */

#include "bpa_dyn.h"

/* block_idx <-> (pub_idx, blk_idx) reuses the pool policy split, so a
 * dynamic pool still spreads consecutive allocations across public
 * addresses when the policy is interleaved. Ver bpa_dyn_block_addr(). */

static void bpa_dyn_unfree (bpa_dyn_t *d, u32 local);
static void bpa_dyn_push (bpa_dyn_t *d, u32 local);
static u32 bpa_dyn_pop (bpa_dyn_t *d);
static void bpa_dyn_index_build (bpa_dyn_t *d);

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
  d->worker = worker;
  d->n_workers = n_workers;

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

  /*
   * The least-loaded index, for the one kind of slice that has a choice to
   * make (#603). Not a reserve: a deterministic pool's reserve is a handful of
   * blocks an address, handed out as extensions on the subscriber's own
   * address where it can be, and its spread is the formula's.
   */
  if (!reserve && p->dyn_share && p->policy == BPA_POLICY_INTERLEAVED
      && p->dyn_share->n_addr == p->outside.count
      && p->dyn_share->n_workers == n_workers)
    {
      d->share = p->dyn_share;
      d->n_addr = p->outside.count;
      bpa_dyn_index_build (d);
    }

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
  /* BEFORE the memset, which is where it was not: freeing after it hands
     vec_free a pointer that has just been zeroed, so the vector leaks and
     the call looks right. */
  vec_free (d->foreign);
  vec_free (d->foreign_idle);
  vec_free (d->addr_next);
  vec_free (d->addr_prev);
  vec_free (d->addr_head);
  vec_free (d->addr_tail);
  vec_free (d->addr_free);
  vec_free (d->tree);
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
      /* Who, not how many packets: the rebalance's demand (#611). */
      bpa_demand_note (&d->demand, sub_idx, 0);
      return -1;
    }

  local = bpa_dyn_take (d);
  d->owner[local] = sub_idx;
  d->idle_since[local] = 0;
  hash_set (d->by_sub, sub_idx, (uword) local + 1);
  bpa_dyn_count (d, local, 1);
  d->n_alloc++;
  d->n_allocations++;
  *is_new = 1;

found:
  /*
   * NOT `local * n_workers + worker` any more: a borrowed slot's block is
   * another worker's and is not congruent to this one (#210). Every block
   * below n_local takes the same arithmetic it always did, in the branch
   * bpa_dyn_block_of() predicts.
   */
  block_idx = bpa_dyn_block_at (d, local, worker, n_workers);
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
      bpa_demand_note (&d->demand, sub_idx, 1);
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
       * Under the least-loaded choice the slice keeps its free blocks BY
       * ADDRESS (#603), so the question the second walk below estimates is
       * one read: the oldest free block on the subscriber's own address, or
       * none. The walk is not just slower here, it is wrong: it jumps to
       * where the stack is about to hand blocks out, and a slice that takes
       * blocks by address no longer fills from the bottom - measured at 96%
       * of extensions paired on the parallel pattern over eight addresses
       * with the walk, against 99% before #603 and 100% with this.
       */
      if (local == BPA_NO_BLOCK && d->share && d->addr_free[pub_idx] != 0)
	{
	  local = d->addr_head[pub_idx];
	  bpa_dyn_unfree (d, local);
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

  /* Unpaired, it goes where an arrival would: an extension is not a second
     subscriber on its address and is not counted as one, but of the
     addresses it could land on, the least shared is still the better. */
  if (local == BPA_NO_BLOCK)
    local = bpa_dyn_take (d);
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

  /* Through the helper for the same reason bpa_dyn_acquire does: an
     extension can come off a borrowed slot, and that block is not this
     worker's by arithmetic (#210). */
  block_idx = bpa_dyn_block_at (d, local, worker, n_workers);
  b->sub_idx = sub_idx;
  if (PREDICT_FALSE (bpa_dyn_addr (d, p, block_idx, b)))
    return -1;
  return 0;
}

/* ------------------------------------------------------------------ */
/* the least-loaded choice (#603)                                      */

/*
 * The public address a slot's block is on. Through bpa_dyn_block_at()
 * because a borrowed slot's block is the lender's (#210), and under
 * `policy interleaved` the address is the block index modulo the address
 * count - bpa_split()'s remainder, without the pool.
 */
static_always_inline u32
bpa_dyn_slot_addr (const bpa_dyn_t *d, u32 local)
{
  return bpa_dyn_block_at (d, local, d->worker, d->n_workers) % d->n_addr;
}

/*
 * One entry of the tournament: the load in the high word and the address in
 * the low, so the smaller of two entries IS the rule - fewer subscribers,
 * then the lower address - with no second array to look the key up in. An
 * address with no free block in this slice has load ~0 and loses to every
 * address that has one.
 *
 * The first version kept the keys apart and the addresses in the tree, and
 * paid for it: every match was two reads of a key array as large as the
 * public set, and no replay could stop early, because a node whose winner
 * had not changed might still be carrying a key that had. Measured on the
 * bench below at 774 ns an allocation on a /16; this is the replacement.
 */
static_always_inline u64
bpa_dyn_entry (u32 load, u32 a)
{
  return ((u64) load << 32) | a;
}

/*
 * Replays the matches above one address after its entry moved, and stops at
 * the first node that comes out the same: from there up nothing has changed.
 * At most log2(n_leaves) matches.
 */
static void
bpa_dyn_tree_fix (bpa_dyn_t *d, u32 a)
{
  u32 i = (d->n_leaves + a) >> 1;

  while (i)
    {
      u64 l = d->tree[2 * i], r = d->tree[2 * i + 1], m = r < l ? r : l;

      if (d->tree[i] == m)
	break;
      d->tree[i] = m;
      i >>= 1;
    }
}

/*
 * An address's pool-wide load: the sum of every worker's row. Each row is
 * written by one slice only - from its worker's thread, or from the main
 * thread under the barrier while that worker is stopped - so no element has
 * two writers, and a read that races a write sees the old count or the new
 * one, both of which are a count that was true.
 */
static_always_inline u32
bpa_dyn_load_of (const bpa_dyn_share_t *sh, u32 a)
{
  const u32 *row = sh->load + (uword) a * sh->n_workers;
  u32 v, sum = 0;

  for (v = 0; v < sh->n_workers; v++)
    sum += __atomic_load_n (&row[v], __ATOMIC_RELAXED);
  return sum;
}

/* Re-reads one address's pool-wide load into this slice's tree. */
static void
bpa_dyn_addr_refresh (bpa_dyn_t *d, u32 a)
{
  u64 e = bpa_dyn_entry (
    d->addr_free[a] ? bpa_dyn_load_of (d->share, a) : ~0u, a);

  if (e == d->tree[d->n_leaves + a])
    return;
  d->tree[d->n_leaves + a] = e;
  bpa_dyn_tree_fix (d, a);
}

/* A free slot joins its address's list, at the TAIL: oldest-freed first. */
static void
bpa_dyn_addr_link (bpa_dyn_t *d, u32 local)
{
  u32 a = bpa_dyn_slot_addr (d, local), t = d->addr_tail[a];

  d->addr_next[local] = BPA_NO_BLOCK;
  d->addr_prev[local] = t;
  if (t != BPA_NO_BLOCK)
    d->addr_next[t] = local;
  else
    d->addr_head[a] = local;
  d->addr_tail[a] = local;
  if (d->addr_free[a]++ == 0)
    bpa_dyn_addr_refresh (d, a);
}

/* And leaves it, from anywhere in the list: a pin, a pair, a stride or a
 * loan names its block, and that block is rarely the head. */
static void
bpa_dyn_addr_unlink (bpa_dyn_t *d, u32 local)
{
  u32 a = bpa_dyn_slot_addr (d, local);
  u32 p = d->addr_prev[local], n = d->addr_next[local];

  if (p != BPA_NO_BLOCK)
    d->addr_next[p] = n;
  else
    d->addr_head[a] = n;
  if (n != BPA_NO_BLOCK)
    d->addr_prev[n] = p;
  else
    d->addr_tail[a] = p;
  d->addr_next[local] = d->addr_prev[local] = BPA_NO_BLOCK;
  if (--d->addr_free[a] == 0)
    bpa_dyn_addr_refresh (d, a);
}

/*
 * The index over a fresh slice. Its blocks are linked in ascending order, so
 * on a pool nobody has churned the choice is the stack's: with one worker the
 * slice hands out blocks 0, 1, 2 ... exactly as bpa_dyn_pop() did, because
 * the lowest address with the fewest subscribers is the next block's.
 */
static void
bpa_dyn_index_build (bpa_dyn_t *d)
{
  u32 i;

  d->n_leaves = 1;
  while (d->n_leaves < d->n_addr)
    d->n_leaves <<= 1;
  if (d->n_local)
    {
      vec_validate (d->addr_next, d->n_local - 1);
      vec_validate (d->addr_prev, d->n_local - 1);
    }
  vec_validate (d->addr_head, d->n_addr - 1);
  vec_validate (d->addr_tail, d->n_addr - 1);
  vec_validate (d->addr_free, d->n_addr - 1);
  vec_validate (d->tree, 2 * d->n_leaves - 1);
  for (i = 0; i < d->n_addr; i++)
    {
      d->addr_head[i] = d->addr_tail[i] = BPA_NO_BLOCK;
      d->addr_free[i] = 0;
    }
  for (i = 0; i < d->n_leaves; i++)
    d->tree[d->n_leaves + i] = bpa_dyn_entry (~0u, i);
  for (i = d->n_leaves - 1; i >= 1; i--)
    d->tree[i] = d->tree[2 * i];
  for (i = 0; i < d->n_local; i++)
    bpa_dyn_addr_link (d, i);
}

/*
 * Reads back every address whose load another slice LOWERED since this one
 * last chose. The summary word first, then the word, each taken with an
 * exchange, so a bit set while this runs is either seen now or left for the
 * next drain - never cleared unseen. A plain read of the summary before the
 * exchange, so an allocation after which nobody released anything - the
 * ordinary case on a worker - pays no locked instruction here at all.
 */
static void
bpa_dyn_drain (bpa_dyn_t *d)
{
  u64 *st = d->share->stale[d->worker], *sum = d->share->stale_sum[d->worker];
  u32 s, n_sum = vec_len (sum);

  for (s = 0; s < n_sum; s++)
    {
      u64 x;

      if (!__atomic_load_n (&sum[s], __ATOMIC_RELAXED))
	continue;
      x = __atomic_exchange_n (&sum[s], 0, __ATOMIC_SEQ_CST);
      while (x)
	{
	  u32 wi = s * 64 + __builtin_ctzll (x);
	  u64 y = __atomic_exchange_n (&st[wi], 0, __ATOMIC_SEQ_CST);

	  x &= x - 1;
	  while (y)
	    {
	      u32 a = wi * 64 + __builtin_ctzll (y);

	      y &= y - 1;
	      if (a < d->n_addr)
		bpa_dyn_addr_refresh (d, a);
	    }
	}
    }
}

/*
 * The address the rule picks right now: the least loaded pool-wide among
 * those this slice has a free block on, the lowest on a tie.
 */
static u32
bpa_dyn_choose (bpa_dyn_t *d)
{
  u32 a, tries;
  u64 cur;

  /*
   * EXACT, in two halves. A load another slice LOWERED arrives as a bit, and
   * the drain reads it back. A load another worker RAISED - an arrival on
   * its own slice, which is the one change made on a worker thread and
   * outside the barrier - sends nothing: this slice's entry for it is merely
   * too low, which can only make that address win when it should not. So
   * the winner is re-read, and when it was too low it is corrected and the
   * match replayed, until the winner reads true. Every entry is then at or
   * below the truth and the winner AT it, so it is the least-loaded address.
   *
   * Bounded by the address count, because each pass corrects one address
   * and a corrected address stays correct for as long as nothing moves it;
   * paid for, over time, by the arrivals on other workers that made those
   * entries low - one correction each at most, rather than a message each
   * whether or not this slice ever looks. That is the trade against
   * telling every worker about every arrival, which would put a locked
   * instruction per worker on every allocation: measured, before this, at
   * 128 ns an allocation on the bench's pool.
   */
  bpa_dyn_drain (d);
  for (tries = 0; tries <= d->n_addr; tries++)
    {
      a = (u32) d->tree[1];
      cur = d->tree[d->n_leaves + a];
      bpa_dyn_addr_refresh (d, a);
      if (cur == d->tree[d->n_leaves + a])
	break;
    }
  return (u32) d->tree[1];
}

u32
bpa_dyn_take (bpa_dyn_t *d)
{
  u32 a, local;

  if (!d->share)
    return bpa_dyn_pop (d);
  a = bpa_dyn_choose (d);
  /* Unreachable while free_list and the index agree, which test_bpa_dyn.c
     asserts after every scenario; the stack is the answer if they did not. */
  if (PREDICT_FALSE (a >= d->n_addr || d->addr_free[a] == 0))
    return bpa_dyn_pop (d);
  local = d->addr_head[a];
  bpa_dyn_unfree (d, local);
  return local;
}

void
bpa_dyn_count (bpa_dyn_t *d, u32 local, int delta)
{
  bpa_dyn_share_t *sh = d->share;
  u32 a, v, word, *cell;
  u64 bit, sbit;

  if (!sh)
    return;
  a = bpa_dyn_slot_addr (d, local);
  /* This worker's own row: no other slice writes it, so no lock. */
  cell = &sh->load[(uword) a * sh->n_workers + d->worker];
  __atomic_store_n (cell, *cell + (delta > 0 ? 1 : -1), __ATOMIC_RELAXED);
  bpa_dyn_addr_refresh (d, a);
  if (delta > 0)
    return;

  /*
   * A LOWERED load is the one the others must be told about: an entry left
   * too high would keep an address out of the running, and the rule would
   * stop being the rule without anything saying so. Every other worker, not
   * only the ones whose own blocks are on this address, because a borrowed
   * block can put any address in any slice (#210).
   *
   * Every caller that lowers a load - the reap, a pin that moves a primary,
   * an unpin, the free - runs on the main thread under the barrier, so these
   * ORs meet no worker; they are atomic anyway, so that the day one of them
   * moves to a worker the bits are still never lost. Read before the OR, so
   * a bit still waiting to be drained costs nothing.
   */
  word = a >> 6;
  bit = 1ULL << (a & 63);
  sbit = 1ULL << (word & 63);
  for (v = 0; v < sh->n_workers; v++)
    {
      if (v == d->worker)
	continue;
      if (!(__atomic_load_n (&sh->stale[v][word], __ATOMIC_SEQ_CST) & bit))
	__atomic_fetch_or (&sh->stale[v][word], bit, __ATOMIC_SEQ_CST);
      if (!(__atomic_load_n (&sh->stale_sum[v][word >> 6], __ATOMIC_SEQ_CST)
	    & sbit))
	__atomic_fetch_or (&sh->stale_sum[v][word >> 6], sbit,
			   __ATOMIC_SEQ_CST);
    }
}

void
bpa_dyn_share_init (bpa_pool_t *p, u32 n_workers)
{
  bpa_dyn_share_t *sh = 0;
  u32 w, n_words, n_sum;

  if (p->dyn_share || p->mode != BPA_MODE_DYNAMIC
      || p->policy != BPA_POLICY_INTERLEAVED || p->outside.count == 0
      || n_workers == 0)
    return;
  n_words = (p->outside.count + 63) / 64;
  n_sum = (n_words + 63) / 64;
  vec_validate (sh, 0);
  sh->n_addr = p->outside.count;
  sh->n_workers = n_workers;
  vec_validate (sh->load, (uword) sh->n_addr * n_workers - 1);
  vec_validate (sh->stale, n_workers - 1);
  vec_validate (sh->stale_sum, n_workers - 1);
  for (w = 0; w < n_workers; w++)
    {
      vec_validate (sh->stale[w], n_words - 1);
      vec_validate (sh->stale_sum[w], n_sum - 1);
    }
  p->dyn_share = sh;
}

void
bpa_dyn_share_free (bpa_pool_t *p)
{
  bpa_dyn_share_t *sh = p->dyn_share;
  u32 w;

  if (!sh)
    return;
  for (w = 0; w < sh->n_workers; w++)
    {
      vec_free (sh->stale[w]);
      vec_free (sh->stale_sum[w]);
    }
  vec_free (sh->stale);
  vec_free (sh->stale_sum);
  vec_free (sh->load);
  vec_free (sh);
  p->dyn_share = 0;
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
  if (d->share)
    bpa_dyn_addr_link (d, local);
}

static u32
bpa_dyn_pop (bpa_dyn_t *d)
{
  u32 local = vec_pop (d->free_list);

  d->free_pos[local] = BPA_NO_BLOCK;
  if (d->share)
    bpa_dyn_addr_unlink (d, local);
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
  if (d->share)
    bpa_dyn_addr_unlink (d, local);
}

u32
bpa_dyn_lend (bpa_dyn_t *d, u32 worker, u32 n_workers, u32 want, u32 *out)
{
  u32 n = 0;

  if (!d->active || !out)
    return 0;

  while (n < want)
    {
      u32 top = vec_len (d->free_list), local;

      /*
       * THE TOPMOST OF THIS SLICE'S OWN BLOCKS. A borrowed block sits on the
       * same free list - that is what leaves bpa_dyn_acquire() untouched -
       * and it is not this slice's to lend on (#210): it belongs to another
       * worker, which holds a slot for it and expects to take it back from
       * here. Lent a second time, the first lender would wait for ever and
       * its take_back would refuse.
       *
       * Measured before this line existed: draining a borrower's free list
       * lent the borrowed block away, and the block the loan was for ended
       * up held by nobody at all.
       */
      while (top > 0 && d->free_list[top - 1] >= d->n_local)
	top--;
      if (top == 0)
	break;
      local = d->free_list[top - 1];
      bpa_dyn_unfree (d, local);

      /*
       * The block is off the stack and its free_pos is cleared, so the only
       * thing left is to say WHY it is not there. A block whose owner is
       * BPA_LENT is skipped by the reap, refused by the pin, and invisible
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
bpa_dyn_borrow (bpa_dyn_t *d, u32 block_idx)
{
  u32 k, slot = BPA_NO_BLOCK;

  if (!d->active || block_idx == BPA_NO_BLOCK)
    return -1;

  /*
   * A VACANT SLOT FIRST. Slots are never deleted - by_sub and extras hold
   * local indices, and deleting one in the middle would move another
   * subscriber's block out from under them - so a slot whose block went
   * home is reused, and the array is bounded by the most this worker ever
   * borrowed at one time rather than by how many times it has borrowed.
   */
  for (k = 0; k < vec_len (d->foreign); k++)
    {
      if (d->foreign[k] == block_idx)
	return -1; /* already here: borrowing it twice would free it twice */
      if (d->foreign[k] == BPA_NO_BLOCK && slot == BPA_NO_BLOCK)
	slot = k;
    }

  if (slot == BPA_NO_BLOCK)
    {
      slot = vec_len (d->foreign);
      vec_add1 (d->foreign, block_idx);
      vec_add1 (d->foreign_idle, 0);
      /* One more slot in every array the slice indexes by local. They are
         grown together because every one of them is read by local index and
         a short one is a read past the end on the first borrowed block. */
      vec_add1 (d->owner, BPA_NO_BLOCK);
      vec_add1 (d->idle_since, 0);
      vec_add1 (d->pinned, 0);
      vec_add1 (d->free_pos, BPA_NO_BLOCK);
      /* And the least-loaded index's links, when there is one: a borrowed
	 block is filed under ITS public address, which is the lender's
	 arithmetic and any address at all as far as this slice knows. */
      if (d->share)
	{
	  vec_add1 (d->addr_next, BPA_NO_BLOCK);
	  vec_add1 (d->addr_prev, BPA_NO_BLOCK);
	}
    }
  else
    d->foreign[slot] = block_idx;
  /* A fresh loan has not been sitting idle, whatever the slot held before. */
  d->foreign_idle[slot] = 0;

  d->owner[d->n_local + slot] = BPA_NO_BLOCK;
  d->idle_since[d->n_local + slot] = 0;
  d->pinned[d->n_local + slot] = 0;
  /* IT ARRIVES FREE, on this slice's own free list, which is what leaves
     bpa_dyn_acquire() untouched: the next subscriber that asks pops it like
     any other block. */
  bpa_dyn_push (d, d->n_local + slot);
  return 0;
}

int
bpa_dyn_give_back (bpa_dyn_t *d, u32 block_idx)
{
  u32 k;

  if (!d->active)
    return -1;

  for (k = 0; k < vec_len (d->foreign); k++)
    {
      u32 local = d->n_local + k;

      if (d->foreign[k] != block_idx)
	continue;
      /*
       * FREE, OR IT STAYS. A held borrowed block is a subscriber's; handing
       * it back would let the lender give it to somebody else while this
       * worker is still translating out of it - two subscribers on one
       * public port range, which is the defect all of this accounting
       * exists to prevent. The rebalance asks again next tick.
       */
      if (d->owner[local] != BPA_NO_BLOCK)
	return -1;
      bpa_dyn_unfree (d, local);
      d->foreign[k] = BPA_NO_BLOCK;
      return 0;
    }
  return -1;
}

u32
bpa_dyn_foreign_free (const bpa_dyn_t *d)
{
  u32 k, n = 0;

  /* Over the borrowed slots only - bounded by the most this worker ever
     borrowed at one time, and walked once a tick under the barrier. */
  for (k = 0; k < vec_len (d->foreign); k++)
    if (d->foreign[k] != BPA_NO_BLOCK
	&& d->owner[d->n_local + k] == BPA_NO_BLOCK)
      n++;
  return n;
}

int
bpa_dyn_holds (void *ctx, u32 sub_idx)
{
  bpa_dyn_t *d = ctx;

  return d && d->by_sub && hash_get (d->by_sub, sub_idx) != 0;
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
  /* The subscriber is counted where its PRIMARY is, and the pin moves that:
     off the old block's address and onto this one's, or onto this one's
     alone when it held nothing (#603). */
  if (!q)
    bpa_dyn_count (d, local, 1);
  else if ((u32) (q[0] - 1) != local)
    {
      bpa_dyn_count (d, (u32) (q[0] - 1), -1);
      bpa_dyn_count (d, local, 1);
    }
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
    {
      hash_unset (d->by_sub, d->owner[local]);
      bpa_dyn_count (d, local, -1);
    }
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

  /*
   * EVERY SLOT, not every block of this worker's own: a borrowed block is
   * held by a subscriber exactly like an owned one, and a reap that stopped
   * at n_local would never release it - the block would be held for ever by
   * a subscriber that left, on a slice that cannot give it back because it
   * is not free (#210).
   */
  for (i = 0; i < bpa_dyn_slots (d); i++)
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
      out[freed].block_idx = bpa_dyn_block_at (d, i, worker, n_workers);

      /* Only the primary lives in by_sub; an extension has an entry in
       * extras instead, and removing the wrong one would strand the other. */
      if (hash_get (d->by_sub, d->owner[i])
	  && hash_get (d->by_sub, d->owner[i])[0] == (uword) i + 1)
	{
	  hash_unset (d->by_sub, d->owner[i]);
	  bpa_dyn_count (d, i, -1);
	}
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
