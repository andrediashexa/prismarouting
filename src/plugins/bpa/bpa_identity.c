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


#ifndef BPA_STANDALONE

/*
 * Build the table from a file the control plane names.
 *
 * WHOLE OR NOT AT ALL. The new table is built beside the old one and swapped
 * in under the worker barrier only when every line read: a table half loaded
 * is a set of subscribers whose records would say "unknown" for a reason
 * nobody would ever find, and the operator asked for a file rather than for
 * as much of one as happened to parse.
 *
 * The count and the instant are what `show` reports. The entries are not, and
 * never will be: this is personal data, and its presence in the log is the
 * reason the log is protected (#21).
 */
clib_error_t *
bpa_identity_load (const char *path)
{
  bpa_main_t *bm = &bpa_main;
  bpa_identity_entry_t *fresh = 0, e;
  u32 line_no = 0, truncated = 0;
  clib_error_t *err = 0;
  char line[512];
  FILE *f;

  if (!path || !path[0])
    {
      /* Asked for nothing: the table goes away and every record says
       * "unknown" again, which is a state an operator can mean. */
      vlib_worker_thread_barrier_sync (bm->vlib_main);
      vec_free (bm->identity.e);
      vec_free (bm->identity.path);
      bm->identity.n = 0;
      bm->identity.n_truncated = 0;
      bm->identity.read_at = 0;
      vlib_worker_thread_barrier_release (bm->vlib_main);
      return 0;
    }

  f = fopen (path, "r");
  if (!f)
    return clib_error_return (0, "cannot read '%s'", path);

  while (fgets (line, sizeof (line), f))
    {
      int rc;

      line_no++;
      rc = bpa_identity_parse_line (line, &e);
      if (rc == 0)
	continue;
      if (rc < 0)
	{
	  err = clib_error_return (
	    0, "'%s' line %u is neither a comment nor '<address> <identity>'",
	    path, line_no);
	  goto done;
	}
      if (rc == 2)
	truncated++;
      vec_add1 (fresh, e);
    }

  if (vec_len (fresh) > 1)
    qsort (fresh, vec_len (fresh), sizeof (fresh[0]), bpa_identity_cmp);
  /* A duplicate address is refused rather than resolved by position: two
   * lines claiming one address are two answers to the question a court asks,
   * and picking the later one would be this box deciding which subscriber to
   * name. */
  {
    u32 i;

    for (i = 1; i < vec_len (fresh); i++)
      if (fresh[i].ip == fresh[i - 1].ip)
	{
	  err = clib_error_return (
	    0, "'%s' names %u.%u.%u.%u twice, and two identities for one "
	       "address is two answers to the same question",
	    path, (fresh[i].ip >> 24) & 0xff, (fresh[i].ip >> 16) & 0xff,
	    (fresh[i].ip >> 8) & 0xff, fresh[i].ip & 0xff);
	  goto done;
	}
  }

  vlib_worker_thread_barrier_sync (bm->vlib_main);
  vec_free (bm->identity.e);
  bm->identity.e = fresh;
  bm->identity.n = vec_len (fresh);
  bm->identity.n_truncated = truncated;
  bm->identity.read_at = unix_time_now ();
  vec_free (bm->identity.path);
  bm->identity.path = format (0, "%s%c", path, 0);
  vlib_worker_thread_barrier_release (bm->vlib_main);
  fresh = 0;

done:
  fclose (f);
  vec_free (fresh);
  return err;
}

#endif /* not BPA_STANDALONE */
