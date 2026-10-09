"""
The box's CPUs, one by one (#598): the topology - model, sockets, physical
cores, threads a core, NUMA nodes, and each logical CPU's socket, core,
node, HT siblings and plane - and the usage of each over a window. What
GetSystem's CpuTopology and CpuUsage carry, what GetHistory keeps a day of,
and what `show system cpu cores` draws.

ONE READER. The topology is tuneadvice.cpu_topology() - the tuner's own
Topology, rooted where the box's /sys is - and the planes are
tuneadvice.cpu_planes(), the reading `show system tuning advice` (#596)
judges, so this screen and that advice cannot disagree about which CPU is
which. Nothing here reads a file: the Shell and the API's sampler read, and
these decide and draw, so the screen, the fields and the tests read one
dict.

WHAT BUSY MEANS. user + nice + system + irq + softirq over the window, out
of every jiffy /proc/stat counted - not idle or iowait, and not steal,
which is said apart because on a VM it is the figure that matters (#596's
7.7% on the .166). A CPU a VPP thread polls on reads ~100% busy whatever
it forwards: a poll-mode loop never sleeps. There `polling` is true, and
a worker's figure is VPP's own busy ratio - the share of its node time its
graph took, BusyMeter's since #533 - not this one.
"""
import textwrap

from tuneadvice import cpu_spec

WIDTH = 79

#: The planes, as GetSystem says them, and as the screen does.
PLANES = ("control-plane", "main", "worker", "none")
_SAID = {"control-plane": "control plane", "main": "main thread",
         "none": "none"}


def cpu_model(cpuinfo):
    """
    The CPU's model name, from /proc/cpuinfo's first `model name` line;
    "" when there is none (an ARM kernel prints none, and a guess here would
    be a model the box does not have).
    """
    for line in (cpuinfo or "").splitlines():
        k, _, v = line.partition(":")
        if k.strip() == "model name" and v.strip():
            return " ".join(v.split())
    return ""


def topology_fields(topology, planes, model="", workers=()):
    """
    CpuTopology's fields from tuneadvice.cpu_topology() and cpu_planes():
    the totals, the cores of each socket, and a row per online CPU - its
    socket, core and node (-1 where /sys did not say), its HT siblings but
    itself, its plane, and its worker number (-1 for no worker).

    threads_per_core is the largest sibling group, not online / cores: a
    box with SMT and one sibling offline has 2 threads a core where it has
    them, and the division would say 1.5.
    """
    cpus = topology.get("cpus") or {}
    online = sorted(topology.get("online") or cpus)
    wid = {c: i for i, c in enumerate(workers or [])}
    sockets = {}
    for c, v in cpus.items():
        s = sockets.setdefault(v["socket"], {"cores": set(), "cpus": 0})
        s["cores"].add(v["core"])
        s["cpus"] += 1
    rows = []
    for c in online:
        v = cpus.get(c) or {}
        node = v.get("node")
        rows.append({"cpu": c,
                     "socket": v["socket"] if "socket" in v else -1,
                     "core": v["core"] if "core" in v else -1,
                     "numa_node": -1 if node is None else node,
                     "siblings": [x for x in v.get("siblings") or []
                                  if x != c],
                     "plane": planes.get(c, "none"),
                     "worker": wid.get(c, -1)})
    return {"model": model,
            "sockets": int(topology.get("sockets") or 0),
            "physical_cores": int(topology.get("cores") or 0),
            "threads_per_core": max((len(v["siblings"]) for v in
                                     cpus.values()), default=0),
            "numa_nodes": int(topology.get("nodes") or 0),
            "logical_cpus": len(online),
            "socket_cores": [{"socket": s, "physical_cores": len(v["cores"]),
                              "logical_cpus": v["cpus"]}
                             for s, v in sorted(sockets.items())],
            "cpus": rows}


