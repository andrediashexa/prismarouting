/*
 * bpa_dyn.h - dynamic (PBA) block allocator.
 *
 * In deterministic mode a subscriber's block is a formula. In dynamic mode
 * the block is allocated on the first flow and released after the subscriber
 * goes idle, which lets a provider serve more subscribers than
 * blocks-times-addresses would allow. The cost is that every allocation and
 * release is an event that must be logged (RFC 8158 natEvent 16 and 17).
 *
 * Sharding: block allocation would be shared state across workers, which
 * would reintroduce the lock the rest of the design avoids. Instead the
 * block space is partitioned: worker w owns every block_idx where
 * block_idx % n_workers == w, so allocation is worker-local.
 *
 * That partition is what makes the handoff work unchanged in both modes:
 *
 *   in2out  worker = sub_idx   % n_workers
 *   out2in  worker = block_idx % n_workers
 *
 * A worker only ever hands a subscriber a block from its own slice, so the
 * two agree. And in deterministic mode block_idx == sub_idx, so the same
 * line of code serves both.
 */

#ifndef __included_bpa_dyn_h__
#define __included_bpa_dyn_h__

/* Standalone for the reason bpa_pool.h is, and for one more: the containers
 * ARE the behaviour here - which block comes off the free stack, and what a
 * removal displaces. test/bpa_vec_shim.h reproduces those two, and
 * test/test_bpa_dyn.c drives this allocator with no VPP and no container. */
#ifdef BPA_STANDALONE
#include "bpa_vec_shim.h"
#else
#include <vlib/vlib.h>
#endif

#include "bpa_pool.h"

/*
 * How long a block stays reserved after its last session ends.
 *
 * This is not tidiness: without hold-down, a subscriber that goes quiet for
 * a second and comes back churns a release and an allocation, and every one
 * of those is a log record. Hold-down is the main lever on log volume.
 */
#define BPA_BLOCK_HOLDDOWN 120

#define BPA_NO_BLOCK ((u32) ~0)

/*
 * owner[] for a block this worker has LENT to another (#210).
 *
 * A third state, because "free" and "held by subscriber N" are both wrong
 * for it and both wrong in a way that loses the block: free, and this slice
 * hands it to a subscriber the other worker is already translating; held,
 * and the reap releases it and the release record names the sentinel as a
 * subscriber. It is not in the free list and it is not in by_sub, so every
 * search that looks in either passes over it without being told to.
 *
 * ~1 rather than a flag array beside owner[]: a lent block cannot also be
 * owned, which is the difference from `pinned[]`, and a whole byte per block
 * per worker for a state that is empty on every box that never exhausts a
 * slice is a cost paid by everybody for nothing.
 */
#define BPA_LENT ((u32) ~1)

/*
 * Blocks of the subscriber's own public address an extension may look at,
 * per walk, before it takes one from the free stack instead.
 *
 * A bound and not a search of the address, because a public address holds
 * blocks_mapped blocks - 16128 with a /16 port range and 4-port blocks - and
 * an extension that cannot be paired would cross all of them to find that out,
 * on the packet that already found no free port anywhere.
 *
 * Thirty-two, measured rather than picked. Over three arrival patterns - one
 * subscriber at a time, everybody-arrives-then-everybody-extends, and a steady
 * state with three quarters of the subscribers reaped - across 1 to 256 public
 * addresses and 1 to 8 workers, the two walks in bpa_dyn_acquire_extra() pair
 * 100%, 99.6% and 99.2% of extensions at their worst cell. Sixteen gives up
 * the third of those to 76.5%, on the worker counts that share no factor with
 * the address count and therefore step along an address at their sparsest;
 * sixty-four buys 100% back, at twice the walk.
 *
 * The worst case is therefore 2 * BPA_PAIR_WALK reads of owner[], once per
 * block extension - not per packet, and not per session.
 */
#define BPA_PAIR_WALK 32

