/* bpa_api.c - the binary API implementation. */

#include <vnet/vnet.h>
#include <vlibapi/api.h>
#include <vlibmemory/api.h>
#include <vnet/ip/ip_types_api.h>
#include <vnet/format_fns.h>

#include "bpa.h"
#include "bpa_log.h"
#include <vnet/fib/fib_table.h>

#include <bpa/bpa.api_enum.h>
#include <bpa/bpa.api_types.h>

#define REPLY_MSG_ID_BASE bpa_main.msg_id_base
#include <vlibapi/api_helper_macros.h>

static void
vl_api_bpa_pool_add_del_t_handler (vl_api_bpa_pool_add_del_t *mp)
{
  vl_api_bpa_pool_add_del_reply_t *rmp;
  bpa_pool_t cfg = { 0 };
  /* The binary API has no ALG field yet, so a pool created through it gets
   * the defaults. Adding one is an API change and belongs with the ALG work
   * as a whole, not to whichever ALG lands first. */
  clib_error_t *e = 0;
  u32 *in_b = 0, *in_c = 0, *out_b = 0, *out_c = 0;
  u8 *name;
  int rv = 0, i;

  /* Before the change, cleared after it: an entry can then only ever carry
   * the identity of the request that produced it. */
  bpa_actor_set ((char *) mp->audit_user, mp->audit_uid);
  name = format (0, "%s", mp->name);
  /* Not zero: zero is VRF 0, and a client that says nothing must not bind the
   * pool to it. */
  cfg.fib_index = cfg.vrf_id = ~0u;
  cfg.out_fib_index = cfg.out_vrf_id = ~0u;

  if (!mp->is_add)
    {
      e = bpa_pool_del (name);
      goto done;
    }

  if (mp->n_inside > mp->n_prefixes)
    {
      rv = VNET_API_ERROR_INVALID_VALUE;
      goto done;
    }

  for (i = 0; i < mp->n_prefixes; i++)
    {
      ip_prefix_t p;
      u32 base, count;

      ip_prefix_decode2 (&mp->prefixes[i], &p);
      if (ip_prefix_version (&p) != AF_IP4)
	{
	  rv = VNET_API_ERROR_INVALID_ADDRESS_FAMILY;
	  goto done;
	}
      if (ip_prefix_len (&p) > 32)
	{
	  rv = VNET_API_ERROR_INVALID_VALUE;
	  goto done;
	}

      count = 1u << (32 - ip_prefix_len (&p));
      base = clib_net_to_host_u32 (ip_prefix_v4 (&p).as_u32) & ~(count - 1);

      if (i < mp->n_inside)
	{
	  vec_add1 (in_b, base);
	  vec_add1 (in_c, count);
	}
      else
	{
	  vec_add1 (out_b, base);
	  vec_add1 (out_c, count);
	}
    }

  e = bpa_addr_set_build (&cfg.inside, in_b, in_c, vec_len (in_b));
  if (e)
    goto done;
  e = bpa_addr_set_build (&cfg.outside, out_b, out_c, vec_len (out_b));
  if (e)
    goto done;

  cfg.name = name;
  cfg.block_size = clib_net_to_host_u16 (mp->block_size);
  cfg.port_min = clib_net_to_host_u16 (mp->port_min);
  cfg.port_max = clib_net_to_host_u16 (mp->port_max);
  cfg.policy = (bpa_policy_t) mp->policy;
  cfg.mode = (bpa_mode_t) mp->mode;
  cfg.filter = (bpa_filter_t) mp->filter;
  cfg.max_blocks = mp->max_blocks;

  /*
   * Two masks, as the pool stores them: what is enabled, and what the caller
   * actually stated an opinion about. A client that sends zero for both - one
   * written before these fields existed - gets the shipped defaults from
   * bpa_pool_init(), which is why the defaults are resolved there and not
   * assumed here.
   */
  cfg.alg = clib_net_to_host_u32 (mp->alg);
  cfg.alg_set = clib_net_to_host_u32 (mp->alg_set);

  cfg.timeouts.udp = clib_net_to_host_u32 (mp->timeouts.udp);
  cfg.timeouts.icmp = clib_net_to_host_u32 (mp->timeouts.icmp);
  cfg.timeouts.tcp_syn = clib_net_to_host_u32 (mp->timeouts.tcp_syn);
  cfg.timeouts.tcp_trans = clib_net_to_host_u32 (mp->timeouts.tcp_transitory);
  cfg.timeouts.tcp_estab = clib_net_to_host_u32 (mp->timeouts.tcp_established);
  cfg.timeouts.gre = clib_net_to_host_u32 (mp->timeouts.gre);
  cfg.timeouts.port_holddown =
    clib_net_to_host_u32 (mp->timeouts.port_holddown);
  cfg.timeouts.alg_expect = clib_net_to_host_u32 (mp->timeouts.alg_expect);

  cfg.tcp_mss = clib_net_to_host_u16 (mp->tcp_mss);
  cfg.tcp_mss_auto = mp->tcp_mss_auto;
  cfg.block_stride = clib_net_to_host_u16 (mp->block_stride);
  cfg.block_reserve = clib_net_to_host_u16 (mp->block_reserve);

  cfg.vrf_id = clib_net_to_host_u32 (mp->vrf_id);
  cfg.out_vrf_id = clib_net_to_host_u32 (mp->out_vrf_id);
  if (cfg.vrf_id != ~0u)
    {
      cfg.fib_index = fib_table_find (FIB_PROTOCOL_IP4, cfg.vrf_id);
      if (cfg.fib_index == ~0u)
	{
	  rv = VNET_API_ERROR_NO_SUCH_FIB;
	  goto done;
	}
    }
  if (cfg.out_vrf_id != ~0u)
    {
      cfg.out_fib_index = fib_table_find (FIB_PROTOCOL_IP4, cfg.out_vrf_id);
      if (cfg.out_fib_index == ~0u)
	{
	  rv = VNET_API_ERROR_NO_SUCH_FIB;
	  goto done;
	}
    }

  e = bpa_pool_add (&cfg, 0);

done:
  if (e)
    {
      /* The detailed message goes to the log; the client gets a code. */
      vlib_log_err (bpa_main.log_class, "%U", format_clib_error, e);
      clib_error_free (e);
      if (!rv)
	rv = VNET_API_ERROR_INVALID_VALUE;
    }
  vec_free (in_b);
  vec_free (in_c);
  vec_free (out_b);
  vec_free (out_c);
  vec_free (name);
  bpa_actor_clear ();
  REPLY_MACRO (VL_API_BPA_POOL_ADD_DEL_REPLY);
}

