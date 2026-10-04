/*
 * bpa_audit.c - dated configuration trail.
 *
 * In deterministic mode there is no allocation event to log: the proof of who
 * used (public IP, port, instant) is the algorithm plus the pool
 * configuration. That only holds while it is known WHICH configuration was in
 * force at that instant. If the pool changed on the 10th and the court order
 * asks about the 5th, the current formula answers the wrong question with
 * complete confidence.
 *
 * So every pool change is recorded, dated in UTC, in an append-only file. The
 * record carries the complete definition, so that it can be read back without
 * depending on anything else.
 *
 * The pools are not the whole of it: where they are APPLIED changes the same
 * answer. An interface with no CGNAT on it translates nothing, so the trail
 * records attaching and detaching it too.
 */

/*
 * Standalone, the same way bpa_ftp.h is, and for ONE function: the hex
 * reader. Everything else here formats with VPP's format(), digests with
 * vppinfra's SHA-2, or writes to the CLI, so under BPA_STANDALONE only
 * bpa_audit_unhex() is compiled. It earns the guard on its own - it is the
 * function that decides whether a line of the trail carries a digest at all,
 * and it is the only place the chain's evidence is turned back into bytes.
 * The VPP build defines BPA_STANDALONE nowhere, so every guard below takes
 * its `#else` branch and the file preprocesses exactly as it did before.
 */
#ifdef BPA_STANDALONE
#include "bpa_shim.h"
#else
#include <fcntl.h>
#include <time.h>
#include <unistd.h>

#include <vppinfra/crypto/sha2.h>

#include "bpa.h"

#ifndef BPA_AUDIT_PATH_DEFAULT
#define BPA_AUDIT_PATH_DEFAULT "/var/log/prisma-routing/config-history.log"
#endif

/* not a unit: it formats a pool with VPP's format() into a u8 vector, and is
 * not compiled at all under BPA_STANDALONE - the guard above says why. */
static u8 *
bpa_audit_format_pool (u8 *s, bpa_pool_t *p)
{
  ip4_address_t a;
  u32 i;

  /*
   * A NAT64 pool by what it was configured with (#485): its IPv6 inside,
   * the subscriber length and the NAT64 prefix. Its IPv4 inside is the
   * pseudo range [0, N) the arithmetic runs on, and a trail entry reading
   * "inside 0.0.0.0+65536" is a definition nobody can recompute a
   * subscriber from - which, for a deterministic pool, is the one thing
   * this trail is for.
   */
  if (p->is_nat64)
    {
      for (i = 0; i < p->n_inside6; i++)
	s = format (s, " inside %U/%u", format_ip6_address,
		    (ip6_address_t *) p->inside6_addr[i], p->inside6_len[i]);
      s = format (s, " subscriber-length %u nat64-prefix %U/%u",
		  p->inside6.sub_len, format_ip6_address,
		  (ip6_address_t *) p->nat64_prefix, p->nat64_len);
    }
  else
    for (i = 0; i < p->inside.n_ranges; i++)
      {
	a.as_u32 = clib_host_to_net_u32 (p->inside.ranges[i].base);
	s = format (s, " inside %U+%u", format_ip4_address, &a,
		    p->inside.ranges[i].count);
      }
  for (i = 0; i < p->outside.n_ranges; i++)
    {
      a.as_u32 = clib_host_to_net_u32 (p->outside.ranges[i].base);
      s = format (s, " outside %U+%u", format_ip4_address, &a,
		  p->outside.ranges[i].count);
    }
  s = format (s, " block %u ports %u-%u policy %U mode %U", p->block_size,
	      p->port_min, p->port_max, format_bpa_policy, p->policy,
	      format_bpa_mode, p->mode);

  /*
   * The reserve is part of the FORMULA, not a preference about it: it decides
   * how many blocks of each public address the mapping may use, and under the
   * blocked policy it is the divisor. A reader recomputing who held an
   * address and port on the date in a court order gets a different answer
   * without it, and gets it confidently.
   *
   * Written only when there is one. An entry with no `reserve` was written by
   * a pool that had none, which is the value the arithmetic takes when the
   * field is absent - and is what every entry written before this existed
   * means.
   */
  if (p->block_reserve)
    s = format (s, " reserve %u", p->block_reserve);

  for (i = 0; i < p->bypass_src.n_ranges; i++)
    {
      a.as_u32 = clib_host_to_net_u32 (p->bypass_src.ranges[i].base);
      s = format (s, " bypass-src %U+%u", format_ip4_address, &a,
		  p->bypass_src.ranges[i].count);
    }
  for (i = 0; i < p->bypass_dst.n_ranges; i++)
    {
      a.as_u32 = clib_host_to_net_u32 (p->bypass_dst.ranges[i].base);
      s = format (s, " bypass-dst %U+%u", format_ip4_address, &a,
		  p->bypass_dst.ranges[i].count);
    }

  if (p->max_sessions || p->max_rate)
    s = format (s, " limits sessions %u rate %u", p->max_sessions,
		p->max_rate);

  /*
   * The forwards, in full.
   *
   * They are the one part of a pool that is NOT derivable from the formula:
   * everything else here lets a reader recompute who held an address and port
   * at a given instant, and a forwarded port is an exception the arithmetic
   * cannot produce. A trail that omits them answers a court order confidently
   * and wrongly for exactly those ports.
   */
  for (i = 0; i < vec_len (p->statics); i++)
    {
      /* A NAT64 forward by its host (#485): the trail is what answers who
       * held a forwarded port, and an index answers nobody. */
      s = format (s, " forward %U/%u->%U:%u", format_bpa_proto,
		  p->statics[i].proto, p->statics[i].port_pub,
		  format_bpa_static_host, &p->statics[i],
		  p->statics[i].port_priv);
    }
  return s;
}

