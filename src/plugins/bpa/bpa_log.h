/*
 * bpa_log.h - IPFIX export of block events (RFC 8158).
 *
 * Logs block allocation, not flows. That is what makes the CGNAT auditable
 * without producing terabytes a day. See docs/logging.md.
 */

#ifndef __included_bpa_log_h__
#define __included_bpa_log_h__

/*
 * Standalone, the same way bpa_ftp.h is: test/test_bpa_log_tcp.c drives the
 * TCP exporter's buffer arithmetic, its spill file and its socket handling,
 * none of which reads a packet or the graph. What this header needs from VPP
 * is the integer types, f64, ip4_address_t, clib_error_t and a name for
 * vlib_main_t to point at - the prototypes below take vlib_main_t only by
 * pointer, so an opaque forward declaration is the whole of it. The VPP build
 * defines BPA_STANDALONE nowhere and takes the `#else` branch, which is this
 * header exactly as it was.
 */
#ifdef BPA_STANDALONE
#include "bpa_log_shim.h"
typedef struct vlib_main_t vlib_main_t;
#else
#include <vlib/vlib.h>
#include <vnet/ip/ip.h>
#endif

#include "bpa_identity.h"
#include "bpa_log_ring.h"

/* RFC 8158, natEvent record */
#define BPA_NAT_EVENT_PORTS_EXHAUSTED 12
#define BPA_NAT_EVENT_BLOCK_ALLOC     16
#define BPA_NAT_EVENT_BLOCK_DEALLOC   17

/* Tabela 21 da RFC 8158: 8 + 1 + 4 + 4 + 2 + 2 + 4 */
#define BPA_LOG_RECORD_SIZE 25

/*
 * THE BOOT MARKER. A PROPOSAL - the maintainer may change its shape, and the
 * argument for this one is below so that changing it is an argument and not a
 * preference.
 *
 * WHAT IT IS FOR. A drain on graceful stop (bpa_stop_drain()) closes the
 * allocations a box saw coming. A power loss, an OOM kill and a panic emit
 * nothing at all, and cannot: there is no instant at which the box could have
 * written the record. So after the drain a collector reading an allocation
 * with no release still has two readings of it, and they are opposite answers
 * to a court order:
 *
 *   "the subscriber still holds those ports"   - the box is up, nothing changed
 *   "the box died and nobody knows when"       - the ports were freed at the
 *                                                crash and may already belong
 *                                                to someone else
 *
 * WHAT THE EXISTING RECORDS ALREADY CARRY, and why it is not enough. The
 * message header has an Export Time and a Sequence Number, and RFC 7011 does
 * say a Collector may notice a restart when the sequence number goes
 * backwards. It is a heuristic and it is explicitly ambiguous: over UDP the
 * same thing is produced by reordering, by loss, and by the counter wrapping.
 * A retention log answers a court order, so a heuristic that is also the
 * signature of two ordinary network events is not a basis to say a subscriber
 * stopped holding an address. Nothing else in the record is per-incarnation:
 * observationTimeMilliseconds is the event's own time, and natPoolId names a
 * pool, which survives the restart.
 *
 * THE SHAPE. A second template carrying IE 160,
 * systemInitTimeMilliseconds - "the absolute timestamp of the last
 * (re-)initialization of the IPFIX Device". That is this exact statement,
 * already registered with IANA and already in VPP's own element table, so a
 * collector that knows the IPFIX registry decodes it with no vendor
 * documentation and no private codepoint.
 *
 * WHAT A COLLECTOR DOES WITH IT. Keep the last value seen per exporter and
 * observation domain. While it does not change, an allocation with no release
 * is still in force. When it changes, every allocation still open from before
 * it ended at or before the new value - the box did not observe the end, so
 * the honest answer is a window, not an instant, and the window is
 * [last record of the old incarnation, new systemInitTime]. With the drain
 * that window is empty on a planned stop, because the natEvent 17 is there.
 *
 * WHAT WAS NOT CHOSEN, and why:
 *
 *   - A new natEvent value. IANA's natEvent registry ends at 17; 18 and above
 *     are unassigned, and taking one is squatting on a public registry that
 *     may assign it to something else. The record would also be undecodable
 *     by anyone without our documentation.
 *
 *   - natEvent 17 with a wildcard subscriber and range. A collector that does
 *     not know the convention reads it as the release of a block nobody ever
 *     allocated, which is a new unmatched record - the very shape this is
 *     trying to remove.
 *
 *   - An IPFIX Options Template scoped by observationDomainId, which is the
 *     formally correct home for metadata about an exporter. VPP's
 *     ipfix-export builds Template Sets (set id 2) only, so it would mean a
 *     private copy of vnet_flow_rewrite_generic_callback() to emit set id 3,
 *     for a record every collector would have to special-case anyway. Worth
 *     revisiting if VPP grows options-template support.
 *
 *   - An eighth field on the block record itself, so every record names its
 *     own incarnation. It cannot be lost independently of the record it
 *     qualifies, which is a real advantage over UDP - but it is 8 bytes on a
 *     25-byte record, a third more of a log docs/logging.md sizes at 80 MB a
 *     day, to carry a value that changes once per boot.
 */
