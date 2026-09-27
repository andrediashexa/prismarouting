/*
 * test_bpa_l4.c - the L4 id a packet is keyed on, and the destination
 * rewrite every returning packet leaves with (bpa_l4_wire.h, #472).
 *
 * The id is what every session lookup, every worker handoff and every port
 * allocation is keyed on; read from the wrong offset or in the wrong byte
 * order, a flow matches nobody's session and a fresh one is opened per
 * packet - a block burnt through in a second, reported as "the internet
 * stopped". The rewrite is the checksum every out2in and hairpinned packet
 * carries to the subscriber; wrong, the subscriber's own stack drops it.
 *
 * Every checksum is checked the way a RECEIVER checks it: the header (or the
 * segment and its pseudo-header) summed with the checksum field still in it
 * must come to all ones. The sum is written out below, independently of the
 * incremental update under test.
 *
 * Mutations shown red: bpa_l4_reass() reading the port without the byte
 * swap; bpa_rewrite_dst() leaving the address out of the TCP correction;
 * the bounds check off by one byte.
 */
#include <stdio.h>
#include <string.h>
#include "bpa_tcp_shim.h"

#include "../src/plugins/bpa/bpa_l4_wire.h"

static int failures = 0;
static unsigned checks = 0;

#define CHECK(cond, ...)                                                      \
  do                                                                          \
    {                                                                         \
      checks++;                                                               \
      if (!(cond))                                                            \
	{                                                                     \
	  printf ("  FAIL: ");                                               \
	  printf (__VA_ARGS__);                                               \
	  printf ("\n");                                                      \
	  failures++;                                                         \
	}                                                                     \
    }                                                                         \
  while (0)

/* ------------------------------------------------------------ packets */

#define PRIV 0x64400005u /* 100.64.0.5 */
#define PUB  0xc8000001u /* 200.0.0.1 */
#define PEER 0x08080808u

/* The ones' complement sum of `n` bytes, big-endian words, not folded. */
static u32
sum_bytes (const u8 *p, u32 n, u32 sum)
{
  u32 i;

  for (i = 0; i + 1 < n; i += 2)
    sum += ((u32) p[i] << 8) | p[i + 1];
  if (n & 1)
    sum += (u32) p[n - 1] << 8;
  return sum;
}

static u16
fold (u32 sum)
{
  while (sum >> 16)
    sum = (sum & 0xffff) + (sum >> 16);
  return (u16) sum;
}

/* The IPv4 header, as a receiver checks it. */
static int
ip_ok (const ip4_header_t *ip)
{
  return fold (sum_bytes ((const u8 *) ip, ip4_header_bytes (ip), 0))
	 == 0xffff;
}

/* A TCP or UDP segment with its pseudo-header, as a receiver checks it. */
static int
l4_ok (const ip4_header_t *ip, const u8 *l4, u32 len)
{
  u32 sum = sum_bytes ((const u8 *) &ip->src_address, 8, 0);

  sum += ip->protocol + len;
  return fold (sum_bytes (l4, len, sum)) == 0xffff;
}

/* The field's own checksum set so that the receiver's sum comes out right:
 * computed over the whole thing with the field at zero. */
static void
set_ip_csum (ip4_header_t *ip)
{
  ip->checksum = 0;
  ip->checksum = clib_host_to_net_u16 (
    (u16) ~fold (sum_bytes ((const u8 *) ip, ip4_header_bytes (ip), 0)));
}

static u16
l4_csum (const ip4_header_t *ip, const u8 *l4, u32 len)
{
  u32 sum = sum_bytes ((const u8 *) &ip->src_address, 8, 0);

  sum += ip->protocol + len;
  return clib_host_to_net_u16 ((u16) ~fold (sum_bytes (l4, len, sum)));
}

static u8 pkt[128];

/* An IPv4 header with `ihl` words, from PEER to PUB, followed by `l4len`
 * bytes of patterned payload. Returns where the L4 header starts. */
static u8 *
mk (u8 proto_ip, u32 ihl, u32 l4len)
{
  ip4_header_t *ip = (ip4_header_t *) pkt;
  u32 i;

  memset (pkt, 0, sizeof (pkt));
  ip->ip_version_and_header_length = 0x40 | ihl;
  ip->ttl = 63;
  ip->protocol = proto_ip;
  ip->length = clib_host_to_net_u16 ((u16) (ihl * 4 + l4len));
  ip->src_address.as_u32 = clib_host_to_net_u32 (PEER);
  ip->dst_address.as_u32 = clib_host_to_net_u32 (PUB);
  for (i = 0; i < l4len; i++)
    pkt[ihl * 4 + i] = (u8) (0x31 + i * 7);
  return pkt + ihl * 4;
}

static void
put16 (u8 *p, u16 v)
{
  p[0] = v >> 8;
  p[1] = v & 0xff;
}

