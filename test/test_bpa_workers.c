/*
 * test_bpa_workers.c - which worker owns a subscriber, and which thread
 * that is (bpa_workers.h, #466, #472).
 *
 * THE ROUND TRIP is the property: the thread bpa_owner_thread() sends a
 * subscriber to must be the one bpa_thread_worker() calls that subscriber's
 * owner (sub % n). A subscriber whose packets reach a thread that does not
 * recognise itself is translated from an empty table - sessions refused, or
 * a second block handed out. Held for every owner layout a box can start
 * with: all workers, an odd count, one, and the RX/CGNAT splits of #466 with
 * the owners scattered among the receivers.
 *
 * Mutation shown red: bpa_thread_table() indexing by the worker id instead
 * of its thread (wid instead of wid + 1).
 */
#include <stdio.h>
#include <string.h>
#include "bpa_shim.h"
#include "bpa_vec_shim.h"
#include "../src/plugins/bpa/bpa_pool.c"
#include "../src/plugins/bpa/bpa_workers.h"

static int failures = 0;
static unsigned checks = 0;

#define CHECK(cond, ...)                                                      \
  do                                                                          \
    {                                                                         \
      checks++;                                                               \
      if (!(cond))                                                            \
	{                                                                     \
	  printf ("  FAIL: ");                                               \
	  printf (__VA_ARGS__);                                               \
	  printf ("\n");                                                      \
	  failures++;                                                         \
	}                                                                     \
    }                                                                         \
  while (0)

/* One owner layout: `n_vpp` VPP workers, of which the ids in `owners`
 * translate. Returns how many subscribers failed the round trip. */
static u32
round_trip (u32 n_vpp, const u32 *owners, u32 n_owners, const char *name)
{
  u32 *wids = 0, *thread_w, k, sub, bad = 0, idle = 0, t;
  bpa_divisor_t div;

  for (k = 0; k < n_owners; k++)
    vec_add1 (wids, owners[k]);
  thread_w = bpa_thread_table (wids, n_vpp);
  bpa_divisor_init (&div, n_owners);

  CHECK (vec_len (thread_w) == n_vpp + 1,
	 "%s: the table covers %u threads, not the %u this dataplane has",
	 name, vec_len (thread_w), n_vpp + 1);
  for (sub = 0; sub < 5000; sub++)
    {
      u32 w, nw, thread = bpa_owner_thread (n_owners, wids, &div, sub);

      bpa_thread_worker (n_owners, thread_w, thread, &w, &nw);
      if (nw != n_owners || w != sub % n_owners)
	bad++;
    }
  /* The threads that only receive - the main thread and every worker not
   * in the list - are no worker at all. */
  for (t = 0; t <= n_vpp; t++)
    {
      u32 w, nw, is_owner = 0;

      for (k = 0; k < n_owners; k++)
	if (bpa_thread_of_wid (owners[k]) == t)
	  is_owner = 1;
      bpa_thread_worker (n_owners, thread_w, t, &w, &nw);
      if (!is_owner && w != ~0u)
	idle++;
    }
  CHECK (bad == 0, "%s: %u of 5000 subscribers reach a thread that does not "
	 "own them", name, bad);
  CHECK (idle == 0, "%s: %u receive-only thread(s) claim a worker", name,
	 idle);
  vec_free (wids);
  vec_free (thread_w);
  return bad + idle;
}

int
main (void)
{
  printf ("=== every subscriber reaches the thread that owns it ===\n");
  {
    static const u32 all4[] = { 0, 1, 2, 3 };
    static const u32 odd3[] = { 0, 1, 2 };
    static const u32 one[] = { 0 };
    static const u32 split[] = { 1, 3 };	   /* RX on 0 and 2 */
    static const u32 scattered[] = { 0, 2, 5, 6 }; /* 8 workers, 4 translate */
    static const u32 last[] = { 7 };

    round_trip (4, all4, 4, "4 workers, all translating");
    round_trip (3, odd3, 3, "3 workers");
    round_trip (1, one, 1, "one worker");
    round_trip (4, split, 2, "4 workers, 2 translating");
    round_trip (8, scattered, 4, "8 workers, 4 scattered");
    round_trip (8, last, 1, "8 workers, the last one translating");
    printf ("  every subscriber lands on its owner's thread, and every "
	    "receive-only thread is nobody's\n");
  }

  printf ("\n=== no workers: one slice, on thread 0 ===\n");
  {
    u32 w = 9, nw = 9;
    bpa_divisor_t div;

    bpa_divisor_init (&div, 1);
    bpa_thread_worker (0, 0, 0, &w, &nw);
    CHECK (w == 0 && nw == 1, "no workers reads as worker %u of %u", w, nw);
    CHECK (bpa_owner_thread (0, 0, &div, 12345) == 0,
	   "no workers sent a subscriber off thread 0");
    bpa_thread_worker (4, 0, 99, &w, &nw);
    CHECK (w == ~0u, "a thread past the table claimed worker %u", w);
    printf ("  a box with no workers is one slice on the main thread\n");
  }

  printf ("\n=== RX queue q goes to the thread that transmits on TX queue q "
	  "(#487) ===\n");
  {
    /* The #475 bench: 7 workers, TX queue q on thread q, queue 0 shared by
     * main and thread 7; VPP had RX queue q on thread q + 1. */
    static const u32 q0[] = { 0, 7 }, q3[] = { 3 }, main_only[] = { 0 };
    static const u32 shared[] = { 2, 5 };

    CHECK (bpa_rx_thread_for (q3, 1, 4) == 3,
	   "RX queue 3 stayed on thread 4, one past its transmitter");
    CHECK (bpa_rx_thread_for (q0, 2, 1) == 7,
	   "RX queue 0 went to thread %u, not the worker sharing TX queue 0",
	   bpa_rx_thread_for (q0, 2, 1));
    CHECK (bpa_rx_thread_for (main_only, 1, 1) == ~0u,
	   "a queue only main transmits on was moved to main, which never "
	   "polls");
    CHECK (bpa_rx_thread_for (q3, 0, 4) == ~0u,
	   "a queue nobody transmits on was moved");
    CHECK (bpa_rx_thread_for (shared, 2, 5) == 5,
	   "a queue already on one of its transmitters was moved");
    CHECK (bpa_rx_thread_for (shared, 2, 1) == 2,
	   "a shared TX queue did not pick its lowest transmitter");
    printf ("  each RX queue lands on a worker that transmits on its TX "
	    "twin, and main is never chosen\n");
  }

  printf ("\n%u checks, %d failures\n", checks, failures);
  printf (failures ? "%d FAILURES\n" : "ALL TESTS PASSED\n", failures);
  return failures ? 1 : 0;
}
