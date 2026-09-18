/*
 * bpa_identity_table.c - the WRITER half of the identity table (#21).
 *
 * Split from bpa_identity.c along the line that matters to a harness: the
 * reader - the parse, the bisection, the unknown marker - touches nothing but
 * its arguments, and three test programs compile it. The writer grows and
 * shrinks a VPP vector, so a harness that takes it needs the vector shim, and
 * a harness that only reads should not have to.
 *
 * Nothing about the rules lives here twice: the bisection both halves use is
 * bpa_identity_at(), next door.
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
 * One subscriber, taken from an accounting stream rather than from a file.
 *
 * WHY THE TABLE IS UPDATED IN PLACE HERE and rebuilt whole in the loader
 * above: a file is a statement of the world and arrives complete, so the only
 * safe way to take it is whole or not at all. An Accounting-Start is an EVENT
 * about one subscriber, and rebuilding a 200000-entry table for each of them
 * would be a box that spends its afternoon copying arrays.
 *
 * The cost of in-place is a memmove per event, which is what a sorted array
 * charges for an insert. Accounting is a few events a second on a large box
 * and the table is read on the allocation path, which is the side that had to
 * be cheap.
 *
 * Returns 1 when the table changed.
 */
int
bpa_identity_upsert (bpa_identity_table_t *t, u32 ip, const char *name)
{
  int found;
  u32 at;

  if (!t || !name || !name[0])
    return 0;
  at = bpa_identity_at (t, ip, &found);
  if (found)
    {
      if (!strcmp (t->e[at].name, name))
	return 0;
      snprintf (t->e[at].name, sizeof (t->e[at].name), "%s", name);
      return 1;
    }
  {
    bpa_identity_entry_t blank = { 0 };

    vec_add1 (t->e, blank);
    t->n = vec_len (t->e);
    if (at + 1 < t->n)
      memmove (&t->e[at + 1], &t->e[at],
	       (t->n - at - 1) * sizeof (t->e[0]));
    t->e[at].ip = ip;
    snprintf (t->e[at].name, sizeof (t->e[at].name), "%s", name);
  }
  return 1;
}

/*
 * The subscriber left: their address belongs to nobody until somebody else
 * takes it.
 *
 * REMOVED AND NOT BLANKED. An entry holding an empty name would read as a
 * subscriber this box knows and cannot name, and the record would say so;
 * what is true is that nobody is there, which is what `unknown` says.
 */
int
bpa_identity_remove (bpa_identity_table_t *t, u32 ip)
{
  int found;
  u32 at;

  if (!t || !t->n)
    return 0;
  at = bpa_identity_at (t, ip, &found);
  if (!found)
    return 0;
  if (at + 1 < t->n)
    memmove (&t->e[at], &t->e[at + 1],
	     (t->n - at - 1) * sizeof (t->e[0]));
  vec_set_len (t->e, t->n - 1);
  t->n = vec_len (t->e);
  return 1;
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
  return bpa_identity_load_kind (path, BPA_IDENTITY_MAP);
}

clib_error_t *
bpa_identity_load_kind (const char *path, bpa_identity_kind_t kind)
{
  bpa_main_t *bm = &bpa_main;
  bpa_identity_entry_t *fresh = 0, e;
  u32 line_no = 0, truncated = 0;
  int in_lease = 0;
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
      rc = kind == BPA_IDENTITY_DHCP_LEASES
	     ? bpa_identity_lease_line (line, &e, &in_lease)
	     : bpa_identity_parse_line (line, &e);
      if (rc == 0)
	continue;
      if (rc < 0)
	{
	  err = clib_error_return (
	    0,
	    kind == BPA_IDENTITY_DHCP_LEASES
	      ? "'%s' line %u is not a lease this box can read"
	      : "'%s' line %u is neither a comment nor '<address> <identity>'",
	    path, line_no);
	  goto done;
	}
      if (rc == 2)
	truncated++;
      vec_add1 (fresh, e);
    }

  /*
   * SORTED BY ADDRESS, AND THEN BY WHERE THE LINE WAS, which qsort alone
   * cannot give: it is not stable, so a lease file whose address appears
   * twice would keep whichever of the two the sort happened to leave last.
   * For a renewal that is the difference between the current subscriber and
   * the one before them.
   *
   * The keys are (address << 32 | line order) in a u64, sorted as numbers, so
   * the tiebreak is exact and costs eight bytes per entry for the length of
   * this function rather than four in every entry for the life of the table.
   */
  if (vec_len (fresh) > 1)
    {
      u64 *keys = 0;
      bpa_identity_entry_t *sorted = 0;
      u32 i;

      vec_validate (keys, vec_len (fresh) - 1);
      for (i = 0; i < vec_len (fresh); i++)
	keys[i] = ((u64) fresh[i].ip << 32) | i;
      qsort (keys, vec_len (keys), sizeof (keys[0]), bpa_identity_key_cmp);
      vec_validate (sorted, vec_len (fresh) - 1);
      for (i = 0; i < vec_len (keys); i++)
	sorted[i] = fresh[keys[i] & 0xffffffffu];
      vec_free (keys);
      vec_free (fresh);
      fresh = sorted;
    }
  /* A duplicate address is refused rather than resolved by position: two
   * lines claiming one address are two answers to the question a court asks,
   * and picking the later one would be this box deciding which subscriber to
   * name. */
  {
    u32 i;

    /*
     * A LEASE FILE NAMES AN ADDRESS AGAIN EVERY TIME IT IS RENEWED, and the
     * later entry is the current one - so a duplicate there is ordinary and
     * the last one wins. In a static MAP a duplicate is two answers to one
     * question and is refused, which is the paragraph below. Two grammars,
     * two meanings for the same shape, and this is where they part.
     */
    if (kind == BPA_IDENTITY_DHCP_LEASES)
      {
	u32 w = 0;

	for (i = 0; i < vec_len (fresh); i++)
	  if (i + 1 == vec_len (fresh) || fresh[i].ip != fresh[i + 1].ip)
	    fresh[w++] = fresh[i];
	vec_set_len (fresh, w);
      }
    for (i = 1; kind == BPA_IDENTITY_MAP && i < vec_len (fresh); i++)
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
  bm->identity.kind = kind;
  vlib_worker_thread_barrier_release (bm->vlib_main);
  fresh = 0;

done:
  fclose (f);
  vec_free (fresh);
  return err;
}

#endif /* not BPA_STANDALONE */
