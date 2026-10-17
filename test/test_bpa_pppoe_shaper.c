/*
 * test_bpa_pppoe_shaper.c - a PPPoE session's plan as a queue (#610): what
 * passes, what waits, what is dropped, when a waiting packet may leave, and
 * CoDel on the queue.
 *
 * WHY THESE. Each is a way the shaper could be wrong that a throughput
 * figure would not show: a packet that jumps a queue (the order a TCP flow
 * is delivered in), a bucket that fills past its size after a quiet minute
 * (a burst the plan did not sell), a packet longer than the bucket that
 * never leaves (a stuck session), a CoDel that drops a burst instead of a
 * standing queue, and a target under one packet's time at a slow plan.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "bpa_shim.h"
#include "../src/plugins/bpa/bpa_pppoe_shaper.h"

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

static bpa_shaper_t
shaped (u32 kbit, u32 queue_ms)
{
  bpa_shaper_t s;

  memset (&s, 0xaa, sizeof (s));
  s.gen = 7;
  bpa_shaper_reset (&s);
  bpa_shaper_set (&s, kbit, queue_ms);
  return s;
}

static void
test_params (void)
{
  f64 rate;
  u32 burst, limit;

  CHECK (bpa_shaper_params (20000, 100, &rate, &burst, &limit),
	 "a 20 Mbit/s plan is a plan");
  CHECK (rate == 2500000., "20 Mbit/s is 2,500,000 bytes a second (%f)", rate);
  CHECK (burst == 5000, "the bucket is 2 ms of it: 5000 (%u)", burst);
  CHECK (limit == 250000, "the queue is 100 ms of it: 250000 (%u)", limit);

  bpa_shaper_params (64, 100, &rate, &burst, &limit);
  CHECK (burst == BPA_SHAPER_BURST_MIN,
	 "a slow plan's bucket is still two packets (%u)", burst);
  CHECK (limit == BPA_SHAPER_QUEUE_MIN,
	 "and its queue still ten packets (%u)", limit);

  bpa_shaper_params (BPA_SHAPER_KBIT_MAX, BPA_SHAPER_QUEUE_MS_MAX, &rate,
		     &burst, &limit);
  CHECK (limit == 1250000000u,
	 "10 Gbit/s for a second is 1.25 GB, which a u32 holds (%u)", limit);

  CHECK (!bpa_shaper_params (0, 100, &rate, &burst, &limit),
	 "0 kbit/s is not a plan: it is no shaper");
  CHECK (!bpa_shaper_params (BPA_SHAPER_KBIT_MAX + 1, 100, &rate, &burst,
			     &limit),
	 "past 10 Gbit/s is refused, as the policer's is");
  CHECK (!bpa_shaper_params (20000, BPA_SHAPER_QUEUE_MS_MIN - 1, &rate, &burst,
			     &limit),
	 "a queue shorter than its least is refused");
  CHECK (!bpa_shaper_params (20000, BPA_SHAPER_QUEUE_MS_MAX + 1, &rate, &burst,
			     &limit),
	 "a queue longer than its most is refused");
}

static void
test_reset_and_set (void)
{
  bpa_shaper_t s = shaped (20000, 100);

  CHECK (s.gen == 7, "a reset keeps the generation (%u)", s.gen);
  CHECK (s.owner == BPA_SHAPER_UNBOUND && s.head == BPA_SHAPER_NONE &&
	   s.tail == BPA_SHAPER_NONE && s.slot == BPA_SHAPER_NONE,
	 "and starts unbound, empty, in no slot of the calendar");
  CHECK (s.passed == 0 && s.q_pkts == 0 && s.drop_aqm == 0,
	 "with nothing counted");

  /* A CoA on a live session: the queue and the counters stay, the bucket
   * is cut to the new size, never filled. */
  bpa_shaper_refill (&s, 1.0);
  s.q_pkts = 3;
  s.q_bytes = 4500;
  s.passed = 11;
  CHECK (bpa_shaper_set (&s, 4000, 100), "a CoA to 4 Mbit/s");
  CHECK (s.q_pkts == 3 && s.q_bytes == 4500 && s.passed == 11,
	 "keeps the queue and the counters");
  CHECK (s.tokens == (f64) BPA_SHAPER_BURST_MIN,
	 "and cuts the bucket to its new size (%f)", s.tokens);
  CHECK (s.kbit == 4000 && s.limit == 50000, "the new limit is 50000 (%u)",
	 s.limit);
  bpa_shaper_set (&s, 100000, 100);
  CHECK (s.tokens == (f64) BPA_SHAPER_BURST_MIN,
	 "a faster plan does not fill the bucket (%f)", s.tokens);
  CHECK (!bpa_shaper_set (&s, 0, 100) && s.kbit == 100000,
	 "a bad rate changes nothing");
}

