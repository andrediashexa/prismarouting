/*
 * bpa_nat64.c - NAT64 translation (#485, phase 2).
 *
 * Stateful NAT64 here is the CGNAT with an IPv6 inside. A subscriber is a
 * prefix of a NAT64 pool's inside (bpa_nat64.h); it sends to an IPv4 server
 * embedded under the pool's NAT64 prefix (RFC 6052) and leaves on its own
 * public address and port block, computed exactly as for an IPv4 subscriber,
 * because the pool's `inside` is the dense pseudo set its sub_idx indexes.
 * Sessions are the ordinary ones (bpa_session.c): same table, same blocks,
 * same expiry, same out2in key; what differs is the inside key - the host's
 * /128 in in2out6 - and the two header translations, RFC 7915.
 *
 * IPv6 -> IPv4 runs here, on the ip6-unicast arc of the inside interfaces.
 * IPv4 -> IPv6 runs in bpa-out2in, which finds the session as it always has
 * and hands a NAT64 one to bpa_nat64_out2in() / bpa_nat64_out2in_icmp_err().
 * VPP's own nat64 plugin is not used: it keeps its own tables, logs per
 * session and has no port blocks. Its RFC 7915 ICMP code is
 * <vnet/ip/ip6_to_ip4.h> and <vnet/ip/ip4_to_ip6.h>, and that is reused.
 */
#include "bpa.h"
#include <vnet/ip/ip6_to_ip4.h>
#include <vnet/ip/ip4_to_ip6.h>
#include <vnet/ip/reass/ip6_sv_reass.h>
#include <vnet/udp/udp_packet.h>
#include <vnet/tcp/tcp_packet.h>

/* ------------------------------------------------------------------ */
/* the pool and the session                                            */
/* ------------------------------------------------------------------ */

/* not a unit: it walks bpa_main's pools, VPP's; the rule it applies is
 * bpa_set6_index_of(), which test/test_bpa_nat64.c holds, and lab/nat64.sh
 * drives this through a box. */
int
bpa_pool_by_inside6 (bpa_main_t *bm, const u8 a[16], u32 *pool_index,
		     u32 *sub_idx)
{
  bpa_pool_t *p;

  if (PREDICT_TRUE (bm->n_nat64 == 0))
    return -1;
  pool_foreach (p, bm->pools)
    {
      if (!p->is_nat64 || p->deleted)
	continue;
      if (bpa_set6_index_of (&p->inside6, a, sub_idx) == 0)
	{
	  *pool_index = p - bm->pools;
	  return 0;
	}
    }
  return -1;
}

/* not a unit: it writes a per-thread bihash, VPP's; lab/nat64.sh drives
 * the sessions it keys. The key is bpa_in6_key(), held by
 * test/test_bpa_nat64.c. */
void
bpa_nat64_session_key (bpa_per_thread_t *pt, bpa_session_t *s,
		       const ip6_address_t *host)
{
  clib_bihash_kv_24_8_t kv;
  bpa_kv_24_8_t k6;
  u32 si = s - pt->sessions;

  vec_validate (pt->nat64_host, si);
  pt->nat64_host[si] = *host;
  bpa_in6_key (&k6, host->as_u8, s->port_priv, s->proto, s->fib_index);
  clib_memcpy (kv.key, k6.key, sizeof (kv.key));
  kv.value = si;
  clib_bihash_add_del_24_8 (&pt->in2out6, &kv, 1);
}

/* not a unit: the delete of the key above, from bpa_session_free(). */
void
bpa_nat64_session_unkey (bpa_per_thread_t *pt, bpa_session_t *s)
{
  clib_bihash_kv_24_8_t kv;
  bpa_kv_24_8_t k6;
  u32 si = s - pt->sessions;

  if (si >= vec_len (pt->nat64_host))
    return;
  bpa_in6_key (&k6, pt->nat64_host[si].as_u8, s->port_priv, s->proto,
	       s->fib_index);
  clib_memcpy (kv.key, k6.key, sizeof (kv.key));
  clib_bihash_add_del_24_8 (&pt->in2out6, &kv, 0);
}

/* not a unit: a bihash search, VPP's. */
static_always_inline bpa_session_t *
bpa_nat64_find (bpa_per_thread_t *pt, const u8 host[16], u16 port, u8 proto,
		u32 fib)
{
  clib_bihash_kv_24_8_t kv, value;
  bpa_kv_24_8_t k6;

  bpa_in6_key (&k6, host, port, proto, fib);
  clib_memcpy (kv.key, k6.key, sizeof (kv.key));
  if (clib_bihash_search_24_8 (&pt->in2out6, &kv, &value))
    return 0;
  return pool_elt_at_index (pt->sessions, value.value);
}

/* ------------------------------------------------------------------ */
/* the interfaces                                                      */
/* ------------------------------------------------------------------ */

/*
 * The IPv6 half of an inside interface: only while a NAT64 pool exists.
 * An inside interface carries the operator's native IPv6 too, and putting
 * shallow reassembly and a node in front of all of it on a box with no
 * NAT64 pool would change how its IPv6 fragments are handled for nothing.
 */
/* not a unit: vnet_feature_enable_disable on a live interface; the labs
 * drive it. */
static void
bpa_nat64_interface (u32 sw_if_index, int enable)
{
  const char *feature =
    vlib_num_workers () ? "bpa-nat64-in2out-handoff" : "bpa-nat64-in2out";
  int on =
    vnet_feature_is_enabled ("ip6-unicast", feature, sw_if_index) == 1;

  if (on == !!enable)
    return;
  if (vnet_feature_enable_disable ("ip6-unicast", feature, sw_if_index,
				   enable, 0, 0))
    return;
  ip6_sv_reass_enable_disable_with_refcnt (sw_if_index, enable);
}

