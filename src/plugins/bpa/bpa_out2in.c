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
#include "bpa_l4.h"

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
    return format (s, "BPA out2in %U:%u proto %U sem sessao",
		   format_ip4_address, &pub, t->port_pub, format_bpa_proto,
		   t->proto);

  return format (s, "BPA out2in %U:%u -> %U:%u proto %U", format_ip4_address,
		 &pub, t->port_pub, format_ip4_address, &priv, t->port_priv,
		 format_bpa_proto, t->proto);
}

#define foreach_bpa_out2in_error                                              \
  _ (TRANSLATED, "pacotes traduzidos")                                        \
  _ (NOT_OURS, "destino fora dos pools publicos")                             \
  _ (NO_SESSION, "sem sessao para o destino")                                 \
  _ (UNSUPPORTED, "protocolo nao traduzido")                                  \
  _ (OUT_OF_BLOCK, "porta fora de qualquer bloco")                            \
  _ (TRUNCATED, "pacote truncado")

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

static_always_inline void
bpa_rewrite_dst (ip4_header_t *ip, void *l4h, u8 proto, u32 new_addr,
		 u16 new_port)
{
  u32 old_addr = clib_net_to_host_u32 (ip->dst_address.as_u32);
  ip_csum_t sum;

  if (old_addr != new_addr)
    {
      sum = ip->checksum;
      sum = ip_csum_update (sum, ip->dst_address.as_u32,
			    clib_host_to_net_u32 (new_addr), ip4_header_t,
			    dst_address);
      ip->checksum = ip_csum_fold (sum);
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
      sum = ip_csum_update (sum, ip->dst_address.as_u32,
			    clib_host_to_net_u32 (new_addr), ip4_header_t,
			    dst_address);
      sum = ip_csum_update (sum, tcp->dst_port,
			    clib_host_to_net_u16 (new_port), tcp_header_t,
			    dst_port);
      tcp->checksum = ip_csum_fold (sum);
      tcp->dst_port = clib_host_to_net_u16 (new_port);
    }
  else
    {
      udp_header_t *udp = l4h;
      if (udp->checksum)
	{
	  sum = udp->checksum;
	  sum = ip_csum_update (sum, ip->dst_address.as_u32,
				clib_host_to_net_u32 (new_addr), ip4_header_t,
				dst_address);
	  sum = ip_csum_update (sum, udp->dst_port,
				clib_host_to_net_u16 (new_port),
				udp_header_t, dst_port);
	  udp->checksum = ip_csum_fold (sum);
	}
      udp->dst_port = clib_host_to_net_u16 (new_port);
    }

  ip->dst_address.as_u32 = clib_host_to_net_u32 (new_addr);
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
  u32 n_translated = 0;

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

      if (PREDICT_FALSE (bpa_l4_dst (b[0], ip, proto, &port_pub, &l4h)))
	{
	  b[0]->error = node->errors[BPA_OUT2IN_ERROR_TRUNCATED];
	  goto trace;
	}

      /*
       * Arithmetic pre-check: does this port belong to any block of this
       * pool at all? Rejects scan traffic before it touches the hash.
       */
      {
	bpa_pool_t *p = pool_elt_at_index (bm->pools, pool_index);
	if (PREDICT_FALSE (bpa_reverse (p, dst_host, port_pub, &ip_priv)))
	  {
	    b[0]->error = node->errors[BPA_OUT2IN_ERROR_OUT_OF_BLOCK];
	    goto trace;
	  }
      }

      bpa_key (&kv, dst_host, port_pub, (u8) proto);
      if (PREDICT_FALSE (clib_bihash_search_16_8 (&pt->out2in, &kv, &value)))
	{
	  /* No session: unsolicited inbound. Correct NAT behaviour is drop. */
	  b[0]->error = node->errors[BPA_OUT2IN_ERROR_NO_SESSION];
	  goto trace;
	}

      s = pool_elt_at_index (pt->sessions, value.value);
      matched = 1;
      s->last_heard = now;
      if (proto == BPA_PROTO_TCP)
	{
	  tcp_header_t *tcp = l4h;
	  if (tcp->flags & TCP_FLAG_ACK)
	    s->tcp_estab = 1;
	}

      bpa_rewrite_dst (ip, l4h, (u8) proto, s->ip_priv, s->port_priv);
      n_translated++;
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
};