static void
vl_api_bpa_pool_settings_set_t_handler (vl_api_bpa_pool_settings_set_t *mp)
{
  vl_api_bpa_pool_settings_set_reply_t *rmp;
  bpa_timeouts_t t = { 0 };
  clib_error_t *e;
  u8 *name;
  int rv = 0;

  /* Before the change, cleared after it: an entry can then only ever carry
   * the identity of the request that produced it. */
  bpa_actor_set ((char *) mp->audit_user, mp->audit_uid);
  name = format (0, "%s", mp->name);

  t.udp = clib_net_to_host_u32 (mp->timeouts.udp);
  t.icmp = clib_net_to_host_u32 (mp->timeouts.icmp);
  t.tcp_syn = clib_net_to_host_u32 (mp->timeouts.tcp_syn);
  t.tcp_trans = clib_net_to_host_u32 (mp->timeouts.tcp_transitory);
  t.tcp_estab = clib_net_to_host_u32 (mp->timeouts.tcp_established);
  t.gre = clib_net_to_host_u32 (mp->timeouts.gre);
  t.port_holddown = clib_net_to_host_u32 (mp->timeouts.port_holddown);
  t.alg_expect = clib_net_to_host_u32 (mp->timeouts.alg_expect);

  e = bpa_pool_settings_set (name, (bpa_filter_t) mp->filter, mp->max_blocks,
			     clib_net_to_host_u32 (mp->alg),
			     clib_net_to_host_u32 (mp->alg_set),
			     clib_net_to_host_u16 (mp->tcp_mss),
			     mp->tcp_mss_auto, mp->util_alarm,
			     clib_net_to_host_u16 (mp->block_stride), &t);
  if (e)
    {
      vlib_log_err (bpa_main.log_class, "%U", format_clib_error, e);
      clib_error_free (e);
      rv = VNET_API_ERROR_INVALID_VALUE;
    }

  vec_free (name);
  bpa_actor_clear ();
  REPLY_MACRO (VL_API_BPA_POOL_SETTINGS_SET_REPLY);
}

