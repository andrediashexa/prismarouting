/* bpa.h - CGNAT com Bloco de Portas Alocadas. */

#ifndef __included_bpa_h__
#define __included_bpa_h__

#include <vlib/vlib.h>
#include <vnet/vnet.h>
#include <vnet/ip/ip.h>

#include "bpa_pool.h"
#include "bpa_ports.h"

typedef struct
{
  bpa_pool_t *pools;	/* pool vector, indexed by pool id */
  uword *pool_by_name;	/* name -> pool index */

  u16 msg_id_base;
  vlib_main_t *vlib_main;
  vnet_main_t *vnet_main;
} bpa_main_t;

extern bpa_main_t bpa_main;

clib_error_t *bpa_pool_add (bpa_pool_t *cfg, u32 *pool_index);
clib_error_t *bpa_pool_del (u8 *name);

format_function_t format_bpa_pool;
format_function_t format_bpa_policy;
format_function_t format_bpa_mode;

#endif /* __included_bpa_h__ */
