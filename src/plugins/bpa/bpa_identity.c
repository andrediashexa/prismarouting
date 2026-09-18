/*
 * bpa_identity.c - which subscriber a private address belonged to, at the
 * instant a block was allocated to it (#21).
 *
 * WHY THIS EXISTS. A court order names a person; the log names a private
 * address at an instant. Joining the two needs a second system and a manual
 * step, performed under time pressure by whoever is on call - which is
 * exactly the condition under which a wrong answer gets sent. Marco Civil
 * asks for the connection record for a year, and a record nobody can resolve
 * to a subscriber satisfies the letter and misses the point.
 *
 * WHAT IT IS, AND WHAT IT IS NOT. A sorted array of (inside address,
 * identity), searched by bisection. Not a hash table, and the reason is the
 * shape of the data rather than a preference: the table is written whole by
 * the control plane and read on the allocation path, so the cost that matters
 * is the read, a sorted array has no worst case worth naming at this size,
 * and it needs no allocator on the read side at all.
 *
 * IT HOLDS PERSONAL DATA, which is the reason the log it feeds is protected.
 * So it is readable only through the one function that copies an identity
 * into a record, and nothing here prints an entry: `show` says how many
 * entries there are and when they were read, and that is all any screen on
 * this box will say.
 *
 * Standalone, the way bpa_ftp.c's text half is: parsing, sorting and
 * searching read no packet, no session and no pool.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef BPA_STANDALONE
#include "bpa_shim.h"
#else
#include "bpa.h"
#endif
#include "bpa_identity.h"

/*
 * What a record carries when this box does not know who held the address.
 *
 * A VALUE AND NOT AN ABSENT FIELD, which is this issue's own requirement read
 * carefully: a collector has to be able to tell "this box does not know" from
 * "this box did not say", and a missing field says the second about a box
 * that meant the first.
 */
const char bpa_identity_unknown[] = "unknown";

int
bpa_identity_cmp (const void *a, const void *b)
{
  const bpa_identity_entry_t *x = a, *y = b;

  return x->ip < y->ip ? -1 : x->ip > y->ip ? 1 : 0;
}

/*
 * One line of the map file: `<inside address> <identity>`.
 *
 * Returns 1 when the line yielded an entry, 0 when it was a comment or blank,
 * and -1 when it is neither - a line this box cannot read is REFUSED rather
 * than skipped, because a provisioning system that writes a malformed line is
 * one whose next line may be malformed too, and a table silently missing a
 * subscriber is the failure this file exists to prevent.
 */
int
bpa_identity_parse_line (const char *line, bpa_identity_entry_t *out)
{
  unsigned a, b, c, d;
  int at = 0;
  const char *p = line;

  while (*p == ' ' || *p == '\t')
    p++;
  if (*p == 0 || *p == '#' || *p == '\n')
    return 0;
  if (sscanf (p, "%u.%u.%u.%u %n", &a, &b, &c, &d, &at) < 4 || !at)
    return -1;
  if (a > 255 || b > 255 || c > 255 || d > 255)
    return -1;
  p += at;
  while (*p == ' ' || *p == '\t')
    p++;
  if (*p == 0 || *p == '\n')
    return -1;

  out->ip = (a << 24) | (b << 16) | (c << 8) | d;
  {
    u32 n = 0;

    while (p[n] && p[n] != '\n' && p[n] != '\r' && n < BPA_IDENTITY_MAX - 1)
      {
	out->name[n] = p[n];
	n++;
      }
    out->name[n] = 0;
    /* A name longer than the field is TRUNCATED and said so by the caller's
     * count, not refused: the subscriber exists and the record naming them
     * partly is worth more than no record. The bound is documented where the
     * statement is. */
    if (p[n] && p[n] != '\n' && p[n] != '\r')
      return 2;
  }
  return 1;
}

/*
 * The identity in force for `ip`, copied into `out`, or the unknown marker.
 *
 * COPIED and not returned by pointer, because the caller is the allocation
 * path and what it is building is a record of an instant: a pointer into a
 * table the control plane may rewrite a moment later would name whoever holds
 * the address by the time the log is drained, which is the defect this issue
 * names in as many words.
 */
void
bpa_identity_lookup (const bpa_identity_table_t *t, u32 ip, char *out, u32 cap)
{
  u32 lo = 0, hi;

  if (!cap)
    return;
  out[0] = 0;
  if (!t || !t->n || !t->e)
    {
      snprintf (out, cap, "%s", bpa_identity_unknown);
      return;
    }
  hi = t->n - 1;
  while (lo <= hi)
    {
      u32 mid = lo + (hi - lo) / 2;

      if (t->e[mid].ip == ip)
	{
	  snprintf (out, cap, "%s", t->e[mid].name);
	  return;
	}
      if (t->e[mid].ip < ip)
	lo = mid + 1;
      else if (mid == 0)
	break;
      else
	hi = mid - 1;
    }
  snprintf (out, cap, "%s", bpa_identity_unknown);
}
