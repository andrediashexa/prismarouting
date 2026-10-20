/*
 * bpa_pppoe_shaper.h - a PPPoE session's plan enforced by a queue (#610):
 * what passes at once, what waits, what is dropped, and when a waiting
 * packet may go.
 *
 * WHY THERE IS ONE. VPP 26.06 has a policer and no shaper (docs/bng.md 2.2:
 * nothing in `vppctl help` matches `shap`, `hqos` is in no plugin), so a
 * plan was a 1r2c policer on the session interface (#607): up to the rate
 * passes, past it is DROPPED. A TCP flow needs a half-second bucket to hold
 * its plan through that (0.155.2), UDP above the plan is lost rather than
 * delayed, and one subscriber's flows share nothing fairly. This is the
 * queue: a packet past the rate waits, up to `limit` bytes of them, and
 * leaves when the bucket has its bytes.
 *
 * ONE BUCKET, ONE FIFO, PER SESSION AND DIRECTION, on ONE THREAD. The
 * bucket and the queue are written only by the thread that owns the
 * session (bpa_pppoe_shaper.c hands every other thread's packets to it), so
 * nothing here takes a lock. The control plane changes a rate under the
 * worker barrier.
 *
 *   * a packet that finds the queue EMPTY and the bucket holding its length
 *     passes at once - the common case of a session under its plan costs a
 *     refill and a compare;
 *   * one that does not, waits at the tail - unless the queue already holds
 *     `limit` bytes (dropped, the session's own queue full), or the box's
 *     shapers hold every buffer they may (dropped, counted apart: that is
 *     the box, not the plan);
 *   * the head leaves when the bucket holds its length, or when the bucket
 *     is FULL - a packet longer than the bucket would otherwise never leave,
 *     and the bucket then goes negative by the difference, which the rate
 *     pays back.
 *
 * THE BUCKET IS SMALL ON PURPOSE. A policer's bucket is what TCP's
 * overshoot is absorbed by, so it had to be half a second; here the queue
 * absorbs it, and the bucket is only what may leave at once after a quiet
 * moment: BPA_SHAPER_BURST_MS of the rate, never under two full packets.
 *
 * THE QUEUE IS IN TIME, NOT PACKETS: `queue_ms` of the rate in bytes, never
 * under BPA_SHAPER_QUEUE_MIN. The longest a packet waits is then the same
 * at every plan, which is what a subscriber notices; a count of packets
 * would be 600 ms at 2 Mbit/s and 1 ms at 1 Gbit/s.
 *
 * WHAT IS COUNTED, per session and direction, by the owner: packets passed
 * at once, delayed (queued and sent), dropped by the session's queue and by
 * the box's, bytes sent, the queue now and its most, and each delayed
 * packet's wait - the sum (for an average) and the most.
 *
 * WHY A HEADER. bpa_pppoe_shaper.c is the VPP-facing half - the nodes, the
 * handoff, the CLI - and is excused from unit tests for that; every decision
 * is here, and test/test_bpa_pppoe_shaper.c drives it with calls alone.
 */

#ifndef __included_bpa_pppoe_shaper_h__
#define __included_bpa_pppoe_shaper_h__

#define BPA_SHAPER_DOWN 0 /* to the subscriber: the session's output */
#define BPA_SHAPER_UP	1 /* from it: the session's input */

/* No buffer: an empty queue's head and tail, a last packet's next. */
#define BPA_SHAPER_NONE 0xffffffffu
/* A session no packet has reached yet: bound to the first thread one
 * reaches (bpa_pppoe_shaper.c). */
#define BPA_SHAPER_UNBOUND 0xffffu

/* The bucket: 2 ms of the rate, never under two 1500-byte packets. */
#define BPA_SHAPER_BURST_MS  2
#define BPA_SHAPER_BURST_MIN 3000
/* The queue: `queue_ms` of the rate, never under ten 1500-byte packets. It
 * is the hard cap behind CoDel (below), which keeps a TCP flow's standing
 * queue near its target; what fills it is traffic that does not slow down
 * - UDP past the plan - and what it costs then is this much wait. */
