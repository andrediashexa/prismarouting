/*
 * bpa_keys.h - the session table's keys, and the two rules a session lives by.
 *
 * WHY A HEADER OF ITS OWN (#472). These were static_always_inline in
 * bpa_session.h, which only a VPP build compiles - a bihash_16_8 and VPP's
 * tcp_packet.h are in it - so none of them had a unit test, and until #472
 * the checker did not count a header's functions at all. They are the part of
 * the CGNAT that decides which session a packet belongs to and how long it
 * lives, and a key that packs two fields over each other is two subscribers
 * sharing a mapping. So they live here, compile with the plain-cc harness as
 * well as with VPP, and test/test_bpa_keys.c holds them.
 *
 * The functions that take a bpa_session_t stay in bpa_session.h as one-line
 * wrappers over the rules below, because that struct is VPP's.
 */
#ifndef __included_bpa_keys_h__
#define __included_bpa_keys_h__

#include "bpa_pool.h"

#ifdef BPA_STANDALONE
/* The layout VPP's bihash_16_8 gives its key-value pair: two key words and
 * one value word. The harness needs the fields, not the table. */
typedef struct
{
  u64 key[2];
  u64 value;
} clib_bihash_kv_16_8_t;
#define BPA_TCP_FLAG_FIN 0x01
#define BPA_TCP_FLAG_RST 0x04
#define BPA_TCP_FLAG_ACK 0x10
#else
#include <vppinfra/bihash_16_8.h>
#include <vnet/tcp/tcp_packet.h>
#define BPA_TCP_FLAG_FIN TCP_FLAG_FIN
#define BPA_TCP_FLAG_RST TCP_FLAG_RST
#define BPA_TCP_FLAG_ACK TCP_FLAG_ACK
#endif

/*
 * A TCP session's state, as far as the timeout needs one. Without it a
 * closed connection held its port for the established timeout - two hours.
 * In a 512-port block that exhausts the subscriber quickly, and the complaint
 * arrives as "the internet stopped" with nothing obvious in the dataplane.
 */
typedef enum
{
  BPA_TCP_INIT = 0, /* handshake in progress */
  BPA_TCP_ESTAB,    /* ACK seen after the handshake */
  BPA_TCP_CLOSING,  /* FIN seen in either direction */
  BPA_TCP_CLOSED,   /* RST: expires on the next sweep */
} bpa_tcp_state_t;

/*
 * Key for the per-subscriber record.
 *
 * BOTH halves, and the second one is not decoration. sub_idx is an index
 * within ONE pool's inside set, so it is unique per pool and not globally:
 * keyed on sub_idx alone, subscriber 5 of pool A and subscriber 5 of pool B
 * hashed to the same entry and shared one set of port bitmaps and one list of
 * blocks. A subscriber of pool B was then translated onto pool A's public
 * address and logged against pool A's block - a wrong answer to a court
 * order, produced with full confidence (issue #57).
 */
static_always_inline uword
bpa_sub_key (u32 pool_index, u32 sub_idx)
{
  return ((uword) pool_index << 32) | (uword) sub_idx;
}

/*
 * The two session keys, and they are not the same key.
 *
 * They were the same function while the second word was unused, which made it
 * easy to forget that the private side and the public side identify different
 * things. They are separate now because only one of them needs the VRF:
 *
 *   IN  (private address, private port, protocol, VRF)
 *   OUT (public address, public port, protocol)
 *
 * The private side needs the VRF because two tenants may use the same private
 * address, and without it one subscriber is translated onto the other's
 * mapping - silently, which is the failure this product exists to prevent.
 *
 * The public side does not, because a public address belongs to exactly one
 * pool: two pools sharing one would make the return path ambiguous, and that
 * is refused when the pool is created.
 *
 * The VRF is the packet's FIB index, which is available before the pool is
 * known - and it has to be, because the session lookup happens first. That is
 * the whole reason this qualifier is the packet's and not the pool's.
 */
static_always_inline void
bpa_key_in (clib_bihash_kv_16_8_t *kv, u32 addr, u16 port, u8 proto, u32 fib)
{
  kv->key[0] = (u64) addr | ((u64) port << 32) | ((u64) proto << 48);
  kv->key[1] = fib;
}

