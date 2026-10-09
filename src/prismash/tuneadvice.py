"""
`show system tuning advice` (#596): the box suggests its own tuning from the
resources it has and from what it measures - and, when it is a virtual
machine, how its host should pin it.

THE CASE IT IS BUILT FROM. 10.40.200.166 is Proxmox VM 102 on sv01 (two Xeon
E5-2620, 6 cores / 12 threads a socket, the X520 on NUMA node 1), with 8
vCPUs: control plane 2, main thread dedicated, 5 workers. Its workers ran at
uneven speeds with no traffic to speak of - `show runtime`'s loops/s per
worker 2.98M / 3.29M / 3.23M / 2.41M / 3.23M, two of them sharing a physical
core through HT - and the guest measured ~7.7% steal. Pinned by hand, one
vCPU per host thread on node 1 (main 1, workers 3 5 7 9 11, both
control-plane vCPUs on 13, the sibling of the idle main thread's core), they
ran 2.86M / 3.30M / 3.22M / 3.21M / 3.25M and the steal, 3.2%, was all on
the two control-plane vCPUs sharing that one host thread. Everything that
fixed it was read off the host by hand; the box had the signals to say most
of it itself. Those numbers are this module's fixtures (test_tuneadvice.py).

READ ONLY, AND CHEAP. Nothing here changes the box: applying a suggestion is
the operator's commit. What is measured is two readings a window apart -
/proc/stat, and `show runtime` twice rather than `clear runtime` and once,
which would zero the counters every other reader (the API's busy ratios,
another operator's screen) is reading. `show runtime` takes the worker
barrier for the copy, as the API's BusyMeter has every second since #533.

THE RULES ARE THE TUNER'S. The sizing table is appliance/prisma-routing-tune's
plan() run on a box of each size (load_tuner(), Topology.given()), and the
hugepage check is the tuner's own readback of what the kernel did with what
it was asked (_rb_hugepages). A second copy of either here would be a
second answer the first time one of them changes.

Pure, apart from load_tuner(): the Shell reads the facts (tuning_advice_facts
in prismash.py) and everything below decides and draws from them, so the
screen, the API's fields and the tests all read one dict.
"""
import importlib.machinery
import importlib.util
import os
import re
import statistics
import textwrap

from system import is_number

WIDTH = 79

# -------------------------------------------------------------- thresholds
#
# Chosen against the reference case, and written down so a reader can argue
# with a number rather than with a feeling.

#: A worker this far below the median loops/s of its peers, at low load, is
#: uneven. The .166's "before" had worker 3 at 2.41M against a median of
#: 3.23M (25% below); its "after" had the slowest at 2.86M against 3.22M
#: (11% below) - pinned, one physical core each, and idle. 15% sits between
#: the two with room on both sides. Against the median, not the fastest: the
#: question is which worker is held back, and a median names it.
UNEVEN_BELOW_MEDIAN = 0.15
#: Low load: every worker under this many packets per input call. Above it a
#: worker's loop rate follows its traffic, and a busy worker polls slower
#: than an idle one for a reason that is not its core.
LOW_LOAD_VECTORS = 2.0
#: A worker running fewer main loops a second than this is not polling (it
#: waits for interrupts): the API's SLEEPING_LOOPS, measured in
#: lab/api-grpc.sh's dataplane (98 a second asleep, 750 thousand polling).
SLEEPING_LOOPS = 1000
#: Steal on the dataplane's vCPUs (workers, and the main thread's when it has
#: one of its own), in percent of the window. The .166 measured 7.7% before
#: pinning and 0 on those vCPUs after; 1% is a vCPU preempted ten
#: milliseconds a second, which a poll-mode worker feels as burst loss.
STEAL_DATAPLANE = 1.0
#: The control plane's CPUs busier than this, with the main thread sharing
#: them, is a main thread that waits for FRR and the VTY.
CP_BUSY_DEDICATE = 50.0
#: ...and idler than this, with the main thread on a core of its own and
#: fewer workers than SMALL_BOX_WORKERS, is a core that would forward more
#: as a worker.
CP_IDLE_SHARE = 10.0
SMALL_BOX_WORKERS = 4
#: The measurement window, in seconds: the default, and the bounds.
WINDOW_DEFAULT = 5
WINDOW_MIN, WINDOW_MAX = 1, 60
#: QEMU's own threads - its main loop, I/O threads, the vhost of a virtio
#: management card - need a few host threads, not a node: the .166's
#: hand-written script gave them four siblings and they fit. Taken from the
#: END of the node, so the first workers' siblings stay quiet as well.
EMULATOR_THREADS = 4
#: The sizing table's rows, in vCPUs.
SIZING_VCPUS = (4, 6, 8, 10, 12, 16, 20, 24, 28, 32)

SEVERITY_WARN, SEVERITY_INFO = "warn", "info"
RESTART_NONE, RESTART_DATAPLANE, RESTART_REBOOT = "none", "dataplane", "reboot"

#: Where the tuner is, in the order looked: the mgmt image's copy beside the
#: VTY's modules, the host's, and the source tree's. PRISMA_TUNER first, so a
#: lab or a test can name one.
TUNER_PATHS = ("/usr/lib/prisma-routing/prisma-routing-tune",
               "/usr/bin/prisma-routing-tune",
               os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "..", "appliance", "prisma-routing-tune"))


