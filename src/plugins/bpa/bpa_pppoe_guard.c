/*
 * bpa_pppoe_guard.c - PPPoE accepted only on the access interfaces, and a
 * subscriber's MAC held by the access interface its session is on (#597):
 * `set bpa pppoe-access`, `show bpa pppoe-access`, and the two nodes that
 * stand between ethernet-input and VPP's pppoe plugin.
 *
 * bpa_pppoe_guard.h says what VPP does without this, measured, and what is
 * decided per frame. This file is the VPP-facing half; every function here
 * carries `not a unit:`, and lab/pppoe.sh sections 18 and 19 drive it.
 *
 * HOW IT GETS IN FRONT OF THE PLUGIN WITHOUT A CHANGE TO VPP. ethernet-input
 * picks the next node of an L3 frame by its ethertype, from one table
 * (ethernet_main.l3_next) that `ethernet_register_input_type` fills, and a
 * second registration of an ethertype REPLACES the first one's entry
 * (vnet/ethernet/node.c: next_by_ethertype_register writes the slot the
 * sparse vector already has). pppoe_init() registers 0x8863 to
 * pppoe-cp-dispatch and 0x8864 to pppoe-input at plugin init; the first
 * `set bpa pppoe-access` registers both to the guard's nodes, whose only
 * next, past the drop, is the node the plugin had. Not at this plugin's
 * init: a box with no PPPoE keeps VPP's path exactly as it was, and the
 * plugin's own drop of what it punts to local0 (docs/bng.md 0.3 item 4).
 * Once hooked it stays hooked until the dataplane restarts - with an empty
 * set it refuses every PPPoE frame, which is right when no control plane
 * listens - and the container's entrypoint lays it again after a restart,
 * as it lays the TAP.
 *
 * FRAMES FROM THE CONTROL PLANE DO NOT PASS HERE. `create pppoe cp` puts
 * pppoe-input on the TAP's device-input arc, which runs before
 * ethernet-input, so what accel-ppp sends never meets the guard - except a
 * session's IPv6 (#609), which bpa-pppoe-cp-ip6 takes off that arc first.
 *
 * IPv6 ON A SESSION (#609, docs/bng.md 0.8): the CPE's Router Solicitation
 * and DHCPv6 go to the control plane from the session guard, and the
 * control plane's answers go to the subscriber from bpa-pppoe-cp-ip6; both
 * through pppoe-cp-dispatch, the path an LCP frame takes each way.
 *
 * THE COST, on every PPPoE frame: one more node, a bit read for the
 * interface, and for a data frame nothing else. A discovery or control frame
 * takes a spinlock and a 4-way set: one per subscriber every
 * lcp-echo-interval, and per dial. Measured in lab/pppoe.sh with `show
 * runtime` (docs/bng.md 0.3 item 10).
 */

#include <vlib/vlib.h>
#include <vnet/vnet.h>
#include <vnet/ethernet/ethernet.h>
#include <vppinfra/lock.h>

#include "bpa.h"
#include "bpa_pppoe_guard.h"

#define foreach_bpa_pppoe_guard_error                                        \
  _ (PASSED, "PPPoE frames passed to the pppoe plugin")                      \
  _ (NOT_ACCESS, "PPPoE frames from an interface that is not access")        \
  _ (HELD, "PPPoE frames for a MAC held by another access interface")        \
  _ (PUNTED, "IPv6 RS and DHCPv6 sent to the PPPoE control plane")           \
  _ (FROM_CP, "IPv6 from the PPPoE control plane sent to the subscriber")

typedef enum
{
#define _(sym, str) BPA_PPPOE_GUARD_ERROR_##sym,
  foreach_bpa_pppoe_guard_error
#undef _
    BPA_PPPOE_GUARD_N_ERROR,
} bpa_pppoe_guard_error_t;

static char *bpa_pppoe_guard_error_strings[] = {
#define _(sym, str) str,
  foreach_bpa_pppoe_guard_error
#undef _
};

