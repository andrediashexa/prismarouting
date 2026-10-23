#!/usr/bin/env python3
"""
Management in a VRF on a card the dataplane does not own (#613).

The common VM layout: a virtio NIC Linux keeps for management (ens18) beside
the cards DPDK claimed. `interface ens18 / vrf 100` passed `commit check` and
the apply refused it - "interface ens18: the dataplane does not have it" -
so the installer's default (ifupdown's `gateway`, distance 0 in zebra) kept
beating every `router static` default and the dataplane's FIB 0 default
stayed dpo-drop. Each part is shown red by a mutation, named where it is
checked:

  * the readings: the host's own addresses and defaults of one interface,
    and which interfaces are Linux-only;
  * the doors: the shapes the move asks for, and nothing wider;
  * the model: what a Linux-only interface's block may hold;
  * the move: the defaults and addresses off, zebra settled, the link moved,
    zebra settled, the addresses back, the defaults in the new table;
  * the commit check, the apply and the boot's missing cards.

lab/mgmt-vrf.sh linux is the same on a real kernel, zebra and dataplane.
"""
import contextlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src", "prismash"))
import config  # noqa: E402
import doors  # noqa: E402
import prismash  # noqa: E402
import system  # noqa: E402
import vppapi  # noqa: E402
from config import Config, CONSOLE_COLUMNS  # noqa: E402

fail = 0


def check(c, msg):
    global fail
    print(("  [ ok ] " if c else "  FAIL: ") + msg)
    fail += 0 if c else 1


@contextlib.contextmanager
def spoken():
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        yield out


def narrow(text):
    return all(len(r) <= CONSOLE_COLUMNS for r in text.splitlines())


# `ip -o addr show` as Debian 12's iproute2 6.1 prints it (measured in the lab
# image): the management card with a primary, a secondary, a DHCP lease, its
# IPv6 global, a SLAAC one and its link-local, and another card.
ADDRS = (
    "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever "
    "preferred_lft forever\n"
    "2: ens18    inet 10.34.200.166/24 brd 10.34.200.255 scope global "
    "ens18\\       valid_lft forever preferred_lft forever\n"
    "2: ens18    inet 10.34.201.1/24 brd 10.34.201.255 scope global "
    "secondary ens18\\       valid_lft forever preferred_lft forever\n"
    "2: ens18    inet 10.34.202.9/24 brd 10.34.202.255 scope global dynamic "
    "ens18\\       valid_lft 3500sec preferred_lft 3500sec\n"
    "2: ens18    inet6 2001:db8:166::166/64 scope global \\       valid_lft "
    "forever preferred_lft forever\n"
    "2: ens18    inet6 2001:db8:166:0:be24:11ff:fe00:1/64 scope global "
    "dynamic mngtmpaddr \\       valid_lft 86000sec preferred_lft 14000sec\n"
    "2: ens18    inet6 fe80::be24:11ff:fe00:1/64 scope link \\       "
    "valid_lft forever preferred_lft forever\n"
    "3: ens19    inet 10.0.0.1/24 brd 10.0.0.255 scope global ens19\\       "
    "valid_lft forever preferred_lft forever\n")

print("=== linux_addresses: what the host holds for good, one interface ===")
got = system.linux_addresses(ADDRS, "ens18")
# Mutation shown red: without `"dynamic" not in w`, the DHCP lease and the
# SLAAC address are written back by hand and outlive their daemons.
check(got == ["10.34.200.166/24", "10.34.201.1/24", "2001:db8:166::166/64"],
      f"global, permanent, in Linux's order: {got}")
check(system.linux_addresses(ADDRS, "ens19") == ["10.0.0.1/24"]
      and system.linux_addresses(ADDRS, "ens2") == []
      and system.linux_addresses("", "ens18") == []
      and system.linux_addresses(None, "ens18") == [],
      "another interface's are not its, and nothing is nothing")

print("\n=== linux_defaults: the host's own defaults, not a daemon's ===")
ROUTES = ("default via 10.34.200.1 \n"
          "default via 10.34.200.254 proto dhcp src 10.34.202.9 metric 100 \n"
          "default via 10.34.200.2 proto static metric 20 \n"
          "default via fe80::1 proto ra metric 1024 expires 1790sec "
          "pref medium\n"
          "default via 2001:db8:166::1 metric 1024 pref medium\n"
          "default proto boot metric 1024 pref medium\\	nexthop via "
          "2001:db8:166::2 weight 1\n"
          "default via nonsense \n")
