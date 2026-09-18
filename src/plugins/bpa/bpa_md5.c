/*
 * bpa_md5.c - MD5 (RFC 1321), because RADIUS asks for it (#21).
 *
 * NOT A SECURITY CHOICE. MD5 is broken for anything that needs collision
 * resistance and nothing here relies on that: RFC 2866 defines the RADIUS
 * Request and Response Authenticators as MD5 over the packet and the shared
 * secret, so a box that speaks accounting speaks MD5 or speaks nothing. The
 * audit trail, which IS about tamper evidence, uses SHA-2 and is untouched.
 *
 * WHY THIS FILE EXISTS AT ALL. vppinfra ships AES, GHASH, Poly1305 and SHA-2
 * and no MD5, and the build image carries no OpenSSL headers - measured on
 * prisma-routing-dev:26.06, /usr/include/vppinfra/crypto/ and
 * /usr/include/openssl. Adding a crypto dependency to the appliance for one
 * 128-bit digest is a larger decision than writing the digest, and this one
 * is checked against the RFC's own test vectors rather than against itself.
 *
 * Standalone: it reads nothing but its arguments.
 */
#include <string.h>

#ifdef BPA_STANDALONE
#include "bpa_shim.h"
#else
#include "bpa.h"
#endif
#include "bpa_md5.h"

#define ROTL(x, c) (((x) << (c)) | ((x) >> (32 - (c))))

static const u32 md5_k[64] = {
  0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a,
  0xa8304613, 0xfd469501, 0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be,
  0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821, 0xf61e2562, 0xc040b340,
  0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
  0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8,
  0x676f02d9, 0x8d2a4c8a, 0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c,
  0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70, 0x289b7ec6, 0xeaa127fa,
  0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
  0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92,
  0xffeff47d, 0x85845dd1, 0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1,
  0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391,
};

static const u8 md5_r[64] = {
  7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
  5, 9,  14, 20, 5, 9,  14, 20, 5, 9,  14, 20, 5, 9,  14, 20,
  4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
  6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
};

static void
md5_block (u32 h[4], const u8 *p)
{
  u32 m[16], a = h[0], b = h[1], c = h[2], d = h[3], i;

  for (i = 0; i < 16; i++)
    m[i] = (u32) p[i * 4] | ((u32) p[i * 4 + 1] << 8)
	   | ((u32) p[i * 4 + 2] << 16) | ((u32) p[i * 4 + 3] << 24);

  for (i = 0; i < 64; i++)
    {
      u32 f, g, tmp;

      if (i < 16)
	f = (b & c) | (~b & d), g = i;
      else if (i < 32)
	f = (d & b) | (~d & c), g = (5 * i + 1) & 15;
      else if (i < 48)
	f = b ^ c ^ d, g = (3 * i + 5) & 15;
      else
	f = c ^ (b | ~d), g = (7 * i) & 15;

      tmp = d;
      d = c;
      c = b;
      b = b + ROTL (a + f + md5_k[i] + m[g], md5_r[i]);
      a = tmp;
    }
  h[0] += a;
  h[1] += b;
  h[2] += c;
  h[3] += d;
}

/*
 * The digest of `n` bytes, in one call.
 *
 * ONE CALL AND NO STREAMING CONTEXT, because every use here is a whole
 * message: a RADIUS packet with its secret appended is at most a few hundred
 * bytes and is in memory already. A context would be three more functions to
 * get wrong for a caller that does not exist.
 */
void
bpa_md5 (const u8 *data, u32 n, u8 out[16])
{
  u32 h[4] = { 0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476 };
  u8 tail[128];
  u32 i, whole = n & ~63u, rest = n - whole;
  u64 bits = (u64) n * 8;

  for (i = 0; i < whole; i += 64)
    md5_block (h, data + i);

  memset (tail, 0, sizeof (tail));
  memcpy (tail, data + whole, rest);
  tail[rest] = 0x80;
  /* The length goes in the last eight bytes of the final block, which is the
   * second one whenever the remainder leaves no room for it. */
  i = (rest < 56) ? 64 : 128;
  for (u32 j = 0; j < 8; j++)
    tail[i - 8 + j] = (u8) (bits >> (8 * j));
  md5_block (h, tail);
  if (i == 128)
    md5_block (h, tail + 64);

  for (i = 0; i < 4; i++)
    {
      out[i * 4] = (u8) h[i];
      out[i * 4 + 1] = (u8) (h[i] >> 8);
      out[i * 4 + 2] = (u8) (h[i] >> 16);
      out[i * 4 + 3] = (u8) (h[i] >> 24);
    }
}
