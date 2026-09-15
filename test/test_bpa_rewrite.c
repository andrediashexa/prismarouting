/*
 * test_bpa_rewrite.c - the outbound rewrite's checksum arithmetic.
 *
 * This exists because the optimisation it guards has already been written
 * once, measured at a 28% saving, and shipped wrong (#63). Three ALG labs
 * caught it with ipsum=BAD tcpsum=BAD; nothing cheaper did, because there was
 * nothing cheaper. A lab that needs a container, a packet generator and a
 * capture to say "this checksum is wrong" is a lab that runs at the end of a
 * change rather than during it, and a checksum bug that survives to the end of
 * a change is a checksum bug that gets argued about instead of printed.
 *
 * So every assertion here compares against a FULL recomputation over the
 * translated packet, written independently below, and most of them go one
 * step further and verify the way a RECEIVER does - summing the segment with
 * its checksum field still in it and requiring all ones. That is the exact
 * question `ipsum=BAD` answers, and it has no opinion about which of the two
 * spellings of zero the sum came out as.
 *
 * The four things a lab structurally cannot enumerate, and this file does:
 *
 *   - the sign. VPP's ip_csum_add_even() SUBTRACTS from a checksum, because
 *     it is named for what happens to the data. Applying a precomputed delta
 *     with it is the bug that was reverted, and it is asserted here directly
 *     rather than inferred from a failing capture.
 *   - the three shapes of correction. The IPv4 header checksum takes the
 *     address change; TCP and UDP take the address AND the port, through the
 *     pseudo-header; ICMP takes neither address nor port but the echo
 *     identifier, because it has no pseudo-header at all. One delta for all
 *     three is wrong in two different directions at once.
 *   - a UDP datagram that arrived with no checksum, which must leave with
 *     none. One packet in a capture proves nothing about the branch.
 *   - a one-to-one pool, where the public address IS the private one and the
 *     correction must be a true no-op.
 */

#include <stdio.h>
#include <string.h>
#include "bpa_tcp_shim.h"
#include "../src/plugins/bpa/bpa_rewrite.h"

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
      else                                                                    \
	{                                                                     \
	  printf ("  [ ok ] ");                                               \
	  printf (__VA_ARGS__);                                               \
	  printf ("\n");                                                      \
	}                                                                     \
    }                                                                         \
  while (0)

/* ------------------------------------------------ independent arithmetic */

/*
 * A plain one's complement sum over bytes. Deliberately not written with any
 * of the helpers under test: a test that reuses the arithmetic it is checking
 * asserts that the code agrees with itself.
 */
static u16
ones_sum (const u8 *p, u32 n, u32 seed)
{
  u32 s = seed, i;

  for (i = 0; i + 1 < n; i += 2)
    s += ((u32) p[i] << 8) | p[i + 1];
  if (i < n)
    s += (u32) p[i] << 8;
  while (s >> 16)
    s = (s & 0xffff) + (s >> 16);
  return (u16) s;
}

/* A packet: 20 bytes of IPv4 header and whatever follows, network order. */
typedef struct
{
  u8 b[80];
  u32 l4len;
  u8 proto;
} pkt_t;

static ip4_header_t *
ip_of (pkt_t *p)
{
  return (ip4_header_t *) p->b;
}
static void *
l4_of (pkt_t *p)
{
  return p->b + 20;
}

/* The pseudo-header of RFC 793: the two addresses as they now stand in the
 * header, the protocol, and the L4 length. */
static u32
pseudo (pkt_t *p)
{
  return (u32) ones_sum (p->b + 12, 8, 0) + p->proto + p->l4len;
}

/*
 * What a receiver does: sum the whole thing WITH its checksum still in place
 * and require all ones. Answers exactly the question `ipsum=BAD` asks, and is
 * indifferent to which spelling of zero the sender folded to.
 */
static int
ip_verifies (pkt_t *p)
{
  return ones_sum (p->b, 20, 0) == 0xffff;
}

