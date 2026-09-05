/* bpa.h - CGNAT com Bloco de Portas Alocadas. */

#ifndef __included_bpa_h__
#define __included_bpa_h__

#include <vlib/vlib.h>
#include <vnet/vnet.h>
#include <vnet/ip/ip.h>
#include <vnet/feature/feature.h>

#include "bpa_pool.h"
#include "bpa_ports.h"
#include "bpa_session.h"

typedef struct
{
  bpa_pool_t *pools;   /* pool vector, indexed by pool id */
  uword *pool_by_name; /* name -> pool index */

  bpa_per_thread_t *per_thread; /* one per vlib worker */
  bpa_timeouts_t timeouts;

  u16 msg_id_base;
  vlib_main_t *vlib_main;
  vnet_main_t *vnet_main;
} bpa_main_t;

extern bpa_main_t bpa_main;
extern vlib_node_registration_t bpa_in2out_node;
extern vlib_node_registration_t bpa_out2in_node;

clib_error_t *bpa_pool_add (bpa_pool_t *cfg, u32 *pool_index);
clib_error_t *bpa_pool_del (u8 *name);

format_function_t format_bpa_pool;
format_function_t format_bpa_policy;
format_function_t format_bpa_mode;
format_function_t format_bpa_proto;

/*
 * Pool selection. A single box serves one provider, so pools are few and a
 * linear scan is cheaper than a lookup structure. If a deployment ever grows
 * many pools this becomes an mtrie.
 */
static_always_inline int
bpa_pool_by_inside (bpa_main_t *bm, u32 addr, u32 *pool_index, u32 *sub_idx)
{
  bpa_pool_t *p;

  pool_foreach (p, bm->pools)
    {
      if (bpa_set_index_of (&p->inside, addr, sub_idx) == 0)
	{
	  *pool_index = p - bm->pools;
	  return 0;
	}
    }
  return -1;
}

static_always_inline int
bpa_pool_by_outside (bpa_main_t *bm, u32 addr, u32 *pool_index)
{
  bpa_pool_t *p;
  u32 idx;

  pool_foreach (p, bm->pools)
    {
      if (bpa_set_index_of (&p->outside, addr, &idx) == 0)
	{
	  *pool_index = p - bm->pools;
	  return 0;
	}
    }
  return -1;
}

/* IP protocol number -> bpa_proto_t, or -1 when we do not translate it. */
static_always_inline int
bpa_proto_of (u8 ip_proto)
{
  switch (ip_proto)
    {
    case IP_PROTOCOL_TCP:
      return BPA_PROTO_TCP;
    case IP_PROTOCOL_UDP:
      return BPA_PROTO_UDP;
    case IP_PROTOCOL_ICMP:
      return BPA_PROTO_ICMP;
    default:
      return -1;
    }
}

#endif /* __included_bpa_h__ */