static void
test_refill (void)
{
  bpa_shaper_t s = shaped (20000, 100);

  bpa_shaper_refill (&s, 10.0);
  CHECK (s.tokens == 5000., "the first look starts the bucket full (%f)",
	 s.tokens);
  s.tokens = 0.;
  bpa_shaper_refill (&s, 10.001);
  CHECK (s.tokens > 2499. && s.tokens < 2501.,
	 "a millisecond at 20 Mbit/s is 2500 bytes (%f)", s.tokens);
  bpa_shaper_refill (&s, 9.0);
  CHECK (s.tokens > 2499. && s.tokens < 2501. && s.last == 10.001,
	 "a clock that went back adds nothing (%f)", s.tokens);
  bpa_shaper_refill (&s, 70.0);
  CHECK (s.tokens == 5000.,
	 "a quiet minute fills the bucket to its size, not past it (%f)",
	 s.tokens);
}

static void
test_admit (void)
{
  bpa_shaper_t s = shaped (20000, 100);
  f64 t = 1.0;

  CHECK (bpa_shaper_admit (&s, 1500, t, 1) == BPA_SHAPER_PASS,
	 "under the plan, a packet passes at once");
  CHECK (bpa_shaper_admit (&s, 1500, t, 1) == BPA_SHAPER_PASS &&
	   bpa_shaper_admit (&s, 1500, t, 1) == BPA_SHAPER_PASS,
	 "the bucket's 5000 bytes are three packets");
  CHECK (s.passed == 3 && s.bytes == 4500, "counted: 3 passed, 4500 bytes");
  CHECK (bpa_shaper_admit (&s, 1500, t, 1) == BPA_SHAPER_QUEUE,
	 "the fourth waits");
  CHECK (s.q_pkts == 1 && s.q_bytes == 1500, "in the queue");
  /* 2 ms later the bucket holds its size again, but a packet behind a
   * queue never jumps it: the order is the flow's. */
  CHECK (bpa_shaper_admit (&s, 100, t + 0.002, 1) == BPA_SHAPER_QUEUE,
	 "a small packet behind a queue waits behind it");
  CHECK (s.q_pkts == 2 && s.q_bytes == 1600 && s.q_bytes_max == 1600,
	 "the queue and its most (%u)", s.q_bytes_max);

  {
    bpa_shaper_t f = shaped (64, 100); /* the queue: 15000 bytes */
    u32 i;

    f.tokens = 0.;
    f.last = t;
    for (i = 0; i < 10; i++)
      bpa_shaper_admit (&f, 1500, t, 1);
    CHECK (f.q_bytes == 15000, "ten packets fill a 15000-byte queue");
    CHECK (bpa_shaper_admit (&f, 1, t, 1) == BPA_SHAPER_DROP_FULL &&
	     f.drop_full == 1,
	   "and one byte more is dropped, the queue full");
    CHECK (f.q_bytes == 15000, "without entering it");
  }
  {
    bpa_shaper_t b = shaped (20000, 100);

    b.tokens = 0.;
    b.last = t;
    CHECK (bpa_shaper_admit (&b, 1500, t, 0) == BPA_SHAPER_DROP_BOX &&
	     b.drop_box == 1 && b.drop_full == 0 && b.q_pkts == 0,
	   "with the box's buffers held, a packet that would wait is "
	   "dropped, counted as the box's");
    b.tokens = 5000.;
    CHECK (bpa_shaper_admit (&b, 1500, t, 0) == BPA_SHAPER_PASS,
	   "but one within the plan still passes: it holds no buffer");
  }
}

