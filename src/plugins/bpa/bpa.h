/* bpa.h - CGNAT with allocated port blocks (BPA). */

#ifndef __included_bpa_h__
#define __included_bpa_h__

#include <vlib/vlib.h>
#include <vnet/vnet.h>
#include <vnet/ip/ip.h>
#include <vnet/feature/feature.h>
#include <vnet/fib/ip4_fib.h>
#include <vnet/dpo/load_balance.h>

#include "bpa_identity.h"
#include "bpa_local.h"
#include "bpa_pool.h"
#include "bpa_workers.h"
#include "bpa_ports.h"
#include "bpa_session.h"
#include "bpa_auto_mss.h"

typedef struct
{
  bpa_pool_t *pools;   /* pool vector, indexed by pool id */
  uword *pool_by_name; /* name -> pool index */

  bpa_per_thread_t *per_thread; /* one per vlib worker */
  bpa_timeouts_t timeouts;
  u32 block_holddown; /* seconds a block stays reserved after going idle */
  /*
   * The lowest and highest public address across ALL pools, in host order.
   *
   * A hairpin pre-filter, and nothing else. Every translated packet has to ask
   * "is this destination one of ours", and answering it properly means a scan
   * over pools each doing a binary search - paid on every packet, for a case
   * that is rare. Two compares against this window answer "definitely not"
   * for almost all traffic; anything inside it still goes through the real
   * lookup, so the filter can never hide a hairpin. Recomputed whenever a pool
   * is added or removed.
   */
  u32 outside_lo;
  u32 outside_hi;

  /*
   * The MSS a pool set to "auto" clamps to: the smallest IPv4 MTU among the
   * interfaces this CGNAT runs on, less an IP and a TCP header.
   *
   * The SMALLEST, not the egress one. A segment governed by a clamped MSS
   * crosses both an inside and an outside interface of this box, and the node
   * that clamps runs before the forwarding lookup, so which interface it will
   * leave by is not yet known. Taking the minimum is the conservative answer
   * and the only one available at that point; a clamp that is smaller than it
   * needs to be costs some throughput, and one that is larger protects
   * nothing.
   *
   * Maintained as interfaces join and leave, and reconciled once a second by
   * the expiry process, because an MTU can change under a running box.
   *
   * MAINTAINED and not recomputed. It used to be recomputed, by walking every
   * interface on the box and asking the feature arc four times per interface
   * whether the CGNAT was on it, on every attach. That made attaching N
   * interfaces cost N walks: 8.3 ms per attach at 10,000 interfaces, 83
   * seconds to attach them all, where `policer input` on the same arc costs
   * 42 us (#287). bpa_auto_mss.h holds the bookkeeping that replaced it.
   */
  u16 auto_mss;

  /*
   * The minimum above, and the per-interface MTUs it is the minimum over.
   *
   * `auto_mss_mtu` is indexed by sw_if_index and is zero for every interface
   * the CGNAT is not attached to, so it is also the only record of WHICH
   * interfaces those are. It is written from what the feature arc says about
   * the one interface being changed, never from a guess, so a toggle the arc
   * refused cannot leave this disagreeing with it.
   */
  bpa_auto_mss_t auto_mss_track;
  u32 *auto_mss_mtu;

  u32 hash_buckets;   /* session table sizing */
  u32 hash_memory;

  /*
   * The one table in this plugin that is shared across workers. Both of the
   * things it holds are packed by bpa_shared_value(): whom to give the packet
   * to, and which VRF to give it back into.
   *
   *   (public address, peer address)
   *     -> the subscriber running an IPsec tunnel to that peer
   *   (public address, the SERVER's Call ID, server address)
   *     -> the subscriber running a PPTP call the server assigned that ID
   *
   * bpa_esp.h says at length why the first has to be shared, and the argument
   * is about a packet carrying no identifier this box can steer on rather
   * than about ESP: the arithmetic that sends every other reply to the worker
   * holding the subscriber cannot run on it.
   *
   * The second is here for the same reason one step removed (#203). PPTP data
   * IS steerable - inbound GRE carries the Call ID this box handed out, drawn
   * from the subscriber's port block - but an ICMP error about an OUTBOUND
   * GRE packet quotes the Call ID the SERVER chose, which is in no block, so
   * bpa_reverse_sub() cannot recover an owner from it and no worker can be
   * picked. The packet is therefore answered wherever it lands.
   *
   * A bihash is multi-writer by construction, so the price is a bucket lock
   * paid by ESP packets and by PPTP call setup and teardown - not by PPTP
   * data, which never reads this.
   */
  clib_bihash_16_8_t shared;

  /* handoff */
  u32 in2out_fq_index;
  u32 out2in_fq_index;
  u32 nat64_in2out_fq_index; /* #485 */
  u32 n_nat64;		     /* NAT64 pools, for the ip6 features */
  u32 n_workers;
  bpa_divisor_t workers; /* spreads subscribers over the workers */
  /*
   * WHICH WORKERS TRANSLATE (#466). n_workers above counts the workers that
   * own subscribers - every VPP worker, unless `bpa { cgnat-workers <list> }`
   * in startup.conf names fewer. The rest only take packets off the cards
   * and hand them to the owner, which is the split the maintainer asked to
   * be able to benchmark against every worker doing both.
   *
   * owner_wid[w] is the VPP worker id of BPA worker w; thread_w[thread] is
   * the BPA worker a thread is, or ~0 for the main thread and for a worker
   * that only receives. Everything that turns one into the other goes
   * through bpa_worker_id() and bpa_sub_thread() below.
   */
  u32 *owner_wid;
  u32 *thread_w;

  /* The box's own interface addresses, as bpa_local.h's set: the filter
   * bpa_to_us() asks before the FIB (#489). Rebuilt on the main thread,
   * under the barrier, whenever an interface gains or loses one. */
  u32 *local_addrs;

  /*
   * WHO IS HOLDING A BLOCK THAT WAS LENT (#210), keyed by pool and block.
   *
   * The return path steers on arithmetic: bpa_reverse_sub() turns (public
   * address, port) into the block's own index, and a dynamic block is owned
   * by the worker that index is congruent to - which is exactly why a
   * subscriber may only be given a block congruent to itself. Lending breaks
   * that on purpose: the block stays the lender's by arithmetic and the
   * SUBSCRIBER holding it is on the borrower, so a reply steered by the
   * formula arrives at a worker whose session table was never going to hold
   * it.
   *
   * So the loan is written down where the return path can read it. Written
   * only under the barrier, by the rebalance; read on the handoff, which is
   * the last place a packet can still be sent somewhere else.
   *
   * n_lent_total IS THE FAST PATH. Every box that has never exhausted a
   * slice has none of these, and pays one load and a comparison against zero
   * - not a hash lookup - for the whole mechanism.
   */
  uword *lent_to;    /* (pool_index << 32 | block_idx) -> worker + 1 */
  u32 n_lent_total;  /* how many entries lent_to holds */
  /*
   * AND THE MECHANISM IS OFF UNTIL SOMEBODY TURNS IT ON (#210).
   *
   * Moving a block works - a refused subscriber is served one tick later
   * from a block another worker lent, measured in lab/dynamic.sh - and
   * every setting that lends eagerly enough to do that also lends a block
   * the lender's own next subscriber wants: the same lab's three-block pool
   * went from 3 allocations and 1 refusal to 2 and 2.
   *
   * Which side of that an operator wants is a decision about their traffic,
   * not one this file can take. So the mechanism ships complete and idle:
   * `bpa rebalance enable` turns it on, the lab turns it on to measure it,
   * and a box nobody has told stays exactly as it was.
   */
  u8 rebalance_on;

  vlib_log_class_t log_class; /* shows up in "show logging" */
  u8 *audit_path;	      /* dated configuration trail */

  /*
   * Who asked for the change being applied right now.
   *
   * Set by the API handler from what the client sent, immediately before the
   * change is made, and cleared immediately after - so an entry can only ever
   * be attributed to the request that produced it. A change that arrives by
   * any other route finds this empty and is recorded as unattributed, which
   * is the honest answer and a visible one: an entry that silently omitted
   * the field would be indistinguishable from one where nobody was
   * accountable (issue #34).
   *
   * A single slot rather than one per thread: API handlers run on the main
   * thread under the worker barrier, one at a time.
   */
  /*
   * The hash chain over the trail (issue #22).
   *
   * `audit_head` is the digest of the last entry written, `audit_seq` its
   * sequence number. Both are recovered from the file at startup, so a
   * restart continues the chain rather than starting a second one.
   *
   * They are also what makes TRUNCATION detectable at all. Removing entries
   * from the END of the file leaves a chain that verifies perfectly - every
   * remaining link is genuine - so a file cannot report its own truncation.
   * Only something that remembers where the chain had got to can, which is
   * what these two fields are for.
   */
  u8 audit_head[32];
  u64 audit_seq;

  /*
   * Entries this dataplane could not write, and would otherwise have lost in
   * silence (#236).
   *
   * `audit_head` and `audit_seq` advance only when the entry reached the file,
   * so what is in the file stays a valid chain: a lost entry must not make the
   * NEXT one read as tampering. But a chain that is quietly short is worse
   * than one that is loudly broken, and the file cannot carry the news of an
   * entry that could not be written to it. This counter is the only place that
   * news exists, so `show bpa pool history verify` and the boot self-test read
   * it and say the evidence is incomplete.
   *
   * It is lost on a restart, deliberately. Reconstructing it would mean
   * writing it somewhere, and the thing that has just failed is writing.
   */
  u64 audit_lost;

  u8 *actor_user;  /* the account the client named, or 0 */
  u32 actor_uid;   /* the uid it was running as, or ~0 */
  u8 actor_is_api; /* the change arrived over the binary API */

  /*
   * When THIS incarnation of the dataplane started, on the wall clock.
   *
   * Latched once, in bpa_init(), and never recomputed: its job is to CHANGE
   * when the process is replaced, and a value recomputed from the current
   * clock would jump every time NTP steps and look to a collector like a
   * restart that did not happen. The cost of latching is that a box whose
   * clock was wrong before NTP settled carries a wrong absolute value for the
   * life of the process - it still differs from the previous incarnation's,
   * which is what a collector reads it for.
   */
  f64 boot_wall;

  /*
   * When the box was last asked to release everything it holds because it is
   * about to stop, or 0. See bpa_stop_drain() and the re-announcement in
   * bpa-expire-process: a box that is still here a few seconds later did not
   * stop, and the log must not go on claiming those blocks are free.
   */
  f64 drained_at;

  u16 msg_id_base;
  vlib_main_t *vlib_main;
  vnet_main_t *vnet_main;
  /*
   * Who held a private address, for the log (#21). Written whole by the
   * control plane and read on the allocation path; see bpa_identity.c for why
   * it is a sorted array and for the rule that keeps it off every screen.
   */
  bpa_identity_table_t identity;
} bpa_main_t;

