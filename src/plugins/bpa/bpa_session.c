/* bpa_session.c - per-worker session table. */

#include "bpa.h"
#include "bpa_exhaust.h"
#include "bpa_esp.h"
#include "bpa_pptp.h"
#include "bpa_rewrite.h"
#include "bpa_log.h"
#include "bpa_stats.h"

/*
 * A change to the statistics segment's pool counters (#504), charged to the
 * thread that OWNS `pt` rather than the one running: sessions expire on the
 * main thread under the barrier, and VPP asserts a simple counter never goes
 * below zero in its own slot.
 */
#define BPA_STAT(which, pt, pool, n)                                          \
  bpa_stats_add (&bpa_stats_main.which, (pt) - bpa_main.per_thread, (pool),  \
		 (n))

/*
 * Per-worker table sizing. Sessions are sharded by subscriber, so each
 * worker holds roughly total_sessions / n_workers entries.
 *
 * The size is a RESERVATION of virtual space, not immediate consumption -
 * but the OOM killer counts virtual. At 64 MB per table, two tables and
 * several threads, VPP mapped over 1 GB and was killed on a 2 GB box.
 * The default is now modest and configurable: whoever has 200k subscribers
 * raises it in startup.conf, whoever has a small box does not pay for it.
 */

/* not a unit: it creates this worker's bihash and its pools at the
 * size bpa_main was configured with, which is VPP allocating. lab/soak.sh
 * fills those tables on a box and lab/handoff.sh drives two workers. */
clib_error_t *
bpa_per_thread_init (bpa_per_thread_t *pt, u32 thread_index)
{
  bpa_main_t *bm = &bpa_main;
  u8 *name;

  if (pt->initialised)
    return 0;

  name = format (0, "bpa-in2out-%u%c", thread_index, 0);
  clib_bihash_init_16_8 (&pt->in2out, (char *) name, bm->hash_buckets,
			 bm->hash_memory);
  name = format (0, "bpa-out2in-%u%c", thread_index, 0);
  clib_bihash_init_16_8 (&pt->out2in, (char *) name, bm->hash_buckets,
			 bm->hash_memory);
  /* Reserved address space, touched only by NAT64 traffic (#485). */
  name = format (0, "bpa-in2out6-%u%c", thread_index, 0);
  clib_bihash_init_24_8 (&pt->in2out6, (char *) name, bm->hash_buckets,
			 bm->hash_memory);

  pt->sub_by_idx = hash_create (0, sizeof (uword));
  /* This thread's ring, in the segment bpa_init() made before any thread was
   * set up: logging never allocates on a worker (#547), and the rings are
   * outside the main heap so another process can drain them (#550). */
  pt->log_q = bpa_log_seg.base ? bpa_log_seg_ring (bpa_log_seg.base,
						    thread_index)
			       : 0;
  pt->exhaust = clib_mem_alloc_aligned (sizeof (bpa_exhaust_t),
					CLIB_CACHE_LINE_BYTES);
  clib_memset (pt->exhaust, 0, sizeof (bpa_exhaust_t));
  pt->initialised = 1;
  return 0;
}

/*
 * Adds one block to a subscriber, with its own bitmap.
 *
 * Any port this block holds that the operator has forwarded is marked used
 * before the block is handed to anybody. That is the whole of "a forwarded
 * port MUST be reserved": the allocator is never told about it, so there is no
 * path by which it can be given to another flow - not a check that could be
 * forgotten on one of the paths that allocate.
 */
/* not a unit: the session table is VPP's: a bihash the worker owns, a pool of
 * subscribers indexed by a hash, and per-thread vectors. This adds a
 * block to one subscriber's list. What a block IS and where it comes from is
 * bpa_pool.h's and bpa_dyn.c's, both driven by their own harnesses;
 * lab/extblocks.sh watches a subscriber take a second one on a box. */
static void
bpa_sub_add_block (bpa_sub_t *sub, const bpa_pool_t *p, const bpa_block_t *b)
{
  u32 n_words = (p->block_size + 63) / 64;
  bpa_subblock_t *sb;
  u32 i, n;

  vec_add2 (sub->blocks, sb, 1);
  sb->blk = *b;
  bpa_portmap_init (&sb->map, p->block_size, clib_mem_alloc (n_words * sizeof (u64)));

  n = vec_len (p->statics);
  if (PREDICT_TRUE (n == 0))
    return;

  /* The forwards of one block are a run in a vector sorted by public port. */
  for (i = bpa_static_lower (p->statics, n, b->port_lo);
       i < n && p->statics[i].port_pub <= b->port_hi; i++)
    if (p->statics[i].ip_pub == b->ip_pub)
      bpa_portmap_reserve (&sb->map, p->statics[i].port_off);
}

/* Returns the subscriber's port state, creating it on first use. */
/* not a unit: the session table is VPP's: a bihash the worker owns, a pool of
 * subscribers indexed by a hash, and per-thread vectors. This finds or
 * creates the record, which is a hash lookup and a pool allocation.
 * lab/datapath.sh sends the first packet of a subscriber through a box. */
static bpa_sub_t *
bpa_sub_get (bpa_per_thread_t *pt, const bpa_pool_t *p, u32 pool_index,
	     const bpa_block_t *b)
{
  uword key = bpa_sub_key (pool_index, b->sub_idx);
  bpa_sub_t *sub;
  uword *q;

  q = hash_get (pt->sub_by_idx, key);
  if (q)
    return pool_elt_at_index (pt->subs, q[0]);

  pool_get_zero (pt->subs, sub);
  bpa_sub_add_block (sub, p, b);
  hash_set (pt->sub_by_idx, key, sub - pt->subs);
  BPA_STAT (subscribers, pt, pool_index, 1);
  BPA_STAT (blocks, pt, pool_index, 1);
  return sub;
}

/* not a unit: VPP's counters; the arithmetic is bpa_subblocks_usage(),
 * which test/test_bpa_subblock.c holds, and lab/snmp.sh fills a subscriber
 * past 90% and reads the pool's count move */
/*
 * Tells the statistics segment what this subscriber uses now (#504): the
 * pool's port total moves by the difference, and its count of subscribers at
 * 90% or more by the crossing. Called where the usage changes - a session
 * made or freed, a block added - so no reader ever walks the subscribers,
 * which is what the SNMP agent used to do on each refresh.
 */
static void
bpa_sub_seen (bpa_per_thread_t *pt, u32 pool_index, bpa_sub_t *sub)
{
  u32 used, total;
  u8 near;

  bpa_sub_usage (sub, &used, &total);
  near = total && (u64) used * 10 >= (u64) total * 9;
  if (used != sub->ports_seen)
    {
      BPA_STAT (ports, pt, pool_index, (i64) used - (i64) sub->ports_seen);
      sub->ports_seen = used;
    }
  if (near != sub->near_full)
    {
      BPA_STAT (near_full, pt, pool_index, near ? 1 : -1);
      sub->near_full = near;
    }
}

/* not a unit: the session table is VPP's: a bihash the worker owns, a pool of
 * subscribers indexed by a hash, and per-thread vectors. This returns the
 * record when its last session goes, releasing the blocks with it.
 * lab/ipfix-stop.sh reads the records that release writes. */
static void
bpa_sub_put (bpa_per_thread_t *pt, u32 pool_index, u32 sub_idx,
	     bpa_sub_t *sub)
{
  bpa_subblock_t *sb;

  if (sub->n_sessions)
    return;

  BPA_STAT (subscribers, pt, pool_index, -1);
  BPA_STAT (blocks, pt, pool_index, -(i64) vec_len (sub->blocks));
  BPA_STAT (ports, pt, pool_index, -(i64) sub->ports_seen);
  if (sub->near_full)
    BPA_STAT (near_full, pt, pool_index, -1);
  vec_foreach (sb, sub->blocks)
    clib_mem_free (sb->map.bits);
  vec_free (sub->blocks);
  hash_unset (pt->sub_by_idx, bpa_sub_key (pool_index, sub_idx));
  pool_put (pt->subs, sub);
}

/*
 * How many blocks this subscriber already holds, and whether another is
 * allowed. Used by the datapath to decide whether to ask for an extension
 * before giving up on the packet.
 */
/* not a unit: it asks the worker's hash how many blocks a subscriber
 * holds. The count it returns is read on a box by lab/extblocks.sh, which
 * is where a subscriber gets a second block and gives it back. */
u32
bpa_sub_n_blocks (bpa_per_thread_t *pt, u32 pool_index, u32 sub_idx)
{
  uword *q = hash_get (pt->sub_by_idx, bpa_sub_key (pool_index, sub_idx));
  if (!q)
    return 0;
  return vec_len (pool_elt_at_index (pt->subs, q[0])->blocks);
}

