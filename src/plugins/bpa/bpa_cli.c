/*
 * bpa_cli.c - debug CLI for BPA pools.
 *
 * WHY EVERY FUNCTION HERE CARRIES `not a unit:` (#390). The rule this
 * repository set is that every function has a unit test and a reader refuses
 * the one that has none. This file is the rendering half of the plugin, and
 * the maintainer's decision - both answers, split by file - puts it on the
 * other side of that line from `bpa_session.c` and the datapath:
 *
 *   * a VLIB_CLI_COMMAND body takes an `unformat_input_t` the CLI fills. There
 *     is no plain-cc stand-in for VPP's parser, and building one would be
 *     writing a second parser to test the first against.
 *   * a format function takes a `va_list` and appends through `format()`,
 *     which is VPP's own vector printer.
 *   * the two helpers that take a `vlib_main_t` write into the CLI's buffer
 *     through `vlib_cli_output()`.
 *
 * WHAT STILL HOLDS THEM, because an excuse is only honest beside the check it
 * leans on: `test/test_plugin_cli_says.py` requires every message this file
 * can print to be named by a lab, a suite or a document, and the labs capture
 * those screens on a running box. A wrong sentence here is caught there; what
 * is NOT caught is arithmetic, and the functions in this file that do
 * arithmetic - `bpa_counter_total`, the subscriber lookups - are deliberately
 * NOT excused. They are waiting for the harness built against the real VPP,
 * which is the other half of the same decision.
 *
 * `bpa_top_cmp` was in that list and is not any more: it needed nothing but
 * two rows, so it moved to bpa_top.h and test/test_bpa_top.c drives it. That
 * is the shape the rest of this list was waiting for - a harness where one is
 * possible, an excuse only where it is not - and #472 paid the rest of it the
 * same way: the arithmetic of a forward clause (bpa_static_make), of which
 * pool an address answers for (bpa_claimant_take) and of the block a
 * subscriber holds (bpa_dyn_held) moved to bpa_pool.h and bpa_dyn.h, where
 * the plain-cc suites drive it, and what stayed here is the walk over VPP's
 * own tables, excused as such.
 *
 * The marker on each one says which of the three classes it is and points
 * here, rather than repeating this paragraph forty-one times: an excuse
 * nobody reads is an allowlist, which is what the rule was written against.
 */

#include <time.h>
#include <sys/stat.h>

#include <vlib/vlib.h>
#include <vnet/ip/ip.h>

#include "bpa.h"
#include "bpa_top.h"
#include "bpa_radius.h"
#include "bpa_mss.h"
#include "bpa_errors.h"
#include "bpa_log.h"
#include "bpa_exhaust.h"
#include <vnet/fib/fib_table.h>
#include <vnet/fib/fib_entry.h>
#include <vnet/ipfix-export/flow_report.h>

/* not a unit: a VPP format function over a va_list - see the header */
u8 *
format_bpa_policy (u8 *s, va_list *args)
{
  bpa_policy_t p = va_arg (*args, int);
  return format (s, "%s",
		 p == BPA_POLICY_INTERLEAVED ? "interleaved" : "blocked");
}

/* not a unit: a VPP format function over a va_list - see the header */
u8 *
format_bpa_proto (u8 *s, va_list *args)
{
  u32 p = va_arg (*args, u32);

  switch (p)
    {
    case BPA_PROTO_TCP:
      return format (s, "tcp");
    case BPA_PROTO_UDP:
      return format (s, "udp");
    case BPA_PROTO_ICMP:
      return format (s, "icmp");
    case BPA_PROTO_GRE:
      return format (s, "gre");
    case BPA_PROTO_ESP:
      return format (s, "esp");
    default:
      return format (s, "proto-%u", p);
    }
}

/* The whole mask at once: "ftp on rtsp off pptp on esp on sip on". One line an
 * operator can read, rather than one line per ALG that has to be assembled by
 * eye. */
/* not a unit: a VPP format function over a va_list - see the header */
u8 *
format_bpa_alg (u8 *s, va_list *args)
{
  u32 enabled = va_arg (*args, u32);
  int first = 1;

#define _(sym, nm, desc)                                                      \
  s = format (s, "%s%s %s", first ? "" : "  ", nm,                            \
	      (enabled & BPA_ALG_##sym) ? "on" : "off");                      \
  first = 0;
  foreach_bpa_alg
#undef _

    return s;
}

/* not a unit: a VPP format function over a va_list - see the header */
u8 *
format_bpa_mode (u8 *s, va_list *args)
{
  bpa_mode_t m = va_arg (*args, int);
  return format (s, "%s",
		 m == BPA_MODE_DETERMINISTIC ? "deterministic" : "dynamic");
}

/* Renders a host-order address the way the rest of VPP prints v4. */
/* not a unit: a VPP format function over a va_list - see the header */
static u8 *
format_bpa_addr (u8 *s, va_list *args)
{
  u32 host = va_arg (*args, u32);
  ip4_address_t a;

  a.as_u32 = clib_host_to_net_u32 (host);
  return format (s, "%U", format_ip4_address, &a);
}

/* Renders the ranges of an address set, e.g. "200.150.0.8+4, 45.170.16.64+2". */
/*
 * Whether this box has anywhere for CGNAT packets to enter and leave.
 *
 * The attachments are a property of the BOX and not of a pool - `cgnat
 * interface <name> inside` is a top-level statement - so one answer serves
 * every pool printed.
 *
 * Both sides are needed and neither is enough. A subscriber packet enters at
 * bpa-in2out and its reply comes back at bpa-out2in; with either missing the
 * pool translates nothing, and the two failures are told apart below because
 * they are different mistakes with different fixes.
 *
 * The handoff spellings are tested as well as the plain ones, for the reason
 * `show bpa config` gives where it does the same walk: with workers the
 * feature an interface actually carries is the handoff node, and a check that
 * looked for the plain name alone would report a correctly attached box as
 * having nothing.
 */
/* not a unit: walks VPP's interfaces and asks its feature arcs;
 * lab/vty-pool.sh reads each of the three answers in "show bpa pool" */
static void
bpa_attachments (int *inside, int *outside)
{
  vnet_main_t *vnm = vnet_get_main ();
  vnet_sw_interface_t *si;

  *inside = 0;
  *outside = 0;
  pool_foreach (si, vnm->interface_main.sw_interfaces)
    {
      if (vnet_feature_is_enabled ("ip4-unicast", "bpa-in2out",
				   si->sw_if_index) == 1
	  || vnet_feature_is_enabled ("ip4-unicast", "bpa-in2out-handoff",
				      si->sw_if_index) == 1)
	*inside = 1;
      if (vnet_feature_is_enabled ("ip4-unicast", "bpa-out2in",
				   si->sw_if_index) == 1
	  || vnet_feature_is_enabled ("ip4-unicast", "bpa-out2in-handoff",
				      si->sw_if_index) == 1)
	*outside = 1;
    }
}

/* not a unit: a VPP format function over a va_list - see the header */
static u8 *
format_bpa_addr_set (u8 *s, va_list *args)
{
  bpa_addr_set_t *set = va_arg (*args, bpa_addr_set_t *);
  u32 i;

  for (i = 0; i < set->n_ranges; i++)
    s = format (s, "%s%U+%u", i ? ", " : "", format_bpa_addr,
		set->ranges[i].base, set->ranges[i].count);
  return s;
}

/* not a unit: a VPP format function over a va_list - see the header */
u8 *
format_bpa_pool (u8 *s, va_list *args)
{
  bpa_pool_t *p = va_arg (*args, bpa_pool_t *);
  u32 indent = format_get_indent (s);
  u32 usable = bpa_pool_usable (p);

  s = format (s, "pool %v [%U / %U]", p->name, format_bpa_mode, p->mode,
	      format_bpa_policy, p->policy);
  if (p->vrf_id != ~0u)
    s = format (s, "\n%Uvrf          %u%s", format_white_space, indent + 2,
		p->vrf_id,
		p->out_vrf_id != ~0u ? "" : " (translated traffic stays in it)");
  if (p->out_vrf_id != ~0u)
    s = format (s, "\n%Uoutside vrf  %u", format_white_space, indent + 2,
		p->out_vrf_id);
  s = format (s, "\n%Uinside       %U (%u addresses, %u range(s))",
	      format_white_space, indent + 2, format_bpa_addr_set, &p->inside,
	      p->inside.count, p->inside.n_ranges);
  s = format (s, "\n%Uoutside      %U (%u addresses, %u range(s))",
	      format_white_space, indent + 2, format_bpa_addr_set, &p->outside,
	      p->outside.count, p->outside.n_ranges);
  s = format (s, "\n%Uports        %u-%u (%u per IP)", format_white_space,
	      indent + 2, p->port_min, p->port_max, p->port_count);
  s = format (s, "\n%Ublock        %u ports -> %u blocks per public IP",
	      format_white_space, indent + 2, p->block_size, p->blocks_per_ip);
  /*
   * Both numbers, and the capacity below is computed from the second one.
   * A reserve that appeared only as a setting, with a capacity still counted
   * off blocks_per_ip, would promise subscribers the pool will drop.
   */
  if (p->block_reserve)
    s = format (s, "\n%Ureserve      %u block(s) per public IP -> %u mapped, "
		   "%u held for extensions",
		format_white_space, indent + 2, p->block_reserve,
		p->blocks_mapped, p->reserve_count);
  s = format (s, "\n%Usharing      %u:1", format_white_space, indent + 2,
	      p->blocks_mapped);
  /*
   * The range of ports one subscriber can end up with, in ports.
   *
   * The operator sells ports and the box speaks in blocks, and "block 64,
   * max-blocks 8" is already a per-pool range - a subscriber starts at 64
   * ports and grows one block at a time, only when it fills one, as far as
   * 512. Nothing said so, and a range nobody can see is a range nobody sizes
   * a pool with.
   *
   * A deterministic pool joins this only once it has a reserve. Without one
   * the minimum IS the maximum by construction - the formula assigns every
   * block to somebody, so there is none to give - and max-blocks above 1 on
   * such a pool describes a growth that cannot happen (#39).
   */
  /* A first allocation above one block (#462) moves the bottom of the range:
   * a new subscriber starts there, and grows from there a block at a time. */
  if (p->mode == BPA_MODE_DYNAMIC && p->initial_blocks > 1)
    s = format (s, "\n%Uper sub      %u-%u ports (starts at %u blocks, grown "
		   "one at a time to %u as each fills)",
		format_white_space, indent + 2,
		p->block_size * p->initial_blocks,
		p->block_size * p->max_blocks, p->initial_blocks,
		p->max_blocks);
  else if ((p->mode == BPA_MODE_DYNAMIC || p->block_reserve)
	   && p->max_blocks > 1)
    s = format (s, "\n%Uper sub      %u-%u ports (1-%u blocks, grown one at a "
		   "time as each fills)",
		format_white_space, indent + 2, p->block_size,
		p->block_size * p->max_blocks, p->max_blocks);
  else
    s = format (s, "\n%Uper sub      %u ports (one block)", format_white_space,
		indent + 2, p->block_size);
  /*
   * A promise about layout, and no mode test guarding it, because a pool that
   * carries a stride is a dynamic pool: bpa_pool_init() and
   * bpa_pool_settings_set() are the only two ways a stride is stored and both
   * refuse it on a deterministic pool through the one
   * bpa_pool_stride_refusal() they share, so that is true by construction
   * (#375). This row printed on such a pool since 0.67.0 while the allocator
   * never read the setting, which is the promise the refusal exists to stop
   * the box making.
   */
  if (p->block_stride)
    s = format (s, "\n%Ustride       %u (%u ports between a subscriber's "
		   "blocks, on its own public address)",
		format_white_space, indent + 2, p->block_stride,
		p->block_stride * p->block_size);
  s = format (s, "\n%Ucapacity     %u subscribers (usable %u)",
	      format_white_space, indent + 2, p->capacity, usable);
  /*
   * In dynamic mode with workers that figure is a ceiling nobody is promised,
   * and the floor goes beside it. Why the two differ is with the arithmetic,
   * in bpa_pool_guaranteed().
   *
   * Measured on a three-block pool with two workers: subscribers at offsets
   * 5, 6 and 9 land on workers 1, 0 and 1, the slices hold two blocks and
   * one, and the third subscriber is refused with a block standing free on
   * the other worker - while this line said "usable 3". The floor is the
   * number to size against; the difference between the two is what an even
   * spread would buy. Fixing the mechanism is a design decision with a cost
   * either way and is #210 - this is the half that stops the box claiming
   * what it cannot do.
   *
   * Printed only when it is BELOW the ceiling, which is also how the modes
   * with no shard to lose anything to - deterministic, and a single worker -
   * print nothing: the floor comes back equal to the ceiling there, so this
   * reader does not have to know which modes those are. A caveat that repeats
   * the number above it is a caveat people learn to skip, which is how the one
   * that matters gets skipped too.
   */
  /*
   * And the other reason that figure can be a number nobody will see (#299).
   *
   * Measured on a real appliance whose dataplane had no interface at all: a
   * pool committed cleanly, the boot self-test gave it a green tick, and this
   * line said "capacity 504 subscribers (usable 504)" for a box that would
   * translate nothing, because there was nothing for CGNAT to be attached to.
   * An operator sizes against that number and the honest one was zero.
   *
   * NOT a refusal, and that is deliberate. Writing the pools before the
   * interfaces exist is ordinary staged work, and `prismash --load` applies them
   * in that order at every boot, so refusing here would refuse a box its own
   * saved configuration. This is #210's option 3 and #226's option A one more
   * time: the mechanism stays and the box stops claiming what it cannot do.
   *
   * Printed only when it is true, for the reason the caveat above gives about
   * itself: a line that appears on a correctly attached box is one people
   * learn to skip, and then they skip this one too.
   */
  {
    int has_in, has_out;

    bpa_attachments (&has_in, &has_out);
    if (!has_in && !has_out)
      s = format (s, "\n%U             nobody yet: no interface is attached "
		     "to CGNAT, so no packet can reach this pool in either "
		     "direction",
		  format_white_space, indent + 2);
    else if (!has_in)
      s = format (s, "\n%U             nobody yet: no interface is attached "
		     "on the inside, so no subscriber packet can reach this "
		     "pool",
		  format_white_space, indent + 2);
    else if (!has_out)
      s = format (s, "\n%U             nobody yet: no interface is attached "
		     "on the outside, so a translated packet has no way back",
		  format_white_space, indent + 2);
  }
  {
    u32 nw = bpa_main.n_workers;
    u32 guaranteed = bpa_pool_guaranteed (p, nw);

    if (guaranteed < usable)
      s = format (s, "\n%U             %u whatever the addresses are: blocks "
		     "are sharded %u ways and a subscriber draws only from "
		     "its own worker's slice",
		  format_white_space, indent + 2, guaranteed, nw);
  }
  s = format (s, "\n%Udivisor      %u (%s)", format_white_space, indent + 2,
	      p->div.d, p->div.is_pow2 ? "shift" : "reciprocal");
  /* Printed whichever way each one is set. "The ALG is off" and "the ALG saw
   * nothing" are different diagnoses, and the counters alone cannot tell them
   * apart; a line that appears only when off reads as an absent feature. */
  s = format (s, "\n%Ualg          %U", format_white_space, indent + 2,
	      format_bpa_alg, p->alg);
  /*
   * The distribution, not a mean.
   *
   * With 200k subscribers a pool can read as half empty while a thousand
   * customers are at their limit, and the mean says nothing about either. What
   * an operator needs to see is the shape: how many subscribers are near the
   * end of their block, and whether that number is moving.
   *
   * Walked here, in a show command, over the per-subscriber records the
   * allocator maintains anyway. Nothing is counted per packet and the session
   * table is never touched.
   */
  {
    bpa_main_t *bm = &bpa_main;
    u32 idx = p - bm->pools;
    u32 bucket[6] = { 0 };
    u32 ti, live = 0;
    static const char *label[6] = { "  0-24%", " 25-49%", " 50-74%",
				    " 75-89%", " 90-99%", "    100%" };

    for (ti = 0; ti < vec_len (bm->per_thread); ti++)
      {
	bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, ti);
	uword key, value;

	if (!pt->initialised)
	  continue;
	/* clang-format off */
	hash_foreach (key, value, pt->sub_by_idx,
	({
	  u32 used, total, pct;
	  bpa_sub_t *sub;

	  if ((u32) (key >> 32) != idx)
	    continue;
	  sub = pool_elt_at_index (pt->subs, value);
	  bpa_sub_usage (sub, &used, &total);
	  if (total == 0)
	    continue;
	  pct = (used * 100) / total;
	  live++;
	  bucket[pct >= 100 ? 5 : pct >= 90 ? 4 : pct >= 75 ? 3
		 : pct >= 50   ? 2 : pct >= 25 ? 1 : 0]++;
	}));
	/* clang-format on */
      }

    s = format (s, "\n%Uutilisation  %u subscriber(s) holding a block",
		format_white_space, indent + 2, live);
    if (live)
      {
	u32 k;

	for (k = 0; k < 6; k++)
	  if (bucket[k])
	    s = format (s, "\n%U%s  %u", format_white_space, indent + 4,
			label[k], bucket[k]);
      }
    if (p->util_alarm)
      s = format (s, "\n%Ualarm at     %u%% of a subscriber's block",
		  format_white_space, indent + 2, p->util_alarm);
  }

  if (p->bypass_src.count || p->bypass_dst.count)
    {
      bpa_main_t *bm = &bpa_main;
      u64 n = 0;
      u32 i;

      /* Summed across workers: the counter is per thread so no two of them
       * fight over a cache line to increment it. */
      for (i = 0; i < vec_len (bm->per_thread); i++)
	{
	  bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, i);
	  u32 idx = p - bm->pools;

	  if (idx < vec_len (pt->n_bypassed))
	    n += pt->n_bypassed[idx];
	}
      s = format (s, "\n%Ubypass       %u source and %u destination "
		     "address(es), %llu packets left untranslated",
		  format_white_space, indent + 2, p->bypass_src.count,
		  p->bypass_dst.count, n);
    }
  s = format (s, "\n%Uper subscriber", format_white_space, indent + 2);
  if (p->max_sessions)
    s = format (s, "  at most %u sessions", p->max_sessions);
  else
    s = format (s, "  sessions unlimited");
  if (p->max_rate)
    s = format (s, ", %u new per second", p->max_rate);
  else
    s = format (s, ", rate unlimited");
  /* The RESOLVED number for "auto", not the word: an operator turning
   * clamping on wants to know what it came out as, and "auto" alone would
   * make a wrong MTU somewhere else look like a working clamp. */
  if (p->tcp_mss_auto)
    s = format (s, "\n%Utcp-mss      auto -> %u%s", format_white_space,
		indent + 2, bpa_main.auto_mss,
		bpa_main.auto_mss ? "" : " (no CGNAT interface has an MTU)");
  else if (p->tcp_mss)
    s = format (s, "\n%Utcp-mss      %u", format_white_space, indent + 2,
		p->tcp_mss);
  else
    s = format (s, "\n%Utcp-mss      off", format_white_space, indent + 2);
  return s;
}

