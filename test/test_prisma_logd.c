/*
 * test_prisma_logd.c - the log container's process (#550), every function but
 * main(): a segment laid out here in /dev/shm as VPP lays it out, a syslog
 * collector on 127.0.0.1, events pushed into the export ring the way VPP's
 * bpa_log_xpush() does, and what reaches the collector, the spill file and the
 * segment's counters read back. No VPP, no container.
 */
#define PRISMA_LOGD_NO_MAIN
#include "../src/logger/prisma_logd.c"

#include <poll.h>
#include <sys/wait.h>

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

static void
push (u8 *base, u8 kind, u8 event, u32 priv, const char *ident)
{
  bpa_log_ring_t *x = bpa_log_seg_xring (base);
  bpa_log_xev_t *v =
    bpa_log_ring_reserve (x, BPA_LOG_XRING_SLOTS, BPA_LOG_XEV_BYTES);

  if (!v)
    return;
  memset (v, 0, sizeof (*v));
  v->kind = kind;
  v->wall = 1790000000.25;
  v->ev.event = event;
  v->ev.ip_priv = priv;
  v->ev.ip_pub = htonl (0xcb007100);
  v->ev.port_lo = 1024;
  v->ev.port_hi = 3071;
  if (ident)
    snprintf (v->ev.identity, sizeof (v->ev.identity), "%s", ident);
  bpa_log_ring_publish (x);
}

/* Reads what the collector got within ms milliseconds. */
static int
collect (int c, char *buf, int size, int ms)
{
  int got = 0;
  struct pollfd p = { .fd = c, .events = POLLIN };

  while (got < size - 1 && poll (&p, 1, ms) > 0)
    {
      int n = read (c, buf + got, size - 1 - got);
      if (n <= 0)
	break;
      got += n;
      ms = 50;
    }
  buf[got] = 0;
  return got;
}

