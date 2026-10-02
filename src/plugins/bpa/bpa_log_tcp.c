/*
 * s->c - IPFIX export over TCP, with a local buffer.
 *
 * VPP's vnet/ipfix-export infrastructure only speaks UDP. For telemetry that
 * is enough; for a compliance log it is not: losing one natEvent 17 recreates
 * the gap between releasing and reallocating a block, and it is in that gap
 * that a court request returns the wrong subscriber (see docs/logging.md).
 *
 * The IPFIX message is assembled by hand here - the format is simple and it is
 * not worth bending the UDP infrastructure to fit. The socket is non-blocking:
 * a slow collector must not stall VPP's main thread. When the collector becomes
 * unreachable the bytes go to a spill file and come back on reconnection, in
 * order.
 */

#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

/*
 * Standalone, the same way bpa_log.h now is. Everything in this file below
 * the includes reads only its own state and the socket: the IPFIX message is
 * assembled into a vector, the spill file is ordinary POSIX I/O, and neither
 * needs a packet, a session or the graph. The one function that does is
 * bpa_log_tcp_enable(), which formats a path with VPP's format() and calls
 * back into bpa.c to replay the pins; it is not compiled under
 * BPA_STANDALONE, which is also why "bpa.h" is not needed there. The VPP
 * build defines BPA_STANDALONE nowhere, so both guards take their `#else`
 * branch and the file preprocesses exactly as it did before.
 */
#ifndef BPA_STANDALONE
#include "bpa.h"
#endif
#include "bpa_log.h"
#include "bpa_frame.h"
#include "bpa_sock.h"
#include "bpa_identity.h"
#include <string.h>

/*
 * 258 AND NOT 256, and the number is the point rather than a detail (#21).
 *
 * The block record carries an eighth element now - userName, the subscriber
 * who held the address - and IPFIX does not let a Template ID mean two
 * different sets of fields: a collector caches a template by id, and one that
 * still holds 256 from a box of an earlier version would read the new record
 * as the old one and report the identity's first bytes as a pool id. Changing
 * the id means a cached 256 is never matched by anything this box sends, so a
 * stale cache cannot misparse - it simply waits for the template it does not
 * have, which arrives within the minute.
 */
#define BPA_TCP_TEMPLATE_ID  258
/* The boot marker's template. See bpa_log.h for what it carries and why. */
#define BPA_TCP_MARKER_TEMPLATE_ID 257
#define BPA_TCP_DOMAIN	     1
#define BPA_TCP_RETRY_SECS   5.0
#define BPA_TCP_TEMPLATE_IVL 60.0
/* Above this, the in-memory buffer goes to the spill file. */
#define BPA_TCP_SPILL_BYTES  (1 << 20)
/*
 * A CONNECTED stream is never disconnected for being slow (#547). Spilling
 * at the first EAGAIN and dropping the connection mid-message capped the
 * stream at about 500 KB per connection on the #475 bench - 501,008 bytes,
 * every time - so any rate above ~130 lines/s grew the spill for ever. Now
 * what the socket has not started goes to the file once the collector has
 * taken nothing for BPA_TCP_STALL_SECS, or at BPA_TCP_BUSY_BYTES whatever it
 * is doing; the message on the wire stays, and the connection with it.
 */
#define BPA_TCP_STALL_SECS   10.0
#ifndef BPA_TCP_BUSY_BYTES /* the flood test sets it lower */
/* 8 MB, not more: a spill is one write() on the main thread, and logging must
 * never be what holds anything else up. */
#define BPA_TCP_BUSY_BYTES   (8 << 20)
#endif
/* How much of the spill file is in memory at once (#547). */
#define BPA_TCP_REPLAY_CHUNK (1 << 20)

bpa_log_stream_t bpa_log_tcp;
bpa_log_stream_t bpa_log_syslog;

/*
 * Both streams start closed (#551). A file-scope struct begins as zeros, and
 * zero is a VALID descriptor - stdin. `logging syslog disable` on a box whose
 * syslog had never been enabled went through bpa_tcp_disconnect(), which
 * closed descriptor 0: VPP's stdin. The next vppctl session was handed
 * descriptor 0, and when it ended VPP took it for its console closing and
 * exited with status 0 - which Docker does not restart. Measured on the
 * legacy bench at 0.149.7: fd 0 /dev/null before the commit, gone after it,
 * VPP exited on the next `vppctl show version`. bpa_init() calls this, beside
 * the RADIUS listener that learnt the same thing in #21.
 */
void
bpa_log_streams_init (void)
{
  bpa_log_tcp.fd = -1;
  bpa_log_syslog.fd = -1;
}

/*
 * RFC 8158 Table 21 elements, in the same order as the record, and since #21
 * one more: 371 userName, the subscriber this box knew at the instant of the
 * allocation.
 *
 * 371 IS READ OFF THE IANA REGISTRY and not chosen: `userName`, dataType
 * `string`, taken from iana.org/assignments/ipfix on 2026-09-18. A private
 * enterprise element would have been this product inventing a number every
 * collector would then have to be taught; a wrong standard one is worse
 * still, because it parses.
 *
 * 65535 is IPFIX's length for a VARIABLE-length field: the record carries one
 * length octet before the value. An identity is at most BPA_IDENTITY_MAX - 1
 * characters, so the short form is always enough.
 */
#define BPA_IPFIX_VARLEN 65535
static const u16 bpa_tcp_ies[][2] = {
  { 323, 8 }, { 230, 1 }, { 8, 4 }, { 225, 4 }, { 361, 2 }, { 362, 2 },
  { 283, 4 }, { 371, BPA_IPFIX_VARLEN },
};

/* observationTimeMilliseconds, systemInitTimeMilliseconds. */
static const u16 bpa_tcp_marker_ies[][2] = {
  { 323, 8 }, { 160, 8 },
};

