/*
 * bpa_log_ring.h - the block-event rings, in a segment of their own (#550).
 *
 * WHY A SEGMENT AND NOT THE MAIN HEAP. Log export must never cost the box a
 * forwarded or translated packet - the maintainer's rule of 2026-10-01, after
 * a stalled syslog collector cost 78% of a 30k pps burst at rx on the legacy
 * bench and a 573 MB spill replay aborted VPP out of main heap. 0.149.7 made
 * the per-worker queue a fixed ring allocated once (#547), but in the main
 * heap, readable only by VPP's own main thread. #550 moves every slow part of
 * export - TCP, the spill, its replay, reconnection - into a log container;
 * that container has to read the events, and it cannot read VPP's heap. So
 * the rings live in one shared segment the plugin creates at start
 * (/dev/shm/prisma-bpa-log; the dataplane container runs with `ipc: host`),
 * laid out by this file and nothing else, read by whichever consumer the
 * header names.
 *
 * THE LAYOUT IS A CONTRACT between two programs built at different times, so
 * it carries a magic, a version and every size a reader needs to check before
 * it trusts an offset - and bpa_log_seg_check() is what a reader runs.
 *
 *   [ bpa_log_seg_hdr_t                    ]  BPA_LOG_SEG_HDR_BYTES
 *   [ ring 0: bpa_log_ring_t | slots * ev  ]  ring_bytes
 *   [ ring 1 ...                           ]
 *
 * One writer per ring (the worker that owns it) and one reader (the
 * consumer): head is written only by the worker, tail only by the reader, both
 * free-running u32s, so `head - tail` is the fill whatever the wrap. A full
 * ring drops the event and counts it; the worker never allocates, never
 * waits, and never calls into the kernel.
 *
 * Plain C, no VPP: test/test_bpa_log_ring.c drives every function here.
 */
#ifndef __included_bpa_log_ring_h__
#define __included_bpa_log_ring_h__

#include <stddef.h>
#include <string.h>

#define BPA_LOG_SEG_MAGIC	 "PRBALOG1"
#define BPA_LOG_SEG_VERSION	 2
#define BPA_LOG_SEG_NAME	 "/prisma-bpa-log"
#define BPA_LOG_SEG_HDR_BYTES	 1024
#define BPA_LOG_RING_HDR_BYTES	 192
/* The export ring (#550 step 2): VPP -> the log container, one record per
 * event for the TCP streams, wall clock stamped by VPP when it drained it. */
#define BPA_LOG_XRING_SLOTS	 65536
#define BPA_LOG_XEV_BYTES	 64
#define BPA_LOG_XEV_RECORD	 1
#define BPA_LOG_XEV_MARKER	 2
/* Who carries the TCP streams (syslog, IPFIX over TCP). */
#define BPA_LOG_OWNER_VPP	 0
#define BPA_LOG_OWNER_LOGGER	 1
/* The log container is alive while its heartbeat is younger than this. */
#define BPA_LOG_BEAT_STALE_NS	 3000000000ULL
#define BPA_LOG_STREAM_IPFIX	 0
#define BPA_LOG_STREAM_SYSLOG	 1
/* Who drains the rings. 0.149.x: VPP's own process; the log container takes
 * them over in #550's second step by setting the header, never both. */
#define BPA_LOG_CONSUMER_VPP	 0
#define BPA_LOG_CONSUMER_LOGGER	 1

/* A TCP stream's configuration, as VPP holds it: written by VPP only, gen
 * odd while it is being written (a reader that sees odd, or a gen that moved
 * under it, reads again). */
typedef struct
{
  volatile u32 gen;
  u8 enabled;
  u8 wire;		/* BPA_WIRE_IPFIX or BPA_WIRE_SYSLOG */
  u16 port;
  u32 collector;	/* network order, ip4_address_t.as_u32 */
  u32 pen;
  u32 pri;
  char hostname[64];
  u8 pad[12];
} bpa_log_seg_cfg_t;