typedef struct
{
  uword *access;	  /* bitmap, by sw_if_index */
  bpa_pppoe_held_t *held; /* 4 << BPA_PPPOE_HOLD_SETS_LOG2 ways */
  clib_spinlock_t lock;
  u32 hold;
  u8 hooked;
  u32 next_discovery; /* pppoe-cp-dispatch, from the discovery guard */
  u32 next_session;   /* pppoe-input, from the session guard */
  u32 next_session_cp; /* pppoe-cp-dispatch, from the session guard (#609) */
  u32 cp_sw_if_index;  /* the control plane's TAP, ~0 none (#609) */
  u32 next_cp_ip6;     /* pppoe-cp-dispatch, from bpa-pppoe-cp-ip6 */
  u8 cp_set;
} bpa_pppoe_guard_main_t;

static bpa_pppoe_guard_main_t bpa_pppoe_guard_main;

typedef struct
{
  u32 sw_if_index;
  u8 verdict;
  u8 code;
  u16 ppp_proto;
} bpa_pppoe_guard_trace_t;

/* not a unit: a trace formatter VPP calls with a va_list */
static u8 *
format_bpa_pppoe_guard_trace (u8 *s, va_list *args)
{
  CLIB_UNUSED (vlib_main_t * vm) = va_arg (*args, vlib_main_t *);
  CLIB_UNUSED (vlib_node_t * node) = va_arg (*args, vlib_node_t *);
  bpa_pppoe_guard_trace_t *t = va_arg (*args, bpa_pppoe_guard_trace_t *);
  static const char *v[] = { "pass", "not an access interface",
			     "MAC held by another access interface",
			     "IPv6 control, to the control plane" };
  return format (s, "bpa-pppoe-guard: sw_if_index %u code 0x%x proto 0x%x %s",
		 t->sw_if_index, t->code, t->ppp_proto,
		 v[t->verdict < 4 ? t->verdict : 0]);
}

/* not a unit: one frame's verdict, with the table taken under its lock
 * when the frame needs it; bpa_pppoe_guard_verdict() is the decision, and
 * test/test_bpa_pppoe_guard.c drives it */
static_always_inline bpa_pppoe_verdict_t
bpa_pppoe_guard_one (bpa_pppoe_guard_main_t *gm, vlib_buffer_t *b,
		     int is_discovery, u32 now, u8 *code, u16 *proto)
{
  /* ethernet-input left current_data at the PPPoE header: ver_type, code,
   * session id, length, and - session stage - the PPP protocol. */
  u8 *p = vlib_buffer_get_current (b);
  u32 rx = vnet_buffer (b)->sw_if_index[VLIB_RX];
  bpa_pppoe_verdict_t v;
  int ip6c = 0;

  *code = p[1];
  *proto = is_discovery ? 0 : clib_net_to_host_u16 (*(u16 *) (p + 6));
  if (!clib_bitmap_get (gm->access, rx))
    return BPA_PPPOE_NOT_ACCESS;
  /* A session's IPv6: its RS and DHCPv6 go to the control plane (#609).
   * Three bytes read of an IPv6 data packet, nothing of an IPv4 one. */
  if (*proto == BPA_PPP_IP6 && !is_discovery && b->current_length > 8)
    ip6c = bpa_pppoe_ip6_is_control (p + 8, b->current_length - 8);
  if (!bpa_pppoe_needs_table (is_discovery, *proto, ip6c))
    return BPA_PPPOE_PASS;
  ethernet_header_t *eh = ethernet_buffer_get_header (b);
  u64 key = bpa_pppoe_mac_key (eh->src_address);
  clib_spinlock_lock (&gm->lock);
  v = bpa_pppoe_guard_verdict (gm->held, BPA_PPPOE_HOLD_SETS_LOG2, key, rx, 1,
			       is_discovery, *code, *proto, ip6c, now,
			       gm->hold);
  clib_spinlock_unlock (&gm->lock);
  return v;
}

/* not a unit: the nodes' body; it needs frames and the graph.
 *
 * A FRAME THAT ALL PASSES - every subscriber's data, the common case - is
 * handed on whole (enqueue_to_single_next): with a next per packet the
 * session guard read 143 clocks a packet in lab/pppoe.sh's `show runtime`
 * against pppoe-input's 153, and 106 against 133 after (docs/bng.md 0.3
 * item 10) */
