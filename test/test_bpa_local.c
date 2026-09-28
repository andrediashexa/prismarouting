/*
 * test_bpa_local.c - the box's own addresses as a set (bpa_local.h, #489).
 *
 * THE PROPERTY: every address the set was built from is in it, and an
 * address it was not built from is not - over enough addresses to fill the
 * table to its limit and make probes collide and wrap. A set that drops a
 * member sends a subscriber's packet for the box through the CGNAT, which
 * is #470 back; one that holds a stranger only costs the FIB lookup it was
 * written to avoid, so both are checked, the first harder.
 *
 * Mutation shown red: bpa_local_build() sized to the count and not twice it
 * (a full table, where a probe for an absent address never ends).
 */
#include <stdio.h>
#include <string.h>
#include "bpa_shim.h"
#include "bpa_vec_shim.h"
#include "../src/plugins/bpa/bpa_local.h"

static int failures = 0;
static unsigned checks = 0;

#define CHECK(cond, ...)                                                      \
  do                                                                          \
    {                                                                         \
      checks++;                                                               \
      if (!(cond))                                                            \
	{                                                                     \
	  printf ("  FAIL: ");                                               \
	  printf (__VA_ARGS__);                                               \
	  printf ("\n");                                                      \
	  failures++;                                                         \
	}                                                                     \
    }                                                                         \
  while (0)

/* An address as the packet carries it: network order, a.b.c.d. */
static u32
ip (u32 a, u32 b, u32 c, u32 d)
{
  u8 o[4] = { (u8) a, (u8) b, (u8) c, (u8) d };
  u32 v;
  memcpy (&v, o, 4);
  return v;
}

int
main (void)
{
  printf ("=== an empty box holds nothing ===\n");
  {
    u32 *none = 0, *slots;

    CHECK (!bpa_local_has (0, ip (100, 64, 0, 1)),
	   "a set that was never built held an address");
    slots = bpa_local_build (none);
    CHECK (vec_len (slots) == 16, "an empty set is %u slots, not 16",
	   vec_len (slots));
    CHECK (!bpa_local_has (slots, ip (100, 64, 0, 1)),
	   "an empty set held an address");
    CHECK (!bpa_local_has (slots, 0), "0.0.0.0 was taken for a member");
    vec_free (slots);
    CHECK (bpa_local_slot (ip (100, 64, 0, 1), 15) <= 15
	     && bpa_local_slot (ip (100, 64, 0, 1), 15)
		  == bpa_local_slot (ip (100, 64, 0, 1), 15),
	   "a slot fell outside the table, or moved between two asks");
    printf ("  nothing is the box's before it has an address\n");
  }

  printf ("\n=== a box's usual handful ===\n");
  {
    u32 *addrs = 0, *slots;

    vec_add1 (addrs, ip (100, 64, 0, 1));    /* inside gateway */
    vec_add1 (addrs, ip (198, 51, 100, 2));  /* uplink */
    vec_add1 (addrs, ip (10, 95, 210, 241)); /* management */
    vec_add1 (addrs, ip (128, 0, 0, 1));     /* backplane, other VRF */
    vec_add1 (addrs, ip (100, 64, 0, 1));    /* same address twice */
    vec_add1 (addrs, 0);		     /* never a member */
    slots = bpa_local_build (addrs);
    CHECK (bpa_local_has (slots, ip (100, 64, 0, 1))
	     && bpa_local_has (slots, ip (198, 51, 100, 2))
	     && bpa_local_has (slots, ip (10, 95, 210, 241))
	     && bpa_local_has (slots, ip (128, 0, 0, 1)),
	   "an address of the box is missing from its set");
    CHECK (!bpa_local_has (slots, ip (100, 64, 0, 2))
	     && !bpa_local_has (slots, ip (8, 8, 8, 8))
	     && !bpa_local_has (slots, ip (198, 51, 100, 1)),
	   "an address that is not the box's was taken for one");
    vec_free (slots);
    vec_free (addrs);
    printf ("  its own four, and not a subscriber, a gateway or the "
	    "internet\n");
  }

  printf ("\n=== a thousand addresses, colliding and wrapping ===\n");
  {
    u32 *addrs = 0, *slots, i, missing = 0, strangers = 0;

    for (i = 1; i <= 1000; i++)
      vec_add1 (addrs, ip (10, (i >> 8) & 255, i & 255, 1));
    slots = bpa_local_build (addrs);
    CHECK (vec_len (slots) >= 2 * 1000, "1000 addresses in %u slots",
	   vec_len (slots));
    for (i = 0; i < vec_len (addrs); i++)
      missing += !bpa_local_has (slots, addrs[i]);
    for (i = 1; i <= 1000; i++)
      strangers += bpa_local_has (slots, ip (10, (i >> 8) & 255, i & 255, 2));
    CHECK (missing == 0, "%u of 1000 members missing", missing);
    CHECK (strangers == 0, "%u of 1000 strangers taken for members",
	   strangers);
    vec_free (slots);
    vec_free (addrs);
    printf ("  every member found and no stranger, at half full\n");
  }

  printf ("\n%u checks, %d failures\n", checks, failures);
  printf (failures ? "%d FAILURES\n" : "ALL TESTS PASSED\n", failures);
  return failures ? 1 : 0;
}