static void
test_send (void)
{
  bpa_shaper_t s = shaped (20000, 100);

  s.tokens = 1000.;
  CHECK (!bpa_shaper_may_send (&s, 1500), "1000 bytes of bucket: 1500 waits");
  CHECK (s.tokens == 1000., "and nothing is taken");
  s.tokens = 1500.;
  CHECK (bpa_shaper_may_send (&s, 1500) && s.tokens == 0.,
	 "1500 bytes of bucket: it leaves");

  /* A packet longer than the bucket - a GSO frame - leaves when the
   * bucket is full, and the rate pays the difference back. */
  s.tokens = (f64) s.burst;
  CHECK (bpa_shaper_may_send (&s, 9000), "a 9000-byte packet leaves a full "
					 "5000-byte bucket");
  CHECK (s.tokens == -4000., "which goes 4000 under (%f)", s.tokens);

  s.q_pkts = 2;
  s.q_bytes = 3000;
  bpa_shaper_sent (&s, 1500, 4000);
  bpa_shaper_sent (&s, 1500, 9000);
  CHECK (s.q_pkts == 0 && s.q_bytes == 0 && s.delayed == 2 &&
	   s.bytes == 3000,
	 "two sent: the queue empty, two delayed, 3000 bytes");
  CHECK (s.delay_sum_us == 13000 && s.delay_max_us == 9000,
	 "their waits summed and the most kept");

  s.q_pkts = 1;
  s.q_bytes = 1500;
  bpa_shaper_dropped_head (&s, 1500);
  CHECK (s.q_pkts == 0 && s.q_bytes == 0 && s.delayed == 2,
	 "a head CoDel dropped leaves the queue and is not counted sent");
}

static void
test_wait (void)
{
  bpa_shaper_t s = shaped (20000, 100);

  s.tokens = 0.;
  /* 1500 bytes at 2,500,000 a second: 600 us, six ticks of 100 */
  CHECK (bpa_shaper_wait_ticks (&s, 1500, 100e-6, 2047) == 6,
	 "1500 bytes short at 20 Mbit/s: 6 ticks (%u)",
	 bpa_shaper_wait_ticks (&s, 1500, 100e-6, 2047));
  s.tokens = 1400.;
  CHECK (bpa_shaper_wait_ticks (&s, 1500, 100e-6, 2047) == 1,
	 "100 bytes short: one tick, never none");
  s.tokens = 2000.;
  CHECK (bpa_shaper_wait_ticks (&s, 1500, 100e-6, 2047) == 1,
	 "already enough: one tick");
  s.tokens = 0.;
  CHECK (bpa_shaper_wait_ticks (&s, 9000, 100e-6, 2047) == 20,
	 "a packet longer than the bucket waits for a full bucket, 2 ms (%u)",
	 bpa_shaper_wait_ticks (&s, 9000, 100e-6, 2047));
  {
    bpa_shaper_t slow = shaped (1, 100); /* 125 bytes a second */
    slow.tokens = 0.;
    CHECK (bpa_shaper_wait_ticks (&slow, 1500, 100e-6, 2047) == 2047,
	   "a wait past the calendar's horizon is asked again at the horizon");
  }
  CHECK (bpa_shaper_waited_us (5, 0xfffffffbu) == 10,
	 "a wait across the u32 microsecond wrap is still 10 us");
  s.q_bytes = 250000;
  CHECK (bpa_shaper_queue_ms_now (&s) == 100,
	 "250000 bytes at 20 Mbit/s are 100 ms of queue (%u)",
	 bpa_shaper_queue_ms_now (&s));
}

