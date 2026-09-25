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
#include "bpa_rate.h"
#include "bpa_ports.h"
#include "bpa_dyn.h"
#include "bpa_log.h"
#include "bpa_block_ident.h"

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

/*
 * bpa_proto_t moved to bpa_pool.h, which every build of this plugin includes
 * and a standalone harness can too: bpa_static_check() compares a forward's
 * protocol, and a second copy of these values written where the harness
 * could reach them would be a copy that can disagree with this one.
 */

#include "bpa_seq.h"

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
   * The VRF this subscriber's traffic arrived in, so the reply can be sent
   * back into it and the private-side key can be rebuilt when the session is
   * freed. Without it, freeing a session would delete the wrong key - or none
   * at all - and the table would keep an entry pointing at reclaimed memory.
   */
  u32 fib_index;
  /*
   * The VRF translated traffic leaves by, when the pool names one. ~0 leaves
   * the packet where it was.
   *
   * On the session rather than read from the pool, because the alternative is
   * a pool lookup on every translated packet to answer a question that cannot
   * change for the life of the session.
   */
  u32 out_fib_index;
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
  /*
   * The two checksum corrections the outbound rewrite runs on, worked out
   * once by bpa_csum_deltas() when the session is created.
   *
   * They can be worked out once because they cannot change: the subscriber
   * always sends from (ip_priv, port_priv) and this session always rewrites
   * that to (ip_pub, port_pub). Recomputing them per packet was 201 of the
   * 395 clocks bpa-in2out cost (#63).
   *
   * Set at the END of every path that creates a session, after ip_priv has
   * been resolved - a correction built from a zero ip_priv is wrong on every
   * packet of that session and there is no later packet that would repair it.
   */
  u16 csum_d_addr; /* the IPv4 header checksum: the address change only */
  u16 csum_d_l4;   /* TCP and UDP: address and port. ICMP: the echo id. */
  f64 last_heard;

  /*
   * Payload rewrites on this connection, one correction per direction. Both
   * stay zero on every session no ALG has touched, which is nearly all of
   * them, and the datapath tests them with a single OR.
   *
   * THE mechanism, for every ALG that changes a payload length. There used to
   * be two of these - this one, and a narrower set of fields belonging to the
   * FTP ALG - because the two arrived on separate branches at the same time
   * and folding them together during a merge, with no test that would catch a
   * mistake, is how a merge breaks two working features at once (#48). A
   * connection is either FTP or RTSP, so the duplication cost bytes on every
   * session rather than correctness, and it cost the next ALG a choice between
   * two mechanisms of which only one could be right.
   *
   * Twenty-four bytes on every session, FTP or not, which is what a side table
   * keyed on the session index would have avoided. It is not worth it: the
   * table would need its own lifecycle hanging off bpa_session_free(), and
   * this project has already learned that a second thing to keep in step is a
   * second thing to get wrong.
   */
  bpa_seq_adj_t adj[BPA_N_DIR];

  /*
   * An ALG expectation: a media mapping opened because a control message
   * announced it, before anything used it. It goes back on the normal timers
   * the moment a packet arrives; until then it expires on a much shorter one,
   * so ports announced and never used do not sit out the full UDP timeout.
   */
  u8 is_expect;

  /*
   * This mapping came from a configured port forward, not from traffic.
   *
   * It expires like any other session - there is nothing to gain from holding
   * a table entry for a port nobody is using - and the difference is what
   * happens to the PORT when it does: an ordinary session hands it back to the
   * allocator, and a forwarded one must not, because the port belongs to the
   * configuration and giving it to the next flow would break the forward
   * between two connections.
   */
  u8 is_static;
} bpa_session_t;

