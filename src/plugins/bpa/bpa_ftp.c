/*
 * bpa_ftp.c - FTP ALG: parsing, rewriting, and the expected data connection.
 *
 * The rules this file follows, and why, are in bpa_ftp.h. The one that
 * governs every function here: a payload that does not parse EXACTLY leaves
 * the packet untouched and is counted. An ALG that rewrites on a guess
 * corrupts traffic that would otherwise merely fail to connect, which is a
 * worse outcome than the one it exists to prevent.
 */

/*
 * Standalone, the same way bpa_ftp.h already is and for the same reason: the
 * text half of this ALG - the decimal parser, the two address grammars and
 * the line parse that refuses - decides whether a subscriber's payload is
 * touched at all, and none of it reads a packet, a session or a pool. Under
 * BPA_STANDALONE the VPP headers are not included and the two functions that
 * DO need them are not compiled; the VPP build defines nothing, so every
 * guard below takes its `#else` branch and the file preprocesses exactly as
 * it did before.
 */
#ifndef BPA_STANDALONE
#include "bpa.h"
#endif
#include "bpa_ftp.h"
#ifndef BPA_STANDALONE
#include "bpa_alg.h"
#endif

/* Longest replacement: "1|255.255.255.255|65535" for EPRT, 23 bytes. */
#define BPA_FTP_MAX_ARG 32

typedef enum
{
  BPA_FTP_FORM_PORT = 0, /* PORT h1,h2,h3,h4,p1,p2   (RFC 959)  */
  BPA_FTP_FORM_EPRT,	 /* EPRT |1|a.b.c.d|port|    (RFC 2428) */
  BPA_FTP_FORM_227,	 /* 227 ... (h1,h2,h3,h4,p1,p2)         */
  BPA_FTP_FORM_229,	 /* 229 ... (|||port|)                  */
} bpa_ftp_form_t;

typedef struct
{
  u32 arg_off; /* first byte of the region to replace */
  u32 arg_len; /* how many bytes it spans */
  u32 addr;    /* announced address, host order; 0 when the form has none */
  u16 port;    /* announced port */
  u8 delim;    /* RFC 2428 delimiter, for the forms that use one */
  u8 form;
} bpa_ftp_parse_t;

/* ------------------------------------------------------------------ text */

