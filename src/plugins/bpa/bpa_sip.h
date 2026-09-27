/*
 * bpa_sip.h - SIP message parsing for the ALG (RFC 3261), and the SDP body it
 * carries (RFC 4566).
 *
 * UDP ONLY, and that is a decision rather than an omission. See the note on
 * transports below.
 *
 * Pure byte arithmetic over a datagram payload, deliberately free of VPP types
 * so it builds either inside the plugin or standalone against the test shim,
 * for the same reason bpa_rtsp.h is: the parser is the half of an ALG that
 * decides whether to touch a payload at all, and a parser that can only be
 * exercised with a running VPP does not get the number of cases it needs.
 *
 * SIP is a large protocol and this file understands a small part of it. What
 * keeps that honest is one rule, applied everywhere:
 *
 *   THIS PARSER LOCATES SPANS. IT DECIDES NOTHING.
 *
 * It reports where an address or a port sits and what it reads there, and the
 * caller rewrites a span only when the address in it is the subscriber's own
 * private address. Everything else is left exactly as it arrived. That single
 * rule is what makes the ALG safe in the three places SIP behind NAT usually
 * goes wrong:
 *
 *  - a message whose top Via belongs to somebody else. A RESPONSE the
 *    subscriber sends carries the CALLER's Via, and rewriting it would send
 *    the caller's own responses into this box. The address does not match, so
 *    it is not touched, and no direction flag was needed to work that out.
 *  - a CPE that already knows its public address, from STUN or from a
 *    previous rport. Its Via and its SDP name the public address; they do not
 *    match the private one; nothing is rewritten and the two NAT traversals
 *    do not fight. This is the failure mode operators know as "the ALG broke
 *    my phone", and it is designed out here rather than documented.
 *  - a header this file half-understands. It does not exist: a message whose
 *    syntax does not come apart cleanly is refused WHOLE, counted, and passed
 *    through untouched, exactly as bpa_ftp.c and bpa_rtsp.h both state.
 *
 * What it looks at, and nothing else:
 *
 *   INVITE sip:2000@8.8.8.8 SIP/2.0\r\n
 *   Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z9hG4bKnashds8\r\n   <- sent-by
 *   From: <sip:1000@8.8.8.8>;tag=abc\r\n
 *   To: <sip:2000@8.8.8.8>\r\n
 *   Call-ID: a84b4c76e66710\r\n
 *   CSeq: 1 INVITE\r\n
 *   Contact: <sip:1000@100.64.0.5:5060>\r\n                      <- URI host
 *   Content-Type: application/sdp\r\n
 *   Content-Length: 142\r\n                                      <- recomputed
 *   \r\n
 *   v=0\r\n
 *   o=- 1 1 IN IP4 100.64.0.5\r\n
 *   c=IN IP4 100.64.0.5\r\n                                      <- media addr
 *   m=audio 8000 RTP/AVP 0\r\n                                   <- media port
 *
 * What it deliberately does NOT look at, each with its reason:
 *
 *   From, To          Addresses of record - identities, not places. They are
 *                     covered by the digest authentication a registrar
 *                     computes over the message, and they are what a dialog
 *                     is matched by. Rewriting them breaks registration and
 *                     dialog matching to fix nothing: nobody sends a packet
 *                     to a From header.
 *   Call-ID           Often contains the host part. It is an opaque dialog
 *                     identifier and both ends compare it byte for byte.
 *   Route,            Proxy addresses. They belong to the operator's SBC or
 *   Record-Route      the far end, never to the subscriber, and a proxy that
 *                     put its own address there did so knowing where it is.
 *   Request-URI       Names the callee, outbound. Inbound it names this box's
 *                     public address, which a strict UA could reject - see
 *                     the note in bpa_alg.h. Left alone rather than guessed.
 *   o= (SDP origin)   Carries an address that is documentation, not a
 *                     destination: RFC 4566 s5.2 makes it part of the session
 *                     identifier, and some stacks compare it across re-INVITEs
 *                     to decide whether the session changed. Rewriting it can
 *                     turn a re-INVITE into a new session.
 *   a=rtcp:           Would let RTCP sit somewhere other than port+1. Reading
 *                     it without being able to reserve that exact port is
 *                     worse than not reading it, and the pair this ALG
 *                     reserves is always consecutive.
 *   received=,        The far end telling the client what address and port it
 *   rport=            was actually seen from. That is TRUE, and it is how a
 *                     CPE discovers it is behind NAT. Rewriting it to the
 *                     private address would replace a fact with a lie.
 *
 * Transports. This ALG binds to UDP/5060 only.
 *
 * SIP over TCP is legal, is what large messages fall back to (RFC 3261 s18.1.1
 * requires it above the path MTU), and is NOT handled here. Two reasons, and
 * neither is effort:
 *
 *  - A TCP SIP message is a stream, not a datagram. A parser that can only see
 *    one segment cannot see the end of a message, and an INVITE with SDP plus
 *    a few Route headers is regularly larger than one segment - so the honest
 *    TCP answer is reassembly, which is a different piece of work with a
 *    different memory story. RTSP gets away with one segment because its
 *    requests are small; SIP would refuse the very messages it exists for.
 *  - SIP over TLS (sips:, 5061) cannot be touched by anything, and a UA
 *    configured for TCP is usually one configured for TLS.
 *
 * A UDP-only ALG that says so is better than a TCP one that is half done. A
 * TCP session to port 5060 is therefore never inspected: it is translated like
 * any other connection, which is what happens today.
 */

