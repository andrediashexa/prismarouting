/*
 * test_bpa_log_layout.c - the segment bpa_log_seg_create() makes, as a
 * contract between its writer and its readers (#606).
 *
 * bpa_log_seg_size() and bpa_log_seg_layout() (bpa_log.h) are what the
 * plugin sizes and lays the segment out with, for one ring per vlib main: a
 * box with W workers has W + 1. For 0, 1 and 4 workers this asks the
 * segment itself whether the header's fields, the ring count and the size
 * agree - every ring where the one before it ends, the export ring right
 * after the last worker ring, the watch ring ending exactly at the end - and
 * whether bpa_log_seg_check(), what prisma-logd runs, reads it and refuses
 * one cut short or of another layout. test/test_log_rings.py hands the same
 * segment to the Python readers (logmon, lab/log_rings.py), which is where
 * #606's stale reader would have gone red. No VPP.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "bpa_log.h"

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

#define EV ((u32) sizeof (bpa_log_event_t))

int
main (void)
{
  static const u32 workers[] = { 0, 1, 4 };
  const u64 ring = BPA_LOG_RING_HDR_BYTES + (u64) BPA_LOG_QUEUE_MAX * EV;
  const u64 xring = BPA_LOG_RING_HDR_BYTES
		    + (u64) BPA_LOG_XRING_SLOTS * BPA_LOG_XEV_BYTES;
  const u64 mring = BPA_LOG_RING_HDR_BYTES
		    + (u64) BPA_LOG_MRING_SLOTS * BPA_LOG_XEV_BYTES;

  setvbuf (stdout, 0, _IONBF, 0);
  for (u32 w = 0; w < sizeof (workers) / sizeof (workers[0]); w++)
    {
      u32 n = workers[w] + 1;
      u64 size = bpa_log_seg_size (n);
      u8 *m = calloc (1, size);
      bpa_log_seg_hdr_t *h = (bpa_log_seg_hdr_t *) m;

      printf ("=== %u worker(s): %u ring(s), %llu bytes ===\n", workers[w], n,
	      (unsigned long long) size);
      CHECK (size == BPA_LOG_SEG_HDR_BYTES + n * ring + xring + mring,
	     "%u rings: the size is the header, the rings, the export and "
	     "the watch ring, nothing else",
	     n);
      /* A size that does not add up is reported, not written past: the
       * layout below would run off the end of the allocation. */
      if (size != BPA_LOG_SEG_HDR_BYTES + n * ring + xring + mring)
	{
	  free (m);
	  continue;
	}
      CHECK (bpa_log_seg_layout (m, n, 99) == 0, "%u rings: laid out", n);
      CHECK (!memcmp (h->magic, BPA_LOG_SEG_MAGIC, 8)
	       && h->version == BPA_LOG_SEG_VERSION
	       && h->hdr_bytes == BPA_LOG_SEG_HDR_BYTES && h->n_rings == n
	       && h->ring_slots == BPA_LOG_QUEUE_MAX && h->event_bytes == EV
	       && h->ring_bytes == ring && h->generation == 99,
	     "%u rings: the header says what was laid out", n);
      for (u32 i = 0; i < n; i++)
	CHECK ((u8 *) bpa_log_seg_ring (m, i)
		 == m + BPA_LOG_SEG_HDR_BYTES + i * ring,
	       "ring %u of %u starts where ring %u ends", i, n, i ? i - 1 : 0);
      CHECK (h->xring_offset == BPA_LOG_SEG_HDR_BYTES + n * ring
	       && (u8 *) bpa_log_seg_xring (m) == m + h->xring_offset,
	     "%u rings: the export ring starts after the last worker ring", n);
      CHECK (h->mring_offset == h->xring_offset + xring
	       && h->mring_slots == BPA_LOG_MRING_SLOTS
	       && h->mring_offset + mring == size,
	     "%u rings: the watch ring follows it and ends the segment", n);
      CHECK (bpa_log_seg_check (m, size, EV) == 0,
	     "%u rings: bpa_log_seg_check() reads it", n);
      CHECK (bpa_log_seg_check (m, h->xring_offset + xring - 1, EV) == -6,
	     "%u rings: one byte short of the export ring is refused", n);
      CHECK (bpa_log_seg_check (m, size, 52) == -4,
	     "%u rings: a reader of 52-byte events refuses it", n);

      /* Each ring's events land in that ring and nowhere else. */
      for (u32 i = 0; i < n; i++)
	for (u32 k = 0; k <= i; k++)
	  {
	    bpa_log_ring_t *r = bpa_log_seg_ring (m, i);
	    bpa_log_event_t *e = bpa_log_ring_reserve (r, BPA_LOG_QUEUE_MAX,
							EV);
	    e->event = BPA_NAT_EVENT_BLOCK_ALLOC;
	    e->ip_priv = i;
	    bpa_log_ring_publish (r);
	  }
      for (u32 i = 0; i < n; i++)
	{
	  bpa_log_ring_t *r = bpa_log_seg_ring (m, i);
	  bpa_log_event_t *e = bpa_log_ring_slot (r, BPA_LOG_QUEUE_MAX, EV, i);
	  CHECK (r->written == i + 1 && e->ip_priv == i,
		 "ring %u holds its own %u event(s)", i, i + 1);
	}
      CHECK (bpa_log_seg_xring (m)->head == 0 && bpa_log_seg_mring (m)
	       && bpa_log_seg_mring (m)->head == 0,
	     "%u rings: and the export and watch rings none of them", n);

      h->version = BPA_LOG_SEG_VERSION - 1;
      CHECK (bpa_log_seg_check (m, size, EV) == -3,
	     "%u rings: another layout version is refused", n);
      free (m);
    }

  printf ("\n%u checks, %d failures\n", checks, failures);
  if (failures)
    {
      printf ("%d FAILURES\n", failures);
      return 1;
    }
  printf ("ALL TESTS PASSED\n");
  return 0;
}
