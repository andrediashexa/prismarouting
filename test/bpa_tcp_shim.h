/*
 * bpa_tcp_shim.h - TCP header, checksum and session stand-ins for the
 * standalone unit tests.
 *
 * Extends bpa_shim.h rather than replacing it, so the existing pool, port and
 * RTSP tests are untouched by anything here.
 *
 * The checksum arithmetic below is a stand-in for VPP's, and the tests that
 * use it MUST NOT take it on trust: every checksum assertion in
 * test_bpa_mss.c and test_bpa_ftp.c compares the INCREMENTAL update made by
 * the code under test against a FULL recomputation over the whole segment,
 * written independently in the test. If this arithmetic were wrong, that
 * comparison is what fails - the tests are not asserting that the shim agrees
 * with itself.
 *
 * The session struct carries only the fields the pure functions read. A field
 * renamed in the real bpa_session_t breaks this build, which is the point; a
 * field whose TYPE changes would not, and that is the limit of what a shim
 * can promise.
 */
#ifndef __included_bpa_tcp_shim_h__
#define __included_bpa_tcp_shim_h__

#include <string.h>
#include "bpa_shim.h"

/* This test runs on a little-endian host; the byte order helpers are spelled
 * out rather than pulled from <endian.h> so the file says what it assumes. */
static inline u16
bpa_bswap16 (u16 v)
{
  return (u16) ((v >> 8) | (v << 8));
}
static inline u32
bpa_bswap32 (u32 v)
{
  return ((v >> 24) & 0xff) | ((v >> 8) & 0xff00) | ((v << 8) & 0xff0000)
	 | ((v << 24) & 0xff000000u);
}

#define clib_net_to_host_u16(x) bpa_bswap16 (x)
#define clib_host_to_net_u16(x) bpa_bswap16 (x)
#define clib_net_to_host_u32(x) bpa_bswap32 (x)
#define clib_host_to_net_u32(x) bpa_bswap32 (x)

#define clib_mem_unaligned(p, type)                                           \
  (*((type *) __builtin_assume_aligned ((void *) (p), 1)))

/* ------------------------------------------------------------ checksums */

typedef uint64_t ip_csum_t;

/*
 * Copied from VPP 26.06, /usr/include/vnet/ip/ip_packet.h, names and all.
 *
 * The names say the opposite of what the functions do, and that is not a
 * transcription slip - it is the trap that cost issue #63 a revert. They are
 * named for what happens to the DATA, while the value they operate on is the
 * checksum, which is the one's complement of the sum over that data:
 *
 *   ip_csum_add_even (c, x)   a field of value x is ADDED to the data,
 *                             so x is SUBTRACTED from c
 *   ip_csum_sub_even (c, x)   a field of value x LEAVES the data,
 *                             so x is ADDED to c
 *
 * This shim used to define them the intuitive way round and compose
 * ip_csum_update the other way round to compensate. The composite agreed with
 * VPP, so every test passed - and any code that reached for a primitive
 * directly got the opposite of what the same call does on the box, with no
 * test able to see it. That is exactly the mistake issue #63 shipped and
 * reverted, so the shim now mirrors VPP instead of correcting it.
 */
static inline ip_csum_t
ip_csum_with_carry (ip_csum_t sum, ip_csum_t x)
{
  ip_csum_t t = sum + x;
  return t + (t < x);
}

/* Update checksum changing field at even byte offset from x -> 0. */
static inline ip_csum_t
ip_csum_add_even (ip_csum_t c, ip_csum_t x)
{
  ip_csum_t d;

  d = c - x;

  /* Fold in carry from high bit. */
  d -= d > c;

  return d;
}

/* Update checksum changing field at even byte offset from 0 -> x. */
static inline ip_csum_t
ip_csum_sub_even (ip_csum_t c, ip_csum_t x)
{
  return ip_csum_with_carry (c, x);
}

/*
 * The `type, field` pair exists in VPP's macro to derive the field's offset
 * and size. VPP 26.06 uses them for one thing only: a field of ODD SIZE is
 * shifted a byte into place. Every field this plugin rewrites - the IPv4
 * source address, a TCP or UDP source port, an ICMP echo identifier - is two
 * or four bytes, so the shift never fires and the pair is accepted and
 * unused here. There is no odd-OFFSET byte swap in this release; do not add
 * one on the strength of older VPP.
 *
 * `sum` is the checksum as it is STORED, the one's complement of the sum over
 * the segment. On that inverted value the correction is to add the word that
 * is going away and subtract the one replacing it:
 *
 *   HC  = ~S                    the field as stored
 *   S'  = S - old + new         the sum after the change
 *   HC' = ~S' = ~S + old - new
 *
 * which is what sub_even-then-add_even spells, given what those two names
 * mean above. Written the other way round it is wrong by exactly
 * 2*(old-new), which is near enough to right that a segment with a small
 * clamp can still checksum correctly by luck. The full recomputation in the
 * tests is what settled the direction, and is what would catch it moving
 * again.
 */
