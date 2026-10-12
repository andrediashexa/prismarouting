#!/usr/bin/env python3
"""The verdict and the numbers of the three-VM bench (#475).

  bench3vm_check.py <run directory>

Reads what lab/bench3vm.sh collected from the three machines and holds them
against each other. Three witnesses, and none of them is asked to vouch for
itself:

  * the INTERNET guest's who-server log: which public address and port each
    connection arrived from, and which subscriber it said it was;
  * the SUBSCRIBERS' guest: which subscribers got through, and what they were
    told they looked like from outside;
  * the BOX: its syslog records (as the collector on the internet guest
    received them), `show bpa blocks`, and `show bpa subscriber` for every
    subscriber.

Prints one [ ok ] / [FAIL] line per check, the measurements, and writes
summary.json beside the inputs. Exits 1 if any check failed.
"""
import glob
import ipaddress
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

POOL = ipaddress.ip_network("203.0.113.0/29")
INSIDE = ipaddress.ip_network("100.64.0.0/10")
# Seconds of disagreement allowed between the box's clock (syslog stamps)
# and the internet guest's (who-log stamps). Both guests take their clock
# from the same host at boot and nothing sets either afterwards; measured
# spread on this bench is well under a second.
SKEW = 2.0

FAILS = []


def check(ok, what, detail=""):
    print(f"  [{' ok ' if ok else 'FAIL'}] {what}")
    if detail:
        for line in detail.rstrip().splitlines():
            print(f"         {line}")
    if not ok:
        FAILS.append(what)


def load_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    for line in open(path):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def load_json(path, default):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return default


SYSLOG = re.compile(
    r"^<\d+>1 (\S+) \S+ prisma-cgnat - (BLOCK-ALLOC|BLOCK-RELEASE|"
    r"PORTS-EXHAUSTED) \[cgnat@\d+ natEvent=\"(\d+)\" subscriber=\"([^\"]+)\""
    r" public=\"([^\"]+)\" portStart=\"(\d+)\" portEnd=\"(\d+)\"")


def parse_syslog(path):
    recs = []
    if not os.path.exists(path):
        return recs
    for line in open(path, errors="replace"):
        m = SYSLOG.match(line.strip())
        if not m:
            continue
        ts, kind, _ev, sub, pub, lo, hi = m.groups()
        t = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ")
        t = (t - datetime(1970, 1, 1)).total_seconds()
        recs.append({"t": t, "kind": kind, "sub": sub, "pub": pub,
                     "lo": int(lo), "hi": int(hi)})
    return recs


def intervals(recs):
    """(sub, pub, lo, hi) -> list of [alloc time, release time or None]."""
    out = defaultdict(list)
    for r in sorted(recs, key=lambda r: r["t"]):
        key = (r["sub"], r["pub"], r["lo"], r["hi"])
        if r["kind"] == "BLOCK-ALLOC":
            out[key].append([r["t"], None])
        elif r["kind"] == "BLOCK-RELEASE" and out[key] \
                and out[key][-1][1] is None:
            out[key][-1][1] = r["t"]
    return out


def who_held(ivs, pub, port, t):
    """Who the records say held pub:port at time t - the question a court
    order asks - answered the way a collector has to: the LAST allocation of
    a block holding that port before the instant, provided no release of it
    came before the instant. Returns the set of subscribers so answered
    (empty or one; two would be two allocations at the same millisecond).

    The last one, and not every interval that covers t: a block released by
    one subscriber is handed to the next within the second on this bench, so
    "covers t, give or take the clocks' skew" named both - the first run of
    this check read that as the records being ambiguous, and they were not.
    """
    best, when = set(), None
    for (sub, p, lo, hi), spans in ivs.items():
        if p != pub or not lo <= port <= hi:
            continue
        for a, r in spans:
            if a > t + SKEW:
                continue
            if r is not None and r < t - SKEW:
                continue
            if when is None or a > when:
                best, when = {sub}, a
            elif a == when:
                best.add(sub)
    return best


BLOCK_LINE = re.compile(r"^\s+block \d+\s+(\S+)\s+ports (\d+)-(\d+)")
HOLDS_LINE = re.compile(r"^\s+holds (\S+) ports (\d+)-(\d+)")
FORMULA_LINE = re.compile(r"the formula gives it (\S+) ports (\d+)-(\d+)")