got = system.linux_defaults(ROUTES)
# Mutation shown red: accepting `proto static` carries FRR's own static
# (rt_protos.d names 196 "static") into a table zebra does not own.
check(got == ["10.34.200.1", "10.34.200.254", "2001:db8:166::1"],
      f"ifupdown's (proto boot, unprinted) and dhclient's only: {got}")
check(system.linux_defaults("") == [] and system.linux_defaults(None) == [],
      "nothing is nothing")

print("\n=== the doors a Linux-only move asks for, and nothing wider ===")
built = [doors.linux_defaults_argv("ens18", 0, False),
         doors.linux_defaults_argv("ens18", 100, True),
         doors.linux_addr_argv("del", "10.34.200.166/24", "ens18"),
         doors.linux_addr_argv("add", "10.34.200.166/24", "ens18"),
         doors.linux_addr_argv("add", "2001:db8:166::166/64", "ens18"),
         doors.linux_default_argv("del", "10.34.200.1", "ens18", 0),
         doors.linux_default_argv("del", "2001:db8:166::1", "ens18", 100),
         doors.linux_default_argv("replace", "10.34.200.1", "ens18", 0),
         doors.linux_default_argv("replace", "10.34.200.1", "ens18", 100)]
check(built[0] == ("ip", "-o", "route", "show", "default", "dev", "ens18",
                   "table", "main")
      and built[1] == ("ip", "-6", "-o", "route", "show", "default", "dev",
                       "ens18", "table", "100")
      and built[3] == ("ip", "addr", "add", "10.34.200.166/24", "brd", "+",
                       "dev", "ens18")
      and built[4] == ("ip", "addr", "add", "2001:db8:166::166/64", "dev",
                       "ens18")
      and built[6] == ("ip", "route", "del", "default", "via",
                       "2001:db8:166::1", "dev", "ens18", "table", "100"),
      "the shapes, as ip takes them")
for argv in built:
    check(doors.verb_of(argv) is not None,
          f"opened: {' '.join(argv)} -> {doors.verb_of(argv)}")
# Mutation shown red: without the reserved-prefix test in _linux_door, the
# backplane's prisma-bp would have its addresses taken off through a door.
for argv in (("ip", "addr", "del", "10.0.0.1/24", "dev", "prisma-bp"),
             ("ip", "addr", "del", "-x", "dev", "ens18"),
             ("ip", "addr", "flush", "dev", "ens18"),
             ("ip", "addr", "add", "10.0.0.1/24", "dev", "ens18"),
             ("ip", "addr", "add", "10.0.0.1/24", "dev", "ens18", "x", "y"),
             ("ip", "-o", "route", "show", "default", "dev", "ens18",
              "table", "254"),
             ("ip", "-o", "route", "show", "default", "dev", "prisma-bp",
              "table", "main"),
             ("ip", "-o", "route", "show", "dev", "ens18", "table", "main",
              "x"),
             ("ip", "route", "del", "default", "via", "x", "dev", "ens18"),
             ("ip", "route", "del", "default", "via", "10.0.0.2", "dev",
              "ens18", "table", "255"),
             ("ip", "route", "del", "10.0.0.0/8", "via", "10.0.0.2", "dev",
              "ens18")):
    check(doors.verb_of(argv) is None, f"refused: {' '.join(argv)}")

check(doors._linux_door(built[0]) == "read a Linux interface's default routes"
      and doors._linux_door(built[2])
      == "move a Linux interface's address between VRFs"
      and doors._linux_door(built[5])
      == "take a Linux interface's default route out"
      and doors._linux_door(built[7]) is None
      and doors._linux_door(("ip", "link", "set", "dev", "ens18", "up"))
      is None,
      "_linux_door: its three shapes by name; the replace and the rest are "
      "verb_of's other doors")

