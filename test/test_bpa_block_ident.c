/*
 * The identity a block's ALLOCATION record named, kept until its RELEASE
 * record names it too (#397).
 *
 * WHAT THIS FILE IS FOR. The decision #397 records is that a release repeats
 * what the allocation said, and every way of getting that wrong produces a
 * record that PARSES: the subscriber who holds the address now, an empty
 * identity, the previous holder of a reused block. None of them is visible in
 * a log, and all of them are the wrong answer to a court order - which is the
 * same argument test_bpa_identity.c makes one step earlier in the same path.
 *
 * So the store is driven here: what it remembers, what it refuses to remember,
 * that a recall is also a forget, that a reused block does not answer with the
 * subscriber before it, and that a table nobody ever wrote to answers rather
 * than crashing.
 *
 * INCLUDED rather than linked, the way every harness here is.
 */
#include <stdio.h>
#include <string.h>

#include "bpa_shim.h"
#include "bpa_vec_shim.h"
#include "bpa_identity.c"
#include "bpa_block_ident.c"

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

static u32
ip (u32 a, u32 b, u32 c, u32 d)
{
  return (a << 24) | (b << 16) | (c << 8) | d;
}

int
main (void)
{
  bpa_block_ident_table_t t = { 0 };
  char out[BPA_IDENTITY_MAX];
  u64 k1, k2, k3;

  printf ("=== the key is the three fields every record carries ===\n");
  k1 = bpa_block_ident_key (0, ip (200, 150, 0, 16), 1024);
  k2 = bpa_block_ident_key (0, ip (200, 150, 0, 16), 2048);
  k3 = bpa_block_ident_key (1, ip (200, 150, 0, 16), 1024);
  CHECK (k1 != k2, "two blocks on one address are two keys");
  CHECK (k1 != k3, "and the same block in two pools is two keys");
  CHECK (k1 == bpa_block_ident_key (0, ip (200, 150, 0, 16), 1024),
	 "and the key is the same every time it is built");

  printf ("\n=== what the allocation said, and the release repeats ===\n");
  bpa_block_ident_remember (&t, k1, "assinante-4471");
  CHECK (t.n_held == 1, "one block remembered: %u", t.n_held);
  CHECK (bpa_block_ident_recall (&t, k1, out, sizeof (out)) == 1
	   && strcmp (out, "assinante-4471") == 0,
	 "the release repeats the allocation's identity: %s", out);
  CHECK (t.n_recalled == 1, "and it is counted as answered from here: %llu",
	 (unsigned long long) t.n_recalled);

  printf ("\n=== a recall is also a forget, because a block is released "
	  "once ===\n");
  CHECK (t.n_held == 0, "the entry is gone: %u", t.n_held);
  strncpy (out, "untouched", sizeof (out));
  CHECK (bpa_block_ident_recall (&t, k1, out, sizeof (out)) == 0
	   && strcmp (out, "untouched") == 0,
	 "a second release finds nothing and leaves the buffer alone: %s",
	 out);
  CHECK (t.n_unremembered == 1, "and says so, once: %llu",
	 (unsigned long long) t.n_unremembered);

  printf ("\n=== a reused block does not answer with the subscriber "
	  "before it ===\n");
  /* THE FAILURE THIS WHOLE FILE IS ABOUT. The slot is reused by design - it
   * is why there is a free stack - and a reused slot that kept its old name
   * would attribute one subscriber's traffic to another, in a record that
   * parses. */
  bpa_block_ident_remember (&t, k1, "assinante-0001");
  CHECK (bpa_block_ident_recall (&t, k1, out, sizeof (out)) == 1, "first");
  bpa_block_ident_remember (&t, k1, "assinante-0002");
  CHECK (bpa_block_ident_recall (&t, k1, out, sizeof (out)) == 1
	   && strcmp (out, "assinante-0002") == 0,
	 "the block's new holder, not the one before: %s", out);
  /* And two allocations with no release between them, which is #215's own
   * defect: the later name wins, because it is the one the later allocation
   * record carried. */
  bpa_block_ident_remember (&t, k1, "assinante-0003");
  bpa_block_ident_remember (&t, k1, "assinante-0004");
  CHECK (t.n_held == 1, "and a second allocation is not a second entry: %u",
	 t.n_held);
  CHECK (bpa_block_ident_recall (&t, k1, out, sizeof (out)) == 1
	   && strcmp (out, "assinante-0004") == 0,
	 "with the identity of the later allocation: %s", out);

  printf ("\n=== the unknown marker is not remembered at all ===\n");
  /* This is what makes the whole mechanism free on a box with no identity
   * table, which is most of them: nothing to repeat, and the release looks up
   * the same unknown it would have looked up anyway. */
  bpa_block_ident_remember (&t, k2, bpa_identity_unknown);
  CHECK (t.n_held == 0, "no entry for a subscriber nobody knows: %u",
	 t.n_held);
  bpa_block_ident_remember (&t, k2, "");
  CHECK (t.n_held == 0, "and none for an empty name: %u", t.n_held);
  bpa_block_ident_remember (&t, k2, 0);
  CHECK (t.n_held == 0, "and none for no name at all: %u", t.n_held);

  printf ("\n=== several blocks at once, each its own ===\n");
  bpa_block_ident_remember (&t, k1, "one");
  bpa_block_ident_remember (&t, k2, "two");
  bpa_block_ident_remember (&t, k3, "three");
  CHECK (t.n_held == 3, "three blocks held: %u", t.n_held);
  CHECK (bpa_block_ident_recall (&t, k2, out, sizeof (out)) == 1
	   && strcmp (out, "two") == 0,
	 "the middle one, by its own key: %s", out);
  CHECK (bpa_block_ident_recall (&t, k1, out, sizeof (out)) == 1
	   && strcmp (out, "one") == 0,
	 "and the first is still itself afterwards: %s", out);
  CHECK (bpa_block_ident_recall (&t, k3, out, sizeof (out)) == 1
	   && strcmp (out, "three") == 0,
	 "and so is the third: %s", out);

  printf ("\n=== a name longer than the field is truncated, not lost ===\n");
  bpa_block_ident_remember (
    &t, k1, "um-nome-de-assinante-muito-mais-longo-do-que-caberia-aqui");
  CHECK (bpa_block_ident_recall (&t, k1, out, sizeof (out)) == 1
	   && strlen (out) == BPA_IDENTITY_MAX - 1
	   && strncmp (out, "um-nome-de-assinante", 20) == 0,
	 "truncated to the field, with the beginning kept: %s", out);

  printf ("\n=== forgetting without reading ===\n");
  /* A block whose pool was deleted and whose record was dropped: the entry has
   * to leave anyway, or it outlives every reader of it. */
  bpa_block_ident_remember (&t, k1, "assinante-9999");
  bpa_block_ident_forget (&t, k1);
  CHECK (t.n_held == 0, "the entry is gone: %u", t.n_held);
  {
    u64 recalled = t.n_recalled, unremembered = t.n_unremembered;

    bpa_block_ident_forget (&t, k1);
    CHECK (t.n_recalled == recalled && t.n_unremembered == unremembered,
	   "and forgetting is not a release, so neither counter moves");
  }

  printf ("\n=== a deleted pool takes its entries with it ===\n");
  /* THE REUSE THIS GUARDS. A pool index is handed to the next pool created, so
   * an entry left behind by a release whose record was never queued would make
   * that pool's first release name the deleted pool's subscriber. */
  bpa_block_ident_remember (&t, bpa_block_ident_key (7, ip (45, 0, 0, 1), 512),
			    "pool-7-assinante");
  bpa_block_ident_remember (&t, bpa_block_ident_key (7, ip (45, 0, 0, 1), 1024),
			    "pool-7-outro");
  bpa_block_ident_remember (&t, bpa_block_ident_key (8, ip (45, 0, 0, 1), 512),
			    "pool-8-assinante");
  CHECK (t.n_held == 3, "three blocks over two pools: %u", t.n_held);
  bpa_block_ident_forget_pool (&t, 7);
  CHECK (t.n_held == 1, "pool 7's two are gone: %u", t.n_held);
  CHECK (bpa_block_ident_recall (&t, bpa_block_ident_key (7, ip (45, 0, 0, 1),
							  512),
				 out, sizeof (out)) == 0,
	 "and a block of pool 7 remembers nothing");
  CHECK (bpa_block_ident_recall (&t, bpa_block_ident_key (8, ip (45, 0, 0, 1),
							  512),
				 out, sizeof (out)) == 1
	   && strcmp (out, "pool-8-assinante") == 0,
	 "while pool 8's block is untouched: %s", out);
  /* And the freed slots are reused rather than leaked, which is what the key
   * in the slot is for. */
  {
    u32 before = vec_len (t.slot);

    bpa_block_ident_remember (&t, bpa_block_ident_key (9, ip (45, 0, 0, 2), 64),
			      "pool-9");
    CHECK (vec_len (t.slot) == before,
	   "a slot a deleted pool gave back is reused: %u of %u",
	   vec_len (t.slot), before);
    bpa_block_ident_recall (&t, bpa_block_ident_key (9, ip (45, 0, 0, 2), 64),
			    out, sizeof (out));
  }
  bpa_block_ident_forget_pool (0, 7);
  bpa_block_ident_forget_pool (&t, 7);
  CHECK (1, "and forgetting a pool with nothing in it is not a crash");

  printf ("\n=== a table nobody wrote to answers, rather than crashing ===\n");
  {
    bpa_block_ident_table_t empty = { 0 };

    CHECK (bpa_block_ident_recall (&empty, k1, out, sizeof (out)) == 0,
	   "an empty table remembers nothing");
    CHECK (bpa_block_ident_recall (0, k1, out, sizeof (out)) == 0,
	   "and neither does no table at all");
    bpa_block_ident_remember (0, k1, "nobody");
    bpa_block_ident_forget (0, k1);
    bpa_block_ident_free (0);
    CHECK (1, "and none of the three crashes on a null table");
    bpa_block_ident_free (&empty);
  }

  printf ("\n=== freeing it releases what it held ===\n");
  bpa_block_ident_remember (&t, k1, "assinante-1");
  bpa_block_ident_remember (&t, k2, "assinante-2");
  bpa_block_ident_free (&t);
  CHECK (t.n_held == 0, "nothing held after the free: %u", t.n_held);
  CHECK (bpa_block_ident_recall (&t, k1, out, sizeof (out)) == 0,
	 "and nothing answers from it");

  printf ("\n%s\n", fail ? "FAILURES" : "ALL TESTS PASSED");
  return fail ? 1 : 0;
}