/*
 * One port block held by a subscriber, with its bitmap.
 *
 * A subscriber usually holds exactly one. It gets a second when the first
 * fills, the per-subscriber cap allows it, and the pool has a block to give:
 * off the free list in dynamic mode, out of the reserve in deterministic mode.
 * A deterministic pool with NO reserve has none to give, because the formula
 * already assigns every mapped block to somebody (issue #39).
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
  /*
   * Whether this subscriber has already been reported as near its limit.
   *
   * One bit, so the alarm fires once per crossing rather than once per port.
   * A log line per allocation past the threshold is a log nobody can keep and
   * an alarm nobody reads - and the subscriber that trips it is, by
   * definition, the one allocating fastest.
   */
  u8 alarmed;

  /*
   * Token bucket for the creation rate, in sessions per second.
   *
   * A bucket rather than a one-second window: a window lets a scanner spend
   * two windows' worth of budget across the boundary, which is exactly the
   * burst the limit exists to bound. The bucket holds one second's worth, so
   * the allowed burst is a stated number and not an artefact of when the
   * clock ticked.
   *
   * Two words on a structure there is one of per subscriber per worker, on a
   * path that is already allocating a port.
   */
  f64 rate_last;
  f32 tokens;

  /*
   * Refusals, kept apart because they need different answers from the
   * operator. A full block means the customer needs more ports; a cap hit
   * means the cap is doing its job, or is too low for that customer; a rate
   * refusal usually means an infection.
   */
  u32 n_refused_sessions;
  u32 n_refused_rate;

  /*
   * Times this subscriber asked for a port and was refused.
   *
   * Counted here rather than per pool because the question an operator
   * arrives with is about one customer: "is this the box, or is it them?".
   * A pool-wide counter cannot answer it. Written only on the failure path,
   * so the fast path pays nothing.
   */
  u32 n_refused;
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
  u32 pool_index;
  u32 sub_idx;
  u16 port_off;
  /* Which of the subscriber's blocks the port belongs to. A subscriber can
   * hold more than one, and freeing an offset into the wrong bitmap would
   * hand out a port that is still in use. */
  u8 blk_idx;
  f64 until;
} bpa_quarantine_t;

/*
 * Key for the per-subscriber record.
 *
 * BOTH halves, and the second one is not decoration. sub_idx is an index
 * within ONE pool's inside set, so it is unique per pool and not globally:
 * keyed on sub_idx alone, subscriber 5 of pool A and subscriber 5 of pool B
 * hashed to the same entry and shared one set of port bitmaps and one list of
 * blocks. A subscriber of pool B was then translated onto pool A's public
 * address and logged against pool A's block - a wrong answer to a court
 * order, produced with full confidence (issue #57).
 */
static_always_inline uword
bpa_sub_key (u32 pool_index, u32 sub_idx)
{
  return ((uword) pool_index << 32) | (uword) sub_idx;
}

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
  /*
   * The identity each HELD block's allocation record named, so its release
   * record can say the same thing (#397). Per thread because a block belongs
   * to one worker's slice, so the worker that allocates it is the worker that
   * releases it - and the two release paths that run in process context read
   * this under the worker barrier, the way they already read owner[].
   */
  bpa_block_ident_table_t block_ident;
  bpa_quarantine_t *quarantine;   /* ports waiting out their hold-down */
  u32 quarantine_head;            /* drained from the front, FIFO by time */
  u64 n_quarantine_early;         /* released before their time, counted */
  u32 n_expired;
  u32 expire_cursor;	  /* budgeted walk resumes here */
  /*
   * Packets this worker let past untranslated, indexed by pool index.
   *
   * Per thread and not on the pool, because the pool is shared across workers
   * and a counter written by all of them on the datapath is a cache line every
   * worker fights over. Summed when somebody asks. Sized when a pool is added,
   * so the datapath never grows a vector.
   */
  u64 *n_bypassed;
  /* Sessions refused because the heap could not take the session pool's
   * next growth (#460). A refusal instead of the abort it used to be. */
  u64 n_no_memory;
  u8 initialised;
} bpa_per_thread_t;