int
main (void)
{
  ip4_header_t *ip = (ip4_header_t *) pkt;
  u16 port = 0;
  void *l4h;
  u8 *l4;

  printf ("=== bpa_l4_read: the id each protocol is keyed on ===\n");
  l4 = mk (IP_PROTOCOL_TCP, 5, 20);
  put16 (l4, 40000);
  put16 (l4 + 2, 443);
  CHECK (bpa_l4_read (ip, pkt + 40, BPA_PROTO_TCP, 0, &port, &l4h) == 0
	   && port == 40000 && l4h == l4,
	 "TCP's source port read as %u", port);
  CHECK (bpa_l4_read (ip, pkt + 40, BPA_PROTO_TCP, 1, &port, &l4h) == 0
	   && port == 443,
	 "TCP's destination port read as %u", port);
  CHECK (bpa_l4_read (ip, pkt + 40, BPA_PROTO_UDP, 1, &port, &l4h) == 0
	   && port == 443,
	 "UDP's destination port read as %u", port);

  /* An IP header with options: the id is past them, not at byte 20. */
  l4 = mk (IP_PROTOCOL_UDP, 6, 8);
  put16 (l4, 5060);
  put16 (l4 + 2, 1024);
  CHECK (bpa_l4_read (ip, pkt + 32, BPA_PROTO_UDP, 0, &port, &l4h) == 0
	   && port == 5060 && l4h == l4,
	 "behind 4 bytes of IP options the port read as %u", port);

  /* The bound: the first 8 bytes of the L4 header must be in the buffer. */
  l4 = mk (IP_PROTOCOL_UDP, 5, 8);
  CHECK (bpa_l4_read (ip, pkt + 28, BPA_PROTO_UDP, 0, &port, &l4h) == 0,
	 "an L4 header ending exactly at the buffer's end was refused");
  CHECK (bpa_l4_read (ip, pkt + 27, BPA_PROTO_UDP, 0, &port, &l4h) == -1,
	 "an L4 header one byte short of the buffer was read past its end");

  /* ICMP: echo request and reply carry the identifier; nothing else does. */
  l4 = mk (IP_PROTOCOL_ICMP, 5, 8);
  l4[0] = ICMP4_echo_request;
  put16 (l4 + 4, 0x1234);
  CHECK (bpa_l4_read (ip, pkt + 28, BPA_PROTO_ICMP, 0, &port, &l4h) == 0
	   && port == 0x1234,
	 "an echo request's identifier read as 0x%04x", port);
  l4[0] = ICMP4_echo_reply;
  CHECK (bpa_l4_read (ip, pkt + 28, BPA_PROTO_ICMP, 1, &port, &l4h) == 0
	   && port == 0x1234,
	 "an echo reply's identifier read from somewhere else: 0x%04x", port);
  l4[0] = ICMP4_destination_unreachable;
  CHECK (bpa_l4_read (ip, pkt + 28, BPA_PROTO_ICMP, 0, &port, &l4h) == -1,
	 "an ICMP error was keyed on bytes of its quoted datagram");

  /* GRE: PPTP's Call ID, and nothing that is not PPTP's GRE. */
  l4 = mk (IP_PROTOCOL_GRE, 5, 8);
  l4[0] = 0x20;
  l4[1] = 0x01;
  put16 (l4 + 2, 0x880b);
  put16 (l4 + 6, 777);
  CHECK (bpa_l4_read (ip, pkt + 28, BPA_PROTO_GRE, 0, &port, &l4h) == 0
	   && port == 777,
	 "PPTP's Call ID read as %u", port);
  l4[1] = 0x00;
  CHECK (bpa_l4_read (ip, pkt + 28, BPA_PROTO_GRE, 0, &port, &l4h) == -1,
	 "plain GRE was translated as if it had a Call ID");

  /* ESP: no id, no header to touch, and no bound to fail. */
  mk (IP_PROTOCOL_IPSEC_ESP, 5, 0);
  port = 99;
  l4h = pkt;
  CHECK (bpa_l4_read (ip, pkt + 20, BPA_PROTO_ESP, 0, &port, &l4h) == 0
	   && port == 0 && l4h == 0,
	 "ESP came out with id %u and an L4 header to rewrite", port);

  printf ("\n=== bpa_l4_reass: a fragment's id, from reassembly ===\n");
  {
    u16 src = clib_host_to_net_u16 (12345), dst = clib_host_to_net_u16 (53);

    l4h = pkt;
    CHECK (bpa_l4_reass (BPA_PROTO_UDP, 0, src, dst, &port, &l4h) == 0
	     && port == 12345 && l4h == 0,
	   "a fragment's source port read as %u (14640 is the byte swap: "
	   "a phantom session per fragment)", port);
    CHECK (bpa_l4_reass (BPA_PROTO_UDP, 1, src, dst, &port, &l4h) == 0
	     && port == 53,
	   "a fragment's destination port read as %u", port);
    CHECK (bpa_l4_reass (BPA_PROTO_GRE, 0, src, dst, &port, &l4h) == -1,
	   "a GRE fragment was keyed on fields reassembly never filled");
  }

  printf ("\n=== bpa_rewrite_dst: every checksum a receiver checks ===\n");
  /* TCP, with a payload so the segment is more than its header. */
  l4 = mk (IP_PROTOCOL_TCP, 5, 32);
  put16 (l4, 443);
  put16 (l4 + 2, 1024);
  l4[12] = 0x50;
  set_ip_csum (ip);
  ((tcp_header_t *) l4)->checksum = 0;
  ((tcp_header_t *) l4)->checksum = l4_csum (ip, l4, 32);
  CHECK (ip_ok (ip) && l4_ok (ip, l4, 32), "the TCP packet was built wrong");
  bpa_rewrite_dst (ip, l4, BPA_PROTO_TCP, PRIV, 40000);
  CHECK (clib_net_to_host_u32 (ip->dst_address.as_u32) == PRIV
	   && clib_net_to_host_u16 (((tcp_header_t *) l4)->dst_port) == 40000,
	 "TCP was not sent to the subscriber's socket");
  CHECK (ip_ok (ip), "TCP: the IP checksum is wrong after the rewrite");
  CHECK (l4_ok (ip, l4, 32),
	 "TCP: the segment's checksum is wrong after the rewrite - the "
	 "subscriber's stack drops every returning segment");

  /* UDP with a checksum, and UDP without one. */
  l4 = mk (IP_PROTOCOL_UDP, 5, 20);
  put16 (l4, 53);
  put16 (l4 + 2, 1030);
  put16 (l4 + 4, 20);
  set_ip_csum (ip);
  ((udp_header_t *) l4)->checksum = 0;
  ((udp_header_t *) l4)->checksum = l4_csum (ip, l4, 20);
  CHECK (l4_ok (ip, l4, 20), "the UDP packet was built wrong");
  bpa_rewrite_dst (ip, l4, BPA_PROTO_UDP, PRIV, 33333);
  CHECK (ip_ok (ip) && l4_ok (ip, l4, 20)
	   && clib_net_to_host_u16 (((udp_header_t *) l4)->dst_port) == 33333,
	 "UDP: a checksum or the port is wrong after the rewrite");
  l4 = mk (IP_PROTOCOL_UDP, 5, 20);
  put16 (l4 + 2, 1030);
  ((udp_header_t *) l4)->checksum = 0;
  set_ip_csum (ip);
  bpa_rewrite_dst (ip, l4, BPA_PROTO_UDP, PRIV, 33333);
  CHECK (((udp_header_t *) l4)->checksum == 0 && ip_ok (ip),
	 "a UDP datagram that arrived with no checksum left with one");

  /* ICMP echo: the identifier, which is in the ICMP checksum; no address. */
  l4 = mk (IP_PROTOCOL_ICMP, 5, 16);
  l4[0] = ICMP4_echo_reply;
  put16 (l4 + 4, 2000);
  set_ip_csum (ip);
  put16 (l4 + 2, 0);
  put16 (l4 + 2, (u16) ~fold (sum_bytes (l4, 16, 0)));
  bpa_rewrite_dst (ip, l4, BPA_PROTO_ICMP, PRIV, 0x4142);
  CHECK (ip_ok (ip), "ICMP: the IP checksum is wrong after the rewrite");
  CHECK (fold (sum_bytes (l4, 16, 0)) == 0xffff
	   && ((u32) l4[4] << 8 | l4[5]) == 0x4142,
	 "ICMP: the echo's checksum or identifier is wrong after the rewrite");

  /* GRE: the Call ID is a plain store; only the IP checksum moves. */
  l4 = mk (IP_PROTOCOL_GRE, 5, 8);
  l4[0] = 0x20;
  l4[1] = 0x01;
  put16 (l4 + 2, 0x880b);
  put16 (l4 + 6, 900);
  set_ip_csum (ip);
  bpa_rewrite_dst (ip, l4, BPA_PROTO_GRE, PRIV, 45);
  CHECK (ip_ok (ip) && bpa_gre_call_id (l4) == 45
	   && ((u32) l4[2] << 8 | l4[3]) == 0x880b,
	 "GRE: the Call ID, the protocol or the IP checksum is wrong");

  /* No L4 header (a fragment, ESP): the address alone, the bytes after the
   * IP header untouched. */
  {
    u8 before[20];

    l4 = mk (IP_PROTOCOL_UDP, 5, 20);
    set_ip_csum (ip);
    memcpy (before, l4, 20);
    bpa_rewrite_dst (ip, 0, BPA_PROTO_UDP, PRIV, 1);
    CHECK (ip_ok (ip) && clib_net_to_host_u32 (ip->dst_address.as_u32) == PRIV
	     && memcmp (before, l4, 20) == 0,
	   "a packet with no L4 header had bytes past its IP header written");
  }

  /* One-to-one: the address does not change, and the IP checksum must not
   * either. */
  {
    u16 csum;

    l4 = mk (IP_PROTOCOL_TCP, 5, 20);
    put16 (l4 + 2, 80);
    l4[12] = 0x50;
    set_ip_csum (ip);
    ((tcp_header_t *) l4)->checksum = 0;
    ((tcp_header_t *) l4)->checksum = l4_csum (ip, l4, 20);
    csum = ip->checksum;
    bpa_rewrite_dst (ip, l4, BPA_PROTO_TCP, PUB, 8080);
    CHECK (ip->checksum == csum && ip_ok (ip) && l4_ok (ip, l4, 20),
	   "a rewrite to the same address changed the IP checksum");
  }

  printf ("\n=== the payload an ALG may reach into ===\n");
  {
    static vlib_buffer_t b;
    ip4_header_t *bip = (ip4_header_t *) b.data;
    tcp_header_t *tcp = (tcp_header_t *) (b.data + 20);
    udp_header_t *udp = (udp_header_t *) (b.data + 20);
    u8 *pl = 0;
    u32 pl_len = 0;

    memset (&b, 0, sizeof (b));
    bip->ip_version_and_header_length = 0x45;
    bip->length = clib_host_to_net_u16 (20 + 20 + 30);
    tcp->data_offset_and_reserved = 0x50;
    b.current_length = 70;
    CHECK (bpa_tcp_payload (&b, bip, tcp, &pl, &pl_len) == 0
	     && pl == b.data + 40 && pl_len == 30,
	   "bpa_tcp_payload: %u bytes at +%d, not 30 at +40", pl_len,
	   (int) (pl - b.data));
    tcp->data_offset_and_reserved = 0x80; /* 12 bytes of options */
    CHECK (bpa_tcp_payload (&b, bip, tcp, &pl, &pl_len) == 0
	     && pl == b.data + 52 && pl_len == 18,
	   "bpa_tcp_payload: the options were read as payload");
    b.current_length = 69;
    CHECK (bpa_tcp_payload (&b, bip, tcp, &pl, &pl_len) == -1,
	   "bpa_tcp_payload: a length the buffer does not hold was trusted");
    b.current_length = 70;
    tcp->data_offset_and_reserved = 0x40;
    CHECK (bpa_tcp_payload (&b, bip, tcp, &pl, &pl_len) == -1,
	   "bpa_tcp_payload: a TCP header under 20 bytes was accepted");
    tcp->data_offset_and_reserved = 0x50;
    bip->length = clib_host_to_net_u16 (40);
    CHECK (bpa_tcp_payload (&b, bip, tcp, &pl, &pl_len) == -1,
	   "bpa_tcp_payload: a bare ACK was given a payload");

    memset (&b, 0, sizeof (b));
    bip->ip_version_and_header_length = 0x45;
    bip->length = clib_host_to_net_u16 (20 + 8 + 100);
    udp->length = clib_host_to_net_u16 (8 + 100);
    b.current_length = 128;
    CHECK (bpa_alg_udp_payload (&b, bip, udp, &pl, &pl_len) == 0
	     && pl == b.data + 28 && pl_len == 100,
	   "bpa_alg_udp_payload: %u bytes, not 100", pl_len);
    udp->length = clib_host_to_net_u16 (8 + 200);
    CHECK (bpa_alg_udp_payload (&b, bip, udp, &pl, &pl_len) == -1,
	   "bpa_alg_udp_payload: a UDP length beyond the IP length was read "
	   "- the SIP parser is reachable from the internet");
    udp->length = clib_host_to_net_u16 (8 + 100);
    b.current_length = 127;
    CHECK (bpa_alg_udp_payload (&b, bip, udp, &pl, &pl_len) == -1,
	   "bpa_alg_udp_payload: a datagram past the buffer was read");
    b.current_length = 128;
    udp->length = clib_host_to_net_u16 (8);
    bip->length = clib_host_to_net_u16 (28);
    CHECK (bpa_alg_udp_payload (&b, bip, udp, &pl, &pl_len) == -1,
	   "bpa_alg_udp_payload: an empty datagram was given a payload");

    CHECK (bpa_udp_csum_on_wire (0) == 0xffff
	     && bpa_udp_csum_on_wire (0x1234) == 0x1234,
	   "bpa_udp_csum_on_wire: a computed zero went out as 'no checksum'");
  }

  return bpa_test_verdict (checks, failures);
}