#ifndef __included_bpa_sip_h__
#define __included_bpa_sip_h__

#ifdef BPA_STANDALONE
#include "bpa_shim.h"
#else
#include <vlib/vlib.h>
#endif

/*
 * The IANA port for SIP over UDP. 5061 is SIP over TLS and there is nothing in
 * it to read; other ports are configuration, and an operator who moved SIP
 * needs a knob rather than this parser sniffing for something that looks like
 * a start line.
 */
#define BPA_SIP_PORT 5060

/* "255.255.255.255:65535" - the widest thing this file ever writes. */
#define BPA_SIP_TXT_MAX 21

/*
 * Media streams in one SDP body that this ALG will map.
 *
 * Four covers audio, video and the two directions of a call being renegotiated
 * at once. Each one costs a reserved port PAIR out of a 512-port block, so this
 * is also the bound on what one malformed offer can take from a subscriber.
 * An offer with more than four is refused whole rather than half-mapped.
 */
#define BPA_SIP_MAX_MEDIA 4

/*
 * Spans one message may contribute: one Via, two Contacts, one Content-Length,
 * and up to BPA_SIP_MAX_MEDIA each of c= and m= plus the session-level c=.
 * A message that needs more is refused, so this number is also the worst-case
 * growth calculation in bpa_alg.h.
 */
#define BPA_SIP_MAX_ITEMS 14

typedef enum
{
  /* Not a SIP message. Nothing here to do and nothing wrong: something else
   * on port 5060, or the first scan of the day. */
  BPA_SIP_NONE = 0,
  BPA_SIP_PARSED,   /* it came apart cleanly; see n_items */
  BPA_SIP_UNPARSED, /* it is SIP and we did not understand it */
} bpa_sip_verdict_t;

typedef enum
{
  BPA_SIP_VIA = 0,  /* the topmost Via sent-by, "host" or "host:port" */
  BPA_SIP_CONTACT,  /* a Contact URI host, "host" or "host:port" */
  BPA_SIP_CONN,	    /* the address of an SDP "c=IN IP4 ..." line */
  BPA_SIP_MEDIA,    /* the port of an SDP "m=" line */
  BPA_SIP_CLEN,	    /* the digits of Content-Length */
} bpa_sip_item_kind_t;

typedef struct
{
  u32 off; /* first byte of the span, within the payload */
  u32 len; /* bytes the span covers */
  /*
   * The literal IPv4 address this span is about, in host order, or 0.
   *
   * Zero for a host that is not a dotted quad - a name, an IPv6 literal - and
   * for a CLEN item, which is about no address at all. The caller rewrites a
   * span only when this equals the address the session belongs to, so zero
   * means "left alone" without any further rule.
   *
   * For a MEDIA item this is the connection address that GOVERNS the stream:
   * the media-level c= when the section has one, otherwise the session-level
   * c=. A port is only ours to map when the address it is announced at is.
   */
  u32 addr;
  /*
   * VIA and CONTACT: the sent-by port, 0 when the header did not carry one.
   * MEDIA: the private media port, which is what the pair is reserved for.
   */
  u16 port;
  u8 kind;
} bpa_sip_item_t;

typedef struct
{
  bpa_sip_item_t items[BPA_SIP_MAX_ITEMS];
  u32 n_items;
  u32 body_off; /* first byte of the body; == len when there is none */
  u32 body_len;
  u8 is_response; /* a status line rather than a request line */
} bpa_sip_msg_t;

/*
 * One replacement, ready to apply. Collected before anything is written,
 * because the media ports have to be reserved before their text exists and a
 * payload half rewritten is the one outcome worse than not rewriting at all.
 */