def parse_subscribers(path):
    """`show bpa subscriber` for every subscriber, as the box answered:
    address -> list of (public, lo, hi) blocks it holds."""
    held = {}
    cur = None
    if not os.path.exists(path):
        return held
    for line in open(path, errors="replace"):
        if line.startswith("SUB "):
            cur = line.split()[1]
            held[cur] = []
            continue
        if cur is None:
            continue
        m = BLOCK_LINE.match(line) or HOLDS_LINE.match(line)
        if m:
            held[cur].append((m.group(1), int(m.group(2)), int(m.group(3))))
    return held


TOTAL = re.compile(r"total: (\d+)/(\d+) in use, (\d+) allocations .*?, "
                   r"(\d+) releases, (\d+) refusals")


def parse_blocks(path):
    try:
        text = open(path).read()
    except OSError:
        return None
    m = TOTAL.search(text)
    if not m:
        return None
    k = ("in_use", "total", "allocations", "releases", "refusals")
    return dict(zip(k, map(int, m.groups())))


def spread(counts, addresses):
    vals = [counts.get(a, 0) for a in addresses]
    lo, hi = min(vals), max(vals)
    return {"per_address": dict(zip(addresses, vals)), "min": lo, "max": hi,
            "subscribers": sum(vals),
            "max_over_min": round(hi / lo, 3) if lo else None}


def even(n, k):
    """max/min of the most even spread of n holders over k addresses - what
    taking a block from the least-loaded address would give."""
    lo = n // k
    hi = lo + (1 if n % k else 0)
    return round(hi / lo, 3) if lo else None


def parse_errors_by_thread(path):
    """`show errors` prints one group per thread that counted anything; the
    handoff node's two counters in each group, in order."""
    groups = []
    cur = None
    if not os.path.exists(path):
        return groups
    for line in open(path):
        m = re.match(r"\s*(\d+)\s+(\S+)\s+(.*?)\s+error\s*$", line)
        if not m:
            continue
        n, node, what = int(m.group(1)), m.group(2), m.group(3)
        if node == "bpa-in2out-handoff" and "handed to another" in what:
            cur = {"handed": n, "same": 0}
            groups.append(cur)
        elif node == "bpa-in2out-handoff" and "already on owning" in what:
            if cur is None or cur.get("same"):
                cur = {"handed": 0, "same": 0}
                groups.append(cur)
            cur["same"] = n
            cur = None
    return groups


def parse_runtime(path):
    """thread name -> {node: vectors} from `show runtime`."""
    out = {}
    cur = None
    if not os.path.exists(path):
        return out
    for line in open(path):
        m = re.match(r"Thread (\d+) (\S+)", line)
        if m:
            cur = m.group(2)
            out[cur] = {}
            continue
        f = line.split()
        if cur and len(f) >= 7 and f[2].isdigit() and f[3].isdigit():
            out[cur][f[0]] = int(f[3])
    return out


def iface_counters(path):
    out = {}
    cur = None
    if not os.path.exists(path):
        return out
    for line in open(path):
        f = line.split()
        if len(f) >= 6 and f[1].isdigit() and f[2] in ("up", "down"):
            cur = f[0]
            out[cur] = {}
            f = f[4:]
        if cur and len(f) >= 3 and f[-1].isdigit():
            out[cur][" ".join(f[:-1])] = int(f[-1])
        elif cur and len(f) == 2 and f[-1].isdigit():
            out[cur][f[0]] = int(f[1])
    return out


def iperf_sum(pattern, udp):
    total_bps = 0.0
    pkts = lost = retr = 0
    n = 0
    for p in sorted(glob.glob(pattern)):
        d = load_json(p, {})
        end = d.get("end", {})
        if udp:
            s = end.get("sum", {})
            pkts += s.get("packets", 0)
            lost += s.get("lost_packets", 0)
            total_bps += s.get("bits_per_second", 0)
        else:
            s = end.get("sum_received", {})
            total_bps += s.get("bits_per_second", 0)
            retr += end.get("sum_sent", {}).get("retransmits", 0)
        if s:
            n += 1
    return {"streams": n, "gbps": round(total_bps / 1e9, 3),
            "packets": pkts, "lost": lost, "retransmits": retr}


