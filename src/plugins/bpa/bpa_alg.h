/*
 * bpa_alg.h - application-level gateway machinery shared by the two
 * translation nodes.
 *
 * An ALG rewrites a payload, and a payload rewrite is three problems, not one:
 *
 *  1. the bytes, which is protocol-specific and lives in bpa_rtsp.h and
 *     bpa_sip.h;
 *  2. the packet, whose length and both checksums change with it;
 *  3. the stream, whose sequence numbers no longer describe where the bytes
 *     are - and that one does not fail at the rewrite, it fails later.
 *
 * The ICMP error translation of 0.2.0 is the precedent for taking (2)
 * seriously: rewriting the packet was not enough, because a wrong checksum
 * makes the subscriber's stack drop it silently while the counter reports a
 * success. The same is true here, with the addition that TCP will also drop it
 * for a sequence number that does not line up.
 *
 * (3) is TCP's alone, and that is why the SIP half of this file looks so much
 * smaller than the RTSP half rather than less careful. A datagram has no
 * stream to renumber: what a UDP payload rewrite invalidates is the IP total
 * length, the UDP length and the UDP checksum, and that is the whole list.
 * Carrying bpa_seq_adj_t through a UDP path would be apparatus for a problem
 * that cannot arise, and a reader would reasonably conclude it was there
 * because something needed it.
 */

#ifndef __included_bpa_alg_h__
#define __included_bpa_alg_h__

#include <vnet/ip/ip4_packet.h>
#include <vnet/tcp/tcp_packet.h>
#include <vnet/udp/udp_packet.h>

#include "bpa.h"
#include "bpa_seq.h"
#include "bpa_rtsp.h"
#include "bpa_sip.h"

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
			    BPA_PROTO_UDP, s->fib_index, now, &pub_lo))
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

  bpa_key_out (&kv, s->ip_pub, cp.lo, BPA_PROTO_UDP);
  if (clib_bihash_search_16_8 (&pt->out2in, &kv, &v0))
    return BPA_ALG_UNPARSED;
  bpa_key_out (&kv, s->ip_pub, cp.hi, BPA_PROTO_UDP);
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

/*
 * The UDP payload of a datagram that is entirely inside this buffer.
 *
 * Returns -1 for anything this box must not reach into: a datagram with no
 * payload, a UDP length that disagrees with the IP length, or a length the
 * buffer does not actually hold. A UDP header carries its OWN length as well
 * as the IP header's, and the two disagreeing is either a truncated capture or
 * a crafted packet - the SIP parser is reachable from the internet on a port
 * that is scanned continuously, so the bound comes from the smaller of the two
 * and both are checked before a single payload byte is read.
 */
static_always_inline int
bpa_alg_udp_payload (vlib_buffer_t *b, ip4_header_t *ip, udp_header_t *udp,
		     u8 **pl, u32 *pl_len)
{
  u8 *end = (u8 *) vlib_buffer_get_current (b) + b->current_length;
  u32 ip_hlen = ip4_header_bytes (ip);
  u32 ip_len = clib_net_to_host_u16 (ip->length);
  u32 udp_len = clib_net_to_host_u16 (udp->length);
  u8 *p;

  if (PREDICT_FALSE (udp_len <= sizeof (udp_header_t)))
    return -1;
  if (PREDICT_FALSE (ip_len != ip_hlen + udp_len))
    return -1;

  p = (u8 *) udp + sizeof (udp_header_t);
  *pl = p;
  *pl_len = udp_len - (u32) sizeof (udp_header_t);

  if (PREDICT_FALSE (p + *pl_len > end))
    return -1;
  return 0;
}

/*
 * Repairs everything a UDP payload rewrite invalidated: the buffer, the IP
 * total length and checksum, the UDP length, and the UDP checksum.
 *
 * The UDP checksum is recomputed in full rather than incrementally, for the
 * reason bpa_alg_splice gives: the payload changed length, so there is no
 * old-value/new-value pair to feed ip_csum_update with. Computed even when the
 * datagram arrived with a zero checksum - which is legal IPv4 UDP for "not
 * computed" - because the packet that leaves this box should be one a receiver
 * can check, and a SIP stack behind a residential CPE is not the place to find
 * out that it could not.
 *
 * The all-zero result is sent as 0xffff (RFC 768): on the wire zero means "no
 * checksum here", so a correctly computed zero would tell the receiver not to
 * check the very thing that was just made correct.
 */
