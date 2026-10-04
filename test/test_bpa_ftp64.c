/*
 * test_bpa_ftp64.c - the FTP64 ALG's text half (RFC 6384, #485 phase 5).
 *
 * What a NAT64 pool's FTP ALG turns each command and reply into, and what it
 * refuses to touch. The datapath half - the splice, the mapping an EPRT
 * opens, the sequence correction - is lab/ftp64.sh's, with a real client and
 * a real IPv4 server; this is the decision under it, which a lab can only
 * sample: every RFC 6384 case, the RFC's own examples byte for byte, and the
 * malformed inputs a server or a client on the internet can send.
 */

#include <stdio.h>
#include <string.h>
#include "bpa_tcp_shim.h"
#include "../src/plugins/bpa/bpa_ftp.h"
/* Included for the same reason test_bpa_ftp.c includes it: the parsers the
 * FTP64 half stands on are static. */
#include "../src/plugins/bpa/bpa_ftp.c"

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

/* 2001:db8:1200:ab00::10, the host lab/nat64.sh's subscriber sends from. */
static const u8 HOST[16] = { 0x20, 0x01, 0x0d, 0xb8, 0x12, 0x00, 0xab, 0x00,
			     0,	   0,	 0,    0,    0,	   0,	 0,    0x10 };

static u8 rep[BPA_FTP64_MAX_LINE + 1];
static u32 rep_len, line_len;

static int
client (const char *line, u8 *st, u16 *port)
{
  u16 dummy = 0;

  memset (rep, 0, sizeof (rep));
  return bpa_ftp64_client ((const u8 *) line, (u32) strlen (line), st, HOST,
			   rep, &rep_len, &line_len, port ? port : &dummy);
}

static int
server_retx (const char *line, u8 *st, int is_retx)
{
  memset (rep, 0, sizeof (rep));
  return bpa_ftp64_server ((const u8 *) line, (u32) strlen (line), st,
			   is_retx, rep, &rep_len, &line_len);
}

static int
server (const char *line, u8 *st)
{
  return server_retx (line, st, 0);
}

static int
rep_is (const char *s)
{
  return rep_len == strlen (s) && memcmp (rep, s, rep_len) == 0;
}

static void
test_epsv (void)
{
  u8 st = 0;

  printf ("=== bpa_ftp64_client: EPSV goes as PASV (RFC 6384 6) ===\n");
  CHECK (client ("EPSV\r\n", &st, 0) == BPA_FTP64_REPLACE
	   && rep_is ("PASV\r\n") && line_len == 6
	   && (st & BPA_FTP64_PENDING) == BPA_FTP64_P_229,
	 "EPSV -> PASV, the 227 to come back as 229 (st %x, '%.*s')", st,
	 (int) rep_len, rep);
  st = 0;
  CHECK (client ("epsv 2\r\n", &st, 0) == BPA_FTP64_REPLACE
	   && rep_is ("PASV\r\n")
	   && (st & BPA_FTP64_PENDING) == BPA_FTP64_P_229,
	 "EPSV 2 (IPv6, what an IPv6 client asks), any case, -> PASV");
  st = 0;
  CHECK (client ("EPSV ALL\r\n", &st, 0) == BPA_FTP64_REPLACE
	   && rep_is ("NOOP\r\n")
	   && (st & BPA_FTP64_PENDING) == BPA_FTP64_P_504,
	 "EPSV ALL never reaches the server: a NOOP, answered 504");
  st = 0;
  CHECK (client ("EPSV all\r\n", &st, 0) == BPA_FTP64_REPLACE
	   && (st & BPA_FTP64_PENDING) == BPA_FTP64_P_504,
	 "and its argument is case-insensitive");
  st = 0;
  CHECK (client ("EPSV 1\r\n", &st, 0) == BPA_FTP64_REPLACE
	   && rep_is ("NOOP\r\n")
	   && (st & BPA_FTP64_PENDING) == BPA_FTP64_P_522,
	 "EPSV with another network protocol: a NOOP, answered 522");
  st = 0;
  CHECK (client ("EPSVX\r\n", &st, 0) == BPA_FTP64_PASS && st == 0,
	 "EPSVX is not EPSV");
  CHECK (client ("EPSV foo\r\n", &st, 0) == BPA_FTP64_PASS && st == 0,
	 "an argument RFC 2428 does not define goes to the server, which "
	 "answers it");
  CHECK (client ("RETR a.bin\r\n", &st, 0) == BPA_FTP64_PASS && st == 0,
	 "anything else passes (RFC 6384 10)");
}

