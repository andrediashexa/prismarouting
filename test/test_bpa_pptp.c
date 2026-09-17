/*
 * test_bpa_pptp.c - the three functions of the PPTP ALG that read only a TCP
 * segment.
 *
 * PPTP is rewritten in place: a Call ID is a 16-bit field inside the control
 * connection's payload, and replacing it changes no length, so no sequence
 * number moves. What that leaves is a checksum correction and a bounds check,
 * and both fail silently.
 *
 *   - a wrong checksum correction makes the peer drop the segment. The
 *     control connection then stalls with the Call ID rewritten exactly as
 *     the counter says it was, which is the failure mode issue #63 shipped
 *     in the outbound rewrite and this file exists to keep out of the ALG.
 *   - a payload bound read one byte wide has the parser looking at whatever
 *     the driver left in the buffer behind an Ethernet frame padded to its
 *     60-byte minimum. A lab cannot see it: the message parses or it does
 *     not, and either way nothing is printed.
 *   - the worst-outcome fold decides what the caller is told about a segment
 *     carrying several messages. Folded the other way, one message that was
 *     fine hides a malformed one beside it.
 *
 * The checksum here is compared against a FULL recomputation over the
 * segment, written independently below, never against the incremental
 * arithmetic that produced it.
 */

#include <stdio.h>
#include <string.h>

#include "bpa_tcp_shim.h"
/*
 * INCLUDED rather than linked: all three are static (two of them
 * static_always_inline), so nothing compiled beside the file can call one.
 * bpa_pptp.c carries the BPA_STANDALONE guards that leave the message
 * handlers, which need a session pool and a bihash, out of this build.
 */
#include "../src/plugins/bpa/bpa_pptp.c"

static int failures = 0;
static unsigned checks = 0;

#define CHECK(cond, ...)                                                      \
  do                                                                          \
    {                                                                         \
      checks++;                                                               \
      if (!(cond))                                                            \
	{                                                                     \
	  printf ("  FAIL: ");                                                \
	  printf (__VA_ARGS__);                                               \
	  printf ("\n");                                                      \
	  failures++;                                                         \
	}                                                                     \
    }                                                                         \
  while (0)

/*
 * The checksum a receiver computes, over the TCP header and the payload
 * behind it, with the checksum field itself taken as zero. No pseudo-header:
 * pptp_set_u16() corrects only for the bytes it changed, and those are all
 * inside this span, so a correction that is right leaves this value
 * unchanged.
 */
static u16
full_checksum (const u8 *p, unsigned len, unsigned csum_off)
{
  uint32_t sum = 0;
  unsigned i;

  for (i = 0; i + 1 < len; i += 2)
    {
      if (i == csum_off)
	continue;
      sum += (uint32_t) ((p[i] << 8) | p[i + 1]);
    }
  if (len & 1)
    sum += (uint32_t) (p[len - 1] << 8);
  while (sum >> 16)
    sum = (sum & 0xffff) + (sum >> 16);
  return (u16) ~sum;
}

/* A segment with `plen` bytes of payload, the TCP checksum already right. */
static tcp_header_t *
segment (vlib_buffer_t *b, u32 plen, u8 data_offset_words)
{
  tcp_header_t *tcp = (tcp_header_t *) vlib_buffer_get_current (b);

  memset (b->data, 0, sizeof (b->data));
  b->current_data = 0;
  b->flags = 0;
  b->current_length = (u16) (data_offset_words * 4 + plen);
  tcp->src_port = clib_host_to_net_u16 (1723);
  tcp->dst_port = clib_host_to_net_u16 (49152);
  tcp->data_offset_and_reserved = (u8) (data_offset_words << 4);
  tcp->checksum = clib_host_to_net_u16 (
    full_checksum ((const u8 *) tcp, b->current_length,
		   (unsigned) ((u8 *) &tcp->checksum - (u8 *) tcp)));
  return tcp;
}

int
main (void)
{
  vlib_buffer_t b;

  printf ("=== PPTP ALG: what one segment alone decides ===\n");

  printf ("\n--- the worst outcome of a segment wins ---\n");
  /*
   * The values are bpa_pptp.h's enumerators in order - NOTHING, MANGLED,
   * UNKNOWN, NO_CALL_ID, MALFORMED - spelled as numbers because that header
   * needs VPP. Taking the larger is what keeps a malformed message from being
   * reported as a segment that was fine, whichever order the two arrive in.
   */
  CHECK (worse (0, 4) == 4 && worse (4, 0) == 4 && worse (1, 3) == 3
	   && worse (2, 2) == 2,
	 "worse() takes the more serious of two outcomes: got %d %d %d %d",
	 worse (0, 4), worse (4, 0), worse (1, 3), worse (2, 2));

  printf ("\n--- a Call ID replaced, checksum and all ---\n");
  {
    tcp_header_t *tcp = segment (&b, 16, 5);
    u16 *cid = (u16 *) ((u8 *) tcp + 20 + 12);
    u16 after_first, after_repeat;

    cid[0] = clib_host_to_net_u16 (1);
    tcp->checksum = clib_host_to_net_u16 (
      full_checksum ((const u8 *) tcp, b.current_length, 16));

    pptp_set_u16 (tcp, cid, 40000);
    after_first = tcp->checksum;
    /* The same value again: a retransmit of a message already rewritten must
     * not take a second correction, which would leave the segment checksumming
     * wrong by exactly twice the change. */
    pptp_set_u16 (tcp, cid, 40000);
    after_repeat = tcp->checksum;

    CHECK (clib_net_to_host_u16 (cid[0]) == 40000
	     && clib_net_to_host_u16 (after_first)
		  == full_checksum ((const u8 *) tcp, b.current_length, 16)
	     && after_repeat == after_first,
	   "pptp_set_u16 writes the new Call ID and the segment still "
	   "checksums, once and on a repeat: cid=%u csum=%04x/%04x wanted "
	   "%04x",
	   clib_net_to_host_u16 (cid[0]), clib_net_to_host_u16 (after_first),
	   clib_net_to_host_u16 (after_repeat),
	   full_checksum ((const u8 *) tcp, b.current_length, 16));
  }

  printf ("\n--- where the payload ends ---\n");
  {
    tcp_header_t *tcp;
    u32 len_opts = 0, len_empty = 99, len_short = 99;
    u8 *with_options, *empty, *short_header;

    /* Options present: the payload starts after the real header length, not
     * after twenty bytes. Then a segment with no payload at all, and one
     * whose data offset claims a header shorter than TCP's own - each leaves
     * nothing to parse, and parsing it anyway means reading the padding a
     * driver left in the buffer. */
    tcp = segment (&b, 16, 6);
    with_options = pptp_payload (&b, tcp, &len_opts);

    tcp = segment (&b, 0, 5);
    empty = pptp_payload (&b, tcp, &len_empty);

    tcp = segment (&b, 16, 5);
    tcp->data_offset_and_reserved = 4 << 4;
    short_header = pptp_payload (&b, tcp, &len_short);

    CHECK (with_options == (u8 *) vlib_buffer_get_current (&b) + 24
	     && len_opts == 16 && empty == 0 && short_header == 0,
	   "pptp_payload spans what the header leaves: offset=%ld len=%u, "
	   "empty=%p, short header=%p",
	   with_options
	     ? (long) (with_options - (u8 *) vlib_buffer_get_current (&b))
	     : -1L,
	   len_opts, (void *) empty, (void *) short_header);
  }

  return bpa_test_verdict (checks, failures);
}
