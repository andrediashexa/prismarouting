/*
 * test_bpa_top.c - the order "show bpa top" reads its rows in.
 *
 * WHAT THE SCREEN IS FOR. It answers "who is closest to running out", which
 * is the question that prevents a support call rather than answering one. An
 * operator reads the first rows and nothing else, so an order that is subtly
 * wrong sends them to the wrong subscriber while looking exactly right - and
 * nothing about the screen would say so.
 *
 * WHY IT HAD NO TEST until now: the comparator lived in bpa_cli.c, whose own
 * header says its sentences are held by test/test_plugin_cli_says.py and its
 * screens by the labs, and that the arithmetic in it is deliberately NOT
 * excused - it names this function - because it is waiting for a harness.
 * The arithmetic needs nothing but two rows, so the harness is this file.
 */

#include <stdio.h>
#include <string.h>
#include "bpa_shim.h"
#include "../src/plugins/bpa/bpa_top.h"

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

static bpa_top_t
row (u32 pct, u32 used)
{
  bpa_top_t r;

  memset (&r, 0, sizeof (r));
  r.pct = pct;
  r.used = used;
  return r;
}

/* What vec_sort_with_function does with the answer, in the small: an
 * insertion sort, so the order this file asserts is the order the screen
 * prints. Reading the comparator's sign alone would assert a convention
 * rather than a screen. */
static void
sort_rows (bpa_top_t *v, unsigned n)
{
  unsigned i, j;

  for (i = 1; i < n; i++)
    {
      bpa_top_t key = v[i];

      for (j = i; j > 0 && bpa_top_cmp (&v[j - 1], &key) > 0; j--)
	v[j] = v[j - 1];
      v[j] = key;
    }
}

int
main (void)
{
  printf ("=== the fullest subscriber is read first ===\n");
  {
    bpa_top_t v[4];

    v[0] = row (10, 100);
    v[1] = row (99, 5);
    v[2] = row (50, 4000);
    v[3] = row (0, 0);
    sort_rows (v, 4);
    CHECK (v[0].pct == 99, "the fullest row is not first: %u%%", v[0].pct);
    CHECK (v[1].pct == 50 && v[2].pct == 10 && v[3].pct == 0,
	   "the rows after it are not in falling order: %u %u %u",
	   v[1].pct, v[2].pct, v[3].pct);
    CHECK (v[0].used == 5,
	   "the fullest row was ordered by ports and not by how full it is: "
	   "a subscriber at 99%% of a small block is closer to running out "
	   "than one at 50%% of a large one");
  }
  printf ("  [ ok ] four rows, read fullest first\n");

  printf ("\n=== a tie goes to the one holding more ports ===\n");
  /* Two subscribers at the same percentage are equally close to the wall;
   * the one holding more ports is the one whose next allocation is larger. */
  {
    bpa_top_t v[3];

    v[0] = row (90, 10);
    v[1] = row (90, 3000);
    v[2] = row (90, 700);
    sort_rows (v, 3);
    CHECK (v[0].used == 3000 && v[1].used == 700 && v[2].used == 10,
	   "a tie on percentage was not broken by ports used: %u %u %u",
	   v[0].used, v[1].used, v[2].used);
  }
  printf ("  [ ok ] and the rest follow it\n");

  printf ("\n=== two rows that are the same row ===\n");
  /* Equal on both is 0, in both directions: a comparator that answered
   * anything else would make the order depend on the order the rows were
   * built in, and the screen would move between two identical boxes. */
  {
    bpa_top_t x = row (40, 40), y = row (40, 40);

    CHECK (bpa_top_cmp (&x, &y) == 0, "two equal rows did not compare equal");
    CHECK (bpa_top_cmp (&y, &x) == 0, "and not in the other direction");
  }
  printf ("  [ ok ] equal rows compare equal, both ways round\n");

  printf ("\n=== and the comparison is a comparison ===\n");
  /*
   * `(int) y->used - (int) x->used` is the obvious form and the one that
   * goes wrong quietly: the fields are u32 and a difference that does not
   * fit in an int reverses the answer. No row can reach these values today -
   * `used` is bounded by the ports of one subscriber's blocks - so this is a
   * class being removed rather than a defect being fixed, and it is asserted
   * because the next person to write the obvious form would otherwise find
   * nothing red.
   */
  {
    bpa_top_t x = row (50, 3000000000u), y = row (50, 100u);

    CHECK (bpa_top_cmp (&x, &y) < 0,
	   "the row using three billion ports did not sort before the one "
	   "using a hundred: the difference overflowed");
    CHECK (bpa_top_cmp (&y, &x) > 0, "and not the other way round either");
  }
  {
    bpa_top_t x = row (3000000000u, 1), y = row (100u, 1);

    CHECK (bpa_top_cmp (&x, &y) < 0,
	   "the same overflow on the percentage");
  }
  printf ("  [ ok ] values an int cannot hold are still ordered\n");

  printf ("\n%u check(s)\n", checks);
  if (failures == 0)
    {
      printf ("ALL TESTS PASSED\n");
      return 0;
    }
  printf ("%d FAILURES\n", failures);
  return 1;
}