static void
test_eprt (void)
{
  u8 st = 0;
  u16 port = 0;

  printf ("=== bpa_ftp64_client: EPRT, stateful (RFC 6384 7.2) ===\n");
  CHECK (client ("EPRT |2|2001:db8:1200:ab00::10|5282|\r\n", &st, &port)
	     == BPA_FTP64_EPRT
	   && port == 5282 && line_len == 38 && st == 0,
	 "EPRT naming the control connection's own address: a mapping for "
	 "5282 (port %u, line %u)",
	 port, line_len);
  port = 0;
  CHECK (client ("EPRT |2|2001:DB8:1200:AB00:0:0:0:10|5282|\r\n", &st, &port)
	     == BPA_FTP64_EPRT
	   && port == 5282,
	 "the same address written uncompressed and in capitals");
  CHECK (client ("EPRT !2!2001:db8:1200:ab00::10!5282!\r\n", &st, &port)
	   == BPA_FTP64_EPRT,
	 "any delimiter RFC 2428 allows");
  CHECK (client ("EPRT |2|2001:db8:1200:ab00::11|5282|\r\n", &st, 0)
	   == BPA_FTP64_PASS,
	 "another host's address is passed unchanged (SHOULD NOT translate)");
  CHECK (client ("EPRT |1|192.0.2.31|5282|\r\n", &st, 0) == BPA_FTP64_PASS,
	 "an IPv4 EPRT is passed unchanged");
  CHECK (client ("EPRT |2|2001:db8::zz|5282|\r\n", &st, 0)
	   == BPA_FTP64_UNTOUCHED,
	 "an address that does not parse: untouched, counted");
  CHECK (client ("EPRT |2|2001:db8:1200:ab00::10|0|\r\n", &st, 0)
	   == BPA_FTP64_UNTOUCHED,
	 "port 0 is no port");
  CHECK (client ("EPRT |2|2001:db8:1200:ab00::10|70000|\r\n", &st, 0)
	   == BPA_FTP64_UNTOUCHED,
	 "nor is one past 65535");
  CHECK (client ("EPRT |3|2001:db8:1200:ab00::10|5282|\r\n", &st, 0)
	   == BPA_FTP64_UNTOUCHED,
	 "a family RFC 2428 does not define");
  CHECK (client ("EPRT |2|2001:db8:1200:ab00::10|5282\r\n", &st, 0)
	   == BPA_FTP64_UNTOUCHED,
	 "the closing delimiter is required");
  CHECK (client ("EPRT |2|2001:db8:1200:ab00::10|5282|x\r\n", &st, 0)
	   == BPA_FTP64_UNTOUCHED,
	 "and nothing after it");
  CHECK (st == 0, "none of those touched the state (%x)", st);
}