/* not a unit: bpa_static_make() - held by test/test_bpa_pool.c - wrapped in
 * a clib_error_t for the CLI (#472) */
static clib_error_t *
bpa_static_clause (bpa_pool_t *cfg, u8 proto, u32 port_pub,
		   const ip4_address_t *priv, u32 port_priv)
{
  bpa_static_t sf;

  if (bpa_static_make (proto, port_pub, clib_net_to_host_u32 (priv->as_u32),
		       port_priv, &sf))
    return clib_error_return (0, "forward: port out of range");
  vec_add1 (cfg->statics, sf);
  return 0;
}

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_pool_add_command_fn (vlib_main_t *vm, unformat_input_t *input,
			 vlib_cli_command_t *cmd)
{
  unformat_input_t _line_input, *line_input = &_line_input;
  clib_error_t *error = 0;
  ip4_address_t addr;
  u32 len = 0;
  u32 block = 512, port_min = 1024, port_max = 65535, mss = 0, vrf = 0;
  u32 tv = 0;

  u32 maxb = 0, initb = 0, stride = 0, reserve = 0;
  u32 pfx_base, pfx_count;
  u32 *in_b = 0, *in_c = 0, *out_b = 0, *out_c = 0;
  u32 *bys_b = 0, *bys_c = 0, *byd_b = 0, *byd_c = 0;
  bpa_pool_t cfg = { 0 };
  u8 *name = 0;
  int is_del = 0, is_check = 0;

  cfg.policy = BPA_POLICY_INTERLEAVED;
  cfg.mode = BPA_MODE_DETERMINISTIC;
  cfg.alg = BPA_ALG_DEFAULT;
  /* Not zero: zero is VRF 0, and "no VRF stated" has to be a different
   * answer from "the default VRF" or every existing pool would silently
   * become bound to one. */
  cfg.fib_index = cfg.vrf_id = ~0u;
  cfg.out_fib_index = cfg.out_vrf_id = ~0u;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (0, "expected pool name");

  while (unformat_check_input (line_input) != UNFORMAT_END_OF_INPUT)
    {
      u32 fwd_pub = 0, fwd_priv = 0;

      if (unformat (line_input, "del"))
	is_del = 1;
      else if (unformat (line_input, "check"))
	is_check = 1;
      /* inside and outside repeat: provider IPv4 is fragmented */
      else if (unformat (line_input, "inside %U/%d", unformat_ip4_address,
			 &addr, &len))
	{
	  if (bpa_prefix_range (clib_net_to_host_u32 (addr.as_u32), len,
				&pfx_base, &pfx_count))
	    {
	      error = clib_error_return (0, "inside prefix length %u invalid",
					 len);
	      goto done;
	    }
	  vec_add1 (in_c, pfx_count);
	  vec_add1 (in_b, pfx_base);
	}
      else if (unformat (line_input, "outside %U/%d", unformat_ip4_address,
			 &addr, &len))
	{
	  if (bpa_prefix_range (clib_net_to_host_u32 (addr.as_u32), len,
				&pfx_base, &pfx_count))
	    {
	      error = clib_error_return (0, "outside prefix length %u invalid",
					 len);
	      goto done;
	    }
	  vec_add1 (out_c, pfx_count);
	  vec_add1 (out_b, pfx_base);
	}
      else if (unformat (line_input, "block %u", &block))
	;
      else if (unformat (line_input, "ports %u-%u", &port_min, &port_max))
	;
      else if (unformat (line_input, "policy interleaved"))
	cfg.policy = BPA_POLICY_INTERLEAVED;
      else if (unformat (line_input, "policy blocked"))
	cfg.policy = BPA_POLICY_BLOCKED;
      else if (unformat (line_input, "mode deterministic"))
	cfg.mode = BPA_MODE_DETERMINISTIC;
      else if (unformat (line_input, "mode dynamic"))
	cfg.mode = BPA_MODE_DYNAMIC;
      /* Per-pool lifetimes. Absent means inherit the global, which is why
       * zero is the "not set" value rather than a legal setting. */
      else if (unformat (line_input, "timeout udp %u", &tv))
	cfg.timeouts.udp = tv;
      else if (unformat (line_input, "timeout icmp %u", &tv))
	cfg.timeouts.icmp = tv;
      else if (unformat (line_input, "timeout tcp-syn %u", &tv))
	cfg.timeouts.tcp_syn = tv;
      else if (unformat (line_input, "timeout tcp-transitory %u", &tv))
	cfg.timeouts.tcp_trans = tv;
      else if (unformat (line_input, "timeout tcp-established %u", &tv))
	cfg.timeouts.tcp_estab = tv;
      else if (unformat (line_input, "timeout gre %u", &tv))
	cfg.timeouts.gre = tv;
      else if (unformat (line_input, "timeout port-holddown %u", &tv))
	cfg.timeouts.port_holddown = tv;
      else if (unformat (line_input, "timeout alg-expect %u", &tv))
	cfg.timeouts.alg_expect = tv;

      else if (unformat (line_input, "max-blocks %u", &maxb))
	cfg.max_blocks = (u8) maxb;
      else if (unformat (line_input, "initial-blocks %u", &initb))
	{
	  /* The field is a u8, as max_blocks is: 256 would arrive as 0 and be
	   * a first allocation of one block with no word said (#351). */
	  if (initb > 255)
	    {
	      error = clib_error_return (
		0, "initial-blocks %u: expected 1-255 blocks", initb);
	      goto done;
	    }
	  cfg.initial_blocks = (u8) initb;
	}
      else if (unformat (line_input, "block-stride %u", &stride))
	{
	  if (stride > 65535)
	    {
	      error = clib_error_return (
		0, "block-stride %u: expected 0-65535 blocks", stride);
	      goto done;
	    }
	  cfg.block_stride = (u16) stride;
	}
      else if (unformat (line_input, "reserve %u", &reserve))
	{
	  if (reserve > 65535)
	    {
	      error = clib_error_return (
		0, "reserve %u: expected 0-65535 blocks per public address",
		reserve);
	      goto done;
	    }
	  cfg.block_reserve = (u16) reserve;
	}
      else if (unformat (line_input, "bypass destination %U/%d",
			 unformat_ip4_address, &addr, &len))
	{
	  if (bpa_prefix_range (clib_net_to_host_u32 (addr.as_u32), len,
				&pfx_base, &pfx_count))
	    {
	      error = clib_error_return (
		0, "bypass destination prefix length %u invalid", len);
	      goto done;
	    }
	  vec_add1 (byd_c, pfx_count);
	  vec_add1 (byd_b, pfx_base);
	}
      else if (unformat (line_input, "bypass source %U/%d",
			 unformat_ip4_address, &addr, &len))
	{
	  if (bpa_prefix_range (clib_net_to_host_u32 (addr.as_u32), len,
				&pfx_base, &pfx_count))
	    {
	      error = clib_error_return (
		0, "bypass source prefix length %u invalid", len);
	      goto done;
	    }
	  vec_add1 (bys_c, pfx_count);
	  vec_add1 (bys_b, pfx_base);
	}
      else if (unformat (line_input, "vrf %u", &vrf))
	cfg.vrf_id = vrf;
      else if (unformat (line_input, "outside-vrf %u", &vrf))
	cfg.out_vrf_id = vrf;
      else if (unformat (line_input, "utilisation-alarm %u", &mss))
	{
	  if (mss < 1 || mss > 100)
	    {
	      error = clib_error_return (
		0, "utilisation-alarm %u: expected 1-100 percent", mss);
	      goto done;
	    }
	  cfg.util_alarm = (u8) mss;
	}
      else if (unformat (line_input, "max-sessions %u", &cfg.max_sessions))
	;
      else if (unformat (line_input, "max-session-rate %u", &cfg.max_rate))
	;
      else if (unformat (line_input, "tcp-mss auto"))
	cfg.tcp_mss_auto = 1;
      else if (unformat (line_input, "tcp-mss %u", &mss))
	{
	  if (mss < BPA_MSS_MIN || mss > BPA_MSS_MAX)
	    {
	      error = clib_error_return (
		0, "tcp-mss %u is outside %u-%u: below the MSS every IPv4 "
		   "host must accept (RFC 1122) the clamp is legal and "
		   "useless, and above a jumbo frame it describes no path",
		mss, BPA_MSS_MIN, BPA_MSS_MAX);
	      goto done;
	    }
	  cfg.tcp_mss = (u16) mss;
	}
      /*
       * "alg <name> on|off" in a candidate configuration. alg_set records
       * that an opinion was stated, so bpa_pool_init() leaves it alone
       * instead of resolving it back to the default.
       *
       * There were TWO of these chains, from two branches that each added one
       * and a merge that kept both - the same accident lab/ftp-alg.sh records
       * for the configuration emitter a few hundred lines away, in the same
       * file, found separately. The first one reached matched "on" and "off",
       * set cfg.alg and never cfg.alg_set, so bpa_pool_init() resolved the bit
       * straight back to the shipped default: `bpa pool lab ... alg ftp off`
       * was accepted, printed nothing, and produced a pool with the ALG ON
       * (#206).
       *
       * It took lab/ftp-alg.sh's own off mode with it. That lab's header says
       * "sh tools/lab.sh ftp-alg off -> LAB FAILED", it passed, and it is the
       * only lab that spells the switch this way: rtsp writes "disable", which
       * this chain matched and that one did not, and pptp, esp and sip use the
       * runtime command. One lab used the broken spelling and it is the one
       * that went quiet.
       */
#define _(sym, nm, desc)                                                      \
  else if (unformat (line_input, "alg " nm " on")                             \
	   || unformat (line_input, "alg " nm " enable"))                     \
  {                                                                           \
    cfg.alg |= BPA_ALG_##sym;                                                 \
    cfg.alg_set |= BPA_ALG_##sym;                                             \
  }                                                                           \
  else if (unformat (line_input, "alg " nm " off")                            \
	   || unformat (line_input, "alg " nm " disable"))                    \
  {                                                                           \
    cfg.alg &= ~BPA_ALG_##sym;                                                \
    cfg.alg_set |= BPA_ALG_##sym;                                             \
  }
      foreach_bpa_alg
#undef _
      /*
       * A forward stated as part of the pool definition. This is the shape a
       * candidate configuration takes, and the only shape "check" can
       * validate: a forward is judged against the pool's block sizing and
       * address sets, and for a pool that does not exist yet those are on
       * this line and nowhere else.
       */
      else if (unformat (line_input, "forward tcp %u to %U %u", &fwd_pub,
			 unformat_ip4_address, &addr, &fwd_priv))
	{
	  error = bpa_static_clause (&cfg, BPA_PROTO_TCP, fwd_pub, &addr,
				     fwd_priv);
	  if (error)
	    goto done;
	}
      else if (unformat (line_input, "forward udp %u to %U %u", &fwd_pub,
			 unformat_ip4_address, &addr, &fwd_priv))
	{
	  error = bpa_static_clause (&cfg, BPA_PROTO_UDP, fwd_pub, &addr,
				     fwd_priv);
	  if (error)
	    goto done;
	}
      else if (unformat (line_input, "filter endpoint-independent"))
	cfg.filter = BPA_FILTER_ENDPOINT_INDEPENDENT;
      else if (unformat (line_input, "filter address-dependent"))
	cfg.filter = BPA_FILTER_ADDRESS_DEPENDENT;
      else if (!name && unformat (line_input, "%v", &name))
	;
      else
	{
	  error = clib_error_return (0, "unknown input '%U'",
				     format_unformat_error, line_input);
	  goto done;
	}
    }

  if (!name)
    {
      error = clib_error_return (0, "pool name is required");
      goto done;
    }

  if (is_del)
    {
      error = bpa_pool_del (name);
      goto done;
    }

  if (vec_len (in_b) == 0 || vec_len (out_b) == 0)
    {
      error = clib_error_return (
	0, "at least one inside and one outside prefix are required");
      goto done;
    }
  if (port_max > 65535 || port_min < 1 || port_min > port_max)
    {
      error = clib_error_return (0, "invalid port range");
      goto done;
    }
  if (block > 65535)
    {
      error = clib_error_return (0, "block size out of range");
      goto done;
    }

  error = bpa_addr_set_build (&cfg.inside, in_b, in_c, vec_len (in_b));
  if (error)
    goto done;
  error = bpa_addr_set_build (&cfg.outside, out_b, out_c, vec_len (out_b));
  if (error)
    goto done;
  /* Only when there is something to build. An empty address set is an error
   * for a pool's own prefixes - a pool with no addresses translates nothing -
   * and it is the NORMAL state of a bypass list. Building it unconditionally
   * made every "bpa pool" command fail, which the lab found on its first run. */
  if (vec_len (bys_b))
    {
      error = bpa_addr_set_build (&cfg.bypass_src, bys_b, bys_c,
				  vec_len (bys_b));
      if (error)
	goto done;
    }
  if (vec_len (byd_b))
    {
      error = bpa_addr_set_build (&cfg.bypass_dst, byd_b, byd_c,
				  vec_len (byd_b));
      if (error)
	goto done;
    }

  /* A VRF id is what an operator types; a FIB index is what the datapath
   * compares. Resolved here so that a VRF which does not exist is refused
   * while somebody is still looking at the command, rather than becoming a
   * pool that quietly matches nothing. */
  if (cfg.vrf_id != ~0u)
    {
      cfg.fib_index = fib_table_find (FIB_PROTOCOL_IP4, cfg.vrf_id);
      if (cfg.fib_index == ~0u)
	{
	  error = clib_error_return (
	    0, "vrf %u does not exist; create it before a pool selects on it",
	    cfg.vrf_id);
	  goto done;
	}
    }
  if (cfg.out_vrf_id != ~0u)
    {
      cfg.out_fib_index = fib_table_find (FIB_PROTOCOL_IP4, cfg.out_vrf_id);
      if (cfg.out_fib_index == ~0u)
	{
	  error = clib_error_return (0, "outside-vrf %u does not exist",
				     cfg.out_vrf_id);
	  goto done;
	}
    }

  cfg.name = name;
  cfg.port_min = (u16) port_min;
  cfg.port_max = (u16) port_max;
  cfg.block_size = (u16) block;

  if (is_check)
    {
      error = bpa_pool_validate (&cfg);
      if (!error)
	{
	  bpa_main_t *bm = &bpa_main;
	  uword *q;

	  vlib_cli_output (vm, "pool '%v' is valid", name);

	  /*
	   * If a pool of this name is running, say what replacing it would do
	   * to the subscribers on it. In numbers and with examples: an
	   * operator told "this changes the mapping" hears the box being
	   * cautious, and an operator told "1024 of 1024 subscribers move,
	   * and 100.64.0.5 goes from here to there" knows what they are
	   * about to do.
	   */
	  q = hash_get_mem (bm->pool_by_name, name);
	  if (q)
	    {
	      bpa_pool_t *now = pool_elt_at_index (bm->pools, q[0]);
	      bpa_pool_t probe = cfg;
	      u32 example[2], total = 0, moved;

	      probe.statics = 0;
	      if (!bpa_pool_init (&probe) && now->mode == BPA_MODE_DETERMINISTIC
		  && probe.mode == BPA_MODE_DETERMINISTIC)
		{
		  moved = bpa_pool_remap_count (now, &probe, &total, example,
						ARRAY_LEN (example));
		  if (moved)
		    {
		      u32 k;

		      u32 ti, live = 0;

		      /* How many of them are holding state right now. The
		       * mapping changes for all of them either way - a past
		       * log record was computed with the old definition - but
		       * what an operator wants to know first is how many
		       * customers are about to be cut off.
		       *
		       * Under the barrier explicitly, because this command is
		       * mp-safe and the dispatcher no longer takes one for
		       * it: sub_by_idx is a worker's hash and a worker adds to
		       * it on the packet path, so reading it while that
		       * happens is a walk through a table being rehashed.
		       * One pass over the subscribers of a check the operator
		       * asked for, not something the datapath waits on
		       * routinely. */
		      vlib_worker_thread_barrier_sync (vm);
		      for (ti = 0; ti < vec_len (bm->per_thread); ti++)
			{
			  bpa_per_thread_t *pt =
			    vec_elt_at_index (bm->per_thread, ti);
			  uword key, value;

			  if (!pt->initialised)
			    continue;
			  /* clang-format off */
			  hash_foreach (key, value, pt->sub_by_idx,
			  ({
			    (void) value;
			    if ((u32) (key >> 32) == q[0])
			      live++;
			  }));
			  /* clang-format on */
			}
		      vlib_worker_thread_barrier_release (vm);

		      vlib_cli_output (
			vm,
			"remap: %u of %u subscribers change public address or "
			"port block; %u hold live state right now",
			moved, total, live);
		      for (k = 0; k < moved && k < ARRAY_LEN (example); k++)
			{
			  bpa_block_t a, b;
			  ip4_address_t pa, pb, pv;

			  if (bpa_forward (now, example[k], &a))
			    continue;
			  pv.as_u32 = clib_host_to_net_u32 (example[k]);
			  pa.as_u32 = clib_host_to_net_u32 (a.ip_pub);
			  if (bpa_forward (&probe, example[k], &b))
			    {
			      vlib_cli_output (
				vm, "remap:   %U was %U:%u-%u, now served by "
				    "no block at all",
				format_ip4_address, &pv, format_ip4_address,
				&pa, a.port_lo, a.port_hi);
			      continue;
			    }
			  pb.as_u32 = clib_host_to_net_u32 (b.ip_pub);
			  vlib_cli_output (
			    vm, "remap:   %U moves from %U:%u-%u to %U:%u-%u",
			    format_ip4_address, &pv, format_ip4_address, &pa,
			    a.port_lo, a.port_hi, format_ip4_address, &pb,
			    b.port_lo, b.port_hi);
			}
		      vlib_cli_output (
			vm, "remap: every session on this pool ends when it is "
			    "applied. Past answers stay resolvable - the "
			    "configuration trail records which definition was "
			    "in force - but the subscribers do not.");
		    }
		}
	    }
	}
    }
  else
    error = bpa_pool_add (&cfg, 0);

  /* After it is applied, never instead of applying it. */
  if (!error)
    {
      /* On a copy, the way bpa_pool_validate() computes derived values: the
       * stride warning needs blocks_per_ip, which only exists after
       * bpa_pool_init(), and `cfg` is the operator's input rather than a
       * built pool. A copy also covers `check`, where the operator wants to
       * hear about the stride before the pool exists. */
      bpa_pool_t probe = cfg;

      probe.statics = 0;
      bpa_timeouts_warn (vm, &cfg.timeouts, name);
      if (!bpa_pool_init (&probe))
	{
	  /* The rows only, for BOTH of them. When this command CREATED the
	   * pool, bpa_pool_add() has already written the record - warning
	   * again here would log the same sentence about the same pool twice
	   * - and when it was `check`, there is no pool for a record to be
	   * about. #372 and #373 arrived at this separately, one warning
	   * each, and the argument is the same one. */
	  bpa_stride_say (vm, &probe);
	  bpa_reserve_say (vm, &probe);
	}
    }

done:
  vec_free (in_b);
  vec_free (in_c);
  vec_free (out_b);
  vec_free (out_c);
  vec_free (bys_b);
  vec_free (bys_c);
  vec_free (byd_b);
  vec_free (byd_c);
  /* bpa_pool_add() takes its own copy, exactly as it does for the name. */
  vec_free (cfg.statics);
  vec_free (name);
  unformat_free (line_input);
  return error;
}

