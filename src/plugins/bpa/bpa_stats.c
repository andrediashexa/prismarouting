/*
 * The CGNAT's state in VPP's statistics segment (#504). bpa_stats.h says why
 * the numbers are kept where they change rather than walked.
 */
#include "bpa.h"
#include "bpa_log.h"
#include "bpa_stats.h"
#include <vlib/stats/stats.h>

bpa_stats_main_t bpa_stats_main;

static const char *conf_name[7] = {
  "bpa-pool-mode",     "bpa-pool-block-size", "bpa-pool-inside",
  "bpa-pool-outside",  "bpa-pool-blocks-total", "bpa-pool-capacity",
  "bpa-pool-guaranteed"
};
static const char *conf_path[7] = {
  "/bpa/pool/mode",     "/bpa/pool/block-size", "/bpa/pool/inside",
  "/bpa/pool/outside",  "/bpa/pool/blocks-total", "/bpa/pool/capacity",
  "/bpa/pool/guaranteed"
};

/* not a unit: registers VPP statistics-segment entries, which exist only in a
 * running vlib; lab/snmp.sh reads every one of them back */
void
bpa_stats_init (void)
{
  bpa_stats_main_t *sm = &bpa_stats_main;
  static const char *tcp[7] = { "enabled", "connected", "records", "sent",
				"spilled", "dropped", "connects" };
  u32 i;

  sm->subscribers.name = "bpa-pool-subscribers";
  sm->subscribers.stat_segment_name = "/bpa/pool/subscribers";
  sm->blocks.name = "bpa-pool-blocks";
  sm->blocks.stat_segment_name = "/bpa/pool/blocks";
  sm->sessions.name = "bpa-pool-sessions";
  sm->sessions.stat_segment_name = "/bpa/pool/sessions";
  sm->refused.name = "bpa-pool-refused";
  sm->refused.stat_segment_name = "/bpa/pool/refused";
  sm->names = vlib_stats_add_string_vector ("/bpa/pool/names");
  for (i = 0; i < 7; i++)
    {
      sm->conf[i].name = (char *) conf_name[i];
      sm->conf[i].stat_segment_name = (char *) conf_path[i];
    }

  sm->g_ipfix[0] = vlib_stats_add_gauge ("/bpa/log/ipfix/enabled");
  sm->g_ipfix[1] = vlib_stats_add_gauge ("/bpa/log/ipfix/records");
  sm->g_ipfix[2] = vlib_stats_add_gauge ("/bpa/log/ipfix/dropped");
  sm->g_ipfix[3] = vlib_stats_add_gauge ("/bpa/log/ipfix/markers");
  for (i = 0; i < 7; i++)
    {
      sm->g_tcp[i] = vlib_stats_add_gauge ("/bpa/log/tcp/%s", tcp[i]);
      sm->g_syslog[i] = vlib_stats_add_gauge ("/bpa/log/syslog/%s", tcp[i]);
    }
  sm->ready = 1;
}

/* not a unit: validates VPP counter vectors, which reallocates per-thread
 * memory and so runs on the main thread with the workers stopped - which is
 * where a pool is created. lab/snmp.sh creates one and reads its row. */
void
bpa_stats_pool_add (u32 pool_index, const u8 *name)
{
  bpa_stats_main_t *sm = &bpa_stats_main;
  u32 i;

  if (!sm->ready)
    return;
  vlib_validate_simple_counter (&sm->subscribers, pool_index);
  vlib_validate_simple_counter (&sm->blocks, pool_index);
  vlib_validate_simple_counter (&sm->sessions, pool_index);
  vlib_validate_simple_counter (&sm->refused, pool_index);
  for (i = 0; i < 7; i++)
    {
      vlib_validate_simple_counter (&sm->conf[i], pool_index);
      vlib_zero_simple_counter (&sm->conf[i], pool_index);
    }
  /* A pool index is reused after a delete: the new pool starts from zero,
   * not from what the last one left behind. */
  vlib_zero_simple_counter (&sm->subscribers, pool_index);
  vlib_zero_simple_counter (&sm->blocks, pool_index);
  vlib_zero_simple_counter (&sm->sessions, pool_index);
  vlib_zero_simple_counter (&sm->refused, pool_index);
  vlib_stats_set_string_vector (&sm->names, pool_index, "%v", name);
}

