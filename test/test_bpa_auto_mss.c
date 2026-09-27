/*
 * test_bpa_auto_mss.c - unit test for the "tcp-mss auto" minimum.
 *
 * TWO THINGS ARE ASSERTED HERE, and they are different kinds of claim.
 *
 * The first is the ANSWER. bpa_auto_mss.h maintains the smallest MTU among
 * the interfaces the CGNAT is attached to, incrementally, where the code it
 * replaced recomputed it by walking every interface on the box (#287).
 * Incremental bookkeeping is wrong in ways that depend on the ORDER of
 * operations - a minimum that leaves, a minimum that two interfaces share, an
 * MTU that moves upward off the minimum - and a lab exercises one order. So
 * every assertion below compares the maintained value against `walk()`, which
 * is the removed loop written out again: a full scan, the slow obvious way.
 * If the maintained value ever disagrees with what the walk would have said,
 * that is the regression, and this file is the only place that can see it
 * without a box with ten thousand interfaces on it.
 *
 * The second is the COST, and it is asserted by COUNTING, not by timing.
 * #287 is a cost that grew with the size of the box; the fix is a cost that
 * does not. A wall-clock assertion would be a fact about the machine - this
 * one runs a release lab suite alongside `make test`, and a stopwatch under
 * that load goes red for a reason that has nothing to do with this code. So
 * bpa_auto_mss_t counts the slots it reads, and the assertion is that
 * attaching the ten-thousandth interface reads exactly as many as attaching
 * the first. That is the property the issue is about, and it is the same on
 * any machine.
 */

#include <stdio.h>
#include <string.h>
#include "bpa_tcp_shim.h"
#include "../src/plugins/bpa/bpa_auto_mss.h"

static int failures = 0;
static unsigned checks = 0;

#define CHECK(cond, ...)                                                      \
  do                                                                          \
    {                                                                         \
      checks++;                                                               \
      if (!(cond))                                                            \
	{                                                                     \
	  printf ("  FAIL: ");                                                \
	  printf (__VA_ARGS__);                                               \
	  printf ("\n");                                                      \
	  failures++;                                                         \
	}                                                                     \
    }                                                                         \
  while (0)

#define SLOTS 8192

static u32 slots[SLOTS];

/*
 * The independent half: the loop bpa_auto_mss_update() used to be, kept here
 * verbatim in shape so that the maintained value has something written by
 * somebody else to disagree with. It exists to be slow and obvious.
 */
static u16
walk (const u32 *mtu, u32 n)
{
  u32 i, min = ~0u;

  for (i = 0; i < n; i++)
    if (mtu[i] && mtu[i] < min)
      min = mtu[i];

  return (min == ~0u || min <= BPA_MSS_OVERHEAD)
	   ? 0
	   : (u16) (min - BPA_MSS_OVERHEAD);
}

/* Deterministic, so a failure here is reproducible by running it again. */
static u32 rng_state = 0x9e3779b9u;

static u32
rng (void)
{
  rng_state ^= rng_state << 13;
  rng_state ^= rng_state >> 17;
  rng_state ^= rng_state << 5;
  return rng_state;
}

static void
reset (bpa_auto_mss_t *t, u32 n_slots)
{
  memset (slots, 0, sizeof (slots));
  bpa_auto_mss_init (t, slots, n_slots);
}

/* Sets a slot and asserts, there and then, that the maintained answer is
 * still the one a full walk would give. Every case below goes through here,
 * so no case can forget to compare. */
static void
set_and_compare (bpa_auto_mss_t *t, u32 idx, u32 mtu, const char *what)
{
  bpa_auto_mss_set (t, idx, mtu);
  CHECK (bpa_auto_mss_value (t) == walk (t->mtu, t->n_slots),
	 "%s: maintained %u, the walk says %u", what,
	 (unsigned) bpa_auto_mss_value (t),
	 (unsigned) walk (t->mtu, t->n_slots));
}

/*
 * The cases an operator can produce, named one at a time.
 *
 * Named rather than left to the random sequence below, because a random
 * sequence that happens to cover a case today covers it by luck, and the
 * cases here are the ones the incremental bookkeeping is actually made of.
 */