/*
 * Attach an extra block to a subscriber that already exists.
 *
 * Only ever called after the caller has obtained a block it is entitled to
 * hand out. Nothing here checks where the block came from, because the two
 * modes answer that differently and only the caller knows which it is.
 */
/* not a unit: it asks the dynamic allocator for another block for a
 * subscriber that filled the one it had. bpa_dyn.c is that allocator and
 * test/test_bpa_dyn.c drives it; lab/extblocks.sh is the box. */
void
bpa_sub_extend (bpa_per_thread_t *pt, const bpa_pool_t *p, u32 pool_index,
		u32 sub_idx, const bpa_block_t *b)
{
  uword *q = hash_get (pt->sub_by_idx, bpa_sub_key (pool_index, sub_idx));
  if (!q)
    return;
  bpa_sub_add_block (pool_elt_at_index (pt->subs, q[0]), p, b);
  BPA_STAT (blocks, pt, pool_index, 1);
  bpa_sub_seen (pt, pool_index, pool_elt_at_index (pt->subs, q[0]));
}

/*
 * Gives a live subscriber record the block a forward has just pinned.
 *
 * A pin can land on a subscriber that is already translating out of a
 * different block, because in dynamic mode the allocator gave it whichever was
 * free. From this instant its forwarded socket lives in the pinned block, and
 * this record - one per worker - is what bpa_session_static_open() searches
 * for it. Without this the forward is accepted, shown by "show bpa forward",
 * and translates nothing until the customer's last session expires: configured
 * and silently inert, which is the failure mode every other part of this
 * feature is arranged to avoid.
 *
 * A subscriber with no sessions has no record and needs none: the record is
 * built from the pinned block when its first packet arrives.
 */
/* not a unit: it moves a block into a subscriber's list after the
 * allocator handed it over, under the worker's own state.
 * lab/dynamic-workers.sh drives the path on a box with several workers. */
void
bpa_sub_adopt_block (bpa_per_thread_t *pt, const bpa_pool_t *p, u32 pool_index,
		     const bpa_block_t *b)
{
  uword *q = hash_get (pt->sub_by_idx, bpa_sub_key (pool_index, b->sub_idx));
  bpa_sub_t *sub;
  u32 i;

  if (!q)
    return;

  sub = pool_elt_at_index (pt->subs, q[0]);
  for (i = 0; i < vec_len (sub->blocks); i++)
    if (sub->blocks[i].blk.ip_pub == b->ip_pub
	&& sub->blocks[i].blk.port_lo == b->port_lo)
      return;

  bpa_sub_add_block (sub, p, b);
  BPA_STAT (blocks, pt, pool_index, 1);
  bpa_sub_seen (pt, pool_index, sub);
}

/*
 * Builds the session record for a port offset that is already allocated in
 * `sb`'s bitmap, and indexes it in both directions.
 *
 * Split out because two callers need exactly this and differ only in how they
 * chose the offset: the datapath takes whatever the allocator gives it, while
 * an ALG needs a specific pair. Duplicating the tail would be duplicating the
 * two hash insertions, and a session indexed in one direction only is a
 * mapping that translates outbound and drops every reply.
 */
/* not a unit: it writes the session into the worker's bihash, which
 * is where every packet after the first finds it. lab/datapath.sh sends
 * those packets. */
static bpa_session_t *
bpa_session_bind (bpa_per_thread_t *pt, const bpa_pool_t *p, u32 pool_index,
		  bpa_sub_t *sub, const bpa_subblock_t *sb, u32 blk_idx,
		  u16 off, u16 port_priv, u8 proto, u32 fib, f64 now)
{
  clib_bihash_kv_16_8_t kv;
  bpa_session_t *s;

  /*
   * The one gate that makes a budgeted delete terminate.
   *
   * bpa_pool_del() drops the worker barrier between chunks, so the datapath
   * runs while the pool is half drained. Without this, an ALG opening a data
   * connection on a control session the walk has not reached yet would put a
   * new session behind the cursor, and it would be left holding a public port
   * of a pool that no longer exists. Here rather than in each caller because
   * this is where every path that is not GRE ends up.
   */
  if (PREDICT_FALSE (p->deleted))
    return 0;
  /* The static forward and the FTP data connection come here without
   * passing bpa_session_create(): the same gate, for the same abort (#460). */
  if (PREDICT_FALSE (!bpa_session_room (pt)))
    return 0;

  pool_get_zero (pt->sessions, s);
  s->ip_pub = sb->blk.ip_pub;
  s->port_priv = port_priv;
  s->port_off = off;
  s->blk_idx = (u8) blk_idx;
  s->port_pub = (u16) (sb->blk.port_lo + off);
  s->proto = proto;
  s->sub_idx = sb->blk.sub_idx;
  s->pool_index = pool_index;
  /* Set before the key is built from it, which is the only ordering that
   * matters here: a session keyed in one VRF and freed in another leaves an
   * entry pointing at reclaimed memory. */
  s->fib_index = fib;
  s->out_fib_index = p->out_fib_index;
  s->last_heard = now;

  if (bpa_set_addr_of (&p->inside, sb->blk.sub_idx, &s->ip_priv))
    {
      pool_put (pt->sessions, s);
      return 0;
    }

  sub->n_sessions++;
  BPA_STAT (sessions, pt, pool_index, 1);
  bpa_sub_seen (pt, pool_index, sub);

  /* A NAT64 session's inside key is its IPv6 host, which the caller holds
   * and installs (bpa_nat64_session_key): the pseudo ip_priv is the
   * subscriber, shared by every host of the prefix, and keyed here two hosts
   * on one port would overwrite each other (#485). */
  s->is_nat64 = p->is_nat64;
  if (PREDICT_TRUE (!p->is_nat64))
    {
      bpa_key_in (&kv, s->ip_priv, s->port_priv, proto, s->fib_index);
      kv.value = s - pt->sessions;
      clib_bihash_add_del_16_8 (&pt->in2out, &kv, 1);
    }

  bpa_key_out (&kv, s->ip_pub, s->port_pub, proto);
  kv.value = s - pt->sessions;
  clib_bihash_add_del_16_8 (&pt->out2in, &kv, 1);

  /*
   * Last, because it reads ip_priv, which bpa_set_addr_of() resolved above and
   * which every early return here skips. A session that leaves this function
   * carries a correction; a session that does not leave it has none to carry.
   */
  bpa_session_csum_deltas (s);

  return s;
}

/*
 * Two consecutive public ports, RTP even, for media an ALG has announced.
 *
 * Deliberately refuses rather than improvises. If either private port is
 * already mapped, or no even-aligned pair is free in a block on the right
 * public address, the caller leaves the packet alone and counts it: an
 * announced port pair that does not behave as a pair is the failure this
 * exists to prevent, so half of one is worse than none.
 */
/* not a unit: it reserves the SECOND port an ALG needs - RTP
 * beside RTCP - in the subscriber's own block, through the port map and the
 * bihash. Whether an existing pair may be announced again is
 * bpa_pair_is_announceable(), the pair itself bpa_portmap_alloc_pair(), each
 * driven by name; lab/rtsp.sh and lab/sip.sh drive the ALGs that ask. */
