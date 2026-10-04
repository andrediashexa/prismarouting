/* bpa.c - plugin registration and pool management. */

#include <vlib/vlib.h>
#include <vnet/plugin/plugin.h>
#include <vpp/app/version.h>
#include <vnet/ip/ip_interface.h>
#include <vnet/ip/reass/ip4_sv_reass.h>
#include <vnet/interface/rx_queue_funcs.h>
#include <vnet/interface/tx_queue_funcs.h>

#include <bpa/bpa_version.h>

#include "bpa.h"
#include "bpa_stats.h"
#include "bpa_radius.h"
#include "bpa_mss.h"

bpa_main_t bpa_main;

/*
 * Refuses a pool whose public set contains an address configured on one of
 * our own interfaces.
 *
 * bpa-out2in runs on the ip4-unicast arc, before ip4-lookup and therefore
 * before ip4-local decides a packet is for us. It would happily translate
 * the box's own BGP session or OSPF adjacency, which then breaks
 * intermittently with nothing obvious in any log. Catching it here costs
 * one walk over the interface addresses at config time.
 */
/* not a unit: it walks VPP's own interface pool and every address on it -
 * vnet_get_main(), pool_foreach over sw_interfaces and
 * foreach_ip_interface_address - none of which exists outside a running VPP.
 * The rule it enforces is measured on a box by lab/localaddr.sh, which gives
 * a pool an address the box itself carries and reads the refusal back. */
static clib_error_t *
bpa_check_local_overlap (const bpa_addr_set_t *outside, u8 *name)
{
  vnet_main_t *vnm = vnet_get_main ();
  ip4_main_t *im = &ip4_main;
  ip_lookup_main_t *lm = &im->lookup_main;
  vnet_sw_interface_t *si;
  ip4_address_t clash;
  u32 clash_sw_if_index = ~0;
  int found = 0;

  pool_foreach (si, vnm->interface_main.sw_interfaces)
    {
      ip_interface_address_t *ia;

      if (found)
	break;

      foreach_ip_interface_address (
	lm, ia, si->sw_if_index, 1 /* honor unnumbered */, ({
	  ip4_address_t *a = ip_interface_address_get_address (lm, ia);
	  u32 host = clib_net_to_host_u32 (a->as_u32);
	  u32 idx;

	  if (!found && bpa_set_index_of (outside, host, &idx) == 0)
	    {
	      found = 1;
	      clash = *a;
	      clash_sw_if_index = si->sw_if_index;
	    }
	}));
    }

  if (found)
    return clib_error_return (
      0,
      "pool '%v': public address %U is configured on %U. CGNAT would "
      "translate the box's own control-plane traffic (BGP, OSPF) and the "
      "session would drop for no apparent reason",
      name, format_ip4_address, &clash, format_vnet_sw_if_index_name, vnm,
      clash_sw_if_index);

  return 0;
}

/* not a unit: it turns bpa_static_check()'s answer into the sentence an
 * operator reads, which needs VPP's format() and a clib_error_t. WHICH
 * refusal it is - and the two fields a legal forward is completed with - is
 * the decision, and test/test_bpa_static.c drives that. Two of the sentences
 * here name a subscriber this pool's own arithmetic found, which is why the
 * wording stays where bpa_reverse() and the pool are. lab/forward.sh
 * produces them on a box, and test/test_prismash_refusals.py reads their
 * widths. */
/*
 * Where a forward is refused, and why each refusal exists.
 *
 * `sf` is completed rather than only checked: ip_pub and port_off are derived
 * from the pool arithmetic, and deriving them here means the one place that
 * decides a forward is legal is also the one place that says what it means.
 *
 * `p` MUST already be through bpa_pool_init(): every test below reads a
 * derived field.
 */
static clib_error_t *
bpa_static_validate (const bpa_pool_t *p, bpa_static_t *sf)
{
  ip4_address_t priv, pub, o;
  bpa_static_t checked = { 0 };
  bpa_block_t blk = { 0 };
  u32 other = 0;

  priv.as_u32 = clib_host_to_net_u32 (sf->ip_priv);

  switch (bpa_static_check (p, sf, &blk, &checked))
    {
    case BPA_STATIC_BAD_PROTO:
      return clib_error_return (0, "only tcp and udp can be forwarded");

    case BPA_STATIC_BAD_PORT:
      return clib_error_return (0, "port 0 is not a port");

    case BPA_STATIC_PORT_OUTSIDE:
      return clib_error_return (
	0, "public port %u is outside pool '%v', which translates %u-%u",
	sf->port_pub, p->name, p->port_min, p->port_max);

    case BPA_STATIC_NO_BLOCK:
      /*
       * A dynamic pool is oversubscribed on purpose - that is what it is for
       * - so "beyond capacity" is the ordinary case there and not an error
       * the operator made. It still has no block to pin, and saying so in
       * the pool's own numbers is the difference between a refusal that can
       * be acted on and one that reads like a bug.
       */
      if (p->mode == BPA_MODE_DYNAMIC
	  && bpa_set_index_of (&p->inside, sf->ip_priv, &other) == 0)
	return clib_error_return (
	  0,
	  "%U is subscriber %u of pool '%v', which has %u blocks. A forward "
	  "pins the block the pool's arithmetic gives the subscriber, and "
	  "there is none this far in: enlarge the public set, or serve this "
	  "customer from a pool that reaches it",
	  format_ip4_address, &priv, other, p->name, p->capacity);

      return clib_error_return (
	0,
	"%U is not a subscriber of pool '%v', or is beyond its capacity of "
	"%u",
	format_ip4_address, &priv, p->name, p->capacity);

    case BPA_STATIC_WRONG_BLOCK:
      /*
       * Anywhere else the port is somebody's - the formula assigns every
       * block to some subscriber - and taking it would translate that
       * customer onto a port this box is also sending elsewhere. Naming who
       * holds it turns "refused" into something the operator can act on.
       */
      pub.as_u32 = clib_host_to_net_u32 (blk.ip_pub);
      if (bpa_reverse (p, blk.ip_pub, sf->port_pub, &other) == 0)
	{
	  o.as_u32 = clib_host_to_net_u32 (other);
	  return clib_error_return (
	    0,
	    "public port %u belongs to %U's block, not to %U's. %U holds "
	    "%U:%u-%u",
	    sf->port_pub, format_ip4_address, &o, format_ip4_address, &priv,
	    format_ip4_address, &priv, format_ip4_address, &pub, blk.port_lo,
	    blk.port_hi);
	}
      return clib_error_return (
	0, "public port %u is not in %U's block, which is %U:%u-%u",
	sf->port_pub, format_ip4_address, &priv, format_ip4_address, &pub,
	blk.port_lo, blk.port_hi);

    case BPA_STATIC_OK:
      break;
    }

  *sf = checked;
  return 0;
}

/* not a unit: it turns bpa_static_clash()'s answer into the sentence an
 * operator reads, which needs VPP's format() and a clib_error_t. WHICH of the
 * two clashes it is - and that a difference of protocol or of public address
 * is not one - is the decision, and test/test_bpa_static.c drives that. The
 * widths of both sentences are read by test/test_prismash_refusals.py, and
 * lab/forward.sh produces them on a box. */
/*
 * Two forwards that cannot both hold.
 *
 * One public SOCKET serves one subscriber socket, and one subscriber socket
 * answers on one public socket - the second is endpoint-independent mapping
 * (RFC 4787 REQ-1), which the rest of this box already guarantees and a
 * second forward on the same socket would quietly break.
 *
 * The public socket is (address, port). A pool has as many addresses as the
 * operator gave it and hands the same port offset to one subscriber per
 * address, so 203.0.113.16:1100 and 203.0.113.17:1100 are two sockets that
 * two customers already own. Comparing the port alone made a port sellable
 * once per POOL instead of once per address, and refused the second customer
 * by naming a first one they have never heard of (issue #214).
 *
 * The private side is deliberately NOT narrowed the same way: a subscriber's
 * public address is derived from its private one, so (ip_priv, port_priv) is
 * already one address's worth of socket.
 *
 * Both callers run bpa_static_validate() first, which is where ip_pub is
 * derived. A forward that has not been through it has no address to compare
 * and must not reach here.
 */
static clib_error_t *
bpa_static_conflict (const bpa_pool_t *p, const bpa_static_t *prior,
		     u32 n_prior, const bpa_static_t *sf)
{
  const bpa_static_t *o = 0;
  ip4_address_t a, pub;

  switch (bpa_static_clash (prior, n_prior, sf, &o))
    {
    case BPA_STATIC_CLASH_PUB:
      a.as_u32 = clib_host_to_net_u32 (o->ip_priv);
      pub.as_u32 = clib_host_to_net_u32 (o->ip_pub);
      return clib_error_return (
	0, "public port %u on %U is already forwarded to %U:%u", sf->port_pub,
	format_ip4_address, &pub, format_ip4_address, &a, o->port_priv);
    case BPA_STATIC_CLASH_PRIV:
      a.as_u32 = clib_host_to_net_u32 (sf->ip_priv);
      return clib_error_return (
	0, "%U:%u is already forwarded from public port %u",
	format_ip4_address, &a, sf->port_priv, o->port_pub);
    case BPA_STATIC_CLASH_NONE:
      break;
    }
  return 0;
}

/*
 * Validates the forwards a candidate pool carries, against that pool.
 *
 * Runs on a copy that has been through bpa_pool_init(), so a forward is judged
 * by the block sizing and address sets the operator is about to commit, not by
 * the ones currently running.
 */
/* not a unit: it words the refusal bpa_statics_complete() found, through
 * bpa_static_validate() and bpa_static_conflict(), which need VPP's format()
 * and a clib_error_t. Which forward fails, and that each is judged against
 * the ones before it and written back completed, is bpa_statics_complete()
 * in bpa_pool.h, driven by test/test_bpa_pool.c. */
static clib_error_t *
bpa_statics_validate (bpa_pool_t *p, bpa_static_t *in)
{
  u32 bad = bpa_statics_complete (p, in, vec_len (in));
  bpa_static_t sf;
  clib_error_t *e;

  if (bad == ~0u)
    return 0;
  /* The same two questions again, for the one that failed, to say why. */
  sf = in[bad];
  e = bpa_static_validate (p, &sf);
  if (!e)
    e = bpa_static_conflict (p, in, bad, &sf);
  return e;
}

/* ------------------------------------------------------------------ */
/* pinning a dynamic block to the subscriber a forward belongs to      */

/*
 * Where a forward's pin lives, and why it is derived rather than stored.
 *
 * Everything here comes out of the configuration and nothing else. The
 * subscriber's dense index comes from its address; the block index IS that
 * index, because the block a subscriber is entitled to is bpa_split() of its
 * own index in both modes; and the worker comes from the index too. There is
 * no pin table to write, to load, or to disagree with the forwards - and the
 * repository has already learned that lesson once, over the second index on
 * p->statics that bpa_pool.h decided not to keep.
 *
 * That is also the whole of "the pin survives a restart". A box that reboots
 * replays its configuration; the replay creates the pool and its forwards; and
 * creating a forward is what pins. Nothing else has to be persisted, so
 * nothing else can come back stale.
 *
 * Returns -1 when there is no pin to make: a deterministic pool, where the
 * block cannot move in the first place, or an address the pool's arithmetic
 * does not reach.
 */
/* not a unit: it asks bpa_main which worker owns a subscriber's
 * block, through bpa_sub_thread() and bpa_worker_id(), neither of which
 * has meaning outside a running VPP with workers. lab/dynamic-workers.sh
 * drives a pinned forward on a box with several. */
static int
bpa_static_pin_site (const bpa_pool_t *p, const bpa_static_t *sf, u32 *ti,
		     u32 *block_idx, u32 *w, u32 *nw, bpa_block_t *blk)
{
  bpa_main_t *bm = &bpa_main;

  if (p->mode != BPA_MODE_DYNAMIC || bpa_forward (p, sf->ip_priv, blk))
    return -1;

  *block_idx = blk->sub_idx;
  *ti = bpa_sub_thread (bm, blk->sub_idx);
  bpa_worker_id (bm, *ti, w, nw);
  return 0;
}

/* The allocator that owns a pool's blocks on one thread, or 0. */
/* not a unit: it reaches into bpa_main's per-thread state for one
 * worker's dynamic allocator. The allocator itself is bpa_dyn.c and
 * test/test_bpa_dyn.c drives it. */
static bpa_dyn_t *
bpa_static_dyn (u32 pool_index, u32 ti)
{
  bpa_main_t *bm = &bpa_main;
  bpa_per_thread_t *pt;

  if (ti >= vec_len (bm->per_thread))
    return 0;
  pt = vec_elt_at_index (bm->per_thread, ti);
  if (pool_index >= vec_len (pt->dyn))
    return 0;
  return vec_elt_at_index (pt->dyn, pool_index);
}

/*
 * Refuses a forward whose block another subscriber is holding right now.
 *
 * Taking it would end that customer's live sessions so that a different one
 * can have an inbound port, which is not a trade a box makes on its own. The
 * refusal names them, because the operator's next move depends on who it is -
 * wait for them to go idle, move the forward, or move the customer.
 *
 * Not inside bpa_static_validate(): that one judges a CONFIGURATION, and is
 * run on a candidate pool that has no allocator yet. This is a fact about
 * running state, and only the paths that have a pool index can ask it. A
 * candidate pool needs no such check anyway - committing it frees the old
 * pool's blocks with the pool, so at the instant it is applied nobody holds
 * anything.
 */
