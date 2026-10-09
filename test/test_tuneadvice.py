#!/usr/bin/env python3
"""
`show system tuning advice` (#596), against the case it was built from.

THE FIXTURES ARE 10.40.200.166's. Proxmox VM 102 on sv01 (2x Xeon E5-2620,
node 1 = the odd CPUs, cores 1..11 with HT siblings 13..23, the X520 on node
1), 8 vCPUs: control plane 2 (CPUs 0-1), main thread dedicated (2), workers
3-7. Measured on the box with `show runtime`, near-zero traffic:

  before   main 245k, workers 2.98M / 3.29M / 3.23M / 2.41M / 3.23M loops/s,
           ~7.7% steal (two workers shared a physical core through HT)
  after    workers 2.86M / 3.30M / 3.22M / 3.21M / 3.25M, steal 3.2% - all
           of it on the two control-plane vCPUs, which share one host thread

and the hookscript written by hand that made the difference is
test/prisma-pin-sv01-reference.hook, verbatim. So: the uneven-workers
finding fires on "before" and is quiet on "after"; so does the steal
finding; and the hookscript generated from the box's own layout and the
host's pasted topology maps every vCPU where the hand-written one does.

The readings are fed in as VPP and the kernel print them - `show runtime`
twice, `show threads`, `show hardware-interfaces`, /proc/stat twice - so
the numbers above are what the parsers make of text, not of dicts. The
window's clock and sleep are injected: a 5 s window takes no time here.
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(REPO, "src", "prismash"))
import tuneadvice as T  # noqa: E402

fail = 0
STARTED = time.monotonic()


def check(cond, msg):
    global fail
    if cond:
        print(f"  [ ok ] {msg}")
    else:
        print(f"  FAIL: {msg}")
        fail += 1


# ------------------------------------------------------------- fixtures

BEFORE = [2.98e6, 3.29e6, 3.23e6, 2.41e6, 3.23e6]
AFTER = [2.86e6, 3.30e6, 3.22e6, 3.21e6, 3.25e6]
MAIN = 245000.0
WINDOW = 5.0


def runtime_text(loops, main=MAIN, at=0.0, vectors=0):
    """`show runtime` as VPP prints it, `at` seconds into a steady run: the
    polling nodes' calls grow at each thread's loop rate."""
    rows = ["Thread 0 vpp_main (lcore 2)",
            f"Time {3600 + at:.1f}, 10 sec internal node vector rate 0.00 "
            f"loops/sec {main:.2f}",
            "  vector rates in 0.0000e0, out 0.0000e0, drop 0.0000e0, punt "
            "0.0000e0",
            "             Name                 State         Calls          "
            "Vectors        Suspends         Clocks       Vectors/Call  ",
            "bpa-expire-process               any wait                 0     "
            "          0            3601          1.23e4            0.00",
            f"unix-epoll-input                 polling        "
            f"{int(main * (3600 + at)):>12}               0               0"
            f"          1.02e3            0.00",
            "---------------"]
    for i, lp in enumerate(loops):
        calls = int(lp * (3600 + at))
        rows += [f"Thread {i + 1} vpp_wk_{i} (lcore {i + 3})",
                 f"Time {3600 + at:.1f}, 10 sec internal node vector rate "
                 f"0.00 loops/sec {lp:.2f}",
                 "  vector rates in 0.0000e0, out 0.0000e0, drop 0.0000e0, "
                 "punt 0.0000e0",
                 "             Name                 State         Calls      "
                 "    Vectors        Suspends         Clocks       Vectors/"
                 "Call  ",
                 f"dpdk-input                       polling        "
                 f"{calls:>12}  {int(vectors * calls):>14}               0"
                 f"          1.40e2            0.00",
                 "ethernet-input                   active                  "
                 "12              12               0          1.10e3        "
                 "    1.00",
                 "---------------"]
    return "\n".join(rows) + "\n"


THREADS = """\
ID     Name                Type        LWP     Sched Policy (Priority)  lcore  Core   Socket State
0      vpp_main                        1092    other (0)                2      0      0
1      vpp_wk_0            workers     1101    other (0)                3      0      0
2      vpp_wk_1            workers     1102    other (0)                4      0      0
3      vpp_wk_2            workers     1103    other (0)                5      0      0
4      vpp_wk_3            workers     1104    other (0)                6      0      0
5      vpp_wk_4            workers     1105    other (0)                7      0      0
"""

HW = """\
              Name                Idx   Link  Hardware
TenGigabitEthernet1/0/0            1     up   TenGigabitEthernet1/0/0
  Link speed: 10 Gbps
  RX Queues:
    queue thread         mode
    0     vpp_wk_0 (1)   polling
    1     vpp_wk_1 (2)   polling
    2     vpp_wk_2 (3)   polling
    3     vpp_wk_3 (4)   polling
    4     vpp_wk_4 (5)   polling
  TX Queues:
    TX Hash: [name: hash-eth-l34 priority: 50 description: Hash ethernet L34 headers]
    queue shared thread(s)
    0     no     0
  Ethernet address 90:e2:ba:00:00:01
  Intel 82599
    carrier up full duplex max-frame-size 9022
    rx: queues 5 (max 128), desc 1024 (min 32 max 4096 align 8)
    tx: queues 6 (max 64), desc 1024 (min 32 max 4096 align 8)
    pci: device 8086:10fb subsystem 8086:000c address 0000:01:00.00 numa 0
    rss active:        ipv4-tcp ipv4-udp ipv4 ipv6-tcp ipv6-udp ipv6
local0                             0    down  local0
  Link speed: unknown
  local
tap4096                            2     up   tap4096
  Link speed: unknown
  RX Queues:
    queue thread         mode
    0     vpp_wk_0 (1)   polling
  Ethernet address 02:fe:d7:00:54:c4