/*
 * WHAT THE WORKERS OF ONE INTERLEAVED DYNAMIC POOL SHARE (#603): how many
 * subscribers each public address has, pool-wide, and which addresses each
 * worker has not yet heard went DOWN.
 *
 * The rule it serves: under `policy interleaved` an arriving subscriber takes
 * a free block of its worker's slice on the public address with the FEWEST
 * subscribers in the whole pool, the lowest address on a tie. Before it, the
 * slice's free blocks were a stack and an arrival took the block the last
 * leaver gave back - measured on the three-VM bench (#475) at a max/min of
 * 1.67, 1.14, 2.2 and 2.0 subscribers per address after six rounds of churn,
 * where 1.14 was possible. ref/bpa_model.py (DynamicSlices) is the rule.
 *
 * POOL-WIDE, NOT PER WORKER, and that was measured rather than assumed: three
 * workers each keeping their OWN share even still stack their surpluses on
 * the same addresses - 1.44 after six rounds on the bench's shape in the
 * model, against 1.14. And EXACT, not a snapshot: a per-worker copy of the
 * pool's loads refreshed once a tick gave 1.16 on average and 1.5 at worst,
 * because a burst of arrivals on three workers all reads the same stale
 * minimum and all three put their subscriber there.
 *
 * `load` is a row per address with a cell per worker, and a slice writes
 * only its own cell: the pool-wide count is the sum of the row, and no cell
 * has two writers, so an allocation takes no lock. `stale` is how a worker
 * learns that another slice LOWERED an address - which happens only in the
 * reap, a pin and an unpin, all on the main thread under the barrier - one
 * bit an address, set for every other worker and read back before it next
 * chooses. A RAISED load needs no message; bpa_dyn_take() says why. Bits and
 * not a queue, so a burst of releases on one address is one bit and the
 * memory is fixed: A/8 bytes a worker, plus A/512 for the summary that keeps
 * the drain from walking every word.
 */
typedef struct bpa_dyn_share
{
  u32 *load;	   /* [address * n_workers + worker] -> subscribers there */
  u64 **stale;	   /* worker -> bitmap of addresses another slice lowered */
  u64 **stale_sum; /* worker -> bitmap of the words of stale[w] with a bit */
  u32 n_addr;
  u32 n_workers;
} bpa_dyn_share_t;

/* A subscriber's extension block: which local block, and whose it is. */
typedef struct
{
  u32 sub_idx;
  u32 local;
} bpa_dyn_extra_t;