static void
test_algs_auth (void)
{
  u8 st = 0;

  printf ("=== bpa_ftp64_client: ALGS (11) and AUTH (5) ===\n");
  CHECK (client ("ALGS STATUS64\r\n", &st, 0) == BPA_FTP64_REPLACE
	   && rep_is ("NOOP\r\n")
	   && (st & BPA_FTP64_PENDING) == BPA_FTP64_P_216,
	 "ALGS STATUS64 goes as NOOP, its 200 to become 216");
  st = 0;
  CHECK (client ("ALGS DISABLE64\r\n", &st, 0) == BPA_FTP64_REPLACE
	   && (st & BPA_FTP64_DISABLED)
	   && (st & BPA_FTP64_PENDING) == BPA_FTP64_P_216,
	 "ALGS DISABLE64 turns translation off");
  st &= (u8) ~BPA_FTP64_PENDING;
  CHECK (client ("EPSV\r\n", &st, 0) == BPA_FTP64_PASS,
	 "and EPSV then reaches the server as it is");
  CHECK (client ("ALGS ENABLE64\r\n", &st, 0) == BPA_FTP64_REPLACE
	   && !(st & BPA_FTP64_DISABLED),
	 "the channel is still watched for ALGS ENABLE64, which turns it on");
  st = 0;
  CHECK (client ("ALGS\r\n", &st, 0) == BPA_FTP64_REPLACE
	   && (st & BPA_FTP64_PENDING) == BPA_FTP64_P_504,
	 "ALGS with no argument: 504");
  st = 0;
  CHECK (client ("ALGS STATUS6\r\n", &st, 0) == BPA_FTP64_REPLACE
	   && (st & BPA_FTP64_PENDING) == BPA_FTP64_P_504,
	 "and with one it does not know");
  st = 0;
  CHECK (client ("AUTH TLS\r\n", &st, 0) == BPA_FTP64_PASS
	   && (st & BPA_FTP64_PENDING) == BPA_FTP64_P_AUTH,
	 "AUTH passes, and the reply decides");
  st = BPA_FTP64_TRANSPARENT;
  CHECK (client ("EPSV\r\n", &st, 0) == BPA_FTP64_PASS
	   && client ("ALGS STATUS64\r\n", &st, 0) == BPA_FTP64_PASS
	   && st == BPA_FTP64_TRANSPARENT,
	 "once AUTH is taken nothing is translated, ALGS included");
}

static void
test_framing (void)
{
  u8 st = 0;

  printf ("=== bpa_ftp64_client: lines ===\n");
  CHECK (client ("EPSV", &st, 0) == BPA_FTP64_UNTOUCHED && st == 0,
	 "a command of ours with no CRLF in the segment is left and counted");
  CHECK (client ("EPSV\n", &st, 0) == BPA_FTP64_UNTOUCHED,
	 "a bare LF is not the NVT line end");
  CHECK (client ("NOOP\r\nEPSV\r\n", &st, 0) == BPA_FTP64_UNTOUCHED
	   && st == 0,
	 "one on the SECOND line is not translated, and is counted");
  CHECK (client ("NOOP\r\nRETR x\r\n", &st, 0) == BPA_FTP64_PASS,
	 "a second line of anything else is nothing");
  CHECK (client ("NOOP", &st, 0) == BPA_FTP64_PASS,
	 "and a partial line of anything else is nothing");
}

