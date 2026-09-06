/*
 * bpa_alg.h - application-level gateway machinery shared by the two
 * translation nodes.
 *
 * An ALG rewrites a payload, and a payload rewrite is three problems, not one:
 *
 *  1. the bytes, which is protocol-specific and lives in bpa_rtsp.h;
 *  2. the packet, whose length and both checksums change with it;
 *  3. the stream, whose sequence numbers no longer describe where the bytes
 *     are - and that one does not fail at the rewrite, it fails later.
 *
 * The ICMP error translation of 0.2.0 is the precedent for taking (2)
 * seriously: rewriting the packet was not enough, because a wrong checksum
 * makes the subscriber's stack drop it silently while the counter reports a
 * success. The same is true here, with the addition that TCP will also drop it
 * for a sequence number that does not line up.
 */

#ifndef __included_bpa_alg_h__
#define __included_bpa_alg_h__

#include <vnet/ip/ip4_packet.h>
#include <vnet/tcp/tcp_packet.h>

#include "bpa.h"
#include "bpa_rtsp.h"

typedef enum
{
  BPA_ALG_NOOP = 0, /* nothing here for us */
  BPA_ALG_DONE,	    /* rewritten */
  BPA_ALG_UNPARSED, /* ours, not understood, left untouched */
  BPA_ALG_PARTIAL,  /* not contained in one segment, left untouched */
  BPA_ALG_NO_ROOM,  /* the longer payload would not fit, left untouched */
  BPA_ALG_NO_PORTS, /* no consecutive media port pair available */
} bpa_alg_result_t;

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
 * Delta that applies to one position in a stream.
 *
 * Signed comparison, so a stream that has wrapped past 2^32 still answers
 * "which side of the correction point is this" correctly.
 */
static_always_inline i32
bpa_seq_delta_at (const bpa_seq_adj_t *a, u32 seq)
{
  return ((i32) (seq - a->correction_pos) > 0) ? a->off_after : a->off_before;
}

/*
 * Moves a TCP header's sequence and acknowledgement numbers onto the rewritten
 * stream, fixing the checksum as it goes.
 *
 * `fwd` describes the direction this packet travels; `rev` the other one, from
 * which its acknowledgements come. The acknowledgement is un-corrected before
 * being classified, because it is expressed in the rewritten stream's
 * coordinates and the correction point is expressed in the original's.
 */
static_always_inline void
bpa_tcp_seq_adjust (tcp_header_t *tcp, const bpa_seq_adj_t *fwd,
		    const bpa_seq_adj_t *rev)
{
  ip_csum_t sum = tcp->checksum;
  u32 seq = clib_net_to_host_u32 (tcp->seq_number);
  u32 ack = clib_net_to_host_u32 (tcp->ack_number);
  i32 dseq = bpa_seq_delta_at (fwd, seq);
  i32 dack;

  if (rev->off_after || rev->off_before)
    dack = bpa_seq_delta_at (rev, ack - (u32) rev->off_before);
  else
    dack = 0;

  if (dseq)
    {
      u32 new_seq = clib_host_to_net_u32 (seq + (u32) dseq);
      sum = ip_csum_update (sum, tcp->seq_number, new_seq, tcp_header_t,
			    seq_number);
      tcp->seq_number = new_seq;
    }

  /* Only a segment that carries an acknowledgement has an ack field worth
   * moving; in a SYN without ACK the field is whatever the sender left there. */
  if (dack && (tcp->flags & TCP_FLAG_ACK))
    {
      u32 new_ack = clib_host_to_net_u32 (ack - (u32) dack);
      sum = ip_csum_update (sum, tcp->ack_number, new_ack, tcp_header_t,
			    ack_number);
      tcp->ack_number = new_ack;
    }

  tcp->checksum = ip_csum_fold (sum);
}

/*
 * Replaces payload[off .. off+old_len) with `new`, and repairs everything the
 * change invalidates: the IP total length, the IP checksum, the TCP checksum
 * and - when the length moved - this direction's sequence correction.
 *
 * The TCP checksum is recomputed in full rather than incrementally. The
 * payload changed length, so there is no old-value/new-value pair to feed
 * ip_csum_update with, and getting that wrong is the failure this project has
 * already seen once: the packet is translated, the counter says so, and the
 * receiver drops it without a word.
 *
 * MUST run before bpa_tcp_seq_adjust, because the correction point it records
 * is in the sender's own coordinates.
 */
