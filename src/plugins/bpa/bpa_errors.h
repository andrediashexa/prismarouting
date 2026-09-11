/*
 * bpa_errors.h - the node counters, and which of them mean a dropped packet.
 *
 * VPP registers every node counter as an "error", so "show errors" lists
 * "packets translated" beside "no pool for source address" as though they were
 * the same kind of thing. During an incident they are not: one is the box
 * working and the other is a customer losing traffic.
 *
 * The second field says which. It lives in the same list as the string so the
 * two cannot drift - a separate table of names would be a second thing to keep
 * in step, and this project has already learned what that costs.
 */

#ifndef __included_bpa_errors_h__
#define __included_bpa_errors_h__

/* _ (symbol, is_a_drop, description) */
#define foreach_bpa_in2out_error \
  _ (TRANSLATED, 0, "packets translated") \
  _ (SESSION_NEW, 0, "sessions created") \
  _ (FORWARDED, 0, "sessions opened from a static port forward") \
  _ (MSS_CLAMPED, 0, "TCP MSS options lowered to fit the path") \
  _ (NO_POOL, 1, "no pool for source address") \
  _ (BYPASS_SRC, 0, "packets from a bypassed source, left untranslated") \
  _ (BYPASS_DST, 0, "packets to a bypassed destination, left untranslated") \
  _ (UNSUPPORTED, 1, "unsupported protocol") \
  _ (OVER_CAPACITY, 1, "subscriber beyond pool capacity") \
  _ (BLOCK_EXHAUSTED, 1, "port block exhausted") \
  _ (OVER_SESSIONS, 1, "subscriber at its concurrent session cap") \
  _ (OVER_RATE, 1, "subscriber opening sessions faster than allowed") \
  _ (NO_BLOCK, 1, "no free block in dynamic pool") \
  _ (TRUNCATED, 1, "truncated packet") \
  _ (HAIRPINNED, 0, "packets hairpinned to another subscriber") \
  _ (HAIRPIN_NO_PEER, 1, "hairpin to a public address with no session") \
  _ (FTP_REWRITTEN, 0, "FTP ALG payloads rewritten") \
  _ (FTP_UNTOUCHED, 0, "FTP payloads left untouched (malformed or unsupported)") \
  _ (FTP_NO_PORT, 0, "FTP data connection could not get a public port") \
  _ (FTP_SEQ, 0, "TCP sequence numbers shifted after an FTP rewrite") \
  _ (RTSP_REWRITTEN, 0, "RTSP Transport headers rewritten") \
  _ (RTSP_UNPARSED, 0, "RTSP Transport not understood") \
  _ (RTSP_PARTIAL, 0, "RTSP message spans segments") \
  _ (RTSP_NO_ROOM, 0, "RTSP rewrite would not fit") \
  _ (RTSP_NO_PORTS, 0, "RTSP media port pair unavailable") \
  _ (GRE_NOT_ENHANCED, 1, "GRE that is not the enhanced GRE of RFC 2637") \
  _ (GRE_NO_CALL, 1, "GRE for a call the control channel never announced") \
  _ (PPTP_MANGLED, 0, "PPTP control messages rewritten") \
  _ (PPTP_UNKNOWN, 0, "PPTP message about an unknown call") \
  _ (PPTP_NO_CALL_ID, 0, "PPTP call refused: no free entry in the block") \
  _ (PPTP_MALFORMED, 0, "malformed or split PPTP control message") \
  _ (ESP_BOUND, 0, "IPsec tunnels bound to a peer") \
  _ (ESP_OFF, 1, "ESP dropped: passthrough is off") \
  _ (ESP_PEER_TAKEN, 1, "ESP peer is another subscriber's")

#define foreach_bpa_out2in_error \
  _ (TRANSLATED, 0, "packets translated") \
  _ (NOT_OURS, 0, "destination outside public pools") \
  _ (NO_SESSION, 1, "no session for destination") \
  _ (FORWARDED, 0, "sessions opened from a static port forward") \
  _ (MSS_CLAMPED, 0, "TCP MSS options lowered to fit the path") \
  _ (FILTERED, 1, "dropped by address-dependent filtering") \
  _ (UNSUPPORTED, 1, "unsupported protocol") \
  _ (OUT_OF_BLOCK, 1, "port outside every block") \
  _ (BLOCK_FREE, 1, "block not allocated to any subscriber") \
  _ (ICMP_ERROR, 0, "ICMP errors translated") \
  _ (ICMP_MALFORMED, 1, "truncated or malformed ICMP error") \
  _ (TRUNCATED, 1, "truncated packet") \
  _ (FTP_ACK, 0, "TCP acknowledgements shifted after an FTP rewrite") \
  _ (RTSP_REWRITTEN, 0, "RTSP Transport replies rewritten") \
  _ (RTSP_UNPARSED, 0, "RTSP reply not understood") \
  _ (RTSP_PARTIAL, 0, "RTSP reply spans segments") \
  _ (RTSP_NO_ROOM, 0, "RTSP reply rewrite would not fit") \
  _ (GRE_NOT_ENHANCED, 1, "GRE that is not the enhanced GRE of RFC 2637") \
  _ (PPTP_MANGLED, 0, "PPTP control messages rewritten") \
  _ (PPTP_UNKNOWN, 0, "PPTP message about an unknown call") \
  _ (PPTP_MALFORMED, 0, "malformed or split PPTP control message") \
  _ (ESP_TRANSLATED, 0, "ESP passed to a subscriber") \
  _ (ESP_OFF, 1, "ESP dropped: passthrough is off") \
  _ (ESP_NO_TUNNEL, 1, "ESP from a peer with no tunnel")

#endif /* __included_bpa_errors_h__ */
