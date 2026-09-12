/* bpa.h - CGNAT with allocated port blocks (BPA). */

#ifndef __included_bpa_h__
#define __included_bpa_h__

#include <vlib/vlib.h>
#include <vnet/vnet.h>
#include <vnet/ip/ip.h>
#include <vnet/feature/feature.h>

#include "bpa_pool.h"
#include "bpa_ports.h"
#include "bpa_session.h"

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
   * Recomputed when an interface joins or leaves the CGNAT, and once a second
   * by the expiry process, because an MTU can change under a running box.
   */
  u16 auto_mss;

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
  u32 n_workers;
  bpa_divisor_t workers; /* spreads subscribers over the workers */

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

  u8 *actor_user;  /* the account the client named, or 0 */
  u32 actor_uid;   /* the uid it was running as, or ~0 */
  u8 actor_is_api; /* the change arrived over the binary API */

  u16 msg_id_base;
  vlib_main_t *vlib_main;
  vnet_main_t *vnet_main;
} bpa_main_t;

extern bpa_main_t bpa_main;
extern vlib_node_registration_t bpa_in2out_node;
extern vlib_node_registration_t bpa_out2in_node;
extern vlib_node_registration_t bpa_in2out_handoff_node;
extern vlib_node_registration_t bpa_out2in_handoff_node;

/* Queues a log event from the dataplane. No lock: the queue is per-thread. */
void bpa_log_queue_event (bpa_per_thread_t *pt, u8 event, u32 ip_priv,
			  u32 ip_pub, u16 port_lo, u16 port_hi, u32 pool_id);

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
 * with up to `max_examples` of their addresses. Both pools MUST be through
 * bpa_pool_init().
 */
u32 bpa_pool_remap_count (const bpa_pool_t *now, const bpa_pool_t *cfg,
			  u32 *n_total, u32 *example, u32 max_examples);
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
 * filtering mode, the block cap, the ALG switches, the MSS clamp and the
 * lifetimes. Everything that changes the MAPPING is deliberately absent.
 */
clib_error_t *bpa_pool_settings_set (u8 *name, bpa_filter_t filter,
				     u8 max_blocks, u32 alg, u32 alg_set,
				     u16 tcp_mss, u8 tcp_mss_auto,
				     u8 util_alarm,
				     const bpa_timeouts_t *timeouts);
/*
 * Replaces one of a pool's bypass lists, entire. An empty list removes the
 * bypass; see the definition for why it is not per prefix.
 */
clib_error_t *bpa_bypass_set (u8 *name, int is_dst, const u32 *bases,
			      const u32 *counts, u32 n);
clib_error_t *bpa_pool_add (bpa_pool_t *cfg, u32 *pool_index);
clib_error_t *bpa_pool_del (u8 *name);
void bpa_outside_window_update (void);
void bpa_auto_mss_update (void);
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

format_function_t format_bpa_pool;
format_function_t format_bpa_policy;
format_function_t format_bpa_mode;
format_function_t format_bpa_alg;
format_function_t format_bpa_proto;
format_function_t format_bpa_alg;

/*
 * Pool selection. A single box serves one provider, so pools are few and a
 * linear scan is cheaper than a lookup structure. If a deployment ever grows
 * many pools this becomes an mtrie.
 */
static_always_inline int
bpa_pool_by_inside (bpa_main_t *bm, u32 addr, u32 fib, u32 *pool_index,
		    u32 *sub_idx)
{
  bpa_pool_t *p;

  pool_foreach (p, bm->pools)
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
	continue;
      /*
       * A pool being drained by bpa_pool_del() is already gone as far as new
       * traffic is concerned. Skipped here so the packet is counted as "no
       * pool" - which is the truth - rather than travelling on to be refused
       * by the session bind and counted as a full port block, which is the
       * counter an operator chases when a customer complains.
       */
      if (PREDICT_FALSE (p->deleted))
	continue;
      if (bpa_set_index_of (&p->inside, addr, sub_idx) == 0)
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
static_always_inline int
bpa_maybe_outside (const bpa_main_t *bm, u32 addr)
{
  return addr >= bm->outside_lo && addr <= bm->outside_hi;
}

static_always_inline int
bpa_pool_by_outside (bpa_main_t *bm, u32 addr, u32 *pool_index)
{
  bpa_pool_t *p;
  u32 idx;

  pool_foreach (p, bm->pools)
    {
      if (bpa_set_index_of (&p->outside, addr, &idx) == 0)
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
static_always_inline void
bpa_worker_id (const bpa_main_t *bm, u32 thread_index, u32 *w, u32 *nw)
{
  if (PREDICT_TRUE (bm->n_workers != 0))
    {
      *nw = bm->n_workers;
      *w = thread_index - 1;
    }
  else
    {
      *nw = 1;
      *w = 0;
    }
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
 * and blackholed. `hexarouting-tune` configures workers on every appliance, so
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
static_always_inline void
bpa_next_after (u16 *next, vlib_buffer_t *b, u16 lookup)
{
  if (bpa_main.n_workers)
    next[0] = lookup;
  else
    vnet_feature_next_u16 (next, b);
}

#endif /* __included_bpa_h__ */
