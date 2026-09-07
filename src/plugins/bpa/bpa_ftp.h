/*
 * bpa_ftp.h - FTP ALG: the address FTP carries in its payload, in ASCII.
 *
 * FTP announces an endpoint inside the control connection instead of deriving
 * it from the packet headers. Active mode's PORT (RFC 959) and EPRT
 * (RFC 2428) carry the address the peer must connect back to; the 227 and 229
 * replies carry the same thing from a server. Behind a NAT the address in
 * there is a private one, and the data connection is attempted against an
 * address nobody can route to: the control connection looks perfectly healthy
 * while a directory listing hangs forever.
 *
 * Two things this ALG deliberately does NOT do, both recorded because a later
 * reader will otherwise assume they were forgotten:
 *
 * 1. It only ever rewrites in the in2out direction. Only there does the
 *    payload carry an address that belongs to the inside. A 227 arriving from
 *    a server on the internet announces THAT server's address, which is
 *    already reachable - rewriting it would corrupt a session that works.
 *    Which keyword is looked for therefore follows the direction: a segment
 *    towards port 21 is a client's, so PORT/EPRT; a segment from port 21 is a
 *    server's, so 227/229.
 *
 * 2. It rewrites at most the FIRST line of a segment. FTP is strictly
 *    request/response, so a command is a segment, and searching further into
 *    a payload is how an ALG ends up rewriting a byte sequence inside a file
 *    name. A keyword found on a later line leaves the packet untouched and
 *    increments the counter, so the case is visible rather than silent.
 */

#ifndef __included_bpa_ftp_h__
#define __included_bpa_ftp_h__

/* Standalone so that test/test_bpa_ftp.c can reach the sequence arithmetic
 * without VPP. Only the pure half is available under it: the payload rewriter
 * below needs a vlib buffer and belongs to the labs. The shift does not, and
 * it is serial-number arithmetic - the kind that is right for years and then
 * wrong for one connection that lived long enough to wrap. */
#ifdef BPA_STANDALONE
#include "bpa_tcp_shim.h"
#else
#include <vlib/vlib.h>
#include <vnet/ip/ip4_packet.h>
#include <vnet/tcp/tcp_packet.h>

#include "bpa_pool.h"
#include "bpa_session.h"
#endif

#define BPA_FTP_CONTROL_PORT 21

/* bpa_session_t.alg bits. */
#define BPA_ALG_FTP_SEQ 0x01 /* a rewrite happened; seq/ack need shifting */

typedef enum
{
  BPA_FTP_NOTHING = 0, /* no address-bearing line here; nothing to count */
  BPA_FTP_REWRITTEN,   /* payload rewritten and a data session installed */
  BPA_FTP_UNTOUCHED,   /* looked like FTP and was not parsed: left alone */
  BPA_FTP_NO_PORT,     /* no public port for the data connection: left alone */
} bpa_ftp_result_t;

/*
 * Serial-number comparison (RFC 1982): true when `a` is after `b`.
 * Plain `>` wraps wrongly across the 2^32 boundary, and a control connection
 * that lives long enough to wrap would start shifting sequence numbers the
 * wrong way with nothing in any counter to say so.
 */
static_always_inline int
bpa_seq_after (u32 a, u32 b)
{
  return (i32) (b - a) < 0;
}

/*
 * Is this TCP segment part of an FTP control connection?
 *
 * The source port comes from the SESSION, not from the header. By the time
 * the ALG runs, in2out has already replaced the header's source port with the
 * public one, so a subscriber's own FTP server - the case the 227 and 229
 * replies exist for - stops looking like port 21 exactly when we need to
 * recognise it. Read off the header, this test silently never fired for the
 * server-side forms, and the lab caught it by asserting on the captured
 * payload rather than on a counter.
 */
static_always_inline int
bpa_ftp_is_control (const tcp_header_t *tcp, const bpa_session_t *s)
{
  return clib_net_to_host_u16 (tcp->dst_port) == BPA_FTP_CONTROL_PORT
	 || s->port_priv == BPA_FTP_CONTROL_PORT;
}

