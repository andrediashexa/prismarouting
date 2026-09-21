/*
 * Which blocks move between workers' slices, and when (#210).
 *
 * WHAT THIS FILE IS FOR. A dynamic pool's blocks are partitioned per worker
 * and the subscriber-to-worker assignment is a hash; nothing makes the two
 * agree, so a subscriber is refused while blocks sit free next door. The
 * maintainer chose to move the block rather than the subscriber, under the
 * barrier `bpa_expire_process` already takes - and what moves is arithmetic
 * over counters, which is this.
 *
 * Every way of getting it wrong is quiet. A plan that lends a block the
 * lender itself needs moves the shortage instead of ending it and the next
 * tick moves it back, which is churn under a barrier that stops the world. A
 * plan that is not deterministic produces a different box every second and
 * no assertion can name the result. A plan with no bound holds that barrier
 * for as long as the counters say, which an operator measures as jitter.
 *
 * INCLUDED rather than linked, the way every harness here is.
 */
#include <stdio.h>
#include <string.h>

#include "bpa_shim.h"
#include "bpa_rebalance.c"

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

/* moved <plan> <n> <from> <to>: how many blocks the plan moves between two
   slices, summed, because a plan may reach the same pair twice. */
static u32
moved (const bpa_rebalance_move_t *m, u32 n, u32 from, u32 to)
{
  u32 i, t = 0;
  for (i = 0; i < n; i++)
    if (m[i].from == from && m[i].to == to)
      t += m[i].n;
  return t;
}

static u32
total (const bpa_rebalance_move_t *m, u32 n)
{
  u32 i, t = 0;
  for (i = 0; i < n; i++)
    t += m[i].n;
  return t;
}