def load_tuner(paths=None):
    """
    appliance/prisma-routing-tune as a module, or None when none of `paths`
    is there. Loaded, not run: nothing in it executes on import but its
    constants, and the advice calls plan() and the readbacks it needs.
    """
    import sys
    if "prisma_routing_tune" in sys.modules:
        return sys.modules["prisma_routing_tune"]
    want = [os.environ.get("PRISMA_TUNER")] if paths is None else list(paths)
    if paths is None:
        want += list(TUNER_PATHS)
    for path in want:
        if not path or not os.path.isfile(path):
            continue
        try:
            loader = importlib.machinery.SourceFileLoader(
                "prisma_routing_tune", path)
            spec = importlib.util.spec_from_file_location(
                "prisma_routing_tune", path, loader=loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
        except Exception:        # noqa: BLE001 - a tuner that will not load
            continue             # is the next path's turn, or None
        sys.modules["prisma_routing_tune"] = mod
        return mod
    return None


# ------------------------------------------------------------ the readings

def cpu_spec(cpus):
    """[0, 1, 2, 5] -> '0-2,5'; '' for none."""
    cpus = sorted(set(cpus))
    if not cpus:
        return ""
    runs, start, prev = [], cpus[0], cpus[0]
    for c in cpus[1:]:
        if c == prev + 1:
            prev = c
            continue
        runs.append((start, prev))
        start = prev = c
    runs.append((start, prev))
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def cpu_parse(spec):
    """'0-2,5' -> [0, 1, 2, 5]; what is not a list is left out."""
    out = []
    # Every blank out, the newline the kernel's files end with included:
    # "2-7\n" read as a list of nothing was every CPU isolated read as none.
    for part in "".join((spec or "").split()).split(","):
        a, _, b = part.partition("-")
        if is_number(a) and (not b or is_number(b)):
            out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return sorted(set(out))


def cpu_topology(tune, root="/", online=None):
    """
    The CPUs as this box sees them, read once and the one reader of them
    (#596; GetSystem's CPU topology, #598, is to be built on this and not
    beside it): {"online", "sockets", "cores", "nodes", "smt", "node_of":
    {cpu: node}, "siblings": {cpu: [cpus]}, "cpus": {cpu: {"socket",
    "core", "node", "siblings"}}}.

    Through the tuner's own Topology rooted at `root` (/host in the
    management container), so the box and its boot-time plan see the same
    cores. `online` is /sys's online list when it was read; the topology's
    CPUs otherwise. A tuner that could not be loaded, or a /sys with no
    topology, is a box of `online` CPUs and nothing else known.
    """
    topo = None
    if tune is not None:
        try:
            topo = tune.Topology(root or "/")
        except (SystemExit, OSError, ValueError):
            topo = None
    cpus = topo.cpus if topo else {}
    node_of = {c: n for n, cs in (topo.nodes.items() if topo else ())
               for c in cs}
    return {"online": list(online or sorted(cpus)),
            "sockets": len({v[0] for v in cpus.values()}),
            "cores": len({(v[0], v[1]) for v in cpus.values()}),
            "nodes": len(topo.nodes) if topo else 0,
            "smt": any(len(v[2]) > 1 for v in cpus.values()),
            "node_of": node_of,
            "siblings": {c: v[2] for c, v in cpus.items()},
            "cpus": {c: {"socket": v[0], "core": v[1],
                         "node": node_of.get(c), "siblings": v[2]}
                     for c, v in sorted(cpus.items())}}


def cpu_planes(topology, layout):
    """
    {cpu: "worker" | "main" | "control-plane"}: which plane each online CPU
    serves this boot, from cpu_topology() and the layout (`show threads`
    and the control plane's fence) - the per-CPU plane #598 asks GetSystem
    for, and the roles guest_roles() pins by.
    """
    out = {c: "control-plane" for c in topology.get("online") or []}
    if layout.get("main") is not None:
        out[layout["main"]] = "main"
    for c in layout.get("workers") or []:
        out[c] = "worker"
    return dict(sorted(out.items()))


_THREAD = re.compile(r"^\s*(\d+)\s+(vpp_main|vpp_wk_(\d+))\s.*?\(\d+\)\s+"
                     r"(\d+)\s+(\d+)\s+(\d+)")


def parse_threads(text):
    """
    `show threads`: {"main": cpu, "workers": {worker: cpu}}, the CPU being
    the lcore VPP pinned the thread to. main is None when no row says.
    """
    out = {"main": None, "workers": {}}
    for line in (text or "").splitlines():
        m = _THREAD.match(line)
        if not m:
            continue
        if m.group(2) == "vpp_main":
            out["main"] = int(m.group(4))
        else:
            out["workers"][int(m.group(3))] = int(m.group(4))
    return out


_RT_ROW = re.compile(r"^(.*?)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+(?:e[-+]?\d+)?)"
                     r"\s+([\d.]+)\s*$")
_RT_LOOPS = re.compile(r"loops/sec\s+([\d.]+(?:e[-+]?\d+)?)")


def parse_runtime(text):
    """
    `show runtime`, per thread: {"main" | worker: {"loops_hdr", "calls",
    "vectors", "polling"}} - the header's own loops/sec (VPP's smoothed
    rate), and the polling input nodes' cumulative calls (the most of any:
    each runs once a loop) and vectors (their sum: what came in).

    A box with no workers prints no `Thread` line, and its one block is the
    main thread's.
    """
    out, cur = {}, None
    for line in (text or "").splitlines():
        m = re.match(r"^Thread \d+ vpp_(main|wk_(\d+))", line)
        if m:
            key = "main" if m.group(1) == "main" else int(m.group(2))
            cur = out.setdefault(key, {"loops_hdr": None, "calls": 0,
                                       "vectors": 0, "polling": False})
            continue
        if line.startswith("Time ") and cur is None:
            cur = out.setdefault("main", {"loops_hdr": None, "calls": 0,
                                          "vectors": 0, "polling": False})
        if cur is None:
            continue
        h = _RT_LOOPS.search(line)
        if line.startswith("Time ") and h:
            cur["loops_hdr"] = float(h.group(1))
            continue
        r = _RT_ROW.match(line)
        if not r or r.group(1).strip() in ("Name", ""):
            continue
        head = r.group(1).rstrip()
        if not head.endswith("polling"):
            continue
        calls, vectors = int(r.group(2)), int(r.group(3))
        cur["polling"] = True
        cur["calls"] = max(cur["calls"], calls)
        cur["vectors"] += vectors
    return out


def thread_rates(before, after, seconds):
    """
    {"main" | worker: {"loops_per_second", "vectors_per_call"}} between two
    parse_runtime() readings `seconds` apart: the polling calls' growth over
    the window (the loop rate, measured, with no `clear runtime`), and the
    packets per poll. A thread polling nothing keeps VPP's own header rate.
    """
    out = {}
    for key, a in after.items():
        b = before.get(key)
        loops = a["loops_hdr"] or 0.0
        vpc = 0.0
        if b is not None and a["polling"] and seconds > 0 \
                and a["calls"] >= b["calls"]:
            dc = a["calls"] - b["calls"]
            loops = dc / seconds
            vpc = (a["vectors"] - b["vectors"]) / dc if dc else 0.0
        out[key] = {"loops_per_second": round(loops, 1),
                    "vectors_per_call": round(max(0.0, vpc), 2)}
    return out


_HW_HEAD = re.compile(r"^(\S+)\s+(\d+)\s+(up|down)\s+\S+")
_HW_RXQ = re.compile(r"^\s+(\d+)\s+(vpp_main|vpp_wk_(\d+))\s+\(\d+\)\s+(\w+)")


def parse_hardware(text):
    """
    `show hardware-interfaces`: the dataplane's cards, those DPDK drives -
    the ones that say `rx: queues` - each {"name", "driver", "pci",
    "numa", "rx_queues", "placement": {queue: "main" | worker}, "rss"}.
    numa is None when the card declares none (a virtio guest says 0 or
    nothing; a vdev has no PCI line at all).
    """
    cards, cur, in_rx, after_mac = [], None, False, False
    for raw in (text or "").splitlines():
        line = raw.rstrip("\r")
        m = _HW_HEAD.match(line)
        if m:
            cur = {"name": m.group(1), "driver": "", "pci": "", "numa": None,
                   "rx_queues": None, "placement": {}, "rss": ""}
            cards.append(cur)
            in_rx = after_mac = False
            continue
        if cur is None:
            continue
        s = line.strip()
        if s == "RX Queues:":
            in_rx = True
            continue
        if in_rx:
            q = _HW_RXQ.match(line)
            if q:
                cur["placement"][int(q.group(1))] = \
                    "main" if q.group(2) == "vpp_main" else int(q.group(3))
                continue
            if s.startswith("queue "):
                continue
            in_rx = False
        if s.startswith("Ethernet address"):
            after_mac = True
            continue
        if after_mac:
            # The line under the MAC is the driver's own name for the card:
            # "Intel 82599", "Red Hat Virtio", "null".
            cur["driver"] = s
            after_mac = False
            continue
        r = re.match(r"^rx: queues (\d+)", s)
        if r:
            cur["rx_queues"] = int(r.group(1))
            continue
        p = re.match(r"^pci: .*address (\S+)(?: numa (-?\d+))?", s)
        if p:
            cur["pci"] = p.group(1)
            if p.group(2) is not None and int(p.group(2)) >= 0:
                cur["numa"] = int(p.group(2))
            continue
        if s.startswith("rss active:"):
            cur["rss"] = s.split(":", 1)[1].strip()
    return [c for c in cards if c["rx_queues"] is not None]


def parse_proc_stat(text):
    """/proc/stat's per-CPU rows: {cpu: (total, idle, steal)} in jiffies."""
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"^cpu(\d+)\s+(.*)$", line)
        if not m:
            continue
        v = [int(x) for x in m.group(2).split() if is_number(x)]
        if len(v) < 8:
            continue
        # user nice system idle iowait irq softirq steal [guest guest_nice]:
        # guest time is already inside user, so the first eight are the whole.
        out[int(m.group(1))] = (sum(v[:8]), v[3] + v[4], v[7])
    return out


