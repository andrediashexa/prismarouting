/*
 * test_bpa_icmp.c - the ICMP error parse, and the packets it must refuse.
 *
 * bpa_icmp_err_parse() is the only place in this plugin that walks headers a
 * remote wrote INSIDE another packet. Its accepting half is easy to see from a
 * lab: the translated error comes out of an interface with valid checksums, or
 * it does not. Its refusing half cannot be seen from a lab at all - a packet
 * that is dropped after a bounds check and a packet that is dropped after
 * reading twenty bytes past the buffer look exactly the same from outside, and
 * a malformed ICMP error costs an attacker nothing to send. So the refusals
 * are asserted here instead.
 *
 * Every packet is placed so its LAST byte is the last byte of a mapped page,
 * with an unmapped page right behind it. A parse that reads one byte past the
 * end it was given does not return a wrong answer here - the process dies, and
 * the test fails by disappearing. That is the only way this file can tell the
 * difference between "refused" and "refused after looking".
 *
 * x86_64, like the other standalone tests: a packet that ends exactly at the
 * page boundary starts wherever its length puts it, so the headers are read
 * unaligned. That is what the real thing does to a buffer whose L2 header has
 * been stripped anyway.
 */
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "bpa_tcp_shim.h"
#include "../src/plugins/bpa/bpa_icmp.h"

static int failures = 0;

#define CHECK(cond, ...)                                                      \
  do                                                                          \
    {                                                                         \
      if (cond)                                                               \
	printf ("  ok   " __VA_ARGS__);                                       \
      else                                                                    \
	{                                                                     \
	  printf ("  FAIL " __VA_ARGS__);                                     \
	  failures++;                                                         \
	}                                                                     \
      printf ("\n");                                                          \
    }                                                                         \
  while (0)

/* A header one byte longer or shorter than the product's would move every
 * offset below, and the test would then agree with the shim rather than with
 * the box. */
_Static_assert (sizeof (ip4_header_t) == 20, "ip4_header_t must be 20 bytes");
_Static_assert (sizeof (icmp46_header_t) == 4, "icmp46_header_t must be 4 bytes");

/* Offsets in the 56-byte packet built by mk(), with no options anywhere. */
#define O_OUTER_IHL  0
#define O_ICMP_TYPE  20
#define O_INNER	     28
#define O_INNER_IHL  28
#define O_INNER_PROTO 37
#define O_INNER_SRC  40
#define O_INNER_L4   48
#define PKT_LEN	     56

/*
 * The packet lab/mkicmperr.py builds: a fragmentation-needed from a router,
 * carrying 200.150.0.17:1536 -> 8.8.8.8:53.
 */
static void
mk (u8 *p)
{
  memset (p, 0, PKT_LEN);
  p[0] = 0x45;			/* outer: version 4, 20-byte header */
  p[8] = 64;			/* ttl */
  p[9] = IP_PROTOCOL_ICMP;
  p[12] = 4; p[13] = 4; p[14] = 4; p[15] = 4;		    /* 4.4.4.4 */
  p[16] = 200; p[17] = 150; p[18] = 0; p[19] = 1;	    /* us */

  p[O_ICMP_TYPE] = ICMP4_destination_unreachable;
  p[O_ICMP_TYPE + 1] = 4;	/* fragmentation needed */
  p[O_ICMP_TYPE + 6] = 0x05;	/* next-hop MTU 1500, in the second word */
  p[O_ICMP_TYPE + 7] = 0xdc;

  p[O_INNER] = 0x45;
  p[O_INNER + 8] = 64;
  p[O_INNER_PROTO] = IP_PROTOCOL_UDP;
  p[O_INNER_SRC] = 200; p[O_INNER_SRC + 1] = 150;
  p[O_INNER_SRC + 2] = 0; p[O_INNER_SRC + 3] = 17;	    /* 200.150.0.17 */
  p[O_INNER_SRC + 4] = 8; p[O_INNER_SRC + 5] = 8;
  p[O_INNER_SRC + 6] = 8; p[O_INNER_SRC + 7] = 8;	    /* 8.8.8.8 */

  p[O_INNER_L4] = 0x06; p[O_INNER_L4 + 1] = 0x00;	    /* src port 1536 */
  p[O_INNER_L4 + 2] = 0x00; p[O_INNER_L4 + 3] = 0x35;	    /* dst port 53 */
}

