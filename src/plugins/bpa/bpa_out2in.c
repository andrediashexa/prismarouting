/*
 * bpa_out2in.c - internet -> subscriber translation.
 *
 * Runs on the ip4-unicast feature arc of the outside interfaces.
 *
 * bpa_reverse() recovers the subscriber address from (public ip, port) with
 * pure arithmetic, but not the original source port, so a session lookup is
 * still needed. What the arithmetic gives us is that the lookup is always
 * worker-local: the packet is already on the worker that owns the subscriber.
 */

#include <vnet/ip/icmp46_packet.h>
#include <vnet/udp/udp_packet.h>
#include <vnet/tcp/tcp_packet.h>

#include "bpa.h"
#include "bpa_ftp.h"
#include "bpa_l4.h"
#include "bpa_alg.h"

typedef struct
{
  u32 ip_pub, ip_priv;
  u16 port_pub, port_priv;
  u8 proto;
  u8 matched;
} bpa_out2in_trace_t;

static u8 *
format_bpa_out2in_trace (u8 *s, va_list *args)
{
  CLIB_UNUSED (vlib_main_t * vm) = va_arg (*args, vlib_main_t *);
  CLIB_UNUSED (vlib_node_t * node) = va_arg (*args, vlib_node_t *);
  bpa_out2in_trace_t *t = va_arg (*args, bpa_out2in_trace_t *);
  ip4_address_t pub, priv;

  pub.as_u32 = clib_host_to_net_u32 (t->ip_pub);
  priv.as_u32 = clib_host_to_net_u32 (t->ip_priv);

  if (!t->matched)
    return format (s, "BPA out2in %U:%u proto %U no session",
		   format_ip4_address, &pub, t->port_pub, format_bpa_proto,
		   t->proto);

  return format (s, "BPA out2in %U:%u -> %U:%u proto %U", format_ip4_address,
		 &pub, t->port_pub, format_ip4_address, &priv, t->port_priv,
		 format_bpa_proto, t->proto);
}

#define foreach_bpa_out2in_error                                              \
  _ (TRANSLATED, "packets translated")                                        \
  _ (NOT_OURS, "destination outside public pools")                             \
  _ (NO_SESSION, "no session for destination")                                 \
  _ (FILTERED, "dropped by address-dependent filtering")                       \
  _ (UNSUPPORTED, "unsupported protocol")                                  \
  _ (OUT_OF_BLOCK, "port outside every block")                            \
  _ (BLOCK_FREE, "block not allocated to any subscriber")                      \
  _ (ICMP_ERROR, "ICMP errors translated")                                     \
  _ (ICMP_MALFORMED, "truncated or malformed ICMP error")                      \
  _ (TRUNCATED, "truncated packet")                                            \
  _ (FTP_ACK, "TCP acknowledgements shifted after an FTP rewrite")         \
  /* Kept under the 35 characters "show errors" prints; see bpa_in2out.c. */  \
  _ (RTSP_REWRITTEN, "RTSP Transport replies rewritten")                       \
  _ (RTSP_UNPARSED, "RTSP reply not understood")                               \
  _ (RTSP_PARTIAL, "RTSP reply spans segments")                                \
  _ (RTSP_NO_ROOM, "RTSP reply rewrite would not fit")

typedef enum
{
#define _(sym, str) BPA_OUT2IN_ERROR_##sym,
  foreach_bpa_out2in_error
#undef _
    BPA_OUT2IN_N_ERROR,
} bpa_out2in_error_t;

static char *bpa_out2in_error_strings[] = {
#define _(sym, str) str,
  foreach_bpa_out2in_error
#undef _
};

typedef enum
{
  BPA_OUT2IN_NEXT_DROP,
  BPA_OUT2IN_N_NEXT,
} bpa_out2in_next_t;


/*
 * ICMP errors carry the original packet embedded, and it is the embedded one
 * that says who they are about.
 *
 * Without translating it, PMTUD's "fragmentation needed" never reaches the
 * subscriber: the TCP connection hangs and the symptom that reaches support
 * is "some sites do not open". Traceroute also stops working through the
 * CGNAT.
 */
