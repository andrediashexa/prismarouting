/* test_bpa_ports.c - unit test for the per-subscriber port allocator. */

#include <stdio.h>
#include <string.h>
#include "bpa_shim.h"
#include "../src/plugins/bpa/bpa_ports.h"

static int failures = 0;
static unsigned checks = 0;

#define CHECK(cond, ...)                                                      \
  do                                                                          \
    {                                                                         \
      checks++;                                                               \
      if (!(cond))                                                            \
	{                                                                     \
	  if (failures < 10)                                                  \
	    {                                                                 \
	      printf ("  FAIL: ");                                           \
	      printf (__VA_ARGS__);                                           \
	      printf ("\n");                                                  \
	    }                                                                 \
	  failures++;                                                         \
	}                                                                     \
    }                                                                         \
  while (0)

/* Drains the block and verifies every offset comes back exactly once. */
static void
test_exhaust (u16 n_ports)
{
  u64 storage[BPA_PORTMAP_MAX_WORDS];
  bpa_portmap_t pm;
  u8 seen[65536];
  u32 i, got = 0, dup = 0, oob = 0;

  memset (seen, 0, sizeof (seen));
  bpa_portmap_init (&pm, n_ports, storage);
  CHECK (pm.n_free == n_ports, "block %u: initial n_free %u", n_ports,
	 pm.n_free);

  for (;;)
    {
      int off = bpa_portmap_alloc (&pm, -1);
      if (off < 0)
	break;
      if (off >= (int) n_ports)
	oob++;
      else if (seen[off])
	dup++;
      else
	seen[off] = 1;
      got++;
      if (got > (u32) n_ports + 8)
	break; /* runaway guard */
    }

  CHECK (got == n_ports, "block %u: handed out %u ports, expected %u", n_ports,
	 got, n_ports);
  CHECK (dup == 0, "block %u: %u duplicated ports", n_ports, dup);
  CHECK (oob == 0, "block %u: %u ports outside the block", n_ports, oob);
  CHECK (pm.n_free == 0, "block %u: final n_free %u", n_ports, pm.n_free);
  CHECK (bpa_portmap_alloc (&pm, -1) == -1, "block %u: allocated from an exhausted map",
	 n_ports);

  for (i = 0; i < n_ports; i++)
    CHECK (seen[i] == 1, "block %u: offset %u never handed out", n_ports, i);

  printf ("  block %-5u %u distinct ports, exhaustion correct\n", n_ports,
	  got);
}