/* not a unit: it asks that allocator who is holding the block a
 * forward would pin, which needs the per-worker state above. The refusal it
 * words is produced on a box by lab/dynamic.sh. */
static clib_error_t *
bpa_static_holder (u32 pool_index, const bpa_pool_t *p, const bpa_static_t *sf)
{
  u32 ti, block_idx, w, nw, held = 0, held_addr = 0;
  ip4_address_t a, pub;
  bpa_block_t blk;
  bpa_dyn_t *d;

  if (bpa_static_pin_site (p, sf, &ti, &block_idx, &w, &nw, &blk))
    return 0;

  d = bpa_static_dyn (pool_index, ti);
  if (!d)
    return 0;

  if (bpa_dyn_owner (d, block_idx, nw, &held) || held == blk.sub_idx)
    return 0;

  if (bpa_set_addr_of (&p->inside, held, &held_addr))
    return 0;

  a.as_u32 = clib_host_to_net_u32 (held_addr);
  pub.as_u32 = clib_host_to_net_u32 (blk.ip_pub);
  return clib_error_return (
    0,
    "public port %u is in block %U:%u-%u, which %U is holding now. Pinning it "
    "would end that subscriber's sessions to give this one an inbound port; "
    "the block is free again once they go idle",
    sf->port_pub, format_ip4_address, &pub, blk.port_lo, blk.port_hi,
    format_ip4_address, &a);
}

/*
 * Pins the block a forward sits in, or gives it back.
 *
 * MUST be called under the worker barrier: it reaches into another thread's
 * allocator and into that thread's log queue.
 *
 * The block event is queued rather than emitted here for the reason
 * docs/logging.md gives about templates: a pin usually happens while the
 * configuration is being replayed, which is before any IPFIX template has gone
 * out, and a record emitted then is one no collector can decode. The queue is
 * drained by bpa-expire-process, which is also where every other block event
 * leaves from.
 *
 * The event fires on the block, not on the forward. A second forward in the
 * same block pins nothing new, and a second natEvent 16 with no natEvent 17
 * between them is precisely the hole that makes a retention log answer a court
 * order wrongly.
 */
/* not a unit: it takes the worker barrier and pins or releases a block
 * in one worker's allocator. bpa_dyn_pin() and bpa_dyn_unpin() are the
 * arithmetic and test/test_bpa_dyn.c drives them. */
static void
bpa_static_pin (u32 pool_index, const bpa_pool_t *p, const bpa_static_t *sf,
		int is_add)
{
  bpa_main_t *bm = &bpa_main;
  u32 ti, block_idx, w, nw, held = 0;
  bpa_per_thread_t *pt;
  bpa_block_t blk;
  bpa_dyn_t *d;
  int changed = 0;

  if (bpa_static_pin_site (p, sf, &ti, &block_idx, &w, &nw, &blk))
    return;

  d = bpa_static_dyn (pool_index, ti);
  if (!d)
    return;
  pt = vec_elt_at_index (bm->per_thread, ti);

  if (is_add)
    {
      if (bpa_dyn_pin (d, block_idx, blk.sub_idx, nw,
		       vlib_time_now (bm->vlib_main), &held, &changed))
	return;

      /*
       * The subscriber may already be translating out of a different block -
       * in dynamic mode the allocator gave it whichever was free - and from
       * this instant its forwarded socket lives in the pinned one. The
       * per-worker record is what bpa_session_static_open() searches, so a
       * record that never heard of the pinned block is a forward that shows in
       * the configuration and translates nothing until the customer's last
       * session expires.
       */
      bpa_sub_adopt_block (pt, p, pool_index, &blk);
    }
  else
    bpa_dyn_unpin (d, block_idx, nw, &changed);

  if (changed)
    bpa_log_queue_event (pt,
			 is_add ? BPA_NAT_EVENT_BLOCK_ALLOC
				: BPA_NAT_EVENT_BLOCK_DEALLOC,
			 sf->ip_priv, blk.ip_pub, blk.port_lo, blk.port_hi,
			 pool_index);
}

/*
 * Re-announces every pinned block, when the log is switched on.
 *
 * A pin is made once, by a configuration change, and then holds a block for as
 * long as the forward exists. On a box that has just booted, the configuration
 * replay creates the pools - and their pins - BEFORE it turns the export on,
 * which is the order prismash applies a configuration in. Without this, every
 * pinned block on that box is held from then on with no allocation record
 * anywhere, which is the one outcome docs/logging.md exists to prevent: a
 * mapping nobody can attribute to a subscriber later.
 *
 * Only the pinned ones. An ordinary dynamic block held across the same moment
 * is released within a hold-down and reallocated with a record of its own, so
 * the gap closes by itself; a pinned block never gets that second chance.
 *
 * The record is stamped when the log was enabled and not when the pin was
 * made, because that is the truth this box can tell: it says the block is this
 * subscriber's as of now. A record that guessed at the earlier instant would
 * be a fabricated one.
 */
/* not a unit: it takes the worker barrier and walks every pool in
 * bpa_main to re-announce the blocks forwards are holding. What a
 * re-announcement looks like is read on a box by lab/ipfix.sh. */
void
bpa_log_pins_replay (void)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;

  vlib_worker_thread_barrier_sync (bm->vlib_main);
  pool_foreach (p, bm->pools)
    {
      u32 pool_index = p - bm->pools, i;

      if (p->mode != BPA_MODE_DYNAMIC)
	continue;

      for (i = 0; i < vec_len (p->statics); i++)
	{
	  u32 ti, block_idx, w, nw;
	  bpa_block_t blk;
	  bpa_dyn_t *d;

	  if (bpa_static_pin_site (p, &p->statics[i], &ti, &block_idx, &w, &nw,
				   &blk))
	    continue;
	  d = bpa_static_dyn (pool_index, ti);
	  /* One record per BLOCK: two forwards in one block are one pin, and
	   * two allocations with no release between them is the hole this is
	   * closing, not a second copy of the fix. */
	  if (!d || !bpa_dyn_is_pinned (d, block_idx, nw)
	      || bpa_static_first_in_block (p->statics, vec_len (p->statics),
					    &p->statics[i]) == 0)
	    continue;

	  bpa_log_queue_event (vec_elt_at_index (bm->per_thread, ti),
			       BPA_NAT_EVENT_BLOCK_ALLOC, p->statics[i].ip_priv,
			       blk.ip_pub, blk.port_lo, blk.port_hi,
			       pool_index);
	}
    }
  vlib_worker_thread_barrier_release (bm->vlib_main);
}

/* not a unit: it words the refusal, which needs VPP's format() and a
 * clib_error_t - and the sentence names the first two subscribers, so it
 * needs them and not only a count. HOW MANY forwards a protocol and a public
 * port have, and which two to name, is bpa_static_on_port() in bpa_pool.h
 * and test/test_bpa_static.c drives it. lab/forward.sh produces this refusal
 * on a box. */
/*
 * The one forward on (protocol, public port) when the caller did not say which
 * subscriber, or an error naming the ones it could have meant.
 *
 * The public address is derived and never typed, so the public port used to
 * BE the name of a forward. On a pool with several public addresses it names
 * up to one forward per address, and removing the first match would take an
 * inbound port away from a customer nobody mentioned. Ambiguity is refused
 * and the operator is told how to say which - the subscriber, which is the
 * one thing they do type.
 */
static clib_error_t *
bpa_static_only_on_port (const bpa_pool_t *p, const bpa_static_t *sf,
			 const bpa_static_t **out)
{
  const bpa_static_t *first = 0, *second = 0;
  ip4_address_t a;
  clib_error_t *e;
  u8 *who = 0;
  u32 n_match = bpa_static_on_port (p->statics, vec_len (p->statics),
				    sf->port_pub, sf->proto, &first, &second);

  if (n_match <= 1)
    {
      *out = first;
      return 0;
    }

  a.as_u32 = clib_host_to_net_u32 (first->ip_priv);
  who = format (who, "%U", format_ip4_address, &a);
  a.as_u32 = clib_host_to_net_u32 (second->ip_priv);
  who = format (who, " and %U", format_ip4_address, &a);
  if (n_match > 2)
    who = format (who, " and %u more", n_match - 2);

  e = clib_error_return (
    0,
    "%U port %u is forwarded on %u public addresses of pool '%v' - for %v. "
    "Name the subscriber: 'bpa forward %v %U %u to <private-ip> "
    "<private-port> del'",
    format_bpa_proto, sf->proto, sf->port_pub, n_match, p->name, who, p->name,
    format_bpa_proto, sf->proto, sf->port_pub);
  vec_free (who);
  return e;
}

/*
 * Adds or removes one static port forward on a running pool.
 *
 * `is_check` validates and changes nothing, which is what a commit check
 * needs: a forward is refused for reasons - the port is in another
 * subscriber's block, the address is not in the pool - that an operator must
 * be told before the commit rather than after it.
 */
/* not a unit: it looks the pool up in bpa_main's hash, mutates the
 * vector under the worker barrier and pins or releases a block. The
 * decisions it takes are bpa_static_position(), bpa_static_on_port() and
 * the two above, all in bpa_pool.h; lab/forward.sh drives the whole thing
 * on a box. */
clib_error_t *
bpa_static_add_del (u8 *name, bpa_static_t *sf, int is_add, int is_check)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;
  clib_error_t *e;
  uword *q;
  u32 i, pool_index;

  q = hash_get_mem (bm->pool_by_name, name);
  if (!q)
    return clib_error_return (0, "no such pool '%v'", name);

  pool_index = q[0];
  p = pool_elt_at_index (bm->pools, pool_index);

  /*
   * Removing is identified by the public port and the protocol, and nothing
   * else: that pair is what a forward IS. The private side is not asked for -
   * an operator removing a forward should not have to remember which port it
   * pointed at - so this runs before the validation below, which is about
   * whether a forward may be created.
   */
  if (!is_add)
    {
      const bpa_static_t *have = 0;
      bpa_static_t gone;
      bpa_block_t blk;

      /*
       * Given the subscriber, the public address follows from the pool's own
       * arithmetic and the forward is named exactly. That is the shape the
       * VTY sends - it removes a forward it holds in full - and the shape an
       * operator gets by putting "del" on the end of the line they added.
       */
      if (sf->ip_priv != 0)
	{
	  ip4_address_t priv;

	  priv.as_u32 = clib_host_to_net_u32 (sf->ip_priv);
	  if (bpa_forward (p, sf->ip_priv, &blk))
	    return clib_error_return (
	      0, "%U is not a subscriber of pool '%v', or is beyond its "
		 "capacity of %u",
	      format_ip4_address, &priv, p->name, p->capacity);
	  have = bpa_static_by_pub (p, blk.ip_pub, sf->port_pub, sf->proto);
	}
      else
	{
	  e = bpa_static_only_on_port (p, sf, &have);
	  if (e)
	    return e;
	}

      if (!have)
	return clib_error_return (0, "%U port %u is not forwarded",
				  format_bpa_proto, sf->proto, sf->port_pub);
      if (is_check)
	return 0;

      gone = *have;
      i = have - p->statics;
      /* Workers read p->statics on the datapath; mutate it under the
       * barrier, and do the session surgery inside the same one. */
      vlib_worker_thread_barrier_sync (bm->vlib_main);
      bpa_static_uninstall (pool_index, &gone);
      vec_delete (p->statics, 1, i);
      /* After the removal, so "is anything still forwarded in this block"
       * is asked of the configuration that is about to be in force. */
      if (bpa_static_last_in_block (p->statics, vec_len (p->statics), &gone))
	bpa_static_pin (pool_index, p, &gone, 0);
      vlib_worker_thread_barrier_release (bm->vlib_main);
      bpa_audit ("forward-del", p, 0);
      return 0;
    }

  e = bpa_static_validate (p, sf);
  if (e)
    return e;

  e = bpa_static_conflict (p, p->statics, vec_len (p->statics), sf);
  if (e)
    return e;

  /* Last, because it is the only refusal that depends on what the box is
   * doing this second rather than on what it was told. An operator who gets
   * it has a configuration that is legal and a moment that is not. */
  e = bpa_static_holder (pool_index, p, sf);
  if (e || is_check)
    return e;

  i = bpa_static_position (p->statics, vec_len (p->statics), sf);
  vlib_worker_thread_barrier_sync (bm->vlib_main);
  vec_insert_elts (p->statics, sf, 1, i);
  /* Before the install: the install reserves the forwarded port in the
   * subscriber's bitmap, and on a dynamic pool the block that bitmap belongs
   * to is the one this is about to pin. */
  bpa_static_pin (pool_index, p, &p->statics[i], 1);
  bpa_static_install (pool_index, &p->statics[i]);
  vlib_worker_thread_barrier_release (bm->vlib_main);

  bpa_audit ("forward-add", p, 0);
  return 0;
}

