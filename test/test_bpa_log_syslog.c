/*
 * The syslog line, byte for byte (#20).
 *
 * WHAT THIS FILE IS FOR. A second transport for the compliance log is only
 * worth having if the two logs can be laid beside each other, and that is a
 * property of the LINE: the same instant at the same resolution, the same
 * fields under names a collector can parse without a regular expression, and
 * a refusal rather than a truncation. None of that needs a socket, a
 * collector or a VPP, and all of it is invisible in a capture that parses -
 * a wrong public address is a well-formed line and a wrong answer to a court
 * order.
 *
 * INCLUDED rather than linked, the way every harness here is: the file is
 * standalone under BPA_STANDALONE, so this compiles with plain cc.
 */
#include <stdio.h>
#include <string.h>

#include "bpa_shim.h"
#include "bpa_log_syslog.c"

static int fail;

#define CHECK(cond, ...)                                                      \
  do                                                                          \
    {                                                                         \
      if (cond)                                                               \
	printf ("  [ ok ] " __VA_ARGS__), printf ("\n");                      \
      else                                                                    \
	fail++, printf ("  FAIL: " __VA_ARGS__), printf ("\n");               \
    }                                                                         \
  while (0)

/* 2026-09-18T02:40:59.123Z, as seconds since the epoch. */
#define WALL 1789699259.123

