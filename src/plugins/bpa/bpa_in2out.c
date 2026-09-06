/*
 * bpa_in2out.c - subscriber -> internet translation.
 *
 * Runs on the ip4-unicast feature arc of the inside interfaces.
 */

#include <vnet/ip/icmp46_packet.h>
#include <vnet/udp/udp_packet.h>
#include <vnet/tcp/tcp_packet.h>

#include "bpa.h"
#include "bpa_l4.h"
#include "bpa_log.h"

typedef struct
{
  u32 ip_priv, ip_pub;
  u16 port_priv, port_pub;
  u8 proto;
  u8 created;
  u8 is_frag;
  u16 reass_port;
} bpa_in2out_trace_t;

static u8 *
format_bpa_in2out_trace (u8 *s, va_list *args)
{
  CLIB_UNUSED (vlib_main_t * vm) = va_arg (*args, vlib_main_t *);
  CLIB_UNUSED (vlib_node_t * node) = va_arg (*args, vlib_node_t *);
  bpa_in2out_trace_t *t = va_arg (*args, bpa_in2out_trace_t *);
  ip4_address_t priv, pub;

  priv.as_u32 = clib_host_to_net_u32 (t->ip_priv);
  pub.as_u32 = clib_host_to_net_u32 (t->ip_pub);

  return format (s, "BPA in2out %U:%u -> %U:%u proto %U%s%s",
		 format_ip4_address, &priv, t->port_priv, format_ip4_address,
		 &pub, t->port_pub, format_bpa_proto, t->proto,
		 t->created ? " (new session)" : "",
		 t->is_frag ? " [non-first fragment]" : "");
}

#define foreach_bpa_in2out_error                                              \
  _ (TRANSLATED, "packets translated")                                        \
  _ (SESSION_NEW, "sessions created")                                          \
  _ (NO_POOL, "no pool for source address")                           \
  _ (UNSUPPORTED, "unsupported protocol")                                  \
  _ (OVER_CAPACITY, "subscriber beyond pool capacity")                  \
  _ (BLOCK_EXHAUSTED, "port block exhausted")                             \
  _ (NO_BLOCK, "no free block in dynamic pool")                            \
  _ (TRUNCATED, "truncated packet")

typedef enum
{
#define _(sym, str) BPA_IN2OUT_ERROR_##sym,
  foreach_bpa_in2out_error
#undef _
    BPA_IN2OUT_N_ERROR,
} bpa_in2out_error_t;

static char *bpa_in2out_error_strings[] = {
#define _(sym, str) str,
  foreach_bpa_in2out_error
#undef _
};

typedef enum
{
  BPA_IN2OUT_NEXT_DROP,
  BPA_IN2OUT_N_NEXT,
} bpa_in2out_next_t;

static_always_inline void
bpa_rewrite_src (ip4_header_t *ip, void *l4h, u8 proto, u32 new_addr,
		 u16 new_port)
{
  u32 old_addr = clib_net_to_host_u32 (ip->src_address.as_u32);
  ip_csum_t sum;

  if (old_addr != new_addr)
    {
      sum = ip->checksum;
      sum = ip_csum_update (sum, ip->src_address.as_u32,
			    clib_host_to_net_u32 (new_addr), ip4_header_t,
			    src_address);
      ip->checksum = ip_csum_fold (sum);
    }

  /* A non-first fragment: there is no port to rewrite, and the L4 checksum
   * lives in the first fragment. Only the address changes. */
  if (PREDICT_FALSE (l4h == 0))
    {
      ip->src_address.as_u32 = clib_host_to_net_u32 (new_addr);
      return;
    }

  if (proto == BPA_PROTO_ICMP)
    {
      icmp46_header_t *icmp = l4h;
      u16 *id = (u16 *) (icmp + 1);
      sum = icmp->checksum;
      sum = ip_csum_update (sum, id[0], clib_host_to_net_u16 (new_port),
			    icmp_echo_header_t, identifier);
      icmp->checksum = ip_csum_fold (sum);
      id[0] = clib_host_to_net_u16 (new_port);
    }
  else if (proto == BPA_PROTO_TCP)
    {
      tcp_header_t *tcp = l4h;
      sum = tcp->checksum;
      sum = ip_csum_update (sum, ip->src_address.as_u32,
			    clib_host_to_net_u32 (new_addr), ip4_header_t,
			    src_address);
      sum = ip_csum_update (sum, tcp->src_port,
			    clib_host_to_net_u16 (new_port), tcp_header_t,
			    src_port);
      tcp->checksum = ip_csum_fold (sum);
      tcp->src_port = clib_host_to_net_u16 (new_port);
    }
  else
    {
      udp_header_t *udp = l4h;
      /* A zero UDP checksum means "not computed" and must stay zero. */
      if (udp->checksum)
	{
	  sum = udp->checksum;
	  sum = ip_csum_update (sum, ip->src_address.as_u32,
				clib_host_to_net_u32 (new_addr), ip4_header_t,
				src_address);
	  sum = ip_csum_update (sum, udp->src_port,
				clib_host_to_net_u16 (new_port),
				udp_header_t, src_port);
	  udp->checksum = ip_csum_fold (sum);
	}
      udp->src_port = clib_host_to_net_u16 (new_port);
    }

  ip->src_address.as_u32 = clib_host_to_net_u32 (new_addr);
}