/*
 * Who asked, as it goes on the line.
 *
 * Three shapes, and the third is the one that matters: an entry that could not
 * be attributed says so. Leaving the field off instead would make it
 * indistinguishable from an entry written before this existed - and from one
 * where nobody was accountable.
 *
 *   by alice uid 1001 via api      the client named an account
 *   by uid 1001 via api            it sent only a number
 *   by unattributed via api        it sent neither
 *   by unattributed via cli        it did not come through the API at all,
 *                                  which is what a change typed at the VPP
 *                                  CLI looks like: there is no operator to
 *                                  name, because there is no session to ask.
 */
/* not a unit: it reads the actor out of bpa_main and formats it with VPP's
 * format(); not compiled under BPA_STANDALONE. */
static u8 *
bpa_audit_format_actor (u8 *s)
{
  bpa_main_t *bm = &bpa_main;

  if (bm->actor_user && vec_len (bm->actor_user))
    {
      s = format (s, " by %v", bm->actor_user);
      if (bm->actor_uid != ~0u)
	s = format (s, " uid %u", bm->actor_uid);
    }
  else if (bm->actor_uid != ~0u)
    s = format (s, " by uid %u", bm->actor_uid);
  else
    s = format (s, " by unattributed");

  return format (s, " via %s", bm->actor_is_api ? "api" : "cli");
}

/* not a unit: it stores the actor the API handed in, in bpa_main, for the next
 * entry to name; not compiled under BPA_STANDALONE. */
void
bpa_actor_set (const char *user, u32 uid)
{
  bpa_main_t *bm = &bpa_main;

  vec_free (bm->actor_user);
  if (user && user[0])
    bm->actor_user = format (0, "%s", user);

  /*
   * A field a client did not send arrives as ZERO, and zero is a real uid:
   * root. Taken at face value, every client that sends nothing would be
   * recorded as the most privileged account on the box - not missing
   * attribution but FALSE attribution, which is the one outcome worse than
   * none.
   *
   * The NAME is what says an identity was supplied. A client that sends one
   * is attributed by it, uid 0 included - prismash resolves the account even
   * when it is root, so a genuine root operator still appears as "root uid
   * 0". A client that sends neither a name nor a non-zero uid is recorded as
   * unattributed, which is the honest reading of an empty message.
   */
  bm->actor_uid = (!bm->actor_user && uid == 0) ? ~0u : uid;
  bm->actor_is_api = 1;
}

/* not a unit: it drops that actor again, in bpa_main; not compiled under
 * BPA_STANDALONE. */
