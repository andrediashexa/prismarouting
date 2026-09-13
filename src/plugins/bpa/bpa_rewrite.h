/*
 * bpa_rewrite.h - the subscriber -> internet rewrite, and the per-session
 * checksum corrections it runs on.
 *
 * Why this is a header of its own: the rewrite is 201 of the 395 clocks
 * bpa-in2out costs per packet (#63), roughly half of them in the IP header
 * checksum and half in the L4 one, and it is arithmetic whose failure mode is
 * silent - the packet leaves, the counter says translated, and the receiver
 * drops it without a word. Arithmetic like that belongs where a unit test can
 * reach it without a running VPP, not inside a node file that needs one.
 *
 *
 * WHAT MAKES IT CHEAPER
 *
 * The correction is CONSTANT for the life of a session. The subscriber always
 * sends from the same address and port and the session always rewrites them to
 * the same pair, so the two corrections can be worked out once, when the
 * session is created, and added per packet. Per packet this replaces four
 * 64-bit one's complement steps with one addition and one fold.
 *
 *
 * THE TRAP, WHICH HAS ALREADY BEEN SHIPPED AND REVERTED ONCE
 *
 * VPP's two primitives are named for what happens to the DATA, not to the
 * checksum - and the checksum is the one's complement of the sum over that
 * data, so the names read backwards:
 *
 *   ip_csum_add_even (c, x)   a field of value x is ADDED to the data,
 *                             so x is SUBTRACTED from c
 *   ip_csum_sub_even (c, x)   a field of value x LEAVES the data,
 *                             so x is ADDED to c
 *
 * The first attempt at this optimisation applied the delta with
 * ip_csum_add_even, which subtracts it. Every checksum it produced was wrong
 * by exactly twice the delta - measured here at 199988 of 200000 random
 * packets, the remainder being the cases where the delta is zero. It measured
 * 395 -> 284 clocks and failed lab/ftp-alg.sh, lab/rtsp.sh and lab/pptp.sh
 * with ipsum=BAD tcpsum=BAD.
 *
 * bpa_csum_add() exists so that no caller here has to remember any of that.
 */

#ifndef __included_bpa_rewrite_h__
#define __included_bpa_rewrite_h__

/* Standalone so that test/test_bpa_rewrite.c can reach it without VPP. The
 * rewrite is checksum arithmetic whose failure mode is silent - the packet
 * leaves, the counter says translated, the receiver drops it without a word -
 * and a lab that needs a container to say so is a lab that gets run less
 * often than it should be. Same reason bpa_mss.h and bpa_ftp.h carry this. */
#ifdef BPA_STANDALONE
#include "bpa_tcp_shim.h"
#else
#include <vnet/ip/ip4_packet.h>
#include <vnet/ip/icmp46_packet.h>
#include <vnet/udp/udp_packet.h>
#include <vnet/tcp/tcp_packet.h>

#include "bpa_session.h"
#endif

/*
 * Adds a correction to a stored checksum. Spelled out rather than calling
 * ip_csum_sub_even(), which is what does it, because a reader who has not
 * read the block above would take that for a subtraction and be right to.
 */
static_always_inline ip_csum_t
bpa_csum_add (ip_csum_t c, u16 delta)
{
  return ip_csum_with_carry (c, delta);
}

/*
 * The two corrections a session's rewrite needs, worked out once at creation.
 *
 * TWO, not one, and they are not interchangeable. The IPv4 header checksum
 * covers the addresses and nothing below them; the TCP and UDP checksums
 * cover a pseudo-header carrying those same addresses AND the ports. Using
 * the combined one on the IP header was measured wrong on 98.9% of TCP
 * packets.
 *
 * ICMP is the third shape: its checksum covers no pseudo-header at all
 * (RFC 792), so the address change is none of its business and only the echo
 * identifier belongs in its correction. Handing it the address delta as well
 * is the same mistake in the other direction.
 *
 * GRE and ESP get no L4 correction because neither has a checksum this box
 * may touch - enhanced GRE requires the C bit to be zero (RFC 2637), and an
 * ESP SPI is inside the integrity check (RFC 4303 s3.3.2).
 *
 * Every operand is converted to NETWORK order first. The session stores
 * addresses and ports in host order, and a checksum is a sum over the bytes
 * as they travel; a correction built from host-order operands disagreed with
 * a full recomputation on 199979 of 200000 packets.
 *
 * The result of each is FOLDED to 16 bits, which matters for more than size:
 * when the public address equals the private one - a one-to-one pool - the
 * folded correction is exactly zero, so adding it per packet is a true no-op
 * and the rewrite needs no "did the address actually change" branch.
 */
