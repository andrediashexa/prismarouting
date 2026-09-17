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
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

#include "bpa_log_shim.h"
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
    at = bpa_tcp_msg_start (&b);
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
    CHECK (while_off == 0 && vec_len (bpa_log_tcp.outbuf) == 45 && rd16 (2) == 45
	     && rd16 (16) == BPA_TCP_TEMPLATE_ID && rd16 (18) == 29
	     && rd64 (20) == 1700000000500ull && bpa_log_tcp.outbuf[28] == 16
	     && rd32 (29) == 0x0a000001 && rd32 (33) == 0xc6336401
	     && rd16 (37) == 2048 && rd16 (39) == 3071 && rd32 (41) == 9
	     && bpa_log_tcp.seq == 1 && bpa_log_tcp.n_records == 1,
	   "bpa_log_tcp_record writes one 45-byte message carrying the seven "
	   "RFC 8158 fields, and nothing at all while the transport is off: "
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

    bpa_tcp_disconnect ();
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
    bpa_tcp_try_connect (999.0);
    connects_after_gate = bpa_log_tcp.n_connects;
    attempt_after_gate = bpa_log_tcp.next_attempt;

    bpa_tcp_try_connect (1000.0);
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

    bpa_tcp_disconnect ();
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

    bpa_tcp_spill_now ();
    CHECK (file_equals (spill_file, want, n) && bpa_log_tcp.n_spilled == n
	     && vec_len (bpa_log_tcp.outbuf) == 0 && bpa_log_tcp.fd == -1,
	   "bpa_tcp_spill_now writes the buffer whole, empties it, counts it "
	   "and drops a connection holding half a message: file=%ld "
	   "spilled=%llu left=%u fd=%d",
	   file_size (spill_file), (unsigned long long) bpa_log_tcp.n_spilled,
	   vec_len (bpa_log_tcp.outbuf), bpa_log_tcp.fd);

    /* The threshold. Under it the bytes stay in memory, where the socket can
     * still take them in order; at it they go to disk rather than growing
     * without bound. */
    reset (1);
    fake_message (45);
    bpa_tcp_spill ();
    {
      u64 spilled_small = bpa_log_tcp.n_spilled;
      u32 kept_small = vec_len (bpa_log_tcp.outbuf);
      u8 *p;

      vec_add2 (bpa_log_tcp.outbuf, p, BPA_TCP_SPILL_BYTES);
      memset (p, 0, BPA_TCP_SPILL_BYTES);
      bpa_tcp_spill ();
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

  printf ("\n--- and back out of it, in order ---\n");
  {
    int fd;

    reset (1);
    fd = open (spill_file, O_WRONLY | O_CREAT | O_TRUNC, 0640);
    if (fd < 0 || write (fd, "OLDER", 5) != 5)
      {
	printf ("  could not write the spill file\n");
	return bpa_test_verdict (checks, failures + 1);
      }
    close (fd);
    {
      u8 *b = bpa_log_tcp.outbuf;
      bpa_tcp_put (&b, "NEWER", 5);
      bpa_log_tcp.outbuf = b;
    }

    bpa_tcp_replay_spill ();
    CHECK (vec_len (bpa_log_tcp.outbuf) == 10
	     && memcmp (bpa_log_tcp.outbuf, "OLDERNEWER", 10) == 0
	     && bpa_log_tcp.n_replayed == 5 && file_size (spill_file) == -1,
	   "bpa_tcp_replay_spill puts the older bytes IN FRONT and removes "
	   "the file: \"%.10s\" replayed=%llu file=%ld",
	   (const char *) bpa_log_tcp.outbuf,
	   (unsigned long long) bpa_log_tcp.n_replayed,
	   file_size (spill_file));
  }

  printf ("\n--- what the socket has taken whole ---\n");
  {
    u32 left_after, head_after, left_bogus, head_bogus;

    reset (1);
    fake_message (45);
    fake_message (36);
    fake_message (45);
    bpa_log_tcp.head_sent = 45 + 36 + 10;

    bpa_tcp_drop_sent ();
    left_after = vec_len (bpa_log_tcp.outbuf);
    head_after = bpa_log_tcp.head_sent;

    /* A length no message this file writes could carry. Guessing where the
     * next one starts would misframe everything behind it for good, so the
     * walk stops. */
    reset (1);
    fake_message (45);
    bpa_log_tcp.outbuf[2] = 0;
    bpa_log_tcp.outbuf[3] = 8;
    bpa_log_tcp.head_sent = 45;
    bpa_tcp_drop_sent ();
    left_bogus = vec_len (bpa_log_tcp.outbuf);
    head_bogus = bpa_log_tcp.head_sent;

    CHECK (left_after == 45 && head_after == 10 && left_bogus == 45
	     && head_bogus == 45,
	   "bpa_tcp_drop_sent drops whole messages only, and stops on a "
	   "length it cannot trust: left=%u head=%u, bogus left=%u head=%u",
	   left_after, head_after, left_bogus, head_bogus);
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
    bpa_tcp_disconnect ();
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

  unlink (spill_file);
  rmdir (spill_dir);
  return bpa_test_verdict (checks, failures);
}