void
bpa_actor_clear (void)
{
  bpa_main_t *bm = &bpa_main;

  vec_free (bm->actor_user);
  bm->actor_uid = ~0u;
  bm->actor_is_api = 0;
}

/*
 * The hash chain.
 *
 * Each entry carries a sequence number and a digest over the PREVIOUS digest
 * followed by its own text. Changing a byte anywhere changes that entry's
 * digest, and every digest after it, so an alteration cannot be made to
 * verify by fixing up the line it is on.
 *
 * WHAT THIS DOES AND DOES NOT PROVE, since a reader is entitled to know
 * before relying on it:
 *
 *   It detects a trail that was EDITED - a line changed, a line removed from
 *   the middle - and it names the entry where the chain first breaks. That is
 *   the case this exists for: a record that can be quietly corrected is a
 *   record that proves what happened only to somebody who already trusts the
 *   operator, and it exists to be shown to somebody who does not.
 *
 *   It does NOT stop somebody who has write access to the file AND this
 *   algorithm from rewriting the trail and recomputing every digest. Nothing
 *   kept only on the box can: an attacker who owns the box owns the evidence
 *   on it. What makes the chain evidence against its own author is a copy of
 *   the digests somewhere they do not control - and that copy already exists,
 *   because every entry is also sent to VPP's log, which deployments that
 *   take this seriously forward to a remote syslog. The digest travels with
 *   it. Comparing the head digest here against the one in yesterday's remote
 *   log is what turns "the file verifies" into "the file is the same one".
 *
 *   TRUNCATION at the end cannot be detected by reading the file. The
 *   remaining links are genuine, so the chain verifies. It is caught by
 *   comparing against what this process remembers writing - which is why the
 *   head and sequence are recovered at startup - and after a restart that
 *   followed a truncation, only the remote copy can tell.
 */
/* not a unit: it chains the entry with vppinfra's SHA-2, which is the one piece
 * of this file a standalone build has no implementation of. */
static void
bpa_audit_digest (const u8 *prev, const u8 *content, uword len, u8 *out)
{
  clib_sha2_ctx_t ctx;

  clib_sha2_init (&ctx, CLIB_SHA2_256);
  clib_sha2_update (&ctx, prev, 32);
  clib_sha2_update (&ctx, content, len);
  clib_sha2_final (&ctx, out);
}

#endif /* not BPA_STANDALONE */

/* "<64 hex>" -> 32 bytes. Returns 0 when the text is not a digest. */
static int
bpa_audit_unhex (const char *hex, u8 *out)
{
  int i;

  for (i = 0; i < 32; i++)
    {
      int hi = -1, lo = -1, k;

      for (k = 0; k < 2; k++)
	{
	  char c = hex[i * 2 + k];
	  int v;

	  if (c >= '0' && c <= '9')
	    v = c - '0';
	  else if (c >= 'a' && c <= 'f')
	    v = c - 'a' + 10;
	  else
	    return 0;
	  if (k == 0)
	    hi = v;
	  else
	    lo = v;
	}
      out[i] = (u8) ((hi << 4) | lo);
    }
  return 1;
}

#ifndef BPA_STANDALONE

/*
 * Where the chain had got to, read back from the file at startup.
 *
 * Without this a restart begins a second chain, and every verification from
 * then on reports a break at the restart - which trains whoever runs it to
 * ignore the answer, and an alarm that is always on is an alarm that is off.
 */
/* not a unit: it opens the trail on disk and reads the chain back at startup;
 * not compiled under BPA_STANDALONE, and it needs the file. */
