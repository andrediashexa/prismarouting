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
  /*
   * GRE, and only the enhanced GRE of RFC 2637 - the one PPTP carries its
   * payload in. Its Call ID stands in for a port everywhere below.
   *
   * Plain GRE (RFC 2784) has no per-flow identifier at all, so several
   * subscribers behind one public address cannot be told apart and it is not
   * translated. That is a property of the protocol, not a gap here.
   */
  BPA_PROTO_GRE,
  BPA_N_PROTO,
} bpa_proto_t;

/*
 * How far a PPTP call has got. Only meaningful for BPA_PROTO_GRE.
 *
 * A call is created by the Outgoing-Call-Request going out, when only the
 * subscriber's own Call ID is known. The server's Call ID arrives in the
 * reply, and until it does there is no way to key the outbound direction -
 * so the two directions become usable at different moments, and the state
 * says which.
 */
typedef enum
{
  BPA_GRE_CALLING = 0, /* request sent, no reply yet: inbound only */
  BPA_GRE_ESTABLISHED, /* the server's Call ID is known: both directions */
} bpa_gre_state_t;

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
  /*
   * The remote address this session last spoke to, in host order.
   *
   * Unused under endpoint-independent filtering, which is the default and
   * which does not care who replies. Under address-dependent filtering it is
   * the thing being filtered on.
   *
   * ONE address, not a set. A source port reused toward two remotes will only
   * accept replies from the more recent one - an approximation, stated here
   * because a reader deserves to know where it stops. Tracking the set would
   * mean per-remote state on a datapath whose whole point is not having any,
   * and the drops are counted so an operator can see if it ever bites.
   */
  u32 remote;
  /*
   * BPA_PROTO_GRE only: the Call ID the PPTP server assigned, which is what
   * the subscriber's outbound GRE packets carry. Valid once gre_state is
   * BPA_GRE_ESTABLISHED; the subscriber's own Call ID lives in port_priv and
   * the one this box handed out lives in port_pub, exactly as ports do.
   */
  u16 peer_call_id;
  u8 gre_state;
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

/*
 * A port waiting out its hold-down.
 *
 * A per-port release timestamp would be the obvious design and costs 32x the
 * memory: the bitmap is 64 bytes for a 512-port block, and timestamps would
 * be 2 KB - 400 MB across 200k subscribers, to solve a problem that lasts
 * seconds. This queue costs memory proportional to session CHURN instead,
 * which is what the hold-down window is actually about.
 */
typedef struct
{
  u32 sub_idx;
  u16 port_off;
  /* Which of the subscriber's blocks the port belongs to. A subscriber can
   * hold more than one, and freeing an offset into the wrong bitmap would
   * hand out a port that is still in use. */
  u8 blk_idx;
  f64 until;
} bpa_quarantine_t;

/*
 * Bounded. If churn outruns the drain, ports are released early and counted
 * rather than withheld: refusing to release ports exhausts the subscriber's
 * block, which is a worse failure than the delayed-segment collision the
 * hold-down exists to avoid.
 */
#define BPA_QUARANTINE_MAX 65536

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
  bpa_quarantine_t *quarantine;   /* ports waiting out their hold-down */
  u32 quarantine_head;            /* drained from the front, FIFO by time */
  u64 n_quarantine_early;         /* released before their time, counted */
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

/*
 * A GRE mapping needs two keys that are not each other's mirror, so it gets
 * its own key builder.
 *
 * Inbound is ordinary: the packet carries the Call ID this box handed out, so
 * (public address, that Call ID) finds it, and bpa_key() above already builds
 * that - the Call ID comes out of the subscriber's port block like a port, so
 * the arithmetic pre-check and the worker handoff work on it unchanged.
 *
 * Outbound is not: the packet carries the Call ID the SERVER assigned, which
 * this box did not choose and cannot make unique. Two servers picking the
 * same value for the same subscriber is entirely possible - a subscriber
 * with two PPTP tunnels up is an ordinary home worker - so the remote address
 * goes into the second key word to separate them.
 *
 * `role` separates the two entries the ALG keeps in the same table: the data
 * key, found by the server's Call ID, and the lookup key the ALG itself uses,
 * found by the subscriber's own Call ID. Without it a subscriber whose own
 * Call ID happened to equal the server's would overwrite one with the other.
 *
 * The second key word was reserved for a VRF id. GRE spends it. When VRF
 * lands (issue #26) this key needs a wider hash or a packed encoding, and
 * that is written down here rather than discovered then.
 */
#define BPA_GRE_KEY_DATA 0 /* keyed by the server's Call ID  */
#define BPA_GRE_KEY_ALG	 1 /* keyed by the subscriber's own */

static_always_inline void
bpa_key_gre (clib_bihash_kv_16_8_t *kv, u32 addr, u16 call_id, u32 remote,
	     u8 role)
{
  kv->key[0] = (u64) addr | ((u64) call_id << 32)
	       | ((u64) BPA_PROTO_GRE << 48) | ((u64) role << 56);
  kv->key[1] = remote;
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
    case BPA_PROTO_GRE:
      return t->gre;
    default:
      break;
    }

  switch (s->tcp_state)
    {
    case BPA_TCP_CLOSED:
      return 0; /* RST: no reason to hold the port */
    case BPA_TCP_ESTAB:
      return t->tcp_estab;
    case BPA_TCP_INIT:
      /* SYN sent, never established. A scan produces one of these per probe
       * and they must not be held for the transitory timeout. */
      return t->tcp_syn;
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
/*
 * A PPTP call, created by the ALG when the subscriber asks for one. Takes the
 * subscriber's own Call ID and returns a session whose port_pub is the Call ID
 * this box will present to the internet - allocated out of the subscriber's
 * port block, so it is logged, reversible and worker-local like every other
 * port. The subscriber MUST already hold a block; the PPTP control connection
 * that carried the request is the proof that it does.
 */
bpa_session_t *bpa_session_create_gre (bpa_per_thread_t *pt,
				       const bpa_pool_t *p, u32 pool_index,
				       u32 sub_idx, u32 ip_pub, u16 call_id,
				       u32 remote, f64 now);
/* The server answered: the outbound direction can be keyed now. */
void bpa_session_gre_established (bpa_per_thread_t *pt, bpa_session_t *s,
				  u16 peer_call_id);
void bpa_session_free (bpa_per_thread_t *pt, bpa_session_t *s, f64 now);
u32 bpa_expire_walk (bpa_per_thread_t *pt, const bpa_timeouts_t *t,
		     f64 now, u32 budget);

#endif /* __included_bpa_session_h__ */