/* not a unit: VPP's counters, read from the pool bpa_pool_init() built;
 * lab/snmp.sh reads each back as the pool table's columns */
void
bpa_stats_pool_describe (u32 pool_index, const bpa_pool_t *p)
{
  bpa_stats_main_t *sm = &bpa_stats_main;
  u64 v[7];
  u32 i;

  if (!sm->ready)
    return;
  v[0] = p->mode == BPA_MODE_DYNAMIC ? 2 : 1;
  v[1] = p->block_size;
  v[2] = p->inside.count;
  v[3] = p->outside.count;
  v[4] = (u64) p->outside.count * p->blocks_mapped;
  v[5] = p->capacity;
  /* The same function the CLI and the API read (bpa_api.c): two copies of
   * a capacity figure is how one of them ends up wrong. */
  v[6] = bpa_pool_guaranteed (p, bpa_main.n_workers);
  for (i = 0; i < 7; i++)
    vlib_set_simple_counter (&sm->conf[i], 0, pool_index, v[i]);
}

/* not a unit: VPP's counters and string vector; lab/snmp.sh deletes a pool
 * and reads that its row is gone */
void
bpa_stats_pool_del (u32 pool_index)
{
  bpa_stats_main_t *sm = &bpa_stats_main;
  u32 i;

  if (!sm->ready || pool_index >= vec_len (sm->names))
    return;
  vlib_zero_simple_counter (&sm->subscribers, pool_index);
  vlib_zero_simple_counter (&sm->blocks, pool_index);
  vlib_zero_simple_counter (&sm->sessions, pool_index);
  vlib_zero_simple_counter (&sm->refused, pool_index);
  for (i = 0; i < 7; i++)
    vlib_zero_simple_counter (&sm->conf[i], pool_index);
  /* An empty name is the reader's sign that the index holds no pool. */
  vlib_stats_set_string_vector (&sm->names, pool_index, "%s", "");
}

/* not a unit: sets VPP gauges from the transports' own counters, which are
 * written by the main thread; called once an interval by
 * bpa_expire_process. lab/snmp.sh reads them back over SNMP. */
static void
bpa_stats_stream (const u32 *g, const bpa_log_stream_t *st)
{
  vlib_stats_set_gauge (g[0], st->enabled);
  vlib_stats_set_gauge (g[1], st->connected);
  vlib_stats_set_gauge (g[2], st->n_records);
  vlib_stats_set_gauge (g[3], st->n_sent);
  vlib_stats_set_gauge (g[4], st->n_spilled);
  vlib_stats_set_gauge (g[5], st->n_dropped);
  vlib_stats_set_gauge (g[6], st->n_connects);
}

/* not a unit: VPP gauges, see bpa_stats_stream */
void
bpa_stats_publish (void)
{
  bpa_stats_main_t *sm = &bpa_stats_main;
  bpa_log_main_t *lm = &bpa_log_main;

  if (!sm->ready)
    return;
  vlib_stats_set_gauge (sm->g_ipfix[0], lm->enabled);
  vlib_stats_set_gauge (sm->g_ipfix[1], lm->n_records);
  vlib_stats_set_gauge (sm->g_ipfix[2], lm->n_dropped);
  vlib_stats_set_gauge (sm->g_ipfix[3], lm->n_markers);
  bpa_stats_stream (sm->g_tcp, &bpa_log_tcp);
  bpa_stats_stream (sm->g_syslog, &bpa_log_syslog);
}
