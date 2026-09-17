/*
 * test_bpa_audit.c - the one function of the configuration trail that reads
 * nothing but text.
 *
 * bpa_audit.c keeps a hash chain over an append-only file, and the chain is
 * what makes the trail evidence rather than a note: an entry that was altered
 * no longer matches its own digest, and every digest after it moves too. All
 * of that is arithmetic over bytes, and the bytes only exist because this
 * function turns the sixty-four characters on the line back into thirty-two
 * of them.
 *
 * Which makes its two refusals the load-bearing half. A reader that accepted
 * a line whose digest field is short, or whose characters are not hex, would
 * hand the verifier a half-filled buffer and a comparison against uninitialised
 * memory - and the verdict printed to whoever is investigating a court order
 * would be a coin toss rather than a reading of the file. Both are silent:
 * the trail still opens, still prints, and still says something.
 *
 * Everything else in bpa_audit.c formats with VPP's format(), digests with
 * vppinfra's SHA-2, or writes to the CLI, and waits for the VPP build.
 */

#include <stdio.h>
#include <string.h>

#include "bpa_shim.h"
/*
 * INCLUDED rather than linked: bpa_audit_unhex() is static, so nothing
 * compiled beside the file can call it. bpa_audit.c carries the
 * BPA_STANDALONE guards that leave the rest of the trail out of this build.
 */
#include "../src/plugins/bpa/bpa_audit.c"

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

int
main (void)
{
  /* Sixty-four characters, every nibble value once in each half, so a swap of
   * the two nibbles of a byte cannot land on the same answer. */
  static const char good[] = "0123456789abcdef0123456789abcdef"
			     "fedcba9876543210fedcba9876543210";
  /* The same text with one uppercase character. format_hex_bytes writes lower
   * case, so an upper-case digit is a line this exporter did not write. */
  static const char upper[] = "0123456789abcdeF0123456789abcdef"
			      "fedcba9876543210fedcba9876543210";
  /* Sixty-three characters: the field is one short and the sixty-fourth byte
   * read is the terminator. */
  static const char short_field[] = "0123456789abcdef0123456789abcdef"
				    "fedcba9876543210fedcba987654321";
  static const u8 want[32] = {
    0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef,
    0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef,
    0xfe, 0xdc, 0xba, 0x98, 0x76, 0x54, 0x32, 0x10,
    0xfe, 0xdc, 0xba, 0x98, 0x76, 0x54, 0x32, 0x10,
  };
  u8 out[32];

  printf ("=== the configuration trail: reading a digest back ===\n\n");

  memset (out, 0xa5, sizeof (out));
  CHECK (bpa_audit_unhex (good, out) == 1 && memcmp (out, want, 32) == 0
	   && bpa_audit_unhex (upper, out) == 0
	   && bpa_audit_unhex (short_field, out) == 0,
	 "bpa_audit_unhex rebuilds the thirty-two bytes and refuses anything "
	 "that is not sixty-four lower-case hex characters: rc=%d "
	 "first=%02x%02x last=%02x%02x, upper=%d short=%d",
	 bpa_audit_unhex (good, out), out[0], out[1], out[30], out[31],
	 bpa_audit_unhex (upper, out), bpa_audit_unhex (short_field, out));

  return bpa_test_verdict (checks, failures);
}