#define BPA_SHAPER_QUEUE_MS_DEFAULT 100
#define BPA_SHAPER_QUEUE_MS_MIN	    5
#define BPA_SHAPER_QUEUE_MS_MAX	    1000
#define BPA_SHAPER_QUEUE_MIN	    15000
/* What the VTY and RADIUS can give: 10 Gbit/s (pppoe.RATE_MAX_KBIT). */
#define BPA_SHAPER_KBIT_MAX 10000000u
/* The share of the box's buffers its shapers may hold between them. */
#define BPA_SHAPER_BOX_SHARE_DIV 4

typedef enum
{
  BPA_SHAPER_PASS = 0,	 /* goes on now */
  BPA_SHAPER_QUEUE,	 /* waits at the tail */
  BPA_SHAPER_DROP_FULL,	 /* the session's queue holds `limit` already */
  BPA_SHAPER_DROP_BOX,	 /* the box's shapers hold every buffer they may */
} bpa_shaper_verdict_t;

typedef struct
{
  /* The control plane's, set under the barrier. rate 0: not shaped. */
  f64 rate;   /* bytes a second */
  u32 kbit;   /* as given */
  u32 burst;  /* bytes the bucket holds */
  u32 limit;  /* bytes the queue holds */
  u32 gen;    /* moved at each removal: a handed-off packet of the last
		 session on this interface is not this one's */
  u16 owner;  /* the thread, or BPA_SHAPER_UNBOUND */
  u16 queue_ms;
  u32 active; /* has a queue: in a slot of the owner's calendar, or being
		 sent */
  u32 slot;   /* the calendar slot it waits in, BPA_SHAPER_NONE */
  /* The owner thread's. */
  f64 tokens;
  f64 last;
  u32 head, tail; /* buffer indices, BPA_SHAPER_NONE when empty */
  u32 q_pkts, q_bytes;
  u32 q_bytes_max;
  u32 delay_max_us;
  u64 passed, delayed, bytes, drop_full, drop_box, delay_sum_us;
  /* CoDel's state (bpa_shaper_codel_drop), the owner's */
  u64 drop_aqm;
  u32 codel_first_above_us; /* 0: the queue's wait is not above target */
  u32 codel_drop_next_us;
  u32 codel_count, codel_lastcount;
  u32 codel_target_us, codel_interval_us; /* 0: the defaults below */
  u8 codel_dropping;
  u8 codel_off; /* a plain FIFO: `set bpa pppoe-shaper ... codel off` */
} bpa_shaper_t;

/* `s` as an interface that is not shaped, its generation kept. */
static_always_inline void
bpa_shaper_reset (bpa_shaper_t *s)
{
  u32 gen = s->gen;

  memset (s, 0, sizeof (*s));
  s->gen = gen;
  s->owner = BPA_SHAPER_UNBOUND;
  s->head = s->tail = s->slot = BPA_SHAPER_NONE;
}

/* The bucket and the queue for `kbit` and `queue_ms`, in bytes; 0 when
 * either is out of range. A 10 Gbit/s plan's second of queue is 1.25 GB,
 * which a u32 holds. */
static_always_inline int
bpa_shaper_params (u32 kbit, u32 queue_ms, f64 *rate, u32 *burst, u32 *limit)
{
  f64 r;
  u64 b, l;

  if (kbit == 0 || kbit > BPA_SHAPER_KBIT_MAX || queue_ms < BPA_SHAPER_QUEUE_MS_MIN
      || queue_ms > BPA_SHAPER_QUEUE_MS_MAX)
    return 0;
  r = (f64) kbit * 1000. / 8.;
  b = (u64) (r * BPA_SHAPER_BURST_MS / 1000.);
  l = (u64) (r * queue_ms / 1000.);
  *rate = r;
  *burst = b < BPA_SHAPER_BURST_MIN ? BPA_SHAPER_BURST_MIN : (u32) b;
  *limit = l < BPA_SHAPER_QUEUE_MIN ? BPA_SHAPER_QUEUE_MIN : (u32) l;
  return 1;
}

