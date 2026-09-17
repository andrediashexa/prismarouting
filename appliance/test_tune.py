#!/usr/bin/env python3
"""
Tests for hexarouting-tune against synthetic topologies.

The development machine has neither SMT nor multiple NUMA nodes, which is
exactly where the tuning decisions matter. So the topologies are built on
disk and checked.
"""
import os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TUNE = os.path.join(HERE, "hexarouting-tune")


def read_file(path):
    """A whole file, or "" - so a missing one fails a check and not the run."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""
fail = 0

def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")

def _dpdk_stanza(out):
    """
    The `dpdk { ... }` block of a rendered config, and nothing else.

    The whole config mentions MTU in the report comments on purpose, so a
    check that looked for "mtu" anywhere would be green whatever the stanza
    said - which is the needle-matches-a-label shape. The stanza is where a
    keyword gets invented, so the stanza is what is read.
    """
    keep, acc = False, []
    for line in out.splitlines():
        if line.startswith("dpdk {"):
            keep = True
        if keep:
            acc.append(line)
        if keep and line.startswith("}"):
            break
    return "\n".join(acc)


def w(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(str(content) + "\n")

def build(root, sockets, cores_per_socket, threads, nic_node=None,
          hugepages_gb=0, isolated="", nic_pci=None, ram_gb=64,
          nic_bus=None, hugepage_kb=1048576, tx_queues=None):
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
            # `nic_bus` puts a BUS device between the netdev and the PCI one,
            # which is the shape of every virtio guest: `device` points at
            # virtio4 and the PCI address is its parent. Without it this
            # fixture only ever built the bare-metal shape, where the basename
            # already is the address - so the whole generated stanza was only
            # ever exercised on the layout that happened to work (#286).
            target = dev if nic_bus is None else f"{dev}/{nic_bus}"
            if nic_bus is not None:
                os.makedirs(target, exist_ok=True)
            os.symlink(target, f"{root}/sys/class/net/eth0/device")
            # HOW MANY QUEUES THE CARD HAS, which the tuner did not read at
            # all until #330 and which is the difference between a virtio
            # guest and the 10G card this was developed on. The default is
            # none, so every fixture written before this one keeps the shape
            # it had: the tuner falls back to one queue per worker when the
            # card cannot be read.
            if tx_queues:
                for _q in range(tx_queues):
                    os.makedirs(f"{root}/sys/class/net/eth0/queues/tx-{_q}",
                                exist_ok=True)
                    os.makedirs(f"{root}/sys/class/net/eth0/queues/rx-{_q}",
                                exist_ok=True)
        else:
            w(f"{root}/sys/class/net/eth0/device/numa_node", nic_node)
    # The POOL THAT EXISTS, and only that one. A box whose CPU cannot do 1G
    # has no hugepages-1048576kB directory at all - asking for one on the
    # kernel command line is silently ignored - and the fixture has to be
    # shaped that way or it cannot build the box this was measured on (#290).
    for _kb in ((2048, 1048576) if hugepage_kb == "both" else (hugepage_kb,)):
        w(f"{root}/sys/kernel/mm/hugepages/hugepages-{_kb}kB/nr_hugepages",
          hugepages_gb * (1048576 // _kb))
    w(f"{root}/sys/devices/system/cpu/isolated", isolated)
    # Plenty of RAM unless a case says otherwise, so the existing cases are
    # unaffected by the cap below.
    w(f"{root}/proc/meminfo", f"MemTotal:       {ram_gb * 1024 * 1024} kB")
    return node_cpus

def run(root, *args, maxmtu=None, addr=None):
    """
    The tuner against a synthetic root.

    `maxmtu` installs a stub `ip` on PATH. The card's maximum MTU is a netlink
    attribute rather than a file under `root`, so without a seam the fixtures
    could not reach that half at all - and the first version of the #330 fix
    had a check that could not fail because of it.
    """
    env = dict(os.environ)
    # HEXA_IP is set on EVERY run, not only the ones that name a maximum.
    # Without it the tuner asked the machine's own `ip` about a card named in
    # a synthetic tree, and every fixture here that names an interface read
    # whatever that machine's eth0 answered: 47 such reads in one run,
    # measured. Nothing asserts on the difference today, so this was harmless
    # - but "harmless" is a property of the checks that exist, and the mtu
    # half of #330 is exactly the kind that gets one added.
    #
    # No maximum means a card that could not be read, which is a box whose
    # `ip` has nothing to say about the device - one of the cases below.
    #
    # TWO QUESTIONS NOW, and the stub has to tell them apart. Since #378 the
    # tuner also asks `ip -4 -o addr show dev <if>` for the address of a card
    # it is about to claim - the one reading that decides whether the last
    # card may go at all - and a stub that answered the link question to both
    # would have the address parser read a link line and find nothing, which
    # is a pass for the wrong reason.
    _bin = tempfile.mkdtemp()
    with open(os.path.join(_bin, "ip"), "w") as _f:
        # TWO QUESTIONS AND SEVERAL CARDS, and the stub has to tell them
        # apart.
        #
        # The tuner asks `ip -4 -o addr show dev <if>` for the address of a
        # card it is about to claim (#378) and `ip -d link show <if>` for
        # that card's maximum MTU (#382). A stub that answered the link
        # question to both would have the address parser read a link line and
        # find nothing, which is a pass for the wrong reason - so the address
        # arm is matched FIRST, because an addr command names an interface too.
        #
        # And PER INTERFACE, because the box can claim more than one card
        # while `ethernet { default-mtu }` is one value for all of them. A
        # stub answering the same maximum for every card could not build the
        # box that rule is about - a 1500 card beside a 9000 one - so the
        # rule would have no case that could fail. An int still means "every
        # card"; a list of addresses exercises "exactly one global address".
        _f.write("#!/bin/sh\ncase \"$*\" in\n")
        if addr is not None:
            _f.write("  *addr*)\n")
            for _a in ([addr] if isinstance(addr, str) else addr):
                _f.write(f"    echo '2: eth0    inet {_a} brd 10.0.0.255 "
                         f"scope global eth0\\       valid_lft forever'\n")
            _f.write("    exit 0 ;;\n")
        else:
            _f.write("  *addr*) exit 1 ;;\n")
        if isinstance(maxmtu, dict):
            for _if, _m in maxmtu.items():
                _f.write(f"  *'{_if}') echo '2: {_if}: <BROADCAST> mtu 1500 "
                         f"... minmtu 68 maxmtu {_m}'; exit 0 ;;\n")
            _f.write("  *) exit 1 ;;\n")
        elif maxmtu is None:
            _f.write("  *) exit 1 ;;\n")
        else:
            _f.write(f"  *) echo '2: eth0: <BROADCAST> mtu 1500 ... minmtu 68 "
                     f"maxmtu {maxmtu}'; exit 0 ;;\n")
        _f.write("esac\n")
    os.chmod(os.path.join(_bin, "ip"), 0o755)
    env["HEXA_IP"] = os.path.join(_bin, "ip")
    # THE RESERVE COMES OUT OF THE TREE, never off the machine running this.
    #
    # The tuner reads the management network the installer recorded and
    # reserves that card for Linux (#378). Its default is an absolute path, so
    # without this every fixture here would consult the build runner's own
    # /etc/network/interfaces.d/hexarouting - and a developer box that has one
    # would measure something the runner does not. That is HEXA_IP's argument
    # two paragraphs up, and the same trap: it is harmless only for as long as
    # nobody's machine has the file.
    #
    # A caller that wants a reserve writes one into the tree and passes its
    # own --mgmt-conf, which overrides this because argparse keeps the last.
    # And the file the tuner leaves the computed way back in, inside the tree
    # for the same reason (#378): its real path is /run, which a suite cannot
    # write and must not share between fixtures.
    env["HEXA_MGMT_PATH"] = os.path.join(root, "run/hexarouting/mgmt")
    mgmt = os.path.join(root, "etc/network/interfaces.d/hexarouting")
    p = subprocess.run([sys.executable, TUNE, "--sysfs-root", root,
                        "--mgmt-conf", mgmt] + list(args),
                       capture_output=True, text=True, env=env)
    return p.stdout


def add_nic(root, name, pci, tx_queues=None, node=0):
    """
    A second, third or fourth card on a fixture box.

    build() makes exactly one, called eth0, because until #378 the tuner could
    claim exactly one. A box that claims every card but a reserve cannot be
    described by that fixture at all - and the cases below are about which of
    several it takes, so a fixture that cannot hold several would let every
    one of them pass by having nothing to choose between.
    """
    dev = f"{root}/sys/devices/pci0000:00/{pci}"
    w(f"{dev}/numa_node", node)
    os.makedirs(f"{root}/sys/class/net/{name}", exist_ok=True)
    os.symlink(dev, f"{root}/sys/class/net/{name}/device")
    for _q in range(tx_queues or 0):
        os.makedirs(f"{root}/sys/class/net/{name}/queues/tx-{_q}", exist_ok=True)
        os.makedirs(f"{root}/sys/class/net/{name}/queues/rx-{_q}", exist_ok=True)


def reserve(root, ifname, how="dhcp"):
    """Write the management network the installer would have recorded."""
    d = f"{root}/etc/network/interfaces.d"
    os.makedirs(d, exist_ok=True)
    with open(f"{d}/hexarouting", "w") as f:
        f.write(f"auto {ifname}\niface {ifname} inet {how}\n")

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
          hugepages_gb=8, isolated="1-7,9-15", nic_pci="0000:3b:00.0")
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

print("\n=== the statistics segment is sized for the interfaces configured (#288) ===")
# THE DEFECT, measured twice at the same step (docs/bng.md 2.1): a box built by
# this tuner runs on VPP's built-in 32 MB statistics segment, and creating
# subscriber interfaces on it printed "after 7000: 7000 live" and then "VPP
# DIED while creating sessions 7000..8000", with _vec_realloc_internal under
# vlib_stats_create_counter on the stack. The limit of the one memory resource
# the tuner did not size is an ABORT and not a refusal: the whole dataplane,
# and nothing said about what ran out.
#
# So it is sized, and from the same kind of fact as the queue count beside it
# (#330): the interfaces this box is CONFIGURED for, read from the file that
# creates them, rather than inferred from a subscriber target that counts
# translations. The arithmetic is the measurement divided: 32 MiB over the
# 7,000 interfaces that segment was measured holding is 4,793 bytes each.

def interfaces_conf(root, n, extra=""):
    """A saved configuration naming `n` interfaces, as hexash writes them."""
    path = os.path.join(root, "hexarouting.conf")
    with open(path, "w") as f:
        f.write("! cgnat\n")
        for i in range(n):
            f.write(f"interface Ten0/0/0.{i}\n vlan {i % 4000 + 1}\n exit\n")
        f.write(extra)
    return path

with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_gb=6, isolated="1-7")
    # The routing half of the file names interfaces too, in FRR's own blocks,
    # and names ONES THAT ARE ALREADY ABOVE. A box carrying 20,000
    # sub-interfaces with OSPF on one of them is configured for 20,000
    # interfaces, not 20,001 - and a reader that counted `interface ` lines
    # would say 20,001 here and something further off on a real file.
    conf = interfaces_conf(root, 20000,
                           extra="! routing\ninterface Ten0/0/0.0\n"
                                 " ip ospf cost 10\n exit\n")
    out = run(root, "--interface", "eth0", "--config", conf)
    stanzas = out.split("### stanzas appended to /etc/vpp/startup.conf")[1]
    check("statseg" in stanzas,
          f"a box configured for 20,000 interfaces was left on VPP's 32 MB "
          f"default, which was measured aborting below 8,000. Generated: "
          f"{stanzas!r}")
    # THE ARITHMETIC, and it is the measurement rather than a constant
    # somebody liked: 20,000 x 4,793 B is 95,860,000 B, which is 91.4 MiB, so
    # 92M. Sized instead from the 2.9 kB of counter CONTENT measured at 40,000
    # interfaces it would be 56M - and that is the figure that predicts 12,000
    # interfaces on a segment that died below 8,000, because it leaves out the
    # contiguous directory that has to hold two copies while it grows.
    check("size 92M" in stanzas,
          f"20,000 interfaces at the measured 4,793 bytes each is 92M; the "
          f"stanza says: "
          f"{[l.strip() for l in stanzas.splitlines() if 'size' in l]}")
    # AND THE REPORT CARRIES THE SAME NUMBER. Two computations of one figure
    # is the shape #326 was: the page quoted a line the tuner had stopped
    # printing, and nothing compared them. Here the operator's screen and the
    # file VPP reads are the two copies.
    _in_stanza = re.search(r"^\s*size (\d+M)$", stanzas, re.M)
    _in_report = re.search(r"statistics segment: (\d+M)", out)
    check(bool(_in_report) and bool(_in_stanza)
          and _in_report.group(1) == _in_stanza.group(1),
          f"the report and the generated stanza must carry the same size, "
          f"and the report has "
          f"{_in_report.group(1) if _in_report else 'none'} where "
          f"startup.conf has "
          f"{_in_stanza.group(1) if _in_stanza else 'none'} - so the one "
          f"screen an operator reads is not about the box: "
          f"{[l for l in out.splitlines() if 'statistics' in l]}")
    check("20000" in out.split("###")[0],
          f"the report names no interface count, so a size appears in "
          f"startup.conf with nothing saying what it was sized for: "
          f"{[l for l in out.splitlines() if 'statistics' in l]}")
    # AND THE COUNT IS HEXASH'S OWN. The tuner loads the VTY's parser instead
    # of grepping the file, because a second reader of that grammar answers
    # the same until the VTY learns another way to name an interface, and the
    # box that finds out is the one with twenty thousand of them.
    sys.path.insert(0, os.path.join(HERE, "..", "src", "hexash"))
    import config as _cfg     # noqa: E402
    _theirs = len(_cfg.Config.parse(open(conf).read()).interface_names())
    check(f"the {_theirs} interface(s)" in out,
          f"the tuner and hexash do not agree about how many interfaces that "
          f"file configures: hexash says {_theirs} and the report says "
          f"{[l for l in out.splitlines() if 'interface(s)' in l]}")
    # AND docs/appliance.md QUOTES THIS RUN. The page prints the report and
    # the stanza under "A box configured for 20,000 sub-interfaces", and the
    # box that produces them is this one: one socket, eight cores, 64 GB. Two
    # of the three numbers in that block do not follow from the interface
    # count at all - the hugepage figure is worker_count driven, so the same
    # 200,000 subscribers ask for 6 GB here and 12 GB on a 32-core machine -
    # and a reader who runs the tuner on their own box and gets a different
    # number has nothing on the page to reconcile it with.
    #
    # Held against THIS run rather than re-derived, which is the #326 rule one
    # step further: that page quoted a tuner line the tuner had stopped
    # printing, and nothing compared them. Every line of the two quoted blocks
    # has to be a line this run produced.
    _doc = open(os.path.join(HERE, "..", "docs", "appliance.md"),
                encoding="utf-8").read()
    _blocks = re.findall(r"```\n(# 1G hugepages:.*?)```", _doc, re.S)
    check(len(_blocks) == 1,
          f"docs/appliance.md has {len(_blocks)} quoted tuner reports opening "
          f"with '# 1G hugepages:', so this check is measuring the wrong one "
          f"or none at all")
    if len(_blocks) == 1:
        # The one substitution, and it is not a loosening: the report names
        # the file it counted interfaces in, and on a real box that is
        # /etc/hexarouting/hexarouting.conf while here it is a temporary
        # directory. Comparing without it would report the page wrong about a
        # line that is right, which is a check going red on correct work.
        _said = out.replace(conf, "/etc/hexarouting/hexarouting.conf")
        _missing = [l for l in _blocks[0].splitlines()
                    if l.strip() and l not in _said.splitlines()]
        check(not _missing,
              f"docs/appliance.md quotes the tuner as printing lines this box "
              f"does not print, so the page shows an operator a screen no box "
              f"produces: {_missing}")
    # The box itself, named on the page, because the numbers depend on it.
    check("one socket, eight cores" in _doc,
          "docs/appliance.md quotes that report without saying which box it "
          "came from, and the hugepage figure in it is a function of the "
          "worker count rather than of the interface count the page names")
    print(f"  20,000 interfaces -> "
          f"{[l.strip() for l in stanzas.splitlines() if l.strip().startswith('size ')]}")

with tempfile.TemporaryDirectory() as root:
    # THE FIRST BOOT CARRIES IT TOO, and this is not symmetry for its own
    # sake: the hugepages and the pinning wait for a reboot because the kernel
    # has to grant them, and the statistics segment does not - hexarouting.service
    # hands the same saved configuration to `hexash --load` on the first boot,
    # so a box that aborts on its own configuration aborts on it that time.
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_gb=0, isolated="")
    conf = interfaces_conf(root, 20000)
    out = run(root, "--interface", "eth0", "--config", conf)
    stanzas = out.split("### stanzas appended to /etc/vpp/startup.conf")[1]
    check("FIRST BOOT MODE" in stanzas,
          "this fixture stopped being the first-boot branch, so the check "
          "below is about the other profile")
    check("size 92M" in stanzas,
          f"the conservative profile left the segment at 32 MB for a box "
          f"configured for 20,000 interfaces, and that box loads the same "
          f"configuration on its first boot: {stanzas!r}")
    print("  and on the first boot, where the same configuration is loaded")

with tempfile.TemporaryDirectory() as root:
    # THE BOX THIS PRODUCT ACTUALLY SHIPS TO, and the one rule 7 of this file
    # is about: a CGNAT box terminating subscribers on a handful of ports has
    # nothing to size for, and must not be told about a size it does not need.
    # 7,000 is not an arbitrary second case - it is the count that segment was
    # measured holding, so it is the last box on the default's own evidence.
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_gb=6, isolated="1-7")
    for _n in (3, 7000):
        conf = interfaces_conf(root, _n)
        out = run(root, "--interface", "eth0", "--config", conf)
        stanzas = out.split("### stanzas appended to /etc/vpp/startup.conf")[1]
        check("statseg" not in stanzas,
              f"a box configured for {_n} interface(s) was given a statseg "
              f"stanza. VPP's own default holds 7,000, so anything written "
              f"here is either the same number or a smaller segment than the "
              f"box already had: "
              f"{[l.strip() for l in stanzas.splitlines() if 'size' in l]}")
        check(f"configured for {_n}, in" in out,
              f"the report does not say what it counted, so a box on the "
              f"default cannot be told from a box nobody read the "
              f"configuration of: "
              f"{[l for l in out.splitlines() if 'statistics' in l]}")
    print("  and a box with a handful of ports is left on the default")

with tempfile.TemporaryDirectory() as root:
    # A BOX WITH NOTHING CONFIGURED YET, which is every box on the boot after
    # the installer. Every defect this file has carried was a sentence true of
    # one box and false of another, and "sized for 0 interfaces" would be one.
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_gb=6, isolated="1-7")
    out = run(root, "--interface", "eth0",
              "--config", os.path.join(root, "not-configured-yet.conf"))
    stanzas = out.split("### stanzas appended to /etc/vpp/startup.conf")[1]
    check("statseg" not in stanzas,
          f"a box with no saved configuration was given a statistics segment "
          f"sized for the interfaces it has not got: {stanzas!r}")
    check(not re.search(r"statistics segment: \d+M", out),
          f"a box with no interfaces to size for was told about a size it "
          f"does not need: {[l for l in out.splitlines() if 'statistics' in l]}")
    # And the standing advice, which is what that box can act on instead.
    check("32 MB" in out, f"it must name the default the box actually gets; "
                          f"output was {out!r}")
    check("7,000 and 8,000" in out,
          "a limit without the measured number behind it is a rumour")
    check("/etc/hexarouting/vpp-base.conf" in out,
          "saying there is a wall without saying where the stanza goes leaves "
          "the operator with nothing to do about it")
    print("  a box with nothing configured is told about the wall, not a size")

with tempfile.TemporaryDirectory() as root:
    # SOMEBODY ELSE'S STANZA WINS, ALWAYS. The base file is concatenated AHEAD
    # of these stanzas, so a generated one is the last word on every boot: a
    # box whose operator chose 1G would silently get whatever this program
    # computed, on every reboot, for as long as the box lived.
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_gb=6, isolated="1-7")
    conf = interfaces_conf(root, 20000)
    base = os.path.join(root, "vpp-base.conf")
    with open(base, "w") as f:
        f.write("unix {\n  nodaemon\n}\n\nstatseg {\n  size 1G\n}\n")
    out = run(root, "--interface", "eth0", "--config", conf, "--vpp-base", base)
    stanzas = out.split("### stanzas appended to /etc/vpp/startup.conf")[1]
    check("statseg" not in stanzas,
          f"the operator's own statseg stanza was overridden by a generated "
          f"one, which is the last word on every boot: {stanzas!r}")
    check("left to the stanza somebody already put in" in out,
          f"the report does not say the segment was left to the stanza in the "
          f"base file, so a box whose operator chose the size reads exactly "
          f"like a box this program sized: "
          f"{[l for l in out.splitlines() if 'statistics' in l]}")
    # THE SHIPPED BASE FILE IS NOT SOMEBODY'S DECISION. It explains where a
    # statseg goes in a comment block, and a box that read its own
    # documentation as a stanza would never size anything at all.
    with open(base, "w") as f:
        f.write("# statseg{} DOES belong here, and this is the only place\n"
                "#   statseg {\n#     size 256M\n#   }\n"
                "unix {\n  nodaemon\n}\n")
    out = run(root, "--interface", "eth0", "--config", conf, "--vpp-base", base)
    stanzas = out.split("### stanzas appended to /etc/vpp/startup.conf")[1]
    check("size 92M" in stanzas,
          f"the comment block the product ships in vpp-base.conf was read as "
          f"somebody having sized the segment, so this box keeps the 32 MB "
          f"that aborts at 7,000: {stanzas!r}")
    print("  an operator's stanza is left alone; the shipped comment is not one")

with tempfile.TemporaryDirectory() as root:
    # A CONFIGURATION THIS PROGRAM COULD NOT READ is the state #288 lives on
    # after this change: the box keeps the segment that aborts, and the only
    # thing standing between that and a dead dataplane is the sentence. Same
    # reason the unreadable card says so above (#330): "could not read it" and
    # "read it and it is fine" must not look the same on the one screen.
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_gb=6, isolated="1-7")
    conf = os.path.join(root, "hexarouting.conf")
    # Half-written by a box that lost power mid-save: real bytes, not UTF-8.
    with open(conf, "wb") as f:
        f.write(b"! cgnat\ninterface Ten0/0/0.1\n description \xff\xfe\n exit\n")
    out = run(root, "--interface", "eth0", "--config", conf)
    stanzas = out.split("### stanzas appended to /etc/vpp/startup.conf")[1]
    check("statseg" not in stanzas,
          f"a configuration that could not be read produced a segment size "
          f"anyway, so the number is not the box's: {stanzas!r}")
    # THE PATH AND THE WORDS TOGETHER. "could not be read" alone is already
    # printed two lines up by the card that could not be read (#330), so a
    # check for it passes on a box whose configuration was never opened -
    # which is the check that cannot fail, and the whole point of this one.
    check(f"{conf} could not be read" in out,
          f"the box could not read its own configuration, sized nothing on "
          f"the strength of it, and said nothing about it - which reads "
          f"exactly like a box that has nothing to size for: "
          f"{[l for l in out.splitlines() if 'segment' in l or conf in l]}")
    print("  and a configuration it could not read is said out loud")

with tempfile.TemporaryDirectory() as root:
    # AND A BOX THAT CANNOT REACH THE PARSER AT ALL, which is an install that
    # put /usr/lib/hexarouting somewhere this program cannot see it. The
    # configuration on that box is fine, so telling the operator it could not
    # be parsed sends them to edit the one thing that is right - and it is the
    # same mistake as reading a card badly and calling the card broken (#330).
    # Reached by running the tuner from a directory with neither hexash beside
    # it nor one installed, which is exactly what that box has.
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_gb=6, isolated="1-7")
    conf = interfaces_conf(root, 20000)
    _alone = os.path.join(root, "bin")
    os.makedirs(_alone, exist_ok=True)
    shutil.copy(TUNE, os.path.join(_alone, "hexarouting-tune"))
    # The one tuner run in this file that does not go through run(), so it
    # carries the netlink seam itself: without it the card named here is asked
    # about on the machine underneath, which is the last of the 48 such reads
    # this sweep found.
    _p = subprocess.run([sys.executable, os.path.join(_alone, "hexarouting-tune"),
                         "--sysfs-root", root, "--interface", "eth0",
                         "--config", conf], capture_output=True, text=True,
                        env=dict(os.environ, HEXA_IP="/nonexistent-ip"))
    _o = _p.stdout
    check("parser is not installed" in _o,
          f"the box could not reach hexash's parser and reported its "
          f"configuration as unreadable, which is the one file on it that is "
          f"fine: {[l for l in _o.splitlines() if 'segment' in l or 'parser' in l]}")
    check("statseg" not in _o.split("### stanzas appended to /etc/vpp/startup.conf")[1],
          f"a box that could not count its interfaces sized the segment "
          f"anyway: {_o.split('### stanzas')[1]!r}")
    print("  and so is an install this program cannot find the parser in")

print("\n=== first boot: kernel still without hugepages or isolcpus ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_gb=0, isolated="")
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
          hugepages_gb=6, isolated="1-7")
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
          hugepages_gb=6, isolated="")
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
          hugepages_gb=6, isolated="1", ram_gb=3)
    out = run(root, "--interface", "eth0", "--subscribers", "200000")
    asked = int(field(out, "# 1G hugepages:").split()[0])
    check(asked == 0,
          f"a 3 GB box must not be told to reserve {asked} GB of hugepages")
    check("WARNING" in out, "the operator must be told the box is undersized")
    print(f"  3 GB box, 200k subscribers -> {asked} GB requested, warned")

with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_gb=6, isolated="1-7", ram_gb=16)
    out = run(root, "--interface", "eth0", "--subscribers", "200000")
    asked = int(field(out, "# 1G hugepages:").split()[0])
    check(0 < asked <= 12, f"16 GB should allow a real pool, got {asked}")
    check("WARNING" not in out, "a box with room must not be warned")
    print(f"  16 GB box, 200k subscribers -> {asked} GB requested, no warning")

with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_gb=6, isolated="1-7", ram_gb=8)
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
          hugepages_gb=2, isolated="1-3", ram_gb=16)
    out = run(root, "--interface", "eth0", "--subscribers", "200000")
    heap = [l for l in out.splitlines() if "main-heap-size" in l]
    check(any("1G" in l for l in heap),
          f"a 2 GB pool must not be given a 2G heap: {heap}")
    print(f"  2 GB reserved ->{heap[0].split('main-heap-size')[1]}")

with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          hugepages_gb=8, isolated="1-3", ram_gb=32)
    out = run(root, "--interface", "eth0", "--subscribers", "200000")
    heap = [l for l in out.splitlines() if "main-heap-size" in l]
    check(any("2G" in l for l in heap),
          f"a large pool should still get the full heap: {heap}")
    print(f"  8 GB reserved ->{heap[0].split('main-heap-size')[1]}")

print("\n=== one RX queue per worker, and the NIC's PCI address ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_gb=6, isolated="1-7", nic_pci="0000:3b:00.0")
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
          hugepages_gb=6, isolated="1-3")
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
          hugepages_gb=6, isolated="1-7")
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
          hugepages_gb=6, isolated="1-7,9-15")
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


def apply_box():
    """
    A box for apply-tuning to run against, and the two commands that decide
    what it does to the machine.

    Both were unstubbed. `systemctl is-active vpp.service` was answered by
    whatever machine ran this suite, which is the three-way decision #336 is
    about being taken from the wrong box - and `hexarouting-bind-nic` was
    looked for on that machine's PATH and RUN when it was there. Measured
    before this existed: with the product installed, this section took a card
    off the machine running the tests five times in one run. That is the
    warning in test/test_apply_tuning.sh, which stubbed both, and this file is
    the other caller nobody swept.

    HEXA_ROOT for the same reason: without it apply-tuning reads the machine's
    own /etc/default/grub.d, /proc/cmdline and /sys, and writes the cpufreq
    governor of any box whose suite runs as root.
    """
    b = _tf.mkdtemp()
    r = os.path.join(b, "root")
    for d in ("proc", "etc/default/grub.d", "etc/hexarouting",
              "sys/kernel/mm/hugepages/hugepages-1048576kB"):
        _os.makedirs(os.path.join(r, d), exist_ok=True)
    with open(os.path.join(r, "proc/cmdline"), "w") as f:
        f.write("BOOT_IMAGE=/vmlinuz ro quiet\n")
    # Seven, which no machine has. It is what the "reserved 1G pages now"
    # line reads back, and the one number in the report that can only have
    # come from this tree - so the check below can tell a run that honoured
    # HEXA_ROOT from one that read the machine's own /sys.
    with open(os.path.join(
            r, "sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages"),
            "w") as f:
        f.write("7\n")
    # is-active answers no, which is the boot: the tuner has chosen the card
    # and VPP is ordered after this unit. The other two answers are
    # test/test_apply_tuning.sh's, which owns this script's decisions.
    with open(os.path.join(b, "systemctl"), "w") as f:
        f.write("#!/bin/sh\ncase \"$*\" in *is-active*) exit 3 ;; esac\nexit 0\n")
    with open(os.path.join(b, "hexarouting-bind-nic"), "w") as f:
        f.write(f"#!/bin/sh\necho bound >> {os.path.join(b, 'bindlog')}\n"
                "exit 0\n")
    for c in ("systemctl", "hexarouting-bind-nic"):
        _os.chmod(os.path.join(b, c), 0o755)
    return b, r


fakebin, fakeroot = apply_box()
# A slow fake update-grub: if it gets called, the test notices
with open(os.path.join(fakebin, "update-grub"), "w") as f:
    f.write("#!/bin/sh\necho CALLED-UPDATE-GRUB\nsleep 5\n")
_os.chmod(os.path.join(fakebin, "update-grub"), 0o755)
with open(os.path.join(fakebin, "hexarouting-tune"), "w") as f:
    f.write("#!/bin/sh\nexit 0\n")
_os.chmod(os.path.join(fakebin, "hexarouting-tune"), 0o755)

env = dict(_os.environ, PATH=fakebin + ":" + _os.environ["PATH"],
           HEXA_ROOT=fakeroot,
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
# THE BOX IT RAN AGAINST, asserted rather than assumed, because for the whole
# life of this section it was the machine running the suite. Seven reserved 1G
# pages is this fixture's number and no machine's, so a run that reads back
# anything else read somebody's real /sys - and a run that reads the real /sys
# is a run that reached the real PATH, where an appliance keeps a binder that
# takes its dataplane card off the kernel.
check("reserved 1G pages now: 7" in out,
      "apply-tuning did not run against the tree this file built, so it was "
      f"reading the machine underneath it: {out!r}")
# And the binder it found was this one. Measured before the stub existed: on a
# machine with the product installed, this file ran the real
# hexarouting-bind-nic five times in one run.
check(os.path.exists(os.path.join(fakebin, "bindlog")),
      "apply-tuning bound no card through this file's binder, so whichever "
      "one it did reach is not the one this test put there")
print("  normal system: behaviour preserved, governor reported")
_sh.rmtree(fakebin, ignore_errors=True)

# --- the worker cap has to survive the trip from tune.env to the tuner (#172) ---
#
# hexarouting-tune has accepted --max-workers since it was written. apply-tuning
# never passed it and tune.env had no variable for it, so the flag was reachable
# only by hand and every booted box ran uncapped. Testing the computation alone
# is what let that stand: the calculation was right and nothing called it.
print("\n=== the worker cap reaches the tuner from the environment ===")
fakebin, fakeroot = apply_box()
argvlog = os.path.join(fakebin, "argv")
with open(os.path.join(fakebin, "hexarouting-tune"), "w") as f:
    f.write(f'#!/bin/sh\necho "$@" >> {argvlog}\nexit 0\n')
_os.chmod(os.path.join(fakebin, "hexarouting-tune"), 0o755)

def apply_argv(**extra):
    open(argvlog, "w").close()
    env = dict(_os.environ, PATH=fakebin + ":" + _os.environ["PATH"],
               HEXA_ROOT=fakeroot,
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
          hugepages_gb=8, isolated="1-3")
    out = run(root, "--interface", "ens18")   # no such interface in the tree
    check("no-pci" in out,
          "an unresolvable NIC must produce 'no-pci', so nothing is claimed")
    check("dev default" not in out,
          "and must NOT leave DPDK probing for whatever it can bind")
    print("  " + (field(out, "# dataplane NIC PCI address(es):") or "(none)"))

print("\n=== no interface named at all: same answer ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          hugepages_gb=8, isolated="1-3")
    out = run(root)
    check("no-pci" in out, "an empty setting must claim nothing either")
    check("dev default" not in out, "and must not fall back to probing")

print("\n=== the interface carrying the default route is refused ===")
# Even when it is named on purpose. Handing VPP the management path does not
# degrade the box, it removes it: the operator loses SSH the instant the
# dataplane starts, and gets it back by walking to a console.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          hugepages_gb=8, isolated="1-3")
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

print("\n=== a box with ONE card may give it up, with the way back written "
      "down (#303) ===")
# The refusal above is right about every box with somewhere else to put the
# dataplane. On a box with one card its advice - name the other one - named a
# card that does not exist, and the box could never forward. It steps aside
# there, and only there, and only when the box has recorded the management
# path it is going to put back: `--keep-management`, which apply-tuning passes
# when the VTY has written HEXA_DATAPLANE_MGMT beside the card's name.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-3")
    os.makedirs(f"{root}/proc/net", exist_ok=True)
    with open(f"{root}/proc/net/route", "w") as f:
        f.write("Iface\tDestination\tGateway\tFlags\n")
        f.write("eth0\t00000000\t0102A8C0\t0003\n")
    # Without the record: refused, exactly as before. A hand-edited tune.env
    # naming the management card must not take the box off the network.
    out = run(root, "--interface", "eth0")
    check("no-pci" in out and "REFUSED" in out,
          "the only card was handed over with no record of how to get the box "
          "back, which is a box nobody can reach")
    # With it: claimed, and the journal says what happened and why.
    out = run(root, "--interface", "eth0", "--keep-management")
    check("no-pci" not in out,
          "a box with one card and a recorded management path still could not "
          "be given a dataplane, which is the dead end #303 is about")
    check("dev 0000:3b:00.0" in out,
          f"and the card was not named: "
          f"{field(out, '# dataplane NIC PCI address(es):')}")
    check("only card" in out and "linux_cp" in out,
          f"the journal does not say the box handed over its management card "
          f"or what keeps it reachable: {field(out, '# ')}")
    print("  " + field(out, "# "))

print("\n=== and a box with a SECOND card is refused even with the record ===")
# The record says the box knows how to come back on the pair. It does not say
# the box should: with a spare card there is one to name instead, and giving
# away the management card for nothing is the mistake #123 exists for.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-3")
    spare = f"{root}/sys/devices/pci0000:00/0000:3b:00.1"
    os.makedirs(spare, exist_ok=True)
    os.makedirs(f"{root}/sys/class/net/eth1", exist_ok=True)
    os.symlink(spare, f"{root}/sys/class/net/eth1/device")
    os.makedirs(f"{root}/proc/net", exist_ok=True)
    with open(f"{root}/proc/net/route", "w") as f:
        f.write("Iface\tDestination\tGateway\tFlags\n")
        f.write("eth0\t00000000\t0102A8C0\t0003\n")
    out = run(root, "--interface", "eth0", "--keep-management")
    check("no-pci" in out and "REFUSED" in out,
          "a box with a spare card gave away its management card anyway")
    print("  " + field(out, "# "))

print("\n=== the box claims every card it has, with nobody having typed (#378) ===")
# THE RULE, and the state it replaces: a fresh install came up with `no-pci`
# and could not forward until somebody named a card. Nothing here passes
# --interface, which is the whole assertion - an operator who types nothing
# gets a box that forwards.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-3")
    add_nic(root, "eth1", "0000:3b:00.1")
    add_nic(root, "eth2", "0000:3b:00.2")
    out = run(root)
    check("no-pci" not in out,
          "a box with three cards and nothing typed claimed none of them, "
          "which is the state #378 exists to end")
    # THE COUNT, not one address. Binding the first and leaving two on their
    # kernel drivers is a box that forwards on a third of its ports and reads
    # as healthy, because a device named in a file is not a device the
    # dataplane has (#283).
    devs = [l.split()[1] for l in out.splitlines() if l.strip().startswith("dev 0000:")]
    check(sorted(devs) == ["0000:3b:00.0", "0000:3b:00.1", "0000:3b:00.2"],
          f"the box did not claim all three of its cards: {devs}")
    check("dev default" in out,
          "the allowlist lost its defaults stanza")

print("\n=== except one reserved for Linux, which the installer already asked ===")
# NOT A NEW SETTING. The card is the management network the installer recorded
# at the console before the first boot, read back out of the file it wrote.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-3")
    add_nic(root, "eth1", "0000:3b:00.1")
    add_nic(root, "eth2", "0000:3b:00.2")
    reserve(root, "eth1")
    out = run(root)
    devs = [l.split()[1] for l in out.splitlines() if l.strip().startswith("dev 0000:")]
    check("0000:3b:00.1" not in devs,
          f"the card the box was installed to be managed on was claimed "
          f"anyway, so the box loses the network it was given: {devs}")
    check(sorted(devs) == ["0000:3b:00.0", "0000:3b:00.2"],
          f"the other two cards were not both claimed: {devs}")
    # AND IT SAYS SO. A box that silently ate a card is worse than one that
    # asked, and a box that silently SPARED one is a card an operator cannot
    # find the reason for.
    check("eth1" in out and "reserved for Linux" in out,
          f"the report does not say which card was reserved or why: "
          f"{[l for l in out.splitlines() if l.startswith('#')][:6]}")

print("\n=== and never the last card, whatever the installer answered ===")
# THE NARROWING. A reserve allowed to take the only card leaves the commonest
# box there is - a VM with one NIC - installed, correct, and unable to
# forward: the exact state this change exists to end, coming back through the
# reserve. So on a one-card box the dataplane gets the card.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-3")
    reserve(root, "eth0")
    out = run(root)
    check("no-pci" not in out and "dev 0000:3b:00.0" in out,
          f"a one-card box reserved its only card and claimed nothing, which "
          f"is the dead end #303 was filed for: "
          f"{field(out, '# dataplane NIC PCI address(es):')}")
    check("never takes the last card" in out,
          f"the box took the card it was told to reserve and did not say why: "
          f"{[l for l in out.splitlines() if l.startswith('#')][:6]}")

print("\n=== a reserve naming a card this box has not got is said, not obeyed ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-3")
    add_nic(root, "eth1", "0000:3b:00.1")
    reserve(root, "ens99")
    out = run(root)
    devs = [l.split()[1] for l in out.splitlines() if l.strip().startswith("dev 0000:")]
    check(sorted(devs) == ["0000:3b:00.0", "0000:3b:00.1"],
          f"a reserve naming a card that is not here cost the box a card: "
          f"{devs}")
    check("ens99" in out,
          f"the box reserved nothing and did not say that the card it was "
          f"told to reserve is missing: "
          f"{[l for l in out.splitlines() if l.startswith('#')][:6]}")

print("\n=== the override claims that card and no other ===")
# WHAT `dataplane interface <name>` IS FOR NOW. Not the ordinary way to set up
# a box - nobody has to type it - but the way to say "only this one" on a box
# with several cards where one carries subscribers.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-3")
    add_nic(root, "eth1", "0000:3b:00.1")
    add_nic(root, "eth2", "0000:3b:00.2")
    out = run(root, "--interface", "eth1")
    devs = [l.split()[1] for l in out.splitlines() if l.strip().startswith("dev 0000:")]
    check(devs == ["0000:3b:00.1"],
          f"naming one card did not stop the box claiming the others, so an "
          f"operator cannot keep a card for anything: {devs}")

print("\n=== every claimed card keeps its Linux name in the dataplane (#389) ===")
# WHY A NAME AT ALL. Without one VPP calls the port after its PCI address -
# GigabitEthernet0/13/0, 21 characters - and FRR 10.7.1 refuses any interface
# name over 16, so on an installed box no interface could leave `shutdown`:
# every commit of an `interface` block failed at the routing reload and rolled
# back. Every card gets a name here, and the name is the one Linux gave it,
# read in the same pass that decided to claim it. The reserved card has no
# stanza and so no name: it is Linux's, under that same name.
#
# READ PER BLOCK, not as "name eth1 somewhere in the file": a name in the
# wrong block is a card called after another card, which is worse than the
# PCI spelling it replaces.
def dev_blocks(out):
    """{pci: [the lines inside that card's dev block]} of the dpdk stanza."""
    blocks, pci, acc = {}, None, []
    for line in _dpdk_stanza(out).splitlines():
        w = line.split()
        # `dev default` is the defaults block, not a card.
        if w[:1] == ["dev"] and len(w) == 3 and w[2] == "{" \
                and w[1] != "default":
            pci, acc = w[1], []
        elif w[:1] == ["}"] and pci is not None:
            blocks[pci] = acc
            pci = None
        elif pci is not None:
            acc.append(line.strip())
    return blocks

with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-3")
    add_nic(root, "eth1", "0000:3b:00.1")
    add_nic(root, "eth2", "0000:3b:00.2")
    out = run(root)
    blocks = dev_blocks(out)
    for pci, name in (("0000:3b:00.0", "eth0"), ("0000:3b:00.1", "eth1"),
                      ("0000:3b:00.2", "eth2")):
        check(f"name {name}" in blocks.get(pci, []),
              f"the dev block for {pci} does not name the card {name}, so the "
              f"dataplane calls it after its address and FRR refuses it: "
              f"{blocks.get(pci)}")
    print("  " + "; ".join(f"{p} is {[l for l in b if l.startswith('name ')]}"
                           for p, b in sorted(blocks.items())))

# THE RESERVED CARD IS NAMED NOWHERE. It has no dev block, and a `name eth1`
# that turned up in another card's block would be the wrong-block defect
# above.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-3")
    add_nic(root, "eth1", "0000:3b:00.1")
    add_nic(root, "eth2", "0000:3b:00.2")
    reserve(root, "eth1")
    out = run(root)
    blocks = dev_blocks(out)
    check("name eth1" not in _dpdk_stanza(out),
          f"the card reserved for Linux is named in the dataplane's stanza: "
          f"{_dpdk_stanza(out)}")
    check(sorted(blocks) == ["0000:3b:00.0", "0000:3b:00.2"]
          and "name eth0" in blocks["0000:3b:00.0"]
          and "name eth2" in blocks["0000:3b:00.2"],
          f"the two claimed cards are not each named in their own block: "
          f"{blocks}")
    print("  eth1 reserved: named nowhere; eth0 and eth2 named in their blocks")

# The override names that card and no other, under that same name.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-3")
    add_nic(root, "eth1", "0000:3b:00.1")
    out = run(root, "--interface", "eth1")
    blocks = dev_blocks(out)
    check(list(blocks) == ["0000:3b:00.1"]
          and "name eth1" in blocks["0000:3b:00.1"],
          f"the named card is not the one card named in the stanza: {blocks}")

# And the virtio shape, which is every VM this product is first tried on: the
# name is the netdev's, whatever bus device sits between it and the address.
_nd = tempfile.mkdtemp()
build(_nd, 1, 4, 1, nic_node=0, hugepages_gb=8, isolated="1-3",
      nic_pci="0000:00:13.0", nic_bus="virtio4")
_nout = run(_nd)
check("name eth0" in dev_blocks(_nout).get("0000:00:13.0", []),
      f"a virtio card's block does not carry its Linux name: "
      f"{dev_blocks(_nout)}")
print("  and a virtio card is named eth0 in the block for 0000:00:13.0")

print("\n=== each card gets the queue count ITS card reports (#330, plural) ===")
# One number for the box is the -22 of #330 arrived at from the other side: a
# virtio card beside a 10G card cannot take the 10G card's queue count.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-7", ram_gb=8,
          tx_queues=1)
    add_nic(root, "eth1", "0000:3b:00.1", tx_queues=4)
    out = run(root)
    stanza = _dpdk_stanza(out)
    first = stanza.split("dev 0000:3b:00.0", 1)[1].split("}", 1)[0]
    second = stanza.split("dev 0000:3b:00.1", 1)[1].split("}", 1)[0]
    check("num-rx-queues 1" in first,
          f"the one-queue card was asked for more than it has, which is the "
          f"-22 that stops the device coming up: {first!r}")
    check("num-rx-queues 4" in second,
          f"the four-queue card was given the other card's count: {second!r}")

print("\n=== one MTU for the box, and the SMALLEST card decides it (#378+#382) ===")
# THE INTERACTION THAT ONLY EXISTS WITH BOTH CHANGES IN ONE TREE. #382 writes
# `ethernet { default-mtu <n> }`, which is VPP-wide because this VPP has no
# per-device MTU keyword at all (#380). #378 makes the box claim several
# cards. So a box with a 1500 card and a 9000 card has ONE number for both,
# and the smaller has to win: a card asked for more than it can carry has its
# device refused at PMD init and the port sits in `error` state forwarding
# nothing, which cannot be repaired at runtime.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-3")
    add_nic(root, "eth1", "0000:3b:00.1")
    out = run(root, maxmtu={"eth0": 1500, "eth1": 9216})
    check("default-mtu 1500" in out,
          f"the box took the larger card's maximum, so the 1500 card's device "
          f"is refused at PMD init and that port forwards nothing: "
          f"{[l for l in out.splitlines() if 'mtu' in l.lower()][:6]}")
    # AND IT SAYS WHAT THAT COSTS. An operator with a 9000-capable card needs
    # to know why it started at 1500, or the box looks broken to them.
    #
    # THE REPORT ONLY, and that is not tidiness. The generated `ethernet`
    # stanza carries its own comment explaining the same rule, in nearly the
    # same words - so a needle read from the whole output matches the stanza
    # and stays green however the REPORT is reworded. Measured: with the
    # report's sentence deleted outright this check went green, because
    # render_ethernet()'s comment still said "smallest maximum". That is
    # TESTING.md's fourth lie with two copies of the message instead of one.
    #
    # The report's rows start at column one; every row of a generated stanza
    # is indented.
    _report = " ".join(l for l in out.splitlines() if l.startswith("# ")
                       or l.startswith("#  "))
    check("smallest maximum" in _report and "raises that port" in _report,
          f"the report does not say the bigger card is starting below its own "
          f"maximum, or how to raise it: "
          f"{[l for l in out.splitlines() if l.startswith('#')][:10]}")

print("\n=== and a box whose cards can all do jumbo is left alone ===")
# The other direction, and it is the one that keeps this from being a
# regression on the hardware this product was built for.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-3")
    add_nic(root, "eth1", "0000:3b:00.1")
    out = run(root, maxmtu={"eth0": 9216, "eth1": 9216})
    check("ethernet {" not in out,
          f"a box whose cards can all carry VPP's default was given an "
          f"ethernet stanza anyway: "
          f"{[l for l in out.splitlines() if 'mtu' in l.lower()][:6]}")

print("\n=== a card whose maximum cannot be read does not lower the box's ===")
# Unread is not 1500, it is no information. Letting it vote would hand every
# box with one unreadable card the smallest value some other card happens to
# have - and the report has to name it, because a port that then refuses to
# start has that as its only explanation.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-3")
    add_nic(root, "eth1", "0000:3b:00.1")
    out = run(root, maxmtu={"eth0": 4000})      # eth1 cannot be read
    check("default-mtu 4000" in out,
          f"a card that could not be read changed the value the box chose: "
          f"{[l for l in out.splitlines() if 'mtu' in l.lower()][:6]}")
    check("NIC max MTU, eth1: could not be read" in out,
          f"the card whose maximum could not be read is not named, so a port "
          f"that refuses to start has no explanation on this screen: "
          f"{[l for l in out.splitlines() if l.startswith('#')][:10]}")

print("\n=== the way back is computed off the live card, at boot (#378) ===")
# THE PIECE THAT MAKES THE STORED RECORD UNNECESSARY. The address of a card
# being handed over can only be read in this window: after the bind the card
# is gone from Linux and its address with it, so the tuner is the only thing
# awake at the right moment. A record written at a commit is a fact the box
# remembered; this is a fact it can recompute, and one that cannot be stale.
def _routed(root, iface, gw_hex):
    os.makedirs(f"{root}/proc/net", exist_ok=True)
    with open(f"{root}/proc/net/route", "w") as f:
        f.write("Iface\tDestination\tGateway\tFlags\n")
        f.write(f"{iface}\t00000000\t{gw_hex}\t0003\n")

with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-3")
    with open(f"{root}/sys/class/net/eth0/address", "w") as f:
        f.write("52:54:00:AB:CD:EF\n")
    _routed(root, "eth0", "0102A8C0")     # 192.168.2.1, little-endian
    out = run(root, addr="192.168.2.50/24")
    check("dev 0000:3b:00.0" in out,
          "the only card was not claimed, so there is no handover to compute")
    check("192.168.2.50/24" in out and "192.168.2.1" in out,
          f"the box did not work out how to stay reachable without the card "
          f"it is about to claim: "
          f"{[l for l in out.splitlines() if l.startswith('#')][:8]}")
    # THE MAC, LOWERCASED, because it is matched against what the dataplane
    # reports for the interface it took and the two spellings must agree.
    check("52:54:00:ab:cd:ef" in out,
          f"the MAC is missing or not lowercased, so the card cannot be found "
          f"again on the dataplane's side: {out!r}"[:400])
    # AND THE GATEWAY DECODED, not copied. /proc/net/route holds a
    # little-endian hex word, so a reader that printed it raw would put
    # '0102A8C0' where an operator expects an address.
    check("0102A8C0" not in out,
          "the gateway was printed as the raw hex field rather than decoded")

print("\n=== and it is written where the binder and the VTY read it ===")
# DRIVEN AT THE FUNCTION and not through `--write`, and the reason is not
# convenience: the tuner's write block puts files in /etc/default/grub.d and
# /etc/vpp, so it cannot run unprivileged at all - a suite that tried would be
# asserting on a PermissionError. What is driven here is the holder of the
# record's SHAPE, which is what appliance/bind-dataplane-nic reads with `cat`
# and what hexash parses into a dict, so the thing those two have to agree on
# is exactly what this measures. test/test_bind_nic.sh reads the same four
# fields back from its own fixture, which is the other half.
#
# The same two globals the exec further down sets, and for the same reason:
# the module prefix resolves paths off __file__, so without them the import
# raises rather than the check failing.
_ns2 = {"__name__": "hexarouting_tune", "__file__": TUNE}
exec(compile(open(TUNE).read().split("\ndef plan(")[0], TUNE, "exec"), _ns2)
with tempfile.TemporaryDirectory() as root:
    _f = f"{root}/run/hexarouting/mgmt"
    _h = {"name": "eth0", "address": "192.168.2.50/24",
          "mac": "52:54:00:ab:cd:ef", "gateway": "192.168.2.1"}
    check(_ns2["write_management_path"](_h, path=_f) == _f,
          "the writer did not report where it put the record")
    _said = open(_f).read().split()
    check(_said == ["eth0", "192.168.2.50/24", "52:54:00:ab:cd:ef",
                    "192.168.2.1"],
          f"the file does not carry name, address, MAC and gateway in that "
          f"order, which is what the binder and the VTY both read: {_said!r}")

print("\n=== a card whose way back cannot be read is NOT handed over ===")
# An address the box cannot see is an address it cannot put back, and a
# handover with no way back is the walk to the console every refusal in this
# path exists to prevent. The tuner says which of the three was missing,
# because that is the only thing an operator can act on.
for _label, _addr, _mac, _gw in (
        ("no address", None, "52:54:00:AB:CD:EF", "0102A8C0"),
        ("no MAC", "192.168.2.50/24", None, "0102A8C0"),
        ("no gateway", "192.168.2.50/24", "52:54:00:AB:CD:EF", "00000000")):
    with tempfile.TemporaryDirectory() as root:
        build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
              nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-3")
        if _mac:
            with open(f"{root}/sys/class/net/eth0/address", "w") as f:
                f.write(_mac + "\n")
        _routed(root, "eth0", _gw)
        out = run(root, addr=_addr)
        check("could not read a way back" in " ".join(out.split()),
              f"{_label}: the box handed over its only card without saying it "
              f"has no way back: "
              f"{[l for l in out.splitlines() if l.startswith('#')][:8]}")
        check("dev 0000:3b:00.0" not in out,
              f"{_label}: the card was still claimed with no way back to put "
              f"on the pair, so the box comes up forwarding and unreachable")

print("\n=== a card with two addresses is one the box will not carry back ===")
# THE REFUSAL IS THE POINT, not the arithmetic. A card with several global
# addresses is a card whose management address nothing here can choose
# between, and choosing wrong means the box comes back on an address nobody is
# trying to reach - which is indistinguishable, from outside, from a box that
# never came back. The same rule the VTY's own reader applies.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-3")
    with open(f"{root}/sys/class/net/eth0/address", "w") as f:
        f.write("52:54:00:AB:CD:EF\n")
    _routed(root, "eth0", "0102A8C0")
    out = run(root, addr=["192.168.2.50/24", "10.9.9.9/32"])
    check("could not read a way back" in " ".join(out.split()),
          f"a card with two global addresses was carried back on one of them, "
          f"chosen by position: "
          f"{[l for l in out.splitlines() if l.startswith('#')][:8]}")
    check("dev 0000:3b:00.0" not in out,
          "and the card was claimed anyway, so the box hands over the port it "
          "is reached on and guesses which address to put back")

print("\n=== a stale way back is removed, not left for the next boot ===")
# THE WHOLE ARGUMENT FOR /run. A file describing a handover THIS boot is not
# making would be read by the binder as permission to take a card whose
# address it cannot restore. It is rewritten or removed every boot.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-3")
    add_nic(root, "eth1", "0000:3b:00.1")
    reserve(root, "eth0")                  # eth0 stays with Linux now
    _routed(root, "eth0", "0102A8C0")
    _f = f"{root}/run/hexarouting/mgmt"
    os.makedirs(os.path.dirname(_f), exist_ok=True)
    with open(_f, "w") as f:
        f.write("eth0 192.168.2.50/24 52:54:00:ab:cd:ef 192.168.2.1\n")
    # The boot decides there is no handover - eth0 is reserved and eth1 is
    # what gets claimed - and the record from the earlier boot must go.
    out = run(root, addr="192.168.2.50/24")
    check("192.168.2.50" not in out,
          f"the tuner reported a handover on a boot that reserves the card "
          f"carrying the default route: "
          f"{[l for l in out.splitlines() if l.startswith('#')][:8]}")
    _ns2["write_management_path"](None, path=_f)
    check(not os.path.exists(_f),
          "a way back from an earlier boot survived one that hands over "
          "nothing, so the binder could take a card on stale permission")


print("\n=== the documents do not instruct a step the box no longer needs ===")
# A HOLDER AND A READER, which is the only shape that keeps a claim in a
# document true. The holder is the TUNER, driven here on a box with a card and
# the setting shipped empty; the reader is docs/appliance.md, which told an
# operator to name the card from the VTY after installing. That instruction
# was right for as long as the box claimed nothing on its own, and a release
# that ships a document saying the opposite of the product is the defect this
# repository is organised against.
#
# THE PRODUCT SIDE IS DRIVEN, and that is what makes this worth having rather
# than a grep. If somebody reverts the claiming rule, the tuner stops claiming,
# this check stops demanding anything of the document, and the pair stays
# consistent without either being edited to match the other.
#
# WHAT IT STILL MISSES, said rather than left to be discovered: the document
# side is a needle, so an instruction reworded into different words passes.
# There is no sound way to read "tells the operator to name the card" out of
# prose, and a list of phrasings here would be the second copy this kind of
# check exists to forbid. It catches the sentence that is there today and the
# obvious restatements of it, and nothing else.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-3")
    _claims = "no-pci" not in run(root)
_shipped = read_file(os.path.join(HERE, "etc/tune.env"))
_empty = any(l.strip() == "HEXA_DATAPLANE_IF=" for l in _shipped.splitlines())
check(_claims and _empty,
      f"the tuner no longer claims a card on a box that has one with the "
      f"setting shipped empty (claims={_claims}, ships empty={_empty}), so "
      f"this check is holding the documents to nothing")
_DOC = os.path.join(HERE, "../docs/appliance.md")
_doc = " ".join(read_file(_DOC).split())
for _bad in ("name the dataplane NIC from the VTY",
             "After installing, name the dataplane"):
    check(_bad not in _doc,
          f"docs/appliance.md still tells an operator to name the card after "
          f"installing - {_bad!r} - and the box claims one by itself")
# AND THE OVERRIDE IS STILL DOCUMENTED, which is the other half: removing the
# instruction must not remove the statement. A box with several cards where
# one carries subscribers needs it, and a document that never mentions it
# leaves that operator with no way to choose.
check("dataplane interface <name>" in _doc,
      "docs/appliance.md no longer mentions 'dataplane interface <name>' at "
      "all, so an operator with several cards has no documented way to "
      "choose which one the dataplane gets")

print("\n=== a NIC that is not the default route is still claimed ===")
# The refusal must not become "never claim anything", which would be the same
# defect pointing the other way: a box that cannot forward is also a box that
# does not work.
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          nic_pci="0000:3b:00.0", hugepages_gb=8, isolated="1-3")
    os.makedirs(f"{root}/proc/net", exist_ok=True)
    with open(f"{root}/proc/net/route", "w") as f:
        f.write("Iface\tDestination\tGateway\tFlags\n")
        f.write("ens19\t00000000\t0102A8C0\t0003\n")
    out = run(root, "--interface", "eth0")
    check("no-pci" not in out,
          "a data NIC that is not the management path must still be claimed")
    check("dev 0000:" in out,
          f"and named by PCI address: {field(out, '# dataplane NIC PCI address(es):')}")


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
# `__file__` too: the tuner resolves hexash's configuration parser relative to
# its own path, and a namespace without one is not standing in for the module.
_ns = {"__name__": "hexarouting_tune", "__file__": TUNE}
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


