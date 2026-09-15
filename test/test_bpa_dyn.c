/*
 * test_bpa_dyn.c - unit test for the dynamic block allocator.
 *
 * The other C suites in this project are arithmetic: a function of its
 * arguments, checked against vectors from ref/bpa_model.py. This one is not.
 * bpa_dyn.c holds state, and every question worth asking about it - where a
 * subscriber's SECOND block lands, whether the free stack and its index still
 * agree, whether a preference ever costs a customer a block - is a question
 * about a SEQUENCE of calls rather than about one.
 *
 * Standalone, against test/bpa_vec_shim.h, and that is the point: paired
 * address pooling (RFC 4787 REQ-2, and the required default for a CGN under
 * RFC 6888 REQ-2) used to be asserted only by labs behind docker, and the one
 * lab that measured the dynamic half - lab/crossaddr.sh - was written to pin
 * what the box did WRONG. A conformance statement whose only evidence needs a
 * container is a statement nobody checks on the way past.
 *
 * WHAT MAKES EACH CHECK ABLE TO FAIL is written beside it. Several of the
 * obvious ones are true for free on the wrong pool - "the extension is on the
 * subscriber's own address" cannot go red on a pool with ONE public address -
 * so the pools here are built to leave somewhere else to land, and a guard
 * beside each such check asserts that they did.
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "bpa_shim.h"
#include "bpa_vec_shim.h"
#include "../src/plugins/bpa/bpa_pool.h"
#include "../src/plugins/bpa/bpa_dyn.h"

static int failures = 0;
static unsigned checks = 0;

#define CHECK(cond, ...)                                                      \
  do                                                                          \
    {                                                                         \
      checks++;                                                               \
      if (!(cond))                                                            \
	{                                                                     \
	  if (failures < 12)                                                  \
	    {                                                                 \
	      printf ("  FAIL: ");                                            \
	      printf (__VA_ARGS__);                                           \
	      printf ("\n");                                                  \
	    }                                                                 \
	  failures++;                                                         \
	}                                                                     \
    }                                                                         \
  while (0)

static clib_error_t *
mkpool (bpa_pool_t *p, const char *name, u32 n_out, u16 port_max, u16 bsz,
	bpa_policy_t policy)
{
  u32 in_base = 0x64400000, in_count = 65536;
  u32 out_base = 0xc8960010, out_count = n_out;
  clib_error_t *e;

  memset (p, 0, sizeof (*p));
  p->name = (u8 *) name;
  p->port_min = 1024;
  p->port_max = port_max;
  p->block_size = bsz;
  p->policy = policy;
  p->mode = BPA_MODE_DYNAMIC;
  p->max_blocks = 4;
  e = bpa_addr_set_build (&p->inside, &in_base, &in_count, 1);
  if (e)
    return e;
  e = bpa_addr_set_build (&p->outside, &out_base, &out_count, 1);
  if (e)
    return e;
  return bpa_pool_init (p);
}

/*
 * The same pool with an operator's block stride on it.
 *
 * mkpool() memsets and never sets one, so every pool in this file had
 * block_stride 0 - and the whole stride arm of bpa_dyn_acquire_extra() is
 * behind `p->block_stride != 0 && !d->reserve`. Measured with gcov over all
 * ten C harnesses: those twelve lines, and the n_stride_placed and
 * n_stride_missed counters with them, were never executed by any of the
 * 65,376 checks this repository runs without a container.
 *
 * lab/extblocks.sh and lab/dynamic-workers.sh do reach it, so the path is not
 * unmeasured - it is measured only where measuring is expensive. A regression
 * in it goes green through every docker-free suite and is found at a release.
 */
static clib_error_t *
mkpool_stride (bpa_pool_t *p, const char *name, u32 n_out, u16 port_max,
	       u16 bsz, bpa_policy_t policy, u16 stride)
{
  clib_error_t *e = mkpool (p, name, n_out, port_max, bsz, policy);
  if (e)
    return e;
  p->block_stride = stride;
  return bpa_pool_init (p);
}

/* ------------------------------------------------------------------ */
/* a driver for a slice, so a scenario reads as a sequence               */

