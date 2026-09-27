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

/*
 * A port for a new session, from the blocks the subscriber already holds,
 * oldest first: the offset, with `*blk_idx` naming its block, or -1 when
 * every block is full - the caller then decides whether another block can be
 * had. Port preservation only applies to the block whose range contains the
 * original port, which is normally the first; elsewhere any free port will
 * do.
 */
static_always_inline int
bpa_subblocks_alloc (bpa_subblock_t *blocks, u16 port_priv, u32 *blk_idx)
{
  u32 i;

  for (i = 0; i < vec_len (blocks); i++)
    {
      bpa_subblock_t *sb = &blocks[i];
      int preferred = -1, off;

      if (port_priv >= sb->blk.port_lo && port_priv <= sb->blk.port_hi)
	preferred = port_priv - sb->blk.port_lo;
      off = bpa_portmap_alloc (&sb->map, preferred);
      if (off >= 0)
	{
	  *blk_idx = i;
	  return off;
	}
    }
  return -1;
}

/*
 * The fill alarm's next state, from how full the subscriber is (`pct`) and
 * the pool's threshold: returns 1 exactly when this allocation CROSSED it and
 * a line is to be logged. Ten points of hysteresis on the way down, so a
 * subscriber sitting on the threshold does not produce a line every time a
 * port is freed and taken. Evaluated where a port is taken and nowhere else:
 * utilisation only rises there.
 */
static_always_inline int
bpa_alarm_step (u8 *alarmed, u32 pct, u32 threshold)
{
  if (!*alarmed && pct >= threshold)
    {
      *alarmed = 1;
      return 1;
    }
  if (*alarmed && pct + 10 < threshold)
    *alarmed = 0;
  return 0;
}

#endif /* __included_bpa_subblock_h__ */