int
bpa_alg_reserve_pair (bpa_per_thread_t *pt, const bpa_pool_t *p,
		      u32 pool_index, u32 sub_idx, u32 want_ip_pub,
		      u16 priv_lo, u8 proto, u32 fib, f64 now, u16 *pub_lo)
{
  clib_bihash_kv_16_8_t kv, value;
  bpa_sub_t *sub;
  uword *q;
  u32 blk_idx, ip_priv;

  if (priv_lo == 0 || priv_lo == 65535)
    return -1;

  q = hash_get (pt->sub_by_idx, bpa_sub_key (pool_index, sub_idx));
  if (!q)
    return -1;
  sub = pool_elt_at_index (pt->subs, q[0]);

  if (bpa_set_addr_of (&p->inside, sub_idx, &ip_priv))
    return -1;

  /*
   * Already mapped: a retransmitted SETUP, or a second one naming the same
   * ports. Hand back the pair given the first time.
   *
   * Refusing here was the first implementation and it is wrong: the
   * retransmission would then leave carrying the subscriber's PRIVATE ports,
   * the server would believe the last thing it read, and the media would be
   * sent to an address that means nothing - the exact failure this ALG
   * exists to prevent, reachable by one lost packet.
   */
  bpa_key_in (&kv, ip_priv, priv_lo, proto, fib);
  if (clib_bihash_search_16_8 (&pt->in2out, &kv, &value) == 0)
    {
      bpa_session_t *m0 = pool_elt_at_index (pt->sessions, value.value);
      bpa_session_t *m1;

      bpa_key_in (&kv, ip_priv, (u16) (priv_lo + 1), proto, fib);
      if (clib_bihash_search_16_8 (&pt->in2out, &kv, &value))
	return -1; /* half a pair: not something to announce */
      m1 = pool_elt_at_index (pt->sessions, value.value);

      if (!bpa_pair_is_announceable (want_ip_pub, m0->ip_pub, m0->port_pub,
				     m1->ip_pub, m1->port_pub))
	return -1;

      m0->last_heard = now;
      m1->last_heard = now;
      *pub_lo = m0->port_pub;
      return 0;
    }

  /* The low port is free and the high one is not. Allocating a fresh pair
   * would announce a low port whose neighbour belongs to another flow. */
  bpa_key_in (&kv, ip_priv, (u16) (priv_lo + 1), proto, fib);
  if (clib_bihash_search_16_8 (&pt->in2out, &kv, &value) == 0)
    return -1;

  for (blk_idx = 0; blk_idx < vec_len (sub->blocks); blk_idx++)
    {
      bpa_subblock_t *sb = vec_elt_at_index (sub->blocks, blk_idx);
      bpa_session_t *s0, *s1;
      u32 i0, i1;
      int off;

      if (sb->blk.ip_pub != want_ip_pub)
	continue;

      off = bpa_portmap_alloc_pair (&sb->map, sb->blk.port_lo & 1);
      if (off < 0)
	continue;

      /*
       * INDICES, not pointers, across the second bind.
       *
       * bpa_session_bind() ends in pool_get_zero(), which may grow the session
       * vector and move every element of it. So the pointer the FIRST bind
       * returned is dangling the moment the second one reallocates - and the
       * two writes below used it. One of the pair silently kept is_expect = 0,
       * sat on the UDP timeout instead of the two-second alg-expect timer, and
       * an announced media port nobody used was held for hours; the write
       * itself landed in memory the allocator had already given back.
       *
       * Intermittent by nature - it needs the second bind to cross a growth
       * boundary - which is what made it look like a loaded runner: two runs
       * in three failed. lab/rtsp.sh sees it as "2 left" where 1 is right.
       *
       * bpa_ftp.c learned this in its own words - "Installing the expectation
       * allocates from the session pool, which may move it. Everything below -
       * including the caller's own pointer - has to come from the index, not
       * from the pointer we walked in with." The same sentence belongs here.
       */
      s0 = bpa_session_bind (pt, p, pool_index, sub, sb, blk_idx, (u16) off,
			     priv_lo, proto, fib, now);
      i0 = s0 ? (u32) (s0 - pt->sessions) : ~0u;
      s1 = bpa_session_bind (pt, p, pool_index, sub, sb, blk_idx,
			     (u16) (off + 1), (u16) (priv_lo + 1), proto, fib,
			     now);
      i1 = s1 ? (u32) (s1 - pt->sessions) : ~0u;

      /* Both re-resolved before either is touched, including on the error
       * path: handing a moved pointer to bpa_session_free() frees whatever
       * occupies that slot now. */
      s0 = (i0 != ~0u) ? pool_elt_at_index (pt->sessions, i0) : 0;
      s1 = (i1 != ~0u) ? pool_elt_at_index (pt->sessions, i1) : 0;

      if (s0 == 0 || s1 == 0)
	{
	  /* Only reachable when the subscriber index stopped resolving, which
	   * means the pool changed under us. Give both ports back rather than
	   * leave a half-open pair behind. */
	  if (s0)
	    bpa_session_free (pt, s0, now);
	  else
	    bpa_portmap_free (&sb->map, (u16) off);
	  if (s1)
	    bpa_session_free (pt, s1, now);
	  else
	    bpa_portmap_free (&sb->map, (u16) (off + 1));
	  return -1;
	}

      s0->is_expect = 1;
      s1->is_expect = 1;
      *pub_lo = s0->port_pub;
      return 0;
    }

  return -1;
}

/*
 * The creation-rate bucket is bpa_rate.h: two fields and a clock, and
 * nothing of VPP - so it lives where test/test_bpa_rate.c can call it. What
 * is here is the subscriber whose fields they are.
 */
/* not a unit: a one-line wrapper over bpa_rate_take() in bpa_rate.h,
 * which test/test_bpa_rate.c drives; what is here is the subscriber record
 * whose two fields it passes. */
static_always_inline int
bpa_sub_rate_take (bpa_sub_t *sub, u32 rate, f64 now)
{
  return bpa_rate_take (&sub->tokens, &sub->rate_last, rate, now);
}


/* not a unit: the whole creation path: a port out of the map, a
 * record in the bihash, a counter on the worker, and the refusals that stop
 * each of those. lab/datapath.sh, lab/limits.sh and lab/soak.sh drive it,
 * and the decisions it consults - the port choice (bpa_subblocks_alloc),
 * the fill alarm (bpa_alarm_step), the bucket, the block arithmetic - are
 * driven by their own harnesses. */
bpa_session_t *
bpa_session_create (bpa_per_thread_t *pt, const bpa_pool_t *p, u32 pool_index,
		    const bpa_block_t *b, u16 port_priv, u8 proto, u32 fib,
		    f64 now, bpa_create_kind_t kind, bpa_create_why_t *why)
{
  bpa_session_t *s;
  bpa_sub_t *sub;
  bpa_subblock_t *sb = 0;
  u32 blk_idx;
  int off;

  *why = BPA_CREATE_OK;
  /* Before anything is taken - the subscriber record, a port - so a refusal
   * leaves nothing behind to undo (#460). */
  if (PREDICT_FALSE (!bpa_session_room (pt)))
    {
      *why = BPA_CREATE_NO_MEMORY;
      return 0;
    }
  sub = bpa_sub_get (pt, p, pool_index, b);

  /*
   * The per-subscriber bounds, before a port is taken.
   *
   * Here rather than in the caller because the subscriber record is already
   * resolved at this point: checking in the datapath node would mean a second
   * lookup for a number that is one dereference away. A packet on an existing
   * session never reaches this function at all.
   */
  if (PREDICT_FALSE (kind == BPA_CREATE_TRAFFIC && p->max_sessions
		     && sub->n_sessions >= p->max_sessions))
    {
      sub->n_refused_sessions++;
      *why = BPA_CREATE_OVER_SESSIONS;
      bpa_sub_put (pt, pool_index, b->sub_idx, sub);
      return 0;
    }
  if (PREDICT_FALSE (kind == BPA_CREATE_TRAFFIC && p->max_rate
		     && !bpa_sub_rate_take (sub, p->max_rate, now)))
    {
      sub->n_refused_rate++;
      *why = BPA_CREATE_OVER_RATE;
      bpa_sub_put (pt, pool_index, b->sub_idx, sub);
      return 0;
    }

  /* The blocks the subscriber already holds, oldest first, keeping the
   * original port where its block allows (bpa_subblocks_alloc). */
  off = bpa_subblocks_alloc (sub->blocks, port_priv, &blk_idx);
  if (off >= 0)
    sb = vec_elt_at_index (sub->blocks, blk_idx);

  /*
   * Every block full. The caller decides whether an extension is possible -
   * it owns the free list in dynamic mode, and knows there is none in
   * deterministic mode. Here we only report that another block is needed.
   */
  if (off < 0)
    {
      /* Recorded before the subscriber can be reclaimed: this is the number
       * "show bpa subscriber" reports, and it is the difference between "the
       * customer is at their limit" and "the customer is idle". */
      sub->n_refused++;
      BPA_STAT (refused, pt, pool_index, 1);
      *why = BPA_CREATE_NO_PORT;
      bpa_sub_put (pt, pool_index, b->sub_idx, sub);
      return 0;
    }

  /*
   * The fill alarm, evaluated where a port is TAKEN and nowhere else.
   *
   * Utilisation only rises here, so this is the only place a crossing can
   * happen; it falls on the release path, and the flag is cleared on the next
   * allocation rather than by touching that path. The cost is that a
   * subscriber which empties and never allocates again keeps its flag, which
   * costs a bit and no correctness: the alarm is about a subscriber filling
   * up, and one that has stopped asking is not filling up.
   */
  if (PREDICT_FALSE (p->util_alarm != 0))
    {
      u32 used, total, pct;

      bpa_sub_usage (sub, &used, &total);
      pct = total ? (used * 100) / total : 0;

      if (bpa_alarm_step (&sub->alarmed, pct, p->util_alarm))
	{
	  ip4_address_t a;

	  a.as_u32 = clib_host_to_net_u32 (b->ip_pub);
	  vlib_log_warn (bpa_main.log_class,
			 "subscriber %u of pool %v is %u%% through its "
			 "%u ports on %U: past the %u%% alarm. RFC 8158 "
			 "natEvent 12 would only report this once the "
			 "customer had already started losing traffic.",
			 b->sub_idx, p->name, pct, total, format_ip4_address,
			 &a, p->util_alarm);
	}
    }

  s = bpa_session_bind (pt, p, pool_index, sub, sb, blk_idx, (u16) off,
			port_priv, proto, fib, now);
  if (s == 0)
    {
      bpa_portmap_free (&sb->map, (u16) off);
      *why = BPA_CREATE_NO_PORT;
      bpa_sub_put (pt, pool_index, b->sub_idx, sub);
      return 0;
    }

  return s;
}

