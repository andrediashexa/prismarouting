/* bpa_api.c - implementacao da API binaria. */

#include <vnet/vnet.h>
#include <vlibapi/api.h>
#include <vlibmemory/api.h>
#include <vnet/ip/ip_types_api.h>
#include <vnet/format_fns.h>

#include "bpa.h"

#include <bpa/bpa.api_enum.h>
#include <bpa/bpa.api_types.h>

#define REPLY_MSG_ID_BASE bpa_main.msg_id_base
#include <vlibapi/api_helper_macros.h>

static void
vl_api_bpa_pool_add_del_t_handler (vl_api_bpa_pool_add_del_t *mp)
{
  vl_api_bpa_pool_add_del_reply_t *rmp;
  bpa_pool_t cfg = { 0 };
  clib_error_t *e = 0;
  u32 *in_b = 0, *in_c = 0, *out_b = 0, *out_c = 0;
  u8 *name;
  int rv = 0, i;

  name = format (0, "%s", mp->name);

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

  e = bpa_pool_add (&cfg, 0);

done:
  if (e)
    {
      /* A mensagem detalhada vai para o log; o cliente recebe codigo. */
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
  REPLY_MACRO (VL_API_BPA_POOL_ADD_DEL_REPLY);
}

static void
vl_api_bpa_interface_add_del_feature_t_handler (
  vl_api_bpa_interface_add_del_feature_t *mp)
{
  vl_api_bpa_interface_add_del_feature_reply_t *rmp;
  u32 sw_if_index = clib_net_to_host_u32 (mp->sw_if_index);
  const char *feature;
  int rv;

  VALIDATE_SW_IF_INDEX (mp);

  if (bpa_main.n_workers)
    feature = mp->is_inside ? "bpa-in2out-handoff" : "bpa-out2in-handoff";
  else
    feature = mp->is_inside ? "bpa-in2out" : "bpa-out2in";

  rv = vnet_feature_enable_disable ("ip4-unicast", feature, sw_if_index,
				    mp->is_add, 0, 0);

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
  mp->blocks_per_ip = clib_host_to_net_u32 (p->blocks_per_ip);
  mp->capacity = clib_host_to_net_u32 (p->capacity);
  mp->inside_count = clib_host_to_net_u32 (p->inside.count);
  mp->outside_count = clib_host_to_net_u32 (p->outside.count);

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
  return 0;
}

VLIB_API_INIT_FUNCTION (bpa_api_hookup);
