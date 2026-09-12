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
#include "bpa_errors.h"
#include "bpa_esp.h"
#include "bpa_ftp.h"
#include "bpa_icmp.h"
#include "bpa_l4.h"
#include "bpa_alg.h"
#include "bpa_mss.h"
#include "bpa_pptp.h"

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

typedef enum
{
#define _(sym, drop, str) BPA_OUT2IN_ERROR_##sym,
  foreach_bpa_out2in_error
#undef _
    BPA_OUT2IN_N_ERROR,
} bpa_out2in_error_t;

static char *bpa_out2in_error_strings[] = {
#define _(sym, drop, str) str,
  foreach_bpa_out2in_error
#undef _
};

typedef enum
{
  BPA_OUT2IN_NEXT_DROP,
  BPA_OUT2IN_NEXT_LOOKUP,
  BPA_OUT2IN_N_NEXT,
} bpa_out2in_next_t;



/*
 * Translates an ICMP error coming from the internet, from the embedded packet
 * bpa_icmp_err_parse() has already located and bounds-checked.
 *
 * Structure: outer IP (dst = our public) / ICMP / embedded IP
 * (src = our public, dst = remote) / 8 bytes of the original L4.
 *
 * Rewrites the outer destination and the embedded source. The ICMP checksum
 * covers the whole embedded packet, so every change in there - including the
 * embedded IP's own checksum - has to be reflected in it.
 *
 * Returns 0 when it translated, -1 when this worker holds no session for the
 * flow the error is about. The two used to be one return value together with
 * every bounds failure above, and the caller counted all of them as a
 * malformed packet - so a session that simply lived on another worker was
 * reported as the internet sending rubbish (#201). A counter that describes
 * the packet when the fault is in the box is worse than no counter: it sends
 * whoever reads it looking outward.
 */
