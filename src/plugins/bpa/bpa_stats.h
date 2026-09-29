/*
 * The CGNAT's state in VPP's statistics segment (#504).
 *
 * WHY HERE AND NOT ON A SCREEN. Every runtime number this plugin had -
 * subscribers, blocks, sessions, refusals - existed only as prose on the
 * `show bpa` screens, and the SNMP agent got its aggregates by parsing
 * `show bpa top 100000`: a walk of every subscriber in every worker's table,
 * on each refresh. On a box sized for a million subscribers that is a million
 * entries walked every ten seconds to answer six numbers.
 *
 * So the numbers are kept where they change, per pool and per thread, in
 * VPP's own simple counters - the kind /if/rx is - and a reader sums the
 * threads. The thread is the one that OWNS the subscriber, never the one
 * running the code: sessions expire on the main thread under the barrier,
 * and a decrement charged to thread 0 would leave the worker's slot high and
 * thread 0's wrapped below zero. The sum would still be right modulo 2^64;
 * a reader that summed and printed would not be.
 *
 * Read with `vpp_get_stats socket-name /run/vpp/stats.sock dump "^/bpa/"`:
 *
 *   /bpa/pool/names          the pool's name, by pool index
 *   /bpa/pool/subscribers    subscribers holding a block now
 *   /bpa/pool/blocks         blocks those subscribers hold
 *   /bpa/pool/sessions       sessions open
 *   /bpa/pool/refused        times a subscriber was refused a port (counter)
 *   /bpa/log/<transport>/... the IPFIX, TCP and syslog transports' counters
 */
#ifndef included_bpa_stats_h
#define included_bpa_stats_h

#include <vlib/vlib.h>
#include <vlib/counter.h>
#include <vlib/stats/stats.h>
#include "bpa.h"

typedef struct
{
  vlib_simple_counter_main_t subscribers;
  vlib_simple_counter_main_t blocks;
  vlib_simple_counter_main_t sessions;
  vlib_simple_counter_main_t refused;
  /* What the pool IS, set once it is built, in thread 0's slot: mode (1
   * deterministic, 2 dynamic), block size, inside and outside addresses,
   * blocks it can hand out, subscribers it can serve and the floor it
   * guarantees on this box's workers. */
  vlib_simple_counter_main_t conf[7];
  /* /bpa/pool/names, a string vector indexed like the counters */
  vlib_stats_string_vector_t names;
  /* gauges: IPFIX, then the TCP and syslog streams */
  u32 g_ipfix[4];
  u32 g_tcp[7];
  u32 g_syslog[7];
  u8 ready;
} bpa_stats_main_t;

extern bpa_stats_main_t bpa_stats_main;

void bpa_stats_init (void);
void bpa_stats_pool_add (u32 pool_index, const u8 *name);
void bpa_stats_pool_describe (u32 pool_index, const bpa_pool_t *p);
void bpa_stats_pool_del (u32 pool_index);
void bpa_stats_publish (void);

/* not a unit: VPP's simple counter, for the thread that owns the subscriber;
 * lab/snmp.sh reads the result through vpp_get_stats and through SNMP */
static_always_inline void
bpa_stats_add (vlib_simple_counter_main_t *cm, u32 thread_index,
	       u32 pool_index, i64 n)
{
  if (PREDICT_FALSE (!bpa_stats_main.ready))
    return;
  if (n >= 0)
    vlib_increment_simple_counter (cm, thread_index, pool_index, n);
  else
    vlib_decrement_simple_counter (cm, thread_index, pool_index, -n);
}

#endif /* included_bpa_stats_h */
