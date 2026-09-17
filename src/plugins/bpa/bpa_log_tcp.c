/*
 * bpa_log_tcp.c - IPFIX export over TCP, with a local buffer.
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

#define BPA_TCP_TEMPLATE_ID  256
/* The boot marker's template. See bpa_log.h for what it carries and why. */
#define BPA_TCP_MARKER_TEMPLATE_ID 257
#define BPA_TCP_DOMAIN	     1
#define BPA_TCP_RETRY_SECS   5.0
#define BPA_TCP_TEMPLATE_IVL 60.0
/* Above this, the in-memory buffer goes to the spill file. */
#define BPA_TCP_SPILL_BYTES  (1 << 20)

bpa_log_tcp_t bpa_log_tcp;

/* RFC 8158 Table 21 elements, in the same order as the record. */
static const u16 bpa_tcp_ies[][2] = {
  { 323, 8 }, { 230, 1 }, { 8, 4 }, { 225, 4 }, { 361, 2 }, { 362, 2 },
  { 283, 4 },
};

/* observationTimeMilliseconds, systemInitTimeMilliseconds. */
static const u16 bpa_tcp_marker_ies[][2] = {
  { 323, 8 }, { 160, 8 },
};

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
bpa_tcp_msg_start (u8 **b)
{
  u32 at = vec_len (*b);

  bpa_tcp_put_u16 (b, 10); /* version */
  bpa_tcp_put_u16 (b, 0);  /* length, filled in by msg_end */
  /* RFC 7011 Export Time: seconds since the UNIX epoch. It used to be the
   * f64 the caller had at hand, which was vlib_time_now() - seconds since VPP
   * started. Read here, so there is nothing for a caller to get wrong. */
  bpa_tcp_put_u32 (b, (u32) unix_time_now ());
  bpa_tcp_put_u32 (b, bpa_log_tcp.seq);
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
bpa_tcp_prepend_template (f64 now)
{
  u8 *tmp = 0;
  u8 **b = &tmp;
  u32 at = bpa_tcp_msg_start (b);
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

  vec_insert (bpa_log_tcp.outbuf, vec_len (tmp), 0);
  clib_memcpy_fast (bpa_log_tcp.outbuf, tmp, vec_len (tmp));
  vec_free (tmp);

  bpa_log_tcp.last_template = now;
  bpa_log_tcp.need_template = 0;
}

void
bpa_log_tcp_record (f64 wall, u8 event, u32 ip_priv, u32 ip_pub, u16 port_lo,
		    u16 port_hi, u32 pool_id)
{
  u8 **b = &bpa_log_tcp.outbuf;
  u32 at, set_at;
  u64 ms;

  if (!bpa_log_tcp.enabled)
    return;

  /* One record per message keeps the code simple; the cost is 20 bytes of
   * header per event, and block events are rare by definition. */
  at = bpa_tcp_msg_start (b);
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

  {
    u16 slen = clib_host_to_net_u16 (vec_len (*b) - set_at);
    clib_memcpy_fast (*b + set_at + 2, &slen, 2);
  }
  bpa_tcp_msg_end (b, at);
  bpa_log_tcp.seq++;
  bpa_log_tcp.n_records++;
}

/*
 * The boot marker, over TCP. Written straight into the out buffer like any
 * other record, so it spills and replays with them and cannot overtake them.
 */
void
bpa_log_tcp_marker (f64 wall, f64 init)
{
  u8 **b = &bpa_log_tcp.outbuf;
  u32 at, set_at;
  u64 ms;

  if (!bpa_log_tcp.enabled)
    return;

  at = bpa_tcp_msg_start (b);
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
  bpa_log_tcp.seq++;
  bpa_log_tcp.n_records++;
}

static void
bpa_tcp_disconnect (void)
{
  if (bpa_log_tcp.fd >= 0)
    close (bpa_log_tcp.fd);
  bpa_log_tcp.fd = -1;
  bpa_log_tcp.connected = 0;
  /* A new connection is a new stream: whatever the old one took half of goes
   * out again from its start, behind the template the next one begins with. */
  bpa_log_tcp.head_sent = 0;
}

static void
bpa_tcp_try_connect (f64 now)
{
  struct sockaddr_in sa;
  int fd, one = 1;

  if (now < bpa_log_tcp.next_attempt)
    return;
  bpa_log_tcp.next_attempt = now + BPA_TCP_RETRY_SECS;

  fd = socket (AF_INET, SOCK_STREAM, 0);
  if (fd < 0)
    return;
  fcntl (fd, F_SETFL, O_NONBLOCK);
  setsockopt (fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof (one));

  clib_memset (&sa, 0, sizeof (sa));
  sa.sin_family = AF_INET;
  sa.sin_port = clib_host_to_net_u16 (bpa_log_tcp.port);
  sa.sin_addr.s_addr = bpa_log_tcp.collector.as_u32;

  if (connect (fd, (struct sockaddr *) &sa, sizeof (sa)) == 0
      || errno == EINPROGRESS)
    {
      bpa_log_tcp.fd = fd;
      bpa_log_tcp.head_sent = 0;
      bpa_log_tcp.connected = (errno == EINPROGRESS) ? 0 : 1;
      bpa_log_tcp.pending_connect = (errno == EINPROGRESS) ? 1 : 0;
      bpa_log_tcp.n_connects++;
      /* every new connection starts with the template: without it the collector
       * drops. An explicit flag, not clock arithmetic: vlib_time_now() is
       * monotonic and starts at zero, so "now - 0 > interval" is false during
       * VPP's first minute of life - and the template never went out. */
      bpa_log_tcp.need_template = 1;
      return;
    }
  close (fd);
}

/*
 * Writes whatever is in the buffer to disk, whether or not it is over the
 * threshold. Split out from bpa_tcp_spill() for the exit path, which has no
 * threshold to wait for: see bpa_log_tcp_flush_exit().
 */
static void
bpa_tcp_spill_now (void)
{
  int fd;

  if (!vec_len (bpa_log_tcp.outbuf) || !bpa_log_tcp.spill_path)
    return;

  fd = open ((char *) bpa_log_tcp.spill_path,
	     O_WRONLY | O_APPEND | O_CREAT, 0640);
  if (fd < 0)
    {
      /* Nowhere left to write: dropping is the only way out, but it is counted. */
      bpa_log_tcp.n_dropped += vec_len (bpa_log_tcp.outbuf);
      vec_reset_length (bpa_log_tcp.outbuf);
      return;
    }
  if (write (fd, bpa_log_tcp.outbuf, vec_len (bpa_log_tcp.outbuf))
      == (ssize_t) vec_len (bpa_log_tcp.outbuf))
    bpa_log_tcp.n_spilled += vec_len (bpa_log_tcp.outbuf);
  else
    bpa_log_tcp.n_dropped += vec_len (bpa_log_tcp.outbuf);
  close (fd);
  vec_reset_length (bpa_log_tcp.outbuf);

  if (bpa_log_tcp.head_sent)
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
      bpa_tcp_disconnect ();
    }
}