"""


def proc_stat(steal_by_cpu, at):
    """/proc/stat at `at` windows into the run: each CPU's jiffies grow by
    1000 a window, `steal_by_cpu[c]` per mille of them stolen."""
    rows = ["cpu  1 2 3 4 5 6 7 8 0 0"]
    for c, st in enumerate(steal_by_cpu):
        s = int(st * 10)
        busy = 1000 - s if c >= 2 else 120
        idle = 1000 - s - busy
        k = at
        rows.append(f"cpu{c} {10000 + busy * k} 0 {500} {50000 + idle * k} 0 0 "
                    f"0 {s * k} 0 0")
    return "\n".join(rows) + "\n"


def facts(loops, steal, smt=False, virt="kvm"):
    """The .166's facts as tuning_advice_facts() returns them, with the
    window read from the fixtures' text through the module's parsers."""
    rates = T.thread_rates(T.parse_runtime(runtime_text(loops)),
                           T.parse_runtime(runtime_text(loops, at=WINDOW)),
                           WINDOW)
    online = list(range(8))
    return {"topology": {"online": online, "sockets": 1, "cores": 8,
                         "nodes": 1, "smt": smt,
                         "node_of": {c: 0 for c in online},
                         "siblings": {c: [c] for c in online}},
            "virt": {"kind": virt, "source": "systemd-detect-virt"},
            "memory": {"total_kb": 16 * 1048576 - 420000,
                       "hugepage_kb": 1048576, "hugepages_total": 6,
                       "effect": "in-effect",
                       "effect_said": "6 page(s) of 1G reserved"},
            "layout": {"control_plane": [0, 1], "main": 2,
                       "main_dedicated": True, "workers": [3, 4, 5, 6, 7],
                       "isolated": [2, 3, 4, 5, 6, 7]},
            "cards": T.parse_hardware(HW), "mtu": 1500,
            "tcp_mss": {"cgnat": "auto"},
            "window": {"seconds": WINDOW, "threads": rates,
                       "cpus": T.cpu_window(
                           T.parse_proc_stat(proc_stat(steal, 1)),
                           T.parse_proc_stat(proc_stat(steal, 2)))}}


BEFORE_STEAL = [7.7] * 8
# 3.2% of the box, all of it on the two control-plane vCPUs: 12.8% each.
AFTER_STEAL = [12.8, 12.8, 0, 0, 0, 0, 0, 0]

# sv01's `lscpu -e=CPU,NODE,SOCKET,CORE`: two sockets of 6 cores and 12
# threads, node 0 the even CPUs and node 1 the odd, CPU n and n+12 one core.
SV01 = "CPU NODE SOCKET CORE\n" + "\n".join(
    f"{c:>3} {c % 2:>4} {c % 2:>6} {(c % 12) // 2 + 6 * (c % 2):>4}"
    for c in range(24)) + "\nnic-node 1\n"

# `numastat -p` of VM 102's QEMU on sv01, measured 2026-10-08: 16.1 GB on
# node 1 and none on node 0 - local, by first touch, with nothing binding it.
NUMASTAT = """
Per-node process memory usage (in MBs) for PID 4021 (kvm)
                           Node 0          Node 1           Total
                  --------------- --------------- ---------------
Huge                         0.00            0.00            0.00
Heap                         0.02            0.00            0.02
Stack                        0.01            0.00            0.01
Private                      2.10        16484.00        16486.10
----------------  --------------- --------------- ---------------
Total                        2.13        16484.00        16486.13
"""

REFERENCE = os.path.join(HERE, "prisma-pin-sv01-reference.hook")


def script_map(text, vcpus):
    """What a hookscript's map() answers for each vCPU, run by sh: the
    assignments above it and the function, then `map n` for each."""
    keep, inside = [], False
    for line in text.splitlines():
        if line.startswith("map()"):
            inside = True
        if inside:
            keep.append(line)
            if line.startswith("}") or (line.startswith("map()")
                                        and line.rstrip().endswith("}")):
                break
    prog = "\n".join(keep) + "\n" + "".join(
        f'printf "%s %s\\n" {v} "$(map {v})"\n' for v in vcpus)
    out = subprocess.run(["sh", "-c", prog], capture_output=True,
                         text=True).stdout
    return dict(line.split() for line in out.splitlines()
                if len(line.split()) == 2)


def script_emu(text):
    """The CPUs a hookscript gives QEMU's other threads."""
    import re
    m = re.search(r'^EMU=(\S+)$', text, re.M)
    if m:
        return m.group(1)
    m = re.search(r'taskset -pc (\d[\d,]*) "\$tid"', text)
    return m.group(1) if m else None


tune = T.load_tuner()

# ------------------------------------------------------------ the readers
print("=== the readings, as VPP and the kernel print them ===")
check(T.cpu_spec([0, 1, 2, 5, 7, 8]) == "0-2,5,7-8" and T.cpu_spec([]) == "",
      "cpu_spec: runs folded, none is ''")
check(T.cpu_parse("0-2,5, 7") == [0, 1, 2, 5, 7] and T.cpu_parse("x") == []
      and T.cpu_parse("2-7\n") == [2, 3, 4, 5, 6, 7],
      "cpu_parse: a list, the kernel's newline and all; what is not one "
      "left out")
thr = T.parse_threads(THREADS)
check(thr == {"main": 2, "workers": {0: 3, 1: 4, 2: 5, 3: 6, 4: 7}},
      f"parse_threads: main on 2, workers 0-4 on 3-7: {thr}")
rt = T.parse_runtime(runtime_text(BEFORE))
check(sorted(rt, key=str) == [0, 1, 2, 3, 4, "main"]
      and rt[3]["calls"] == int(2.41e6 * 3600) and rt[3]["polling"]
      and rt["main"]["loops_hdr"] == MAIN,
      "parse_runtime: each thread's polling calls and the header's rate")