void
bpa_audit_recover (void)
{
  bpa_main_t *bm = &bpa_main;
  u8 buf[65536];
  char *line, *p;
  int fd, n;

  fd = open ((char *) bm->audit_path, O_RDONLY);
  if (fd < 0)
    return;

  /* The tail is enough: only the last entry is needed, and a trail that has
   * been running for a year should not be read into memory to find it. */
  if (lseek (fd, 0, SEEK_END) > (off_t) sizeof (buf) - 1)
    lseek (fd, -(off_t) (sizeof (buf) - 1), SEEK_END);
  else
    lseek (fd, 0, SEEK_SET);

  n = read (fd, buf, sizeof (buf) - 1);
  close (fd);
  if (n <= 0)
    return;
  buf[n] = 0;

  /* Last complete line. */
  while (n > 0 && buf[n - 1] == '\n')
    buf[--n] = 0;
  line = (char *) buf;
  for (p = (char *) buf + n - 1; p >= (char *) buf; p--)
    if (*p == '\n')
      {
	line = p + 1;
	break;
      }

  p = strstr (line, " sha256 ");
  if (!p || strlen (p + 8) < 64)
    return; /* a trail written before the chain existed */
  if (!bpa_audit_unhex (p + 8, bm->audit_head))
    return;

  p = strstr (line, " seq ");
  if (p)
    bm->audit_seq = strtoull (p + 5, 0, 10);
}

/*
 * One entry, from the date to the digest.
 *
 * Every kind of change goes through here rather than formatting a line of its
 * own. An entry that carried different fields from its neighbours - or the
 * same fields in a different order - cannot be compared with them, and
 * comparing them is the whole of what a reader does with this file. It is
 * also the only place the chain advances, so a change that wrote its own line
 * would either skip a sequence number or break every digest after it.
 *
 * `kind` and `subject` are what was changed - "pool residential",
 * "interface GigabitEthernet0/0/0". `detail` is everything the change needs
 * to be read back on its own, and goes AFTER the identity for the same reason
 * the pool definition does: it is open-ended, and an identity at the end of a
 * line of unknown length is one a reader has to go looking for.
 */
/* not a unit: it writes an entry to the trail, digesting and appending to a
 * file; not compiled under BPA_STANDALONE. */
static void
bpa_audit_entry (const char *op, const char *kind, u8 *subject, bpa_pool_t *p,
		 const char *detail)
{
  bpa_main_t *bm = &bpa_main;
  struct tm tm;
  time_t now;
  u8 *line = 0;
  int fd;
  off_t at;

  now = (time_t) unix_time_now ();
  gmtime_r (&now, &tm);

  line = format (0, "%04d-%02d-%02dT%02d:%02d:%02dZ %s %s %v",
		 tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday, tm.tm_hour,
		 tm.tm_min, tm.tm_sec, op, kind, subject);
  /* Before the definition, not after it: the definition is open-ended - it
   * grows a field with every feature - and an identity at the end of a line
   * of unknown length is one a reader has to go looking for. */
  line = bpa_audit_format_actor (line);
  if (p)
    line = bpa_audit_format_pool (line, p);
  if (detail)
    line = format (line, "%s", detail);

  /*
   * Sequence, then digest. The digest covers everything on the line up to and
   * including the sequence number - not the digest field itself, which cannot
   * cover itself - and the digest of the entry before it.
   */
  line = format (line, " seq %llu", bm->audit_seq + 1);
  u8 digest[32];
  bpa_audit_digest (bm->audit_head, line, vec_len (line), digest);
  line = format (line, " sha256 %U", format_hex_bytes, digest, 32);
  /*
   * The chain is NOT advanced here, and that is the whole of #236.
   *
   * It used to advance before the write and whether or not the write happened.
   * One entry the box could not write then left the head pointing at a line
   * the file does not contain and the sequence one ahead of it, so the next
   * entry that DID get written landed with a gap - and `bpa_audit_verify`
   * reported "1 entry was removed from the middle" over a file nobody had
   * touched. Measured on a box whose records filesystem had gone read-only,
   * which is what ext4 does to itself when the disk under it fails:
   *
   *   BROKEN before entry 5
   *     the sequence jumps from 3 to 5: 1 entry was removed from the middle
   *
   * That is the exact misdiagnosis the paragraph above bpa_audit_verify says
   * the function was built to avoid, produced by the writer rather than by an
   * attacker, and handed to whoever is investigating a court order.
   *
   * So the head advances below, only once the bytes are in the file, and a
   * failure is counted in audit_lost instead. What is in the file is then
   * always a chain that verifies, and the entry that never made it is
   * reported as what it is.
   */

  /*
   * Also to VPP's log: a local file survives a reboot, not a lost disk.
   * Anyone who takes this seriously ships VPP's log to a remote syslog, and
   * then the trail leaves the box - and so does the digest, which is what
   * makes the chain evidence against somebody who can rewrite the file.
   */
  vlib_log_notice (bm->log_class, "%v", line);

  fd = open ((char *) bm->audit_path, O_WRONLY | O_APPEND | O_CREAT, 0640);
  if (fd < 0)
    {
      bm->audit_lost++;
      vlib_log_err (bm->log_class,
		    "cannot open the configuration trail at %v: the proof for "
		    "deterministic mode depends on it",
		    bm->audit_path);
      goto done;
    }

  /*
   * Where the file ended before this entry, so a short write can be taken
   * back. A filesystem that fills mid-write returns fewer bytes than it was
   * given and leaves a fragment behind: no newline, no digest, and both
   * readers then treat it as a line written before the chain existed, which
   * is a wrong answer to give an investigator. lseek on an O_APPEND fd is the
   * length, and it is taken before the write for that reason.
   */
  at = lseek (fd, 0, SEEK_END);

  line = format (line, "\n");
  if (write (fd, line, vec_len (line)) != (ssize_t) vec_len (line))
    {
      bm->audit_lost++;
      if (at >= 0)
	{
	  if (ftruncate (fd, at) != 0)
	    vlib_log_err (bm->log_class,
			  "a partly written entry is in the trail and could "
			  "not be removed: the file ends mid-entry");
	}
      vlib_log_err (bm->log_class,
		    "incomplete write to the configuration trail: entry %llu "
		    "was not recorded",
		    bm->audit_seq + 1);
    }
  else
    {
      fsync (fd); /* compliance: it must not live only in the cache */
      clib_memcpy (bm->audit_head, digest, 32);
      bm->audit_seq++;
    }
  close (fd);

done:
  vec_free (line);
}

