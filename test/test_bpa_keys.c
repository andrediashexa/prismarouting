/*
 * test_bpa_keys.c - the session table's keys and a session's two rules
 * (bpa_keys.h, #472).
 *
 * These decide which session a packet belongs to and how long it lives, and
 * none had a unit test: they were static_always_inline in a header only VPP
 * compiles, and the checker did not count headers. What each case asserts is
 * the property a wrong answer would break on a box:
 *
 *   * no key field reaches into another's bits - two subscribers sharing a
 *     key share a mapping, which is one translated onto the other;
 *   * the VRF separates two tenants' identical private addresses, the pool
 *     separates two pools' identical subscriber indices (#57);
 *   * GRE's remote and VRF are both in the second word, apart; so are ESP's;
 *   * the shared table's keys (ESP, PPTP) have no VRF, and its value
 *     round-trips the subscriber and the VRF;
 *   * the timeout a session gets, per protocol and TCP state;
 *   * the TCP state machine, every transition.
 *
 * Mutations shown red: bpa_key_in's port shifted by 24 (overlaps the address);
 * bpa_tcp_next letting an ACK reopen a closing connection.
 */
#include <stdio.h>
#include <string.h>
#include "bpa_shim.h"
#include "bpa_vec_shim.h"
/* The one byte-order helper bpa_esp_spi() needs. bpa_tcp_shim.h has it, and
 * a copy of bpa_proto_t that collides with bpa_pool.h's. Little-endian, like
 * every host this runs on. */
#define clib_net_to_host_u32(x) __builtin_bswap32 (x)
#include "../src/plugins/bpa/bpa_keys.h"

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

static int
same (const clib_bihash_kv_16_8_t *a, const clib_bihash_kv_16_8_t *b)
{
  return a->key[0] == b->key[0] && a->key[1] == b->key[1];
}

