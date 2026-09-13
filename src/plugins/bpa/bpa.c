/* bpa.c - plugin registration and pool management. */

#include <vlib/vlib.h>
#include <vnet/plugin/plugin.h>
#include <vpp/app/version.h>
#include <vnet/ip/ip_interface.h>
#include <vnet/ip/reass/ip4_sv_reass.h>

#include "bpa.h"
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
  ip4_address_t priv, pub;
  bpa_block_t blk;
  u32 other = 0;

  priv.as_u32 = clib_host_to_net_u32 (sf->ip_priv);

  /*
   * Both modes, and the same arithmetic decides in both.
   *
   * In deterministic mode the subscriber's block is the formula's answer and
   * nothing can move it. In dynamic mode the block moves, so the forward
   * makes it stop: bpa_static_pin() holds the block whose index IS the
   * subscriber's dense index - the one bpa_split() would have given it - for
   * as long as the forward exists.
   *
   * Deriving the block from the subscriber instead of letting the operator
   * name one is what lets every other line of this feature serve both modes
   * unchanged. bpa_reverse_sub() still recovers the block from (address,
   * port); the handoff still lands both directions on one worker, because
   * block_idx == sub_idx takes the same remainder; and
   * bpa_session_static_open() still finds the block by asking bpa_forward().
   * An operator-chosen block would have broken all three, and on a box with
   * workers it would have made most (address, port) pairs unpinnable for a
   * given subscriber for a reason no operator can be expected to hear.
   */
  if (sf->proto != BPA_PROTO_TCP && sf->proto != BPA_PROTO_UDP)
    return clib_error_return (0, "only tcp and udp can be forwarded");

  if (sf->port_pub == 0 || sf->port_priv == 0)
    return clib_error_return (0, "port 0 is not a port");

  if (sf->port_pub < p->port_min || sf->port_pub > p->port_max)
    return clib_error_return (
      0, "public port %u is outside pool '%v', which translates %u-%u",
      sf->port_pub, p->name, p->port_min, p->port_max);

  if (bpa_forward (p, sf->ip_priv, &blk))
    {
      /*
       * A dynamic pool is oversubscribed on purpose - that is what it is for -
       * so "beyond capacity" is the ordinary case there and not an error the
       * operator made. It still has no block to pin, and saying so in the
       * pool's own numbers is the difference between a refusal that can be
       * acted on and one that reads like a bug.
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
	0, "%U is not a subscriber of pool '%v', or is beyond its capacity of "
	   "%u",
	format_ip4_address, &priv, p->name, p->capacity);
    }

  /*
   * The port has to be in this subscriber's own block.
   *
   * Anywhere else it is somebody's - the formula assigns every block to some
   * subscriber - and taking it would translate that customer onto a port this
   * box is also sending elsewhere. Naming who holds it turns "refused" into
   * something the operator can act on.
   */
  if (sf->port_pub < blk.port_lo || sf->port_pub > blk.port_hi)
    {
      pub.as_u32 = clib_host_to_net_u32 (blk.ip_pub);
      if (bpa_reverse (p, blk.ip_pub, sf->port_pub, &other) == 0)
	{
	  ip4_address_t o;

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
    }

  sf->ip_pub = blk.ip_pub;
  sf->port_off = (u16) (sf->port_pub - blk.port_lo);
  return 0;
}

/*
 * Where a forward belongs in a vector ordered by (public port, protocol).
 *
 * Two forwards may now share both, on different public addresses, and a tie
 * goes AFTER the ones already there. The VTY keeps its own copy of a pool's
 * forwards in the order it read them out of "show bpa config" and renders
 * both sides of a commit diff with a stable sort, so a device that inserted a
 * new tie in front of an older one would show a diff on the following commit
 * that nobody asked for. The vector stays ordered by public port, which is
 * the only thing bpa_static_lower() needs.
 */
static u32
bpa_static_position (const bpa_pool_t *p, const bpa_static_t *sf)
{
  u32 n = vec_len (p->statics);
  u32 i = bpa_static_lower (p->statics, n, sf->port_pub);

  while (i < n && p->statics[i].port_pub == sf->port_pub
	 && p->statics[i].proto <= sf->proto)
    i++;
  return i;
}

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
bpa_static_conflict (const bpa_pool_t *p, const bpa_static_t *sf)
{
  ip4_address_t a, pub;
  u32 i;

  vec_foreach_index (i, p->statics)
    {
      const bpa_static_t *o = &p->statics[i];

      if (o->proto != sf->proto)
	continue;
      if (o->port_pub == sf->port_pub && o->ip_pub == sf->ip_pub)
	{
	  a.as_u32 = clib_host_to_net_u32 (o->ip_priv);
	  pub.as_u32 = clib_host_to_net_u32 (o->ip_pub);
	  return clib_error_return (
	    0, "public port %u on %U is already forwarded to %U:%u",
	    sf->port_pub, format_ip4_address, &pub, format_ip4_address, &a,
	    o->port_priv);
	}
      if (o->ip_priv == sf->ip_priv && o->port_priv == sf->port_priv)
	{
	  a.as_u32 = clib_host_to_net_u32 (sf->ip_priv);
	  return clib_error_return (
	    0, "%U:%u is already forwarded from public port %u",
	    format_ip4_address, &a, sf->port_priv, o->port_pub);
	}
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
static clib_error_t *
bpa_statics_validate (bpa_pool_t *p, bpa_static_t *in)
{
  bpa_static_t *seen = 0;
  clib_error_t *e = 0;
  u32 i;

  vec_foreach_index (i, in)
    {
      bpa_static_t sf = in[i];

      p->statics = seen;
      e = bpa_static_validate (p, &sf);
      if (!e)
	e = bpa_static_conflict (p, &sf);
      if (e)
	break;
      /*
       * Written back, because bpa_static_validate() COMPLETES a forward as
       * well as checking it: ip_pub and port_off are derived here and nowhere
       * else. Validating a copy and storing the original left every forward
       * given on a "bpa pool" line with a zero public address, which reserves
       * nothing and translates nothing - a forward the configuration shows
       * and the dataplane does not have.
       */
      in[i] = sf;
      vec_add1 (seen, sf);
    }

  p->statics = 0;
  vec_free (seen);
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

/* Is this the first forward of its subscriber in the sorted vector? The block
 * is the subscriber's own, so that makes it the one forward that stands for
 * the block. */
static int
bpa_static_first_in_block (const bpa_pool_t *p, const bpa_static_t *sf)
{
  u32 i;

  vec_foreach_index (i, p->statics)
    if (p->statics[i].ip_priv == sf->ip_priv)
      return &p->statics[i] == sf;
  return 0;
}

/*
 * Re-announces every pinned block, when the log is switched on.
 *
 * A pin is made once, by a configuration change, and then holds a block for as
 * long as the forward exists. On a box that has just booted, the configuration
 * replay creates the pools - and their pins - BEFORE it turns the export on,
 * which is the order hexash applies a configuration in. Without this, every
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
	      || bpa_static_first_in_block (p, &p->statics[i]) == 0)
	    continue;

	  bpa_log_queue_event (vec_elt_at_index (bm->per_thread, ti),
			       BPA_NAT_EVENT_BLOCK_ALLOC, p->statics[i].ip_priv,
			       blk.ip_pub, blk.port_lo, blk.port_hi,
			       pool_index);
	}
    }
  vlib_worker_thread_barrier_release (bm->vlib_main);
}

/* Is any forward left in the block this one was in? The block is the
 * subscriber's own, so the question is whether the subscriber has another. */
static int
bpa_static_last_in_block (const bpa_pool_t *p, const bpa_static_t *sf)
{
  u32 i;

  vec_foreach_index (i, p->statics)
    if (p->statics[i].ip_priv == sf->ip_priv)
      return 0;
  return 1;
}

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
  u32 n = vec_len (p->statics);
  u32 i = bpa_static_lower (p->statics, n, sf->port_pub);
  u32 n_match = 0;
  ip4_address_t a;
  clib_error_t *e;
  u8 *who = 0;

  for (; i < n && p->statics[i].port_pub == sf->port_pub; i++)
    {
      if (p->statics[i].proto != sf->proto)
	continue;
      n_match++;
      if (!first)
	first = &p->statics[i];
      else if (!second)
	second = &p->statics[i];
    }

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
      if (bpa_static_last_in_block (p, &gone))
	bpa_static_pin (pool_index, p, &gone, 0);
      vlib_worker_thread_barrier_release (bm->vlib_main);
      bpa_audit ("forward-del", p, 0);
      return 0;
    }

  e = bpa_static_validate (p, sf);
  if (e)
    return e;

  e = bpa_static_conflict (p, sf);
  if (e)
    return e;

  /* Last, because it is the only refusal that depends on what the box is
   * doing this second rather than on what it was told. An operator who gets
   * it has a configuration that is legal and a moment that is not. */
  e = bpa_static_holder (pool_index, p, sf);
  if (e || is_check)
    return e;

  i = bpa_static_position (p, sf);
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

/* Do two address ranges have any address in common? */
static_always_inline int
bpa_ranges_overlap (const bpa_range_t *a, const bpa_range_t *b)
{
  return (u64) a->base < (u64) b->base + b->count
	 && (u64) b->base < (u64) a->base + a->count;
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
static clib_error_t *
bpa_check_pool_overlap (const bpa_pool_t *cfg)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;
  u32 i, j;

  pool_foreach (p, bm->pools)
    {
      int same_name = vec_len (p->name) == vec_len (cfg->name)
		      && !memcmp (p->name, cfg->name, vec_len (cfg->name));

      /* A pool being replaced does not overlap itself. */
      if (same_name)
	continue;

      /* Public addresses are compared whatever the VRFs are: out2in keys on
       * the public address alone, so two pools sharing one are two answers to
       * the same question. */
      for (i = 0; i < cfg->outside.n_ranges; i++)
	for (j = 0; j < p->outside.n_ranges; j++)
	  if (bpa_ranges_overlap (&cfg->outside.ranges[i],
				  &p->outside.ranges[j]))
	    {
	      ip4_address_t a;

	      a.as_u32 = clib_host_to_net_u32 (cfg->outside.ranges[i].base);
	      return clib_error_return (
		0,
		"public address %U is already in pool '%v'. Two pools cannot "
		"share one: the return path finds a pool by public address "
		"and would have two answers",
		format_ip4_address, &a, p->name);
	    }

      if (p->fib_index != ~0u && cfg->fib_index != ~0u
	  && p->fib_index != cfg->fib_index)
	continue;

      for (i = 0; i < cfg->inside.n_ranges; i++)
	for (j = 0; j < p->inside.n_ranges; j++)
	  if (bpa_ranges_overlap (&cfg->inside.ranges[i], &p->inside.ranges[j]))
	    {
	      ip4_address_t a;

	      a.as_u32 = clib_host_to_net_u32 (cfg->inside.ranges[i].base);
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
    }
  return 0;
}

/*
 * How many subscribers a proposed pool would move, and where two of them go.
 *
 * This is the sharpest edge in the whole design. In deterministic mode a
 * subscriber's public address and port block are a function of the pool
 * definition, so ADDING ONE PUBLIC PREFIX changes the divisor and re-maps
 * EVERY subscriber at that instant. Every session breaks, every mapping moves,
 * and the box reports nothing unusual because nothing unusual happened: it is
 * doing exactly what it was told.
 *
 * The audit trail keeps past answers resolvable - it records which definition
 * was in force - so this is not a correctness problem. It is an outage the
 * operator has to be told about BEFORE it happens, in numbers rather than in
 * documentation they will read afterwards.
 *
 * Counted rather than reasoned about: an operator asked to trust "this changes
 * the mapping" will believe the box is being cautious. Told "1024 of 1024
 * subscribers move, and 100.64.0.5 goes from 200.150.0.17:1536-2047 to
 * 200.150.0.33:2048-2559", they know what they are about to do.
 *
 * `now` MUST already be through bpa_pool_init(); `cfg` is a candidate that has
 * been through it on a copy.
 */
u32
bpa_pool_remap_count (const bpa_pool_t *now, const bpa_pool_t *cfg,
		      u32 *n_total, u32 *example, u32 max_examples)
{
  u32 moved = 0, seen = 0, i;
  u32 usable = now->inside.count < now->capacity ? now->inside.count
						 : now->capacity;

  *n_total = usable;
  for (i = 0; i < usable; i++)
    {
      bpa_block_t a, b;
      u32 addr;

      if (bpa_set_addr_of (&now->inside, i, &addr))
	continue;
      seen++;
      if (bpa_forward (now, addr, &a))
	continue;
      /* An address the new definition does not serve at all has moved as
       * surely as one that moved somewhere else. */
      if (bpa_forward (cfg, addr, &b) || a.ip_pub != b.ip_pub
	  || a.port_lo != b.port_lo)
	{
	  if (moved < max_examples)
	    example[moved] = addr;
	  moved++;
	}
    }
  (void) seen;
  return moved;
}

/*
 * Every check bpa_pool_add() performs, without creating anything.
 *
 * Exists so a configuration can be validated before it is applied. Without
 * it, a "commit check" can only inspect syntax and then approve a pool the
 * device will refuse - false confidence exactly where an operator is relying
 * on the check.
 */
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
void
bpa_outside_window_update (void)
{
  bpa_main_t *bm = &bpa_main;
  u32 lo = ~0u, hi = 0;
  bpa_pool_t *p;
  u32 i;

  pool_foreach (p, bm->pools)
    {
      for (i = 0; i < p->outside.n_ranges; i++)
	{
	  u32 base = p->outside.ranges[i].base;
	  u32 last = base + p->outside.ranges[i].count - 1;

	  if (base < lo)
	    lo = base;
	  if (last > hi)
	    hi = last;
	}
    }

  if (lo == ~0u)
    {
      bm->outside_lo = 1;   /* an empty window: lo > hi rejects everything */
      bm->outside_hi = 0;
      return;
    }
  bm->outside_lo = lo;
  bm->outside_hi = hi;
}

/*
 * Recomputes the MSS a pool set to "auto" clamps to.
 *
 * Cheap and called often rather than hooked to an MTU change: the box has a
 * handful of interfaces, and a callback that fires on every MTU change
 * anywhere in VPP is a subscription to somebody else's lifecycle for a number
 * that is one loop to recompute.
 */
void
bpa_auto_mss_update (void)
{
  bpa_main_t *bm = &bpa_main;
  vnet_main_t *vnm = bm->vnet_main;
  vnet_sw_interface_t *si;
  u32 min = ~0u;

  if (!vnm)
    return;

  pool_foreach (si, vnm->interface_main.sw_interfaces)
    {
      u32 idx = si->sw_if_index, mtu;
      int on = 0;

      /* The handoff variants are the same interface carrying the same
       * traffic; a box with workers would otherwise report no CGNAT
       * interfaces at all and leave "auto" resolving to nothing. */
      on |= vnet_feature_is_enabled ("ip4-unicast", "bpa-in2out", idx) == 1;
      on |= vnet_feature_is_enabled ("ip4-unicast", "bpa-in2out-handoff", idx)
	    == 1;
      on |= vnet_feature_is_enabled ("ip4-unicast", "bpa-out2in", idx) == 1;
      on |= vnet_feature_is_enabled ("ip4-unicast", "bpa-out2in-handoff", idx)
	    == 1;
      if (!on)
	continue;

      mtu = vnet_sw_interface_get_mtu (vnm, idx, VNET_MTU_IP4);
      if (mtu && mtu < min)
	min = mtu;
    }

  /* No CGNAT interface yet, or an MTU smaller than the headers themselves:
   * zero, which the datapath reads as "do not clamp". Inventing a number
   * here would be a clamp nobody configured. */
  bm->auto_mss = (min == ~0u || min <= BPA_MSS_OVERHEAD)
		   ? 0
		   : (u16) (min - BPA_MSS_OVERHEAD);
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
int
bpa_interface_feature_enable_disable (u32 sw_if_index, int is_inside,
				      int enable)
{
  const char *feature;
  int rv;

  if (bpa_main.n_workers)
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

  /* The set of interfaces the CGNAT runs on has just changed, and that set is
   * what "tcp-mss auto" is the minimum over. Left alone, the clamp keeps
   * describing an interface set that no longer exists, and a subscriber
   * behind the new interface is handed an MSS larger than the path takes. */
  bpa_auto_mss_update ();

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
void
bpa_timeouts_warn (vlib_main_t *vm, const bpa_timeouts_t *t, u8 *pool)
{
  bpa_floor_ctx_t ctx = { .vm = vm, .pool = pool };

  bpa_timeouts_check (t, bpa_floor_warn, &ctx);
}

clib_error_t *
bpa_pool_settings_set (u8 *name, bpa_filter_t filter, u8 max_blocks, u32 alg,
		       u32 alg_set, u16 tcp_mss, u8 tcp_mss_auto,
		       u8 util_alarm, const bpa_timeouts_t *timeouts)
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

  /* Under the barrier: the datapath reads the filtering mode, the ALG mask and
   * the clamp on the packet path, and a half-applied set of them is a pool
   * behaving as neither the old configuration nor the new one. */
  vlib_worker_thread_barrier_sync (bm->vlib_main);
  p->filter = filter;
  p->max_blocks = max_blocks ? max_blocks : 1;
  p->alg = alg;
  p->alg_set = alg_set;
  p->tcp_mss = tcp_mss;
  p->tcp_mss_auto = tcp_mss_auto;
  p->util_alarm = util_alarm;
  p->timeouts = *timeouts;
  vlib_worker_thread_barrier_release (bm->vlib_main);

  /* The VTY changes per-pool timeouts here, so this is where that change is
   * warned about. No vlib_main_t: the operator is at the other end of an API
   * call, and hexash relays it from "show bpa timeouts". */
  bpa_timeouts_warn (0, timeouts, p->name);

  bpa_audit ("settings", p, 0);
  return 0;
}

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
  }
  bpa_outside_window_update ();

  if (p->mode == BPA_MODE_DYNAMIC)
    {
      u32 i, nw, w;

      for (i = 0; i < vec_len (bm->per_thread); i++)
	{
	  bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, i);

	  /* thread 0 only carries blocks when there are no workers */
	  if (bm->n_workers && i == 0)
	    continue;

	  bpa_worker_id (bm, i, &w, &nw);
	  vec_validate (pt->dyn, p - bm->pools);
	  e = bpa_dyn_init (vec_elt_at_index (pt->dyn, p - bm->pools), p, w,
			    nw);
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

  vlib_worker_thread_barrier_release (bm->vlib_main);

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
 * DETERMINISTIC POOLS ARE NOT AFFECTED, and the check says so rather than
 * relying on the loop finding nothing: bpa_dyn_init() is called only for
 * BPA_MODE_DYNAMIC, so a deterministic pool has no allocator here. It also has
 * no events at all - the mapping is the formula plus the dated configuration
 * trail, which is why that mode logs nothing per subscriber - so a release
 * emitted here would be the second half of a pair whose first half was never
 * written, and a collector reading it would have to invent the allocation.
 */
static u32
bpa_pool_log_blocks (const bpa_pool_t *p, u32 pool_index, u8 event)
{
  bpa_main_t *bm = &bpa_main;
  u32 i, n = 0;

  if (p->mode != BPA_MODE_DYNAMIC)
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
      for (local = 0; local < d->n_local; local++)
	{
	  bpa_block_t blk;
	  u32 ip_priv;

	  if (d->owner[local] == BPA_NO_BLOCK)
	    continue;
	  if (bpa_set_addr_of (&p->inside, d->owner[local], &ip_priv))
	    continue;

	  bpa_dyn_block_addr (p, local * nw + w, &blk);
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
    }
  vec_free (p->name);
  vec_free (p->statics);
  pool_put (bm->pools, p);
  bpa_outside_window_update ();
  vlib_worker_thread_barrier_release (vm);
  return 0;
}

/*
 * Every block this box is still holding, as one RFC 8158 event each.
 *
 * Not static and not local to a caller, because the two callers must walk
 * exactly the same set: bpa_stop_drain() gives the blocks back and the
 * re-announcement in bpa-expire-process takes them back.
 */
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
 *   - An ExecStop in hexarouting.service rather than in a vpp.service drop-in
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

static clib_error_t *
bpa_stop_drain (vlib_main_t *vm)
{
  bpa_main_t *bm = &bpa_main;
  u32 saved;

  if (!bpa_log_main.enabled && !bpa_log_tcp.enabled)
    return 0;

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
  saved = bpa_log_tcp_flush_exit ();
  if (saved)
    vlib_log_notice (bm->log_class,
		     "stopping: %u byte(s) the collector had not taken went to "
		     "the spill file, to be replayed on the next connection",
		     saved);
  return 0;
}

VLIB_MAIN_LOOP_EXIT_FUNCTION (bpa_stop_drain);

/*
 * The mirror of bpa_check_local_overlap(): an address configured after the
 * pool exists. We cannot refuse it from here, so we make the noise loud
 * enough that it is found before a customer does.
 */
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

static clib_error_t *
bpa_init (vlib_main_t *vm)
{
  bpa_main_t *bm = &bpa_main;
  vlib_thread_main_t *tm = vlib_get_thread_main ();
  clib_error_t *e;
  u32 i;

  bm->vlib_main = vm;
  bm->vnet_main = vnet_get_main ();
  bm->pool_by_name = hash_create_vec (0, sizeof (u8), sizeof (uword));
  bm->log_class = vlib_log_register_class ("bpa", 0);
  bm->audit_path = format (0, "/var/log/hexarouting/config-history.log%c", 0);
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
  bm->n_workers = vlib_num_workers ();
  bpa_divisor_init (&bm->workers, bm->n_workers ? bm->n_workers : 1);

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
  for (i = 0; i < vec_len (bm->per_thread); i++)
    {
      e = bpa_per_thread_init (vec_elt_at_index (bm->per_thread, i), i);
      if (e)
	return e;
    }

  return 0;
}

VLIB_INIT_FUNCTION (bpa_init);

VLIB_PLUGIN_REGISTER () = {
  .version = "0.1",
  .description = "CGNAT with allocated port blocks (BPA)",
};