typedef struct
{
  u32 *owner;	    /* local idx -> sub_idx, BPA_NO_BLOCK when free */
  f64 *idle_since;  /* local idx -> when it went idle; 0 while in use */
  /*
   * local idx -> the configuration says this block is one subscriber's.
   *
   * Parallel to owner[] rather than a flag inside it because the two answer
   * different questions: owner[] says who is holding the block now, pinned[]
   * says why it may not be taken away. A block can be pinned and idle at the
   * same time - that is the whole point of the pin - and a sentinel owner
   * would have had to encode both in one word.
   */
  u8 *pinned;
  u32 *free_list;   /* stack of free local idx */
  /*
   * local idx -> where it sits in free_list, BPA_NO_BLOCK when it is held.
   *
   * The inverse of free_list, maintained by the three operations that move
   * it, so taking a NAMED block out of the stack is an index rather than a
   * search. Every path that places a block somewhere chosen - a pin, a
   * stride, the paired-address preference - needs exactly that, and the
   * search it replaces is over the whole slice: measured at 72684 entries
   * examined for ONE extension on a 16-address pool at 3 workers, and at
   * about 0.8 ms per pin on a 258048-block slice, all of it the walk (#226).
   *
   * Four bytes per block against owner[]'s four, idle_since[]'s eight and
   * pinned[]'s one - a 258048-block slice grows from about 4.4 MB to 5.4 MB,
   * once per worker per pool. That is the trade, and it is paid so that
   * paired address pooling can be the default RFC 6888 REQ-2 requires
   * without a walk on the packet that asks for the extension.
   */
  u32 *free_pos;
  uword *by_sub;    /* sub_idx -> local idx + 1, so 0 means "none" */
  u32 n_local;	    /* blocks in this worker's slice */
  u32 n_alloc;	    /* currently held */
  u32 n_pinned;	    /* of those, held because a forward says so */
  /*
   * Blocks of this slice currently held by ANOTHER worker (#210).
   *
   * Kept so that free = n_local - n_alloc - n_lent stays exact: without it a
   * lent block reads as free to everything that subtracts, and `show bpa
   * pool` would report a capacity the box has already given away.
   */
  u32 n_lent;
  /*
   * Blocks BORROWED from another worker's slice, by pool-wide index (#210).
   *
   * A slot here is a local index of n_local + k, so owner[], idle_since[],
   * pinned[] and free_pos[] cover it exactly like a block of this worker's
   * own - which is what leaves bpa_dyn_acquire() untouched: a borrowed block
   * is pushed onto the same free list and popped by the same line.
   *
   * IT CANNOT BE HELD AS AN ORDINARY LOCAL BLOCK, and that is not a choice.
   * A slice's index space is arithmetic - block_idx = local * n_workers +
   * worker - so every index in it is congruent to that worker's id, and a
   * borrowed block is congruent to the LENDER's. The pool-wide index is the
   * only name the two workers can both use, so it is kept here and
   * bpa_dyn_block_of() is what every site that used to multiply now asks.
   *
   * BPA_NO_BLOCK in a slot means VACANT: the block went home and the slot is
   * waiting for the next borrow. Reusing it rather than deleting it keeps
   * every other slot's index still, which by_sub and extras both hold, and
   * bounds the array at the most this worker ever borrowed at one time.
   */
  u32 *foreign;
  /*
   * Parallel to foreign[]: has this borrowed block been free since the last
   * tick? One byte per slot, and the slots are bounded by the most this
   * worker ever borrowed at one time (#210).
   */
  u8 *foreign_idle;

/*
 * HOW MANY TICKS A LOAN NOBODY HAS USED IS KEPT, and the number is about
 * what a refused subscriber does next rather than about this allocator.
 *
 * The block is lent because somebody was refused, and that somebody will try
 * again: a DNS client retries in about a second, a TCP SYN is retransmitted
 * at 1 s and then at 3 s. A loan returned on the next tick is a block that
 * was available for one second and is gone before the second attempt -
 * measured, in `LAB_WORKERS=1 sh tools/lab.sh dynamic`, where the box lent a
 * block at 09:48:51 and took it home at 09:48:52 while the retry was still
 * coming.
 *
 * Five ticks covers the second and third attempts. It is also the whole
 * window in which the lender is without a block it owns, which is the cost:
 * the measurement that set this number is in the lab's header.
 */
#define BPA_LOAN_GRACE_TICKS 5
  u64 n_allocations;
  u64 n_extensions;   /* blocks handed out as extensions *//* lifetime; becomes natEvent 16 count */
  /*
   * Extensions the pool's stride placed, and extensions it could not.
   *
   * Two counters because a stride that is configured and never honoured looks
   * from outside exactly like no stride at all - the subscriber still gets a
   * block and still translates - and the operator would be reading a setting
   * the box is not applying. A miss is not a failure: the block the stride
   * named was taken by somebody else, or the subscriber sits at the end of
   * its public address, and falling back to the free list is what keeps the
   * customer working. It is only a failure when it is every one of them.
   */
  u64 n_stride_placed;
  u64 n_stride_missed;
  /*
   * Extensions that landed on the subscriber's own public address, and
   * extensions that could not.
   *
   * The RFC 4787 REQ-2 conformance number for this box, live. A subscriber's
   * FIRST block is on its own address by arithmetic in both modes; every
   * block after it is a preference the allocator applies and can fail to
   * honour, so the property is whatever these two say it is. An operator
   * quoting paired address pooling in a tender reads the fraction their own
   * pool produced rather than the claim.
   *
   * Both allocators increment them, because both prefer and both fall back:
   * a dynamic pool walks its own address, a deterministic one walks its
   * reserve. One property, one pair of counters.
   *
   * Counted here and not derived from n_extensions, because the third case is
   * real and is neither: an extension with no primary to measure from, which
   * happens when the primary was reaped while an extension outlived it. That
   * one is not a miss - there was no address to pair with.
   */
  u64 n_paired_placed;
  u64 n_paired_missed;
  u64 n_releases;   /* lifetime; becomes natEvent 17 count */
  u64 n_exhausted;  /* allocation refused; becomes natEvent 12 */
  /*
   * What n_exhausted was at the last rebalance tick (#210).
   *
   * The demand the rebalance acts on is the DELTA: a worker short an hour
   * ago and served since would otherwise pull blocks towards itself for
   * ever, with the pull growing as the box ages. Kept beside the counter it
   * is read against rather than in a table of its own, because a second
   * structure indexed by (thread, pool) is a second thing to keep in step
   * with pools being created and deleted.
   */
  u64 n_exhausted_seen;
  /*
   * What this slice had free at the last rebalance tick (#210).
   *
   * THE LOAN NEEDS HYSTERESIS, and this is the measurement that says so. The
   * first tick lent on the same pass as the first refusal, using a free
   * count that included blocks the lender's own subscribers were about to
   * ask for - and `LAB_WORKERS=1 sh tools/lab.sh dynamic` went from
   * "3 allocations, 3 releases, 1 refusals" to "2 allocations, 2 releases,
   * 2 refusals". The mechanism built to recover a refused subscriber cost
   * one.
   *
   * So what a slice may lend is what was free at the PREVIOUS tick and is
   * still free now: a block that has sat unused across a full interval,
   * rather than one that is free for the instant between two packets.
   */
  u32 n_free_seen;
  /*
   * THE HIGH-WATER MARK OF THIS SLICE'S OWN USE, decaying by one a tick.
   *
   * What a slice may lend is what it holds above what it has recently
   * needed, and "recently" has to be measured rather than assumed: a slice
   * whose blocks are free this instant may be one whose subscribers are
   * between packets. Both ends of that were measured (#210):
   *
   *   lending on free-right-now  - the dynamic lab went from 3 allocations
   *                                and 1 refusal to 2 and 2: the mechanism
   *                                built to recover a refused subscriber
   *                                cost a live one
   *   lending above the peak     - the slice that had served two subscribers
   *                                lends nothing, and the slice that has
   *                                served none lends its block
   *
   * It decays so that a slice which was busy an hour ago is not held to it
   * for ever: one block a tick, so a minute of quiet forgives sixty.
   */
  u32 n_peak;
  u8 active;
  /*
   * Extension blocks: (subscriber, local block) pairs beyond the one in
   * by_sub. Kept as a list rather than widening by_sub because extensions are
   * rare - only subscribers that filled a block have one - and because
   * releasing them has to be driven from the same reap that releases primary
   * blocks, which scans owner[] anyway.
   *
   * Without this, an extension would be owned but never marked idle, and
   * never released: a leak that only shows up as a pool that slowly stops
   * having free blocks.
   */
  bpa_dyn_extra_t *extras;
  /*
   * This slice indexes the pool's RESERVE, not the blocks the formula hands
   * out.
   *
   * A deterministic pool has no free list - every block is somebody's by
   * arithmetic - so the only blocks it can give away are the ones its reserve
   * holds out of the formula. Those live in an index space of their own
   * (bpa_reserve_index), and the ONLY difference between allocating from it
   * and allocating a dynamic block is how an index becomes an address and a
   * port range. Everything else this allocator does - the free stack, the
   * hold-down, the extras list, the reap, the counters that become natEvent
   * 16 and 17 - is the same work for the same reason, so it is the same code
   * with a flag rather than a second copy that has to be kept in step.
   */
  u8 reserve;

  /*
   * THE LEAST-LOADED CHOICE (#603), present only when `share` is: a dynamic
   * pool under `policy interleaved`. Every other slice leaves all of these
   * empty and allocates off the top of free_list as it always has.
   *
   * free_list stays the slice's set of free blocks - its length is the free
   * count everything else reads, lending takes from its top, and the paired
   * walk jumps from it - and these are an INDEX over it by public address:
   *
   *   addr_next/prev   per slot, the doubly linked free list of the slot's
   *                    own public address, so a named block (a pin, a pair,
   *                    a stride, a loan) leaves it in constant time
   *   addr_head/tail   per address, oldest-freed first: a block goes back at
   *                    the tail, so the one handed out is the one that has
   *                    been free longest rather than the one somebody left a
   *                    moment ago
   *   addr_free        per address, how many free blocks this slice has there
   *   tree             a tournament over the addresses whose entries are
   *                    (pool-wide load as this slice last read it, address)
   *                    in one u64, so the smaller entry is the rule; no free
   *                    block here reads as load ~0. tree[1] names the address
   *                    to allocate from; a change to one address replays at
   *                    most log2(n_leaves) matches
   *
   * Twelve bytes a public address for the three per-address arrays, sixteen
   * for the tree (two u64 per leaf, rounded up to a power of two), and eight
   * a slot for the links: on the bench's /29 with 40 blocks a slice, 544
   * bytes; on a /16 with 4096-port blocks at three workers, 1.8 MB plus
   * 2.6 MB of links, per worker per pool. The measured cost on the
   * allocation and release paths is in docs/architecture.md ("How it is
   * kept, and what it costs").
   */
  bpa_dyn_share_t *share;
  u32 worker, n_workers, n_addr, n_leaves;
  u32 *addr_next, *addr_prev;
  u32 *addr_head, *addr_tail, *addr_free;
  u64 *tree;
} bpa_dyn_t;

