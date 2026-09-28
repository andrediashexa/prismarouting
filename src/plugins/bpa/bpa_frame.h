/*
 * bpa_frame.h - how long the message at the front of an exporter's buffer is,
 * in either wire the stream speaks (#492).
 *
 * The TCP exporter carries IPFIX and syslog through one implementation, and
 * the one place it needs a message's length is to drop from the front of its
 * buffer the messages the collector has already taken. It read bytes 2-3 as
 * the IPFIX message length whatever the wire was. A syslog message begins
 * "211 <134>1", so bytes 2-3 are "1 " and read as 12576: once 12576 bytes had
 * gone out - about sixty boot markers on an idle box - it deleted 12576
 * bytes, which is no message boundary, and the stream after any reconnection,
 * and the spill file, began mid-message. Measured on the #475 bench (0.140.2):
 * the collector's first bytes after a restart were `34Z" observedAt=...`, and
 * an RFC 6587 collector misframes everything after that.
 *
 * Both framings, then, and 0 for "not a whole message I can read": the
 * caller stops there rather than guess, because a wrong guess corrupts the
 * stream for good.
 *
 * IPFIX (RFC 7011): the length is bytes 2-3 of the 16-byte header.
 * syslog over TCP (RFC 6587 octet counting): "<decimal length> <message>".
 */
#ifndef __included_bpa_frame_h__
#define __included_bpa_frame_h__

/* The length of the message at the front of `buf` (`len` bytes of it) on the
 * given wire, including its framing, or 0 when the front is not a readable
 * frame header. A message longer than `len` is still reported whole: the
 * caller compares it with what has been sent, not with what is buffered. */
static_always_inline u32
bpa_frame_len (int is_syslog, const u8 *buf, u32 len)
{
  u32 n = 0, i = 0;

  if (!is_syslog)
    {
      if (len < 4)
	return 0;
      n = ((u32) buf[2] << 8) | buf[3];
      return n >= 16 ? n : 0;
    }
  /* RFC 6587 caps nothing, but a syslog message this exporter writes is one
   * line of a few hundred bytes: six digits is room for a megabyte, and more
   * digits than that is not a header. */
  while (i < len && i < 7 && buf[i] >= '0' && buf[i] <= '9')
    n = n * 10 + (buf[i++] - '0');
  if (i == 0 || i == 7 || i >= len || buf[i] != ' ' || n == 0)
    return 0;
  return i + 1 + n;
}

#endif /* __included_bpa_frame_h__ */
