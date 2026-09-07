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
   * Also to VPP's log: a local file survives a reboot, not a lost disk.
   * Anyone who takes this seriously ships VPP's log to a remote syslog, and
   * then the trail leaves the box.
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

static clib_error_t *
bpa_show_history_command_fn (vlib_main_t *vm, unformat_input_t *input,
			     vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  u8 buf[4096];
  int fd, n;

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
  .short_help = "show bpa pool history",
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