/*
 * Shift the sequence number of a subscriber -> internet segment.
 *
 * Rewriting an ASCII address changes the LENGTH of the payload, so every byte
 * the subscriber sends after it sits at a different offset in the stream than
 * the subscriber believes. Without this the server acknowledges bytes the
 * client never sent, the client retransmits, and the control connection
 * stalls a few commands after the rewrite - a failure that looks like
 * anything except FTP.
 *
 * The offset to apply is chosen by comparing against the position of the last
 * rewrite, so a retransmission of data sent BEFORE it still gets the offset
 * that was in force when it was first sent.
 *
 * Returns 1 when it changed something.
 */
static_always_inline int
bpa_ftp_seq_in2out (tcp_header_t *tcp, const bpa_session_t *s)
{
  u32 seq, new_seq;
  ip_csum_t sum;
  i32 off;

  if (PREDICT_TRUE (!(s->alg & BPA_ALG_FTP_SEQ)))
    return 0;

  seq = clib_net_to_host_u32 (tcp->seq_number);
  off = bpa_seq_after (seq, s->alg_seq_pos) ? s->alg_off_after
					    : s->alg_off_before;
  if (off == 0)
    return 0;

  new_seq = clib_host_to_net_u32 (seq + (u32) off);
  sum = tcp->checksum;
  sum = ip_csum_update (sum, tcp->seq_number, new_seq, tcp_header_t,
			seq_number);
  tcp->checksum = ip_csum_fold (sum);
  tcp->seq_number = new_seq;
  return 1;
}

/*
 * Shift the acknowledgement number of an internet -> subscriber segment.
 *
 * The remote acknowledges the stream as WE lengthened it. The subscriber only
 * knows its own numbering, so the acknowledgement has to be shifted back or
 * the subscriber sees its own unsent bytes acknowledged.
 *
 * Applied whenever the session carries drift, even if the ALG has since been
 * switched off for the pool: a connection this ALG already touched must keep
 * being corrected, otherwise turning the knob off breaks the very sessions it
 * was meant to stop interfering with.
 *
 * Returns 1 when it changed something.
 */
static_always_inline int
bpa_ftp_ack_out2in (tcp_header_t *tcp, const bpa_session_t *s)
{
  u32 ack, new_ack;
  ip_csum_t sum;
  i32 off;

  if (PREDICT_TRUE (!(s->alg & BPA_ALG_FTP_SEQ)))
    return 0;
  if (!(tcp->flags & TCP_FLAG_ACK))
    return 0;

  ack = clib_net_to_host_u32 (tcp->ack_number);
  /* The comparison is done in the subscriber's numbering, which is why the
   * "before" offset is removed from the acknowledgement first. */
  off = bpa_seq_after (ack - (u32) s->alg_off_before, s->alg_seq_pos)
	  ? s->alg_off_after
	  : s->alg_off_before;
  if (off == 0)
    return 0;

  new_ack = clib_host_to_net_u32 (ack - (u32) off);
  sum = tcp->checksum;
  sum = ip_csum_update (sum, tcp->ack_number, new_ack, tcp_header_t,
			ack_number);
  tcp->checksum = ip_csum_fold (sum);
  tcp->ack_number = new_ack;
  return 1;
}

/*
 * Inspect one already-translated subscriber -> internet TCP segment, rewrite
 * the endpoint it announces, and install the session that will accept the
 * data connection it just promised.
 *
 * `*sp` is the control connection's session, already translated, so its
 * ip_pub is the address the subscriber now appears to have.
 *
 * `sp` is in/out: installing the expected data connection allocates from the
 * per-worker session pool, which may move it, and every pointer into it -
 * including the control session's - is stale afterwards. The caller's pointer
 * is refreshed here so that it cannot be forgotten at the call site. The
 * first version of this code wrote the sequence correction through the stale
 * pointer: the write landed in freed memory, every later segment left
 * unshifted, and no counter said anything.
 */
#ifndef BPA_STANDALONE
bpa_ftp_result_t bpa_ftp_in2out (vlib_main_t *vm, vlib_buffer_t *b,
				 ip4_header_t *ip, bpa_per_thread_t *pt,
				 const bpa_pool_t *p, bpa_session_t **sp,
				 f64 now);
#endif

#endif /* __included_bpa_ftp_h__ */