/* What a TCP stream did, as the log container counts it: written by the
 * logger only, read by VPP for `show cgnat logging`. */
typedef struct
{
  u64 n_records, n_sent, n_spilled, n_replayed, n_dropped, n_connects;
  u64 n_service, n_send_calls, n_resynced, n_stalls, n_capped, pending;
  u32 connected, pending_connect;
  i32 last_errno;
  u32 pad;
} bpa_log_seg_stats_t;

typedef struct
{
  char magic[8];	/* BPA_LOG_SEG_MAGIC, not NUL-terminated */
  u32 version;		/* BPA_LOG_SEG_VERSION */
  u32 hdr_bytes;	/* BPA_LOG_SEG_HDR_BYTES */
  u32 n_rings;		/* one per vlib main: index = thread index */
  u32 ring_slots;	/* a power of two */
  u32 event_bytes;	/* sizeof (bpa_log_event_t) the writer was built with */
  u32 ring_bytes;	/* ring header + slots * event_bytes */
  u64 generation;	/* the writer's start, ns: a reader re-attaches on change */
  volatile u32 consumer; /* BPA_LOG_CONSUMER_* */
  u32 pad0;
  /* Version 2 (#550 step 2): the TCP streams, carried by the log container. */
  f64 boot_wall;	/* the dataplane's start, wall clock: the boot marker's init */
  u32 xring_slots;	/* BPA_LOG_XRING_SLOTS */
  u32 xev_bytes;	/* BPA_LOG_XEV_BYTES */
  u64 xring_offset;	/* from the segment's base */
  volatile u64 logger_beat_ns; /* CLOCK_MONOTONIC, by the logger; 0 = gone */
  volatile u32 logger_pid;     /* by the logger, for the screen */
  volatile u32 tcp_owner;      /* BPA_LOG_OWNER_*, by VPP only */
  bpa_log_seg_cfg_t cfg[2];    /* by VPP: what each stream is configured to */
  bpa_log_seg_stats_t stats[2]; /* by the logger: what each stream did */
} bpa_log_seg_hdr_t;

/* head and tail on cache lines of their own: the worker writes one, the
 * reader the other, and a shared line would bounce between them on every
 * event. */
typedef struct
{
  volatile u32 head;	/* written by the worker only */
  u8 pad0[60];
  volatile u32 tail;	/* written by the consumer only */
  u8 pad1[60];
  volatile u64 dropped; /* events lost to a full ring, by the worker */
  volatile u64 written; /* events queued, by the worker */
  u8 pad2[48];
} bpa_log_ring_t;

_Static_assert (sizeof (bpa_log_seg_hdr_t) <= BPA_LOG_SEG_HDR_BYTES,
		"the segment header outgrew its reserved bytes");
_Static_assert (sizeof (bpa_log_ring_t) == BPA_LOG_RING_HDR_BYTES,
		"the ring header is part of the segment's contract");
_Static_assert (sizeof (bpa_log_seg_cfg_t) == 96, "stream cfg: 96 bytes");
_Static_assert (sizeof (bpa_log_seg_stats_t) == 112, "stream stats: 112 bytes");

/* Bytes one ring takes, header and slots. */
static inline u32
bpa_log_ring_bytes (u32 slots, u32 event_bytes)
{
  return BPA_LOG_RING_HDR_BYTES + slots * event_bytes;
}

/* Bytes the worker rings take, header included: where the export ring
 * starts. */
static inline u64
bpa_log_seg_xring_offset (u32 n_rings, u32 slots, u32 event_bytes)
{
  return (u64) BPA_LOG_SEG_HDR_BYTES
	 + (u64) n_rings * bpa_log_ring_bytes (slots, event_bytes);
}

/* Bytes the whole segment takes for n worker rings and the export ring. */
static inline u64
bpa_log_seg_bytes (u32 n_rings, u32 slots, u32 event_bytes)
{
  return bpa_log_seg_xring_offset (n_rings, slots, event_bytes)
	 + bpa_log_ring_bytes (BPA_LOG_XRING_SLOTS, BPA_LOG_XEV_BYTES);
}

