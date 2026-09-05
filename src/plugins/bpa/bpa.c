/* bpa.c - plugin registration and pool management. */

#include <vlib/vlib.h>
#include <vnet/plugin/plugin.h>
#include <vpp/app/version.h>
#include <vnet/ip/ip_interface.h>

#include "bpa.h"

bpa_main_t bpa_main;

/*
 * Refuses a pool whose public set contains an address configured on one of
 * our own interfaces.
 *
 * bpa-out2in runs on the ip4-unicast arc, before ip4-lookup and therefore
 * before ip4-local decides a packet is for us. It would happily translate
 * the box's own BGP session or OSPF adjacency, which then breaks
 * intermittently with nothing obvious in any log. Catching it here costs
 * one walk over the interface addresses at config time.
 */
static clib_error_t *
bpa_check_local_overlap (const bpa_addr_set_t *outside, u8 *name)
{
  vnet_main_t *vnm = vnet_get_main ();
  ip4_main_t *im = &ip4_main;
  ip_lookup_main_t *lm = &im->lookup_main;
  vnet_sw_interface_t *si;
  ip4_address_t clash;
  u32 clash_sw_if_index = ~0;
  int found = 0;

  pool_foreach (si, vnm->interface_main.sw_interfaces)
    {
      ip_interface_address_t *ia;

      if (found)
	break;

      foreach_ip_interface_address (
	lm, ia, si->sw_if_index, 1 /* honor unnumbered */, ({
	  ip4_address_t *a = ip_interface_address_get_address (lm, ia);
	  u32 host = clib_net_to_host_u32 (a->as_u32);
	  u32 idx;

	  if (!found && bpa_set_index_of (outside, host, &idx) == 0)
	    {
	      found = 1;
	      clash = *a;
	      clash_sw_if_index = si->sw_if_index;
	    }
	}));
    }

  if (found)
    return clib_error_return (
      0,
      "pool '%v': o endereco publico %U esta configurado em %U. O CGNAT "
      "traduziria o proprio trafego de controle da caixa (BGP, OSPF) e a "
      "sessao cairia sem motivo aparente",
      name, format_ip4_address, &clash, format_vnet_sw_if_index_name, vnm,
      clash_sw_if_index);

  return 0;
}

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

  e = bpa_check_local_overlap (&cfg->outside, cfg->name);
  if (e)
    return e;

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

  if (p->mode == BPA_MODE_DYNAMIC)
    {
      u32 i, nw, w;

      for (i = 0; i < vec_len (bm->per_thread); i++)
	{
	  bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, i);

	  /* thread 0 only carries blocks when there are no workers */
	  if (bm->n_workers && i == 0)
	    continue;

	  bpa_worker_id (bm, i, &w, &nw);
	  vec_validate (pt->dyn, p - bm->pools);
	  e = bpa_dyn_init (vec_elt_at_index (pt->dyn, p - bm->pools), p, w,
			    nw);
	  if (e)
	    {
	      hash_unset_mem (bm->pool_by_name, p->name);
	      vec_free (p->name);
	      pool_put (bm->pools, p);
	      vlib_worker_thread_barrier_release (bm->vlib_main);
	      return e;
	    }
	}
    }

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
  {
    u32 i;
    for (i = 0; i < vec_len (bm->per_thread); i++)
      {
	bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, i);
	if (q[0] < vec_len (pt->dyn))
	  bpa_dyn_free (vec_elt_at_index (pt->dyn, q[0]));
      }
  }
  hash_unset_mem (bm->pool_by_name, p->name);
  vec_free (p->name);
  pool_put (bm->pools, p);

  vlib_worker_thread_barrier_release (bm->vlib_main);
  return 0;
}

/*
 * The mirror of bpa_check_local_overlap(): an address configured after the
 * pool exists. We cannot refuse it from here, so we make the noise loud
 * enough that it is found before a customer does.
 */
static void
bpa_ip4_address_change (ip4_main_t *im, uword opaque, u32 sw_if_index,
			ip4_address_t *address, u32 address_length,
			u32 if_address_index, u32 is_delete)
{
  bpa_main_t *bm = &bpa_main;
  vnet_main_t *vnm = vnet_get_main ();
  bpa_pool_t *p;
  u32 host, idx;

  if (is_delete)
    return;

  host = clib_net_to_host_u32 (address->as_u32);

  pool_foreach (p, bm->pools)
    {
      if (bpa_set_index_of (&p->outside, host, &idx) == 0)
	vlib_log_err (
	  bm->log_class,
	  "%U foi configurado em %U mas pertence ao pool publico '%v'. O "
	  "trafego de controle da caixa nesse endereco sera traduzido pelo "
	  "CGNAT. Remova o endereco ou o pool.",
	  format_ip4_address, address, format_vnet_sw_if_index_name, vnm,
	  sw_if_index, p->name);
    }
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
  bm->log_class = vlib_log_register_class ("bpa", 0);

  {
    ip4_main_t *im = &ip4_main;
    ip4_add_del_interface_address_callback_t cb = {
      .function = bpa_ip4_address_change,
      .function_opaque = 0,
    };
    vec_add1 (im->add_del_interface_address_callbacks, cb);
  }

  bm->timeouts.udp = BPA_TIMEOUT_UDP;
  bm->timeouts.icmp = BPA_TIMEOUT_ICMP;
  bm->timeouts.tcp_trans = BPA_TIMEOUT_TCP_TRANS;
  bm->timeouts.tcp_estab = BPA_TIMEOUT_TCP_ESTAB;
  bm->block_holddown = BPA_BLOCK_HOLDDOWN;

  /*
   * Subscribers are spread over the workers with the same reciprocal used
   * by the pool split, so the worker count need not be a power of two.
   * With no workers configured everything stays on thread 0 and the
   * handoff nodes are never put on the feature arc.
   */
  bm->n_workers = vlib_num_workers ();
  bpa_divisor_init (&bm->workers, bm->n_workers ? bm->n_workers : 1);

  bm->in2out_fq_index =
    vlib_frame_queue_main_init (bpa_in2out_node.index, 0);
  bm->out2in_fq_index =
    vlib_frame_queue_main_init (bpa_out2in_node.index, 0);

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
