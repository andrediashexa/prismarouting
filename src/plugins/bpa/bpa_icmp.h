/*
 * bpa_icmp.h - what an ICMP error is ABOUT, and the bounds that make reading
 * it safe.
 *
 * An ICMP error carries no identifier of its own. The outer header is
 * (some router) -> (our public address) with no L4 at all, and the session it
 * refers to is named by the packet embedded in it:
 *
 *   outer IP / ICMP (4) + 4 type-specific / embedded IP / >= 8 bytes of L4
 *
 * The embedded header runs the other way round from the outer one, so its
 * SOURCE address and SOURCE port are the public address and public port this
 * box handed out - the same (address, port) the plain return path takes from
 * the outer DESTINATION, and therefore the same session key.
 *
 * Two nodes need that pair and they must not disagree about it:
 *
 *   bpa-out2in-handoff  picks the worker that owns the subscriber from it
 *   bpa-out2in          looks the session up in THAT worker's table
 *
 * If they parsed separately they could one day differ by a bounds check or a
 * protocol, and a disagreement here is not a wrong answer - it is a packet
 * delivered to a worker that has never heard of the session it is about, and
 * dropped there for a reason that describes the packet rather than the box
 * (#201). One parse, both callers.
 *
 * The end of the readable bytes is a PARAMETER rather than something derived
 * from the IP length field, because the length field belongs to whoever sent
 * the packet. A malformed ICMP error is ordinary hostile traffic - it costs an
 * attacker nothing to claim a 60-byte embedded header in a 40-byte datagram -
 * so every step below is measured against what is really in the buffer, by
 * subtracting pointers that are known to lie inside it rather than by adding
 * an attacker's number to one and comparing.
 */

#ifndef __included_bpa_icmp_h__
#define __included_bpa_icmp_h__

/* Standalone so test/test_bpa_icmp.c can hammer the bounds with truncated
 * packets without a container. The refusals are the interesting half of this
 * file and they cannot be observed from a lab: a packet that is dropped looks
 * the same whether it was rejected or read past. Same reason bpa_ports.h,
 * bpa_mss.h and bpa_rtsp.h carry this. */
#ifdef BPA_STANDALONE
#include "bpa_tcp_shim.h"
#else
#include <vnet/ip/ip4_packet.h>
#include <vnet/ip/icmp46_packet.h>
#endif

/*
 * ICMP errors carry the original packet embedded, and it is the embedded one
 * that says who they are about.
 *
 * Without translating it, PMTUD's "fragmentation needed" never reaches the
 * subscriber: the TCP connection hangs and the symptom that reaches support
 * is "some sites do not open". Traceroute also stops working through the
 * CGNAT.
 */
static_always_inline int
bpa_icmp_is_error (u8 type)
{
  switch (type)
    {
    case ICMP4_destination_unreachable:
    case ICMP4_time_exceeded:
    case ICMP4_parameter_problem:
    case ICMP4_source_quench:
    case ICMP4_redirect:
      return 1;
    default:
      return 0;
    }
}

typedef struct
{
  ip4_header_t *inner; /* the embedded IP header, for the rewrite */
  u16 *port;	       /* where the public port sits inside it */
  u32 pub_addr;	       /* host order: the embedded SOURCE address */
  u16 pub_port;	       /* host order: the embedded SOURCE port */
  u8 ip_proto;	       /* the embedded packet's IP protocol number */
} bpa_icmp_err_t;

/*
 * Returns 0 and fills `e` when this is an ICMP error whose embedded packet
 * names a translatable flow. -1 means the caller must not touch it, which
 * covers three different things on purpose - they all end the same way:
 *
 *   - not an error at all (an echo, which the ordinary path handles)
 *   - truncated: the bytes the embedded headers claim are not present
 *   - embedded GRE or ESP, whose identifier is not where this reads
 *
 * `end` is one past the last readable byte of THIS buffer. On a chained
 * buffer that is the first segment's end, so an error whose embedded header
 * spilled into the next segment is refused rather than followed - the
 * conservative direction, and a 56-byte ICMP error does not span segments.
 */
static_always_inline int
bpa_icmp_err_parse (ip4_header_t *ip, const u8 *end, bpa_icmp_err_t *e)
{
  const u8 *p = (const u8 *) ip;
  icmp46_header_t *icmp;
  ip4_header_t *inner;
  u32 avail, hlen, inner_hlen, port_off;

  if (PREDICT_FALSE (end <= p))
    return -1;
  avail = (u32) (end - p);

  hlen = ip4_header_bytes (ip);
  /* 4 bytes of ICMP header plus the 4 that are type-specific - the next-hop
   * MTU of a fragmentation-needed lives in that second word (RFC 1191). */
  if (PREDICT_FALSE (hlen < sizeof (ip4_header_t) || avail < hlen + 8))
    return -1;

  icmp = (icmp46_header_t *) (p + hlen);
  if (PREDICT_FALSE (!bpa_icmp_is_error (icmp->type)))
    return -1;

  avail -= hlen + 8;
  inner = (ip4_header_t *) (p + hlen + 8);
  if (PREDICT_FALSE (avail < sizeof (ip4_header_t)))
    return -1;

  /* Only now is the embedded IHL readable, and it is still the sender's
   * number: a claimed 60-byte header in a datagram that stops after 20 has to
   * come out as a refusal, not as a read into whatever follows the buffer. */
  inner_hlen = ip4_header_bytes (inner);
  if (PREDICT_FALSE (inner_hlen < sizeof (ip4_header_t) || avail < inner_hlen))
    return -1;
  avail -= inner_hlen;

  switch (inner->protocol)
    {
    case IP_PROTOCOL_TCP:
    case IP_PROTOCOL_UDP:
      port_off = 0;
      break;
    case IP_PROTOCOL_ICMP:
      /* The echo identifier, which is what stands in for a port. It sits four
       * bytes into the embedded ICMP header, still inside the eight bytes
       * RFC 792 requires an error to quote. */
      port_off = 4;
      break;
    default:
      /*
       * GRE and ESP reach the ordinary return path but not this one. Neither
       * keeps an identifier in the first bytes of its header - the PPTP Call
       * ID is at offset 6, and ESP has none this box may key on at all - so
       * reading a port here would build a session key out of a flags word.
       * Refused rather than looked up on a value that means something else.
       */
      return -1;
    }

  if (PREDICT_FALSE (avail < port_off + 2))
    return -1;

  e->inner = inner;
  e->port = (u16 *) ((u8 *) inner + inner_hlen + port_off);
  e->pub_addr = clib_net_to_host_u32 (inner->src_address.as_u32);
  e->pub_port = clib_net_to_host_u16 (e->port[0]);
  e->ip_proto = inner->protocol;
  return 0;
}

#endif /* __included_bpa_icmp_h__ */