print("\n=== beyond_vrf: a Linux-only interface takes `vrf` alone ===")
c = Config()
c.vrfs = {"ens18": 100}
c.declared = {"ens18"}
check(c.beyond_vrf("ens18") == [], "vrf and the block itself: nothing more")
c.descriptions["ens18"] = "management"
c.addresses["ens18"] = ["10.34.200.166/24"]
c.mtus["ens18"] = 1500
c.routing = ["interface ens18", " ip ospf area 0", "exit"]
got = c.beyond_vrf("ens18")
check(got == ["description management", "ipv4 address 10.34.200.166/24",
              "mtu 1500", "interface ens18 (routing)"],
      f"everything the dataplane or FRR would be handed: {got}")

# The host, as `ip -d -o link show` prints it: management's card in mgmt,
# a pair's TAP (vppup0) in cust, a card Linux has in nothing, the VRFs, the
# box's own VRF and a member of it.
LINK = ("{n}: {name}: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq "
        "{master}state UP mode DEFAULT group default qlen 1000\\    "
        "link/ether 52:54:00:00:00:0{n} brd ff:ff:ff:ff:ff:ff promiscuity 0 "
        "\\    {kind}addrgenmode eui64 numtxqueues 1\n")


def links(ens18_in=None):
    rows = [("ens18", f"master {ens18_in} " if ens18_in else "",
             "vrf_slave table 100 " if ens18_in else ""),
            ("vppup0", "master cust ", "vrf_slave table 200 "),
            ("ens20", "", ""),
            ("mgmt", "", "vrf table 100 "),
            ("cust", "", "vrf table 200 "),
            ("prisma-bp", "", "vrf table 128 "),
            ("bp0", "master prisma-bp ", "vrf_slave table 128 ")]
    return "".join(LINK.format(n=i + 2, name=n, master=m, kind=k)
                   for i, (n, m, k) in enumerate(rows))


print("\n=== linux_only_vrfs: read back off the VRF device it is in ===")
got = prismash.linux_only_vrfs(doors.ip_links(links("mgmt")),
                               {"host-veth-up", "vppup0", "tap4096"})
# Mutation shown red: without `name not in dataplane`, the pair's TAP reads
# as a Linux-only interface of its own and the next commit moves it.
check(got == {"ens18": 100},
      f"ens18 in mgmt; the TAP, the box's own and a free card are not: {got}")
check(prismash.linux_only_vrfs(doors.ip_links(links()), set()) == {
    "vppup0": 200}, "a TAP nobody says is a pair's reads as Linux's")


# The dataplane's FIB with nothing of management's in it: table 0 only.
FIB = ("ipv4-VRF:0, fib_index:0, flow hash:[src dst sport dport proto "
       "flowlabel ] epoch:0 flags:none locks:[default-route:1, ]\n"
       "0.0.0.0/0\n  unicast-ip4-chain\n  [@0]: dpo-load-balance: "
       "[proto:ip4 index:1 buckets:1 uRPF:0 to:[0:0]]\n"
       "    [0] [@0]: dpo-drop ip4\n")


class Dp(prismash.Vpp):
    """The dataplane: the upstream card and its pair; no ens18."""

    TRACE = False

    def __init__(self, answer=True):
        self.answer = answer

    def run(self, cmd):
        if not self.answer:
            return 1, ""
        if cmd == "show ip fib":
            return 0, FIB
        if cmd == "show lcp":
            return 0, ("itf-pair: [0] host-veth-up tap4096 vppup0 5 "
                       "type tap\n")
        if cmd == "show interface":
            return 0, ("              Name               Idx    State  "
                       "MTU (L3/IP4/IP6/MPLS)\n"
                       "host-veth-up                      1      up    "
                       "      1500/0/0/0\n"
                       "local0                            0     down   "
                       "      0/0/0/0\n"
                       "tap4096                           2      up    "
                       "      1500/0/0/0\n")
        return 0, ""


