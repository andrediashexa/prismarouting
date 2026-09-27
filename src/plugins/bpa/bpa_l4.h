/*
 * bpa_l4.h - L4 identifier extraction, shared by the translation nodes and
 * the handoff nodes. The handoff nodes need the destination port to work
 * out which subscriber owns a returning packet.
 */

#ifndef __included_bpa_l4_h__
#define __included_bpa_l4_h__

#include <vnet/ip/ip4_packet.h>
#include <vnet/ip/icmp46_packet.h>

#include <vnet/buffer.h>

#include "bpa_session.h"

/*
 * Enhanced GRE, RFC 2637 section 4.1. The Call ID sits in the low half of the
 * Key field and is what stands in for a port here.
 *
 *   byte 0  C R K S s Recur      - K (0x20) MUST be set, C and R MUST be 0
 *   byte 1  A Flags Ver          - Ver MUST be 1
 *   bytes 2-3  Protocol Type     - 0x880B
 *   bytes 4-5  Key high: payload length
 *   bytes 6-7  Key low:  Call ID
 */
#include "bpa_gre.h"
#include "bpa_l4_wire.h"

/*
 * Returns 0 and the L4 id; -1 when the packet is not translatable.
 *
 * `*l4h` comes out zero when the packet carries no L4 header - the case of a
 * non-first fragment. For those, the ports come from VPP's shallow virtual
 * reassembly, which kept them from the first fragment. Without it, a fragment
 * with no port matches no session and is dropped: large DNS, VPN and gaming
 * start failing intermittently.
 */
/* not a unit: reads VPP's buffer metadata (the fragment flag and the ports
 * shallow reassembly kept) and hands them to bpa_l4_reass() and
 * bpa_l4_read() in bpa_l4_wire.h, which test/test_bpa_l4.c holds (#472) */
static_always_inline int
bpa_l4_id (vlib_buffer_t *b, ip4_header_t *ip, u8 proto, int want_dst,
	   u16 *port, void **l4h)
{
  if (PREDICT_FALSE (proto != BPA_PROTO_ESP
		     && vnet_buffer (b)->ip.reass.is_non_first_fragment))
    return bpa_l4_reass (proto, want_dst,
			 vnet_buffer (b)->ip.reass.l4_src_port,
			 vnet_buffer (b)->ip.reass.l4_dst_port, port, l4h);
  return bpa_l4_read (ip, (u8 *) vlib_buffer_get_current (b)
			    + b->current_length, proto, want_dst, port, l4h);
}

/* not a unit: bpa_l4_id() for the source side */
static_always_inline int
bpa_l4_src (vlib_buffer_t *b, ip4_header_t *ip, u8 proto, u16 *port,
	    void **l4h)
{
  return bpa_l4_id (b, ip, proto, 0, port, l4h);
}

/* not a unit: bpa_l4_id() for the destination side */
static_always_inline int
bpa_l4_dst (vlib_buffer_t *b, ip4_header_t *ip, u8 proto, u16 *port,
	    void **l4h)
{
  return bpa_l4_id (b, ip, proto, 1, port, l4h);
}

#endif /* __included_bpa_l4_h__ */
