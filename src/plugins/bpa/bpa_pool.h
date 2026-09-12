/*
 * bpa_pool.h - BPA (Bloco de Portas Alocadas) address/port mapping.
 *
 * Reference implementation and test vectors live in ref/bpa_model.py.
 * Any change here must keep ref/validate.py and `make test` passing.
 *
 * Address sets are built from possibly non-contiguous prefixes: no provider
 * holds its IPv4 in one aligned block, so assuming a single prefix would
 * rule out most deployments. Everything downstream works on a dense index.
 */

#ifndef __included_bpa_pool_h__
#define __included_bpa_pool_h__

/* The mapping core is pure arithmetic, so it builds either inside VPP or
 * standalone against a small shim. Standalone is what the unit test uses:
 * it keeps the algorithm testable in CI without a VPP tree. */
#ifdef BPA_STANDALONE
#include "bpa_shim.h"
#else
#include <vlib/vlib.h>
#include <vnet/ip/ip4_packet.h>
#endif

/* Ranges are inlined in the pool, so no allocation sits on the datapath.
 * A provider with more than this many separate prefixes is rejected with a
 * clear error rather than silently truncated. */
#define BPA_MAX_RANGES 256

#define BPA_TIMEOUT_UDP		300
#define BPA_TIMEOUT_ICMP	60
#define BPA_TIMEOUT_TCP_TRANS	240
#define BPA_TIMEOUT_TCP_ESTAB	7440
/*
 * A session that sent SYN and never established. Separate from transitory,
 * and much shorter: one infected host scanning the internet holds a port per
 * probe for the transitory timeout, exhausts its own block, and reaches
 * support as "the internet stopped" with a healthy dataplane.
 */
#define BPA_TIMEOUT_TCP_SYN	20
/*
 * Seconds a released port stays out of circulation.
 *
 * A port freed and immediately handed out again can collide with a delayed
 * segment from the connection that just ended, which the subscriber sees as a
 * connection reset that nobody can reproduce. Short enough to cost no
 * measurable capacity: at 512 ports and a session per second, four seconds
 * withholds under 1% of a block.
 */
#define BPA_PORT_HOLDDOWN	4
/*
 * An ALG expectation that never sees a packet.
 *
 * When an RTSP SETUP announces its media ports, two mappings are opened before
 * anything has used them. If the media never arrives - the client gave up, the
 * server refused, the SETUP was retried with different ports - those two ports
 * would otherwise sit on the UDP timeout, five minutes each, out of a 512-port
 * block. A camera reconnecting in a loop turns that into exhaustion of its own
 * subscriber's block, which reaches support as "the internet stopped".
 *
 * Short, because an expectation that is going to be used is used within a
 * round trip of the SETUP reply.
 */
#define BPA_TIMEOUT_ALG_EXPECT	60
/*
 * A PPTP call's GRE mapping, idle.
 *
 * There is no RFC floor for this - RFC 4787 and RFC 5382 cover UDP and TCP.
 * The number comes from what the traffic looks like: a live PPTP tunnel
 * carries PPP, and PPP sends LCP echoes on the order of every 30 seconds, so
 * an idle mapping means a tunnel that is genuinely gone. Half an hour is long
 * enough that a tunnel surviving a brief outage keeps its Call ID, and short
 * enough that a subscriber who reconnects all day does not accumulate dead
 * calls in a 512-port block.
 */
#define BPA_TIMEOUT_GRE		1800

typedef struct
{
  u32 udp;
  u32 icmp;
  u32 tcp_trans;
  u32 tcp_estab;
  u32 tcp_syn;
  u32 gre;
  u32 port_holddown;
  u32 alg_expect;
} bpa_timeouts_t;

/*
 * The floors the RFCs set, and what going under each one costs.
 *
 * They are SHOULDs, not MUSTs, and this box treats them that way: a value
 * below a floor is ACCEPTED, because an operator may have a reason and a box
 * that refuses is less useful than the one they are replacing. What it will
 * not do is accept one silently.
 *
 * Shortening a timeout is the first thing anybody reaches for when a pool
 * fills, and it works - ports recycle faster and more subscribers fit on one
 * public address. What it also does is cut connections that are idle rather
 * than finished, and those come back weeks later as "it disconnects at
 * random", which nobody attributes to a number changed in another month.
 *
 * One list, so the command that sets a timeout, the warning it prints and
 * "show bpa timeouts" cannot disagree about what the floor is or why.
 *
 * _ (field, name, floor seconds, reference, what breaks under it)
 */
