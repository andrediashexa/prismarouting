/*
 * bpa_block_ident.c - see bpa_block_ident.h for what this decides and why.
 *
 * A hash to a slot, and a free stack of slots, because a VPP hash value is one
 * word and an identity is BPA_IDENTITY_MAX bytes. The same shape bpa_dyn.c
 * uses for its free list, and for the same reason: reusing a slot by index is
 * a pop rather than a search.
 */
#ifdef BPA_STANDALONE
#include "bpa_shim.h"
#include "bpa_vec_shim.h"
#else
#include <vlib/vlib.h>
#endif

#include "bpa_block_ident.h"

#include <string.h>

/*
 * Remembers `name` as the identity of the block `key` names.
 *
 * THE UNKNOWN MARKER IS NOT REMEMBERED, and that is what makes this free on a
 * box with no identity table: there is nothing to repeat, the release would
 * look up the same unknown, and an entry per held block on every box that has
 * never loaded a map would be a cost paid by everybody for nothing.
 *
 * An allocation over a key that is already here REPLACES the name. Two
 * allocations of one block with no release between them is a defect the
 * release accounting exists to find (#215), and a stale name kept under the
 * new allocation would make the release name the older subscriber - which is
 * the exact failure this file exists to prevent, one holder further along.
 */
void
bpa_block_ident_remember (bpa_block_ident_table_t *t, u64 key,
			  const char *name)
{
  uword *p;
  u32 slot;

  if (!t || !name || name[0] == 0
      || strncmp (name, bpa_identity_unknown, BPA_IDENTITY_MAX) == 0)
    return;

  if (!t->by_block)
    t->by_block = hash_create (0, sizeof (uword));

  p = hash_get (t->by_block, key);
  if (p)
    slot = (u32) (p[0] - 1);
  else
    {
      if (vec_len (t->free_slot))
	slot = vec_pop (t->free_slot);
      else
	{
	  bpa_block_ident_t blank = { BPA_NO_IDENT_KEY, { 0 } };
	  vec_add1 (t->slot, blank);
	  slot = (u32) (vec_len (t->slot) - 1);
	}
      hash_set (t->by_block, key, (uword) slot + 1);
      t->n_held++;
    }

  t->slot[slot].key = key;
  memset (t->slot[slot].name, 0, sizeof (t->slot[slot].name));
  strncpy (t->slot[slot].name, name, sizeof (t->slot[slot].name) - 1);
}

/*
 * The identity remembered for `key`, and FORGETS it. Returns 1 when there was
 * one, 0 when there was not.
 *
 * Forgetting is part of the read because a block has exactly one release: a
 * recall that left the entry behind would hold every block this box has ever
 * handed out, which is a leak with a court record's name on it.
 *
 * `out` is left alone when nothing was remembered, so a caller can fill it
 * with a lookup of its own and not have to clear it first.
 */
int
bpa_block_ident_recall (bpa_block_ident_table_t *t, u64 key, char *out,
			u32 cap)
{
  uword *p;
  u32 slot;

  if (!t || !t->by_block || !out || cap == 0)
    {
      if (t)
	t->n_unremembered++;
      return 0;
    }
  p = hash_get (t->by_block, key);
  if (!p)
    {
      t->n_unremembered++;
      return 0;
    }
  slot = (u32) (p[0] - 1);
  memset (out, 0, cap);
  strncpy (out, t->slot[slot].name, cap - 1);
  hash_unset (t->by_block, key);
  t->slot[slot].key = BPA_NO_IDENT_KEY;
  vec_add1 (t->free_slot, slot);
  t->n_held--;
  t->n_recalled++;
  return 1;
}

/*
 * Drops what is remembered for `key` without reading it.
 *
 * For the release paths that emit no record: a pool being deleted frees every
 * block it held and logs them, but a block whose pool is gone and whose record
 * was dropped - the queue was full - still has to leave this table, or the
 * entry outlives every reader of it.
 */
void
bpa_block_ident_forget (bpa_block_ident_table_t *t, u64 key)
{
  char scratch[BPA_IDENTITY_MAX];
  u64 recalled;

  if (!t)
    return;
  /* The same walk, so there is one place that maintains the free stack and
   * the count. What differs is that nothing reads the name - and that this is
   * not a release answered from here, so the counters do not move. */
  recalled = t->n_recalled;
  if (bpa_block_ident_recall (t, key, scratch, sizeof (scratch)))
    t->n_recalled = recalled;
  else
    t->n_unremembered--;
}

/*
 * Drops every entry of one pool.
 *
 * A pool being deleted logs each block it held, and each of those releases
 * takes its own entry out - but only the ones whose record was actually
 * queued. A queue that was full, or a box with no transport enabled, leaves
 * entries behind, and the pool index they are filed under is handed to the
 * next pool created. This is what keeps that pool's first releases from naming
 * the deleted pool's subscribers.
 *
 * Walks the SLOTS rather than the hash: each slot carries the key it is filed
 * under for exactly this, and VPP's hash iteration cannot be driven by a
 * standalone harness.
 */
void
bpa_block_ident_forget_pool (bpa_block_ident_table_t *t, u32 pool_id)
{
  u32 i;

  if (!t || !t->by_block)
    return;
  for (i = 0; i < vec_len (t->slot); i++)
    {
      u64 key = t->slot[i].key;

      if (key == BPA_NO_IDENT_KEY)
	continue;
      if ((u32) ((key >> 48) & 0xffff) != (pool_id & 0xffff))
	continue;
      hash_unset (t->by_block, key);
      t->slot[i].key = BPA_NO_IDENT_KEY;
      vec_add1 (t->free_slot, i);
      t->n_held--;
    }
}

void
bpa_block_ident_free (bpa_block_ident_table_t *t)
{
  if (!t)
    return;
  hash_free (t->by_block);
  vec_free (t->slot);
  vec_free (t->free_slot);
  t->n_held = 0;
}