/*
 * A PPTP call.
 *
 * Almost bpa_session_create(), and deliberately not it: the two directions of
 * a GRE mapping are not keyed the same way, and only the inbound one can be
 * installed here. See bpa_key_gre() for why.
 *
 * The subscriber must already exist. It does: this is only ever called from
 * the PPTP ALG, on a control connection that is itself a translated session,
 * so a block was allocated before the call request was ever sent.
 */
/* not a unit: the same for the enhanced GRE that PPTP carries
 * its payload in, whose Call ID stands in for a port. lab/pptp.sh drives a
 * call through a box. */
bpa_session_t *
bpa_session_create_gre (bpa_per_thread_t *pt, const bpa_pool_t *p,
			u32 pool_index, u32 sub_idx, u32 ip_pub, u16 call_id,
			u32 remote, u32 fib, f64 now)
{
  clib_bihash_kv_16_8_t kv;
  bpa_session_t *s;
  bpa_sub_t *sub;
  bpa_subblock_t *sb = 0;
  u32 blk_idx;
  int off = -1;
  uword *q;

  q = hash_get (pt->sub_by_idx, bpa_sub_key (pool_index, sub_idx));
  if (!q)
    return 0;
  sub = pool_elt_at_index (pt->subs, q[0]);

  /*
   * Only blocks on the control connection's own public address, and this is
   * a protocol requirement rather than tidiness: a PPTP server ties the GRE
   * flow to the address the control connection came from. A subscriber
   * holding a second block on a different public address (issue #13) whose
   * call was placed from there would build a tunnel the server discards.
   *
   * No port preservation. The subscriber's Call ID is not a port and lands
   * inside the block only by coincidence; keeping it would buy nothing and
   * would make two calls that chose the same Call ID contend for one offset.
   */
  for (blk_idx = 0; blk_idx < vec_len (sub->blocks); blk_idx++)
    {
      sb = vec_elt_at_index (sub->blocks, blk_idx);
      if (sb->blk.ip_pub != ip_pub)
	continue;
      off = bpa_portmap_alloc (&sb->map, -1);
      if (off >= 0)
	break;
    }
  if (off < 0 || sb == 0)
    return 0; /* no room on that address: the tunnel does not come up */

  /* GRE builds its own record rather than going through bpa_session_bind(),
   * so it needs the drain gate of its own. See the one there. */
  if (PREDICT_FALSE (p->deleted) || PREDICT_FALSE (!bpa_session_room (pt)))
    {
      bpa_portmap_free (&sb->map, (u16) off);
      return 0;
    }

  pool_get_zero (pt->sessions, s);
  s->ip_pub = sb->blk.ip_pub;
  s->port_priv = call_id;
  s->port_off = (u16) off;
  s->blk_idx = (u8) blk_idx;
  s->port_pub = sb->blk.port_lo + (u16) off;
  s->proto = BPA_PROTO_GRE;
  s->gre_state = BPA_GRE_CALLING;
  s->sub_idx = sub_idx;
  s->pool_index = pool_index;
  s->fib_index = fib;
  s->out_fib_index = p->out_fib_index;
  s->remote = remote;
  s->last_heard = now;

  if (bpa_set_addr_of (&p->inside, sub_idx, &s->ip_priv))
    {
      bpa_portmap_free (&sb->map, (u16) off);
      pool_put (pt->sessions, s);
      return 0;
    }

  sub->n_sessions++;
  BPA_STAT (sessions, pt, pool_index, 1);
  bpa_sub_seen (pt, pool_index, sub);

  /* Inbound: the server will send GRE carrying the Call ID we just handed
   * out, and it came from the block, so the pre-check and the handoff find
   * the right subscriber without knowing anything about PPTP. */
  bpa_key_out (&kv, s->ip_pub, s->port_pub, BPA_PROTO_GRE);
  kv.value = s - pt->sessions;
  clib_bihash_add_del_16_8 (&pt->out2in, &kv, 1);

  /* The ALG's own handle on this call, so a retransmitted call request maps
   * to the same Call ID instead of allocating a second one. */
  bpa_key_gre (&kv, s->ip_priv, call_id, remote, BPA_GRE_KEY_ALG,
	       s->fib_index);
  kv.value = s - pt->sessions;
  clib_bihash_add_del_16_8 (&pt->in2out, &kv, 1);

  /*
   * Last, because it reads ip_priv, which bpa_set_addr_of() resolved above and
   * which every early return here skips. A session that leaves this function
   * carries a correction; a session that does not leave it has none to carry.
   */
  bpa_session_csum_deltas (s);

  return s;
}

/*
 * An IPsec tunnel to one peer.
 *
 * Almost bpa_session_bind(), and deliberately not it: that one exists to hand
 * out a public PORT, and ESP has nowhere to carry one. What this still needs
 * from the subscriber record is everything else - the caps, the session count
 * that keeps a dynamic block out of hold-down, and the public address the
 * block already decided.
 */
/* not a unit: the same for IPsec ESP, which has no port at all
 * and is bound by SPI. lab/esp.sh drives a tunnel through a box. */
bpa_session_t *
bpa_session_create_esp (bpa_per_thread_t *pt, const bpa_pool_t *p,
			u32 pool_index, const bpa_block_t *b, u32 remote,
			u32 fib, f64 now, bpa_create_why_t *why)
{
  bpa_main_t *bm = &bpa_main;
  clib_bihash_kv_16_8_t kv, value;
  bpa_session_t *s;
  bpa_sub_t *sub;
  u32 ip_priv;

  *why = BPA_CREATE_OK;

  /* The drain gate, for the same reason bpa_session_bind() has one: the pool
   * delete drops the barrier between chunks, and a session created behind the
   * cursor would outlive the pool it belongs to. */
  if (PREDICT_FALSE (p->deleted))
    return 0;

  if (bpa_set_addr_of (&p->inside, b->sub_idx, &ip_priv))
    return 0;

  /*
   * Refused BEFORE the subscriber record is touched, because this refusal is
   * not about this subscriber at all: the peer already belongs to a neighbour
   * sharing the public address, and binding it here would make two customers'
   * tunnels indistinguishable on the way back. Counted by the caller, never
   * guessed at.
   */
  bpa_key_esp_pub (&kv, b->ip_pub, remote);
  if (PREDICT_FALSE (clib_bihash_search_16_8 (&bm->shared, &kv, &value) == 0
		     && (u32) value.value != ip_priv))
    {
      *why = BPA_CREATE_ESP_PEER_TAKEN;
      return 0;
    }

  sub = bpa_sub_get (pt, p, pool_index, b);

  if (PREDICT_FALSE (p->max_sessions && sub->n_sessions >= p->max_sessions))
    {
      sub->n_refused_sessions++;
      *why = BPA_CREATE_OVER_SESSIONS;
      bpa_sub_put (pt, pool_index, b->sub_idx, sub);
      return 0;
    }
  if (PREDICT_FALSE (p->max_rate && !bpa_sub_rate_take (sub, p->max_rate, now)))
    {
      sub->n_refused_rate++;
      *why = BPA_CREATE_OVER_RATE;
      bpa_sub_put (pt, pool_index, b->sub_idx, sub);
      return 0;
    }
  if (PREDICT_FALSE (!bpa_session_room (pt)))
    {
      *why = BPA_CREATE_NO_MEMORY;
      bpa_sub_put (pt, pool_index, b->sub_idx, sub);
      return 0;
    }

  pool_get_zero (pt->sessions, s);
  s->ip_priv = ip_priv;
  s->ip_pub = b->ip_pub;
  /*
   * Zero, and it means zero: an ESP flow has no ports, and printing a number
   * here would put a port in "show bpa session" that never travelled. The
   * free path reads blk_idx and port_off only for sessions that took one, so
   * these staying zero is not a latent double free.
   */
  s->port_priv = 0;
  s->port_pub = 0;
  s->proto = BPA_PROTO_ESP;
  s->sub_idx = b->sub_idx;
  s->pool_index = pool_index;
  s->fib_index = fib;
  s->out_fib_index = p->out_fib_index;
  s->remote = remote;
  s->last_heard = now;

  sub->n_sessions++;
  BPA_STAT (sessions, pt, pool_index, 1);
  bpa_sub_seen (pt, pool_index, sub);

  bpa_key_esp (&kv, ip_priv, remote, fib);
  kv.value = s - pt->sessions;
  clib_bihash_add_del_16_8 (&pt->in2out, &kv, 1);

  /*
   * The shared half. It is what the inbound direction reads, on whatever
   * worker the packet happened to land on, and it holds the VRF as well as
   * the address because the reply has to go back into the table the
   * subscriber's traffic came from.
   */
  bpa_key_esp_pub (&kv, b->ip_pub, remote);
  kv.value = bpa_shared_value (ip_priv, fib);
  clib_bihash_add_del_16_8 (&bm->shared, &kv, 1);

  /*
   * Last, because it reads ip_priv, which bpa_set_addr_of() resolved above and
   * which every early return here skips. A session that leaves this function
   * carries a correction; a session that does not leave it has none to carry.
   */
  bpa_session_csum_deltas (s);

  return s;
}

