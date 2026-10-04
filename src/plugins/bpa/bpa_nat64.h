/*
 * bpa_nat64.h - the arithmetic of a NAT64 pool (#485, phase 1).
 *
 * Stateful NAT64 here is the CGNAT with an IPv6 inside: the outside is the
 * same IPv4 pool shared by port, and the obligation is the same - who held
 * (public address, port, instant). This header holds the parts of that which
 * are pure arithmetic, so they compile with the plain-cc harness as well as
 * with VPP and test/test_bpa_nat64.c holds them against ref/bpa_model.py:
 *
 *   - the subscriber index: inside IPv6 prefixes plus a subscriber prefix
 *     length, presented as the same dense index space bpa_pool.h gives IPv4
 *     addresses. The subscriber is the delegated PREFIX, never a /128: a host
 *     address inside a /64 changes with privacy extensions (RFC 8981), so a
 *     deterministic mapping keyed on it cannot exist, and the prefix is what
 *     RADIUS attributes to a person (Delegated-IPv6-Prefix, RFC 4818);
 *   - RFC 6052 address embedding and extraction at every prefix length;
 *   - RFC 6052 section 3.1: what the Well-Known Prefix may not carry;
 *   - the inside session key, which no longer fits 16 bytes.
 *
 * No datapath uses it yet: the translation (RFC 7915) is phase 2. Nothing in
 * the VTY accepts a NAT64 pool until then, so nothing is configured and inert.
 */
#ifndef __included_bpa_nat64_h__
#define __included_bpa_nat64_h__

#include "bpa_pool.h"

typedef unsigned __int128 bpa_u128;

/*
 * The capacity rule, unchanged from IPv4 (docs/architecture.md "The
 * mapping"): a pool's subscriber index lives in 28 bits. With IPv6 it becomes
 * a refusal an operator actually meets - 2001:db8::/32 in /56 subscribers is
 * 2^24 and fits, in /64 subscribers it is 2^32 and must be refused.
 */
#define BPA_NAT64_MAX_SUBSCRIBERS (1u << 28)
/* Inside prefixes one pool may declare. Generous: an operator's IPv6 is not
 * fragmented the way its IPv4 is, and the set is searched per packet. */
#define BPA_NAT64_MAX_PREFIXES 64

static_always_inline bpa_u128
bpa_ip6_to_u128 (const u8 a[16])
{
  bpa_u128 v = 0;
  for (int i = 0; i < 16; i++)
    v = (v << 8) | a[i];
  return v;
}

static_always_inline void
bpa_u128_to_ip6 (bpa_u128 v, u8 a[16])
{
  for (int i = 15; i >= 0; i--)
    {
      a[i] = (u8) v;
      v >>= 8;
    }
}

/* ------------------------------------------------------------------ */
/* RFC 6052                                                            */
/* ------------------------------------------------------------------ */

/* The six lengths RFC 6052 section 2.2 defines. */
static_always_inline int
bpa_nat64_len_ok (u8 len)
{
  return len == 32 || len == 40 || len == 48 || len == 56 || len == 64
	 || len == 96;
}

/*
 * A NAT64 prefix a pool may take: one of the six lengths, no bit set past
 * the length, and bits 64-71 - the u octet - zero. The u octet MUST be zero
 * (RFC 6052 2.2); in a /96 it is inside the prefix itself, so a /96 whose
 * ninth octet is not zero would make every address it builds non-conforming.
 */
static_always_inline int
bpa_nat64_prefix_valid (const u8 prefix[16], u8 len)
{
  if (!bpa_nat64_len_ok (len))
    return 0;
  for (int i = len / 8; i < 16; i++)
    if (prefix[i])
      return 0;
  return prefix[8] == 0;
}

/*
 * Embed an IPv4 address (host order) under a NAT64 prefix, RFC 6052 2.2.
 * Every length is a whole number of octets, so the IPv4 octets are copied
 * from octet len/8 on, stepping over octet 8 (the u octet, left zero); what
 * follows is the suffix, zero. Returns -1 on a length RFC 6052 does not
 * define.
 */
