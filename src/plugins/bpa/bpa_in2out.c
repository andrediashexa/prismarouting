/*
 * bpa_in2out.c - subscriber -> internet translation.
 *
 * Runs on the ip4-unicast feature arc of the inside interfaces.
 */

#include <vnet/ip/icmp46_packet.h>
#include <vnet/udp/udp_packet.h>
#include <vnet/tcp/tcp_packet.h>

#include "bpa.h"
#include "bpa_errors.h"
#include "bpa_esp.h"
#include "bpa_ftp.h"
#include "bpa_l4.h"
#include "bpa_log.h"
#include "bpa_alg.h"
#include "bpa_mss.h"
#include "bpa_pptp.h"

typedef struct
{
  u32 ip_priv, ip_pub;
  u16 port_priv, port_pub;
  u8 proto;
  u8 created;
  u8 bypassed;
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

  /* Said in words, because "-> 0.0.0.0:0" is what an untranslated packet
   * would otherwise look like in a trace, and that reads as a failure. */
  if (t->bypassed)
    return format (s, "BPA in2out %U:%u proto %U bypassed, not translated",
		   format_ip4_address, &priv, t->port_priv, format_bpa_proto,
		   t->proto);

  return format (s, "BPA in2out %U:%u -> %U:%u proto %U%s%s",
		 format_ip4_address, &priv, t->port_priv, format_ip4_address,
		 &pub, t->port_pub, format_bpa_proto, t->proto,
		 t->created ? " (new session)" : "",
		 t->is_frag ? " [non-first fragment]" : "");
}

typedef enum
{
#define _(sym, drop, str) BPA_IN2OUT_ERROR_##sym,
  foreach_bpa_in2out_error
#undef _
    BPA_IN2OUT_N_ERROR,
} bpa_in2out_error_t;

static char *bpa_in2out_error_strings[] = {
#define _(sym, drop, str) str,
  foreach_bpa_in2out_error
#undef _
};