typedef struct
{
  u32 off;
  u32 old_len;
  u8 len;
  u8 txt[BPA_SIP_TXT_MAX];
} bpa_sip_edit_t;

/* Case-insensitive compare of n bytes against a literal. SIP header names,
 * parameter names and the SDP token "IN IP4" are all case-insensitive, and
 * real user agents use every spelling. */
static_always_inline int
bpa_sip_ci (const u8 *p, const char *lit, u32 n)
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
bpa_sip_is_ws (u8 c)
{
  return c == ' ' || c == '\t';
}

static_always_inline int
bpa_sip_is_digit (u8 c)
{
  return c >= '0' && c <= '9';
}

/*
 * A dotted quad and nothing else.
 *
 * Returns the number of bytes consumed, or 0. The byte that follows must not
 * be a digit or a dot: "10.0.0.1.example.com" is a host NAME whose first
 * labels happen to look numeric, and reading it as an address would rewrite
 * four characters out of the middle of somebody's domain.
 */
static_always_inline u32
bpa_sip_ip4 (const u8 *p, u32 len, u32 *addr)
{
  u32 v = 0, i = 0, k;

  for (k = 0; k < 4; k++)
    {
      u32 o = 0, nd = 0;

      if (k)
	{
	  if (i >= len || p[i] != '.')
	    return 0;
	  i++;
	}
      while (i < len && bpa_sip_is_digit (p[i]) && nd < 3)
	{
	  o = o * 10 + (u32) (p[i] - '0');
	  i++;
	  nd++;
	}
      if (nd == 0 || o > 255)
	return 0;
      v = (v << 8) | o;
    }

  if (i < len && (bpa_sip_is_digit (p[i]) || p[i] == '.'))
    return 0;

  *addr = v;
  return i;
}

/* Decimal, bounded. Returns bytes consumed, 0 when there are no digits or the
 * value does not fit what the caller asked for. */
static_always_inline u32
bpa_sip_num (const u8 *p, u32 len, u32 max, u32 *out)
{
  u32 v = 0, i = 0;

  while (i < len && bpa_sip_is_digit (p[i]))
    {
      v = v * 10 + (u32) (p[i] - '0');
      if (v > max)
	return 0;
      i++;
    }
  if (i == 0)
    return 0;
  *out = v;
  return i;
}

/* Decimal into `out`, returning its length. Never more than 10 bytes. */
static_always_inline u32
bpa_sip_fmt_u32 (u8 *out, u32 v)
{
  u8 tmp[10];
  u32 d = 0, i;

  do
    {
      tmp[d++] = (u8) ('0' + v % 10);
      v /= 10;
    }
  while (v);
  for (i = 0; i < d; i++)
    out[i] = tmp[d - 1 - i];
  return d;
}

/* "a.b.c.d", host order in. At most 15 bytes. */
static_always_inline u32
bpa_sip_fmt_addr (u8 *out, u32 addr)
{
  u32 n = 0, k;

  for (k = 0; k < 4; k++)
    {
      if (k)
	out[n++] = '.';
      n += bpa_sip_fmt_u32 (out + n, (addr >> (24 - 8 * k)) & 0xff);
    }
  return n;
}

/*
 * "a.b.c.d:port", always with the port.
 *
 * Always, even when the header this replaces carried no port. A Via or Contact
 * that names the public address without a port names port 5060 on it, and this
 * box maps no such thing - the response or the in-dialog request would arrive
 * at a port belonging to nobody, which is the exact failure the ALG exists to
 * remove. The cost is that a UA which wrote no port gets one back on the way
 * in, so a byte-for-byte comparison of its own top Via fails; RFC 3261 s17.1.3
 * matches responses on the branch parameter, which is untouched, and every
 * in-kernel SIP ALG makes the same trade.
 */
static_always_inline u32
bpa_sip_fmt_addr_port (u8 *out, u32 addr, u16 port)
{
  u32 n = bpa_sip_fmt_addr (out, addr);

  out[n++] = ':';
  return n + bpa_sip_fmt_u32 (out + n, port);
}

/* Index of the CR of the next CRLF at or after `from`, or ~0. A bare LF is
 * not a line ending here: RFC 3261 s7 is CRLF, and accepting half of it in a
 * parser that computes offsets is how a length ends up one byte wrong. */
static_always_inline u32
bpa_sip_eol (const u8 *pl, u32 len, u32 from)
{
  u32 i;

  for (i = from; i + 1 < len; i++)
    if (pl[i] == '\r' && pl[i + 1] == '\n')
      return i;
  return ~0u;
}

