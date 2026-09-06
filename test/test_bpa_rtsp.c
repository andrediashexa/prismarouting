/*
 * test_bpa_rtsp.c - unit test for the RTSP Transport parser.
 *
 * The parser is the half of the ALG that decides whether to touch a payload at
 * all, so most of this file is about the cases where the answer is "no". An
 * ALG that rewrites a header it misread corrupts a connection that would
 * otherwise merely have failed to carry video, and the counter reports a
 * success either way - which is why the refusals are asserted one by one
 * rather than assumed.
 */

#include <stdio.h>
#include <string.h>
#include "bpa_shim.h"
#include "../src/plugins/bpa/bpa_rtsp.h"

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

static bpa_rtsp_verdict_t
run (const char *msg, int is_reply, bpa_rtsp_pair_t *cp)
{
  memset (cp, 0, sizeof (*cp));
  return bpa_rtsp_client_port ((const u8 *) msg, (u32) strlen (msg), is_reply,
			       cp);
}

/* Asserts the verdict and, for a match, that the offset really points at the
 * digits. An offset that is off by one produces a rewritten header that still
 * parses and names the wrong ports. */
static void
expect (const char *what, const char *msg, int is_reply,
	bpa_rtsp_verdict_t want, const char *want_text)
{
  bpa_rtsp_pair_t cp;
  bpa_rtsp_verdict_t got = run (msg, is_reply, &cp);

  CHECK (got == want, "%s: verdict %u, expected %u", what, got, want);
  if (got != BPA_RTSP_FOUND || want != BPA_RTSP_FOUND)
    return;

  CHECK (cp.len == strlen (want_text) && !memcmp (msg + cp.off, want_text,
						  cp.len),
	 "%s: matched \"%.*s\", expected \"%s\"", what, (int) cp.len,
	 msg + cp.off, want_text);
  CHECK (cp.hi == cp.lo + 1, "%s: %u-%u is not a consecutive pair", what,
	 cp.lo, cp.hi);
}