print("\n=== a virtio box gets a dpdk stanza naming its card (#286) ===")
# END TO END, and not just nic_pci(). The unit check asserts the address is
# resolved; this asserts the GENERATED STANZA names it, which is the thing VPP
# reads. Those are different failures: a resolver that works and a stanza that
# still says no-pci would leave the box exactly as broken.
_vd = tempfile.mkdtemp()
build(_vd, 1, 4, 1, nic_node=0, hugepages_gb=8, isolated="1-3",
      nic_pci="0000:00:13.0", nic_bus="virtio4")
_vout = run(_vd, "--interface", "eth0", "--subscribers", "1000")
check("dev 0000:00:13.0" in _vout,
      f"a virtio card produced no device line, so VPP is handed nothing and "
      f"the box forwards nothing with the interface named correctly: "
      f"{_vout[_vout.find('dpdk'):][:120]!r}")
check("no-pci" not in _vout,
      f"the stanza says no-pci for a card with a PCI address: "
      f"{_vout[_vout.find('dpdk'):][:120]!r}")

# And the bare-metal shape still works, because the walk must not depend on
# there being a bus device in the way.
_bd = tempfile.mkdtemp()
build(_bd, 1, 4, 1, nic_node=0, hugepages_gb=8, isolated="1-3",
      nic_pci="0000:3b:00.0")
