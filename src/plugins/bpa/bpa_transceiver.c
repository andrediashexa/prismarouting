/*
 * bpa_transceiver.c - a module's EEPROM, read through DPDK on a card the
 * dataplane owns (#590): `show bpa transceiver [<interface>]`.
 *
 * WHAT IT PRINTS IS BYTES. The decode - SFF-8472's calibration, the
 * thresholds, dBm - is src/prismash/transceiver.py's, one decoder that the
 * VTY, the API and the SNMP agent all read, and that test/test_transceiver.py
 * drives with byte dumps built from the published layouts. A second decoder
 * here would be a second answer to the same question.
 *
 * WHERE THE READ LIVES, and the three it is not.
 *
 *   * Not VPP's own output. `show hardware-interfaces detail` already calls
 *     rte_eth_dev_get_module_eeprom() for the whole map and prints the
 *     identity from it (format_dpdk_device_module_info, VPP 26.06) - and
 *     throws the diagnostics page away. Changing that is patching VPP, and
 *     this product installs fd.io's packages unpatched (build/Dockerfile.vpp,
 *     the ISO): a patched VPP is a VPP build of our own, for every release.
 *   * Not DPDK's telemetry. `dpdk { telemetry }` would start the telemetry
 *     thread, whose /ethdev/module_eeprom decodes the same page into prose.
 *     It is off on every box (/run/dpdk/vpp empty on 10.40.200.166), turning
 *     it on is a dataplane restart and a new socket into the dataplane, and
 *     its read runs on that thread, beside the main thread's own calls into
 *     the same port, rather than on the main thread.
 *   * Not a link against DPDK. VPP links it statically into dpdk_plugin.so
 *     and exports none of it, and the library is stripped (measured, see
 *     tools/dpdk-symbols.sh). So the five functions are called at offsets
 *     read from fd.io's vpp-dbg for that exact build, and ONLY after the
 *     build-id of the dpdk_plugin.so mapped into this process has been
 *     compared with the one the offsets were read from
 *     (bpa_dpdk_syms.h). A different build says "unavailable" and calls
 *     nothing.
 *
 * WITHOUT DISTURBING FORWARDING. The command is mp-safe: VPP runs a CLI
 * command that is not under the worker barrier, which stops every worker
 * for as long as the command runs - and an ixgbe EEPROM read is a
 * bit-banged I2C transaction, a fraction of a millisecond a byte. VPP's own
 * `show hardware-interfaces detail` is not mp-safe and reads all 512 bytes
 * under the barrier. This reads on the main thread with the workers running,
 * touches nothing the workers touch (the interface pool is the main
 * thread's, the module EEPROM is the driver's), keeps the identity and the
 * thresholds for BPA_XCVR_STATIC_SECONDS and answers a second ask within
 * BPA_XCVR_LIVE_SECONDS from what it read - so an SNMP walk, the VTY and
 * the API asking at once cost one read. On a box with no workers the main
 * thread IS the forwarding thread, and a poll's 24 bytes are its cost.
 *
 * THE STAND-IN. `bpa transceiver stand-in <interface> type <t> hex <bytes>`
 * gives an interface a module's bytes, served as `source stand-in` - the
 * labs have no SFP (VPP runs on taps and af_packet there), and the VTY, the
 * API and the SNMP agent are proved against the same command a box answers.
 * Every line it produces says `stand-in`, so it cannot pass for a reading.
 *
 * Every function here is the VPP-facing half and carries `not a unit:`; the
 * arithmetic is bpa_xcvr.h's, and test/test_bpa_xcvr.c drives it.
 */

#define _GNU_SOURCE
#include <link.h>
#include <string.h>

#include <vlib/vlib.h>
#include <vnet/vnet.h>

#include "bpa.h"
#include "bpa_xcvr.h"
#include "bpa_dpdk_syms.h"

/* How long the identity and the thresholds are kept, and how long a reading
 * answers a second ask. Seconds. */
