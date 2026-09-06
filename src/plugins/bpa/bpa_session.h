/*
 * bpa_session.h - session table and per-worker state.
 *
 * Why sessions exist even in deterministic mode: bpa_reverse() recovers the
 * subscriber's ADDRESS from (public ip, port) by arithmetic, but not the
 * original source PORT. The return path needs that, so a session maps
 * public port -> private port. What the arithmetic buys us is that the
 * lookup is always worker-local and never needs allocation logging.
 *
 * All session state is per-worker. Both directions of a subscriber are
 * steered to the same worker by bpa_owner_thread(), so nothing here is
 * shared and nothing needs a lock.
 */

#ifndef __included_bpa_session_h__
#define __included_bpa_session_h__

#include <vlib/vlib.h>
#include <vppinfra/bihash_16_8.h>
#include <vnet/tcp/tcp_packet.h>

#include "bpa_pool.h"
#include "bpa_ports.h"
#include "bpa_dyn.h"
#include "bpa_log.h"

/*
 * Default timeouts in seconds. The floors come from RFC 4787 (UDP, 2 min)
 * and RFC 5382 (TCP established 2h4m, transitory 4 min). Providers tune
 * these: shorter timeouts recycle ports faster and fit more subscribers per
 * public IP, at the cost of breaking long idle connections.
 */
#define BPA_TIMEOUT_UDP		300
#define BPA_TIMEOUT_ICMP	60
#define BPA_TIMEOUT_TCP_TRANS	240
#define BPA_TIMEOUT_TCP_ESTAB	7440

typedef struct
{
  u32 udp;
  u32 icmp;
  u32 tcp_trans;
  u32 tcp_estab;
} bpa_timeouts_t;

/*
 * Minimal TCP state, just enough not to hold a port for nothing.
 *
 * Marking "established" on any ACK and never handling RST or FIN makes a
 * closed connection hold its port for the established timeout - two hours.
 * In a 512-port block that exhausts the subscriber quickly, and the complaint
 * arrives as "the internet stopped" with nothing obvious in the dataplane.
 */
typedef enum
{
  BPA_TCP_INIT = 0, /* handshake in progress */
  BPA_TCP_ESTAB,    /* ACK seen after the handshake */
  BPA_TCP_CLOSING,  /* FIN seen in either direction */
  BPA_TCP_CLOSED,   /* RST: expires on the next sweep */
} bpa_tcp_state_t;

typedef enum
{
  BPA_PROTO_TCP = 0,
  BPA_PROTO_UDP,
  BPA_PROTO_ICMP,
  BPA_N_PROTO,
} bpa_proto_t;

typedef struct
{
  u32 ip_priv;	 /* host order */
  u32 ip_pub;	 /* host order */
  u16 port_priv; /* host order; ICMP echo id for BPA_PROTO_ICMP */
  u16 port_pub;
  u16 port_off;	 /* offset within the block, needed to free the port */
  u8 blk_idx;	 /* which of the subscriber's blocks that offset is in */
  u8 proto;	 /* bpa_proto_t */
  u8 tcp_state; /* bpa_tcp_state_t */
  u32 sub_idx;
  u32 pool_index;
  f64 last_heard;
} bpa_session_t;

/*
 * One port block held by a subscriber, with its bitmap.
 *
 * A subscriber usually holds exactly one. It gets a second only in dynamic
 * mode, when the first fills and the per-subscriber cap allows it - a
 * deterministic pool has no spare block to give, because the formula already
 * assigns every block to somebody (see issue #39).
 */
typedef struct
{
  bpa_block_t blk;   /* public address and port range */
  bpa_portmap_t map;
} bpa_subblock_t;

/*
 * Per-subscriber port state.
 *
 * blocks[0] is the block the subscriber started with; anything after it is an
 * extension. The vector is the common case of length one, so the extra
 * indirection costs an allocation the subscriber already needed.
 */