check(rt[0]["calls"] != 12,
      "  and an active node's calls (ethernet-input's 12) are not the loop")
one = T.parse_runtime("Time 1.0, 10 sec internal node vector rate 0.00 "
                      "loops/sec 99.00\n")
check(one == {"main": {"loops_hdr": 99.0, "calls": 0, "vectors": 0,
                       "polling": False}},
      "  a box with no workers prints no Thread line: its block is main's")
rates = T.thread_rates(T.parse_runtime(runtime_text(BEFORE)),
                       T.parse_runtime(runtime_text(BEFORE, at=WINDOW)),
                       WINDOW)
check([round(rates[i]["loops_per_second"] / 1e4) for i in range(5)]
      == [298, 329, 323, 241, 323]
      and round(rates["main"]["loops_per_second"]) == 245000,
      f"thread_rates: the loop rate is the calls' growth over the window, "
      f"no counter cleared: {[rates[i]['loops_per_second'] for i in range(5)]}")
loaded = T.thread_rates(T.parse_runtime(runtime_text(BEFORE, vectors=4)),
                        T.parse_runtime(runtime_text(BEFORE, at=WINDOW,
                                                     vectors=4)), WINDOW)
check(abs(loaded[0]["vectors_per_call"] - 4.0) < 0.01,
      f"  and vectors/call is packets per poll: {loaded[0]}")
check(T.thread_rates({}, {"main": {"loops_hdr": 7.0, "calls": 0,
                                   "vectors": 0, "polling": False}},
                     5)["main"]["loops_per_second"] == 7.0,
      "  a thread polling nothing keeps VPP's own header rate")
cards = T.parse_hardware(HW)
check(len(cards) == 1 and cards[0]["name"] == "TenGigabitEthernet1/0/0"
      and cards[0]["driver"] == "Intel 82599" and cards[0]["rx_queues"] == 5
      and cards[0]["placement"] == {0: 0, 1: 1, 2: 2, 3: 3, 4: 4}
      and cards[0]["numa"] == 0 and cards[0]["pci"] == "0000:01:00.00"
      and cards[0]["rss"].startswith("ipv4-tcp"),
      f"parse_hardware: the card DPDK drives, and not the tap: {cards}")
st = T.parse_proc_stat(proc_stat(BEFORE_STEAL, 1))
check(len(st) == 8 and st[3][2] == 77, f"parse_proc_stat: per CPU: {st[3]}")
win = T.cpu_window(T.parse_proc_stat(proc_stat(BEFORE_STEAL, 1)),
                   T.parse_proc_stat(proc_stat(BEFORE_STEAL, 2)))
check(win[3]["steal"] == 7.7 and win[0]["busy"] == 12.0,
      f"cpu_window: steal and busy in percent of the window: {win[3]} "
      f"{win[0]}")
mi = T.meminfo("MemTotal:       16357444 kB\nHugePages_Total:       6\n"
               "Hugepagesize:    1048576 kB\nBogus: x\n")
check(mi == {"MemTotal": 16357444, "HugePages_Total": 6,
             "Hugepagesize": 1048576}, f"meminfo: {mi}")
check(T.detect_virt("kvm\n") == ("kvm", "systemd-detect-virt")
      and T.detect_virt("", "QEMU", "Standard PC (Q35 + ICH9, 2009)")
      == ("kvm", "DMI")
      and T.detect_virt("", "Microsoft Corporation", "Virtual Machine")
      == ("microsoft", "DMI")
      and T.detect_virt("", "", "", "flags\t: fpu hypervisor sse")
      == ("hypervisor", "CPUID")
      and T.detect_virt("", "Dell Inc.", "PowerEdge R640", "flags : fpu")
      == ("none", "CPUID")
      and T.detect_virt() == ("", ""),
      "detect_virt: the door's word, then DMI, then the CPUID flag")
host = T.parse_host_topology(SV01 + "garbage line\n")
check(len(host["cpus"]) == 24 and host["cpus"][13] == (1, 1, 6)
      and host["nic_node"] == 1 and host["errors"] == ["garbage line"],
      f"parse_host_topology: lscpu's rows, the NIC's node, what it could not "
      f"read: {host['cpus'][13]} {host['errors']}")
hm = T.parse_host_topology(SV01 + NUMASTAT)
check(hm["vm_memory_mb"] == {0: 2.13, 1: 16484.0} and hm["errors"] == []
      and len(hm["cpus"]) == 24,
      f"  and a pasted numastat -p: the VM's memory by node: "
      f"{hm['vm_memory_mb']}")
check(T.parse_host_topology("0 - 0 0\n-1\n") == {
          "cpus": {0: (0, 0, 0)}, "nic_node": None, "errors": [],
          "vm_memory_mb": {}},
      "  a host with no NUMA is node 0, and -1 is no node declared")

# ---------------------------------------------------- the reference case
print("\n=== the .166, before and after the pinning ===")
fb, fa = facts(BEFORE, BEFORE_STEAL), facts(AFTER, AFTER_STEAL)
u, why = T.find_uneven(fb)
check(u is not None and "worker 3 (cpu 6) 2.41M" in u["measured"]
      and "25% below" in u["measured"],
      f"find_uneven: fires on the before numbers, naming worker 3: "
      f"{u and u['measured']}")
u, why = T.find_uneven(fa)
check(u is None and "none more than 15%" in why,
      f"  and is quiet on the after numbers: {why}")
u, why = T.find_uneven(facts([2.0e6, 3.3e6], [0] * 8))
check(u is not None, "  two workers are enough to judge")
busy = facts(BEFORE, BEFORE_STEAL)
busy["window"]["threads"][0]["vectors_per_call"] = 30.0
u, why = T.find_uneven(busy)
check(u is None and "carry traffic" in why,
      f"  and a loaded worker's rate is not judged: {why}")
