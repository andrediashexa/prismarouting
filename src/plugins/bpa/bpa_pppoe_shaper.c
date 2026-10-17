/*
 * bpa_pppoe_shaper.c - a PPPoE session's plan as a queue (#610): `set bpa
 * pppoe-shaper`, `show bpa pppoe-shaper`, the feature nodes that admit a
 * packet or queue it, and the node that sends what waited.
 *
 * bpa_pppoe_shaper.h says what is decided per packet, and why; this file is
 * the VPP-facing half, every function here carries `not a unit:`, and
 * lab/pppoe.sh section 24 and lab/pppoe-shaper.sh drive it. docs/bng.md 0.9
 * has the design and the measurements.
 *
 * WHERE IT SITS. The download on the session interface's OUTPUT -
 * `ip4-output` and `ip6-output`, after the midchain has written the PPPoE
 * header, where VPP's `policer output` sits - so it meters what the session
 * sends, IPv4 and IPv6 in one bucket as the policer does; the PPPoE and
 * Ethernet header are not counted (`save_rewrite_length`): the plan is the
 * subscriber's IP rate. The upload, when it is asked for, on the INPUT -
 * `ip4-unicast` and `ip6-unicast`, before the CGNAT, where `policer input`
 * sits. Enabled per session by the control plane (accel-ppp's VPP hook,
 * build/accel-ppp-ng.patch vpppolicer.c), so a session without a plan, and
 * every interface that is not a session, never meets it.
 *
 * ONE OWNER THREAD A SESSION. A subscriber's packets arrive on any worker:
 * its CGNAT downloads on the worker that owns its block (bpa_handoff.c), its
 * IPv6 wherever the card's RSS put it. The bucket and the queue are one
 * per session, so they live on one thread: the first worker a packet of the
 * session reaches binds it (a compare-and-swap), and every other worker
 * hands its packets there, by VPP's frame queues - the policer's own
 * arrangement (policer-output-handoff). A packet the main thread sends while
 * the box has workers - vppctl's own ping - binds the session to a worker
 * by its sw_if_index, never to the main thread, which forwards nothing.
 *
 * WHAT WAITED, AND WHO SENDS IT. A queued packet is linked through its own
 * buffer's metadata (vnet_buffer2's `unused` words: the next buffer, the
 * time it arrived, its length), so a queue costs no memory but the buffers
 * it holds, and a session costs one bpa_shaper_t a direction whether it
 * ever queues or not. A session with a queue waits in its owner thread's
 * CALENDAR - 2048 slots of 100 us, the session in the slot of the tick its
 * head is due (bpa_shaper_wait_ticks: when the bucket will hold the head) -
 * and bpa-pppoe-shaper-tx, an input node, goes through the slots that came
 * due since its last turn and sends what is in them. It is POLLING only
 * while its thread holds a queue, and DISABLED otherwise: a box with no
 * queue anywhere runs no node of this file at all, and a turn with nothing
 * due is a clock read and a compare.
 *
 * What was measured and dropped on the way (lab/pppoe-shaper.sh, the #610
 * probe):
 *   * walking every session with a queue at every turn: a cost that grows
 *     with the sessions held at their plan at once;
 *   * a node of VPP's SCHED kind, woken by VPP's own timer wheel when the
 *     first slot is due, polling nothing: one TCP flow through a 20 Mbit/s
 *     plan got 7.8. A thread whose interfaces are in interrupt mode - the
 *     labs' af_packet, a box's card in interrupt or adaptive mode - sleeps
 *     in epoll for as long as VPP's wheel says, and VPP reads only its fast
 *     ring for that (vlib/file.c, tw_timer_first_expires_in_ticks): a wake
 *     in its slow ring is up to 10 ms late, a dozen packets of that plan.
 *   The turns of a polling sender cost about 106 clocks each when nothing
 *   is due (three workers, 20 million turns in 5 s) - spin a worker whose
 *   card it polls does anyway, and a main thread with no worker does only
 *   while a queue waits.
 *
 * A packet that leaves takes the arc's next feature, as it would have,
 * through `vnet_feature_next()`: the sender is a SIBLING of the feature
 * nodes (VLIB `sibling_of`), so it shares their next arcs and a feature's
 * next index means the same node from either.
 *
 * WHAT A SESSION'S END DOES. accel-ppp's hook removes the shaper with the
 * policers; and VPP's pppoe plugin sets a deleted session's interface
 * administratively down before it hands it to the next session, which this
 * file hears (VNET_SW_INTERFACE_ADMIN_UP_DOWN_FUNCTION) and takes the
 * shaper off with - so a control plane killed with -9, whose sessions the
 * next one deletes, leaves no shaper on an interface the next subscriber is
 * given. Removing frees what was queued, under the barrier.
 *
 * THE BOX'S SHARE OF BUFFERS. Every queued packet holds a buffer of the
 * pool every card's receive ring draws on, so the shapers together may hold
 * a quarter of it, split over the threads that forward
 * (bpa_shaper_thread_cap); past that a packet that would wait is dropped
 * and counted apart from a session's own full queue.
 */

#include <vlib/vlib.h>
#include <vnet/vnet.h>
#include <vnet/feature/feature.h>
#include <vnet/ip/ip.h>

#include "bpa.h"
#include "bpa_pppoe_shaper.h"

/* The calendar's horizon: 2047 ticks of 100 us (204.7 ms). */
#define BPA_SHAPER_MAX_TICKS (BPA_SHAPER_SLOTS - 1)