#define BPA_XCVR_STATIC_SECONDS 60.0
#define BPA_XCVR_LIVE_SECONDS	2.0

/* DPDK's own structures, as rte_dev_info.h and rte_ether.h declare them in
 * DPDK 26.03 (the one VPP 26.06 carries). Declared here because the plugin
 * builds against vpp-dev, which ships no DPDK headers; the layouts are part
 * of DPDK's stable ABI. */
typedef struct
{
  u32 type;
  u32 eeprom_len;
} bpa_rte_module_info_t;

typedef struct
{
  void *data;
  u32 offset;
  u32 length;
  u32 magic;
} bpa_rte_eeprom_info_t;

typedef struct
{
  int (*is_valid_port) (u16 port);
  int (*macaddr_get) (u16 port, u8 *mac);
  int (*name_by_port) (u16 port, char *name);
  int (*module_info) (u16 port, bpa_rte_module_info_t *mi);
  int (*module_eeprom) (u16 port, bpa_rte_eeprom_info_t *ei);
  u8 resolved; /* 0 not tried, 1 callable, 2 refused */
  char why[160];
} bpa_xcvr_dpdk_t;

/* One interface's last read. */
typedef struct
{
  f64 static_at;
  f64 live_at;
  u32 type;
  u32 len;
  int rv;	/* the last read's DPDK return, 0 for a good one */
  u16 port;
  u8 data[BPA_XCVR_MAX_LEN];
  u8 valid[BPA_XCVR_MAX_LEN];
  char device[64];
} bpa_xcvr_cache_t;

/* A stand-in module's bytes, by sw_if_index. */
typedef struct
{
  u32 type;
  u8 *bytes;
} bpa_xcvr_standin_t;

static bpa_xcvr_dpdk_t bpa_xcvr_dpdk;
static bpa_xcvr_cache_t *bpa_xcvr_cache;     /* by hw_if_index */
static bpa_xcvr_standin_t *bpa_xcvr_standin; /* by sw_if_index */

typedef struct
{
  uword base;
  u8 id[64];
  u32 id_len;
  u8 found;
} bpa_xcvr_find_t;

/* not a unit: a dl_iterate_phdr callback over the objects mapped into the
 * running VPP; the note walk it does is bpa_xcvr_build_id(), which
 * test/test_bpa_xcvr.c drives */
static int
bpa_xcvr_find_dpdk (struct dl_phdr_info *info, size_t size, void *arg)
{
  bpa_xcvr_find_t *f = arg;
  const char *name = info->dlpi_name;
  size_t n = name ? strlen (name) : 0;
  int i;

  if (n < 15 || strcmp (name + n - 15, "/dpdk_plugin.so"))
    return 0;
  f->found = 1;
  f->base = info->dlpi_addr;
  for (i = 0; i < info->dlpi_phnum; i++)
    {
      const ElfW (Phdr) *p = &info->dlpi_phdr[i];
      if (p->p_type != PT_NOTE)
	continue;
      f->id_len = bpa_xcvr_build_id (
	(const u8 *) (info->dlpi_addr + p->p_vaddr), p->p_memsz, f->id,
	sizeof (f->id));
      if (f->id_len)
	break;
    }
  return 1;
}

/* not a unit: resolves function pointers inside the running VPP's
 * dpdk_plugin.so - see the header */