static_always_inline int
bpa_sip_add (bpa_sip_msg_t *m, u8 kind, u32 off, u32 len, u32 addr, u16 port)
{
  bpa_sip_item_t *it;

  if (m->n_items >= BPA_SIP_MAX_ITEMS)
    return -1;
  it = &m->items[m->n_items++];
  it->kind = kind;
  it->off = off;
  it->len = len;
  it->addr = addr;
  it->port = port;
  return 0;
}

/*
 * A "host[:port]" span, as it appears in a Via sent-by or a Contact URI.
 *
 * `end` bounds the enclosing value. Returns the length of the span, and sets
 * *addr to 0 when the host is not a dotted quad - a name, or an IPv6 literal -
 * which is not an error and not something this ALG acts on.
 *
 * Returns 0 when there is no host at all, which the caller treats as a header
 * it did not understand.
 */
static_always_inline u32
bpa_sip_hostport (const u8 *pl, u32 start, u32 end, u32 *addr, u16 *port)
{
  u32 i = start, n;

  *addr = 0;
  *port = 0;

  if (i >= end)
    return 0;

  /*
   * An IPv6 reference. Recorded as "a host we do not act on" rather than
   * refused: an IPv6 SIP endpoint reached over IPv4 is somebody else's
   * arrangement, and there is no address of ours in it to fix.
   */
  if (pl[i] == '[')
    {
      while (i < end && pl[i] != ']')
	i++;
      if (i >= end)
	return 0;
      i++;
    }
  else
    {
      n = bpa_sip_ip4 (pl + i, end - i, addr);
      if (n)
	i += n;
      else
	{
	  /* A host name. Ends at the first character that cannot be in one. */
	  u32 h0 = i;

	  while (i < end && pl[i] != ':' && pl[i] != ';' && pl[i] != ','
		 && pl[i] != '>' && pl[i] != '?' && pl[i] != '/'
		 && !bpa_sip_is_ws (pl[i]))
	    i++;
	  if (i == h0)
	    return 0;
	}
    }

  if (i < end && pl[i] == ':')
    {
      u32 v;

      n = bpa_sip_num (pl + i + 1, end - i - 1, 65535, &v);
      if (n == 0 || v == 0)
	return 0; /* a colon that introduces no port is not a sent-by */
      i += 1 + n;
      *port = (u16) v;
    }

  return i - start;
}

/*
 * The topmost Via's sent-by.
 *
 * `v0`..`vend` bound the header value. Only the first comma-separated element
 * matters: that is the Via this box would have inserted, and every one after
 * it belongs to a proxy further along.
 *
 * Returns 0 on success, -1 when the value is not a shape this file knows - in
 * which case the whole message is refused, because a Via is the one header a
 * response has to come back through and getting it wrong is not recoverable
 * further down the message.
 */
static_always_inline int
bpa_sip_via (const u8 *pl, u32 v0, u32 vend, bpa_sip_msg_t *m)
{
  u32 i = v0, span, addr;
  u16 port;

  while (i < vend && bpa_sip_is_ws (pl[i]))
    i++;

  /* "SIP/2.0/" then the transport token. The version digits are not pinned:
   * the sent-by syntax is the same either side of a version bump. */
  if (i + 8 > vend || !bpa_sip_ci (pl + i, "sip/", 4)
      || !bpa_sip_is_digit (pl[i + 4]) || pl[i + 5] != '.'
      || !bpa_sip_is_digit (pl[i + 6]) || pl[i + 7] != '/')
    return -1;
  i += 8;

  /*
   * The transport has to be the one the datagram arrived on. A Via naming TCP
   * or TLS on a UDP datagram is either a UA this ALG must not touch - it will
   * be listening somewhere this box has not mapped - or a crafted message, and
   * neither is a thing to guess at.
   */
  if (i + 3 > vend || !bpa_sip_ci (pl + i, "udp", 3))
    return -1;
  i += 3;
  if (i < vend && !bpa_sip_is_ws (pl[i]))
    return -1;
  while (i < vend && bpa_sip_is_ws (pl[i]))
    i++;

  /* The sent-by ends at the first parameter, the next Via, or the line. */
  {
    u32 e = i;

    while (e < vend && pl[e] != ';' && pl[e] != ',' && !bpa_sip_is_ws (pl[e]))
      e++;
    span = bpa_sip_hostport (pl, i, e, &addr, &port);
    if (span == 0 || i + span != e)
      return -1;
  }

  return bpa_sip_add (m, BPA_SIP_VIA, i, span, addr, port);
}

/*
 * A Contact header's URI host.
 *
 * "*" is legal in a REGISTER that clears every binding and names no place;
 * nothing is recorded for it. Anything else must come apart into a URI whose
 * host this file can find, or the message is refused.
 */