#define foreach_bpa_shaper_error                                             \
  _ (PASSED, "packets within the plan, passed at once")                      \
  _ (QUEUED, "packets past the plan, queued")                                \
  _ (FULL, "packets dropped, the session's queue full")                      \
  _ (BOX, "packets dropped, the box's shaper buffers all held")              \
  _ (HANDOFF, "packets handed to the session's owner thread")                \
  _ (CONGESTED, "packets dropped, the owner thread's handoff queue full")    \
  _ (GONE, "packets dropped, their session's shaper removed")                \
  _ (SENT, "queued packets sent")                                            \
  _ (AQM, "queued packets dropped by CoDel, the queue standing")

typedef enum
{
#define _(sym, str) BPA_SHAPER_ERROR_##sym,
  foreach_bpa_shaper_error
#undef _
    BPA_SHAPER_N_ERROR,
} bpa_shaper_error_t;

static char *bpa_shaper_error_strings[] = {
#define _(sym, str) str,
  foreach_bpa_shaper_error
#undef _
};

typedef struct
{
  u32 *slot[BPA_SHAPER_SLOTS];		/* keys, (sw_if_index << 1) | dir */
  u64 occupied[BPA_SHAPER_SLOT_WORDS]; /* the slots that hold one */
  u64 done;	  /* the last tick the sender went through */
  u32 *due;	  /* one slot's keys, taken out to be sent */
  u32 *aqm_drop;  /* the heads CoDel dropped in one call, reused */
  u32 n_active;	  /* sessions with a queue */
  u32 held;	  /* buffers this thread's queues hold */
} bpa_shaper_thread_t;

typedef struct
{
  bpa_shaper_t *s[2];	   /* by sw_if_index, per direction */
  bpa_shaper_thread_t *pt; /* by thread */
  u32 fq[2];		   /* frame queues to the owner, per direction */
  u32 cap;		   /* buffers one thread's queues may hold */
  u32 n_shaped[2];
  u8 n_workers;
} bpa_shaper_main_t;

static bpa_shaper_main_t bpa_shaper_main;

/* The buffer metadata a queued packet carries (vnet_buffer2, `unused`): the
 * next in its queue, when it arrived (us), its metered length, and - for a
 * packet handed to the owner - the generation of the shaper it was meant
 * for. Nothing in VPP 26.06 reads or writes these words. */
#define bpa_shaper_next(b) (vnet_buffer2 (b)->unused[0])
#define bpa_shaper_when(b) (vnet_buffer2 (b)->unused[1])
#define bpa_shaper_len(b)  (vnet_buffer2 (b)->unused[2])
#define bpa_shaper_gen(b)  (vnet_buffer2 (b)->unused[3])

typedef struct
{
  u32 sw_if_index;
  u8 dir;
  u8 verdict; /* bpa_shaper_verdict_t, or 9 handed off, 8 gone */
  u16 thread;
  u32 q_pkts;
} bpa_shaper_trace_t;

/* not a unit: a trace formatter VPP calls with a va_list */
static u8 *
format_bpa_shaper_trace (u8 *s, va_list *args)
{
  CLIB_UNUSED (vlib_main_t * vm) = va_arg (*args, vlib_main_t *);
  CLIB_UNUSED (vlib_node_t * node) = va_arg (*args, vlib_node_t *);
  bpa_shaper_trace_t *t = va_arg (*args, bpa_shaper_trace_t *);
  static const char *v[] = { "passed", "queued", "dropped, queue full",
			     "dropped, box full" };
  const char *what = t->verdict < 4 ? v[t->verdict] :
		     t->verdict == 9 ? "handed to the owner" :
				       "dropped, shaper removed";

  return format (s, "BPA pppoe-shaper %s sw_if_index %u: %s, thread %u, "
		    "%u queued",
		 t->dir == BPA_SHAPER_UP ? "up" : "down", t->sw_if_index, what,
		 t->thread, t->q_pkts);
}

extern vlib_node_registration_t bpa_shaper_tx_node;

/* not a unit: the owner of a shaper no packet reached yet, by the thread
 * one reached first; needs the threads */
static_always_inline u16
bpa_shaper_owner (bpa_shaper_main_t *sm, bpa_shaper_t *s, u32 sw_if_index,
		  u32 ti)
{
  u16 want = (u16) ti;

  if (PREDICT_TRUE (s->owner != BPA_SHAPER_UNBOUND))
    return s->owner;
  /* The main thread forwards nothing on a box with workers: a packet it
   * sends (a ping from vppctl) binds the session to a worker instead. */
  if (ti == 0 && sm->n_workers)
    want = (u16) (1 + sw_if_index % sm->n_workers);
  clib_atomic_cmp_and_swap (&s->owner, BPA_SHAPER_UNBOUND, want);
  return s->owner;
}

/* not a unit: `s` into its owner's calendar, `ticks` from `now` */
static_always_inline void
bpa_shaper_at (vlib_main_t *vm, bpa_shaper_thread_t *pt, bpa_shaper_t *s,
	       u32 key, u32 ticks, f64 now)
{
  u64 due = bpa_shaper_due_tick ((u64) (now / BPA_SHAPER_TICK) + ticks,
				 pt->done);
  u32 sl = (u32) (due & BPA_SHAPER_SLOT_MASK);

  vec_add1 (pt->slot[sl], key);
  pt->occupied[sl >> 6] |= 1ULL << (sl & 63);
  s->slot = sl;
}

