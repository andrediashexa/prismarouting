/* bpa_cli.c - debug CLI for BPA pools. */

#include <vlib/vlib.h>
#include <vnet/ip/ip.h>

#include "bpa.h"
#include "bpa_errors.h"
#include "bpa_log.h"
#include <vnet/ip/reass/ip4_sv_reass.h>

u8 *
format_bpa_policy (u8 *s, va_list *args)
{
  bpa_policy_t p = va_arg (*args, int);
  return format (s, "%s",
		 p == BPA_POLICY_INTERLEAVED ? "interleaved" : "blocked");
}

u8 *
format_bpa_proto (u8 *s, va_list *args)
{
  u32 p = va_arg (*args, u32);

  switch (p)
    {
    case BPA_PROTO_TCP:
      return format (s, "tcp");
    case BPA_PROTO_UDP:
      return format (s, "udp");
    case BPA_PROTO_ICMP:
      return format (s, "icmp");
    case BPA_PROTO_GRE:
      return format (s, "gre");
    default:
      return format (s, "proto-%u", p);
    }
}

/* The whole mask at once: "ftp on rtsp off pptp on". One line an operator can
 * read, rather than one line per ALG that has to be assembled by eye. */
u8 *
format_bpa_alg (u8 *s, va_list *args)
{
  u32 enabled = va_arg (*args, u32);
  int first = 1;

#define _(sym, nm, desc)                                                      \
  s = format (s, "%s%s %s", first ? "" : "  ", nm,                            \
	      (enabled & BPA_ALG_##sym) ? "on" : "off");                      \
  first = 0;
  foreach_bpa_alg
#undef _

    return s;
}

u8 *
format_bpa_mode (u8 *s, va_list *args)
{
  bpa_mode_t m = va_arg (*args, int);
  return format (s, "%s",
		 m == BPA_MODE_DETERMINISTIC ? "deterministic" : "dynamic");
}

/* Renders a host-order address the way the rest of VPP prints v4. */
static u8 *
format_bpa_addr (u8 *s, va_list *args)
{
  u32 host = va_arg (*args, u32);
  ip4_address_t a;

  a.as_u32 = clib_host_to_net_u32 (host);
  return format (s, "%U", format_ip4_address, &a);
}

/* Renders the ranges of an address set, e.g. "200.150.0.8+4, 45.170.16.64+2". */
static u8 *
format_bpa_addr_set (u8 *s, va_list *args)
{
  bpa_addr_set_t *set = va_arg (*args, bpa_addr_set_t *);
  u32 i;

  for (i = 0; i < set->n_ranges; i++)
    s = format (s, "%s%U+%u", i ? ", " : "", format_bpa_addr,
		set->ranges[i].base, set->ranges[i].count);
  return s;
}

u8 *
format_bpa_pool (u8 *s, va_list *args)
{
  bpa_pool_t *p = va_arg (*args, bpa_pool_t *);
  u32 indent = format_get_indent (s);
  u32 usable = p->capacity < p->inside.count ? p->capacity : p->inside.count;

  s = format (s, "pool %v [%U / %U]", p->name, format_bpa_mode, p->mode,
	      format_bpa_policy, p->policy);
  s = format (s, "\n%Uinside       %U (%u addresses, %u range(s))",
	      format_white_space, indent + 2, format_bpa_addr_set, &p->inside,
	      p->inside.count, p->inside.n_ranges);
  s = format (s, "\n%Uoutside      %U (%u addresses, %u range(s))",
	      format_white_space, indent + 2, format_bpa_addr_set, &p->outside,
	      p->outside.count, p->outside.n_ranges);
  s = format (s, "\n%Uports        %u-%u (%u per IP)", format_white_space,
	      indent + 2, p->port_min, p->port_max, p->port_count);
  s = format (s, "\n%Ublock        %u ports -> %u blocks per public IP",
	      format_white_space, indent + 2, p->block_size, p->blocks_per_ip);
  s = format (s, "\n%Usharing      %u:1", format_white_space, indent + 2,
	      p->blocks_per_ip);
  s = format (s, "\n%Ucapacity     %u subscribers (usable %u)",
	      format_white_space, indent + 2, p->capacity, usable);
  s = format (s, "\n%Udivisor      %u (%s)", format_white_space, indent + 2,
	      p->div.d, p->div.is_pow2 ? "shift" : "reciprocal");
  /* Printed whichever way each one is set. "The ALG is off" and "the ALG saw
   * nothing" are different diagnoses, and the counters alone cannot tell them
   * apart; a line that appears only when off reads as an absent feature. */
  s = format (s, "\n%Ualg          %U", format_white_space, indent + 2,
	      format_bpa_alg, p->alg);
  return s;
}

/*
 * One "forward" clause of a pool definition.
 *
 * Only the range checks unformat cannot make. Everything else a forward can be
 * refused for - the subscriber, the block, a collision with another forward -
 * needs the pool's derived values and is checked by bpa_pool_validate() once
 * the whole definition has been read.
 */
static clib_error_t *
bpa_static_clause (bpa_pool_t *cfg, u8 proto, u32 port_pub,
		   const ip4_address_t *priv, u32 port_priv)
{
  bpa_static_t sf = { 0 };

  if (port_pub == 0 || port_pub > 65535 || port_priv == 0
      || port_priv > 65535)
    return clib_error_return (0, "forward: port out of range");

  sf.proto = proto;
  sf.ip_priv = clib_net_to_host_u32 (priv->as_u32);
  sf.port_pub = (u16) port_pub;
  sf.port_priv = (u16) port_priv;
  vec_add1 (cfg->statics, sf);
  return 0;
}

static clib_error_t *
bpa_pool_add_command_fn (vlib_main_t *vm, unformat_input_t *input,
			 vlib_cli_command_t *cmd)
{
  unformat_input_t _line_input, *line_input = &_line_input;
  clib_error_t *error = 0;
  ip4_address_t addr;
  u32 len = 0;
  u32 block = 512, port_min = 1024, port_max = 65535;
  u32 tv = 0;

  u32 maxb = 0;
  u32 *in_b = 0, *in_c = 0, *out_b = 0, *out_c = 0;
  bpa_pool_t cfg = { 0 };
  u8 *name = 0;
  int is_del = 0, is_check = 0;

  cfg.policy = BPA_POLICY_INTERLEAVED;
  cfg.mode = BPA_MODE_DETERMINISTIC;
  cfg.alg = BPA_ALG_DEFAULT;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (0, "expected pool name");

  while (unformat_check_input (line_input) != UNFORMAT_END_OF_INPUT)
    {
      u32 fwd_pub = 0, fwd_priv = 0;

      if (unformat (line_input, "del"))
	is_del = 1;
      else if (unformat (line_input, "check"))
	is_check = 1;
      /* inside and outside repeat: provider IPv4 is fragmented */
      else if (unformat (line_input, "inside %U/%d", unformat_ip4_address,
			 &addr, &len))
	{
	  if (len > 32)
	    {
	      error = clib_error_return (0, "inside prefix length %u invalid",
					 len);
	      goto done;
	    }
	  vec_add1 (in_c, 1u << (32 - len));
	  vec_add1 (in_b, clib_net_to_host_u32 (addr.as_u32)
			    & ~(vec_elt (in_c, vec_len (in_c) - 1) - 1));
	}
      else if (unformat (line_input, "outside %U/%d", unformat_ip4_address,
			 &addr, &len))
	{
	  if (len > 32)
	    {
	      error = clib_error_return (0, "outside prefix length %u invalid",
					 len);
	      goto done;
	    }
	  vec_add1 (out_c, 1u << (32 - len));
	  vec_add1 (out_b, clib_net_to_host_u32 (addr.as_u32)
			     & ~(vec_elt (out_c, vec_len (out_c) - 1) - 1));
	}
      else if (unformat (line_input, "block %u", &block))
	;
      else if (unformat (line_input, "ports %u-%u", &port_min, &port_max))
	;
      else if (unformat (line_input, "policy interleaved"))
	cfg.policy = BPA_POLICY_INTERLEAVED;
      else if (unformat (line_input, "policy blocked"))
	cfg.policy = BPA_POLICY_BLOCKED;
      else if (unformat (line_input, "mode deterministic"))
	cfg.mode = BPA_MODE_DETERMINISTIC;
      else if (unformat (line_input, "mode dynamic"))
	cfg.mode = BPA_MODE_DYNAMIC;
      /* Per-pool lifetimes. Absent means inherit the global, which is why
       * zero is the "not set" value rather than a legal setting. */
      else if (unformat (line_input, "timeout udp %u", &tv))
	cfg.timeouts.udp = tv;
      else if (unformat (line_input, "timeout icmp %u", &tv))
	cfg.timeouts.icmp = tv;
      else if (unformat (line_input, "timeout tcp-syn %u", &tv))
	cfg.timeouts.tcp_syn = tv;
      else if (unformat (line_input, "timeout tcp-transitory %u", &tv))
	cfg.timeouts.tcp_trans = tv;
      else if (unformat (line_input, "timeout tcp-established %u", &tv))
	cfg.timeouts.tcp_estab = tv;
      /* "alg <name> off" in a candidate configuration. Only the departures
       * from the default are ever written out, so a configuration file does
       * not grow a line per ALG per pool. */
#define _(sym, nm, desc)                                                      \
  else if (unformat (line_input, "alg " nm " off")) cfg.alg &= ~BPA_ALG_##sym; \
  else if (unformat (line_input, "alg " nm " on")) cfg.alg |= BPA_ALG_##sym;
      foreach_bpa_alg
#undef _
      else if (unformat (line_input, "timeout gre %u", &tv))
	cfg.timeouts.gre = tv;
      else if (unformat (line_input, "timeout port-holddown %u", &tv))
	cfg.timeouts.port_holddown = tv;
      else if (unformat (line_input, "timeout alg-expect %u", &tv))
	cfg.timeouts.alg_expect = tv;

      else if (unformat (line_input, "max-blocks %u", &maxb))
	cfg.max_blocks = (u8) maxb;
      /* "alg <name> on|off" in a candidate configuration. alg_set records
       * that an opinion was stated, so bpa_pool_init() leaves it alone
       * instead of resolving it back to the default. */
#define _(sym, nm, desc)                                                      \
  else if (unformat (line_input, "alg " nm " on")                             \
	   || unformat (line_input, "alg " nm " enable"))                     \
  {                                                                           \
    cfg.alg |= BPA_ALG_##sym;                                                 \
    cfg.alg_set |= BPA_ALG_##sym;                                             \
  }                                                                           \
  else if (unformat (line_input, "alg " nm " off")                            \
	   || unformat (line_input, "alg " nm " disable"))                    \
  {                                                                           \
    cfg.alg &= ~BPA_ALG_##sym;                                                \
    cfg.alg_set |= BPA_ALG_##sym;                                             \
  }
      foreach_bpa_alg
#undef _
      /*
       * A forward stated as part of the pool definition. This is the shape a
       * candidate configuration takes, and the only shape "check" can
       * validate: a forward is judged against the pool's block sizing and
       * address sets, and for a pool that does not exist yet those are on
       * this line and nowhere else.
       */
      else if (unformat (line_input, "forward tcp %u to %U %u", &fwd_pub,
			 unformat_ip4_address, &addr, &fwd_priv))
	{
	  error = bpa_static_clause (&cfg, BPA_PROTO_TCP, fwd_pub, &addr,
				     fwd_priv);
	  if (error)
	    goto done;
	}
      else if (unformat (line_input, "forward udp %u to %U %u", &fwd_pub,
			 unformat_ip4_address, &addr, &fwd_priv))
	{
	  error = bpa_static_clause (&cfg, BPA_PROTO_UDP, fwd_pub, &addr,
				     fwd_priv);
	  if (error)
	    goto done;
	}
      else if (unformat (line_input, "filter endpoint-independent"))
	cfg.filter = BPA_FILTER_ENDPOINT_INDEPENDENT;
      else if (unformat (line_input, "filter address-dependent"))
	cfg.filter = BPA_FILTER_ADDRESS_DEPENDENT;
      else if (!name && unformat (line_input, "%v", &name))
	;
      else
	{
	  error = clib_error_return (0, "unknown input '%U'",
				     format_unformat_error, line_input);
	  goto done;
	}
    }

  if (!name)
    {
      error = clib_error_return (0, "pool name is required");
      goto done;
    }

  if (is_del)
    {
      error = bpa_pool_del (name);
      goto done;
    }

  if (vec_len (in_b) == 0 || vec_len (out_b) == 0)
    {
      error = clib_error_return (
	0, "at least one inside and one outside prefix are required");
      goto done;
    }
  if (port_max > 65535 || port_min < 1 || port_min > port_max)
    {
      error = clib_error_return (0, "invalid port range");
      goto done;
    }
  if (block > 65535)
    {
      error = clib_error_return (0, "block size out of range");
      goto done;
    }

  error = bpa_addr_set_build (&cfg.inside, in_b, in_c, vec_len (in_b));
  if (error)
    goto done;
  error = bpa_addr_set_build (&cfg.outside, out_b, out_c, vec_len (out_b));
  if (error)
    goto done;

  cfg.name = name;
  cfg.port_min = (u16) port_min;
  cfg.port_max = (u16) port_max;
  cfg.block_size = (u16) block;

  if (is_check)
    {
      error = bpa_pool_validate (&cfg);
      if (!error)
	vlib_cli_output (vm, "pool '%v' is valid", name);
    }
  else
    error = bpa_pool_add (&cfg, 0);

done:
  vec_free (in_b);
  vec_free (in_c);
  vec_free (out_b);
  vec_free (out_c);
  /* bpa_pool_add() takes its own copy, exactly as it does for the name. */
  vec_free (cfg.statics);
  vec_free (name);
  unformat_free (line_input);
  return error;
}

VLIB_CLI_COMMAND (bpa_pool_add_command, static) = {
  .path = "bpa pool",
  .short_help =
    "bpa pool <name> inside <pfx>/<len> [inside <pfx>/<len> ...] "
    "outside <pfx>/<len> [outside <pfx>/<len> ...] "
    "[block <n>] [ports <min>-<max>] "
    "[policy interleaved|blocked] [mode deterministic|dynamic] "
    "[alg <ftp|rtsp|pptp> on|off] "
    "[forward <tcp|udp> <public-port> to <private-ip> <private-port>] "
    "[check] | bpa pool <name> del",
  .function = bpa_pool_add_command_fn,
};

static clib_error_t *
bpa_show_pool_command_fn (vlib_main_t *vm, unformat_input_t *input,
			  vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;

  if (pool_elts (bm->pools) == 0)
    {
      vlib_cli_output (vm, "no pools configured");
      return 0;
    }

  pool_foreach (p, bm->pools)
    {
      vlib_cli_output (vm, "%U", format_bpa_pool, p);
    }
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_pool_command, static) = {
  .path = "show bpa pool",
  .short_help = "show bpa pool",
  .function = bpa_show_pool_command_fn,
};

static clib_error_t *
bpa_interface_command_fn (vlib_main_t *vm, unformat_input_t *input,
			  vlib_cli_command_t *cmd)
{
  unformat_input_t _line_input, *line_input = &_line_input;
  vnet_main_t *vnm = vnet_get_main ();
  clib_error_t *error = 0;
  u32 sw_if_index = ~0;
  int is_in = -1, enable = 1;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (0, "expected direction and interface");

  while (unformat_check_input (line_input) != UNFORMAT_END_OF_INPUT)
    {
      if (unformat (line_input, "in2out"))
	is_in = 1;
      else if (unformat (line_input, "out2in"))
	is_in = 0;
      else if (unformat (line_input, "del"))
	enable = 0;
      else if (unformat (line_input, "%U", unformat_vnet_sw_interface, vnm,
			 &sw_if_index))
	;
      else
	{
	  error = clib_error_return (0, "unknown input '%U'",
				     format_unformat_error, line_input);
	  goto done;
	}
    }

  if (is_in < 0)
    {
      error = clib_error_return (0, "specify in2out or out2in");
      goto done;
    }
  if (sw_if_index == ~0)
    {
      error = clib_error_return (0, "interface is required");
      goto done;
    }

  {
    const char *feature;
    int rv;

    if (bpa_main.n_workers)
      feature = is_in ? "bpa-in2out-handoff" : "bpa-out2in-handoff";
    else
      feature = is_in ? "bpa-in2out" : "bpa-out2in";

    rv = vnet_feature_enable_disable ("ip4-unicast", feature, sw_if_index,
				      enable, 0, 0);

    /*
     * Shallow virtual reassembly: it keeps the ports from the first fragment
     * and attaches them to the following ones. Without it a fragment with no
     * port matches no session and is dropped. It is refcounted, so coexisting
     * with another feature that also uses it is not a problem.
     */
    if (!rv)
      ip4_sv_reass_enable_disable_with_refcnt (sw_if_index, enable);
    if (rv)
      error = clib_error_return (0, "vnet_feature_enable_disable failed: %d",
				 rv);
  }

done:
  unformat_free (line_input);
  return error;
}

VLIB_CLI_COMMAND (bpa_interface_command, static) = {
  .path = "set interface bpa",
  .short_help = "set interface bpa <in2out|out2in> <interface> [del]",
  .function = bpa_interface_command_fn,
};

static clib_error_t *
bpa_show_session_command_fn (vlib_main_t *vm, unformat_input_t *input,
			     vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  bpa_per_thread_t *pt;
  u32 i, total = 0;

  vec_foreach_index (i, bm->per_thread)
    {
      pt = vec_elt_at_index (bm->per_thread, i);
      vlib_cli_output (vm, "thread %u: %u sessions, %u active subscribers, "
			   "%u expired",
		       i, pool_elts (pt->sessions), pool_elts (pt->subs),
		       pt->n_expired);
      total += pool_elts (pt->sessions);
    }
  vlib_cli_output (vm, "total: %u sessions", total);
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_session_command, static) = {
  .path = "show bpa session",
  .short_help = "show bpa session",
  .function = bpa_show_session_command_fn,
};

/*
 * Troubleshooting commands.
 *
 * The five that existed before report aggregates: "thread 0: 3 sessions, 2
 * active subscribers". That is enough to know the box is working and useless
 * for the question an operator actually arrives with, which is always about
 * one customer - "100.64.0.5 says nothing works" - or its inverse, "who was
 * behind this address and port".
 *
 * None of these walks the session table. Everything below reads the
 * per-subscriber record, which has one entry per active subscriber rather than
 * one per session, or answers by arithmetic. A "show" that stalls forwarding
 * is worse than no "show".
 */

/* Which worker owns a subscriber, as a thread index. */
static u32
bpa_sub_thread (const bpa_main_t *bm, u32 sub_idx)
{
  if (bm->n_workers == 0)
    return 0;
  return bpa_owner_worker (&bm->workers, sub_idx) + 1;
}

static bpa_sub_t *
bpa_sub_find (bpa_main_t *bm, u32 pool_index, u32 sub_idx,
	      bpa_per_thread_t **pt_out)
{
  u32 ti = bpa_sub_thread (bm, sub_idx);
  bpa_per_thread_t *pt;
  uword *q;

  if (ti >= vec_len (bm->per_thread))
    return 0;
  pt = vec_elt_at_index (bm->per_thread, ti);
  if (!pt->initialised)
    return 0;
  q = hash_get (pt->sub_by_idx, bpa_sub_key (pool_index, sub_idx));
  if (!q)
    return 0;
  if (pt_out)
    *pt_out = pt;
  return pool_elt_at_index (pt->subs, q[0]);
}

static clib_error_t *
bpa_show_subscriber_command_fn (vlib_main_t *vm, unformat_input_t *input,
				vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  unformat_input_t _line_input, *line_input = &_line_input;
  clib_error_t *error = 0;
  ip4_address_t addr;
  u32 pool_index, sub_idx, i;
  bpa_pool_t *p;
  bpa_sub_t *sub;
  bpa_block_t blk;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (0, "usage: show bpa subscriber <inside-address>");
  if (!unformat (line_input, "%U", unformat_ip4_address, &addr))
    {
      error = clib_error_return (0, "expected an inside IPv4 address");
      goto done;
    }

  {
    u32 host = clib_net_to_host_u32 (addr.as_u32);

    if (bpa_pool_by_inside (bm, host, &pool_index, &sub_idx))
      {
	error = clib_error_return (
	  0, "%U is not inside any configured pool", format_ip4_address, &addr);
	goto done;
      }
    p = pool_elt_at_index (bm->pools, pool_index);

    vlib_cli_output (vm, "subscriber %U   pool %v, index %u, worker thread %u",
		     format_ip4_address, &addr, p->name, sub_idx,
		     bpa_sub_thread (bm, sub_idx));

    /*
     * In deterministic mode the block is knowable whether or not the
     * subscriber has ever sent a packet, and saying so matters: "no state on
     * this box" and "no block for this subscriber" are different answers.
     */
    if (p->mode == BPA_MODE_DETERMINISTIC)
      {
	if (bpa_forward (p, host, &blk) == 0)
	  {
	    ip4_address_t pub;
	    pub.as_u32 = clib_host_to_net_u32 (blk.ip_pub);
	    vlib_cli_output (vm, "  the formula gives it %U ports %u-%u",
			     format_ip4_address, &pub, blk.port_lo,
			     blk.port_hi);
	  }
	else
	  vlib_cli_output (vm, "  BEYOND CAPACITY: the pool has no block for "
			       "this address, so it is not translated at all");
      }

    sub = bpa_sub_find (bm, pool_index, sub_idx, 0);
    if (!sub)
      {
	vlib_cli_output (vm, "  no live state: this subscriber holds no "
			     "block and has no sessions on this box");
	goto done;
      }

    for (i = 0; i < vec_len (sub->blocks); i++)
      {
	bpa_subblock_t *sb = vec_elt_at_index (sub->blocks, i);
	u32 used = sb->map.n_ports - sb->map.n_free;
	ip4_address_t pub;

	pub.as_u32 = clib_host_to_net_u32 (sb->blk.ip_pub);
	vlib_cli_output (
	  vm, "  block %u  %U  ports %u-%u   %u of %u in use (%u%%), %u free%s",
	  i, format_ip4_address, &pub, sb->blk.port_lo,
	  sb->blk.port_hi, used, sb->map.n_ports,
	  sb->map.n_ports ? (used * 100) / sb->map.n_ports : 0, sb->map.n_free,
	  i ? "   (extension)" : "");
      }
    vlib_cli_output (vm, "  sessions %u   port refusals %u", sub->n_sessions,
		     sub->n_refused);
    if (sub->n_refused)
      vlib_cli_output (vm, "  refusals are not a box fault: this subscriber "
			   "asked for more ports than its block holds");
  }

done:
  unformat_free (line_input);
  return error;
}

VLIB_CLI_COMMAND (bpa_show_subscriber_command, static) = {
  .path = "show bpa subscriber",
  .short_help = "show bpa subscriber <inside-address>",
  .function = bpa_show_subscriber_command_fn,
};

/*
 * The inverse, and the one a court order or an abuse report asks.
 *
 * It says WHERE the answer comes from, because the two sources do not carry
 * the same weight. The formula answers for any instant, past or present, as
 * long as the pool has not been resized. A dynamic allocation answers for
 * NOW - the block was somebody else's yesterday, and only the RFC 8158 log
 * says whose.
 */
static clib_error_t *
bpa_show_translation_command_fn (vlib_main_t *vm, unformat_input_t *input,
				 vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  unformat_input_t _line_input, *line_input = &_line_input;
  clib_error_t *error = 0;
  ip4_address_t addr, priv;
  u32 pool_index, block_idx, sub_idx, port = 0, ip_priv = 0;
  bpa_pool_t *p;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (
      0, "usage: show bpa translation <public-address> <port>");
  if (!unformat (line_input, "%U %u", unformat_ip4_address, &addr, &port))
    {
      error = clib_error_return (0, "expected a public address and a port");
      goto done;
    }
  if (port > 65535)
    {
      error = clib_error_return (0, "port %u is out of range", port);
      goto done;
    }

  {
    u32 host = clib_net_to_host_u32 (addr.as_u32);

    if (bpa_pool_by_outside (bm, host, &pool_index))
      {
	error = clib_error_return (0, "%U is not a public address of any pool",
				   format_ip4_address, &addr);
	goto done;
      }
    p = pool_elt_at_index (bm->pools, pool_index);

    if (bpa_reverse_sub (p, host, (u16) port, &block_idx))
      {
	vlib_cli_output (vm, "%U:%u belongs to pool %v but to no block: the "
			     "port is outside the pool's usable range, so "
			     "nothing was ever translated here",
			 format_ip4_address, &addr, port, p->name);
	goto done;
      }

    if (p->mode == BPA_MODE_DETERMINISTIC)
      {
	if (bpa_set_addr_of (&p->inside, block_idx, &ip_priv))
	  {
	    error = clib_error_return (0, "block %u has no subscriber address",
				       block_idx);
	    goto done;
	  }
	priv.as_u32 = clib_host_to_net_u32 (ip_priv);
	vlib_cli_output (vm, "%U:%u  ->  %U", format_ip4_address, &addr, port,
			 format_ip4_address, &priv);
	vlib_cli_output (vm, "  pool %v, block %u, DETERMINISTIC", p->name,
			 block_idx);
	vlib_cli_output (
	  vm, "  from the FORMULA. It holds for any instant, past or present, "
	      "as long as this pool's addresses and block size have not "
	      "changed since. No allocation record is involved.");
	goto done;
      }

    /* Dynamic: the answer is about now, and saying otherwise would be a
     * wrong answer to a court order given with full confidence. */
    {
      u32 ti = 0, w, nw, found = 0;

      for (ti = 0; ti < vec_len (bm->per_thread); ti++)
	{
	  bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, ti);

	  if (!pt->initialised || pool_index >= vec_len (pt->dyn))
	    continue;
	  bpa_worker_id (bm, ti, &w, &nw);
	  if (bpa_dyn_owner (vec_elt_at_index (pt->dyn, pool_index), block_idx,
			     nw, &sub_idx))
	    continue;
	  if (bpa_set_addr_of (&p->inside, sub_idx, &ip_priv))
	    continue;
	  found = 1;
	  break;
	}

      if (!found)
	{
	  vlib_cli_output (vm, "%U:%u  ->  nobody, right now", format_ip4_address,
			   &addr, port);
	  vlib_cli_output (vm, "  pool %v, block %u, DYNAMIC: no subscriber "
			       "holds this block at the moment", p->name,
			   block_idx);
	}
      else
	{
	  priv.as_u32 = clib_host_to_net_u32 (ip_priv);
	  vlib_cli_output (vm, "%U:%u  ->  %U", format_ip4_address, &addr,
			   port, format_ip4_address, &priv);
	  vlib_cli_output (vm, "  pool %v, block %u, DYNAMIC", p->name,
			   block_idx);
	}
      vlib_cli_output (
	vm, "  this answers for NOW ONLY. The block belonged to somebody else "
	    "before and will again; for a past instant read the RFC 8158 log, "
	    "which records when each block was assigned and released.");
    }
  }

done:
  unformat_free (line_input);
  return error;
}

VLIB_CLI_COMMAND (bpa_show_translation_command, static) = {
  .path = "show bpa translation",
  .short_help = "show bpa translation <public-address> <port>",
  .function = bpa_show_translation_command_fn,
};

/* One row of "show bpa top". */
typedef struct
{
  u32 ip_priv;
  u32 pct;
  u32 used;
  u32 total;
  u32 n_blocks;
  u32 n_refused;
  u32 pool_index;
} bpa_top_t;

static int
bpa_top_cmp (void *a, void *b)
{
  bpa_top_t *x = a, *y = b;
  if (x->pct != y->pct)
    return (int) y->pct - (int) x->pct;
  return (int) y->used - (int) x->used;
}

/*
 * The subscribers closest to running out.
 *
 * This is the question that prevents the support call rather than answering
 * it: natEvent 12 reports exhaustion, which is to say it reports that somebody
 * has already lost traffic.
 *
 * Walks the per-subscriber table, which holds one entry per ACTIVE SUBSCRIBER
 * - not one per session. On a box with 200k subscribers and millions of
 * sessions those differ by three orders of magnitude.
 */
static clib_error_t *
bpa_show_top_command_fn (vlib_main_t *vm, unformat_input_t *input,
			 vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  unformat_input_t _line_input, *line_input = &_line_input;
  bpa_top_t *rows = 0, *r;
  u32 want = 10, ti, i;

  if (unformat_user (input, unformat_line_input, line_input))
    {
      (void) unformat (line_input, "%u", &want);
      unformat_free (line_input);
    }
  if (want == 0)
    want = 10;

  for (ti = 0; ti < vec_len (bm->per_thread); ti++)
    {
      bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, ti);
      uword key, value;

      if (!pt->initialised)
	continue;
      /* clang-format off */
      hash_foreach (key, value, pt->sub_by_idx,
      ({
	u32 pool_index = (u32) (key >> 32);
	u32 sub_idx = (u32) key;
	bpa_sub_t *sub;
	u32 used = 0, total = 0, ip_priv = 0;

	if (pool_is_free_index (bm->pools, pool_index))
	  continue;
	sub = pool_elt_at_index (pt->subs, value);
	for (i = 0; i < vec_len (sub->blocks); i++)
	  {
	    bpa_subblock_t *sb = vec_elt_at_index (sub->blocks, i);
	    used += sb->map.n_ports - sb->map.n_free;
	    total += sb->map.n_ports;
	  }
	if (total == 0)
	  continue;
	if (bpa_set_addr_of (&pool_elt_at_index (bm->pools, pool_index)->inside,
			     sub_idx, &ip_priv))
	  continue;
	vec_add2 (rows, r, 1);
	r->ip_priv = ip_priv;
	r->used = used;
	r->total = total;
	r->pct = (used * 100) / total;
	r->n_blocks = vec_len (sub->blocks);
	r->n_refused = sub->n_refused;
	r->pool_index = pool_index;
      }));
      /* clang-format on */
    }

  if (vec_len (rows) == 0)
    {
      vlib_cli_output (vm, "no subscriber holds a block right now");
      return 0;
    }

  vec_sort_with_function (rows, bpa_top_cmp);
  vlib_cli_output (vm, "%-16s %-12s %11s %5s %7s %9s", "subscriber", "pool",
		   "ports", "full", "blocks", "refusals");
  for (i = 0; i < vec_len (rows) && i < want; i++)
    {
      ip4_address_t a;
      bpa_pool_t *p = pool_elt_at_index (bm->pools, rows[i].pool_index);

      a.as_u32 = clib_host_to_net_u32 (rows[i].ip_priv);
      vlib_cli_output (vm, "%-16U %-12v %5u/%-5u %4u%% %7u %9u",
		       format_ip4_address, &a, p->name, rows[i].used,
		       rows[i].total, rows[i].pct, rows[i].n_blocks,
		       rows[i].n_refused);
    }
  vlib_cli_output (vm, "%u subscriber(s) holding blocks", vec_len (rows));
  vec_free (rows);
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_top_command, static) = {
  .path = "show bpa top",
  .short_help = "show bpa top [<n>]",
  .function = bpa_show_top_command_fn,
};