/*
 * The two session keys, and they are not the same key.
 *
 * They were the same function while the second word was unused, which made it
 * easy to forget that the private side and the public side identify different
 * things. They are separate now because only one of them needs the VRF:
 *
 *   IN  (private address, private port, protocol, VRF)
 *   OUT (public address, public port, protocol)
 *
 * The private side needs the VRF because two tenants may use the same private
 * address, and without it one subscriber is translated onto the other's
 * mapping - silently, which is the failure this product exists to prevent.
 *
 * The public side does not, because a public address belongs to exactly one
 * pool: two pools sharing one would make the return path ambiguous, and that
 * is refused when the pool is created.
 *
 * The VRF is the packet's FIB index, which is available before the pool is
 * known - and it has to be, because the session lookup happens first. That is
 * the whole reason this qualifier is the packet's and not the pool's.
 */
static_always_inline void
bpa_key_in (clib_bihash_kv_16_8_t *kv, u32 addr, u16 port, u8 proto, u32 fib)
{
  kv->key[0] = (u64) addr | ((u64) port << 32) | ((u64) proto << 48);
  kv->key[1] = fib;
}

static_always_inline void
bpa_key_out (clib_bihash_kv_16_8_t *kv, u32 addr, u16 port, u8 proto)
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
	     u8 role, u32 fib)
{
  kv->key[0] = (u64) addr | ((u64) call_id << 32)
	       | ((u64) BPA_PROTO_GRE << 48) | ((u64) role << 56);
  /* Packed, not chosen between: the remote address is what separates two
   * servers that picked the same Call ID, and the VRF is what separates two
   * tenants that use the same private address. Both are needed and both fit,
   * the remote in the low half and the FIB index in the high one. */
  kv->key[1] = (u64) remote | ((u64) fib << 32);
}