#define BPA_LOG_MARKER_RECORD_SIZE 16 /* 8 + 8 */

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
  /*
   * WHO HELD THE ADDRESS, copied HERE rather than looked up when the queue is
   * drained (#21). The lease may have moved in between, and a record naming
   * whoever holds the address by drain time is well-formed, stored, and the
   * wrong answer to a court order - which is the whole of what this field
   * costs BPA_IDENTITY_MAX bytes per queued event to prevent.
   *
   * "unknown" when this box has no identity for the address, because a
   * collector has to tell a box that does not know from one that did not say.
   */
  char identity[BPA_IDENTITY_MAX];
} bpa_log_event_t;

/* The event is a record in a segment another program reads (#550): its size
 * and offsets are that program's contract, checked by bpa_log_seg_check()
 * against event_bytes in the header. A change here is a new
 * BPA_LOG_SEG_VERSION, never a silent one. */
_Static_assert (sizeof (bpa_log_event_t) == 52,
		"bpa_log_event_t is the shared segment's record: 52 bytes");
_Static_assert (offsetof (bpa_log_event_t, identity) == 17,
		"bpa_log_event_t's identity starts at byte 17");

/* A slot of the export ring (#550 step 2): an event for the TCP streams, or
 * the boot marker, with the wall clock VPP read when it drained it - the time
 * the record carries, the same instant the UDP record of the event carries. */
typedef struct
{
  bpa_log_event_t ev;
  u8 kind;		/* BPA_LOG_XEV_RECORD or BPA_LOG_XEV_MARKER */
  u8 pad[3];
  f64 wall;
} bpa_log_xev_t;

_Static_assert (sizeof (bpa_log_xev_t) == BPA_LOG_XEV_BYTES,
		"bpa_log_xev_t is the export ring's slot: 64 bytes");

/* Per-thread queue ceiling: if the process falls behind, the excess is lost
 * in an accounted way, rather than growing without bound. */
#define BPA_LOG_QUEUE_MAX 8192

#ifndef BPA_STANDALONE
/* The rings' segment (#550): its base, its size, and where it is - the
 * shared segment, or the main heap when /dev/shm could not be had (then
 * nothing outside VPP can read it, and `show cgnat logging` says why). */
typedef struct
{
  u8 *base;
  u64 bytes;
  u8 shared;		/* 1: /dev/shm/prisma-bpa-log; 0: the main heap */
  int err;		/* errno of the step that failed, when not shared */
  const char *failed;	/* that step */
} bpa_log_seg_t;

