/*
 * bpa_log.h - IPFIX export of block events (RFC 8158).
 *
 * Logs block allocation, not flows. That is what makes the CGNAT auditable
 * without producing terabytes a day. See docs/logging.md.
 */

#ifndef __included_bpa_log_h__
#define __included_bpa_log_h__

#include <vlib/vlib.h>
#include <vnet/ip/ip.h>

/* RFC 8158, natEvent record */
#define BPA_NAT_EVENT_PORTS_EXHAUSTED 12
#define BPA_NAT_EVENT_BLOCK_ALLOC     16
#define BPA_NAT_EVENT_BLOCK_DEALLOC   17

/* Tabela 21 da RFC 8158: 8 + 1 + 4 + 4 + 2 + 2 + 4 */
#define BPA_LOG_RECORD_SIZE 25

/*
 * An event queued by the dataplane.
 *
 * The node does NOT call the export path directly: emitting IPFIX from inside
 * graph dispatch does not work - the buffer is written but never reaches the
 * wire. Found in lab/ipfix.sh, where only the events emitted from process
 * context arrived at the collector. The dataplane queues, a process drains.
 */
typedef struct
{
  u32 ip_priv;
  u32 ip_pub;
  u32 pool_id;
  u16 port_lo;
  u16 port_hi;
  u8 event;
} bpa_log_event_t;

/* Per-thread queue ceiling: if the process falls behind, the excess is lost
 * in an accounted way, rather than growing without bound. */
#define BPA_LOG_QUEUE_MAX 8192

typedef struct
{
  u32 template_id;
  u32 stream_index;
  u32 flow_report_index;
  u64 n_records;
  u64 n_flush_called;
  u64 n_flush_sent;
  u64 n_flush_empty;
  u64 n_getbuf_fail;
  u64 n_dropped; /* no collector configured */
  u8 enabled;
} bpa_log_main_t;

extern bpa_log_main_t bpa_log_main;

clib_error_t *bpa_log_enable (int enable);

/*
 * Emits on every enabled transport. The single fan-out point: a caller that
 * reaches one exporter directly will sooner or later reach only that one.
 *
 * It reads the wall clock itself rather than taking it as a parameter,
 * because taking it as a parameter is how the compliance log came to carry
 * "00:00:05" instead of the time of day - vlib_time_now() counts from when
 * VPP started, and it was the value at hand at both call sites.
 */
void bpa_log_emit (vlib_main_t *vm, u8 event, u32 ip_priv, u32 ip_pub,
		   u16 port_lo, u16 port_hi, u32 pool_id);

/* Actually emits over UDP. May only be called from process context.
 * wall is seconds since the UNIX epoch, not since VPP started. */
void bpa_log_block_event (vlib_main_t *vm, u32 thread_index, f64 wall,
			  u8 event, u32 ip_priv, u32 ip_pub, u16 port_lo,
			  u16 port_hi, u32 pool_id);

/*
 * Export over TCP, for when losing a record is not acceptable.
 * The socket is non-blocking and the excess goes to a spill file.
 */
typedef struct
{
  int fd;
  ip4_address_t collector;
  u16 port;
  u8 enabled;
  u8 connected;
  u8 pending_connect;
  u8 *outbuf;
  u8 *spill_path;
  f64 next_attempt;
  f64 last_template;
  u8 need_template;
  u32 seq;
  u64 n_records, n_sent, n_spilled, n_replayed, n_dropped, n_connects;
  u64 n_service, n_send_calls;
  int last_errno;
} bpa_log_tcp_t;

extern bpa_log_tcp_t bpa_log_tcp;

clib_error_t *bpa_log_tcp_enable (ip4_address_t *collector, u16 port,
				  int enable);
/* wall is seconds since the UNIX epoch. */
void bpa_log_tcp_record (f64 wall, u8 event, u32 ip_priv, u32 ip_pub,
			 u16 port_lo, u16 port_hi, u32 pool_id);
/* now is vlib_time_now(): monotonic, which is what scheduling a retry wants
 * and what a timestamp on a record does not. */
void bpa_log_tcp_service (f64 now);

/* Drains every thread's queue. Called under the barrier. */
void bpa_log_drain (vlib_main_t *vm);

/* bpa_log_queue_event() is declared in bpa.h, which already sees
 * bpa_per_thread_t; declaring it here would need a circular dependency. */

#endif /* __included_bpa_log_h__ */