/*
 * Sets p->dyn_share for a dynamic pool under `policy interleaved` and leaves
 * every other pool alone. Called once, before the first bpa_dyn_init() of the
 * pool: a slice reads the pointer when it is made, and a slice made before it
 * allocates off the stack for its whole life.
 *
 * bpa_dyn_share_free() after the last slice is freed, because a slice being
 * freed is still a slice whose releases tell the others.
 */
void bpa_dyn_share_init (bpa_pool_t *p, u32 n_workers);
void bpa_dyn_share_free (bpa_pool_t *p);

/*
 * The free block an arrival takes: the least-loaded address's oldest when the
 * slice has a share, the top of the stack when it does not. The caller has
 * checked that the slice is not empty.
 */
u32 bpa_dyn_take (bpa_dyn_t *d);

/*
 * The slot `local` gained (+1) or lost (-1) a subscriber's PRIMARY block:
 * moves this worker's cell of its public address's load, and on a loss tells
 * the other workers. A no-op on a slice with no share.
 */
void bpa_dyn_count (bpa_dyn_t *d, u32 local, int delta);

/* Defined so test/bench_bpa_dyn.c can be built against this allocator and
 * against the stack it replaced, and time both with one file. */
#define BPA_DYN_TAKE 1

/*
 * `reserve` chooses which index space this slice covers: the pool's reserve
 * (deterministic mode) or its whole block range (dynamic mode).
 */