static_always_inline int
bpa_alg_splice (vlib_main_t *vm, vlib_buffer_t *b, ip4_header_t *ip,
		tcp_header_t *tcp, u8 *pl, u32 pl_len, u32 off, u32 old_len,
		const u8 *new_txt, u32 new_len, bpa_seq_adj_t *adj)
{
  i32 d = (i32) new_len - (i32) old_len;

  if (d > 0
      && (i32) b->current_data + (i32) b->current_length + d
	   > (i32) vlib_buffer_get_default_data_size (vm))
    return -1;

  if (d)
    clib_memmove (pl + off + new_len, pl + off + old_len,
		  pl_len - off - old_len);
  clib_memcpy_fast (pl + off, new_txt, new_len);

  if (d)
    {
      u32 seq = clib_net_to_host_u32 (tcp->seq_number);

      b->current_length = (u16) (b->current_length + d);
      ip->length = clib_host_to_net_u16 (
	(u16) ((i32) clib_net_to_host_u16 (ip->length) + d));
      ip->checksum = ip4_header_checksum (ip);

      /*
       * Everything sent after this segment is displaced; this segment, and
       * any retransmission of it, is not - which is why the correction point
       * is this segment's own sequence number and the comparison is strict.
       *
       * Unless this IS that retransmission, arriving with the same rewrite
       * already accounted for. Pushing a second correction for it would
       * double the offset, and the connection would stall a few segments
       * later looking like something else entirely.
       */
      if (adj->correction_pos != seq || adj->off_after - adj->off_before != d)
	{
	  adj->off_before = adj->off_after;
	  adj->off_after += d;
	  adj->correction_pos = seq;
	}
    }

  tcp->checksum = 0;
  tcp->checksum = ip4_tcp_udp_compute_checksum (vm, b, ip);
  return 0;
}

/*
 * RTSP, subscriber -> internet: the SETUP that announces where the client
 * wants its media.
 *
 * The ports in the header are the subscriber's private ones, which mean
 * nothing on the internet: the server sends RTP to them at our public address
 * and it lands on a port belonging to nobody. The control connection succeeds
 * and the video never arrives - the black frame from an IP camera that works
 * on the LAN.
 */
static_always_inline bpa_alg_result_t
bpa_rtsp_in2out (vlib_main_t *vm, vlib_buffer_t *b, ip4_header_t *ip,
		 tcp_header_t *tcp, bpa_per_thread_t *pt, const bpa_pool_t *p,
		 u32 s_index, f64 now)
{
  /*
   * The session is addressed by INDEX, not by pointer, and re-resolved after
   * the reservation.
   *
   * Opening the two media sessions calls pool_get_zero on pt->sessions, which
   * reallocates the pool when it grows - and every bpa_session_t pointer taken
   * before that call then points into freed memory. The first version held the
   * caller's pointer across it: the rewrite worked, the sequence correction
   * was written to the old allocation, and the next segment left with an
   * uncorrected sequence number. The lab caught it because it reads the
   * captured packet; nothing else did.
   */
  bpa_session_t *s = pool_elt_at_index (pt->sessions, s_index);
  bpa_rtsp_pair_t cp;
  u8 txt[BPA_RTSP_PAIR_MAX];
  u8 *pl;
  u32 pl_len, n;
  u16 pub_lo = 0;

  /* A message spread over several buffers is a message this parser cannot
   * see the end of. */
  if (PREDICT_FALSE (b->flags & VLIB_BUFFER_NEXT_PRESENT))
    return BPA_ALG_PARTIAL;
  if (bpa_tcp_payload (b, ip, tcp, &pl, &pl_len))
    return BPA_ALG_NOOP;

  switch (bpa_rtsp_client_port (pl, pl_len, 0, &cp))
    {
    case BPA_RTSP_FOUND:
      break;
    case BPA_RTSP_UNPARSED:
      return BPA_ALG_UNPARSED;
    case BPA_RTSP_PARTIAL:
      return BPA_ALG_PARTIAL;
    default:
      return BPA_ALG_NOOP;
    }

  /* Checked before the ports are taken: a reservation made and then abandoned
   * because the rewrite did not fit holds two ports for a timeout. */
  if ((i32) b->current_data + (i32) b->current_length
	+ (i32) BPA_RTSP_PAIR_MAX
      > (i32) vlib_buffer_get_default_data_size (vm))
    return BPA_ALG_NO_ROOM;

  if (bpa_alg_reserve_pair (pt, p, s->pool_index, s->sub_idx, s->ip_pub, cp.lo,
			    BPA_PROTO_UDP, now, &pub_lo))
    return BPA_ALG_NO_PORTS;
  s = pool_elt_at_index (pt->sessions, s_index); /* the pool may have moved */

  n = bpa_rtsp_format_pair (txt, pub_lo, (u16) (pub_lo + 1));
  if (bpa_alg_splice (vm, b, ip, tcp, pl, pl_len, cp.off, cp.len, txt, n,
		      &s->adj[BPA_DIR_OUT]))
    return BPA_ALG_NO_ROOM;

  return BPA_ALG_DONE;
}

