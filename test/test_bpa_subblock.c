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

  printf ("\n=== a new session's port, over the blocks held ===\n");
  {
    static u64 bits0[1], bits1[1];
    u32 blk = 99, i;
    int off;

    v = 0;
    memset (&sb, 0, sizeof (sb));
    sb.blk.port_lo = 1024;
    sb.blk.port_hi = 1087;
    bpa_portmap_init (&sb.map, 64, bits0);
    vec_add1 (v, sb);
    sb.blk.port_lo = 2048;
    sb.blk.port_hi = 2111;
    bpa_portmap_init (&sb.map, 64, bits1);
    vec_add1 (v, sb);

    off = bpa_subblocks_alloc (v, 1030, &blk);
    CHECK (off == 6 && blk == 0,
	   "port 1030 inside the first block came out as offset %d of block "
	   "%u - port preservation lost", off, blk);
    off = bpa_subblocks_alloc (v, 2050, &blk);
    CHECK (blk == 0 && off >= 0 && off != 6,
	   "a port in the SECOND block's range was preserved there while the "
	   "first, oldest block had room (block %u)", blk);
    for (i = 0; i < 62; i++)
      bpa_subblocks_alloc (v, 0, &blk);
    off = bpa_subblocks_alloc (v, 2050, &blk);
    CHECK (blk == 1 && off == 2,
	   "with the first block full, port 2050 came out as offset %d of "
	   "block %u, not its own offset 2 in block 1", off, blk);
    for (i = 0; i < 63; i++)
      bpa_subblocks_alloc (v, 0, &blk);
    blk = 77;
    CHECK (bpa_subblocks_alloc (v, 1030, &blk) == -1 && blk == 77,
	   "a subscriber with every block full was handed a port");
    CHECK (bpa_subblocks_alloc (0, 1030, &blk) == -1,
	   "a subscriber holding no block was handed a port");
    vec_free (v);
    printf ("  oldest block first, the original port kept where it can be\n");
  }

  printf ("\n=== the fill alarm: once per crossing, ten points of "
	  "hysteresis ===\n");
  {
    u8 alarmed = 0;
    u32 lines = 0, pct;

    CHECK (bpa_alarm_step (&alarmed, 79, 80) == 0 && !alarmed,
	   "79%% raised an 80%% alarm");
    CHECK (bpa_alarm_step (&alarmed, 80, 80) == 1 && alarmed,
	   "80%% did not raise an 80%% alarm");
    CHECK (bpa_alarm_step (&alarmed, 95, 80) == 0 && alarmed,
	   "a subscriber past the alarm logged it again");
    CHECK (bpa_alarm_step (&alarmed, 71, 80) == 0 && alarmed,
	   "71%% - inside the ten points - cleared the alarm");
    CHECK (bpa_alarm_step (&alarmed, 69, 80) == 0 && !alarmed,
	   "69%% did not clear the alarm");
    /* Sitting on the threshold: 79, 80, 79, 80... is one line, not one per
     * port taken. */
    alarmed = 0;
    for (pct = 0; pct < 1000; pct++)
      lines += (u32) bpa_alarm_step (&alarmed, 79 + (pct & 1), 80);
    CHECK (lines == 1, "a subscriber sitting on the threshold logged %u "
	   "lines", lines);
    alarmed = 0;
    CHECK (bpa_alarm_step (&alarmed, 5, 5) == 1
	     && bpa_alarm_step (&alarmed, 0, 5) == 0 && alarmed,
	   "a threshold under ten points cleared an alarm it cannot fall "
	   "ten points below");
  }

  printf ("\n%u checks, %d failures\n", checks, failures);
  printf (failures ? "%d FAILURES\n" : "ALL TESTS PASSED\n", failures);
  return failures ? 1 : 0;
}
