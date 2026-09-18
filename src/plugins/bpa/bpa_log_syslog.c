/*
 * bpa_log_syslog.c - the same block events as RFC 5424 syslog lines.
 *
 * WHY A SECOND TRANSPORT AT ALL. The event set does not change and must not:
 * one record per allocation and release, never per flow. What changes is who
 * can receive it. Many ISP collectors speak syslog and nothing else, and a
 * product that cannot be read by the collector a provider already runs is
 * disqualified for a reason that has nothing to do with the quality of its
 * CGNAT (#20).
 *
 * WHAT THIS FILE IS, AND IS NOT. It builds the LINE and nothing else: no
 * socket, no queue, no clock of its own. That is deliberate and it is what
 * makes the format testable without a collector, a box or a VPP - the half of
 * an exporter that is easy to get quietly wrong is the one that decides what
 * a court-ordered lookup will read, and a wrong field is not visible in a
 * capture that parses.
 *
 * Standalone for the same reason bpa_log_tcp.c's assembly half is: nothing
 * below reads a packet, a session or the graph.
 */
#include <stdio.h>
#include <string.h>
#include <time.h>

#ifdef BPA_STANDALONE
#include "bpa_shim.h"
#else
#include "bpa.h"
#endif
#include "bpa_log.h"

/*
 * The MSGID, one per natEvent this box emits.
 *
 * A NAME AND NOT THE NUMBER, because the number is already in the structured
 * data and a MSGID is what a collector filters on: `natEvent="16"` in a search
 * box is a thing somebody looks up, and BLOCK-ALLOC is not. RFC 5424 gives
 * MSGID 32 printable characters and these are well inside it.
 *
 * An event this box does not know is not invented here: it comes back as a
 * null pointer and the caller refuses the line, because a syslog message
 * naming an event the collector cannot map is worse than no message - it is a
 * record that parses, is stored, and answers a court request with a shrug.
 */
const char *
bpa_syslog_msgid (u8 event)
{
  switch (event)
    {
    case BPA_NAT_EVENT_BLOCK_ALLOC:
      return "BLOCK-ALLOC";
    case BPA_NAT_EVENT_BLOCK_DEALLOC:
      return "BLOCK-RELEASE";
    case BPA_NAT_EVENT_PORTS_EXHAUSTED:
      return "PORTS-EXHAUSTED";
    default:
      return 0;
    }
}

/*
 * RFC 5424 section 6.3.3: inside a structured-data value, `"`, `\` and `]`
 * are escaped with a backslash and nothing else is. Returns the number of
 * bytes written, or 0 when the value does not fit - a truncated SD element is
 * a line a collector rejects whole.
 */
u32
bpa_syslog_escape (char *out, u32 cap, const char *in)
{
  u32 n = 0;

  for (; *in; in++)
    {
      int esc = (*in == '"' || *in == '\\' || *in == ']');

      if (n + (u32) (esc ? 2 : 1) >= cap)
	return 0;
      if (esc)
	out[n++] = '\\';
      out[n++] = *in;
    }
  out[n] = 0;
  return n;
}

/*
 * The timestamp, RFC 3339 with milliseconds, in UTC.
 *
 * THE SAME INSTANT AND THE SAME RESOLUTION as the IPFIX record's
 * observationTimeMilliseconds, from the same `wall` the caller already has.
 * That is the whole of the requirement: a syslog line whose clock cannot be
 * compared with the IPFIX one cannot corroborate it, and corroboration is
 * what a second transport is for.
 *
 * UTC, with `Z`, and never local time: the box's timezone is an install-time
 * answer and the log outlives it.
 */
u32
bpa_syslog_timestamp (char *out, u32 cap, f64 wall)
{
  time_t secs = (time_t) wall;
  u32 ms = (u32) ((u64) (wall * 1e3) % 1000);
  struct tm tm;

  if (cap < 25 || !gmtime_r (&secs, &tm))
    return 0;
  return (u32) snprintf (out, cap, "%04d-%02d-%02dT%02d:%02d:%02d.%03uZ",
			 tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday,
			 tm.tm_hour, tm.tm_min, tm.tm_sec, ms);
}

/*
 * One RFC 5424 line for one block event. Returns its length, or 0 when the
 * line could not be built - an unknown event, a hostname that will not fit, a
 * buffer too small.
 *
 * THE ENTERPRISE NUMBER IS THE CALLER'S, and it has no default. RFC 5424
 * allows an SD-ID without an `@enterprise` suffix only for names IANA has
 * registered, and this product has none; inventing one would put a name in
 * every operator's log that means something else to whoever registers it
 * later. So `logging syslog` asks for the provider's own PEN and refuses
 * without it, and this function is handed what the operator answered.
 *
 * THE FIELDS ARE THE IPFIX RECORD'S, by name, so the two logs can be laid
 * beside each other: natEvent, the subscriber's address, the public address,
 * the port range and the pool. The MSG after the structured data repeats them
 * as a sentence for a person reading a tail, which costs bytes and is what
 * makes the difference between a log somebody can diagnose from and one they
 * have to write a parser for first.
 */
u32
bpa_syslog_line (char *out, u32 cap, f64 wall, u8 event, u32 ip_priv,
		 u32 ip_pub, u16 port_lo, u16 port_hi, u32 pool_id,
		 const char *hostname, u32 pen, u32 pri)
{
  char ts[32], host[256];
  const char *msgid = bpa_syslog_msgid (event);
  int n;

  if (!msgid || !bpa_syslog_timestamp (ts, sizeof (ts), wall))
    return 0;
  if (!bpa_syslog_escape (host, sizeof (host), hostname ? hostname : "-"))
    return 0;

  n = snprintf (
    out, cap,
    "<%u>1 %s %s prisma-cgnat - %s "
    "[cgnat@%u natEvent=\"%u\" subscriber=\"%u.%u.%u.%u\" "
    "public=\"%u.%u.%u.%u\" portStart=\"%u\" portEnd=\"%u\" pool=\"%u\"] "
    "%u.%u.%u.%u %s %u.%u.%u.%u ports %u-%u pool %u",
    pri, ts, host, msgid, pen, event,
    (ip_priv >> 24) & 0xff, (ip_priv >> 16) & 0xff, (ip_priv >> 8) & 0xff,
    ip_priv & 0xff,
    (ip_pub >> 24) & 0xff, (ip_pub >> 16) & 0xff, (ip_pub >> 8) & 0xff,
    ip_pub & 0xff, port_lo, port_hi, pool_id,
    (ip_priv >> 24) & 0xff, (ip_priv >> 16) & 0xff, (ip_priv >> 8) & 0xff,
    ip_priv & 0xff,
    event == BPA_NAT_EVENT_BLOCK_DEALLOC ? "released" : "->",
    (ip_pub >> 24) & 0xff, (ip_pub >> 16) & 0xff, (ip_pub >> 8) & 0xff,
    ip_pub & 0xff, port_lo, port_hi, pool_id);

  /* snprintf TELLS the truth about what it could not write, and a line that
   * was cut is refused rather than sent: a collector storing half a record
   * has stored a wrong one. */
  if (n < 0 || (u32) n >= cap)
    return 0;
  return (u32) n;
}