static_always_inline void
bpa_alg_udp_relength (vlib_main_t *vm, vlib_buffer_t *b, ip4_header_t *ip,
		      udp_header_t *udp, i32 d)
{
  if (d)
    {
      b->current_length = (u16) ((i32) b->current_length + d);
      ip->length = clib_host_to_net_u16 (
	(u16) ((i32) clib_net_to_host_u16 (ip->length) + d));
      udp->length = clib_host_to_net_u16 (
	(u16) ((i32) clib_net_to_host_u16 (udp->length) + d));
      ip->checksum = ip4_header_checksum (ip);
    }

  udp->checksum = 0;
  udp->checksum = ip4_tcp_udp_compute_checksum (vm, b, ip);
  if (udp->checksum == 0)
    udp->checksum = 0xffff;
}

/*
 * SIP, subscriber -> internet.
 *
 * The two failures an ISP pays for, in one function:
 *
 *  - the phone registers a Contact nobody can reach. Every inbound call is
 *    rejected by the softswitch as "user not registered" or rings nowhere, and
 *    the subscriber's complaint is "I can call out but nobody can call me".
 *  - the call connects and there is no audio, or audio one way. The SDP
 *    announced a private address and a port on it, and the far end is sending
 *    RTP to a place that does not exist. This is the single most expensive
 *    support call an ISP takes, because it does not look like a network fault
 *    to anyone: the signalling worked.
 *
 * Both are the same fix - rewrite what the payload says about where to reach
 * this subscriber - and one of them, the audio, also needs a MAPPING to exist
 * before the far end uses it. That is what bpa_alg_reserve_pair is for, and it
 * is the same call the RTSP SETUP above makes: an ALG that rewrites SDP and
 * opens no expectation delivers signalling and no audio, which is the failure
 * it was deployed to remove.
 *
 * Runs on the ALREADY-TRANSLATED packet, so the address it writes into the
 * payload is the one the subscriber now appears to have, and so the checksum
 * it recomputes covers the pseudo-header the datagram will travel with.
 *
 * Direction is not a parameter and there is no request/response test. A
 * response the subscriber sends carries the CALLER's Via, and the rule that
 * only the subscriber's own private address is ever rewritten settles that on
 * its own - see the head of bpa_sip.h.
 */