/*
 * Refuses a pool that would make selection ambiguous.
 *
 * Two pools claiming one inside address is not an error the datapath can
 * resolve: bpa_pool_by_inside() returns the FIRST that matches, so the second
 * pool exists, accepts configuration and translates nothing - and nothing says
 * so (issue #73). Two pools sharing a PUBLIC address is worse, because the
 * return path keys on it and has no VRF to separate them.
 *
 * A VRF is what makes an inside overlap legitimate: two tenants may use the
 * same private range, and that is the case this exists to allow rather than to
 * forbid. So an inside overlap is refused only when both pools could answer
 * for the same packet - when at least one of them is bound to no VRF, or both
 * are bound to the same one.
 */
/* not a unit: it walks bpa_main's pool of pools with pool_foreach and
 * formats a clib_error_t. Whether two pools collide - which side, and when
 * the VRFs keep them apart - is bpa_pools_collide() in bpa_pool.h, which
 * test/test_bpa_pool.c drives (#424, #472); what is left here is the walk
 * and the sentence, and lab/two-pools.sh produces both on a box. */
static clib_error_t *
bpa_check_pool_overlap (const bpa_pool_t *cfg)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;

  pool_foreach (p, bm->pools)
    {
      int same_name = vec_len (p->name) == vec_len (cfg->name)
		      && !memcmp (p->name, cfg->name, vec_len (cfg->name));
      ip4_address_t a;
      u32 addr;
      int outside;

      /* A pool being replaced does not overlap itself. */
      if (same_name || !bpa_pools_collide (cfg, p, &addr, &outside))
	continue;

      a.as_u32 = clib_host_to_net_u32 (addr);
      if (outside)
	return clib_error_return (
	  0,
	  "public address %U is already in pool '%v'. Two pools cannot "
	  "share one: the return path finds a pool by public address "
	  "and would have two answers",
	  format_ip4_address, &a, p->name);
      return clib_error_return (
	0,
	"inside address %U is already served by pool '%v'%s. The "
	"first pool that claims an address answers for it, so the "
	"second would translate nothing and say nothing - bind them "
	"to different VRFs, or give them different ranges",
	format_ip4_address, &a, p->name,
	(p->fib_index == ~0u || cfg->fib_index == ~0u)
	  ? " (one of them serves every VRF)"
	  : " in the same VRF");
    }
  return 0;
}


/*
 * Every check bpa_pool_add() performs, without creating anything.
 *
 * Exists so a configuration can be validated before it is applied. Without
 * it, a "commit check" can only inspect syntax and then approve a pool the
 * device will refuse - false confidence exactly where an operator is relying
 * on the check.
 */
/* not a unit: it is the order the checks run in, and two of them walk
 * VPP's own pools (bpa_check_local_overlap, bpa_check_pool_overlap). The
 * order is the finding it carries - the forwards last, because every reason
 * to refuse one is a fact bpa_pool_init() computes - and lab/two-pools.sh
 * and lab/forward.sh produce it on a box. */
clib_error_t *
bpa_pool_validate (bpa_pool_t *cfg)
{
  bpa_pool_t probe;
  clib_error_t *e;

  /*
   * Deliberately no name-collision check. A candidate configuration that
   * carries an existing name is a REPLACEMENT, and validating it must not
   * require deleting the running pool first - a check that mutates state is
   * not a check.
   */
  e = bpa_check_local_overlap (&cfg->outside, cfg->name);
  if (e)
    return e;
  e = bpa_check_pool_overlap (cfg);
  if (e)
    return e;
  /* The NAT64 refusals bpa_pool_add() makes, so a check does not approve a
   * pool the commit then cannot create (#485). */
  {
    const char *why = bpa_nat64_unsupported (cfg);

    if (why)
      return clib_error_return (0, "%s", why);
    e = bpa_nat64_check_overlap (cfg);
    if (e)
      return e;
  }

  /* run the derived-value computation on a copy, so nothing is retained */
  probe = *cfg;
  probe.statics = 0;
  e = bpa_pool_init (&probe);
  if (e)
    return e;

  /*
   * The forwards last, because every reason to refuse one is a fact about the
   * pool that only exists after bpa_pool_init() has computed it: the block a
   * subscriber holds, the pool's capacity, the port range.
   */
  return bpa_statics_validate (&probe, cfg->statics);
}

/*
 * Recompute the hairpin pre-filter window: the lowest and highest public
 * address any pool holds.
 *
 * Called under the worker barrier, from the two places that change the set of
 * pools. Empty when there are no pools, which makes the filter reject
 * everything - correct, because with no pool there is nothing to hairpin to.
 */
/* not a unit: it walks bpa_main's pools; the window's arithmetic is
 * bpa_window_widen() and bpa_window_close() in bpa_pool.h, driven by
 * test/test_bpa_pool.c. The out2in path checks a packet against it before
 * looking anything up, and lab/datapath.sh drives that path. */
void
bpa_outside_window_update (void)
{
  bpa_main_t *bm = &bpa_main;
  u32 lo = ~0u, hi = 0;
  bpa_pool_t *p;

  pool_foreach (p, bm->pools)
    bpa_window_widen (&p->outside, &lo, &hi);
  bpa_window_close (&lo, &hi);
  bm->outside_lo = lo;
  bm->outside_hi = hi;
}

/*
 * What ONE interface contributes to the "auto" clamp: its IPv4 MTU while the
 * CGNAT is on it, and zero once it is not.
 *
 * Asked of the feature arc rather than of a copy kept here, because the arc
 * is what the datapath obeys. A toggle the arc refused, or one it counted
 * twice because the same interface was attached twice, must not leave this
 * plugin's idea of the set disagreeing with the set that is actually running.
 *
 * The handoff variants are the same interface carrying the same traffic; a
 * box with workers would otherwise report no CGNAT interfaces at all and
 * leave "auto" resolving to nothing.
 */
/* not a unit: it asks vnet whether a CGNAT feature is enabled
 * on an interface and what that interface's IP4 MTU is - four
 * vnet_feature_is_enabled() calls and vnet_sw_interface_get_mtu().
 * lab/mss.sh is where an MTU reaches a subscriber's TCP handshake. */
static u32
bpa_interface_cgnat_mtu (vnet_main_t *vnm, u32 sw_if_index)
{
  int on = 0;

  on |= vnet_feature_is_enabled ("ip4-unicast", "bpa-in2out", sw_if_index)
	== 1;
  on |=
    vnet_feature_is_enabled ("ip4-unicast", "bpa-in2out-handoff", sw_if_index)
    == 1;
  on |= vnet_feature_is_enabled ("ip4-unicast", "bpa-out2in", sw_if_index)
	== 1;
  on |=
    vnet_feature_is_enabled ("ip4-unicast", "bpa-out2in-handoff", sw_if_index)
    == 1;
  if (!on)
    return 0;

  return vnet_sw_interface_get_mtu (vnm, sw_if_index, VNET_MTU_IP4);
}

/* Points the tracker at the vector, which vec_validate may have moved. */
/* not a unit: it grows two VPP vectors on bpa_main so they can be
 * indexed by sw_if_index. What they hold is decided by
 * bpa_auto_mss_storage() in bpa_auto_mss.h, which test/test_bpa_auto_mss.c
 * drives. */
static void
bpa_auto_mss_slots (bpa_main_t *bm, u32 sw_if_index)
{
  vec_validate (bm->auto_mss_mtu, sw_if_index);
  bpa_auto_mss_storage (&bm->auto_mss_track, bm->auto_mss_mtu,
			vec_len (bm->auto_mss_mtu));
}

/*
 * One interface changed. Constant work: one interface's feature state, one
 * MTU, one slot - and no reference at all to how many interfaces the box has,
 * which is the whole of #287.
 */
/* not a unit: it reads an interface's MTU out of vnet
 * and writes the clamp into those vectors. The arithmetic - what MSS an MTU
 * allows - is bpa_auto_mss.h's and is driven by test/test_bpa_auto_mss.c. */
void
bpa_auto_mss_interface_update (u32 sw_if_index)
{
  bpa_main_t *bm = &bpa_main;
  vnet_main_t *vnm = bm->vnet_main;

  if (!vnm)
    return;

  bpa_auto_mss_slots (bm, sw_if_index);
  bpa_auto_mss_set (&bm->auto_mss_track, sw_if_index,
		    bpa_interface_cgnat_mtu (vnm, sw_if_index));
  bm->auto_mss = bpa_auto_mss_value (&bm->auto_mss_track);
}

/*
 * Reconciles the clamp with the MTUs the box is actually carrying.
 *
 * Over the interfaces the CGNAT is attached to, and not over the box: an MTU
 * that moved under a running box is the only thing this pass can learn, and
 * an interface the CGNAT is not on cannot change the answer.
 *
 * Still a pass and not a callback on MTU changes, for the reason the previous
 * version of this function gave: a hook that fires on every MTU change
 * anywhere in VPP is a subscription to somebody else's lifecycle. What made
 * that argument fail was not the pass, it was running the pass on every
 * attach as well.
 */
/* not a unit: the same for every interface this box has, which
 * means walking vnet's interface pool. lab/mss.sh reads the clamp off a
 * handshake. */
void
bpa_auto_mss_update (void)
{
  bpa_main_t *bm = &bpa_main;
  vnet_main_t *vnm = bm->vnet_main;
  u32 i;

  if (!vnm)
    return;

  bpa_auto_mss_storage (&bm->auto_mss_track, bm->auto_mss_mtu,
			vec_len (bm->auto_mss_mtu));

  for (i = 0; i < vec_len (bm->auto_mss_mtu); i++)
    {
      if (bm->auto_mss_mtu[i] == 0)
	continue;

      /*
       * An interface deleted while the CGNAT was on it leaves its slot
       * behind, and reading the MTU of a freed pool entry is a read of
       * whatever is there now. Deletion does not come through
       * bpa_interface_feature_enable_disable(), so this is where it is
       * noticed - within a second, during which the clamp can only be
       * SMALLER than the surviving interfaces need, which costs throughput
       * and breaks nothing.
       */
      if (!vnet_sw_interface_is_valid (vnm, i))
	{
	  bpa_auto_mss_set (&bm->auto_mss_track, i, 0);
	  continue;
	}

      bpa_auto_mss_set (&bm->auto_mss_track, i,
			vnet_sw_interface_get_mtu (vnm, i, VNET_MTU_IP4));
    }

  bm->auto_mss = bpa_auto_mss_value (&bm->auto_mss_track);
}

/*
 * Attaching CGNAT to an interface, or taking it off, for every caller.
 *
 * One function and not two identical ones, because everything the feature
 * toggle drags with it is invisible when it is missing, and a caller that
 * forgets a piece produces a box that looks configured and is not. That is
 * not hypothetical: the VPP CLI did all of this and the binary API did only
 * the first line of it, so every interface the product attached after #76
 * moved the VTY off vppctl carried CGNAT with no reassembly and no MSS
 * recompute (#183). Whatever the next caller is - a startup file, a
 * netconf agent - it gets the whole thing by calling this.
 */
/*
 * Each RX queue of the card under `sw_if_index` polled by the thread that
 * transmits on the TX queue with the same number (#487). bpa_rx_thread_for()
 * says why and holds the choice; this is only the walk over VPP's tables.
 *
 * At attach, because that is when the card starts carrying subscribers, and
 * once is enough: measured on the #475 bench, the placement survives the
 * card going down and up and an MTU change. A card with no queues - a tap, a
 * loopback - has nothing to walk.
 */
/* not a unit: it reads and moves VPP's queue
 * placement, which only a running dataplane has; bpa_rx_thread_for() is the
 * choice, held by test/test_bpa_workers.c, and the #475 bench measured the
 * handoff it removes. */
static void
bpa_rx_align (u32 sw_if_index)
{
  vnet_main_t *vnm = vnet_get_main ();
  vnet_hw_interface_t *hw = vnet_get_sup_hw_interface (vnm, sw_if_index);
  u32 *tx_threads = 0, *qi, *ti, moved = 0;

  if (!hw || vlib_num_workers () == 0)
    return;
  vec_foreach (qi, hw->rx_queue_indices)
    {
      vnet_hw_if_rx_queue_t *rxq = vnet_hw_if_get_rx_queue (vnm, qi[0]);
      u32 thread;

      if (!rxq)
	continue;
      vec_reset_length (tx_threads);
      vec_foreach (ti, hw->tx_queue_indices)
	{
	  vnet_hw_if_tx_queue_t *txq = vnet_hw_if_get_tx_queue (vnm, ti[0]);
	  uword t;

	  if (!txq || txq->queue_id != rxq->queue_id)
	    continue;
	  clib_bitmap_foreach (t, txq->threads)
	    vec_add1 (tx_threads, (u32) t);
	}
      thread = bpa_rx_thread_for (tx_threads, vec_len (tx_threads),
				  rxq->thread_index);
      if (thread != ~0u && thread != rxq->thread_index)
	{
	  vnet_hw_if_set_rx_queue_thread_index (vnm, qi[0], thread);
	  moved++;
	}
    }
  vec_free (tx_threads);
  if (moved)
    vnet_hw_if_update_runtime_data (vnm, hw->hw_if_index);
}

/* not a unit: it enables or disables this
 * plugin's nodes on an interface through vnet_feature_enable_disable(),
 * which is VPP's graph. test/test_feature_order.py holds the order those
 * nodes sit in, and lab/datapath.sh sends packets through them. */
