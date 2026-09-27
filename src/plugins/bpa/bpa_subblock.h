/*
 * bpa_subblock.h - one port block a subscriber holds, and what is read off
 * the list of them (#472).
 *
 * Out of bpa_session.h, which only VPP compiles, so the arithmetic over a
 * subscriber's blocks has a unit test: test/test_bpa_subblock.c.
 */
#ifndef __included_bpa_subblock_h__
#define __included_bpa_subblock_h__

#include "bpa_pool.h"
#include "bpa_ports.h"
#ifdef BPA_STANDALONE
#include "bpa_vec_shim.h"
#endif

/*
 * One port block held by a subscriber, with its bitmap.
 *
 * A subscriber usually holds exactly one. It gets a second when the first
 * fills, the per-subscriber cap allows it, and the pool has a block to give:
 * off the free list in dynamic mode, out of the reserve in deterministic mode.
 * A deterministic pool with NO reserve has none to give, because the formula
 * already assigns every mapped block to somebody (issue #39).
 */
typedef struct
{
  bpa_block_t blk;   /* public address and port range */
  bpa_portmap_t map;
} bpa_subblock_t;

/*
 * How full a subscriber's blocks are, across all of them: ports in use and
 * ports held, over a vector of blocks.
 *
 * Summed from the port bitmaps, which the allocator maintains anyway, so
 * nothing new is counted per packet and the session table is never walked.
 */
static_always_inline void
bpa_subblocks_usage (const bpa_subblock_t *blocks, u32 *used, u32 *total)
{
  const bpa_subblock_t *sb;

  *used = *total = 0;
  vec_foreach (sb, blocks)
    {
      *total += sb->map.n_ports;
      *used += sb->map.n_ports - sb->map.n_free;
    }
}

#endif /* __included_bpa_subblock_h__ */