s, why = T.find_steal(fb)
check(s is not None and "7.7% of the window" in s["measured"],
      f"find_steal: fires on the before steal: {s and s['measured']}")
s, why = T.find_steal(fa)
check(s is None and "0.00% on the dataplane" in why
      and "12.8% on the control plane" in why,
      f"  and is quiet on the after steal, all on the control plane: {why}")
check(T.dataplane_cpus(fb) == [2, 3, 4, 5, 6, 7]
      and T.dataplane_cpus({"layout": {"main": 0, "workers": [1]}}) == [1],
      "dataplane_cpus: the workers', and main's only when dedicated")
check(T._avg([0, 1], {0: {"steal": 1.0}, 1: {"steal": 3.0}}, "steal") == 2.0
      and T._avg([5], {}, "steal") is None, "_avg: the mean, None for none")
found, checked = T.findings(fb)
check([x["key"] for x in found] == ["uneven-workers", "steal"],
      f"findings: before has exactly the two: {[x['key'] for x in found]}")
found_a, checked_a = T.findings(fa)
check(found_a == [] and any(c.startswith("uneven workers") for c in checked_a)
      and any(c.startswith("steal") for c in checked_a)
      and any(c.startswith("queues") for c in checked_a),
      "  and after has none, and says what it checked")
check(T._finding("k", "warn", "t", "m", "th", "w") == {
          "key": "k", "severity": "warn", "title": "t", "measured": "m",
          "threshold": "th", "why": "w"}, "_finding: the shape both read")
check(T._m(2.41e6) == "2.41M" and T._m(245000) == "245k" and T._m(98) == "98",
      "_m: loop rates said short")

# ----------------------------------------------------- the other findings
print("\n=== the topology findings ===")
f = facts(AFTER, AFTER_STEAL)
f["cards"][0]["rx_queues"] = 2
f["cards"][0]["placement"] = {0: 0, 1: "main"}
q, ok = T.find_queues(f)
check(sorted(x["key"] for x in q) == ["queues-main:TenGigabitEthernet1/0/0",
                                      "queues:TenGigabitEthernet1/0/0"]
      and "2 rx queue(s) (Intel 82599) for 5 workers" in q[-1]["measured"],
      f"find_queues: fewer queues than workers, and one on main: "
      f"{[x['measured'] for x in q]}")
f["cards"][0]["rx_queues"] = 7
f["cards"][0]["placement"] = {i: i % 5 for i in range(7)}
q, ok = T.find_queues(f)
check([x["key"] for x in q] == ["queues-uneven:TenGigabitEthernet1/0/0"]
      and q[0]["severity"] == "info", "  and 7 over 5 do not divide")
q, ok = T.find_queues(fa)
check(q == [] and ok == ["queues: TenGigabitEthernet1/0/0 5 for 5 worker(s)"],
      f"  and 5 for 5 is fine: {ok}")
f = facts(AFTER, AFTER_STEAL)
f["topology"]["node_of"] = {c: (1 if c >= 6 else 0) for c in range(8)}
n, ok = T.find_numa(f)
check(len(n) == 1 and "worker(s) 3 (cpu 6, node 1), 4 (cpu 7, node 1)"
      in n[0]["measured"], f"find_numa: workers off the card's node: "
      f"{n and n[0]['measured']}")
f["cards"][0]["numa"] = None
n, ok = T.find_numa(f)
check(n == [] and "declares no node" in ok[0], "  and a card with none is not judged")
f = facts(AFTER, AFTER_STEAL)
f["memory"].update(effect="not-in-effect",
                   effect_said="6 page(s) of 1G were asked for and the kernel "
                               "reserved 4")
h, why = T.find_hugepages(f)
check(h is not None and "reserved 4" in h["measured"],
      "find_hugepages: the tuner's readback, refused, is a finding")
check(T.find_hugepages(fa) == (None, "hugepages: 6 page(s) of 1G reserved"),
      "  and in effect is not")
f["memory"]["effect"] = None
check(T.find_hugepages(f)[0] is None, "  and none asked is none")
f = facts(AFTER, AFTER_STEAL, smt=True, virt="none")
f["topology"]["siblings"] = {3: [3, 7], 7: [3, 7]}
sib, why = T.find_siblings(f)
check(sib is not None and "workers 0 and 4 (cpus 3, 7)" in sib["measured"],
      f"find_siblings: two workers on one core: {sib and sib['measured']}")
check(T.find_siblings(fa)[0] is None, "  and a guest that cannot see HT says so")
f = facts(AFTER, [0] * 8, virt="none")
f["layout"]["workers"] = [3, 4]
f["window"]["threads"] = {k: v for k, v in f["window"]["threads"].items()
                          if k in ("main", 0, 1)}
f["window"]["cpus"][0]["busy"] = f["window"]["cpus"][1]["busy"] = 4.0
mi_, why = T.find_main(f)
check(mi_ is not None and mi_["key"] == "main-idle",
      f"find_main: a dedicated main thread on an idle box of 2 workers: "
      f"{mi_ and mi_['measured']}")
f["layout"].update(main=0, main_dedicated=False, control_plane=[0, 1, 2])
for c in (0, 1, 2):
    f["window"]["cpus"][c]["busy"] = 80.0
mb, why = T.find_main(f)
check(mb is not None and mb["key"] == "main-busy",
      f"  and a shared one on a busy control plane: {mb and mb['measured']}")
check(T.find_main(fa)[0] is None, "  and the .166's is neither")
f = facts(AFTER, AFTER_STEAL, virt="none")
f["layout"]["isolated"] = [2, 3, 4]
iso, why = T.find_isolation(f)
check(iso is not None and "5-7" in iso["measured"],
      f"find_isolation: workers the kernel does not isolate, bare metal: "
      f"{iso and iso['measured']}")