static int
l4_verifies (pkt_t *p)
{
  if (p->proto == IP_PROTOCOL_ICMP)
    return ones_sum (p->b + 20, p->l4len, 0) == 0xffff;
  return ones_sum (p->b + 20, p->l4len, pseudo (p)) == 0xffff;
}

/* Seals a freshly built packet so it is one a receiver would accept. */
static void
seal (pkt_t *p)
{
  u8 *l4 = p->b + 20;

  p->b[10] = p->b[11] = 0;
  *(u16 *) (p->b + 10) = clib_host_to_net_u16 ((u16) ~ones_sum (p->b, 20, 0));

  if (p->proto == IP_PROTOCOL_TCP)
    {
      l4[16] = l4[17] = 0;
      *(u16 *) (l4 + 16) =
	clib_host_to_net_u16 ((u16) ~ones_sum (l4, p->l4len, pseudo (p)));
    }
  else if (p->proto == IP_PROTOCOL_UDP)
    {
      l4[6] = l4[7] = 0;
      *(u16 *) (l4 + 6) =
	clib_host_to_net_u16 ((u16) ~ones_sum (l4, p->l4len, pseudo (p)));
      if (*(u16 *) (l4 + 6) == 0)
	*(u16 *) (l4 + 6) = 0xffff; /* RFC 768: zero means "not computed" */
    }
  else if (p->proto == IP_PROTOCOL_ICMP)
    {
      l4[2] = l4[3] = 0;
      *(u16 *) (l4 + 2) =
	clib_host_to_net_u16 ((u16) ~ones_sum (l4, p->l4len, 0));
    }
}

/* ------------------------------------------------------ packet builders */

static u64 rs = 88172645463325252ull;
static u32
rnd (void)
{
  rs ^= rs << 13;
  rs ^= rs >> 7;
  rs ^= rs << 17;
  return (u32) rs;
}

static pkt_t
build (u8 proto, u32 src, u16 sport, u32 dst, u16 dport, u32 l4len)
{
  pkt_t p;
  u32 i;

  memset (&p, 0, sizeof p);
  p.proto = proto;
  p.l4len = l4len;
  for (i = 20; i < 20 + l4len; i++)
    p.b[i] = (u8) rnd ();

  p.b[0] = 0x45;
  *(u16 *) (p.b + 2) = clib_host_to_net_u16 ((u16) (20 + l4len));
  p.b[8] = 64;
  p.b[9] = proto;
  *(u32 *) (p.b + 12) = clib_host_to_net_u32 (src);
  *(u32 *) (p.b + 16) = clib_host_to_net_u32 (dst);

  if (proto == IP_PROTOCOL_ICMP)
    {
      p.b[20] = 8; /* echo request */
      p.b[21] = 0;
      *(u16 *) (p.b + 24) = clib_host_to_net_u16 (sport); /* identifier */
    }
  else if (proto == IP_PROTOCOL_GRE)
    {
      p.b[20] = 0x20; /* K set, C and R clear */
      p.b[21] = 0x01; /* version 1 */
      *(u16 *) (p.b + 22) = clib_host_to_net_u16 (0x880b);
      *(u16 *) (p.b + 26) = clib_host_to_net_u16 (sport); /* Call ID */
    }
  else
    {
      *(u16 *) (p.b + 20) = clib_host_to_net_u16 (sport);
      *(u16 *) (p.b + 22) = clib_host_to_net_u16 (dport);
      if (proto == IP_PROTOCOL_UDP)
	*(u16 *) (p.b + 24) = clib_host_to_net_u16 ((u16) l4len);
      else
	p.b[32] = (u8) ((l4len / 4) << 4);
    }

  seal (&p);
  return p;
}

/* bpa_proto_t from the IP protocol number, the way bpa_l4_id() decides it. */
static u8
bpa_proto_of (u8 ip_proto)
{
  switch (ip_proto)
    {
    case IP_PROTOCOL_TCP:
      return BPA_PROTO_TCP;
    case IP_PROTOCOL_UDP:
      return BPA_PROTO_UDP;
    case IP_PROTOCOL_ICMP:
      return BPA_PROTO_ICMP;
    case IP_PROTOCOL_GRE:
      return BPA_PROTO_GRE;
    default:
      return BPA_PROTO_ESP;
    }
}