static_always_inline int
bpa_sip_contact (const u8 *pl, u32 v0, u32 vend, bpa_sip_msg_t *m)
{
  u32 i = v0, uend = vend, span, addr;
  u16 port;

  while (i < vend && bpa_sip_is_ws (pl[i]))
    i++;
  if (i >= vend)
    return -1;
  if (pl[i] == '*')
    return 0;

  /* A display name in quotes may contain anything, angle brackets included. */
  if (pl[i] == '"')
    {
      i++;
      while (i < vend && pl[i] != '"')
	i += (pl[i] == '\\' && i + 1 < vend) ? 2 : 1;
      if (i >= vend)
	return -1;
      i++;
      while (i < vend && bpa_sip_is_ws (pl[i]))
	i++;
    }

  if (i < vend && pl[i] == '<')
    {
      i++;
      uend = i;
      while (uend < vend && pl[uend] != '>')
	uend++;
      if (uend >= vend)
	return -1;
    }
  else
    {
      /*
       * No angle brackets. RFC 3261 s20.10: the URI then runs to the end of
       * the value, and a ";" belongs to the URI rather than to the header -
       * which does not matter here, because the host ends before either.
       */
      while (i + 3 <= vend && !bpa_sip_ci (pl + i, "sip", 3))
	{
	  if (pl[i] == ',')
	    return -1; /* a display name we did not recognise */
	  i++;
	}
      uend = vend;
    }

  if (i + 4 > uend || !bpa_sip_ci (pl + i, "sip", 3))
    return -1;
  i += 3;
  if (pl[i] == 's')
    i++;
  if (i >= uend || pl[i] != ':')
    return -1;
  i++;

  /* userinfo, when there is one. It ends at the "@" that introduces the host,
   * and an "@" cannot appear in the host part of a SIP URI. */
  {
    u32 at = i, h = i;

    while (at < uend && pl[at] != ';' && pl[at] != '?')
      {
	if (pl[at] == '@')
	  h = at + 1;
	at++;
      }
    i = h;
  }

  {
    u32 e = i;

    while (e < uend && pl[e] != ';' && pl[e] != '?' && pl[e] != ','
	   && !bpa_sip_is_ws (pl[e]))
      e++;
    span = bpa_sip_hostport (pl, i, e, &addr, &port);
    if (span == 0 || i + span != e)
      return -1;
  }

  return bpa_sip_add (m, BPA_SIP_CONTACT, i, span, addr, port);
}

/*
 * The SDP body: the connection addresses and the media ports.
 *
 * Line endings are read leniently here and strictly above. SDP is a different
 * grammar carried inside SIP, RFC 4566 s5 asks parsers to tolerate a bare LF,
 * and tolerating it costs nothing because no span this function records
 * crosses a line ending - the offsets it produces are inside a line either
 * way. The SIP header block is strict because its offsets ARE its line
 * endings.
 */