/* The outside key: the public socket alone; the public space has no VRF. */
static_always_inline void
bpa_key_out (clib_bihash_kv_16_8_t *kv, u32 addr, u16 port, u8 proto)
{
  kv->key[0] = (u64) addr | ((u64) port << 32) | ((u64) proto << 48);
  kv->key[1] = 0;
}

/*
 * A GRE mapping needs two keys that are not each other's mirror, so it gets
 * its own key builder.
 *
 * Inbound is ordinary: the packet carries the Call ID this box handed out, so
 * (public address, that Call ID) finds it, and bpa_key() above already builds
 * that - the Call ID comes out of the subscriber's port block like a port, so
 * the arithmetic pre-check and the worker handoff work on it unchanged.
 *
 * Outbound is not: the packet carries the Call ID the SERVER assigned, which
 * this box did not choose and cannot make unique. Two servers picking the
 * same value for the same subscriber is entirely possible - a subscriber
 * with two PPTP tunnels up is an ordinary home worker - so the remote address
 * goes into the second key word to separate them.
 *
 * `role` separates the two entries the ALG keeps in the same table: the data
 * key, found by the server's Call ID, and the lookup key the ALG itself uses,
 * found by the subscriber's own Call ID. Without it a subscriber whose own
 * Call ID happened to equal the server's would overwrite one with the other.
 *
 * The second key word was reserved for a VRF id, and GRE spends it; since
 * VRF landed (#26) the two are packed - the remote in the low half, the FIB
 * index in the high one - which test/test_bpa_keys.c holds apart.
 */
#define BPA_GRE_KEY_DATA 0 /* keyed by the server's Call ID  */
#define BPA_GRE_KEY_ALG	 1 /* keyed by the subscriber's own */

static_always_inline void
bpa_key_gre (clib_bihash_kv_16_8_t *kv, u32 addr, u16 call_id, u32 remote,
	     u8 role, u32 fib)
{
  kv->key[0] = (u64) addr | ((u64) call_id << 32)
	       | ((u64) BPA_PROTO_GRE << 48) | ((u64) role << 56);
  kv->key[1] = (u64) remote | ((u64) fib << 32);
}

/*
 * How long a session lives in silence, from what it is and the timeouts in
 * force. An ALG's expectation lives by its own timeout whatever it carries;
 * GRE and ESP share one, since both are portless tunnels judged live by the
 * same kind of keepalive; TCP by its state. A closed (RST) connection gets
 * 0, and a handshake that never completed the SYN timeout, not the
 * transitory one: a scan makes one of those per probe.
 */
static_always_inline u32
bpa_timeout_for (u8 proto, u8 tcp_state, u8 is_expect,
		 const bpa_timeouts_t *t)
{
  if (PREDICT_FALSE (is_expect))
    return t->alg_expect;
  switch (proto)
    {
    case BPA_PROTO_UDP:
      return t->udp;
    case BPA_PROTO_ICMP:
      return t->icmp;
    case BPA_PROTO_GRE:
    case BPA_PROTO_ESP:
      return t->gre;
    default:
      break;
    }
  switch (tcp_state)
    {
    case BPA_TCP_CLOSED:
      return 0;
    case BPA_TCP_ESTAB:
      return t->tcp_estab;
    case BPA_TCP_INIT:
      return t->tcp_syn;
    default:
      return t->tcp_trans;
    }
}

/*
 * The TCP state after a segment with these flags. RST closes whatever the
 * state; FIN moves anything not already closed to closing; an ACK
 * establishes only a connection still in its handshake - an ACK on a closing
 * one must not reopen it for two hours.
 */
static_always_inline u8
bpa_tcp_next (u8 state, u8 flags)
{
  if (PREDICT_FALSE (flags & BPA_TCP_FLAG_RST))
    return BPA_TCP_CLOSED;
  if (PREDICT_FALSE (flags & BPA_TCP_FLAG_FIN))
    return state != BPA_TCP_CLOSED ? BPA_TCP_CLOSING : state;
  if ((flags & BPA_TCP_FLAG_ACK) && state == BPA_TCP_INIT)
    return BPA_TCP_ESTAB;
  return state;
}

#endif /* __included_bpa_keys_h__ */