VLIB_CLI_COMMAND (bpa_pool_add_command, static) = {
  .path = "bpa pool",
  .short_help =
    "bpa pool <name> inside <pfx>/<len> [inside <pfx>/<len> ...] "
    "outside <pfx>/<len> [outside <pfx>/<len> ...] "
    "[block <n>] [ports <min>-<max>] "
    "[policy interleaved|blocked] [mode deterministic|dynamic] "
    "[alg <ftp|rtsp|pptp|esp|sip> on|off] "
    "[bypass <destination|source> <pfx>/<len>] "
    "[vrf <id>] [outside-vrf <id>] "
    "[utilisation-alarm <percent>] "
    "[max-sessions <n>] [max-session-rate <n>] "
    "[tcp-mss <bytes|auto>] "
    "[max-blocks <n>] [initial-blocks <n>] [block-stride <n>] [reserve <n>] "
    "[forward <tcp|udp> <public-port> to <private-ip> <private-port>] "
    "[check] | bpa pool <name> del",
  .function = bpa_pool_add_command_fn,
  /*
   * Runs without the CLI dispatcher's barrier, and everything under it takes
   * its own. Not a micro-optimisation: bpa_pool_del() frees a pool's sessions
   * in budgeted chunks so the datapath can run in between, and a chunk cannot
   * release a barrier the dispatcher is holding on its behalf - the release
   * is a no-op at recursion depth, which VPP's own event log says out loud
   * ("bar-relrrec"). Without this line the delete is budgeted and the outage
   * is exactly as long as it was.
   */
  .is_mp_safe = 1,
};

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_show_pool_command_fn (vlib_main_t *vm, unformat_input_t *input,
			  vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;

  if (pool_elts (bm->pools) == 0)
    {
      vlib_cli_output (vm, "no pools configured");
      return 0;
    }

  pool_foreach (p, bm->pools)
    {
      vlib_cli_output (vm, "%U", format_bpa_pool, p);
      /*
      /*
       * And the two facts the rows above cannot carry, because neither is a
       * property of the pool alone.
       *
       * The stride first. format_bpa_pool() prints "stride 4 (2048 ports
       * between a subscriber's blocks, on its own public address)", which is
       * a promise about layout, and whether it holds depends on the thread
       * count the box happened to start with - which the pool does not carry
       * and the formatter cannot read. Left unsaid, this screen states a
       * layout the box is not producing. The reserve is the same kind of
       * fact, about the same thread count.
       *
       * Said HERE because it is the only readback there is. A pool applied
       * over the binary API has no terminal to print to - bpa_pool_add() and
       * bpa_pool_settings_set() warn with a null vm, so only the log hears
       * them - and prismash reads this command back after a commit to put both
       * in front of the operator who typed the pool. Through the SAME
       * functions that say them there: a second sentence here, worded or
       * computed differently, would be a second thing to keep in step.
       *
       * They cost nothing on every other pool: each say prints nothing at all
       * unless its own arithmetic really does come up short.
       *
       * KEEP THESE LAST IN THIS LOOP BODY, and keep any other warning said
       * here BESIDE them rather than after them. prismash relays these by taking
       * everything between a `warning:` row and the next row opening with
       * `pool `, because the message folds and only its first row carries a
       * word to look for (#271). Anything printed about a pool AFTER this
       * call would be relayed to the operator as part of the warning. The
       * constraint is written here, where somebody adding a row would be
       * looking, and not only in prismash, where they would not.
       */
      bpa_stride_say (vm, p);
      bpa_reserve_say (vm, p);
    }
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_pool_command, static) = {
  .path = "show bpa pool",
  .short_help = "show bpa pool",
  .function = bpa_show_pool_command_fn,
};

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_interface_command_fn (vlib_main_t *vm, unformat_input_t *input,
			  vlib_cli_command_t *cmd)
{
  unformat_input_t _line_input, *line_input = &_line_input;
  vnet_main_t *vnm = vnet_get_main ();
  clib_error_t *error = 0;
  u32 sw_if_index = ~0;
  int is_in = -1, enable = 1;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (0, "expected direction and interface");

  while (unformat_check_input (line_input) != UNFORMAT_END_OF_INPUT)
    {
      if (unformat (line_input, "in2out"))
	is_in = 1;
      else if (unformat (line_input, "out2in"))
	is_in = 0;
      else if (unformat (line_input, "del"))
	enable = 0;
      else if (unformat (line_input, "%U", unformat_vnet_sw_interface, vnm,
			 &sw_if_index))
	;
      else
	{
	  error = clib_error_return (0, "unknown input '%U'",
				     format_unformat_error, line_input);
	  goto done;
	}
    }

  if (is_in < 0)
    {
      error = clib_error_return (0, "specify in2out or out2in");
      goto done;
    }
  if (sw_if_index == ~0)
    {
      error = clib_error_return (0, "interface is required");
      goto done;
    }

  {
    /* Everything an attach entails, including the trail entry: typed here
     * there is no session to ask, so it is recorded as unattributed via cli -
     * which is the honest answer and not the same as leaving the change out
     * of the record. */
    int rv = bpa_interface_feature_enable_disable (sw_if_index, is_in, enable);

    if (rv)
      error = clib_error_return (0, "vnet_feature_enable_disable failed: %d",
				 rv);
  }

done:
  unformat_free (line_input);
  return error;
}

VLIB_CLI_COMMAND (bpa_interface_command, static) = {
  .path = "set interface bpa",
  .short_help = "set interface bpa <in2out|out2in> <interface> [del]",
  .function = bpa_interface_command_fn,
};

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_show_session_command_fn (vlib_main_t *vm, unformat_input_t *input,
			     vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  bpa_per_thread_t *pt;
  u32 i, total = 0;

  vec_foreach_index (i, bm->per_thread)
    {
      pt = vec_elt_at_index (bm->per_thread, i);
      vlib_cli_output (vm, "thread %u: %u sessions, %u active subscribers, "
			   "%u expired",
		       i, pool_elts (pt->sessions), pool_elts (pt->subs),
		       pt->n_expired);
      total += pool_elts (pt->sessions);
    }
  vlib_cli_output (vm, "total: %u sessions", total);
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_session_command, static) = {
  .path = "show bpa session",
  .short_help = "show bpa session",
  .function = bpa_show_session_command_fn,
};

/*
 * Troubleshooting commands.
 *
 * The five that existed before report aggregates: "thread 0: 3 sessions, 2
 * active subscribers". That is enough to know the box is working and useless
 * for the question an operator actually arrives with, which is always about
 * one customer - "100.64.0.5 says nothing works" - or its inverse, "who was
 * behind this address and port".
 *
 * None of these walks the session table. Everything below reads the
 * per-subscriber record, which has one entry per active subscriber rather than
 * one per session, or answers by arithmetic. A "show" that stalls forwarding
 * is worse than no "show".
 */

/* not a unit: a lookup in VPP's per-thread hash and pool of subscriber
 * records, keyed by bpa_sub_key() (test/test_bpa_keys.c); lab/two-pools.sh
 * reads "show bpa subscriber" through it (#472) */
static bpa_sub_t *
bpa_sub_find (bpa_main_t *bm, u32 pool_index, u32 sub_idx,
	      bpa_per_thread_t **pt_out)
{
  u32 ti = bpa_sub_thread (bm, sub_idx);
  bpa_per_thread_t *pt;
  uword *q;

  if (ti >= vec_len (bm->per_thread))
    return 0;
  pt = vec_elt_at_index (bm->per_thread, ti);
  if (!pt->initialised)
    return 0;
  q = hash_get (pt->sub_by_idx, bpa_sub_key (pool_index, sub_idx));
  if (!q)
    return 0;
  if (pt_out)
    *pt_out = pt;
  return pool_elt_at_index (pt->subs, q[0]);
}

/* not a unit: finds the subscriber's thread in VPP's per-thread state and
 * asks bpa_dyn_held(), which test/test_bpa_dyn.c holds (#472) */
static int
bpa_sub_block_held (bpa_main_t *bm, const bpa_pool_t *p, u32 pool_index,
		    u32 sub_idx, bpa_block_t *blk)
{
  u32 ti = bpa_sub_thread (bm, sub_idx), w, nw;
  bpa_per_thread_t *pt;

  if (ti >= vec_len (bm->per_thread))
    return 0;
  pt = vec_elt_at_index (bm->per_thread, ti);
  if (pool_index >= vec_len (pt->dyn))
    return 0;
  bpa_worker_id (bm, ti, &w, &nw);
  return bpa_dyn_held (vec_elt_at_index (pt->dyn, pool_index), p, sub_idx, w,
		       nw, blk);
}

/*
 * Is this subscriber's block held by the configuration rather than by its
 * traffic?
 *
 * Asked with the subscriber's index alone, because the pinned block is always
 * the one whose index IS that index - a forward is pinned to the block the
 * pool's arithmetic gives the subscriber, and nothing else can be pinned.
 */
/* not a unit: finds the subscriber's thread in VPP's per-thread state and
 * asks bpa_dyn_is_pinned(), which test/test_bpa_dyn.c holds (#472) */
static int
bpa_sub_block_pinned (bpa_main_t *bm, const bpa_pool_t *p, u32 pool_index,
		      u32 sub_idx)
{
  u32 ti = bpa_sub_thread (bm, sub_idx), w, nw;
  bpa_per_thread_t *pt;

  if (p->mode != BPA_MODE_DYNAMIC || ti >= vec_len (bm->per_thread))
    return 0;
  pt = vec_elt_at_index (bm->per_thread, ti);
  if (pool_index >= vec_len (pt->dyn))
    return 0;

  bpa_worker_id (bm, ti, &w, &nw);
  return bpa_dyn_is_pinned (vec_elt_at_index (pt->dyn, pool_index), sub_idx,
			    nw);
}

/*
 * Which pool this address belongs to, for a command that has no packet.
 *
 * bpa_pool_by_inside() answers with the FIRST pool that claims the address,
 * because that is what the datapath does with a packet in hand. This command
 * has no VRF to offer it, so where two tenants share an inside prefix - which
 * the overlap check allows precisely because a VRF tells them apart - the
 * first claimant is not necessarily the pool the subscriber's traffic was
 * translated by. Keying the per-subscriber record on it then found nothing
 * and the command reported "no live state" over live sessions (issue #74).
 *
 * So the state decides: the pool that is holding this subscriber's sessions
 * or its block is the pool being asked about. When none is, the first
 * claimant answers as before - which tenant an idle address belongs to is a
 * question only the operator can settle, and that is issue #81.
 */
/* not a unit: walks VPP's pool of pools; the rule it applies is
 * bpa_claimant_take(), held by test/test_bpa_pool.c (#472) */