#define foreach_bpa_timeout_floor                                             \
  _ (udp, "udp", 120, "RFC 4787 REQ-5",                                       \
     "a UDP mapping closing while an application still believes it is "       \
     "there - DNS retries, SIP registrations, games reconnecting")            \
  _ (icmp, "icmp", 60, "RFC 5508 REQ-2",                                      \
     "an echo reply arriving after its mapping closed, which reads as "       \
     "packet loss on every path test through this box")                       \
  _ (tcp_trans, "tcp-transitory", 240, "RFC 5382 REQ-5",                      \
     "a half-closed connection dropped before its peer finishes closing it")  \
  _ (tcp_estab, "tcp-established", 7440, "RFC 5382 REQ-5",                    \
     "an idle but live connection cut - an SSH session left open over "       \
     "lunch, a database pool, a sensor that speaks once an hour")

/*
 * Reports every value below its floor, once per breach.
 *
 * Called where a timeout is CHANGED, never on the datapath: a warning per
 * packet is a warning nobody reads and a log nobody can keep.
 */
typedef void (*bpa_floor_report_t) (void *ctx, const char *name, u32 value,
				    u32 floor, const char *rfc,
				    const char *breaks);

static_always_inline u32
bpa_timeouts_check (const bpa_timeouts_t *t, bpa_floor_report_t cb, void *ctx)
{
  u32 n = 0;

  /* Zero means "inherit the global", not "zero seconds", so it is not a
   * breach: the value that will actually be used was checked where the global
   * was set. */
#define _(field, name, floor, rfc, breaks)                                    \
  if (t->field != 0 && t->field < (floor))                                    \
    {                                                                         \
      n++;                                                                    \
      if (cb)                                                                 \
	cb (ctx, name, t->field, (floor), rfc, breaks);                       \
    }
  foreach_bpa_timeout_floor
#undef _
  return n;
}

/*
 * A pool may override any of these; zero means "inherit the global value".
 * Resolved off the fast path - the datapath only stamps last_heard, and the
 * expiry walk is the single caller that needs a number.
 */
static_always_inline bpa_timeouts_t
bpa_timeouts_resolve (const bpa_timeouts_t *pool, const bpa_timeouts_t *global)
{
  bpa_timeouts_t r;
  r.udp = pool->udp ? pool->udp : global->udp;
  r.icmp = pool->icmp ? pool->icmp : global->icmp;
  r.tcp_trans = pool->tcp_trans ? pool->tcp_trans : global->tcp_trans;
  r.tcp_estab = pool->tcp_estab ? pool->tcp_estab : global->tcp_estab;
  r.tcp_syn = pool->tcp_syn ? pool->tcp_syn : global->tcp_syn;
  r.gre = pool->gre ? pool->gre : global->gre;
  r.port_holddown = pool->port_holddown ? pool->port_holddown
					: global->port_holddown;
  r.alg_expect = pool->alg_expect ? pool->alg_expect : global->alg_expect;
  return r;
}

typedef enum
{
  BPA_POLICY_INTERLEAVED = 0, /* neighbours spread across public IPs (default) */
  BPA_POLICY_BLOCKED,	      /* neighbours grouped on the same public IP */
} bpa_policy_t;

typedef enum
{
  BPA_MODE_DETERMINISTIC = 0,
  BPA_MODE_DYNAMIC,
} bpa_mode_t;

/*
 * Which inbound packets a mapping accepts (RFC 4787 REQ-8).
 *
 * ENDPOINT_INDEPENDENT is what this implementation has always done, and not by
 * intent expressed anywhere: the out2in key is (public ip, public port,
 * protocol) and carries no remote address, so any host may reach an open
 * mapping. That is a full-cone NAT, which peer-to-peer applications need and
 * which also lets unsolicited traffic through.
 *
 * Which of those an operator wants is their trade, not ours.
 */
typedef enum
{
  BPA_FILTER_ENDPOINT_INDEPENDENT = 0,
  BPA_FILTER_ADDRESS_DEPENDENT,
} bpa_filter_t;

