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

#include "bpa.h"
#include "bpa_esp.h"
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


/*
 * Finds the subscriber an ICMP error about an outbound GRE packet is about.
 *
 * Returns 0 and fills both outputs, or -1 when no call on that public address
 * has that Call ID with that server - which is the ordinary case for an error
 * about a call that has already gone away, and for unsolicited rubbish.
 */
/* not a unit: a search of VPP's shared bihash; the key, the value and the
 * owner check are bpa_keys.h's, held by test/test_bpa_keys.c, and
 * lab/pptp.sh carries a call through it (#472) */
static_always_inline int
bpa_gre_lookup (u32 pub, u16 call_id, u32 remote, u32 *ip_priv, u32 *fib)
{
  bpa_main_t *bm = &bpa_main;
  clib_bihash_kv_16_8_t kv, value;

  bpa_key_gre_pub (&kv, pub, call_id, remote);
  if (PREDICT_FALSE (clib_bihash_search_16_8 (&bm->shared, &kv, &value)))
    return -1;
  bpa_shared_unpack (value.value, ip_priv, fib);
  return 0;
}

/*
 * Removes the shared binding, and ONLY if it is still this subscriber's.
 *
 * The same rule the ESP teardown follows, for the same reason: this is a
 * table another worker can have written between the two halves of a teardown,
 * and deleting a neighbour's binding would break PMTUD on a call that is
 * working. Here the window is narrower than ESP's - the key carries the
 * server's Call ID, so a collision needs the server to reuse a Call ID for a
 * different subscriber on the same public address while the old call is being
 * torn down - but "narrower" is not "impossible", and the check costs one
 * lookup on call teardown.
 */
/* not a unit: a delete from VPP's shared bihash, for the reason above;
 * held as bpa_gre_lookup() is */
static_always_inline void
bpa_gre_unbind (u32 pub, u16 call_id, u32 remote, u32 ip_priv, u32 fib)
{
  bpa_main_t *bm = &bpa_main;
  clib_bihash_kv_16_8_t kv, value;

  bpa_key_gre_pub (&kv, pub, call_id, remote);
  if (clib_bihash_search_16_8 (&bm->shared, &kv, &value) == 0
      && bpa_shared_is (value.value, ip_priv, fib))
    clib_bihash_add_del_16_8 (&bm->shared, &kv, 0);
}

/* Subscriber -> server. `ctrl` is the control connection's own session. */
int bpa_pptp_out (bpa_per_thread_t *pt, vlib_buffer_t *b, tcp_header_t *tcp,
		  bpa_session_t *ctrl, f64 now);

/* Server -> subscriber. */
int bpa_pptp_in (bpa_per_thread_t *pt, vlib_buffer_t *b, tcp_header_t *tcp,
		 bpa_session_t *ctrl, f64 now);

#endif /* __included_bpa_pptp_h__ */