#define ip_csum_update(sum, old, new, type, field)                            \
  ip_csum_add_even (ip_csum_sub_even ((sum), (ip_csum_t) (old)),              \
		    (ip_csum_t) (new))

static inline u16
ip_csum_fold (ip_csum_t c)
{
  while (c >> 16)
    c = (c & 0xffff) + (c >> 16);
  return (u16) c;
}

/* ------------------------------------------------------------ IPv4, ICMP */

/*
 * Field for field with VPP's, and laid out so that sizeof() is 20 with no
 * padding on any sane ABI - test_bpa_icmp.c asserts that at compile time,
 * because a header that is one byte longer here than on the box would move
 * every offset the parse under test computes and quietly make the test agree
 * with itself instead of with the product.
 */
typedef union
{
  u8 as_u8[4];
  u32 as_u32;
} ip4_address_t;

typedef struct
{
  u8 ip_version_and_header_length;
  u8 tos;
  u16 length;
  u16 fragment_id;
  u16 flags_and_fragment_offset;
  u8 ttl;
  u8 protocol;
  u16 checksum;
  ip4_address_t src_address, dst_address;
} ip4_header_t;

static inline int
ip4_header_bytes (const ip4_header_t *i)
{
  return sizeof (u32) * (i->ip_version_and_header_length & 0xf);
}

typedef struct
{
  u8 type;
  u8 code;
  u16 checksum;
} icmp46_header_t;

/* The numbers, not VPP's spelling of them: these are on the wire. */
#define ICMP4_echo_reply	      0
#define ICMP4_destination_unreachable 3
#define ICMP4_source_quench	      4
#define ICMP4_redirect		      5
#define ICMP4_echo_request	      8
#define ICMP4_time_exceeded	      11
#define ICMP4_parameter_problem	      12

#define IP_PROTOCOL_ICMP       1
#define IP_PROTOCOL_TCP	       6
#define IP_PROTOCOL_UDP	       17
#define IP_PROTOCOL_GRE	       47
#define IP_PROTOCOL_IPSEC_ESP  50

/* ---------------------------------------------------------- TCP and UDP */

typedef struct
{
  u16 src_port;
  u16 dst_port;
  u32 seq_number;
  u32 ack_number;
  u8 data_offset_and_reserved;
  u8 flags;
  u16 window;
  u16 checksum;
  u16 urgent_pointer;
} tcp_header_t;

typedef struct
{
  u16 src_port;
  u16 dst_port;
  u16 length;
  u16 checksum;
} udp_header_t;

static inline int
tcp_header_bytes (const tcp_header_t *t)
{
  return (t->data_offset_and_reserved >> 4) * 4;
}

#define TCP_OPTION_EOL	  0
#define TCP_OPTION_NOOP	  1
#define TCP_OPTION_MSS	  2
#define TCP_OPTION_WINDOW 3

#define TCP_FLAG_FIN 0x01
#define TCP_FLAG_SYN 0x02
#define TCP_FLAG_ACK 0x10

/*
 * The echo header, so that the `type, field` pair the rewrite passes to
 * ip_csum_update names something that exists here too. The macro discards the
 * pair, so a missing type would still compile - and a mirror that compiles
 * whatever it is given mirrors nothing.
 */
typedef struct
{
  u16 identifier;
  u16 sequence;
} icmp_echo_header_t;

/*
 * VPP compiles ASSERT out of a release build, and so does this. The suites
 * assert the same invariants by name, where a broken one prints a red line
 * saying which check it was instead of dropping a core.
 */
#define ASSERT(x) ((void) 0)

/* -------------------------------------------------------------- session */

/* Mirrors bpa_proto_t. The rewrite branches on it, so the ORDER matters as
 * much as the names: these are stored in a session and compared, never
 * printed. */
typedef enum
{
  BPA_PROTO_TCP = 0,
  BPA_PROTO_UDP,
  BPA_PROTO_ICMP,
  BPA_PROTO_GRE,
  BPA_PROTO_ESP,
  BPA_N_PROTO,
} bpa_proto_t;

#include "../src/plugins/bpa/bpa_seq.h"

typedef struct
{
  u32 ip_priv, ip_pub;
  u16 port_pub;
  u16 csum_d_addr, csum_d_l4;
  u8 proto;
  u16 port_priv;
  /* The correction an ALG left behind, which is now ONE mechanism shared by
   * all of them (#48). This mirror carries the real field so that renaming it
   * over there breaks this build, which is the point of the mirror. */
  bpa_seq_adj_t adj[BPA_N_DIR];
} bpa_session_t;

#endif /* __included_bpa_tcp_shim_h__ */