/*
 * Application-level gateways, per pool.
 *
 * Per pool because the pool is the unit an operator sells: a residential pool
 * and a business pool are not the same customer and need not want the same
 * rewriting. One registry, so that adding an ALG means adding one line here
 * and the CLI, the running configuration and "show bpa alg" follow.
 *
 * Two masks, not one. `alg` is what is enabled; `alg_set` is what the
 * operator has actually stated an opinion about. A pool is built from a
 * zeroed structure in more than one place - the CLI, the binary API - and
 * neither should have to remember to restate the defaults. bpa_pool_init()
 * resolves the two, the same way it resolves max_blocks, so "not configured"
 * and "the documented default" are the same value by construction, and an
 * ALG that ships OFF by default is expressible rather than a special case.
 */
#define foreach_bpa_alg                                                       \
  _ (FTP, "ftp", "FTP PORT/EPRT and the 227/229 replies")                     \
  _ (RTSP, "rtsp", "RTSP Transport client_port, and the media pair it needs") \
  _ (PPTP, "pptp", "PPTP control channel and its GRE data channel")         \
  _ (ESP, "esp", "IPsec ESP passthrough, bound to the peer gateway")        \
  _ (SIP, "sip", "SIP over UDP: Via, Contact, SDP, and the RTP pair it needs")

typedef enum
{
#define _(sym, nm, desc) BPA_ALG_BIT_##sym,
  foreach_bpa_alg
#undef _
    BPA_N_ALG,
} bpa_alg_bit_t;

typedef enum
{
#define _(sym, nm, desc) BPA_ALG_##sym = 1 << BPA_ALG_BIT_##sym,
  foreach_bpa_alg
#undef _
} bpa_alg_t;

/*
 * What ships on.
 *
 * All five, and each for its own reason rather than by a blanket rule. The
 * general rule stands - an ALG that misparses corrupts traffic that would
 * otherwise merely fail, so an ambiguous parse defaults off - and these are
 * the ones whose parse can be made strict:
 *
 *   FTP  - active FTP does not work at all without it;
 *   RTSP - the black frame nobody blames on the CGNAT;
 *   PPTP - its rewrite changes no length, so no sequence number moves, and
 *          every message is guarded by a magic cookie and its own length
 *          field. Without it PPTP fails for everyone, not only on collision.
 *   ESP  - it parses nothing and rewrites nothing but the addresses, so there
 *          is no payload it can corrupt. Without it an ESP packet is an
 *          unsupported protocol and the tunnel does not come up at all.
 *   SIP  - #14 names it as one of the two that pay for themselves. It is on
 *          for a stricter reason than the others: it rewrites ONLY a literal
 *          IPv4 address equal to the subscriber's own private address, so a
 *          message it misreads is a message it leaves alone, and a CPE that
 *          already knows its public address is untouched by construction.
 *          Without it the subscriber can call out and nobody can call in.
 */
#define BPA_ALG_DEFAULT                                                       \
  (BPA_ALG_FTP | BPA_ALG_RTSP | BPA_ALG_PPTP | BPA_ALG_ESP | BPA_ALG_SIP)

static_always_inline int
bpa_alg_on (u32 enabled, u32 bit)
{
  return (enabled & bit) != 0;
}


/*
 * Precomputed reciprocal, so the datapath never issues a hardware divide.
 * Used for the pool policy split and for spreading subscribers over workers.
 * ref/check_magic.py proves it exact for every divisor below BPA_MAX_CAPACITY.
 */
typedef struct
{
  u32 d;
  u64 magic;
  u8 shift;
  u8 log2;
  u8 is_pow2;
} bpa_divisor_t;

void bpa_divisor_init (bpa_divisor_t *dv, u32 d);

static_always_inline u32
bpa_divide (const bpa_divisor_t *dv, u32 n)
{
  if (PREDICT_TRUE (dv->is_pow2))
    return n >> dv->log2;
  return (u32) (((u64) n * dv->magic) >> dv->shift);
}

static_always_inline u32
bpa_modulo (const bpa_divisor_t *dv, u32 n)
{
  return n - bpa_divide (dv, n) * dv->d;
}

typedef struct
{
  u32 base;	 /* first address, host order */
  u32 count;	 /* addresses in this range */
  u32 first_idx; /* dense index of `base` within the set */
} bpa_range_t;

/*
 * A set of addresses presented as a dense index space [0, count).
 * Ranges are sorted by base, which - because first_idx is assigned in that
 * same order - makes them sorted by first_idx too. One binary search serves
 * both directions.
 */