int
bpa_interface_feature_enable_disable (u32 sw_if_index, int is_inside,
				      int enable)
{
  const char *feature;
  int rv;

  /* On any box with workers, and not only when every worker translates:
   * with `cgnat-workers` naming fewer, a packet can arrive on a worker that
   * owns nobody, and only the handoff takes it to one that does (#466). */
  if (vlib_num_workers ())
    feature = is_inside ? "bpa-in2out-handoff" : "bpa-out2in-handoff";
  else
    feature = is_inside ? "bpa-in2out" : "bpa-out2in";

  rv = vnet_feature_enable_disable ("ip4-unicast", feature, sw_if_index,
				    enable, 0, 0);
  if (rv)
    return rv;

  /*
   * Shallow virtual reassembly: it keeps the ports from the first fragment
   * and attaches them to the following ones. Without it a fragment with no
   * port matches no session and is dropped. It is refcounted, so coexisting
   * with another feature that also uses it is not a problem - and the detach
   * has to give back exactly what the attach took, or reassembly stays on an
   * interface the CGNAT no longer touches with nothing left to turn it off.
   */
  ip4_sv_reass_enable_disable_with_refcnt (sw_if_index, enable);

  if (enable)
    bpa_rx_align (sw_if_index);

  /* The set of interfaces the CGNAT runs on has just changed, and that set is
   * what "tcp-mss auto" is the minimum over. Left alone, the clamp keeps
   * describing an interface set that no longer exists, and a subscriber
   * behind the new interface is handed an MSS larger than the path takes.
   *
   * THIS interface and not all of them: see bpa_auto_mss.h for why that
   * distinction is the difference between an attach and 83 seconds of them. */
  bpa_auto_mss_interface_update (sw_if_index);
  /* Its IPv6 half, while a NAT64 pool exists (#485). */
  if (is_inside)
    bpa_nat64_interfaces_update ();

  /*
   * Recorded only once the dataplane has accepted it, and by NAME: an entry
   * for a change that was refused is a trail that disagrees with the box, and
   * an sw_if_index is a number that means nothing to whoever reads the file a
   * year later, on a box that has been renumbered by a reboot.
   */
  {
    u8 *ifname = format (0, "%U", format_vnet_sw_if_index_name,
			 vnet_get_main (), sw_if_index);

    bpa_audit_interface (enable ? "attach" : "detach", ifname, is_inside);
    vec_free (ifname);
  }
  return 0;
}

/*
 * The per-subscriber bounds of a pool, changed in place.
 *
 * In place because of when an operator sets them: looking at one infected
 * subscriber, on a box carrying everybody else. Recreating the pool to apply a
 * cap would disconnect every neighbour to bound one customer, which trades a
 * complaint for an outage (issue #69 is the general case).
 *
 * Sessions already open are not closed. A cap is a bound on what may be
 * created, and tearing down mappings that predate it would make setting it an
 * outage of its own - the subscriber comes back under the cap within a
 * timeout, and until then it holds what it already had.
 */
/*
 * Replaces one of a pool's bypass lists.
 *
 * The whole list, not one prefix at a time. bpa_addr_set_build() merges
 * adjacent prefixes into ranges, so what is stored no longer remembers which
 * prefixes an operator typed - and removing "one of them" from a merged range
 * is a question with no honest answer. Setting the list entire has one, and it
 * is also what a candidate configuration naturally produces.
 *
 * An empty list is how a bypass is removed.
 */
/*
 * Every per-pool setting that can be changed without recreating the pool.
 *
 * One entry point rather than five, because the list of settings that are safe
 * to change in place is a fact about the pool and belongs in one place. The
 * ones NOT here are the ones that change the mapping - the prefixes, the block
 * size, the port range, the policy and the mode - and those still require the
 * pool to be rebuilt, because every session on it is invalid the moment they
 * change.
 *
 * Sessions already open keep whatever was done to them. Turning an ALG off
 * does not close the mappings it opened, and shortening a timeout does not
 * expire anything early: both take effect on what happens next, which is what
 * an operator changing a setting on a live box is asking for.
 */
/*
 * Says that a timeout is under its floor, to the operator who set it and to
 * the log.
 *
 * Both, because they are not the same person: the one who shortens a timeout
 * when a pool fills sees the CLI, and the one diagnosing "it disconnects at
 * random" six weeks later has the log.
 */
typedef struct
{
  vlib_main_t *vm;
  u8 *pool;
} bpa_floor_ctx_t;

/* not a unit: it formats one RFC floor's warning with VPP's format() and
 * writes it to a vlib_main_t. WHICH floors exist and what each breaks is
 * the X-macro in bpa_pool.h, which test/test_doc_copies.py reads against the
 * three places those numbers are written down. */
static void
bpa_floor_warn (void *ctxp, const char *name, u32 value, u32 floor,
		const char *rfc, const char *breaks)
{
  bpa_floor_ctx_t *ctx = ctxp;
  u8 *where = ctx->pool ? format (0, " on pool %v", ctx->pool) : 0;

  if (ctx->vm)
    {
      vlib_cli_output (ctx->vm,
		       "warning: %s %u%v is below the %u seconds %s asks for",
		       name, value, where, floor, rfc);
      vlib_cli_output (ctx->vm, "         under it, expect %s", breaks);
      vlib_cli_output (ctx->vm,
		       "         the value is applied; this is a warning, "
		       "not a refusal");
    }
  vlib_log_warn (bpa_main.log_class,
		 "timeout %s set to %u%v, below the %u seconds %s asks for: "
		 "expect %s",
		 name, value, where, floor, rfc, breaks);
  vec_free (where);
}

/*
 * One entry point for "this timeout is under its floor", so the CLI, the API
 * and the pool creation path cannot warn differently - or one of them forget.
 * `vm` may be null, and then only the log hears about it.
 */
/* not a unit: it walks that X-macro calling the one above, so it needs
 * the same vlib_main_t, and the numbers it walks are held by
 * test/test_doc_copies.py. */
void
bpa_timeouts_warn (vlib_main_t *vm, const bpa_timeouts_t *t, u8 *pool)
{
  bpa_floor_ctx_t ctx = { .vm = vm, .pool = pool };

  bpa_timeouts_check (t, bpa_floor_warn, &ctx);
}

/*
 * A stride the worker sharding cannot honour, said where the operator can act
 * on it: at the prompt that applied it, and on every screen that reports it.
 *
 * A WARNING and not a refusal, and the difference matters more than it looks.
 * Whether a stride is reachable depends on the worker count, which is a
 * startup setting rather than part of the pool: a configuration accepted on a
 * single-threaded box would be REFUSED after the same box is given four
 * workers, and the pool would then fail to come up at all. A pool that does
 * not exist is every subscriber on it losing traffic; a stride not honoured is
 * a preference about layout that the counters in "show bpa blocks" report.
 * The first is not an acceptable price for the second.
 *
 * THREE FUNCTIONS AND NOT ONE, because three different occasions ask for this
 * and they want different halves. The guard below owns the arithmetic, the say
 * owns the words, and the warn is the say plus the record.
 */

/*
 * Whether this box's sharding can honour the pool's stride, and what to
 * suggest when it cannot: 0 when there is nothing to warn about, 1 when there
 * is. `nwp` takes the worker count the sentence quotes, `okp` the nearest
 * honourable stride, which is 0 when this pool has none.
 *
 * THE ONE OWNER OF THE ARITHMETIC. Three occasions ask this question now - a
 * pool is created, its settings are changed, and somebody types "show bpa
 * pool" - and a second copy of the reachability test written for any of them
 * would be a second thing to keep in step with this one. A worker-less box is never
 * short: bpa_stride_reachable() answers yes at one worker, which is why `nw`
 * is floored here and not at each caller.
 *
 * AND NO TEST OF THE MODE, because a pool that reaches here with a stride is
 * a dynamic pool. Every sentence below is about worker sharding - blocks are
 * sharded N ways, the nearest stride that is a multiple - and all of it is
 * false on a deterministic pool, where the stride is not read at any worker
 * count and no stride would work. That pool cannot exist: bpa_pool_init()
 * and bpa_pool_settings_set() are the only two ways a stride is ever stored
 * and both refuse it through the one bpa_pool_stride_refusal() they share,
 * so "both refuse it" is true by construction and not by two copies
 * agreeing (#375). A mode test here would be a second owner of the same
 * rule and could never be shown failing.
 */
/* not a unit: it reads how many workers this box has out of bpa_main, which
 * exists only inside VPP. THE ARITHMETIC is bpa_stride_short_at() in
 * bpa_pool.h and test/test_bpa_pool.c drives it; what is here is the floor -
 * one worker on a worker-less box - kept in the one place that reads the
 * count rather than at each of the three callers. */
static int
bpa_stride_short (const bpa_pool_t *p, u32 *nwp, u32 *okp)
{
  bpa_main_t *bm = &bpa_main;
  u32 nw = bm->n_workers ? bm->n_workers : 1;

  *nwp = nw;
  return bpa_stride_short_at (p, nw, okp);
}

/*
 * The three rows an operator reads, and nothing else.
 *
 * SEPARATE FROM THE RECORD, because the two have different occasions. The
 * record belongs to the event - a stride was applied that this box cannot
 * honour, once - and the rows belong to whoever is looking. "show bpa pool" is
 * the readback prismash uses to put this in front of an operator who applied the
 * pool over the API and has no terminal on the box, and a show that wrote a
 * log line every time somebody ran it would fill the log with one event
 * repeated by its own reader.
 *
 * Same words for every occasion, on purpose: the sentence an operator is told
 * at the prompt and the sentence they are told at the console are the same
 * sentence, so neither can drift into being the friendlier one.
 */
/* not a unit: it prints the stride warning to a vlib_main_t's CLI, which
 * is `show bpa pool`'s screen. WHETHER to warn is bpa_stride_short_at() in
 * bpa_pool.h and is driven there; the words are read by
 * test/test_plugin_cli_says.py, and lab/crossaddr.sh drives a strided pool
 * on a box. */
void
bpa_stride_say (vlib_main_t *vm, const bpa_pool_t *p)
{
  u32 nw, ok;

  if (!vm || !bpa_stride_short (p, &nw, &ok))
    return;

  vlib_cli_output (vm,
		   "warning: block-stride %u on pool %v cannot be honoured: "
		   "blocks are sharded %u ways and a stride step moves by "
		   "%u, so every block the stride names belongs to another "
		   "worker",
		   p->block_stride, p->name, nw, bpa_stride_unit (p));
  if (ok)
    vlib_cli_output (vm, "         the nearest stride that can be: %u", ok);
  else
    vlib_cli_output (vm, "         no stride fits this pool at %u workers",
		     nw);
  vlib_cli_output (vm, "         the setting is applied and extensions fall "
		       "back to the free list; this is a warning, not a "
		       "refusal");
}

/*
 * The rows plus the record.
 *
 * `vm` may be null, and then only the log hears about it - the API path has an
 * operator at the other end of a message rather than a terminal, and prismash
 * relays the rows from "show bpa pool" instead.
 */
/* not a unit: the same sentence at the moment the stride is set, written
 * to vlib's log rather than the CLI. The decision is bpa_stride_short_at(). */
void
bpa_stride_warn (vlib_main_t *vm, const bpa_pool_t *p)
{
  bpa_main_t *bm = &bpa_main;
  u32 nw, ok;

  if (!bpa_stride_short (p, &nw, &ok))
    return;

  bpa_stride_say (vm, p);
  vlib_log_warn (bm->log_class,
		 "pool %v: block-stride %u unreachable at %u workers "
		 "(a step moves by %u)%s%u",
		 p->name, p->block_stride, nw, bpa_stride_unit (p),
		 ok ? "; nearest that is: " : "; none fits", ok);
}

/*
 * A reserve too small for the worker count, said at creation and whenever the
 * pool is asked about.
 *
 * The reserve is sharded the way every other block is - worker w hands out
 * only the indices congruent to w, because the return path resolves the
 * thread from the index it reads off the packet - so a reserve of two blocks
 * on a box with four workers leaves two workers with nothing to give. The
 * subscribers that hash to those workers are refused an extension every time,
 * and from outside that is indistinguishable from a reserve that is merely
 * busy.
 *
 * A warning and not a refusal, for the reason bpa_stride_warn() gives: the
 * worker count is a startup setting, so refusing here would stop a pool
 * accepted on one box from coming up on the same box with more workers, and
 * every subscriber on it would lose traffic to enforce a property about
 * extensions.
 *
 * THREE FUNCTIONS AND NOT ONE, because three different occasions ask for it
 * and they want different halves. The guard below owns the arithmetic, the
 * say owns the words, and the warn is the say plus the record.
 */

/*
 * How many of this box's workers the reserve does not reach, and 0 when it
 * reaches all of them.
 *
 * THE ONE OWNER OF THE ARITHMETIC. Three callers ask this question now - the
 * pool is created, the operator types "show bpa pool", and the record is
 * written - and a second copy of `reserve_count >= n_workers` in any of them
 * is a second thing to keep in step with this one. A worker-less box compares
 * one against one and is never short, which is why `nw` is floored at 1 here
 * and not at each caller.
 */