/*
 * Why packets were dropped, under names an operator recognises.
 *
 * "show errors" already carries these numbers, mixed in with every other node
 * in the box and truncated at 35 characters. This is the same data with only
 * the CGNAT nodes in it, summed across workers, and with the zero rows left
 * out - during an incident the interesting line is the one that is not zero.
 */
/* Which counters mean a packet did not get through; see bpa_errors.h. */
static u8 bpa_in2out_is_drop[] = {
#define _(sym, drop, str) drop,
  foreach_bpa_in2out_error
#undef _
};

static u8 bpa_out2in_is_drop[] = {
#define _(sym, drop, str) drop,
  foreach_bpa_out2in_error
#undef _
};

static u64
bpa_counter_total (vlib_main_t *vm, vlib_node_t *n, u32 i)
{
  u64 total = 0;
  u32 t;

  /* Summed across workers: a per-thread number answers a question nobody
   * asked, and a subscriber is only on one worker anyway. */
  for (t = 0; t < vlib_get_n_threads (); t++)
    {
      vlib_main_t *tvm = vlib_get_main_by_index (t);

      if (!tvm)
	continue;
      if (n->error_heap_index + i < vec_len (tvm->error_main.counters))
	total += tvm->error_main.counters[n->error_heap_index + i];
    }
  return total;
}