extern bpa_main_t bpa_main;
extern vlib_node_registration_t bpa_in2out_node;
extern vlib_node_registration_t bpa_out2in_node;
extern vlib_node_registration_t bpa_in2out_handoff_node;
extern vlib_node_registration_t bpa_out2in_handoff_node;
/* `bpa { ip6-mcast-follow off }`: bpa_ip6_bind.c's repair not made (#594). */
extern u8 bpa_ip6_mcast_follow_off;

/* Queues a log event from the dataplane. No lock: the queue is per-thread. */
/*
 * The loan book (#210). Written under the barrier by the rebalance, read on
 * the handoff by every worker.
 *
 * A key is the pool and the block, because a block index means nothing
 * without the pool it belongs to, and two pools' blocks share an index space
 * that starts at zero.
 */
static_always_inline u64
bpa_lent_key (u32 pool_index, u32 block_idx)
{
  return ((u64) pool_index << 32) | block_idx;
}

void bpa_lent_set (bpa_main_t *bm, u32 pool_index, u32 block_idx, u32 worker);
void bpa_lent_clear (bpa_main_t *bm, u32 pool_index, u32 block_idx);
/* Every loan of one pool given up at once, for a pool being replaced. */
void bpa_lent_clear_pool (bpa_main_t *bm, u32 pool_index);