static void
test_calendar (void)
{
  u64 occ[BPA_SHAPER_SLOT_WORDS];

  memset (occ, 0, sizeof (occ));
  CHECK (bpa_shaper_next_slot (occ, 0) == BPA_SHAPER_NONE &&
	   bpa_shaper_next_slot (occ, 1234) == BPA_SHAPER_NONE,
	 "an empty calendar has no next slot");
  occ[5 >> 6] |= 1ULL << 5;
  CHECK (bpa_shaper_next_slot (occ, 5) == 0, "a slot itself is 0 ahead");
  CHECK (bpa_shaper_next_slot (occ, 0) == 5, "slot 5 is 5 ahead of 0");
  CHECK (bpa_shaper_next_slot (occ, 6) == BPA_SHAPER_SLOTS - 1,
	 "and from 6, the whole turn less one round (%u)",
	 bpa_shaper_next_slot (occ, 6));
  occ[2000 >> 6] |= 1ULL << (2000 & 63);
  CHECK (bpa_shaper_next_slot (occ, 6) == 1994,
	 "slot 2000 is 1994 ahead of 6 (%u)", bpa_shaper_next_slot (occ, 6));
  CHECK (bpa_shaper_next_slot (occ, 2001) == 52,
	 "past the last slot it comes round to 5: 52 ahead of 2001 (%u)",
	 bpa_shaper_next_slot (occ, 2001));
  memset (occ, 0, sizeof (occ));
  occ[BPA_SHAPER_SLOT_WORDS - 1] |= 1ULL << 63;
  CHECK (bpa_shaper_next_slot (occ, 0) == BPA_SHAPER_SLOTS - 1,
	 "the last slot found from the first");
  memset (occ, 0, sizeof (occ));
  occ[3] |= 1ULL << 1; /* slot 193 */
  CHECK (bpa_shaper_next_slot (occ, 200) == BPA_SHAPER_SLOTS - 7,
	 "a slot just behind, in the same word, is a turn less 7 ahead (%u)",
	 bpa_shaper_next_slot (occ, 200));

  CHECK (bpa_shaper_due_tick (100, 99) == 100, "a tick ahead stays");
  CHECK (bpa_shaper_due_tick (50, 99) == 100,
	 "a tick already gone through is the next one");
  CHECK (bpa_shaper_due_tick (99 + 5000, 99) == 99 + BPA_SHAPER_SLOTS - 1,
	 "one past the horizon is held at it, not aliased into a slot");
}

static void
test_cap (void)
{
  CHECK (bpa_shaper_thread_cap (114688, 7) == 4096,
	 "prisma-bench's pool (7 x 16384) over 7 workers: a quarter, 4096 a "
	 "thread (%u)",
	 bpa_shaper_thread_cap (114688, 7));
  CHECK (bpa_shaper_thread_cap (16384, 0) == 4096,
	 "no workers: the main thread's, a quarter of 16384");
  CHECK (bpa_shaper_thread_cap (4096, 8) == 512,
	 "never under 512 a thread");
}

