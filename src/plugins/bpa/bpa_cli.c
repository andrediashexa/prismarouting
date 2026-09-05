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
  s = format (s, "\n%Uinterno      %U (%u enderecos, %u faixa(s))",
	      format_white_space, indent + 2, format_bpa_addr_set, &p->inside,
	      p->inside.count, p->inside.n_ranges);
  s = format (s, "\n%Upublico      %U (%u enderecos, %u faixa(s))",
	      format_white_space, indent + 2, format_bpa_addr_set, &p->outside,
	      p->outside.count, p->outside.n_ranges);
  s = format (s, "\n%Uportas       %u-%u (%u por IP)", format_white_space,
	      indent + 2, p->port_min, p->port_max, p->port_count);
  s = format (s, "\n%Ubloco        %u portas -> %u blocos por IP publico",
	      format_white_space, indent + 2, p->block_size, p->blocks_per_ip);
  s = format (s, "\n%Ucompartilha  %u:1", format_white_space, indent + 2,
	      p->blocks_per_ip);
  s = format (s, "\n%Ucapacidade   %u assinantes (aproveitavel %u)",
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
  u32 *in_b = 0, *in_c = 0, *out_b = 0, *out_c = 0;
  bpa_pool_t cfg = { 0 };
  u8 *name = 0;
  int is_del = 0;

  cfg.policy = BPA_POLICY_INTERLEAVED;
  cfg.mode = BPA_MODE_DETERMINISTIC;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (0, "expected pool name");

  while (unformat_check_input (line_input) != UNFORMAT_END_OF_INPUT)
    {
      if (unformat (line_input, "del"))
	is_del = 1;
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
    "| bpa pool <name> del",
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
      vlib_cli_output (vm, "nenhum pool configurado");
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
     * Reassembly virtual raso: guarda as portas do primeiro fragmento e as
     * anexa aos seguintes. Sem isso um fragmento sem porta nao casa com
     * sessao e e descartado. E refcontado, entao conviver com outra feature
     * que tambem o use nao e problema.
     */
    if (!rv)
      ip4_sv_reass_enable_disable_with_refcnt (sw_if_index, enable);
    if (rv)
      error = clib_error_return (0, "vnet_feature_enable_disable falhou: %d",
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
      vlib_cli_output (vm, "thread %u: %u sessoes, %u assinantes ativos, "
			   "%u expiradas",
		       i, pool_elts (pt->sessions), pool_elts (pt->subs),
		       pt->n_expired);
      total += pool_elts (pt->sessions);
    }
  vlib_cli_output (vm, "total: %u sessoes", total);
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
		       "udp %u  icmp %u  tcp-transitory %u  tcp-established %u",
		       bm->timeouts.udp, bm->timeouts.icmp,
		       bm->timeouts.tcp_trans, bm->timeouts.tcp_estab);
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
			   "  thread %u: %u/%u blocos em uso, %Lu alocacoes, "
			   "%Lu liberacoes, %Lu recusas",
			   i, d->n_alloc, d->n_local, d->n_allocations,
			   d->n_releases, d->n_exhausted);
	  total += d->n_local;
	  alloc += d->n_alloc;
	  allocs += d->n_allocations;
	  rels += d->n_releases;
	  exh += d->n_exhausted;
	}
      vlib_cli_output (vm,
		       "  total: %Lu/%Lu em uso, %Lu alocacoes, %Lu "
		       "liberacoes, %Lu recusas",
		       alloc, total, allocs, rels, exh);
    }

  if (!any)
    vlib_cli_output (vm, "nenhum pool dinamico configurado");
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
    return clib_error_return (0, "diga 'enable' ou 'disable'");

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

  vlib_cli_output (vm, "exportacao IPFIX de eventos de bloco (RFC 8158)");
  vlib_cli_output (vm, "  estado      %s",
		   lm->enabled ? "habilitada" : "desabilitada");
  vlib_cli_output (vm, "  template    %u (%u bytes por registro)",
		   lm->template_id, BPA_LOG_RECORD_SIZE);
  vlib_cli_output (vm, "  registros   %Lu emitidos, %Lu descartados",
		   lm->n_records, lm->n_dropped);
  vlib_cli_output (vm, "  natEvent    16 alocacao, 17 liberacao, 12 esgotamento");
  vlib_cli_output (vm, "  despachos   %Lu com dados, %Lu vazios, %Lu sem buffer",
		   lm->n_flush_sent, lm->n_flush_empty, lm->n_getbuf_fail);

  if (bpa_log_tcp.enabled)
    {
      vlib_cli_output (vm, "transporte TCP (sobrevive a queda do coletor)");
      vlib_cli_output (vm, "  coletor     %U:%u  %s", format_ip4_address,
		       &bpa_log_tcp.collector, bpa_log_tcp.port,
		       bpa_log_tcp.connected	     ? "conectado"
		       : bpa_log_tcp.pending_connect ? "conectando"
						     : "desconectado");
      vlib_cli_output (vm,
		       "  registros   %Lu emitidos, %Lu bytes enviados, "
		       "%Lu pendentes",
		       bpa_log_tcp.n_records, bpa_log_tcp.n_sent,
		       (u64) vec_len (bpa_log_tcp.outbuf));
      vlib_cli_output (vm,
		       "  transbordo  %Lu bytes em disco, %Lu reenviados, "
		       "%Lu descartados",
		       bpa_log_tcp.n_spilled, bpa_log_tcp.n_replayed,
		       bpa_log_tcp.n_dropped);
      vlib_cli_output (vm,
		       "  conexoes    %Lu, service %Lu, send %Lu, errno %d",
		       bpa_log_tcp.n_connects, bpa_log_tcp.n_service,
		       bpa_log_tcp.n_send_calls, bpa_log_tcp.last_errno);
    }
  else
    vlib_cli_output (vm, "transporte TCP: desabilitado");
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_logging_command, static) = {
  .path = "show bpa logging",
  .short_help = "show bpa logging",
  .function = bpa_show_logging_command_fn,
};

/*
 * Decompoe uma faixa [base, base+count) nos prefixos CIDR que a cobrem.
 *
 * As faixas do pool nascem de prefixos mas sao fundidas quando adjacentes,
 * entao uma faixa pode nao ser mais um prefixo unico. Para a configuracao
 * poder ser relida, ela precisa voltar a sair como prefixos.
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

      /* maior potencia de dois alinhada em base e que cabe em count */
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
   * Emite a configuracao na sintaxe da VTY, para o hexash poder mostrar
   * "show running-config" e gravar. O dataplane e a fonte da verdade: a
   * VTY roda como processo curto e nao pode guardar estado entre chamadas.
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
    return clib_error_return (0, "informe o endereco do coletor");
  unformat (input, "port %u", &port);
  if (port > 65535)
    return clib_error_return (0, "porta invalida");

  return bpa_log_tcp_enable (&addr, (u16) port, 1);
}

VLIB_CLI_COMMAND (bpa_logging_tcp_command, static) = {
  .path = "bpa logging tcp",
  .short_help = "bpa logging tcp <coletor> [port <n>] | bpa logging tcp disable",
  .function = bpa_logging_tcp_command_fn,
};
