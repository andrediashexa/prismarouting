/*
 * bpa_block_ident.h - the identity a block's ALLOCATION record named (#397).
 *
 * WHAT THIS DECIDES, and it is a decision rather than a consequence. An
 * allocation record carries the identity in force at the allocation, which is
 * #21's requirement and is asserted on the allocation lines of lab/syslog.sh.
 * A release record used to carry the identity in force at the RELEASE, because
 * that is when its own event happens - so when the identity table changed in
 * between, the two records for one block named different subscribers.
 *
 * The maintainer chose, of the three answers #397 sets out, to CARRY THE
 * IDENTITY WITH THE BLOCK: a release repeats what the allocation said. A
 * collector reading only the release line then gets the subscriber who held
 * the block, and a court order about last month is answered by either record
 * on its own rather than by pairing them.
 *
 * WHERE IT IS KEPT, and why not in the allocator. The identity is looked up
 * once, where the allocation record is built, and remembered against the
 * block that record names - so nothing on the packet path changes, the
 * allocator keeps no field it cannot test standalone, and a box with no
 * identity table pays nothing at all: `remember` of the unknown marker stores
 * nothing.
 *
 * WHAT IT COSTS: one entry per HELD block whose subscriber is known, of
 * BPA_IDENTITY_MAX bytes plus its hash entry, on the worker that holds the
 * block. Not per configured block - a pool is configured for its capacity and
 * holds what its subscribers asked for - and not per queued event, which is
 * what the queue's own copy costs.
 */
#ifndef __included_bpa_block_ident_h__
#define __included_bpa_block_ident_h__

#ifdef BPA_STANDALONE
#include "bpa_shim.h"
#include "bpa_vec_shim.h"
#endif

#include "bpa_identity.h"

typedef struct
{
  /*
   * The key this slot is filed under, kept beside the name so that a walk of
   * the slots can find every entry of one pool without iterating the hash.
   * `bpa_block_ident_forget_pool` is why: a pool being deleted has to take its
   * entries with it, or the next pool to be given that index inherits them -
   * and a record naming the previous pool's subscriber is the failure this
   * whole file exists to prevent, one reuse further along.
   *
   * BPA_NO_IDENT_KEY while the slot is on the free stack.
   */
  u64 key;
  char name[BPA_IDENTITY_MAX];
} bpa_block_ident_t;

/* No block has this key: the pool field is 16 bits and the port is 16, so a
 * key with every bit set names pool 65535 at 255.255.255.255 port 65535. */
#define BPA_NO_IDENT_KEY 0xffffffffffffffffULL

typedef struct
{
  /*
   * block key -> slot index + 1, so 0 is "nothing remembered".
   *
   * Keyed by what the RECORD names - the pool, the public address and the
   * first port of the block - rather than by the allocator's own index. The
   * two release paths that do not come from the allocator (a pool deleted, a
   * stop-drain) have the record's fields and not the allocator's, and a key
   * only one of three callers can build is a key the other two cannot look
   * up.
   */
  uword *by_block;
  bpa_block_ident_t *slot; /* names, indexed by the hash's value - 1 */
  u32 *free_slot;	   /* stack of slots to reuse */
  u32 n_held;		   /* entries in use */
  u64 n_recalled;	   /* releases answered from here */
  /*
   * Releases with nothing remembered, which is not a failure and is worth
   * counting: a block allocated before this version, or by a worker whose
   * table was freed, or one whose subscriber was never in the table. The
   * caller falls back to a lookup at the release, which is what the box did
   * for every release before #397.
   */
  u64 n_unremembered;
} bpa_block_ident_table_t;

/*
 * The key for one block, out of the three fields every record carries.
 *
 * The pool index is 16 bits here and a u32 in the plugin: a box with 65536
 * pools does not exist and the whole key has to fit a uword on the 64-bit
 * targets this product builds for. The port is the block's FIRST port, which
 * is what both records print.
 */
static inline u64
bpa_block_ident_key (u32 pool_id, u32 ip_pub, u16 port_lo)
{
  return ((u64) (pool_id & 0xffff) << 48) | ((u64) ip_pub << 16)
	 | (u64) port_lo;
}

void bpa_block_ident_remember (bpa_block_ident_table_t *t, u64 key,
			       const char *name);
int bpa_block_ident_recall (bpa_block_ident_table_t *t, u64 key, char *out,
			    u32 cap);
void bpa_block_ident_forget (bpa_block_ident_table_t *t, u64 key);
void bpa_block_ident_forget_pool (bpa_block_ident_table_t *t, u32 pool_id);
void bpa_block_ident_free (bpa_block_ident_table_t *t);

#endif /* __included_bpa_block_ident_h__ */