/*
 * RTSP, internet -> subscriber: the 200 reply, which echoes client_port back.
 *
 * It echoes the PUBLIC pair we put in the request, and some clients check that
 * the reply names the ports they asked for. The private pair is not carried
 * anywhere: it is recovered from the two media mappings the request opened, and
 * if either of them is missing the packet is left alone. Restoring one port of
 * a pair, or guessing the other, is worse than not rewriting at all.
 */
static_always_inline bpa_alg_result_t
bpa_rtsp_out2in (vlib_main_t *vm, vlib_buffer_t *b, ip4_header_t *ip,
		 tcp_header_t *tcp, bpa_per_thread_t *pt, bpa_session_t *s)
{
  clib_bihash_kv_16_8_t kv, v0, v1;
  bpa_rtsp_pair_t cp;
  bpa_session_t *m0, *m1;
  u8 txt[BPA_RTSP_PAIR_MAX];
  u8 *pl;
  u32 pl_len, n;

  if (PREDICT_FALSE (b->flags & VLIB_BUFFER_NEXT_PRESENT))
    return BPA_ALG_PARTIAL;
  if (bpa_tcp_payload (b, ip, tcp, &pl, &pl_len))
    return BPA_ALG_NOOP;

  switch (bpa_rtsp_client_port (pl, pl_len, 1, &cp))
    {
    case BPA_RTSP_FOUND:
      break;
    case BPA_RTSP_UNPARSED:
      return BPA_ALG_UNPARSED;
    case BPA_RTSP_PARTIAL:
      return BPA_ALG_PARTIAL;
    default:
      return BPA_ALG_NOOP;
    }

  bpa_key (&kv, s->ip_pub, cp.lo, BPA_PROTO_UDP);
  if (clib_bihash_search_16_8 (&pt->out2in, &kv, &v0))
    return BPA_ALG_UNPARSED;
  bpa_key (&kv, s->ip_pub, cp.hi, BPA_PROTO_UDP);
  if (clib_bihash_search_16_8 (&pt->out2in, &kv, &v1))
    return BPA_ALG_UNPARSED;

  m0 = pool_elt_at_index (pt->sessions, v0.value);
  m1 = pool_elt_at_index (pt->sessions, v1.value);

  /* They must belong to the subscriber this control connection belongs to,
   * and they must still be a pair on the private side. */
  if (m0->sub_idx != s->sub_idx || m1->sub_idx != s->sub_idx
      || m1->port_priv != m0->port_priv + 1)
    return BPA_ALG_UNPARSED;

  n = bpa_rtsp_format_pair (txt, m0->port_priv, m1->port_priv);
  if (bpa_alg_splice (vm, b, ip, tcp, pl, pl_len, cp.off, cp.len, txt, n,
		      &s->adj[BPA_DIR_IN]))
    return BPA_ALG_NO_ROOM;

  return BPA_ALG_DONE;
}

#endif /* __included_bpa_alg_h__ */
