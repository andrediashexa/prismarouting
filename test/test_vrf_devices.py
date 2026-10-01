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
r = Runner(links=LINKS.replace("master red ", "").replace(
    "vrf_slave table 10 ", ""))
sh = shell(r)
t = Config()
t.vrf_devices = {"red": 10, "svc": 30}
t.vrfs = {"host-veth-red": 10, "host-veth-svc": 30}
d = {"vrfdev_add": {"svc": 30}, "vrfdev_del": ["old"]}
check(sh.apply_vrf_devices(d, t), "applied")
check(r.asked[:4] == [["ip", "link", "del", "dev", "old"],
                      ["ip", "link", "add", "svc", "type", "vrf", "table",
                       "30"],
                      ["ip", "link", "set", "dev", "svc", "up"],
                      list(doors.LINKS_ARGV)],
      f"in that order: {r.asked[:4]}")
check(["ip", "link", "set", "dev", "vppred0", "master", "red"] in r.asked
      and ["ip", "link", "set", "dev", "vppsvc0", "master", "svc"]
      in r.asked, "each TAP into its table's VRF")
for cmd in r.asked:
    check(doors.verb_of(cmd) is not None or tuple(cmd) in doors.QUERIES,
          f"through a door: {' '.join(cmd)}")
for refuse, needle in (
        ((["ip", "link", "del"]), "vrf old: the device was not removed"),
        ((["ip", "link", "add"]), "vrf svc: the device was not made")):
    sh = shell(Runner(refuse=[refuse]))
    with spoken() as o:
        ok = sh.apply_vrf_devices(d, t)
    check(not ok and needle in o.getvalue() and narrow(o.getvalue()),
          f"a refusal stops it: {o.getvalue().strip()!r}")

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

print("\n" + ("ALL TESTS PASSED" if not fail else f"{fail} FAILURES"))
sys.exit(1 if fail else 0)