static int
bpa_subscriber_pool (bpa_main_t *bm, u32 host, u32 *pool_index, u32 *sub_idx)
{
  bpa_pool_t *p;
  int found = 0;

  pool_foreach (p, bm->pools)
    {
      bpa_block_t blk;
      u32 idx, si;

      if (p->deleted)
	continue;
      if (bpa_set_index_of (&p->inside, host, &si))
	continue;

      idx = p - bm->pools;
      if (bpa_claimant_take (&found, idx, si,
			     bpa_sub_find (bm, idx, si, 0)
			       || bpa_sub_block_held (bm, p, idx, si, &blk),
			     pool_index, sub_idx))
	return 0;
    }

  return found ? 0 : -1;
}

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_show_subscriber_command_fn (vlib_main_t *vm, unformat_input_t *input,
				vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  unformat_input_t _line_input, *line_input = &_line_input;
  clib_error_t *error = 0;
  ip4_address_t addr;
  u32 pool_index, sub_idx, i;
  bpa_pool_t *p;
  bpa_sub_t *sub;
  bpa_block_t blk;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (0, "usage: show bpa subscriber <inside-address>");
  if (!unformat (line_input, "%U", unformat_ip4_address, &addr))
    {
      error = clib_error_return (0, "expected an inside IPv4 address");
      goto done;
    }

  {
    u32 host = clib_net_to_host_u32 (addr.as_u32);

    /* No packet, so no VRF: on a box with one VRF there is one claimant; on a
     * box with several the one holding this subscriber's state answers, and
     * which tenant an idle address belongs to is issue #81. */
    if (bpa_subscriber_pool (bm, host, &pool_index, &sub_idx))
      {
	error = clib_error_return (
	  0, "%U is not inside any configured pool", format_ip4_address, &addr);
	goto done;
      }
    p = pool_elt_at_index (bm->pools, pool_index);

    vlib_cli_output (vm, "subscriber %U   pool %v, index %u, worker thread %u",
		     format_ip4_address, &addr, p->name, sub_idx,
		     bpa_sub_thread (bm, sub_idx));

    /*
     * In deterministic mode the block is knowable whether or not the
     * subscriber has ever sent a packet, and saying so matters: "no state on
     * this box" and "no block for this subscriber" are different answers.
     */
    if (p->mode == BPA_MODE_DETERMINISTIC)
      {
	if (bpa_forward (p, host, &blk) == 0)
	  {
	    ip4_address_t pub;
	    pub.as_u32 = clib_host_to_net_u32 (blk.ip_pub);
	    vlib_cli_output (vm, "  the formula gives it %U ports %u-%u",
			     format_ip4_address, &pub, blk.port_lo,
			     blk.port_hi);
	  }
	else
	  vlib_cli_output (vm, "  BEYOND CAPACITY: the pool has no block for "
			       "this address, so it is not translated at all");
      }

    sub = bpa_sub_find (bm, pool_index, sub_idx, 0);
    if (!sub)
      {
	/*
	 * Two answers, not one. The record read above is created with the
	 * subscriber's first session and destroyed with its last, while a
	 * dynamic block stays reserved for the whole hold-down after it: its
	 * absence means "no sessions", and reading it as "no block" told an
	 * operator a block was free while the allocator was still refusing it
	 * to everybody else.
	 */
	if (bpa_sub_block_held (bm, p, pool_index, sub_idx, &blk))
	  {
	    ip4_address_t pub;

	    pub.as_u32 = clib_host_to_net_u32 (blk.ip_pub);
	    vlib_cli_output (
	      vm, "  holds %U ports %u-%u, and has no sessions on this box%s",
	      format_ip4_address, &pub, blk.port_lo, blk.port_hi,
	      bpa_sub_block_pinned (bm, p, pool_index, sub_idx)
		? " - PINNED by a forward, so no hold-down will take it"
		: "");
	  }
	else
	  vlib_cli_output (vm, "  no live state: this subscriber holds no "
			       "block and has no sessions on this box");
	goto done;
      }

    {
      int pinned = bpa_sub_block_pinned (bm, p, pool_index, sub_idx);
      bpa_block_t own;
      int have_own = bpa_forward (p, clib_net_to_host_u32 (addr.as_u32),
				  &own) == 0;

      for (i = 0; i < vec_len (sub->blocks); i++)
	{
	  bpa_subblock_t *sb = vec_elt_at_index (sub->blocks, i);
	  u32 used = sb->map.n_ports - sb->map.n_free;
	  ip4_address_t pub;
	  /* The pin is on the subscriber's own block, which is not
	   * necessarily blocks[0]: a pin that arrived while the subscriber
	   * was translating out of another one leaves that other one behind
	   * as an extension. */
	  int this_pinned = pinned && have_own
			    && sb->blk.ip_pub == own.ip_pub
			    && sb->blk.port_lo == own.port_lo;

	  pub.as_u32 = clib_host_to_net_u32 (sb->blk.ip_pub);
	  vlib_cli_output (
	    vm,
	    "  block %u  %U  ports %u-%u   %u of %u in use (%u%%), %u free%s",
	    i, format_ip4_address, &pub, sb->blk.port_lo, sb->blk.port_hi,
	    used, sb->map.n_ports,
	    sb->map.n_ports ? (used * 100) / sb->map.n_ports : 0, sb->map.n_free,
	    this_pinned ? "   (pinned by a forward)"
			: (i ? "   (extension)" : ""));
	}
    }
    vlib_cli_output (vm,
		     "  sessions %u   refusals: ports %u, session cap %u, "
		     "rate cap %u",
		     sub->n_sessions, sub->n_refused,
		     sub->n_refused_sessions, sub->n_refused_rate);
    /* Three counters because they need three different answers, and the
     * operator arrives at this command with one customer's address and the
     * question "is it the box or is it them". */
    if (sub->n_refused)
      vlib_cli_output (vm, "  port refusals are not a box fault: this "
			   "subscriber asked for more ports than its block "
			   "holds");
    if (sub->n_refused_sessions)
      vlib_cli_output (vm, "  session-cap refusals: the cap is doing its job, "
			   "or it is too low for this customer");
    if (sub->n_refused_rate)
      vlib_cli_output (vm, "  rate-cap refusals: this host is opening "
			   "sessions faster than the pool allows, which is "
			   "usually an infection rather than a customer");
  }

done:
  unformat_free (line_input);
  return error;
}

VLIB_CLI_COMMAND (bpa_show_subscriber_command, static) = {
  .path = "show bpa subscriber",
  .short_help = "show bpa subscriber <inside-address>",
  .function = bpa_show_subscriber_command_fn,
};

/*
 * The inverse, and the one a court order or an abuse report asks.
 *
 * It says WHERE the answer comes from, because the two sources do not carry
 * the same weight. The formula answers for any instant, past or present, as
 * long as the pool has not been resized. A dynamic allocation answers for
 * NOW - the block was somebody else's yesterday, and only the RFC 8158 log
 * says whose.
 */
/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_show_translation_command_fn (vlib_main_t *vm, unformat_input_t *input,
				 vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  unformat_input_t _line_input, *line_input = &_line_input;
  clib_error_t *error = 0;
  ip4_address_t addr, priv;
  u32 pool_index, block_idx, sub_idx, port = 0, ip_priv = 0;
  bpa_xlt_source_t src;
  bpa_pool_t *p;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (
      0, "usage: show bpa translation <public-address> <port>");
  if (!unformat (line_input, "%U %u", unformat_ip4_address, &addr, &port))
    {
      error = clib_error_return (0, "expected a public address and a port");
      goto done;
    }
  if (port > 65535)
    {
      error = clib_error_return (0, "port %u is out of range", port);
      goto done;
    }

  {
    u32 host = clib_net_to_host_u32 (addr.as_u32);

    if (bpa_pool_by_outside (bm, host, &pool_index))
      {
	error = clib_error_return (0, "%U is not a public address of any pool",
				   format_ip4_address, &addr);
	goto done;
      }
    p = pool_elt_at_index (bm->pools, pool_index);

    src = bpa_translation_source (p, host, (u16) port, &block_idx);
    if (src == BPA_XLT_NONE)
      {
	vlib_cli_output (vm, "%U:%u belongs to pool %v but to no block: the "
			     "port is outside the pool's usable range, so "
			     "nothing was ever translated here",
			 format_ip4_address, &addr, port, p->name);
	goto done;
      }

    if (src == BPA_XLT_FORMULA)
      {
	if (bpa_set_addr_of (&p->inside, block_idx, &ip_priv))
	  {
	    error = clib_error_return (0, "block %u has no subscriber address",
				       block_idx);
	    goto done;
	  }
	priv.as_u32 = clib_host_to_net_u32 (ip_priv);
	vlib_cli_output (vm, "%U:%u  ->  %U", format_ip4_address, &addr, port,
			 format_ip4_address, &priv);
	vlib_cli_output (vm, "  pool %v, block %u, DETERMINISTIC", p->name,
			 block_idx);
	vlib_cli_output (
	  vm, "  from the FORMULA. It holds for any instant, past or present, "
	      "as long as this pool's addresses and block size have not "
	      "changed since. No allocation record is involved.");
	goto done;
      }

    /* Dynamic, or a deterministic pool's RESERVE: the answer is about now,
     * and saying otherwise would be a wrong answer to a court order given
     * with full confidence. The reserve is held by the same allocator in an
     * index space of its own, so the same question finds its holder. */
    {
      u32 ti = 0, w, nw, found = 0;

      for (ti = 0; ti < vec_len (bm->per_thread); ti++)
	{
	  bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, ti);

	  if (!pt->initialised || pool_index >= vec_len (pt->dyn))
	    continue;
	  bpa_worker_id (bm, ti, &w, &nw);
	  /* bpa_dyn_holder() and not bpa_dyn_owner(): this asks every worker,
	   * and only the one whose slice holds the block may answer (#432). */
	  if (bpa_dyn_holder (vec_elt_at_index (pt->dyn, pool_index),
			      block_idx, w, nw, &sub_idx))
	    continue;
	  if (bpa_set_addr_of (&p->inside, sub_idx, &ip_priv))
	    continue;
	  found = 1;
	  break;
	}

      if (!found)
	{
	  vlib_cli_output (vm, "%U:%u  ->  nobody, right now", format_ip4_address,
			   &addr, port);
	  vlib_cli_output (vm, "  pool %v, %s %u, %s: no subscriber "
			       "holds this block at the moment", p->name,
			   src == BPA_XLT_RESERVE ? "reserve block" : "block",
			   block_idx,
			   src == BPA_XLT_RESERVE ? "DETERMINISTIC RESERVE"
						  : "DYNAMIC");
	}
      else
	{
	  priv.as_u32 = clib_host_to_net_u32 (ip_priv);
	  vlib_cli_output (vm, "%U:%u  ->  %U", format_ip4_address, &addr,
			   port, format_ip4_address, &priv);
	  vlib_cli_output (vm, "  pool %v, %s %u, %s", p->name,
			   src == BPA_XLT_RESERVE ? "reserve block" : "block",
			   block_idx,
			   src == BPA_XLT_RESERVE ? "DETERMINISTIC RESERVE"
						  : "DYNAMIC");
	}
      vlib_cli_output (
	vm, "  this answers for NOW ONLY. The block belonged to somebody else "
	    "before and will again; for a past instant read the RFC 8158 log, "
	    "which records when each block was assigned and released.");
    }
  }

done:
  unformat_free (line_input);
  return error;
}

VLIB_CLI_COMMAND (bpa_show_translation_command, static) = {
  .path = "show bpa translation",
  .short_help = "show bpa translation <public-address> <port>",
  .function = bpa_show_translation_command_fn,
};

/*
 * The row of "show bpa top" and the order the rows are read in are in
 * bpa_top.h, which needs nothing but two rows - no pool, no VPP, no vector -
 * so test/test_bpa_top.c can drive it. That is this file's own argument
 * about its arithmetic, taken up rather than excused.
 */

/*
 * The subscribers closest to running out.
 *
 * This is the question that prevents the support call rather than answering
 * it: natEvent 12 reports exhaustion, which is to say it reports that somebody
 * has already lost traffic.
 *
 * Walks the per-subscriber table, which holds one entry per ACTIVE SUBSCRIBER
 * - not one per session. On a box with 200k subscribers and millions of
 * sessions those differ by three orders of magnitude.
 */
/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_show_top_command_fn (vlib_main_t *vm, unformat_input_t *input,
			 vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  unformat_input_t _line_input, *line_input = &_line_input;
  bpa_top_t *rows = 0, *r;
  u32 want = 10, ti, i;

  if (unformat_user (input, unformat_line_input, line_input))
    {
      (void) unformat (line_input, "%u", &want);
      unformat_free (line_input);
    }
  if (want == 0)
    want = 10;

  for (ti = 0; ti < vec_len (bm->per_thread); ti++)
    {
      bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, ti);
      uword key, value;

      if (!pt->initialised)
	continue;
      /* clang-format off */
      hash_foreach (key, value, pt->sub_by_idx,
      ({
	u32 pool_index = (u32) (key >> 32);
	u32 sub_idx = (u32) key;
	bpa_sub_t *sub;
	u32 used = 0, total = 0, ip_priv = 0;

	if (pool_is_free_index (bm->pools, pool_index))
	  continue;
	sub = pool_elt_at_index (pt->subs, value);
	for (i = 0; i < vec_len (sub->blocks); i++)
	  {
	    bpa_subblock_t *sb = vec_elt_at_index (sub->blocks, i);
	    used += sb->map.n_ports - sb->map.n_free;
	    total += sb->map.n_ports;
	  }
	if (total == 0)
	  continue;
	if (bpa_set_addr_of (&pool_elt_at_index (bm->pools, pool_index)->inside,
			     sub_idx, &ip_priv))
	  continue;
	vec_add2 (rows, r, 1);
	r->ip_priv = ip_priv;
	r->used = used;
	r->total = total;
	r->pct = (used * 100) / total;
	r->n_blocks = vec_len (sub->blocks);
	r->n_refused = sub->n_refused;
	r->pool_index = pool_index;
      }));
      /* clang-format on */
    }

  if (vec_len (rows) == 0)
    {
      vlib_cli_output (vm, "no subscriber holds a block right now");
      return 0;
    }

  vec_sort_with_function (rows, bpa_top_cmp);
  vlib_cli_output (vm, "%-16s %-12s %11s %5s %7s %9s", "subscriber", "pool",
		   "ports", "full", "blocks", "refusals");
  for (i = 0; i < vec_len (rows) && i < want; i++)
    {
      ip4_address_t a;
      bpa_pool_t *p = pool_elt_at_index (bm->pools, rows[i].pool_index);

      a.as_u32 = clib_host_to_net_u32 (rows[i].ip_priv);
      vlib_cli_output (vm, "%-16U %-12v %5u/%-5u %4u%% %7u %9u",
		       format_ip4_address, &a, p->name, rows[i].used,
		       rows[i].total, rows[i].pct, rows[i].n_blocks,
		       rows[i].n_refused);
    }
  vlib_cli_output (vm, "%u subscriber(s) holding blocks", vec_len (rows));
  vec_free (rows);
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_top_command, static) = {
  .path = "show bpa top",
  .short_help = "show bpa top [<n>]",
  .function = bpa_show_top_command_fn,
};

/*
 * Why packets were dropped, under names an operator recognises.
 *
 * "show errors" already carries these numbers, mixed in with every other node
 * in the box and truncated at 35 characters. This is the same data with only
 * the CGNAT nodes in it, summed across workers, and with the zero rows left
 * out - during an incident the interesting line is the one that is not zero.
 */
/* Which counters mean a packet did not get through; see bpa_errors.h. */
static u8 bpa_in2out_is_drop[] = {
#define _(sym, drop, str) drop,
  foreach_bpa_in2out_error
#undef _
};

static u8 bpa_out2in_is_drop[] = {
#define _(sym, drop, str) drop,
  foreach_bpa_out2in_error
#undef _
};

/* not a unit: it renders through vlib_cli_output() - see the header */
static u64
bpa_counter_total (vlib_main_t *vm, vlib_node_t *n, u32 i)
{
  u64 total = 0;
  u32 t;

  /* Summed across workers: a per-thread number answers a question nobody
   * asked, and a subscriber is only on one worker anyway. */
  for (t = 0; t < vlib_get_n_threads (); t++)
    {
      vlib_main_t *tvm = vlib_get_main_by_index (t);

      if (!tvm)
	continue;
      if (n->error_heap_index + i < vec_len (tvm->error_main.counters))
	total += tvm->error_main.counters[n->error_heap_index + i];
    }
  return total;
}