static void
vl_api_bpa_bypass_set_t_handler (vl_api_bpa_bypass_set_t *mp)
{
  vl_api_bpa_bypass_set_reply_t *rmp;
  u32 *bases = 0, *counts = 0;
  clib_error_t *e = 0;
  u8 *name;
  int rv = 0, i;

  /* Before the change, cleared after it: an entry can then only ever carry
   * the identity of the request that produced it. */
  bpa_actor_set ((char *) mp->audit_user, mp->audit_uid);
  name = format (0, "%s", mp->name);

  for (i = 0; i < mp->n_prefixes; i++)
    {
      ip_prefix_t p;
      u32 count;

      ip_prefix_decode2 (&mp->prefixes[i], &p);
      if (ip_prefix_version (&p) != AF_IP4 || ip_prefix_len (&p) > 32)
	{
	  rv = VNET_API_ERROR_INVALID_VALUE;
	  goto done;
	}
      count = 1u << (32 - ip_prefix_len (&p));
      vec_add1 (counts, count);
      vec_add1 (bases,
		clib_net_to_host_u32 (ip_prefix_v4 (&p).as_u32) & ~(count - 1));
    }

  e = bpa_bypass_set (name, mp->is_destination, bases, counts,
		      vec_len (bases));

done:
  if (e)
    {
      vlib_log_err (bpa_main.log_class, "%U", format_clib_error, e);
      clib_error_free (e);
      if (!rv)
	rv = VNET_API_ERROR_INVALID_VALUE;
    }
  vec_free (bases);
  vec_free (counts);
  vec_free (name);
  bpa_actor_clear ();
  REPLY_MACRO (VL_API_BPA_BYPASS_SET_REPLY);
}

static void
vl_api_bpa_global_set_t_handler (vl_api_bpa_global_set_t *mp)
{
  vl_api_bpa_global_set_reply_t *rmp;
  bpa_main_t *bm = &bpa_main;
  int rv = 0;
  u32 v;

  /* A zero timeout is not a timeout, so zero means "leave it alone" and needs
   * no separate flag. Writing them one at a time rather than replacing the
   * struct: the caller sends what the configuration says, and what the
   * configuration does not mention is not the caller's to reset. */
#define SET_IF(field, msgfield)                                               \
  v = clib_net_to_host_u32 (mp->timeouts.msgfield);                           \
  if (v)                                                                      \
    bm->timeouts.field = v;

  SET_IF (udp, udp)
  SET_IF (icmp, icmp)
  SET_IF (tcp_syn, tcp_syn)
  SET_IF (tcp_trans, tcp_transitory)
  SET_IF (tcp_estab, tcp_established)
  SET_IF (gre, gre)
  SET_IF (port_holddown, port_holddown)
  SET_IF (alg_expect, alg_expect)
#undef SET_IF

  if (mp->set_block_holddown)
    bm->block_holddown = clib_net_to_host_u32 (mp->block_holddown);

  if (mp->set_session_table)
    {
      /* The same refusal the CLI makes, and for the same reason: the tables
       * are allocated when the first pool is created, and changing the sizing
       * afterwards would describe a table that is not the one in use. */
      if (pool_elts (bm->pools))
	{
	  /* Logged, not only coded. The client attaches the last line of
	   * VPP's log to a refusal so the operator gets a sentence instead of
	   * a number - and a handler that returns a code without writing one
	   * leaves it attaching somebody else's unrelated error. That is
	   * worse than no explanation: it sends the operator to debug a file
	   * permission when the tables are simply already allocated. */
	  vlib_log_err (bm->log_class,
			"session-table: tables already created; set this "
			"before configuring pools");
	  rv = VNET_API_ERROR_INSTANCE_IN_USE;
	}
      else
	{
	  bm->hash_buckets = clib_net_to_host_u32 (mp->session_buckets);
	  bm->hash_memory =
	    ((u64) clib_net_to_host_u32 (mp->session_memory_mb)) << 20;
	}
    }

  REPLY_MACRO (VL_API_BPA_GLOBAL_SET_REPLY);
}

