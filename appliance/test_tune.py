#!/usr/bin/env python3
"""
Tests for hexarouting-tune against synthetic topologies.

The development machine has neither SMT nor multiple NUMA nodes, which is
exactly where the tuning decisions matter. So the topologies are built on
disk and checked.
"""
import os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TUNE = os.path.join(HERE, "hexarouting-tune")
fail = 0

def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")

def w(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(str(content) + "\n")

def build(root, sockets, cores_per_socket, threads, nic_node=None,
          hugepages_1g=0, isolated="", nic_pci=None, ram_gb=64):
    """
    Typical Linux numbering: first every first thread, then every second one.
    With 2 sockets x 8 cores x 2 threads, the sibling of cpu0 is cpu16.
    e a cpu16.
    """
    per_thread = sockets * cores_per_socket
    node_cpus = {s: [] for s in range(sockets)}
    for t in range(threads):
        for s in range(sockets):
            for c in range(cores_per_socket):
                n = t * per_thread + s * cores_per_socket + c
                sibs = [k * per_thread + s * cores_per_socket + c
                        for k in range(threads)]
                base = f"{root}/sys/devices/system/cpu/cpu{n}/topology"
                w(f"{base}/physical_package_id", s)
                w(f"{base}/core_id", c)
                w(f"{base}/thread_siblings_list", ",".join(map(str, sibs)))
                node_cpus[s].append(n)
    for s, cpus in node_cpus.items():
        w(f"{root}/sys/devices/system/node/node{s}/cpulist",
          ",".join(map(str, sorted(cpus))))
    if nic_node is not None:
        if nic_pci:
            # A real NIC is a symlink to the PCI device. Without reproducing
            # that, the path generating the dpdk stanza is never exercised.
            dev = f"{root}/sys/devices/pci0000:00/{nic_pci}"
            w(f"{dev}/numa_node", nic_node)
            os.makedirs(f"{root}/sys/class/net/eth0", exist_ok=True)
            os.symlink(dev, f"{root}/sys/class/net/eth0/device")
        else:
            w(f"{root}/sys/class/net/eth0/device/numa_node", nic_node)
    w(f"{root}/sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages",
      hugepages_1g)
    w(f"{root}/sys/devices/system/cpu/isolated", isolated)
    # Plenty of RAM unless a case says otherwise, so the existing cases are
    # unaffected by the cap below.
    w(f"{root}/proc/meminfo", f"MemTotal:       {ram_gb * 1024 * 1024} kB")
    return node_cpus

def run(root, *args):
    p = subprocess.run([sys.executable, TUNE, "--sysfs-root", root] + list(args),
                       capture_output=True, text=True)
    return p.stdout

def field(out, prefix):
    for line in out.split("\n"):
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""

def expand(spec):
    out = []
    for part in spec.split(","):
        if not part: continue
        if "-" in part:
            a, b = part.split("-"); out += list(range(int(a), int(b)+1))
        else:
            out.append(int(part))
    return sorted(out)

# ---------------------------------------------------------------- casos

print("=== 2 sockets x 8 cores x 2 threads (32 CPUs), NIC no no 1 ===")
with tempfile.TemporaryDirectory() as root:
    nodes = build(root, sockets=2, cores_per_socket=8, threads=2, nic_node=1)
    out = run(root, "--interface", "eth0")
    workers = expand(field(out, "# workers"). split(": ")[-1])
    isolated = expand(field(out, "# isolated:"))
    oscpus = expand(field(out, "# operating system and VPP main:"))
    print(f"  workers={len(workers)} isolated={len(isolated)} os={oscpus}")

    check(all(c in nodes[1] for c in workers),
          f"workers fora do no da NIC: {[c for c in workers if c not in nodes[1]]}")
    check(len(workers) == 7, f"expected 7 workers (8 cores - 1 for the OS), got {len(workers)}")
    check(len(isolated) == 14, f"expected 14 isolated CPUs (7 cores x 2 threads), got {len(isolated)}")
    check(not set(workers) & set(oscpus), "worker landed on an operating-system CPU")
    check(len(oscpus) == 2, f"the OS should keep the whole core (2 threads), got {oscpus}")
    # no worker sibling may be left out of the isolation
    for c in workers:
        sib = c + 16 if c < 16 else c - 16
        check(sib in isolated, f"SMT sibling {sib} of worker {c} was not isolated")

print("\n=== 1 socket x 4 cores, no SMT ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0)
    out = run(root, "--interface", "eth0")
    workers = expand(field(out, "# workers").split(": ")[-1])
    isolated = expand(field(out, "# isolated:"))
    print(f"  workers={workers} isolated={isolated}")
    check(workers == [1, 2, 3], f"expected [1,2,3], got {workers}")
    check(isolated == [1, 2, 3], f"without SMT the isolated set is the workers; got {isolated}")

print("\n=== minimal machine: 1 socket x 1 core, no SMT ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=1, threads=1, nic_node=0)
    out = run(root, "--interface", "eth0")
    print("  " + field(out, "# workers"))
    check("(none)" in out or "no workers" in out,
          "a 1-core machine should end with no workers, not with an empty isolcpus")
    check("isolcpus=" not in out.split("### /etc/vpp")[0].split("GRUB")[-1],
          "must not emit isolcpus on a machine with no core to spare")

print("\n=== NIC with no NUMA affinity declared (-1) ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=2, cores_per_socket=4, threads=1, nic_node=-1)
    out = run(root, "--interface", "eth0")
    check("declares no NUMA affinity" in out,
          "should warn that the NIC declared no NUMA node")
    workers = expand(field(out, "# workers").split(": ")[-1])
    check(all(c < 4 for c in workers),
          f"with no affinity it should fall back to node 0; workers={workers}")
    print(f"  fell back to node 0: workers={workers}")

print("\n=== worker limit is honoured ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=2, nic_node=0)
    out = run(root, "--interface", "eth0", "--max-workers", "3")
    workers = expand(field(out, "# workers").split(": ")[-1])
    isolated = expand(field(out, "# isolated:"))
    check(len(workers) == 3, f"expected 3 workers, got {len(workers)}")
    check(len(isolated) == 6, f"3 cores x 2 threads = 6 isolated, got {len(isolated)}")
    print(f"  workers={workers} isolated={isolated}")

print("\n=== no cap is the shipped default, and it takes every core but one ===")
# HEXA_MAX_WORKERS ships empty. An operator who sets nothing has to get the
# machine they get today: changing the worker count by default would change the
# throughput of every installed box on its next boot.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=2, nic_node=0)
    uncapped = run(root, "--interface", "eth0")
    workers = expand(field(uncapped, "# workers").split(": ")[-1])
    isolated = expand(field(uncapped, "# isolated:"))
    check(len(workers) == 7, f"uncapped, 8 cores give 7 workers; got {len(workers)}")
    check(len(isolated) == 14, f"7 cores x 2 threads = 14 isolated, got {len(isolated)}")
    check(uncapped != run(root, "--interface", "eth0", "--max-workers", "3"),
          "if the cap changes nothing when set, it is not wired to anything")
    print(f"  uncapped -> {len(workers)} workers")

print("\n=== a cap below the count frees the cores, not just the worker list ===")
# The point of the cap is cores the control plane can run on, not fewer
# workers. A cap that shortened the worker list but left the cores in isolcpus
# would cost forwarding capacity and hand the control plane nothing - the
# scheduler still would not be allowed to put frr, hexash or sshd there.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=2, nic_node=0,
          hugepages_1g=8, isolated="1-7,9-15", nic_pci="0000:3b:00.0")
    out = run(root, "--interface", "eth0", "--max-workers", "3")
    workers = expand(field(out, "# workers").split(": ")[-1])
    isolated = expand(field(out, "# isolated:"))
    check(workers == [1, 2, 3], f"expected workers [1,2,3], got {workers}")
    check(isolated == [1, 2, 3, 9, 10, 11],
          f"only the capped cores and their siblings stay isolated; got {isolated}")
    check("isolcpus=1-3,9-11" in out,
          "the kernel command line must release the cores the cap gave back")
    check("num-rx-queues 3" in out,
          "one RX queue per worker still holds under a cap: more queues than "
          "workers leaves traffic arriving on a queue nobody polls")
    print(f"  workers={workers} isolated={isolated}")

print("\n=== a cap above what the machine has invents no workers ===")
# An operator sizing for the box they are about to buy must not get a plan
# pinning workers to cores that do not exist: VPP would fail to place a thread
# and the dataplane would not start at all.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0)
    out = run(root, "--interface", "eth0", "--max-workers", "64")
    workers = expand(field(out, "# workers").split(": ")[-1])
    check(workers == [1, 2, 3],
          f"a 4-core node has 3 workers to give, whatever is asked; got {workers}")
    check(workers == expand(field(run(root, "--interface", "eth0"),
                                  "# workers").split(": ")[-1]),
          "a cap above the count must be identical to no cap")
    print(f"  asked for 64 on a 4-core node -> {workers}")

print("\n=== hugepages grow with the subscriber count ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0)
    small = int(field(run(root, "--interface", "eth0", "--subscribers", "10000"),
                      "# 1G hugepages:").split()[0])
    big = int(field(run(root, "--interface", "eth0", "--subscribers", "500000"),
                    "# 1G hugepages:").split()[0])
    check(big > small, f"500k subscribers ({big}G) should ask for more than 10k ({small}G)")
    print(f"  10k subscribers -> {small}G ; 500k -> {big}G")

print("\n=== first boot: kernel still without hugepages or isolcpus ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_1g=0, isolated="")
    out = run(root, "--interface", "eth0")
    check("FIRST BOOT" in out, "should detect that the kernel is not tuned")
    check("FIRST BOOT MODE" in out, "startup.conf should come out conservative")
    check("corelist-workers" not in out,
          "must not pin workers without isolcpus: VPP would take the OS cores")
    check("default-hugepage" not in out,
          "must not demand hugepages that do not exist: that is a VPP restart loop")
    check("page-size 4k" in out, "should fall back to 4k pages")
    check("plugin dpdk_plugin.so { disable }" in out,
          "without hugepages the DPDK EAL fails and VPP exits; with Restart=always "
          "that becomes a restart loop")
    # and the GRUB line keeps asking for the tuning on the next boot
    check("isolcpus=1-7" in out, "GRUB must still ask for isolcpus on the next boot")
    check("hugepages=" in out, "GRUB must still ask for hugepages on the next boot")
    print("  conservative now, GRUB asking for the tuning on the next boot")

print("\n=== next boot: kernel already tuned ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_1g=6, isolated="1-7")
    out = run(root, "--interface", "eth0")
    check("FIRST BOOT" not in out, "should no longer be in first boot")
    check("corelist-workers 1-7" in out, "should pin the workers")
    check("default-hugepage" in out, "should use hugepages")
    check("plugin dpdk_plugin.so { disable }" not in out,
          "with hugepages DPDK has to come back")
    check("bpa_plugin.so { enable }" in out, "the CGNAT plugin has to be enabled")
    print("  full config: pinning and hugepages")

print("\n=== hugepages reserved but isolcpus missing: still conservative ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_1g=6, isolated="")
    out = run(root, "--interface", "eth0")
    check("FIRST BOOT" in out,
          "without isolcpus, pinning a worker makes VPP compete with the OS")
    print("  halfway is still treated as untuned")

print("\n=== hugepages are bounded by the RAM the machine has ===")
# An installed box asked for 5 GB of hugepages on 3 GB of RAM, started VPP
# against a heap that was not there, and died with SIGSEGV in a restart loop -
# after passing every other check. Sizing from the subscriber target alone is
# what produced it.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=2, threads=1, nic_node=0,
          hugepages_1g=6, isolated="1", ram_gb=3)
    out = run(root, "--interface", "eth0", "--subscribers", "200000")
    asked = int(field(out, "# 1G hugepages:").split()[0])
    check(asked == 0,
          f"a 3 GB box must not be told to reserve {asked} GB of hugepages")
    check("WARNING" in out, "the operator must be told the box is undersized")
    print(f"  3 GB box, 200k subscribers -> {asked} GB requested, warned")

with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_1g=6, isolated="1-7", ram_gb=16)
    out = run(root, "--interface", "eth0", "--subscribers", "200000")
    asked = int(field(out, "# 1G hugepages:").split()[0])
    check(0 < asked <= 12, f"16 GB should allow a real pool, got {asked}")
    check("WARNING" not in out, "a box with room must not be warned")
    print(f"  16 GB box, 200k subscribers -> {asked} GB requested, no warning")

with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_1g=6, isolated="1-7", ram_gb=8)
    out = run(root, "--interface", "eth0", "--subscribers", "2000000")
    check("WARNING" in out, "an oversized target on a small box must warn")
    check("add RAM" in out, "and say what to do about it")
    print("  8 GB box, 2M subscribers -> capped and warned")

print("\n=== the main heap is sized from the pages actually reserved ===")
# A fixed 2G heap against a smaller pool starts VPP on memory that is not
# there, and it dies with SIGSEGV. Seen on a box whose RAM was reduced between
# the boot that computed the tuning and the boot that used it.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          hugepages_1g=2, isolated="1-3", ram_gb=16)
    out = run(root, "--interface", "eth0", "--subscribers", "200000")
    heap = [l for l in out.splitlines() if "main-heap-size" in l]
    check(any("1G" in l for l in heap),
          f"a 2 GB pool must not be given a 2G heap: {heap}")
    print(f"  2 GB reserved ->{heap[0].split('main-heap-size')[1]}")

with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          hugepages_1g=8, isolated="1-3", ram_gb=32)
    out = run(root, "--interface", "eth0", "--subscribers", "200000")
    heap = [l for l in out.splitlines() if "main-heap-size" in l]
    check(any("2G" in l for l in heap),
          f"a large pool should still get the full heap: {heap}")
    print(f"  8 GB reserved ->{heap[0].split('main-heap-size')[1]}")

print("\n=== one RX queue per worker, and the NIC's PCI address ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_1g=6, isolated="1-7", nic_pci="0000:3b:00.0")
    out = run(root, "--interface", "eth0")
    workers = expand(field(out, "# workers").split(": ")[-1])
    check("dev 0000:3b:00.0" in out, "the dpdk stanza must name the PCI device")
    check(f"num-rx-queues {len(workers)}" in out,
          f"one queue per worker: expected {len(workers)}")
    check("num-tx-queues" in out, "without a TX queue count DPDK falls back to its default")
    print(f"  dpdk: {len(workers)} queues on 0000:3b:00.0")

print("\n=== a NIC that is not PCI must not invent an address ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          hugepages_1g=6, isolated="1-3")
    out = run(root, "--interface", "eth0")
    check("dev 0000:" not in out, "must not invent a PCI address")
    # This used to assert "dev default" - sizing the queues on the default
    # rather than naming a device. That reads as a harmless fallback and is
    # not one: with no device named, VPP's DPDK plugin probes and claims
    # whatever it can bind, which on a single-NIC box is the management
    # interface. The box left the network the moment the dataplane started
    # (#123). The test asserted the behaviour that caused it.
    check("no-pci" in out,
          "an unresolvable NIC must claim nothing, not everything")
    check("dev default" not in out,
          "and must not leave DPDK probing for a device to take")
    print("  claims no device")

print("\n=== C-states and THP off by default ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_1g=6, isolated="1-7")
    out = run(root, "--interface", "eth0")
    check("processor.max_cstate=1" in out, "deep C-states must leave the command line")
    check("intel_idle.max_cstate=0" in out, "intel_idle also governs C-states")
    check("transparent_hugepage=never" in out, "khugepaged stalls the CPU it compacts on")
    off = run(root, "--interface", "eth0", "--no-low-latency")
    check("max_cstate" not in off, "--no-low-latency has to actually remove it")
    check("isolcpus=1-7" in off, "and must not take the rest of the tuning with it")
    print("  on by default, switchable off")

print("\n=== IRQs banned from the isolated cores ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=2, nic_node=0,
          hugepages_1g=6, isolated="1-7,9-15")
    out = run(root, "--interface", "eth0")
    banned = field(out, "IRQBALANCE_BANNED_CPULIST=")
    check(expand(banned) == expand(field(out, "# isolated:")),
          f"banned must be exactly the isolated set; got {banned!r}")
    print(f"  banned: {banned}")

print("\n=== a machine with no core to isolate emits no empty ban list ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=1, threads=1, nic_node=0)
    out = run(root, "--interface", "eth0")
    check("IRQBALANCE_BANNED_CPULIST=" not in out,
          "banning an empty list disables irqbalance entirely in some versions")
    print("  emits no ban")

# ---------------------------------------------------------------- apply-tuning

print("\n=== apply-tuning must not stall the boot on a live system ===")
import subprocess, os as _os, tempfile as _tf, shutil as _sh

APPLY = os.path.join(HERE, "apply-tuning")
fakebin = _tf.mkdtemp()
# A slow fake update-grub: if it gets called, the test notices
with open(os.path.join(fakebin, "update-grub"), "w") as f:
    f.write("#!/bin/sh\necho CALLED-UPDATE-GRUB\nsleep 5\n")
_os.chmod(os.path.join(fakebin, "update-grub"), 0o755)
with open(os.path.join(fakebin, "hexarouting-tune"), "w") as f:
    f.write("#!/bin/sh\nexit 0\n")
_os.chmod(os.path.join(fakebin, "hexarouting-tune"), 0o755)

env = dict(_os.environ, PATH=fakebin + ":" + _os.environ["PATH"],
           HEXA_DATAPLANE_IF="", HEXA_SUBSCRIBERS="1000")
r = subprocess.run(["sh", APPLY], capture_output=True, text=True, env=env,
                   timeout=30)
out = r.stdout + r.stderr
check("CALLED-UPDATE-GRUB" in out or "tuning unchanged" in out,
      "on a normal system update-grub should be considered")
# The governor step runs before everything else: if it aborts because there
# is no cpufreq, the rest of apply-tuning never happens.
check("governor" in out, f"apply-tuning should report the governor; output was {out!r}")
check(r.returncode == 0, f"apply-tuning must not fail here (rc={r.returncode})")
print("  normal system: behaviour preserved, governor reported")
_sh.rmtree(fakebin, ignore_errors=True)

# --- the worker cap has to survive the trip from tune.env to the tuner (#172) ---
#
# hexarouting-tune has accepted --max-workers since it was written. apply-tuning
# never passed it and tune.env had no variable for it, so the flag was reachable
# only by hand and every booted box ran uncapped. Testing the computation alone
# is what let that stand: the calculation was right and nothing called it.
print("\n=== the worker cap reaches the tuner from the environment ===")
fakebin = _tf.mkdtemp()
argvlog = os.path.join(fakebin, "argv")
with open(os.path.join(fakebin, "hexarouting-tune"), "w") as f:
    f.write(f'#!/bin/sh\necho "$@" >> {argvlog}\nexit 0\n')
_os.chmod(os.path.join(fakebin, "hexarouting-tune"), 0o755)

def apply_argv(**extra):
    open(argvlog, "w").close()
    env = dict(_os.environ, PATH=fakebin + ":" + _os.environ["PATH"],
               HEXA_DATAPLANE_IF="", HEXA_SUBSCRIBERS="1000", **extra)
    r = subprocess.run(["sh", APPLY], capture_output=True, text=True, env=env,
                       timeout=30)
    return open(argvlog).read().strip(), r

argv, r = apply_argv(HEXA_MAX_WORKERS="4")
check("--max-workers 4" in argv, f"the cap never reached the tuner: {argv!r}")
check(r.returncode == 0, f"apply-tuning must still succeed (rc={r.returncode})")
print(f"  HEXA_MAX_WORKERS=4 -> {argv}")

argv, r = apply_argv(HEXA_MAX_WORKERS="")
check("--max-workers" not in argv,
      f"empty must stay uncapped, or every box changes on its next boot: {argv!r}")
check(r.returncode == 0, f"apply-tuning must still succeed (rc={r.returncode})")
print(f"  HEXA_MAX_WORKERS= (empty) -> {argv}")

# Unset behaves as empty: an installed box whose tune.env predates the variable
# has to keep booting the way it does now.
argv, r = apply_argv()
check("--max-workers" not in argv,
      f"an older tune.env with no such variable must stay uncapped: {argv!r}")
check(r.returncode == 0, f"apply-tuning must still succeed (rc={r.returncode})")
print("  unset -> uncapped, same as an existing box")

# A typo must cost the setting, not the tuning. argparse exits non-zero on a
# non-integer, which would fail hexarouting-tune.service and leave the kernel
# command line at whatever the previous boot wrote - isolcpus and startup.conf
# then disagree, which is the restart-loop shape, not a lost setting.
argv, r = apply_argv(HEXA_MAX_WORKERS="four")
check("--max-workers" not in argv, f"garbage must not be forwarded: {argv!r}")
check(r.returncode == 0,
      f"a typo in tune.env must not fail the tuning unit (rc={r.returncode})")
check("HEXA_MAX_WORKERS" in r.stdout + r.stderr,
      "and it must say so, or the operator sets it and nothing happens")
print("  HEXA_MAX_WORKERS=four -> ignored, reported, tuning still applied")
_sh.rmtree(fakebin, ignore_errors=True)

# --- which NIC the dataplane is allowed to claim (#123) ---
#
# The shipped default was HEXA_DATAPLANE_IF=eth0, which does not exist on a
# Debian 12 box with predictable names. The tuner could not resolve it, emitted
# "dev default" with no allowlist, and VPP took whatever NIC it found - the
# management one. Not knowing which NIC carries traffic made it take ALL of
# them, when it must take NONE.
print("\n=== an unidentified dataplane NIC must claim no device ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          hugepages_1g=8, isolated="1-3")
    out = run(root, "--interface", "ens18")   # no such interface in the tree
    check("no-pci" in out,
          "an unresolvable NIC must produce 'no-pci', so nothing is claimed")
    check("dev default" not in out,
          "and must NOT leave DPDK probing for whatever it can bind")
    print("  " + (field(out, "# dataplane NIC PCI address:") or "(none)"))

print("\n=== no interface named at all: same answer ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          hugepages_1g=8, isolated="1-3")
    out = run(root)
    check("no-pci" in out, "an empty setting must claim nothing either")
    check("dev default" not in out, "and must not fall back to probing")

print("\n=== the interface carrying the default route is refused ===")
# Even when it is named on purpose. Handing VPP the management path does not
# degrade the box, it removes it: the operator loses SSH the instant the
# dataplane starts, and gets it back by walking to a console.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          hugepages_1g=8, isolated="1-3")
    os.makedirs(f"{root}/proc/net", exist_ok=True)
    with open(f"{root}/proc/net/route", "w") as f:
        f.write("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n")
        f.write("eth0\t00000000\t0102A8C0\t0003\t0\t0\t0\t00000000\n")
    out = run(root, "--interface", "eth0")
    check("no-pci" in out,
          "the management interface must not be handed to the dataplane")
    check("REFUSED" in out,
          "and the refusal must be stated, not silent")
    print("  " + field(out, "# "))

print("\n=== a NIC that is not the default route is still claimed ===")
# The refusal must not become "never claim anything", which would be the same
# defect pointing the other way: a box that cannot forward is also a box that
# does not work.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_1g=8, isolated="1-3")
    os.makedirs(f"{root}/proc/net", exist_ok=True)
    with open(f"{root}/proc/net/route", "w") as f:
        f.write("Iface\tDestination\tGateway\tFlags\n")
        f.write("ens19\t00000000\t0102A8C0\t0003\n")
    out = run(root, "--interface", "eth0")
    check("no-pci" not in out,
          "a data NIC that is not the management path must still be claimed")
    check("dev 0000:" in out,
          f"and named by PCI address: {field(out, '# dataplane NIC PCI address:')}")


print("\n=== a virtio NIC resolves to the PCI device behind it (#286) ===")
# Every VM has one, and the tuner could not name a single one of them. The
# `device` link points at the device that OWNS the netdev, and on virtio that
# is the virtio bus device whose PARENT is the PCI one:
#
#   /sys/class/net/ens18/device -> ../../../virtio3
#   realpath: /sys/devices/pci0000:00/0000:00:12.0/virtio3
#
# Reading the basename gave `virtio3`, so nic_pci() answered None, the tuner
# wrote `no-pci`, and VPP was given nothing - with the interface named
# correctly. DPDK drives virtio through its own poll-mode driver; the card was
# always eligible.
#
# Bare metal hid it: an Intel NIC's `device` link points straight at the PCI
# device, and there the basename IS the address.
_d = tempfile.mkdtemp()
os.makedirs(f"{_d}/sys/devices/pci0000:00/0000:00:12.0/virtio3", exist_ok=True)
os.makedirs(f"{_d}/sys/class/net/ens18", exist_ok=True)
os.symlink(f"{_d}/sys/devices/pci0000:00/0000:00:12.0/virtio3",
           f"{_d}/sys/class/net/ens18/device")
# Something genuinely not on PCI, which must still answer "nothing to give".
os.makedirs(f"{_d}/sys/devices/platform/soc/eth-mmio", exist_ok=True)
os.makedirs(f"{_d}/sys/class/net/eth9", exist_ok=True)
os.symlink(f"{_d}/sys/devices/platform/soc/eth-mmio",
           f"{_d}/sys/class/net/eth9/device")

# The tuner is a script, not a module, so it is loaded rather than imported.
# Its own functions are what is under test here: driving the whole thing as a
# subprocess would answer "was a dpdk stanza written" and this asks the
# narrower question of what one interface resolves to.
_ns = {"__name__": "hexarouting_tune"}
exec(compile(open(TUNE).read().split("\ndef plan(")[0], TUNE, "exec"), _ns)
Topology, is_pci_addr = _ns["Topology"], _ns["is_pci_addr"]

_t = Topology.__new__(Topology)
_t.root = _d
check(_t.nic_pci("ens18") == "0000:00:12.0",
      f"a virtio NIC resolved to {_t.nic_pci('ens18')!r}: the tuner writes "
      f"no-pci for it, so every box in a VM is given no dataplane card even "
      f"with the interface named correctly")
check(_t.nic_pci("eth9") is None,
      f"a device that is not on PCI at all resolved to "
      f"{_t.nic_pci('eth9')!r}; DPDK has nothing it could be given and saying "
      f"otherwise puts a name in startup.conf that claims no device")
check(_t.nic_pci("nosuch") is None,
      "an interface this box has not got resolved to something")

# The shape test itself, because it decides which directory is handed to DPDK.
for _good in ("0000:00:12.0", "0000:3b:00.0", "10000:ff:1f.7"):
    check(is_pci_addr(_good), f"{_good!r} is a PCI address and was rejected")
for _bad in ("virtio3", "soc", "0000:00:12", "0000:zz:12.0", "", "a:b:c.d"):
    check(not is_pci_addr(_bad),
          f"{_bad!r} is not a PCI address and was accepted, so it could be "
          f"handed to DPDK as a device")
# AND THE VTY MUST AGREE WITH THE TUNER. The tuner decides what goes into
# startup.conf; `show hardware` decides what the operator is offered. A box
# where the two disagree offers a card the tuner then refuses, or hides one it
# would have accepted - and this is a second copy of one truth, which is the
# defect this repository keeps finding. Compared here rather than trusted.
sys.path.insert(0, os.path.join(HERE, "..", "src", "hexash"))
import hexash as _hx     # noqa: E402

for _if in ("ens18", "eth9", "nosuch"):
    _mine = _t.nic_pci(_if)
    _theirs = _hx.pci_address_of(f"{_d}/sys/class/net/{_if}/device") or None
    check(_mine == _theirs,
          f"the tuner resolves {_if!r} to {_mine!r} and 'show hardware' to "
          f"{_theirs!r}: one of them offers a card the other will not take")
for _n in ("0000:00:12.0", "10000:ff:1f.7", "virtio3", "a:b:c.d", ""):
    check(is_pci_addr(_n) == _hx.is_pci_addr(_n),
          f"the two copies of the PCI shape test disagree about {_n!r}")
print("  the virtio parent, nothing that is not PCI, and the VTY agreeing")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