/* not a unit: it walks the box's interfaces; the labs drive it. */
void
bpa_nat64_interfaces_update (void)
{
  bpa_main_t *bm = &bpa_main;
  vnet_main_t *vnm = vnet_get_main ();
  vnet_sw_interface_t *si;
  const char *f4 = vlib_num_workers () ? "bpa-in2out-handoff" : "bpa-in2out";

  pool_foreach (si, vnm->interface_main.sw_interfaces)
    {
      int inside = vnet_feature_is_enabled ("ip4-unicast", f4,
					    si->sw_if_index) == 1;
      bpa_nat64_interface (si->sw_if_index, inside && bm->n_nat64 > 0);
    }
}

/* ------------------------------------------------------------------ */
/* IPv6 -> IPv4 (RFC 7915 section 5)                                   */
/* ------------------------------------------------------------------ */

#define foreach_bpa_nat64_in2out_error                                        \
  _ (TRANSLATED, "packets translated")                                        \
  _ (NEW_SESSION, "sessions created")                                         \
  _ (NOT_OURS, "IPv6 packets no NAT64 pool takes")                            \
  _ (UNSUPPORTED, "protocol NAT64 does not carry")                            \
  _ (TRUNCATED, "packets too short to translate")                             \
  _ (NONGLOBAL, "Well-Known Prefix to a non-global IPv4 (RFC 6052 3.1)")      \
  _ (OVER_CAPACITY, "subscriber beyond the pool's capacity")                  \
  _ (NO_PORT, "subscriber's port block exhausted")                            \
  _ (NO_SESSION, "fragment or ICMP error with no session")                    \
  _ (ICMP_FAILED, "ICMPv6 RFC 7915 does not translate")

typedef enum
{
#define _(sym, str) BPA_NAT64_IN2OUT_ERROR_##sym,
  foreach_bpa_nat64_in2out_error
#undef _
    BPA_NAT64_IN2OUT_N_ERROR,
} bpa_nat64_in2out_error_t;

static char *bpa_nat64_in2out_error_strings[] = {
#define _(sym, str) str,
  foreach_bpa_nat64_in2out_error
#undef _
};

typedef enum
{
  BPA_NAT64_IN2OUT_NEXT_DROP,
  BPA_NAT64_IN2OUT_NEXT_IP4_LOOKUP,
  BPA_NAT64_IN2OUT_NEXT_IP6_LOOKUP,
  BPA_NAT64_IN2OUT_N_NEXT,
} bpa_nat64_in2out_next_t;

typedef struct
{
  ip6_address_t src;
  u32 dst4;
  u32 ip_pub;
  u16 port_priv;
  u16 port_pub;
  u8 proto;
  u8 translated;
} bpa_nat64_trace_t;

/* not a unit: a VPP format function over a va_list. */
static u8 *
format_bpa_nat64_trace (u8 *s, va_list *args)
{
  CLIB_UNUSED (vlib_main_t * vm) = va_arg (*args, vlib_main_t *);
  CLIB_UNUSED (vlib_node_t * node) = va_arg (*args, vlib_node_t *);
  bpa_nat64_trace_t *t = va_arg (*args, bpa_nat64_trace_t *);
  ip4_address_t d, p;

  if (!t->translated)
    return format (s, "BPA NAT64: %U not translated", format_ip6_address,
		   &t->src);
  d.as_u32 = clib_host_to_net_u32 (t->dst4);
  p.as_u32 = clib_host_to_net_u32 (t->ip_pub);
  return format (s, "BPA NAT64: [%U]:%u -> %U:%u, to %U, proto %u",
		 format_ip6_address, &t->src, t->port_priv, format_ip4_address,
		 &p, t->port_pub, format_ip4_address, &d, t->proto);
}

/*
 * The fixed part of an IPv4 header translated from an IPv6 one, RFC 7915
 * 5.1. DF is set above 1260 bytes and an identification generated below it
 * when there is no Fragment Header; with one, its offset, M flag and the low
 * bits of its identification carry over and DF is clear.
 */
/* not a unit: it writes VPP's ip4_header_t; lab/nat64.sh reads every field
 * of a translated packet off the wire. */
static_always_inline void
bpa_nat64_ip4_fill (ip4_header_t *ip4, u32 vtcfl, u8 hop, u16 total,
		    const ip6_frag_hdr_t *f, u8 proto, u32 src, u32 dst,
		    u16 *next_id)
{
  ip4->ip_version_and_header_length = IP4_VERSION_AND_HEADER_LENGTH_NO_OPTIONS;
  ip4->tos = ip6_translate_tos (vtcfl);
  ip4->length = clib_host_to_net_u16 (total);
  if (f)
    {
      u16 fl = ip6_frag_hdr_offset (f);

      if (ip6_frag_hdr_more (f))
	fl |= IP4_HEADER_FLAG_MORE_FRAGMENTS;
      ip4->fragment_id = frag_id_6to4 (f->identification);
      ip4->flags_and_fragment_offset = clib_host_to_net_u16 (fl);
    }
  else if (total > 1260)
    {
      ip4->fragment_id = 0;
      ip4->flags_and_fragment_offset =
	clib_host_to_net_u16 (IP4_HEADER_FLAG_DONT_FRAGMENT);
    }
  else
    {
      ip4->fragment_id = clib_host_to_net_u16 ((*next_id)++);
      ip4->flags_and_fragment_offset = 0;
    }
  ip4->ttl = hop;
  ip4->protocol = proto;
  ip4->src_address.as_u32 = src;
  ip4->dst_address.as_u32 = dst;
  ip4->checksum = ip4_header_checksum (ip4);
}

/*
 * TCP and UDP, in place: the IPv4 header is written just in front of the
 * transport header, over the end of the IPv6 one. Everything the IPv6
 * header says is read before that write. The transport checksum follows the
 * pseudo-header from IPv6 to IPv4 and the source port into the block, on
 * the first fragment only - it covers the whole datagram, and only the
 * pseudo-header and the port, which the first fragment carries, change.
 */