/* Writes the excess to disk, to neither grow without bound nor lose an event. */
static void
bpa_tcp_spill (void)
{
  if (vec_len (bpa_log_tcp.outbuf) < BPA_TCP_SPILL_BYTES)
    return;
  bpa_tcp_spill_now ();
}

/* Resends the spill before anything new, to preserve ordering. */
static void
bpa_tcp_replay_spill (void)
{
  struct stat st;
  u8 *buf;
  int fd;
  ssize_t n;

  if (stat ((char *) bpa_log_tcp.spill_path, &st) || st.st_size == 0)
    return;

  fd = open ((char *) bpa_log_tcp.spill_path, O_RDONLY);
  if (fd < 0)
    return;

  buf = 0;
  vec_validate (buf, st.st_size - 1);
  n = read (fd, buf, st.st_size);
  close (fd);

  if (n == st.st_size)
    {
      /* Prepend: the spill is older than what is in memory, and an allocation
       * has to reach the collector before its own release. */
      vec_insert (bpa_log_tcp.outbuf, n, 0);
      clib_memcpy_fast (bpa_log_tcp.outbuf, buf, n);
      unlink ((char *) bpa_log_tcp.spill_path);
      bpa_log_tcp.n_replayed += n;
    }
  vec_free (buf);
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
bpa_tcp_drop_sent (void)
{
  while (bpa_log_tcp.head_sent >= 4)
    {
      u16 mlen;

      clib_memcpy_fast (&mlen, bpa_log_tcp.outbuf + 2, 2);
      mlen = clib_net_to_host_u16 (mlen);
      /* Defensive: every message in here was written by this file, so a
       * length below the header size is not reachable. If it ever were,
       * guessing at where the next message starts would corrupt the stream
       * for good, where stopping leaves it as it is. */
      if (mlen < 16 || bpa_log_tcp.head_sent < mlen)
	break;
      vec_delete (bpa_log_tcp.outbuf, mlen, 0);
      bpa_log_tcp.head_sent -= mlen;
    }
}

void
bpa_log_tcp_service (f64 now)
{
  ssize_t n;

  if (!bpa_log_tcp.enabled)
    return;

  bpa_log_tcp.n_service++;

  if (bpa_log_tcp.fd < 0)
    {
      bpa_tcp_try_connect (now);
      bpa_tcp_spill ();
      return;
    }

  if (bpa_log_tcp.pending_connect)
    {
      struct pollfd pfd = { .fd = bpa_log_tcp.fd, .events = POLLOUT };
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
	  if (now > bpa_log_tcp.next_attempt)
	    {
	      bpa_tcp_disconnect (); /* handshake did not complete: start over */
	      bpa_tcp_spill ();
	    }
	  return;
	}

      if (getsockopt (bpa_log_tcp.fd, SOL_SOCKET, SO_ERROR, &err, &l) || err)
	{
	  bpa_log_tcp.last_errno = err;
	  bpa_tcp_disconnect ();
	  bpa_tcp_spill ();
	  return;
	}

      bpa_log_tcp.pending_connect = 0;
      bpa_log_tcp.connected = 1;
      bpa_tcp_replay_spill ();
    }

  /* Never in front of a message the socket has already started: the template
   * would land in the middle of it on the wire. head_sent is zero on every
   * new connection, which is the case need_template is for, so this only ever
   * defers the periodic refresh by a tick. */
  if ((bpa_log_tcp.need_template
       || now - bpa_log_tcp.last_template > BPA_TCP_TEMPLATE_IVL)
      && bpa_log_tcp.head_sent == 0)
    bpa_tcp_prepend_template (now);

  while (vec_len (bpa_log_tcp.outbuf) > bpa_log_tcp.head_sent)
    {
      bpa_log_tcp.n_send_calls++;
      n = send (bpa_log_tcp.fd, bpa_log_tcp.outbuf + bpa_log_tcp.head_sent,
		vec_len (bpa_log_tcp.outbuf) - bpa_log_tcp.head_sent,
		MSG_NOSIGNAL);
      if (n < 0)
	bpa_log_tcp.last_errno = errno;
      if (n > 0)
	{
	  bpa_log_tcp.head_sent += n;
	  bpa_log_tcp.n_sent += n;
	  bpa_tcp_drop_sent ();
	  continue;
	}
      if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
	break; /* slow collector: stays in the buffer, retried next tick */
      bpa_tcp_disconnect ();
      break;
    }

  bpa_tcp_spill ();
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
bpa_log_tcp_flush_exit (void)
{
  u64 before = bpa_log_tcp.n_spilled;

  if (!bpa_log_tcp.enabled || !vec_len (bpa_log_tcp.outbuf))
    return 0;

  bpa_tcp_spill_now ();
  /* What reached the FILE, not what was offered to it. A spill path that
   * cannot be written counts the bytes as dropped and empties the buffer just
   * the same, and measuring by how much the buffer shrank would report that
   * as a save. */
  return (u32) (bpa_log_tcp.n_spilled - before);
}

#ifndef BPA_STANDALONE

clib_error_t *
bpa_log_tcp_enable (ip4_address_t *collector, u16 port, int enable)
{
  if (!enable)
    {
      bpa_tcp_disconnect ();
      bpa_log_tcp.enabled = 0;
      return 0;
    }

  {
    /* See bpa_log_enable(): the same transition, from the other transport. */
    int was = bpa_log_tcp.enabled || bpa_log_main.enabled;

    bpa_log_tcp.collector = *collector;
    bpa_log_tcp.port = port ? port : 4739;
    bpa_log_tcp.fd = -1;
    bpa_log_tcp.next_attempt = 0;
    bpa_log_tcp.enabled = 1;
    if (!bpa_log_tcp.spill_path)
      bpa_log_tcp.spill_path =
	format (0, "/var/log/hexarouting/ipfix-spill.bin%c", 0);
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