static_always_inline int
bpa_icmp_is_error (u8 type)
{
  switch (type)
    {
    case ICMP4_destination_unreachable:
    case ICMP4_time_exceeded:
    case ICMP4_parameter_problem:
    case ICMP4_source_quench:
    case ICMP4_redirect:
      return 1;
    default:
      return 0;
    }
}

/*
 * Translates an ICMP error coming from the internet.
 *
 * Structure: outer IP (dst = our public) / ICMP / embedded IP
 * (src = our public, dst = remote) / 8 bytes of the original L4.
 *
 * Rewrites the outer destination and the embedded source. The ICMP checksum
 * covers the whole embedded packet, so every change in there - including the
 * embedded IP's own checksum - has to be reflected in it.
 *
 * Returns 0 when it translated.
 */
static_always_inline int
bpa_icmp_error_out2in (vlib_buffer_t *b, ip4_header_t *ip,
		       bpa_per_thread_t *pt, u32 *ip_priv_out)
{
  u8 *end = (u8 *) vlib_buffer_get_current (b) + b->current_length;
  icmp46_header_t *icmp = (icmp46_header_t *) ((u8 *) ip + ip4_header_bytes (ip));
  clib_bihash_kv_16_8_t kv, value;
  bpa_session_t *s;
  ip4_header_t *inner;
  u16 *inner_port;
  u32 inner_hlen, inner_src;
  ip_csum_t sum;
  int inner_proto;
  u16 old_port;
  u32 old_addr;

  /* ICMP (4) + 4 unused + minimum IP (20) + 8 of the original L4 */
  if (PREDICT_FALSE ((u8 *) icmp + 8 + 20 + 8 > end))
    return -1;

  inner = (ip4_header_t *) ((u8 *) icmp + 8);
  inner_hlen = ip4_header_bytes (inner);
  if (PREDICT_FALSE (inner_hlen < 20 || (u8 *) inner + inner_hlen + 8 > end))
    return -1;

  inner_proto = bpa_proto_of (inner->protocol);
  if (PREDICT_FALSE (inner_proto < 0))
    return -1;

  /* For TCP and UDP the source port is the first two bytes of the L4;
   * for embedded ICMP, the identifier sits right after the header. */
  inner_port = (u16 *) ((u8 *) inner + inner_hlen);
  if (inner_proto == BPA_PROTO_ICMP)
    inner_port = (u16 *) ((u8 *) inner + inner_hlen + 4);
  if (PREDICT_FALSE ((u8 *) inner_port + 2 > end))
    return -1;

  inner_src = clib_net_to_host_u32 (inner->src_address.as_u32);
  bpa_key (&kv, inner_src, clib_net_to_host_u16 (inner_port[0]),
	   (u8) inner_proto);
  if (PREDICT_FALSE (clib_bihash_search_16_8 (&pt->out2in, &kv, &value)))
    return -1;

  s = pool_elt_at_index (pt->sessions, value.value);
  *ip_priv_out = s->ip_priv;

  /* --- outer destination: our public address -> subscriber --- */
  sum = ip->checksum;
  sum = ip_csum_update (sum, ip->dst_address.as_u32,
			clib_host_to_net_u32 (s->ip_priv), ip4_header_t,
			dst_address);
  ip->checksum = ip_csum_fold (sum);
  ip->dst_address.as_u32 = clib_host_to_net_u32 (s->ip_priv);

  /* --- embedded source, with the ICMP checksum following all of it --- */
  {
    ip_csum_t icmp_sum = icmp->checksum;
    u16 old_inner_csum = inner->checksum;
    ip_csum_t inner_sum;

    old_addr = inner->src_address.as_u32;
    old_port = inner_port[0];

    inner_sum = inner->checksum;
    inner_sum = ip_csum_update (inner_sum, old_addr,
				clib_host_to_net_u32 (s->ip_priv),
				ip4_header_t, src_address);
    inner->checksum = ip_csum_fold (inner_sum);
    inner->src_address.as_u32 = clib_host_to_net_u32 (s->ip_priv);
    inner_port[0] = clib_host_to_net_u16 (s->port_priv);

    icmp_sum = ip_csum_update (icmp_sum, old_addr,
			       inner->src_address.as_u32, ip4_header_t,
			       src_address);
    icmp_sum = ip_csum_update (icmp_sum, old_inner_csum, inner->checksum,
			       ip4_header_t, checksum);
    icmp_sum = ip_csum_update (icmp_sum, old_port, inner_port[0],
			       udp_header_t, src_port);
    icmp->checksum = ip_csum_fold (icmp_sum);
  }

  return 0;
}