int
main (void)
{
  clib_bihash_kv_16_8_t a, b;

  printf ("=== the subscriber record's key: pool and index, apart ===\n");
  CHECK (bpa_sub_key (1, 5) != bpa_sub_key (2, 5),
	 "subscriber 5 of two pools is one record (#57)");
  CHECK (bpa_sub_key (0, 0xffffffffu) != bpa_sub_key (1, 0),
	 "the last index of pool 0 is the first of pool 1");
  CHECK (bpa_sub_key (7, 9) == (((uword) 7 << 32) | 9),
	 "bpa_sub_key is not pool << 32 | index");

  printf ("\n=== the inside key: address, port, protocol and VRF ===\n");
  bpa_key_in (&a, 0x64400005u, 40000, BPA_PROTO_TCP, 0);
  bpa_key_in (&b, 0x64400005u, 40000, BPA_PROTO_TCP, 0);
  CHECK (same (&a, &b), "the same flow keyed twice gave two keys");
  bpa_key_in (&b, 0x64400005u, 40000, BPA_PROTO_TCP, 3);
  CHECK (!same (&a, &b), "one private address in two VRFs is one key");
  bpa_key_in (&b, 0x64400005u, 40000, BPA_PROTO_UDP, 0);
  CHECK (!same (&a, &b), "TCP and UDP from one socket are one key");
  bpa_key_in (&b, 0x64400005u, 40001, BPA_PROTO_TCP, 0);
  CHECK (!same (&a, &b), "two ports are one key");
  /* No field reaches into another's bits: the extremes of each, against the
   * others at zero, all give different words. */
  bpa_key_in (&a, 0xffffffffu, 0, 0, 0);
  bpa_key_in (&b, 0, 0xffff, 0, 0);
  CHECK ((a.key[0] & b.key[0]) == 0, "the address and the port overlap");
  bpa_key_in (&b, 0, 0, 0xff, 0);
  CHECK ((a.key[0] & b.key[0]) == 0, "the address and the protocol overlap");
  bpa_key_in (&a, 0, 0xffff, 0, 0);
  CHECK ((a.key[0] & b.key[0]) == 0, "the port and the protocol overlap");

  printf ("\n=== the outside key: the public socket, no VRF ===\n");
  bpa_key_out (&a, 0xc8000001u, 1024, BPA_PROTO_UDP);
  CHECK (a.key[1] == 0, "the outside key carries a second word");
  bpa_key_out (&b, 0xc8000001u, 1025, BPA_PROTO_UDP);
  CHECK (!same (&a, &b), "two public ports are one outside key");

  printf ("\n=== GRE: the role, the remote and the VRF, apart ===\n");
  bpa_key_gre (&a, 0x64400005u, 77, 0x08080808u, BPA_GRE_KEY_DATA, 0);
  bpa_key_gre (&b, 0x64400005u, 77, 0x08080808u, BPA_GRE_KEY_ALG, 0);
  CHECK (!same (&a, &b), "the ALG's entry and the data entry are one key");
  bpa_key_gre (&b, 0x64400005u, 77, 0x01010101u, BPA_GRE_KEY_DATA, 0);
  CHECK (!same (&a, &b), "two servers choosing one Call ID are one key");
  bpa_key_gre (&b, 0x64400005u, 77, 0x08080808u, BPA_GRE_KEY_DATA, 2);
  CHECK (!same (&a, &b), "one GRE call in two VRFs is one key");
  bpa_key_gre (&a, 0, 0, 0xffffffffu, 0, 0);
  bpa_key_gre (&b, 0, 0, 0, 0, 0xffffffffu);
  CHECK ((a.key[1] & b.key[1]) == 0, "the remote and the VRF overlap");
  bpa_key_in (&b, 0x64400005u, 77, BPA_PROTO_GRE, 0);
  bpa_key_gre (&a, 0x64400005u, 77, 0, BPA_GRE_KEY_DATA, 0);
  CHECK (a.key[0] == b.key[0],
	 "the GRE data key's first word is not the ordinary key's, so the "
	 "worker handoff would read it differently");

  printf ("\n=== ESP: the SPI, the private and the public binding ===\n");
  {
    static const u8 hdr[8] = { 0x12, 0x34, 0x56, 0x78, 0, 0, 0, 1 };
    u32 ip, fib;

    CHECK (bpa_esp_spi (hdr) == 0x12345678u,
	   "the SPI read as 0x%08x, not in network order", bpa_esp_spi (hdr));
    bpa_key_esp (&a, 0x64400005u, 0x08080808u, 0);
    bpa_key_esp (&b, 0x64400005u, 0x01010101u, 0);
    CHECK (!same (&a, &b), "one subscriber's two peers are one binding");
    bpa_key_esp (&b, 0x64400005u, 0x08080808u, 4);
    CHECK (!same (&a, &b), "one ESP binding in two VRFs is one key");
    bpa_key_esp (&a, 0, 0xffffffffu, 0);
    bpa_key_esp (&b, 0, 0, 0xffffffffu);
    CHECK ((a.key[1] & b.key[1]) == 0, "the ESP peer and the VRF overlap");
    bpa_key_esp (&a, 0x64400005u, 0x08080808u, 0);
    bpa_key_in (&b, 0x64400005u, 0, BPA_PROTO_ESP, 0);
    CHECK (a.key[0] == b.key[0],
	   "the ESP key's first word is not the ordinary key's shape");
    bpa_key_esp_pub (&a, 0xc8000001u, 0x08080808u);
    CHECK (a.key[1] == 0x08080808u,
	   "the public ESP key carries more than the peer (a VRF?)");
    bpa_key_esp_pub (&b, 0xc8000001u, 0x01010101u);
    CHECK (!same (&a, &b), "two peers of one public address are one key");

    bpa_shared_unpack (bpa_shared_value (0x64400005u, 0xfffffffeu), &ip, &fib);
    CHECK (ip == 0x64400005u && fib == 0xfffffffeu,
	   "the shared value came back as %08x in VRF %u", ip, fib);
    bpa_shared_unpack (bpa_shared_value (0xffffffffu, 0), &ip, &fib);
    CHECK (ip == 0xffffffffu && fib == 0, "a full address leaked into the VRF");
    CHECK (bpa_shared_is (bpa_shared_value (0x0a000005u, 1), 0x0a000005u, 1),
	   "a subscriber does not recognise its own binding");
    CHECK (!bpa_shared_is (bpa_shared_value (0x0a000005u, 2), 0x0a000005u, 1),
	   "one private address in VRF 1 owns the binding VRF 2 made - its "
	   "teardown deletes a working tunnel");
    CHECK (!bpa_shared_is (bpa_shared_value (0x0a000006u, 1), 0x0a000005u, 1),
	   "a neighbour's binding reads as this subscriber's");
  }

  printf ("\n=== PPTP's public GRE key: the server's Call ID, no VRF ===\n");
  bpa_key_gre_pub (&a, 0xc8000001u, 77, 0x08080808u);
  bpa_key_gre_pub (&b, 0xc8000001u, 78, 0x08080808u);
  CHECK (!same (&a, &b), "two Call IDs to one server are one binding");
  bpa_key_gre_pub (&b, 0xc8000001u, 77, 0x01010101u);
  CHECK (!same (&a, &b), "two servers choosing one Call ID are one binding");
  CHECK (a.key[1] == 0x08080808u, "the public GRE key carries a VRF");
  bpa_key_gre (&b, 0xc8000001u, 77, 0x08080808u, BPA_GRE_KEY_DATA, 0);
  CHECK (same (&a, &b), "the public GRE key is not packed as bpa_key_gre()");
  bpa_key_esp_pub (&b, 0xc8000001u, 0x08080808u);
  CHECK (!same (&a, &b), "a GRE binding and an ESP binding share a key");

  printf ("\n=== how long a session lives ===\n");
  {
    bpa_timeouts_t t;

    memset (&t, 0, sizeof (t));
    t.udp = 300; t.icmp = 60; t.tcp_trans = 240; t.tcp_estab = 7440;
    t.tcp_syn = 6; t.gre = 1800; t.alg_expect = 30;
    CHECK (bpa_timeout_for (BPA_PROTO_UDP, 0, 0, &t) == 300, "udp");
    CHECK (bpa_timeout_for (BPA_PROTO_ICMP, 0, 0, &t) == 60, "icmp");
    CHECK (bpa_timeout_for (BPA_PROTO_GRE, 0, 0, &t) == 1800, "gre");
    CHECK (bpa_timeout_for (BPA_PROTO_ESP, 0, 0, &t) == 1800,
	   "ESP does not share GRE's tunnel timeout");
    CHECK (bpa_timeout_for (BPA_PROTO_UDP, 0, 1, &t) == 30,
	   "an ALG's expectation lives by its protocol, not by alg-expect");
    CHECK (bpa_timeout_for (BPA_PROTO_TCP, BPA_TCP_INIT, 0, &t) == 6,
	   "a handshake that never completed is held past the SYN timeout");
    CHECK (bpa_timeout_for (BPA_PROTO_TCP, BPA_TCP_ESTAB, 0, &t) == 7440,
	   "an established connection is not given the established timeout");
    CHECK (bpa_timeout_for (BPA_PROTO_TCP, BPA_TCP_CLOSING, 0, &t) == 240,
	   "a closing connection is not given the transitory timeout");
    CHECK (bpa_timeout_for (BPA_PROTO_TCP, BPA_TCP_CLOSED, 0, &t) == 0,
	   "a reset connection holds its port");
  }

  printf ("\n=== the TCP state machine, every transition ===\n");
  {
    static const u8 states[] = { BPA_TCP_INIT, BPA_TCP_ESTAB, BPA_TCP_CLOSING,
				 BPA_TCP_CLOSED };
    u32 i;

    for (i = 0; i < 4; i++)
      {
	u8 st = states[i];
	CHECK (bpa_tcp_next (st, BPA_TCP_FLAG_RST) == BPA_TCP_CLOSED,
	       "an RST in state %u did not close", st);
	CHECK (bpa_tcp_next (st, BPA_TCP_FLAG_RST | BPA_TCP_FLAG_ACK)
		 == BPA_TCP_CLOSED,
	       "an RST+ACK in state %u did not close", st);
	CHECK (bpa_tcp_next (st, BPA_TCP_FLAG_FIN | BPA_TCP_FLAG_ACK)
		 == (st == BPA_TCP_CLOSED ? BPA_TCP_CLOSED : BPA_TCP_CLOSING),
	       "a FIN in state %u went to %u", st,
	       bpa_tcp_next (st, BPA_TCP_FLAG_FIN | BPA_TCP_FLAG_ACK));
	CHECK (bpa_tcp_next (st, 0x02 /* SYN */) == st,
	       "a bare SYN moved state %u", st);
      }
    CHECK (bpa_tcp_next (BPA_TCP_INIT, BPA_TCP_FLAG_ACK) == BPA_TCP_ESTAB,
	   "the handshake's ACK did not establish");
    CHECK (bpa_tcp_next (BPA_TCP_ESTAB, BPA_TCP_FLAG_ACK) == BPA_TCP_ESTAB,
	   "an ACK on an established connection moved it");
    CHECK (bpa_tcp_next (BPA_TCP_CLOSING, BPA_TCP_FLAG_ACK) == BPA_TCP_CLOSING,
	   "an ACK reopened a closing connection for the established timeout");
    CHECK (bpa_tcp_next (BPA_TCP_CLOSED, BPA_TCP_FLAG_ACK) == BPA_TCP_CLOSED,
	   "an ACK reopened a reset connection");
  }

  printf ("\n%u checks, %d failures\n", checks, failures);
  printf (failures ? "%d FAILURES\n" : "ALL TESTS PASSED\n", failures);
  return failures ? 1 : 0;
}
