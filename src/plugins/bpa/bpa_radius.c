/*
 * bpa_radius.c - RADIUS accounting, read for one thing only: who is on an
 * address (#21).
 *
 * WHAT THIS IS NOT. It is not a RADIUS server. It authenticates nobody,
 * authorises nobody, and answers no Access-Request: a provider's BNG already
 * does that, and this box is a second listener on the accounting stream the
 * BNG is already sending. Everything below reads an Accounting-Request (RFC
 * 2866) and decides two things - is it genuine, and which subscriber is on
 * which address.
 *
 * GENUINE IS CHECKED AND NOT ASSUMED. The Request Authenticator is MD5 over
 * the packet with the authenticator field zeroed and the shared secret
 * appended, and a packet that does not match it is DROPPED without a reply.
 * Without that check anybody who can reach the port can name any subscriber
 * on any address, which would make this box's compliance log a thing that
 * takes dictation.
 *
 * Standalone: the parse and the digest read their arguments and nothing else.
 * The socket is the plugin's half.
 */
#include <string.h>

#ifdef BPA_STANDALONE
#include "bpa_shim.h"
#else
#include "bpa.h"
#endif
#include "bpa_md5.h"
#include "bpa_radius.h"

/*
 * Is this an Accounting-Request this box will act on?
 *
 * Returns BPA_RADIUS_OK and fills `out` when it is; BPA_RADIUS_MALFORMED when
 * the packet is not one at all; BPA_RADIUS_BAD_AUTH when it is well formed and
 * the authenticator does not match the secret, which is a DIFFERENT answer
 * because the two need different counters: the first is somebody's broken
 * client and the second is somebody else's box, or this one's secret being
 * wrong.
 */
int
bpa_radius_acct_parse (const u8 *pkt, u32 len, const char *secret,
		       bpa_radius_acct_t *out)
{
  u8 want[16], *scratch;
  u8 buf[4096];
  u32 at, slen;

  if (len < 20 || len > sizeof (buf))
    return BPA_RADIUS_MALFORMED;
  /* Code 4 is Accounting-Request. Anything else on this port is somebody
   * else's traffic and is not this box's business. */
  if (pkt[0] != 4)
    return BPA_RADIUS_MALFORMED;
  if ((u32) ((pkt[2] << 8) | pkt[3]) != len)
    return BPA_RADIUS_MALFORMED;

  /*
   * RFC 2866 section 3: the Request Authenticator is
   * MD5(Code + Identifier + Length + 16 zero octets + Attributes + Secret).
   */
  slen = (u32) strlen (secret ? secret : "");
  if (20 + slen > sizeof (buf))
    return BPA_RADIUS_MALFORMED;
  scratch = buf;
  memcpy (scratch, pkt, len);
  memset (scratch + 4, 0, 16);
  memcpy (scratch + len, secret ? secret : "", slen);
  bpa_md5 (scratch, len + slen, want);
  if (memcmp (want, pkt + 4, 16) != 0)
    return BPA_RADIUS_BAD_AUTH;

  memset (out, 0, sizeof (*out));
  out->id = pkt[1];
  memcpy (out->request_authenticator, pkt + 4, 16);

  for (at = 20; at + 2 <= len;)
    {
      u8 type = pkt[at], alen = pkt[at + 1];
      const u8 *val = pkt + at + 2;
      u32 vlen;

      /* An attribute is at least its own header, and one that claims to run
       * past the packet is a packet this box does not read further: a parser
       * that clamps here reads whatever follows as an attribute. */
      if (alen < 2 || at + alen > len)
	return BPA_RADIUS_MALFORMED;
      vlen = (u32) alen - 2;

      switch (type)
	{
	case 1: /* User-Name */
	  {
	    u32 n = vlen < BPA_IDENTITY_MAX - 1 ? vlen : BPA_IDENTITY_MAX - 1;

	    memcpy (out->user, val, n);
	    out->user[n] = 0;
	    if (n < vlen)
	      out->user_truncated = 1;
	  }
	  break;
	case 8: /* Framed-IP-Address */
	  if (vlen == 4)
	    {
	      out->framed_ip = ((u32) val[0] << 24) | ((u32) val[1] << 16)
			       | ((u32) val[2] << 8) | val[3];
	      out->have_ip = 1;
	    }
	  break;
	case 40: /* Acct-Status-Type */
	  if (vlen == 4)
	    {
	      out->status = ((u32) val[0] << 24) | ((u32) val[1] << 16)
			    | ((u32) val[2] << 8) | val[3];
	      out->have_status = 1;
	    }
	  break;
	default:
	  break;
	}
      at += alen;
    }

  if (!out->have_status)
    return BPA_RADIUS_MALFORMED;
  return BPA_RADIUS_OK;
}