def cpu_window(before, after):
    """{cpu: {"busy", "steal"}} in percent of the window, per CPU."""
    out = {}
    for c, a in after.items():
        b = before.get(c)
        if b is None:
            continue
        total = a[0] - b[0]
        if total <= 0:
            continue
        idle, steal = a[1] - b[1], a[2] - b[2]
        out[c] = {"busy": round(100.0 * (total - idle - steal) / total, 1),
                  "steal": round(100.0 * steal / total, 2)}
    return out


def meminfo(text):
    """/proc/meminfo as {key: kB or count}; what is not a number is left out."""
    out = {}
    for line in (text or "").splitlines():
        k, _, v = line.partition(":")
        w = v.split()
        if w and is_number(w[0]):
            out[k.strip()] = int(w[0])
    return out


#: What systemd-detect-virt says for a virtual machine, and what DMI says.
_DMI_VENDORS = (("QEMU", "kvm"), ("KVM", "kvm"), ("VMware", "vmware"),
                ("innotek", "oracle"), ("Xen", "xen"), ("Parallels", "parallels"),
                ("Amazon EC2", "amazon"), ("Google", "google"))


def detect_virt(answer="", vendor="", product="", cpuinfo=""):
    """
    (kind, source): what the box runs on, by the first reading that names
    it - systemd-detect-virt's answer through the host's door, then DMI's
    vendor and product, then the CPUID `hypervisor` flag, which says that
    and not by whom. ("none", source) is bare metal; ("", "") is a box none
    of them could answer for.
    """
    w = (answer or "").strip().split()
    if w and re.fullmatch(r"[a-z0-9_-]+", w[0]):
        return w[0], "systemd-detect-virt"
    who = f"{vendor or ''} {product or ''}"
    if "Microsoft" in who and "Virtual" in who:
        return "microsoft", "DMI"
    for name, kind in _DMI_VENDORS:
        if name in who:
            return kind, "DMI"
    flags = re.search(r"^flags\s*:(.*)$", cpuinfo or "", re.M)
    if flags:
        if "hypervisor" in flags.group(1).split():
            return "hypervisor", "CPUID"
        return "none", "CPUID"
    if vendor or product:
        return "none", "DMI"
    return "", ""


def parse_host_topology(text):
    """
    The host's `lscpu -e=CPU,NODE,SOCKET,CORE`, pasted, and the NIC's
    numa_node: {"cpus": {cpu: (node, socket, core)}, "nic_node", "errors"}.

    The NIC's node is a line of its own - `nic-node 1`, `numa_node 1`, or
    the bare number `cat .../numa_node` prints. -1 (none declared) is None.
    A row lscpu prints with `-` for the node (a host with no NUMA) is node 0.
    """
    cpus, nic, errors = {}, None, []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s or s.upper().startswith("CPU"):
            continue
        w = s.replace(":", " ").split()
        if len(w) == 2 and w[0] in ("nic-node", "numa_node", "nic"):
            w = w[1:]
        if len(w) == 1 and re.fullmatch(r"-?\d+", w[0]):
            nic = int(w[0]) if int(w[0]) >= 0 else None
            continue
        if len(w) >= 4 and is_number(w[0]):
            node = 0 if w[1] == "-" else w[1]
            try:
                cpus[int(w[0])] = (int(node), int(w[2]), int(w[3]))
                continue
            except ValueError:
                pass
        errors.append(s)
    return {"cpus": cpus, "nic_node": nic, "errors": errors}


# ----------------------------------------------------------- the findings

def _m(n):
    """2412345.0 -> '2.41M', 245000 -> '245k'."""
    if n >= 1e6:
        return f"{n / 1e6:.2f}M"
    if n >= 1e3:
        return f"{n / 1e3:.0f}k"
    return f"{n:.0f}"


def _finding(key, severity, title, measured, threshold, why):
    """One finding, in the shape the screen and the API both read."""
    return {"key": key, "severity": severity, "title": title,
            "measured": measured, "threshold": threshold, "why": why}


def dataplane_cpus(f):
    """The CPUs the dataplane polls on: the workers', and the main thread's
    when it has a core of its own."""
    lay = f.get("layout") or {}
    out = list(lay.get("workers") or [])
    if lay.get("main_dedicated") and lay.get("main") is not None:
        out.append(lay["main"])
    return sorted(set(out))


def _avg(cpus, window, field):
    """The mean of `field` over `cpus` in a cpu_window(), or None."""
    vals = [window[c][field] for c in cpus if c in window]
    return round(sum(vals) / len(vals), 2) if vals else None


def find_uneven(f):
    """The uneven-workers finding, or (None, why it was not judged)."""
    rates = (f.get("window") or {}).get("threads") or {}
    lay = f.get("layout") or {}
    wk = {k: v for k, v in rates.items() if k != "main"}
    polling = {k: v for k, v in wk.items()
               if v["loops_per_second"] >= SLEEPING_LOOPS}
    if len(polling) < 2:
        return None, "uneven workers: not judged, fewer than two polling"
    if any(v["vectors_per_call"] >= LOW_LOAD_VECTORS for v in polling.values()):
        return None, ("uneven workers: not judged, the workers carry "
                      "traffic (a loop rate follows its packets)")
    med = statistics.median(v["loops_per_second"] for v in polling.values())
    slow = sorted((k, v["loops_per_second"]) for k, v in polling.items()
                  if v["loops_per_second"] < med * (1 - UNEVEN_BELOW_MEDIAN))
    if not slow:
        return None, (f"uneven workers: none more than "
                      f"{UNEVEN_BELOW_MEDIAN:.0%} below the median "
                      f"{_m(med)} loops/s")
    cpus = lay.get("workers") or []
    said = "; ".join(
        f"worker {k}" + (f" (cpu {cpus[k]})" if k < len(cpus) else "")
        + f" {_m(v)} loops/s, {1 - v / med:.0%} below" for k, v in slow)
    every = " / ".join(_m(polling[k]["loops_per_second"])
                       for k in sorted(polling))
    virt = (f.get("virt") or {}).get("kind")
    where = ("its vCPU shares a host thread - with another vCPU, an HT "
             "sibling, or another guest" if virt not in ("", "none")
             else "its CPU shares a physical core (an HT sibling) or is "
                  "preempted")
    return _finding(
        "uneven-workers", SEVERITY_WARN, "uneven workers",
        f"{said}; the median is {_m(med)} (all: {every})",
        f"more than {UNEVEN_BELOW_MEDIAN:.0%} below the median, every worker "
        f"under {LOW_LOAD_VECTORS:g} vectors/call",
        f"idle poll-mode workers spin at the same rate on cores of their "
        f"own; one slower is held back: {where}. Under load it forwards "
        f"that much less, and its subscribers see it."), None


