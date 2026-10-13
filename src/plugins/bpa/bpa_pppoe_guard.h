/*
 * bpa_pppoe_guard.h - which PPPoE frames reach VPP's pppoe plugin (#597):
 * only those that arrive on an access interface, and none that would take a
 * subscriber's MAC away from the access interface its session is on.
 *
 * WHAT VPP 26.06 DOES WITHOUT THIS, measured in lab/pppoe.sh on 2026-10-13
 * before the guard (docs/bng.md 0.3 items 7 and 9):
 *
 *   * pppoe_init() registers both PPPoE ethertypes box-wide, so a PADI that
 *     arrives on ANY interface is punted to the control plane and answered:
 *     5 PADIs from host-lan0, a CGNAT inside port, drew 5 PADOs.
 *   * pppoe-cp-dispatch LEARNS client MAC -> interface from every discovery
 *     frame and every session-stage control frame (pppoe_learn_process,
 *     "mac move"), keyed by the MAC alone, and a session's way DOWN - its
 *     interface and VLAN tag - is copied from that table when the session
 *     is added (pppoe.c, vnet_pppoe_add_del_session). So a subscriber that
 *     dials while its MAC is being heard on another access interface gets
 *     its downloads sent there: sub1 redialling while a CPE with its MAC
 *     sent PADIs on VLAN 100 had its session's frames sent out of
 *     host-acc0.100, to that CPE. Two CPEs with one MAC on two access ports
 *     also each held a session at once.
 *   * The control plane's own frames are NOT crossed between VLANs of the
 *     card (measured: sub1's PADT reached sub1 with its MAC heard on VLAN
 *     100): the plugin hands them to interface-output whole, and the tag is
 *     the one accel-ppp sent them with, on pppoe-cp.<vlan>.
 *
 * WHAT THIS DECIDES, per frame, before the pppoe plugin sees it:
 *
 *   1. a frame of either ethertype from an interface that is not an access
 *      interface is dropped, counted;
 *   2. a discovery frame or a session-stage CONTROL frame whose MAC is HELD
 *      by another access interface is dropped, counted. A MAC is held by the
 *      access interface its session's control frames last came from, for
 *      `hold` seconds after the last one: a live session answers accel-ppp's
 *      LCP echo every lcp-echo-interval (20 s, pppoe.accel_conf), and
 *      accel-ppp ends one that misses lcp-echo-failure (3) of them - so 60 s
 *      is as long as the control plane itself believes in the session. A
 *      PADT from the holding interface lets the MAC go at once;
 *   3. everything else passes: a session's IP frames are checked for the
 *      access interface only (1), never against the table - that check is
 *      per packet, on every subscriber's traffic.
 *
 * A CPE that goes without a PADT - pppd killed sends none, measured - frees
 * its MAC `hold` seconds after its last control frame.
 *
 * WHAT IT DOES NOT CLOSE, and why: a session's DATA frames are matched by
 * the plugin on (MAC, session id) with no interface (pppoe_decap.c), so a
 * frame with a held MAC and the right session id, sent from another ACCESS
 * interface, is still decapsulated as that session's. Closing it needs the
 * interface in the plugin's key, which is a change to VPP.
 *
 * THE TABLE IS BOUNDED, by construction: 4-way sets, a fixed number of them,
 * a new MAC taking an empty or expired way and else the oldest. A flood of
 * invented MACs from an access port can evict a real one - which loses that
 * subscriber's protection, not the box - and never grows anything. VPP's own
 * link table is a bihash that does grow with every MAC heard; this one is
 * not allowed to be the second thing that does.
 *
 * WHY A HEADER. bpa_pppoe_guard.c is the VPP-facing half - the nodes, the
 * hook, the CLI - and is excused from unit tests for that; what it decides
 * is here, and test/test_bpa_pppoe_guard.c drives it with calls alone.
 */

#ifndef __included_bpa_pppoe_guard_h__
#define __included_bpa_pppoe_guard_h__

/* PPPoE discovery codes (RFC 2516) the guard reads. */
#define BPA_PPPOE_PADI 0x09
#define BPA_PPPOE_PADT 0xa7
/* The two PPP protocols the plugin decapsulates as data (pppoe_decap.c);
 * every other one is a control frame it punts. */
#define BPA_PPP_IP4 0x0021
#define BPA_PPP_IP6 0x0057

#define BPA_PPPOE_HOLD_WAYS 4
/* 16384 sets of 4: 65,536 MACs in 1 MiB, past what one box terminates. */
#define BPA_PPPOE_HOLD_SETS_LOG2 14
#define BPA_PPPOE_HOLD_DEFAULT	 60

typedef enum
{
  BPA_PPPOE_PASS = 0,
  BPA_PPPOE_NOT_ACCESS,
  BPA_PPPOE_HELD,
} bpa_pppoe_verdict_t;

/* One way of a set. `key` 0 is an empty way. */
typedef struct
{
  u64 key;	   /* bpa_pppoe_mac_key() */
  u32 sw_if_index; /* the access interface that holds it */
  u32 seen;	   /* when its last control frame came, seconds */
} bpa_pppoe_held_t;

/* A MAC as a key that is never 0: the 48 bits and a marker bit above them,
 * so the all-zero MAC is a key like any other and 0 stays "empty". */
