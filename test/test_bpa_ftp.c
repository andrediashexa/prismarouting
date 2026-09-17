/*
 * test_bpa_ftp.c - unit test for the FTP ALG's sequence arithmetic.
 *
 * Rewriting an ASCII address in a control connection changes the LENGTH of
 * the payload, so every byte after it sits at a different offset in the
 * stream than the subscriber believes. The correction is arithmetic on two
 * header fields, and when it is wrong the connection stalls a few commands
 * later - a failure that looks like anything except FTP, with nothing in any
 * counter to say so.
 *
 * Three things a lab structurally cannot enumerate, and this file does:
 *
 *   - the 2^32 boundary. The comparison is signed because plain `>` is wrong
 *     across it, and a control connection has to live a long time to get
 *     there. No lab is going to send four gigabytes to find out.
 *   - a retransmission of data sent BEFORE the rewrite, which must keep the
 *     offset that was in force when it was first sent.
 *   - the reply direction on a subscriber's own FTP server, where the port
 *     is read off the session because in2out has already replaced the one in
 *     the header.
 *
 * Every checksum is compared against a full recomputation over the segment,
 * not against the incremental arithmetic that produced it.
 */

#include <stdio.h>
#include <string.h>
#include "bpa_tcp_shim.h"
#include "../src/plugins/bpa/bpa_ftp.h"
/*
 * The parser itself, INCLUDED rather than linked: every function in the text
 * half of this ALG is static, so nothing compiled beside the file can call
 * one. bpa_ftp.c carries the BPA_STANDALONE guards that leave the two
 * functions needing a buffer and a session pool out of this build.
 */
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

static u16
full_checksum (const tcp_header_t *t)
{
  const u8 *p = (const u8 *) t;
  unsigned csum_off = (unsigned) ((const u8 *) &t->checksum - p);
  uint32_t sum = 0;
  unsigned i;

  for (i = 0; i + 1 < sizeof (*t); i += 2)
    {
      if (i == csum_off)
	continue;
      sum += (uint32_t) ((p[i] << 8) | p[i + 1]);
    }
  while (sum >> 16)
    sum = (sum & 0xffff) + (sum >> 16);
  return (u16) ~sum;
}

static void
build (tcp_header_t *t, u32 seq, u32 ack, u8 flags)
{
  memset (t, 0, sizeof (*t));
  t->src_port = clib_host_to_net_u16 (21);
  t->dst_port = clib_host_to_net_u16 (49152);
  t->seq_number = clib_host_to_net_u32 (seq);
  t->ack_number = clib_host_to_net_u32 (ack);
  t->flags = flags;
  t->data_offset_and_reserved = 5 << 4;
  t->checksum = clib_host_to_net_u16 (full_checksum (t));
}

/*
 * A session whose subscriber -> internet direction has drifted.
 *
 * There used to be a flag argument saying whether an ALG had touched this
 * session at all. It is gone with the flag: under one shared mechanism "has
 * drifted" IS "the offsets are not both zero", and the flag was redundant with
 * them in every case except a session whose two rewrites cancelled exactly -
 * where no correction is needed anyway (#48).
 */
static bpa_session_t
session (u32 pos, i32 before, i32 after)
{
  bpa_session_t s;
  memset (&s, 0, sizeof (s));
  s.adj[BPA_DIR_OUT].correction_pos = pos;
  s.adj[BPA_DIR_OUT].off_before = before;
  s.adj[BPA_DIR_OUT].off_after = after;
  return s;
}

/* ------------------------------------------------------ serial arithmetic */