static void
vl_api_bpa_logging_set_t_handler (vl_api_bpa_logging_set_t *mp)
{
  vl_api_bpa_logging_set_reply_t *rmp;
  ip4_address_t collector;
  clib_error_t *e;
  int rv = 0;

  /* Both halves, in the order the configuration states them: where the
   * records go, then whether they are produced. The two are independent in
   * the dataplane - bpa_log_enable checks VPP's own IPFIX exporter, and the
   * TCP transport is a path of its own - so this is about the box ending
   * where the message says, not about one making the other succeed.
   *
   * A refused transport stops here rather than going on to the state: the
   * caller is about to be told the message failed, and applying half of what
   * it asked for leaves a box nobody described. */
  if (mp->set_tcp)
    {
      ip4_address_decode (mp->collector, &collector);
      e = bpa_log_tcp_enable (&collector, clib_net_to_host_u16 (mp->port),
			      mp->tcp_enable);
      if (e)
	{
	  /* The sentence goes to the log, where the client reads it back and
	   * attaches it to the code - see the session-table refusal above. */
	  vlib_log_err (bpa_main.log_class, "%U", format_clib_error, e);
	  clib_error_free (e);
	  rv = VNET_API_ERROR_INVALID_VALUE;
	  goto reply;
	}
    }

  if (mp->set_enable)
    {
      e = bpa_log_enable (mp->enable);
      if (e)
	{
	  vlib_log_err (bpa_main.log_class, "%U", format_clib_error, e);
	  clib_error_free (e);
	  rv = VNET_API_ERROR_INVALID_VALUE;
	}
    }

reply:
  REPLY_MACRO (VL_API_BPA_LOGGING_SET_REPLY);
}

/*
 * The syslog transport (issue #20).
 *
 * A handler of its own beside the one above, matching the message: a syslog
 * collector is a different destination and not another flag on the IPFIX one,
 * and the two run side by side on a box whose collector speaks syslog and
 * whose own tooling reads IPFIX.
 *
 * THE ENTERPRISE NUMBER IS REFUSED AT ZERO, here as at the dataplane CLI.
 * RFC 5424 reserves a structured-data name without an `@enterprise` suffix
 * for names IANA has registered; this product has none, so every line carries
 * the operator's own number and there is nothing sensible to default it to.
 * Refusing at configuration time is the whole point: the alternative is a
 * year of lines a collector silently misparses.
 */
/*
 * The identity table's source (issue #21).
 *
 * The path is copied out of the message and handed to the loader, which reads
 * the file whole or refuses it whole. The refusal's sentence goes to the log
 * where the client reads it back, the way every refusal here does.
 */
static void
vl_api_bpa_identity_set_t_handler (vl_api_bpa_identity_set_t *mp)
{
  vl_api_bpa_identity_set_reply_t *rmp;
  clib_error_t *e;
  char path[256];
  int rv = 0;

  /* Read the way every other string on this API is read - the generated
   * accessor, not the wire layout - so a VPP bump that changes that layout
   * changes it in one place. */
  snprintf (path, sizeof (path), "%s", (const char *) mp->path);

  e = bpa_identity_load (path[0] ? path : 0);
  if (e)
    {
      vlib_log_err (bpa_main.log_class, "%U", format_clib_error, e);
      clib_error_free (e);
      rv = VNET_API_ERROR_INVALID_VALUE;
    }

  REPLY_MACRO (VL_API_BPA_IDENTITY_SET_REPLY);
}

static void
vl_api_bpa_logging_syslog_set_t_handler (vl_api_bpa_logging_syslog_set_t *mp)
{
  vl_api_bpa_logging_syslog_set_reply_t *rmp;
  ip4_address_t collector;
  clib_error_t *e;
  int rv = 0;

  if (!mp->enable)
    {
      e = bpa_log_stream_enable (&bpa_log_syslog, 0, 0, 0);
      goto done;
    }

  if (!clib_net_to_host_u32 (mp->enterprise))
    {
      vlib_log_err (bpa_main.log_class,
		    "cgnat logging syslog: an enterprise number is required, "
		    "because RFC 5424 reserves an unsuffixed structured-data "
		    "name for IANA");
      rv = VNET_API_ERROR_INVALID_VALUE;
      goto reply;
    }
  if (mp->facility > 23 || mp->severity > 7)
    {
      vlib_log_err (bpa_main.log_class,
		    "cgnat logging syslog: facility is 0-23 and severity "
		    "is 0-7");
      rv = VNET_API_ERROR_INVALID_VALUE;
      goto reply;
    }

  ip4_address_decode (mp->collector, &collector);
  bpa_log_syslog.wire = BPA_WIRE_SYSLOG;
  bpa_log_syslog.pen = clib_net_to_host_u32 (mp->enterprise);
  bpa_log_syslog.pri = (u32) mp->facility * 8 + mp->severity;
  e = bpa_log_stream_enable (&bpa_log_syslog, &collector,
			     clib_net_to_host_u16 (mp->port), 1);

done:
  if (e)
    {
      /* The sentence goes to the log, where the client reads it back and
       * attaches it to the code - the same route every refusal here takes. */
      vlib_log_err (bpa_main.log_class, "%U", format_clib_error, e);
      clib_error_free (e);
      rv = VNET_API_ERROR_INVALID_VALUE;
    }

reply:
  REPLY_MACRO (VL_API_BPA_LOGGING_SYSLOG_SET_REPLY);
}

