/*
 * bpa_l4_wire.h - reading a packet's L4 identifier and rewriting its
 * destination, on the bytes alone.
 *
 * WHY A HEADER OF ITS OWN (#472). These were bpa_l4.h, which includes
 * vnet/buffer.h for one thing - the fragment flag and the two ports VPP's
 * shallow reassembly keeps in a buffer's metadata - so the port every
 * translated packet is keyed on, and the checksum every returning packet
 * leaves with, had no unit test: a lab saw them only as a packet that did
 * or did not arrive. Here they take the metadata as arguments, and compile
 * against VPP's headers or against test/bpa_tcp_shim.h, as bpa_rewrite.h
 * does; test/test_bpa_l4.c holds them. bpa_l4.h keeps the wrappers that read
 * the buffer.
 *
 * Nothing is included: the includer brings VPP's ip4/icmp/tcp/udp headers
 * and the checksum helpers, or the shim's.
 */

#ifndef __included_bpa_l4_wire_h__
#define __included_bpa_l4_wire_h__

#include "bpa_gre.h"

/*
 * A non-first fragment: no L4 header here, so the id is the one VPP's shallow
 * virtual reassembly kept from the first fragment, in network order as it
 * left it. `*l4h` comes out zero - only the address will be rewritten.
 *
 * Reassembly recovers TCP and UDP ports. It does not parse GRE, so there is
 * no Call ID to recover and the fields it left behind belong to some other
 * protocol. Reading them would look up a key built from whatever happened to
 * be there, so GRE is refused.
 *
 * Reassembly stores the port in network order. Reading it as host order
 * returns the value byte-swapped - 12345 (0x3039) becomes 14640 (0x3930) -
 * and the node opens a phantom session, burning one port of the block per
 * fragment.
 */
static_always_inline int
bpa_l4_reass (u8 proto, int want_dst, u16 reass_src, u16 reass_dst,
	      u16 *port, void **l4h)
{
  if (PREDICT_FALSE (proto == BPA_PROTO_GRE))
    return -1;
  *l4h = 0;
  *port = clib_net_to_host_u16 (want_dst ? reass_dst : reass_src);
  return 0;
}

/*
 * A whole packet, or a first fragment: the id from its own L4 header, which
 * must end before `end` (where the buffer's bytes stop). Returns 0 and the
 * id, with `*l4h` at the L4 header; -1 when the packet is not translatable.
 */
static_always_inline int
bpa_l4_read (ip4_header_t *ip, const u8 *end, u8 proto, int want_dst,
	     u16 *port, void **l4h)
{
  u32 hlen = ip4_header_bytes (ip);
  void *p = (u8 *) ip + hlen;

  /*
   * ESP has no identifier this box may use and no header it may rewrite: the
   * SPI is covered by the integrity check (RFC 4303 s3.3.2), so touching it
   * would make the subscriber's own stack discard the packet. What binds an
   * ESP flow to a subscriber is the peer address, and that is in the IP
   * header, so there is nothing to read here.
   *
   * bpa_l4_id() asks this before it asks about fragments, on purpose. A
   * fragmented ESP packet needs exactly the same treatment as a whole one -
   * its address translated and nothing else - and refusing it for having no
   * port to recover would drop every tunnel carrying a full-sized datagram.
   */
  if (PREDICT_FALSE (proto == BPA_PROTO_ESP))
    {
      *l4h = 0;
      *port = 0;
      return 0;
    }

  if (PREDICT_FALSE ((const u8 *) p + 8 > end))
    return -1;

  *l4h = p;

  if (proto == BPA_PROTO_GRE)
    {
      if (PREDICT_FALSE (!bpa_gre_is_enhanced (p)))
	return -1;
      *port = bpa_gre_call_id (p);
      return 0;
    }

  if (proto == BPA_PROTO_ICMP)
    {
      icmp46_header_t *icmp = p;
      /* Only echo carries an identifier usable as a port. ICMP errors need
       * the embedded packet rewritten, which is a separate job. Both echo
       * request and reply keep the identifier in the same place, so the
       * direction does not change where we read it from. */
      if (icmp->type != ICMP4_echo_request && icmp->type != ICMP4_echo_reply)
	return -1;
      *port = clib_net_to_host_u16 (((u16 *) (icmp + 1))[0]);
      return 0;
    }

  /* TCP and UDP both start with source port then destination port. */
  *port = clib_net_to_host_u16 (((u16 *) p)[want_dst ? 1 : 0]);
  return 0;
}

