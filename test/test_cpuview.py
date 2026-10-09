#!/usr/bin/env python3
"""
The CPUs one by one (#598): GetSystem's CpuTopology and CpuUsage, and `show
system cpu cores`, against a box described as /sys and /proc describe one.

THE BOX. Two sockets of two cores with HT - CPUs 0-3 the first thread of
each core, 4-7 the second, as Linux numbers them - and a NUMA node a socket:
sv01's shape (#596's host) cut down to eight CPUs. The tuner's layout on it:
the control plane on 0 and its sibling 4, the main thread on 1 with its
sibling 5 isolated and idle, workers on 2 and 3 with their siblings 6 and 7
isolated and idle. So every plane is here, "none" included: the three
isolated siblings no VPP thread runs on.

The topology is read through the tuner's own Topology, from files, so what
is held is the reader `show system tuning advice` uses (tuneadvice.
cpu_topology), not a second one. The window's clock and sleep are injected:
a one-second window takes no time here, and nothing depends on the hour.
"""
import contextlib
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(REPO, "src", "prismash"))
import cpuview as V  # noqa: E402
import tuneadvice as T  # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if cond:
        print(f"  [ ok ] {msg}")
    else:
        print(f"  FAIL: {msg}")
        fail += 1


_root = tempfile.mkdtemp(prefix="cpuview-")
os.environ["PRISMA_CONFIG"] = os.path.join(_root, "prisma-routing.conf")
os.environ["PRISMA_CONFIRM"] = os.path.join(_root, "confirm-pending")