static void
bpa_show_drops_node (vlib_main_t *vm, u32 node_index, const char *label,
		     const u8 *is_drop, u32 n_is_drop)
{
  vlib_node_t *n = vlib_get_node (vm, node_index);
  u32 i, dropped = 0, other = 0;

  vlib_cli_output (vm, "%s", label);

  /* Drops first, and alone under their own heading. "show errors" lists
   * "packets translated" beside "no pool for source address" as though they
   * were the same kind of thing; during an incident they are not. */
  for (i = 0; i < n->n_errors; i++)
    {
      u64 total;

      if (i >= n_is_drop || !is_drop[i])
	continue;
      total = bpa_counter_total (vm, n, i);
      if (total == 0)
	continue;
      if (!dropped++)
	vlib_cli_output (vm, "  packets that did not get through");
      /* The description lives in the global error heap rather than on the
       * node: vlib_node_t carries only where its block starts. */
      vlib_cli_output (vm, "  %12Lu  %s", total,
		       vm->error_main.counters_heap[n->error_heap_index + i]
			 .desc);
    }
  if (!dropped)
    vlib_cli_output (vm, "  nothing dropped");

  for (i = 0; i < n->n_errors; i++)
    {
      u64 total;

      if (i < n_is_drop && is_drop[i])
	continue;
      total = bpa_counter_total (vm, n, i);
      if (total == 0)
	continue;
      if (!other++)
	vlib_cli_output (vm, "  everything else it counted");
      vlib_cli_output (vm, "  %12Lu  %s", total,
		       vm->error_main.counters_heap[n->error_heap_index + i]
			 .desc);
    }
}