static void bpa_tcp_spill_now (bpa_log_stream_t *s, int all);
static void bpa_tcp_offload (bpa_log_stream_t *s);
static void bpa_tcp_backlog_open (bpa_log_stream_t *s);
static void bpa_tcp_backlog_tail (bpa_log_stream_t *s);
static void bpa_tcp_save_off (bpa_log_stream_t *s, u64 off);

static void
bpa_tcp_put (u8 **b, const void *data, u32 n)
{
  u8 *p;
  vec_add2 (*b, p, n);
  clib_memcpy_fast (p, data, n);
}

static void
bpa_tcp_put_u16 (u8 **b, u16 v)
{
  v = clib_host_to_net_u16 (v);
  bpa_tcp_put (b, &v, 2);
}

static void
bpa_tcp_put_u32 (u8 **b, u32 v)
{
  v = clib_host_to_net_u32 (v);
  bpa_tcp_put (b, &v, 4);
}

/* IPFIX message header (RFC 7011), with the length filled in afterwards. */
static u32
bpa_tcp_msg_start (bpa_log_stream_t *s, u8 **b)
{
  u32 at = vec_len (*b);

  bpa_tcp_put_u16 (b, 10); /* version */
  bpa_tcp_put_u16 (b, 0);  /* length, filled in by msg_end */
  /* RFC 7011 Export Time: seconds since the UNIX epoch. It used to be the
   * f64 the caller had at hand, which was vlib_time_now() - seconds since VPP
   * started. Read here, so there is nothing for a caller to get wrong. */
  bpa_tcp_put_u32 (b, (u32) unix_time_now ());
  bpa_tcp_put_u32 (b, s->seq);
  bpa_tcp_put_u32 (b, BPA_TCP_DOMAIN);
  return at;
}

static void
bpa_tcp_msg_end (u8 **b, u32 at)
{
  u16 len = clib_host_to_net_u16 (vec_len (*b) - at);
  clib_memcpy_fast (*b + at + 2, &len, 2);
}

/*
 * Builds the template message and places it AT THE FRONT of the buffer.
 *
 * Prepending is mandatory on a new connection: whatever was held back during
 * the outage is already in the buffer, and without the template in front the
 * collector drops all of it - which is what the test showed, with the exporter
 * reporting "270 bytes sent" and the collector counting zero records.
 */
static void
/* now is vlib_time_now(): it only schedules the next template, and the
 * message's own Export Time is read from the wall clock inside msg_start. */
bpa_tcp_prepend_template (bpa_log_stream_t *s, f64 now)
{
  /* A syslog stream has no template: every line carries its own field names,
   * which is the whole of why a collector can read it without one. */
  if (s->wire == BPA_WIRE_SYSLOG)
    {
      s->need_template = 0;
      return;
    }
  u8 *tmp = 0;
  u8 **b = &tmp;
  u32 at = bpa_tcp_msg_start (s, b);
  u32 set_at = vec_len (*b);
  u32 i;

  /* Both templates in one set, so a reconnection cannot deliver the block
   * template and leave the marker's behind - a marker the collector cannot
   * decode is a restart it cannot see. */
  bpa_tcp_put_u16 (b, 2); /* set de template */
  bpa_tcp_put_u16 (b, 0);
  bpa_tcp_put_u16 (b, BPA_TCP_TEMPLATE_ID);
  bpa_tcp_put_u16 (b, ARRAY_LEN (bpa_tcp_ies));
  for (i = 0; i < ARRAY_LEN (bpa_tcp_ies); i++)
    {
      bpa_tcp_put_u16 (b, bpa_tcp_ies[i][0]);
      bpa_tcp_put_u16 (b, bpa_tcp_ies[i][1]);
    }
  bpa_tcp_put_u16 (b, BPA_TCP_MARKER_TEMPLATE_ID);
  bpa_tcp_put_u16 (b, ARRAY_LEN (bpa_tcp_marker_ies));
  for (i = 0; i < ARRAY_LEN (bpa_tcp_marker_ies); i++)
    {
      bpa_tcp_put_u16 (b, bpa_tcp_marker_ies[i][0]);
      bpa_tcp_put_u16 (b, bpa_tcp_marker_ies[i][1]);
    }

  {
    u16 slen = clib_host_to_net_u16 (vec_len (*b) - set_at);
    clib_memcpy_fast (*b + set_at + 2, &slen, 2);
  }
  bpa_tcp_msg_end (b, at);

  vec_insert (s->outbuf, vec_len (tmp), 0);
  clib_memcpy_fast (s->outbuf, tmp, vec_len (tmp));
  s->front_extra += vec_len (tmp);
  vec_free (tmp);

  s->last_template = now;
  s->need_template = 0;
}