static_always_inline int
bpa_nat64_embed (const u8 prefix[16], u8 len, u32 ip4, u8 out[16])
{
  if (!bpa_nat64_len_ok (len))
    return -1;
  u8 v4[4] = { (u8) (ip4 >> 24), (u8) (ip4 >> 16), (u8) (ip4 >> 8),
	       (u8) ip4 };
  int pos = len / 8;
  for (int i = 0; i < 16; i++)
    out[i] = i < pos ? prefix[i] : 0;
  for (int k = 0; k < 4; k++)
    {
      if (pos == 8)
	pos++;
      out[pos++] = v4[k];
    }
  return 0;
}

/*
 * The IPv4 address an IPv4-embedded IPv6 address carries, RFC 6052 2.2: the
 * reverse of bpa_nat64_embed(), reading the same octets. The u octet is not
 * checked here - a reply's source is built by this box, and a destination
 * that matched the prefix is translated by its IPv4 octets.
 */
static_always_inline int
bpa_nat64_extract (const u8 addr[16], u8 len, u32 *ip4)
{
  if (!bpa_nat64_len_ok (len))
    return -1;
  int pos = len / 8;
  u32 v = 0;
  for (int k = 0; k < 4; k++)
    {
      if (pos == 8)
	pos++;
      v = (v << 8) | addr[pos++];
    }
  *ip4 = v;
  return 0;
}

/* Does `addr` fall under `prefix`/`len`? */
static_always_inline int
bpa_ip6_in_prefix (const u8 addr[16], const u8 prefix[16], u8 len)
{
  if (len == 0)
    return 1;
  bpa_u128 a = bpa_ip6_to_u128 (addr), p = bpa_ip6_to_u128 (prefix);
  u8 sh = 128 - len;
  return sh >= 128 ? 1 : (a >> sh) == (p >> sh);
}

/* 64:ff9b::/96, the Well-Known Prefix. */
static_always_inline int
bpa_nat64_is_wkp (const u8 prefix[16], u8 len)
{
  static const u8 wkp[16] = { 0x00, 0x64, 0xff, 0x9b };
  return len == 96 && memcmp (prefix, wkp, 16) == 0;
}

/*
 * RFC 6052 section 3.1: the Well-Known Prefix MUST NOT be used to represent
 * non-global IPv4 addresses. "Non-global" here is IANA's IPv4 Special-Purpose
 * Address Registry entries that are not globally reachable. A destination in
 * one of them under the WKP is refused, not translated.
 */
static_always_inline int
bpa_nat64_wkp_may_carry (u32 ip4)
{
  static const struct
  {
    u32 base;
    u8 len;
  } nonglobal[] = {
    { 0x00000000, 8 },	/* 0.0.0.0/8 "this network" */
    { 0x0a000000, 8 },	/* 10.0.0.0/8 private */
    { 0x64400000, 10 }, /* 100.64.0.0/10 shared address space */
    { 0x7f000000, 8 },	/* 127.0.0.0/8 loopback */
    { 0xa9fe0000, 16 }, /* 169.254.0.0/16 link local */
    { 0xac100000, 12 }, /* 172.16.0.0/12 private */
    { 0xc0000000, 24 }, /* 192.0.0.0/24 IETF protocol assignments */
    { 0xc0000200, 24 }, /* 192.0.2.0/24 documentation */
    { 0xc0a80000, 16 }, /* 192.168.0.0/16 private */
    { 0xc6120000, 15 }, /* 198.18.0.0/15 benchmarking */
    { 0xc6336400, 24 }, /* 198.51.100.0/24 documentation */
    { 0xcb007100, 24 }, /* 203.0.113.0/24 documentation */
    { 0xe0000000, 4 },	/* 224.0.0.0/4 multicast */
    { 0xf0000000, 4 },	/* 240.0.0.0/4 reserved, and the broadcast */
  };
  for (u32 i = 0; i < sizeof (nonglobal) / sizeof (nonglobal[0]); i++)
    {
      u32 mask = ~0u << (32 - nonglobal[i].len);
      if ((ip4 & mask) == nonglobal[i].base)
	return 0;
    }
  return 1;
}

