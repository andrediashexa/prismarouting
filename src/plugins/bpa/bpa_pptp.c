/*
 * bpa_pptp.c - PPTP control channel rewriting, RFC 2637.
 *
 * Every control message begins with the same twelve bytes:
 *
 *   0  Length (2)          total octets of this message
 *   2  PPTP Message Type   1 for a control message
 *   4  Magic Cookie        0x1A2B3C4D
 *   8  Control Message Type
 *  10  Reserved0
 *  12  body
 *
 * and in every message this ALG touches, the Call ID that matters sits at
 * offset 12 or 14. The rewrite never changes the length, so no TCP sequence
 * number has to be adjusted - which removes the single hardest part of
 * writing an ALG, and the part most implementations get wrong.
 *
 * Which field belongs to which end is the whole difficulty, and it is not
 * symmetric. The rules below follow RFC 2637 sections 2.7 to 2.15, with one
 * deliberate departure: Call-Disconnect-Notify. The RFC says its Call ID is
 * the one the PAC assigned, but deployed servers put the PNS's there, and
 * Linux's nf_nat_pptp has treated it as a peer Call ID for twenty years. A
 * CGNAT that follows the text here breaks against the servers that exist.
 */

#include "bpa.h"
#include "bpa_pptp.h"

#define PPTP_MSG_CONTROL 1
#define PPTP_MAGIC	 0x1a2b3c4d

/* Control message types, RFC 2637 section 2.1. */
#define PPTP_OUT_CALL_REQUEST	    7
#define PPTP_OUT_CALL_REPLY	    8
#define PPTP_CALL_CLEAR_REQUEST	    12
#define PPTP_CALL_DISCONNECT_NOTIFY 13
#define PPTP_WAN_ERROR_NOTIFY	    14
#define PPTP_SET_LINK_INFO	    15

#define PPTP_HDR_BYTES	 12
#define PPTP_RESULT_CONNECTED 1

/* Worst outcome wins: the caller must not be told a segment was fine because
 * its last message happened to be. */
static_always_inline int
worse (int a, int b)
{
  return b > a ? b : a;
}

/*
 * Replace a big-endian u16 inside the TCP payload, keeping the checksum
 * right. Incremental, so it composes with the address and port rewrite the
 * translation node does afterwards.
 */
static_always_inline void
pptp_set_u16 (tcp_header_t *tcp, u16 *field, u16 new_host)
{
  u16 nv = clib_host_to_net_u16 (new_host);
  ip_csum_t sum;

  if (field[0] == nv)
    return;
  sum = tcp->checksum;
  sum = ip_csum_update (sum, field[0], nv, tcp_header_t, src_port);
  tcp->checksum = ip_csum_fold (sum);
  field[0] = nv;
}

/*
 * The TCP payload, or 0 when there is none to look at.
 *
 * Refuses anything it cannot see whole. A control message split across
 * segments is not reassembled here: doing that means holding subscriber
 * payload in the dataplane, and PPTP control messages are small and sent one
 * per segment by every implementation in use. A split message is counted as
 * malformed rather than half-parsed.
 */
static_always_inline u8 *
pptp_payload (vlib_buffer_t *b, tcp_header_t *tcp, u32 *len)
{
  u8 *end = (u8 *) vlib_buffer_get_current (b) + b->current_length;
  u32 hlen;
  u8 *p;

  if (PREDICT_FALSE (tcp == 0))
    return 0;
  if (PREDICT_FALSE ((u8 *) tcp + sizeof (*tcp) > end))
    return 0;

  hlen = tcp_header_bytes (tcp);
  if (PREDICT_FALSE (hlen < sizeof (*tcp)))
    return 0;

  p = (u8 *) tcp + hlen;
  if (PREDICT_FALSE (p >= end))
    return 0;

  *len = (u32) (end - p);
  return p;
}

/* The GRE session this call request already created, if it is a retransmit. */
static bpa_session_t *
pptp_find_by_own (bpa_per_thread_t *pt, u32 ip_priv, u16 call_id, u32 remote,
		  u32 fib)
{
  clib_bihash_kv_16_8_t kv, value;

  bpa_key_gre (&kv, ip_priv, call_id, remote, BPA_GRE_KEY_ALG, fib);
  if (clib_bihash_search_16_8 (&pt->in2out, &kv, &value))
    return 0;
  return pool_elt_at_index (pt->sessions, value.value);
}

