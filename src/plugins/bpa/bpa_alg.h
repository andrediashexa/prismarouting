/*
 * bpa_alg.h - which application-layer gateways are switched on.
 *
 * An ALG reads and rewrites application payload, which is the opposite of
 * what the rest of this datapath does. It is here because some protocols put
 * an address or an identifier inside the payload and then expect the peer to
 * use it: without an ALG the protocol does not work through the CGNAT, and
 * the subscriber reports it as "the internet is broken".
 *
 * Each ALG is one bit, and each bit is an operator decision. An ALG that
 * misparses is worse than no ALG - it corrupts traffic that would otherwise
 * merely fail - so every one of them MUST be switchable off on a running box
 * without restarting anything.
 */

#ifndef __included_bpa_alg_h__
#define __included_bpa_alg_h__

#define foreach_bpa_alg                                                       \
  _ (PPTP, "pptp", "PPTP control channel and its GRE data channel")

typedef enum
{
#define _(sym, name, desc) BPA_ALG_BIT_##sym,
  foreach_bpa_alg
#undef _
    BPA_N_ALG,
} bpa_alg_bit_t;

typedef enum
{
#define _(sym, name, desc) BPA_ALG_##sym = 1 << BPA_ALG_BIT_##sym,
  foreach_bpa_alg
#undef _
} bpa_alg_t;

/*
 * Enabled by default.
 *
 * PPTP is old and its cryptography is broken, which is an argument against
 * using it - not an argument for a CGNAT silently breaking the subscribers
 * who still do. A router dialling a corporate PPTP server produces a support
 * call the operator cannot diagnose, because from the outside everything
 * looks fine: the control connection establishes and the tunnel carries
 * nothing.
 */
#define BPA_ALG_DEFAULT (BPA_ALG_PPTP)

static_always_inline int
bpa_alg_on (u32 enabled, u32 bit)
{
  return (enabled & bit) != 0;
}

#endif /* __included_bpa_alg_h__ */