typedef struct
{
  bpa_dyn_t *d;	      /* one per worker */
  u32 nw;
  bpa_pool_t *p;
  bpa_block_t *primary;
  u8 *held;
  u32 n_subs;
  /* what the scenario saw, so the counters have something to be checked
   * against that was not read from the counters */
  u32 seen_ext, seen_paired, seen_refused, seen_wrong_worker;
  u32 refused_with_blocks_free;
} drv_t;

static void
drv_init (drv_t *v, bpa_pool_t *p, u32 nw, u32 n_subs)
{
  u32 w;

  memset (v, 0, sizeof (*v));
  v->p = p;
  v->nw = nw;
  v->n_subs = n_subs;
  v->d = calloc (nw, sizeof (*v->d));
  v->primary = calloc (n_subs, sizeof (*v->primary));
  v->held = calloc (n_subs, 1);
  for (w = 0; w < nw; w++)
    bpa_dyn_init (&v->d[w], p, w, nw, 0);
}

static void
drv_free (drv_t *v)
{
  u32 w;

  for (w = 0; w < v->nw; w++)
    bpa_dyn_free (&v->d[w]);
  free (v->d);
  free (v->primary);
  free (v->held);
}

static void
drv_arrive (drv_t *v, u32 s)
{
  u32 w = s % v->nw;
  int is_new;

  if (bpa_dyn_acquire (&v->d[w], v->p, s, w, v->nw, &v->primary[s], &is_new)
      == 0)
    v->held[s] = 1;
}

/* One extension, and everything worth noticing about it recorded here rather
 * than read back from the allocator's own counters. */
static void
drv_extend (drv_t *v, u32 s, u32 nth)
{
  u32 w = s % v->nw, pblk, blk_idx;
  bpa_dyn_t *d = &v->d[w];
  bpa_block_t ext;
  int is_new, had_free;

  if (!v->held[s] || bpa_dyn_block_of (d, s, w, v->nw, &pblk))
    return;
  had_free = vec_len (d->free_list) != 0;
  if (bpa_dyn_acquire_extra (d, v->p, s, w, v->nw, pblk, nth, &ext, &is_new))
    {
      v->seen_refused++;
      if (had_free)
	v->refused_with_blocks_free++;
      return;
    }
  v->seen_ext++;
  if (ext.pub_idx == v->primary[s].pub_idx)
    v->seen_paired++;
  blk_idx = d->extras[vec_len (d->extras) - 1].local * v->nw + w;
  if (blk_idx % v->nw != w)
    v->seen_wrong_worker++;
}

/* Three quarters of the subscribers go quiet and are reaped.
 *
 * Chosen by a mixer and not by `s % 4`: under `policy interleaved` the public
 * address IS s % outside.count, so a churn picked with a small modulus empties
 * whole addresses and fills others, which measures the choice of modulus
 * rather than the allocator. */
static int
goes_quiet (u32 s)
{
  u32 h = s * 2654435761u;

  h ^= h >> 15;
  return (h % 4) != 0;
}

static void
drv_churn (drv_t *v)
{
  bpa_dyn_release_t out[64];
  u32 s, w;

  for (s = 0; s < v->n_subs; s++)
    if (goes_quiet (s))
      bpa_dyn_idle (&v->d[s % v->nw], s, 1.0);
  for (w = 0; w < v->nw; w++)
    while (bpa_dyn_reap (&v->d[w], 1000.0, 120, w, v->nw, out, 64))
      ;
  for (s = 0; s < v->n_subs; s++)
    if (goes_quiet (s))
      v->held[s] = 0;
}

/* ------------------------------------------------------------------ */

enum
{
  PAT_SERIAL,	/* one subscriber takes all its blocks before the next arrives */
  PAT_PARALLEL, /* everybody arrives, then everybody extends */
  PAT_CHURN,	/* everybody arrives, most are reaped, the rest extend */
  PAT_N
};

static const char *pat_name[PAT_N] = { "serial", "parallel", "churn" };

static void
run_pattern (drv_t *v, int pat, u32 n_ext)
{
  u32 s, k;

  for (s = 0; s < v->n_subs; s++)
    {
      drv_arrive (v, s);
      if (pat == PAT_SERIAL)
	for (k = 0; k < n_ext; k++)
	  drv_extend (v, s, k + 1);
    }
  if (pat == PAT_CHURN)
    drv_churn (v);
  if (pat != PAT_SERIAL)
    for (k = 0; k < n_ext; k++)
      for (s = 0; s < v->n_subs; s++)
	drv_extend (v, s, k + 1);
}

