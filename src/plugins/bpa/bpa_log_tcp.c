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

#include "bpa.h"
#include "bpa_log.h"

#define BPA_TCP_TEMPLATE_ID  256
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
bpa_tcp_msg_start (u8 **b, f64 now)
{
  u32 at = vec_len (*b);

  bpa_tcp_put_u16 (b, 10); /* versao */
  bpa_tcp_put_u16 (b, 0);  /* tamanho, preenchido em msg_end */
  bpa_tcp_put_u32 (b, (u32) now);
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
bpa_tcp_prepend_template (f64 now)
{
  u8 *tmp = 0;
  u8 **b = &tmp;
  u32 at = bpa_tcp_msg_start (b, now);
  u32 set_at = vec_len (*b);
  u32 i;

  bpa_tcp_put_u16 (b, 2); /* set de template */
  bpa_tcp_put_u16 (b, 0);
  bpa_tcp_put_u16 (b, BPA_TCP_TEMPLATE_ID);
  bpa_tcp_put_u16 (b, ARRAY_LEN (bpa_tcp_ies));
  for (i = 0; i < ARRAY_LEN (bpa_tcp_ies); i++)
    {
      bpa_tcp_put_u16 (b, bpa_tcp_ies[i][0]);
      bpa_tcp_put_u16 (b, bpa_tcp_ies[i][1]);
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
bpa_log_tcp_record (f64 now, u8 event, u32 ip_priv, u32 ip_pub, u16 port_lo,
		    u16 port_hi, u32 pool_id)
{
  u8 **b = &bpa_log_tcp.outbuf;
  u32 at, set_at;
  u64 ms;

  if (!bpa_log_tcp.enabled)
    return;

  /* One record per message keeps the code simple; the cost is 20 bytes of
   * header per event, and block events are rare by definition. */
  at = bpa_tcp_msg_start (b, now);
  set_at = vec_len (*b);
  bpa_tcp_put_u16 (b, BPA_TCP_TEMPLATE_ID);
  bpa_tcp_put_u16 (b, 0);

  ms = clib_host_to_net_u64 ((u64) (now * 1e3));
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

static void
bpa_tcp_disconnect (void)
{
  if (bpa_log_tcp.fd >= 0)
    close (bpa_log_tcp.fd);
  bpa_log_tcp.fd = -1;
  bpa_log_tcp.connected = 0;
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

/* Writes the excess to disk, to neither grow without bound nor lose an event. */
static void
bpa_tcp_spill (void)
{
  int fd;

  if (vec_len (bpa_log_tcp.outbuf) < BPA_TCP_SPILL_BYTES)
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

  if (bpa_log_tcp.need_template
      || now - bpa_log_tcp.last_template > BPA_TCP_TEMPLATE_IVL)
    bpa_tcp_prepend_template (now);

  while (vec_len (bpa_log_tcp.outbuf))
    {
      bpa_log_tcp.n_send_calls++;
      n = send (bpa_log_tcp.fd, bpa_log_tcp.outbuf,
		vec_len (bpa_log_tcp.outbuf), MSG_NOSIGNAL);
      if (n < 0)
	bpa_log_tcp.last_errno = errno;
      if (n > 0)
	{
	  vec_delete (bpa_log_tcp.outbuf, n, 0);
	  bpa_log_tcp.n_sent += n;
	  continue;
	}
      if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
	break; /* coletor lento: fica no buffer, tenta no proximo tick */
      bpa_tcp_disconnect ();
      break;
    }

  bpa_tcp_spill ();
}

clib_error_t *
bpa_log_tcp_enable (ip4_address_t *collector, u16 port, int enable)
{
  if (!enable)
    {
      bpa_tcp_disconnect ();
      bpa_log_tcp.enabled = 0;
      return 0;
    }

  bpa_log_tcp.collector = *collector;
  bpa_log_tcp.port = port ? port : 4739;
  bpa_log_tcp.fd = -1;
  bpa_log_tcp.next_attempt = 0;
  bpa_log_tcp.enabled = 1;
  if (!bpa_log_tcp.spill_path)
    bpa_log_tcp.spill_path =
      format (0, "/var/log/hexarouting/ipfix-spill.bin%c", 0);
  return 0;
}
