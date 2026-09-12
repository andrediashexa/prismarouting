/*
 * bpa_log.c - IPFIX export of block events (RFC 8158).
 *
 * Reuses VPP's vnet/ipfix-export infrastructure: the template, the sequence
 * number and the transport already exist. What this file adds is the RFC 8158
 * Table 21 template and the emission of the events themselves.
 *
 * There is no per-flow record, deliberately. In dynamic mode one record goes
 * out per allocation and one per release; in deterministic mode none goes out
 * at all, because there is no event - the proof is the algorithm plus the
 * configuration.
 */

#include <vnet/ipfix-export/flow_report.h>
#include <vnet/udp/udp_local.h>

#include "bpa.h"
#include "bpa_log.h"

bpa_log_main_t bpa_log_main;

/*
 * RFC 8158 Table 21 template.
 *
 * portRangeEnd and natPoolId are optional in the standard, but they go along:
 * without the end of the block the collector would have to know the block_size
 * in force on the date of the event to know the range, and that is exactly the
 * information the configuration trail exists to keep.
 */
static ipfix_report_element_t bpa_block_elements[] = {
  { observationTimeMilliseconds, 8 },
  { natEvent, 1 },
  { sourceIPv4Address, 4 },
  { postNATSourceIPv4Address, 4 },
  { portRangeStart, 2 },
  { portRangeEnd, 2 },
  { natPoolId, 4 },
};

static vlib_frame_t *
bpa_log_data_callback (flow_report_main_t *frm, ipfix_exporter_t *exp,
		       flow_report_t *fr, vlib_frame_t *f, u32 *to_next,
		       u32 node_index)
{
  /*
   * Records are pushed by bpa_log_drain(), which writes and dispatches in the
   * same pass. This callback exists only because the infrastructure demands
   * it; there is nothing to pull on a timer.
   */
  return f;
}

clib_error_t *
bpa_log_enable (int enable)
{
  bpa_log_main_t *lm = &bpa_log_main;
  flow_report_main_t *frm = &flow_report_main;
  vnet_flow_report_add_del_args_t a;
  ipfix_exporter_t *exp;
  u16 template_id = 0;
  int rv;

  if ((u8) (enable != 0) == lm->enabled)
    return 0;

  exp = pool_elt_at_index (frm->exporters, 0);

  /* The refusal that made UDP export impossible on a shipped box for the
   * life of the product (#209): VPP's own exporter has no destination unless
   * something sets one, and until the VTY grew a statement for it nothing
   * did. The advice names the VTY statement and not VPP's CLI command,
   * because this sentence is now carried to the operator's screen by the
   * binary API reply (#76) and the operator is at the VTY. */
  if (enable && ip_address_is_zero (&exp->ipfix_collector))
    return clib_error_return (
      0, "no IPFIX collector configured; use "
	 "'cgnat logging udp <collector> src <source>' first");

  clib_memset (&a, 0, sizeof (a));
  a.is_add = enable;
  a.domain_id = 1;
  a.src_port = UDP_DST_PORT_ipfix;
  a.rewrite_callback = vnet_flow_rewrite_generic_callback;
  a.flow_data_callback = bpa_log_data_callback;
  a.report_elements = bpa_block_elements;
  a.n_report_elements = ARRAY_LEN (bpa_block_elements);
  a.stream_indexp = &lm->stream_index;

  rv = vnet_flow_report_add_del (exp, &a, &template_id);
  if (rv)
    return flow_report_add_del_error_to_clib_error (rv);

  lm->template_id = template_id;
  lm->flow_report_index = a.flow_report_index;
  lm->enabled = enable ? 1 : 0;
  return 0;
}

/*
 * Queues an event from the dataplane. The queue is per thread, so there is no
 * lock: each worker only ever writes to its own.
 */