int
main (void)
{
  bpa_rtsp_pair_t cp;
  u8 out[BPA_RTSP_PAIR_MAX];
  u32 n;

  printf ("RTSP Transport parser\n");

  /* ---- the cases the ALG exists for ---- */

  expect ("a plain SETUP",
	  "SETUP rtsp://cam/track1 RTSP/1.0\r\n"
	  "CSeq: 3\r\n"
	  "Transport: RTP/AVP;unicast;client_port=6970-6971\r\n"
	  "\r\n",
	  0, BPA_RTSP_FOUND, "6970-6971");

  expect ("the 200 reply that echoes it back",
	  "RTSP/1.0 200 OK\r\n"
	  "CSeq: 3\r\n"
	  "Transport: RTP/AVP;unicast;client_port=1030-1031;"
	  "server_port=9000-9001\r\n"
	  "Session: 12345678\r\n"
	  "\r\n",
	  1, BPA_RTSP_FOUND, "1030-1031");

  /* Header names and parameter names are case-insensitive, and firmware
   * varies. Rejecting on case would turn the ALG off for a whole vendor. */
  expect ("case does not matter",
	  "SETUP rtsp://cam/track1 RTSP/1.0\r\n"
	  "TRANSPORT: RTP/AVP;UNICAST;Client_Port=6970-6971\r\n"
	  "\r\n",
	  0, BPA_RTSP_FOUND, "6970-6971");

  expect ("whitespace around the parameter",
	  "SETUP rtsp://cam/track1 RTSP/1.0\r\n"
	  "Transport: RTP/AVP;unicast; client_port = 6970-6971\r\n"
	  "\r\n",
	  0, BPA_RTSP_FOUND, "6970-6971");

  expect ("a header whose name only starts like Transport is skipped",
	  "SETUP rtsp://cam/track1 RTSP/1.0\r\n"
	  "Transport-Info: ;client_port=1-2\r\n"
	  "Transport: RTP/AVP;unicast;client_port=6970-6971\r\n"
	  "\r\n",
	  0, BPA_RTSP_FOUND, "6970-6971");

  /* An odd RTP port is against RFC 3550's recommendation but is what the
   * client asked for on ITS side; the pair we hand out is the one that has to
   * be even. Refusing here would disable the ALG for those clients. */
  expect ("an odd client base is still a pair",
	  "SETUP rtsp://cam/track1 RTSP/1.0\r\n"
	  "Transport: RTP/AVP;unicast;client_port=6971-6972\r\n"
	  "\r\n",
	  0, BPA_RTSP_FOUND, "6971-6972");

  expect ("a trailing parameter after the range",
	  "SETUP rtsp://cam/track1 RTSP/1.0\r\n"
	  "Transport: RTP/AVP;unicast;client_port=6970-6971;mode=\"PLAY\"\r\n"
	  "\r\n",
	  0, BPA_RTSP_FOUND, "6970-6971");

  /* ---- nothing to do, and nothing wrong ---- */

  expect ("a request that is not SETUP",
	  "OPTIONS rtsp://cam RTSP/1.0\r\nCSeq: 1\r\n\r\n", 0, BPA_RTSP_NONE,
	  "");
  expect ("a reply that is not 200",
	  "RTSP/1.0 454 Session Not Found\r\nCSeq: 3\r\n\r\n", 1,
	  BPA_RTSP_NONE, "");
  expect ("media interleaved on the control connection",
	  "SETUP rtsp://cam/track1 RTSP/1.0\r\n"
	  "Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n"
	  "\r\n",
	  0, BPA_RTSP_NONE, "");
  expect ("a SETUP with no Transport header at all",
	  "SETUP rtsp://cam/track1 RTSP/1.0\r\nCSeq: 3\r\n\r\n", 0,
	  BPA_RTSP_NONE, "");
  expect ("a reply read as a request", "RTSP/1.0 200 OK\r\n\r\n", 0,
	  BPA_RTSP_NONE, "");

  /* ---- refusals: it is ours and we did not understand it ---- */

  expect ("a single port with no range",
	  "SETUP rtsp://cam/track1 RTSP/1.0\r\n"
	  "Transport: RTP/AVP;unicast;client_port=6970\r\n"
	  "\r\n",
	  0, BPA_RTSP_UNPARSED, "");
  expect ("a range that is not two consecutive ports",
	  "SETUP rtsp://cam/track1 RTSP/1.0\r\n"
	  "Transport: RTP/AVP;unicast;client_port=6970-6975\r\n"
	  "\r\n",
	  0, BPA_RTSP_UNPARSED, "");
  expect ("a range that runs backwards",
	  "SETUP rtsp://cam/track1 RTSP/1.0\r\n"
	  "Transport: RTP/AVP;unicast;client_port=6971-6970\r\n"
	  "\r\n",
	  0, BPA_RTSP_UNPARSED, "");
  expect ("a port that is not a number",
	  "SETUP rtsp://cam/track1 RTSP/1.0\r\n"
	  "Transport: RTP/AVP;unicast;client_port=abcd-abce\r\n"
	  "\r\n",
	  0, BPA_RTSP_UNPARSED, "");
  expect ("a port beyond 65535",
	  "SETUP rtsp://cam/track1 RTSP/1.0\r\n"
	  "Transport: RTP/AVP;unicast;client_port=99999-100000\r\n"
	  "\r\n",
	  0, BPA_RTSP_UNPARSED, "");
  expect ("port zero",
	  "SETUP rtsp://cam/track1 RTSP/1.0\r\n"
	  "Transport: RTP/AVP;unicast;client_port=0-1\r\n"
	  "\r\n",
	  0, BPA_RTSP_UNPARSED, "");
  expect ("the parameter with no value",
	  "SETUP rtsp://cam/track1 RTSP/1.0\r\n"
	  "Transport: RTP/AVP;unicast;client_port;mode=\"PLAY\"\r\n"
	  "\r\n",
	  0, BPA_RTSP_UNPARSED, "");
  /* Legal RFC 2326 header folding, essentially unseen in the wild and not
   * handled. It has to refuse, not silently read half the value. */
  expect ("a folded Transport header",
	  "SETUP rtsp://cam/track1 RTSP/1.0\r\n"
	  "Transport: RTP/AVP;unicast;\r\n"
	  " client_port=6970-6971\r\n"
	  "\r\n",
	  0, BPA_RTSP_UNPARSED, "");

  /* ---- the message does not end here ---- */

  /*
   * A SETUP split across TCP segments. Without demanding the blank line, "no
   * Transport header in this packet" and "the Transport header has not
   * arrived yet" are the same observation, and the second one would be
   * answered by letting the SETUP through with the subscriber's private ports
   * in it.
   */
  expect ("a header block that does not end in this segment",
	  "SETUP rtsp://cam/track1 RTSP/1.0\r\n"
	  "CSeq: 3\r\n"
	  "Transport: RTP/AVP;unicast;client_p",
	  0, BPA_RTSP_PARTIAL, "");
  expect ("a complete-looking one still missing the blank line",
	  "SETUP rtsp://cam/track1 RTSP/1.0\r\n"
	  "Transport: RTP/AVP;unicast;client_port=6970-6971\r\n",
	  0, BPA_RTSP_PARTIAL, "");

  /* ---- truncation must never read past the buffer ---- */

  /* Every prefix of a real SETUP, to prove no length check is missing. The
   * assertion is that this terminates without a sanitiser or a crash; the
   * verdict for a prefix is not interesting. */
  {
    const char *full = "SETUP rtsp://cam/track1 RTSP/1.0\r\n"
		       "Transport: RTP/AVP;unicast;client_port=6970-6971\r\n"
		       "\r\n";
    u32 i, len = (u32) strlen (full);
    u32 found = 0;

    for (i = 0; i <= len; i++)
      {
	bpa_rtsp_pair_t c;
	if (bpa_rtsp_client_port ((const u8 *) full, i, 0, &c)
	    == BPA_RTSP_FOUND)
	  found++;
      }
    CHECK (found == 1, "only the complete message may match, %u prefixes did",
	   found);
  }

  /* ---- rendering the replacement ---- */

  n = bpa_rtsp_format_pair (out, 1030, 1031);
  CHECK (n == 9 && !memcmp (out, "1030-1031", 9),
	 "format_pair(1030,1031) gave \"%.*s\"", (int) n, out);
  n = bpa_rtsp_format_pair (out, 2, 3);
  CHECK (n == 3 && !memcmp (out, "2-3", 3), "format_pair(2,3) gave \"%.*s\"",
	 (int) n, out);
  n = bpa_rtsp_format_pair (out, 65534, 65535);
  CHECK (n == 11 && !memcmp (out, "65534-65535", 11),
	 "format_pair(65534,65535) gave \"%.*s\"", (int) n, out);
  CHECK (n <= BPA_RTSP_PAIR_MAX, "the widest pair overflows the buffer bound");

  /* What the parser finds must be exactly what the renderer can replace: the
   * splice copies new_len bytes over cp.len bytes and moves the rest. */
  {
    const char *msg = "SETUP rtsp://cam/t RTSP/1.0\r\n"
		      "Transport: RTP/AVP;unicast;client_port=6970-6971\r\n"
		      "\r\n";
    CHECK (run (msg, 0, &cp) == BPA_RTSP_FOUND, "round trip: no match");
    n = bpa_rtsp_format_pair (out, cp.lo, cp.hi);
    CHECK (n == cp.len && !memcmp (msg + cp.off, out, n),
	   "re-rendering what was parsed did not reproduce it");
  }

  printf ("%u checks, %d failures\n", checks, failures);
  return failures ? 1 : 0;
}