static void
test_codel (void)
{
  bpa_shaper_t s = shaped (20000, 100);
  u32 t = 1000000, target = BPA_SHAPER_CODEL_TARGET_US,
      interval = BPA_SHAPER_CODEL_INTERVAL_US, first, second, third;

  CHECK (bpa_shaper_codel_target_us (&s) == target,
	 "at 20 Mbit/s the target is the default (%u)",
	 bpa_shaper_codel_target_us (&s));
  CHECK (!bpa_shaper_codel_drop (&s, target - 1, 100000, t),
	 "a head that waited under the target is not dropped");
  CHECK (!bpa_shaper_codel_drop (&s, target + 1, 100000, t),
	 "one above it is not either: a burst, until it has stood");
  CHECK (!bpa_shaper_codel_drop (&s, target + 1, 100000, t + interval - 1),
	 "nor through the whole interval");
  CHECK (bpa_shaper_codel_drop (&s, target + 1, 100000, t + interval),
	 "a queue that stood above the target an interval: one dropped");
  CHECK (s.codel_dropping && s.drop_aqm == 1 && s.codel_count == 1,
	 "dropping, one counted");
  first = s.codel_drop_next_us;
  CHECK (first == t + 2 * interval, "the next an interval later (%u)",
	 first - t);
  CHECK (!bpa_shaper_codel_drop (&s, target + 1, 100000, t + interval + 1),
	 "not before then");
  CHECK (bpa_shaper_codel_drop (&s, target + 1, 100000, first),
	 "and then the second");
  second = s.codel_drop_next_us - first;
  CHECK (second == (u32) (interval / 1.4142135623730951),
	 "the third interval/sqrt(2) after it (%u)", second);
  bpa_shaper_codel_drop (&s, target + 1, 100000, s.codel_drop_next_us);
  third = s.codel_drop_next_us;
  CHECK (s.codel_count == 3 && s.drop_aqm == 3, "three dropped");
  CHECK (!bpa_shaper_codel_drop (&s, target - 1, 100000, third) &&
	   !s.codel_dropping && s.codel_first_above_us == 0,
	 "a head under the target ends the episode");
  CHECK (!bpa_shaper_codel_drop (&s, target * 10, 1500, third + interval * 2),
	 "a queue of one packet is never dropped from");

  /* Back above it soon after: the episode resumes from its count (RFC
   * 8289's count reuse), so the drops do not start slow again. */
  bpa_shaper_codel_drop (&s, target + 1, 100000, third + 10);
  CHECK (bpa_shaper_codel_drop (&s, target + 1, 100000,
				third + 10 + interval),
	 "above again for an interval: dropping");
  CHECK (s.codel_count >= 1 && s.codel_count <= 3,
	 "with the count carried over or restarted, never past the last (%u)",
	 s.codel_count);

  {
    bpa_shaper_t slow = shaped (512, 100); /* 1500 bytes take 23.4 ms */
    CHECK (bpa_shaper_codel_target_us (&slow) == 23437,
	   "at 512 kbit/s the target is one packet's time, 23437 us (%u)",
	   bpa_shaper_codel_target_us (&slow));
    CHECK (!bpa_shaper_codel_drop (&slow, 20000, 100000, t) &&
	     !bpa_shaper_codel_drop (&slow, 20000, 100000, t + 10 * interval),
	   "and a packet that waited 20 ms there is never dropped for it");
  }
  {
    bpa_shaper_t off = shaped (20000, 100);
    off.codel_off = 1;
    bpa_shaper_codel_drop (&off, target * 10, 100000, t);
    CHECK (!bpa_shaper_codel_drop (&off, target * 10, 100000,
				   t + 10 * interval) &&
	     off.drop_aqm == 0,
	   "codel off: a FIFO, nothing dropped");
  }
  {
    bpa_shaper_t own = shaped (20000, 100);
    own.codel_target_us = 5000;
    own.codel_interval_us = 100000;
    CHECK (bpa_shaper_codel_target_us (&own) == 5000 &&
	     bpa_shaper_codel_interval_us (&own) == 100000,
	   "a target and an interval of its own are used");
    bpa_shaper_codel_drop (&own, 6000, 100000, t);
    CHECK (bpa_shaper_codel_drop (&own, 6000, 100000, t + 100000),
	   "and RFC 8289's 5/100 drops after 100 ms above 5");
  }
  CHECK (bpa_shaper_codel_law (0, 4, 200000) == 100000,
	 "the control law: 200 ms / sqrt(4) = 100 ms (%u)",
	 bpa_shaper_codel_law (0, 4, 200000));
  CHECK (bpa_shaper_codel_law (0, 0, 200000) == 200000,
	 "a count of 0 is taken as 1");
}

int
main (void)
{
  test_params ();
  test_reset_and_set ();
  test_refill ();
  test_admit ();
  test_send ();
  test_wait ();
  test_calendar ();
  test_cap ();
  test_codel ();
  return bpa_test_verdict (checks, failures);
}