/* A new rate on a live session - a CoA - or a first one. The queue and the
 * counters are kept; the bucket is cut to the new size, never filled. */
static_always_inline int
bpa_shaper_set (bpa_shaper_t *s, u32 kbit, u32 queue_ms)
{
  f64 rate;
  u32 burst, limit;

  if (!bpa_shaper_params (kbit, queue_ms, &rate, &burst, &limit))
    return 0;
  s->rate = rate;
  s->kbit = kbit;
  s->burst = burst;
  s->limit = limit;
  s->queue_ms = (u16) queue_ms;
  if (s->tokens > (f64) burst)
    s->tokens = (f64) burst;
  return 1;
}

/*
 * The bucket brought up to `now` (seconds). Refilled by ELAPSED TIME, as
 * bpa_rate.h's is, so a quiet session costs nothing to keep; a FIRST call
 * starts it full, and a clock that did not move, or went back, adds nothing.
 */
static_always_inline void
bpa_shaper_refill (bpa_shaper_t *s, f64 now)
{
  if (PREDICT_FALSE (s->last == 0.))
    {
      s->last = now;
      s->tokens = (f64) s->burst;
      return;
    }
  if (now > s->last)
    {
      s->tokens += (now - s->last) * s->rate;
      if (s->tokens > (f64) s->burst)
	s->tokens = (f64) s->burst;
      s->last = now;
    }
}

/*
 * One packet of `len` bytes arriving at `now`: what becomes of it. `room` is
 * whether the box's shapers may hold one more buffer on this thread. A PASS
 * has been taken from the bucket and counted; a QUEUE has been counted in
 * the queue, and the caller links it at the tail.
 */
static_always_inline bpa_shaper_verdict_t
bpa_shaper_admit (bpa_shaper_t *s, u32 len, f64 now, int room)
{
  bpa_shaper_refill (s, now);
  if (s->q_pkts == 0 && s->tokens >= (f64) len)
    {
      s->tokens -= (f64) len;
      s->passed++;
      s->bytes += len;
      return BPA_SHAPER_PASS;
    }
  if ((u64) s->q_bytes + len > s->limit)
    {
      s->drop_full++;
      return BPA_SHAPER_DROP_FULL;
    }
  if (!room)
    {
      s->drop_box++;
      return BPA_SHAPER_DROP_BOX;
    }
  s->q_pkts++;
  s->q_bytes += len;
  if (s->q_bytes > s->q_bytes_max)
    s->q_bytes_max = s->q_bytes;
  return BPA_SHAPER_QUEUE;
}

/* Whether the head, of `len` bytes, may leave now (the bucket refilled by
 * the caller); taken from the bucket when it may. A full bucket lets any
 * length go: one longer than the bucket would otherwise wait for ever. */
static_always_inline int
bpa_shaper_may_send (bpa_shaper_t *s, u32 len)
{
  if (s->tokens < (f64) len && s->tokens < (f64) s->burst)
    return 0;
  s->tokens -= (f64) len;
  return 1;
}

/*
 * THE CALENDAR a thread's waiting sessions are in (bpa_pppoe_shaper.c):
 * BPA_SHAPER_SLOTS slots of BPA_SHAPER_TICK, a session in the slot of the
 * tick its head is due, and a bitmap of the slots that hold one - so the
 * sender finds the next slot due by reading at most 32 words, however long
 * it was since its last turn, and never turns through empty ticks.
 */
#define BPA_SHAPER_TICK	     100e-6
#define BPA_SHAPER_SLOTS     2048u
#define BPA_SHAPER_SLOT_MASK (BPA_SHAPER_SLOTS - 1)
#define BPA_SHAPER_SLOT_WORDS (BPA_SHAPER_SLOTS / 64)

/* The tick `due` as a slot of the calendar whose last processed tick is
 * `done`: never at or before `done` (it would not be seen until the
 * calendar came round), never a whole turn ahead (it would alias). */