int
main (void)
{
  char line[1024], ts[32], esc[64];
  u32 n;

  printf ("=== the syslog line this box writes for a block event ===\n");

  printf ("\n--- the timestamp is the IPFIX instant, to the millisecond ---\n");
  n = bpa_syslog_timestamp (ts, sizeof (ts), WALL);
  CHECK (n && strcmp (ts, "2026-09-18T02:40:59.123Z") == 0,
	 "UTC, RFC 3339, milliseconds: %s", ts);
  CHECK (bpa_syslog_timestamp (ts, 8, WALL) == 0,
	 "and a buffer that cannot hold it is refused, not truncated");

  printf ("\n--- an event this box does not know has no MSGID ---\n");
  CHECK (strcmp (bpa_syslog_msgid (16), "BLOCK-ALLOC") == 0
	   && strcmp (bpa_syslog_msgid (17), "BLOCK-RELEASE") == 0
	   && strcmp (bpa_syslog_msgid (12), "PORTS-EXHAUSTED") == 0,
	 "the three natEvents this box emits carry a name a person filters on");
  CHECK (bpa_syslog_msgid (18) == 0 && bpa_syslog_msgid (0) == 0,
	 "and anything else answers nothing, so the caller refuses the line");

  printf ("\n--- RFC 5424 6.3.3: three characters are escaped, and only three "
	  "---\n");
  n = bpa_syslog_escape (esc, sizeof (esc), "a\"b\\c]d e");
  CHECK (n && strcmp (esc, "a\\\"b\\\\c\\]d e") == 0,
	 "quote, backslash and bracket: %s", esc);
  CHECK (bpa_syslog_escape (esc, 4, "abcdefgh") == 0,
	 "and a value that does not fit is refused whole");

  printf ("\n--- one allocation, byte for byte ---\n");
  n = bpa_syslog_line (line, sizeof (line), WALL, 16, 0x64400005, 0xC8960010,
		       1024, 1027, 0, "cgnat-sp-01", 44444, 134, "joao.silva@isp", 0);
  CHECK (n == strlen (line)
	   && strcmp (line,
		      "<134>1 2026-09-18T02:40:59.123Z cgnat-sp-01 "
		      "prisma-cgnat - BLOCK-ALLOC "
		      "[cgnat@44444 natEvent=\"16\" subscriber=\"100.64.0.5\" "
		      "public=\"200.150.0.16\" portStart=\"1024\" "
		      "portEnd=\"1027\" pool=\"0\" subscriberId=\"joao.silva@isp\"] "
		      "100.64.0.5 -> 200.150.0.16 ports 1024-1027 pool 0")
	     == 0,
	 "the line is what a collector will store:\n         %s", line);

  printf ("\n--- and one release, which reads as one to a person ---\n");
  n = bpa_syslog_line (line, sizeof (line), WALL, 17, 0x64400005, 0xC8960010,
		       1024, 1027, 3, "cgnat-sp-01", 44444, 134, "joao.silva@isp", 0);
  CHECK (n && strstr (line, "BLOCK-RELEASE ") != 0
	   && strstr (line, "natEvent=\"17\"") != 0
	   && strstr (line, "100.64.0.5 released 200.150.0.16") != 0
	   && strstr (line, "pool=\"3\"") != 0,
	 "the MSGID, the field and the sentence agree about what happened");

  printf ("\n--- a line that would not fit is refused, never cut ---\n");
  CHECK (bpa_syslog_line (line, 40, WALL, 16, 0x64400005, 0xC8960010, 1024,
			  1027, 0, "cgnat-sp-01", 44444, 134, "joao.silva@isp", 0) == 0,
	 "a collector storing half a record has stored a wrong one");
  CHECK (bpa_syslog_line (line, sizeof (line), WALL, 18, 0x64400005,
			  0xC8960010, 1024, 1027, 0, "h", 44444, 134, "joao.silva@isp", 0) == 0,
	 "and so is an event with no name, rather than one invented here");

  printf ("\n--- the enterprise number is the operator's, and it is in the "
	  "SD-ID ---\n");
  n = bpa_syslog_line (line, sizeof (line), WALL, 16, 0x64400005, 0xC8960010,
		       1024, 1027, 0, "h", 9, 134, "joao.silva@isp", 0);
  CHECK (n && strstr (line, "[cgnat@9 ") != 0,
	 "RFC 5424 allows a bare SD-ID only to IANA, so this box asks");

  printf ("\n--- the priority is the caller's, and it reaches the wire ---\n");
  n = bpa_syslog_line (line, sizeof (line), WALL, 16, 0x64400005, 0xC8960010,
		       1024, 1027, 0, "h", 9, 190, "joao.silva@isp", 0);
  CHECK (n && strncmp (line, "<190>1 ", 7) == 0,
	 "facility and severity are one number and this file does not pick it");

  printf ("\n--- a hostname carrying a delimiter cannot break the SD ---\n");
  n = bpa_syslog_line (line, sizeof (line), WALL, 16, 0x64400005, 0xC8960010,
		       1024, 1027, 0, "od]d\"one", 9, 134, "joao.silva@isp", 0);
  CHECK (n && strstr (line, "od\\]d\\\"one") != 0,
	 "escaped where it stands: %s", line);

  printf ("\n--- who held the address, and what is said when nobody knows ---\n");
  n = bpa_syslog_line (line, sizeof (line), WALL, 16, 0x64400005, 0xC8960010,
		       1024, 1027, 0, "h", 9, 134, 0, 0);
  CHECK (n && strstr (line, "subscriberId=\"unknown\"") != 0,
	 "no identity is the unknown MARKER, never an empty field: a collector "
	 "must tell a box that does not know from one that did not say");
  n = bpa_syslog_line (line, sizeof (line), WALL, 16, 0x64400005, 0xC8960010,
		       1024, 1027, 0, "h", 9, 134, "", 0);
  CHECK (n && strstr (line, "subscriberId=\"unknown\"") != 0,
	 "and so is an empty one, which is what a truncated table yields");
  n = bpa_syslog_line (line, sizeof (line), WALL, 16, 0x64400005, 0xC8960010,
		       1024, 1027, 0, "h", 9, 134, "od]d\"name", 0);
  CHECK (n && strstr (line, "od\\]d\\\"name") != 0,
	 "an identity comes from a provisioning system, so it is escaped "
	 "where it stands: %s",
	 line);

  printf ("\n--- the boot marker, in the same grammar as a block line ---\n");
  n = bpa_syslog_marker_line (line, sizeof (line), WALL, 1789600000.0,
			      "cgnat-sp-01", 44444, 134);
  CHECK (n == strlen (line)
	   && strcmp (line,
		      "<134>1 2026-09-18T02:40:59.123Z cgnat-sp-01 "
		      "prisma-cgnat - BOOT-MARKER "
		      "[cgnat@44444 bootTime=\"2026-09-16T23:06:40.000Z\" "
		      "observedAt=\"2026-09-18T02:40:59.123Z\"] "
		      "this box has been up since 2026-09-16T23:06:40.000Z")
	     == 0,
	 "a collector reads which incarnation of the box it is reading:"
	 "\n         %s",
	 line);
  CHECK (bpa_syslog_marker_line (line, 40, WALL, 1789600000.0, "h", 9, 134)
	   == 0,
	 "and a marker that would not fit is refused like any other line");

  printf ("\n--- a NAT64 subscriber, named by its delegated prefix (#485) ---\n");
  {
    bpa_log_sub6_t v6 = { { 0x20, 0x01, 0x0d, 0xb8, 0x12, 0x00, 0xab }, 56 };
    char sub[64];

    n = bpa_syslog_line (line, sizeof (line), WALL, 16, 171, 0xCB007103,
			 1024, 1087, 2, "cgnat-sp-01", 44444, 134, "ana@isp",
			 &v6);
    CHECK (n && strstr (line, "subscriber=\"2001:db8:1200:ab00::/56\" ") != 0
	     && strstr (line, "] 2001:db8:1200:ab00::/56 -> 203.0.113.3 ") != 0
	     && strstr (line, "0.0.0.171") == 0,
	   "the prefix in the field and the sentence, never the index:\n"
	   "         %s", line);
    CHECK (bpa_log_subscriber_str (sub, sizeof (sub), 0x64400005, 0) == 10
	     && !strcmp (sub, "100.64.0.5"), "IPv4 as before: %s", sub);
    v6.len = 0;
    CHECK (bpa_log_subscriber_str (sub, sizeof (sub), 0x64400005, &v6)
	     && !strcmp (sub, "100.64.0.5"), "length 0 is an IPv4 subscriber");
    v6.len = 129;
    CHECK (bpa_log_subscriber_str (sub, sizeof (sub), 0, &v6) == 0,
	   "a length past 128 is refused, not printed");
    v6.len = 56;
    CHECK (bpa_log_subscriber_str (sub, 10, 0, &v6) == 0,
	   "and a buffer it does not fit is refused whole");
  }

  printf ("\n%s\n", fail ? "FAILURES" : "ALL TESTS PASSED");
  return fail ? 1 : 0;
}
