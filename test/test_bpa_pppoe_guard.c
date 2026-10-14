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
				  is_discovery, code, proto, 0, now, HOLD);
}

/* A session's IPv6 control frame (#609): an RS, a DHCPv6 Solicit. */
static bpa_pppoe_verdict_t
frame6 (u64 key, u32 rx, int is_access, u32 now)
{
  return bpa_pppoe_guard_verdict (tbl, LOG2, key, rx, is_access, 0, 0,
				  BPA_PPP_IP6, 1, now, HOLD);
}

/* An IPv6 header to `dst` (16 bytes) carrying `nh`, and 4 bytes of the
 * next header: ICMPv6 type, or UDP source and destination ports. */
static u32
ip6 (u8 *p, const u8 *dst, u8 nh, u8 b0, u8 b1, u8 b2, u8 b3)
{
  memset (p, 0, BPA_IP6_HEADER + 8);
  p[0] = 0x60;
  p[6] = nh;
  p[7] = 255;
  p[8] = 0xfe;
  p[9] = 0x80;
  memcpy (p + 24, dst, 16);
  p[BPA_IP6_HEADER] = b0;
  p[BPA_IP6_HEADER + 1] = b1;
  p[BPA_IP6_HEADER + 2] = b2;
  p[BPA_IP6_HEADER + 3] = b3;
  return BPA_IP6_HEADER + 8;
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
  CHECK (!bpa_pppoe_needs_table (0, BPA_PPP_IP4, 0) &&
	   !bpa_pppoe_needs_table (0, BPA_PPP_IP6, 0),
	 "IP data never takes the lock");
  CHECK (bpa_pppoe_needs_table (0, LCP, 0) && bpa_pppoe_needs_table (1, 0, 0),
	 "control and discovery do");
  CHECK (bpa_pppoe_needs_table (0, BPA_PPP_IP6, 1),
	 "and an IPv6 control frame does: it reaches the control plane, "
	 "whose dispatch node learns its MAC's port");
  printf ("  [ ok ] hot path\n");

  printf ("\n=== IPv6 a session's control plane answers (#609) ===\n");
  {
    const u8 allrouters[16] = { 0xff, 0x02, [15] = 0x02 };
    const u8 alldhcp[16] = { 0xff, 0x02, [13] = 0x01, [14] = 0x00,
			     [15] = 0x02 };
    const u8 ll[16] = { 0xfe, 0x80, [15] = 0x01 };
    const u8 global[16] = { 0x20, 0x01, 0x0d, 0xb8, [15] = 0x01 };
    u8 p[BPA_IP6_HEADER + 8];
    u32 n;

    n = ip6 (p, allrouters, 58, BPA_ICMP6_ROUTER_SOLICIT, 0, 0, 0);
    CHECK (bpa_pppoe_ip6_is_control (p, n), "an RS to ff02::2");
    n = ip6 (p, ll, 58, BPA_ICMP6_ROUTER_SOLICIT, 0, 0, 0);
    CHECK (bpa_pppoe_ip6_is_control (p, n), "an RS to the BNG's fe80::1");
    n = ip6 (p, alldhcp, 17, 0x02, 0x22, 0x02, 0x23);
    CHECK (bpa_pppoe_ip6_is_control (p, n),
	   "a DHCPv6 Solicit, 546 to 547, to ff02::1:2");
    n = ip6 (p, ll, 17, 0x02, 0x22, 0x02, 0x23);
    CHECK (bpa_pppoe_ip6_is_control (p, n), "a DHCPv6 Renew to fe80::1");
    n = ip6 (p, global, 17, 0x02, 0x22, 0x02, 0x23);
    CHECK (!bpa_pppoe_ip6_is_control (p, n),
	   "UDP 547 to a global address is the subscriber's traffic "
	   "(a relay behind the CPE), forwarded");
    n = ip6 (p, global, 58, BPA_ICMP6_ROUTER_SOLICIT, 0, 0, 0);
    CHECK (!bpa_pppoe_ip6_is_control (p, n), "an RS to a global address");
    n = ip6 (p, allrouters, 58, 128, 0, 0, 0);
    CHECK (!bpa_pppoe_ip6_is_control (p, n),
	   "an echo request is not the control plane's");
    n = ip6 (p, allrouters, 0, 0, 0, 0, 0);
    CHECK (!bpa_pppoe_ip6_is_control (p, n),
	   "an extension header first (MLD's hop-by-hop) is not read past");
    n = ip6 (p, alldhcp, 6, 0x02, 0x22, 0x02, 0x23);
    CHECK (!bpa_pppoe_ip6_is_control (p, n), "TCP to port 547 is not DHCPv6");
    n = ip6 (p, allrouters, 58, BPA_ICMP6_ROUTER_SOLICIT, 0, 0, 0);
    CHECK (!bpa_pppoe_ip6_is_control (p, BPA_IP6_HEADER + 3),
	   "a frame too short to hold the type is not read past its end");
    p[0] = 0x45;
    CHECK (!bpa_pppoe_ip6_is_control (p, n), "an IPv4 header is not IPv6");
  }
  printf ("  [ ok ] the IPv6 to punt\n");

  printf ("\n=== an IPv6 control frame is judged as a control frame ===\n");
  memset (tbl, 0, sizeof (tbl));
  CHECK (frame6 (k1, UPSTREAM, 0, 400) == BPA_PPPOE_NOT_ACCESS,
	 "an RS from the upstream is refused");
  CHECK (frame (k1, ACC_A, 1, 0, 0, LCP, 401) == BPA_PPPOE_PASS,
	 "sub1's LCP on A");
  CHECK (frame6 (k1, ACC_A, 1, 402) == BPA_PPPOE_PUNT,
	 "sub1's RS on A goes to the control plane");
  CHECK (frame6 (k1, ACC_B, 1, 403) == BPA_PPPOE_HELD,
	 "sub1's MAC sending an RS from B is refused: it would move sub1's "
	 "port in the plugin's link table, as an LCP frame would");
  e = bpa_pppoe_held_find (tbl, LOG2, k1);
  CHECK (e && e->sw_if_index == ACC_A && e->seen == 402,
	 "A still holds it, refreshed by the RS");
  CHECK (frame (k1, ACC_B, 1, 0, 0, BPA_PPP_IP6, 404) == BPA_PPPOE_PASS,
	 "IPv6 data is still never judged against the table");
  printf ("  [ ok ] IPv6 control\n");

  printf ("\n=== the control plane's own IPv6, off its TAP (#609) ===\n");
  {
    u8 f[64];
    u32 at = 0;
    memset (f, 0, sizeof (f));
    f[12] = 0x88;
    f[13] = 0x64;
    f[14] = 0x11;
    f[20] = 0x00;
    f[21] = 0x57;
    CHECK (bpa_pppoe_cp_is_ip6 (f, 64, &at) && at == 14,
	   "an untagged PPP-IPv6 session frame: the PPPoE header at 14, %u",
	   at);
    f[21] = 0x21;
    CHECK (!bpa_pppoe_cp_is_ip6 (f, 64, &at), "PPP-IPv4 is not taken");
    f[21] = 0x57;
    f[13] = 0x63;
    CHECK (!bpa_pppoe_cp_is_ip6 (f, 64, &at), "nor a discovery frame");
    memset (f, 0, sizeof (f));
    f[12] = 0x81;
    f[13] = 0x00;
    f[15] = 100;
    f[16] = 0x88;
    f[17] = 0x64;
    f[18] = 0x11;
    f[24] = 0x00;
    f[25] = 0x57;
    CHECK (bpa_pppoe_cp_is_ip6 (f, 64, &at) && at == 18,
	   "on VLAN 100 (pppoe-cp.100): the PPPoE header at 18, %u", at);
    CHECK (!bpa_pppoe_cp_is_ip6 (f, 25, &at),
	   "a frame cut before its PPP protocol is not read past its end");
    f[12] = 0x86;
    f[13] = 0xdd;
    CHECK (!bpa_pppoe_cp_is_ip6 (f, 64, &at),
	   "the TAP's own IPv6 (the kernel's RS on pppoe-cp) is not taken");
  }
  printf ("  [ ok ] from the control plane\n");

  return bpa_test_verdict (checks, failures);
}
