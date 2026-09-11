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

typedef enum
{
  BPA_FTP_NOTHING = 0, /* no address-bearing line here; nothing to count */
  BPA_FTP_REWRITTEN,   /* payload rewritten and a data session installed */
  BPA_FTP_UNTOUCHED,   /* looked like FTP and was not parsed: left alone */
  BPA_FTP_NO_PORT,     /* no public port for the data connection: left alone */
} bpa_ftp_result_t;

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
