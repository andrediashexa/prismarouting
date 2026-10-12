"""
How a dynamic `policy interleaved` pool spreads subscribers over its public
addresses under churn, in the reference model (#603).

The plugin is held to the model block for block by test/test_bpa_dyn.c, over
the sequences ref/gen_dyn_vectors.py writes. What that cannot say is whether
the RULE is the right one: five sequences show the plugin does what the model
does, not that what the model does stays even over churn in general. This is
that claim, over many seeds of the bench's churn (#475, lab/bench3vm.sh):
fill the pool, then rounds of half the holders leaving at random and thirty
idle subscribers arriving.

  the stack (the rule before #603) drifts: its mean max/min after six rounds
  is well above the most even spread, and some runs reach 2 or more - the
  box measured 1.67, 1.14, 2.2 and 2.0

  least-loaded holds the most even spread possible from the sixth round on,
  in every run; before that, a round can leave a hole thirty arrivals cannot
  fill (half the holders leaving at random from a full pool can empty one
  address to five while another keeps ten)

Mutations shown red, one at a time, in ref/bpa_model.py: arrive() keying on
the address alone (addresses left empty: 2100 run-rounds above 8/7); keying
on the worker's OWN share of the load instead of the pool's (the mean after
six rounds rises to 1.44, 2018 run-rounds above 8/7); taking an address's
newest free block instead of its oldest (the fresh one-worker pool stops
handing out 0, 1, 2 ...).
"""
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "ref"))
from bpa_model import (PoolConfig, MapPolicy, DynamicSlices,  # noqa: E402
                       even_spread, BpaError)

fail = 0


def check(c, msg):
    global fail
    print(("  [ ok ] " if c else "  FAIL: ") + msg)
    fail += 0 if c else 1


BENCH = PoolConfig("bench", ["100.64.0.0/22"], ["203.0.113.0/29"],
                   block_size=4096, policy=MapPolicy.INTERLEAVED)
RUNS, ROUNDS = 300, 12


def churn(rule, seed):
    """max/min subscribers per address after each round."""
    rnd = random.Random(seed)
    d = DynamicSlices(BENCH, 3, rule)
    subs = list(range(1, 241))
    rnd.shuffle(subs)
    for s in subs:
        d.arrive(s)
    out = []
    for _ in range(ROUNDS):
        d.leave(rnd.sample(sorted(d.held), len(d.held) // 2))
        idle = [s for s in range(1, 241) if s not in d.held]
        for s in rnd.sample(idle, 30):
            d.arrive(s)
        # An address with nobody on it is as uneven as a pool gets.
        out.append(d.spread() or float("inf"))
    return out


print("=== the bench's pool, the stack against least-loaded ===")
stack = [churn("stack", s) for s in range(RUNS)]
least = [churn("least-loaded", s) for s in range(RUNS)]
even = even_spread(61, 8)
st6 = statistics.mean(r[5] for r in stack)
ll6 = statistics.mean(r[5] for r in least)
print(f"  mean max/min after round 6 over {RUNS} runs: stack {st6:.3f}, "
      f"least-loaded {ll6:.3f}, most even possible {even:.3f}")
check(st6 > 1.4,
      f"the stack drifts: mean max/min {st6:.3f} after six rounds")
check(max(r[5] for r in stack) >= 2.0,
      "and some runs of the stack reach 2 or more, as the box did")
bad = [(seed, k) for seed, r in enumerate(least) for k in range(5, ROUNDS)
       if r[k] > 8 / 7 + 1e-9]
check(not bad, f"least-loaded: from round 6 on, every run at 8/7 or better "
      f"({len(bad)} run-rounds above it)")
print("=== the rule's edges ===")
d = DynamicSlices(BENCH, 1)
check(d.rule == "least-loaded", "an interleaved pool defaults to least-loaded")
check([d.arrive(s) for s in range(9)] == list(range(9)),
      "one worker, a fresh pool: blocks 0, 1, 2 ... as the stack gave them")
blocked = PoolConfig("blocked", ["100.64.0.0/22"], ["203.0.113.0/29"],
                     block_size=4096, policy=MapPolicy.BLOCKED)
check(DynamicSlices(blocked, 3).rule == "stack",
      "a blocked pool keeps the stack")
try:
    DynamicSlices(blocked, 3, "least-loaded")
    check(False, "least-loaded on a blocked pool is refused")
except BpaError:
    check(True, "least-loaded on a blocked pool is refused")
d = DynamicSlices(BENCH, 3)
for s in range(1, 121):
    d.arrive(s)
check(d.arrive(121 + 0) is None and d.spread() == 1.0,
      "a full pool refuses, and full is perfectly even")
d.leave([1, 2, 3])
check(d.load.count(14) == 3 and sum(d.load) == 117,
      "leave() uncounts the leavers on their addresses")
check(even_spread(60, 8) == 8 / 7 and even_spread(3, 8) is None,
      "even_spread: 60 over 8 is 8/7; fewer than one each has no ratio")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
