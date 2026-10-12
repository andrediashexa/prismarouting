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
#include "bpa_nat64.h"

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
 * Do two address ranges have any address in common?
 *
 * HERE RATHER THAN BESIDE ITS CALLER, so it can be driven without a VPP: it
 * is the rule that refuses a pool overlapping another, and what a mistake in
 * it costs is in bpa_check_pool_overlap()'s own comment - two pools claiming
 * one inside address means the second accepts configuration and translates
 * nothing, with nothing saying so (#73).
 *
 * THE CASTS ARE THE POINT. `base + count` is where a range ends, and on a
 * range ending at the top of the address space that sum wraps in 32 bits: a
 * pool at 255.255.255.0/24 would then look disjoint from everything and
 * overlap checks would pass on a box where they must not. Both sides are
 * widened before the comparison, and test/test_bpa_pool.c drives exactly
 * that case.
 */
static_always_inline int
bpa_ranges_overlap (const bpa_range_t *a, const bpa_range_t *b)
{
  return (u64) a->base < (u64) b->base + b->count
	 && (u64) b->base < (u64) a->base + a->count;
}

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
 * reverse arithmetic disagree with the session table.
 *
 * That argument stands on its own, and the property it protects is not
 * unconditional: in dynamic mode an extension block normally lands on another
 * public address (#226). A forward is still refused on any address but the
 * subscriber's own first one, which is the conservative side of that.
 *
 * So the operator states
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
  /*
   * A NAT64 pool's forward (#485 phase 4): the host it points at, one /128
   * of the subscriber's delegated prefix. ip_priv is then the subscriber's
   * INDEX - its pseudo inside address - so the validation, the pin and the
   * reply's agreement check are the IPv4 ones; the host is what the NAT64
   * session is keyed and translated on. bpa_nat64_static_resolve() fills
   * ip_priv from it.
   */
  u8 has_host6;
  u8 host6[16];
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

  /*
   * How many blocks a subscriber is given at once when its first block is
   * taken: the first allocation, where max_blocks is the most it may grow
   * to (#462). 0 and 1 are the historical behaviour, one block. Asked for by
   * the maintainer as a first allocation of 512 ports growing in steps of
   * 256 - block_size 256, initial_blocks 2 - and read only in dynamic mode,
   * where the allocator decides at the first packet what a subscriber gets.
   *
   * NOT part of the mapping, like block_stride below: it changes what the
   * NEXT new subscriber is handed and moves nothing already held.
   */
  u8 initial_blocks;

  /*
   * Distance between the blocks one subscriber holds, in blocks of its own
   * public address. Zero is what the allocator has always done: an extension
   * is whatever comes off the free list next.
   *
   * One is contiguous - the second block's ports start where the first block's
   * end - which is one range for an investigator to read instead of two.
   * Larger spreads a subscriber across its address's port range, which is what
   * makes it harder to profile one by watching a single range. Neither is
   * obviously right, which is why it is a setting.
   *
   * NOT part of the mapping. bpa_forward() does not read it and must not: in
   * deterministic mode a change to the mapping re-maps every subscriber on the
   * pool, and a setting about the SECOND block a subscriber gets has no
   * business moving the first (docs/pool-resize.md, issue #23).
   */
  u16 block_stride;

  /*
   * Blocks held out of the formula on every public address, so a
   * deterministic subscriber that fills its block has somewhere to be given a
   * second one.
   *
   * Deterministic mode has no spare block by construction: bpa_forward()
   * assigns every block to some subscriber, so a block handed out as an
   * extension is one taken from somebody else and their mapping breaks with
   * no error anywhere. A reserve is the only way to have one to give, and it
   * is paid for in capacity (issue #39).
   *
   * Counted PER PUBLIC ADDRESS rather than as a pool-wide total, because the
   * allocator PREFERS the subscriber's own public address for its reserve
   * block - RFC 4787 REQ-2, the same property a static forward is refused on
   * another address to protect. A total that did not divide evenly would
   * leave some addresses with no reserve at all, so the preference could
   * never be honoured for the subscribers on them.
   *
   * A preference and not a rule, and `bpa_dyn.c` says why where it is made:
   * the reserve is sharded by index, so the blocks on a subscriber's own
   * address are not all in its worker's slice and the ones that are may be
   * held. Refusing the extension rather than taking a block elsewhere would
   * turn a property about layout into a customer losing traffic. This comment
   * used to say the extension "has to land" there, which is a guarantee the
   * code deliberately does not make (#226). A DYNAMIC pool's extensions now
   * carry the same preference, by the same argument and with the same
   * fall-back; bpa_pair_step() below is the arithmetic both walk with.
   *
   * Zero is every configuration written before this existed, and leaves
   * blocks_mapped equal to blocks_per_ip and the mapping untouched.
   */
  u16 block_reserve;

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
   * A pool whose sides are prefix-lists (#589). The lists themselves are
   * the VTY's - they are FRR's `ip prefix-list` - and arrive here resolved:
   * `inside` and `outside` above are already the effective sets, permits
   * less denies, so the formula maps over exactly those and nothing else.
   *
   * `untranslated` is what the inside list DENIES: sources that leave with
   * their own address. NOT part of `inside`, and that is the point of a set
   * of its own: a bypass source sits inside the pool's inside and so holds
   * a subscriber index of the formula, which for a deny would be a mapping
   * computed over addresses the operator excluded. It is consulted only for
   * a source no pool claims (bpa_pool_by_untranslated()).
   *
   * `outside_excluded` is what the outside list's denies took out of its
   * permits. The dataplane never translates to it - it is not in `outside`
   * - and keeps it so the trail and `show bpa config` can say which
   * addresses a deterministic mapping was computed without.
   *
   * The names are kept for the same readback, NUL-terminated: "" is a side
   * given by its prefixes.
   */
  bpa_addr_set_t untranslated;
  bpa_addr_set_t outside_excluded;
  char inside_list[64];
  char outside_list[64];

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

  /*
   * What every worker's slice of a dynamic `policy interleaved` pool shares:
   * how many subscribers each public address has, pool-wide (#603). 0 on
   * every other pool, and on a configuration bpa_pool_add() has not made a
   * pool yet: the CLI and the API build theirs zeroed, so a copy taken from
   * one never owns the array the pool will.
   *
   * Here and not in a slice because no one slice owns it: an arrival on
   * worker 0 is placed by what workers 1 and 2 have put on each address.
   * The type is bpa_dyn.h's; this file only carries the pointer.
   */
  struct bpa_dyn_share *dyn_share;

  /* derived */
  u32 port_count;
  u32 blocks_per_ip; /* blocks a public address HOLDS */
  /*
   * Blocks the formula may hand out on one public address:
   * blocks_per_ip - block_reserve. Kept apart from blocks_per_ip on purpose -
   * the reverse direction needs both to tell a reserve port from a port that
   * is outside the pool altogether, and those two go to different places.
   */
  u32 blocks_mapped;
  u32 reserve_count; /* outside.count * block_reserve */
  u32 capacity;	     /* outside.count * blocks_mapped */

  u8 log2_block; /* block_size is a power of two */

  /*
   * Divisor for the policy split:
   *   INTERLEAVED -> outside.count   (blk_idx = sub / d, pub_idx = sub % d)
   *   BLOCKED     -> blocks_mapped   (pub_idx = sub / d, blk_idx = sub % d)
   *
   * blocks_mapped and not blocks_per_ip: under the blocked policy the divisor
   * IS the mapping, so a reserve re-divides it and moves every subscriber past
   * the first public address. That is why a reserve is a capacity change and
   * goes through the #23 procedure.
   */
  bpa_divisor_t div;

  /*
   * NAT64 (#485). A pool whose inside is IPv6: `inside` then holds a dense
   * pseudo set [0, inside6.count) so every block, capacity and worker rule
   * that reads sub_idx works unchanged, and no IPv4 packet ever matches it -
   * bpa_pool_by_inside() skips a NAT64 pool. The subscriber is a prefix of
   * nat64_sub_len bits out of inside6; the destination is the IPv4 address
   * embedded under nat64_prefix (RFC 6052). The prefixes are kept as typed,
   * for `show bpa config` to give back what was configured.
   */
  u8 is_nat64;
  u8 nat64_len;
  u8 nat64_prefix[16];
  bpa_set6_t inside6;
  u8 n_inside6;
  u8 inside6_addr[BPA_NAT64_MAX_PREFIXES][16];
  u8 inside6_len[BPA_NAT64_MAX_PREFIXES];
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
/*
 * The two stride refusals that are facts about the pool rather than about
 * this box's thread count, for a pool that is past the point where
 * blocks_mapped is known: `stride` on a pool that is not dynamic, and one
 * that steps off the subscriber's own public address. Shared by the two
 * messages a stride arrives on - bpa_pool_init() for a pool being built,
 * bpa_pool_settings_set() for one that stays put - and `stride` is an
 * argument rather than p->block_stride because on the second route the pool
 * still carries its old one. Nothing is written; zero is never refused.
 */
clib_error_t *bpa_pool_stride_refusal (const bpa_pool_t *p, u32 stride);
/*
 * The first allocation, refused where it could never be what it says (#462):
 * on a pool that is not dynamic, and above the cap the same message carries.
 * `initial` and `max_blocks` are arguments for the reason `stride` is one
 * above: on the settings route the pool still carries its old values.
 * Nothing is written; 0 and 1 are never refused.
 */
clib_error_t *bpa_pool_initial_refusal (const bpa_pool_t *p, u32 initial,
					u32 max_blocks);
clib_error_t *bpa_nat64_cfg_finish (bpa_pool_t *cfg, u8 sub_len);
int bpa_nat64_static_resolve (const bpa_pool_t *p, bpa_static_t *sf);
const char *bpa_nat64_unsupported (const bpa_pool_t *cfg);
clib_error_t *bpa_addr_set_build (bpa_addr_set_t *s, const u32 *bases,
				  const u32 *counts, u32 n);

/* ------------------------------------------------------------------ */
/* capacity                                                            */

/*
 * The number an operator sizes a pool against, in one place.
 *
 * Three readers now: "show bpa pool" prints it, bpa_pool_details reports it
 * over the binary API, and bpa_pool_remap_count() walks it. A number that has
 * to agree in three files is a number with one holder, not three copies kept
 * in step by hand - and the arithmetic below had two of those already.
 *
 * Here rather than beside the command that prints it because here it builds
 * standalone. Until this moved, the central capacity claim of the product was
 * asserted by nothing that runs without a container: `make test` could not
 * reach it, and a regression in it would have shipped green.
 */

/*
 * Subscribers the pool has a block for at all.
 *
 * Capacity counts blocks and the inside set counts addresses, and neither
 * bounds the other: a pool can hold more blocks than it has subscribers to
 * give them to, and an inside range can be far larger than the public
 * addresses behind it.
 */
static_always_inline u32
bpa_pool_usable (const bpa_pool_t *p)
{
  return p->capacity < p->inside.count ? p->capacity : p->inside.count;
}

/*
 * How many subscribers a pool serves whatever addresses they have.
 *
 * A worst case and deliberately not a mean. The operator sizing a pool has no
 * say over which of their inside addresses are switched on at any moment, so
 * a figure that only holds on the average leaves a box refusing subscribers
 * below the number it printed - the same false promise this exists to remove,
 * arriving one step quieter. What is returned is the largest n for which
 * EVERY set of n subscribers is served.
 *
 * In dynamic mode with workers the pool's blocks are sharded so allocation
 * needs no lock: worker w owns every block whose index is congruent to w, and
 * a subscriber may only draw from the worker its own index maps to. Both
 * halves are right on their own - one keeps allocation worker-local, the
 * other keeps a flow's two directions on one worker - and neither was
 * designed against the other. A subscriber index is the offset of its address
 * within the inside set, not a counter, so on any real address plan the two
 * do not balance.
 *
 * A worker refuses only when more inside addresses map to it than it has
 * blocks, and the adversary then puts one subscriber more than that worker's
 * slice on it. So the answer is the smallest slice among the workers that can
 * be crowded, and the whole usable set when none of them can be.
 *
 * That second case is why this is not simply capacity/n_workers. A pool of 12
 * blocks in front of 8 inside addresses serves all 8 on any worker count,
 * because no worker can be handed more subscribers than it has blocks for;
 * calling it 4 would send an operator to buy addresses they already own.
 *
 * The number does not depend on WHICH addresses are in use, only on how many
 * the inside set holds - that is what makes it safe to print.
 *
 * Deterministic mode and a single worker have nothing to lose to the shard.
 * There a block is derived rather than allocated, or there is one slice and
 * it is the whole pool, so the answer is the ceiling and the caller prints no
 * caveat. That test lives here and not at each reader: a caveat shown where
 * it does not apply is one people learn to skip, and then they skip the one
 * that matters, so the three readers must not each decide it for themselves.
 *
 * Only the MODE half of that guard changes the answer; measured by removing
 * it, and the deterministic pools then promise a floor they cannot lose. The
 * worker half does not - at one worker the loop finds one slice holding
 * everything, and at zero, which is what bpa_main.n_workers reads on a
 * single-threaded box and what the API handler passes, it does not run at
 * all. It is kept because a reader asking "what about a box with no workers"
 * should find the answer here rather than derive it from an empty loop.
 */
static_always_inline u32
bpa_pool_guaranteed (const bpa_pool_t *p, u32 n_workers)
{
  u32 w, worst = ~0u;

  if (p->mode != BPA_MODE_DYNAMIC || n_workers <= 1)
    return bpa_pool_usable (p);

  for (w = 0; w < n_workers; w++)
    {
      /* Worker w owns the block indices congruent to w (bpa_dyn_init), and
       * the subscriber indices congruent to w may only draw from them
       * (bpa_owner_worker). */
      u32 nb = p->capacity, ns = p->inside.count;
      u32 blocks = nb / n_workers + (w < nb % n_workers ? 1 : 0);
      u32 subs = ns / n_workers + (w < ns % n_workers ? 1 : 0);

      if (subs > blocks && blocks < worst)
	worst = blocks;
    }

  /* No worker can be crowded, so every usable subscriber is served. Said as
   * the ceiling rather than as inside.count - the two are equal here, since
   * a pool nobody can crowd has a block per inside address - because that
   * makes "the floor never exceeds the ceiling" true by construction instead
   * of by an argument a later edit can break. */
  return worst == ~0u ? bpa_pool_usable (p) : worst;
}

/*
 * `<addr>/<len>` as the range a pool or a bypass holds: its first address,
 * host order, and how many. 0 when it is one, -1 when it cannot be.
 *
 * THE ONE PLACE THIS IS WORKED OUT (#473). It was `1u << (32 - len)` written
 * out seven times across bpa_cli.c and bpa_api.c, and at len 0 that is a
 * shift by the width of the type - undefined, and 1 on x86: `bypass
 * destination 0.0.0.0/0` bypassed the single address 0.0.0.0, in silence. A
 * /0 is refused rather than represented: a range's count is a u32 and holds
 * at most 2^31 here, and every address is neither a pool nor a bypass - a box
 * meant to translate nothing has no `cgnat inside`.
 */
static_always_inline int
bpa_prefix_range (u32 addr, u32 len, u32 *base, u32 *count)
{
  if (len == 0 || len > 32)
    return -1;
  *count = len == 32 ? 1 : 1u << (32 - len);
  *base = addr & ~(*count - 1);
  return 0;
}

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

/*
 * Does this pool claim `addr` as a subscriber, from a packet in VRF `fib`?
 * (#472: the rule bpa_pool_by_inside() walks the pools with.) A pool bound
 * to a VRF claims only its VRF's packets, so two tenants may share an inside
 * prefix; ~0 on either side is "no VRF stated" and matches any. A deleted
 * pool - draining - claims nobody new.
 */
static_always_inline int
bpa_pool_claims_inside (const bpa_pool_t *p, u32 addr, u32 fib, u32 *sub_idx)
{
  /*
   * A pool bound to a VRF answers only for that one, which is what lets
   * two pools serve the same inside prefix for two tenants.
   *
   * ~0 means "every VRF" on the POOL - what a configuration written
   * before this existed means - and "any VRF" in the QUERY, which is what
   * a show command asks, having no packet and therefore no VRF. Both,
   * because neither a pool nor a question should have to lie about which
   * it means: without the second, a show command would report that a
   * VRF-bound pool's subscriber belongs to no pool at all.
   */
  if (fib != ~0u && p->fib_index != ~0u && p->fib_index != fib)
    return 0;
  /*
   * A pool being drained by bpa_pool_del() is already gone as far as new
   * traffic is concerned. Skipped here so the packet is counted as "no
   * pool" - which is the truth - rather than travelling on to be refused
   * by the session bind and counted as a full port block, which is the
   * counter an operator chases when a customer complains.
   */
  if (PREDICT_FALSE (p->deleted))
    return 0;
  /* A NAT64 pool's `inside` is the pseudo set its sub_idx arithmetic runs on,
   * not addresses: no IPv4 source belongs to it (#485). */
  if (PREDICT_FALSE (p->is_nat64))
    return 0;
  return bpa_set_index_of (&p->inside, addr, sub_idx) == 0;
}

/*
 * Does this pool own public address `addr`? A deleted pool still does:
 * its sessions drain, and their replies must still find them.
 */
static_always_inline int
bpa_pool_claims_outside (const bpa_pool_t *p, u32 addr)
{
  u32 idx;
  return bpa_set_index_of (&p->outside, addr, &idx) == 0;
}

/*
 * One "forward" clause's range checks - the ones unformat cannot make, for
 * the CLI and the API alike. Everything else a forward can be refused for -
 * the subscriber, the block, a collision with another forward - needs the
 * pool's derived values and is checked by bpa_pool_validate() once the whole
 * definition has been read. 0 and the entry, or -1 for a port of 0 or past
 * 65535.
 */
static_always_inline int
bpa_static_make (u8 proto, u32 port_pub, u32 ip_priv, u32 port_priv,
		 bpa_static_t *sf)
{
  if (port_pub == 0 || port_pub > 65535 || port_priv == 0
      || port_priv > 65535)
    return -1;
  memset (sf, 0, sizeof (*sf));
  sf->proto = proto;
  sf->ip_priv = ip_priv;
  sf->port_pub = (u16) port_pub;
  sf->port_priv = (u16) port_priv;
  return 0;
}

/*
 * One pool claiming an inside address, offered in pool order to a command
 * that has no packet and so no VRF: which pool is the address's?
 *
 * The first claimant, unless a later one holds the subscriber's live state -
 * where two tenants share an inside prefix, the first claimant is not
 * necessarily the pool the traffic was translated by, and keying on it
 * reported "no live state" over live sessions (#74). Returns 1 when this
 * claimant settles it (it is live); the caller stops there.
 */
static_always_inline int
bpa_claimant_take (int *found, u32 idx, u32 si, int is_live,
		   u32 *pool_index, u32 *sub_idx)
{
  if (!*found || is_live)
    {
      *pool_index = idx;
      *sub_idx = si;
      *found = 1;
    }
  return is_live;
}

/*
 * Would pool `cfg` collide with the existing pool `p`? 0 when not; otherwise
 * 1 with `*addr` the first of cfg's addresses at fault and `*outside` saying
 * which side. The caller words the refusal.
 *
 * Public addresses are compared whatever the VRFs are: out2in keys on the
 * public address alone, so two pools sharing one are two answers to the same
 * question. Inside addresses only within one VRF - or where either pool
 * serves every VRF (~0): the first pool that claims an address answers for
 * it, so the second would translate nothing and say nothing.
 */
static_always_inline int
bpa_pools_collide (const bpa_pool_t *cfg, const bpa_pool_t *p, u32 *addr,
		   int *outside)
{
  u32 i, j;

  for (i = 0; i < cfg->outside.n_ranges; i++)
    for (j = 0; j < p->outside.n_ranges; j++)
      if (bpa_ranges_overlap (&cfg->outside.ranges[i], &p->outside.ranges[j]))
	{
	  *addr = cfg->outside.ranges[i].base;
	  *outside = 1;
	  return 1;
	}

  if (p->fib_index != ~0u && cfg->fib_index != ~0u
      && p->fib_index != cfg->fib_index)
    return 0;

  /* The pseudo inside of a NAT64 pool overlaps every other one by
   * construction; bpa_nat64_collide() compares the IPv6 prefixes instead. */
  if (cfg->is_nat64 || p->is_nat64)
    return 0;

  for (i = 0; i < cfg->inside.n_ranges; i++)
    for (j = 0; j < p->inside.n_ranges; j++)
      if (bpa_ranges_overlap (&cfg->inside.ranges[i], &p->inside.ranges[j]))
	{
	  *addr = cfg->inside.ranges[i].base;
	  *outside = 0;
	  return 1;
	}
  return 0;
}

/*
 * The out2in pre-filter's window: widened by each pool's public ranges in
 * turn, from lo = ~0, hi = 0. A window nothing widened is closed to lo = 1,
 * hi = 0 by bpa_window_close(), which bpa_in_window() answers no to for
 * every address - 0.0.0.0 included, which lo = hi = 0 would have let in.
 */
static_always_inline void
bpa_window_widen (const bpa_addr_set_t *set, u32 *lo, u32 *hi)
{
  u32 i;

  for (i = 0; i < set->n_ranges; i++)
    {
      u32 base = set->ranges[i].base;
      u32 last = base + set->ranges[i].count - 1;

      if (base < *lo)
	*lo = base;
      if (last > *hi)
	*hi = last;
    }
}

static_always_inline void
bpa_window_close (u32 *lo, u32 *hi)
{
  if (*lo == ~0u)
    {
      *lo = 1;
      *hi = 0;
    }
}

/* Is `addr` inside [lo, hi]? The out2in pre-filter's cheap first question;
 * an empty window is lo = 1, hi = 0, and answers no to everything. */
static_always_inline int
bpa_in_window (u32 lo, u32 hi, u32 addr)
{
  return addr >= lo && addr <= hi;
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

/*
 * Does this pool let `addr`, from a packet in VRF `fib`, leave untranslated
 * because its inside prefix-list denies it (#589)? The VRF and the drain are
 * bpa_pool_claims_inside()'s rules, for its reasons: a pool bound to a VRF
 * speaks for that VRF's sources only, and a pool being deleted speaks for
 * nobody new. A NAT64 pool has no IPv4 source to speak for.
 */
static_always_inline int
bpa_pool_passes_untranslated (const bpa_pool_t *p, u32 addr, u32 fib)
{
  u32 idx;

  if (PREDICT_TRUE (p->untranslated.count == 0))
    return 0;
  if (fib != ~0u && p->fib_index != ~0u && p->fib_index != fib)
    return 0;
  if (p->deleted || p->is_nat64)
    return 0;
  return bpa_set_index_of (&p->untranslated, addr, &idx) == 0;
}

/* ------------------------------------------------------------------ */
/* static port forwards                                                */

/*
 * THE ARITHMETIC OVER THE VECTOR, WHICH IS NOT THE VECTOR. Everything from
 * here to the guard below takes `(v, n)` and never a pool, so it compiles
 * standalone against a shim with no VPP vectors and test/test_bpa_static.c
 * drives it. What needs a `bpa_pool_t` - and so a vector - stays behind the
 * guard, which is the line this file already drew, moved to where the
 * dependency actually starts.
 *
 * A forward is configuration laid over the mapping and not part of it, which
 * is why none of this is in ref/bpa_model.py: the model specifies where a
 * subscriber's ports are, and these decide where a configured forward sits
 * in the list of them.
 */

/*
 * Index of the first forward whose public port is >= `port`, or `n` when
 * there is none. The callers below are the whole interface; nothing indexes
 * this vector directly, because its ordering is an implementation detail of
 * exactly this search.
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
 * Where a forward belongs in a vector ordered by (public port, protocol).
 *
 * Two forwards may share both, on different public addresses, and a tie goes
 * AFTER the ones already there. The VTY keeps its own copy of a pool's
 * forwards in the order it read them out of `show bpa config` and renders
 * both sides of a commit diff with a stable sort, so a device that inserted
 * a new tie in front of an older one would show a diff on the following
 * commit that nobody asked for.
 */
static_always_inline u32
bpa_static_position (const bpa_static_t *v, u32 n, const bpa_static_t *sf)
{
  u32 i = bpa_static_lower (v, n, sf->port_pub);

  while (i < n && v[i].port_pub == sf->port_pub && v[i].proto <= sf->proto)
    i++;
  return i;
}

/*
 * Is this the first forward of its subscriber in the sorted vector? The block
 * is the subscriber's own, so that makes it the one forward that stands for
 * the block.
 */
static_always_inline int
bpa_static_first_in_block (const bpa_static_t *v, u32 n,
			   const bpa_static_t *sf)
{
  u32 i;

  for (i = 0; i < n; i++)
    if (v[i].ip_priv == sf->ip_priv)
      return &v[i] == sf;
  return 0;
}

/*
 * Was that the LAST forward its subscriber had? Called after the forward has
 * already been removed from the vector, so finding none of the subscriber's
 * own means the block it pinned is no longer pinned by anything.
 */
static_always_inline int
bpa_static_last_in_block (const bpa_static_t *v, u32 n,
			  const bpa_static_t *sf)
{
  u32 i;

  for (i = 0; i < n; i++)
    if (v[i].ip_priv == sf->ip_priv)
      return 0;
  return 1;
}

/*
 * TWO FORWARDS THAT CANNOT BOTH HOLD, decided here and worded in bpa.c.
 *
 * One public SOCKET serves one subscriber socket, and one subscriber socket
 * is reached through one public socket - both directions have to hold or the
 * translation is ambiguous in one of them. The two clashes are different
 * failures and an operator needs to be told which: the first says somebody
 * else already has that public port on that address, the second says this
 * subscriber socket is already reachable on another one.
 *
 * THE ADDRESS IS PART OF THE PUBLIC KEY. A pool has more than one public
 * address and the same port on two of them is two sockets - keyed on the port
 * alone, one forward made that port unsellable to every subscriber sharing
 * its offset on the other addresses (#214).
 *
 * The decision is separated from the message because the message needs VPP's
 * format() and a clib_error_t, and the decision needs neither: this is what
 * test/test_bpa_static.c drives, and bpa_static_conflict() in bpa.c turns
 * the answer into the sentence an operator reads.
 */
typedef enum
{
  BPA_STATIC_CLASH_NONE = 0,
  BPA_STATIC_CLASH_PUB,	 /* that public socket is already forwarded */
  BPA_STATIC_CLASH_PRIV, /* that subscriber socket already has a forward */
} bpa_static_clash_t;

static_always_inline bpa_static_clash_t
bpa_static_clash (const bpa_static_t *v, u32 n, const bpa_static_t *sf,
		  const bpa_static_t **other)
{
  u32 i;

  for (i = 0; i < n; i++)
    {
      const bpa_static_t *o = &v[i];

      /* A forward pins a port of one protocol; TCP 1100 and UDP 1100 are
       * different sockets and neither says anything about the other. */
      if (o->proto != sf->proto)
	continue;
      if (o->port_pub == sf->port_pub && o->ip_pub == sf->ip_pub)
	{
	  *other = o;
	  return BPA_STATIC_CLASH_PUB;
	}
      if (o->ip_priv == sf->ip_priv && o->port_priv == sf->port_priv)
	{
	  *other = o;
	  return BPA_STATIC_CLASH_PRIV;
	}
    }
  *other = 0;
  return BPA_STATIC_CLASH_NONE;
}

/*
 * THE PROTOCOLS THIS PRODUCT KNOWS, and it lives here rather than in
 * bpa_session.h because the arithmetic below compares one: this header is
 * what a standalone harness includes, and a second copy of these values for
 * the harness to compare against would be a copy that can disagree.
 */
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
  /*
   * IPsec ESP (RFC 4303). Portless like GRE, and unlike GRE there is nothing
   * in it this box may rewrite - the SPI is inside the integrity check. It is
   * here as a protocol the session table knows about so that a tunnel can be
   * bound to a subscriber at all; see bpa_esp.h for what binds it.
   */
  BPA_PROTO_ESP,
  BPA_N_PROTO,
} bpa_proto_t;

/*
 * From here down a pool is needed, and with it VPP's vectors.
 */
#ifndef BPA_STANDALONE

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
		    u16 port_priv, u8 proto, const u8 *host6)
{
  u32 n = vec_len (p->statics);
  u32 i = bpa_static_lower (p->statics, n, b->port_lo);

  for (; i < n && p->statics[i].port_pub <= b->port_hi; i++)
    if (p->statics[i].ip_priv == ip_priv
	&& p->statics[i].port_priv == port_priv
	&& p->statics[i].proto == proto
	/* On a NAT64 pool the subscriber is a prefix: its forward is one
	 * host of it, and another host on the same port is not it (#485). */
	&& (!host6 || !memcmp (p->statics[i].host6, host6, 16)))
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
/* the reserve                                                         */

/*
 * The blocks above blocks_mapped on every public address, presented as their
 * own dense index space [0, reserve_count).
 *
 * Its own space and not a continuation of the subscriber index, because under
 * the blocked policy a block index above blocks_mapped is ANOTHER
 * SUBSCRIBER'S block: bpa_join() multiplies by blocks_mapped, so
 * (pub 0, block blocks_mapped) and (pub 1, block 0) are the same number.
 * Giving the reserve its own index is what keeps the two apart under either
 * policy, and it is the index the return path resolves a reserve port to.
 */

static_always_inline int
bpa_is_reserve_block (const bpa_pool_t *p, u32 blk_idx)
{
  return blk_idx >= p->blocks_mapped && blk_idx < p->blocks_per_ip;
}

/* (public address, block within it) -> dense reserve index. */
static_always_inline u32
bpa_reserve_index (const bpa_pool_t *p, u32 pub_idx, u32 blk_idx)
{
  u32 off = blk_idx - p->blocks_mapped;

  /* Laid out the way the policy lays out subscribers, so consecutive reserve
   * blocks spread across public addresses under interleaved exactly as
   * consecutive subscribers do - which is what makes the worker slices of the
   * reserve line up with the worker slices of the subscribers. */
  if (PREDICT_TRUE (p->policy == BPA_POLICY_INTERLEAVED))
    return off * p->outside.count + pub_idx;
  return pub_idx * p->block_reserve + off;
}

/*
 * dense reserve index -> (public address index, block within it).
 *
 * A plain division and not a reciprocal: this runs when a block is placed or
 * released, which is once per extension, and never per packet. The per-packet
 * direction is bpa_reserve_index() above, which is a multiply and an add.
 */
static_always_inline void
bpa_reserve_split (const bpa_pool_t *p, u32 res_idx, u32 *pub_idx,
		   u32 *blk_idx)
{
  if (PREDICT_TRUE (p->policy == BPA_POLICY_INTERLEAVED))
    {
      *pub_idx = res_idx % p->outside.count;
      *blk_idx = p->blocks_mapped + res_idx / p->outside.count;
    }
  else
    {
      *pub_idx = res_idx / p->block_reserve;
      *blk_idx = p->blocks_mapped + res_idx % p->block_reserve;
    }
}

/* Where a reserve block is, in the shape bpa_forward() answers in. */
static_always_inline int
bpa_reserve_addr (const bpa_pool_t *p, u32 res_idx, bpa_block_t *b)
{
  if (PREDICT_FALSE (res_idx >= p->reserve_count))
    return -1;
  bpa_reserve_split (p, res_idx, &b->pub_idx, &b->blk_idx);
  if (PREDICT_FALSE (bpa_set_addr_of (&p->outside, b->pub_idx, &b->ip_pub)))
    return -1;
  b->port_lo = p->port_min + b->blk_idx * p->block_size;
  b->port_hi = b->port_lo + p->block_size - 1;
  return 0;
}

/*
 * (public address, port) -> reserve index. Returns 0 when the port is in the
 * reserve, -1 when it is not.
 *
 * The question the return path asks about every packet whose port
 * bpa_reverse_sub() refused: a reserve port is resolved by the allocator, and
 * anything else is somebody scanning.
 */
static_always_inline int
bpa_reserve_index_of (const bpa_pool_t *p, u32 ip_pub, u16 port, u32 *res_idx)
{
  u32 pub_idx, blk_idx;

  if (PREDICT_TRUE (p->block_reserve == 0))
    return -1;
  if (PREDICT_FALSE (bpa_set_index_of (&p->outside, ip_pub, &pub_idx)))
    return -1;
  if (PREDICT_FALSE (port < p->port_min || port > p->port_max))
    return -1;

  blk_idx = (u32) (port - p->port_min) >> p->log2_block;
  if (PREDICT_FALSE (!bpa_is_reserve_block (p, blk_idx)))
    return -1;

  *res_idx = bpa_reserve_index (p, pub_idx, blk_idx);
  return 0;
}

/*
 * Which worker may hand this reserve block out.
 *
 * A subscriber is owned by sub_idx % n_workers in both directions, and the
 * return path can read only the reserve index off a packet addressed to a
 * reserve port. So the two meet on one thread only when the block a
 * subscriber is given is congruent to the subscriber, and that is a
 * constraint the allocator has to obey rather than a fact about it: worker w
 * owns the reserve indices where res_idx % n_workers == w, exactly as it owns
 * the dynamic blocks where block_idx % n_workers == w.
 */
static_always_inline u32
bpa_reserve_worker (u32 res_idx, u32 n_workers)
{
  return res_idx % n_workers;
}

/* ------------------------------------------------------------------ */
/* the blocks beyond the first                                         */

/*
 * What "one block further along this public address" is worth in block
 * indices.
 *
 * The block index space is the one bpa_split() divides, so stepping one block
 * along an address means stepping over whatever the policy puts between two
 * blocks of the same address: the whole public set under interleaved, one
 * under blocked. Equal to p->div.d in the interleaved case, and spelled out
 * rather than reused because the two are the same number for different
 * reasons.
 */
static_always_inline u32
bpa_stride_unit (const bpa_pool_t *p)
{
  return p->policy == BPA_POLICY_INTERLEAVED ? p->outside.count : 1;
}

/*
 * Block index of the nth block a subscriber holds after its first, under the
 * pool's stride. Returns 0 on success, -1 when the stride cannot place it.
 *
 * -1 has two causes and the caller treats them alike - it falls back to the
 * free list - but they are worth telling apart when reading a counter. With no
 * stride there is no arithmetic to do. With one, the step ran off the end of
 * the subscriber's own public address, and the block after that one belongs to
 * a different address whose blocks this worker may not own.
 */
static_always_inline int
bpa_stride_block (const bpa_pool_t *p, u32 primary_block, u32 nth, u32 *out)
{
  u32 pub_idx, blk_idx, target;

  if (p->block_stride == 0)
    return -1;

  bpa_split (p, primary_block, &pub_idx, &blk_idx);
  target = blk_idx + nth * p->block_stride;
  if (PREDICT_FALSE (target >= p->blocks_mapped))
    return -1;

  *out = bpa_join (p, pub_idx, target);
  return 0;
}

/*
 * Whether worker sharding lets this pool's stride be honoured at all.
 *
 * A dynamic pool partitions blocks by block_idx % n_workers so that allocation
 * needs no lock, and a subscriber draws only from its own worker's slice -
 * both directions of a flow have to meet on one thread, and out2in resolves
 * the thread from the block index. The nth block under a stride sits exactly
 * nth*stride*unit indices from the first, so it is in the same slice only when
 * that distance is a multiple of the worker count.
 *
 * nth = 1 decides it for every nth: if stride*unit is a multiple of the worker
 * count then so is every multiple of it, and if it is not then no nth makes it
 * one that is congruent - the failure is total rather than partial, which is
 * why this is a refusal at configuration time and not a counter.
 */
static_always_inline int
bpa_stride_reachable (const bpa_pool_t *p, u32 n_workers)
{
  if (p->block_stride == 0 || n_workers <= 1)
    return 1;
  return ((u64) p->block_stride * bpa_stride_unit (p)) % n_workers == 0;
}

/*
 * The smallest stride at or above `want` that the sharding can honour, so a
 * refusal can say what to type instead. Zero when there is none below the
 * blocks one public address holds.
 */
static_always_inline u32
bpa_stride_next_ok (const bpa_pool_t *p, u32 want, u32 n_workers)
{
  u32 unit = bpa_stride_unit (p), s;

  for (s = want ? want : 1; s < p->blocks_mapped; s++)
    if (((u64) s * unit) % n_workers == 0)
      return s;
  return 0;
}

/*
 * How far along a public address one has to step to land back in the same
 * worker's slice: the smallest s with s*unit divisible by n_workers, which is
 * n_workers / gcd(unit, n_workers).
 *
 * This is what separates keeping a subscriber on ONE public address from
 * keeping its blocks at a fixed distance. A stride has to honour the one
 * number the operator typed, so bpa_stride_reachable() is a yes or no about
 * that number; pairing may take any multiple of the step below, so it needs
 * only that a multiple exists - and one always does, because s = n_workers
 * satisfies it for every unit. The two are therefore not the same
 * reachability question, and a pool where no stride is honourable can still
 * be paired.
 *
 * What it does NOT promise is that a candidate is in range: the step still has
 * to fit inside blocks_mapped, which is why the caller walks rather than
 * computing one answer.
 */
static_always_inline u32
bpa_pair_step (const bpa_pool_t *p, u32 n_workers)
{
  u32 a = bpa_stride_unit (p), b = n_workers, t;

  if (n_workers <= 1)
    return 1;
  while (b != 0)
    {
      t = a % b;
      a = b;
      b = t;
    }
  return n_workers / a;
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
  /*
   * blocks_mapped, not blocks_per_ip. A port in the reserve belongs to a
   * subscriber the FORMULA cannot name - only the allocator knows whose it is
   * - so answering here with bpa_join() would hand the packet to whoever the
   * arithmetic happens to produce, which under the blocked policy is a real
   * and different subscriber. Reserve ports are resolved by
   * bpa_reserve_index_of() and the reserve allocator instead.
   */
  if (PREDICT_FALSE (blk_idx >= p->blocks_mapped))
    return -1;

  *sub_idx = bpa_join (p, pub_idx, blk_idx);
  return 0;
}

/*
 * Where the answer to "who was behind this public address and port" comes
 * from - the question a court order or an abuse report asks.
 *
 * A port the formula maps is answered by the formula (deterministic) or by the
 * allocator (dynamic). A port in a deterministic pool's RESERVE is refused by
 * bpa_reverse_sub() - the formula names nobody for it - but it is not
 * "nothing": it belongs to whichever subscriber was handed that reserve block,
 * and the allocation log is the authority for when. `show cgnat translation`
 * answered those ports "nothing was ever translated here", a false negative
 * given with confidence on the one question this product exists to answer
 * (#483). `*idx` is the block index, or the reserve index for RESERVE.
 */
typedef enum
{
  BPA_XLT_NONE = 0, /* in no block: nothing was ever translated here */
  BPA_XLT_FORMULA,  /* deterministic: the formula answers, for any instant */
  BPA_XLT_RESERVE,  /* a deterministic pool's reserve: the allocator, now */
  BPA_XLT_DYNAMIC,  /* dynamic: the allocator answers, for now only */
} bpa_xlt_source_t;

static_always_inline bpa_xlt_source_t
bpa_translation_source (const bpa_pool_t *p, u32 ip_pub, u16 port, u32 *idx)
{
  if (bpa_reverse_sub (p, ip_pub, port, idx) == 0)
    return p->mode == BPA_MODE_DETERMINISTIC ? BPA_XLT_FORMULA
					     : BPA_XLT_DYNAMIC;
  if (bpa_reserve_index_of (p, ip_pub, port, idx) == 0)
    return BPA_XLT_RESERVE;
  return BPA_XLT_NONE;
}

/*
 * outside -> inside. Arithmetic plus two binary searches over small arrays,
 * with no session lookup at all. This is why the deterministic mode needs
 * no allocation logging for its mapped blocks and why the return path shards
 * cleanly. A reserve port is the one case this cannot answer, and it says so
 * rather than guessing: see bpa_reserve_index_of().
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

/*
 * WHETHER A FORWARD CAN BE MADE AT ALL, decided here and worded in bpa.c.
 *
 * Every refusal below is a different thing for an operator to do, which is
 * why they are an enum and not a boolean: a protocol that cannot carry a
 * forward, a port that is not a port, a public port outside the pool, a
 * subscriber the pool's arithmetic reaches no block for, and a public port
 * that belongs to somebody else's block.
 *
 * THE BLOCK IS DERIVED AND NEVER NAMED. In deterministic mode it is the
 * formula's answer; in dynamic mode the forward is what makes the block stop
 * moving. Letting an operator name one instead would have broken the return
 * path, the handoff and the session open - all three ask bpa_forward() for
 * the same block this asks for.
 *
 * On BPA_STATIC_OK - and only then - `out` carries the two derived fields a
 * forward is stored with: the public address, and the offset of the port
 * inside the block, which is the bit the port map reserves.
 */
typedef enum
{
  BPA_STATIC_OK = 0,
  BPA_STATIC_BAD_PROTO,	   /* not TCP and not UDP */
  BPA_STATIC_BAD_PORT,	   /* port 0, on either side */
  BPA_STATIC_PORT_OUTSIDE, /* the public port is not one this pool translates */
  BPA_STATIC_NO_BLOCK,	   /* the pool reaches no block for this subscriber */
  BPA_STATIC_WRONG_BLOCK,  /* the public port is in another subscriber's */
} bpa_static_check_t;

static_always_inline bpa_static_check_t
bpa_static_check (const bpa_pool_t *p, const bpa_static_t *sf,
		  bpa_block_t *blk, bpa_static_t *out)
{
  if (sf->proto != BPA_PROTO_TCP && sf->proto != BPA_PROTO_UDP)
    return BPA_STATIC_BAD_PROTO;
  if (sf->port_pub == 0 || sf->port_priv == 0)
    return BPA_STATIC_BAD_PORT;
  if (sf->port_pub < p->port_min || sf->port_pub > p->port_max)
    return BPA_STATIC_PORT_OUTSIDE;
  if (bpa_forward (p, sf->ip_priv, blk))
    return BPA_STATIC_NO_BLOCK;
  if (sf->port_pub < blk->port_lo || sf->port_pub > blk->port_hi)
    return BPA_STATIC_WRONG_BLOCK;

  *out = *sf;
  out->ip_pub = blk->ip_pub;
  out->port_off = (u16) (sf->port_pub - blk->port_lo);
  return BPA_STATIC_OK;
}

/*
 * A candidate pool's forwards, each judged against the pool and against the
 * ones before it, and each COMPLETED in place: bpa_static_check() derives
 * ip_pub and port_off, and nothing else does. Validating a copy and keeping
 * the original once left every forward given on a "bpa pool" line with a
 * zero public address, which reserves nothing and translates nothing - a
 * forward the configuration shows and the dataplane does not have.
 *
 * Returns ~0 when all of them hold, or the index of the first that does not,
 * left as it was given; those before it are completed. The caller words the
 * refusal for that one.
 */
static_always_inline u32
bpa_statics_complete (const bpa_pool_t *p, bpa_static_t *in, u32 n)
{
  u32 i;

  for (i = 0; i < n; i++)
    {
      bpa_static_t checked;
      bpa_block_t blk;
      const bpa_static_t *o = 0;

      memset (&checked, 0, sizeof (checked));
      memset (&blk, 0, sizeof (blk));
      if (bpa_static_check (p, &in[i], &blk, &checked) != BPA_STATIC_OK
	  || bpa_static_clash (in, i, &checked, &o) != BPA_STATIC_CLASH_NONE)
	return i;
      in[i] = checked;
    }
  return ~0u;
}

/*
 * IS THE STRIDE THIS OPERATOR TYPED REACHABLE AT `nw` WORKERS, and if not,
 * the nearest one that is.
 *
 * The worker count comes in already floored at 1: a worker-less box is never
 * short, because bpa_stride_reachable() answers yes at one worker, and the
 * floor belongs to the one place that reads how many workers this box has -
 * bpa.c - rather than to each of the three callers that ask this question.
 *
 * AND NO TEST OF THE MODE. A pool that has a stride at all is a dynamic
 * pool: bpa_pool_init() and bpa_pool_settings_set() are the only two ways a
 * stride is stored and both refuse it on a deterministic pool through the
 * one refusal they share (#375). A mode test here would be a second owner of
 * that rule and could never be shown failing.
 */
static_always_inline int
bpa_stride_short_at (const bpa_pool_t *p, u32 nw, u32 *okp)
{
  *okp = 0;
  if (p->block_stride == 0 || bpa_stride_reachable (p, nw))
    return 0;
  *okp = bpa_stride_next_ok (p, p->block_stride, nw);
  return 1;
}

/*
 * HOW MANY OF THIS BOX'S WORKERS THE RESERVE DOES NOT REACH, and 0 when it
 * reaches all of them.
 *
 * The same floor and the same reason: a worker-less box compares one against
 * one and is never short. A reserve of zero is not a short reserve - it is a
 * pool that asked for none - which is the case a subtraction alone would get
 * wrong.
 */
static_always_inline u32
bpa_reserve_short_at (const bpa_pool_t *p, u32 nw)
{
  if (p->block_reserve == 0 || p->reserve_count >= nw)
    return 0;
  return nw - p->reserve_count;
}

/*
 * WHICH FORWARD AN OPERATOR MEANT, when they named a protocol and a public
 * port and not a subscriber.
 *
 * A pool has more than one public address, so the same port can be forwarded
 * on several of them - to several subscribers. Removing "the" forward on that
 * port is then a question with more than one answer, and answering it with
 * the first would end a customer's inbound service because another customer
 * shares the port's offset.
 *
 * So this counts them. One match is the operator's forward and is handed
 * back; more than one is a refusal, and the caller words it with the first
 * two subscribers named - which is why both are returned rather than only
 * the count. Zero is not an error here: the caller that has no forward to
 * remove says so in its own words.
 *
 * The scan starts at bpa_static_lower() and stops at the first row with
 * another public port, so it reads the run of one port and not the vector.
 */
static_always_inline u32
bpa_static_on_port (const bpa_static_t *v, u32 n, u16 port_pub, u8 proto,
		    const bpa_static_t **first, const bpa_static_t **second)
{
  u32 i = bpa_static_lower (v, n, port_pub);
  u32 n_match = 0;

  *first = 0;
  *second = 0;
  for (; i < n && v[i].port_pub == port_pub; i++)
    {
      if (v[i].proto != proto)
	continue;
      n_match++;
      if (!*first)
	*first = &v[i];
      else if (!*second)
	*second = &v[i];
    }
  return n_match;
}

/*
 * HOW MANY SUBSCRIBERS A NEW DEFINITION OF THIS POOL WOULD MOVE.
 *
 * The one question an operator needs answered before committing a change to
 * a pool that is carrying traffic: this is deterministic CGNAT, so a
 * subscriber's public address and port block are a FUNCTION of the pool's
 * definition - change the definition and some of them are somewhere else,
 * with every session they hold dropped and every log line before the change
 * pointing at an address that is now somebody else's.
 *
 * `example` takes the first `max_examples` addresses that moved, so the
 * warning can show an operator a few of them rather than only a count. It is
 * the caller's array and is written only up to that bound.
 *
 * AN ADDRESS THE NEW DEFINITION DOES NOT SERVE AT ALL has moved as surely as
 * one that moved somewhere else: a subscriber who falls outside the new
 * inside set has no mapping, which is the same outage from the subscriber's
 * side and a worse one from the operator's, because nothing will log it.
 */
static_always_inline u32
bpa_pool_remap_count (const bpa_pool_t *now, const bpa_pool_t *cfg,
		      u32 *n_total, u32 *example, u32 max_examples)
{
  u32 moved = 0, i;
  u32 usable = bpa_pool_usable (now);

  *n_total = usable;
  for (i = 0; i < usable; i++)
    {
      bpa_block_t a, b;
      u32 addr;

      if (bpa_set_addr_of (&now->inside, i, &addr))
	continue;
      if (bpa_forward (now, addr, &a))
	continue;
      if (bpa_forward (cfg, addr, &b) || a.ip_pub != b.ip_pub
	  || a.port_lo != b.port_lo)
	{
	  if (moved < max_examples)
	    example[moved] = addr;
	  moved++;
	}
    }
  return moved;
}

#endif /* __included_bpa_pool_h__ */