_bout = run(_bd, "--interface", "eth0", "--subscribers", "1000")
check("dev 0000:3b:00.0" in _bout,
      f"a card whose device link points straight at the PCI device stopped "
      f"working: {_bout[_bout.find('dpdk'):][:120]!r}")
print("  the stanza names the card on both shapes")


print("\n=== a box with no 1G pool is tuned on 2M, not left in first boot (#290) ===")
# Measured on a customer VM: the CPU does not advertise pdpe1gb, so the kernel
# has no hugepages-1048576kB pool, ignored `hugepagesz=1G hugepages=5` on the
# command line, and the tuner read 0 reserved on every boot. `tuned_now` was
# never true, DPDK stayed disabled, and the box told the operator to reboot -
# for the life of the box. It already had 2 GB of 2M pages reserved by VPP's
# own shipped sysctl, sitting unused.
#
# 2M is not a degraded mode. VPP runs on it; 1G is a preference about TLB
# entries.
_h2 = tempfile.mkdtemp()
build(_h2, 1, 8, 1, nic_node=0, hugepages_gb=2, isolated="1-7",
      nic_pci="0000:00:12.0", nic_bus="virtio3", ram_gb=8,
      hugepage_kb=2048)
_o2 = run(_h2, "--interface", "eth0", "--subscribers", "200000")
check("default_hugepagesz=2M" in _o2 and "hugepagesz=2M" in _o2,
      f"a box with no 1G pool was still asked for 1G pages, which the kernel "
      f"ignores: {[l for l in _o2.splitlines() if 'hugepagesz' in l]}")
