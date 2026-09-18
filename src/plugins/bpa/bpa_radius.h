/*
 * bpa_radius.h - the accounting stream a BNG already sends, read for the one
 * thing this box needs from it: who is on an address (#21).
 */
#ifndef __included_bpa_radius_h__
#define __included_bpa_radius_h__

#ifdef BPA_STANDALONE
#include "bpa_shim.h"
#endif
#include "bpa_identity.h"

#define BPA_RADIUS_OK	     0
#define BPA_RADIUS_MALFORMED 1
#define BPA_RADIUS_BAD_AUTH  2

/* RFC 2866 Acct-Status-Type values this box acts on. */
#define BPA_ACCT_START	 1
#define BPA_ACCT_STOP	 2
#define BPA_ACCT_INTERIM 3

typedef struct
{
  u8 id;
  u8 request_authenticator[16];
  u32 status;
  u32 framed_ip;
  char user[BPA_IDENTITY_MAX];
  u8 have_ip;
  u8 have_status;
  u8 user_truncated;
} bpa_radius_acct_t;

int bpa_radius_acct_parse (const u8 *pkt, u32 len, const char *secret,
			   bpa_radius_acct_t *out);
u32 bpa_radius_acct_response (u8 *out, u32 cap, const bpa_radius_acct_t *req,
			      const char *secret);

#endif /* __included_bpa_radius_h__ */
