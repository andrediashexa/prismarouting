/*
 * bpa_linkcount.h - how many times each port's link went up and down (#588):
 * the counter a reading every ten seconds needs to see a flap that happened
 * between two of its readings.
 *
 * WHY A COUNTER, AND WHY HERE. The host's watcher (src/prismash/eventwatch.py)
 * reads `show hardware-interfaces brief` every 10 s and compares the link
 * with the reading before. A port that went down and came back between two
 * readings reads "up" both times: the flap is invisible, and it is the flap
 * an operator chasing a bad fibre most needs to see. BGP and BFD already have
 * their daemons' own counters for this (connectionsDropped, session-down);
 * VPP keeps none for a link - `show hardware-interfaces` prints the state and
 * nothing of how it got there (VPP 26.06, measured). What VPP does have is
 * the moment: every link change of every hardware interface, from any driver
 * (DPDK's link poll, memif's connect and disconnect, af_xdp, af_packet),
 * goes through vnet_hw_interface_set_flags(), which calls the functions
 * registered with VNET_HW_INTERFACE_LINK_UP_DOWN_FUNCTION. The plugin
 * registers one (bpa_linkcount.c) and counts here.
 *
 * WHAT IT CANNOT SEE: a change the driver never reported. DPDK's ports are
 * polled by VPP's dpdk process (a few seconds apart), so a flap shorter than
 * that poll is not a change VPP saw either.
 *
 * WHY A HEADER. bpa_linkcount.c is the VPP-facing half - the callback, the
 * CLI - and is excused from unit tests for that; what it decides is here,
 * where test/test_bpa_linkcount.c drives it with nothing but calls.
 */

#ifndef __included_bpa_linkcount_h__
#define __included_bpa_linkcount_h__

/* One hardware interface's link history since it was created, or since the
 * plugin loaded. */
typedef struct
{
  u32 ups;   /* times its link came up */
  u32 downs; /* times its link went down */
  u8 up;     /* the state the last change left it in */
  u8 known;  /* whether any change has been seen */
} bpa_linkcount_t;

/*
 * A change of the link to `up`, as VPP reported it. VPP calls the link
 * functions only when LINK_UP changes, so every call is a transition - but a
 * repeated report of the state it already has (a driver setting the flags
 * twice) is not counted twice: the counter is "how many times it went down",
 * and a second "down" in a row is not a second time.
 */
static_always_inline void
bpa_linkcount_note (bpa_linkcount_t *c, int up)
{
  up = up != 0;
  if (c->known && c->up == up)
    return;
  if (up)
    c->ups++;
  else
    c->downs++;
  c->up = up;
  c->known = 1;
}

/*
 * An interface created or deleted: its history starts again. A deleted
 * interface's hw_if_index is handed to the next one created, and that one's
 * link has not gone down before. The watcher reads a counter that went
 * backwards as a new baseline, never as a flap.
 */
static_always_inline void
bpa_linkcount_reset (bpa_linkcount_t *c)
{
  c->ups = c->downs = 0;
  c->up = c->known = 0;
}

#endif /* __included_bpa_linkcount_h__ */
