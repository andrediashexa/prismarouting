/*
 * MD5 against RFC 1321's own test vectors (#21).
 *
 * KNOWN ANSWERS AND NOT A ROUND TRIP. A digest checked against itself passes
 * for every implementation including a wrong one, and a wrong Response
 * Authenticator is a RADIUS client that retransmits forever while this box
 * reports accounting as working. The seven vectors below are the suite in
 * appendix A.5 of the RFC, byte for byte, plus the two lengths where the
 * padding decides the answer: 55 bytes (the length fits in the first block)
 * and 56 (it does not, and a second block is appended).
 */
#include <stdio.h>
#include <string.h>

#include "bpa_shim.h"
#include "bpa_md5.c"

static int fail;

#define CHECK(cond, ...)                                                      \
  do                                                                          \
    {                                                                         \
      if (cond)                                                               \
	printf ("  [ ok ] " __VA_ARGS__), printf ("\n");                      \
      else                                                                    \
	fail++, printf ("  FAIL: " __VA_ARGS__), printf ("\n");               \
    }                                                                         \
  while (0)

static const char *
hex (const u8 d[16], char out[33])
{
  static const char *H = "0123456789abcdef";
  int i;

  for (i = 0; i < 16; i++)
    {
      out[i * 2] = H[d[i] >> 4];
      out[i * 2 + 1] = H[d[i] & 15];
    }
  out[32] = 0;
  return out;
}

int
main (void)
{
  struct
  {
    const char *in;
    const char *want;
  } v[] = {
    { "", "d41d8cd98f00b204e9800998ecf8427e" },
    { "a", "0cc175b9c0f1b6a831c399e269772661" },
    { "abc", "900150983cd24fb0d6963f7d28e17f72" },
    { "message digest", "f96b697d7cb7938d525a2f31aaf161d0" },
    { "abcdefghijklmnopqrstuvwxyz", "c3fcd3d76192e4007dfb496cca67e13b" },
    { "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
      "d174ab98d277d9f5a5611c2c9f419d9f" },
    { "123456789012345678901234567890123456789012345678901234567890123456"
      "78901234567890",
      "57edf4a22be3c955ac49da2e2107b67a" },
  };
  u8 d[16];
  char got[33];
  u32 i;

  printf ("=== MD5, against RFC 1321 appendix A.5 ===\n");
  for (i = 0; i < sizeof (v) / sizeof (v[0]); i++)
    {
      bpa_md5 ((const u8 *) v[i].in, (u32) strlen (v[i].in), d);
      CHECK (strcmp (hex (d, got), v[i].want) == 0, "\"%.20s%s\" -> %s",
	     v[i].in, strlen (v[i].in) > 20 ? "..." : "", got);
    }

  printf ("\n--- the two lengths where the padding decides the answer ---\n");
  {
    /* 55 bytes: the length still fits in the first block. 56: it does not,
     * and a second block of padding is appended. An implementation that
     * gets the boundary wrong passes every vector above. */
    char a55[56], a56[57];

    memset (a55, 'a', 55); a55[55] = 0;
    memset (a56, 'a', 56); a56[56] = 0;
    bpa_md5 ((const u8 *) a55, 55, d);
    CHECK (strcmp (hex (d, got), "ef1772b6dff9a122358552954ad0df65") == 0,
	   "55 bytes -> %s", got);
    bpa_md5 ((const u8 *) a56, 56, d);
    CHECK (strcmp (hex (d, got), "3b0c8ac703f828b04c6c197006d17218") == 0,
	   "56 bytes -> %s", got);
  }

  printf ("\n--- and a message longer than one block ---\n");
  {
    /* The expected value is from an INDEPENDENT implementation - python3
     * hashlib.md5(bytes(range(200))) - because a value this file worked out
     * from the code beside it would agree with that code's mistakes. The
     * RFC's own suite above is short enough that nothing in it crosses three
     * blocks. */
    u8 big[200];

    for (i = 0; i < sizeof (big); i++)
      big[i] = (u8) i;
    bpa_md5 (big, sizeof (big), d);
    CHECK (strcmp (hex (d, got), "fb7001d34b8e82c9b579be5005d5b0a5") == 0,
	   "200 bytes of 0..199 -> %s", got);
  }

  printf ("\n--- the block transform, called by name ---\n");
  {
    /* The digest above drives it for every vector, but the reader that asks
     * which functions no test NAMES cannot see a static called through
     * another. Driven here on the padded empty message, whose answer is the
     * first vector's. */
    u32 h[4] = { 0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476 };
    u8 block[64];

    memset (block, 0, sizeof (block));
    block[0] = 0x80;
    md5_block (h, block);
    CHECK (h[0] == 0xd98c1dd4 && h[3] == 0x7e42f8ec,
	   "one block of the empty message's padding: %08x..%08x", h[0], h[3]);
  }

  printf ("\n%s\n", fail ? "FAILURES" : "ALL TESTS PASSED");
  return fail ? 1 : 0;
}
