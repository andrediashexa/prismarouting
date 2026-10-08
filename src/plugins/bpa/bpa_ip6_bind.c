/*
 * bpa_ip6_bind.c - an interface moved to another IPv6 table keeps answering
 * neighbour solicitations (#594).
 *
 * What VPP 26.06 does, read in its source and measured in lab/mgmt-vrf.sh:
 *
 * ip6_link_enable() - the FIRST lock on an interface's IPv6 - adds the
 * interface as an Accept path to four entries of the multicast FIB the
 * interface is in: ff02::1:ff00:0/104 (solicited-node), ff02::1, ff02::2 and
 * ff02::16 (ip6_mfib_interface_enable_disable(), source "special"). Without
 * that path a packet to one of those groups fails the RPF check of the
 * interface's mfib and is dropped.
 *
 * `set interface ip6 table` (sw_interface_set_table, ip_table_bind) moves the
 * interface to the new unicast and multicast tables and does NOT move those
 * paths: mfib_table_bind() only swaps the index. They stay in the old table,
 * and the new table's solicited-node entry has no interface to accept from.
 * A move with the interface's IPv6 still enabled is therefore an interface
 * that drops every MULTICAST neighbour solicitation from then on: its own
 * addresses are not resolvable by a neighbour that does not already have
 * them. Unicast NUD probes still work, which is why it is intermittent.
 *
 * And the IPv6 IS still enabled on any paired interface: VPP refuses the move
 * while a global address is on (the VTY takes them off and puts them back,
 * set_vrf in prismash), but linux_cp mirrors the TAP's link-local as a lock
 * of its own (lcp_router_link_addr_add_del), and with keep_addr_on_down -
 * which `system management vrf` sets (#580) - the TAP keeps it through the
 * VRF's enslave. Measured on the management card in table 100, after the
 * move: `show ip6 mfib table 100` listed ff02::1:ff00:0/104 with no
 * interface, and every one of the gateway's solicitations for 2001:db8:200::1
 * was counted as "Multicast RPF check failed" for as long as the run lasted.
 * The gateway got the box back only when the box itself solicited it - the
 * Linux side's NS carries its MAC - which took 0 s in one run and over 30 s
 * in the next: that is the "settled after 20 s" of #594, and IPv4 never saw
 * it because ARP is not multicast.
 *
 * So the paths follow the interface: on every IPv6 table bind of an
 * interface whose IPv6 is enabled, they are taken out of the table it leaves
 * and put in the one it joins - what ip6_link_enable() would have done had
 * the link been enabled after the move. One place for every way a table
 * changes: the VTY's commit, --load and --reapply, the management VRF, a
 * tenant VRF (#540), vppctl.
 *
 * In this plugin because it is the only VPP plugin this product ships (the
 * argument bpa_ra.c makes), and not in VPP because this product installs
 * fd.io's packages unpatched.
 */

#include <vlib/vlib.h>
#include <vnet/vnet.h>
#include <vnet/ip/ip6.h>
#include <vnet/fib/fib_table.h>
#include <vnet/mfib/mfib_table.h>

#include "bpa.h"

/* vnet/ip/ip6_link.h's, which fd.io's vpp-dev package does not install:
 * libvnet exports the function (nm -D, VPP 26.06), and this is its
 * declaration as that header has it. */
extern bool ip6_link_is_enabled (u32 sw_if_index);

/*
 * `bpa { ip6-mcast-follow off }` in startup.conf: the move as VPP does it,
 * for lab/mgmt-vrf.sh's `unrepaired` run, which must go red without this.
 * Read at each bind rather than at init, so the order VPP calls config and
 * init functions in does not matter.
 */
u8 bpa_ip6_mcast_follow_off;

/* not a unit: it writes four entries of a live VPP multicast FIB, which no
 * plain-cc harness has; lab/mgmt-vrf.sh moves the management card into a VRF
 * and back and asserts the gateway resolves its IPv6 address each time, and
 * its `unrepaired` run is the same lab without this. */
