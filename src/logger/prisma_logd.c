/*
 * prisma_logd.c - the log container's process (#550): it carries the CGNAT's
 * TCP log streams - syslog (RFC 5424 over TCP) and IPFIX over TCP - so that a
 * stalled collector, a reconnect, or the replay of a spill of hundreds of
 * megabytes costs the dataplane nothing.
 *
 * WHY (measured on the legacy bench, 2026-10-01): with the streams in VPP, a
 * stalled syslog collector cost 78% of a 30k pps burst at rx (0.149.5), and
 * after #547 bounded the memory, replaying a 573 MB spill on VPP's main thread
 * still cost 11.7% - 27% with a 3 s monitoring poll. The maintainer's rule:
 * log export must never affect forwarding or translation. So the work leaves
 * VPP: VPP only copies each event into the export ring of
 * /dev/shm/prisma-bpa-log (bpa_log_ring.h), and this process formats, sends,
 * spills and replays it, in a container of its own, on the control-plane
 * cores, under a memory limit.
 *
 * THE SAME CODE AS VPP USED. The streams are bpa_log_tcp.c, compiled here
 * standalone exactly as test/test_bpa_log_tcp.c compiles it - the record
 * writers, the non-blocking socket, the spill file and its chunked replay
 * (#547) - so the wire formats are the ones the collectors already read, and
 * a fix to either side is a fix to both. Its sockets are kernel sockets in the
 * host's network namespace, as VPP's were: the same route, the same source
 * address, the same VRF.
 *
 * WHO CARRIES THE STREAMS is decided by VPP from this process's heartbeat
 * (bpa_log_handover): while it is fresh, VPP has spilled what it held, closed
 * its connections and set tcp_owner to the logger; this process touches the
 * spill files and the collectors only while that is so. On SIGTERM it spills
 * what it holds and zeroes the heartbeat, and VPP takes the streams back at
 * once.
 *
 * Plain C over POSIX: test/test_prisma_logd.c drives every function below
 * except main() against a segment it lays out itself.
 */
#define _GNU_SOURCE

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "bpa_log_shim.h"
#include "../plugins/bpa/bpa_log_tcp.c"
#include "bpa_log_ring.h"
#include "logd_ipfix_udp.c"

/* What this process holds of the segment. */
typedef struct
{
  u8 *base;
  u64 bytes;
  u64 generation;
  bpa_log_seg_cfg_t cfg[2];	/* the last configuration applied */
  u8 owned;			/* we carry the streams right now */
  char spill_dir[256];
  u64 lost_owner;		/* times VPP took the streams back from us */
  u64 xdrained;
  logd_udp_t udp;		/* the RFC 8158 export over UDP (#550 step 3) */
  u8 udp_on;			/* we carry it right now */
} logd_t;

/* The stream behind index i of the segment's cfg and stats. */
static bpa_log_stream_t *
logd_stream (u32 i)
{
  return i == BPA_LOG_STREAM_SYSLOG ? &bpa_log_syslog : &bpa_log_tcp;
}

/* Maps the segment at path if it is one this build reads: 0, or -1 with
 * nothing mapped. */
