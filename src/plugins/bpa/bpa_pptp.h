/*
 * bpa_pptp.h - the PPTP application-layer gateway.
 *
 * PPTP splits itself in two: a TCP control connection on port 1723, and the
 * payload in GRE packets that carry no ports at all. What ties them together
 * is a 16-bit Call ID that each end chooses for itself and announces over the
 * control connection.
 *
 * That is exactly the thing a CGNAT cannot allow. Two subscribers behind one
 * public address both choose Call ID 1 - Windows and most routers start
 * low - and the inbound GRE for the two tunnels becomes indistinguishable.
 * Whoever set up second wins, and the other subscriber's tunnel connects and
 * then carries nothing.
 *
 * So the Call ID is rewritten the way a source port is rewritten, and it is
 * allocated from the same port block. That is not just tidiness: it means the
 * inbound arithmetic pre-check, the worker handoff and the RFC 8158 log all
 * work on a PPTP call without knowing PPTP exists.
 */

#ifndef __included_bpa_pptp_h__
#define __included_bpa_pptp_h__

#include <vnet/ip/ip4_packet.h>
#include <vnet/tcp/tcp_packet.h>

#include "bpa_session.h"

#define BPA_PPTP_PORT 1723

/*
 * What the ALG did with a segment. A segment can carry several control
 * messages, so the caller is told the most serious outcome among them:
 * a failure is never hidden behind a success in the same segment.
 */
typedef enum
{
  BPA_PPTP_NOTHING = 0, /* nothing in here needed rewriting */
  BPA_PPTP_MANGLED,	/* a Call ID was rewritten */
  BPA_PPTP_UNKNOWN,	/* a message about a call this box does not have */
  BPA_PPTP_NO_CALL_ID,	/* the block had no free entry to allocate */
  BPA_PPTP_MALFORMED,	/* truncated, or not PPTP at all */
} bpa_pptp_result_t;

/* Subscriber -> server. `ctrl` is the control connection's own session. */
int bpa_pptp_out (bpa_per_thread_t *pt, vlib_buffer_t *b, tcp_header_t *tcp,
		  bpa_session_t *ctrl, f64 now);

/* Server -> subscriber. */
int bpa_pptp_in (bpa_per_thread_t *pt, vlib_buffer_t *b, tcp_header_t *tcp,
		 bpa_session_t *ctrl, f64 now);

#endif /* __included_bpa_pptp_h__ */