static void
bpa_ip6_mcast_accepts (u32 mfib_index, u32 sw_if_index, int is_add)
{
  /* The groups and the path exactly as ip6_mfib.c's ip6_mfib_specials and
   * ip6_mfib_interface_enable_disable() have them in VPP 26.06: the same
   * source and the same path, so the link's own disable removes what this
   * added and nothing is left behind in either table. */
  static const struct
  {
    u8 grp[16];
    u8 len;
  } groups[] = {
    /* ff02::1:ff00:0/104, solicited-node: the one #594 lost */
    { { 0xff, 0x02, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x01, 0xff, 0, 0, 0 }, 104 },
    /* ff02::2, all routers */
    { { 0xff, 0x02, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x02 }, 128 },
    /* ff02::1, all nodes */
    { { 0xff, 0x02, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x01 }, 128 },
    /* ff02::16, MLDv2 routers */
    { { 0xff, 0x02, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x16 }, 128 },
  };
  const fib_route_path_t path = {
    .frp_proto = DPO_PROTO_IP6,
    .frp_sw_if_index = sw_if_index,
    .frp_fib_index = ~0,
    .frp_weight = 1,
    .frp_mitf_flags = MFIB_ITF_FLAG_ACCEPT,
  };
  mfib_prefix_t pfx = {
    .fp_proto = FIB_PROTOCOL_IP6,
  };
  int i;

  for (i = 0; i < ARRAY_LEN (groups); i++)
    {
      /* Written out whole: built from a four-byte tail, the first version
       * of this put the solicited-node 0x01 one byte late and moved an
       * entry for ff02::1ff:0/104, which nothing is sent to. */
      clib_memcpy (pfx.fp_grp_addr.ip6.as_u8, groups[i].grp, 16);
      pfx.fp_len = groups[i].len;
      if (is_add)
	mfib_table_entry_path_update (mfib_index, &pfx, MFIB_SOURCE_SPECIAL,
				      MFIB_ENTRY_FLAG_NONE, &path);
      else
	mfib_table_entry_path_remove (mfib_index, &pfx, MFIB_SOURCE_SPECIAL,
				      &path);
    }
}

/* not a unit: an ip6 table-bind callback VPP calls from fib_table_bind();
 * what it does is bpa_ip6_mcast_accepts', and the lab named there drives
 * both. */
static void
bpa_ip6_table_bind (ip6_main_t *im, uword opaque, u32 sw_if_index,
		    u32 new_fib_index, u32 old_fib_index)
{
  u32 old_mfib, new_mfib, table_id;

  if (bpa_ip6_mcast_follow_off || new_fib_index == old_fib_index)
    return;
  /* A link that is not enabled holds no paths, and gets them in whatever
   * table it is in when it is enabled. */
  if (!ip6_link_is_enabled (sw_if_index))
    return;
  /* Called BEFORE the multicast bind: the index the interface has now is the
   * table it leaves. The one it joins is the multicast table of the same id,
   * the one ip_table_bind() binds it to next - and when there is none it is
   * not bound to one, so nothing moves. */
  old_mfib = im->mfib_index_by_sw_if_index[sw_if_index];
  table_id = fib_table_get_table_id (new_fib_index, FIB_PROTOCOL_IP6);
  new_mfib = mfib_table_find (FIB_PROTOCOL_IP6, table_id);
  if (new_mfib == ~0 || new_mfib == old_mfib)
    return;
  bpa_ip6_mcast_accepts (old_mfib, sw_if_index, 0);
  bpa_ip6_mcast_accepts (new_mfib, sw_if_index, 1);
}

/* not a unit: the callback's registration, called once by VPP at start. */
static clib_error_t *
bpa_ip6_bind_init (vlib_main_t *vm)
{
  ip6_table_bind_callback_t cb = {
    .function = bpa_ip6_table_bind,
  };

  vec_add1 (ip6_main.table_bind_callbacks, cb);
  return 0;
}

VLIB_INIT_FUNCTION (bpa_ip6_bind_init);
