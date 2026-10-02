#define _GNU_SOURCE
/*
 * test_bpa_log_tcp.c - the IPFIX-over-TCP exporter, without a collector.
 *
 * This is the transport the compliance log rides on, and what it promises is
 * ORDER and NO LOSS: an allocation reaches the collector before its own
 * release, and a record the socket would not take is on disk rather than
 * gone. Both promises are kept by arithmetic on one buffer - where bytes are
 * inserted, how many are dropped, what goes to the file - and every way of
 * getting that arithmetic wrong is silent. #219 is the shape of it: 82917
 * bytes spilled at a stop, 82917 replayed on the next boot, and the collector
 * decoded ONE record out of them, because the file had begun mid-message and
 * everything behind it was misframed. The exporter's own counters said the
 * bytes were saved and replayed, and they were.
 *
 * So the assertions here read the bytes. A record is decoded field by field
 * out of the buffer rather than compared against a length, and the spill file
 * is read back and compared with what went into it.
 *
 * NO NETWORK. The one check that needs a socket listens on 127.0.0.1 and
 * connects to itself. Nothing here resolves a name, leaves the loopback
 * interface, or needs root.
 */

#include <arpa/inet.h>
#include <fcntl.h>
#include <pthread.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

#include "bpa_log_shim.h"
/* The flood test below drives the ceiling without allocating 64 MB. */
#define BPA_TCP_BUSY_BYTES (4 << 20)
/*
 * INCLUDED rather than linked: nine of the fifteen functions driven here are
 * static, so nothing compiled beside the file could call one. bpa_log_tcp.c
 * carries the BPA_STANDALONE guards that leave bpa_log_tcp_enable() - the one
 * function that formats a path with VPP's format() and calls back into
 * bpa.c - out of this build.
 */
#include "../src/plugins/bpa/bpa_log_tcp.c"

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

static char spill_dir[64];
static char spill_file[128];

/* Big-endian readers, so a field is asserted as it goes on the wire. */
static u16
rd16 (u32 off)
{
  const u8 *p = bpa_log_tcp.outbuf + off;
  return (u16) ((p[0] << 8) | p[1]);
}

static u32
rd32 (u32 off)
{
  const u8 *p = bpa_log_tcp.outbuf + off;
  return ((u32) p[0] << 24) | ((u32) p[1] << 16) | ((u32) p[2] << 8) | p[3];
}

static u64
rd64 (u32 off)
{
  return ((u64) rd32 (off) << 32) | rd32 (off + 4);
}

static char offp_g[160];
static int drain_fd;
static volatile long drained;

/* A collector that reads as fast as it can, for the drain check. */
static void *
drain_reader (void *arg)
{
  char b[1 << 16];
  ssize_t n;

  (void) arg;
  while ((n = read (drain_fd, b, sizeof (b))) > 0)
    drained += n;
  return 0;
}
static u32 n_marker_bytes;

/* A fresh exporter that keeps the spill file and its progress: a restart. */
static void
reset_keep_file (void)
{
  vec_free (bpa_log_tcp.outbuf);
  memset (&bpa_log_tcp, 0, sizeof (bpa_log_tcp));
  bpa_log_tcp.fd = -1;
  bpa_log_tcp.enabled = 1;
  bpa_log_tcp.spill_path = (u8 *) spill_file;
}

/* A fresh exporter: no socket, an empty buffer, a spill path of its own. */
static void
reset (int enabled)
{
  vec_free (bpa_log_tcp.outbuf);
  memset (&bpa_log_tcp, 0, sizeof (bpa_log_tcp));
  bpa_log_tcp.fd = -1;
  bpa_log_tcp.enabled = (u8) enabled;
  bpa_log_tcp.spill_path = (u8 *) spill_file;
  unlink (spill_file);
  snprintf (offp_g, sizeof (offp_g), "%s.off", spill_file);
  unlink (offp_g);
}

static long
file_size (const char *path)
{
  struct stat st;

  if (stat (path, &st))
    return -1;
  return (long) st.st_size;
}

static int
file_equals (const char *path, const u8 *want, u32 n)
{
  u8 buf[4096];
  int fd = open (path, O_RDONLY);
  ssize_t got;

  if (fd < 0)
    return 0;
  got = read (fd, buf, sizeof (buf));
  close (fd);
  return got == (ssize_t) n && memcmp (buf, want, n) == 0;
}

/* One IPFIX message of `mlen` bytes, appended by hand, so that the framing
 * functions can be driven on a buffer whose contents the test chose. */
static void
fake_message (u16 mlen)
{
  u8 *p;
  u32 i;

  vec_add2 (bpa_log_tcp.outbuf, p, mlen);
  memset (p, 0, mlen);
  p[0] = 0;
  p[1] = 10;
  p[2] = (u8) (mlen >> 8);
  p[3] = (u8) mlen;
  for (i = 16; i < mlen; i++)
    p[i] = (u8) i;
}

