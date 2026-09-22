/*
 * test_bpa_static.c - the arithmetic over a pool's static forwards.
 *
 * WHAT THIS IS AND WHY IT COULD NOT EXIST BEFORE. A forward is configuration
 * laid over the mapping, held in a VPP vector on the pool, and every one of
 * these functions used to take that pool - so a harness with no VPP vectors
 * could not call any of them, and four functions that decide where an
 * operator's forward lands were named by no test at all.
 *
 * They take `(v, n)` now. The vector belongs to the caller, which is the
 * only line in bpa.c that has one; the arithmetic is here, and this file
 * drives it over plain arrays.
 *
 * WHAT IS ASSERTED is the ordering contract the VTY depends on, and it is
 * not an implementation detail: prismash keeps its own copy of a pool's
 * forwards in the order `show bpa config` printed them and diffs both sides
 * of a commit with a stable sort. A position that put a new tie in front of
 * an older one would show the operator a diff on the NEXT commit that
 * nobody asked for.
 */

#include <stdio.h>
#include <string.h>
#include "bpa_shim.h"
#include "../src/plugins/bpa/bpa_pool.h"

/*
 * The two protocol values these functions compare. They are bpa_session.h's
 * enum, which a standalone build does not reach - that header needs VPP - and
 * what matters to everything below is only that TCP sorts before UDP, which
 * is the ORDER the enum gives them and the only property bpa_static_position
 * reads (it compares with <=, never for a particular value).
 */
#define T_TCP 0
#define T_UDP 1

static int failures = 0;
static unsigned checks = 0;

#define CHECK(cond, ...)                                                      \
  do                                                                          \
    {                                                                         \
      checks++;                                                               \
      if (!(cond))                                                            \
	{                                                                     \
	  printf ("  FAIL: ");                                                \
	  printf (__VA_ARGS__);                                               \
	  printf ("\n");                                                      \
	  failures++;                                                         \
	}                                                                     \
    }                                                                         \
  while (0)

/* A vector's worth of forwards, ordered the way the pool keeps them. */
static bpa_static_t V[8];

static void
mk (u32 i, u32 ip_priv, u16 port_pub, u8 proto)
{
  memset (&V[i], 0, sizeof (V[i]));
  V[i].ip_priv = ip_priv;
  V[i].port_pub = port_pub;
  V[i].proto = proto;
}