check("FIRST BOOT" not in _o2,
      f"a box with hugepages reserved and cores isolated was left in the "
      f"first-boot profile, which is where it stays for ever: "
      f"{[l for l in _o2.splitlines() if 'state:' in l]}")
check("plugin dpdk_plugin.so { disable }" not in _o2,
      "DPDK was left disabled on a box that has hugepages")
check("2M hugepages:" in _o2,
      f"the report says 1G on a box getting 2M pages: "
      f"{[l for l in _o2.splitlines() if 'hugepages:' in l]}")

# And a box that CAN do 1G still gets 1G, because it is the better page.
_h1 = tempfile.mkdtemp()
build(_h1, 1, 8, 1, nic_node=0, hugepages_gb=2, isolated="1-7",
      nic_pci="0000:3b:00.0", ram_gb=64)
_o1 = run(_h1, "--interface", "eth0", "--subscribers", "200000")
check("default_hugepagesz=1G" in _o1,
      f"a box that can do 1G was given 2M pages: "
      f"{[l for l in _o1.splitlines() if 'hugepagesz' in l]}")
check("1G hugepages:" in _o1,
      "the report does not say 1G on a box getting 1G pages")

# A box with BOTH pools, which is what ordinary bare metal has. Without this
# the preference is unasserted: a box with one pool gets that one whatever
# order the sizes are tried in, so reversing the order changed nothing and the
# mutation stayed green.
_hb = tempfile.mkdtemp()
build(_hb, 1, 8, 1, nic_node=0, hugepages_gb=2, isolated="1-7",
      nic_pci="0000:3b:00.0", ram_gb=64, hugepage_kb="both")