/* Lays out a zeroed segment of bpa_log_seg_bytes() at base. Returns 0, or -1
 * when slots is not a power of two (the index mask depends on it). */
static inline int
bpa_log_seg_init (void *base, u32 n_rings, u32 slots, u32 event_bytes,
		  u64 generation)
{
  bpa_log_seg_hdr_t *h = (bpa_log_seg_hdr_t *) base;

  if (!slots || (slots & (slots - 1)) || !event_bytes)
    return -1;
  memset (base, 0, bpa_log_seg_bytes (n_rings, slots, event_bytes));
  h->version = BPA_LOG_SEG_VERSION;
  h->hdr_bytes = BPA_LOG_SEG_HDR_BYTES;
  h->n_rings = n_rings;
  h->ring_slots = slots;
  h->event_bytes = event_bytes;
  h->ring_bytes = bpa_log_ring_bytes (slots, event_bytes);
  h->generation = generation;
  h->consumer = BPA_LOG_CONSUMER_VPP;
  h->xring_slots = BPA_LOG_XRING_SLOTS;
  h->xev_bytes = BPA_LOG_XEV_BYTES;
  h->xring_offset = bpa_log_seg_xring_offset (n_rings, slots, event_bytes);
  h->tcp_owner = BPA_LOG_OWNER_VPP;
  /* The magic last: a reader that sees it sees a complete header. */
  __atomic_thread_fence (__ATOMIC_RELEASE);
  memcpy (h->magic, BPA_LOG_SEG_MAGIC, 8);
  return 0;
}

/* What a reader runs before it trusts a single offset: 0 when the segment of
 * `size` bytes at base is one this build can read with events of
 * event_bytes, else a negative reason (see bpa_log_seg_check_reason). */
static inline int
bpa_log_seg_check (const void *base, u64 size, u32 event_bytes)
{
  const bpa_log_seg_hdr_t *h = (const bpa_log_seg_hdr_t *) base;

  if (size < BPA_LOG_SEG_HDR_BYTES)
    return -1;
  if (memcmp (h->magic, BPA_LOG_SEG_MAGIC, 8))
    return -2;
  if (h->version != BPA_LOG_SEG_VERSION || h->hdr_bytes != BPA_LOG_SEG_HDR_BYTES)
    return -3;
  if (h->event_bytes != event_bytes)
    return -4;
  if (!h->ring_slots || (h->ring_slots & (h->ring_slots - 1))
      || h->ring_bytes != bpa_log_ring_bytes (h->ring_slots, h->event_bytes))
    return -5;
  if (bpa_log_seg_bytes (h->n_rings, h->ring_slots, h->event_bytes) > size)
    return -6;
  if (h->xring_slots != BPA_LOG_XRING_SLOTS || h->xev_bytes != BPA_LOG_XEV_BYTES
      || h->xring_offset
	   != bpa_log_seg_xring_offset (h->n_rings, h->ring_slots,
					h->event_bytes))
    return -7;
  return 0;
}

static inline const char *
bpa_log_seg_check_reason (int rv)
{
  switch (rv)
    {
    case 0: return "readable";
    case -1: return "shorter than its header";
    case -2: return "not a block-event segment (magic)";
    case -3: return "a layout version this build does not read";
    case -4: return "events of another size than this build's";
    case -5: return "ring sizes that do not add up";
    case -6: return "rings that run past the end of the segment";
    case -7: return "an export ring this build does not read";
    }
  return "unknown";
}

/* Ring i's header. */
static inline bpa_log_ring_t *
bpa_log_seg_ring (void *base, u32 i)
{
  bpa_log_seg_hdr_t *h = (bpa_log_seg_hdr_t *) base;

  return (bpa_log_ring_t *) ((u8 *) base + BPA_LOG_SEG_HDR_BYTES
			     + (u64) i * h->ring_bytes);
}