/* not a unit: the same reading, out of the same bpa_main. The arithmetic is
 * bpa_reserve_short_at() in bpa_pool.h and is driven there. */
static u32
bpa_reserve_short (const bpa_pool_t *p, u32 *nwp)
{
  bpa_main_t *bm = &bpa_main;
  u32 nw = bm->n_workers ? bm->n_workers : 1;

  *nwp = nw;
  return bpa_reserve_short_at (p, nw);
}

/*
 * The three rows an operator reads, and nothing else.
 *
 * SEPARATE FROM THE RECORD, because the two have different occasions. The
 * record belongs to the event - a pool was created with a reserve that does
 * not reach every worker, once - and the rows belong to whoever is looking.
 * "show bpa pool" is the readback prismash uses to put this in front of an
 * operator who applied the pool over the API and has no terminal on the box,
 * and a show that wrote a log line every time somebody ran it would fill the
 * log with one event repeated by its own reader.
 *
 * Same words for every occasion, on purpose: the sentence an operator is told
 * at the prompt and the sentence they are told at the console are the same
 * sentence, so neither can drift into being the friendlier one.
 */
/* not a unit: it prints the reserve warning to a vlib_main_t's CLI. The
 * decision is bpa_reserve_short_at() in bpa_pool.h, driven there. */
void
bpa_reserve_say (vlib_main_t *vm, const bpa_pool_t *p)
{
  u32 nw;
  u32 none = bpa_reserve_short (p, &nw);

  if (!vm || none == 0)
    return;

  vlib_cli_output (
    vm, "warning: the reserve on pool %v is %u block(s) and this box has "
	"%u workers: %u of them hold no reserve at all, and their "
	"subscribers can never be given a second block",
    p->name, p->reserve_count, nw, none);
  vlib_cli_output (vm,
		   "         raise the reserve to at least %u, or add "
		   "public addresses; each reserve block per address is "
		   "%u more",
		   nw, p->outside.count);
  vlib_cli_output (vm, "         the pool is created either way; this is "
		       "a warning, not a refusal");
}

/* not a unit: the same sentence at the moment the reserve is set, to
 * vlib's log. The decision is bpa_reserve_short_at(). */
void
bpa_reserve_warn (vlib_main_t *vm, const bpa_pool_t *p)
{
  bpa_main_t *bm = &bpa_main;
  u32 nw;

  if (bpa_reserve_short (p, &nw) == 0)
    return;

  bpa_reserve_say (vm, p);
  vlib_log_warn (bm->log_class,
		 "pool %v: reserve of %u blocks does not reach all %u workers",
		 p->name, p->reserve_count, nw);
}

/* not a unit: it finds the pool in bpa_main and rewrites the
 * settings under the barrier, then re-derives what depends on them. The
 * refusals it applies are shared with bpa_pool_init() by construction
 * (#375), and lab/vty-pool.sh sets every one of them on a box. */
clib_error_t *
bpa_pool_settings_set (u8 *name, bpa_filter_t filter, u8 max_blocks,
		       u8 initial_blocks, u32 alg, u32 alg_set, u16 tcp_mss,
		       u8 tcp_mss_auto, u8 util_alarm, u16 block_stride,
		       const bpa_timeouts_t *timeouts)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;
  uword *q;

  q = hash_get_mem (bm->pool_by_name, name);
  if (!q)
    return clib_error_return (0, "no such pool '%v'", name);

  if (tcp_mss && (tcp_mss < BPA_MSS_MIN || tcp_mss > BPA_MSS_MAX))
    return clib_error_return (
      0, "tcp-mss %u is outside %u-%u", tcp_mss, BPA_MSS_MIN, BPA_MSS_MAX);
  if (util_alarm > 100)
    return clib_error_return (0, "utilisation-alarm %u: expected 1-100 percent",
			      util_alarm);

  p = pool_elt_at_index (bm->pools, q[0]);

  /* THE TWO STRIDE REFUSALS THAT ARE FACTS ABOUT THE POOL ITSELF rather than
   * about this box's thread count - a stride on a pool that is not dynamic,
   * and one that steps off the subscriber's own public address - through
   * the one function bpa_pool_init() refuses them with. Needed here as well
   * as there, because `block-stride` rides on bpa_pool_add_del as well as on
   * this message and a pool being created never comes through this call;
   * and shared rather than copied, because this file needs a VPP to compile
   * and the copy that lived here was measured by nothing. The argument for
   * both, and the sentence, are on bpa_pool_stride_refusal() (#375). */
  {
    clib_error_t *e = bpa_pool_stride_refusal (p, block_stride);

    if (!e)
      e = bpa_pool_initial_refusal (p, initial_blocks, max_blocks);
    if (e)
      return e;
  }

  /* Under the barrier: the datapath reads the filtering mode, the ALG mask and
   * the clamp on the packet path, and a half-applied set of them is a pool
   * behaving as neither the old configuration nor the new one. */
  vlib_worker_thread_barrier_sync (bm->vlib_main);
  p->filter = filter;
  p->max_blocks = max_blocks ? max_blocks : 1;
  /* In place for the stride's reason below: it decides what the NEXT new
   * subscriber is given, and nothing already held moves (#462). */
  p->initial_blocks = initial_blocks;
  p->alg = alg;
  p->alg_set = alg_set;
  p->tcp_mss = tcp_mss;
  p->tcp_mss_auto = tcp_mss_auto;
  p->util_alarm = util_alarm;
  /* In place, and safe there: the stride decides where the NEXT extension
   * goes. Nothing already handed out moves, and bpa_forward() never reads it,
   * so no subscriber's own block changes (docs/pool-resize.md). */
  p->block_stride = block_stride;
  p->timeouts = *timeouts;
  vlib_worker_thread_barrier_release (bm->vlib_main);

  /* The VTY changes per-pool timeouts here, so this is where that change is
   * warned about. No vlib_main_t: the operator is at the other end of an API
   * call, and prismash relays it from "show bpa timeouts". */
  bpa_timeouts_warn (0, timeouts, p->name);
  /* And the stride, which reaches a live pool through this call and a new one
   * through bpa_pool_add(). Both are needed and neither covers the other: a
   * pool being created carries its stride on the creation message and never
   * comes through here at all. prismash relays the rows from "show bpa pool"
   * after either. */
  bpa_stride_warn (0, p);

  bpa_audit ("settings", p, 0);
  return 0;
}

/* not a unit: it builds an address set and stores it on a pool in
 * bpa_main. The set builder is bpa_addr_set_build() in bpa_pool.h, which
 * test/test_bpa_pool.c drives; lab/bypass.sh sends packets past it. */
clib_error_t *
bpa_bypass_set (u8 *name, int is_dst, const u32 *bases, const u32 *counts,
		u32 n)
{
  bpa_main_t *bm = &bpa_main;
  bpa_addr_set_t built = { 0 };
  clib_error_t *e;
  bpa_pool_t *p;
  uword *q;

  q = hash_get_mem (bm->pool_by_name, name);
  if (!q)
    return clib_error_return (0, "no such pool '%v'", name);

  /* Built before the barrier and copied inside it: a half-built set on a
   * running datapath would bypass whatever the partial ranges happened to
   * cover.
   *
   * An empty list is legal here and is how a bypass is removed, so it is not
   * handed to bpa_addr_set_build() - which refuses an empty set, correctly,
   * for a pool's own prefixes. The zeroed set below has count 0, which
   * bpa_is_bypassed() reads as "nothing is bypassed". */
  if (n)
    {
      e = bpa_addr_set_build (&built, bases, counts, n);
      if (e)
	return e;
    }

  p = pool_elt_at_index (bm->pools, q[0]);
  vlib_worker_thread_barrier_sync (bm->vlib_main);
  if (is_dst)
    p->bypass_dst = built;
  else
    p->bypass_src = built;
  vlib_worker_thread_barrier_release (bm->vlib_main);

  bpa_audit (is_dst ? "bypass-destination" : "bypass-source", p, 0);
  return 0;
}

/* not a unit: it stores two numbers on a pool in bpa_main. What they do
 * is measured on a box by lab/limits.sh, which fills a pool to its session
 * limit and reads the refusal. */
clib_error_t *
bpa_limits_set (u8 *name, u32 max_sessions, u32 max_rate)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;
  uword *q;

  q = hash_get_mem (bm->pool_by_name, name);
  if (!q)
    return clib_error_return (0, "no such pool '%v'", name);

  p = pool_elt_at_index (bm->pools, q[0]);

  /* Two words the session-creation path reads on a worker; mutate them under
   * the barrier so no creation sees one old and one new. */
  vlib_worker_thread_barrier_sync (bm->vlib_main);
  p->max_sessions = max_sessions;
  p->max_rate = max_rate;
  vlib_worker_thread_barrier_release (bm->vlib_main);

  bpa_audit ("limits", p, 0);
  return 0;
}

/*
 * NAT64 pools against each other (#485): an IPv6 subscriber prefix belongs
 * to one pool, and one NAT64 prefix to one pool - the reverse direction
 * derives a reply's source from the pool's prefix, so two pools on one would
 * leave the second's replies unattributable.
 */
/* not a unit: it walks bpa_main's pools; the overlap rule is
 * bpa_ip6_prefixes_overlap(), which test/test_bpa_nat64.c holds. */
clib_error_t *
bpa_nat64_check_overlap (const bpa_pool_t *cfg)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;
  u32 i, j;

  if (!cfg->is_nat64)
    return 0;
  pool_foreach (p, bm->pools)
    {
      /* The same name is the pool this one replaces, when it is a commit
       * check asking; bpa_pool_add() has refused a live name already. */
      if (!p->is_nat64 || p->deleted || vec_is_equal (p->name, cfg->name))
	continue;
      if (p->nat64_len == cfg->nat64_len
	  && !memcmp (p->nat64_prefix, cfg->nat64_prefix, 16))
	return clib_error_return (0, "NAT64 prefix already used by pool %v",
				  p->name);
      for (i = 0; i < cfg->n_inside6; i++)
	for (j = 0; j < p->n_inside6; j++)
	  if (bpa_ip6_prefixes_overlap (cfg->inside6_addr[i],
					cfg->inside6_len[i],
					p->inside6_addr[j], p->inside6_len[j]))
	    return clib_error_return (0, "inside IPv6 prefix overlaps pool %v",
				      p->name);
    }
  return 0;
}

/* not a unit: it inserts a pool into bpa_main's pool and hash under the
 * worker barrier. What it validates with is bpa_pool_validate() above and
 * bpa_pool_init(), which test/test_bpa_pool.c drives against
 * ref/bpa_model.py. */