static void
test_server (void)
{
  u8 st;

  printf ("=== bpa_ftp64_server: the reply ===\n");
  st = BPA_FTP64_P_229;
  CHECK (server ("227 Entering Passive Mode (192,0,2,31,237,19)\r\n", &st)
	     == BPA_FTP64_REPLACE
	   && rep_is ("229 Entering Extended Passive Mode (|||60691|)\r\n")
	   && line_len == 47 && (st & BPA_FTP64_PENDING) == 0,
	 "RFC 6384 6's own example, byte for byte: '%.*s'", (int) rep_len,
	 rep);
  st = BPA_FTP64_P_229;
  CHECK (server ("227 =10,0,0,9,1,2\r\n", &st) == BPA_FTP64_REPLACE
	   && rep_is ("229 Entering Extended Passive Mode (|||258|)\r\n"),
	 "a 227 without the parenthesis (RFC 1123 4.1.2.6), and a private "
	 "address ignored");
  st = BPA_FTP64_P_229;
  CHECK (server ("500 PASV not understood\r\n", &st) == BPA_FTP64_PASS
	   && st == 0,
	 "a refusal reaches the client as it is, and nothing waits");
  st = BPA_FTP64_P_229;
  CHECK (server ("227 Entering Passive Mode\r\n", &st) == BPA_FTP64_UNTOUCHED
	   && st == 0,
	 "a 227 with no numbers is left, counted");
  st = BPA_FTP64_P_229;
  CHECK (server ("227 (192,0,2,31,0,0)\r\n", &st) == BPA_FTP64_UNTOUCHED,
	 "port 0 is not translated into a 229");
  st = BPA_FTP64_P_229;
  CHECK (server ("150 Opening\r\n", &st) == BPA_FTP64_PASS
	   && st == BPA_FTP64_P_229,
	 "a preliminary reply leaves the answer pending");
  st = BPA_FTP64_P_229;
  CHECK (server ("227-first\r\n227 last\r\n", &st) == BPA_FTP64_UNTOUCHED
	   && st == 0,
	 "a multi-line reply is not one of these");
  st = BPA_FTP64_P_229;
  CHECK (server ("hello\r\n", &st) == BPA_FTP64_UNTOUCHED && st == 0,
	 "and not a reply at all drops the pending answer");
  st = BPA_FTP64_P_229;
  CHECK (server ("227 (1,2,3,4,5,6)", &st) == BPA_FTP64_UNTOUCHED,
	 "a reply split across segments is left");
  st = 0;
  CHECK (server ("227 Entering Passive Mode (192,0,2,31,237,19)\r\n", &st)
	   == BPA_FTP64_PASS,
	 "a 227 nobody asked for through the ALG is never rewritten");

  /* The server sends a rewritten reply again: rewritten again, the same. */
  st = BPA_FTP64_P_229;
  server ("227 Entering Passive Mode (192,0,2,31,237,19)\r\n", &st);
  CHECK (server_retx ("227 Entering Passive Mode (192,0,2,31,237,19)\r\n",
		      &st, 1)
	     == BPA_FTP64_REPLACE
	   && rep_is ("229 Entering Extended Passive Mode (|||60691|)\r\n"),
	 "a retransmitted 227 is rewritten into the same 229");
  CHECK (server ("227 Entering Passive Mode (192,0,2,31,237,19)\r\n", &st)
	   == BPA_FTP64_PASS,
	 "and a 227 that is not a retransmission, with nothing pending, is not");
  st = 0;
  CHECK (server_retx ("227 (1,2,3,4,5,6)\r\n", &st, 1) == BPA_FTP64_PASS,
	 "a retransmission of a reply nothing rewrote passes");

  st = BPA_FTP64_P_522;
  CHECK (server ("200 NOOP ok\r\n", &st) == BPA_FTP64_REPLACE
	   && rep_is ("522 Network protocol not supported\r\n")
	   && (st & BPA_FTP64_PENDING) == 0,
	 "the NOOP that stood in for EPSV 1 answers 522");
  st = BPA_FTP64_P_504;
  CHECK (server ("200 OK\r\n", &st) == BPA_FTP64_REPLACE
	   && rep_is ("504 Command not implemented for that parameter\r\n"),
	 "for EPSV ALL, 504");
  st = BPA_FTP64_P_216;
  CHECK (server ("200 OK\r\n", &st) == BPA_FTP64_REPLACE
	   && rep_is ("216 EPSVEPRT\r\n"),
	 "for ALGS, 216 EPSVEPRT while translating");
  st = BPA_FTP64_P_216 | BPA_FTP64_DISABLED;
  CHECK (server ("200 OK\r\n", &st) == BPA_FTP64_REPLACE
	   && rep_is ("216 NONE\r\n")
	   && (st & 0x1f) == BPA_FTP64_DISABLED,
	 "and 216 NONE once disabled");
  st = BPA_FTP64_P_216;
  CHECK (server ("421 closing\r\n", &st) == BPA_FTP64_PASS && st == 0,
	 "a NOOP answered otherwise reaches the client as it is");

  st = BPA_FTP64_P_AUTH;
  CHECK (server ("234 AUTH TLS ok\r\n", &st) == BPA_FTP64_PASS
	   && st == BPA_FTP64_TRANSPARENT,
	 "AUTH taken: transparent from here");
  st = BPA_FTP64_P_AUTH;
  CHECK (server ("334 ADAT=\r\n", &st) == BPA_FTP64_PASS
	   && st == BPA_FTP64_TRANSPARENT,
	 "a 3xx (security data follows) is taken too");
  st = BPA_FTP64_P_AUTH;
  CHECK (server ("504 not here\r\n", &st) == BPA_FTP64_PASS && st == 0,
	 "AUTH refused (4xx, 5xx) changes nothing");
}