/* The export ring's header. */
static inline bpa_log_ring_t *
bpa_log_seg_xring (void *base)
{
  return (bpa_log_ring_t *) ((u8 *) base
			     + ((bpa_log_seg_hdr_t *) base)->xring_offset);
}

/* Is the log container alive at now_ns (CLOCK_MONOTONIC)? */
static inline int
bpa_log_seg_logger_alive (const bpa_log_seg_hdr_t *h, u64 now_ns)
{
  u64 beat = h->logger_beat_ns;

  return beat && now_ns >= beat ? now_ns - beat < BPA_LOG_BEAT_STALE_NS
				: beat != 0;
}

/* VPP's side of a stream's configuration: gen odd while written. */
static inline void
bpa_log_seg_cfg_write (bpa_log_seg_cfg_t *c, u8 enabled, u8 wire, u16 port,
		       u32 collector, u32 pen, u32 pri, const char *hostname)
{
  __atomic_store_n (&c->gen, c->gen | 1, __ATOMIC_RELEASE);
  __atomic_thread_fence (__ATOMIC_SEQ_CST);
  c->enabled = enabled;
  c->wire = wire;
  c->port = port;
  c->collector = collector;
  c->pen = pen;
  c->pri = pri;
  memset (c->hostname, 0, sizeof (c->hostname));
  if (hostname)
    strncpy (c->hostname, hostname, sizeof (c->hostname) - 1);
  __atomic_store_n (&c->gen, (c->gen | 1) + 1, __ATOMIC_RELEASE);
}

/* The logger's side: a consistent copy, or -1 while VPP is writing. */
static inline int
bpa_log_seg_cfg_read (const bpa_log_seg_cfg_t *c, bpa_log_seg_cfg_t *out)
{
  u32 g0 = __atomic_load_n (&c->gen, __ATOMIC_ACQUIRE);

  if (g0 & 1)
    return -1;
  memcpy (out, (const void *) c, sizeof (*out));
  __atomic_thread_fence (__ATOMIC_ACQUIRE);
  if (__atomic_load_n (&c->gen, __ATOMIC_ACQUIRE) != g0)
    return -1;
  out->gen = g0;
  return 0;
}

/* The event in slot index (free-running; masked here). */
static inline void *
bpa_log_ring_slot (bpa_log_ring_t *r, u32 slots, u32 event_bytes, u32 index)
{
  return (u8 *) r + BPA_LOG_RING_HDR_BYTES
	 + (u64) (index & (slots - 1)) * event_bytes;
}

/* The worker's side: the slot to fill next, or NULL when the ring is full -
 * then counted, and the caller drops the event. Fill it, then publish. */
static inline void *
bpa_log_ring_reserve (bpa_log_ring_t *r, u32 slots, u32 event_bytes)
{
  u32 head = r->head;
  u32 tail = __atomic_load_n (&r->tail, __ATOMIC_ACQUIRE);

  if (head - tail >= slots)
    {
      r->dropped++;
      return 0;
    }
  return bpa_log_ring_slot (r, slots, event_bytes, head);
}

/* Publishes the slot reserve() returned: the reader sees it whole. */
static inline void
bpa_log_ring_publish (bpa_log_ring_t *r)
{
  r->written++;
  __atomic_store_n (&r->head, r->head + 1, __ATOMIC_RELEASE);
}

/* The consumer's side: how many events are waiting, read with the ordering
 * that makes their contents visible. */
static inline u32
bpa_log_ring_ready (bpa_log_ring_t *r, u32 *tail_out)
{
  u32 head = __atomic_load_n (&r->head, __ATOMIC_ACQUIRE);

  *tail_out = r->tail;
  return head - *tail_out;
}

/* Gives n consumed slots back to the worker. */
static inline void
bpa_log_ring_consume (bpa_log_ring_t *r, u32 n)
{
  __atomic_store_n (&r->tail, r->tail + n, __ATOMIC_RELEASE);
}

#endif /* __included_bpa_log_ring_h__ */
