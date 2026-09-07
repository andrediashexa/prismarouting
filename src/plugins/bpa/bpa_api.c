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
  /* The binary API has no ALG field yet, so a pool created through it gets
   * the defaults. Adding one is an API change and belongs with the ALG work
   * as a whole, not to whichever ALG lands first. */
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
  REPLY_MACRO (VL_API_BPA_POOL_ADD_DEL_REPLY);
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
  REPLY_MACRO (VL_API_BPA_FORWARD_ADD_DEL_REPLY);
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
  mp->filter = (vl_api_bpa_filter_t) p->filter;
  mp->max_blocks = p->max_blocks;
  mp->alg = clib_host_to_net_u32 (p->alg);
  mp->tcp_mss = clib_host_to_net_u16 (p->tcp_mss);
  mp->tcp_mss_auto = p->tcp_mss_auto;
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