/*
 * The server answered with its own Call ID, which is what the subscriber's
 * outbound GRE packets will carry from now on. Only now can the outbound
 * direction be keyed at all.
 */
/* not a unit: it moves a GRE session out of its half-open
 * state when the peer's Call ID arrives. lab/pptp.sh is where both
 * directions of a call are seen. */
void
bpa_session_gre_established (bpa_per_thread_t *pt, bpa_session_t *s,
			     u16 peer_call_id)
{
  bpa_main_t *bm = &bpa_main;
  clib_bihash_kv_16_8_t kv;

  if (s->gre_state == BPA_GRE_ESTABLISHED)
    {
      if (s->peer_call_id == peer_call_id)
	return;
      /* A second reply naming a different Call ID: drop the stale key rather
       * than leave the table pointing at a call that no longer exists. Both
       * keys, because the shared one is keyed on the same Call ID and would
       * otherwise send an ICMP error about a call that ended to a subscriber
       * who is no longer in it. */
      bpa_key_gre (&kv, s->ip_priv, s->peer_call_id, s->remote,
		   BPA_GRE_KEY_DATA, s->fib_index);
      clib_bihash_add_del_16_8 (&pt->in2out, &kv, 0);
      bpa_gre_unbind (s->ip_pub, s->peer_call_id, s->remote, s->ip_priv,
			  s->fib_index);
    }

  s->peer_call_id = peer_call_id;
  s->gre_state = BPA_GRE_ESTABLISHED;

  bpa_key_gre (&kv, s->ip_priv, peer_call_id, s->remote, BPA_GRE_KEY_DATA,
	       s->fib_index);
  kv.value = s - pt->sessions;
  clib_bihash_add_del_16_8 (&pt->in2out, &kv, 1);

  /*
   * And the shared half, which exists for one packet: an ICMP error about an
   * OUTBOUND GRE packet. That packet quotes the Call ID the server chose, and
   * the key above is on the subscriber's PRIVATE address, which an error
   * arriving from the internet does not carry. bpa.h says why the table is
   * shared rather than per worker.
   *
   * Written here and nowhere earlier because this is the first moment the
   * server's Call ID exists at all - before the reply there is nothing to key
   * on, which is the same reason the data key is written here.
   */
  bpa_key_gre_pub (&kv, s->ip_pub, peer_call_id, s->remote);
  kv.value = bpa_shared_value (s->ip_priv, s->fib_index);
  clib_bihash_add_del_16_8 (&bm->shared, &kv, 1);
}

/*
 * Return quarantined ports whose hold-down has passed.
 *
 * The queue is in time order because every entry gets the same hold-down, so
 * the drain stops at the first entry that is not ready instead of scanning.
 * Budgeted like the expiry walk: a burst of releases must not stall the
 * process node.
 *
 * The head advances rather than the vector being shifted; it is reset when the
 * queue empties, which is the common case between bursts.
 */
/* not a unit: it walks the quarantine list under the worker's
 * clock, returning ports whose wait is over. lab/icmp-tcp.sh is where a
 * quarantined port is watched on a box. */
static u32
bpa_quarantine_drain (bpa_per_thread_t *pt, f64 now, u32 budget)
{
  bpa_quarantine_t *q;
  bpa_sub_t *sub;
  u32 done = 0;
  uword *h;

  while (done < budget && pt->quarantine_head < vec_len (pt->quarantine))
    {
      q = vec_elt_at_index (pt->quarantine, pt->quarantine_head);
      if (q->until > now)
	break;

      /*
       * The subscriber may have gone away while the port waited - its block
       * released, its bitmap reclaimed. Then there is nothing to give back,
       * and that is not an error.
       */
      h = hash_get (pt->sub_by_idx,
		    bpa_sub_key (q->pool_index, q->sub_idx));
      if (h)
	{
	  sub = pool_elt_at_index (pt->subs, h[0]);
	  if (q->blk_idx < vec_len (sub->blocks))
	    bpa_portmap_free (&vec_elt_at_index (sub->blocks, q->blk_idx)->map,
			      q->port_off);
	}
      pt->quarantine_head++;
      done++;
    }

  if (pt->quarantine_head >= vec_len (pt->quarantine))
    {
      vec_reset_length (pt->quarantine);
      pt->quarantine_head = 0;
    }
  return done;
}

/*
 * Hand a port back, after its hold-down.
 *
 * Immediately when the hold-down is zero, so a deployment that does not want
 * it pays nothing - not even the queue.
 */
/* not a unit: it gives a port back to the subscriber's map, or to
 * the quarantine when the protocol asks for one. lab/nat-behaviour.sh reads
 * what a released port does next on a box. */
static void
bpa_port_release (bpa_per_thread_t *pt, u32 pool_index, u32 sub_idx,
		  u8 blk_idx, u16 port_off, f64 now)
{
  bpa_main_t *bm = &bpa_main;
  bpa_quarantine_t *q;
  bpa_sub_t *sub;
  uword *h;

  if (bm->timeouts.port_holddown == 0)
    {
      h = hash_get (pt->sub_by_idx, bpa_sub_key (pool_index, sub_idx));
      if (h)
	{
	  sub = pool_elt_at_index (pt->subs, h[0]);
	  if (blk_idx < vec_len (sub->blocks))
	    bpa_portmap_free (&vec_elt_at_index (sub->blocks, blk_idx)->map,
			      port_off);
	}
      return;
    }

  /*
   * Full queue: release the oldest entry now rather than refuse this one.
   * Withholding ports until the drain catches up exhausts the block, which
   * is a worse failure than the collision the hold-down prevents.
   */
  if (vec_len (pt->quarantine) - pt->quarantine_head >= BPA_QUARANTINE_MAX)
    {
      bpa_quarantine_drain (pt, now + 1e9, 1);
      pt->n_quarantine_early++;
    }

  vec_add2 (pt->quarantine, q, 1);
  q->pool_index = pool_index;
  q->sub_idx = sub_idx;
  q->port_off = port_off;
  q->blk_idx = blk_idx;
  q->until = now + (f64) bm->timeouts.port_holddown;
}

/* not a unit: it removes the session from the worker's bihash and
 * everything that hangs off it. lab/soak.sh ends sessions in bulk on a box. */