void
bpa_log_stream_record (bpa_log_stream_t *s, f64 wall, u8 event, u32 ip_priv,
		       u32 ip_pub, u16 port_lo, u16 port_hi, u32 pool_id,
		       const char *identity)
{
  u8 **b = &s->outbuf;
  u32 at, set_at;
  u64 ms;

  if (!s->enabled)
    return;
  /* A ceiling on the heap whatever happens between two service ticks: a
   * flood of 1.4 million exhaustion events into a stalled collector grew this
   * buffer until VPP aborted, "Out-of-memory, calling os_panic()" (#547). */
  if (vec_len (s->outbuf) >= BPA_TCP_BUSY_BYTES)
    {
      if (s->fd >= 0 && s->connected)
	bpa_tcp_offload (s);
      else
	bpa_tcp_spill_now (s, 0);
    }

  /*
   * THE SYSLOG WIRE, framed by RFC 6587 octet counting (#20).
   *
   * The length and a space in front of each line, not a trailing newline:
   * non-transparent framing cannot carry a message containing the delimiter
   * and leaves a collector guessing where a truncated line ended, which is
   * the failure this exporter exists to avoid. A line the formatter refuses
   * to build - an event with no name, a value that will not fit - is counted
   * as dropped and never half-written, for the same reason.
   */
  if (s->wire == BPA_WIRE_SYSLOG)
    {
      char line[1024];
      u8 hdr[16];
      u32 n = bpa_syslog_line (line, sizeof (line), wall, event, ip_priv,
			       ip_pub, port_lo, port_hi, pool_id,
			       s->hostname ? (const char *) s->hostname : "-",
			       s->pen, s->pri, identity);
      int hn;

      if (!n)
	{
	  s->n_dropped++;
	  return;
	}
      hn = snprintf ((char *) hdr, sizeof (hdr), "%u ", n);
      bpa_tcp_put (b, hdr, (u32) hn);
      bpa_tcp_put (b, line, n);
      s->seq++;
      s->n_records++;
      return;
    }

  /* One record per message keeps the code simple; the cost is 20 bytes of
   * header per event, and block events are rare by definition. */
  at = bpa_tcp_msg_start (s, b);
  set_at = vec_len (*b);
  bpa_tcp_put_u16 (b, BPA_TCP_TEMPLATE_ID);
  bpa_tcp_put_u16 (b, 0);

  /* wall, not vlib_time_now(): this is the instant the event happened, and it
   * has to be comparable with the UDP exporter's record and with a court
   * request naming a time of day. */
  ms = clib_host_to_net_u64 ((u64) (wall * 1e3));
  bpa_tcp_put (b, &ms, 8);
  bpa_tcp_put (b, &event, 1);
  bpa_tcp_put_u32 (b, ip_priv);
  bpa_tcp_put_u32 (b, ip_pub);
  bpa_tcp_put_u16 (b, port_lo);
  bpa_tcp_put_u16 (b, port_hi);
  bpa_tcp_put_u32 (b, pool_id);
  /*
   * userName, variable length: one octet of length, then the value. The
   * identity the caller was holding, or the unknown marker - never an empty
   * string, because a collector has to tell a box that does not know from one
   * that did not say (#21).
   */
  {
    const char *who = identity && identity[0] ? identity
					      : bpa_identity_unknown;
    u32 len = (u32) strlen (who);
    u8 l8;

    if (len > 254)
      len = 254;
    l8 = (u8) len;
    bpa_tcp_put (b, &l8, 1);
    bpa_tcp_put (b, who, len);
  }

  {
    u16 slen = clib_host_to_net_u16 (vec_len (*b) - set_at);
    clib_memcpy_fast (*b + set_at + 2, &slen, 2);
  }
  bpa_tcp_msg_end (b, at);
  s->seq++;
  s->n_records++;
}

/*
 * The boot marker, over TCP. Written straight into the out buffer like any
 * other record, so it spills and replays with them and cannot overtake them.
 */
void
bpa_log_stream_marker (bpa_log_stream_t *s, f64 wall, f64 init)
{
  u8 **b = &s->outbuf;
  u32 at, set_at;
  u64 ms;

  if (!s->enabled)
    return;
  /* A ceiling on the heap whatever happens between two service ticks: a
   * flood of 1.4 million exhaustion events into a stalled collector grew this
   * buffer until VPP aborted, "Out-of-memory, calling os_panic()" (#547). */
  if (vec_len (s->outbuf) >= BPA_TCP_BUSY_BYTES)
    {
      if (s->fd >= 0 && s->connected)
	bpa_tcp_offload (s);
      else
	bpa_tcp_spill_now (s, 0);
    }

  /* The syslog wire has its own line for this, in the same grammar as a block
   * line: without this branch a syslog stream received IPFIX bytes here, and
   * a collector reading them would have taken the next line's length from
   * the middle of a binary record (#20). */
  if (s->marker_written)
    return;
  s->marker_written = 1;

  if (s->wire == BPA_WIRE_SYSLOG)
    {
      char line[512];
      u8 hdr[16];
      u32 n = bpa_syslog_marker_line (
	line, sizeof (line), wall, init,
	s->hostname ? (const char *) s->hostname : "-", s->pen, s->pri);
      int hn;

      if (!n)
	{
	  s->n_dropped++;
	  return;
	}
      hn = snprintf ((char *) hdr, sizeof (hdr), "%u ", n);
      bpa_tcp_put (b, hdr, (u32) hn);
      bpa_tcp_put (b, line, n);
      s->seq++;
      s->n_records++;
      return;
    }

  at = bpa_tcp_msg_start (s, b);
  set_at = vec_len (*b);
  bpa_tcp_put_u16 (b, BPA_TCP_MARKER_TEMPLATE_ID);
  bpa_tcp_put_u16 (b, 0);

  ms = clib_host_to_net_u64 ((u64) (wall * 1e3));
  bpa_tcp_put (b, &ms, 8);
  ms = clib_host_to_net_u64 ((u64) (init * 1e3));
  bpa_tcp_put (b, &ms, 8);

  {
    u16 slen = clib_host_to_net_u16 (vec_len (*b) - set_at);
    clib_memcpy_fast (*b + set_at + 2, &slen, 2);
  }
  bpa_tcp_msg_end (b, at);
  s->seq++;
  s->n_records++;
}