_ob = run(_hb, "--interface", "eth0", "--subscribers", "200000")
check("default_hugepagesz=1G" in _ob,
      f"a box that has both pools was given 2M pages, and 1G is one TLB entry "
      f"where 2M is five hundred and twelve: "
      f"{[l for l in _ob.splitlines() if 'hugepagesz' in l]}")

# And the COUNT, which is what the kernel actually reserves. Asking for 6 on a
# 2M box reserves 12 MB, not 6 GB, and the box comes up with almost no
# hugepage memory while every other number says it has six gigabytes of it.
import re as _re                                                  # noqa: E402
_m = _re.search(r"default_hugepagesz=2M hugepagesz=2M hugepages=(\d+)", _o2)
check(_m is not None and int(_m.group(1)) == 6 * 512,
      f"a 2M box asked the kernel for {_m.group(1) if _m else '?'} pages, and "
      f"6 GB of 2M pages is {6 * 512}: the kernel reserves what was asked for "
      f"and every other number in the profile assumes the GB figure")

# A box with NEITHER pool is a box with no hugepages, and must stay
# conservative rather than claim a size it has not got.
_h0 = tempfile.mkdtemp()
build(_h0, 1, 8, 1, nic_node=0, hugepages_gb=0, isolated="",
      nic_pci="0000:3b:00.0", ram_gb=64)
