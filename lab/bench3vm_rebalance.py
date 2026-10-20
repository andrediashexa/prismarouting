#!/usr/bin/env python3
"""The numbers of #210's question, read off one or more runs of the bench's
uneven mode (lab/bench3vm.sh uneven off|on).

  bench3vm_rebalance.py <run directory> [<run directory>...]

One run prints its own reading and writes rebalance.json beside its inputs;
several print one row each, so the off and the on runs sit in one table.

TWO WITNESSES, counted apart because they count different things:

  * the SUBSCRIBERS (sched.jsonl, one line per session window): a window is
    served if any attempt in it got through, refused-first if its first
    attempt did not; time to first translation is from the window's start to
    the attempt that got through. This is what a subscriber lives through.
  * the BOX (`show bpa blocks` at the end, sampled every 10 s, and the
    rebalance's own log lines): allocations, and refusals - which the box
    counts per PACKET it could not translate, so one refused attempt is
    three of them (the SYN and its retransmits at 1 and 3 s), and a lent
    block is counted by the log line that moved it.
"""
import calendar
import json
import os
import re
import statistics
import sys

BLOCKS = re.compile(r"thread (\d+): (\d+)/(\d+) blocks in use, (\d+) allocations"
                    r" \((\d+) extensions\), (\d+) releases, (\d+) refusals")
TOTAL = re.compile(r"total: (\d+)/(\d+) in use, (\d+) allocations .*?(\d+) releases,"
                   r" (\d+) refusals")


def pct(xs, q):
    """The q-th percentile of xs by nearest rank; None when xs is empty."""
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round(q / 100 * len(xs) + 0.5)) - 1))]


def subscribers(rows):
    """What the subscribers lived through, overall and per residue."""
    def one(rs):
        served = [r for r in rs if r["ok"] is not None]
        first_ref = [r for r in rs if r["tries"] > 1 or r["ok"] is None]
        rescued = [r for r in first_ref if r["ok"] is not None]
        ttft = [r["ok"] - r["start"] for r in rescued]
        want = sum(r["end"] - r["start"] for r in rs)
        had = sum(r["end"] - r["ok"] for r in served)
        return {
            "windows": len(rs),
            "served": len(served),
            "never": len(rs) - len(served),
            "subs": len({r["sub"] for r in rs}),
            "subs_served": len({r["sub"] for r in served}),
            "tries": sum(r["tries"] for r in rs),
            "refused_first": len(first_ref),
            "rescued": len(rescued),
            "ttft_median": round(statistics.median(ttft), 1) if ttft else None,
            "ttft_p90": round(pct(ttft, 90), 1) if ttft else None,
            "online_share": round(had / want, 3) if want else None,
        }
    out = {"all": one(rows)}
    for r in sorted({r["residue"] for r in rows}):
        out[f"residue{r}"] = one([x for x in rows if x["residue"] == r])
    return out