class Host:
    """The host's doors, with state: ens18's master, its addresses and the
    defaults through it, per table. Every argv kept in `log`."""

    def __init__(self, master=None, log=None, refuse=()):
        self.master = master
        self.addrs = ["10.34.200.166/24", "2001:db8:166::166/64"]
        self.routes = {0: ["10.34.200.1", "2001:db8:166::1"], 100: []}
        self.log = log if log is not None else []
        self.refuse = refuse

    def __call__(self, argv, stdin=None, timeout=30):
        argv = tuple(argv)
        self.log.append(("host", " ".join(argv)))
        if any(argv[:len(p)] == tuple(p) for p in self.refuse):
            return 2, "RTNETLINK answers: Operation not permitted\n"
        if argv == doors.LINKS_ARGV:
            return 0, links(self.master)
        if argv == doors.ADDRS_ARGV:
            return 0, "".join(
                f"2: ens18    {'inet6' if ':' in a else 'inet'} {a} scope "
                f"global ens18\\       valid_lft forever preferred_lft "
                f"forever\n" for a in self.addrs)
        if argv[:5] in (("ip", "-o", "route", "show", "default"),
                        ("ip", "-6", "-o", "route", "show")):
            t = 0 if argv[-1] == "main" else int(argv[-1])
            v6 = argv[1] == "-6"
            return 0, "".join(f"default via {g} \n"
                              for g in self.routes.get(t, [])
                              if (":" in g) == v6)
        if argv[:3] == ("ip", "route", "del"):
            t = int(argv[9]) if len(argv) == 10 else 0
            self.routes[t].remove(argv[5])
        if argv[:3] == ("ip", "route", "replace"):
            t = int(argv[9]) if len(argv) == 10 else 0
            self.routes.setdefault(t, []).append(argv[5])
        if argv[:3] == ("ip", "addr", "del"):
            self.addrs.remove(argv[3])
        if argv[:3] == ("ip", "addr", "add"):
            self.addrs.append(argv[3])
        if argv[:4] == ("ip", "link", "set", "dev") and argv[5] == "master":
            self.master = argv[6]
            self.routes[0] = []         # the kernel drops them on the cycle
        if argv[:4] == ("ip", "link", "set", "dev") and argv[5] == "nomaster":
            self.master = None
            self.routes[100] = []
        return 0, ""


class Zebra:
    """FRR as the VTY asks it: `show interface ens18 json` names a VRF, the
    routes asked are answered from `held`; each ask kept in `log`."""

    def __init__(self, log, held=None, host=None):
        self.log = log
        self.held = held or {}
        self.host = host

    def show(self, cmd):
        self.log.append(("zebra", cmd))
        if cmd == "show interface ens18 json":
            vrf = (self.host and self.host.master) or "default"
            return 0, json.dumps({"ens18": {"vrfName": vrf,
                                            "ipAddresses": []}})
        return 0, json.dumps(self.held.get(cmd, {}))

    def running(self):
        return []


def shell(host=None, dp=None):
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT}
    sh._prompt_name = prismash.HOSTNAME_DEFAULT
    sh.dp = dp or Dp()
    host = host or Host()
    sh.sys = system.System(runner=host)
    sh.frr = Zebra(host.log, host=host)
    sh.mode = "config"
    sh.frr_ctx = []
    sh.nat_ctx = []
    sh.candidate = Config()
    return sh


print("\n=== the readings, off the dataplane and the host ===")
sh = shell(Host(master="mgmt"))
check(sh.dataplane_and_pairs() == {"host-veth-up", "local0", "tap4096",
                                   "vppup0"},
      f"dataplane_and_pairs: its interfaces and its pairs' ends: "
      f"{sh.dataplane_and_pairs()}")
check(shell(dp=Dp(answer=False)).dataplane_and_pairs() is None,
      "and None when it does not answer, which is not 'none'")
check(sh.host_links()["ens18"]["master"] == "mgmt"
      and shell(Host(refuse=[doors.LINKS_ARGV])).host_links() == {},
      "host_links: the host's links, {} when it cannot be asked")
check(sh.linux_vrfs() == {"ens18": 100},
      f"linux_vrfs: ens18 in table 100: {sh.linux_vrfs()}")
check(shell(Host(master="mgmt"), Dp(answer=False)).linux_vrfs() == {},
      "linux_vrfs: nothing when the pairs cannot be told apart")
check(sh.linux_only({"ens18", "ens20", "host-veth-up", "vppup0", "mgmt",
                     "prisma-bp", "nope"}) == {"ens18", "ens20"},
      f"linux_only: Linux's cards, never a pair's, a VRF or a name nobody "
      f"has: {sh.linux_only({'ens18', 'ens20', 'vppup0', 'mgmt', 'nope'})}")
check(shell(dp=Dp(answer=False)).linux_only({"ens18"}) == set()
      and sh.linux_only(set()) == set(),
      "linux_only: none when the dataplane cannot be asked, or none asked")