static bpa_xcvr_dpdk_t *
bpa_xcvr_resolve (void)
{
  bpa_xcvr_dpdk_t *d = &bpa_xcvr_dpdk;
  bpa_xcvr_find_t f = { 0 };

  if (d->resolved)
    return d;
  d->resolved = 2;
  dl_iterate_phdr (bpa_xcvr_find_dpdk, &f);
  if (!f.found)
    {
      snprintf (d->why, sizeof (d->why),
		"the dataplane has no DPDK loaded (dpdk_plugin.so)");
      return d;
    }
  if (!bpa_xcvr_build_id_is (f.id, f.id_len, BPA_DPDK_SYMS_BUILD_ID))
    {
      /* The one refusal that protects the dataplane: these offsets are
       * addresses inside one build, and inside any other they are the
       * middle of some other function. */
      snprintf (d->why, sizeof (d->why),
		"dpdk_plugin.so is not the VPP %s build this plugin was "
		"made for",
		BPA_DPDK_SYMS_VPP_VERSION);
      return d;
    }
  d->is_valid_port = (void *) (f.base + BPA_DPDK_OFF_RTE_ETH_DEV_IS_VALID_PORT);
  d->macaddr_get = (void *) (f.base + BPA_DPDK_OFF_RTE_ETH_MACADDR_GET);
  d->name_by_port = (void *) (f.base + BPA_DPDK_OFF_RTE_ETH_DEV_GET_NAME_BY_PORT);
  d->module_info = (void *) (f.base + BPA_DPDK_OFF_RTE_ETH_DEV_GET_MODULE_INFO);
  d->module_eeprom =
    (void *) (f.base + BPA_DPDK_OFF_RTE_ETH_DEV_GET_MODULE_EEPROM);
  d->resolved = 1;
  return d;
}

/* not a unit: reads VPP's interface tables and calls into DPDK - see the
 * header */
static int
bpa_xcvr_port_of (bpa_xcvr_dpdk_t *d, vnet_hw_interface_t *hi, u16 *port)
{
  u8 mac[8];
  u32 p, hits = 0, hit = 0;

  /* VPP's dpdk plugin numbers its devices in DPDK's port order and skips
   * only ports it refuses (dpdk_lib_init), so dev_instance IS the port on
   * every box this product builds. Taken only when the port's MAC is the
   * interface's - a VPP that numbered differently is caught here, not
   * read from the wrong cage. */
  if (vec_len (hi->hw_address) >= 6 && hi->dev_instance < 1024
      && d->is_valid_port ((u16) hi->dev_instance)
      && d->macaddr_get ((u16) hi->dev_instance, mac) == 0
      && !memcmp (mac, hi->hw_address, 6))
    {
      *port = (u16) hi->dev_instance;
      return 0;
    }
  /* Otherwise the one port with this MAC. Two (a bundle's members carry the
   * bundle's) is no answer rather than a guess. */
  for (p = 0; p < 64 && vec_len (hi->hw_address) >= 6; p++)
    if (d->is_valid_port ((u16) p) && d->macaddr_get ((u16) p, mac) == 0
	&& !memcmp (mac, hi->hw_address, 6))
      {
	hits++;
	hit = p;
      }
  if (hits != 1)
    return -1;
  *port = (u16) hit;
  return 0;
}

/* not a unit: calls into DPDK on a live port - see the header */
static int
bpa_xcvr_read (bpa_xcvr_dpdk_t *d, bpa_xcvr_cache_t *c, u16 port, f64 now)
{
  bpa_rte_module_info_t mi = { 0 };
  bpa_xcvr_range_t r[4];
  u32 i, n;
  int rv, with_static;

  if ((rv = d->module_info (port, &mi)) != 0)
    return rv;
  if (mi.eeprom_len > BPA_XCVR_MAX_LEN)
    mi.eeprom_len = BPA_XCVR_MAX_LEN;
  with_static = c->type != mi.type || c->len != mi.eeprom_len
		|| now - c->static_at >= BPA_XCVR_STATIC_SECONDS;
  if (with_static)
    {
      clib_memset (c->valid, 0, sizeof (c->valid));
      c->type = mi.type;
      c->len = mi.eeprom_len;
    }
  n = bpa_xcvr_ranges (mi.type, mi.eeprom_len, with_static, r);
  for (i = 0; i < n; i++)
    {
      bpa_rte_eeprom_info_t ei = { .data = c->data + r[i].offset,
				   .offset = r[i].offset,
				   .length = r[i].length };
      if ((rv = d->module_eeprom (port, &ei)) != 0)
	{
	  /* A static range half read is not kept: the next ask starts over. */
	  c->static_at = 0;
	  return rv;
	}
      clib_memset (c->valid + r[i].offset, 1, r[i].length);
    }
  if (with_static)
    c->static_at = now;
  c->live_at = now;
  return 0;
}