/*
 * Moves blocks between workers' slices, once a tick, under the barrier
 * bpa_expire_process already takes and after the reap (#210).
 */
void bpa_rebalance_tick (bpa_main_t *bm);

/*
 * Which worker holds this block now, or -1 when nobody borrowed it.
 *
 * The caller checks n_lent_total first: this is the slow half, and on a box
 * with no loans it is never reached.
 */
static_always_inline int
bpa_lent_worker (const bpa_main_t *bm, u32 pool_index, u32 block_idx,
		 u32 *worker)
{
  uword *q = hash_get (bm->lent_to, bpa_lent_key (pool_index, block_idx));

  if (PREDICT_TRUE (q == 0))
    return -1;
  *worker = (u32) (q[0] - 1);
  return 0;
}

void bpa_log_queue_event (bpa_per_thread_t *pt, u8 event, u32 ip_priv,
			  u32 ip_pub, u16 port_lo, u16 port_hi, u32 pool_id);

/*
 * Re-announces every pinned block as an allocation, when the log is switched
 * on. A pin is made once and then holds a block for as long as the forward
 * exists, and a configuration replay pins before it enables the export - so
 * without this a rebooted box holds pinned blocks with no record of who has
 * them. Takes the barrier itself.
 */
void bpa_log_pins_replay (void);

