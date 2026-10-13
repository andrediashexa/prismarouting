/*
 * test_bpa_pppoe_guard.c - which PPPoE frames reach VPP's pppoe plugin
 * (#597): only an access interface's, and none that takes a subscriber's
 * MAC from the access interface its session is on.
 *
 * WHY THESE. Each case is a frame lab/pppoe.sh sent before the guard and
 * watched the plugin take: a PADI from the upstream answered with a PADO;
 * sub1's MAC on VLAN 100 moving sub1's control plane to the other port. And
 * the cases that must still pass, because a guard that refused a CPE moving
 * to another port after it hung up, or a session's own data, would be a
 * worse defect than the one it closes.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "bpa_shim.h"
#include "../src/plugins/bpa/bpa_pppoe_guard.h"

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

#define LOG2 4 /* 16 sets of 4: small enough to fill */
#define HOLD 60
#define ACC_A 5	   /* host-acc0 */
#define ACC_B 9	   /* host-acc0.100 */
#define UPSTREAM 2 /* host-wan0 */
#define LCP 0xc021

static bpa_pppoe_held_t tbl[BPA_PPPOE_HOLD_WAYS << LOG2];

static bpa_pppoe_verdict_t
frame (u64 key, u32 rx, int is_access, int is_discovery, u8 code, u16 proto,
       u32 now)
{
  return bpa_pppoe_guard_verdict (tbl, LOG2, key, rx, is_access,
				  is_discovery, code, proto, now, HOLD);
}