static clib_error_t *
bpa_show_drops_command_fn (vlib_main_t *vm, unformat_input_t *input,
			   vlib_cli_command_t *cmd)
{
  bpa_show_drops_node (vm, bpa_in2out_node.index, "bpa-in2out",
		       bpa_in2out_is_drop, ARRAY_LEN (bpa_in2out_is_drop));
  bpa_show_drops_node (vm, bpa_out2in_node.index, "bpa-out2in",
		       bpa_out2in_is_drop, ARRAY_LEN (bpa_out2in_is_drop));
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_drops_command, static) = {
  .path = "show bpa drops",
  .short_help = "show bpa drops",
  .function = bpa_show_drops_command_fn,
};

/*
 * Change a pool's filtering mode in place.
 *
 * Separate from "bpa pool", which creates, because recreating a pool to change
 * one setting drops every session it holds - and the whole point of choosing
 * between endpoint-independent and address-dependent filtering is that an
 * operator can decide, on a box carrying traffic, without disconnecting the
 * subscribers whose behaviour is being changed.
 */
static clib_error_t *
bpa_filter_command_fn (vlib_main_t *vm, unformat_input_t *input,
		       vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  unformat_input_t _line_input, *line_input = &_line_input;
  clib_error_t *error = 0;
  bpa_filter_t f;
  bpa_pool_t *p;
  u8 *name = 0;
  int found = 0;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (0, "usage: bpa filter <pool> "
				"<endpoint-independent|address-dependent>");

  if (!unformat (line_input, "%v", &name))
    {
      error = clib_error_return (0, "missing pool name");
      goto done;
    }
  if (unformat (line_input, "endpoint-independent"))
    f = BPA_FILTER_ENDPOINT_INDEPENDENT;
  else if (unformat (line_input, "address-dependent"))
    f = BPA_FILTER_ADDRESS_DEPENDENT;
  else
    {
      error = clib_error_return (0, "expected endpoint-independent or "
				    "address-dependent");
      goto done;
    }

  pool_foreach (p, bm->pools)
    if (vec_len (p->name) == vec_len (name)
	&& !memcmp (p->name, name, vec_len (name)))
      {
	p->filter = f;
	found = 1;
	break;
      }

  if (!found)
    error = clib_error_return (0, "no such pool '%v'", name);

done:
  vec_free (name);
  unformat_free (line_input);
  return error;
}

