/*
 * test_bpa_sip.c - unit test for the SIP message parser and the edit machinery
 * the ALG applies with it.
 *
 * Most of this file is about the cases where the answer is "do not touch it".
 * SIP is a large protocol and this parser understands a small part of it; what
 * keeps that safe is that every part it does not understand is refused WHOLE,
 * and a refusal that is not asserted is a refusal nobody has seen happen.
 *
 * The second half is the edit list, which is where a rewrite goes wrong
 * silently. Several substitutions of different lengths in one message move
 * every offset after the first one, and an ALG that got that wrong would emit
 * a message that still parses and names the wrong things - the worst way for
 * this to fail, because it looks like a working ALG and a broken phone.
 */

#include <stdio.h>
#include <string.h>
#include "bpa_shim.h"
#include "../src/plugins/bpa/bpa_sip.h"

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

#define IP4(a, b, c, d)                                                       \
  (((u32) (a) << 24) | ((u32) (b) << 16) | ((u32) (c) << 8) | (u32) (d))

static const u32 PRIV = IP4 (100, 64, 0, 5);
static const u32 PUB = IP4 (200, 150, 0, 17);

static bpa_sip_verdict_t
run (const char *msg, bpa_sip_msg_t *m)
{
  memset (m, 0, sizeof (*m));
  return bpa_sip_parse ((const u8 *) msg, (u32) strlen (msg), m);
}

/* The item of this kind, or 0. Only ever one Via and one Content-Length in
 * these messages, so "the first" is "the one". */
static const bpa_sip_item_t *
item_of (const bpa_sip_msg_t *m, bpa_sip_item_kind_t kind, u32 nth)
{
  u32 i, seen = 0;

  for (i = 0; i < m->n_items; i++)
    if (m->items[i].kind == (u8) kind && seen++ == nth)
      return &m->items[i];
  return 0;
}

/*
 * Asserts that a span covers exactly the text it claims to.
 *
 * An offset off by one produces a rewritten header that still parses and names
 * a different address, so the span is compared against the text AND against
 * where that text sits in the message - not merely against its contents, which
 * would pass for a span pointing at a different copy of the same digits.
 */
static void
span_is (const char *what, const char *msg, const bpa_sip_item_t *it,
	 const char *want)
{
  if (!it)
    {
      CHECK (0, "%s: no such item", what);
      return;
    }
  CHECK (it->len == strlen (want) && !memcmp (msg + it->off, want, it->len),
	 "%s: span is \"%.*s\", expected \"%s\"", what, (int) it->len,
	 msg + it->off, want);
}

/* ------------------------------------------------------------------ */

static const char *INVITE =
  "INVITE sip:2000@8.8.8.8 SIP/2.0\r\n"
  "Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z9hG4bKnashds8\r\n"
  "From: \"Ana\" <sip:1000@8.8.8.8>;tag=abc\r\n"
  "To: <sip:2000@8.8.8.8>\r\n"
  "Call-ID: a84b4c76e66710@100.64.0.5\r\n"
  "CSeq: 1 INVITE\r\n"
  "Contact: <sip:1000@100.64.0.5:5060>\r\n"
  "Content-Type: application/sdp\r\n"
  "Content-Length: 111\r\n"
  "\r\n"
  "v=0\r\n"
  "o=- 1 1 IN IP4 100.64.0.5\r\n"
  "s=-\r\n"
  "c=IN IP4 100.64.0.5\r\n"
  "t=0 0\r\n"
  "m=audio 8000 RTP/AVP 0\r\n"
  "a=rtpmap:0 PCMU/8000\r\n";

