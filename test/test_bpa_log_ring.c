/*
 * test_bpa_log_ring.c - the block-event rings' segment (#550), as
 * bpa_log_ring.h lays it out: the header a reader checks, the ring a worker
 * fills and a consumer empties, the drop when full, the wrap of the
 * free-running indexes, one writer and one reader on two threads, and a
 * reader in ANOTHER PROCESS through a real /dev/shm segment - the log
 * container's case. No VPP.
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
#include "bpa_shim.h"
#include "bpa_log_ring.h"

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

typedef struct
{
  u32 seq;
  u32 ring;
  u8 rest[44];
} ev_t; /* 52 bytes, the plugin's event size */

#define SLOTS 64
#define N_RINGS 3

static void *
seg_alloc (u32 n, u32 slots, u32 eb, u64 *size)
{
  *size = bpa_log_seg_bytes (n, slots, eb);
  return aligned_alloc (64, (*size + 63) & ~63ULL);
}

typedef struct
{
  bpa_log_ring_t *r;
  u32 n;
  u64 consumed;
  u64 out_of_order;
} reader_t;

static volatile int writer_done;

static void *
reader (void *arg)
{
  reader_t *rd = arg;
  u32 expect = 0;

  for (;;)
    {
      u32 tail, n = bpa_log_ring_ready (rd->r, &tail), k;
      int done = writer_done;

      for (k = 0; k < n; k++)
	{
	  ev_t *e = bpa_log_ring_slot (rd->r, SLOTS, sizeof (ev_t), tail + k);
	  /* Sequence numbers only rise; a gap is a drop the writer counted. */
	  if (e->seq < expect)
	    rd->out_of_order++;
	  expect = e->seq + 1;
	}
      bpa_log_ring_consume (rd->r, n);
      rd->consumed += n;
      if (!n && done && bpa_log_ring_ready (rd->r, &tail) == 0)
	break;
    }
  return 0;
}