/*
 * Queues one event per block currently held, over every pool and every
 * thread. MUST be called under the worker barrier: it reads every thread's
 * allocator and writes to every thread's log queue.
 *
 * The event is a parameter because the two callers are opposites and must
 * walk exactly the same set of blocks: the stop drain gives them all back
 * (natEvent 17), and the re-announcement after a drain that was not followed
 * by a stop takes them all back (natEvent 16). Two walks that could drift
 * apart would leave the log with a release the box never re-opened.
 */
u32 bpa_blocks_log_all (u8 event);

/*
 * Releases every block held, as natEvent 17, and puts the records on the wire
 * in the same call. Returns how many. Takes the worker barrier itself.
 *
 * Two callers, and the difference between them is whether the graph is still
 * running: the VLIB_MAIN_LOOP_EXIT hook in bpa.c, and the "bpa stop-drain"
 * command an ExecStop runs while it still is. bpa.c carries the measurement
 * that made it two.
 */
u32 bpa_drain_blocks (vlib_main_t *vm);

/*
 * How long after a drain the box waits before deciding it was not stopped
 * after all, and takes the blocks back with a fresh natEvent 16.
 *
 * Longer than the gap between an ExecStop returning and systemd's SIGTERM,
 * which is immediate, so a real stop never reaches this. Short enough that a
 * drain typed by hand on a live box leaves the log claiming those blocks are
 * free for seconds rather than for the year it is kept.
 */
#define BPA_DRAIN_REANNOUNCE 5.0

/* Adds a pinned block to a subscriber's live record on one worker, if it has
 * one and does not already carry the block. Under the barrier. */
void bpa_sub_adopt_block (bpa_per_thread_t *pt, const bpa_pool_t *p,
			  u32 pool_index, const bpa_block_t *b);

/*
 * Who is asking, for the duration of one API request.
 *
 * Set before the change and cleared after it, so that an entry can only carry
 * the identity of the request that produced it - not of whichever request
 * happened to run last. `user` may be empty and `uid` may be ~0; both absent
 * is recorded as unattributed rather than left off the line.
 */
