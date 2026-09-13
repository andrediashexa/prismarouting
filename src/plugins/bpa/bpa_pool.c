/*
 * bpa_pool.c - address-set construction and pool setup.
 *
 * Keep in sync with ref/bpa_model.py; ref/check_magic.py proves the
 * reciprocal constants computed here match integer division exactly.
 */

#include <stdlib.h>

#include "bpa_pool.h"

/*
 * Largest pool capacity we accept. The reciprocal multiplies a value below
 * this by a magic under 2^33, keeping the product under 2^61 and safely
 * inside u64. 2^28 is 268M subscribers - far past any real deployment.
 */
#define BPA_MAX_CAPACITY (1u << 28)

typedef struct
{
  u32 base;
  u32 count;
} bpa_pfx_t;

static int
bpa_pfx_cmp (const void *a, const void *b)
{
  u32 x = ((const bpa_pfx_t *) a)->base;
  u32 y = ((const bpa_pfx_t *) b)->base;

  return x < y ? -1 : (x > y ? 1 : 0);
}

clib_error_t *
bpa_addr_set_build (bpa_addr_set_t *s, const u32 *bases, const u32 *counts,
		    u32 n)
{
  bpa_pfx_t tmp[BPA_MAX_RANGES];
  u32 i, out = 0, idx = 0;

  if (n == 0)
    return clib_error_return (0, "address set is empty");
  if (n > BPA_MAX_RANGES)
    return clib_error_return (0, "%u prefixes exceeds the %u supported", n,
			      BPA_MAX_RANGES);

  for (i = 0; i < n; i++)
    {
      if (counts[i] == 0)
	return clib_error_return (0, "prefix %u is empty", i);
      if ((u64) bases[i] + counts[i] > 0x100000000ull)
	return clib_error_return (0, "prefix %u wraps past 255.255.255.255", i);
      tmp[i].base = bases[i];
      tmp[i].count = counts[i];
    }

  qsort (tmp, n, sizeof (tmp[0]), bpa_pfx_cmp);

  /* Overlap would break the address <-> index bijection, and a broken
   * bijection means a subscriber can be billed or logged as another one.
   * Reject it instead of quietly repairing the config. */
  for (i = 1; i < n; i++)
    if ((u64) tmp[i].base < (u64) tmp[i - 1].base + tmp[i - 1].count)
      return clib_error_return (0, "overlapping prefixes in address set");

  /* Adjacent ranges collapse into one, which shortens the datapath search. */
  for (i = 0; i < n; i++)
    {
      if (out && (u64) s->ranges[out - 1].base + s->ranges[out - 1].count
		   == (u64) tmp[i].base)
	{
	  s->ranges[out - 1].count += tmp[i].count;
	  continue;
	}
      s->ranges[out].base = tmp[i].base;
      s->ranges[out].count = tmp[i].count;
      out++;
    }

  for (i = 0; i < out; i++)
    {
      s->ranges[i].first_idx = idx;
      idx += s->ranges[i].count;
    }

  s->n_ranges = out;
  s->count = idx;
  return 0;
}

/*
 *   l     = bit_length(d)
 *   magic = ceil(2^(32+l) / d)
 *   n / d == (n * magic) >> (32 + l)   for every n < BPA_MAX_CAPACITY
 *
 * The product stays under 2^61, safely inside u64. Proven for 1074
 * arbitrary divisors by ref/check_magic.py.
 */
void
bpa_divisor_init (bpa_divisor_t *dv, u32 d)
{
  u8 l = 0;

  dv->d = d;
  dv->is_pow2 = (d && ((d & (d - 1)) == 0)) ? 1 : 0;

  dv->log2 = 0;
  if (dv->is_pow2)
    while ((1u << dv->log2) < d)
      dv->log2++;

  while ((1u << l) <= d && l < 32)
    l++;

  dv->magic = ((((u64) 1 << (32 + l)) + d - 1) / d);
  dv->shift = 32 + l;
}

static u8
bpa_log2_exact (u32 v)
{
  u8 s = 0;
  while ((1u << s) < v)
    s++;
  return s;
}

