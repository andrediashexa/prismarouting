/*
 * The accounting packet, and the two ways it can be wrong (#21).
 *
 * THE PACKETS ARE BUILT BY THIS FILE AND SIGNED WITH THE SAME MD5 THE PARSER
 * CHECKS, which would be a round trip and worth nothing on its own - so the
 * assertions are about what the parser DECIDES rather than about the digest:
 * a packet whose authenticator is one byte wrong is refused as BAD_AUTH and
 * not as malformed, an attribute that runs past the packet ends the parse, a
 * User-Name longer than the field is truncated and says so, and a request
 * with no Acct-Status-Type is not an accounting request at all. The digest
 * itself is checked against RFC 1321's vectors in test_bpa_md5.c, which is
 * where a round trip would have been caught.
 */
#include <stdio.h>
#include <string.h>

#include "bpa_shim.h"
#include "bpa_md5.c"
#include "bpa_radius.c"

static int fail;

#define CHECK(cond, ...)                                                      \
  do                                                                          \
    {                                                                         \
      if (cond)                                                               \
	printf ("  [ ok ] " __VA_ARGS__), printf ("\n");                      \
      else                                                                    \
	fail++, printf ("  FAIL: " __VA_ARGS__), printf ("\n");               \
    }                                                                         \
  while (0)

#define SECRET "s3cr3t-shared-with-the-bng"

/* An Accounting-Request with the attributes given, signed the way a NAS
 * signs one. */
static u32
build (u8 *pkt, u8 id, const u8 *attrs, u32 alen, const char *secret)
{
  u8 buf[512];
  u32 len = 20 + alen, slen = (u32) strlen (secret);

  pkt[0] = 4;
  pkt[1] = id;
  pkt[2] = (u8) (len >> 8);
  pkt[3] = (u8) len;
  memset (pkt + 4, 0, 16);
  memcpy (pkt + 20, attrs, alen);
  memcpy (buf, pkt, len);
  memcpy (buf + len, secret, slen);
  bpa_md5 (buf, len + slen, pkt + 4);
  return len;
}