static_always_inline u64
bpa_pppoe_mac_key (const u8 *mac)
{
  return (1ULL << 48) | ((u64) mac[0] << 40) | ((u64) mac[1] << 32) |
	 ((u64) mac[2] << 24) | ((u64) mac[3] << 16) | ((u64) mac[4] << 8) |
	 (u64) mac[5];
}

/* The set `key` belongs to, of 1 << sets_log2. */
static_always_inline u32
bpa_pppoe_held_set (u64 key, u32 sets_log2)
{
  return (u32) ((key * 0x9E3779B97F4A7C15ULL) >> (64 - sets_log2));
}

/* The way holding `key` in `tbl`, or 0. */
static_always_inline bpa_pppoe_held_t *
bpa_pppoe_held_find (bpa_pppoe_held_t *tbl, u32 sets_log2, u64 key)
{
  bpa_pppoe_held_t *set =
    tbl + (u64) bpa_pppoe_held_set (key, sets_log2) * BPA_PPPOE_HOLD_WAYS;
  for (int i = 0; i < BPA_PPPOE_HOLD_WAYS; i++)
    if (set[i].key == key)
      return &set[i];
  return 0;
}

/*
 * `key` heard on `sw_if_index` at `now`: its way refreshed, or one taken -
 * an empty way first, then one whose hold has run out, then the oldest.
 * Returns 1 when a MAC still held was evicted to make room.
 */
static_always_inline int
bpa_pppoe_held_note (bpa_pppoe_held_t *tbl, u32 sets_log2, u64 key,
		     u32 sw_if_index, u32 now, u32 hold)
{
  bpa_pppoe_held_t *set =
    tbl + (u64) bpa_pppoe_held_set (key, sets_log2) * BPA_PPPOE_HOLD_WAYS;
  bpa_pppoe_held_t *take = 0;
  int i;

  for (i = 0; i < BPA_PPPOE_HOLD_WAYS; i++)
    if (set[i].key == key)
      {
	set[i].sw_if_index = sw_if_index;
	set[i].seen = now;
	return 0;
      }
  for (i = 0; i < BPA_PPPOE_HOLD_WAYS && !take; i++)
    if (set[i].key == 0)
      take = &set[i];
  for (i = 0; i < BPA_PPPOE_HOLD_WAYS && !take; i++)
    if (now - set[i].seen >= hold)
      take = &set[i];
  int evicted = 0;
  if (!take)
    {
      take = &set[0];
      for (i = 1; i < BPA_PPPOE_HOLD_WAYS; i++)
	if (now - set[i].seen > now - take->seen)
	  take = &set[i];
      evicted = 1;
    }
  take->key = key;
  take->sw_if_index = sw_if_index;
  take->seen = now;
  return evicted;
}

/*
 * The verdict on one PPPoE frame, and the table updated by it.
 *
 *   is_access    the frame came in on an access interface
 *   is_discovery ethertype 0x8863 (else 0x8864, a session-stage frame)
 *   code         the PPPoE code (discovery)
 *   ppp_proto    the PPP protocol (session stage)
 *
 * The caller holds whatever lock the table needs: only a discovery frame
 * or a control frame reaches the table, never a data frame.
 */
static_always_inline bpa_pppoe_verdict_t
bpa_pppoe_guard_verdict (bpa_pppoe_held_t *tbl, u32 sets_log2, u64 key,
			 u32 rx_sw_if_index, int is_access, int is_discovery,
			 u8 code, u16 ppp_proto, u32 now, u32 hold)
{
  bpa_pppoe_held_t *e;

  if (!is_access)
    return BPA_PPPOE_NOT_ACCESS;
  if (!is_discovery && (ppp_proto == BPA_PPP_IP4 || ppp_proto == BPA_PPP_IP6))
    return BPA_PPPOE_PASS;
  e = bpa_pppoe_held_find (tbl, sets_log2, key);
  if (e && e->sw_if_index != rx_sw_if_index && now - e->seen < hold)
    return BPA_PPPOE_HELD;
  if (is_discovery)
    {
      /* The subscriber hung up where its session was: the MAC is free. */
      if (code == BPA_PPPOE_PADT && e && e->sw_if_index == rx_sw_if_index)
	e->key = 0;
      return BPA_PPPOE_PASS;
    }
  bpa_pppoe_held_note (tbl, sets_log2, key, rx_sw_if_index, now, hold);
  return BPA_PPPOE_PASS;
}

/* Whether a data or control frame needs the table: the hot path's test,
 * kept here so the unit test holds the node to the same rule. */
static_always_inline int
bpa_pppoe_needs_table (int is_discovery, u16 ppp_proto)
{
  return is_discovery ||
	 (ppp_proto != BPA_PPP_IP4 && ppp_proto != BPA_PPP_IP6);
}

/* MACs still held at `now`, of the table's 1 << sets_log2 sets. */
static_always_inline u32
bpa_pppoe_held_count (bpa_pppoe_held_t *tbl, u32 sets_log2, u32 now,
		      u32 hold)
{
  u32 n = 0;
  u64 ways = (u64) BPA_PPPOE_HOLD_WAYS << sets_log2;
  for (u64 i = 0; i < ways; i++)
    if (tbl[i].key && now - tbl[i].seen < hold)
      n++;
  return n;
}

#endif /* __included_bpa_pppoe_guard_h__ */