static void
bpa_tcp_disconnect (bpa_log_stream_t *s)
{
  if (s->fd >= 0)
    close (s->fd);
  s->fd = -1;
  s->connected = 0;
  /* The chunk of the backlog this connection was sending is still in the
   * file: it is read again, whole, by the next one (#547). */
  if (s->backlog)
    {
      u32 front = s->front_extra, n = s->replay_len;

      if (front > vec_len (s->outbuf))
	front = vec_len (s->outbuf);
      if (front + n > vec_len (s->outbuf))
	n = vec_len (s->outbuf) - front;
      /* What was written since the last tick goes behind the backlog first;
       * the chunk is given back to the file; the message in front of it -
       * one the socket had started, older than the whole file - stays, to go
       * out whole on the next connection. */
      bpa_tcp_backlog_tail (s);
      vec_delete (s->outbuf, n, front);
      s->spill_off -= s->replay_len;
      s->replay_len = 0;
      bpa_tcp_save_off (s, s->spill_off);
    }
  else
    s->front_extra = 0;
  /* A new connection is a new stream: whatever the old one took half of goes
   * out again from its start, behind the template the next one begins with. */
  s->head_sent = 0;
}

static void
bpa_tcp_try_connect (bpa_log_stream_t *s, f64 now)
{
  struct sockaddr_in sa;
  int fd, one = 1;

  if (now < s->next_attempt)
    return;
  s->next_attempt = now + BPA_TCP_RETRY_SECS;

  fd = socket (AF_INET, SOCK_STREAM, 0);
  if (fd < 0)
    return;
  fcntl (fd, F_SETFL, O_NONBLOCK);
  setsockopt (fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof (one));

  clib_memset (&sa, 0, sizeof (sa));
  sa.sin_family = AF_INET;
  sa.sin_port = clib_host_to_net_u16 (s->port);
  sa.sin_addr.s_addr = s->collector.as_u32;

  if (connect (fd, (struct sockaddr *) &sa, sizeof (sa)) == 0
      || errno == EINPROGRESS)
    {
      s->fd = fd;
      s->head_sent = 0;
      s->connected = (errno == EINPROGRESS) ? 0 : 1;
      s->pending_connect = (errno == EINPROGRESS) ? 1 : 0;
      s->n_connects++;
      s->last_progress = now;
      if (s->connected)
	bpa_tcp_backlog_open (s);
      /* every new connection starts with the template: without it the collector
       * drops. An explicit flag, not clock arithmetic: vlib_time_now() is
       * monotonic and starts at zero, so "now - 0 > interval" is false during
       * VPP's first minute of life - and the template never went out. */
      s->need_template = 1;
      return;
    }
  close (fd);
}

/* The file beside the spill that says how much of it went out (#547). */
static void
bpa_tcp_off_path (bpa_log_stream_t *s, char *p, u32 size)
{
  snprintf (p, size, "%s.off", (char *) s->spill_path);
}

/*
 * Records how much of the spill file has been taken by a collector, so a
 * restart does not send the whole backlog again: the backlog is not cut from
 * the front of the file as it goes - rewriting 573 MB a megabyte at a time is
 * not an option - so its progress lives here (#547).
 */
static void
bpa_tcp_save_off (bpa_log_stream_t *s, u64 off)
{
  char p[512];
  int fd;

  if (!s->spill_path)
    return;
  bpa_tcp_off_path (s, p, sizeof (p));
  fd = open (p, O_WRONLY | O_CREAT | O_TRUNC, 0640);
  if (fd >= 0)
    {
      if (write (fd, &off, sizeof (off)) != (ssize_t) sizeof (off))
	s->last_errno = errno;
      close (fd);
    }
}

/* The saved progress through the spill file, or 0 when there is none. */
static u64
bpa_tcp_load_off (bpa_log_stream_t *s)
{
  u64 off = 0;
  char p[512];
  int fd;

  if (!s->spill_path)
    return 0;
  bpa_tcp_off_path (s, p, sizeof (p));
  fd = open (p, O_RDONLY);
  if (fd >= 0)
    {
      if (read (fd, &off, sizeof (off)) != (ssize_t) sizeof (off))
	off = 0;
      close (fd);
    }
  return off;
}

/* The backlog is gone: the file and its progress, the progress first. */
static void
bpa_tcp_backlog_done (bpa_log_stream_t *s)
{
  char p[512];

  bpa_tcp_off_path (s, p, sizeof (p));
  unlink (p);
  unlink ((char *) s->spill_path);
  s->backlog = 0;
  s->spill_off = 0;
  s->replay_len = 0;
}

/* Appends `n` bytes to the spill file: counted as spilled, or as dropped. */
static void
bpa_tcp_append (bpa_log_stream_t *s, const u8 *data, u32 n)
{
  int fd;

  if (!n)
    return;
  /* A file already there - an earlier connection's, an earlier boot's - is
   * the backlog this joins, with its progress; none, and any progress file
   * left beside it is stale. */
  if (!s->backlog)
    {
      bpa_tcp_backlog_open (s);
      if (!s->backlog)
	{
	  char p[512];

	  bpa_tcp_off_path (s, p, sizeof (p));
	  unlink (p);
	  s->spill_off = 0;
	}
    }
  {
    struct stat st;

    if (stat ((char *) s->spill_path, &st) == 0
	&& (u64) st.st_size + n > BPA_TCP_SPILL_MAX)
      {
	/* The file at its ceiling: the newest records go, counted - the
	 * disk is the box's, not the log's (#547). */
	s->n_capped += n;
	s->n_dropped += n;
	return;
      }
  }
  fd = open ((char *) s->spill_path, O_WRONLY | O_APPEND | O_CREAT, 0640);
  if (fd < 0)
    {
      /* Nowhere left to write: dropping is the only way out, but it is counted. */
      s->n_dropped += n;
      return;
    }
  if (write (fd, data, n) == (ssize_t) n)
    {
      s->n_spilled += n;
      s->backlog = 1;
    }
  else
    s->n_dropped += n;
  close (fd);
}

/*
 * Writes whatever is in the buffer to disk, whether or not it is over the
 * threshold. Split out from bpa_tcp_spill() for the exit path, which has no
 * threshold to wait for: see bpa_log_tcp_flush_exit().
 *
 * In a backlog the front of the buffer is a chunk of the file itself: it is
 * not written again but given back - spill_off moves back over it - and only
 * what was written after it goes to the end of the file (#547).
 */
