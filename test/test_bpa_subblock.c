/*
 * test_bpa_subblock.c - the arithmetic over a subscriber's blocks
 * (bpa_subblock.h, #472). `show bpa subscriber` and `show bpa top` read it.
 *
 * Mutation shown red: used summed as n_free instead of n_ports - n_free.
 */
#include <stdio.h>
#include <string.h>
#include "bpa_shim.h"
#include "bpa_vec_shim.h"
#include "../src/plugins/bpa/bpa_subblock.h"

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

int
main (void)
{
  bpa_subblock_t *v = 0, sb;
  u32 used = 7, total = 7;

  printf ("=== a subscriber's usage, over its blocks ===\n");
  bpa_subblocks_usage (v, &used, &total);
  CHECK (used == 0 && total == 0,
	 "no blocks is %u of %u, not nothing", used, total);

  memset (&sb, 0, sizeof (sb));
  sb.map.n_ports = 512; sb.map.n_free = 500;	/* 12 in use */
  vec_add1 (v, sb);
  sb.map.n_ports = 256; sb.map.n_free = 0;	/* full */
  vec_add1 (v, sb);
  sb.map.n_ports = 256; sb.map.n_free = 256;	/* just attached */
  vec_add1 (v, sb);
  bpa_subblocks_usage (v, &used, &total);
  CHECK (total == 1024, "three blocks hold %u ports, not 1024", total);
  CHECK (used == 268, "12 + 256 + 0 ports in use read as %u", used);
  vec_free (v);
  printf ("  ports in use and ports held, summed over every block\n");

  printf ("\n%u checks, %d failures\n", checks, failures);
  printf (failures ? "%d FAILURES\n" : "ALL TESTS PASSED\n", failures);
  return failures ? 1 : 0;
}