/*
 * The call named by a Call ID this box handed out.
 *
 * The subscriber's private address is checked against the control connection
 * the message arrived on. Without that check a server could name any Call ID
 * on the shared public address and tear down, or redirect, a call belonging
 * to a different subscriber - the public address is shared, so the Call ID
 * alone identifies nobody in particular.
 */
static bpa_session_t *
pptp_find_by_ours (bpa_per_thread_t *pt, const bpa_session_t *ctrl,
		   u16 call_id)
{
  clib_bihash_kv_16_8_t kv, value;
  bpa_session_t *s;

  bpa_key_out (&kv, ctrl->ip_pub, call_id, BPA_PROTO_GRE);
  if (clib_bihash_search_16_8 (&pt->out2in, &kv, &value))
    return 0;
  s = pool_elt_at_index (pt->sessions, value.value);
  if (PREDICT_FALSE (s->ip_priv != ctrl->ip_priv))
    return 0;
  return s;
}

/* ------------------------------------------------------------- outbound */

static int
pptp_out_message (bpa_per_thread_t *pt, tcp_header_t *tcp, u8 *msg, u32 len,
		  bpa_session_t *ctrl, f64 now)
{
  bpa_main_t *bm = &bpa_main;
  u16 type = clib_net_to_host_u16 (((u16 *) msg)[4]);
  u16 *cid = (u16 *) (msg + 12);
  bpa_session_t *call;
  bpa_pool_t *p;
  u16 own;

  switch (type)
    {
    case PPTP_OUT_CALL_REQUEST:
    case PPTP_CALL_CLEAR_REQUEST:
      /*
       * Both carry the Call ID the subscriber assigned itself, at offset 12.
       * Incoming-Call-Reply carries one too, and is deliberately not handled:
       * it only appears when the subscriber is acting as a PAC answering a
       * dial-in call, which nothing behind a CGNAT can be - the call would
       * have to reach it first, and there is no mapping until it does.
       */
      break;
    default:
      /* Start/Stop-Control-Connection, Echo, Set-Link-Info outbound: no Call
       * ID of the subscriber's in them, so nothing to do. */
      return BPA_PPTP_NOTHING;
    }

  if (PREDICT_FALSE (len < 14))
    return BPA_PPTP_MALFORMED;

  own = clib_net_to_host_u16 (cid[0]);

  call = pptp_find_by_own (pt, ctrl->ip_priv, own, ctrl->remote,
			   ctrl->fib_index);
  if (call == 0)
    {
      if (type != PPTP_OUT_CALL_REQUEST)
	{
	  /* Clearing a call this box never saw. Nothing to rewrite: passing
	   * it on unchanged is right, because the server will recognise its
	   * own Call ID even if we do not. */
	  return BPA_PPTP_UNKNOWN;
	}

      if (PREDICT_FALSE (pool_is_free_index (bm->pools, ctrl->pool_index)))
	return BPA_PPTP_UNKNOWN;
      p = pool_elt_at_index (bm->pools, ctrl->pool_index);

      call = bpa_session_create_gre (pt, p, ctrl->pool_index, ctrl->sub_idx,
				     ctrl->ip_pub, own, ctrl->remote,
				     ctrl->fib_index, now);
      if (PREDICT_FALSE (call == 0))
	{
	  /*
	   * The block is full. The call request goes out UNCHANGED, which
	   * means the subscriber's own Call ID reaches the server and the
	   * tunnel may work or may collide with a neighbour's. Rewriting it
	   * to something we did not allocate would be worse: it would send a
	   * Call ID that belongs to another subscriber.
	   */
	  return BPA_PPTP_NO_CALL_ID;
	}
    }

  call->last_heard = now;
  pptp_set_u16 (tcp, cid, call->port_pub);
  return BPA_PPTP_MANGLED;
}