int
main (void)
{
  bpa_static_t sf;

  printf ("=== the first forward at or after a public port ===\n");
  /* Three ports, each twice: the search has to land on the FIRST of a run,
   * because both callers walk forward from what it returns. */
  mk (0, 0x0a000001, 1000, T_TCP);
  mk (1, 0x0a000002, 1000, T_UDP);
  mk (2, 0x0a000003, 2000, T_TCP);
  mk (3, 0x0a000004, 2000, T_UDP);
  mk (4, 0x0a000005, 3000, T_TCP);
  CHECK (bpa_static_lower (V, 5, 1000) == 0, "1000 is not at 0");
  CHECK (bpa_static_lower (V, 5, 2000) == 2, "2000 is not at 2");
  CHECK (bpa_static_lower (V, 5, 3000) == 4, "3000 is not at 4");
  /* A port nobody forwards: the index it WOULD take, which is what an insert
   * needs and what a search has to stop at. */
  CHECK (bpa_static_lower (V, 5, 1500) == 2, "1500 does not land before 2000");
  CHECK (bpa_static_lower (V, 5, 1) == 0, "a port below every one is not 0");
  CHECK (bpa_static_lower (V, 5, 65535) == 5, "a port above every one is not n");
  CHECK (bpa_static_lower (V, 0, 1000) == 0, "an empty vector is not 0");
  printf ("  [ ok ] %u searches over a vector of five\n", checks);

  printf ("\n=== where a new forward belongs ===\n");
  /* A NEW PORT goes where the search says. */
  mk (5, 0x0a000006, 1500, T_TCP);
  sf = V[5];
  CHECK (bpa_static_position (V, 5, &sf) == 2,
	 "a new port did not land where the search put it");

  /* A TIE GOES AFTER, and this is the assertion the VTY's diff rests on.
   * Same port, same protocol as V[0]: the answer must be 1 and not 0. */
  mk (5, 0x0a000009, 1000, T_TCP);
  sf = V[5];
  CHECK (bpa_static_position (V, 5, &sf) == 1,
	 "a tie on (port, proto) went in FRONT of the one already there: "
	 "the VTY would show a diff on the next commit that nobody asked "
	 "for");

  /* AND THE PROTOCOL ORDERS WITHIN A PORT. A UDP forward on 1000 goes after
   * the TCP one and after the UDP one; a TCP forward goes after the TCP run
   * and BEFORE the UDP. */
  mk (5, 0x0a000009, 1000, T_UDP);
  sf = V[5];
  CHECK (bpa_static_position (V, 5, &sf) == 2,
	 "a UDP tie on 1000 did not go after the UDP already there");
  CHECK (V[0].proto == T_TCP && V[1].proto == T_UDP,
	 "this case no longer arranges TCP before UDP, so it proves nothing");

  /* An empty vector takes the first one at 0. */
  CHECK (bpa_static_position (V, 0, &sf) == 0,
	 "the first forward of an empty pool did not land at 0");
  printf ("  [ ok ] a tie goes after, and the protocol orders within a port\n");

  printf ("\n=== the forward that stands for the block ===\n");
  /* One subscriber with three forwards: the FIRST of them stands for the
   * block, because the block is the subscriber's own. */
  mk (0, 0x0a000001, 1000, T_TCP);
  mk (1, 0x0a000001, 1100, T_TCP);
  mk (2, 0x0a000002, 1200, T_TCP);
  mk (3, 0x0a000001, 1300, T_UDP);
  CHECK (bpa_static_first_in_block (V, 4, &V[0]) == 1,
	 "the subscriber's first forward does not stand for the block");
  CHECK (bpa_static_first_in_block (V, 4, &V[1]) == 0,
	 "a second forward of the same subscriber also claimed the block: "
	 "the block would be announced twice");
  CHECK (bpa_static_first_in_block (V, 4, &V[3]) == 0,
	 "a third one claimed it too");
  /* Another subscriber's only forward stands for its own block. */
  CHECK (bpa_static_first_in_block (V, 4, &V[2]) == 1,
	 "a subscriber with one forward does not stand for its block");
  /* A forward that is not in this vector at all belongs to no block here.
   * The identity is the pointer, not the contents: two forwards can agree
   * on every field and be different rows. */
  sf = V[0];
  CHECK (bpa_static_first_in_block (V, 4, &sf) == 0,
	 "a copy of the first forward claimed the block the original holds");
  printf ("  [ ok ] one forward stands for a block, by identity and not by "
	  "contents\n");

  printf ("\n=== was that the last forward the subscriber had ===\n");
  /* Called AFTER the removal, so what it reads is what is left. The
   * subscriber of V[2] has no other row here. */
  mk (0, 0x0a000001, 1000, T_TCP);
  mk (1, 0x0a000001, 1100, T_TCP);
  sf = V[2];   /* 0x0a000002, removed */
  CHECK (bpa_static_last_in_block (V, 2, &sf) == 1,
	 "a subscriber with nothing left was not read as unpinning its block");
  sf = V[0];
  CHECK (bpa_static_last_in_block (V, 2, &sf) == 0,
	 "a subscriber that still has a forward was read as unpinning its "
	 "block: the block would be released under a live forward");
  CHECK (bpa_static_last_in_block (V, 0, &sf) == 1,
	 "an empty vector did not read as nothing left");
  printf ("  [ ok ] the block is released only when nothing of the "
	  "subscriber's is left\n");

  printf ("\n=== two forwards that cannot both hold ===\n");
  /* One public socket serves one subscriber socket, and one subscriber
   * socket is reached through one public socket. Both directions have to
   * hold, and which one broke is what an operator is told. */
  {
    const bpa_static_t *other = 0;
    bpa_static_t v[3];
    bpa_static_t cand;

    memset (v, 0, sizeof (v));
    /* 10.0.0.1:80 reachable on 203.0.113.16:1000, TCP */
    v[0].ip_priv = 0x0a000001; v[0].port_priv = 80;
    v[0].ip_pub = 0xcb007110;  v[0].port_pub = 1000; v[0].proto = T_TCP;
    /* 10.0.0.2:80 on the SAME public port, a different public address */
    v[1].ip_priv = 0x0a000002; v[1].port_priv = 80;
    v[1].ip_pub = 0xcb007111;  v[1].port_pub = 1000; v[1].proto = T_TCP;

    /* THE SAME PUBLIC SOCKET: taken. */
    cand = v[0];
    cand.ip_priv = 0x0a000009; cand.port_priv = 8080;
    CHECK (bpa_static_clash (v, 2, &cand, &other) == BPA_STATIC_CLASH_PUB,
	   "a second forward of one public socket was allowed");
    CHECK (other == &v[0],
	   "the clash did not name the forward that already holds it, so the "
	   "refusal cannot say who has it");

    /* THE SAME SUBSCRIBER SOCKET, reached through another public port:
     * also refused, because the return direction would be ambiguous. */
    cand = v[0];
    cand.port_pub = 2000; cand.ip_pub = 0xcb007112;
    CHECK (bpa_static_clash (v, 2, &cand, &other) == BPA_STATIC_CLASH_PRIV,
	   "one subscriber socket was given two public sockets");
    CHECK (other == &v[0], "the private clash named the wrong forward");

    /* THE ADDRESS IS PART OF THE PUBLIC KEY (#214). The same port on
     * another public address is another socket, and refusing it made the
     * port unsellable to every subscriber sharing its offset. */
    cand = v[0];
    cand.ip_pub = 0xcb007113; cand.ip_priv = 0x0a00000a; cand.port_priv = 25;
    CHECK (bpa_static_clash (v, 2, &cand, &other) == BPA_STATIC_CLASH_NONE,
	   "the same public port on ANOTHER address was refused: one forward "
	   "would make that port unsellable to every subscriber sharing its "
	   "offset");
    CHECK (other == 0, "a clash that did not happen named a forward anyway");

    /* AND A DIFFERENT PROTOCOL IS A DIFFERENT SOCKET. TCP 1000 says nothing
     * about UDP 1000, in either direction. */
    cand = v[0];
    cand.proto = T_UDP; cand.ip_priv = 0x0a00000b; cand.port_priv = 53;
    CHECK (bpa_static_clash (v, 2, &cand, &other) == BPA_STATIC_CLASH_NONE,
	   "UDP on a port TCP forwards was refused");
    cand = v[0];
    cand.proto = T_UDP; cand.port_pub = 3000; cand.ip_pub = 0xcb007114;
    CHECK (bpa_static_clash (v, 2, &cand, &other) == BPA_STATIC_CLASH_NONE,
	   "the same subscriber socket on another protocol was refused");

    /* An empty pool refuses nothing. */
    CHECK (bpa_static_clash (v, 0, &cand, &other) == BPA_STATIC_CLASH_NONE,
	   "an empty pool found a clash");
  }
  printf ("  [ ok ] both directions are refused, and neither the protocol "
	  "nor the public address is confused with the port\n");

  printf ("\n=== which forward the operator meant ===\n");
  /*
   * A pool has more than one public address, so the same port can be
   * forwarded on several of them, to several subscribers. `no forward tcp
   * 1000` is then a question with more than one answer, and answering it
   * with the first would end a customer's inbound service because another
   * customer shares that port's offset.
   */
  {
    const bpa_static_t *first = 0, *second = 0;
    bpa_static_t v[4];

    memset (v, 0, sizeof (v));
    /* Three forwards on port 1000: two TCP on different public addresses,
     * one UDP. The vector is ordered by public port, which is what the
     * search this walks from depends on. */
    v[0].ip_priv = 0x0a000001; v[0].ip_pub = 0xcb007110;
    v[0].port_pub = 1000; v[0].proto = T_TCP;
    v[1].ip_priv = 0x0a000002; v[1].ip_pub = 0xcb007111;
    v[1].port_pub = 1000; v[1].proto = T_TCP;
    v[2].ip_priv = 0x0a000003; v[2].ip_pub = 0xcb007112;
    v[2].port_pub = 1000; v[2].proto = T_UDP;
    v[3].ip_priv = 0x0a000004; v[3].ip_pub = 0xcb007110;
    v[3].port_pub = 2000; v[3].proto = T_TCP;

    /* ONE MATCH is the operator's forward, handed back. */
    CHECK (bpa_static_on_port (v, 4, 1000, T_UDP, &first, &second) == 1,
	   "one UDP forward on 1000 was not counted as one");
    CHECK (first == &v[2] && second == 0,
	   "the single match was not the UDP row, or a second was named");

    /* TWO IS A REFUSAL, and the caller names both - which is why both come
     * back and not only the count. */
    CHECK (bpa_static_on_port (v, 4, 1000, T_TCP, &first, &second) == 2,
	   "two TCP forwards on one port were not both counted");
    CHECK (first == &v[0] && second == &v[1],
	   "the two named are not the two that hold the port");

    /* NONE IS NOT AN ERROR HERE. The caller that has no forward to remove
     * says so in its own words. */
    CHECK (bpa_static_on_port (v, 4, 3000, T_TCP, &first, &second) == 0,
	   "a port nobody forwards was counted");
    CHECK (first == 0 && second == 0,
	   "a port nobody forwards named a forward anyway");

    /* AND THE RUN STOPS AT THE PORT. A forward on another port must not be
     * counted, whatever else it shares - the scan walks one port's run. */
    CHECK (bpa_static_on_port (v, 4, 2000, T_TCP, &first, &second) == 1,
	   "the run of port 2000 did not stop at its own rows");
    CHECK (first == &v[3], "port 2000 named the wrong forward");
    CHECK (bpa_static_on_port (v, 0, 1000, T_TCP, &first, &second) == 0,
	   "an empty vector had a forward on 1000");
  }
  printf ("  [ ok ] one match is the answer, two are a refusal that can "
	  "name both\n");

  printf ("\n%u check(s)\n", checks);
  if (failures == 0)
    {
      printf ("ALL TESTS PASSED\n");
      return 0;
    }
  printf ("%d FAILURES\n", failures);
  return 1;
}
