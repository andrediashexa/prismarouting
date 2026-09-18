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
  u8 buf[128];
  u32 slen = (u32) strlen (secret ? secret : "");

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
