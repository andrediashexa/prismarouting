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

/*
 * ONE FUNCTION FOR BOTH ROUTES. `block-stride` reaches the plugin on two
 * messages: bpa_pool_add_del, where the pool is being built and
 * bpa_pool_init() reads the stride off it, and bpa_pool_settings_set, where
 * it changes in place on a pool that stays put. Each used to carry its own
 * copy of these two tests, and the second copy lived in bpa.c, which needs
 * a VPP to compile - so the settings route's refusal was asserted by
 * nothing, while three comments rested on both routes refusing the same
 * pools. Now they refuse through this one function, the harness that
 * compiles this file measures it on both routes, and "both refuse it" is
 * true by construction rather than by agreement (#375).
 *
 * Both are facts about the POOL - they hold at every worker count, on
 * every box and after every restart - which is what separates them from
 * bpa_stride_warn(): refusing over a worker count would stop a pool
 * accepted on one box from coming up on the same box with more workers.
 *
 * A STRIDE ON A POOL WHOSE EXTENSIONS DO NOT COME OFF THE FREE LIST. A
 * stride steps along the MAPPED blocks of the subscriber's public address,
 * and bpa_dyn_acquire_extra() applies it only on that walk. A deterministic
 * pool's extension never makes that walk: it comes out of the reserve, an
 * index space of its own with a placement rule of its own - the first free
 * reserve block on the subscriber's own public address in this worker's
 * slice, and otherwise the top of this worker's reserve free stack, on
 * whatever address that is. The own address is a preference and not a rule
 * there, exactly as it is for a dynamic pool; what is fixed is that neither
 * step reads the stride, so the setting is never read on that pool.
 * Refused rather than accepted and inert, for the reason the mirror image
 * is refused in bpa_pool_init(): a pool moved from dynamic to deterministic
 * says what it is losing, instead of keeping a number that quietly stopped
 * meaning anything and a "show bpa pool" row still promising the layout.
 *
 * AND A STRIDE THAT STEPS OFF THE ADDRESS. blocks_mapped or more steps
 * past the end of the subscriber's own public address on its very first
 * extension, so it can never place anything. Refused rather than accepted
 * and left silently inert. Against blocks_mapped, which is the bound
 * bpa_stride_block() itself applies; the settings route used to test
 * blocks_per_ip, and on the one mode that reaches this test the two are
 * the same number, a dynamic pool having no reserve.
 *
 * The mode first: a stride too large for a deterministic pool draws the
 * mode sentence, because that pool has no stride that fits, of any size.
 */
clib_error_t *
bpa_pool_stride_refusal (const bpa_pool_t *p, u32 stride)
{
  if (stride == 0)
    return 0;
  if (p->mode != BPA_MODE_DYNAMIC)
    return clib_error_return (
      0, "pool '%v': block-stride applies to dynamic pools only; a stride "
	 "steps along the mapped blocks, and a deterministic pool's extension "
	 "comes out of this worker's reserve instead, own address first, so "
	 "the setting is never read",
      p->name);
  if (stride >= p->blocks_mapped)
    return clib_error_return (
      0, "pool '%v': block-stride %u does not fit: a public address holds %u "
	 "mappable blocks, so a subscriber's second block would land past the "
	 "end of them",
      p->name, stride, p->blocks_mapped);
  return 0;
}

/*
 * A first allocation larger than one block, on the two pools it cannot mean
 * anything on (#462).
 *
 * NOT DYNAMIC. A deterministic subscriber's first block is the formula's,
 * and anything past it comes out of the reserve only when that block fills;
 * handing a new subscriber reserve blocks up front would spend the reserve
 * on subscribers that never needed it, and the reserve is what the formula
 * pays capacity for. Refused rather than stored and inert, the reasoning
 * bpa_pool_stride_refusal() gives.
 *
 * ABOVE THE CAP. max_blocks is the most a subscriber may hold; a first
 * allocation over it would be one the allocator stops short of without a
 * word, and the operator would read a number the box never honours. A cap of
 * 0 is one block, the way bpa_pool_init() and the settings route store it.
 */
clib_error_t *
bpa_pool_initial_refusal (const bpa_pool_t *p, u32 initial, u32 max_blocks)
{
  u32 cap = max_blocks ? max_blocks : 1;

  if (initial <= 1)
    return 0;
  if (p->mode != BPA_MODE_DYNAMIC)
    return clib_error_return (
      0, "pool '%v': initial-blocks applies to dynamic pools only; a "
	 "deterministic subscriber's first block is the formula's",
      p->name);
  if (initial > cap)
    return clib_error_return (
      0, "pool '%v': initial-blocks %u is above max-blocks %u, the most a "
	 "subscriber may hold",
      p->name, initial, cap);
  return 0;
}