def find_steal(f):
    """The dataplane-steal finding, or (None, what was measured)."""
    win = (f.get("window") or {}).get("cpus") or {}
    dp = dataplane_cpus(f)
    st = _avg(dp, win, "steal")
    lay = f.get("layout") or {}
    cp = [c for c in (lay.get("control_plane") or []) if c not in dp]
    cps = _avg(cp, win, "steal")
    if st is None:
        return None, "steal: not measured"
    cp_said = (f"; {cps:.1f}% on the control plane's CPUs" if cps else "")
    if st <= STEAL_DATAPLANE:
        return None, (f"steal: {st:.2f}% on the dataplane's CPUs{cp_said}"
                      + (" - the control plane's vCPUs share a host thread "
                         "by design" if cps and cps > STEAL_DATAPLANE else ""))
    worst = max(dp, key=lambda c: win.get(c, {}).get("steal", 0))
    return _finding(
        "steal", SEVERITY_WARN, "steal on the dataplane's CPUs",
        f"{st:.1f}% of the window on the dataplane's CPUs "
        f"({cpu_spec(dp)}), cpu {worst} the most at "
        f"{win[worst]['steal']:.1f}%{cp_said}",
        f"more than {STEAL_DATAPLANE:g}% on any worker's or the dedicated "
        f"main thread's CPU",
        "steal is time the hypervisor ran something else on the host thread "
        "this vCPU was given. A poll-mode worker off its CPU drops what "
        "arrives meanwhile; the cure is on the host, not here."), None


def find_queues(f):
    """The queue findings, and what was checked."""
    lay = f.get("layout") or {}
    workers = len(lay.get("workers") or [])
    found, ok = [], []
    for c in f.get("cards") or []:
        q = c["rx_queues"] or 0
        on = set(c["placement"].values())
        if workers and "main" in on:
            found.append(_finding(
                f"queues-main:{c['name']}", SEVERITY_WARN,
                f"{c['name']}: a queue on the main thread",
                f"queue(s) {cpu_spec([k for k, v in c['placement'].items() if v == 'main'])} "
                f"polled by vpp_main, with {workers} worker(s)",
                "every queue on a worker when there are workers",
                "the main thread also expires sessions under the worker "
                "barrier and answers the VTY; packets it polls wait for "
                "that."))
        if workers and q < workers:
            found.append(_finding(
                f"queues:{c['name']}", SEVERITY_WARN,
                f"{c['name']}: fewer queues than workers",
                f"{q} rx queue(s) ({c['driver'] or 'driver unknown'}) for "
                f"{workers} workers: {workers - q} receive nothing from it",
                "one rx queue per worker",
                "RSS spreads a card's flows over its queues, one queue a "
                "worker; a worker with no queue forwards only what others "
                "hand it."))
        elif workers and q > workers and q % workers:
            found.append(_finding(
                f"queues-uneven:{c['name']}", SEVERITY_INFO,
                f"{c['name']}: queues do not divide over the workers",
                f"{q} rx queues over {workers} workers",
                "a multiple of the workers",
                "some workers poll one queue more than others, and RSS "
                "gives each queue the same share of flows."))
        else:
            ok.append(f"queues: {c['name']} {q} for {workers} worker(s)")
    return found, ok


def find_numa(f):
    """A worker on another NUMA node than the card it polls (where seen)."""
    node_of = (f.get("topology") or {}).get("node_of") or {}
    wcpus = (f.get("layout") or {}).get("workers") or []
    found, ok = [], []
    for c in f.get("cards") or []:
        if c["numa"] is None:
            ok.append(f"NUMA: {c['name']} declares no node, not judged")
            continue
        off = sorted({w for w in c["placement"].values()
                      if isinstance(w, int) and w < len(wcpus)
                      and node_of.get(wcpus[w], c["numa"]) != c["numa"]})
        if not off:
            ok.append(f"NUMA: {c['name']}'s workers on its node {c['numa']}")
            continue
        found.append(_finding(
            f"numa:{c['name']}", SEVERITY_WARN,
            f"{c['name']}: polled across NUMA nodes",
            f"card on node {c['numa']}; worker(s) "
            + ", ".join(f"{w} (cpu {wcpus[w]}, node {node_of.get(wcpus[w])})"
                        for w in off),
            "every worker polling a card on the card's node",
            "each packet then crosses the socket interconnect twice, and "
            "that costs more than the extra worker earns."))
    return found, ok


def find_hugepages(f):
    """Hugepages short of what the box asked the kernel for, by the tuner's
    own readback of the kernel command line (_rb_hugepages)."""
    mem = f.get("memory") or {}
    effect = mem.get("effect")
    said = mem.get("effect_said") or ""
    if effect is None:
        return None, "hugepages: none asked on the kernel command line"
    if effect == "in-effect":
        return None, f"hugepages: {said}"
    if effect == "cannot-tell":
        return None, f"hugepages: cannot tell, {said}"
    return _finding(
        "hugepages", SEVERITY_WARN, "hugepages short",
        said, "every page the kernel command line asks for, reserved",
        "the dataplane's heap and packet buffers live in them; short, the "
        "dataplane starts on the first-boot profile or not at all."), None


def find_siblings(f):
    """Two workers on HT siblings of one core - where the box can see HT,
    which a guest whose vCPUs are presented as cores cannot."""
    topo = f.get("topology") or {}
    sib = topo.get("siblings") or {}
    wcpus = (f.get("layout") or {}).get("workers") or []
    if not topo.get("smt"):
        return None, "HT siblings: none visible to this box"
    pairs = []
    for i, a in enumerate(wcpus):
        for j in range(i + 1, len(wcpus)):
            if wcpus[j] in (sib.get(a) or []):
                pairs.append((i, j))
    if not pairs:
        return None, "HT siblings: no two workers on one core"
    return _finding(
        "ht-siblings", SEVERITY_WARN, "two workers on one physical core",
        "; ".join(f"workers {i} and {j} (cpus {wcpus[i]}, {wcpus[j]})"
                  for i, j in pairs),
        "one worker per physical core",
        "two poll-mode threads on one core share its execution units and "
        "each runs at well under a core's speed; the tuner takes one "
        "thread a core and isolates the sibling."), None


def find_main(f):
    """A dedicated main thread an idle box does not need, or a shared one a
    busy control plane holds up."""
    lay = f.get("layout") or {}
    win = (f.get("window") or {}).get("cpus") or {}
    cp = [c for c in (lay.get("control_plane") or [])
          if c not in (lay.get("workers") or [])]
    busy = _avg(cp, win, "busy")
    workers = len(lay.get("workers") or [])
    if busy is None or lay.get("main") is None:
        return None, "main thread: not judged, no layout or window"
    if lay.get("main_dedicated"):
        if busy < CP_IDLE_SHARE and 0 < workers < SMALL_BOX_WORKERS:
            return _finding(
                "main-idle", SEVERITY_INFO,
                "a dedicated main thread on an idle box",
                f"control plane {busy:.0f}% busy over the window, "
                f"{workers} worker(s); the main thread has cpu "
                f"{lay['main']} to itself",
                f"control plane under {CP_IDLE_SHARE:g}% busy and fewer "
                f"than {SMALL_BOX_WORKERS} workers",
                f"shared, that core becomes worker {workers}: "
                f"{100 // workers}% more forwarding, at the cost of the "
                f"main thread waiting for whatever the control plane is "
                f"doing - here, little."), None
        return None, (f"main thread: dedicated, control plane "
                      f"{busy:.0f}% busy")
    if busy >= CP_BUSY_DEDICATE and workers >= 2:
        return _finding(
            "main-busy", SEVERITY_WARN,
            "the main thread shares a busy control plane",
            f"control plane {busy:.0f}% busy over the window, and the "
            f"main thread runs on cpu {lay['main']} with it",
            f"control plane at {CP_BUSY_DEDICATE:g}% busy or more",
            "the main thread takes the worker barrier once a second to "
            "expire sessions, and forwarding waits while it is off the CPU "
            "- here, behind FRR, sshd and the VTY."), None
    return None, f"main thread: shared, control plane {busy:.0f}% busy"