int
bpa_pptp_out (bpa_per_thread_t *pt, vlib_buffer_t *b, tcp_header_t *tcp,
	      bpa_session_t *ctrl, f64 now)
{
  int result = BPA_PPTP_NOTHING;
  u32 len = 0, off = 0;
  u8 *pay;

  pay = pptp_payload (b, tcp, &len);
  if (pay == 0)
    return BPA_PPTP_NOTHING;

  while (off + PPTP_HDR_BYTES <= len)
    {
      u8 *msg = pay + off;
      u32 mlen = clib_net_to_host_u16 (((u16 *) msg)[0]);

      if (clib_net_to_host_u16 (((u16 *) msg)[1]) != PPTP_MSG_CONTROL
	  || clib_net_to_host_u32 (*(u32 *) (msg + 4)) != PPTP_MAGIC)
	return worse (result, BPA_PPTP_MALFORMED);
      if (mlen < PPTP_HDR_BYTES || off + mlen > len)
	return worse (result, BPA_PPTP_MALFORMED);

      result = worse (result,
		      pptp_out_message (pt, tcp, msg, mlen, ctrl, now));
      off += mlen;
    }

  return result;
}

/* -------------------------------------------------------------- inbound */

static int
pptp_in_message (bpa_per_thread_t *pt, tcp_header_t *tcp, u8 *msg, u32 len,
		 bpa_session_t *ctrl, f64 now)
{
  u16 type = clib_net_to_host_u16 (((u16 *) msg)[4]);
  bpa_session_t *call;
  u16 *pcid;
  u32 need;
  int teardown = 0, reject = 0;

  switch (type)
    {
    case PPTP_OUT_CALL_REPLY:
      /* Call ID at 12 is the server's; Peer's Call ID at 14 is the one this
       * box handed out and must be turned back into the subscriber's. */
      pcid = (u16 *) (msg + 14);
      need = 20; /* through the Result Code at offset 16 */
      break;
    case PPTP_CALL_DISCONNECT_NOTIFY:
      pcid = (u16 *) (msg + 12);
      need = 14;
      teardown = 1;
      break;
    case PPTP_WAN_ERROR_NOTIFY:
    case PPTP_SET_LINK_INFO:
      pcid = (u16 *) (msg + 12);
      need = 14;
      break;
    default:
      /* Incoming-Call-Request names only the server's own Call ID, and the
       * rest carry none at all. */
      return BPA_PPTP_NOTHING;
    }

  if (PREDICT_FALSE (len < need))
    return BPA_PPTP_MALFORMED;

  call = pptp_find_by_ours (pt, ctrl, clib_net_to_host_u16 (pcid[0]));
  if (call == 0)
    return BPA_PPTP_UNKNOWN;

  if (type == PPTP_OUT_CALL_REPLY)
    {
      if (msg[16] == PPTP_RESULT_CONNECTED)
	bpa_session_gre_established (
	  pt, call, clib_net_to_host_u16 (((u16 *) (msg + 12))[0]));
      else
	{
	  /*
	   * The call was refused. Holding the Call ID until it times out
	   * would let a subscriber retrying against a server that keeps
	   * saying no drain its own block, one entry per attempt.
	   */
	  reject = 1;
	}
    }

  call->last_heard = now;
  pptp_set_u16 (tcp, pcid, call->port_priv);

  if (teardown || reject)
    bpa_session_free (pt, call, now);

  return BPA_PPTP_MANGLED;
}

int
bpa_pptp_in (bpa_per_thread_t *pt, vlib_buffer_t *b, tcp_header_t *tcp,
	     bpa_session_t *ctrl, f64 now)
{
  int result = BPA_PPTP_NOTHING;
  u32 len = 0, off = 0;
  u8 *pay;

  pay = pptp_payload (b, tcp, &len);
  if (pay == 0)
    return BPA_PPTP_NOTHING;

  while (off + PPTP_HDR_BYTES <= len)
    {
      u8 *msg = pay + off;
      u32 mlen = clib_net_to_host_u16 (((u16 *) msg)[0]);

      if (clib_net_to_host_u16 (((u16 *) msg)[1]) != PPTP_MSG_CONTROL
	  || clib_net_to_host_u32 (*(u32 *) (msg + 4)) != PPTP_MAGIC)
	return worse (result, BPA_PPTP_MALFORMED);
      if (mlen < PPTP_HDR_BYTES || off + mlen > len)
	return worse (result, BPA_PPTP_MALFORMED);

      result = worse (result, pptp_in_message (pt, tcp, msg, mlen, ctrl, now));
      off += mlen;
    }

  return result;
}
