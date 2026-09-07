/* bpa.c - plugin registration and pool management. */

#include <vlib/vlib.h>
#include <vnet/plugin/plugin.h>
#include <vpp/app/version.h>
#include <vnet/ip/ip_interface.h>

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
   * Deterministic only, and not from lack of effort.
   *
   * In dynamic mode a block belongs to whoever currently holds it, and it is
   * released when that subscriber goes idle. A port pinned inside one would
   * follow the block to the next subscriber - the forward would keep working
   * and start reaching a different customer, which is worse than not working.
   * Pinning the block itself is a feature of its own, not a detail of this
   * one.
   */
  if (p->mode != BPA_MODE_DETERMINISTIC)
    return clib_error_return (
      0,
      "pool '%v' is in dynamic mode: a forwarded port would follow the block "
      "to whichever subscriber holds it next, so forwards are accepted only "
      "on a deterministic pool",
      p->name);

  if (sf->proto != BPA_PROTO_TCP && sf->proto != BPA_PROTO_UDP)
    return clib_error_return (0, "only tcp and udp can be forwarded");

  if (sf->port_pub == 0 || sf->port_priv == 0)
    return clib_error_return (0, "port 0 is not a port");

  if (sf->port_pub < p->port_min || sf->port_pub > p->port_max)
    return clib_error_return (
      0, "public port %u is outside pool '%v', which translates %u-%u",
      sf->port_pub, p->name, p->port_min, p->port_max);

  if (bpa_forward (p, sf->ip_priv, &blk))
    return clib_error_return (
      0, "%U is not a subscriber of pool '%v', or is beyond its capacity of "
	 "%u",
      format_ip4_address, &priv, p->name, p->capacity);

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

/* Where a forward belongs in a vector ordered by (public port, protocol). */
static u32
bpa_static_position (const bpa_pool_t *p, const bpa_static_t *sf)
{
  u32 n = vec_len (p->statics);
  u32 i = bpa_static_lower (p->statics, n, sf->port_pub);

  while (i < n && p->statics[i].port_pub == sf->port_pub
	 && p->statics[i].proto < sf->proto)
    i++;
  return i;
}

/*
 * Two forwards that cannot both hold.
 *
 * One public port serves one socket, and one subscriber socket answers on one
 * public port - the second is endpoint-independent mapping (RFC 4787 REQ-1),
 * which the rest of this box already guarantees and a second forward on the
 * same socket would quietly break.
 */
static clib_error_t *
bpa_static_conflict (const bpa_pool_t *p, const bpa_static_t *sf)
{
  ip4_address_t a;
  u32 i;

  vec_foreach_index (i, p->statics)
    {
      const bpa_static_t *o = &p->statics[i];

      if (o->proto != sf->proto)
	continue;
      if (o->port_pub == sf->port_pub)
	{
	  a.as_u32 = clib_host_to_net_u32 (o->ip_priv);
	  return clib_error_return (
	    0, "public port %u is already forwarded to %U:%u", sf->port_pub,
	    format_ip4_address, &a, o->port_priv);
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
      const bpa_static_t *have = bpa_static_by_pub (p, sf->port_pub, sf->proto);
      bpa_static_t gone;

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
      vlib_worker_thread_barrier_release (bm->vlib_main);
      bpa_audit ("forward-del", p, 0);
      return 0;
    }

  e = bpa_static_validate (p, sf);
  if (e)
    return e;

  e = bpa_static_conflict (p, sf);
  if (e || is_check)
    return e;

  i = bpa_static_position (p, sf);
  vlib_worker_thread_barrier_sync (bm->vlib_main);
  vec_insert_elts (p->statics, sf, 1, i);
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
    }

  vlib_worker_thread_barrier_release (bm->vlib_main);

  bpa_audit ("add", p, 0);

  if (pool_index)
    *pool_index = p - bm->pools;
  return 0;
}

clib_error_t *
bpa_pool_del (u8 *name)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;
  uword *q;

  q = hash_get_mem (bm->pool_by_name, name);
  if (!q)
    return clib_error_return (0, "no such pool");

  vlib_worker_thread_barrier_sync (bm->vlib_main);

  p = pool_elt_at_index (bm->pools, q[0]);
  bpa_audit ("del", p, 0);
  {
    f64 now = vlib_time_now (bm->vlib_main);
    u32 i;

    for (i = 0; i < vec_len (bm->per_thread); i++)
      {
	bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, i);

	/*
	 * Every session this pool owned, freed with it.
	 *
	 * Left behind, they outlive the definition that produced them: VPP
	 * reuses the pool index, so a pool created afterwards inherits
	 * sessions carrying the OLD arithmetic - a subscriber still
	 * translated onto a public port that the new mapping assigns to
	 * somebody else, with nothing anywhere saying so. Redefining a pool
	 * is meant to invalidate its sessions; until this loop existed, it
	 * only invalidated the reasoning about them.
	 *
	 * Indices are collected before anything is freed: bpa_session_free()
	 * also reclaims the subscriber record, and mutating a pool while
	 * iterating it is how a walk starts reading freed memory.
	 */
	u32 *doomed = 0, *ix;
	bpa_session_t *s;

	pool_foreach (s, pt->sessions)
	  if (s->pool_index == q[0])
	    vec_add1 (doomed, s - pt->sessions);

	vec_foreach (ix, doomed)
	  bpa_session_free (pt, pool_elt_at_index (pt->sessions, *ix), now);
	vec_free (doomed);

	if (q[0] < vec_len (pt->dyn))
	  bpa_dyn_free (vec_elt_at_index (pt->dyn, q[0]));
      }
  }
  hash_unset_mem (bm->pool_by_name, p->name);
  vec_free (p->name);
  vec_free (p->statics);
  pool_put (bm->pools, p);
  bpa_outside_window_update ();

  vlib_worker_thread_barrier_release (bm->vlib_main);
  return 0;
}

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