void
bpa_session_free (bpa_per_thread_t *pt, bpa_session_t *s, f64 now)
{
  bpa_main_t *bm = &bpa_main;
  clib_bihash_kv_16_8_t kv;
  bpa_pool_t *p;
  bpa_sub_t *sub;
  uword *q;

  /* GRE holds two inbound-table entries under keys of its own shape, and
   * removing them with bpa_key() would silently leave both behind - the
   * session would be gone while the table still pointed at it. */
  if (PREDICT_FALSE (s->proto == BPA_PROTO_GRE))
    {
      bpa_key_gre (&kv, s->ip_priv, s->port_priv, s->remote, BPA_GRE_KEY_ALG,
		   s->fib_index);
      clib_bihash_add_del_16_8 (&pt->in2out, &kv, 0);
      if (s->gre_state == BPA_GRE_ESTABLISHED)
	{
	  bpa_key_gre (&kv, s->ip_priv, s->peer_call_id, s->remote,
		       BPA_GRE_KEY_DATA, s->fib_index);
	  clib_bihash_add_del_16_8 (&pt->in2out, &kv, 0);
	  /* Both halves go together, and the shared one is written only when
	   * the call established - so it exists exactly when this key does. */
	  bpa_gre_unbind (s->ip_pub, s->peer_call_id, s->remote, s->ip_priv,
			  s->fib_index);
	}
    }
  else if (PREDICT_FALSE (s->proto == BPA_PROTO_ESP))
    {
      clib_bihash_kv_16_8_t value;

      bpa_key_esp (&kv, s->ip_priv, s->remote, s->fib_index);
      clib_bihash_add_del_16_8 (&pt->in2out, &kv, 0);

      /*
       * The shared binding goes only if it is still THIS subscriber's. It is
       * the one entry in this plugin that another worker can have replaced
       * between the two halves of a teardown, and deleting a neighbour's
       * binding would black-hole a tunnel that is working.
       */
      bpa_key_esp_pub (&kv, s->ip_pub, s->remote);
      if (clib_bihash_search_16_8 (&bm->shared, &kv, &value) == 0
	  && bpa_shared_is (value.value, s->ip_priv, s->fib_index))
	clib_bihash_add_del_16_8 (&bm->shared, &kv, 0);
    }
  else if (PREDICT_FALSE (s->is_nat64))
    bpa_nat64_session_unkey (pt, s);
  else
    {
      bpa_key_in (&kv, s->ip_priv, s->port_priv, s->proto, s->fib_index);
      clib_bihash_add_del_16_8 (&pt->in2out, &kv, 0);
    }

  /* ESP never took an inbound key of this shape - it has no public port to
   * key one on - and removing one here would be a delete of whatever else
   * happened to sit at (this address, port 0). */
  if (PREDICT_TRUE (s->proto != BPA_PROTO_ESP))
    {
      bpa_key_out (&kv, s->ip_pub, s->port_pub, s->proto);
      clib_bihash_add_del_16_8 (&pt->out2in, &kv, 0);
    }

  q = hash_get (pt->sub_by_idx, bpa_sub_key (s->pool_index, s->sub_idx));
  if (q)
    {
      sub = pool_elt_at_index (pt->subs, q[0]);
      /* Both changes at once: the port goes through the hold-down queue
       * (#12), and it has to name the block it came from (#13), because a
       * subscriber can now hold several and freeing an offset into the wrong
       * bitmap would hand out a port that is still in use.
       *
       * Except a forwarded port, which is configuration rather than an
       * allocation. Handing it back would let the next flow take it while the
       * customer is between two connections, and the forward would then be
       * silently pointed at somebody else's traffic. */
      /* And not an ESP tunnel, which never took a port: releasing offset 0 of
       * its first block would hand out a port that is still in use by
       * somebody else's flow. */
      if (PREDICT_TRUE (!s->is_static && s->proto != BPA_PROTO_ESP))
	bpa_port_release (pt, s->pool_index, s->sub_idx, s->blk_idx,
			  s->port_off, now);
      if (sub->n_sessions)
	{
	  sub->n_sessions--;
	  BPA_STAT (sessions, pt, s->pool_index, -1);
	}
      bpa_sub_seen (pt, s->pool_index, sub);

      /*
       * Last session gone: a block that was ALLOCATED enters hold-down
       * instead of being released right away. Releasing immediately would
       * make a subscriber who pauses for a second churn a release and an
       * allocation, and each of those is a log record.
       *
       * A deterministic pool with a reserve has allocated blocks too - the
       * reserve ones - and they have to come back the same way. Missing this
       * leaks a reserve block per subscriber that ever extended: owned
       * forever, never reaped, and visible only as a reserve that slowly
       * stops having anything to give.
       */
      if (sub->n_sessions == 0
	  && !pool_is_free_index (bm->pools, s->pool_index))
	{
	  p = pool_elt_at_index (bm->pools, s->pool_index);
	  if ((p->mode == BPA_MODE_DYNAMIC || p->block_reserve)
	      && s->pool_index < vec_len (pt->dyn))
	    bpa_dyn_idle (vec_elt_at_index (pt->dyn, s->pool_index),
			  s->sub_idx, now);
	}

      bpa_sub_put (pt, s->pool_index, s->sub_idx, sub);
    }

  pool_put (pt->sessions, s);
}

/*
 * The session a configured port forward describes.
 *
 * Almost bpa_session_create(), and deliberately not it: that one asks the
 * allocator for a port, and here the port is already decided and already
 * reserved. Everything else - the subscriber record, the block, both hash
 * insertions - is the same, which is why bpa_session_bind() exists.
 */
/* not a unit: it opens the session a static forward needs, on
 * the worker that owns the block, which it finds by asking bpa_forward().
 * lab/forward.sh sends the first inbound packet of a forward on a box. */
bpa_session_t *
bpa_session_static_open (bpa_per_thread_t *pt, const bpa_pool_t *p,
			 u32 pool_index, const bpa_static_t *sf, f64 now)
{
  bpa_session_t *s;
  bpa_sub_t *sub;
  bpa_subblock_t *sb = 0;
  bpa_block_t blk;
  u32 blk_idx;

  /* The forward was validated against this arithmetic when it was
   * configured. If it disagrees now the pool has been redefined underneath
   * it, and translating on a stale mapping is worse than not translating. */
  if (PREDICT_FALSE (bpa_forward (p, sf->ip_priv, &blk)))
    return 0;

  sub = bpa_sub_get (pt, p, pool_index, &blk);

  for (blk_idx = 0; blk_idx < vec_len (sub->blocks); blk_idx++)
    {
      bpa_subblock_t *c = vec_elt_at_index (sub->blocks, blk_idx);

      if (c->blk.ip_pub == sf->ip_pub && sf->port_pub >= c->blk.port_lo
	  && sf->port_pub <= c->blk.port_hi)
	{
	  sb = c;
	  break;
	}
    }
  if (PREDICT_FALSE (sb == 0))
    {
      bpa_sub_put (pt, pool_index, blk.sub_idx, sub);
      return 0;
    }

  /* Reserved at block creation, so this normally reports "already taken".
   * It is called anyway for the one case it is not: a block created before
   * this forward existed, on a box that was already carrying traffic. */
  bpa_portmap_reserve (&sb->map, sf->port_off);

  s = bpa_session_bind (pt, p, pool_index, sub, sb, blk_idx, sf->port_off,
			sf->port_priv, sf->proto,
			(p->fib_index == ~0u) ? 0 : p->fib_index, now);
  if (PREDICT_FALSE (s == 0))
    {
      bpa_sub_put (pt, pool_index, blk.sub_idx, sub);
      return 0;
    }

  s->is_static = 1;
  return s;
}

/*
 * Drops queued hold-downs for one port.
 *
 * A port freed a moment ago is in the quarantine, and the drain will clear its
 * bit when the hold-down expires. If a forward has since reserved that bit,
 * the drain would hand the operator's forwarded port to the next flow, seconds
 * after it was configured and with nothing to see. Cancelled by making the
 * entry name a pool index no subscriber can be found under, which the drain
 * already treats as "the subscriber went away".
 */
/* not a unit: it takes a port back out of that list when the
 * same subscriber asks for it again. lab/icmp-tcp.sh drives it. */
static void
bpa_quarantine_cancel (bpa_per_thread_t *pt, u32 pool_index, u32 sub_idx,
		       u8 blk_idx, u16 port_off)
{
  u32 i;

  for (i = pt->quarantine_head; i < vec_len (pt->quarantine); i++)
    {
      bpa_quarantine_t *q = vec_elt_at_index (pt->quarantine, i);

      if (q->pool_index == pool_index && q->sub_idx == sub_idx
	  && q->blk_idx == blk_idx && q->port_off == port_off)
	q->pool_index = ~0u;
    }
}

/* The subscriber's block that contains a forwarded port, or 0. */
/* not a unit: it finds the block a forward pins, in the worker's own
 * state. The arithmetic that decides WHICH block is bpa_pool.h's and is
 * driven by test/test_bpa_pool.c; lab/forward.sh is the box. */
static bpa_subblock_t *
bpa_static_block (bpa_per_thread_t *pt, u32 pool_index, u32 sub_idx,
		  const bpa_static_t *sf, u32 *blk_idx)
{
  bpa_sub_t *sub;
  uword *h;
  u32 i;

  h = hash_get (pt->sub_by_idx, bpa_sub_key (pool_index, sub_idx));
  if (!h)
    return 0;

  sub = pool_elt_at_index (pt->subs, h[0]);
  for (i = 0; i < vec_len (sub->blocks); i++)
    {
      bpa_subblock_t *sb = vec_elt_at_index (sub->blocks, i);

      if (sb->blk.ip_pub == sf->ip_pub && sf->port_pub >= sb->blk.port_lo
	  && sf->port_pub <= sb->blk.port_hi)
	{
	  *blk_idx = i;
	  return sb;
	}
    }
  return 0;
}

/* Frees the session under `kv`, if there is one. */
/* not a unit: the same by key, for the paths that have one and
 * not the record. lab/datapath.sh drives them. */
static void
bpa_session_free_key (bpa_per_thread_t *pt, clib_bihash_16_8_t *h,
		      clib_bihash_kv_16_8_t *kv, f64 now)
{
  clib_bihash_kv_16_8_t value;

  if (clib_bihash_search_16_8 (h, kv, &value) == 0)
    bpa_session_free (pt, pool_elt_at_index (pt->sessions, value.value), now);
}

