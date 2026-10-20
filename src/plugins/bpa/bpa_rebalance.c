/*
 * bpa_rebalance.c - see bpa_rebalance.h for what this decides and why.
 *
 * Greedy, and deterministic on purpose: the fullest slice lends to the
 * neediest, in chunks, until one side runs out. Determinism is what makes it
 * testable at all - a rebalance that depended on iteration order would
 * produce a different box every tick and no assertion could name the result.
 */
#ifdef BPA_STANDALONE
#include "bpa_shim.h"
#else
#include <vlib/vlib.h>
#endif

#include "bpa_rebalance.h"
#include "bpa_demand.h"

u32
bpa_rebalance_spare (u32 n_local, u32 n_alloc, u32 n_lent, u32 peak)
{
  /*
   * AGAINST THE PEAK, not against what is held this instant: a slice whose
   * blocks are free right now may be one whose subscribers are between
   * packets, and lending one then is what cost a live allocation when this
   * was measured - the dynamic lab went from 3 allocations and 1 refusal to
   * 2 and 2.
   */
  u32 need = n_alloc > peak ? n_alloc : peak;
  u32 spoken_for = need + n_lent;

  return n_local > spoken_for ? n_local - spoken_for : 0;
}

/*
 * WHAT A SLICE MAY LEND is what it holds beyond its own refusals.
 *
 * A worker that refused somebody and has since freed a block needs that
 * block: lending it would move the shortage rather than end it, and the next
 * tick would move it back - which is churn under a barrier that stops the
 * world, the one cost this whole mechanism is trying not to pay.
 *
 * Measured against the alternative on paper: with `free` alone as the
 * lendable amount, a two-worker box where both refused one subscriber and
 * both then freed one block lends in both directions on the same tick.
 */
static u32
lendable (const bpa_rebalance_slice_t *s)
{
  return s->free > s->demand ? s->free - s->demand : 0;
}

/*
 * WHAT A SLICE STILL NEEDS is its refusals beyond what it already holds.
 *
 * A worker that refused three subscribers and now has two free blocks will
 * serve two of them without help, so it asks for one. Asking for three would
 * take blocks that no subscriber on this worker can use, and they would sit
 * there until a later tick moved them back.
 */
static u32
wanting (const bpa_rebalance_slice_t *s)
{
  return s->demand > s->free ? s->demand - s->free : 0;
}

u32
bpa_rebalance_plan (const bpa_rebalance_slice_t *slices, u32 n_slices,
		    bpa_rebalance_move_t *out, u32 max_moves)
{
  u32 n_out = 0;
  u32 i;
  /*
   * The working copies. The caller's slices are what the box holds and are
   * not this function's to edit - it decides, and the barrier applies - so
   * the amounts are drawn down here as the plan is built. Bounded by the
   * worker count, which is bounded by the machine's cores.
   */
  u32 lend[BPA_REBALANCE_MAX_SLICES];
  u32 need[BPA_REBALANCE_MAX_SLICES];

  if (!slices || !out || max_moves == 0)
    return 0;
  if (n_slices > BPA_REBALANCE_MAX_SLICES)
    n_slices = BPA_REBALANCE_MAX_SLICES;

  for (i = 0; i < n_slices; i++)
    {
      lend[i] = lendable (&slices[i]);
      need[i] = wanting (&slices[i]);
    }

  while (n_out < max_moves)
    {
      u32 src = ~0u, dst = ~0u, n;

      /*
       * The fullest lender and the neediest borrower, lowest index first on
       * a tie. `>` rather than `>=` is what makes the tie go to the lower
       * index, and that is the whole of the determinism: with `>=` the same
       * counters would produce a different plan depending on which end the
       * scan started from.
       */
      for (i = 0; i < n_slices; i++)
	{
	  if (lend[i] > 0 && (src == ~0u || lend[i] > lend[src]))
	    src = i;
	  if (need[i] > 0 && (dst == ~0u || need[i] > need[dst]))
	    dst = i;
	}

      /* One side is out: there is nothing left to decide. */
      if (src == ~0u || dst == ~0u)
	break;

      /*
       * A slice cannot lend to itself, and this is not impossible input: a
       * worker with three free blocks and four refusals is both the fullest
       * lender and the neediest borrower on a box where nobody else has
       * either. `lendable` and `wanting` cannot both be non-zero for one
       * slice - one of them is always zero by their arithmetic - so reaching
       * here means the two winners are different slices, and this stops the
       * loop rather than pretending otherwise if that ever stops holding.
       */
      if (src == dst)
	break;

      n = lend[src] < need[dst] ? lend[src] : need[dst];
      out[n_out].from = src;
      out[n_out].to = dst;
      out[n_out].n = n;
      n_out++;
      lend[src] -= n;
      need[dst] -= n;
    }

  return n_out;
}