/* not a unit: it rewrites a vlib_buffer_t, VPP's; lab/nat64.sh checks
 * every checksum of what it emits. */
static_always_inline void
bpa_nat64_in2out_l4 (vlib_buffer_t *b, ip6_header_t *ip6, u16 l4_off,
		     u16 frag_off, u8 l4_proto, const bpa_session_t *s,
		     u32 dst4_net, u16 *next_id)
{
  u8 *l4 = (u8 *) ip6 + l4_off;
  ip4_header_t *ip4 = (ip4_header_t *) (l4 - sizeof (ip4_header_t));
  ip6_address_t src6 = ip6->src_address, dst6 = ip6->dst_address;
  u32 vtcfl = ip6->ip_version_traffic_class_and_flow_label;
  u16 l4_len = clib_net_to_host_u16 (ip6->payload_length) +
	       sizeof (ip6_header_t) - l4_off;
  u8 hop = ip6->hop_limit;
  ip6_frag_hdr_t fcopy, *f = 0;
  u32 src4_net = clib_host_to_net_u32 (s->ip_pub);

  if (frag_off)
    {
      fcopy = *(ip6_frag_hdr_t *) ((u8 *) ip6 + frag_off);
      f = &fcopy;
    }

  if (!f || ip6_frag_hdr_offset (f) == 0)
    {
      u16 *csum, *sport, newp = clib_host_to_net_u16 (s->port_pub);
      ip_csum_t sum;

      if (l4_proto == IP_PROTOCOL_TCP)
	{
	  tcp_header_t *t = (tcp_header_t *) l4;
	  csum = &t->checksum;
	  sport = &t->src_port;
	}
      else
	{
	  udp_header_t *u = (udp_header_t *) l4;
	  csum = &u->checksum;
	  sport = &u->src_port;
	}
      sum = *csum;
      sum = ip_csum_sub_even (sum, src6.as_u64[0]);
      sum = ip_csum_sub_even (sum, src6.as_u64[1]);
      sum = ip_csum_sub_even (sum, dst6.as_u64[0]);
      sum = ip_csum_sub_even (sum, dst6.as_u64[1]);
      sum = ip_csum_add_even (sum, src4_net);
      sum = ip_csum_add_even (sum, dst4_net);
      sum = ip_csum_sub_even (sum, *sport);
      sum = ip_csum_add_even (sum, newp);
      *sport = newp;
      *csum = ip_csum_fold (sum);
      /* Zero means "no checksum" to an IPv4 UDP receiver. */
      if (l4_proto == IP_PROTOCOL_UDP && *csum == 0)
	*csum = 0xffff;
    }

  bpa_nat64_ip4_fill (ip4, vtcfl, hop, l4_len + sizeof (ip4_header_t), f,
		      l4_proto, src4_net, dst4_net, next_id);
  vlib_buffer_advance (b, (u8 *) ip4 - (u8 *) ip6);
}

typedef struct
{
  const bpa_session_t *s;
  u32 dst4_net;
} bpa_nat64_icmp_ctx_t;

/* not a unit: an icmp6_to_icmp() callback. The outer header: from the
 * subscriber's public address to the IPv4 the destination embeds, and for
 * an echo the identifier into the block. */
static int
bpa_nat64_icmp6_outer (ip6_header_t *ip6, ip4_header_t *ip4, void *arg)
{
  bpa_nat64_icmp_ctx_t *c = arg;
  icmp46_header_t *icmp = (icmp46_header_t *) (ip4 + 1);

  ip4->src_address.as_u32 = clib_host_to_net_u32 (c->s->ip_pub);
  ip4->dst_address.as_u32 = c->dst4_net;
  if (icmp->type == ICMP4_echo_request || icmp->type == ICMP4_echo_reply)
    ((u16 *) (icmp + 1))[0] = clib_host_to_net_u16 (c->s->port_pub);
  return 0;
}

/* not a unit: icmp6_to_icmp()'s inner callback, for an error the
 * subscriber sends about a packet it received: that packet came from the
 * server (embedded) to the subscriber (its public address and port). */
static int
bpa_nat64_icmp6_inner (ip6_header_t *ip6, ip4_header_t *ip4, void *arg)
{
  bpa_nat64_icmp_ctx_t *c = arg;

  /* The source is the server, under the pool's prefix: the outer
   * destination carries the same IPv4, which the caller extracted with the
   * pool's own length. */
  ip4->src_address.as_u32 = c->dst4_net;
  ip4->dst_address.as_u32 = clib_host_to_net_u32 (c->s->ip_pub);
  if (ip4->protocol == IP_PROTOCOL_TCP || ip4->protocol == IP_PROTOCOL_UDP)
    {
      udp_header_t *u = (udp_header_t *) ((u8 *) ip4 + sizeof (*ip4));
      u->dst_port = clib_host_to_net_u16 (c->s->port_pub);
    }
  return 0;
}

/*
 * The L4 identity of an inbound IPv6 packet: the source port, the ICMPv6
 * echo identifier, or for an ICMPv6 error the embedded packet's DESTINATION
 * port with the host it was addressed to. Returns the IP protocol of what
 * keys the session, or -1 when nothing in the packet can.
 */