clib_error_t *
bpa_pool_add (bpa_pool_t *cfg, u32 *pool_index)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;
  clib_error_t *e;
  uword *q;

  q = hash_get_mem (bm->pool_by_name, cfg->name);
  if (q)
    return clib_error_return (0, "pool already exists");

  e = bpa_check_local_overlap (&cfg->outside, cfg->name);
  if (e)
    return e;
  e = bpa_check_pool_overlap (cfg);
  if (e)
    return e;
  /* NAT64 (#485): what phase 2 does not do is refused, not accepted inert,
   * and its IPv6 prefixes against every other NAT64 pool's. */
  {
    const char *why = bpa_nat64_unsupported (cfg);

    if (why)
      return clib_error_return (0, "%s", why);
    e = bpa_nat64_check_overlap (cfg);
    if (e)
      return e;
  }

  /* Workers read bm->pools on the datapath; mutate it under the barrier. */
  vlib_worker_thread_barrier_sync (bm->vlib_main);

  pool_get_zero (bm->pools, p);
  *p = *cfg;

  e = bpa_pool_init (p);
  if (e)
    {
      pool_put (bm->pools, p);
      vlib_worker_thread_barrier_release (bm->vlib_main);
      return e;
    }

  /* Validated against the pool as initialised, then owned by it. A pool
   * created with a forward it would refuse is a pool whose configuration and
   * behaviour disagree from the first packet. */
  e = bpa_statics_validate (p, cfg->statics);
  if (e)
    {
      pool_put (bm->pools, p);
      vlib_worker_thread_barrier_release (bm->vlib_main);
      return e;
    }

  p->name = vec_dup (cfg->name);
  p->statics = vec_dup (cfg->statics);
  hash_set_mem (bm->pool_by_name, p->name, p - bm->pools);

  /* Sized here so the datapath never grows a vector: every worker gets a slot
   * for this pool before any packet can reach it. */
  {
    u32 i;

    for (i = 0; i < vec_len (bm->per_thread); i++)
      vec_validate (vec_elt_at_index (bm->per_thread, i)->n_bypassed,
		    p - bm->pools);
    /* Its row in the statistics segment, for the same reason (#504). */
    bpa_stats_pool_add (p - bm->pools, p->name);
  }
  bpa_outside_window_update ();

  /*
   * One allocator per worker, in both modes that have blocks to hand out.
   *
   * Dynamic pools allocate every block on demand. A deterministic pool
   * allocates nothing at all UNLESS it carries a reserve, and then it
   * allocates only out of that: the reserve is the only set of blocks the
   * formula has not already given to somebody. Both use the same allocator
   * over a different index space (issue #39).
   */
  if (p->mode == BPA_MODE_DYNAMIC || p->block_reserve)
    {
      u32 i, nw, w;
      int reserve = p->mode != BPA_MODE_DYNAMIC;

      for (i = 0; i < vec_len (bm->per_thread); i++)
	{
	  bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, i);

	  /* thread 0 only carries blocks when there are no workers, and a
	   * worker that only receives carries none (#466) */
	  if (bm->n_workers
	      && (i >= vec_len (bm->thread_w) || bm->thread_w[i] == ~0))
	    continue;

	  bpa_worker_id (bm, i, &w, &nw);
	  vec_validate (pt->dyn, p - bm->pools);
	  e = bpa_dyn_init (vec_elt_at_index (pt->dyn, p - bm->pools), p, w,
			    nw, reserve);
	  if (e)
	    {
	      hash_unset_mem (bm->pool_by_name, p->name);
	      vec_free (p->name);
	      vec_free (p->statics);
	      pool_put (bm->pools, p);
	      vlib_worker_thread_barrier_release (bm->vlib_main);
	      return e;
	    }
	}

      /*
       * And the pins, once every slice exists.
       *
       * This is the path a restart takes. The configuration replay creates the
       * pool with the forwards it carries, and a forward on a dynamic pool is
       * a pin - so a box comes back holding exactly the blocks its
       * configuration says, without anything having been saved but the
       * configuration. Nothing is checked for a current holder here: the
       * allocators above were created empty a few lines ago, so there is
       * nobody to take a block from.
       */
      for (i = 0; i < vec_len (p->statics); i++)
	bpa_static_pin (p - bm->pools, p, &p->statics[i], 1);
    }

  if (p->is_nat64)
    bm->n_nat64++;
  vlib_worker_thread_barrier_release (bm->vlib_main);
  /* The inside interfaces take their IPv6 half with the first NAT64 pool. */
  if (p->is_nat64)
    bpa_nat64_interfaces_update ();

  /*
   * A reserve too small for this box's workers is warned about HERE, and here
   * is the only place it needs saying.
   *
   * The reserve is not on bpa_pool_settings_set at all - see the 0.13.0 note
   * in bpa.api - because it is taken out of capacity, capacity is the divisor
   * under the blocked policy, and changing it re-maps subscribers. So the VTY
   * classifies `reserve` as a mapping field, and an operator who LOWERS a
   * reserve on a live pool does not edit it in place: the pool is removed and
   * added again, through this function. Creation and every later change are
   * the same call, which is why one call site covers both - and why the
   * warning cannot be missed on the day the number is lowered.
   *
   * No vlib_main_t, for the reason bpa_pool_settings_set() gives above: the
   * operator is at the other end of an API call rather than at a terminal, so
   * this goes to the log, and prismash relays it from "show bpa pool" - which
   * says the same sentence through the same function.
   *
   * After the barrier and after bpa_pool_init(), because reserve_count is
   * computed in there: warning before it would read a zero the pool never has.
   */
  bpa_reserve_warn (0, p);

  /*
   * A stride this box's workers cannot honour is warned about HERE, and this
   * is the call site an operator actually reaches.
   *
   * `block-stride` rides on the creation message as well as on
   * bpa_pool_settings_set, so a pool created with a stride arrives here and
   * nowhere else: the VTY sends the whole pool in one bpa_pool_add_del and
   * skips the settings call entirely, because a pool being created already
   * carries what that call would set. Warning only in settings_set therefore
   * left creation - which is where the operator types the number for the
   * first time - saying nothing at all, not even to the log.
   *
   * No vlib_main_t, for the reason bpa_pool_settings_set() gives above: the
   * operator is at the other end of an API call rather than at a terminal, so
   * this goes to the log, and prismash relays the rows from "show bpa pool" -
   * which says the same sentence through the same function.
   *
   * After the barrier and after bpa_pool_init(), because the arithmetic reads
   * blocks_mapped and the policy's stride unit: warning before it would
   * measure a pool whose derived values are all still zero.
   */
  bpa_stride_warn (0, p);
  bpa_stats_pool_describe (p - bm->pools, p);

  bpa_audit ("add", p, 0);

  if (pool_index)
    *pool_index = p - bm->pools;
  return 0;
}

/*
 * How much of a pool delete happens inside one barrier acquisition.
 *
 * Measured on the two-worker lab (lab/pool-del.sh), which reads the barrier
 * hold out of VPP's own accounting rather than inferring it from packet
 * counts. Freeing a session costs about 0.6 us - two bihash deletions, the
 * subscriber bookkeeping and a hold-down entry - and stepping over one that
 * belongs to another pool costs a few nanoseconds.
 *
 * Before there was a budget, deleting a pool stopped forwarding for 6.0 ms at
 * 10 000 sessions and 37 ms at 49 000: a straight line through the size of the
 * table, which puts the design target of 200k subscribers with several
 * sessions each into the hundreds of milliseconds. Every subscriber on the box
 * loses that, for a pool delete an ordinary `commit` can reach (issue #69).
 * With these budgets the same two deletes hold the barrier for 1.5 ms and
 * 1.4 ms - the line is flat, which is the whole point.
 *
 * 2000 frees is about a millisecond of barrier; 64k steps is a fifth of one,
 * and bounds the case the free budget does not - a pool with a handful of
 * sessions in a table full of somebody else's.
 */
#define BPA_DEL_FREE_BUDGET 2000
#define BPA_DEL_SCAN_BUDGET 65536
/*
 * And how long the workers get in between.
 *
 * Releasing the barrier is not the same as giving the workers time on it: the
 * flag goes down and goes straight back up, and a delete that yields nothing
 * but the flag is still an outage, only a slower one. A millisecond back for
 * every millisecond taken leaves the datapath at about half its throughput
 * for the length of the delete rather than at none of it - measured, a 10 000
 * session delete is 9 acquisitions over 11 ms.
 */
#define BPA_DEL_YIELD 1e-3

/*
 * Hands the workers the CPU with the barrier open.
 *
 * A spin rather than vlib_process_suspend(): this runs from a CLI process and
 * from an API handler, and suspending the latter would let the next request
 * in while a pool is half deleted. Blocking the main thread for the length of
 * a delete costs nothing that is running - it is the thread that called us.
 */
/* not a unit: it spins on vlib_time_now() to let workers past the
 * barrier, which is VPP's clock and VPP's barrier. What it protects is
 * measured by lab/pool-del.sh, which deletes a pool under traffic. */
static void
bpa_barrier_yield (vlib_main_t *vm)
{
  f64 until = vlib_time_now (vm) + BPA_DEL_YIELD;

  while (vlib_time_now (vm) < until)
    CLIB_PAUSE ();
}

/*
 * Every block the pool is still holding, as one RFC 8158 event each.
 *
 * The event is a parameter and every caller walks the same set. Deleting a
 * pool and stopping the box both give the blocks back (natEvent 17); a drain
 * that was not followed by a stop takes them back (natEvent 16). Two walks
 * that could drift apart would leave the log with a release the box never
 * re-opened, which is the same kind of hole as a release that never happened.
 *
 * A block held at a pool delete is freed by bpa_dyn_free() and never
 * reaches the reap, so nothing on the ordinary release path runs for it. In
 * dynamic mode the allocation log IS the connection record the Marco Civil
 * requires to be kept for a year, and the question it answers is "who held
 * this public port at this instant" - read as the last allocation before the
 * instant asked about. An allocation with no release says the subscriber held
 * those ports from then on, for as long as the log is kept, so deleting a pool
 * used to name a subscriber who had already been disconnected. That is the
 * worst direction for this particular error to go in (#215).
 *
 * The record is stamped at the delete, not at some earlier moment when the
 * block stopped being used: the box did not observe that moment and a record
 * that guessed at it would be a fabricated one. The same argument
 * bpa_log_pins_replay() makes for the other half of this hole.
 *
 * Queued rather than emitted, through the one call every other block event
 * goes through. A delete arrives from a CLI process and from an API handler,
 * and the export path may only be reached from process context - the queue is
 * what bpa-expire-process drains, under the barrier, a second later.
 *
 * A DETERMINISTIC POOL WITH NO RESERVE IS NOT AFFECTED, and the check says so
 * rather than relying on the loop finding nothing: it has no allocator here
 * and no events at all - the mapping is the formula plus the dated
 * configuration trail, which is why that mode logs nothing per subscriber - so
 * a release emitted here would be the second half of a pair whose first half
 * was never written, and a collector reading it would have to invent the
 * allocation.
 *
 * With a reserve it IS affected, and for exactly the reason the paragraph
 * above gives. A reserve block is the one thing on a deterministic pool the
 * formula does NOT produce, so it was announced with a natEvent 16 when it was
 * handed out, and a delete that took it back in silence would leave the
 * collector holding an allocation that never ends - naming a subscriber who
 * has been disconnected, for as long as the log is kept (#215).
 */
/* not a unit: it walks one pool's blocks under the worker barrier
 * and queues a record for each. What a record contains is
 * test/test_bpa_log_syslog.c's and test/test_bpa_audit.c's; lab/ipfix.sh
 * reads them off a collector. */
static u32
bpa_pool_log_blocks (const bpa_pool_t *p, u32 pool_index, u8 event)
{
  bpa_main_t *bm = &bpa_main;
  u32 i, n = 0;

  if (p->mode != BPA_MODE_DYNAMIC && p->block_reserve == 0)
    return 0;

  for (i = 0; i < vec_len (bm->per_thread); i++)
    {
      bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, i);
      bpa_dyn_t *d;
      u32 w, nw, local;

      if (pool_index >= vec_len (pt->dyn))
	continue;
      d = vec_elt_at_index (pt->dyn, pool_index);
      if (!d->active)
	continue;

      bpa_worker_id (bm, i, &w, &nw);

      /*
       * owner[] rather than by_sub: an extension block has no entry in by_sub
       * and is held by a subscriber exactly like a primary one, and a pinned
       * block is an owner too. Whatever the block is being held for, it was
       * announced with a natEvent 16 and is about to disappear.
       */
      /*
       * Every slot, borrowed ones included (#210): a block this worker took
       * from another's slice is held by one of ITS subscribers, was
       * announced with a natEvent 16 by this worker, and is about to
       * disappear with the pool. The lender's own walk skips it, because
       * there it reads as lent.
       */
      for (local = 0; local < bpa_dyn_slots (d); local++)
	{
	  bpa_block_t blk;
	  u32 ip_priv;

	  if (d->owner[local] == BPA_NO_BLOCK)
	    continue;
	  /*
	   * Lent to another worker (#210): the subscriber holding it is on
	   * that worker's books, and this walk would announce the event twice
	   * - once here naming the sentinel, once there naming the
	   * subscriber. The borrower is the one that owes the record.
	   */
	  if (d->owner[local] == BPA_LENT)
	    continue;
	  if (bpa_set_addr_of (&p->inside, d->owner[local], &ip_priv))
	    continue;

	  if (bpa_dyn_addr (d, p, bpa_dyn_block_at (d, local, w, nw), &blk))
	    continue;
	  bpa_log_queue_event (pt, event, ip_priv, blk.ip_pub, blk.port_lo,
			       blk.port_hi, pool_index);
	  n++;
	}
    }
  return n;
}

/*
 * Deleting a pool means freeing every session it produced, and there may be
 * millions of them.
 *
 * Sessions MUST NOT outlive the definition that produced them: VPP reuses the
 * pool index, so a pool created afterwards would inherit sessions carrying the
 * OLD arithmetic - a subscriber still translated onto a public port that the
 * new mapping assigns to somebody else, with nothing anywhere saying so.
 * Redefining a pool is meant to invalidate its sessions.
 *
 * That is why this is still synchronous: it returns only once the last one is
 * gone, so the `del` and the `add` of a `commit` that replaces a pool cannot
 * overlap, and the replacement can never hand out a public port the old
 * definition is still using. What changed is that the walk no longer happens
 * inside ONE barrier acquisition. The pool is detached first, which is what
 * stops the datapath creating more of what we are freeing, and the rest runs
 * in budgeted chunks with the workers forwarding in between.
 *
 * This is also why the two entry points that reach it - the "bpa pool" CLI
 * command and the bpa_pool_add_del API message - are declared mp-safe. Their
 * dispatchers take the barrier around the whole call otherwise, and releasing
 * a barrier we did not take is a no-op: the chunks would be real and the
 * outage would not have moved.
 */
/* not a unit: it takes the worker barrier, drains the pool's blocks and
 * removes it from bpa_main. lab/pool-del.sh is where a pool is deleted on a
 * box and the sessions that were on it are read. */