check(T.find_isolation(fa) == (None, None), "  and a guest is not judged")
check(T.virtualized(fb) and not T.virtualized({"virt": {"kind": "none"}})
      and not T.virtualized({}), "virtualized: a hosted box only")

# ------------------------------------------------- what to type, and where
print("\n=== the recommendations ===")
check(T.box_recommendations(fb, T.findings(fb)[0]) == [],
      "box_recommendations: the .166 has nothing to type on the box")
recs = T.box_recommendations(f, [mi_, mb, iso, sib, h] + q[:0])
check([r["evidence"] for r in recs] == ["main-idle", "main-busy",
                                        "isolation", "ht-siblings",
                                        "hugepages"]
      and recs[0]["lines"] == ["configure", "system", "cpu",
                               "main-thread shared", "commit"]
      and recs[0]["restart"] == "reboot"
      and recs[2]["lines"] == ["system reboot"],
      "  each finding its lines, its restart, and its evidence")
qf = T._finding("queues:eth0", "warn", "t", "1 rx queue(s) (Red Hat Virtio)",
                "", "")
r = T.box_recommendations(fb, [qf])
check(r[0]["lines"] == ["system dataplane restart"]
      and r[0]["restart"] == "dataplane",
      "  a card short of queues is reopened: a dataplane restart")
roles = T.guest_roles(fb)
check(roles == {0: ("cp",), 1: ("cp",), 2: ("main",), 3: ("worker", 0),
                4: ("worker", 1), 5: ("worker", 2), 6: ("worker", 3),
                7: ("worker", 4)},
      f"guest_roles: the .166's vCPUs by role, from its own layout: {roles}")
check([c[:2] for c in T.host_cores(host, 1)] == [(1, 13), (3, 15), (5, 17),
                                                (7, 19), (9, 21), (11, 23)],
      "host_cores: node 1's cores, each its two threads")
pin = T.host_pinning(roles, host)
check(pin["map"] == {0: 13, 1: 13, 2: 1, 3: 3, 4: 5, 5: 7, 6: 9, 7: 11}
      and pin["emulator"] == [17, 19, 21, 23] and pin["node"] == 1
      and pin["others"] == list(range(0, 24, 2)) and pin["notes"] == [],
      f"host_pinning: main 1, cp 13, workers 3-11, QEMU 17-23: {pin}")
nonode = T.parse_host_topology(SV01.replace("nic-node 1", ""))
p2 = T.host_pinning(roles, nonode)
check(p2["notes"] and "not given" in p2["notes"][0],
      "  with no NIC node given it says which it assumed")
flat = T.parse_host_topology("\n".join(f"{c} 0 0 {c}" for c in range(4)))
p3 = T.host_pinning(roles, flat)
check(any("no HT siblings" in n for n in p3["notes"])
      and any("no physical core left" in n for n in p3["notes"]),
      f"  a host too small and with no HT says both: {p3['notes']}")
check(T._placeholders(roles)[0] == "HOST_CP"
      and T._placeholders(roles)[2] == "HOST_MAIN"
      and T._placeholders(roles)[7] == "HOST_W4",
      "_placeholders: a HOST_* name per role")
check(T._role_name(("worker", 3)) == "VPP worker 3"
      and T._role_name(("cp",)) == "control plane", "_role_name")

print("\n=== the Proxmox hookscript, against the one written by hand ===")
ref = open(REFERENCE).read()
gen = T.proxmox_hookscript(roles, pin)
want = script_map(ref, range(8))
got = script_map(gen, range(8))
check(len(want) == 8 and got == want,
      f"proxmox_hookscript: every vCPU where the .166's hand-written script "
      f"puts it: {got}")
check(script_emu(gen) == script_emu(ref) == "17,19,21,23",
      f"  and QEMU's other threads on the same four: {script_emu(gen)}")
check(subprocess.run(["sh", "-n"], input=gen, text=True).returncode == 0,
      "  and it parses as sh")
ph = T.proxmox_hookscript(roles)
check("HOST_W4" in ph and "FILL IN" in ph,
      "  without the host's topology its CPUs are marked HOST_*")
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "prisma-pin.sh")
    open(path, "w").write(ph)
    r = subprocess.run(["sh", path, "102", "post-start"], capture_output=True,
                       text=True)
check(r.returncode == 1 and "fill in the HOST_*" in r.stderr,
      f"  and refuses to run until they are filled in: {r.stderr.strip()}")
check(T.fold_assign("A", [1, 3]) == ["A=1,3"]
      and all(len(x) <= 79 for x in T.fold_assign("A", range(0, 200, 2)))
      and T.fold_assign("A", range(0, 200, 2))[1].startswith("A=$A,"),
      "fold_assign: a long list built up a piece at a time")
qm = T.qm_lines(roles, pin, fb)
check("qm set <vmid> --affinity $CPUS" in qm and "CPUS=1,3,5,7,9,11,13,17,"
      "19,21,23" in qm and any("hostnodes=1," in x for x in qm)
      and "OTHERS=0,2,4,6,8,10,12,14,16,18,20,22" in qm,
      f"qm_lines: affinity, the NIC's node, the other guests: {qm}")
check("<HOST-CPUS>" in "\n".join(T.qm_lines(roles, None, fb)),
      "  and placeholders without the host")
lv = T.libvirt_cputune(roles, pin, fb)
check("<vcpupin vcpu='2' cpuset='1'/>" in lv
      and "<emulatorpin cpuset='17,19,21,23'/>" in lv
      and "nodeset='1'" in lv, "libvirt_cputune: the same pinning")
check("cpuset='HOST_W0'" in T.libvirt_cputune(roles, None, fb),
      "  and placeholders without the host")