/* not a unit: it parses a vlib_buffer_t; lab/nat64.sh drives each case. */
static int
bpa_nat64_l4_id (vlib_main_t *vm, vlib_buffer_t *b, ip6_header_t *ip6,
		 u8 l4_proto, u16 l4_off, u16 frag_off, u16 *port,
		 const u8 **host, int *is_err)
{
  u8 *end = (u8 *) vlib_buffer_get_current (b) + b->current_length;
  u8 *l4 = (u8 *) ip6 + l4_off;
  int non_first = frag_off && ip6_frag_hdr_offset (
				(ip6_frag_hdr_t *) ((u8 *) ip6 + frag_off)) != 0;

  *is_err = 0;
  *host = ip6->src_address.as_u8;
  if (l4_proto == IP_PROTOCOL_TCP || l4_proto == IP_PROTOCOL_UDP)
    {
      if (non_first)
	{
	  *port = clib_net_to_host_u16 (vnet_buffer (b)->ip.reass.l4_src_port);
	  return l4_proto;
	}
      if (l4 + 4 > end)
	return -1;
      *port = clib_net_to_host_u16 (((udp_header_t *) l4)->src_port);
      return l4_proto;
    }
  if (l4_proto != IP_PROTOCOL_ICMP6 || non_first || l4 + 8 > end)
    return -1;
  {
    icmp46_header_t *ic = (icmp46_header_t *) l4;

    if (ic->type == ICMP6_echo_request || ic->type == ICMP6_echo_reply)
      {
	*port = clib_net_to_host_u16 (((u16 *) (ic + 1))[0]);
	return IP_PROTOCOL_ICMP;
      }
    if (ic->type >= 128)
      return -1;
    /* An error: the session is the packet it quotes, which went from the
     * server to this host - so its destination is the host and port. */
    {
      ip6_header_t *in = (ip6_header_t *) (l4 + 8);
      u8 *inl4 = (u8 *) (in + 1);

      if (inl4 + 4 > end)
	return -1;
      *is_err = 1;
      *host = in->dst_address.as_u8;
      if (in->protocol == IP_PROTOCOL_TCP || in->protocol == IP_PROTOCOL_UDP)
	{
	  *port = clib_net_to_host_u16 (((udp_header_t *) inl4)->dst_port);
	  return in->protocol;
	}
      if (in->protocol == IP_PROTOCOL_ICMP6)
	{
	  *port = clib_net_to_host_u16 (((u16 *) (inl4 + 4))[0]);
	  return IP_PROTOCOL_ICMP;
	}
    }
  }
  return -1;
}

/* not a unit: the node's body; it needs frames and the graph. */
VLIB_NODE_FN (bpa_nat64_in2out_node)
(vlib_main_t *vm, vlib_node_runtime_t *node, vlib_frame_t *frame)
{
  bpa_main_t *bm = &bpa_main;
  bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, vm->thread_index);
  u32 n_left, *from = vlib_frame_vector_args (frame);
  vlib_buffer_t *bufs[VLIB_FRAME_SIZE], **b = bufs;
  u16 nexts[VLIB_FRAME_SIZE], *next = nexts;
  f64 now = vlib_time_now (vm);
  u32 n_translated = 0, n_new = 0, n_not_ours = 0;

  n_left = frame->n_vectors;
  vlib_get_buffers (vm, from, bufs, n_left);

  while (n_left > 0)
    {
      ip6_header_t *ip6 = vlib_buffer_get_current (b[0]);
      bpa_session_t *s = 0;
      bpa_pool_t *p;
      u32 pool_index, sub_idx, dst4 = 0, fib;
      u16 l4_off = 0, frag_off = 0, port = 0;
      u8 l4_proto = 0;
      const u8 *host = 0;
      int key_proto, is_err = 0, translated = 0;

      next[0] = BPA_NAT64_IN2OUT_NEXT_DROP;

      if (bpa_pool_by_inside6 (bm, ip6->src_address.as_u8, &pool_index,
			       &sub_idx))
	goto not_ours;
      p = pool_elt_at_index (bm->pools, pool_index);
      if (!bpa_ip6_in_prefix (ip6->dst_address.as_u8, p->nat64_prefix,
			      p->nat64_len))
	goto not_ours;

      bpa_nat64_extract (ip6->dst_address.as_u8, p->nat64_len, &dst4);
      if (PREDICT_FALSE (bpa_nat64_is_wkp (p->nat64_prefix, p->nat64_len)
			 && !bpa_nat64_wkp_may_carry (dst4)))
	{
	  b[0]->error = node->errors[BPA_NAT64_IN2OUT_ERROR_NONGLOBAL];
	  goto trace;
	}

      if (ip6_parse (vm, b[0], ip6, b[0]->current_length, &l4_proto, &l4_off,
		     &frag_off))
	{
	  b[0]->error = node->errors[BPA_NAT64_IN2OUT_ERROR_TRUNCATED];
	  goto trace;
	}
      key_proto = bpa_nat64_l4_id (vm, b[0], ip6, l4_proto, l4_off, frag_off,
				   &port, &host, &is_err);
      if (key_proto < 0)
	{
	  b[0]->error = node->errors[BPA_NAT64_IN2OUT_ERROR_UNSUPPORTED];
	  goto trace;
	}

      fib = vlib_buffer_get_ip6_fib_index (b[0]);
      s = bpa_nat64_find (pt, host, port, (u8) bpa_proto_of ((u8) key_proto),
			  fib);
      if (!s)
	{
	  bpa_block_t blk;
	  bpa_create_why_t why;
	  int non_first =
	    frag_off && ip6_frag_hdr_offset (
			  (ip6_frag_hdr_t *) ((u8 *) ip6 + frag_off)) != 0;

	  /* Only a first packet opens a mapping: an error or a later
	   * fragment names one that has to exist already. */
	  if (is_err || non_first)
	    {
	      b[0]->error = node->errors[BPA_NAT64_IN2OUT_ERROR_NO_SESSION];
	      goto trace;
	    }
	  if (bpa_forward (p, sub_idx, &blk))
	    {
	      b[0]->error = node->errors[BPA_NAT64_IN2OUT_ERROR_OVER_CAPACITY];
	      goto trace;
	    }
	  s = bpa_session_create (pt, p, pool_index, &blk, port,
				  (u8) bpa_proto_of ((u8) key_proto), fib, now,
				  BPA_CREATE_TRAFFIC, &why);
	  if (!s)
	    {
	      b[0]->error = node->errors[BPA_NAT64_IN2OUT_ERROR_NO_PORT];
	      goto trace;
	    }
	  bpa_nat64_session_key (pt, s, &ip6->src_address);
	  n_new++;
	}

      s->last_heard = now;
      s->remote = dst4;
      dst4 = clib_host_to_net_u32 (dst4);

      if (l4_proto == IP_PROTOCOL_ICMP6)
	{
	  bpa_nat64_icmp_ctx_t c = { .s = s, .dst4_net = dst4 };

	  if (icmp6_to_icmp (vm, b[0], bpa_nat64_icmp6_outer, &c,
			     bpa_nat64_icmp6_inner, &c))
	    {
	      b[0]->error = node->errors[BPA_NAT64_IN2OUT_ERROR_ICMP_FAILED];
	      goto trace;
	    }
	}
      else
	{
	  if (l4_proto == IP_PROTOCOL_TCP && (!frag_off ||
	      ip6_frag_hdr_offset ((ip6_frag_hdr_t *) ((u8 *) ip6 + frag_off))
		== 0))
	    bpa_tcp_track (s, ((tcp_header_t *) ((u8 *) ip6 + l4_off))->flags);
	  bpa_nat64_in2out_l4 (b[0], ip6, l4_off, frag_off, l4_proto, s, dst4,
			       &pt->nat64_ip_id);
	}

      vnet_buffer (b[0])->sw_if_index[VLIB_TX] = s->out_fib_index;
      next[0] = BPA_NAT64_IN2OUT_NEXT_IP4_LOOKUP;
      translated = 1;
      n_translated++;
      goto trace;

    not_ours:
      n_not_ours++;
      if (bm->n_workers)
	next[0] = BPA_NAT64_IN2OUT_NEXT_IP6_LOOKUP;
      else
	vnet_feature_next_u16 (&next[0], b[0]);

    trace:
      if (PREDICT_FALSE (b[0]->flags & VLIB_BUFFER_IS_TRACED))
	{
	  bpa_nat64_trace_t *t = vlib_add_trace (vm, node, b[0], sizeof (*t));
	  clib_memset (t, 0, sizeof (*t));
	  t->translated = (u8) translated;
	  if (translated)
	    {
	      t->src = pt->nat64_host[s - pt->sessions];
	      t->dst4 = clib_net_to_host_u32 (dst4);
	      t->ip_pub = s->ip_pub;
	      t->port_priv = s->port_priv;
	      t->port_pub = s->port_pub;
	      t->proto = s->proto;
	    }
	}
      b++;
      next++;
      n_left--;
    }

  vlib_buffer_enqueue_to_next (vm, node, from, nexts, frame->n_vectors);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_NAT64_IN2OUT_ERROR_TRANSLATED, n_translated);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_NAT64_IN2OUT_ERROR_NEW_SESSION, n_new);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_NAT64_IN2OUT_ERROR_NOT_OURS, n_not_ours);
  return frame->n_vectors;
}

