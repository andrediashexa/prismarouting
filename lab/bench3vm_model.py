#!/usr/bin/env python3
"""What the bench's churn does to the spread, in a model (#475).

  bench3vm_model.py [runs]

lab/bench3vm.sh measures subscribers per public address on the box after
rounds of churn. This replays the same pool and the same churn against a
model of the allocator, so the measured drift can be told apart from luck:
120 blocks, block n on public address n mod 8 (policy interleaved), worker
w owning the blocks n = local * 3 + w and the subscribers s mod 3, each
worker's free blocks a STACK (bpa_dyn.c: vec_add1 on release, vec_pop on
allocation). Each round half the holders leave at random and 30 idle
subscribers arrive. Printed: the mean max/min of holders per address after
rounds 1, 6 and 12, for the stack and for the alternative #475 asked to be
priced - a free block taken from the address with the fewest holders.

A model and not the box: it says what the stack does to the spread, not
what a least-loaded choice would cost on the allocation path.

Kept as the record of what #475 measured before #603. The box takes the
least-loaded block now, and the rule it is held to is ref/bpa_model.py's
DynamicSlices - which releases a round's leavers in the order the reap does
rather than at random, so its stack averages 1.60 after six rounds where
this one says 2.25. Both are a stack drifting; the order is the difference.
"""
import random
import statistics
import sys

N_ADDR, N_WORK, PER_ADDR, N_SUBS = 8, 3, 15, 240
N_BLOCKS = N_ADDR * PER_ADDR


def churn(policy, rounds, seed):
    rnd = random.Random(seed)
    free = {w: list(reversed(range(N_BLOCKS // N_WORK))) for w in range(N_WORK)}
    held = {}

    def load():
        c = [0] * N_ADDR
        for b in held.values():
            c[b % N_ADDR] += 1
        return c

    def join(s):
        w = s % N_WORK
        if not free[w]:
            return
        if policy == "stack":
            local = free[w].pop()
        else:
            c = load()
            local = min(free[w], key=lambda x: c[(x * N_WORK + w) % N_ADDR])
            free[w].remove(local)
        held[s] = local * N_WORK + w

    subs = list(range(1, N_SUBS + 1))
    rnd.shuffle(subs)
    for s in subs:
        join(s)
    out = []
    for _ in range(rounds):
        for s in rnd.sample(sorted(held), len(held) // 2):
            b = held.pop(s)
            free[b % N_WORK].append(b // N_WORK)
        idle = [s for s in range(1, N_SUBS + 1) if s not in held]
        for s in rnd.sample(idle, 30):
            join(s)
        c = load()
        out.append(max(c) / min(c) if min(c) else float("inf"))
    return out


def main():
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    for policy in ("stack", "least-loaded"):
        res = [churn(policy, 12, seed) for seed in range(runs)]
        means = [round(statistics.mean(r[i] for r in res), 2)
                 for i in (0, 5, 11)]
        print(f"{policy:13s} max/min after rounds 1, 6, 12: {means}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