def find_isolation(f):
    """Workers on CPUs this boot does not isolate, on bare metal - the box
    between two plans (#486), or tuned by hand."""
    lay = f.get("layout") or {}
    if (f.get("virt") or {}).get("kind") not in ("none",):
        return None, None
    iso = set(lay.get("isolated") or [])
    loose = [c for c in (lay.get("workers") or []) if c not in iso]
    if not loose:
        return None, "isolation: every worker's CPU isolated"
    return _finding(
        "isolation", SEVERITY_WARN, "workers the kernel does not isolate",
        f"worker CPU(s) {cpu_spec(loose)} not in the kernel's isolated "
        f"list ({cpu_spec(iso) or 'none'})",
        "every worker's CPU isolated at boot",
        "the scheduler may run anything on a CPU it was not told to leave "
        "alone, and a worker preempted drops what arrives meanwhile."), None


def findings(f):
    """(found, checked): every finding, worst first, and a line for each
    check that ran and found nothing - the summary says what was looked at
    as well as what was wrong."""
    found, checked = [], []
    for fn in (find_uneven, find_steal, find_hugepages, find_siblings,
               find_main, find_isolation):
        got, ok = fn(f)
        if got:
            found.append(got)
        if ok:
            checked.append(ok)
    for fn in (find_queues, find_numa):
        got, ok = fn(f)
        found += got
        checked += ok
    found.sort(key=lambda x: x["severity"] != SEVERITY_WARN)
    return found, checked


def virtualized(f):
    """True for a box something hosts; False on bare metal or unknown."""
    return (f.get("virt") or {}).get("kind") not in ("", "none", None)


def box_recommendations(f, found):
    """
    What to type on the box, each {"title", "lines", "restart", "evidence"}
    - the finding behind it, so nothing is recommended without a
    measurement.
    """
    out = []
    keys = {x["key"]: x for x in found}
    if "main-idle" in keys:
        out.append({"title": "give the main thread's core to a worker",
                    "lines": ["configure", "system", "cpu",
                              "main-thread shared", "commit"],
                    "restart": RESTART_REBOOT, "evidence": "main-idle"})
    if "main-busy" in keys:
        out.append({"title": "give VPP's main thread a core of its own",
                    "lines": ["configure", "system", "cpu",
                              "main-thread dedicated", "commit"],
                    "restart": RESTART_REBOOT, "evidence": "main-busy"})
    for key in ("isolation", "ht-siblings", "hugepages"):
        if key in keys:
            out.append({"title": {"isolation": "boot into the plan: the "
                                               "isolation is on the kernel "
                                               "command line",
                                  "ht-siblings": "boot into the plan: it "
                                                 "takes one thread a core",
                                  "hugepages": "boot again: hugepages are "
                                               "reserved at boot"}[key],
                        "lines": ["system reboot"],
                        "restart": RESTART_REBOOT, "evidence": key})
    for x in found:
        if x["key"].startswith("queues:"):
            card = x["key"].split(":", 1)[1]
            out.append({"title": f"reopen {card} once the host gives it "
                                 f"more queues" if virtualized(f) else
                                 f"reopen {card} with a queue per worker",
                        "lines": ["system dataplane restart"],
                        "restart": RESTART_DATAPLANE, "evidence": x["key"]})
    return out


# --------------------------------------------------- the hypervisor's half

def guest_roles(f):
    """
    {vcpu: ("main",) | ("cp",) | ("worker", n)}: what each of this box's
    CPUs does, from its own layout - a guest's CPU n is the host's vCPU n
    (QEMU's thread `CPU n/KVM`). A CPU in no role is the control plane's:
    it is where the scheduler runs everything not pinned.
    """
    lay = f.get("layout") or {}
    planes = cpu_planes(f.get("topology") or {}, lay)
    for c in lay.get("control_plane") or []:
        planes.setdefault(c, "control-plane")
    wid = {c: i for i, c in enumerate(lay.get("workers") or [])}
    return {c: ("worker", wid[c]) if p == "worker" else
            ("main",) if p == "main" else ("cp",)
            for c, p in sorted(planes.items())}


def host_cores(host, node):
    """The host's physical cores on `node`, [(cpu, ...)] by their first CPU,
    from parse_host_topology()."""
    by = {}
    for cpu, (n, sock, core) in host["cpus"].items():
        if n == node:
            by.setdefault((sock, core), []).append(cpu)
    return sorted((tuple(sorted(v)) for v in by.values()), key=lambda t: t[0])


def host_pinning(roles, host):
    """
    The host CPU for each vCPU, from the host's own topology:
    {"map": {vcpu: cpu}, "emulator": [...], "others": [...], "node",
    "notes"}.

    The .166's rule, generalised: the main thread's vCPU on the first
    physical core of the NIC's node, the other control-plane vCPUs together
    on that core's HT sibling (the main thread is mostly idle, so its
    sibling is the quietest place on the node - on 15, beside worker 0, the
    control plane cut that worker from 3.0M to 2.1M loops/s), each worker
    on a physical core of its own, QEMU's other threads on the siblings
    left, and every other guest on the other node(s).
    """
    notes = []
    nodes = sorted({n for n, _, _ in host["cpus"].values()})
    node = host.get("nic_node")
    if node not in nodes:
        node = max(nodes, key=lambda n: len(host_cores(host, n)))
        notes.append(f"the NIC's NUMA node was not given: node {node}, the "
                     f"one with the most cores, is assumed - give "
                     f"`nic-node <n>`")
    cores = list(host_cores(host, node))
    workers = sorted((r[1], v) for v, r in roles.items() if r[0] == "worker")
    mains = [v for v, r in roles.items() if r[0] == "main"]
    cps = [v for v, r in roles.items() if r[0] == "cp"]
    pin, spare = {}, []
    if not cores:
        return {"map": {}, "emulator": [], "others": [], "node": node,
                "notes": notes + [f"node {node} has no CPU"]}
    first = cores.pop(0)
    for v in mains:
        pin[v] = first[0]
    cp_at = first[1] if len(first) > 1 else None
    if cp_at is None:
        if cores:
            cp_at = cores.pop(0)[0]
            notes.append("the host shows no HT siblings: the control plane "
                         "takes a physical core of its own")
        else:
            cp_at = first[0]
    for v in cps:
        pin[v] = cp_at
    for _, v in workers:
        if cores:
            core = cores.pop(0)
            pin[v] = core[0]
            spare += list(core[1:])
        else:
            notes.append(f"node {node} has no physical core left for vCPU "
                         f"{v}'s worker: it shares one, and runs slower")
            spare_core = spare.pop(0) if spare else cp_at
            pin[v] = spare_core
    for core in cores:
        spare += list(core)
    spare += list(first[2:])
    emulator = sorted(set(spare))[-EMULATOR_THREADS:] or [cp_at]
    used = set(pin.values()) | set(emulator)
    others = sorted(c for c, (n, _, _) in host["cpus"].items()
                    if n != node)
    if not others:
        others = sorted(c for c in host["cpus"] if c not in used)
        notes.append("the host has one NUMA node: the other guests get "
                     "what this one leaves")
    return {"map": dict(sorted(pin.items())), "emulator": emulator,
            "others": others, "node": node, "notes": notes}


def _placeholders(roles):
    """The HOST_* names a hookscript is written with when the host's
    topology was not given: one per role, and per worker."""
    out = {}
    for v, r in roles.items():
        out[v] = {"main": "HOST_MAIN", "cp": "HOST_CP"}.get(
            r[0], f"HOST_W{r[1]}" if r[0] == "worker" else "HOST_CP")
    return out


def _role_name(r):
    """A role, said."""
    return {"main": "VPP main thread", "cp": "control plane"}.get(
        r[0], f"VPP worker {r[1]}" if r[0] == "worker" else "")


