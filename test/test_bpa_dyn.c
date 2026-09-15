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
 * n_stride_missed counters with them, were never executed by a single one of
 * the checks this repository runs without a container. The count is what
 * `make test` prints; it was 65,376 when this was written and is larger every
 * time a case is added, which is why it is not written here.
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
  u32 w = s % v->nw, pblk = BPA_NO_BLOCK, blk_idx;
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

  printf ("\n=== a second flow gets the block the subscriber already holds "
	  "===\n");
  {
    /*
     * THE PATH EVERY PACKET AFTER THE FIRST TAKES, and nothing here had ever
     * entered it. Measured with gcov over all ten C harnesses: 864,834 calls
     * to bpa_dyn_acquire() and not one of them found the subscriber already in
     * by_sub, because every scenario above acquires once per subscriber and
     * then extends. The three lines behind `q != 0` - the ones that hand back
     * the block the subscriber is holding and stop its hold-down - were never
     * executed by any of the checks this repository runs without a container.
     *
     * Two things go wrong when they go wrong, and a counter shows neither:
     *
     *   the subscriber is handed a SECOND block for its second flow, which is
     *   one customer on two public port ranges - the RFC 4787 REQ-2 failure
     *   the whole file above is about, arriving through the ordinary path
     *   rather than through an extension - and a pool that empties at the rate
     *   FLOWS arrive rather than at the rate subscribers do;
     *
     *   and the allocation is logged again. RFC 8158 natEvent 16 with no 17
     *   between it and the last one is the gap docs/logging.md exists to
     *   close, and it would be emitted once per flow.
     *
     * What the block must be is read from bpa_dyn_block_of() and turned into a
     * port range by the pool's own arithmetic, NOT compared against what the
     * first call to bpa_dyn_acquire() returned: two calls to one function
     * agreeing about a wrong answer is not evidence.
     */
    drv_t v;
    bpa_dyn_t *d;
    bpa_block_t again;
    u32 blk = 0, pub_idx = 0, blk_idx = 0, want_lo, free_before;
    u64 allocs_before;
    int is_new = -1;

    drv_init (&v, &p4, 1, 3);
    drv_arrive (&v, 0);
    drv_arrive (&v, 1);
    d = &v.d[0];
    CHECK (v.held[0], "subscriber 0 holds no block, so nothing below is about "
		      "a second flow");
    CHECK (bpa_dyn_block_of (d, 0, 0, 1, &blk) == 0,
	   "the allocator will not say which block subscriber 0 holds");

    /* One worker, so the local index and the block index are the same number,
     * and the port range is the pool's arithmetic rather than the allocator's
     * answer. */
    bpa_split (&p4, blk, &pub_idx, &blk_idx);
    want_lo = p4.port_min + blk_idx * p4.block_size;

    allocs_before = d->n_allocations;
    free_before = vec_len (d->free_list);
    memset (&again, 0, sizeof (again));
    CHECK (bpa_dyn_acquire (d, &p4, 0, 0, 1, &again, &is_new) == 0,
	   "a second flow from a subscriber that already holds a block was "
	   "refused");
    CHECK (again.port_lo == want_lo,
	   "the second flow is translated out of ports %u-%u, where the block "
	   "subscriber 0 holds starts at %u",
	   again.port_lo, again.port_hi, want_lo);
    CHECK (again.pub_idx == pub_idx,
	   "the second flow left on public address index %u and the first on "
	   "%u: one subscriber, two public addresses",
	   again.pub_idx, pub_idx);
    CHECK (is_new == 0,
	   "the second flow was reported as a fresh allocation, which is a "
	   "natEvent 16 with no release between it and the last one");
    CHECK (d->n_allocations == allocs_before,
	   "%llu lifetime allocations after a flow that allocated nothing, "
	   "against %llu before it",
	   (unsigned long long) d->n_allocations,
	   (unsigned long long) allocs_before);
    CHECK (vec_len (d->free_list) == free_before,
	   "the free stack went from %u blocks to %u on a flow that needed "
	   "none", free_before, (u32) vec_len (d->free_list));

    /*
     * And the stamp. bpa_dyn_idle() starts the hold-down when a subscriber's
     * LAST session ends, so a flow arriving before it expires has to stop it -
     * otherwise the reap takes the block out from under a subscriber that is
     * using it, and the customer loses every established flow at once.
     *
     * This is what bpa_dyn.h's note about the deleted bpa_dyn_active() rests
     * on: clearing the stamp on the PRIMARY alone is deliberate, and it only
     * happens here.
     */
    bpa_dyn_idle (d, 0, 100.0);
    CHECK (d->idle_since[blk] == 100.0,
	   "the subscriber going quiet did not start a hold-down, so the "
	   "check below has nothing to stop");
    CHECK (bpa_dyn_acquire (d, &p4, 0, 0, 1, &again, &is_new) == 0,
	   "the subscriber coming back before its hold-down expired was "
	   "refused");
    CHECK (d->idle_since[blk] == 0,
	   "the subscriber came back and its block is still counting down "
	   "from %.1f", d->idle_since[blk]);
    {
      bpa_dyn_release_t out[8];
      u32 n = bpa_dyn_reap (d, 1000.0, 120, 0, 1, out, 8);
      u32 owner_sub = ~0u;

      CHECK (n == 0, "the reap released %u blocks where every holder is live",
	     n);
      CHECK (bpa_dyn_owner (d, blk, 1, &owner_sub) == 0 && owner_sub == 0,
	     "the block of a subscriber that came back 900 seconds ago was "
	     "reaped anyway");
    }
    /*
     * And with an EXTENSION held. bpa_dyn_acquire_extra() deliberately leaves
     * by_sub alone, because by_sub names the subscriber's FIRST block and this
     * path has to keep handing that one back. Every case above reaches the
     * `q != 0` branch on a subscriber holding exactly one block, so the
     * extension is not there to be returned by mistake. Measured: with
     * `hash_set (d->by_sub, sub_idx, local + 1)` added beside the extras
     * entry, all 143 checks of this file passed and a subscriber's ordinary
     * flows would move to its second port range.
     */
    {
      u32 ext_blk;

      drv_extend (&v, 0, 1);
      CHECK (v.seen_ext == 1, "the extension was refused, so nothing below is "
			      "about a subscriber holding two blocks");
      ext_blk = d->extras[vec_len (d->extras) - 1].local * 1 + 0;
      CHECK (ext_blk != blk,
	     "the extension landed on block %u, the same one the primary "
	     "holds, so the two cannot be told apart below", ext_blk);
      memset (&again, 0, sizeof (again));
      CHECK (bpa_dyn_acquire (d, &p4, 0, 0, 1, &again, &is_new) == 0,
	     "a flow from a subscriber holding a primary and an extension was "
	     "refused");
      CHECK (again.port_lo == want_lo,
	     "the flow was translated out of ports %u-%u, where the "
	     "subscriber's FIRST block starts at %u: an ordinary flow moved "
	     "onto the extension", again.port_lo, again.port_hi, want_lo);
    }

    printf ("  the same block, the same public address, no second allocation "
	    "record, and the hold-down stopped\n");
    drv_free (&v);
  }

  printf ("\n=== what the reap will not take ===\n");
  {
    /*
     * Three blocks, each idle, each held for a different reason, and the reap
     * has to tell them apart. Two of those three refusals had never been
     * executed by anything docker-free.
     *
     * A PINNED block is the operator's static forward. Reaping it gives the
     * block to the next subscriber that asks and leaves the forward pointing
     * at them - inbound traffic for one customer delivered to another, which
     * is the failure that kept forwards off dynamic pools until the pin
     * existed. The hold-down is minutes and a forwarded port has to survive a
     * night with no traffic, so "idle for longer than the hold-down" is
     * exactly the state it is in every morning.
     *
     * A block still INSIDE its hold-down belongs to a subscriber that may come
     * back. Taking it early is the churn the hold-down exists to remove, and
     * every one of those is an RFC 8158 natEvent 17 followed by a 16.
     *
     * The three are driven together because what makes the check able to fail
     * is the block beside it: the reap must release the one whose hold-down
     * HAS expired in the same pass, so a reap that refused everything would go
     * red too.
     */
    drv_t v;
    bpa_dyn_t *d;
    bpa_dyn_release_t out[8];
    u32 pinned_blk = 0, fresh_blk = 0, expired_blk = 0;
    u32 owner_sub = ~0u, n;
    int is_new = 0, released = 0;

    drv_init (&v, &p4, 1, 3);
    drv_arrive (&v, 0);
    drv_arrive (&v, 1);
    drv_arrive (&v, 2);
    d = &v.d[0];
    CHECK (bpa_dyn_block_of (d, 0, 0, 1, &pinned_blk) == 0
	     && bpa_dyn_block_of (d, 1, 0, 1, &expired_blk) == 0
	     && bpa_dyn_block_of (d, 2, 0, 1, &fresh_blk) == 0,
	   "the three subscribers did not all take a block, so the reap below "
	   "has nothing to tell apart");

    CHECK (bpa_dyn_pin (d, pinned_blk, 0, 1, 10.0, &owner_sub, &is_new) == 0,
	   "the block of subscriber 0 could not be pinned");

    /* All three go quiet at the same instant, so the only thing separating
     * them at the reap is how long ago that was and whether one is pinned. */
    bpa_dyn_idle (d, 0, 10.0);
    bpa_dyn_idle (d, 1, 10.0);
    bpa_dyn_idle (d, 2, 900.0);

    n = bpa_dyn_reap (d, 1000.0, 120, 0, 1, out, 8);
    CHECK (n == 1,
	   "the reap released %u blocks of the three: one hold-down had "
	   "expired, one had not, and one is pinned", n);
    CHECK (n == 1 && out[0].block_idx == expired_blk && out[0].sub_idx == 1,
	   "the reap released block %u of subscriber %u where the only "
	   "expired holder is subscriber 1 on block %u",
	   n ? out[0].block_idx : ~0u, n ? out[0].sub_idx : ~0u, expired_blk);

    owner_sub = ~0u;
    CHECK (bpa_dyn_owner (d, pinned_blk, 1, &owner_sub) == 0 && owner_sub == 0,
	   "the pinned block was reaped 990 seconds into a 120 second "
	   "hold-down, so the operator's forward now points at whoever takes "
	   "it next");
    owner_sub = ~0u;
    CHECK (bpa_dyn_owner (d, fresh_blk, 1, &owner_sub) == 0 && owner_sub == 2,
	   "the block of a subscriber 100 seconds into a 120 second "
	   "hold-down was released");
    CHECK (d->n_releases == 1,
	   "the box counted %llu releases where one block went back",
	   (unsigned long long) d->n_releases);
    CHECK (index_disagreements (d) == 0,
	   "the free stack and its index disagree after a partial reap");

    /* And the pin is not a permanent hold: once the forward goes, the
     * ordinary lifetime applies with no special case to remember, which is
     * what makes the refusal above a statement about the PIN rather than
     * about this block. */
    bpa_dyn_unpin (d, pinned_blk, 1, &released);
    owner_sub = ~0u;
    CHECK (bpa_dyn_owner (d, pinned_blk, 1, &owner_sub) != 0,
	   "the block is still held by subscriber %u after the forward it was "
	   "holding for was removed", owner_sub);
    printf ("  the pinned one kept, the one still counting down kept, the "
	    "expired one released, and the pin is not forever\n");
    drv_free (&v);
  }

  printf ("\n=== an extension goes quiet with the subscriber holding it ===\n");
  {
    /*
     * bpa_dyn_idle() stamps the primary out of by_sub and then walks extras
     * for the same subscriber. Without that walk the extension is owned
     * forever: its idle_since stays zero, the reap skips it for the same
     * reason it skips a live block, and the pool is a slice that slowly stops
     * having free blocks with nothing in any counter saying why.
     *
     * Nothing above reaches it. drv_churn() idles and reaps, but every
     * subscriber it quietens holds one block at that point, so the walk runs
     * over an empty list. Measured: with the vec_foreach over extras deleted,
     * all 143 checks of this file passed.
     *
     * Both blocks in one reap, so a reap that released nothing would go red
     * on the primary as well and the count below is about the extension.
     */
    drv_t v;
    bpa_dyn_t *d;
    bpa_dyn_release_t out[8];
    u32 free_before, n;

    drv_init (&v, &p4, 1, 3);
    drv_arrive (&v, 0);
    d = &v.d[0];
    CHECK (v.held[0], "subscriber 0 took no primary");
    drv_extend (&v, 0, 1);
    CHECK (v.seen_ext == 1, "the extension was refused, so there is no second "
			    "block to go quiet");
    free_before = vec_len (d->free_list);

    bpa_dyn_idle (d, 0, 10.0);
    n = bpa_dyn_reap (d, 1000.0, 120, 0, 1, out, 8);
    CHECK (n == 2,
	   "the subscriber went quiet holding two blocks and the reap "
	   "released %u: an extension whose hold-down never started is owned "
	   "for the life of the process", n);
    CHECK (vec_len (d->free_list) == free_before + 2,
	   "the free stack went from %u blocks to %u where two were released",
	   free_before, (u32) vec_len (d->free_list));
    CHECK (index_disagreements (d) == 0,
	   "the free stack and its index disagree after both blocks went "
	   "back");
    printf ("  the primary and the extension both released\n");
    drv_free (&v);
  }

  printf ("\n=== a pin this worker cannot honour is refused, and says which "
	  "kind ===\n");
  {
    /*
     * bpa_dyn_pin() has two refusals and they go to different places. -1 is
     * "another subscriber is holding it right now", which an operator can act
     * on and which names whose sessions the pin would have broken; -2 is "the
     * block is not in this worker's slice", which is a caller that resolved
     * the wrong worker and nothing an operator can do anything about.
     *
     * The -2 had never been executed. It is the guard that stands between a
     * mistaken block index and a write past the end of owner[], and it is the
     * only thing between a pin aimed at the wrong worker and a pin silently
     * landing on whatever block that worker holds at the truncated index.
     */
    drv_t v;
    bpa_dyn_t *d, idle;
    u32 held_by = ~0u, blk = 0;
    int is_new = 1;

    /* Subscribers 0 and 2, because both map to worker 0 - bpa_owner_worker is
     * sub_idx % n_workers - so the counts below are about one slice. */
    drv_init (&v, &p4, 2, 4);
    drv_arrive (&v, 0);
    drv_arrive (&v, 2);
    d = &v.d[0];
    CHECK (d->n_local > 0 && d->n_local < p4.capacity,
	   "worker 0 holds %u of the pool's %u blocks, so a block index past "
	   "its slice is not a thing this pool has",
	   d->n_local, p4.capacity);

    /* A block index whose local index is one past the end of this slice. The
     * arithmetic is the allocator's own: local = block_idx / n_workers. */
    CHECK (bpa_dyn_pin (d, d->n_local * 2, 3, 2, 1.0, &held_by, &is_new) == -2,
	   "a pin of local block %u, one past the %u this worker's slice "
	   "holds, was not refused as the wrong worker",
	   d->n_local, d->n_local);
    CHECK (is_new == 0,
	   "a refused pin reported an allocation, which is a natEvent 16 for "
	   "a block nobody was given");
    CHECK (d->n_pinned == 0 && d->n_alloc == 2,
	   "the refused pin left %u pins and %u blocks held, against 0 and 2 "
	   "before it", d->n_pinned, d->n_alloc);

    /* And the same refusal for a slice that never came up. A zeroed
     * bpa_dyn_t is what bpa_dyn_free() leaves behind and what a pool that
     * failed to initialise has. */
    memset (&idle, 0, sizeof (idle));
    held_by = ~0u;
    is_new = 1;
    CHECK (bpa_dyn_pin (&idle, 0, 0, 1, 1.0, &held_by, &is_new) == -2,
	   "a pin against a slice that never came up was not refused");

    /* The other refusal, for contrast: it is the one an operator is told
     * about, and it carries whose block it is. */
    CHECK (bpa_dyn_block_of (d, 0, 0, 2, &blk) == 0,
	   "subscriber 0 holds no block on worker 0");
    held_by = ~0u;
    CHECK (bpa_dyn_pin (d, blk, 99, 2, 1.0, &held_by, &is_new) == -1,
	   "pinning a block another subscriber is holding was not refused");
    CHECK (held_by == 0,
	   "the refusal named subscriber %u as the holder where subscriber 0 "
	   "holds it, so an operator is told the wrong sessions would break",
	   held_by);
    printf ("  the wrong worker and the wrong holder are two refusals and the "
	    "box tells them apart\n");
    drv_free (&v);
  }

  printf ("\n=== a pin that takes a free block is an allocation, once ===\n");
  {
    /*
     * The other half of bpa_dyn_pin(), and none of it had been executed: the
     * branch that takes a block off the FREE STACK because the operator
     * configured a forward on a block nobody is holding.
     *
     * *is_new is what becomes RFC 8158 natEvent 16, and it has to be set
     * exactly once. Set on a pin of a block the subscriber already holds, the
     * box emits a second allocation record with no release between them -
     * which is the gap docs/logging.md exists to close, and a forward
     * re-applied at every configuration reload would emit one per reload.
     * Not set when the block really did come off the free stack, the block is
     * held for the life of the forward and no record says it ever left the
     * pool.
     */
    drv_t v;
    bpa_dyn_t *d;
    u32 held_by = ~0u, free_blk, owner_sub = ~0u, free_before;
    u64 allocs_before;
    int is_new = 0;

    drv_init (&v, &p4, 1, 2);
    drv_arrive (&v, 0);
    d = &v.d[0];
    CHECK (vec_len (d->free_list) > 0,
	   "the slice has no free block, so there is nothing for a pin to "
	   "take");
    /* The block the next allocation would have been given, so this really is
     * the free stack being taken from and not a block nobody wanted. */
    free_blk = d->free_list[vec_len (d->free_list) - 1];
    free_before = vec_len (d->free_list);
    allocs_before = d->n_allocations;

    CHECK (bpa_dyn_pin (d, free_blk, 7, 1, 50.0, &held_by, &is_new) == 0,
	   "a pin of a block nobody holds was refused");
    CHECK (is_new == 1,
	   "the block came off the free stack and the caller was not told to "
	   "emit a natEvent 16, so nothing records that it left the pool");
    CHECK (bpa_dyn_owner (d, free_blk, 1, &owner_sub) == 0 && owner_sub == 7,
	   "the pinned block is held by subscriber %u, not by the one the "
	   "forward names", owner_sub);
    CHECK (vec_len (d->free_list) == free_before - 1,
	   "the free stack went from %u to %u blocks on a pin that took one",
	   free_before, (u32) vec_len (d->free_list));
    CHECK (d->n_allocations == allocs_before + 1 && d->n_alloc == 2,
	   "%llu lifetime allocations and %u blocks held after a pin that "
	   "allocated one, against %llu and 1 before",
	   (unsigned long long) d->n_allocations, d->n_alloc,
	   (unsigned long long) allocs_before);
    CHECK (d->n_pinned == 1, "%u blocks are pinned, not the one that is",
	   d->n_pinned);
    CHECK (d->idle_since[free_blk] == 50.0,
	   "the pinned block is not idle from the instant it was pinned, so "
	   "the ordinary lifetime does not apply to it when the pin goes");
    CHECK (index_disagreements (d) == 0,
	   "the free stack and its index disagree after a pin took a block "
	   "off it");

    /* The same pin again, which is what a configuration reload does. */
    is_new = 1;
    CHECK (bpa_dyn_pin (d, free_blk, 7, 1, 60.0, &held_by, &is_new) == 0,
	   "re-pinning a block the subscriber already holds was refused");
    CHECK (is_new == 0,
	   "re-applying the same forward reported a second allocation, which "
	   "is a natEvent 16 with no release between it and the first");
    CHECK (d->n_allocations == allocs_before + 1,
	   "%llu lifetime allocations after a pin that allocated nothing",
	   (unsigned long long) d->n_allocations);
    CHECK (d->n_pinned == 1, "%u pins after pinning one block twice",
	   d->n_pinned);
    printf ("  taken off the free stack, one allocation record, and a second "
	    "pin of it is not another\n");
    drv_free (&v);
  }

  printf ("\n=== unpinning a block nothing is using gives it straight back "
	  "===\n");
  {
    /*
     * The whole release arm of bpa_dyn_unpin() - eighteen lines - had never
     * been executed by anything docker-free.
     *
     * A pin holds a block against the hold-down. When the last forward in it
     * goes and nothing is using it, the pin was the only thing holding it, so
     * it goes back NOW rather than sitting out a hold-down that is measuring
     * nothing. *released is what tells the caller to emit RFC 8158
     * natEvent 17; without it the block is back in the pool, handed to the
     * next subscriber that asks, and the audit trail still says the first one
     * has it - which is the record an operator answers a subpoena from.
     *
     * The mirror is asserted beside it, because a release that fired
     * unconditionally would be the worse bug: a block with live sessions on it
     * would be handed away underneath them.
     */
    drv_t v;
    bpa_dyn_t *d;
    u32 held_by = ~0u, free_blk, busy_blk = 0, owner_sub = ~0u, free_before;
    u64 releases_before;
    int is_new = 0, released = 1;

    drv_init (&v, &p4, 1, 2);
    drv_arrive (&v, 0);
    d = &v.d[0];
    free_blk = d->free_list[vec_len (d->free_list) - 1];
    CHECK (bpa_dyn_pin (d, free_blk, 7, 1, 50.0, &held_by, &is_new) == 0,
	   "a pin of a block nobody holds was refused");
    free_before = vec_len (d->free_list);
    releases_before = d->n_releases;

    released = 0;
    bpa_dyn_unpin (d, free_blk, 1, &released);
    CHECK (released == 1,
	   "the pin was the only thing holding the block and the caller was "
	   "not told to emit a natEvent 17, so the audit trail still says "
	   "subscriber 7 has it");
    CHECK (bpa_dyn_owner (d, free_blk, 1, &owner_sub) != 0,
	   "the block is still held by subscriber %u after the only reason to "
	   "hold it went away", owner_sub);
    CHECK (vec_len (d->free_list) == free_before + 1,
	   "the free stack went from %u to %u blocks on a release that "
	   "returned one", free_before, (u32) vec_len (d->free_list));
    CHECK (d->n_releases == releases_before + 1 && d->n_alloc == 1,
	   "%llu lifetime releases and %u blocks held, against %llu and 2 "
	   "before the block went back",
	   (unsigned long long) d->n_releases, d->n_alloc,
	   (unsigned long long) releases_before);
    CHECK (d->n_pinned == 0, "%u blocks are still pinned", d->n_pinned);
    CHECK (bpa_dyn_block_of (d, 7, 0, 1, &busy_blk) != 0,
	   "the allocator still says subscriber 7 holds block %u, so the next "
	   "pop hands one block to two subscribers", busy_blk);
    CHECK (index_disagreements (d) == 0,
	   "the free stack and its index disagree after an unpin released a "
	   "block");

    /* The mirror: a block a subscriber is USING leaves by the hold-down like
     * any other, and the unpin releases nothing. */
    CHECK (bpa_dyn_block_of (d, 0, 0, 1, &busy_blk) == 0,
	   "subscriber 0 holds no block");
    CHECK (bpa_dyn_pin (d, busy_blk, 0, 1, 70.0, &held_by, &is_new) == 0,
	   "pinning the block its own subscriber is using was refused");
    released = 1;
    bpa_dyn_unpin (d, busy_blk, 1, &released);
    CHECK (released == 0,
	   "unpinning a block with live sessions on it emitted a natEvent 17 "
	   "for a block that did not go back");
    owner_sub = ~0u;
    CHECK (bpa_dyn_owner (d, busy_blk, 1, &owner_sub) == 0 && owner_sub == 0,
	   "the block of a subscriber with live sessions was handed back to "
	   "the pool when its forward was removed");
    printf ("  back to the stack with a release record, and the one in use "
	    "kept with none\n");
    drv_free (&v);
  }

  printf ("\n=== a slice with no blocks in it: refused, or empty on purpose "
	  "===\n");
  {
    /*
     * The two arms of the one condition in bpa_dyn_init(), and neither had
     * been executed.
     *
     * A DYNAMIC pool whose capacity does not reach a worker cannot translate
     * for the subscribers that hash to it at all, so it is refused at
     * configuration time rather than at the first packet.
     *
     * A RESERVE slice that does not reach a worker is created EMPTY instead,
     * and bpa_dyn.c says why at length: the worker count is a startup setting
     * and not part of the pool, so refusing here would mean a configuration
     * accepted on a single-threaded box stops the pool coming up after the
     * same box is given four workers - every subscriber on it losing traffic,
     * to enforce a property about extensions. What the empty slice does
     * instead is refuse every extension and count it.
     *
     * Both arms of one `if`, so a test of the dynamic half alone would stay
     * green with the reserve half deleted.
     */
    bpa_pool_t pn;
    bpa_dyn_t d;
    clib_error_t *e2;
    bpa_block_t ext;
    int is_new = 0;

    e2 = mkpool (&pn, "narrow", 1, 1039, 4, BPA_POLICY_INTERLEAVED);
    CHECK (e2 == 0, "pool 'narrow' rejected");
    CHECK (pn.capacity == 4,
	   "pool 'narrow' holds %u blocks, and the worker counts below are "
	   "chosen against 4", pn.capacity);

    /* Four blocks over five workers: worker 4 gets none of them. */
    CHECK (bpa_dyn_init (&d, &pn, 3, 5, 0) == 0,
	   "worker 3, which the 4 blocks do reach, was refused");
    bpa_dyn_free (&d);
    CHECK (bpa_dyn_init (&d, &pn, 4, 5, 0) != 0,
	   "worker 4 was given a slice of a 4-block pool it holds no block "
	   "of, so every subscriber that hashes to it is refused at the "
	   "first packet with nothing said at configuration time");

    /* The same shape on a reserve, which comes up empty rather than refusing.
     * One reserve block per public address and one public address, so worker 1
     * of two owns nothing. */
    {
      bpa_pool_t pr2;

      e2 = mkpool (&pr2, "narrow-reserve", 1, 1039, 4, BPA_POLICY_INTERLEAVED);
      CHECK (e2 == 0, "pool 'narrow-reserve' rejected");
      pr2.mode = BPA_MODE_DETERMINISTIC;
      pr2.block_reserve = 1;
      CHECK (bpa_pool_init (&pr2) == 0, "the reserve was not carved out");
      CHECK (pr2.reserve_count == 1,
	     "the reserve holds %u blocks, and the worker count below is "
	     "chosen against 1", pr2.reserve_count);

      memset (&d, 0, sizeof (d));
      CHECK (bpa_dyn_init (&d, &pr2, 1, 2, 1) == 0,
	     "a reserve slice holding no block refused to come up, so a pool "
	     "that worked on one worker stops coming up on two");
      CHECK (d.active == 1 && d.n_local == 0,
	     "the empty reserve slice came up with %u blocks and active=%u",
	     d.n_local, d.active);
      CHECK (bpa_dyn_acquire_extra (&d, &pr2, 1, 1, 2, BPA_NO_BLOCK, 1, &ext,
				    &is_new) != 0,
	     "an empty reserve slice handed out a block it does not have");
      CHECK (d.n_exhausted == 1,
	     "the refusal was not counted: %llu exhaustions, so "
	     "'show bpa blocks' reports nothing where every extension on this "
	     "worker is refused",
	     (unsigned long long) d.n_exhausted);
      bpa_dyn_free (&d);
    }
    printf ("  the dynamic slice refused at configuration time, the reserve "
	    "slice empty and counting what it cannot do\n");
  }

  printf ("\n=== which block a subscriber holds, when it holds none ===\n");
  {
    /*
     * bpa_dyn_block_of() is what "show bpa subscriber" asks, because the
     * per-subscriber session record cannot answer it: that record dies with
     * the subscriber's last session while the block stays reserved here for
     * the whole hold-down. Both of its refusals were never executed.
     *
     * A wrong answer here is not a blank line in a command. *block_idx is left
     * alone on a refusal, so a caller that read the return value wrongly
     * prints whatever was on its stack as a block index - and every reader of
     * this function turns a block index into a public address and a port
     * range.
     */
    drv_t v;
    bpa_dyn_t *d, never;
    u32 blk = 0xdeadbeef;

    drv_init (&v, &p4, 1, 2);
    drv_arrive (&v, 0);
    d = &v.d[0];
    CHECK (bpa_dyn_block_of (d, 0, 0, 1, &blk) == 0,
	   "a subscriber that holds a block was reported as holding none");
    blk = 0xdeadbeef;
    CHECK (bpa_dyn_block_of (d, 55, 0, 1, &blk) != 0,
	   "a subscriber that has never sent a packet was reported as holding "
	   "block %u", blk);
    CHECK (blk == 0xdeadbeef,
	   "the refusal wrote %u into the caller's block index", blk);

    memset (&never, 0, sizeof (never));
    blk = 0xdeadbeef;
    CHECK (bpa_dyn_block_of (&never, 0, 0, 1, &blk) != 0,
	   "a slice that never came up reported a subscriber holding block %u",
	   blk);

    /*
     * And the other direction. bpa_dyn_owner() is what the RETURN path asks:
     * a packet arrived at a public port, the arithmetic turned it into a block
     * index, and this says whose block that is. A block index outside the
     * slice has to come back as "nobody" - the index is derived from an
     * address and a port a remote host chose, so it is an attacker's number
     * until this says otherwise, and owner[] is a vector of exactly n_local
     * entries.
     */
    {
      u32 sub = 0xdeadbeef;

      CHECK (bpa_dyn_owner (d, 0, 1, &sub) == 0 && sub == 0,
	     "the block subscriber 0 is holding is reported as held by %u",
	     sub);
      sub = 0xdeadbeef;
      CHECK (bpa_dyn_owner (d, d->n_local, 1, &sub) != 0,
	     "block %u, one past the %u this slice holds, was reported as "
	     "subscriber %u's", d->n_local, d->n_local, sub);
      CHECK (sub == 0xdeadbeef,
	     "the refusal wrote subscriber %u into the caller's index", sub);
      sub = 0xdeadbeef;
      CHECK (bpa_dyn_owner (&never, 0, 1, &sub) != 0,
	     "a slice that never came up reported block 0 as subscriber %u's",
	     sub);
    }
    printf ("  a subscriber with no block, a block outside the slice, and a "
	    "slice with no blocks, and none of them writes an index\n");
    drv_free (&v);
  }

  printf ("\n=== a subscriber's second forward moves its first block to the "
	  "extras ===\n");
  {
    /*
     * The other arm of bpa_dyn_unpin(), which had never been executed: the
     * block going back is an EXTENSION rather than the subscriber's primary,
     * so it has to come off the extras list instead of out of by_sub.
     *
     * An operator with two forwards for one subscriber on two blocks is what
     * produces it. The second pin makes its block the subscriber's primary -
     * bpa_dyn_acquire() answers with the primary, and the forwarded socket has
     * to be in it - and pushes the first one onto the extras. Unpinning the
     * first then finds by_sub naming the OTHER block.
     *
     * An entry left behind on that list is not cosmetic: it names a local
     * index that is handed to somebody else later, so the old subscriber going
     * idle would stamp THEIR block as idle and the reap would take a block
     * with live sessions on it.
     */
    drv_t v;
    bpa_dyn_t *d;
    u32 held_by = ~0u, first, second, owner_sub = ~0u;
    int is_new = 0, released = 0;

    drv_init (&v, &p4, 1, 2);
    d = &v.d[0];
    first = d->free_list[vec_len (d->free_list) - 1];
    CHECK (bpa_dyn_pin (d, first, 5, 1, 10.0, &held_by, &is_new) == 0,
	   "the first forward's block could not be pinned");
    second = d->free_list[vec_len (d->free_list) - 1];
    CHECK (second != first,
	   "both forwards would land on block %u, so there is no first block "
	   "to displace", first);
    CHECK (bpa_dyn_pin (d, second, 5, 1, 10.0, &held_by, &is_new) == 0,
	   "the second forward's block could not be pinned");

    {
      u32 primary = ~0u;

      CHECK (bpa_dyn_block_of (d, 5, 0, 1, &primary) == 0 && primary == second,
	     "the subscriber's primary is block %u after a forward was put on "
	     "block %u, so its own outbound traffic leaves from a block the "
	     "forward is not in", primary, second);
      CHECK (vec_len (d->extras) == 1 && d->extras[0].local == first
	       && d->extras[0].sub_idx == 5,
	     "%u blocks are on the extras list after the first one was "
	     "displaced", (u32) vec_len (d->extras));
    }

    bpa_dyn_unpin (d, first, 1, &released);
    CHECK (released == 1,
	   "the displaced block was the only thing the first forward held and "
	   "the caller was not told to emit a natEvent 17");
    CHECK (bpa_dyn_owner (d, first, 1, &owner_sub) != 0,
	   "the displaced block is still held by subscriber %u", owner_sub);
    CHECK (vec_len (d->extras) == 0,
	   "%u entries are left on the extras list naming a block that is back "
	   "in the pool, so the next subscriber to be given it has its block "
	   "stamped idle by somebody else", (u32) vec_len (d->extras));
    {
      u32 primary = ~0u;

      CHECK (bpa_dyn_block_of (d, 5, 0, 1, &primary) == 0 && primary == second,
	     "releasing the displaced block took the subscriber's primary "
	     "with it: it now holds block %u", primary);
    }
    CHECK (index_disagreements (d) == 0,
	   "the free stack and its index disagree after a displaced block went "
	   "back");
    printf ("  the first forward's block off the extras and back in the pool, "
	    "the second still the subscriber's own\n");
    drv_free (&v);
  }

  printf ("\n=== the reserve walk, where it has to give up ===\n");
  {
    /*
     * The deterministic reserve's preference is BOUNDED by the sharding, and
     * the three lines where it gives up had never been executed: the candidate
     * that belongs to another worker, the candidate that is already held, and
     * the walk that ends having found neither.
     *
     * A reserve block's index decides which worker may hand it out
     * (bpa_reserve_worker), and a subscriber may only draw from its own
     * worker's slice - both directions of a flow have to meet on one thread.
     * So the blocks on a subscriber's own public address are not all reachable
     * from it, and the ones that are may be somebody else's. That is why the
     * pairing is a PREFERENCE: refusing the extension rather than taking a
     * block elsewhere would turn a property about layout into a customer
     * losing traffic.
     *
     * Three workers over four public addresses on purpose. At one or two
     * workers every reserve index on an address is congruent to the same
     * worker as the subscriber and the give-up lines cannot be reached at all,
     * which is the shape the section above uses and the reason this one is
     * separate.
     */
    bpa_pool_t pr3;
    bpa_dyn_t d3[3];
    u32 s, w, pub, placed = 0, paired = 0, refused = 0;
    u64 box_placed = 0, box_missed = 0;
    u8 taken[32];

    memset (taken, 0, sizeof (taken));

    CHECK (mkpool (&pr3, "reserve-shard", 4, 1151, 4,
		   BPA_POLICY_INTERLEAVED) == 0,
	   "pool 'reserve-shard' rejected");
    pr3.mode = BPA_MODE_DETERMINISTIC;
    pr3.block_reserve = 8;
    CHECK (bpa_pool_init (&pr3) == 0,
	   "pool 'reserve-shard' rejected after the reserve was carved out");
    CHECK (pr3.reserve_count == 32 && pr3.capacity == 96,
	   "the reserve holds %u blocks over a capacity of %u, and the "
	   "arithmetic below is chosen against 32 and 96",
	   pr3.reserve_count, pr3.capacity);

    for (w = 0; w < 3; w++)
      CHECK (bpa_dyn_init (&d3[w], &pr3, w, 3, 1) == 0,
	     "reserve slice %u refused", w);

    /*
     * The subscribers of one public address arrive together, and that is what
     * makes this section say anything. Driven in index order the four
     * addresses take turns, so each one runs out of reserve blocks at about
     * the moment the whole slice does - and a worker whose free stack is empty
     * refuses before the walk is entered at all. Driven by address, an address
     * runs out while the slice still holds blocks on the OTHER three, which is
     * exactly the state the give-up path exists for. Neither order is more
     * real than the other: which subscribers wake up together is not something
     * a pool has any say in.
     */
    for (pub = 0; pub < 4; pub++)
      for (s = pub; s < pr3.capacity; s += 4)
	{
	  bpa_block_t ext;
	  u32 pub_idx, blk_idx;
	  int is_new;

	  w = s % 3;
	  bpa_split (&pr3, s, &pub_idx, &blk_idx);
	  if (bpa_dyn_acquire_extra (&d3[w], &pr3, s, w, 3, BPA_NO_BLOCK, 1,
				     &ext, &is_new))
	    {
	      refused++;
	      continue;
	    }
	  placed++;
	  taken[bpa_reserve_index (&pr3, ext.pub_idx, ext.blk_idx)]++;
	  if (ext.pub_idx == pub_idx)
	    paired++;
	}
    for (w = 0; w < 3; w++)
      {
	box_placed += d3[w].n_paired_placed;
	box_missed += d3[w].n_paired_missed;
      }

    CHECK (placed == pr3.reserve_count,
	   "%u of the reserve's %u blocks were handed out, so the walk stopped "
	   "before the reserve did", placed, pr3.reserve_count);
    CHECK (refused == pr3.capacity - pr3.reserve_count,
	   "%u extensions were refused where %u subscribers asked for one past "
	   "the %u blocks there are",
	   refused, pr3.capacity, pr3.reserve_count);
    CHECK (paired < placed,
	   "every one of the %u reserve extensions landed on its holder's own "
	   "public address, so the give-up path this section is about was "
	   "never taken", placed);
    CHECK (paired > 0,
	   "none of the %u reserve extensions paired, so the preference is "
	   "doing nothing at all rather than being bounded", placed);
    CHECK (box_placed == paired,
	   "the box counted %llu paired reserve extensions where %u landed on "
	   "their holder's own address",
	   (unsigned long long) box_placed, paired);
    CHECK (box_missed == placed - paired,
	   "the box counted %llu unpaired reserve extensions where %u were",
	   (unsigned long long) box_missed, placed - paired);

    /*
     * And no reserve block went to two subscribers. The walk chooses a block
     * by NAME rather than taking whatever is on top of the stack, so the test
     * that it is free is the only thing between a preference about layout and
     * one subscriber's inbound traffic arriving on another's mapping.
     */
    for (s = 0; s < 32; s++)
      CHECK (taken[s] <= 1,
	     "reserve block %u was handed to %u subscribers", s, taken[s]);
    for (w = 0; w < 3; w++)
      CHECK (index_disagreements (&d3[w]) == 0,
	     "reserve slice %u: the free stack and its index disagree after "
	     "the walk", w);
    printf ("  %u of %u reserve extensions paired at 3 workers over 4 "
	    "addresses, %u could not be, and none of them was refused for it\n",
	    paired, placed, placed - paired);
    for (w = 0; w < 3; w++)
      bpa_dyn_free (&d3[w]);
  }

  printf ("\n=== a stride the allocator cannot place is counted, not "
	  "refused ===\n");
  {
    /*
     * n_stride_missed had never been incremented by anything docker-free. The
     * section above drives a stride that lands; this one drives one that
     * cannot, because the two counters only mean anything as a pair: a stride
     * that is configured and never honoured looks from outside exactly like no
     * stride at all - the subscriber still gets a block and still translates -
     * and the operator would be reading a setting the box is not applying.
     *
     * The miss here is the one bpa_stride_block() refuses: the step runs off
     * the end of the subscriber's own public address, and the block after that
     * one belongs to a different address whose blocks this worker may not own.
     * What must NOT happen is a refusal: a preference about layout must not
     * cost the customer the block.
     */
    bpa_pool_t ps2;
    drv_t v;
    u32 pblk = 0, ppub = 0, pblk_i = 0, nth;

    CHECK (mkpool_stride (&ps2, "stride-off-end", 4, 1279, 4,
			  BPA_POLICY_INTERLEAVED, 2) == 0,
	   "the strided pool did not build");
    CHECK (ps2.blocks_mapped == 64,
	   "a public address holds %u mappable blocks, and the nth below is "
	   "chosen against 64", ps2.blocks_mapped);

    drv_init (&v, &ps2, 1, 1);
    drv_arrive (&v, 0);
    CHECK (v.held[0], "the subscriber took no primary");
    CHECK (bpa_dyn_block_of (&v.d[0], 0, 0, 1, &pblk) == 0,
	   "the subscriber holds no block to measure a stride from");
    bpa_split (&ps2, pblk, &ppub, &pblk_i);

    /* The first nth whose stride of 2 steps past the end of this address. */
    nth = (ps2.blocks_mapped - pblk_i + 1) / 2;
    CHECK (pblk_i + nth * 2 >= ps2.blocks_mapped,
	   "block %u plus %u strides of 2 is still inside the %u this address "
	   "holds, so the stride below can be placed and this section says "
	   "nothing", pblk_i, nth, ps2.blocks_mapped);

    drv_extend (&v, 0, nth);
    CHECK (v.seen_ext == 1,
	   "the extension was refused because its stride could not be placed, "
	   "which turns a preference about layout into a customer losing "
	   "traffic");
    CHECK (v.d[0].n_stride_missed == 1,
	   "the box counted %llu missed strides where one stride ran off the "
	   "end of the subscriber's address, so an operator reading "
	   "'show bpa blocks' sees a stride being honoured that is not",
	   (unsigned long long) v.d[0].n_stride_missed);
    CHECK (v.d[0].n_stride_placed == 0,
	   "the box counted %llu placed strides on an extension no stride "
	   "could place", (unsigned long long) v.d[0].n_stride_placed);
    printf ("  the stride ran off the end of the address, the subscriber got "
	    "a block anyway, and the box counted the miss\n");
    drv_free (&v);
  }

  (void) i;
  return bpa_test_verdict (checks, failures);
}