static void
bpa_tcp_spill_now (bpa_log_stream_t *s, int all)
{
  u32 from = 0;

  if (!s->spill_path)
    return;
  if (s->backlog)
    {
      u32 front = s->front_extra, n = s->replay_len;

      if (front > vec_len (s->outbuf))
	front = vec_len (s->outbuf);
      if (front + n > vec_len (s->outbuf))
	n = vec_len (s->outbuf) - front;
      vec_delete (s->outbuf, n, front);
      s->spill_off -= s->replay_len;
      s->replay_len = 0;
      /* What the collector took stays taken across the exit this may be. */
      bpa_tcp_save_off (s, s->spill_off);
      /* The message in front of the chunk - one a connection had started,
       * older than the file - stays in memory to go out first, except at the
       * exit, where memory is about to go: it is written last, the one record
       * a backlog can put out of order (#547). */
      if (!all)
	from = front;
    }
  if (all)
    s->front_extra = 0;
  if (vec_len (s->outbuf) > from)
    bpa_tcp_append (s, s->outbuf + from, vec_len (s->outbuf) - from);
  vec_set_len (s->outbuf, from);

  if (all && s->head_sent)
    {
      /*
       * The collector holds the first head_sent bytes of a message whose rest
       * has just gone to the file, so this connection's stream can never be
       * completed - the next record written to it would be read as the tail of
       * that message, and everything after it misframed. The spill replays
       * whole messages on a new connection, behind a new template, so dropping
       * the connection is what makes the file readable instead of a second
       * kind of corruption.
       */
      bpa_tcp_disconnect (s);
    }
}

/*
 * A CONNECTED stream's excess to disk without dropping the connection (#547):
 * everything the socket has not started goes to the file - behind the
 * backlog when there is one, and as its start when there is not - and the
 * message on the wire stays in memory to finish.
 */
static void
bpa_tcp_offload (bpa_log_stream_t *s)
{
  u32 keep = 0, len = vec_len (s->outbuf);

  if (s->backlog)
    {
      bpa_tcp_backlog_tail (s);
      return;
    }
  if (s->head_sent)
    {
      keep = bpa_frame_len (s->wire == BPA_WIRE_SYSLOG, s->outbuf, len);
      if (!keep || keep < s->head_sent || keep > len)
	keep = len; /* nothing it can cut cleanly: the next tick will */
    }
  if (keep >= len)
    return;
  bpa_tcp_append (s, s->outbuf + keep, len - keep);
  vec_set_len (s->outbuf, keep);
  if (s->backlog)
    {
      s->front_extra = keep;
      s->replay_len = 0;
    }
}

/* Writes the excess to disk, to neither grow without bound nor lose an event. */
static void
bpa_tcp_spill (bpa_log_stream_t *s)
{
  if (vec_len (s->outbuf) < BPA_TCP_SPILL_BYTES)
    return;
  bpa_tcp_spill_now (s, 0);
}

/*
 * The spill of a CONNECTED stream (#547): only once the collector has taken
 * nothing for BPA_TCP_STALL_SECS, or the buffer is past BPA_TCP_BUSY_BYTES.
 * A collector that is reading, however slowly, keeps its connection and
 * drains the backlog; one that is not loses it, and is not offered another
 * for a while.
 */
static void
bpa_tcp_spill_stalled (bpa_log_stream_t *s, f64 now)
{
  u32 len = vec_len (s->outbuf);

  if (len < BPA_TCP_SPILL_BYTES)
    return;
  if (len < BPA_TCP_BUSY_BYTES && now - s->last_progress <= BPA_TCP_STALL_SECS)
    return;
  s->n_stalls++;
  bpa_tcp_offload (s);
}

/*
 * In a backlog, the records written since the last tick go to the end of the
 * file, behind everything older (#547). What stays in memory is the template
 * and the chunk being sent.
 */
static void
bpa_tcp_backlog_tail (bpa_log_stream_t *s)
{
  u32 front = s->front_extra + s->replay_len;

  if (!s->backlog || vec_len (s->outbuf) <= front)
    return;
  bpa_tcp_append (s, s->outbuf + front, vec_len (s->outbuf) - front);
  vec_set_len (s->outbuf, front);
}

/*
 * The next chunk of the spill file into the buffer, behind the template: at
 * most BPA_TCP_REPLAY_CHUNK, cut at the last whole message, the file's front
 * resynchronised when it does not frame. Called with nothing of the file in
 * the buffer. The file is never read whole: a 573 MB spill read whole aborted
 * VPP on the #475 bench, "Out-of-memory, calling os_panic()" (#547).
 */
