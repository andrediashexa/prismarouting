/*
 * bpa_ra.c - a received Router Advertisement reaches the Linux side of a
 * linux_cp pair, which is what BGP unnumbered is waiting for.
 *
 * Not IPv6 multicast in general, and that was the surprise: an OSPFv3 packet
 * to ff02::5 already arrives on the TAP of a pair with no address on either
 * end, measured. VPP has no local handler for protocol 89, so it falls
 * through ip6-local to ip6-punt and linux_cp's redirect takes it from there -
 * the same reason OSPFv2's 224.0.0.5 has always worked. ICMPv6 is the
 * exception, because VPP handles ICMPv6 itself.
 *
 * Why this node has to exist, measured on VPP 26.06:
 *
 * VPP hands a locally-received ICMPv6 packet to the node registered for its
 * type. For type 134 that is `icmp6-router-advertisement`, and that node is
 * registered with ONE next node - "ip6-drop". There is no punt path in it and
 * no configuration that adds one, so an advertisement from a neighbour is
 * counted and thrown away, whatever the interface it arrived on.
 *
 * Its siblings do not all do that, which is what makes this a hole rather
 * than a design:
 *
 *   icmp6-neighbor-advertisement   next node: "ip6-punt"
 *   icmp6-neighbor-solicitation    answered by VPP, or dropped
 *   icmp6-router-solicitation      punts when this box is not configured to
 *                                  answer, with the comment "but maybe Linux
 *                                  is via LCP, so punt the request"
 *
 * `ip6-punt` is where linux_cp is waiting: creating a pair installs an IPv6
 * punt redirect for the phy's rx that DVRs the packet, ethernet header and
 * all, out of the pair's TAP. Neighbour advertisements reach Linux through
 * exactly that path today. Router advertisements are the one ND message that
 * never gets there.
 *
 * What it costs when they do not: FRR learns an unnumbered peer's address
 * from the peer's Router Advertisement and from nothing else. Without it
 * zebra never has an address for the neighbour, and `neighbor <if> interface
 * remote-as external` - a configuration the VTY accepts and FRR holds - sits
 * at Idle with "remote AS 0" for ever (#213, #208).
 *
 * Punted for every interface rather than only for paired ones. An interface
 * with no pair has no punt redirect, so `ip6-punt` drops the packet, which is
 * the fate it had before this node existed. The half that IS given up is
 * VPP's own processing of a received advertisement - the RS retransmit timer
 * and the ip6_ra_report subscribers, which are the DHCPv6-PD client's, not
 * this product's. On a box where every dataplane port is paired into the
 * kernel, the kernel is the IPv6 control plane, exactly as it already is for
 * ARP: linux_cp punts that to Linux with nodes of its own.
 *
 * In this plugin because it is the only VPP plugin this product ships. A
 * second one for a single node would cost a CMake target, an install path, an
 * ISO packaging step and a line in every startup configuration.
 */

#include <vlib/vlib.h>
#include <vnet/vnet.h>
#include <vnet/ip/icmp6.h>
#include <vnet/ip/ip6.h>
#include <vnet/ip/format.h>

typedef struct
{
  u32 sw_if_index;
  ip6_address_t src;
} bpa_ra_trace_t;

/* not a unit: a VPP format function over a va_list, which is the class bpa_cli.c's
 * header describes: it takes its arguments off a va_list a caller built and
 * writes through format(), so a harness would be building VPP's own
 * plumbing to watch a string come out. `show trace` after any lab that
 * traces is where these are read */
static u8 *
format_bpa_ra_trace (u8 *s, va_list *args)
{
  CLIB_UNUSED (vlib_main_t * vm) = va_arg (*args, vlib_main_t *);
  CLIB_UNUSED (vlib_node_t * node) = va_arg (*args, vlib_node_t *);
  bpa_ra_trace_t *t = va_arg (*args, bpa_ra_trace_t *);

  return format (s, "BPA RA punt: rx %U from %U", format_vnet_sw_if_index_name,
		 vnet_get_main (), t->sw_if_index, format_ip6_address, &t->src);
}