VLIB_NODE_FN (bpa_out2in_node)
(vlib_main_t *vm, vlib_node_runtime_t *node, vlib_frame_t *frame)
{
  bpa_main_t *bm = &bpa_main;
  u32 thread_index = vm->thread_index;
  bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, thread_index);
  u32 n_left_from, *from;
  vlib_buffer_t *bufs[VLIB_FRAME_SIZE], **b = bufs;
  u16 nexts[VLIB_FRAME_SIZE], *next = nexts;
  f64 now = vlib_time_now (vm);
  u32 n_translated = 0, n_icmp_err = 0, n_ftp_ack = 0, n_rtsp = 0;

  from = vlib_frame_vector_args (frame);
  n_left_from = frame->n_vectors;
  vlib_get_buffers (vm, from, bufs, n_left_from);

  while (n_left_from > 0)
    {
      ip4_header_t *ip = vlib_buffer_get_current (b[0]);
      clib_bihash_kv_16_8_t kv, value;
      bpa_session_t *s = 0;
      void *l4h = 0;
      u32 pool_index = 0, dst_host, ip_priv = 0;
      u16 port_pub = 0;
      int proto, matched = 0;

      next[0] = BPA_OUT2IN_NEXT_DROP;
      dst_host = clib_net_to_host_u32 (ip->dst_address.as_u32);

      /* Not addressed to one of our public pools: leave it alone. */
      if (bpa_pool_by_outside (bm, dst_host, &pool_index))
	{
	  vlib_node_increment_counter (vm, node->node_index,
				       BPA_OUT2IN_ERROR_NOT_OURS, 1);
	  vnet_feature_next_u16 (&next[0], b[0]);
	  goto trace;
	}

      proto = bpa_proto_of (ip->protocol);
      if (PREDICT_FALSE (proto < 0))
	{
	  b[0]->error = node->errors[BPA_OUT2IN_ERROR_UNSUPPORTED];
	  goto trace;
	}

      /* ICMP error: what identifies the session is the embedded packet, not
       * the outer header. Separate path. */
      if (PREDICT_FALSE (proto == BPA_PROTO_ICMP
			 && !vnet_buffer (b[0])->ip.reass.is_non_first_fragment))
	{
	  icmp46_header_t *ic =
	    (icmp46_header_t *) ((u8 *) ip + ip4_header_bytes (ip));

	  if ((u8 *) ic + 4 <= (u8 *) vlib_buffer_get_current (b[0])
				 + b[0]->current_length
	      && bpa_icmp_is_error (ic->type))
	    {
	      if (bpa_icmp_error_out2in (b[0], ip, pt, &ip_priv))
		{
		  b[0]->error =
		    node->errors[BPA_OUT2IN_ERROR_ICMP_MALFORMED];
		  goto trace;
		}
	      matched = 1;
	      n_icmp_err++;
	      vnet_feature_next_u16 (&next[0], b[0]);
	      goto trace;
	    }
	}

      if (PREDICT_FALSE (bpa_l4_dst (b[0], ip, proto, &port_pub, &l4h)))
	{
	  b[0]->error = node->errors[BPA_OUT2IN_ERROR_TRUNCATED];
	  goto trace;
	}

      /*
       * Arithmetic pre-check: does this port belong to any block of this
       * pool at all? Rejects scan traffic before it touches the hash.
       *
       * bpa_reverse_sub() yields the BLOCK index. In deterministic mode the
       * block index is the subscriber index - that identity is what makes
       * the mode stateless. In dynamic mode the block belongs to whoever
       * currently holds it, so it takes a lookup.
       */
      {
	bpa_pool_t *p = pool_elt_at_index (bm->pools, pool_index);
	u32 block_idx, sub_idx;

	if (PREDICT_FALSE (
	      bpa_reverse_sub (p, dst_host, port_pub, &block_idx)))
	  {
	    b[0]->error = node->errors[BPA_OUT2IN_ERROR_OUT_OF_BLOCK];
	    goto trace;
	  }

	if (PREDICT_TRUE (p->mode == BPA_MODE_DETERMINISTIC))
	  {
	    if (PREDICT_FALSE (
		  bpa_set_addr_of (&p->inside, block_idx, &ip_priv)))
	      {
		b[0]->error = node->errors[BPA_OUT2IN_ERROR_OUT_OF_BLOCK];
		goto trace;
	      }
	  }
	else
	  {
	    u32 w, nw;

	    bpa_worker_id (bm, thread_index, &w, &nw);
	    if (PREDICT_FALSE (
		  pool_index >= vec_len (pt->dyn)
		  || bpa_dyn_owner (vec_elt_at_index (pt->dyn, pool_index),
				    block_idx, nw, &sub_idx)
		  || bpa_set_addr_of (&p->inside, sub_idx, &ip_priv)))
	      {
		b[0]->error = node->errors[BPA_OUT2IN_ERROR_BLOCK_FREE];
		goto trace;
	      }
	  }
      }

      bpa_key (&kv, dst_host, port_pub, (u8) proto);
      if (PREDICT_FALSE (clib_bihash_search_16_8 (&pt->out2in, &kv, &value)))
	{
	  /* No session: unsolicited inbound. Correct NAT behaviour is drop. */
	  b[0]->error = node->errors[BPA_OUT2IN_ERROR_NO_SESSION];
	  goto trace;
	}

      /*
       * Address-dependent filtering: a mapping only accepts replies from the
       * remote the subscriber actually contacted (RFC 4787 REQ-8).
       *
       * Checked here rather than in the key, because the key IS the mapping:
       * putting the remote in it would give one subscriber a different
       * external port per destination, which is endpoint-DEPENDENT mapping
       * and breaks the peer-to-peer applications this product exists to keep
       * working. Filtering and mapping are separate decisions and this only
       * changes the first.
       */
      s = pool_elt_at_index (pt->sessions, value.value);
      if (PREDICT_FALSE (pool_elt_at_index (bm->pools, pool_index)->filter
			   == BPA_FILTER_ADDRESS_DEPENDENT
			 && s->remote != 0
			 && s->remote != clib_net_to_host_u32 (
			      ip->src_address.as_u32)))
	{
	  b[0]->error = node->errors[BPA_OUT2IN_ERROR_FILTERED];
	  goto trace;
	}

      matched = 1;
      s->last_heard = now;
      /* The media an ALG announced has arrived: it is a flow now, not a
       * prediction, so it stops living on the short expectation timer. */
      s->is_expect = 0;
      if (proto == BPA_PROTO_TCP)
	bpa_tcp_track (s, ((tcp_header_t *) l4h)->flags);

      bpa_rewrite_dst (ip, l4h, (u8) proto, s->ip_priv, s->port_priv);
      n_translated++;

      /*
       * The remote acknowledges the subscriber's stream as the FTP ALG
       * lengthened it. The subscriber only knows its own numbering, so the
       * acknowledgement is shifted back here. Without it the subscriber sees
       * bytes it never sent acknowledged, and the control connection stalls a
       * few commands after the rewrite - which does not look like FTP, and
       * does not look like NAT either.
       */
      if (PREDICT_FALSE (proto == BPA_PROTO_TCP && l4h != 0
			 && (s->alg & BPA_ALG_FTP_SEQ)))
	n_ftp_ack += bpa_ftp_ack_out2in (l4h, s);

      /*
       * RTSP ALG, internet -> subscriber: the reply that echoes client_port.
       *
       * Same ordering as in2out - after the address rewrite so the checksum
       * covers the final pseudo-header, before the sequence correction so the
       * correction point is recorded in the server's own coordinates.
       */
      if (PREDICT_FALSE (proto == BPA_PROTO_TCP && l4h != 0))
	{
	  tcp_header_t *tcp = l4h;
	  bpa_pool_t *sp = pool_elt_at_index (bm->pools, pool_index);

	  if (bpa_alg_on (sp->alg, BPA_ALG_RTSP)
	      && clib_net_to_host_u16 (tcp->src_port) == BPA_RTSP_PORT)
	    {
	      switch (bpa_rtsp_out2in (vm, b[0], ip, tcp, pt, s))
		{
		case BPA_ALG_DONE:
		  n_rtsp++;
		  break;
		case BPA_ALG_UNPARSED:
		  vlib_node_increment_counter (
		    vm, node->node_index, BPA_OUT2IN_ERROR_RTSP_UNPARSED, 1);
		  break;
		case BPA_ALG_PARTIAL:
		  vlib_node_increment_counter (
		    vm, node->node_index, BPA_OUT2IN_ERROR_RTSP_PARTIAL, 1);
		  break;
		case BPA_ALG_NO_ROOM:
		  vlib_node_increment_counter (
		    vm, node->node_index, BPA_OUT2IN_ERROR_RTSP_NO_ROOM, 1);
		  break;
		default:
		  break;
		}
	    }

	  if (PREDICT_FALSE (s->adj[BPA_DIR_OUT].off_after
			     | s->adj[BPA_DIR_OUT].off_before
			     | s->adj[BPA_DIR_IN].off_after
			     | s->adj[BPA_DIR_IN].off_before))
	    bpa_tcp_seq_adjust (tcp, &s->adj[BPA_DIR_IN],
				&s->adj[BPA_DIR_OUT]);
	}

      vnet_feature_next_u16 (&next[0], b[0]);

    trace:
      if (PREDICT_FALSE (b[0]->flags & VLIB_BUFFER_IS_TRACED))
	{
	  bpa_out2in_trace_t *t =
	    vlib_add_trace (vm, node, b[0], sizeof (*t));
	  t->proto = (u8) (proto < 0 ? 0 : proto);
	  t->matched = matched;
	  t->ip_pub = dst_host;
	  t->port_pub = port_pub;
	  t->ip_priv = s ? s->ip_priv : ip_priv;
	  t->port_priv = s ? s->port_priv : 0;
	}

      b++;
      next++;
      n_left_from--;
    }

  vlib_buffer_enqueue_to_next (vm, node, from, nexts, frame->n_vectors);

  vlib_node_increment_counter (vm, node->node_index,
			       BPA_OUT2IN_ERROR_TRANSLATED, n_translated);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_OUT2IN_ERROR_ICMP_ERROR, n_icmp_err);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_OUT2IN_ERROR_FTP_ACK, n_ftp_ack);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_OUT2IN_ERROR_RTSP_REWRITTEN, n_rtsp);
  return frame->n_vectors;
}

VLIB_REGISTER_NODE (bpa_out2in_node) = {
  .name = "bpa-out2in",
  .vector_size = sizeof (u32),
  .format_trace = format_bpa_out2in_trace,
  .type = VLIB_NODE_TYPE_INTERNAL,
  .n_errors = BPA_OUT2IN_N_ERROR,
  .error_strings = bpa_out2in_error_strings,
  .n_next_nodes = BPA_OUT2IN_N_NEXT,
  .next_nodes = {
    [BPA_OUT2IN_NEXT_DROP] = "error-drop",
  },
};

VNET_FEATURE_INIT (bpa_out2in_feature, static) = {
  .arc_name = "ip4-unicast",
  .node_name = "bpa-out2in",
  .runs_before = VNET_FEATURES ("ip4-lookup"),
  /*
   * Has to run AFTER shallow virtual reassembly, otherwise the buffer's port
   * fields do not exist yet and a following fragment is read with a garbage
   * port - which creates a phantom session and burns one port of the block
   * per fragment.
   */
  .runs_after = VNET_FEATURES ("ip4-sv-reassembly-feature"),
};