_o0 = run(_h0, "--interface", "eth0", "--subscribers", "200000")
check("FIRST BOOT" in _o0,
      f"a box with no hugepages reserved and nothing isolated left the "
      f"conservative profile: {[l for l in _o0.splitlines() if 'state:' in l]}")
print("  2M where 1G cannot exist, 1G where it can, conservative where neither")

print("\n=== the report docs/appliance.md quotes is the report the box prints ===")
# THE DEFECT (#326). The page quoted the tuner as printing
# `1G hugepages: 6 requested`. It prints `6 GB requested`, and on a machine
# with no 1 GB pool the line begins `2M hugepages:`. The commit that rewrote
# that line landed three hours after the one that quoted it correctly and did
# not touch the page.
#
# Nothing could compare the two: the report was printed inline in main(), so
# there was no way to render it without running the whole tuner. It is
# render_report() now, beside render_grub(), render_vpp() and
# render_irqbalance(), and this drives the tuner for real and reads its output.
#
# THE FIXED TEXT, not the numbers. The page's box is not this fixture's box, so
# the figures differ and should. What must agree is the wording between them -
# that is what a reader compares their own console against, and that is what
# changed underneath the page.
_rep = tempfile.mkdtemp()
build(_rep, 1, 8, 1, nic_node=0, hugepages_gb=2, isolated="1-7",
      nic_pci="0000:3b:00.0", ram_gb=64)
_repout = run(_rep, "--interface", "eth0", "--subscribers", "200000")
_printed = [l for l in _repout.splitlines() if "hugepages:" in l]
_doc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "docs", "appliance.md"), encoding="utf-8").read()
_quoted = [l.strip() for l in _doc.splitlines() if "hugepages:" in l
           and l.lstrip().startswith("#")]
check(bool(_printed), f"the tuner printed no hugepages line: {_repout[:200]!r}")
check(bool(_quoted), "docs/appliance.md quotes no hugepages line any more, so "
                     "this check is comparing nothing")
if _printed and _quoted:
    # The wording either side of the numbers.
    _fixed = re.sub(r"\d+", "", _printed[0])
    _badly = [q for q in _quoted if re.sub(r"\d+", "", q) != _fixed]
    check(not _badly,
          f"docs/appliance.md quotes the tuner as printing\n    {_badly}\n"
          f"and the tuner prints\n    ['{_printed[0]}']\n"
          f"(numbers ignored: the page's box is not this fixture's box, but "
          f"the wording is what a reader compares their console against)")
    if not _badly:
        print(f"  the page and the box agree on: {_fixed.strip()}")

print("\n=== the stanza matches the card, not the processor (#330) ===")
# THE DEFECT, measured on the maintainer's installed box after a handover that
# worked. VPP owned the NIC and the device would not start:
#
#   dpdk: [0] Configured number of TX queues (7) is bigger than maximum
#         supported (1)
#   dpdk: [0] rte_eth_dev_set_mtu failed (mtu 8996, Unknown error -22)
#   dpdk: Interface GigabitEthernet0/12/0 error -22
#
# `nq` was one queue per WORKER, which is a fact about the processor, and the
# card was never asked. Every other figure in that stanza is measured. On a
# virtio guest - how most smaller deployments run - the card has one queue and
# a maxmtu of 1500, and both numbers were wrong at once.
_vq = tempfile.mkdtemp()
build(_vq, 1, 8, 1, nic_node=0, hugepages_gb=4, isolated="1-7",
      nic_pci="0000:00:12.0", nic_bus="virtio3", ram_gb=8, tx_queues=1)
_vout = run(_vq, "--interface", "eth0", "--subscribers", "200000",
            maxmtu=1500)

check("num-tx-queues 1" in _vout,
      f"a one-queue card was asked for more than one transmit queue, which is "
      f"the -22 the box actually got: "
      f"{[l.strip() for l in _vout.splitlines() if 'queues' in l]}")
check("num-rx-queues 7" not in _vout,
      f"the receive side still asks for one per worker: "
      f"{[l.strip() for l in _vout.splitlines() if 'rx-queues' in l]}")

# AND THE OPERATOR IS TOLD, because one queue and seven workers means one
# worker receives packets. Capping quietly would trade a box that does not
# start for a box that runs at a seventh of its CPU with nothing said.
check("NIC queues, eth0: 1" in _vout and "Capped" in _vout,
      f"the cap was applied silently: "
      f"{[l for l in _vout.splitlines() if 'NIC queues' in l]}")

# AND THE MTU IS REPORTED AND NOT WRITTEN, which is the opposite of what #330
# did. It put `mtu 1500` inside the dev stanza; the keyword does not exist,
# VPP refuses the whole file and exits, and vpp.service restarts it until
# systemd gives up. That shipped in v0.75.0 and stopped the dataplane on every
# box with a card below 9000.
#
# TWO CHECKS, because the defect had two halves and only one of them is about
# the words. The stanza must not carry the keyword at all, and the operator
# must still be told what their card is - a tuner that silently stopped
# mentioning it would be the same information loss with no red light.
check("mtu" not in _dpdk_stanza(_vout),
      f"the dev stanza carries an mtu keyword, which VPP has not got: it "
      f"refuses the file and the box never starts: "
      f"{[l for l in _dpdk_stanza(_vout).splitlines() if 'mtu' in l]}")
