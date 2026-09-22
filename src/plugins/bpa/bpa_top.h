/*
 * bpa_top.h - one row of "show bpa top", and the order the rows are read in.
 *
 * WHY THIS IS A HEADER AND NOT SIX LINES IN bpa_cli.c. That file's own
 * argument is that its sentences are held by test/test_plugin_cli_says.py
 * and its screens by the labs, and that what is NOT held is the arithmetic -
 * which it names, and which it deliberately refuses to excuse. This is that
 * arithmetic, and it needs nothing but two rows: no pool, no VPP, no vector.
 * So it is here, where a harness can call it.
 *
 * WHAT THE ORDER IS FOR. `show bpa top` answers "who is closest to running
 * out", which is the question that prevents a support call rather than
 * answering one. The operator reads the first rows and nothing else, so an
 * order that is subtly wrong is a screen that sends them to the wrong
 * subscriber while looking exactly right.
 */

#ifndef __included_bpa_top_h__
#define __included_bpa_top_h__

/* One row of "show bpa top". */
typedef struct
{
  u32 ip_priv;
  u32 pct;
  u32 used;
  u32 total;
  u32 n_blocks;
  u32 n_refused;
  u32 pool_index;
} bpa_top_t;

/*
 * Fullest first, and on a tie the one using more ports.
 *
 * COMPARED AND NOT SUBTRACTED. `(int) y->used - (int) x->used` is the
 * obvious form and it is the one that goes wrong quietly: the fields are
 * u32, and a difference that does not fit in an int is a comparison that
 * reverses. Today no row can reach that - `used` is bounded by the ports of
 * one subscriber's blocks - so this is not a defect being fixed but a class
 * being removed, and it costs one branch.
 *
 * The percentage first because that is the question. Two subscribers at 90%
 * are equally close to the wall; the one holding more ports is the one whose
 * next allocation is larger, so it goes first.
 */
static_always_inline int
bpa_top_cmp (void *a, void *b)
{
  bpa_top_t *x = a, *y = b;

  if (x->pct != y->pct)
    return x->pct > y->pct ? -1 : 1;
  if (x->used != y->used)
    return x->used > y->used ? -1 : 1;
  return 0;
}

#endif /* __included_bpa_top_h__ */