/* ------------------------------------------------------------------ */
/* The subscriber index                                                */
/* ------------------------------------------------------------------ */

/*
 * One inside range, in units of SUBSCRIBER PREFIXES: `base` is the
 * subscriber number of its first prefix - the address shifted right by
 * 128 - sub_len - so a range is a run of consecutive subscribers exactly as
 * bpa_range_t is a run of consecutive addresses.
 */
typedef struct
{
  bpa_u128 base;
  u32 count;
  u32 first_idx;
} bpa_range6_t;

typedef struct
{
  bpa_range6_t ranges[BPA_NAT64_MAX_PREFIXES];
  u32 n_ranges;
  u32 count;  /* subscribers in the set */
  u8 sub_len; /* the subscriber prefix length, /32../128 */
} bpa_set6_t;

typedef enum
{
  BPA_SET6_OK = 0,
  BPA_SET6_EMPTY,	/* no prefix */
  BPA_SET6_TOO_MANY,	/* more than BPA_NAT64_MAX_PREFIXES */
  BPA_SET6_BAD_SUBLEN,	/* subscriber length outside 32..128 */
  BPA_SET6_SHORTER,	/* an inside prefix longer than a subscriber */
  BPA_SET6_HOST_BITS,	/* an inside prefix with bits past its length */
  BPA_SET6_OVERLAP,	/* two inside prefixes share an address */
  BPA_SET6_CAPACITY,	/* more than 2^28 subscribers */
} bpa_set6_error_t;

/*
 * Build the set from `n` inside prefixes. Sorted by base, adjacent ranges
 * merged - the same rules as the IPv4 set and as AddrSet in
 * ref/bpa_model.py, so the dense index is the same function in both.
 */
static inline bpa_set6_error_t
bpa_set6_build (bpa_set6_t *s, const u8 (*prefix)[16], const u8 *len, u32 n,
		u8 sub_len)
{
  s->n_ranges = s->count = 0;
  s->sub_len = sub_len;
  if (!n)
    return BPA_SET6_EMPTY;
  if (n > BPA_NAT64_MAX_PREFIXES)
    return BPA_SET6_TOO_MANY;
  if (sub_len < 32 || sub_len > 128)
    return BPA_SET6_BAD_SUBLEN;

  bpa_range6_t tmp[BPA_NAT64_MAX_PREFIXES];
  for (u32 i = 0; i < n; i++)
    {
      if (len[i] > sub_len)
	return BPA_SET6_SHORTER;
      bpa_u128 a = bpa_ip6_to_u128 (prefix[i]);
      u8 host = 128 - len[i];
      if (host && host < 128 && (a & (((bpa_u128) 1 << host) - 1)))
	return BPA_SET6_HOST_BITS;
      u8 sub_shift = 128 - sub_len;
      u8 width = sub_len - len[i]; /* subscribers per prefix = 2^width */
      if (width >= 29)
	return BPA_SET6_CAPACITY;
      tmp[i].base = sub_shift >= 128 ? 0 : a >> sub_shift;
      tmp[i].count = 1u << width;
    }
  /* insertion sort: n is small and this runs at commit time */
  for (u32 i = 1; i < n; i++)
    for (u32 j = i; j > 0 && tmp[j].base < tmp[j - 1].base; j--)
      {
	bpa_range6_t t = tmp[j];
	tmp[j] = tmp[j - 1];
	tmp[j - 1] = t;
      }
  u64 total = 0;
  for (u32 i = 0; i < n; i++)
    {
      if (i && tmp[i].base < tmp[i - 1].base + tmp[i - 1].count)
	return BPA_SET6_OVERLAP;
      if (s->n_ranges
	  && s->ranges[s->n_ranges - 1].base + s->ranges[s->n_ranges - 1].count
	       == tmp[i].base)
	s->ranges[s->n_ranges - 1].count += tmp[i].count;
      else
	{
	  s->ranges[s->n_ranges] = tmp[i];
	  s->ranges[s->n_ranges].first_idx = (u32) total;
	  s->n_ranges++;
	}
      total += tmp[i].count;
      if (total > BPA_NAT64_MAX_SUBSCRIBERS)
	return BPA_SET6_CAPACITY;
    }
  s->count = (u32) total;
  return BPA_SET6_OK;
}