def box(run):
    """What the box counted: the end of `show bpa blocks`, the samples, and
    the rebalance's own log lines."""
    out = {"threads": {}}
    try:
        text = open(os.path.join(run, "blocks-end.txt")).read()
    except OSError:
        text = ""
    for m in BLOCKS.finditer(text):
        t = int(m.group(1))
        out["threads"][t] = {"alloc": int(m.group(4)), "refusals": int(m.group(7)),
                             "releases": int(m.group(6))}
    m = TOTAL.search(text)
    if m:
        out.update(allocations=int(m.group(3)), releases=int(m.group(4)),
                   refusals=int(m.group(5)))
    # The samples: how often a slice sat full while another had 5 or more
    # blocks free - the state #210 is about - and the most out on loan.
    full_beside_free, n, lent_max = 0, 0, 0
    try:
        samples = open(os.path.join(run, "blocks-samples.txt")).read().split("@@")
    except OSError:
        samples = []
    for s in samples:
        th = [(int(m.group(2)), int(m.group(3))) for m in BLOCKS.finditer(s)]
        if len(th) < 2:
            continue
        n += 1
        if any(a >= b for a, b in th) and any(b - a >= 5 for a, b in th):
            full_beside_free += 1
        lent = sum(int(x) for x in re.findall(r"(\d+) lent to another thread", s))
        lent_max = max(lent_max, lent)
    out.update(samples=n, full_beside_free=full_beside_free, lent_max=lent_max)
    try:
        log = open(os.path.join(run, "vpp-log.txt")).read()
    except OSError:
        log = ""
    out["lent"] = len(re.findall(r"lent from worker", log))
    out["home_unused"] = len(re.findall(r"went home unused", log))
    # When the loans happened, by minute of the run (the box's log is in UTC,
    # the samples are stamped with the container's epoch; both come from the
    # same host clock). Measured on the first run: every loan in the first
    # ten seconds and none after, which is what this column is for.
    first = None
    for s in samples:
        m = re.match(r"\s*(\d+)", s)
        if m:
            first = int(m.group(1))
            break
    per_min = {}
    for m in re.finditer(r"(\d{4})/(\d\d)/(\d\d) (\d\d):(\d\d):(\d\d)\S* .*lent from worker",
                         log):
        t = calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0))
        k = int((t - first) // 60) if first else 0
        per_min[k] = per_min.get(k, 0) + 1
    out["lent_by_minute"] = per_min
    return out


def read(run):
    """One run's reading, also written to rebalance.json in the run."""
    rows = [json.loads(line) for line in open(os.path.join(run, "sched.jsonl"))
            if line.strip()]
    meta = {}
    for line in open(os.path.join(run, "meta.txt")):
        for w in line.split():
            if "=" in w:
                k, v = w.split("=", 1)
                meta[k] = v
    res = {"run": os.path.basename(run.rstrip("/")),
           "rebalance": meta.get("rebalance", "?"),
           "load": meta.get("host_load_before", "?") + " -> "
           + meta.get("host_load_after", "?"),
           "subscribers": subscribers(rows), "box": box(run)}
    # Inside the lab the run is the container's to write; read again from
    # the host, as a user, it belongs to root and the reading is enough.
    try:
        with open(os.path.join(run, "rebalance.json"), "w") as f:
            json.dump(res, f, indent=1)
    except OSError:
        pass
    return res


def row(r):
    """One run as one table row."""
    a, b = r["subscribers"]["all"], r["box"]
    hot = r["subscribers"].get("residue0", {})
    rest = [v for k, v in r["subscribers"].items() if k not in ("all", "residue0")]
    rest_never = sum(v["never"] for v in rest)
    rest_win = sum(v["windows"] for v in rest)
    return (f"| {r['run']} | {r['rebalance']} | {a['served']}/{a['windows']} "
            f"| {a['subs_served']}/{a['subs']} | {a['online_share']} "
            f"| {hot.get('never')}/{hot.get('windows')} | {rest_never}/{rest_win} "
            f"| {a['refused_first']} -> {a['rescued']} "
            f"| {a['ttft_median']} / {a['ttft_p90']} "
            f"| {b.get('allocations')} | {b.get('refusals')} "
            f"| {b['lent']} ({b['home_unused']} unused) "
            f"| {b['full_beside_free']}/{b['samples']} | {r['load']} |")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    runs = [read(d) for d in sys.argv[1:]]
    if len(runs) == 1:
        print(json.dumps(runs[0], indent=1))
    print("| run | rebalance | windows served | subscribers served "
          "| online share | hot never served | others never served "
          "| refused first -> got through | to first translation, median / p90 s "
          "| box allocations | box refusals (packets) | blocks lent "
          "| samples: full slice beside free | host load |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in runs:
        print(row(r))
    # Per slice, which is where a lender's cost shows: a residue whose own
    # subscribers are refused more with rebalance on paid for the loans.
    print()
    print("| run | rebalance | residue | subscribers | windows | never served "
          "| refused first -> got through | to first translation, median / p90 s "
          "| online share | box refusals (packets), its thread |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in runs:
        for k, v in sorted(r["subscribers"].items()):
            if k == "all":
                continue
            th = r["box"]["threads"].get(str(int(k[7:]) + 1),
                                         r["box"]["threads"].get(int(k[7:]) + 1, {}))
            print(f"| {r['run']} | {r['rebalance']} | {k[7:]} | {v['subs']} "
                  f"| {v['windows']} | {v['never']} | {v['refused_first']} -> {v['rescued']} "
                  f"| {v['ttft_median']} / {v['ttft_p90']} | {v['online_share']} "
                  f"| {th.get('refusals')} |")
    print()
    for r in runs:
        print(f"{r['run']}: loans by minute {r['box']['lent_by_minute']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