static_always_inline uword
bpa_pppoe_guard_inline (vlib_main_t *vm, vlib_node_runtime_t *node,
			vlib_frame_t *frame, int is_discovery)
{
  bpa_pppoe_guard_main_t *gm = &bpa_pppoe_guard_main;
  u32 n = frame->n_vectors, *from = vlib_frame_vector_args (frame);
  vlib_buffer_t *bufs[VLIB_FRAME_SIZE];
  u16 nexts[VLIB_FRAME_SIZE];
  u32 pass_next = is_discovery ? gm->next_discovery : gm->next_session;
  u32 now = (u32) vlib_time_now (vm);
  u32 i, n_pass = 0, n_punt = 0;

  vlib_get_buffers (vm, from, bufs, n);
  for (i = 0; i < n; i++)
    {
      vlib_buffer_t *b = bufs[i];
      u8 code;
      u16 proto;
      bpa_pppoe_verdict_t v =
	bpa_pppoe_guard_one (gm, b, is_discovery, now, &code, &proto);

      if (PREDICT_TRUE (v == BPA_PPPOE_PASS))
	{
	  nexts[i] = pass_next;
	  n_pass++;
	}
      else if (v == BPA_PPPOE_PUNT)
	{
	  /* pppoe-cp-dispatch reads the PPPoE header where ethernet-input
	   * left it, as pppoe-input hands it a control frame */
	  nexts[i] = gm->next_session_cp;
	  n_punt++;
	}
      else
	{
	  nexts[i] = 0; /* error-drop */
	  b->error = node->errors[v == BPA_PPPOE_HELD ?
				    BPA_PPPOE_GUARD_ERROR_HELD :
				    BPA_PPPOE_GUARD_ERROR_NOT_ACCESS];
	}
      if (PREDICT_FALSE (b->flags & VLIB_BUFFER_IS_TRACED))
	{
	  bpa_pppoe_guard_trace_t *t = vlib_add_trace (vm, node, b, sizeof (*t));
	  t->sw_if_index = vnet_buffer (b)->sw_if_index[VLIB_RX];
	  t->verdict = v;
	  t->code = code;
	  t->ppp_proto = proto;
	}
    }
  vlib_node_increment_counter (vm, node->node_index,
			       BPA_PPPOE_GUARD_ERROR_PASSED, n_pass);
  if (n_punt)
    vlib_node_increment_counter (vm, node->node_index,
				 BPA_PPPOE_GUARD_ERROR_PUNTED, n_punt);
  if (PREDICT_TRUE (n_pass == n))
    vlib_buffer_enqueue_to_single_next (vm, node, from, pass_next, n);
  else
    vlib_buffer_enqueue_to_next (vm, node, from, nexts, n);
  return n;
}

VLIB_NODE_FN (bpa_pppoe_discovery_guard_node)
(vlib_main_t *vm, vlib_node_runtime_t *node, vlib_frame_t *frame)
{
  return bpa_pppoe_guard_inline (vm, node, frame, 1);
}

VLIB_NODE_FN (bpa_pppoe_session_guard_node)
(vlib_main_t *vm, vlib_node_runtime_t *node, vlib_frame_t *frame)
{
  return bpa_pppoe_guard_inline (vm, node, frame, 0);
}

VLIB_REGISTER_NODE (bpa_pppoe_discovery_guard_node) = {
  .name = "bpa-pppoe-discovery-guard",
  .vector_size = sizeof (u32),
  .format_trace = format_bpa_pppoe_guard_trace,
  .type = VLIB_NODE_TYPE_INTERNAL,
  .n_errors = BPA_PPPOE_GUARD_N_ERROR,
  .error_strings = bpa_pppoe_guard_error_strings,
  .n_next_nodes = 1,
  .next_nodes = { [0] = "error-drop" },
};

VLIB_REGISTER_NODE (bpa_pppoe_session_guard_node) = {
  .name = "bpa-pppoe-session-guard",
  .vector_size = sizeof (u32),
  .format_trace = format_bpa_pppoe_guard_trace,
  .type = VLIB_NODE_TYPE_INTERNAL,
  .n_errors = BPA_PPPOE_GUARD_N_ERROR,
  .error_strings = bpa_pppoe_guard_error_strings,
  .n_next_nodes = 1,
  .next_nodes = { [0] = "error-drop" },
};