static_always_inline u64
bpa_shaper_due_tick (u64 due, u64 done)
{
  if (due <= done)
    due = done + 1;
  if (due - done >= BPA_SHAPER_SLOTS)
    due = done + BPA_SHAPER_SLOTS - 1;
  return due;
}

/* How many ticks after `from` (a slot) the first occupied slot is, going
 * round once - 0 for `from` itself - or BPA_SHAPER_NONE for an empty
 * calendar. */
static_always_inline u32
bpa_shaper_next_slot (const u64 *occupied, u32 from)
{
  u32 i, w, base;

  for (i = 0; i <= BPA_SHAPER_SLOT_WORDS; i++)
    {
      w = ((from >> 6) + i) % BPA_SHAPER_SLOT_WORDS;
      u64 bits = occupied[w];
      base = w << 6;
      if (i == 0)
	bits &= ~0ULL << (from & 63);
      else if (i == BPA_SHAPER_SLOT_WORDS)
	bits &= (from & 63) ? ~(~0ULL << (from & 63)) : 0;
      if (bits)
	return (base + (u32) __builtin_ctzll (bits) - from) &
	       BPA_SHAPER_SLOT_MASK;
    }
  return BPA_SHAPER_NONE;
}

/*
 * How many ticks of `tick` seconds until the head, of `len` bytes, may
 * leave: until the bucket holds its length, or is full. At least one, and
 * at most `max_ticks` - the calendar's horizon (2047 ticks of 100 us); a
 * slower wait is asked again then, which costs one visit.
 */
static_always_inline u32
bpa_shaper_wait_ticks (const bpa_shaper_t *s, u32 len, f64 tick,
		       u32 max_ticks)
{
  f64 need = (f64) (len < s->burst ? len : s->burst) - s->tokens;
  f64 ticks;

  if (need <= 0. || s->rate <= 0.)
    return 1;
  ticks = need / s->rate / tick;
  if (ticks >= (f64) max_ticks)
    return max_ticks;
  return ticks < 1. ? 1 : (u32) ticks + ((f64) (u32) ticks < ticks);
}

/*
 * CoDel (RFC 8289) on the session's queue, asked for each head that is
 * about to leave: 1 when it is to be dropped instead.
 *
 * WHY. A FIFO in front of the plan fills to its limit under ONE TCP flow -
 * that is what TCP does with a buffer - and every other packet of the
 * subscriber then waits the whole queue: measured through a 20 Mbit/s plan
 * (the #610 probe, veths), a ping under one TCP download took 88 ms with a
 * 100 ms queue, 47 with 50 ms, 25 with 25 ms - and 12.7 through the
 * policer, which queues nothing. A shorter queue starves a long path
 * instead: one flow 200 ms away got 8.0 Mbit/s through a 25 ms queue and
 * 17.7 through 100 ms. CoDel keeps the queue a TCP flow needs for a burst
 * and drops, one packet an interval, only while the queue has STOOD above
 * `target` for a whole `interval` - which is the standing queue, not the
 * burst. bpa_pppoe_shaper.c keeps the limit as the hard cap behind it.
 *
 * `waited_us` is how long this head waited; `q_bytes` what the queue holds
 * with it. Times are u32 microseconds, compared by their difference.
 *
 * THE TARGET AND THE INTERVAL ARE NOT RFC 8289's 5 ms and 100 ms, measured
 * (the same probe, 20 Mbit/s, one TCP flow 15 s; ping under one flow; four
 * flows' Jain index; one flow with 50 and 200 ms added to the path):
 *
 *   target/interval   ping   4 flows   +50 ms   +200 ms   retransmits
 *   FIFO 100 ms        91     0.992     19.0     17.7         13
 *   5 / 100 ms         20     0.993     17.9     14.1         32
 *   10 / 100 ms        20     0.998     18.0     14.4         28
 *   15 / 200 ms        26     0.995     18.9     16.6         20
 *   20 / 200 ms        30     0.999     19.0     16.8         15
 *   30 / 300 ms        39     0.986     19.0     14.7         10
 *   policer 0.5 s      13     0.804     19.4     14.8       1129
 *
 * RFC 8289's pair is for a router's link, where one flow of a far server is
 * one of many; here the queue is ONE subscriber's, and that flow is often
 * all it carries - 5/100 gave it 70% of the plan at +200 ms. 15/200 keeps
 * a long path at 83% and the ping under load at 26 ms. The target is never
 * under the time one 1500-byte packet takes at the rate (RFC 8289 4.4: a
 * target under one packet's time drops at a plan too slow for it).
 */