static_always_inline int
bpa_sip_sdp (const u8 *pl, u32 b0, u32 bend, bpa_sip_msg_t *m)
{
  u32 i = b0, n_media = 0;
  u32 sess_conn = 0;
  u32 media_item[BPA_SIP_MAX_MEDIA];
  u32 media_conn[BPA_SIP_MAX_MEDIA];
  u32 k;

  while (i < bend)
    {
      u32 e = i, line_end;

      while (e < bend && pl[e] != '\n')
	e++;
      line_end = (e > i && pl[e - 1] == '\r') ? e - 1 : e;

      if (line_end >= i + 2 && pl[i + 1] == '=')
	{
	  if (pl[i] == 'c')
	    {
	      u32 j = i + 2, addr = 0, span;

	      while (j < line_end && bpa_sip_is_ws (pl[j]))
		j++;
	      /* "IN IP4 <addr>". IP6 is somebody else's network layer and
	       * there is nothing of ours in it. */
	      if (j + 7 <= line_end && bpa_sip_ci (pl + j, "in", 2)
		  && bpa_sip_is_ws (pl[j + 2]))
		{
		  j += 3;
		  while (j < line_end && bpa_sip_is_ws (pl[j]))
		    j++;
		  if (j + 4 <= line_end && bpa_sip_ci (pl + j, "ip4", 3)
		      && bpa_sip_is_ws (pl[j + 3]))
		    {
		      j += 4;
		      while (j < line_end && bpa_sip_is_ws (pl[j]))
			j++;
		      span = bpa_sip_ip4 (pl + j, line_end - j, &addr);
		      /* A "/" after the address is a multicast TTL or a range
		       * (RFC 4566 s5.7). Neither is a unicast destination this
		       * box could map, so it is left alone. */
		      if (span && j + span == line_end)
			{
			  if (n_media == 0)
			    sess_conn = addr;
			  else
			    media_conn[n_media - 1] = addr;
			  if (bpa_sip_add (m, BPA_SIP_CONN, j, span, addr, 0))
			    return -1;
			}
		    }
		}
	    }
	  else if (pl[i] == 'm')
	    {
	      u32 j = i + 2, v = 0, span;

	      if (n_media >= BPA_SIP_MAX_MEDIA)
		return -1;
	      media_item[n_media] = ~0u;
	      media_conn[n_media] = ~0u;
	      n_media++;

	      /* the media token, then the port */
	      while (j < line_end && !bpa_sip_is_ws (pl[j]))
		j++;
	      while (j < line_end && bpa_sip_is_ws (pl[j]))
		j++;
	      span = bpa_sip_num (pl + j, line_end - j, 65535, &v);
	      /*
	       * A "<port>/<n>" range names several streams and this box
	       * reserves one pair, so it is not mapped. A port of zero is a
	       * stream being REFUSED (RFC 3264 s6) and there is nothing to
	       * map either; rewriting it would re-offer a stream the endpoint
	       * has just turned down.
	       */
	      if (span && v != 0 && j + span < line_end
		  && bpa_sip_is_ws (pl[j + span]))
		{
		  media_item[n_media - 1] = m->n_items;
		  if (bpa_sip_add (m, BPA_SIP_MEDIA, j, span, 0, (u16) v))
		    return -1;
		}
	    }
	}

      i = e + 1;
    }

  /* A media port is ours to map only when the address the stream is announced
   * at is ours: the media-level c= when the section has one, the session-level
   * c= otherwise. */
  for (k = 0; k < n_media; k++)
    if (media_item[k] != ~0u)
      m->items[media_item[k]].addr =
	(media_conn[k] != ~0u) ? media_conn[k] : sess_conn;

  return 0;
}

/*
 * Takes a SIP datagram apart and records every span the ALG could act on.
 *
 * BPA_SIP_NONE          not SIP; nothing to do and nothing wrong.
 * BPA_SIP_PARSED        it came apart cleanly. n_items may still be zero.
 * BPA_SIP_UNPARSED      it is SIP and some part of it did not come apart.
 *                       The caller MUST leave the packet untouched and count
 *                       it: half a rewritten message is the one outcome worse
 *                       than none.
 */