static void
seq_boundary_cases (void)
{
  bpa_seq_adj_t a;

  printf ("--- bpa_seq_delta_at: serial numbers, not integers ---\n");

  /* A correction at 1000: eight bytes after it, nothing before. */
  a.correction_pos = 1000;
  a.off_before = 0;
  a.off_after = 8;

  CHECK (bpa_seq_delta_at (&a, 1200) == 8, "1200 is after 1000");
  CHECK (bpa_seq_delta_at (&a, 900) == 0, "900 is not");
  CHECK (bpa_seq_delta_at (&a, 1000) == 0,
	 "and the rewritten segment itself is not after itself");

  /*
   * The boundary. Written with a plain `>` these two answer the opposite way
   * round, and the correction is then applied in the wrong direction on a
   * connection that has been up long enough to wrap - which is the connection
   * least likely to be reproduced by anyone investigating it.
   */
  a.correction_pos = 0xffffff00u;
  CHECK (bpa_seq_delta_at (&a, 0x00000100u) == 8,
	 "a wrapped sequence is after one just below 2^32");
  CHECK (bpa_seq_delta_at (&a, 0xfffffe00u) == 0,
	 "and the one below 2^32 is not after the wrapped one");
  /*
   * Anchored to what the function ANSWERS. Written as
   * `(0x00000100u > 0xffffff00u) == 0` this named no production symbol at
   * all: an integer constant expression the compiler folds to 1, true of C
   * rather than of bpa_seq_delta_at, and no mutation of bpa_seq.h could turn
   * it red. Asked as "the two comparisons disagree here", it goes red the
   * moment the production one stops being signed.
   */
  {
    int signed_says = bpa_seq_delta_at (&a, 0x00000100u) != 0;
    int plain_says = (0x00000100u > a.correction_pos);

    CHECK (signed_says != plain_says,
	   "a plain > answers this boundary the other way round, which is "
	   "why the comparison is signed");
  }
}

/* ------------------------------------------------------ is this control? */

static void
is_control_cases (void)
{
  tcp_header_t t;
  bpa_session_t s;

  printf ("--- bpa_ftp_is_control ---\n");
  CHECK (BPA_FTP_CONTROL_PORT == 21, "the FTP control port moved");

  build (&t, 1000, 0, TCP_FLAG_ACK);
  t.dst_port = clib_host_to_net_u16 (21);
  s = session (0, 0, 0);
  CHECK (bpa_ftp_is_control (&t, &s) == 1,
	 "a segment to port 21 is a control connection");

  /*
   * The subscriber's own FTP server. By the time the ALG runs, in2out has
   * replaced the header's source port with the public one, so this case
   * stops looking like port 21 exactly when it has to be recognised - which
   * is why the test reads the port off the SESSION.
   */
  build (&t, 1000, 0, TCP_FLAG_ACK);
  t.dst_port = clib_host_to_net_u16 (49152);
  t.src_port = clib_host_to_net_u16 (34567);
  s = session (0, 0, 0);
  s.port_priv = 21;
  CHECK (bpa_ftp_is_control (&t, &s) == 1,
	 "a reply from the subscriber's own server is control traffic");

  build (&t, 1000, 0, TCP_FLAG_ACK);
  t.dst_port = clib_host_to_net_u16 (49152);
  s = session (0, 0, 0);
  s.port_priv = 40000;
  CHECK (bpa_ftp_is_control (&t, &s) == 0,
	 "ordinary traffic is not a control connection");
}

/* ----------------------------------------------------- the sequence shift */

static void
expect_seq (const char *what, bpa_session_t *s, u32 seq, int want_ret,
	    u32 want_seq)
{
  tcp_header_t t;
  u16 before;
  int ret;

  build (&t, seq, 0, TCP_FLAG_ACK);
  before = t.checksum;
  /* Subscriber -> internet: this direction is the one that drifted, and the
   * other carries no acknowledgement correction. */
  ret = bpa_tcp_seq_adjust (&t, &s->adj[BPA_DIR_OUT], &s->adj[BPA_DIR_IN]);

  CHECK (ret == want_ret, "%s: returned %d, expected %d", what, ret, want_ret);
  CHECK (clib_net_to_host_u32 (t.seq_number) == want_seq,
	 "%s: sequence %u, expected %u", what,
	 clib_net_to_host_u32 (t.seq_number), want_seq);
  if (ret)
    CHECK (clib_net_to_host_u16 (t.checksum) == full_checksum (&t),
	   "%s: checksum %04x, a full recomputation says %04x", what,
	   clib_net_to_host_u16 (t.checksum), full_checksum (&t));
  else
    CHECK (t.checksum == before, "%s: declined but changed the checksum", what);
}

static void
expect_ack (const char *what, bpa_session_t *s, u32 ack, u8 flags,
	    int want_ret, u32 want_ack)
{
  tcp_header_t t;
  u16 before;
  int ret;

  build (&t, 500, ack, flags);
  before = t.checksum;
  /* Internet -> subscriber: the acknowledgement is expressed in the stream
   * the OTHER direction lengthened, which is why that one is the reverse. */
  ret = bpa_tcp_seq_adjust (&t, &s->adj[BPA_DIR_IN], &s->adj[BPA_DIR_OUT]);

  CHECK (ret == want_ret, "%s: returned %d, expected %d", what, ret, want_ret);
  CHECK (clib_net_to_host_u32 (t.ack_number) == want_ack,
	 "%s: acknowledgement %u, expected %u", what,
	 clib_net_to_host_u32 (t.ack_number), want_ack);
  if (ret)
    CHECK (clib_net_to_host_u16 (t.checksum) == full_checksum (&t),
	   "%s: checksum %04x, a full recomputation says %04x", what,
	   clib_net_to_host_u16 (t.checksum), full_checksum (&t));
  else
    CHECK (t.checksum == before, "%s: declined but changed the checksum", what);
}