static void
bpa_tcp_replay_chunk (bpa_log_stream_t *s)
{
  int is_syslog = s->wire == BPA_WIRE_SYSLOG;
  struct stat st;
  u8 *buf = 0;
  int fd;

  if (!s->backlog || s->replay_len || !s->spill_path)
    return;
  if (stat ((char *) s->spill_path, &st) || (u64) st.st_size <= s->spill_off)
    {
      bpa_tcp_backlog_done (s);
      return;
    }
  fd = open ((char *) s->spill_path, O_RDONLY);
  if (fd < 0)
    return;
  bpa_tcp_save_off (s, s->spill_off);
  vec_validate (buf, BPA_TCP_REPLAY_CHUNK - 1);
  while (s->spill_off < (u64) st.st_size)
    {
      u64 left = (u64) st.st_size - s->spill_off;
      u32 want = left < BPA_TCP_REPLAY_CHUNK ? (u32) left : BPA_TCP_REPLAY_CHUNK;
      ssize_t got = pread (fd, buf, want, (off_t) s->spill_off);
      u32 n, off = 0, m;

      if (got <= 0)
	break;
      n = (u32) got;
#ifndef BPA_RESYNC_OFF /* lab/syslog-spill-resync.sh's off mode; no box */
      if (!bpa_frame_plausible (is_syslog, buf, n))
	{
	  /* Bytes that do not frame - a fragment the #492 cut wrote, and every
	   * release since replayed for ever - are skipped to the next message,
	   * counted. */
	  u32 skip = bpa_frame_resync (is_syslog, buf, n);

	  if (!skip)
	    skip = 1;
	  s->spill_off += skip;
	  s->n_resynced += skip;
	  continue;
	}
#endif
      while (off < n && (m = bpa_frame_len (is_syslog, buf + off, n - off))
	     && off + m <= n)
	off += m;
      if (!off)
	{
	  /* One message longer than the chunk, or a front this build does not
	   * resynchronise: sent as read, which is what 0.149.4 did. */
	  off = n;
	}
      {
	u8 *dst;

	vec_add2 (s->outbuf, dst, off);
	clib_memcpy_fast (dst, buf, off);
      }
      s->replay_len = off;
      s->spill_off += off;
      s->n_replayed += off;
      break;
    }
  close (fd);
  vec_free (buf);
  if (!s->replay_len && s->spill_off >= (u64) st.st_size)
    bpa_tcp_backlog_done (s);
}

/*
 * A backlog found on connecting: a file left by an earlier connection or an
 * earlier boot, read from where the saved progress says it was left.
 */
static void
bpa_tcp_backlog_open (bpa_log_stream_t *s)
{
  struct stat st;

  if (s->backlog || !s->spill_path)
    return;
  if (stat ((char *) s->spill_path, &st) || st.st_size == 0)
    return;
  s->backlog = 1;
  s->spill_off = bpa_tcp_load_off (s);
  if (s->spill_off > (u64) st.st_size)
    s->spill_off = 0;
}

/* `n` bytes left the front of the buffer: the template's first, then the
 * backlog chunk's (#547). */
static void
bpa_tcp_front_dropped (bpa_log_stream_t *s, u32 n)
{
  u32 t = n < s->front_extra ? n : s->front_extra;

  s->front_extra -= t;
  n -= t;
  s->replay_len -= n < s->replay_len ? n : s->replay_len;
}

/*
 * Drops from the front every message the socket has taken WHOLE.
 *
 * The buffer must always begin at a message boundary. Two things insert at the
 * front - the template and the spill replay - and the spill file is a stream
 * the collector frames by the length in each header, so a file that began with
 * the tail of a message desynchronises everything behind it. Measured that way
 * before this existed: 82917 bytes spilled at a stop, 82917 replayed on the
 * next boot, and the collector decoded one record out of them (#219).
 */
static void
bpa_tcp_drop_sent (bpa_log_stream_t *s)
{
  while (s->head_sent)
    {
      /* All of it taken: all of it goes, whatever the framing claims. A frame
       * claiming more than the buffer holds - read out of a damaged file -
       * otherwise waited for a rest that never comes, the chunk was never
       * let go, and the backlog stopped for good (#547). */
      if (s->head_sent >= vec_len (s->outbuf))
	{
	  u32 all = vec_len (s->outbuf);

	  vec_reset_length (s->outbuf);
	  s->head_sent = 0;
	  bpa_tcp_front_dropped (s, all);
	  break;
	}
      /* By the stream's own framing (#492): this read the IPFIX length on
       * the syslog wire too, and cut the stream 12576 bytes in, mid-message.
       * A front it cannot read is left as it is: guessing at where the next
       * message starts would corrupt the stream for good. */
#ifdef BPA_FRAME_OFF
      /* lab/syslog-reconnect.sh's off mode builds the plugin this way, to
       * show the cut this exists for. No box is built with it. */
      u32 mlen = bpa_frame_len (0, s->outbuf, vec_len (s->outbuf));
#else
      u32 mlen = bpa_frame_len (s->wire == BPA_WIRE_SYSLOG, s->outbuf,
				vec_len (s->outbuf));
#endif

#if !defined(BPA_FRAME_OFF) && !defined(BPA_RESYNC_OFF)
      if (!bpa_frame_plausible (s->wire == BPA_WIRE_SYSLOG, s->outbuf,
				vec_len (s->outbuf)))
	{
	  /* Not a frame at the front. Left there, nothing behind it is ever
	   * dropped and the whole buffer is sent again on every connection
	   * (#547): the bytes before the next message go, counted. */
	  u32 skip = bpa_frame_resync (s->wire == BPA_WIRE_SYSLOG, s->outbuf,
				       vec_len (s->outbuf));
	  if (!skip)
	    break;
	  vec_delete (s->outbuf, skip, 0);
	  s->head_sent = s->head_sent > skip ? s->head_sent - skip : 0;
	  s->n_resynced += skip;
	  bpa_tcp_front_dropped (s, skip);
	  continue;
	}
#endif
      if (!mlen || s->head_sent < mlen)
	break;
      vec_delete (s->outbuf, mlen, 0);
      s->head_sent -= mlen;
      bpa_tcp_front_dropped (s, mlen);
    }
}

/*
 * Whether this stream has anything to send: a buffer, or a backlog on disk
 * (#547). bpa-log-process serves a stream that has every few milliseconds,
 * one that has not once a second - served only by the once-a-second expiry
 * process, a stream took one socket buffer a second, and on the #475 bench a
 * backlog drained at ~135 KB/s while records arrived faster.
 */
int
bpa_log_stream_pending (bpa_log_stream_t *s)
{
  return s->enabled && (vec_len (s->outbuf) > 0 || s->backlog);
}

