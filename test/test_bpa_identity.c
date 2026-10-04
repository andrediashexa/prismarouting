/*
 * The identity table: who held a private address, and what a record says when
 * this box does not know (#21).
 *
 * WHAT THIS FILE IS FOR. Every criterion on that issue is about a value being
 * right at an instant or being absent honestly, and both are invisible in a
 * log that parses: a record naming the subscriber who holds the address NOW
 * rather than the one who held it at the allocation is well-formed, stored,
 * and the wrong answer to a court order. So the parse, the search and the
 * unknown marker are read here, where a wrong one is a failure rather than a
 * plausible line.
 *
 * INCLUDED rather than linked, the way every harness here is: the file is
 * standalone under BPA_STANDALONE, so this compiles with plain cc.
 */
#include <stdio.h>
#include <string.h>

#include "bpa_shim.h"
/* The vector shim, for the same reason test_bpa_dyn.c takes it: the table's
 * in-place updates are vec_add1 and vec_set_len, and what they DO - where an
 * insert lands, what a removal shifts - is the behaviour under test. */
#include "bpa_vec_shim.h"
#include "bpa_identity.c"
#include "bpa_identity_table.c"

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
  bpa_identity_entry_t e;
  char out[BPA_IDENTITY_MAX];
  int rc;

  printf ("=== the identity table, and the record that names nobody ===\n");

  printf ("\n--- one line of the map a provisioning system writes ---\n");
  rc = bpa_identity_parse_line ("100.64.0.5 joao.silva@isp", &e);
  CHECK (rc == 1 && e.ip == ip (100, 64, 0, 5)
	   && strcmp (e.name, "joao.silva@isp") == 0,
	 "an address and an identity: rc=%d %08x %s", rc, e.ip, e.name);
  rc = bpa_identity_parse_line ("   100.64.0.6\tpppoe-4471  ", &e);
  CHECK (rc == 1 && e.ip == ip (100, 64, 0, 6)
	   && strcmp (e.name, "pppoe-4471  ") == 0,
	 "leading space and a tab are separators; the identity is the rest "
	 "of the line: %s",
	 e.name);

  printf ("\n--- what is not a line, and what is not an error ---\n");
  CHECK (bpa_identity_parse_line ("# a comment", &e) == 0
	   && bpa_identity_parse_line ("", &e) == 0
	   && bpa_identity_parse_line ("   \n", &e) == 0,
	 "comments and blank lines yield no entry and no refusal");
  CHECK (bpa_identity_parse_line ("100.64.0.5", &e) == -1,
	 "an address with no identity is refused, not stored as empty");
  CHECK (bpa_identity_parse_line ("not-an-address here", &e) == -1
	   && bpa_identity_parse_line ("300.1.2.3 x", &e) == -1,
	 "a line this box cannot read is refused, because the next line of "
	 "that file may be wrong too");

  printf ("\n--- a name longer than the field is truncated, and says so ---\n");
  {
    char line[128];
    snprintf (line, sizeof (line), "100.64.0.9 %s",
	      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
    rc = bpa_identity_parse_line (line, &e);
    CHECK (rc == 2 && strlen (e.name) == BPA_IDENTITY_MAX - 1,
	   "the subscriber exists, so a partial name beats no record: "
	   "rc=%d len=%zu",
	   rc, strlen (e.name));
  }

  printf ("\n--- the search: the identity in force for an address ---\n");
  {
    bpa_identity_entry_t es[4];
    bpa_identity_table_t t = { 0 };
    u32 i;

    const char *lines[] = { "100.64.0.9 nine", "100.64.0.1 one",
			    "100.64.0.5 five", "100.64.1.200 many" };
    for (i = 0; i < 4; i++)
      CHECK (bpa_identity_parse_line (lines[i], &es[i]) == 1,
	     "fixture line %u read", i);
    qsort (es, 4, sizeof (es[0]), bpa_identity_cmp);
    t.e = es;
    t.n = 4;

    CHECK (es[0].ip < es[1].ip && es[1].ip < es[2].ip && es[2].ip < es[3].ip,
	   "the table is sorted by address, whatever order the file had");
    /* The comparator by name, because qsort above proves it orders and not
     * what it answers: a comparator that returned 0 for every pair would sort
     * a file that was already sorted and lose every other one. */
    {
      bpa_identity_entry_t lo = { .ip = 1 }, hi = { .ip = 2 };

      CHECK (bpa_identity_cmp (&lo, &hi) < 0
	       && bpa_identity_cmp (&hi, &lo) > 0
	       && bpa_identity_cmp (&lo, &lo) == 0,
	     "the comparator orders by address and calls equal equal");
    }

    bpa_identity_lookup (&t, ip (100, 64, 0, 5), out, sizeof (out));
    CHECK (strcmp (out, "five") == 0, "the middle entry: %s", out);
    bpa_identity_lookup (&t, ip (100, 64, 0, 1), out, sizeof (out));
    CHECK (strcmp (out, "one") == 0, "the first: %s", out);
    bpa_identity_lookup (&t, ip (100, 64, 1, 200), out, sizeof (out));
    CHECK (strcmp (out, "many") == 0, "the last: %s", out);

    bpa_identity_lookup (&t, ip (100, 64, 0, 2), out, sizeof (out));
    CHECK (strcmp (out, bpa_identity_unknown) == 0,
	   "an address between two entries is unknown, not the neighbour: %s",
	   out);
    bpa_identity_lookup (&t, ip (10, 0, 0, 1), out, sizeof (out));
    CHECK (strcmp (out, bpa_identity_unknown) == 0,
	   "and so is one below everything the table holds: %s", out);
    bpa_identity_lookup (&t, ip (255, 255, 255, 255), out, sizeof (out));
    CHECK (strcmp (out, bpa_identity_unknown) == 0,
	   "and one above it: %s", out);
  }

  printf ("\n--- an ISC dhcpd lease file, which a provider already has ---\n");
  {
    const char *file[] = {
      "# written by isc-dhcp-server",
      "lease 100.64.0.5 {",
      "  starts 4 2026/09/18 06:00:00;",
      "  binding state active;",
      "  hardware ethernet bc:24:11:ea:db:12;",
      "  client-hostname \"cpe-12345\";",
      "}",
      "lease 100.64.0.6 {",
      "  binding state active;",
      "  hardware ethernet 02:00:00:00:00:07;",
      "}",
      "lease 100.64.0.7 {",
      "  binding state free;",
      "  hardware ethernet 02:00:00:00:00:09;",
      "  client-hostname \"gone-last-week\";",
      "}",
      "lease 100.64.0.8 {",
      "  binding state active;",
      "}",
    };
    bpa_identity_entry_t got[8];
    u32 n = 0, i;
    int in_block = 0;

    for (i = 0; i < sizeof (file) / sizeof (file[0]); i++)
      if (bpa_identity_lease_line (file[i], &got[n], &in_block) == 1)
	n++;

    CHECK (n == 2, "two of the four leases yield an entry: %u", n);
    CHECK (got[0].ip == ip (100, 64, 0, 5)
	     && strcmp (got[0].name, "cpe-12345") == 0,
	   "the client-hostname is the identity when there is one: %s",
	   got[0].name);
    CHECK (got[1].ip == ip (100, 64, 0, 6)
	     && strcmp (got[1].name, "02:00:00:00:00:07") == 0,
	   "and the hardware address when there is not: %s", got[1].name);
    /* THE ONE THAT MATTERS. A lease file holds every lease the server ever
     * wrote, and a `free` one still carries the old client's name. Naming a
     * subscriber from one of those is the wrong answer to a court order with
     * a straight face. */
    CHECK (n == 2 || got[2].ip != ip (100, 64, 0, 7),
	   "a lease that is not active yields nothing, whatever name it "
	   "still carries");
    CHECK (in_block == 0, "the parser ends outside a block, as the file does");
  }

  printf ("\n--- one subscriber at a time, from an accounting stream ---\n");
  {
    bpa_identity_table_t t = { 0 };
    int found;

    CHECK (bpa_identity_upsert (&t, ip (100, 64, 0, 5), "joao@isp") == 1
	     && t.n == 1,
	   "the first entry: n=%u", t.n);
    bpa_identity_lookup (&t, ip (100, 64, 0, 5), out, sizeof (out));
    CHECK (strcmp (out, "joao@isp") == 0, "and it is found: %s", out);

    /* Out of order, because an accounting stream is in nobody's order. */
    bpa_identity_upsert (&t, ip (100, 64, 0, 9), "nine@isp");
    bpa_identity_upsert (&t, ip (100, 64, 0, 1), "one@isp");
    bpa_identity_upsert (&t, ip (100, 64, 0, 7), "seven@isp");
    CHECK (t.n == 4 && t.e[0].ip < t.e[1].ip && t.e[1].ip < t.e[2].ip
	     && t.e[2].ip < t.e[3].ip,
	   "the table stays sorted whatever order the events arrive in");
    bpa_identity_lookup (&t, ip (100, 64, 0, 7), out, sizeof (out));
    CHECK (strcmp (out, "seven@isp") == 0, "each is found after: %s", out);

    /* An Interim for a session whose name changed, and one that changed
     * nothing: the second must not report a change, because a box that
     * reports every interim as a change has a counter nobody can read. */
    CHECK (bpa_identity_upsert (&t, ip (100, 64, 0, 7), "seven@isp") == 0,
	   "the same name again is not a change");
    CHECK (bpa_identity_upsert (&t, ip (100, 64, 0, 7), "7@isp") == 1,
	   "a different name for one address replaces it");
    bpa_identity_lookup (&t, ip (100, 64, 0, 7), out, sizeof (out));
    CHECK (strcmp (out, "7@isp") == 0, "and the later one wins: %s", out);

    /* The Stop. */
    CHECK (bpa_identity_remove (&t, ip (100, 64, 0, 7)) == 1 && t.n == 3,
	   "a stop removes the entry: n=%u", t.n);
    bpa_identity_lookup (&t, ip (100, 64, 0, 7), out, sizeof (out));
    CHECK (strcmp (out, bpa_identity_unknown) == 0,
	   "and the address reads as nobody's, not as a blank name: %s", out);
    CHECK (bpa_identity_remove (&t, ip (100, 64, 0, 7)) == 0,
	   "a stop for a session already gone changes nothing");
    bpa_identity_at (&t, ip (100, 64, 0, 9), &found);
    CHECK (found, "and what is left is still findable");
    CHECK (bpa_identity_upsert (&t, ip (100, 64, 0, 5), "") == 0,
	   "an empty name is not an identity and is not stored");
  }

  printf ("\n--- the key sort, which is what makes a renewal win ---\n");
  {
    /* A lease file names an address again every time it is renewed, and the
     * later line is the current subscriber. qsort is not stable, so the order
     * is carried in the key rather than hoped for. */
    u64 k[3] = { ((u64) 5 << 32) | 9, ((u64) 5 << 32) | 2,
		 ((u64) 4 << 32) | 7 };

    CHECK (bpa_identity_key_cmp (&k[0], &k[1]) > 0
	     && bpa_identity_key_cmp (&k[1], &k[0]) < 0
	     && bpa_identity_key_cmp (&k[0], &k[0]) == 0,
	   "the key comparator orders whole keys, address above line");
    qsort (k, 3, sizeof (k[0]), bpa_identity_key_cmp);
    CHECK (k[0] == (((u64) 4 << 32) | 7) && k[1] == (((u64) 5 << 32) | 2)
	     && k[2] == (((u64) 5 << 32) | 9),
	   "address first, then the line it was on: %llu %llu %llu",
	   (unsigned long long) k[0], (unsigned long long) k[1],
	   (unsigned long long) k[2]);
  }

  printf ("\n--- a table that is not there answers, rather than crashing ---\n");
  {
    bpa_identity_table_t empty = { 0 };

    bpa_identity_lookup (&empty, ip (100, 64, 0, 5), out, sizeof (out));
    CHECK (strcmp (out, bpa_identity_unknown) == 0,
	   "no table is 'unknown': %s", out);
    bpa_identity_lookup (0, ip (100, 64, 0, 5), out, sizeof (out));
    CHECK (strcmp (out, bpa_identity_unknown) == 0,
	   "and so is no pointer at all: %s", out);
    /* The allocation path calls this. A box whose identity file was never
     * loaded still emits every record, which is this issue's own rule: a
     * missing record is worse than an incomplete one. */
  }

  printf ("\n=== what a file's lines mean once they are read ===\n");
  /*
   * Three decisions, and all three are about naming the RIGHT subscriber in
   * a record a court may read. They were inside the loader, which needs a
   * file and VPP's vectors; they need neither, and nothing drove them.
   */
  {
    bpa_identity_entry_t v[6];
    bpa_identity_entry_t scratch[6];
    u64 keys[6];
    u32 at = 0, n;

    /* A lease file as dhcpd leaves one: the same address renewed, with the
     * CURRENT subscriber on the later line. Deliberately out of address
     * order, which is how a lease file comes. */
    memset (v, 0, sizeof (v));
    v[0].ip = ip (10, 0, 0, 2); strcpy (v[0].name, "b-old");
    v[1].ip = ip (10, 0, 0, 1); strcpy (v[1].name, "a");
    v[2].ip = ip (10, 0, 0, 2); strcpy (v[2].name, "b-now");
    v[3].ip = ip (10, 0, 0, 3); strcpy (v[3].name, "c");

    bpa_identity_sort_by_address (v, 4, keys, scratch);
    CHECK (v[0].ip == ip (10, 0, 0, 1) && v[3].ip == ip (10, 0, 0, 3),
	   "the entries come back in address order");
    CHECK (strcmp (v[1].name, "b-old") == 0
	     && strcmp (v[2].name, "b-now") == 0,
	   "and within one address the file's own order is kept - an "
	   "unstable sort would keep whichever line it happened to leave "
	   "last, which for a renewal is the wrong subscriber");

    /* THE LAST LINE WINS, on a lease file. */
    n = bpa_identity_collapse_leases (v, 4);
    CHECK (n == 3, "the renewed address is one entry now, not two: %u left", n);
    CHECK (v[1].ip == ip (10, 0, 0, 2)
	     && strcmp (v[1].name, "b-now") == 0,
	   "and it kept '%s', the subscriber on the later line - the "
	   "earlier one is who used to have the address", v[1].name);

    /* AND IN A STATIC MAP THE SAME SHAPE IS REFUSED. Two lines claiming one
     * address are two answers to the question a court asks, and picking the
     * later one would be this box deciding which subscriber to name. */
    memset (v, 0, sizeof (v));
    v[0].ip = ip (10, 0, 0, 1); strcpy (v[0].name, "a");
    v[1].ip = ip (10, 0, 0, 2); strcpy (v[1].name, "b");
    v[2].ip = ip (10, 0, 0, 2); strcpy (v[2].name, "b-again");
    v[3].ip = ip (10, 0, 0, 3); strcpy (v[3].name, "c");
    CHECK (bpa_identity_dup_at (v, 4, &at) == 1 && at == 2,
	   "a map naming one address twice is refused, at the second of the "
	   "pair (at=%u)", at);
    CHECK (bpa_identity_dup_at (v, 2, &at) == 0,
	   "and a map with no duplicate is not refused");
    CHECK (bpa_identity_dup_at (v, 0, &at) == 0, "nor an empty one");

    /* A file with one line, and one with none: neither is a duplicate and
     * neither is a sort. */
    n = bpa_identity_collapse_leases (v, 1);
    CHECK (n == 1, "one lease stays one: %u", n);
    bpa_identity_sort_by_address (v, 1, keys, scratch);
    CHECK (v[0].ip == ip (10, 0, 0, 1), "and one entry is where it was after the sort");
  }

  printf ("\n--- NAT64: delegations, and the subscriber they cover (#485) ---\n");
  {
    bpa_identity_table_t t = { 0 };
    char out[BPA_IDENTITY_MAX];
    u8 a56[16] = { 0x20, 0x01, 0x0d, 0xb8, 0x12, 0x00, 0xab };
    u8 b56[16] = { 0x20, 0x01, 0x0d, 0xb8, 0x12, 0x00, 0xac };
    u8 c48[16] = { 0x20, 0x01, 0x0d, 0xb8, 0x13, 0x00 };
    u8 c56[16] = { 0x20, 0x01, 0x0d, 0xb8, 0x13, 0x00, 0x05 };
    u8 a60[16] = { 0x20, 0x01, 0x0d, 0xb8, 0x12, 0x00, 0xab, 0x10 };
    u8 host[16] = { 0x20, 0x01, 0x0d, 0xb8, 0x12, 0x00, 0xab, 0, 0, 0,
		    0, 0, 0, 0, 0, 0x10 };

    CHECK (bpa_identity6_upsert (&t, a56, 56, "ana@isp") == 1
	     && bpa_identity6_upsert (&t, c48, 48, "corp@isp") == 1
	     && bpa_identity6_upsert (&t, b56, 56, "bia@isp") == 1 && t.n6 == 3,
	   "three delegations, in any order: %u", t.n6);
    CHECK (memcmp (t.e6[0].prefix, a56, 16) == 0
	     && memcmp (t.e6[0].prefix, t.e6[1].prefix, 16) < 0
	     && memcmp (t.e6[1].prefix, t.e6[2].prefix, 16) < 0,
	   "kept sorted, which the bisection depends on");
    bpa_identity_lookup6 (&t, a56, 56, out, sizeof (out));
    CHECK (!strcmp (out, "ana@isp"), "a /56 subscriber delegated a /56: %s",
	   out);
    bpa_identity_lookup6 (&t, c56, 56, out, sizeof (out));
    CHECK (!strcmp (out, "corp@isp"), "a /56 inside a delegated /48: %s", out);
    bpa_identity_lookup6 (&t, host, 56, out, sizeof (out));
    CHECK (!strcmp (out, "ana@isp"),
	   "host bits in the subscriber's address do not matter: %s", out);
    CHECK (bpa_identity6_upsert (&t, a56, 56, "ana@isp") == 0,
	   "the same delegation again changes nothing");

    /* A /60 inside A's /56 replaces it: the newer delegation is the one in
     * force, and two overlapping entries would break the bisection. */
    CHECK (bpa_identity6_upsert (&t, a60, 60, "ana2@isp") == 1 && t.n6 == 3,
	   "an overlapping delegation replaces the one it overlaps: %u", t.n6);
    bpa_identity_lookup6 (&t, a56, 56, out, sizeof (out));
    CHECK (!strcmp (out, bpa_identity_unknown),
	   "and a /60 covers only part of a /56 subscriber, so it names it "
	   "not at all: %s", out);
    CHECK (bpa_identity6_remove (&t, b56, 56) == 1
	   && bpa_identity6_remove (&t, b56, 56) == 0 && t.n6 == 2,
	   "a Stop removes its delegation, once");
    bpa_identity_lookup6 (&t, b56, 56, out, sizeof (out));
    CHECK (!strcmp (out, bpa_identity_unknown), "and its subscriber is unknown "
	   "again: %s", out);
    CHECK (bpa_identity6_at (&t, c48) == 2,
	   "at() is the first entry starting after the address");
    vec_free (t.e6);
  }
  {
    /* The four helpers the table is made of, each on its own. */
    u8 a[16] = { 0x20, 0x01, 0x0d, 0xb8, 0x12, 0x00, 0xab, 0xcd, 0xff };
    u8 m[16], n56[16] = { 0x20, 0x01, 0x0d, 0xb8, 0x12, 0x00, 0xab };
    u8 n48[16] = { 0x20, 0x01, 0x0d, 0xb8, 0x12, 0x00 };
    u8 o56[16] = { 0x20, 0x01, 0x0d, 0xb8, 0x12, 0x00, 0xac };
    bpa_identity_table_t t = { 0 };

    bpa_identity6_mask (m, a, 56);
    CHECK (memcmp (m, n56, 16) == 0, "mask clears every bit past the length");
    bpa_identity6_mask (m, a, 60);
    CHECK (m[7] == 0xc0 && m[8] == 0, "and inside an octet: %02x", m[7]);
    CHECK (bpa_identity6_covers (n48, 48, n56, 56)
	     && !bpa_identity6_covers (n56, 56, n48, 48)
	     && !bpa_identity6_covers (o56, 56, n56, 56),
	   "covers: a /48 covers its /56, not the other way, not a neighbour");
    CHECK (bpa_identity6_overlap (n48, 48, n56, 56)
	     && bpa_identity6_overlap (n56, 56, n48, 48)
	     && !bpa_identity6_overlap (o56, 56, n56, 56),
	   "overlap: either containing the other, never neighbours");
    bpa_identity6_upsert (&t, n56, 56, "a");
    bpa_identity6_upsert (&t, o56, 56, "b");
    bpa_identity6_cut (&t, 0);
    CHECK (t.n6 == 1 && !strcmp (t.e6[0].name, "b"),
	   "cut takes one entry out and keeps the order of the rest");
    vec_free (t.e6);
  }

  printf ("\n%s\n", fail ? "FAILURES" : "ALL TESTS PASSED");
  return fail ? 1 : 0;
}
