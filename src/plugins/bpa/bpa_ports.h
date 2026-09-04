/*
 * bpa_ports.h - per-subscriber port allocator inside a BPA block.
 *
 * Even in deterministic mode the block alone is not enough: two flows from
 * the same subscriber to the same destination must not collide on the same
 * source port. This is the only per-subscriber state the deterministic
 * datapath needs - block_size bits each (64 bytes for a 512-port block).
 *
 * A set bit means the port is in use. Tail bits beyond n_ports are set at
 * init so they can never be handed out.
 */

#ifndef __included_bpa_ports_h__
#define __included_bpa_ports_h__

#ifdef BPA_STANDALONE
#include "bpa_shim.h"
#else
#include <vlib/vlib.h>
#endif

#define BPA_PORTMAP_MAX_WORDS (65536 / 64)

typedef struct
{
  u16 n_ports;  /* block size */
  u16 n_words;  /* ceil(n_ports / 64) */
  u16 n_free;   /* ports still available */
  u16 hint;     /* rotating cursor, so we do not rescan from zero */
  u64 *bits;    /* caller-owned, n_words entries */
} bpa_portmap_t;

static_always_inline void
bpa_portmap_init (bpa_portmap_t *pm, u16 n_ports, u64 *storage)
{
  u32 i, tail;

  pm->n_ports = n_ports;
  pm->n_words = (u16) ((n_ports + 63) / 64);
  pm->n_free = n_ports;
  pm->hint = 0;
  pm->bits = storage;

  for (i = 0; i < pm->n_words; i++)
    pm->bits[i] = 0;

  /* mark the padding bits of the last word as permanently used */
  tail = (u32) pm->n_words * 64 - n_ports;
  if (tail)
    pm->bits[pm->n_words - 1] = ~0ull << (64 - tail);
}

static_always_inline int
bpa_portmap_is_used (const bpa_portmap_t *pm, u16 off)
{
  return (pm->bits[off >> 6] >> (off & 63)) & 1ull;
}

/*
 * Allocate a port offset within the block.
 *
 * `preferred` implements port preservation: when the subscriber's original
 * source port happens to fall inside the block and is free, we keep it.
 * That keeps some peer-to-peer and SIP behaviour working. Pass -1 to skip.
 *
 * Returns the offset, or -1 when the block is exhausted.
 */
static_always_inline int
bpa_portmap_alloc (bpa_portmap_t *pm, int preferred)
{
  u32 i, w0;

  if (PREDICT_FALSE (pm->n_free == 0))
    return -1;

  if (preferred >= 0 && preferred < (int) pm->n_ports
      && !bpa_portmap_is_used (pm, (u16) preferred))
    {
      pm->bits[preferred >> 6] |= 1ull << (preferred & 63);
      pm->n_free--;
      return preferred;
    }

  w0 = (u32) pm->hint >> 6;
  for (i = 0; i < pm->n_words; i++)
    {
      u32 idx = w0 + i;
      u64 avail;

      if (idx >= pm->n_words)
	idx -= pm->n_words;

      avail = ~pm->bits[idx];
      if (avail)
	{
	  u32 bit = (u32) __builtin_ctzll (avail);
	  u32 off = idx * 64 + bit;

	  pm->bits[idx] |= 1ull << bit;
	  pm->n_free--;
	  pm->hint = (u16) ((off + 1) % pm->n_ports);
	  return (int) off;
	}
    }

  return -1; /* n_free disagreed with the bitmap */
}

static_always_inline void
bpa_portmap_free (bpa_portmap_t *pm, u16 off)
{
  u64 mask = 1ull << (off & 63);

  if (PREDICT_FALSE (off >= pm->n_ports))
    return;
  if (PREDICT_FALSE (!(pm->bits[off >> 6] & mask)))
    return; /* double free */

  pm->bits[off >> 6] &= ~mask;
  pm->n_free++;
}

#endif /* __included_bpa_ports_h__ */