check("NIC max MTU, eth0: 1500" in _vout,
      f"a card with maxmtu 1500 is not mentioned in the report at all, so "
      f"an operator has no way to connect VPP's MTU complaint to their "
      f"card. It was: "
      f"error the box printed: "
      f"{[l.strip() for l in _vout.splitlines() if 'mtu' in l.lower()]}")
check("NIC max MTU, eth0: 1500" in _vout,
      f"the MTU the card forced was not reported: "
      f"{[l for l in _vout.splitlines() if 'MTU' in l]}")

# AND IT IS WRITTEN, which is what #382 is. Reporting it was the whole of what
# this tuner did about the card's MTU until then, on the reading that VPP's
# `rte_eth_dev_set_mtu failed` was a complaint about a device that still came
# up. Measured on a booted box: the device does not come up - the port sits in
# `error` state with `flags: pmd-init-fail` - and no runtime call recovers it.
#
# THE VALUE, not the presence of a stanza. The number is the card's, read off
# the card, and a stanza carrying VPP's own 9000 would leave the box exactly
# where it was while looking like a fix.
check("ethernet {" in _vout and "default-mtu 1500" in _vout,
      f"a card whose maximum is 1500 was left to start at VPP's 9000, which "
      f"is the driver refusing the port and the box forwarding nothing: "
      f"{[l.strip() for l in _vout.splitlines() if 'mtu' in l.lower()]}")
# IN THE dpdk STANZA IT WOULD BE #380 AGAIN, so the two are asserted apart:
# the keyword is legal in `ethernet` and is `unknown input` in `dpdk`, and a
# stanza is not a place-independent thing.
check("default-mtu" not in _dpdk_stanza(_vout),
      f"the default-mtu landed in the dpdk stanza, where VPP answers "
      f"'unknown input' and refuses the whole file: "
      f"{_dpdk_stanza(_vout)!r}")

print("\n=== the first-boot profile says the same thing about the card ===")
# Both profiles or neither. A box boots conservative until the kernel has
# reserved hugepages, and a card that cannot do 9000 cannot do 9000 on that
# boot either - two files that disagree about the hardware is the shape this
# repository keeps removing.
_fb = tempfile.mkdtemp()
build(_fb, 1, 8, 1, nic_node=0, hugepages_gb=0, isolated="", ram_gb=8,
      nic_pci="0000:00:12.0", nic_bus="virtio3", tx_queues=1)
_fbout = run(_fb, "--interface", "eth0", "--subscribers", "200000",
             maxmtu=1500)
check("FIRST BOOT MODE" in _fbout,
      f"the fixture is not in first-boot mode, so this is measuring the "
      f"other profile: {_fbout[:200]!r}")
check("default-mtu 1500" in _fbout,
      f"the first-boot profile leaves interfaces at 9000 on a card that "
      f"cannot carry it: "
      f"{[l.strip() for l in _fbout.splitlines() if 'mtu' in l.lower()]}")

print("\n=== and a card that can do jumbo is left alone ===")
# The other direction, and it is the one that keeps this from being a
# regression on the hardware this product was built for. A 10G card with eight
# queues gets exactly what it got before: one queue per worker and no mtu line
# at all, because VPP's own default is right there.
_jq = tempfile.mkdtemp()
build(_jq, 1, 8, 1, nic_node=0, hugepages_gb=4, isolated="1-7",
      nic_pci="0000:3b:00.0", ram_gb=64, tx_queues=8)
_jout = run(_jq, "--interface", "eth0", "--subscribers", "200000",
            maxmtu=9216)
check("num-tx-queues 7" in _jout,
      f"a card with eight queues was capped below its worker count: "
      f"{[l.strip() for l in _jout.splitlines() if 'queues' in l]}")
check("Capped" not in _jout,
      f"a card that needed no cap was reported as capped: "
      f"{[l for l in _jout.splitlines() if 'NIC queues' in l]}")
check("mtu " not in _jout.split("dpdk {", 1)[-1].split("}", 1)[0],
      f"a jumbo-capable card was given an mtu line, which changes what every "
      f"physical box has always done: "
      f"{_jout.split('dpdk {', 1)[-1].split('}', 1)[0]!r}")
# AND NO ethernet STANZA EITHER. #382 is about a card that cannot do 9000; a
# card that can keeps VPP's own default, and writing 9216 here would change
# what every physical box has always started at for no reason anybody asked
# for.
check("default-mtu" not in _jout,
      f"a card whose maximum is above VPP's default was given a default-mtu "
      f"anyway: {[l.strip() for l in _jout.splitlines() if 'mtu' in l.lower()]}")

print("\n=== and the check is reading a card at all ===")
# Without this the two sections above pass on a fixture whose queue directory
# was never created, which is the shape #151 spent an issue on.
_probe = tempfile.mkdtemp()
build(_probe, 1, 8, 1, nic_node=0, hugepages_gb=4, isolated="1-7",
      nic_pci="0000:00:12.0", ram_gb=8, tx_queues=2)
_pout = run(_probe, "--interface", "eth0", "--subscribers", "200000")
check("NIC queues, eth0: 2" in _pout,
      f"the tuner read no queue count from a fixture that has two, so these "
      f"checks are asserting about a card that was never built: "
      f"{[l for l in _pout.splitlines() if 'NIC' in l]}")

print("\n=== a card it could not read is said out loud, not assumed fine ===")
# The state #330 can still happen in. With no queues/ directory the tuner
# falls back to one queue per worker, which is exactly the number that made
# DPDK refuse the user's virtio card. If the report stays quiet about that,
# "could not read the card" and "read it and it is fine" look identical on
# the one screen an operator has, and the next person debugs the wrong thing.
_unread = tempfile.mkdtemp()
build(_unread, 1, 8, 1, nic_node=0, hugepages_gb=4, isolated="1-7",
      nic_pci="0000:00:12.0", nic_bus="virtio3", ram_gb=8)
_uout = run(_unread, "--interface", "eth0", "--subscribers", "200000")
check("num-tx-queues 7" in _uout,
      f"the unreadable-card fixture no longer falls back to one queue per "
      f"worker, so this check is not about the state it was written for: "
      f"{[l for l in _uout.splitlines() if 'queues' in l]}")
check("could not be read" in _uout,
      f"the tuner asked for 7 queues because it could not read the card and "
      f"said nothing about it, so a box that is about to refuse the device "
      f"looks the same as a healthy one: "
      f"{[l for l in _uout.splitlines() if 'NIC' in l]}")

print("\n=== and a box with no dataplane card says nothing about queues ===")
# The other half of the same question. Every defect this file has carried was
# a sentence true of one box and false of another, printed by a path that
# never asked which box it was on: a queue count is not a fact about a machine
# that has no card to count queues on.
_nonic = tempfile.mkdtemp()
build(_nonic, 1, 8, 1, hugepages_gb=4, isolated="1-7", ram_gb=8)
_nout = run(_nonic, "--subscribers", "200000")
check("NIC queues" not in _nout,
      f"a box with no dataplane interface was told something about its NIC "
      f"queues: {[l for l in _nout.splitlines() if 'NIC' in l]}")

# AND IT IS NOT TOLD ITS CARD IS NOT A PCI DEVICE (#331). None covered two
# states that are nothing alike - no card named, and a named card whose
# address could not be read - and the sentence written for the second was
# printed for the first, which is every fresh install. Two lines above it the
# same report says no NIC was given, so the box contradicted itself on one
# screen, and the reasonable reading sent an operator looking for why their
# card was not PCI: the #286 dead end again.
check("not a PCI device" not in _nout,
      f"a box with no dataplane card was told its card is not a PCI device: "
      f"{[l for l in _nout.splitlines() if 'PCI' in l]}")
check(any("no card the dataplane could claim" in l
          for l in _nout.splitlines()),
      f"and it is not told why there is no address either: "
      f"{[l for l in _nout.splitlines() if 'PCI' in l]}")

# The state the old sentence WAS written for, which must keep its answer: a
# card was named and the walk up to its PCI parent found nothing. Deleting the
# sentence for the box it was wrong on would take it from the box it was right
# on, and that box is the one that cannot forward.
_nopci = tempfile.mkdtemp()
build(_nopci, 1, 8, 1, hugepages_gb=4, isolated="1-7", ram_gb=8)
_ppout = run(_nopci, "--interface", "eth0", "--subscribers", "200000")
check("was named and no PCI address could be read" in _ppout,
      f"a named card with no PCI address is no longer reported, so a box that "
      f"cannot give DPDK a device is not told why: "
      f"{[l for l in _ppout.splitlines() if 'PCI' in l]}")

print("\n=== the cores table an operator sizes hardware from is the tuner's ===")
# A TRUTH WRITTEN IN TWO PLACES, and until now only one of them was ever
# checked. The page an operator reads before buying a processor carries a
# table of how many cores forward and how many are left for everything else,
# and that is a claim about what THIS program does. Nothing compared them, so
# the day the split changes the page goes on selling the old one - and it is
# read by somebody choosing hardware, who finds out after the box is racked.
#
# The tuner is the holder and the page is the reader, which is the only way
# round that works: the page cannot be the holder because the box does not
# read it. Driven here rather than by a documentation suite because the answer
# has to come from RUNNING the tuner on a machine of that shape; a check that
# compared the table against the source code that computes it would be
# agreeing with an expression rather than with an outcome.
_WIKI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                     "wiki", "comecar-aqui", "dimensionamento-e-requisitos.md")
try:
    _page = open(_WIKI, encoding="utf-8").read()
except OSError as _e:
    _page = ""
    check(False, f"the page carrying the cores table could not be read "
                 f"({_e}), so nothing here compares it with anything")
# | 32  | 31 | 1 |  - three integer columns, and only those rows.
_rows = re.findall(r"^\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*$",
                   _page, re.M)
check(len(_rows) >= 3,
      f"the cores table is no longer on the page in a shape this can read, so "
      f"the comparison has quietly stopped happening: found {len(_rows)} row(s)")
for _cores, _fwd, _rest in _rows:
    _cores, _fwd, _rest = int(_cores), int(_fwd), int(_rest)
    _r = tempfile.mkdtemp()
    # One thread per core, because the table's first column says cores and a
    # box with SMT would make "3" ambiguous between cores and CPUs.
    build(_r, 1, _cores, 1, hugepages_gb=4, ram_gb=64)
    _o = run(_r, "--subscribers", "200000")
    _m = re.search(r"^# workers \((\d+)\):", _o, re.M)
    _got = int(_m.group(1)) if _m else None
    check(_got == _fwd,
          f"the page tells somebody buying a {_cores}-core processor that "
          f"{_fwd} of them forward, and this tuner makes {_got}: that page is "
          f"read before the hardware is ordered")
    # And the other column is the same fact said the other way round, so a
    # page whose two columns do not add up is wrong about one of them.
    check(_fwd + _rest == _cores,
          f"the {_cores}-core row says {_fwd} forwarding and {_rest} for "
          f"everything else, which is not {_cores}")

# --------------------------------------------------------------------------
# What the box DID with the tuning, which is not what the tuner asked for.
#
# Every case above asks what this tuner decides. These ask what a box did with
# the decision, which is the other half and the one that goes wrong quietly:
# `update-grub` was never run, or the box has not rebooted since, or the
# kernel read the parameter and refused it because the hardware cannot do it.
# The third is #290 - a customer VM whose command line carried
# `hugepagesz=1G hugepages=5` on a cpu with no 1G page, for the life of the
# box, while every reader of that command line reported it tuned.
#
# FOUR ANSWERS, and each one has a case here. "requested and NOT in effect"
# is the state the defect lives in, and it is lost by being reported as
# either of the two states beside it: as "in effect" it is an appliance
# claiming tuning it does not have, as "not requested" it is a broken box
# that reads as a deliberately plain one. The fourth is a parameter this box
# exposes no way to read - rcu_nocbs where no kernel thread is visible,
# C-states on a machine with no idle driver - and reporting that as taken or
# as refused is a guess.
# --------------------------------------------------------------------------
print("\n=== the tuning in effect, which is not the tuning requested ===")