/* not a unit: `s`, whose queue was empty, now holds a packet of `len`
 * bytes: into the calendar for when it is due. A calendar nothing waited
 * in is brought to `now` first, so it is not gone through tick by tick */
static_always_inline void
bpa_shaper_activate (vlib_main_t *vm, bpa_shaper_thread_t *pt,
		     bpa_shaper_t *s, u32 key, u32 len, f64 now)
{
  s->active = 1;
  if (pt->n_active++ == 0)
    {
      pt->done = (u64) (now / BPA_SHAPER_TICK);
      vlib_node_set_state (vm, bpa_shaper_tx_node.index,
			   VLIB_NODE_STATE_POLLING);
    }
  bpa_shaper_at (
    vm, pt, s, key,
    bpa_shaper_wait_ticks (s, len, BPA_SHAPER_TICK, BPA_SHAPER_MAX_TICKS),
    now);
}

/* not a unit: the feature nodes' body, and the handoff targets'; it needs
 * frames and the graph.
 *
 * `from_handoff`: the packet was handed here by another thread, so this is
 * its owner, and the shaper may have been removed - or given to the next
 * session on this interface - while it was in the frame queue: its
 * generation says which. */
static_always_inline uword
bpa_shaper_inline (vlib_main_t *vm, vlib_node_runtime_t *node,
		   vlib_frame_t *frame, int dir, int from_handoff)
{
  bpa_shaper_main_t *sm = &bpa_shaper_main;
  u32 n = frame->n_vectors, *from = vlib_frame_vector_args (frame);
  u32 ti = vm->thread_index;
  bpa_shaper_thread_t *pt = vec_elt_at_index (sm->pt, ti);
  vlib_buffer_t *bufs[VLIB_FRAME_SIZE];
  u32 out[VLIB_FRAME_SIZE], ho[VLIB_FRAME_SIZE];
  u16 nexts[VLIB_FRAME_SIZE], ho_thread[VLIB_FRAME_SIZE];
  u32 n_out = 0, n_ho = 0, i;
  u32 n_pass = 0, n_queue = 0, n_full = 0, n_box = 0, n_gone = 0;
  f64 now = vlib_time_now (vm);
  u32 now_us = (u32) (u64) (now * 1e6);
  u32 rx_tx = dir == BPA_SHAPER_DOWN ? VLIB_TX : VLIB_RX;
  bpa_shaper_t *ss = sm->s[dir];

  vlib_get_buffers (vm, from, bufs, n);
  for (i = 0; i < n; i++)
    {
      vlib_buffer_t *b = bufs[i];
      u32 bi = from[i];
      u32 sw = vnet_buffer (b)->sw_if_index[rx_tx];
      bpa_shaper_t *s = sw < vec_len (ss) ? ss + sw : 0;
      u8 verdict = 8;
      u32 next;

      if (PREDICT_FALSE (!s || s->rate <= 0.
			 || (from_handoff && bpa_shaper_gen (b) != s->gen)))
	{
	  if (from_handoff)
	    {
	      /* its config index may name a feature string freed with the
	       * shaper: not followed */
	      out[n_out] = bi;
	      nexts[n_out++] = 0;
	      b->error = node->errors[BPA_SHAPER_ERROR_GONE];
	      n_gone++;
	    }
	  else
	    {
	      /* enabled with nothing set cannot happen (the two go together
	       * under the barrier); passed, never dropped, if it ever does */
	      vnet_feature_next (&next, b);
	      out[n_out] = bi;
	      nexts[n_out++] = next;
	      verdict = BPA_SHAPER_PASS;
	    }
	  goto trace;
	}

      if (PREDICT_FALSE (bpa_shaper_owner (sm, s, sw, ti) != ti))
	{
	  bpa_shaper_gen (b) = s->gen;
	  ho[n_ho] = bi;
	  ho_thread[n_ho++] = s->owner;
	  verdict = 9;
	  goto trace;
	}

      {
	u32 len = vlib_buffer_length_in_chain (vm, b);
	if (dir == BPA_SHAPER_DOWN)
	  len -= vnet_buffer (b)->ip.save_rewrite_length;
	verdict = bpa_shaper_admit (s, len, now, pt->held < sm->cap);
	switch (verdict)
	  {
	  case BPA_SHAPER_PASS:
	    vnet_feature_next (&next, b);
	    out[n_out] = bi;
	    nexts[n_out++] = next;
	    n_pass++;
	    break;
	  case BPA_SHAPER_QUEUE:
	    bpa_shaper_next (b) = BPA_SHAPER_NONE;
	    bpa_shaper_when (b) = now_us;
	    bpa_shaper_len (b) = len;
	    if (s->tail == BPA_SHAPER_NONE)
	      s->head = bi;
	    else
	      bpa_shaper_next (vlib_get_buffer (vm, s->tail)) = bi;
	    s->tail = bi;
	    pt->held++;
	    n_queue++;
	    if (!s->active)
	      bpa_shaper_activate (vm, pt, s, (sw << 1) | dir, len, now);
	    break;
	  case BPA_SHAPER_DROP_FULL:
	    out[n_out] = bi;
	    nexts[n_out++] = 0;
	    b->error = node->errors[BPA_SHAPER_ERROR_FULL];
	    n_full++;
	    break;
	  default:
	    out[n_out] = bi;
	    nexts[n_out++] = 0;
	    b->error = node->errors[BPA_SHAPER_ERROR_BOX];
	    n_box++;
	    break;
	  }
      }

    trace:
      if (PREDICT_FALSE (b->flags & VLIB_BUFFER_IS_TRACED))
	{
	  bpa_shaper_trace_t *t = vlib_add_trace (vm, node, b, sizeof (*t));
	  t->sw_if_index = sw;
	  t->dir = dir;
	  t->verdict = verdict;
	  t->thread = ti;
	  t->q_pkts = s ? s->q_pkts : 0;
	}
    }

  if (n_out)
    vlib_buffer_enqueue_to_next (vm, node, out, nexts, n_out);
  if (n_ho)
    {
      u32 n_enq = vlib_buffer_enqueue_to_thread (vm, node, sm->fq[dir], ho,
						 ho_thread, n_ho, 1);
      vlib_node_increment_counter (vm, node->node_index,
				   BPA_SHAPER_ERROR_HANDOFF, n_enq);
      if (n_enq < n_ho)
	vlib_node_increment_counter (vm, node->node_index,
				     BPA_SHAPER_ERROR_CONGESTED, n_ho - n_enq);
    }
  if (n_pass)
    vlib_node_increment_counter (vm, node->node_index, BPA_SHAPER_ERROR_PASSED,
				 n_pass);
  if (n_queue)
    vlib_node_increment_counter (vm, node->node_index, BPA_SHAPER_ERROR_QUEUED,
				 n_queue);
  if (n_full)
    vlib_node_increment_counter (vm, node->node_index, BPA_SHAPER_ERROR_FULL,
				 n_full);
  if (n_box)
    vlib_node_increment_counter (vm, node->node_index, BPA_SHAPER_ERROR_BOX,
				 n_box);
  if (n_gone)
    vlib_node_increment_counter (vm, node->node_index, BPA_SHAPER_ERROR_GONE,
				 n_gone);
  return n;
}