# ------------------------------------------- the host's kernel and memory
print("\n=== the host: its CPUs isolated, the VM's memory on the NIC's node ===")
check(T.host_cpus(pin) == [1, 3, 5, 7, 9, 11, 13, 17, 19, 21, 23]
      and T.host_cpus(None) == [],
      "host_cpus: every vCPU's host CPU and QEMU's, what the host isolates")
check(T.vm_memory_gb(fb) == 16 and T.vm_memory_gb({}) == 1,
      "vm_memory_gb: the guest's MemTotal, rounded up to the VM's 16 GB")
hk = "\n".join(T.host_kernel_lines(pin, fb))
check("CPUS=1,3,5,7,9,11,13,17,19,21,23" in hk
      and 'isolcpus=$CPUS"' in hk and 'nohz_full=$CPUS"' in hk
      and 'rcu_nocbs=$CPUS"' in hk and "hugepagesz=1G hugepages=1:16" in hk
      and "update-grub" in hk and "reboot the HOST" in hk
      and "node1/hugepages/" in hk,
      "host_kernel_lines: isolcpus, nohz_full, rcu_nocbs on the VM's CPUs, "
      "16 1G pages on node 1, update-grub and a host reboot")
with tempfile.TemporaryDirectory() as d:
    r = subprocess.run(["sh", "-c", hk.replace("/etc/default/grub.d/",
                                               d + "/").replace(
        "update-grub", ":")], capture_output=True, text=True)
    cfg = open(os.path.join(d, "prisma-host.cfg")).read()
check(r.returncode == 0 and 'GRUB_CMDLINE_LINUX="$GRUB_CMDLINE_LINUX '
      'isolcpus=1,3,5,7,9,11,13,17,19,21,23"' in cfg
      and "hugepages=1:16" in cfg,
      f"  and run, it writes a grub.d file that keeps the line before it: "
      f"{cfg.splitlines()[0]}")
hp = "\n".join(T.host_kernel_lines(None, fb))
check("CPUS=HOST_CPUS" in hp and "hugepages=HOST_NODE:16" in hp,
      "  without the host's topology, placeholders")
vm = T.find_vm_memory(hm, pin, fb)
check(vm is not None and vm["severity"] == "info"
      and "16.1 GB on node 1, 0.0 GB on node 0" in vm["measured"]
      and "first touch" in vm["measured"] and "numa: 1" in vm["why"],
      f"find_vm_memory: sv01's 16.1 GB on node 1 is local by luck, not "
      f"bound: {vm and vm['measured']}")
remote = dict(hm, vm_memory_mb={0: 8000.0, 1: 8484.0})
check(T.find_vm_memory(remote, pin, fb)["severity"] == "warn",
      "  and memory on the other node is a warning")
check(T.find_vm_memory(host, pin, fb) is None
      and T.find_vm_memory(hm, None, fb) is None,
      "  and nothing without the paste or the host's topology")
am = T.advise(fb, tune, SV01 + NUMASTAT)
check("vm-memory" in [x["key"] for x in am["findings"]]
      and any(h["evidence"] == "vm-memory" and h["restart"] == "vm-restart"
              for h in am["hypervisor"])
      and "isolcpus=$CPUS" in am["snippets"]["host_kernel"]
      and "never places another VM" in am["snippets"]["isolation"]
      and "not managed by Proxmox" in am["snippets"]["isolation"]
      and "does not bind" in am["snippets"]["memory"],
      "advise: the memory finding and its step, the host kernel block, "
      "and why each")
sm = "\n".join(T.render(fb, am))
check("--- Proxmox host, kernel command line" in sm
      and "takes a reboot of the host" in sm
      and "takes a stop and start of the VM" in sm
      and all(len(x) <= 79 for x in sm.splitlines()),
      "render: the host kernel block, what each step takes, 79 wide")
check("policy=bind" in "\n".join(T.qm_lines(roles, pin, fb))
      and "--numa 1 alone does not" in "\n".join(T.qm_lines(roles, pin, fb)),
      "qm_lines: numa0 binds; --numa 1 alone does not, said")

# ----------------------------------------------------------------- sizing
print("\n=== the sizing table: the tuner's own plan() ===")
check(tune is not None, "load_tuner: the tuner loads from the source tree")
check(T.load_tuner(["/nonexistent"]) is tune,
      "  and is loaded once")
check(T.recommended_split(4) == (1, "shared")
      and T.recommended_split(8) == (2, "dedicated")
      and T.recommended_split(24) == (3, "dedicated"),
      "recommended_split: shared below 6, two cp from 8, three from 24")
rows = T.sizing_table(tune, 8)
r8 = [r for r in rows if r["vcpus"] == 8][0]
check(r8 == {"vcpus": 8, "control_plane": 2, "main": "dedicated",
             "workers": 5, "host_threads": 12, "this_box": True},
      f"sizing_table: 8 vCPUs is the .166's 2 / main / 5: {r8}")
check([r["vcpus"] for r in rows][0] == 4 and rows[-1]["vcpus"] == 32
      and [r for r in rows if r["vcpus"] == 4][0]["workers"] == 3,
      "  from 4 to 32, and 4 keeps 3 workers")
check(T.sizing_table(None) == [], "  and none without the tuner")
g = tune.Topology.given(6, threads=2, nodes=2)
check(sorted(g.nodes) == [0, 1] and len(g.cpus) == 24
      and g.cpus[0][2] == [0, 12] and g.nodes[1][0] == 6,
      "Topology.given: a box described, in Linux's numbering")

# ------------------------------------------------------------ the screens
print("\n=== advise, render, the API's fields ===")
ab = T.advise(fb, tune, "")
check([x["key"] for x in ab["findings"]] == ["uneven-workers", "steal"]
      and [h["evidence"] for h in ab["hypervisor"]] == [
          "steal", "steal", "uneven-workers"]
      and ab["hypervisor"][0]["restart"] == "host-reboot"
      and ab["snippets"]["host_topology_used"] is False and ab["box"] == [],
      "advise: before - the two findings, the host's steps, placeholders")