/*
 * The free stack and its index are two spellings of one fact, and this is the
 * reader that keeps them one. Returns the number of ways they disagree.
 *
 * Three ways they can, and each is a real bug rather than a tidiness rule:
 * a free block whose index does not point at it is a block bpa_dyn_unfree()
 * will fail to take, so it can be handed to two subscribers; a held block
 * still carrying a position is one the stack may hand out while somebody is
 * using it; and a stack longer or shorter than n_local - n_alloc means a block
 * was leaked or duplicated.
 */
static u32
index_disagreements (const bpa_dyn_t *d)
{
  u32 i, bad = 0, len = vec_len (d->free_list);

  for (i = 0; i < d->n_local; i++)
    {
      if (d->owner[i] == BPA_NO_BLOCK)
	{
	  if (d->free_pos[i] >= len || d->free_list[d->free_pos[i]] != i)
	    bad++;
	}
      else if (d->free_pos[i] != BPA_NO_BLOCK)
	bad++;
    }
  if (len + d->n_alloc != d->n_local)
    bad++;
  return bad;
}

/* The oracle for bpa_pair_step(): found by searching rather than computed, so
 * the two do not share an argument. */
static u32
smallest_step (u32 unit, u32 nw)
{
  u32 s;

  for (s = 1; s < 1000000; s++)
    if ((u64) s * unit % nw == 0)
      return s;
  return 0;
}