extern bpa_log_seg_t bpa_log_seg;
clib_error_t *bpa_log_seg_create (u32 n_rings);
/* The log container carries the TCP streams (#550 step 2). */
int bpa_log_logger_owns (void);
/* The container carries the UDP export too (#550 step 3). */
int bpa_log_udp_by_logger (void);
/* Sends the current thread's partial UDP buffer. */
void bpa_log_flush (vlib_main_t *vm);
/* Called from the log process every pass: hands the TCP streams to a log
 * container that has come up, takes them back from one that has gone. */
void bpa_log_handover (void);
#endif

/* A TCP stream's spill file's ceiling on disk: past it a record is dropped
 * and counted (#547). A collector away for days must not fill the box's
 * disk. */
#ifndef BPA_TCP_SPILL_MAX
#define BPA_TCP_SPILL_MAX (256ULL << 20)
#endif

typedef struct
{
  u32 template_id;
  u32 stream_index;
  u32 flow_report_index;
  /* The boot marker's own template, on the same stream: one stream may carry
   * several templates, and sharing it keeps one sequence number over both so
   * a collector sees one ordered flow of records rather than two. */
  u32 marker_template_id;
  u32 marker_report_index;
  u64 n_markers;
  f64 next_marker;
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
		   u16 port_lo, u16 port_hi, u32 pool_id,
		   const char *identity);

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
  /*
   * How much of the message at the FRONT of outbuf the socket has already
   * taken. A non-blocking send() stops in the middle of a message whenever the
   * window is nearly shut, and outbuf has to keep starting at a message
   * boundary: the template and the spill replay are both inserted at the
   * front, and the spill file is a stream a collector frames the same way.
   */
  u32 head_sent;
  u8 *spill_path;
  f64 next_attempt;
  f64 last_template;
  u8 need_template;
  u32 seq;
  u64 n_records, n_sent, n_spilled, n_replayed, n_dropped, n_connects;
  u64 n_service, n_send_calls;
  /* #547: bytes skipped to find a message boundary (a spill or buffer whose
   * front did not frame), spills made because the collector stopped taking
   * bytes, how many records the spill's ceiling refused, and when the
   * collector last took some. */
  u64 n_resynced, n_stalls, n_capped;
  f64 last_progress;
  /* The boot marker went into this stream: once per enable, which is once
   * per boot - repeated every 30 s into a reliable stream it was most of a
   * collector's 55 million rows on the #475 bench (#547). */
  u8 marker_written;
  /*
   * THE BACKLOG (#547). While the spill file holds anything not yet sent, it
   * is read a chunk at a time from spill_off - never whole: a 573 MB spill
   * read whole aborted VPP for want of heap on the #475 bench - and every new
   * record goes to the END of the file, which is what keeps it behind the
   * older ones. The front of outbuf is then front_extra bytes of template,
   * replay_len bytes of the file, and after them only records written since
   * the last service tick, on their way to the file.
   */
  u8 backlog;
  u64 spill_off;
  u32 replay_len, front_extra;
  int last_errno;
  /*
   * WHICH WIRE THIS STREAM SPEAKS (#20). The reliability machinery below -
   * the non-blocking socket, the spill file, the replay in order - is what
   * makes this transport fit for a compliance log, and it has nothing to do
   * with the format of what it carries. So it has ONE implementation and the
   * format is a field: a second copy of reconnection and spill handling would
   * be a second place for the log to lose a record.
   */
  u8 wire;                      /* BPA_WIRE_IPFIX or BPA_WIRE_SYSLOG */
  /*
   * The syslog half, unread when the wire is IPFIX. The enterprise number has
   * no default and is the operator's: RFC 5424 allows a bare SD-ID only to
   * names IANA has registered, and inventing one would put a name in every
   * operator's log that means something else to whoever registers it later.
   */
  u32 pen;
  u32 pri;
  u8 *hostname;
} bpa_log_stream_t;