/*
 * The text half of the ALG: the decimal parser, the two address grammars and
 * the line parse.
 *
 * What these decide is whether a subscriber's payload is touched at all, and
 * they decide it on bytes a remote wrote. A parser that accepts a little
 * more than the grammar does not fail visibly - it rewrites something that is
 * not an address, inside a file name or a password, and the transfer that
 * breaks is not the one the operator is looking at.
 */
static void
text_cases (void)
{
  u32 v1 = 0, v2 = 0, v3 = 0;
  u8 out[32];

  printf ("\n--- the text half: numbers ---\n");

  /*
   * Three things at once, because all three are the same rule: how far a run
   * of digits is read. The ceiling refuses a value over `max` outright, and
   * the five-digit bound stops before a sixth digit - a port cannot need one,
   * and a payload can carry any number of them.
   */
  CHECK (bpa_ftp_dec ((const u8 *) "65535x", 6, 65535, &v1) == 5 && v1 == 65535
	   && bpa_ftp_dec ((const u8 *) "70000", 5, 65535, &v2) == 0
	   && bpa_ftp_dec ((const u8 *) "999999", 6, 4294967295u, &v3) == 5
	   && v3 == 99999,
	 "bpa_ftp_dec reads at most five digits and refuses over the "
	 "ceiling: got %u/%u %u %u/%u",
	 bpa_ftp_dec ((const u8 *) "65535x", 6, 65535, &v1), v1,
	 bpa_ftp_dec ((const u8 *) "70000", 5, 65535, &v2),
	 bpa_ftp_dec ((const u8 *) "999999", 6, 4294967295u, &v3), v3);

  memset (out, 0, sizeof (out));
  CHECK (bpa_ftp_put_dec (out, 65535) == 5
	   && memcmp (out, "65535", 5) == 0
	   && bpa_ftp_put_dec (out, 0) == 1 && out[0] == '0',
	 "bpa_ftp_put_dec writes the digits most significant first: "
	 "wrote \"%.5s\"",
	 out);

  printf ("\n--- the text half: keywords ---\n");

  /*
   * RFC 959 s2.3 makes the commands case-insensitive, and every client that
   * sends "port " in lower case depends on it. The second and third are the
   * refusals: a keyword that only looks like one, and a payload too short to
   * hold the whole of it.
   */
  CHECK (bpa_ftp_starts ((const u8 *) "port 1,2,3,4,5,6", 16, "PORT ") == 1
	   && bpa_ftp_starts ((const u8 *) "PORX 1,2", 8, "PORT ") == 0
	   && bpa_ftp_starts ((const u8 *) "PO", 2, "PORT ") == 0,
	 "bpa_ftp_starts folds case and refuses a near miss: got %d %d %d",
	 bpa_ftp_starts ((const u8 *) "port 1,2,3,4,5,6", 16, "PORT "),
	 bpa_ftp_starts ((const u8 *) "PORX 1,2", 8, "PORT "),
	 bpa_ftp_starts ((const u8 *) "PO", 2, "PORT "));

  printf ("\n--- the text half: the two address grammars ---\n");

  {
    u32 addr = 0;
    u16 port = 0;
    u32 n;

    /* 192.168.0.1 port 50000, which is 195*256 + 80. */
    n = bpa_ftp_parse_hostport ((const u8 *) "192,168,0,1,195,80\r\n", 20,
				&addr, &port);
    CHECK (n == 18 && addr == 0xc0a80001 && port == 50000
	     && bpa_ftp_parse_hostport ((const u8 *) "192.168.0.1.195.80", 18,
					&addr, &port)
		  == 0,
	   "bpa_ftp_parse_hostport reads six comma-separated octets: "
	   "n=%u addr=%08x port=%u",
	   n, addr, port);

    memset (out, 0, sizeof (out));
    n = bpa_ftp_put_hostport (out, 0x0a000001, 50000);
    CHECK (n == 15 && memcmp (out, "10,0,0,1,195,80", 15) == 0,
	   "bpa_ftp_put_hostport writes the same grammar back: n=%u \"%.15s\"",
	   n, out);

    addr = 0;
    n = bpa_ftp_parse_dotted ((const u8 *) "203.0.113.9|", 12, &addr);
    CHECK (n == 11 && addr == 0xcb007109
	     && bpa_ftp_parse_dotted ((const u8 *) "203,0,113,9", 11, &addr)
		  == 0,
	   "bpa_ftp_parse_dotted reads four dot-separated octets: "
	   "n=%u addr=%08x",
	   n, addr);

    memset (out, 0, sizeof (out));
    n = bpa_ftp_put_dotted (out, 0xcb007109);
    CHECK (n == 11 && memcmp (out, "203.0.113.9", 11) == 0,
	   "bpa_ftp_put_dotted writes the same grammar back: n=%u \"%.11s\"",
	   n, out);
  }

  printf ("\n--- the text half: the line parse ---\n");

  {
    bpa_ftp_parse_t r;
    const char *port_line = "PORT 10,0,0,5,195,80\r\n";
    const char *epsv_line = "229 Entering EPSV (|||50000|)\r\n";
    const char *bad_line = "PORT 10,0,0,5,195,80\n";

    /*
     * A client's PORT and a server's 229, with the region each form replaces:
     * for PORT the whole argument from offset 5, for 229 only the digits
     * inside the parentheses. The third is the refusal a bare LF earns - RFC
     * 959 makes the control connection Telnet NVT, so a line that does not
     * end CRLF is already not what the protocol says.
     */
    CHECK (bpa_ftp_parse ((const u8 *) port_line, 22, 0, &r) == 0
	     && r.form == BPA_FTP_FORM_PORT && r.arg_off == 5
	     && r.arg_len == 15 && r.addr == 0x0a000005 && r.port == 50000
	     && bpa_ftp_parse ((const u8 *) epsv_line, 31, 1, &r) == 0
	     && r.form == BPA_FTP_FORM_229 && r.arg_off == 22
	     && r.arg_len == 5 && r.port == 50000
	     && bpa_ftp_parse ((const u8 *) bad_line, 21, 0, &r) == -1,
	   "bpa_ftp_parse names the region to replace and refuses a bare LF: "
	   "form=%u arg %u+%u addr=%08x port=%u",
	   r.form, r.arg_off, r.arg_len, r.addr, r.port);

    /*
     * A keyword on a second line. The ALG leaves it alone - see bpa_ftp.h -
     * and this is what tells "saw nothing" from "declined to act".
     */
    CHECK (bpa_ftp_keyword_later (
	     (const u8 *) "200 OK\r\n227 Entering (10,0,0,5,195,80)\r\n", 39,
	     1)
	       == 1
	     && bpa_ftp_keyword_later (
		  (const u8 *) "227 Entering (10,0,0,5,195,80)\r\n", 32, 1)
		  == 0,
	   "bpa_ftp_keyword_later sees a keyword on a LATER line only");
  }
}