#define BPA_SHAPER_CODEL_TARGET_US   15000u
#define BPA_SHAPER_CODEL_INTERVAL_US 200000u
#define BPA_SHAPER_MTU		     1500u

static_always_inline u32
bpa_shaper_codel_interval_us (const bpa_shaper_t *s)
{
  return s->codel_interval_us ? s->codel_interval_us :
				BPA_SHAPER_CODEL_INTERVAL_US;
}

static_always_inline u32
bpa_shaper_codel_target_us (const bpa_shaper_t *s)
{
  u32 target =
    s->codel_target_us ? s->codel_target_us : BPA_SHAPER_CODEL_TARGET_US;
  f64 one = s->rate > 0. ? (f64) BPA_SHAPER_MTU * 1e6 / s->rate : 0.;

  return one > (f64) target ? (u32) one : target;
}

/* t + interval / sqrt(count): RFC 8289's control law. sqrt by Newton's
 * method over a double, so the header needs no libm. */
static_always_inline u32
bpa_shaper_codel_law (u32 t, u32 count, u32 interval_us)
{
  f64 c = (f64) (count ? count : 1), r = c, prev = 0.;
  int i;

  for (i = 0; i < 32 && r != prev; i++)
    {
      prev = r;
      r = 0.5 * (r + c / r);
    }
  return t + (u32) ((f64) interval_us / r);
}

static_always_inline int
bpa_shaper_codel_drop (bpa_shaper_t *s, u32 waited_us, u32 q_bytes,
		       u32 now_us)
{
  int ok_to_drop = 0;
  u32 delta, interval = bpa_shaper_codel_interval_us (s);

  if (s->codel_off)
    return 0;
  if (waited_us < bpa_shaper_codel_target_us (s) || q_bytes <= BPA_SHAPER_MTU)
    s->codel_first_above_us = 0;
  else if (s->codel_first_above_us == 0)
    /* 0 is "not above": the one instant that wraps to it is moved by 1 us */
    s->codel_first_above_us = now_us + interval ? now_us + interval : 1;
  else if ((i32) (now_us - s->codel_first_above_us) >= 0)
    ok_to_drop = 1;

  if (s->codel_dropping)
    {
      if (!ok_to_drop)
	{
	  s->codel_dropping = 0;
	  return 0;
	}
      if ((i32) (now_us - s->codel_drop_next_us) >= 0)
	{
	  s->codel_count++;
	  s->codel_drop_next_us =
	    bpa_shaper_codel_law (s->codel_drop_next_us, s->codel_count, interval);
	  s->drop_aqm++;
	  return 1;
	}
      return 0;
    }
  if (!ok_to_drop)
    return 0;
  /* Into the dropping state: from where the last episode left off when it
   * was recent, as the RFC's count reuse says. */
  s->codel_dropping = 1;
  delta = s->codel_count - s->codel_lastcount;
  s->codel_count =
    (delta > 1 && (i32) (now_us - s->codel_drop_next_us) <
		    (i32) (16 * interval)) ?
      delta :
      1;
  s->codel_drop_next_us =
    bpa_shaper_codel_law (now_us, s->codel_count, interval);
  s->codel_lastcount = s->codel_count;
  s->drop_aqm++;
  return 1;
}

/* The head, of `len` bytes, dropped by CoDel as it was about to leave. */
static_always_inline void
bpa_shaper_dropped_head (bpa_shaper_t *s, u32 len)
{
  s->q_pkts--;
  s->q_bytes -= len;
}

