/* bpa_session.c - per-worker session table. */

#include "bpa.h"
#include "bpa_log.h"

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

  pt->sub_by_idx = hash_create (0, sizeof (uword));
  pt->initialised = 1;
  return 0;
}

/* Adds one block to a subscriber, with its own bitmap. */
static void
bpa_sub_add_block (bpa_sub_t *sub, const bpa_pool_t *p, const bpa_block_t *b)
{
  u32 n_words = (p->block_size + 63) / 64;
  bpa_subblock_t *sb;

  vec_add2 (sub->blocks, sb, 1);
  sb->blk = *b;
  bpa_portmap_init (&sb->map, p->block_size, clib_mem_alloc (n_words * sizeof (u64)));
}

/* Returns the subscriber's port state, creating it on first use. */
static bpa_sub_t *
bpa_sub_get (bpa_per_thread_t *pt, const bpa_pool_t *p, const bpa_block_t *b)
{
  bpa_sub_t *sub;
  uword *q;

  q = hash_get (pt->sub_by_idx, b->sub_idx);
  if (q)
    return pool_elt_at_index (pt->subs, q[0]);

  pool_get_zero (pt->subs, sub);
  bpa_sub_add_block (sub, p, b);
  hash_set (pt->sub_by_idx, b->sub_idx, sub - pt->subs);
  return sub;
}

static void
bpa_sub_put (bpa_per_thread_t *pt, u32 sub_idx, bpa_sub_t *sub)
{
  bpa_subblock_t *sb;

  if (sub->n_sessions)
    return;

  vec_foreach (sb, sub->blocks)
    clib_mem_free (sb->map.bits);
  vec_free (sub->blocks);
  hash_unset (pt->sub_by_idx, sub_idx);
  pool_put (pt->subs, sub);
}

/*
 * How many blocks this subscriber already holds, and whether another is
 * allowed. Used by the datapath to decide whether to ask for an extension
 * before giving up on the packet.
 */