check(shell().linux_way_back("ens18", 0) == ["10.34.200.1",
                                             "2001:db8:166::1"]
      and shell().linux_way_back("ens18", 100) == [],
      "linux_way_back: each family's gateway, in the table asked")

print("\n=== zebra_defaults_held: what zebra still has through it ===")
log = []
sh = shell(Host(log=log))
sh.frr = Zebra(log, {"show ip route vrf default 0.0.0.0/0 json": {
    "0.0.0.0/0": [{"protocol": "kernel", "nexthops": [
        {"ip": "10.34.200.1", "interfaceName": "ens18"}]},
                  {"protocol": "static", "nexthops": [
                      {"ip": "10.0.0.2", "interfaceName": "vppup0"}]}]}})
check(sh.zebra_defaults_held("ens18", ["10.34.200.1"]) == ["0.0.0.0/0"]
      and ("zebra", "show ipv6 route vrf default ::/0 json") not in log,
      f"the kernel's default through it, IPv4 only asked: {log[-1:]}")
check(sh.zebra_defaults_held("ens18", []) == [],
      "nothing asked, nothing held")
sh.frr = Zebra(log)
sh.zebra_ask = lambda cmd: None
check(sh.zebra_defaults_held("ens18", ["10.34.200.1"]) == [],
      "and nothing to wait for when zebra cannot be asked")

print("\n=== move_linux_only: bare, zebra settled, the way back with it ===")
log = []
host = Host(log=log)
sh = shell(host)
with spoken() as o:
    ok = sh.move_linux_only("ens18", "mgmt", 100)
steps = [w for k, w in log if w.startswith(("ip route del", "ip addr",
                                            "ip link set",
                                            "ip route replace",
                                            "show interface"))]
# Mutation shown red: with the defaults not read off the table it leaves
# (gws = []), the kernel drops them on the move and table 100 has no way
# back - the box unreachable from off its subnet.
check(ok and steps[:4] == [
    "ip route del default via 10.34.200.1 dev ens18",
    "ip route del default via 2001:db8:166::1 dev ens18",
    "ip addr del 2001:db8:166::166/64 dev ens18",
    "ip addr del 10.34.200.166/24 dev ens18"]
    and steps[4].startswith("show interface ens18")
    and "ip link set dev ens18 master mgmt" in steps
    and steps[-4:] == [
        "ip addr add 10.34.200.166/24 brd + dev ens18",
        "ip addr add 2001:db8:166::166/64 dev ens18",
        "ip route replace default via 10.34.200.1 dev ens18 table 100",
        "ip route replace default via 2001:db8:166::1 dev ens18 table 100"],
    f"defaults and addresses off, zebra, moved, back, way back in 100: "
    f"{steps}")
check(host.routes == {0: [], 100: ["10.34.200.1", "2001:db8:166::1"]}
      and sorted(host.addrs) == ["10.34.200.166/24",
                                 "2001:db8:166::166/64"]
      and o.getvalue() == "",
      f"the host after: {host.routes} {host.addrs} {o.getvalue()!r}")
for _, cmd in [e for e in log if e[0] == "host"]:
    check(doors.verb_of(cmd.split()) is not None
          or tuple(cmd.split()) in doors.QUERIES,
          f"through a door: {cmd}")
# And out again, as an expired `commit confirmed` takes it: the way back is
# the main table's again.
log.clear()
with spoken():
    ok = sh.move_linux_only("ens18", None, 0)
check(ok and host.master is None
      and host.routes == {0: ["10.34.200.1", "2001:db8:166::1"], 100: []}
      and ("host", "ip link set dev ens18 nomaster") in log,
      f"taken out: the defaults back in main: {host.routes}")
log.clear()
check(sh.move_linux_only("ens18", None, 0)
      and not [w for _, w in log if w.startswith(("ip addr", "ip route"))],
      "already where it is asked to be: nothing touched")

print("\n=== move_linux_only: refusals, and the addresses put back ===")
for host, want, table, needle in (
        (Host(), "mgmt", 100, None),
        (Host(master="prisma-bp"), "mgmt", 100,
         "interface ens18: not in a tenant VRF"),
        (Host(), None, 100, "interface ens18: vrf 100 has no VRF device")):
    sh = shell(host)
    if needle is None:
        sh.host_links = lambda: {}
        needle = "interface ens18: not on this box"
    with spoken() as o:
        ok = sh.move_linux_only("ens18", want, table)
    check(not ok and needle in o.getvalue() and narrow(o.getvalue()),
          f"refused: {o.getvalue().strip()!r}")