typedef struct
{
  bpa_range_t ranges[BPA_MAX_RANGES];
  u32 n_ranges;
  u32 count;
} bpa_addr_set_t;

/*
 * A static port forward: one inbound port pinned to one subscriber's socket.
 *
 * Called "forward" everywhere an operator can see, and `bpa_static_t` here,
 * because bpa_forward() already means the inside -> outside arithmetic and two
 * things called forward in one file is how a reader is lost.
 *
 * The public ADDRESS is not stored as a choice: it is the subscriber's own,
 * the one bpa_forward() gives it. Letting the operator name a different one
 * would break paired address pooling (RFC 4787 REQ-2) - the subscriber would
 * answer on an address it never sends from - and would make the deterministic
 * reverse arithmetic disagree with the session table. So the operator states
 * the public PORT, and it MUST fall inside that subscriber's own block; a port
 * in someone else's block is refused rather than quietly taken from them.
 *
 * ip_pub and port_off are derived, not configured. They are stored because
 * every reader of this table needs both and recomputing them means repeating
 * bpa_forward() on a path that already has the answer.
 */
typedef struct
{
  u32 ip_priv;	 /* host order */
  u32 ip_pub;	 /* host order; derived - the subscriber's own address */
  u16 port_pub;
  u16 port_priv;
  u16 port_off;	 /* port_pub - block.port_lo, the bit reserved in the map */
  u8 proto;	 /* bpa_proto_t; TCP or UDP, the only two with ports to pin */
} bpa_static_t;

typedef struct
{
  u8 *name;

  bpa_addr_set_t inside;
  bpa_addr_set_t outside;

  u16 port_min;
  u16 port_max;
  u16 block_size; /* power of two */
  bpa_policy_t policy;
  bpa_mode_t mode;
  bpa_filter_t filter;

  /* Per-pool session lifetimes. Zero in any field means inherit the global.
   * The pool is the unit an operator sells, so it is the unit of policy. */
  bpa_timeouts_t timeouts;

  /*
   * How many blocks one subscriber may hold. 1 is the historical behaviour:
   * fill the block and be dropped. Above 1, a subscriber whose blocks are all
   * full is given another - which turns "the internet stopped for one
   * customer" into a logged, bounded event.
   *
   * Bounded on purpose. Without a cap one infected host consumes the pool
   * that every other subscriber needs, and the operator trades a customer
   * complaint for an outage.
   */
  u8 max_blocks;

  /* Which ALGs run here, and which of those the operator actually chose.
   * Resolved once by bpa_pool_init(); see the registry above. */
  u32 alg;
  u32 alg_set;

  /*
   * Traffic that leaves untranslated.
   *
   * Some destinations have to see the real source: the operator's own
   * management network, an internal server, a CDN cache that accounts by
   * client address, a government portal that refuses shared addresses. And
   * some sources must not be translated at all - a customer with their own
   * public address on the same access VLAN.
   *
   * The same structure the address sets use, for the reason the issue gives:
   * it is inline, so a lookup costs a binary search over a small array and
   * never an allocation. Building them through bpa_addr_set_build() also means
   * adjacent prefixes merge exactly as they do for a pool's own ranges, so the
   * search sees one range where an operator typed four.
   */
  bpa_addr_set_t bypass_src;
  bpa_addr_set_t bypass_dst;

  /*
   * The VRF this pool serves, as a FIB index. ~0 means every VRF, which is
   * what every configuration written before this existed means.
   *
   * A pool bound to a VRF is how one inside prefix can be served by two pools
   * for two tenants. Without it they would be two pools claiming the same
   * range, and the first one would answer for both (issue #73).
   *
   * `vrf_id` is what the operator typed; `fib_index` is what the FIB calls it.
   * Both are kept because the first is the only one worth printing and the
   * second is the only one worth comparing.
   */
  u32 fib_index;
  u32 vrf_id;

  /*
   * The VRF translated traffic leaves by, when it differs from the
   * subscriber's. ~0 leaves the packet where it was, which is what a
   * single-VRF box wants and what every existing configuration gets.
   */
  u32 out_fib_index;
  u32 out_vrf_id;

  /*
   * Raise an alarm when a subscriber's blocks are this full, as a percentage.
   * Zero is off.
   *
   * RFC 8158 natEvent 12 already reports exhaustion, which is to say it
   * reports the moment a customer started losing traffic - by then the support
   * call is in the queue. This is the number that fires before that.
   */
  u8 util_alarm;

  /*
   * Per-subscriber bounds. Zero is unlimited in both, which is the default and
   * the point: an upgrade must not start refusing traffic that worked
   * yesterday.
   *
   * `max_sessions` caps what one subscriber holds at once. `max_rate` caps how
   * fast it may open new ones, which is the one that bounds a scan - a host
   * that opens and abandons sessions at line rate stays under any concurrent
   * cap while consuming the block as fast as the timers release it.
   */
  u32 max_sessions;
  u32 max_rate;

  /*
   * TCP MSS clamping. Zero is off, which is the default: a clamp is a change
   * to somebody else's handshake and an operator asks for it.
   *
   * `tcp_mss_auto` derives the value from the interface MTUs instead of the
   * operator naming one; the resolved number is not stored here, because an
   * MTU can change under a running box and a clamp derived from a stale one
   * is a clamp that quietly does nothing.
   */
  u16 tcp_mss;
  u8 tcp_mss_auto;

  /*
   * Static port forwards, sorted by (port_pub, proto).
   *
   * ONE ordering, and everything reads it through the same binary search: the
   * out2in lookup wants an exact public port, the in2out lookup and the
   * bitmap reservation both want the run of forwards inside one block's port
   * range, and a run in a sorted vector is where a binary search lands. A
   * second index keyed the other way was the obvious alternative and would be
   * a second thing to keep in step.
   */
  bpa_static_t *statics;

  /*
   * The delete has begun and this definition is being drained.
   *
   * The slot stays allocated until the last session that came out of it has
   * been freed, because VPP reuses pool indices: a session that outlived its
   * definition would be inherited by whatever pool took the index next, and
   * a subscriber would go on being translated onto a public port the new
   * mapping has already given to somebody else. Nothing may bind a new
   * session to a pool carrying this flag, which is what makes the drain
   * finish rather than chase traffic it is still creating.
   */
  u8 deleted;

  /* derived */
  u32 port_count;
  u32 blocks_per_ip;
  u32 capacity; /* outside.count * blocks_per_ip */

  u8 log2_block; /* block_size is a power of two */

  /*
   * Divisor for the policy split:
   *   INTERLEAVED -> outside.count   (blk_idx = sub / d, pub_idx = sub % d)
   *   BLOCKED     -> blocks_per_ip   (pub_idx = sub / d, blk_idx = sub % d)
   */
  bpa_divisor_t div;
} bpa_pool_t;