/* not a unit: the variadic front door to that write; not compiled under
 * BPA_STANDALONE. */
void
bpa_audit (const char *op, bpa_pool_t *p, u8 *name_only)
{
  bpa_audit_entry (op, "pool", p ? p->name : name_only, p, 0);
}

/*
 * Attaching or detaching CGNAT on an interface.
 *
 * Not a pool change, and recorded all the same: detaching `out2in` stops
 * translation for every subscriber behind that interface, and a trail that
 * showed the pools but not where they were applied would show the box
 * changing without showing what changed (#124). The direction is on the line
 * because the entry has to be readable on its own - "attach interface X" does
 * not say which half of the translation was attached.
 */
/* not a unit: the same write for an interface change, formatting the
 * interface with VPP's format(); not compiled under BPA_STANDALONE. */
void
bpa_audit_interface (const char *op, u8 *ifname, int is_inside)
{
  bpa_audit_entry (op, "interface", ifname, 0,
		   is_inside ? " direction inside" : " direction outside");
}

/*
 * Walk the trail and report the FIRST place the chain breaks.
 *
 * "The trail has been tampered with" is not an actionable sentence. Which
 * entry, on what date, and in which of the three ways is: an entry whose text
 * no longer matches its digest was altered; a gap in the sequence means an
 * entry was removed; and a file that simply stops early was truncated, which
 * the file itself cannot tell you.
 */
/* not a unit: it re-reads the trail from disk and recomputes the chain over
 * it; not compiled under BPA_STANDALONE, and it needs the file. */