void bpa_actor_set (const char *user, u32 uid);
void bpa_actor_clear (void);

/*
 * Recover the hash chain's head from the trail on disk, at startup. A restart
 * that did not do this would begin a second chain, and every verification
 * afterwards would report a break at the restart.
 */
void bpa_audit_recover (void);

void bpa_audit (const char *op, bpa_pool_t *p, u8 *name_only);
void bpa_audit_interface (const char *op, u8 *ifname, int is_inside);

clib_error_t *bpa_pool_validate (bpa_pool_t *cfg);
/*
 * How many of a running pool's subscribers a proposed definition would move,
 * with up to `max_examples` of their addresses, is bpa_pool_remap_count() in
 * bpa_pool.h - it is pool arithmetic and nothing else, so it lives with the
 * arithmetic and test/test_bpa_pool.c drives it. Both pools MUST be through
 * bpa_pool_init().
 */
/*
 * One static port forward, added to or removed from a pool that already
 * exists. `is_check` validates and changes nothing.
 *
 * Separate from bpa_pool_add() for the reason "bpa filter" and "bpa alg" are:
 * recreating a pool to add a forward would drop every session it holds, and an
 * operator adding a forward for one customer must not disconnect the rest.
 */
clib_error_t *bpa_static_add_del (u8 *name, bpa_static_t *sf, int is_add,
				  int is_check);
/*
 * A pool's per-subscriber bounds, changed without recreating it. Zero in
 * either is unlimited.
 */
clib_error_t *bpa_limits_set (u8 *name, u32 max_sessions, u32 max_rate);
/*
 * The per-pool settings that can change without recreating the pool: the
 * filtering mode, the block cap, the block stride, the ALG switches, the MSS
 * clamp and the lifetimes. Everything that changes the MAPPING is
 * deliberately absent.
 */
/* The workers that own subscribers (#466); see the definition. */
void bpa_set_owners (bpa_main_t *bm, u32 *wids);
clib_error_t *bpa_pool_settings_set (u8 *name, bpa_filter_t filter,
				     u8 max_blocks, u8 initial_blocks,
				     u32 alg, u32 alg_set,
				     u16 tcp_mss, u8 tcp_mss_auto,
				     u8 util_alarm, u16 block_stride,
				     const bpa_timeouts_t *timeouts);
/*
 * Replaces one of a pool's bypass lists, entire. An empty list removes the
 * bypass; see the definition for why it is not per prefix.
 */
/*
 * Replaces a pool's prefix-list names, its untranslated sources and its
 * excluded public addresses, entire (#589): the first `n_untranslated` of
 * the ranges are the sources, the rest the excluded addresses.
 */
clib_error_t *bpa_pool_lists_set (u8 *name, const char *inside_list,
				  const char *outside_list, const u32 *bases,
				  const u32 *counts, u32 n_untranslated, u32 n);
clib_error_t *bpa_bypass_set (u8 *name, int is_dst, const u32 *bases,
			      const u32 *counts, u32 n);
clib_error_t *bpa_pool_add (bpa_pool_t *cfg, u32 *pool_index);
clib_error_t *bpa_pool_del (u8 *name);
void bpa_outside_window_update (void);
/*
 * Re-reads the MTU of every interface the CGNAT is attached to, and drops the
 * ones that have been deleted. Called once a second by the expiry process:
 * nothing in VPP tells this plugin that an MTU changed, and a clamp derived
 * from an MTU that has moved is describing a path the box is no longer on.
 *
 * Proportional to the number of CGNAT interfaces, NOT to the number of
 * interfaces on the box, and not on the attach path at all.
 */
void bpa_auto_mss_update (void);
/*
 * Records what ONE interface contributes to the "auto" clamp, reading the
 * feature arc for that interface only. Constant work, which is the whole of
 * #287: this is what an attach calls.
 */
