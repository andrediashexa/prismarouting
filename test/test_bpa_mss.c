/*
 * test_bpa_mss.c - unit test for the TCP MSS clamp.
 *
 * The clamp walks an option list it did not write, in a header whose data
 * offset it does not control, and rewrites two bytes plus a checksum. Its own
 * comment says what makes it worth testing here rather than only in a lab:
 *
 *   getting it wrong at all fails silently - the peer drops the segment and
 *   the connection hangs exactly as it did before clamping, with a counter
 *   here claiming the fix was applied.
 *
 * So most of this file is about the cases where the answer is "leave it
 * alone": no option, padding, a length byte pointing outside the header, a
 * data offset that lies, an MSS already small enough. A lab exercises one
 * path through this and cannot enumerate them.
 *
 * The checksum is never compared against the same incremental arithmetic that
 * produced it. It is compared against a full recomputation over the segment,
 * written below and independent of the shim.
 */

#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include "bpa_tcp_shim.h"
#include "../src/plugins/bpa/bpa_mss.h"

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

/*
 * A synthetic segment: TCP header, then whatever option bytes the case wants.
 * No IP header - the clamp never reads one, and the pseudo-header terms of the
 * real checksum cancel out of an incremental update, which is the whole reason
 * the update can be done without one.
 */
typedef struct
{
  tcp_header_t tcp;
  u8 opt[40];
} seg_t;

/* The independent half: a plain one's-complement sum over the header and its
 * options, with the checksum field taken out. Written the slow obvious way on
 * purpose - it exists to disagree with the clever version. */
static u16
full_checksum (const seg_t *g, unsigned bytes)
{
  const u8 *p = (const u8 *) g;
  unsigned csum_off = (unsigned) ((const u8 *) &g->tcp.checksum - p);
  uint32_t sum = 0;
  unsigned i;

  for (i = 0; i + 1 < bytes; i += 2)
    {
      if (i == csum_off)
	continue; /* the field being computed is not part of the sum */
      sum += (uint32_t) ((p[i] << 8) | p[i + 1]);
    }
  if (i < bytes)
    sum += (uint32_t) (p[i] << 8);

  while (sum >> 16)
    sum = (sum & 0xffff) + (sum >> 16);
  return (u16) ~sum;
}

/* Builds a segment whose checksum is correct before the clamp touches it, so
 * that "still correct afterwards" means something. */
static unsigned
build (seg_t *g, const u8 *opts, unsigned n_opts, unsigned data_offset_words)
{
  unsigned bytes;

  memset (g, 0, sizeof (*g));
  g->tcp.src_port = clib_host_to_net_u16 (49152);
  g->tcp.dst_port = clib_host_to_net_u16 (443);
  g->tcp.seq_number = clib_host_to_net_u32 (0x11223344);
  g->tcp.ack_number = 0;
  g->tcp.flags = TCP_FLAG_SYN;
  g->tcp.window = clib_host_to_net_u16 (64240);
  g->tcp.data_offset_and_reserved = (u8) (data_offset_words << 4);
  if (n_opts)
    memcpy (g->opt, opts, n_opts);

  bytes = sizeof (tcp_header_t) + n_opts;
  g->tcp.checksum = clib_host_to_net_u16 (full_checksum (g, bytes));
  return bytes;
}

static u16
mss_in (const seg_t *g, unsigned off)
{
  return clib_net_to_host_u16 (clib_mem_unaligned (g->opt + off, u16));
}

/*
 * One case. `want_ret` is what the clamp must return; `want_mss` is what the
 * option must hold afterwards, or 0 when the case has no MSS option to check.
 */
static void
run (const char *what, const u8 *opts, unsigned n_opts,
     unsigned data_offset_words, unsigned end_trim, u16 clamp, int want_ret,
     u16 want_mss, int mss_off)
{
  seg_t g;
  unsigned bytes = build (&g, opts, n_opts, data_offset_words);
  const u8 *end = (const u8 *) &g + bytes - end_trim;
  u16 before = g.tcp.checksum;
  int ret = bpa_tcp_mss_clamp (&g.tcp, end, clamp);

  CHECK (ret == want_ret, "%s: returned %d, expected %d", what, ret, want_ret);

  if (want_ret)
    {
      CHECK (mss_in (&g, (unsigned) mss_off) == want_mss,
	     "%s: option holds %u, expected %u", what,
	     mss_in (&g, (unsigned) mss_off), want_mss);
      /* The assertion the incremental arithmetic exists for. */
      CHECK (clib_net_to_host_u16 (g.tcp.checksum) == full_checksum (&g, bytes),
	     "%s: checksum %04x, a full recomputation says %04x", what,
	     clib_net_to_host_u16 (g.tcp.checksum), full_checksum (&g, bytes));
    }
  else
    {
      /* A clamp that declined must not have touched the packet at all. An
       * early version that returned 0 after writing was indistinguishable
       * from this one on the counter. */
      CHECK (g.tcp.checksum == before,
	     "%s: declined but changed the checksum", what);
      if (mss_off >= 0 && (unsigned) mss_off + 2 <= n_opts)
	CHECK (mss_in (&g, (unsigned) mss_off) == want_mss,
	       "%s: declined but changed the option to %u", what,
	       mss_in (&g, (unsigned) mss_off));
    }
}