VLIB_CLI_COMMAND (bpa_filter_command, static) = {
  .path = "bpa filter",
  .short_help = "bpa filter <pool> <endpoint-independent|address-dependent>",
  .function = bpa_filter_command_fn,
};

/*
 * A static port forward, added to or removed from a running pool.
 *
 * The public ADDRESS is not asked for and cannot be given: it is the
 * subscriber's own, the one the pool's arithmetic assigns. See bpa_static_t.
 */
static clib_error_t *
bpa_forward_command_fn (vlib_main_t *vm, unformat_input_t *input,
			vlib_cli_command_t *cmd)
{
  unformat_input_t _line_input, *line_input = &_line_input;
  clib_error_t *error = 0;
  bpa_static_t sf = { 0 };
  ip4_address_t priv = { 0 };
  u32 port_pub = 0, port_priv = 0;
  int is_del = 0, is_check = 0, have_proto = 0, have_pub = 0, have_to = 0;
  u8 *name = 0;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (
      0, "usage: bpa forward <pool> <tcp|udp> <public-port> "
	 "to <private-ip> <private-port>");

  if (!unformat (line_input, "%v", &name))
    {
      error = clib_error_return (0, "missing pool name");
      goto done;
    }

  while (unformat_check_input (line_input) != UNFORMAT_END_OF_INPUT)
    {
      if (unformat (line_input, "tcp"))
	sf.proto = BPA_PROTO_TCP, have_proto = 1;
      else if (unformat (line_input, "udp"))
	sf.proto = BPA_PROTO_UDP, have_proto = 1;
      else if (unformat (line_input, "to %U %u", unformat_ip4_address, &priv,
			 &port_priv))
	have_to = 1;
      else if (unformat (line_input, "del"))
	is_del = 1;
      else if (unformat (line_input, "check"))
	is_check = 1;
      else if (!have_pub && unformat (line_input, "%u", &port_pub))
	have_pub = 1;
      else
	{
	  error = clib_error_return (0, "unknown input '%U'",
				     format_unformat_error, line_input);
	  goto done;
	}
    }

  if (!have_proto || !have_pub)
    {
      error = clib_error_return (0, "a protocol and a public port are "
				    "required");
      goto done;
    }
  if (!is_del && !have_to)
    {
      error = clib_error_return (0, "missing 'to <private-ip> "
				    "<private-port>'");
      goto done;
    }
  if (port_pub == 0 || port_pub > 65535 || (!is_del && (port_priv == 0
						       || port_priv > 65535)))
    {
      error = clib_error_return (0, "port out of range");
      goto done;
    }

  sf.ip_priv = clib_net_to_host_u32 (priv.as_u32);
  sf.port_pub = (u16) port_pub;
  sf.port_priv = (u16) port_priv;

  /* Deleting names the public port and nothing else, because that pair is
   * what a forward is. The private side is accepted and ignored, so that
   * removing one can be the add command with "del" on the end - which is what
   * an operator with the line in their history will type. */
  error = bpa_static_add_del (name, &sf, !is_del, is_check);
  if (!error && is_check)
    vlib_cli_output (vm, "forward is valid");

done:
  vec_free (name);
  unformat_free (line_input);
  return error;
}