int
main (void)
{
  u8 pkt[512], resp[64];
  bpa_radius_acct_t a;
  u32 len;

  printf ("=== an Accounting-Request, and what this box takes from it ===\n");

  printf ("\n--- a Start that names a subscriber and an address ---\n");
  {
    u8 attrs[] = {
      /* The length field of a RADIUS attribute counts its own two header
       * bytes: 8 characters of name make 10. */
      1, 10, 'j', 'o', 'a', 'o', '@', 'i', 's', 'p', /* User-Name */
      8, 6, 100, 64, 0, 5,                          /* Framed-IP-Address */
      40, 6, 0, 0, 0, BPA_ACCT_START,               /* Acct-Status-Type */
    };
    len = build (pkt, 42, attrs, sizeof (attrs), SECRET);
    CHECK (bpa_radius_acct_parse (pkt, len, SECRET, &a) == BPA_RADIUS_OK
	     && a.status == BPA_ACCT_START && a.have_ip
	     && a.framed_ip == ((100u << 24) | (64u << 16) | 5u)
	     && strcmp (a.user, "joao@isp") == 0 && a.id == 42,
	   "status=%u ip=%08x user=%s id=%u", a.status, a.framed_ip, a.user,
	   a.id);
  }

  printf ("\n--- and the response a NAS is waiting for ---\n");
  {
    u32 n = bpa_radius_acct_response (resp, sizeof (resp), &a, SECRET);
    u8 want[16], buf[128];

    /* Recomputed here the way RFC 2866 says, from the REQUEST's
     * authenticator: a response signed over its own zeros would be accepted
     * by nothing. */
    memcpy (buf, resp, 20);
    memcpy (buf + 4, a.request_authenticator, 16);
    memcpy (buf + 20, SECRET, strlen (SECRET));
    bpa_md5 (buf, 20 + (u32) strlen (SECRET), want);
    CHECK (n == 20 && resp[0] == 5 && resp[1] == 42 && resp[3] == 20
	     && memcmp (resp + 4, want, 16) == 0,
	   "Accounting-Response, 20 bytes, signed over the request's "
	   "authenticator");
    CHECK (bpa_radius_acct_response (resp, 8, &a, SECRET) == 0,
	   "and a buffer too small writes nothing rather than half a reply");
  }

  printf ("\n--- a packet signed with another secret is NOT malformed ---\n");
  {
    u8 attrs[] = { 40, 6, 0, 0, 0, BPA_ACCT_STOP };

    len = build (pkt, 1, attrs, sizeof (attrs), "somebody-elses-secret");
    CHECK (bpa_radius_acct_parse (pkt, len, SECRET, &a) == BPA_RADIUS_BAD_AUTH,
	   "it is BAD_AUTH: somebody else's box, or this one's secret being "
	   "wrong, and the two need different counters from a broken client");
  }

  printf ("\n--- what is not an accounting request at all ---\n");
  {
    u8 attrs[] = { 40, 6, 0, 0, 0, BPA_ACCT_START };

    len = build (pkt, 1, attrs, sizeof (attrs), SECRET);
    pkt[0] = 1; /* Access-Request: somebody else's traffic on this port */
    CHECK (bpa_radius_acct_parse (pkt, len, SECRET, &a) == BPA_RADIUS_MALFORMED,
	   "a code this box does not serve is not read");

    len = build (pkt, 1, attrs, sizeof (attrs), SECRET);
    pkt[3] = (u8) (len + 4); /* a length that lies */
    CHECK (bpa_radius_acct_parse (pkt, len, SECRET, &a) == BPA_RADIUS_MALFORMED,
	   "a length that disagrees with the datagram is refused");

    CHECK (bpa_radius_acct_parse (pkt, 12, SECRET, &a) == BPA_RADIUS_MALFORMED,
	   "and so is a packet shorter than a header");
  }

  printf ("\n--- an attribute that runs past the packet ends the parse ---\n");
  {
    u8 attrs[] = { 40, 6, 0, 0, 0, BPA_ACCT_START, 1, 40, 'x', 'y' };

    len = build (pkt, 2, attrs, sizeof (attrs), SECRET);
    CHECK (bpa_radius_acct_parse (pkt, len, SECRET, &a) == BPA_RADIUS_MALFORMED,
	   "a parser that clamped here would read whatever followed as an "
	   "attribute");
  }

  printf ("\n--- a request with no Acct-Status-Type is not one ---\n");
  {
    u8 attrs[] = { 8, 6, 100, 64, 0, 5 };

    len = build (pkt, 3, attrs, sizeof (attrs), SECRET);
    CHECK (bpa_radius_acct_parse (pkt, len, SECRET, &a) == BPA_RADIUS_MALFORMED,
	   "without it there is nothing to act on: no start, no stop");
  }

  printf ("\n--- a User-Name longer than the field is truncated, and says "
	  "so ---\n");
  {
    u8 attrs[80];
    u32 n = 0, i;

    attrs[n++] = 1;
    attrs[n++] = 2 + 50;
    for (i = 0; i < 50; i++)
      attrs[n++] = 'a';
    attrs[n++] = 40; attrs[n++] = 6;
    attrs[n++] = 0; attrs[n++] = 0; attrs[n++] = 0; attrs[n++] = BPA_ACCT_START;
    len = build (pkt, 4, attrs, n, SECRET);
    CHECK (bpa_radius_acct_parse (pkt, len, SECRET, &a) == BPA_RADIUS_OK
	     && strlen (a.user) == BPA_IDENTITY_MAX - 1 && a.user_truncated,
	   "the subscriber exists, so a partial name beats no record: len=%zu "
	   "truncated=%u",
	   strlen (a.user), a.user_truncated);
  }

  printf ("\n--- a Stop with no address is read, and acted on by the caller "
	  "---\n");
  {
    u8 attrs[] = { 1, 6, 'j', 'o', 'a', 'o', 40, 6, 0, 0, 0, BPA_ACCT_STOP };
    /* 6 = two header bytes and four characters, which is "joao". */

    len = build (pkt, 5, attrs, sizeof (attrs), SECRET);
    CHECK (bpa_radius_acct_parse (pkt, len, SECRET, &a) == BPA_RADIUS_OK
	     && a.status == BPA_ACCT_STOP && !a.have_ip,
	   "have_ip says the address is absent rather than zero, which is an "
	   "address");
  }

  printf ("\n%s\n", fail ? "FAILURES" : "ALL TESTS PASSED");
  return fail ? 1 : 0;
}