int
main (void)
{
  printf ("=== IPFIX over TCP: the buffer, the spill and the socket ===\n");

  strcpy (spill_dir, "/tmp/bpa-log-tcp-XXXXXX");
  if (mkdtemp (spill_dir) == 0)
    {
      printf ("could not make a temporary directory\n");
      return bpa_test_verdict (0, 1);
    }
  snprintf (spill_file, sizeof (spill_file), "%s/spill.bin", spill_dir);

  printf ("\n--- putting bytes in ---\n");
  {
    u8 *b;

    reset (1);
    b = bpa_log_tcp.outbuf;
    bpa_tcp_put (&b, "abc", 3);
    bpa_tcp_put (&b, "de", 2);
    bpa_log_tcp.outbuf = b;
    CHECK (vec_len (bpa_log_tcp.outbuf) == 5
	     && memcmp (bpa_log_tcp.outbuf, "abcde", 5) == 0,
	   "bpa_tcp_put appends, it does not overwrite: %u bytes \"%.5s\"",
	   vec_len (bpa_log_tcp.outbuf), (const char *) bpa_log_tcp.outbuf);

    reset (1);
    b = bpa_log_tcp.outbuf;
    bpa_tcp_put_u16 (&b, 0x1234);
    bpa_log_tcp.outbuf = b;
    CHECK (vec_len (bpa_log_tcp.outbuf) == 2 && bpa_log_tcp.outbuf[0] == 0x12
	     && bpa_log_tcp.outbuf[1] == 0x34,
	   "bpa_tcp_put_u16 writes network order: %02x %02x",
	   bpa_log_tcp.outbuf[0], bpa_log_tcp.outbuf[1]);

    reset (1);
    b = bpa_log_tcp.outbuf;
    bpa_tcp_put_u32 (&b, 0x01020304);
    bpa_log_tcp.outbuf = b;
    CHECK (vec_len (bpa_log_tcp.outbuf) == 4 && bpa_log_tcp.outbuf[0] == 1
	     && bpa_log_tcp.outbuf[1] == 2 && bpa_log_tcp.outbuf[2] == 3
	     && bpa_log_tcp.outbuf[3] == 4,
	   "bpa_tcp_put_u32 writes network order: %02x %02x %02x %02x",
	   bpa_log_tcp.outbuf[0], bpa_log_tcp.outbuf[1],
	   bpa_log_tcp.outbuf[2], bpa_log_tcp.outbuf[3]);
  }

  printf ("\n--- the message header (RFC 7011) ---\n");
  {
    u8 *b;
    u32 at;
    u64 now = (u64) unix_time_now ();

    /*
     * Started behind an existing message, because the offset it returns is
     * the whole of what msg_end later needs: a header written at zero and a
     * header written at 45 have to be closed the same way.
     */
    reset (1);
    bpa_log_tcp.seq = 7;
    fake_message (45);
    b = bpa_log_tcp.outbuf;
    at = bpa_tcp_msg_start (&bpa_log_tcp, &b);
    bpa_log_tcp.outbuf = b;
    CHECK (at == 45 && vec_len (bpa_log_tcp.outbuf) == 61 && rd16 (45) == 10
	     && rd16 (47) == 0 && rd32 (49) >= (u32) now - 5
	     && rd32 (49) <= (u32) now + 5 && rd32 (53) == 7
	     && rd32 (57) == BPA_TCP_DOMAIN,
	   "bpa_tcp_msg_start writes version, a placeholder length, the wall "
	   "clock, the sequence and the domain: at=%u len=%u ver=%u len0=%u "
	   "time=%u seq=%u dom=%u",
	   at, vec_len (bpa_log_tcp.outbuf), rd16 (45), rd16 (47), rd32 (49),
	   rd32 (53), rd32 (57));

    /* Ten more bytes, then closed: the length is the message's own, not the
     * buffer's. */
    b = bpa_log_tcp.outbuf;
    bpa_tcp_put (&b, "0123456789", 10);
    bpa_tcp_msg_end (&b, at);
    bpa_log_tcp.outbuf = b;
    CHECK (rd16 (47) == 26 && rd16 (2) == 45,
	   "bpa_tcp_msg_end fills in the length of ITS message and leaves the "
	   "one in front alone: %u, and the first is still %u",
	   rd16 (47), rd16 (2));
  }

  printf ("\n--- a block event on the wire ---\n");
  {
    u32 while_off;

    /* With the transport off there is nothing to buffer: a record written
     * anyway would go out on the next connection as an event nobody asked
     * for. */
    reset (0);
    bpa_log_tcp_record (1700000000.5, BPA_NAT_EVENT_BLOCK_DEALLOC, 1, 2, 3, 4,
			5);
    while_off = vec_len (bpa_log_tcp.outbuf);

    reset (1);
    bpa_log_tcp_record (1700000000.5, BPA_NAT_EVENT_BLOCK_ALLOC, 0x0a000001,
			0xc6336401, 2048, 3071, 9);
    /* 53 and not 45 since #21: the eighth element, userName, is variable
     * length - one octet of length and the value, and with no identity table
     * the value is the unknown marker (7 bytes). The set length and the
     * message length move with it, and the TEMPLATE ID moved too, so a
     * collector holding the old one never matches these bytes. */
    CHECK (while_off == 0 && vec_len (bpa_log_tcp.outbuf) == 53 && rd16 (2) == 53
	     && rd16 (16) == BPA_TCP_TEMPLATE_ID && rd16 (18) == 37
	     && rd64 (20) == 1700000000500ull && bpa_log_tcp.outbuf[28] == 16
	     && rd32 (29) == 0x0a000001 && rd32 (33) == 0xc6336401
	     && rd16 (37) == 2048 && rd16 (39) == 3071 && rd32 (41) == 9
	     && bpa_log_tcp.outbuf[45] == 7
	     && memcmp (bpa_log_tcp.outbuf + 46, "unknown", 7) == 0
	     && bpa_log_tcp.seq == 1 && bpa_log_tcp.n_records == 1,
	   "bpa_log_tcp_record writes one 53-byte message carrying the seven "
	   "RFC 8158 fields and the subscriber, and nothing at all while the "
	   "transport is off: "
	   "off=%u len=%u set=%u/%u ms=%llu ev=%u priv=%08x pub=%08x %u-%u "
	   "pool=%u",
	   while_off, vec_len (bpa_log_tcp.outbuf), rd16 (16), rd16 (18),
	   (unsigned long long) rd64 (20), bpa_log_tcp.outbuf[28], rd32 (29),
	   rd32 (33), rd16 (37), rd16 (39), rd32 (41));
  }

  printf ("\n--- the boot marker ---\n");
  {
    reset (1);
    bpa_log_tcp_marker (1700000000.5, 1699999000.25);
    CHECK (vec_len (bpa_log_tcp.outbuf) == 36 && rd16 (2) == 36
	     && rd16 (16) == BPA_TCP_MARKER_TEMPLATE_ID && rd16 (18) == 20
	     && rd64 (20) == 1700000000500ull
	     && rd64 (28) == 1699999000250ull && bpa_log_tcp.seq == 1,
	   "bpa_log_tcp_marker carries when it was written and when this "
	   "incarnation started: len=%u tid=%u now=%llu init=%llu",
	   vec_len (bpa_log_tcp.outbuf), rd16 (16),
	   (unsigned long long) rd64 (20), (unsigned long long) rd64 (28));
  }

  printf ("\n--- dropping the connection ---\n");
  {
    int pipefd[2];

    reset (1);
    if (pipe (pipefd))
      {
	printf ("  could not make a pipe\n");
	return bpa_test_verdict (checks, failures + 1);
      }
    close (pipefd[0]);
    bpa_log_tcp.fd = pipefd[1];
    bpa_log_tcp.connected = 1;
    bpa_log_tcp.pending_connect = 0;
    bpa_log_tcp.head_sent = 17;

    bpa_tcp_disconnect (&bpa_log_tcp);
    CHECK (bpa_log_tcp.fd == -1 && bpa_log_tcp.connected == 0
	     && bpa_log_tcp.head_sent == 0
	     && fcntl (pipefd[1], F_GETFD) == -1,
	   "bpa_tcp_disconnect closes the descriptor and forgets the partly "
	   "sent message: fd=%d connected=%u head_sent=%u still open=%d",
	   bpa_log_tcp.fd, bpa_log_tcp.connected, bpa_log_tcp.head_sent,
	   fcntl (pipefd[1], F_GETFD) != -1);
  }

  printf ("\n--- reaching the collector ---\n");
  {
    int lfd;
    struct sockaddr_in sa;
    socklen_t sl = sizeof (sa);
    u64 connects_after_gate;
    f64 attempt_after_gate;

    lfd = socket (AF_INET, SOCK_STREAM, 0);
    memset (&sa, 0, sizeof (sa));
    sa.sin_family = AF_INET;
    sa.sin_addr.s_addr = htonl (INADDR_LOOPBACK);
    sa.sin_port = 0;
    if (lfd < 0 || bind (lfd, (struct sockaddr *) &sa, sizeof (sa))
	|| listen (lfd, 1) || getsockname (lfd, (struct sockaddr *) &sa, &sl))
      {
	printf ("  could not listen on the loopback interface\n");
	return bpa_test_verdict (checks, failures + 1);
      }

    reset (1);
    bpa_log_tcp.collector.as_u32 = sa.sin_addr.s_addr;
    bpa_log_tcp.port = ntohs (sa.sin_port);
    bpa_log_tcp.next_attempt = 1000.0;

    /* Before the retry interval is up, nothing is attempted: a collector that
     * is down must not cost a socket per drain. */
    bpa_tcp_try_connect (&bpa_log_tcp, 999.0);
    connects_after_gate = bpa_log_tcp.n_connects;
    attempt_after_gate = bpa_log_tcp.next_attempt;

    bpa_tcp_try_connect (&bpa_log_tcp, 1000.0);
    CHECK (connects_after_gate == 0 && attempt_after_gate == 1000.0
	     && bpa_log_tcp.fd >= 0 && bpa_log_tcp.n_connects == 1
	     && bpa_log_tcp.need_template == 1
	     && bpa_log_tcp.next_attempt == 1000.0 + BPA_TCP_RETRY_SECS,
	   "bpa_tcp_try_connect waits out the retry interval, then connects "
	   "and arms the template: gated=%llu/%.1f fd=%d connects=%llu "
	   "template=%u next=%.1f",
	   (unsigned long long) connects_after_gate, attempt_after_gate,
	   bpa_log_tcp.fd, (unsigned long long) bpa_log_tcp.n_connects,
	   bpa_log_tcp.need_template, bpa_log_tcp.next_attempt);

    bpa_tcp_disconnect (&bpa_log_tcp);
    close (lfd);
  }

  printf ("\n--- to the spill file ---\n");
  {
    int pipefd[2];
    u8 want[81];
    u32 n;

    reset (1);
    fake_message (45);
    fake_message (36);
    n = vec_len (bpa_log_tcp.outbuf);
    memcpy (want, bpa_log_tcp.outbuf, n);

    /* A connection that has taken the front of a message and not the rest
     * cannot be completed once that rest is on disk, so the spill drops it. */
    if (pipe (pipefd))
      {
	printf ("  could not make a pipe\n");
	return bpa_test_verdict (checks, failures + 1);
      }
    close (pipefd[0]);
    bpa_log_tcp.fd = pipefd[1];
    bpa_log_tcp.connected = 1;
    bpa_log_tcp.head_sent = 10;

    bpa_tcp_spill_now (&bpa_log_tcp, 1);
    CHECK (file_equals (spill_file, want, n) && bpa_log_tcp.n_spilled == n
	     && vec_len (bpa_log_tcp.outbuf) == 0 && bpa_log_tcp.fd == -1,
	   "bpa_tcp_spill_now at the exit writes the buffer whole, empties it, "
	   "counts it and drops a connection holding half a message: file=%ld "
	   "spilled=%llu left=%u fd=%d",
	   file_size (spill_file), (unsigned long long) bpa_log_tcp.n_spilled,
	   vec_len (bpa_log_tcp.outbuf), bpa_log_tcp.fd);

    /* The threshold. Under it the bytes stay in memory, where the socket can
     * still take them in order; at it they go to disk rather than growing
     * without bound. */
    reset (1);
    fake_message (45);
    bpa_tcp_spill (&bpa_log_tcp);
    {
      u64 spilled_small = bpa_log_tcp.n_spilled;
      u32 kept_small = vec_len (bpa_log_tcp.outbuf);
      u8 *p;

      vec_add2 (bpa_log_tcp.outbuf, p, BPA_TCP_SPILL_BYTES);
      memset (p, 0, BPA_TCP_SPILL_BYTES);
      bpa_tcp_spill (&bpa_log_tcp);
      CHECK (spilled_small == 0 && kept_small == 45
	       && bpa_log_tcp.n_spilled == 45 + BPA_TCP_SPILL_BYTES
	       && vec_len (bpa_log_tcp.outbuf) == 0,
	     "bpa_tcp_spill writes only once the buffer is over the "
	     "threshold: small spilled=%llu kept=%u, large spilled=%llu "
	     "kept=%u",
	     (unsigned long long) spilled_small, kept_small,
	     (unsigned long long) bpa_log_tcp.n_spilled,
	     vec_len (bpa_log_tcp.outbuf));
    }
  }

  printf ("\n--- and back out of it, in order, a chunk at a time (#547) ---\n");
  {
    int fd;
    u8 older[45 + 36], frag[20 + 45], *big;
    char offp[160];
    u32 i, nbig = (3u << 20) / 45, first;

    /* Two whole messages in the file, then a newer one written since: the
     * newer one goes BEHIND them, in the file, and the chunk comes back out
     * in that order. */
    reset (1);
    fake_message (45);
    fake_message (36);
    memcpy (older, bpa_log_tcp.outbuf, sizeof (older));
    vec_reset_length (bpa_log_tcp.outbuf);
    fd = open (spill_file, O_WRONLY | O_CREAT | O_TRUNC, 0640);
    if (fd < 0 || write (fd, older, sizeof (older)) != (ssize_t) sizeof (older))
      {
	printf ("  could not write the spill file\n");
	return bpa_test_verdict (checks, failures + 1);
      }
    close (fd);
    fake_message (20);
    bpa_tcp_backlog_open (&bpa_log_tcp);
    bpa_tcp_backlog_tail (&bpa_log_tcp);
    bpa_tcp_replay_chunk (&bpa_log_tcp);
    CHECK (vec_len (bpa_log_tcp.outbuf) == 45 + 36 + 20
	     && memcmp (bpa_log_tcp.outbuf, older, sizeof (older)) == 0
	     && bpa_log_tcp.outbuf[45 + 36 + 3] == 20
	     && bpa_log_tcp.replay_len == 101 && bpa_log_tcp.n_replayed == 101
	     && bpa_log_tcp.n_spilled == 20 && file_size (spill_file) == 101,
	   "the older bytes come out IN FRONT, the newer behind them through the "
	   "file: len=%u replay=%u replayed=%llu spilled=%llu file=%ld",
	   vec_len (bpa_log_tcp.outbuf), bpa_log_tcp.replay_len,
	   (unsigned long long) bpa_log_tcp.n_replayed,
	   (unsigned long long) bpa_log_tcp.n_spilled, file_size (spill_file));
    /* Sent whole: the file and its progress go. */
    bpa_log_tcp.head_sent = vec_len (bpa_log_tcp.outbuf);
    bpa_tcp_drop_sent (&bpa_log_tcp);
    bpa_tcp_replay_chunk (&bpa_log_tcp);
    snprintf (offp, sizeof (offp), "%s.off", spill_file);
    CHECK (vec_len (bpa_log_tcp.outbuf) == 0 && bpa_log_tcp.replay_len == 0
	     && !bpa_log_tcp.backlog && file_size (spill_file) == -1
	     && file_size (offp) == -1,
	   "a backlog sent whole removes the file and its progress: len=%u "
	   "backlog=%u file=%ld off=%ld", vec_len (bpa_log_tcp.outbuf),
	   bpa_log_tcp.backlog, file_size (spill_file), file_size (offp));

    /* A file that begins mid-message, as one written by a release with the
     * #492 cut does - the tail of a message, then a whole one. */
    reset (1);
    fake_message (45);
    memset (frag, 0x41, 20);
    memcpy (frag + 20, bpa_log_tcp.outbuf, 45);
    vec_reset_length (bpa_log_tcp.outbuf);
    fd = open (spill_file, O_WRONLY | O_CREAT | O_TRUNC, 0640);
    if (fd < 0 || write (fd, frag, sizeof (frag)) != (ssize_t) sizeof (frag))
      {
	printf ("  could not write the spill file\n");
	return bpa_test_verdict (checks, failures + 1);
      }
    close (fd);
    bpa_tcp_backlog_open (&bpa_log_tcp);
    bpa_tcp_replay_chunk (&bpa_log_tcp);
    CHECK (vec_len (bpa_log_tcp.outbuf) == 45 && bpa_log_tcp.outbuf[1] == 10
	     && bpa_log_tcp.n_resynced == 20 && bpa_log_tcp.n_replayed == 45,
	   "a spill that begins mid-message is replayed from its first whole "
	   "message, the fragment counted: len=%u resynced=%llu replayed=%llu",
	   vec_len (bpa_log_tcp.outbuf),
	   (unsigned long long) bpa_log_tcp.n_resynced,
	   (unsigned long long) bpa_log_tcp.n_replayed);

    /* Three megabytes of backlog: never more than a chunk in memory, cut on
     * a message; given back on a disconnect; and its progress survives a
     * restart. The bench's file was 573 MB, read whole, and VPP aborted. */
    reset (1);
    for (i = 0; i < nbig; i++)
      fake_message (45);
    big = bpa_log_tcp.outbuf;
    bpa_log_tcp.outbuf = 0;
    fd = open (spill_file, O_WRONLY | O_CREAT | O_TRUNC, 0640);
    if (fd < 0 || write (fd, big, nbig * 45) != (ssize_t) (nbig * 45))
      {
	printf ("  could not write the spill file\n");
	return bpa_test_verdict (checks, failures + 1);
      }
    close (fd);
    vec_free (big);
    bpa_tcp_backlog_open (&bpa_log_tcp);
    bpa_tcp_replay_chunk (&bpa_log_tcp);
    first = bpa_log_tcp.replay_len;
    CHECK (first <= BPA_TCP_REPLAY_CHUNK && first > BPA_TCP_REPLAY_CHUNK - 45
	     && first % 45 == 0 && vec_len (bpa_log_tcp.outbuf) == first
	     && bpa_log_tcp.spill_off == first,
	   "a 3 MB backlog is read %u bytes at a time, on a message boundary "
	   "(chunk %u): in memory %u, offset %llu", first, BPA_TCP_REPLAY_CHUNK,
	   vec_len (bpa_log_tcp.outbuf),
	   (unsigned long long) bpa_log_tcp.spill_off);
    /* Half of it sent, then the connection drops: the rest is given back. */
    bpa_log_tcp.head_sent = 45 * 100;
    bpa_tcp_drop_sent (&bpa_log_tcp);
    fake_message (36); /* a newer record, written meanwhile */
    bpa_tcp_disconnect (&bpa_log_tcp);
    CHECK (bpa_log_tcp.spill_off == 45 * 100 && bpa_log_tcp.replay_len == 0
	     && vec_len (bpa_log_tcp.outbuf) == 0
	     && file_size (spill_file) == (long) (nbig * 45 + 36),
	   "a disconnect gives the unsent chunk back to the file and puts the "
	   "newer record behind it: offset %llu, in memory %u, file %ld",
	   (unsigned long long) bpa_log_tcp.spill_off,
	   vec_len (bpa_log_tcp.outbuf), file_size (spill_file));
    bpa_tcp_backlog_tail (&bpa_log_tcp);
    bpa_tcp_replay_chunk (&bpa_log_tcp); /* saves 45 * 100 as taken */
    CHECK (file_size (spill_file) == (long) (nbig * 45 + 36)
	     && bpa_log_tcp.outbuf[3] == 45 && bpa_log_tcp.spill_off > 45 * 100,
	   "the newer record went to the end of the file, and the next chunk "
	   "starts where the collector stopped: file %ld, offset %llu",
	   file_size (spill_file), (unsigned long long) bpa_log_tcp.spill_off);
    reset_keep_file ();
    bpa_tcp_backlog_open (&bpa_log_tcp);
    CHECK (bpa_log_tcp.backlog && bpa_log_tcp.spill_off == 45 * 100,
	   "a restart reads the backlog from where it was taken, not from the "
	   "start: offset %llu", (unsigned long long) bpa_log_tcp.spill_off);
    /* The exit: the chunk in flight given back, and what was taken saved. */
    bpa_tcp_replay_chunk (&bpa_log_tcp);
    bpa_log_tcp.head_sent = 45 * 10;
    bpa_tcp_drop_sent (&bpa_log_tcp);
    bpa_log_stream_flush_exit (&bpa_log_tcp);
    reset_keep_file ();
    bpa_tcp_backlog_open (&bpa_log_tcp);
    CHECK (bpa_log_tcp.spill_off == 45 * 110,
	   "the exit saves what the collector took of the chunk in flight: "
	   "offset %llu, not %u", (unsigned long long) bpa_log_tcp.spill_off,
	   45 * 110);
    /* A spill made while disconnected joins that backlog, progress kept. */
    fake_message (36);
    bpa_log_tcp.backlog = 0;
    bpa_tcp_spill_now (&bpa_log_tcp, 0);
    CHECK (bpa_log_tcp.backlog && bpa_log_tcp.spill_off == 45 * 110
	     && file_size (spill_file) == (long) (nbig * 45 + 36 + 36),
	   "a spill joins the backlog already on disk without losing its "
	   "progress: offset %llu, file %ld",
	   (unsigned long long) bpa_log_tcp.spill_off, file_size (spill_file));
    unlink (offp);
    unlink (spill_file);
  }

  printf ("\n--- the backlog's own bookkeeping (#547) ---\n");
  {
    char p[160];
    u8 two[45 + 36];

    reset (1);
    bpa_tcp_off_path (&bpa_log_tcp, p, sizeof (p));
    CHECK (strlen (p) == strlen (spill_file) + 4
	     && strcmp (p + strlen (spill_file), ".off") == 0,
	   "bpa_tcp_off_path is the spill's name and .off: %s", p);
    bpa_tcp_save_off (&bpa_log_tcp, 123456789ULL);
    CHECK (bpa_tcp_load_off (&bpa_log_tcp) == 123456789ULL
	     && file_size (p) == 8,
	   "bpa_tcp_save_off and bpa_tcp_load_off round-trip the progress in "
	   "8 bytes: %llu", (unsigned long long) bpa_tcp_load_off (&bpa_log_tcp));
    fake_message (45);
    fake_message (36);
    memcpy (two, bpa_log_tcp.outbuf, sizeof (two));
    bpa_tcp_append (&bpa_log_tcp, two, sizeof (two));
    CHECK (file_equals (spill_file, two, sizeof (two))
	     && bpa_log_tcp.n_spilled == sizeof (two) && bpa_log_tcp.backlog
	     && bpa_tcp_load_off (&bpa_log_tcp) == 0,
	   "bpa_tcp_append writes, counts and opens a backlog, dropping a "
	   "progress file no backlog owns: spilled=%llu backlog=%u",
	   (unsigned long long) bpa_log_tcp.n_spilled, bpa_log_tcp.backlog);
    bpa_log_tcp.front_extra = 30;
    bpa_log_tcp.replay_len = 81;
    bpa_tcp_front_dropped (&bpa_log_tcp, 45);
    CHECK (bpa_log_tcp.front_extra == 0 && bpa_log_tcp.replay_len == 66,
	   "bpa_tcp_front_dropped takes the template first, then the chunk: "
	   "extra=%u replay=%u", bpa_log_tcp.front_extra, bpa_log_tcp.replay_len);
    bpa_tcp_save_off (&bpa_log_tcp, 7);
    bpa_tcp_backlog_done (&bpa_log_tcp);
    CHECK (!bpa_log_tcp.backlog && file_size (spill_file) == -1
	     && file_size (p) == -1 && bpa_log_tcp.spill_off == 0,
	   "bpa_tcp_backlog_done removes the file and its progress: file=%ld "
	   "off=%ld", file_size (spill_file), file_size (p));
  }

  printf ("\n--- a collector that never reads, under a flood of events (#547) "
	  "---\n");
  {
    int sv[2], one = 4096;
    u32 i, peak = 0, events = 400000;

    reset (1);
    if (socketpair (AF_UNIX, SOCK_STREAM, 0, sv))
      {
	printf ("  no socketpair\n");
	return bpa_test_verdict (checks, failures + 1);
      }
    setsockopt (sv[0], SOL_SOCKET, SO_SNDBUF, &one, sizeof (one));
    fcntl (sv[0], F_SETFL, O_NONBLOCK);
    bpa_log_tcp.fd = sv[0];
    bpa_log_tcp.connected = 1;
    bpa_log_tcp.last_progress = 0;
    for (i = 0; i < events; i++)
      {
	bpa_log_tcp_record (1700000000.5, 12, 0x64400000 + (i & 1023),
			    0xcb007100, 0, 0, 1);
	if (vec_len (bpa_log_tcp.outbuf) > peak)
	  peak = vec_len (bpa_log_tcp.outbuf);
	if (i % 20000 == 0)
	  bpa_log_stream_service (&bpa_log_tcp, 100.0 + i / 20000);
      }
    CHECK (peak <= BPA_TCP_BUSY_BYTES + 256 && file_size (spill_file) > 0
	     && bpa_log_tcp.n_dropped == 0,
	   "%u events into a collector that reads nothing: the buffer peaked at "
	   "%u bytes (ceiling %u), the rest on disk (%ld), nothing dropped",
	   events, peak, BPA_TCP_BUSY_BYTES, file_size (spill_file));
    close (sv[1]);
    if (bpa_log_tcp.fd >= 0)
      bpa_tcp_disconnect (&bpa_log_tcp);
    vec_reset_length (bpa_log_tcp.outbuf);
    unlink (spill_file);
    snprintf (offp_g, sizeof (offp_g), "%s.off", spill_file);
    unlink (offp_g);
  }

  printf ("\n--- a slow collector keeps its connection (#547) ---\n");
  {
    u32 kept_busy, kept_stalled, n;
    int pipefd[2];

    /* Half of the first message on the wire, 1 MB behind it. A collector
     * still taking bytes keeps all of it in memory; one that took nothing for
     * BPA_TCP_STALL_SECS has everything the socket had not started moved to
     * the file - the message on the wire stays, and so does the connection.
     * Spilling at the first EAGAIN and dropping the connection mid-message
     * capped the stream at ~500 KB a connection on the #475 bench. */
    reset (1);
    if (pipe (pipefd))
      {
	printf ("  could not make a pipe\n");
	return bpa_test_verdict (checks, failures + 1);
      }
    bpa_log_tcp.fd = pipefd[1];
    bpa_log_tcp.connected = 1;
    for (n = 0; vec_len (bpa_log_tcp.outbuf) < BPA_TCP_SPILL_BYTES + 45; n++)
      fake_message (45);
    bpa_log_tcp.head_sent = 20;
    bpa_log_tcp.last_progress = 100.0;
    bpa_tcp_spill_stalled (&bpa_log_tcp, 100.0 + BPA_TCP_STALL_SECS - 1);
    kept_busy = vec_len (bpa_log_tcp.outbuf);
    bpa_tcp_spill_stalled (&bpa_log_tcp, 100.0 + BPA_TCP_STALL_SECS + 1);
    kept_stalled = vec_len (bpa_log_tcp.outbuf);
    CHECK (kept_busy == n * 45 && kept_stalled == 45
	     && file_size (spill_file) == (long) ((n - 1) * 45)
	     && bpa_log_tcp.n_stalls == 1 && bpa_log_tcp.fd == pipefd[1]
	     && bpa_log_tcp.backlog && bpa_log_tcp.front_extra == 45
	     && bpa_log_tcp.head_sent == 20,
	   "a collector still taking bytes keeps the buffer, a stalled one has "
	   "all but the message on the wire moved to the file, connection kept: "
	   "busy=%u stalled=%u file=%ld stalls=%llu fd=%d front=%u",
	   kept_busy, kept_stalled, file_size (spill_file),
	   (unsigned long long) bpa_log_tcp.n_stalls, bpa_log_tcp.fd,
	   bpa_log_tcp.front_extra);
    /* A record written meanwhile: bpa_tcp_offload on a backlog sends it to the
     * end of the file, behind everything older, the wire untouched. */
    fake_message (36);
    bpa_tcp_offload (&bpa_log_tcp);
    CHECK (vec_len (bpa_log_tcp.outbuf) == 45
	     && file_size (spill_file) == (long) ((n - 1) * 45 + 36),
	   "bpa_tcp_offload puts a newer record behind the backlog: in memory "
	   "%u, file %ld", vec_len (bpa_log_tcp.outbuf), file_size (spill_file));
    /* The message finishes; the backlog's first chunk comes in behind it. */
    bpa_log_tcp.head_sent = 45;
    bpa_tcp_drop_sent (&bpa_log_tcp);
    bpa_tcp_replay_chunk (&bpa_log_tcp);
    CHECK (bpa_log_tcp.front_extra == 0 && bpa_log_tcp.replay_len > 0
	     && bpa_log_tcp.replay_len % 45 == 0 && bpa_log_tcp.spill_off
	     == bpa_log_tcp.replay_len,
	   "the message on the wire finishes and the backlog follows it on the "
	   "same connection: front=%u chunk=%u offset %llu",
	   bpa_log_tcp.front_extra, bpa_log_tcp.replay_len,
	   (unsigned long long) bpa_log_tcp.spill_off);
    close (pipefd[0]);
    close (pipefd[1]);
    bpa_log_tcp.fd = -1;
    vec_reset_length (bpa_log_tcp.outbuf);
    unlink (spill_file);
    snprintf (offp_g, sizeof (offp_g), "%s.off", spill_file);
    unlink (offp_g);
  }

  printf ("\n--- a 20 MB backlog drains to a reader in a bounded number of passes "
	  "(#547) ---\n");
  {
    int sv[2];
    pthread_t rd;
    u32 passes = 0, i;
    int fd;
    u8 *all = 0;

    reset (1);
    for (i = 0; vec_len (bpa_log_tcp.outbuf) < (20u << 20); i++)
      fake_message (200);
    all = bpa_log_tcp.outbuf;
    bpa_log_tcp.outbuf = 0;
    fd = open (spill_file, O_WRONLY | O_CREAT | O_TRUNC, 0640);
    if (fd < 0 || write (fd, all, vec_len (all)) != (ssize_t) vec_len (all)
	|| socketpair (AF_UNIX, SOCK_STREAM, 0, sv))
      {
	printf ("  could not make the backlog or the pair\n");
	return bpa_test_verdict (checks, failures + 1);
      }
    close (fd);
    drain_fd = sv[1];
    drained = 0;
    pthread_create (&rd, 0, drain_reader, 0);
    fcntl (sv[0], F_SETFL, O_NONBLOCK);
    bpa_log_tcp.fd = sv[0];
    bpa_log_tcp.connected = 1;
    bpa_tcp_backlog_open (&bpa_log_tcp);
    while (bpa_log_tcp.backlog && passes < 20000)
      {
	bpa_log_stream_service (&bpa_log_tcp, 1.0 + passes * 0.005);
	passes++;
	usleep (200);
      }
    CHECK (!bpa_log_tcp.backlog && bpa_log_tcp.n_sent == vec_len (all)
	     && file_size (spill_file) == -1,
	   "a 20 MB backlog went out whole to a reader in %u passes (sent %llu of "
	   "%u), its file gone", passes, (unsigned long long) bpa_log_tcp.n_sent,
	   vec_len (all));
    close (sv[0]);
    bpa_log_tcp.fd = -1;
    pthread_join (rd, 0);
    close (sv[1]);
    vec_free (all);
  }

  printf ("\n--- a frame claiming more than the buffer holds (#547) ---\n");
  reset (1);
  fake_message (45);
  bpa_log_tcp.outbuf[3] = 200; /* says 200 bytes; 45 are there */
  bpa_log_tcp.replay_len = 45;
  bpa_log_tcp.backlog = 1;
  bpa_log_tcp.head_sent = 45;
  bpa_tcp_drop_sent (&bpa_log_tcp);
  CHECK (vec_len (bpa_log_tcp.outbuf) == 0 && bpa_log_tcp.head_sent == 0
	   && bpa_log_tcp.replay_len == 0,
	 "a buffer the socket took whole is dropped whole, whatever its framing "
	 "claims - or the backlog stops for good: left=%u head=%u chunk=%u",
	 vec_len (bpa_log_tcp.outbuf), bpa_log_tcp.head_sent,
	 bpa_log_tcp.replay_len);
  bpa_log_tcp.backlog = 0;

  printf ("\n--- what bpa-log-process serves quickly (#547) ---\n");
  reset (1);
  CHECK (!bpa_log_stream_pending (&bpa_log_tcp),
	 "an empty stream is not pending");
  fake_message (45);
  CHECK (bpa_log_stream_pending (&bpa_log_tcp), "a buffered one is");
  vec_reset_length (bpa_log_tcp.outbuf);
  bpa_log_tcp.backlog = 1;
  CHECK (bpa_log_stream_pending (&bpa_log_tcp), "and so is a backlog on disk");
  bpa_log_tcp.enabled = 0;
  CHECK (!bpa_log_stream_pending (&bpa_log_tcp), "a disabled one never is");
  bpa_log_tcp.backlog = 0;

  printf ("\n--- the spill's ceiling on disk, and one boot marker (#547) ---\n");
  {
    u8 one[45];
    u64 dropped0;

    reset (1);
    fake_message (45);
    memcpy (one, bpa_log_tcp.outbuf, 45);
    vec_reset_length (bpa_log_tcp.outbuf);
    bpa_tcp_append (&bpa_log_tcp, one, 45);
    dropped0 = bpa_log_tcp.n_dropped;
    /* A file already at the ceiling: truncate() makes it so without writing. */
    if (truncate (spill_file, (off_t) BPA_TCP_SPILL_MAX))
      {
	printf ("  could not size the spill file\n");
	return bpa_test_verdict (checks, failures + 1);
      }
    bpa_tcp_append (&bpa_log_tcp, one, 45);
    CHECK (file_size (spill_file) == (long) BPA_TCP_SPILL_MAX
	     && bpa_log_tcp.n_capped == 45 && bpa_log_tcp.n_dropped == dropped0 + 45,
	   "a spill at its %llu MB ceiling takes nothing more, and counts it: "
	   "file %ld, capped %llu", (unsigned long long) (BPA_TCP_SPILL_MAX >> 20),
	   file_size (spill_file), (unsigned long long) bpa_log_tcp.n_capped);
    unlink (spill_file);

    reset (1);
    bpa_log_stream_marker (&bpa_log_tcp, 1700000000.0, 1699999000.0);
    n_marker_bytes = vec_len (bpa_log_tcp.outbuf);
    bpa_log_stream_marker (&bpa_log_tcp, 1700000030.0, 1699999000.0);
    CHECK (n_marker_bytes > 0 && vec_len (bpa_log_tcp.outbuf) == n_marker_bytes
	     && bpa_log_tcp.marker_written,
	   "the boot marker goes into a stream once, not every 30 s: %u bytes "
	   "after one, %u after two", n_marker_bytes, vec_len (bpa_log_tcp.outbuf));
  }

  printf ("\n--- what the socket has taken whole ---\n");
  {
    u32 left_after, head_after, left_bogus, head_bogus;

    reset (1);
    fake_message (45);
    fake_message (36);
    fake_message (45);
    bpa_log_tcp.head_sent = 45 + 36 + 10;

    bpa_tcp_drop_sent (&bpa_log_tcp);
    left_after = vec_len (bpa_log_tcp.outbuf);
    head_after = bpa_log_tcp.head_sent;

    /* A front that does not frame - a fragment, then a whole message the
     * socket took half of. Until #547 the walk stopped there for good: the
     * buffer was never dropped from again, and the whole of it went back to
     * the spill file and out again on every connection. It now skips to the
     * next whole message and counts what it skipped. */
    reset (1);
    {
      u8 *p;

      vec_add2 (bpa_log_tcp.outbuf, p, 12);
      memset (p, 0x41, 12);
    }
    fake_message (45);
    bpa_log_tcp.head_sent = 12 + 10;
    bpa_tcp_drop_sent (&bpa_log_tcp);
    left_bogus = vec_len (bpa_log_tcp.outbuf);
    head_bogus = bpa_log_tcp.head_sent;

    CHECK (left_after == 45 && head_after == 10 && left_bogus == 45
	     && head_bogus == 10 && bpa_log_tcp.n_resynced == 12
	     && bpa_log_tcp.outbuf[1] == 10,
	   "bpa_tcp_drop_sent drops whole messages only, and skips a front "
	   "that does not frame to the next one (#547): left=%u head=%u, "
	   "fragment left=%u head=%u resynced=%llu",
	   left_after, head_after, left_bogus, head_bogus,
	   (unsigned long long) bpa_log_tcp.n_resynced);
  }

  printf ("\n--- one turn of the service loop ---\n");
  {
    u64 service_off;

    reset (0);
    bpa_log_tcp_service (10.0);
    service_off = bpa_log_tcp.n_service;

    reset (1);
    bpa_log_tcp.collector.as_u32 = htonl (INADDR_LOOPBACK);
    bpa_log_tcp.port = 1;
    fake_message (45);
    bpa_log_tcp_service (10.0);
    CHECK (service_off == 0 && bpa_log_tcp.n_service == 1
	     && bpa_log_tcp.next_attempt == 10.0 + BPA_TCP_RETRY_SECS
	     && vec_len (bpa_log_tcp.outbuf) == 45,
	   "bpa_log_tcp_service does nothing while the transport is off, and "
	   "with no socket it retries and keeps the buffer: off=%llu "
	   "service=%llu next=%.1f left=%u",
	   (unsigned long long) service_off,
	   (unsigned long long) bpa_log_tcp.n_service,
	   bpa_log_tcp.next_attempt, vec_len (bpa_log_tcp.outbuf));
    bpa_tcp_disconnect (&bpa_log_tcp);
  }

  printf ("\n--- the last act before the process leaves ---\n");
  {
    u32 saved, saved_off, saved_nowhere;
    int reached_the_file;
    u8 want[45];

    reset (1);
    fake_message (45);
    memcpy (want, bpa_log_tcp.outbuf, 45);
    saved = bpa_log_tcp_flush_exit ();
    reached_the_file = file_equals (spill_file, want, 45);

    reset (0);
    fake_message (45);
    saved_off = bpa_log_tcp_flush_exit ();

    /* A path that cannot be written. The buffer is emptied just the same, so
     * measuring the saving by how much it shrank would report the loss as a
     * save. */
    reset (1);
    bpa_log_tcp.spill_path = (u8 *) "/proc/self/no/such/place";
    fake_message (45);
    saved_nowhere = bpa_log_tcp_flush_exit ();

    CHECK (saved == 45 && reached_the_file && saved_off == 0
	     && saved_nowhere == 0 && bpa_log_tcp.n_dropped == 45,
	   "bpa_log_tcp_flush_exit returns what reached the FILE: saved=%u "
	   "off=%u nowhere=%u dropped=%llu",
	   saved, saved_off, saved_nowhere,
	   (unsigned long long) bpa_log_tcp.n_dropped);

    bpa_log_tcp.spill_path = (u8 *) spill_file;
  }

  /* ------------------------------------------- the syslog wire (#20) */
  /*
   * The stream carries either wire, and this is the half that differs: the
   * framing. RFC 6587 octet counting - the length and a space in front of
   * each line - and not a trailing newline, because non-transparent framing
   * cannot carry a message containing the delimiter and leaves a collector
   * guessing where a truncated line ended.
   */
  {
    bpa_log_stream_t sys = { 0 };
    u32 first_len = 0;
    int framed_right = 0, one_line = 0;
    char want[64];

    sys.enabled = 1;
    sys.wire = BPA_WIRE_SYSLOG;
    sys.pen = 44444;
    sys.pri = 134;
    sys.hostname = (u8 *) "cgnat-sp-01";

    bpa_log_stream_record (&sys, 1789699259.123, 16, 0x64400005, 0xC8960010,
			   1024, 1027, 0, "joao.silva@isp");
    /* The frame says how many bytes follow, and what follows is exactly that
     * many - read here rather than assumed, because a collector that trusts
     * the count and is given another number reads the next line's first
     * bytes as the end of this one. */
    {
      char *buf = (char *) sys.outbuf;
      u32 len = vec_len (sys.outbuf), i = 0, n = 0;

      while (i < len && buf[i] >= '0' && buf[i] <= '9')
	n = n * 10 + (u32) (buf[i++] - '0');
      first_len = n;
      framed_right = i < len && buf[i] == ' ' && (i + 1 + n) == len;
      one_line = memchr (buf, '\n', len) == 0;
    }
    snprintf (want, sizeof (want), "<134>1 2026-09-18T02:40:59.123Z");
    /* The identity the record was BUILT with reaches the wire. The queue
     * entry carries it from the instant of the allocation, so what is
     * asserted here is that the stream writes what it was handed and
     * resolves nothing of its own (#21). */
    CHECK (memmem (sys.outbuf, vec_len (sys.outbuf),
		   "subscriberId=\"joao.silva@isp\"",
		   strlen ("subscriberId=\"joao.silva@isp\"")) != 0,
	   "the line carries the identity the caller was holding");
    CHECK (framed_right && first_len > 0
	     && strncmp ((char *) sys.outbuf + 4, want, strlen (want)) == 0,
	   "a syslog record is octet-counted and the count is the line: "
	   "%u byte(s), framed=%d, no newline=%d",
	   first_len, framed_right, one_line);

    /* An event this box has no name for is DROPPED and not half-written. A
     * collector storing a line that names nothing has stored a record that
     * answers a court request with a shrug. */
    {
      u32 before = vec_len (sys.outbuf);
      u64 dropped = sys.n_dropped;

      bpa_log_stream_record (&sys, 1789699259.123, 18, 0x64400005,
			     0xC8960010, 1024, 1027, 0, "joao.silva@isp");
      CHECK (vec_len (sys.outbuf) == before && sys.n_dropped == dropped + 1,
	     "an event with no name is counted as dropped, not written: "
	     "buffer %u -> %u, dropped %llu -> %llu",
	     before, vec_len (sys.outbuf), (unsigned long long) dropped,
	     (unsigned long long) sys.n_dropped);
    }

    /* THE BOOT MARKER, which without its own branch wrote IPFIX bytes into a
     * syslog stream - and a collector framing by octet count would have read
     * the next line's length out of the middle of a binary record. Driven
     * through bpa_log_stream_marker by name, on a syslog stream. */
    {
      u32 before = vec_len (sys.outbuf);
      char *buf;
      u32 len, i = 0, n = 0;
      int framed;

      bpa_log_stream_marker (&sys, 1789699259.123, 1789600000.0);
      buf = (char *) sys.outbuf + before;
      len = vec_len (sys.outbuf) - before;
      while (i < len && buf[i] >= '0' && buf[i] <= '9')
	n = n * 10 + (u32) (buf[i++] - '0');
      framed = i < len && buf[i] == ' ' && (i + 1 + n) == len;
      CHECK (framed && memmem (buf, len, "BOOT-MARKER", 11) != 0
	       && memmem (buf, len, "bootTime=", 9) != 0,
	     "the marker is a syslog line in the same grammar, framed the "
	     "same way: %u byte(s), framed=%d",
	     n, framed);
    }

    /* The service loop and the exit flush are the stream's, whichever wire it
     * speaks: driven here by name on a stream that is not connected, which is
     * the state a box is in whenever its collector is away. */
    {
      u32 saved;

      sys.fd = -1;
      sys.spill_path = (u8 *) "/proc/self/no/such/place";
      bpa_log_stream_service (&sys, 1.0);
      saved = bpa_log_stream_flush_exit (&sys);
      CHECK (saved == 0 && vec_len (sys.outbuf) == 0,
	     "a syslog stream with nowhere to write empties its buffer and "
	     "reports that nothing was saved: saved=%u left=%u",
	     saved, vec_len (sys.outbuf));
    }

    /* And the template: IPFIX prepends one on a schedule, syslog has none,
     * because every line carries its own field names. */
    sys.need_template = 1;
    bpa_tcp_prepend_template (&sys, 100.0);
    CHECK (sys.need_template == 0,
	   "a syslog stream asks for no template and stops asking");

    vec_free (sys.outbuf);
  }

  /* #551: a stream that was never enabled must not close descriptor 0.
   * The globals start as zeros, as they do in the plugin; without
   * bpa_log_streams_init() their fd is 0 and the disconnect below closes
   * stdin, which is what took VPP down on the bench. */
  {
    int keep = dup (0);
    memset (&bpa_log_tcp, 0, sizeof (bpa_log_tcp));
    memset (&bpa_log_syslog, 0, sizeof (bpa_log_syslog));
    bpa_log_streams_init ();
    bpa_tcp_disconnect (&bpa_log_syslog);
    bpa_tcp_disconnect (&bpa_log_tcp);
    CHECK (fcntl (0, F_GETFD) != -1,
	   "disabling a log stream never enabled leaves descriptor 0 open "
	   "(#551): it closed stdin, and VPP exited on the next vppctl");
    CHECK (bpa_log_tcp.fd == -1 && bpa_log_syslog.fd == -1,
	   "both streams start closed: tcp fd %d, syslog fd %d",
	   bpa_log_tcp.fd, bpa_log_syslog.fd);
    if (fcntl (0, F_GETFD) == -1 && keep >= 0)
      dup2 (keep, 0);
    if (keep >= 0)
      close (keep);
  }

  unlink (spill_file);
  rmdir (spill_dir);
  return bpa_test_verdict (checks, failures);
}
