/*
 * test_bpa_rate.c - the subscriber's creation-rate bucket.
 *
 * WHAT THE RULE BUYS. `max-rate` on a pool is how fast ONE subscriber may
 * open new sessions, and therefore how quickly a single infected CPE can
 * fill a box that every other customer is also behind. Too strict and an
 * ordinary browser is refused; too loose and the rule buys nothing. Neither
 * failure looks like anything on a screen: the box goes on forwarding, and
 * what changes is which sessions exist.
 *
 * It lived in bpa_session.c and touched two fields and a clock - no session,
 * no pool, no VPP - so nothing drove it.
 */

#include <stdio.h>
#include <string.h>
#include "bpa_shim.h"
#include "../src/plugins/bpa/bpa_rate.h"

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

/* How many sessions a subscriber gets in one go, starting from a state. */
static u32
burst (f32 *tokens, f64 *last, u32 rate, f64 now)
{
  u32 n = 0;

  while (bpa_rate_take (tokens, last, rate, now))
    n++;
  return n;
}

int
main (void)
{
  printf ("=== the first ask is not refused ===\n");
  /* A subscriber whose record was created a moment ago has an empty bucket
   * and a zero clock. Refusing it would refuse the first packet of every new
   * subscriber on the box, which is the shape nobody would report as a rate
   * limit working. */
  {
    f32 tokens = 0.f;
    f64 last = 0.;

    CHECK (bpa_rate_take (&tokens, &last, 10, 1000.0) == 1,
	   "the first session a new subscriber asks for was refused");
    CHECK (last == 1000.0, "the bucket did not remember when it was asked");
  }
  printf ("  [ ok ] a new subscriber starts full\n");

  printf ("\n=== the burst is one second's worth, and no more ===\n");
  /* The cap IS the burst the operator agreed to. A bucket that kept filling
   * while a subscriber was quiet would let a CPE that slept for an hour open
   * an hour's worth of sessions in one instant - which is the traffic this
   * rule exists to refuse. */
  {
    f32 tokens = 0.f;
    f64 last = 0.;
    u32 n;

    n = burst (&tokens, &last, 10, 1000.0);
    CHECK (n == 10, "a rate of 10 gave a first burst of %u", n);

    /* An hour of silence, then ask again: still ten. */
    n = burst (&tokens, &last, 10, 1000.0 + 3600.0);
    CHECK (n == 10,
	   "after an hour quiet the bucket gave %u sessions at once, and the "
	   "burst the operator agreed to is one second's worth", n);
  }
  printf ("  [ ok ] ten a second is ten, an hour later too\n");

  printf ("\n=== it refills with time and not on a timer ===\n");
  {
    f32 tokens = 0.f;
    f64 last = 0.;
    u32 n;

    burst (&tokens, &last, 100, 2000.0);
    /* Half a second at 100/s is fifty. */
    n = burst (&tokens, &last, 100, 2000.5);
    CHECK (n == 50, "half a second at 100/s gave %u, not 50", n);
    /* And a tenth of a second is ten. */
    n = burst (&tokens, &last, 100, 2000.6);
    CHECK (n == 10, "a tenth of a second at 100/s gave %u, not 10", n);
  }
  printf ("  [ ok ] half a second is half the rate\n");

  printf ("\n=== a clock that did not move adds nothing ===\n");
  /*
   * VPP's clock is monotonic, so a clock going backwards is not a case this
   * box produces. What is asserted is that the arithmetic does not INVENT
   * tokens if it ever did: tokens invented are sessions this rule was set to
   * refuse, and they would arrive exactly when something else on the box had
   * already gone wrong.
   */
  {
    f32 tokens = 0.f;
    f64 last = 0.;
    u32 n;

    burst (&tokens, &last, 5, 3000.0);
    CHECK (bpa_rate_take (&tokens, &last, 5, 3000.0) == 0,
	   "the same instant refilled the bucket");
    CHECK (bpa_rate_take (&tokens, &last, 5, 2999.0) == 0,
	   "a clock that went backwards refilled the bucket");
    /*
     * AND `dt > 0` IS NOT `dt != 0`, WHICH THIS FILE CANNOT SHOW - said out
     * loud rather than left as a case somebody later thinks is covered.
     * Taking a backwards reading as the new mark subtracts dt*rate tokens
     * and moves the mark back by dt, and the next forward ask adds exactly
     * what was subtracted: the two cancel, so no sequence of asks at one
     * rate tells them apart. They differ only if the RATE changes in
     * between, and then the mutation is the conservative one - it hands out
     * fewer sessions, not more. The guard stays because tokens that went
     * negative are a bucket holding a debt nobody agreed to, not because a
     * mutation here goes red.
     */
    n = burst (&tokens, &last, 5, 3000.0);
    CHECK (n == 0,
	   "after a backwards reading the bucket gave %u sessions at the "
	   "very instant it was emptied: the backwards clock became the mark "
	   "it refills from", n);
    n = burst (&tokens, &last, 5, 3001.0);
    CHECK (n == 5,
	   "and a second later it gave %u, not a second's worth", n);
  }
  printf ("  [ ok ] no time, no tokens\n");

  printf ("\n=== and what the caller must not ask ===\n");
  /*
   * A rate of zero means "no limit" to an operator, and both callers check
   * `p->max_rate` before asking - so this function never sees it on a box.
   * Asserted anyway, because the answer it gives is the opposite of what the
   * word means: a zero bucket refuses everything.
   */
  {
    f32 tokens = 0.f;
    f64 last = 0.;

    CHECK (bpa_rate_take (&tokens, &last, 0, 4000.0) == 0,
	   "a rate of zero let a session through, and the callers rely on "
	   "never asking it");
  }
  printf ("  [ ok ] a rate of zero refuses, which is why the callers "
	  "check first\n");

  printf ("\n%u check(s)\n", checks);
  if (failures == 0)
    {
      printf ("ALL TESTS PASSED\n");
      return 0;
    }
  printf ("%d FAILURES\n", failures);
  return 1;
}
