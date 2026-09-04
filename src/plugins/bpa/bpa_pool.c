/*
 * bpa_pool.c - pool setup and derived-value computation.
 *
 * Keep in sync with ref/bpa_model.py; ref/check_magic.py proves the
 * reciprocal constants computed here match integer division exactly.
 */

#include "bpa_pool.h"

/*
 * Largest pool capacity we accept. The reciprocal below multiplies a
 * value < capacity by a magic < 2^33, so capping capacity at 2^28 keeps
 * the intermediate product under 2^61 and safely inside u64.
 * 2^28 is 268M subscribers - orders of magnitude past any real deployment.
 */
#define BPA_MAX_CAPACITY (1u << 28)

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
  u8 l;

  if (!bpa_is_pow2 (p->outside_count))
    return clib_error_return (0, "pool '%v': public prefix size %u is not a "
				 "power of two", p->name, p->outside_count);

  if (!bpa_is_pow2 (p->block_size))
    return clib_error_return (0, "pool '%v': block size %u must be a power "
				 "of two", p->name, p->block_size);

  if (p->port_min < 1 || p->port_min > p->port_max)
    return clib_error_return (0, "pool '%v': invalid port range %u-%u",
			      p->name, p->port_min, p->port_max);

  p->port_count = (u32) p->port_max - p->port_min + 1;

  if (p->block_size > p->port_count)
    return clib_error_return (0, "pool '%v': block size %u exceeds the %u "
				 "usable ports", p->name, p->block_size,
			      p->port_count);

  p->blocks_per_ip = p->port_count / p->block_size;
  p->capacity = p->outside_count * p->blocks_per_ip;

  if (p->capacity > BPA_MAX_CAPACITY)
    return clib_error_return (0, "pool '%v': capacity %u exceeds the %u limit",
			      p->name, p->capacity, BPA_MAX_CAPACITY);

  p->log2_outside = bpa_log2_exact (p->outside_count);
  p->log2_block = bpa_log2_exact (p->block_size);
  p->outside_mask = p->outside_count - 1;

  /*
   * Reciprocal for division by blocks_per_ip, used only by the BLOCKED
   * policy. INTERLEAVED never divides: both directions are shift-only.
   *
   *   l     = bit_length(d)
   *   magic = ceil(2^(32+l) / d)
   *   n / d == (n * magic) >> (32 + l)   for every n < BPA_MAX_CAPACITY
   */
  l = 0;
  while ((1u << l) <= p->blocks_per_ip && l < 32)
    l++;

  p->bpi_magic =
    (u64) ((((u64) 1 << (32 + l)) + p->blocks_per_ip - 1) / p->blocks_per_ip);
  p->bpi_shift = 32 + l;

  return 0;
}
