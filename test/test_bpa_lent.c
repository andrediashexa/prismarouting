/*
 * The loan book: which worker is holding a block it does not own (#210).
 *
 * WHAT THIS FILE IS FOR. A dynamic pool's return path steers by arithmetic -
 * the block's own index says which worker owns it - and a subscriber may hold
 * only a block congruent to itself, so the reply lands where the session is.
 * Lending breaks that deliberately, and this table is what the handoff reads
 * to send the reply to the borrower instead.
 *
 * Every way of getting it wrong is a packet delivered to a worker with no
 * session for it, which reads on a box as a subscriber whose traffic works in
 * one direction. And the counter is not bookkeeping: it is the fast path -
 * the handoff tests it before it looks at anything else, so a count that
 * drifts above zero turns a comparison into a hash lookup on every packet of
 * every box, for ever.
 *
 * INCLUDED rather than linked, the way every harness here is.
 */
#include <stdio.h>
#include <string.h>

#include "bpa_shim.h"
#include "bpa_vec_shim.h"

/* The two lines of bpa.h this needs, rather than the whole plugin: that
   header reaches for vlib, and none of the loan book does. */
typedef struct
{
  uword *lent_to;
  u32 n_lent_total;
} bpa_main_t;

static_always_inline u64
bpa_lent_key (u32 pool_index, u32 block_idx)
{
  return ((u64) pool_index << 32) | block_idx;
}

static_always_inline int
bpa_lent_worker (const bpa_main_t *bm, u32 pool_index, u32 block_idx,
		 u32 *worker)
{
  uword *q = hash_get (bm->lent_to, bpa_lent_key (pool_index, block_idx));

  if (PREDICT_TRUE (q == 0))
    return -1;
  *worker = (u32) (q[0] - 1);
  return 0;
}

#include "bpa_lent.c"

static int fail;

#define CHECK(cond, ...)                                                      \
  do                                                                          \
    {                                                                         \
      if (cond)                                                               \
	printf ("  [ ok ] " __VA_ARGS__), printf ("\n");                      \
      else                                                                    \
	fail++, printf ("  FAIL: " __VA_ARGS__), printf ("\n");               \
    }                                                                         \
  while (0)

int
main (void)
{
  bpa_main_t bm;
  u32 w = ~0u;

  clib_memset (&bm, 0, sizeof (bm));

  printf ("=== a box with no loans answers without looking ===\n");
  CHECK (bm.n_lent_total == 0, "an empty book counts %u", bm.n_lent_total);
  CHECK (bpa_lent_worker (&bm, 0, 7, &w) != 0,
	 "an empty book named a worker for a block nobody borrowed");

  printf ("\n=== a loan is written and read back ===\n");
  bpa_lent_set (&bm, 3, 42, 1);
  CHECK (bm.n_lent_total == 1, "one loan counts %u", bm.n_lent_total);
  CHECK (bpa_lent_worker (&bm, 3, 42, &w) == 0 && w == 1,
	 "block 42 of pool 3 is held by worker %u", w);

  printf ("\n=== the pool is part of the key ===\n");
  /* Two pools' blocks both start at zero, so a book keyed by block alone
     would send a reply for one pool's block to the borrower of another's. */
  CHECK (bpa_lent_worker (&bm, 4, 42, &w) != 0,
	 "block 42 of pool 4 answered with pool 3's borrower");
  bpa_lent_set (&bm, 4, 42, 2);
  CHECK (bpa_lent_worker (&bm, 3, 42, &w) == 0 && w == 1,
	 "pool 3's loan changed to worker %u when pool 4 borrowed", w);
  CHECK (bpa_lent_worker (&bm, 4, 42, &w) == 0 && w == 2,
	 "pool 4's block is held by worker %u", w);
  CHECK (bm.n_lent_total == 2, "two loans count %u", bm.n_lent_total);

  printf ("\n=== a loan written twice is one loan ===\n");
  /*
   * THE COUNTER IS THE FAST PATH. A count that drifts above the number of
   * entries leaves every box testing a number that never returns to zero,
   * and the comparison the whole mechanism is free on becomes a hash lookup
   * on every packet.
   */
  bpa_lent_set (&bm, 3, 42, 1);
  CHECK (bm.n_lent_total == 2, "rewriting a loan counted it again: %u",
	 bm.n_lent_total);
  bpa_lent_set (&bm, 3, 42, 5);
  CHECK (bm.n_lent_total == 2, "moving a loan counted it again: %u",
	 bm.n_lent_total);
  CHECK (bpa_lent_worker (&bm, 3, 42, &w) == 0 && w == 5,
	 "a block moved to another borrower is held by worker %u", w);

  printf ("\n=== a loan comes home ===\n");
  bpa_lent_clear (&bm, 3, 42);
  CHECK (bm.n_lent_total == 1, "after one came home the book counts %u",
	 bm.n_lent_total);
  CHECK (bpa_lent_worker (&bm, 3, 42, &w) != 0,
	 "a block that came home still names worker %u", w);
  /* Twice, which the rebalance can do: a block given back and the entry
     cleared again on the next tick. The count must not go below what the
     book holds, or the fast path stops testing anything. */
  bpa_lent_clear (&bm, 3, 42);
  CHECK (bm.n_lent_total == 1, "clearing twice counted %u", bm.n_lent_total);

  printf ("\n=== a pool being replaced gives up every loan ===\n");
  bpa_lent_set (&bm, 4, 1, 0);
  bpa_lent_set (&bm, 4, 3, 1);
  bpa_lent_set (&bm, 9, 1, 1);
  CHECK (bm.n_lent_total == 4, "four loans count %u", bm.n_lent_total);
  bpa_lent_clear_pool (&bm, 4);
  CHECK (bm.n_lent_total == 1,
	 "pool 4 gave up its loans and the book counts %u", bm.n_lent_total);
  CHECK (bpa_lent_worker (&bm, 4, 42, &w) != 0
	   && bpa_lent_worker (&bm, 4, 1, &w) != 0
	   && bpa_lent_worker (&bm, 4, 3, &w) != 0,
	 "a block of the replaced pool still names a borrower");
  CHECK (bpa_lent_worker (&bm, 9, 1, &w) == 0 && w == 1,
	 "another pool's loan went with it: worker %u", w);

  printf ("\n=== and a pool that borrowed nothing gives up nothing ===\n");
  bpa_lent_clear_pool (&bm, 77);
  CHECK (bm.n_lent_total == 1, "clearing an untouched pool left %u",
	 bm.n_lent_total);

  printf (fail ? "\n%d FAILURES\n" : "\nALL TESTS PASSED\n", fail);
  return fail ? 1 : 0;
}
