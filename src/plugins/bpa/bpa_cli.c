/* bpa_cli.c - debug CLI for BPA pools. */

#include <vlib/vlib.h>
#include <vnet/ip/ip.h>

#include "bpa.h"

u8 *
format_bpa_policy (u8 *s, va_list *args)
{
  bpa_policy_t p = va_arg (*args, int);
  return format (s, "%s",
		 p == BPA_POLICY_INTERLEAVED ? "interleaved" : "blocked");
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

static u8 *
format_prefix_len (u8 *s, va_list *args)
{
  u32 count = va_arg (*args, u32);
  u32 len = 32;

  while (len && (1u << (32 - len)) < count)
    len--;
  return format (s, "/%u", len);
}

u8 *
format_bpa_pool (u8 *s, va_list *args)
{
  bpa_pool_t *p = va_arg (*args, bpa_pool_t *);
  u32 indent = format_get_indent (s);
  u32 usable = p->capacity < p->inside_count ? p->capacity : p->inside_count;

  s = format (s, "pool %v [%U / %U]", p->name, format_bpa_mode, p->mode,
	      format_bpa_policy, p->policy);
  s = format (s, "\n%Uinterno      %U%U (%u enderecos)", format_white_space,
	      indent + 2, format_bpa_addr, p->inside_base, format_prefix_len,
	      p->inside_count, p->inside_count);
  s = format (s, "\n%Upublico      %U%U (%u enderecos)", format_white_space,
	      indent + 2, format_bpa_addr, p->outside_base, format_prefix_len,
	      p->outside_count, p->outside_count);
  s = format (s, "\n%Uportas       %u-%u (%u por IP)", format_white_space,
	      indent + 2, p->port_min, p->port_max, p->port_count);
  s = format (s, "\n%Ubloco        %u portas -> %u blocos por IP publico",
	      format_white_space, indent + 2, p->block_size, p->blocks_per_ip);
  s = format (s, "\n%Ucompartilha  %u:1", format_white_space, indent + 2,
	      p->blocks_per_ip);
  s = format (s, "\n%Ucapacidade   %u assinantes (aproveitavel %u)",
	      format_white_space, indent + 2, p->capacity, usable);
  return s;
}

static clib_error_t *
bpa_pool_add_command_fn (vlib_main_t *vm, unformat_input_t *input,
			 vlib_cli_command_t *cmd)
{
  unformat_input_t _line_input, *line_input = &_line_input;
  clib_error_t *error = 0;
  ip4_address_t in_addr, out_addr;
  u32 in_len = 0, out_len = 0;
  u32 block = 512, port_min = 1024, port_max = 65535;
  bpa_pool_t cfg = { 0 };
  u8 *name = 0;
  int have_in = 0, have_out = 0, is_del = 0;

  cfg.policy = BPA_POLICY_INTERLEAVED;
  cfg.mode = BPA_MODE_DETERMINISTIC;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (0, "expected pool name");

  while (unformat_check_input (line_input) != UNFORMAT_END_OF_INPUT)
    {
      if (unformat (line_input, "del"))
	is_del = 1;
      else if (unformat (line_input, "inside %U/%d", unformat_ip4_address,
			 &in_addr, &in_len))
	have_in = 1;
      else if (unformat (line_input, "outside %U/%d", unformat_ip4_address,
			 &out_addr, &out_len))
	have_out = 1;
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

  if (!have_in || !have_out)
    {
      error = clib_error_return (0, "inside and outside prefixes are required");
      goto done;
    }
  if (in_len > 32 || out_len > 32)
    {
      error = clib_error_return (0, "prefix length out of range");
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

  cfg.name = name;
  cfg.inside_count = 1u << (32 - in_len);
  cfg.outside_count = 1u << (32 - out_len);
  cfg.inside_base =
    clib_net_to_host_u32 (in_addr.as_u32) & ~(cfg.inside_count - 1);
  cfg.outside_base =
    clib_net_to_host_u32 (out_addr.as_u32) & ~(cfg.outside_count - 1);
  cfg.port_min = (u16) port_min;
  cfg.port_max = (u16) port_max;
  cfg.block_size = (u16) block;

  error = bpa_pool_add (&cfg, 0);

done:
  vec_free (name);
  unformat_free (line_input);
  return error;
}

VLIB_CLI_COMMAND (bpa_pool_add_command, static) = {
  .path = "bpa pool",
  .short_help = "bpa pool <name> inside <pfx>/<len> outside <pfx>/<len> "
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