typedef struct
{
  u32 sub_idx;
  u32 pub_idx;
  u32 blk_idx;
  u32 ip_pub; /* host order */
  u16 port_lo;
  u16 port_hi;
} bpa_block_t;

clib_error_t *bpa_pool_init (bpa_pool_t *p);
clib_error_t *bpa_addr_set_build (bpa_addr_set_t *s, const u32 *bases,
				  const u32 *counts, u32 n);

/* ------------------------------------------------------------------ */
/* address set lookups                                                */

/* Dense index of `addr`, or -1 when it is not in the set. */
static_always_inline int
bpa_set_index_of (const bpa_addr_set_t *s, u32 addr, u32 *idx)
{
  u32 lo = 0, hi;

  if (PREDICT_FALSE (s->n_ranges == 0))
    return -1;
  hi = s->n_ranges - 1;

  /* the common deployment has a single contiguous range */
  if (PREDICT_TRUE (s->n_ranges == 1))
    {
      u32 off = addr - s->ranges[0].base;
      if (PREDICT_FALSE (off >= s->ranges[0].count))
	return -1;
      *idx = off;
      return 0;
    }

  while (lo <= hi)
    {
      u32 mid = lo + (hi - lo) / 2;
      const bpa_range_t *r = &s->ranges[mid];

      if (addr < r->base)
	{
	  if (mid == 0)
	    break;
	  hi = mid - 1;
	}
      else if (addr - r->base >= r->count)
	lo = mid + 1;
      else
	{
	  *idx = r->first_idx + (addr - r->base);
	  return 0;
	}
    }
  return -1;
}