void bpa_auto_mss_interface_update (u32 sw_if_index);
/*
 * Attaches CGNAT to an interface, or takes it off. Returns what
 * vnet_feature_enable_disable() returned, and does nothing else when that
 * failed.
 *
 * EVERY caller MUST come through here rather than toggle the feature itself:
 * the toggle is only the first of the things an attach has to do, and the
 * rest of them are silent when they are missing (#183).
 */
int bpa_interface_feature_enable_disable (u32 sw_if_index, int is_inside,
					  int enable);
/*
 * Warns about every timeout below the floor its RFC sets. `vm` may be null,
 * and then only the log hears it. Called where a timeout CHANGES, never on the
 * datapath.
 */
void bpa_timeouts_warn (vlib_main_t *vm, const bpa_timeouts_t *t, u8 *pool);
void bpa_stride_warn (vlib_main_t *vm, const bpa_pool_t *p);
/*
 * The stride warning WITHOUT the log record: the rows an operator reads, for a
 * caller that is answering somebody who is looking rather than reporting an
 * event. `show bpa pool` is one - it is the readback prismash relays to an
 * operator who applied the pool over the API - and so is `bpa pool ... check`,
 * which creates nothing to write a record about. The record for a stride that
 * IS applied is written once, by bpa_pool_add() or bpa_pool_settings_set().
 */
void bpa_stride_say (vlib_main_t *vm, const bpa_pool_t *p);
void bpa_reserve_warn (vlib_main_t *vm, const bpa_pool_t *p);
/*
 * The reserve warning WITHOUT the log record: the rows an operator reads, for
 * a caller that is answering somebody who is looking rather than reporting an
 * event. `show bpa pool` is one - it is the readback prismash relays to an
 * operator who applied the pool over the API - and so is `bpa pool ... check`,
 * which creates nothing to write a record about. The record for a pool that
 * IS created is written once, by bpa_pool_add().
 */
void bpa_reserve_say (vlib_main_t *vm, const bpa_pool_t *p);

format_function_t format_bpa_pool;
format_function_t format_bpa_policy;
format_function_t format_bpa_mode;
format_function_t format_bpa_mode_word;
format_function_t format_bpa_alg;
format_function_t format_bpa_proto;
format_function_t format_bpa_alg;

/*
 * Pool selection. A single box serves one provider, so pools are few and a
 * linear scan is cheaper than a lookup structure. If a deployment ever grows
 * many pools this becomes an mtrie.
 */
/* not a unit: it walks VPP's pool of pools; the rule for one pool is
 * bpa_pool_claims_inside() in bpa_pool.h, held by test/test_bpa_pool.c
 * (#472) */
static_always_inline int
bpa_pool_by_inside (bpa_main_t *bm, u32 addr, u32 fib, u32 *pool_index,
		    u32 *sub_idx)
{
  bpa_pool_t *p;

  pool_foreach (p, bm->pools)
    {
      if (bpa_pool_claims_inside (p, addr, fib, sub_idx))
	{
	  *pool_index = p - bm->pools;
	  return 0;
	}
    }
  return -1;
}

/*
 * Which pool, if any, lets `addr` leave untranslated because its inside
 * prefix-list denies it (#589). Asked only once bpa_pool_by_inside() has
 * found no pool: a source a pool translates is that pool's, and the VTY
 * refuses a deny another pool's inside covers, so the order never decides
 * anything a configuration said otherwise.
 */
/* not a unit: it walks VPP's pool of pools; the rule for one pool is
 * bpa_pool_passes_untranslated() in bpa_pool.h, held by test/test_bpa_pool.c
 * (#589) */
static_always_inline int
bpa_pool_by_untranslated (bpa_main_t *bm, u32 addr, u32 fib, u32 *pool_index)
{
  bpa_pool_t *p;

  pool_foreach (p, bm->pools)
    {
      if (bpa_pool_passes_untranslated (p, addr, fib))
	{
	  *pool_index = p - bm->pools;
	  return 0;
	}
    }
  return -1;
}