typedef struct
{
  bpa_subblock_t *blocks;
  u32 n_sessions; /* when this hits zero the bitmaps can be reclaimed */
} bpa_sub_t;

typedef struct
{
  bpa_session_t *sessions;	  /* pool */
  clib_bihash_16_8_t in2out;	  /* (priv ip, priv port, proto) -> session */
  clib_bihash_16_8_t out2in;	  /* (pub ip, pub port, proto)  -> session */
  bpa_sub_t *subs;		  /* pool of per-subscriber port bitmaps */
  bpa_dyn_t *dyn;	  /* dynamic block state, indexed by pool index */
  uword *sub_by_idx;		  /* sub_idx -> index into subs */
  bpa_log_event_t *log_queue; /* queued by the dataplane, drained by the process */
  u64 log_dropped;
  u32 n_expired;
  u32 expire_cursor;	  /* budgeted walk resumes here */
  u8 initialised;
} bpa_per_thread_t;

/*
 * Session key. bihash_16_8 keys are two u64 words; we use one and leave the
 * second for a future VRF id, which is what a multi-tenant deployment would
 * need. Keeping the slot reserved now costs nothing.
 */
static_always_inline void
bpa_key (clib_bihash_kv_16_8_t *kv, u32 addr, u16 port, u8 proto)
{
  kv->key[0] = (u64) addr | ((u64) port << 32) | ((u64) proto << 48);
  kv->key[1] = 0;
}

static_always_inline u32
bpa_session_timeout (const bpa_session_t *s, const bpa_timeouts_t *t)
{
  switch (s->proto)
    {
    case BPA_PROTO_UDP:
      return t->udp;
    case BPA_PROTO_ICMP:
      return t->icmp;
    default:
      break;
    }

  switch (s->tcp_state)
    {
    case BPA_TCP_CLOSED:
      return 0; /* RST: no reason to hold the port */
    case BPA_TCP_ESTAB:
      return t->tcp_estab;
    default:
      return t->tcp_trans;
    }
}

/*
 * Tracks the state from the flags. Valid in both directions: an RST from
 * either side ends it, and a FIN from either side already takes the session
 * off the long timeout.
 */
static_always_inline void
bpa_tcp_track (bpa_session_t *s, u8 flags)
{
  if (PREDICT_FALSE (flags & TCP_FLAG_RST))
    {
      s->tcp_state = BPA_TCP_CLOSED;
      return;
    }
  if (PREDICT_FALSE (flags & TCP_FLAG_FIN))
    {
      if (s->tcp_state != BPA_TCP_CLOSED)
	s->tcp_state = BPA_TCP_CLOSING;
      return;
    }
  if ((flags & TCP_FLAG_ACK) && s->tcp_state == BPA_TCP_INIT)
    s->tcp_state = BPA_TCP_ESTAB;
}

/* Modest defaults: the size is a virtual reservation, and the OOM killer
 * counts virtual. See the comment in bpa_session.c. */
#define BPA_HASH_BUCKETS_DEFAULT (16 * 1024)
#define BPA_HASH_MEMORY_DEFAULT	 (16 << 20)

clib_error_t *bpa_per_thread_init (bpa_per_thread_t *pt, u32 thread_index);
bpa_session_t *bpa_session_create (bpa_per_thread_t *pt, const bpa_pool_t *p,
				   u32 pool_index, const bpa_block_t *b,
				   u16 port_priv, u8 proto, f64 now);

/* Extended blocks: how many a subscriber holds, and attaching one more. */
u32 bpa_sub_n_blocks (bpa_per_thread_t *pt, u32 sub_idx);
void bpa_sub_extend (bpa_per_thread_t *pt, const bpa_pool_t *p, u32 sub_idx,
		     const bpa_block_t *b);
void bpa_session_free (bpa_per_thread_t *pt, bpa_session_t *s, f64 now);
u32 bpa_expire_walk (bpa_per_thread_t *pt, const bpa_timeouts_t *t,
		     f64 now, u32 budget);

#endif /* __included_bpa_session_h__ */