/* not a unit: it renders through vlib_cli_output() - see the header */
static void
bpa_show_drops_node (vlib_main_t *vm, u32 node_index, const char *label,
		     const u8 *is_drop, u32 n_is_drop)
{
  vlib_node_t *n = vlib_get_node (vm, node_index);
  u32 i, dropped = 0, other = 0;

  vlib_cli_output (vm, "%s", label);

  /* Drops first, and alone under their own heading. "show errors" lists
   * "packets translated" beside "no pool for source address" as though they
   * were the same kind of thing; during an incident they are not. */
  for (i = 0; i < n->n_errors; i++)
    {
      u64 total;

      if (i >= n_is_drop || !is_drop[i])
	continue;
      total = bpa_counter_total (vm, n, i);
      if (total == 0)
	continue;
      if (!dropped++)
	vlib_cli_output (vm, "  packets that did not get through");
      /* The description lives in the global error heap rather than on the
       * node: vlib_node_t carries only where its block starts. */
      vlib_cli_output (vm, "  %12Lu  %s", total,
		       vm->error_main.counters_heap[n->error_heap_index + i]
			 .desc);
    }
  if (!dropped)
    vlib_cli_output (vm, "  nothing dropped");

  for (i = 0; i < n->n_errors; i++)
    {
      u64 total;

      if (i < n_is_drop && is_drop[i])
	continue;
      total = bpa_counter_total (vm, n, i);
      if (total == 0)
	continue;
      if (!other++)
	vlib_cli_output (vm, "  everything else it counted");
      vlib_cli_output (vm, "  %12Lu  %s", total,
		       vm->error_main.counters_heap[n->error_heap_index + i]
			 .desc);
    }
}

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_show_drops_command_fn (vlib_main_t *vm, unformat_input_t *input,
			   vlib_cli_command_t *cmd)
{
  bpa_show_drops_node (vm, bpa_in2out_node.index, "bpa-in2out",
		       bpa_in2out_is_drop, ARRAY_LEN (bpa_in2out_is_drop));
  bpa_show_drops_node (vm, bpa_out2in_node.index, "bpa-out2in",
		       bpa_out2in_is_drop, ARRAY_LEN (bpa_out2in_is_drop));
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_drops_command, static) = {
  .path = "show bpa drops",
  .short_help = "show bpa drops",
  .function = bpa_show_drops_command_fn,
};

/*
 * Change a pool's filtering mode in place.
 *
 * Separate from "bpa pool", which creates, because recreating a pool to change
 * one setting drops every session it holds - and the whole point of choosing
 * between endpoint-independent and address-dependent filtering is that an
 * operator can decide, on a box carrying traffic, without disconnecting the
 * subscribers whose behaviour is being changed.
 */
/*
 * Change a pool's MSS clamp in place.
 *
 * Separate from "bpa pool" for the reason "bpa filter" and "bpa alg" are:
 * recreating a pool to change one setting drops every session it holds. An
 * operator turning clamping on is answering "some sites do not load" on a box
 * that is carrying traffic, and disconnecting everybody to do it would trade
 * one complaint for an outage.
 *
 * Existing connections keep whatever MSS they negotiated. There is nothing to
 * fix retroactively: the option is only in the handshake, and a connection
 * that is already suffering the black hole will be reopened by whatever is
 * suffering it.
 */
/*
 * A pool's per-subscriber bounds, in place.
 *
 * Both are given together, and omitting one sets it to unlimited, because a
 * command that silently keeps whichever half you did not mention is a command
 * whose effect depends on what was there before.
 */
/*
 * Replaces one of a pool's bypass lists, in place.
 *
 * The WHOLE list, because bpa_addr_set_build() merges adjacent prefixes into
 * ranges: after building, the set no longer remembers which prefixes were
 * typed, and "remove one of them" from a merged range has no honest answer.
 * Setting the list entire has one, and it is what a candidate configuration
 * produces anyway.
 *
 * "none" empties it, which is how a bypass is removed.
 */
/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_bypass_command_fn (vlib_main_t *vm, unformat_input_t *input,
		       vlib_cli_command_t *cmd)
{
  unformat_input_t _line_input, *line_input = &_line_input;
  clib_error_t *error = 0;
  ip4_address_t addr;
  u32 *bases = 0, *counts = 0;
  u32 len = 0, pfx_base, pfx_count;
  u8 *name = 0;
  int is_dst = -1;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (
      0, "usage: bpa bypass <pool> <destination|source> <pfx>/<len> ... | none");

  if (!unformat (line_input, "%v", &name))
    {
      error = clib_error_return (0, "missing pool name");
      goto done;
    }
  if (unformat (line_input, "destination"))
    is_dst = 1;
  else if (unformat (line_input, "source"))
    is_dst = 0;
  else
    {
      error = clib_error_return (0, "expected destination or source");
      goto done;
    }

  while (unformat_check_input (line_input) != UNFORMAT_END_OF_INPUT)
    {
      if (unformat (line_input, "none"))
	continue;
      if (unformat (line_input, "%U/%d", unformat_ip4_address, &addr, &len))
	{
	  if (bpa_prefix_range (clib_net_to_host_u32 (addr.as_u32), len,
				&pfx_base, &pfx_count))
	    {
	      error = clib_error_return (0, "prefix length %u invalid", len);
	      goto done;
	    }
	  vec_add1 (counts, pfx_count);
	  vec_add1 (bases, pfx_base);
	  continue;
	}
      error = clib_error_return (0, "unknown input '%U'",
				 format_unformat_error, line_input);
      goto done;
    }

  error = bpa_bypass_set (name, is_dst, bases, counts, vec_len (bases));
  if (!error)
    vlib_cli_output (vm, "pool %v: %u %s prefix(es) bypassed", name,
		     vec_len (bases), is_dst ? "destination" : "source");

done:
  vec_free (bases);
  vec_free (counts);
  vec_free (name);
  unformat_free (line_input);
  return error;
}

VLIB_CLI_COMMAND (bpa_bypass_command, static) = {
  .path = "bpa bypass",
  .short_help = "bpa bypass <pool> <destination|source> <pfx>/<len> [...] "
		"| none",
  .function = bpa_bypass_command_fn,
};

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_limits_command_fn (vlib_main_t *vm, unformat_input_t *input,
		       vlib_cli_command_t *cmd)
{
  unformat_input_t _line_input, *line_input = &_line_input;
  clib_error_t *error = 0;
  u32 max_sessions = 0, max_rate = 0;
  u8 *name = 0;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (
      0, "usage: bpa limits <pool> [max-sessions <n>] [max-session-rate <n>]");

  if (!unformat (line_input, "%v", &name))
    {
      error = clib_error_return (0, "missing pool name");
      goto done;
    }

  while (unformat_check_input (line_input) != UNFORMAT_END_OF_INPUT)
    {
      if (unformat (line_input, "max-sessions %u", &max_sessions))
	;
      else if (unformat (line_input, "max-session-rate %u", &max_rate))
	;
      else if (unformat (line_input, "none"))
	max_sessions = max_rate = 0;
      else
	{
	  error = clib_error_return (0, "unknown input '%U'",
				     format_unformat_error, line_input);
	  goto done;
	}
    }

  error = bpa_limits_set (name, max_sessions, max_rate);
  if (!error)
    vlib_cli_output (vm, "pool %v: sessions %s, rate %s", name,
		     max_sessions ? "capped" : "unlimited",
		     max_rate ? "capped" : "unlimited");

done:
  vec_free (name);
  unformat_free (line_input);
  return error;
}

VLIB_CLI_COMMAND (bpa_limits_command, static) = {
  .path = "bpa limits",
  .short_help =
    "bpa limits <pool> [max-sessions <n>] [max-session-rate <n>] | none",
  .function = bpa_limits_command_fn,
};

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_mss_command_fn (vlib_main_t *vm, unformat_input_t *input,
		    vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  unformat_input_t _line_input, *line_input = &_line_input;
  clib_error_t *error = 0;
  u32 mss = 0;
  u8 *name = 0;
  int is_auto = 0, is_off = 0, found = 0;
  bpa_pool_t *p;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (0, "usage: bpa tcp-mss <pool> <bytes|auto|off>");

  if (!unformat (line_input, "%v", &name))
    {
      error = clib_error_return (0, "missing pool name");
      goto done;
    }
  if (unformat (line_input, "auto"))
    is_auto = 1;
  else if (unformat (line_input, "off") || unformat (line_input, "0"))
    is_off = 1;
  else if (!unformat (line_input, "%u", &mss))
    {
      error = clib_error_return (0, "expected a size in bytes, auto, or off");
      goto done;
    }

  if (!is_auto && !is_off && (mss < BPA_MSS_MIN || mss > BPA_MSS_MAX))
    {
      error = clib_error_return (
	0, "tcp-mss %u is outside %u-%u: below the MSS every IPv4 host must "
	   "accept (RFC 1122) the clamp is legal and useless, and above a "
	   "jumbo frame it describes no path",
	mss, BPA_MSS_MIN, BPA_MSS_MAX);
      goto done;
    }

  pool_foreach (p, bm->pools)
    if (vec_len (p->name) == vec_len (name)
	&& !memcmp (p->name, name, vec_len (name)))
      {
	p->tcp_mss_auto = is_auto;
	p->tcp_mss = is_auto || is_off ? 0 : (u16) mss;
	found = 1;
	break;
      }

  if (!found)
    error = clib_error_return (0, "no such pool '%v'", name);
  else if (is_auto && bm->auto_mss == 0)
    /* Not an error: the pool is configured and will clamp the moment an
     * interface joins the CGNAT. Silence here would look like it was
     * working. */
    vlib_cli_output (vm, "note: no CGNAT interface has an MTU yet, so 'auto' "
			 "clamps nothing until one does");

done:
  vec_free (name);
  unformat_free (line_input);
  return error;
}

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_filter_command_fn (vlib_main_t *vm, unformat_input_t *input,
		       vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  unformat_input_t _line_input, *line_input = &_line_input;
  clib_error_t *error = 0;
  bpa_filter_t f;
  bpa_pool_t *p;
  u8 *name = 0;
  int found = 0;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (0, "usage: bpa filter <pool> "
				"<endpoint-independent|address-dependent>");

  if (!unformat (line_input, "%v", &name))
    {
      error = clib_error_return (0, "missing pool name");
      goto done;
    }
  if (unformat (line_input, "endpoint-independent"))
    f = BPA_FILTER_ENDPOINT_INDEPENDENT;
  else if (unformat (line_input, "address-dependent"))
    f = BPA_FILTER_ADDRESS_DEPENDENT;
  else
    {
      error = clib_error_return (0, "expected endpoint-independent or "
				    "address-dependent");
      goto done;
    }

  pool_foreach (p, bm->pools)
    if (vec_len (p->name) == vec_len (name)
	&& !memcmp (p->name, name, vec_len (name)))
      {
	p->filter = f;
	found = 1;
	break;
      }

  if (!found)
    error = clib_error_return (0, "no such pool '%v'", name);

done:
  vec_free (name);
  unformat_free (line_input);
  return error;
}

VLIB_CLI_COMMAND (bpa_mss_command, static) = {
  .path = "bpa tcp-mss",
  .short_help = "bpa tcp-mss <pool> <bytes|auto|off>",
  .function = bpa_mss_command_fn,
};

VLIB_CLI_COMMAND (bpa_filter_command, static) = {
  .path = "bpa filter",
  .short_help = "bpa filter <pool> <endpoint-independent|address-dependent>",
  .function = bpa_filter_command_fn,
};

/*
 * A static port forward, added to or removed from a running pool.
 *
 * The public ADDRESS is not asked for and cannot be given: it is the
 * subscriber's own, the one the pool's arithmetic assigns. See bpa_static_t.
 */
/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_forward_command_fn (vlib_main_t *vm, unformat_input_t *input,
			vlib_cli_command_t *cmd)
{
  unformat_input_t _line_input, *line_input = &_line_input;
  clib_error_t *error = 0;
  bpa_static_t sf = { 0 };
  ip4_address_t priv = { 0 };
  u32 port_pub = 0, port_priv = 0;
  int is_del = 0, is_check = 0, have_proto = 0, have_pub = 0, have_to = 0;
  u8 *name = 0;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (
      0, "usage: bpa forward <pool> <tcp|udp> <public-port> "
	 "to <private-ip> <private-port>");

  if (!unformat (line_input, "%v", &name))
    {
      error = clib_error_return (0, "missing pool name");
      goto done;
    }

  while (unformat_check_input (line_input) != UNFORMAT_END_OF_INPUT)
    {
      if (unformat (line_input, "tcp"))
	sf.proto = BPA_PROTO_TCP, have_proto = 1;
      else if (unformat (line_input, "udp"))
	sf.proto = BPA_PROTO_UDP, have_proto = 1;
      else if (unformat (line_input, "to %U %u", unformat_ip4_address, &priv,
			 &port_priv))
	have_to = 1;
      else if (unformat (line_input, "del"))
	is_del = 1;
      else if (unformat (line_input, "check"))
	is_check = 1;
      else if (!have_pub && unformat (line_input, "%u", &port_pub))
	have_pub = 1;
      else
	{
	  error = clib_error_return (0, "unknown input '%U'",
				     format_unformat_error, line_input);
	  goto done;
	}
    }

  if (!have_proto || !have_pub)
    {
      error = clib_error_return (0, "a protocol and a public port are "
				    "required");
      goto done;
    }
  if (!is_del && !have_to)
    {
      error = clib_error_return (0, "missing 'to <private-ip> "
				    "<private-port>'");
      goto done;
    }
  if (port_pub == 0 || port_pub > 65535 || (!is_del && (port_priv == 0
						       || port_priv > 65535)))
    {
      error = clib_error_return (0, "port out of range");
      goto done;
    }

  sf.ip_priv = clib_net_to_host_u32 (priv.as_u32);
  sf.port_pub = (u16) port_pub;
  sf.port_priv = (u16) port_priv;

  /* Deleting names the public port and nothing else, because that pair is
   * what a forward is. The private side is accepted and ignored, so that
   * removing one can be the add command with "del" on the end - which is what
   * an operator with the line in their history will type. */
  error = bpa_static_add_del (name, &sf, !is_del, is_check);
  if (!error && is_check)
    vlib_cli_output (vm, "forward is valid");

done:
  vec_free (name);
  unformat_free (line_input);
  return error;
}

VLIB_CLI_COMMAND (bpa_forward_command, static) = {
  .path = "bpa forward",
  .short_help = "bpa forward <pool> <tcp|udp> <public-port> to <private-ip> "
		"<private-port> [check] | bpa forward <pool> <tcp|udp> "
		"<public-port> del",
  .function = bpa_forward_command_fn,
};

/*
 * Which interfaces have unicast RPF turned on, and in which mode.
 *
 * uRPF is VPP's own feature and this plugin does not own it - "set urpf ip4 rx
 * <off|loose|strict> <interface>" enables it. What is missing there is a way to
 * ask, in one call, what is currently on: VPP answers per interface, in a page
 * of human-shaped text, and the VTY has to read the answer back on every commit
 * to know whether its own change took effect.
 *
 * It is reported here rather than left out because on a CGNAT it is not an
 * ordinary interface setting: without it a spoofed source is translated and
 * attributed to a subscriber who did not send the packet, and the record that
 * says so is indistinguishable from a true one. See docs/architecture.md.
 *
 * One line per interface, "<name> <mode>", which is what a machine reads and
 * what an operator can also make sense of.
 */
/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_show_urpf_command_fn (vlib_main_t *vm, unformat_input_t *input,
			  vlib_cli_command_t *cmd)
{
  vnet_main_t *vnm = vnet_get_main ();
  vnet_sw_interface_t *si;
  u32 n = 0;

  pool_foreach (si, vnm->interface_main.sw_interfaces)
    {
      const char *mode = 0;

      if (vnet_feature_is_enabled ("ip4-unicast", "ip4-rx-urpf-strict",
				   si->sw_if_index) == 1)
	mode = "strict";
      else if (vnet_feature_is_enabled ("ip4-unicast", "ip4-rx-urpf-loose",
					si->sw_if_index) == 1)
	mode = "loose";

      if (!mode)
	continue;

      vlib_cli_output (vm, "%U %s", format_vnet_sw_if_index_name, vnm,
		       si->sw_if_index, mode);
      n++;
    }

  if (n == 0)
    vlib_cli_output (vm, "no interface has uRPF enabled");
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_urpf_command, static) = {
  .path = "show bpa urpf",
  .short_help = "show bpa urpf",
  .function = bpa_show_urpf_command_fn,
};

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_show_forward_command_fn (vlib_main_t *vm, unformat_input_t *input,
			     vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;
  u8 *want = 0;
  u32 total = 0;

  unformat (input, "%v", &want);

  pool_foreach (p, bm->pools)
    {
      ip4_address_t pub, priv;
      u32 i;

      if (want && (vec_len (want) != vec_len (p->name)
		   || memcmp (want, p->name, vec_len (want))))
	continue;
      if (vec_len (p->statics) == 0)
	continue;

      vlib_cli_output (vm, "pool %v", p->name);
      vec_foreach_index (i, p->statics)
	{
	  const bpa_static_t *sf = &p->statics[i];

	  pub.as_u32 = clib_host_to_net_u32 (sf->ip_pub);
	  priv.as_u32 = clib_host_to_net_u32 (sf->ip_priv);
	  vlib_cli_output (vm, "  %U %U:%u -> %U:%u", format_bpa_proto,
			   sf->proto, format_ip4_address, &pub, sf->port_pub,
			   format_ip4_address, &priv, sf->port_priv);
	  total++;
	}
    }

  if (total == 0)
    vlib_cli_output (vm, "no forwards configured");
  vec_free (want);
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_forward_command, static) = {
  .path = "show bpa forward",
  .short_help = "show bpa forward [<pool>]",
  .function = bpa_show_forward_command_fn,
};