/*
 * Could this address possibly be one of ours? Two compares.
 *
 * A superset test on purpose: it says "definitely not" or "maybe", never
 * "yes". Everything it lets through is checked properly by
 * bpa_pool_by_outside(), so a hairpin cannot be missed - only a non-hairpin
 * can be dismissed early, which is the whole point.
 */
/* not a unit: it reads bpa_main_t, VPP's; the rule is bpa_in_window() in
 * bpa_pool.h, held by test/test_bpa_pool.c (#472) */
static_always_inline int
bpa_maybe_outside (const bpa_main_t *bm, u32 addr)
{
  return bpa_in_window (bm->outside_lo, bm->outside_hi, addr);
}

/* not a unit: it walks VPP's pool of pools; the rule for one pool is
 * bpa_pool_claims_outside() in bpa_pool.h, held by test/test_bpa_pool.c
 * (#472) */
static_always_inline int
bpa_pool_by_outside (bpa_main_t *bm, u32 addr, u32 *pool_index)
{
  bpa_pool_t *p;

  pool_foreach (p, bm->pools)
    {
      if (bpa_pool_claims_outside (p, addr))
	{
	  *pool_index = p - bm->pools;
	  return 0;
	}
    }
  return -1;
}

/*
 * This worker's identity for block sharding. With no workers configured
 * everything runs on thread 0 and behaves as a single slice.
 */
/* not a unit: it reads bpa_main_t, VPP's; the rule is bpa_thread_worker()
 * in bpa_workers.h, which test/test_bpa_workers.c holds (#472) */
static_always_inline void
bpa_worker_id (const bpa_main_t *bm, u32 thread_index, u32 *w, u32 *nw)
{
  bpa_thread_worker (bm->n_workers, bm->thread_w, thread_index, w, nw);
}

/*
 * The inverse: which thread holds a subscriber's state.
 *
 * In bpa.h rather than beside its first caller because the configuration path
 * now needs it too - pinning a block reaches into the allocator of exactly one
 * worker, and a pin applied to the wrong one is a block held where no packet
 * of that subscriber will ever look.
 */
/* not a unit: it reads bpa_main_t, VPP's; the rule is bpa_owner_thread()
 * in bpa_workers.h, which test/test_bpa_workers.c holds (#472) */
static_always_inline u32
bpa_sub_thread (const bpa_main_t *bm, u32 sub_idx)
{
  return bpa_owner_thread (bm->n_workers, bm->owner_wid, &bm->workers,
			   sub_idx);
}

/* IP protocol number -> bpa_proto_t, or -1 when we do not translate it. */
static_always_inline int
bpa_proto_of (u8 ip_proto)
{
  switch (ip_proto)
    {
    case IP_PROTOCOL_TCP:
      return BPA_PROTO_TCP;
    case IP_PROTOCOL_UDP:
      return BPA_PROTO_UDP;
    case IP_PROTOCOL_ICMP:
      return BPA_PROTO_ICMP;
    case IP_PROTOCOL_GRE:
      return BPA_PROTO_GRE;
    case IP_PROTOCOL_IPSEC_ESP:
      return BPA_PROTO_ESP;
    default:
      return -1;
    }
}

/*
 * Where a packet goes after this node.
 *
 * NOT vnet_feature_next on a box with workers, and the difference was the
 * whole product: with workers the node on the ip4-unicast arc is the HANDOFF
 * node, not this one. The feature machinery stores each chain entry's next as
 * an index into the next-node list of the node that consumes it - the handoff
 * node - and this node then applied that index to its own list, whose only
 * member was error-drop. Every packet was translated, counted as translated,
 * and blackholed. `prisma-routing-tune` configures workers on every appliance, so
 * that was the shipped configuration; no lab asserted that a packet LEAVES a
 * box that has them, which is why it survived (#199).
 *
 * With workers the arc is already terminated at the handoff - that node
 * enqueues to a thread and never consults the chain - so there is nothing
 * further on it to hand the packet to, and this node sends it to ip4-lookup
 * itself. BPA registers `runs_before ip4-lookup`, so that is where the chain
 * was going.
 *
 * The narrowing is stated rather than hidden: with workers, a feature some
 * OTHER plugin enables between BPA and ip4-lookup on this arc is skipped. This
 * product enables nothing there, and the alternative on offer today is a
 * dropped packet.
 */
