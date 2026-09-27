/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * The sequence-number correction an ALG leaves behind, and the one place it is
 * applied.
 *
 * Its own header because there is exactly ONE of these. There used to be two -
 * this one, and a narrower set of fields the FTP ALG kept for itself - which
 * arrived on separate branches at the same time. Folding them together during
 * a merge, with no test that would catch a mistake, is how a merge breaks two
 * working features at once, so they were left alone and #48 was filed so that
 * "we chose not to do it yet" would not quietly become "nobody noticed".
 *
 * A file of its own says the thing a comment cannot: the next ALG that changes
 * a payload length does not have a choice to make.
 *
 * Standalone-capable, so test/test_bpa_ftp.c can reach the arithmetic without
 * VPP. That arithmetic is serial-number arithmetic - the kind that is right for
 * years and then wrong for one connection that lived long enough to wrap.
 */

#ifndef __included_bpa_seq_h__
#define __included_bpa_seq_h__

#ifndef BPA_STANDALONE
#include <vlib/vlib.h>
#include <vnet/tcp/tcp_packet.h>
#endif

/*
 * Direction of a TCP stream within one session. Two, because an ALG that
 * rewrites a payload changes the length of ONE direction, and the other
 * direction's acknowledgements have to be moved by the same amount the other
 * way.
 */
#define BPA_DIR_OUT 0 /* subscriber -> internet */
#define BPA_DIR_IN  1 /* internet -> subscriber */
#define BPA_N_DIR   2

/*
 * Sequence-number correction for one direction of a TCP stream.
 *
 * Rewriting a payload changes its length, so every byte after the rewrite sits
 * at a different offset in the stream than the sender thinks. Left
 * uncorrected, the control connection does not fail at the rewrite: it stalls
 * some seconds later, when the peer acknowledges a byte count that never
 * matches, and the failure looks unrelated to the ALG that caused it.
 *
 * Two generations rather than one running total, so a RETRANSMISSION of the
 * segment that was rewritten - which carries a sequence number from before the
 * correction - is moved by the delta that applied to it at the time, not by
 * the new one. A single cumulative offset gets that case wrong by exactly the
 * length of the rewrite, and a retransmitted SETUP is not a rare event on a
 * camera with a poor uplink.
 *
 * Only the most recent correction is remembered. A stream rewritten twice and
 * then retransmitting from before the FIRST rewrite is still wrong; the
 * alternative is a per-session list of correction points on a datapath whose
 * whole point is not having any, and RTSP is request/response with a small
 * window, so the exposure is one message.
 */
typedef struct
{
  i32 off_before;     /* delta up to and including correction_pos */
  i32 off_after;      /* delta strictly after it */
  u32 correction_pos; /* original sequence number of the rewritten segment */
} bpa_seq_adj_t;

/*
 * Delta that applies to one position in a stream.
 *
 * Signed comparison, so a stream that has wrapped past 2^32 still answers
 * "which side of the correction point is this" correctly. Written with a plain
 * `>` it answers the opposite way round across the boundary, and the ALG then
 * shifts in the wrong direction on the connection least likely to be
 * reproduced by anyone investigating it.
 */
static_always_inline i32
bpa_seq_delta_at (const bpa_seq_adj_t *a, u32 seq)
{
  return ((i32) (seq - a->correction_pos) > 0) ? a->off_after : a->off_before;
}

/*
 * Records that the segment starting at `seq` was rewritten `d` bytes longer
 * (or shorter), in the sender's own coordinates.
 *
 * Everything sent after this segment is displaced; this segment, and
 * any retransmission of it, is not - which is why the correction point
 * is this segment's own sequence number and the comparison is strict.
 *
 * Unless this IS that retransmission, arriving with the same rewrite
 * already accounted for. Pushing a second correction for it would
 * double the offset, and the connection would stall a few segments
 * later looking like something else entirely.
 */
static_always_inline void
bpa_seq_note (bpa_seq_adj_t *adj, u32 seq, i32 d)
{
  if (adj->correction_pos != seq || adj->off_after - adj->off_before != d)
    {
      adj->off_before = adj->off_after;
      adj->off_after += d;
      adj->correction_pos = seq;
    }
}
/*
 * Moves a TCP header's sequence and acknowledgement numbers onto the rewritten
 * stream, fixing the checksum as it goes.
 *
 * `fwd` describes the direction this packet travels; `rev` the other one, from
 * which its acknowledgements come. The acknowledgement is un-corrected before
 * being classified, because it is expressed in the rewritten stream's
 * coordinates and the correction point is expressed in the original's.
 *
 * Returns 1 when it moved a field, so the node can count it. Worth a counter
 * because renumbering is the half of an ALG that keeps working long after the
 * rewrite that caused it - a connection this box lengthened must go on being
 * corrected even after the ALG is switched off for the pool - and without a
 * counter there is nothing to look at that says it is still happening.
 */
static_always_inline int
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
  return (dseq || (dack && (tcp->flags & TCP_FLAG_ACK))) ? 1 : 0;
}

#endif /* __included_bpa_seq_h__ */
