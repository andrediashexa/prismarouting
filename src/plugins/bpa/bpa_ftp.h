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

/*
 * FTP64 (RFC 6384): the FTP ALG of a NAT64 pool (#485 phase 5).
 *
 * Not the IPv4 ALG above with a wider address. On a NAT64 pool the client is
 * IPv6 and the server IPv4, so the two speak different halves of FTP: the
 * client sends EPSV and EPRT (RFC 2428), which many IPv4 servers do not
 * implement, and expects 229 back; the server answers PASV with 227 and
 * takes PORT. The ALG translates between the halves, on the client's
 * control connection only - a subscriber's own IPv6 FTP server reached from
 * IPv4 is not what RFC 6384 covers, and is left alone.
 *
 * What it does, by RFC 6384 section:
 *  6    EPSV (and EPSV 2) goes to the server as PASV; its 227 comes back as
 *       229 with the port and no address (the 227's address is ignored, as
 *       the RFC says it SHOULD be). EPSV ALL is answered 504 and EPSV with
 *       another protocol 522, neither reaching the server.
 *  7.2  EPRT |2|<the control connection's own IPv6 address>|port| opens a
 *       mapping from the subscriber's block to that port and goes to the
 *       server as PORT with the public address. An EPRT naming any other
 *       address, or an IPv4 one, is passed unchanged (SHOULD NOT translate).
 *  5    AUTH, once the server accepts it (2xx or 3xx), turns translation off
 *       for the rest of the connection - the control channel is TLS from
 *       there - while the sequence correction already made goes on.
 *  11   ALGS STATUS64 / ENABLE64 / DISABLE64 answered with 216 (EPSVEPRT or
 *       NONE), anything else after ALGS with 504.
 *  12   A command not propagated - EPSV ALL, EPSV <other>, ALGS - goes to the
 *       server as NOOP, and the server's 200 to it is rewritten into the
 *       answer the client is owed: the box never builds a packet of its own.
 *
 * What it does not do, and the RFC lets it not: section 8's default port 20
 * (MAY), and a non-200 answer to the NOOP is passed to the client rather
 * than tearing the control connection down (SHOULD). Telnet option
 * negotiation is passed through rather than refused (MUST in section 5):
 * a client that negotiates Telnet options over an FTP control channel was
 * not found to exist, and refusing them means building packets.
 *
 * The state of one control connection is one byte on its session
 * (bpa_session_t.ftp64): the answer the next reply has to be turned into,
 * and two flags.
 */
#define BPA_FTP64_PENDING     0x07 /* what the next reply becomes */
#define BPA_FTP64_P_229	      1	   /* EPSV went as PASV: 227 -> 229 */
#define BPA_FTP64_P_522	      2	   /* a NOOP stood in for EPSV <other> */
#define BPA_FTP64_P_504	      3	   /* ... for EPSV ALL, or a bad ALGS */
#define BPA_FTP64_P_216	      4	   /* ... for ALGS: the status */
#define BPA_FTP64_P_AUTH      5	   /* AUTH sent: does the server take it */
#define BPA_FTP64_TRANSPARENT 0x08 /* AUTH taken: translate nothing more */
#define BPA_FTP64_DISABLED    0x10 /* ALGS DISABLE64 */
#define BPA_FTP64_LAST_SHIFT  5	   /* bits 5-7: the last reply rewritten */

typedef enum
{
  BPA_FTP64_PASS = 0,  /* nothing to translate: leaves as it came */
  BPA_FTP64_REPLACE,   /* the first line becomes `rep` */
  BPA_FTP64_EPRT,      /* translate once a mapping for `*port` exists */
  BPA_FTP64_UNTOUCHED, /* one of ours that does not parse: left, counted */
} bpa_ftp64_action_t;

/* Longest line either side writes: the 229, with a five-digit port. */
#define BPA_FTP64_MAX_LINE 64

bpa_ftp64_action_t bpa_ftp64_client (const u8 *pl, u32 len, u8 *state,
				     const u8 host6[16], u8 *rep,
				     u32 *rep_len, u32 *line_len, u16 *port);
bpa_ftp64_action_t bpa_ftp64_server (const u8 *pl, u32 len, u8 *state,
				     int is_retx, u8 *rep, u32 *rep_len,
				     u32 *line_len);
u32 bpa_ftp64_put_port (u8 *dst, u32 addr, u16 port);
#ifndef BPA_STANDALONE
/* bpa_nat64.c: the server's reply in bpa-out2in, and the renumbering. */
bpa_ftp_result_t bpa_ftp64_out2in (vlib_main_t *vm, vlib_buffer_t *b,
				   bpa_session_t *s, int alg_on,
				   u32 *n_seq_adj);
#endif

/*
 * RFC 6384 section 5: an ALG that changes the length of a control channel
 * MUST keep the urgent pointer's meaning across it or clear URG. Cleared,
 * on every segment of a control connection the ALG has lengthened: an urgent
 * pointer counts bytes from its own segment's sequence number, and one sent
 * before a rewrite and pointing past it would land the rewrite's length off.
 * What URG carries in FTP is the Telnet Synch of an ABOR, whose IAC bytes
 * still arrive in the stream. Returns 1 when it changed the header.
 */
#ifndef TCP_FLAG_URG
#define TCP_FLAG_URG 0x20
#endif
static_always_inline int
bpa_ftp64_clear_urg (tcp_header_t *t)
{
  u16 w0, w1;
  ip_csum_t sum;

  if (!(t->flags & TCP_FLAG_URG))
    return 0;
  /* The flags byte shares a 16-bit word with the data offset: the checksum
   * moves by the word. */
  memcpy (&w0, &t->data_offset_and_reserved, 2);
  t->flags &= (u8) ~TCP_FLAG_URG;
  memcpy (&w1, &t->data_offset_and_reserved, 2);
  sum = t->checksum;
  sum = ip_csum_sub_even (sum, w0);
  sum = ip_csum_add_even (sum, w1);
  sum = ip_csum_sub_even (sum, t->urgent_pointer);
  t->urgent_pointer = 0;
  t->checksum = ip_csum_fold (sum);
  return 1;
}

#endif /* __included_bpa_ftp_h__ */