static void
the_named_cases (void)
{
  bpa_auto_mss_t t;

  printf ("=== the value the datapath reads ===\n");

  reset (&t, 16);
  CHECK (bpa_auto_mss_value (&t) == 0,
	 "no CGNAT interface must clamp nothing, got %u",
	 (unsigned) bpa_auto_mss_value (&t));

  set_and_compare (&t, 3, 1500, "one interface at 1500");
  CHECK (bpa_auto_mss_value (&t) == 1460, "1500 less 40 headers is 1460, "
					  "got %u",
	 (unsigned) bpa_auto_mss_value (&t));

  /* The SMALLEST, not the newest: a subscriber on the 1492 path is handed an
   * MSS the 1500 path would have allowed otherwise. */
  set_and_compare (&t, 7, 1492, "a narrower interface joins");
  CHECK (bpa_auto_mss_value (&t) == 1452, "the minimum must win, got %u",
	 (unsigned) bpa_auto_mss_value (&t));

  set_and_compare (&t, 9, 9000, "a jumbo interface joins");
  CHECK (bpa_auto_mss_value (&t) == 1452,
	 "a wider interface must not raise the clamp, got %u",
	 (unsigned) bpa_auto_mss_value (&t));

  /* The direction that cannot be done with a running minimum alone, and the
   * one the old walk got right for free. */
  set_and_compare (&t, 7, 0, "the narrowest interface leaves");
  CHECK (bpa_auto_mss_value (&t) == 1460,
	 "the clamp must rise to the next narrowest, got %u",
	 (unsigned) bpa_auto_mss_value (&t));

  set_and_compare (&t, 3, 0, "and the next");
  set_and_compare (&t, 9, 0, "and the last");
  CHECK (bpa_auto_mss_value (&t) == 0,
	 "an empty set must clamp nothing again, got %u",
	 (unsigned) bpa_auto_mss_value (&t));

  /* Two interfaces sharing the minimum. This is the case the count of
   * interfaces-at-the-minimum exists for: removing one of them must NOT
   * raise the clamp, because the other one still has that path. */
  reset (&t, 16);
  set_and_compare (&t, 0, 1492, "two interfaces at 1492");
  set_and_compare (&t, 1, 1492, "two interfaces at 1492");
  set_and_compare (&t, 2, 1500, "and one at 1500");
  set_and_compare (&t, 0, 0, "one of the two leaves");
  CHECK (bpa_auto_mss_value (&t) == 1452,
	 "the other interface is still on the 1492 path, got %u",
	 (unsigned) bpa_auto_mss_value (&t));
  set_and_compare (&t, 1, 0, "and so does the other");
  CHECK (bpa_auto_mss_value (&t) == 1460,
	 "now the clamp may rise, got %u", (unsigned) bpa_auto_mss_value (&t));

  /* An MTU that moves under a running box, which is why the expiry process
   * still makes a pass. Upward off the minimum is the awkward one: the
   * interface has not left, but the minimum it was holding has. */
  reset (&t, 16);
  set_and_compare (&t, 0, 1400, "an interface at 1400");
  set_and_compare (&t, 1, 1500, "and one at 1500");
  set_and_compare (&t, 0, 9000, "the narrow one is widened to 9000");
  CHECK (bpa_auto_mss_value (&t) == 1460,
	 "the clamp must follow the MTU up, got %u",
	 (unsigned) bpa_auto_mss_value (&t));
  set_and_compare (&t, 1, 600, "and the other is narrowed to 600");
  CHECK (bpa_auto_mss_value (&t) == 560,
	 "and down, got %u", (unsigned) bpa_auto_mss_value (&t));

  /* An MTU at or below the headers themselves. Not a clamp of zero and not a
   * clamp that wrapped: "do not clamp", the same answer as no interface. */
  reset (&t, 16);
  set_and_compare (&t, 0, BPA_MSS_OVERHEAD, "an MTU equal to the headers");
  CHECK (bpa_auto_mss_value (&t) == 0,
	 "an MTU of exactly %u leaves no room for a segment, got %u",
	 (unsigned) BPA_MSS_OVERHEAD, (unsigned) bpa_auto_mss_value (&t));
  set_and_compare (&t, 0, BPA_MSS_OVERHEAD - 1, "and one below them");
  CHECK (bpa_auto_mss_value (&t) == 0,
	 "an MTU below the headers must not wrap, got %u",
	 (unsigned) bpa_auto_mss_value (&t));
  set_and_compare (&t, 0, BPA_MSS_OVERHEAD + 1, "and one byte above them");
  CHECK (bpa_auto_mss_value (&t) == 1,
	 "one byte above the headers is an MSS of 1, got %u",
	 (unsigned) bpa_auto_mss_value (&t));

  /*
   * Setting a slot to what it already holds must change nothing, and must not
   * COST anything either. An attach repeated is an ordinary thing for an
   * operator to do - a startup file replayed, an API client that does not
   * check first - and the interface that was already the narrowest on the box
   * is the case where the count at the minimum would otherwise fall to zero
   * and put the whole array back through a rescan.
   *
   * The cost is what is asserted, because the ANSWER survives without the
   * early return: the rescan rebuilds exactly what the decrement took away.
   * Removing the early return and running this file is how that was
   * established, and asserting the answer here would have been asserting
   * something two different implementations both get right.
   */
  reset (&t, 16);
  set_and_compare (&t, 0, 1500, "an interface attaches");
  {
    u64 before = t.n_slots_read;

    set_and_compare (&t, 0, 1500, "and is attached again");
    CHECK (t.n_slots_read - before == 1,
	   "re-attaching the only interface read %llu slots, not 1",
	   (unsigned long long) (t.n_slots_read - before));
  }
  /*
   * The second interface is WIDER, and the width is the whole point of the
   * number. With both at 1500 the answer is 1460 for every value n_at_min can
   * hold - one, two or three - because the rescan a wrong count suppresses
   * would only rebuild the same 1500 the stale count still names. This case
   * was written that way, and it stayed green with a repeated attach counted
   * twice at the minimum; the only thing that turned red was the 20000-step
   * differential at the bottom of this file, at step 6678, which is the
   * general oracle and not the check written for the defect.
   *
   * At 1600 the count is observable: once 1500 leaves, the rescan is the only
   * thing that can find 1600, and a count that did not reach zero does not
   * run it.
   */
  set_and_compare (&t, 1, 1600, "a second, wider interface");
  set_and_compare (&t, 0, 0, "the narrowest one leaves");
  CHECK (bpa_auto_mss_value (&t) == 1560,
	 "a repeated attach must not have been counted twice, got %u",
	 (unsigned) bpa_auto_mss_value (&t));

  /* A slot the storage does not have. The caller grows the array first, so
   * this should not happen; it must not corrupt the answer when it does. */
  reset (&t, 16);
  set_and_compare (&t, 0, 1500, "an interface attaches");
  bpa_auto_mss_set (&t, 16, 600);
  CHECK (bpa_auto_mss_value (&t) == 1460,
	 "an index past the storage must be ignored, got %u",
	 (unsigned) bpa_auto_mss_value (&t));

  /* Growing the storage keeps the set. bpa.c grows a vec as sw_if_indices
   * arrive, and vec_validate may move it; re-pointing the structure must not
   * be the same thing as forgetting what is in it. */
  reset (&t, 4);
  set_and_compare (&t, 0, 1492, "an interface on a small array");
  bpa_auto_mss_storage (&t, slots, 16);
  CHECK (bpa_auto_mss_value (&t) == 1452,
	 "growing the storage must keep the set, got %u",
	 (unsigned) bpa_auto_mss_value (&t));
  set_and_compare (&t, 12, 1400, "and a slot the array only just has");
  CHECK (bpa_auto_mss_value (&t) == 1360, "got %u",
	 (unsigned) bpa_auto_mss_value (&t));
}