def usage_rows(window, ms, threads=None):
    """
    CpuUsage rows from a tuneadvice.cpu_window() reading `ms` milliseconds
    long: each CPU's busy and steal in percent, and - for a CPU a VPP thread
    runs on, `threads` {cpu: {"polling", "busy_ratio"}} - whether that
    thread polls, and a worker's busy ratio (0 for the main thread, which
    VPP measures none for).
    """
    threads = threads or {}
    out = []
    for c in sorted(window):
        t = threads.get(c) or {}
        out.append({"cpu": c,
                    "busy_percent": float(window[c]["busy"]),
                    "steal_percent": float(window[c]["steal"]),
                    "polling": bool(t.get("polling")),
                    "busy_ratio": float(t.get("busy_ratio") or 0.0),
                    "window_ms": int(ms)})
    return out


def _plane(row):
    """A CPU's plane, said: "worker 3", "main thread", "control plane"."""
    if row["plane"] == "worker" and row["worker"] >= 0:
        return f"worker {row['worker']}"
    return _SAID.get(row["plane"], row["plane"])


def _dash(v):
    """A topology number, or "-" for one /sys did not give (-1)."""
    return "-" if v < 0 else str(v)


def _wrap(text, indent=""):
    """`text` wrapped to WIDTH."""
    return textwrap.wrap(text, WIDTH, initial_indent=indent,
                         subsequent_indent=indent, break_on_hyphens=False)


def render(facts):
    """
    `show system cpu cores`, as rows no wider than WIDTH, from {"topology":
    topology_fields(), "usage": usage_rows(), "said": where the usage is
    from}. A CPU with no usage row reads "-": not measured, never 0.
    """
    t = facts.get("topology") or {}
    use = {u["cpu"]: u for u in facts.get("usage") or []}
    out = [t.get("model") or "CPU model not reported by the kernel"]
    per = sorted({s["physical_cores"] for s in t.get("socket_cores") or []})
    if len(per) == 1 and t.get("sockets", 0) > 1:
        each = f" ({per[0]} a socket)"
    elif len(per) > 1:
        each = " (" + ", ".join(f"{s['physical_cores']} on socket "
                                f"{s['socket']}" for s in t["socket_cores"]) \
            + ")"
    else:
        each = ""
    out += _wrap(f"{t.get('sockets', 0)} socket(s), "
                 f"{t.get('physical_cores', 0)} physical core(s){each}, "
                 f"{t.get('threads_per_core', 0)} thread(s) a core, "
                 f"{t.get('numa_nodes', 0)} NUMA node(s), "
                 f"{t.get('logical_cpus', 0)} CPU(s) online")
    out += _wrap(facts.get("said") or "")
    out += _wrap("busy is user, nice, system, irq and softirq; steal is "
                 "apart. A polling VPP thread's CPU reads ~100% busy "
                 "whatever it forwards: a worker's load is VPP's own busy "
                 "ratio, under VPP.")
    out.append("")
    out.append(f"  {'cpu':>3} {'sock':>4} {'core':>4} {'node':>4}  "
               f"{'siblings':<10} {'plane':<13} {'busy':>6} {'steal':>6}  VPP")
    for r in t.get("cpus") or []:
        u = use.get(r["cpu"])
        sib = cpu_spec(r["siblings"]) or "-"
        if len(sib) > 10:
            sib = sib[:9] + "+"
        vpp = ""
        if u and u["polling"]:
            vpp = (f"polling {u['busy_ratio']:.0%}" if r["plane"] == "worker"
                   else "polling")
        busy = (f"{u['busy_percent']:.1f}%", f"{u['steal_percent']:.1f}%") \
            if u else ("-", "-")
        out.append(f"  {r['cpu']:>3} {_dash(r['socket']):>4} "
                   f"{_dash(r['core']):>4} {_dash(r['numa_node']):>4}  "
                   f"{sib:<10} {_plane(r):<13} {busy[0]:>6} {busy[1]:>6}"
                   f"  {vpp}".rstrip())
    return out