int
main (void)
{
  bpa_pool_t p4, p64, p1, pblk;
  clib_error_t *e;
  u32 nw, i;

  e = mkpool (&p4, "four", 4, 1039, 4, BPA_POLICY_INTERLEAVED);
  CHECK (e == 0, "pool 'four' rejected");
  /* The same four addresses holding sixteen blocks each, so a worker count
   * up to eight still leaves every slice something to choose from. */
  e = mkpool (&p64, "four-deep", 4, 1279, 4, BPA_POLICY_INTERLEAVED);
  CHECK (e == 0, "pool 'four-deep' rejected");
  e = mkpool (&p1, "one", 1, 1039, 4, BPA_POLICY_INTERLEAVED);
  CHECK (e == 0, "pool 'one' rejected");
  e = mkpool (&pblk, "blocked", 4, 1039, 4, BPA_POLICY_BLOCKED);
  CHECK (e == 0, "pool 'blocked' rejected");
  if (failures)
    return bpa_test_verdict (checks, failures);

  printf ("=== how far along an address a block stays in one slice ===\n");
  {
    /*
     * bpa_pair_step() is the arithmetic the whole preference rests on: the
     * smallest number of blocks along a public address whose block-index
     * distance is a multiple of the worker count. Wrong, and the preference
     * hands a subscriber a block of ANOTHER worker's slice, whose replies
     * arrive on a thread holding no session for them.
     *
     * Checked against a search, not against the same formula.
     */
    for (nw = 1; nw <= 8; nw++)
      {
	u32 got = bpa_pair_step (&p4, nw);
	u32 want = smallest_step (bpa_stride_unit (&p4), nw);

	CHECK (got == want,
	       "4 addresses at %u workers: step %u, smallest that works is %u",
	       nw, got, want);
	got = bpa_pair_step (&pblk, nw);
	want = smallest_step (bpa_stride_unit (&pblk), nw);
	CHECK (got == want,
	       "blocked at %u workers: step %u, smallest that works is %u", nw,
	       got, want);
      }
    printf ("  interleaved over 4 addresses:");
    for (nw = 1; nw <= 8; nw++)
      printf (" %u@%uw", bpa_pair_step (&p4, nw), nw);
    printf ("\n  and it is the smallest one a search finds, at every count\n");
  }

  printf ("\n=== a dynamic extension lands on the subscriber's own "
	  "public address ===\n");
  {
    /*
     * The defect this file exists for. Four subscribers on four public
     * addresses: under `policy interleaved` the next block index is the next
     * ADDRESS, so an extension off the free stack is on somebody else's.
     *
     * SIX subscribers arrive and FOUR extend, and the two are different
     * numbers on purpose. With four of each, the stack hands out blocks 4 to 7
     * to the four extenders in the order they ask, and 4 % 4 == 0 puts every
     * one of them back on its own address by accident - the same alignment
     * luck #226 measured on its `b4` pool, and a check written that way stays
     * green with the preference deleted. Six arrivals move the stack on by
     * two, so nothing lines up and the allocator has to choose.
     *
     * Two guards, because the check is still true for free on the wrong pool.
     * The first asserts the four extenders really are on four different
     * addresses - on a one-address pool "it landed on its own" is true
     * whatever the allocator does, which is exactly how this property went
     * unmeasured until #226. The second asserts that at the moment of each
     * extension the free stack still held a block on a DIFFERENT address, so
     * the allocator had somewhere else to go and chose not to.
     */
    drv_t v;
    u32 seen_pub = 0, other_available = 0, s;

    drv_init (&v, &p4, 1, 6);
    for (s = 0; s < 6; s++)
      drv_arrive (&v, s);
    for (s = 0; s < 4; s++)
      seen_pub |= 1u << v.primary[s].pub_idx;
    CHECK (seen_pub == 0xf,
	   "the four subscribers hold %u of the 4 public addresses, so "
	   "'on its own address' is true for free",
	   __builtin_popcount (seen_pub));

    for (s = 0; s < 4; s++)
      {
	u32 k;

	for (k = 0; k < vec_len (v.d[0].free_list); k++)
	  {
	    bpa_block_t b;

	    bpa_dyn_block_addr (&p4, v.d[0].free_list[k], &b);
	    if (b.pub_idx != v.primary[s].pub_idx)
	      {
		other_available++;
		break;
	      }
	  }
	drv_extend (&v, s, 1);
      }
    CHECK (other_available == 4,
	   "only %u of 4 extensions had a free block on another public "
	   "address to take, so the pairing was not a choice",
	   other_available);
    CHECK (v.seen_ext == 4, "%u of 4 extensions were placed at all",
	   v.seen_ext);
    CHECK (v.seen_paired == 4,
	   "%u of 4 extensions landed on the holder's own public address",
	   v.seen_paired);
    printf ("  %u of %u extensions on the holder's own public address, "
	    "with a block elsewhere free every time\n",
	    v.seen_paired, v.seen_ext);

    /*
     * The counters an operator reads, against what the scenario watched
     * happen. They are the only RFC 4787 REQ-2 number the box reports for a
     * dynamic pool, so a counter incremented in the wrong branch would have
     * `show bpa blocks` state conformance the allocator did not deliver.
     */
    CHECK (v.d[0].n_paired_placed == v.seen_paired,
	   "the box counted %llu paired extensions where %u were",
	   (unsigned long long) v.d[0].n_paired_placed, v.seen_paired);
    CHECK (v.d[0].n_paired_missed == v.seen_ext - v.seen_paired,
	   "the box counted %llu unpaired extensions where %u were",
	   (unsigned long long) v.d[0].n_paired_missed,
	   v.seen_ext - v.seen_paired);
    CHECK (v.d[0].n_paired_placed + v.d[0].n_paired_missed
	     == v.d[0].n_extensions,
	   "%llu extensions, but %llu paired plus %llu missed",
	   (unsigned long long) v.d[0].n_extensions,
	   (unsigned long long) v.d[0].n_paired_placed,
	   (unsigned long long) v.d[0].n_paired_missed);
    printf ("  and the box says so itself: %llu placed, %llu missed, "
	    "%llu extensions\n", (unsigned long long) v.d[0].n_paired_placed,
	    (unsigned long long) v.d[0].n_paired_missed,
	    (unsigned long long) v.d[0].n_extensions);
    drv_free (&v);
  }

  printf ("\n=== the step holds the address at every worker count ===\n");
  {
    /*
     * bpa_dyn_take_free() does not test which worker owns the block it is
     * handed, because bpa_pair_step() is what makes every candidate congruent
     * to the primary - and the primary is this worker's. That is an invariant
     * the caller keeps rather than a fact about the callee, and it is worth
     * knowing what breaks when it stops being kept.
     *
     * NOT a cross-slice block. `bpa_join(...) / n_workers` truncates, so a
     * candidate that is not congruent resolves to a block that IS in this
     * worker's slice and is simply NOT THE ONE ASKED FOR - on another public
     * address. Asserting "the block is in my slice" is therefore a check that
     * cannot fail: `local * n_workers + worker` is congruent to worker for
     * every local there is. This one was written that way first and stayed
     * green with bpa_pair_step() returning 1, which is the whole defect.
     *
     * So what is asserted is what a wrong step actually produces: a
     * subscriber on two public addresses. The arithmetic half - that the step
     * really is congruent - is asserted against the model in
     * test/test_bpa_pool.c, where it belongs.
     *
     * TWO things have to hold of a worker count before this row says anything
     * about the allocator, and 24 arrivals over 4 addresses had neither at six
     * of the eight. Both were found by deleting the preference and watching
     * only 5 and 7 workers turn red.
     *
     * The SLICE has to span more than one public address. Under `policy
     * interleaved` block b is on address b % 4, and worker w holds the blocks
     * b = local * nw + w, so its slice reaches 4 / gcd (nw, 4) addresses -
     * exactly ONE at 4 and at 8 workers. There "the extension is on the
     * holder's own address" is the one-address pool this file's header warns
     * about, met from the other direction: the allocator cannot get it wrong
     * because there is nowhere else to put it.
     *
     * The ARRIVALS must not align the free stack. With 24 subscribers the
     * stack handed out block 24 next, and 24 % 4 == 0 landed every extension
     * back on its holder's own address by accident - the same alignment luck
     * the scenario above avoids by arriving six and extending four. 26 is not
     * a multiple of 4, and that is all that is wanted of it.
     *
     * So the guard is the one that scenario uses, applied per worker count and
     * read off the allocator rather than worked out here: at the moment of
     * each extension, did this worker's free stack still hold a block on a
     * DIFFERENT public address. Where it did for all sixteen, the pairing was
     * a choice and is asserted. Where it never did, the row is reported and
     * not asserted, and how many counts fall each way is itself checked - so a
     * pool or a worker range that quietly stops leaving anywhere else to land
     * is a red check rather than a silently emptier suite.
     */
    u32 total = 0, split = 0, measured = 0;

    for (nw = 1; nw <= 8; nw++)
      {
	drv_t v;
	u32 s, other = 0;

	drv_init (&v, &p64, nw, 26);
	for (s = 0; s < 26; s++)
	  drv_arrive (&v, s);
	for (s = 0; s < 16; s++)
	  {
	    u32 w = s % nw, k;

	    for (k = 0; k < vec_len (v.d[w].free_list); k++)
	      {
		bpa_block_t b;

		/* free_list holds LOCAL indices; the block is local * nw + w. */
		bpa_dyn_block_addr (&p64, v.d[w].free_list[k] * nw + w, &b);
		if (b.pub_idx != v.primary[s].pub_idx)
		  {
		    other++;
		    break;
		  }
	      }
	    drv_extend (&v, s, 1);
	  }
	CHECK (v.seen_ext == 16, "%u of 16 extensions placed at %u workers",
	       v.seen_ext, nw);
	if (other == 16)
	  {
	    measured++;
	    CHECK (v.seen_paired == v.seen_ext,
		   "%u of %u extensions at %u workers left their holder on two "
		   "public addresses",
		   v.seen_ext - v.seen_paired, v.seen_ext, nw);
	  }
	else
	  CHECK (other == 0,
		 "at %u workers only %u of 16 extensions had a block on "
		 "another public address free, so the row is neither a choice "
		 "nor an arithmetic certainty",
		 nw, other);
	total += v.seen_ext;
	split += v.seen_ext - v.seen_paired;
	drv_free (&v);
      }
    CHECK (measured == 6,
	   "%u of the 8 worker counts had somewhere else to put every "
	   "extension, not the 6 whose slice spans more than one address",
	   measured);
    printf ("  %u extensions over 1 to 8 workers, %u of them on a second "
	    "public address, and %u of the 8 counts had a slice able to say "
	    "so\n", total, split, measured);
  }

  printf ("\n=== the free stack and its index stay one fact ===\n");
  {
    /*
     * free_pos[] is what makes taking a NAMED block off the stack an index
     * rather than a walk of the whole slice, and it is maintained by hand at
     * four call sites. A stale entry hands one block to two subscribers.
     *
     * Driven through every path that moves the stack - allocate, extend, pin,
     * unpin, reap - because the displacement vec_del1() causes is exactly
     * what a hand-maintained inverse gets wrong.
     */
    drv_t v;
    u32 bad = 0, s;
    int released;

    drv_init (&v, &p4, 2, 8);
    run_pattern (&v, PAT_SERIAL, 2);
    for (nw = 0; nw < 2; nw++)
      bad += index_disagreements (&v.d[nw]);
    CHECK (bad == 0, "after allocating and extending: %u disagreements", bad);

    for (s = 0; s < 8; s++)
      {
	int is_new;
	u32 held_by;

	bpa_dyn_pin (&v.d[s % 2], (s / 2) * 2 + (s % 2), s, 2, 1.0, &held_by,
		     &is_new);
      }
    bad = 0;
    for (nw = 0; nw < 2; nw++)
      bad += index_disagreements (&v.d[nw]);
    CHECK (bad == 0, "after pinning: %u disagreements", bad);

    for (s = 0; s < 8; s++)
      bpa_dyn_unpin (&v.d[s % 2], (s / 2) * 2 + (s % 2), 2, &released);
    drv_churn (&v);
    bad = 0;
    for (nw = 0; nw < 2; nw++)
      bad += index_disagreements (&v.d[nw]);
    CHECK (bad == 0, "after unpinning and reaping: %u disagreements", bad);
    printf ("  every free block found by its index, every held one absent "
	    "from the stack, across allocate, extend, pin, unpin and reap\n");
    drv_free (&v);
  }

  printf ("\n=== the preference never costs a subscriber a block ===\n");
  {
    /*
     * The half of "a preference and not a rule" that a pairing rate cannot
     * show. An extension is refused only when the slice has NO free block at
     * all; a preference that refused because it could not pair would turn a
     * property about layout into a customer losing traffic, and would look
     * from outside like a pool that is full.
     */
    u32 refused_wrongly = 0, ext = 0, pat;

    for (pat = 0; pat < PAT_N; pat++)
      for (nw = 1; nw <= 4; nw++)
	{
	  drv_t v;

	  drv_init (&v, &p4, nw, 16);
	  run_pattern (&v, pat, 3);
	  refused_wrongly += v.refused_with_blocks_free;
	  ext += v.seen_ext;
	  drv_free (&v);
	}
    CHECK (refused_wrongly == 0,
	   "%u extensions were refused while the slice still had a free "
	   "block", refused_wrongly);
    printf ("  %u extensions placed over 3 patterns and 4 worker counts, "
	    "and no refusal with a block free\n", ext);
  }

  printf ("\n=== how often it pairs, over shapes and arrival patterns ===\n");
  {
    /*
     * The rate, stated rather than asserted at 100%, because the preference
     * is bounded and the bound is the honest part: BPA_PAIR_WALK candidates
     * per walk, so an address whose candidates are all taken falls back to
     * the free stack.
     *
     * The floors below are what this allocator measured over exactly these
     * shapes, rounded down to a whole percent, so a change that moves any of
     * them is a change somebody has to explain. Measured: 100%, 99.6% and
     * 99.2% here, and 100%, 99.1% and 97.6% at ten times the subscribers.
     * On the allocator this replaced the same three read 0.0%, 0.0% and 0.2%.
     *
     * The grid is 9 address counts by 8 worker counts and the LOWEST cell of
     * each row is what is asserted, not the mean. Five and seven workers are
     * in it deliberately: they share no factor with any of these address
     * counts, so bpa_pair_step() is the worker count itself and the
     * candidates on one address are at their sparsest - which is where an
     * earlier bound of 16 was found to give up 23 points that 32 keeps.
     */
    static const u32 addrs[] = { 1, 2, 4, 8, 16, 32, 64, 128, 256 };
    static const u32 floor_pct[PAT_N] = { 100, 99, 99 };
    u32 pat, ai, worst[PAT_N];

    for (pat = 0; pat < PAT_N; pat++)
      worst[pat] = 100;
    for (pat = 0; pat < PAT_N; pat++)
      {
	printf ("  %-9s", pat_name[pat]);
	for (ai = 0; ai < 9; ai++)
	  {
	    u32 low = 100;

	    for (nw = 1; nw <= 8; nw++)
	      {
		bpa_pool_t p;
		drv_t v;
		u32 pct;

		if (mkpool (&p, "sweep", addrs[ai], 16383, 4,
			    BPA_POLICY_INTERLEAVED))
		  continue;
		drv_init (&v, &p, nw, 2000);
		run_pattern (&v, pat, 2);
		pct = v.seen_ext ? 100 * v.seen_paired / v.seen_ext : 100;
		if (pct < low)
		  low = pct;
		drv_free (&v);
	      }
	    printf (" %3u:%3u%%", addrs[ai], low);
	    if (low < worst[pat])
	      worst[pat] = low;
	  }
	printf ("\n");
      }
    for (pat = 0; pat < PAT_N; pat++)
      CHECK (worst[pat] >= floor_pct[pat],
	     "%s arrivals paired %u%% of extensions at worst, below the %u%% "
	     "this allocator measured",
	     pat_name[pat], worst[pat], floor_pct[pat]);
  }

  printf ("\n=== a deterministic reserve prefers the same address ===\n");
  {
    /*
     * The other allocator behind the same counters. A deterministic pool has
     * no free blocks - bpa_forward() assigns every one of them - so its
     * extensions come out of the reserve, and the reserve has the same
     * preference for the subscriber's own public address and the same
     * fall-back.
     *
     * Driven here because until now it was asserted only by lab/detreserve.sh,
     * behind docker, and that lab's own REQ-2 line ran on a pool with ONE
     * public address for six releases - where "it landed on its own address"
     * is true whatever the allocator does. Four addresses with one reserve
     * block each, and the same guard: the four holders must be on four
     * different addresses or this says nothing.
     */
    bpa_pool_t pr;
    bpa_dyn_t d;
    u32 s, seen_pub = 0, paired = 0, placed = 0;

    memset (&pr, 0, sizeof (pr));
    e = mkpool (&pr, "reserve", 4, 1055, 4, BPA_POLICY_INTERLEAVED);
    CHECK (e == 0, "pool 'reserve' rejected");
    pr.mode = BPA_MODE_DETERMINISTIC;
    pr.block_reserve = 1;
    e = bpa_pool_init (&pr);
    CHECK (e == 0, "pool 'reserve' rejected after the reserve was carved out");
    CHECK (pr.reserve_count == 4, "the reserve holds %u blocks, not 4",
	   pr.reserve_count);

    CHECK (bpa_dyn_init (&d, &pr, 0, 1, 1) == 0, "the reserve slice refused");
    /*
     * Descending subscriber order against an ascending free stack, so the
     * block on top is never the one the holder's own address holds and the
     * allocator has to choose. Ascending order pairs by accident here, which
     * is the same alignment luck the dynamic scenario above avoids.
     */
    for (s = 4; s-- > 0;)
      {
	bpa_block_t ext;
	u32 pub_idx, blk_idx;
	int is_new;

	bpa_split (&pr, s, &pub_idx, &blk_idx);
	seen_pub |= 1u << pub_idx;
	if (bpa_dyn_acquire_extra (&d, &pr, s, 0, 1, BPA_NO_BLOCK, 1, &ext,
				   &is_new))
	  continue;
	placed++;
	if (ext.pub_idx == pub_idx)
	  paired++;
      }
    CHECK (seen_pub == 0xf,
	   "the four subscribers hold %u of the 4 public addresses, so "
	   "'on its own address' is true for free",
	   __builtin_popcount (seen_pub));
    CHECK (placed == 4, "%u of 4 reserve extensions were placed at all",
	   placed);
    CHECK (paired == 4,
	   "%u of 4 reserve extensions landed on the holder's own public "
	   "address", paired);
    CHECK (d.n_paired_placed == paired && d.n_paired_missed == placed - paired,
	   "the reserve counted %llu placed and %llu missed where %u and %u "
	   "happened",
	   (unsigned long long) d.n_paired_placed,
	   (unsigned long long) d.n_paired_missed, paired, placed - paired);
    printf ("  %u of %u reserve extensions on the holder's own public "
	    "address, over 4 addresses, and the counters say the same\n",
	    paired, placed);
    bpa_dyn_free (&d);
  }

  printf ("\n=== one public address: paired whatever the allocator does ===\n");
  {
    /*
     * The control. On a pool with one public address every extension is on
     * the subscriber's own address by arithmetic, so this row is green with
     * the preference switched off - which is what the check above needs four
     * addresses to avoid, and what made lab/detreserve.sh's own REQ-2
     * assertion unable to fail for six releases.
     */
    drv_t v;

    drv_init (&v, &p1, 1, 2);
    run_pattern (&v, PAT_SERIAL, 1);
    CHECK (v.seen_ext > 0, "no extension was placed on the one-address pool");
    CHECK (v.seen_paired == v.seen_ext,
	   "%u of %u extensions on a ONE-address pool were somewhere else, "
	   "which is arithmetically impossible",
	   v.seen_ext - v.seen_paired, v.seen_ext);
    printf ("  %u of %u, and it would say the same with the preference "
	    "deleted, which is why it is not the check above\n",
	    v.seen_paired, v.seen_ext);
    drv_free (&v);
  }

  printf ("\n=== an operator's block stride is honoured in dynamic mode ===\n");
  {
    /*
     * THE ARM NOTHING HERE HAD EVER ENTERED. Every pool above has
     * block_stride 0, and bpa_dyn_acquire_extra()'s stride placement is behind
     * `p->block_stride != 0 && !d->reserve`. gcov over all ten C harnesses:
     * those lines and both of their counters, never executed.
     *
     * One worker, so the "another worker's slice" fall-back cannot be what
     * answers this - with nw 1 every block is in the slice, and a wrong step
     * lands on a block that is free and simply not the one the stride names.
     */
    bpa_pool_t ps;
    clib_error_t *e;
    drv_t v;
    u32 pblk = 0, want = 0, got = 0, w = 0;

    e = mkpool_stride (&ps, "stride", 4, 1279, 4, BPA_POLICY_INTERLEAVED, 2);
    CHECK (e == 0, "the strided pool did not build");
    if (e == 0)
      {
	drv_init (&v, &ps, 1, 1);
	drv_arrive (&v, 0);
	CHECK (v.held[0], "the subscriber took no primary, so nothing below "
			  "is about a stride");
	if (v.held[0] && bpa_dyn_block_of (&v.d[w], 0, w, 1, &pblk) == 0)
	  {
	    drv_extend (&v, 0, 1);
	    CHECK (v.seen_ext == 1, "the first extension was refused");
	    got = v.d[w].extras[vec_len (v.d[w].extras) - 1].local * 1 + w;
	    {
	      u32 ppub, pblk_i, gpub, gblk_i;
	      bpa_split (&ps, pblk, &ppub, &pblk_i);
	      bpa_split (&ps, got, &gpub, &gblk_i);
	      want = pblk_i + 2;
	      CHECK (gpub == ppub,
		     "the extension left the subscriber's public address, so "
		     "a stride along it cannot be what placed it: %u then %u",
		     ppub, gpub);
	      CHECK (gblk_i == want,
		     "the extension is block %u along that address and the "
		     "operator's stride of 2 from %u names %u: the number "
		     "they typed decided nothing",
		     gblk_i, pblk_i, want);
	    }
	    /*
	     * AND THE BOX SAYS IT DID IT. The counters are what an operator
	     * reads to know whether their stride is being honoured, and a
	     * placement that happens without being counted reports a pool as
	     * ignoring a number it is obeying.
	     */
	    CHECK (v.d[w].n_stride_placed == 1,
		   "the block went where the stride named and the box counted "
		   "%llu placements",
		   (unsigned long long) v.d[w].n_stride_placed);
	    CHECK (v.d[w].n_stride_missed == 0,
		   "the box counted %llu missed strides on a placement that "
		   "hit",
		   (unsigned long long) v.d[w].n_stride_missed);
	    printf ("  block %u, which is the stride of 2 from %u, and the "
		    "box counted it\n", got, pblk);
	  }
	drv_free (&v);
      }
  }

  (void) i;
  return bpa_test_verdict (checks, failures);
}
