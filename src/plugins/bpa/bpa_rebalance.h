/*
 * bpa_rebalance.h - which blocks move between workers' slices, and when.
 *
 * THE DEFECT THIS IS THE ARITHMETIC OF (#210). A dynamic pool's blocks are
 * partitioned across workers so that a block has exactly one owner and needs
 * no lock. A subscriber lands on a worker by HASH. Nothing makes the two
 * agree, so a subscriber can be refused while blocks sit free on another
 * worker - measured on a three-block pool with two workers, where a
 * subscriber was translated to 0.0.0.0:0 with a block standing free next
 * door, and `show bpa pool` said "capacity 3 subscribers (usable 3)".
 *
 * The maintainer chose option 2: the partition follows the hash. The block
 * moves; the subscriber does not, because `bpa_handoff.c` needs the owner to
 * be computable from the packet alone, by arithmetic, with no shared table.
 *
 * WHERE THE MOVE HAPPENS, and it is not here. `bpa_expire_process` already
 * takes `vlib_worker_thread_barrier_sync()` once a second, and under that
 * barrier every worker's slice is quiescent by construction: no new lock, no
 * atomic on the packet path, and `bpa_dyn_acquire` untouched - which is what
 * keeps the fast path at the clocks `lab/throughput.sh` measures. What it
 * costs is latency: a subscriber whose slice is empty is refused until the
 * next tick and served after it. Against being refused FOR EVER while blocks
 * sit free, that is the trade #210 is about.
 *
 * THIS FILE IS ONLY THE DECISION: counters in, moves out. No pool, no
 * packet, no VPP, so every way of getting it wrong is reachable from a plain
 * `cc` harness - the same reason bpa_block_ident.c is its own file (#397).
 */
#ifndef __included_bpa_rebalance_h__
#define __included_bpa_rebalance_h__

/*
 * What one worker's slice looks like to the rebalance.
 *
 * `demand` is the SUBSCRIBERS this slice refused in the last few ticks that
 * still hold nothing, less the borrowed blocks it already holds free for
 * them - bpa_demand.h. Not the refused packets since a baseline (#611): a
 * SYN and its retransmits were three, and a refusal no loan answered
 * counted for ever.
 */
typedef struct
{
  u32 free;   /**< blocks free in this worker's slice right now */
  u32 demand; /**< subscribers this worker wants a block for */
} bpa_rebalance_slice_t;

/** One block's worth of movement: `n` blocks from `from` to `to`. */
typedef struct
{
  u32 from;
  u32 to;
  u32 n;
} bpa_rebalance_move_t;

/*
 * THE BARRIER IS A FORWARDING PAUSE, so the work under it is bounded.
 *
 * Every move is a write into two workers' slices while the world is stopped;
 * an unbounded loop there is a pause an operator measures as jitter. The cap
 * is per tick rather than per pool, because a box with two hundred pools
 * would otherwise hold the barrier two hundred times longer than a box with
 * one - and the tick comes round again in a second.
 */
#define BPA_REBALANCE_MAX_MOVES 8

/*
 * The most workers this plan is written for, and the array it sizes lives on
 * the stack under the barrier. VPP's own worker count is bounded by the
 * cores the tuner isolates, and the largest box this product has been sized
 * for has 32 of them; a machine with more gets its first slices rebalanced
 * and the rest left alone, which is worse than nothing for nobody and better
 * than a heap allocation under a barrier that has stopped the world.
 */
#define BPA_REBALANCE_MAX_SLICES 64

/**
 * Decides which blocks move, writing at most `max_moves` entries into `out`.
 *
 * Returns how many entries were written. A box where nothing is short gets
 * zero, which is every box that has never exhausted a slice.
 */
u32 bpa_rebalance_plan (const bpa_rebalance_slice_t *slices, u32 n_slices,
			bpa_rebalance_move_t *out, u32 max_moves);

/**
 * What a slice may lend: what it holds above what it has recently needed.
 *
 * Here rather than beside the tick because it is arithmetic over four
 * numbers and every way of getting it wrong is a block lent that should not
 * have been - which cost a live allocation when this was first measured
 * (#210). `peak` is the high-water mark of recent use, `lent` what is
 * already out on loan.
 */
u32 bpa_rebalance_spare (u32 n_local, u32 n_alloc, u32 n_lent, u32 peak);

#endif /* __included_bpa_rebalance_h__ */
