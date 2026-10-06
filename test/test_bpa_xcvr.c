/*
 * test_bpa_xcvr.c - the arithmetic of the plugin's transceiver read (#590):
 * which bytes of a module's EEPROM it asks DPDK for, and how it finds the
 * GNU build-id that decides whether it may call into dpdk_plugin.so at all.
 *
 * WHY THESE TWO. A range list that asks for the wrong page is a screen of
 * "thresholds" read out of the identity page, and nothing on that screen
 * would look wrong. A build-id comparison that matched the wrong note - or
 * accepted a different build - is a call into the middle of some other
 * function of the dataplane, at an offset taken from a build that is not the
 * one running: the one refusal in bpa_transceiver.c that protects the box.
 */

#include <stdio.h>
#include <string.h>
#include "bpa_shim.h"
#include "../src/plugins/bpa/bpa_xcvr.h"

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

/* One ELF note: namesz, descsz, type, the name and the descriptor, each
 * padded to 4. Returns the bytes written. */
static u32
note (u8 *at, const char *name, u32 type, const u8 *desc, u32 descsz)
{
  u32 namesz = strlen (name) + 1, n = 0;

  memcpy (at, &namesz, 4);
  memcpy (at + 4, &descsz, 4);
  memcpy (at + 8, &type, 4);
  n = 12;
  memset (at + n, 0, (namesz + 3) & ~3u);
  memcpy (at + n, name, namesz);
  n += (namesz + 3) & ~3u;
  memset (at + n, 0, (descsz + 3) & ~3u);
  memcpy (at + n, desc, descsz);
  n += (descsz + 3) & ~3u;
  return n;
}

/* The build-id of vpp-plugin-dpdk 26.06-release's dpdk_plugin.so, as
 * `readelf -n` prints it on the box's image (prisma-vpp) and in the dev
 * image: tools/dpdk-symbols.sh writes the same string. */
static const char real_id[] = "4b44bc4d2c9a7432f298e52080e5cbac2dbdd9b2";

static void
id_bytes (u8 *out)
{
  u32 i;
  for (i = 0; i < 20; i++)
    sscanf (real_id + 2 * i, "%2hhx", &out[i]);
}