u32
bpa_demand_tick (bpa_demand_t *dm, int (*holds) (void *, u32), void *ctx)
{
  u32 keep_key[BPA_DEMAND_MAX];
  u8 keep_age[BPA_DEMAND_MAX], keep_extra[BPA_DEMAND_MAX];
  u32 n_keep = 0, s, demand;

  if (!dm)
    return 0;
  if (dm->n == 0 && dm->n_overflow == 0)
    return 0;

  /*
   * WHAT STILL COUNTS: refused inside the window, and - for a first block -
   * still holding none. Measured before the second test existed (#611): a
   * subscriber served by a loan stayed demand until somebody subtracted the
   * loan, and the subtraction was the only thing that ever forgave anything.
   * Asking the slice whether the subscriber holds a block now is the answer
   * itself rather than a bookkeeping of it.
   */
  for (s = 0; s < BPA_DEMAND_SLOTS && n_keep < BPA_DEMAND_MAX; s++)
    {
      u32 key = dm->key[s];

      if (key == 0)
	continue;
      if (dm->age[s] >= BPA_DEMAND_WINDOW)
	continue;
      if (!dm->extra[s] && holds && holds (ctx, key - 1))
	continue;
      keep_key[n_keep] = key;
      keep_age[n_keep] = dm->age[s] + 1;
      keep_extra[n_keep] = dm->extra[s];
      n_keep++;
    }
  demand = n_keep + (dm->n_overflow ? 1 : 0);

  /*
   * REBUILT RATHER THAN DELETED FROM. Open addressing cannot simply empty a
   * slot: a later key whose probe run passed through it would no longer be
   * found, and the packet path would insert it a second time - one
   * subscriber counted twice, the defect this replaces. Under the barrier,
   * with at most BPA_DEMAND_MAX survivors, the rebuild is a few hundred
   * stores once a second, and only on a slice that refused somebody.
   */
  clib_memset (dm, 0, sizeof (*dm));
  for (s = 0; s < n_keep; s++)
    {
      u32 t = bpa_demand_slot (keep_key[s]), i;

      /* No further from home than the packet path looks: an entry it
	 cannot find is one it would insert again. At half load a run that
	 long does not happen in practice, and an entry dropped here is back
	 on its subscriber's next refused packet. */
      for (i = 0; i < BPA_DEMAND_PROBES && dm->key[t] != 0; i++)
	t = (t + 1) & (BPA_DEMAND_SLOTS - 1);
      if (dm->key[t] != 0)
	continue;
      dm->key[t] = keep_key[s];
      dm->age[t] = keep_age[s];
      dm->extra[t] = keep_extra[s];
      dm->n++;
    }
  return demand;
}

void
bpa_demand_forget (bpa_demand_t *dm)
{
  /*
   * THE BASELINE MOVES WHILE THE SWITCH IS OFF (#611). Before, nothing
   * advanced it, and `bpa rebalance enable` on a box that had been up a
   * while read every refusal since boot as this second's demand - measured
   * at `demand 1048/242` on the first tick of #475's on-at-180-s runs.
   * Emptied, the first tick after enabling sees the last second only.
   */
  if (dm && (dm->n || dm->n_overflow))
    clib_memset (dm, 0, sizeof (*dm));
}