/*
 * Rewrite the destination address and port, fixing both checksums.
 *
 * Shared because two nodes need it: out2in on the way in, and in2out for
 * hairpinned traffic, which is translated twice in one pass - source on the
 * way out, destination straight back in.
 */
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

  /* Nothing below the IP header to touch: a non-first fragment, whose ports
   * and L4 checksum live in the first one, or ESP, which has neither. Only
   * the address changes. */
  if (PREDICT_FALSE (l4h == 0))
    {
      ip->dst_address.as_u32 = clib_host_to_net_u32 (new_addr);
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
      sum = ip_csum_update (sum, ip->dst_address.as_u32,
			    clib_host_to_net_u32 (new_addr), ip4_header_t,
			    dst_address);
      sum = ip_csum_update (sum, tcp->dst_port,
			    clib_host_to_net_u16 (new_port), tcp_header_t,
			    dst_port);
      tcp->checksum = ip_csum_fold (sum);
      tcp->dst_port = clib_host_to_net_u16 (new_port);
    }
  else if (proto == BPA_PROTO_GRE)
    {
      /* Enhanced GRE carries no checksum of its own (RFC 2637 requires the C
       * bit to be zero), and the IP checksum was already fixed above, so the
       * Call ID is a plain two-byte store. */
      ((u16 *) l4h)[BPA_GRE_CALL_ID_OFF / 2] =
	clib_host_to_net_u16 (new_port);
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

/*
 * The TCP payload of a packet that is entirely inside this buffer.
 *
 * Returns -1 for a packet with no payload, a header that does not fit, or a
 * length the buffer does not actually hold. Every one of those means "do not
 * touch it", which is the safe answer for all three.
 */
static_always_inline int
bpa_tcp_payload (vlib_buffer_t *b, ip4_header_t *ip, tcp_header_t *tcp,
		 u8 **pl, u32 *pl_len)
{
  u8 *end = (u8 *) vlib_buffer_get_current (b) + b->current_length;
  u32 ip_hlen = ip4_header_bytes (ip);
  u32 tcp_hlen = tcp_header_bytes (tcp);
  u32 ip_len = clib_net_to_host_u16 (ip->length);
  u8 *p;

  if (PREDICT_FALSE (tcp_hlen < sizeof (tcp_header_t)))
    return -1;
  if (PREDICT_FALSE (ip_len <= ip_hlen + tcp_hlen))
    return -1;

  p = (u8 *) tcp + tcp_hlen;
  *pl = p;
  *pl_len = ip_len - ip_hlen - tcp_hlen;

  if (PREDICT_FALSE (p + *pl_len > end))
    return -1;
  return 0;
}

/*
 * The UDP payload of a datagram that is entirely inside this buffer.
 *
 * Returns -1 for anything this box must not reach into: a datagram with no
 * payload, a UDP length that disagrees with the IP length, or a length the
 * buffer does not actually hold. A UDP header carries its OWN length as well
 * as the IP header's, and the two disagreeing is either a truncated capture or
 * a crafted packet - the SIP parser is reachable from the internet on a port
 * that is scanned continuously, so the bound comes from the smaller of the two
 * and both are checked before a single payload byte is read.
 */
static_always_inline int
bpa_alg_udp_payload (vlib_buffer_t *b, ip4_header_t *ip, udp_header_t *udp,
		     u8 **pl, u32 *pl_len)
{
  u8 *end = (u8 *) vlib_buffer_get_current (b) + b->current_length;
  u32 ip_hlen = ip4_header_bytes (ip);
  u32 ip_len = clib_net_to_host_u16 (ip->length);
  u32 udp_len = clib_net_to_host_u16 (udp->length);
  u8 *p;

  if (PREDICT_FALSE (udp_len <= sizeof (udp_header_t)))
    return -1;
  if (PREDICT_FALSE (ip_len != ip_hlen + udp_len))
    return -1;

  p = (u8 *) udp + sizeof (udp_header_t);
  *pl = p;
  *pl_len = udp_len - (u32) sizeof (udp_header_t);

  if (PREDICT_FALSE (p + *pl_len > end))
    return -1;
  return 0;
}

/*
 * A UDP checksum as it goes on the wire: an all-zero result is sent as 0xffff
 * (RFC 768), because zero on the wire means "no checksum here" and would tell
 * the receiver not to check the very thing that was just made correct.
 */
static_always_inline u16
bpa_udp_csum_on_wire (u16 csum)
{
  return csum ? csum : 0xffff;
}

#endif /* __included_bpa_l4_wire_h__ */