/*
 * A forward configured on a pool that is already carrying traffic.
 *
 * Two mappings can be standing in its way, and both lose: whatever holds the
 * public port, because the operator has just said who that port belongs to,
 * and whatever the subscriber's own socket is mapped to, because after this it
 * must be mapped to the forwarded port and one socket cannot have two. Each is
 * a single connection, dropped at the moment an operator configures a forward
 * for that customer.
 *
 * Doing nothing instead was the alternative, and it fails silently: the
 * forward is accepted, reported by "show", and does not work until whatever
 * held the port happens to expire.
 */
/* not a unit: it puts a forward's port into the subscriber's map
 * and its session into the bihash, under the barrier. lab/forward.sh drives
 * the whole thing. */
void
bpa_static_install (u32 pool_index, const bpa_static_t *sf)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p = pool_elt_at_index (bm->pools, pool_index);
  f64 now = vlib_time_now (bm->vlib_main);
  clib_bihash_kv_16_8_t kv;
  bpa_block_t blk;
  u32 i;
  /* A forward is configured against a pool, not observed on a packet, so the
   * VRF in its private-side key is the pool's. A pool that serves every VRF
   * keys at 0, which is what its subscribers' traffic keys at on a box with
   * one - and on a box with several, such a pool is the configuration the
   * overlap check tells the operator not to have. */
  u32 key_fib = (p->fib_index == ~0u) ? 0 : p->fib_index;

  if (bpa_forward (p, sf->ip_priv, &blk))
    return;

  for (i = 0; i < vec_len (bm->per_thread); i++)
    {
      bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, i);
      bpa_subblock_t *sb;
      u32 blk_idx;

      if (!pt->initialised)
	continue;

      bpa_key_out (&kv, sf->ip_pub, sf->port_pub, sf->proto);
      bpa_session_free_key (pt, &pt->out2in, &kv, now);
      bpa_key_in (&kv, sf->ip_priv, sf->port_priv, sf->proto, key_fib);
      bpa_session_free_key (pt, &pt->in2out, &kv, now);

      sb = bpa_static_block (pt, pool_index, blk.sub_idx, sf, &blk_idx);
      if (!sb)
	continue;

      /* Order matters: the frees above may have queued this very port for
       * hold-down, and the reservation has to outlive the drain. */
      bpa_quarantine_cancel (pt, pool_index, blk.sub_idx, (u8) blk_idx,
			     sf->port_off);
      bpa_portmap_reserve (&sb->map, sf->port_off);
    }
}

/* The mirror: the port goes back to the allocator it was withheld from. */
/* not a unit: the same in reverse, and it is where a removed
 * forward gives its port back. lab/forward.sh removes one on a box. */
void
bpa_static_uninstall (u32 pool_index, const bpa_static_t *sf)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p = pool_elt_at_index (bm->pools, pool_index);
  f64 now = vlib_time_now (bm->vlib_main);
  clib_bihash_kv_16_8_t kv;
  bpa_block_t blk;
  u32 i;

  if (bpa_forward (p, sf->ip_priv, &blk))
    return;

  for (i = 0; i < vec_len (bm->per_thread); i++)
    {
      bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, i);
      bpa_subblock_t *sb;
      u32 blk_idx;

      if (!pt->initialised)
	continue;

      /* Freed first: a static session does not release its port, so the
       * bitmap is cleared here and not by the free. */
      bpa_key_out (&kv, sf->ip_pub, sf->port_pub, sf->proto);
      bpa_session_free_key (pt, &pt->out2in, &kv, now);

      sb = bpa_static_block (pt, pool_index, blk.sub_idx, sf, &blk_idx);
      if (sb)
	bpa_portmap_free (&sb->map, sf->port_off);
    }
}

/*
 * Budgeted drain of one pool's sessions on one worker's table.
 *
 * The same shape as bpa_expire_walk() below and for the same reason: the walk
 * runs under the worker barrier, so it has to be able to stop. It differs in
 * where it stops - expiry sweeps forever and resumes anywhere, this one has to
 * cover the whole table exactly once and say when it has, because the pool's
 * definition may not be freed until nothing is left pointing at it.
 *
 * Once round is enough because bpa_session_bind() refuses a pool marked
 * deleted: nothing can appear behind the cursor. `*cursor` carries the walk
 * across barrier acquisitions; pool_put() does not move the entries that
 * remain, so an index that was valid before a free is still that entry after
 * it.
 *
 * Returns non-zero when this thread has nothing of the pool left.
 */
/* not a unit: it walks every session of a pool being deleted, on every
 * worker, under the barrier. lab/pool-del.sh deletes a pool under traffic. */
int
bpa_pool_drain (bpa_per_thread_t *pt, u32 pool_index, u32 *cursor,
		u32 scan_budget, u32 free_budget, f64 now)
{
  u32 cap = vec_len (pt->sessions);
  u32 scanned = 0, freed = 0;

  while (*cursor < cap && scanned < scan_budget && freed < free_budget)
    {
      if (!pool_is_free_index (pt->sessions, *cursor))
	{
	  bpa_session_t *s = pool_elt_at_index (pt->sessions, *cursor);

	  if (s->pool_index == pool_index)
	    {
	      bpa_session_free (pt, s, now);
	      freed++;
	    }
	}
      (*cursor)++;
      scanned++;
    }

  return *cursor >= cap;
}

/*
 * Budgeted expiry walk. Called from the process node so a large table
 * never stalls a worker: it resumes where it stopped.
 */
/* not a unit: the worker's own expiry walk over VPP's session pool, under
 * the barrier its process node holds. When a session has expired is
 * bpa_is_expired() over bpa_timeout_for(), both driven by
 * test/test_bpa_keys.c; lab/soak.sh runs long enough for the walk to matter
 * and lab/datapath.sh reads a session that expired. */
u32
bpa_expire_walk (bpa_per_thread_t *pt, const bpa_timeouts_t *t, f64 now,
		 u32 budget)
{
  bpa_main_t *bm = &bpa_main;
  u32 checked = 0, freed = 0;
  u32 cursor = pt->expire_cursor;
  u32 cap = vec_len (pt->sessions);

  if (cap == 0)
    {
      pt->expire_cursor = 0;
      return 0;
    }

  while (checked < budget && checked < cap)
    {
      if (cursor >= cap)
	cursor = 0;

      if (!pool_is_free_index (pt->sessions, cursor))
	{
	  bpa_session_t *s = pool_elt_at_index (pt->sessions, cursor);
	  /* The pool is the unit of policy, so the lifetime comes from the
	   * session's own pool with the global as the fallback. A pool that
	   * was deleted while its sessions were still open falls back
	   * entirely, which expires them rather than stranding them. */
	  bpa_timeouts_t et = *t;
	  if (!pool_is_free_index (bm->pools, s->pool_index))
	    {
	      bpa_pool_t *sp = pool_elt_at_index (bm->pools, s->pool_index);
	      et = bpa_timeouts_resolve (&sp->timeouts, t);
	    }
	  if (bpa_is_expired (now, s->last_heard,
			      bpa_session_timeout (s, &et)))
	    {
	      bpa_session_free (pt, s, now);
	      freed++;
	    }
	}
      cursor++;
      checked++;
    }

  pt->expire_cursor = cursor;
  pt->n_expired += freed;

  /* Ports come back on the same pass that frees sessions: a separate timer
   * would be one more thing to get wrong, and the budget already bounds it. */
  bpa_quarantine_drain (pt, now, budget);
  return freed;
}

/*
 * Expiry process.
 *
 * Session state is per-worker and freeing an entry races with the worker
 * reading it, so the walk runs under the worker barrier. The budget keeps the
 * WALK short: at 10k sessions that part is held for microseconds.
 *
 * The walk is not the whole barrier, and the sentence above used to be read as
 * though it were. Three other things run inside the same acquisition and
 * BPA_EXPIRE_BUDGET bounds none of them:
 *
 *   - the per-pool bpa_dyn_reap() loop below, up to ARRAY_LEN(rel) releases
 *     per pool per thread, each one emitting an IPFIX record from here;
 *   - bpa_blocks_log_all(), in the drained-but-not-stopped case, which walks
 *     every block every pool is holding and carries no budget at all;
 *   - bpa_log_drain(), which encodes and dispatches everything every thread
 *     queued since the last pass, capped per thread by BPA_LOG_QUEUE_MAX
 *     (8192) and not by the expiry budget.
 *
 * So on a dynamic-mode box with logging enabled the held interval is set by
 * the logging load and the pool count rather than by the session table. This
 * is written where the code is because the open half of #172 is the
 * held-barrier interval measured under control-plane load on real hardware,
 * and it cannot be taken on a virtio guest. Whoever takes it needs to know
 * which knob moves the number before they choose what to vary, and reading it
 * back out of this function afterwards is how the first attempt gets spent.
 *
 * The phase-2 version moves expiry inline into each worker's node, which
 * removes the barrier entirely. That only pays off once the table is large,
 * so it is deliberately not the first implementation.
 */