static_always_inline u32
bpa_session_timeout (const bpa_session_t *s, const bpa_timeouts_t *t)
{
  /* An announced media port nothing has used yet. Checked first because it
   * overrides the protocol's own lifetime for exactly as long as the mapping
   * is a prediction rather than a flow. */
  if (PREDICT_FALSE (s->is_expect))
    return t->alg_expect;

  switch (s->proto)
    {
    case BPA_PROTO_UDP:
      return t->udp;
    case BPA_PROTO_ICMP:
      return t->icmp;
    case BPA_PROTO_GRE:
    /*
     * ESP shares the tunnel timeout with GRE deliberately, rather than get a
     * knob of its own. Both are portless tunnels and both are judged live by
     * the same signal - PPP sends LCP echoes, IPsec sends DPD, each on the
     * order of tens of seconds - so half an hour of silence means the same
     * thing for both. A second timeout nobody would ever set differently is a
     * second thing to keep in step, and this plugin has already learned what
     * that costs.
     */
    case BPA_PROTO_ESP:
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

/* The shared ESP binding table. One entry per public address and peer
 * gateway, not per flow, so it is an order of magnitude smaller. */
#define BPA_SHARED_BUCKETS_DEFAULT (1024)
#define BPA_ESP_MEMORY_DEFAULT	(2 << 20)

clib_error_t *bpa_per_thread_init (bpa_per_thread_t *pt, u32 thread_index);
/*
 * Why a session could not be created. The caller needs to know: a full block
 * may be answerable with another block, and a cap never is - trying an
 * extension after a cap refusal would hand out a block to a subscriber the
 * operator has just told the box to bound.
 */
typedef enum
{
  BPA_CREATE_OK = 0,
  BPA_CREATE_NO_PORT,	    /* every block this subscriber holds is full */
  BPA_CREATE_OVER_SESSIONS, /* at its concurrent cap */
  BPA_CREATE_OVER_RATE,	    /* opening them faster than allowed */
  /*
   * ESP only: another subscriber on this same public address already has a
   * tunnel to this peer, and the two are indistinguishable on the wire. The
   * binding is not taken from its owner and not shared; see bpa_esp.h.
   */
  BPA_CREATE_ESP_PEER_TAKEN,
  /* The dataplane's heap cannot take the session pool's next growth (#460). */
  BPA_CREATE_NO_MEMORY,
} bpa_create_why_t;

/*
 * WHETHER THE HEAP CAN TAKE THE NEXT GROWTH OF THIS WORKER'S SESSION POOL
 * (#460).
 *
 * The session pool, both session hashes (BIHASH_USE_HEAP is 1 for 16_8, so
 * the bihash ignores its memory size and lives in the heap) and the
 * subscriber records are all in VPP's main heap - and a heap allocation that
 * fails ABORTS the process. Measured with a 512 MB heap and two million
 * sessions offered: VPP was gone within ten seconds, the backtrace ending in
 * _vec_realloc_internal from bpa_session_bind, and every subscriber on the
 * box lost service at once.
 *
 * ASKED ONLY WHEN THE POOL IS ABOUT TO GROW, which is the moment that
 * aborted: pool_get takes a free element when there is one and grows the
 * vector by half when there is not (vec.c: req_size += n_data_bytes / 2),
 * and a realloc can need the old block and the new one together. Reading the
 * heap's free space is not an option on this path: clib_mem_get_heap_usage
 * walks every chunk, measured at 86 ms with two million sessions, against 3
 * ms for a trivial CLI command.
 *
 * So it ASKS FOR THE MEMORY, without aborting, and gives it straight back:
 * the pool's next size, plus the hash growth of the sessions that fit before
 * the growth after it (BPA_HEAP_HASH_BYTES each, measured at about 300 bytes
 * a session all told at two million), plus a fixed margin for everything
 * else that allocates meanwhile. Conservative near the ceiling by design: a
 * session refused and counted is an answer; an abort is an outage.
 */
#define BPA_HEAP_HASH_BYTES 256
#define BPA_HEAP_MARGIN (32ULL << 20)

static_always_inline int
bpa_session_room (bpa_per_thread_t *pt)
{
  uword len, bytes, probe;
  void *m;

  if (PREDICT_TRUE (pool_free_elts (pt->sessions) > 0))
    return 1;
  len = vec_len (pt->sessions);
  if (PREDICT_TRUE (len < vec_max_len (pt->sessions)))
    return 1;
#ifdef BPA_HEAP_GUARD_OFF
  /* lab/heap-full.sh's off mode builds the plugin this way, to show the
   * abort this gate exists for. No box is built with it. */
  return 1;
#endif
  bytes = len * sizeof (bpa_session_t);
  probe = bytes + bytes / 2 + (len / 2) * BPA_HEAP_HASH_BYTES + BPA_HEAP_MARGIN;
  m = clib_mem_alloc_aligned_or_null (probe, CLIB_CACHE_LINE_BYTES);
  if (PREDICT_FALSE (m == 0))
    {
      pt->n_no_memory++;
      return 0;
    }
  clib_mem_free (m);
  return 1;
}

/*
 * Who is asking for the mapping.
 *
 * TRAFFIC is a packet from the subscriber, and the per-subscriber bounds apply
 * to it. ALG is a mapping opened on behalf of a control connection that has
 * already passed those bounds - an FTP data connection, an announced media
 * port - and it is NOT refused by them: an announced port that does not work
 * is the failure an ALG exists to prevent, and the subscriber cannot open
 * thousands of sessions this way without first opening thousands of control
 * connections, which the bounds do see.
 *
 * It still counts toward the concurrent total, because it occupies a port like
 * anything else.
 */
typedef enum
{
  BPA_CREATE_TRAFFIC = 0,
  BPA_CREATE_ALG,
} bpa_create_kind_t;

bpa_session_t *bpa_session_create (bpa_per_thread_t *pt, const bpa_pool_t *p,
				   u32 pool_index, const bpa_block_t *b,
				   u16 port_priv, u8 proto, u32 fib, f64 now,
				   bpa_create_kind_t kind,
				   bpa_create_why_t *why);

/* Extended blocks: how many a subscriber holds, and attaching one more. */
u32 bpa_sub_n_blocks (bpa_per_thread_t *pt, u32 pool_index, u32 sub_idx);

/*
 * How full a subscriber's blocks are, across all of them.
 *
 * Summed from the port bitmaps, which the allocator maintains anyway, so
 * nothing new is counted per packet and the session table is never walked.
 */
static_always_inline void
bpa_sub_usage (const bpa_sub_t *sub, u32 *used, u32 *total)
{
  const bpa_subblock_t *sb;

  *used = *total = 0;
  vec_foreach (sb, sub->blocks)
    {
      *total += sb->map.n_ports;
      *used += sb->map.n_ports - sb->map.n_free;
    }
}
void bpa_sub_extend (bpa_per_thread_t *pt, const bpa_pool_t *p, u32 pool_index,
		     u32 sub_idx, const bpa_block_t *b);
/*
 * Reserve a consecutive public port pair, RTP even, and open the two mappings
 * an ALG has just announced. `want_ip_pub` is the public address the control
 * connection already presents: the media must arrive on the SAME address,
 * because the header carries only ports and the peer takes the address from
 * the connection it is answering. A pair taken from a block on another public
 * address of the same subscriber would be announced and never reachable.
 *
 * Returns 0 and the low public port. Both sessions are created as
 * expectations.
 */
int bpa_alg_reserve_pair (bpa_per_thread_t *pt, const bpa_pool_t *p,
			  u32 pool_index, u32 sub_idx, u32 want_ip_pub,
			  u16 priv_lo, u8 proto, u32 fib, f64 now,
			  u16 *pub_lo);

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
				       u32 remote, u32 fib, f64 now);
/* The server answered: the outbound direction can be keyed now. */
void bpa_session_gre_established (bpa_per_thread_t *pt, bpa_session_t *s,
				  u16 peer_call_id);
/*
 * An IPsec tunnel to one peer, created by the first outbound ESP packet.
 *
 * Takes NO port: there is nowhere in an ESP packet to put one, and a port
 * allocated here would be a number that never appears on the wire while
 * costing an entry of a 512-entry block. It still counts as a session, so the
 * per-subscriber caps apply and a dynamic block is held while the tunnel
 * lives.
 *
 * Writes the shared binding that the inbound direction reads. Returns 0 with
 * `*why` set when the caps refuse it, or when the peer already belongs to
 * another subscriber of the same public address.
 */
bpa_session_t *bpa_session_create_esp (bpa_per_thread_t *pt,
				       const bpa_pool_t *p, u32 pool_index,
				       const bpa_block_t *b, u32 remote,
				       u32 fib, f64 now,
				       bpa_create_why_t *why);
/*
 * The session a configured port forward describes, opened on first use.
 *
 * Called from both directions on a session miss - inbound because that is what
 * a forward is for, outbound because the subscriber may speak first from the
 * forwarded socket and must get the forwarded port when it does. The port is
 * already reserved in the subscriber's bitmap, so this takes it rather than
 * asking the allocator for one.
 */
bpa_session_t *bpa_session_static_open (bpa_per_thread_t *pt,
					const bpa_pool_t *p, u32 pool_index,
					const bpa_static_t *sf, f64 now);

/*
 * A forward added to or removed from a pool that is already carrying traffic.
 *
 * Both run on the main thread under the worker barrier: they walk every
 * worker's session table, and the datapath must not be in it at the time.
 */
void bpa_static_install (u32 pool_index, const bpa_static_t *sf);
void bpa_static_uninstall (u32 pool_index, const bpa_static_t *sf);

void bpa_session_free (bpa_per_thread_t *pt, bpa_session_t *s, f64 now);
u32 bpa_expire_walk (bpa_per_thread_t *pt, const bpa_timeouts_t *t,
		     f64 now, u32 budget);
/*
 * One budgeted step of a pool delete, on one thread's table. Runs under the
 * worker barrier and returns non-zero when the thread has none of that pool's
 * sessions left; the pool MUST be marked deleted first, or the walk can be
 * outrun by the traffic it is draining.
 */
int bpa_pool_drain (bpa_per_thread_t *pt, u32 pool_index, u32 *cursor,
		    u32 scan_budget, u32 free_budget, f64 now);

#endif /* __included_bpa_session_h__ */
