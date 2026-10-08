#!/usr/bin/env python3
"""
A tenant VRF's Linux device (#540): `vrf <name> table <id>`.

FRR's `router bgp <as> vrf <name>` and every VRF-scoped kernel route need a
Linux VRF device of that name, on the table VPP and linux-nl key the FIB by.
The box made none - lab/policy-c.sh made `red` and `svc` by hand - so the
statement makes it, puts the Linux ends of that table's interfaces in it,
and undoes both. Each part is shown red by a mutation:

  * the statement: what the prompt and a file take and refuse, one predicate
    for both (mutation: drop the prisma-* refusal from vrf_device_error);
  * the model: written ahead of the interfaces, read back, and a new table
    is the device removed and made again (mutation: vrfdev_del without the
    changed-table half);
  * the doors: the shapes, and the host asked before the door opens - only
    a VRF is removed, nothing enters or leaves prisma-bp or a bridge
    (mutation: guard_of returning None for `ip link del`);
  * the apply: removed, made, up, then the members, through the host's
    doors and in that order (mutation: members before the devices).
"""
import contextlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src", "prismash")
sys.path.insert(0, SRC)
import config  # noqa: E402
import doors  # noqa: E402
import prismash  # noqa: E402
import system  # noqa: E402
from config import Config, CONSOLE_COLUMNS  # noqa: E402

fail = 0


def check(c, msg):
    global fail
    print(("  [ ok ] " if c else "  FAIL: ") + msg)
    fail += 0 if c else 1


