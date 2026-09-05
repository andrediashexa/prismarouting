/* bpa.c - plugin registration and pool management. */

#include <vlib/vlib.h>
#include <vnet/plugin/plugin.h>
#include <vpp/app/version.h>

#include "bpa.h"

bpa_main_t bpa_main;

clib_error_t *
bpa_pool_add (bpa_pool_t *cfg, u32 *pool_index)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;
  clib_error_t *e;
  uword *q;

  q = hash_get_mem (bm->pool_by_name, cfg->name);
  if (q)
    return clib_error_return (0, "pool already exists");

  /* Workers read bm->pools on the datapath; mutate it under the barrier. */
  vlib_worker_thread_barrier_sync (bm->vlib_main);

  pool_get_zero (bm->pools, p);
  *p = *cfg;

  e = bpa_pool_init (p);
  if (e)
    {
      pool_put (bm->pools, p);
      vlib_worker_thread_barrier_release (bm->vlib_main);
      return e;
    }

  p->name = vec_dup (cfg->name);
  hash_set_mem (bm->pool_by_name, p->name, p - bm->pools);

  vlib_worker_thread_barrier_release (bm->vlib_main);

  if (pool_index)
    *pool_index = p - bm->pools;
  return 0;
}

clib_error_t *
bpa_pool_del (u8 *name)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;
  uword *q;

  q = hash_get_mem (bm->pool_by_name, name);
  if (!q)
    return clib_error_return (0, "no such pool");

  vlib_worker_thread_barrier_sync (bm->vlib_main);

  p = pool_elt_at_index (bm->pools, q[0]);
  hash_unset_mem (bm->pool_by_name, p->name);
  vec_free (p->name);
  pool_put (bm->pools, p);

  vlib_worker_thread_barrier_release (bm->vlib_main);
  return 0;
}

static clib_error_t *
bpa_init (vlib_main_t *vm)
{
  bpa_main_t *bm = &bpa_main;
  vlib_thread_main_t *tm = vlib_get_thread_main ();
  clib_error_t *e;
  u32 i;

  bm->vlib_main = vm;
  bm->vnet_main = vnet_get_main ();
  bm->pool_by_name = hash_create_vec (0, sizeof (u8), sizeof (uword));

  bm->timeouts.udp = BPA_TIMEOUT_UDP;
  bm->timeouts.icmp = BPA_TIMEOUT_ICMP;
  bm->timeouts.tcp_trans = BPA_TIMEOUT_TCP_TRANS;
  bm->timeouts.tcp_estab = BPA_TIMEOUT_TCP_ESTAB;

  /* One session table per vlib main, worker or not: index by thread. */
  vec_validate (bm->per_thread, tm->n_vlib_mains - 1);
  for (i = 0; i < vec_len (bm->per_thread); i++)
    {
      e = bpa_per_thread_init (vec_elt_at_index (bm->per_thread, i), i);
      if (e)
	return e;
    }

  return 0;
}

VLIB_INIT_FUNCTION (bpa_init);

VLIB_PLUGIN_REGISTER () = {
  .version = "0.1",
  .description = "CGNAT com Bloco de Portas Alocadas (BPA)",
};