/*
 * Turn an ALG on or off for a pool, in place.
 *
 * Separate from "bpa pool" for the same reason "bpa filter" is: recreating a
 * pool to change one setting drops every session it holds. An operator who
 * has just been told an ALG is mangling a customer's transfer needs to switch
 * it off on a box that is carrying traffic, and if doing so disconnects
 * everybody the switch is not usable at the moment it is needed.
 *
 * Sessions already open keep whatever was done to them. Turning an ALG off
 * does not close the mappings it opened and does not stop the sequence
 * renumbering a lengthened connection depends on - the datapath decides that
 * from the session, not from this switch - so it stops new rewrites without
 * breaking the connections it already touched.
 */
/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_alg_command_fn (vlib_main_t *vm, unformat_input_t *input,
		    vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  unformat_input_t _line_input, *line_input = &_line_input;
  clib_error_t *error = 0;
  bpa_pool_t *p;
  u8 *name = 0;
  u32 which = 0;
  int on = -1, found = 0;

  if (!unformat_user (input, unformat_line_input, line_input))
    return clib_error_return (
      0, "usage: bpa alg <pool> <ftp|rtsp|pptp|esp|sip> <on|off>");

  if (!unformat (line_input, "%v", &name))
    {
      error = clib_error_return (0, "missing pool name");
      goto done;
    }
#define _(sym, nm, desc)                                                      \
  if (unformat (line_input, nm))                                              \
    which = BPA_ALG_##sym;                                                    \
  else
  foreach_bpa_alg
#undef _
    {
      error =
	clib_error_return (0, "expected an ALG name (ftp, rtsp, pptp, esp, sip)");
      goto done;
    }
  /* "enable"/"disable" are accepted as well as "on"/"off": the three ALGs
   * were written separately and shipped both spellings, and refusing the one
   * an operator already has in a script buys nothing. */
  if (unformat (line_input, "on") || unformat (line_input, "enable"))
    on = 1;
  else if (unformat (line_input, "off") || unformat (line_input, "disable"))
    on = 0;
  else
    {
      error = clib_error_return (0, "expected on or off");
      goto done;
    }

  pool_foreach (p, bm->pools)
    if (vec_len (p->name) == vec_len (name)
	&& !memcmp (p->name, name, vec_len (name)))
      {
	if (on)
	  p->alg |= which;
	else
	  p->alg &= ~which;
	p->alg_set |= which;
	vlib_cli_output (vm, "%v: %U", p->name, format_bpa_alg,
			 p->alg);
	found = 1;
	break;
      }

  if (!found)
    error = clib_error_return (0, "no such pool '%v'", name);

done:
  vec_free (name);
  unformat_free (line_input);
  return error;
}

VLIB_CLI_COMMAND (bpa_alg_command, static) = {
  .path = "bpa alg",
  .short_help = "bpa alg <pool> <ftp|rtsp|pptp|esp|sip> <on|off>",
  .function = bpa_alg_command_fn,
};

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_show_alg_command_fn (vlib_main_t *vm, unformat_input_t *input,
			 vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;

  if (pool_elts (bm->pools) == 0)
    {
      vlib_cli_output (vm, "no pools configured");
      return 0;
    }

  pool_foreach (p, bm->pools)
    {
      vlib_cli_output (vm, "%v", p->name);
#define _(sym, nm, desc)                                                      \
  vlib_cli_output (vm, "  %-6s %-3s  %s", nm,                                 \
		   (p->alg & BPA_ALG_##sym) ? "on" : "off", desc);
      foreach_bpa_alg
#undef _
    }
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_alg_command, static) = {
  .path = "show bpa alg",
  .short_help = "show bpa alg",
  .function = bpa_show_alg_command_fn,
};

/*
 * Every timeout the box will actually use, with the ones under their floor
 * marked. The mark matters as much as the number: an operator who does not
 * know to ask cannot find out that a value is unusual.
 */
/* not a unit: it renders through vlib_cli_output() - see the header */
static void
bpa_timeouts_show (vlib_main_t *vm, const bpa_timeouts_t *t, const char *what)
{
  vlib_cli_output (vm, "%s", what);
#define _(field, name, floor, rfc, breaks)                                    \
  vlib_cli_output (vm, "  %-16s %6u%s", name, t->field,                       \
		   (t->field != 0 && t->field < (floor))                      \
		     ? "   BELOW the floor " rfc " asks for"                  \
		     : "");
  foreach_bpa_timeout_floor
#undef _
  /* The three with no floor in any RFC, printed so the command shows the
   * whole picture rather than only what happens to be governed. */
  vlib_cli_output (vm, "  %-16s %6u", "tcp-syn", t->tcp_syn);
  vlib_cli_output (vm, "  %-16s %6u", "gre", t->gre);
  vlib_cli_output (vm, "  %-16s %6u", "port-holddown", t->port_holddown);
  vlib_cli_output (vm, "  %-16s %6u", "alg-expect", t->alg_expect);
}

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_show_timeouts_command_fn (vlib_main_t *vm, unformat_input_t *input,
			      vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;

  bpa_timeouts_show (vm, &bm->timeouts, "global");
  pool_foreach (p, bm->pools)
    {
      bpa_timeouts_t eff = bpa_timeouts_resolve (&p->timeouts, &bm->timeouts);
      u8 *what = format (0, "pool %v (overrides resolved)%c", p->name, 0);

      bpa_timeouts_show (vm, &eff, (char *) what);
      vec_free (what);
    }
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_timeouts_command, static) = {
  .path = "show bpa timeouts",
  .short_help = "show bpa timeouts",
  .function = bpa_show_timeouts_command_fn,
};

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_timeouts_command_fn (vlib_main_t *vm, unformat_input_t *input,
			 vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  unformat_input_t _line_input, *line_input = &_line_input;
  clib_error_t *error = 0;
  u32 v;

  if (!unformat_user (input, unformat_line_input, line_input))
    {
      vlib_cli_output (vm,
		       "udp %u  icmp %u  tcp-syn %u  tcp-transitory %u  "
		       "tcp-established %u  gre %u  port-holddown %u  "
		       "alg-expect %u",
		       bm->timeouts.udp, bm->timeouts.icmp,
		       bm->timeouts.tcp_syn, bm->timeouts.tcp_trans,
		       bm->timeouts.tcp_estab, bm->timeouts.gre,
		       bm->timeouts.port_holddown,
		       bm->timeouts.alg_expect);
      return 0;
    }

  while (unformat_check_input (line_input) != UNFORMAT_END_OF_INPUT)
    {
      if (unformat (line_input, "udp %u", &v))
	bm->timeouts.udp = v;
      else if (unformat (line_input, "icmp %u", &v))
	bm->timeouts.icmp = v;
      else if (unformat (line_input, "tcp-transitory %u", &v))
	bm->timeouts.tcp_trans = v;
      else if (unformat (line_input, "tcp-established %u", &v))
	bm->timeouts.tcp_estab = v;
      else if (unformat (line_input, "tcp-syn %u", &v))
	bm->timeouts.tcp_syn = v;
      else if (unformat (line_input, "gre %u", &v))
	bm->timeouts.gre = v;
      else if (unformat (line_input, "port-holddown %u", &v))
	bm->timeouts.port_holddown = v;
      else if (unformat (line_input, "alg-expect %u", &v))
	bm->timeouts.alg_expect = v;
      else
	{
	  error = clib_error_return (0, "unknown input '%U'",
				     format_unformat_error, line_input);
	  break;
	}
    }

  /* Applied first, warned about second: the operator asked for it and may
   * have a reason. Once per change, which is what this function is. */
  if (!error)
    bpa_timeouts_warn (vm, &bm->timeouts, 0);

  unformat_free (line_input);
  return error;
}

VLIB_CLI_COMMAND (bpa_timeouts_command, static) = {
  .path = "bpa timeouts",
  .short_help = "bpa timeouts [udp <s>] [icmp <s>] [tcp-syn <s>] "
		"[tcp-transitory <s>] [tcp-established <s>] [gre <s>] "
		"[port-holddown <s>] [alg-expect <s>]",
  .function = bpa_timeouts_command_fn,
};

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_show_blocks_command_fn (vlib_main_t *vm, unformat_input_t *input,
			    vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  bpa_pool_t *p;
  u32 i;
  int any = 0;

  pool_foreach (p, bm->pools)
    {
      u32 pi = p - bm->pools;
      u64 total = 0, alloc = 0, allocs = 0, rels = 0, exh = 0, exts = 0;
      u64 pinned = 0, placed = 0, missed = 0;
      u64 paired = 0, unpaired = 0;

      /*
       * Both kinds of allocator, because both hand out blocks that have to be
       * given back and both can run out. A deterministic pool with a reserve
       * is the one place that mode counts a refusal at all, and a refusal
       * nobody can see is the support call arriving with nothing behind it.
       */
      if (p->mode != BPA_MODE_DYNAMIC && p->block_reserve == 0)
	continue;
      any = 1;

      if (p->mode == BPA_MODE_DYNAMIC)
	vlib_cli_output (vm, "pool %v (hold-down %us)", p->name,
			 bm->block_holddown);
      else
	/*
	 * Said as a reserve, not as a block count, because the number an
	 * operator acts on is different: these blocks came OUT of capacity,
	 * and the answer to running out of them is to enlarge the reserve
	 * (which re-maps subscribers) or to add public addresses, never to
	 * wait for a hold-down.
	 */
	vlib_cli_output (
	  vm, "pool %v (deterministic reserve, %u block(s) per public "
	      "address, %u in all, hold-down %us)",
	  p->name, p->block_reserve, p->reserve_count, bm->block_holddown);
      for (i = 0; i < vec_len (bm->per_thread); i++)
	{
	  bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, i);
	  bpa_dyn_t *d;

	  if (pi >= vec_len (pt->dyn))
	    continue;
	  d = vec_elt_at_index (pt->dyn, pi);
	  if (!d->active)
	    continue;

	  vlib_cli_output (vm,
			   "  thread %u: %u/%u blocks in use, %Lu allocations "
			   "(%Lu extensions), %Lu releases, %Lu refusals, "
			   "%u pinned",
			   i, d->n_alloc, d->n_local, d->n_allocations,
			   d->n_extensions, d->n_releases, d->n_exhausted,
			   d->n_pinned);
	  /*
	   * The blocks this worker has given to another (#210), and only when
	   * there are any. A slice reading "2/5 blocks in use" with two of
	   * its five lent has three numbers an operator can subtract and get
	   * the wrong one; and a column of zeroes on every box that never
	   * exhausts a slice is a column people learn to skip, which is how
	   * the one that matters gets skipped too - the same argument the
	   * guaranteed floor makes about itself.
	   */
	  if (d->n_lent)
	    vlib_cli_output (vm,
			     "             %u lent to another thread, so %u "
			     "of this slice is neither in use nor free here",
			     d->n_lent, d->n_lent);
	  total += d->n_local;
	  alloc += d->n_alloc;
	  allocs += d->n_allocations;
	  exts += d->n_extensions;
	  rels += d->n_releases;
	  exh += d->n_exhausted;
	  pinned += d->n_pinned;
	  placed += d->n_stride_placed;
	  missed += d->n_stride_missed;
	  paired += d->n_paired_placed;
	  unpaired += d->n_paired_missed;
	}
      /*
       * Pinned is counted apart from "in use" and is a subset of it. An
       * operator reading a pool that looks full needs to know how much of it
       * no hold-down will ever give back: a pinned block is a forward the
       * operator sold, not a subscriber that is slow to go quiet.
       */
      vlib_cli_output (vm,
		       "  total: %Lu/%Lu in use, %Lu allocations "
		       "(%Lu extensions), %Lu releases, %Lu refusals, "
		       "%Lu pinned",
		       alloc, total, allocs, exts, rels, exh, pinned);
      /*
       * Only where a stride is configured, and both numbers together.
       *
       * A stride that is set and never honoured looks from outside exactly
       * like no stride at all - the subscriber still gets a block and still
       * translates - so an operator reading their own configuration would
       * believe a layout the box is not producing. A miss is not a fault: the
       * block the stride named was held by somebody else, or the subscriber
       * sits at the end of its public address. It is a fault when it is all
       * of them, and that is only visible next to the number that was
       * placed.
       *
       * AND NO MODE TEST, which this row carried until #375. It made the two
       * numbers a dynamic-pool screen, so the one pool where the stride was
       * never applied at all was also the one pool with no reading to
       * discover that from. A stride on a deterministic pool is refused now,
       * so the test could only ever be true - and were it false again,
       * "0 placed, N fell back" is the direction that tells somebody, where
       * silence is the direction that hid it since 0.67.0.
       */
      if (p->block_stride)
	vlib_cli_output (vm,
			 "  stride %u: %Lu extensions placed where it says, "
			 "%Lu fell back to the free list",
			 p->block_stride, placed, missed);
      /*
       * Paired address pooling, as a count rather than as a claim.
       *
       * RFC 6888 REQ-2 makes Paired the required DEFAULT for a CGN, and this
       * box states it in its documentation - so an operator sizing or selling
       * against that statement needs the number their own pool produced, not
       * the sentence. The allocator PREFERS the subscriber's own public
       * address for an extension and falls back rather than refusing, so the
       * property is whatever these two say it is.
       *
       * Both allocators, because both prefer and both fall back: a
       * deterministic pool's extension comes from its reserve, and the reserve
       * is sharded by index, so the blocks on the subscriber's own address are
       * not all in its worker's slice. One number for one property.
       *
       * Only where an extension is possible at all. A pool at max-blocks 1
       * never extends, and a deterministic pool with no reserve has nothing to
       * extend from; either would print two zeros that read like a failure.
       */
      if (p->max_blocks > 1
	  && (p->mode == BPA_MODE_DYNAMIC || p->block_reserve))
	vlib_cli_output (
	  vm, "  RFC 4787 REQ-2 paired: %Lu of %Lu extension(s) on the "
	      "holder's own address",
	  paired, paired + unpaired);
      /*
       * The refusals above, named for what they mean on this pool and kept
       * apart from the OTHER way a subscriber is refused an extension:
       * reaching max-blocks. They need different operator actions - enlarge
       * the reserve against one, raise the cap against the other - and one
       * counter for both would say neither (issue #39, criterion 5). The cap
       * refusals are in "show bpa errors" as block-exhausted, on the node.
       */
      if (p->mode != BPA_MODE_DYNAMIC)
	vlib_cli_output (
	  vm, "  reserve exhausted %Lu time(s): a subscriber filled its block, "
	      "was under max-blocks %u, and the reserve had nothing left",
	  exh, p->max_blocks);
    }

  if (!any)
    /* Both kinds, because both appear above. A box with a deterministic pool
     * and a reserve was told "no dynamic pools configured" and its reserve
     * was right there. */
    vlib_cli_output (vm, "no pool on this box allocates blocks: no dynamic "
			 "pool, and no deterministic pool with a reserve");
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_blocks_command, static) = {
  .path = "show bpa blocks",
  .short_help = "show bpa blocks",
  .function = bpa_show_blocks_command_fn,
};

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_holddown_command_fn (vlib_main_t *vm, unformat_input_t *input,
			 vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  u32 v;

  if (unformat (input, "%u", &v))
    {
      bm->block_holddown = v;
      return 0;
    }

  vlib_cli_output (vm, "block-holddown %u", bm->block_holddown);
  return 0;
}

VLIB_CLI_COMMAND (bpa_holddown_command, static) = {
  .path = "bpa block-holddown",
  .short_help = "bpa block-holddown [<seconds>]",
  .function = bpa_holddown_command_fn,
};

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_logging_command_fn (vlib_main_t *vm, unformat_input_t *input,
			vlib_cli_command_t *cmd)
{
  int enable = -1;

  if (unformat (input, "enable"))
    enable = 1;
  else if (unformat (input, "disable"))
    enable = 0;
  else
    return clib_error_return (0, "say 'enable' or 'disable'");

  return bpa_log_enable (enable);
}

/*
 * "bpa rebalance": move blocks between workers' slices, or stop (#210).
 *
 * OFF ON A BOX NOBODY HAS TOLD, and the reason is measured rather than
 * cautious: the mechanism serves a subscriber that was refused with blocks
 * free next door, and it does that by lending a block the lender's own next
 * subscriber may want. Both were measured in lab/dynamic.sh, and which side
 * an operator wants is a decision about their traffic.
 */
/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_rebalance_command_fn (vlib_main_t *vm, unformat_input_t *input,
			  vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;

  if (unformat (input, "enable"))
    bm->rebalance_on = 1;
  else if (unformat (input, "disable"))
    bm->rebalance_on = 0;
  else
    return clib_error_return (0, "say 'enable' or 'disable'");

  vlib_cli_output (vm, "rebalance %s",
		   bm->rebalance_on ? "enabled" : "disabled");
  return 0;
}

VLIB_CLI_COMMAND (bpa_rebalance_command, static) = {
  .path = "bpa rebalance",
  .short_help = "bpa rebalance <enable|disable> (move blocks between workers)",
  .function = bpa_rebalance_command_fn,
};

VLIB_CLI_COMMAND (bpa_logging_command, static) = {
  .path = "bpa logging",
  .short_help = "bpa logging <enable|disable>",
  .function = bpa_logging_command_fn,
};

/*
 * "bpa stop-drain": release every block, because the box is about to stop.
 *
 * Run from an ExecStop, not by hand. It is deliberately absent from the VTY:
 * an operator configures through prismash, and this is not a configuration
 * statement, it is the first half of a shutdown.
 *
 * IT IS STILL SAFE TO TYPE, and that matters more than the naming. A box that
 * is drained and then does NOT stop is holding blocks the log has just said
 * were given back, which is the same lie as the one this whole change exists
 * to remove, only inverted. So bpa-expire-process re-announces every held
 * block a few seconds later if the box is still here. The cost is a bounded
 * window in which the log says a block is free while it is held; the
 * alternative is an unbounded one in which it says so for ever.
 */
/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_stop_drain_command_fn (vlib_main_t *vm, unformat_input_t *input,
			   vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  u32 n;

  if (!bpa_log_main.enabled && !bpa_log_tcp.enabled
      && !bpa_log_syslog.enabled)
    {
      vlib_cli_output (vm, "logging is not enabled; nothing to drain");
      return 0;
    }

  n = bpa_drain_blocks (vm);

  /*
   * Suspending, not returning: the UDP records are on a frame that ip4-lookup
   * has not run yet, and this command exists precisely because the graph is
   * the thing that puts them on the wire. Returning here would hand systemd
   * back control, systemd would send SIGTERM, and the frame would die with
   * the same records on it that the exit hook loses. A CLI process that
   * suspends lets the main loop go round.
   */
  vlib_process_suspend (vm, 0.5);

  vlib_cli_output (vm, "released %u block(s) still held", n);
  return 0;
}

VLIB_CLI_COMMAND (bpa_stop_drain_command, static) = {
  .path = "bpa stop-drain",
  .short_help = "bpa stop-drain (release every held block; the box is stopping)",
  .function = bpa_stop_drain_command_fn,
  /*
   * Runs without the CLI dispatcher's barrier, for the reason "bpa pool" gives
   * above and one of its own. bpa_drain_blocks() takes its own barrier and
   * RELEASES it before this command suspends - and a release at recursion
   * depth is a no-op, so under the dispatcher's barrier the suspend below
   * would hold every worker still for its whole length. The suspend is there
   * to let the graph run; holding the workers through it is the opposite of
   * what it is for.
   */
  .is_mp_safe = 1,
};

/*
 * not a unit: one vlib_cli_output of a stat(). The spill line above gives
 * TOTALS since start, and on the #475 bench "171600136 bytes on disk" was
 * read as a file that size when it held 1.75 MB (#547): what is in the file
 * NOW, and what was skipped to find a message, said apart.
 */
static void
bpa_show_spill_now (vlib_main_t *vm, bpa_log_stream_t *s)
{
  struct stat st;
  u64 now = 0;

  if (s->spill_path && stat ((char *) s->spill_path, &st) == 0)
    now = (u64) st.st_size;
  vlib_cli_output (vm,
		   "  file        %Lu bytes now of %Lu MB at most, %Lu dropped "
		   "over it, %Lu resynced, %Lu stalls",
		   now, (u64) (BPA_TCP_SPILL_MAX >> 20), s->n_capped,
		   s->n_resynced, s->n_stalls);
}

/*
 * not a unit: it asks the FIB which interface reaches the collector and sums
 * that interface's transmit node error counters on every thread - none of
 * which exists outside a running VPP; lab/ipfix.sh prints it.
 *
 * What a record lost AFTER the exporter costs (#549): on the #475 bench 71,107
 * packets of the exhaustion flood were dropped at ens20's transmit ("dpdk tx
 * failure") while this screen said "0 dropped". The exporter cannot see a
 * packet the NIC did not send; the interface's transmit node can, so it is
 * shown here - for every sender on that interface, which is said.
 */
static void
bpa_show_egress_tx_lost (vlib_main_t *vm, ip4_address_t *dst)
{
  vnet_main_t *vnm = vnet_get_main ();
  fib_prefix_t pfx = { .fp_proto = FIB_PROTOCOL_IP4, .fp_len = 32 };
  fib_node_index_t fei;
  vnet_hw_interface_t *hw;
  vlib_node_t *n;
  u32 sw, t, i;
  u64 lost = 0;

  pfx.fp_addr.ip4 = *dst;
  fei = fib_table_lookup (0, &pfx);
  sw = fib_entry_get_resolving_interface (fei);
  if (sw == ~0)
    {
      vlib_cli_output (vm, "  transmit    no interface reaches the collector");
      return;
    }
  hw = vnet_get_sup_hw_interface (vnm, sw);
  n = vlib_get_node (vm, hw->tx_node_index);
  for (t = 0; t < vlib_get_n_threads (); t++)
    {
      vlib_error_main_t *em = &vlib_get_main_by_index (t)->error_main;

      for (i = 0; i < n->n_errors; i++)
	{
	  u32 at = n->error_heap_index + i;

	  if (at < vec_len (em->counters))
	    lost += em->counters[at]
		    - (at < vec_len (em->counters_last_clear)
			 ? em->counters_last_clear[at] : 0);
	}
    }
  vlib_cli_output (vm,
		   "  transmit    %v: %Lu packet(s) the interface failed to "
		   "send, from every sender on it",
		   hw->name, lost);
}

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_show_logging_command_fn (vlib_main_t *vm, unformat_input_t *input,
			     vlib_cli_command_t *cmd)
{
  bpa_log_main_t *lm = &bpa_log_main;
  ipfix_exporter_t *exp;

  vlib_cli_output (vm, "IPFIX export of block events (RFC 8158)");
  vlib_cli_output (vm, "  state       %s",
		   lm->enabled ? "enabled" : "disabled");

  /*
   * WHERE the UDP records go, which this never said. "state enabled" is true
   * of a box whose exporter was cleared afterwards - measured: clearing the
   * collector leaves lm->enabled set and stops every record - so the state
   * line on its own cannot tell an exporting box from a silent one, and the
   * boot self-test reads these two lines together for exactly that reason.
   */
  exp = pool_elt_at_index (flow_report_main.exporters, 0);
  if (ip_address_is_zero (&exp->ipfix_collector))
    vlib_cli_output (vm, "  exporter    none: nothing is sent over UDP");
  else
    vlib_cli_output (vm,
		     "  exporter    %U:%u src %U (path mtu %u, template "
		     "every %us)",
		     format_ip_address, &exp->ipfix_collector,
		     exp->collector_port, format_ip_address, &exp->src_address,
		     exp->path_mtu, exp->template_interval);
  vlib_cli_output (vm, "  template    %u (%u bytes per record)",
		   lm->template_id, BPA_LOG_RECORD_SIZE);
  vlib_cli_output (vm, "  records     %Lu emitted, %Lu dropped",
		   lm->n_records, lm->n_dropped);
  vlib_cli_output (vm, "  natEvent    16 allocation, 17 de-allocation, 12 exhaustion");
  /* What the once-per-interval rule held back (#549): the operator reads here
   * that 772 subscribers ran out, not 1.6 million packets. */
  {
    bpa_main_t *bm = &bpa_main;
    u64 held = 0;
    u32 ti;

    for (ti = 0; ti < vec_len (bm->per_thread); ti++)
      {
	bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, ti);

	if (pt->exhaust)
	  held += pt->exhaust->suppressed;
      }
    vlib_cli_output (vm,
		     "  exhaustion  one record per subscriber per %.0fs: %Lu "
		     "repeats not reported",
		     BPA_EXHAUST_IVL, held);
    held = 0;
    for (ti = 0; ti < vec_len (bm->per_thread); ti++)
      {
	bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, ti);
	if (pt->log_q)
	  held += pt->log_q->dropped;
      }
    /* A worker never waits for the log (#547): a full queue drops, here. */
    vlib_cli_output (vm,
		     "  queue       %Lu event(s) dropped with a worker's queue "
		     "full (%u each)",
		     held, BPA_LOG_QUEUE_MAX);
    /* Where the rings are (#550): a box that could not have the shared
     * segment says so, because then nothing outside VPP can drain them. */
    if (bpa_log_seg.shared)
      vlib_cli_output (vm, "  rings       /dev/shm%s, %Lu bytes, outside the "
			   "main heap",
		       BPA_LOG_SEG_NAME, bpa_log_seg.bytes);
    else
      vlib_cli_output (vm, "  rings       in the main heap: %s failed (%s)",
		       bpa_log_seg.failed ? bpa_log_seg.failed : "?",
		       strerror (bpa_log_seg.err));
  }
  bpa_show_egress_tx_lost (vm, &exp->ipfix_collector.ip.ip4);
  /*
   * The marker's own line. Without it an operator cannot tell an exporter that
   * is announcing this incarnation from one that never registered the second
   * template - and the collector's whole restart rule hangs off it.
   */
  {
    time_t init = (time_t) bpa_main.boot_wall;
    struct tm tm;

    gmtime_r (&init, &tm);
    vlib_cli_output (vm,
		     "  boot marker template %u, %Lu sent, init "
		     "%04d-%02d-%02dT%02d:%02d:%02dZ "
		     "(IE 160 systemInitTimeMilliseconds)",
		     lm->marker_template_id, lm->n_markers, tm.tm_year + 1900,
		     tm.tm_mon + 1, tm.tm_mday, tm.tm_hour, tm.tm_min,
		     tm.tm_sec);
  }
  vlib_cli_output (vm, "  flushes     %Lu with data, %Lu empty, %Lu without buffer",
		   lm->n_flush_sent, lm->n_flush_empty, lm->n_getbuf_fail);

  if (bpa_log_tcp.enabled)
    {
      vlib_cli_output (vm, "TCP transport (survives collector outage)");
      vlib_cli_output (vm, "  collector   %U:%u  %s", format_ip4_address,
		       &bpa_log_tcp.collector, bpa_log_tcp.port,
		       bpa_log_tcp.connected	     ? "connected"
		       : bpa_log_tcp.pending_connect ? "connecting"
						     : "disconnected");
      vlib_cli_output (vm,
		       "  records     %Lu emitted, %Lu bytes sent, "
		       "%Lu pending",
		       bpa_log_tcp.n_records, bpa_log_tcp.n_sent,
		       (u64) vec_len (bpa_log_tcp.outbuf));
      vlib_cli_output (vm,
		       "  spill       %Lu bytes on disk, %Lu replayed, "
		       "%Lu dropped",
		       bpa_log_tcp.n_spilled, bpa_log_tcp.n_replayed,
		       bpa_log_tcp.n_dropped);
      bpa_show_spill_now (vm, &bpa_log_tcp);
      vlib_cli_output (vm,
		       "  connections %Lu, service %Lu, send %Lu, errno %d",
		       bpa_log_tcp.n_connects, bpa_log_tcp.n_service,
		       bpa_log_tcp.n_send_calls, bpa_log_tcp.last_errno);
    }
  else
    vlib_cli_output (vm, "TCP transport: disabled");

  /* And the syslog stream, which an operator reading this screen is asking
   * about as much as the other two: a transport nobody can see the state of
   * is one they diagnose by guessing (#20). */
  if (bpa_log_syslog.enabled)
    {
      vlib_cli_output (vm, "syslog transport (RFC 5424 over TCP)");
      vlib_cli_output (vm, "  collector   %U:%u  %s", format_ip4_address,
		       &bpa_log_syslog.collector, bpa_log_syslog.port,
		       bpa_log_syslog.connected	       ? "connected"
		       : bpa_log_syslog.pending_connect ? "connecting"
							: "disconnected");
      vlib_cli_output (vm, "  enterprise  %u, facility %u, severity %u",
		       bpa_log_syslog.pen, bpa_log_syslog.pri / 8,
		       bpa_log_syslog.pri % 8);
      vlib_cli_output (vm,
		       "  lines       %Lu written, %Lu bytes sent, "
		       "%Lu pending",
		       bpa_log_syslog.n_records, bpa_log_syslog.n_sent,
		       (u64) vec_len (bpa_log_syslog.outbuf));
      vlib_cli_output (vm,
		       "  spill       %Lu bytes on disk, %Lu replayed, "
		       "%Lu dropped",
		       bpa_log_syslog.n_spilled, bpa_log_syslog.n_replayed,
		       bpa_log_syslog.n_dropped);
      bpa_show_spill_now (vm, &bpa_log_syslog);
    }
  else
    vlib_cli_output (vm, "syslog transport: disabled");
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_logging_command, static) = {
  .path = "show bpa logging",
  .short_help = "show bpa logging",
  .function = bpa_show_logging_command_fn,
};