static_always_inline void
bpa_csum_deltas (u32 ip_priv, u32 ip_pub, u16 port_priv, u16 port_pub,
		 u8 proto, u16 *d_addr, u16 *d_l4)
{
  ip_csum_t a = ip_csum_update (0, clib_host_to_net_u32 (ip_priv),
				clib_host_to_net_u32 (ip_pub), ip4_header_t,
				src_address);
  ip_csum_t l;

  *d_addr = ip_csum_fold (a);

  if (proto == BPA_PROTO_ICMP)
    l = ip_csum_update (0, clib_host_to_net_u16 (port_priv),
			clib_host_to_net_u16 (port_pub), icmp_echo_header_t,
			identifier);
  else if (proto == BPA_PROTO_TCP || proto == BPA_PROTO_UDP)
    l = ip_csum_update (a, clib_host_to_net_u16 (port_priv),
			clib_host_to_net_u16 (port_pub), tcp_header_t,
			src_port);
  else
    l = 0;

  *d_l4 = ip_csum_fold (l);
}

/*
 * The same, for a session that has just been filled in. One call site per
 * path that creates a session, so that adding a fourth path and forgetting
 * the corrections is a grep away rather than a wrong checksum in the field.
 */
static_always_inline void
bpa_session_csum_deltas (bpa_session_t *s)
{
  bpa_csum_deltas (s->ip_priv, s->ip_pub, s->port_priv, s->port_pub, s->proto,
		   &s->csum_d_addr, &s->csum_d_l4);
}

/*
 * Rewrite the source address and port, fixing both checksums.
 *
 * `d_addr` and `d_l4` are the session's, from bpa_csum_deltas(). They are
 * only correct because a session hit means the packet's source address and
 * port ARE the ones the session was keyed on: bpa_key_in() packs exactly
 * (ip_priv, port_priv, proto, fib), and the node looks up with the values it
 * read off this packet. ESP and GRE are keyed on the address and the peer
 * rather than a port, and neither takes an L4 correction, so the port half of
 * that invariant is not needed for them.
 *
 * That is the one thing this form gives up against reading the old values out
 * of the packet: the old way was self-correcting if the session ever
 * disagreed with the packet, and this way is not. Hence the assert, which
 * costs nothing in a release build and names the invariant in a debug one.
 */
static_always_inline void
bpa_rewrite_src (ip4_header_t *ip, void *l4h, u8 proto, u32 new_addr,
		 u16 new_port, u16 d_addr, u16 d_l4)
{
#if CLIB_DEBUG > 0
  /*
   * The invariant, checked against the packet in front of us rather than
   * restated in a comment: the corrections the session carries are the ones
   * THIS address and port change need. Compiled out of a release build, which
   * is why the unit suite asserts the same thing where it can fail by name
   * instead of by abort.
   */
  {
    u16 chk_addr, chk_l4;
    u16 old_port =
      (l4h == 0 || proto == BPA_PROTO_GRE)
	? new_port
	: clib_net_to_host_u16 (proto == BPA_PROTO_ICMP
				  ? ((u16 *) ((icmp46_header_t *) l4h + 1))[0]
				  : ((udp_header_t *) l4h)->src_port);
    bpa_csum_deltas (clib_net_to_host_u32 (ip->src_address.as_u32), new_addr,
		     old_port, new_port, proto, &chk_addr, &chk_l4);
    ASSERT (chk_addr == d_addr);
    ASSERT (l4h == 0 || proto == BPA_PROTO_GRE || chk_l4 == d_l4);
  }
#endif

  ip->checksum = ip_csum_fold (bpa_csum_add (ip->checksum, d_addr));
  ip->src_address.as_u32 = clib_host_to_net_u32 (new_addr);

  /* Nothing below the IP header to touch: a non-first fragment, whose ports
   * and L4 checksum live in the first one, or ESP, which has neither. Only
   * the address changes. */
  if (PREDICT_FALSE (l4h == 0))
    return;

  /*
   * GRE going out carries the Call ID the SERVER assigned, and the server
   * expects to see it come back unchanged. Only the address is ours to
   * rewrite here - the Call ID this box allocated appears in the other
   * direction, which is where bpa_rewrite_dst() puts it.
   */
  if (proto == BPA_PROTO_GRE)
    return;

  if (proto == BPA_PROTO_ICMP)
    {
      icmp46_header_t *icmp = l4h;
      u16 *id = (u16 *) (icmp + 1);
      icmp->checksum = ip_csum_fold (bpa_csum_add (icmp->checksum, d_l4));
      id[0] = clib_host_to_net_u16 (new_port);
    }
  else if (proto == BPA_PROTO_TCP)
    {
      tcp_header_t *tcp = l4h;
      tcp->checksum = ip_csum_fold (bpa_csum_add (tcp->checksum, d_l4));
      tcp->src_port = clib_host_to_net_u16 (new_port);
    }
  else
    {
      udp_header_t *udp = l4h;
      /*
       * A zero UDP checksum means "not computed" and must stay zero.
       *
       * The other half of RFC 768 needs no code here and it is worth saying
       * why. A computed zero would have to be sent as 0xffff, and this form
       * cannot produce one: the result is ip_csum_fold (stored + delta) with
       * both operands sixteen bits, which is zero only when both are, and
       * `stored` is non-zero inside this branch.
       */
      if (udp->checksum)
	udp->checksum = ip_csum_fold (bpa_csum_add (udp->checksum, d_l4));
      udp->src_port = clib_host_to_net_u16 (new_port);
    }
}

#endif /* __included_bpa_rewrite_h__ */