#define BPA_EXPIRE_INTERVAL 1.0
#define BPA_EXPIRE_BUDGET   10000

/*
 * Every session of one subscriber, ended at once (#558, phase 5): `bpa clear
 * subscriber`, which the VTY's `clear cgnat subscriber` and the API's Clear
 * run. The caller holds the worker barrier, as for the expiry walk. The
 * block is left to the path an idle one takes: with its last session gone
 * it is held down and then released by the expiry process, with its natEvent
 * 17 - so the log says what an expiry would, and a subscriber whose block
 * had to change still waits out the hold-down another subscriber's flows
 * were promised.
 */
/* not a unit: it frees sessions out of a worker's tables, which need the
 * plugin's state; lab/api-grpc.sh clears a subscriber and reads it gone. */
u32
bpa_subscriber_clear (bpa_per_thread_t *pt, u32 ip_priv, f64 now)
{
  u32 i, freed = 0;

  for (i = 0; i < vec_len (pt->sessions); i++)
    {
      bpa_session_t *s;

      if (pool_is_free_index (pt->sessions, i))
	continue;
      s = pool_elt_at_index (pt->sessions, i);
      if (s->ip_priv != ip_priv)
	continue;
      bpa_session_free (pt, s, now);
      freed++;
    }
  return freed;
}

/* not a unit: a VPP process node; it needs the graph and its clock. */
static uword
bpa_expire_process (vlib_main_t *vm, vlib_node_runtime_t *rt, vlib_frame_t *f)
{
  bpa_main_t *bm = &bpa_main;

  while (1)
    {
      f64 now;
      u32 i, freed = 0;

      vlib_process_wait_for_event_or_clock (vm, BPA_EXPIRE_INTERVAL);
      vlib_process_get_events (vm, 0);

      /* The logging transports' counters, into the statistics segment
       * (#504): a handful of assignments, on the thread that writes them. */
      bpa_stats_publish ();

      if (vec_len (bm->per_thread) == 0)
	continue;

      now = vlib_time_now (vm);

      /* An MTU can change under a running box, and a pool clamping to "auto"
       * would then be clamping to a number that describes the path it used to
       * be on. One loop over a handful of interfaces, once an interval. */
      bpa_auto_mss_update ();

      vlib_worker_thread_barrier_sync (vm);
      for (i = 0; i < vec_len (bm->per_thread); i++)
	{
	  bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, i);
	  u32 j;

	  freed += bpa_expire_walk (pt, &bm->timeouts, now, BPA_EXPIRE_BUDGET);

	  /* blocks whose hold-down ran out go back to the free list, and
	   * each release is an RFC 8158 natEvent 17 */
	  for (j = 0; j < vec_len (pt->dyn); j++)
	    {
	      bpa_dyn_release_t rel[64];
	      u32 nrel, k, w, nw;
	      bpa_pool_t *p;

	      if (pool_is_free_index (bm->pools, j))
		continue;
	      p = pool_elt_at_index (bm->pools, j);

	      bpa_worker_id (bm, i, &w, &nw);
	      nrel = bpa_dyn_reap (vec_elt_at_index (pt->dyn, j), now,
				   bm->block_holddown, w, nw, rel,
				   ARRAY_LEN (rel));

	      for (k = 0; k < nrel; k++)
		{
		  bpa_block_t blk;
		  u32 ip_priv = 0;

		  if (bpa_set_addr_of (&p->inside, rel[k].sub_idx, &ip_priv))
		    continue;
		  if (bpa_dyn_addr (vec_elt_at_index (pt->dyn, j), p,
				    rel[k].block_idx, &blk))
		    continue;

		  /* runs on the main thread, so the IPFIX buffer is that
		   * thread's. One call, not one per transport: this site used
		   * to name UDP and TCP itself, which is how a third exporter
		   * gets added in one place and forgotten in the other - and
		   * how it came to hand vlib_time_now() to a record. */
		  {
		    /*
		     * WHAT THE ALLOCATION SAID, repeated here (#397). This
		     * used to be the identity as it is NOW, on the grounds
		     * that a drain is not the instant of an allocation and
		     * there was no earlier reading to prefer - which was true
		     * until the block started keeping one.
		     *
		     * The fallback is that same lookup, for a block allocated
		     * before this version or by a subscriber the table never
		     * had: `recall` leaves the buffer alone when it has
		     * nothing, so the order here is lookup first, recall
		     * over it.
		     */
		    char who[BPA_IDENTITY_MAX];

		    bpa_identity_lookup (&bm->identity, ip_priv, who,
					 sizeof (who));
		    bpa_block_ident_recall (
		      &pt->block_ident,
		      bpa_block_ident_key (j, blk.ip_pub, blk.port_lo), who,
		      sizeof (who));
		    bpa_log_emit (vm, BPA_NAT_EVENT_BLOCK_DEALLOC, ip_priv,
				  blk.ip_pub, blk.port_lo, blk.port_hi, j,
				  who);
		  }
		}
	    }
	}
      /*
       * AND BLOCKS MOVE BETWEEN SLICES (#210), after the reap and under the
       * same barrier: a subscriber refused while blocks sit free on another
       * worker is the defect, and the counters this reads are only current
       * once the hold-down releases above are back on their free lists.
       */
      bpa_rebalance_tick (bm);

      /*
       * A drain that was not followed by a stop.
       *
       * "bpa stop-drain" says every block has been given back, and it is only
       * true because the box is about to stop. If the box is still here, the
       * log is now claiming that blocks this box is holding are free, which is
       * the same lie as an allocation that is never closed, inverted - and it
       * would stand for as long as the log is kept. So the blocks are taken
       * back, with a real natEvent 16, and the log reads "released at T1, held
       * again from T2". The window between the two is a few seconds in which
       * the log is wrong, and bounded; leaving it alone is unbounded.
       *
       * Under the same barrier as everything else here, because
       * bpa_blocks_log_all() reaches into every thread's allocator.
       */
      if (bm->drained_at && now - bm->drained_at > BPA_DRAIN_REANNOUNCE)
	{
	  u32 back = bpa_blocks_log_all (BPA_NAT_EVENT_BLOCK_ALLOC);

	  bm->drained_at = 0;
	  if (back)
	    vlib_log_notice (bm->log_class,
			     "drained but not stopped: %u block(s) are still "
			     "held and have been announced again",
			     back);
	}

      /* the dataplane only queues; this is where the records go out */
      bpa_log_drain (vm);

      vlib_worker_thread_barrier_release (vm);
    }

  return 0;
}

VLIB_REGISTER_NODE (bpa_expire_node) = {
  .function = bpa_expire_process,
  .type = VLIB_NODE_TYPE_PROCESS,
  .name = "bpa-expire-process",
};

/*
 * not a unit: a VLIB process loop, which exists only inside a running VPP;
 * what it calls is bpa_log_stream_pending() and bpa_log_stream_service(),
 * which test/test_bpa_log_tcp.c drives, and lab/syslog-stall-heap.sh measures
 * the backlog draining through it.
 *
 * The TCP log streams, on their own clock (#547): every millisecond while one
 * has something to send, once a second when neither has. On the expiry
 * process's one-second clock a stream sent one socket buffer a second. Each
 * pass sends until the socket would block and reads at most a few 1 MB chunks
 * of backlog: bounded work on the main thread, and none on a worker.
 *
 * Throughput from the cadence and not from a bigger socket buffer: a 4 MB
 * SO_SNDBUF doubled the drain in lab/syslog-stall-heap.sh, and put that much
 * more in the kernel of a connection a stopped box abandons - which
 * lab/ipfix-outbuf.sh caught as a release that reached neither the stop nor
 * the replay. What the kernel took is out of the spill's reach.
 */
static uword
bpa_log_process (vlib_main_t *vm, vlib_node_runtime_t *rt, vlib_frame_t *f)
{
  while (1)
    {
      int owns = bpa_log_logger_owns ();
      int busy = !owns && (bpa_log_stream_pending (&bpa_log_tcp)
			   || bpa_log_stream_pending (&bpa_log_syslog));

      vlib_process_wait_for_event_or_clock (vm, busy ? 0.001 : 0.25);
      vlib_process_get_events (vm, 0);
      /* Who carries the TCP streams, decided again every pass (#550): the
       * log container while its heartbeat is fresh, VPP otherwise. */
      bpa_log_handover ();
      if (bpa_log_logger_owns ())
	continue;
      bpa_log_tcp_service (vlib_time_now (vm));
      bpa_log_stream_service (&bpa_log_syslog, vlib_time_now (vm));
    }
  return 0;
}

VLIB_REGISTER_NODE (bpa_log_process_node) = {
  .function = bpa_log_process,
  .type = VLIB_NODE_TYPE_PROCESS,
  .name = "bpa-log-process",
};