def proxmox_hookscript(roles, pin=None):
    """
    The Proxmox hookscript, as text: /var/lib/vz/snippets/prisma-pin.sh on
    the host. With `pin` (host_pinning()) the host CPUs are filled in; with
    none they are HOST_* names, and the script refuses to run until they
    are replaced.
    """
    fill = (pin or {}).get("map") or {}
    ph = _placeholders(roles)
    val = {v: str(fill[v]) if v in fill else ph[v] for v in roles}
    emu = ",".join(str(c) for c in (pin or {}).get("emulator") or []) \
        or "HOST_EMU"
    groups = {}
    for v, c in val.items():
        groups.setdefault(c, []).append(v)
    out = ["#!/bin/sh",
           "# Proxmox hookscript for the Prisma Routing VM, from `show system",
           "# tuning advice` (#596): each vCPU thread pinned to one host CPU.",
           "# This box's layout, and where each role goes:"]
    for v, r in roles.items():
        out.append(f"#   vCPU {v:<3} {_role_name(r):<18} -> {val[v]}")
    out.append(f"#   QEMU's other threads, vhost -> {emu}")
    if not fill:
        out += ["# FILL IN every HOST_* with a host CPU number: the main",
                "# thread a physical core on the NIC's NUMA node, the",
                "# control plane that core's HT sibling, each worker a",
                "# physical core of its own on the same node, HOST_EMU the",
                "# siblings left. Or paste the host's `lscpu -e=CPU,NODE,",
                "# SOCKET,CORE` into `show system tuning advice host-topology`",
                "# and the box fills them in."]
    out += ["# On the host: save as /var/lib/vz/snippets/prisma-pin.sh,",
            "# chmod 755, and qm set <vmid> --hookscript",
            "# local:snippets/prisma-pin.sh",
            "vmid=${1:?usage: prisma-pin.sh <vmid> [phase]}; "
            "phase=${2:-post-start}",
            "[ \"$phase\" = post-start ] || exit 0"]
    out += [f"EMU={emu}",
            "map() {",
            "  case \"$1\" in"]
    for c, vs in groups.items():
        out.append(f"    {'|'.join(str(v) for v in vs)}) echo {c} ;;")
    out += ["  esac",
            "}"]
    if not fill:
        # Refused while a HOST_* is left: taskset given a word pins nothing
        # and says so per thread, in a log nobody reads at VM start.
        out += [f"for v in {' '.join(str(v) for v in roles)}; do",
                "  case \"$(map $v),$EMU\" in *HOST_*)",
                "    echo \"prisma-pin: fill in the HOST_* CPUs\" >&2; "
                "exit 1 ;;",
                "  esac",
                "done"]
    out += ["sleep 2",
            "pid=$(cat /var/run/qemu-server/$vmid.pid) || exit 0",
            "for t in /proc/$pid/task/*; do",
            "  tid=${t##*/}; name=$(cat $t/comm)",
            "  case \"$name\" in",
            "    \"CPU \"*\"/KVM\") n=${name#CPU }; n=${n%%/KVM}; "
            "c=$(map $n)",
            "      [ -n \"$c\" ] && taskset -pc \"$c\" \"$tid\" >/dev/null ;;",
            "    *) taskset -pc \"$EMU\" \"$tid\" >/dev/null ;;",
            "  esac",
            "done",
            "for v in $(pgrep -f \"^vhost-$pid\"); do",
            "  taskset -pc \"$EMU\" \"$v\" >/dev/null",
            "done",
            "exit 0"]
    return "\n".join(out) + "\n"


def fold_assign(name, cpus):
    """`name=<cpus>` as shell lines no wider than WIDTH: a list too long for
    one is built up a piece at a time (`name=$name,...`)."""
    out, cur = [], ""
    for c in cpu_spec(cpus).split(","):
        head = f"{name}=${name}," if out else f"{name}="
        if cur and len(head) + len(cur) + len(c) + 1 > WIDTH:
            out.append(head + cur)
            cur = c
        else:
            cur = f"{cur},{c}" if cur else c
    out.append((f"{name}=${name}," if out else f"{name}=") + cur)
    return out


def qm_lines(roles, pin, f):
    """The `qm set` lines: the hookscript, the VM's affinity, its NUMA node
    and its memory on hugepages there, and the other guests' affinity."""
    n = len(roles)
    fill = (pin or {}).get("map") or {}
    out = ["qm set <vmid> --hookscript local:snippets/prisma-pin.sh"]
    if fill:
        out += fold_assign("CPUS", list(fill.values()) + list(pin["emulator"]))
        out.append("qm set <vmid> --affinity $CPUS")
        node = str(pin["node"])
    else:
        out.append("qm set <vmid> --affinity <HOST-CPUS>")
        node = "<NIC-NODE>"
    kb = ((f.get("memory") or {}).get("total_kb") or 0)
    out += [f"qm set <vmid> --numa 1 \\",
            f"  --numa0 cpus=0-{n - 1},hostnodes={node},"
            f"memory=<VM-MEMORY-MB>,policy=bind",
            "qm set <vmid> --hugepages 1024 --keephugepages 1",
            "# <VM-MEMORY-MB>: the VM's memory as the host has it configured",
            f"# (this box sees {kb / 1048576:.1f} GB)",
            "# and every other guest on this host, off these CPUs:"]
    if fill and pin["others"]:
        out += fold_assign("OTHERS", pin["others"])
        out.append("# qm set <other vmid> --affinity $OTHERS")
    else:
        out.append("# qm set <other vmid> --affinity <the other node's CPUs>")
    return out


def libvirt_cputune(roles, pin, f):
    """The same pinning as a libvirt domain's <cputune>, with the memory on
    the NIC's node and on hugepages."""
    fill = (pin or {}).get("map") or {}
    ph = _placeholders(roles)
    out = [f"<vcpu placement='static'>{len(roles)}</vcpu>", "<cputune>"]
    for v in roles:
        out.append(f"  <vcpupin vcpu='{v}' "
                   f"cpuset='{fill[v] if v in fill else ph[v]}'/>")
    emu = ",".join(str(c) for c in (pin or {}).get("emulator") or []) \
        or "HOST_EMU"
    node = str(pin["node"]) if fill else "NIC_NODE"
    out += [f"  <emulatorpin cpuset='{emu}'/>", "</cputune>",
            "<numatune>", f"  <memory mode='strict' nodeset='{node}'/>",
            "</numatune>", "<memoryBacking>", "  <hugepages/>",
            "</memoryBacking>"]
    return "\n".join(out) + "\n"


PRINCIPLE = (
    "Pin each vCPU to one host thread on the NIC's NUMA node: one physical "
    "core per worker, the main thread on a core of its own, the control "
    "plane on that core's HT sibling (the main thread is mostly idle, so "
    "its sibling is the quietest place on the node), and QEMU's other "
    "threads on the siblings left. Back the VM's memory with hugepages on "
    "that node, keep the other guests off those cores (on the other node "
    "when there is one), and pass the NIC through. A vCPU left to float "
    "shares its host thread with whatever else is runnable, and a "
    "poll-mode worker never yields it.")


# ----------------------------------------------------------------- sizing

def recommended_split(vcpus):
    """
    (control-plane cores, main thread) this advice recommends for a VM of
    `vcpus`: the main thread its own from 6 (on 4 it would cost one worker
    in three), two control-plane vCPUs from 8 (a full BGP table converging
    is the reason, tune.env says), three from 24.
    """
    cp = 1 if vcpus < 8 else 2 if vcpus < 24 else 3
    return cp, "dedicated" if vcpus >= 6 else "shared"