/* not a unit: writes into the CLI's buffer - see the header */
static void
bpa_xcvr_print_bytes (vlib_main_t *vm, const u8 *data, const u8 *valid,
		      u32 len)
{
  u32 row, i;
  u8 *s = 0;

  for (row = 0; row < len; row += 16)
    {
      int any = 0;
      for (i = row; i < row + 16 && i < len; i++)
	any |= valid[i];
      if (!any)
	continue;
      vec_reset_length (s);
      s = format (s, "  %04x", row);
      for (i = row; i < row + 16 && i < len; i++)
	s = valid[i] ? format (s, " %02x", data[i]) : format (s, " --");
      vlib_cli_output (vm, "%v", s);
    }
  vec_free (s);
}

/* not a unit: a walk over VPP's interface pool, printing through the CLI -
 * see the header */
static void
bpa_xcvr_show_one (vlib_main_t *vm, vnet_main_t *vnm, vnet_hw_interface_t *hi)
{
  vnet_device_class_t *dc = vnet_get_device_class (vnm, hi->dev_class_index);
  bpa_xcvr_dpdk_t *d;
  bpa_xcvr_cache_t *c;
  bpa_xcvr_standin_t *st;
  f64 now = vlib_time_now (vm);
  u16 port;
  int rv;

  if (hi->sw_if_index < vec_len (bpa_xcvr_standin)
      && (st = &bpa_xcvr_standin[hi->sw_if_index])->bytes)
    {
      u32 len = vec_len (st->bytes);
      u8 *valid = 0;
      vec_validate_init_empty (valid, len - 1, 1);
      vlib_cli_output (vm, "interface %v source stand-in type %s length %u",
		       hi->name, bpa_xcvr_type_name (st->type), len);
      bpa_xcvr_print_bytes (vm, st->bytes, valid, len);
      vec_free (valid);
      return;
    }
  if (!dc || !dc->name || strcmp (dc->name, "dpdk"))
    {
      vlib_cli_output (vm, "interface %v unread not-dpdk not a DPDK port",
		       hi->name);
      return;
    }
  d = bpa_xcvr_resolve ();
  if (d->resolved != 1)
    {
      vlib_cli_output (vm, "interface %v unread unavailable %s", hi->name,
		       d->why);
      return;
    }
  if (bpa_xcvr_port_of (d, hi, &port))
    {
      vlib_cli_output (vm,
		       "interface %v unread unavailable no single DPDK port "
		       "carries this interface's MAC",
		       hi->name);
      return;
    }
  vec_validate (bpa_xcvr_cache, hi->hw_if_index);
  c = &bpa_xcvr_cache[hi->hw_if_index];
  if (c->port != port || !c->device[0])
    {
      clib_memset (c, 0, sizeof (*c));
      c->port = port;
      if (d->name_by_port (port, c->device) != 0)
	snprintf (c->device, sizeof (c->device), "port%u", port);
      c->device[sizeof (c->device) - 1] = 0;
    }
  if (!c->live_at || now - c->live_at >= BPA_XCVR_LIVE_SECONDS || c->rv)
    c->rv = rv = bpa_xcvr_read (d, c, port, now);
  else
    rv = c->rv;
  if (rv)
    {
      /* ixgbe answers -EIO with the cage empty, and -ENOTSUP from a PMD
       * with no module access (virtio, the null PMD). */
      vlib_cli_output (vm,
		       "interface %v unread no-module the card answered %d "
		       "(%s) on DPDK port %u",
		       hi->name, rv, strerror (rv < 0 ? -rv : rv), port);
      c->live_at = 0;
      return;
    }
  vlib_cli_output (vm,
		   "interface %v source dpdk type %s length %u port %u "
		   "device %s age %.1f",
		   hi->name, bpa_xcvr_type_name (c->type), c->len, port,
		   c->device, now - c->live_at);
  bpa_xcvr_print_bytes (vm, c->data, c->valid, c->len);
}