clib_error_t *bpa_dyn_init (bpa_dyn_t *d, const bpa_pool_t *p, u32 worker,
			    u32 n_workers, int reserve);
void bpa_dyn_free (bpa_dyn_t *d);

/*
 * LENDS up to `want` free blocks of this slice to another worker (#210).
 *
 * Writes the POOL-WIDE block index of each into `out` - not the local one -
 * because the borrower's index space is its own: local * n_workers + worker
 * is congruent to the owner's worker id, and a borrowed block is congruent
 * to the lender's. The only name the two workers can both use is the global
 * one.
 *
 * Returns how many it lent, which is fewer than `want` when the slice runs
 * out of free blocks. It lends no block that is held, and none that is
 * pinned - a pinned block is not in the free list, which is where these come
 * from, so that follows rather than being checked twice.
 *
 * CALLED UNDER THE BARRIER, like everything else in the rebalance: this
 * writes owner[] and the free list of a slice whose worker is stopped.
 */
u32 bpa_dyn_lend (bpa_dyn_t *d, u32 worker, u32 n_workers, u32 want,
		  u32 *out);

/*
 * TAKES BACK a block this slice lent, by its pool-wide index.
 *
 * Returns 0 when the block came home, -1 when this slice never lent it -
 * which is a bug in the caller rather than a state to recover from, and is
 * an answer rather than an assert so the barrier is not a place a wrong
 * index stops the box.
 */
int bpa_dyn_take_back (bpa_dyn_t *d, u32 block_idx, u32 n_workers);

/* A block returned to the pool, so the caller can log natEvent 17. */
typedef struct
{
  u32 sub_idx;
  u32 block_idx;
} bpa_dyn_release_t;

/*
 * Block currently held by this subscriber, allocating one if needed.
 * Returns 0 on success, -1 when the slice is exhausted.
 * *is_new is set when a block was actually allocated, which is the moment
 * that has to be logged as RFC 8158 natEvent 16.
 */
int bpa_dyn_acquire (bpa_dyn_t *d, const bpa_pool_t *p, u32 sub_idx,
		     u32 worker, u32 n_workers, bpa_block_t *b, int *is_new);