VLIB_NODE_FN (bpa_shaper_down_node)
(vlib_main_t *vm, vlib_node_runtime_t *node, vlib_frame_t *frame)
{
  return bpa_shaper_inline (vm, node, frame, BPA_SHAPER_DOWN, 0);
}

VLIB_NODE_FN (bpa_shaper_up_node)
(vlib_main_t *vm, vlib_node_runtime_t *node, vlib_frame_t *frame)
{
  return bpa_shaper_inline (vm, node, frame, BPA_SHAPER_UP, 0);
}

VLIB_NODE_FN (bpa_shaper_down_owner_node)
(vlib_main_t *vm, vlib_node_runtime_t *node, vlib_frame_t *frame)
{
  return bpa_shaper_inline (vm, node, frame, BPA_SHAPER_DOWN, 1);
}

VLIB_NODE_FN (bpa_shaper_up_owner_node)
(vlib_main_t *vm, vlib_node_runtime_t *node, vlib_frame_t *frame)
{
  return bpa_shaper_inline (vm, node, frame, BPA_SHAPER_UP, 1);
}

/* The download node owns the next arcs; the four others are its siblings,
 * so a feature next index read in any of them names the same node. */
VLIB_REGISTER_NODE (bpa_shaper_down_node) = {
  .name = "bpa-pppoe-shaper-down",
  .vector_size = sizeof (u32),
  .format_trace = format_bpa_shaper_trace,
  .type = VLIB_NODE_TYPE_INTERNAL,
  .n_errors = BPA_SHAPER_N_ERROR,
  .error_strings = bpa_shaper_error_strings,
  .n_next_nodes = 1,
  .next_nodes = { [0] = "error-drop" },
};

VLIB_REGISTER_NODE (bpa_shaper_up_node) = {
  .name = "bpa-pppoe-shaper-up",
  .vector_size = sizeof (u32),
  .format_trace = format_bpa_shaper_trace,
  .type = VLIB_NODE_TYPE_INTERNAL,
  .n_errors = BPA_SHAPER_N_ERROR,
  .error_strings = bpa_shaper_error_strings,
  .sibling_of = "bpa-pppoe-shaper-down",
};

VLIB_REGISTER_NODE (bpa_shaper_down_owner_node) = {
  .name = "bpa-pppoe-shaper-down-owner",
  .vector_size = sizeof (u32),
  .format_trace = format_bpa_shaper_trace,
  .type = VLIB_NODE_TYPE_INTERNAL,
  .n_errors = BPA_SHAPER_N_ERROR,
  .error_strings = bpa_shaper_error_strings,
  .sibling_of = "bpa-pppoe-shaper-down",
};

VLIB_REGISTER_NODE (bpa_shaper_up_owner_node) = {
  .name = "bpa-pppoe-shaper-up-owner",
  .vector_size = sizeof (u32),
  .format_trace = format_bpa_shaper_trace,
  .type = VLIB_NODE_TYPE_INTERNAL,
  .n_errors = BPA_SHAPER_N_ERROR,
  .error_strings = bpa_shaper_error_strings,
  .sibling_of = "bpa-pppoe-shaper-down",
};

/* The download: after the midchain's rewrite, where `policer output` is. */
VNET_FEATURE_INIT (bpa_shaper_ip4_down, static) = {
  .arc_name = "ip4-output",
  .node_name = "bpa-pppoe-shaper-down",
  .runs_before = VNET_FEATURES ("interface-output"),
};

