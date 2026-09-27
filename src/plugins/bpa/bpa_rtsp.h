/*
 * bpa_rtsp.h - RTSP Transport header parsing for the ALG (RFC 2326 s12.39).
 *
 * Pure byte arithmetic over a TCP payload, deliberately free of VPP types so
 * it builds either inside the plugin or standalone against the test shim.
 * The parser is the part that MUST NOT guess: a payload rewritten from a
 * misparse corrupts a connection that would otherwise merely have failed to
 * carry video, which is the worse of the two outcomes. A parser that can only
 * be exercised with a running VPP does not get the number of cases it needs,
 * so this one is reachable from `make test`.
 *
 * What it looks for, and nothing else:
 *
 *   SETUP rtsp://cam/track1 RTSP/1.0\r\n
 *   CSeq: 3\r\n
 *   Transport: RTP/AVP;unicast;client_port=6970-6971\r\n
 *   \r\n
 *
 * Everything it does not fully understand comes back as a verdict the caller
 * turns into a counter, never into a rewrite.
 */

#ifndef __included_bpa_rtsp_h__
#define __included_bpa_rtsp_h__

#ifdef BPA_STANDALONE
#include "bpa_shim.h"
#else
#include <vlib/vlib.h>
#endif

/* The IANA port for RTSP. Cameras and NVRs are also found on 8554 and other
 * ad-hoc ports, and those are deliberately NOT matched: the only thing that
 * says "this TCP stream is RTSP" without guessing is the well-known port, and
 * sniffing for one would put the parser in front of traffic that merely looks
 * like it. An operator who needs another port needs a knob, not a heuristic. */
#define BPA_RTSP_PORT 554

/* "<lo>-<hi>" for two 16-bit ports: 5 + 1 + 5. */
#define BPA_RTSP_PAIR_MAX 11

typedef enum
{
  /* Nothing here to rewrite, and nothing wrong: not an RTSP message we act
   * on, or a SETUP with no client_port (interleaved or multicast media). */
  BPA_RTSP_NONE = 0,
  BPA_RTSP_FOUND,    /* a well-formed consecutive client_port pair */
  BPA_RTSP_UNPARSED, /* it is ours and we did not understand it */
  BPA_RTSP_PARTIAL,  /* the header block does not end inside this segment */
} bpa_rtsp_verdict_t;

typedef struct
{
  u32 off; /* offset of the first digit, within the payload */
  u32 len; /* bytes covered by "<lo>-<hi>" */
  u16 lo;
  u16 hi;
} bpa_rtsp_pair_t;

/* Case-insensitive compare of n bytes against a literal. RTSP method names
 * are case-sensitive but header names and transport parameters are not, and
 * real clients vary. */
static_always_inline int
bpa_rtsp_ci (const u8 *p, const char *lit, u32 n)
{
  u32 i;

  for (i = 0; i < n; i++)
    {
      u8 a = p[i], c = (u8) lit[i];

      if (a >= 'A' && a <= 'Z')
	a = (u8) (a + 32);
      if (c >= 'A' && c <= 'Z')
	c = (u8) (c + 32);
      if (a != c)
	return 0;
    }
  return 1;
}

static_always_inline int
bpa_rtsp_is_ws (u8 c)
{
  return c == ' ' || c == '\t';
}

/*
 * Finds the client_port pair in an RTSP SETUP request (is_reply == 0) or in
 * the reply to one (is_reply == 1).
 *
 * Only SETUP carries client_port, and only a 200 reply carries one we can
 * trust, so anything else is BPA_RTSP_NONE rather than a counted anomaly:
 * having nothing to do is not a failure.
 */
