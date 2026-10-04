/*
 * bpa_identity.h - the subscriber a private address belonged to (#21).
 *
 * The table is written whole by the control plane and read on the allocation
 * path. See bpa_identity.c for why it is a sorted array, and for the rule
 * that keeps personal data out of every screen on this box.
 */
#ifndef __included_bpa_identity_h__
#define __included_bpa_identity_h__

#ifdef BPA_STANDALONE
#include "bpa_shim.h"
#endif

/*
 * How much of an identity a record carries.
 *
 * MEASURED AGAINST WHAT IT COSTS rather than chosen: the identity is copied
 * into the queue entry at the instant of allocation, so this bound times
 * BPA_LOG_QUEUE_MAX (8192) times the worker count is what the queues hold. At
 * 32 bytes that is 256 KB per worker, which is the price of a record that
 * names who held the address AT the allocation rather than who holds it when
 * the log is drained.
 *
 * A RADIUS User-Name is 253 bytes at its widest and a PPPoE login is far
 * shorter in practice; a name longer than this is truncated rather than
 * refused, and the statement that loads the table says so.
 */
#define BPA_IDENTITY_MAX 32

/*
 * Where a table came from. The static map is a file an operator's
 * provisioning writes; the leases are one a DHCP server writes and nobody
 * edits. Same table, different grammar, and the KIND is carried rather than
 * sniffed: a file's first line is a poor thing to bet a court record on.
 */
typedef enum
{
  BPA_IDENTITY_MAP = 0,
  BPA_IDENTITY_DHCP_LEASES = 1,
} bpa_identity_kind_t;

typedef struct
{
  u32 ip;
  char name[BPA_IDENTITY_MAX];
} bpa_identity_entry_t;

/*
 * A NAT64 subscriber's identity (#485): the prefix a BNG delegated
 * (Delegated-IPv6-Prefix, RFC 4818), and who it was delegated to. The
 * subscriber a record names is a prefix too, so the question is which
 * delegation COVERS it - and the entries never overlap, so the answer is one
 * bisection on the start address.
 */
typedef struct
{
  u8 prefix[16];
  u8 len;
  char name[BPA_IDENTITY_MAX];
} bpa_identity6_entry_t;

typedef struct
{
  bpa_identity_entry_t *e;
  u32 n;
  bpa_identity6_entry_t *e6; /* sorted by prefix, never overlapping */
  u32 n6;
  f64 read_at;          /* when the control plane last built it */
  u32 n_truncated;      /* names that did not fit, counted rather than lost */
  /*
   * The file this came from, so `show bpa config` can give it back. The path
   * is configuration; what is IN the file is not, and is never printed.
   */
  u8 *path;
  /* Which grammar that file is in, so the running configuration can give
   * the statement back the way it was typed. */
  bpa_identity_kind_t kind;
} bpa_identity_table_t;

extern const char bpa_identity_unknown[];

int bpa_identity_cmp (const void *a, const void *b);
int bpa_identity_key_cmp (const void *a, const void *b);
u32 bpa_identity_at (const bpa_identity_table_t *t, u32 ip, int *found);
int bpa_identity_upsert (bpa_identity_table_t *t, u32 ip, const char *name);
int bpa_identity_remove (bpa_identity_table_t *t, u32 ip);
int bpa_identity_parse_line (const char *line, bpa_identity_entry_t *out);
int bpa_identity_lease_line (const char *line, bpa_identity_entry_t *out,
			     int *in_block);
void bpa_identity_lookup (const bpa_identity_table_t *t, u32 ip, char *out,
			  u32 cap);
u32 bpa_identity6_at (const bpa_identity_table_t *t, const u8 addr[16]);
void bpa_identity_lookup6 (const bpa_identity_table_t *t, const u8 addr[16],
			   u8 len, char *out, u32 cap);
int bpa_identity6_upsert (bpa_identity_table_t *t, const u8 prefix[16],
			  u8 len, const char *name);
int bpa_identity6_remove (bpa_identity_table_t *t, const u8 prefix[16],
			  u8 len);
#ifndef BPA_STANDALONE
clib_error_t *bpa_identity_load (const char *path);
clib_error_t *bpa_identity_load_kind (const char *path,
				      bpa_identity_kind_t kind);
#endif

#endif /* __included_bpa_identity_h__ */