/*
 * The same set built in different orders.
 *
 * The maintained minimum depends on the order the operations arrive in and
 * the walk does not, which is exactly the property that has to hold: an
 * operator does not attach interfaces in MTU order.
 */
static void
order_must_not_matter (void)
{
  static const u32 mtus[] = { 1500, 1492, 9000, 1400, 1500, 576, 2000, 1492 };
  const u32 n = sizeof (mtus) / sizeof (mtus[0]);
  bpa_auto_mss_t t;
  u32 i, pass;
  u16 expected;

  printf ("\n=== the order the interfaces arrive in ===\n");

  reset (&t, 16);
  for (i = 0; i < n; i++)
    bpa_auto_mss_set (&t, i, mtus[i]);
  expected = bpa_auto_mss_value (&t);
  CHECK (expected == 536, "576 less 40 is the smallest path here, got %u",
	 (unsigned) expected);

  /* Forwards, backwards, and every rotation in between. */
  for (pass = 0; pass < n; pass++)
    {
      reset (&t, 16);
      for (i = 0; i < n; i++)
	{
	  u32 k = (i + pass) % n;
	  bpa_auto_mss_set (&t, k, mtus[k]);
	}
      CHECK (bpa_auto_mss_value (&t) == expected,
	     "rotation %u gave %u, not %u", (unsigned) pass,
	     (unsigned) bpa_auto_mss_value (&t), (unsigned) expected);
    }

  /* And taken apart in every rotation, comparing at every step. */
  for (pass = 0; pass < n; pass++)
    {
      reset (&t, 16);
      for (i = 0; i < n; i++)
	bpa_auto_mss_set (&t, i, mtus[i]);
      for (i = 0; i < n; i++)
	{
	  u32 k = (i + pass) % n;

	  set_and_compare (&t, k, 0, "taking the set apart");
	}
      CHECK (bpa_auto_mss_value (&t) == 0,
	     "rotation %u left %u behind after removing everything",
	     (unsigned) pass, (unsigned) bpa_auto_mss_value (&t));
    }
}