int
main (void)
{
  const u8 m1[6] = { 0x02, 0xaa, 0, 0, 0, 0x01 };
  const u8 m2[6] = { 0x02, 0xaa, 0, 0, 0, 0x02 };
  const u8 zero[6] = { 0 };
  u64 k1 = bpa_pppoe_mac_key (m1), k2 = bpa_pppoe_mac_key (m2);

  printf ("=== a MAC is a key, and no MAC is the empty key ===\n");
  CHECK (k1 != k2 && k1 != 0, "two MACs, two keys: %llx %llx",
	 (unsigned long long) k1, (unsigned long long) k2);
  CHECK (bpa_pppoe_mac_key (zero) != 0, "00:00:00:00:00:00 is a key too");
  CHECK (bpa_pppoe_held_set (k1, LOG2) < (1u << LOG2), "a set in range");
  printf ("  [ ok ] keys\n");

  printf ("\n=== anything from an interface that is not access is refused ===\n");
  CHECK (frame (k1, UPSTREAM, 0, 1, BPA_PPPOE_PADI, 0, 100) ==
	   BPA_PPPOE_NOT_ACCESS,
	 "a PADI from the upstream");
  CHECK (frame (k1, UPSTREAM, 0, 0, 0, BPA_PPP_IP4, 100) ==
	   BPA_PPPOE_NOT_ACCESS,
	 "a session's IP frame from the upstream");
  CHECK (frame (k1, UPSTREAM, 0, 0, 0, LCP, 100) == BPA_PPPOE_NOT_ACCESS,
	 "an LCP frame from the upstream");
  CHECK (bpa_pppoe_held_find (tbl, LOG2, k1) == 0,
	 "and none of them holds the MAC anywhere");
  printf ("  [ ok ] not-access\n");

  printf ("\n=== a subscriber dials on A, and its MAC is A's ===\n");
  CHECK (frame (k1, ACC_A, 1, 1, BPA_PPPOE_PADI, 0, 100) == BPA_PPPOE_PASS,
	 "its PADI passes");
  CHECK (bpa_pppoe_held_find (tbl, LOG2, k1) == 0,
	 "discovery alone holds nothing: a CPE that never got a session "
	 "does not lock its MAC");
  CHECK (frame (k1, ACC_A, 1, 0, 0, LCP, 101) == BPA_PPPOE_PASS,
	 "its LCP passes");
  bpa_pppoe_held_t *e = bpa_pppoe_held_find (tbl, LOG2, k1);
  CHECK (e && e->sw_if_index == ACC_A && e->seen == 101,
	 "and now A holds it, seen at 101");
  printf ("  [ ok ] held by A\n");

  printf ("\n=== the same MAC on B, while A's session lives ===\n");
  CHECK (frame (k1, ACC_B, 1, 1, BPA_PPPOE_PADI, 0, 120) == BPA_PPPOE_HELD,
	 "B's PADI is refused");
  CHECK (frame (k1, ACC_B, 1, 0, 0, LCP, 121) == BPA_PPPOE_HELD,
	 "B's LCP, which would move A's control plane, is refused");
  CHECK (frame (k1, ACC_B, 1, 1, BPA_PPPOE_PADT, 0, 122) == BPA_PPPOE_HELD,
	 "B's PADT, which would end A's session, is refused");
  e = bpa_pppoe_held_find (tbl, LOG2, k1);
  CHECK (e && e->sw_if_index == ACC_A, "A still holds it");
  CHECK (frame (k1, ACC_B, 1, 0, 0, BPA_PPP_IP4, 123) == BPA_PPPOE_PASS,
	 "a DATA frame from B is not judged here (per packet; the "
	 "header says why)");
  CHECK (frame (k1, ACC_A, 1, 0, 0, LCP, 150) == BPA_PPPOE_PASS,
	 "A's next echo reply passes and refreshes");
  CHECK (frame (k1, ACC_B, 1, 1, BPA_PPPOE_PADI, 0, 150 + HOLD - 1) ==
	   BPA_PPPOE_HELD,
	 "B is still refused a second before A's hold runs out");
  printf ("  [ ok ] B refused while A lives\n");

  printf ("\n=== A's session goes quiet: after the hold, B may dial ===\n");
  CHECK (frame (k1, ACC_B, 1, 1, BPA_PPPOE_PADI, 0, 150 + HOLD) ==
	   BPA_PPPOE_PASS,
	 "B's PADI passes once A's hold is over");
  CHECK (frame (k1, ACC_B, 1, 0, 0, LCP, 150 + HOLD + 1) == BPA_PPPOE_PASS,
	 "B's LCP passes");
  e = bpa_pppoe_held_find (tbl, LOG2, k1);
  CHECK (e && e->sw_if_index == ACC_B, "and B holds the MAC now");
  CHECK (frame (k1, ACC_A, 1, 1, BPA_PPPOE_PADI, 0, 150 + HOLD + 2) ==
	   BPA_PPPOE_HELD,
	 "and A, back, is the one refused");
  printf ("  [ ok ] moved after the hold\n");

  printf ("\n=== a PADT where the session is lets the MAC go at once ===\n");
  CHECK (frame (k1, ACC_B, 1, 1, BPA_PPPOE_PADT, 0, 300) == BPA_PPPOE_PASS,
	 "B's PADT passes");
  CHECK (bpa_pppoe_held_find (tbl, LOG2, k1) == 0, "and frees the MAC");
  CHECK (frame (k1, ACC_A, 1, 1, BPA_PPPOE_PADI, 0, 301) == BPA_PPPOE_PASS,
	 "A dials a second later");
  printf ("  [ ok ] released by PADT\n");

  printf ("\n=== another MAC is another subscriber ===\n");
  CHECK (frame (k2, ACC_B, 1, 0, 0, LCP, 302) == BPA_PPPOE_PASS,
	 "02:aa::02 on B");
  CHECK (frame (k2, ACC_B, 1, 1, BPA_PPPOE_PADI, 0, 303) == BPA_PPPOE_PASS,
	 "its own PADI on B");
  printf ("  [ ok ] independent\n");

  printf ("\n=== a full set takes an expired way, then the oldest ===\n");
  memset (tbl, 0, sizeof (tbl));
  u64 keys[64];
  u32 set0 = bpa_pppoe_held_set (k1, LOG2), n = 0;
  for (u64 m = 1; n < 6 && m < 100000; m++)
    {
      u8 mac[6] = { 0x02, 0xbb, (u8) (m >> 16), (u8) (m >> 8), (u8) m, 0 };
      u64 k = bpa_pppoe_mac_key (mac);
      if (bpa_pppoe_held_set (k, LOG2) == set0)
	keys[n++] = k;
    }
  CHECK (n == 6, "six MACs of one set found: %u", n);
  for (u32 i = 0; i < 4; i++)
    CHECK (bpa_pppoe_held_note (tbl, LOG2, keys[i], ACC_A, 1000 + i, HOLD) ==
	     0,
	   "way %u taken without evicting", i);
  /* keys[0] has expired by 1000 + HOLD; the fifth takes its way. */
  CHECK (bpa_pppoe_held_note (tbl, LOG2, keys[4], ACC_A, 1000 + HOLD, HOLD) ==
	   0,
	 "an expired way is taken, nothing live evicted");
  CHECK (bpa_pppoe_held_find (tbl, LOG2, keys[0]) == 0,
	 "the expired one is gone");
  CHECK (bpa_pppoe_held_note (tbl, LOG2, keys[5], ACC_A, 1000 + HOLD, HOLD) ==
	   1,
	 "with every way live the oldest is evicted, and that is said");
  CHECK (bpa_pppoe_held_find (tbl, LOG2, keys[1]) == 0,
	 "the oldest live one (seen 1001) was the one taken");
  CHECK (bpa_pppoe_held_find (tbl, LOG2, keys[5]) != 0, "the new one is in");
  CHECK (bpa_pppoe_held_count (tbl, LOG2, 1000 + HOLD, HOLD) == 4,
	 "four held: %u", bpa_pppoe_held_count (tbl, LOG2, 1000 + HOLD, HOLD));
  CHECK (bpa_pppoe_held_count (tbl, LOG2, 3000, HOLD) == 0,
	 "none once every hold is over");
  printf ("  [ ok ] bounded\n");

  printf ("\n=== the node's hot-path test is the verdict's ===\n");
  CHECK (!bpa_pppoe_needs_table (0, BPA_PPP_IP4) &&
	   !bpa_pppoe_needs_table (0, BPA_PPP_IP6),
	 "IP data never takes the lock");
  CHECK (bpa_pppoe_needs_table (0, LCP) && bpa_pppoe_needs_table (1, 0),
	 "control and discovery do");
  printf ("  [ ok ] hot path\n");

  return bpa_test_verdict (checks, failures);
}