/* A full one's-complement sum over the header, independent of the code under
 * test: the incremental update is held to it, never to itself. */
static u16
full_checksum (const tcp_header_t *t)
{
  const u8 *p = (const u8 *) t;
  unsigned csum_off = (unsigned) ((const u8 *) &t->checksum - p);
  uint32_t sum = 0;
  unsigned i;

  for (i = 0; i + 1 < sizeof (*t); i += 2)
    if (i != csum_off)
      sum += (uint32_t) ((p[i] << 8) | p[i + 1]);
  while (sum >> 16)
    sum = (sum & 0xffff) + (sum >> 16);
  return (u16) ~sum;
}

static void
test_urg (void)
{
  tcp_header_t t;

  printf ("=== bpa_ftp64_clear_urg (RFC 6384 5) ===\n");
  memset (&t, 0, sizeof (t));
  t.src_port = clib_host_to_net_u16 (40000);
  t.dst_port = clib_host_to_net_u16 (21);
  t.seq_number = clib_host_to_net_u32 (1000);
  t.data_offset_and_reserved = 0x50;
  t.flags = TCP_FLAG_URG | TCP_FLAG_ACK | 0x08;
  t.urgent_pointer = clib_host_to_net_u16 (3);
  t.checksum = clib_host_to_net_u16 (full_checksum (&t));
  CHECK (bpa_ftp64_clear_urg (&t) == 1 && !(t.flags & TCP_FLAG_URG)
	   && (t.flags & TCP_FLAG_ACK) && t.urgent_pointer == 0
	   && clib_net_to_host_u16 (t.checksum) == full_checksum (&t),
	 "URG and the pointer cleared, the checksum still the full sum "
	 "(%04x, full %04x)",
	 clib_net_to_host_u16 (t.checksum), full_checksum (&t));
  CHECK (bpa_ftp64_clear_urg (&t) == 0
	   && clib_net_to_host_u16 (t.checksum) == full_checksum (&t),
	 "a segment without URG is left as it is");
}