host = Host(refuse=[("ip", "link", "set")])
with spoken() as o:
    ok = shell(host).move_linux_only("ens18", "mgmt", 100)
check(not ok and "ens18 was not put in mgmt" in o.getvalue()
      and sorted(host.addrs) == ["10.34.200.166/24", "2001:db8:166::166/64"],
      f"a refused move puts the addresses back: {host.addrs}")
host = Host(refuse=[("ip", "addr", "del")])
with spoken() as o:
    ok = shell(host).move_linux_only("ens18", "mgmt", 100)
check(not ok and "interface ens18: an address was not taken off"
      in o.getvalue() and narrow(o.getvalue()) and host.master is None,
      f"a refused address stops it before the move: {o.getvalue()!r}")
host = Host(refuse=[("ip", "addr", "add")])
with spoken() as o:
    ok = shell(host).linux_addrs_back("ens18", ["10.34.200.166/24"])
check(not ok and "interface ens18: an address was not put back"
      in o.getvalue() and narrow(o.getvalue()),
      f"linux_addrs_back: a refusal said: {o.getvalue().strip()!r}")
host = Host(refuse=[("ip", "route", "replace")])
with spoken() as o:
    ok = shell(host).move_linux_only("ens18", "mgmt", 100)
check(ok and "warning: a default route was not put back"
      in o.getvalue() and narrow(o.getvalue()) and host.master == "mgmt",
      f"a default not put back is said, not fatal: {o.getvalue()!r}")


class Api:
    """The binary API: no interface it is asked about by name exists here
    but the dataplane's own, and every write is kept."""

    def __init__(self):
        self.calls = []

    def available(self):
        return vppapi.OK, "stub"

    def interface_index(self, name):
        return {"host-veth-up": 1, "tap4096": 2}.get(name)

    def call(self, message, args):
        self.calls.append((message, args))
        return vppapi.Reply(vppapi.OK)

    def dump(self, message, args):
        return vppapi.Reply(vppapi.OK, fields={"rows": []})


def checked(target, host=None, running=None):
    """`commit check` of `target` on the box above: what it said, and ok.
    FRR says only which table each VRF is."""
    sh = shell(host)
    sh.api = Api()
    sh.frr = type("F", (prismash.Frr,), {
        "available": lambda s: True, "running": lambda s: [],
        "daemons": lambda s: {"zebra", "mgmtd", "staticd"},
        "show": lambda s, c: ((0, "vrf mgmt id 5 table 100\n")
                              if c == "show vrf" else (1, "")),
        "reload": lambda s, l, check_only=False: (0, "")})()
    sh.running_config = lambda: running or Config()
    with spoken() as o:
        ok = sh._apply(target, check_only=True)
    return ok, o.getvalue()


def mgmt(extra=None, table=100):
    t = Config()
    t.vrf_devices = {"mgmt": 100}
    t.vrfs = {"ens18": table}
    t.management = {"vrf": "mgmt"}
    for k, v in (extra or {}).items():
        getattr(t, k).update(v)
    return t


print("\n=== commit check: a Linux-only interface in a VRF ===")
ok, said = checked(mgmt())
check(ok and "error" not in said,
      f"ens18 in mgmt, the dataplane without it: accepted: {said!r}")
# Mutation shown red: without `named | vrf_named - linux_only`, a name
# neither side has passes the check and the apply refuses it half way.
t = mgmt()
t.vrfs = {"ens99": 100}
ok, said = checked(t)
check(not ok and "interface ens99: this box has no such interface" in said
      and narrow(said),
      f"a name neither the dataplane nor Linux has: {said.strip()!r}")
ok, said = checked(mgmt(table=300))
check(not ok and "interface ens18: vrf 300 has no VRF device" in said
      and narrow(said), f"a table no VRF device is on: {said.strip()!r}")
ok, said = checked(mgmt({"addresses": {"ens18": ["10.34.200.166/24"]},
                         "mtus": {"ens18": 1500}}))
check(not ok and "interface ens18: Linux's, so 'vrf' alone applies" in said
      and "ipv4 address 10.34.200.166/24; mtu 1500" in said
      and "the dataplane does not hold this card" not in said
      and narrow(said),
      f"an address and an MTU on it, said once: {said.strip()!r}")