static void
bpa_audit_verify (vlib_main_t *vm)
{
  bpa_main_t *bm = &bpa_main;
  u8 prev[32] = { 0 }, digest[32], stored[32];
  u8 *data = 0;
  char *line, *next, *end;
  u64 seq = 0, expect = 0, unchained = 0, checked = 0;
  int fd, n, broken = 0, last_nl;
  u8 buf[65536];

  fd = open ((char *) bm->audit_path, O_RDONLY);
  if (fd < 0)
    {
      vlib_cli_output (vm, "no trail at %v", bm->audit_path);
      return;
    }
  while ((n = read (fd, buf, sizeof (buf))) > 0)
    vec_add (data, buf, n);
  close (fd);
  vec_add1 (data, 0);

  line = (char *) data;
  end = (char *) data + vec_len (data) - 1;
  /* Whether the file's last byte closed its last line. Read once, before the
   * loop chops the buffer into strings. */
  last_nl = (end > (char *) data && end[-1] == '\n');

  for (; line < end && *line; line = next)
    {
      char *sha, *sq;
      u64 this_seq;
      uword content_len;

      next = strchr (line, '\n');
      if (next)
	*next++ = 0;
      else
	next = end;

      sha = strstr (line, " sha256 ");
      if (!sha || strlen (sha + 8) < 64)
	{
	  /*
	   * The LAST line with no digest is a different event from any other
	   * line with no digest, and they used to be reported the same way.
	   *
	   * An entry written before the chain existed is followed by the
	   * entries that came after it. A fragment is the end of the file: no
	   * newline closed it, so it is what a write that stopped half way
	   * leaves behind. Calling that "written before the chain existed"
	   * tells an investigator the trail is old where it is actually cut
	   * (#236).
	   */
	  if (next >= end && !last_nl)
	    {
	      vlib_cli_output (vm, "  TRUNCATED: the file ends mid-entry");
	      vlib_cli_output (vm,
			       "    %.*s", (int) (end - line), line);
	      vlib_cli_output (vm,
			       "    a write stopped part way through this "
			       "entry. It is not an entry written before the "
			       "chain existed, and it is not one somebody "
			       "removed");
	      broken = 1;
	      break;
	    }
	  /* Written before the chain existed. Readable, and counted, but not
	   * covered: saying so is the difference between "this trail is
	   * intact" and "this part of it is not claimed to be". */
	  unchained++;
	  continue;
	}

      content_len = (uword) (sha - line);
      sq = strstr (line, " seq ");
      this_seq = sq ? strtoull (sq + 5, 0, 10) : 0;

      /*
       * The SEQUENCE is checked first, and the order is the diagnosis.
       *
       * Removing an entry also breaks the digest of the entry that followed
       * it - its predecessor is no longer the one it was chained to - so a
       * verifier that checks the hash first reports every removal as an
       * alteration of the innocent line after it. Which is both the wrong
       * entry and the wrong event, and would send whoever is investigating to
       * look at a line nobody touched.
       */
      if (expect && this_seq != expect)
	{
	  vlib_cli_output (vm, "  BROKEN before entry %llu", this_seq);
	  vlib_cli_output (vm,
			   "    the sequence jumps from %llu to %llu: %llu "
			   "entr%s removed from the middle",
			   expect - 1, this_seq, this_seq - expect,
			   this_seq - expect == 1 ? "y was" : "ies were");
	  broken = 1;
	  break;
	}

      bpa_audit_digest (prev, (const u8 *) line, content_len, digest);

      if (!bpa_audit_unhex (sha + 8, stored)
	  || clib_memcmp (stored, digest, 32))
	{
	  vlib_cli_output (vm, "  BROKEN at entry %llu", this_seq);
	  /* The whole entry, not a preview. Somebody reading this is
	   * investigating what was changed, and the change is as likely to be
	   * at the end of the line as at the start - a truncated excerpt hides
	   * exactly the part they came for. */
	  vlib_cli_output (vm, "    %.*s", (int) content_len, line);
	  vlib_cli_output (vm, "    this entry does not match its own digest: "
			       "its text was altered after it was written");
	  broken = 1;
	  break;
	}

      clib_memcpy (prev, digest, 32);
      seq = this_seq;
      expect = this_seq + 1;
      checked++;
    }

  vec_free (data);

  if (unchained)
    vlib_cli_output (vm,
		     "  %llu entr%s carr%s no digest: written before the "
		     "chain existed, readable but not covered",
		     unchained, unchained == 1 ? "y" : "ies",
		     unchained == 1 ? "ies" : "y");

  /*
   * Said whether or not the chain verifies, and said in these words.
   *
   * The file cannot carry this: the news is precisely that a write to it
   * failed. Only this process knows, and only until it restarts. A chain that
   * verifies over a file missing entries is a true statement about the file
   * and a false impression about the box, so the count goes out with the
   * verdict rather than beside it (#236).
   *
   * "could not be written" and not "removed": nobody removed anything, and an
   * investigator sent looking for whoever edited the file is an investigator
   * sent to the wrong place.
   */
  if (bm->audit_lost)
    vlib_cli_output (vm,
		     "  INCOMPLETE: %llu entr%s could not be written since "
		     "this dataplane started, so %s missing from this file "
		     "and %s nowhere else either. Check the disk under %v",
		     bm->audit_lost, bm->audit_lost == 1 ? "y" : "ies",
		     bm->audit_lost == 1 ? "it is" : "they are",
		     bm->audit_lost == 1 ? "it is" : "they are",
		     bm->audit_path);

  if (broken)
    return;

  if (!checked)
    {
      /*
       * Not "intact". A file with nothing to check has not been verified, and
       * reporting it as clean is how a trail that lost its chain entirely -
       * the one thing an attacker would try first - reads as healthy.
       */
      vlib_cli_output (vm, "  no chained entries: nothing here is covered");
      return;
    }

  vlib_cli_output (vm, "  %llu entr%s checked, chain intact", checked,
		   checked == 1 ? "y" : "ies");

  /*
   * The half the file cannot answer. Entries removed from the END leave a
   * chain in which every remaining link is genuine, so the file verifies. It
   * is caught here only because this process remembers what it wrote.
   */
  if (bm->audit_seq > seq)
    vlib_cli_output (vm,
		     "  TRUNCATED: the file ends at entry %llu and this "
		     "dataplane wrote up to %llu - %llu entr%s removed from "
		     "the end",
		     seq, bm->audit_seq, bm->audit_seq - seq,
		     bm->audit_seq - seq == 1 ? "y was" : "ies were");
  else if (checked)
    {
      vlib_cli_output (vm, "  head  seq %llu sha256 %U", seq,
		       format_hex_bytes, prev, 32);
      vlib_cli_output (vm,
		       "  a chain that verifies proves the file was not "
		       "edited, not that it is the same file: compare this "
		       "digest with the copy in your remote syslog");
    }
}