clib_error_t *
bpa_pool_init (bpa_pool_t *p)
{
  u64 capacity;
  clib_error_t *e;

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
   * The stride, on the pool it is arriving with. The two refusals and the
   * argument for them are on bpa_pool_stride_refusal(), which is shared with
   * the in-place route so that the two cannot refuse different pools.
   */
  if ((e = bpa_pool_stride_refusal (p, p->block_stride)))
    return e;
  if ((e = bpa_pool_initial_refusal (p, p->initial_blocks, p->max_blocks)))
    return e;

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

/*
 * NAT64 (#485): turn a pool configuration whose inside is IPv6 into one the
 * rest of the CGNAT can run. The IPv6 prefixes and the subscriber length
 * become the subscriber index (bpa_set6_build); `inside` becomes the dense
 * pseudo set [0, count) that every block, capacity and worker rule reads
 * sub_idx through; the NAT64 prefix defaults to the Well-Known Prefix and is
 * held to RFC 6052. Nothing here touches a running pool.
 */
clib_error_t *
bpa_nat64_cfg_finish (bpa_pool_t *cfg, u8 sub_len)
{
  static const u8 wkp[16] = { 0x00, 0x64, 0xff, 0x9b };
  u32 base = 0, count;
  bpa_set6_error_t e;
  u32 i;

  if (cfg->n_inside6 == 0)
    return clib_error_return (0, "an IPv6 inside needs at least one prefix");
  if (sub_len == 0)
    return clib_error_return (0, "an IPv6 inside needs subscriber-length");
  if (cfg->nat64_len == 0)
    {
      memcpy (cfg->nat64_prefix, wkp, 16);
      cfg->nat64_len = 96;
    }
  if (!bpa_nat64_prefix_valid (cfg->nat64_prefix, cfg->nat64_len))
    return clib_error_return (0, "nat64-prefix: RFC 6052 takes /32, /40, /48, "
				 "/56, /64 or /96, bits 64-71 zero");

  e = bpa_set6_build (&cfg->inside6, (const u8 (*)[16]) cfg->inside6_addr,
		      cfg->inside6_len, cfg->n_inside6, sub_len);
  switch (e)
    {
    case BPA_SET6_OK:
      break;
    case BPA_SET6_CAPACITY:
      return clib_error_return (0, "more than 2^28 subscriber prefixes: "
				   "lengthen the inside or shorten "
				   "subscriber-length");
    case BPA_SET6_SHORTER:
      return clib_error_return (0, "an inside prefix is longer than "
				   "subscriber-length");
    case BPA_SET6_OVERLAP:
      return clib_error_return (0, "two inside IPv6 prefixes overlap");
    case BPA_SET6_HOST_BITS:
      return clib_error_return (0, "an inside IPv6 prefix has bits set past "
				   "its length");
    case BPA_SET6_BAD_SUBLEN:
      return clib_error_return (0, "subscriber-length is 32-128");
    default:
      return clib_error_return (0, "the IPv6 inside was refused");
    }

  /* The NAT64 prefix is where destinations are; a subscriber inside it would
   * have its replies built into its own prefix. */
  for (i = 0; i < cfg->n_inside6; i++)
    if (bpa_ip6_prefixes_overlap (cfg->inside6_addr[i], cfg->inside6_len[i],
				  cfg->nat64_prefix, cfg->nat64_len))
      return clib_error_return (0, "the NAT64 prefix overlaps an inside "
				   "prefix");

  count = cfg->inside6.count;
  cfg->is_nat64 = 1;
  /* No ALG runs on a NAT64 pool yet, whatever the default for IPv4 is: each
   * is its own phase, with its own lab (#485). Stated in both masks, so
   * bpa_pool_init() does not resolve the IPv4 defaults back in. */
  cfg->alg = 0;
  cfg->alg_set = ~0u;
  return bpa_addr_set_build (&cfg->inside, &base, &count, 1);
}

/*
 * What a NAT64 pool does not do, refused rather than accepted and inert
 * (#485's rule: every per-pool statement is supported with a test or refused
 * with a test). Returns the reason, or 0.
 */
const char *
bpa_nat64_unsupported (const bpa_pool_t *cfg)
{
  if (!cfg->is_nat64)
    return 0;
  /* Dynamic mode, with max-blocks, initial-allocation and block-stride,
   * since phase 3, and a deterministic pool's reserve since phase 4:
   * bpa_in2out's allocator on the subscriber's index. */
  /* NOT "yet": there is nothing for a bypass to mean. A NAT64 subscriber's
   * packet is addressed into the NAT64 prefix, and the only way it reaches
   * an IPv4 host is translated - so neither an untranslated source nor an
   * untranslated destination is a path that exists. */
  if (cfg->bypass_src.n_ranges || cfg->bypass_dst.n_ranges)
    return "a NAT64 pool takes no bypass: an IPv6 packet reaches IPv4 only "
	   "translated";
  if (cfg->alg)
    return "a NAT64 pool runs no ALG yet";
  return 0;
}

/*
 * A forward's subscriber, on a pool of either family (#485 phase 4).
 *
 * On a NAT64 pool the forward names a host - one /128 of a delegated prefix -
 * and the subscriber is the prefix's index, which is its pseudo inside
 * address: written into ip_priv, so every check and pin after this one is
 * the IPv4 code unchanged. 0, or why not: -1 the host is in no inside
 * prefix, -2 an IPv6 host on an IPv4 pool, -3 an IPv4 host on a NAT64 one.
 */
int
bpa_nat64_static_resolve (const bpa_pool_t *p, bpa_static_t *sf)
{
  u32 idx;

  if (!p->is_nat64)
    return sf->has_host6 ? -2 : 0;
  if (!sf->has_host6)
    return -3;
  if (bpa_set6_index_of (&p->inside6, sf->host6, &idx))
    return -1;
  sf->ip_priv = idx;
  return 0;
}