VNET_FEATURE_INIT (bpa_shaper_ip6_down, static) = {
  .arc_name = "ip6-output",
  .node_name = "bpa-pppoe-shaper-down",
  .runs_before = VNET_FEATURES ("interface-output"),
};

/* The upload: before the CGNAT, where `policer input` is - and after
 * shallow reassembly, as everything in front of the CGNAT on this arc is
 * (test/test_feature_order.py: a fragment reaching the CGNAT through a node
 * that ran before reassembly burns a port of the block). */
VNET_FEATURE_INIT (bpa_shaper_ip4_up, static) = {
  .arc_name = "ip4-unicast",
  .node_name = "bpa-pppoe-shaper-up",
  .runs_before = VNET_FEATURES ("bpa-in2out", "bpa-in2out-handoff",
				"ip4-lookup"),
  .runs_after = VNET_FEATURES ("ip4-sv-reassembly-feature"),
};

VNET_FEATURE_INIT (bpa_shaper_ip6_up, static) = {
  .arc_name = "ip6-unicast",
  .node_name = "bpa-pppoe-shaper-up",
  .runs_before = VNET_FEATURES ("bpa-nat64-in2out",
				"bpa-nat64-in2out-handoff", "ip6-lookup"),
  .runs_after = VNET_FEATURES ("ip6-sv-reassembly-feature"),
};

/* not a unit: the sender's body; it needs frames, the graph and the
 * thread's calendar. One call goes through the slots from the last it went
 * through to now and sends, for each session due, what its bucket allows -
 * at most a frame in all; a session still holding a queue goes back into
 * the calendar for when its head is due. */
VLIB_NODE_FN (bpa_shaper_tx_node)
(vlib_main_t *vm, vlib_node_runtime_t *node, vlib_frame_t *frame)
{
  bpa_shaper_main_t *sm = &bpa_shaper_main;
  bpa_shaper_thread_t *pt = vec_elt_at_index (sm->pt, vm->thread_index);
  u32 out[VLIB_FRAME_SIZE];
  u16 nexts[VLIB_FRAME_SIZE];
  u32 n = 0, i, sl, ahead;
  f64 now = vlib_time_now (vm);
  u64 now_tick = (u64) (now / BPA_SHAPER_TICK), t;
  u32 now_us = (u32) (u64) (now * 1e6);

  if (pt->n_active == 0)
    {
      vlib_node_set_state (vm, node->node_index, VLIB_NODE_STATE_DISABLED);
      return 0;
    }
  if (now_tick <= pt->done)
    return 0;
  /* the slots from the one after `done` up to now, at most a turn */
  t = now_tick - pt->done >= BPA_SHAPER_SLOTS ? now_tick - BPA_SHAPER_SLOTS + 1 :
						 pt->done + 1;
  while (t <= now_tick && n < VLIB_FRAME_SIZE)
    {
      ahead = bpa_shaper_next_slot (pt->occupied,
				    (u32) (t & BPA_SHAPER_SLOT_MASK));
      if (ahead == BPA_SHAPER_NONE || t + ahead > now_tick)
	{
	  t = now_tick + 1;
	  break;
	}
      t += ahead;
      sl = (u32) (t & BPA_SHAPER_SLOT_MASK);
      /* the slot's keys taken out whole: a session sent again below goes
       * into another slot, or this one a turn later */
      vec_reset_length (pt->due);
      vec_append (pt->due, pt->slot[sl]);
      vec_reset_length (pt->slot[sl]);
      pt->occupied[sl >> 6] &= ~(1ULL << (sl & 63));
      pt->done = t;
      for (i = 0; i < vec_len (pt->due); i++)
	{
	  u32 key = pt->due[i];
	  bpa_shaper_t *s = vec_elt_at_index (sm->s[key & 1], key >> 1);
	  u32 len = 0;

	  s->slot = BPA_SHAPER_NONE;
	  bpa_shaper_refill (s, now);
	  while (s->head != BPA_SHAPER_NONE)
	    {
	      u32 bi = s->head, next, waited;
	      vlib_buffer_t *b = vlib_get_buffer (vm, bi);

	      len = bpa_shaper_len (b);
	      if (n == VLIB_FRAME_SIZE || !bpa_shaper_may_send (s, len))
		break;
	      waited = bpa_shaper_waited_us (now_us, bpa_shaper_when (b));
	      s->head = bpa_shaper_next (b);
	      if (s->head == BPA_SHAPER_NONE)
		s->tail = BPA_SHAPER_NONE;
	      pt->held--;
	      if (bpa_shaper_codel_drop (s, waited, s->q_bytes, now_us))
		{
		  /* the bucket gives back what the dropped packet took */
		  s->tokens += (f64) len;
		  bpa_shaper_dropped_head (s, len);
		  vec_add1 (pt->aqm_drop, bi);
		  continue;
		}
	      bpa_shaper_sent (s, len, waited);
	      vnet_feature_next (&next, b);
	      out[n] = bi;
	      nexts[n++] = next;
	    }
	  if (s->head == BPA_SHAPER_NONE)
	    {
	      s->active = 0;
	      pt->n_active--;
	    }
	  else
	    /* the frame filled: the next tick; else when the head is due */
	    bpa_shaper_at (vm, pt, s, key,
			   n == VLIB_FRAME_SIZE ?
			     1 :
			     bpa_shaper_wait_ticks (s, len, BPA_SHAPER_TICK,
						    BPA_SHAPER_MAX_TICKS),
			   now);
	}
      t++;
    }
  if (n)
    {
      vlib_buffer_enqueue_to_next (vm, node, out, nexts, n);
      vlib_node_increment_counter (vm, bpa_shaper_down_node.index,
				   BPA_SHAPER_ERROR_SENT, n);
    }
  if (vec_len (pt->aqm_drop))
    {
      vlib_node_increment_counter (vm, bpa_shaper_down_node.index,
				   BPA_SHAPER_ERROR_AQM,
				   vec_len (pt->aqm_drop));
      vlib_buffer_free (vm, pt->aqm_drop, vec_len (pt->aqm_drop));
      vec_reset_length (pt->aqm_drop);
    }
  if (pt->n_active == 0)
    vlib_node_set_state (vm, node->node_index, VLIB_NODE_STATE_DISABLED);
  return n;
}