VLIB_CLI_COMMAND (bpa_forward_command, static) = {
  .path = "bpa forward",
  .short_help = "bpa forward <pool> <tcp|udp> <public-port> to <private-ip> "
		"<private-port> [check] | bpa forward <pool> <tcp|udp> "
		"<public-port> del",
  .function = bpa_forward_command_fn,
};

static clib_error_t *
bpa_show_forward_command_fn (vlib_main_t *vm, unformat_input_t *input,
			     vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;
  u8 *want = 0;
  u32 total = 0;

  unformat (input, "%v", &want);

  pool_foreach (p, bm->pools)
    {
      ip4_address_t pub, priv;
      u32 i;

      if (want && (vec_len (want) != vec_len (p->name)
		   || memcmp (want, p->name, vec_len (want))))
	continue;
      if (vec_len (p->statics) == 0)
	continue;

      vlib_cli_output (vm, "pool %v", p->name);
      vec_foreach_index (i, p->statics)
	{
	  const bpa_static_t *sf = &p->statics[i];

	  pub.as_u32 = clib_host_to_net_u32 (sf->ip_pub);
	  priv.as_u32 = clib_host_to_net_u32 (sf->ip_priv);
	  vlib_cli_output (vm, "  %U %U:%u -> %U:%u", format_bpa_proto,
			   sf->proto, format_ip4_address, &pub, sf->port_pub,
			   format_ip4_address, &priv, sf->port_priv);
	  total++;
	}
    }

  if (total == 0)
    vlib_cli_output (vm, "no forwards configured");
  vec_free (want);
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_forward_command, static) = {
  .path = "show bpa forward",
  .short_help = "show bpa forward [<pool>]",
  .function = bpa_show_forward_command_fn,
};

/*
 * Turn an ALG on or off for a pool, in place.
 *
 * Separate from "bpa pool" for the same reason "bpa filter" is: recreating a
 * pool to change one setting drops every session it holds. An operator who
 * has just been told an ALG is mangling a customer's transfer needs to switch
 * it off on a box that is carrying traffic, and if doing so disconnects
 * everybody the switch is not usable at the moment it is needed.
 *
 * Sessions already open keep whatever was done to them. Turning an ALG off
 * does not close the mappings it opened and does not stop the sequence
 * renumbering a lengthened connection depends on - the datapath decides that
 * from the session, not from this switch - so it stops new rewrites without
 * breaking the connections it already touched.
 */
static clib_error_t *
bpa_alg_command_fn (vlib_main_t *vm, unformat_input_t *input,
		    vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  unformat_input_t _line_input, *line_input = &_line_input;
  clib_error_t *error = 0;
  bpa_pool_t *p;
  u8 *name = 0;
  u32 which = 0;
  int on = -1, found = 0;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (
      0, "usage: bpa alg <pool> <ftp|rtsp|pptp> <on|off>");

  if (!unformat (line_input, "%v", &name))
    {
      error = clib_error_return (0, "missing pool name");
      goto done;
    }
#define _(sym, nm, desc)                                                      \
  if (unformat (line_input, nm))                                              \
    which = BPA_ALG_##sym;                                                    \
  else
  foreach_bpa_alg
#undef _
    {
      error = clib_error_return (0, "expected an ALG name (ftp, rtsp, pptp)");
      goto done;
    }
  /* "enable"/"disable" are accepted as well as "on"/"off": the three ALGs
   * were written separately and shipped both spellings, and refusing the one
   * an operator already has in a script buys nothing. */
  if (unformat (line_input, "on") || unformat (line_input, "enable"))
    on = 1;
  else if (unformat (line_input, "off") || unformat (line_input, "disable"))
    on = 0;
  else
    {
      error = clib_error_return (0, "expected on or off");
      goto done;
    }

  pool_foreach (p, bm->pools)
    if (vec_len (p->name) == vec_len (name)
	&& !memcmp (p->name, name, vec_len (name)))
      {
	if (on)
	  p->alg |= which;
	else
	  p->alg &= ~which;
	p->alg_set |= which;
	vlib_cli_output (vm, "%v: %U", p->name, format_bpa_alg,
			 p->alg);
	found = 1;
	break;
      }

  if (!found)
    error = clib_error_return (0, "no such pool '%v'", name);

done:
  vec_free (name);
  unformat_free (line_input);
  return error;
}

VLIB_CLI_COMMAND (bpa_alg_command, static) = {
  .path = "bpa alg",
  .short_help = "bpa alg <pool> <ftp|rtsp|pptp> <on|off>",
  .function = bpa_alg_command_fn,
};