int
main (void)
{
  printf ("=== an SFP with diagnostics: identity, thresholds, readings ===\n");
  {
    bpa_xcvr_range_t r[4];
    u32 n = bpa_xcvr_ranges (BPA_XCVR_SFF_8472, 512, 1, r);

    CHECK (n == 3, "three ranges on the first read, got %u", n);
    CHECK (r[0].offset == 0 && r[0].length == 96,
	   "A0h's identity is 0-95 (vendor, part, serial, date, byte 92's "
	   "diagnostic type): got %u+%u", r[0].offset, r[0].length);
    CHECK (r[1].offset == 256 && r[1].length == 96,
	   "A2h starts at 256 in DPDK's map, and its thresholds and "
	   "calibration are 0-95: got %u+%u", r[1].offset, r[1].length);
    CHECK (r[2].offset == 352 && r[2].length == 24,
	   "the readings are A2h 96-119 (256+96): got %u+%u", r[2].offset,
	   r[2].length);
  }
  printf ("  [ ok ] A0h 0-95, A2h 0-95, A2h 96-119\n");

  printf ("\n=== a poll reads only the readings ===\n");
  /* The cost the split exists for: an ixgbe byte is a bit-banged I2C
   * transaction, and the identity and thresholds do not change. */
  {
    bpa_xcvr_range_t r[4];
    u32 n = bpa_xcvr_ranges (BPA_XCVR_SFF_8472, 512, 0, r), total = 0, i;

    for (i = 0; i < n; i++)
      total += r[i].length;
    CHECK (n == 1 && r[0].offset == 352,
	   "a poll asks for the readings alone: %u range(s)", n);
    CHECK (total == 24, "24 bytes a poll, got %u", total);
  }
  printf ("  [ ok ] 24 bytes\n");

  printf ("\n=== an SFP without diagnostics: its identity, when asked ===\n");
  {
    bpa_xcvr_range_t r[4];

    CHECK (bpa_xcvr_ranges (BPA_XCVR_SFF_8079, 256, 1, r) == 1
	     && r[0].offset == 0 && r[0].length == 96,
	   "sff-8079 is A0h's 0-95");
    CHECK (bpa_xcvr_ranges (BPA_XCVR_SFF_8079, 256, 0, r) == 0,
	   "and a poll of it reads nothing: there is nothing live in it");
  }
  printf ("  [ ok ] 0-95, and nothing on a poll\n");

  printf ("\n=== a QSFP: page 00h, page 03h's thresholds, the lower page ===\n");
  {
    bpa_xcvr_range_t r[4];
    u32 n = bpa_xcvr_ranges (BPA_XCVR_SFF_8636, 640, 1, r);

    CHECK (n == 3, "three ranges for a paged QSFP, got %u", n);
    CHECK (r[0].offset == 128 && r[0].length == 128,
	   "page 00h upper is 128-255: got %u+%u", r[0].offset, r[0].length);
    CHECK (r[1].offset == 512 && r[1].length == 72,
	   "page 03h upper is at 512, thresholds 128-199 of it: got %u+%u",
	   r[1].offset, r[1].length);
    CHECK (r[2].offset == 0 && r[2].length == 58,
	   "the lower page's 0-57 carry the four lanes: got %u+%u",
	   r[2].offset, r[2].length);
    n = bpa_xcvr_ranges (BPA_XCVR_SFF_8436, 256, 1, r);
    CHECK (n == 2 && r[0].offset == 128 && r[1].offset == 0,
	   "a flat-memory map (256 bytes) has no page 03h to ask for: %u "
	   "range(s)", n);
    n = bpa_xcvr_ranges (BPA_XCVR_SFF_8636, 640, 0, r);
    CHECK (n == 1 && r[0].offset == 0 && r[0].length == 58,
	   "and a poll reads the lower page alone");
  }
  printf ("  [ ok ] 128-255, 512-583, 0-57\n");

  printf ("\n=== a map shorter than its document is refused ===\n");
  {
    bpa_xcvr_range_t r[4];

    CHECK (bpa_xcvr_ranges (BPA_XCVR_SFF_8472, 256, 1, r) == 0,
	   "an sff-8472 map of 256 bytes has no A2h to read");
    CHECK (bpa_xcvr_ranges (BPA_XCVR_SFF_8636, 128, 1, r) == 0,
	   "a QSFP map without page 00h");
    CHECK (bpa_xcvr_ranges (7, 640, 1, r) == 0, "a type DPDK may add later");
  }
  printf ("  [ ok ] nothing is read past the map\n");

  printf ("\n=== the names, both ways ===\n");
  {
    u32 t;

    for (t = BPA_XCVR_SFF_8079; t <= BPA_XCVR_SFF_8436; t++)
      CHECK (bpa_xcvr_type_of (bpa_xcvr_type_name (t)) == t,
	     "type %u does not come back through its name", t);
    CHECK (!strcmp (bpa_xcvr_type_name (BPA_XCVR_SFF_8472), "sff-8472"),
	   "sff-8472 is spelled as prismash's parse_dump reads it");
    CHECK (!strcmp (bpa_xcvr_type_name (9), "unknown"), "an unknown type");
    CHECK (bpa_xcvr_type_of ("sff-9999") == 0, "an unknown name");
  }
  printf ("  [ ok ] sff-8079 sff-8472 sff-8636 sff-8436\n");

  printf ("\n=== the build-id, found among the notes ===\n");
  {
    u8 seg[256], id[64], want[20], abi[16] = { 0, 0, 0, 0, 3, 0, 0, 0,
					       2, 0, 0, 0, 0, 0, 0, 0 };
    u32 len, n;

    id_bytes (want);
    /* The ABI tag first, as a Debian-built object carries it: the build-id
     * is not always the first note. */
    len = note (seg, "GNU", 1, abi, sizeof (abi));
    len += note (seg + len, "GNU", 3, want, 20);
    n = bpa_xcvr_build_id (seg, len, id, sizeof (id));
    CHECK (n == 20 && !memcmp (id, want, 20),
	   "the build-id after the ABI tag was not found: %u byte(s)", n);
    CHECK (bpa_xcvr_build_id_is (id, n, real_id),
	   "and it does not compare equal to its own spelling");
  }
  printf ("  [ ok ] the second note, 20 bytes\n");

  printf ("\n=== a note of another owner, or none, is no build-id ===\n");
  {
    u8 seg[256], id[64], want[20];
    u32 len;

    id_bytes (want);
    len = note (seg, "Go", 3, want, 20);
    CHECK (bpa_xcvr_build_id (seg, len, id, sizeof (id)) == 0,
	   "type 3 under another owner's name is not GNU's build-id");
    CHECK (bpa_xcvr_build_id (seg, 0, id, sizeof (id)) == 0,
	   "an empty segment");
    len = note (seg, "GNU", 3, want, 20);
    CHECK (bpa_xcvr_build_id (seg, len - 4, id, sizeof (id)) == 0,
	   "a descriptor running past the segment is not read");
    CHECK (bpa_xcvr_build_id (seg, len, id, 8) == 0,
	   "nor one longer than the room for it");
  }
  printf ("  [ ok ] none of them\n");

  printf ("\n=== a different build is refused ===\n");
  /* The refusal that keeps the offsets from being called in a build they
   * were not read from. */
  {
    u8 want[20];

    id_bytes (want);
    CHECK (bpa_xcvr_build_id_is (want, 20, real_id), "the same build");
    want[19] ^= 1;
    CHECK (!bpa_xcvr_build_id_is (want, 20, real_id),
	   "a build differing in its last bit was accepted");
    want[19] ^= 1;
    want[0] ^= 0x10;
    CHECK (!bpa_xcvr_build_id_is (want, 20, real_id),
	   "a build differing in its first digit was accepted");
    want[0] ^= 0x10;
    CHECK (!bpa_xcvr_build_id_is (want, 19, real_id),
	   "a shorter id that is a prefix of it was accepted");
    CHECK (!bpa_xcvr_build_id_is (want, 0, ""),
	   "no id at all compared equal to no spelling");
  }
  printf ("  [ ok ] one bit, one digit, a prefix, nothing\n");

  printf ("\n%u check(s)\n", checks);
  if (failures == 0)
    {
      printf ("ALL TESTS PASSED\n");
      return 0;
    }
  printf ("%d FAILURES\n", failures);
  return 1;
}