static void
test_text (void)
{
  u8 a[16], want[16];

  printf ("=== bpa_ftp64_ip6, bpa_ftp64_put_port ===\n");
  CHECK (bpa_ftp64_ip6 ((const u8 *) "2001:db8:1200:ab00::10", 22, a) == 0
	   && memcmp (a, HOST, 16) == 0,
	 "the compressed form");
  memset (want, 0, 16);
  CHECK (bpa_ftp64_ip6 ((const u8 *) "::", 2, a) == 0
	   && memcmp (a, want, 16) == 0,
	 "::");
  want[15] = 1;
  CHECK (bpa_ftp64_ip6 ((const u8 *) "::1", 3, a) == 0
	   && memcmp (a, want, 16) == 0,
	 "::1");
  memset (want, 0, 16);
  want[0] = 0;
  want[1] = 1;
  CHECK (bpa_ftp64_ip6 ((const u8 *) "1::", 3, a) == 0
	   && memcmp (a, want, 16) == 0,
	 "1::");
  memset (want, 0, 16);
  want[10] = want[11] = 0xff;
  want[12] = 192;
  want[14] = 2;
  want[15] = 1;
  CHECK (bpa_ftp64_ip6 ((const u8 *) "::ffff:192.0.2.1", 16, a) == 0
	   && memcmp (a, want, 16) == 0,
	 "a dotted IPv4 tail");
  CHECK (bpa_ftp64_ip6 ((const u8 *) "1:2:3:4:5:6:7:8", 15, a) == 0
	   && a[1] == 1 && a[15] == 8,
	 "eight groups");
  CHECK (bpa_ftp64_ip6 ((const u8 *) "1:2:3:4:5:6:7::", 15, a) == 0
	   && a[13] == 7 && a[15] == 0,
	 ":: standing for the one group left");
  CHECK (bpa_ftp64_ip6 ((const u8 *) "1:2:3:4:5:6:7:8:9", 17, a) != 0
	   && bpa_ftp64_ip6 ((const u8 *) "1:2:3:4:5:6:7", 13, a) != 0
	   && bpa_ftp64_ip6 ((const u8 *) "1::2::3", 7, a) != 0
	   && bpa_ftp64_ip6 ((const u8 *) ":1", 2, a) != 0
	   && bpa_ftp64_ip6 ((const u8 *) "1:", 2, a) != 0
	   && bpa_ftp64_ip6 ((const u8 *) ":::", 3, a) != 0
	   && bpa_ftp64_ip6 ((const u8 *) "12345::", 7, a) != 0
	   && bpa_ftp64_ip6 ((const u8 *) "g::", 3, a) != 0
	   && bpa_ftp64_ip6 ((const u8 *) "::1.2.3", 7, a) != 0
	   && bpa_ftp64_ip6 ((const u8 *) "1:2:3:4:5:6:7:1.2.3.4", 21, a) != 0
	   && bpa_ftp64_ip6 ((const u8 *) "", 0, a) != 0,
	 "and each malformed form refused");

  rep_len = bpa_ftp64_put_port (rep, 0xc000021fu, 60192);
  CHECK (rep_is ("PORT 192,0,2,31,235,32\r\n"),
	 "RFC 6384 7.2's own PORT, byte for byte: '%.*s'", (int) rep_len,
	 rep);
  rep_len = bpa_ftp64_put_port (rep, 0xffffffffu, 65535);
  CHECK (rep_len <= BPA_FTP64_MAX_LINE, "the widest PORT fits the buffer");

  CHECK (bpa_ftp64_line ((const u8 *) "ab\r\ncd", 6) == 4
	   && bpa_ftp64_line ((const u8 *) "ab\n", 3) == 0,
	 "bpa_ftp64_line: the first CRLF, or none");
  CHECK (bpa_ftp64_cmd ((const u8 *) "EPSV 2", 6, "EPSV")
	   && !bpa_ftp64_cmd ((const u8 *) "EPSVALL", 7, "EPSV"),
	 "bpa_ftp64_cmd: the word and a space, or the word alone");
  CHECK (bpa_ftp64_arg_is ((const u8 *) "EPSV all", 8, "ALL")
	   && !bpa_ftp64_arg_is ((const u8 *) "EPSV alle", 9, "ALL"),
	 "bpa_ftp64_arg_is: the whole argument");
  CHECK (bpa_ftp64_hex ('f') == 15 && bpa_ftp64_hex ('A') == 10
	   && bpa_ftp64_hex ('g') == -1,
	 "bpa_ftp64_hex");
  CHECK (bpa_ftp64_put (rep, "xy") == 2 && rep[0] == 'x',
	 "bpa_ftp64_put copies without the NUL");
  {
    u16 port = 0;
    CHECK (bpa_ftp64_eprt ((const u8 *) "EPRT |2|2001:db8:1200:ab00::10|9|",
			   33, HOST, &port)
	       == 1
	     && port == 9,
	   "bpa_ftp64_eprt on a body without its CRLF");
  }
  CHECK (bpa_ftp64_later ((const u8 *) "\nALGS", 5)
	   && !bpa_ftp64_later ((const u8 *) "ALGS", 4),
	 "bpa_ftp64_later reads line starts after an LF");

  /* Every line either side writes fits the buffer the datapath holds. */
  {
    u8 st = BPA_FTP64_P_504;

    server ("200 OK\r\n", &st);
    CHECK (rep_len <= BPA_FTP64_MAX_LINE, "the 504 fits");
    st = BPA_FTP64_P_229;
    server ("227 (255,255,255,255,255,255)\r\n", &st);
    CHECK (rep_len <= BPA_FTP64_MAX_LINE && rep_len == 48,
	   "and the widest 229 (%u)", rep_len);
  }
}

int
main (void)
{
  test_epsv ();
  test_eprt ();
  test_algs_auth ();
  test_framing ();
  test_server ();
  test_text ();
  test_urg ();
  return bpa_test_verdict (checks, failures);
}
