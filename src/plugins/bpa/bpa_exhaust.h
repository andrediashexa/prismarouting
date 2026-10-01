/*
 * bpa_exhaust.h - one exhaustion record per subscriber per interval (#549).
 *
 * natEvent 12 was queued for every packet a subscriber with no block sent. On
 * the #475 bench a fill run - 1020 subscribers x 2100 ports into 248 blocks -
 * emitted 1,621,448 IPFIX records, one per refused packet of the 772
 * subscribers left without a block; 71,107 of the packets carrying them were
 * dropped at ens20's transmit, and the same flood into the syslog stream grew
 * its spill to 573 MB (#547). RFC 8158 asks for the event, not for one per
 * packet: an operator wants to know who ran out, and when, not how many
 * packets that subscriber tried afterwards.
 *
 * So a small table per worker, four ways to a set on the subscriber's
 * address: the first exhaustion of an address is reported, and the next only
 * once BPA_EXHAUST_IVL has passed. Four ways and not one: in a direct-mapped
 * table two subscribers in one slot evict each other on every packet, and
 * each of their packets is reported again - the flood this exists to stop.
 * A set with more than four live subscribers evicts its oldest entry, so the
 * worst case is a record early, never one held back past the interval.
 */
#ifndef __included_bpa_exhaust_h__
#define __included_bpa_exhaust_h__

#define BPA_EXHAUST_SETS 4096 /* a power of two */
#define BPA_EXHAUST_WAYS 4
#define BPA_EXHAUST_IVL	 10.0 /* seconds */

typedef struct bpa_exhaust_t_
{
  u32 ip[BPA_EXHAUST_SETS][BPA_EXHAUST_WAYS];
  f64 at[BPA_EXHAUST_SETS][BPA_EXHAUST_WAYS];
  u64 suppressed;
} bpa_exhaust_t;

/* 1 when `ip`'s exhaustion at `now` is to be reported, 0 when it repeats one
 * reported less than BPA_EXHAUST_IVL ago (counted in `suppressed`). */
static_always_inline int
bpa_exhaust_report (bpa_exhaust_t *x, u32 ip, f64 now)
{
  u32 h = ((ip * 2654435761u) >> 20) & (BPA_EXHAUST_SETS - 1);
  u32 w, old = 0;

  for (w = 0; w < BPA_EXHAUST_WAYS; w++)
    {
      if (x->at[h][w] != 0 && x->ip[h][w] == ip)
	{
	  if (now - x->at[h][w] < BPA_EXHAUST_IVL)
	    {
	      x->suppressed++;
	      return 0;
	    }
	  x->at[h][w] = now;
	  return 1;
	}
      if (x->at[h][w] < x->at[h][old])
	old = w;
    }
  x->ip[h][old] = ip;
  x->at[h][old] = now;
  return 1;
}

#endif /* __included_bpa_exhaust_h__ */
