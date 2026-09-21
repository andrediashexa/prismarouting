/*
 * bpa_lent.c - the loan book: which worker is holding a block it does not own.
 *
 * See bpa.h for why it exists and bpa_rebalance.h for what decides the loans.
 * Three writers, all of them under vlib_worker_thread_barrier_sync(), and one
 * reader on the handoff - which is the last place a packet can still be sent
 * to another worker.
 */
/*
 * BPA_STANDALONE is the seam every file here has: the harness brings the two
 * lines of bpa.h this needs and the shim's vectors and hashes, because
 * bpa.h itself reaches for vlib and none of the loan book does.
 */
#ifdef BPA_STANDALONE
#include "bpa_shim.h"
#include "bpa_vec_shim.h"
#else
#include "bpa.h"
#endif

void
bpa_lent_set (bpa_main_t *bm, u32 pool_index, u32 block_idx, u32 worker)
{
  u64 key = bpa_lent_key (pool_index, block_idx);
  uword *q;

  /*
   * Created on the first loan and not at init, because the table is empty on
   * every box that never exhausts a slice - which is most of them - and an
   * allocation made once per box at startup for a feature it will never use
   * is a cost with nobody to charge it to.
   */
  if (!bm->lent_to)
    bm->lent_to = hash_create (0, sizeof (uword));
  q = hash_get (bm->lent_to, key);

  /*
   * Counted on the transition, not on the call. A loan written twice - the
   * same block moved to the same worker by two ticks - would otherwise leave
   * n_lent_total above the number of entries for ever, and the fast path's
   * "is there anything here at all" would answer yes on a box whose last
   * loan came home years ago.
   */
  if (!q)
    bm->n_lent_total++;
  hash_set (bm->lent_to, key, (uword) worker + 1);
}

void
bpa_lent_clear (bpa_main_t *bm, u32 pool_index, u32 block_idx)
{
  u64 key = bpa_lent_key (pool_index, block_idx);

  if (!hash_get (bm->lent_to, key))
    return;
  hash_unset (bm->lent_to, key);
  bm->n_lent_total--;
}

void
bpa_lent_clear_pool (bpa_main_t *bm, u32 pool_index)
{
  u64 *keys = 0, *k;
  u64 key;
  uword val;

  /*
   * A POOL BEING REPLACED HAS NO LOANS, and leaving them would be worse than
   * useless: the new pool's blocks start at index zero like the old one's, so
   * a stale entry would steer a reply for a block that means something else
   * now to a worker that never borrowed anything.
   *
   * Collected first and unset after, because hash_foreach is not a place to
   * delete from the table it is walking.
   */
  /* *INDENT-OFF* */
  hash_foreach (key, val, bm->lent_to, ({
    (void) val;
    if ((u32) (key >> 32) == pool_index)
      vec_add1 (keys, key);
  }));
  /* *INDENT-ON* */

  vec_foreach (k, keys)
    {
      hash_unset (bm->lent_to, k[0]);
      bm->n_lent_total--;
    }
  vec_free (keys);
}

#ifndef BPA_STANDALONE
/*
 * THE TICK THAT MOVES A BLOCK (#210).
 *
 * Called from bpa_expire_process, under the barrier it already takes and
 * AFTER the reap: the counters this reads - what a slice has free, what it
 * refused - are only current once the blocks whose hold-down ran out are
 * back on their free lists.
 *
 * It decides nothing itself. bpa_rebalance_plan() is the arithmetic, with a
 * harness of its own; this is the part that needs a box.
 */
#include "bpa_rebalance.h"

/*
 * WHAT A SLICE HAS FREE, and the arithmetic is bpa_rebalance.c's so that it
 * can be driven without a box.
 *
 * DELIBERATELY UNDER-COUNTED: n_alloc counts the BORROWED blocks this worker
 * is holding as well as its own, so a worker holding three borrowed blocks
 * reads three of its own as unavailable when they are free. The exact number
 * would need a second counter maintained in the allocator, and the error is
 * in the safe direction - a slice lends less than it could, never more than
 * it has. The next tick comes round in a second.
 */
/* not a unit: it is bpa_rebalance_spare() applied to one slice's four
 * fields, and that function is driven on its own in
 * test/test_bpa_rebalance.c. A harness for this one would be a harness for
 * the field names. */
static u32
bpa_slice_free (const bpa_dyn_t *d)
{
  return bpa_rebalance_spare (d->n_local, d->n_alloc, d->n_lent, d->n_peak);
}

/* not a unit: it needs a barrier, every worker's slice and a pool table, and
 * it is driven where those exist - lab/dynamic.sh turns the mechanism on,
 * fills a slice, and reads the block that moved. What it DECIDES is
 * bpa_rebalance_plan() and bpa_rebalance_spare(), both of which have a
 * harness of their own. */