static_always_inline bpa_rtsp_verdict_t
bpa_rtsp_client_port (const u8 *pl, u32 len, int is_reply,
		      bpa_rtsp_pair_t *cp)
{
  u32 i, hdr_end = 0;
  int terminated = 0;

  if (is_reply)
    {
      /* "RTSP/1.0 200 " - the version digits are not pinned to 1.0 because
       * RTSP 2.0 (RFC 7826) keeps the same Transport syntax. */
      if (len < 12 || !bpa_rtsp_ci (pl, "RTSP/", 5))
	return BPA_RTSP_NONE;
      if (pl[5] < '0' || pl[5] > '9' || pl[6] != '.' || pl[7] < '0'
	  || pl[7] > '9' || pl[8] != ' ')
	return BPA_RTSP_NONE;
      if (pl[9] != '2' || pl[10] != '0' || pl[11] != '0')
	return BPA_RTSP_NONE;
    }
  else
    {
      if (len < 6 || !bpa_rtsp_ci (pl, "SETUP ", 6))
	return BPA_RTSP_NONE;
    }

  /*
   * The header block has to END in this segment. Without that, "no Transport
   * header here" and "the Transport header has not arrived yet" are the same
   * observation, and the second one must not be answered by letting the
   * packet through unrewritten and forgetting about it.
   */
  for (i = 0; i + 3 < len; i++)
    if (pl[i] == '\r' && pl[i + 1] == '\n' && pl[i + 2] == '\r'
	&& pl[i + 3] == '\n')
      {
	hdr_end = i + 2; /* the CRLF closing the last header line */
	terminated = 1;
	break;
      }
  if (!terminated)
    return BPA_RTSP_PARTIAL;

  /* Transport is never the first line - a request or status line precedes it -
   * so it is always introduced by a CRLF. */
  for (i = 0; i + 11 < hdr_end; i++)
    {
      u32 j, k, vstart, lend;

      if (pl[i] != '\r' || pl[i + 1] != '\n')
	continue;
      j = i + 2;
      if (!bpa_rtsp_ci (pl + j, "transport", 9))
	continue;
      j += 9;
      while (j < hdr_end && bpa_rtsp_is_ws (pl[j]))
	j++;
      if (j >= hdr_end || pl[j] != ':')
	continue; /* "Transport-Foo:", not ours */
      vstart = j + 1;

      for (lend = vstart; lend + 1 < len; lend++)
	if (pl[lend] == '\r' && pl[lend + 1] == '\n')
	  break;

      /*
       * A folded continuation line (RFC 2326 inherits HTTP's LWS folding).
       * Legal, essentially unseen in the wild, and not handled: the value
       * would continue past where this loop stops looking, so refusing is the
       * only answer that cannot corrupt the header.
       */
      if (lend + 2 < len && bpa_rtsp_is_ws (pl[lend + 2]))
	return BPA_RTSP_UNPARSED;

      for (k = vstart; k + 11 < lend; k++)
	{
	  u32 m, d0, nd;
	  u32 v;
	  u16 lo, hi;

	  if (pl[k] != ';')
	    continue;
	  m = k + 1;
	  while (m < lend && bpa_rtsp_is_ws (pl[m]))
	    m++;
	  if (m + 11 > lend || !bpa_rtsp_ci (pl + m, "client_port", 11))
	    continue;
	  m += 11;
	  while (m < lend && bpa_rtsp_is_ws (pl[m]))
	    m++;
	  if (m >= lend || pl[m] != '=')
	    return BPA_RTSP_UNPARSED;
	  m++;
	  while (m < lend && bpa_rtsp_is_ws (pl[m]))
	    m++;

	  d0 = m;
	  for (v = 0, nd = 0; m < lend && pl[m] >= '0' && pl[m] <= '9'; m++)
	    {
	      v = v * 10 + (u32) (pl[m] - '0');
	      if (v > 65535)
		return BPA_RTSP_UNPARSED;
	      nd++;
	    }
	  if (nd == 0 || v == 0)
	    return BPA_RTSP_UNPARSED;
	  lo = (u16) v;

	  /* A single port with no range is legal RTSP and cannot be served:
	   * RTCP would have nowhere to go. Refused rather than guessed at. */
	  if (m >= lend || pl[m] != '-')
	    return BPA_RTSP_UNPARSED;
	  m++;

	  for (v = 0, nd = 0; m < lend && pl[m] >= '0' && pl[m] <= '9'; m++)
	    {
	      v = v * 10 + (u32) (pl[m] - '0');
	      if (v > 65535)
		return BPA_RTSP_UNPARSED;
	      nd++;
	    }
	  if (nd == 0)
	    return BPA_RTSP_UNPARSED;
	  hi = (u16) v;

	  /* Something other than a parameter separator after the range means
	   * we read part of a value whose shape we do not know. */
	  if (m < lend && pl[m] != ';' && pl[m] != ',')
	    return BPA_RTSP_UNPARSED;

	  /* RTP and RTCP are a pair. A range that is not two consecutive
	   * ports is not the thing this ALG knows how to map. */
	  if (hi != lo + 1)
	    return BPA_RTSP_UNPARSED;

	  cp->off = d0;
	  cp->len = m - d0;
	  cp->lo = lo;
	  cp->hi = hi;
	  return BPA_RTSP_FOUND;
	}

      /* A Transport header with no client_port: RTP interleaved on the
       * control connection, or a multicast destination. Nothing to do. */
      return BPA_RTSP_NONE;
    }

  return BPA_RTSP_NONE;
}

/* Writes "<lo>-<hi>" and returns its length. At most BPA_RTSP_PAIR_MAX. */
static_always_inline u32
bpa_rtsp_format_pair (u8 *out, u16 lo, u16 hi)
{
  u32 n = 0, i, k;
  u8 tmp[5];
  u16 v;

  for (k = 0; k < 2; k++)
    {
      u32 d = 0;

      v = k ? hi : lo;
      do
	{
	  tmp[d++] = (u8) ('0' + v % 10);
	  v = (u16) (v / 10);
	}
      while (v);
      for (i = 0; i < d; i++)
	out[n++] = tmp[d - 1 - i];
      if (k == 0)
	out[n++] = '-';
    }
  return n;
}

/*
 * Are the two media mappings found for a reply's client_port still this
 * control connection's pair? Both must belong to its subscriber, and they
 * must still be consecutive on the private side: restoring one port of a
 * pair, or guessing the other, is worse than not rewriting at all.
 */
static_always_inline int
bpa_rtsp_pair_is_ours (u32 sub, u32 sub0, u16 port0, u32 sub1, u16 port1)
{
  return sub0 == sub && sub1 == sub && (u32) port1 == (u32) port0 + 1;
}

#endif /* __included_bpa_rtsp_h__ */