t = mgmt()
t.vrfs["host-veth-up"] = 100
ok, said = checked(t)
check(ok, f"a dataplane interface beside it is the dataplane's: {said!r}")

print("\n=== linux_connected: management's way out is next to it ===")
check(shell().linux_connected(mgmt()) == {100: ["10.34.200.166/24"]},
      f"its IPv4 address, under table 100: "
      f"{shell().linux_connected(mgmt())}")
check(shell().linux_connected(Config()) == {}, "no `vrf`, nothing asked")
t = mgmt()
t.routing = ["vrf mgmt", " ip route 0.0.0.0/0 10.34.200.1", "exit-vrf"]
ok, said = checked(t)
check(ok and "no connected network" not in said,
      f"vrf mgmt's default through ens18's gateway is accepted: {said!r}")
t.routing = ["vrf mgmt", " ip route 0.0.0.0/0 10.99.0.1", "exit-vrf"]
ok, said = checked(t)
# Mutation shown red: without linux_connected() in the next-hop check, table
# 100 is in no FIB and no statement, so the check cannot place the VRF's
# routes and passes a next hop on nothing - the way `commit check` passed
# the issue's commit without asking.
check(not ok and "this next hop is on no connected network: 10.99.0.1"
      in said, "and one through nothing it has is refused")

print("\n=== apply_vrf_moves: a Linux-only move, no dataplane table ===")
log = []
host = Host(log=log)
sh = shell(host)
sh.api = Api()
moved = []
sh.set_vrf = lambda i, t: moved.append((i, t)) or True
sh.set_address = lambda i, a, add: moved.append((i, a)) or True
sh.move_linux_end = lambda i, w, a, reapply=False: moved.append(i) or True
vrf_set = Config().delta_to(mgmt())["vrf_set"]
vrf_set["host-veth-up"] = (100, ["10.0.0.1/24"], ["10.0.0.1/24"])
with spoken() as o:
    ok = sh.apply_vrf_moves(vrf_set, mgmt(), True)
# Mutation shown red: without the linux_only branch, set_vrf("ens18") is
# asked and the commit stops at "the dataplane does not have it".
check(ok and host.master == "mgmt"
      and host.routes[100] == ["10.34.200.1", "2001:db8:166::1"]
      and moved == [("host-veth-up", "10.0.0.1/24"), "host-veth-up",
                    ("host-veth-up", 100), ("host-veth-up", "10.0.0.1/24")],
      f"ens18 enslaved with its way back; the dataplane's card as before: "
      f"{host.master} {moved} {o.getvalue()!r}")
sh.move_linux_only = lambda i, w, t: False
check(not sh.apply_vrf_moves(Config().delta_to(mgmt())["vrf_set"], mgmt(),
                             True), "a refused move stops it")
sh = shell()
sh.set_vrf = lambda i, t: False
check(not sh.apply_vrf_moves({"host-veth-up": (100, [], [])}, mgmt(), False),
      "and so does a refused table")

print("\n=== the running configuration reads it back ===")
sh = shell(Host(master="mgmt"))
sh.api = Api()
with spoken():
    run = sh._running_config()
check(run.vrfs.get("ens18") == 100 and " vrf 100" in run.interface_lines(
    "ens18"), f"interface ens18 / vrf 100, read back: {run.vrfs}")

print("\n=== a boot: a Linux-only interface is not a missing card ===")
sh = shell()
check(sh.missing_cards(mgmt()) == [],
      f"ens18 with `vrf` alone is Linux's: {sh.missing_cards(mgmt())}")
t = mgmt({"addresses": {"ens18": ["10.34.200.166/24"]}})
# Mutation shown red: missing_cards() without the beyond_vrf test applies a
# card's addresses at boot to a dataplane that does not have it.
check(sh.missing_cards(t) == ["ens18"],
      f"with an address it is a card the dataplane lacks: "
      f"{sh.missing_cards(t)}")
t = mgmt()
t.vrfs = {"ens21": 100}
check(sh.missing_cards(t) == ["ens21"],
      "and one Linux does not have either is missing")

print("\n" + ("ALL TESTS PASSED" if not fail else f"{fail} FAILURES"))
sys.exit(1 if fail else 0)