void
bpa_log_stream_service (bpa_log_stream_t *s, f64 now)
{
  ssize_t n = 0;

  if (!s->enabled)
    return;

  s->n_service++;

  if (s->fd < 0)
    {
      bpa_tcp_try_connect (s, now);
      bpa_tcp_spill (s);
      return;
    }

  if (s->pending_connect)
    {
      struct pollfd pfd = { .fd = s->fd, .events = POLLOUT };
      int err = 0;
      socklen_t l = sizeof (err);

      /*
       * SO_ERROR equal to zero does NOT mean connected: during the handshake
       * it is zero as well. Without waiting for POLLOUT, the exporter declared
       * itself connected with the handshake still in progress and every send
       * returned EAGAIN forever - with "show" saying "connected" and nothing
       * going out.
       */
      if (poll (&pfd, 1, 0) <= 0)
	{
	  if (now > s->next_attempt)
	    {
	      bpa_tcp_disconnect (s); /* handshake did not complete: start over */
	      bpa_tcp_spill (s);
	    }
	  return;
	}

      if (getsockopt (s->fd, SOL_SOCKET, SO_ERROR, &err, &l) || err)
	{
	  s->last_errno = err;
	  bpa_tcp_disconnect (s);
	  bpa_tcp_spill (s);
	  return;
	}

      s->pending_connect = 0;
      s->connected = 1;
      s->last_progress = now;
      bpa_tcp_backlog_open (s);
    }

  /* The backlog first: what was written since the last tick goes behind it
   * in the file, and the next chunk of it comes in (#547). */
  bpa_tcp_backlog_tail (s);
  bpa_tcp_replay_chunk (s);

  /* Never in front of a message the socket has already started: the template
   * would land in the middle of it on the wire. head_sent is zero on every
   * new connection, which is the case need_template is for, so this only ever
   * defers the periodic refresh by a tick. */
  if ((s->need_template
       || now - s->last_template > BPA_TCP_TEMPLATE_IVL)
      && s->head_sent == 0)
    bpa_tcp_prepend_template (s, now);

  /* A collector that has closed its end takes nothing more: the kernel
   * would accept the next send and the peer's RST would discard it (#493).
   * Disconnected here, the buffer is kept, spilled past its threshold, and
   * sent whole behind a new template on the next connection. */
#ifndef BPA_PEER_CHECK_OFF /* lab/syslog-reconnect.sh's off mode; no box */
  if (vec_len (s->outbuf) > s->head_sent && bpa_sock_peer_closed (s->fd))
    {
      bpa_tcp_disconnect (s);
      bpa_tcp_spill (s);
      return;
    }
#endif

  while (vec_len (s->outbuf) > s->head_sent)
    {
      s->n_send_calls++;
      n = send (s->fd, s->outbuf + s->head_sent,
		vec_len (s->outbuf) - s->head_sent,
		MSG_NOSIGNAL);
      if (n < 0)
	s->last_errno = errno;
      if (n > 0)
	{
	  s->head_sent += n;
	  s->n_sent += n;
	  s->last_progress = now;
	  bpa_tcp_drop_sent (s);
	  continue;
	}
      if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
	break; /* slow collector: stays in the buffer, retried next tick */
      bpa_tcp_disconnect (s);
      break;
    }
  /* The chunk went out whole: the next one, up to a few a tick (#547). */
  {
    int more = 8;

    while (s->fd >= 0 && s->backlog && !s->replay_len && more-- > 0
	   && vec_len (s->outbuf) == s->head_sent)
      {
	u32 before;

	bpa_tcp_backlog_tail (s);
	bpa_tcp_replay_chunk (s);
	if (!s->replay_len)
	  break;
	before = s->head_sent;
	while (vec_len (s->outbuf) > s->head_sent)
	  {
	    n = send (s->fd, s->outbuf + s->head_sent,
		      vec_len (s->outbuf) - s->head_sent, MSG_NOSIGNAL);
	    s->n_send_calls++;
	    if (n <= 0)
	      break;
	    s->head_sent += n;
	    s->n_sent += n;
	    s->last_progress = now;
	    bpa_tcp_drop_sent (s);
	  }
	if (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK)
	  {
	    s->last_errno = errno;
	    bpa_tcp_disconnect (s);
	    break;
	  }
	if (s->head_sent == before && vec_len (s->outbuf))
	  break;
      }
  }

  if (s->fd < 0)
    {
      bpa_tcp_spill (s);
      return;
    }
  bpa_tcp_spill_stalled (s, now);
}

/*
 * The last thing this exporter does before the process leaves: whatever the
 * socket would not take goes to the spill file.
 *
 * WHAT IS AT RISK AND WHAT IS NOT. send() is non-blocking, so a collector that
 * is connected and not reading closes the window and send() returns EAGAIN -
 * and the records stay in outbuf, because the spill only triggers above 1 MB.
 * Bytes the socket DID take are the kernel's problem and it delivers them
 * behind the FIN; bytes it refused are in this process and die with it. The
 * stop drain (bpa_drain_blocks()) writes one natEvent 17 per block still held
 * into that same buffer, so on a box with blocks held and a collector that is
 * behind, the releases are exactly what is lost - and in dynamic mode a
 * release that never arrives leaves the allocation open in the retention log
 * for ever, which is the whole of #217 reopened by the transport (#219).
 *
 * WHY IT SPILLS INSTEAD OF WAITING FOR THE SOCKET. Waiting was the obvious
 * shape and it is the worse one:
 *
 *   - it buys nothing in the case that produces the loss. The collector that
 *     leaves bytes here is the one that has stopped reading; its window is
 *     shut, so POLLOUT does not come and the wait expires having sent nothing.
 *     A collector that IS reading has already taken everything, and this
 *     function finds an empty buffer and returns.
 *   - it is a wait in a stop path. A box that will not stop is worse than a
 *     record that is late, and every second here is a second of a reboot.
 *   - bytes pushed into the socket at the last moment are no longer
 *     recoverable: they cannot also be spilled without the collector getting
 *     them twice. Spilling is certain, and the spill file is replayed in
 *     order on the next connection, which is machinery that already exists
 *     for exactly this - a collector that could not take the bytes when they
 *     were written.
 *
 * SO THE BOUND IS ONE write(). There is no timed wait to fit inside anything:
 * the appliance's ExecStop (appliance/systemd/vpp-stop-drain.conf) sets no
 * timeout of its own and vpp.service ships none, so the budget is systemd's
 * DefaultTimeoutStopSec of 90 s, and this costs a syscall on a buffer the
 * threshold above caps at 1 MB.
 *
 * NO RECORD ARRIVES TWICE. Every whole message the socket took has already
 * been dropped from the buffer (bpa_tcp_drop_sent()). What can be written
 * twice is the ONE message the socket took only the front of: the collector
 * holds a fragment it can never complete and discards with the connection,
 * and gets the whole message from the spill. A fragment nobody could decode
 * is not a record.
 */
