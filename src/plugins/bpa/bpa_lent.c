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
