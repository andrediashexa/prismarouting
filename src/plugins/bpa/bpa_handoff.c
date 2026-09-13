/*
 * bpa_handoff.c - steers a packet to the worker that owns the subscriber.
 *
 * Session state and the per-subscriber port bitmap are per-worker with no
 * locking, which is only safe if BOTH directions of a subscriber land on the
 * same worker. The NIC's RSS gives no such guarantee: the outbound packet
 * hashes on the private address and the reply on the public one.
 *
 * So each direction resolves the subscriber's dense index and hands the
 * buffer to bpa_owner_worker() of that index:
 *
 *   in2out  - the index comes straight from the source address
 *   out2in  - bpa_reverse_sub() recovers it from (public address, port) by
 *             arithmetic alone, with no shared table to consult
 *
 * That second line is the reason the deterministic mapping matters beyond
 * logging: without it the return path could not find its owner without a
 * lookup in a structure shared across workers.
 *
 * It also says where the difficulty is. The pair has to come from the packet,
 * and an ICMP error does not carry one in its outer header - it is a router
 * reporting on somebody else's datagram, and the datagram is inside. So the
 * out2in side reads the embedded header (bpa_icmp.h), with the same parse
 * bpa-out2in will use on the worker this sends it to.
 */

#include "bpa.h"
#include "bpa_icmp.h"
#include "bpa_l4.h"

typedef struct
{
  u32 sub_idx;
  u16 owner;
  u16 from_thread;
  u8 matched;
} bpa_handoff_trace_t;

static u8 *
format_bpa_handoff_trace (u8 *s, va_list *args)
{
  CLIB_UNUSED (vlib_main_t * vm) = va_arg (*args, vlib_main_t *);
  CLIB_UNUSED (vlib_node_t * node) = va_arg (*args, vlib_node_t *);
  bpa_handoff_trace_t *t = va_arg (*args, bpa_handoff_trace_t *);

  if (!t->matched)
    return format (s, "BPA handoff: no subscriber, staying on thread %u",
		   t->from_thread);

  return format (s, "BPA handoff: subscriber %u, thread %u -> %u", t->sub_idx,
		 t->from_thread, t->owner);
}

#define foreach_bpa_handoff_error                                             \
  _ (HANDED_OFF, "packets handed to another worker")                          \
  _ (SAME_WORKER, "packets already on owning worker")                                \
  _ (NO_OWNER, "packets with no identifiable subscriber")                         \
  _ (CONGESTED, "packets dropped by congestion")

typedef enum
{
#define _(sym, str) BPA_HANDOFF_ERROR_##sym,
  foreach_bpa_handoff_error
#undef _
    BPA_HANDOFF_N_ERROR,
} bpa_handoff_error_t;

static char *bpa_handoff_error_strings[] = {
#define _(sym, str) str,
  foreach_bpa_handoff_error
#undef _
};