static_always_inline bpa_sip_verdict_t
bpa_sip_parse (const u8 *pl, u32 len, bpa_sip_msg_t *m)
{
  u32 i, eol;
  u32 clen = ~0u;
  int ct_is_sdp = 0, have_via = 0;

  m->n_items = 0;
  m->body_off = len;
  m->body_len = 0;
  m->is_response = 0;

  eol = bpa_sip_eol (pl, len, 0);
  if (eol == ~0u || eol < 8)
    return BPA_SIP_NONE;

  /*
   * Which of the two start lines this is, and no third answer.
   *
   * A request is recognised by its trailing " SIP/2.0" rather than by a table
   * of method names. SIP has twenty-odd methods across a dozen RFCs and a
   * table would silently stop recognising the next one; the version token is
   * mandatory on every request there will ever be.
   */
  if (bpa_sip_ci (pl, "SIP/2.0 ", 8))
    m->is_response = 1;
  else if (!bpa_sip_ci (pl + eol - 8, " SIP/2.0", 8))
    return BPA_SIP_NONE;

  i = eol + 2;

  for (;;)
    {
      u32 n0 = i, ncolon, v0, vend;

      eol = bpa_sip_eol (pl, len, i);
      if (eol == ~0u)
	return BPA_SIP_UNPARSED; /* the header block does not end in here */
      if (eol == i)
	{
	  i += 2; /* the blank line: the body starts here */
	  break;
	}

      /*
       * A folded continuation line. Legal (RFC 3261 s7.3.1), essentially
       * unseen, and not handled: the value would continue past where this
       * loop stops looking, so refusing is the only answer that cannot
       * corrupt a header.
       *
       * Kept even though the HCOLON rule below refuses almost every fold on
       * its own - a continuation begins with whitespace, so it has no header
       * name before its colon. Almost: a continuation of the form "\t:x" gets
       * past that rule as a nameless header and would leave the folded value
       * above it half read. This check is the one that states the rule, and
       * the unit test asserts exactly that shape so it is not redundant in
       * the only way that would matter.
       */
      if (eol + 2 < len && bpa_sip_is_ws (pl[eol + 2]))
	return BPA_SIP_UNPARSED;

      /* HCOLON: the name, optional whitespace, then ":" (RFC 3261 s25.1). */
      ncolon = n0;
      while (ncolon < eol && !bpa_sip_is_ws (pl[ncolon]) && pl[ncolon] != ':')
	ncolon++;
      {
	u32 c = ncolon;

	while (c < eol && bpa_sip_is_ws (pl[c]))
	  c++;
	if (c >= eol || pl[c] != ':')
	  return BPA_SIP_UNPARSED;
	v0 = c + 1;
      }
      vend = eol;

      {
	u32 nlen = ncolon - n0;

	/* The compact forms are not optional to support: a UA that uses them
	 * uses them for everything, and not recognising "m:" means not
	 * rewriting the one header that says where to reach the subscriber. */
	if ((nlen == 3 && bpa_sip_ci (pl + n0, "via", 3))
	    || (nlen == 1 && bpa_sip_ci (pl + n0, "v", 1)))
	  {
	    /* Only the topmost. The rest belong to proxies. */
	    if (!have_via)
	      {
		have_via = 1;
		if (bpa_sip_via (pl, v0, vend, m))
		  return BPA_SIP_UNPARSED;
	      }
	  }
	else if ((nlen == 7 && bpa_sip_ci (pl + n0, "contact", 7))
		 || (nlen == 1 && bpa_sip_ci (pl + n0, "m", 1)))
	  {
	    if (bpa_sip_contact (pl, v0, vend, m))
	      return BPA_SIP_UNPARSED;
	  }
	else if ((nlen == 14 && bpa_sip_ci (pl + n0, "content-length", 14))
		 || (nlen == 1 && bpa_sip_ci (pl + n0, "l", 1)))
	  {
	    u32 j = v0, span, v;

	    /*
	     * A SECOND Content-Length is refused rather than resolved.
	     *
	     * Two of them disagreeing means this box and the next proxy along
	     * would each read a different body out of the same datagram, and
	     * whichever one this file rewrote would be the one the other did
	     * not use. There is no correct choice to make, so no choice is
	     * made.
	     */
	    if (clen != ~0u)
	      return BPA_SIP_UNPARSED;
	    while (j < vend && bpa_sip_is_ws (pl[j]))
	      j++;
	    span = bpa_sip_num (pl + j, vend - j, 0xffff, &v);
	    if (span == 0)
	      return BPA_SIP_UNPARSED;
	    clen = v;
	    if (bpa_sip_add (m, BPA_SIP_CLEN, j, span, 0, 0))
	      return BPA_SIP_UNPARSED;
	  }
	else if ((nlen == 12 && bpa_sip_ci (pl + n0, "content-type", 12))
		 || (nlen == 1 && bpa_sip_ci (pl + n0, "c", 1)))
	  {
	    u32 j = v0;

	    while (j < vend && bpa_sip_is_ws (pl[j]))
	      j++;
	    ct_is_sdp = (j + 15 <= vend
			 && bpa_sip_ci (pl + j, "application/sdp", 15));
	  }
      }

      i = eol + 2;
    }

  m->body_off = i;
  m->body_len = len - i;

  /*
   * The length the message declares has to be the length that arrived.
   *
   * Not pedantry: this is the bound on everything below. A Content-Length
   * that disagrees with the datagram is a truncated message or a crafted one,
   * and either way the body this parser would walk is not the body the
   * endpoints will read. A body with no Content-Length at all is the same
   * problem stated differently.
   */
  if (m->body_len)
    {
      if (clen != m->body_len)
	return BPA_SIP_UNPARSED;
      /*
       * A body that is not SDP: multipart, an ISUP payload, a dialog event
       * package - or one that arrived with no Content-Type at all, which RFC
       * 3261 s20.15 does not allow and which leaves nothing to decide from.
       * Refused WHOLE rather than passed with its headers rewritten: a
       * multipart body has an SDP part inside it that this file cannot reach,
       * so rewriting the signalling and not the media would deliver a call
       * with no audio and no counter to say why.
       *
       * The declared type decides, and the first three bytes of the body do
       * not. They are checked too, below, but only to catch a body that claims
       * to be SDP and is not - never to promote one that claims to be
       * something else.
       */
      if (!ct_is_sdp)
	return BPA_SIP_UNPARSED;
      if (m->body_len < 3 || !bpa_sip_ci (pl + m->body_off, "v=0", 3))
	return BPA_SIP_UNPARSED;
      if (bpa_sip_sdp (pl, m->body_off, len, m))
	return BPA_SIP_UNPARSED;
    }
  else if (clen != ~0u && clen != 0)
    return BPA_SIP_UNPARSED;

  return BPA_SIP_PARSED;
}