static u32
bpa_ftp_dec (const u8 *p, u32 len, u32 max, u32 *out)
{
  u32 v = 0, i = 0;

  /* Bounded at five digits so a long run of zeroes cannot be read as a
   * valid port and so the loop is bounded by the grammar, not by the
   * payload. */
  while (i < len && i < 5 && p[i] >= '0' && p[i] <= '9')
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

static u32
bpa_ftp_put_dec (u8 *dst, u32 v)
{
  u8 tmp[5];
  u32 n = 0, i;

  do
    {
      tmp[n++] = (u8) ('0' + (v % 10));
      v /= 10;
    }
  while (v);

  for (i = 0; i < n; i++)
    dst[i] = tmp[n - 1 - i];
  return n;
}

/* FTP commands are case-insensitive (RFC 959 s2.3), replies are digits. */
static int
bpa_ftp_starts (const u8 *p, u32 len, const char *kw)
{
  u32 i;

  for (i = 0; kw[i]; i++)
    {
      u8 c;

      if (i >= len)
	return 0;
      c = p[i];
      if (c >= 'a' && c <= 'z')
	c = (u8) (c - 'a' + 'A');
      if (c != (u8) kw[i])
	return 0;
    }
  return 1;
}

/* "h1,h2,h3,h4,p1,p2". Returns the bytes consumed, or 0. */
static u32
bpa_ftp_parse_hostport (const u8 *p, u32 len, u32 *addr, u16 *port)
{
  u32 v[6], off = 0, i, n;

  for (i = 0; i < 6; i++)
    {
      if (i)
	{
	  if (off >= len || p[off] != ',')
	    return 0;
	  off++;
	}
      if (off >= len)
	return 0;
      n = bpa_ftp_dec (p + off, len - off, 255, &v[i]);
      if (n == 0)
	return 0;
      off += n;
    }

  *addr = (v[0] << 24) | (v[1] << 16) | (v[2] << 8) | v[3];
  *port = (u16) ((v[4] << 8) | v[5]);
  return off;
}

static u32
bpa_ftp_put_hostport (u8 *dst, u32 addr, u16 port)
{
  u32 n = 0;

  n += bpa_ftp_put_dec (dst + n, (addr >> 24) & 0xff);
  dst[n++] = ',';
  n += bpa_ftp_put_dec (dst + n, (addr >> 16) & 0xff);
  dst[n++] = ',';
  n += bpa_ftp_put_dec (dst + n, (addr >> 8) & 0xff);
  dst[n++] = ',';
  n += bpa_ftp_put_dec (dst + n, addr & 0xff);
  dst[n++] = ',';
  n += bpa_ftp_put_dec (dst + n, (port >> 8) & 0xff);
  dst[n++] = ',';
  n += bpa_ftp_put_dec (dst + n, port & 0xff);
  return n;
}

/* "a.b.c.d". Returns the bytes consumed, or 0. */
static u32
bpa_ftp_parse_dotted (const u8 *p, u32 len, u32 *addr)
{
  u32 v[4], off = 0, i, n;

  for (i = 0; i < 4; i++)
    {
      if (i)
	{
	  if (off >= len || p[off] != '.')
	    return 0;
	  off++;
	}
      if (off >= len)
	return 0;
      n = bpa_ftp_dec (p + off, len - off, 255, &v[i]);
      if (n == 0)
	return 0;
      off += n;
    }

  *addr = (v[0] << 24) | (v[1] << 16) | (v[2] << 8) | v[3];
  return off;
}

static u32
bpa_ftp_put_dotted (u8 *dst, u32 addr)
{
  u32 n = 0;

  n += bpa_ftp_put_dec (dst + n, (addr >> 24) & 0xff);
  dst[n++] = '.';
  n += bpa_ftp_put_dec (dst + n, (addr >> 16) & 0xff);
  dst[n++] = '.';
  n += bpa_ftp_put_dec (dst + n, (addr >> 8) & 0xff);
  dst[n++] = '.';
  n += bpa_ftp_put_dec (dst + n, addr & 0xff);
  return n;
}

/* ---------------------------------------------------------------- parsing */

/*
 * Parse the FIRST line of the payload.
 *
 * Returns 0 when it parsed, -1 when the line begins with one of our keywords
 * and does not parse (which the caller counts), and 1 when there is nothing
 * here for us at all.
 *
 * Every form demands its exact grammar and a CRLF terminator. RFC 959 defines
 * the control connection as Telnet NVT, where a line ends in CRLF; accepting
 * a bare LF would mean guessing about a payload that is already not what the
 * protocol says it should be.
 */
static int
bpa_ftp_parse (const u8 *pl, u32 len, int server_side, bpa_ftp_parse_t *r)
{
  u32 off, n;

  clib_memset (r, 0, sizeof (*r));

  if (!server_side && bpa_ftp_starts (pl, len, "PORT "))
    {
      r->form = BPA_FTP_FORM_PORT;
      r->arg_off = 5;
      n = bpa_ftp_parse_hostport (pl + 5, len - 5, &r->addr, &r->port);
      if (n == 0)
	return -1;
      r->arg_len = n;
      off = 5 + n;
      if (off + 2 > len || pl[off] != '\r' || pl[off + 1] != '\n')
	return -1;
      return 0;
    }

  if (!server_side && bpa_ftp_starts (pl, len, "EPRT "))
    {
      u32 family;

      r->form = BPA_FTP_FORM_EPRT;
      /* RFC 2428: the character right after the command is the delimiter for
       * the whole argument. It is <SP> that separates command from argument,
       * so the delimiter is the byte after that. */
      if (len < 7)
	return -1;
      r->delim = pl[5];
      if (r->delim < 33 || r->delim > 126)
	return -1;

      off = 6;
      n = bpa_ftp_dec (pl + off, len - off, 2, &family);
      if (n == 0)
	return -1;
      r->arg_off = off;
      off += n;
      if (off >= len || pl[off] != r->delim)
	return -1;
      off++;

      /*
       * Family 2 is IPv6. A v4 CGNAT has nothing to put there, so the packet
       * goes through untouched and is counted rather than mangled into
       * something that is not an EPRT any more.
       */
      if (family != 1)
	return -1;

      n = bpa_ftp_parse_dotted (pl + off, len - off, &r->addr);
      if (n == 0)
	return -1;
      off += n;
      if (off >= len || pl[off] != r->delim)
	return -1;
      off++;

      {
	u32 port;
	n = bpa_ftp_dec (pl + off, len - off, 65535, &port);
	if (n == 0 || port == 0)
	  return -1;
	r->port = (u16) port;
	off += n;
      }
      if (off >= len || pl[off] != r->delim)
	return -1;
      off++;
      r->arg_len = off - 1 - r->arg_off; /* everything up to the last delim */
      if (off + 2 > len || pl[off] != '\r' || pl[off + 1] != '\n')
	return -1;
      return 0;
    }

  if (server_side && bpa_ftp_starts (pl, len, "227 "))
    {
      r->form = BPA_FTP_FORM_227;
      /* RFC 959 does not fix the reply text, only that the argument is
       * parenthesised. Find the parenthesis on this line and nowhere else. */
      for (off = 4; off < len && pl[off] != '(' && pl[off] != '\r'; off++)
	;
      if (off >= len || pl[off] != '(')
	return -1;
      off++;
      n = bpa_ftp_parse_hostport (pl + off, len - off, &r->addr, &r->port);
      if (n == 0)
	return -1;
      r->arg_off = off;
      r->arg_len = n;
      off += n;
      if (off + 3 > len || pl[off] != ')' || pl[off + 1] != '\r'
	  || pl[off + 2] != '\n')
	return -1;
      return 0;
    }

  if (server_side && bpa_ftp_starts (pl, len, "229 "))
    {
      u32 port;

      r->form = BPA_FTP_FORM_229;
      for (off = 4; off < len && pl[off] != '(' && pl[off] != '\r'; off++)
	;
      if (off >= len || pl[off] != '(')
	return -1;
      off++;
      /* (<d><d><d>port<d>): protocol and address are empty in EPSV. */
      if (off + 4 > len)
	return -1;
      r->delim = pl[off];
      if (r->delim < 33 || r->delim > 126)
	return -1;
      if (pl[off + 1] != r->delim || pl[off + 2] != r->delim)
	return -1;
      off += 3;

      n = bpa_ftp_dec (pl + off, len - off, 65535, &port);
      if (n == 0 || port == 0)
	return -1;
      r->port = (u16) port;
      r->arg_off = off; /* only the digits move */
      r->arg_len = n;
      off += n;
      if (off + 4 > len || pl[off] != r->delim || pl[off + 1] != ')'
	  || pl[off + 2] != '\r' || pl[off + 3] != '\n')
	return -1;
      return 0;
    }

  return 1;
}

/*
 * Does a keyword we handle start a LATER line of this payload?
 *
 * We do not rewrite it - see bpa_ftp.h - but leaving it entirely silent would
 * make "the ALG saw nothing" and "the ALG declined to act" the same
 * observation, which is exactly the distinction the operator needs.
 */
static int
bpa_ftp_keyword_later (const u8 *pl, u32 len, int server_side)
{
  u32 i;

  for (i = 0; i + 1 < len; i++)
    {
      if (pl[i] != '\n')
	continue;
      if (server_side)
	{
	  if (bpa_ftp_starts (pl + i + 1, len - i - 1, "227 ")
	      || bpa_ftp_starts (pl + i + 1, len - i - 1, "229 "))
	    return 1;
	}
      else if (bpa_ftp_starts (pl + i + 1, len - i - 1, "PORT ")
	       || bpa_ftp_starts (pl + i + 1, len - i - 1, "EPRT "))
	return 1;
    }
  return 0;
}

/* ------------------------------------------------------------ FTP64 text */

/*
 * The first line of a payload: its length with the CRLF, or 0 when the
 * segment holds no complete line. Only the first line is ever translated, for
 * the IPv4 ALG's reason (bpa_ftp.h): a later line is where a file name lives.
 */
static u32
bpa_ftp64_line (const u8 *pl, u32 len)
{
  u32 i;

  for (i = 0; i + 1 < len; i++)
    if (pl[i] == '\r' && pl[i + 1] == '\n')
      return i + 2;
  return 0;
}

/* Is `body` (the line without its CRLF) the four-letter command `kw`, alone
 * or followed by an argument? "EPSVX" is not EPSV. */
static int
bpa_ftp64_cmd (const u8 *body, u32 n, const char *kw)
{
  return bpa_ftp_starts (body, n, kw) && (n == 4 || body[4] == ' ');
}

/* The argument after "XXXX ", compared case-insensitively and whole. */
static int
bpa_ftp64_arg_is (const u8 *body, u32 n, const char *arg)
{
  u32 l = (u32) strlen (arg);

  return n == 5 + l && bpa_ftp_starts (body + 5, l, arg);
}

static int
bpa_ftp64_hex (u8 c)
{
  if (c >= '0' && c <= '9')
    return c - '0';
  if (c >= 'a' && c <= 'f')
    return c - 'a' + 10;
  if (c >= 'A' && c <= 'F')
    return c - 'A' + 10;
  return -1;
}

/*
 * An IPv6 address in RFC 4291 text form, all of `len` and nothing else:
 * groups of one to four hex digits, at most one "::", and a dotted IPv4 tail
 * in the last 32 bits. 0, or -1 when it is not exactly that. Compared, never
 * printed: the EPRT is translated only when it names the address the control
 * connection comes from, and a parser that guesses would compare a guess.
 */
static int
bpa_ftp64_ip6 (const u8 *p, u32 len, u8 out[16])
{
  u16 g[8];
  int n = 0, gap = -1, i, k;
  u32 off = 0;

  if (len >= 2 && p[0] == ':' && p[1] == ':')
    {
      gap = 0;
      off = 2;
    }
  else if (len && p[0] == ':')
    return -1;

  while (off < len)
    {
      u32 e = off, v = 0, d = 0;

      /* A dotted tail: the run up to the next ':' has a '.'. */
      while (e < len && p[e] != ':' && p[e] != '.')
	e++;
      if (e < len && p[e] == '.')
	{
	  u32 a;

	  if (n > 6 || bpa_ftp_parse_dotted (p + off, len - off, &a)
			 != len - off)
	    return -1;
	  g[n++] = (u16) (a >> 16);
	  g[n++] = (u16) a;
	  off = len;
	  break;
	}
      while (off < len && d < 5 && bpa_ftp64_hex (p[off]) >= 0)
	{
	  v = (v << 4) | (u32) bpa_ftp64_hex (p[off]);
	  off++;
	  d++;
	}
      if (d == 0 || d > 4 || n >= 8)
	return -1;
      g[n++] = (u16) v;
      if (off == len)
	break;
      if (p[off] != ':')
	return -1;
      off++;
      if (off < len && p[off] == ':')
	{
	  if (gap >= 0)
	    return -1;
	  gap = n;
	  off++;
	}
      else if (off == len)
	return -1; /* a single trailing colon */
    }

  if (gap < 0 ? n != 8 : n > 7)
    return -1;
  memset (out, 0, 16);
  for (i = 0; i < (gap < 0 ? n : gap); i++)
    {
      out[2 * i] = (u8) (g[i] >> 8);
      out[2 * i + 1] = (u8) g[i];
    }
  if (gap >= 0)
    for (i = gap, k = 8 - (n - gap); i < n; i++, k++)
      {
	out[2 * k] = (u8) (g[i] >> 8);
	out[2 * k + 1] = (u8) g[i];
      }
  return 0;
}

static u32
bpa_ftp64_put (u8 *dst, const char *s)
{
  u32 n = (u32) strlen (s);

  memcpy (dst, s, n);
  return n;
}

/* "PORT h1,h2,h3,h4,p1,p2\r\n": what an EPRT goes to the server as. */
u32
bpa_ftp64_put_port (u8 *dst, u32 addr, u16 port)
{
  u32 n = bpa_ftp64_put (dst, "PORT ");

  n += bpa_ftp_put_hostport (dst + n, addr, port);
  return n + bpa_ftp64_put (dst + n, "\r\n");
}

/*
 * EPRT |2|<address>|<port>|, all of the line's body. 1 and the port when it
 * names `host6`; 0 when it is well formed and names another address or IPv4
 * (passed unchanged, RFC 6384 7.2); -1 when it does not parse.
 */
static int
bpa_ftp64_eprt (const u8 *body, u32 n, const u8 host6[16], u16 *port)
{
  u8 d, a6[16];
  u32 off = 6, s, fam, v;

  if (n < 7)
    return -1;
  d = body[5];
  if (d < 33 || d > 126)
    return -1;
  if (bpa_ftp_dec (body + off, n - off, 2, &fam) != 1)
    return -1;
  off++;
  if (off >= n || body[off] != d)
    return -1;
  s = ++off;
  while (off < n && body[off] != d)
    off++;
  if (off >= n)
    return -1;
  if (fam == 2 ? bpa_ftp64_ip6 (body + s, off - s, a6) != 0
	       : bpa_ftp_parse_dotted (body + s, off - s, &v) != off - s)
    return -1;
  off++;
  s = bpa_ftp_dec (body + off, n - off, 65535, &v);
  if (s == 0 || v == 0)
    return -1;
  off += s;
  if (off + 1 != n || body[off] != d)
    return -1;
  if (fam != 2 || memcmp (a6, host6, 16) != 0)
    return 0;
  *port = (u16) v;
  return 1;
}

/* A later line starting with a command this ALG would have translated:
 * counted as untouched, as the IPv4 ALG counts its own (bpa_ftp.h). */
static int
bpa_ftp64_later (const u8 *pl, u32 len)
{
  u32 i;

  for (i = 0; i + 1 < len; i++)
    if (pl[i] == '\n'
	&& (bpa_ftp_starts (pl + i + 1, len - i - 1, "EPSV")
	    || bpa_ftp_starts (pl + i + 1, len - i - 1, "EPRT")
	    || bpa_ftp_starts (pl + i + 1, len - i - 1, "ALGS")))
      return 1;
  return 0;
}

/*
 * The client's command, subscriber -> server. `*state` is the session's
 * byte and is written only when the answer is not UNTOUCHED; `rep` (room for
 * BPA_FTP64_MAX_LINE) replaces the first `*line_len` bytes on REPLACE. On
 * EPRT the caller opens the mapping for `*port` and writes the PORT itself,
 * with bpa_ftp64_put_port(), because the public port is not known here.
 */
bpa_ftp64_action_t
bpa_ftp64_client (const u8 *pl, u32 len, u8 *state, const u8 host6[16],
		  u8 *rep, u32 *rep_len, u32 *line_len, u16 *port)
{
  u8 st = *state;
  u32 l = bpa_ftp64_line (pl, len), n;

  *rep_len = 0;
  *line_len = l;
  if (st & BPA_FTP64_TRANSPARENT)
    return BPA_FTP64_PASS;
  if (l == 0)
    {
      /* A command of ours with no CRLF in this segment: split, or not FTP. */
      if (bpa_ftp_starts (pl, len, "EPSV") || bpa_ftp_starts (pl, len, "EPRT")
	  || bpa_ftp_starts (pl, len, "ALGS"))
	return BPA_FTP64_UNTOUCHED;
      return BPA_FTP64_PASS;
    }
  n = l - 2;

  /* ALGS first, and even with translation off: the RFC has the channel
   * watched for the ALGS that turns it back on (section 11). */
  if (bpa_ftp64_cmd (pl, n, "ALGS"))
    {
      u8 p = BPA_FTP64_P_216;

      if (bpa_ftp64_arg_is (pl, n, "STATUS64"))
	;
      else if (bpa_ftp64_arg_is (pl, n, "ENABLE64"))
	st &= (u8) ~BPA_FTP64_DISABLED;
      else if (bpa_ftp64_arg_is (pl, n, "DISABLE64"))
	st |= BPA_FTP64_DISABLED;
      else
	p = BPA_FTP64_P_504;
      *state = (u8) ((st & ~BPA_FTP64_PENDING) | p);
      *rep_len = bpa_ftp64_put (rep, "NOOP\r\n");
      return BPA_FTP64_REPLACE;
    }
  if (st & BPA_FTP64_DISABLED)
    return BPA_FTP64_PASS;

  if (bpa_ftp64_cmd (pl, n, "AUTH"))
    {
      *state = (u8) ((st & ~BPA_FTP64_PENDING) | BPA_FTP64_P_AUTH);
      return BPA_FTP64_PASS;
    }

  if (bpa_ftp64_cmd (pl, n, "EPSV"))
    {
      u32 net;
      u8 p;

      if (n == 4 || bpa_ftp64_arg_is (pl, n, "2"))
	{
	  *state = (u8) ((st & ~BPA_FTP64_PENDING) | BPA_FTP64_P_229);
	  *rep_len = bpa_ftp64_put (rep, "PASV\r\n");
	  return BPA_FTP64_REPLACE;
	}
      if (bpa_ftp64_arg_is (pl, n, "ALL"))
	p = BPA_FTP64_P_504;
      else if (bpa_ftp_dec (pl + 5, n - 5, 65535, &net) == n - 5)
	p = BPA_FTP64_P_522;
      else
	return BPA_FTP64_PASS; /* not RFC 2428: the server says so */
      *state = (u8) ((st & ~BPA_FTP64_PENDING) | p);
      *rep_len = bpa_ftp64_put (rep, "NOOP\r\n");
      return BPA_FTP64_REPLACE;
    }

  if (bpa_ftp64_cmd (pl, n, "EPRT"))
    switch (bpa_ftp64_eprt (pl, n, host6, port))
      {
      case 1:
	return BPA_FTP64_EPRT;
      case 0:
	return BPA_FTP64_PASS;
      default:
	return BPA_FTP64_UNTOUCHED;
      }

  /* From the first line's own LF, so the second line is seen too. */
  return bpa_ftp64_later (pl + l - 1, len - l + 1) ? BPA_FTP64_UNTOUCHED
						   : BPA_FTP64_PASS;
}

/*
 * The server's reply, server -> subscriber: what the pending translation
 * turns it into. Only a single-line final reply is translated; a 1xx is
 * preliminary and the pending answer waits for the final one.
 *
 * `is_retx` says this segment starts where the last rewritten one did - the
 * server sending it again because the first copy was lost. The answer it was
 * rewritten into is the last-reply bits, and it is rewritten again into the
 * same: with the pending answer already spent, it would otherwise reach the
 * client as the 227 the client cannot use, at a length the renumbering does
 * not expect.
 */
bpa_ftp64_action_t
bpa_ftp64_server (const u8 *pl, u32 len, u8 *state, int is_retx, u8 *rep,
		  u32 *rep_len, u32 *line_len)
{
  u8 st = *state, p = st & BPA_FTP64_PENDING;
  u32 l = bpa_ftp64_line (pl, len);

  *rep_len = 0;
  *line_len = l;
  if (p == 0 && is_retx)
    p = (u8) (st >> BPA_FTP64_LAST_SHIFT);
  if (p == 0)
    return BPA_FTP64_PASS;
  if (l < 6 || pl[0] < '1' || pl[0] > '5' || pl[1] < '0' || pl[1] > '9'
      || pl[2] < '0' || pl[2] > '9' || (pl[3] != ' ' && pl[3] != '-'))
    {
      /* Not a reply this can read: the pending answer is dropped rather
       * than applied to whatever comes next. */
      *state = (u8) (st & ~BPA_FTP64_PENDING);
      return BPA_FTP64_UNTOUCHED;
    }
  if (pl[0] == '1')
    return BPA_FTP64_PASS;
  *state = (u8) (st & ~BPA_FTP64_PENDING);
  if (pl[3] == '-')
    return BPA_FTP64_UNTOUCHED; /* multi-line: not one of these */

  switch (p)
    {
    case BPA_FTP64_P_229:
      {
	u32 off = 4, addr, k;
	u16 port;

	if (memcmp (pl, "227", 3) != 0)
	  return BPA_FTP64_PASS; /* 425, 500: the client hears it as it is */
	/* RFC 1123 4.1.2.6: the numbers are the first digits on the line,
	 * parenthesised or not. */
	while (off < l - 2 && !(pl[off] >= '0' && pl[off] <= '9'))
	  off++;
	k = bpa_ftp_parse_hostport (pl + off, l - 2 - off, &addr, &port);
	if (k == 0 || port == 0)
	  return BPA_FTP64_UNTOUCHED;
	k = bpa_ftp64_put (rep, "229 Entering Extended Passive Mode (|||");
	k += bpa_ftp_put_dec (rep + k, port);
	k += bpa_ftp64_put (rep + k, "|)\r\n");
	*rep_len = k;
	*state = (u8) ((*state & 0x1f) | (p << BPA_FTP64_LAST_SHIFT));
	return BPA_FTP64_REPLACE;
      }
    case BPA_FTP64_P_522:
    case BPA_FTP64_P_504:
    case BPA_FTP64_P_216:
      if (memcmp (pl, "200", 3) != 0)
	return BPA_FTP64_PASS;
      *rep_len = bpa_ftp64_put (
	rep, p == BPA_FTP64_P_522   ? "522 Network protocol not supported\r\n"
	     : p == BPA_FTP64_P_504 ? "504 Command not implemented for that "
				      "parameter\r\n"
	     : (st & BPA_FTP64_DISABLED) ? "216 NONE\r\n"
					 : "216 EPSVEPRT\r\n");
      *state = (u8) ((*state & 0x1f) | (p << BPA_FTP64_LAST_SHIFT));
      return BPA_FTP64_REPLACE;
    case BPA_FTP64_P_AUTH:
      /* Taken (2xx, or 3xx for the security data that follows): the
       * channel is the client's and the server's from here. */
      if (pl[0] == '2' || pl[0] == '3')
	*state |= BPA_FTP64_TRANSPARENT;
      return BPA_FTP64_PASS;
    default:
      return BPA_FTP64_PASS;
    }
}

#ifndef BPA_STANDALONE

/* ------------------------------------------------- the data connection */

/*
 * Install the session that will accept the data connection the subscriber
 * just announced.
 *
 * Notes that are not obvious from the code:
 *
 * - The session is created in BPA_TCP_INIT, so it expires on the half-open
 *   timer (twenty seconds by default) if the connection never arrives. An
 *   announcement that goes unanswered therefore costs one port for as long as
 *   a scan probe does, and no longer.
 *
 * - Its `remote` stays zero, and out2in skips the address-dependent filter
 *   when it is. That is deliberate: the subscriber asked to be connected TO,
 *   so the connection is solicited even though nothing outbound preceded it,
 *   and the ALG must not be silently defeated by the pool's filtering mode.
 *
 * - The same private port announced twice reuses the session it already has.
 *   Creating a second one would overwrite the hash entry and strand the first,
 *   leaking a port out of the subscriber's block on every repeat.
 */
/* not a unit: it searches this worker's bihash and hands out a block, so it needs a
 * running dataplane - and it is not compiled at all under BPA_STANDALONE,
 * which is what leaves the text half of this ALG testable. lab/ftp-alg.sh
 * drives it with real FTP */
static bpa_session_t *
bpa_ftp_expect (bpa_per_thread_t *pt, const bpa_pool_t *p, bpa_session_t *ctl,
		u16 port_priv, f64 now)
{
  clib_bihash_kv_16_8_t kv, value;
  bpa_block_t blk = { 0 };

  bpa_key_in (&kv, ctl->ip_priv, port_priv, BPA_PROTO_TCP, ctl->fib_index);
  if (clib_bihash_search_16_8 (&pt->in2out, &kv, &value) == 0)
    {
      bpa_session_t *d = pool_elt_at_index (pt->sessions, value.value);
      d->last_heard = now;
      return d;
    }

  /*
   * The control session is alive, so this subscriber's block and port bitmap
   * exist. Asserting it rather than assuming it: bpa_session_create() would
   * otherwise build a subscriber around the all-zero block below, which is a
   * block belonging to somebody else.
   */
  if (bpa_sub_n_blocks (pt, ctl->pool_index, ctl->sub_idx) == 0)
    return 0;

  blk.sub_idx = ctl->sub_idx;
  /* An announced data connection, not traffic asking for a mapping: the
   * control connection that announced it already passed the subscriber's
   * bounds, and refusing this half would break the transfer rather than bound
   * anything. See bpa_create_kind_t. */
  {
    bpa_create_why_t why;

    return bpa_session_create (pt, p, ctl->pool_index, &blk, port_priv,
			       BPA_PROTO_TCP, ctl->fib_index, now,
			       BPA_CREATE_ALG, &why);
  }
}

/* ------------------------------------------------------------- rewriting */

/* not a unit: it rewrites a packet in a vlib_buffer_t and opens a session, neither of
 * which exists outside a VPP - and like bpa_ftp_expect() it is not compiled
 * under BPA_STANDALONE. The parse it rests on IS driven here; what this adds
 * is the buffer, and lab/ftp-alg.sh is where that is asked */
bpa_ftp_result_t
bpa_ftp_in2out (vlib_main_t *vm, vlib_buffer_t *b, ip4_header_t *ip,
		bpa_per_thread_t *pt, const bpa_pool_t *p, bpa_session_t **sp,
		f64 now)
{
  u8 rep[BPA_FTP_MAX_ARG];
  bpa_session_t *s = *sp;
  bpa_ftp_parse_t pr;
  bpa_session_t *data;
  tcp_header_t *tcp;
  u8 *end, *pl;
  u32 hlen, plen, rep_len = 0, s_index;
  int server_side, rc;

  end = (u8 *) vlib_buffer_get_current (b) + b->current_length;
  hlen = ip4_header_bytes (ip);
  tcp = (tcp_header_t *) ((u8 *) ip + hlen);

  /*
   * Where the payload ends is what the IP header says, not what the buffer
   * holds. An Ethernet frame padded up to its 60-byte minimum leaves trailing
   * bytes in the buffer that are not payload, and a parser that reads them is
   * parsing whatever the sender's driver happened to leave there.
   */
  {
    u32 total = clib_net_to_host_u16 (ip->length);
    if (total >= hlen && (u8 *) ip + total < end)
      end = (u8 *) ip + total;
  }

  /* A segment split across buffers, or one whose header does not fit, is not
   * something to guess about. */
  if (PREDICT_FALSE (b->flags & VLIB_BUFFER_NEXT_PRESENT))
    return BPA_FTP_NOTHING;
  if (PREDICT_FALSE ((u8 *) tcp + sizeof (*tcp) > end))
    return BPA_FTP_NOTHING;

  pl = (u8 *) tcp + tcp_header_bytes (tcp);
  if (PREDICT_FALSE (pl < (u8 *) tcp + sizeof (*tcp) || pl >= end))
    return BPA_FTP_NOTHING;
  plen = (u32) (end - pl);

  /*
   * Which side of the conversation the subscriber is on, taken from the
   * session because in2out has already replaced the header's source port
   * with the public one. A client sends PORT and EPRT; a server sends the
   * 227 and 229 replies. Looking for the wrong pair is not a missed rewrite,
   * it is an ALG that could rewrite something that is not what it thinks.
   */
  server_side = s->port_priv == BPA_FTP_CONTROL_PORT;

  rc = bpa_ftp_parse (pl, plen, server_side, &pr);
  if (rc > 0)
    return bpa_ftp_keyword_later (pl, plen, server_side) ? BPA_FTP_UNTOUCHED
							 : BPA_FTP_NOTHING;
  if (rc < 0)
    return BPA_FTP_UNTOUCHED;

  /*
   * The announced address MUST be the one this session comes from.
   *
   * A client announcing somebody else's address is either behind a second NAT
   * - in which case rewriting would point the data connection at the wrong
   * host on our inside - or is attempting an FTP bounce. Both are reasons to
   * leave the packet exactly as it came in. The 229 reply carries no address
   * at all, so there is nothing to check.
   */
  if (pr.form != BPA_FTP_FORM_229 && pr.addr != s->ip_priv)
    return BPA_FTP_UNTOUCHED;

  /*
   * Installing the expectation allocates from the session pool, which may
   * move it. Everything below - including the caller's own pointer - has to
   * come from the index, not from the pointer we walked in with.
   */
  s_index = s - pt->sessions;
  data = bpa_ftp_expect (pt, p, s, pr.port, now);
  if (PREDICT_FALSE (data == 0))
    return BPA_FTP_NO_PORT;
  s = *sp = pool_elt_at_index (pt->sessions, s_index);

  /* The public address comes from the DATA session, not the control one: with
   * extended blocks a subscriber can hold blocks on two public addresses. */
  switch (pr.form)
    {
    case BPA_FTP_FORM_PORT:
    case BPA_FTP_FORM_227:
      rep_len = bpa_ftp_put_hostport (rep, data->ip_pub, data->port_pub);
      break;
    case BPA_FTP_FORM_EPRT:
      rep[rep_len++] = '1';
      rep[rep_len++] = pr.delim;
      rep_len += bpa_ftp_put_dotted (rep + rep_len, data->ip_pub);
      rep[rep_len++] = pr.delim;
      rep_len += bpa_ftp_put_dec (rep + rep_len, data->port_pub);
      break;
    case BPA_FTP_FORM_229:
      rep_len = bpa_ftp_put_dec (rep, data->port_pub);
      break;
    }

  /*
   * The shared splice: it moves the tail, repairs the IP length, both
   * checksums and this direction's sequence correction, and refuses rather
   * than growing past the buffer.
   *
   * This ALG used to do all of that itself, into a second set of correction
   * fields of its own (#48). Two mechanisms for one job meant the next ALG
   * that lengthens a payload had to pick one, and half of any reader's mental
   * model would then be wrong. The one kept is the one with the better
   * retransmission guard: a rewritten segment arriving again, with the same
   * rewrite already accounted for, must not push a second correction, and the
   * version deleted here would have doubled the offset for exactly that case.
   */
  if (PREDICT_FALSE (bpa_alg_splice (vm, b, ip, tcp, pl, plen, pr.arg_off,
				     pr.arg_len, rep, rep_len,
				     &s->adj[BPA_DIR_OUT]) < 0))
    return BPA_FTP_UNTOUCHED;

  return BPA_FTP_REWRITTEN;
}

#endif /* BPA_STANDALONE */