/*
 * The other way (#609): a session's IPv6 that the control plane sends - its
 * Router Advertisement, its DHCPv6 reply - taken off the TAP before VPP's
 * pppoe-input, which would look the session up by the source MAC (the
 * access card's) and drop it, and handed to pppoe-cp-dispatch as pppoe-
 * input hands it an LCP frame: the PPPoE header current, the frame sent out
 * of the access interface the client's MAC is on. A feature on the TAP's
 * device-input arc, in front of pppoe-input, laid by `set bpa pppoe-access
 * cp <tap>`; every other frame goes on to pppoe-input untouched.
 */
typedef struct
{
  u32 is_ip6;
} bpa_pppoe_cp_trace_t;

/* not a unit: a trace formatter VPP calls with a va_list */
static u8 *
format_bpa_pppoe_cp_trace (u8 *s, va_list *args)
{
  CLIB_UNUSED (vlib_main_t * vm) = va_arg (*args, vlib_main_t *);
  CLIB_UNUSED (vlib_node_t * node) = va_arg (*args, vlib_node_t *);
  bpa_pppoe_cp_trace_t *t = va_arg (*args, bpa_pppoe_cp_trace_t *);
  return format (s, "bpa-pppoe-cp-ip6: %s",
		 t->is_ip6 ? "a session's IPv6, to the subscriber" :
			     "on to pppoe-input");
}

/* not a unit: the node's body; it needs frames and the graph, and
 * bpa_pppoe_cp_is_ip6() is the decision test/test_bpa_pppoe_guard.c drives */
VLIB_NODE_FN (bpa_pppoe_cp_ip6_node)
(vlib_main_t *vm, vlib_node_runtime_t *node, vlib_frame_t *frame)
{
  bpa_pppoe_guard_main_t *gm = &bpa_pppoe_guard_main;
  u32 n = frame->n_vectors, *from = vlib_frame_vector_args (frame);
  vlib_buffer_t *bufs[VLIB_FRAME_SIZE];
  u16 nexts[VLIB_FRAME_SIZE];
  u32 i, n_ip6 = 0;

  vlib_get_buffers (vm, from, bufs, n);
  for (i = 0; i < n; i++)
    {
      vlib_buffer_t *b = bufs[i];
      u32 at = 0, next = 0;
      int ip6 = bpa_pppoe_cp_is_ip6 (vlib_buffer_get_current (b),
				     b->current_length, &at);

      if (ip6 && gm->hooked)
	{
	  vlib_buffer_advance (b, at);
	  nexts[i] = gm->next_cp_ip6;
	  n_ip6++;
	}
      else
	{
	  vnet_feature_next (&next, b);
	  nexts[i] = next;
	}
      if (PREDICT_FALSE (b->flags & VLIB_BUFFER_IS_TRACED))
	{
	  bpa_pppoe_cp_trace_t *t = vlib_add_trace (vm, node, b, sizeof (*t));
	  t->is_ip6 = ip6 && gm->hooked;
	}
    }
  if (n_ip6)
    vlib_node_increment_counter (vm, bpa_pppoe_session_guard_node.index,
				 BPA_PPPOE_GUARD_ERROR_FROM_CP, n_ip6);
  vlib_buffer_enqueue_to_next (vm, node, from, nexts, n);
  return n;
}

VLIB_REGISTER_NODE (bpa_pppoe_cp_ip6_node) = {
  .name = "bpa-pppoe-cp-ip6",
  .vector_size = sizeof (u32),
  .format_trace = format_bpa_pppoe_cp_trace,
  .type = VLIB_NODE_TYPE_INTERNAL,
  /* pppoe-cp-dispatch is added when the guard is hooked: named here, a
   * dataplane started without the pppoe plugin would refuse to start */
  .n_next_nodes = 1,
  .next_nodes = { [0] = "error-drop" },
};

VNET_FEATURE_INIT (bpa_pppoe_cp_ip6_feature, static) = {
  .arc_name = "device-input",
  .node_name = "bpa-pppoe-cp-ip6",
  .runs_before = VNET_FEATURES ("pppoe-input"),
};

/* not a unit: puts the guard's nodes in front of the pppoe plugin's, once;
 * needs the graph and runs under the worker barrier (a CLI command that is
 * not mp-safe), lab/pppoe.sh section 18 drives it */
