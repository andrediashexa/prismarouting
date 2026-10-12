/*
 * bench_bpa_dyn.c - what choosing a block costs, before and after #603.
 *
 * Not a suite: a number that depends on the machine is not a verdict, and
 * `make test` runs on a build host shared with other agents' labs. It is run
 * by hand with `make bench-dyn`, and what it printed is in docs/architecture.md
 * ("How it is kept, and what it costs").
 *
 * WHAT IS TIMED is the part of an allocation and of a release that #603
 * changes, and nothing around it. bpa_dyn_acquire() also inserts into by_sub,
 * and the standalone shim's hash is a sorted array whose insert is a memmove
 * over every subscriber - a cost VPP's hash does not have, which would bury
 * the allocator's own nanoseconds under the shim's. So the two halves are
 * timed through the same internal functions bpa_dyn_acquire() and
 * bpa_dyn_reap() call:
 *
 *   allocate   which free block, and taking it off the free structures:
 *              bpa_dyn_pop() before, bpa_dyn_take() plus the per-address
 *              count after
 *   release    putting it back: bpa_dyn_push() before, bpa_dyn_push() plus
 *              the per-address count after
 *
 * Built against either allocator: BPA_DYN_TAKE is defined by the #603 one,
 * so the same file measures the stack it replaced when compiled against the
 * tree before it (BENCH_SRC= in the Makefile target).
 *
 * Three workers' slices are held at half full and churned: a random holder
 * leaves, an arrival takes a block, in batches of 4096 with the workers
 * taking turns - so every allocation pays for draining what the other two
 * released, which is the cost a worker on its own would never show. The
 * bench's shape (#475) and four larger public sets. One thread drives all
 * three, so what is NOT here is contention on the shared lines between
 * cores; the lab number in docs/architecture.md is where that would show.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "bpa_shim.h"
#include "bpa_vec_shim.h"
#include "bpa_pool.h"
#include "bpa_dyn.c"

#ifdef BPA_DYN_TAKE
#define TAKE(d) bpa_dyn_take_counted (d)
#define GIVE(d, l) bpa_dyn_give_counted (d, l)
static u32
bpa_dyn_take_counted (bpa_dyn_t *d)
{
  u32 local = bpa_dyn_take (d);

  bpa_dyn_count (d, local, 1);
  return local;
}
static void
bpa_dyn_give_counted (bpa_dyn_t *d, u32 local)
{
  bpa_dyn_count (d, local, -1);
  bpa_dyn_push (d, local);
}
#define RULE "least-loaded (#603)"
#else
#define TAKE(d) bpa_dyn_pop (d)
#define GIVE(d, l) bpa_dyn_push (d, l)
#define RULE "stack (before #603)"
#endif

static double
now_ns (void)
{
  struct timespec ts;

  clock_gettime (CLOCK_MONOTONIC, &ts);
  return ts.tv_sec * 1e9 + ts.tv_nsec;
}

static int
cmp_d (const void *a, const void *b)
{
  double x = *(const double *) a, y = *(const double *) b;
  return x < y ? -1 : x > y;
}

static void
bench (u32 n_out, u16 bsz)
{
  bpa_pool_t p;
  bpa_dyn_t d[3];
  u32 in_base = 0x64400000, in_count = 1u << 22, out_base = 0xc8000000;
  u32 *held[3], nheld[3], i, b, w, batches = 200, per = 4096;
  double *ta = calloc (batches, sizeof (double));
  double *tr = calloc (batches, sizeof (double));
  u32 x = 2463534242u;

  memset (&p, 0, sizeof (p));
  p.name = (u8 *) "bench";
  p.port_min = 1024;
  p.port_max = 65535;
  p.block_size = bsz;
  p.policy = BPA_POLICY_INTERLEAVED;
  p.mode = BPA_MODE_DYNAMIC;
  p.max_blocks = 4;
  if (bpa_addr_set_build (&p.inside, &in_base, &in_count, 1)
      || bpa_addr_set_build (&p.outside, &out_base, &n_out, 1)
      || bpa_pool_init (&p))
    {
      printf ("pool refused\n");
      exit (1);
    }
#ifdef BPA_DYN_TAKE
  bpa_dyn_share_init (&p, 3);
#endif
  for (w = 0; w < 3; w++)
    {
      if (bpa_dyn_init (&d[w], &p, w, 3, 0))
	{
	  printf ("slice refused\n");
	  exit (1);
	}
      held[w] = calloc (d[w].n_local, sizeof (u32));
      for (nheld[w] = 0; nheld[w] < d[w].n_local / 2; nheld[w]++)
	held[w][nheld[w]] = TAKE (&d[w]);
      /* never more than half a slice in one batch, so a small slice still
	 churns rather than draining */
      if (per > nheld[w])
	per = nheld[w];
    }

  for (b = 0; b < batches; b++)
    {
      u32 *gone = calloc (per, sizeof (u32)), *gw = calloc (per, sizeof (u32));
      double t0;

      /* who leaves is drawn before the clock starts; the three workers take
	 turns, so every allocation drains what the other two released */
      for (i = 0; i < per; i++)
	{
	  u32 k;

	  w = i % 3;
	  x ^= x << 13;
	  x ^= x >> 17;
	  x ^= x << 5;
	  k = x % nheld[w];
	  gw[i] = w;
	  gone[i] = held[w][k];
	  held[w][k] = held[w][--nheld[w]];
	}
      t0 = now_ns ();
      for (i = 0; i < per; i++)
	GIVE (&d[gw[i]], gone[i]);
      tr[b] = (now_ns () - t0) / per;
      t0 = now_ns ();
      for (i = 0; i < per; i++)
	{
	  w = gw[i];
	  held[w][nheld[w]++] = TAKE (&d[w]);
	}
      ta[b] = (now_ns () - t0) / per;
      free (gone);
      free (gw);
    }
  qsort (ta, batches, sizeof (double), cmp_d);
  qsort (tr, batches, sizeof (double), cmp_d);
  printf ("  %6u addresses %7u blocks a slice   allocate %6.1f ns "
	  "(%6.2f M/s)   release %6.1f ns (%6.2f M/s)\n",
	  n_out, d[0].n_local, ta[batches / 2], 1e3 / ta[batches / 2],
	  tr[batches / 2], 1e3 / tr[batches / 2]);
  for (w = 0; w < 3; w++)
    {
      bpa_dyn_free (&d[w]);
      free (held[w]);
    }
#ifdef BPA_DYN_TAKE
  bpa_dyn_share_free (&p);
#endif
  free (ta);
  free (tr);
}

/* not a unit: the benchmark's entry point, run by hand (make bench-dyn) */
int
main (void)
{
  printf ("%s, three workers' slices half full, median of 200 batches "
	  "of 4096 (or half a slice)\n",
	  RULE);
  bench (8, 4096);    /* the bench's pool: 203.0.113.0/29 */
  bench (256, 4096);  /* a /24 */
  bench (1024, 4096); /* a /22 */
  bench (4096, 4096); /* a /20 */
  bench (65536, 16384); /* a /16, 3 blocks an address */
  return 0;
}
