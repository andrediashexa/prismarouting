/*
 * bpa_linkcount.c - each port's link changes, counted (#588): `show bpa
 * link-changes`, which the host's watcher reads beside `show
 * hardware-interfaces brief` so that a link that went down and came back
 * between two of its readings is still an event, with how many times.
 *
 * bpa_linkcount.h says why the count is kept here and what it cannot see.
 * Every function in this file is the VPP-facing half and carries `not a
 * unit:`; what it decides is the header's, driven by
 * test/test_bpa_linkcount.c, and lab/show-log-watch.sh drives this file on a
 * memif whose far end is taken away and put back between two readings.
 *
 * MAIN THREAD ONLY. VPP calls the link and add/del functions from the main
 * thread (vnet_hw_interface_set_flags, the interface creation and deletion
 * paths), and a CLI command runs there too; no worker touches the counts.
 * So the command is mp-safe - it does not stop the workers to read a vector
 * only the main thread writes - as `show bpa transceiver` is.
 */

#include <vlib/vlib.h>
#include <vnet/vnet.h>

#include "bpa.h"
#include "bpa_linkcount.h"

static bpa_linkcount_t *bpa_linkcounts; /* by hw_if_index */

/* not a unit: a VNET_HW_INTERFACE_LINK_UP_DOWN_FUNCTION VPP calls on every
 * link change; bpa_linkcount_note() is what it does, and is tested */
static clib_error_t *
bpa_linkcount_up_down (vnet_main_t *vnm, u32 hw_if_index, u32 flags)
{
  vec_validate (bpa_linkcounts, hw_if_index);
  bpa_linkcount_note (&bpa_linkcounts[hw_if_index],
		      (flags & VNET_HW_INTERFACE_FLAG_LINK_UP) != 0);
  return 0;
}

VNET_HW_INTERFACE_LINK_UP_DOWN_FUNCTION (bpa_linkcount_up_down);

/* not a unit: a VNET_HW_INTERFACE_ADD_DEL_FUNCTION VPP calls when an
 * interface is created or deleted; bpa_linkcount_reset() is what it does,
 * and is tested */
static clib_error_t *
bpa_linkcount_add_del (vnet_main_t *vnm, u32 hw_if_index, u32 is_add)
{
  vec_validate (bpa_linkcounts, hw_if_index);
  bpa_linkcount_reset (&bpa_linkcounts[hw_if_index]);
  return 0;
}

VNET_HW_INTERFACE_ADD_DEL_FUNCTION (bpa_linkcount_add_del);

/* not a unit: a VLIB_CLI_COMMAND body, driven by lab/show-log-watch.sh -
 * the line's shape is read by eventwatch.parse_link_changes(), which
 * test/test_eventwatch.py holds to this file's format string */
static clib_error_t *
bpa_show_link_changes_command_fn (vlib_main_t *vm, unformat_input_t *input,
				  vlib_cli_command_t *cmd)
{
  vnet_main_t *vnm = vnet_get_main ();
  vnet_hw_interface_t *hi;

  pool_foreach (hi, vnm->interface_main.hw_interfaces)
    {
      bpa_linkcount_t c = { 0 };
      if (hi->hw_if_index == 0) /* local0 */
	continue;
      if (hi->hw_if_index < vec_len (bpa_linkcounts))
	c = bpa_linkcounts[hi->hw_if_index];
      vlib_cli_output (vm, "interface %v link %s ups %u downs %u", hi->name,
		       (hi->flags & VNET_HW_INTERFACE_FLAG_LINK_UP) ? "up" :
								      "down",
		       c.ups, c.downs);
    }
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_link_changes_command, static) = {
  .path = "show bpa link-changes",
  .short_help = "show bpa link-changes",
  .function = bpa_show_link_changes_command_fn,
  /* Off the worker barrier: see the header. */
  .is_mp_safe = 1,
};
