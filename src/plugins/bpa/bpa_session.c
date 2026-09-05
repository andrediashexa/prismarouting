/* bpa_session.c - per-worker session table. */

#include "bpa.h"
#include "bpa_log.h"

/*
 * Per-worker table sizing. Sessions are sharded by subscriber, so each
 * worker holds roughly total_sessions / n_workers entries.
 *
 * O tamanho e RESERVA de espaco virtual, nao consumo imediato - mas o
 * matador de OOM conta o virtual. Com 64 MB por tabela, duas tabelas e
 * varias threads, o VPP mapeava mais de 1 GB e era morto numa caixa de 2 GB.
 * O padrao ficou modesto e configuravel: quem tem 200k assinantes aumenta
 * pelo startup.conf, quem tem uma caixa pequena nao paga por isso.
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

/* Returns the subscriber's port bitmap, creating it on first use. */
static bpa_sub_t *
bpa_sub_get (bpa_per_thread_t *pt, const bpa_pool_t *p, u32 sub_idx)
{
  bpa_sub_t *sub;
  uword *q;
  u64 *bits;
  u32 n_words;

  q = hash_get (pt->sub_by_idx, sub_idx);
  if (q)
    return pool_elt_at_index (pt->subs, q[0]);

  n_words = (p->block_size + 63) / 64;
  bits = clib_mem_alloc (n_words * sizeof (u64));

  pool_get_zero (pt->subs, sub);
  bpa_portmap_init (&sub->map, p->block_size, bits);
  hash_set (pt->sub_by_idx, sub_idx, sub - pt->subs);
  return sub;
}

static void
bpa_sub_put (bpa_per_thread_t *pt, u32 sub_idx, bpa_sub_t *sub)
{
  if (sub->n_sessions)
    return;

  clib_mem_free (sub->map.bits);
  hash_unset (pt->sub_by_idx, sub_idx);
  pool_put (pt->subs, sub);
}

bpa_session_t *
bpa_session_create (bpa_per_thread_t *pt, const bpa_pool_t *p, u32 pool_index,
		    const bpa_block_t *b, u16 port_priv, u8 proto, f64 now)
{
  clib_bihash_kv_16_8_t kv;
  bpa_session_t *s;
  bpa_sub_t *sub;
  int off, preferred = -1;

  sub = bpa_sub_get (pt, p, b->sub_idx);

  /* Port preservation: keep the original source port when it happens to
   * fall inside this subscriber's block and is still free. */
  if (port_priv >= b->port_lo && port_priv <= b->port_hi)
    preferred = port_priv - b->port_lo;

  off = bpa_portmap_alloc (&sub->map, preferred);
  if (off < 0)
    {
      bpa_sub_put (pt, b->sub_idx, sub);
      return 0; /* block exhausted */
    }

  pool_get_zero (pt->sessions, s);
  s->ip_pub = b->ip_pub;
  s->port_priv = port_priv;
  s->port_off = (u16) off;
  s->port_pub = b->port_lo + (u16) off;
  s->proto = proto;
  s->sub_idx = b->sub_idx;
  s->pool_index = pool_index;
  s->last_heard = now;

  if (bpa_set_addr_of (&p->inside, b->sub_idx, &s->ip_priv))
    {
      bpa_portmap_free (&sub->map, (u16) off);
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

void
bpa_session_free (bpa_per_thread_t *pt, bpa_session_t *s, f64 now)
{
  bpa_main_t *bm = &bpa_main;
  clib_bihash_kv_16_8_t kv;
  bpa_pool_t *p;
  bpa_sub_t *sub;
  uword *q;

  bpa_key (&kv, s->ip_priv, s->port_priv, s->proto);
  clib_bihash_add_del_16_8 (&pt->in2out, &kv, 0);

  bpa_key (&kv, s->ip_pub, s->port_pub, s->proto);
  clib_bihash_add_del_16_8 (&pt->out2in, &kv, 0);

  q = hash_get (pt->sub_by_idx, s->sub_idx);
  if (q)
    {
      sub = pool_elt_at_index (pt->subs, q[0]);
      bpa_portmap_free (&sub->map, s->port_off);
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
	  if (now - s->last_heard > (f64) bpa_session_timeout (s, t))
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

		  /* roda na main thread, entao o buffer IPFIX e o dela */
		  bpa_log_block_event (vm, vm->thread_index,
				       BPA_NAT_EVENT_BLOCK_DEALLOC, ip_priv,
				       blk.ip_pub, blk.port_lo, blk.port_hi, j);
		  bpa_log_tcp_record (now, BPA_NAT_EVENT_BLOCK_DEALLOC,
				      ip_priv, blk.ip_pub, blk.port_lo,
				      blk.port_hi, j);
		}
	    }
	}
      /* o dataplane so enfileira; e daqui que os registros saem */
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