u32
bpa_sub_n_blocks (bpa_per_thread_t *pt, u32 sub_idx)
{
  uword *q = hash_get (pt->sub_by_idx, sub_idx);
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
void
bpa_sub_extend (bpa_per_thread_t *pt, const bpa_pool_t *p, u32 sub_idx,
		const bpa_block_t *b)
{
  uword *q = hash_get (pt->sub_by_idx, sub_idx);
  if (!q)
    return;
  bpa_sub_add_block (pool_elt_at_index (pt->subs, q[0]), p, b);
}

bpa_session_t *
bpa_session_create (bpa_per_thread_t *pt, const bpa_pool_t *p, u32 pool_index,
		    const bpa_block_t *b, u16 port_priv, u8 proto, f64 now)
{
  clib_bihash_kv_16_8_t kv;
  bpa_session_t *s;
  bpa_sub_t *sub;
  bpa_subblock_t *sb = 0;
  u32 blk_idx;
  int off = -1, preferred;

  sub = bpa_sub_get (pt, p, b);

  /*
   * Try the blocks the subscriber already holds, oldest first. Port
   * preservation only applies to the block whose range contains the original
   * port, which is normally the first.
   */
  for (blk_idx = 0; blk_idx < vec_len (sub->blocks); blk_idx++)
    {
      sb = vec_elt_at_index (sub->blocks, blk_idx);
      preferred = -1;
      if (port_priv >= sb->blk.port_lo && port_priv <= sb->blk.port_hi)
	preferred = port_priv - sb->blk.port_lo;
      off = bpa_portmap_alloc (&sb->map, preferred);
      if (off >= 0)
	break;
    }

  /*
   * Every block full. The caller decides whether an extension is possible -
   * it owns the free list in dynamic mode, and knows there is none in
   * deterministic mode. Here we only report that another block is needed.
   */
  if (off < 0)
    {
      bpa_sub_put (pt, b->sub_idx, sub);
      return 0;
    }

  pool_get_zero (pt->sessions, s);
  s->ip_pub = sb->blk.ip_pub;
  s->port_priv = port_priv;
  s->port_off = (u16) off;
  s->blk_idx = (u8) blk_idx;
  s->port_pub = sb->blk.port_lo + (u16) off;
  s->proto = proto;
  s->sub_idx = b->sub_idx;
  s->pool_index = pool_index;
  s->last_heard = now;

  if (bpa_set_addr_of (&p->inside, b->sub_idx, &s->ip_priv))
    {
      bpa_portmap_free (&sb->map, (u16) off);
      pool_put (pt->sessions, s);
      bpa_sub_put (pt, b->sub_idx, sub);
      return 0;
    }

  sub->n_sessions++;

  bpa_key (&kv, s->ip_priv, s->port_priv, proto);
  kv.value = s - pt->sessions;
  clib_bihash_add_del_16_8 (&pt->in2out, &kv, 1);

  bpa_key (&kv, s->ip_pub, s->port_pub, proto);
  kv.value = s - pt->sessions;
  clib_bihash_add_del_16_8 (&pt->out2in, &kv, 1);

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
bpa_session_t *
bpa_session_create_gre (bpa_per_thread_t *pt, const bpa_pool_t *p,
			u32 pool_index, u32 sub_idx, u32 ip_pub, u16 call_id,
			u32 remote, f64 now)
{
  clib_bihash_kv_16_8_t kv;
  bpa_session_t *s;
  bpa_sub_t *sub;
  bpa_subblock_t *sb = 0;
  u32 blk_idx;
  int off = -1;
  uword *q;

  q = hash_get (pt->sub_by_idx, sub_idx);
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
  s->remote = remote;
  s->last_heard = now;

  if (bpa_set_addr_of (&p->inside, sub_idx, &s->ip_priv))
    {
      bpa_portmap_free (&sb->map, (u16) off);
      pool_put (pt->sessions, s);
      return 0;
    }

  sub->n_sessions++;

  /* Inbound: the server will send GRE carrying the Call ID we just handed
   * out, and it came from the block, so the pre-check and the handoff find
   * the right subscriber without knowing anything about PPTP. */
  bpa_key (&kv, s->ip_pub, s->port_pub, BPA_PROTO_GRE);
  kv.value = s - pt->sessions;
  clib_bihash_add_del_16_8 (&pt->out2in, &kv, 1);

  /* The ALG's own handle on this call, so a retransmitted call request maps
   * to the same Call ID instead of allocating a second one. */
  bpa_key_gre (&kv, s->ip_priv, call_id, remote, BPA_GRE_KEY_ALG);
  kv.value = s - pt->sessions;
  clib_bihash_add_del_16_8 (&pt->in2out, &kv, 1);

  return s;
}

/*
 * The server answered with its own Call ID, which is what the subscriber's
 * outbound GRE packets will carry from now on. Only now can the outbound
 * direction be keyed at all.
 */
void
bpa_session_gre_established (bpa_per_thread_t *pt, bpa_session_t *s,
			     u16 peer_call_id)
{
  clib_bihash_kv_16_8_t kv;

  if (s->gre_state == BPA_GRE_ESTABLISHED)
    {
      if (s->peer_call_id == peer_call_id)
	return;
      /* A second reply naming a different Call ID: drop the stale key rather
       * than leave the table pointing at a call that no longer exists. */
      bpa_key_gre (&kv, s->ip_priv, s->peer_call_id, s->remote,
		   BPA_GRE_KEY_DATA);
      clib_bihash_add_del_16_8 (&pt->in2out, &kv, 0);
    }

  s->peer_call_id = peer_call_id;
  s->gre_state = BPA_GRE_ESTABLISHED;

  bpa_key_gre (&kv, s->ip_priv, peer_call_id, s->remote, BPA_GRE_KEY_DATA);
  kv.value = s - pt->sessions;
  clib_bihash_add_del_16_8 (&pt->in2out, &kv, 1);
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
      h = hash_get (pt->sub_by_idx, q->sub_idx);
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
static void
bpa_port_release (bpa_per_thread_t *pt, u32 sub_idx, u8 blk_idx, u16 port_off,
		  f64 now)
{
  bpa_main_t *bm = &bpa_main;
  bpa_quarantine_t *q;
  bpa_sub_t *sub;
  uword *h;

  if (bm->timeouts.port_holddown == 0)
    {
      h = hash_get (pt->sub_by_idx, sub_idx);
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
  q->sub_idx = sub_idx;
  q->port_off = port_off;
  q->blk_idx = blk_idx;
  q->until = now + (f64) bm->timeouts.port_holddown;
}

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
      bpa_key_gre (&kv, s->ip_priv, s->port_priv, s->remote, BPA_GRE_KEY_ALG);
      clib_bihash_add_del_16_8 (&pt->in2out, &kv, 0);
      if (s->gre_state == BPA_GRE_ESTABLISHED)
	{
	  bpa_key_gre (&kv, s->ip_priv, s->peer_call_id, s->remote,
		       BPA_GRE_KEY_DATA);
	  clib_bihash_add_del_16_8 (&pt->in2out, &kv, 0);
	}
    }
  else
    {
      bpa_key (&kv, s->ip_priv, s->port_priv, s->proto);
      clib_bihash_add_del_16_8 (&pt->in2out, &kv, 0);
    }

  bpa_key (&kv, s->ip_pub, s->port_pub, s->proto);
  clib_bihash_add_del_16_8 (&pt->out2in, &kv, 0);

  q = hash_get (pt->sub_by_idx, s->sub_idx);
  if (q)
    {
      sub = pool_elt_at_index (pt->subs, q[0]);
      /* Both changes at once: the port goes through the hold-down queue
       * (#12), and it has to name the block it came from (#13), because a
       * subscriber can now hold several and freeing an offset into the wrong
       * bitmap would hand out a port that is still in use. */
      bpa_port_release (pt, s->sub_idx, s->blk_idx, s->port_off, now);
      if (sub->n_sessions)
	sub->n_sessions--;

      /*
       * Last session gone: in dynamic mode the block enters hold-down
       * instead of being released right away. Releasing immediately would
       * make a subscriber who pauses for a second churn a release and an
       * allocation, and each of those is a log record.
       */
      if (sub->n_sessions == 0
	  && !pool_is_free_index (bm->pools, s->pool_index))
	{
	  p = pool_elt_at_index (bm->pools, s->pool_index);
	  if (p->mode == BPA_MODE_DYNAMIC
	      && s->pool_index < vec_len (pt->dyn))
	    bpa_dyn_idle (vec_elt_at_index (pt->dyn, s->pool_index),
			  s->sub_idx, now);
	}

      bpa_sub_put (pt, s->sub_idx, sub);
    }

  pool_put (pt->sessions, s);
}

/*
 * Budgeted expiry walk. Called from the process node so a large table
 * never stalls a worker: it resumes where it stopped.
 */
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
	  if (now - s->last_heard > (f64) bpa_session_timeout (s, &et))
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
 * reading it, so the walk runs under the worker barrier. The budget keeps
 * each pass short: at 10k sessions the barrier is held for microseconds.
 *
 * The phase-2 version moves expiry inline into each worker's node, which
 * removes the barrier entirely. That only pays off once the table is large,
 * so it is deliberately not the first implementation.
 */
#define BPA_EXPIRE_INTERVAL 1.0
#define BPA_EXPIRE_BUDGET   10000

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

      if (vec_len (bm->per_thread) == 0)
	continue;

      now = vlib_time_now (vm);

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
		  bpa_dyn_block_addr (p, rel[k].block_idx, &blk);

		  /* runs on the main thread, so the IPFIX buffer is that thread's */
		  bpa_log_block_event (vm, vm->thread_index,
				       BPA_NAT_EVENT_BLOCK_DEALLOC, ip_priv,
				       blk.ip_pub, blk.port_lo, blk.port_hi, j);
		  bpa_log_tcp_record (now, BPA_NAT_EVENT_BLOCK_DEALLOC,
				      ip_priv, blk.ip_pub, blk.port_lo,
				      blk.port_hi, j);
		}
	    }
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