/* One past the last readable byte; an unmapped page starts here. */
static u8 *guard_end;

/* Copies `len` bytes of the packet so that they END at the guard page. */
static ip4_header_t *
place (const u8 *pkt, u32 len)
{
  u8 *p = guard_end - len;
  memcpy (p, pkt, len);
  return (ip4_header_t *) p;
}

static int
parse (const u8 *pkt, u32 len, bpa_icmp_err_t *e)
{
  ip4_header_t *ip = place (pkt, len);
  return bpa_icmp_err_parse (ip, guard_end, e);
}

int
main (void)
{
  long pagesz = sysconf (_SC_PAGESIZE);
  u8 pkt[PKT_LEN];
  bpa_icmp_err_t e;
  u8 *region;
  u32 i;

  /* Two pages, the second one unreadable: the first ends where a packet must
   * stop. */
  region = mmap (0, 2 * pagesz, PROT_READ | PROT_WRITE,
		 MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (region == MAP_FAILED
      || mprotect (region + pagesz, pagesz, PROT_NONE) != 0)
    {
      printf ("test_bpa_icmp: could not build the guard page\n");
      return 1;
    }
  guard_end = region + pagesz;

  printf ("bpa_icmp_err_parse: what it accepts\n");

  mk (pkt);
  CHECK (parse (pkt, PKT_LEN, &e) == 0, "a frag-needed carrying UDP parses");
  CHECK (e.pub_addr == 0xc8960011, "the public address is the EMBEDDED source");
  CHECK (e.pub_port == 1536, "the public port is the EMBEDDED source port");
  CHECK (e.ip_proto == IP_PROTOCOL_UDP, "the protocol is the embedded one");

  mk (pkt);
  pkt[O_INNER_PROTO] = IP_PROTOCOL_TCP;
  CHECK (parse (pkt, PKT_LEN, &e) == 0 && e.pub_port == 1536,
	 "embedded TCP keeps the port in the same two bytes");

  mk (pkt);
  pkt[O_INNER_PROTO] = IP_PROTOCOL_ICMP;
  pkt[O_INNER_L4 + 4] = 0x30;
  pkt[O_INNER_L4 + 5] = 0x39;
  CHECK (parse (pkt, PKT_LEN, &e) == 0 && e.pub_port == 12345,
	 "embedded ICMP takes the identifier four bytes in, not the type");

  mk (pkt);
  pkt[O_ICMP_TYPE] = ICMP4_time_exceeded;
  CHECK (parse (pkt, PKT_LEN, &e) == 0, "a time-exceeded is an error too");

  /* Outer options push everything along; the parse must follow the IHL rather
   * than assume 20. */
  {
    u8 opt[PKT_LEN + 4];
    mk (pkt);
    memcpy (opt, pkt, 20);
    memset (opt + 20, 1, 4); /* four NOPs */
    memcpy (opt + 24, pkt + 20, PKT_LEN - 20);
    opt[0] = 0x46; /* 24-byte outer header */
    CHECK (parse (opt, PKT_LEN + 4, &e) == 0 && e.pub_port == 1536,
	   "an outer header with options is followed, not assumed");
  }

  printf ("\nbpa_icmp_err_parse: what it refuses, without reading past the end\n");

  mk (pkt);
  pkt[O_ICMP_TYPE] = ICMP4_echo_reply;
  CHECK (parse (pkt, PKT_LEN, &e) != 0, "an echo reply is not an error");
  mk (pkt);
  pkt[O_ICMP_TYPE] = ICMP4_echo_request;
  CHECK (parse (pkt, PKT_LEN, &e) != 0, "nor is an echo request");

  mk (pkt);
  pkt[O_INNER_PROTO] = IP_PROTOCOL_GRE;
  CHECK (parse (pkt, PKT_LEN, &e) != 0,
	 "embedded GRE is refused: the Call ID an outbound packet carries is "
	 "the server's, and no table is keyed on it with our public address");

  /*
   * ESP is the one with no identifier of its own, and it is the one that
   * works. Its binding is keyed on (our public address, peer) in the table
   * shared between workers, and BOTH of those are in the embedded IP header -
   * so the error carries the whole key and needs no payload bytes at all.
   */
  mk (pkt);
  pkt[O_INNER_PROTO] = IP_PROTOCOL_IPSEC_ESP;
  CHECK (parse (pkt, PKT_LEN, &e) == 0, "embedded ESP parses");
  CHECK (e.port == 0, "and hands the caller no port to write through");
  CHECK (e.pub_port == 0, "nor a port value to key on");
  CHECK (e.pub_addr == 0xc8960011u,
	 "the embedded source is our public address, 200.150.0.17");
  CHECK (e.peer == 0x08080808u,
	 "and the embedded destination is the peer, 8.8.8.8");
  CHECK (e.ip_proto == IP_PROTOCOL_IPSEC_ESP, "and it says which protocol");

  /*
   * The bound the ported protocols need and this one does not. An ICMP error
   * is only required to quote eight bytes of the original datagram; truncated
   * to the embedded IP header alone, TCP has no port to read and ESP has
   * everything it needs.
   */
  mk (pkt);
  pkt[O_INNER_PROTO] = IP_PROTOCOL_IPSEC_ESP;
  CHECK (parse (pkt, O_INNER_L4, &e) == 0,
	 "ESP parses from the embedded IP header alone");
  mk (pkt);
  CHECK (parse (pkt, O_INNER_L4, &e) != 0,
	 "while TCP truncated to the same point is refused");

  /*
   * The attacker's header lengths. Both of these are the shape that reads off
   * the end: a length field is the sender's number and the bytes behind it are
   * not the sender's to describe.
   */
  mk (pkt);
  pkt[O_OUTER_IHL] = 0x4f; /* outer claims 60 bytes, 56 are here */
  CHECK (parse (pkt, PKT_LEN, &e) != 0,
	 "an outer IHL longer than the packet is refused");

  mk (pkt);
  pkt[O_INNER_IHL] = 0x4f; /* embedded claims 60, 28 are left */
  CHECK (parse (pkt, PKT_LEN, &e) != 0,
	 "an embedded IHL longer than what is left is refused");

  mk (pkt);
  pkt[O_INNER_IHL] = 0x44; /* 16 bytes: shorter than an IPv4 header can be */
  CHECK (parse (pkt, PKT_LEN, &e) != 0, "an embedded IHL below 20 is refused");

  mk (pkt);
  pkt[O_OUTER_IHL] = 0x44;
  CHECK (parse (pkt, PKT_LEN, &e) != 0, "an outer IHL below 20 is refused");

  /*
   * Every truncation there is. The last two bytes of the embedded L4 may go
   * missing and the parse must still say no, because the port it wants is
   * right at the front of it - which is the check that is easiest to write so
   * that it passes by an accident of arithmetic.
   */
  mk (pkt);
  for (i = 0; i < O_INNER_L4 + 2; i++)
    if (parse (pkt, i, &e) == 0)
      {
	printf ("  FAIL a %u-byte packet was accepted\n", i);
	failures++;
	break;
      }
  CHECK (i == O_INNER_L4 + 2,
	 "every length from 0 to %u bytes is refused", (u32) (O_INNER_L4 + 1));

  mk (pkt);
  CHECK (parse (pkt, O_INNER_L4 + 2, &e) == 0,
	 "and %u bytes - the port, and not a byte more - is enough",
	 (u32) (O_INNER_L4 + 2));

  /* For embedded ICMP the identifier is four bytes further in, so the same
   * length that sufficed above must not. */
  mk (pkt);
  pkt[O_INNER_PROTO] = IP_PROTOCOL_ICMP;
  CHECK (parse (pkt, O_INNER_L4 + 5, &e) != 0,
	 "embedded ICMP needs the identifier, not just the type and code");
  mk (pkt);
  pkt[O_INNER_PROTO] = IP_PROTOCOL_ICMP;
  CHECK (parse (pkt, O_INNER_L4 + 6, &e) == 0,
	 "and is accepted once the identifier is there");

  printf ("\n%s\n", failures ? "FAILED" : "test_bpa_icmp: all checks passed");
  return failures != 0;
}