static clib_error_t *
bpa_pppoe_guard_hook (vlib_main_t *vm)
{
  bpa_pppoe_guard_main_t *gm = &bpa_pppoe_guard_main;
  vlib_node_t *cpd, *inp;

  if (gm->hooked)
    return 0;
  cpd = vlib_get_node_by_name (vm, (u8 *) "pppoe-cp-dispatch");
  inp = vlib_get_node_by_name (vm, (u8 *) "pppoe-input");
  if (!cpd || !inp)
    return clib_error_return (0, "the pppoe plugin is not loaded");
  gm->next_discovery = vlib_node_add_next (
    vm, bpa_pppoe_discovery_guard_node.index, cpd->index);
  gm->next_session =
    vlib_node_add_next (vm, bpa_pppoe_session_guard_node.index, inp->index);
  gm->next_session_cp =
    vlib_node_add_next (vm, bpa_pppoe_session_guard_node.index, cpd->index);
  gm->next_cp_ip6 =
    vlib_node_add_next (vm, bpa_pppoe_cp_ip6_node.index, cpd->index);
  vec_validate_aligned (gm->held,
			(BPA_PPPOE_HOLD_WAYS << BPA_PPPOE_HOLD_SETS_LOG2) - 1,
			CLIB_CACHE_LINE_BYTES);
  clib_spinlock_init (&gm->lock);
  ethernet_register_input_type (vm, ETHERNET_TYPE_PPPOE_DISCOVERY,
				bpa_pppoe_discovery_guard_node.index);
  ethernet_register_input_type (vm, ETHERNET_TYPE_PPPOE_SESSION,
				bpa_pppoe_session_guard_node.index);
  gm->hooked = 1;
  return 0;
}

/* not a unit: a VLIB_CLI_COMMAND body, driven by appliance/prisma-bng-
 * entrypoint at every start of the container and by lab/pppoe.sh */
static clib_error_t *
bpa_set_pppoe_access_command_fn (vlib_main_t *vm, unformat_input_t *input,
				 vlib_cli_command_t *cmd)
{
  bpa_pppoe_guard_main_t *gm = &bpa_pppoe_guard_main;
  vnet_main_t *vnm = vnet_get_main ();
  unformat_input_t _line, *line = &_line;
  uword *want = 0;
  u32 sw_if_index, hold = BPA_PPPOE_HOLD_DEFAULT, cp = ~0;
  clib_error_t *error = 0;

  if (unformat_user (input, unformat_line_input, line))
    {
      while (unformat_check_input (line) != UNFORMAT_END_OF_INPUT)
	{
	  if (unformat (line, "hold %u", &hold))
	    ;
	  else if (unformat (line, "cp %U", unformat_vnet_sw_interface, vnm,
			     &cp))
	    ;
	  else if (unformat (line, "%U", unformat_vnet_sw_interface, vnm,
			     &sw_if_index))
	    want = clib_bitmap_set (want, sw_if_index, 1);
	  else
	    {
	      error = clib_error_return (0, "unknown input '%U'",
					 format_unformat_error, line);
	      break;
	    }
	}
      unformat_free (line);
    }
  if (!error && (hold < 1 || hold > 86400))
    error = clib_error_return (0, "hold is 1-86400 seconds");
  if (!error)
    error = bpa_pppoe_guard_hook (vm);
  if (error)
    {
      clib_bitmap_free (want);
      return error;
    }
  /* Under the barrier: no worker is reading the bitmap being replaced. */
  clib_bitmap_free (gm->access);
  gm->access = want;
  gm->hold = hold;
  /* The control plane's TAP (#609): its sessions' IPv6 to the subscriber.
   * Once per TAP - a feature enabled twice runs twice - and moved when the
   * TAP is another; not given, it stays where it is, as the TAP does
   * (prisma-bng-entrypoint's unplumb leaves it in place). */
  if (cp != ~0 && (!gm->cp_set || gm->cp_sw_if_index != cp))
    {
      if (gm->cp_set)
	vnet_feature_enable_disable ("device-input", "bpa-pppoe-cp-ip6",
				     gm->cp_sw_if_index, 0, 0, 0);
      vnet_feature_enable_disable ("device-input", "bpa-pppoe-cp-ip6", cp, 1,
				   0, 0);
      gm->cp_sw_if_index = cp;
      gm->cp_set = 1;
    }
  return 0;
}