def sizing_table(tune, this_vcpus=None):
    """
    [{"vcpus", "control_plane", "main", "workers", "host_threads",
    "this_box"}]: for each size, the split recommended_split() gives and the
    workers the tuner's own plan() makes of it on a VM of that many vCPUs
    (Topology.given(): one node, no SMT, as a guest is usually shown).
    host_threads is what the host has to set aside, pinned: a core per
    worker and one for the main thread, whose sibling carries the control
    plane. [] when the tuner could not be loaded.
    """
    if tune is None:
        return []
    sizes = sorted(set(SIZING_VCPUS) | ({this_vcpus} if this_vcpus else set()))
    rows = []
    for n in sizes:
        if n < 2:
            continue
        cp, main = recommended_split(n)
        p = tune.plan(tune.Topology.given(n), None, 200000,
                      control_plane_cores=cp, main_thread=main)
        dedicated = bool(p["main_cpus"])
        w = len(p["workers"])
        rows.append({"vcpus": n, "control_plane": len(p["os_cpus"]),
                     "main": "dedicated" if dedicated else "shared",
                     "workers": w, "host_threads": 2 * (w + 1),
                     "this_box": n == this_vcpus})
    return rows


# ---------------------------------------------------------------- advise

def advise(f, tune=None, host_text=""):
    """Everything the screen and the API carry, decided from the facts `f`:
    findings, recommendations, snippets, sizing."""
    found, checked = findings(f)
    host = parse_host_topology(host_text) if host_text else None
    roles = guest_roles(f)
    pin = host_pinning(roles, host) if host and host["cpus"] else None
    virt = virtualized(f)
    snippets = None
    # Only a layout the dataplane reported: with no main thread read there
    # is nothing to say which vCPU does what, and a script pinning every
    # vCPU as the control plane's would be a guess written as a measurement.
    if virt and roles and (f.get("layout") or {}).get("main") is not None:
        snippets = {"principle": PRINCIPLE,
                    "proxmox_hookscript": proxmox_hookscript(roles, pin),
                    "proxmox_qm": "\n".join(qm_lines(roles, pin, f)) + "\n",
                    "libvirt_cputune": libvirt_cputune(roles, pin, f),
                    "host_topology_used": pin is not None,
                    "notes": (pin or {}).get("notes", [])
                    + ([f"host-topology: {len(host['errors'])} line(s) not "
                        f"read, the first: {host['errors'][0]}"]
                       if host and host["errors"] else [])}
    hyp = []
    keys = {x["key"] for x in found}
    if virt:
        for k, t in (("steal", "pin the vCPUs: the hookscript below"),
                     ("uneven-workers", "pin the vCPUs, one physical core "
                                        "per worker: the hookscript below")):
            if k in keys:
                hyp.append({"title": t, "lines": [], "restart": RESTART_NONE,
                            "evidence": k})
        for x in found:
            if x["key"].startswith("queues:") and "irtio" in x["measured"]:
                hyp.append({"title": "give the virtio card one queue per "
                                     "worker on the host: qm set <vmid> "
                                     "--net<n> <its line>,queues=<workers>",
                            "lines": [], "restart": RESTART_NONE,
                            "evidence": x["key"]})
            if x["key"].startswith("numa:"):
                hyp.append({"title": "pin the vCPUs on the card's NUMA node",
                            "lines": [], "restart": RESTART_NONE,
                            "evidence": x["key"]})
    return {"findings": found, "checked": checked,
            "box": box_recommendations(f, found), "hypervisor": hyp,
            "snippets": snippets,
            "sizing": sizing_table(tune, len(roles) or None)}


# ----------------------------------------------------------------- render

def _wrap(text, indent="  ", first=None):
    """`text` wrapped to WIDTH, `first` (default indent) on the first row."""
    return textwrap.wrap(text, WIDTH,
                         initial_indent=indent if first is None else first,
                         subsequent_indent=indent, break_long_words=True,
                         break_on_hyphens=False)


def render_facts(f):
    """The rows of `what this box has` and `measured over`."""
    out = ["what this box has"]
    t = f.get("topology") or {}
    out += _wrap(f"{len(t.get('online') or [])} online: "
                 f"{t.get('sockets', 0)} socket(s), {t.get('cores', 0)} "
                 f"physical core(s), {t.get('nodes', 0)} NUMA node(s), HT "
                 f"siblings {'visible' if t.get('smt') else 'none visible'}",
                 indent=" " * 16, first="  CPUs          ")
    v = f.get("virt") or {}
    out.append(f"  virtualized   "
               + ({"": "cannot tell", "none": "no, bare metal"}.get(
                   v.get("kind", ""), v.get("kind", "")))
               + (f" ({v['source']})" if v.get("source") else ""))
    m = f.get("memory") or {}
    kb = m.get("hugepage_kb") or 0
    hp = (f"{m.get('hugepages_total', 0)} x "
          + (f"{kb // 1048576}G" if kb >= 1048576 else f"{kb // 1024}M")
          if kb else "none")
    out.append(f"  memory        {(m.get('total_kb') or 0) / 1048576:.1f} "
               f"GB; hugepages {hp}")
    lay = f.get("layout") or {}
    main = lay.get("main")
    out += _wrap(f"control plane "
                 f"{cpu_spec(lay.get('control_plane') or []) or '-'}, main "
                 f"{main if main is not None else '-'} "
                 f"({'dedicated' if lay.get('main_dedicated') else 'shared'}),"
                 f" workers {cpu_spec(lay.get('workers') or []) or 'none'}",
                 indent=" " * 16, first="  layout        ")
    mss = f.get("tcp_mss") or {}
    out += _wrap(f"{f.get('mtu') or '-'} by default; tcp-mss "
                 + (", ".join(f"{k} {v}" for k, v in sorted(mss.items()))
                    or "not set"), indent=" " * 16, first="  MTU           ")
    cards = f.get("cards")
    if cards is None:
        out.append("  cards         the dataplane did not answer")
    for c in cards or []:
        on = set(c["placement"].values())
        wk = cpu_spec([w for w in on if w != "main"])
        said = ", ".join((["main"] if "main" in on else [])
                         + ([f"worker(s) {wk}"] if wk else []))
        out += _wrap(f"{c['name']}: {c['driver'] or 'driver unknown'}, "
                     f"{c['rx_queues']} rx queue(s) on "
                     + (said or "no thread")
                     + f", NUMA {c['numa'] if c['numa'] is not None else 'not declared'}"
                     + (f", RSS {c['rss']}" if c["rss"] else ""),
                     indent="                  ",
                     first="  card          ")
    w = f.get("window") or {}
    out.append("")
    out.append(f"measured over {w.get('seconds', 0):g} s "
               f"(two readings, no counter cleared)")
    rates = w.get("threads") or {}
    cpus = w.get("cpus") or {}
    if not rates:
        out.append("  the dataplane did not answer `show runtime`")
    else:
        out.append("  thread       cpu    loops/s  vectors/call   steal")
        wcpus = lay.get("workers") or []
        for k in ["main"] + sorted(x for x in rates if x != "main"):
            if k not in rates:
                continue
            cpu = main if k == "main" else (wcpus[k] if k < len(wcpus)
                                            else None)
            st = cpus.get(cpu, {}).get("steal") if cpu is not None else None
            out.append(f"  {('main' if k == 'main' else f'worker {k}'):<11}"
                       f"{'-' if cpu is None else cpu:>4}"
                       f"{_m(rates[k]['loops_per_second']):>11}"
                       f"{rates[k]['vectors_per_call']:>14.2f}"
                       f"{'-' if st is None else f'{st:.1f}%':>8}")
    cp = [c for c in lay.get("control_plane") or [] if c != main
          or not lay.get("main_dedicated")]
    if cpus:
        out.append(f"  control plane {_avg(cp, cpus, 'busy') or 0:.0f}% "
                   f"busy, {_avg(cp, cpus, 'steal') or 0:.1f}% steal")
    return out