int
main (void)
{
  u64 size;
  u8 *seg = seg_alloc (N_RINGS, SLOTS, sizeof (ev_t), &size);
  bpa_log_seg_hdr_t *h = (bpa_log_seg_hdr_t *) seg;
  bpa_log_ring_t *r;
  u32 i, tail;

  printf ("=== the segment and its header ===\n");
  CHECK (sizeof (ev_t) == 52, "the test's event is the plugin's 52 bytes");
  CHECK (bpa_log_seg_xring_offset (3, 64, 52) == 1024 + 3 * (192 + 64 * 52),
	 "the export ring starts where the worker rings end");
  CHECK (bpa_log_ring_bytes (64, 52) == 192 + 64 * 52,
	 "one ring is its 192-byte header and its slots");
  CHECK (size == 1024 + 3 * (192 + 64 * 52) + (192 + 65536 * 64),
	 "a segment is its header, each worker ring's header and slots, and "
	 "the export ring: %llu",
	 (unsigned long long) size);
  CHECK (sizeof (bpa_log_seg_hdr_t) <= 1024,
	 "the version 2 header fits its 1024 bytes: %zu",
	 sizeof (bpa_log_seg_hdr_t));
  CHECK (bpa_log_seg_init (seg, N_RINGS, 63, sizeof (ev_t), 1) == -1,
	 "a ring that is not a power of two is refused (the mask needs it)");
  CHECK (bpa_log_seg_init (seg, N_RINGS, SLOTS, sizeof (ev_t), 77) == 0,
	 "a power-of-two ring is laid out");
  CHECK (!memcmp (h->magic, "PRBALOG1", 8) && h->version == 2
	   && h->n_rings == N_RINGS && h->generation == 77
	   && h->consumer == BPA_LOG_CONSUMER_VPP,
	 "the header carries the magic, version, sizes and generation, and "
	 "VPP is the consumer until the log container takes over");
  CHECK (h->tcp_owner == BPA_LOG_OWNER_VPP && h->xring_slots == 65536
	   && h->xev_bytes == 64
	   && (u8 *) bpa_log_seg_xring (seg)
		== seg + 1024 + 3 * (192 + 64 * 52),
	 "VPP carries the TCP streams until a log container says it is alive, "
	 "and the export ring follows the worker rings");

  printf ("=== what a reader checks before trusting an offset ===\n");
  CHECK (bpa_log_seg_check (seg, size, sizeof (ev_t)) == 0,
	 "a whole segment of this build reads");
  CHECK (bpa_log_seg_check (seg, 100, sizeof (ev_t)) == -1,
	 "shorter than its header: refused");
  CHECK (bpa_log_seg_check (seg, size - 1, sizeof (ev_t)) == -6,
	 "a segment cut short of its last ring: refused");
  CHECK (bpa_log_seg_check (seg, size, 60) == -4,
	 "events of another size (another build's layout): refused");
  h->version = 1;
  CHECK (bpa_log_seg_check (seg, size, sizeof (ev_t)) == -3,
	 "a layout version this build does not know (step 1's): refused");
  h->version = 2;
  h->xring_slots = 1024;
  CHECK (bpa_log_seg_check (seg, size, sizeof (ev_t)) == -7,
	 "an export ring of another size: refused");
  h->xring_slots = 65536;
  h->magic[0] = 'X';
  CHECK (bpa_log_seg_check (seg, size, sizeof (ev_t)) == -2,
	 "not the magic: refused");
  memcpy (h->magic, BPA_LOG_SEG_MAGIC, 8);
  h->ring_bytes++;
  CHECK (bpa_log_seg_check (seg, size, sizeof (ev_t)) == -5,
	 "ring sizes that do not add up: refused");
  h->ring_bytes--;
  CHECK (!strcmp (bpa_log_seg_check_reason (-4),
		  "events of another size than this build's"),
	 "each refusal has words");
  CHECK ((u8 *) bpa_log_seg_ring (seg, 2)
	   == seg + 1024 + 2 * (192 + 64 * 52),
	 "ring 2 starts after the header and two whole rings");

  printf ("=== the log container's heartbeat and the streams' config ===\n");
  {
    bpa_log_seg_cfg_t c;

    CHECK (!bpa_log_seg_logger_alive (h, 5000000000ULL),
	   "no heartbeat: no log container");
    h->logger_beat_ns = 4000000000ULL;
    CHECK (bpa_log_seg_logger_alive (h, 6000000000ULL),
	   "a heartbeat 2 s old: alive");
    CHECK (!bpa_log_seg_logger_alive (h, 7500000000ULL),
	   "a heartbeat 3.5 s old: gone - VPP takes the streams back");
    h->logger_beat_ns = 0;
    CHECK (bpa_log_seg_cfg_read (&h->cfg[1], &c) == 0 && c.gen == 0
	     && !c.enabled,
	   "a stream nobody configured reads as disabled");
    bpa_log_seg_cfg_write (&h->cfg[1], 1, 1, 601, 0x0300640a, 32473, 134,
			   "prisma-bench");
    CHECK (bpa_log_seg_cfg_read (&h->cfg[1], &c) == 0 && c.gen == 2
	     && c.enabled && c.port == 601 && c.pen == 32473 && c.pri == 134
	     && c.collector == 0x0300640a && !strcmp (c.hostname, "prisma-bench"),
	   "a written config reads back whole, gen even and moved");
    h->cfg[1].gen = 3;
    CHECK (bpa_log_seg_cfg_read (&h->cfg[1], &c) == -1,
	   "a config being written (gen odd) is not read");
    h->cfg[1].gen = 2;
  }

  printf ("=== one ring: fill, drop, drain, wrap ===\n");
  r = bpa_log_seg_ring (seg, 1);
  for (i = 0; i < SLOTS; i++)
    {
      ev_t *e = bpa_log_ring_reserve (r, SLOTS, sizeof (ev_t));
      if (!e)
	break;
      e->seq = i;
      bpa_log_ring_publish (r);
    }
  CHECK (i == SLOTS && r->written == SLOTS && r->dropped == 0,
	 "a ring takes exactly its slots: %u written %llu", i,
	 (unsigned long long) r->written);
  CHECK (bpa_log_ring_reserve (r, SLOTS, sizeof (ev_t)) == 0 && r->dropped == 1,
	 "full, the next event is refused and counted - never waited for");
  CHECK (bpa_log_ring_ready (r, &tail) == SLOTS && tail == 0,
	 "the consumer sees every event the worker published");
  CHECK (((ev_t *) bpa_log_ring_slot (r, SLOTS, sizeof (ev_t), tail + 5))->seq
	   == 5,
	 "and reads them in the order they were written");
  bpa_log_ring_consume (r, SLOTS);
  CHECK (bpa_log_ring_ready (r, &tail) == 0
	   && bpa_log_ring_reserve (r, SLOTS, sizeof (ev_t)) != 0,
	 "consumed, the slots are the worker's again");
  CHECK (bpa_log_seg_ring (seg, 0)->written == 0
	   && bpa_log_seg_ring (seg, 2)->written == 0,
	 "the other rings are untouched: one writer per ring");

  /* Free-running indexes across the u32 wrap. */
  r->head = r->tail = 0xFFFFFFF0u;
  for (i = 0; i < 40; i++)
    {
      ev_t *e = bpa_log_ring_reserve (r, SLOTS, sizeof (ev_t));
      e->seq = 1000 + i;
      bpa_log_ring_publish (r);
      if (i % 3 == 2)
	{
	  u32 n = bpa_log_ring_ready (r, &tail);
	  bpa_log_ring_consume (r, n);
	}
    }
  CHECK (r->head == 0xFFFFFFF0u + 40 && bpa_log_ring_ready (r, &tail) <= SLOTS,
	 "head and tail wrap past 2^32 and the fill stays head - tail");

  printf ("=== one writer, one reader, two threads ===\n");
  {
    u8 *s2 = seg_alloc (1, SLOTS, sizeof (ev_t), &size);
    reader_t rd = { 0 };
    pthread_t t;
    u32 attempts = 2000000, seq;

    bpa_log_seg_init (s2, 1, SLOTS, sizeof (ev_t), 1);
    rd.r = bpa_log_seg_ring (s2, 0);
    writer_done = 0;
    pthread_create (&t, 0, reader, &rd);
    for (seq = 0; seq < attempts; seq++)
      {
	ev_t *e = bpa_log_ring_reserve (rd.r, SLOTS, sizeof (ev_t));
	if (!e)
	  continue;
	e->seq = seq;
	bpa_log_ring_publish (rd.r);
      }
    writer_done = 1;
    pthread_join (t, 0);
    CHECK (rd.r->written + rd.r->dropped == attempts,
	   "every attempt is either written or counted as dropped: %llu + %llu",
	   (unsigned long long) rd.r->written,
	   (unsigned long long) rd.r->dropped);
    CHECK (rd.consumed == rd.r->written,
	   "the reader consumed exactly what was written: %llu of %llu",
	   (unsigned long long) rd.consumed,
	   (unsigned long long) rd.r->written);
    CHECK (rd.out_of_order == 0,
	   "and never read an event out of order or half-written: %llu",
	   (unsigned long long) rd.out_of_order);
    free (s2);
  }

  printf ("=== a reader in another process, through /dev/shm ===\n");
  {
    char name[64];
    int fd;
    u64 sz = bpa_log_seg_bytes (2, SLOTS, sizeof (ev_t));
    u8 *w;
    pid_t pid;
    int status = 0;

    snprintf (name, sizeof (name), "/prisma-bpa-log-test-%d", (int) getpid ());
    fd = shm_open (name, O_CREAT | O_EXCL | O_RDWR, 0600);
    CHECK (fd >= 0, "a segment can be made in /dev/shm");
    if (fd >= 0 && ftruncate (fd, sz) == 0)
      {
	w = mmap (0, sz, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	bpa_log_seg_init (w, 2, SLOTS, sizeof (ev_t), 42);
	for (i = 0; i < 10; i++)
	  {
	    ev_t *e = bpa_log_ring_reserve (bpa_log_seg_ring (w, 1), SLOTS,
					    sizeof (ev_t));
	    e->seq = 500 + i;
	    e->ring = 1;
	    bpa_log_ring_publish (bpa_log_seg_ring (w, 1));
	  }
	pid = fork ();
	if (pid == 0)
	  {
	    /* The log container's side: open by name, check, read, consume. */
	    int rfd = shm_open (name, O_RDWR, 0);
	    u8 *m = mmap (0, sz, PROT_READ | PROT_WRITE, MAP_SHARED, rfd, 0);
	    bpa_log_ring_t *rr = bpa_log_seg_ring (m, 1);
	    u32 t, n, ok = 1, k;

	    if (bpa_log_seg_check (m, sz, sizeof (ev_t)) != 0)
	      _exit (2);
	    n = bpa_log_ring_ready (rr, &t);
	    for (k = 0; k < n; k++)
	      if (((ev_t *) bpa_log_ring_slot (rr, SLOTS, sizeof (ev_t), t + k))
		    ->seq != 500 + k)
		ok = 0;
	    bpa_log_ring_consume (rr, n);
	    _exit (n == 10 && ok ? 0 : 3);
	  }
	waitpid (pid, &status, 0);
	CHECK (WIFEXITED (status) && WEXITSTATUS (status) == 0,
	       "another process opens the segment by name, checks it and reads "
	       "the ten events in order (exit %d)",
	       WIFEXITED (status) ? WEXITSTATUS (status) : -1);
	CHECK (bpa_log_seg_ring (w, 1)->tail == 10,
	       "and its consumption is what the writer sees: the slots are free");
	munmap (w, sz);
      }
    if (fd >= 0)
      close (fd);
    shm_unlink (name);
  }

  free (seg);
  printf ("\n%u checks, %d failures\n", checks, failures);
  if (failures)
    {
      printf ("%d FAILURES\n", failures);
      return 1;
    }
  printf ("ALL TESTS PASSED\n");
  return 0;
}