void
bpa_log_queue_event (bpa_per_thread_t *pt, u8 event, u32 ip_priv, u32 ip_pub,
		     u16 port_lo, u16 port_hi, u32 pool_id)
{
  bpa_log_event_t *e;

  /* One enabled transport is enough: with only TCP on, the previous version
   * dropped every allocation event here, silently. */
  if (PREDICT_FALSE (!bpa_log_main.enabled && !bpa_log_tcp.enabled))
    return;

  if (PREDICT_FALSE (vec_len (pt->log_queue) >= BPA_LOG_QUEUE_MAX))
    {
      pt->log_dropped++;
      return;
    }

  vec_add2 (pt->log_queue, e, 1);
  e->event = event;
  e->ip_priv = ip_priv;
  e->ip_pub = ip_pub;
  e->port_lo = port_lo;
  e->port_hi = port_hi;
  e->pool_id = pool_id;
}

/*
 * Dispatches the current thread's partial buffer, if any.
 *
 * Leaving this to flow_data_callback is not enough: the buffer written in one
 * pass was lost before the next callback fired, and only the last batch ever
 * reached the collector. Writing and dispatching have to be the same pass.
 * mesma operacao.
 */
static void
bpa_log_flush (vlib_main_t *vm)
{
  bpa_log_main_t *lm = &bpa_log_main;
  flow_report_main_t *frm = &flow_report_main;
  flow_report_per_thread_t *ptd;
  ipfix_exporter_t *exp;
  flow_report_t *fr;

  if (!lm->enabled)
    return;

  lm->n_flush_called++;
  exp = pool_elt_at_index (frm->exporters, 0);
  fr = pool_elt_at_index (exp->reports, lm->flow_report_index);

  if (vm->thread_index >= vec_len (fr->per_thread_data))
    return;

  ptd = vec_elt_at_index (fr->per_thread_data, vm->thread_index);

  if (ptd->buffer == 0 || ptd->next_data_offset <= exp->all_headers_size)
    {
      lm->n_flush_empty++;
      return;
    }

  lm->n_flush_sent++;

  vnet_ipfix_exp_send_buffer (
    vm, exp, fr, pool_elt_at_index (exp->streams, fr->stream_index),
    vm->thread_index, ptd->buffer);
}

/*
 * Drains every queue. Runs under the worker barrier, in process context, which
 * is the only place from which the IPFIX export actually reaches the wire.
 */
/*
 * Emits on the enabled transports. UDP for telemetry, TCP for the log that
 * cannot lose a record; both may be on at the same time.
 *
 * The wall clock is read HERE, once, and handed to both. It used to be a
 * parameter, and both call sites passed vlib_time_now() - VPP's clock since
 * process start - so the TCP exporter stamped allocations "00:00:05" while
 * the UDP one stamped the same event "14:04:43". A retention log answers
 * "who held this address at this time", and the two transports have to agree
 * or neither corroborates the other. See
 * https://github.com/andrediashexa/hexarouting/issues/117.
 */
void
bpa_log_emit (vlib_main_t *vm, u8 event, u32 ip_priv, u32 ip_pub, u16 port_lo,
	      u16 port_hi, u32 pool_id)
{
  f64 wall = unix_time_now ();

  bpa_log_block_event (vm, vm->thread_index, wall, event, ip_priv, ip_pub,
		       port_lo, port_hi, pool_id);
  bpa_log_tcp_record (wall, event, ip_priv, ip_pub, port_lo, port_hi, pool_id);
}