void
bpa_rebalance_tick (bpa_main_t *bm)
{
  u32 pool_index;

  /* OFF UNTIL ASKED. See bpa_main_t.rebalance_on for the two measurements
     that put it behind a switch rather than on by default. */
  if (!bm->rebalance_on)
    return;

  /* Nothing to balance on a box with one worker: there is no second slice
     for a block to come from, and the whole mechanism is the difference
     between slices. */
  if (bm->n_workers < 2)
    return;

  /* *INDENT-OFF* */
  pool_foreach_index (pool_index, bm->pools)
    {
  /* *INDENT-ON* */
      bpa_pool_t *p = pool_elt_at_index (bm->pools, pool_index);
      bpa_rebalance_slice_t slices[BPA_REBALANCE_MAX_SLICES];
      bpa_rebalance_move_t plan[BPA_REBALANCE_MAX_MOVES];
      bpa_dyn_t *dyn[BPA_REBALANCE_MAX_SLICES];
      u32 n_slices = 0, n_moves, m, i;
      /* What each slice actually received, which is what its refusals are
         forgiven by. A move that was planned and then refused by the
         borrower forgives nothing. */
      u32 served[BPA_REBALANCE_MAX_SLICES];

      /* Deterministic pools shard their RESERVE the same way, and the same
         defect applies to it - but a reserve index is not a block index and
         the return path reads it through bpa_reserve_index_of(), so lending
         one would need the loan book keyed differently. Dynamic first,
         because that is where #210 was measured. */
      if (p->mode != BPA_MODE_DYNAMIC)
	continue;

      clib_memset (slices, 0, sizeof (slices));
      clib_memset (served, 0, sizeof (served));
      clib_memset (dyn, 0, sizeof (dyn));

      for (i = 0; i < vec_len (bm->per_thread); i++)
	{
	  bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, i);
	  bpa_dyn_t *d;
	  u32 w, nw, demand;

	  if (pool_index >= vec_len (pt->dyn))
	    continue;
	  d = vec_elt_at_index (pt->dyn, pool_index);
	  if (!d->active)
	    continue;
	  bpa_worker_id (bm, i, &w, &nw);
	  if (w >= BPA_REBALANCE_MAX_SLICES)
	    continue;

	  /*
	   * REFUSALS SINCE THE LAST TICK, not the lifetime count: a worker
	   * that was short an hour ago and has been served since would
	   * otherwise pull blocks towards itself for ever, and the pull
	   * would grow with the age of the box.
	   */
	  demand = (u32) (d->n_exhausted - d->n_exhausted_seen);
	  /*
	   * NOT CONSUMED HERE. A refusal this tick cannot act on has to
	   * survive to the next one, and the first tick after a refusal is
	   * exactly the tick that cannot act: the lender's spare is what was
	   * free at the PREVIOUS tick, and a box that has just started
	   * refusing has no previous reading. Advanced below by what was
	   * actually served, so an unserved refusal is still demand a second
	   * later.
	   *
	   * Measured before it was: the tick consumed the demand, the next
	   * tick saw none, and the subscriber stayed refused with a block
	   * standing free next door - #210 exactly, produced by the
	   * mechanism built to end it.
	   */

	  /*
	   * FREE NOW *AND* AT THE LAST TICK. Measured: lending on the same
	   * pass as the first refusal takes blocks the lender's own
	   * subscribers are about to ask for, and the dynamic lab went from
	   * one refusal to two - the mechanism built to recover a refused
	   * subscriber cost one. A block has to have sat unused across a
	   * whole interval before it counts as spare.
	   */
	  /* The mark first, so this tick's reading includes what the slice
	     is holding right now. */
	  if (d->n_alloc > d->n_peak)
	    d->n_peak = d->n_alloc;
	  else if (d->n_peak > 0)
	    d->n_peak--;

	  {
	    u32 free_now = bpa_slice_free (d);

	    slices[w].free = free_now < d->n_free_seen ? free_now
						       : d->n_free_seen;
	    d->n_free_seen = free_now;
	  }
	  slices[w].demand = demand;
	  dyn[w] = d;
	  if (w + 1 > n_slices)
	    n_slices = w + 1;
	}

      if (n_slices < 2)
	continue;

      n_moves = bpa_rebalance_plan (slices, n_slices, plan,
				    BPA_REBALANCE_MAX_MOVES);
      if (n_moves || slices[0].demand || slices[1].demand)
	vlib_log_notice (bm->log_class,
			 "pool %u rebalance: free %u/%u demand %u/%u -> "
			 "%u move(s)",
			 pool_index, slices[0].free, slices[1].free,
			 slices[0].demand, slices[1].demand, n_moves);

      for (m = 0; m < n_moves; m++)
	{
	  u32 blocks[BPA_REBALANCE_MAX_MOVES];
	  u32 want = plan[m].n, got, k;

	  if (want > ARRAY_LEN (blocks))
	    want = ARRAY_LEN (blocks);
	  got = bpa_dyn_lend (dyn[plan[m].from], plan[m].from, bm->n_workers,
			      want, blocks);

	  for (k = 0; k < got; k++)
	    {
	      /*
	       * AND IF THE BORROWER WILL NOT TAKE IT, IT GOES STRAIGHT BACK.
	       * A block that left its slice and arrived nowhere is a block
	       * no subscriber can ever be given again - the quietest way for
	       * this mechanism to lose the capacity it exists to recover.
	       */
	      if (bpa_dyn_borrow (dyn[plan[m].to], blocks[k]) != 0)
		{
		  bpa_dyn_take_back (dyn[plan[m].from], blocks[k],
				     bm->n_workers);
		  continue;
		}
	      bpa_lent_set (bm, pool_index, blocks[k], plan[m].to);
	      served[plan[m].to]++;
	      vlib_log_notice (bm->log_class,
			      "pool %u: block %u lent from worker %u to %u",
			      pool_index, blocks[k], plan[m].from,
			      plan[m].to);
	    }
	}

      /*
       * AND THE DEMAND EACH SLICE WAS ACTUALLY SERVED IS FORGIVEN, the rest
       * carried forward: a refusal nobody could act on is still a refusal
       * next tick, which is what lets the loan happen on the tick after the
       * one that first saw it.
       */
      for (i = 0; i < n_slices; i++)
	{
	  u32 unserved;

	  if (!dyn[i])
	    continue;
	  unserved = slices[i].demand > served[i]
		       ? slices[i].demand - served[i]
		       : 0;
	  dyn[i]->n_exhausted_seen = dyn[i]->n_exhausted - unserved;
	}

      /*
       * AND THE LOANS THAT ARE NO LONGER NEEDED GO HOME.
       *
       * A borrower with nothing of its own free is still short and keeps
       * what it has. One with its own blocks free does not need somebody
       * else's, and holding it would be this mechanism taking capacity from
       * the worker that owns it - the defect it exists to fix, pointing the
       * other way.
       *
       * Only blocks that are FREE move: a held one is a subscriber's, and
       * bpa_dyn_give_back refuses it. The next tick asks again.
       */
      for (i = 0; i < n_slices; i++)
	{
	  bpa_dyn_t *d = dyn[i];
	  u32 k;

	  if (!d)
	    continue;
	  /*
	   * A LOAN NOBODY USED GOES STRAIGHT HOME (#210).
	   *
	   * The demand that justified it was a refusal in the last interval,
	   * and the subscriber behind it may never come back - while the
	   * lender's own next subscriber is usually seconds away. Measured:
	   * a loan made on that signal cost a live allocation in
	   * `LAB_WORKERS=1 sh tools/lab.sh dynamic`, where the box went from
	   * one refusal to two.
	   *
	   * So a borrowed block that is still free one whole interval after
	   * it arrived is given back. It bounds the damage to a single
	   * interval and makes the mechanism self-correcting: a borrower
	   * that really needed the block is holding it by now, and one that
	   * did not has stopped costing the lender anything.
	   */
	  for (k = 0; k < vec_len (d->foreign); k++)
	    {
	      u32 blk = d->foreign[k];
	      u32 owner;

	      if (blk == BPA_NO_BLOCK)
		continue;
	      if (d->owner[d->n_local + k] != BPA_NO_BLOCK)
		{
		  /* In use: it is doing the job it was borrowed for. */
		  d->foreign_idle[k] = 0;
		  continue;
		}
	      if (d->foreign_idle[k] < BPA_LOAN_GRACE_TICKS)
		{
		  /*
		   * Free, and still inside the window the subscriber that
		   * caused the loan has to come back in. A block returned on
		   * the next tick is available for one second, which is
		   * shorter than every retry a client makes.
		   */
		  d->foreign_idle[k]++;
		  continue;
		}
	      if (bpa_dyn_give_back (d, blk) != 0)
		continue;
	      owner = blk % bm->n_workers;
	      if (owner < n_slices && dyn[owner])
		bpa_dyn_take_back (dyn[owner], blk, bm->n_workers);
	      bpa_lent_clear (bm, pool_index, blk);
	      vlib_log_notice (bm->log_class,
			      "pool %u: block %u went home unused from "
			      "worker %u",
			      pool_index, blk, i);
	    }

	  if (slices[i].demand != 0 || bpa_slice_free (d) == 0)
	    continue;
	  for (k = 0; k < vec_len (d->foreign); k++)
	    {
	      u32 blk = d->foreign[k];
	      u32 owner;

	      if (blk == BPA_NO_BLOCK)
		continue;
	      if (bpa_dyn_give_back (d, blk) != 0)
		continue;
	      owner = blk % bm->n_workers;
	      if (owner < n_slices && dyn[owner])
		bpa_dyn_take_back (dyn[owner], blk, bm->n_workers);
	      bpa_lent_clear (bm, pool_index, blk);
	    }
	}
  /* *INDENT-OFF* */
    }
  /* *INDENT-ON* */
}
#endif /* BPA_STANDALONE */