VLIB_REGISTER_NODE (bpa_shaper_tx_node) = {
  .name = "bpa-pppoe-shaper-tx",
  .type = VLIB_NODE_TYPE_INPUT,
  .state = VLIB_NODE_STATE_DISABLED,
  .sibling_of = "bpa-pppoe-shaper-down",
};

/* not a unit: plugin init, VPP's - the handoff queues and the per-thread
 * lists, sized once the threads are known */
static clib_error_t *
bpa_shaper_init (vlib_main_t *vm)
{
  bpa_shaper_main_t *sm = &bpa_shaper_main;
  vlib_thread_main_t *tm = vlib_get_thread_main ();
  vlib_buffer_main_t *bm = vm->buffer_main;
  vlib_buffer_pool_t *bp;
  u32 n_buffers = 0;

  sm->fq[BPA_SHAPER_DOWN] =
    vlib_frame_queue_main_init (bpa_shaper_down_owner_node.index, 0);
  sm->fq[BPA_SHAPER_UP] =
    vlib_frame_queue_main_init (bpa_shaper_up_owner_node.index, 0);
  vec_validate (sm->pt, tm->n_vlib_mains - 1);
  sm->n_workers = (u8) vlib_num_workers ();
  vec_foreach (bp, bm->buffer_pools)
    n_buffers += bp->n_buffers;
  sm->cap = bpa_shaper_thread_cap (n_buffers, sm->n_workers ? sm->n_workers : 1);
  return 0;
}

VLIB_INIT_FUNCTION (bpa_shaper_init) = {
  .runs_after = VLIB_INITS ("bpa_init"),
};

/* not a unit: frees what `s` has queued, and takes it out of its owner's
 * wheel;
 * under the barrier (a CLI command, or VPP's admin-down callback) */
static void
bpa_shaper_flush (vlib_main_t *vm, bpa_shaper_t *s, u32 key)
{
  bpa_shaper_main_t *sm = &bpa_shaper_main;
  u32 *bis = 0, bi = s->head;

  while (bi != BPA_SHAPER_NONE)
    {
      vec_add1 (bis, bi);
      bi = bpa_shaper_next (vlib_get_buffer (vm, bi));
    }
  if (vec_len (bis))
    {
      vlib_buffer_free (vm, bis, vec_len (bis));
      if (s->owner < vec_len (sm->pt))
	sm->pt[s->owner].held -= vec_len (bis);
    }
  vec_free (bis);
  if (s->active && s->owner < vec_len (sm->pt))
    {
      bpa_shaper_thread_t *pt = vec_elt_at_index (sm->pt, s->owner);
      if (s->slot != BPA_SHAPER_NONE)
	{
	  /* out of its slot */
	  u32 *keys = pt->slot[s->slot], k;
	  for (k = 0; k < vec_len (keys); k++)
	    if (keys[k] == key)
	      {
		keys[k] = keys[vec_len (keys) - 1];
		vec_dec_len (keys, 1);
		break;
	      }
	  if (vec_len (keys) == 0)
	    pt->occupied[s->slot >> 6] &= ~(1ULL << (s->slot & 63));
	}
      pt->n_active--;
    }
  s->slot = BPA_SHAPER_NONE;
  s->head = s->tail = BPA_SHAPER_NONE;
  s->q_pkts = s->q_bytes = 0;
  s->active = 0;
}

/* not a unit: the arcs a direction's node is on, enabled or not; the
 * feature machinery needs the graph */
static void
bpa_shaper_feature (u32 sw_if_index, int dir, int enable)
{
  if (dir == BPA_SHAPER_DOWN)
    {
      vnet_feature_enable_disable ("ip4-output", "bpa-pppoe-shaper-down",
				   sw_if_index, enable, 0, 0);
      vnet_feature_enable_disable ("ip6-output", "bpa-pppoe-shaper-down",
				   sw_if_index, enable, 0, 0);
    }
  else
    {
      vnet_feature_enable_disable ("ip4-unicast", "bpa-pppoe-shaper-up",
				   sw_if_index, enable, 0, 0);
      vnet_feature_enable_disable ("ip6-unicast", "bpa-pppoe-shaper-up",
				   sw_if_index, enable, 0, 0);
    }
}

/* not a unit: one direction of one interface shaped at `kbit` (0: not
 * shaped), under the barrier. Enabled ONCE per interface and direction - a
 * feature enabled twice runs twice (docs/bng.md 0.6, defect 2) - and a new
 * rate on a shaped interface keeps its queue and its counters (a CoA). */
