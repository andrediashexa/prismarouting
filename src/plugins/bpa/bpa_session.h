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

#include "bpa_pool.h"
#include "bpa_ports.h"
#include "bpa_dyn.h"

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
  u8 proto;	 /* bpa_proto_t */
  u8 tcp_estab;
  u32 sub_idx;
  u32 pool_index;
  f64 last_heard;
} bpa_session_t;

/* Per-subscriber port bitmap plus its backing storage. */
typedef struct
{
  bpa_portmap_t map;
  u32 n_sessions; /* when this hits zero the bitmap can be reclaimed */
} bpa_sub_t;

typedef struct
{
  bpa_session_t *sessions;	  /* pool */
  clib_bihash_16_8_t in2out;	  /* (priv ip, priv port, proto) -> session */
  clib_bihash_16_8_t out2in;	  /* (pub ip, pub port, proto)  -> session */
  bpa_sub_t *subs;		  /* pool of per-subscriber port bitmaps */
  bpa_dyn_t *dyn;	  /* dynamic block state, indexed by pool index */
  uword *sub_by_idx;		  /* sub_idx -> index into subs */
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
      return s->tcp_estab ? t->tcp_estab : t->tcp_trans;
    }
}

clib_error_t *bpa_per_thread_init (bpa_per_thread_t *pt, u32 thread_index);
bpa_session_t *bpa_session_create (bpa_per_thread_t *pt, const bpa_pool_t *p,
				   u32 pool_index, const bpa_block_t *b,
				   u16 port_priv, u8 proto, f64 now);
void bpa_session_free (bpa_per_thread_t *pt, bpa_session_t *s, f64 now);
u32 bpa_expire_walk (bpa_per_thread_t *pt, const bpa_timeouts_t *t,
		     f64 now, u32 budget);

#endif /* __included_bpa_session_h__ */