int
main (void)
{
  char seg[64], dir[] = "/tmp/prisma-logd-test-XXXXXX", spill[600];
  u64 bytes = bpa_log_seg_bytes (2, 64, sizeof (bpa_log_event_t));
  bpa_log_seg_hdr_t *h;
  u8 *base;
  logd_t l = { 0 };
  int fd, lis, c = -1, i;
  struct sockaddr_in a = { .sin_family = AF_INET };
  socklen_t al = sizeof (a);
  char buf[65536];
  struct stat st;

  bpa_log_tcp.fd = bpa_log_syslog.fd = -1;
  signal (SIGPIPE, SIG_IGN);
  snprintf (seg, sizeof (seg), "/prisma-logd-test-%d", (int) getpid ());
  mkdtemp (dir);
  snprintf (l.spill_dir, sizeof (l.spill_dir), "%s", dir);
  snprintf (spill, sizeof (spill), "%s/syslog-spill.bin", dir);

  fd = shm_open (seg, O_CREAT | O_EXCL | O_RDWR, 0600);
  if (fd < 0 || ftruncate (fd, bytes))
    {
      printf ("cannot make a segment\n");
      return 1;
    }
  base = mmap (0, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
  close (fd);
  h = (bpa_log_seg_hdr_t *) base;

  printf ("=== attaching ===\n");
  {
    char p[128];

    snprintf (p, sizeof (p), "/dev/shm%s", seg);
    CHECK (logd_attach (&l, p) == -1,
	   "a segment with no header yet is refused (VPP still laying it out)");
    bpa_log_seg_init (base, 2, 64, sizeof (bpa_log_event_t), 9);
    h->boot_wall = 1789999000.0;
    h->version = 1;
    CHECK (logd_attach (&l, p) == -1,
	   "a layout version this build does not read is refused");
    h->version = 2;
    CHECK (logd_attach (&l, p) == 0 && l.generation == 9,
	   "VPP's segment is attached, its generation remembered");
    logd_detach (&l);
    CHECK (logd_attach (&l, p) == 0, "and attached again after letting go");
  }

  printf ("=== the configuration, from the segment ===\n");
  lis = socket (AF_INET, SOCK_STREAM, 0);
  a.sin_addr.s_addr = htonl (INADDR_LOOPBACK);
  bind (lis, (struct sockaddr *) &a, sizeof (a));
  listen (lis, 4);
  getsockname (lis, (struct sockaddr *) &a, &al);
  CHECK (logd_apply_cfg (&l, BPA_LOG_STREAM_SYSLOG) == 0,
	 "nothing configured, nothing applied");
  bpa_log_seg_cfg_write (&h->cfg[BPA_LOG_STREAM_SYSLOG], 1, BPA_WIRE_SYSLOG,
			 ntohs (a.sin_port), htonl (INADDR_LOOPBACK), 32473,
			 134, "prisma-bench");
  CHECK (logd_apply_cfg (&l, BPA_LOG_STREAM_SYSLOG) == 1
	   && bpa_log_syslog.enabled && bpa_log_syslog.pen == 32473
	   && bpa_log_syslog.wire == BPA_WIRE_SYSLOG
	   && !strcmp ((char *) bpa_log_syslog.hostname, "prisma-bench")
	   && !strcmp ((char *) bpa_log_syslog.spill_path, spill),
	 "the syslog stream takes VPP's collector, enterprise, hostname, and "
	 "the spill file in the spill directory");
  CHECK (logd_apply_cfg (&l, BPA_LOG_STREAM_SYSLOG) == 0,
	 "an unchanged generation is not applied twice");

  printf ("=== VPP still carries the streams: hands off ===\n");
  push (base, BPA_LOG_XEV_RECORD, BPA_NAT_EVENT_BLOCK_ALLOC, htonl (0x64400005), "alice");
  CHECK (logd_pass (&l, 5000000000ULL) == 0 && h->logger_beat_ns == 5000000000ULL
	   && h->logger_pid == (u32) getpid (),
	 "a pass beats the heartbeat and says who it is");
  CHECK (bpa_log_seg_xring (base)->tail == 0 && bpa_log_syslog.fd < 0,
	 "but while VPP owns the streams nothing is read and nothing connects");

  printf ("=== the streams are ours: records reach the collector ===\n");
  h->tcp_owner = BPA_LOG_OWNER_LOGGER;
  push (base, BPA_LOG_XEV_MARKER, 0, 0, 0);
  push (base, BPA_LOG_XEV_RECORD, BPA_NAT_EVENT_BLOCK_DEALLOC, htonl (0x64400006), "bob");
  for (i = 0; i < 40 && c < 0; i++)
    {
      struct pollfd p = { .fd = lis, .events = POLLIN };

      logd_pass (&l, 5100000000ULL + i * 1000000ULL);
      if (poll (&p, 1, 10) > 0)
	c = accept (lis, 0, 0);
    }
  for (i = 0; i < 20; i++)
    logd_pass (&l, 5200000000ULL + i * 1000000ULL);
  collect (c, buf, sizeof (buf), 500);
  CHECK (c >= 0, "the stream connects to the collector");
  CHECK (strstr (buf, "BOOT-MARKER") && strstr (buf, "BLOCK-ALLOC")
	   && strstr (buf, "BLOCK-RELEASE"),
	 "the marker and both block events arrive as syslog lines: [%.200s]",
	 buf);
  CHECK (strstr (buf, "alice") && strstr (buf, "bob")
	   && strstr (buf, "prisma-bench") && strstr (buf, "@32473"),
	 "each line names its subscriber, the box's hostname and the "
	 "operator's enterprise");
  CHECK (strstr (buf, "BOOT-MARKER") < strstr (buf, "BLOCK-RELEASE"),
	 "in the order VPP queued them: the marker before the event after it");
  CHECK (bpa_log_seg_xring (base)->tail == 3,
	 "the export ring is consumed: %u", bpa_log_seg_xring (base)->tail);
  CHECK (h->stats[BPA_LOG_STREAM_SYSLOG].n_records == 3
	   && h->stats[BPA_LOG_STREAM_SYSLOG].connected == 1
	   && h->stats[BPA_LOG_STREAM_SYSLOG].n_sent > 0,
	 "and the counters VPP's screen reads are published: %llu records",
	 (unsigned long long) h->stats[BPA_LOG_STREAM_SYSLOG].n_records);
  CHECK (h->stats[BPA_LOG_STREAM_IPFIX].n_records == 0,
	 "a stream not configured writes nothing");

  printf ("=== VPP takes the streams back ===\n");
  h->tcp_owner = BPA_LOG_OWNER_VPP;
  push (base, BPA_LOG_XEV_RECORD, BPA_NAT_EVENT_BLOCK_ALLOC, htonl (0x64400007), "carol");
  logd_pass (&l, 6000000000ULL);
  CHECK (!l.owned && l.lost_owner == 1 && bpa_log_syslog.fd < 0,
	 "the container lets go of its connection at once");
  CHECK (stat (spill, &st) != 0,
	 "and writes nothing to the spill file it no longer owns");

  printf ("=== stopping with a collector gone: everything to the spill ===\n");
  close (c);
  close (lis);
  h->tcp_owner = BPA_LOG_OWNER_LOGGER;
  for (i = 0; i < 5; i++)
    push (base, BPA_LOG_XEV_RECORD, BPA_NAT_EVENT_BLOCK_ALLOC, htonl (0x64400010 + i), "dave");
  logd_drain (&l, 65536);
  /* logd_release_all() is bpa_log_stream_release() on each enabled stream. */
  bpa_log_stream_release (&bpa_log_syslog);
  logd_release_all (&l);
  CHECK (stat (spill, &st) == 0 && st.st_size > 0,
	 "what the container held is in the spill file: %lld bytes",
	 (long long) (stat (spill, &st) == 0 ? st.st_size : 0));
  CHECK (vec_len (bpa_log_syslog.outbuf) == 0 && !l.owned,
	 "and nothing is left in its memory");
  {
    int sf = open (spill, O_RDONLY);
    int n = read (sf, buf, sizeof (buf) - 1);

    buf[n > 0 ? n : 0] = 0;
    close (sf);
    CHECK (strstr (buf, "dave") != 0,
	   "the spilled lines are the syslog lines VPP's replay reads");
  }

  printf ("=== a disabled stream lets go ===\n");
  bpa_log_seg_cfg_write (&h->cfg[BPA_LOG_STREAM_SYSLOG], 0, BPA_WIRE_SYSLOG,
			 0, 0, 0, 0, 0);
  CHECK (logd_apply_cfg (&l, BPA_LOG_STREAM_SYSLOG) == 1
	   && !bpa_log_syslog.enabled,
	 "a configuration that turns the stream off turns it off");

  munmap (base, bytes);
  shm_unlink (seg);
  unlink (spill);
  {
    char off[700];
    snprintf (off, sizeof (off), "%s.off", spill);
    unlink (off);
  }
  rmdir (dir);
  printf ("\n%u checks, %d failures\n", checks, failures);
  if (failures)
    {
      printf ("%d FAILURES\n", failures);
      return 1;
    }
  printf ("ALL TESTS PASSED\n");
  return 0;
}