/*
 * Adds one replacement, keeping the list in ascending offset order.
 *
 * Sorted on insertion rather than sorted afterwards because the Content-Length
 * rewrite is discovered LAST - its value depends on how much the body grew -
 * and sits FIRST, among the headers. Returns -1 when the list is full, which
 * the caller turns into "left untouched".
 */
static_always_inline int
bpa_sip_edit_add (bpa_sip_edit_t *e, u32 *n, u32 off, u32 old_len,
		  const u8 *txt, u32 tlen)
{
  u32 i = *n, k;

  if (*n >= BPA_SIP_MAX_ITEMS || tlen > BPA_SIP_TXT_MAX)
    return -1;

  while (i > 0 && e[i - 1].off > off)
    {
      e[i] = e[i - 1];
      i--;
    }
  e[i].off = off;
  e[i].old_len = old_len;
  e[i].len = (u8) tlen;
  for (k = 0; k < tlen; k++)
    e[i].txt[k] = txt[k];
  (*n)++;
  return 0;
}

/*
 * Applies every replacement in one pass and returns the length change.
 *
 * Last to first. An edit's recorded offset is an offset into the payload as it
 * ARRIVED, and working backwards means no edit has moved when its turn comes.
 * Forwards, every offset after the first substitution of a different length is
 * wrong by the drift so far - which produces a message that still parses and
 * names the wrong things, the worst way for this to fail.
 *
 * The caller must have checked that the growth fits.
 */
static_always_inline i32
bpa_sip_apply (u8 *pl, u32 pl_len, const bpa_sip_edit_t *e, u32 n)
{
  u32 cur = pl_len, i, k;

  for (i = n; i-- > 0;)
    {
      u32 after = e[i].off + e[i].old_len;
      u32 tail = cur - after;

      if (e[i].len != e[i].old_len)
	clib_memmove (pl + e[i].off + e[i].len, pl + after, tail);
      for (k = 0; k < e[i].len; k++)
	pl[e[i].off + k] = e[i].txt[k];
      cur = cur + e[i].len - e[i].old_len;
    }

  return (i32) cur - (i32) pl_len;
}

/*
 * What rewriting this message could grow it by, worked out BEFORE a single
 * port is reserved. A reservation made and then abandoned because the rewrite
 * did not fit holds two ports of a 512-port block for the expectation
 * timeout, and a phone retrying every few seconds turns that into exhaustion
 * of its own subscriber's block - the lesson bpa_rtsp_in2out records.
 *
 * Only the items naming the subscriber's own private address are rewritten,
 * each to at most its kind's widest text; Content-Length always may be, since
 * any body edit moves it. `*clen_at` is its item, or ~0 when there is none.
 */
static_always_inline u32
bpa_sip_growth (const bpa_sip_msg_t *m, u32 ip_priv, u32 *clen_at)
{
  u32 i, want = 0;

  *clen_at = ~0u;
  for (i = 0; i < m->n_items; i++)
    {
      const bpa_sip_item_t *it = &m->items[i];
      u32 widest = 0;

      switch (it->kind)
	{
	case BPA_SIP_VIA:
	case BPA_SIP_CONTACT:
	  widest = it->addr == ip_priv ? BPA_SIP_TXT_MAX : 0;
	  break;
	case BPA_SIP_CONN:
	  widest = it->addr == ip_priv ? 15 : 0; /* "255.255.255.255" */
	  break;
	case BPA_SIP_MEDIA:
	  widest = it->addr == ip_priv ? 5 : 0; /* "65535" */
	  break;
	case BPA_SIP_CLEN:
	  *clen_at = i;
	  widest = 5;
	  break;
	}
      if (widest > it->len)
	want += widest - it->len;
    }
  return want;
}

/*
 * The item of a response's TOPMOST Via, when it names exactly the public
 * address and port this box wrote there; ~0 otherwise - a request, a topmost
 * Via that is somebody else's, or none. Only the topmost: RFC 3261 s18.1.2
 * has the client check that one, and a Via further down belongs to a proxy.
 */
static_always_inline u32
bpa_sip_our_via (const bpa_sip_msg_t *m, u32 ip_pub, u16 port_pub)
{
  u32 i;

  if (!m->is_response)
    return ~0u;
  for (i = 0; i < m->n_items; i++)
    {
      const bpa_sip_item_t *it = &m->items[i];

      if (it->kind != BPA_SIP_VIA)
	continue;
      return it->addr == ip_pub && it->port == port_pub ? i : ~0u;
    }
  return ~0u;
}

#endif /* __included_bpa_sip_h__ */
