/*
 * bpa_sock.h - has the collector closed its end of the exporter's connection?
 * (#493)
 *
 * The TCP exporter sent without asking. When a collector exits it sends a
 * FIN; the next send() on this side still succeeds - the kernel takes the
 * bytes - and the peer answers them with an RST, so they are gone, and only
 * the send after that fails and makes the exporter reconnect. Measured with
 * lab/syslog-reconnect.sh: collector A exited, the box wrote 50 allocations,
 * and its counters said 153 lines written, 41099 bytes sent, 0 spilled, 0
 * dropped - while the collector that replaced A received two boot markers.
 * A retention log that loses the records written while its collector
 * restarts, and reports them delivered, has a hole exactly where it is read.
 *
 * So the exporter asks before every send. A closed peer shows as POLLRDHUP
 * or POLLHUP, or as a readable socket whose read returns zero bytes (the FIN)
 * on a kernel or a socket type without POLLRDHUP. A collector does not send
 * on this stream, so a readable socket with data is not a close, and the
 * data is left where it is. What this cannot see is a collector host that
 * vanishes without a FIN; nothing on the sending side of TCP can, and
 * docs/logging.md says so.
 */
#ifndef __included_bpa_sock_h__
#define __included_bpa_sock_h__

#include <errno.h>
#include <poll.h>
#include <sys/socket.h>

/* 1 when the peer of `fd` has closed or the connection has failed, 0 when it
 * is open (or cannot be asked: a bad descriptor is the caller's to see on
 * its own send). Never blocks. */
static inline int
bpa_sock_peer_closed (int fd)
{
  struct pollfd p = { .fd = fd, .events = POLLIN
#ifdef POLLRDHUP
				       | POLLRDHUP
#endif
  };
  char c;
  ssize_t n;

  if (fd < 0 || poll (&p, 1, 0) <= 0)
    return 0;
  if (p.revents & POLLNVAL)
    return 0;
  if (p.revents & (POLLHUP | POLLERR))
    return 1;
#ifdef POLLRDHUP
  if (p.revents & POLLRDHUP)
    return 1;
#endif
  if (!(p.revents & POLLIN))
    return 0;
  n = recv (fd, &c, 1, MSG_PEEK | MSG_DONTWAIT);
  if (n == 0)
    return 1;
  if (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)
    return 1;
  return 0;
}

#endif /* __included_bpa_sock_h__ */
