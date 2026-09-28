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

  printf ("\n%u checks, %d failures\n", checks, failures);
  printf (failures ? "%d FAILURES\n" : "ALL TESTS PASSED\n", failures);
  return failures ? 1 : 0;
}
