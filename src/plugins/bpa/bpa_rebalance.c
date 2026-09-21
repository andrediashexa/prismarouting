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