static_always_inline int
bpa_icmp_error_out2in (ip4_header_t *ip, const bpa_icmp_err_t *e,
		       bpa_per_thread_t *pt, u32 *ip_priv_out, u32 *fib_out)
{
  clib_bihash_kv_16_8_t kv, value;
  bpa_session_t *s;
  ip4_header_t *inner = e->inner;
  u16 *inner_port = e->port;
  icmp46_header_t *icmp =
    (icmp46_header_t *) ((u8 *) ip + ip4_header_bytes (ip));
  ip_csum_t sum;
  int inner_proto = bpa_proto_of (e->ip_proto);
  u16 old_port;
  u32 old_addr, ip_priv, new_port = 0;

  if (e->ip_proto == IP_PROTOCOL_GRE)
    {
      /*
       * The PPTP call this error is about, out of the shared table for the
       * reason bpa.h gives: the embedded packet carries the Call ID the
       * SERVER chose, which is in no subscriber's port block, so there is no
       * worker to steer to and the error is answered wherever it landed.
       *
       * Unlike ESP's key this one is unambiguous, and bpa_pptp.h says why:
       * a server hands out one Call ID per call from its own table, so two
       * subscribers sharing a public address and talking to the same server
       * have different ones.
       *
       * Nothing inside the embedded GRE header is rewritten. The Call ID in
       * there is the SERVER's, which this box never translated - the one it
       * did translate is the subscriber's own, and that one is not in an
       * outbound packet at all. So the rewrite below is the addresses and the
       * checksums, and that is the whole list.
       */
      if (PREDICT_FALSE (bpa_gre_lookup (e->pub_addr, e->call_id, e->peer,
					 &ip_priv, fib_out)))
	return -1;
      *ip_priv_out = ip_priv;
    }
  else if (e->ip_proto == IP_PROTOCOL_IPSEC_ESP)
    {
      /*
       * The tunnel this error is about, out of the table that is global for
       * the reason bpa_esp.h gives - and being global is what makes it
       * reachable here, on whatever worker the error arrived on, with no
       * steer to arrange. The ordinary inbound ESP path a few hundred lines
       * below resolves the identical key.
       *
       * It inherits the ambiguity that path documents, exactly: two
       * subscribers on one public address with tunnels to the same peer
       * cannot be told apart, so the second one's PMTUD stays broken. That is
       * a smaller failure than the one being fixed - the first subscriber's
       * tunnel black-holed large packets with no signal at all - and it is
       * the same trade already made for the tunnel itself.
       */
      if (PREDICT_FALSE (bpa_esp_lookup (e->pub_addr, e->peer, &ip_priv,
					 fib_out)))
	return -1;
      *ip_priv_out = ip_priv;
    }
  else
    {
      bpa_key_out (&kv, e->pub_addr, e->pub_port, (u8) inner_proto);
      if (PREDICT_FALSE (clib_bihash_search_16_8 (&pt->out2in, &kv, &value)))
	return -1;

      s = pool_elt_at_index (pt->sessions, value.value);
      ip_priv = s->ip_priv;
      new_port = s->port_priv;
      *ip_priv_out = ip_priv;
      *fib_out = s->fib_index;
    }

  /* --- outer destination: our public address -> subscriber --- */
  sum = ip->checksum;
  sum = ip_csum_update (sum, ip->dst_address.as_u32,
			clib_host_to_net_u32 (ip_priv), ip4_header_t,
			dst_address);
  ip->checksum = ip_csum_fold (sum);
  ip->dst_address.as_u32 = clib_host_to_net_u32 (ip_priv);

  /* --- embedded source, with the ICMP checksum following all of it --- */
  {
    ip_csum_t icmp_sum = icmp->checksum;
    u16 old_inner_csum = inner->checksum;
    ip_csum_t inner_sum;

    old_addr = inner->src_address.as_u32;

    inner_sum = inner->checksum;
    inner_sum = ip_csum_update (inner_sum, old_addr,
				clib_host_to_net_u32 (ip_priv),
				ip4_header_t, src_address);
    inner->checksum = ip_csum_fold (inner_sum);
    inner->src_address.as_u32 = clib_host_to_net_u32 (ip_priv);

    icmp_sum = ip_csum_update (icmp_sum, old_addr,
			       inner->src_address.as_u32, ip4_header_t,
			       src_address);
    icmp_sum = ip_csum_update (icmp_sum, old_inner_csum, inner->checksum,
			       ip4_header_t, checksum);

    /* ESP has no port to put back, and the embedded packet may not even carry
     * the two bytes one would sit in. Guarded on the pointer the parse handed
     * over rather than on the protocol, so a protocol added to that switch
     * without a port cannot reach a write through a null here. */
    if (inner_port)
      {
	old_port = inner_port[0];
	inner_port[0] = clib_host_to_net_u16 (new_port);
	icmp_sum = ip_csum_update (icmp_sum, old_port, inner_port[0],
				   udp_header_t, src_port);
      }
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
  u32 n_translated = 0, n_icmp_err = 0, n_seq_adj = 0, n_rtsp = 0, n_sip = 0;
  u32 n_forwarded = 0, n_mss = 0;
  u32 n_pptp = 0, n_pptp_unknown = 0, n_pptp_bad = 0;
  u32 n_esp = 0;

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
	  bpa_next_after (&next[0], b[0], BPA_OUT2IN_NEXT_LOOKUP);
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
	  u8 *end =
	    (u8 *) vlib_buffer_get_current (b[0]) + b[0]->current_length;
	  icmp46_header_t *ic =
	    (icmp46_header_t *) ((u8 *) ip + ip4_header_bytes (ip));
	  bpa_icmp_err_t e;

	  /* The type is read here as well as inside the parse, and it has to
	   * be: an echo is not an error and belongs on the ordinary path
	   * below, so "this did not parse" must not be allowed to mean both
	   * "malformed" and "not for this branch". */
	  if ((u8 *) ic + 4 <= end && bpa_icmp_is_error (ic->type))
	    {
	      if (bpa_icmp_err_parse (ip, end, &e))
		{
		  b[0]->error =
		    node->errors[BPA_OUT2IN_ERROR_ICMP_MALFORMED];
		  goto trace;
		}
	      {
		u32 err_fib = ~0;

		if (bpa_icmp_error_out2in (ip, &e, pt, &ip_priv, &err_fib))
		  {
		    b[0]->error = node->errors[BPA_OUT2IN_ERROR_NO_SESSION];
		    goto trace;
		  }
		/* Into the VRF the subscriber's own traffic came from. The
		 * outside interface's table has no route to a private
		 * address, which the ordinary paths both say in their own
		 * words - and an ICMP error is the one that used to be sent
		 * on without it. */
		if (err_fib != ~0u)
		  vnet_buffer (b[0])->sw_if_index[VLIB_TX] = err_fib;
	      }
	      matched = 1;
	      n_icmp_err++;
	      bpa_next_after (&next[0], b[0], BPA_OUT2IN_NEXT_LOOKUP);
	      goto trace;
	    }
	}

      /*
       * ESP, which is the whole of the inbound passthrough.
       *
       * Before the port arithmetic below, not after it, because there is no
       * port: the pre-check that rejects scan traffic on a port outside every
       * block would reject every ESP packet on port zero. What identifies the
       * subscriber is the peer that sent it, looked up in the one table this
       * plugin shares between workers - see bpa_esp.h for why it has to be
       * shared, and for the pair of subscribers it cannot separate.
       *
       * Nothing below the IP header is touched. The SPI is inside the
       * integrity check (RFC 4303 s3.3.2), so rewriting it would hand the
       * subscriber a packet its own stack discards - the silent drop this
       * project already met once with a stale checksum, except that this one
       * cannot be repaired because the key is the subscriber's.
       */
      if (PREDICT_FALSE (proto == BPA_PROTO_ESP))
	{
	  u32 esp_fib;

	  if (!bpa_alg_on (pool_elt_at_index (bm->pools, pool_index)->alg,
			   BPA_ALG_ESP))
	    {
	      b[0]->error = node->errors[BPA_OUT2IN_ERROR_ESP_OFF];
	      goto trace;
	    }

	  if (bpa_esp_lookup (dst_host,
			      clib_net_to_host_u32 (ip->src_address.as_u32),
			      &ip_priv, &esp_fib))
	    {
	      b[0]->error = node->errors[BPA_OUT2IN_ERROR_ESP_NO_TUNNEL];
	      goto trace;
	    }

	  /* Back into the VRF the subscriber's own traffic came from, for the
	   * reason spelled out further down: the outside interface's table
	   * has no route to a private address. */
	  vnet_buffer (b[0])->sw_if_index[VLIB_TX] = esp_fib;
	  bpa_rewrite_dst (ip, 0, (u8) proto, ip_priv, 0);
	  matched = 1;
	  n_esp++;
	  n_translated++;
	  bpa_next_after (&next[0], b[0], BPA_OUT2IN_NEXT_LOOKUP);
	  goto trace;
	}

      if (PREDICT_FALSE (bpa_l4_dst (b[0], ip, proto, &port_pub, &l4h)))
	{
	  /* Plain GRE arriving at a shared public address identifies no
	   * subscriber, so there is nobody to give it to. Counted apart from
	   * a truncated packet, which is a different problem entirely. */
	  b[0]->error =
	    node->errors[proto == BPA_PROTO_GRE
			   ? BPA_OUT2IN_ERROR_GRE_NOT_ENHANCED
			   : BPA_OUT2IN_ERROR_TRUNCATED];
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

      bpa_key_out (&kv, dst_host, port_pub, (u8) proto);
      if (PREDICT_FALSE (clib_bihash_search_16_8 (&pt->out2in, &kv, &value)))
	{
	  bpa_pool_t *p = pool_elt_at_index (bm->pools, pool_index);
	  const bpa_static_t *sf = 0;

	  /*
	   * Unsolicited inbound, which a NAT drops - unless the operator has
	   * said this port belongs to a subscriber. That is the whole feature:
	   * the mapping exists because it was configured, not because the
	   * subscriber sent something first.
	   *
	   * The forward is trusted only when it agrees with the arithmetic
	   * above, which resolved this public port to a subscriber
	   * independently. They can only disagree if the pool was redefined
	   * under a forward that survived it, and translating on the older of
	   * two answers is how a customer receives another customer's traffic.
	   */
	  if (PREDICT_FALSE (vec_len (p->statics) != 0))
	    sf = bpa_static_by_pub (p, port_pub, (u8) proto);

	  if (PREDICT_FALSE (sf != 0 && sf->ip_priv == ip_priv))
	    s = bpa_session_static_open (pt, p, pool_index, sf, now);

	  if (PREDICT_TRUE (s == 0))
	    {
	      b[0]->error = node->errors[BPA_OUT2IN_ERROR_NO_SESSION];
	      goto trace;
	    }
	  n_forwarded++;
	}
      else
	s = pool_elt_at_index (pt->sessions, value.value);

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
       *
       * A forwarded mapping is exempt. This mode asks "did this subscriber
       * contact you first", and for a configured forward the answer is no by
       * design - the operator has already decided. Left in the filter, a
       * forwarded port would work for whoever the subscriber last replied to
       * and silently for nobody else.
       */
      if (PREDICT_FALSE (pool_elt_at_index (bm->pools, pool_index)->filter
			   == BPA_FILTER_ADDRESS_DEPENDENT
			 && !s->is_static
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
	{
	  tcp_header_t *tcp = l4h;

	  bpa_tcp_track (s, tcp->flags);

	  /* The other half of the clamp: a SYN/ACK announces what the server
	   * will receive, so this is the one that limits what the SUBSCRIBER
	   * sends. A SYN arriving here rather than a SYN/ACK is an inbound
	   * connection to a static port forward, and wants clamping for the
	   * same reason. */
	  if (PREDICT_FALSE ((tcp->flags & TCP_FLAG_SYN) != 0))
	    {
	      const bpa_pool_t *mp =
		pool_elt_at_index (bm->pools, pool_index);
	      u16 mss = mp->tcp_mss_auto ? bm->auto_mss : mp->tcp_mss;

	      if (mss)
		n_mss += bpa_tcp_mss_clamp (
		  tcp, (u8 *) vlib_buffer_get_current (b[0])
			 + b[0]->current_length,
		  mss);
	    }

	  /* The server's half of the PPTP control channel: it names the Call
	   * ID this box handed out, and the subscriber only understands its
	   * own. */
	  if (PREDICT_FALSE (
		clib_net_to_host_u16 (tcp->src_port) == BPA_PPTP_PORT
		&& bpa_alg_on (
		  pool_elt_at_index (bm->pools, pool_index)->alg,
		  BPA_ALG_PPTP)))
	    {
	      /*
	       * The index, because the ALG may create a GRE session and
	       * pool_get() moves the whole vector. `s` is a pointer into it
	       * and everything below this block dereferences it - the VRF, the
	       * counters, the trace. bpa_ftp.c and bpa_rtsp both re-resolve
	       * for this reason and say so; PPTP was the one that did not
	       * (#207).
	       */
	      u32 s_index = (u32) (s - pt->sessions);

	      switch (bpa_pptp_in (pt, b[0], tcp, s, now))
		{
		case BPA_PPTP_MANGLED:
		  n_pptp++;
		  break;
		case BPA_PPTP_UNKNOWN:
		  n_pptp_unknown++;
		  break;
		case BPA_PPTP_MALFORMED:
		  n_pptp_bad++;
		  break;
		default:
		  break;
		}
	      /* Re-resolved: the ALG may have moved the pool under us. */
	      s = pool_elt_at_index (pt->sessions, s_index);
	    }
	}

      /*
       * Back into the VRF the subscriber's traffic came from.
       *
       * Without this the reply is looked up in the FIB of the interface it
       * arrived on - the outside one - and on a box where inside and outside
       * are different VRFs it is dropped for having no route, which looks
       * like the CGNAT failing to translate rather than a lookup in the wrong
       * table.
       */
      vnet_buffer (b[0])->sw_if_index[VLIB_TX] = s->fib_index;

      bpa_rewrite_dst (ip, l4h, (u8) proto, s->ip_priv, s->port_priv);
      n_translated++;

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

	  /*
	   * The remote acknowledges the subscriber's stream as an ALG
	   * lengthened it. The subscriber only knows its own numbering, so the
	   * acknowledgement is shifted back here. Without it the subscriber
	   * sees bytes it never sent acknowledged, and the connection stalls a
	   * few messages after the rewrite - which does not look like the ALG,
	   * and does not look like NAT either.
	   */
	  if (PREDICT_FALSE (s->adj[BPA_DIR_OUT].off_after
			     | s->adj[BPA_DIR_OUT].off_before
			     | s->adj[BPA_DIR_IN].off_after
			     | s->adj[BPA_DIR_IN].off_before))
	    n_seq_adj += bpa_tcp_seq_adjust (tcp, &s->adj[BPA_DIR_IN],
					     &s->adj[BPA_DIR_OUT]);
	}

      /*
       * SIP ALG, internet -> subscriber: the response, whose topmost Via is
       * the one this box rewrote on the way out. Left as it arrived, the
       * phone discards its own answers (RFC 3261 s18.1.2) and every outbound
       * call rings nowhere.
       *
       * Matched on the SOURCE port, or on the subscriber's own private port -
       * never on udp->dst_port, which bpa_rewrite_dst has already replaced
       * with the private one by the time this runs.
       */
      if (PREDICT_FALSE (proto == BPA_PROTO_UDP && l4h != 0))
	{
	  udp_header_t *udp = l4h;
	  bpa_pool_t *sp = pool_elt_at_index (bm->pools, pool_index);

	  if (bpa_alg_on (sp->alg, BPA_ALG_SIP)
	      && (clib_net_to_host_u16 (udp->src_port) == BPA_SIP_PORT
		  || s->port_priv == BPA_SIP_PORT))
	    {
	      switch (bpa_sip_out2in (vm, b[0], ip, udp, s))
		{
		case BPA_ALG_DONE:
		  n_sip++;
		  break;
		case BPA_ALG_UNPARSED:
		  vlib_node_increment_counter (
		    vm, node->node_index, BPA_OUT2IN_ERROR_SIP_UNPARSED, 1);
		  break;
		case BPA_ALG_PARTIAL:
		  vlib_node_increment_counter (
		    vm, node->node_index, BPA_OUT2IN_ERROR_SIP_PARTIAL, 1);
		  break;
		case BPA_ALG_NO_ROOM:
		  vlib_node_increment_counter (
		    vm, node->node_index, BPA_OUT2IN_ERROR_SIP_NO_ROOM, 1);
		  break;
		default:
		  break;
		}
	    }
	}

      bpa_next_after (&next[0], b[0], BPA_OUT2IN_NEXT_LOOKUP);

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
			       BPA_OUT2IN_ERROR_FORWARDED, n_forwarded);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_OUT2IN_ERROR_MSS_CLAMPED, n_mss);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_OUT2IN_ERROR_SEQ_ADJUSTED, n_seq_adj);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_OUT2IN_ERROR_RTSP_REWRITTEN, n_rtsp);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_OUT2IN_ERROR_SIP_REWRITTEN, n_sip);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_OUT2IN_ERROR_PPTP_MANGLED, n_pptp);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_OUT2IN_ERROR_PPTP_UNKNOWN, n_pptp_unknown);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_OUT2IN_ERROR_PPTP_MALFORMED, n_pptp_bad);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_OUT2IN_ERROR_ESP_TRANSLATED, n_esp);
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
    [BPA_OUT2IN_NEXT_LOOKUP] = "ip4-lookup",
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