/*
 * Another block for a subscriber that already has one. Separate from
 * bpa_dyn_acquire because that one returns the existing block when the
 * subscriber is known, which is exactly the wrong answer here.
 *
 * `primary` is the block the subscriber started with and `nth` is how many it
 * already holds, which together are what the pool's stride is measured from:
 * the nth block sits nth*stride blocks along the primary's own public
 * address. Both are ignored when no stride is configured, which is the
 * default and today's behaviour - whatever the free list hands out next.
 *
 * A stride the allocator cannot honour - the block is taken, or it is not in
 * this worker's slice - falls back to the free list and is counted. Refusing
 * the extension instead would turn a preference about layout into a customer
 * losing traffic.
 */
int bpa_dyn_acquire_extra (bpa_dyn_t *d, const bpa_pool_t *p, u32 sub_idx,
			   u32 worker, u32 n_workers, u32 primary, u32 nth,
			   bpa_block_t *b, int *is_new);

/*
 * Holds one block for one subscriber until the configuration says otherwise.
 *
 * This is what makes a static port forward possible on a dynamic pool: the
 * unit an operator sells is an inbound port, and the unit this allocator moves
 * is a block, so the block is what has to stop moving. A pinned block is never
 * handed to anybody else and never reaped, whatever the hold-down says.
 *
 * Returns 0 when the block is this subscriber's afterwards, -1 when another
 * subscriber is holding it right now - its index goes in *held_by, because
 * "refused" is only useful to an operator who is told whose sessions the pin
 * would have broken - and -2 when the block is not in this worker's slice,
 * which is a caller that resolved the wrong worker rather than anything an
 * operator can act on.
 *
 * *is_new is set only when the block came off the free list, which is the
 * moment that is an RFC 8158 natEvent 16. Pinning a block the subscriber is
 * already holding allocates nothing, and a second allocation record with no
 * release between them is exactly the gap docs/logging.md exists to close.
 */
int bpa_dyn_pin (bpa_dyn_t *d, u32 block_idx, u32 sub_idx, u32 n_workers,
		 f64 now, u32 *held_by, int *is_new);

/*
 * The mirror: the last forward in the block is gone, so the block is an
 * ordinary dynamic block again.
 *
 * *released is set when the block went straight back to the free list, which
 * is a natEvent 17 the caller has to emit. That happens when nothing is using
 * it - the pin was the only reason it was held - and does not when the
 * subscriber still has sessions on it, which then leave by the hold-down like
 * any other block.
 */
void bpa_dyn_unpin (bpa_dyn_t *d, u32 block_idx, u32 n_workers, int *released);

/*
 * How many slots this slice has: its own blocks, then the borrowed ones.
 *
 * Every walk over owner[] wants this rather than n_local, because a borrowed
 * block is held by a subscriber exactly like an owned one - it is reaped, it
 * is announced, and it is released. n_local is still the right bound for the
 * candidate searches, which are arithmetic over THIS worker's congruent
 * indices and have nothing to say about a foreign block.
 */
static_always_inline u32
bpa_dyn_slots (const bpa_dyn_t *d)
{
  return d->n_local + vec_len (d->foreign);
}

/*
 * The pool-wide index of a slot, whether it is this worker's or borrowed.
 *
 * Replaces `local * n_workers + worker`, which is right for every index
 * below n_local and wrong for every one above it.
 */
static_always_inline u32
bpa_dyn_block_at (const bpa_dyn_t *d, u32 local, u32 worker, u32 n_workers)
{
  if (PREDICT_TRUE (local < d->n_local))
    return local * n_workers + worker;
  if (local - d->n_local >= vec_len (d->foreign))
    return BPA_NO_BLOCK;
  return d->foreign[local - d->n_local];
}

/*
 * BORROWS a block another worker lent, by its pool-wide index.
 *
 * It arrives free: pushed onto this slice's free list, so the next
 * subscriber that asks gets it through bpa_dyn_acquire() with no branch
 * added to that path. Returns 0, or -1 when the slice cannot take it.
 *
 * Called under the barrier, like everything else in the rebalance.
 */
int bpa_dyn_borrow (bpa_dyn_t *d, u32 block_idx);