static_always_inline uword
bpa_handoff_inline (vlib_main_t *vm, vlib_node_runtime_t *node,
		    vlib_frame_t *frame, u32 fq_index, int is_in2out)
{
  bpa_main_t *bm = &bpa_main;
  u32 n_left_from, *from;
  vlib_buffer_t *bufs[VLIB_FRAME_SIZE], **b = bufs;
  u16 thread_indices[VLIB_FRAME_SIZE], *ti = thread_indices;
  u32 this_thread = vm->thread_index;
  u32 n_handed = 0, n_same = 0, n_no_owner = 0, n_enq;

  from = vlib_frame_vector_args (frame);
  n_left_from = frame->n_vectors;
  vlib_get_buffers (vm, from, bufs, n_left_from);

  while (n_left_from > 0)
    {
      ip4_header_t *ip = vlib_buffer_get_current (b[0]);
      u32 sub_idx = 0, pool_index = 0;
      int matched = 0;

      if (is_in2out)
	{
	  u32 src = clib_net_to_host_u32 (ip->src_address.as_u32);
	  /* The handoff has to agree with in2out about which pool owns this
	   * source, VRF included: otherwise a packet is sent to the worker one
	   * pool would own it on, and translated there by another. */
	  matched = (bpa_pool_by_inside (bm, src,
					 vlib_buffer_get_ip4_fib_index (b[0]),
					 &pool_index, &sub_idx)
		     == 0);
	}
      else
	{
	  u32 dst = clib_net_to_host_u32 (ip->dst_address.as_u32);

	  /* The same gate bpa-out2in opens with, and it stays first for the
	   * same reason it is first there: a packet not addressed to one of
	   * our public addresses is none of this node's business, and nothing
	   * further inside it may decide which worker it lands on. */
	  if (bpa_pool_by_outside (bm, dst, &pool_index) == 0)
	    {
	      int proto = bpa_proto_of (ip->protocol);
	      bpa_icmp_err_t e;
	      u32 pub = dst;
	      void *l4h;
	      u16 port;
	      int have_id = 1;

	      /*
	       * An ICMP error has no port of its own - the outer header is a
	       * router talking to our public address and nothing more - so
	       * the pair that names the session is inside the packet embedded
	       * in it. Read it here, or the error is left on whatever worker
	       * the NIC chose and bpa-out2in looks for the session in a table
	       * that was never going to hold it (#201).
	       *
	       * The same parse bpa-out2in will use, from bpa_icmp.h, so the
	       * worker this picks is by construction the worker that node
	       * searches. A truncated or unkeyable error is refused here
	       * exactly as it is refused there: it stays put, and the
	       * translation node is the one that counts it.
	       *
	       * Only for a first fragment, because that is the only case
	       * where bpa-out2in takes the embedded path at all. A later
	       * fragment carries no embedded header to read, and steering it
	       * on one parsed out of payload bytes would send it where the
	       * other node does not look.
	       */
	      if (PREDICT_FALSE (
		    proto == BPA_PROTO_ICMP
		    && !vnet_buffer (b[0])->ip.reass.is_non_first_fragment
		    && bpa_icmp_err_parse (ip,
					   (u8 *) vlib_buffer_get_current (b[0])
					     + b[0]->current_length,
					   &e)
			 == 0))
		{
		  /* The embedded SOURCE, not the outer destination. RFC 792
		   * makes them the same address, and when a sender disagrees
		   * it is the embedded one that keys the session - so it is
		   * the embedded one that has to choose the worker.
		   *
		   * Unless there is no port. An error about an outbound ESP
		   * or PPTP GRE packet parses, and parses correctly, but what
		   * it names is in the table shared between workers - so
		   * there is no owner to steer to and the packet is right
		   * where it needs to be. Left unmatched EXPLICITLY:
		   * bpa_reverse_sub() would also fail on a port of zero,
		   * because zero is in no subscriber's block, and arriving at
		   * the right answer because an unrelated check happens to
		   * reject the input is how a wrong answer arrives the day
		   * that check changes.
		   *
		   * GRE is the case that proves the point rather than an
		   * extra one: it HAS an identifier, the server's Call ID,
		   * and e->call_id carries it - but it is not drawn from the
		   * subscriber's port block, so bpa_reverse_sub() cannot turn
		   * it into an owner. Steering on it would send the error to
		   * a worker that does not hold the call (#203). */
		  if (e.port)
		    {
		      pub = e.pub_addr;
		      port = e.pub_port;
		    }
		  else
		    have_id = 0;
		}
	      /*
	       * ESP comes out of here unmatched, and that is the design
	       * rather than a gap: it carries no port, so bpa_reverse_sub()
	       * has nothing to work from and no rule computable from the
	       * packet alone would put the two directions of a tunnel on one
	       * worker. bpa_l4_dst() hands it port 0, which is outside every
	       * block, so the arithmetic below refuses it and it stays on
	       * whatever thread it arrived on - where bpa-out2in resolves it
	       * in the shared binding table that exists for exactly this
	       * reason (bpa_esp.h).
	       */
	      else if (proto < 0
		       || bpa_l4_dst (b[0], ip, (u8) proto, &port, &l4h) != 0)
		have_id = 0; /* nothing in this packet names a subscriber */

	      /* A second pool lookup only when the embedded header named a
	       * different public address from the outer one, which is a
	       * sender disagreeing with RFC 792. The ordinary packet, and the
	       * ordinary ICMP error, pay nothing for this. */
	      if (have_id
		  && (pub == dst
		      || bpa_pool_by_outside (bm, pub, &pool_index) == 0))
		{
		  bpa_pool_t *p = pool_elt_at_index (bm->pools, pool_index);
		  matched = (bpa_reverse_sub (p, pub, port, &sub_idx) == 0);
		  /*
		   * A port in a deterministic pool's reserve names no
		   * subscriber by arithmetic, which is why bpa_reverse_sub()
		   * refuses it - but it still has to reach the worker holding
		   * the block, and the only thing this node can read off the
		   * packet is the reserve index.
		   *
		   * That works because the allocator is constrained to match:
		   * worker w hands out only the reserve indices congruent to
		   * w, exactly as it owns only the dynamic blocks congruent to
		   * w, and a subscriber is owned by sub_idx % n_workers. So
		   * steering on the reserve index lands on the same thread the
		   * subscriber's own outbound packets do. Without this the
		   * reply stays where the NIC put it and bpa-out2in searches a
		   * session table that was never going to hold it.
		   */
		  if (!matched)
		    matched =
		      (bpa_reserve_index_of (p, pub, port, &sub_idx) == 0);
		}
	    }
	}

      if (matched)
	{
	  u32 owner =
	    bm->n_workers
	      ? vlib_get_worker_thread_index (
		  bpa_owner_worker (&bm->workers, sub_idx))
	      : 0;
	  ti[0] = (u16) owner;
	  if (owner == this_thread)
	    n_same++;
	  else
	    n_handed++;
	}
      else
	{
	  /* Nothing to translate: leave it where it is and let the
	   * translation node account for it. */
	  ti[0] = (u16) this_thread;
	  n_no_owner++;
	}

      if (PREDICT_FALSE (b[0]->flags & VLIB_BUFFER_IS_TRACED))
	{
	  bpa_handoff_trace_t *t = vlib_add_trace (vm, node, b[0], sizeof (*t));
	  t->sub_idx = sub_idx;
	  t->owner = ti[0];
	  t->from_thread = (u16) this_thread;
	  t->matched = (u8) matched;
	}

      b++;
      ti++;
      n_left_from--;
    }

  n_enq = vlib_buffer_enqueue_to_thread (vm, node, fq_index, from,
					 thread_indices, frame->n_vectors, 1);

  if (PREDICT_FALSE (n_enq < frame->n_vectors))
    vlib_node_increment_counter (vm, node->node_index,
				 BPA_HANDOFF_ERROR_CONGESTED,
				 frame->n_vectors - n_enq);

  vlib_node_increment_counter (vm, node->node_index,
			       BPA_HANDOFF_ERROR_HANDED_OFF, n_handed);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_HANDOFF_ERROR_SAME_WORKER, n_same);
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_HANDOFF_ERROR_NO_OWNER, n_no_owner);
  return frame->n_vectors;
}