void
bpa_log_drain (vlib_main_t *vm)
{
  bpa_main_t *bm = &bpa_main;
  flow_report_main_t *frm = &flow_report_main;
  ipfix_exporter_t *exp;
  flow_report_t *fr;
  u32 i, j;

  if (!bpa_log_main.enabled && !bpa_log_tcp.enabled)
    return;

  /*
   * A record emitted before the first template is undecodable: the collector
   * does not know the format and drops it. Observed in lab/ipfix.sh, where the
   * first four events went out on the wire and no collector could have read
   * them. For a compliance log that is silent loss right after a restart,
   * which is exactly when nobody is watching.
   *
   * Events stay queued until the template goes out. The queue is bounded, so
   * an unreachable collector produces accounted loss rather than memory
   * growing without end.
   */
  if (bpa_log_main.enabled)
    {
      exp = pool_elt_at_index (frm->exporters, 0);
      fr = pool_elt_at_index (exp->reports, bpa_log_main.flow_report_index);
      if (fr->last_template_sent == 0)
	return;
    }

  for (i = 0; i < vec_len (bm->per_thread); i++)
    {
      bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, i);

      for (j = 0; j < vec_len (pt->log_queue); j++)
	{
	  bpa_log_event_t *e = vec_elt_at_index (pt->log_queue, j);

	  bpa_log_emit (vm, e->event, e->ip_priv, e->ip_pub, e->port_lo,
			e->port_hi, e->pool_id);
	}
      vec_reset_length (pt->log_queue);
    }

  bpa_log_flush (vm);
  bpa_log_tcp_service (vlib_time_now (vm));
}

void
bpa_log_block_event (vlib_main_t *vm, u32 thread_index, f64 wall, u8 event,
		     u32 ip_priv, u32 ip_pub, u16 port_lo, u16 port_hi,
		     u32 pool_id)
{
  bpa_log_main_t *lm = &bpa_log_main;
  flow_report_main_t *frm = &flow_report_main;
  flow_report_per_thread_t *ptd;
  ipfix_exporter_t *exp;
  flow_report_t *fr;
  vlib_buffer_t *b0;
  u64 now_ms;
  u16 offset;
  u8 *d;

  if (PREDICT_TRUE (!lm->enabled))
    {
      lm->n_dropped++;
      return;
    }

  exp = pool_elt_at_index (frm->exporters, 0);
  fr = pool_elt_at_index (exp->reports, lm->flow_report_index);

  b0 = vnet_ipfix_exp_get_buffer (vm, exp, fr, thread_index);
  if (PREDICT_FALSE (b0 == 0))
    {
      lm->n_dropped++;
      lm->n_getbuf_fail++;
      return;
    }

  ptd = vec_elt_at_index (fr->per_thread_data, thread_index);
  offset = ptd->next_data_offset;
  d = (u8 *) vlib_buffer_get_current (b0) + offset;

  /* Milliseconds since the epoch, from the instant bpa_log_emit() read - the
   * same one the TCP exporter stamps, so the same event carries the same time
   * on both. Computing it again here would drift the two apart by however
   * long the fan-out took, which is small and is still two answers to one
   * question. */
  now_ms = (u64) (wall * 1e3);

  clib_memcpy_fast (d, &(u64){ clib_host_to_net_u64 (now_ms) }, 8);
  d += 8;
  d[0] = event;
  d += 1;
  clib_memcpy_fast (d, &(u32){ clib_host_to_net_u32 (ip_priv) }, 4);
  d += 4;
  clib_memcpy_fast (d, &(u32){ clib_host_to_net_u32 (ip_pub) }, 4);
  d += 4;
  clib_memcpy_fast (d, &(u16){ clib_host_to_net_u16 (port_lo) }, 2);
  d += 2;
  clib_memcpy_fast (d, &(u16){ clib_host_to_net_u16 (port_hi) }, 2);
  d += 2;
  clib_memcpy_fast (d, &(u32){ clib_host_to_net_u32 (pool_id) }, 4);

  offset += BPA_LOG_RECORD_SIZE;
  ptd->next_data_offset = offset;
  b0->current_length = offset;
  lm->n_records++;

  /* dispatch before the next record would not fit */
  if (offset + BPA_LOG_RECORD_SIZE > exp->path_mtu)
    vnet_ipfix_exp_send_buffer (
      vm, exp, fr, pool_elt_at_index (exp->streams, fr->stream_index),
      thread_index, b0);
}