ah = T.advise(fb, tune, SV01)
check(ah["snippets"]["host_topology_used"]
      and script_map(ah["snippets"]["proxmox_hookscript"], range(8)) == want,
      "  with the host's topology, the reference mapping")
check(T.advise(dict(fb, virt={"kind": "none"}), tune)["snippets"] is None,
      "  no hypervisor half on bare metal")
nolay = dict(fb, layout={})
check(T.advise(nolay, tune)["snippets"] is None,
      "  nor with no layout read")
check(T._wrap("a " * 60, indent="  ")[0].startswith("  a")
      and all(len(x) <= 79 for x in T._wrap("word " * 80)),
      "_wrap: to 79 columns")
rf = T.render_facts(fb)
check(any("workers 3-7" in x for x in rf)
      and any(x.startswith("  worker 3") and "2.41M" in x for x in rf),
      "render_facts: the layout and each thread's rate")
for name, fx, a in (("before", fb, ab), ("before + host", fb, ah),
                    ("after", fa, T.advise(fa, tune, "")),
                    ("bare metal", dict(fa, virt={"kind": "none"}),
                     T.advise(dict(fa, virt={"kind": "none"}), tune))):
    rows_ = T.render(fx, a)
    wide = [x for x in rows_ if len(x) > 79]
    check(not wide, f"render: {name} fits 79 columns ({len(rows_)} rows): "
                    f"{wide[:1]}")
screen = "\n".join(T.render(fa, T.advise(fa, tune, "")))
check("findings: none" in screen and "checked and fine:" in screen
      and "nothing to change" in screen,
      "  the after screen says nothing is wrong, and what was checked")
af = T.advice_fields(fb, ah)
check(af["facts"]["worker_cpus"] == "3-7" and af["facts"]["main_cpu"] == 2
      and af["facts"]["cards"][0]["queue_threads"] == [0, 1, 2, 3, 4]
      and [t["worker"] for t in af["facts"]["threads"]] == [-1, 0, 1, 2, 3, 4]
      and af["facts"]["threads"][4]["steal_percent"] == 7.7
      and af["sizing"][0]["main_thread"] == "shared"
      and af["snippets"]["host_topology_used"]
      and "isolcpus" in af["snippets"]["host_kernel"]
      and af["snippets"]["memory_binding"].startswith("Bind the VM's")
      and {r["side"] for r in af["recommendations"]} == {"hypervisor"},
      "advice_fields: the proto's names, from the same pair")
check(T.advice_fields(fb, dict(ab, snippets=None))["snippets"] is None,
      "  and no snippets is the message unset")

# ------------------------------------------------- the Shell and the API
print("\n=== the Shell reads a box, and the API carries it ===")
_root = tempfile.mkdtemp(prefix="tuneadvice-")
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
    put(f"sys/devices/system/cpu/cpu{c}/topology/physical_package_id", "0")
    put(f"sys/devices/system/cpu/cpu{c}/topology/core_id", str(c))
    put(f"sys/devices/system/cpu/cpu{c}/topology/thread_siblings_list", str(c))
put("sys/devices/system/node/node0/cpulist", "0-7")
put("sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages", "6")
put("sys/class/dmi/id/sys_vendor", "QEMU")
put("sys/class/dmi/id/product_name", "Standard PC (i440FX + PIIX, 1996)")
import prismash  # noqa: E402
import apiserver  # noqa: E402
sh = prismash.Shell()
sh.caps = {prismash.CAP_ALL}
sh.SYS_DEVICES = os.path.join(_root, "sys/devices")
sh.DMI = os.path.join(_root, "sys/class/dmi/id")
sh.CPUINFO = put("proc/cpuinfo", "flags\t: fpu hypervisor\n")
sh.CPU_ONLINE = put("sys/devices/system/cpu/online", "0-7\n")
sh.CPU_ISOLATED = put("sys/devices/system/cpu/isolated", "2-7\n")
sh.CPU_FENCE = put("fence", "0-1\n")
sh.MEMINFO = put("proc/meminfo", "MemTotal: 16357444 kB\n"
                 "HugePages_Total: 6\nHugepagesize: 1048576 kB\n")
sh.PROC_CMDLINE = put("proc/cmdline", "BOOT_IMAGE=/vmlinuz hugepagesz=1G "
                      "hugepages=6 isolcpus=2-7\n")
sh.STARTUP_CONF = put("startup.conf", "ethernet {\n  default-mtu 1500\n}\n")
sh.TUNE_ENV = put("tune.env", "PRISMA_CONTROL_PLANE_CORES=2\n"
                  "PRISMA_MAIN_THREAD=dedicated\n")
stat_reads = iter([proc_stat(BEFORE_STEAL, 1), proc_stat(BEFORE_STEAL, 2)])
prismash.PROC_STAT = put("proc/stat", proc_stat(BEFORE_STEAL, 1))
_real_read = sh.read_text


def read_text(path):
    """The box's files; /proc/stat moves on at each read."""
    if path == prismash.PROC_STAT:
        return next(stat_reads)
    return _real_read(path)


sh.read_text = read_text
asked, slept = [], []
rt_reads = iter([runtime_text(BEFORE), runtime_text(BEFORE, at=WINDOW)])


def run(_self, cmd):
    asked.append(cmd)
    return {"show threads": (0, THREADS),
            "show hardware-interfaces": (0, HW)}.get(
        cmd, (0, next(rt_reads)) if cmd == "show runtime" else (0, ""))


sh.dp = type("D", (), {"TRACE": False, "run": run})()
sh.running_config = lambda: type("C", (), {"pools": {
    "cgnat": type("P", (), {"tcp_mss": "auto"})()}})()