static_always_inline bpa_alg_result_t
bpa_sip_in2out (vlib_main_t *vm, vlib_buffer_t *b, ip4_header_t *ip,
		udp_header_t *udp, bpa_per_thread_t *pt, const bpa_pool_t *p,
		u32 s_index, f64 now)
{
  bpa_session_t *s = pool_elt_at_index (pt->sessions, s_index);
  bpa_sip_msg_t m;
  bpa_sip_edit_t ed[BPA_SIP_MAX_ITEMS];
  u8 txt[BPA_SIP_TXT_MAX];
  u8 *pl;
  u32 pl_len, n_ed = 0, i, want = 0, clen_at = ~0u;
  i32 body_d = 0, d;
  /*
   * Every field of the session is read ONCE, here, and the pointer is not used
   * again.
   *
   * bpa_alg_reserve_pair calls pool_get_zero on pt->sessions, which reallocates
   * when the pool grows, and every bpa_session_t pointer taken before that call
   * then points into freed memory. The RTSP ALG re-resolves by index after its
   * single reservation; this one may reserve up to BPA_SIP_MAX_MEDIA times, so
   * it copies instead - there is no pointer left to go stale.
   */
  const u32 ip_priv = s->ip_priv, ip_pub = s->ip_pub;
  const u32 pool_index = s->pool_index, sub_idx = s->sub_idx;
  const u32 fib_index = s->fib_index;
  const u16 port_pub = s->port_pub;

  /* A message this parser cannot see the end of. A SIP datagram spread over
   * several buffers is jumbo or reassembled oddly, and either way the body
   * the endpoints will read is not the one in front of us. */
  if (PREDICT_FALSE (b->flags & VLIB_BUFFER_NEXT_PRESENT))
    return BPA_ALG_PARTIAL;
  if (bpa_alg_udp_payload (b, ip, udp, &pl, &pl_len))
    return BPA_ALG_NOOP;

  switch (bpa_sip_parse (pl, pl_len, &m))
    {
    case BPA_SIP_PARSED:
      break;
    case BPA_SIP_UNPARSED:
      return BPA_ALG_UNPARSED;
    default:
      return BPA_ALG_NOOP;
    }

  /*
   * What this message could grow by, worked out BEFORE a single port is
   * reserved. A reservation made and then abandoned because the rewrite did
   * not fit holds two ports of a 512-port block for the expectation timeout,
   * and a phone retrying every few seconds turns that into exhaustion of its
   * own subscriber's block - the lesson bpa_rtsp_in2out records.
   */
  for (i = 0; i < m.n_items; i++)
    {
      const bpa_sip_item_t *it = &m.items[i];
      u32 widest = 0;

      switch (it->kind)
	{
	case BPA_SIP_VIA:
	case BPA_SIP_CONTACT:
	  widest = it->addr == ip_priv ? BPA_SIP_TXT_MAX : 0;
	  break;
	case BPA_SIP_CONN:
	  widest = it->addr == ip_priv ? 15 : 0; /* "255.255.255.255" */
	  break;
	case BPA_SIP_MEDIA:
	  widest = it->addr == ip_priv ? 5 : 0; /* "65535" */
	  break;
	case BPA_SIP_CLEN:
	  clen_at = i;
	  widest = 5;
	  break;
	}
      if (widest > it->len)
	want += widest - it->len;
    }

  if (want == 0)
    return BPA_ALG_NOOP;
  if ((i32) b->current_data + (i32) b->current_length + (i32) want
      > (i32) vlib_buffer_get_default_data_size (vm))
    return BPA_ALG_NO_ROOM;

  for (i = 0; i < m.n_items; i++)
    {
      const bpa_sip_item_t *it = &m.items[i];
      u32 n;

      if (it->addr != ip_priv)
	continue;

      switch (it->kind)
	{
	/*
	 * Via says where a RESPONSE to this request goes; Contact says where
	 * the NEXT request in this dialog goes, and in a REGISTER it is the
	 * whole point of the message. Both are written as the public address
	 * and the public port of this very session, which is the one mapping
	 * that is certain to exist for as long as the phone keeps talking.
	 */
	case BPA_SIP_VIA:
	case BPA_SIP_CONTACT:
	  n = bpa_sip_fmt_addr_port (txt, ip_pub, port_pub);
	  if (bpa_sip_edit_add (ed, &n_ed, it->off, it->len, txt, n))
	    return BPA_ALG_NO_ROOM;
	  break;

	/* Where the media is to be sent. Inside the body, so it moves
	 * Content-Length with it. */
	case BPA_SIP_CONN:
	  n = bpa_sip_fmt_addr (txt, ip_pub);
	  if (bpa_sip_edit_add (ed, &n_ed, it->off, it->len, txt, n))
	    return BPA_ALG_NO_ROOM;
	  body_d += (i32) n - (i32) it->len;
	  break;

	/*
	 * The port the media is to be sent to, and the mapping that makes it
	 * arrive. RTP on the port and RTCP on the one above it, which is why
	 * the pair is reserved rather than a single port allocated: RFC 3550
	 * s11 puts RTCP at port+1 and a stream whose RTCP goes nowhere is a
	 * call that drops after a minute on some equipment rather than one
	 * that never starts.
	 *
	 * A failure here abandons any pair reserved for an earlier m= line in
	 * the same message. Deliberate: the alternative is unwinding them,
	 * which is a second code path exercised by nothing, and they expire on
	 * BPA_TIMEOUT_ALG_EXPECT - the timer that exists for exactly this.
	 */
	case BPA_SIP_MEDIA:
	  {
	    u16 pub_lo = 0;

	    if (bpa_alg_reserve_pair (pt, p, pool_index, sub_idx, ip_pub,
				      it->port, BPA_PROTO_UDP, fib_index, now,
				      &pub_lo))
	      return BPA_ALG_NO_PORTS;

	    n = bpa_sip_fmt_u32 (txt, pub_lo);
	    if (bpa_sip_edit_add (ed, &n_ed, it->off, it->len, txt, n))
	      return BPA_ALG_NO_ROOM;
	    body_d += (i32) n - (i32) it->len;
	  }
	  break;

	default:
	  break;
	}
    }

  /*
   * Content-Length last, because until every body edit is known there is no
   * number to write - and first in the message, which is why the edit list
   * sorts on insertion.
   *
   * Not optional. A body that grew by two bytes under a Content-Length that
   * did not is a message the far end reads two bytes short of its end, which
   * on a proxy is a 400 Bad Request and on a phone is silence. The header is
   * the one thing here rewritten because of what this ALG did rather than
   * because of what it found.
   */
  if (body_d)
    {
      u32 n;

      if (clen_at == ~0u)
	return BPA_ALG_UNPARSED;
      n = bpa_sip_fmt_u32 (txt, (u32) ((i32) m.body_len + body_d));
      if (bpa_sip_edit_add (ed, &n_ed, m.items[clen_at].off,
			    m.items[clen_at].len, txt, n))
	return BPA_ALG_NO_ROOM;
    }

  if (n_ed == 0)
    return BPA_ALG_NOOP;

  d = bpa_sip_apply (pl, pl_len, ed, n_ed);
  bpa_alg_udp_relength (vm, b, ip, udp, d);
  return BPA_ALG_DONE;
}

