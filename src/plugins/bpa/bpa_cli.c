/* bpa_cli.c - debug CLI for BPA pools. */

#include <vlib/vlib.h>
#include <vnet/ip/ip.h>

#include "bpa.h"
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
    default:
      return format (s, "proto-%u", p);
    }
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
  return s;
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
  u32 *in_b = 0, *in_c = 0, *out_b = 0, *out_c = 0;
  bpa_pool_t cfg = { 0 };
  u8 *name = 0;
  int is_del = 0, is_check = 0;

  cfg.policy = BPA_POLICY_INTERLEAVED;
  cfg.mode = BPA_MODE_DETERMINISTIC;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (0, "expected pool name");

  while (unformat_check_input (line_input) != UNFORMAT_END_OF_INPUT)
    {
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
      else if (unformat (line_input, "timeout port-holddown %u", &tv))
	cfg.timeouts.port_holddown = tv;
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
		       "tcp-established %u  port-holddown %u",
		       bm->timeouts.udp, bm->timeouts.icmp,
		       bm->timeouts.tcp_syn, bm->timeouts.tcp_trans,
		       bm->timeouts.tcp_estab, bm->timeouts.port_holddown);
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
      else if (unformat (line_input, "port-holddown %u", &v))
	bm->timeouts.port_holddown = v;
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
  .short_help = "bpa timeouts [udp <s>] [icmp <s>] [tcp-transitory <s>] "
		"[tcp-established <s>]",
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
      u64 total = 0, alloc = 0, allocs = 0, rels = 0, exh = 0;

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
			   "  thread %u: %u/%u blocks in use, %Lu allocations, "
			   "%Lu releases, %Lu refusals",
			   i, d->n_alloc, d->n_local, d->n_allocations,
			   d->n_releases, d->n_exhausted);
	  total += d->n_local;
	  alloc += d->n_alloc;
	  allocs += d->n_allocations;
	  rels += d->n_releases;
	  exh += d->n_exhausted;
	}
      vlib_cli_output (vm,
		       "  total: %Lu/%Lu in use, %Lu allocations, %Lu "
		       "releases, %Lu refusals",
		       alloc, total, allocs, rels, exh);
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
  .short_help = "bpa block-holddown [<segundos>]",
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
      if (p->timeouts.tcp_trans)
	s = format (s, " timeout tcp-transitory %u\n", p->timeouts.tcp_trans);
      if (p->timeouts.tcp_estab)
	s = format (s, " timeout tcp-established %u\n", p->timeouts.tcp_estab);
      if (p->timeouts.port_holddown)
	s = format (s, " timeout port-holddown %u\n", p->timeouts.port_holddown);
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