/*
 * Translates the way the node does: the corrections come off a session that
 * was built from (ip_priv, port_priv) -> (ip_pub, port_pub), exactly as
 * bpa_session_csum_deltas() builds them at session creation.
 *
 * `have_l4` is 0 for the cases the node passes l4h == 0 for: a non-first
 * fragment, whose ports and L4 checksum live in the first one, and ESP.
 */
static void
translate (pkt_t *p, u32 ip_priv, u16 port_priv, u32 ip_pub, u16 port_pub,
	   int have_l4)
{
  bpa_session_t s;
  u8 bproto = bpa_proto_of (p->proto);

  memset (&s, 0, sizeof s);
  s.ip_priv = ip_priv;
  s.ip_pub = ip_pub;
  s.port_priv = port_priv;
  s.port_pub = port_pub;
  s.proto = bproto;
  bpa_session_csum_deltas (&s);

  bpa_rewrite_src (ip_of (p), have_l4 ? l4_of (p) : 0, bproto, s.ip_pub,
		   s.port_pub, s.csum_d_addr, s.csum_d_l4);
}

static u16
sport_of (pkt_t *p)
{
  if (p->proto == IP_PROTOCOL_ICMP)
    return clib_net_to_host_u16 (*(u16 *) (p->b + 24));
  if (p->proto == IP_PROTOCOL_GRE)
    return clib_net_to_host_u16 (*(u16 *) (p->b + 26));
  return clib_net_to_host_u16 (*(u16 *) (p->b + 20));
}

