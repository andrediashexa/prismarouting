/*
 * bpa_rate.h - the subscriber's creation-rate bucket.
 *
 * WHY IT IS A HEADER OF ITS OWN. It is the rule an operator buys when they
 * set `max-rate` on a pool: how fast one subscriber may open new sessions,
 * and therefore how quickly a single infected CPE can fill a box that every
 * other customer is also behind. It touches two fields and a clock and
 * nothing else - no session, no pool, no VPP - so it belongs where a harness
 * can call it, and test/test_bpa_rate.c does.
 *
 * It takes the two fields rather than the subscriber so that this file needs
 * nothing from bpa_session.h, which needs VPP.
 */

#ifndef __included_bpa_rate_h__
#define __included_bpa_rate_h__

/*
 * One token out of the bucket. Returns 0 when there was none, which is a
 * refusal.
 *
 * Refilled by ELAPSED TIME rather than on a timer, so a subscriber that has
 * been quiet costs nothing to keep: the bucket is only ever touched when it
 * asks for a session. Capped at one second's worth, which is the burst the
 * operator is agreeing to when they set the rate.
 *
 * FIRST ASK STARTS FULL, so a subscriber is not refused for the accident of
 * having had its record created a moment ago - `*last == 0` is that record's
 * first question and not a subscriber who has been waiting since the epoch.
 *
 * A CLOCK THAT DID NOT MOVE ADDS NOTHING, and one that went backwards adds
 * nothing either: `dt > 0` is the only case that refills. VPP's clock is
 * monotonic, so going backwards is not a case this box produces - what the
 * test asserts is that the arithmetic does not invent tokens if it ever
 * does, because tokens invented are sessions this rule was set to refuse.
 */
static_always_inline int
bpa_rate_take (f32 *tokens, f64 *last, u32 rate, f64 now)
{
  f32 fill = (f32) rate;

  if (PREDICT_FALSE (*last == 0.))
    {
      *last = now;
      *tokens = fill;
    }
  else
    {
      f64 dt = now - *last;

      if (dt > 0.)
	{
	  *last = now;
	  *tokens += (f32) (dt * (f64) rate);
	  if (*tokens > fill)
	    *tokens = fill;
	}
    }

  if (*tokens < 1.f)
    return 0;
  *tokens -= 1.f;
  return 1;
}

#endif /* __included_bpa_rate_h__ */
