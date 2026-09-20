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
#include "bpa_radius.h"

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

/*
 * The boot marker's template: when this event was written, and when the
 * dataplane process that wrote it started. IE 160 is IANA's
 * systemInitTimeMilliseconds, "the absolute timestamp of the last
 * (re-)initialization of the IPFIX Device" - the argument for using it, and
 * for the three shapes that were not used, is in bpa_log.h.
 */
static ipfix_report_element_t bpa_marker_elements[] = {
  { observationTimeMilliseconds, 8 },
  { systemInitTimeMilliseconds, 8 },
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

  /*
   * The marker's own template, on the SAME stream: one stream may carry
   * several templates, and sharing it keeps one sequence number across both,
   * so a collector reads one ordered flow of records instead of two it has to
   * interleave by timestamp.
   */
  a.report_elements = bpa_marker_elements;
  a.n_report_elements = ARRAY_LEN (bpa_marker_elements);
  /* Distinct from the block report, which is registered with the same
   * callback: the remove path matches on the pair and would otherwise have
   * two identical reports to choose between. */
  a.opaque.as_uword = 1;
  rv = vnet_flow_report_add_del (exp, &a, &template_id);
  if (rv)
    return flow_report_add_del_error_to_clib_error (rv);

  lm->marker_template_id = template_id;
  lm->marker_report_index = a.flow_report_index;
  lm->enabled = enable ? 1 : 0;

  /* A transport coming up where there was none: every block that is pinned
   * right now has an allocation nobody has recorded, and a pinned block never
   * produces another event of its own until the operator removes the forward.
   * Guarded on the transition so that turning the second transport on does not
   * announce them all again. */
  if (lm->enabled && !bpa_log_tcp.enabled)
    {
      /* The marker first: a collector has to know which incarnation the
       * records that follow belong to before it reads them. */
      lm->next_marker = 0;
      bpa_log_pins_replay ();
    }
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
   * dropped every allocation event here, silently - and the syslog stream
   * arrived into exactly that trap, which lab/syslog.sh caught on its first
   * run. Every transport this box has belongs in this test, and the test is
   * here rather than at each exporter because this is where the event is
   * still a queue entry rather than a record (#20). */
  if (PREDICT_FALSE (!bpa_log_main.enabled && !bpa_log_tcp.enabled
		     && !bpa_log_syslog.enabled))
    return;

  if (PREDICT_FALSE (vec_len (pt->log_queue) >= BPA_LOG_QUEUE_MAX))
    {
      pt->log_dropped++;
      return;
    }

  vec_add2 (pt->log_queue, e, 1);
  /* THE IDENTITY IS TAKEN HERE, at the instant of the allocation, for the
   * reason the field's own comment gives: a lookup at drain time would name
   * whoever holds the address by then (#21). */
  bpa_identity_lookup (&bpa_main.identity, ip_priv, e->identity,
		       sizeof (e->identity));
  /*
   * AND THE BLOCK REMEMBERS IT, so that its release says the same thing
   * (#397). The maintainer's decision, of the three answers that issue sets
   * out: a release repeats what the allocation said, rather than naming
   * whoever holds the private address when the block goes back.
   *
   * Here and not in the allocator: this is the one place the lookup already
   * happens, so nothing on the packet path changes and a box with no identity
   * table pays nothing - `remember` stores no unknown.
   *
   * A release reads it back, and reading forgets: a block has one release, and
   * an entry that outlived it would be a leak with a court record's name on
   * it. When nothing was remembered - a block allocated before this version,
   * or a subscriber the table never had - what is in `e->identity` is the
   * lookup just made, which is what every release carried before #397.
   */
  if (event == BPA_NAT_EVENT_BLOCK_ALLOC)
    bpa_block_ident_remember (
      &pt->block_ident, bpa_block_ident_key (pool_id, ip_pub, port_lo),
      e->identity);
  else if (event == BPA_NAT_EVENT_BLOCK_DEALLOC)
    bpa_block_ident_recall (&pt->block_ident,
			    bpa_block_ident_key (pool_id, ip_pub, port_lo),
			    e->identity, sizeof (e->identity));
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
	      u16 port_hi, u32 pool_id, const char *identity)
{
  f64 wall = unix_time_now ();

  bpa_log_block_event (vm, vm->thread_index, wall, event, ip_priv, ip_pub,
		       port_lo, port_hi, pool_id);
  bpa_log_tcp_record (wall, event, ip_priv, ip_pub, port_lo, port_hi, pool_id);
  /* The third transport, and the last line of the fan-out this function's
   * own comment exists for: a caller that reaches one exporter directly will
   * sooner or later reach only that one (#20). */
  bpa_log_stream_record (&bpa_log_syslog, wall, event, ip_priv, ip_pub,
			 port_lo, port_hi, pool_id, identity);
}

/*
 * The boot marker record: when it was written, and when this incarnation of
 * the dataplane started.
 *
 * Both transports, through one call, for the reason bpa_log_emit() gives: a
 * caller that reaches one exporter directly will sooner or later reach only
 * that one. The wall clock is read once here and handed to both, so the same
 * marker carries the same time on both.
 */
void
bpa_log_marker_emit (vlib_main_t *vm)
{
  bpa_log_main_t *lm = &bpa_log_main;
  bpa_main_t *bm = &bpa_main;
  flow_report_main_t *frm = &flow_report_main;
  flow_report_per_thread_t *ptd;
  ipfix_exporter_t *exp;
  flow_report_t *fr;
  vlib_buffer_t *b0;
  f64 wall = unix_time_now ();
  u16 offset;
  u8 *d;

  bpa_log_tcp_marker (wall, bm->boot_wall);
  /* Every enabled stream gets the marker, for the reason there is one: a
   * collector cannot read an allocation with no release until it knows which
   * incarnation of the box wrote it (#20). */
  bpa_log_stream_marker (&bpa_log_syslog, wall, bm->boot_wall);

  if (!lm->enabled)
    return;

  exp = pool_elt_at_index (frm->exporters, 0);
  fr = pool_elt_at_index (exp->reports, lm->marker_report_index);

  b0 = vnet_ipfix_exp_get_buffer (vm, exp, fr, vm->thread_index);
  if (b0 == 0)
    {
      lm->n_getbuf_fail++;
      return;
    }

  ptd = vec_elt_at_index (fr->per_thread_data, vm->thread_index);
  offset = ptd->next_data_offset;
  d = (u8 *) vlib_buffer_get_current (b0) + offset;

  clib_memcpy_fast (d, &(u64){ clib_host_to_net_u64 ((u64) (wall * 1e3)) }, 8);
  d += 8;
  clib_memcpy_fast (
    d, &(u64){ clib_host_to_net_u64 ((u64) (bm->boot_wall * 1e3)) }, 8);

  offset += BPA_LOG_MARKER_RECORD_SIZE;
  ptd->next_data_offset = offset;
  b0->current_length = offset;
  lm->n_markers++;

  /*
   * Dispatched here and not left to the next flush. The marker's own buffer
   * belongs to its own report, and bpa_log_flush() only ever dispatches the
   * block report's - so a marker left in it would sit there until another
   * marker filled the buffer, which at one record every half minute is never.
   */
  vnet_ipfix_exp_send_buffer (
    vm, exp, fr, pool_elt_at_index (exp->streams, fr->stream_index),
    vm->thread_index, b0);
}

void
bpa_log_drain (vlib_main_t *vm)
{
  bpa_main_t *bm = &bpa_main;
  flow_report_main_t *frm = &flow_report_main;
  ipfix_exporter_t *exp;
  flow_report_t *fr;
  u32 i, j;

  if (!bpa_log_main.enabled && !bpa_log_tcp.enabled
      && !bpa_log_syslog.enabled)
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

  /*
   * The marker, BEFORE the queue is drained, and that order is the whole
   * point of it rather than a detail of this function.
   *
   * bpa_log_enable() and bpa_log_tcp_enable() both arm it with
   * `next_marker = 0` and then queue the pins, each under a comment saying the
   * marker goes first, and docs/logging.md and the comment above
   * bpa_log_marker_emit() say the same. Arming is all either of them can do:
   * at that moment there is no vlib_main_t on the path and the block report's
   * template may not have gone out yet, which is what the early return above
   * guards. So the ordering is decided HERE, and since v0.67.0 - five
   * releases - this block sat below the drain and put every replayed pin
   * ahead of the marker that was supposed to introduce it (#282).
   *
   * What that costs is #217's argument read backwards: a collector takes
   * records arriving before this incarnation's marker as belonging to the
   * previous one, and the pins are this incarnation's allocations. A box that
   * has just rebooted announces every forward it carries, and every one of
   * them lands on the wrong side of the line.
   *
   * Safe above bpa_log_flush() because the marker does not travel in the block
   * report's buffer: bpa_log_marker_emit() dispatches its own, for the reason
   * given where it does it. And it carries both transports, since it opens
   * with bpa_log_tcp_marker() before testing lm->enabled.
   *
   * Over TCP this is the whole story - a stream delivers in order. Over UDP
   * two datagrams can still be reordered by the network, which is why the
   * marker repeats: one that arrives late is the same class of event as one
   * that is lost, and the next one closes it.
   *
   * Repeated rather than sent once, on its own timer: one 16-byte record every
   * half minute.
   */
  {
    f64 now = vlib_time_now (vm);

    if (now >= bpa_log_main.next_marker)
      {
	bpa_log_main.next_marker = now + BPA_LOG_MARKER_INTERVAL;
	bpa_log_marker_emit (vm);
      }
  }

  for (i = 0; i < vec_len (bm->per_thread); i++)
    {
      bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, i);

      for (j = 0; j < vec_len (pt->log_queue); j++)
	{
	  bpa_log_event_t *e = vec_elt_at_index (pt->log_queue, j);

	  bpa_log_emit (vm, e->event, e->ip_priv, e->ip_pub, e->port_lo,
			e->port_hi, e->pool_id, e->identity);
	}
      vec_reset_length (pt->log_queue);
    }

  bpa_log_flush (vm);

  bpa_log_tcp_service (vlib_time_now (vm));
  bpa_log_stream_service (&bpa_log_syslog, vlib_time_now (vm));
  /* And the accounting the BNG is sending, read from the same process for
   * the same reason: it is a socket, it is a few packets a second, and the
   * dataplane has no business parsing somebody else's protocol (#21). */
  bpa_radius_service ();
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