int
main (void)
{
  bpa_rebalance_move_t plan[BPA_REBALANCE_MAX_MOVES];
  u32 n;

  printf ("=== the two amounts the plan is made of, on their own ===\n");
  {
    /*
     * Called alone, because everything below is the LOOP over them, and a
     * loop can be right about the wrong amounts: a plan that moves exactly
     * what `lendable` says will move too much if `lendable` is `free`.
     */
    bpa_rebalance_slice_t spare = { .free = 5, .demand = 2 };
    bpa_rebalance_slice_t lean = { .free = 2, .demand = 5 };
    bpa_rebalance_slice_t idle = { .free = 0, .demand = 0 };

    CHECK (lendable (&spare) == 3, "5 free and 2 refused lends 3, got %u",
	   lendable (&spare));
    CHECK (lendable (&lean) == 0, "a slice that is short lends nothing, got %u",
	   lendable (&lean));
    CHECK (lendable (&idle) == 0, "an empty slice lends nothing, got %u",
	   lendable (&idle));
    CHECK (wanting (&lean) == 3, "5 refused and 2 free asks for 3, got %u",
	   wanting (&lean));
    CHECK (wanting (&spare) == 0,
	   "a slice with blocks to spare asks for nothing, got %u",
	   wanting (&spare));
    CHECK (wanting (&idle) == 0, "an idle slice asks for nothing, got %u",
	   wanting (&idle));
  }

  printf ("=== the box #210 was measured on ===\n");
  {
    /*
     * Three blocks, two workers: the slices hold two and one, subscribers at
     * offsets 5, 6 and 9 land on workers 1, 0 and 1, and the third is
     * refused with a block free on worker 0. That is the measurement in the
     * issue, in counters: worker 0 has a block free and nobody asking,
     * worker 1 has nothing free and one refusal.
     */
    bpa_rebalance_slice_t s[2] = { { .free = 1, .demand = 0 },
				   { .free = 0, .demand = 1 } };
    n = bpa_rebalance_plan (s, 2, plan, BPA_REBALANCE_MAX_MOVES);
    CHECK (n == 1, "one move planned, got %u", n);
    CHECK (moved (plan, n, 0, 1) == 1,
	   "the free block goes from worker 0 to worker 1, got %u",
	   moved (plan, n, 0, 1));
  }

  printf ("\n=== a box where nobody was refused ===\n");
  {
    /*
     * THE COMMON CASE, and it has to cost nothing: every box that has never
     * exhausted a slice is this one, and it runs under a barrier once a
     * second for the life of the box.
     */
    bpa_rebalance_slice_t s[4] = { { .free = 10, .demand = 0 },
				   { .free = 3, .demand = 0 },
				   { .free = 0, .demand = 0 },
				   { .free = 7, .demand = 0 } };
    n = bpa_rebalance_plan (s, 4, plan, BPA_REBALANCE_MAX_MOVES);
    CHECK (n == 0, "a box with no demand plans nothing, got %u move(s)", n);
  }

  printf ("\n=== a box that is simply full ===\n");
  {
    /* Demand everywhere and nothing free is an exhausted pool, not an
       unbalanced one. Moving blocks between slices would refuse the same
       subscribers in a different order. */
    bpa_rebalance_slice_t s[3] = { { .free = 0, .demand = 4 },
				   { .free = 0, .demand = 2 },
				   { .free = 0, .demand = 9 } };
    n = bpa_rebalance_plan (s, 3, plan, BPA_REBALANCE_MAX_MOVES);
    CHECK (n == 0, "an exhausted pool plans nothing, got %u move(s)", n);
  }

  printf ("\n=== a lender keeps what it needs itself ===\n");
  {
    /*
     * Worker 0 refused two subscribers and has since freed three blocks, so
     * it can spare one. Lending all three would refuse its own two and pull
     * them back next tick - churn under the barrier, which is the cost this
     * mechanism exists to avoid.
     */
    bpa_rebalance_slice_t s[2] = { { .free = 3, .demand = 2 },
				   { .free = 0, .demand = 5 } };
    n = bpa_rebalance_plan (s, 2, plan, BPA_REBALANCE_MAX_MOVES);
    CHECK (total (plan, n) == 1,
	   "a lender with two refusals of its own lends 1 of its 3, moved %u",
	   total (plan, n));
  }

  printf ("\n=== a borrower asks only for what it cannot serve ===\n");
  {
    /*
     * Three refusals and two free blocks is one block short, not three.
     * Asking for three would take blocks no subscriber on this worker can
     * use, and they would sit there until a later tick moved them back.
     */
    bpa_rebalance_slice_t s[2] = { { .free = 9, .demand = 0 },
				   { .free = 2, .demand = 3 } };
    n = bpa_rebalance_plan (s, 2, plan, BPA_REBALANCE_MAX_MOVES);
    CHECK (total (plan, n) == 1,
	   "a borrower three short of nothing asks for 1, got %u",
	   total (plan, n));
  }

  printf ("\n=== the fullest lends to the neediest, and it is repeatable ===\n");
  {
    bpa_rebalance_slice_t s[4] = { { .free = 2, .demand = 0 },
				   { .free = 0, .demand = 5 },
				   { .free = 9, .demand = 0 },
				   { .free = 0, .demand = 1 } };
    bpa_rebalance_move_t again[BPA_REBALANCE_MAX_MOVES];
    u32 n2;

    n = bpa_rebalance_plan (s, 4, plan, BPA_REBALANCE_MAX_MOVES);
    CHECK (n >= 1 && plan[0].from == 2 && plan[0].to == 1,
	   "the first move is the fullest slice to the neediest, got %u->%u",
	   n ? plan[0].from : ~0u, n ? plan[0].to : ~0u);
    CHECK (total (plan, n) == 6,
	   "every refusal that can be served is served: 6 expected, got %u",
	   total (plan, n));

    /* THE SAME PLAN, TWICE. A rebalance whose result depended on where the
       scan started would produce a different box every tick, and no
       assertion here could name the outcome. */
    n2 = bpa_rebalance_plan (s, 4, again, BPA_REBALANCE_MAX_MOVES);
    CHECK (n2 == n && 0 == memcmp (plan, again, n * sizeof (plan[0])),
	   "the same counters plan the same moves twice");
  }

  printf ("\n=== a tie goes to the lower index, which is the determinism ===\n");
  {
    /*
     * Two lenders holding the same amount and two borrowers short by the
     * same amount. Something has to break the tie, and if it is the scan's
     * direction then the plan depends on how the loop was written rather
     * than on what the box holds - the same counters would give a different
     * answer after an edit nobody thought was a behaviour change.
     *
     * Measured with `>=` instead of `>` in the scan: every case above stays
     * green and this one flips, which is why it is here.
     */
    bpa_rebalance_slice_t s[4] = { { .free = 5, .demand = 0 },
				   { .free = 5, .demand = 0 },
				   { .free = 0, .demand = 2 },
				   { .free = 0, .demand = 2 } };
    n = bpa_rebalance_plan (s, 4, plan, BPA_REBALANCE_MAX_MOVES);
    CHECK (n >= 1 && plan[0].from == 0,
	   "the lower-indexed of two equal lenders goes first, got %u",
	   n ? plan[0].from : ~0u);
    CHECK (n >= 1 && plan[0].to == 2,
	   "and the lower-indexed of two equal borrowers is served first, "
	   "got %u",
	   n ? plan[0].to : ~0u);
  }

  printf ("\n=== nothing moves to itself ===\n");
  {
    /* A worker cannot be both the lender and the borrower: `lendable` and
       `wanting` cannot both be non-zero for one slice. Asserted rather than
       argued, because the loop would otherwise emit a move of a block from a
       slice to the same slice - which the barrier would apply, and which
       would look like a working rebalance that fixes nothing. */
    bpa_rebalance_slice_t s[1] = { { .free = 4, .demand = 7 } };
    u32 i;
    n = bpa_rebalance_plan (s, 1, plan, BPA_REBALANCE_MAX_MOVES);
    for (i = 0; i < n; i++)
      CHECK (plan[i].from != plan[i].to, "move %u is from %u to itself", i,
	     plan[i].from);
    CHECK (n == 0, "a single slice has nobody to trade with, got %u move(s)",
	   n);
  }

  printf ("\n=== the barrier is not held for as long as the counters say ===\n");
  {
    /*
     * Every move is a write into two slices while the world is stopped. With
     * one lender and many borrowers the greedy loop would emit one move per
     * borrower, so a box with a hundred workers would hold the barrier a
     * hundred writes long - and the tick comes round again in a second
     * anyway.
     */
    bpa_rebalance_slice_t s[BPA_REBALANCE_MAX_MOVES + 4];
    u32 i;

    memset (s, 0, sizeof (s));
    s[0].free = 1000;
    for (i = 1; i < BPA_REBALANCE_MAX_MOVES + 4; i++)
      s[i].demand = 1;
    n = bpa_rebalance_plan (s, BPA_REBALANCE_MAX_MOVES + 4, plan,
			    BPA_REBALANCE_MAX_MOVES);
    CHECK (n == BPA_REBALANCE_MAX_MOVES,
	   "the plan stops at the cap of %u, got %u",
	   (u32) BPA_REBALANCE_MAX_MOVES, n);
  }

  printf ("\n=== and it answers rather than crashing ===\n");
  {
    bpa_rebalance_slice_t s[2] = { { .free = 5, .demand = 0 },
				   { .free = 0, .demand = 5 } };
    CHECK (bpa_rebalance_plan (0, 2, plan, BPA_REBALANCE_MAX_MOVES) == 0,
	   "no slices at all");
    CHECK (bpa_rebalance_plan (s, 2, 0, BPA_REBALANCE_MAX_MOVES) == 0,
	   "nowhere to write the plan");
    CHECK (bpa_rebalance_plan (s, 2, plan, 0) == 0, "room for no moves");
    CHECK (bpa_rebalance_plan (s, 0, plan, BPA_REBALANCE_MAX_MOVES) == 0,
	   "a box with no workers");
  }

  printf (fail ? "\n%d FAILURES\n" : "\nALL TESTS PASSED\n", fail);
  return fail ? 1 : 0;
}