/* The head, of `len` bytes, sent after waiting `waited_us`. */
static_always_inline void
bpa_shaper_sent (bpa_shaper_t *s, u32 len, u32 waited_us)
{
  s->q_pkts--;
  s->q_bytes -= len;
  s->delayed++;
  s->bytes += len;
  s->delay_sum_us += waited_us;
  if (waited_us > s->delay_max_us)
    s->delay_max_us = waited_us;
}

/* How long the packet stamped `then_us` waited until `now_us`: both are a
 * u32 of microseconds, which wraps every 71 minutes - far past the longest
 * a packet can wait (BPA_SHAPER_QUEUE_MS_MAX at the slowest rate's bucket). */
static_always_inline u32
bpa_shaper_waited_us (u32 now_us, u32 then_us)
{
  return now_us - then_us;
}

/* How long the queue now takes to leave at the rate, ms: what the last
 * packet in it will wait. */
static_always_inline u32
bpa_shaper_queue_ms_now (const bpa_shaper_t *s)
{
  if (s->rate <= 0.)
    return 0;
  return (u32) ((f64) s->q_bytes * 1000. / s->rate);
}

/* The buffers ONE thread's shapers may hold: the box's share of the pool,
 * split over the threads that forward. Never under 512, so a small lab pool
 * still queues. */
static_always_inline u32
bpa_shaper_thread_cap (u32 n_buffers, u32 n_threads)
{
  u32 cap;

  if (n_threads == 0)
    n_threads = 1;
  cap = n_buffers / BPA_SHAPER_BOX_SHARE_DIV / n_threads;
  return cap < 512 ? 512 : cap;
}

/*
 * THE BOX'S TOTALS, one direction's (#610, PRISMA-ROUTING-MIB's
 * prBngShaper): what every shaper counted, the ones that ended included.
 *
 * WHY A RUNNING TOTAL. A session's counters are its bpa_shaper_t's, and a
 * session's end resets it (bpa_shaper_reset), so the sum of the LIVE
 * shapers goes BACKWARDS each time a session ends - a Counter64 that an NMS
 * rates would read every hangup as a wrap or a discontinuity, and a box
 * with churn would graph nothing true. So what an ending shaper counted is
 * added to its direction's `retired` before it is reset (bpa_shaper_apply,
 * under the barrier), and the box's total is `retired` plus the live
 * shapers - in the DATAPLANE, not the SNMP agent: an agent that kept it
 * would lose every session that began and ended between two of its reads,
 * and start again at each restart of snmpd.
 *
 * MONOTONIC: the live counters only grow on their owner thread; `retired`
 * only grows, by exactly what the shaper it took them from loses, on the
 * main thread under the barrier; and the sum is read on the main thread
 * too (`show bpa pppoe-shaper totals`), so no read falls between the two.
 * A dataplane restart starts them at zero, as every VPP counter does:
 * prSystemDataplaneUptime is the discontinuity. What a session still had
 * QUEUED at its end is freed with it and counted nowhere - it was neither
 * sent nor dropped by the plan; at most `limit` bytes a session.
 */
typedef struct
{
  u64 passed, delayed, bytes, drop_full, drop_aqm, drop_box;
  u64 q_pkts, q_bytes; /* queued now: the live shapers' only */
  u32 shaped;	       /* live shapers */
} bpa_shaper_totals_t;

/* `s`'s counters added into `t`; its queue now too when `live`. */
static_always_inline void
bpa_shaper_totals_add (bpa_shaper_totals_t *t, const bpa_shaper_t *s,
		       int live)
{
  t->passed += s->passed;
  t->delayed += s->delayed;
  t->bytes += s->bytes;
  t->drop_full += s->drop_full;
  t->drop_aqm += s->drop_aqm;
  t->drop_box += s->drop_box;
  if (live)
    {
      t->q_pkts += s->q_pkts;
      t->q_bytes += s->q_bytes;
      t->shaped++;
    }
}

#endif /* __included_bpa_pppoe_shaper_h__ */
