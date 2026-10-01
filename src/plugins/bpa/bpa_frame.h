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

/*
 * Whether a whole, plausible message starts at `buf` (#547): it frames, and
 * the next thing after it is either the end of what is buffered or another
 * frame. On syslog the message itself must start "<": a length header alone
 * is two digits and a space, which the middle of a line can hold by chance.
 */
static_always_inline int
bpa_frame_plausible (int is_syslog, const u8 *buf, u32 len)
{
  u32 n = bpa_frame_len (is_syslog, buf, len), h = 0;

  if (!n)
    return 0;
  if (is_syslog)
    {
      while (h < len && buf[h] != ' ')
	h++;
      if (h + 1 >= len || buf[h + 1] != '<')
	return 0;
    }
  else if (buf[0] != 0 || buf[1] != 10) /* IPFIX version 10 */
    return 0;
  /* A next message too short to read a header from is the end of the
   * buffer, not evidence against this one. */
  return n >= len || len - n < 8
	 || bpa_frame_len (is_syslog, buf + n, len - n) != 0;
}

/*
 * Where the first plausible message in `buf` begins: 0 when the front is one,
 * `len` when nothing in the buffer is.
 *
 * A front that does not frame was left as it was, for the reason at the top
 * of this file - and that made it permanent (#547). On the #475 bench a spill
 * written by 0.140.2 began mid-line; every release since replayed it on each
 * connection, could drop nothing behind it because the front never framed,
 * wrote the whole buffer - sent bytes and all - back to the file, and sent it
 * again on the next connection: 55 million duplicate records at the
 * collector in three days. Bytes that cannot be framed are lost either way;
 * skipping to the next message is what keeps the ones behind them from being
 * sent for ever.
 */
static_always_inline u32
bpa_frame_resync (int is_syslog, const u8 *buf, u32 len)
{
  u32 i;

  for (i = 0; i < len; i++)
    if (bpa_frame_plausible (is_syslog, buf + i, len - i))
      return i;
  return len;
}

#endif /* __included_bpa_frame_h__ */