u32
bpa_log_stream_flush_exit (bpa_log_stream_t *s)
{
  u64 before = s->n_spilled;

  if (!s->enabled || !vec_len (s->outbuf))
    return 0;

  bpa_tcp_spill_now (s, 1);
  /* What reached the FILE, not what was offered to it. A spill path that
   * cannot be written counts the bytes as dropped and empties the buffer just
   * the same, and measuring by how much the buffer shrank would report that
   * as a save. */
  return (u32) (s->n_spilled - before);
}

#ifndef BPA_STANDALONE

/* not a unit: it opens or closes a TCP connection to a
 * collector, and is not compiled under BPA_STANDALONE at all - the guard
 * below it is what leaves the RECORD WRITER of this file testable, which is
 * what test/test_bpa_log_tcp.c drives. lab/ipfix-tcp.sh is where a
 * connection is made to a collector on a box. */
clib_error_t *
bpa_log_stream_enable (bpa_log_stream_t *s, ip4_address_t *collector, u16 port, int enable)
{
  if (!enable)
    {
      bpa_tcp_disconnect (s);
      s->enabled = 0;
      return 0;
    }

  {
    /* See bpa_log_enable(): the same transition, from the other transport. */
    int was = s->enabled || bpa_log_main.enabled;

    if (!s->enabled)
      s->marker_written = 0; /* a stream switched on announces the boot once */
    s->collector = *collector;
    s->port = port ? port : (s->wire == BPA_WIRE_SYSLOG ? 601 : 4739);
    s->fd = -1;
    s->next_attempt = 0;
    s->enabled = 1;
    /* Each wire spills into a file of its own: two streams sharing one would
     * replay each other's bytes into the wrong collector, and the file is a
     * stream the collector frames. 601 is RFC 6587's port for syslog over
     * TCP, as 4739 is IPFIX's. */
    if (!s->spill_path)
      s->spill_path =
	s->wire == BPA_WIRE_SYSLOG
	  ? format (0, "/var/log/prisma-routing/syslog-spill.bin%c", 0)
	  : format (0, "/var/log/prisma-routing/ipfix-spill.bin%c", 0);
    if (s->wire == BPA_WIRE_SYSLOG && !s->hostname)
      {
	char hn[256];

	/* The HOSTNAME field is the box's own name and a collector groups by
	 * it. Read once, here, rather than per line: it is answered by the
	 * kernel and cannot change without a reboot this stream does not
	 * survive. */
	if (gethostname (hn, sizeof (hn)) != 0)
	  hn[0] = 0;
	hn[sizeof (hn) - 1] = 0;
	s->hostname = format (0, "%s%c", hn[0] ? hn : "-", 0);
      }
    if (!was)
      {
	/* The marker before anything else, for the reason bpa_log_enable()
	 * gives: the collector has to know which incarnation these records
	 * belong to before it reads them. */
	bpa_log_main.next_marker = 0;
	bpa_log_pins_replay ();
      }
  }
  return 0;
}

#endif /* BPA_STANDALONE */


/*
 * The IPFIX stream's own names, kept so that every caller reads the way it
 * did. One line each: what they are is the stream above, and which stream
 * they mean is the whole of their content.
 */
void
bpa_log_tcp_record (f64 wall, u8 event, u32 ip_priv, u32 ip_pub, u16 port_lo,
		    u16 port_hi, u32 pool_id)
{
  /* The IPFIX record carries no identity field yet - the element has to be
   * chosen rather than guessed at, and a wrong one is worse than none (#21
   * step 4). The stream takes the parameter all the same, so the two wires
   * differ in what they can express and not in what they are given. */
  bpa_log_stream_record (&bpa_log_tcp, wall, event, ip_priv, ip_pub, port_lo,
			 port_hi, pool_id, 0);
}

void
bpa_log_tcp_marker (f64 wall, f64 init)
{
  bpa_log_stream_marker (&bpa_log_tcp, wall, init);
}

void
bpa_log_tcp_service (f64 now)
{
  bpa_log_stream_service (&bpa_log_tcp, now);
}

u32
bpa_log_tcp_flush_exit (void)
{
  return bpa_log_stream_flush_exit (&bpa_log_tcp);
}

#ifndef BPA_STANDALONE

/*
 * The IPFIX stream's enable, under the name its callers already use.
 *
 * It was missing for one commit and the plugin would not LOAD: the .so links
 * lazily, so the build was green, the unit suites were green - they are
 * standalone and never see this half - and VPP failed at start with
 * "undefined symbol: bpa_log_tcp_enable". lab/ipfix-tcp.sh is what said so.
 */
/* not a unit: the one-line door to the above, behind the same
 * guard and for the same reason. */
clib_error_t *
bpa_log_tcp_enable (ip4_address_t *collector, u16 port, int enable)
{
  return bpa_log_stream_enable (&bpa_log_tcp, collector, port, enable);
}

#endif /* not BPA_STANDALONE */