static clib_error_t *
bpa_show_alg_command_fn (vlib_main_t *vm, unformat_input_t *input,
			 vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;

  if (pool_elts (bm->pools) == 0)
    {
      vlib_cli_output (vm, "no pools configured");
      return 0;
    }

  pool_foreach (p, bm->pools)
    {
      vlib_cli_output (vm, "%v", p->name);
#define _(sym, nm, desc)                                                      \
  vlib_cli_output (vm, "  %-6s %-3s  %s", nm,                                 \
		   (p->alg & BPA_ALG_##sym) ? "on" : "off", desc);
      foreach_bpa_alg
#undef _
    }
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_alg_command, static) = {
  .path = "show bpa alg",
  .short_help = "show bpa alg",
  .function = bpa_show_alg_command_fn,
};

static clib_error_t *
bpa_timeouts_command_fn (vlib_main_t *vm, unformat_input_t *input,
			 vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  unformat_input_t _line_input, *line_input = &_line_input;
  clib_error_t *error = 0;
  u32 v;

  if (!unformat_user (input, unformat_line_input, line_input))
    {
      vlib_cli_output (vm,
		       "udp %u  icmp %u  tcp-syn %u  tcp-transitory %u  "
		       "tcp-established %u  gre %u  port-holddown %u  "
		       "alg-expect %u",
		       bm->timeouts.udp, bm->timeouts.icmp,
		       bm->timeouts.tcp_syn, bm->timeouts.tcp_trans,
		       bm->timeouts.tcp_estab, bm->timeouts.gre,
		       bm->timeouts.port_holddown,
		       bm->timeouts.alg_expect);
      return 0;
    }

  while (unformat_check_input (line_input) != UNFORMAT_END_OF_INPUT)
    {
      if (unformat (line_input, "udp %u", &v))
	bm->timeouts.udp = v;
      else if (unformat (line_input, "icmp %u", &v))
	bm->timeouts.icmp = v;
      else if (unformat (line_input, "tcp-transitory %u", &v))
	bm->timeouts.tcp_trans = v;
      else if (unformat (line_input, "tcp-established %u", &v))
	bm->timeouts.tcp_estab = v;
      else if (unformat (line_input, "tcp-syn %u", &v))
	bm->timeouts.tcp_syn = v;
      else if (unformat (line_input, "gre %u", &v))
	bm->timeouts.gre = v;
      else if (unformat (line_input, "port-holddown %u", &v))
	bm->timeouts.port_holddown = v;
      else if (unformat (line_input, "alg-expect %u", &v))
	bm->timeouts.alg_expect = v;
      else
	{
	  error = clib_error_return (0, "unknown input '%U'",
				     format_unformat_error, line_input);
	  break;
	}
    }

  unformat_free (line_input);
  return error;
}

VLIB_CLI_COMMAND (bpa_timeouts_command, static) = {
  .path = "bpa timeouts",
  .short_help = "bpa timeouts [udp <s>] [icmp <s>] [tcp-syn <s>] "
		"[tcp-transitory <s>] [tcp-established <s>] [gre <s>] "
		"[port-holddown <s>] [alg-expect <s>]",
  .function = bpa_timeouts_command_fn,
};

static clib_error_t *
bpa_show_blocks_command_fn (vlib_main_t *vm, unformat_input_t *input,
			    vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;
  u32 i;
  int any = 0;

  pool_foreach (p, bm->pools)
    {
      u32 pi = p - bm->pools;
      u64 total = 0, alloc = 0, allocs = 0, rels = 0, exh = 0, exts = 0;

      if (p->mode != BPA_MODE_DYNAMIC)
	continue;
      any = 1;

      vlib_cli_output (vm, "pool %v (hold-down %us)", p->name,
		       bm->block_holddown);
      for (i = 0; i < vec_len (bm->per_thread); i++)
	{
	  bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, i);
	  bpa_dyn_t *d;

	  if (pi >= vec_len (pt->dyn))
	    continue;
	  d = vec_elt_at_index (pt->dyn, pi);
	  if (!d->active)
	    continue;

	  vlib_cli_output (vm,
			   "  thread %u: %u/%u blocks in use, %Lu allocations "
			   "(%Lu extensions), %Lu releases, %Lu refusals",
			   i, d->n_alloc, d->n_local, d->n_allocations,
			   d->n_extensions, d->n_releases, d->n_exhausted);
	  total += d->n_local;
	  alloc += d->n_alloc;
	  allocs += d->n_allocations;
	  exts += d->n_extensions;
	  rels += d->n_releases;
	  exh += d->n_exhausted;
	}
      vlib_cli_output (vm,
		       "  total: %Lu/%Lu in use, %Lu allocations "
		       "(%Lu extensions), %Lu releases, %Lu refusals",
		       alloc, total, allocs, exts, rels, exh);
    }

  if (!any)
    vlib_cli_output (vm, "no dynamic pools configured");
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_blocks_command, static) = {
  .path = "show bpa blocks",
  .short_help = "show bpa blocks",
  .function = bpa_show_blocks_command_fn,
};

static clib_error_t *
bpa_holddown_command_fn (vlib_main_t *vm, unformat_input_t *input,
			 vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  u32 v;

  if (unformat (input, "%u", &v))
    {
      bm->block_holddown = v;
      return 0;
    }

  vlib_cli_output (vm, "block-holddown %u", bm->block_holddown);
  return 0;
}

VLIB_CLI_COMMAND (bpa_holddown_command, static) = {
  .path = "bpa block-holddown",
  .short_help = "bpa block-holddown [<seconds>]",
  .function = bpa_holddown_command_fn,
};

static clib_error_t *
bpa_logging_command_fn (vlib_main_t *vm, unformat_input_t *input,
			vlib_cli_command_t *cmd)
{
  int enable = -1;

  if (unformat (input, "enable"))
    enable = 1;
  else if (unformat (input, "disable"))
    enable = 0;
  else
    return clib_error_return (0, "say 'enable' or 'disable'");

  return bpa_log_enable (enable);
}

VLIB_CLI_COMMAND (bpa_logging_command, static) = {
  .path = "bpa logging",
  .short_help = "bpa logging <enable|disable>",
  .function = bpa_logging_command_fn,
};

static clib_error_t *
bpa_show_logging_command_fn (vlib_main_t *vm, unformat_input_t *input,
			     vlib_cli_command_t *cmd)
{
  bpa_log_main_t *lm = &bpa_log_main;

  vlib_cli_output (vm, "IPFIX export of block events (RFC 8158)");
  vlib_cli_output (vm, "  state       %s",
		   lm->enabled ? "enabled" : "disabled");
  vlib_cli_output (vm, "  template    %u (%u bytes per record)",
		   lm->template_id, BPA_LOG_RECORD_SIZE);
  vlib_cli_output (vm, "  records     %Lu emitted, %Lu dropped",
		   lm->n_records, lm->n_dropped);
  vlib_cli_output (vm, "  natEvent    16 allocation, 17 de-allocation, 12 exhaustion");
  vlib_cli_output (vm, "  flushes     %Lu with data, %Lu empty, %Lu without buffer",
		   lm->n_flush_sent, lm->n_flush_empty, lm->n_getbuf_fail);

  if (bpa_log_tcp.enabled)
    {
      vlib_cli_output (vm, "TCP transport (survives collector outage)");
      vlib_cli_output (vm, "  collector   %U:%u  %s", format_ip4_address,
		       &bpa_log_tcp.collector, bpa_log_tcp.port,
		       bpa_log_tcp.connected	     ? "connected"
		       : bpa_log_tcp.pending_connect ? "connecting"
						     : "disconnected");
      vlib_cli_output (vm,
		       "  records     %Lu emitted, %Lu bytes sent, "
		       "%Lu pending",
		       bpa_log_tcp.n_records, bpa_log_tcp.n_sent,
		       (u64) vec_len (bpa_log_tcp.outbuf));
      vlib_cli_output (vm,
		       "  spill       %Lu bytes on disk, %Lu replayed, "
		       "%Lu dropped",
		       bpa_log_tcp.n_spilled, bpa_log_tcp.n_replayed,
		       bpa_log_tcp.n_dropped);
      vlib_cli_output (vm,
		       "  connections %Lu, service %Lu, send %Lu, errno %d",
		       bpa_log_tcp.n_connects, bpa_log_tcp.n_service,
		       bpa_log_tcp.n_send_calls, bpa_log_tcp.last_errno);
    }
  else
    vlib_cli_output (vm, "TCP transport: disabled");
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_logging_command, static) = {
  .path = "show bpa logging",
  .short_help = "show bpa logging",
  .function = bpa_show_logging_command_fn,
};

/*
 * Decomposes a range [base, base+count) into the CIDR prefixes covering it.
 *
 * Pool ranges are born from prefixes but merged when adjacent, so a range may
 * no longer be a single prefix. For the configuration to be readable back, it
 * has to come out as prefixes again.
 */
static u8 *
format_bpa_range_as_prefixes (u8 *s, va_list *args)
{
  u32 base = va_arg (*args, u32);
  u32 count = va_arg (*args, u32);
  u32 indent = va_arg (*args, u32);
  const char *kw = va_arg (*args, const char *);

  while (count > 0)
    {
      u32 size = 1;
      u32 len;
      ip4_address_t a;

      /* largest power of two aligned on base that still fits in count */
      while (size < count && (base & size) == 0 && size < 0x80000000u)
	size <<= 1;
      while (size > count)
	size >>= 1;

      for (len = 32; len > 0 && (1u << (32 - len)) < size; len--)
	;

      a.as_u32 = clib_host_to_net_u32 (base);
      s = format (s, "%U%s %U/%u\n", format_white_space, indent, kw,
		  format_ip4_address, &a, len);

      base += size;
      count -= size;
    }
  return s;
}

static clib_error_t *
bpa_show_config_command_fn (vlib_main_t *vm, unformat_input_t *input,
			    vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  vnet_main_t *vnm = vnet_get_main ();
  vnet_sw_interface_t *si;
  bpa_pool_t *p;
  u8 *s = 0;
  u32 i;

  /*
   * Emits the configuration in VTY syntax, so hexash can show
   * "show running-config" and save it. The dataplane is the source of truth:
   * the VTY runs as a short-lived process and cannot hold state between calls.
   */
  pool_foreach (p, bm->pools)
    {
      s = format (s, "cgnat pool %v\n", p->name);
      for (i = 0; i < p->inside.n_ranges; i++)
	s = format (s, "%U", format_bpa_range_as_prefixes,
		    p->inside.ranges[i].base, p->inside.ranges[i].count, 1,
		    "inside");
      for (i = 0; i < p->outside.n_ranges; i++)
	s = format (s, "%U", format_bpa_range_as_prefixes,
		    p->outside.ranges[i].base, p->outside.ranges[i].count, 1,
		    "outside");
      s = format (s, " block-size %u\n", p->block_size);
      if (p->port_min != 1024 || p->port_max != 65535)
	s = format (s, " ports %u %u\n", p->port_min, p->port_max);
      s = format (s, " policy %U\n", format_bpa_policy, p->policy);
      s = format (s, " mode %U\n", format_bpa_mode, p->mode);
      /* Only the overrides. Emitting inherited values would turn every
       * global change into a diff against every pool. */
      if (p->timeouts.udp)
	s = format (s, " timeout udp %u\n", p->timeouts.udp);
      if (p->timeouts.icmp)
	s = format (s, " timeout icmp %u\n", p->timeouts.icmp);
      if (p->timeouts.tcp_syn)
	s = format (s, " timeout tcp-syn %u\n", p->timeouts.tcp_syn);
      if (p->timeouts.gre)
	s = format (s, " timeout gre %u\n", p->timeouts.gre);
      if (p->timeouts.tcp_trans)
	s = format (s, " timeout tcp-transitory %u\n", p->timeouts.tcp_trans);
      if (p->timeouts.tcp_estab)
	s = format (s, " timeout tcp-established %u\n", p->timeouts.tcp_estab);
      if (p->timeouts.port_holddown)
	s = format (s, " timeout port-holddown %u\n", p->timeouts.port_holddown);
      if (p->timeouts.alg_expect)
	s = format (s, " timeout alg-expect %u\n", p->timeouts.alg_expect);

      if (p->max_blocks > 1)
	s = format (s, " max-blocks %u\n", p->max_blocks);
      if (p->filter != BPA_FILTER_ENDPOINT_INDEPENDENT)
	s = format (s, " filter address-dependent\n");
      /* Only the departures from the default: emitting a line per ALG per
       * pool would fill every running configuration with lines that say
       * nothing. */
#define _(sym, nm, desc)                                                      \
  if ((p->alg & BPA_ALG_##sym) != (BPA_ALG_DEFAULT & BPA_ALG_##sym))          \
    s = format (s, " alg %s %s\n", nm, (p->alg & BPA_ALG_##sym) ? "on" : "off");
      foreach_bpa_alg
#undef _
      /* An inbound mapping the configuration does not show is a mapping
       * nobody can account for later. Emitted in the vector's own order,
       * which is by public port, so the running configuration is stable. */
      vec_foreach_index (i, p->statics)
	{
	  ip4_address_t priv;

	  priv.as_u32 = clib_host_to_net_u32 (p->statics[i].ip_priv);
	  s = format (s, " forward %U %u to %U %u\n", format_bpa_proto,
		      p->statics[i].proto, p->statics[i].port_pub,
		      format_ip4_address, &priv, p->statics[i].port_priv);
	}
      s = format (s, " exit\n");
    }

  pool_foreach (si, vnm->interface_main.sw_interfaces)
    {
      if (vnet_feature_is_enabled ("ip4-unicast", "bpa-in2out",
				   si->sw_if_index) == 1
	  || vnet_feature_is_enabled ("ip4-unicast", "bpa-in2out-handoff",
				      si->sw_if_index) == 1)
	s = format (s, "cgnat interface %U inside\n",
		    format_vnet_sw_if_index_name, vnm, si->sw_if_index);
      if (vnet_feature_is_enabled ("ip4-unicast", "bpa-out2in",
				   si->sw_if_index) == 1
	  || vnet_feature_is_enabled ("ip4-unicast", "bpa-out2in-handoff",
				      si->sw_if_index) == 1)
	s = format (s, "cgnat interface %U outside\n",
		    format_vnet_sw_if_index_name, vnm, si->sw_if_index);
    }

  if (bm->timeouts.udp != BPA_TIMEOUT_UDP)
    s = format (s, "cgnat timeout udp %u\n", bm->timeouts.udp);
  if (bm->timeouts.icmp != BPA_TIMEOUT_ICMP)
    s = format (s, "cgnat timeout icmp %u\n", bm->timeouts.icmp);
  if (bm->timeouts.tcp_trans != BPA_TIMEOUT_TCP_TRANS)
    s = format (s, "cgnat timeout tcp-transitory %u\n", bm->timeouts.tcp_trans);
  if (bm->timeouts.tcp_syn != BPA_TIMEOUT_TCP_SYN)
    s = format (s, "cgnat timeout tcp-syn %u\n", bm->timeouts.tcp_syn);
  if (bm->timeouts.port_holddown != BPA_PORT_HOLDDOWN)
    s = format (s, "cgnat timeout port-holddown %u\n",
		bm->timeouts.port_holddown);
  if (bm->timeouts.alg_expect != BPA_TIMEOUT_ALG_EXPECT)
    s = format (s, "cgnat timeout alg-expect %u\n", bm->timeouts.alg_expect);
  if (bm->timeouts.tcp_estab != BPA_TIMEOUT_TCP_ESTAB)
    s = format (s, "cgnat timeout tcp-established %u\n", bm->timeouts.tcp_estab);
  if (bm->block_holddown != BPA_BLOCK_HOLDDOWN)
    s = format (s, "cgnat block-holddown %u\n", bm->block_holddown);
  if (bpa_log_main.enabled)
    s = format (s, "cgnat logging enable\n");

  if (s)
    {
      vlib_cli_output (vm, "%v", s);
      vec_free (s);
    }
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_config_command, static) = {
  .path = "show bpa config",
  .short_help = "show bpa config",
  .function = bpa_show_config_command_fn,
};

static clib_error_t *
bpa_logging_tcp_command_fn (vlib_main_t *vm, unformat_input_t *input,
			    vlib_cli_command_t *cmd)
{
  ip4_address_t addr = { 0 };
  u32 port = 4739;

  if (unformat (input, "disable"))
    return bpa_log_tcp_enable (0, 0, 0);

  if (!unformat (input, "%U", unformat_ip4_address, &addr))
    return clib_error_return (0, "collector address required");
  unformat (input, "port %u", &port);
  if (port > 65535)
    return clib_error_return (0, "invalid port");

  return bpa_log_tcp_enable (&addr, (u16) port, 1);
}

VLIB_CLI_COMMAND (bpa_logging_tcp_command, static) = {
  .path = "bpa logging tcp",
  .short_help = "bpa logging tcp <collector> [port <n>] | bpa logging tcp disable",
  .function = bpa_logging_tcp_command_fn,
};

static clib_error_t *
bpa_sessions_command_fn (vlib_main_t *vm, unformat_input_t *input,
			 vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  u32 buckets = 0, mem_mb = 0;

  if (unformat (input, "buckets %u memory %u", &buckets, &mem_mb)
      || unformat (input, "memory %u buckets %u", &mem_mb, &buckets))
    {
      if (pool_elts (bm->pools))
	return clib_error_return (
	  0, "tables already created; set this before configuring pools");
      bm->hash_buckets = buckets;
      bm->hash_memory = mem_mb << 20;
      return 0;
    }

  vlib_cli_output (vm, "buckets %u, memory %u MB per table",
		   bm->hash_buckets, bm->hash_memory >> 20);
  vlib_cli_output (vm,
		   "  (two tables per thread; the size is a virtual "
		   "reservation, and the OOM killer counts virtual)");
  return 0;
}

VLIB_CLI_COMMAND (bpa_sessions_command, static) = {
  .path = "bpa session-table",
  .short_help = "bpa session-table [buckets <n> memory <MB>]",
  .function = bpa_sessions_command_fn,
};