/*
 * Decomposes a range [base, base+count) into the CIDR prefixes covering it.
 *
 * Pool ranges are born from prefixes but merged when adjacent, so a range may
 * no longer be a single prefix. For the configuration to be readable back, it
 * has to come out as prefixes again.
 */
/* not a unit: a VPP format function over a va_list - see the header */
static u8 *
format_bpa_range_as_prefixes (u8 *s, va_list *args)
{
  u32 base = va_arg (*args, u32);
  u32 count = va_arg (*args, u32);
  u32 indent = va_arg (*args, u32);
  const char *kw = va_arg (*args, const char *);

  while (count > 0)
    {
      u32 size = 1;
      u32 len;
      ip4_address_t a;

      /* largest power of two aligned on base that still fits in count */
      while (size < count && (base & size) == 0 && size < 0x80000000u)
	size <<= 1;
      while (size > count)
	size >>= 1;

      for (len = 32; len > 0 && (1u << (32 - len)) < size; len--)
	;

      a.as_u32 = clib_host_to_net_u32 (base);
      s = format (s, "%U%s %U/%u\n", format_white_space, indent, kw,
		  format_ip4_address, &a, len);

      base += size;
      count -= size;
    }
  return s;
}

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_show_config_command_fn (vlib_main_t *vm, unformat_input_t *input,
			    vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  vnet_main_t *vnm = vnet_get_main ();
  vnet_sw_interface_t *si;
  ipfix_exporter_t *exp;
  bpa_pool_t *p;
  u8 *s = 0;
  u32 i;

  /*
   * Emits the configuration in VTY syntax, so prismash can show
   * "show running-config" and save it. The dataplane is the source of truth:
   * the VTY runs as a short-lived process and cannot hold state between calls.
   */
  pool_foreach (p, bm->pools)
    {
      s = format (s, "cgnat pool %v\n", p->name);
      for (i = 0; i < p->inside.n_ranges; i++)
	s = format (s, "%U", format_bpa_range_as_prefixes,
		    p->inside.ranges[i].base, p->inside.ranges[i].count, 1,
		    "inside");
      for (i = 0; i < p->outside.n_ranges; i++)
	s = format (s, "%U", format_bpa_range_as_prefixes,
		    p->outside.ranges[i].base, p->outside.ranges[i].count, 1,
		    "outside");
      s = format (s, " block-size %u\n", p->block_size);
      if (p->port_min != 1024 || p->port_max != 65535)
	s = format (s, " ports %u %u\n", p->port_min, p->port_max);
      s = format (s, " policy %U\n", format_bpa_policy, p->policy);
      s = format (s, " mode %U\n", format_bpa_mode, p->mode);
      /* Only the overrides. Emitting inherited values would turn every
       * global change into a diff against every pool. */
      if (p->timeouts.udp)
	s = format (s, " timeout udp %u\n", p->timeouts.udp);
      if (p->timeouts.icmp)
	s = format (s, " timeout icmp %u\n", p->timeouts.icmp);
      if (p->timeouts.tcp_syn)
	s = format (s, " timeout tcp-syn %u\n", p->timeouts.tcp_syn);
      if (p->timeouts.gre)
	s = format (s, " timeout gre %u\n", p->timeouts.gre);
      if (p->timeouts.tcp_trans)
	s = format (s, " timeout tcp-transitory %u\n", p->timeouts.tcp_trans);
      if (p->timeouts.tcp_estab)
	s = format (s, " timeout tcp-established %u\n", p->timeouts.tcp_estab);
      if (p->timeouts.port_holddown)
	s = format (s, " timeout port-holddown %u\n", p->timeouts.port_holddown);
      if (p->timeouts.alg_expect)
	s = format (s, " timeout alg-expect %u\n", p->timeouts.alg_expect);

      if (p->max_blocks > 1)
	s = format (s, " max-blocks %u\n", p->max_blocks);
      if (p->initial_blocks > 1)
	s = format (s, " initial-blocks %u\n", p->initial_blocks);
      if (p->block_stride)
	s = format (s, " block-stride %u\n", p->block_stride);
      if (p->block_reserve)
	s = format (s, " reserve %u\n", p->block_reserve);
      for (i = 0; i < p->bypass_src.n_ranges; i++)
	s = format (s, "%U", format_bpa_range_as_prefixes,
		    p->bypass_src.ranges[i].base,
		    p->bypass_src.ranges[i].count, 1, "bypass source");
      for (i = 0; i < p->bypass_dst.n_ranges; i++)
	s = format (s, "%U", format_bpa_range_as_prefixes,
		    p->bypass_dst.ranges[i].base,
		    p->bypass_dst.ranges[i].count, 1, "bypass destination");
      if (p->vrf_id != ~0u)
	s = format (s, " vrf %u\n", p->vrf_id);
      if (p->out_vrf_id != ~0u)
	s = format (s, " outside-vrf %u\n", p->out_vrf_id);
      if (p->util_alarm)
	s = format (s, " utilisation-alarm %u\n", p->util_alarm);
      if (p->max_sessions)
	s = format (s, " max-sessions %u\n", p->max_sessions);
      if (p->max_rate)
	s = format (s, " max-session-rate %u\n", p->max_rate);
      if (p->tcp_mss_auto)
	s = format (s, " tcp-mss auto\n");
      else if (p->tcp_mss)
	s = format (s, " tcp-mss %u\n", p->tcp_mss);
      if (p->filter != BPA_FILTER_ENDPOINT_INDEPENDENT)
	s = format (s, " filter address-dependent\n");
      /* Only the departures from the default: emitting a line per ALG per
       * pool would fill every running configuration with lines that say
       * nothing. */
#define _(sym, nm, desc)                                                      \
  if ((p->alg & BPA_ALG_##sym) != (BPA_ALG_DEFAULT & BPA_ALG_##sym))          \
    s = format (s, " alg %s %s\n", nm, (p->alg & BPA_ALG_##sym) ? "on" : "off");
      foreach_bpa_alg
#undef _
      /* An inbound mapping the configuration does not show is a mapping
       * nobody can account for later. Emitted in the vector's own order,
       * which is by public port, so the running configuration is stable. */
      vec_foreach_index (i, p->statics)
	{
	  ip4_address_t priv;

	  priv.as_u32 = clib_host_to_net_u32 (p->statics[i].ip_priv);
	  s = format (s, " forward %U %u to %U %u\n", format_bpa_proto,
		      p->statics[i].proto, p->statics[i].port_pub,
		      format_ip4_address, &priv, p->statics[i].port_priv);
	}
      s = format (s, " exit\n");
    }

  pool_foreach (si, vnm->interface_main.sw_interfaces)
    {
      if (vnet_feature_is_enabled ("ip4-unicast", "bpa-in2out",
				   si->sw_if_index) == 1
	  || vnet_feature_is_enabled ("ip4-unicast", "bpa-in2out-handoff",
				      si->sw_if_index) == 1)
	s = format (s, "cgnat interface %U inside\n",
		    format_vnet_sw_if_index_name, vnm, si->sw_if_index);
      if (vnet_feature_is_enabled ("ip4-unicast", "bpa-out2in",
				   si->sw_if_index) == 1
	  || vnet_feature_is_enabled ("ip4-unicast", "bpa-out2in-handoff",
				      si->sw_if_index) == 1)
	s = format (s, "cgnat interface %U outside\n",
		    format_vnet_sw_if_index_name, vnm, si->sw_if_index);
    }

  if (bm->timeouts.udp != BPA_TIMEOUT_UDP)
    s = format (s, "cgnat timeout udp %u\n", bm->timeouts.udp);
  if (bm->timeouts.icmp != BPA_TIMEOUT_ICMP)
    s = format (s, "cgnat timeout icmp %u\n", bm->timeouts.icmp);
  if (bm->timeouts.tcp_trans != BPA_TIMEOUT_TCP_TRANS)
    s = format (s, "cgnat timeout tcp-transitory %u\n", bm->timeouts.tcp_trans);
  if (bm->timeouts.tcp_syn != BPA_TIMEOUT_TCP_SYN)
    s = format (s, "cgnat timeout tcp-syn %u\n", bm->timeouts.tcp_syn);
  if (bm->timeouts.port_holddown != BPA_PORT_HOLDDOWN)
    s = format (s, "cgnat timeout port-holddown %u\n",
		bm->timeouts.port_holddown);
  if (bm->timeouts.alg_expect != BPA_TIMEOUT_ALG_EXPECT)
    s = format (s, "cgnat timeout alg-expect %u\n", bm->timeouts.alg_expect);
  if (bm->timeouts.tcp_estab != BPA_TIMEOUT_TCP_ESTAB)
    s = format (s, "cgnat timeout tcp-established %u\n", bm->timeouts.tcp_estab);
  if (bm->block_holddown != BPA_BLOCK_HOLDDOWN)
    s = format (s, "cgnat block-holddown %u\n", bm->block_holddown);
  /*
   * VPP's own UDP exporter. It is core state, not this plugin's - prismash
   * sets it with VPP's set_ipfix_exporter message, because libvnet exports
   * no setter a plugin could call and copying the handler's field
   * assignments here would be a copy that drifts with every VPP bump.
   *
   * It is READ here because this is the one place the CGNAT running
   * configuration is produced, and a statement that cannot be read back
   * cannot be saved, rolled back or audited - which is what #116 cost the
   * TCP collector.
   *
   * Emitted BEFORE the enable line and not after it: the dataplane refuses
   * 'cgnat logging enable' while there is no collector, so a configuration
   * that reloaded in the other order would refuse its own second line.
   */
  exp = pool_elt_at_index (flow_report_main.exporters, 0);
  if (!ip_address_is_zero (&exp->ipfix_collector))
    s = format (s, "cgnat logging udp %U src %U port %u\n", format_ip_address,
		&exp->ipfix_collector, format_ip_address, &exp->src_address,
		exp->collector_port);
  if (bpa_log_main.enabled)
    s = format (s, "cgnat logging enable\n");
  /*
   * The TCP collector, which used to be applied and never reported back. The
   * running configuration is read from the box, so an omission here is not a
   * cosmetic one: saving the configuration and reloading it produced a box
   * that looked identical and exported nothing, and a rollback could not
   * restore a destination that was never in the revision. Nothing said so.
   * https://github.com/andrediashexa/hexarouting/issues/116
   */
  if (bpa_log_tcp.enabled)
    s = format (s, "cgnat logging tcp %U port %u\n", format_ip4_address,
		&bpa_log_tcp.collector, bpa_log_tcp.port);
  if (bpa_main.identity.path)
    s = format (s, "cgnat identity %s %v\n",
		bpa_main.identity.kind == BPA_IDENTITY_DHCP_LEASES
		  ? "dhcp-leases"
		  : "file",
		bpa_main.identity.path);
  /*
   * The listener, WITHOUT its secret. The running configuration this
   * dataplane gives back is read by the VTY and written to the box's file,
   * and a secret that travelled that way would be printed by `show
   * configuration` on every screen. The VTY keeps it, the way it keeps an
   * SNMP community, and writes it to the file with the rest (#21).
   */
  if (bpa_radius.enabled)
    s = format (s, "cgnat identity radius port %u\n", bpa_radius.port);
  if (bpa_log_syslog.enabled)
    s = format (s,
		"cgnat logging syslog %U port %u enterprise %u facility %u "
		"severity %u\n",
		format_ip4_address, &bpa_log_syslog.collector,
		bpa_log_syslog.port, bpa_log_syslog.pen,
		bpa_log_syslog.pri / 8, bpa_log_syslog.pri % 8);

  if (s)
    {
      vlib_cli_output (vm, "%v", s);
      vec_free (s);
    }
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_config_command, static) = {
  .path = "show bpa config",
  .short_help = "show bpa config",
  .function = bpa_show_config_command_fn,
};

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_logging_tcp_command_fn (vlib_main_t *vm, unformat_input_t *input,
			    vlib_cli_command_t *cmd)
{
  ip4_address_t addr = { 0 };
  u32 port = 4739;

  if (unformat (input, "disable"))
    return bpa_log_tcp_enable (0, 0, 0);

  if (!unformat (input, "%U", unformat_ip4_address, &addr))
    return clib_error_return (0, "collector address required");
  unformat (input, "port %u", &port);
  if (port > 65535)
    return clib_error_return (0, "invalid port");

  return bpa_log_tcp_enable (&addr, (u16) port, 1);
}

VLIB_CLI_COMMAND (bpa_logging_tcp_command, static) = {
  .path = "bpa logging tcp",
  .short_help = "bpa logging tcp <collector> [port <n>] | bpa logging tcp disable",
  .function = bpa_logging_tcp_command_fn,
};

/*
 * The same events, as RFC 5424 syslog lines, for the collectors that speak
 * nothing else (#20).
 *
 * THE ENTERPRISE NUMBER IS REQUIRED and has no default. RFC 5424 allows a
 * structured-data name without an `@enterprise` suffix only for names IANA
 * has registered; this product has none, and inventing one would put a name
 * in every operator's log that means something else to whoever registers it
 * later. So the operator is asked for their own, and refused without it -
 * a refusal at configuration time, where it can be acted on, rather than a
 * log a collector silently misparses for a year.
 */
/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_logging_syslog_command_fn (vlib_main_t *vm, unformat_input_t *input,
			       vlib_cli_command_t *cmd)
{
  ip4_address_t addr = { 0 };
  u32 port = 601, pen = 0, facility = 16, severity = 6;

  if (unformat (input, "disable"))
    return bpa_log_stream_enable (&bpa_log_syslog, 0, 0, 0);

  if (!unformat (input, "%U", unformat_ip4_address, &addr))
    return clib_error_return (0, "collector address required");
  while (unformat_check_input (input) != UNFORMAT_END_OF_INPUT)
    {
      if (unformat (input, "port %u", &port))
	;
      else if (unformat (input, "enterprise %u", &pen))
	;
      else if (unformat (input, "facility %u", &facility))
	;
      else if (unformat (input, "severity %u", &severity))
	;
      else
	return clib_error_return (0, "unknown argument");
    }
  if (port > 65535)
    return clib_error_return (0, "invalid port");
  if (!pen)
    return clib_error_return (
      0, "enterprise <n> is required: RFC 5424 reserves an unsuffixed "
	 "structured-data name for IANA, so the lines carry your own "
	 "enterprise number");
  if (facility > 23 || severity > 7)
    return clib_error_return (0, "facility is 0-23 and severity is 0-7");

  bpa_log_syslog.wire = BPA_WIRE_SYSLOG;
  bpa_log_syslog.pen = pen;
  bpa_log_syslog.pri = facility * 8 + severity;
  return bpa_log_stream_enable (&bpa_log_syslog, &addr, (u16) port, 1);
}

/*
 * Where this box reads who held a private address (#21).
 *
 * A FILE AND NOT A LIST OF STATEMENTS, because the list is written by a
 * provisioning system and can hold one line per subscriber: a configuration
 * that carried 200000 of them would be a configuration nobody can read, and
 * every commit would rewrite it whole.
 *
 * `show bpa identity` says how many entries and when they were read. It does
 * not print one, and neither does any other screen on this box: identity is
 * personal data, and its presence in the log is the reason the log is
 * protected.
 */
/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_identity_command_fn (vlib_main_t *vm, unformat_input_t *input,
			 vlib_cli_command_t *cmd)
{
  u8 *path = 0;
  clib_error_t *e;

  if (unformat (input, "none"))
    return bpa_identity_load (0);
  if (!unformat (input, "file %s", &path))
    return clib_error_return (0, "usage: bpa identity file <path> | none");
  e = bpa_identity_load ((const char *) path);
  vec_free (path);
  return e;
}

VLIB_CLI_COMMAND (bpa_identity_command, static) = {
  .path = "bpa identity",
  .short_help = "bpa identity file <path> | bpa identity none",
  .function = bpa_identity_command_fn,
};

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_show_identity_command_fn (vlib_main_t *vm, unformat_input_t *input,
			      vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;

  if (!bm->identity.n)
    {
      vlib_cli_output (vm, "no identity table: every record says 'unknown'");
      return 0;
    }
  vlib_cli_output (vm, "identity table: %u entry(s), read %U", bm->identity.n,
		   format_time_float, 0, bm->identity.read_at);
  if (bpa_radius.enabled)
    {
      vlib_cli_output (vm, "radius accounting: listening on port %u",
		       bpa_radius.port);
      vlib_cli_output (vm,
		       "  packets    %Lu received, %Lu applied, %Lu answered",
		       bpa_radius.n_received, bpa_radius.n_applied,
		       bpa_radius.n_answered);
      /* The two refusals apart, because they are different diagnoses: one is
       * somebody's broken client and the other is this box's secret, or
       * somebody else's box on this port. */
      vlib_cli_output (vm, "  refused    %Lu bad authenticator, %Lu malformed",
		       bpa_radius.n_bad_auth, bpa_radius.n_malformed);
    }
  if (bm->identity.n_truncated)
    vlib_cli_output (vm,
		     "  %u name(s) were longer than %u characters and are "
		     "carried in part",
		     bm->identity.n_truncated, BPA_IDENTITY_MAX - 1);
  /* THE ENTRIES ARE NOT PRINTED, here or anywhere. See bpa_identity.c. */
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_identity_command, static) = {
  .path = "show bpa identity",
  .short_help = "show bpa identity",
  .function = bpa_show_identity_command_fn,
};

VLIB_CLI_COMMAND (bpa_logging_syslog_command, static) = {
  .path = "bpa logging syslog",
  .short_help = "bpa logging syslog <collector> enterprise <n> [port <n>] "
		"[facility <0-23>] [severity <0-7>] | bpa logging syslog "
		"disable",
  .function = bpa_logging_syslog_command_fn,
};

/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_sessions_command_fn (vlib_main_t *vm, unformat_input_t *input,
			 vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  u32 buckets = 0, mem_mb = 0;

  if (unformat (input, "buckets %u memory %u", &buckets, &mem_mb)
      || unformat (input, "memory %u buckets %u", &mem_mb, &buckets))
    {
      if (pool_elts (bm->pools))
	return clib_error_return (
	  0, "tables already created; set this before configuring pools");
      bm->hash_buckets = buckets;
      bm->hash_memory = mem_mb << 20;
      return 0;
    }

  vlib_cli_output (vm, "buckets %u, memory %u MB per table",
		   bm->hash_buckets, bm->hash_memory >> 20);
  vlib_cli_output (vm,
		   "  (two tables per thread; the size is a virtual "
		   "reservation, and the OOM killer counts virtual)");
  return 0;
}

VLIB_CLI_COMMAND (bpa_sessions_command, static) = {
  .path = "bpa session-table",
  .short_help = "bpa session-table [buckets <n> memory <MB>]",
  .function = bpa_sessions_command_fn,
};

/*
 * `show bpa workers` (#466): which workers translate and which only receive.
 * The split is startup.conf's (`bpa { cgnat-workers <list> }`), and this is
 * the dataplane saying what it took from it.
 */
/* not a unit: a VLIB_CLI_COMMAND body, driven by the labs - see the header */
static clib_error_t *
bpa_show_workers_command_fn (vlib_main_t *vm, unformat_input_t *input,
			     vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  u32 k, t;

  if (vlib_num_workers () == 0)
    {
      vlib_cli_output (vm, "no workers: the main thread translates");
      return 0;
    }
  for (k = 0; k < vlib_num_workers (); k++)
    {
      t = vlib_get_worker_thread_index (k);
      vlib_cli_output (vm, "worker %u (thread %u): %s", k, t,
		       t < vec_len (bm->thread_w) && bm->thread_w[t] != ~0
			 ? "translates" : "receives and hands over");
    }
  vlib_cli_output (vm, "%u of %u worker(s) translate", bm->n_workers,
		   vlib_num_workers ());
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_workers_command, static) = {
  .path = "show bpa workers",
  .short_help = "show bpa workers",
  .function = bpa_show_workers_command_fn,
};
