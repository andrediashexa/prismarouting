/* test_bpa_ports.c - unit test for the per-subscriber port allocator. */

#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
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

/*
 * Builds a map whose storage is EXACTLY as long as the map, with an unmapped
 * page right behind the last word.
 *
 * Three of this allocator's bounds are reads rather than verdicts: the hint's
 * wrap back into the map, the pair scan's refusal to consider the port after
 * the last one, and free()'s refusal of an offset outside the block. Delete
 * any of them and every check below went on passing, because every other map
 * in this file is carved out of a 1024-word array in a caller's frame and a
 * read a few words past a 2-word map lands inside it, on bytes that happen to
 * read as "in use". That is not an assertion, it is undefined behaviour with a
 * convenient result.
 *
 * Returns the region to unmap, or 0.
 */
static void *
map_against_guard_page (bpa_portmap_t *pm, u16 n_ports, long pagesz)
{
  u32 n_words = (u32) ((n_ports + 63) / 64);
  u8 *region = mmap (0, 2 * (size_t) pagesz, PROT_READ | PROT_WRITE,
		     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);

  if (region == MAP_FAILED)
    return 0;
  if (mprotect (region + pagesz, (size_t) pagesz, PROT_NONE))
    {
      munmap (region, 2 * (size_t) pagesz);
      return 0;
    }

  bpa_portmap_init (pm, n_ports,
		    (u64 *) (region + pagesz - n_words * sizeof (u64)));
  return region;
}

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
  /* The scan is what refuses this, not the "n_free == 0" line above it: with
   * every bit set no word has a free one, so deleting that line changes
   * nothing an input can reach. It is a fast path over a map whose count and
   * bitmap agree, and through this API they always do.
   * bpa_portmap_alloc_pair's "n_free < 2" has the same shape. Both are noted
   * here rather than covered, because an assertion claiming to be about
   * either would be about the scan. */
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

    /*
     * The out-of-range free that lands INSIDE the map's own last word.
     *
     * 5000 above is 76 words past a two-word map, so with "off >= n_ports"
     * deleted it reads the caller's storage and the double-free test happens
     * to send it home - the check passes on a read that was never the map's.
     * A block of 100 ports has 28 bits of tail in its second word, and every
     * one of them is inside the storage and marked used at init. Freeing one
     * needs no page past the end to go wrong: it clears a tail bit and raises
     * n_free above the block size, and the next drain hands out an offset
     * outside the subscriber's block - which is another subscriber's port.
     */
    bpa_portmap_init (&pm, 100, storage);
    bpa_portmap_free (&pm, 110); /* in the tail of the last word */
    CHECK (pm.n_free == 100, "freeing a tail offset changed n_free to %u",
	   pm.n_free);
    CHECK (bpa_portmap_is_used (&pm, 110) == 1,
	   "a tail offset was made allocatable");
    printf ("  a free inside the last word but outside the block is ignored\n");
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

  printf ("\n=== a reserved port is never handed out ===\n");
  {
    int off;
    u32 i, bad = 0;

    /* What a static port forward does to a block: one offset taken out of
     * the allocator's reach for as long as the configuration says so. */
    bpa_portmap_init (&pm, 64, storage);
    CHECK (bpa_portmap_reserve (&pm, 20) == 0, "reserving a free port failed");
    CHECK (pm.n_free == 63, "n_free is %u after one reservation", pm.n_free);

    /* Idempotent: the same public port may be forwarded for TCP and for UDP,
     * and the bitmap has no protocol dimension. A second reservation must not
     * take a second port out of the count. */
    CHECK (bpa_portmap_reserve (&pm, 20) == 1,
	   "a second reservation of the same port did not report it taken");
    CHECK (pm.n_free == 63, "n_free is %u after reserving twice", pm.n_free);

    /* Port preservation asks for exactly that offset and must be refused it,
     * which is the case bpa_portmap_alloc's "preferred" argument gets wrong
     * on its own: it silently hands out a different port instead. */
    off = bpa_portmap_alloc (&pm, 20);
    CHECK (off >= 0 && off != 20,
	   "the allocator returned the reserved offset (%d)", off);

    /* And it is still out of reach when the block is drained. */
    while (bpa_portmap_alloc (&pm, -1) >= 0)
      ;
    CHECK (bpa_portmap_is_used (&pm, 20) == 1,
	   "the reserved offset was handed out while draining the block");

    bpa_portmap_free (&pm, 20);
    CHECK (bpa_portmap_is_used (&pm, 20) == 0,
	   "the reservation could not be lifted");

    CHECK (bpa_portmap_reserve (&pm, 64) == -1,
	   "reserving an offset outside the block was accepted");

    for (i = 0; i < 64; i++)
      if (i != 20 && !bpa_portmap_is_used (&pm, (u16) i))
	bad++;
    CHECK (bad == 0, "%u ports went missing around the reservation", bad);
    printf ("  reserved once, refused to preservation, and given back\n");
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

  printf ("\n=== the map's own storage, and nothing past it ===\n");
  {
    long pagesz = sysconf (_SC_PAGESIZE);
    bpa_portmap_t g;
    void *region;
    u32 got = 0, oob = 0;
    int off;

    /*
     * Every call below that can die is named BEFORE it runs. Without the
     * bound it is about, this process does not disagree - it dies inside the
     * call, and a crash with the line still in stdio's buffer names nothing.
     */
    setvbuf (stdout, NULL, _IONBF, 0);

    /* First, the ordinary case with nothing to absorb a mistake: a full drain
     * of a map that owns exactly its own storage. It must hand out the block
     * and not one offset more. */
    region = map_against_guard_page (&g, 128, pagesz);
    CHECK (region != 0, "could not place a 128-port map against a guard page");
    if (region)
      {
	printf ("  draining a 128-port map whose storage ends at a page\n");
	while ((off = bpa_portmap_alloc (&g, -1)) >= 0)
	  {
	    if (off >= 128)
	      oob++;
	    if (++got > 200)
	      break; /* runaway guard */
	  }
	CHECK (got == 128, "the drain handed out %u ports, expected 128", got);
	CHECK (oob == 0, "%u offsets came from outside the map", oob);
	munmap (region, 2 * (size_t) pagesz);
      }

    /*
     * The hint's wrap, which a plain drain never reaches: the cursor follows
     * the allocations, so the scan finds its free bit in the word it starts
     * in and never walks off the end. It matters when the LAST word fills
     * before the first, which is what this builds - reserve() takes ports
     * without moving the cursor, so the cursor can be parked in the last
     * word while the free port is back in the first one.
     */
    region = map_against_guard_page (&g, 128, pagesz);
    CHECK (region != 0, "could not place the wrap's map against a guard page");
    if (region)
      {
	u32 i;

	for (i = 0; i < 64; i++)
	  (void) bpa_portmap_reserve (&g, (u16) i); /* word 0 full */
	off = bpa_portmap_alloc (&g, -1);	    /* parks the cursor in word 1 */
	CHECK (off == 64, "the scan left word 0 and gave %d, expected 64", off);
	for (i = 65; i < 128; i++)
	  (void) bpa_portmap_reserve (&g, (u16) i); /* word 1 full too */
	bpa_portmap_free (&g, 0);		    /* the only port left */

	printf ("  allocating with the cursor in the last word and the only "
		"free port in the first\n");
	CHECK (bpa_portmap_alloc (&g, -1) == 0,
	       "the scan did not come back round to offset 0");

	/* free() of an offset outside the block, with nothing behind the map
	 * to absorb the read. */
	printf ("  freeing offset 5000 of that map\n");
	bpa_portmap_free (&g, 5000);
	CHECK (g.n_free == 0, "an out-of-range free changed n_free to %u",
	       g.n_free);
	munmap (region, 2 * (size_t) pagesz);
      }

    /*
     * The pair scan's upper bound. A 64-port block fills its last word
     * exactly, so there is no tail to mark offset 64 used - and with an odd
     * parity the scan reaches offset 63, whose partner is the first word
     * past the map. "off + 1 < n_ports" is what stops it looking.
     */
    region = map_against_guard_page (&g, 64, pagesz);
    CHECK (region != 0, "could not place a 64-port map against a guard page");
    if (region)
      {
	got = 0;
	oob = 0;
	printf ("  exhausting a 64-port map by odd-based pairs\n");
	while ((off = bpa_portmap_alloc_pair (&g, 1)) >= 0)
	  {
	    if (off + 1 >= 64)
	      oob++;
	    if (++got > 64)
	      break; /* runaway guard */
	  }
	/* Offsets 1..62 are 31 pairs; 0 and 63 have no partner of the right
	 * parity inside the block and are left alone. */
	CHECK (got == 31, "odd-based pairs in a 64-port block: %u, expected 31",
	       got);
	CHECK (oob == 0, "%u pairs reached past the end of the block", oob);
	CHECK (g.n_free == 2, "n_free is %u, expected the two odd ones out",
	       g.n_free);
	munmap (region, 2 * (size_t) pagesz);
      }
  }

  return bpa_test_verdict (checks, failures);
}