def render(f, a):
    """The whole screen, as rows no wider than WIDTH."""
    out = render_facts(f)
    out.append("")
    if a["findings"]:
        out.append("findings")
        for x in a["findings"]:
            out += _wrap(f"[{x['severity']}] {x['title']}: {x['measured']}",
                         indent="         ", first="  ")
            out += _wrap(f"threshold: {x['threshold']}", indent="         ",
                         first="         ")
            out += _wrap(f"why: {x['why']}", indent="         ",
                         first="         ")
    else:
        out.append("findings: none")
    out.append("checked and fine:")
    for c in a["checked"]:
        out += _wrap(c, indent="    ", first="  ")
    out.append("")
    out.append("on the box")
    if not a["box"]:
        out.append("  nothing to change: no finding asks for a line here")
    for r in a["box"]:
        out += _wrap(f"{r['title']} (because: {r['evidence']}; takes "
                     + {"none": "nothing", "dataplane": "a dataplane restart",
                        "reboot": "a reboot"}[r["restart"]] + ")",
                     indent="    ", first="  - ")
        out += [f"      {line}" for line in r["lines"]]
    sn = a.get("snippets")
    if virtualized(f) and not sn:
        out.append("")
        out += _wrap(f"on the hypervisor ({(f.get('virt') or {}).get('kind')})"
                     f": no pinning written - the dataplane's threads could "
                     f"not be read, so nothing says which vCPU does what.",
                     indent="  ", first="")
    if sn:
        out.append("")
        out.append(f"on the hypervisor ({(f.get('virt') or {}).get('kind')})")
        for r in a["hypervisor"]:
            out += _wrap(f"{r['title']} (because: {r['evidence']})",
                         indent="    ", first="  - ")
        if not a["hypervisor"]:
            out += _wrap("no steal or unevenness was measured; the pinning "
                         "below is what this layout wants, should it be "
                         "needed.", indent="  ")
        out += _wrap(sn["principle"], indent="  ")
        for n in sn["notes"]:
            out += _wrap(n, indent="    ", first="  note: ")
        for title, key in (
                ("Proxmox hookscript, /var/lib/vz/snippets/prisma-pin.sh",
                 "proxmox_hookscript"),
                ("Proxmox, on the host", "proxmox_qm"),
                ("libvirt, in the domain's XML", "libvirt_cputune")):
            out.append(f"  --- {title} " + "-" * max(
                0, WIDTH - 7 - len(title)))
            out += sn[key].rstrip("\n").splitlines()
            out.append("  " + "-" * (WIDTH - 2))
    if a.get("sizing"):
        out.append("")
        out.append("sizing: what the tuner makes of a VM of each size")
        out.append("  vCPUs  control plane  main thread  workers  "
                   "host threads to pin")
        for r in a["sizing"]:
            out.append(f"  {r['vcpus']:>5}  {r['control_plane']:>13}  "
                       f"{r['main']:>11}  {r['workers']:>7}  "
                       f"{r['host_threads']:>19}"
                       + ("  <- this box" if r["this_box"] else ""))
        out += _wrap("host threads: a physical core (two threads) per "
                     "worker and one for the main thread, whose sibling "
                     "carries the control plane.", indent="  ")
    return out


def advice_fields(f, a):
    """The API's TuningAdvice fields (#596), from the same facts and advice
    the screen draws; the text is added by the caller."""
    t = f.get("topology") or {}
    m = f.get("memory") or {}
    lay = f.get("layout") or {}
    w = f.get("window") or {}
    cpus = w.get("cpus") or {}
    wcpus = lay.get("workers") or []
    threads = []
    for k, r in sorted((w.get("threads") or {}).items(),
                       key=lambda x: -1 if x[0] == "main" else x[0]):
        cpu = lay.get("main") if k == "main" else (
            wcpus[k] if k < len(wcpus) else None)
        threads.append({"worker": -1 if k == "main" else int(k),
                        "cpu": -1 if cpu is None else int(cpu),
                        "loops_per_second": float(r["loops_per_second"]),
                        "vectors_per_call": float(r["vectors_per_call"]),
                        "steal_percent": float(
                            cpus.get(cpu, {}).get("steal", 0.0))})
    cp = lay.get("control_plane") or []
    dp = dataplane_cpus(f)
    facts = {
        "cpus_online": len(t.get("online") or []),
        "sockets": int(t.get("sockets") or 0),
        "physical_cores": int(t.get("cores") or 0),
        "numa_nodes": int(t.get("nodes") or 0),
        "smt_visible": bool(t.get("smt")),
        "virtualization": str((f.get("virt") or {}).get("kind") or ""),
        "virtualization_source": str((f.get("virt") or {}).get("source")
                                     or ""),
        "memory_total_bytes": int(m.get("total_kb") or 0) * 1024,
        "hugepage_size_bytes": int(m.get("hugepage_kb") or 0) * 1024,
        "hugepages_reserved": int(m.get("hugepages_total") or 0),
        "hugepages_readback": str(m.get("effect_said") or ""),
        "control_plane_cpus": cpu_spec(cp),
        "main_cpu": -1 if lay.get("main") is None else int(lay["main"]),
        "main_dedicated": bool(lay.get("main_dedicated")),
        "worker_cpus": cpu_spec(wcpus),
        "default_mtu": int(f.get("mtu") or 0),
        "tcp_mss": ", ".join(f"{k} {v}" for k, v in
                             sorted((f.get("tcp_mss") or {}).items())),
        "window_ms": int(float(w.get("elapsed") or w.get("seconds") or 0)
                         * 1000),
        "steal_dataplane_percent": float(_avg(dp, cpus, "steal") or 0.0),
        "steal_control_plane_percent": float(
            _avg([c for c in cp if c not in dp], cpus, "steal") or 0.0),
        "cards": [{"name": c["name"], "driver": c["driver"], "pci": c["pci"],
                   "numa_node": -1 if c["numa"] is None else int(c["numa"]),
                   "rx_queues": int(c["rx_queues"] or 0),
                   "queue_threads": [-1 if c["placement"][q] == "main"
                                     else int(c["placement"][q])
                                     for q in sorted(c["placement"])]}
                  for c in f.get("cards") or []],
        "threads": threads,
    }
    sn = a.get("snippets")
    return {
        "facts": facts,
        "findings": [dict(x) for x in a["findings"]],
        "checked": list(a["checked"]),
        "recommendations": [dict(r, side="box") for r in a["box"]]
        + [dict(r, side="hypervisor") for r in a["hypervisor"]],
        # None on bare metal, or with no layout to pin: the message unset.
        "snippets": {"principle": sn["principle"],
                     "proxmox_hookscript": sn["proxmox_hookscript"],
                     "proxmox_qm": sn["proxmox_qm"],
                     "libvirt_cputune": sn["libvirt_cputune"],
                     "host_topology_used": bool(sn["host_topology_used"]),
                     "notes": list(sn["notes"])} if sn else None,
        "sizing": [{"vcpus": r["vcpus"], "control_plane": r["control_plane"],
                    "main_thread": r["main"], "workers": r["workers"],
                    "host_threads": r["host_threads"],
                    "this_box": r["this_box"]} for r in a["sizing"]],
    }