/*
 * Attaches, detaches and MTU changes in an arbitrary order, compared against
 * the walk after every single one.
 *
 * The named cases are the ones somebody thought of. This is the half that
 * covers the ones nobody did.
 */
static void
against_the_walk (void)
{
  static const u32 mtus[] = { 576, 1400, 1492, 1500, 2000, 9000, 41, 40 };
  bpa_auto_mss_t t;
  u32 step;

  printf ("\n=== twenty thousand changes against a full walk ===\n");

  reset (&t, 64);
  for (step = 0; step < 20000; step++)
    {
      u32 idx = rng () % 64;
      u32 r = rng () % 5;
      /* One change in five is a detach, so the set both fills and empties
       * rather than settling full. */
      u32 mtu = r == 0 ? 0 : mtus[rng () % (sizeof (mtus) / sizeof (mtus[0]))];

      bpa_auto_mss_set (&t, idx, mtu);
      if (bpa_auto_mss_value (&t) != walk (t.mtu, t.n_slots))
	break;
    }

  /* The step it stopped at, so a failure names the operation rather than the
   * sweep. One report and not twenty thousand. */
  CHECK (step == 20000,
	 "step %u: slot to %u left the clamp at %u, the walk says %u",
	 (unsigned) step, (unsigned) t.mtu[0],
	 (unsigned) bpa_auto_mss_value (&t), (unsigned) walk (t.mtu,
							      t.n_slots));
}

/*
 * THE COST. Counted, because a stopwatch on this machine measures the machine.
 *
 * The claim being defended is the one in #287: `set interface bpa in2out` cost
 * 4.3 ms at 5,000 interfaces and 8.3 ms at 10,000, because every attach walked
 * every interface. What makes that shape impossible is that an attach reads a
 * fixed number of slots no matter how many there are, and that is what is
 * asserted here - not a duration.
 */