int
main (void)
{
  bpa_session_t s;

  printf ("=== FTP ALG sequence arithmetic ===\n\n");

  seq_boundary_cases ();
  printf ("\n");
  is_control_cases ();
  text_cases ();

  printf ("\n--- the correction, subscriber -> internet ---\n");

  /* A rewrite at stream position 1000 lengthened the payload by 8 bytes. */
  s = session (1000, 0, 8);
  expect_seq ("a segment sent after the rewrite is shifted", &s, 1200, 1, 1208);

  /*
   * The half a lab cannot stage on demand: the subscriber retransmits data it
   * sent BEFORE the rewrite. It must carry the offset that was in force when
   * it was first sent, or the server sees the same bytes at two different
   * stream positions.
   */
  expect_seq ("a retransmission from before the rewrite is not shifted", &s,
	      900, 0, 900);

  /* Wrapped, with the rewrite just below 2^32. */
  s = session (0xffffff00u, 0, 8);
  expect_seq ("a segment after a rewrite that wrapped is still shifted", &s,
	      0x00000100u, 1, 0x00000108u);
  expect_seq ("and one from before it is still not", &s, 0xfffffe00u, 0,
	      0xfffffe00u);

  /*
   * A session no ALG has touched must be left completely alone. Every TCP
   * session on the box reaches this function, and all but a handful of them
   * have never been rewritten.
   *
   * There used to be TWO cases here - "no rewrite" and "a rewrite of zero
   * bytes" - because a flag separated them. They are one case now, and that
   * is the point rather than a loss: with the flag gone, "untouched" and
   * "touched but not drifted" are the same state and there is nothing left
   * that can disagree about which it is. A shift of zero would still rewrite
   * the checksum, which is a write to every segment of every FTP connection
   * for no reason, so the assertion that nothing is written still earns its
   * place.
   */
  s = session (1000, 0, 0);
  expect_seq ("a session with no drift is untouched", &s, 1200, 0, 1200);

  /* A rewrite that SHORTENED the payload: a shorter public address than the
   * private one is perfectly possible (100.64.0.100 -> 45.1.1.1). */
  s = session (1000, 0, -4);
  expect_seq ("a rewrite that shortened the payload shifts backwards", &s,
	      1200, 1, 1196);

  printf ("\n--- the correction, internet -> subscriber ---\n");

  s = session (1000, 0, 8);
  /* The remote acknowledges the stream as we lengthened it; the subscriber
   * only knows its own numbering. */
  expect_ack ("an acknowledgement past the rewrite is shifted back", &s, 1208,
	      TCP_FLAG_ACK, 1, 1200);
  expect_ack ("one from before it is not", &s, 900, TCP_FLAG_ACK, 0, 900);

  /* A segment with no ACK flag carries no acknowledgement number worth the
   * name; shifting the field anyway corrupts a SYN. */
  expect_ack ("a segment without the ACK flag is left alone", &s, 1208,
	      TCP_FLAG_SYN, 0, 1208);

  /*
   * A session whose pool has since had the ALG switched off. The drift
   * already happened, so the correction has to keep being applied - otherwise
   * turning the knob off breaks exactly the connections it was meant to stop
   * interfering with. The correction lives on the SESSION for this reason,
   * and not on the pool.
   */
  s = session (1000, 0, 8);
  expect_ack ("a session already drifting is still corrected", &s, 1208,
	      TCP_FLAG_ACK, 1, 1200);

  s = session (1000, 0, 0);
  expect_ack ("a session that never drifted is not", &s, 1208, TCP_FLAG_ACK, 0,
	      1208);

  /*
   * The SECOND term of "off_after || off_before", which nothing above reaches.
   *
   * Every other reverse adjustment in this file has a non-zero off_after, so
   * the disjunction is answered by its first term and the second is never the
   * reason the branch is taken. Measured: with the guard cut down to
   * `if (rev->off_after)` all 58 checks of this file passed, and that is a
   * live defect - an acknowledgement landing BEFORE the correction point of a
   * session whose later rewrite took the drift back to zero is expressed in a
   * stream that was 8 bytes longer, and goes back to the subscriber
   * uncorrected.
   *
   * 900 is before the correction point, so the delta is off_before and not
   * off_after, which is the only arrangement that puts the second term of the
   * disjunction on the path.
   */
  s = session (1000, 8, 0);
  expect_ack ("one before the correction point of a session whose drift went "
	      "back to zero is still shifted",
	      &s, 900, TCP_FLAG_ACK, 1, 892);

  printf ("\n--- a control connection rewritten TWICE ---\n");
  /*
   * Two PORT commands on one control connection, which is ordinary: the
   * offset before the second rewrite is already 8, and after it 16. Until
   * this case existed every assertion above ran with off_before == 0, which
   * made the subtraction in the acknowledgement path unobservable - removing
   * it entirely changed no result. That is the shape of a test that passes
   * without testing.
   */
  s = session (2000, 8, 16);

  expect_seq ("a segment before the second rewrite carries the first offset",
	      &s, 1500, 1, 1508);
  expect_seq ("and one after it carries both", &s, 2500, 1, 2516);

  expect_ack ("an acknowledgement before the second rewrite loses 8", &s, 1908,
	      TCP_FLAG_ACK, 1, 1900);
  expect_ack ("and one after it loses 16", &s, 2516, TCP_FLAG_ACK, 1, 2500);

  /*
   * The boundary the subtraction exists for. This acknowledgement is past the
   * rewrite position in the INTERNET's numbering and before it in the
   * subscriber's, which is the whole window where the two disagree - exactly
   * as wide as the drift that was already in force. Compared without removing
   * that drift first, this segment is corrected by 16 instead of 8 and the
   * subscriber sees eight bytes it never sent acknowledged.
   */
  expect_ack ("an acknowledgement inside the rewritten line uses the offset "
	      "in force when those bytes were sent",
	      &s, 2004, TCP_FLAG_ACK, 1, 1996);

  return bpa_test_verdict (checks, failures);
}