static void
vl_api_bpa_limits_set_t_handler (vl_api_bpa_limits_set_t *mp)
{
  vl_api_bpa_limits_set_reply_t *rmp;
  clib_error_t *e;
  u8 *name;
  int rv = 0;

  /* Before the change, cleared after it: an entry can then only ever carry
   * the identity of the request that produced it. */
  bpa_actor_set ((char *) mp->audit_user, mp->audit_uid);
  name = format (0, "%s", mp->name);
  e = bpa_limits_set (name, clib_net_to_host_u32 (mp->max_sessions),
		      clib_net_to_host_u32 (mp->max_rate));
  if (e)
    {
      vlib_log_err (bpa_main.log_class, "%U", format_clib_error, e);
      clib_error_free (e);
      rv = VNET_API_ERROR_INVALID_VALUE;
    }

  vec_free (name);
  bpa_actor_clear ();
  REPLY_MACRO (VL_API_BPA_LIMITS_SET_REPLY);
}

static void
vl_api_bpa_forward_add_del_t_handler (vl_api_bpa_forward_add_del_t *mp)
{
  vl_api_bpa_forward_add_del_reply_t *rmp;
  bpa_static_t sf = { 0 };
  ip4_address_t priv;
  clib_error_t *e;
  u8 *name;
  int rv = 0;

  /* Before the change, cleared after it: an entry can then only ever carry
   * the identity of the request that produced it. */
  bpa_actor_set ((char *) mp->audit_user, mp->audit_uid);
  name = format (0, "%s", mp->name);
  ip4_address_decode (mp->ip_priv, &priv);

  sf.proto = (mp->proto == BPA_API_FWD_UDP) ? BPA_PROTO_UDP : BPA_PROTO_TCP;
  sf.ip_priv = clib_net_to_host_u32 (priv.as_u32);
  sf.port_pub = clib_net_to_host_u16 (mp->port_pub);
  sf.port_priv = clib_net_to_host_u16 (mp->port_priv);

  /* Removing is identified by the public port and the protocol alone; the
   * private fields above are ignored on that path. */
  e = bpa_static_add_del (name, &sf, mp->is_add, 0);
  if (e)
    {
      /* The detailed message goes to the log; the client gets a code. */
      vlib_log_err (bpa_main.log_class, "%U", format_clib_error, e);
      clib_error_free (e);
      rv = VNET_API_ERROR_INVALID_VALUE;
    }

  vec_free (name);
  bpa_actor_clear ();
  REPLY_MACRO (VL_API_BPA_FORWARD_ADD_DEL_REPLY);
}

static void
vl_api_bpa_interface_add_del_feature_t_handler (
  vl_api_bpa_interface_add_del_feature_t *mp)
{
  vl_api_bpa_interface_add_del_feature_reply_t *rmp;
  u32 sw_if_index = clib_net_to_host_u32 (mp->sw_if_index);
  int rv;

  VALIDATE_SW_IF_INDEX (mp);

  /* Before the change, cleared after it: an entry can then only ever carry
   * the identity of the request that produced it. */
  bpa_actor_set ((char *) mp->audit_user, mp->audit_uid);

  /* The same call the VPP CLI makes, and not a second copy of what it does:
   * this handler once enabled the feature and returned, which left every
   * interface the product attaches without shallow reassembly and with a
   * stale "tcp-mss auto" clamp (#183). */
  rv = bpa_interface_feature_enable_disable (sw_if_index, mp->is_inside,
					     mp->is_add);
  bpa_actor_clear ();

  BAD_SW_IF_INDEX_LABEL;
  REPLY_MACRO (VL_API_BPA_INTERFACE_ADD_DEL_FEATURE_REPLY);
}

