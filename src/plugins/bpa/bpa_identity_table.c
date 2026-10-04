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

/* The prefix with every bit past len cleared: a delegation is a network. */
static void
bpa_identity6_mask (u8 out[16], const u8 in[16], u8 len)
{
  u32 i;

  for (i = 0; i < 16; i++)
    {
      u32 bits = len > i * 8 ? len - i * 8 : 0;

      out[i] = bits >= 8 ? in[i] : (u8) (in[i] & (u8) (0xff << (8 - bits)));
    }
}

/* Entry i out, order kept: the same memmove the IPv4 remove makes. */
static void
bpa_identity6_cut (bpa_identity_table_t *t, u32 i)
{
  if (i + 1 < t->n6)
    memmove (&t->e6[i], &t->e6[i + 1], (t->n6 - i - 1) * sizeof (t->e6[0]));
  vec_set_len (t->e6, t->n6 - 1);
  t->n6 = vec_len (t->e6);
}

/* Whether two prefixes share an address. */
static int
bpa_identity6_overlap (const u8 a[16], u8 al, const u8 b[16], u8 bl)
{
  u8 m = al < bl ? al : bl, x[16], y[16];

  bpa_identity6_mask (x, a, m);
  bpa_identity6_mask (y, b, m);
  return !memcmp (x, y, 16);
}

/*
 * A delegation, from an Accounting-Start or Interim carrying
 * Delegated-IPv6-Prefix (#485). The table in place, for the IPv4 upsert's
 * reason.
 *
 * WHAT IT OVERLAPS GOES: a prefix delegated now is this subscriber's, and an
 * older entry covering any of it is a delegation that ended without its Stop
 * reaching this box. Keeping both would make the lookup's single bisection
 * wrong, and would answer a court with whichever of two people sorted first.
 *
 * Returns 1 when the table changed.
 */
int
bpa_identity6_upsert (bpa_identity_table_t *t, const u8 prefix[16], u8 len,
		      const char *name)
{
  bpa_identity6_entry_t e = { 0 };
  u32 i, at;

  if (!t || !name || !name[0] || len == 0 || len > 128)
    return 0;
  bpa_identity6_mask (e.prefix, prefix, len);
  e.len = len;
  snprintf (e.name, sizeof (e.name), "%s", name);
  for (i = 0; i < t->n6; i++)
    if (t->e6[i].len == len && !memcmp (t->e6[i].prefix, e.prefix, 16))
      {
	if (!strcmp (t->e6[i].name, e.name))
	  return 0;
	memcpy (t->e6[i].name, e.name, sizeof (e.name));
	return 1;
      }
  for (i = 0; i < t->n6;)
    if (bpa_identity6_overlap (t->e6[i].prefix, t->e6[i].len, e.prefix, len))
      bpa_identity6_cut (t, i);
    else
      i++;
  at = bpa_identity6_at (t, e.prefix);
  vec_add1 (t->e6, e);
  t->n6 = vec_len (t->e6);
  if (at + 1 < t->n6)
    {
      memmove (&t->e6[at + 1], &t->e6[at],
	       (t->n6 - at - 1) * sizeof (t->e6[0]));
      t->e6[at] = e;
    }
  return 1;
}

/* The delegation ended: its Accounting-Stop. Exact prefix only - a Stop
 * names what it ends. */
int
bpa_identity6_remove (bpa_identity_table_t *t, const u8 prefix[16], u8 len)
{
  u8 m[16];
  u32 i;

  if (!t || !t->n6)
    return 0;
  bpa_identity6_mask (m, prefix, len);
  for (i = 0; i < t->n6; i++)
    if (t->e6[i].len == len && !memcmp (t->e6[i].prefix, m, 16))
      {
	bpa_identity6_cut (t, i);
	return 1;
      }
  return 0;
}

/*
 * WHAT A FILE'S LINES MEAN ONCE THEY ARE READ, which is three decisions and
 * not one - and all three are about naming the right subscriber in a record
 * a court may read.
 *
 * They are here, above the guard, because the loader below needs a file and
 * VPP's vectors and these need neither: they take an array and its length,
 * and test/test_bpa_identity.c drives them.
 */

