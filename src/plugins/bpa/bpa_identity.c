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

/* (address, line order) packed into a u64, so the sort is stable by
 * construction: see bpa_identity_load_kind. */
int
bpa_identity_key_cmp (const void *a, const void *b)
{
  u64 x = *(const u64 *) a, y = *(const u64 *) b;

  return x < y ? -1 : x > y ? 1 : 0;
}

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
 * One line of an ISC dhcpd lease file, read as a small state machine.
 *
 * WHY A SECOND GRAMMAR AT ALL. The static map is what a provider writes when
 * they have a provisioning database; a provider running IPoE has the answer
 * already, in the lease file their DHCP server maintains, and asking them to
 * transcribe it into a second file is asking for a copy that goes stale (#21).
 *
 *   lease 100.64.0.5 {
 *     starts 4 2026/09/18 06:00:00;
 *     binding state active;
 *     hardware ethernet bc:24:11:ea:db:12;
 *     client-hostname "cpe-12345";
 *   }
 *
 * WHAT IS TAKEN AS THE IDENTITY, in this order: the client-hostname, then the
 * hardware address. A hostname is what a support desk recognises; a MAC is
 * what is always there. Nothing is invented when neither is: the lease
 * yields no entry, and the record for that address says `unknown` - which is
 * the honest answer and is a value the log already carries.
 *
 * BINDING STATE IS READ AND NOT ASSUMED. A lease file holds every lease the
 * server has ever written, including `free` and `expired` ones with the old
 * client's name still in them, and naming a subscriber from one of those is
 * the wrong answer to a court order with a straight face. Only `active`
 * yields an entry.
 *
 * Returns 1 when a lease closed and yielded an entry, 0 otherwise, and -1 for
 * a line this parser cannot make sense of inside a lease block.
 */
int
bpa_identity_lease_line (const char *line, bpa_identity_entry_t *out,
			 int *in_block)
{
  static bpa_identity_entry_t pending;
  static char host[BPA_IDENTITY_MAX], mac[BPA_IDENTITY_MAX];
  static int active;
  unsigned a, b, c, d;
  const char *p = line;

  while (*p == ' ' || *p == '\t')
    p++;

  if (!*in_block)
    {
      if (sscanf (p, "lease %u.%u.%u.%u {", &a, &b, &c, &d) == 4)
	{
	  if (a > 255 || b > 255 || c > 255 || d > 255)
	    return -1;
	  pending.ip = (a << 24) | (b << 16) | (c << 8) | d;
	  pending.name[0] = host[0] = mac[0] = 0;
	  active = 0;
	  *in_block = 1;
	}
      return 0;
    }

  if (*p == '}')
    {
      *in_block = 0;
      if (!active)
	return 0;
      /* The hostname first, then the hardware address: a support desk
       * recognises the first and the second is always there. */
      snprintf (pending.name, sizeof (pending.name), "%s",
		host[0] ? host : mac);
      if (!pending.name[0])
	return 0;
      *out = pending;
      return 1;
    }

  if (!strncmp (p, "binding state ", 14))
    active = !strncmp (p + 14, "active", 6);
  else if (!strncmp (p, "client-hostname ", 16))
    {
      const char *q = strchr (p, '"');
      u32 n = 0;

      if (q)
	{
	  q++;
	  while (*q && *q != '"' && n < sizeof (host) - 1)
	    host[n++] = *q++;
	}
      host[n] = 0;
    }
  else if (!strncmp (p, "hardware ethernet ", 18))
    {
      u32 n = 0;
      const char *q = p + 18;

      while (*q && *q != ';' && n < sizeof (mac) - 1)
	mac[n++] = *q++;
      mac[n] = 0;
    }
  return 0;
}


/*
 * Where `ip` belongs in a sorted table: the index of the entry that holds it,
 * or of the first entry above it. `*found` says which of the two.
 *
 * ONE SEARCH FOR BOTH READERS. The lookup below and the two updaters need the
 * same answer, and a second bisection written beside this one is a second
 * place for an off-by-one to live - which in this table means a subscriber
 * named as their neighbour.
 */
u32
bpa_identity_at (const bpa_identity_table_t *t, u32 ip, int *found)
{
  u32 lo = 0, hi;

  *found = 0;
  if (!t || !t->n || !t->e)
    return 0;
  lo = 0;
  hi = t->n;
  while (lo < hi)
    {
      u32 mid = lo + (hi - lo) / 2;

      if (t->e[mid].ip == ip)
	{
	  *found = 1;
	  return mid;
	}
      if (t->e[mid].ip < ip)
	lo = mid + 1;
      else
	hi = mid;
    }
  return lo;
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
  if (!cap)
    return;
  out[0] = 0;
  if (!t || !t->n || !t->e)
    {
      snprintf (out, cap, "%s", bpa_identity_unknown);
      return;
    }
  {
    int found;
    u32 at = bpa_identity_at (t, ip, &found);

    if (found)
      {
	snprintf (out, cap, "%s", t->e[at].name);
	return;
      }
  }
  snprintf (out, cap, "%s", bpa_identity_unknown);
}
