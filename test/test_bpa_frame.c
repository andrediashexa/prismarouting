/*
 * test_bpa_frame.c - the length of the message at the front of an exporter's
 * buffer, on both wires (bpa_frame.h, #492).
 *
 * THE CASE THAT BROKE: a syslog frame read as IPFIX. "211 <134>1..." has
 * bytes 2-3 "1 ", which the old code read as a 12576-byte message; the
 * exporter deleted that many bytes and the stream began mid-message.
 *
 * Mutation shown red: bpa_frame_len() ignoring is_syslog and reading bytes
 * 2-3 on both wires, which is the code #492 replaced.
 */
#include <stdio.h>
#include <string.h>
#include "bpa_shim.h"
#include "../src/plugins/bpa/bpa_frame.h"

static int failures = 0;
static unsigned checks = 0;

#define CHECK(cond, ...)                                                      \
  do                                                                          \
    {                                                                         \
      checks++;                                                               \
      if (!(cond))                                                            \
	{                                                                     \
	  printf ("  FAIL: ");                                               \
	  printf (__VA_ARGS__);                                               \
	  printf ("\n");                                                      \
	  failures++;                                                         \
	}                                                                     \
    }                                                                         \
  while (0)

static u32
syslog_len (const char *s)
{
  return bpa_frame_len (1, (const u8 *) s, (u32) strlen (s));
}

int
main (void)
{
  printf ("=== a syslog frame is its decimal prefix, a space and that many "
	  "bytes ===\n");
  {
    const char *boot = "211 <134>1 2026-09-28T20:37:01.901Z prisma-bench "
		       "prisma-cgnat - BOOT-MARKER [cgnat@32473 ...";

    CHECK (syslog_len (boot) == 3 + 1 + 211,
	   "the bench's boot marker is %u bytes, not 215", syslog_len (boot));
    CHECK (syslog_len ("5 hello6 world!") == 7,
	   "two frames back to back: the first is %u, not 7",
	   syslog_len ("5 hello6 world!"));
    CHECK (syslog_len ("1234567 x") == 0,
	   "seven digits were taken for a length");
    CHECK (syslog_len ("34Z\" observedAt=") == 0,
	   "the middle of a message was read as a frame");
    CHECK (syslog_len ("211") == 0 && syslog_len ("211<") == 0,
	   "a prefix with no space after it was read as a frame");
    CHECK (syslog_len ("0 ") == 0, "a zero-length frame was accepted");
    CHECK (syslog_len ("") == 0, "an empty buffer had a frame");
    printf ("  the length and its prefix; a mid-message front is not a "
	    "frame\n");
  }

  printf ("\n=== the case that broke: a syslog frame is not an IPFIX "
	  "header ===\n");
  {
    const u8 *b = (const u8 *) "211 <134>1 2026";

    CHECK (bpa_frame_len (0, b, 15) == 12576,
	   "read as IPFIX, bytes 2-3 are not the 12576 the bench measured "
	   "(%u) - this test no longer describes the bug", bpa_frame_len (0, b, 15));
    CHECK (bpa_frame_len (1, b, 15) == 215,
	   "read as syslog, the frame is %u bytes, not 215",
	   bpa_frame_len (1, b, 15));
    printf ("  the same bytes: 12576 as IPFIX, 215 as syslog\n");
  }

  printf ("\n=== an IPFIX message is its header's length ===\n");
  {
    u8 h[16] = { 0x00, 0x0a, 0x00, 0x34 };

    CHECK (bpa_frame_len (0, h, 16) == 52, "an IPFIX header of length 52 read "
	   "as %u", bpa_frame_len (0, h, 16));
    CHECK (bpa_frame_len (0, h, 16) == 52 && bpa_frame_len (0, h, 4) == 52,
	   "the length needs the whole message present");
    h[3] = 8;
    CHECK (bpa_frame_len (0, h, 16) == 0,
	   "a length shorter than the header was accepted");
    CHECK (bpa_frame_len (0, h, 3) == 0, "three bytes had a length");
    printf ("  bytes 2-3, at least the 16-byte header\n");
  }

  printf ("\n=== a front that does not frame is skipped to the next message "
	  "(#547) ===\n");
  {
    /* The #475 bench's spill, as written by 0.140.2 and replayed by every
     * release since: the tail of a boot marker, then whole lines. */
    const char *spill = "otTime=\"2026-09-28T18:19:18.534Z\" observedAt=x]"
			" up\n" "12 <134>1 a b c" "9 <134>1 xyz";
    u32 len = (u32) strlen (spill), at = (u32) (strstr (spill, "12 <") - spill);
    const char *chance = "40 ok 12 <134>1 a b c";
    u8 ip[40] = { 0x41, 0x41, 0x41, 0x00, 0x0a, 0x00, 0x14 };

    CHECK (bpa_frame_resync (1, (const u8 *) spill, len) == at,
	   "the fragment is skipped to the first whole line: %u, not %u",
	   bpa_frame_resync (1, (const u8 *) spill, len), at);
    CHECK (bpa_frame_resync (1, (const u8 *) spill + at, len - at) == 0,
	   "a front that frames is kept where it is");
    CHECK (bpa_frame_resync (1, (const u8 *) chance, (u32) strlen (chance))
	     == 6,
	   "two digits and a space mid-line are not a frame unless a \"<\" "
	   "follows: %u, not 6",
	   bpa_frame_resync (1, (const u8 *) chance, (u32) strlen (chance)));
    CHECK (bpa_frame_resync (1, (const u8 *) "no frame at all", 15) == 15,
	   "nothing that frames resyncs to the end");
    /* IPFIX: three stray bytes, then a 20-byte message and another. */
    ip[3 + 20 + 1] = 0x0a;
    ip[3 + 20 + 3] = 0x10;
    CHECK (bpa_frame_resync (0, ip, 3 + 20 + 16) == 3,
	   "an IPFIX stream resyncs on version 10 and a length: %u, not 3",
	   bpa_frame_resync (0, ip, 3 + 20 + 16));
    CHECK (!bpa_frame_plausible (0, ip, 39) && bpa_frame_plausible (0, ip + 3, 36),
	   "bpa_frame_plausible: 0x4141 is no IPFIX version, 10 is");
    CHECK (bpa_frame_plausible (1, (const u8 *) "12 <134>1 a b c1", 16),
	   "a whole line followed by a header too short to read is plausible");
    printf ("  the bench's fragment skipped; a frame needs \"<\" or version "
	    "10, and a frame after it\n");
  }

  printf ("\n%u checks, %d failures\n", checks, failures);
  printf (failures ? "%d FAILURES\n" : "ALL TESTS PASSED\n", failures);
  return failures ? 1 : 0;
}
