/*
 * bpa_gre.h - the enhanced GRE header PPTP uses, and nothing else.
 *
 * This is the layout knowledge on its own, with no dependency on VPP, on a
 * session table or on a buffer. It sits apart for one reason: two places in
 * this plugin read a PPTP Call ID out of a GRE header - the data path in
 * bpa_l4.h and the ICMP error path in bpa_icmp.h - and #199 was two copies of
 * one piece of arithmetic drifting apart in this same plugin, which
 * blackholed every translated packet on every appliance with workers.
 *
 * Splitting it out is also what lets test/test_bpa_icmp.c reach it: that test
 * compiles bpa_icmp.h against a shim with no VPP in it, and the refusals are
 * the half of that file a lab cannot observe - a packet that was rejected and
 * a packet that was read past both look like a drop.
 *
 * RFC 2637 section 4.1:
 *
 *   byte 0     C R K S s Recur    - K (0x20) MUST be set, C and R MUST be 0
 *   byte 1     A Flags Ver        - Ver MUST be 1
 *   bytes 2-3  Protocol Type      - 0x880B
 *   bytes 4-5  Key high: payload length
 *   bytes 6-7  Key low:  Call ID
 */

#ifndef __included_bpa_gre_h__
#define __included_bpa_gre_h__

#define BPA_GRE_FLAG_KEY    0x20
#define BPA_GRE_PROTO_PPP   0x880b
#define BPA_GRE_CALL_ID_OFF 6

/*
 * The whole header, which an ICMP error has to have quoted before the Call ID
 * at offset 6 can be read.
 *
 * RFC 792 requires an error to quote eight bytes of the original payload, so
 * the Call ID is inside that by exactly two bytes. It fits - and a sender
 * that quotes less is a packet this box refuses, not a read past the buffer.
 */
#define BPA_GRE_MIN_BYTES 8

/* Is this the enhanced GRE that PPTP uses? Anything else is not translatable
 * behind a shared address and the caller must say so in its own counter. */
static_always_inline int
bpa_gre_is_enhanced (const u8 *g)
{
  return (g[0] & BPA_GRE_FLAG_KEY) && (g[1] & 0x07) == 1
	 && clib_net_to_host_u16 (((const u16 *) g)[1]) == BPA_GRE_PROTO_PPP;
}

/* The Call ID, which identifies the call in BOTH directions - it is always
 * the receiver's, so which end sent the packet does not change where it is
 * read from, exactly as with an ICMP echo identifier. The caller has already
 * proved BPA_GRE_MIN_BYTES are present. */
static_always_inline u16
bpa_gre_call_id (const u8 *g)
{
  return clib_net_to_host_u16 (((const u16 *) g)[BPA_GRE_CALL_ID_OFF / 2]);
}

#endif /* __included_bpa_gre_h__ */
