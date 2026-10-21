/*
 * bpa_demand.h - which subscribers a worker's slice has refused lately (#611).
 *
 * THE SIGNAL `bpa rebalance` LENDS ON, and the one it replaced was wrong four
 * ways, each measured on #475's bench (`lab/bench3vm.sh uneven`, 3 workers,
 * 160 subscribers 80/56/24 on 40-block slices). The demand was
 * `n_exhausted - n_exhausted_seen`, and:
 *
 *   1. it counted refused PACKETS - one refused connect is three SYNs;
 *   2. it was forgiven only by a loan, so a refusal nobody answered counted
 *      for ever (`demand 1835/300` nine minutes in), and a lender that had
 *      refused its own subscribers once never lent again;
 *   3. the baseline did not move while the switch was off, so `bpa rebalance
 *      enable` on a running box started from every refusal since boot
 *      (`demand 1048/242` on the first tick);
 *   4. a borrower with that standing demand gave a loan back after five idle
 *      ticks and borrowed it again at once: 192 of 211 loans went home unused.
 *
 * WHAT IT IS NOW: the DISTINCT subscribers this slice refused in the last
 * BPA_DEMAND_WINDOW ticks that still hold no block. A set, not a counter -
 * so a SYN and its two retransmits are one subscriber; aged, so a refusal
 * stops counting five seconds after the subscriber stopped asking; and
 * emptied every tick while the switch is off, so enabling it starts from the
 * last second rather than from boot.
 *
 * TWO WRITERS AND NO LOCK. The worker that owns the slice writes it on the
 * packet it refuses (bpa_demand_note, below - the refusal branch only, never
 * the packet that is served); the rebalance tick reads, ages and rebuilds it
 * under the barrier bpa_expire_process already takes, when no worker runs.
 * It lives inside bpa_dyn_t, which is per worker and per pool, so the two
 * never share a line with another worker.
 */
#ifndef __included_bpa_demand_h__
#define __included_bpa_demand_h__

/*
 * THE SIZE OF THE SET, and why a bounded one is not an approximation that
 * matters. A tick lends at most BPA_REBALANCE_MAX_MOVES moves of at most
 * BPA_REBALANCE_MAX_MOVES blocks each - 64 blocks - so a demand above 64 per
 * slice changes nothing the tick does. The table is 128 slots kept at most
 * half full, which is what lets an insert stop after a short probe: open
 * addressing at load 0.5 averages 1.5 probes for a hit and 2.5 for a miss.
 *
 * 128 x (4 + 1 + 1) bytes and two counters: 776 bytes per worker per dynamic
 * pool, against the slice's own arrays at about 21 bytes a BLOCK.
 */
#define BPA_DEMAND_SLOTS 128
#define BPA_DEMAND_MAX	 64
#define BPA_DEMAND_PROBES 16

/*
 * HOW LONG A REFUSAL COUNTS: five ticks after the subscriber's last refused
 * packet. The same five seconds BPA_LOAN_GRACE_TICKS gives a loan, and for
 * the same reason - a TCP client retransmits at 1 s and 3 s, so a subscriber
 * still trying is refused again inside the window and stays in it, and one
 * that has gone quiet (the bench's clients back off for up to 32 s) stops
 * holding blocks away from the lender within five seconds.
 */
#define BPA_DEMAND_WINDOW 5

typedef struct
{
  u32 key[BPA_DEMAND_SLOTS]; /* sub_idx + 1; 0 is an empty slot */
  u8 age[BPA_DEMAND_SLOTS];  /* ticks since this subscriber was refused */
  /*
   * Refused an EXTENSION rather than its first block. Such a subscriber
   * holds a block already, so "it holds one now" is not "it was served", and
   * the tick lets the entry age out instead.
   */
  u8 extra[BPA_DEMAND_SLOTS];
  u32 n;	  /* occupied slots */
  u32 n_overflow; /* refusals since the last tick that found no slot */
} bpa_demand_t;

/* Fibonacci hashing: the top bits of a multiply by 2^32/phi, which spreads
   consecutive subscriber indices - the bench's, and any pool's - across the
   table instead of into one run. */
static_always_inline u32
bpa_demand_slot (u32 key)
{
  return (key * 2654435769u) >> 25; /* 32 - log2 (BPA_DEMAND_SLOTS) */
}

/*
 * A refusal, on the worker that refused it.
 *
 * ON THE REFUSAL BRANCH ONLY: bpa_dyn_acquire() and bpa_dyn_acquire_extra()
 * call this where they already counted n_exhausted, so a packet that is
 * served pays nothing. A refused one pays a multiply and, at the load the
 * table is kept at, one or two compares - measured in `make bench-dyn`, the
 * numbers in docs/architecture.md.
 */
static_always_inline void
bpa_demand_note (bpa_demand_t *dm, u32 sub_idx, u8 extra)
{
  u32 key = sub_idx + 1, s = bpa_demand_slot (key), i;

  for (i = 0; i < BPA_DEMAND_PROBES; i++, s = (s + 1) & (BPA_DEMAND_SLOTS - 1))
    {
      if (dm->key[s] == key)
	{
	  dm->age[s] = 0;
	  dm->extra[s] |= extra;
	  return;
	}
      if (dm->key[s] == 0)
	{
	  if (dm->n >= BPA_DEMAND_MAX)
	    break;
	  dm->key[s] = key;
	  dm->age[s] = 0;
	  dm->extra[s] = extra;
	  dm->n++;
	  return;
	}
    }
  dm->n_overflow++;
}

/**
 * The tick: how many subscribers this slice wants a block for.
 *
 * Drops every entry `holds` says now holds a block (a first-block refusal
 * that has since been served) and every entry older than the window, ages
 * the rest, and rebuilds the table so that no deletion leaves a hole in a
 * probe run. A refusal that found no slot adds one: the set was full, so at
 * least one more subscriber was refused, and the tick cannot lend more than
 * BPA_DEMAND_MAX anyway.
 */
u32 bpa_demand_tick (bpa_demand_t *dm, int (*holds) (void *, u32),
		     void *ctx);

/** Empties the set: the tick with the switch off, so nothing accumulates. */
void bpa_demand_forget (bpa_demand_t *dm);

#endif /* __included_bpa_demand_h__ */
