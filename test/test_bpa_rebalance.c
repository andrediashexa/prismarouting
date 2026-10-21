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

/* holds_odd: the stand-in for bpa_dyn_holds - an odd subscriber holds a
   block, an even one does not. */
static int
holds_odd (void *ctx, u32 sub_idx)
{
  (void) ctx;
  return (sub_idx & 1) != 0;
}

int
main (void)
{
  bpa_rebalance_move_t plan[BPA_REBALANCE_MAX_MOVES];
  u32 n;

  printf ("=== what a slice may lend, on its own ===\n");
  {
    /*
     * The four numbers, and each of them is a way to lend a block that
     * should have stayed: the slice's own size, what it is holding, what it
     * has already lent, and what it has NEEDED recently. The last is the
     * one the measurement added - a slice whose blocks are free this
     * instant may be one whose subscribers are between packets, and lending
     * then cost a live allocation (#210).
     */
    CHECK (bpa_rebalance_spare (4, 1, 0, 0) == 3,
	   "4 blocks with 1 held lends 3, got %u",
	   bpa_rebalance_spare (4, 1, 0, 0));
    CHECK (bpa_rebalance_spare (4, 1, 0, 4) == 0,
	   "a slice that has needed all four lends nothing, got %u",
	   bpa_rebalance_spare (4, 1, 0, 4));
    CHECK (bpa_rebalance_spare (4, 0, 0, 2) == 2,
	   "a slice idle now that recently held 2 lends 2, got %u",
	   bpa_rebalance_spare (4, 0, 0, 2));
    CHECK (bpa_rebalance_spare (4, 3, 0, 1) == 1,
	   "what is held beats a stale peak: got %u",
	   bpa_rebalance_spare (4, 3, 0, 1));
    CHECK (bpa_rebalance_spare (4, 1, 2, 1) == 1,
	   "blocks already lent are not lent twice, got %u",
	   bpa_rebalance_spare (4, 1, 2, 1));
    CHECK (bpa_rebalance_spare (2, 3, 0, 0) == 0,
	   "a slice holding more than it has lends nothing rather than "
	   "wrapping, got %u", bpa_rebalance_spare (2, 3, 0, 0));
  }

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


  printf ("\n=== the demand is subscribers, not packets (#611) ===\n");
  {
    /*
     * One refused connect is three SYNs. The count this replaced read them
     * as three subscribers, and on #475's bench the crowded slice's demand
     * stood at 1835 nine minutes in with forty blocks in the slice.
     */
    bpa_demand_t dm;
    u32 i, d;

    memset (&dm, 0, sizeof (dm));
    for (i = 0; i < 3; i++)
      bpa_demand_note (&dm, 42, 0);
    CHECK (dm.n == 1, "three refused packets of one subscriber are %u "
	   "entries, not 1", dm.n);
    for (i = 0; i < 10; i++)
      bpa_demand_note (&dm, 1000 + 2 * i, 0);
    d = bpa_demand_tick (&dm, 0, 0);
    CHECK (d == 11, "eleven subscribers refused read as demand %u", d);
    CHECK (bpa_demand_tick (0, 0, 0) == 0, "no set at all answers 0");
  }

  printf ("\n=== and it ages: a refusal stops counting when the subscriber "
	  "stops asking ===\n");
  {
    /*
     * The old count was forgiven only by a loan, so a lender that refused
     * its own subscribers once read demand above its free blocks from then
     * on and never lent again: every loan of #475's on-from-start runs was
     * in their first ten seconds.
     */
    bpa_demand_t dm;
    u32 t, d = 0, last = 0;

    memset (&dm, 0, sizeof (dm));
    bpa_demand_note (&dm, 7, 0);
    for (t = 1; t <= BPA_DEMAND_WINDOW + 2; t++)
      {
	d = bpa_demand_tick (&dm, 0, 0);
	if (d)
	  last = t;
      }
    CHECK (last == BPA_DEMAND_WINDOW,
	   "a single refusal counted for %u ticks, not the window's %u",
	   last, (u32) BPA_DEMAND_WINDOW);
    CHECK (d == 0 && dm.n == 0, "and after the window it is gone: demand %u, "
	   "%u entries", d, dm.n);

    /* A subscriber still retrying stays: a refusal every other tick keeps
       it in the window as long as it lasts. */
    for (t = 0; t < 4 * BPA_DEMAND_WINDOW; t++)
      {
	if (t % 2 == 0)
	  bpa_demand_note (&dm, 7, 0);
	d = bpa_demand_tick (&dm, 0, 0);
	if (d != 1)
	  break;
      }
    CHECK (t == 4 * BPA_DEMAND_WINDOW, "a subscriber retrying every two "
	   "ticks dropped out of the window at tick %u", t);

    /* And the rebuild keeps it findable: the next refused packet of a
       survivor refreshes it rather than adding it twice. */
    bpa_demand_note (&dm, 7, 0);
    CHECK (dm.n == 1, "a survivor of the rebuild was inserted again: %u "
	   "entries", dm.n);
  }

  printf ("\n=== a first block that has since been served stops counting "
	  "===\n");
  {
    bpa_demand_t dm;
    u32 d;

    memset (&dm, 0, sizeof (dm));
    bpa_demand_note (&dm, 2, 0); /* even: still holds nothing */
    bpa_demand_note (&dm, 3, 0); /* odd: holds a block now */
    bpa_demand_note (&dm, 5, 1); /* odd, but refused an EXTENSION */
    d = bpa_demand_tick (&dm, holds_odd, 0);
    CHECK (d == 2, "served first block dropped, the unserved one and the "
	   "extension kept: demand %u, not 2", d);
    CHECK (dm.n == 2, "%u entries survive, not 2", dm.n);
  }

  printf ("\n=== the switch off empties it, so enabling starts from now "
	  "===\n");
  {
    /*
     * `bpa rebalance enable` on a running box used to read every refusal
     * since boot as this second's demand - `demand 1048/242` on the first
     * tick of #475's on-at-180-s runs.
     */
    bpa_demand_t dm;
    u32 i;

    memset (&dm, 0, sizeof (dm));
    for (i = 0; i < 30; i++)
      bpa_demand_note (&dm, i, 0);
    bpa_demand_forget (&dm);
    CHECK (dm.n == 0 && bpa_demand_tick (&dm, 0, 0) == 0,
	   "forgotten, the set still holds %u entries", dm.n);
    bpa_demand_forget (0);
    CHECK (1, "forgetting no set at all does not crash");
  }

  printf ("\n=== and it is bounded: memory, and the probes a refused packet "
	  "pays ===\n");
  {
    bpa_demand_t dm;
    u32 i, d, k, home, found;
    u32 same[BPA_DEMAND_PROBES + 2];

    memset (&dm, 0, sizeof (dm));
    for (i = 0; i < BPA_DEMAND_MAX + 10; i++)
      bpa_demand_note (&dm, 5000 + i, 0);
    CHECK (dm.n == BPA_DEMAND_MAX, "the set holds %u, past its cap of %u",
	   dm.n, (u32) BPA_DEMAND_MAX);
    CHECK (dm.n_overflow == 10, "%u refusals counted as overflow, not 10",
	   dm.n_overflow);
    d = bpa_demand_tick (&dm, 0, 0);
    CHECK (d == BPA_DEMAND_MAX + 1, "a full set reads %u, not its cap plus "
	   "the one more it proves", d);
    CHECK (dm.n_overflow == 0, "the overflow survived the tick");

    /* Keys that all hash home to one slot fill one run of
       BPA_DEMAND_PROBES, and the next is overflow rather than a walk of
       the whole table on the packet path. */
    memset (&dm, 0, sizeof (dm));
    home = bpa_demand_slot (1);
    for (k = 0, found = 0; found < BPA_DEMAND_PROBES + 2; k++)
      if (bpa_demand_slot (k + 1) == home)
	same[found++] = k;
    for (i = 0; i < BPA_DEMAND_PROBES + 2; i++)
      bpa_demand_note (&dm, same[i], 0);
    CHECK (dm.n == BPA_DEMAND_PROBES && dm.n_overflow == 2,
	   "%u colliding keys: %u placed, %u overflow; want %u and 2",
	   (u32) BPA_DEMAND_PROBES + 2, dm.n, dm.n_overflow,
	   (u32) BPA_DEMAND_PROBES);
    CHECK (bpa_demand_slot (0xffffffffu) < BPA_DEMAND_SLOTS,
	   "a slot past the table");
  }

  printf (fail ? "\n%d FAILURES\n" : "\nALL TESTS PASSED\n", fail);
  return fail ? 1 : 0;
}