/* not a unit: a VPP CLI command handler: it takes an unformat_input_t
 * and writes to a vlib_main_t's CLI. lab/audit.sh drives it on a box. */
static clib_error_t *
bpa_show_history_command_fn (vlib_main_t *vm, unformat_input_t *input,
			     vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  u8 buf[4096];
  int fd, n;

  if (unformat (input, "verify"))
    {
      vlib_cli_output (vm, "configuration trail: %v", bm->audit_path);
      bpa_audit_verify (vm);
      return 0;
    }

  fd = open ((char *) bm->audit_path, O_RDONLY);
  if (fd < 0)
    {
      vlib_cli_output (vm, "no trail at %v", bm->audit_path);
      return 0;
    }

  vlib_cli_output (vm, "configuration trail: %v", bm->audit_path);
  while ((n = read (fd, buf, sizeof (buf) - 1)) > 0)
    {
      buf[n] = 0;
      vlib_cli_output (vm, "%s", buf);
    }
  close (fd);
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_history_command, static) = {
  .path = "show bpa pool history",
  .short_help = "show bpa pool history [verify]",
  .function = bpa_show_history_command_fn,
};

/* not a unit: a VPP CLI command handler, the same class as the one
 * above. lab/audit.sh drives it on a box. */
static clib_error_t *
bpa_audit_path_command_fn (vlib_main_t *vm, unformat_input_t *input,
			   vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  u8 *path = 0;

  if (unformat (input, "%v", &path))
    {
      /*
       * NUL-terminate it. A vec from unformat is not a C string, and this
       * path is handed straight to open(): without the terminator the plugin
       * opens whatever bytes follow it in memory, which fails or succeeds
       * depending on the allocator - the worst way for anything to behave,
       * and how this was found (a lab pointing audit-path at a copy got "no
       * trail" for two files and read the third correctly).
       */
      vec_add1 (path, 0);
      vec_dec_len (path, 1);
      vec_free (bm->audit_path);
      bm->audit_path = path;
      return 0;
    }

  vlib_cli_output (vm, "%v", bm->audit_path);
  return 0;
}

VLIB_CLI_COMMAND (bpa_audit_path_command, static) = {
  .path = "bpa audit-path",
  .short_help = "bpa audit-path [<path>]",
  .function = bpa_audit_path_command_fn,
};

#endif /* not BPA_STANDALONE */