#define foreach_bpa_ra_error                                                  \
  _ (PUNTED, "router advertisements punted to the host")

typedef enum
{
#define _(sym, str) BPA_RA_ERROR_##sym,
  foreach_bpa_ra_error
#undef _
    BPA_RA_N_ERROR,
} bpa_ra_error_t;

/*
 * Declared with a severity rather than as a plain error string: this counts
 * the node WORKING, and `show errors` files a plain string under "error". An
 * operator reading that list on a healthy box would be chasing the one line
 * that says the box is doing its job.
 */
static vlib_error_desc_t bpa_ra_error_counters[] = {
#define _(sym, str) { #sym, str, VL_COUNTER_SEVERITY_INFO },
  foreach_bpa_ra_error
#undef _
};

typedef enum
{
  BPA_RA_NEXT_PUNT,
  BPA_RA_N_NEXT,
} bpa_ra_next_t;

VLIB_NODE_FN (bpa_ra_node)
(vlib_main_t *vm, vlib_node_runtime_t *node, vlib_frame_t *frame)
{
  u32 n_left_from, *from, *to_next, next_index;

  from = vlib_frame_vector_args (frame);
  n_left_from = frame->n_vectors;
  next_index = node->cached_next_index;

  while (n_left_from > 0)
    {
      u32 n_left_to_next;

      vlib_get_next_frame (vm, node, next_index, to_next, n_left_to_next);

      while (n_left_from > 0 && n_left_to_next > 0)
	{
	  vlib_buffer_t *b0;
	  u32 bi0, next0 = BPA_RA_NEXT_PUNT;

	  bi0 = to_next[0] = from[0];
	  from += 1;
	  to_next += 1;
	  n_left_from -= 1;
	  n_left_to_next -= 1;

	  b0 = vlib_get_buffer (vm, bi0);

	  /*
	   * ip6-icmp-input dispatches with the buffer still at the IPv6
	   * header - it reads the type through ip6_next_header rather than
	   * advancing - so the packet handed on is whole and needs no rewind.
	   * icmp6-neighbor-advertisement punts from the same position.
	   */
	  if (PREDICT_FALSE (b0->flags & VLIB_BUFFER_IS_TRACED))
	    {
	      ip6_header_t *ip0 = vlib_buffer_get_current (b0);
	      bpa_ra_trace_t *t = vlib_add_trace (vm, node, b0, sizeof (*t));
	      t->sw_if_index = vnet_buffer (b0)->sw_if_index[VLIB_RX];
	      t->src = ip0->src_address;
	    }

	  vlib_validate_buffer_enqueue_x1 (vm, node, next_index, to_next,
					   n_left_to_next, bi0, next0);
	}

      vlib_put_next_frame (vm, node, next_index, n_left_to_next);
    }

  vlib_node_increment_counter (vm, node->node_index, BPA_RA_ERROR_PUNTED,
			       frame->n_vectors);

  return frame->n_vectors;
}

VLIB_REGISTER_NODE (bpa_ra_node) = {
  .name = "bpa-icmp6-ra-punt",
  .vector_size = sizeof (u32),
  .format_trace = format_bpa_ra_trace,
  .type = VLIB_NODE_TYPE_INTERNAL,
  .n_errors = BPA_RA_N_ERROR,
  .error_counters = bpa_ra_error_counters,
  .n_next_nodes = BPA_RA_N_NEXT,
  .next_nodes = { [BPA_RA_NEXT_PUNT] = "ip6-punt" },
};

/* not a unit: the node's init, called once by VPP with the graph up. */
static clib_error_t *
bpa_ra_init (vlib_main_t *vm)
{
  /*
   * AFTER ip6_ra_init, which registers the node that drops them. The last
   * registration for a type wins, so running before it would be overwritten
   * and this file would be dead code that still compiled.
   */
  icmp6_register_type (vm, ICMP6_router_advertisement, bpa_ra_node.index);
  return 0;
}

VLIB_INIT_FUNCTION (bpa_ra_init) = {
  .runs_after = VLIB_INITS ("ip6_ra_init"),
};