static void
an_attach_costs_the_same_on_a_big_box (void)
{
  bpa_auto_mss_t t;
  u32 n, i;

  printf ("\n=== what one attach costs ===\n");

  /* Three shapes of box: every interface the same MTU, each new one wider
   * than the last, and each new one narrower - the last being the order that
   * moves the minimum on every single attach. */
  for (i = 0; i < 3; i++)
    {
      const char *shape =
	i == 0 ? "all the same MTU"
	       : (i == 1 ? "each one wider" : "each one narrower");
      u64 first, last, total;

      reset (&t, SLOTS);
      first = 0;
      last = 0;
      for (n = 0; n < SLOTS; n++)
	{
	  u64 before = t.n_slots_read;
	  u32 mtu = i == 0 ? 1500 : (i == 1 ? 1500 + n : 9000 - n);

	  bpa_auto_mss_set (&t, n, mtu);
	  if (n == 0)
	    first = t.n_slots_read - before;
	  last = t.n_slots_read - before;
	}
      total = t.n_slots_read;

      CHECK (first == last,
	     "%s: the first attach read %llu slots and the %uth read %llu",
	     shape, (unsigned long long) first, (unsigned) SLOTS,
	     (unsigned long long) last);
      CHECK (last == 1, "%s: an attach must read one slot, it read %llu",
	     shape, (unsigned long long) last);

      /*
       * The shape, said as a total as well as a per-call cost. An
       * implementation that walked everything on each attach would read
       * SLOTS*(SLOTS+1)/2 here - 33 million against 8192 - so this number
       * separates the two designs by three orders of magnitude and would go
       * red on any regression back towards the walk.
       */
      CHECK (total == SLOTS,
	     "%s: attaching %u interfaces read %llu slots, not %u", shape,
	     (unsigned) SLOTS, (unsigned long long) total, (unsigned) SLOTS);
    }

  /*
   * Taking them off again, on the box every operator actually has: one MTU,
   * or a few. Every detach but the last is a decrement of the count of
   * interfaces at the minimum, and only the departure of the last one looks
   * at the array.
   *
   * THE LIMIT, stated rather than hidden: a box whose interfaces all have
   * DIFFERENT MTUs, detached narrowest first, rescans on every detach and is
   * still quadratic in that one case. It is a flat scan of a u32 array rather
   * than a walk of the interface pool through the feature arc, so it is some
   * three orders of magnitude cheaper than what #287 measured, and it is not
   * on the attach path at all - but it is not constant, and saying so is
   * cheaper than somebody measuring it later.
   */
  reset (&t, SLOTS);
  for (n = 0; n < SLOTS; n++)
    bpa_auto_mss_set (&t, n, 1500);
  {
    u64 before = t.n_slots_read;
    u64 spent;

    for (n = 0; n < SLOTS; n++)
      bpa_auto_mss_set (&t, n, 0);
    spent = t.n_slots_read - before;

    CHECK (spent <= 2 * (u64) SLOTS,
	   "detaching %u interfaces that share an MTU read %llu slots, "
	   "which is more than linear", (unsigned) SLOTS,
	   (unsigned long long) spent);
    CHECK (bpa_auto_mss_value (&t) == 0,
	   "and the set must be empty afterwards, got %u",
	   (unsigned) bpa_auto_mss_value (&t));
  }
}

int
main (void)
{
  the_named_cases ();
  order_must_not_matter ();
  against_the_walk ();
  an_attach_costs_the_same_on_a_big_box ();

  /* The constant the clamp is derived from, asserted here as well as in
   * test_bpa_mss.c: this file subtracts it, and a change to it that nobody
   * meant would move every "auto" clamp on every box. */
  CHECK (BPA_MSS_OVERHEAD == 40,
	 "an IPv4 and a TCP header without options are 40 bytes");

  /* #472: the rescan by name, as the oracle the incremental path must agree
   * with. After every one of a long run of attaches, detaches and MTU
   * changes, a copy rebuilt from the slots by bpa_auto_mss_rescan() must hold
   * the same minimum, the same count at it and the same count on. */
  printf ("\n--- bpa_auto_mss_rescan: the incremental state is the rescan's ---\n");
  {
    static u32 slots[64];
    static const u32 mtus[] = { 0, 1500, 1492, 9000, 1480, 1500, 576 };
    bpa_auto_mss_t t, again;
    u32 step, seed = 12345, bad = 0;

    memset (slots, 0, sizeof (slots));
    bpa_auto_mss_init (&t, slots, 64);
    for (step = 0; step < 20000; step++)
      {
	seed = seed * 1103515245u + 12345u;
	bpa_auto_mss_set (&t, (seed >> 8) % 64, mtus[(seed >> 20) % 7]);
	again = t;
	bpa_auto_mss_rescan (&again);
	if (again.min != t.min || again.n_at_min != t.n_at_min
	    || again.n_on != t.n_on)
	  bad++;
      }
    CHECK (bad == 0, "%u of 20000 steps left the incremental minimum apart "
	   "from a rescan's", bad);
    memset (slots, 0, sizeof (slots));
    again = t;
    bpa_auto_mss_rescan (&again);
    CHECK (again.n_on == 0 && again.min == ~0u && again.n_at_min == 0,
	   "a rescan of empty slots found a minimum of %u", again.min);
  }

  return bpa_test_verdict (checks, failures);
}
