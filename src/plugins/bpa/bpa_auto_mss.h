/*
 * bpa_auto_mss.h - the smallest MTU among the interfaces the CGNAT runs on.
 *
 * "tcp-mss auto" clamps to that minimum less an IPv4 and a TCP header, and
 * bpa.h says at length why it is the MINIMUM and not the egress interface's:
 * the node that clamps runs before the forwarding lookup, so which interface
 * the segment will leave by is not yet known.
 *
 * The number is one line of arithmetic. Keeping it up to date as interfaces
 * join and leave is not, and doing it the obvious way is what #287 is: the
 * minimum was recomputed by walking every interface on the box and asking the
 * feature arc four times per interface whether the CGNAT was on it. One
 * attach cost a walk of the whole box, so attaching N interfaces cost N walks
 * - 8.3 ms per attach at 10,000 interfaces, 83 seconds to attach them all,
 * against 42 us for `policer input` on the same feature arc.
 *
 * So the minimum is maintained here instead of recomputed. Adding an
 * interface can only lower a minimum, which is one compare, and that is the
 * operation the quadratic was made of. Removing one is the hard direction: a
 * minimum that leaves cannot be recovered from a running value, so this keeps
 * a COUNT of how many interfaces sit exactly at it. Every removal above the
 * minimum, and every removal from a minimum that more than one interface
 * shares, is a decrement. Only the departure of the last interface holding
 * the smallest MTU on the box looks at everything again, and what it looks at
 * is a flat array of u32 rather than the interface pool and the feature arc.
 *
 * Storage is the caller's, exactly as bpa_portmap_t's bitmap is: how many
 * interfaces a box has is not a fact this file can know, and an allocation
 * here would be one this file also had to free.
 */

#ifndef __included_bpa_auto_mss_h__
#define __included_bpa_auto_mss_h__

/* Standalone so that test/test_bpa_auto_mss.c can reach it without VPP. The
 * whole point of this file is that its bookkeeping is incremental, and
 * incremental bookkeeping is wrong in ways that only show up after a
 * particular ORDER of operations - which is a thing to enumerate in a unit
 * test, not to hope a lab stumbles into. Same reason bpa_ports.h and
 * bpa_mss.h carry this. */
#ifdef BPA_STANDALONE
#include "bpa_shim.h"
#else
#include <vlib/vlib.h>
#endif

#include "bpa_mss.h" /* BPA_MSS_OVERHEAD */

typedef struct
{
  /*
   * mtu[i] is the IPv4 MTU of sw_if_index i when the CGNAT is attached to it,
   * and 0 when it is not. Caller-owned, `n_slots` entries.
   *
   * Zero as the "not ours" sentinel and not a separate bitmap, because an
   * interface whose MTU reads as zero could never have contributed to a
   * minimum anyway: the walk this replaces skipped it with `if (mtu && ...)`.
   */
  u32 *mtu;
  u32 n_slots;

  u32 n_on;     /* slots that are not zero */
  u32 min;      /* the smallest value among them; ~0u when n_on is 0 */
  u32 n_at_min; /* how many slots hold exactly `min` */

  /*
   * Slots this structure has read since init.
   *
   * Here so that the cost of an attach can be ASSERTED. The defect in #287 is
   * a cost that grows with the size of the box, and the only honest way to
   * check that it has stopped growing is to count the work rather than to
   * time it: a wall-clock assertion on a machine that is also running a
   * release lab suite goes red for a reason that has nothing to do with this
   * code. A count is a fact about the algorithm and the same on any machine.
   */
  u64 n_slots_read;
} bpa_auto_mss_t;

/* Recomputes everything from the slots. The one operation here that is not
 * constant, and the reason the rest of this file exists is to reach it as
 * rarely as possible. */
static inline void
bpa_auto_mss_rescan (bpa_auto_mss_t *t)
{
  u32 i, min = ~0u, n_at_min = 0, n_on = 0;

  for (i = 0; i < t->n_slots; i++)
    {
      u32 m = t->mtu[i];

      t->n_slots_read++;
      if (!m)
	continue;
      n_on++;
      if (m < min)
	{
	  min = m;
	  n_at_min = 1;
	}
      else if (m == min)
	n_at_min++;
    }

  t->min = min;
  t->n_at_min = n_at_min;
  t->n_on = n_on;
}

static inline void
bpa_auto_mss_init (bpa_auto_mss_t *t, u32 *storage, u32 n_slots)
{
  t->mtu = storage;
  t->n_slots = n_slots;
  t->n_on = 0;
  /* ~0u and not 0: zero is the smallest possible MTU, so an empty set that
   * started at zero would claim to have the smallest one there is. */
  t->min = ~0u;
  t->n_at_min = 0;
  t->n_slots_read = 0;
}

/*
 * Re-points the structure at storage the caller has grown.
 *
 * Nothing derived is recomputed, and nothing needs to be: growing the array
 * only ever appends slots that are zero, and a zero slot is an interface the
 * CGNAT is not on. Separate from init because init forgets the set, and
 * growing the array must not.
 */
static inline void
bpa_auto_mss_storage (bpa_auto_mss_t *t, u32 *storage, u32 n_slots)
{
  t->mtu = storage;
  t->n_slots = n_slots;
}

/*
 * Records what one interface contributes: its IPv4 MTU while the CGNAT is on
 * it, 0 once it is not. The only mutator, so an attach, a detach and an MTU
 * that changed underneath are the same call with different arguments and
 * cannot drift apart.
 */
static inline void
bpa_auto_mss_set (bpa_auto_mss_t *t, u32 sw_if_index, u32 mtu)
{
  u32 old;

  if (sw_if_index >= t->n_slots)
    return;

  t->n_slots_read++;
  old = t->mtu[sw_if_index];
  if (old == mtu)
    return;

  t->mtu[sw_if_index] = mtu;
  if (old)
    t->n_on--;
  if (mtu)
    t->n_on++;

  /*
   * `n_at_min` is only ever a statement about the CURRENT minimum, never a
   * per-MTU census: once the minimum moves, whatever it said about the old
   * value is unreachable and cannot be consulted again. That is what keeps
   * this pair of branches from needing to agree about which minimum they are
   * talking about, and it is why the rescan below can be the only thing that
   * ever rebuilds the count.
   */
  if (old && old == t->min && --t->n_at_min == 0)
    {
      /* The last interface at the smallest MTU has gone, so the next smallest
       * is not in any counter here. The rescan takes the new value in
       * mtu[sw_if_index] with it, which is why this returns. */
      bpa_auto_mss_rescan (t);
      return;
    }

  if (mtu)
    {
      if (mtu < t->min)
	{
	  t->min = mtu;
	  t->n_at_min = 1;
	}
      else if (mtu == t->min)
	t->n_at_min++;
    }
}

/*
 * The MSS a pool set to "auto" clamps to. Zero means "do not clamp", for no
 * CGNAT interface and for an MTU smaller than the headers themselves:
 * inventing a number here would be a clamp nobody configured.
 */
static inline u16
bpa_auto_mss_value (const bpa_auto_mss_t *t)
{
  if (t->n_on == 0 || t->min <= BPA_MSS_OVERHEAD)
    return 0;
  return (u16) (t->min - BPA_MSS_OVERHEAD);
}

#endif /* __included_bpa_auto_mss_h__ */