VLIB_REGISTER_NODE (bpa_nat64_in2out_node) = {
  .name = "bpa-nat64-in2out",
  .vector_size = sizeof (u32),
  .format_trace = format_bpa_nat64_trace,
  .type = VLIB_NODE_TYPE_INTERNAL,
  .n_errors = BPA_NAT64_IN2OUT_N_ERROR,
  .error_strings = bpa_nat64_in2out_error_strings,
  .n_next_nodes = BPA_NAT64_IN2OUT_N_NEXT,
  .next_nodes = {
    [BPA_NAT64_IN2OUT_NEXT_DROP] = "error-drop",
    [BPA_NAT64_IN2OUT_NEXT_IP4_LOOKUP] = "ip4-lookup",
    [BPA_NAT64_IN2OUT_NEXT_IP6_LOOKUP] = "ip6-lookup",
  },
};

VNET_FEATURE_INIT (bpa_nat64_in2out_feature, static) = {
  .arc_name = "ip6-unicast",
  .node_name = "bpa-nat64-in2out",
  /* After shallow reassembly: a later fragment's port is in the buffer
   * metadata only once ip6-sv-reassembly has read the first one. */
  .runs_after = VNET_FEATURES ("ip6-sv-reassembly-feature"),
};

/* ------------------------------------------------------------------ */
/* the handoff                                                         */
/* ------------------------------------------------------------------ */

/*
 * The worker that owns a subscriber is the one its block's arithmetic names,
 * for an IPv6 subscriber exactly as for an IPv4 one: bpa_sub_thread() of
 * sub_idx, which is the worker bpa-out2in-handoff steers the replies to from
 * the public port. A packet no NAT64 pool takes stays where it is.
 */
/* not a unit: the handoff node's body; it needs frames and the graph. */
VLIB_NODE_FN (bpa_nat64_in2out_handoff_node)
(vlib_main_t *vm, vlib_node_runtime_t *node, vlib_frame_t *frame)
{
  bpa_main_t *bm = &bpa_main;
  u32 n_left, *from = vlib_frame_vector_args (frame), n_enq;
  vlib_buffer_t *bufs[VLIB_FRAME_SIZE], **b = bufs;
  u16 thread_indices[VLIB_FRAME_SIZE], *ti = thread_indices;
  u32 this_thread = vm->thread_index;

  n_left = frame->n_vectors;
  vlib_get_buffers (vm, from, bufs, n_left);
  while (n_left > 0)
    {
      ip6_header_t *ip6 = vlib_buffer_get_current (b[0]);
      u32 pool_index, sub_idx;

      ti[0] = (u16) this_thread;
      if (bpa_pool_by_inside6 (bm, ip6->src_address.as_u8, &pool_index,
			       &sub_idx) == 0)
	ti[0] = (u16) bpa_sub_thread (bm, sub_idx);
      b++;
      ti++;
      n_left--;
    }
  n_enq = vlib_buffer_enqueue_to_thread (vm, node, bm->nat64_in2out_fq_index,
					 from, thread_indices,
					 frame->n_vectors, 1);
  if (n_enq < frame->n_vectors)
    vlib_node_increment_counter (vm, node->node_index, 0,
				 frame->n_vectors - n_enq);
  return frame->n_vectors;
}