int
main (void)
{
  bpa_sip_msg_t m;
  const bpa_sip_item_t *it;
  u8 out[BPA_SIP_TXT_MAX];
  u32 n;

  printf ("SIP parser and edit list\n");

  /* ---- the message the ALG exists for ---- */
  {
    u32 body = (u32) (strstr (INVITE, "v=0") - INVITE);

    CHECK (run (INVITE, &m) == BPA_SIP_PARSED, "a plain INVITE did not parse");
    CHECK (m.is_response == 0, "an INVITE was read as a response");
    CHECK (m.body_off == body, "body at %u, expected %u", m.body_off, body);
    CHECK (m.body_len == 111, "body length %u, expected 111 as declared",
	   m.body_len);

    it = item_of (&m, BPA_SIP_VIA, 0);
    span_is ("Via sent-by", INVITE, it, "100.64.0.5:5060");
    CHECK (it && it->addr == PRIV && it->port == 5060,
	   "Via sent-by parsed as %08x:%u", it ? it->addr : 0,
	   it ? it->port : 0);

    it = item_of (&m, BPA_SIP_CONTACT, 0);
    span_is ("Contact host", INVITE, it, "100.64.0.5:5060");
    CHECK (it && it->addr == PRIV, "Contact host parsed as %08x",
	   it ? it->addr : 0);

    it = item_of (&m, BPA_SIP_CONN, 0);
    span_is ("SDP c=", INVITE, it, "100.64.0.5");
    CHECK (it && it->addr == PRIV, "c= parsed as %08x", it ? it->addr : 0);

    it = item_of (&m, BPA_SIP_MEDIA, 0);
    span_is ("SDP m= port", INVITE, it, "8000");
    CHECK (it && it->port == 8000 && it->addr == PRIV,
	   "m= port %u at %08x, expected 8000 at the session c=",
	   it ? it->port : 0, it ? it->addr : 0);

    it = item_of (&m, BPA_SIP_CLEN, 0);
    span_is ("Content-Length value", INVITE, it, "111");

    /* Call-ID carries the private address too, and rewriting it would break
     * the dialog both ends match on. Exactly five spans, so a sixth appearing
     * is a test failure rather than a surprise in production. */
    CHECK (m.n_items == 5, "%u spans recorded, expected 5", m.n_items);
  }

  /* ---- the same message, compact header names ---- */
  {
    static const char *c =
      "INVITE sip:2000@8.8.8.8 SIP/2.0\r\n"
      "v: SIP/2.0/UDP 100.64.0.5:5060;branch=z9\r\n"
      "m: <sip:1000@100.64.0.5:5060>\r\n"
      "c: application/sdp\r\n"
      "l: 50\r\n"
      "\r\n"
      "v=0\r\n"
      "c=IN IP4 100.64.0.5\r\n"
      "m=audio 8000 RTP/AVP 0\r\n";

    CHECK (run (c, &m) == BPA_SIP_PARSED, "compact header names did not parse");
    CHECK (m.n_items == 5, "compact form gave %u spans, expected 5",
	   m.n_items);
    span_is ("compact Via", c, item_of (&m, BPA_SIP_VIA, 0),
	     "100.64.0.5:5060");
    span_is ("compact Contact", c, item_of (&m, BPA_SIP_CONTACT, 0),
	     "100.64.0.5:5060");
  }

  /* ---- a response ---- */
  {
    static const char *r = "SIP/2.0 200 OK\r\n"
			   "Via: SIP/2.0/UDP 200.150.0.17:10512;branch=z9\r\n"
			   "Content-Length: 0\r\n"
			   "\r\n";

    CHECK (run (r, &m) == BPA_SIP_PARSED, "a 200 OK did not parse");
    CHECK (m.is_response == 1, "a status line was read as a request");
    it = item_of (&m, BPA_SIP_VIA, 0);
    CHECK (it && it->addr == PUB && it->port == 10512,
	   "the response Via did not come back as the public address:port");
  }

  /* ---- only the topmost Via ---- */
  {
    static const char *two = "BYE sip:a@8.8.8.8 SIP/2.0\r\n"
			     "Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
			     "Via: SIP/2.0/UDP 100.64.0.9:5060;branch=z2\r\n"
			     "Content-Length: 0\r\n"
			     "\r\n";
    u32 i, n_via = 0;

    CHECK (run (two, &m) == BPA_SIP_PARSED, "two Via headers did not parse");
    for (i = 0; i < m.n_items; i++)
      n_via += m.items[i].kind == BPA_SIP_VIA;
    CHECK (n_via == 1, "%u Via spans recorded; only the topmost is ours",
	   n_via);
    span_is ("the topmost Via", two, item_of (&m, BPA_SIP_VIA, 0),
	     "100.64.0.5:5060");
  }

  /* ---- addresses that are not ours, and hosts that are not addresses ---- */
  {
    static const char *name = "REGISTER sip:8.8.8.8 SIP/2.0\r\n"
			      "Via: SIP/2.0/UDP phone.lan:5060;branch=z1\r\n"
			      "Contact: <sip:1@10.0.0.1.example.com>\r\n"
			      "Content-Length: 0\r\n"
			      "\r\n";

    CHECK (run (name, &m) == BPA_SIP_PARSED, "host names did not parse");
    it = item_of (&m, BPA_SIP_VIA, 0);
    CHECK (it && it->addr == 0, "a host NAME in Via was read as an address");
    span_is ("the name span still covers host and port", name, it,
	     "phone.lan:5060");
    it = item_of (&m, BPA_SIP_CONTACT, 0);
    CHECK (it && it->addr == 0,
	   "\"10.0.0.1.example.com\" was read as the address 10.0.0.1");
  }

  /*
   * The top of the port range, which nothing else here reaches.
   *
   * bpa_sip_num() refuses a value above the ceiling its caller names, and
   * every port in this file is five digits below it - so the ceiling was
   * probed from neither side. Measured: with the test cut to `v >= max` all
   * 74 checks passed, and 65535 is a port a UA may really be listening on.
   * The refusal above it is in the table further down.
   */
  {
    static const char *top = "REGISTER sip:8.8.8.8 SIP/2.0\r\n"
			     "Via: SIP/2.0/UDP 100.64.0.5:65535;branch=z1\r\n"
			     "Content-Length: 0\r\n"
			     "\r\n";

    CHECK (run (top, &m) == BPA_SIP_PARSED, "the highest port did not parse");
    it = item_of (&m, BPA_SIP_VIA, 0);
    CHECK (it && it->addr == PRIV && it->port == 65535,
	   "a sent-by on port 65535 came back as %u", it ? it->port : 0);
  }

  {
    static const char *v6 = "REGISTER sip:8.8.8.8 SIP/2.0\r\n"
			    "Via: SIP/2.0/UDP [2001:db8::1]:5060;branch=z1\r\n"
			    "Content-Length: 0\r\n"
			    "\r\n";

    CHECK (run (v6, &m) == BPA_SIP_PARSED, "an IPv6 sent-by did not parse");
    it = item_of (&m, BPA_SIP_VIA, 0);
    CHECK (it && it->addr == 0, "an IPv6 literal was read as an IPv4 address");
    span_is ("the IPv6 span covers the brackets and the port", v6, it,
	     "[2001:db8::1]:5060");
  }

  /* ---- Contact shapes ---- */
  {
    static const char *bare = "REGISTER sip:8.8.8.8 SIP/2.0\r\n"
			      "Via: SIP/2.0/UDP 100.64.0.5;branch=z1\r\n"
			      "Contact: sip:1000@100.64.0.5\r\n"
			      "Content-Length: 0\r\n"
			      "\r\n";

    CHECK (run (bare, &m) == BPA_SIP_PARSED,
	   "a Contact with no angle brackets did not parse");
    it = item_of (&m, BPA_SIP_VIA, 0);
    CHECK (it && it->addr == PRIV && it->port == 0,
	   "a Via with no port did not come back with port 0");
    span_is ("a Via with no port", bare, it, "100.64.0.5");
    it = item_of (&m, BPA_SIP_CONTACT, 0);
    CHECK (it && it->addr == PRIV, "a bare Contact URI host was not found");
    span_is ("a bare Contact URI host", bare, it, "100.64.0.5");
  }

  {
    static const char *star = "REGISTER sip:8.8.8.8 SIP/2.0\r\n"
			      "Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
			      "Contact: *\r\n"
			      "Expires: 0\r\n"
			      "Content-Length: 0\r\n"
			      "\r\n";

    CHECK (run (star, &m) == BPA_SIP_PARSED, "Contact: * did not parse");
    CHECK (item_of (&m, BPA_SIP_CONTACT, 0) == 0,
	   "Contact: * names no place and must record no span");
  }

  {
    /* A display name containing an "@" and a ">" - both of which the URI
     * parser looks for - in quotes, where they mean nothing. */
    static const char *dn =
      "REGISTER sip:8.8.8.8 SIP/2.0\r\n"
      "Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
      "Contact: \"a@b>c\" <sip:1000@100.64.0.5:5060>;expires=60\r\n"
      "Content-Length: 0\r\n"
      "\r\n";

    CHECK (run (dn, &m) == BPA_SIP_PARSED, "a quoted display name refused");
    span_is ("the URI host past a quoted display name", dn,
	     item_of (&m, BPA_SIP_CONTACT, 0), "100.64.0.5:5060");
  }

  /* ---- SDP: which port belongs to which address ---- */
  {
    static const char *two_m = "INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
			       "Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
			       "Content-Type: application/sdp\r\n"
			       "Content-Length: 105\r\n"
			       "\r\n"
			       "v=0\r\n"
			       "c=IN IP4 100.64.0.5\r\n"
			       "m=audio 8000 RTP/AVP 0\r\n"
			       "m=video 9000 RTP/AVP 96\r\n"
			       "c=IN IP4 8.8.4.4\r\n"
			       "a=sendonly\r\n";

    CHECK (run (two_m, &m) == BPA_SIP_PARSED, "two media sections refused");
    it = item_of (&m, BPA_SIP_MEDIA, 0);
    CHECK (it && it->port == 8000 && it->addr == PRIV,
	   "the audio port took the wrong connection address: %08x",
	   it ? it->addr : 0);
    it = item_of (&m, BPA_SIP_MEDIA, 1);
    CHECK (it && it->port == 9000 && it->addr == IP4 (8, 8, 4, 4),
	   "the video port did not take its OWN c=, which follows it: %08x",
	   it ? it->addr : 0);
  }

  {
    static const char *odd = "INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
			     "Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
			     "Content-Type: application/sdp\r\n"
			     "Content-Length: 101\r\n"
			     "\r\n"
			     "v=0\r\n"
			     "c=IN IP4 224.2.1.1/127\r\n"
			     "m=audio 0 RTP/AVP 0\r\n"
			     "m=video 9000/2 RTP/AVP 96\r\n"
			     "m=text 7000 RTP/AVP 98\r\n";
    u32 i, n_media = 0, n_conn = 0;

    CHECK (run (odd, &m) == BPA_SIP_PARSED, "unusual SDP lines refused");
    for (i = 0; i < m.n_items; i++)
      {
	n_media += m.items[i].kind == BPA_SIP_MEDIA;
	n_conn += m.items[i].kind == BPA_SIP_CONN;
      }
    CHECK (n_conn == 0, "a multicast c= with a TTL must not be mapped");
    CHECK (n_media == 1,
	   "%u media ports recorded; a refused stream (port 0) and a port "
	   "RANGE are neither of them mappable",
	   n_media);
    it = item_of (&m, BPA_SIP_MEDIA, 0);
    CHECK (it && it->port == 7000 && it->addr == 0,
	   "the one mappable port did not come back with no address of ours");
  }

  {
    static const char *many = "INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
			      "Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
			      "Content-Type: application/sdp\r\n"
			      "Content-Length: 143\r\n"
			      "\r\n"
			      "v=0\r\n"
			      "c=IN IP4 100.64.0.5\r\n"
			      "m=audio 8000 RTP/AVP 0\r\n"
			      "m=video 9000 RTP/AVP 96\r\n"
			      "m=text 7000 RTP/AVP 98\r\n"
			      "m=app 6000 RTP/AVP 99\r\n"
			      "m=x 5000 RTP/AVP 99\r\n";

    CHECK (run (many, &m) == BPA_SIP_UNPARSED,
	   "more media streams than this ALG can reserve pairs for must "
	   "refuse the message, not map some of them");
  }

  /* ---- the refusals ---- */
  {
    static const struct
    {
      const char *what;
      const char *msg;
      bpa_sip_verdict_t want;
    } bad[] = {
      { "not SIP at all", "GET / HTTP/1.1\r\nHost: x\r\n\r\n", BPA_SIP_NONE },
      { "a datagram with no line ending", "INVITE sip:a SIP/2.0",
	BPA_SIP_NONE },
      { "a SIP keepalive (CRLF)", "\r\n\r\n", BPA_SIP_NONE },
      { "a header block that does not end",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n",
	BPA_SIP_UNPARSED },
      { "a folded header line",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP\r\n 100.64.0.5:5060;branch=z1\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      /*
       * The one fold the HCOLON rule alone would let through: a continuation
       * that is whitespace and then a colon reads as a header with no name.
       * Without the explicit fold check the Via above it is taken as complete
       * and half its value is never seen. This is the case that makes that
       * check load-bearing rather than belt-and-braces.
       */
      { "a fold whose continuation reads as a nameless header",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:5060\r\n :branch=z1\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      { "a header line with no colon",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"this is not a header\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      { "a Via naming a transport this datagram did not arrive on",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/TCP 100.64.0.5:5060;branch=z1\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      { "a Via with no sent-by",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP ;branch=z1\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      { "a Contact that is not a URI",
	"REGISTER sip:8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
	"Contact: not-a-uri\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      { "a Content-Length shorter than the body that arrived",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
	"Content-Type: application/sdp\r\n"
	"Content-Length: 4\r\n"
	"\r\n"
	"v=0\r\n"
	"c=IN IP4 100.64.0.5\r\n",
	BPA_SIP_UNPARSED },
      { "a Content-Length longer than the body that arrived",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
	"Content-Type: application/sdp\r\n"
	"Content-Length: 400\r\n"
	"\r\n"
	"v=0\r\n"
	"c=IN IP4 100.64.0.5\r\n",
	BPA_SIP_UNPARSED },
      { "a body with no Content-Length",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
	"Content-Type: application/sdp\r\n"
	"\r\n"
	"v=0\r\n"
	"c=IN IP4 100.64.0.5\r\n",
	BPA_SIP_UNPARSED },
      { "a Content-Length that promises a body which never arrived",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
	"Content-Length: 120\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      { "a multipart body with SDP somewhere inside it",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
	"Content-Type: multipart/mixed;boundary=x\r\n"
	"Content-Length: 38\r\n"
	"\r\n"
	"--x\r\nv=0\r\nc=IN IP4 100.64.0.5\r\n--x--\r\n",
	BPA_SIP_UNPARSED },
      /* Two of them disagreeing means this box and the next proxy along read
       * a different body out of the same datagram - and whichever one was
       * rewritten is the one the other did not use. */
      { "two Content-Length headers",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
	"Content-Type: application/sdp\r\n"
	/* The second one agrees with the body, so a parser that simply let
	 * the last one win would accept this and rewrite a body the proxy
	 * reading the FIRST header never sees. */
	"Content-Length: 4\r\n"
	"Content-Length: 26\r\n"
	"\r\n"
	"v=0\r\n"
	"c=IN IP4 100.64.0.5\r\n",
	BPA_SIP_UNPARSED },
      { "a body with no Content-Type at all",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
	"Content-Length: 26\r\n"
	"\r\n"
	"v=0\r\n"
	"c=IN IP4 100.64.0.5\r\n",
	BPA_SIP_UNPARSED },
      /* The DECLARED type decides. A body that begins like SDP under a
       * Content-Type that says otherwise is not SDP, and reading it because
       * its first three bytes are familiar is guessing. */
      { "a body that begins like SDP under another Content-Type",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
	"Content-Type: text/plain\r\n"
	"Content-Length: 26\r\n"
	"\r\n"
	"v=0\r\n"
	"c=IN IP4 100.64.0.5\r\n",
	BPA_SIP_UNPARSED },
      /*
       * The shapes below are the refusals of bpa_sip_hostport(),
       * bpa_sip_via() and bpa_sip_contact() that nothing docker-free had ever
       * executed. Every one of them is a header this parser must NOT come
       * apart, because the caller rewrites what it comes apart into: a Via it
       * misreads is a response that never comes back, and a Contact it
       * misreads is a subscriber nobody can call.
       */
      /* bpa_sip_hostport: a "[" that never closes. An IPv6 reference is a
       * host this ALG does not act on, but a reference with no "]" is not a
       * host at all and the scan for it must not run past the value. */
      { "a Contact whose IPv6 reference never closes",
	"REGISTER sip:8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
	"Contact: <sip:a@[2001:db8::1>\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      /* bpa_sip_hostport: a host name of no characters. */
      { "a Via whose sent-by begins with its own port separator",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP :5060;branch=z1\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      /* bpa_sip_hostport, through bpa_sip_num: a colon that introduces no
       * port. A sent-by of "host:" names port 5060 to some stacks and nothing
       * to others, and this box maps neither. */
      { "a Via whose sent-by ends at the colon",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:;branch=z1\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      { "a Via whose sent-by port is zero",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:0;branch=z1\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      /* bpa_sip_num's upper bound. A port of 70000 is 4464 on sixteen bits,
       * which is a real port belonging to a real subscriber. */
      { "a Via whose sent-by port does not fit in sixteen bits",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:70000;branch=z1\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      /* bpa_sip_via: the version token. Not pinned to 2.0 - the sent-by
       * syntax survives a version bump - but it has to be there, because the
       * transport and the sent-by are located by counting from it. */
      { "a Via with no version token at all",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: UDP 100.64.0.5:5060;branch=z1\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      { "a Via whose version is not two digits round a dot",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2x0/UDP 100.64.0.5:5060;branch=z1\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      /* bpa_sip_via: a transport token that merely STARTS with UDP. Without
       * this the sent-by would be read three bytes into the token. */
      { "a Via whose transport only starts like UDP",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDPX 100.64.0.5:5060;branch=z1\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      /* bpa_sip_contact: a header with nothing in it. */
      { "a Contact with nothing in its value",
	"REGISTER sip:8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
	"Contact:  \r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      /* bpa_sip_contact: a quoted display name that never closes. The scan
       * for the closing quote skips backslash escapes, so it is the one place
       * in this parser that advances by two. */
      { "a Contact whose quoted display name never closes",
	"REGISTER sip:8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
	"Contact: \"Ana <sip:a@100.64.0.5>\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      { "a Contact whose angle bracket never closes",
	"REGISTER sip:8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
	"Contact: <sip:a@100.64.0.5\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      /* bpa_sip_contact: a comma reached while looking for the scheme. A
       * comma separates Contact values, so a URI is being skipped past. */
      { "a Contact with an unquoted display name holding a comma",
	"REGISTER sip:8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
	"Contact: Ana,B <sip:a@100.64.0.5>\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      /* bpa_sip_contact: a scheme with no colon after it. */
      { "a Contact whose scheme is not followed by a colon",
	"REGISTER sip:8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
	"Contact: <sipfoo@100.64.0.5>\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      /* bpa_sip_num, through Content-Length: a value with no digits in it.
       * Everything below the header block is bounded by this number. */
      { "a Content-Length that is not a number",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
	"Content-Length: none\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      /* bpa_sip_num, through a sent-by port: one above the ceiling. Read
       * without the ceiling it truncates to zero, and the ALG maps a port
       * nobody asked for. The acceptance of 65535 is above. */
      { "a Via sent-by whose port is one above the range",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:65536;branch=z1\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      /*
       * bpa_sip_via: a sent-by the host parse does not reach the end of.
       *
       * The address stops at the "x" and the delimiter is the semicolon, so
       * the span bpa_sip_hostport() returns is shorter than the sent-by. That
       * is the one shape where a rewrite lands inside a host rather than over
       * it: without the `i + span != e` test the Via is recorded as ten bytes
       * and the replacement leaves "200.150.0.17x:5060" behind. Measured:
       * with that test deleted, all 74 checks of this file passed.
       */
      { "a Via sent-by with bytes after the address the host parse stopped at",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5x:5060;branch=z1\r\n"
	"Content-Length: 0\r\n"
	"\r\n",
	BPA_SIP_UNPARSED },
      { "a body that claims to be SDP and is not",
	"INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	"Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
	"Content-Type: application/sdp\r\n"
	"Content-Length: 8\r\n"
	"\r\n"
	"garbage\n",
	BPA_SIP_UNPARSED },
    };
    unsigned k;

    for (k = 0; k < sizeof (bad) / sizeof (bad[0]); k++)
      {
	bpa_sip_verdict_t got = run (bad[k].msg, &m);

	CHECK (got == bad[k].want, "%s: verdict %u, expected %u", bad[k].what,
	       got, bad[k].want);
      }
  }

  /*
   * No PREFIX of a complete message may parse.
   *
   * "the header I am looking for is not here" and "it has not arrived yet" are
   * the same observation to a parser that does not insist on the end of the
   * message, and answering the second by rewriting what is in front of it is
   * how an ALG mangles a datagram it only half received.
   */
  {
    u32 len = (u32) strlen (INVITE), i, parsed = 0;

    for (i = 0; i < len; i++)
      {
	bpa_sip_msg_t p;

	memset (&p, 0, sizeof (p));
	if (bpa_sip_parse ((const u8 *) INVITE, i, &p) == BPA_SIP_PARSED)
	  parsed++;
      }
    CHECK (parsed == 0, "%u truncated prefixes parsed as a whole message",
	   parsed);
  }

  /* ---- what gets written ---- */
  n = bpa_sip_fmt_u32 (out, 10512);
  CHECK (n == 5 && !memcmp (out, "10512", 5), "fmt_u32 gave \"%.*s\"", (int) n,
	 out);
  n = bpa_sip_fmt_u32 (out, 0);
  CHECK (n == 1 && out[0] == '0', "fmt_u32(0) gave \"%.*s\"", (int) n, out);
  n = bpa_sip_fmt_addr (out, PUB);
  CHECK (n == 12 && !memcmp (out, "200.150.0.17", 12),
	 "fmt_addr gave \"%.*s\"", (int) n, out);
  n = bpa_sip_fmt_addr_port (out, IP4 (255, 255, 255, 255), 65535);
  CHECK (n == 21 && !memcmp (out, "255.255.255.255:65535", 21),
	 "the widest address:port gave \"%.*s\"", (int) n, out);
  CHECK (n <= BPA_SIP_TXT_MAX, "the widest address:port overflows its bound");

  /*
   * The edit list.
   *
   * Three substitutions of three different length changes - longer, shorter
   * and the same - applied in one pass over one payload. Built in the order a
   * caller discovers them, which is NOT the order they appear in the message:
   * Content-Length is worked out last and sits first.
   */
  {
    char buf[128];
    const char *before = "A=1234 B=99 C=7\r\n";
    const char *after = "A=1 B=987654 C=7\r\n";
    bpa_sip_edit_t e[BPA_SIP_MAX_ITEMS];
    u32 ne = 0;
    i32 d;

    memcpy (buf, before, strlen (before) + 1);
    /* out of order on purpose */
    CHECK (bpa_sip_edit_add (e, &ne, 14, 1, (const u8 *) "7", 1) == 0,
	   "edit_add refused C");
    CHECK (bpa_sip_edit_add (e, &ne, 9, 2, (const u8 *) "987654", 6) == 0,
	   "edit_add refused B");
    CHECK (bpa_sip_edit_add (e, &ne, 2, 4, (const u8 *) "1", 1) == 0,
	   "edit_add refused A");
    CHECK (e[0].off == 2 && e[1].off == 9 && e[2].off == 14,
	   "the edit list did not sort on insertion: %u %u %u", e[0].off,
	   e[1].off, e[2].off);

    d = bpa_sip_apply ((u8 *) buf, (u32) strlen (before), e, ne);
    CHECK (d == 1, "apply reported a length change of %d, expected +1",
	   (int) d);
    CHECK (!memcmp (buf, after, strlen (after)),
	   "apply produced \"%.*s\", expected \"%.*s\"",
	   (int) strlen (before) + 1, buf, (int) strlen (after), after);
  }

  /* The list is bounded, and a text longer than the widest thing this file
   * can write is refused rather than truncated into a span. */
  {
    bpa_sip_edit_t e[BPA_SIP_MAX_ITEMS];
    u32 ne = 0, i;
    u8 wide[BPA_SIP_TXT_MAX + 1];

    memset (wide, 'x', sizeof (wide));
    CHECK (bpa_sip_edit_add (e, &ne, 0, 1, wide, BPA_SIP_TXT_MAX + 1) == -1,
	   "an over-wide replacement was accepted");

    /*
     * And the widest one that FITS is accepted, which is the other side of the
     * same bound and the side that costs a customer something.
     *
     * BPA_SIP_TXT_MAX is sized for "255.255.255.255:65535" - 21 bytes, the
     * exact string bpa_sip_fmt_addr_port produces at its widest and the one
     * the check above at the formatter asserts. Only the over-wide side was
     * ever driven here, so `tlen > BPA_SIP_TXT_MAX` written as `>=` refuses
     * the widest legal replacement and the ALG silently leaves those messages
     * alone: measured, all 107 checks of this file passed with that edit in
     * place. A bound probed from one side is half a bound.
     */
    ne = 0;
    CHECK (bpa_sip_edit_add (e, &ne, 0, 1, wide, BPA_SIP_TXT_MAX) == 0,
	   "the widest replacement that fits was refused");
    ne = 0;
    for (i = 0; i < BPA_SIP_MAX_ITEMS; i++)
      CHECK (bpa_sip_edit_add (e, &ne, i, 1, (const u8 *) "y", 1) == 0,
	     "edit %u of a full list was refused early", i);
    CHECK (bpa_sip_edit_add (e, &ne, 99, 1, (const u8 *) "y", 1) == -1,
	   "the edit list accepted one past its bound");
  }

  /*
   * The round trip: parse the INVITE, rewrite every span that names the
   * subscriber, and demand the exact message the far end must receive -
   * including the Content-Length the body edits moved.
   *
   * This is the assertion the whole file is for. Each piece of it can be right
   * on its own and the result still wrong, because the spans are offsets into
   * a payload that four of them are changing the length of.
   */
  {
    char buf[1024];
    bpa_sip_edit_t e[BPA_SIP_MAX_ITEMS];
    u32 ne = 0, i, len = (u32) strlen (INVITE);
    i32 body_d = 0, d;
    const bpa_sip_item_t *clen = 0;
    static const char *want =
      "INVITE sip:2000@8.8.8.8 SIP/2.0\r\n"
      "Via: SIP/2.0/UDP 200.150.0.17:10512;branch=z9hG4bKnashds8\r\n"
      "From: \"Ana\" <sip:1000@8.8.8.8>;tag=abc\r\n"
      "To: <sip:2000@8.8.8.8>\r\n"
      "Call-ID: a84b4c76e66710@100.64.0.5\r\n"
      "CSeq: 1 INVITE\r\n"
      "Contact: <sip:1000@200.150.0.17:10512>\r\n"
      "Content-Type: application/sdp\r\n"
      "Content-Length: 114\r\n"
      "\r\n"
      "v=0\r\n"
      "o=- 1 1 IN IP4 100.64.0.5\r\n"
      "s=-\r\n"
      "c=IN IP4 200.150.0.17\r\n"
      "t=0 0\r\n"
      "m=audio 10514 RTP/AVP 0\r\n"
      "a=rtpmap:0 PCMU/8000\r\n";

    memcpy (buf, INVITE, len + 1);
    CHECK (run (INVITE, &m) == BPA_SIP_PARSED, "round trip: parse failed");

    for (i = 0; i < m.n_items; i++)
      {
	const bpa_sip_item_t *x = &m.items[i];
	u32 w;

	if (x->kind == BPA_SIP_CLEN)
	  {
	    clen = x;
	    continue;
	  }
	if (x->addr != PRIV)
	  continue;
	switch (x->kind)
	  {
	  case BPA_SIP_VIA:
	  case BPA_SIP_CONTACT:
	    w = bpa_sip_fmt_addr_port (out, PUB, 10512);
	    break;
	  case BPA_SIP_CONN:
	    w = bpa_sip_fmt_addr (out, PUB);
	    body_d += (i32) w - (i32) x->len;
	    break;
	  default:
	    w = bpa_sip_fmt_u32 (out, 10514);
	    body_d += (i32) w - (i32) x->len;
	    break;
	  }
	CHECK (bpa_sip_edit_add (e, &ne, x->off, x->len, out, w) == 0,
	       "round trip: edit %u refused", i);
      }

    CHECK (clen != 0, "round trip: no Content-Length span");
    if (clen)
      {
	u32 w = bpa_sip_fmt_u32 (out, (u32) ((i32) m.body_len + body_d));

	CHECK (bpa_sip_edit_add (e, &ne, clen->off, clen->len, out, w) == 0,
	       "round trip: the Content-Length edit was refused");
      }

    d = bpa_sip_apply ((u8 *) buf, len, e, ne);
    buf[len + d] = 0;
    CHECK ((u32) ((i32) len + d) == (u32) strlen (want)
	     && !memcmp (buf, want, strlen (want)),
	   "round trip produced:\n----\n%s----\nexpected:\n----\n%s----", buf,
	   want);

    /* The rewritten message must still be a message: parsing it back has to
     * find the public address where the private one was, and a body whose
     * declared length is the one that is now there. */
    {
      bpa_sip_msg_t again;

      memset (&again, 0, sizeof (again));
      CHECK (bpa_sip_parse ((const u8 *) buf, (u32) ((i32) len + d), &again)
	       == BPA_SIP_PARSED,
	     "the rewritten message no longer parses");
      CHECK (again.body_len == 114,
	     "the rewritten body is %u bytes; Content-Length says 114",
	     again.body_len);
      it = item_of (&again, BPA_SIP_VIA, 0);
      CHECK (it && it->addr == PUB && it->port == 10512,
	     "the rewritten Via does not name the public address and port");
      it = item_of (&again, BPA_SIP_MEDIA, 0);
      CHECK (it && it->port == 10514 && it->addr == PUB,
	     "the rewritten media port is not announced at the public "
	     "address");
    }
  }

  /*
   * A host that LOOKS like an address and is not one.
   *
   * bpa_sip_ip4() consumes a dotted quad and nothing else, and the arm that
   * refuses one of the four octets had never been executed - every header in
   * this file carries either a complete address or a name that starts with a
   * letter. The dangerous shape is the one in between: three octets, or digits
   * with something other than a dot between them. Read as an address it would
   * be compared against the subscriber's own, and a rewrite there replaces
   * bytes out of the middle of somebody's host name.
   *
   * Accepted rather than refused, with no address recorded, which is what the
   * whole file rests on: the caller rewrites a span only where `addr` equals
   * the session's private address, so zero means "left alone" with no further
   * rule.
   */
  {
    static const char *names[] = {
      "100.64.0",     /* three octets: the fourth never arrives */
      "100.64.0.5.6", /* five: a name whose first labels look numeric */
      "100x64.0.5",   /* a separator that is not a dot */
      "100.64.0.999", /* an octet that does not fit in a byte */
    };
    char msg[512], want[64];
    unsigned k;

    for (k = 0; k < sizeof (names) / sizeof (names[0]); k++)
      {
	snprintf (msg, sizeof (msg),
		  "INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
		  "Via: SIP/2.0/UDP %s:5060;branch=z1\r\n"
		  "Content-Length: 0\r\n"
		  "\r\n",
		  names[k]);
	CHECK (run (msg, &m) == BPA_SIP_PARSED,
	       "a Via naming the host \"%s\" refused the message, where it is "
	       "a host this ALG has nothing to do with", names[k]);
	it = item_of (&m, BPA_SIP_VIA, 0);
	snprintf (want, sizeof (want), "%s:5060", names[k]);
	span_is ("a sent-by that is not a dotted quad", msg, it, want);
	CHECK (it && it->addr == 0,
	       "the host \"%s\" was read as address %08x, so a rewrite would "
	       "replace bytes out of the middle of it", names[k],
	       it ? it->addr : 0);
      }
    /* The control: the same message with a real dotted quad IS read as an
     * address, so the four above are about the shape and not about the
     * parser having stopped recognising addresses. */
    snprintf (msg, sizeof (msg),
	      "INVITE sip:a@8.8.8.8 SIP/2.0\r\n"
	      "Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
	      "Content-Length: 0\r\n"
	      "\r\n");
    CHECK (run (msg, &m) == BPA_SIP_PARSED, "the control message refused");
    it = item_of (&m, BPA_SIP_VIA, 0);
    CHECK (it && it->addr == PRIV,
	   "a real dotted quad was read as address %08x", it ? it->addr : 0);
  }

  /*
   * "sips:", and the SDP written with the whitespace RFC 4566 allows.
   *
   * Both are ACCEPTING paths that nothing had executed, and both fail
   * silently: a Contact this parser walks past is a subscriber the internet
   * cannot call back, and a c= line it walks past is a media address left
   * announcing the private one - the call connects and there is no audio.
   */
  {
    static const char *msg =
      "INVITE sip:2000@8.8.8.8 SIP/2.0\r\n"
      "Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n"
      "Contact: <sips:1000@100.64.0.5:5061>\r\n"
      "Content-Type: application/sdp\r\n"
      "Content-Length: 56\r\n"
      "\r\n"
      "v=0\r\n"
      "c=  IN   IP4   100.64.0.5\r\n"
      "m=audio 8000 RTP/AVP 0\r\n";

    CHECK (run (msg, &m) == BPA_SIP_PARSED,
	   "a sips: Contact and a spaced-out c= line refused the message");
    it = item_of (&m, BPA_SIP_CONTACT, 0);
    span_is ("sips Contact host", msg, it, "100.64.0.5:5061");
    CHECK (it && it->addr == PRIV && it->port == 5061,
	   "the sips: Contact parsed as %08x:%u", it ? it->addr : 0,
	   it ? it->port : 0);
    it = item_of (&m, BPA_SIP_CONN, 0);
    span_is ("spaced c= address", msg, it, "100.64.0.5");
    CHECK (it && it->addr == PRIV, "the spaced c= parsed as %08x",
	   it ? it->addr : 0);
    it = item_of (&m, BPA_SIP_MEDIA, 0);
    CHECK (it && it->port == 8000 && it->addr == PRIV,
	   "the media port was not governed by the spaced c= line: %u at "
	   "%08x", it ? it->port : 0, it ? it->addr : 0);
  }

  /*
   * More spans than one message may contribute.
   *
   * bpa_sip_add()'s refusal, and the three places that check what it returned,
   * none of which had been executed. The bound is not arbitrary: the items
   * array is inline in the message structure, and bpa_alg.h's worst-case
   * growth calculation is BPA_SIP_MAX_ITEMS spans wide - so a message that
   * needed more and was half recorded would be rewritten past the space the
   * caller reserved for it.
   *
   * Contact headers are the filler because nothing bounds how many a REGISTER
   * may carry; the media sections have a bound of their own four lines up.
   * Every message here is refused WHOLE, which is the contract: half a
   * rewritten message is the one outcome worse than none.
   *
   * Each of the four cases puts the overflow on a different span kind, so the
   * three `if (bpa_sip_add (...))` arms are each the one that fires. The
   * Content-Length is computed from the body rather than typed, because a
   * message refused for a length that no longer matches its body is a message
   * refused for the wrong reason and would go on passing with the bound gone.
   */
  {
    char msg[4096];
    char body[256];
    u32 n;
    unsigned k, pos;

    for (pos = 0; pos < 4; pos++)
      {
	const char *what;
	u32 n_contacts;

	/*
	 * Items in header order: one Via, then the Contacts, then
	 * Content-Length, then the session c=, then the media m=. The count
	 * is chosen so that the span named below is the (BPA_SIP_MAX_ITEMS+1)
	 * th one.
	 */
	switch (pos)
	  {
	  case 0: what = "a Contact"; n_contacts = BPA_SIP_MAX_ITEMS; break;
	  case 1: what = "Content-Length"; n_contacts = BPA_SIP_MAX_ITEMS - 1;
	    break;
	  case 2: what = "an SDP c="; n_contacts = BPA_SIP_MAX_ITEMS - 2;
	    break;
	  default: what = "an SDP m="; n_contacts = BPA_SIP_MAX_ITEMS - 3;
	    break;
	  }

	body[0] = 0;
	if (pos >= 2)
	  snprintf (body, sizeof (body),
		    "v=0\r\nc=IN IP4 100.64.0.5\r\n"
		    "m=audio 8000 RTP/AVP 0\r\n");
	n = (u32) snprintf (msg, sizeof (msg), "REGISTER sip:8.8.8.8 SIP/2.0\r\n"
			    "Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n");
	for (k = 0; k < n_contacts; k++)
	  n += (u32) snprintf (msg + n, sizeof (msg) - n,
			       "Contact: <sip:%u@100.64.0.5:5060>\r\n", k);
	if (body[0])
	  n += (u32) snprintf (msg + n, sizeof (msg) - n,
			       "Content-Type: application/sdp\r\n");
	n += (u32) snprintf (msg + n, sizeof (msg) - n,
			     "Content-Length: %u\r\n\r\n",
			     (u32) strlen (body));
	snprintf (msg + n, sizeof (msg) - n, "%s", body);

	CHECK (run (msg, &m) == BPA_SIP_UNPARSED,
	       "a message whose %s span is the %uth was not refused whole",
	       what, BPA_SIP_MAX_ITEMS + 1);
      }

    /* The control: one Contact fewer than the first case is exactly
     * BPA_SIP_MAX_ITEMS spans - one Via, twelve Contacts and a
     * Content-Length - and must parse, so the four above are about the bound
     * rather than about a message this parser dislikes for some other
     * reason. */
    n = (u32) snprintf (msg, sizeof (msg), "REGISTER sip:8.8.8.8 SIP/2.0\r\n"
			"Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z1\r\n");
    for (k = 0; k + 2 < BPA_SIP_MAX_ITEMS; k++)
      n += (u32) snprintf (msg + n, sizeof (msg) - n,
			   "Contact: <sip:%u@100.64.0.5:5060>\r\n", k);
    snprintf (msg + n, sizeof (msg) - n, "Content-Length: 0\r\n\r\n");
    CHECK (run (msg, &m) == BPA_SIP_PARSED,
	   "a message with exactly %u spans was refused", BPA_SIP_MAX_ITEMS);
    CHECK (m.n_items == BPA_SIP_MAX_ITEMS,
	   "the control message contributed %u spans, not %u, so the counts "
	   "above are not landing where they are meant to", m.n_items,
	   BPA_SIP_MAX_ITEMS);
  }

  /* ------------------------------------------------------------------
   * #472: the parser's pieces, called by name. Every one was reached only
   * through bpa_sip_parse(), so a piece that misread its input and was
   * rescued by another would pass; each is held to its own contract here.
   * ------------------------------------------------------------------ */
  printf ("\n=== the parser's pieces, one at a time ===\n");
  {
    const u8 *via = (const u8 *) "SIP/2.0/UDP 10.0.0.5:5060;branch=z9hG4bK1";
    const u8 *con = (const u8 *) "<sip:alice@10.0.0.5:5062>;expires=60";
    const u8 *star = (const u8 *) "*";
    const u8 *sdp = (const u8 *) "v=0\r\nc=IN IP4 10.0.0.5\r\n"
				 "m=audio 49170 RTP/AVP 0\r\n";
    const u8 *name = (const u8 *) "10.0.0.1.example.com";
    bpa_sip_msg_t m;
    u32 addr, v;
    u16 port;

    CHECK (bpa_sip_ci ((const u8 *) "cOnTaCt", "contact", 7)
	     && !bpa_sip_ci ((const u8 *) "contacx", "contact", 7),
	   "bpa_sip_ci: header names are compared case-blind, and only so");
    CHECK (bpa_sip_is_ws (' ') && bpa_sip_is_ws ('\t')
	     && !bpa_sip_is_ws ('\r'),
	   "bpa_sip_is_ws: space and tab, and not CR");
    CHECK (bpa_sip_is_digit ('0') && bpa_sip_is_digit ('9')
	     && !bpa_sip_is_digit ('a') && !bpa_sip_is_digit ('/'),
	   "bpa_sip_is_digit: the ten digits and nothing else");

    CHECK (bpa_sip_ip4 ((const u8 *) "10.0.0.5:5060", 13, &addr) == 8
	     && addr == 0x0a000005u,
	   "bpa_sip_ip4: 10.0.0.5 followed by a port is 8 bytes, 10.0.0.5");
    CHECK (bpa_sip_ip4 (name, (u32) strlen ((const char *) name), &addr) == 0,
	   "bpa_sip_ip4: a host NAME whose first labels look numeric was read "
	   "as an address, and would be rewritten out of the middle of a domain");
    CHECK (bpa_sip_num ((const u8 *) "5060;", 5, 65535, &v) == 4 && v == 5060,
	   "bpa_sip_num: 5060 is four bytes");
    CHECK (bpa_sip_num ((const u8 *) "70000", 5, 65535, &v) == 0,
	   "bpa_sip_num: a value past its bound was taken");
    CHECK (bpa_sip_num ((const u8 *) ";", 1, 65535, &v) == 0,
	   "bpa_sip_num: no digits was a number");
    CHECK (bpa_sip_eol ((const u8 *) "ab\r\ncd", 6, 0) == 2,
	   "bpa_sip_eol: the CR of the CRLF");
    CHECK (bpa_sip_eol ((const u8 *) "ab\ncd", 5, 0) == ~0u,
	   "bpa_sip_eol: a bare LF was a line ending; the offsets after it "
	   "would be one byte off");

    CHECK (bpa_sip_hostport (via, 12, (u32) strlen ((const char *) via), &addr,
			     &port) == 13
	     && addr == 0x0a000005u && port == 5060,
	   "bpa_sip_hostport: 10.0.0.5:5060 is 13 bytes, the address and 5060");

    memset (&m, 0, sizeof (m));
    CHECK (bpa_sip_via (via, 0, (u32) strlen ((const char *) via), &m) == 0
	     && m.n_items == 1 && m.items[0].kind == BPA_SIP_VIA
	     && m.items[0].addr == 0x0a000005u && m.items[0].port == 5060
	     && m.items[0].off == 12 && m.items[0].len == 13,
	   "bpa_sip_via: the sent-by span is bytes 12-24, 10.0.0.5:5060");

    memset (&m, 0, sizeof (m));
    CHECK (bpa_sip_contact (con, 0, (u32) strlen ((const char *) con), &m) == 0
	     && m.n_items == 1 && m.items[0].kind == BPA_SIP_CONTACT
	     && m.items[0].addr == 0x0a000005u && m.items[0].port == 5062,
	   "bpa_sip_contact: the URI's host and port");
    memset (&m, 0, sizeof (m));
    CHECK (bpa_sip_contact (star, 0, 1, &m) == 0 && m.n_items == 0,
	   "bpa_sip_contact: a REGISTER's '*' names no place, and recorded one");

    memset (&m, 0, sizeof (m));
    CHECK (bpa_sip_sdp (sdp, 0, (u32) strlen ((const char *) sdp), &m) == 0,
	   "bpa_sip_sdp refused a plain offer");
    {
      u32 k, conn = 0, media = 0;
      for (k = 0; k < m.n_items; k++)
	{
	  if (m.items[k].kind == BPA_SIP_CONN && m.items[k].addr == 0x0a000005u)
	    conn++;
	  if (m.items[k].kind == BPA_SIP_MEDIA && m.items[k].port == 49170)
	    media++;
	}
      CHECK (conn == 1 && media == 1,
	     "bpa_sip_sdp: the c= and the m= came out as %u and %u items", conn,
	     media);
    }

    memset (&m, 0, sizeof (m));
    for (v = 0; v < BPA_SIP_MAX_ITEMS; v++)
      bpa_sip_add (&m, BPA_SIP_VIA, v, 1, 0, 0);
    CHECK (bpa_sip_add (&m, BPA_SIP_VIA, 99, 1, 0, 0) != 0
	     && m.n_items == BPA_SIP_MAX_ITEMS,
	   "bpa_sip_add: an item past BPA_SIP_MAX_ITEMS was taken");
    printf ("  each piece of the parser holds its own contract\n");
  }

  /* #472: the two rules the translation nodes apply to a parsed message. */
  printf ("\n=== what a rewrite could grow by; whose Via comes back ===\n");
  {
    bpa_sip_msg_t m;
    u32 clen_at, want;

    memset (&m, 0, sizeof (m));
    bpa_sip_add (&m, BPA_SIP_VIA, 10, 18, PRIV, 5060);	   /* ours */
    bpa_sip_add (&m, BPA_SIP_CONTACT, 40, 18, 0x0a000009u, 5060); /* not */
    bpa_sip_add (&m, BPA_SIP_CLEN, 80, 3, 0, 0);
    bpa_sip_add (&m, BPA_SIP_CONN, 120, 10, PRIV, 0);
    bpa_sip_add (&m, BPA_SIP_MEDIA, 150, 4, PRIV, 8000);
    want = bpa_sip_growth (&m, PRIV, &clen_at);
    CHECK (want == (BPA_SIP_TXT_MAX - 18) + (5 - 3) + (15 - 10) + (5 - 4),
	   "bpa_sip_growth: %u bytes, not the widest each of ours can become",
	   want);
    CHECK (clen_at == 2, "bpa_sip_growth: Content-Length is item %u", clen_at);
    CHECK (bpa_sip_growth (&m, 0x0a0000ffu, &clen_at) == 2,
	   "bpa_sip_growth: another subscriber's items were counted");
    memset (&m, 0, sizeof (m));
    bpa_sip_add (&m, BPA_SIP_VIA, 10, 21, PRIV, 5060);
    CHECK (bpa_sip_growth (&m, PRIV, &clen_at) == 0 && clen_at == ~0u,
	   "bpa_sip_growth: a Via already at the widest grows, or a missing "
	   "Content-Length was found");

    memset (&m, 0, sizeof (m));
    m.is_response = 1;
    bpa_sip_add (&m, BPA_SIP_VIA, 10, 18, PUB, 1024);
    bpa_sip_add (&m, BPA_SIP_VIA, 40, 18, PUB, 2048);
    CHECK (bpa_sip_our_via (&m, PUB, 1024) == 0,
	   "bpa_sip_our_via: the topmost Via this box wrote was not found - "
	   "the phone drops every response");
    CHECK (bpa_sip_our_via (&m, PUB, 2048) == ~0u,
	   "bpa_sip_our_via: a Via below the topmost was put back");
    CHECK (bpa_sip_our_via (&m, PUB, 1025) == ~0u,
	   "bpa_sip_our_via: another session's port on our address was taken");
    m.is_response = 0;
    CHECK (bpa_sip_our_via (&m, PUB, 1024) == ~0u,
	   "bpa_sip_our_via: a request's Via was rewritten");
    printf ("  growth counted before a port is taken; only our Via back\n");
  }

  return bpa_test_verdict (checks, failures);
}