def effect(root):
    """{rule: (state, detail)} from the tuner's own report against `root`."""
    p = subprocess.run([sys.executable, TUNE, "--report-effective",
                        "--sysfs-root", root], capture_output=True, text=True)
    check(p.returncode == 0,
          f"--report-effective exited {p.returncode}, so nothing below "
          f"measured anything: {p.stderr.strip()[:200]}")
    got = {}
    for line in p.stdout.splitlines():
        state, _, rest = line.partition(" ")
        rule, _, detail = rest.partition(": ")
        got[rule] = (state, detail)
    return got

def grub_params(root):
    """The parameters this tuner really writes for `root`.

    READ OFF THE TUNER and never listed in this file. A list here would be a
    third copy of the kernel command line - the document has the second, and
    test/test_kernel_tuning.py is what compares those two - and the assertion
    it feeds is precisely that nobody has to remember to update one.
    """
    out = run(root, "--subscribers", "200000")
    for line in out.splitlines():
        if line.startswith("GRUB_CMDLINE_LINUX_DEFAULT="):
            body = line.split("=", 1)[1].strip().strip('"')
            return [t for t in body.split() if not t.startswith("$")]
    return []

def booted_box(root, params):
    """A root that booted `params` and whose kernel took every one of them.

    The starting point for each case below, which then breaks exactly one
    thing. Built from the parameter list the tuner generated rather than from
    a constant, so a knob added to render_grub() arrives here on its own.
    """
    cmd = " ".join(params)
    w(f"{root}/proc/cmdline", "BOOT_IMAGE=/boot/vmlinuz ro quiet " + cmd)
    w(f"{root}/etc/default/grub.d/hexarouting.cfg",
      "# Generated by hexarouting-tune. Do not edit by hand.\n"
      'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT ' + cmd + '"')
    p = dict((t.split("=", 1) + [""])[:2] for t in params)
    iso = p.get("isolcpus", "")
    w(f"{root}/sys/devices/system/cpu/isolated", iso)
    w(f"{root}/sys/devices/system/cpu/nohz_full", iso)
    # The kernel names one callback thread per offloaded cpu and nothing in
    # /sys answers for rcu_nocbs at all, so these threads ARE the readback.
    w(f"{root}/proc/3/comm", "rcu_gp")
    pid = 100
    for c in expand(p.get("rcu_nocbs", "")):
        w(f"{root}/proc/{pid}/comm", f"rcuop/{c}")
        pid += 1
    kb = {"1G": 1048576, "2M": 2048}[p.get("hugepagesz", "1G")]
    w(f"{root}/sys/kernel/mm/hugepages/hugepages-{kb}kB/nr_hugepages",
      p.get("hugepages", "0"))
    w(f"{root}/proc/meminfo", f"MemTotal: 67108864 kB\nHugepagesize: {kb} kB")
    os.makedirs(f"{root}/sys/class/iommu/dmar0", exist_ok=True)
    w(f"{root}/sys/kernel/iommu_groups/0/type", "identity")
    w(f"{root}/sys/devices/system/cpu/cpuidle/current_driver", "acpi_idle")
    w(f"{root}/sys/devices/system/cpu/cpu0/cpuidle/state0/name", "POLL")
    w(f"{root}/sys/devices/system/cpu/cpu0/cpuidle/state1/name", "C1")
    w(f"{root}/sys/kernel/mm/transparent_hugepage/enabled",
      "always madvise [never]")
    # The knobs that are not boot parameters, under the name install.sh gives
    # appliance/etc/sysctl.conf on a box.
    shipped = os.path.join(HERE, "etc", "sysctl.conf")
    with open(shipped) as f:
        sysctl = f.read()
    w(f"{root}/etc/sysctl.d/99-hexarouting.conf", sysctl.rstrip("\n"))
    for line in sysctl.splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, _, v = line.partition("=")
            w(f"{root}/proc/sys/" + k.strip().replace(".", "/"), v.strip())
    w(f"{root}/etc/default/irqbalance", f"IRQBALANCE_BANNED_CPULIST={iso}")
    w(f"{root}/proc/900/comm", "irqbalance")
    with open(f"{root}/proc/900/environ", "wb") as f:
        f.write(b"PATH=/usr/bin\0IRQBALANCE_BANNED_CPULIST="
                + iso.encode() + b"\0")
    return p

# A box that booted exactly what this tuner generated, and whose kernel took
# all of it. Every other case starts here and breaks one thing.
_hw = tempfile.mkdtemp()
build(_hw, 1, 4, 1, nic_node=0, nic_pci="0000:3b:00.0", hugepages_gb=8,
      isolated="1-3", ram_gb=64)
_params = grub_params(_hw)
check(_params, "the tuner generated no GRUB command line for a four-core box, "
               "so every case below is measuring an empty parameter list")
_took = tempfile.mkdtemp()
_shape = booted_box(_took, _params)
_rep = effect(_took)

# THE LOCK ON THE TWO LISTS. Every parameter render_grub() emits has to be
# reported on, and reported on with a readback rather than with the fourth
# state - a knob added to the command line with no way to read it back is an
# appliance that goes on claiming tuning it cannot check.
for _t in _params:
    _name = _t.split("=", 1)[0]
    _state, _why = _rep.get(_name, ("(not reported at all)", ""))
    check(_state == "in-effect",
          f"the tuner puts {_name} on the kernel command line and, on a box "
          f"that booted it and took it, --report-effective says "
          f"'{_state}': {_why}")
check(not any(s == "no-reader" for s, _ in _rep.values()),
      "a parameter the tuner writes has no readback in READBACK, so a box "
      "sets it and nothing can ever say whether the kernel took it")

# And the sysctls and the IRQ ban, which are requested in files of their own
# and are just as capable of being written and not applied.
for _k in ("kernel.numa_balancing", "IRQBALANCE_BANNED_CPULIST"):
    check(_rep.get(_k, ("missing", ""))[0] == "in-effect",
          f"{_k} is set on this box and the report says "
          f"{_rep.get(_k, ('it is not a rule at all',))[0]}")
print(f"  [ ok ] {len(_rep)} tuning rule(s) read back off a box that took "
      f"every one")

# --- the third state: requested, and the kernel refused it (#290) ---
_vm = tempfile.mkdtemp()
booted_box(_vm, _params)
# The customer VM: the command line asks for 1G pages and the cpu has none,
# so /sys carries only the 2M pool and the 1G parameters are ignored.
shutil.rmtree(f"{_vm}/sys/kernel/mm/hugepages/hugepages-1048576kB")
w(f"{_vm}/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages", 38)
w(f"{_vm}/proc/meminfo", "MemTotal: 67108864 kB\nHugepagesize: 2048 kB")
_r = effect(_vm)
check(_r.get("hugepagesz", ("missing",))[0] == "not-in-effect",
      f"a box whose command line asks for 1G pages and whose kernel has no 1G "
      f"pool reported hugepagesz as {_r.get('hugepagesz')}: that is #290, and "
      f"reporting it as taken is the appliance claiming tuning it has not got")
check("1G" in _r.get("hugepagesz", ("", ""))[1],
      "the refusal does not name the page size that was refused, so the "
      "operator is told a parameter did not take and not which one")
# The kernel simply not isolating what it was told to isolate.
w(f"{_vm}/sys/devices/system/cpu/isolated", "")
w(f"{_vm}/sys/devices/system/cpu/nohz_full", "  (null)")
shutil.rmtree(f"{_vm}/proc/100")
_r = effect(_vm)
for _k in ("isolcpus", "nohz_full", "rcu_nocbs"):
    check(_r.get(_k, ("missing",))[0] == "not-in-effect",
          f"the command line asks for {_k} and the kernel is doing none of "
          f"it, and the report says {_r.get(_k)}")
print("  [ ok ] a parameter the kernel refused is named as refused, not as set")

# --- and the third state again, one step earlier: never reached this boot ---
_stale = tempfile.mkdtemp()
booted_box(_stale, _params)
w(f"{_stale}/proc/cmdline", "BOOT_IMAGE=/boot/vmlinuz ro quiet")
_r = effect(_stale)
_cl = _r.get("the kernel command line", ("missing", ""))
check(_cl[0] == "not-in-effect",
      f"a box whose generated GRUB file asks for every parameter and whose "
      f"/proc/cmdline carries none of them reported {_cl[0]}: that is the "
      f"box where update-grub never ran, and it is not a tuned box")
# The INSTRUCTION and not the word. `update-grub` appears in the diagnosis
# too - "update-grub has not run since that file was written" - so a needle
# on the word alone is satisfied by a message that says what went wrong and
# never says what to do about it: measured, by cutting the last sentence off
# that message and watching this go green.
check("Run update-grub" in _cl[1],
      f"the box that has not run update-grub is not told to run it, which "
      f"leaves the operator a verdict and no move: {_cl[1]}")
check(len([1 for s, _ in _r.values() if s == "not-in-effect"]) == 1,
      "one command line that missed the boot was reported once per parameter "
      "on it: on an 80-column console that is the same sentence twenty times, "
      "pushing everything else off the screen")
print("  [ ok ] a command line that never reached the boot says so once")

# --- the fourth state: nothing on this box can answer either way ---
_blind = tempfile.mkdtemp()
booted_box(_blind, _params)
for _gone in ("sys/devices/system/cpu/nohz_full",
              "sys/devices/system/cpu/cpuidle/current_driver",
              "sys/kernel/mm/transparent_hugepage/enabled",
              "proc/3/comm"):
    os.remove(os.path.join(_blind, _gone))
shutil.rmtree(f"{_blind}/sys/devices/system/cpu/cpu0/cpuidle")
shutil.rmtree(f"{_blind}/sys/kernel/iommu_groups")
_r = effect(_blind)
for _k in ("nohz_full", "rcu_nocbs", "transparent_hugepage",
           "intel_idle.max_cstate", "processor.max_cstate", "iommu"):
    check(_r.get(_k, ("missing",))[0] == "cannot-tell",
          f"a box that exposes no way to read {_k} back reported "
          f"{_r.get(_k)}: a parameter whose effect cannot be read is neither "
          f"taken nor refused, and printing either is a guess")
print("  [ ok ] a parameter with no readback here says so, rather than guessing")

# --- and the state that is none of those: never asked for ---
_plain = tempfile.mkdtemp()
w(f"{_plain}/proc/cmdline", "BOOT_IMAGE=/boot/vmlinuz ro quiet")
_r = effect(_plain)
_cl = _r.get("the kernel command line", ("missing", ""))
check(_cl[0] == "not-requested",
      f"a box with no generated GRUB file at all reported {_cl[0]}: it has "
      f"asked for nothing, which is a different box from one whose request "
      f"was refused, and the operator's next move differs")
# And a machine whose /proc cannot be read is not a machine with no tuning.
_dark = tempfile.mkdtemp()
booted_box(_dark, _params)
os.remove(f"{_dark}/proc/cmdline")
_r = effect(_dark)
check(_r.get("the kernel command line", ("missing",))[0] == "cannot-tell",
      "a box whose /proc/cmdline cannot be read reported on its tuning "
      "anyway, which is a report about a boot nothing here has seen")
print("  [ ok ] a box that asked for nothing, and a box that cannot be asked")

for _d in (_hw, _took, _vm, _stale, _blind, _plain, _dark):
    shutil.rmtree(_d, ignore_errors=True)

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