static char *bpa_nat64_handoff_error_strings[] = {
  "packets dropped by congestion",
};

VLIB_REGISTER_NODE (bpa_nat64_in2out_handoff_node) = {
  .name = "bpa-nat64-in2out-handoff",
  .vector_size = sizeof (u32),
  .type = VLIB_NODE_TYPE_INTERNAL,
  .n_errors = 1,
  .error_strings = bpa_nat64_handoff_error_strings,
  .n_next_nodes = 1,
  .next_nodes = {
    [0] = "error-drop",
  },
};

VNET_FEATURE_INIT (bpa_nat64_in2out_handoff_feature, static) = {
  .arc_name = "ip6-unicast",
  .node_name = "bpa-nat64-in2out-handoff",
  .runs_after = VNET_FEATURES ("ip6-sv-reassembly-feature"),
};

/* ------------------------------------------------------------------ */
/* IPv4 -> IPv6 (RFC 7915 section 4), called from bpa-out2in           */
/* ------------------------------------------------------------------ */

/*
 * TCP and UDP to a NAT64 session. The IPv6 header goes in front of the
 * transport header - 40 bytes where the IPv4 one had 20 or more, plus a
 * Fragment Header when the IPv4 packet is a fragment (RFC 7915 4.1). The
 * checksum follows the pseudo-header and the destination port back to the
 * host; an IPv4 UDP datagram with no checksum gets one computed, which needs
 * the whole datagram, so a FRAGMENT of one is refused (RFC 7915 4.5).
 * Returns 0, or -1 for that refusal.
 */
/* not a unit: it rewrites a vlib_buffer_t; lab/nat64.sh drives each case. */
int
bpa_nat64_out2in (vlib_main_t *vm, vlib_buffer_t *b, bpa_per_thread_t *pt,
		  bpa_session_t *s)
{
  bpa_main_t *bm = &bpa_main;
  ip4_header_t *ip4 = vlib_buffer_get_current (b);
  const bpa_pool_t *p = pool_elt_at_index (bm->pools, s->pool_index);
  u32 ihl = ip4_header_bytes (ip4);
  u8 *l4 = (u8 *) ip4 + ihl;
  u16 fl = clib_net_to_host_u16 (ip4->flags_and_fragment_offset);
  u16 off = fl & 0x1fff;
  int is_frag = off != 0 || (fl & IP4_HEADER_FLAG_MORE_FRAGMENTS);
  u32 src4 = clib_net_to_host_u32 (ip4->src_address.as_u32);
  u32 src4_net = ip4->src_address.as_u32, dst4_net = ip4->dst_address.as_u32;
  u16 l4_len = clib_net_to_host_u16 (ip4->length) - ihl;
  u8 proto = ip4->protocol, ttl = ip4->ttl, tos = ip4->tos;
  u16 id = ip4->fragment_id;
  ip6_address_t src6, dst6 = pt->nat64_host[s - pt->sessions];
  ip6_header_t *ip6;
  int need_frag;

  bpa_nat64_embed (p->nat64_prefix, p->nat64_len, src4, src6.as_u8);

  if (off == 0)
    {
      u16 *csum, *dport, newp = clib_host_to_net_u16 (s->port_priv);
      ip_csum_t sum;

      if (proto == IP_PROTOCOL_TCP)
	{
	  tcp_header_t *t = (tcp_header_t *) l4;
	  csum = &t->checksum;
	  dport = &t->dst_port;
	  bpa_tcp_track (s, t->flags);
	}
      else
	{
	  udp_header_t *u = (udp_header_t *) l4;
	  csum = &u->checksum;
	  dport = &u->dst_port;
	}
      *dport = newp;
      if (proto == IP_PROTOCOL_UDP && *csum == 0)
	{
	  if (is_frag)
	    return -1;
	  /* Whole datagram, pseudo-header and all, before anything moves. */
	  sum = ip_csum_with_carry (0, clib_host_to_net_u16 (l4_len));
	  sum = ip_csum_with_carry (sum, clib_host_to_net_u16 (IP_PROTOCOL_UDP));
	  sum = ip_csum_with_carry (sum, src6.as_u64[0]);
	  sum = ip_csum_with_carry (sum, src6.as_u64[1]);
	  sum = ip_csum_with_carry (sum, dst6.as_u64[0]);
	  sum = ip_csum_with_carry (sum, dst6.as_u64[1]);
	  sum = ip_incremental_checksum_buffer (vm, b, l4 - (u8 *) ip4, l4_len,
						sum);
	  *csum = ~ip_csum_fold (sum);
	  if (*csum == 0)
	    *csum = 0xffff;
	}
      else
	{
	  sum = *csum;
	  sum = ip_csum_sub_even (sum, src4_net);
	  sum = ip_csum_sub_even (sum, dst4_net);
	  sum = ip_csum_add_even (sum, src6.as_u64[0]);
	  sum = ip_csum_add_even (sum, src6.as_u64[1]);
	  sum = ip_csum_add_even (sum, dst6.as_u64[0]);
	  sum = ip_csum_add_even (sum, dst6.as_u64[1]);
	  sum = ip_csum_sub_even (sum, clib_host_to_net_u16 (s->port_pub));
	  sum = ip_csum_add_even (sum, newp);
	  *csum = ip_csum_fold (sum);
	}
    }

  /* RFC 7915 4.1: an IPv4 packet that may be fragmented (DF clear) and
   * would make an IPv6 packet over 1280 bytes SHOULD be fragmented to 1280.
   * It is given a Fragment Header here - an unfragmented one at offset 0 -
   * and bpa-nat64-frag6 cuts it; an IPv6 router never fragments, and the
   * subscriber's link may be 1500 bytes, where a translated 1500-byte IPv4
   * fragment is 1528. */
  need_frag = !(fl & IP4_HEADER_FLAG_DONT_FRAGMENT)
	      && sizeof (ip6_header_t) + (is_frag ? 8 : 0) + l4_len > 1280;
  if (need_frag)
    is_frag = 1;

  if (is_frag)
    {
      ip6_frag_hdr_t *f = (ip6_frag_hdr_t *) (l4 - sizeof (*f));

      ip6 = (ip6_header_t *) ((u8 *) f - sizeof (*ip6));
      f->next_hdr = proto;
      f->rsv = 0;
      f->fragment_offset_and_more = clib_host_to_net_u16 (
	(off << 3) | ((fl & IP4_HEADER_FLAG_MORE_FRAGMENTS) ? 1 : 0));
      f->identification = frag_id_4to6 (id);
      ip6->protocol = IP_PROTOCOL_IPV6_FRAGMENTATION;
      ip6->payload_length = clib_host_to_net_u16 (l4_len + sizeof (*f));
    }
  else
    {
      ip6 = (ip6_header_t *) (l4 - sizeof (*ip6));
      ip6->protocol = proto;
      ip6->payload_length = clib_host_to_net_u16 (l4_len);
    }
  ip6->ip_version_traffic_class_and_flow_label =
    clib_host_to_net_u32 ((6 << 28) | ((u32) tos << 20));
  ip6->hop_limit = ttl;
  ip6->src_address = src6;
  ip6->dst_address = dst6;
  vlib_buffer_advance (b, (u8 *) ip6 - (u8 *) ip4);

  vnet_buffer (b)->sw_if_index[VLIB_TX] = s->fib_index;
  return need_frag ? 1 : 0;
}

