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
#include "bpa_identity.c"

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

  printf ("\n%s\n", fail ? "FAILURES" : "ALL TESTS PASSED");
  return fail ? 1 : 0;
}
