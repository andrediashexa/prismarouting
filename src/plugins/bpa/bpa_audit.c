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
 */

#include <fcntl.h>
#include <time.h>
#include <unistd.h>

#include <vppinfra/crypto/sha2.h>

#include "bpa.h"

#ifndef BPA_AUDIT_PATH_DEFAULT
#define BPA_AUDIT_PATH_DEFAULT "/var/log/hexarouting/config-history.log"
#endif

static u8 *
bpa_audit_format_pool (u8 *s, bpa_pool_t *p)
{
  ip4_address_t a;
  u32 i;

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
      a.as_u32 = clib_host_to_net_u32 (p->statics[i].ip_priv);
      s = format (s, " forward %U/%u->%U:%u", format_bpa_proto,
		  p->statics[i].proto, p->statics[i].port_pub,
		  format_ip4_address, &a, p->statics[i].port_priv);
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
   * is attributed by it, uid 0 included - hexash resolves the account even
   * when it is root, so a genuine root operator still appears as "root uid
   * 0". A client that sends neither a name nor a non-zero uid is recorded as
   * unattributed, which is the honest reading of an empty message.
   */
  bm->actor_uid = (!bm->actor_user && uid == 0) ? ~0u : uid;
  bm->actor_is_api = 1;
}

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
static void
bpa_audit_digest (const u8 *prev, const u8 *content, uword len, u8 *out)
{
  clib_sha2_ctx_t ctx;

  clib_sha2_init (&ctx, CLIB_SHA2_256);
  clib_sha2_update (&ctx, prev, 32);
  clib_sha2_update (&ctx, content, len);
  clib_sha2_final (&ctx, out);
}

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

/*
 * Where the chain had got to, read back from the file at startup.
 *
 * Without this a restart begins a second chain, and every verification from
 * then on reports a break at the restart - which trains whoever runs it to
 * ignore the answer, and an alarm that is always on is an alarm that is off.
 */
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

void
bpa_audit (const char *op, bpa_pool_t *p, u8 *name_only)
{
  bpa_main_t *bm = &bpa_main;
  struct tm tm;
  time_t now;
  u8 *line = 0;
  int fd;

  now = (time_t) unix_time_now ();
  gmtime_r (&now, &tm);

  line = format (0, "%04d-%02d-%02dT%02d:%02d:%02dZ %s pool %v",
		 tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday, tm.tm_hour,
		 tm.tm_min, tm.tm_sec, op, p ? p->name : name_only);
  /* Before the definition, not after it: the definition is open-ended - it
   * grows a field with every feature - and an identity at the end of a line
   * of unknown length is one a reader has to go looking for. */
  line = bpa_audit_format_actor (line);
  if (p)
    line = bpa_audit_format_pool (line, p);

  /*
   * Sequence, then digest. The digest covers everything on the line up to and
   * including the sequence number - not the digest field itself, which cannot
   * cover itself - and the digest of the entry before it.
   */
  line = format (line, " seq %llu", bm->audit_seq + 1);
  {
    u8 digest[32];

    bpa_audit_digest (bm->audit_head, line, vec_len (line), digest);
    clib_memcpy (bm->audit_head, digest, 32);
    bm->audit_seq++;
    line = format (line, " sha256 %U", format_hex_bytes, digest, 32);
  }

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
      vlib_log_err (bm->log_class,
		    "cannot open the configuration trail at %v: the proof for "
		    "deterministic mode depends on it",
		    bm->audit_path);
      goto done;
    }

  line = format (line, "\n");
  if (write (fd, line, vec_len (line)) != (ssize_t) vec_len (line))
    vlib_log_err (bm->log_class, "incomplete write to the configuration trail");
  else
    fsync (fd); /* compliance: it must not live only in the cache */
  close (fd);

done:
  vec_free (line);
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
static void
bpa_audit_verify (vlib_main_t *vm)
{
  bpa_main_t *bm = &bpa_main;
  u8 prev[32] = { 0 }, digest[32], stored[32];
  u8 *data = 0;
  char *line, *next, *end;
  u64 seq = 0, expect = 0, unchained = 0, checked = 0;
  int fd, n, broken = 0;
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
  .short_help = "bpa audit-path [<caminho>]",
  .function = bpa_audit_path_command_fn,
};