static clib_error_t *
bpa_shaper_apply (vlib_main_t *vm, u32 sw_if_index, int dir, u32 kbit,
		  u32 queue_ms)
{
  bpa_shaper_main_t *sm = &bpa_shaper_main;
  bpa_shaper_t *s;
  u32 old_len = vec_len (sm->s[dir]), k;

  if (kbit == 0)
    {
      if (sw_if_index >= old_len || sm->s[dir][sw_if_index].rate <= 0.)
	return 0;
      s = vec_elt_at_index (sm->s[dir], sw_if_index);
      bpa_shaper_flush (vm, s, (sw_if_index << 1) | dir);
      bpa_shaper_feature (sw_if_index, dir, 0);
      s->gen++;
      bpa_shaper_reset (s);
      sm->n_shaped[dir]--;
      return 0;
    }
  vec_validate (sm->s[dir], sw_if_index);
  for (k = old_len; k < vec_len (sm->s[dir]); k++)
    bpa_shaper_reset (vec_elt_at_index (sm->s[dir], k));
  s = vec_elt_at_index (sm->s[dir], sw_if_index);
  if (s->rate > 0.)
    {
      if (!bpa_shaper_set (s, kbit, queue_ms))
	return clib_error_return (0, "rate 1-%u kbit/s, queue %u-%u ms",
				  BPA_SHAPER_KBIT_MAX, BPA_SHAPER_QUEUE_MS_MIN,
				  BPA_SHAPER_QUEUE_MS_MAX);
      return 0;
    }
  bpa_shaper_reset (s);
  if (!bpa_shaper_set (s, kbit, queue_ms))
    return clib_error_return (0, "rate 1-%u kbit/s, queue %u-%u ms",
			      BPA_SHAPER_KBIT_MAX, BPA_SHAPER_QUEUE_MS_MIN,
			      BPA_SHAPER_QUEUE_MS_MAX);
  bpa_shaper_feature (sw_if_index, dir, 1);
  sm->n_shaped[dir]++;
  return 0;
}

/* not a unit: VPP calls it at every interface's admin change. VPP's pppoe
 * plugin sets a deleted session's interface down before the interface is
 * handed to the next session (pppoe.c vnet_pppoe_add_del_session): the
 * shaper goes with the session, whoever deleted it */
static clib_error_t *
bpa_shaper_admin_up_down (vnet_main_t *vnm, u32 sw_if_index, u32 flags)
{
  vlib_main_t *vm = vlib_get_main ();
  int dir;

  if (flags & VNET_SW_INTERFACE_FLAG_ADMIN_UP)
    return 0;
  for (dir = 0; dir < 2; dir++)
    bpa_shaper_apply (vm, sw_if_index, dir, 0, 0);
  return 0;
}

VNET_SW_INTERFACE_ADMIN_UP_DOWN_FUNCTION (bpa_shaper_admin_up_down);

/* not a unit: a VLIB_CLI_COMMAND body, driven by accel-ppp's VPP hook
 * (cli_inband, build/accel-ppp-ng.patch vpppolicer.c), lab/pppoe.sh and
 * lab/pppoe-shaper.sh */
static clib_error_t *
bpa_set_pppoe_shaper_command_fn (vlib_main_t *vm, unformat_input_t *input,
				 vlib_cli_command_t *cmd)
{
  bpa_shaper_main_t *sm = &bpa_shaper_main;
  vnet_main_t *vnm = vnet_get_main ();
  unformat_input_t _line, *line = &_line;
  u32 sw_if_index = ~0, down = ~0, up = ~0, queue_ms = BPA_SHAPER_QUEUE_MS_DEFAULT;
  u32 buffers = ~0;
  int codel = -1;
  u32 target = ~0, interval = ~0;
  clib_error_t *error = 0;

  if (!unformat_user (input, unformat_line_input, line))
    return clib_error_return (0, "set bpa pppoe-shaper <interface> | "
				 "sw-if-index <n> [down <kbit>] [up <kbit>] "
				 "[queue <ms>] | buffers <n>");
  while (unformat_check_input (line) != UNFORMAT_END_OF_INPUT)
    {
      if (unformat (line, "sw-if-index %u", &sw_if_index))
	;
      else if (unformat (line, "down %u", &down))
	;
      else if (unformat (line, "up %u", &up))
	;
      else if (unformat (line, "queue %u", &queue_ms))
	;
      else if (unformat (line, "buffers %u", &buffers))
	;
      else if (unformat (line, "codel on"))
	codel = 1;
      else if (unformat (line, "codel off"))
	codel = 0;
      else if (unformat (line, "target %u", &target))
	;
      else if (unformat (line, "interval %u", &interval))
	;
      else if (unformat (line, "%U", unformat_vnet_sw_interface, vnm,
			 &sw_if_index))
	;
      else
	{
	  error = clib_error_return (0, "unknown input '%U'",
				     format_unformat_error, line);
	  goto done;
	}
    }
  if (buffers != ~0)
    {
      /* the per-thread share of the box's queues, for a lab or a box that
       * wants another share than a quarter of the pool */
      if (buffers < 64)
	error = clib_error_return (0, "buffers: at least 64 a thread");
      else
	sm->cap = buffers;
      goto done;
    }
  if (sw_if_index == ~0 || !vnet_get_sw_interface_or_null (vnm, sw_if_index))
    {
      error = clib_error_return (0, "no such interface");
      goto done;
    }
  if (down == ~0 && up == ~0)
    {
      error = clib_error_return (0, "down <kbit> and/or up <kbit>; 0 removes");
      goto done;
    }
  if (down != ~0)
    error = bpa_shaper_apply (vm, sw_if_index, BPA_SHAPER_DOWN, down, queue_ms);
  if (!error && up != ~0)
    error = bpa_shaper_apply (vm, sw_if_index, BPA_SHAPER_UP, up, queue_ms);
  if (!error && codel != -1)
    {
      int dir;
      for (dir = 0; dir < 2; dir++)
	if (sw_if_index < vec_len (sm->s[dir]) &&
	    sm->s[dir][sw_if_index].rate > 0.)
	  sm->s[dir][sw_if_index].codel_off = !codel;
    }
  if (!error && (target != ~0 || interval != ~0))
    {
      int dir;
      for (dir = 0; dir < 2; dir++)
	if (sw_if_index < vec_len (sm->s[dir]) &&
	    sm->s[dir][sw_if_index].rate > 0.)
	  {
	    if (target != ~0)
	      sm->s[dir][sw_if_index].codel_target_us = target;
	    if (interval != ~0)
	      sm->s[dir][sw_if_index].codel_interval_us = interval;
	  }
    }
done:
  unformat_free (line);
  return error;
}