/*
 * SIP, internet -> subscriber: putting the top Via back the way the phone
 * wrote it.
 *
 * RFC 3261 s18.1.2 has a client discard a response whose topmost Via is not
 * the one it sent. Having rewritten that Via on the way out, this box has to
 * undo it on the way back or every response is silently dropped by the phone
 * itself - the call appears to go out and nothing ever answers.
 *
 * Responses only, and only when the Via names this session's own public
 * address AND public port. That pair is the exact thing this box wrote; an
 * inbound REQUEST's topmost Via belongs to whoever sent it, and a crafted one
 * naming our public address has nothing to gain but is refused anyway because
 * the rule is cheaper to state than to except.
 *
 * What is deliberately NOT put back:
 *
 *   Contact, in a response  It is the REMOTE's, and in a 200 to a REGISTER it
 *                           is the registrar echoing the whole binding list.
 *                           The public address is what the registrar actually
 *                           holds, so showing the phone something else would
 *                           be describing a binding that does not exist.
 *   the SDP body            The answer's c= and m= are the far end's own
 *                           public address and port. There is nothing of this
 *                           subscriber's in them.
 *   Request-URI, inbound    An inbound in-dialog request arrives addressed to
 *                           the Contact this box published, so its
 *                           Request-URI names the public address. Most user
 *                           agents route on the dialog rather than on the
 *                           Request-URI host; the ones that do not would need
 *                           this, and it is written down as not done rather
 *                           than guessed at.
 */
static_always_inline bpa_alg_result_t
bpa_sip_out2in (vlib_main_t *vm, vlib_buffer_t *b, ip4_header_t *ip,
		udp_header_t *udp, bpa_session_t *s)
{
  bpa_sip_msg_t m;
  bpa_sip_edit_t ed[BPA_SIP_MAX_ITEMS];
  u8 txt[BPA_SIP_TXT_MAX];
  u8 *pl;
  u32 pl_len, n_ed = 0, i, n;
  i32 d;

  if (PREDICT_FALSE (b->flags & VLIB_BUFFER_NEXT_PRESENT))
    return BPA_ALG_PARTIAL;
  if (bpa_alg_udp_payload (b, ip, udp, &pl, &pl_len))
    return BPA_ALG_NOOP;

  switch (bpa_sip_parse (pl, pl_len, &m))
    {
    case BPA_SIP_PARSED:
      break;
    case BPA_SIP_UNPARSED:
      return BPA_ALG_UNPARSED;
    default:
      return BPA_ALG_NOOP;
    }

  if (!m.is_response)
    return BPA_ALG_NOOP;

  for (i = 0; i < m.n_items; i++)
    {
      const bpa_sip_item_t *it = &m.items[i];

      if (it->kind != BPA_SIP_VIA)
	continue;
      if (it->addr != s->ip_pub || it->port != s->port_pub)
	break;

      if ((i32) b->current_data + (i32) b->current_length
	    + (i32) BPA_SIP_TXT_MAX
	  > (i32) vlib_buffer_get_default_data_size (vm))
	return BPA_ALG_NO_ROOM;

      n = bpa_sip_fmt_addr_port (txt, s->ip_priv, s->port_priv);
      if (bpa_sip_edit_add (ed, &n_ed, it->off, it->len, txt, n))
	return BPA_ALG_NO_ROOM;
      break;
    }

  if (n_ed == 0)
    return BPA_ALG_NOOP;

  d = bpa_sip_apply (pl, pl_len, ed, n_ed);
  bpa_alg_udp_relength (vm, b, ip, udp, d);
  return BPA_ALG_DONE;
}

#endif /* __included_bpa_alg_h__ */
