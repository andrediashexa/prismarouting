/*
 * bpa_ftp.c - FTP ALG: parsing, rewriting, and the expected data connection.
 *
 * The rules this file follows, and why, are in bpa_ftp.h. The one that
 * governs every function here: a payload that does not parse EXACTLY leaves
 * the packet untouched and is counted. An ALG that rewrites on a guess
 * corrupts traffic that would otherwise merely fail to connect, which is a
 * worse outcome than the one it exists to prevent.
 */

#include "bpa.h"
#include "bpa_ftp.h"

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
static bpa_session_t *
bpa_ftp_expect (bpa_per_thread_t *pt, const bpa_pool_t *p, bpa_session_t *ctl,
		u16 port_priv, f64 now)
{
  clib_bihash_kv_16_8_t kv, value;
  bpa_block_t blk = { 0 };

  bpa_key (&kv, ctl->ip_priv, port_priv, BPA_PROTO_TCP);
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
			       BPA_PROTO_TCP, now, BPA_CREATE_ALG, &why);
  }
}

/* ------------------------------------------------------------- rewriting */

/*
 * Record the length change so that everything the subscriber sends after this
 * point is renumbered. The shape is the one netfilter arrived at: remember
 * where the last change happened and the offset on each side of it, so a
 * retransmission from before the change is still shifted by what was in force
 * when it was first sent.
 *
 * Only the two most recent offsets are kept. A retransmission older than the
 * last-but-one rewrite gets the wrong one - stated here rather than hidden,
 * because on an FTP control connection rewrites are seconds apart and a
 * retransmission that old has already been given up on by the sender.
 */
static void
bpa_ftp_seqadj_set (bpa_session_t *s, u32 seq, i32 delta)
{
  i32 after;

  if (s->alg_off_before == s->alg_off_after
      || bpa_seq_after (seq, s->alg_seq_pos))
    {
      s->alg_seq_pos = seq;
      s->alg_off_before = s->alg_off_after;
    }

  after = (i32) s->alg_off_after + delta;
  /* The offsets are 16-bit: an FTP control connection rewrites a handful of
   * short strings, and one that somehow drifted past 32 KB is one we stop
   * following rather than wrap into nonsense. */
  if (after > 32000 || after < -32000)
    return;
  s->alg_off_after = (i16) after;
  s->alg |= BPA_ALG_FTP_SEQ;
}

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
  u32 hlen, plen, rep_len = 0, tail, s_index;
  i32 delta;
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

  delta = (i32) rep_len - (i32) pr.arg_len;

  /* Growing past the buffer would corrupt whatever follows it. */
  if (PREDICT_FALSE ((i32) b->current_data + (i32) b->current_length + delta
		     > (i32) vlib_buffer_get_default_data_size (vm)))
    return BPA_FTP_UNTOUCHED;

  tail = plen - pr.arg_off - pr.arg_len;
  if (tail && delta)
    clib_memmove (pl + pr.arg_off + rep_len, pl + pr.arg_off + pr.arg_len,
		  tail);
  clib_memcpy_fast (pl + pr.arg_off, rep, rep_len);

  b->current_length = (u16) ((i32) b->current_length + delta);
  ip->length = clib_host_to_net_u16 (
    (u16) ((i32) clib_net_to_host_u16 (ip->length) + delta));

  bpa_ftp_seqadj_set (s, clib_net_to_host_u32 (tcp->seq_number), delta);

  /*
   * Both checksums from scratch, not incrementally.
   *
   * The payload changed length, so an incremental update would have to fold
   * in every moved byte - and the ICMP work already proved what a wrong
   * checksum costs here: the receiver drops the packet in silence while every
   * counter reports success. The segment is one short command, so recomputing
   * it is not a cost worth the risk of getting the arithmetic wrong.
   */
  ip->checksum = 0;
  ip->checksum = ip4_header_checksum (ip);
  tcp->checksum = 0;
  tcp->checksum = ip4_tcp_udp_compute_checksum (vm, b, ip);

  return BPA_FTP_REWRITTEN;
}