VLIB_CLI_COMMAND (bpa_set_pppoe_shaper_command, static) = {
  .path = "set bpa pppoe-shaper",
  .short_help = "set bpa pppoe-shaper <interface> | sw-if-index <n> "
		"[down <kbit>] [up <kbit>] [queue <ms>] [codel on|off] | "
		"buffers <n>",
  .function = bpa_set_pppoe_shaper_command_fn,
};

/* not a unit: one shaped direction's lines, read by prismash's
 * pppoe.shaper_counts() by these words */
static void
bpa_shaper_show_one (vlib_main_t *vm, u32 sw_if_index, int dir)
{
  bpa_shaper_main_t *sm = &bpa_shaper_main;
  vnet_main_t *vnm = vnet_get_main ();
  bpa_shaper_t *s = vec_elt_at_index (sm->s[dir], sw_if_index);

  vlib_cli_output (vm,
		   "%U sw-if-index %u %s rate %u kbit queue %u ms limit %u "
		   "bytes burst %u bytes owner %d",
		   format_vnet_sw_if_index_name, vnm, sw_if_index, sw_if_index,
		   dir == BPA_SHAPER_UP ? "up" : "down", s->kbit, s->queue_ms,
		   s->limit, s->burst,
		   s->owner == BPA_SHAPER_UNBOUND ? -1 : (int) s->owner);
  vlib_cli_output (vm,
		   "  queued %u packets %u bytes %u ms, most %u bytes",
		   s->q_pkts, s->q_bytes, bpa_shaper_queue_ms_now (s),
		   s->q_bytes_max);
  vlib_cli_output (vm,
		   "  passed %lu delayed %lu dropped-queue %lu dropped-codel %lu "
		   "dropped-box %lu bytes %lu",
		   s->passed, s->delayed, s->drop_full, s->drop_aqm, s->drop_box,
		   s->bytes);
  vlib_cli_output (vm, "  delay-avg-us %lu delay-max-us %u codel %s",
		   s->delayed ? s->delay_sum_us / s->delayed : 0,
		   s->delay_max_us, s->codel_off ? "off" : "on");
}

/* not a unit: a VLIB_CLI_COMMAND body, driven by prismash's `show pppoe
 * session <id>` and lab/pppoe.sh */
static clib_error_t *
bpa_show_pppoe_shaper_command_fn (vlib_main_t *vm, unformat_input_t *input,
				  vlib_cli_command_t *cmd)
{
  bpa_shaper_main_t *sm = &bpa_shaper_main;
  vnet_main_t *vnm = vnet_get_main ();
  u32 sw_if_index = ~0, i, held = 0, active = 0;
  int all = 0, dir;

  while (unformat_check_input (input) != UNFORMAT_END_OF_INPUT)
    {
      if (unformat (input, "sw-if-index %u", &sw_if_index))
	;
      else if (unformat (input, "all"))
	all = 1;
      else if (unformat (input, "%U", unformat_vnet_sw_interface, vnm,
			 &sw_if_index))
	;
      else
	return clib_error_return (0, "unknown input '%U'",
				  format_unformat_error, input);
    }
  for (i = 0; i < vec_len (sm->pt); i++)
    {
      held += sm->pt[i].held;
      active += sm->pt[i].n_active;
    }
  vlib_cli_output (vm,
		   "pppoe-shaper: %u down %u up shaped, %u with a queue, "
		   "buffers held %u, %u a thread at most",
		   sm->n_shaped[BPA_SHAPER_DOWN], sm->n_shaped[BPA_SHAPER_UP],
		   active, held, sm->cap);
  for (dir = 0; dir < 2; dir++)
    for (i = 0; i < vec_len (sm->s[dir]); i++)
      if (sm->s[dir][i].rate > 0. && (all || i == sw_if_index))
	bpa_shaper_show_one (vm, i, dir);
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_pppoe_shaper_command, static) = {
  .path = "show bpa pppoe-shaper",
  .short_help = "show bpa pppoe-shaper [<interface> | sw-if-index <n> | all]",
  .function = bpa_show_pppoe_shaper_command_fn,
};