/*
 * The Accounting-Response for a request this box accepted.
 *
 * RFC 2866: the Response Authenticator is MD5 over the response with the
 * REQUEST's authenticator in its place, plus the secret. A NAS that gets no
 * reply retransmits until it gives up and then logs this box as a dead
 * accounting server, so answering is not politeness - it is the difference
 * between a provider's accounting working and their monitoring going off.
 *
 * Returns the number of bytes written, which is always 20: a response carries
 * no attributes.
 */
u32
bpa_radius_acct_response (u8 *out, u32 cap, const bpa_radius_acct_t *req,
			  const char *secret)
{
  /*
   * BIG ENOUGH FOR ANY SECRET THIS BOX CAN HOLD, which 128 was not (#425).
   *
   * bpa.api declares `string secret[128]`, so the secret reaching here is up
   * to 128 bytes, and this scratch carries the 20-byte response header in
   * front of it: 148. At 128 the guard below refused every secret longer
   * than 108 - and refusing here is not a refusal an operator ever sees. The
   * packet had already VERIFIED and the accounting had already been applied;
   * what was lost was the Accounting-Response, so the NAS retransmitted for
   * ever and logged this box as dead while its records were arriving.
   *
   * Measured: a 108-character secret answered, 109 did not, and the
   * boundary is exactly 20 + slen > 128.
   */
  u8 buf[20 + BPA_RADIUS_SECRET_MAX];
  u32 slen = (u32) strlen (secret ? secret : "");

  /* Kept, and now unreachable for any secret the API can deliver: a guard
     that cannot fire is cheaper than a buffer nobody bounds. */
  if (cap < 20 || 20 + slen > sizeof (buf))
    return 0;
  out[0] = 5; /* Accounting-Response */
  out[1] = req->id;
  out[2] = 0;
  out[3] = 20;
  memcpy (out + 4, req->request_authenticator, 16);
  memcpy (buf, out, 20);
  memcpy (buf + 20, secret ? secret : "", slen);
  bpa_md5 (buf, 20 + slen, out + 4);
  return 20;
}


#ifndef BPA_STANDALONE

#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

bpa_radius_listener_t bpa_radius;

/*
 * Listen for the accounting a BNG is already sending.
 *
 * A SOCKET AND NOT A GRAPH NODE. The packets arrive on the management path,
 * not on the dataplane, and they are a few a second: a node would put a
 * parser for somebody else's protocol on the forwarding path to save nothing.
 * It is read from the same process that drains the log queue, which is where
 * every other thing this plugin does with a socket already lives.
 *
 * NON-BLOCKING, for the reason the exporter's socket is: a process that
 * blocks here stops draining the compliance log, and the log is what this
 * feature exists to improve.
 */
/* not a unit: it opens a UDP socket and binds it, and it is not compiled at all under
 * BPA_STANDALONE - which is what leaves the packet arithmetic in this file
 * testable. lab/syslog.sh drives it with a NAS that speaks RFC 2866 */