/* Address at dense index `idx`, or -1 when out of range. */
static_always_inline int
bpa_set_addr_of (const bpa_addr_set_t *s, u32 idx, u32 *addr)
{
  u32 lo = 0, hi = s->n_ranges - 1;

  if (PREDICT_FALSE (idx >= s->count))
    return -1;

  if (PREDICT_TRUE (s->n_ranges == 1))
    {
      *addr = s->ranges[0].base + idx;
      return 0;
    }

  while (lo <= hi)
    {
      u32 mid = lo + (hi - lo) / 2;
      const bpa_range_t *r = &s->ranges[mid];

      if (idx < r->first_idx)
	{
	  if (mid == 0)
	    break;
	  hi = mid - 1;
	}
      else if (idx - r->first_idx >= r->count)
	lo = mid + 1;
      else
	{
	  *addr = r->base + (idx - r->first_idx);
	  return 0;
	}
    }
  return -1;
}

/* ------------------------------------------------------------------ */
/* bypass                                                              */

/*
 * Does this packet leave untranslated?
 *
 * Consulted only when the session lookup has already missed, which is where it
 * belongs: bypassed traffic never has a session, so a packet that matched one
 * cannot be bypassed. The cost of that placement is stated rather than hidden
 * - a bypassed flow pays the pool lookup on every packet, because there is no
 * session for it to hit. That is the price of not allocating anything for it,
 * and the issue asks for exactly that trade.
 */
static_always_inline int
bpa_is_bypassed (const bpa_pool_t *p, u32 src, u32 dst)
{
  u32 idx;

  if (PREDICT_TRUE (p->bypass_src.count == 0 && p->bypass_dst.count == 0))
    return 0;

  return bpa_set_index_of (&p->bypass_src, src, &idx) == 0
	 || bpa_set_index_of (&p->bypass_dst, dst, &idx) == 0;
}

/* ------------------------------------------------------------------ */
/* static port forwards                                                */

/*
 * Not in the standalone build. A forward is configuration laid over the
 * mapping, not part of it: ref/bpa_model.py specifies the arithmetic, and the
 * unit test compiles this header against a shim that has no VPP vectors
 * because it has no configuration to hold.
 */
#ifndef BPA_STANDALONE

/*
 * Index of the first forward whose public port is >= `port`, or vec_len when
 * there is none. The three callers below are the whole interface; nothing
 * indexes this vector directly, because its ordering is an implementation
 * detail of exactly this search.
 */
static_always_inline u32
bpa_static_lower (const bpa_static_t *v, u32 n, u16 port)
{
  u32 lo = 0, hi = n;

  while (lo < hi)
    {
      u32 mid = lo + (hi - lo) / 2;

      if (v[mid].port_pub < port)
	lo = mid + 1;
      else
	hi = mid;
    }
  return lo;
}

/*
 * The forward that owns (public address, public port, protocol), or 0.
 *
 * The ADDRESS is part of the key because a pool has more than one, and a port
 * on one of them is not the same socket as the same port on another: the
 * pool's own arithmetic has already given 203.0.113.16:1100 and
 * 203.0.113.17:1100 to two different subscribers. Keyed on the port alone,
 * one forward made the port unsellable to the N-1 subscribers who share its
 * offset on the other addresses (issue #214).
 *
 * Consulted on the out2in path only when the session lookup missed, which is
 * unsolicited inbound traffic: a forwarded port that is in use answers from
 * the session table like any other mapping. The address costs that path
 * nothing - the packet arrived at it, and the arithmetic above the call has
 * already used it to find the block.
 */
static_always_inline const bpa_static_t *
bpa_static_by_pub (const bpa_pool_t *p, u32 ip_pub, u16 port_pub, u8 proto)
{
  u32 n = vec_len (p->statics);
  u32 i = bpa_static_lower (p->statics, n, port_pub);

  for (; i < n && p->statics[i].port_pub == port_pub; i++)
    if (p->statics[i].proto == proto && p->statics[i].ip_pub == ip_pub)
      return &p->statics[i];
  return 0;
}

/*
 * The forward that pins (subscriber socket, protocol), or 0.
 *
 * Searched over the block's port range rather than by private port, because
 * the vector is ordered by public port and a forward always sits inside its
 * own subscriber's block. The run is short - the forwards of one subscriber -
 * so the scan is bounded by what one customer was given, not by the pool.
 *
 * Consulted on the in2out path only when the session lookup missed. Without
 * it a subscriber who sends FROM the forwarded socket gets an ordinary
 * dynamic port, and the mapping the operator configured is then contradicted
 * by the subscriber's own traffic.
 */