/*
 * GIVES BACK a borrowed block, which must be free: a held one is a
 * subscriber's, and handing it to the lender would let the lender give it to
 * somebody else while this worker is still translating out of it. Returns 0,
 * or -1 when this slice does not hold it free.
 */
int bpa_dyn_give_back (bpa_dyn_t *d, u32 block_idx);

/* Is this block held by configuration rather than by traffic? */
static_always_inline int
bpa_dyn_is_pinned (const bpa_dyn_t *d, u32 block_idx, u32 n_workers)
{
  u32 local = block_idx / n_workers;

  if (PREDICT_FALSE (!d->active || local >= d->n_local))
    return 0;
  return d->pinned[local] != 0;
}

/* Subscriber's last session ended: start the hold-down. */
void bpa_dyn_idle (bpa_dyn_t *d, u32 sub_idx, f64 now);

/*
 * There was a bpa_dyn_active() here - declared, defined, documented
 * "subscriber became active again before hold-down expired", and called by
 * nothing. #216 asked whether that was a missing call or a dead function. It
 * is neither: it would have been WRONG to call, and it is gone.
 *
 * What it did was clear the idle stamp on every block a subscriber holds,
 * extensions included. bpa_dyn_idle() sets those stamps at one moment only -
 * when the subscriber's session count reaches ZERO - and at that moment every
 * block it holds is empty, extension and primary alike. A subscriber that
 * comes back with one session needs one block, so its extension SHOULD go
 * back to the pool, and it does, because bpa_dyn_acquire() clears the stamp on
 * the primary alone.
 *
 * Wiring bpa_dyn_active() in would have kept the empty extension owned - the
 * leak the comment on bpa_dyn_idle() exists to warn about, arriving through
 * the function that looked like its counterpart.
 *
 * Measured rather than argued: lab/extblocks.sh takes a subscriber to two
 * blocks, lets it go quiet, brings it back with ONE flow and waits out the
 * hold-down. It ends holding one block, its own, still translating. With
 * bpa_dyn_active() called from bpa_dyn_acquire() it ends holding two.
 */

/*
 * Release blocks idle for longer than holddown. Fills `out` with what was
 * released so the caller can emit natEvent 17, and returns how many.
 *
 * A pinned block is never among them, however long it has been idle. That is
 * the half of the pin that the operator actually sold: the forwarded port has
 * to still be the subscriber's after a night with no traffic, and the
 * hold-down is measured in minutes.
 */
u32 bpa_dyn_reap (bpa_dyn_t *d, f64 now, u32 holddown, u32 worker,
		  u32 n_workers, bpa_dyn_release_t *out, u32 max_out);

/* block_idx -> (public address, port range). */
static_always_inline void
bpa_dyn_block_addr (const bpa_pool_t *p, u32 block_idx, bpa_block_t *b)
{
  bpa_split (p, block_idx, &b->pub_idx, &b->blk_idx);
  bpa_set_addr_of (&p->outside, b->pub_idx, &b->ip_pub);
  b->port_lo = p->port_min + b->blk_idx * p->block_size;
  b->port_hi = b->port_lo + p->block_size - 1;
}

/*
 * The same, for whichever index space this slice covers. Returns 0 on
 * success.
 *
 * The one place the two allocators differ. Everything else about a held block
 * - who has it, when it went idle, when it goes back - is the same question
 * whether the block came off a free list or out of a reserve, and the record
 * they produce is the same record.
 */
static_always_inline int
bpa_dyn_addr (const bpa_dyn_t *d, const bpa_pool_t *p, u32 idx,
	      bpa_block_t *b)
{
  if (PREDICT_FALSE (d->reserve != 0))
    return bpa_reserve_addr (p, idx, b);
  bpa_dyn_block_addr (p, idx, b);
  return 0;
}

/* Which subscriber currently holds this block. -1 when it is free. */
static_always_inline int
bpa_dyn_owner (const bpa_dyn_t *d, u32 block_idx, u32 n_workers, u32 *sub_idx)
{
  u32 local = block_idx / n_workers;

  if (PREDICT_FALSE (!d->active || local >= d->n_local))
    return -1;
  if (PREDICT_FALSE (d->owner[local] == BPA_NO_BLOCK))
    return -1;

  *sub_idx = d->owner[local];
  return 0;
}