clib_error_t *
bpa_radius_enable (u16 port, const char *secret, int enable)
{
  bpa_radius_listener_t *r = &bpa_radius;
  struct sockaddr_in a;
  int fd, one = 1;

  if (r->fd >= 0)
    {
      close (r->fd);
      r->fd = -1;
    }
  r->enabled = 0;
  vec_free (r->secret);
  if (!enable)
    return 0;

  if (!secret || !secret[0])
    return clib_error_return (
      0, "a shared secret is required: without one this box cannot tell an "
	 "accounting packet from anybody's packet, and would take dictation "
	 "about who is on which address");

  fd = socket (AF_INET, SOCK_DGRAM, 0);
  if (fd < 0)
    return clib_error_return (0, "socket: %s", strerror (errno));
  setsockopt (fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof (one));
  if (fcntl (fd, F_SETFL, O_NONBLOCK) < 0)
    {
      close (fd);
      return clib_error_return (0, "O_NONBLOCK: %s", strerror (errno));
    }
  clib_memset (&a, 0, sizeof (a));
  a.sin_family = AF_INET;
  a.sin_addr.s_addr = INADDR_ANY;
  a.sin_port = clib_host_to_net_u16 (port ? port : BPA_RADIUS_ACCT_PORT);
  if (bind (fd, (struct sockaddr *) &a, sizeof (a)) < 0)
    {
      clib_error_t *e = clib_error_return (0, "bind %u: %s",
					   port ? port : BPA_RADIUS_ACCT_PORT,
					   strerror (errno));
      close (fd);
      return e;
    }

  r->fd = fd;
  r->port = port ? port : BPA_RADIUS_ACCT_PORT;
  r->secret = format (0, "%s%c", secret, 0);
  r->enabled = 1;
  return 0;
}

/*
 * Read whatever arrived, act on it, answer it.
 *
 * BOUNDED PER CALL. The loop takes at most BPA_RADIUS_PER_SERVICE datagrams
 * and comes back for the rest next time: a box being flooded on this port
 * must not be a box that stops draining its log, which is the same argument
 * the queue ceiling makes one layer down.
 *
 * A Start or an Interim WITH an address names the subscriber on it; a Stop
 * takes that name away. An Interim without an address changes nothing - it is
 * a session this box cannot place, and guessing which address it meant is how
 * one subscriber comes to be named in another's record.
 */
/* not a unit: it reads from that socket inside VPP's main loop and reaches into the
 * identity table under the barrier; like bpa_radius_enable() it is outside
 * the standalone build, and lab/syslog.sh is where it is exercised */
void
bpa_radius_service (void)
{
  bpa_radius_listener_t *r = &bpa_radius;
  bpa_main_t *bm = &bpa_main;
  u32 taken = 0;

  if (!r->enabled || r->fd < 0)
    return;

  while (taken++ < BPA_RADIUS_PER_SERVICE)
    {
      u8 pkt[4096], reply[32];
      struct sockaddr_in from;
      socklen_t flen = sizeof (from);
      bpa_radius_acct_t acct;
      ssize_t n;
      int rc, changed = 0;

      n = recvfrom (r->fd, pkt, sizeof (pkt), 0, (struct sockaddr *) &from,
		    &flen);
      if (n <= 0)
	return;

      r->n_received++;
      rc = bpa_radius_acct_parse (pkt, (u32) n, (const char *) r->secret,
				  &acct);
      if (rc == BPA_RADIUS_BAD_AUTH)
	{
	  r->n_bad_auth++;
	  continue; /* no reply: see bpa_radius_acct_parse */
	}
      if (rc != BPA_RADIUS_OK)
	{
	  r->n_malformed++;
	  continue;
	}

      if (acct.have_ip)
	{
	  vlib_worker_thread_barrier_sync (bm->vlib_main);
	  if (acct.status == BPA_ACCT_STOP)
	    changed = bpa_identity_remove (&bm->identity, acct.framed_ip);
	  else if (acct.user[0])
	    changed = bpa_identity_upsert (&bm->identity, acct.framed_ip,
					   acct.user);
	  vlib_worker_thread_barrier_release (bm->vlib_main);
	  if (changed)
	    {
	      bm->identity.read_at = unix_time_now ();
	      r->n_applied++;
	    }
	}

      {
	u32 rn = bpa_radius_acct_response (reply, sizeof (reply), &acct,
					   (const char *) r->secret);
	if (rn)
	  {
	    if (sendto (r->fd, reply, rn, 0, (struct sockaddr *) &from, flen)
		== (ssize_t) rn)
	      r->n_answered++;
	    else
	      r->n_unanswered++;
	  }
      }
    }
}

#endif /* not BPA_STANDALONE */