clib_error_t *
bpa_pool_del (u8 *name)
{
  bpa_main_t *bm = &bpa_main;
  vlib_main_t *vm = bm->vlib_main;
  u32 pool_index, i;
  bpa_pool_t *p;
  uword *q;

  q = hash_get_mem (bm->pool_by_name, name);
  if (!q)
    return clib_error_return (0, "no such pool");
  pool_index = q[0];

  /*
   * Detached. Two words, so this acquisition is as short as any other
   * configuration change: after it the name resolves to nothing and no worker
   * will bind another session to the pool, which is what makes the walk below
   * terminate instead of chasing traffic it is still creating.
   */
  vlib_worker_thread_barrier_sync (vm);
  p = pool_elt_at_index (bm->pools, pool_index);
  bpa_audit ("del", p, 0);
  p->deleted = 1;
  hash_unset_mem (bm->pool_by_name, p->name);
  vlib_worker_thread_barrier_release (vm);

  /*
   * The sessions, a chunk per acquisition. Still under the barrier for each
   * chunk - session state is per-worker and freeing an entry races with the
   * worker reading it - but never for the whole table.
   */
  for (i = 0; i < vec_len (bm->per_thread); i++)
    {
      bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, i);
      u32 cursor = 0;
      int done;

      if (!pt->initialised)
	continue;

      do
	{
	  vlib_worker_thread_barrier_sync (vm);
	  done = bpa_pool_drain (pt, pool_index, &cursor, BPA_DEL_SCAN_BUDGET,
				 BPA_DEL_FREE_BUDGET, vlib_time_now (vm));
	  vlib_worker_thread_barrier_release (vm);

	  if (!done)
	    bpa_barrier_yield (vm);
	}
      while (!done);
    }

  /*
   * And the definition itself, once nothing points at it. The public window
   * narrows here and not at the top: while the pool was draining, its
   * addresses still had live mappings on them, and a window that had already
   * closed would have sent their replies past the translation.
   */
  vlib_worker_thread_barrier_sync (vm);
  p = pool_elt_at_index (bm->pools, pool_index);
  /* Before the free, because the allocator is the only thing that knows which
   * subscriber each block belongs to. */
  bpa_pool_log_blocks (p, pool_index, BPA_NAT_EVENT_BLOCK_DEALLOC);
  for (i = 0; i < vec_len (bm->per_thread); i++)
    {
      bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, i);

      if (pool_index < vec_len (pt->dyn))
	bpa_dyn_free (vec_elt_at_index (pt->dyn, pool_index));
      /* And what this worker remembered about the blocks of this pool (#397).
       * The releases above took their own entries out; what is left belongs to
       * releases whose record was never queued, and the pool index they are
       * filed under is about to be handed to the next pool created. */
      bpa_block_ident_forget_pool (&pt->block_ident, pool_index);
    }
  /*
   * And the loans filed against this pool (#210), for the same reason as the
   * line above: the index is about to be handed to the next pool created,
   * whose blocks start at zero like this one's, and a stale entry would
   * steer a reply for a block that means something else now to a worker that
   * never borrowed anything.
   */
  bpa_lent_clear_pool (bm, pool_index);
  bpa_stats_pool_del (pool_index);
  if (p->is_nat64 && bm->n_nat64)
    bm->n_nat64--;
  vec_free (p->name);
  vec_free (p->statics);
  pool_put (bm->pools, p);
  bpa_outside_window_update ();
  vlib_worker_thread_barrier_release (vm);
  /* The last NAT64 pool gone: the inside interfaces drop their IPv6 half. */
  bpa_nat64_interfaces_update ();
  return 0;
}

/*
 * Every block this box is still holding, as one RFC 8158 event each.
 *
 * Not static and not local to a caller, because the two callers must walk
 * exactly the same set: bpa_stop_drain() gives the blocks back and the
 * re-announcement in bpa-expire-process takes them back.
 */
/* not a unit: the same for every pool, at the moment logging is
 * switched on. lab/ipfix.sh drives that switch on a box. */
u32
bpa_blocks_log_all (u8 event)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;
  u32 n = 0;

  pool_foreach (p, bm->pools)
    n += bpa_pool_log_blocks (p, p - bm->pools, event);
  return n;
}

/*
 * Every block this box is still holding, released before the box stops.
 *
 * WHY THIS IS THE SAME BUG AS #215 AND NOT A TIDINESS ONE. In dynamic mode the
 * allocation log IS the connection record the Marco Civil requires to be kept
 * for a year, and it is read as "the last allocation before the instant asked
 * about". An allocation with no release says the subscriber held those ports
 * from then on, for as long as the log is kept - so a planned reboot used to
 * name a subscriber who had already been disconnected, and the box came back
 * up and emitted a SECOND natEvent 16 for every pinned block with no 17
 * between them (bpa_log_pins_replay()). A pool being deleted was the rarer
 * case; this is every upgrade and every reboot (#217).
 *
 * WHERE IT HANGS, AND WHY IT IS IN TWO PLACES. The answer was measured, not
 * chosen: the UDP export does not leave from a kernel socket, it is a packet
 * the DATAPLANE sends - vnet_ipfix_exp_send_buffer() writes a buffer and hands
 * a frame to ip4-lookup, and the graph is what puts it on the wire. So the
 * moment the graph stops running, UDP export stops working, and the graph
 * stops running before any in-process exit hook is reached.
 *
 * Measured on this build, with a real collector on the far side of the wire
 * and one block held at the stop:
 *
 *   VLIB_MAIN_LOOP_EXIT function, on SIGTERM
 *     the hook runs   - yes, after VPP's own "received SIGTERM ... exiting"
 *     blocks found    - 1
 *     UDP             - record written (n_records advanced, flush called) and
 *                       never seen by the collector: the frame is enqueued to
 *                       a graph that will not be dispatched again
 *     TCP             - natEvent 17 decoded by the collector at the stop
 *                       instant; that exporter writes to a kernel socket and
 *                       needs no graph
 *
 * That is why there are two entry points and one body:
 *
 *   - THE EXIT HOOK, here. It covers every graceful exit there is - systemd's
 *     SIGTERM, a kill from anywhere else, `vppctl quit` - and it is the only
 *     thing that covers them for the TCP transport, which is the one
 *     docs/logging.md calls the log that cannot lose a record. Over UDP it
 *     writes records the graph then swallows, which costs nothing and is not
 *     pretended otherwise.
 *
 *   - "bpa stop-drain", run from an ExecStop in a vpp.service drop-in, BEFORE
 *     systemd sends the signal. It runs in a CLI process inside the still
 *     running graph, so the UDP records actually leave, on the very path every
 *     other record takes and the operator configured. This is the case the
 *     issue calls the commonest of the three: every planned reboot and every
 *     upgrade goes through `systemctl stop`.
 *
 * WHAT THE OTHER TWO WOULD HAVE COST:
 *
 *   - A SIGTERM handler of our own would have to be installed over VPP's,
 *     which is how a dataplane stops being stopped properly, and it would run
 *     in signal context: the drain takes the worker barrier and appends to
 *     vectors the main thread may be in the middle of, neither of which is
 *     async-signal-safe. And it would buy nothing the exit hook does not
 *     already have, because it still could not run the graph.
 *
 *   - An ExecStop in prisma-routing.service rather than in a vpp.service drop-in
 *     would be in the wrong unit twice over: that unit is a oneshot that
 *     applies a configuration and holds no blocks, and it says in as many
 *     words why it has no ExecStop - stopping the control plane must not touch
 *     a production pool. The blocks are in the VPP process, so the drop-in
 *     goes where the appliance already ships two others.
 *
 * THE TIMESTAMP IS THE STOP INSTANT, not some earlier moment when the block
 * stopped being used: the box did not observe that moment and a record that
 * guessed at it would be a fabricated one. The same argument
 * bpa_pool_log_blocks() and bpa_log_pins_replay() both make.
 *
 * THIS DOES NOT CLOSE THE HOLE ON ITS OWN, and must not be read as if it did.
 * A power loss, an OOM kill and a panic emit nothing, and cannot: there is no
 * instant at which the box could have written the record. A collector that
 * assumed after this that every allocation is closed would be wrong about
 * exactly the boxes that crashed. What tells those apart is the boot marker -
 * bpa_log_marker_emit(), and the argument for its shape in bpa_log.h.
 */
/* not a unit: it walks a pool's blocks releasing them, under the
 * barrier, so the log closes every allocation it opened. lab/ipfix-stop.sh
 * measures the drain on a box and lab/drain-slope.sh times it. */
u32
bpa_drain_blocks (vlib_main_t *vm)
{
  bpa_main_t *bm = &bpa_main;
  u32 n;

  /*
   * The barrier for the same reason bpa_pool_del() takes it: the walk reaches
   * into every thread's allocator and writes to every thread's log queue.
   */
  vlib_worker_thread_barrier_sync (vm);
  n = bpa_blocks_log_all (BPA_NAT_EVENT_BLOCK_DEALLOC);
  /*
   * Queued and drained in the same call, because there may be no later: the
   * one-second bpa-expire-process tick that normally puts records on the wire
   * does not run again after the exit hook.
   */
  bpa_log_drain (vm);
  bm->drained_at = vlib_time_now (vm);
  vlib_worker_thread_barrier_release (vm);

  /*
   * Said out loud on the box, because this is the one record whose absence
   * nobody can see from the collector: a box that stopped without draining
   * looks exactly like a box that is still running.
   */
  vlib_log_notice (bm->log_class, "stopping: released %u block(s) still held",
		   n);
  return n;
}

/* not a unit: the CLI's door to that drain, and the one
 * vpp-stop-drain.conf runs on the way down. test/test_bpa_dyn.c drives the
 * allocator it empties; lab/ipfix-stop.sh reads the records it writes. */
static clib_error_t *
bpa_stop_drain (vlib_main_t *vm)
{
  bpa_main_t *bm = &bpa_main;
  u32 saved;
  u64 before, lost, sys_before;

  if (!bpa_log_main.enabled && !bpa_log_tcp.enabled
      && !bpa_log_syslog.enabled)
    return 0;

  /*
   * The loss is counted from HERE, before the drain, and not from the flush
   * below (#435). When the ExecStop did not run - it is `ExecStop=-`, allowed
   * to fail - the drain is this hook's, and on a stalled collector the
   * releases it writes push the buffer past the spill threshold: an exporter
   * whose spill cannot be written drops them there, inside the drain. Counted
   * from after it, those bytes were in no file, in no collector, and in no
   * sentence either, on the one path whose sentence says it is the only
   * place the loss is said. Measured with 742000 bytes buffered on a full
   * filesystem and no ExecStop: nothing said, three runs out of three.
   */
  before = bpa_log_tcp.n_dropped;
  sys_before = bpa_log_syslog.n_dropped;

  /*
   * Already done by the ExecStop, moments ago. Emitting the same releases
   * again would leave the collector with two natEvent 17 against one 16, and
   * a release that closes an allocation twice is not tidier than one that
   * never arrives - it is a second wrong answer.
   */
  if (!bm->drained_at)
    bpa_drain_blocks (vm);

  /*
   * WHETHER OR NOT THE DRAIN RAN HERE. The latch above says the records were
   * WRITTEN, not that they left: the TCP exporter's socket is non-blocking,
   * so a collector that is connected and not reading leaves them in that
   * exporter's own buffer, and its spill to disk does not trigger below 1 MB.
   * Measured on this build with a collector stalled and 1501 blocks held:
   * 15372 bytes still in the buffer at the stop, and the natEvent 17 for every
   * one of those blocks lost with the process (#219).
   *
   * So this runs on both paths and after both of them. It is the last point at
   * which anything can be done about those bytes.
   */
  /*
   * What was LOST, as well as what was saved, and at a level that leaves the
   * process.
   *
   * docs/logging.md says a discard MUST be counted and never silent. On every
   * other path the counter is enough: the box keeps running and
   * `show bpa logging` reads it back. Here it is not. This function runs from
   * VLIB_MAIN_LOOP_EXIT, so the counter it increments dies with the process
   * moments later, and a number nobody can read is not a count (#270).
   *
   * journalctl is the only place this can still be read after the box has
   * stopped, and the notice below does not reach it at VPP's default log
   * level - measured on a run that spilled 82935 bytes, where the line is
   * absent from the process output entirely. Losing a record is an error, so
   * it goes out as one.
   */
  saved = bpa_log_tcp_flush_exit ();
  lost = bpa_log_tcp.n_dropped - before;

  if (saved)
    vlib_log_notice (bm->log_class,
		     "stopping: %u byte(s) the collector had not taken went to "
		     "the spill file, to be replayed on the next connection",
		     saved);
  if (lost)
    vlib_log_err (bm->log_class,
		  "stopping: %llu byte(s) of connection records could NOT be "
		  "written and are lost. The spill file at %s could not be "
		  "opened or could not be written to; these records are in no "
		  "file and in no collector, and this is the only place that "
		  "is said",
		  (unsigned long long) lost,
		  bpa_log_tcp.spill_path ? (char *) bpa_log_tcp.spill_path
					 : "(no path configured)");

  /* The syslog stream drains the same way and reports the same loss: a
   * record this box wrote and nothing took is lost whichever wire it was
   * bound for (#20). */
  if (bpa_log_syslog.enabled)
    {
      u32 sys_saved = bpa_log_stream_flush_exit (&bpa_log_syslog);
      u64 sys_lost = bpa_log_syslog.n_dropped - sys_before;

      if (sys_saved)
	vlib_log_notice (bm->log_class,
			 "stopping: %u syslog byte(s) the collector had not "
			 "taken went to the spill file, to be replayed on "
			 "the next connection",
			 sys_saved);
      if (sys_lost)
	vlib_log_err (bm->log_class,
		      "stopping: %llu syslog byte(s) were never written and "
		      "are lost, in no file and in no collector",
		      (unsigned long long) sys_lost);
    }
  return 0;
}