@contextlib.contextmanager
def spoken():
    """What the VTY printed inside the block, read with .getvalue()."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        yield out


def narrow(text):
    """True when every row of `text` fits the console."""
    return all(len(r) <= CONSOLE_COLUMNS for r in text.splitlines())


# What `ip -d -o link show` prints, measured on Debian's 6.1 (iproute2 6.1):
# a TAP in red, red itself, the backplane's VRF and its bridge, a bridge
# member, and a TAP in nothing.
LINKS = (
    "7: vppred0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq master "
    "red state UP mode DEFAULT group default qlen 1000\\    link/ether "
    "02:fe:00:00:00:01 brd ff:ff:ff:ff:ff:ff promiscuity 0  allmulti 0 "
    "minmtu 68 maxmtu 65521 \\    tun type tap pi off vnet_hdr on \\    "
    "vrf_slave table 10 addrgenmode eui64 numtxqueues 1\n"
    "9: red: <NOARP,MASTER,UP,LOWER_UP> mtu 65575 qdisc noqueue state UP "
    "mode DEFAULT group default qlen 1000\\    link/ether 8a:5e:6b:89:51:b1 "
    "brd ff:ff:ff:ff:ff:ff promiscuity 0  allmulti 0 minmtu 1280 maxmtu "
    "65575 \\    vrf table 10 addrgenmode eui64 numtxqueues 1\n"
    "3: prisma-bp: <NOARP,MASTER,UP,LOWER_UP> mtu 65575 qdisc noqueue "
    "state UP mode DEFAULT group default qlen 1000\\    link/ether "
    "e2:36:d9:e6:36:11 brd ff:ff:ff:ff:ff:ff promiscuity 0 \\    vrf table "
    "128 addrgenmode eui64 numtxqueues 1\n"
    "4: bp0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue "
    "master prisma-bp state UP mode DEFAULT group default qlen 1000\\    "
    "link/ether 1a:2b:3c:4d:5e:6f brd ff:ff:ff:ff:ff:ff promiscuity 0 \\    "
    "bridge forward_delay 1500 \\    vrf_slave table 128 addrgenmode eui64\n"
    "5: veth-mgmt@if2: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc "
    "noqueue master bp0 state UP mode DEFAULT group default qlen 1000\\    "
    "link/ether 1a:2b:3c:4d:5e:70 brd ff:ff:ff:ff:ff:ff link-netnsid 0 "
    "promiscuity 1 \\    veth \\    bridge_slave state forwarding\n"
    "8: vppsvc0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq state "
    "UP mode DEFAULT group default qlen 1000\\    link/ether "
    "02:fe:00:00:00:02 brd ff:ff:ff:ff:ff:ff promiscuity 0 \\    tun type "
    "tap pi off vnet_hdr on addrgenmode eui64 numtxqueues 1\n")

print("=== vrf_device_error: one predicate for the prompt, file and door ===")
check(config.vrf_device_error("red", 10) is None, "red on table 10 is taken")
check(config.vrf_device_error("red", "4294967295") is None,
      "the widest table an interface's 'vrf' takes is taken")
for name, table, needle in (
        ("prisma-red", 10, "the box's own"),
        ("prisma-bp", 128, "the box's own"),
        ("default", 10, "the table without a VRF"),
        ("a" * 16, 10, "not a name"),
        ("-x", 10, "not a name"),
        ("re/d", 10, "not a name"),
        ("red", 0, "not a table"),
        ("red", "1e3", "not a table"),
        ("red", 4294967296, "not a table"),
        ("red", 254, "the kernel's own"),
        ("red", 253, "the kernel's own"),
        ("red", 255, "the kernel's own")):
    why = config.vrf_device_error(name, table)
    check(why is not None and needle in why[0]
          and len(why[0]) <= CONSOLE_COLUMNS,
          f"vrf {name[:20]} table {table}: {why and why[0]}")

print("\n=== vrf_table_taken: one table, one VRF ===")
check(config.vrf_table_taken({"red": 10}, "svc", 30) is None,
      "another table is free")
check(config.vrf_table_taken({"red": 10}, "red", 10) is None,
      "a VRF typed again on its own table is not a clash")
why = config.vrf_table_taken({"red": 10}, "svc", "10")
check(why is not None and "vrf red's" in why[0], f"taken: {why}")

print("\n=== the model: a file, written back, and the delta ===")
FILE = ("! cgnat\nvrf svc table 30\nvrf red table 10\n"
        "interface host-veth-red\n vrf 10\n exit\n"
        "vrf blue table 10\nvrf prisma-x table 5\nvrf q\n"
        "! routing\nvrf red\n exit-vrf\n"
        "router bgp 65000 vrf red\n exit\n")
c = Config.parse(FILE)
check(c.vrf_devices == {"red": 10, "svc": 30},
      f"both devices read: {c.vrf_devices}")
check([u[1] for u in c.unknown] == ["vrf blue table 10",
                                    "vrf prisma-x table 5", "vrf q"],
      f"a table twice, a reserved name, no table: {c.unknown}")
check("vrf red" in c.routing and "router bgp 65000 vrf red" in c.routing,
      "FRR's `vrf red` block stays FRR's, in the routing half")
text = c.text()
rows = text.splitlines()
check(rows.index("vrf red table 10") < rows.index("vrf svc table 30")
      < rows.index("interface host-veth-red"),
      "written in name order, ahead of the interfaces whose tables they are")
check(Config.parse(text).vrf_devices == c.vrf_devices,
      "and read back as written")
d = Config().delta_to(c)
check(d["vrfdev_add"] == {"red": 10, "svc": 30} and d["vrfdev_del"] == [],
      f"a new box makes both: {d['vrfdev_add']} {d['vrfdev_del']}")
moved = Config.parse(text)
moved.vrf_devices["red"] = 11
del moved.vrf_devices["svc"]
d = c.delta_to(moved)
check(d["vrfdev_add"] == {"red": 11} and d["vrfdev_del"] == ["red", "svc"],
      f"a new table is the device made again: {d['vrfdev_add']} "
      f"{d['vrfdev_del']}")
check(not any(c.delta_to(Config.parse(text))[k]
              for k in ("vrfdev_add", "vrfdev_del")),
      "the same file twice is no change")
check({"vrfdev_add", "vrfdev_del"} <= set(prismash.DELTA_ADMIN),
      "and it is an administrator's change, not a CGNAT operator's")

print("\n=== the doors: the shapes ===")
for argv, verb in (
        (["ip", "link", "add", "red", "type", "vrf", "table", "10"],
         "make a VRF device"),
        (["ip", "link", "del", "dev", "red"], "remove a VRF device"),
        (["ip", "link", "set", "dev", "vppred0", "master", "red"],
         "put a Linux interface in a VRF"),
        (["ip", "link", "set", "dev", "vppred0", "nomaster"],
         "take a Linux interface out of its VRF"),
        (["ip", "link", "set", "dev", "red", "up"],
         "set a Linux interface up or down")):
    check(doors.verb_of(argv) == verb, f"{' '.join(argv)}: {verb}")
check(doors.LINKS_ARGV in doors.QUERIES,
      "the link list is a question any account may ask")
for argv, why in (
        (["ip", "link", "add", "prisma-bp", "type", "vrf", "table", "128"],
         "the backplane's VRF made again"),
        (["ip", "link", "add", "red", "type", "vrf", "table", "254"],
         "a VRF on the main table"),
        (["ip", "link", "add", "red", "type", "bridge", "table", "10"],
         "a bridge"),
        (["ip", "link", "add", "red", "type", "vrf", "table", "10", "x"],
         "a word more"),
        (["ip", "link", "del", "dev", "prisma-bp"], "the backplane's VRF"),
        (["ip", "link", "del", "dev", "-x"], "an option for a device"),
        (["ip", "link", "del", "red"], "without dev"),
        (["ip", "link", "set", "dev", "bp0", "master", "prisma-bp"],
         "into the backplane's VRF"),
        (["ip", "link", "set", "dev", "-x", "master", "red"],
         "an option for a member"),
        (["ip", "link", "set", "dev", "vppred0", "master", "../red"],
         "a path for a VRF"),
        (["ip", "link", "set", "dev", "vppred0", "nomaster", "red"],
         "a word after nomaster")):
    check(doors.verb_of(argv) is None, f"refused: {why}")

print("\n=== ip_links: what the host's list says of each device ===")
links = doors.ip_links(LINKS)
check(links["vppred0"] == {"master": "red", "vrf": None, "vrf_slave": 10},
      f"a member: {links['vppred0']}")
check(links["red"] == {"master": None, "vrf": 10, "vrf_slave": None},
      f"a VRF: {links['red']}")
check(links["veth-mgmt"]["master"] == "bp0"
      and links["veth-mgmt"]["vrf_slave"] is None,
      f"a bridge's member, its peer suffix off: {links['veth-mgmt']}")
check(links["vppsvc0"] == {"master": None, "vrf": None, "vrf_slave": None},
      "a TAP in nothing")
check(doors.ip_links("") == {} and doors.ip_links(None) == {},
      "nothing is no devices")


class Host:
    """The host the daemon asks: `ip -d -o link show dev X` from LINKS."""

    def __init__(self, text=LINKS):
        self.links = doors.ip_links(text)
        self.text = text
        self.ran = []

    def __call__(self, argv, stdin=None, timeout=30):
        self.ran.append(list(argv))
        if list(argv[:5]) == ["ip", "-d", "-o", "link", "show"] \
                and argv[5:6] == ["dev"]:
            rows = [r for r in self.text.splitlines()
                    if r.split(": ")[1].split("@")[0] == argv[6]]
            return (0, rows[0] + "\n") if rows else \
                (1, f'Device "{argv[6]}" does not exist.\n')
        return 0, ""


print("\n=== guard_of: the host asked before the door opens ===")
h = Host()
check(doors._link("red", h)["vrf"] == 10 and doors._link("nope", h) is None,
      "_link: one device read, and none for a name the host lacks")
for argv, needle in (
        (["ip", "link", "del", "dev", "vppsvc0"], "not a VRF device"),
        (["ip", "link", "del", "dev", "nope"], "not a VRF device"),
        (["ip", "link", "set", "dev", "bp0", "master", "red"],
         "in prisma-bp, which is not a tenant VRF"),
        (["ip", "link", "set", "dev", "bp0", "nomaster"],
         "in prisma-bp"),
        (["ip", "link", "set", "dev", "veth-mgmt", "master", "red"],
         "in bp0, which is not a tenant VRF"),
        (["ip", "link", "set", "dev", "red", "master", "red"],
         "is a VRF device"),
        (["ip", "link", "set", "dev", "vppsvc0", "nomaster"], "in no VRF"),
        (["ip", "link", "set", "dev", "vppsvc0", "master", "vppred0"],
         "vppred0 is not a VRF device"),
        (["ip", "link", "set", "dev", "prisma-x", "master", "red"],
         "the box's own"),
        (["ip", "link", "set", "dev", "nope", "master", "red"],
         "no such device")):
    why = doors.guard_of(argv, h)
    check(why is not None and needle in why, f"{' '.join(argv[3:])}: {why}")
for argv in (["ip", "link", "del", "dev", "red"],
             ["ip", "link", "set", "dev", "vppsvc0", "master", "red"],
             ["ip", "link", "set", "dev", "vppred0", "nomaster"],
             ["ip", "link", "set", "dev", "vppred0", "up"],
             ["hostname"]):
    check(doors.guard_of(argv, h) is None, f"opened: {' '.join(argv)}")

print("\n=== answer(): the guard, after the shape and the uid ===")
h = Host()
r = doors.answer('{"argv": ["ip", "link", "del", "dev", "vppsvc0"]}', 0,
                 execute=h)
check(r["rc"] == 126 and "remove a VRF device: vppsvc0 is not a VRF" in
      r["out"] and h.ran == [["ip", "-d", "-o", "link", "show", "dev",
                              "vppsvc0"]],
      f"a TAP is not removed, and only the read ran: {r} {h.ran}")
h = Host()
r = doors.answer('{"argv": ["ip", "link", "del", "dev", "red"]}', 1000,
                 execute=h)
check(r["rc"] == 126 and "only root" in r["out"] and h.ran == [],
      "another account is refused before the host is asked")
h = Host()
r = doors.answer('{"argv": ["ip", "link", "del", "dev", "red"]}', 0,
                 execute=h)
check(r["rc"] == 0 and h.ran[-1] == ["ip", "link", "del", "dev", "red"],
      f"root removes a VRF: {h.ran}")
h = Host()
r = doors.answer('{"argv": ["ip", "-d", "-o", "link", "show"]}', 1000,
                 execute=h)
check(r["rc"] == 0 and h.ran == [["ip", "-d", "-o", "link", "show"]],
      "the list is any account's to read")


class Runner:
    """The System seam: every argv kept, LINKS answered for the list."""

    def __init__(self, links=LINKS, refuse=()):
        self.asked = []
        self.links = links
        self.refuse = refuse

    def __call__(self, argv, stdin=None, timeout=30):
        self.asked.append(list(argv))
        if tuple(argv) == doors.LINKS_ARGV:
            return 0, self.links
        if any(list(argv[:len(p)]) == list(p) for p in self.refuse):
            return 2, "RTNETLINK answers: Operation not supported\n"
        return 0, ""


class Dp(prismash.Vpp):
    """The dataplane: two pairs, red's TAP and svc's."""

    TRACE = False

    def run(self, cmd):
        if cmd == "show lcp":
            return 0, ("itf-pair: [0] host-veth-red tap4096 vppred0 5 "
                       "type tap\n"
                       "itf-pair: [1] host-veth-svc tap4097 vppsvc0 6 "
                       "type tap\n")
        if cmd == "show interface":
            return 0, ("              Name               Idx    State  "
                       "MTU (L3/IP4/IP6/MPLS)\n"
                       "host-veth-red                     1      up    "
                       "      1500/0/0/0\n"
                       "host-veth-svc                     2      up    "
                       "      1500/0/0/0\n"
                       "tap4096                           3      up    "
                       "      1500/0/0/0\n"
                       "tap4097                           4      up    "
                       "      1500/0/0/0\n")
        return 0, ""


def shell(runner=None, candidate=None):
    """A Shell over the stubs, at the top of configuration mode."""
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT}
    sh._prompt_name = prismash.HOSTNAME_DEFAULT
    sh.dp = Dp()
    sh.sys = system.System(runner=runner or Runner())
    sh.mode = "config"
    sh.frr_ctx = []
    sh.nat_ctx = []
    sh.candidate = candidate if candidate is not None else Config()
    return sh


print("\n=== the prompt: vrf <name> table <id> ===")
sh = shell()
sh.do_config("vrf red table 10")
sh.do_config("vrf svc table 30")
check(sh.candidate.vrf_devices == {"red": 10, "svc": 30},
      f"staged as typed: {sh.candidate.vrf_devices}")
check(sh.candidate.routing == [], "and nothing of it handed to FRR")
for typed, needle in (("vrf blue table 10", "table 10 is vrf red's"),
                      ("vrf prisma-bp table 128", "is the box's own"),
                      ("vrf red table 254", "the kernel's own"),
                      ("vrf red table", "usage: vrf <name> table <id>"),
                      ("vrf red table 10 x", "usage: vrf <name> table <id>"),
                      ("no vrf red table 11", "usage: no vrf red table [10]"),
                      ("no vrf red table 10 x", "usage: no vrf red table")):
    with spoken() as o:
        sh.do_config(typed)
    check(needle in o.getvalue() and narrow(o.getvalue())
          and sh.candidate.vrf_devices == {"red": 10, "svc": 30},
          f"{typed!r} refused, nothing changed: {o.getvalue().strip()!r}")
check(sh.vrf_device_statement(["vrf", "blue", "table", "20"])
      and sh.candidate.vrf_devices.pop("blue") == 20,
      "vrf_device_statement: the words as do_config splits them")
sh.do_config("no vrf svc table 30")
sh.do_config("no vrf red table")
check(sh.candidate.vrf_devices == {},
      "'no vrf <name> table', with its table or without, removes it")
with spoken():
    sh.do_config("vrf red")
check(sh.candidate.routing[-1:] == ["vrf red"],
      f"FRR's `vrf red` block is still FRR's: {sh.candidate.routing}")
# NOT in the `?` tree, and on purpose: the marker on `show running-config`
# reads that tree, and a `vrf` word there would call FRR's `vrf red` block
# checked when nothing here reads its statements
# (test_prismash_complete.py, "a block with no grammar says so").
check("vrf" not in prismash.TREE["config"],
      "and FRR's `vrf` block is still marked as passed through unread")

print("\n=== device_vrf_devices: read back off the host ===")
sh = shell()
check(sh.device_vrf_devices() == {"red": 10},
      f"red, and the box's own left out: {sh.device_vrf_devices()}")
check(sh.device_vrf_devices(everything=True) == {"red": 10,
                                                 "prisma-bp": 128},
      "the box's own when asked for everything")
check(shell(Runner(links="")).device_vrf_devices() == {},
      "no list, no devices")

print("\n=== vrf_tables_free: the backplane's table is not a tenant's ===")
c = Config()
c.vrf_devices = {"mgmt": 128, "red": 10}
sh = shell()
with spoken() as o:
    ok = sh.vrf_tables_free(c)
check(not ok and "vrf mgmt: table 128 is prisma-bp's" in o.getvalue()
      and "vrf red" not in o.getvalue() and narrow(o.getvalue()),
      f"refused, with the VRF that holds it: {o.getvalue().strip()!r}")
r = Runner()
check(shell(r).vrf_tables_free(Config()) and r.asked == [],
      "a target with no VRF devices asks the host nothing")

print("\n=== apply_vrf_devices: removed, made, up, then the members ===")
# `old` on the host, a VRF with nothing in it, as the list prints a device.
OLD = ("12: old: <NOARP,MASTER,UP,LOWER_UP> mtu 65575 qdisc noqueue state UP "
       "mode DEFAULT group default qlen 1000\\    link/ether "
       "8a:5e:6b:89:51:b2 brd ff:ff:ff:ff:ff:ff promiscuity 0 \\    vrf "
       "table 40 addrgenmode eui64 numtxqueues 1\n")
r = Runner(links=LINKS.replace("master red ", "").replace(
    "vrf_slave table 10 ", "") + OLD)
sh = shell(r)
t = Config()
t.vrf_devices = {"red": 10, "svc": 30}
t.vrfs = {"host-veth-red": 10, "host-veth-svc": 30}
d = {"vrfdev_add": {"svc": 30}, "vrfdev_del": ["old"]}
check(sh.apply_vrf_devices(d, t), "applied")
said = [a for a in r.asked if tuple(a) != doors.LINKS_ARGV]
check(said[:3] == [["ip", "link", "del", "dev", "old"],
                   ["ip", "link", "add", "svc", "type", "vrf", "table", "30"],
                   ["ip", "link", "set", "dev", "svc", "up"]],
      f"in that order: {said[:3]}")
check(["ip", "link", "set", "dev", "vppred0", "master", "red"] in r.asked
      and ["ip", "link", "set", "dev", "vppsvc0", "master", "svc"]
      in r.asked, "each TAP into its table's VRF")
for cmd in r.asked:
    check(doors.verb_of(cmd) is not None or tuple(cmd) in doors.QUERIES,
          f"through a door: {' '.join(cmd)}")
for refuse, needle in (
        ((["ip", "link", "del"]), "vrf old: the device was not removed"),
        ((["ip", "link", "add"]), "vrf svc: the device was not made")):
    sh = shell(Runner(links=LINKS + OLD, refuse=[refuse]))
    with spoken() as o:
        ok = sh.apply_vrf_devices(d, t)
    check(not ok and needle in o.getvalue() and narrow(o.getvalue()),
          f"a refusal stops it: {o.getvalue().strip()!r}")
r = Runner(links=LINKS.replace("master red ", "").replace(
    "vrf_slave table 10 ", ""))
sh = shell(r)
check(sh.apply_vrf_devices(d, t)
      and ["ip", "link", "del", "dev", "old"] not in r.asked,
      "a device already gone is not removed again")
r = Runner()
sh = shell(r)
check(sh.apply_vrf_devices({"vrfdev_add": {"red": 10}, "vrfdev_del": ["red"]},
                           t)
      and not [a for a in r.asked if a[:3] == ["ip", "link", "del"]
               or a[:3] == ["ip", "link", "add"]],
      "and one make_vrf_devices() already made again is left as it is")

print("\n=== enslave_vrf_members: converged, both ways ===")
r = Runner()
sh = shell(r)
t = Config()
t.vrf_devices = {"red": 10}
t.vrfs = {"host-veth-red": 10}
check(sh.enslave_vrf_members(t)
      and [a for a in r.asked if a[:3] == ["ip", "link", "set"]] == [],
      f"red's TAP already in red, svc's in nothing: nothing to do")
r = Runner()
sh = shell(r)
t.vrfs = {}
check(sh.enslave_vrf_members(t)
      and ["ip", "link", "set", "dev", "vppred0", "nomaster"] in r.asked,
      "an interface moved out of the table has its TAP taken out")
r = Runner()
sh = shell(r)
t.vrf_devices = {}
t.vrfs = {"host-veth-red": 10}
check(sh.enslave_vrf_members(t)
      and ["ip", "link", "set", "dev", "vppred0", "nomaster"] in r.asked,
      "and so does one whose VRF device is no longer stated")
r = Runner(links=LINKS.replace("master red ", "master bp0 ").replace(
    "vrf_slave table 10 ", ""), refuse=[["ip", "link", "set"]])
sh = shell(r)
t.vrf_devices = {"red": 10}
check(sh.enslave_vrf_members(t)
      and [a for a in r.asked if a[:3] == ["ip", "link", "set"]] == [],
      "a TAP in a bridge is left where it is")
r = Runner(links=LINKS.replace("master red ", ""), refuse=[["ip", "link",
                                                            "set"]])
sh = shell(r)
with spoken() as o:
    ok = sh.enslave_vrf_members(t)
check(not ok and "interface host-veth-red: vppred0 was not put in red" in
      o.getvalue() and narrow(o.getvalue()),
      f"a refused member stops the commit: {o.getvalue().strip()!r}")
r = Runner(links=LINKS, refuse=[["ip", "link", "set"]])
sh = shell(r)
t.vrfs = {}
with spoken() as o:
    ok = sh.enslave_vrf_members(t)
check(not ok and "vppred0 was not taken out of red" in o.getvalue(),
      f"and a refused release: {o.getvalue().strip()!r}")

print("\n=== #595: zebra's answers, read ===")
# Shapes measured on FRR 10.7.1: `show interface <if> json`, and `show ip
# route vrf <name> json` - a route of an interface's own, the kernel's beside
# it, a static, and a static whose next hop went (neither selected nor
# installed).
ZIF = {"vppred0": {"vrfName": "red", "ipAddresses": [
    {"address": "10.10.0.1/24"}, {"address": "fe80::fe:ff:fe00:1/64"}]}}
ZRT = {"10.10.0.0/24": [{"protocol": "connected", "selected": True,
                         "installed": True,
                         "nexthops": [{"interfaceName": "vppred0"}]}],
       "2001:db8:a::/64": [{"protocol": "kernel", "nexthops": [
           {"interfaceName": "vppred0"}]}],
       "10.20.0.0/24": [{"protocol": "connected", "installed": True,
                         "nexthops": [{"interfaceName": "vppsvc0"}]}],
       "0.0.0.0/0": [{"protocol": "static", "selected": True,
                      "installed": True,
                      "nexthops": [{"ip": "10.10.0.2",
                                    "interfaceName": "vppred0"}]}],
       "fe80::/64": [{"protocol": "connected", "nexthops": [
           {"interfaceName": "vppred0"}]}]}
check(prismash.zebra_json('{"a": 1}') == {"a": 1}
      and prismash.zebra_json("{}") == {}
      and prismash.zebra_json("% VRF red not active") is None
      and prismash.zebra_json("[1]") is None
      and prismash.zebra_json("") is None,
      "zebra_json: a JSON object, and None for anything else")
check(prismash.zebra_if_vrf(ZIF, "vppred0") == "red"
      and prismash.zebra_if_vrf(ZIF, "vppsvc0") is None
      and prismash.zebra_if_vrf(None, "vppred0") is None,
      "zebra_if_vrf: the VRF zebra holds it in, None when it does not know")
check(prismash.zebra_held_routes(ZRT, "vppred0")
      == ["10.10.0.0/24", "2001:db8:a::/64"]
      and prismash.zebra_held_routes(ZRT, "vppsvc0") == ["10.20.0.0/24"]
      and prismash.zebra_held_routes(None, "vppred0") == [],
      "zebra_held_routes: its own routes through it - not a static, not "
      "another's, not the link-local")
settled = {"0.0.0.0/0": [{"protocol": "static", "nexthops": [
    {"ip": "10.10.0.2"}]}]}
check(prismash.zebra_vrf_unsettled(ZRT)
      == ["0.0.0.0/0", "10.10.0.0/24", "10.20.0.0/24", "2001:db8:a::/64",
          "fe80::/64"]
      and prismash.zebra_vrf_unsettled(settled) == []
      and prismash.zebra_vrf_unsettled({}) == []
      and prismash.zebra_vrf_unsettled(None) == [],
      "zebra_vrf_unsettled: an interface's routes or one in the FIB; a "
      "static with no next hop is settled")


class Zebra:
    """FRR's vtysh as the VTY asks it: `answers` by command, each a list
    taken one per ask (the last kept), and every command in `log`."""

    def __init__(self, answers=None, log=None, rc=0):
        self.answers = {k: list(v) for k, v in (answers or {}).items()}
        self.log = log if log is not None else []
        self.rc = rc

    def show(self, cmd):
        self.log.append(("zebra", cmd))
        got = self.answers.get(cmd, ["{}"])
        return self.rc, got.pop(0) if len(got) > 1 else got[0]


@contextlib.contextmanager
def pinned_clock(step=1.0):
    """time.monotonic advancing `step` per sleep, and no real sleeping."""
    now = [100.0]
    saved = (prismash.time.monotonic, prismash.time.sleep)
    prismash.time.monotonic = lambda: now[0]
    prismash.time.sleep = lambda s: now.__setitem__(0, now[0] + step)
    try:
        yield now
    finally:
        prismash.time.monotonic, prismash.time.sleep = saved


def recorded(sh):
    """The events the shell records, kept instead of written."""
    kept = []
    sh.record_event = lambda sev, text, **k: kept.append((sev, text, k))
    return kept


print("\n=== #595: zebra_settle: until zebra caught up, bounded, said ===")
sh = shell()
kept = recorded(sh)
left = [["10.10.0.0/24"], ["10.10.0.0/24"], []]
with pinned_clock() as now, spoken() as o:
    ok = sh.zebra_settle("interface x", lambda: left.pop(0))
check(ok and left == [] and now[0] == 102.0 and o.getvalue() == ""
      and kept == [], f"waits, polling, until nothing is pending: {now[0]}")
asked = []
os.environ["PRISMA_VRF_SETTLE_S"] = "0"
try:
    check(sh.zebra_settle("x", lambda: asked.append(1) or ["y"])
          and asked == [], "PRISMA_VRF_SETTLE_S=0: the box before #595, "
          "which did not wait")
    os.environ["PRISMA_VRF_SETTLE_S"] = "3"
    with pinned_clock() as now, spoken() as o:
        ok = sh.zebra_settle("vrf red",
                             lambda: ["a/1", "b/2", "c/3", "d/4"])
finally:
    del os.environ["PRISMA_VRF_SETTLE_S"]
check(not ok and now[0] == 103.0, f"bounded by the setting: {now[0]}")
check("warning: zebra had not caught up with a move between VRFs" in o.getvalue()
      and "vrf red: after 3 s it still held" in o.getvalue()
      and "a/1, b/2, c/3 and more" in o.getvalue() and narrow(o.getvalue()),
      f"said: {o.getvalue()!r}")
check(kept and kept[0][0] == "warning" and kept[0][2] == {"kind": "vrf-settle"}
      and "still holding a/1, b/2, c/3 and more" in kept[0][1],
      f"and recorded for show log: {kept}")
with pinned_clock() as now, spoken():
    sh.zebra_settle("x", lambda: ["z"])
check(now[0] == 100.0 + prismash.Shell.ZEBRA_SETTLE_S,
      f"10 s when not set: {now[0]}")

print("\n=== #595: what zebra holds, asked of zebra ===")
sh = shell()
sh.frr = Zebra({"show interface vppred0 json": [json.dumps(ZIF)],
                "show ip route vrf red 10.10.0.0/24 json":
                    [json.dumps({"10.10.0.0/24": ZRT["10.10.0.0/24"]})]})
check(sh.zebra_ask("show interface vppred0 json") == ZIF
      and Zebra(rc=1).show("x")[0] == 1
      and shell().zebra_ask("show interface x json") is None,
      "zebra_ask: its answer, None when FRR could not be asked")
sh.frr.log.clear()
check(sh.zebra_if_held("vppred0", ["10.10.0.1/24"])
      == ["10.10.0.1/24", "10.10.0.0/24"],
      "zebra_if_held: the address on the interface and its route")
check([c for _, c in sh.frr.log]
      == ["show interface vppred0 json",
          "show ip route vrf red 10.10.0.0/24 json",
          "show ip route vrf red 10.10.0.1/32 json"],
      f"asked by prefix in the VRF zebra holds it in: {sh.frr.log}")
sh.frr = Zebra({"show interface vppred0 json": [json.dumps(
    {"vppred0": {"vrfName": "default", "ipAddresses": []}})]})
check(sh.zebra_if_held("vppred0", ["2001:db8:a::1/64", "bad"]) == []
      and ("zebra", "show ipv6 route vrf default 2001:db8:a::/64 json")
      in sh.frr.log
      and ("zebra", "show ipv6 route vrf default 2001:db8:a::1/128 json")
      in sh.frr.log,
      f"nothing held, IPv6 asked as IPv6, a non-address skipped: "
      f"{sh.frr.log}")
check(shell().zebra_if_held("vppred0", ["10.10.0.1/24"]) == [],
      "and nothing to wait for when zebra cannot be asked")
sh.frr = Zebra({"show interface vppred0 json": [json.dumps(ZIF)]})
check(sh.zebra_if_elsewhere("vppred0", "red") == []
      and sh.zebra_if_elsewhere("vppred0", None) == ["vppred0 in red"]
      and shell().zebra_if_elsewhere("vppred0", "red") == [],
      "zebra_if_elsewhere: [] once it is in the VRF asked (None: default)")
sh.frr = Zebra({"show ip route vrf red json": [json.dumps(ZRT)],
                "show ipv6 route vrf red json": [json.dumps(settled)]})
check(sh.zebra_vrf_held("red") == prismash.zebra_vrf_unsettled(ZRT)
      and shell().zebra_vrf_held("red") == [],
      "zebra_vrf_held: both families, nothing when it cannot be asked")

print("\n=== #595: the pairs, and a Linux end put in or taken out ===")
check(shell().pair_ends() == {"host-veth-red": "vppred0",
                              "host-veth-svc": "vppsvc0"},
      f"pair_ends: each dataplane interface's Linux end: "
      f"{shell().pair_ends()}")
sh = shell()
sh.dp.run = lambda cmd: (1, "")
check(sh.pair_ends() is None, "pair_ends: None when the dataplane says nothing")
r = Runner()
check(shell(r).set_linux_master("host-veth-svc", "vppsvc0", "red", None)
      and shell(r).set_linux_master("host-veth-red", "vppred0", None, "red")
      and r.asked == [["ip", "link", "set", "dev", "vppsvc0", "master", "red"],
                      ["ip", "link", "set", "dev", "vppred0", "nomaster"]],
      f"set_linux_master: in, and out: {r.asked}")
with spoken() as o:
    ok = shell(Runner(refuse=[["ip", "link", "set"]])).set_linux_master(
        "host-veth-red", "vppred0", None, "red")
check(not ok and "interface host-veth-red: vppred0 was not taken out of red"
      in o.getvalue() and narrow(o.getvalue()),
      f"set_linux_master: a refusal said: {o.getvalue().strip()!r}")


def moving(links=LINKS, answers=None):
    """A shell whose addresses, host and zebra all write to one log."""
    log = []
    r = Runner(links=links)
    sh = shell(r)
    sh.sys = system.System(runner=lambda a, stdin=None, timeout=30:
                           log.append(("host", " ".join(a))) or r(a))
    sh.frr = Zebra(answers, log)
    sh.set_address = lambda i, a, add: log.append(
        ("addr", f"{i} {a} {'on' if add else 'off'}")) or True
    return sh, log


print("\n=== #595: move_linux_end: bare, zebra caught up each side ===")
GONE = json.dumps({"vppred0": {"vrfName": "red", "ipAddresses": []}})
OUT = json.dumps({"vppred0": {"vrfName": "default", "ipAddresses": []}})
sh, log = moving(answers={"show interface vppred0 json":
                          [json.dumps(ZIF), GONE, OUT],
                          "show ip route vrf red 10.10.0.0/24 json": ["{}"]})
with pinned_clock(), spoken():
    ok = sh.move_linux_end("host-veth-red", None, ["10.10.0.1/24"],
                           reapply=True)
steps = [w for k, w in log if k == "addr"
         or w.startswith(("ip link set", "show interface"))]
check(ok and steps == ["host-veth-red 10.10.0.1/24 off",
                       "show interface vppred0 json",
                       "show interface vppred0 json",
                       "ip link set dev vppred0 nomaster",
                       "show interface vppred0 json",
                       "host-veth-red 10.10.0.1/24 on"],
      f"off, zebra lets go of it, out, zebra has it out, on: {steps}")
sh, log = moving(answers={"show interface vppsvc0 json": [OUT.replace(
    "vppred0", "vppsvc0"), json.dumps({"vppsvc0": {"vrfName": "red"}})]})
with pinned_clock() as now, spoken():
    ok = sh.move_linux_end("host-veth-svc", "red", ["10.20.0.1/24"])
check(ok and not [w for k, w in log if k == "addr"]
      and log[-2:] == [("host", "ip link set dev vppsvc0 master red"),
                       ("zebra", "show interface vppsvc0 json")],
      f"addresses the caller's when not reapply, zebra asked after: {log}")
for links, want, why in ((LINKS, "red", "already where it goes"),
                         (LINKS.replace("master red ", "master bp0 ")
                          .replace("vrf_slave table 10 ", ""), None,
                          "in a bridge, left there"),
                         (LINKS.replace("7: vppred0", "7: vppx0"), None,
                          "with no Linux end on the host")):
    sh, log = moving(links=links)
    check(sh.move_linux_end("host-veth-red", want, ["10.10.0.1/24"],
                            reapply=True)
          and not [w for k, w in log if k == "addr"
                   or w.startswith("ip link set")],
          f"move_linux_end: nothing to move when {why}")
sh, log = moving()
sh.set_address = lambda i, a, add: False
check(not sh.move_linux_end("host-veth-red", None, ["10.10.0.1/24"],
                            reapply=True)
      and not [w for k, w in log if w.startswith("ip link set")],
      "a refused address stops it before the link moves")
sh, log = moving()
sh.sys = system.System(runner=Runner(refuse=[["ip", "link", "set"]]))
with spoken():
    check(not sh.move_linux_end("host-veth-red", None, []),
          "and so does a refused move")

print("\n=== #595: remove_vrf_device: the members out, zebra, the device ===")
sh, log = moving(answers={"show ip route vrf red json":
                          [json.dumps(ZRT), "{}"]})
run = Config()
run.addresses = {"host-veth-red": ["10.10.0.1/24"]}
with pinned_clock(), spoken():
    ok = sh.remove_vrf_device("red", run)
steps = [w for k, w in log if k == "addr" or w.startswith(
    ("ip link", "show ip route vrf red json"))]
check(ok and steps == ["host-veth-red 10.10.0.1/24 off",
                       "ip link set dev vppred0 nomaster",
                       "host-veth-red 10.10.0.1/24 on",
                       "show ip route vrf red json",
                       "show ip route vrf red json",
                       "ip link del dev red"],
      f"its member moved out bare, zebra settled, then removed: {steps}")
sh, log = moving()
with spoken():
    check(sh.remove_vrf_device("red") and ("addr", "host-veth-red "
          "10.10.0.1/24 off") not in log
          and ("host", "ip link del dev red") in log,
          "without the running configuration a member moves as it is")
sh, log = moving()
sh.sys = system.System(runner=Runner(refuse=[["ip", "link", "del"]]))
with spoken() as o:
    ok = sh.remove_vrf_device("svc")
check(not ok and "vrf svc: the device was not removed" in o.getvalue(),
      f"a refused removal said: {o.getvalue().strip()!r}")

print("\n=== #595: make_vrf_devices: before any interface moves ===")
r = Runner()
sh = shell(r)
check(sh.make_vrf_devices({"vrfdev_add": {"red": 10, "svc": 30},
                           "vrfdev_del": []}, Config())
      and [a for a in r.asked if tuple(a) != doors.LINKS_ARGV]
      == [["ip", "link", "add", "svc", "type", "vrf", "table", "30"],
          ["ip", "link", "set", "dev", "svc", "up"]],
      f"made and up, one already there left: {r.asked}")
sh, log = moving()
with spoken():
    ok = sh.make_vrf_devices({"vrfdev_add": {"red": 11},
                              "vrfdev_del": ["red"]}, Config())
said = [w for k, w in log if w.startswith(("ip link del", "ip link add",
                                           "ip link set"))]
check(ok and said == ["ip link set dev vppred0 nomaster",
                      "ip link del dev red",
                      "ip link add red type vrf table 11",
                      "ip link set dev red up"],
      f"one on another table: members out, removed, made again: {said}")
with spoken() as o:
    ok = shell(Runner(refuse=[["ip", "link", "add"]])).make_vrf_devices(
        {"vrfdev_add": {"svc": 30}, "vrfdev_del": []}, Config())
check(not ok and "vrf svc: the device was not made" in o.getvalue(),
      f"a refusal stops it: {o.getvalue().strip()!r}")

print("\n" + ("ALL TESTS PASSED" if not fail else f"{fail} FAILURES"))
sys.exit(1 if fail else 0)