/*
 * The one bound this file cannot see from a return value, run against an
 * unmapped page instead.
 *
 * "opt + 1 >= opt_end" stops the walk from reading an option's LENGTH byte
 * when only its KIND byte is inside the options. Deleting it changes no
 * verdict: the byte read is one past opt_end, and whatever it holds the
 * "opt + len > opt_end" test below stops the walk anyway - there is no len
 * small enough to fit in the nothing that is left and large enough to pass
 * "len < 2". The only difference the deletion makes is the read, so the
 * segment is placed with its last option byte hard against an unmapped page,
 * the way test_bpa_icmp.c does it, and the read becomes a SIGSEGV.
 *
 * Three NOOPs then a kind byte is how the packet is shaped: the walk has to
 * get to the very last byte of the options for the bound to be the one that
 * fires.
 */
static void
last_option_byte_against_a_guard_page (void)
{
  long pagesz = sysconf (_SC_PAGESIZE);
  static const u8 opts[] = { TCP_OPTION_NOOP, TCP_OPTION_NOOP,
			     TCP_OPTION_NOOP, TCP_OPTION_MSS };
  seg_t g;
  unsigned bytes = build (&g, opts, sizeof (opts), 6);
  u8 *region, *seg;

  /* Two pages, the second unreadable: the packet must stop at the seam. */
  region = mmap (0, 2 * (size_t) pagesz, PROT_READ | PROT_WRITE,
		 MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (region == MAP_FAILED
      || mprotect (region + pagesz, (size_t) pagesz, PROT_NONE))
    {
      printf ("  FAIL: could not place a guard page\n");
      failures++;
      return;
    }

  seg = region + pagesz - bytes;
  memcpy (seg, &g, bytes);

  /* Named BEFORE the call. Without the bound this process dies inside it, and
   * a crash with nothing printed leaves whoever reads the log to bisect the
   * file to find out which case it was. */
  printf ("  the last option byte, against a guard page\n");

  /* `end` is the seam, which is also where the data offset says the options
   * stop. A walk that reads the byte after the last option reads the page. */
  CHECK (bpa_tcp_mss_clamp ((tcp_header_t *) seg, region + pagesz, 1400) == 0,
	 "an option kind in the last option byte is not given a length byte");

  munmap (region, 2 * (size_t) pagesz);
}

int
main (void)
{
  /*
   * Unbuffered, for the guard-page case at the bottom: that one is designed to
   * die rather than to disagree, and a crash with the output still in stdio's
   * buffer names nothing.
   */
  setvbuf (stdout, NULL, _IONBF, 0);
  printf ("=== TCP MSS clamp ===\n\n");

  /* MSS 1460, four bytes: kind, len, value. */
  static const u8 mss1460[] = { TCP_OPTION_MSS, 4, 0x05, 0xb4 };
  run ("a plain MSS option is lowered", mss1460, 4, 6, 0, 1400, 1, 1400, 2);

  /* Only ever lowers. Raising announces a segment size the sender never
   * agreed to receive, which is corruption with a plausible name. */
  static const u8 mss536[] = { TCP_OPTION_MSS, 4, 0x02, 0x18 };
  run ("an MSS already below the clamp is left alone", mss536, 4, 6, 0, 1400,
       0, 536, 2);
  run ("an MSS exactly at the clamp is left alone", mss1460, 4, 6, 0, 1460, 0,
       1460, 2);

  /* A SYN with no MSS option means the sender accepts 536, which is smaller
   * than any clamp worth setting. There is nothing to fix, and inserting one
   * would lengthen the header. */
  run ("a header with no options is left alone", NULL, 0, 5, 0, 1400, 0, 0, -1);
  static const u8 win_only[] = { TCP_OPTION_WINDOW, 3, 7, TCP_OPTION_EOL };
  run ("a header with other options only is left alone", win_only, 4, 6, 0,
       1400, 0, 0, -1);

  /* Padding around the option, which is how a real SYN carries it. */
  static const u8 padded[] = { TCP_OPTION_NOOP, TCP_OPTION_NOOP,
			       TCP_OPTION_MSS,	4,
			       0x05,		0xb4,
			       TCP_OPTION_EOL,	TCP_OPTION_EOL };
  run ("NOOP padding before the option is walked", padded, 8, 7, 0, 1400, 1,
       1400, 4);

  static const u8 after_eol[] = { TCP_OPTION_EOL, 0,	     0,	   0,
				  TCP_OPTION_MSS, 4,	     0x05, 0xb4 };
  run ("an option after EOL is not reached", after_eol, 8, 7, 0, 1400, 0,
       1460, 6);

  /*
   * The EOL break, isolated. The case above pads with zeros, and a zero
   * length byte is refused by "len < 2" - so it stays green with
   * "if (kind == TCP_OPTION_EOL) break;" deleted. What follows EOL is
   * whatever the sender wrote, and a sender who writes a well-formed option
   * there has it walked unless the break is really there.
   */
  static const u8 after_eol_walkable[] = { TCP_OPTION_EOL, 4,	  0x05, 0xb4,
					   TCP_OPTION_MSS, 4,	  0x05, 0xb4 };
  run ("an option after EOL is not reached even when it is well formed",
       after_eol_walkable, 8, 7, 0, 1400, 0, 1460, 6);

  /* Malformed lists. Each of these is a packet an attacker can send, and the
   * clamp must stop rather than guess where the next option starts. */
  static const u8 len_zero[] = { TCP_OPTION_WINDOW, 0, 0, 0 };
  run ("a zero length byte stops the walk", len_zero, 4, 6, 0, 1400, 0, 0, -1);

  static const u8 len_one[] = { TCP_OPTION_WINDOW, 1, 0, 0 };
  run ("a length below the minimum stops the walk", len_one, 4, 6, 0, 1400, 0,
       0, -1);

  static const u8 len_past[] = { TCP_OPTION_WINDOW, 40,	  0,	0,
				 TCP_OPTION_MSS,   4,	  0x05, 0xb4 };
  run ("a length pointing outside the header stops the walk", len_past, 8, 7,
       0, 1400, 0, 1460, 6);

  /*
   * The case the `opt + len > opt_end` bound actually protects, which is not
   * the one above: there the walk would have run off the end and stopped by
   * itself. Here the option's LENGTH BYTE is honest but the data offset says
   * the options end two bytes into it, so the value being rewritten lies in
   * the payload. Without the bound the clamp writes an MSS into somebody's
   * data and updates the checksum to match, which is a corruption that
   * verifies.
   */
  static const u8 truncated_mss[] = { TCP_OPTION_NOOP, TCP_OPTION_NOOP,
				      TCP_OPTION_MSS,  4,
				      0x05,	       0xb4,
				      0x00,	       0x00 };
  run ("an MSS option cut short by the data offset is not rewritten",
       truncated_mss, 8, 6, 0, 1400, 0, 1460, 4);

  static const u8 mss_short[] = { TCP_OPTION_MSS, 3, 0x05, 0xb4 };
  run ("an MSS option of the wrong length is not rewritten", mss_short, 4, 6,
       0, 1400, 0, 1460, 2);

  /* A data offset larger than the packet: the header claims options that are
   * not there. Reading them is a read past the buffer. */
  run ("a data offset past the end of the packet is refused", mss1460, 4, 10,
       0, 1400, 0, 1460, 2);
  run ("and so is one past a truncated capture", mss1460, 4, 6, 2, 1400, 0,
       1460, 2);

  /*
   * A data offset smaller than the header itself: opt_end < opt.
   *
   * This one cannot be isolated and is not meant to be: when opt_end is below
   * opt the walk's own "opt < opt_end" is false on the first pass, so the
   * clamp declines whether or not the guard is there. Same for "len < 2" and
   * the zero length byte below - deleting that one does not give a wrong
   * answer, it stops the walk advancing at all, so the suite hangs instead of
   * printing a verdict. Both are noted rather than covered, because an
   * assertion that claimed to be about either would be about something else.
   */
  run ("a data offset below the minimum header is refused", mss1460, 4, 4, 0,
       1400, 0, 1460, 2);

  /* The bounds an operator may configure, asserted so that a change to either
   * is a decision rather than an accident. 536 is what RFC 1122 s4.2.2.6
   * requires every IPv4 host to accept; below it the clamp is legal and
   * useless. */
  CHECK (BPA_MSS_MIN == 536, "the floor moved from the RFC 1122 minimum");
  CHECK (BPA_MSS_MAX == 9000, "the ceiling moved from a jumbo frame");
  CHECK (BPA_MSS_OVERHEAD == 40,
	 "an IPv4 and a TCP header without options are 40 bytes");

  /* Clamping AT the floor has to work: it is the smallest value the VTY
   * accepts, so if the walk had an off-by-one at the boundary the one
   * configuration guaranteed to be legal would be the one that breaks. */
  run ("clamping to the floor works", mss1460, 4, 6, 0, BPA_MSS_MIN, 1,
       BPA_MSS_MIN, 2);

  last_option_byte_against_a_guard_page ();

  printf ("\n%u checks, %d failures\n", checks, failures);
  printf ("%s\n", failures ? "TESTS FAILED" : "ALL TESTS PASSED");
  return failures ? 1 : 0;
}