static void
send_pool_details (bpa_pool_t *p, vl_api_registration_t *reg, u32 context)
{
  vl_api_bpa_pool_details_t *mp;

  mp = vl_msg_api_alloc (sizeof (*mp));
  clib_memset (mp, 0, sizeof (*mp));
  mp->_vl_msg_id =
    ntohs (VL_API_BPA_POOL_DETAILS + bpa_main.msg_id_base);
  mp->context = context;

  clib_strncpy ((char *) mp->name, (char *) p->name, sizeof (mp->name) - 1);
  mp->block_size = clib_host_to_net_u16 (p->block_size);
  mp->port_min = clib_host_to_net_u16 (p->port_min);
  mp->port_max = clib_host_to_net_u16 (p->port_max);
  mp->policy = (vl_api_bpa_policy_t) p->policy;
  mp->mode = (vl_api_bpa_mode_t) p->mode;
  mp->filter = (vl_api_bpa_filter_t) p->filter;
  mp->max_blocks = p->max_blocks;
  mp->block_stride = clib_host_to_net_u16 (p->block_stride);
  mp->block_reserve = clib_host_to_net_u16 (p->block_reserve);
  mp->alg = clib_host_to_net_u32 (p->alg);
  mp->tcp_mss = clib_host_to_net_u16 (p->tcp_mss);
  mp->tcp_mss_auto = p->tcp_mss_auto;
  mp->max_sessions = clib_host_to_net_u32 (p->max_sessions);
  mp->max_rate = clib_host_to_net_u32 (p->max_rate);
  mp->timeouts.udp = clib_host_to_net_u32 (p->timeouts.udp);
  mp->timeouts.icmp = clib_host_to_net_u32 (p->timeouts.icmp);
  mp->timeouts.tcp_syn = clib_host_to_net_u32 (p->timeouts.tcp_syn);
  mp->timeouts.tcp_transitory = clib_host_to_net_u32 (p->timeouts.tcp_trans);
  mp->timeouts.tcp_established = clib_host_to_net_u32 (p->timeouts.tcp_estab);
  mp->timeouts.gre = clib_host_to_net_u32 (p->timeouts.gre);
  mp->timeouts.port_holddown =
    clib_host_to_net_u32 (p->timeouts.port_holddown);
  mp->timeouts.alg_expect = clib_host_to_net_u32 (p->timeouts.alg_expect);
  mp->blocks_per_ip = clib_host_to_net_u32 (p->blocks_per_ip);
  mp->blocks_mapped = clib_host_to_net_u32 (p->blocks_mapped);
  mp->reserve_count = clib_host_to_net_u32 (p->reserve_count);
  mp->capacity = clib_host_to_net_u32 (p->capacity);
  mp->inside_count = clib_host_to_net_u32 (p->inside.count);
  mp->outside_count = clib_host_to_net_u32 (p->outside.count);
  /* The floor, not recomputed here: bpa_pool_guaranteed() is what the CLI
   * prints, and two copies of a capacity figure is how one of them ends up
   * promising subscribers the other knows are refused. */
  mp->n_workers = clib_host_to_net_u32 (bpa_main.n_workers);
  mp->guaranteed =
    clib_host_to_net_u32 (bpa_pool_guaranteed (p, bpa_main.n_workers));

  vl_api_send_msg (reg, (u8 *) mp);
}

static void
vl_api_bpa_pool_dump_t_handler (vl_api_bpa_pool_dump_t *mp)
{
  vl_api_registration_t *reg;
  bpa_pool_t *p;

  reg = vl_api_client_index_to_registration (mp->client_index);
  if (!reg)
    return;

  pool_foreach (p, bpa_main.pools)
    {
      send_pool_details (p, reg, mp->context);
    }
}

#include <bpa/bpa.api.c>

static clib_error_t *
bpa_api_hookup (vlib_main_t *vm)
{
  bpa_main.msg_id_base = setup_message_id_table ();

  /*
   * This one message runs without the API dispatcher's barrier, and takes its
   * own around each thing it changes.
   *
   * It is the message `prismash commit` deletes a pool with, and a delete frees
   * that pool's sessions in budgeted chunks so the datapath keeps running in
   * between. A chunk cannot release a barrier the dispatcher took on its
   * behalf: the release is a no-op at recursion depth, and the commit would
   * stop forwarding for the whole walk exactly as it did before. See
   * bpa_pool_del().
   */
  vl_api_set_msg_thread_safe (vlibapi_get_main (),
			      bpa_main.msg_id_base + VL_API_BPA_POOL_ADD_DEL,
			      1);
  return 0;
}

VLIB_API_INIT_FUNCTION (bpa_api_hookup);
