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

/* RFC 2866's port, and how many datagrams one service pass takes. */
#define BPA_RADIUS_ACCT_PORT	1813
#define BPA_RADIUS_PER_SERVICE	64

typedef struct
{
  int fd;
  u16 port;
  u8 enabled;
  u8 *secret;

  u64 n_received, n_bad_auth, n_malformed, n_applied;
  u64 n_answered, n_unanswered;
} bpa_radius_listener_t;

extern bpa_radius_listener_t bpa_radius;

/*
 * The longest secret this box can be given, from the field it travels in:
 * `string secret[128]` in bpa.api. Every buffer that holds a secret beside
 * something else is sized from this rather than from a number that looked
 * big enough - #425 was a 128-byte scratch holding a 20-byte header and a
 * secret, which silently stopped answering the NAS at 109 characters.
 */
#define BPA_RADIUS_SECRET_MAX 128

#ifndef BPA_STANDALONE

clib_error_t *bpa_radius_enable (u16 port, const char *secret, int enable);
void bpa_radius_service (void);
#endif

int bpa_radius_acct_parse (const u8 *pkt, u32 len, const char *secret,
			   bpa_radius_acct_t *out);
u32 bpa_radius_acct_response (u8 *out, u32 cap, const bpa_radius_acct_t *req,
			      const char *secret);

#endif /* __included_bpa_radius_h__ */