static_always_inline const bpa_static_t *
bpa_static_by_priv (const bpa_pool_t *p, const bpa_block_t *b, u32 ip_priv,
		    u16 port_priv, u8 proto)
{
  u32 n = vec_len (p->statics);
  u32 i = bpa_static_lower (p->statics, n, b->port_lo);

  for (; i < n && p->statics[i].port_pub <= b->port_hi; i++)
    if (p->statics[i].ip_priv == ip_priv
	&& p->statics[i].port_priv == port_priv
	&& p->statics[i].proto == proto)
      return &p->statics[i];
  return 0;
}

#endif /* BPA_STANDALONE */

/* ------------------------------------------------------------------ */
/* policy split                                                        */

static_always_inline void
bpa_split (const bpa_pool_t *p, u32 sub_idx, u32 *pub_idx, u32 *blk_idx)
{
  u32 q = bpa_divide (&p->div, sub_idx);
  u32 r = sub_idx - q * p->div.d;

  if (PREDICT_TRUE (p->policy == BPA_POLICY_INTERLEAVED))
    {
      *blk_idx = q; /* divisor is outside.count */
      *pub_idx = r;
    }
  else
    {
      *pub_idx = q; /* divisor is blocks_per_ip */
      *blk_idx = r;
    }
}

static_always_inline u32
bpa_join (const bpa_pool_t *p, u32 pub_idx, u32 blk_idx)
{
  if (PREDICT_TRUE (p->policy == BPA_POLICY_INTERLEAVED))
    return blk_idx * p->div.d + pub_idx;
  return pub_idx * p->div.d + blk_idx;
}

/* ------------------------------------------------------------------ */
/* translation                                                         */

/* inside -> outside. Returns 0 on success. */
static_always_inline int
bpa_forward (const bpa_pool_t *p, u32 ip_priv, bpa_block_t *b)
{
  u32 sub_idx;

  if (PREDICT_FALSE (bpa_set_index_of (&p->inside, ip_priv, &sub_idx)))
    return -1;
  if (PREDICT_FALSE (sub_idx >= p->capacity))
    return -1;

  bpa_split (p, sub_idx, &b->pub_idx, &b->blk_idx);

  if (PREDICT_FALSE (bpa_set_addr_of (&p->outside, b->pub_idx, &b->ip_pub)))
    return -1;

  b->sub_idx = sub_idx;
  b->port_lo = p->port_min + b->blk_idx * p->block_size;
  b->port_hi = b->port_lo + p->block_size - 1;
  return 0;
}

/*
 * outside -> inside, stopping at the dense subscriber index.
 * The handoff node needs only this: resolving the index to an address
 * would cost a second binary search it has no use for.
 */
static_always_inline int
bpa_reverse_sub (const bpa_pool_t *p, u32 ip_pub, u16 port, u32 *sub_idx)
{
  u32 pub_idx, blk_idx;

  if (PREDICT_FALSE (bpa_set_index_of (&p->outside, ip_pub, &pub_idx)))
    return -1;
  if (PREDICT_FALSE (port < p->port_min || port > p->port_max))
    return -1;

  blk_idx = (u32) (port - p->port_min) >> p->log2_block;
  if (PREDICT_FALSE (blk_idx >= p->blocks_per_ip))
    return -1;

  *sub_idx = bpa_join (p, pub_idx, blk_idx);
  return 0;
}

/*
 * outside -> inside. Arithmetic plus two binary searches over small arrays,
 * with no session lookup at all. This is why the deterministic mode needs
 * no allocation logging and why the return path shards cleanly.
 */
static_always_inline int
bpa_reverse (const bpa_pool_t *p, u32 ip_pub, u16 port, u32 *ip_priv)
{
  u32 sub_idx;

  if (PREDICT_FALSE (bpa_reverse_sub (p, ip_pub, port, &sub_idx)))
    return -1;

  return bpa_set_addr_of (&p->inside, sub_idx, ip_priv);
}

/*
 * Worker that owns a subscriber. Both directions must agree on this,
 * otherwise the per-subscriber port bitmap becomes shared state.
 *
 * The worker count is whatever the operator configured, so this uses the
 * same reciprocal as the pool split rather than requiring a power of two.
 */
static_always_inline u32
bpa_owner_worker (const bpa_divisor_t *workers, u32 sub_idx)
{
  return bpa_modulo (workers, sub_idx);
}

#endif /* __included_bpa_pool_h__ */