static int
bpa_is_pow2 (u32 v)
{
  return v && ((v & (v - 1)) == 0);
}

clib_error_t *
bpa_pool_init (bpa_pool_t *p)
{
  u64 capacity;

  if (p->inside.count == 0 || p->outside.count == 0)
    return clib_error_return (0, "pool '%v': address sets must be built first",
			      p->name);

  if (!bpa_is_pow2 (p->block_size))
    return clib_error_return (
      0, "pool '%v': block size %u must be a power of two", p->name,
      p->block_size);

  if (p->port_min < 1 || p->port_min > p->port_max)
    return clib_error_return (0, "pool '%v': invalid port range %u-%u",
			      p->name, p->port_min, p->port_max);

  p->port_count = (u32) p->port_max - p->port_min + 1;

  if (p->block_size > p->port_count)
    return clib_error_return (
      0, "pool '%v': block size %u exceeds the %u usable ports", p->name,
      p->block_size, p->port_count);

  p->blocks_per_ip = p->port_count / p->block_size;

  /*
   * A reserve that takes every block leaves a pool that maps nobody. Refused
   * rather than accepted as a capacity of zero, because a capacity of zero
   * reads as a pool that works and serves no one.
   */
  if (p->block_reserve >= p->blocks_per_ip)
    return clib_error_return (
      0, "pool '%v': reserve %u leaves no blocks to map: a public address "
	 "holds %u blocks of %u ports",
      p->name, p->block_reserve, p->blocks_per_ip, p->block_size);

  /*
   * A dynamic pool already has spare blocks: every block nothing is holding
   * is on the free list, and that list is where an extension comes from. A
   * reserve there would take blocks OUT of the list the extension is drawn
   * from. Refused rather than accepted and inert, so that a pool moved from
   * deterministic to dynamic says what it is losing.
   */
  if (p->block_reserve && p->mode != BPA_MODE_DETERMINISTIC)
    return clib_error_return (
      0, "pool '%v': reserve applies to deterministic pools only; a dynamic "
	 "pool's extension already comes off the free list",
      p->name);

  p->blocks_mapped = p->blocks_per_ip - p->block_reserve;
  p->reserve_count = p->outside.count * p->block_reserve;

  capacity = (u64) p->outside.count * p->blocks_mapped;
  if (capacity > BPA_MAX_CAPACITY)
    return clib_error_return (
      0, "pool '%v': capacity %llu exceeds the %u limit", p->name,
      (unsigned long long) capacity, BPA_MAX_CAPACITY);
  p->capacity = (u32) capacity;

  /*
   * One block per subscriber unless the operator asks otherwise, which is the
   * behaviour every existing configuration already has. A cap of one keeps
   * this feature entirely inert until it is chosen.
   */
  if (p->max_blocks == 0)
    p->max_blocks = 1;

  /*
   * A stride of blocks_per_ip or more steps past the end of the subscriber's
   * own public address on its very first extension, so it can never place
   * anything. Refused rather than accepted and left silently inert.
   */
  if (p->block_stride >= p->blocks_mapped)
    return clib_error_return (
      0, "pool '%v': block-stride %u does not fit: a public address holds %u "
	 "mappable blocks, so a subscriber's second block would land past the "
	 "end of them",
      p->name, p->block_stride, p->blocks_mapped);

  /*
   * The documented defaults, except where the operator said otherwise. A pool
   * that states no opinion gets what the documentation promises rather than
   * the behaviour of an uninitialised field.
   */
  p->alg = (BPA_ALG_DEFAULT & ~p->alg_set) | (p->alg & p->alg_set);

  p->log2_block = bpa_log2_exact (p->block_size);

  /* One divisor drives the policy split; see bpa_split(). */
  {
    u32 d = p->policy == BPA_POLICY_INTERLEAVED ? p->outside.count
						: p->blocks_mapped;
    if (d == 0)
      return clib_error_return (0, "pool '%v': divisor is zero", p->name);
    bpa_divisor_init (&p->div, d);
  }

  return 0;
}
