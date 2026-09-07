/*
 * bpa_mss.h - TCP MSS clamping.
 *
 * A subscriber behind a PPPoE access network has 1492 bytes of usable MTU, or
 * less once a tunnel is in the way. Nothing tells the far end that: the remote
 * announces an MSS derived from ITS own link, sends full-sized segments, and
 * they are dropped where the path narrows. Path MTU discovery is supposed to
 * fix it, and it is routinely broken by somebody filtering ICMP along the way.
 * What the subscriber sees is that some sites do not load - not that they are
 * slow, that they hang - which looks nothing like an MTU problem to anyone
 * taking the call.
 *
 * Clamping the MSS option of the handshake removes the category. Both
 * directions, because the option announces what the SENDER is willing to
 * RECEIVE: clamping the subscriber's SYN limits what the internet sends back,
 * and clamping the server's SYN/ACK limits what the subscriber sends out.
 *
 * What this deliberately does NOT do: insert an MSS option into a SYN that has
 * none. The option is in the TCP header, so adding one lengthens the header
 * and moves nothing - but a SYN without an MSS option means the sender accepts
 * the 536-byte default, which is already smaller than any clamp worth setting.
 * There is nothing to fix.
 */

#ifndef __included_bpa_mss_h__
#define __included_bpa_mss_h__

/* Standalone so that test/test_bpa_mss.c can reach it without VPP. The
 * clamp is arithmetic on an option list whose failure mode is silent - the
 * peer drops the segment and the connection hangs exactly as it did before -
 * so its refusals have to be assertable without a container. Same reason
 * bpa_ports.h and bpa_rtsp.h carry this. */
#ifdef BPA_STANDALONE
#include "bpa_tcp_shim.h"
#else
#include <vnet/tcp/tcp_packet.h>
#endif

/* An IPv4 header and a TCP header, both without options: what an MSS is
 * measured against, so an MTU minus this is the largest MSS that fits. */
#define BPA_MSS_OVERHEAD 40

/*
 * The floor and the ceiling an operator may configure.
 *
 * 536 is the MSS every IPv4 host must accept (RFC 1122 s4.2.2.6, from the
 * 576-byte minimum datagram). Below it the clamp would be legal and useless -
 * and a typo such as "tcp-mss 14" would quietly break every connection through
 * the box, which is worth refusing at the prompt rather than discovering in
 * production. 9000 is a jumbo frame; above that the number is not describing
 * any path this box is on.
 */
#define BPA_MSS_MIN 536
#define BPA_MSS_MAX 9000

/*
 * Lowers the MSS option of a TCP header to `mss`, if it is higher.
 *
 * Only ever lowers. Raising it would announce a segment size the sender never
 * agreed to receive, which is not clamping - it is corruption with a plausible
 * name.
 *
 * The checksum is updated incrementally against the two bytes that changed.
 * It has to be: recomputing it over the segment would be a second pass over
 * the packet, and getting it wrong at all fails silently - the peer drops the
 * segment and the connection hangs exactly as it did before clamping, with a
 * counter here claiming the fix was applied.
 *
 * Returns 1 when it changed the option, 0 otherwise. `end` is one past the
 * last valid byte of the packet, so a truncated or lying data offset walks
 * off nothing.
 */
static_always_inline int
bpa_tcp_mss_clamp (tcp_header_t *tcp, const u8 *end, u16 mss)
{
  u8 *opt = (u8 *) (tcp + 1);
  u8 *opt_end = (u8 *) tcp + tcp_header_bytes (tcp);

  if (PREDICT_FALSE (opt_end > end || opt_end < opt))
    return 0;

  while (opt < opt_end)
    {
      u8 kind = opt[0], len;

      if (kind == TCP_OPTION_EOL)
	break;
      if (kind == TCP_OPTION_NOOP)
	{
	  opt++;
	  continue;
	}

      /* Every other option carries its own length in the second byte, and a
       * length that would leave the options is a malformed header: stop
       * rather than guess where the next one starts. */
      if (opt + 1 >= opt_end)
	break;
      len = opt[1];
      if (len < 2 || opt + len > opt_end)
	break;

      if (kind == TCP_OPTION_MSS && len == 4)
	{
	  u16 old_net = clib_mem_unaligned (opt + 2, u16);
	  u16 new_net;
	  ip_csum_t sum;

	  if (clib_net_to_host_u16 (old_net) <= mss)
	    return 0;

	  new_net = clib_host_to_net_u16 (mss);
	  clib_mem_unaligned (opt + 2, u16) = new_net;

	  /* Two bytes in, two bytes out. udp_header_t.src_port names a u16
	   * field for the macro's sizeof and nothing more, which is how the
	   * ICMP path in bpa_out2in.c already spells this. */
	  sum = tcp->checksum;
	  sum = ip_csum_update (sum, old_net, new_net, udp_header_t, src_port);
	  tcp->checksum = ip_csum_fold (sum);
	  return 1;
	}

      opt += len;
    }

  return 0;
}

#endif /* __included_bpa_mss_h__ */