/*
 * Is this destination one of the box's own addresses, in this VRF? (#470)
 *
 * Measured on a 0.135.0 box: a subscriber's echo reply to the box's own
 * ping (100.64.0.2 -> 100.64.0.1, the inside interface) was translated -
 * "BPA in2out 100.64.0.2:23789 -> 200.0.0.2:1027 (new session)" - and then
 * dropped by ip4-local, "ip4 source lookup miss", because the pool is a
 * blackhole. So every flow from a subscriber to the box died: a ping to the
 * gateway, to the uplink address, ssh or DNS to the box, each spending a
 * session of the subscriber's block on the way.
 *
 * The FIB's own answer, read the way ip4-lookup reads it: a receive DPO is
 * an address of this box. Asked BEFORE the session table and not only when
 * it misses: a session is keyed on the inside endpoint alone, so a packet to
 * the box from a port that already has a session outside would hit it and be
 * translated all the same.
 */
/* not a unit: the answer is VPP's FIB - a forwarding lookup and the DPO
 * it lands on - which no stand-in has; lab/to-us.sh sends a subscriber's
 * ping to the box itself through the CGNAT and reads it answered (#470) */
static_always_inline int
bpa_to_us (u32 fib_index, const ip4_address_t *dst)
{
  const load_balance_t *lb;

  /* Not one of the box's addresses in any VRF: not for the box, and the FIB
   * is not asked (#489, bpa_local.h). */
  if (PREDICT_TRUE (!bpa_local_has (bpa_main.local_addrs, dst->as_u32)))
    return 0;
  lb = load_balance_get (ip4_fib_forwarding_lookup (fib_index, dst));
  return load_balance_get_bucket_i (lb, 0)->dpoi_type == DPO_RECEIVE;
}

/* not a unit: the next node is VPP's feature arc (vnet_feature_next_u16)
 * or its lookup node; every datapath lab takes one branch or the other */
static_always_inline void
bpa_next_after (u16 *next, vlib_buffer_t *b, u16 lookup)
{
  if (bpa_main.n_workers)
    next[0] = lookup;
  else
    vnet_feature_next_u16 (next, b);
}

/* NAT64 (#485), bpa_nat64.c */
/* A forward's host, and a pool's private address, as screens and refusals
 * name them: a NAT64 pool's by its IPv6 host or prefix, never by the index
 * its pseudo inside holds (#485). */
format_function_t format_bpa_static_host;
format_function_t format_bpa_priv;
int bpa_pool_by_inside6 (bpa_main_t *bm, const u8 a[16], u32 fib,
			 u32 *pool_index,
			 u32 *sub_idx);
void bpa_nat64_session_key (bpa_per_thread_t *pt, bpa_session_t *s,
			    const ip6_address_t *host);
void bpa_nat64_session_unkey (bpa_per_thread_t *pt, bpa_session_t *s);
void bpa_nat64_interfaces_update (void);
void bpa_nat64_interface_update (u32 sw_if_index);
clib_error_t *bpa_nat64_check_overlap (const bpa_pool_t *cfg);
int bpa_nat64_out2in (vlib_main_t *vm, vlib_buffer_t *b, bpa_per_thread_t *pt,
		      bpa_session_t *s);
int bpa_nat64_out2in_icmp (vlib_main_t *vm, vlib_buffer_t *b,
			   bpa_per_thread_t *pt, bpa_session_t *s);

#endif /* __included_bpa_h__ */