/*
 * Which subscriber holds pool-wide block `block_idx` IN THIS SLICE - the
 * slice of `worker` of `n_workers`. -1 when this slice does not hold it, or
 * holds it free.
 *
 * bpa_dyn_owner() cannot answer that for a caller that asks EVERY worker,
 * which is what `show bpa translation` does (#432). It divides the block
 * index by the worker count and reads that slot, and for block 1 the slot on
 * worker 0 is worker 0's block 0 - somebody else's block. MEASURED with two
 * workers: 100.64.0.12 held 1024-17407 only, the pool counted 2/3 blocks in
 * use, and the reverse lookup of port 17408 answered "-> 100.64.0.12". That
 * is the screen an operator answers an abuse report from.
 *
 * So a block is looked for where this slice can hold it: among its own
 * blocks only if it is congruent to this worker, and among the borrowed
 * slots by its pool-wide index (#210). The datapath keeps bpa_dyn_owner():
 * the handoff puts a packet on the worker its block is congruent to, which
 * is the one case the division is right.
 */
static_always_inline int
bpa_dyn_holder (const bpa_dyn_t *d, u32 block_idx, u32 worker,
		u32 n_workers, u32 *sub_idx)
{
  u32 slot, i;

  if (PREDICT_FALSE (!d->active || n_workers == 0))
    return -1;
  if (block_idx % n_workers == worker && block_idx / n_workers < d->n_local)
    slot = block_idx / n_workers;
  else
    {
      for (i = 0; i < vec_len (d->foreign); i++)
	if (d->foreign[i] == block_idx)
	  break;
      if (i == vec_len (d->foreign))
	return -1;
      slot = d->n_local + i;
    }
  /* BPA_LENT is the lender's mark for a block another worker is holding:
     not a subscriber, and not this slice's to answer for (#210). Returned
     as one, it reached `show bpa translation` as subscriber 4294967294, and
     only a failed address lookup behind it kept that off the screen. */
  if (d->owner[slot] == BPA_NO_BLOCK || d->owner[slot] == BPA_LENT)
    return -1;
  *sub_idx = d->owner[slot];
  return 0;
}

/*
 * The mirror: which block this subscriber holds. -1 when it holds none.
 *
 * Asked by "show bpa subscriber", because the per-subscriber session record
 * cannot answer it: that record dies with the subscriber's last session while
 * the block stays reserved here for the whole hold-down. Only the primary
 * block, which is the one whose absence means the subscriber holds nothing.
 */
static_always_inline int
bpa_dyn_block_of (bpa_dyn_t *d, u32 sub_idx, u32 worker, u32 n_workers,
		  u32 *block_idx)
{
  uword *q;

  if (PREDICT_FALSE (!d->active))
    return -1;
  q = hash_get (d->by_sub, sub_idx);
  if (!q)
    return -1;

  /*
   * Through bpa_dyn_block_at(), because the block this subscriber holds may
   * be one this worker BORROWED, and that one is not congruent to this
   * worker's id (#210). Multiplied here, `show bpa subscriber` would print
   * the address of whatever block of this worker's own happens to sit at
   * that index - a public address the subscriber is not translating to.
   */
  *block_idx = bpa_dyn_block_at (d, (u32) (q[0] - 1), worker, n_workers);
  return *block_idx == BPA_NO_BLOCK ? -1 : 0;
}


/*
 * The block the allocator is holding for this subscriber right now, as
 * `show bpa subscriber` prints it: 1 and the block, or 0 when it holds none.
 * Only a dynamic pool holds one; in deterministic mode the block is the
 * formula's, printed whether or not the subscriber has ever sent a packet.
 */
static_always_inline int
bpa_dyn_held (bpa_dyn_t *d, const bpa_pool_t *p, u32 sub_idx, u32 worker,
	      u32 n_workers, bpa_block_t *blk)
{
  u32 block_idx;

  if (p->mode != BPA_MODE_DYNAMIC
      || bpa_dyn_block_of (d, sub_idx, worker, n_workers, &block_idx))
    return 0;
  bpa_dyn_block_addr (p, block_idx, blk);
  blk->sub_idx = sub_idx;
  return 1;
}

#endif /* __included_bpa_dyn_h__ */