def put(rel, text):
    """A file of the synthetic box."""
    p = os.path.join(_root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        fh.write(text)
    return p


for c in range(8):
    core = c % 4
    put(f"sys/devices/system/cpu/cpu{c}/topology/physical_package_id",
        str(core // 2))
    put(f"sys/devices/system/cpu/cpu{c}/topology/core_id", str(core % 2))
    put(f"sys/devices/system/cpu/cpu{c}/topology/thread_siblings_list",
        f"{core},{core + 4}")
put("sys/devices/system/node/node0/cpulist", "0-1,4-5")
put("sys/devices/system/node/node1/cpulist", "2-3,6-7")

CPUINFO = ("processor\t: 0\nvendor_id\t: GenuineIntel\n"
           "model name\t: Intel(R) Xeon(R) CPU E5-2620 v3 @ 2.40GHz\n"
           "flags\t\t: fpu hypervisor\n\nprocessor\t: 1\n"
           "model name\t: Intel(R) Xeon(R) CPU E5-2620 v3 @ 2.40GHz\n")

THREADS = """\
ID     Name                Type        LWP     Sched Policy (Priority)  lcore  Core   Socket State
0      vpp_main                        1092    other (0)                1      1      0
1      vpp_wk_0            workers     1101    other (0)                2      0      1
2      vpp_wk_1            workers     1102    other (0)                3      1      1
"""


def runtime(at, active=0.0):
    """`show runtime` `at` seconds into a steady run: main 245k loops/s,
    worker 0 polling 3M loops/s with `active` of its node clocks in its
    graph, worker 1 at 98 loops/s - asleep, waiting for interrupts."""
    rows = ["Thread 0 vpp_main (lcore 1)",
            f"Time {at:.1f}, 10 sec internal node vector rate 0.00 loops/sec "
            f"245000.00",
            "             Name                 State         Calls          "
            "Vectors        Suspends         Clocks       Vectors/Call  ",
            f"unix-epoll-input                 polling        "
            f"{int(245000 * at):>12}               0               0"
            f"          1.00e3            0.00",
            "---------------"]
    for w, loops in ((0, 3e6), (1, 98.0)):
        calls = int(loops * at)
        # ip4-input's clocks, at 100 a vector as dpdk-input's are a call:
        # `active` of the two together.
        vec = int(calls * active / (1 - active)) if w == 0 else 0
        rows += [f"Thread {w + 1} vpp_wk_{w} (lcore {w + 2})",
                 f"Time {at:.1f}, 10 sec internal node vector rate 0.00 "
                 f"loops/sec {loops:.2f}",
                 "             Name                 State         Calls      "
                 "    Vectors        Suspends         Clocks       Vectors/"
                 "Call  ",
                 f"dpdk-input                       polling        "
                 f"{calls:>12}               0               0"
                 f"          1.00e2            0.00",
                 f"ip4-input                        active         "
                 f"{vec:>12}  {vec:>14}"
                 f"               0          1.00e2            1.00",
                 "---------------"]
    return "\n".join(rows) + "\n"


def proc_stat(t):
    """/proc/stat `t` seconds in, 100 jiffies a second a CPU: the control
    plane's 0 and 4 12% busy (2 of it softirq), 1-3 polling (100% busy, 3
    of it stolen on cpu 2), 5-7 idle."""
    rows = ["cpu  0 0 0 0 0 0 0 0 0 0"]
    for c in range(8):
        if c in (0, 4):
            v = (8 * t, 0, 2 * t, 88 * t, 0, 0, 2 * t, 0)
        elif c in (1, 2, 3):
            st = 3 * t if c == 2 else 0
            v = (100 * t - st, 0, 0, 0, 0, 0, 0, st)
        else:
            v = (0, 0, 0, 100 * t, 0, 0, 0, 0)
        rows.append(f"cpu{c} " + " ".join(str(x) for x in v) + " 0 0")
    return "\n".join(rows) + "\n"


# ------------------------------------------------------- the pure half
print("=== cpuview: the model, the topology, the usage ===")
check(V.cpu_model(CPUINFO) == "Intel(R) Xeon(R) CPU E5-2620 v3 @ 2.40GHz"
      and V.cpu_model("processor\t: 0\nCPU part\t: 0xd08\n") == ""
      and V.cpu_model("") == "",
      "cpu_model: /proc/cpuinfo's first model name; none on a kernel that "
      "prints none")
tune = T.load_tuner()
topo = T.cpu_topology(tune, _root, list(range(8)))
layout = {"control_plane": [0, 4], "main": 1, "main_dedicated": True,
          "workers": [2, 3], "isolated": [1, 2, 3, 5, 6, 7]}
planes = T.cpu_planes(topo, layout)
check(planes == {0: "control-plane", 1: "main", 2: "worker", 3: "worker",
                 4: "control-plane", 5: "none", 6: "none", 7: "none"},
      f"cpu_planes: the isolated siblings no thread runs on are 'none' "
      f"(#598): {planes}")
check(T.cpu_planes(topo, {"control_plane": [0, 1, 2, 3, 4, 5, 6],
                          "main": 1, "workers": [2]})[7] == "none",
      "  and so is a CPU outside the control plane's fence")
F = V.topology_fields(topo, planes, V.cpu_model(CPUINFO), layout["workers"])
check((F["sockets"], F["physical_cores"], F["threads_per_core"],
       F["numa_nodes"], F["logical_cpus"]) == (2, 4, 2, 2, 8),
      f"topology_fields: 2 sockets, 4 cores, 2 threads a core, 2 nodes, 8 "
      f"CPUs: {F['sockets'], F['physical_cores'], F['threads_per_core']}")
check(F["socket_cores"] == [{"socket": 0, "physical_cores": 2,
                             "logical_cpus": 4},
                            {"socket": 1, "physical_cores": 2,
                             "logical_cpus": 4}],
      f"  each socket's cores and CPUs: {F['socket_cores']}")
check(F["cpus"][2] == {"cpu": 2, "socket": 1, "core": 0, "numa_node": 1,
                       "siblings": [6], "plane": "worker", "worker": 0}
      and F["cpus"][5] == {"cpu": 5, "socket": 0, "core": 1, "numa_node": 0,
                           "siblings": [1], "plane": "none", "worker": -1},
      f"  each CPU: socket, core, node, its sibling but itself, plane, "
      f"worker: {F['cpus'][2]}")
half = {"online": [0, 1, 2], "sockets": 1, "cores": 2, "nodes": 1,
        "cpus": {0: {"socket": 0, "core": 0, "node": 0, "siblings": [0, 2]},
                 2: {"socket": 0, "core": 0, "node": 0, "siblings": [0, 2]},
                 1: {"socket": 0, "core": 1, "node": 0, "siblings": [1]}}}
check(V.topology_fields(half, {})["threads_per_core"] == 2,
      "  threads a core is the largest sibling group: a sibling offline does "
      "not make it 1")
bare = V.topology_fields(T.cpu_topology(None, "/nonexistent", [0, 1]),
                         {0: "control-plane"})
check(bare["cpus"][1] == {"cpu": 1, "socket": -1, "core": -1,
                          "numa_node": -1, "siblings": [], "plane": "none",
                          "worker": -1} and bare["threads_per_core"] == 0,
      "  /sys with no topology: -1 where it does not say, never a guess")
W = T.cpu_window(T.parse_proc_stat(proc_stat(10)),
                 T.parse_proc_stat(proc_stat(11)))
U = V.usage_rows(W, 1000, {2: {"polling": True, "busy_ratio": 0.4},
                           1: {"polling": True, "busy_ratio": 0.0}})
check(U[0] == {"cpu": 0, "busy_percent": 12.0, "steal_percent": 0.0,
               "polling": False, "busy_ratio": 0.0, "window_ms": 1000}
      and U[2] == {"cpu": 2, "busy_percent": 97.0, "steal_percent": 3.0,
                   "polling": True, "busy_ratio": 0.4, "window_ms": 1000},
      f"usage_rows: busy (softirq in it) and steal apart; a polling worker "
      f"its VPP busy ratio: {U[2]}")
check(V._plane(F["cpus"][3]) == "worker 1" and V._plane(F["cpus"][1])
      == "main thread" and V._plane(F["cpus"][0]) == "control plane",
      "_plane: a worker by its number, the others in words")
check(V._dash(-1) == "-" and V._dash(0) == "0", "_dash: -1 is '-'")
check(len(V._wrap("x " * 50)) == 2
      and " ".join(V._wrap("x " * 50)) == ("x " * 50).strip()
      and all(len(x) <= 79 for x in V._wrap("y" * 30 + " z" * 40)),
      "_wrap: 79 wide")
SCREEN = V.render({"topology": F, "usage": U, "said": "usage measured over "
                                                      "1.0 s"})
print("\n".join("    | " + x for x in SCREEN))
check(SCREEN[0] == "Intel(R) Xeon(R) CPU E5-2620 v3 @ 2.40GHz"
      and "2 socket(s), 4 physical core(s) (2 a socket), 2 thread(s) a core, "
      "2 NUMA node(s)" in " ".join(SCREEN),
      "render: the model, then the topology in a sentence")
row = {x.split()[0]: x for x in SCREEN if x[:5].strip().isdigit()}
check(row["2"].split() == ["2", "1", "0", "1", "6", "worker", "0", "97.0%",
                           "3.0%", "polling", "40%"],
      f"  a worker: its place, busy, steal, polling and VPP's busy: "
      f"{row['2']!r}")
check(row["1"].endswith("polling") and "main thread" in row["1"]
      and row["5"].split()[5:] == ["none", "0.0%", "0.0%"],
      "  the main thread polls with no busy ratio; an isolated sibling is "
      "none, idle")
check(all(len(x) <= 79 for x in SCREEN), "  every row fits 79 columns")
wide = dict(F, cpus=[dict(F["cpus"][0], cpu=255, socket=15, core=127,
                          numa_node=15, siblings=[64, 128, 192, 255],
                          plane="worker", worker=127)],
            model="M" * 120)
WSCR = V.render({"topology": wide, "usage": [dict(
    U[2], cpu=255, busy_percent=100.0, steal_percent=100.0, busy_ratio=1.0)],
    "said": "usage over the API's last sample: 3600 s, ended 86400 s ago"})
check(all(len(x) <= 79 for x in WSCR[1:]) and "64,128,19+" in WSCR[-1],
      f"  and at its widest values - 255, worker 127, 100%, four siblings "
      f"cut: {WSCR[-1]!r}")
NONE = V.render({"topology": F, "usage": [], "said": ""})
check([x.split()[-2:] for x in NONE if x.startswith("    0 ")]
      == [["-", "-"]], "  a CPU with no usage row reads '-', never 0")

# ------------------------------------------------------- the Shell's half
print("\n=== the Shell reads the box: show system cpu cores ===")
import prismash  # noqa: E402
import apiserver  # noqa: E402
sh = prismash.Shell()
sh.caps = {prismash.CAP_ALL}
sh.SYS_DEVICES = os.path.join(_root, "sys/devices")
sh.CPUINFO = put("proc/cpuinfo", CPUINFO)
sh.CPU_ONLINE = put("sys/devices/system/cpu/online", "0-7\n")
sh.CPU_ISOLATED = put("sys/devices/system/cpu/isolated", "1-3,5-7\n")
sh.CPU_FENCE = put("fence", "0,4\n")
prismash.PROC_STAT = put("proc/stat", proc_stat(10))
stats = iter([proc_stat(10), proc_stat(11)])
rts = iter([runtime(10.0, active=0.4), runtime(11.0, active=0.4)])
asked = []
_real_read = sh.read_text
sh.read_text = lambda p: next(stats) if p == prismash.PROC_STAT \
    else _real_read(p)


def run(_self, cmd):
    asked.append(cmd)
    if cmd == "show runtime":
        return 0, next(rts)
    return {"show threads": (0, THREADS)}.get(cmd, (0, ""))


sh.dp = type("D", (), {"TRACE": False, "run": run})()
check(sh.cpu_layout(topo) == layout,
      f"cpu_layout: show threads, the isolated CPUs and the fence: "
      f"{sh.cpu_layout(topo)}")
prismash.Shell._TOPOLOGY.clear()
cf = sh.cpu_facts()
check(cf["topology"] == F and cf["layout"] == layout,
      "cpu_facts: the topology through the tuner's reader, planes, model")
_ct = []
_orig = T.cpu_topology
T.cpu_topology = lambda *a: (_ct.append(a), _orig(*a))[1]
sh.cpu_facts()
T.cpu_topology = _orig
check(_ct == [], "  read once: a second call does not read /sys again")
slept, clock = [], iter([500.0, 501.0])
U2 = sh.cpu_usage_window(layout, 1.0, sleep=slept.append,
                         clock=lambda: next(clock))
check(slept == [1.0] and asked.count("show runtime") == 2
      and "clear runtime" not in asked,
      f"cpu_usage_window: one window, show runtime twice, nothing cleared: "
      f"{asked}")
check([(u["cpu"], u["polling"], u["busy_ratio"]) for u in U2 if u["cpu"] < 4]
      == [(0, False, 0.0), (1, False, 0.0), (2, True, 0.4), (3, False, 0.0)]
      and U2[2]["window_ms"] == 1000 and U2[2]["steal_percent"] == 3.0,
      f"  worker 0 polls, 40% busy by VPP; worker 1, asleep, does not; nor "
      f"does main beside workers: {[(u['cpu'], u['polling'], u['busy_ratio']) for u in U2]}")
stats = iter([proc_stat(10), proc_stat(11)])
rts = iter([runtime(10.0), runtime(11.0)])
clock = iter([500.0, 501.0])
U3 = sh.cpu_usage_window(dict(layout, workers=[]), 1.0, sleep=slept.append,
                         clock=lambda: next(clock))
check([u["cpu"] for u in U3 if u["polling"]] == [1],
      "  with no workers, the main thread polls the cards itself")
given = sh.cpu_cores_facts({"rows": U, "said": "given"})
check(given == {"topology": F, "usage": U, "said": "given"},
      "cpu_cores_facts: the API's usage, handed over, is not measured again")
stats = iter([proc_stat(10), proc_stat(11)])
rts = iter([runtime(10.0, active=0.4), runtime(11.0, active=0.4)])
sh.cpu_usage_window = lambda lay: U
own = sh.cpu_cores_facts()
check(own["usage"] == U and own["said"] == "usage measured over 1.0 s",
      f"  and at the prompt, a window of its own: {own['said']!r}")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.show_system_cpu_cores({"topology": F, "usage": U, "said": "x"})
check(out.getvalue().splitlines() == V.render({"topology": F, "usage": U,
                                               "said": "x"}),
      "show_system_cpu_cores: draws the reading it is handed")
out = io.StringIO()
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
    sh.feed("show system cpu cores")
check("worker 0" in out.getvalue() and "97.0%" in out.getvalue(),
      "  `show system cpu cores` at the prompt draws it")
api = apiserver.READERS["cpu_cores"](sh, {"rows": U, "said": "s"})
check(api["facts"]["topology"] == F and "97.0%" in api["text"]
      and api["text"].splitlines()[0] == F["model"],
      "the API's cpu_cores reader: fields and text from one reading")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
