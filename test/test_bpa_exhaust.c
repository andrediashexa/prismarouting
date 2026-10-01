/*
 * test_bpa_exhaust.c - one exhaustion record per subscriber per interval
 * (#549), as bpa_exhaust_report() decides it. No VPP: the header is plain C.
 */
#include <stdio.h>
#include <string.h>
#include "bpa_shim.h"
#include "bpa_exhaust.h"

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

static bpa_exhaust_t x;

int
main (void)
{
  u32 i, j, reported = 0;

  printf ("=== the bench's fill run: 772 subscribers x 2100 refused packets "
	  "===\n");
  memset (&x, 0, sizeof (x));
  for (j = 0; j < 2100; j++)
    for (i = 0; i < 772; i++)
      reported += bpa_exhaust_report (&x, 0x64400000 + 248 + i,
				      1000.0 + j * 0.001);
  CHECK (reported == 772,
	 "one record per subscriber, not 1,621,200: %u reported", reported);
  CHECK (x.suppressed + reported == 772 * 2100,
	 "every packet not reported is counted: %llu + %u",
	 (unsigned long long) x.suppressed, reported);
  printf ("  %u records for 1,621,200 refused packets, %llu suppressed\n",
	  reported, (unsigned long long) x.suppressed);

  printf ("\n=== the interval ===\n");
  memset (&x, 0, sizeof (x));
  CHECK (bpa_exhaust_report (&x, 0x64400001, 5.0) == 1,
	 "a first exhaustion is reported");
  CHECK (bpa_exhaust_report (&x, 0x64400001, 5.0 + BPA_EXHAUST_IVL - 0.5) == 0,
	 "the same subscriber inside the interval is not");
  CHECK (bpa_exhaust_report (&x, 0x64400001, 5.0 + BPA_EXHAUST_IVL + 0.5) == 1,
	 "and is again once the interval has passed");
  CHECK (bpa_exhaust_report (&x, 0x64400002, 5.1) == 1,
	 "another subscriber is reported on its own");
  for (i = 0; i < 64; i++) /* far more than four ways' worth in a run */
    bpa_exhaust_report (&x, 0x0a000000 + i * 4096 * 4096, 6.0);
  CHECK (bpa_exhaust_report (&x, 0x0a000000, 6.0 + BPA_EXHAUST_IVL + 1) == 1,
	 "a crowded set still reports a subscriber once its interval is over");
  printf ("  first reported, repeats inside %.0fs not, after it again\n",
	  BPA_EXHAUST_IVL);

  printf ("\n%u checks, %d failures\n", checks, failures);
  printf (failures ? "%d FAILURES\n" : "ALL TESTS PASSED\n", failures);
  return failures ? 1 : 0;
}