VLIB_NODE_FN (bpa_in2out_handoff_node)
(vlib_main_t *vm, vlib_node_runtime_t *node, vlib_frame_t *frame)
{
  return bpa_handoff_inline (vm, node, frame, bpa_main.in2out_fq_index, 1);
}

VLIB_NODE_FN (bpa_out2in_handoff_node)
(vlib_main_t *vm, vlib_node_runtime_t *node, vlib_frame_t *frame)
{
  return bpa_handoff_inline (vm, node, frame, bpa_main.out2in_fq_index, 0);
}

VLIB_REGISTER_NODE (bpa_in2out_handoff_node) = {
  .name = "bpa-in2out-handoff",
  .vector_size = sizeof (u32),
  .format_trace = format_bpa_handoff_trace,
  .type = VLIB_NODE_TYPE_INTERNAL,
  .n_errors = BPA_HANDOFF_N_ERROR,
  .error_strings = bpa_handoff_error_strings,
  .n_next_nodes = 1,
  .next_nodes = { [0] = "error-drop" },
};

VLIB_REGISTER_NODE (bpa_out2in_handoff_node) = {
  .name = "bpa-out2in-handoff",
  .vector_size = sizeof (u32),
  .format_trace = format_bpa_handoff_trace,
  .type = VLIB_NODE_TYPE_INTERNAL,
  .n_errors = BPA_HANDOFF_N_ERROR,
  .error_strings = bpa_handoff_error_strings,
  .n_next_nodes = 1,
  .next_nodes = { [0] = "error-drop" },
};

VNET_FEATURE_INIT (bpa_in2out_handoff_feature, static) = {
  .arc_name = "ip4-unicast",
  .node_name = "bpa-in2out-handoff",
  .runs_before = VNET_FEATURES ("ip4-lookup"),
  /*
   * Has to run AFTER shallow virtual reassembly, otherwise the buffer's port
   * fields do not exist yet and a following fragment is read with a garbage
   * port - which creates a phantom session and burns one port of the block
   * per fragment.
   */
  .runs_after = VNET_FEATURES ("ip4-sv-reassembly-feature"),
};

VNET_FEATURE_INIT (bpa_out2in_handoff_feature, static) = {
  .arc_name = "ip4-unicast",
  .node_name = "bpa-out2in-handoff",
  .runs_before = VNET_FEATURES ("ip4-lookup"),
  /*
   * Has to run AFTER shallow virtual reassembly, otherwise the buffer's port
   * fields do not exist yet and a following fragment is read with a garbage
   * port - which creates a phantom session and burns one port of the block
   * per fragment.
   */
  .runs_after = VNET_FEATURES ("ip4-sv-reassembly-feature"),
};