typedef struct
{
  const bpa_pool_t *p;
  const bpa_session_t *s;
  const ip6_address_t *host;
} bpa_nat64_icmp4_ctx_t;

/* not a unit: an icmp_to_icmp6() callback. Outer header: from the IPv4
 * sender, embedded, to the subscriber's host; an echo's identifier back to
 * the subscriber's. */
static int
bpa_nat64_icmp4_outer (vlib_buffer_t *b, ip4_header_t *ip4, ip6_header_t *ip6,
		       void *arg)
{
  bpa_nat64_icmp4_ctx_t *c = arg;
  icmp46_header_t *icmp = (icmp46_header_t *) (ip6 + 1);

  bpa_nat64_embed (c->p->nat64_prefix, c->p->nat64_len,
		   clib_net_to_host_u32 (ip4->src_address.as_u32),
		   ip6->src_address.as_u8);
  ip6->dst_address = *c->host;
  if (icmp->type == ICMP6_echo_request || icmp->type == ICMP6_echo_reply)
    ((u16 *) (icmp + 1))[0] = clib_host_to_net_u16 (c->s->port_priv);
  return 0;
}

/* not a unit: icmp_to_icmp6()'s inner callback. The packet an error
 * quotes went from this subscriber (public address, port) to the server:
 * back to the host and its own port, the server embedded. */
static int
bpa_nat64_icmp4_inner (vlib_buffer_t *b, ip4_header_t *ip4, ip6_header_t *ip6,
		       void *arg)
{
  bpa_nat64_icmp4_ctx_t *c = arg;

  ip6->src_address = *c->host;
  bpa_nat64_embed (c->p->nat64_prefix, c->p->nat64_len,
		   clib_net_to_host_u32 (ip4->dst_address.as_u32),
		   ip6->dst_address.as_u8);
  if (ip6->protocol == IP_PROTOCOL_TCP || ip6->protocol == IP_PROTOCOL_UDP)
    {
      udp_header_t *u = (udp_header_t *) (ip6 + 1);
      u->src_port = clib_host_to_net_u16 (c->s->port_priv);
    }
  else if (ip6->protocol == IP_PROTOCOL_ICMP6)
    {
      icmp46_header_t *ic = (icmp46_header_t *) (ip6 + 1);
      ((u16 *) (ic + 1))[0] = clib_host_to_net_u16 (c->s->port_priv);
    }
  return 0;
}

/*
 * ICMP to a NAT64 session: an echo, or an error about a packet the
 * subscriber sent (bpa-out2in found the session from the quoted packet).
 * Packet Too Big gets its MTU raised to what IPv6 can carry before the +20
 * the translation adds: an IPv6 host ignores a PTB below 1280 (RFC 8200
 * 5), and the IPv4 side then fragments, which the translator allows by
 * clearing DF on what it sends at or under 1260 bytes (RFC 7915 5.1).
 */
/* not a unit: it rewrites a vlib_buffer_t; lab/nat64.sh drives each case. */
int
bpa_nat64_out2in_icmp (vlib_main_t *vm, vlib_buffer_t *b,
		       bpa_per_thread_t *pt, bpa_session_t *s)
{
  bpa_main_t *bm = &bpa_main;
  ip4_header_t *ip4 = vlib_buffer_get_current (b);
  icmp46_header_t *icmp = (icmp46_header_t *) ((u8 *) ip4 +
					       ip4_header_bytes (ip4));
  bpa_nat64_icmp4_ctx_t c = {
    .p = pool_elt_at_index (bm->pools, s->pool_index),
    .s = s,
    .host = &pt->nat64_host[s - pt->sessions],
  };

  if (ip4_header_bytes (ip4) != sizeof (*ip4))
    return -1; /* icmp_to_icmp6() assumes no options */
  if (icmp->type == ICMP4_destination_unreachable
      && icmp->code
	   == ICMP4_destination_unreachable_fragmentation_needed_and_dont_fragment_set)
    {
      u32 *mtu = (u32 *) (icmp + 1);
      if (clib_net_to_host_u32 (*mtu) < 1260)
	*mtu = clib_host_to_net_u32 (1260);
    }
  if (icmp_to_icmp6 (b, bpa_nat64_icmp4_outer, &c, bpa_nat64_icmp4_inner, &c))
    return -1;
  vnet_buffer (b)->sw_if_index[VLIB_TX] = s->fib_index;
  return 0;
}

