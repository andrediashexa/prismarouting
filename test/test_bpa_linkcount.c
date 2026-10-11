/*
 * test_bpa_linkcount.c - a port's link changes, counted (#588): what
 * `show bpa link-changes` prints and the host's watcher reads to see a flap
 * that happened between two of its readings.
 *
 * WHY THESE. A counter that counted every report rather than every change
 * would say a link that a driver reported "down" twice went down twice; one
 * that kept its count across an interface deleted and created again would
 * hand the new interface the old one's history - and a memif's far end is
 * deleted and created again every time it reconnects (lab/show-log-watch.sh).
 */

#include <stdio.h>
#include <string.h>
#include "bpa_shim.h"
#include "../src/plugins/bpa/bpa_linkcount.h"

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

int
main (void)
{
  bpa_linkcount_t c;

  printf ("=== a port that comes up, flaps three times, and stays up ===\n");
  memset (&c, 0, sizeof (c));
  bpa_linkcount_note (&c, 1);
  CHECK (c.ups == 1 && c.downs == 0 && c.up && c.known,
	 "its first change, up: ups %u downs %u", c.ups, c.downs);
  for (int i = 0; i < 3; i++)
    {
      bpa_linkcount_note (&c, 0);
      bpa_linkcount_note (&c, 1);
    }
  CHECK (c.ups == 4 && c.downs == 3 && c.up,
	 "three flaps: ups %u downs %u", c.ups, c.downs);
  printf ("  [ ok ] ups %u, downs %u\n", c.ups, c.downs);

  printf ("\n=== a report of the state it already has is not a change ===\n");
  bpa_linkcount_note (&c, 1);
  bpa_linkcount_note (&c, 0);
  bpa_linkcount_note (&c, 0);
  CHECK (c.ups == 4 && c.downs == 4 && !c.up,
	 "up again and down twice is one down: ups %u downs %u", c.ups,
	 c.downs);
  bpa_linkcount_note (&c, 0x40); /* any non-zero is up */
  CHECK (c.ups == 5 && c.up, "a flag word that is not 1 is still up");
  printf ("  [ ok ] counted once\n");

  printf ("\n=== a first change down, on a port seen for the first time ===\n");
  memset (&c, 0, sizeof (c));
  bpa_linkcount_note (&c, 0);
  CHECK (c.ups == 0 && c.downs == 1 && !c.up && c.known,
	 "VPP calls only on a change, so a first down is one: downs %u",
	 c.downs);
  printf ("  [ ok ] downs %u\n", c.downs);

  printf ("\n=== an interface deleted and created again starts again ===\n");
  bpa_linkcount_reset (&c);
  CHECK (c.ups == 0 && c.downs == 0 && !c.known,
	 "the history is gone: ups %u downs %u known %u", c.ups, c.downs,
	 c.known);
  bpa_linkcount_note (&c, 0);
  CHECK (c.downs == 1, "and a down after it is counted, not taken for the "
		       "old state");
  printf ("  [ ok ] from zero\n");

  printf ("\n%u check(s)\n", checks);
  if (failures == 0)
    {
      printf ("ALL TESTS PASSED\n");
      return 0;
    }
  printf ("%d FAILURES\n", failures);
  return 1;
}