/* The two wires a stream can speak. */
#define BPA_WIRE_IPFIX	0
#define BPA_WIRE_SYSLOG 1

extern bpa_log_stream_t bpa_log_tcp;
extern bpa_log_stream_t bpa_log_syslog;
/* Both streams closed (fd -1) before anything can disable them (#551). */
void bpa_log_streams_init (void);

/*
 * One stream, whichever wire it speaks. The five below take it explicitly;
 * the bpa_log_tcp_* names beside them are the IPFIX stream's, kept so that
 * every caller that had one goes on reading the way it did.
 */
void bpa_log_stream_record (bpa_log_stream_t *s, f64 wall, u8 event,
			    u32 ip_priv, u32 ip_pub, u16 port_lo, u16 port_hi,
			    u32 pool_id, const char *identity);
void bpa_log_stream_marker (bpa_log_stream_t *s, f64 wall, f64 init);
void bpa_log_stream_service (bpa_log_stream_t *s, f64 now);
int bpa_log_stream_pending (bpa_log_stream_t *s);
u32 bpa_log_stream_flush_exit (bpa_log_stream_t *s);
/* Hands the stream to another process: memory to the spill, socket closed,
 * backlog forgotten (#550). */
void bpa_log_stream_release (bpa_log_stream_t *s);
#ifndef BPA_STANDALONE
/* Publishes a stream's configuration for the log container. */
void bpa_log_seg_publish (bpa_log_stream_t *s);
/* `show cgnat logging`: the log container's counters into s, when it owns. */
u64 bpa_log_stream_shown (bpa_log_stream_t *s);
#endif
clib_error_t *bpa_log_stream_enable (bpa_log_stream_t *s,
				     ip4_address_t *collector, u16 port,
				     int enable);

/* The syslog line itself, in bpa_log_syslog.c: no socket, no queue, no clock
 * of its own, so it is compiled and driven by plain cc. */
const char *bpa_syslog_msgid (u8 event);
u32 bpa_syslog_escape (char *out, u32 cap, const char *in);
u32 bpa_syslog_timestamp (char *out, u32 cap, f64 wall);
u32 bpa_syslog_marker_line (char *out, u32 cap, f64 wall, f64 init,
			    const char *hostname, u32 pen, u32 pri);
u32 bpa_syslog_line (char *out, u32 cap, f64 wall, u8 event, u32 ip_priv,
		     u32 ip_pub, u16 port_lo, u16 port_hi, u32 pool_id,
		     const char *hostname, u32 pen, u32 pri,
		     const char *identity);

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

/*
 * The exporter's last act before the process leaves: whatever the socket
 * refused goes to the spill file, to be replayed on the next connection.
 * Returns the number of bytes saved. See the comment on the definition for
 * why it does not wait for the socket instead.
 */
u32 bpa_log_tcp_flush_exit (void);

/*
 * The boot marker, on every enabled transport. See the long comment above
 * BPA_LOG_MARKER_RECORD_SIZE for what it is and what a collector does with it.
 *
 * Emitted when a transport comes up, BEFORE bpa_log_pins_replay() announces
 * anything - a collector has to know which incarnation the records that follow
 * belong to before it reads them - and again on a timer, because over UDP a
 * marker that went out once and was lost leaves the collector reading the new
 * incarnation's records as the old one's.
 */
void bpa_log_marker_emit (vlib_main_t *vm);

/* How often the marker is repeated, in seconds. */
#define BPA_LOG_MARKER_INTERVAL 30.0

/* wall is seconds since the UNIX epoch, for the record's own timestamp;
 * init is the same clock, read once when this incarnation started. */
void bpa_log_tcp_marker (f64 wall, f64 init);

/* bpa_log_queue_event() is declared in bpa.h, which already sees
 * bpa_per_thread_t; declaring it here would need a circular dependency. */

#endif /* __included_bpa_log_h__ */