VLIB_CLI_COMMAND (bpa_set_pppoe_access_command, static) = {
  .path = "set bpa pppoe-access",
  .short_help = "set bpa pppoe-access [hold <seconds>] [cp <tap>] "
		"[<interface> ...]",
  .function = bpa_set_pppoe_access_command_fn,
};

/* not a unit: the sum of one counter of a node over every thread, from the
 * error heap - VPP's `show errors` sums it the same way */
static u64
bpa_pppoe_guard_count (vlib_main_t *vm, u32 node_index, u32 code)
{
  u64 sum = 0;
  vlib_node_t *n = vlib_get_node (vm, node_index);
  foreach_vlib_main ()
    {
      vlib_error_main_t *em = &this_vlib_main->error_main;
      u32 i = n->error_heap_index + code;
      if (i < vec_len (em->counters))
	sum += em->counters[i];
    }
  return sum;
}

/* not a unit: a VLIB_CLI_COMMAND body, driven by lab/pppoe.sh - whose
 * lines it reads by these words */
static clib_error_t *
bpa_show_pppoe_access_command_fn (vlib_main_t *vm, unformat_input_t *input,
				  vlib_cli_command_t *cmd)
{
  bpa_pppoe_guard_main_t *gm = &bpa_pppoe_guard_main;
  vnet_main_t *vnm = vnet_get_main ();
  u32 i, held = 0;
  u8 *names = 0;

  if (!gm->hooked)
    {
      vlib_cli_output (vm, "pppoe-access: not set; VPP's pppoe plugin takes "
			   "PPPoE from every interface");
      return 0;
    }
  clib_bitmap_foreach (i, gm->access)
    {
      names = format (names, " %U", format_vnet_sw_if_index_name, vnm, i);
    }
  clib_spinlock_lock (&gm->lock);
  held = bpa_pppoe_held_count (gm->held, BPA_PPPOE_HOLD_SETS_LOG2,
			       (u32) vlib_time_now (vm), gm->hold);
  clib_spinlock_unlock (&gm->lock);
  vlib_cli_output (vm, "pppoe-access:%v", names ? names : (u8 *) " none");
  vlib_cli_output (vm, "hold %u s, macs held %u", gm->hold, held);
  vlib_cli_output (
    vm, "discovery passed %lu not-access %lu held %lu",
    bpa_pppoe_guard_count (vm, bpa_pppoe_discovery_guard_node.index,
			   BPA_PPPOE_GUARD_ERROR_PASSED),
    bpa_pppoe_guard_count (vm, bpa_pppoe_discovery_guard_node.index,
			   BPA_PPPOE_GUARD_ERROR_NOT_ACCESS),
    bpa_pppoe_guard_count (vm, bpa_pppoe_discovery_guard_node.index,
			   BPA_PPPOE_GUARD_ERROR_HELD));
  vlib_cli_output (
    vm, "session passed %lu not-access %lu held %lu",
    bpa_pppoe_guard_count (vm, bpa_pppoe_session_guard_node.index,
			   BPA_PPPOE_GUARD_ERROR_PASSED),
    bpa_pppoe_guard_count (vm, bpa_pppoe_session_guard_node.index,
			   BPA_PPPOE_GUARD_ERROR_NOT_ACCESS),
    bpa_pppoe_guard_count (vm, bpa_pppoe_session_guard_node.index,
			   BPA_PPPOE_GUARD_ERROR_HELD));
  vlib_cli_output (
    vm, "ip6 to the control plane %lu from it %lu",
    bpa_pppoe_guard_count (vm, bpa_pppoe_session_guard_node.index,
			   BPA_PPPOE_GUARD_ERROR_PUNTED),
    bpa_pppoe_guard_count (vm, bpa_pppoe_session_guard_node.index,
			   BPA_PPPOE_GUARD_ERROR_FROM_CP));
  if (gm->cp_set)
    vlib_cli_output (vm, "control plane %U", format_vnet_sw_if_index_name,
		     vnm, gm->cp_sw_if_index);
  vec_free (names);
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_pppoe_access_command, static) = {
  .path = "show bpa pppoe-access",
  .short_help = "show bpa pppoe-access",
  .function = bpa_show_pppoe_access_command_fn,
};