/* ------------------------------------------------------------------ */
/* fragmenting to 1280 (RFC 7915 4.1)                                  */
/* ------------------------------------------------------------------ */

/*
 * Copy `n` bytes at offset `o` of a (possibly chained) buffer.
 */
/* not a unit: it reads a vlib_buffer_t chain, VPP's; lab/nat64.sh sends a
 * 3000-byte datagram through it. */
static void
bpa_nat64_copy_out (vlib_main_t *vm, vlib_buffer_t *b, u32 o, u8 *dst, u32 n)
{
  while (n)
    {
      u32 take;

      while (o >= b->current_length)
	{
	  o -= b->current_length;
	  if (!(b->flags & VLIB_BUFFER_NEXT_PRESENT))
	    return;
	  b = vlib_get_buffer (vm, b->next_buffer);
	}
      take = clib_min (n, b->current_length - o);
      clib_memcpy_fast (dst, vlib_buffer_get_current (b) + o, take);
      dst += take;
      n -= take;
      o += take;
    }
}

#define BPA_NAT64_FRAG_DATA ((1280 - 40 - 8) & ~7) /* 1232 */

typedef enum
{
  BPA_NAT64_FRAG6_NEXT_DROP,
  BPA_NAT64_FRAG6_NEXT_LOOKUP,
  BPA_NAT64_FRAG6_N_NEXT,
} bpa_nat64_frag6_next_t;

/*
 * A translated packet bpa_nat64_out2in() gave a Fragment Header because it
 * is over 1280 and may be fragmented: cut into pieces of at most 1280,
 * each carrying the same IPv6 header and Fragment Header with its own
 * offset - the original's plus where the piece starts - and More set on
 * every piece but the last of a datagram that was itself the last.
 */
/* not a unit: the node's body; it needs frames, buffers and the graph.
 * lab/nat64.sh sends a 3000-byte datagram through it and compares bytes. */
VLIB_NODE_FN (bpa_nat64_frag6_node)
(vlib_main_t *vm, vlib_node_runtime_t *node, vlib_frame_t *frame)
{
  u32 n_left = frame->n_vectors, *from = vlib_frame_vector_args (frame);
  u32 *to_free = 0, n_out = 0;

  while (n_left > 0)
    {
      u32 bi = from[0];
      vlib_buffer_t *b = vlib_get_buffer (vm, bi);
      ip6_header_t *ip6 = vlib_buffer_get_current (b);
      ip6_frag_hdr_t *f = (ip6_frag_hdr_t *) (ip6 + 1);
      u32 plen = clib_net_to_host_u16 (ip6->payload_length) - sizeof (*f);
      u32 base = ip6_frag_hdr_offset_bytes (f), pos = 0;
      int more = ip6_frag_hdr_more (f);
      u32 tx = vnet_buffer (b)->sw_if_index[VLIB_TX];

      while (pos < plen)
	{
	  u32 nbi, chunk = clib_min (plen - pos, BPA_NAT64_FRAG_DATA);
	  vlib_buffer_t *nb;
	  ip6_header_t *nip6;
	  ip6_frag_hdr_t *nf;
	  int last = pos + chunk >= plen;

	  if (vlib_buffer_alloc (vm, &nbi, 1) != 1)
	    break;
	  nb = vlib_get_buffer (vm, nbi);
	  nip6 = vlib_buffer_get_current (nb);
	  clib_memcpy_fast (nip6, ip6, sizeof (*ip6) + sizeof (*f));
	  nf = (ip6_frag_hdr_t *) (nip6 + 1);
	  bpa_nat64_copy_out (vm, b, sizeof (*ip6) + sizeof (*f) + pos,
			      (u8 *) (nf + 1), chunk);
	  nip6->payload_length = clib_host_to_net_u16 (sizeof (*nf) + chunk);
	  nf->fragment_offset_and_more = clib_host_to_net_u16 (
	    (((base + pos) >> 3) << 3) | (last && !more ? 0 : 1));
	  nb->current_length = sizeof (*nip6) + sizeof (*nf) + chunk;
	  vnet_buffer (nb)->sw_if_index[VLIB_RX] =
	    vnet_buffer (b)->sw_if_index[VLIB_RX];
	  vnet_buffer (nb)->sw_if_index[VLIB_TX] = tx;
	  vlib_set_next_frame_buffer (vm, node, BPA_NAT64_FRAG6_NEXT_LOOKUP,
				      nbi);
	  n_out++;
	  pos += chunk;
	}
      vec_add1 (to_free, bi);
      from++;
      n_left--;
    }
  if (vec_len (to_free))
    vlib_buffer_free (vm, to_free, vec_len (to_free));
  vec_free (to_free);
  vlib_node_increment_counter (vm, node->node_index, 0, n_out);
  return frame->n_vectors;
}

static char *bpa_nat64_frag6_error_strings[] = {
  "IPv6 fragments made (RFC 7915 4.1)",
};

VLIB_REGISTER_NODE (bpa_nat64_frag6_node) = {
  .name = "bpa-nat64-frag6",
  .vector_size = sizeof (u32),
  .type = VLIB_NODE_TYPE_INTERNAL,
  .n_errors = 1,
  .error_strings = bpa_nat64_frag6_error_strings,
  .n_next_nodes = BPA_NAT64_FRAG6_N_NEXT,
  .next_nodes = {
    [BPA_NAT64_FRAG6_NEXT_DROP] = "error-drop",
    [BPA_NAT64_FRAG6_NEXT_LOOKUP] = "ip6-lookup",
  },
};

/* not a unit: plugin init, VPP's. */
static clib_error_t *
bpa_nat64_init (vlib_main_t *vm)
{
  bpa_main.nat64_in2out_fq_index =
    vlib_frame_queue_main_init (bpa_nat64_in2out_node.index, 0);
  return 0;
}

VLIB_INIT_FUNCTION (bpa_nat64_init) = {
  .runs_after = VLIB_INITS ("bpa_init"),
};