VLIB_NODE_FN (bpa_in2out_node)
(vlib_main_t *vm, vlib_node_runtime_t *node, vlib_frame_t *frame)
{
  bpa_main_t *bm = &bpa_main;
  u32 thread_index = vm->thread_index;
  bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, thread_index);
  u32 n_left_from, *from;
  vlib_buffer_t *bufs[VLIB_FRAME_SIZE], **b = bufs;
  u16 nexts[VLIB_FRAME_SIZE], *next = nexts;
  f64 now = vlib_time_now (vm);
  u32 n_translated = 0, n_new = 0;

  from = vlib_frame_vector_args (frame);
  n_left_from = frame->n_vectors;
  vlib_get_buffers (vm, from, bufs, n_left_from);

  while (n_left_from > 0)
    {
      ip4_header_t *ip = vlib_buffer_get_current (b[0]);
      clib_bihash_kv_16_8_t kv, value;
      bpa_session_t *s = 0;
      bpa_pool_t *p;
      bpa_block_t blk;
      void *l4h = 0;
      u32 pool_index = 0, sub_idx = 0, src_host;
      u16 port_priv = 0;
      int proto, created = 0;

      next[0] = BPA_IN2OUT_NEXT_DROP;
      /* captured before any rewrite: the trace must show the pre-NAT source */
      src_host = clib_net_to_host_u32 (ip->src_address.as_u32);

      proto = bpa_proto_of (ip->protocol);
      if (PREDICT_FALSE (proto < 0))
	{
	  b[0]->error = node->errors[BPA_IN2OUT_ERROR_UNSUPPORTED];
	  goto trace;
	}

      if (PREDICT_FALSE (bpa_l4_src (b[0], ip, proto, &port_priv, &l4h)))
	{
	  b[0]->error = node->errors[BPA_IN2OUT_ERROR_TRUNCATED];
	  goto trace;
	}

      bpa_key (&kv, src_host, port_priv, (u8) proto);
      if (clib_bihash_search_16_8 (&pt->in2out, &kv, &value) == 0)
	{
	  s = pool_elt_at_index (pt->sessions, value.value);
	  goto translate;
	}

      if (PREDICT_FALSE (
	    bpa_pool_by_inside (bm, src_host, &pool_index, &sub_idx)))
	{
	  b[0]->error = node->errors[BPA_IN2OUT_ERROR_NO_POOL];
	  goto trace;
	}

      p = pool_elt_at_index (bm->pools, pool_index);

      if (PREDICT_TRUE (p->mode == BPA_MODE_DETERMINISTIC))
	{
	  if (PREDICT_FALSE (bpa_forward (p, src_host, &blk)))
	    {
	      b[0]->error = node->errors[BPA_IN2OUT_ERROR_OVER_CAPACITY];
	      goto trace;
	    }
	}
      else
	{
	  u32 w, nw;
	  int is_new = 0;

	  bpa_worker_id (bm, thread_index, &w, &nw);
	  if (PREDICT_FALSE (
		pool_index >= vec_len (pt->dyn)
		|| bpa_dyn_acquire (vec_elt_at_index (pt->dyn, pool_index), p,
				    sub_idx, w, nw, &blk, &is_new)))
	    {
	      /* RFC 8158 natEvent 12: beyond compliance, this is the alarm that
	       * gets ahead of the support call. */
	      bpa_log_queue_event (pt, BPA_NAT_EVENT_PORTS_EXHAUSTED, src_host,
				   0, 0, 0, pool_index);
	      b[0]->error = node->errors[BPA_IN2OUT_ERROR_NO_BLOCK];
	      goto trace;
	    }

	  if (is_new)
	    bpa_log_queue_event (pt, BPA_NAT_EVENT_BLOCK_ALLOC, src_host,
				 blk.ip_pub, blk.port_lo, blk.port_hi,
				 pool_index);
	}

      s = bpa_session_create (pt, p, pool_index, &blk, port_priv, (u8) proto,
			      now);

      /*
       * Every block this subscriber holds is full. Give it another, if the
       * pool allows more and there is one to give.
       *
       * Only in dynamic mode. A deterministic pool has no spare block by
       * construction - the formula assigns every block to some subscriber, so
       * any block handed out here is one taken from somebody else, and their
       * mapping would break with no error anywhere. That needs a reserve
       * carved out of capacity, which changes the mapping for everyone and is
       * therefore its own change (issue #39).
       */
      if (PREDICT_FALSE (s == 0 && p->mode == BPA_MODE_DYNAMIC
			 && bpa_sub_n_blocks (pt, sub_idx) < p->max_blocks))
	{
	  u32 w, nw;
	  int is_new = 0;
	  bpa_block_t ext;

	  bpa_worker_id (bm, thread_index, &w, &nw);
	  if (pool_index < vec_len (pt->dyn)
	      && bpa_dyn_acquire_extra (vec_elt_at_index (pt->dyn, pool_index),
					p, sub_idx, w, nw, &ext, &is_new) == 0)
	    {
	      bpa_sub_extend (pt, p, sub_idx, &ext);
	      /* An extension is not derivable from anything, in either mode.
	       * A block the log does not record cannot be attributed to
	       * anyone, which is the whole point of recording blocks. */
	      bpa_log_queue_event (pt, BPA_NAT_EVENT_BLOCK_ALLOC, src_host,
				   ext.ip_pub, ext.port_lo, ext.port_hi,
				   pool_index);
	      s = bpa_session_create (pt, p, pool_index, &ext, port_priv,
				      (u8) proto, now);
	    }
	}

      if (PREDICT_FALSE (s == 0))
	{
	  b[0]->error = node->errors[BPA_IN2OUT_ERROR_BLOCK_EXHAUSTED];
	  goto trace;
	}
      created = 1;
      n_new++;

    translate:
      s->last_heard = now;
      if (proto == BPA_PROTO_TCP)
	bpa_tcp_track (s, ((tcp_header_t *) l4h)->flags);

      bpa_rewrite_src (ip, l4h, (u8) proto, s->ip_pub, s->port_pub);
      n_translated++;
      vnet_feature_next_u16 (&next[0], b[0]);

    trace:
      if (PREDICT_FALSE (b[0]->flags & VLIB_BUFFER_IS_TRACED))
	{
	  bpa_in2out_trace_t *t =
	    vlib_add_trace (vm, node, b[0], sizeof (*t));
	  t->proto = (u8) (proto < 0 ? 0 : proto);
	  t->created = created;
	  t->ip_priv = src_host;
	  t->port_priv = port_priv;
	  t->ip_pub = s ? s->ip_pub : 0;
	  t->port_pub = s ? s->port_pub : 0;
	  t->is_frag = vnet_buffer (b[0])->ip.reass.is_non_first_fragment;
	  t->reass_port = vnet_buffer (b[0])->ip.reass.l4_src_port;
	}

      b++;
      next++;
      n_left_from--;
    }

  vlib_buffer_enqueue_to_next (vm, node, from, nexts, frame->n_vectors);

  vlib_node_increment_counter (vm, node->node_index,
			       BPA_IN2OUT_ERROR_TRANSLATED, n_translated);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_IN2OUT_ERROR_SESSION_NEW, n_new);
  return frame->n_vectors;
}

VLIB_REGISTER_NODE (bpa_in2out_node) = {
  .name = "bpa-in2out",
  .vector_size = sizeof (u32),
  .format_trace = format_bpa_in2out_trace,
  .type = VLIB_NODE_TYPE_INTERNAL,
  .n_errors = BPA_IN2OUT_N_ERROR,
  .error_strings = bpa_in2out_error_strings,
  .n_next_nodes = BPA_IN2OUT_N_NEXT,
  .next_nodes = {
    [BPA_IN2OUT_NEXT_DROP] = "error-drop",
  },
};

VNET_FEATURE_INIT (bpa_in2out_feature, static) = {
  .arc_name = "ip4-unicast",
  .node_name = "bpa-in2out",
  .runs_before = VNET_FEATURES ("ip4-lookup"),
  /*
   * Has to run AFTER shallow virtual reassembly, otherwise the buffer's port
   * fields do not exist yet and a following fragment is read with a garbage
   * port - which creates a phantom session and burns one port of the block
   * per fragment.
   */
  .runs_after = VNET_FEATURES ("ip4-sv-reassembly-feature"),
};