VLIB_MAIN_LOOP_EXIT_FUNCTION (bpa_stop_drain);

/*
 * The mirror of bpa_check_local_overlap(): an address configured after the
 * pool exists. We cannot refuse it from here, so we make the noise loud
 * enough that it is found before a customer does.
 */
/* not a unit: a VPP callback: the stack calls it when an
 * address is added to or removed from an interface, with an
 * ip4_main_t-registered function pointer that exists nowhere else.
 * lab/localaddr.sh is where a box's own address meets a pool. */
static void
bpa_ip4_address_change (ip4_main_t *im, uword opaque, u32 sw_if_index,
			ip4_address_t *address, u32 address_length,
			u32 if_address_index, u32 is_delete)
{
  bpa_main_t *bm = &bpa_main;
  vnet_main_t *vnm = vnet_get_main ();
  bpa_pool_t *p;
  u32 host, idx;

  if (is_delete)
    return;

  host = clib_net_to_host_u32 (address->as_u32);

  pool_foreach (p, bm->pools)
    {
      if (bpa_set_index_of (&p->outside, host, &idx) == 0)
	vlib_log_err (
	  bm->log_class,
	  "%U was configured on %U but belongs to public pool '%v'. The box's "
	  "own control-plane traffic on that address will be translated by "
	  "CGNAT. Remove the address or the pool.",
	  format_ip4_address, address, format_vnet_sw_if_index_name, vnm,
	  sw_if_index, p->name);
    }
}

/*
 * The set bpa_to_us() asks before the FIB (#489, bpa_local.h), rebuilt from
 * every IPv4 interface address the box has. `skip_*` names the one being
 * removed, for a callback that runs while it is still in the pool; `add` the
 * one being added, for one that runs before it is. A duplicate or an absent
 * skip is harmless: the set is a filter, and the FIB still answers.
 *
 * Swapped under the barrier, because a worker may be reading the old vector
 * in the middle of a probe; freed only once none can be.
 */
/* not a unit: it walks VPP's interface-address pool and takes the worker
 * barrier, which only a running dataplane has; the set it builds is
 * bpa_local_build(), which test/test_bpa_local.c holds. */
static void
bpa_local_rebuild (u32 skip_sw_if_index, u32 skip_addr, u32 add)
{
  bpa_main_t *bm = &bpa_main;
  ip_lookup_main_t *lm = &ip4_main.lookup_main;
  ip_interface_address_t *ia;
  u32 *addrs = 0, *slots, *old, skipped = 0;

  pool_foreach (ia, lm->if_address_pool)
    {
      ip4_address_t *a = ip_interface_address_get_address (lm, ia);

      if (!skipped && ia->sw_if_index == skip_sw_if_index
	  && a->as_u32 == skip_addr)
	{
	  skipped = 1;
	  continue;
	}
      vec_add1 (addrs, a->as_u32);
    }
  if (add)
    vec_add1 (addrs, add);
  slots = bpa_local_build (addrs);
  vec_free (addrs);

  vlib_worker_thread_barrier_sync (vlib_get_main ());
  old = bm->local_addrs;
  bm->local_addrs = slots;
  vlib_worker_thread_barrier_release (vlib_get_main ());
  vec_free (old);
}

/* not a unit: VPP's interface-address callback, which only passes its
 * arguments to bpa_local_rebuild(). */
static void
bpa_local_address_change (ip4_main_t *im, uword opaque, u32 sw_if_index,
			  ip4_address_t *address, u32 address_length,
			  u32 if_address_index, u32 is_del)
{
  if (is_del)
    bpa_local_rebuild (sw_if_index, address->as_u32, 0);
  else
    bpa_local_rebuild (~0, 0, address->as_u32);
}

/* not a unit: the plugin's init, called once by VPP with the graph up. */
static clib_error_t *
bpa_init (vlib_main_t *vm)
{
  /* The accounting listener starts closed. Zero is a VALID file descriptor -
   * it is stdin - so a struct that begins as zeros would have this box
   * reading the console for RADIUS packets on the first service pass (#21). */
  bpa_radius.fd = -1;
  /* The two TCP log streams, for the same reason (#551). */
  bpa_log_streams_init ();

  bpa_main_t *bm = &bpa_main;
  vlib_thread_main_t *tm = vlib_get_thread_main ();
  clib_error_t *e;
  u32 i;

  bm->vlib_main = vm;
  bm->vnet_main = vnet_get_main ();
  {
    ip4_add_del_interface_address_callback_t cb = {
      .function = bpa_local_address_change,
    };
    vec_add1 (ip4_main.add_del_interface_address_callbacks, cb);
  }
  bm->pool_by_name = hash_create_vec (0, sizeof (u8), sizeof (uword));
  bm->log_class = vlib_log_register_class ("bpa", 0);
  bm->audit_path = format (0, "/var/log/prisma-routing/config-history.log%c", 0);
  vec_dec_len (bm->audit_path, 1);
  /*
   * Explicitly, because zero is a real uid. bpa_main starts zeroed, so
   * without this every change made before an API request ever arrived would
   * be recorded as "by uid 0" - root - which is not merely missing
   * attribution, it is FALSE attribution, and a trail that names the wrong
   * person is worse than one that names nobody.
   */
  bm->actor_uid = ~0u;

  /*
   * Empty, with a minimum of ~0u rather than the zero bpa_main starts at:
   * zero is the smallest MTU there is, so an empty set left at zero would
   * claim to have found the narrowest path on the box.
   */
  bpa_auto_mss_init (&bm->auto_mss_track, 0, 0);

  /*
   * When this incarnation started. Latched here, once: see bpa_main_t, and
   * bpa_log.h for what a collector reads it for.
   */
  bm->boot_wall = unix_time_now ();

  /*
   * Where the chain had got to. Without this a restart starts a second chain
   * and every verification afterwards reports a break at the restart, which
   * trains whoever runs it to ignore the answer - and an alarm that is always
   * on is an alarm that is off.
   */
  bpa_audit_recover ();

  {
    ip4_main_t *im = &ip4_main;
    ip4_add_del_interface_address_callback_t cb = {
      .function = bpa_ip4_address_change,
      .function_opaque = 0,
    };
    vec_add1 (im->add_del_interface_address_callbacks, cb);
  }

  bm->timeouts.udp = BPA_TIMEOUT_UDP;
  bm->timeouts.icmp = BPA_TIMEOUT_ICMP;
  bm->timeouts.tcp_trans = BPA_TIMEOUT_TCP_TRANS;
  bm->timeouts.tcp_estab = BPA_TIMEOUT_TCP_ESTAB;
  bm->timeouts.tcp_syn = BPA_TIMEOUT_TCP_SYN;
  bm->timeouts.gre = BPA_TIMEOUT_GRE;
  bm->timeouts.port_holddown = BPA_PORT_HOLDDOWN;
  bm->timeouts.alg_expect = BPA_TIMEOUT_ALG_EXPECT;
  bm->block_holddown = BPA_BLOCK_HOLDDOWN;
  bm->hash_buckets = BPA_HASH_BUCKETS_DEFAULT;
  bm->hash_memory = BPA_HASH_MEMORY_DEFAULT;

  /*
   * Subscribers are spread over the workers with the same reciprocal used
   * by the pool split, so the worker count need not be a power of two.
   * With no workers configured everything stays on thread 0 and the
   * handoff nodes are never put on the feature arc.
   */
  {
    u32 *all = 0, k;

    for (k = 0; k < vlib_num_workers (); k++)
      vec_add1 (all, k);
    bpa_set_owners (bm, all);
  }

  bm->in2out_fq_index =
    vlib_frame_queue_main_init (bpa_in2out_node.index, 0);
  bm->out2in_fq_index =
    vlib_frame_queue_main_init (bpa_out2in_node.index, 0);

  /*
   * The ESP binding table, shared by every worker.
   *
   * Sized well below the session tables and on purpose: it holds one entry
   * per (public address, peer gateway) with an IPsec tunnel up, which in a
   * residential mix is orders of magnitude fewer than sessions. The size is a
   * virtual reservation and the OOM killer counts virtual - that lesson is
   * written up over BPA_HASH_BUCKETS_DEFAULT, and it applies here too.
   */
  clib_bihash_init_16_8 (&bm->shared, "bpa-shared", BPA_SHARED_BUCKETS_DEFAULT,
			 BPA_ESP_MEMORY_DEFAULT);

  /* One session table per vlib main, worker or not: index by thread. */
  vec_validate (bm->per_thread, tm->n_vlib_mains - 1);
  /* And one block-event ring each, in a segment of their own (#550). */
  e = bpa_log_seg_create (tm->n_vlib_mains);
  if (e)
    return e;
  for (i = 0; i < vec_len (bm->per_thread); i++)
    {
      e = bpa_per_thread_init (vec_elt_at_index (bm->per_thread, i), i);
      if (e)
	return e;
    }

  bpa_stats_init ();
  return 0;
}

VLIB_INIT_FUNCTION (bpa_init);

/*
 * The workers that own subscribers (#466): `wids` is a vector of VPP worker
 * ids, taken over by the call. Every worker by default; fewer only from
 * startup.conf, before any pool exists - a pool's dynamic slices are sharded
 * by this count, so it is never changed under one.
 */
/* not a unit: it resets bpa_main's worker tables from VPP's worker count;
 * the table is bpa_thread_table() in bpa_workers.h, which
 * test/test_bpa_workers.c holds (#472), and lab/cgnat-workers.sh starts a
 * dataplane with a split and sends traffic through it. */
void
bpa_set_owners (bpa_main_t *bm, u32 *wids)
{
  vec_free (bm->owner_wid);
  bm->owner_wid = wids;
  bm->n_workers = vec_len (wids);
  vec_free (bm->thread_w);
  bm->thread_w = bpa_thread_table (wids, vlib_num_workers ());
  bpa_divisor_init (&bm->workers, bm->n_workers ? bm->n_workers : 1);
}

/*
 * `bpa { cgnat-workers <list> }` in startup.conf (#466): the VPP worker ids
 * that translate, the rest only receive and hand over. Refused rather than
 * trimmed when it names a worker this dataplane does not have, or none -
 * a box whose CGNAT runs on a different set than its startup.conf says is
 * the benchmark measuring something else.
 */
/* not a unit: a VLIB_CONFIG_FUNCTION, called by VPP with its own parser
 * while it reads startup.conf; lab/cgnat-workers.sh gives it one. */
static clib_error_t *
bpa_config (vlib_main_t *vm, unformat_input_t *input)
{
  uword *bitmap = 0;
  u32 *wids = 0, i;

  while (unformat_check_input (input) != UNFORMAT_END_OF_INPUT)
    {
      if (unformat (input, "cgnat-workers %U", unformat_bitmap_list, &bitmap))
	;
      else
	return clib_error_return (0, "bpa: unknown input '%U'",
				  format_unformat_error, input);
    }
  if (!bitmap)
    return 0;
  clib_bitmap_foreach (i, bitmap)
    {
      if (i >= vlib_num_workers ())
	{
	  clib_bitmap_free (bitmap);
	  vec_free (wids);
	  return clib_error_return (
	    0, "bpa: cgnat-workers names worker %u, and this dataplane has "
	       "%u (0-%u)",
	    i, vlib_num_workers (), vlib_num_workers () - 1);
	}
      vec_add1 (wids, i);
    }
  clib_bitmap_free (bitmap);
  if (vec_len (wids) == 0)
    return clib_error_return (0, "bpa: cgnat-workers names no worker");
  bpa_set_owners (&bpa_main, wids);
  return 0;
}

VLIB_CONFIG_FUNCTION (bpa_config, "bpa");

/*
 * The version column of `show plugins`, and the only version the LOADED
 * dataplane carries.
 *
 * It was the literal "0.1" for seventy-one releases (#291). Every box ever
 * shipped reported the same three characters, so an operator on an upgraded
 * box had no reading anywhere that could contradict the VERSION file
 * install.sh had just overwritten - and that file is written by the upgrade,
 * while the dataplane answering here is the one VPP loaded at the last boot.
 * Between those two moments the box stated a version it was not running.
 *
 * BPA_BUILD_VERSION is generated by tools/plugin-version-h.sh from
 * tools/version-stamp.sh, which is where install.sh gets the VERSION file
 * from as well. One calculation, so `show version` comparing the two is
 * comparing a build against a build and not a spelling against a spelling.
 *
 * The bound is measured, not chosen. VPP 26.06 lays `show plugins` out in
 * fixed columns - the name starts at 5, the version at 46, the description at
 * 79 - so the version has 33 columns, and prismash reads the field by finding
 * the run of two or more spaces that ends it. A stamp of 32 or more leaves
 * fewer than two and the field silently runs into the description. Failing
 * the BUILD is the only place that can be said out loud; failing at the
 * parser would be a `show version` that quietly reports the wrong thing,
 * which is the defect this whole change is about.
 */
STATIC_ASSERT (sizeof (BPA_BUILD_VERSION) <= 32,
	       "the version stamp does not fit the `show plugins` column");

VLIB_PLUGIN_REGISTER () = {
  .version = BPA_BUILD_VERSION,
  .description = "CGNAT with allocated port blocks (BPA)",
};