int
logd_attach (logd_t *l, const char *path)
{
  struct stat st;
  int fd = open (path, O_RDWR);
  u8 *m;

  if (fd < 0)
    return -1;
  if (fstat (fd, &st) || (u64) st.st_size < BPA_LOG_SEG_HDR_BYTES)
    {
      close (fd);
      return -1;
    }
  m = mmap (0, st.st_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
  close (fd);
  if (m == MAP_FAILED)
    return -1;
  if (bpa_log_seg_check (m, st.st_size, sizeof (bpa_log_event_t)))
    {
      munmap (m, st.st_size);
      return -1;
    }
  l->base = m;
  l->bytes = st.st_size;
  l->generation = ((bpa_log_seg_hdr_t *) m)->generation;
  memset (l->cfg, 0, sizeof (l->cfg));
  return 0;
}

/* Lets go of a segment VPP replaced (a dataplane restart makes a new one). */
void
logd_detach (logd_t *l)
{
  if (l->base)
    munmap (l->base, l->bytes);
  l->base = 0;
  l->bytes = 0;
}

/* Applies stream i's configuration from the segment when it changed: 1 when
 * it did, 0 when not, -1 while VPP is writing it. */
int
logd_apply_cfg (logd_t *l, u32 i)
{
  bpa_log_seg_hdr_t *h = (bpa_log_seg_hdr_t *) l->base;
  bpa_log_stream_t *s = logd_stream (i);
  bpa_log_seg_cfg_t c;
  char path[512];

  if (bpa_log_seg_cfg_read (&h->cfg[i], &c))
    return -1;
  if (c.gen == l->cfg[i].gen)
    return 0;
  if (s->enabled
      && (!c.enabled || c.collector != l->cfg[i].collector
	  || c.port != l->cfg[i].port))
    bpa_log_stream_release (s);
  s->wire = i == BPA_LOG_STREAM_SYSLOG ? BPA_WIRE_SYSLOG : BPA_WIRE_IPFIX;
  s->collector.as_u32 = c.collector;
  s->port = c.port ? c.port : (s->wire == BPA_WIRE_SYSLOG ? 601 : 4739);
  s->pen = c.pen;
  s->pri = c.pri;
  free (s->hostname);
  s->hostname = (u8 *) strdup (c.hostname[0] ? c.hostname : "-");
  if (!s->spill_path)
    {
      snprintf (path, sizeof (path), "%s/%s", l->spill_dir,
		i == BPA_LOG_STREAM_SYSLOG ? "syslog-spill.bin"
					   : "ipfix-spill.bin");
      s->spill_path = (u8 *) strdup (path);
    }
  if (c.enabled && !s->enabled)
    {
      s->fd = -1;
      s->next_attempt = 0;
      s->marker_written = 0;
    }
  s->enabled = c.enabled;
  l->cfg[i] = c;
  return 1;
}

/* Turns n events of the export ring into records on the enabled streams;
 * returns how many it took. */
u32
logd_drain (logd_t *l, u32 max)
{
  bpa_log_seg_hdr_t *h = (bpa_log_seg_hdr_t *) l->base;
  bpa_log_ring_t *x = bpa_log_seg_xring (l->base);
  u32 tail, n = bpa_log_ring_ready (x, &tail), k, i;

  if (n > max)
    n = max;
  for (k = 0; k < n; k++)
    {
      bpa_log_xev_t *v =
	bpa_log_ring_slot (x, BPA_LOG_XRING_SLOTS, BPA_LOG_XEV_BYTES, tail + k);

      if (l->udp_on)
	{
	  if (v->kind == BPA_LOG_XEV_MARKER)
	    logd_udp_marker (&l->udp, v->wall, v->wall, h->boot_wall);
	  else
	    logd_udp_record (&l->udp, v->wall, v->wall, v->ev.event,
			     v->ev.ip_priv, v->ev.ip_pub, v->ev.port_lo,
			     v->ev.port_hi, v->ev.pool_id, &v->ev.sub6);
	}

      for (i = 0; i < 2; i++)
	{
	  bpa_log_stream_t *s = logd_stream (i);

	  if (!s->enabled)
	    continue;
	  if (v->kind == BPA_LOG_XEV_MARKER)
	    bpa_log_stream_marker (s, v->wall, h->boot_wall);
	  else
	    bpa_log_stream_record (
	      s, v->wall, v->ev.event, v->ev.ip_priv, v->ev.ip_pub,
	      v->ev.port_lo, v->ev.port_hi, v->ev.pool_id,
	      /* The IPFIX record carries no identity yet (#21 step 4), the
	       * same as VPP's bpa_log_tcp_record() gives it. */
	      i == BPA_LOG_STREAM_SYSLOG ? v->ev.identity : 0, &v->ev.sub6);
	}
    }
  bpa_log_ring_consume (x, n);
  l->xdrained += n;
  /* A drain's records go out together, as VPP's bpa_log_flush() sends them. */
  if (l->udp_on && n)
    logd_udp_flush (&l->udp, (u32) time (0));
  return n;
}

/* What each stream did, where VPP's `show cgnat logging` reads it. */
void
logd_publish_stats (logd_t *l)
{
  bpa_log_seg_hdr_t *h = (bpa_log_seg_hdr_t *) l->base;
  u32 i;

  for (i = 0; i < 2; i++)
    {
      bpa_log_stream_t *s = logd_stream (i);
      bpa_log_seg_stats_t *t = &h->stats[i];

      t->n_records = s->n_records;
      t->n_sent = s->n_sent;
      t->n_spilled = s->n_spilled;
      t->n_replayed = s->n_replayed;
      t->n_dropped = s->n_dropped;
      t->n_connects = s->n_connects;
      t->n_service = s->n_service;
      t->n_send_calls = s->n_send_calls;
      t->n_resynced = s->n_resynced;
      t->n_stalls = s->n_stalls;
      t->n_capped = s->n_capped;
      t->pending = vec_len (s->outbuf);
      t->connected = s->connected;
      t->pending_connect = s->pending_connect;
      t->last_errno = s->last_errno;
    }
  h->udp_stats = l->udp.st;
}

/* Takes up, keeps, or lets go of the UDP export, from VPP's configuration:
 * the socket is made when the export is ours to carry, its templates go out
 * when due, and a configuration that is no longer ours closes it. */
void
logd_udp_update (logd_t *l, f64 now)
{
  bpa_log_seg_hdr_t *h = (bpa_log_seg_hdr_t *) l->base;
  bpa_log_seg_udp_cfg_t c;

  if (bpa_log_seg_udp_cfg_read (&h->udp_cfg, &c))
    return;
  if (!logd_udp_takes (&c))
    {
      if (l->udp_on || l->udp.fd >= 0)
	logd_udp_close (&l->udp);
      l->udp_on = 0;
      return;
    }
  if (logd_udp_open (&l->udp, &c))
    {
      l->udp_on = 0;
      return;
    }
  l->udp_on = 1;
  logd_udp_templates (&l->udp, now);
}

/* Gives both streams back: memory to the spill, sockets closed. */
void
logd_release_all (logd_t *l)
{
  u32 i;

  for (i = 0; i < 2; i++)
    if (logd_stream (i)->enabled)
      bpa_log_stream_release (logd_stream (i));
  if (l->udp_on)
    logd_udp_flush (&l->udp, (u32) time (0));
  logd_udp_close (&l->udp);
  l->udp_on = 0;
  l->owned = 0;
}

/* One pass: heartbeat, ownership, configuration, the export ring, the
 * streams, the counters. Returns 1 when there is work left (go round again
 * at once), 0 when idle. */
int
logd_pass (logd_t *l, u64 now_ns)
{
  bpa_log_seg_hdr_t *h = (bpa_log_seg_hdr_t *) l->base;
  int owner, busy = 0;
  u32 i;
  f64 now = (f64) now_ns * 1e-9;

  __atomic_store_n (&h->logger_beat_ns, now_ns, __ATOMIC_RELEASE);
  h->logger_pid = (u32) getpid ();
  for (i = 0; i < 2; i++)
    logd_apply_cfg (l, i);
  owner = __atomic_load_n (&h->tcp_owner, __ATOMIC_ACQUIRE)
	  == BPA_LOG_OWNER_LOGGER;
  if (!owner)
    {
      /* VPP carries the streams: it has not seen our heartbeat yet, or it
       * took them back because we were stalled. Touch nothing it owns. */
      if (l->owned)
	{
	  l->lost_owner++;
	  for (i = 0; i < 2; i++)
	    {
	      bpa_log_stream_t *s = logd_stream (i);

	      /* Its files are VPP's again: what we held is dropped, counted. */
	      s->n_dropped += vec_len (s->outbuf);
	      vec_reset_length (s->outbuf);
	      if (s->fd >= 0)
		close (s->fd);
	      s->fd = -1;
	      s->connected = 0;
	      s->backlog = 0;
	    }
	  /* And what the UDP export held open: dropped, counted. */
	  logd_udp_close (&l->udp);
	  l->udp_on = 0;
	  l->owned = 0;
	}
      h->udp_stats = l->udp.st;
      return 0;
    }
  l->owned = 1;
  logd_udp_update (l, unix_time_now ());
  if (logd_drain (l, 4096) == 4096)
    busy = 1;
  for (i = 0; i < 2; i++)
    {
      bpa_log_stream_t *s = logd_stream (i);

      if (!s->enabled)
	continue;
      bpa_log_stream_service (s, now);
      if (bpa_log_stream_pending (s))
	busy = 1;
    }
  logd_publish_stats (l);
  return busy;
}

#ifndef PRISMA_LOGD_NO_MAIN
static volatile sig_atomic_t logd_stop;

/* not a unit: a signal handler. */
static void
logd_on_term (int sig)
{
  (void) sig;
  logd_stop = 1;
}

/* not a unit: the clock main() passes to logd_pass(), which the test drives
 * with clocks of its own. */
static u64
logd_mono_ns (void)
{
  struct timespec t;

  clock_gettime (CLOCK_MONOTONIC, &t);
  return (u64) t.tv_sec * 1000000000ULL + (u64) t.tv_nsec;
}

/* not a unit: the process's loop and its signals; lab/log-container.sh runs
 * it under traffic, and every function it calls is test_prisma_logd.c's. */
int
main (int argc, char **argv)
{
  const char *seg = getenv ("PRISMA_LOG_SEGMENT");
  const char *dir = getenv ("PRISMA_LOG_SPILL_DIR");
  char path[256];
  logd_t l = { 0 };
  struct sigaction sa = { 0 };

  (void) argc;
  (void) argv;
  snprintf (path, sizeof (path), "%s",
	    seg ? seg : "/dev/shm" BPA_LOG_SEG_NAME);
  snprintf (l.spill_dir, sizeof (l.spill_dir), "%s",
	    dir ? dir : "/var/log/prisma-routing");
  bpa_log_tcp.fd = bpa_log_syslog.fd = -1;
  l.udp.fd = -1;
  sa.sa_handler = logd_on_term;
  sigaction (SIGTERM, &sa, 0);
  sigaction (SIGINT, &sa, 0);
  signal (SIGPIPE, SIG_IGN);
  fprintf (stderr, "prisma-logd: carrying the TCP log streams from %s\n",
	   path);

  while (!logd_stop)
    {
      bpa_log_seg_hdr_t *h;
      int busy;

      if (!l.base && logd_attach (&l, path))
	{
	  sleep (1);
	  continue;
	}
      h = (bpa_log_seg_hdr_t *) l.base;
      busy = logd_pass (&l, logd_mono_ns ());
      /* A dataplane restart makes a new segment: the old one is unlinked and
       * this mapping keeps it alive. Re-attach when the name holds another
       * generation, giving the streams back first. */
      {
	struct stat st;
	int fd = open (path, O_RDONLY);
	u64 gen = 0;

	if (fd >= 0)
	  {
	    if (fstat (fd, &st) == 0 && (u64) st.st_size >= sizeof (*h)
		&& pread (fd, &gen, sizeof (gen),
			  offsetof (bpa_log_seg_hdr_t, generation))
		     == (ssize_t) sizeof (gen)
		&& gen != l.generation)
	      {
		logd_release_all (&l);
		h->logger_beat_ns = 0;
		logd_detach (&l);
	      }
	    close (fd);
	  }
      }
      if (!busy)
	usleep (5000);
      else
	usleep (200);
    }
  if (l.base)
    {
      bpa_log_seg_hdr_t *h = (bpa_log_seg_hdr_t *) l.base;

      if (h->tcp_owner == BPA_LOG_OWNER_LOGGER)
	{
	  /* Everything VPP queued for us becomes records, and the records go
	   * to the spill file, before the heartbeat says we are gone. */
	  while (logd_drain (&l, 65536))
	    ;
	  logd_release_all (&l);
	}
      /* Gone: VPP takes the streams back on its next pass. */
      __atomic_store_n (&h->logger_beat_ns, 0, __ATOMIC_RELEASE);
      logd_publish_stats (&l);
    }
  fprintf (stderr, "prisma-logd: stopped, streams given back to the "
		   "dataplane\n");
  return 0;
}
#endif
