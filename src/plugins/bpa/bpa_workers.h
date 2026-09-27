/*
 * bpa_workers.h - which worker owns a subscriber, and which thread that is
 * (#466, #472).
 *
 * Every packet of a subscriber has to be translated on the one worker that
 * holds its sessions and its block, so the two directions of this mapping -
 * subscriber to thread for the handoff and the configuration, thread to
 * worker for the allocator's slice - have to be each other's inverse. A
 * subscriber sent to a thread that does not recognise itself as its owner is
 * translated from an empty table. These were bpa.h's and bpa.c's, which only
 * VPP compiles, and had no test; here they are plain functions of the
 * tables, and test/test_bpa_workers.c holds the round trip.
 */
#ifndef __included_bpa_workers_h__
#define __included_bpa_workers_h__

#include "bpa_pool.h"
#ifdef BPA_STANDALONE
#include "bpa_vec_shim.h"
/* VPP numbers its threads main first: worker `wid` is thread wid + 1. */
#define bpa_thread_of_wid(wid) ((wid) + 1)
#else
#define bpa_thread_of_wid(wid) vlib_get_worker_thread_index (wid)
#endif

/*
 * The thread-to-worker table for these owner workers (VPP worker ids, in
 * order): entry t is the index k of the worker whose thread is t, and ~0 for
 * a thread that only receives and hands over - the main thread included.
 * `n_vpp_workers` sizes it to every thread the dataplane has.
 */
static_always_inline u32 *
bpa_thread_table (const u32 *wids, u32 n_vpp_workers)
{
  u32 *thread_w = 0, t, k;

  for (t = 0; t <= n_vpp_workers; t++)
    vec_add1 (thread_w, ~0u);
  for (k = 0; k < vec_len (wids); k++)
    thread_w[bpa_thread_of_wid (wids[k])] = k;
  return thread_w;
}

/*
 * This thread's identity for block sharding: its worker index and the
 * worker count. With no workers configured everything runs on thread 0 and
 * behaves as a single slice.
 */
static_always_inline void
bpa_thread_worker (u32 n_workers, const u32 *thread_w, u32 thread_index,
		   u32 *w, u32 *nw)
{
  if (PREDICT_TRUE (n_workers != 0))
    {
      *nw = n_workers;
      *w = thread_index < vec_len (thread_w) ? thread_w[thread_index] : ~0u;
    }
  else
    {
      *nw = 1;
      *w = 0;
    }
}

/* The thread that holds this subscriber's state: its owner worker's. */
static_always_inline u32
bpa_owner_thread (u32 n_workers, const u32 *owner_wid,
		  const bpa_divisor_t *workers, u32 sub_idx)
{
  if (n_workers == 0)
    return 0;
  return bpa_thread_of_wid (owner_wid[bpa_owner_worker (workers, sub_idx)]);
}

#endif /* __included_bpa_workers_h__ */