int
main (void)
{
  u64 storage[BPA_PORTMAP_MAX_WORDS];
  bpa_portmap_t pm;

  printf ("=== exhaustion without collision, several block sizes ===\n");
  test_exhaust (32); /* exercises the tail masking */
  test_exhaust (64);
  test_exhaust (128);
  test_exhaust (512);
  test_exhaust (4096);

  printf ("\n=== port preservation ===\n");
  {
    bpa_portmap_init (&pm, 512, storage);
    CHECK (bpa_portmap_alloc (&pm, 300) == 300, "did not honour port 300");
    CHECK (bpa_portmap_is_used (&pm, 300), "300 was not marked used");
    /* preferred one already taken: must fall back, never repeat */
    CHECK (bpa_portmap_alloc (&pm, 300) != 300, "entregou 300 duas vezes");
    /* preferred outside the block: ignore it and allocate normally */
    {
      int off = bpa_portmap_alloc (&pm, 9999);
      CHECK (off >= 0 && off < 512, "preferida invalida quebrou a alocacao");
    }
    printf ("  preferred port honoured, collision and out-of-range handled\n");
  }

  printf ("\n=== liberacao e reuso ===\n");
  {
    u16 free_me;
    int off;

    bpa_portmap_init (&pm, 128, storage);
    while (bpa_portmap_alloc (&pm, -1) >= 0)
      ;
    CHECK (pm.n_free == 0, "the block did not run out");

    free_me = 77;
    bpa_portmap_free (&pm, free_me);
    CHECK (pm.n_free == 1, "n_free apos liberar = %u", pm.n_free);
    CHECK (!bpa_portmap_is_used (&pm, free_me), "77 continua marcada");

    off = bpa_portmap_alloc (&pm, -1);
    CHECK (off == free_me, "reuso devolveu %d, esperado %u", off, free_me);
    CHECK (pm.n_free == 0, "n_free apos realocar = %u", pm.n_free);
    printf ("  a freed port becomes allocatable again\n");
  }

  printf ("\n=== invalid operations do not corrupt the map ===\n");
  {
    bpa_portmap_init (&pm, 128, storage);
    CHECK (bpa_portmap_alloc (&pm, -1) == 0, "primeira alocacao != 0");

    bpa_portmap_free (&pm, 0);
    CHECK (pm.n_free == 128, "n_free apos liberar = %u", pm.n_free);
    bpa_portmap_free (&pm, 0); /* double free */
    CHECK (pm.n_free == 128, "double free changed n_free to %u", pm.n_free);

    bpa_portmap_free (&pm, 5000); /* outside the block */
    CHECK (pm.n_free == 128, "an out-of-range free changed n_free to %u",
	   pm.n_free);
    printf ("  double free and out-of-range free ignored\n");
  }

  printf ("\n=== consecutive pairs for RTP/RTCP ===\n");
  {
    u32 i, got = 0, bad_parity = 0, bad_pair = 0;
    int off;

    /*
     * RTP conventionally uses an even port with RTCP on the one above
     * (RFC 3550 s11), and camera firmware assumes it. A pair that is
     * consecutive but starts odd fails as "no video", which nobody
     * attributes to the NAT - so the parity is asserted, not just the
     * adjacency.
     */
    bpa_portmap_init (&pm, 512, storage);
    for (;;)
      {
	off = bpa_portmap_alloc_pair (&pm, 0 /* port_lo even */);
	if (off < 0)
	  break;
	if (off & 1)
	  bad_parity++;
	if (!bpa_portmap_is_used (&pm, (u16) off)
	    || !bpa_portmap_is_used (&pm, (u16) (off + 1)))
	  bad_pair++;
	got++;
	if (got > 512)
	  break; /* runaway guard */
      }
    CHECK (got == 256, "512-port block gave %u pairs, expected 256", got);
    CHECK (bad_parity == 0, "%u pairs started on an odd offset", bad_parity);
    CHECK (bad_pair == 0, "%u pairs did not mark both ports", bad_pair);
    CHECK (pm.n_free == 0, "n_free after exhausting by pairs = %u", pm.n_free);

    /* A block whose first port is odd needs odd OFFSETS to land the RTP port
     * on an even public port. Getting this backwards hands out an odd RTP
     * port on exactly the pools that need it most. */
    bpa_portmap_init (&pm, 64, storage);
    off = bpa_portmap_alloc_pair (&pm, 1 /* port_lo odd */);
    CHECK (off == 1, "odd-based block gave offset %d, expected 1", off);

    /* A single free port between two used ones is not a pair. */
    bpa_portmap_init (&pm, 64, storage);
    for (i = 0; i < 64; i++)
      (void) bpa_portmap_alloc (&pm, (int) i);
    bpa_portmap_free (&pm, 10);
    CHECK (bpa_portmap_alloc_pair (&pm, 0) == -1,
	   "handed out a pair with only one free port");
    bpa_portmap_free (&pm, 11);
    CHECK (bpa_portmap_alloc_pair (&pm, 0) == 10,
	   "did not reuse the freed pair at 10");
    printf ("  pairs are consecutive, even-aligned and accounted for\n");
  }

  printf ("\n=== the tail is never handed out (32-port block in a 64-bit word) ===\n");
  {
    u32 i, bad = 0;
    bpa_portmap_init (&pm, 32, storage);
    for (i = 32; i < 64; i++)
      if (!bpa_portmap_is_used (&pm, (u16) i))
	bad++;
    CHECK (bad == 0, "%u tail bits were left free", bad);
    printf ("  32 tail bits marked used at init\n");
  }

  printf ("\n%s  (%u checks, %d failures)\n",
	  failures ? "FAILED" : "ALL TESTS PASSED", checks, failures);
  return failures ? 1 : 0;
}