doors_asked = []
prismash.doors.runner = lambda *a, **k: (
    lambda argv, timeout=30: (doors_asked.append(tuple(argv)) or (0, "kvm\n")))
clock = iter([100.0, 105.0])
t0 = time.monotonic()
got = sh.tuning_advice_facts(5, sleep=slept.append, clock=lambda: next(clock))
took = time.monotonic() - t0
check(slept == [5] and got["window"]["seconds"] == 5
      and got["window"]["elapsed"] == 5.0,
      f"tuning_advice_facts: a window of 5 s, slept once, measured on the "
      f"injected clock (took {took:.2f} s here)")
check(asked.count("show runtime") == 2 and "clear runtime" not in asked,
      f"  show runtime read twice and nothing cleared: {asked}")
check(doors_asked == [("systemd-detect-virt", "--vm")]
      and got["virt"] == {"kind": "kvm", "source": "systemd-detect-virt"},
      "  virtualization asked of the host's door")
check(got["layout"] == {"control_plane": [0, 1], "main": 2,
                        "main_dedicated": True, "workers": [3, 4, 5, 6, 7],
                        "isolated": [2, 3, 4, 5, 6, 7]},
      f"  the layout from show threads and the fence: {got['layout']}")
check(got["topology"]["cores"] == 8 and got["topology"]["online"]
      == list(range(8)) and not got["topology"]["smt"],
      "  the topology through the tuner's own Topology")
check(got["memory"]["effect"] == "in-effect"
      and "6 page(s) of 1G reserved" in got["memory"]["effect_said"],
      f"  hugepages by the tuner's readback: {got['memory']['effect_said']}")
check(got["tcp_mss"] == {"cgnat": "auto"} and got["mtu"] == 1500,
      "  the MTU and each pool's tcp-mss")
check([round(got["window"]["threads"][i]["loops_per_second"] / 1e4)
       for i in range(5)] == [298, 329, 323, 241, 323]
      and got["window"]["cpus"][3]["steal"] == 7.7,
      "  and the window's rates and steal")
fx, adv = sh.tuning_advice(facts=got, host_text=SV01)
check([x["key"] for x in adv["findings"]] == ["uneven-workers", "steal"]
      and adv["snippets"]["host_topology_used"],
      "tuning_advice: the pair the screen and the API draw")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.show_system_tuning_advice([], fx, adv)
check("[warn] uneven workers" in out.getvalue()
      and all(len(x) <= 79 for x in out.getvalue().splitlines()),
      "show_system_tuning_advice: draws the reading it is handed, 79 wide")


def say(line, stdin=""):
    """What the VTY prints for `line`, with `stdin` to read from."""
    buf = io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            sh.feed(line)
    finally:
        sys.stdin = old
    return buf.getvalue()


said = say("show system tuning advice seconds 99")
check(f"seconds: a whole number from {T.WINDOW_MIN} to {T.WINDOW_MAX}" in said,
      f"  refuses a window past 60 s: {said.strip()}")
said = say("show system tuning advice sideways")
check("takes 'seconds <1-60>' and 'host-topology'" in said,
      f"  and a word it does not take: {said.strip()}")
sh.tuning_advice = lambda seconds=5, host_text="", facts=None: (
    sh._seen.append((seconds, host_text)) or (fb, T.advise(fb, tune,
                                                          host_text)))
sh._seen = []
said = say("show system tuning advice seconds 2 host-topology",
           SV01 + "\nignored after the empty line\n")
check(sh._seen == [(2, SV01.rstrip("\n"))] and "measuring for 2 s" in said
      and "    0|1) echo 13 ;;" in said,
      "  host-topology reads the paste up to an empty line, and fills in "
      "the host's CPUs")
sys.stdin, _old = io.StringIO("0 0 0 0\nend\n1 1 1 1\n"), sys.stdin
with contextlib.redirect_stdout(io.StringIO()):
    pasted = sh.read_host_topology()
sys.stdin = _old
check(pasted == "0 0 0 0", f"read_host_topology: stops at `end`: {pasted!r}")
topo = T.cpu_topology(tune, _root, [0, 1, 2, 3, 4, 5, 6, 7])
check(topo["sockets"] == 1 and topo["cores"] == 8 and topo["nodes"] == 1
      and not topo["smt"] and topo["cpus"][5] == {"socket": 0, "core": 5,
                                                  "node": 0, "siblings": [5]},
      f"cpu_topology: the tuner's Topology, per CPU too: {topo['cpus'][5]}")
check(T.cpu_topology(None, "/nonexistent", [0, 1])["online"] == [0, 1]
      and T.cpu_topology(tune, "/nonexistent")["cpus"] == {},
      "  and with no tuner or no /sys, the online CPUs and nothing else")
check(T.cpu_planes(topo, fb["layout"]) == {
          0: "control-plane", 1: "control-plane", 2: "main", 3: "worker",
          4: "worker", 5: "worker", 6: "worker", 7: "worker"},
      "cpu_planes: each CPU's plane this boot, the .166's layout")
check(sh.read_text("/nonexistent/x") == "" and "QEMU" in _real_read(
    os.path.join(_root, "sys/class/dmi/id/sys_vendor")),
      "read_text: a file, or '' when it cannot be read")
api = apiserver.tuning_advice_read(sh, {"seconds": 3, "host_topology": SV01})
check(sh._seen[-1] == (3, SV01) and api["fields"]["facts"]["worker_cpus"]
      == "3-7" and "[warn] uneven workers" in api["text"],
      "tuning_advice_read: the API's fields and text from one reading")
api0 = apiserver.tuning_advice_read(sh, {})
check(sh._seen[-1] == (5, ""), "  and 0 seconds is the VTY's default, 5")

print(f"\n  (this suite: {time.monotonic() - STARTED:.1f} s)")
print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
