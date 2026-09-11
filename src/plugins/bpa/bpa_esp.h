/*
 * bpa_esp.h - IPsec ESP passthrough (RFC 4303).
 *
 * This is NOT an ALG in the sense the other four are. FTP, RTSP, PPTP and SIP
 * exist because a protocol writes an address or a port into a payload and the
 * payload has to be rewritten to agree with the translation. ESP writes
 * nothing this box could correct, and the one field that looks like a port -
 * the SPI - MUST NOT be touched: RFC 4303 section 3.3.2 puts the SPI inside
 * the integrity check, so a rewritten SPI arrives with an ICV that no longer
 * verifies and the receiver discards the packet without a word. That is the
 * exact failure mode this project has already paid for once with a stale
 * checksum, and here it is not even repairable - the key is the subscriber's.
 *
 * So ESP is passed through: the addresses are translated and nothing else is.
 * What has to be invented is only the answer to "which subscriber does an
 * inbound ESP packet belong to", because the packet carries no port and the
 * SPI it carries was chosen by the subscriber's own stack and announced over
 * IKE, which is encrypted.
 *
 * The answer used here is the PEER:
 *
 *   inbound key  = (our public address, the peer's address)
 *   outbound key = (subscriber's private address, the peer's address, VRF)
 *
 * Both directions know both halves without learning anything, which is what
 * makes it work on the first inbound packet of an SA rather than after one
 * has been lost. A subscriber may hold as many tunnels as it has peers.
 *
 * WHERE IT STOPS, and it is a real limit rather than a rounding error: two
 * subscribers sharing one public address whose tunnels go to the SAME peer
 * gateway cannot be told apart, because every field of both flows is then
 * identical on the wire. The first subscriber to send keeps the binding; the
 * second is counted under BPA_ESP_PEER_TAKEN and is not bound, so its inbound
 * packets are dropped rather than delivered to its neighbour. Delivering them
 * would be a guess, and a guess here puts one customer's ciphertext into
 * another customer's IPsec stack. That case is what NAT-T (RFC 3948) exists
 * for, and a CPE that does NAT-T never reaches this code at all: it sends
 * UDP 4500, which is an ordinary UDP session.
 *
 * WHY THE BINDING TABLE IS GLOBAL, when nothing else in this plugin is.
 *
 * Every other mapping is per worker, and that is safe because both directions
 * of a subscriber are steered to the same worker: outbound by the source
 * address, inbound by the arithmetic that turns (public address, port) back
 * into a subscriber index. An inbound ESP packet has no port, so that second
 * step cannot run, and no rule computable from the packet alone sends the two
 * directions of an ESP flow to the same worker. The choice is therefore
 * between a shared table and steering ESP by the peer address - which would
 * put the session on a worker that does not hold the subscriber's port block,
 * and so could not learn the subscriber's public address at all in dynamic
 * mode.
 *
 * A clib_bihash is multi-writer by construction (per-bucket locks), so the
 * cost is a locked bucket on ESP packets only, and ESP is a rounding error in
 * a residential mix. Entries are added by the outbound datapath and removed
 * by bpa_session_free(), which runs under the worker barrier - so the table
 * has no lifetime of its own to get wrong, which was the other candidate for
 * "a second thing to keep in step".
 */

#ifndef __included_bpa_esp_h__
#define __included_bpa_esp_h__

#include <vnet/ip/ip4_packet.h>

#include "bpa.h"

/*
 * RFC 4303 section 2: the ESP header begins with a 32-bit SPI and a 32-bit
 * sequence number. Neither is rewritten; the SPI is read only so that a trace
 * and a lab can say which SA a packet belonged to.
 */
#define BPA_ESP_HDR_BYTES 8

static_always_inline u32
bpa_esp_spi (const void *p)
{
  return clib_net_to_host_u32 (((const u32 *) p)[0]);
}

/*
 * The private side of an ESP binding: one entry per (subscriber, peer).
 *
 * Per peer and not per subscriber, because a subscriber with two tunnels is
 * an ordinary home worker and one entry for both would have to be torn down
 * when either of them ended. The same packing as bpa_key_gre(): the peer
 * address in the low half of the second word, the VRF in the high one.
 */
static_always_inline void
bpa_key_esp (clib_bihash_kv_16_8_t *kv, u32 priv, u32 remote, u32 fib)
{
  kv->key[0] = (u64) priv | ((u64) BPA_PROTO_ESP << 48);
  kv->key[1] = (u64) remote | ((u64) fib << 32);
}

/*
 * The public side, which is the shared table's key. No VRF: a public address
 * belongs to exactly one pool, which is the same reason bpa_key_out() has
 * none.
 */
static_always_inline void
bpa_key_esp_pub (clib_bihash_kv_16_8_t *kv, u32 pub, u32 remote)
{
  kv->key[0] = (u64) pub | ((u64) BPA_PROTO_ESP << 48);
  kv->key[1] = remote;
}

/*
 * The binding's value, which is everything the inbound path needs and nothing
 * more: whom to give the packet to, and which VRF to give it back into.
 *
 * Packed into the bihash's own 64-bit value rather than pointing at a record.
 * A record would be a pool that the datapath allocates from on several workers
 * at once, and pool_get() is not that; two u32 fields fit exactly, so there is
 * nothing to allocate and nothing to race on.
 */
static_always_inline u64
bpa_esp_value (u32 ip_priv, u32 fib)
{
  return (u64) ip_priv | ((u64) fib << 32);
}

static_always_inline void
bpa_esp_unpack (u64 v, u32 *ip_priv, u32 *fib)
{
  *ip_priv = (u32) v;
  *fib = (u32) (v >> 32);
}

/*
 * Finds the subscriber an inbound ESP packet belongs to.
 *
 * Returns 0 and fills both outputs, or -1 when this public address has no
 * tunnel to that peer - which is the ordinary case for unsolicited ESP and
 * for the second subscriber of an ambiguous pair.
 */
static_always_inline int
bpa_esp_lookup (u32 pub, u32 remote, u32 *ip_priv, u32 *fib)
{
  bpa_main_t *bm = &bpa_main;
  clib_bihash_kv_16_8_t kv, value;

  bpa_key_esp_pub (&kv, pub, remote);
  if (PREDICT_FALSE (clib_bihash_search_16_8 (&bm->esp, &kv, &value)))
    return -1;
  bpa_esp_unpack (value.value, ip_priv, fib);
  return 0;
}

#endif /* __included_bpa_esp_h__ */