/*
 * address -> the dense index of the subscriber prefix holding it, or -1 when
 * no inside prefix holds it. Any /128 inside a subscriber prefix gives that
 * prefix's index: that is the point of keying on the prefix.
 */
static_always_inline int
bpa_set6_index_of (const bpa_set6_t *s, const u8 addr[16], u32 *idx)
{
  u8 sh = 128 - s->sub_len;
  bpa_u128 v = sh >= 128 ? 0 : bpa_ip6_to_u128 (addr) >> sh;
  int lo = 0, hi = (int) s->n_ranges - 1;
  while (lo <= hi)
    {
      int mid = (lo + hi) / 2;
      const bpa_range6_t *r = &s->ranges[mid];
      if (v < r->base)
	hi = mid - 1;
      else if (v >= r->base + r->count)
	lo = mid + 1;
      else
	{
	  *idx = r->first_idx + (u32) (v - r->base);
	  return 0;
	}
    }
  return -1;
}

/* dense index -> the subscriber prefix (its first address), or -1. */
static_always_inline int
bpa_set6_prefix_of (const bpa_set6_t *s, u32 idx, u8 out[16])
{
  if (idx >= s->count)
    return -1;
  int lo = 0, hi = (int) s->n_ranges - 1;
  while (lo <= hi)
    {
      int mid = (lo + hi) / 2;
      const bpa_range6_t *r = &s->ranges[mid];
      if (idx < r->first_idx)
	hi = mid - 1;
      else if (idx >= r->first_idx + r->count)
	lo = mid + 1;
      else
	{
	  u8 sh = 128 - s->sub_len;
	  bpa_u128 v = r->base + (idx - r->first_idx);
	  bpa_u128_to_ip6 (sh >= 128 ? 0 : v << sh, out);
	  return 0;
	}
    }
  return -1;
}

/* ------------------------------------------------------------------ */
/* The inside session key                                              */
/* ------------------------------------------------------------------ */

/*
 * 128 bits of address, 16 of port, 8 of protocol and 32 of FIB are 184: the
 * inside key of a NAT64 session does not fit bihash_16_8's two words, so it
 * takes three (bihash_24_8). The address is the host's /128 - a session
 * belongs to one host; the subscriber prefix is what its BLOCK belongs to.
 * The outside key is the IPv4 one, unchanged.
 */
typedef struct
{
  u64 key[3];
  u64 value;
} bpa_kv_24_8_t;

static_always_inline void
bpa_in6_key (bpa_kv_24_8_t *kv, const u8 addr[16], u16 port, u8 proto,
	     u32 fib)
{
  bpa_u128 a = bpa_ip6_to_u128 (addr);
  kv->key[0] = (u64) (a >> 64);
  kv->key[1] = (u64) a;
  kv->key[2] = ((u64) fib << 32) | ((u64) port << 16) | ((u64) proto << 8);
}

static_always_inline void
bpa_in6_key_unpack (const bpa_kv_24_8_t *kv, u8 addr[16], u16 *port,
		    u8 *proto, u32 *fib)
{
  bpa_u128 a = ((bpa_u128) kv->key[0] << 64) | kv->key[1];
  bpa_u128_to_ip6 (a, addr);
  *port = (u16) (kv->key[2] >> 16);
  *proto = (u8) (kv->key[2] >> 8);
  *fib = (u32) (kv->key[2] >> 32);
}

#endif /* __included_bpa_nat64_h__ */