/* not a unit: a VLIB_CLI_COMMAND body, driven by lab/transceiver.sh - see
 * the header */
static clib_error_t *
bpa_show_transceiver_command_fn (vlib_main_t *vm, unformat_input_t *input,
				 vlib_cli_command_t *cmd)
{
  vnet_main_t *vnm = vnet_get_main ();
  vnet_hw_interface_t *hi;
  u32 sw_if_index = ~0;

  if (unformat (input, "%U", unformat_vnet_sw_interface, vnm, &sw_if_index))
    {
      vnet_sw_interface_t *sw = vnet_get_sup_sw_interface (vnm, sw_if_index);
      bpa_xcvr_show_one (vm, vnm,
			 vnet_get_hw_interface (vnm, sw->hw_if_index));
      return 0;
    }
  pool_foreach (hi, vnm->interface_main.hw_interfaces)
    {
      if (hi->hw_if_index == 0) /* local0 */
	continue;
      bpa_xcvr_show_one (vm, vnm, hi);
    }
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_transceiver_command, static) = {
  .path = "show bpa transceiver",
  .short_help = "show bpa transceiver [<interface>]",
  .function = bpa_show_transceiver_command_fn,
  /* Off the worker barrier: see the header. */
  .is_mp_safe = 1,
};

/* not a unit: a VLIB_CLI_COMMAND body, driven by lab/transceiver.sh - see
 * the header */
static clib_error_t *
bpa_transceiver_standin_command_fn (vlib_main_t *vm, unformat_input_t *input,
				    vlib_cli_command_t *cmd)
{
  vnet_main_t *vnm = vnet_get_main ();
  u32 sw_if_index = ~0, type = 0;
  u8 *word = 0, *bytes = 0;
  bpa_xcvr_standin_t *st;

  if (!unformat (input, "%U", unformat_vnet_sw_interface, vnm, &sw_if_index))
    return clib_error_return (0, "bpa transceiver stand-in: which interface");
  sw_if_index = vnet_get_sup_sw_interface (vnm, sw_if_index)->sw_if_index;
  vec_validate (bpa_xcvr_standin, sw_if_index);
  st = &bpa_xcvr_standin[sw_if_index];
  if (unformat (input, "clear"))
    {
      vec_free (st->bytes);
      st->type = 0;
      return 0;
    }
  if (!unformat (input, "type %s hex %U", &word, unformat_hex_string, &bytes))
    return clib_error_return (
      0, "bpa transceiver stand-in: type <sff-...> hex <bytes> | clear");
  vec_add1 (word, 0);
  type = bpa_xcvr_type_of ((char *) word);
  vec_free (word);
  if (!type || vec_len (bytes) == 0 || vec_len (bytes) > BPA_XCVR_MAX_LEN)
    {
      vec_free (bytes);
      return clib_error_return (
	0, "bpa transceiver stand-in: a type sff-8079, sff-8472, sff-8636 or "
	   "sff-8436, and 1-640 bytes");
    }
  vec_free (st->bytes);
  st->bytes = bytes;
  st->type = type;
  return 0;
}

VLIB_CLI_COMMAND (bpa_transceiver_standin_command, static) = {
  .path = "bpa transceiver stand-in",
  .short_help = "bpa transceiver stand-in <interface> "
		"(type <sff-8079|sff-8472|sff-8636|sff-8436> hex <bytes> "
		"| clear)",
  .function = bpa_transceiver_standin_command_fn,
};