int
main (void)
{
  const u32 PRIV = 0x0a0a0a0a;	/* 10.10.10.10 */
  const u32 PUB = 0xc8d80102;	/* 200.216.1.2 */
  const u32 PEER = 0x08080808;	/* 8.8.8.8 */

  printf ("--- the sign: VPP's ip_csum_add_even SUBTRACTS ---\n");
  /*
   * The revert, in three lines. A delta applied with ip_csum_add_even lands
   * with the wrong sign and misses by exactly twice itself, which is why the
   * failing capture showed a checksum that was neither right nor random.
   */
  {
    ip_csum_t c = 0xa225, d = 0x1234;
    CHECK (ip_csum_fold (bpa_csum_add (c, (u16) d)) == 0xb459,
	   "bpa_csum_add adds: 0xa225 + 0x1234 = 0x%04x",
	   ip_csum_fold (bpa_csum_add (c, (u16) d)));
    CHECK (ip_csum_fold (ip_csum_add_even (c, d)) == 0x8ff1,
	   "ip_csum_add_even subtracts: 0xa225 - 0x1234 = 0x%04x",
	   ip_csum_fold (ip_csum_add_even (c, d)));
    CHECK (ip_csum_fold (ip_csum_add_even (c, d))
	     != ip_csum_fold (bpa_csum_add (c, (u16) d)),
	   "so the two are not interchangeable, which is the whole of #63");
  }

  printf ("\n--- TCP: a SYN, the packet the revert was caught on ---\n");
  {
    pkt_t p = build (IP_PROTOCOL_TCP, PRIV, 51000, PEER, 443, 20);
    u16 before_ip = clib_net_to_host_u16 (*(u16 *) (p.b + 10));

    p.b[33] = TCP_FLAG_SYN;
    seal (&p);
    CHECK (ip_verifies (&p) && l4_verifies (&p),
	   "the untranslated SYN verifies before anything touches it");

    translate (&p, PRIV, 51000, PUB, 20000, 1);
    CHECK (ip_verifies (&p), "the translated SYN's IP checksum verifies");
    CHECK (l4_verifies (&p), "the translated SYN's TCP checksum verifies");
    CHECK (clib_net_to_host_u32 (*(u32 *) (p.b + 12)) == PUB,
	   "and it left carrying the public address");
    CHECK (sport_of (&p) == 20000, "and the public port");
    CHECK (clib_net_to_host_u16 (*(u16 *) (p.b + 10)) != before_ip,
	   "the IP checksum actually moved");
  }

  printf ("\n--- TCP: the address changes and the port does not ---\n");
  {
    pkt_t p = build (IP_PROTOCOL_TCP, PRIV, 4000, PEER, 80, 32);
    translate (&p, PRIV, 4000, PUB, 4000, 1);
    CHECK (ip_verifies (&p) && l4_verifies (&p),
	   "both checksums verify when only the address moved");
  }

  printf ("\n--- TCP: the port changes and the address does not ---\n");
  {
    /*
     * A one-to-one pool. The address correction folds to exactly zero, so the
     * rewrite needs no "did the address change" branch - and this is the
     * check that says so, because a correction that folded to 0xffff instead
     * would shift every IP checksum by one and still look like a no-op to
     * anyone reading the code.
     */
    pkt_t p = build (IP_PROTOCOL_TCP, PRIV, 4000, PEER, 80, 20);
    u16 before;
    u32 w;
    int found = 0;
    bpa_session_t s;

    memset (&s, 0, sizeof s);
    s.ip_priv = s.ip_pub = PRIV;
    s.port_priv = 4000;
    s.port_pub = 40000;
    s.proto = BPA_PROTO_TCP;
    bpa_session_csum_deltas (&s);
    CHECK (s.csum_d_addr == 0,
	   "a public address equal to the private one folds to a zero "
	   "correction, not to 0xffff");

    /*
     * On a header whose checksum is ZERO, which is a legal IPv4 header and
     * the only place the two spellings of zero can be told apart: 0xffff is
     * an additive identity for every other value, so a correction that folded
     * to it instead of to 0x0000 would look like a working no-op on every
     * packet but this one. The fragment id is searched for a header that
     * seals that way.
     */
    for (w = 0; w < 0x10000 && !found; w++)
      {
	*(u16 *) (p.b + 4) = (u16) w;
	seal (&p);
	found = *(u16 *) (p.b + 10) == 0;
      }
    CHECK (found, "an IPv4 header whose checksum is 0x0000 exists");
    before = *(u16 *) (p.b + 10);

    translate (&p, PRIV, 4000, PRIV, 40000, 1);
    CHECK (*(u16 *) (p.b + 10) == before,
	   "so a zero IP checksum is left byte for byte alone, not turned "
	   "into 0xffff: 0x%04x",
	   *(u16 *) (p.b + 10));
    CHECK (ip_verifies (&p), "and it still verifies");
    CHECK (l4_verifies (&p), "and the TCP checksum verifies");
  }

  printf ("\n--- UDP ---\n");
  {
    pkt_t p = build (IP_PROTOCOL_UDP, PRIV, 5060, PEER, 5060, 16);
    translate (&p, PRIV, 5060, PUB, 30000, 1);
    CHECK (ip_verifies (&p), "a translated datagram's IP checksum verifies");
    CHECK (l4_verifies (&p), "and its UDP checksum verifies");
  }
  {
    /*
     * RFC 768: zero on the wire means "not computed", so a datagram that
     * arrived without a checksum must leave without one. Adding a correction
     * to it would produce a number that looks computed and is not.
     */
    pkt_t p = build (IP_PROTOCOL_UDP, PRIV, 5060, PEER, 5060, 16);
    *(u16 *) (p.b + 26) = 0;
    p.b[10] = p.b[11] = 0;
    *(u16 *) (p.b + 10) = clib_host_to_net_u16 ((u16) ~ones_sum (p.b, 20, 0));

    translate (&p, PRIV, 5060, PUB, 30000, 1);
    CHECK (*(u16 *) (p.b + 26) == 0,
	   "a UDP datagram that arrived with no checksum leaves with none");
    CHECK (ip_verifies (&p), "and its IP checksum is still repaired");
    CHECK (sport_of (&p) == 30000, "and its source port is still rewritten");
  }

  printf ("\n--- UDP: the datagram the per-packet form got wrong ---\n");
  {
    /*
     * Not a regression guard for this code - a bug in the code this replaces.
     *
     * The per-packet form folded a 64-bit accumulator, and an accumulator can
     * land on exactly zero: stored + old_addr + old_port == new_addr +
     * new_port as plain integers. It then wrote 0x0000, which on a UDP
     * datagram is not a checksum at all but "not computed", telling the
     * receiver to skip the check on a datagram that was just rewritten.
     *
     * The case is built rather than hoped for. A stored checksum of 0x0001
     * and a public address one greater than the private one is the smallest
     * shape that reaches it, and the payload is searched for one that seals
     * to that checksum.
     *
     * This form cannot land there. It folds `stored + delta`, two sixteen-bit
     * values, which is zero only when both are - and the branch it is in has
     * already established that `stored` is not.
     */
    const u32 priv = 0x0a0a0a0a;      /* 10.10.10.10, 0x0a0a0a0a on the wire */
    const u32 pub = 0x0b0a0a0a;       /* 11.10.10.10, 0x0a0a0a0b on the wire */
    pkt_t p = build (IP_PROTOCOL_UDP, priv, 5060, PEER, 5060, 16);
    u32 w;
    int found = 0;

    /*
     * 0x0001 as the WORD VPP reads, not as the value on the wire: every
     * operand in this arithmetic is a raw sixteen- or thirty-two-bit read of
     * a network-order field, and the fold is byte-swap invariant, so the
     * whole calculation lives in that swapped space.
     */
    for (w = 0; w < 0x10000 && !found; w++)
      {
	*(u16 *) (p.b + 28) = (u16) w;
	seal (&p);
	found = *(u16 *) (p.b + 26) == 0x0001;
      }
    CHECK (found, "a datagram whose UDP checksum seals to 0x0001 exists");

    translate (&p, priv, 5060, pub, 5060, 1);
    CHECK (*(u16 *) (p.b + 26) != 0,
	   "a computed UDP checksum of zero leaves as 0xffff, not as wire "
	   "zero: the word is 0x%04x",
	   *(u16 *) (p.b + 26));
    CHECK (l4_verifies (&p), "and it verifies");
    CHECK (ip_verifies (&p), "and so does the IP header");
  }

  printf ("\n--- ICMP echo: no pseudo-header, so no address in the sum ---\n");
  {
    /*
     * The correction for an ICMP echo covers the identifier and NOTHING else.
     * Feeding it the address delta as well - the obvious thing to do if one
     * delta is built for the whole session - moves a checksum that was
     * already right.
     */
    pkt_t p = build (IP_PROTOCOL_ICMP, PRIV, 1234, PEER, 0, 16);
    bpa_session_t s;

    memset (&s, 0, sizeof s);
    s.ip_priv = PRIV;
    s.ip_pub = PUB;
    s.port_priv = 1234;
    s.port_pub = 4321;
    s.proto = BPA_PROTO_ICMP;
    bpa_session_csum_deltas (&s);
    {
      u16 id_only, unused;
      bpa_csum_deltas (PRIV, PRIV, 1234, 4321, BPA_PROTO_ICMP, &unused,
		       &id_only);
      CHECK (s.csum_d_l4 == id_only,
	     "the ICMP correction is the identifier's alone: 0x%04x with the "
	     "address changing, 0x%04x without",
	     s.csum_d_l4, id_only);
    }

    translate (&p, PRIV, 1234, PUB, 4321, 1);
    CHECK (ip_verifies (&p), "the translated echo's IP checksum verifies");
    CHECK (l4_verifies (&p), "and its ICMP checksum verifies");
    CHECK (sport_of (&p) == 4321, "and the identifier was replaced");
  }

  printf ("\n--- the corrections are not interchangeable ---\n");
  {
    /*
     * Both halves of the mistake, in one place. The IP header checksum covers
     * no ports, so the L4 correction is too big for it; the TCP checksum
     * covers the pseudo-header, so the address correction alone is too small.
     */
    u16 d_addr, d_l4;
    bpa_csum_deltas (PRIV, PUB, 51000, 20000, BPA_PROTO_TCP, &d_addr, &d_l4);
    CHECK (d_addr != d_l4,
	   "a TCP session's two corrections differ: 0x%04x and 0x%04x",
	   d_addr, d_l4);

    {
      pkt_t p = build (IP_PROTOCOL_TCP, PRIV, 51000, PEER, 443, 20);
      *(u16 *) (p.b + 10) = clib_host_to_net_u16 (
	ip_csum_fold (bpa_csum_add (clib_net_to_host_u16 (*(u16 *) (p.b + 10)),
				    d_l4)));
      *(u32 *) (p.b + 12) = clib_host_to_net_u32 (PUB);
      CHECK (!ip_verifies (&p),
	     "the L4 correction on the IP header leaves it BAD");
    }
    {
      pkt_t p = build (IP_PROTOCOL_TCP, PRIV, 51000, PEER, 443, 20);
      *(u16 *) (p.b + 36) = clib_host_to_net_u16 (
	ip_csum_fold (bpa_csum_add (clib_net_to_host_u16 (*(u16 *) (p.b + 36)),
				    d_addr)));
      *(u16 *) (p.b + 20) = clib_host_to_net_u16 (20000);
      *(u32 *) (p.b + 12) = clib_host_to_net_u32 (PUB);
      CHECK (!l4_verifies (&p),
	     "the address correction alone on the TCP header leaves it BAD");
    }
  }

  printf ("\n--- the sign, on a real packet ---\n");
  {
    /*
     * What the revert actually produced. Not a restatement of the arithmetic
     * check above: this is the translated SYN, with the same delta, applied
     * with the primitive whose name reads like it adds.
     */
    pkt_t good = build (IP_PROTOCOL_TCP, PRIV, 51000, PEER, 443, 20);
    pkt_t bad = good;
    u16 d_addr, d_l4;

    bpa_csum_deltas (PRIV, PUB, 51000, 20000, BPA_PROTO_TCP, &d_addr, &d_l4);
    translate (&good, PRIV, 51000, PUB, 20000, 1);

    *(u16 *) (bad.b + 10) = clib_host_to_net_u16 (ip_csum_fold (
      ip_csum_add_even (clib_net_to_host_u16 (*(u16 *) (bad.b + 10)), d_addr)));
    *(u16 *) (bad.b + 36) = clib_host_to_net_u16 (ip_csum_fold (
      ip_csum_add_even (clib_net_to_host_u16 (*(u16 *) (bad.b + 36)), d_l4)));
    *(u16 *) (bad.b + 20) = clib_host_to_net_u16 (20000);
    *(u32 *) (bad.b + 12) = clib_host_to_net_u32 (PUB);

    CHECK (!ip_verifies (&bad) && !l4_verifies (&bad),
	   "applied with add_even the SYN is ipsum=BAD tcpsum=BAD: ip 0x%04x "
	   "where 0x%04x is right",
	   clib_net_to_host_u16 (*(u16 *) (bad.b + 10)),
	   clib_net_to_host_u16 (*(u16 *) (good.b + 10)));
  }

  printf ("\n--- GRE: the address, and not the Call ID ---\n");
  {
    /*
     * Enhanced GRE carries the Call ID the SERVER assigned and expects it
     * back unchanged; the one this box handed out goes in on the return path.
     * Enhanced GRE also has no checksum of its own (RFC 2637 requires the C
     * bit clear), so there is nothing below the IP header to repair.
     */
    pkt_t p = build (IP_PROTOCOL_GRE, PRIV, 0x1234, PEER, 0, 12);
    u8 gre_before[12];

    memcpy (gre_before, p.b + 20, sizeof (gre_before));
    translate (&p, PRIV, 0x1234, PUB, 0x5678, 1);
    CHECK (ip_verifies (&p), "a translated GRE packet's IP checksum verifies");
    CHECK (sport_of (&p) == 0x1234,
	   "and the server's Call ID travelled untouched");
    /*
     * The whole GRE header and not only the Call ID, the way the fragment
     * case below says it. Without the early return this packet falls into
     * the UDP branch, which writes the new port over the flags and version
     * at offset 20 and corrects a checksum field GRE does not have - and the
     * Call ID sits at 26 where the delta for a GRE session folds to zero, so
     * it survives byte for byte and the line above stays green. Measured:
     * the early return deleted, all 41 checks of this file passed.
     */
    CHECK (!memcmp (p.b + 20, gre_before, sizeof (gre_before)),
	   "and so did every other byte below the IP header");
  }

  printf ("\n--- no L4 header: ESP, and a non-first fragment ---\n");
  {
    pkt_t p = build (IP_PROTOCOL_UDP, PRIV, 5060, PEER, 5060, 16);
    u16 l4_before = *(u16 *) (p.b + 26);
    u16 sport_before = *(u16 *) (p.b + 20);

    translate (&p, PRIV, 5060, PUB, 30000, 0);
    CHECK (ip_verifies (&p),
	   "a fragment with no L4 header still has its IP checksum repaired");
    CHECK (*(u16 *) (p.b + 26) == l4_before && *(u16 *) (p.b + 20)
					       == sport_before,
	   "and nothing below the IP header was touched");
  }

  printf ("\n--- 20000 random packets, every checksum verified ---\n");
  {
    /*
     * Volume, because the failure this guards is arithmetic: a form that is
     * right on the handful of addresses a lab happens to use and wrong on a
     * carry it never produces is exactly the shape of bug that reaches a
     * subscriber. One in eleven datagrams arrives with no UDP checksum, and
     * one in ninety-seven is translated onto its own address.
     */
    u8 protos[3] = { IP_PROTOCOL_TCP, IP_PROTOCOL_UDP, IP_PROTOCOL_ICMP };
    u32 n, bad_ip = 0, bad_l4 = 0, zeroed = 0, n_nocsum = 0;

    for (n = 0; n < 20000; n++)
      {
	u8 proto = protos[n % 3];
	u32 priv = rnd (), pub = rnd ();
	u16 pport = (u16) rnd (), ppub = (u16) rnd ();
	u32 l4len = (proto == IP_PROTOCOL_TCP) ? 20 + 4 * (n % 5)
					       : 8 + 4 * (n % 5);
	pkt_t p;
	int had_csum;

	if ((n % 97) == 0)
	  pub = priv;
	p = build (proto, priv, pport, rnd (), (u16) rnd (), l4len);
	if (proto == IP_PROTOCOL_UDP && (n % 11) == 0)
	  {
	    *(u16 *) (p.b + 26) = 0;
	    p.b[10] = p.b[11] = 0;
	    *(u16 *) (p.b + 10) =
	      clib_host_to_net_u16 ((u16) ~ones_sum (p.b, 20, 0));
	    n_nocsum++;
	  }
	had_csum = (proto != IP_PROTOCOL_UDP) || *(u16 *) (p.b + 26) != 0;

	translate (&p, priv, pport, pub, ppub, 1);

	if (!ip_verifies (&p))
	  bad_ip++;
	if (proto == IP_PROTOCOL_UDP && !had_csum)
	  {
	    if (*(u16 *) (p.b + 26) != 0)
	      bad_l4++;
	  }
	else if (!l4_verifies (&p))
	  bad_l4++;
	/*
	 * RFC 768 again, from the other side: a COMPUTED zero would tell the
	 * receiver not to check the thing that was just made correct. This
	 * form cannot produce one - the result is fold(stored + delta) with
	 * both operands sixteen bits, zero only when both are - and this is
	 * the check that would notice if that stopped being true.
	 */
	if (proto == IP_PROTOCOL_UDP && had_csum && *(u16 *) (p.b + 26) == 0)
	  zeroed++;
      }

    CHECK (n_nocsum > 0, "%u of the datagrams arrived with no UDP checksum",
	   n_nocsum);
    CHECK (bad_ip == 0, "every IP checksum verifies (%u did not)", bad_ip);
    CHECK (bad_l4 == 0, "every L4 checksum verifies (%u did not)", bad_l4);
    CHECK (zeroed == 0,
	   "no computed UDP checksum came out as wire zero (%u did)", zeroed);
  }

  return bpa_test_verdict (checks, failures);
}