def handoff(run, tag):
    groups = parse_errors_by_thread(f"{run}/errors-{tag}.txt")
    rt = parse_runtime(f"{run}/runtime-{tag}.txt")
    workers = [t for t in rt if t.startswith("vpp_wk")]
    rows = []
    # Each group is one thread's; matched to the thread whose handoff node
    # saw exactly that many packets, so the attribution is read, not assumed.
    for g in groups:
        tot = g["handed"] + g["same"]
        name = next((w for w in workers
                     if rt[w].get("bpa-in2out-handoff") == tot), "?")
        rows.append({"thread": name, "handed": g["handed"], "same": g["same"],
                     "share": round(g["handed"] / tot, 4) if tot else None,
                     "translated_here": rt.get(name, {}).get("bpa-in2out")})
    h = sum(r["handed"] for r in rows)
    s = sum(r["same"] for r in rows)
    return {"per_worker": rows,
            "total_share": round(h / (h + s), 4) if h + s else None}


def main():
    run = sys.argv[1]
    mode = open(f"{run}/mode").read().strip() if os.path.exists(
        f"{run}/mode") else "on"
    addrs = [str(a) for a in POOL]
    summary = {"mode": mode}

    who = load_jsonl(f"{run}/who.log")
    recs = parse_syslog(f"{run}/syslog.txt")
    ivs = intervals(recs)
    allocs = [r for r in recs if r["kind"] == "BLOCK-ALLOC"]
    rels = [r for r in recs if r["kind"] == "BLOCK-RELEASE"]
    fill = load_json(f"{run}/join-fill.json", {}).get("joined", [])
    fill += load_json(f"{run}/join-full.json", {}).get("joined", [])
    blocks_full = parse_blocks(f"{run}/blocks-full.txt")
    blocks_end = parse_blocks(f"{run}/blocks-end.txt")
    left = []
    for p in sorted(glob.glob(f"{run}/churn-*-leave.json")):
        left += load_json(p, {}).get("left", [])

    print("=========== the internet saw only the pool ===========")
    pubs = Counter(w["pub"] for w in who)
    outside = [p for p in pubs if ipaddress.ip_address(p) not in POOL]
    check(who and not outside,
          f"all {len(who)} connections the internet received came from the "
          f"pool's public addresses ({len(pubs)} of them), none from "
          f"100.64.0.0/10",
          f"not from the pool: {outside[:5]}" if outside else "")
    ok_fill = [r for r in fill if r.get("ok")]
    refused = [r for r in fill if not r.get("ok")]
    m = re.search(r"fill_end=(\S+)", open(f"{run}/meta.txt").read()) \
        if os.path.exists(f"{run}/meta.txt") else None
    fill_end = float(m.group(1)) if m else float("inf")
    # Only what the internet saw while the pool was filling: a subscriber
    # refused then may arrive in a churn round later, and be seen then.
    seen_priv = {w["private"] for w in who if w["t"] <= fill_end}
    leaked = [r["private"] for r in refused if r["private"] in seen_priv
              and not any(o["private"] == r["private"] for o in ok_fill)]
    check(bool(ok_fill) and not leaked,
          f"filling the pool: {len(ok_fill)} subscribers got through, "
          f"{len(refused)} were refused and none of those reached the "
          f"internet",
          f"refused yet seen outside: {leaked[:5]}" if leaked else "")
    paired = [r for r in ok_fill if r.get("udp_pub") == r.get("pub")]
    check(ok_fill and len(paired) == len(ok_fill),
          f"TCP and UDP of each subscriber left by the same public address "
          f"({len(paired)} of {len(ok_fill)})")

    print()
    print("=========== blocks allocated on demand ===========")
    check(blocks_full is not None and blocks_full["allocations"] > 0
          and blocks_full["in_use"] == blocks_full["total"],
          "the dynamic pool filled: show bpa blocks reads "
          + (f"{blocks_full['in_use']}/{blocks_full['total']} in use, "
             f"{blocks_full['allocations']} allocations, "
             f"{blocks_full['refusals']} refusals" if blocks_full
             else "no dynamic pool at all"))
    check(blocks_end is not None and blocks_end["releases"] > 0
          and blocks_end["releases"] >= len(left),
          f"churn returned blocks: {len(left)} subscribers left and the box "
          + (f"counts {blocks_end['releases']} releases, "
             f"{blocks_end['allocations']} allocations in all"
             if blocks_end else "has no dynamic pool to count them in"))

    print()
    print("=========== the records name who the internet saw ===========")
    check(len(allocs) > 0,
          f"the collector received {len(allocs)} BLOCK-ALLOC and "
          f"{len(rels)} BLOCK-RELEASE records")
    wrong = []
    for w in who:
        holders = who_held(ivs, w["pub"], w["port"], w["t"])
        if holders != {w["private"]}:
            wrong.append((w, sorted(holders)))
    check(who and allocs and not wrong,
          f"for every one of the {len(who)} connections, the records "
          f"answer 'who held this public address and port at that "
          f"instant' with exactly the subscriber the internet heard from",
          "\n".join(f"{w['proto']} {w['pub']}:{w['port']} said "
                    f"{w['private']}, records say {h or 'nobody'}"
                    for w, h in wrong[:6]))
    used = 0
    unused = []
    for r in allocs:
        hit = any(w["private"] == r["sub"] and w["pub"] == r["pub"]
                  and r["lo"] <= w["port"] <= r["hi"] for w in who)
        if hit:
            used += 1
        else:
            unused.append(r)
    check(allocs and not unused,
          f"and every allocation record was used: {used} of {len(allocs)} "
          f"blocks carried a connection the internet saw, from that "
          f"subscriber",
          "\n".join(f"{r['sub']} -> {r['pub']} {r['lo']}-{r['hi']}"
                    for r in unused[:6]))

    held_box = parse_subscribers(f"{run}/subscribers-end.txt")
    state = load_json(f"{run}/state-end.json", {}).get("held", [])
    disagree = []
    for r in state:
        blocks = held_box.get(r["private"], [])
        if not any(p == r["pub"] and lo <= r["port"] <= hi
                   for p, lo, hi in blocks):
            disagree.append((r["private"], r["pub"], r["port"], blocks))
    check(state and held_box and not disagree,
          f"the box's own reading (show bpa subscriber) agrees with the "
          f"internet for all {len(state)} subscribers holding a connection "
          f"at the end",
          "\n".join(f"{a}: internet saw {p}:{po}, box says {b or 'nothing'}"
                    for a, p, po, b in disagree[:6]))
    log = open(f"{run}/logging.txt").read() if os.path.exists(
        f"{run}/logging.txt") else ""
    m = re.search(r"lines\s+(\d+) written, \d+ bytes sent, (\d+) pending",
                  log)
    d = re.search(r"spill\s+\d+ bytes on disk, \d+ replayed, (\d+) dropped",
                  log)
    sent = int(m.group(1)) if m else -1
    lines = sum(1 for _ in open(f"{run}/syslog.txt")) if os.path.exists(
        f"{run}/syslog.txt") else 0
    check(m is not None and int(m.group(2)) == 0 and d is not None
          and int(d.group(1)) == 0 and sent == lines,
          f"syslog transport: the box wrote {sent} lines, the collector "
          f"holds {lines}, nothing pending or dropped")
    summary["records"] = {"alloc": len(allocs), "release": len(rels),
                          "connections_seen": len(who),
                          "syslog_lines": lines}

    print()
    print("=========== how a dynamic pool spreads subscribers ===========")
    print("  (policy interleaved; per public address, read from the box's")
    print("   show bpa subscriber for every subscriber)")
    spreads = {}
    for p in sorted(glob.glob(f"{run}/subscribers-*.txt")):
        name = os.path.basename(p)[len("subscribers-"):-4]
        held = parse_subscribers(p)
        c = Counter(b[0][0] for b in held.values() if b)
        spreads[name] = spread(c, addrs)
    spreads.pop("end", None)
    order = sorted(spreads, key=lambda n: (n != "fresh", n != "full",
                                           len(n), n))
    for name in order:
        s = spreads[name]
        print(f"  {name:12s} {s['subscribers']:4d} holders  "
              + " ".join(f"{s['per_address'][a]:3d}" for a in addrs)
              + f"   max/min {s['max_over_min']}"
              f" (even: {even(s['subscribers'], len(addrs))})")
    summary["spread"] = spreads

    # THE SPREAD, ASSERTED (#603). Until 0.153.5 these were readings: the
    # free blocks of a slice were a stack, the spread after churn was
    # whatever the last leavers left, and this bench measured 1.67, 1.14,
    # 2.2 and 2.0 after six rounds where 1.14 was possible. A dynamic
    # `policy interleaved` pool now takes the free block on the public
    # address with the fewest subscribers, and ref/bpa_model.py
    # (DynamicSlices), replayed over this bench's churn 1000 times, holds the
    # most even spread from round 6 on in 999 runs and is one subscriber off
    # it in the last (test/test_dyn_spread.py). So: fresh, full, and every
    # round from the sixth no more than one subscriber per address off the
    # most even spread there is - TOLERANCE is that one subscriber at this
    # bench's size (9/7 against 8/7). Rounds 1 to 5 are printed and not
    # asserted: half the holders leaving a FULL pool at random can empty one
    # address to five while another keeps ten, and thirty arrivals cannot
    # fill that in one round whatever the rule.
    TOLERANCE = 0.15
    if mode == "on":
        late = sorted((int(n[len("churn"):]), n) for n in spreads
                      if re.fullmatch(r"churn\d+", n)
                      and int(n[len("churn"):]) >= 6)
        named = [("fresh", spreads.get("fresh")),
                 ("full", spreads.get("full"))] + [(n, spreads[n])
                                                   for _, n in late]
        check(late != [],
              "the churn ran to at least round 6, where the spread is "
              "asserted (BENCH_ROUNDS)")
        for name, s in named:
            if s is None:
                check(False, f"a spread reading for '{name}'")
                continue
            ev = even(s["subscribers"], len(addrs))
            got = s["max_over_min"]
            check(got is not None and ev is not None
                  and got <= ev + TOLERANCE,
                  f"{name}: {s['subscribers']} subscribers over "
                  f"{len(addrs)} addresses at max/min {got}, the most even "
                  f"being {ev} (least-loaded, #603; tolerance {TOLERANCE})")

    print()
    print("=========== throughput and the handoff (virtio, shared host) "
          "===========")
    meta = open(f"{run}/meta.txt").read() if os.path.exists(
        f"{run}/meta.txt") else ""
    tp = {}
    for tag, udp in (("tcp", False), ("udp", True)):
        if not glob.glob(f"{run}/iperf-{tag}-*.json"):
            continue
        it = iperf_sum(f"{run}/iperf-{tag}-*.json", udp)
        ic = iface_counters(f"{run}/iface-{tag}.txt")
        secs = float(re.search(rf"{tag}_seconds=(\S+)", meta).group(1)) \
            if re.search(rf"{tag}_seconds=(\S+)", meta) else 0
        rx = ic.get("ens16", {}).get("rx packets", 0)
        txerr = ic.get("ens17", {}).get("tx-error", 0)
        it["box_rx_pps"] = round(rx / secs) if secs else None
        it["box_tx_errors"] = txerr
        it["handoff"] = handoff(run, tag)
        tp[tag] = it
        print(f"  {tag}: {it['streams']} streams, {it['gbps']} Gbit/s, "
              f"{it['box_rx_pps']} packets/s in on the inside card, "
              f"{txerr} TX errors on the outside card"
              + (f", {it['retransmits']} retransmits" if not udp else
                 f", {it['lost']} of {it['packets']} datagrams lost"))
        for r in it["handoff"]["per_worker"]:
            print(f"     {r['thread']}: {r['handed']} handed to another "
                  f"worker, {r['same']} already on the owner "
                  f"(share {r['share']})")
    summary["throughput"] = tp
    if tp.get("tcp"):
        check(tp["tcp"]["gbps"] > 0.05,
              f"traffic crossed the box under load: {tp['tcp']['gbps']} "
              f"Gbit/s of TCP from {tp['tcp']['streams']} subscribers")

    json.dump(summary, open(f"{run}/summary.json", "w"), indent=1)
    print()
    print(f"  {len(FAILS)} check(s) failed" if FAILS else
          "  every check passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