typedef enum
{
  BPA_IN2OUT_NEXT_DROP,
  BPA_IN2OUT_NEXT_LOOKUP,
  BPA_IN2OUT_NEXT_HAIRPIN,
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

  /* Nothing below the IP header to touch: a non-first fragment, whose ports
   * and L4 checksum live in the first one, or ESP, which has neither. Only
   * the address changes. */
  if (PREDICT_FALSE (l4h == 0))
    {
      ip->src_address.as_u32 = clib_host_to_net_u32 (new_addr);
      return;
    }

  /*
   * GRE going out carries the Call ID the SERVER assigned, and the server
   * expects to see it come back unchanged. Only the address is ours to
   * rewrite here - the Call ID this box allocated appears in the other
   * direction, which is where bpa_rewrite_dst() puts it.
   */
  if (proto == BPA_PROTO_GRE)
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

/*
 * Lets a packet past untranslated, and says so.
 *
 * Returns 1 when the packet was bypassed, which the caller turns into "hand it
 * to the next feature" - no session, no port, no log record. A bypass that
 * allocates first is not a bypass.
 */
static_always_inline int
bpa_bypass_packet (bpa_main_t *bm, bpa_per_thread_t *pt, u32 pool_index,
		   u32 src, u32 dst, u32 *n_src, u32 *n_dst)
{
  const bpa_pool_t *p = pool_elt_at_index (bm->pools, pool_index);
  u32 idx;

  if (PREDICT_TRUE (p->bypass_src.count == 0 && p->bypass_dst.count == 0))
    return 0;

  /* Counted apart: "this customer is not translated" and "this destination is
   * not translated" are different configurations, and an operator looking at
   * one number cannot tell which of them is in force. */
  if (bpa_set_index_of (&p->bypass_src, src, &idx) == 0)
    (*n_src)++;
  else if (bpa_set_index_of (&p->bypass_dst, dst, &idx) == 0)
    (*n_dst)++;
  else
    return 0;

  if (PREDICT_TRUE (pool_index < vec_len (pt->n_bypassed)))
    pt->n_bypassed[pool_index]++;
  return 1;
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
  u32 n_translated = 0, n_new = 0, n_forwarded = 0, n_mss = 0;
  u32 n_bypass_src = 0, n_bypass_dst = 0;
  u32 n_hairpin = 0;
  u32 n_ftp = 0, n_ftp_untouched = 0, n_ftp_noport = 0, n_seq_adj = 0;
  u32 n_rtsp = 0, n_sip = 0;
  u32 n_pptp = 0, n_pptp_unknown = 0, n_pptp_nocid = 0, n_pptp_bad = 0;
  u32 n_esp = 0;

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
      u32 pool_index = 0, sub_idx = 0, src_host, dst_host;
      /* The VRF the packet arrived in. Read here rather than after the pool
       * is chosen, because the session lookup happens first and its key needs
       * it: one vector element, on the same buffer the node already has. */
      u32 fib_index = vlib_buffer_get_ip4_fib_index (b[0]);
      u16 port_priv = 0;
      bpa_create_why_t why = BPA_CREATE_OK;
      int proto, created = 0, bypassed = 0;

      next[0] = BPA_IN2OUT_NEXT_DROP;
      /* captured before any rewrite: the trace must show the pre-NAT source */
      src_host = clib_net_to_host_u32 (ip->src_address.as_u32);
      /* The peer. Read here because it is the ESP session key, and that key
       * is built before the pool is known, exactly as the VRF is. */
      dst_host = clib_net_to_host_u32 (ip->dst_address.as_u32);

      proto = bpa_proto_of (ip->protocol);
      if (PREDICT_FALSE (proto < 0))
	{
	  b[0]->error = node->errors[BPA_IN2OUT_ERROR_UNSUPPORTED];
	  goto trace;
	}

      if (PREDICT_FALSE (bpa_l4_src (b[0], ip, proto, &port_priv, &l4h)))
	{
	  /* Plain GRE has no per-flow identifier, so several subscribers
	   * behind one address cannot be told apart and it is dropped rather
	   * than sent out with a source nobody can reply to. Saying which of
	   * the two happened is the whole value of a separate counter. */
	  b[0]->error =
	    node->errors[proto == BPA_PROTO_GRE
			   ? BPA_IN2OUT_ERROR_GRE_NOT_ENHANCED
			   : BPA_IN2OUT_ERROR_TRUNCATED];
	  goto trace;
	}

      /*
       * GRE is keyed by the server's Call ID together with the server's
       * address, and the mapping is only ever created by the PPTP ALG on the
       * control connection. No session here means data arrived for a call
       * that was never announced - or was announced while the ALG was off.
       */
      if (PREDICT_FALSE (proto == BPA_PROTO_GRE))
	{
	  bpa_key_gre (&kv, src_host, port_priv,
		       clib_net_to_host_u32 (ip->dst_address.as_u32),
		       BPA_GRE_KEY_DATA, fib_index);
	  if (PREDICT_TRUE (
		clib_bihash_search_16_8 (&pt->in2out, &kv, &value) == 0))
	    {
	      s = pool_elt_at_index (pt->sessions, value.value);
	      goto translate;
	    }
	  /* Before calling it a missing call: a subscriber whose PPTP server
	   * is on a bypassed destination has a control channel that was never
	   * translated, so there is no call here to find and never will be. */
	  if (PREDICT_FALSE (
		bpa_pool_by_inside (bm, src_host, fib_index, &pool_index,
				    &sub_idx) == 0
		&& bpa_bypass_packet (
		  bm, pt, pool_index, src_host,
		  clib_net_to_host_u32 (ip->dst_address.as_u32), &n_bypass_src,
		  &n_bypass_dst)))
	    {
	      bypassed = 1;
	      bpa_next_after (&next[0], b[0], BPA_IN2OUT_NEXT_LOOKUP);
	      goto trace;
	    }
	  b[0]->error = node->errors[BPA_IN2OUT_ERROR_GRE_NO_CALL];
	  goto trace;
	}

      /*
       * ESP is keyed on the peer rather than on a port, because it has none.
       * One session per (subscriber, peer): a subscriber with two tunnels is
       * an ordinary home worker, and one entry for both would have to be torn
       * down when either of them ended.
       */
      if (PREDICT_FALSE (proto == BPA_PROTO_ESP))
	bpa_key_esp (&kv, src_host, dst_host, fib_index);
      else
	bpa_key_in (&kv, src_host, port_priv, (u8) proto, fib_index);
      if (clib_bihash_search_16_8 (&pt->in2out, &kv, &value) == 0)
	{
	  s = pool_elt_at_index (pt->sessions, value.value);
	  goto translate;
	}

      if (PREDICT_FALSE (
	    bpa_pool_by_inside (bm, src_host, fib_index, &pool_index,
				&sub_idx)))
	{
	  b[0]->error = node->errors[BPA_IN2OUT_ERROR_NO_POOL];
	  goto trace;
	}

      /*
       * Untranslated traffic, decided before anything is allocated.
       *
       * Here rather than earlier because the pool is only known now, and a
       * pool lookup on every packet would be paid by traffic that is not
       * bypassed - which is nearly all of it. A bypassed flow has no session
       * to hit, so it reaches this point on every packet and pays the lookup
       * each time; that is the cost of allocating nothing for it.
       */
      if (PREDICT_FALSE (bpa_bypass_packet (bm, pt, pool_index, src_host,
					    clib_net_to_host_u32 (
					      ip->dst_address.as_u32),
					    &n_bypass_src, &n_bypass_dst)))
	{
	  bypassed = 1;
	  bpa_next_after (&next[0], b[0], BPA_IN2OUT_NEXT_LOOKUP);
	  goto trace;
	}

      p = pool_elt_at_index (bm->pools, pool_index);

      /*
       * ESP with passthrough switched off does what this box did before the
       * feature existed: nothing, and the packet is dropped. Under a counter
       * of its own, because "the operator turned it off" and "this box does
       * not translate that protocol" send a support call to two different
       * places.
       */
      if (PREDICT_FALSE (proto == BPA_PROTO_ESP
			 && !bpa_alg_on (p->alg, BPA_ALG_ESP)))
	{
	  b[0]->error = node->errors[BPA_IN2OUT_ERROR_ESP_OFF];
	  goto trace;
	}

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

      /*
       * A configured forward on this subscriber's own socket.
       *
       * Consulted before the allocator, not after it. The forwarded public
       * port is reserved and the allocator would hand out a different one,
       * and then the subscriber's outbound traffic would leave on one port
       * while the operator's forward pointed at another - one socket with two
       * public ports, which is also what endpoint-independent mapping forbids
       * (RFC 4787 REQ-1).
       */
      if (PREDICT_FALSE (vec_len (p->statics) != 0))
	{
	  const bpa_static_t *sf =
	    bpa_static_by_priv (p, &blk, src_host, port_priv, (u8) proto);

	  if (sf)
	    {
	      s = bpa_session_static_open (pt, p, pool_index, sf, now);
	      if (s)
		n_forwarded++;
	    }
	}

      /*
       * An IPsec tunnel, which takes no port and so cannot come out of the
       * ordinary create path. The refusal that is specific to it - the peer
       * already belonging to a neighbour on the same public address - is
       * answered here and not shared with the port refusals below, because
       * the operator's response to it is different: nothing about this
       * subscriber's block will fix it.
       */
      if (PREDICT_FALSE (proto == BPA_PROTO_ESP))
	{
	  s = bpa_session_create_esp (pt, p, pool_index, &blk, dst_host,
				      fib_index, now, &why);
	  if (PREDICT_FALSE (s == 0 && why == BPA_CREATE_ESP_PEER_TAKEN))
	    {
	      b[0]->error = node->errors[BPA_IN2OUT_ERROR_ESP_PEER_TAKEN];
	      goto trace;
	    }
	  if (PREDICT_TRUE (s != 0))
	    n_esp++;
	}
      else if (PREDICT_TRUE (s == 0))
	s = bpa_session_create (pt, p, pool_index, &blk, port_priv,
				(u8) proto, fib_index, now,
				BPA_CREATE_TRAFFIC, &why);

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
      /*
       * Another block, only when the block was the problem. After a cap
       * refusal an extension would hand a block to the very subscriber the
       * operator has just told the box to bound.
       */
      if (PREDICT_FALSE (s == 0 && why == BPA_CREATE_NO_PORT
			 && p->mode == BPA_MODE_DYNAMIC
			 && bpa_sub_n_blocks (pt, pool_index, sub_idx)
			      < p->max_blocks))
	{
	  u32 w, nw;
	  int is_new = 0;
	  bpa_block_t ext;

	  bpa_worker_id (bm, thread_index, &w, &nw);
	  if (pool_index < vec_len (pt->dyn)
	      && bpa_dyn_acquire_extra (vec_elt_at_index (pt->dyn, pool_index),
					p, sub_idx, w, nw, &ext, &is_new) == 0)
	    {
	      bpa_sub_extend (pt, p, pool_index, sub_idx, &ext);
	      /* An extension is not derivable from anything, in either mode.
	       * A block the log does not record cannot be attributed to
	       * anyone, which is the whole point of recording blocks. */
	      bpa_log_queue_event (pt, BPA_NAT_EVENT_BLOCK_ALLOC, src_host,
				   ext.ip_pub, ext.port_lo, ext.port_hi,
				   pool_index);
	      s = bpa_session_create (pt, p, pool_index, &ext, port_priv,
				      (u8) proto, fib_index, now,
				      BPA_CREATE_TRAFFIC, &why);
	    }
	}

      if (PREDICT_FALSE (s == 0))
	{
	  /* Three different answers for the operator: buy the customer more
	   * ports, raise their cap, or go and look at their machine. One
	   * counter for all three would say none of them. */
	  b[0]->error =
	    node->errors[why == BPA_CREATE_OVER_SESSIONS
			   ? BPA_IN2OUT_ERROR_OVER_SESSIONS
			 : why == BPA_CREATE_OVER_RATE
			   ? BPA_IN2OUT_ERROR_OVER_RATE
			   : BPA_IN2OUT_ERROR_BLOCK_EXHAUSTED];
	  goto trace;
	}
      created = 1;
      n_new++;

    translate:
      /*
       * The switch, re-read on a tunnel that already has a binding.
       *
       * The inbound direction checks it on every packet because the pool is
       * already in its hand there, and without this the two directions would
       * disagree: an operator who turned passthrough off would watch ESP keep
       * leaving while the replies were dropped, which is a one-way tunnel and
       * a support call that looks like anything but a switch.
       */
      if (PREDICT_FALSE (proto == BPA_PROTO_ESP
			 && !pool_is_free_index (bm->pools, s->pool_index)
			 && !bpa_alg_on (
			   pool_elt_at_index (bm->pools, s->pool_index)->alg,
			   BPA_ALG_ESP)))
	{
	  b[0]->error = node->errors[BPA_IN2OUT_ERROR_ESP_OFF];
	  goto trace;
	}

      s->last_heard = now;
      /* A mapping an ALG predicted has now been used, so it goes back on the
       * protocol's own lifetime. Left as an expectation it would expire in a
       * minute in the middle of a call. */
      s->is_expect = 0;
      /* Remembered on every outbound packet, not only at creation: a session
       * that moves to a new remote must be able to hear the new one reply.
       * Free under endpoint-independent filtering, which never reads it. */
      s->remote = clib_net_to_host_u32 (ip->dst_address.as_u32);
      if (proto == BPA_PROTO_TCP)
	{
	  tcp_header_t *tcp = l4h;

	  bpa_tcp_track (s, tcp->flags);

	  /*
	   * MSS clamping, on the handshake only.
	   *
	   * Before the address rewrite or after it makes no difference: both
	   * are incremental updates of the same checksum field, and neither
	   * reads what the other wrote.
	   */
	  if (PREDICT_FALSE ((tcp->flags & TCP_FLAG_SYN) != 0))
	    {
	      const bpa_pool_t *mp =
		pool_elt_at_index (bm->pools, s->pool_index);
	      u16 mss = mp->tcp_mss_auto ? bm->auto_mss : mp->tcp_mss;

	      if (mss)
		n_mss += bpa_tcp_mss_clamp (
		  tcp, (u8 *) vlib_buffer_get_current (b[0])
			 + b[0]->current_length,
		  mss);
	    }

	  /*
	   * The PPTP control channel. Rewritten BEFORE the address and port,
	   * because both fixes are incremental on the same checksum and the
	   * ALG has to read the payload as the subscriber wrote it.
	   */
	  if (PREDICT_FALSE (
		clib_net_to_host_u16 (tcp->dst_port) == BPA_PPTP_PORT
		&& !pool_is_free_index (bm->pools, s->pool_index)
		&& bpa_alg_on (
		  pool_elt_at_index (bm->pools, s->pool_index)->alg,
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

	      switch (bpa_pptp_out (pt, b[0], tcp, s, now))
		{
		case BPA_PPTP_MANGLED:
		  n_pptp++;
		  break;
		case BPA_PPTP_UNKNOWN:
		  n_pptp_unknown++;
		  break;
		case BPA_PPTP_NO_CALL_ID:
		  n_pptp_nocid++;
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

      /* Out by the pool's outside VRF, where it names one. Read from the
       * session rather than the pool: the answer cannot change for the life
       * of the session, and looking it up per packet would be a pool lookup
       * on the translated path to learn something already known. */
      if (PREDICT_FALSE (s->out_fib_index != ~0u))
	vnet_buffer (b[0])->sw_if_index[VLIB_TX] = s->out_fib_index;

      bpa_rewrite_src (ip, l4h, (u8) proto, s->ip_pub, s->port_pub);
      n_translated++;

      /*
       * FTP ALG. Runs on the already-translated packet, so the address it
       * writes into the payload is the one the subscriber now appears to
       * have.
       *
       * The rewrite comes BEFORE the sequence shift on purpose. The rewrite
       * records its own length change against the sequence number the
       * subscriber sent, and shifting first would record it against a number
       * the subscriber never used - after which every later segment of that
       * connection is renumbered from the wrong origin.
       */
      if (PREDICT_FALSE (proto == BPA_PROTO_TCP && l4h != 0
			 && bpa_ftp_is_control (l4h, s)))
	{
	  if (!pool_is_free_index (bm->pools, s->pool_index))
	    {
	      bpa_pool_t *fp = pool_elt_at_index (bm->pools, s->pool_index);

	      if (bpa_alg_on (fp->alg, BPA_ALG_FTP))
		switch (bpa_ftp_in2out (vm, b[0], ip, pt, fp, &s, now))
		  {
		  case BPA_FTP_REWRITTEN:
		    n_ftp++;
		    break;
		  case BPA_FTP_UNTOUCHED:
		    n_ftp_untouched++;
		    break;
		  case BPA_FTP_NO_PORT:
		    n_ftp_noport++;
		    break;
		  case BPA_FTP_NOTHING:
		    break;
		  }
	    }

	}

      /*
       * RTSP ALG, subscriber -> internet.
       *
       * Runs after the address rewrite, so the checksum it recomputes covers
       * the pseudo-header the packet will actually travel with, and before the
       * sequence correction, because the correction point it records is in the
       * sender's own coordinates.
       *
       * The pool is re-fetched here: on the fast path above, `p` is only set
       * when the session was created by this packet.
       */
      if (PREDICT_FALSE (proto == BPA_PROTO_TCP && l4h != 0))
	{
	  tcp_header_t *tcp = l4h;
	  bpa_pool_t *sp = pool_is_free_index (bm->pools, s->pool_index)
			     ? 0
			     : pool_elt_at_index (bm->pools, s->pool_index);

	  if (sp && bpa_alg_on (sp->alg, BPA_ALG_RTSP)
	      && clib_net_to_host_u16 (tcp->dst_port) == BPA_RTSP_PORT)
	    {
	      u32 s_index = s - pt->sessions;

	      switch (bpa_rtsp_in2out (vm, b[0], ip, tcp, pt, sp, s_index, now))
		{
		case BPA_ALG_DONE:
		  n_rtsp++;
		  break;
		case BPA_ALG_UNPARSED:
		  vlib_node_increment_counter (
		    vm, node->node_index, BPA_IN2OUT_ERROR_RTSP_UNPARSED, 1);
		  break;
		case BPA_ALG_PARTIAL:
		  vlib_node_increment_counter (
		    vm, node->node_index, BPA_IN2OUT_ERROR_RTSP_PARTIAL, 1);
		  break;
		case BPA_ALG_NO_ROOM:
		  vlib_node_increment_counter (
		    vm, node->node_index, BPA_IN2OUT_ERROR_RTSP_NO_ROOM, 1);
		  break;
		case BPA_ALG_NO_PORTS:
		  vlib_node_increment_counter (
		    vm, node->node_index, BPA_IN2OUT_ERROR_RTSP_NO_PORTS, 1);
		  break;
		default:
		  break;
		}
	      /* Opening the media sessions may have reallocated the pool. */
	      s = pool_elt_at_index (pt->sessions, s_index);
	    }

	  /*
	   * Every later segment of a connection an ALG has rewritten, in
	   * either direction. Zero on every session no ALG has touched.
	   *
	   * Applied even when the ALG is off for the pool: a connection this
	   * box already lengthened must keep being renumbered, or turning the
	   * knob off stalls the very sessions the operator was trying to stop
	   * interfering with.
	   */
	  if (PREDICT_FALSE (s->adj[BPA_DIR_OUT].off_after
			     | s->adj[BPA_DIR_OUT].off_before
			     | s->adj[BPA_DIR_IN].off_after
			     | s->adj[BPA_DIR_IN].off_before))
	    n_seq_adj += bpa_tcp_seq_adjust (tcp, &s->adj[BPA_DIR_OUT],
					     &s->adj[BPA_DIR_IN]);
	}

      /*
       * SIP ALG, subscriber -> internet. UDP only - see the transports note
       * at the head of bpa_sip.h.
       *
       * Same ordering as the two above: after the address rewrite, so the
       * address written into the payload is the one the subscriber now appears
       * to have and the checksum covers the pseudo-header the datagram will
       * travel with. Nothing follows it, because a datagram has no sequence
       * numbers to correct.
       *
       * Matched on the DESTINATION port, or on the subscriber's own PRIVATE
       * port - never on udp->src_port, which bpa_rewrite_src has already
       * replaced with the public one by the time this runs. The private port
       * is what catches the other half of SIP: a phone listening on 5060 sends
       * its responses and its in-dialog requests FROM 5060 to wherever the
       * far end asked to be answered, which is very often not 5060.
       */
      if (PREDICT_FALSE (proto == BPA_PROTO_UDP && l4h != 0))
	{
	  udp_header_t *udp = l4h;
	  bpa_pool_t *sp = pool_is_free_index (bm->pools, s->pool_index)
			     ? 0
			     : pool_elt_at_index (bm->pools, s->pool_index);

	  if (sp && bpa_alg_on (sp->alg, BPA_ALG_SIP)
	      && (clib_net_to_host_u16 (udp->dst_port) == BPA_SIP_PORT
		  || s->port_priv == BPA_SIP_PORT))
	    {
	      u32 s_index = s - pt->sessions;

	      switch (bpa_sip_in2out (vm, b[0], ip, udp, pt, sp, s_index, now))
		{
		case BPA_ALG_DONE:
		  n_sip++;
		  break;
		case BPA_ALG_UNPARSED:
		  vlib_node_increment_counter (
		    vm, node->node_index, BPA_IN2OUT_ERROR_SIP_UNPARSED, 1);
		  break;
		case BPA_ALG_PARTIAL:
		  vlib_node_increment_counter (
		    vm, node->node_index, BPA_IN2OUT_ERROR_SIP_PARTIAL, 1);
		  break;
		case BPA_ALG_NO_ROOM:
		  vlib_node_increment_counter (
		    vm, node->node_index, BPA_IN2OUT_ERROR_SIP_NO_ROOM, 1);
		  break;
		case BPA_ALG_NO_PORTS:
		  vlib_node_increment_counter (
		    vm, node->node_index, BPA_IN2OUT_ERROR_SIP_NO_PORTS, 1);
		  break;
		default:
		  break;
		}
	      /* Opening the media sessions may have reallocated the pool. */
	      s = pool_elt_at_index (pt->sessions, s_index);
	    }
	}

      /*
       * Hairpinning (RFC 4787 REQ-9): one subscriber reaching another through
       * its public address.
       *
       * Without this the packet leaves here correctly translated and is then
       * routed to an address this box itself owns, with nothing to send it
       * back in. The symptom is specific and hard to attribute: a camera that
       * answers from the street and not from next door, a service that works
       * for everyone except the neighbours.
       *
       * The source is already A's PUBLIC address at this point, which is what
       * REQ-9 requires - B must see A as external. Presenting A's private
       * address would leak the inside topology and break B's reply path,
       * because B would answer to an address it cannot route to.
       */
      {
	u32 hp_dst = clib_net_to_host_u32 (ip->dst_address.as_u32);
	u32 hp_pool;

	/* Two compares before the real lookup. The lookup is a scan over
	 * pools, each doing a binary search, and it ran on EVERY translated
	 * packet for a case that is rare. The window is a superset, so
	 * anything that could be a hairpin still reaches the lookup. */
	if (PREDICT_FALSE (bpa_maybe_outside (bm, hp_dst)
			   && bpa_pool_by_outside (bm, hp_dst, &hp_pool) == 0))
	  {
	    clib_bihash_kv_16_8_t hkv;
	    u16 hp_port = 0;

	    /*
	     * An IPsec tunnel between two subscribers of this same box. The
	     * neighbour's binding is keyed on the address it sees us as,
	     * which is the public one this packet now carries - the source
	     * was rewritten a few lines above.
	     *
	     * Its last_heard is deliberately not touched: the binding may
	     * belong to another worker's session, and a cross-worker write is
	     * the one thing this datapath does not do. The tunnel is kept
	     * alive by the neighbour's own outbound packets, which is where
	     * every other portless mapping gets its liveness from too.
	     */
	    if (PREDICT_FALSE (proto == BPA_PROTO_ESP))
	      {
		u32 peer_priv, peer_fib;

		if (bpa_esp_lookup (
		      hp_dst, clib_net_to_host_u32 (ip->src_address.as_u32),
		      &peer_priv, &peer_fib))
		  {
		    b[0]->error =
		      node->errors[BPA_IN2OUT_ERROR_HAIRPIN_NO_PEER];
		    goto trace;
		  }
		bpa_rewrite_dst (ip, 0, (u8) proto, peer_priv, 0);
		n_hairpin++;
		goto hp_done;
	      }

	    /*
	     * The peer's session is not ours to read.
	     *
	     * It lives in pt->out2in - the PER-THREAD table of whichever
	     * worker owns B - and this packet is on A's. Looked up here it
	     * misses, and the packet was dropped as "hairpin to a public
	     * address with no session", which is exactly what it looks like
	     * from this thread. With N workers that happened N-1 times out of
	     * N, so the same pair of neighbours worked or did not depending on
	     * which workers their addresses hashed to, with nothing in the
	     * configuration to explain it (#200).
	     *
	     * A hairpin needs TWO workers and there is no way around it: the
	     * source translation needs A's worker, for A's session and A's
	     * port bitmap, and the destination translation needs B's. So it is
	     * STEERED rather than looked up - handed to bpa-out2in-handoff,
	     * which resolves the owner of the destination by the same
	     * arithmetic every reply already uses, and bpa-out2in on that
	     * worker does the destination rewrite.
	     *
	     * That second half IS out2in: a hairpin is an inbound packet whose
	     * source happens to be one of ours. Reusing it rather than keeping
	     * a second copy of the peer lookup here is the whole point - the
	     * copy that used to be here was right on one thread and wrong on
	     * every other one.
	     *
	     * The source is already A's PUBLIC address, which REQ-9 requires
	     * and which the steering also needs: B replies to it and that
	     * reply is an ordinary out2in packet.
	     *
	     * Counted as hairpinned HERE, where it is recognised. A hairpin
	     * that finds nothing listening is dropped by out2in as "no session
	     * for destination", which on that thread is the truth; the
	     * in2out no-peer counter below now belongs to ESP alone.
	     */
	    n_hairpin++;
	    next[0] = BPA_IN2OUT_NEXT_HAIRPIN;
	    goto trace;
	  }
      hp_done:;
      }

      bpa_next_after (&next[0], b[0], BPA_IN2OUT_NEXT_LOOKUP);

    trace:
      if (PREDICT_FALSE (b[0]->flags & VLIB_BUFFER_IS_TRACED))
	{
	  bpa_in2out_trace_t *t =
	    vlib_add_trace (vm, node, b[0], sizeof (*t));
	  t->proto = (u8) (proto < 0 ? 0 : proto);
	  t->created = created;
	  t->bypassed = bypassed;
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
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_IN2OUT_ERROR_FORWARDED, n_forwarded);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_IN2OUT_ERROR_BYPASS_SRC, n_bypass_src);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_IN2OUT_ERROR_BYPASS_DST, n_bypass_dst);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_IN2OUT_ERROR_MSS_CLAMPED, n_mss);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_IN2OUT_ERROR_HAIRPINNED, n_hairpin);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_IN2OUT_ERROR_FTP_REWRITTEN, n_ftp);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_IN2OUT_ERROR_FTP_UNTOUCHED,
			       n_ftp_untouched);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_IN2OUT_ERROR_FTP_NO_PORT, n_ftp_noport);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_IN2OUT_ERROR_SEQ_ADJUSTED, n_seq_adj);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_IN2OUT_ERROR_RTSP_REWRITTEN, n_rtsp);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_IN2OUT_ERROR_SIP_REWRITTEN, n_sip);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_IN2OUT_ERROR_PPTP_MANGLED, n_pptp);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_IN2OUT_ERROR_PPTP_UNKNOWN, n_pptp_unknown);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_IN2OUT_ERROR_PPTP_NO_CALL_ID, n_pptp_nocid);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_IN2OUT_ERROR_PPTP_MALFORMED, n_pptp_bad);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_IN2OUT_ERROR_ESP_BOUND, n_esp);
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
    [BPA_IN2OUT_NEXT_LOOKUP] = "ip4-lookup",
    [BPA_IN2OUT_NEXT_HAIRPIN] = "bpa-out2in-handoff",
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