/*
 * Sorted by address, and within one address by WHERE THE LINE WAS.
 *
 * qsort alone cannot give that - it is not stable - so a lease file whose
 * address appears twice would keep whichever of the two the sort happened to
 * leave last. For a renewal that is the difference between the current
 * subscriber and the one before them.
 *
 * The keys are (address << 32 | line order) in a u64, sorted as numbers, so
 * the tiebreak is exact. `keys` and `scratch` are the caller's, both `n`
 * long: eight and sizeof(entry) bytes per entry for the length of this call,
 * rather than four more in every entry for the life of the table.
 */
static void
bpa_identity_sort_by_address (bpa_identity_entry_t *v, u32 n, u64 *keys,
			      bpa_identity_entry_t *scratch)
{
  u32 i;

  if (n < 2)
    return;
  for (i = 0; i < n; i++)
    keys[i] = ((u64) v[i].ip << 32) | i;
  qsort (keys, n, sizeof (keys[0]), bpa_identity_key_cmp);
  for (i = 0; i < n; i++)
    scratch[i] = v[keys[i] & 0xffffffffu];
  for (i = 0; i < n; i++)
    v[i] = scratch[i];
}

/*
 * A lease file names an address again every time it is renewed, and the
 * LATER entry is the current one - so a duplicate there is ordinary and the
 * last one wins. Returns how many entries are left.
 *
 * Reads the run of one address and keeps its last member, which is the last
 * LINE of that address because the sort above is stable. Taking the first
 * would name the subscriber who used to have the address.
 */
static u32
bpa_identity_collapse_leases (bpa_identity_entry_t *v, u32 n)
{
  u32 i, w = 0;

  for (i = 0; i < n; i++)
    if (i + 1 == n || v[i].ip != v[i + 1].ip)
      v[w++] = v[i];
  return w;
}

/*
 * In a static map a duplicate is two answers to one question and is refused:
 * picking the later one would be this box deciding which subscriber to name.
 * Returns 1 and the index of the second of the pair, or 0.
 */
static int
bpa_identity_dup_at (const bpa_identity_entry_t *v, u32 n, u32 *at)
{
  u32 i;

  for (i = 1; i < n; i++)
    if (v[i].ip == v[i - 1].ip)
      {
	*at = i;
	return 1;
      }
  return 0;
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
/* not a unit: the door the control plane uses, which picks the kind
 * and calls the loader below. lab/syslog.sh loads a map on a box and reads
 * the identity in the records that follow. */
clib_error_t *
bpa_identity_load (const char *path)
{
  return bpa_identity_load_kind (path, BPA_IDENTITY_MAP);
}

/* not a unit: it opens a file, reads it line by line and
 * rebuilds the table under the worker barrier. WHAT A LINE MEANS is
 * bpa_identity_parse_line() and bpa_identity_lease_line(), and what a whole
 * file means once read - the stable sort, the lease that wins, the map
 * duplicate refused - is the three functions above the guard; all five are
 * driven by test/test_bpa_identity.c. */
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
      bpa_identity_entry_t *scratch = 0;

      vec_validate (keys, vec_len (fresh) - 1);
      vec_validate (scratch, vec_len (fresh) - 1);
      bpa_identity_sort_by_address (fresh, vec_len (fresh), keys, scratch);
      vec_free (keys);
      vec_free (scratch);
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
    /* Not on an empty file (#490): an empty vector is a null pointer, and
     * VPP's vec_set_len writes the length through it - ASSERT (v) is gone
     * in a release build. Measured on 0.140.2: `cgnat identity dhcp-leases`
     * naming an empty file, which is what dhcpd writes before its first
     * lease, took the dataplane down with SIGSEGV. The compiler said so the
     * first time the plugin was built at -O2 (-Wstringop-overflow). */
#ifdef BPA_IDENTITY_EMPTY_OFF
    /* lab/identity-empty.sh's off mode builds the plugin this way, to show
     * the crash this guard exists for. No box is built with it. */
    if (kind == BPA_IDENTITY_DHCP_LEASES)
#else
    if (kind == BPA_IDENTITY_DHCP_LEASES && fresh)
#endif
      vec_set_len (fresh,
		   bpa_identity_collapse_leases (fresh, vec_len (fresh)));
    if (kind == BPA_IDENTITY_MAP
	&& bpa_identity_dup_at (fresh, vec_len (fresh), &i))
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
