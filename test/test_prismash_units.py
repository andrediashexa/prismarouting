#!/usr/bin/env python3
"""
The module-level helpers of prismash.py, each called on its own (#390), and
below them the Shell's applying methods over recording stubs (batch H).

WHY A SUITE OF ITS OWN. The other prismash suites are organised by behaviour:
a commit, a refusal, a screen. A helper that one of those behaviours calls
is exercised by accident, and a helper none of them reaches is exercised by
nobody - which is what test/test_every_function.py reads, and it listed
these by name. Every box here calls one function directly, with the input
the function exists for, and reads what it returned or what it changed.
Every box was watched going red against a mutation of the rule it reads
before the mutation was taken back out; the mutation is named beside the
box, so a reader can repeat it.

NOTHING HERE TOUCHES THE MACHINE. The seams the product declares are used
for the three helpers that would otherwise read it: the version files and
the live-medium paths are module constants overridden per box, the account's
groups are read through `grp` and `pwd` modules swapped out under
sys.modules, and the host-key origin is a script of this suite's own handed
in through the `origin` argument. The batch H section at the end builds a
Shell over an API, a dataplane CLI, an Frr and a System runner that each
record what they were asked, points vtysh, the installer and the login shell
at scripts of this suite's own, and puts every file the methods write under
the suite's directory.
"""
import json
import os
import re
import sys
import shlex
import shutil
import tempfile
import types

_root = tempfile.mkdtemp(prefix="prismash-units-")
# Removed at exit (#566): every run left its directory behind, and 1,187 of
# them had filled 9.8 GB of the build VM's /tmp by 2026-10-04 - the disk ran
# out under a release gate. A run that dies by a signal still leaves one.
import atexit  # noqa: E402
atexit.register(shutil.rmtree, _root, True)
# Read at import: every path prismash.py takes from the environment points into
# this suite's own directory, so no box here reads /etc or /usr/share on the
# machine running it.
os.environ["PRISMA_CONFIG"] = os.path.join(_root, "prisma-routing.conf")
os.environ["PRISMA_CONFIRM"] = os.path.join(_root, "confirm-pending")
os.environ["PRISMA_ROLLBACK"] = os.path.join(_root, "rollback")
os.environ["PRISMA_VERSION_FILE"] = os.path.join(_root, "VERSION-stamp")
os.environ["PRISMA_LIVE_SQUASHFS"] = os.path.join(_root, "filesystem.squashfs")
os.environ["PRISMA_INSTALLER"] = os.path.join(_root, "prisma-routing-install")
os.environ["PRISMA_SSH_HOST_KEY_ORIGIN"] = os.path.join(_root, "no-origin")
with open(os.environ["PRISMA_VERSION_FILE"], "w") as _f:
    _f.write("0.99.7\n")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src",
                                "prismash"))
import prismash      # noqa: E402
from config import Pool      # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nat_fixture import in_nat_block      # noqa: E402
import system      # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


def script(name, body, mode=0o755):
    """A program of this suite's own, under its own directory."""
    path = os.path.join(_root, name)
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, mode)
    return path


# ------------------------------------------------------------- the version

print("=== prisma_routing_version_source: which file answered ===")
# Mutation shown red: return VERSION_FROM_TREE for the stamp.
v, src = prismash.prisma_routing_version_source()
check((v, src) == ("0.99.7", prismash.VERSION_FROM_STAMP),
      f"the installed stamp answers first, as 'stamp': {(v, src)!r}")
saved = prismash.VERSION_PATH
try:
    prismash.VERSION_PATH = os.path.join(_root, "no-such-stamp")
    v, src = prismash.prisma_routing_version_source()
    with open(os.path.join(os.path.dirname(prismash.__file__), "..", "..",
                           "VERSION")) as f:
        tree_version = f.read().strip()
    check((v, src) == (tree_version, prismash.VERSION_FROM_TREE),
          f"with no stamp the tree's VERSION answers, as 'tree': {(v, src)!r}")
    empty = os.path.join(_root, "VERSION-empty")
    open(empty, "w").close()
    prismash.VERSION_PATH = empty
    v, src = prismash.prisma_routing_version_source()
    check(src == prismash.VERSION_FROM_TREE and v == tree_version,
          f"an empty stamp states no version and falls through: {(v, src)!r}")
finally:
    prismash.VERSION_PATH = saved
check(prismash.prisma_routing_version() == "0.99.7",
      "prisma_routing_version() is the text half of the same reading")
ok("stamp, then tree, and an empty file is not a version")

print("\n=== version_split: only an installed stamp can disagree ===")
# Mutation shown red: drop the `source == VERSION_FROM_STAMP` term.
STAMP, TREE = prismash.VERSION_FROM_STAMP, prismash.VERSION_FROM_TREE
check(prismash.version_split("0.24.0", "0.25.0", STAMP) is True,
      "a stamp that differs from the loaded build is a split")
check(prismash.version_split("0.25.0", "0.25.0", STAMP) is False,
      "the same build is not a split")
check(prismash.version_split("0.24.0", "0.25.0", TREE) is False,
      "a source tree names the next release, not a build: no split")
check(prismash.version_split(None, "0.25.0", STAMP) is False,
      "a dataplane that never spoke states no build")
check(prismash.version_split("", "0.25.0", STAMP) is False,
      "a dataplane without the plugin states no build")
ok("True only for stamp + a build that is stated + a build that differs")

# --------------------------------------------------------- the interface

print("\n=== dataplane_statement: the whole keyword, not its first word ===")
# Mutation shown red: compare words[:1] instead of words[:len(stmt)].
check(prismash.dataplane_statement(["ipv4", "address", "10.0.0.1/24"])
      == "ipv4 address", "ipv4 address is the dataplane's")
check(prismash.dataplane_statement(["ipv6", "nd", "suppress-ra"])
      == "ipv6 nd suppress-ra", "the three-word statement is matched whole")
check(prismash.dataplane_statement(["ipv6", "nd", "ra-interval", "30"]) is None,
      "ipv6 nd <anything else> is FRR's")
check(prismash.dataplane_statement(["mtu", "9000"]) == "mtu",
      "a one-word statement carries its argument")
check(prismash.dataplane_statement(["ip", "ospf", "cost", "10"]) is None,
      "an FRR statement is None")
ok("each entry of INTERFACE_DATAPLANE is matched by its full length")

# --------------------------------------------------------------- the pool

print("\n=== _pool_no_item: a member the pool has, or a refusal ===")
# Mutation shown red: answer None for a member the pool lacks, passing over
# the typo in silence.
lst = ["10.0.0.0/8", "172.16.0.0/12"]
check(prismash._pool_no_item(lst, "inside", ["no", "inside"])
      == "usage: no inside <prefix>", "a missing prefix is a usage line")
check(prismash._pool_no_item(lst, "inside", ["no", "inside", "192.0.2.0/24"])
      == "the pool has no inside 192.0.2.0/24",
      "a member the pool does not have is refused by name")
check(lst == ["10.0.0.0/8", "172.16.0.0/12"], "a refusal removes nothing")
check(prismash._pool_no_item(lst, "inside", ["no", "inside", "10.0.0.0/8"])
      is None and lst == ["172.16.0.0/12"],
      f"the named member is removed and nothing is said: {lst}")
ok("usage, refusal by name, removal")

print("\n=== _pool_no_timeout: drop an override, or say which exist ===")
# Mutation shown red: leave p.timeouts untouched.
p = Pool("A")
p.timeouts = {"udp": 30, "icmp": 10}
usage = prismash._pool_no_timeout(p, ["no", "timeout", "bogus"])
check(usage is not None and usage.startswith("usage: no timeout <")
      and "udp" in usage, f"an unknown timeout names the list: {usage!r}")
check(prismash._pool_no_timeout(p, ["no", "timeout", "udp"]) is None
      and p.timeouts == {"icmp": 10}, f"udp override dropped: {p.timeouts}")
check(prismash._pool_no_timeout(p, ["no", "timeout", "udp"]) is None,
      "dropping an override already absent is the state asked for")
ok("the override goes and the others stay")

print("\n=== _pool_no_alg: the same, for an ALG ===")
# Mutation shown red: pop w[2] from p.timeouts instead of p.algs.
p = Pool("A")
p.algs = {"ftp": True, "rtsp": False}
usage = prismash._pool_no_alg(p, ["no", "alg"])
check(usage is not None and usage.startswith("usage: no alg <")
      and "ftp" in usage, f"a missing ALG names the list: {usage!r}")
check(prismash._pool_no_alg(p, ["no", "alg", "rtsp"]) is None
      and p.algs == {"ftp": True}, f"rtsp override dropped: {p.algs}")
ok("the ALG override goes and the other stays")

print("\n=== _pool_no_bypass: one prefix or the whole list, per direction ===")
# Mutation shown red: pick bypass_src for "destination".
p = Pool("A")
p.bypass_src = ["10.1.0.0/16", "10.2.0.0/16"]
p.bypass_dst = ["198.51.100.0/24"]
check(prismash._pool_no_bypass(p, ["no", "bypass", "sideways"])
      == "usage: no bypass <source|destination> [<prefix>]",
      "a direction that is neither is a usage line")
check(prismash._pool_no_bypass(p, ["no", "bypass", "source", "10.1.0.0/16"])
      is None and p.bypass_src == ["10.2.0.0/16"],
      f"one source prefix removed: {p.bypass_src}")
check(prismash._pool_no_bypass(p, ["no", "bypass", "source", "10.9.0.0/16"])
      == "the pool has no bypass source 10.9.0.0/16",
      "a prefix the list lacks is refused with the direction in it")
check(prismash._pool_no_bypass(p, ["no", "bypass", "destination"]) is None
      and p.bypass_dst == [] and p.bypass_src == ["10.2.0.0/16"],
      f"no prefix clears the destination list and only it: "
      f"{p.bypass_src} {p.bypass_dst}")
ok("source and destination are two lists, one prefix or all of them")

print("\n=== _pool_no_forward: one port names one forward per address ===")
# Mutation shown red: ignore `ip` when selecting the hit.
p = Pool("A")
p.forwards = [("tcp", 1100, "10.0.0.5", 22), ("tcp", 1100, "10.0.0.6", 22),
              ("udp", 5060, "10.0.0.7", 5060)]
check(prismash._pool_no_forward(p, ["no", "forward", "tcp"])
      == "usage: no forward <tcp|udp> <port> [to <private-ip> <port>]",
      "a missing port is a usage line")
check(prismash._pool_no_forward(p, ["no", "forward", "tcp", "2200"])
      == "the pool has no forward tcp 2200", "a port nobody forwards")
pair = prismash._pool_no_forward(p, ["no", "forward", "tcp", "1100"])
check(isinstance(pair, tuple) and len(pair) == 3
      and "more than one subscriber" in pair[0]
      and "10.0.0.5, 10.0.0.6" in pair[1]
      and pair[2].endswith(
          "no forward tcp 1100 to <private-ip> <private-port>"),
      f"two subscribers on one port: three rows and no removal: {pair!r}")
check(len(p.forwards) == 3, "the ambiguous ask removed nothing")
check(prismash._pool_no_forward(
        p, ["no", "forward", "tcp", "1100", "to", "10.0.0.6", "22"]) is None
      and p.forwards == [("tcp", 1100, "10.0.0.5", 22),
                         ("udp", 5060, "10.0.0.7", 5060)],
      f"'to <ip> <port>' names the subscriber, and only that one goes: "
      f"{p.forwards}")
check(prismash._pool_no_forward(p, ["no", "forward", "udp", "5060", "10.0.0.7"])
      is None and p.forwards == [("tcp", 1100, "10.0.0.5", 22)],
      f"the ip without 'to' also names the subscriber: {p.forwards}")
check(prismash._pool_no_forward(p, ["no", "forward", "tcp", "1100"]) is None
      and p.forwards == [], "one hit on the bare port is removed")
ok("usage, no such forward, an ambiguous pair, and the named subscriber")

print("\n=== _pool_no_clear: the handler it returns sets the omitted value")
# Mutation shown red: setattr(p, attr, None) instead of `value`.
p = Pool("A")
p.max_blocks = 40
go = prismash._pool_no_clear("max_blocks", 0)
check(go(p, ["no", "max-blocks"]) is None, "the handler says nothing")
check(p.max_blocks == 0, f"max_blocks is back at 0: {p.max_blocks!r}")
p.vrf = "7"
check(prismash._pool_no_clear("vrf", "")(p, None) is None and p.vrf == "",
      f"vrf is back at the empty string, not None: {p.vrf!r}")
ok("the field is set to exactly the value handed in")

print("\n=== the first allocation: in ports, sent in blocks, refused when "
      "it cannot be (#462)")
p = Pool("B")
p.block_size = "256"
p.mode = "dynamic"
p.max_blocks = 10
check(p.initial_blocks() == 0 and p.initial_fits() is None,
      "no first allocation: nothing sent, nothing refused")
p.initial_ports = 512
check(p.initial_blocks() == 2 and p.initial_fits() is None,
      f"512 ports of 256-port blocks is two blocks: {p.initial_blocks()}")
check("initial-blocks 2" in p.device_command()
      and p.api_args(True)["initial_blocks"] == 2
      and p.settings_api_args()["initial_blocks"] == 2,
      "and two is what the device command and both messages carry")
check(" initial-allocation 512" in p.lines(),
      "the file carries it in ports, as it was typed")
p.initial_ports = 300
check(p.initial_blocks() == 0
      and p.initial_fits() == "initial-allocation is a whole number of "
                              "256-port blocks, not 300",
      f"ports that are not whole blocks send nothing and are refused: "
      f"{p.initial_fits()!r}")
p.initial_ports = 4096
check(p.initial_fits() == "initial-allocation is above the 2560 ports "
                          "max-blocks 10 allows: 4096",
      f"above the cap: {p.initial_fits()!r}")
p.initial_ports = 512
p.mode = "deterministic"
check((p.initial_fits() or "").startswith("initial-allocation applies to "
                                          "dynamic pools only"),
      f"on a deterministic pool: {p.initial_fits()!r}")
ok("512 over 256-port blocks is 2; not whole, over the cap, not dynamic: "
   "refused")

# -------------------------------------------------------------- the roles


class _Group:
    def __init__(self, name, gid, mem=()):
        self.gr_name, self.gr_gid, self.gr_mem = name, gid, list(mem)


def caps_as(euid, user, groups, primary_gid, broken=False):
    """
    capabilities() with the account described by the arguments, read through
    `grp` and `pwd` modules of this suite's own under sys.modules.
    """
    fake_grp = types.ModuleType("grp")
    fake_pwd = types.ModuleType("pwd")
    by_gid = {g.gr_gid: g for g in groups}

    def getgrall():
        if broken:
            raise KeyError("nss is down")
        return list(groups)
    fake_grp.getgrall = getgrall
    fake_grp.getgrgid = lambda gid: by_gid[gid]
    fake_pwd.getpwuid = lambda uid: types.SimpleNamespace(pw_name=user)
    fake_pwd.getpwnam = lambda name: types.SimpleNamespace(pw_gid=primary_gid)
    real = {m: sys.modules.get(m) for m in ("grp", "pwd")}
    real_euid = os.geteuid
    sys.modules["grp"], sys.modules["pwd"] = fake_grp, fake_pwd
    os.geteuid = lambda: euid
    try:
        return prismash.capabilities()
    finally:
        os.geteuid = real_euid
        for m, mod in real.items():
            if mod is None:
                sys.modules.pop(m, None)
            else:
                sys.modules[m] = mod


print("\n=== capabilities: the system's groups, never anything stored ===")
# Mutation shown red: on the NSS exception return {CAP_ALL, CAP_CGNAT}.
BOTH = {prismash.CAP_ALL, prismash.CAP_CGNAT}
users = _Group("users", 100)
admin = _Group(system.GROUP_ADMIN, 1001, ["alice"])
cgnat = _Group(system.GROUP_CGNAT, 1002, ["bob"])
check(caps_as(0, "root", [users], 0) == BOTH | {prismash.CAP_OPERATOR},
      "root has both, and the system actions (#563)")
check(caps_as(1000, "alice", [users, admin, cgnat], 100) == BOTH,
      "a member of prisma-admin has both")
check(caps_as(1000, "bob", [users, admin, cgnat], 100)
      == {prismash.CAP_CGNAT}, "a member of prisma-cgnat has CGNAT only")
check(caps_as(1000, "carol", [users, admin, cgnat], 1002)
      == {prismash.CAP_CGNAT}, "a primary group counts as membership too")
check(caps_as(1000, "dave", [users, admin, cgnat], 100) == set(),
      "neither group is read-only")
check(caps_as(1000, "alice", [users, admin, cgnat], 100, broken=True)
      == set(), "an NSS that cannot describe the account grants nothing")
ok("root, prisma-admin, prisma-cgnat by member and by primary group, and NSS down")

print("\n=== role_name: the rank in the spelling the user block takes ===")
# Mutation shown red: answer ROLE_CGNAT for CAP_ALL.
check(prismash.role_name(BOTH) == system.ROLE_ADMIN, "both caps is admin")
check(prismash.role_name({prismash.CAP_CGNAT}) == system.ROLE_CGNAT,
      "CGNAT alone is the cgnat role")
check(prismash.role_name(set()) == "read-only", "no cap is read-only")
ok("three ranks, three names")

# ------------------------------------------------------------ the export

print("\n=== udp_export_without_collector: enable with nowhere to send ===")
# Mutation shown red: let `cgnat logging tcp <host>` satisfy the collector.
check(prismash.udp_export_without_collector(["cgnat logging enable"]) is True,
      "enable alone would be refused by the dataplane")
check(prismash.udp_export_without_collector(
        ["cgnat logging enable", "cgnat logging udp 10.0.0.9 4739"]) is False,
      "a UDP collector satisfies it")
check(prismash.udp_export_without_collector(
        ["cgnat logging enable", "cgnat logging udp disable"]) is True,
      "'udp disable' is not a collector")
check(prismash.udp_export_without_collector(
        ["cgnat logging enable", "cgnat logging tcp 10.0.0.9 4739"]) is True,
      "a TCP collector is the other transport and does not satisfy it")
check(prismash.udp_export_without_collector(
        ["cgnat logging udp 10.0.0.9 4739"]) is False,
      "without enable there is no export to refuse")
check(prismash.udp_export_without_collector([]) is False,
      "no statements ask for nothing")
ok("True only for enable with no UDP collector")

# ------------------------------------------------------- the live medium

print("\n=== running_from_live_medium: the squashfs and an installer ===")
# Mutation shown red: os.access(INSTALLER, os.R_OK) instead of X_OK.
sq = os.environ["PRISMA_LIVE_SQUASHFS"]
inst = os.environ["PRISMA_INSTALLER"]
check(prismash.running_from_live_medium() is False,
      "neither file: an installed box")
open(sq, "w").close()
check(prismash.running_from_live_medium() is False,
      "the medium without an installer is not one to install from")
with open(inst, "w") as f:
    f.write("#!/bin/sh\n")
os.chmod(inst, 0o644)
check(prismash.running_from_live_medium() is False,
      "an installer that cannot be executed is not one")
os.chmod(inst, 0o755)
check(prismash.running_from_live_medium() is True,
      "the medium and an executable installer: live")
os.unlink(sq)
check(prismash.running_from_live_medium() is False,
      "the installer alone is not the medium")
ok("both files, and the installer executable")

# ------------------------------------------------------------- the tree

print("\n=== _starts: completion ignores case, and only case ===")
# Mutation shown red: candidate.startswith(prefix) with no lower().
check(prismash._starts("TenGigabitEthernet0/8/0", "ten") is True,
      "the operator's lower case reaches the box's spelling")
check(prismash._starts("ens19", "ENS") is True, "and the other way round")
check(prismash._starts("ens19", "eth") is False, "a different name is not it")
check(prismash._starts("ens19", "ens19x") is False,
      "a prefix longer than the candidate is not it")
ok("case-insensitive prefix")

print("\n=== shaped_entry: a prefix is walked, a word is not (#436) ===")
# Mutation shown red: the shape test answering True for any word - `?` then
# offers `next-hop` after `banana`, and a typo is marked as read.
_node = prismash.STATIC_BLOCK_GRAMMAR
check(prismash.shaped_entry(_node, "192.168.0.0/24") is not None,
      "a prefix finds the <prefix> entry")
check(prismash.shaped_entry(_node, "2001:db8::/32") is not None,
      "and so does an IPv6 one")
check(prismash.shaped_entry(_node, "banana") is None,
      "a word that is not one does not")
check(prismash.shaped_entry(_node, "10.0.0.300/8") is None,
      "nor does a prefix that is no address")
check(prismash.shaped_entry({"exit": ("x", None)}, "10.0.0.0/8") is None,
      "and a node with no <prefix> in it matches nothing")
check(prismash.walk_from(_node, ["10.0.0.0/8", "next-hop"])[1]
      .startswith("<next-hop>"),
      "so the walk reaches next-hop's hint through the operator's prefix")
ok("the tree reads a prefix by its shape")

print("\n=== walk_from: where a walk of words ends in a grammar ===")
# Mutation shown red: return (None, None) for a string subtree, dropping
# the hint.
node = {"show": ("display", {
            "route": ("the table", "<prefix>"),
            "version": ("the build", None)}),
        "exit": ("leave", None)}
sub, hint = prismash.walk_from(node, [])
check(sub is node and hint is None, "no words: the node itself")
sub, hint = prismash.walk_from(node, ["show"])
check(isinstance(sub, dict) and set(sub) == {"route", "version"}
      and hint is None,
      f"'show' offers its subtree: {(sub, hint)!r}")
check(prismash.walk_from(node, ["show", "route"]) == (None, "<prefix>"),
      "a leaf with an argument offers its hint and nothing more")
check(prismash.walk_from(node, ["show", "version"]) == (None, None),
      "a leaf without one offers nothing")
check(prismash.walk_from(node, ["show", "bgp"]) == (None, None),
      "an unknown word is FRR territory from there")
check(prismash.walk_from(node, ["show", "version", "more"]) == (None, None),
      "words past a leaf reach nothing")
check(prismash.walk_from(None, ["show"]) == (None, None),
      "no node to walk from")
ok("subtree, hint, or nothing")

print("\n=== tree_walk: the same walk from a mode's root ===")
# Mutation shown red: walk TREE["config"] whatever the mode.
sub, hint = prismash.tree_walk("operational", ["system"])
check(isinstance(sub, dict) and set(sub) == {"reboot", "shutdown", "upgrade",
                                             "dataplane"}
      and hint is None, f"'system' in operational mode: {(sub, hint)!r}")
check(prismash.tree_walk("operational", ["clear", "ospf", "neighbor"])
      == (None, "<A.B.C.D>"), "a hint reached three words down")
check(prismash.tree_walk("operational", ["pool"]) == (None, None),
      "a config word is not in operational mode")
check(prismash.tree_walk("no-such-mode", ["show"]) == (None, None),
      "a mode the tree lacks offers nothing")
ok("a mode's root, then walk_from")

print("\n=== _tree_reads: did the tree read every word ===")
# Mutation shown red: `if node is None: return True`.
node = {"bgp": ("bgp", {
            "log-neighbor-changes": ("log", None),
            "router-id": ("id", "<A.B.C.D>")}),
        "exit": ("leave", None)}
check(prismash._tree_reads(node, ["bgp", "log-neighbor-changes"]) is True,
      "a statement the tree defines to its end")
check(prismash._tree_reads(node, ["bgp", "router-id", "10.0.0.1"]) is True,
      "an argument: the rest is its business")
check(prismash._tree_reads(node, ["bgp", "router-id", "10.0.0.1", "x"]) is True,
      "and however many words follow the argument")
check(prismash._tree_reads(node, ["bgp", "log-neighbor-changes", "x"]) is False,
      "a word after the statement ended was not read")
check(prismash._tree_reads(node, ["bgp", "bestpath"]) is False,
      "a word the tree has no entry for was not read")
check(prismash._tree_reads(node, ["bgp"]) is True,
      "a prefix of a statement: every word it has was read")
ok("True through a leaf and an argument, False past a leaf or off the tree")

# -------------------------------------------------------- the host keys

print("\n=== ssh_host_keys_handed_to_this_box: the program's lines ===")
# Mutation shown red: read stdout whatever the return code.
origin = script("origin-handed", "#!/bin/sh\n"
                "echo 'handed ssh_host_rsa_key ssh_host_ed25519_key'\n"
                "echo 'unreadable'\necho 'own 1'\n"
                "echo 'fix rm /etc/ssh/ssh_host_*_key'\n"
                "echo 'fix'\necho 'fix ssh-keygen -A'\n")
handed, fixes = prismash.ssh_host_keys_handed_to_this_box(origin)
check(handed == ["ssh_host_rsa_key", "ssh_host_ed25519_key"],
      f"the 'handed' line's names: {handed}")
check(fixes == ["rm /etc/ssh/ssh_host_*_key", "ssh-keygen -A"],
      f"every 'fix' line with a command on it, in order: {fixes}")
origin = script("origin-own", "#!/bin/sh\necho 'handed'\necho 'own 3'\n")
check(prismash.ssh_host_keys_handed_to_this_box(origin) == ([], []),
      "a box that made its own keys: nothing to say")
origin = script("origin-failed", "#!/bin/sh\n"
                "echo 'handed ssh_host_rsa_key'\nexit 1\n")
check(prismash.ssh_host_keys_handed_to_this_box(origin) == ([], []),
      "a program that failed is not believed, whatever it printed")
check(prismash.ssh_host_keys_handed_to_this_box(
        os.path.join(_root, "no-such-program")) == ([], []),
      "a program that is not there: silent")
ok("names and fixes, and silence for own keys, a failure, or no program")

# ------------------------------------------------------------ the routing

print("\n=== as_path_parts: the regex by the keyword, not by position ===")
# Mutation shown red: return (w[3], " ".join(w[5:])) positionally.
check(prismash.as_path_parts("bgp as-path access-list RX1 seq 5 permit ^65001$")
      == ("RX1", "^65001$"), "FRR's read-back with a seq")
check(prismash.as_path_parts("bgp as-path access-list RX1 permit ^65001$")
      == ("RX1", "^65001$"), "the form an operator types")
check(prismash.as_path_parts("bgp as-path access-list RX1 deny _65002_ .*")
      == ("RX1", "_65002_ .*"), "deny, and a regex with a space in it")
check(prismash.as_path_parts("bgp as-path access-list RX1 seq 5")
      == (None, None), "no permit or deny: not one")
check(prismash.as_path_parts("ip prefix-list PL permit 10.0.0.0/8")
      == (None, None), "another statement: not one")
ok("(name, regex) after permit or deny, else (None, None)")

print("\n=== interface_aliases: every name a pair answers to ===")
# Mutation shown red: drop `| {name}`.
pairs = {"vppwan0": ("host-veth-vpp", "tap4096", "vppwan0"),
         "tap4096": ("host-veth-vpp", "tap4096", "vppwan0")}
check(prismash.interface_aliases("vppwan0", pairs)
      == {"host-veth-vpp", "tap4096", "vppwan0"}, "the pair, itself included")
check(prismash.interface_aliases("ens19", pairs) == {"ens19"},
      "an interface in no pair answers to its own name")
check(prismash.interface_aliases("ens19", None) == {"ens19"},
      "and with no pairs at all")
ok("the pair's names plus the name asked")

print("\n=== ospf6_state: OSPFv3 gathered from its own statements ===")
# Mutation shown red: stop resetting `iface` on `router`, so the area under
# `router bgp` is charged to eth1.
routing = ["interface eth0",
           " ipv6 ospf6 area 0.0.0.0",
           " ipv6 ospf6 passive",
           " ip ospf area 0",
           "interface eth1",
           " ipv6 ospf6 mtu-ignore",
           "router bgp 65000",
           " ipv6 ospf6 area 0.0.0.1",
           "router ospf6",
           " ospf6 router-id 10.0.0.1"]
in_area, quiet, ignoring, has = prismash.ospf6_state(routing)
check(in_area == {"eth0": "0.0.0.0"}, f"areas by interface: {in_area}")
check(quiet == {"eth0"}, f"passive: {quiet}")
check(ignoring == {"eth1"}, f"mtu-ignore: {ignoring}")
check(has is True, "router ospf6 means OSPFv3 is configured")
check(prismash.ospf6_state(["interface eth0", " ip ospf area 0"])
      == ({}, set(), set(), False), "OSPFv2 statements are not OSPFv3")
check(prismash.ospf6_state(["interface eth2", " ipv6 ospf6 area 1"])[3] is True,
      "an interface in an area is OSPFv3 even with no router stanza")
ok("area, passive, mtu-ignore per interface, and has_ospf6")

print("\n=== rpki_findings: a policy that does nothing without a cache ===")
# Mutation shown red: drop `not caches`.
rm = ["route-map RPKI deny 10", " match rpki notfound",
      "route-map RPKI deny 20", " match rpki invalid",
      "route-map RPKI permit 30", " match rpki valid"]
out = prismash.rpki_findings(rm)
check(len(out) == 3, f"three clauses, three findings: {out}")
check(out[0][0] == "match rpki notfound"
      and "matches all of them" in out[0][1],
      f"notfound with no cache matches everything: {out[0]!r}")
check(out[1][0] == "match rpki invalid"
      and "nothing is ever invalid" in out[1][1],
      f"invalid with no cache matches nothing: {out[1]!r}")
check("nothing is ever valid" in out[2][1], f"valid likewise: {out[2]!r}")
check(prismash.rpki_findings(["rpki cache 10.0.0.9 3323 preference 1"] + rm)
      == [], "with a cache configured the policy means what it says")
check(prismash.rpki_findings(["route-map X permit 10", " match ip address PL"])
      == [], "no rpki clause, nothing to say")
ok("findings only without a cache")

print("\n=== set_value_error: the one bound FRR does not enforce ===")
# Mutation shown red: `lo <= int(w[2]) < hi`, refusing the top of the range.
check(prismash.set_value_error("set local-preference 100") is None,
      "a number in range")
check(prismash.set_value_error("set local-preference 4294967295") is None,
      "the top of the range is in it")
check(prismash.set_value_error("set local-preference 0") is None,
      "and the bottom")
e = prismash.set_value_error("set local-preference banana")
check(e is not None and e.startswith("'banana' is not a local-preference: "
                                     "expected 0-4294967295"),
      f"a word is refused with the range: {e!r}")
e = prismash.set_value_error("set local-preference 4294967296")
check(e is not None and e.startswith("'4294967296' is not a local-preference"),
      f"one past the top is refused: {e!r}")
check(prismash.set_value_error("set metric banana") is None,
      "a statement FRR bounds itself is left to FRR")
check(prismash.set_value_error("set local-preference") is None,
      "a statement with no value is not this function's")
check(prismash.set_value_error("match local-preference 100") is None,
      "match is not set")
ok("refused with the range, and only for the table's entries")

print("\n=== route_map_references: where a name is used, and what kind ===")
# Mutation shown red: drop the `description` skip.
refs = prismash.route_map_references([
    "router bgp 65000",
    " neighbor 10.0.0.2 route-map RM-IN in",
    " neighbor 10.0.0.2 description upstream, see route-map RM-DOC",
    " no neighbor 10.0.0.3 route-map RM-GONE in",
    " bgp route-map delay-timer 10",
    " redistribute connected route-map RM-CONN",
    " network 10.0.0.0/8 route-map RM-NET",
    " table-map RM-TAB",
    " neighbor 10.0.0.2 advertise-map RM-ADV exist-map RM-EX",
    "route-map RM-IN permit 10",
    " call RM-CALL",
    " description see route-map RM-DOC2",
    " set local-preference 200",
])
check(("neighbor 10.0.0.2 route-map RM-IN in", "RM-IN", "neighbor-in-out")
      in refs, "a neighbor route-map is neighbor-in-out")
check(("redistribute connected route-map RM-CONN", "RM-CONN", "redistribute")
      in refs, "redistribute")
check(("network 10.0.0.0/8 route-map RM-NET", "RM-NET", "network") in refs,
      "network")
check(("table-map RM-TAB", "RM-TAB", "other") in refs, "table-map is other")
check(("call RM-CALL", "RM-CALL", "other") in refs, "call is other")
names = {r[1] for r in refs}
check({"RM-ADV", "RM-EX"} <= names, f"the *-map family: {names}")
check(not ({"RM-DOC", "RM-DOC2", "RM-GONE"} & names),
      f"a description names nothing, and a negation is not a use: {names}")
check("delay-timer" not in names, "bgp route-map delay-timer is a knob")
check("RM-IN" not in {r[1] for r in refs if r[2] != "neighbor-in-out"},
      "the definition line of RM-IN is not a reference")
ok("three fatal kinds, 'other' for the rest, and free text skipped")

print("\n=== route_maps_defined: names with a clause header, first seen ===")
# Mutation shown red: `seen[w[1]] = ...`, so the last clause wins.
seen = prismash.route_maps_defined([
    "route-map RM-A permit 10", " match ip address PL",
    "route-map RM-A deny 20",
    "  route-map RM-B deny 5",
    "bgp route-map delay-timer 10",
    "neighbor 10.0.0.2 route-map RM-REF in",
])
check(set(seen) == {"RM-A", "RM-B"}, f"defined: {sorted(seen)}")
check(seen["RM-A"] == "route-map RM-A permit 10",
      f"where RM-A begins is its first clause: {seen['RM-A']!r}")
check(seen["RM-B"] == "route-map RM-B deny 5",
      f"stripped of its indentation: {seen['RM-B']!r}")
check("RM-REF" not in seen, "a name only referenced is not defined")
ok("first header per name, references and knobs excluded")

# ------------------------------------- the closures, through their owners
#
# `same`, `acts_on`, `by_seq`, `ent` (twice) and `after` are defined inside
# ospf_findings, route_map_findings, bgp_errors, max_prefix_findings and
# prefix_range_error, over their locals, and can be called from nowhere
# else: they carry the reader's marker for that reason. What each one
# decides is read here through its owner, so the rule is not untested for
# being unreachable.

print("\n=== by_seq (route_map_findings): FRR's order, a bad seq last ===")
# Mutation shown red: `(0, 0)` for a seq that is not a number, sorting it
# first and inventing a catch-all out of a typo.
findings = prismash.route_map_findings([
    "route-map ORD permit 20",
    "route-map ORD permit 10", " match ip address PL",
    "route-map ORD permit x5", " match ip address PL2",
])
unreachable = [f for f in findings if "unreachable" in f[2]]
check(not any(f[1] == "route-map ORD permit 10" for f in unreachable),
      f"seq 10, written after the catch-all at 20, is reached before it: "
      f"{findings}")
check(any(f[1] == "route-map ORD permit x5" for f in unreachable),
      f"a clause with a seq FRR will not take sorts last, after the "
      f"catch-all: {findings}")
ok("sorted by seq, non-numeric seq last")

print("\n=== after (prefix_range_error): the number after ge and le ===")
# Mutation shown red: `int(w[w.index(word)])`, reading the keyword itself.
check(prismash.prefix_range_error(
        "ip prefix-list PL permit 10.0.0.0/8 ge 16 le 24")
      is None, "8 <= 16 <= 24 <= 32 holds")
e = prismash.prefix_range_error(
    "ip prefix-list PL permit 10.0.0.0/8 ge 24 le 16")
check(e is not None and "ge 24" in e and "le 16" in e,
      f"ge above le is refused, with both numbers read: {e!r}")
check(prismash.prefix_range_error("ip prefix-list PL permit 10.0.0.0/8 ge x")
      is None, "a ge that is not a number is FRR's to refuse")
ok("ge and le read from the word after each keyword")

print("\n=== ent (bgp_errors): one record per neighbour, however named ===")
# Mutation shown red: a fresh record on every call, so a group's remote-as is
# never seen by its member.
errs = prismash.bgp_errors([
    "router bgp 65000",
    " neighbor UP peer-group",
    " neighbor UP remote-as 65001",
    " neighbor 10.0.0.2 peer-group UP",
    " neighbor 10.0.0.3 peer-group UP",
    " neighbor 10.0.0.3 remote-as 65002",
    " neighbor 10.0.0.4 remote-as 65003",
    " neighbor 10.0.0.5 peer-group NOPE",
])
bad = {e[1] for e in errs}
check("neighbor 10.0.0.2 peer-group UP" not in bad,
      f"a member inherits the group's remote-as through one record: {errs}")
check(any("NOPE" in e[2] for e in errs),
      f"a group nobody declared is named: {errs}")
check(len(errs) == 1, f"and nothing else is refused: {errs}")
ok("statements about one name accumulate on one record")

print("\n=== ent (max_prefix_findings): the same, for the limit ===")
# Mutation shown red: the same fresh-record mutation.
warn = prismash.max_prefix_findings([
    "router bgp 65000",
    " neighbor UP peer-group",
    " neighbor UP remote-as 65001",
    " neighbor UP maximum-prefix 1000",
    " neighbor 10.0.0.2 peer-group UP",
    " neighbor 10.0.0.9 remote-as 65009",
])
named = {w[1] for w in warn}
check(all("10.0.0.2" not in s for s in named),
      f"a member of a group carrying the limit is covered: {warn}")
check(any("10.0.0.9" in s for s in named),
      f"an eBGP peer with no limit anywhere is warned: {warn}")
ok("group and member share one record")

print("\n=== same and acts_on (ospf_findings): the pair's Linux end ===")
# Mutation shown red: `acts_on` answering True for every name.
pairs = {"host-veth-vpp": ("host-veth-vpp", "tap4096", "vppwan0"),
         "tap4096": ("host-veth-vpp", "tap4096", "vppwan0"),
         "vppwan0": ("host-veth-vpp", "tap4096", "vppwan0")}
routing = ["router ospf", " network 10.0.0.0/24 area 0",
           " passive-interface host-veth-vpp"]
out = prismash.ospf_findings(routing, {"host-veth-vpp": ["10.0.0.1/24"]},
                           {"host-veth-vpp", "tap4096", "vppwan0"}, {},
                           lcp_pairs=pairs,
                           device_interfaces={"host-veth-vpp", "tap4096"})
check(any("vppwan0" in f[2] and "host-veth-vpp" in f[1] for f in out),
      f"passive-interface on the dataplane's name acts on nothing, and the "
      f"finding names the Linux end: {out}")
out = prismash.ospf_findings(["router ospf", " network 10.0.0.0/24 area 0",
                            " passive-interface vppwan0"],
                           {"host-veth-vpp": ["10.0.0.1/24"]},
                           {"host-veth-vpp", "tap4096", "vppwan0"}, {},
                           lcp_pairs=pairs,
                           device_interfaces={"host-veth-vpp", "tap4096"})
check(not any("passive-interface" in f[1] for f in out),
      f"the Linux end itself is the spelling FRR acts on: {out}")
ok("the pair's members are one interface, and only one of them acts")

# ===================================================================
# The Shell's applying methods, each called on its own over stubs (#390,
# batch H): everything that sends a message, runs a command, writes a
# file or edits a candidate. The API calls, the dataplane's CLI lines,
# the vtysh argv and the systemctl argv are RECORDED and the box reads
# what was asked; the machine running this is touched by none of it.
# ===================================================================
import contextlib     # noqa: E402
import io             # noqa: E402
import stat           # noqa: E402

import config as _config     # noqa: E402
import vppapi                # noqa: E402
from config import Config    # noqa: E402
from system import User, SnmpUser    # noqa: E402


class RecApi:
    """
    The binary API, recording. `refuse` names messages answered REFUSED,
    `fields` answers a call's reply fields by message, `rows` a dump's rows
    by message, and every call and dump is kept in `calls` and `dumps`.
    """

    def __init__(self, refuse=(), fields=None, rows=None):
        self.refuse = set(refuse)
        self.fields = dict(fields or {})
        self.rows = dict(rows or {})
        self.calls, self.dumps = [], []

    def available(self):
        return (vppapi.OK, "stub")

    def interface_index(self, name):
        return None

    def call(self, message, args):
        self.calls.append((message, args))
        if message in self.refuse:
            return vppapi.Reply(vppapi.REFUSED, retval=-7,
                                reason="the dataplane refused it: stub")
        return vppapi.Reply(vppapi.OK, fields=self.fields.get(message, {}))

    def dump(self, message, args):
        self.dumps.append((message, args))
        if message in self.refuse:
            return vppapi.Reply(vppapi.REFUSED, retval=-7,
                                reason="the dataplane refused it: stub")
        return vppapi.Reply(vppapi.OK,
                            fields={"rows": self.rows.get(message, [])})


class RecDp(prismash.Vpp):
    """The dataplane's CLI, recording, answered from a table by prefix."""

    TRACE = False

    def __init__(self, answers=None):
        self.commands = []
        self.answers = dict(answers or {})

    def run(self, cmd):
        self.commands.append(cmd)
        for prefix, answer in self.answers.items():
            if cmd.startswith(prefix):
                return answer
        return 0, ""

    def running(self):
        return 0, ""


class RecFrr(prismash.Frr):
    """
    FRR, recording. Derived from the real Frr so daemons() and running()
    are the product's own asking through show(); only show() and
    available() are answered here, from a table by prefix, and a command
    the table lacks is what a daemon that is down answers: rc 1, nothing.
    """

    def __init__(self, shows=None, up=True, refuses=None):
        self.shows = dict(shows or {})
        self.up = up
        self.asked = []
        # `dryrun` is FRR's grammar, which this stub does not have: it
        # accepts every statement but the ones `refuses` names, by prefix,
        # with the words FRR would say (#431). The real one is driven with a
        # stand-in vtysh further down.
        self.refuses = dict(refuses or {})
        self.dry = []

    def available(self):
        return self.up

    def dryrun(self, ctx, line):
        self.dry.append((list(ctx), line))
        for prefix, words in self.refuses.items():
            if line.startswith(prefix):
                return prismash.Frr.DRYRUN_REFUSED, words
        return prismash.Frr.DRYRUN_ACCEPTED, ""

    def show(self, cmd):
        self.asked.append(cmd)
        for prefix, answer in self.shows.items():
            if cmd.startswith(prefix):
                return answer
        return (1, "")


class RecRunner:
    """The System seam: every argv kept, answered from a table by prefix."""

    def __init__(self, answers=None):
        self.asked = []
        self.answers = dict(answers or {})

    def __call__(self, argv, stdin=None, timeout=30):
        self.asked.append(list(argv))
        for prefix, answer in self.answers.items():
            if tuple(argv[:len(prefix)]) == prefix:
                return answer
        return 0, ""


def shell(api=None, dp=None, frr=None, caps=None, runner=None, running=None):
    """
    A Shell over the stubs above, its capability stated outright for the
    reason test_prismash_apply.py records: Shell() reads the account's real
    groups, and left alone every privileged box here is refused first.
    """
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT, prismash.CAP_OPERATOR} \
        if caps is None else caps
    # A box on the installer's name (#526), not the machine running this.
    sh._prompt_name = prismash.HOSTNAME_DEFAULT
    sh.api = api or RecApi()
    sh.dp = dp or RecDp()
    sh.frr = frr or RecFrr()
    sh.sys = system.System(runner=runner or RecRunner())
    text = running or ""
    sh.running_config = lambda: Config.parse(text)
    sh.confirm = prismash.ConfirmPending(os.path.join(_root, "confirm-none"))
    return sh


def driven(fn, *a, **k):
    """What the VTY printed while `fn` ran, and what it returned."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rv = fn(*a, **k)
    return rv, out.getvalue()


def rows(text):
    return text.splitlines()


def argv_script(name, rc=0, says=""):
    """
    A program that writes its argv, one per line, to <name>.argv beside
    itself, prints `says` and exits `rc` - the stand-in for vtysh, the
    installer and the login shell.
    """
    log = os.path.join(_root, name + ".argv")
    body = "#!/bin/sh\n"
    body += f": > '{log}'\n"
    body += f"for a in \"$@\"; do printf '%s\\n' \"$a\" >> '{log}'; done\n"
    if says:
        body += f"printf '%s\\n' '{says}'\n"
    body += f"exit {rc}\n"
    return script(name, body), log


def argv_of(log):
    try:
        with open(log) as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return None


# ------------------------------------------------------------- Frr: vtysh

print("\n=== Frr._exec: vtysh's argv, its rc and both of its streams ===")
# Mutation shown red: return only stdout, dropping stderr.
vtysh, log = argv_script("vtysh-exec", rc=3, says="from vtysh")
saved = prismash.VTYSH
try:
    prismash.VTYSH = vtysh
    rc, out = prismash.Frr()._exec(["-c", "show version"])
    check(rc == 3 and out == "from vtysh",
          f"rc and text come from the program, rstripped: {(rc, out)!r}")
    check(argv_of(log) == ["-c", "show version"],
          f"the argv handed to vtysh is the one given: {argv_of(log)}")
    both = script("vtysh-both", "#!/bin/sh\necho out; echo err >&2; exit 0\n")
    prismash.VTYSH = both
    rc, out = prismash.Frr()._exec(["-c", "x"])
    check(out == "out\nerr", f"stdout then stderr, as one text: {out!r}")
    prismash.VTYSH = os.path.join(_root, "no-such-vtysh")
    rc, out = prismash.Frr()._exec(["-c", "x"])
    check(rc == 1 and out.endswith("not found"),
          f"a vtysh that is not there is rc 1 and says so: {(rc, out)!r}")
finally:
    prismash.VTYSH = saved
ok("argv verbatim, rc as exited, stdout and stderr both kept")

print("\n=== Frr.apply_lines: configure terminal, then one -c per line ===")
# Mutation shown red: drop the leading "configure terminal".
vtysh, log = argv_script("vtysh-apply")
saved = prismash.VTYSH
try:
    prismash.VTYSH = vtysh
    rc, out = prismash.Frr().apply_lines(["router bgp 65000",
                                        " neighbor 10.0.0.2 remote-as 65001"])
    check(argv_of(log) == ["-c", "configure terminal",
                           "-c", "router bgp 65000",
                           "-c", " neighbor 10.0.0.2 remote-as 65001"],
          f"the lines follow configure terminal, in order: {argv_of(log)}")
    check(rc == 0, "and the program's rc is the answer")
finally:
    prismash.VTYSH = saved
ok("one vtysh run, the lines in the order given")

print("\n=== Frr.absorb_stale_refusal: the one absorber that was measured "
      "to work ===")
# Mutation shown red: send "ip prefix-list" instead of "no ip prefix-list".
vtysh, log = argv_script("vtysh-absorb", rc=1)
saved = prismash.VTYSH
try:
    prismash.VTYSH = vtysh
    prismash.Frr().absorb_stale_refusal()
    check(argv_of(log) == ["-c", "configure terminal", "-c",
                           f"no ip prefix-list {prismash.Frr.ABSORB_NAME}"],
          f"a real commit whose effect is nothing: {argv_of(log)}")
    prismash.VTYSH = os.path.join(_root, "no-such-vtysh")
    prismash.Frr().absorb_stale_refusal()
    check(True, "a vtysh that is not there raises nothing: best effort")
finally:
    prismash.VTYSH = saved
ok("no ip prefix-list <a name nobody has>, and a missing vtysh is not "
   "an error")

print("\n=== Frr.daemons: None when FRR did not say, a set when it did ===")
# Mutation shown red: return set() when rc != 0.
check(prismash.Frr.daemons(RecFrr()) is None,
      "vtysh refusing is None, not the empty set")
check(prismash.Frr.daemons(RecFrr({"show daemons": (0, " \n")})) is None,
      "an empty answer is None as well: FRR did not say")
check(prismash.Frr.daemons(RecFrr({"show daemons": (0, "zebra bgpd\nospfd\n")}))
      == {"zebra", "bgpd", "ospfd"},
      "the words of the answer, wherever the newlines fall, are the set")
ok("down and silent are None; an answer is its words")

print("\n=== Frr.running: the statements, without vtysh's framing ===")
# Mutation shown red: keep the "frr version" and "end" lines.
text = ("Building configuration...\n\nCurrent configuration:\n!\n"
        "frr version 10.7.1\nfrr defaults traditional\nhostname box\n"
        "router bgp 65000\n neighbor 10.0.0.2 remote-as 65001   \n!\nend\n")
got = prismash.Frr.running(RecFrr({"show running-config": (0, text)}))
# `hostname` is the box's statement since #516, not routing: dropped too.
check(got == ["!", "router bgp 65000",
              " neighbor 10.0.0.2 remote-as 65001", "!"],
      f"framing out, blank rows out, right side stripped: {got}")
check(prismash.Frr.running(RecFrr()) == [],
      "a daemon that is down is a configuration with nothing in it")
ok("five framing prefixes dropped, indentation kept")

print("\n=== Frr.diagnostics: what FRR said, not what frr-reload narrated ===")
# Mutation shown red: keep the INFO rows.
out = ("2026-09-17 10:00:00,001 INFO: Called via ...\n"
       "2026-09-17 10:00:00,002 WARNING: something about your config\n"
       "Waiting for children to finish\n"
       "['router bgp 65000', ' exit']\n"
       "' exit\n"
       "The route-map 'PL-X' does not exist.\n"
       "Loading Config done\n"
       "=== stanza ===\n"
       "frr-reload: done\n"
       "\n")
got = prismash.Frr.diagnostics(out)
check(got == ["2026-09-17 10:00:00,002 WARNING: something about your config",
              "The route-map 'PL-X' does not exist."],
      f"INFO, progress, echoed lists and rules are dropped; the daemon's "
      f"two rows stay: {got}")
check(prismash.Frr.diagnostics(None) == [] and prismash.Frr.diagnostics("") == [],
      "nothing said is nothing kept")
ok("the noise it already knows is dropped, and only that")

# FRR's answer to frr-reload's second pass (#445), as PRISMA_FRR_DUMP
# recorded it on FRR 10.7.1 for a commit that added a static route. Red with
# the filter removed (the pair reaches the operator), and with the header
# dropped whatever is under it (a real note loses its heading).
_second_pass = """[5720|bgpd] sending configuration
% Configuration applied with notes:
No changes found to be committed!
[5729|staticd] sending configuration
[5720|bgpd] done"""
check(prismash.Frr.diagnostics(_second_pass) == [],
      f"FRR's no-op second pass says nothing: "
      f"{prismash.Frr.diagnostics(_second_pass)!r}")
_real_note = """% Configuration applied with notes:
The route-map 'PL-X' does not exist."""
check(prismash.Frr.diagnostics(_real_note) == [
          "% Configuration applied with notes:",
          "The route-map 'PL-X' does not exist."],
      f"and a real note keeps its heading: "
      f"{prismash.Frr.diagnostics(_real_note)!r}")
_both = """% Configuration applied with notes:
No changes found to be committed!
The route-map 'PL-X' does not exist."""
check(prismash.Frr.diagnostics(_both) == [
          "% Configuration applied with notes:",
          "The route-map 'PL-X' does not exist."],
      f"and a real note beside the no-op one keeps its heading too: "
      f"{prismash.Frr.diagnostics(_both)!r}")

print("\n=== Frr._summarise: the reason, not the footer twice ===")
# Mutation shown red: drop the `useful` filter, so the footer alone is the
# summary.
out = ("Loading Config\n"
       "The route-map 'PL-X' does not exist.\n"
       "% Configuration failed.\n")
check(prismash.Frr._summarise(out) == "The route-map 'PL-X' does not exist.\n"
      "% Configuration failed.",
      f"a footer with no reason falls back to what the daemon said, the "
      f"footer under it: {prismash.Frr._summarise(out)!r}")
out = "Exiting: rejected\n% Configuration failed.\nnoise\n"
got = prismash.Frr._summarise(out)
check(got == "Exiting: rejected\n% Configuration failed.",
      f"a row with the reason keeps the caught rows, last four: {got!r}")
check(prismash.Frr._summarise("- a\n- b\n") == "- b",
      "nothing matched and nothing diagnosed is the last row")
check(prismash.Frr._summarise("") == "", "no output is no summary")
ok("prefixed rows, then the daemon's own rows, then the last row")

# ------------------------------------------------------ the stores on disk

print("\n=== Rollback._file: revision n is <path>/n.conf ===")
# Mutation shown red: f"{n}.cfg".
rb = prismash.Rollback(path=os.path.join(_root, "rb"))
check(rb._file(3) == os.path.join(_root, "rb", "3.conf"),
      f"the file of revision 3: {rb._file(3)}")
check(rb._file(0) == os.path.join(_root, "rb", "0.conf"),
      "revision 0 is a file too, the configuration now in effect")
ok("one naming rule for the store")

print("\n=== ConfirmPending.count_attempt: one more, written back ===")
# Mutation shown red: do not increment before arming.
cp = prismash.ConfirmPending(os.path.join(_root, "confirm-count"))
cp.arm(1700000000, "user alice\n", 5)
pending = cp.read()
check(cp.count_attempt(pending) == 1, "the first attempt counts one")
again = cp.read()
check(again["attempts"] == 1 and again["config"] == "user alice\n"
      and again["deadline"] == 1700000000.0 and again["minutes"] == "5",
      f"the file carries the count and nothing else changed: {again}")
check(cp.count_attempt(again) == 2 and cp.read()["attempts"] == 2,
      "and the second counts two")
ok("the count is on disk, so a unit that restarts still gives up")

print("\n=== ConfirmPending.clear: the file goes, and going twice is fine ===")
# Mutation shown red: do not unlink.
cp = prismash.ConfirmPending(os.path.join(_root, "confirm-clear"))
cp.arm(1700000000, "user alice\n", 5)
cp.clear()
check(not os.path.exists(cp.path), "confirmed: nothing left to do")
cp.clear()
check(cp.read() is None, "clearing what is already clear raises nothing")
ok("the record of the window is its file, and clear removes it")

print("\n=== ConfirmPending.give_up: moved aside, kept, and named ===")
# Mutation shown red: unlink instead of rename.
cp = prismash.ConfirmPending(os.path.join(_root, "confirm-giveup"))
cp.arm(1700000000, "user alice\n", 5)
dead = cp.give_up()
check(dead == cp.path + ".failed", f"the aside file is named: {dead}")
check(not os.path.exists(cp.path) and os.path.exists(dead),
      "the pending file is gone and the aside file is there")
kept = ""
if os.path.exists(dead):
    with open(dead) as f:
        kept = f.read()
check("user alice" in kept,
      "it still holds the configuration somebody has to put back")
check(cp.give_up() == dead and cp.read() is None,
      "giving up with nothing pending clears and raises nothing")
ok("kept rather than deleted: it is the configuration to put back by hand")

# ------------------------------------------------- the dataplane, over CLI

print("\n=== Shell.vpp: the dataplane's answer printed, its failure "
      "counted ===")
# Mutation shown red: do not count a non-zero rc in `failed`.
sh = shell(dp=RecDp({"show x": (1, "unknown input"), "ok": (0, "fine")}))
rv, text = driven(lambda: sh.vpp("show x"))
check(rv == 1 and sh.failed == 1 and "unknown input" in rows(text),
      f"a refusal is printed, returned and counted: {(rv, sh.failed, text)!r}")
rv, text = driven(lambda: sh.vpp("ok", quiet=True))
check(rv == 0 and sh.failed == 1 and text == "",
      f"quiet prints nothing and a 0 counts nothing: {(rv, text)!r}")
check(sh.dp.commands == ["show x", "ok"], "the command reaches the dataplane")
ok("rc returned, text printed unless quiet, failures counted")

print("\n=== Shell.osi_protos_enabled: a hex string read as a list ===")
# Mutation shown red: return [] when the dataplane could not be asked.
sh = shell(api=RecApi(fields={"lcp_osi_proto_get": {"osi_protos": "0x83fe"}}))
check(sh.osi_protos_enabled() == [0x83, 0xfe],
      f"'0x83fe' is two protocols: {sh.osi_protos_enabled()}")
sh = shell(api=RecApi(fields={"lcp_osi_proto_get": {"osi_protos": "0x"}}))
check(sh.osi_protos_enabled() == [], "bare '0x' is none, and a real answer")
sh = shell(api=RecApi(refuse={"lcp_osi_proto_get"}))
check(sh.osi_protos_enabled() is None, "could not ask is None, not none")
ok("the hex string the API answers, two characters a protocol")

print("\n=== Shell.enable_osi_isis: punt 0x83 once, and only when it is "
      "not ===")
# Mutation shown red: send the enable even when 0x83 is already punted.
api = RecApi(fields={"lcp_osi_proto_get": {"osi_protos": "0x83"}})
sh = shell(api=api)
check(sh.enable_osi_isis() is True and
      [m for m, a in api.calls] == ["lcp_osi_proto_get"],
      f"already punted: nothing is sent: {api.calls}")
api = RecApi(fields={"lcp_osi_proto_get": {"osi_protos": "0x"}})
sh = shell(api=api)
check(sh.enable_osi_isis() is True and
      api.calls[-1] == ("lcp_osi_proto_enable", {"osi_proto": 0x83}),
      f"not punted: the enable names IS-IS's protocol: {api.calls[-1]}")
api = RecApi(fields={"lcp_osi_proto_get": {"osi_protos": "0x"}},
             refuse={"lcp_osi_proto_enable"})
sh = shell(api=api)
rv, text = driven(sh.enable_osi_isis)
check(rv is False and sh.failed == 1 and
      "error: IS-IS is configured but its frames cannot be punted to the "
      "kernel" in rows(text),
      f"a refusal is False and says what the adjacency would do: {text!r}")
api = RecApi(refuse={"lcp_osi_proto_get"})
sh = shell(api=api)
check(sh.enable_osi_isis() is True and len(api.calls) == 1,
      "a dataplane not answering is not this method's call: nothing sent")
ok("read first, enable only the missing one, refuse out loud")

# ---------------------------------------------------- the cards and pairs

_HW = """              Name                Idx   Link  Hardware
ens19                              1     up   ens19
  Link speed: 10 Gbps
    pci: device 1af4:1041 subsystem 1af4:1100 address 0000:00:13.00 numa 0
GigabitEthernet0/14/0              2     up   GigabitEthernet0/14/0
    pci: device 8086:1572 subsystem 8086:0000 address 0000:00:14.00 numa 0
tap0                               3     up   tap0
local0                             0    down  local0
"""

print("\n=== Shell.dataplane_cards: the cards, off show "
      "hardware-interfaces ===")
# Mutation shown red: ignore rc, so a dataplane that refused still answers.
sh = shell(dp=RecDp({"show hardware-interfaces": (0, _HW)}))
check(sh.dataplane_cards() == {"ens19": (0, 0, 0x13, 0),
                               "GigabitEthernet0/14/0": (0, 0, 0x14, 0)},
      f"two cards with a pci row, the tap and local0 not: "
      f"{sh.dataplane_cards()}")
sh = shell(dp=RecDp({"show hardware-interfaces": (1, _HW)}))
check(sh.dataplane_cards() == {}, "a dataplane that cannot be asked holds "
      "no card, and then nothing is defaulted")
ok("read off the dataplane, empty when it does not answer")

print("\n=== Shell.defaulted_cards: the cards whose name Linux can take ===")
# Mutation shown red: drop the linux_ifname_error filter.
sh = shell(dp=RecDp({"show hardware-interfaces": (0, _HW)}))
check(sh.defaulted_cards() == {"ens19"},
      f"GigabitEthernet0/14/0 has a '/' and gets no default: "
      f"{sh.defaulted_cards()}")
ok("one owner of the condition the prompt asks too")

print("\n=== Shell.pair_cards: a card with a block is paired under its own "
      "name, unless one was chosen ===")
# Mutation shown red: drop the `card in blocks` term, pairing every card.
sh = shell(dp=RecDp({"show hardware-interfaces": (0, _HW)}))
target = Config()
target.mtus["ens19"] = "9000"
sh.pair_cards(target)
check(target.linux_names == {"ens19": "ens19"},
      f"a device block on the card: linux-name ens19: {target.linux_names}")
target = Config()
sh.pair_cards(target)
check(target.linux_names == {},
      f"no block names the card, nothing is paired: {target.linux_names}")
target = Config()
target.mtus["ens19"] = "9000"
target.linux_names["ens19"] = "wan0"
sh.pair_cards(target)
check(target.linux_names == {"ens19": "wan0"},
      f"an override stays: {target.linux_names}")
target = Config()
target.routing = ["interface ens19", " no shutdown"]
sh.pair_cards(target)
check(target.linux_names == {"ens19": "ens19"},
      f"a stanza the routing section opens is a block too: "
      f"{target.linux_names}")
target = Config()
target.mtus["GigabitEthernet0/14/0"] = "9000"
sh.pair_cards(target)
check(target.linux_names == {},
      f"a name Linux cannot take gets no default: {target.linux_names}")
# EVERY INTERFACE WITH A BLOCK (#461), not only cards and loopbacks: a
# sub-interface, a hot card, anything whose name Linux can take - unless Linux
# already holds that name for something that is not this box's own pair.
_net = tempfile.mkdtemp()
_saved_net = prismash.Shell.SYS_NET
prismash.Shell.SYS_NET = _net
try:
    _show_if = ("              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count\n"
                "vin                               3      up          9000/0/0/0\n"
                "ghost0                            4      up          9000/0/0/0\n")
    sh = shell(dp=RecDp({"show hardware-interfaces": (0, _HW),
                         "show interface": (0, _show_if)}))
    check(sh.dataplane_names() >= {"vin", "ghost0"},
          f"the dataplane's names, read as commit check reads them: "
          f"{sh.dataplane_names()}")
    check(not sh.linux_holds("ens19.100"), "nothing in an empty sysfs")
    target = Config()
    target.vlans["ens19.100"] = {"vlan": 100}
    target.addresses["ens19.100"] = ["192.0.2.1/24"]
    sh.pair_cards(target)
    check(target.linux_names.get("ens19.100") == "ens19.100",
          f"a sub-interface with a block is paired under its own name: "
          f"{target.linux_names}")
    os.makedirs(os.path.join(_net, "vin"))
    check(sh.linux_holds("vin"), "a name Linux has is seen")
    target = Config()
    target.addresses["vin"] = ["192.0.2.1/24"]
    sh.pair_cards(target)
    check("vin" not in target.linux_names,
          f"a name Linux already holds for something else is not taken: "
          f"{target.linux_names}")
    running = Config()
    running.linux_names["vin"] = "vin"
    sh.pair_cards(target, running)
    check(target.linux_names.get("vin") == "vin",
          "but this box's own pair, already there, is kept")
    target = Config()
    target.addresses["nosuch0"] = ["192.0.2.9/24"]
    sh.pair_cards(target)
    check("nosuch0" not in target.linux_names,
          "and a name the dataplane has not got is left to the refusal "
          "that names it")
finally:
    prismash.Shell.SYS_NET = _saved_net
# try_default_pair(): a pair the box decided on its own is tried and not
# insisted on (#461) - measured in lab/bypass.sh, where VPP will not pair the
# packet generator's pg0 and the commit used to fail over it.
class _PairApi:
    def __init__(self, state):
        self.state = state
    def interface_index(self, name):
        return 7
    def call(self, message, args):
        return vppapi.Reply(self.state)
sh = shell(api=_PairApi(vppapi.OK))
check(sh.try_default_pair("ens19.100", "ens19.100") is True,
      "a pair the dataplane makes is made")
sh = shell(api=_PairApi(vppapi.REFUSED))
rv, text = driven(lambda: sh.try_default_pair("pg0", "pg0"))
check(rv is False and "note: pg0 has no Linux end" in text
      and "error" not in text,
      f"one it will not make is a note, not an error: {rv} {text!r}")
ok("written into the configuration, at apply, for cards with a block")

# ------------------------------------------------- the CGNAT globals

print("\n=== Shell.global_api: one field carries a value, the rest are "
      "zero ===")
# Mutation shown red: do not apply kw, sending the all-zero message.
api = RecApi()
sh = shell(api=api)
r = sh.global_api(set_block_holddown=True, block_holddown=30)
msg, args = api.calls[0]
check(msg == "bpa_global_set" and r.state == vppapi.OK, "bpa_global_set, OK")
check(args["set_block_holddown"] is True and args["block_holddown"] == 30,
      f"the field asked for carries its value: {args}")
check(args["timeouts"] == dict(sh.ZERO_TIMEOUTS)
      and args["set_session_table"] is False and args["session_buckets"] == 0
      and args["session_memory_mb"] == 0,
      f"every other field is zero, meaning leave it alone: {args}")
ok("the message's contract: zero is untouched, one field is the change")

print("\n=== Shell.apply_logging: enable, disable, tcp, and the shapes it "
      "does not take ===")
# Mutation shown red: enable = (w[2] == "disable").
api = RecApi()
sh = shell(api=api)
sh.apply_logging(["logging", "logging", "enable"])
check(api.calls[-1] == ("bpa_logging_set",
                        {"set_enable": True, "enable": True,
                         "set_tcp": False, "tcp_enable": False,
                         "collector": "0.0.0.0", "port": 0}),
      f"enable: set_enable with enable True: {api.calls[-1]}")
sh.apply_logging(["logging", "logging", "disable"])
check(api.calls[-1][1]["set_enable"] is True
      and api.calls[-1][1]["enable"] is False,
      f"disable: set_enable with enable False: {api.calls[-1]}")
sh.apply_logging(["logging", "logging", "tcp", "10.0.0.9", "port", "4740"])
check(api.calls[-1][1]["set_tcp"] is True
      and api.calls[-1][1]["tcp_enable"] is True
      and api.calls[-1][1]["collector"] == "10.0.0.9"
      and api.calls[-1][1]["port"] == 4740,
      f"tcp <collector> port <n>: {api.calls[-1]}")
sh.apply_logging(["logging", "logging", "tcp", "10.0.0.9"])
check(api.calls[-1][1]["port"] == 0,
      "no port typed is port 0: the default belongs to the dataplane")
sh.apply_logging(["logging", "logging", "tcp", "disable"])
check(api.calls[-1][1]["set_tcp"] is True
      and api.calls[-1][1]["tcp_enable"] is False
      and api.calls[-1][1]["collector"] == "0.0.0.0",
      f"tcp disable: set_tcp with tcp_enable False: {api.calls[-1]}")
n = len(api.calls)
rv, text = driven(sh.apply_logging,
                  ["logging", "logging", "tcp", "10.0.0.9", "port", "70000"])
check(len(api.calls) == n and
      "error: cgnat logging tcp: port 70000 is not one" in rows(text),
      f"a port that is not one is refused before anything is sent: {text!r}")
rv, text = driven(sh.apply_logging, ["logging", "logging", "sideways"])
check(len(api.calls) == n and
      rows(text)[0] == "error: cgnat logging: not a shape this box takes"
      and any(r.strip().startswith("logging tcp <collector>")
              for r in rows(text)),
      f"a shape it does not take lists the ones it does: {text!r}")
print("\n=== Shell.apply_identity: a path, and never the file's contents ===")
# Mutation shown red: args["path"] = "" on the file path.
api = RecApi()
sh = shell(api=api)
sh.apply_identity(["identity", "identity", "file",
                   "/etc/prisma-routing/subscribers.map"])
check(api.calls[-1] == ("bpa_identity_source_set",
                        {"kind": 0,
                         "path": "/etc/prisma-routing/subscribers.map"}),
      f"the statement carries the path and which grammar to read it with: "
      f"{api.calls[-1]}")
sh.apply_identity(["identity", "identity", "dhcp-leases",
                   "/var/lib/dhcp/dhcpd.leases"])
check(api.calls[-1] == ("bpa_identity_source_set",
                        {"kind": 1, "path": "/var/lib/dhcp/dhcpd.leases"}),
      f"a lease file is the same table read with the other grammar: "
      f"{api.calls[-1]}")
sh.apply_identity(["identity", "identity", "file", "/tmp/a file.map"])
check(api.calls[-1][1]["path"] == "/tmp/a file.map",
      f"a path with a space in it is one path: {api.calls[-1]}")
sh.apply_identity(["identity", "identity", "none"])
check(api.calls[-1][1]["path"] == "",
      f"'none' is the empty path, which the dataplane reads as no table: "
      f"{api.calls[-1]}")
n = len(api.calls)
rv, text = driven(sh.apply_identity, ["identity", "identity", "sideways"])
check(len(api.calls) == n
      and rows(text)[0] == "error: cgnat identity: not a shape this box takes"
      and any("identity file <path>" in r for r in rows(text)),
      f"a shape it does not take lists the ones it does: {text!r}")
rv, text = driven(sh.apply_identity, ["identity", "identity", "file"])
check(len(api.calls) == n,
      f"'file' with no path is not a path: {text!r}")
# THE DATAPLANE READS THE FILE, NOT THIS VTY: a refusal about a line comes
# back from there, and is printed as the dataplane's.
api = RecApi(refuse={"bpa_identity_source_set"})
sh = shell(api=api)
rv, text = driven(sh.apply_identity, ["identity", "identity", "file", "/x"])
check(any(r.startswith("error: cgnat identity: ") for r in rows(text)),
      f"a refused file is reported as the dataplane's: {text!r}")
ok("the path is configuration; what is in the file is personal data")

print("\n=== Shell.apply_identity_radius: the listener, and its secret ===")
# Mutation shown red: args["enable"] = False on the enabling path.
api = RecApi()
sh = shell(api=api)
sh.apply_identity_radius(["identity", "identity", "radius", "secret",
                          "s3cr3t"])
check(api.calls[-1] == ("bpa_identity_radius_set",
                        {"enable": True, "port": 0, "secret": "s3cr3t"}),
      f"a secret and the dataplane's own default port: {api.calls[-1]}")
sh.apply_identity_radius(["identity", "identity", "radius", "port", "1814",
                          "secret", "s3cr3t"])
check(api.calls[-1][1]["port"] == 1814,
      f"the options are read in whatever order they were typed: "
      f"{api.calls[-1]}")
sh.apply_identity_radius(["identity", "identity", "radius", "disable"])
check(api.calls[-1][1]["enable"] is False
      and api.calls[-1][1]["secret"] == "",
      f"disable carries no secret at all: {api.calls[-1]}")

n = len(api.calls)
rv, text = driven(sh.apply_identity_radius,
                  ["identity", "identity", "radius", "port", "1813"])
check(len(api.calls) == n
      and rows(text)[0] == "error: cgnat identity radius: a shared secret is "
                           "required"
      and any("dictation" in r for r in rows(text)),
      f"no secret is refused, and the refusal says what it would cost: "
      f"{text!r}")
rv, text = driven(sh.apply_identity_radius,
                  ["identity", "identity", "radius", "secret", "s", "port",
                   "70000"])
check(len(api.calls) == n
      and rows(text)[0] == "error: cgnat identity radius: port is 0-65535",
      f"a port that is not one is refused before anything is sent: {text!r}")
rv, text = driven(sh.apply_identity_radius,
                  ["identity", "identity", "radius", "sideways", "2"])
check(len(api.calls) == n
      and rows(text)[0] == "error: cgnat identity radius: not an option of "
                           "this statement",
      f"a word that is not an option lists the ones that are: {text!r}")
# And it is reachable the way an operator types it, through apply_logging's
# sibling: `cgnat identity radius ...`.
api = RecApi()
sh = shell(api=api)
sh.apply_identity(["identity", "identity", "radius", "secret", "abc"])
check(api.calls[-1][0] == "bpa_identity_radius_set",
      f"`cgnat identity radius` routes here: {api.calls[-1]}")
# AND THE DATAPLANE'S OWN REFUSAL, on both paths: a port already taken, a
# secret the dataplane will not have. It comes back through the API and is
# printed as the dataplane's words, not this VTY's.
api = RecApi(refuse={"bpa_identity_radius_set"})
sh = shell(api=api)
rv, text = driven(sh.apply_identity_radius,
                  ["identity", "identity", "radius", "secret", "s3cr3t"])
check(any(r.startswith("error: cgnat identity radius: ") for r in rows(text)),
      f"a refused listener is reported as the dataplane's: {text!r}")
rv, text = driven(sh.apply_identity_radius,
                  ["identity", "identity", "radius", "disable"])
check(any(r.startswith("error: cgnat identity radius: ") for r in rows(text)),
      f"and so is a refused disable: {text!r}")
ok("a listener that checks nothing would take dictation, so the secret is "
   "required")

print("\n=== Shell.apply_logging_syslog: the second collector, and its "
      "refusals ===")
# Mutation shown red: args["enable"] = False on the enabling path.
api = RecApi()
sh = shell(api=api)
sh.apply_logging(["logging", "logging", "syslog", "10.0.0.9",
                  "enterprise", "44444"])
check(api.calls[-1] == ("bpa_logging_syslog_set",
                        {"enable": True, "collector": "10.0.0.9", "port": 0,
                         "enterprise": 44444, "facility": 16,
                         "severity": 6}),
      f"a collector and an enterprise number, with the dataplane's own "
      f"defaults for the rest: {api.calls[-1]}")
sh.apply_logging(["logging", "logging", "syslog", "10.0.0.9", "enterprise",
                  "9", "port", "1514", "facility", "20", "severity", "5"])
check(api.calls[-1][1]["port"] == 1514
      and api.calls[-1][1]["facility"] == 20
      and api.calls[-1][1]["severity"] == 5,
      f"every option is read wherever it was typed: {api.calls[-1]}")
sh.apply_logging(["logging", "logging", "syslog", "disable"])
check(api.calls[-1][1]["enable"] is False
      and api.calls[-1][1]["collector"] == "0.0.0.0",
      f"disable sends the off, and names no collector: {api.calls[-1]}")

n = len(api.calls)
rv, text = driven(sh.apply_logging,
                  ["logging", "logging", "syslog", "10.0.0.9"])
check(len(api.calls) == n
      and rows(text)[0] == "error: cgnat logging syslog: an enterprise "
                           "number is required"
      and any("IANA" in r for r in rows(text)),
      f"no enterprise number is refused at the prompt: {text!r}")
rv, text = driven(sh.apply_logging,
                  ["logging", "logging", "syslog", "10.0.0.9", "enterprise",
                   "9", "sideways", "2"])
check(len(api.calls) == n
      and rows(text)[0] == "error: cgnat logging syslog: not an option of "
                           "this statement",
      f"a word that is not an option is refused, with the ones that are: "
      f"{text!r}")
rv, text = driven(sh.apply_logging,
                  ["logging", "logging", "syslog", "10.0.0.9", "enterprise"])
check(len(api.calls) == n
      and rows(text)[0] == "error: cgnat logging syslog: enterprise takes a "
                           "number",
      f"an option with no value is refused, not run with a default: {text!r}")
for word, value, top in (("port", "70000", 65535), ("facility", "24", 23),
                         ("severity", "9", 7)):
    rv, text = driven(sh.apply_logging,
                      ["logging", "logging", "syslog", "10.0.0.9",
                       "enterprise", "9", word, value])
    check(len(api.calls) == n
          and rows(text)[0] == f"error: cgnat logging syslog: {word} is "
                               f"0-{top}",
          f"{word} {value} is refused before anything is sent: {text!r}")

# AND THE DATAPLANE'S OWN REFUSAL, which is the one an operator meets when
# the box is not in the state the statement needs: it comes back through the
# API and is printed as the dataplane's words, not this VTY's.
api = RecApi(refuse={"bpa_logging_syslog_set"})
sh = shell(api=api)
rv, text = driven(sh.apply_logging,
                  ["logging", "logging", "syslog", "10.0.0.9", "enterprise",
                   "9"])
check(any(r.startswith("error: cgnat logging syslog: ") for r in rows(text)),
      f"a refused syslog collector is reported as the dataplane's: {text!r}")
rv, text = driven(sh.apply_logging, ["logging", "logging", "syslog",
                                     "disable"])
check(any(r.startswith("error: cgnat logging syslog: ") for r in rows(text)),
      f"and so is a refused disable: {text!r}")
# BY NAME as well as through the dispatch above: apply_logging routes on the
# third word, and a test that only ever went through it would measure the
# routing and not this method.
api = RecApi()
sh = shell(api=api)
sh.apply_logging_syslog(["logging", "logging", "syslog", "10.0.0.9",
                         "enterprise", "7"])
check(api.calls[-1][0] == "bpa_logging_syslog_set"
      and api.calls[-1][1]["enterprise"] == 7,
      f"called by name, it sends the syslog message: {api.calls[-1]}")
ok("one collector, one enterprise number, and every refusal at the prompt")

api = RecApi(refuse={"bpa_logging_set"})
sh = shell(api=api)
rv, text = driven(sh.apply_logging, ["logging", "logging", "enable"])
check(sh.failed == 1 and rows(text)[0].startswith(
      "error: cgnat logging enable: the dataplane refused it"),
      f"a refusal from the dataplane is said with its reason: {text!r}")
ok("one message with both halves, the port checked here, refusals said")

print("\n=== Shell.apply_logging_udp: VPP's own exporter, changed only "
      "where named ===")
# Mutation shown red: collector = w[3] even for `disable`.
CUR = {"src_address": "10.0.0.1", "collector_address": "10.0.0.9",
       "collector_port": 4739, "vrf_id": 7, "path_mtu": 1400,
       "template_interval": 20, "udp_checksum": 1}
api = RecApi(rows={"ipfix_exporter_dump": [dict(CUR)]})
sh = shell(api=api)
sh.apply_logging_udp(["logging", "logging", "udp", "10.0.0.9", "src",
                      "10.0.0.2", "port", "2055"])
check(api.calls[-1] == ("set_ipfix_exporter",
                        {"collector_address": "10.0.0.9",
                         "collector_port": 2055, "src_address": "10.0.0.2",
                         "vrf_id": 7, "path_mtu": 1400,
                         "template_interval": 20, "udp_checksum": True}),
      f"collector, source and port from the statement, the rest as read "
      f"back: {api.calls[-1]}")
sh.apply_logging_udp(["logging", "logging", "udp", "10.0.0.9", "src",
                      "10.0.0.2"])
check(api.calls[-1][1]["collector_port"] == 4739,
      "no port typed keeps the port the exporter has")
sh.apply_logging_udp(["logging", "logging", "udp", "disable"])
check(api.calls[-1][1]["collector_address"] == "0.0.0.0"
      and api.calls[-1][1]["src_address"] == "10.0.0.1",
      f"disable: collector 0.0.0.0, the source kept because VPP refuses "
      f"0.0.0.0 there: {api.calls[-1]}")
n = len(api.calls)
rv, text = driven(sh.apply_logging_udp,
                  ["logging", "logging", "udp", "10.0.0.9"])
check(len(api.calls) == n and "error: usage: logging udp "
      "<collector> src <source> [port <n>]" in rows(text),
      f"no src is a usage line and nothing is sent: {text!r}")
rv, text = driven(sh.apply_logging_udp,
                  ["logging", "logging", "udp", "10.0.0.9", "src",
                   "10.0.0.2", "port", "99999"])
check(len(api.calls) == n and
      "error: cgnat logging udp: port 99999 is not one" in rows(text),
      f"a port that is not one: {text!r}")
never = dict(CUR, src_address="0.0.0.0", path_mtu=0, template_interval=0)
api = RecApi(rows={"ipfix_exporter_dump": [never]})
sh = shell(api=api)
sh.apply_logging_udp(["logging", "logging", "udp", "disable"])
check(api.calls == [], "disabling an exporter never set sends nothing: "
      "VPP has no encoding for it")
sh.apply_logging_udp(["logging", "logging", "udp", "10.0.0.9", "src",
                      "10.0.0.2"])
check(api.calls[-1][1]["path_mtu"] == prismash.IPFIX_PATH_MTU
      and api.calls[-1][1]["template_interval"]
      == prismash.IPFIX_TEMPLATE_INTERVAL,
      f"a zero read back is filled by the product's defaults: "
      f"{api.calls[-1]}")
api = RecApi(rows={"ipfix_exporter_dump": []})
sh = shell(api=api)
rv, text = driven(sh.apply_logging_udp,
                  ["logging", "logging", "udp", "10.0.0.9", "src", "10.0.0.2"])
check(api.calls == [] and "error: cgnat logging udp 10.0.0.9 src 10.0.0.2: "
      "the dataplane reports no IPFIX exporter at all" in rows(text),
      f"an empty dump is a dataplane this VTY does not recognise: {text!r}")
api = RecApi(refuse={"ipfix_exporter_dump"})
sh = shell(api=api)
rv, text = driven(sh.apply_logging_udp,
                  ["logging", "logging", "udp", "10.0.0.9", "src", "10.0.0.2"])
check(api.calls == [] and rows(text)[0].startswith(
      "error: cgnat logging udp 10.0.0.9 src 10.0.0.2: cannot read the "
      "IPFIX exporter"), f"a dump refused is said as such: {text!r}")
ok("read back first, change what the statement names, keep the rest")

# ---------------------------------------------- the box: shell, installer

print("\n=== Shell.start_shell: administrators only, spawned interactive, "
      "and back ===")
# Mutation shown red: drop the "-i".
login, log = argv_script("login-shell")
os.environ["PRISMA_SHELL"] = login
try:
    sh = shell(caps={prismash.CAP_CGNAT})
    rv, text = driven(lambda: sh.start_shell())
    check(argv_of(log) is None and rows(text)[0]
          == "error: start shell: permission denied for a cgnat-operator "
             "account",
          f"a cgnat account is refused and nothing is spawned: {text!r}")
    sh = shell()
    rv, text = driven(lambda: sh.start_shell())
    check(argv_of(log) == ["-i"],
          f"the shell is started interactive, as a child: {argv_of(log)}")
    check(rows(text) == [f"entering {login}; type 'exit' to return to the VTY",
                         "back in the Prisma Routing VTY"],
          f"said on the way out and on the way back: {text!r}")
finally:
    del os.environ["PRISMA_SHELL"]
ok("the capability is the boundary; leaving the shell returns to the VTY")

print("\n=== Shell.do_install: only from the live medium, and only an "
      "administrator ===")
# Mutation shown red: drop the running_from_live_medium() gate.
installer, log = argv_script("prisma-routing-install")
saved = prismash.INSTALLER, prismash.LIVE_SQUASHFS
try:
    prismash.INSTALLER = installer
    prismash.LIVE_SQUASHFS = os.path.join(_root, "filesystem.squashfs")
    sh = shell()
    rv, text = driven(lambda: sh.do_install())
    check(argv_of(log) is None and rows(text)[0]
          == "error: this box is not running from the installation medium",
          f"an installed box refuses and starts nothing: {text!r}")
    open(prismash.LIVE_SQUASHFS, "w").close()
    sh = shell(caps={prismash.CAP_CGNAT})
    rv, text = driven(lambda: sh.do_install())
    check(argv_of(log) is None and "permission denied" in text,
          f"on the medium, a cgnat account is still refused: {text!r}")
    sh = shell()
    rv, text = driven(lambda: sh.do_install())
    check(argv_of(log) == [],
          f"an administrator on the medium hands the console to the "
          f"installer, with no argument: {argv_of(log)}")
finally:
    prismash.INSTALLER, prismash.LIVE_SQUASHFS = saved
    if os.path.exists(prismash.LIVE_SQUASHFS):
        os.remove(prismash.LIVE_SQUASHFS)
ok("two gates, and the medium is checked before the role")

print("\n=== Shell.ask_systemd: the argv, and what systemd said as rows ===")
# Mutation shown red: return `said` alone, so a silent refusal has no row.
run = RecRunner({("systemctl", "reboot"): (4, "Failed to reboot system via "
                                             "logind: Access denied\n   See "
                                             "system logs   \n\n")})
sh = shell(runner=run)
rc, said = sh.ask_systemd("reboot")
check(run.asked == [["systemctl", "reboot"]],
      f"the plain verb, nothing forced: {run.asked}")
check(rc == 4 and said == ["Failed to reboot system via logind: Access "
                           "denied", "See system logs"],
      f"each non-empty line a row, its spaces folded: {(rc, said)!r}")
run = RecRunner({("systemctl", "poweroff"): (3, "")})
sh = shell(runner=run)
check(sh.ask_systemd("poweroff") == (3, ["systemctl exited 3 and said "
                                         "nothing"]),
      "an empty answer is named as such rather than left blank")
ok("systemctl <verb>, its rows, and a silence that says it is one")

print("\n=== Shell.do_system: reboot and shutdown, said before asked, "
      "refused in systemd's words ===")
# Mutation shown red: `shutdown` asks for "reboot".
for rest, verb, head in ((["reboot"], "reboot", "restarting the box"),
                         (["shutdown"], "poweroff", "shutting the box down")):
    run = RecRunner()
    sh = shell(runner=run)
    rv, text = driven(lambda: sh.do_system(rest))
    check([a for a in run.asked if a[0] == "systemctl"]
          == [["systemctl", verb]], f"{rest}: systemctl {verb}: {run.asked}")
    check(rows(text)[0] == head and
          any("every session on this box ends" in r for r in rows(text)),
          f"{rest}: the row, then what it costs: {text!r}")
    check(sh.failed == 0, f"{rest}: rc 0 is not a refusal")
run = RecRunner({("systemctl", "reboot"):
                 (4, "Failed to start reboot.target: Access denied\n")})
sh = shell(runner=run)
rv, text = driven(lambda: sh.do_system(["reboot"]))
check(sh.failed == 1 and "error: the box could not be restarted" in rows(text)
      and any("Failed to start reboot.target" in r for r in rows(text)),
      f"a refusal is the verdict with systemd's rows under it: {text!r}")
run = RecRunner()
sh = shell(runner=run, caps={prismash.CAP_CGNAT})
rv, text = driven(lambda: sh.do_system(["reboot"]))
check(run.asked == [] and "permission denied" in text,
      f"a cgnat account is refused before systemd is asked: {run.asked}")
run = RecRunner()
sh = shell(runner=run)
rv, text = driven(lambda: sh.do_system(["halt"]))
check(run.asked == [] and rows(text)
      == ["error: usage: system reboot | shutdown | upgrade | dataplane "
          "restart"],
      f"a verb it has not got is a usage line: {text!r}")
ok("two verbs, two units, the role checked first and the cost said first")

print("\n=== cards added to the running box, on AF_XDP (#453) ===")
_hd = tempfile.mkdtemp()
_hf = os.path.join(_hd, "hot-cards")
check(prismash.hot_cards(_hf) == {}, "no record is no hot card")
with open(_hf, "w") as _f:
    _f.write("ens20 0000:00:14.0 xdp-ens20\nens21 0000:00:15.0 xdp-ens21\n")
check(prismash.hot_cards(_hf) == {"ens20": "0000:00:14.0",
                                  "ens21": "0000:00:15.0"},
      f"read as name and PCI: {prismash.hot_cards(_hf)!r}")
_notes = prismash.hot_card_notes({"ens20": {}, "ens18": {}}, _hf)
check(len(_notes) == 2 and _notes[0].startswith("  ens20: added to the running")
      and "ens21" not in _notes[0] and "system dataplane restart" in _notes[1],
      f"show interface names the hot cards it lists, and what moves them: "
      f"{_notes!r}")
check(all(len(l) <= 79 for l in _notes), "each row fits the console")
check(prismash.hot_card_notes({"ens18": {}}, _hf) == [],
      "and says nothing when none of them is on the screen")
# Mutation shown red: system_dataplane_restart() calling the tool before
# the cost is said - the screen at the instant it is run is read here.
_tool = os.path.join(_hd, "tool")
with open(_tool, "w") as _f:
    _f.write("#!/bin/sh\necho \"$@\" > " + os.path.join(_hd, "asked") +
             "\nexit ${HOTRC:-0}\n")
os.chmod(_tool, 0o755)
_saved = prismash.HOT_CARD_TOOL, prismash.HOT_CARDS
prismash.HOT_CARD_TOOL, prismash.HOT_CARDS = _tool, _hf
try:
    sh = shell()
    rv, text = driven(lambda: sh.system_dataplane_restart())
    with open(os.path.join(_hd, "asked")) as _f:
        _asked = _f.read().strip()
    check(_asked == "restart-dataplane"
          and "port block" in text and "ens20, ens21" in text
          and "the dataplane is starting" in text,
          f"it says the cost and which cards move, then asks the tool: "
          f"{_asked!r} {text!r}")
    os.environ["HOTRC"] = "3"
    rv, text = driven(lambda: sh.do_system(["dataplane", "restart"]))
    check("the dataplane could not be restarted" in text,
          f"a restart that failed is said as one: {text!r}")
    sh = shell(caps={prismash.CAP_CGNAT})
    os.remove(os.path.join(_hd, "asked"))
    rv, text = driven(lambda: sh.do_system(["dataplane", "restart"]))
    check("permission denied" in text
          and not os.path.exists(os.path.join(_hd, "asked")),
          f"a cgnat account is refused before anything runs: {text!r}")
finally:
    os.environ.pop("HOTRC", None)
    prismash.HOT_CARD_TOOL, prismash.HOT_CARDS = _saved
ok("the record, the notice and the restart that takes them through DPDK")

# #564: on a box the restart runs as a unit of its own and the VTY only
# follows it - on 10.95.200.198 the API's run of it died with the containers
# it stopped, and nothing started them again.
_argv = prismash.dataplane_restart_argv("/x/hot", "/x/log")
check(_argv[:3] == ("systemd-run", "--collect",
                    "--unit=prisma-routing-dataplane-restart")
      and "exec >/x/log 2>&1;" in _argv[-1]
      and _argv[-1].endswith('/x/hot restart-dataplane; echo "prisma-api-exit $?"'),
      f"a unit of its own, its output in the log, its exit line last: {_argv!r}")
check(prismash.detach_dataplane_restart(prismash.HOT_CARD_INSTALLED,
                                        which=lambda c: "/usr/bin/" + c,
                                        isdir=lambda d: True)
      and not prismash.detach_dataplane_restart("/tmp/stand-in",
                                                which=lambda c: "/usr/bin/" + c,
                                                isdir=lambda d: True)
      and not prismash.detach_dataplane_restart(prismash.HOT_CARD_INSTALLED,
                                                which=lambda c: None,
                                                isdir=lambda d: True)
      and not prismash.detach_dataplane_restart(prismash.HOT_CARD_INSTALLED,
                                                which=lambda c: "/usr/bin/" + c,
                                                isdir=lambda d: False),
      "detached only with the installed tool, systemd-run and a running systemd")
sh = shell()
_ran = []
sh.sys._run = lambda argv, **k: (_ran.append(argv), (0, ""))[1]
rv, text = driven(lambda: sh.dataplane_restart_detached(
    follow=lambda path, started, wait: 0))
check(_ran and _ran[0][0] == "systemd-run" and "the dataplane is starting" in text,
      f"it starts the unit and says the dataplane is starting: {_ran!r} {text!r}")
rv, text = driven(lambda: sh.dataplane_restart_detached(
    follow=lambda path, started, wait: 1))
check("could not be restarted" in text, f"a unit that failed is said: {text!r}")
rv, text = driven(lambda: sh.dataplane_restart_detached(
    follow=lambda path, started, wait: None))
check("did not finish" in text and "goes on as a unit" in text,
      f"a unit still running past the wait is said, not called failed: {text!r}")
sh.sys._run = lambda argv, **k: (1, "Unit prisma-routing-dataplane-restart.service was already loaded")
rv, text = driven(lambda: sh.dataplane_restart_detached(follow=lambda *a, **k: 0))
check("could not be started" in text and "already loaded" in text,
      f"a restart already running is refused with systemd's words: {text!r}")
ok("the dataplane restart runs as a unit of its own and is followed (#564)")

# The advice names a command that can be followed (#453): on the containers
# `systemctl restart prisma-routing-containers` is refused by systemd, so the
# screens send the operator to the VTY's own command there.
_du = prismash.dataplane_unit
try:
    prismash.dataplane_unit = lambda: prismash.DATAPLANE_UNIT_CONTAINERS
    check(prismash.dataplane_restart_advice() == "'system dataplane restart'",
          f"the containers: {prismash.dataplane_restart_advice()!r}")
    prismash.dataplane_unit = lambda: prismash.DATAPLANE_UNIT_VPP
    check(prismash.dataplane_restart_advice() == "'systemctl restart vpp'",
          f"the unit path: {prismash.dataplane_restart_advice()!r}")
finally:
    prismash.dataplane_unit = _du
ok("restart advice that can be followed on either dataplane")

print("\n=== Shell.after_boot: what a replay of the file leaves on the "
      "box ===")
# Mutation shown red: drop the dataplane_if carry-over.
running = Config.parse("user alice\n role admin\ndataplane interface ens19\n"
                       "nat cgnat pool A\n inside 10.0.0.0/8\n")
empty = Config.parse("!\n! Prisma Routing\n!\n")
boot = shell().after_boot(empty, running)
check("alice" in boot.users and boot.dataplane_if == "ens19"
      and boot.pools == {},
      f"an empty file: the accounts and the card stay, the pools go: "
      f"users {sorted(boot.users)} card {boot.dataplane_if!r} pools "
      f"{sorted(boot.pools)}")
saved_cfg = Config.parse("user bob\n role admin\n")
boot = shell().after_boot(saved_cfg, running)
check("bob" in boot.users and "alice" not in boot.users,
      f"a file that says something is the holder of the accounts: "
      f"{sorted(boot.users)}")
check(boot.dataplane_if == "ens19",
      "and the card is the tuner's either way, taken from the box")
saved_cfg = Config.parse("user bob\n role admin\ndataplane interface ens20\n")
check(shell().after_boot(saved_cfg, running).dataplane_if == "ens20",
      "a file naming a card keeps its own")
ok("load()'s two decisions, on Config objects")

# ------------------------------------------------------- the sub-modes

print("\n=== Shell.do_snmp_user: auth and priv, whole, and nothing else ===")
# Mutation shown red: setattr(u, "auth", ...) for both words.
sh = shell()
sh.mode, sh.snmp_user = "snmp-user", SnmpUser("mon")
sh.do_snmp_user("auth sha secret words")
sh.do_snmp_user("priv aes other")
check((sh.snmp_user.auth, sh.snmp_user.priv) == ("sha secret words",
                                                 "aes other"),
      f"each word sets its own field, the rest of the row whole: "
      f"{(sh.snmp_user.auth, sh.snmp_user.priv)}")
rv, text = driven(sh.do_snmp_user, "auth sha")
check(rows(text)[0] == "error: a v3 user takes 'auth' or 'priv'"
      and sh.snmp_user.auth == "sha secret words",
      f"a protocol with no passphrase is refused and changes nothing: "
      f"{text!r}")
rv, text = driven(sh.do_snmp_user, "bogus x y")
check(rows(text)[0] == "error: a v3 user takes 'auth' or 'priv'",
      f"any other word is the same refusal: {text!r}")
u = sh.snmp_user
check(sh.do_snmp_user("exit") is True and sh.mode == "config"
      and sh.snmp_user is None, "exit returns to config mode")
ok("two statements, each with a protocol and a passphrase")

print("\n=== Shell.do_user: keys, a hash and a role; plaintext refused ===")
# Mutation shown red: accept a password that is not a crypt hash.
sh = shell()
sh.mode, sh.user = "user", User("bob")
sh.do_user("ssh-key ssh-ed25519 AAAAC3 bob@box")
sh.do_user("ssh-key ssh-ed25519 AAAAC3 bob@box")
sh.do_user("ssh-key ssh-rsa AAAAB3 bob@old")
check(sh.user.keys == ["ssh-ed25519 AAAAC3 bob@box", "ssh-rsa AAAAB3 bob@old"],
      f"a key once, however often typed: {sh.user.keys}")
rv, text = driven(sh.do_user, "password hunter2")
check(sh.user.password == "" and rows(text)[0].startswith(
      "error: expected a crypt hash"),
      f"plaintext is refused, not stored: {text!r}")
sh.do_user("password $6$salt$hash")
check(sh.user.password == "$6$salt$hash", "a crypt hash is taken")
sh.do_user(f"role {system.ROLE_ADMIN}")
check(sh.user.role == system.ROLE_ADMIN, "a role the box has is taken")
rv, text = driven(sh.do_user, "role sideways")
check(sh.user.role == system.ROLE_ADMIN
      and rows(text)[0].startswith("error: role 'sideways'"),
      f"a role it has not got is refused with the list: {text!r}")
sh.do_user("no ssh-key ssh-rsa AAAAB3")
check(sh.user.keys == ["ssh-ed25519 AAAAC3 bob@box"],
      f"no ssh-key removes by prefix, comment or not: {sh.user.keys}")
sh.do_user("no role")
sh.do_user("no password")
check(sh.user.role == "" and sh.user.password == "",
      "no role and no password clear each")
rv, text = driven(sh.do_user, "no sideways")
check(rows(text) == ["error: unknown statement in user mode: sideways"],
      f"the word after `no` is the one named: {text!r}")
rv, text = driven(sh.do_user, "sideways")
check(rows(text) == ["error: unknown statement in user mode: sideways"],
      f"an unknown statement is named: {text!r}")
check(sh.do_user("exit") is True and sh.mode == "config"
      and sh.user is None, "exit returns to config mode")
ok("the account's three settings, each set and each taken back")

print("\n=== Shell.pool_range: a ValueError carrying Pool's own reason ===")
# Mutation shown red: never raise.
sh = shell()
check(sh.pool_range("block-size", "1024") is None, "inside the range: None")
try:
    sh.pool_range("block-size", "16")
    check(False, "16 is under the 64 the statement is offered with")
except ValueError as e:
    check(str(e) == Pool.check_range("block-size", "16"),
          f"the reason is Pool's, unchanged: {e}")
try:
    sh.pool_range("ports", "70000", "max")
    check(False, "70000 is not a port")
except ValueError as e:
    check("ports max" in str(e), f"`what` names the half refused: {e}")
ok("raised, because do_pool's chain already answers a ValueError")

print("\n=== Shell.do_pool: the statements, bounded where the request is "
      "made ===")
# Mutation shown red: drop the Pool.check_order() refusal.
sh = shell()
sh.mode, sh.pool = "pool", Pool("A")
sh.do_pool("prefix-inside 10.0.0.0/8")
sh.do_pool("prefix-outside 203.0.113.0/24")
check(sh.pool.inside == ["10.0.0.0/8"]
      and sh.pool.outside == ["203.0.113.0/24"],
      "prefix-inside and prefix-outside append")
# The old spellings, typed by habit or pasted from an earlier release's saved
# file, are still read as the same statements (#531) - and `no` takes them too.
sh.do_pool("inside 10.1.0.0/16")
sh.do_pool("outside 198.51.100.0/30")
check(sh.pool.inside == ["10.0.0.0/8", "10.1.0.0/16"]
      and sh.pool.outside == ["203.0.113.0/24", "198.51.100.0/30"],
      f"inside / outside are read as prefix-inside / -outside: "
      f"{sh.pool.inside} {sh.pool.outside}")
sh.do_pool("no inside 10.1.0.0/16")
sh.do_pool("no prefix-outside 198.51.100.0/30")
check(sh.pool.inside == ["10.0.0.0/8"]
      and sh.pool.outside == ["203.0.113.0/24"],
      f"and removed by either spelling after no: {sh.pool.inside} "
      f"{sh.pool.outside}")
rv, text = driven(sh.do_pool, "no prefix-inside 192.0.2.0/24")
check("the pool has no prefix-inside 192.0.2.0/24" in text,
      f"a prefix the pool does not have is named in the new word: {text!r}")
check(prismash.pool_prefix_spelled(["inside", "10.0.0.0/8"])
      == ["prefix-inside", "10.0.0.0/8"]
      and prismash.pool_prefix_spelled(["no", "outside", "x"])
      == ["no", "prefix-outside", "x"]
      and prismash.pool_prefix_spelled(["bypass", "inside", "x"])
      == ["bypass", "inside", "x"]
      and prismash.pool_prefix_spelled([]) == [],
      "pool_prefix_spelled renames the statement, and the word after no, "
      "and nothing else")
_half = Pool("H")
_half.inside = ["100.64.0.0/22"]
check(prismash.pool_prefixes_missing(_half)
      == ("pool 'H': no prefix-outside",
          "e.g. 'prefix-outside 203.0.113.0/28'"),
      f"one half missing names that half: {prismash.pool_prefixes_missing(_half)}")
_half = Pool("O")
_half.outside = ["203.0.113.0/30"]
check(prismash.pool_prefixes_missing(_half)
      == ("pool 'O': no prefix-inside", "e.g. 'prefix-inside 100.64.0.0/10'"),
      f"the other half: {prismash.pool_prefixes_missing(_half)}")
check(prismash.pool_prefixes_missing(Pool("E"))
      == ("pool 'E': no prefix-inside and no prefix-outside",
          "e.g. 'prefix-inside 100.64.0.0/10', 'prefix-outside 203.0.113.0/28'"),
      f"both missing names both: {prismash.pool_prefixes_missing(Pool('E'))}")
rv, text = driven(sh.do_pool, "block-size 100")
check(sh.pool.block_size != "100" and "power of two" in text,
      f"100 is not a power of two and is said here, not at commit: {text!r}")
rv, text = driven(sh.do_pool, "block-size 16")
check(text.startswith("error: invalid argument: block-size 16"),
      f"16 is under the range, through pool_range: {text!r}")
sh.do_pool("block-size 1024")
check(sh.pool.block_size == "1024", "1024 is taken")
rv, text = driven(sh.do_pool, "ports 9000 100")
check(sh.pool.ports != "9000 100" and rows(text)[0].startswith(
      "error: invalid argument: ports 9000 100"),
      f"two legal ports in the wrong order are refused: {text!r}")
rv, text = driven(sh.do_pool, "ports 1024 70000")
check(rows(text)[0].startswith("error: invalid argument: ports max 70000"),
      f"the half that is wrong is named: {text!r}")
sh.do_pool("ports 1024 65535")
check(sh.pool.ports == "1024 65535", "a legal range is taken")
rv, text = driven(sh.do_pool, "policy sideways")
check(rows(text) == ["error: invalid argument: policy must be interleaved "
                     "or blocked"], f"policy is one of two: {text!r}")
sh.do_pool("mode dynamic")
check(sh.pool.mode == "dynamic", "mode is taken")
rv, text = driven(sh.do_pool, "no block-size")
check(rows(text)[0] == "error: block-size cannot be removed",
      f"a setting every pool has cannot be taken off: {text!r}")
rv, text = driven(sh.do_pool, "no sideways")
check(rows(text) == ["error: unknown statement in pool mode: sideways"],
      f"the word after `no` is named: {text!r}")
check(sh.do_pool("exit") is True and sh.mode == "config"
      and sh.pool is None, "exit returns to config mode")
ok("each statement staged on the pool, each bound refused with its row")

# -------------------------------------------------- the tuner's file

print("\n=== the CGNAT's memory: what the heap holds and what fits (#454) ===")
# The parsers, on VPP's own text as it printed it in lab/heap-full.sh.
check(prismash.size_bytes("848.18M") == int(848.18 * (1 << 20))
      and prismash.size_bytes("4.00G") == 4 << 30
      and prismash.size_bytes("512") == 512
      and prismash.size_bytes("lots") is None, "a VPP size, read in bytes")
_heap = ("  base 0x7f, size 4g, locked, name 'main heap'\n"
         "    page stats: page-size 4K, total 1048576\n"
         "    total: 4.00G, used: 848.18M, free: 3.17G, trimmable: 3.05G\n")
check(prismash.heap_figures(_heap) == (4 << 30, int(848.18 * (1 << 20)),
                                       int(3.17 * (1 << 30))),
      f"the heap's three figures: {prismash.heap_figures(_heap)}")
check(prismash.heap_figures("unknown input") is None, "and none from nothing")
_ses = "thread 0: 2000000 sessions, 4001 active subscribers\ntotal: 2000000 sessions\n"
check(prismash.sessions_now(_ses) == 2000000, "the session total")
check(prismash.sessions_now("") is None, "and no answer from nothing")
_errs = ("     1533394             bpa-in2out                no memory for a new session       error\n"
         "         7             bpa-in2out                no memory for a new session       error\n"
         "    466606             bpa-in2out                     packets translated           error\n")
check(prismash.refused_for_memory(_errs) == 1533401,
      "the refusals, summed over every line that carries them")
check(prismash.cgnat_session_capacity(8) > 27_000_000
      and prismash.cgnat_session_capacity(0) == 0,
      f"what a heap holds: {prismash.cgnat_session_capacity(8):,} in 8 GB")

_tune = os.path.join(_root, "tune-mem", "tune.env")
_saved_te = prismash.Shell.TUNE_ENV
try:
    prismash.Shell.TUNE_ENV = _tune
    os.makedirs(os.path.dirname(_tune), exist_ok=True)
    with open(_tune, "w") as _f:
        _f.write("PRISMA_SUBSCRIBERS=200000\nPRISMA_CGNAT_MEMORY_GB=8\n")
    sh = shell(dp=RecDp({"show memory main-heap": (0, _heap),
                         "show bpa session": (0, _ses),
                         "show errors": (0, _errs)}))
    rv, text = driven(lambda: sh.show_cgnat_memory())
    check("main heap GB          4.00" in text and "in use GB             0.83" in text
          and "sessions now          2,000,000" in text
          and "refused, no memory    1,533,401" in text
          and "given GB              8" in text and "from the next reboot" in text,
          f"the screen: the heap, what it holds and what fits, the refusals, "
          f"and a figure not yet in effect said as one: {text!r}")
    sh = shell(dp=RecDp({"show memory main-heap": (1, "unknown input")}))
    rv, text = driven(lambda: sh.show_cgnat_memory())
    check("show cgnat memory: the dataplane's heap could not be read" in text,
          f"a heap that cannot be read is said: {text!r}")
    # apply_cgnat_memory: the key written, the tuner asked, the sentence said.
    run = RecRunner()
    sh = shell(runner=run)
    ok_, msg = sh.apply_cgnat_memory(16)
    with open(_tune) as _f:
        _got = _f.read()
    check(ok_ and "PRISMA_CGNAT_MEMORY_GB=16" in _got,
          f"the figure reaches tune.env: {_got!r}")
    ok_, msg = sh.apply_cgnat_memory(0)
    with open(_tune) as _f:
        _got = _f.read()
    check(ok_ and "PRISMA_CGNAT_MEMORY_GB=\n" in _got,
          f"and 'no memory' empties it: {_got!r}")
    prismash.Shell.TUNE_ENV = os.path.join(_root, "no-such", "deeper", "x")
    os.makedirs(os.path.join(_root, "no-such"), exist_ok=True)
    with open(os.path.join(_root, "no-such", "deeper"), "w") as _f:
        _f.write("a file where a directory is wanted")
    sh = shell()
    sh.candidate = sh.running_config()
    sh.candidate.cgnat_memory_gb = 8
    rv, text = driven(lambda: sh.do_commit([]))
    check("cgnat memory:" in text and "commit" in text,
          f"a tune.env that cannot be written fails the commit, said: {text!r}")
finally:
    prismash.Shell.TUNE_ENV = _saved_te

print("\n=== system cpu: the cores by role, typed, stored, read back (#466) ===")
check(_config.cpu_error("control-plane", "2") is None
      and _config.cpu_error("main-thread", "dedicated") is None
      and _config.cpu_error("control-plane", "0").startswith("expected")
      and _config.cpu_error("control-plane", "1025").startswith("expected")
      and _config.cpu_error("main-thread", "both").startswith("expected")
      and _config.cpu_error("workers", "4") is None
      and _config.cpu_error("workers", "0").startswith("expected")
      and "control-plane" in _config.cpu_error("turbo", "on"),
      "cpu_error: the two statements, at their bounds, and a third refused")
_tune = os.path.join(_root, "tune-cpu", "tune.env")
_saved_te = prismash.Shell.TUNE_ENV
_saved_iso = prismash.Shell.CPU_ISOLATED
_saved_sc = prismash.Shell.STARTUP_CONF
try:
    prismash.Shell.TUNE_ENV = _tune
    os.makedirs(os.path.dirname(_tune), exist_ok=True)
    with open(_tune, "w") as _f:
        _f.write("PRISMA_SUBSCRIBERS=200000\nPRISMA_CGNAT_MEMORY_GB=8\n"
                 "PRISMA_CONTROL_PLANE_CORES=\nPRISMA_MAIN_THREAD=\n")
    sh = shell()
    # The real reader, not the harness's stub of it: what is under test is
    # that it reads tune.env.
    _real = lambda: prismash.Shell.running_config(sh)
    sh.candidate = _real()
    check(sh.candidate.cpu == {} and sh.candidate.cgnat_memory_gb == 8,
          f"read back from tune.env: nothing set, and the CGNAT memory that "
          f"was written and never read back until now: {sh.candidate.cpu} "
          f"{sh.candidate.cgnat_memory_gb}")
    for _l in ("system", "cpu", "control-plane 2", "main-thread dedicated",
               "cgnat-workers 2", "no cgnat-workers", "workers 5"):
        driven(lambda: sh.do_config(_l))
    check(sh.candidate.cpu == {"control-plane": "2",
                               "main-thread": "dedicated", "workers": "5"},
          f"typed under system > cpu, held as the model's words: "
          f"{sh.candidate.cpu}")
    rv, text = driven(lambda: sh.do_config("show this"))
    check(text.split() == ["control-plane", "2", "main-thread", "dedicated",
                           "workers", "5"],
          f"show this, at the cpu level: {text!r}")
    for _bad, _why in (("control-plane 0", "expected: control-plane <1-1024>"),
                       ("main-thread alone",
                        "expected: main-thread <shared|dedicated>"),
                       ("turbo on", "there is 'control-plane', "
                                    "'main-thread', 'workers' and "
                                    "'cgnat-workers'"),
                       ("workers 0", "expected: workers <1-1024>"),
                       ("cgnat-workers 0", "expected: cgnat-workers "
                                           "<1-1024>"),
                       ("no turbo", "cpu: no <control-plane|main-thread|workers|")):
        rv, text = driven(lambda: sh.do_config(_bad))
        check(_why in text and sh.candidate.cpu["control-plane"] == "2",
              f"{_bad!r} refused, nothing changed: {text!r}")
    ok_, msg = sh.apply_cpu(sh.candidate.cpu)
    with open(_tune) as _f:
        _got = _f.read()
    check(ok_ and "PRISMA_CONTROL_PLANE_CORES=2\n" in _got
          and "PRISMA_MAIN_THREAD=dedicated\n" in _got
          and "PRISMA_MAX_WORKERS=5\n" in _got
          and "PRISMA_CGNAT_MEMORY_GB=8\n" in _got,
          f"both keys reach tune.env, and the rest stays: {_got!r}")
    back = _real()
    check(back.cpu == {"control-plane": "2", "main-thread": "dedicated",
                       "workers": "5"},
          f"and are read back as the running configuration: {back.cpu}")
    check("system\n cpu\n  control-plane 2\n  main-thread dedicated\n"
          "  workers 5\n  exit"
          in back.text(), "which writes them in the system block")
    driven(lambda: sh.do_config("no main-thread"))
    driven(lambda: sh.do_config("no control-plane"))
    driven(lambda: sh.do_config("no workers"))
    ok_, msg = sh.apply_cpu(sh.candidate.cpu)
    with open(_tune) as _f:
        _got = _f.read()
    check(ok_ and "PRISMA_CONTROL_PLANE_CORES=\n" in _got
          and "PRISMA_MAIN_THREAD=\n" in _got
          and "PRISMA_MAX_WORKERS=\n" in _got and _real().cpu == {},
          f"'no' empties both, which is the default: {_got!r}")
    # A tune.env that cannot be written fails the commit, and says so.
    prismash.Shell.TUNE_ENV = os.path.join(_root, "no-such", "deeper", "y")
    sh2 = shell()
    sh2.candidate = prismash.Shell.running_config(sh2)
    sh2.candidate.cpu = {"control-plane": "2"}
    rv, text = driven(lambda: sh2.do_commit([]))
    check("cpu:" in text, f"an unwritable tune.env fails the commit: {text!r}")
    prismash.Shell.TUNE_ENV = _tune
    # show system cpu: configured beside this boot.
    _iso = os.path.join(_root, "tune-cpu", "isolated")
    _sc = os.path.join(_root, "tune-cpu", "startup.conf")
    with open(_iso, "w") as _f:
        _f.write("1-3\n")
    with open(_sc, "w") as _f:
        _f.write("cpu {\n  main-core 0\n  corelist-workers 1-3\n}\n")
    prismash.Shell.CPU_ISOLATED = _iso
    prismash.Shell.STARTUP_CONF = _sc
    _fe = os.path.join(_root, "tune-cpu", "fence")
    with open(_fe, "w") as _f:
        _f.write("0\n")
    _saved_fe = prismash.Shell.CPU_FENCE
    prismash.Shell.CPU_FENCE = _fe
    rv, text = driven(lambda: sh.show_system_cpu())
    check("control plane held to 0" in text,
          f"the fence, as the kernel applies it: {text!r}")
    _on = os.path.join(_root, "tune-cpu", "online")
    with open(_on, "w") as _f:
        _f.write("0\n")
    _saved_on = prismash.Shell.CPU_ONLINE
    prismash.Shell.CPU_ONLINE = _on
    rv, text = driven(lambda: sh.show_system_cpu())
    check("control plane held to (not held)" in text,
          f"a fence that is every CPU is no fence: {text!r}")
    prismash.Shell.CPU_ONLINE = _saved_on
    prismash.Shell.CPU_FENCE = os.path.join(_root, "no-such-fence")
    rv, text = driven(lambda: sh.show_system_cpu())
    check("control plane held to (not held)" in text,
          f"no cgroup to read is said as not held: {text!r}")
    prismash.Shell.CPU_FENCE = _saved_fe
    rv, text = driven(lambda: sh.show_system_cpu())
    check("CGNAT workers         all, each also receiving" in text
          and "translating workers   all of them" in text,
          f"no split, said as such: {text!r}")
    with open(_sc, "a") as _f:
        _f.write("bpa {\n  cgnat-workers 1-2\n}\n")
    rv, text = driven(lambda: sh.show_system_cpu())
    check("translating workers   1-2" in text,
          f"a split, as the plugin's stanza names it: {text!r}")
    check("VPP workers at most   every core left" in text,
          f"no bound on the workers, said as such: {text!r}")
    check("control-plane cores   1" in text and "VPP main thread       shared"
          in text and "core 0" in text and "VPP workers           1-3" in text
          and "isolated from Linux   1-3" in text,
          f"show system cpu: {text!r}")
finally:
    prismash.Shell.TUNE_ENV = _saved_te
    prismash.Shell.CPU_ISOLATED = _saved_iso
    prismash.Shell.STARTUP_CONF = _saved_sc

print("\n=== system memory: the RAM by role, typed, stored, read back (#466) ===")
check(_config.memory_error("control-plane", "8") is None
      and _config.memory_error("buffers", "65536") is None
      and _config.memory_error("control-plane", "0").startswith("expected")
      and _config.memory_error("buffers", "10").startswith("expected")
      and "'control-plane' and 'buffers'" in _config.memory_error("x", "1"),
      "memory_error: the two statements, at their bounds, and a third refused")
_tune = os.path.join(_root, "tune-mem2", "tune.env")
_saved_te = prismash.Shell.TUNE_ENV
_saved_sc = prismash.Shell.STARTUP_CONF
_saved_mi = prismash.Shell.MEMINFO
_saved_cm = prismash.Shell.CP_MEMORY_NOW
try:
    prismash.Shell.TUNE_ENV = _tune
    os.makedirs(os.path.dirname(_tune), exist_ok=True)
    with open(_tune, "w") as _f:
        _f.write("PRISMA_CGNAT_MEMORY_GB=8\n")
    sh = shell()
    _real = lambda: prismash.Shell.running_config(sh)
    sh.candidate = _real()
    check(sh.candidate.memory == {}, "nothing set, nothing read back")
    for _l in ("system", "memory", "control-plane 12", "buffers 65536"):
        driven(lambda: sh.do_config(_l))
    check(sh.candidate.memory == {"control-plane": "12",
                                  "buffers": "65536"},
          f"typed under system > memory: {sh.candidate.memory}")
    rv, text = driven(lambda: sh.do_config("show this"))
    check(text.split() == ["control-plane", "12", "buffers", "65536"],
          f"show this at the memory level: {text!r}")
    for _bad, _why in (("buffers 5", "expected: buffers <1024-"),
                       ("no turbo", "memory: no <control-plane|buffers>")):
        rv, text = driven(lambda: sh.do_config(_bad))
        check(_why in text, f"{_bad!r} refused: {text!r}")
    ok_, msg = sh.apply_memory(sh.candidate.memory)
    with open(_tune) as _f:
        _got = _f.read()
    check(ok_ and "PRISMA_CONTROL_PLANE_MEMORY_GB=12\n" in _got
          and "PRISMA_BUFFERS_PER_NUMA=65536\n" in _got
          and "PRISMA_CGNAT_MEMORY_GB=8\n" in _got,
          f"both keys reach tune.env, the rest stays: {_got!r}")
    check(_real().memory == {"control-plane": "12", "buffers": "65536"},
          "and are read back into the running configuration")
    check("system\n memory\n  control-plane 12\n  buffers 65536\n  exit"
          in _real().text(), "which writes them in the system block")
    _d = os.path.join(_root, "tune-mem2")
    with open(os.path.join(_d, "meminfo"), "w") as _f:
        _f.write("MemTotal:       16777216 kB\nHugePages_Total:       4\n"
                 "Hugepagesize:    1048576 kB\n")
    with open(os.path.join(_d, "startup.conf"), "w") as _f:
        _f.write("buffers {\n  buffers-per-numa 49152\n}\nmemory {\n"
                 "  main-heap-size 2G\n}\n")
    with open(os.path.join(_d, "cp"), "w") as _f:
        _f.write(str(3 * 1073741824) + "\n")
    prismash.Shell.MEMINFO = os.path.join(_d, "meminfo")
    prismash.Shell.STARTUP_CONF = os.path.join(_d, "startup.conf")
    prismash.Shell.CP_MEMORY_NOW = os.path.join(_d, "cp")
    rv, text = driven(lambda: sh.show_system_memory())
    check("control plane keeps   12 GB" in text
          and "CGNAT memory          8 GB" in text
          and "RAM                   16.0 GB" in text
          and "hugepages             4.0 GB" in text
          and "VPP main heap         2G" in text
          and "packet buffers        49152 per NUMA node" in text
          and "statistics segment    VPP default, 32M" in text
          and "control plane uses    3.0 GB" in text,
          f"show system memory: configured beside this boot: {text!r}")
    prismash.Shell.MEMINFO = os.path.join(_d, "none")
    prismash.Shell.STARTUP_CONF = os.path.join(_d, "none")
    prismash.Shell.CP_MEMORY_NOW = os.path.join(_d, "none")
    rv, text = driven(lambda: sh.show_system_memory())
    check("RAM                   (unknown)" in text
          and "VPP main heap         (not named)" in text
          and "control plane uses    (unknown)" in text,
          f"what cannot be read is said: {text!r}")
    prismash.Shell.TUNE_ENV = os.path.join(_root, "no-such", "deeper", "z")
    sh2 = shell()
    sh2.candidate = prismash.Shell.running_config(sh2)
    # The reading itself, outside the dump cache running_config() opens
    # (#520): the same configuration either way, since nothing in it writes.
    check(prismash.Shell._running_config(sh2).text() == sh2.candidate.text(),
          "_running_config reads what running_config does")
    sh2.candidate.memory = {"buffers": "65536"}
    rv, text = driven(lambda: sh2.do_commit([]))
    check("memory:" in text, f"an unwritable tune.env fails the commit: "
                             f"{text!r}")
finally:
    prismash.Shell.TUNE_ENV = _saved_te
    prismash.Shell.STARTUP_CONF = _saved_sc
    prismash.Shell.MEMINFO = _saved_mi
    prismash.Shell.CP_MEMORY_NOW = _saved_cm
# The statement at the prompt: a number of whole GB, and nothing else.
sh = shell()
sh.candidate = sh.running_config()
driven(lambda: sh.do_config("nat"))
driven(lambda: sh.do_config("cgnat"))
rv, text = driven(lambda: sh.do_config("memory 8G"))
check("usage: memory <GB>, whole GB from 1 to 4096" in text
      and sh.candidate.cgnat_memory_gb == 0, f"8G is refused: {text!r}")
driven(lambda: sh.do_config("memory 8"))
check(sh.candidate.cgnat_memory_gb == 8, "8 is staged")
driven(lambda: sh.do_config("no memory"))
check(sh.candidate.cgnat_memory_gb == 0, "and 'no memory' takes it out")
ok("the CGNAT's memory: stored, shown, refused when it is not a figure")

print("\n=== system > dataplane: the levels, the file and the old line (#457) ===")
check(_config.nest_system("ens19") == ["system", " dataplane", "  interface ens19",
                                      "  exit", " exit"],
      f"the block a file carries: {_config.nest_system('ens19')}")
check(_config.nest_system("") == [], "and nothing when no card was named")
_n, _bad = _config.denest_system(list(enumerate(
    ["system", " dataplane", "  interface ens19", "  exit", " exit",
     "ssh port 22"], 1)))
check([l for _, l in _n] == ["dataplane interface ens19", "ssh port 22"]
      and _bad == [], f"read back as the model's statement: {_n} {_bad}")
_n, _bad = _config.denest_system(list(enumerate(
    ["dataplane interface ens19"], 1)))
check([l for _, l in _n] == ["dataplane interface ens19"] and _bad == [],
      "the old top-level line is still read: a box replays its file at boot")
_n, _bad = _config.denest_system(list(enumerate(["system", " dns", " exit"], 1)))
check(_bad and "there is 'dataplane', 'cpu' and 'memory'" in _bad[0][2],
      f"anything else under system is said: {_bad}")
_c = _config.Config.parse("system\n dataplane\n  interface ens19\n  exit\n exit\n")
check(_c.dataplane_if == "ens19" and not _c.unknown
      and "  interface ens19" in _c.text().splitlines(),
      f"a file round-trips through the block: {_c.dataplane_if!r} {_c.unknown}")
sh = shell()
sh.candidate = sh.running_config()
rv, text = driven(lambda: sh.do_config("dataplane interface ens19"))
check("'dataplane' has moved under 'system'" in text
      and sh.candidate.dataplane_if == "",
      f"the old spelling at the prompt is refused with where it went: {text!r}")
check(sh.system_rewrite("system") == "" and sh.nat_ctx == ["system"],
      "system enters its level")
rv, text = driven(lambda: sh.do_config("dns"))
check("under 'system' there is 'dataplane', 'cpu' and 'memory'" in text,
      f"and only those three are under it (#466): {text!r}")
check(sh.system_rewrite("dataplane") == ""
      and sh.nat_ctx == ["system", "dataplane"], "dataplane enters its level")
check(sh.system_rewrite("interface ens19") == "dataplane interface ens19"
      and sh.system_rewrite("no interface") == "no dataplane interface",
      "and inside it the statement is the model's")
check(sh.system_rewrite("exit") == "" and sh.nat_ctx == ["system"],
      "exit leaves one level")
ok("system > dataplane, read and written in one shape")

print("\n=== a policy naming a list that does not exist is refused (#484) ===")
# The compiler turned such a term into a clause with no match - every route -
# and policy.findings(), which says so, was never asked by the commit.
_bad = ["policy-options policy-statement EXPORT term t1 from prefix-list NAO-EXISTE",
        "policy-options policy-statement EXPORT term t1 then accept"]
_run0, _tgt = Config(), Config()
_tgt.policy = list(_bad)
sh = shell()
sh.running_config = lambda: _run0
rv, text = driven(lambda: sh.apply(_tgt, check_only=True))
check(rv is False
      and "error: policy-statement EXPORT term t1: no prefix-list NAO-EXISTE"
      in text,
      f"a term naming a missing prefix-list was not refused: {rv!r} {text!r}")
# The guard every such refusal here carries: a saved file that ALREADY holds
# the fault still loads at boot, and is told rather than refused.
_run1 = Config()
_run1.policy = list(_bad)
sh = shell()
sh.running_config = lambda: _run1
rv, text = driven(lambda: sh.apply(_tgt, check_only=True))
check("error: policy-statement EXPORT term t1" not in text
      and "no prefix-list NAO-EXISTE" in text,
      f"a fault the running configuration already carried was refused: {text!r}")
# And a name the VTY refuses does not become the level the next lines go in.
sh = shell()
sh.candidate = Config()
sh.mode = "config"
rv, text = driven(lambda: [sh.feed(l) for l in ("policy-options",
                                                 "prefix-list nome#ruim")])
check(sh.nat_ctx == ["policy-options"] and "not a name" in text,
      f"a refused name was entered as a level: {sh.nat_ctx} {text!r}")
ok("a missing list refused at commit, a carried one told, a bad name not entered")

print("\n=== routing policy in the VTY (#458) ===")
check(prismash.compiled_object("route-map PO-P-IN permit 10")
      and prismash.compiled_object(" ip prefix-list PO-X seq 5 permit 10.0.0.0/8")
      and not prismash.compiled_object("route-map MINE permit 10"),
      "the compiler's namespace, and nothing else")
_run = ["route-map PO-P-IN permit 10", " match ip address prefix-list PO-P-T1-4",
        "exit", "ip prefix-list PO-P-T1-4 seq 5 permit 10.0.0.0/8",
        "route-map MINE permit 10", " set local-preference 50", "exit",
        "router bgp 65000", " address-family ipv4 unicast",
        "  neighbor 192.0.2.1 route-map PO-P-IN in",
        "  neighbor 192.0.2.1 route-map PO-Q-OUT out", " exit-address-family",
        "exit"]
check(prismash.strip_compiled(_run) == _run[4:],
      f"what the compiler emitted comes out, the operator's route-map stays: "
      f"{prismash.strip_compiled(_run)}")
check(prismash.policy_uses(_run) == {("P", "import"), ("Q", "export")},
      f"the uses, read off the neighbors: {prismash.policy_uses(_run)}")
_c = Config()
_c.policy = ["policy-options policy-statement P term 1 then reject",
             "policy-options policy-statement Q term 1 then accept"]
_c.routing = prismash.strip_compiled(_run)
_fl = prismash.frr_lines(_c)
check(_fl[-len(_c.routing):] == _c.routing
      and "route-map PO-P-IN deny 10" in _fl
      and "route-map PO-Q-OUT permit 10" in _fl,
      f"FRR is handed the compiled uses, then the routing: {_fl[:6]}")
_pdir = tempfile.mkdtemp()
_saved_pp = prismash.POLICY_PATH
prismash.POLICY_PATH = os.path.join(_pdir, "policy-options")
try:
    sh = shell()
    check(sh.read_policy() == [], "no file is no policy")
    check(sh.write_policy(_c.policy) and sh.read_policy() == _c.policy,
          "written by rename, and read back")
    prismash.POLICY_PATH = os.path.join(_pdir, "no", "such", "dir")
    rv, text = driven(lambda: sh.write_policy(_c.policy))
    check("policy-options: its source was not written" in text,
          f"a file that cannot be written is said: {text!r}")
finally:
    prismash.POLICY_PATH = _saved_pp
sh = shell()
sh.candidate = Config()
sh.mode = "config"
for _l in ("policy-options", "prefix-list C", "203.0.113.0/24", "exit",
           "policy-statement P", "term 10", "from prefix-list C",
           "then accept", "then reject"):
    driven(lambda: sh.do_config(_l))
check(sh.nat_ctx == ["policy-options", "policy-statement P", "term 10"],
      f"the levels: {sh.nat_ctx}")
check("policy-options policy-statement P term 10 then reject"
      in sh.candidate.policy
      and "policy-options policy-statement P term 10 then accept"
      not in sh.candidate.policy,
      "a then replaces the term's then: the last word said holds")
check(sh.policy_rewrite("no then reject")
      == "no policy-options policy-statement P term 10 then reject",
      "no, inside a term, is the model's line with no")
rv, text = driven(lambda: sh.stage_policy("policy-options community X "
                                          "members 1"))
check("is not a community" in text, f"a bad statement is refused: {text!r}")
rv, text = driven(lambda: sh.stage_policy("no policy-options prefix-list Z"))
check("policy-options has no such statement" in text,
      f"removing what is not there is said: {text!r}")
driven(lambda: sh.stage_policy("no policy-options policy-statement P"))
check(not any("policy-statement P" in l for l in sh.candidate.policy),
      "no policy-statement takes it and every term under it")
driven(lambda: sh.do_config("end"))
rv, text = driven(lambda: sh.do_config("route-map PO-X permit 10"))
check("the PO- names belong to policy-options" in text,
      f"the compiler's namespace is refused at the prompt: {text!r}")
# A policy applied to a neighbor that nothing defines, refused at the prompt
# rather than at commit, and nothing staged.
sh.frr_ctx = ["router bgp 65000"]
_before = list(sh.candidate.routing)
rv, text = driven(lambda: sh.do_config("neighbor 10.0.0.2 import NOPE"))
check("no policy-statement named NOPE" in text
      and sh.candidate.routing == _before,
      f"an import naming no policy is refused, nothing staged: {text!r}")
# `no neighbor X` (#562): the neighbour's own statements, removed one by one
# through typedcfg.bgp_neighbor_delete_lines - families first, remote-as
# last - and never the bare line, which FRR "did not keep" and frr-reload
# refused with a family; a peer-group a member still names is refused; and a
# neighbour named in two routers is removed from the one the operator is in.
sh.candidate.routing += [
    "router bgp 65000", "neighbor 10.0.0.9 remote-as 65009",
    "address-family ipv4 unicast",
    "neighbor 10.0.0.9 soft-reconfiguration inbound", "exit-address-family",
    "neighbor PG2 peer-group", "neighbor 10.0.0.8 peer-group PG2", "exit",
    "router bgp 65000 vrf red", "neighbor 10.0.0.9 remote-as 65009", "exit"]
import typedcfg as _tc  # noqa: E402


def _names(nb, header):
    return [st for path, st in _tc.routing_statements(sh.candidate)
            if path[:1] == [header] and st.split()[:2] == ["neighbor", nb]]


sh.frr_ctx = ["router bgp 65000"]
rv, text = driven(lambda: sh.do_config("no neighbor 10.0.0.9"))
check(not _names("10.0.0.9", "router bgp 65000")
      and _names("10.0.0.9", "router bgp 65000 vrf red")
      and "no neighbor 10.0.0.9" not in [l.strip() for l in sh.candidate.routing]
      and sh.frr_ctx == ["router bgp 65000"],
      "remove_bgp_neighbor: every statement of it gone from this router - the "
      "family's too - the VRF's left, no bare line, back at the router level: "
      f"{text!r} {sh.frr_ctx}")
_before = list(sh.candidate.routing)
rv, text = driven(lambda: sh.do_config("no neighbor PG2"))
check("this peer-group still has members" in text and "10.0.0.8" in text
      and sh.candidate.routing == _before,
      f"remove_bgp_neighbor: a group with a member is refused, named: {text!r}")
rv, text = driven(lambda: sh.remove_bgp_neighbor("no neighbor 10.0.0.77"))
check(rv is True and "no such neighbor in this router" in text
      and "10.0.0.77" in text and sh.candidate.routing == _before,
      f"remove_bgp_neighbor: one that is not there, nothing staged: {text!r}")
check(sh.remove_bgp_neighbor("no neighbor 10.0.0.9 remote-as 65009") is None
      and sh.remove_bgp_neighbor("neighbor 10.0.0.9 remote-as 65009") is None,
      "remove_bgp_neighbor: only the bare `no neighbor X` is this")
sh.frr_ctx = ["router bgp 65000 vrf red"]
rv, text = driven(lambda: sh.do_config("no neighbor 10.0.0.9"))
check(not _names("10.0.0.9", "router bgp 65000 vrf red"),
      f"and from the VRF's router, standing in it: {text!r}")
# The same statement in both routers, removed standing in the VRF's: the
# VRF's goes and the default router's stays. Before #562 the first match
# anywhere went - the default router's, the one nobody named.
sh.candidate.routing += ["router bgp 65000", "neighbor 10.0.0.6 remote-as 65006",
                         "exit", "router bgp 65000 vrf red",
                         "neighbor 10.0.0.6 remote-as 65006", "exit"]
sh.frr_ctx = ["router bgp 65000 vrf red"]
rv, text = driven(lambda: sh.do_config("no neighbor 10.0.0.6 remote-as 65006"))
check(not _names("10.0.0.6", "router bgp 65000 vrf red")
      and _names("10.0.0.6", "router bgp 65000"),
      "a `no` takes the statement in the block it is typed in, not the first "
      f"anywhere: {text!r}")
sh.frr_ctx = []
# `export <policy>` inside an IGP (#458 step 2): the redistribute lines it
# stands for, staged together; refused for a policy that is not there and for
# one whose terms say what only a BGP route has.
sh.candidate.policy += [
    "policy-options policy-statement EXT term 1 from protocol static",
    "policy-options policy-statement EXT term 1 then accept",
    "policy-options community K members 1:1",
    "policy-options policy-statement BGPONLY term 1 from community K"]
sh.frr_ctx = ["router ospf"]
_before = list(sh.candidate.routing)
rv, text = driven(lambda: sh.do_config("export EXT"))
_added = sh.candidate.routing[len(_before):]
check(_added[:1] == ["redistribute bgp route-map PO-EXT-OSPF-BGP"]
      and "redistribute static route-map PO-EXT-OSPF-STATIC" in _added
      and len(_added) == len(prismash.policy_mod.IGPS["ospf"]),
      f"export stages a redistribute per protocol: {_added} {text!r}")
check(("EXT", "ospf:static") in prismash.policy_uses(sh.candidate.routing),
      "and the uses read it back, so the maps are compiled")
rv, text = driven(lambda: sh.do_config("no export EXT"))
check(sh.candidate.routing[-1] == "no redistribute isis route-map "
                                  "PO-EXT-OSPF-ISIS",
      f"no export takes every one of them back: {sh.candidate.routing[-3:]}")
_before = list(sh.candidate.routing)
for _bad, _why in (("export NOPE", "no policy-statement named NOPE"),
                   ("export BGPONLY", "cannot be exported into ospf"),
                   ("export", "usage: [no] export <policy>")):
    rv, text = driven(lambda: sh.do_config(_bad))
    check(_why in text and sh.candidate.routing == _before,
          f"{_bad!r} refused, nothing staged: {text!r}")
rv, text = driven(lambda: sh.igp_export("ospf", ["no", "export"]))
check("usage: [no] export <policy>" in text,
      f"Shell.igp_export, called on its own, with no policy: {text!r}")
sh.frr_ctx = ["router isis CORE"]
rv, text = driven(lambda: sh.do_config("export EXT"))
check("redistribute ipv6 static level-2 route-map PO-EXT-ISIS-STATIC"
      in sh.candidate.routing,
      f"inside IS-IS, both families and both levels: {text!r}")
sh.frr_ctx = []
ok("the namespace, the file, the levels, the staging")

print("\n=== routing policy, phase A in the VTY (#535) ===")
check(prismash.policy_names(["A"]) == ["A"]
      and prismash.policy_names(["[", "A", "B", "]"]) == ["A", "B"]
      and prismash.policy_names(["[A", "B]"]) == ["A", "B"]
      and prismash.policy_names(["A", "B"]) is None
      and prismash.policy_names(["[", "A", "A", "]"]) is None
      and prismash.policy_names(["[", "]"]) is None
      and prismash.policy_names(["[", "A"]) is None,
      "one name, or a chain in brackets; two bare names, a repeat, an empty "
      "or an open bracket are not")
check(prismash.compiled_object(prismash.policy_mod.DISCARD_ROUTES[0])
      and prismash.compiled_object(prismash.policy_mod.DISCARD_ROUTES[1])
      and not prismash.compiled_object("ip route 192.0.2.1/32 blackhole")
      and prismash.compiled_object("bgp large-community-list standard PO-L-X "
                                   "seq 5 permit 1:2:3"),
      "the discard routes the compiler emits are its own; one typed with no "
      "tag is the operator's")
_run = ["router bgp 65000", " address-family ipv4 unicast",
        "  neighbor 10.0.0.2 route-map PO-A+B-IN in",
        "  neighbor 10.0.0.2 route-map PO-C-OUT out"]
check(prismash.policy_applications(_run)
      == {"A": ["neighbor 10.0.0.2 import [ A B ]"],
          "B": ["neighbor 10.0.0.2 import [ A B ]"],
          "C": ["neighbor 10.0.0.2 export"]},
      f"a chain is said under each of its policies: "
      f"{prismash.policy_applications(_run)}")
sh = shell()
sh.candidate = Config()
sh.mode = "config"
sh.candidate.policy = [
    "policy-options policy-statement A term 1 then next policy",
    "policy-options policy-statement B term 1 then accept",
    "policy-options policy-statement S term 1 set next-hop self",
    "policy-options policy-statement OM term 1 set metric-type 2"]
sh.frr_ctx = ["router bgp 65000"]
_before = list(sh.candidate.routing)
rv, text = driven(lambda: sh.do_config("neighbor 10.0.0.2 import [ A B ]"))
check(sh.candidate.routing[len(_before):]
      == ["neighbor 10.0.0.2 route-map PO-A+B-IN in"],
      f"a chain staged as the one map it compiles to: "
      f"{sh.candidate.routing[len(_before):]} {text!r}")
_before = list(sh.candidate.routing)
for _bad, _why in (
        ("neighbor 10.0.0.2 import [ A A ]",
         "usage: neighbor <peer> import|export <policy> | [ <policy>... ]"),
        ("neighbor 10.0.0.2 export A B",
         "usage: neighbor <peer> import|export <policy> | [ <policy>... ]"),
        ("neighbor 10.0.0.2 import [ A NOPE ]",
         "no policy-statement named NOPE"),
        ("neighbor 10.0.0.2 import S",
         "set next-hop self is for an export"),
        ("neighbor 10.0.0.2 export OM",
         "set metric-type is for an export into OSPF")):
    rv, text = driven(lambda: sh.do_config(_bad))
    check(_why in text and sh.candidate.routing == _before,
          f"{_bad!r} refused, nothing staged: {text!r}")
sh.frr_ctx = ["router ospf"]
rv, text = driven(lambda: sh.do_config("export [ OM B ]"))
check("redistribute static route-map PO-OM+B-OSPF-STATIC"
      in sh.candidate.routing, f"a chain exported into OSPF: {text!r}")
rv, text = driven(lambda: sh.do_config("export [ OM"))
check("usage: [no] export <policy> | [ <policy>... ]" in text,
      f"an open bracket is a usage: {text!r}")
sh.frr_ctx = []
driven(lambda: sh.stage_policy("policy-options policy-statement B "
                               "default-action reject"))
driven(lambda: sh.stage_policy("policy-options policy-statement B "
                               "default-action accept"))
driven(lambda: sh.stage_policy("policy-options large-community L members "
                               "1:2:3"))
driven(lambda: sh.stage_policy("policy-options large-community L members "
                               "4:5:6"))
driven(lambda: sh.stage_policy("policy-options extended-community E members "
                               "rt:1:2"))
driven(lambda: sh.stage_policy("policy-options extended-community E members "
                               "soo:3:4"))
check([l for l in sh.candidate.policy if "default-action" in l
       or "-community" in l]
      == ["policy-options policy-statement B default-action accept",
          "policy-options large-community L members 4:5:6",
          "policy-options extended-community E members soo:3:4"],
      f"a default-action, and a community set, said again replaces: "
      f"{sh.candidate.policy}")
ok("chains, the refusals at the neighbor, the new definitions staged")

# RTBH on a single-hop eBGP neighbor needs disable-connected-check, or FRR
# leaves the route uninstalled (measured: discard_findings' docstring).
_dp, _ = prismash.policy_mod.parse([
    "policy-options community BH members 65535:666",
    "policy-options policy-statement RT term 1 from community BH",
    "policy-options policy-statement RT term 1 set next-hop discard",
    "policy-options policy-statement OTHER term 1 then accept",
    "policy-options policy-statement CALLS term 1 from policy RT"])
_dr = ["router bgp 65000",
       " neighbor 10.0.0.2 remote-as 65001",
       " neighbor 10.0.0.3 remote-as 65001",
       " neighbor 10.0.0.3 disable-connected-check",
       " neighbor 10.0.0.4 remote-as 65000",
       " neighbor G peer-group",
       " neighbor G ebgp-multihop 2",
       " neighbor 10.0.0.5 remote-as 65009",
       " neighbor 10.0.0.5 peer-group G",
       " neighbor 10.0.0.6 remote-as 65001",
       " address-family ipv4 unicast",
       "  neighbor 10.0.0.2 route-map PO-OTHER+RT-IN in",
       "  neighbor 10.0.0.3 route-map PO-RT-IN in",
       "  neighbor 10.0.0.4 route-map PO-RT-IN in",
       "  neighbor 10.0.0.5 route-map PO-RT-IN in",
       "  neighbor 10.0.0.6 route-map PO-OTHER-IN in",
       "  neighbor 10.0.0.6 route-map PO-CALLS-OUT out",
       " exit-address-family"]
check(prismash.discard_findings(_dp, _dr) == [
    ("neighbor 10.0.0.2 route-map PO-OTHER+RT-IN in",
     "set next-hop discard is not resolved on a single-hop eBGP neighbor: "
     "add 'neighbor 10.0.0.2 disable-connected-check', or the route is "
     "never installed and its traffic is forwarded")],
      f"only the single-hop eBGP import without the statement: "
      f"{prismash.discard_findings(_dp, _dr)}")
_dr2 = _dr[:-2] + ["  neighbor 10.0.0.6 route-map PO-CALLS-IN in"]
check([s for s, _ in prismash.discard_findings(_dp, _dr2)][-1]
      == "neighbor 10.0.0.6 route-map PO-CALLS-IN in",
      "a discard reached through from policy counts")
_c = Config()
_c.policy = [l for l in [
    "policy-options community BH members 65535:666",
    "policy-options policy-statement RT term 1 from community BH",
    "policy-options policy-statement RT term 1 set next-hop discard"]]
# The neighbor from FRR's running text, the import as the VTY stages it:
# flat, after a `router bgp` of its own.
_c.routing = ["router bgp 65000", " neighbor 10.0.0.2 remote-as 65001",
              "exit", "router bgp 65000", "address-family ipv4 unicast",
              "neighbor 10.0.0.2 route-map PO-RT-IN in",
              "exit-address-family", "exit"]
sh = shell()
sh.running = Config()
sh.candidate = _c
rv, text = driven(lambda: sh.do_config("commit check"))
check("disable-connected-check" in text and "error" in text,
      f"refused at commit, with the statement that fixes it: {text!r}")
check(prismash.discard_findings(
    prismash.policy_mod.parse(_c.policy)[0],
    ["router bgp 65000", " neighbor 10.0.0.2 remote-as 65001",
     " neighbor 10.0.0.2 disable-connected-check", "exit"]
    + _c.routing[3:]) == [],
      "what FRR's text says of a neighbor holds for what is staged after")
_staged = ["router bgp 65000", "neighbor 10.0.0.2 remote-as 65001",
           "address-family ipv4 unicast",
           "neighbor 10.0.0.2 route-map PO-RT-IN in", "exit-address-family",
           "neighbor 10.0.0.2 disable-connected-check", "exit"]
_rtp = prismash.policy_mod.parse(_c.policy)[0]
check(prismash.discard_findings(_rtp, _staged) == []
      and len(prismash.discard_findings(
          _rtp, _staged[:-1] + ["no neighbor 10.0.0.2 disable-connected-"
                                "check", "exit"])) == 1,
      "said after the import in the same batch counts; taken back, it does "
      "not")

print("\n=== show policy and test policy (#458 step 3) ===")
_run = ["router bgp 65000", " address-family ipv4 unicast",
        "  neighbor 10.0.0.2 route-map PO-UP-IN in",
        "router ospf", " redistribute static route-map PO-UP-OSPF-STATIC",
        " redistribute bgp route-map PO-UP-OSPF-BGP"]
check(prismash.policy_applications(_run)
      == {"UP": ["neighbor 10.0.0.2 import", "ospf export"]},
      f"where a policy is applied, an IGP export said once: "
      f"{prismash.policy_applications(_run)}")
_UP = ["policy-options prefix-list C 203.0.113.0/24",
       "policy-options policy-statement UP term 10 from prefix-list C",
       "policy-options policy-statement UP term 10 then accept",
       "policy-options policy-statement UP term 20 from route-filter "
       "198.51.100.0/22 upto /24",
       "policy-options policy-statement UP term 20 then reject"]
sh = shell()
sh.frr = type("F", (prismash.Frr,), {"available": lambda s: False,
                                     "running": lambda s: _run,
                                     "show": lambda s, c: (1, "")})()
sh.read_policy = lambda: list(_UP)
check(sh.policy_in_view() == _UP,
      "in operational mode the policy in view is the box's own file")
rv, text = driven(lambda: sh.show_policy([]))
check("policy-statement UP\n  terms: 2" in text
      and "applied: neighbor 10.0.0.2 import, ospf export" in text,
      f"show policy: each one, and where: {text!r}")
rv, text = driven(lambda: sh.show_policy(["UP"]))
check("  term 20\n   from route-filter 198.51.100.0/22 upto /24" in text,
      f"show policy UP: its terms as they are typed: {text!r}")
for _a, _why in ((["NOPE"], "no policy-statement named NOPE"),
                 (["a", "b"], "usage: show policy [<policy>]")):
    rv, text = driven(lambda: sh.show_policy(_a))
    check(_why in text, f"show policy {_a}: {text!r}")
rv, text = driven(lambda: sh.test_policy(["UP", "198.51.100.0/23"]))
check("term 20: reject" in text and "result: reject, by term 20" in text,
      f"test policy: the term that decides: {text!r}")
rv, text = driven(lambda: sh.test_policy(["UP", "10.0.0.0/8"]))
check("result: no term decided it" in text
      and "exported into OSPF or IS-IS it is not" in text
      and "default" not in text,
      f"no term decided: both defaults said, no default row: {text!r}")
# #535: a default-action decides, and a term that passes to the next policy
# says so rather than that nothing decided.
sh.read_policy = lambda: _UP + [
    "policy-options policy-statement UP default-action reject",
    "policy-options policy-statement NP term 1 then next policy"]
rv, text = driven(lambda: sh.test_policy(["UP", "10.0.0.0/8"]))
check("result: reject, by the policy's default-action" in text,
      f"the default-action decides what no term did: {text!r}")
rv, text = driven(lambda: sh.test_policy(["NP", "10.0.0.0/8"]))
check("result: next policy, by term 1" in text
      and "in a chain the policies after this one decide" in text,
      f"then next policy: {text!r}")
sh.read_policy = lambda: list(_UP)
for _a, _why in ((["UP"], "usage: test policy <policy> <prefix>"),
                 (["NOPE", "1.0.0.0/8"], "no policy-statement named NOPE"),
                 (["UP", "banana"], "not a prefix: banana")):
    rv, text = driven(lambda: sh.test_policy(_a))
    check(_why in text, f"test policy {_a}: {text!r}")
sh.candidate = Config()
sh.candidate.policy = ["policy-options policy-statement DRAFT term 1 then "
                       "reject"]
sh.mode = "config"
rv, text = driven(lambda: sh.show_policy([]))
check("policy-statement DRAFT" in text and "UP" not in text,
      f"in configuration mode it is the candidate's: {text!r}")
sh.candidate.policy = []
rv, text = driven(lambda: sh.show_policy([]))
check("no policy-statement is configured" in text, f"none: {text!r}")
ok("where, what, and a prefix walked through it")

print("\n=== routing policy, phase D: counters, test, which decided (#538) ===")
_D_RUN = """router bgp 65000
 neighbor 10.0.0.2 remote-as 65001
 neighbor 10.2.0.2 remote-as 65002
 address-family ipv4 unicast
  network 198.51.100.0/24 route-map PO-NET-BGP-NETWORK
  neighbor 10.0.0.2 route-map PO-UP-IN in
  neighbor 10.2.0.2 route-map PO-DN-OUT out
  table-map PO-TBL-BGP-TABLE
 exit-address-family
exit
router bgp 65000 vrf red
 address-family ipv4 unicast
  import vrf svc
  import vrf route-map PO-LEAK-BGP-VRFIMPORT
 exit-address-family
exit
router ospf
 redistribute bgp route-map PO-UP-OSPF-BGP
exit
ip protocol bgp route-map PO-FIB-FIB-BGP
"""
_D_POL = [f"policy-options {l}" for l in (
    "prefix-list C 203.0.113.0/24",
    "community T members 65001:10",
    "policy-statement UP term cust from prefix-list C",
    "policy-statement UP term cust set local-preference 200",
    "policy-statement UP term cust then accept",
    "policy-statement UP term bogon from route-filter 10.0.0.0/8 orlonger",
    "policy-statement UP term bogon then reject",
    "policy-statement DN term t from community T",
    "policy-statement DN term t then reject",
    "policy-statement NET term 1 set community add T",
    "policy-statement NET term 1 then accept",
    "policy-statement TBL term 1 then accept",
    "policy-statement FIB term 1 then accept",
    "policy-statement LEAK term 1 from source-vrf svc",
    "policy-statement LEAK term 1 then accept",
    "policy-statement GATE term 1 then accept",
    "policy-statement CALLER term 1 from policy GATE",
    "policy-statement CALLER term 1 then accept",
    "policy-statement REJ term 1 from policy GATE",
    "policy-statement REJ term 1 then reject")]
_maps = {"bgpd": {"PO-UP-IN": {"rules": [
    {"sequenceNumber": 10, "invoked": 5}, {"sequenceNumber": 20,
                                           "invoked": 2},
    {"sequenceNumber": 65535, "invoked": 3}]}}}
_path = {"aspath": {"string": "65001"}, "origin": "IGP", "metric": 0,
         "locPrf": 200, "community": {"list": ["65001:10"]},
         "bestpath": {"overall": True}, "nexthops": [{"ip": "10.0.0.2"}],
         "peer": {"peerId": "10.0.0.2"}}
_D_SHOWS = {
    "show running-config": (0, _D_RUN),
    "show route-map json": (0, json.dumps(_maps)),
    "show bgp ipv4 unicast 203.0.113.0/24 json": (0, json.dumps(
        {"paths": [_path]})),
    "show bgp ipv4 unicast 198.51.100.0/24 json": (0, json.dumps(
        {"paths": [{"aspath": {"string": "Local"}, "origin": "IGP",
                    "weight": 32768, "community": {"list": ["65001:10"]},
                    "bestpath": {"overall": True},
                    "peer": {"peerId": "0.0.0.0"}}]})),
    "show bgp ipv4 unicast 10.1.0.0/16 json": (0, "{}"),
    "show bgp vrf svc ipv4 unicast 203.0.113.0/24 json": (0, json.dumps(
        {"paths": [{"aspath": {"string": "65030"}, "origin": "IGP",
                    "bestpath": {"overall": True},
                    "peer": {"peerId": "10.30.0.2"}}]})),
    "show bgp vrf red ipv4 unicast 203.0.113.0/24 json": (0, json.dumps(
        {"paths": [{"aspath": {"string": "65030"}, "nhVrfName": "svc",
                    "locPrf": 200, "bestpath": {"overall": True},
                    "peer": {"peerId": "0.0.0.0"}}]})),
    "show bgp ipv4 unicast neighbors 10.0.0.2 received-routes json": (
        0, json.dumps({"receivedRoutes": {
            "203.0.113.0/24": {"path": "65001", "metric": 0,
                               "origin": "IGP", "nextHop": "10.0.0.2"},
            "10.1.0.0/16": {"path": "65001 64999", "metric": 7,
                            "origin": "IGP", "nextHop": "10.0.0.2"}}})),
    "show bgp ipv4 unicast neighbors 10.2.0.2 advertised-routes json": (
        0, json.dumps({"advertisedRoutes": {}})),
    "show ip route 203.0.113.0/24 json": (0, json.dumps(
        {"203.0.113.0/24": [{"protocol": "bgp", "selected": True,
                             "installed": True, "metric": 0,
                             "nexthops": [{"ip": "10.0.0.2"}]}]})),
    "show ip route 10.1.0.0/16 json": (0, "{}"),
    "show ip ospf database external 203.0.113.0 self-originate json": (
        0, json.dumps({"asExternalLinkStates": [
            {"linkStateId": "203.0.113.0", "networkMask": 24}]})),
}
check(prismash._attach_matches(" table-map PO-TBL-BGP-TABLE")
      == [("TBL", "bgp:table", "table-policy", None)]
      and prismash._attach_matches(" network 198.51.100.0/24 route-map "
                                   "PO-NET-BGP-NETWORK")
      == [("NET", "bgp:network", "network 198.51.100.0/24",
           "198.51.100.0/24")],
      "_attach_matches: attach_reads() with what each is applied to")
_sites = prismash.use_sites(_D_RUN.splitlines())
check(_sites[("UP", "import")] == [("neighbor 10.0.0.2 import", "default",
                                    "10.0.0.2")]
      and _sites[("LEAK", "bgp:vrfimport")] == [("vrf-import (vrf red)",
                                                 "red", None)]
      and _sites[("UP", "ospf:bgp")] == [("ospf export, from bgp",
                                          "default", None)]
      and _sites[("FIB", "fib:bgp")] == [("fib-policy bgp", "default",
                                          None)],
      f"use_sites: every place, its VRF and what it is on: {_sites}")
_dpol = prismash.policy_mod.parse(_D_POL)[0]
_secs = prismash.policy_hit_sections(_dpol, "UP", _sites, _maps)
check([(x["use"], x["daemon"], x["rows"]) for x in _secs]
      == [("import", "bgpd", [("UP", "cust", 5), ("UP", "bogon", 2),
                              (None, None, 3)]),
          ("ospf:bgp", "ospfd", None)],
      f"policy_hit_sections: per use, a daemon with no such map said: "
      f"{_secs}")
_sub = prismash.policy_hit_sections(
    _dpol, "GATE", {("CALLER", "import"): [("neighbor x import",
                                            "default", "x")]}, {})
check(_sub and _sub[0]["called"] and _sub[0]["map"] == "PO-GATE-SUB-IN"
      and _sub[0]["where"] == ["neighbor x import, by CALLER"],
      f"policy_hit_sections: a called policy's own map: {_sub}")
_inv = prismash.policy_hit_sections(
    _dpol, "GATE", {("CALLER", "import"): [("neighbor x import",
                                            "default", "x")],
                    ("REJ", "import"): [("neighbor y import", "default",
                                         "y")]}, {})
check([(x["map"], x["inverted"], x["where"]) for x in _inv]
      == [("PO-GATE-SUB-IN", False, ["neighbor x import, by CALLER"]),
          ("PO-GATE-NOT-IN", True, ["neighbor y import, by REJ"])],
      f"policy_hit_sections: a reject term's call is the inverted map, a "
      f"section of its own (#542): {_inv}")
check(prismash.hit_label(_secs[0], None, None)
      == "no term decided: the use's default, accept"
      and prismash.hit_label({"chain": "A+B", "use": "import"}, "A", None)
      == "A default-action",
      "hit_label: the default rows, a chain's policy named")
check(prismash.bgp_path_route("203.0.113.0/24", dict(
    _path, extendedCommunity={"string": "RT:65001:5 SoO:1:2"},
    rpkiValidationState="not found")) == {
        "prefix": "203.0.113.0/24", "as-path": ["65001"],
        "community": ["65001:10"], "large-community": [],
        "extended-community": ["rt:65001:5", "soo:1:2"], "weight": "0",
        "tag": "0", "origin": "igp", "local-preference": "200",
        "metric": "0", "next-hop": "10.0.0.2", "neighbor": "10.0.0.2",
        "validation-state": "unknown"},
      "bgp_path_route: FRR's path as the simulation reads it")
check(prismash.bgp_path_route("x", {})["validation-state"].startswith(
    "none") and "as-path" in prismash.bgp_path_route("x", {}),
      "bgp_path_route: no RPKI state is said as none, which FRR matches")
check(prismash.zebra_route("10.0.0.0/8", {"metric": 20, "tag": 7,
                                          "nexthops": [{"ip": "1.1.1.1"}]})
      == {"prefix": "10.0.0.0/8", "metric": "20", "tag": "7",
          "next-hop": "1.1.1.1"},
      "zebra_route: the metric, tag and next hop an IGP export sees")
sh = shell(frr=RecFrr(_D_SHOWS))
sh.read_policy = lambda: list(_D_POL)
check(sh.route_map_counts() == _maps
      and shell(frr=RecFrr({})).route_map_counts() is None,
      "route_map_counts: FRR's counters, or None when it did not answer")
rv, text = driven(lambda: sh.show_policy(["UP"]))
check("hits, the routes each term matched" in text
      and "    neighbor 10.0.0.2 import\n      bgpd, route-map PO-UP-IN\n"
          "              5  term cust\n              2  term bogon\n"
          "              3  no term decided: the use's default, accept" in text
      and "no counter: ospfd reports no such route-map" in text,
      f"show policy UP: hits per term per use (print_policy_hits): {text}")
rv, text = driven(lambda: sh.show_policy([]))
check("hits" not in text, "show policy: the list carries no counters")
rv, text = driven(lambda: sh.test_policy(
    ["UP", "route", "203.0.113.0/24", "neighbor", "10.0.0.2"]))
check("at import: UP" in text and "bgpd, route-map PO-UP-IN" in text
      and "  term cust: match\n      then accept" in text
      and "result: accept, by term cust" in text
      and "    local-preference 200" in text,
      f"test policy route (test_policy_route, sim_by): {text}")
rv, text = driven(lambda: sh.test_policy(
    ["DN", "route", "203.0.113.0/24", "at", "export"]))
check("result: accept, by no term: the default of the use" in text,
      f"test policy route, the use's default: {text}")
rv, text = driven(lambda: sh.test_policy(
    ["DN", "route", "203.0.113.0/24", "community", "65001:10", "at",
     "table"]))
check("result: reject, by term t" in text and "after the sets" not in text,
      f"test policy route, a reject: {text}")
rv, text = driven(lambda: sh.test_policy(
    ["DN", "route", "203.0.113.0/24", "community", "65001:10,1:1",
     "at", "ospf", "bgp"]))
check("refused at this use: DN term t: from community is for BGP" in text,
      f"test policy: what the use refuses is said: {text}")
rv, text = driven(lambda: sh.test_policy(["UP", "rib", "203.0.113.0/24"]))
text = " ".join(text.split())
check("the path from 10.0.0.2 in the BGP table" in text
      and "UP, applied there, may set local-preference" in text
      and "local-preference not known" in text
      and "result: accept, by term cust" in text,
      f"test policy rib at import: the path before its import's sets "
      f"(rib_words, rib_route, unset_applied): {text}")
rv, text = driven(lambda: sh.test_policy(
    ["UP", "rib", "10.1.0.0/16", "neighbor", "10.0.0.2"]))
text = " ".join(text.split())
check("its import rejected it" in text and "as-path 65001 64999" in text
      and "result: reject, by term bogon" in text,
      f"test policy rib: a rejected route from received-routes "
      f"(from_received): {text}")
rv, text = driven(lambda: sh.test_policy(
    ["DN", "rib", "203.0.113.0/24", "neighbor", "10.2.0.2", "at",
     "export"]))
check("going to 10.2.0.2" in text and "result: reject, by term t" in text,
      f"test policy rib at export: the best path, going there: {text}")
rv, text = driven(lambda: sh.test_policy(
    ["DN", "rib", "203.0.113.0/24", "at", "export"]))
check("from neighbor is not known" in text, f"export, no neighbor: {text}")
rv, text = driven(lambda: sh.test_policy(
    ["FIB", "rib", "203.0.113.0/24", "at", "fib", "bgp"]))
check("zebra's bgp route" in text and "result: accept, by term 1" in text,
      f"test policy rib at a FIB policy: zebra's route: {text}")
rv, text = driven(lambda: sh.test_policy(
    ["LEAK", "rib", "203.0.113.0/24", "vrf", "svc", "at", "vrf-import"]))
check("VRF svc's best path" in text and "source-vrf svc" in text
      and "result: accept, by term 1" in text,
      f"test policy rib at vrf-import: the source VRF's path: {text}")
rv, text = driven(lambda: sh.test_policy(
    ["NET", "rib", "198.51.100.0/24", "at", "network"]))
check("community less what it adds" in " ".join(text.split())
      and "community (none)" in text,
      f"network: what its policy adds is taken off: {text}")
for _a, _why in (
        (["NOPE", "route", "1.0.0.0/8"], "no policy-statement named NOPE"),
        (["UP", "route", "1.0.0.0/8", "colour", "red"],
         "not an attribute of a route"),
        (["UP", "route", "1.0.0.0/8", "colour", "red"],
         "the attributes are\n    neighbor, community"),
        (["UP", "rib", "1.0.0.0/8", "at", "nowhere"], "at: nowhere is not"),
        (["UP", "rib", "1.0.0.0/8", "at", "nowhere"], "the uses: import"),
        (["UP", "rib", "1.0.0.0/8", "colour"], "rib <prefix> [vrf <name>]"),
        (["UP", "rib", "1.0.0.0/8", "neighbor", "x"], "is not an address"),
        (["UP", "rib", "banana"], "not a prefix: banana"),
        (["UP", "rib", "10.9.0.0/16", "neighbor", "10.0.0.2"],
         "and no copy of what 10.0.0.2 sent"),
        (["UP", "rib", "10.9.0.0/16"], "which neighbor's path"),
        (["UP", "rib", "10.9.0.0/16", "at", "table"], "holds no path"),
        (["LEAK", "rib", "10.9.0.0/16", "at", "vrf-import"],
         "holds no path"),
        (["FIB", "rib", "10.9.0.0/16", "at", "fib", "static"],
         "zebra holds no static route"),
        (["UP", "a", "b"], "usage: test policy <policy> <prefix>")):
    rv, text = driven(lambda: sh.test_policy(_a))
    check(_why in text, f"test policy {_a}: {text!r}")
rv, text = driven(lambda: sh.show_policy(["route", "203.0.113.0/24"]))
check("route 203.0.113.0/24" in text
      and "  neighbor 10.0.0.2 import: UP\n    simulated: accept, by term "
          "cust" in text
      and "FRR: the path from 10.0.0.2 is in the BGP table: accepted" in text
      and "neighbor 10.2.0.2 export: DN" in text
      and "FRR: not advertised to 10.2.0.2" in text
      and "table-policy: TBL" in text and "zebra holds it from BGP" in text
      and "fib-policy bgp: FIB" in text and "zebra installed it" in text
      and "ospf export, from bgp: UP" in text
      and "ospfd originates it, as external" in text
      and "they disagree" not in text,
      f"show policy route: each use, the term, FRR's outcome "
      f"(show_policy_route, policy_outcome): {text}")
rv, text = driven(lambda: sh.show_policy(["route", "10.1.0.0/16"]))
check("simulated: reject, by term bogon" in text
      and "10.0.0.2 sent it and the BGP table holds no path" in text
      and text.index("import: UP") < text.index("export: DN")
      if "export: DN" in text else True
      and "they disagree" not in text,
      f"show policy route: a rejected route, from received-routes: {text}")
_loop = dict(_D_SHOWS)
_loop["show bgp ipv4 unicast neighbors 10.0.0.2 received-routes json"] = (
    0, json.dumps({"localAS": 65000, "receivedRoutes": {
        "10.1.0.0/16": {"path": "65001 65000", "origin": "IGP"}}}))
sh.frr = RecFrr(_loop)
rv, text = driven(lambda: sh.show_policy(["route", "10.1.0.0/16"]))
text = " ".join(text.split())
check("BGP dropped it before the policy: its AS path carries this box's "
      "AS, 65000" in text and "disagree" not in text,
      f"show policy route: a loop is BGP's, not the policy's: {text}")
sh.frr = RecFrr(_D_SHOWS)
rv, text = driven(lambda: sh.show_policy(["route", "203.0.113.0/24"]))
check(text.index("neighbor 10.0.0.2 import") < text.index("table-policy")
      < text.index("fib-policy") < text.index("ospf export")
      < text.index("neighbor 10.2.0.2 export"),
      f"show policy route: in the order a route passes them: {text}")
rv, text = driven(lambda: sh.show_policy(["route", "203.0.113.0/24", "vrf",
                                          "red"]))
check("vrf-import (vrf red): LEAK" in text
      and "leaked from svc" in text and "accept, by term 1" in text,
      f"show policy route vrf: the leak: {text}")
rv, text = driven(lambda: sh.show_policy(["route", "198.51.100.0/24"]))
check("network 198.51.100.0/24: NET" in text
      and "BGP originates it" in text,
      f"show policy route: a network: {text}")
_lie = dict(_D_SHOWS)
_lie["show bgp ipv4 unicast neighbors 10.2.0.2 advertised-routes json"] = (
    0, json.dumps({"advertisedRoutes": {"203.0.113.0/24": {}}}))
sh.frr = RecFrr(_lie)
rv, text = driven(lambda: sh.show_policy(["route", "203.0.113.0/24"]))
check("the simulation says reject and FRR accept: they disagree here"
      in text, f"show policy route: a disagreement is said: {text}")
sh.frr = RecFrr({"show running-config": (0, _D_RUN)})
rv, text = driven(lambda: sh.show_policy(["route", "192.0.2.0/24"]))
text = " ".join(text.split())
check("neither BGP nor zebra holds it" in text,
      f"show policy route: nothing there: {text}")
for _a, _why in ((["route", "banana"], "not a prefix: banana"),
                 (["route", "1.0.0.0/8", "x", "y"],
                  "usage: show policy route <prefix> [vrf <name>]")):
    rv, text = driven(lambda: sh.show_policy(_a))
    check(_why in text, f"show policy {_a}: {text!r}")
# The helpers on their own, named as the methods they are.
sh.frr = RecFrr(_D_SHOWS)
rv, text = driven(lambda: sh.print_policy_hits("UP"))
check("5  term cust" in text, f"print_policy_hits: {text}")
rv, text = driven(lambda: sh.test_policy_route(["UP", "route",
                                                "10.1.0.0/16"]))
check("result: reject, by term bogon" in text, f"test_policy_route: {text}")
rv, text = driven(lambda: sh.show_policy_route(["10.1.0.0/16"]))
check("rejected" in text, f"show_policy_route: {text}")
check(sh.policy_outcome("10.1.0.0/16", "default", "import", "10.0.0.2", [],
                        [])[0] == "reject"
      and sh.policy_outcome("10.9.0.0/16", "default", "import", "10.0.0.2",
                            [], []) is None
      and sh.policy_outcome("10.1.0.0/16", "default", "export", "x", [], [])
      is None
      and sh.policy_outcome("10.1.0.0/16", "default", "bgp:advertise", "x",
                            [], []) is None,
      "policy_outcome: rejected by what FRR shows; None where the route did "
      "not pass, or the use is not per route")
check(prismash.Shell.sim_by("A", {"by": ("A", None)}) == "A's default-action"
      and prismash.Shell.sim_by("A", {"by": ("B", "t")}) == "B term t"
      and prismash.Shell.sim_by("A", {"by": (None, None)})
      == "no term: the default of the use",
      "sim_by: what decided, said")
rv, text = driven(lambda: prismash.Shell.print_sim_notes(
    {"assumed": ["x did not match"], "said": ["y"]}))
check("assumed: x did not match" in text and "simulated with what is known"
      in text, f"print_sim_notes: {text}")
_rw = sh.rib_words("203.0.113.9/24", ["neighbor", "10.0.0.2", "at",
                                      "import"])
check(_rw[1] == "import" and _rw[2] is None
      and _rw[0]["prefix"] == "203.0.113.0/24",
      f"rib_words: the words, then the route: {_rw}")
_rr = sh.rib_route("203.0.113.0/24", "default", "10.2.0.2", "export")
check(_rr[0]["neighbor"] == "10.2.0.2" and _rr[1] is None,
      f"rib_route: at an export, the neighbor it goes to: {_rr}")
_route, _notes = {"prefix": "x", "local-preference": "200"}, []
sh.unset_applied(_route, _notes, "import", "default", "10.0.0.2", None)
check("local-preference" not in _route and "UP, applied there" in _notes[0],
      f"unset_applied: what the applied import may set is not known: "
      f"{_notes}")
_route = {"prefix": "x"}
check(prismash.Shell.from_received(_route, {"path": "65001 65002",
                                            "metric": 4}, ["as-path",
                                                           "metric",
                                                           "origin"])
      == ["as-path", "metric"] and _route["as-path"] == ["65001", "65002"],
      f"from_received: what received-routes has: {_route}")
check(prismash.wire_route({"prefix": "x", "as-path": ["1", "2"],
                           "community": [], "tag": 5})
      == {"prefix": "x", "as-path": "1 2", "community": "", "tag": "5"},
      "wire_route: a route as the API carries it")
# The API's answers (#538): the same computation, as data.
_pd = sh.policies_data()
_up = next(x for x in _pd if x["name"] == "UP")
check(_up["terms"][0] == {"name": "cust", "then": "accept",
                          "lines": ["from prefix-list C",
                                    "set local-preference 200",
                                    "then accept"]}
      and _up["applied"] == ["neighbor 10.0.0.2 import", "ospf export"]
      and _up["text"].startswith("policy-options\n policy-statement UP\n")
      and _up["default_action"] == "",
      f"policies_data: terms, their lines, the block, where: {_up}")
_cd = sh.policy_counters_data("UP")
check(_cd["uses"][0]["route_map"] == "PO-UP-IN"
      and _cd["uses"][0]["terms"][0] == {"policy": "UP", "term": "cust",
                                         "hits": 5, "compiled": True}
      and _cd["uses"][0]["terms"][-1]["term"] == ""
      and not _cd["uses"][1]["counted"]
      and sh.policy_counters_data("NOPE") == {
          "error": "no policy-statement named NOPE"}
      and len({u["route_map"] for u in sh.policy_counters_data()["uses"]})
      == len(sh.policy_counters_data()["uses"]),
      f"policy_counters_data: per use, per term, each map once: {_cd}")
_td = sh.test_policy_data({"policy": "UP", "prefix": "203.0.113.0/24",
                           "attributes": {"neighbor": "10.0.0.2",
                                          "community": "65001:10 65001:20"},
                           "use": "import"})
check(_td["verdict"] == "accept" and _td["decided_term"] == "cust"
      and _td["decided_by"] == "term cust"
      and _td["route"]["community"] == "65001:10 65001:20"
      and _td["route_after"]["local-preference"] == "200"
      and _td["route_map"] == "PO-UP-IN",
      f"test_policy_data: a described route: {_td}")
_td = sh.test_policy_data({"policy": "FIB", "prefix": "203.0.113.0/24",
                           "rib": True, "use": "fib bgp"})
check(_td["verdict"] == "accept" and "zebra's bgp route" in _td["notes"][0],
      f"test_policy_data: a route of the RIB: {_td}")
check(sh.test_policy_data({"policy": "UP", "prefix": "x"})["error"]
      == "not a prefix: x"
      and sh.policy_simulation("UP", "route", "1.0.0.0/8", ["at", "x"])[
          "more"] == [prismash.policy_mod.AT_USAGE],
      "test_policy_data, policy_simulation: a refusal, and what follows it")
ok("hits per term, a route simulated, which policy decided")

print("\n=== routing policy, phase B: the attach points (#536) ===")
check(prismash._take_names(["A", "x"], 0) == (["A"], 1)
      and prismash._take_names(["[", "A", "B", "]", "x"], 0) == (["A", "B"], 4)
      and prismash._take_names(["[A", "B]"], 0) == (["A", "B"], 2)
      and prismash._take_names(["x"], 1) == (None, 1),
      "_take_names: one name, or a chain in brackets, and where it ends")
check(prismash._attach_map(["A", "B"], "bgp:table") == "PO-A+B-BGP-TABLE",
      "_attach_map: the chain's map for the use")
for _w, _where, _want in (
        ("neighbor 10.0.0.2 default-originate policy C", "bgp",
         (["neighbor 10.0.0.2 default-originate route-map PO-C-BGP-DEFAULT"],
          [("C", "bgp:default")])),
        ("network 10.6.0.0/16 policy N", "bgp",
         (["network 10.6.0.0/16 route-map PO-N-BGP-NETWORK"],
          [("N", "bgp:network")])),
        ("aggregate-address 10.8.0.0/16 summary-only policy A", "bgp",
         (["aggregate-address 10.8.0.0/16 summary-only route-map "
           "PO-A-BGP-AGGREGATE"], [("A", "bgp:aggregate")])),
        ("aggregate-address 10.8.0.0/16 suppress-policy S policy A", "bgp",
         (["aggregate-address 10.8.0.0/16 route-map PO-A-BGP-AGGREGATE "
           "suppress-map PO-S-BGP-SUPPRESS"],
          [("A", "bgp:aggregate"), ("S", "bgp:suppress")])),
        ("neighbor 10.0.0.2 advertise [ X Y ] non-exist C", "bgp",
         (["neighbor 10.0.0.2 advertise-map PO-X+Y-BGP-ADVERTISE "
           "non-exist-map PO-C-BGP-CONDITION"],
          [("X+Y", "bgp:advertise"), ("C", "bgp:condition")])),
        ("table-policy T", "bgp",
         (["table-map PO-T-BGP-TABLE"], [("T", "bgp:table")])),
        ("default-information originate always metric 20 metric-type 1 "
         "policy D", "ospf",
         (["default-information originate always metric 20 metric-type 1 "
           "route-map PO-D-OSPF-DEFAULT"], [("D", "ospf:default")])),
        ("default-information originate ipv4 level-2 always metric 30 "
         "policy D", "isis",
         (["default-information originate ipv4 level-2 always route-map "
           "PO-D-ISIS-DEFAULT metric 30"], [("D", "isis:default")])),
        ("no fib-policy ospf F", "top",
         (["no ip protocol ospf route-map PO-F-FIB-OSPF",
           "no ipv6 protocol ospf6 route-map PO-F-FIB-OSPF"],
          [("F", "fib:ospf")])),
        ("network 10.6.0.0/16", "bgp", None),
        ("fib-policy static F", "bgp", None),
        ("table-policy T", "top", None),
        ("fib-policy connected F", "top",
         prismash.ATTACH_USAGE["fib-policy"]),
        ("neighbor 10.0.0.2 advertise X maybe C", "bgp",
         prismash.ATTACH_USAGE["advertise"]),
        ("network 10.6.0.0/16 policy A B", "bgp",
         prismash.ATTACH_USAGE["network"]),
        ("default-information originate ipv4 policy D", "isis",
         prismash.ATTACH_USAGE["isis"]),
        ("default-information originate metric-type 3 policy D", "ospf",
         prismash.ATTACH_USAGE["ospf"]),
        ("aggregate-address 10.8.0.0/16 as-set policy A", "bgp",
         prismash.ATTACH_USAGE["aggregate-address"])):
    _got = prismash.attach_statement(_w.split(), _where)
    check(_got == _want, f"{_w!r} in {_where}: {_got}")
check("one or the other" in prismash._attach_aggregate(
          "aggregate-address 10.8.0.0/16 summary-only suppress-policy S"
          .split()),
      "summary-only beside a suppress-policy is refused, as FRR refuses it")
check(prismash._attach("table-policy T".split(), "bgp")[0]
      == ["table-map PO-T-BGP-TABLE"]
      and prismash._attach_igp_default(
          "default-information originate policy D".split(), "ospf")[0]
      == ["default-information originate route-map PO-D-OSPF-DEFAULT"],
      "_attach and _attach_igp_default, called on their own")
_run = ["router bgp 65000", " address-family ipv4 unicast",
        "  network 10.6.0.0/16 route-map PO-N-BGP-NETWORK",
        "  aggregate-address 10.8.0.0/16 route-map PO-A-BGP-AGGREGATE "
        "suppress-map PO-S+T-BGP-SUPPRESS",
        "  neighbor 10.0.0.2 default-originate route-map PO-C-BGP-DEFAULT",
        "  neighbor 10.0.0.2 advertise-map PO-X-BGP-ADVERTISE exist-map "
        "PO-C-BGP-CONDITION",
        "  table-map PO-T-BGP-TABLE", "router ospf",
        " default-information originate always route-map PO-D-OSPF-DEFAULT",
        "router isis CORE", " default-information originate ipv4 level-2 "
        "always route-map PO-D-ISIS-DEFAULT metric 30",
        "ip protocol static route-map PO-F-FIB-STATIC",
        "ipv6 protocol ospf6 route-map PO-F-FIB-OSPF"]
check(prismash.policy_uses(_run) == {
          ("N", "bgp:network"), ("A", "bgp:aggregate"),
          ("S+T", "bgp:suppress"), ("C", "bgp:default"),
          ("X", "bgp:advertise"), ("C", "bgp:condition"),
          ("T", "bgp:table"), ("D", "ospf:default"), ("D", "isis:default"),
          ("F", "fib:static"), ("F", "fib:ospf")},
      f"every attach point read back as its use: "
      f"{prismash.policy_uses(_run)}")
check(prismash.attach_reads(_run[3])
      == [("A", "bgp:aggregate", "aggregate-address 10.8.0.0/16 policy"),
          ("S+T", "bgp:suppress",
           "aggregate-address 10.8.0.0/16 suppress-policy")]
      and prismash.attach_reads("router ospf") == [],
      "attach_reads: both maps of one aggregate")
_ap = prismash.policy_applications(_run)
check(_ap["C"] == ["neighbor 10.0.0.2 default-originate",
                   "neighbor 10.0.0.2 advertise exist"]
      and _ap["S"] == ["aggregate-address 10.8.0.0/16 suppress-policy "
                       "[ S T ]"]
      and _ap["F"] == ["fib-policy static", "fib-policy ospf"]
      and _ap["D"] == ["ospf default-information", "isis default-information"]
      and _ap["T"] == ["aggregate-address 10.8.0.0/16 suppress-policy "
                       "[ S T ]", "table-policy"],
      f"show policy says where: {_ap}")
_c = Config()
_c.policy = ["policy-options policy-statement F term 1 set src 10.40.0.1",
             "policy-options policy-statement F term 1 then accept"]
_c.routing = ["ip protocol static route-map PO-F-FIB-STATIC"]
check("route-map PO-F-FIB-STATIC permit 10" in prismash.frr_lines(_c)
      and " set src 10.40.0.1" in prismash.frr_lines(_c),
      "a FIB policy read back is compiled for the next commit")
sh = shell()
sh.candidate = Config()
sh.mode = "config"
sh.candidate.policy = [
    "policy-options prefix-list UP 10.9.0.0/16",
    "policy-options policy-statement C term 1 from prefix-list UP",
    "policy-options policy-statement C term 1 then accept",
    "policy-options policy-statement LP term 1 set local-preference 5",
    "policy-options policy-statement F term 1 set src 10.40.0.1",
    "policy-options policy-statement P term 1 from protocol static"]
sh.frr_ctx = ["router bgp 65000", "address-family ipv4 unicast"]
_before = list(sh.candidate.routing)
rv, text = driven(lambda: sh.do_config(
    "neighbor 10.0.0.2 default-originate policy C"))
check(sh.candidate.routing[len(_before):]
      == ["neighbor 10.0.0.2 default-originate route-map PO-C-BGP-DEFAULT"],
      f"default-originate staged as the map: {text!r}")
_before = list(sh.candidate.routing)
for _bad, _why in (
        ("table-policy NOPE", "no policy-statement named NOPE"),
        ("table-policy LP", "set local-preference does nothing on "
                            "table-policy"),
        ("aggregate-address 10.8.0.0/16 suppress-policy LP",
         "cannot be applied on aggregate-address suppress-policy"),
        ("network 10.6.0.0/16 policy F", "set src is for a fib-policy"),
        ("network 10.6.0.0/16 policy", "usage: network <prefix> policy"),
        ("aggregate-address 10.8.0.0/16 summary-only suppress-policy C",
         "one or the other")):
    rv, text = driven(lambda: sh.do_config(_bad))
    check(_why in text and sh.candidate.routing == _before,
          f"{_bad!r} refused, nothing staged: {text!r}")
rv, text = driven(lambda: sh.do_config(
    "no neighbor 10.0.0.2 default-originate policy C"))
check(not any("default-originate" in l for l in sh.candidate.routing),
      f"no takes back the staged line: {sh.candidate.routing}")
sh.frr_ctx = ["router ospf"]
rv, text = driven(lambda: sh.do_config(
    "default-information originate policy P"))
check("from protocol has nothing to match on ospf default-information"
      in text, f"the IGP default chooses its routes: {text!r}")
sh.frr_ctx = []
rv, text = driven(lambda: sh.do_config("fib-policy static F"))
check(sh.candidate.routing[-2:]
      == ["ip protocol static route-map PO-F-FIB-STATIC",
          "ipv6 protocol static route-map PO-F-FIB-STATIC"],
      f"a fib-policy is both families' protocol map: {text!r}")
rv, text = driven(lambda: sh.do_config("fib-policy static C"))
check(sh.candidate.routing[-1] == "ipv6 protocol static route-map "
      "PO-C-FIB-STATIC", f"a fib-policy without a set: {text!r}")
rv, text = driven(lambda: sh.do_config("fib-policy kernel F"))
check("usage: fib-policy bgp|ospf|isis|static <policy>" in text,
      f"a protocol zebra takes no map for: {text!r}")
rv, text = driven(lambda: sh.attach_policy("no such thing", ["x"]))
check("error: nothing staged" in text and "no such thing" in text,
      f"Shell.attach_policy says a refusal that is not a usage: {text!r}")
sh.frr_ctx = ["router bgp 65000"]
sh.frr = type("F", (prismash.Frr,), {
    "dryrun": lambda s, ctx, l: (prismash.Frr.DRYRUN_REFUSED, "% Unknown")})()
_before = list(sh.candidate.routing)
rv, text = driven(lambda: sh.do_config("table-policy C"))
check("FRR answered: % Unknown" in text and sh.candidate.routing == _before,
      f"FRR is asked before anything is staged: {text!r}")
sh.frr_ctx = []
_handed = ["route-map PO-C-BGP-DEFAULT permit 10", "exit",
           "route-map MINE permit 10", "exit"]
check(prismash.compiled_map_warning(
          "The route-map 'PO-C-BGP-DEFAULT' does not exist.", _handed)
      and not prismash.compiled_map_warning(
          "The route-map 'PO-GONE-BGP-DEFAULT' does not exist.", _handed)
      and not prismash.compiled_map_warning(
          "The route-map 'MINE' does not exist.", _handed),
      "compiled_map_warning: bgpd's warning about a map the same commit "
      "defines, and only that")
ok("each attach point typed, translated, refused and read back")

print("\n=== routing policy, phase C: a VRF imports through a policy (#537) ===")
sh = shell()
sh.candidate = Config()
sh.mode = "config"
sh.candidate.policy = [
    "policy-options prefix-list SVC 203.0.113.0/24",
    "policy-options policy-statement L term 1 from prefix-list SVC",
    "policy-options policy-statement L term 1 from source-vrf svc",
    "policy-options policy-statement L term 1 set local-preference 200",
    "policy-options policy-statement L term 1 then accept",
    "policy-options policy-statement M term 1 then accept",
    "policy-options policy-statement NH term 1 set next-hop 10.0.0.9",
    "policy-options policy-statement NB term 1 from neighbor 10.0.0.2"]
sh.candidate.routing = ["router bgp 65000 vrf red",
                        "address-family ipv4 unicast"]
sh.frr_ctx = ["router bgp 65000 vrf red", "address-family ipv4 unicast"]
rv, text = driven(lambda: sh.do_config("vrf-import svc policy L"))
check(sh.candidate.routing[2:] == ["import vrf svc",
                                   "import vrf route-map PO-L-BGP-VRFIMPORT"],
      f"vrf-import with a policy: FRR's import vrf and its route-map: "
      f"{text!r} {sh.candidate.routing}")
rv, text = driven(lambda: sh.do_config("vrf-import default policy L"))
check(sh.candidate.routing[4:] == ["import vrf default"],
      f"a second VRF through the same policy: the map once: "
      f"{sh.candidate.routing}")
_before = list(sh.candidate.routing)
for _bad, _why in (
        ("vrf-import blue policy M", "imports through policy L"),
        ("vrf-import blue", "imports through policy L"),
        ("vrf-import prisma-bp policy L",
         "prisma-bp is a VRF of the box's own: no route leaks out of it"),
        ("vrf-import red policy L", "does not import from itself"),
        ("vrf-import bad/name policy L", "is not a VRF name"),
        ("vrf-import blue policy", "usage: vrf-import <vrf>|default"),
        ("vrf-import blue route-map X", "usage: vrf-import <vrf>|default"),
        ("import vrf prisma-bp", "no route leaks out of it")):
    rv, text = driven(lambda: sh.do_config(_bad))
    check(_why in text and sh.candidate.routing == _before,
          f"{_bad!r} refused, nothing staged: {text!r}")
rv, text = driven(lambda: sh.do_config("no vrf-import svc"))
check("import vrf svc" not in sh.candidate.routing
      and "import vrf route-map PO-L-BGP-VRFIMPORT" in sh.candidate.routing,
      f"no takes one import back; the policy stays for the other: "
      f"{sh.candidate.routing}")
rv, text = driven(lambda: sh.do_config("no vrf-import default policy L"))
check(sh.candidate.routing == ["router bgp 65000 vrf red",
                               "address-family ipv4 unicast"],
      f"and with the last import, the policy: {sh.candidate.routing}")
rv, text = driven(lambda: sh.do_config("no vrf-import default"))
check("does not import default" in text
      and len(sh.candidate.routing) == 2,
      f"no of an import nothing holds: nothing removed: {text!r}")
for _bad, _why in (
        ("vrf-import blue policy NH",
         "set next-hop does nothing on vrf-import"),
        ("vrf-import blue policy NB",
         "from neighbor matches nothing on vrf-import"),
        ("vrf-import blue policy NOPE", "no policy-statement named NOPE")):
    rv, text = driven(lambda: sh.do_config(_bad))
    check(_why in text and len(sh.candidate.routing) == 2,
          f"{_bad!r} refused: {text!r}")
rv, text = driven(lambda: sh.do_config("vrf-import blue"))
check(sh.candidate.routing[-1] == "import vrf blue",
      f"an import without a policy, in a family that has none: {text!r}")
# Each block entered as an operator enters it, so the flat candidate says
# which block every line is in.
for _l in ("exit-address-family", "exit", "router bgp 65000 vrf prisma-bp",
           "address-family ipv4 unicast"):
    driven(lambda: sh.do_config(_l))
check(sh.frr_ctx == ["router bgp 65000 vrf prisma-bp",
                     "address-family ipv4 unicast"], f"in: {sh.frr_ctx}")
rv, text = driven(lambda: sh.do_config("vrf-import default"))
check("no route leaks into it" in text, f"nothing leaks into prisma-bp: "
      f"{text!r}")
for _l in ("exit-address-family", "exit", "router bgp 65000"):
    driven(lambda: sh.do_config(_l))
rv, text = driven(lambda: sh.do_config("vrf-import red"))
check("a VRF imports per address family" in text,
      f"per address family: {text!r}")
driven(lambda: sh.do_config("address-family ipv6 unicast"))
rv, text = driven(lambda: sh.do_config("vrf-import red policy M"))
check(sh.candidate.routing[-2:] == ["import vrf red",
                                    "import vrf route-map PO-M-BGP-VRFIMPORT"],
      f"the default table imports a VRF, in IPv6: {text!r}")
rv, text = driven(lambda: sh.vrf_import("router bgp 65000",
                                        ["vrf-import", "red", "policy", "L"]))
check("imports through policy M" in text,
      f"Shell.vrf_import: the family's policy is M: {text!r}")
sh.frr_ctx = []
check(prismash.policy_uses(sh.candidate.routing) == {("M", "bgp:vrfimport")}
      and prismash.policy_applications(sh.candidate.routing)
      == {"M": ["vrf-import"]},
      f"read back as its use, and where: "
      f"{prismash.policy_applications(sh.candidate.routing)}")
_lr = ["router bgp 65000 vrf prisma-bp", " address-family ipv4 unicast",
       "  import vrf default", " exit-address-family", "exit",
       "router bgp 65000", " address-family ipv4 unicast",
       "  import vrf prisma-bp", "  import vrf red",
       "  import vrf route-map PO-M-BGP-VRFIMPORT", " exit-address-family",
       "exit"]
check([s for s, _ in prismash.leak_findings(_lr)]
      == ["router bgp 65000 vrf prisma-bp: import vrf default",
          "router bgp 65000: import vrf prisma-bp"],
      f"leak_findings: a file's leaks into and out of a reserved VRF: "
      f"{prismash.leak_findings(_lr)}")
check(prismash.bgp_vrf("router bgp 65000 vrf red") == "red"
      and prismash.bgp_vrf("router bgp 65000") == "default"
      and prismash.leak_error("default", "red") is None
      and "into it" in prismash.leak_error("prisma-x", "red"),
      "bgp_vrf and leak_error")
ok("vrf-import typed, translated, refused and read back")

print("\n=== a line the policy compiled to that a daemon refused (#539) ===")
# The case #535's lab found: `set extcommunity rt ... additive` passed vtysh
# -C, bgpd refused the line when it was applied, and the commit said "commit
# complete" over a clause that ran with no set. What decides here is FRR's
# read-back, so a refusal is a line absent from it.
_pc = Config()
_pc.policy = ["policy-options policy-statement P term t from route-filter "
              "10.0.0.0/8 orlonger",
              "policy-options policy-statement P term t set weight 7",
              "policy-options policy-statement P term t then accept"]
_pc.routing = ["router bgp 65000", " neighbor 10.0.0.2 remote-as 65001",
               " address-family ipv4 unicast",
               "  neighbor 10.0.0.2 route-map PO-P-IN in",
               " exit-address-family", "exit"]
_sent = prismash.compiled_lines(_pc)
check(prismash.route_map_bodies(["route-map A permit 10", " set weight 7",
                                  "exit", "ip x", "route-map B permit 10",
                                  " set weight 7", "exit"])
      == {"route-map A permit 10": ["set weight 7"],
          "route-map B permit 10": ["set weight 7"]},
      "route_map_bodies: each clause's statements, so the same line in two "
      "clauses is two statements")
_dm = prismash.compiled_daemons(_pc)
check(_dm == {"PO-P-IN": "bgpd"}
      and prismash.compiled_maps(_pc) == {"PO-P-IN": ("P", "import")},
      f"compiled_maps and compiled_daemons: the map, its use, its daemon: "
      f"{_dm}")


def _rm(sets, matches=("ip address prefix-list PO-P-Tt-4",), daemon="bgpd"):
    """`show route-map json` with PO-P-IN 10 running these rules."""
    return {daemon: {"PO-P-IN": {"rules": [
        {"sequenceNumber": 10, "matchClauses": list(matches),
         "setClauses": list(sets)},
        {"sequenceNumber": 65535, "matchClauses": [], "setClauses": []}]}}}


_asked = []


def _reads(maps):
    def read():
        _asked.append(1)
        return maps
    return read


check(prismash.compiled_refused(_sent, [], _reads(_rm(["weight 7"])), _dm)
      == [] and prismash.compiled_refused(_sent, [], _reads(_rm([])), _dm)
      == [("route-map PO-P-IN permit 10", "set weight 7")],
      "compiled_refused: a set the daemon runs is kept, one it does not is "
      "refused, with its clause")
check(prismash.compiled_refused(_sent, [], _reads(_rm(["weight 7"], [])),
                                _dm)
      == [("route-map PO-P-IN permit 10",
           "match ip address prefix-list PO-P-Tt-4")],
      "and a match the same way")
check(prismash.compiled_refused(_sent, [], _reads(_rm(["weight seven"])),
                                _dm) == [],
      "a rule FRR words its own way is not a refusal: it is counted")
# The measurement that made this the runtime's question: every daemon
# holds every map, and zebra running the clause with no BGP set is not bgpd
# refusing it.
_both = _rm(["weight 7"])
_both.update(_rm([], daemon="zebra"))
check(prismash.compiled_refused(_sent, [], _reads(_both), _dm) == [],
      "another daemon's view of the map is not asked")
check(prismash.compiled_refused(_sent, [], _reads({"bgpd": {}}), _dm)
      == [(None, "route-map PO-P-IN permit 10"),
          (None, "route-map PO-P-IN permit 65535")],
      "a clause the daemon does not have at all is refused whole")
check(prismash.compiled_refused(_sent, [], _reads(None), _dm) == []
      and prismash.compiled_refused(_sent, [], _reads({"zebra": {}}), _dm)
      == [],
      "an FRR that did not answer, or a daemon not in the answer, is not a "
      "refusal")
_asked.clear()
check(prismash.compiled_refused(_sent, _sent, _reads(_rm([])), _dm) == []
      and not _asked,
      "a clause the last commit sent the same is not judged, nor FRR asked")
check(prismash.compiled_owner(_pc, "route-map PO-P-IN permit 10")
      == "policy-statement P term t"
      and prismash.compiled_owner(_pc, "route-map PO-P-IN permit 65535")
      == "no term decided: the default for this use"
      and prismash.compiled_owner(_pc, "route-map PO-Z-IN permit 10") is None
      and prismash.compiled_owner(_pc, None) is None,
      "compiled_owner: the term a clause came from, in the VTY's words")


def _readback_commit(maps):
    """A commit of _pc over an empty box whose FRR runs `maps`."""
    sh = shell(frr=RecFrr({"show route-map json": (0, json.dumps(maps)),
                           "show running-config": (
                               0, "\n".join(_sent + _pc.routing))}))
    sh.candidate = Config.parse(_pc.text())
    sh.candidate.policy, sh.candidate.routing = _pc.policy, _pc.routing
    applied = []

    def _apply(cfg, check_only=False):
        if not check_only:
            applied.append(cfg)
        return True
    sh.apply = _apply
    sh.rb = types.SimpleNamespace(push=lambda *a, **k: None)
    sh.record_applied = lambda *a, **k: None
    rv, text = driven(lambda: sh.do_commit([]))
    return applied, text


_applied, _said = _readback_commit(_rm([]))
check("FRR refused 1 line(s) the routing policy compiled to" in _said
      and "in policy-statement P term t:" in _said
      and "route-map PO-P-IN permit 10: set weight 7" in _said
      and "rolling back to the previous state" in _said
      and "commit complete" not in _said,
      f"the commit refuses, naming the term and the line: {_said!r}")
check(len(_applied) == 2 and not _applied[1].policy,
      f"and puts the previous state back: {[a.policy for a in _applied]}")
_applied, _said = _readback_commit(_rm(["weight 7"]))
check("commit complete" in _said and len(_applied) == 1,
      f"a commit FRR kept whole completes: {_said!r}")
_applied, _said = _readback_commit({})
check("FRR refused" not in _said and "commit complete" in _said,
      f"an FRR that does not answer is not a refusal: {_said!r}")
_pr = Config()
_pr.policy = ["policy-options policy-statement G term t then accept",
              "policy-options policy-statement R term t from policy G",
              "policy-options policy-statement R term t then reject",
              "policy-options policy-statement S term t from policy G",
              "policy-options policy-statement S term t then accept"]
_pr.routing = ["router bgp 65000", " address-family ipv4 unicast",
               "  neighbor 10.0.0.2 route-map PO-R-IN in",
               "  neighbor 10.0.0.2 route-map PO-S-OUT out",
               " exit-address-family", "exit"]
check(prismash.compiled_maps(_pr) == {"PO-R-IN": ("R", "import"),
                                      "PO-G-NOT-IN": ("G", "import"),
                                      "PO-S-OUT": ("S", "export"),
                                      "PO-G-SUB-OUT": ("G", "export")},
      f"compiled_maps: the maps from policy calls, inverted under a reject "
      f"term: {prismash.compiled_maps(_pr)}")
ok("the read-back of what the policy compiled to")

print("\n=== a line of the operator's own route-maps and lists a daemon "
      "refused (#544) ===")
# Typed in this session, so not indented: the block decides, not the column.
_typed = ["route-map OP permit 10", "match ip address prefix-list L",
          "set metric 5", "set extcommunity rt 65000:100 additive", "exit",
          "router bgp 65000", "neighbor 10.0.0.2 remote-as 65001",
          "address-family ipv4 unicast",
          "neighbor 10.0.0.2 route-map OP in", "exit-address-family",
          "exit", "ip prefix-list L seq 5 permit 10.0.0.0/8 le 7",
          "ip prefix-list L seq 10 permit 192.168.0.0/16 le 24"]
check(prismash.typed_route_map_bodies(_typed)
      == {"route-map OP permit 10": [
          "match ip address prefix-list L", "set metric 5",
          "set extcommunity rt 65000:100 additive"]}
      and prismash.typed_route_map_bodies(
          ["route-map A permit 10", " set weight 7", "exit"])
      == {"route-map A permit 10": ["set weight 7"]},
      "typed_route_map_bodies: each clause's statements, typed or read back")
check(prismash.changed_clauses({"a": ["x"], "b": ["y"]}, {"a": ["x"]})
      == {"b": ["y"]}
      and prismash.changed_clauses({"a": ["x"]}, {"a": ["z"]})
      == {"a": ["x"]},
      "changed_clauses: what the last commit did not send the same")
_od = prismash.operator_map_daemons(
    _typed + ["router ospf", " redistribute bgp route-map OP",
              " redistribute static route-map ST", "exit",
              "ip protocol bgp route-map ZB",
              "route-map ZB permit 10", " call SUB", "exit"])
check(_od == {"OP": {"bgpd", "ospfd"}, "ST": {"ospfd"},
              "ZB": {"zebra"}, "SUB": {"zebra"}},
      f"operator_map_daemons: each map by the blocks that apply it, a "
      f"called map where its caller runs: {_od}")


def _om(sets, daemon="bgpd", name="OP"):
    """`show route-map json` with OP 10 running these sets."""
    return {daemon: {name: {"rules": [
        {"sequenceNumber": 10,
         "matchClauses": ["ip address prefix-list L"],
         "setClauses": list(sets)}]}}}


_new = prismash.typed_route_map_bodies(_typed)
check(prismash.clauses_refused(_new, _reads(_om(["metric 5"])),
                               {"OP": {"bgpd"}})
      == [("route-map OP permit 10",
           "set extcommunity rt 65000:100 additive")],
      "clauses_refused: the operator's line bgpd does not run is named")
# One map for a BGP neighbor and an OSPF redistribution: ospfd holds none
# of BGP's sets, and that is FRR running the map as written.
_two = _om(["metric 5", "extcommunity rt 65000:100"])
_two.update(_om(["metric 5"], daemon="ospfd"))
check(prismash.clauses_refused(_new, _reads(_two), {"OP": {"bgpd", "ospfd"}})
      == [],
      "a line one of the map's daemons runs is not refused")
_two = _om(["metric 5"])
_two.update(_om(["metric 5"], daemon="ospfd"))
check(prismash.clauses_refused(_new, _reads(_two), {"OP": {"bgpd", "ospfd"}})
      == [("route-map OP permit 10",
           "set extcommunity rt 65000:100 additive")],
      "and one none of them runs is")
check(prismash.clauses_refused(_new, _reads({"bgpd": {}}), {"OP": "bgpd"})
      == [(None, "route-map OP permit 10")]
      and prismash.clauses_refused(_new, _reads(_om([])), {}) == [],
      "a clause no daemon has is refused whole; a map nothing applies is "
      "not judged")
_le = prismash.list_entries(_typed + [
    "bgp community-list standard C seq 5 permit 65000:1",
    "bgp community-list 7 permit 65000:2",
    "ip prefix-list L description permit all"])
check(_le == {("ip prefix-list", "L"): [
                  "ip prefix-list L permit 10.0.0.0/8 le 7",
                  "ip prefix-list L permit 192.168.0.0/16 le 24"],
              ("bgp community-list", "C"): [
                  "bgp community-list standard C permit 65000:1"],
              ("bgp community-list", "7"): [
                  "bgp community-list 7 permit 65000:2"]},
      f"list_entries: each list's entries without their seq: {_le}")
check(prismash.not_list_entries(
          ["ip prefix-list L seq 5 permit 10.0.0.1/8",
           "neighbor 10.0.0.2 remote-as 65001",
           "ip prefix-list L description permit all"])
      == ["neighbor 10.0.0.2 remote-as 65001",
          "ip prefix-list L description permit all"],
      "not_list_entries: the list entries are the count's to judge, the "
      "rest #197's")
_cl = prismash.changed_lists(_typed, [])
check(_cl == {("ip prefix-list", "L"): _le[("ip prefix-list", "L")]}
      and prismash.changed_lists(_typed, _typed) == {},
      "changed_lists: the lists this commit changed")
# FRR 10.7.1 drops the entry it refused from its configuration.
_frr = [" ip prefix-list L seq 10 permit 192.168.0.0/16 le 24"]
check(prismash.lists_refused(_cl, _frr)
      == ["ip prefix-list L permit 10.0.0.0/8 le 7"]
      and prismash.lists_refused(_cl, _frr + [
          "ip prefix-list L seq 5 permit 10.0.0.0/8 le 7"]) == []
      and prismash.lists_refused(_cl, [
          "ip prefix-list L seq 5 permit 10.0.0.0/8 ge 8 le 7",
          "ip prefix-list L seq 10 permit 192.168.0.0/16 ge 16 le 24"])
      == [],
      "lists_refused: an entry FRR does not hold is refused, one it words "
      "its own way is counted, not refused")


def _typed_commit(maps, running):
    """A commit of _typed over an empty box whose FRR runs `maps` and reads
    back `running`."""
    sh = shell(frr=RecFrr({"show route-map json": (0, json.dumps(maps)),
                           "show running-config": (0, "\n".join(running))}))
    sh.candidate = Config()
    sh.candidate.routing = list(_typed)
    applied = []

    def _apply(cfg, check_only=False):
        if not check_only:
            applied.append(cfg)
        return True
    sh.apply = _apply
    sh.rb = types.SimpleNamespace(push=lambda *a, **k: None)
    sh.record_applied = lambda *a, **k: None
    rv, text = driven(lambda: sh.do_commit([]))
    return applied, text


_whole = ["route-map OP permit 10", " match ip address prefix-list L",
          " set metric 5", " set extcommunity rt 65000:100 additive",
          "ip prefix-list L seq 5 permit 10.0.0.0/8 le 7",
          "ip prefix-list L seq 10 permit 192.168.0.0/16 le 24",
          "router bgp 65000", " neighbor 10.0.0.2 remote-as 65001",
          " address-family ipv4 unicast",
          "  neighbor 10.0.0.2 route-map OP in", " exit-address-family"]
_applied, _said = _typed_commit(_om(["metric 5"]), _whole)
check("FRR refused 1 line(s) of your route-maps and lists" in _said
      and "route-map OP permit 10: set extcommunity rt 65000:100 additive"
      in _said and "the routing policy compiled to" not in _said
      and "rolling back to the previous state" in _said
      and "commit complete" not in _said and len(_applied) == 2,
      f"the commit refuses the operator's line and rolls back: {_said!r}")
_applied, _said = _typed_commit(
    _om(["metric 5", "extcommunity rt 65000:100 additive"]),
    [l for l in _whole if "le 7" not in l])
check("FRR refused 1 line(s) of your route-maps and lists" in _said
      and "ip prefix-list L permit 10.0.0.0/8 le 7" in _said
      and "commit complete" not in _said and len(_applied) == 2,
      f"and a list entry FRR did not keep the same way: {_said!r}")
_applied, _said = _typed_commit(
    _om(["metric 5", "extcommunity rt 65000:100 additive"]), _whole)
check("FRR refused" not in _said and "commit complete" in _said
      and len(_applied) == 1,
      f"a commit FRR kept whole is not refused: {_said!r}")
ok("the read-back of the operator's own route-maps and lists")

print("\n=== Shell.write_tune_key: one assignment, every other line kept ===")
# Mutation shown red: write only the assignment, dropping the other lines.
tune = os.path.join(_root, "tune", "tune.env")
saved = prismash.Shell.TUNE_ENV
try:
    prismash.Shell.TUNE_ENV = tune
    sh = shell()
    check(sh.write_tune_key("PRISMA_WORKERS", "4") == (True, ""),
          "no file at all: one is written rather than refusing")
    with open(tune) as f:
        first = f.read()
    check(first.splitlines()[0].startswith("# Written by prismash")
          and first.splitlines()[-1] == "PRISMA_WORKERS=4",
          f"the new file says who wrote it and holds the key: {first!r}")
    with open(tune, "w") as f:
        f.write("# why the field ships empty\nPRISMA_DATAPLANE_IF=\n"
                "  PRISMA_WORKERS=2\nPRISMA_SUBSCRIBERS=1000\n")
    check(sh.write_tune_key("PRISMA_WORKERS", "8") == (True, ""), "written")
    with open(tune) as f:
        got = f.read()
    check(got == "# why the field ships empty\nPRISMA_DATAPLANE_IF=\n"
                 "PRISMA_WORKERS=8\nPRISMA_SUBSCRIBERS=1000\n",
          f"the one key replaced in place, the prose and the others "
          f"untouched: {got!r}")
    check(not os.path.exists(tune + ".new"),
          "the temporary file was renamed into place, not left")
    prismash.Shell.TUNE_ENV = os.path.join(_root, "no-such-dir-file", "x",
                                         "tune.env")
    os.makedirs(os.path.dirname(os.path.dirname(prismash.Shell.TUNE_ENV)),
                exist_ok=True)
    blocked = os.path.dirname(prismash.Shell.TUNE_ENV)
    open(blocked, "w").close()       # a FILE where the directory should be
    okk, why = sh.write_tune_key("PRISMA_WORKERS", "8")
    check(okk is False and why.startswith(prismash.Shell.TUNE_ENV),
          f"a file that cannot be written is (False, path: reason): "
          f"{(okk, why)!r}")
finally:
    prismash.Shell.TUNE_ENV = saved
ok("hand-editable file: rewritten line for line, through a rename")

print("\n=== Shell.write_dataplane_if: the tuner's input key, and only "
      "it ===")
# Mutation shown red: write the name under another key.
saved = prismash.Shell.TUNE_ENV
try:
    prismash.Shell.TUNE_ENV = tune
    with open(tune, "w") as f:
        f.write("PRISMA_DATAPLANE_IF=\nPRISMA_WORKERS=8\n")
    check(shell().write_dataplane_if("ens19") == (True, ""), "written")
    with open(tune) as f:
        check(f.read() == "PRISMA_DATAPLANE_IF=ens19\nPRISMA_WORKERS=8\n",
              "PRISMA_DATAPLANE_IF carries the card, the worker cap stays")
finally:
    prismash.Shell.TUNE_ENV = saved
ok("write_tune_key(TUNE_KEY, name)")

print("\n=== Shell.apply_dataplane_if: store, restart the tuner, say what "
      "is outstanding ===")
# Mutation shown red: rc 127 answers "" instead of "stored; the tuner is not
# on this machine".
saved = prismash.Shell.TUNE_ENV
try:
    prismash.Shell.TUNE_ENV = tune
    with open(tune, "w") as f:
        f.write("PRISMA_DATAPLANE_IF=\n")
    run = RecRunner()
    check(shell(runner=run).apply_dataplane_if("ens19") == (True, ""),
          "stored and recomputed: nothing outstanding")
    check(run.asked == [["systemctl", "restart", prismash.Shell.TUNE_UNIT]],
          f"the unit is restarted, because it carries the EnvironmentFile: "
          f"{run.asked}")
    with open(tune) as f:
        check("PRISMA_DATAPLANE_IF=ens19" in f.read().splitlines(),
              "and the card was stored first")
    run = RecRunner({("systemctl",): (127, "systemctl: not found")})
    check(shell(runner=run).apply_dataplane_if("ens19")
          == (True, "stored; the tuner is not on this machine, so nothing "
                    "was recomputed"),
          "rc 127 is a developer box: stored, and said so")
    run = RecRunner({("systemctl",): (1, "Job for prisma-routing-tune.service "
                                         "failed.\n")})
    okk, why = shell(runner=run).apply_dataplane_if("ens19")
    check(okk is True and why == "stored, but the tuner did not run: Job for "
          "prisma-routing-tune.service failed.",
          f"any other failure is stored and reported with systemd's row: "
          f"{(okk, why)!r}")
    prismash.Shell.TUNE_ENV = blocked + "/tune.env"
    run = RecRunner()
    okk, why = shell(runner=run).apply_dataplane_if("ens19")
    check(okk is False and run.asked == [],
          f"a file that cannot be written runs no tuner: {(okk, why)!r} "
          f"{run.asked}")
finally:
    prismash.Shell.TUNE_ENV = saved
ok("written, then the unit; three sentences for three rcs; no restart of "
   "the dataplane")

# ------------------------------------------------------------- clears

print("\n=== Shell.clear_ospf: process or one neighbour, administrators "
      "only ===")
# Mutation shown red: send "clear ip ospf process" for a neighbour too.
frr = RecFrr({"clear ip ospf": (0, "")})
sh = shell(frr=frr)
rv, text = driven(lambda: sh.clear_ospf(["process"]))
check(frr.asked == ["clear ip ospf process"]
      and rows(text) == ["cleared: process"],
      f"process: FRR's spelling, then a row saying so: {frr.asked} {text!r}")
rv, text = driven(lambda: sh.clear_ospf(["neighbor", "10.0.0.2"]))
check(frr.asked[-1] == "clear ip ospf neighbor 10.0.0.2"
      and rows(text) == ["cleared: neighbor 10.0.0.2"],
      f"one neighbour: {frr.asked[-1]} {text!r}")
n = len(frr.asked)
rv, text = driven(lambda: sh.clear_ospf(["sideways"]))
check(len(frr.asked) == n and rows(text) == [
      "error: usage: clear ospf process | clear ospf neighbor <A.B.C.D>"],
      f"a shape it has not got is a usage line, nothing sent: {text!r}")
sh = shell(frr=frr, caps={prismash.CAP_CGNAT})
rv, text = driven(lambda: sh.clear_ospf(["process"]))
check(len(frr.asked) == n and "permission denied" in text,
      f"a cgnat account is refused before FRR is asked: {frr.asked[n:]}")
frr = RecFrr({"clear ip ospf": (1, "% OSPF is not running")})
rv, text = driven(lambda: shell(frr=frr).clear_ospf(["process"]))
check(rows(text) == ["% OSPF is not running"],
      f"FRR's refusal is printed and nothing claims a clear: {text!r}")
ok("an outage on purpose, so the role is checked and FRR's answer shown")

print("\n=== Shell.clear_interface and clear_cgnat (#558, phase 5) ===")


class NamedApi(RecApi):
    """RecApi that knows one interface by name."""

    def interface_index(self, name):
        return {"ens19": 5}.get(name)


# Mutation shown red: clear every interface when one is named.
api = NamedApi()
sh = shell(api=api)
rv, text = driven(lambda: sh.clear_interface(["counters"]))
check(api.calls == [("sw_interface_clear_stats", {"sw_if_index": 0xFFFFFFFF})]
      and rows(text) == ["cleared: counters of every interface"],
      f"bare: every interface, index ~0: {api.calls} {text!r}")
rv, text = driven(lambda: sh.clear_interface(["counters", "ens19"]))
check(api.calls[-1] == ("sw_interface_clear_stats", {"sw_if_index": 5})
      and rows(text) == ["cleared: counters of ens19"],
      f"one interface, by its index: {api.calls[-1]} {text!r}")
n = len(api.calls)
rv, text = driven(lambda: sh.clear_interface(["counters", "ens9"]))
check(len(api.calls) == n and rows(text) == [
      "error: the dataplane has no interface named ens9"],
      f"an interface the dataplane has not got is said, nothing sent: {text!r}")
rv, text = driven(lambda: sh.clear_interface(["sideways"]))
check(len(api.calls) == n and rows(text) == [
      "error: usage: clear interface counters [<interface>]"],
      f"a shape it has not got is a usage line: {text!r}")
rv, text = driven(lambda: shell(api=api, caps={prismash.CAP_CGNAT})
                  .clear_interface(["counters"]))
check(len(api.calls) == n and "permission denied" in text,
      f"a CGNAT account may not reset every graph on the box: {text!r}")
api = NamedApi(refuse={"sw_interface_clear_stats"})
rv, text = driven(lambda: shell(api=api).clear_interface(["counters"]))
check(rows(text)[0] == "error: the counters could not be cleared"
      and "stub" in text and "cleared:" not in text,
      f"a refusal is said, and no clear is claimed: {text!r}")

# Mutation shown red: send the address unchecked, or claim a clear on rc 1.
dp = RecDp({"bpa clear subscriber": (0, "cleared 3 session(s) of 100.64.0.10\n"
                                     "its block is released after the "
                                     "hold-down (60s)\n")})
sh = shell(dp=dp, caps={prismash.CAP_CGNAT})
rv, text = driven(lambda: sh.clear_cgnat(["subscriber", "100.64.0.10"]))
check(dp.commands == ["bpa clear subscriber 100.64.0.10"]
      and rows(text)[0] == "cleared 3 session(s) of 100.64.0.10",
      f"the dataplane's own clear, its rows printed; a CGNAT operator may: "
      f"{dp.commands} {text!r}")
for rest, said in ((["subscriber", "100.64.0.300"],
                    "error: a subscriber is an IPv4 or IPv6 address: 100.64.0.300"),
                   (["sessions"], "error: usage: clear cgnat subscriber "
                                  "<A.B.C.D>")):
    rv, text = driven(lambda: sh.clear_cgnat(rest))
    check(dp.commands == ["bpa clear subscriber 100.64.0.10"]
          and rows(text) == [said], f"{rest}: {text!r}")
rv, text = driven(lambda: shell(dp=dp, caps=set()).clear_cgnat(
    ["subscriber", "100.64.0.10"]))
check(len(dp.commands) == 1 and "permission denied" in text,
      f"a read-only account is refused before the dataplane is asked: {text!r}")
dp = RecDp({"bpa clear subscriber": (1, "100.64.9.9 is not inside any "
                                     "configured pool\n")})
rv, text = driven(lambda: shell(dp=dp).clear_cgnat(["subscriber", "100.64.9.9"]))
check(rows(text)[0] == "error: the subscriber's sessions could not be cleared"
      and "100.64.9.9 is not inside any configured pool" in text,
      f"the dataplane's refusal, under a verdict of its own: {text!r}")
ok("interface counters over the binary API; a subscriber's sessions by the "
   "dataplane's own clear; each role checked first")

print("\n=== Shell.clear_isis: all adjacencies, or one by system-id ===")
# Mutation shown red: drop the system-id from the command.
frr = RecFrr({"clear isis": (0, "")})
sh = shell(frr=frr)
rv, text = driven(lambda: sh.clear_isis([]))
check(frr.asked == ["clear isis neighbor"]
      and rows(text) == ["cleared: isis adjacency"],
      f"bare: every adjacency, FRR's spelling: {frr.asked} {text!r}")
rv, text = driven(lambda: sh.clear_isis(["adjacency", "1921.6800.0002"]))
check(frr.asked[-1] == "clear isis neighbor 1921.6800.0002"
      and rows(text) == ["cleared: isis adjacency 1921.6800.0002"],
      f"one system-id: {frr.asked[-1]} {text!r}")
n = len(frr.asked)
for rest in (["sideways"], ["adjacency", "a", "b"]):
    rv, text = driven(lambda: sh.clear_isis(rest))
    check(len(frr.asked) == n and rows(text)
          == ["error: usage: clear isis adjacency [<system-id>]"],
          f"{rest}: usage, nothing sent: {text!r}")
sh = shell(frr=frr, caps=set())
rv, text = driven(lambda: sh.clear_isis([]))
check(len(frr.asked) == n and "permission denied" in text,
      "a read-only account is refused before FRR is asked")
ok("four spellings of adjacency, an optional id, the role checked")

print("\n=== Shell.soft_reconfiguration_on: True, False, and None for a "
      "silent FRR ===")
# Mutation shown red: return False when FRR is not answering.
cfg = (_config.ROUTING_HEADER + "\nrouter bgp 65000\n"
       " neighbor 10.0.0.2 remote-as 65001\n"
       " address-family ipv4 unicast\n"
       "  neighbor 10.0.0.2 soft-reconfiguration inbound\n"
       " exit-address-family\n")
sh = shell(frr=RecFrr(up=True), running=cfg)
check(sh.soft_reconfiguration_on("10.0.0.2") is True,
      "the statement is in the running configuration: True")
check(sh.soft_reconfiguration_on("10.0.0.3") is False,
      "a neighbour without it: False")
sh = shell(frr=RecFrr(up=False), running=cfg)
check(sh.soft_reconfiguration_on("10.0.0.2") is None,
      "FRR not answering is None, not False")
ok("three answers, asked of available() first")

print("\n=== Shell.do_clear: bgp, ospf and isis dispatched, and the "
      "vendor spelling ===")
# Mutation shown red: drop the `clear ip ospf` rewrite.
frr = RecFrr({"clear": (0, "")}, up=True)
sh = shell(frr=frr, running=cfg)
rv, text = driven(lambda: sh.do_clear("clear bgp 10.0.0.2"))
check(frr.asked == ["clear bgp 10.0.0.2"]
      and rows(text) == ["cleared: 10.0.0.2"],
      f"clear bgp <n> reaches FRR as typed: {frr.asked} {text!r}")
rv, text = driven(lambda: sh.do_clear("clear bgp 10.0.0.2 soft in"))
check(frr.asked[-1] == "clear bgp 10.0.0.2 soft in",
      f"soft in, on a neighbour that keeps what it received: {frr.asked[-1]}")
n = len(frr.asked)
rv, text = driven(lambda: sh.do_clear("clear bgp 10.0.0.3 soft"))
check(len(frr.asked) == n and rows(text)[0]
      == "error: 10.0.0.3 does not keep what it received",
      f"soft on a neighbour that keeps nothing is refused, nothing sent: "
      f"{text!r}")
rv, text = driven(lambda: sh.do_clear("clear ip ospf process"))
check(frr.asked[-1] == "clear ip ospf process",
      f"the vendor spelling `clear ip ospf` goes to clear_ospf: "
      f"{frr.asked[-1]}")
rv, text = driven(lambda: sh.do_clear("clear ospf neighbor 10.0.0.9"))
check(frr.asked[-1] == "clear ip ospf neighbor 10.0.0.9", "and the short one")
rv, text = driven(lambda: sh.do_clear("clear isis"))
check(frr.asked[-1] == "clear isis neighbor", "clear isis goes to clear_isis")
n = len(frr.asked)
rv, text = driven(lambda: sh.do_clear("clear bgp"))
check(len(frr.asked) == n and rows(text)[0]
      == "error: usage: clear bgp <neighbour> [soft [in|out]]"
      and len(rows(text)) == 5,
      f"no neighbour is the usage, all five shapes (#558 phase 5 added "
      f"interface counters and a CGNAT subscriber): {text!r}")
sh = shell(frr=frr, running=cfg, caps={prismash.CAP_CGNAT})
rv, text = driven(lambda: sh.do_clear("clear bgp 10.0.0.2"))
check(len(frr.asked) == n and "permission denied" in text,
      "a cgnat account is refused before FRR is asked")
down = RecFrr({"clear": (0, "")}, up=False)
sh = shell(frr=down, running=cfg)
rv, text = driven(lambda: sh.do_clear("clear bgp 10.0.0.2 soft in"))
check(down.asked == [] and rows(text)[0] == "error: FRR is not answering",
      f"soft in on a silent FRR: refused, and no clear is sent to it: "
      f"{text!r}")
ok("one operational verb, three protocols, the soft premise read first")

# ------------------------------------------- the completion's sources

print("\n=== Shell.dyn_interfaces: the dataplane's names and states ===")
# Mutation shown red: keep local0.
show_if = ("              Name               Idx    State  "
           "MTU (L3/IP4/IP6/MPLS)\n"
           "GigabitEthernet0/14/0             1      up          9000/0/0/0\n"
           "  Ethernet address 00:11:22:33:44:55\n"
           "local0                            0     down          0/0/0/0\n"
           "loop0                             2      up          9000/0/0/0\n")
sh = shell(dp=RecDp({"show interface": (0, show_if)}))
check(sh.dyn_interfaces() == {"GigabitEthernet0/14/0": "interface (up)",
                              "loop0": "interface (up)"},
      f"header, continuation rows and local0 out, the state in: "
      f"{sh.dyn_interfaces()}")
check(shell(dp=RecDp({"show interface": (1, "")})).dyn_interfaces() == {},
      "a dataplane not answering offers nothing")
ok("read off show interface, VPP's own loopback left out")

ROUTING = ("router bgp 65000\n"
           " neighbor UP peer-group\n"
           " neighbor UP remote-as 65001\n"
           " neighbor 10.0.0.2 peer-group UP\n"
           " neighbor 10.0.0.3 remote-as 65003\n"
           " neighbor CORE peer-group\n"
           "router isis LAB\n"
           " net 49.0001.1921.6800.0001.00\n"
           "route-map RM-IN permit 10\n"
           "route-map RM-IN deny 20\n"
           "route-map RM-OUT deny 10\n"
           "bgp as-path access-list UPSTREAM seq 5 permit ^65001_\n"
           "bgp community-list standard CL-A seq 5 permit 65000:1\n"
           "bgp community-list CL-B seq 5 permit 65000:2\n"
           "bgp large-community-list expanded LCL seq 5 permit .*\n"
           "ip prefix-list PL4 seq 5 permit 10.0.0.0/8\n"
           "ipv6 prefix-list PL6 seq 5 permit 2001:db8::/32\n")
CAND = Config.parse(in_nat_block(
    "user alice\n role admin\nsnmp-user mon\n auth sha x\n"
    "nat cgnat pool A\n inside 10.0.0.0/8\n exit\n"))
CAND.routing = ROUTING.splitlines()

print("\n=== Shell.dyn_neighbours: addresses with their AS, groups named "
      "as groups ===")
# Mutation shown red: leave a group labelled "neighbour".
sh = shell()
sh.candidate = CAND
got = sh.dyn_neighbours()
check(got == {"UP": "peer group, AS 65001", "10.0.0.2": "peer-group UP",
              "10.0.0.3": "AS 65003", "CORE": "peer group"},
      f"a group is a group with its AS, a member names its group, a peer "
      f"its AS: {got}")
sh = shell(running=_config.ROUTING_HEADER + "\nrouter bgp 1\n"
           " neighbor 10.0.0.9 remote-as 9\n")
check(sh.dyn_neighbours() == {"10.0.0.9": "AS 9"},
      "with no candidate the running configuration answers")
ok("read from the configuration FRR reported, not a model kept here")

print("\n=== Shell.dyn_route_maps: each name once, with its first entry ===")
# Mutation shown red: found[w[1]] = ..., so the LAST entry labels it.
sh = shell()
sh.candidate = CAND
check(sh.dyn_route_maps() == {"RM-IN": "route-map (permit)",
                              "RM-OUT": "route-map (deny)"},
      f"two names, the first entry's action: {sh.dyn_route_maps()}")
ok("several entries share a name; the operator wants it once")

print("\n=== Shell.dyn_peer_groups: only the declaration makes one ===")
# Mutation shown red: take `neighbor X peer-group Y` as declaring X.
sh = shell()
sh.candidate = CAND
check(sh.dyn_peer_groups() == {"UP": "peer group", "CORE": "peer group"},
      f"10.0.0.2 uses a group and is not one: {sh.dyn_peer_groups()}")
ok("neighbor <name> peer-group, with nothing after it")

print("\n=== Shell.dyn_isis_tags: the instances, by tag ===")
# Mutation shown red: match `router` alone, so `router bgp 65000` is one.
sh = shell()
sh.candidate = CAND
check(sh.dyn_isis_tags() == {"LAB": "IS-IS instance"},
      f"router isis LAB and not router bgp: {sh.dyn_isis_tags()}")
ok("the tag an interface's ip router isis has to name")

print("\n=== Shell.dyn_as_path_lists: by name ===")
# Mutation shown red: take w[2] as the name.
sh = shell()
sh.candidate = CAND
check(sh.dyn_as_path_lists() == {"UPSTREAM": "as-path list"},
      f"bgp as-path access-list <name>: {sh.dyn_as_path_lists()}")
ok("the name after the three words")

print("\n=== Shell.dyn_community_lists: both kinds, labelled, with or "
      "without standard/expanded ===")
# Mutation shown red: take w[2] as the name always.
sh = shell()
sh.candidate = CAND
check(sh.dyn_community_lists() == {"CL-A": "community list",
                                   "CL-B": "community list",
                                   "LCL": "large-community list"},
      f"standard CL-A, bare CL-B, expanded large LCL: "
      f"{sh.dyn_community_lists()}")
ok("the name is after the kind word when there is one")

print("\n=== Shell.dyn_prefix_lists: both families in one answer ===")
# Mutation shown red: only `ip`.
sh = shell()
sh.candidate = CAND
check(sh.dyn_prefix_lists() == {"PL4": "ip prefix-list",
                                "PL6": "ipv6 prefix-list"},
      f"ip and ipv6, each saying which: {sh.dyn_prefix_lists()}")
ok("FRR owns them; the family is in the label")

print("\n=== Shell.dyn_pools, dyn_users, dyn_snmp_users: the candidate, or "
      "the running configuration ===")
# Mutation shown red: dyn_pools reading cfg.users.
sh = shell()
sh.candidate = CAND
check(sh.dyn_pools() == {"A": "CGNAT pool"}, f"pools: {sh.dyn_pools()}")
check(sh.dyn_users() == {"alice": "account"}, f"users: {sh.dyn_users()}")
check(sh.dyn_snmp_users() == {"mon": "SNMPv3 user"},
      f"snmp users: {sh.dyn_snmp_users()}")
sh = shell(running=in_nat_block("nat cgnat pool B\n inside 10.0.0.0/8\n"
                                " exit\nuser bob\n"
                                "snmp-user other\n auth sha y\n"))
check(sh.dyn_pools() == {"B": "CGNAT pool"} and sh.dyn_users()
      == {"bob": "account"} and sh.dyn_snmp_users()
      == {"other": "SNMPv3 user"},
      "outside configuration mode the running configuration answers")
ok("three names, one label each")

# ------------------------------------------------------------ the batch

print("\n=== Shell.feed_batch: every refusal recorded, commit's own not, "
      "and the batch runs to its end ===")
# Mutation shown red: record commit's refusal too.
sh = shell()
rv, text = driven(lambda: sh.feed_batch(["clear", "commit", "clear"]))
check(sh.batch_refused == ["clear", "clear"],
      f"the two statements refused are recorded, the whole batch ran: "
      f"{sh.batch_refused}")
check(all("commit" not in c for c in sh.batch_refused) and sh.failed == 3,
      f"commit's own refusal counts in `failed` and is not a statement "
      f"the batch asked for: failed {sh.failed}")
check(rv == 1, "the exit status is failure")
sh = shell()
rv, text = driven(lambda: sh.feed_batch(["", "exit"]))
check(rv == 0 and sh.batch_refused == [],
      f"nothing refused is exit 0 and an empty record: {sh.batch_refused}")
ok("the rule is on the commit, and the record is what it reads")


# =====================================================================
# THE SHELL'S READERS (#390, batch G): every Frr, Rollback and Shell
# method that prints or answers and applies nothing to the dataplane,
# FRR or the box, each called directly on a Shell built over stubs.
#
# The stubs are the seams the product declares: `dp` answers vppctl
# commands from a table, `api` answers dumps and calls from a table,
# `frr` answers vtysh from a table, and the files a reader opens are
# under this suite's own directory, through the class attributes that
# exist for exactly that. `running_config` is stubbed per box, because
# the product's reads the accounts and the ssh drop-in off the box.
#
# A method is called by name, never handed to a helper by reference:
# the reader in test_every_function.py counts only calls.
# =====================================================================
import contextlib
import io
import json
import time

import routes      # noqa: E402
import vppapi      # noqa: E402
from config import Config      # noqa: E402


class Api:
    """A dataplane API answering dumps and calls from two tables."""

    def __init__(self, have=None, rows=None, calls=None, refuse=(),
                 refuse_dump=()):
        self.have = dict(have or {})
        self.rows = dict(rows or {})
        self.answers = dict(calls or {})
        self.refuse = set(refuse)
        self.refuse_dump = set(refuse_dump)
        self.calls = []

    def available(self):
        return (vppapi.OK, "stub")

    def interface_index(self, name):
        return self.have.get(name)

    def call(self, message, args):
        self.calls.append((message, args))
        if message in self.refuse:
            return vppapi.Reply(vppapi.REFUSED, retval=-7, reason="refused")
        fields = self.answers.get(message)
        if callable(fields):
            fields = fields(args)
        return vppapi.Reply(vppapi.OK, fields=dict(fields or {}))

    def dump(self, message, args):
        if message in self.refuse_dump:
            return vppapi.Reply(vppapi.REFUSED, retval=-7, reason="refused")
        return vppapi.Reply(vppapi.OK,
                            fields={"rows": list(self.rows.get(message, []))})


class Dp(prismash.Vpp):
    """
    The CLI side of the dataplane, answering from a table: an exact
    command first, else the longest prefix. An answer is a string (rc 0)
    or an (rc, text) pair; a list is consumed one answer per call, which
    is how the utilisation sampler sees a counter move.
    """

    TRACE = False

    def __init__(self, answers=None):
        self.commands = []
        self.answers = dict(answers or {})

    def run(self, cmd):
        self.commands.append(cmd)
        if cmd in self.answers:
            answer = self.answers[cmd]
        else:
            keys = [k for k in self.answers if cmd.startswith(k + " ")]
            if not keys:
                return 0, ""
            answer = self.answers[max(keys, key=len)]
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        return (0, answer.rstrip()) if isinstance(answer, str) else answer

    def running(self):
        return 0, ""


def frr_stub(shows=None, available=True):
    """The product's own Frr with vtysh answered from a table."""
    answers = dict(shows or {})

    def show(self, cmd):
        if cmd in answers:
            answer = answers[cmd]
        else:
            keys = [k for k in answers if cmd.startswith(k + " ")]
            if not keys:
                return (1, "")
            answer = answers[max(keys, key=len)]
        return (0, answer) if isinstance(answer, str) else answer

    return type("F", (prismash.Frr,), {
        "available": lambda self: available,
        "show": show,
        "reload": lambda self, lines, check_only=False: (0, ""),
        # FRR's grammar is not this stub's to have: it accepts every
        # statement, and the refusals are driven with RecFrr (#431).
        "dryrun": lambda self, ctx, line: (prismash.Frr.DRYRUN_ACCEPTED, ""),
    })()


def shell(dp=None, api=None, frr=None, running=None):
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT, prismash.CAP_OPERATOR}
    sh._prompt_name = prismash.HOSTNAME_DEFAULT       # not this machine's (#526)
    sh.dp = dp or Dp()
    sh.api = api or Api()
    sh.frr = frr or frr_stub()
    if running is not None:
        sh.running_config = lambda: running
    return sh


@contextlib.contextmanager
def spoken():
    """What the VTY printed inside the block, read with .getvalue()."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        yield out


def rows_of(text):
    return text.splitlines()


# The dataplane's own screens, as captured in test_routes.py, so a reader
# here parses what a box prints and not a fixture that agrees with it.
IFACES = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)
pg0                               1      up          9000/0/0/0
                                                   rx packets   5
pg1                               2     down         1500/0/0/0
loop0                             3      up          9000/0/0/0
local0                            0     down          0/0/0/0
"""
ADDR = """pg0 (up):
  L3 200.150.0.1/24
  L3 2001:db8::1/64
pg1 (dn):
local0 (dn):
"""
LCP = """Linux-CP Interface Pairs:
itf-pair: [0] host-veth-vpp tap4096 vppwan0 5 type tap
itf-pair: [1] host-veth-mgmt tap4097 vppmgmt0 6 type tap
"""
HW = """              Name                Idx   Link  Hardware
ens19                              1     up   ens19
  Link speed: 10 Gbps
    pci: device 1af4:1041 subsystem 1af4:1100 address 0000:00:13.00 numa 0
ens20                              2     up   ens20
    pci: device 1af4:1041 subsystem 1af4:1100 address 0000:00:14.00 numa 0
TenGigabitEthernetd8/0/0           3     up   TenGigabitEthernetd8/0/0
    pci: device 8086:1572 subsystem 8086:0000 address 0000:d8:00.01 numa 1
tap0                               4     up   tap0
  Link speed: unknown
local0                             0    down  local0
"""
FIB = """ipv4-VRF:0, fib_index:0, flow hash:[src dst sport dport proto flowlabel ] epoch:0 flags:none locks:[adjacency:1, default-route:1, ]
0.0.0.0/0
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:1 buckets:1 uRPF:14 to:[0:0]]
    [0] [@5]: ipv4 via 200.150.0.2 pg0: mtu:9000 next:3 flags:[] 02000000000202fec40a847f0800
10.0.0.0/8
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:14 buckets:1 uRPF:14 to:[0:0]]
    [0] [@5]: ipv4 via 200.150.0.2 pg0: mtu:9000 next:3 flags:[] 02000000000202fec40a847f0800
200.150.0.0/24
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:10 buckets:1 uRPF:9 to:[0:0]]
    [0] [@4]: ipv4-glean: [src:200.150.0.0/24] pg0: mtu:9000 next:1 flags:[] ffffffffffff02fec40a847f0806
200.150.0.1/32
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:9 buckets:1 uRPF:8 to:[0:0]]
    [0] [@12]: dpo-receive: 200.150.0.1 on pg0
200.150.0.2/32
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:13 buckets:1 uRPF:13 to:[0:0]]
    [0] [@5]: ipv4 via 200.150.0.2 pg0: mtu:9000 next:3 flags:[] 02000000000202fec40a847f0800
224.0.0.0/4
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:4 buckets:1 uRPF:3 to:[0:0]]
    [0] [@0]: dpo-drop ip4
"""
RIB = json.dumps({
    "0.0.0.0/0": [{"protocol": "static", "selected": True,
                   "nexthops": [{"ip": "200.150.0.2",
                                 "interfaceName": "pg0"}]}],
    "198.19.0.0/24": [{"protocol": "isis", "selected": True,
                       "nexthops": [{"ip": "198.19.0.2",
                                     "interfaceName": "pg1"}]}],
    "10.9.0.0/16": [{"protocol": "ospf", "selected": True,
                     "nexthops": [{"ip": "200.150.0.2",
                                   "interfaceName": "pg0"}]}],
})
SW_ROWS = [
    {"interface_name": "pg0", "sw_if_index": 1, "tag": " uplink ",
     "l2_address": "02:FE:AA:BB:CC:DD", "sub_number_of_tags": 0},
    {"interface_name": "pg0.100", "sw_if_index": 4, "tag": "",
     "l2_address": "02:fe:aa:bb:cc:dd", "sub_number_of_tags": 1,
     "sub_id": 100, "sub_outer_vlan_id": 100, "sub_inner_vlan_id": 0},
    {"interface_name": "pg0.200", "sw_if_index": 5, "tag": "",
     "l2_address": "02:fe:aa:bb:cc:dd", "sub_number_of_tags": 2,
     "sub_id": 200, "sub_outer_vlan_id": 200, "sub_inner_vlan_id": 20},
    {"interface_name": "local0", "sw_if_index": 0, "tag": "",
     "l2_address": "00:00:00:00:00:00", "sub_number_of_tags": 0},
]

# ------------------------------------------------------------------ Frr

print("\n=== Frr._exec and Frr.show: vtysh's two streams, one command ===")
# Mutation shown red (_exec): return p.stdout alone, dropping stderr.
# Mutation shown red (show): ["-c"] + cmd.split(), one -c per word.
vtysh = script("vtysh", "#!/bin/sh\n"
               "for a in \"$@\"; do [ \"$a\" = -c ] && continue; "
               "echo \"ran: $a\"; done\n"
               "case \"$*\" in *fail*) echo boom >&2; exit 3;; esac\n")
saved = prismash.VTYSH
try:
    prismash.VTYSH = vtysh
    f = prismash.Frr()
    check(f.show("show version") == (0, "ran: show version"),
          f"one -c carrying the whole command: {f.show('show version')!r}")
    rc, out = f._exec(["-c", "fail me"])
    check(rc == 3 and out == "ran: fail me\nboom",
          f"vtysh's exit code, and stdout then stderr: {(rc, out)!r}")
    prismash.VTYSH = os.path.join(_root, "no-vtysh")
    rc, out = f._exec(["-c", "show version"])
    check(rc == 1 and out.endswith("not found"),
          f"a vtysh that is not there is rc 1 and says so: {(rc, out)!r}")
finally:
    prismash.VTYSH = saved
ok("stdout and stderr together, the exit code as vtysh gave it")

print("\n=== Frr.daemons: not answering is None, not the empty set ===")
# Mutation shown red: `return set()` where FRR did not answer.
f = frr_stub({"show daemons": "zebra bgpd ospfd"})
check(f.daemons() == {"zebra", "bgpd", "ospfd"},
      f"the words of `show daemons` as a set: {f.daemons()!r}")
check(frr_stub({}).daemons() is None,
      "vtysh refusing the command is None")
check(frr_stub({"show daemons": "  "}).daemons() is None,
      "an answer with no words on it is None too")
ok("None when FRR did not say, a set when it did")

print("\n=== Frr.running: the statements, without vtysh's framing ===")
# Mutation shown red: drop "frr version" from the lines skipped.
f = frr_stub({"show running-config": (
    "Building configuration...\n\nCurrent configuration:\n!\n"
    "frr version 10.7.1\nfrr defaults traditional\nhostname box\n!\n"
    "router bgp 65000\n neighbor 10.0.0.2 remote-as 65001   \n!\nend\n")})
check(f.running() == ["!", "!", "router bgp 65000",
                      " neighbor 10.0.0.2 remote-as 65001", "!"],
      f"framing out, indentation kept, trailing blanks off: {f.running()!r}")
check(frr_stub({}).running() == [],
      "FRR not answering is no statements")
ok("the configuration's own lines and nothing vtysh adds")

print("\n=== Frr.diagnostics: what the daemon said, not the tool ===")
# Mutation shown red: the chatty pattern also swallowing WARNING.
said = prismash.Frr.diagnostics(
    "2026-09-17 10:00:01,123 INFO: Called with: reload\n"
    "Waiting for children to finish\n"
    "['router bgp 65000',\n"
    "' exit ']\n"
    "The route-map 'PL-X' does not exist.\n"
    "2026-09-17 10:00:02,000 WARNING: the route-map is empty\n"
    "done\n")
check(said == ["The route-map 'PL-X' does not exist.",
               "2026-09-17 10:00:02,000 WARNING: the route-map is empty"],
      f"FRR's message and the tool's WARNING kept, its narration "
      f"dropped: {said!r}")
check(prismash.Frr.diagnostics(None) == [], "no output is nothing said")
ok("INFO, progress and the echoed command list are noise; the rest is kept")

print("\n=== Frr._summarise: the reason, never the footer alone ===")
# Mutation shown red: `useful = keep`, so the footer counts as a reason.
why = prismash.Frr._summarise("% Configuration failed.\n"
                            "The route-map 'PL-X' does not exist.\n")
check(why.endswith("The route-map 'PL-X' does not exist.")
      and why != "% Configuration failed.",
      f"a footer alone falls through to what the daemon said: {why!r}")
why = prismash.Frr._summarise("blah\nExiting: vtysh refused line 5\n")
check(why == "Exiting: vtysh refused line 5",
      f"a line that carries the reason is the summary: {why!r}")
check(prismash.Frr._summarise("only this") == "only this",
      "with nothing recognised, the last line")
check(prismash.Frr._summarise("") == "", "and nothing from nothing")
ok("prefixed lines first, the daemon's own words second, the tail last")

# ------------------------------------------------------------- Rollback

print("\n=== Rollback._file: revision n is <dir>/<n>.conf ===")
# Mutation shown red: f"{n}" with no suffix.
rb = prismash.Rollback(path=os.path.join(_root, "revs"))
check(rb._file(3) == os.path.join(_root, "revs", "3.conf"),
      f"numbered and suffixed: {rb._file(3)!r}")
ok("the file a revision number names")

# ---------------------------------------------------------- the prompt

print("\n=== Shell.where: the subject being edited, not only its kind ===")
# Mutation shown red: "cgnat pool" without the name.
sh = shell()
sh.mode = "pool"
sh.pool = types.SimpleNamespace(name="residential")
check(sh.where() == "nat / cgnat / pool residential",
      f"the pool by name: {sh.where()!r}")
sh.mode, sh.user = "user", types.SimpleNamespace(name="noc")
check(sh.where() == "user noc", f"the account by name: {sh.where()!r}")
sh.mode, sh.snmp_user = "snmp-user", types.SimpleNamespace(name="mon")
check(sh.where() == "snmp user mon", f"the SNMP user: {sh.where()!r}")
sh.mode, sh.frr_ctx = "config", ["router bgp 65000",
                                 "address-family ipv4 unicast"]
check(sh.where() == "router bgp 65000 / address-family ipv4 unicast",
      f"nested FRR blocks, outermost first: {sh.where()!r}")
sh.frr_ctx = []
check(sh.where() == "", "the top of config mode is nowhere in particular")
ok("names the block AND which one")

print("\n=== Shell.err_prose: one refusal, the verdict then the prose ===")
# Mutation shown red: `self.err(line)` for every row, counting each.
sh = shell()
with spoken() as o:
    sh.err_prose("the verdict", "a reason long enough to be folded by the "
                 "console, which is why it goes through print_prose and "
                 "not through err", "second paragraph")
text = rows_of(o.getvalue())
check(text[0] == "error: the verdict", f"the verdict first: {text[0]!r}")
check(all(r.startswith("    ") for r in text[1:]) and len(text) >= 3,
      f"the prose under it, indented and folded: {text[1:]!r}")
check(sh.failed == 1, f"one refusal however many rows: failed={sh.failed}")
ok("err for the headline, print_prose for the rest, counted once")

print("\n=== Shell.say_version_split: only when the two builds differ ===")
# Mutation shown red: drop the `if not version_split(...): return`.
PLUGINS = ("     Plugin                                   Version         "
           "Description\n  8. bpa_plugin.so                            "
           "{}  CGNAT with allocated port blocks\n")
sh = shell(dp=Dp({"show plugins": PLUGINS.format("0.24.0 (abc1234)")}))
with spoken() as o:
    sh.say_version_split()
check("    the dataplane is running 0.24.0 (abc1234)" in rows_of(o.getvalue()),
      f"a stamp of 0.99.7 against a dataplane at 0.24.0 says so, on a row "
      f"of its own: {o.getvalue()!r}")
sh = shell(dp=Dp({"show plugins": PLUGINS.format("0.99.7")}))
with spoken() as o:
    sh.say_version_split()
check(o.getvalue() == "", f"the same build says nothing: {o.getvalue()!r}")
sh = shell(dp=Dp({}))
with spoken() as o:
    sh.say_version_split()
check(o.getvalue() == "",
      f"a dataplane that never spoke says nothing: {o.getvalue()!r}")
ok("the sentence under a refusal exists only on a box mid-upgrade")

# ------------------------------------------------- the device readers

print("\n=== Shell.device_lcp_taps: the TAP of every pair, from show lcp ===")
# Mutation shown red: every name on the pair line, not only the TAP.
sh = shell(dp=Dp({"show lcp": LCP}))
check(sh.device_lcp_taps() == {"tap4096", "tap4097"},
      f"the TAPs and nothing else on the line: {sh.device_lcp_taps()!r}")
sh = shell(dp=Dp({"show lcp": (1, "unknown input")}))
check(sh.device_lcp_taps() == set(), "cannot be asked is no TAPs")
ok("on the line AND shaped like a linux_cp TAP")

print("\n=== Shell.fib_table_names: the dump's rows, mapped ===")
# Mutation shown red: `routes.table_names([])`, ignoring the rows.
api = Api(rows={"ip_table_dump": [
    {"table": {"name": "vrf10", "table_id": 10, "is_ip6": False}},
    {"table": {"name": "vrf10", "table_id": 10, "is_ip6": True}}]})
sh = shell(api=api)
check(sh.fib_table_names() == {"vrf10": 10},
      f"the named IPv4 table: {sh.fib_table_names()!r}")
sh = shell(api=Api(refuse_dump={"ip_table_dump"}))
check(sh.fib_table_names() == {}, "a refused dump is an empty map")
ok("{name: id} from ip_table_dump, {} when it cannot be asked")

print("\n=== Shell.device_descriptions: the tag, stripped, when set ===")
# Mutation shown red: the tag kept as it came, spaces and all.
sh = shell(api=Api(rows={"sw_interface_dump": SW_ROWS}))
check(sh.device_descriptions() == {"pg0": "uplink"},
      f"only the tagged interface, its tag trimmed: "
      f"{sh.device_descriptions()!r}")
sh = shell(api=Api(refuse_dump={"sw_interface_dump"}))
check(sh.device_descriptions() == {}, "a refused dump is no descriptions")
ok("read back from the dataplane's own tags")

print("\n=== Shell.device_vlans: tagged sub-interfaces only ===")
# Mutation shown red: drop the `sub_number_of_tags` test.
sh = shell(api=Api(rows={"sw_interface_dump": SW_ROWS}))
check(sh.device_vlans() == {"pg0.100": {"outer": 100, "inner": None},
                            "pg0.200": {"outer": 200, "inner": 20}},
      f"outer and inner, a missing inner as None: {sh.device_vlans()!r}")
sh = shell(api=Api(refuse_dump={"sw_interface_dump"}))
check(sh.device_vlans() == {}, "a refused dump is no sub-interfaces")
ok("a sub-interface with no tag is not one this VTY models")

print("\n=== Shell.device_macs: every real MAC, lower case ===")
# Mutation shown red: the all-zero MAC reported as one.
sh = shell(api=Api(rows={"sw_interface_dump": SW_ROWS}))
macs = sh.device_macs()
check(macs.get("pg0") == "02:fe:aa:bb:cc:dd",
      f"lower-cased as the box will print it back: {macs!r}")
check("local0" not in macs, f"00:00:00:00:00:00 is no address: {macs!r}")
sh = shell(api=Api(refuse_dump={"sw_interface_dump"}))
check(sh.device_macs() == {}, "a refused dump is no MACs")
ok("the l2_address of each interface that has one")

print("\n=== Shell.device_vrfs: the non-default table of each interface ===")
# Mutation shown red: drop `and r.fields.get("vrf_id")`, filing table 0.
api = Api(have={"pg0": 1, "pg1": 2},
          calls={"sw_interface_get_table":
                 lambda a: {"vrf_id": {1: 10, 2: 0}[a["sw_if_index"]]}})
sh = shell(dp=Dp({"show interface": IFACES}), api=api)
check(sh.device_vrfs() == {"pg0": 10},
      f"pg0 in table 10, pg1 in the default left out: {sh.device_vrfs()!r}")
check(all(a["sw_if_index"] != 0 for _, a in api.calls),
      "local0 was not asked")
sh = shell(dp=Dp({"show interface": (1, "")}), api=api)
check(sh.device_vrfs() == {}, "no interface list is no tables")
ok("asked per interface, table 0 is the absence of a statement")

print("\n=== Shell.device_addresses: per interface, only those with one ===")
# Mutation shown red: drop `if info["addresses"]`, listing pg1 as [].
sh = shell(dp=Dp({"show interface": IFACES, "show interface addr": ADDR}))
check(sh.device_addresses() == {"pg0": ["200.150.0.1/24", "2001:db8::1/64"]},
      f"both families, no entry for a bare port: {sh.device_addresses()!r}")
sh = shell(dp=Dp({"show interface": (1, "")}))
check(sh.device_addresses() == {}, "cannot be asked is no addresses")
ok("from show interface addr, keyed by the interface")

print("\n=== Shell.device_ra_enabled: send_radv on, cease_radv off ===")
# Mutation shown red: drop `and not row.get("cease_radv")`.
api = Api(have={"pg0": 1, "pg1": 2},
          rows={"sw_interface_ip6nd_ra_dump": [
              {"sw_if_index": 1, "send_radv": True, "cease_radv": False},
              {"sw_if_index": 2, "send_radv": True, "cease_radv": True},
              {"sw_if_index": 3, "send_radv": False}]})
sh = shell(dp=Dp({"show interface": IFACES}), api=api)
check(sh.device_ra_enabled() == {"pg0"},
      f"advertising and not ceasing: {sh.device_ra_enabled()!r}")
sh = shell(dp=Dp({"show interface": IFACES}),
           api=Api(refuse_dump={"sw_interface_ip6nd_ra_dump"}))
check(sh.device_ra_enabled() == set(), "a refused dump is nothing on")
ok("read from the dump, because the text says the same either way")

print("\n=== Shell.dataplane_names: every name show interface lists ===")
# Mutation shown red: local0 left out.
sh = shell(dp=Dp({"show interface": IFACES}))
check(sh.dataplane_names() == {"pg0", "pg1", "loop0", "local0"},
      f"all of them, local0 included: {sh.dataplane_names()!r}")
sh = shell(dp=Dp({"show interface": (1, "")}))
check(sh.dataplane_names() == set(), "cannot be asked is the empty set")
ok("the list a pair's members are placed against")

print("\n=== Shell.device_admin_down: down, and not local0 ===")
# Mutation shown red: drop `and name != "local0"`.
sh = shell(dp=Dp({"show interface": IFACES}))
check(sh.device_admin_down() == {"pg1"},
      f"pg1 is shut, local0 is always down and not shut: "
      f"{sh.device_admin_down()!r}")
sh = shell(dp=Dp({"show interface": (1, "")}))
check(sh.device_admin_down() == set(), "cannot be asked is nothing down")
ok("an admin state read off the table, the null interface excepted")

print("\n=== a sub-interface down because its parent is (#407) ===")
# Mutation shown red: the two readings made one again, either way round. With
# the filter in the raw one, nothing ever brings the child up - the delta is of
# two configurations and neither holds it. Without the filter in the
# configuration one, the box writes a `shutdown` nobody typed, the candidate
# says the interface is meant to be down, and the parent coming up leaves the
# child behind for a reason nothing on the screen explains.
DOWN_PAIR = (IFACES
             + "pg1.300                           5     down          0/0/0/0\n"
             + "pg0.100                           6     down          0/0/0/0\n")
sh = shell(dp=Dp({"show interface": DOWN_PAIR}))
check(sh.device_admin_down_raw() == {"pg1", "pg1.300", "pg0.100"},
      f"the raw reading is every interface the device has down: "
      f"{sh.device_admin_down_raw()!r}")
check(sh.device_admin_down() == {"pg1", "pg0.100"},
      f"and the configuration one leaves out the child of a down parent, "
      f"while keeping the child of an UP one: {sh.device_admin_down()!r}")
check("pg1" in sh.device_admin_down(),
      "the parent's own shutdown is still a statement somebody made")
sh = shell(dp=Dp({"show interface": (1, "")}))
check(sh.device_admin_down_raw() == set(),
      "and a dataplane that cannot be asked is nothing down, in both")
ok("two readings of one table, and the difference between them")

print("\n=== Shell.device_urpf: the interfaces that HAVE it, by mode ===")
# Mutation shown red: "off" accepted as a mode.
sh = shell(dp=Dp({"show bpa urpf": "interface mode\npg0 strict\npg1 loose\n"
                                   "pg2 off\n"}))
check(sh.device_urpf() == {"pg0": "strict", "pg1": "loose"},
      f"strict and loose, off is absence: {sh.device_urpf()!r}")
ok("a two-word row whose second word is a mode")

print("\n=== Shell.device_mtus: the L3 MTU of every interface but local0 ===")
# Mutation shown red: split("/")[1], the IP4 MTU, which is 0 here.
sh = shell(dp=Dp({"show interface": IFACES}))
check(sh.device_mtus() == {"pg0": 9000, "pg1": 1500, "loop0": 9000},
      f"the first of the four, counters and local0 skipped: "
      f"{sh.device_mtus()!r}")
ok("every interface, so a rollback can put an MTU back")

print("\n=== Shell.vpp: prints the answer, counts a refusal ===")
# Mutation shown red: `if rc: self.failed += 1` dropped.
sh = shell(dp=Dp({"show bpa pool": "pool residential\n",
                  "bpa nonsense": (1, "unknown input")}))
with spoken() as o:
    rc = sh.vpp("show bpa pool")
check(rc == 0 and o.getvalue() == "pool residential\n",
      f"the dataplane's text, rc 0: {(rc, o.getvalue())!r}")
with spoken() as o:
    rc = sh.vpp("bpa nonsense")
check(rc == 1 and "unknown input" in o.getvalue() and sh.failed == 1,
      f"a refusal is printed, returned and counted: "
      f"{(rc, o.getvalue(), sh.failed)!r}")
with spoken() as o:
    sh.vpp("show bpa pool", quiet=True)
check(o.getvalue() == "", "quiet prints nothing")
ok("the CLI seam: text out, failures counted")

print("\n=== Shell.osi_protos_enabled: a hex string, read as bytes ===")
# Mutation shown red: `[int(x) for x in got]` over the string.
sh = shell(api=Api(calls={"lcp_osi_proto_get": {"osi_protos": "0x83"}}))
check(sh.osi_protos_enabled() == [0x83],
      f"'0x83' is one protocol: {sh.osi_protos_enabled()!r}")
sh = shell(api=Api(calls={"lcp_osi_proto_get": {"osi_protos": "0x8381"}}))
check(sh.osi_protos_enabled() == [0x83, 0x81],
      f"two bytes are two protocols: {sh.osi_protos_enabled()!r}")
sh = shell(api=Api(calls={"lcp_osi_proto_get": {"osi_protos": "0x"}}))
check(sh.osi_protos_enabled() == [], "bare 0x is none, and is an answer")
sh = shell(api=Api(refuse={"lcp_osi_proto_get"}))
check(sh.osi_protos_enabled() is None, "could not ask is None, not none")
ok("read from the box, two hex digits per protocol")

print("\n=== Shell.linux_still_has_card: by MAC, and cannot look is yes ===")
# Mutation shown red: an unreadable /sys/class/net answering False.
net = os.path.join(_root, "net1")
os.makedirs(os.path.join(net, "ens19"))
os.makedirs(os.path.join(net, "lo"))
with open(os.path.join(net, "ens19", "address"), "w") as _f:
    _f.write("52:54:00:12:34:56\n")
sh = shell()
sh.SYS_CLASS_NET = net
check(sh.linux_still_has_card("52:54:00:12:34:56") is True,
      "the card with that MAC is still Linux's")
check(sh.linux_still_has_card("52:54:00:12:34:57") is False,
      "no netdev carries the other MAC")
check(sh.linux_still_has_card("52:54:00:12:34:56".upper()) is True,
      "case does not matter")
sh.SYS_CLASS_NET = os.path.join(_root, "no-such-net")
check(sh.linux_still_has_card("52:54:00:12:34:56") is True,
      "a box this cannot look at has not handed anything over")
ok("the address file of every netdev, matched on the MAC")

print("\n=== Shell.device_linux_names: dataplane name -> Linux name ===")
# Mutation shown red: w[3], the TAP, as the Linux name.
sh = shell(dp=Dp({"show lcp": LCP}))
check(sh.device_linux_names() == {"host-veth-vpp": "vppwan0",
                                  "host-veth-mgmt": "vppmgmt0"},
      f"the fifth word of each pair line: {sh.device_linux_names()!r}")
sh = shell(dp=Dp({"show lcp": (1, "")}))
check(sh.device_linux_names() == {}, "cannot be asked is no pairs")
ok("read back from show lcp, never remembered")

print("\n=== Shell.dataplane_cards: the PCI cards, or {} unasked ===")
# Mutation shown red: drop the rc check, parsing a refusal's text.
sh = shell(dp=Dp({"show hardware-interfaces": HW}))
cards = sh.dataplane_cards()
check(set(cards) == {"ens19", "ens20", "TenGigabitEthernetd8/0/0"}
      and cards["ens19"] == (0, 0, 0x13, 0),
      f"the three with a pci row, by address: {cards!r}")
sh = shell(dp=Dp({"show hardware-interfaces": (1, HW)}))
check(sh.dataplane_cards() == {},
      "a refused command holds no cards, whatever text came with it")
ok("what show hardware-interfaces prints a pci row under")

print("\n=== Shell.pair_cards: a card with a block defaults to its name ===")
# Mutation shown red: drop `card in blocks`, defaulting every card.
sh = shell(dp=Dp({"show hardware-interfaces": HW}))
target = Config()
target.mtus = {"ens19": 1500, "TenGigabitEthernetd8/0/0": 9000}
target.routing = ["interface ens20", " no shutdown"]
sh.pair_cards(target)
check(target.linux_names == {"ens19": "ens19", "ens20": "ens20"},
      f"a device block and a routing stanza both count, VPP's own name "
      f"gets none: {target.linux_names!r}")
target = Config()
target.mtus = {"ens19": 1500}
target.linux_names = {"ens19": "wan"}
sh.pair_cards(target)
check(target.linux_names == {"ens19": "wan"},
      f"an override stays: {target.linux_names!r}")
target = Config()
sh.pair_cards(target)
check(target.linux_names == {},
      f"a card nobody opened a block for is not paired: "
      f"{target.linux_names!r}")
ok("written into the candidate, for the cards a block names")

print("\n=== Shell.defaulted_cards: the cards Linux can name ===")
# Mutation shown red: drop the linux_ifname_error filter.
sh = shell(dp=Dp({"show hardware-interfaces": HW}))
check(sh.defaulted_cards() == {"ens19", "ens20"},
      f"a DPDK-spelled name is not one Linux takes: "
      f"{sh.defaulted_cards()!r}")
ok("one owner of the condition the prompt asks too")

print("\n=== Shell.card_renames: an old DPDK name to the card's own ===")
# Mutation shown red: `by_addr[key][0]` with no `len(...) == 1` test.
sh = shell(dp=Dp({"show hardware-interfaces": HW}))
text = ("interface GigabitEthernet0/13/0\n"
        " passive-interface GigabitEthernet0/13/0.100\n"
        "interface GigabitEthernet0/15/0\n"
        "interface TenGigabitEthernetd8/0/0\n")
check(sh.card_renames(text) == {"GigabitEthernet0/13/0": "ens19"},
      f"0/13/0 is ens19's address; 0/15/0 is nobody's; a name the box "
      f"still has is left alone: {sh.card_renames(text)!r}")
TWO = HW + ("ens21                              5     up   ens21\n"
            "    pci: device 1af4:1041 subsystem 1af4:1100 "
            "address 0001:00:13.00 numa 0\n")
sh = shell(dp=Dp({"show hardware-interfaces": TWO}))
check(sh.card_renames(text) == {},
      f"the same bus/slot/function in two domains is not renamed: "
      f"{sh.card_renames(text)!r}")
sh = shell(dp=Dp({"show hardware-interfaces": (1, "")}))
check(sh.card_renames(text) == {}, "no cards known is no renames")
ok("matched by address, refused when ambiguous")

print("\n=== Shell.renamed_text: whole words, a sub-interface keeps its tag ===")
# Mutation shown red: drop the lookbehind, so Ten...0/13/0 is renamed too.
out = prismash.Shell.renamed_text(
    "interface GigabitEthernet0/13/0.100\ninterface TenGigabitEthernet0/13/0\n"
    "interface GigabitEthernet0/13/0\n", {"GigabitEthernet0/13/0": "ens19"})
check(out == "interface ens19.100\ninterface TenGigabitEthernet0/13/0\n"
             "interface ens19\n", f"the tag follows, a longer name does "
      f"not lose its tail: {out!r}")
ok("a whole-word rename on the text")

print("\n=== Shell.device_loopbacks: loop<n> off show interface ===")
# Mutation shown red: every name, not only the loopbacks.
sh = shell(dp=Dp({"show interface": IFACES}))
check(sh.device_loopbacks() == {"loop0"},
      f"loop0 and not pg0: {sh.device_loopbacks()!r}")
sh = shell(dp=Dp({"show interface": (1, "")}))
check(sh.device_loopbacks() == set(), "cannot be asked is none")
ok("by VPP's own spelling of a loopback")

# ------------------------------------------------------ pools and commit

print("\n=== Shell.relay_pool_warnings: the show's rows, for these pools ===")
# Mutation shown red: `relay = True`, whatever pool the warning is under.
POOLS = ("pool residential\n  inside 100.64.0.0/22\n"
         "warning: 4 workers share 16 blocks\n  so each worker gets 4\n"
         "pool business\nwarning: business is odd\n")
sh = shell(dp=Dp({"show bpa pool": POOLS}))
with spoken() as o:
    sh.relay_pool_warnings({"residential"})
check(rows_of(o.getvalue()) == ["warning: 4 workers share 16 blocks",
                                "  so each worker gets 4"],
      f"every row under the named pool's warning, none of the other "
      f"pool's: {o.getvalue()!r}")
with spoken() as o:
    sh.relay_pool_warnings(set())
check(o.getvalue() == "", "no pools named, nothing relayed")
ok("read by the show's own structure, printed as the dataplane said it")

print("\n=== Shell.report_remap: said by check, and only when there is one ===")
# Mutation shown red: drop `if not self.remap: return`.
sh = shell()
with spoken() as o:
    sh.report_remap()
check(o.getvalue() == "", f"nothing to re-map, nothing said: {o.getvalue()!r}")
sh.remap = ["remap: 12 subscribers on residential change mapping"]
with spoken() as o:
    sh.report_remap()
text = o.getvalue()
check("RE-MAPS" in text and "12 subscribers on residential" in text
      and "remap:" not in text and "'commit remap'" in text,
      f"the warning, the plugin's sentence without its prefix, and the "
      f"way through: {text!r}")
ok("the warning `commit check` promised")

print("\n=== Shell.say_system_cost: the two fixed rows and the two read ===")
# Mutation shown red: the call to say_unconfirmed dropped.
sh = shell()
called = []
sh.say_unsaved = lambda: called.append("unsaved")
sh.say_unconfirmed = lambda: called.append("unconfirmed")
with spoken() as o:
    sh.say_system_cost()
text = o.getvalue()
check("every session on this box ends" in text and "stop-drain" in text,
      f"what every reboot costs: {text!r}")
check(called == ["unsaved", "unconfirmed"],
      f"and this box's two readings, in that order: {called!r}")
ok("true of every box, then true of this one")

print("\n=== Shell.after_boot: what a replay of the file leaves ===")
# Mutation shown red: `boot.dataplane_if = running.dataplane_if` always.
sh = shell()
running = Config()
running.users = {"noc": "an account"}
running.dataplane_if = "ens19"
running.mtus = {"pg0": 1500}
boot = sh.after_boot(Config(), running)
check(boot.users == running.users and boot.dataplane_if == "ens19"
      and boot.mtus == {},
      f"an empty file keeps the accounts and the card, loses the blocks: "
      f"users={boot.users!r} card={boot.dataplane_if!r} mtus={boot.mtus!r}")
saved = Config.parse("dataplane interface ens18")
boot = sh.after_boot(saved, running)
check(boot is saved and boot.dataplane_if == "ens18" and boot.users == {},
      f"a file that says something is the holder of all of it: "
      f"card={boot.dataplane_if!r} users={boot.users!r}")
ok("load()'s decisions, on Config objects")

print("\n=== Shell.say_unconfirmed: the deadline off the store ===")
# Mutation shown red: time.gmtime(0) in place of the deadline.
sh = shell()
sh.confirm = prismash.ConfirmPending(os.path.join(_root, "pending-g"))
with spoken() as o:
    sh.say_unconfirmed()
check(o.getvalue() == "", f"nothing armed, nothing said: {o.getvalue()!r}")
sh.confirm.arm(1789000000.0, "! cgnat\n", "10")
with spoken() as o:
    sh.say_unconfirmed()
text = rows_of(o.getvalue())
check(text[0] == "warning: the last commit has NOT been confirmed",
      f"the warning first: {text!r}")
check(any("2026-09-10T00:26:40Z" in r for r in text),
      f"with the deadline the file holds, as UTC: {text!r}")
sh.confirm.clear()
ok("read from the file the unit reverts from")

print("\n=== ConfigState: the configuration's state for SNMP (#524) ===")
_cs = prismash.ConfigState(os.path.join(_root, "state", "config"))
check(_cs.read() == {}, "nothing written is nothing read")
_cs.record(confirm_pending="yes", confirm_deadline=1790745000)
_cs.applied("amy", saved_differs="yes")
_cs.bump("rollbacks")
_cs.bump("rollbacks")
_d = _cs.read()
check(_d["confirm_pending"] == "yes" and _d["commits"] == "1"
      and _d["commit_by"] == "amy" and _d["rollbacks"] == "2"
      and prismash.is_number(_d["commit_at"]) and _d["saved_differs"] == "yes",
      f"recorded, kept, counted: {_d}")
check(oct(os.stat(_cs.path).st_mode & 0o777) == "0o644",
      "0644: the SNMP agent is not in the operators' group")
_cs.applied("bob")
check(_cs.read()["commits"] == "2" and _cs.read()["rollbacks"] == "2",
      "the next commit counts on and keeps the rest")
_ro = os.path.join(_root, "ro-state")
os.makedirs(_ro, exist_ok=True)
os.chmod(_ro, 0o500)
_bad = prismash.ConfigState(os.path.join(_ro, "config"))
_bad.record(commits=1)
_bad.applied("amy")
_bad.bump("rollbacks")
check(_bad.read() == {} or os.geteuid() == 0,
      "a store it cannot write is silence, not a failed commit")
os.chmod(_ro, 0o700)
ok("key value lines, rewritten whole, 0644, and never a refusal")

print("\n=== Shell.record_applied: who, when, and whether a reboot loses it ===")
_saved_path = prismash.CONFIG_PATH
prismash.CONFIG_PATH = os.path.join(_root, "saved-for-state.conf")
with open(prismash.CONFIG_PATH, "w") as _f:
    _f.write("ssh port 22\n")
sh = shell()
sh.state = prismash.ConfigState(os.path.join(_root, "state", "applied"))
sh.record_applied("amy", Config.parse("ssh port 22\n"))
check(sh.state.read()["saved_differs"] == "no"
      and sh.state.read()["commit_by"] == "amy",
      f"the saved file is what runs: {sh.state.read()}")
sh.record_applied("amy", Config.parse("ssh port 2222\n"))
check(sh.state.read()["saved_differs"] == "yes"
      and sh.state.read()["commits"] == "2",
      "a change nobody saved is one a reboot would lose")
os.remove(prismash.CONFIG_PATH)
sh.record_applied("amy", Config.parse("ssh port 22\n"))
check(sh.state.read()["saved_differs"] == "unknown",
      "no saved file to compare is not a claim either way")
prismash.CONFIG_PATH = _saved_path
# The commit writes it, and a rollback's commit counts as one.
sh = shell(running=Config.parse("ssh port 22\n"))
sh.state = prismash.ConfigState(os.path.join(_root, "state", "commit"))
sh.rb = prismash.Rollback(os.path.join(_root, "rb-state"))
sh.confirm = prismash.ConfirmPending(os.path.join(_root, "confirm-state"))
sh.apply = lambda target, check_only=False: True
sh.candidate = Config.parse("ssh port 2222\n")
sh.from_rollback = True
with spoken() as _o:
    sh.do_commit([])
_d = sh.state.read()
check(_d.get("commits") == "1" and _d.get("rollbacks") == "1"
      and not sh.from_rollback and _d.get("confirm_pending") == "yes"
      and prismash.is_number(_d.get("confirm_deadline", "")),
      f"a commit is recorded, the rollback it applied counted, and the "
      f"confirm window the SSH change opened is pending: {_d} "
      f"{_o.getvalue()[-300:]!r}")
sh.confirm.clear()
ok("the commit, the boot and the revert all say it through here")

print("\n=== Shell.pool_range: raises with the range, or nothing ===")
# Mutation shown red: `return why` in place of the raise.
sh = shell()
check(sh.pool_range("block-size", "64") is None, "64 is inside the range")
try:
    sh.pool_range("block-size", "abc")
    check(False, "a value that is not a number was accepted")
except ValueError as e:
    check("expected a number 64-32768" in str(e),
          f"the refusal names the range: {e!r}")
ok("a ValueError, because do_pool answers those with the operator's line")

print("\n=== Shell.show_or_pass: which reader each `show` reaches ===")
# Mutation shown red: the prefixed forms dropped, so `show cgnat
# subscriber <address>` is handed to FRR.
dp = Dp({"show bpa pool": "pool residential"})
frr = frr_stub({"show ip route": "K>* 0.0.0.0/0 via 10.0.0.1"})
sh = shell(dp=dp, frr=frr)
reached = []
sh.show_ha = lambda a: reached.append(("ha", a))
sh.show_interfaces = lambda a: reached.append(("interfaces", a))
sh.show_ospf = lambda a, bare_is_neighbors=True: reached.append(
    ("ospf", a, bare_is_neighbors))
sh.show_route = lambda a: reached.append(("route", a))
with spoken() as o:
    sh.show_or_pass("show cgnat pool")
    sh.show_or_pass("show cgnat subscriber 100.64.0.7")
    sh.show_or_pass("show cgnat ha peer")
    sh.show_or_pass("show interfaces terse pg0")
    sh.show_or_pass("show ip ospf")
    sh.show_or_pass("show route 10.0.0.0")
    sh.show_or_pass("show ip route")
check(dp.commands == ["show bpa pool", "show bpa subscriber 100.64.0.7"],
      f"cgnat is spelled bpa at the dataplane, the argument untouched: "
      f"{dp.commands!r}")
check(reached == [("ha", ["peer"]), ("interfaces", ["terse", "pg0"]),
                  ("ospf", [], False), ("route", ["10.0.0.0"])],
      f"each reader gets its words: {reached!r}")
check("K>* 0.0.0.0/0 via 10.0.0.1" in o.getvalue(),
      f"and what nothing here owns goes to FRR: {o.getvalue()!r}")
ok("the dispatch under `show`")

# ----------------------------------------------------- the interfaces

print("\n=== Shell.forwarding_count: what the rows above it are ===")
# Mutation shown red: `len(ifaces)`, counting the null interface - which is
# what the count did on every box until #306, and what it must not go back to
# for a screen that is ever handed the whole table.
n, note = prismash.Shell.forwarding_count({"pg0": {}, "local0": {}})
check((n, note) == (1, []),
      f"one of two forwards, and nothing is left to explain: {(n, note)!r}")
n, note = prismash.Shell.forwarding_count({"pg0": {}, "pg1": {}})
check((n, note) == (2, []), f"two cards read two: {(n, note)!r}")
check(prismash.Shell.forwarding_count({})[0] == 0,
      "and a box with nothing on it reads zero")
ok("the one holder of that count")

print("\n=== routes.operator_interfaces: what somebody configured ===")
# Mutation shown red: the TAP test dropped, which is the screen the maintainer
# read on his own box - one card counted twice, once as itself and once as the
# TAP this product created for it (#403).
_have = ["ens18", "ens18.100", "local0", "loop0", "tap4096", "tap4096.100"]
check(routes.operator_interfaces(_have) == ["ens18", "ens18.100", "loop0"],
      f"the card, its sub-interface and the loopback: "
      f"{routes.operator_interfaces(_have)!r}")
check(routes.operator_interfaces(["local0"]) == [],
      "a box that claimed no card configures nothing")
check(routes.operator_interfaces([]) == [], "and nothing is nothing")
# The ORDER is the caller's, because two of the three screens sort and one
# prints a count: a helper that sorted would decide a presentation question
# for callers that have already decided it.
check(routes.operator_interfaces(["pg1", "pg0"]) == ["pg1", "pg0"],
      "the order it was given, which is the caller's business")
ok("one decision, four screens")

print("\n=== Shell.link_state: a sub-interface's link is its parent's ===")
# Mutation shown red: drop the parent lookup.
hw = {"pg0": {"link": "up", "speed": None}}
check(prismash.Shell.link_state("pg0", hw) == "up", "the interface's own")
check(prismash.Shell.link_state("pg0.100", hw) == "up",
      "pg0.100 is not hardware; its link is pg0's")
check(prismash.Shell.link_state("pg9", hw) == "?",
      "an interface the dataplane reports no link for is '?'")
ok("the parent's link, or '?'")

IF3 = {"pg0": {"state": "up", "mtu": "9000",
               "addresses": ["200.150.0.1/24", "2001:db8::1/64"], "host": ""},
       "pg1": {"state": "up", "mtu": "1500", "addresses": [], "host": ""},
       "local0": {"state": "down", "mtu": "0", "addresses": [], "host": ""}}
HW3 = {"pg0": {"link": "down", "speed": None},
       "pg1": {"link": "up", "speed": None},
       "local0": {"link": "down", "speed": None}}

print("\n=== Shell.show_interfaces_terse: admin and link, and the fault ===")
# Mutation shown red: `link == "up"` in the mismatch test.
sh = shell()
with spoken() as o:
    sh.show_interfaces_terse(IF3, HW3)
text = rows_of(o.getvalue())
check(any(r.split() == ["pg0", "up", "down", "inet", "200.150.0.1/24"]
          for r in text)
      and any(r.split() == ["inet6", "2001:db8::1/64"] for r in text),
      f"the first address on the interface's row, the second under it: "
      f"{text!r}")
check(any(r.split() == ["pg1", "up", "up", "-", "-"] for r in text),
      f"a bare port shows dashes: {text!r}")
check("  2 interface(s) on the dataplane can forward" in text,
      f"the forwarding count: {text!r}")
# Called DIRECTLY here, with the whole table including `local0` - which is not
# how `show_interfaces` calls it since #403, and is the point of the check:
# this screen prints what it is handed and counts what can forward, and the
# note that used to explain the difference is gone with the row.
check(not any("listed above but not counted" in r for r in text),
      f"and nothing left explaining a disagreement: {text!r}")
check(any(r.startswith("  pg0: configured up, link down") for r in text)
      and not any(r.startswith("  pg1:") for r in text),
      f"up/down is the wire and is named; up/up is not: {text!r}")
with spoken() as o:
    sh.show_interfaces_terse({"pg0": IF3["pg0"]}, HW3, total=IF3)
check("  1 of 2 interface(s) that can forward are shown"
      in rows_of(o.getvalue()),
      f"a filtered listing says N of M, both forwarding counts: "
      f"{o.getvalue()!r}")
ok("two columns because they are two facts")

print("\n=== Shell.show_interfaces_description: described of countable ===")
# Mutation shown red: `len(ifaces)` as the denominator.
sh = shell(api=Api(rows={"sw_interface_dump": SW_ROWS}))
with spoken() as o:
    sh.show_interfaces_description(IF3, HW3)
text = rows_of(o.getvalue())
check(any(r.split() == ["pg0", "up", "down", "uplink"] for r in text),
      f"the tag beside the two states: {text!r}")
check("  1 of 2 interface(s) described" in text
      and any("set one with 'description <text>'" in r for r in text),
      f"local0 is not in the denominator, and the empty column is "
      f"answered: {text!r}")
ok("the description is the dataplane's tag, the count is of what can forward")

print("\n=== Shell.bars: block characters only where the console is UTF ===")
# Mutation shown red: BARS_UTF regardless of the encoding.
sh = shell()
saved_stdout = sys.stdout
try:
    sys.stdout = types.SimpleNamespace(encoding="UTF-8", write=lambda s: None)
    utf = sh.bars()
    sys.stdout = types.SimpleNamespace(encoding="ANSI_X3.4-1968",
                                       write=lambda s: None)
    ascii_ = sh.bars()
finally:
    sys.stdout = saved_stdout
check(utf == prismash.Shell.BARS_UTF and ascii_ == prismash.Shell.BARS_ASCII,
      f"UTF gets the blocks, a serial console in ASCII gets its own: "
      f"{(utf, ascii_)!r}")
ok("a graph a serial console can draw")

print("\n=== Shell.spark: a trickle is not silence ===")
# Mutation shown red: drop `level = max(1, level)`.
sh = shell()
sh.bars = lambda: prismash.Shell.BARS_ASCII
c = prismash.Shell.BARS_ASCII
check(sh.spark([0, 5, 10], 10) == c[0] + c[4] + c[8],
      f"zero, half, full: {sh.spark([0, 5, 10], 10)!r}")
check(sh.spark([0.01], 10) == c[1],
      f"traffic that rounds to nothing is drawn at the lowest level: "
      f"{sh.spark([0.01], 10)!r}")
check(sh.spark([3, 4], 0) == c[0] * 2, "no scale is a flat baseline")
ok("eight levels against `full`, the baseline only for a real zero")

print("\n=== Shell.rate: the multiple an operator would have said ===")
# Mutation shown red: `bits > div`, so exactly 1000 is 1000 bit/s.
rates = (prismash.Shell.rate(1.5e9), prismash.Shell.rate(675.56e6),
         prismash.Shell.rate(1000), prismash.Shell.rate(999))
check(rates == ("1.50 Gbit/s", "675.56 Mbit/s", "1.00 kbit/s", "999 bit/s"),
      f"{rates!r}")
ok("G, M, k, then bit/s")

print("\n=== Shell.show_interface_utilisation: a rate is a difference ===")
# Mutation shown red: `lost = now.get("drops", 0)`, the total since boot.
SAMPLE = ("              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)"
          "     Counter          Count\n"
          "pg0                               1      up          9000/0/0/0"
          "     rx bytes    {rx}\n"
          "                                                   drops    "
          "{drops}\n"
          "local0                            0     down          0/0/0/0\n")
dp = Dp({"show interface": [SAMPLE.format(rx=0, drops=5),
                            SAMPLE.format(rx=1000, drops=7)],
         "show hardware-interfaces brief": HW})
sh = shell(dp=dp)
with spoken() as o:
    sh.show_interface_utilisation(["interval", "0.1", "count", "1", "pg0"])
text = rows_of(o.getvalue())
check(text[0].startswith("  sampling every 0.1s, 1 time(s) - 0.1s in total"),
      f"how long it will take, said first: {text[0]!r}")
row = [r for r in text if r.split()[:1] == ["pg0"] and "dropped" not in r]
# 8000 bits over a measured 0.1s or a little more: under 80 kbit/s, and
# well over the 0 a total-since-boot reading would not be.
check(len(row) == 1 and row[0].split()[2] == "kbit/s"
      and 40 <= float(row[0].split()[1]) <= 80
      and not any(r.startswith("  local0") for r in text),
      f"pg0's rate from the two readings, local0 filtered out: {text!r}")
check("  pg0 dropped 2 packet(s) while this was sampling" in text,
      f"drops are the difference too, 7 - 5: {text!r}")
check(any("bars are scaled to" in r for r in text),
      f"no link speed for pg0, so the scale is the busiest sample: {text!r}")
sh = shell(dp=Dp({"show interface": SAMPLE.format(rx=0, drops=0)}))
with spoken() as o:
    sh.show_interface_utilisation(["interval"])
check(rows_of(o.getvalue()) == ["error: interval needs a number after it"],
      f"a word with no number: {o.getvalue()!r}")
with spoken() as o:
    sh.show_interface_utilisation(["count", "500"])
check(rows_of(o.getvalue()) == ["error: count must be between 1 and 120"],
      f"a count outside its range: {o.getvalue()!r}")
ok("two counters, the time between, the difference")

print("\n=== monitor interface: the live view (#519) ===")
_steps, _reset = prismash.interface_steps(
    {"pg0": {"rx bytes": 0, "tx bytes": 100, "rx packets": 0, "drops": 5,
             "rx-miss": 1, "rx-error": 2},
     "pg1": {"rx bytes": 500}},
    {"pg0": {"rx bytes": 1000, "tx bytes": 100, "rx packets": 10,
             "drops": 7, "rx-miss": 2, "rx-error": 5, "tx-error": 1},
     "pg1": {"rx bytes": 100},
     "loop9": {"rx bytes": 99999}}, 2.0)
check(_steps["pg0"]["rx"] == 4000 and _steps["pg0"]["tx"] == 0
      and _steps["pg0"]["rx_pps"] == 5 and _steps["pg0"]["drops"] == 2
      and _steps["pg0"]["lost"] == 3 and _steps["pg0"]["errors"] == 4,
      f"bits and packets over the measured time, counts as counts: {_steps}")
check("loop9" not in _steps,
      "an interface that appeared mid-sample has no step: its total is no rate")
check(_reset and _steps["pg1"]["rx"] == 0,
      "a counter that went backwards reads zero, and says it happened")
check(prismash.interface_steps({"a": {}}, {"a": {}}, 0) == ({}, False),
      "no time between the readings, no rate")
check([prismash.nice_scale(b) for b in (0, 1, 7, 800e3, 1.2e6, 1e9, 3e9)]
      == [0, 1, 10, 1000000, 2000000, 1000000000, 5000000000],
      "the axis tops at 1, 2 or 5 times a power of ten")
_c = prismash.chart_rows([None, 0, 1, 50, 100], 100, 2, 6, True)
check(_c == ["     █", "  ·▁██"],
      f"right-aligned, a zero is the baseline, a trickle is an eighth: {_c!r}")
_c = prismash.chart_rows([0, 100], 0, 2, 3, False)
check(_c == ["   ", " __"], f"no scale: every sample on the baseline: {_c!r}")
check(prismash.chart_rows([100], 100, 1, 1, False) == ["#"],
      "ASCII for a console that answers in ASCII")
check(prismash.monitor_fit("abcdef", 5) == "ab..." and
      prismash.monitor_fit("abc", 5) == "abc", "cut, and said to be cut")
check([prismash.monitor_key(k) for k in
       ("q", "\x03", "l", "n", " ", "\x1b[C", "\x1b[6~", "p", "\x1b[D", "x",
        "")]
      == ["quit", "quit", "scale", "next", "next", "next", "next", "prev",
          "prev", None, None], "the keys, arrows and pages included")

_s = {"rx": 800e3, "tx": 0, "rx_pps": 1000, "tx_pps": 0, "drops": 0,
      "lost": 1, "errors": 2}
_view = {"interval": 1, "names": ["pg0", "pg1"],
         "windows": {"pg0": [None, _s, _s], "pg1": []},
         "speeds": {"pg0": 10e9, "pg1": None}, "mode": "auto", "page": 0,
         "notes": [], "clock": "12:00:00"}
_l, _pages = prismash.render_monitor(_view, 79, 24, True)
check(_pages == 1 and len(_l) == 24 and _l[0].endswith("12:00:00"),
      f"a whole screen, the time at the right: {_l[0]!r}")
check(_l[1] == "pg0   scale 1.00 Mbit/s, auto"
      and _l[2].startswith("  1.00 Mbit/s┤")
      and _l[2].endswith("rx 800.00 kbit/s")
      and _l[3].endswith("1000 pps") and _l[5].endswith("errors 4")
      and _l[9].endswith("drops 2"),
      f"the scale stated, the axis labelled, the rate, pps, the window's "
      f"errors and drops: {_l[1:10]!r}")
check(_l[10] == "pg1   waiting for a second sample"
      and _l[11].endswith("rx --") and _l[12].endswith("-- pps")
      and _l[14].endswith("errors --"),
      f"no step yet is --, never 0: {_l[10:15]!r}")
_view["mode"] = "line"
_l, _ = prismash.render_monitor(_view, 79, 24, True)
check(_l[1] == "pg0   line rate 10.00 Gbit/s: rx 0.0% tx 0.0%"
      and _l[2].startswith(" 10.00 Gbit/s"),
      f"one key away: the share of the line rate, and its axis: {_l[1:3]!r}")
_view["windows"]["pg1"] = [dict(_s, rx=0)]
_l, _ = prismash.render_monitor(_view, 79, 24, True)
check(_l[10] == "pg1   no line rate known; scale 0 bit/s, auto"
      or _l[10] == "pg1   scale: nothing passed in this window",
      f"no line rate to be a share of, and nothing passed: {_l[10]!r}")
_view.update(mode="auto", names=[f"if{i}" for i in range(5)],
             windows={f"if{i}": [_s] for i in range(5)}, page=9,
             notes=["first note", "second note"])
_l, _pages = prismash.render_monitor(_view, 79, 24, False)
check(_pages == 3 and "page 3/3" in _l[0] and _l[1].startswith("if4")
      and _l[-2:] == ["first note", "second note"],
      f"paged, not cut; a page past the end is the last; two notes: "
      f"{_l[0]!r} {_l[-2:]!r}")
_l, _ = prismash.render_monitor(_view, 40, 10, True)
check(_l == ["this view needs 50 columns and 12 row...", "q leaves"],
      f"too small a terminal is said, not drawn: {_l!r}")

# The widest value on every axis, in both encodings (the issue's
# acceptance): a 400G port at 399.99 Gbit/s, 14.88 Mpps, a sub-interface's
# long name, counts in the billions, both notes.
_w = {"rx": 399.99e9, "tx": 399.99e9, "rx_pps": 14880952,
      "tx_pps": 14880952, "drops": 0, "lost": 999999999, "errors": 999999999}
for _utf in (True, False):
    for _mode in ("auto", "line"):
        _view = {"interval": 0.5, "names": ["TenGigabitEthernet3b/0/1.4094"],
                 "windows": {"TenGigabitEthernet3b/0/1.4094": [_w] * 900},
                 "speeds": {"TenGigabitEthernet3b/0/1.4094": 400e9},
                 "mode": _mode, "page": 0, "clock": "23:59:59",
                 "notes": ["the dataplane stopped answering at 23:59:59; "
                           "holding the last sample",
                           "counters went backwards at 23:59:59 (cleared); "
                           "that step reads as zero"]}
        _l, _ = prismash.render_monitor(_view, _config.CONSOLE_COLUMNS, 24,
                                        _utf)
        check(max(map(len, _l)) <= _config.CONSOLE_COLUMNS
              and not any(r.endswith("...") for r in _l[1:]),
              f"fits {_config.CONSOLE_COLUMNS} columns uncut, "
              f"{'UTF-8' if _utf else 'ASCII'}, {_mode}: "
              f"{[r for r in _l if len(r) > _config.CONSOLE_COLUMNS]!r}")

MON = ("              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)"
       "     Counter          Count\n"
       "pg0                               1      up          9000/0/0/0"
       "     rx bytes    {rx}\n"
       "local0                            0     down          0/0/0/0\n")
_sh = shell(dp=Dp({"show interface": MON.format(rx=7)}))
_t, _cnt = _sh.sample_counters()
check(_cnt["pg0"]["rx bytes"] == 7 and _t > 0,
      "one reading of show interface, stamped when it came back")
check(shell(dp=Dp({"show interface": (1, "")})).sample_counters() is None,
      "a dataplane that does not answer is no reading")
check(_sh.monitor_names({"pg0": {}, "pg0.1": {}, "pg1": {}}, ["pg0"])
      == ["pg0"]
      and _sh.monitor_names({"pg0": {}, "pg0.1": {}, "pg1": {}}, ["pg"])
      == ["pg0", "pg0.1", "pg1"],
      "a name that is an interface is that interface; a prefix is them all")
check(_sh.monitor_names(_cnt, []) == ["pg0"]
      and _sh.monitor_names(_cnt, ["LOC"]) == ["local0"]
      and _sh.monitor_names(_cnt, ["eth"]) == [],
      "every interface but local0, or those the names start")
with spoken() as o:
    _sh.do_monitor(["routes"])
check(rows_of(o.getvalue())
      == ["error: monitor what? 'monitor interface' or 'monitor cgnat logging'"],
      f"a thing it does not monitor: {o.getvalue()!r}")
for _args, _said in (
        (["interval"], "error: interval needs a number of seconds after it"),
        (["interval", "x"], "error: interval needs a number of seconds: x"),
        (["interval", "0.1"],
         "error: interval must be between 0.5 and 60 seconds")):
    with spoken() as o:
        _sh.do_monitor(["interface"] + _args)
    check(rows_of(o.getvalue()) == [_said], f"{_args}: {o.getvalue()!r}")
with spoken() as o:
    _sh.monitor_interface([])
check(o.getvalue().startswith("error: monitor interface needs a terminal")
      and "show interface utilization" in " ".join(o.getvalue().split())
      and "\x1b" not in o.getvalue(),
      f"a pipe is told what serves it, and gets no escapes: {o.getvalue()!r}")


# --- monitor cgnat logging (#556): the loop, through its seams ---
import logmon as _LM  # noqa: E402
import struct  # noqa: E402


def _seg(slots=8):
    """A one-ring segment as bpa_log_seg_init() lays it out."""
    rb = _LM.RING_HDR_BYTES + slots * _LM.EVENT_BYTES
    b = bytearray(_LM.HDR_BYTES + rb)
    b[0:8] = _LM.MAGIC
    struct.pack_into("<IIIIII", b, 8, _LM.VERSION, _LM.HDR_BYTES, 1, slots,
                     _LM.EVENT_BYTES, rb)
    struct.pack_into("<d", b, _LM.OFF["boot_wall"], 1_790_000_000.0)
    return b


def _push(b, ev, priv, lo, hi, who=b""):
    head = struct.unpack_from("<I", b, _LM.HDR_BYTES)[0]
    off = _LM.HDR_BYTES + _LM.RING_HDR_BYTES + (head & 7) * _LM.EVENT_BYTES
    a, c, d, e = (int(x) for x in priv.split("."))
    struct.pack_into("<IIIHHB", b, off, a << 24 | c << 16 | d << 8 | e,
                     200 << 24 | 150 << 16 | 16, 0, lo, hi, ev)
    b[off + 17:off + 17 + len(who)] = who
    struct.pack_into("<I", b, _LM.HDR_BYTES, head + 1)


class _Clock:
    """Time that moves only when the loop sleeps; events land on a sleep."""

    def __init__(self, seg, script):
        self.t, self.seg, self.script = 1_790_000_100.0, seg, list(script)

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s
        if self.script:
            self.script.pop(0)(self.seg)


_seg1 = _seg()
_clk = _Clock(_seg1, [lambda b: _push(b, 16, "100.64.0.5", 1024, 17407, b"cli-5"),
                      lambda b: _push(b, 12, "100.64.0.12", 0, 0),
                      lambda b: _push(b, 17, "100.64.0.5", 1024, 17407)])
_sh = shell(dp=Dp({"show bpa pool index": "0 din\n",
                   "show bpa logging": "TCP transport: disabled\n"}))
with spoken() as o:
    _sh.monitor_cgnat_logging(["seconds", "2"], segment=(_seg1, None),
                              now=_clk.now, sleep=_clk.sleep)
_rows = rows_of(o.getvalue())
check(any("ALLOC" in r and "100.64.0.5" in r and "200.150.0.16" in r
          and "1024-17407" in r and "pool din" in r for r in _rows)
      and any("subscriber cli-5" in r for r in _rows)
      and any("EXHAUSTED" in r and "no free block in pool din" in r for r in _rows)
      and any("RELEASE" in r for r in _rows) and any("BOOT" in r for r in _rows)
      and _rows[-1].endswith("stopped") and all(len(r) <= 79 for r in _rows),
      f"monitor cgnat logging prints each event in words: {_rows}")
_seg2 = _seg()
_clk = _Clock(_seg2, [lambda b: _push(b, 16, "100.64.0.5", 1024, 17407),
                      lambda b: _push(b, 16, "100.64.0.6", 17408, 33791)])
_sh = shell(dp=Dp({"show bpa pool index": (1, "unknown input"),
                   "show bpa logging": (1, "")}))
with spoken() as o:
    _sh.monitor_cgnat_logging(["subscriber", "100.64.0.6", "event", "alloc",
                               "seconds", "1"], segment=(_seg2, None),
                              now=_clk.now, sleep=_clk.sleep)
_rows = rows_of(o.getvalue())
check([r for r in _rows if "ALLOC" in r] and all("100.64.0.5" not in r for r in _rows)
      and any("pool #0" in r for r in _rows)
      and any("pools are shown by number" in r for r in _rows)
      and not any("BOOT" in r for r in _rows),
      f"filters, and a dataplane without pool names: {_rows}")
for _args, _seam, _said in (
        (["sideways"], None, "error: monitor cgnat logging does not take 'sideways'"),
        ([], (None, ("no event segment at /x", "the reason")),
         "error: no event segment at /x the reason"),
        ([], (bytearray(2048), None), "error: the event segment is not one this "
         "VTY reads its layout version or its event size is another build's")):
    with spoken() as o:
        _sh.monitor_cgnat_logging(_args, segment=_seam)
    check(" ".join(o.getvalue().split()) == _said, f"{_args}: {o.getvalue()!r}")
with spoken() as o:
    _sh.do_monitor(["cgnat", "logging", "sideways"])
check("does not take 'sideways'" in o.getvalue(),
      "do_monitor hands 'cgnat logging' to it")


class _Tty(io.StringIO):
    """A stand-in terminal: isatty() says yes."""

    def isatty(self):
        return True


class _Term:
    """
    MonitorTerminal's stand-in: keys come off a script, each wait moves a
    clock of its own, and every frame drawn is kept.
    """

    def __init__(self, script):
        self.script = list(script)
        self.now = 1000.0
        self.frames = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def size(self):
        return 80, 24

    def clock(self):
        return self.now

    def keys(self, timeout):
        self.now += timeout
        key = self.script.pop(0) if self.script else "q"
        if key == "^C":
            raise KeyboardInterrupt
        return key

    def draw(self, lines):
        self.frames.append(list(lines))


def _monitor(dp, script, args=()):
    """monitor_interface on a stand-in terminal; its frames."""
    term = _Term(script)
    sh = shell(dp=dp)
    was = prismash.MonitorTerminal, sys.stdin
    prismash.MonitorTerminal = lambda: term
    sys.stdin = _Tty()
    try:
        run = sh.monitor_run
        sh.monitor_run = lambda t, n, w, i, s: run(t, n, w, i, s,
                                                   clock=term.clock,
                                                   wall=lambda: "12:00:00")
        with contextlib.redirect_stdout(_Tty()) as out:
            sh.monitor_interface(list(args))
    finally:
        prismash.MonitorTerminal, sys.stdin = was
    return term.frames, out.getvalue()


_frames, _out = _monitor(Dp({"show interface": (1, "")}), [])
check(_frames == [] and "error: the dataplane is not answering, so there is "
      "nothing to sample" in _out,
      f"refused before the screen is taken: {_out!r}")
_frames, _out = _monitor(Dp({"show interface": MON.format(rx=0)}), [],
                         ["eth9"])
check(_frames == [] and "error: no interface matches" in _out,
      f"a name that matches nothing: {_out!r}")
# The command's own check reads once, then the view: 0 -> 1000 -> 1000
# bytes, silence, and the dataplane back with a counter at 5 - a rate, a flat
# step, a gap held, and a reset.
_dp = Dp({"show interface": [MON.format(rx=0), MON.format(rx=0),
                             MON.format(rx=1000),
                             MON.format(rx=1000), (1, ""),
                             MON.format(rx=5), MON.format(rx=5)],
          "show hardware-interfaces brief": ""})
_frames, _out = _monitor(_dp, [""] * 28 + ["l", "n", "x", "q"])
check("waiting for a second sample" in _frames[0][1],
      f"the first frame has one reading and says so: {_frames[0][1]!r}")
_rates = [f[2] for f in _frames if "rx " in f[2] and "--" not in f[2]]
check(_rates and _rates[0].endswith("rx 8.00 kbit/s"),
      f"1000 bytes over a measured second is 8 kbit/s: {_rates[:2]!r}")
check(any("the dataplane stopped answering at 12:00:00; holding the last "
          "sample" in f[-2:] for f in _frames),
      "a dataplane that goes quiet is said and the view stays")
check(any("counters went backwards at 12:00:00 (cleared); that step reads "
          "as zero" in f[-2:] for f in _frames),
      "and a counter that went backwards when it came back")
check(any("no line rate known" in f[1] for f in _frames),
      "l turns the line-rate scale on, and pg0 has none to show")
check(_out == "", f"nothing printed around the screen: {_out!r}")
# The view begun with the dataplane already quiet: said, and held open.
_t = _Term([""] * 8)
shell(dp=Dp({"show interface": (1, "")})).monitor_run(
    _t, ["pg0"], [], 1.0, {}, clock=_t.clock, wall=lambda: "12:00:00")
check(any("the dataplane stopped answering at 12:00:00" in f[-1]
          for f in _t.frames) and _t.frames[0][1].startswith("pg0"),
      f"monitor_run starts on a quiet dataplane and says so: "
      f"{_t.frames[-1][-2:]!r}")
_frames, _out = _monitor(Dp({"show interface": MON.format(rx=0)}), ["^C"])
check(len(_frames) == 1 and _out == "",
      "Ctrl-C leaves the view like q does, and the terminal is given back")
ok("a live chart: the sampler shared, the screen a pure function, "
   "every refusal before the screen is taken")

#: One virtual router as `vrrp_vr_dump` reports it, copied from a running
#: box: a master holding one address, with the MAC RFC 5798 gives its id.
VRRP_ROWS = [{
    "config": {"sw_if_index": 1, "vr_id": 1, "priority": 200,
               "interval": 100, "flags": "VRRP_API_VR_PREEMPT"},
    "runtime": {"state": "VRRP_API_VR_STATE_MASTER", "mac": "00:00:5e:00:01:01",
                "tracking": {"interfaces_dec": 0, "priority": 200}},
    "n_addrs": 1, "addrs": ["100.64.3.254"],
}]
TRACK_ROWS = [{"ifs": [{"sw_if_index": 2, "priority": 150}]}]

print("\n=== Shell.show_one_interface: the counters, the first row kept ===")
# Mutation shown red: `one.splitlines()[2:]`, dropping the interface's own
# row and with it the first counter (#280).
ONE = ("              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)"
       "     Counter          Count\n"
       "pg0 1 up 9000/0/0/0 rx packets 5\n"
       "                                                   rx bytes 300\n")
api = Api(rows={"sw_interface_dump": SW_ROWS, "vrrp_vr_dump": VRRP_ROWS},
          have={"pg0": 1},
          calls={"sw_interface_get_table": {"vrf_id": 10}})
sh = shell(dp=Dp({"show interface": IFACES,
                  "show interface features pg0": "ip4-unicast:\n  bpa-in2out\n",
                  "show interface pg0": ONE,
                  "show bpa urpf": "pg0 strict\n"}), api=api)
with spoken() as o:
    sh.show_one_interface("pg0", {"pg0": {"state": "up", "mtu": "9000",
                                          "addresses": ["200.150.0.1/24"],
                                          "host": "tap4096 vppwan0"}})
text = [r.strip() for r in rows_of(o.getvalue())]
for want in ("state       up", "mtu         9000",
             "addresses   200.150.0.1/24", "linux       tap4096 vppwan0",
             "vrf         10", "mac         02:fe:aa:bb:cc:dd",
             "description uplink", "urpf        strict", "cgnat       in2out",
             # The virtual router, which is the first thing that matters about
             # a port on a box in a pair: whether the subscribers are here or
             # on the other box.
             "vrrp 1      master, holding the address",
             "100.64.3.254 on 00:00:5e:00:01:01",
             "counters", "rx packets 5", "rx bytes 300"):
    check(want in text, f"{want!r} is on the screen: {text!r}")
# And a port with no virtual router says nothing about one: most ports have
# none, and a line that is always there is a line nobody reads.
sh = shell(dp=Dp({"show interface": IFACES,
                  "show interface features pg0": "ip4-unicast:\n  bpa-in2out\n",
                  "show interface pg0": ONE,
                  "show bpa urpf": "pg0 strict\n"}),
           api=Api(rows={"sw_interface_dump": SW_ROWS}, have={"pg0": 1},
                   calls={"sw_interface_get_table": {"vrf_id": 10}}))
with spoken() as o:
    sh.show_one_interface("pg0", {"pg0": {"state": "up", "mtu": "9000",
                                          "addresses": ["200.150.0.1/24"],
                                          "host": ""}})
check("vrrp" not in o.getvalue(),
      f"a port with no virtual router is not told about one: {o.getvalue()!r}")
ok("everything the box knows about one interface, counters uninterpreted")

print("\n=== Shell.one_nic_note: only the box whose one card is its path ===")
# Mutation shown red: `usable` counting the default-route card too, so the
# box whose one card is its path is told nothing.
pci = os.path.join(_root, "pci")
os.makedirs(os.path.join(pci, "0000:00:13.0"))
os.makedirs(os.path.join(pci, "0000:00:14.0"))


def sysnet(name, *cards):
    """A /sys/class/net of this suite's own, with these NICs on PCI."""
    net = os.path.join(_root, name)
    os.makedirs(os.path.join(net, "lo"))
    for nic, addr in cards:
        d = os.path.join(net, nic)
        os.makedirs(d)
        os.symlink(os.path.join(pci, addr), os.path.join(d, "device"))
    return net


ONE_NIC = sysnet("net-one", ("ens19", "0000:00:13.0"))
TWO_NIC = sysnet("net-two", ("ens19", "0000:00:13.0"),
                 ("ens20", "0000:00:14.0"))
NO_NIC = sysnet("net-none")
ROUTE = script("route", "Iface\tDestination\tGateway\tFlags\n"
               "ens19\t00000000\t0100000A\t0003\n"
               "ens19\t0000000A\t00000000\t0001\n", mode=0o644)
NO_ROUTE = script("no-route", "Iface\tDestination\tGateway\tFlags\n"
                  "ens19\t0000000A\t00000000\t0001\n", mode=0o644)
sh = shell()
sh.SYS_CLASS_NET, sh.PROC_NET_ROUTE = ONE_NIC, ROUTE
note = sh.one_nic_note()
check(note and "one NIC, 'ens19'" in note[0] and "default route" in note[0],
      f"one card and it carries the default route: {note!r}")
sh.SYS_CLASS_NET = TWO_NIC
check(sh.one_nic_note() == [], "a spare card is told nothing extra")
sh.SYS_CLASS_NET, sh.PROC_NET_ROUTE = ONE_NIC, NO_ROUTE
check(sh.one_nic_note() == [],
      "one card and no default route through it is not that box")
sh.SYS_CLASS_NET = NO_NIC
note = sh.one_nic_note()
check(note and "no PCI NIC" in note[0],
      f"no card at all is its own sentence: {note!r}")
ok("adds to an explanation, and only on the box it is true of")

print("\n=== Shell.why_named_nic_absent: which driver holds the card ===")
# Mutation shown red: `driver not in self.DPDK_DRIVERS` for the bound-right
# branch, so a card on virtio-pci is told the binding is fine.
devs = os.path.join(_root, "pci-devices")
drvs = os.path.join(_root, "pci-drivers")
os.makedirs(os.path.join(devs, "0000:00:13.0"))
os.makedirs(os.path.join(devs, "0000:00:14.0"))
os.makedirs(os.path.join(drvs, "vfio-pci"))
os.makedirs(os.path.join(drvs, "virtio-pci"))
os.symlink(os.path.join(drvs, "virtio-pci"),
           os.path.join(devs, "0000:00:13.0", "driver"))
os.symlink(os.path.join(drvs, "vfio-pci"),
           os.path.join(devs, "0000:00:14.0", "driver"))
sh = shell()
sh.PCI_DEVICES, sh.PCI_DRIVERS = devs, drvs
said = sh.why_named_nic_absent("dpdk { dev 0000:00:13.0 }")
check(said[0].startswith("'0000:00:13.0' is named")
      and any("bound to 'virtio-pci', which DPDK cannot" in s for s in said)
      and any(s.startswith("vfio-pci is loaded on this box") for s in said),
      f"the wrong driver, and the right one is there to bind to: {said!r}")
said = sh.why_named_nic_absent("dpdk { dev 0000:00:14.0 }")
check(any("bound to vfio-pci, which DPDK can use" in s for s in said)
      and any("journalctl -u vpp.service" in s for s in said),
      f"bound right: the cause is elsewhere and the journal is named: "
      f"{said!r}")
check(sh.why_named_nic_absent("dpdk { }") == [],
      "no dev named, nothing to explain")
ok("three states that read the same on the screen, told apart")

print("\n=== Shell._sysfs: the attribute, or None ===")
# Mutation shown red: "" where the file is not there.
attr = script("attr", "  1000\n", mode=0o644)
check(prismash.Shell._sysfs(attr) == "1000", "stripped")
check(prismash.Shell._sysfs(os.path.join(_root, "no-attr")) is None,
      "not there is None, which is not an empty reading")
ok("one sysfs attribute")

print("\n=== Shell.which_dpdk_driver: the binder's answer, or a sentence ===")
# Mutation shown red: the return code ignored.
sh = shell()
sh.BIND_NIC = script("binder-ok", "#!/bin/sh\necho vfio-pci\n")
check(sh.which_dpdk_driver() == "vfio-pci", "what the binder said")
sh.BIND_NIC = script("binder-bad", "#!/bin/sh\necho uio_pci_generic\nexit 1\n")
check(sh.which_dpdk_driver().startswith("a driver DPDK can use"),
      f"a binder that failed is not quoted: {sh.which_dpdk_driver()!r}")
sh.BIND_NIC = os.path.join(_root, "no-binder")
check(sh.which_dpdk_driver().endswith("this VTY could not ask"),
      "no binder to ask says so")
ok("asked of the thing that decides")

print("\n=== Shell.dataplane_if_refusal: a name the box has not got ===")
# Mutation shown red: `if name not in have`, refusing on a box this
# cannot look at.
sh = shell()
sh.SYS_CLASS_NET = TWO_NIC
why = sh.dataplane_if_refusal("ens21")
check(why is not None and why[0] == "this box has no such interface"
      and why[1][0] == "the cards it has: ens19, ens20",
      f"refused, naming what the box does have: {why!r}")
check(sh.dataplane_if_refusal("ens20") is None, "a card it has is taken")
sh.SYS_CLASS_NET = os.path.join(_root, "no-such-net")
check(sh.dataplane_if_refusal("ens21") is None,
      "cannot look is not 'the box has none'")
ok("one refusal, by what the box has")

print("\n=== Shell.say_dataplane_if_refusal: the name on the headline ===")
# Mutation shown red: the headline without the name.
sh = shell()
with spoken() as o:
    sh.say_dataplane_if_refusal("ens21", ("this box has no such interface",
                                          ["the cards it has: ens19"]))
text = rows_of(o.getvalue())
check(text[0] == "error: dataplane interface ens21: this box has no such "
                 "interface" and text[1] == "    the cards it has: ens19"
      and sh.failed == 1, f"{text!r} failed={sh.failed}")
ok("the statement's own words, then the reason")

print("\n=== Shell.read_tune_key: the LAST assignment, unquoted ===")
# Mutation shown red: `break` after the first match.
sh = shell()
sh.TUNE_ENV = script("tune.env", 'PRISMA_DATAPLANE_IF="ens18"\n'
                     "OTHER='x'\nPRISMA_DATAPLANE_IF=ens19\n", mode=0o644)
check(sh.read_tune_key("PRISMA_DATAPLANE_IF") == "ens19",
      f"last wins, as systemd reads it: {sh.read_tune_key('PRISMA_DATAPLANE_IF')!r}")
check(sh.read_tune_key("OTHER") == "x", "quotes off")
check(sh.read_tune_key("NOPE") == "", "not set is empty")
sh.TUNE_ENV = os.path.join(_root, "no-tune.env")
check(sh.read_tune_key("PRISMA_DATAPLANE_IF") == "", "no file is empty")
ok("one key out of the tuner's file")

print("\n=== Shell.show_interfaces: the table, one interface, a filter ===")
# Mutation shown red: `exact = []`, so `show interface pg0` on a box with
# pg0.100 matches two by prefix and prints the table instead of the one.
IF_SUB = IFACES + ("pg0.100                           4      up          "
                   "9000/0/0/0\n")
dp = Dp({"show interface": IF_SUB, "show interface addr": ADDR,
         "show lcp": "", "show hardware-interfaces brief": HW})
sh = shell(dp=dp)
seen = []
sh.show_one_interface = lambda name, ifaces: seen.append(name)
sh.show_interfaces_terse = lambda i, h, total=None: seen.append(
    ("terse", sorted(i), sorted(total or [])))
with spoken() as o:
    sh.show_interfaces([])
text = rows_of(o.getvalue())
check(text[0].split() == ["Interface", "State", "MTU", "Address"]
      and any(r.split() == ["pg0", "up", "9000", "200.150.0.1/24"]
              for r in text)
      and any(r.split() == ["2001:db8::1/64"] for r in text)
      and "  4 interface(s) on the dataplane can forward" in text,
      f"the table, a second address under its interface, the count: "
      f"{text!r}")
sh.show_interfaces(["pg0"])
check(seen == ["pg0"], f"an exact name wins over its sub-interfaces: {seen!r}")
sh.show_interfaces(["terse", "pg1"])
check(seen[-1] == ("terse", ["pg1"],
                   sorted(["pg0", "pg1", "loop0", "pg0.100"])),
      f"terse, filtered, with the whole table as the total - and `local0` in "
      f"neither half of it (#403): {seen[-1]!r}")
with spoken() as o:
    sh.show_interfaces(["nope"])
check(rows_of(o.getvalue()) == ["  no interface matches"], o.getvalue())
sh = shell(dp=Dp({"show interface": (1, "")}))
with spoken() as o:
    sh.show_interfaces([])
check(rows_of(o.getvalue()) == ["error: cannot read the interfaces: the "
                                "dataplane is not answering"],
      f"cannot ask is not an empty box: {o.getvalue()!r}")
ok("the dataplane's interfaces, from the dataplane")

# --------------------------------------------------------- the routing

print("\n=== Shell.report_rpki_state: the per-cache marker, not the header ===")
# Mutation shown red: counting "Connected" instead of "(connected)".
cfg = Config()
cfg.routing = ["rpki", " rpki cache 10.0.0.9 3323 preference 1", "exit"]
sh = shell(frr=frr_stub({"show rpki cache-connection":
                         "Connected to group 1\n10.0.0.9:3323 (connected)\n"}),
           running=cfg)
with spoken() as o:
    sh.report_rpki_state()
check(rows_of(o.getvalue()) == ["  RPKI: 1 of 1 cache(s) connected"],
      f"{o.getvalue()!r}")
sh = shell(frr=frr_stub({"show rpki cache-connection":
                         "Connected to group 1\n10.0.0.9:3323\n"}),
           running=cfg)
with spoken() as o:
    sh.report_rpki_state()
text = rows_of(o.getvalue())
check(text[0] == "  RPKI: 1 cache(s) configured, NONE connected"
      and any("expires" in r for r in text[1:]),
      f"the header still says Connected; the marker is gone: {text!r}")
sh = shell(frr=frr_stub({}), running=cfg)
with spoken() as o:
    sh.report_rpki_state()
check(rows_of(o.getvalue()) == ["  RPKI: the module is not loaded, so "
                                "nothing is validated"], o.getvalue())
sh = shell(running=Config())
with spoken() as o:
    sh.report_rpki_state()
check(o.getvalue() == "", "no cache configured is a choice, not a fault")
ok("whether validation is actually happening")

print("\n=== Shell.bgp_prefixes: sorted, past any text before the JSON ===")
# Mutation shown red: `json.loads(out)` without finding the brace.
BGP = ("% some warning FRR prints first\n"
       + json.dumps({"routes": {"10.0.0.0/24": [{"path": "65001 65002"}],
                                "0.0.0.0/0": [{"aspath": {"string": "65003"}}],
                                "10.1.0.0/24": [{"aspath": "65004"}]}}))
sh = shell(frr=frr_stub({"show bgp ipv4 unicast json": BGP}))
check(sh.bgp_prefixes() == ["0.0.0.0/0", "10.0.0.0/24", "10.1.0.0/24"],
      f"{sh.bgp_prefixes()!r}")
check(shell(frr=frr_stub({})).bgp_prefixes() == [],
      "no BGP is no prefixes, not a failure")
ok("every prefix BGP holds")

print("\n=== Shell.frr_json: {} is an answer and None is not ===")
# Mutation shown red: `if not data: return None`, collapsing the two.
sh = shell(frr=frr_stub({"show ip ospf neighbor json": "{}",
                         "show junk json": "not json at all"}))
check(sh.frr_json("show ip ospf neighbor json") == {},
      "a daemon that answered with nothing configured is an empty object")
check(sh.frr_json("show ip ospf interface json") is None,
      "a daemon that is not running is None")
check(sh.frr_json("show junk json") is None,
      "an answer that is not JSON is None")
ok("two situations with two first questions, kept apart")

print("\n=== Shell.report_fib_reach: which of these the FIB has ===")
# Mutation shown red: `if p in in_fib` for the missing list.
sh = shell(dp=Dp({"show ip fib": FIB}))
with spoken() as o:
    sh.report_fib_reach(["10.0.0.0/8", "198.19.0.0/24"], "OSPF")
text = rows_of(o.getvalue())
check(text[0] == "  1 of 2 OSPF prefix(es) are NOT in the forwarding table"
      and text[1] == "    198.19.0.0/24"
      and "    'show route verbose' compares the two tables in full" in text,
      f"the one that stopped half way, named: {text!r}")
with spoken() as o:
    sh.report_fib_reach(["10.0.0.0/8"], "OSPF")
check(rows_of(o.getvalue()) == ["  all 1 OSPF prefix(es) are in the "
                                "forwarding table"], o.getvalue())
with spoken() as o:
    sh.report_fib_reach([], "IS-IS")
check(rows_of(o.getvalue()) == ["  no IS-IS prefixes to check against the "
                                "forwarding table"], o.getvalue())
sh = shell(dp=Dp({"show ip fib": (1, "")}))
with spoken() as o:
    sh.report_fib_reach(["10.0.0.0/8"], "OSPF")
check("not answering" in o.getvalue(), o.getvalue())
ok("the count worth the line, in the same words for every protocol")

print("\n=== Shell.igp_prefixes: what zebra chose from one protocol ===")
# Mutation shown red: "ospf" hard-coded in place of `protocol`.
sh = shell(frr=frr_stub({"show ip route json": RIB}))
check(sh.igp_prefixes("isis") == ["198.19.0.0/24"],
      f"IS-IS's prefix and not OSPF's: {sh.igp_prefixes('isis')!r}")
check(sh.igp_prefixes("ospf") == ["10.9.0.0/16"], "and the other way")
check(shell(frr=frr_stub({})).igp_prefixes("isis") == [],
      "FRR silent is no prefixes")
ok("from show ip route json, by protocol")

print("\n=== Shell.ospf_mtu_stalls: keyed by the name FRR knows ===")
# Mutation shown red: keyed by `ifname`, the dataplane's name.
sh = shell(running=Config())
sh.ospf_mtu_mismatch = lambda routing, mtus: [("host-veth-vpp", "vppwan0",
                                               1500, 9000)]
check(sh.ospf_mtu_stalls() == {"vppwan0": ("host-veth-vpp", 1500, 9000)},
      f"{sh.ospf_mtu_stalls()!r}")


def _boom(routing, mtus):
    raise RuntimeError("ip link not here")


sh.ospf_mtu_mismatch = _boom
check(sh.ospf_mtu_stalls() == {}, "a check that could not run is no stalls")
ok("the commit's check, asked of the running configuration")

print("\n=== Shell.show_ospf_neighbors: the interface name FRR prints ===")
# Mutation shown red: `ifaceName` printed whole, ':<address>' and all.
NEIGH = json.dumps({"neighbors": {
    "10.0.0.2": [{"ifaceName": "vppwan0:10.0.0.1", "nbrState": "Full/DR",
                  "converged": "Full", "deadTime": "35s", "upTime": "1h"}],
    "10.0.0.3": [{"ifaceName": "vppwan1:10.0.1.1", "nbrState": "ExStart/DR",
                  "converged": "ExStart", "deadTime": "31s",
                  "upTime": "2m"}]}})
sh = shell(frr=frr_stub({"show ip ospf neighbor json": NEIGH}))
reached = []
sh.ospf_mtu_stalls = lambda: {"vppwan1": ("host-veth-vpp", 1500, 9000)}
sh.report_ospf_exchange = lambda ex: reached.append(("exchange", ex))
sh.igp_prefixes = lambda p: reached.append(("igp", p)) or ["10.9.0.0/16"]
sh.report_fib_reach = lambda p, w: reached.append(("fib", p, w))
with spoken() as o:
    sh.show_ospf_neighbors()
text = rows_of(o.getvalue())
check(any(r.split() == ["10.0.0.2", "vppwan0", "Full/DR", "35s", "1h"]
          for r in text),
      f"the row, with the interface as the next command takes it: {text!r}")
check("  2 adjacency(ies), 1 full" in text, f"{text!r}")
check(any("interface host-veth-vpp: carries OSPF and its MTU is 1500"
          in r for r in text),
      f"the stall this box can see, in the commit's words: {text!r}")
check(reached == [("exchange", {"vppwan1": ("10.0.0.3", "ExStart")}),
                  ("igp", "ospf"), ("fib", ["10.9.0.0/16"], "OSPF")],
      f"the Exchange report and the FIB count follow: {reached!r}")
sh = shell(frr=frr_stub({}))
with spoken() as o:
    sh.show_ospf_neighbors()
check(rows_of(o.getvalue())[0] == "error: OSPF is not answering",
      f"ospfd down is a refusal, not an empty table: {o.getvalue()!r}")
sh = shell(frr=frr_stub({"show ip ospf neighbor json": "{}"}))
reached = []
sh.report_no_ospf_neighbour = lambda: reached.append("none")
sh.igp_prefixes = lambda p: []
sh.report_fib_reach = lambda p, w: None
sh.show_ospf_neighbors()
check(reached == ["none"], "no neighbour at all is its own report")
ok("FRR's state, beside what only this box can see")

print("\n=== Shell.report_ospf_exchange: this end's MTU beside the stall ===")
# Mutation shown red: reading "mtu" instead of "mtuBytes", printing '?'.
sh = shell(frr=frr_stub({"show ip ospf interface json": json.dumps(
    {"interfaces": {"vppwan1": {"mtuBytes": 9000}}})}))
with spoken() as o:
    sh.report_ospf_exchange({"vppwan1": ("10.0.0.3", "ExStart")})
text = rows_of(o.getvalue())
check(text[0] == "  10.0.0.3 on vppwan1 is in ExStart, which is where the "
                 "two ends compare MTU; this end advertises 9000",
      f"{text!r}")
check(any("ip ospf mtu-ignore" in r for r in text), f"the escape hatch: {text!r}")
ok("the number to compare against, from FRR's own interface view")

print("\n=== Shell.report_no_ospf_neighbour: three situations ===")
# Mutation shown red: `quiet = []`, so passive interfaces count as
# sending hellos.
def no_neighbour(ifaces):
    sh = shell(frr=frr_stub({"show ip ospf interface json": json.dumps(
        {"interfaces": ifaces})}))
    with spoken() as o:
        sh.report_no_ospf_neighbour()
    return rows_of(o.getvalue())


check(no_neighbour({}) == ["  no OSPF adjacency, and no interface is "
                           "running OSPF"], "nothing runs OSPF")
check(no_neighbour({"a": {"ospfEnabled": True, "timerPassiveIface": True},
                    "b": {"ospfEnabled": True, "timerPassiveIface": True}})
      == ["  no OSPF adjacency; all 2 interface(s) running OSPF are passive, "
          "so none sends a hello"], "all passive")
check(no_neighbour({"a": {"ospfEnabled": True, "timerPassiveIface": True},
                    "b": {"ospfEnabled": True}})
      == ["  no OSPF adjacency, on 1 interface(s) that are sending hellos"],
      "one sending hellos and nobody answering")
ok("told apart by whether anything is running OSPF at all")

print("\n=== Shell.isis_circuits: (area, circuit) pairs, flattened ===")
# Mutation shown red: drop the isinstance test, taking a string circuit.
sh = shell(frr=frr_stub({"show isis interface detail json": json.dumps(
    {"areas": [{"area": "A1", "circuits": [{"interface": {"name": "vppwan0"}},
                                           {"interface": "junk"}]},
               {"area": "A2", "circuits": [{"interface": {"name": "vppwan1"}}]}
               ]})}))
check(sh.isis_circuits() == [("A1", {"name": "vppwan0"}),
                             ("A2", {"name": "vppwan1"})],
      f"{sh.isis_circuits()!r}")
check(shell(frr=frr_stub({})).isis_circuits() == [], "isisd down is none")
ok("the area travels with the circuit")

print("\n=== Shell.isis_punt_state: (punting, drops), None when unasked ===")
# Mutation shown red: `False if have is None`.
ERRORS = ("   Count                    Node                  Reason\n"
          "        12               osi-input          unknown osi protocol\n")
sh = shell(api=Api(calls={"lcp_osi_proto_get": {"osi_protos": "0x83"}}),
           dp=Dp({"show errors": ERRORS}))
check(sh.isis_punt_state() == (True, 12), f"{sh.isis_punt_state()!r}")
sh = shell(api=Api(calls={"lcp_osi_proto_get": {"osi_protos": "0x"}}),
           dp=Dp({"show errors": ERRORS}))
check(sh.isis_punt_state() == (False, 12), f"{sh.isis_punt_state()!r}")
sh = shell(api=Api(refuse={"lcp_osi_proto_get"}),
           dp=Dp({"show errors": (1, "")}))
check(sh.isis_punt_state() == (None, 0),
      f"could not ask is None, and no errors read is 0: "
      f"{sh.isis_punt_state()!r}")
ok("whether NLPID 0x83 reaches the kernel, and what was dropped")

print("\n=== Shell.report_isis_punt: on, off with evidence, off without ===")
# Mutation shown red: the `if drops:` test dropped, claiming frames always.
def punt(state, silent=("x",)):
    sh = shell()
    sh.isis_punt_state = lambda: state
    with spoken() as o:
        sh.report_isis_punt(list(silent))
    return " ".join(o.getvalue().split())


check("punting IS-IS to the kernel is unknown" in punt((None, 0)),
      "not answering")
check("IS punting IS-IS" in punt((True, 0)) and "0x83" in punt((True, 0)),
      "on, with a silent circuit: the far end is not answering")
check(punt((True, 0), silent=()) == "", "on, nothing silent: nothing said")
off = punt((False, 12))
check("NOT punting" in off and "12 frame(s) counted in osi-input" in off,
      f"off, and the frames arriving are the evidence: {off!r}")
off = punt((False, 0))
check("NOT punting" in off and "not even arriving" in off
      and "frame(s)" not in off, f"off, and nothing arrived: {off!r}")
ok("both answers, because both are what 2am needs")

print("\n=== Shell.show_isis_adjacency: silent circuits are Up, Active ===")
# Mutation shown red: `is-passive == "active"` dropped, so a passive
# circuit with nobody on it is reported as the punt's shape.
ADJ = json.dumps({"areas": [{"circuits": [
    {"adj": "upstream", "interface": "vppwan0", "level": 2, "state": "Up",
     "expires-in": "28s"},
    {"interface": "vppwan1"}]}]})
sh = shell(frr=frr_stub({"show isis neighbor json": ADJ}))
reached = []
sh.isis_circuits = lambda: [
    ("A1", {"name": "vppwan1", "state": "Up", "is-passive": "active",
            "levels": [{"active-neighbors": 0}]}),
    ("A1", {"name": "vppwan2", "state": "Up", "is-passive": "passive",
            "levels": [{"active-neighbors": 0}]}),
    ("A1", {"name": "vppwan0", "state": "Up", "is-passive": "active",
            "levels": [{"active-neighbors": 1}]})]
sh.report_isis_punt = lambda silent: reached.append(("punt", silent))
sh.report_isis_overload = lambda: reached.append("overload")
sh.igp_prefixes = lambda p: reached.append(("igp", p)) or []
sh.report_fib_reach = lambda p, w: reached.append(("fib", w))
with spoken() as o:
    sh.show_isis_adjacency()
text = rows_of(o.getvalue())
check(any(r.split() == ["upstream", "vppwan0", "L2", "Up", "28s"]
          for r in text) and "  1 adjacency(ies), 1 up" in text,
      f"the adjacency and the count: {text!r}")
check("  circuit vppwan1 in area A1 is Up and Active with no adjacency"
      in text and not any("vppwan2" in r for r in text),
      f"the active circuit with nobody on it, not the passive one: {text!r}")
check(reached[0] == ("punt", [("A1", sh.isis_circuits()[0][1])])
      and reached[1:] == ["overload", ("igp", "isis"), ("fib", "IS-IS")],
      f"the punt report for the silent one, then overload, then the FIB: "
      f"{reached!r}")
sh = shell(frr=frr_stub({}))
with spoken() as o:
    sh.show_isis_adjacency()
check(rows_of(o.getvalue())[0] == "error: IS-IS is not answering",
      o.getvalue())
ok("the shape the punt leaves behind, made visible")

print("\n=== Shell.report_isis_overload: read from the LSP this box floods ===")
# Mutation shown red: `not isis_overload_set(out)` dropped.
DB = "prisma-routing.00-00    *    108   0x00000005  0x0113    1160    0/0/{}\n"
sh = shell(frr=frr_stub({"show isis database": DB.format(1)}))
with spoken() as o:
    sh.report_isis_overload()
check("overload bit set" in o.getvalue(), o.getvalue())
sh = shell(frr=frr_stub({"show isis database": DB.format(0)}))
with spoken() as o:
    sh.report_isis_overload()
check(o.getvalue() == "", "bit clear, nothing said")
sh = shell(frr=frr_stub({}))
with spoken() as o:
    sh.report_isis_overload()
check(o.getvalue() == "", "isisd down, nothing said")
ok("the maintenance drain nobody remembers turning on")

print("\n=== Shell.show_route: the FIB, and verbose the RIB beside it ===")
# Mutation shown red: `verbose = False`.
dp = Dp({"show ip fib": FIB, "show lcp": LCP, "show interface": IFACES})
sh = shell(dp=dp, frr=frr_stub({"show ip route json": RIB}))
with spoken() as o:
    sh.show_route([])
text = rows_of(o.getvalue())
check(any(r.split() == ["10.0.0.0/8", "200.150.0.2", "pg0", "forward"]
          for r in text) and "  3 entries carrying traffic" in text,
      f"the forwarding table: {text!r}")
with spoken() as o:
    sh.show_route(["verbose"])
text = rows_of(o.getvalue())
# The mark is on a row of its OWN since #392, under the route it is about:
# behind a value with no widest it was what a fold landed in.
_at = [i for i, r in enumerate(text) if r.startswith("  198.19.0.0/24")]
check(_at and text[_at[0] + 1].strip() == "** in FRR, NOT forwarding",
      f"verbose compares the two, and marks the route under it: {text!r}")
sh = shell(dp=Dp({"show ip fib": FIB}), frr=frr_stub({}))
with spoken() as o:
    sh.show_route(["verbose"])
check("  (FRR did not answer; showing the forwarding table alone)"
      in rows_of(o.getvalue()), o.getvalue())
sh = shell(dp=Dp({"show ip fib": (1, "")}))
with spoken() as o:
    sh.show_route([])
check(rows_of(o.getvalue()) == ["error: cannot read the forwarding table: "
                                "the dataplane is not answering"],
      o.getvalue())
ok("the table that forwards, and on request the one behind it")

print("\n=== Shell._route_rows: the entries carrying traffic ===")
# Mutation shown red: `rows = dict(table)`, every FIB entry listed.
table = routes.parse_fib(FIB)[0]
sh = shell()
with spoken() as o:
    sh._route_rows(table, [])
text = rows_of(o.getvalue())
check("  3 entries carrying traffic" in text
      and not any(r.startswith("  200.150.0.2/32") for r in text),
      f"a resolved neighbour and a drop are not routes: {text!r}")
with spoken() as o:
    sh._route_rows(table, ["10."])
check("  1 entry carrying traffic" in rows_of(o.getvalue()),
      f"filtered by prefix, singular: {o.getvalue()!r}")
with spoken() as o:
    sh._route_rows(table, ["9.9."])
check(rows_of(o.getvalue()) == ["  no matching entry in the forwarding table"],
      o.getvalue())
ok("what an operator configured, not what VPP resolved")

print("\n=== route_column_widths: measured, or None when it cannot fit ===")
check(prismash.route_column_widths([], ["Destination"]) is None,
      "nothing to measure is not a table")
check(prismash.route_column_widths([("10.0.0.0/8", "10.0.0.1", "pg0")],
                                   ["Destination", "Next hop", "Interface"])
      == [11, 8, 9],
      "each column is the widest of its own values and its own heading")
check(prismash.route_column_widths([("10.0.0.0/8", "x", "y")],
                                 ["Destination", "Next hop", "Interface"])
      == [11, 8, 9],
      "a heading wider than every value under it is the width")
_v6 = ("2001:0db8:0000:0000:0000:0000:0000:0001/128",
       "2001:0db8:0000:0000:0000:0000:0000:0002",
       "GigabitEthernet0/13/0.4094")
check(prismash.route_column_widths([_v6],
                                 ["Destination", "Next hop", "Interface"])
      is None,
      "an IPv6 route at its widest does not fit, and is not squeezed")
check(prismash.route_column_widths(
          [("10.0.0.0/8", "10.0.0.1", "pg0")],
          ["Destination", "Next hop", "Interface"],
          tail=60) is None,
      "and what the caller puts after the last column is counted too")
ok("the columns are the box's own values, and None means print blocks")

print("\n=== next_hop_column and rib_column: what goes in a column ===")
check(prismash.next_hop_column("10.0.0.1") == "10.0.0.1"
      and prismash.next_hop_column("2001:db8::1") == "2001:db8::1",
      "an address is itself")
check(prismash.next_hop_column("connected") == prismash.COLUMN_NONE
      and prismash.next_hop_column("local") == prismash.COLUMN_NONE
      and prismash.next_hop_column("-") == prismash.COLUMN_NONE,
      "VPP's word for a route with no next hop is not a next hop")
check(prismash.rib_column(True) == "yes"
      and prismash.rib_column(False) == prismash.COLUMN_NONE
      and set(prismash.RIB_COLUMN) == {"yes", prismash.COLUMN_NONE},
      "the FRR column is one of two words, and both are declared")
ok("a column of addresses stays a column of addresses")

print("\n=== and a route at its widest is printed in blocks, not folded ===")
# THE GUARANTEE #392 RESTS ON, asserted where a runtime decision can be. The
# width reader cannot know what `{via:<{vw}}` pads to, so it measures these
# rows as they are driven; what keeps them inside the console is this.
_wide_fib = {
    "2001:db8::1/128": {
        "via": [("2001:0db8:0000:0000:0000:0000:0000:0002",
                 "GigabitEthernet0/13/0.4094")],
        "kind": "forward",
    },
}
with spoken() as o:
    shell()._route_rows(_wide_fib, [])
_wide = rows_of(o.getvalue())
check(all(len(r) <= prismash.CONSOLE_COLUMNS for r in _wide),
      f"a v6 route at its widest is over the console: "
      f"{[r for r in _wide if len(r) > prismash.CONSOLE_COLUMNS]!r}")
check(any(r.strip() == "2001:db8::1/128" for r in _wide)
      and any(r.strip().startswith("via:") for r in _wide),
      f"and it is printed as a block, each row ending in its value: {_wide!r}")
with spoken() as o:
    shell()._route_rows_verbose(
        _wide_fib, {"2001:db8::1/128": {}}, [])
_wide = rows_of(o.getvalue())
check(all(len(r) <= prismash.CONSOLE_COLUMNS for r in _wide),
      f"the verbose screen is over the console: "
      f"{[r for r in _wide if len(r) > prismash.CONSOLE_COLUMNS]!r}")
ok("what the reader measures as driven is what this keeps inside the console")

print("\n=== Shell._route_rows_verbose: in FRR and not forwarding ===")
# Mutation shown red: `if in_rib and in_fib` for the mark.
rib = routes.parse_rib(RIB)
sh = shell()
with spoken() as o:
    sh._route_rows_verbose(table, rib, [])
text = rows_of(o.getvalue())
# The mark is a row of its own now (#392), so what is asserted is the route
# it sits UNDER: it is the row before each mark.
marked = [text[i - 1].split()[0] for i, r in enumerate(text)
          if r.strip() == "** in FRR, NOT forwarding"]
check(sorted(marked) == ["10.9.0.0/16", "198.19.0.0/24"],
      f"the two FRR holds and VPP has not: {marked!r}")
check("  2 prefixes in FRR's table and not in the forwarding table." in text,
      f"and the conclusion drawn for the reader: {text!r}")
with spoken() as o:
    sh._route_rows_verbose(table, {"0.0.0.0/0": rib["0.0.0.0/0"]}, [])
check("  every route FRR holds is in the forwarding table"
      in rows_of(o.getvalue()), o.getvalue())
with spoken() as o:
    sh._route_rows_verbose(table, rib, ["9.9."])
check(rows_of(o.getvalue()) == ["  no matching entry in either table"],
      o.getvalue())
ok("two lists compared, not left side by side")

# ----------------------------------------------------- show version

print("\n=== Shell.show_plugin_build: four answers, not two ===")
# Mutation shown red: `if not build:` for the first branch, collapsing
# "not answering" into "reports no build".
sh = shell(dp=Dp({}))
with spoken() as o:
    sh.show_plugin_build()
check(rows_of(o.getvalue())[0] == "  plugin:    the dataplane is not "
                                  "answering, so its build is not known",
      o.getvalue())
sh = shell(dp=Dp({"show plugins": "     Plugin    Version   Description\n"}))
with spoken() as o:
    sh.show_plugin_build()
check(rows_of(o.getvalue())[0] == "  plugin:    the dataplane is answering "
                                  "and reports no build", o.getvalue())
sh = shell(dp=Dp({"show plugins": PLUGINS.format("0.99.7")}))
with spoken() as o:
    sh.show_plugin_build()
check(rows_of(o.getvalue()) == ["  plugin:    0.99.7"],
      f"the same build: the row and nothing under it: {o.getvalue()!r}")
sh = shell(dp=Dp({"show plugins": PLUGINS.format("0.24.0 (abc1234)")}))
with spoken() as o:
    sh.show_plugin_build()
text = rows_of(o.getvalue())
check(text[0] == "  plugin:    0.24.0 (abc1234)"
      and any("not the Prisma Routing build above" in r for r in text),
      f"a different build: the sentence under the row: {text!r}")
saved = prismash.VERSION_PATH
try:
    prismash.VERSION_PATH = os.path.join(_root, "no-such-stamp")
    with spoken() as o:
        sh.show_plugin_build()
    check(any("running from a source tree" in r
              for r in rows_of(o.getvalue())),
          f"no stamp to compare against says so: {o.getvalue()!r}")
finally:
    prismash.VERSION_PATH = saved
ok("silent, not answering, no build, agrees, disagrees, nothing to compare")

print("\n=== Shell.show_daemons: running and stopped, by name ===")
# Mutation shown red: `stalled = []`, so a missing zebra gets the
# /etc/frr/daemons advice.
sh = shell(frr=frr_stub({"show daemons": "zebra bgpd staticd mgmtd"}))
with spoken() as o:
    sh.show_daemons()
text = rows_of(o.getvalue())
check(text[0] == "  daemons:   running  bgpd mgmtd staticd zebra",
      f"sorted, everything FRR reports: {text!r}")
check(text[1].startswith("             stopped  ") and "ospfd" in text[1]
      and "bgpd" not in text[1], f"the configurable ones not up: {text!r}")
check(any("enable it in /etc/frr/daemons" in r for r in text),
      f"an optional daemon is enabled in the file: {text!r}")
sh = shell(frr=frr_stub({"show daemons": "bgpd"}))
with spoken() as o:
    sh.show_daemons()
check(any("frr did not finish starting" in r for r in rows_of(o.getvalue())),
      f"zebra missing is frr not started, not a file to edit: "
      f"{o.getvalue()!r}")
sh = shell(frr=frr_stub({}))
with spoken() as o:
    sh.show_daemons()
check(rows_of(o.getvalue())[0] == "  daemons:   FRR is not answering, so "
                                  "which daemons are running is not known",
      o.getvalue())
ok("the reader arrived from a message naming ONE daemon")

print("\n=== Shell.print_daemon_list: one label, wrapped under itself ===")
# Mutation shown red: the names printed whole on the label's row.
with spoken() as o:
    prismash.Shell.print_daemon_list("  daemons:   running  ", ["bgpd", "zebra"])
check(o.getvalue() == "  daemons:   running  bgpd zebra\n", o.getvalue())
with spoken() as o:
    prismash.Shell.print_daemon_list("  daemons:   stopped  ", [])
check(o.getvalue() == "  daemons:   stopped  (none)\n", o.getvalue())
many = [f"daemon{n:02d}" for n in range(12)]
with spoken() as o:
    prismash.Shell.print_daemon_list("  daemons:   running  ", many)
text = rows_of(o.getvalue())
check(len(text) > 1 and all(len(r) <= 79 for r in text)
      and all(r.startswith(" " * len("  daemons:   running  "))
              for r in text[1:])
      and " ".join(" ".join(text).split()[2:]) == " ".join(many),
      f"wrapped inside the console, no name split: {text!r}")
ok("a list whose length is not this VTY's to predict")

# ------------------------------------------------ candidate and revisions

print("\n=== show configuration compare shows no secret (#506) ===")
# Found on the X520 box: the rollback that took out an enterprise number
# printed the box's community in clear on its context line.
_run = Config.parse("snmp enabled yes\nsnmp community oldsecret\n"
                    "snmp enterprise-number 99999\n")
_cand = Config.parse("snmp enabled yes\nsnmp community newsecret\n")
sh = shell(running=_run)
sh.candidate = _cand
with spoken() as o:
    sh.show_compare()
_d = o.getvalue()
check("oldsecret" not in _d and "newsecret" not in _d,
      f"a community reached the compare screen: {_d!r}")
check("-snmp community <set, not shown>" in _d
      and "+snmp community <set, not shown>" in _d
      and "-snmp enterprise-number 99999" in _d,
      f"the change is still shown, a line each side, without the value: {_d!r}")
check(prismash.compare_redact(["--- running", "+++ candidate", "@@ -1 +1 @@",
                      " neighbor 10.0.0.2 password s3cr3t", ""])
      == ["--- running", "+++ candidate", "@@ -1 +1 @@",
          " neighbor 10.0.0.2 password <set, not shown>", ""],
      "a BGP key on a context line is blanked, and the headers are left alone")
print("  [ ok ] a changed secret is a - and a + line, and neither says it")

print("\n=== snmp trap-host: a destination, refused when it is not one (#507) ===")
sh = shell(running=Config())
sh.candidate = Config()
for _bad, _says in (("snmp trap-host 10.0.0.9", "usage: snmp trap-host"),
                    ("snmp trap-host 10.0.0.9 community c email", "usage"),
                    ("snmp trap-host nms community c", "not an IP address")):
    with spoken() as o:
        sh.do_config(_bad)
    check(_says in o.getvalue(), f"{_bad!r} said {o.getvalue()!r}")
with spoken() as o:
    sh.do_config("snmp trap-host 10.0.0.9 community c1")
    sh.do_config("snmp trap-host 10.0.0.9 community c2 inform")
    sh.do_config("snmp trap-host 10.0.0.8 community c3")
check(sh.candidate.snmp_trap_hosts == [("10.0.0.9", "c2", "inform"),
                                       ("10.0.0.8", "c3", "trap")],
      f"the same address replaces its line: {sh.candidate.snmp_trap_hosts}")
with spoken() as o:
    sh.do_config("no snmp trap-host 10.0.0.9")
check(sh.candidate.snmp_trap_hosts == [("10.0.0.8", "c3", "trap")],
      f"and is taken out: {sh.candidate.snmp_trap_hosts}")
print("  [ ok ] set, replaced, removed, and refused with the reason")

print("\n=== no snmp <setting>: back to the default (#506) ===")
sh = shell(running=_run)
sh.candidate = Config.parse(_run.text())
for _stmt in ("no snmp enterprise-number", "no snmp port",
              "no snmp listen-address", "no snmp enabled"):
    with spoken() as o:
        sh.do_config(_stmt)
    check("error" not in o.getvalue(),
          f"{_stmt!r} was refused: {o.getvalue()!r}")
check(sh.candidate.snmp.get("enterprise-number") == "67033"
      and "enterprise-number" not in sh.candidate.text(),
      f"back to the product's number, and not written: {sh.candidate.snmp}")
check(sh.candidate.snmp.get("enabled") == "no"
      and sh.candidate.snmp.get("port") == "161",
      f"not back to the defaults: {sh.candidate.snmp}")
print("  [ ok ] the number taken out, and the rest put back")

print("\n=== Shell.show_compare: running against candidate ===")
# Mutation shown red: `self.candidate.compare(self.running_config())`.
sh = shell(running=Config())
with spoken() as o:
    sh.show_compare()
check(o.getvalue() == "no candidate configuration\n", o.getvalue())
sh.candidate = Config()
with spoken() as o:
    sh.show_compare()
check(o.getvalue() == "no differences\n", o.getvalue())
sh.candidate = Config()
sh.candidate.mtus = {"pg0": 1500}
with spoken() as o:
    sh.show_compare()
text = rows_of(o.getvalue())
check("+interface pg0" in text and "+ mtu 1500" in text,
      f"what the commit would add, as an addition: {text!r}")
ok("the diff from running to candidate")

print("\n=== Shell.show_rollback: numbered from the current one ===")
# Mutation shown red: `n == 1` labelled current.
revs = os.path.join(_root, "revs-g")
os.makedirs(revs)
with open(os.path.join(revs, "0.conf"), "w") as _f:
    _f.write("# first commit\n! cgnat\n")
with open(os.path.join(revs, "1.conf"), "w") as _f:
    _f.write("! cgnat\n")
sh = shell()
sh.rb = prismash.Rollback(path=revs)
with spoken() as o:
    sh.show_rollback()
text = rows_of(o.getvalue())
check(len(text) == 2 and text[0].startswith("   0  ")
      and text[0].endswith("  current  first commit")
      and text[1].startswith("   1  ") and text[1].endswith("  1 commit(s) ago"),
      f"{text!r}")
sh.rb = prismash.Rollback(path=os.path.join(_root, "revs-empty"))
with spoken() as o:
    sh.show_rollback()
check(o.getvalue() == "no committed revisions\n", o.getvalue())
ok("0 is now, 1 is the one before")

# --------------------------------------------------------- AS paths

print("\n=== Shell.received_as_paths: every rendering FRR uses ===")
# Mutation shown red: the `aspath` dict form dropped.
sh = shell(frr=frr_stub({"show bgp ipv4 unicast json": BGP}))
check(sorted(sh.received_as_paths()) == ["65001 65002", "65003", "65004"],
      f"path, aspath.string and aspath: {sh.received_as_paths()!r}")
check(shell(frr=frr_stub({})).received_as_paths() == [],
      "no BGP is no paths")
ok("what this box has been offered, as FRR renders it")

print("\n=== Shell.report_as_path_reach: matches N of M, or nothing to match ===")
# Mutation shown red: the `if not paths` report dropped.
sh = shell()
sh.received_as_paths = lambda: ["65001 65002", "65003"]
with spoken() as o:
    sh.report_as_path_reach(["bgp as-path access-list UP permit ^65001_"])
check(rows_of(o.getvalue()) == ["note: UP '^65001_' matches 1 of 2 received "
                                "AS path(s); e.g. '65001 65002'"],
      o.getvalue())
sh.received_as_paths = lambda: []
with spoken() as o:
    sh.report_as_path_reach(["bgp as-path access-list UP permit ^65001_"])
check(rows_of(o.getvalue())[0] == "note: no BGP paths received yet",
      f"nothing to match against is said, not '0 of 0': {o.getvalue()!r}")
with spoken() as o:
    sh.report_as_path_reach(["router bgp 65000"])
check(o.getvalue() == "", "no list, nothing to report")
ok("the regex against paths really received")

# -------------------------------------------------------- completion

print("\n=== Shell.complete: an option with a space, a hint without ===")
# Mutation shown red: `return m + " "` for a hint too.
sh = shell()
saved_rl = prismash.readline
try:
    prismash.readline = types.SimpleNamespace(get_line_buffer=lambda: "show cg")
    check(sh.complete("cg", 0) == "cgnat " and sh.complete("cg", 1) is None,
          f"the one option, a space after it, then nothing: "
          f"{(sh.complete('cg', 0), sh.complete('cg', 1))!r}")
    sh._options = lambda line, ending: ({}, "<address>", "")
    check(sh.complete("", 0) == "<address>" and sh.complete("", 1) == "​",
          f"a hint is shown and never inserted: "
          f"{(sh.complete('', 0), sh.complete('', 1))!r}")
finally:
    prismash.readline = saved_rl
ok("readline's completer, over _options")

print("\n=== Shell.prompt_name: the box's name in the prompt (#526) ===")
_asked = []


class _NameSys:
    """read_hostname() answering `name`, counting how often it is asked."""

    def __init__(self, name, broken=False):
        self.name, self.broken = name, broken

    def read_hostname(self):
        _asked.append(self.name)
        if self.broken:
            raise OSError("no door")
        return self.name


_ps = prismash.Shell()
_ps.sys = _NameSys("edge-sp-01")
check(_ps.prompt() == "edge-sp-01> " and _ps.prompt() == "edge-sp-01> "
      and _asked == ["edge-sp-01"],
      f"a named box's prompt, the host asked once for the session: {_asked}")
_ps.mode = "configuration"
check(_ps.prompt() == "edge-sp-01# [edit] ",
      f"and in configuration mode: {_ps.prompt()!r}")
for _name, _want in ((prismash.HOSTNAME_DEFAULT, "prisma> "), ("", "prisma> ")):
    _ps = prismash.Shell()
    _ps.sys = _NameSys(_name)
    check(_ps.prompt() == _want,
          f"the installer's name, or none read, is the product's: {_name!r} -> {_ps.prompt()!r}")
_saved_cn = prismash.Shell.__dict__["container_name"]
try:
    prismash.Shell.container_name = staticmethod(lambda: "abc123def456")
    _ps = prismash.Shell()
    _ps.sys = _NameSys("abc123def456")
    check(_ps.prompt() == "prisma> ",
          "a door nobody answered read the container's own id: not the box's name")
    prismash.Shell.container_name = staticmethod(lambda: "edge-lab")
    _ps = prismash.Shell()
    _ps.sys = _NameSys("edge-lab")
    check(_ps.prompt() == "edge-lab> ",
          "a container that is the box, renamed for real, keeps its name")
finally:
    prismash.Shell.container_name = _saved_cn
check(prismash.Shell.container_name() == ""
      or os.path.exists("/.dockerenv"),
      "container_name() is empty on a host, which is where this suite runs")
_ps = prismash.Shell()
_ps.sys = _NameSys("x", broken=True)
check(_ps.prompt_name() == "prisma", "a door that fails is the product's name, not a crash")
_long = "pop-" + "a" * 60
_ps = prismash.Shell()
_ps.sys = _NameSys(_long)
check(_ps.prompt_name() == _long[:19] + "~" and len(_ps.prompt_name()) == 20,
      f"a long name keeps its start and is marked cut: {_ps.prompt_name()!r}")
_ps.mode = "configuration"
_ps.nat_ctx = ["nat", "cgnat", "pool " + "r" * 60]
_p = _ps.prompt()
check(len(_p) == len("prisma# [edit ") + prismash.Shell.PROMPT_MAX + 2
      and "~# [edit ..." in _p,
      f"and the block path gives up the columns the name took ({len(_p)}): {_p!r}")
# A commit that renames the box sets the prompt in the same session.
_ps = prismash.Shell()
_ps.sys = _NameSys("old-name")
_ps.prompt()
_ps.sys.write_hostname = lambda n: (True, "")
_ps.frr = type("F", (), {"available": lambda self: False})()
check(_ps.apply_hostname("new-name") == (True, "")
      and _ps.prompt() == "new-name> ",
      f"a rename is the next prompt: {_ps.prompt()!r}")
_ps.sys.write_hostname = lambda n: (False, "refused")
check(_ps.apply_hostname("bad") == (False, "refused")
      and _ps.prompt() == "new-name> ",
      "a rename the host refused leaves the prompt as it was")

print("\n=== Shell.display_matches: the options, then the prompt again ===")
# Mutation shown red: the prompt redraw dropped.
sh = shell()
sh._last_opts = {"pool": "the pools", "session": "the sessions"}
sh._last_hint = None
saved_rl = prismash.readline
try:
    prismash.readline = types.SimpleNamespace(
        get_line_buffer=lambda: "show cgnat ")
    with spoken() as o:
        sh.display_matches("", [], 0)
    text = o.getvalue()
    check("  pool     the pools\n  session  the sessions\n" in text,
          f"aligned, with what each is: {text!r}")
    check(text.endswith("prisma> show cgnat "),
          f"the line being typed, redrawn: {text!r}")
    sh._last_opts, sh._last_hint = {}, None
    with spoken() as o:
        sh.display_matches("", [], 0)
    check("  (no known options here; the command is passed to FRR)"
          in rows_of(o.getvalue()), o.getvalue())
finally:
    prismash.readline = saved_rl
ok("what readline shows on a double tab")

# ------------------------------------------------------- abbreviations

print("\n=== takes_a_value: a node that offers the operator's word too ===")
# Mutation shown red: `k.startswith("<")` changed to `k.startswith("[")`.
check(prismash.takes_a_value(prismash.SHOW["interface"][1]),
      "`show interface` offers `terse` and every interface on the box")
check(not prismash.takes_a_value(prismash.INTERFACE_BLOCK),
      "an interface block's statements are all the box's own words")
check(not prismash.takes_a_value(prismash.TREE["operational"]),
      "so is every statement at the operational prompt")
ok("a dynamic key is what makes a node the operator's as well as the box's")

print("\n=== resolve_statement: the four rules, one box each ===")
# Mutation shown red: `if len(hits) > 1` changed to `if len(hits) > 2`, which
# resolved `ipv address ...` to one of the two families by position.
_abbrev = [
    # (node, typed, what the box reads)
    (prismash.INTERFACE_BLOCK, "no shut", "no shutdown"),
    (prismash.INTERFACE_BLOCK, "descr hello there", "description hello there"),
    (prismash.INTERFACE_BLOCK, "mt 9000", "mtu 9000"),
    (prismash.INTERFACE_BLOCK, "urp str", "urpf strict"),
    # Exact wins: `ip` is a statement of its own AND the start of three more.
    (prismash.INTERFACE_BLOCK, "ip ospf area 0", "ip ospf area 0"),
    # A word the tree has no entry for, and everything after it, verbatim.
    (prismash.INTERFACE_BLOCK, "bandwidth 1000", "bandwidth 1000"),
    (prismash.INTERFACE_BLOCK, "link-detect", "link-detect"),
    (prismash.TREE["operational"], "sho int", "show interface"),
    (prismash.TREE["operational"], "conf", "configure"),
    # Past a dynamic key nothing is resolved: `Te0` is a card, not `terse`.
    (prismash.TREE["operational"], "show interface Te0", "show interface Te0"),
    (prismash.TREE["config"], "interf ens18", "interface ens18"),
    (prismash.TREE["config"], "commi", "commit"),
    # `snmp` is a statement and the start of `snmp-user`; exact wins again.
    (prismash.TREE["config"], "snmp community public", "snmp community public"),
]
for _node, _typed, _want in _abbrev:
    _spelled, _amb = prismash.resolve_statement(_node, _typed.split())
    check(_amb is None and " ".join(_spelled) == _want,
          f"{_typed!r} reads as {_want!r}, got "
          f"{(_spelled and ' '.join(_spelled), _amb)!r}")
_spelled, _amb = prismash.resolve_statement(prismash.INTERFACE_BLOCK, ["i", "1"])
check(_spelled is None and _amb == ("i", ["ip", "ipv4", "ipv6", "isis"]),
      f"an ambiguity comes back with its candidates, got "
      f"{(_spelled, _amb)!r}")
# TWO is already an ambiguity, which is where the rule is: a box that only
# refused three or more would resolve `ipv` to one of the two families by
# position, and put an address on the wrong one.
_spelled, _amb = prismash.resolve_statement(prismash.INTERFACE_BLOCK,
                                          ["ipv", "address", "10.0.0.1/24"])
check(_spelled is None and _amb == ("ipv", ["ipv4", "ipv6"]),
      f"two candidates are an ambiguity too, got {(_spelled, _amb)!r}")
ok(f"{len(_abbrev)} statements resolved through the completion tree, and one "
   f"refused")

print("\n=== NOTHING FRR USES IS SHADOWED: every interface statement this "
      "repository writes ===")
# THE MEASUREMENT THAT DECIDES THE RULE (#393). A shadow needs an FRR
# statement that is a STRICT PREFIX of one of this tree's own words and is not
# itself in the tree; resolving such a word would silently hand FRR a
# different statement from the one typed, inside a commit. So the corpus is
# built rather than listed: every statement written inside an `interface`
# block anywhere in lab/, docs/ and wiki/, in both shapes this repository
# writes them - the block form a configuration file uses, and the `-c "..."`
# batches the labs drive the VTY with - plus FRR's own spellings this tree
# names. Each is resolved and asserted to come back EXACTLY as written.
#
# Mutation shown red: the exact-match-first rule in resolve_statement taken
# out, so `ip` became an ambiguity with `ipv4` and `ipv6` and 26 rows of the
# corpus - every `ip ospf` and `ip router isis` this repository writes - were
# refused at the prompt instead of reaching FRR.
_REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
_CLOSERS = {"exit", "quit", "end", "commit", "configure", "!", "save",
            "show", "write", "interface"}
_DASH_C = re.compile(r'-c\s+"([^"]*)"')
_HEAD = re.compile(r"^(\s*)interface\s+\S+\s*$")


def _closes(stmt):
    w = stmt.split()
    return (not w) or w[0] in _CLOSERS


def _iface_statements(path):
    """Every statement inside an `interface` block of one file."""
    try:
        lines = open(path, encoding="utf-8").readlines()
    except (UnicodeDecodeError, OSError):
        return []
    out = []
    # The `-c` form, with shell continuations joined so one command survives
    # a backslash newline.
    for line in "".join(lines).replace("\\\n", " ").split("\n"):
        inside = False
        for stmt in _DASH_C.findall(line):
            stmt = stmt.strip()
            if stmt.split()[:1] == ["interface"] and len(stmt.split()) == 2:
                inside = True
            elif not inside:
                continue
            elif _closes(stmt):
                inside = False
            else:
                out.append(stmt)
    # The block form: an `interface <name>` header, then the indented rows
    # under it to the first row that closes the block.
    i = 0
    while i < len(lines):
        if not _HEAD.match(lines[i].rstrip("\n")):
            i += 1
            continue
        j = i + 1
        while j < len(lines):
            raw = lines[j].rstrip("\n")
            if _closes(raw.strip()) or not raw.startswith(" "):
                break
            out.append(raw.strip())
            j += 1
        i = j
    return out


_corpus = []
for _dir in ("lab", "docs", "wiki"):
    for _dirpath, _, _names in os.walk(os.path.join(_REPO, _dir)):
        for _name in sorted(_names):
            _corpus += _iface_statements(os.path.join(_dirpath, _name))
check(len(_corpus) > 90,
      f"the corpus is read from the tree, and it is {len(_corpus)} rows")

#: FRR's own spellings, the ones this tree names and the ones an operator
#: writes beside them. They are here because a statement FRR takes and this
#: repository has not written down yet is exactly the one a prefix would
#: shadow without anybody noticing.
_FRR = [
    "ip ospf area 0", "ip ospf cost 10", "ip ospf network point-to-point",
    "ip ospf authentication message-digest", "ip ospf dead-interval 4",
    "ip ospf hello-interval 1", "ip ospf priority 0", "ip ospf bfd",
    "ip ospf retransmit-interval 5", "ipv6 ospf6 area 0",
    "ipv6 ospf6 cost 10", "ipv6 ospf6 network broadcast",
    "ip router isis HEXA", "ipv6 router isis HEXA",
    "isis circuit-type level-2-only", "isis network point-to-point",
    "isis metric 10", "isis hello-interval 3", "isis passive", "isis bfd",
    "bandwidth 1000", "link-detect", "multicast", "mpls enable",
    "ip address 10.0.0.1/24", "ipv6 address 2001:db8::1/64", "ip mtu 1400",
    "ip igmp", "ip pim", "ip rip split-horizon", "ipv6 nd ra-interval 10",
    "ipv6 nd prefix 2001:db8::/64", "ipv6 nd router-preference high",
    "ipv6 nd suppress-ra", "vrrp 1 ip 10.0.0.254", "vrrp 1 priority 200",
    "description a link", "shutdown", "no shutdown", "ptm-enable",
    "evpn mh es-id 1",
]
_changed = []
for _stmt in _corpus + _FRR:
    _spelled, _amb = prismash.resolve_statement(prismash.INTERFACE_BLOCK,
                                              _stmt.split())
    _got = f"ambiguous: {_amb[0]}" if _amb else " ".join(_spelled)
    if _got != _stmt:
        _changed.append((_stmt, _got))
check(not _changed, f"the rule changed {len(_changed)} statement(s): "
                    f"{_changed[:5]!r}")
ok(f"{len(_corpus) + len(_FRR)} interface statements "
   f"({len(set(_corpus + _FRR))} distinct) reach FRR exactly as written")

print("\n=== Shell.statement_grammar: where the box owns the words ===")
# Mutation shown red: the `interface ` test changed to `router `, which put
# `no shut` back on FRR's pile in silence.
_sh = shell()
_sh.mode = "operational"
check(_sh.statement_grammar() is prismash.TREE["operational"],
      "the operational prompt resolves against its own mode")
_sh.mode = "config"
_sh.frr_ctx = []
check(_sh.statement_grammar() is prismash.TREE["config"],
      "and so does the top level of configuration mode")
_sh.frr_ctx = ["interface pg0"]
check(_sh.statement_grammar() is prismash.PORT_BLOCK,
      "an interface block resolves against the block's own statements")
# A bundle's own statements only in a bundle (#455).
_sh.frr_ctx = ["interface ae0"]
check(_sh.statement_grammar() is prismash.INTERFACE_BLOCK,
      "and a bundle's block against the whole of it, member and lacp too")
for _block in ("router bgp 65001", "route-map RM permit 10",
               "router ospf", "vrf red"):
    _sh.frr_ctx = [_block]
    check(_sh.statement_grammar() is None,
          f"{_block!r} is FRR's whole, so nothing is resolved in it")
_sh.mode = "pool"
_sh.frr_ctx = []
check(_sh.statement_grammar() is None, "and nothing in a pool block yet")
ok("three places resolve, and every other block is handed over untouched")

print("\n=== Shell.spell_out: once, before dispatch ===")
# Mutation shown red: `line = self.spell_out(line)` taken out of feed(), which
# is the product exactly as #393 measured it.
_sh = shell()
_sh.mode = "config"
_sh.candidate = Config()
with spoken() as o:
    _sh.feed("interface pg0")
    _sh.feed("no shut")
    _sh.feed("descr uplink to the transit")
check(_sh.candidate.admin_down == set() and _sh.candidate.routing ==
      ["interface pg0"],
      f"`no shut` never reached FRR: {_sh.candidate.routing!r}")
check(_sh.candidate.descriptions == {"pg0": "uplink to the transit"},
      f"and `descr <text>` is the description: "
      f"{_sh.candidate.descriptions!r}")
with spoken() as o:
    _sh.feed("shut")
check(_sh.candidate.admin_down == {"pg0"},
      f"the positive statement too: {_sh.candidate.admin_down!r}")
# What `show this` and `save` write is the full spelling, never what was
# typed: the candidate has held nothing else since the resolution moved in
# front of the handlers.
with spoken() as o:
    _sh.feed("show this")
check(" shutdown" in rows_of(o.getvalue())
      and " description uplink to the transit" in rows_of(o.getvalue()),
      f"`show this` writes the statement out whole: {o.getvalue()!r}")
with spoken() as o:
    check(_sh.spell_out("i 1") is None,
          "an ambiguity is refused rather than dispatched")
check("error: an ambiguous abbreviation" in rows_of(o.getvalue()),
      o.getvalue())
ok("the abbreviation is gone before any handler, any screen or any file")


# ------------------------------------------------- reachability (#395)

print("\n=== probe_host_ok: an address, or a hostname, and nothing else ===")
for _good in ("1.1.1.1", "10.0.0.1", "2001:db8::1", "::1", "example.com",
              "a.example.com.", "x-1.co", "a" * 63 + ".com"):
    check(prismash.probe_host_ok(_good), f"{_good!r} is a host and was refused")
for _bad in ("", " ", "a b", "x;rm -rf /", "$(id)", "a..b", "-lead.com",
             "trail-.com", "a" * 64 + ".com", "a" * 254, "10.0.0.1/24",
             "hosté.com", "a|b"):
    check(not prismash.probe_host_ok(_bad), f"{_bad!r} is not a host and passed")
ok("a name is labels of letters, digits and hyphens; a shell's punctuation "
   "is not one")

print("\n=== probe_source_ok: an address, and not a name or a prefix ===")
check(prismash.probe_source_ok("10.0.0.1")
      and prismash.probe_source_ok("2001:db8::1"),
      "an address of either family is a source")
check(not prismash.probe_source_ok("10.0.0.1/24")
      and not prismash.probe_source_ok("example.com")
      and not prismash.probe_source_ok(""),
      "a prefix, a name and nothing are not")
ok("what goes in the packet's source field is one address, decided here")

print("\n=== probe_count_ok: a whole number inside the range, or None ===")
check(prismash.probe_count_ok("1") == 1
      and prismash.probe_count_ok("5") == 5
      and prismash.probe_count_ok(str(prismash.PROBE_COUNT_MAX))
      == prismash.PROBE_COUNT_MAX,
      "the ends of the range are inside it")
for _bad in ("0", str(prismash.PROBE_COUNT_MAX + 1), "+5", " 5", "5\n", "five",
             "", "5.0", "-1"):
    check(prismash.probe_count_ok(_bad) is None,
          f"{_bad!r} is not a count and was taken as one")
ok("the number the operator sees and the number the tool gets are the same")

print("\n=== probe_argv: the flags, which are asserted without running ===")
_p = prismash.PROBE_TOOLS["ping"][0]
check(prismash.probe_argv("ping", "1.1.1.1")
      == [_p, "-c", str(prismash.PROBE_COUNT_DEFAULT), "-n", "1.1.1.1"],
      f"ping, bounded and numeric: {prismash.probe_argv('ping', '1.1.1.1')}")
check(prismash.probe_argv("ping", "h", count=2, source="10.0.0.1")
      == [_p, "-c", "2", "-n", "-I", "10.0.0.1", "h"],
      "ping carries its source as -I")
_t = prismash.PROBE_TOOLS["traceroute"][0]
check(prismash.probe_argv("traceroute", "h", count=20, source="10.0.0.1")
      == [_t, "-q", "1", "-m", "20", "-n", "-s", "10.0.0.1", "h"],
      "traceroute counts hops, one query each, and carries -s")
_m = prismash.PROBE_TOOLS["mtr"][0]
check(prismash.probe_argv("mtr", "h", count=3)
      == [_m, "--report", "--report-cycles", "3", "-n", "h"],
      "mtr reports rather than opening curses on a serial console")
check(prismash.probe_argv("ping", "h")[-1] == "h"
      and prismash.probe_argv("traceroute", "h")[-1] == "h"
      and prismash.probe_argv("mtr", "h")[-1] == "h",
      "the host is last on every one, after every flag")
try:
    prismash.probe_argv("nmap", "h")
    check(False, "a tool this table has not got must not build a vector")
except KeyError:
    pass
ok("three vectors, built here, and a fourth tool cannot be asked for")

print("\n=== probe_options: pairs off the tail, and the word that is not ===")
check(prismash.probe_options([]) == (None, None, None), "nothing is nothing")
check(prismash.probe_options(["count", "3"]) == ("3", None, None), "count alone")
check(prismash.probe_options(["source", "10.0.0.1"]) == (None, "10.0.0.1", None),
      "source alone")
check(prismash.probe_options(["count", "3", "source", "10.0.0.1"])
      == ("3", "10.0.0.1", None), "both, in the order they were typed")
check(prismash.probe_options(["source", "10.0.0.1", "count", "3"])
      == ("3", "10.0.0.1", None), "and in the other order")
check(prismash.probe_options(["count"]) == (None, None, "count"),
      "an option with no value comes back as the word, not as a default")
check(prismash.probe_options(["-c", "3"]) == (None, None, "-c"),
      "a flag of the tool's own is not an option of this statement")
ok("an option missing its value is refused, never run with the default")

print("\n=== Shell.do_probe: the refusals, before anything is executed ===")


def _probe_says(line, needle, caps=None):
    # Through `feed`, because the resolution of an abbreviation and the
    # dispatch are both in front of this statement and a test that called the
    # method would measure neither. do_probe is ALSO called by name below, for
    # the reader that asks which functions no test names.
    _sh = shell()
    if caps is not None:
        _sh.caps = caps
    with spoken() as o:
        _sh.feed(line)
    check(needle in o.getvalue(),
          f"{line!r} did not say {needle!r}: {o.getvalue()!r}")


_probe_says("ping", "error: ping: no host")
_probe_says("traceroute", "usage: traceroute <host> [count <n>]")
_probe_says("ping x;rm", "error: ping: not a host")
_probe_says("ping 1.1.1.1 count 0", "error: ping: count out of range")
_probe_says("mtr 1.1.1.1 count 0", "mtr counts cycles and traceroute counts")
_probe_says("ping 1.1.1.1 count", "error: ping: not an option of this "
                                  "statement")
_probe_says("traceroute 1.1.1.1 source nope", "error: traceroute: not a "
                                              "source address")
# A READ-ONLY ACCOUNT MAY, which is the decision #395 records: these change
# nothing, and the operator asked whether the box reaches something is often
# the one who may not configure it.
_probe_says("ping", "error: ping: no host", caps=set())
# And by name, with the tool word this VTY dispatches on rather than the line
# an operator types: do_probe is one method serving three statements, so what
# it does with its FIRST argument is the half `feed` cannot vary.
_sh = shell()
with spoken() as o:
    _sh.do_probe("mtr", [])
check("error: mtr: no host" in o.getvalue()
      and "usage: mtr <host>" in o.getvalue(), o.getvalue())
with spoken() as o:
    _sh.do_probe("traceroute", ["1.1.1.1", "count", "999"])
check("error: traceroute: count out of range" in o.getvalue(), o.getvalue())
ok("every refusal is at the prompt, and a read-only account reaches them")

print("\n=== and a tool this box has not got is named with its package ===")
_saved_tools = dict(prismash.PROBE_TOOLS)
try:
    prismash.PROBE_TOOLS["mtr"] = (os.path.join(_root, "no-such-mtr"),
                                 "mtr-tiny")
    _probe_says("mtr 1.1.1.1", "error: mtr: this box has no mtr")
    _probe_says("mtr 1.1.1.1", "it comes from the 'mtr-tiny' package")
finally:
    prismash.PROBE_TOOLS.update(_saved_tools)
ok("a statement whose tool is absent says which package carries it")

print("\n=== Shell.run_probe: what it does with the tool it runs ===")
_sh = shell()
_out = script("probe-echo", "#!/bin/sh\nprintf 'ran: %s\\n' \"$*\"\n")
with spoken() as o:
    _sh.run_probe("ping", [_out, "-c", "1", "1.1.1.1"])
# NOT CAPTURED: the tool writes to this process's own stdout, which the
# redirect above is. What matters here is that the VTY added nothing of its
# own around it.
check(o.getvalue().strip() in ("", "ran: -c 1 1.1.1.1"),
      f"the VTY wrapped the tool's output: {o.getvalue()!r}")
_slow = script("probe-slow", "#!/bin/sh\nsleep 5\n")
_saved_deadline = prismash.PROBE_DEADLINE
try:
    prismash.PROBE_DEADLINE = 1
    with spoken() as o:
        _sh.run_probe("ping", [_slow])
    check("error: ping: stopped after 1s" in o.getvalue(),
          f"a tool that hangs is stopped and said so: {o.getvalue()!r}")
finally:
    prismash.PROBE_DEADLINE = _saved_deadline
# AND THE ONE THAT IS NOT THE TOOL'S FAULT: a vector whose program is not
# there at all. Driven because a refusal nobody has watched fire is one this
# repository cannot tell from a refusal that does not fire.
with spoken() as o:
    _sh.run_probe("ping", [os.path.join(_root, "no-such-probe"), "1.1.1.1"])
check("error: ping: cannot run its program" in o.getvalue(),
      f"a missing program is a refusal, not a traceback: {o.getvalue()!r}")


class _Interrupted:
    """`subprocess.run` as Ctrl-C at the console: the probe took the signal."""

    def __init__(self):
        self.called = 0

    def run(self, *a, **k):
        self.called += 1
        raise KeyboardInterrupt


_saved_run = prismash.subprocess.run
_int = _Interrupted()
try:
    prismash.subprocess.run = _int.run
    with spoken() as o:
        _sh.run_probe("ping", ["/bin/true"])
    check(_int.called == 1 and o.getvalue() == "\n",
          f"Ctrl-C comes back to the prompt and ends nothing: "
          f"{o.getvalue()!r}")
finally:
    prismash.subprocess.run = _saved_run
ok("the tool's output is the operator's, and neither a hang nor a Ctrl-C "
   "takes the session with it")


# ---------------------------------------------------------- the banner

print("\n=== Shell.say_handed_ssh_host_keys: only when it is true ===")
# Mutation shown red: `if not handed: return` dropped.
saved_origin = prismash.SSH_HOST_KEY_ORIGIN
try:
    prismash.SSH_HOST_KEY_ORIGIN = script(
        "origin-handed", "#!/bin/sh\n"
        "echo 'handed ssh_host_ed25519_key ssh_host_rsa_key'\n"
        "echo 'fix rm -f /etc/ssh/ssh_host_*'\necho 'fix ssh-keygen -A'\n")
    sh = shell()
    with spoken() as o:
        sh.say_handed_ssh_host_keys()
    text = rows_of(o.getvalue())
    check("This box's ssh host keys were not generated on it:" in text
          and any("ssh_host_ed25519_key ssh_host_rsa_key" in r for r in text)
          and "    rm -f /etc/ssh/ssh_host_*" in text
          and "    ssh-keygen -A" in text,
          f"the finding, the names, the repair as the holder gave it: "
          f"{text!r}")
    prismash.SSH_HOST_KEY_ORIGIN = script("origin-own", "#!/bin/sh\nexit 0\n")
    with spoken() as o:
        sh.say_handed_ssh_host_keys()
    check(o.getvalue() == "", "a box that made its own keys hears nothing")
finally:
    prismash.SSH_HOST_KEY_ORIGIN = saved_origin
ok("a banner, not a health line")

# ------------------------------------------------------------------ VRRP (#7)
#
# The virtual router is the DATAPLANE's, so every one of these goes through
# the binary API and the refusals are the ones an operator meets when it is
# not there, or not where they said it was.


# ------------------------------------------- the peer's mapping, at commit (#8)

PAIR_POOL = in_nat_block("nat cgnat pool residential\n"
             " inside 100.64.0.0/26\n"
             " outside 200.150.16.0/28\n"
             " block-size 2048\n"
             " mode deterministic\n"
             " exit\n")


def with_peer(text):
    """PEER_CONFIG_PATH pointed at a file holding `text`, or at nothing."""
    path = os.path.join(_root, "peer.conf")
    if text is None:
        if os.path.exists(path):
            os.unlink(path)
    else:
        with open(path, "w") as f:
            f.write(text)
    prismash.PEER_CONFIG_PATH = path
    return path


def in_a_pair(text):
    """A candidate with that text AND a virtual router, which is the pair."""
    cfg = Config.parse(text)
    cfg.vrrp["pg0"] = {1: {"addresses": ["100.64.3.254"], "priority": 200,
                           "track": {}}}
    return cfg


print("\n=== Shell.peer_divergence: only in a pair, only when it can read ===")
# Mutation shown red: the `target.vrrp` guard dropped, which gates the commits
# of every box that ever copied a peer file to compare against.
_saved_peer = prismash.PEER_CONFIG_PATH
try:
    with_peer(PAIR_POOL)
    sh = shell()
    same = in_a_pair(PAIR_POOL)
    check(sh.peer_divergence(same) == [],
          f"a pair that agrees has nothing to say: "
          f"{sh.peer_divergence(same)!r}")
    other = in_a_pair(PAIR_POOL.replace("block-size 2048", "block-size 1024"))
    found = sh.peer_divergence(other)
    check(len(found) == 1 and found[0][0] == "residential",
          f"and a block size that moves every subscriber is a finding: "
          f"{found!r}")
    check("block-size 1024" in found[0][1] and "block-size 2048" in found[0][2],
          f"with what each box makes of it: {found[0]!r}")
    not_a_pair = Config.parse(PAIR_POOL.replace("block-size 2048", "block-size 1024"))
    check(sh.peer_divergence(not_a_pair) == [],
          "a box with no virtual router is not in a pair, whatever files "
          "somebody left in /etc")
    with_peer(None)
    check(sh.peer_divergence(other) == [],
          "no peer configuration is an unanswered question, not a divergence")
    with_peer("! nothing but a comment\n")
    check(sh.peer_divergence(other) == [],
          "and neither is one that names no pool")
finally:
    prismash.PEER_CONFIG_PATH = _saved_peer
print("  the one place that decides a box is in a pair and disagrees")

print("\n=== Shell.say_divergence_refusal: the verdict, the pools, the word ===")
# Mutation shown red: the last row dropped, which leaves an operator refused
# with no way to say they meant it.
sh = shell()
sh.diverge = [("residential", "block-size 1024", "block-size 2048")]
with spoken() as o:
    sh.say_divergence_refusal()
text = o.getvalue()
check(text.splitlines()[0] == ("error: this change maps subscribers "
                               "differently from the peer"),
      f"the verdict first: {text.splitlines()[0]!r}")
check("block-size 1024" in text and "block-size 2048" in text,
      f"both statements, each whole: {text!r}")
check("commit diverge" in text.splitlines()[-1],
      f"and the word that gets past it, last: {text.splitlines()[-1]!r}")
print("  the same renderer 'show cgnat ha' uses, under a refusal")

print("\n=== Shell.report_divergence: what 'commit check' says instead ===")
# Mutation shown red: the empty-list return dropped, which prints a heading
# with no findings under it on every check.
sh = shell()
sh.diverge = []
with spoken() as o:
    sh.report_divergence()
check(o.getvalue() == "", "nothing to say when the pair agrees")
sh.diverge = [("residential", "block-size 1024", "block-size 2048")]
with spoken() as o:
    sh.report_divergence()
check("commit diverge" in o.getvalue() and "refuses" in o.getvalue(),
      f"and a warning that names what the commit will do: {o.getvalue()!r}")
print("  a check applies nothing, so it warns where a commit refuses")

print("\n=== Shell.device_vrrp: the virtual routers the BOX holds ===")
# Mutation shown red: the tracking dump left out, which would make every
# commit after a reboot remove the tracking nobody touched.
api = Api(have={"pg0": 1, "pg1": 2},
          rows={"vrrp_vr_dump": VRRP_ROWS,
                "vrrp_vr_track_if_dump": TRACK_ROWS})
sh = shell(dp=Dp({"show interface": IFACES}), api=api)
have = sh.device_vrrp()
check(have == {"pg0": {1: {"addresses": ["100.64.3.254"], "priority": 200,
                           "track": {"pg1": 150}}}},
      f"the address, the priority and what it watches: {have!r}")
sh = shell(dp=Dp({"show interface": IFACES}),
           api=Api(have={"pg0": 1}, refuse_dump={"vrrp_vr_dump"}))
check(sh.device_vrrp() == {}, "a refused dump is no virtual router, not a lie")
ok("read from the box, so an operator can take one off again")

print("\n=== Shell.device_vrrp_state: the role, and what it is advertising ===")
# Mutation shown red: `advertising` taken from the config rather than from
# the runtime, which is exactly the number tracking changes.
rows = [dict(VRRP_ROWS[0])]
rows[0] = dict(rows[0], runtime=dict(rows[0]["runtime"],
                                     tracking={"interfaces_dec": 1,
                                               "priority": 50}))
api = Api(have={"pg0": 1}, rows={"vrrp_vr_dump": rows})
sh = shell(dp=Dp({"show interface": IFACES}), api=api)
state = sh.device_vrrp_state()
check(len(state) == 1 and state[0]["advertising"] == 50
      and state[0]["configured"] == 200,
      f"advertising 50 against a configured 200: {state!r}")
check(state[0]["state"] == "master, holding the address",
      f"the state in the words an operator uses: {state[0]['state']!r}")
ok("a row per virtual router, with the number the election actually uses")

print("\n=== Shell.set_vrrp: the calls, in the order an election survives ===")
# Mutation shown red: vrrp_vr_start_stop dropped, which is a box that took
# the statement and never sends an advertisement.
api = Api(have={"pg0": 1, "pg1": 2})
sh = shell(dp=Dp({"show interface": IFACES}), api=api)
vr = {"addresses": ["100.64.3.254"], "priority": 200, "track": {"pg1": 150}}
check(sh.set_vrrp("pg0", 1, vr) is True, "it goes through")
sent = [m for m, _ in api.calls]
check(sent == ["vrrp_vr_start_stop", "vrrp_vr_add_del", "vrrp_vr_add_del",
               "vrrp_vr_track_if_add_del", "vrrp_vr_start_stop"],
      f"stopped and deleted, built, tracked, started: {sent!r}")
args = dict(api.calls[2][1])
check(args["flags"] == "VRRP_API_VR_PREEMPT",
      f"one flag, which is all the client can encode: {args['flags']!r}")
check(args["priority"] == 200 and args["addrs"] == ["100.64.3.254"],
      f"the priority and the address as configured: {args!r}")
check(api.calls[-1][1]["is_start"] == 1, "and it is started, not just built")
ok("replaced whole and started, because a VR nobody started elects nothing")

print("\n=== Shell.set_vrrp: what it refuses, and in whose words ===")
sh = shell(dp=Dp({"show interface": IFACES}), api=Api(have={}))
with spoken() as o:
    r = sh.set_vrrp("pg0", 1, vr)
check(r is False and "the dataplane does not have it" in o.getvalue(),
      f"an interface the box does not have: {o.getvalue()!r}")
sh = shell(dp=Dp({"show interface": IFACES}), api=Api(have={"pg0": 1}))
with spoken() as o:
    r = sh.set_vrrp("pg0", 1, {"addresses": [], "priority": 100, "track": {}})
check(r is False and "has no address" in o.getvalue(),
      f"a virtual router standing for nothing: {o.getvalue()!r}")
sh = shell(dp=Dp({"show interface": IFACES}),
           api=Api(have={"pg0": 1}, refuse={"vrrp_vr_add_del"}))
with spoken() as o:
    r = sh.set_vrrp("pg0", 1, vr)
check(r is False and "vrrp 1" in o.getvalue(),
      f"a dataplane that refused it: {o.getvalue()!r}")
sh = shell(dp=Dp({"show interface": IFACES}), api=Api(have={"pg0": 1}))
with spoken() as o:
    r = sh.set_vrrp("pg0", 1, vr)
check(r is False and "no interface pg1" in o.getvalue()
      and "was told to" in o.getvalue(),
      f"a tracked interface the box does not have: {o.getvalue()!r}")
sh = shell(dp=Dp({"show interface": IFACES}),
           api=Api(have={"pg0": 1, "pg1": 2},
                   refuse={"vrrp_vr_track_if_add_del"}))
with spoken() as o:
    r = sh.set_vrrp("pg0", 1, vr)
check(r is False and "vrrp 1 track pg1" in o.getvalue(),
      f"a refused tracking call: {o.getvalue()!r}")
sh = shell(dp=Dp({"show interface": IFACES}),
           api=Api(have={"pg0": 1, "pg1": 2},
                   refuse={"vrrp_vr_start_stop"}))
with spoken() as o:
    r = sh.set_vrrp("pg0", 1, vr)
check(r is False and "start:" in o.getvalue(),
      f"a router that could not be started: {o.getvalue()!r}")
ok("six refusals, each naming the interface and the virtual router")

print("\n=== Shell.say_vrrp_restart: what changing a router costs ===")
# Mutation shown red: the sentence reduced to "restarting vrrp 1". What an
# operator needs is not that it restarts - it is that the pair changes hands
# twice while it does, which is a maintenance window and not a keystroke.
sh = shell()
with spoken() as o:
    sh.say_vrrp_restart("pg0", 1)
said = " ".join(o.getvalue().split())
check("changing vrrp 1 on pg0 restarts it" in said, f"what it is: {said!r}")
check("the peer takes the address" in said and "preempt hands it back" in said,
      f"and what that costs the pair: {said!r}")
print("  a sentence about the service, not about the API call")

print("\n=== Shell.del_vrrp: stopped first, then deleted ===")
# Mutation shown red: the start_stop call dropped, which leaves the peer
# waiting out the master-down interval instead of taking it at once.
api = Api(have={"pg0": 1})
sh = shell(dp=Dp({"show interface": IFACES}), api=api)
check(sh.del_vrrp("pg0", 1) is True, "it goes through")
check([m for m, _ in api.calls] == ["vrrp_vr_start_stop", "vrrp_vr_add_del"],
      f"stopped, then deleted: {[m for m, _ in api.calls]!r}")
check(api.calls[0][1]["is_start"] == 0 and api.calls[1][1]["is_add"] == 0,
      f"and both in the removing direction: {api.calls!r}")
sh = shell(dp=Dp({"show interface": IFACES}), api=Api(have={}))
with spoken() as o:
    r = sh.del_vrrp("pg0", 1)
check(r is False and "the dataplane does not have it" in o.getvalue(),
      f"an interface that is not there: {o.getvalue()!r}")
sh = shell(dp=Dp({"show interface": IFACES}), api=Api(have={}))
check(sh.del_vrrp("pg0", 1, quiet=True) is True,
      "quiet: removing from an interface that is gone is not a failure")
sh = shell(dp=Dp({"show interface": IFACES}),
           api=Api(have={"pg0": 1}, refuse={"vrrp_vr_add_del"}))
with spoken() as o:
    r = sh.del_vrrp("pg0", 1)
check(r is False and "no vrrp 1" in o.getvalue(),
      f"a dataplane that refused the removal: {o.getvalue()!r}")
ok("an orderly withdrawal, and a refusal that names it")

print("\n=== Shell.device_vrrp_track: what one virtual router watches ===")
# Mutation shown red: the name lookup dropped, which would key the map by
# sw_if_index and make every commit see a change nobody made.
api = Api(have={"pg0": 1, "pg1": 2},
          rows={"vrrp_vr_track_if_dump": TRACK_ROWS})
sh = shell(dp=Dp({"show interface": IFACES}), api=api)
names = sh.interface_names_by_index()
check(sh.device_vrrp_track(1, 1, names) == {"pg1": 150},
      f"the interface by name, and the decrement: "
      f"{sh.device_vrrp_track(1, 1, names)!r}")
sh = shell(dp=Dp({"show interface": IFACES}),
           api=Api(have={"pg0": 1}, refuse_dump={"vrrp_vr_track_if_dump"}))
check(sh.device_vrrp_track(1, 1, {}) == {},
      "a refused dump is nothing watched")
print("  the half VPP keeps apart from the virtual router itself")

print("\n=== Shell.say_vrrp_refusal: the verdict, and the reason under it ===")
# Mutation shown red: err() for both halves, which is the 92-column row #355
# is about.
sh = shell()
with spoken() as o:
    sh.say_vrrp_refusal(("a verdict", "a reason that is long enough to be "
                                      "folded under it by the console"))
rows = rows_of(o.getvalue())
check(rows[0] == "error: a verdict", f"the verdict alone: {rows[0]!r}")
check(len(rows) >= 2 and rows[1].startswith("    "),
      f"the reason folded under it: {rows[1:]!r}")
sh = shell()
with spoken() as o:
    sh.say_vrrp_refusal(("a verdict on its own", ""))
check(rows_of(o.getvalue()) == ["error: a verdict on its own"],
      f"and one row when there is no prose: {o.getvalue()!r}")
print("  what config.py hands back, printed the way a console can take it")

print("\n=== Shell.interface_names_by_index: one dump for the whole map ===")
sh = shell(dp=Dp({"show interface": IFACES}),
           api=Api(have={"pg0": 1, "pg1": 2, "loop0": 3, "local0": 0}))
check(sh.interface_names_by_index() == {1: "pg0", 2: "pg1", 3: "loop0",
                                        0: "local0"},
      f"index to name: {sh.interface_names_by_index()!r}")
sh = shell(dp=Dp({"show interface": (1, "")}))
check(sh.interface_names_by_index() == {},
      "a dataplane that cannot be asked is an empty map")
ok("what a dump's sw_if_index is turned back into")

# ---------------------------------------------------------------------------
# Batch I: the words a configuration level owns (#400).
#
# Each of these is called on its own, standing at a level this box put it at
# by hand rather than by typing its way in - which is the point: the defect
# was a word that worked in ONE mode, so the boxes below stand in every mode
# there is and ask the same question in each.
# ---------------------------------------------------------------------------

CGNAT_AT = ("nat", "cgnat")   # standing in `nat` > `cgnat` (#446)


def at(mode="config", ctx=(), candidate=None, nat=()):
    """A Shell standing at a level, with a candidate to write into."""
    sh = shell()
    sh.mode = mode
    sh.frr_ctx = list(ctx)
    sh.nat_ctx = list(nat)
    sh.candidate = candidate if candidate is not None else Config()
    return sh


print("\n=== Shell.config_depth: a block and a sub-mode are one level each ===")
# Mutation shown red: `BLOCK_MODES` written out again as ("pool", "user",
# "snmp_user"), the spelling the mode does NOT use - which answers 0 inside an
# SNMP user, so `top` there silently stays put.
check(at().config_depth() == 0, "the top of configuration mode is depth 0")
check(at(ctx=["router bgp 65000"]).config_depth() == 1, "one block, one level")
check(at(ctx=["router bgp 65000",
             "address-family ipv4 unicast"]).config_depth() == 2,
      "a block inside a block is two")
for mode in prismash.BLOCK_MODES:
    check(at(mode=mode).config_depth() == 1, f"the '{mode}' sub-mode is a level")
check(at(mode="operational").config_depth() == 0,
      "operational mode is not below anything")
ok("every level this VTY has, counted the one way")

print("\n=== Shell.leave_one_level: the block's own exit goes with it ===")
# Mutation shown red: the `exit` not appended, which is #180 - the next
# statement typed lands inside the block that was left.
sh = at(ctx=["router bgp 65000", "address-family ipv4 unicast"])
sh.leave_one_level()
check(sh.frr_ctx == ["router bgp 65000"], f"one popped: {sh.frr_ctx!r}")
check(sh.candidate.routing == ["exit"],
      f"and FRR's own exit written: {sh.candidate.routing!r}")
sh = at(ctx=[prismash.STATIC_BLOCK])
sh.leave_one_level()
check(sh.candidate.routing == [],
      f"'{prismash.STATIC_BLOCK}' is this VTY's block, so there is nothing "
      f"for FRR to close: {sh.candidate.routing!r}")
sh = at(mode="pool")
sh.pool = Pool("residential")
sh.leave_one_level()
check(sh.mode == "config" and sh.pool is None,
      f"a sub-mode is left by forgetting it: {sh.mode} {sh.pool!r}")
ok("one level, whichever kind of level it was")

print("\n=== Shell.do_navigate: top, up, and up <n> ===")
# Mutation shown red: `levels > depth` allowed through, which pops an empty
# list and ends the session on an IndexError.
sh = at(ctx=["router bgp 65000", "address-family ipv4 unicast"])
with spoken() as o:
    sh.do_navigate(["top"])
check(sh.config_depth() == 0 and not o.getvalue(),
      f"top goes all the way up and says nothing: {o.getvalue()!r}")
check(sh.candidate.routing == ["exit", "exit"],
      f"closing each block on the way: {sh.candidate.routing!r}")
sh = at(mode="user")
with spoken() as o:
    sh.do_navigate(["top"])
check(sh.mode == "config", "top leaves a sub-mode as well as a block")
sh = at()
with spoken() as o:
    sh.do_navigate(["top"])
check(sh.mode == "config" and not o.getvalue(),
      f"and top at the top is not a refusal: {o.getvalue()!r}")
sh = at(ctx=["router bgp 65000", "address-family ipv4 unicast"])
with spoken() as o:
    sh.do_navigate(["up"])
check(sh.frr_ctx == ["router bgp 65000"] and not o.getvalue(),
      f"up is one level: {sh.frr_ctx!r} {o.getvalue()!r}")
sh = at(ctx=["router bgp 65000", "address-family ipv4 unicast"])
with spoken() as o:
    sh.do_navigate(["up", "2"])
check(sh.config_depth() == 0 and not o.getvalue(),
      f"up 2 is two of them: {sh.config_depth()} {o.getvalue()!r}")
check(sh.mode == "config", "and it is still configuration mode afterwards")
for word, why in (("0", "zero levels"), ("x", "a word"), ("-1", "a negative")):
    sh = at(ctx=["router bgp 65000"])
    with spoken() as o:
        sh.do_navigate(["up", word])
    check(sh.config_depth() == 1 and "error:" in o.getvalue(),
          f"{why} moves nothing and is named: {o.getvalue()!r}")
sh = at(ctx=["router bgp 65000"])
with spoken() as o:
    sh.do_navigate(["up", "1", "2"])
check(sh.config_depth() == 1 and "usage: up" in o.getvalue(),
      f"two arguments are not a level count: {o.getvalue()!r}")
sh = at(ctx=["router bgp 65000"])
with spoken() as o:
    sh.do_navigate(["up", "9"])
check(sh.config_depth() == 1 and "only 1 level(s)" in o.getvalue(),
      f"deeper than the tree is refused, not clamped: {o.getvalue()!r}")
check(sh.candidate.routing == [],
      f"and a refused up writes no exit at all: {sh.candidate.routing!r}")
sh = at()
with spoken() as o:
    sh.do_navigate(["up"])
check("already at the top" in o.getvalue(),
      f"up at the top says which word leaves the mode: {o.getvalue()!r}")
ok("moving inside the hierarchy, and never out of it by accident")

print("\n=== router static: the words are not refused by its grammar ===")
# Mutation shown red: the level words taken back out of `hierarchy_rewrite`'s
# pass-through list, which is how `top` came to work at four levels and be
# refused at the fifth - found by lab/vty.sh, not by a unit box, which is why
# there is one here now.
sh = at()
with spoken() as o:
    for _line in ("router static", "198.19.0.0/24 next-hop 10.0.0.2", "top"):
        sh.do_config(_line)
check(sh.config_depth() == 0 and "error" not in o.getvalue(),
      f"'top' leaves 'router static' like any other level: "
      f"{sh.config_depth()} {o.getvalue()!r}")
check(sh.candidate.routing == ["ip route 198.19.0.0/24 10.0.0.2"],
      f"and FRR's own spelling is what was staged, with no 'exit' for a block "
      f"FRR has not got: {sh.candidate.routing!r}")
sh = at()
sh.probes = []
sh.do_probe = lambda tool, args: sh.probes.append((tool, args))
with spoken() as o:
    for _line in ("router static", "ping 10.0.0.1"):
        sh.do_config(_line)
check(sh.probes == [("ping", ["10.0.0.1"])],
      f"and a probe is answered inside it: {sh.probes!r} {o.getvalue()!r}")
check(sh.candidate.routing == [],
      f"and staged as configuration nowhere: {sh.candidate.routing!r}")
ok("the fifth dispatcher, which the first pass of #400 missed")

print("\n=== Shell.owns_word: the same words at every level ===")
# Mutation shown red: `return False` for the probes, which is the defect -
# every level then hands `ping` to FRR as a routing statement.
sh = at(ctx=["router bgp 65000"])
with spoken():
    check(sh.owns_word(["top"]) is True, "top is this VTY's word")
sh = at(ctx=["router bgp 65000"])
with spoken():
    check(sh.owns_word(["up"]) is True, "and so is up")
sh = at()
sh.probes = []
sh.do_probe = lambda tool, args: sh.probes.append((tool, args))
for tool in prismash.PROBE_TOOLS:
    check(sh.owns_word([tool, "10.0.0.1"]) is True, f"{tool} is answered here")
check(sh.probes == [(t, ["10.0.0.1"]) for t in prismash.PROBE_TOOLS],
      f"each with the argument it was given: {sh.probes!r}")
check(at().owns_word(["router", "bgp", "65000"]) is False,
      "a statement is not one of them, and falls through untouched")
ok("one place, so no level can be the one that forgot")

print("\n=== Shell.level_words: what ? offers for them ===")
# Mutation shown red: `top` and `up` offered at depth 0, where the next
# keystroke refuses them.
check(set(at().level_words()) == set(prismash.PROBE_TOOLS),
      f"at the top, the probes and not the two that would be refused: "
      f"{sorted(at().level_words())!r}")
words = at(ctx=["router bgp 65000"]).level_words()
check(set(words) == set(prismash.PROBE_TOOLS) | {"top", "up"},
      f"inside a block, all of them: {sorted(words)!r}")
check(words["ping"] == prismash.TREE["operational"]["ping"][0],
      f"a probe described in the tree's own words: {words['ping']!r}")
check(all(words[k] for k in words), "and every word has something said about it")
ok("the list an operator reads while typing, and nothing refused in it")

print("\n=== unread_statements: the set a stray word lands in ===")
# Mutation shown red: the `path is None` test dropped, which names a block's
# own `exit` as a statement the VTY failed to read.
lines = ["router bgp 65000", " bgp router-id 10.0.0.1", "exit", "",
         "top", "ping 10.95.200.1"]
check(prismash.unread_statements(lines) == ["top", "ping 10.95.200.1"],
      f"the words that are not statements: "
      f"{prismash.unread_statements(lines)!r}")
check(prismash.unread_statements(["ip route 0.0.0.0/0 10.0.0.2"]) == [],
      "a statement the tree reads is not in it")
check(prismash.unread_statements([]) == [], "and nothing staged is nothing")
ok("the same walk the boundary marker does, read as a list")

print("\n=== Shell.say_frr_refused: where to look, and the way out ===")
# Mutation shown red: the ceiling dropped, which prints forty statements over
# a verdict nobody then reads.
sh = shell()
with spoken() as o:
    sh.say_frr_refused(["router bgp 65000", "exit", "top", "ping 10.0.0.1"])
said = o.getvalue()
check("top" in said and "ping 10.0.0.1" in said,
      f"the unread statements, named: {said!r}")
check("rollback 0" in said, f"and the way out: {said!r}")
check(all(len(r) <= 79 for r in rows_of(said)),
      f"on a console 79 columns wide: {[len(r) for r in rows_of(said)]!r}")
many = [f"word{n}" for n in range(prismash.UNREAD_NAMED + 3)]
with spoken() as o:
    sh.say_frr_refused(many)
said = o.getvalue()
check(f"and {len(many) - prismash.UNREAD_NAMED} more" in said,
      f"past the ceiling it stops naming and counts: {said!r}")
named = [r for r in rows_of(said) if r.strip().startswith("word")]
check(len(named) == prismash.UNREAD_NAMED,
      f"{prismash.UNREAD_NAMED} of them and no more: {named!r}")
with spoken() as o:
    sh.say_frr_refused(["ip route 0.0.0.0/0 10.0.0.2"])
said = o.getvalue()
check("rollback 0" in said and "unread" in said,
      f"with nothing unread, still the way out: {said!r}")
ok("the half FRR's own words do not say")

print("\n=== the prompt refuses the addresses a saved file refuses (#413) ===")
# Mutation shown red: any one call site reverted - `inside potato` is then
# staged at the prompt while a file refuses it, which is the disagreement #417
# exists to catch and the one this issue must not create.
#
# THE PROMPT HALF. The file half is in test_prismash_config.py, one rule and
# one function behind both.
_ADDR_PROMPT = [
    ("inside potato", "expected a prefix"),
    ("inside 300.0.0.0/10", "not an IPv4 prefix"),
    ("outside 200.0.0.1", "expected a prefix"),
    # An IPv6 INSIDE is a NAT64 pool since #485; an outside is IPv4 always.
    ("outside 2001:db8::/32", "expected an IPv4 prefix"),
    ("inside 100.64.0.5/10", "host bits are set"),
    ("bypass source 999.9.9.9/99", "not an IPv4 prefix"),
    ("bypass destination 2001:db8::/32", "expected an IPv4 prefix"),
]
for _line, _needle in _ADDR_PROMPT:
    _sh = shell()
    _sh.mode, _sh.pool = "pool", Pool("A")
    _rv, _text = driven(_sh.do_pool, _line)
    check(_needle in _text, f"{_line!r}: {_needle!r} not in {_text!r}")
    # SAID, not raised: the chain answers a ValueError with "invalid
    # argument: " in front, and these verdicts already carry a value of the
    # operator's own length (#355).
    check("invalid argument" not in _text,
          f"{_line!r}: said through err_prose, not raised: {_text!r}")
    check(not _sh.pool.inside and not _sh.pool.outside
          and not _sh.pool.bypass_src and not _sh.pool.bypass_dst,
          f"{_line!r}: and nothing was staged: {_sh.pool.lines()!r}")
# The control: the same statements with values a box can hold.
_sh = shell()
_sh.mode, _sh.pool = "pool", Pool("A")
for _line in ("inside 100.64.0.0/10", "outside 203.0.113.0/24",
              "bypass source 10.0.0.0/8", "bypass destination 192.0.2.0/24"):
    _rv, _text = driven(_sh.do_pool, _line)
    check(not _text, f"{_line!r} is a real statement and was refused: {_text!r}")
check(_sh.pool.inside == ["100.64.0.0/10"]
      and _sh.pool.bypass_src == ["10.0.0.0/8"],
      f"and each reached the candidate: {_sh.pool.lines()!r}")
ok(f"{len(_ADDR_PROMPT)} refused at the prompt, and four real ones staged")

print("\n=== and the collectors, which reached the exporter unread (#413) ===")
# Mutation shown red: the address_error call dropped from apply_logging_udp -
# `potato` then reaches VPP's set_ipfix_exporter as a collector address.
for _stmt, _what in (
        (["cgnat", "logging", "udp", "potato", "src", "10.0.0.1"],
         "the UDP collector"),
        (["cgnat", "logging", "udp", "10.0.0.9", "src", "potato"],
         "the source it exports from"),
        (["cgnat", "logging", "tcp", "potato"], "the TCP collector"),
        (["cgnat", "logging", "syslog", "potato", "enterprise", "12345"],
         "the syslog collector")):
    _sh = shell()
    _rv, _text = driven(_sh.apply_logging, _stmt)
    check("not an IP address" in _text,
          f"{_what}: {' '.join(_stmt)!r} was taken: {_text!r}")
ok("four collectors, none of them read as an address before this")

print("\n=== the prompt refuses the MACs a saved file refuses (#414) ===")
# Mutation shown red: the prompt's six-colon test left as it was, which is the
# disagreement between the doors that #417 exists to catch.
for _value, _needle in (("zz:zz:zz:zz:zz:zz", "not a hex octet"),
                        ("1:2:3:4:5:6789", "not a hex octet"),
                        ("ff:ff:ff:ff:ff:ff", "broadcast"),
                        ("01:00:5e:00:00:01", "group bit is set")):
    _sh = at()
    with spoken() as _o:
        _sh.do_config("interface pg0")
        _sh.do_config(f"mac-address {_value}")
    check(_needle in _o.getvalue(),
          f"{_value!r}: {_needle!r} not in {_o.getvalue()!r}")
    check(not _sh.candidate.macs,
          f"{_value!r}: and nothing staged: {_sh.candidate.macs!r}")
_sh = at()
with spoken() as _o:
    _sh.do_config("interface pg0")
    _sh.do_config("mac-address 2:0:0:0:0:1")
check(_sh.candidate.macs == {"pg0": "02:00:00:00:00:01"},
      f"and the short spelling is staged as the dataplane writes it: "
      f"{_sh.candidate.macs!r} {_o.getvalue()!r}")
ok("four refused at the prompt, and the short spelling normalised")
print("\n=== prismash.asn_findings: every place an AS is written (#415) ===")
# Mutation shown red: the `words_kept` tuple emptied, which refuses
# `remote-as external` - the ordinary way to run eBGP to a directly connected
# peer, and the exact defect #208 was filed for.
_ASN_ROUTING = [
    "router bgp 0",
    "router bgp 65000",
    "router bgp 23456",
    "neighbor 10.0.0.1 remote-as 99999999999",
    "neighbor 10.0.0.2 remote-as 65001",
    "neighbor vppwan0 interface remote-as external",
    "neighbor v6peers remote-as internal",
    "neighbor 10.0.0.9 remote-as auto",
    "set as-path prepend 65000 0 65000",
    "set as-path prepend 65000 65000",
    "set as-path exclude banana",
    "set as-path prepend last-as 3",
    "neighbor 10.0.0.3 remote-as 23456",
]
_found = prismash.asn_findings(_ASN_ROUTING)
_errors = [stmt for sev, stmt, _ in _found if sev == "error"]
_warnings = [stmt for sev, stmt, _ in _found if sev == "warning"]
check(_errors == ["router bgp 0",
                  "neighbor 10.0.0.1 remote-as 99999999999",
                  "set as-path prepend 65000 0 65000",
                  "set as-path exclude banana"],
      f"four statements carry an AS that is not one: {_errors!r}")
check(_warnings == ["router bgp 23456", "neighbor 10.0.0.3 remote-as 23456"],
      f"and AS_TRANS is a warning wherever it is written: {_warnings!r}")
# THE THREE SPELLINGS THAT ARE NOT NUMBERS, named one at a time, because
# reading them positionally is how #208 refused BGP unnumbered.
for _kept in ("neighbor vppwan0 interface remote-as external",
              "neighbor v6peers remote-as internal",
              "neighbor 10.0.0.9 remote-as auto"):
    check(_kept not in _errors and _kept not in _warnings,
          f"{_kept!r} is a real FRR spelling and must pass untouched")
check("set as-path prepend last-as 3" not in _errors,
      "`last-as` takes a COUNT and not an AS, so 3 is not read as one")
check(prismash.asn_findings([]) == [] and prismash.asn_findings([""]) == [],
      "and nothing to read is not a finding")
ok(f"{len(_errors)} refused, {len(_warnings)} said, and four spellings kept")

print("\n=== and the prompt refuses the stanza before entering it (#415) ===")
# Mutation shown red: the check moved below `self.frr_ctx.append(line)`, which
# leaves the operator standing inside a stanza the box has just refused - so
# every statement they type next lands in it.
for _line, _needle in (("router bgp 0", "out of range"),
                       ("router bgp 4294967296", "out of range"),
                       ("router bgp banana", "expected an AS number"),
                       ("router bgp", "expected an AS number")):
    _sh = at()
    with spoken() as _o:
        _sh.do_config(_line)
    check(_needle in _o.getvalue(),
          f"{_line!r}: {_needle!r} not in {_o.getvalue()!r}")
    check(_sh.frr_ctx == [],
          f"{_line!r}: and the operator is not left inside it: "
          f"{_sh.frr_ctx!r}")
    check(_sh.candidate.routing == [],
          f"{_line!r}: and nothing staged: {_sh.candidate.routing!r}")
_sh = at()
with spoken() as _o:
    _sh.do_config("router bgp 23456")
check(_sh.frr_ctx == ["router bgp 23456"] and "AS_TRANS" in _o.getvalue(),
      f"AS_TRANS is said and the block is entered: {_sh.frr_ctx!r} "
      f"{_o.getvalue()!r}")
_sh = at()
with spoken() as _o:
    _sh.do_config("router bgp 65000")
check(_sh.frr_ctx == ["router bgp 65000"] and not _o.getvalue(),
      f"and an ordinary AS is entered in silence: {_o.getvalue()!r}")
ok("four refused at the prompt, one said, one silent")

print("\n=== the prompt bounds the same text a saved file bounds (#416) ===")
# Mutation shown red: any one call site reverted, which is a disagreement
# between the doors - #417's whole subject.
#
# AND THE MODE MUST NOT MOVE. A name refused after `self.mode = "pool"` leaves
# the operator typing pool statements into a pool the box will not take, which
# is the same shape as a BGP stanza refused after its block is entered (#415).
for _line, _needle, _mode in (
        # 63, not the field's 64: the dataplane refuses a 64-character name
        # with retval -7, so the prompt refuses it first (#423).
        ("pool " + "p" * 200, "and the limit is 63", "config"),
        ("user " + "u" * 200, "and the limit is 32", "config")):
    _sh = at(nat=CGNAT_AT if _line.startswith("pool ") else ())
    with spoken() as _o:
        _sh.do_config(_line)
    check(_needle in _o.getvalue(),
          f"{_line[:24]!r}: {_needle!r} not in {_o.getvalue()!r}")
    check(_sh.mode == _mode,
          f"{_line[:24]!r}: the mode moved to {_sh.mode!r} on a refusal")
    check(not _sh.candidate.pools and not _sh.candidate.users,
          f"{_line[:24]!r}: and nothing was created")
# 63, not 64: VPP truncates a tag to 63 rather than refusing it, so the
# prompt is the only place that can say so (#424).
for _text, _needle in (("A" * 4000, "and the limit is 63"),
                       ("up\x07link", "control character")):
    _sh = at()
    with spoken() as _o:
        _sh.do_config("interface pg0")
        _sh.do_config(f"description {_text}")
    check(_needle in _o.getvalue(),
          f"description: {_needle!r} not in {_o.getvalue()!r}")
    check(not _sh.candidate.descriptions,
          f"description: staged anyway: {_sh.candidate.descriptions!r}")
# The control, and it is the accented one on purpose: the operator this box is
# sold to writes Portuguese.
_sh = at(nat=CGNAT_AT)
with spoken() as _o:
    _sh.do_config("pool residential")
check(_sh.mode == "pool" and not _o.getvalue(),
      f"a real pool name is taken and the mode moves: {_sh.mode!r}")
_sh = at()
with spoken() as _o:
    _sh.do_config("interface pg0")
    _sh.do_config("description uplink para o tr\u00e2nsito")
check(_sh.candidate.descriptions == {"pg0": "uplink para o tr\u00e2nsito"},
      f"and accented text is text: {_sh.candidate.descriptions!r} "
      f"{_o.getvalue()!r}")
ok("four refused at the prompt, the mode held, and accented text taken")

print("\n=== and the two identity statements, against their own fields ===")
# Mutation shown red: the text_error call dropped from apply_identity - a
# 1000-byte path then reaches a 256-byte field.
for _w, _needle in (
        # 255, not the field's 256: vpp_papi refuses the 256th character and
        # the dataplane copies into char[256] (#553).
        (["cgnat", "identity", "file", "/" + "d" * 1000],
         "and the limit is 255"),
        (["cgnat", "identity", "dhcp-leases", "/" + "d" * 1000],
         "and the limit is 255"),
        (["cgnat", "identity", "radius", "secret", "s" * 500],
         # 127, not the field's 128: a 128-character secret arrives at the box as
        # 127 through both doors, and the box then verifies nothing (#425).
        "and the limit is 127")):
    _sh = shell()
    _rv, _text = driven(_sh.apply_identity, _w)
    check(_needle in _text,
          f"{' '.join(_w)[:34]!r}: {_needle!r} not in {_text!r}")
ok("a path and a secret, each against the field bpa.api declares for it")

print("\n=== a cgnat global does not silently become zero (#418) ===")
# Mutation shown red: `int(w[3]) if is_number(w[3]) else 0` put back, which is
# the product as it shipped.
#
# THE THIRD DOOR. A global typed at the prompt is read before it is staged; the
# same line in a saved file goes into Config.globals UNPARSED and is applied at
# commit by apply_global. So apply_global is the file's only reader, and it
# coerced a word to zero - which in this message means "leave this one alone".
# A typo in a file changed nothing, said nothing, and left an operator reading
# a configuration whose timeout was not the one in force.
for _line, _needle in (
        ("cgnat timeout udp abc", "expected a number"),
        ("cgnat timeout udp 0", "expected 1-"),
        ("cgnat timeout tcp-established abc", "expected a number"),
        ("cgnat block-holddown abc", "expected a number"),
        ("cgnat block-holddown 0", "expected 1-")):
    _sh = shell()
    _rv, _text = driven(_sh.apply_global, _line)
    check(_needle in _text,
          f"{_line!r}: {_needle!r} not in {_text!r}")
    check(not _sh.api.calls,
          f"{_line!r}: and nothing was sent to the dataplane: "
          f"{_sh.api.calls!r}")
# The control: a real value still reaches the dataplane.
_sh = shell()
_rv, _text = driven(_sh.apply_global, "cgnat timeout udp 300")
check(not _text and _sh.api.calls,
      f"a real timeout is applied in silence: {_text!r} {_sh.api.calls!r}")
ok("five words that used to mean 'leave it alone', each refused now")

print("\n=== and the prompt refuses the NAME, not only the number (#418) ===")
# Mutation shown red: the TIMEOUT_FIELD test removed - apply_global then
# refuses it at commit, on a candidate the operator has already built.
_sh = at(nat=CGNAT_AT)
with spoken() as _o:
    _sh.do_config("timeout nosuch 5")
check("no such timeout" in _o.getvalue(),
      f"an unknown timeout is refused where it is typed: {_o.getvalue()!r}")
check(not _sh.candidate.globals,
      f"and nothing staged: {_sh.candidate.globals!r}")
_sh = at(nat=CGNAT_AT)
with spoken() as _o:
    _sh.do_config("timeout udp 300")
check(_sh.candidate.globals == ["cgnat timeout udp 300"] and not _o.getvalue(),
      f"a real one is staged in silence: {_sh.candidate.globals!r}")
ok("the name and the number, both where the operator can act on them")

print("\n=== Frr.dryrun: FRR's grammar, asked before a statement is staged "
      "(#431) ===")
# A stand-in vtysh answering in the words FRR 10.7.1 answered in, measured
# with no daemon running. It keeps the file it was handed, because the block
# path in that file is half of what is being asked.
# Mutations shown red: the returncode test inverted; the `%` regex's bracket
# group dropped (FRR's sentence is then not recognised, and a refusal is
# reported as not checked); the ctx lines left out of the file; the unlink
# removed. The temporary file goes to a directory of this suite's own, so
# "it is gone" is asked of files this run made and of nobody else's.
_kept = os.path.join(_root, "dryrun-kept.conf")
_drydir = os.path.join(_root, "dryrun-tmp")
os.makedirs(_drydir, exist_ok=True)


def _vtysh(name, rc, says):
    return script(name, "#!/bin/sh\n"
                  f"[ \"$1\" = --dryrun ] && [ \"$2\" = -f ] || exit 99\n"
                  f"cp \"$3\" '{_kept}'\n"
                  f"printf '%s\\n' {shlex.quote(says)}\n"
                  f"exit {rc}\n")


_saved_vtysh, _saved_tmp = prismash.VTYSH, tempfile.tempdir
try:
    tempfile.tempdir = _drydir
    prismash.VTYSH = _vtysh("vtysh-ok", 0, "")
    _v, _w = prismash.Frr().dryrun(["router bgp 65000",
                                    "address-family ipv4 unicast"],
                                   "network 10.0.0.0/8")
    check((_v, _w) == (prismash.Frr.DRYRUN_ACCEPTED, ""),
          f"rc 0 is accepted, with nothing to say: {(_v, _w)!r}")
    with open(_kept) as _f:
        _asked = _f.read()
    check(_asked == "router bgp 65000\n address-family ipv4 unicast\n"
                   "  network 10.0.0.0/8\n",
          f"and FRR was handed the block path, then the line: {_asked!r}")
    check(os.listdir(_drydir) == [],
          f"and the file it was handed is gone afterwards: "
          f"{os.listdir(_drydir)!r}")

    prismash.VTYSH = _vtysh(
        "vtysh-unknown", 2,
        "line 2: % Unknown command[28]: bananas\n"
        "Configuration file[/etc/frr/frr.conf] processing failure: 2")
    _v, _w = prismash.Frr().dryrun(["router bgp 65000"], "bananas")
    check((_v, _w) == (prismash.Frr.DRYRUN_REFUSED,
                       "% Unknown command: bananas"),
          f"an unknown command is refused in FRR's sentence, with no line "
          f"number of a file the operator never saw: {(_v, _w)!r}")

    prismash.VTYSH = _vtysh("vtysh-incomplete", 4,
                            "line 2: % Command incomplete[28]: neighbor")
    _v, _w = prismash.Frr().dryrun(["router bgp 65000"], "neighbor")
    check((_v, _w) == (prismash.Frr.DRYRUN_REFUSED,
                       "% Command incomplete: neighbor"),
          f"so is an incomplete one, which is rc 4 and not 2: {(_v, _w)!r}")

    # vtysh failing is not FRR refusing: no `%` sentence, no verdict.
    prismash.VTYSH = _vtysh("vtysh-broken", 1,
                            "vtysh: failed to open configuration file")
    _v, _w = prismash.Frr().dryrun([], "router bgp 65000")
    check((_v, _w) == (prismash.Frr.DRYRUN_UNCHECKED,
                       "vtysh: failed to open configuration file"),
          f"a vtysh that could not read the file checked nothing: {(_v, _w)!r}")

    prismash.VTYSH = os.path.join(_root, "no-such-vtysh")
    _v, _w = prismash.Frr().dryrun([], "router bgp 65000")
    check(_v == prismash.Frr.DRYRUN_UNCHECKED and "not found" in _w,
          f"and a box with no vtysh at all is not reported as checked: "
          f"{(_v, _w)!r}")
finally:
    prismash.VTYSH, tempfile.tempdir = _saved_vtysh, _saved_tmp
ok("accepted, refused in FRR's words, or not asked - never a guess between")

print("\n=== the prompt refuses what FRR refuses, and stages nothing (#431) ===")
# Mutations shown red: the dryrun call removed from do_config (the refused
# line is staged, and `router ospf vrf X` leaves the operator inside it);
# DRYRUN_UNCHECKED staged with no note.
_sh = at(ctx=["router bgp 65000"])
_sh.frr = RecFrr(refuses={"bananas": "% Unknown command: bananas"})
with spoken() as _o:
    _sh.do_config("bananas")
check("not a statement FRR accepts here" in _o.getvalue()
      and "% Unknown command: bananas" in _o.getvalue(),
      f"a statement FRR refuses is refused, in FRR's words: "
      f"{_o.getvalue()!r}")
check(_sh.candidate.routing == [],
      f"and nothing was staged: {_sh.candidate.routing!r}")
check(_sh.frr.dry == [(["router bgp 65000"], "bananas")],
      f"and FRR was asked with the block the operator is in: {_sh.frr.dry!r}")
# A block FRR refuses is not entered: the next line would land inside it.
_sh = at()
_sh.frr = RecFrr(refuses={"router ospf vrf": "% Unknown command: x"})
with spoken() as _o:
    _sh.do_config("router ospf vrf nosuch")
check(_sh.frr_ctx == [] and _sh.candidate.routing == [],
      f"a block FRR refuses is neither staged nor entered: "
      f"{_sh.frr_ctx!r} {_sh.candidate.routing!r}")
# The control: what FRR accepts is staged exactly as before, in silence.
_sh = at(ctx=["router bgp 65000"])
_sh.frr = RecFrr()
with spoken() as _o:
    _sh.do_config("neighbor 192.0.2.1 remote-as 65001")
check(_sh.candidate.routing == ["neighbor 192.0.2.1 remote-as 65001"]
      and not _o.getvalue(),
      f"a statement FRR accepts is staged in silence: "
      f"{_sh.candidate.routing!r} {_o.getvalue()!r}")
# Inside `router static`, FRR is asked about the `ip route` the line becomes
# and NOT about the block, which is this VTY's and which FRR does not have.
# lab/vty.sh found it: with the block in the path FRR refused `router
# static` itself, and every static route with it.
# Mutation shown red: the STATIC_BLOCK filter removed from the dryrun call.
_sh = at(ctx=["router static"])
_sh.frr = RecFrr()
_sh.do_config("10.9.0.0/16 next-hop 10.0.0.9")
check(_sh.frr.dry == [([], "ip route 10.9.0.0/16 10.0.0.9")],
      f"a static route is asked about as FRR stores it, outside any block: "
      f"{_sh.frr.dry!r}")
# Removing a statement the candidate holds asks nobody: it was asked about
# when it went in.
_sh = at(candidate=Config())
_sh.candidate.routing = ["ip prefix-list P seq 5 permit 10.0.0.0/8"]
_sh.frr = RecFrr()
_sh.do_config("no ip prefix-list P seq 5 permit 10.0.0.0/8")
check(_sh.candidate.routing == [] and _sh.frr.dry == [],
      f"a `no` that removes a staged line is not sent to FRR: "
      f"{_sh.candidate.routing!r} {_sh.frr.dry!r}")


class _Unasked(RecFrr):
    def dryrun(self, ctx, line):
        return prismash.Frr.DRYRUN_UNCHECKED, "vtysh not found"


_sh = at()
_sh.frr = _Unasked()
with spoken() as _o:
    _sh.do_config("ip prefix-list P seq 5 permit 10.0.0.0/8")
check(_sh.candidate.routing == ["ip prefix-list P seq 5 permit 10.0.0.0/8"]
      and "WITHOUT being checked" in " ".join(_o.getvalue().split())
      and "vtysh not found" in " ".join(_o.getvalue().split()),
      f"FRR not asked: staged, and the prompt says it was not checked: "
      f"{_sh.candidate.routing!r} {_o.getvalue()!r}")
ok("refused where it is typed, or staged and said to be unchecked")

print("\n=== cgnat_statement_short: a cgnat statement is walked to its end "
      "(#431) ===")
# Mutations shown red: the `isinstance(node, str)` branch returning () with
# no value given (`cgnat interface` staged again); the membership test on
# the dict dropped (the walk then fails on `foo` with a KeyError).
for _words, _needle in (
        (["interface"], "'cgnat interface' needs a value"),
        (["pool"], "'cgnat pool' needs a value"),
        (["timeout"], "'cgnat timeout' needs one of: icmp,"),
        (["timeout", "udp"], "'cgnat timeout udp' needs a value"),
        (["logging"], "'cgnat logging' needs one of:"),
        (["logging", "foo"], "'foo' is not one of them"),
        (["session-table"], "'cgnat session-table' needs a value")):
    _got = " ".join(prismash.cgnat_statement_short(_words))
    check(_needle in _got, f"{_words!r}: {_needle!r} not in {_got!r}")
for _words in (["timeout", "udp", "60"], ["logging", "enable"],
               ["logging", "udp", "192.0.2.9", "src", "10.0.0.1"],
               ["identity", "none"], ["block-holddown", "30"]):
    check(prismash.cgnat_statement_short(_words) == (),
          f"{_words!r} is a whole statement and is not refused")
_sh = at(nat=CGNAT_AT)
with spoken() as _o:
    _sh.do_config("interface")
check("not a whole cgnat statement" in _o.getvalue()
      and not _sh.candidate.globals and not _sh.candidate.interfaces,
      f"`cgnat interface` with no name is refused at the prompt: "
      f"{_o.getvalue()!r} {_sh.candidate.globals!r}")
ok("halfway is refused, and so is a word the table does not have")

print("\n=== BANNER: fits a serial console, in characters it can draw (#433) ===")
# Mutation shown red: the 115-column block-character candidate the
# maintainer sent first, put in BANNER - all four checks.
_rows = prismash.BANNER.split("\n")
check(max(len(r) for r in _rows) <= 79,
      f"every banner row fits a 79-column console: "
      f"{max(len(r) for r in _rows)} at its widest")
check(prismash.BANNER.isascii(),
      "the banner is plain ASCII, which a BMC's serial console can draw: "
      + repr(sorted({c for c in prismash.BANNER if not c.isascii()})))
check(len(_rows) <= 8,
      f"and no taller than the signature it replaced, so the lines under it "
      f"stay on the screen: {len(_rows)} rows")
_kit = os.path.join(os.path.dirname(prismash.__file__), "..", "..", "docs",
                    "brand", "07-ascii", "prisma-routing-wordmark.txt")
with open(_kit) as _f:
    check([r.rstrip() for r in _f.read().rstrip("\n").split("\n")] == _rows,
          "and it is the brand kit's wordmark, row for row")
ok("the banner is measured, not assumed")

print("\n=== sigint_ignored: a commit cannot be stopped halfway (#441) ===")
# The terminal sends SIGINT to the whole foreground group, so what matters is
# that the CHILD a commit runs survives it, not only this process.
# Mutation shown red: SIG_IGN replaced by a Python handler that swallows the
# signal - this process survives and the child dies, which is the half-applied
# commit the decorator exists to prevent.
import signal as _signal
import subprocess as _sp


def _applying_body():
    os.kill(os.getpid(), _signal.SIGINT)     # the key, arriving here
    time.sleep(0.05)
    child = _sp.run(["sh", "-c", "kill -INT $$; echo survived"],
                    capture_output=True, text=True)
    return child.stdout.strip()


_applying = prismash.sigint_ignored(_applying_body)
try:
    _said = _applying()
    check(_said == "survived",
          f"a child started while held survives SIGINT, as frr-reload must: "
          f"{_said!r}")
except KeyboardInterrupt:
    check(False, "the held function was interrupted by SIGINT")
check(_signal.getsignal(_signal.SIGINT) is _signal.default_int_handler,
      "and Ctrl-C is an interrupt again once it returns")


def _fails_body():
    raise ValueError("refused")


_fails = prismash.sigint_ignored(_fails_body)
try:
    _fails()
except ValueError:
    pass
check(_signal.getsignal(_signal.SIGINT) is _signal.default_int_handler,
      "including when the held function raises")
ok("held while it applies, and given back afterwards")

print("\n=== router static: next-hop, description, and one route per pair "
      "(#436) ===")
# Mutations shown red: the remove_static_route() call before staging taken
# out (re-typing a route adds a second one); its block_paths filter removed
# (a `no` in router static takes a vrf's route with the same words).
assert prismash._is_network("10.0.0.0/8") and prismash._is_network("::/0")
assert not prismash._is_network("banana")
_sh = at(ctx=["router static"])
_sh.frr = RecFrr()
with spoken() as _o:
    _sh.do_config("192.168.0.0/24 next-hop 172.16.0.1 distance 200 tag 100 "
                  "description link to POP-2")
_key = "ip route 192.168.0.0/24 172.16.0.1"
check(_sh.candidate.routing == [_key + " tag 100 200"] and not _o.getvalue(),
      f"staged in FRR's spelling, in silence: {_sh.candidate.routing!r}")
check(_sh.candidate.static_descriptions == {_key: "link to POP-2"},
      f"and its description kept by its key: "
      f"{_sh.candidate.static_descriptions!r}")
check(_sh.frr.dry == [([], _key + " tag 100 200")],
      f"FRR was asked about the route, never about the description: "
      f"{_sh.frr.dry!r}")
_sh.do_config("192.168.0.0/24 next-hop 172.16.0.1 tag 5")
check(_sh.candidate.routing == [_key + " tag 5"]
      and _sh.candidate.static_descriptions == {},
      f"re-typing the pair changes that route, it does not add one: "
      f"{_sh.candidate.routing!r} {_sh.candidate.static_descriptions!r}")
_sh.candidate.static_descriptions["ip route 172.16.0.0/12 blackhole"] = "x"
_sh.candidate.routing.append("ip route 172.16.0.0/12 blackhole tag 3")
_sh.remove_static_route("ip route 172.16.0.0/12 blackhole")
check(_sh.candidate.routing == [_key + " tag 5"]
      and "ip route 172.16.0.0/12 blackhole" not in
      _sh.candidate.static_descriptions,
      "remove_static_route takes the route by its key, tag and all, and its "
      "description with it")
_sh.candidate.routing += ["vrf red", " ip route 10.0.0.0/8 blackhole",
                          "exit-vrf", "ip route 10.0.0.0/8 blackhole"]
_sh.do_config("no 10.0.0.0/8 next-hop blackhole")
check(_sh.candidate.routing == [_key + " tag 5", "vrf red",
                                " ip route 10.0.0.0/8 blackhole", "exit-vrf"],
      f"'no' removes the global route and leaves the vrf's: "
      f"{_sh.candidate.routing!r}")
_sh = at(ctx=["router static"])
_sh.frr = RecFrr(refuses={"ip route 10.": "% Unknown command: x"})
_sh.do_config("10.1.0.0/16 next-hop 1.2.3.4 description never staged")
check(_sh.candidate.routing == [] and _sh.candidate.static_descriptions == {}
      and _sh.static_pending is None,
      f"a route FRR refuses leaves no description behind either: "
      f"{_sh.candidate.static_descriptions!r} {_sh.static_pending!r}")
_sh = at(ctx=["router static"])
for _typed, _needle in (("route 10.9.0.0/16 10.0.0.9", "'route' is not"),
                        ("ip route 10.9.0.0/16 10.0.0.9", "'route' is not"),
                        ("no banana", "starts with its prefix"),
                        ("banana", "not a statement of 'router static'")):
    with spoken() as _o:
        _sh.do_config(_typed)
    check(_needle in _o.getvalue() and _sh.candidate.routing == [],
          f"{_typed!r} is refused in the block: {_o.getvalue()!r}")

print("\n=== the description file: written at commit, read back filtered "
      "(#436) ===")
# Mutation shown red: read_static_descriptions() without the filter - a
# description of a route FRR no longer has comes back and is shown.
_desc_path = os.path.join(_root, "static-route-descriptions")
_saved_path = prismash.STATIC_DESC_PATH
prismash.STATIC_DESC_PATH = _desc_path
try:
    _sh = shell()
    check(_sh.read_static_descriptions(["ip route 10.0.0.0/8 1.2.3.4"]) == {},
          "no file is no descriptions, not a failure")
    check(_sh.write_static_descriptions(
              {"ip route 10.0.0.0/8 1.2.3.4": "to POP-1",
               "ip route 10.9.0.0/16 blackhole": "gone from FRR"}),
          "the file is written")
    check(not os.path.exists(_desc_path + ".tmp"),
          "by rename, with nothing left beside it")
    got = _sh.read_static_descriptions(
        ["ip route 10.0.0.0/8 1.2.3.4 tag 9 20", "vrf red",
         " ip route 10.9.0.0/16 blackhole", "exit-vrf"])
    check(got == {"ip route 10.0.0.0/8 1.2.3.4": "to POP-1"},
          f"read back for the global routes FRR has, and no others: {got!r}")
    prismash.STATIC_DESC_PATH = os.path.join(_root, "no-such-dir", "f")
    with spoken() as _o:
        _okw = _sh.write_static_descriptions({"k": "v"})
    check(not _okw and "not written" in _o.getvalue(),
          f"a file that cannot be written is said, not swallowed: "
          f"{_o.getvalue()!r}")
finally:
    prismash.STATIC_DESC_PATH = _saved_path
ok("a description is this box's, kept where the box keeps its own settings")

print("\n=== dns server at the prompt (#437) ===")
# Mutation shown red: the DNS_MAX test removed - a fourth server is staged,
# shown, and never asked by the resolver.
_sh = at()
for _typed in ("dns server 1.1.1.1", "dns server 8.8.8.8",
               "dns server 1.1.1.1"):
    _sh.do_config(_typed)
check(_sh.candidate.dns_servers == ["1.1.1.1", "8.8.8.8"],
      f"servers in the order typed, one each: {_sh.candidate.dns_servers!r}")
_sh.do_config("dns server 9.9.9.9")
with spoken() as _o:
    _sh.do_config("dns server 2606:4700::1111")
check(_sh.candidate.dns_servers == ["1.1.1.1", "8.8.8.8", "9.9.9.9"]
      and "already has 3" in _o.getvalue(),
      f"the fourth is refused and says why: {_o.getvalue()!r}")
_sh.dns_statement(["no", "dns", "server", "8.8.8.8"])
check(_sh.candidate.dns_servers == ["1.1.1.1", "9.9.9.9"],
      f"'no dns server' removes one: {_sh.candidate.dns_servers!r}")
for _typed, _needle in (("dns server dns.google", "expected an address"),
                        ("dns server", "usage: [no] dns server"),
                        ("dns resolver 1.1.1.1", "usage: [no] dns server"),
                        ("no dns server fe80::1%eth0", "without a scope")):
    with spoken() as _o:
        _sh.do_config(_typed)
    check(_needle in _o.getvalue()
          and _sh.candidate.dns_servers == ["1.1.1.1", "9.9.9.9"],
          f"{_typed!r} is refused, and nothing changes: {_o.getvalue()!r}")

print("\n=== system upgrade: from the releases directory, verified (#440) ===")
# A releases directory of this suite's own, served over HTTP on loopback, so
# what is asked is a download and not a function returning bytes.
# Mutations shown red, one at a time: the sha256 comparison removed (a
# package that does not match is unpacked and run); the older-version test
# removed (0.99.6 is installed over 0.99.7); extract_within() without its
# path test (a member at ../ lands outside the package).
import http.server as _hs
import tarfile as _tf
import threading as _th
import hashlib as _hl

assert prismash.version_tuple("0.122.9 (f8448d7)") == (0, 122, 9)
check(prismash.version_tuple("0.122.10") > prismash.version_tuple("0.122.9"),
      "0.122.10 is newer than 0.122.9: numbers, not strings")
for _bad in ("0.122", "banana", "", "0.\u0661.2"):
    check(prismash.version_tuple(_bad) is None, f"{_bad!r} is not a version")

_rel = os.path.join(_root, "releases")


def _package(version, rc=0, evil=None):
    name = f"prisma-routing-{version}-upgrade"
    src = os.path.join(_root, "pkgsrc", name)
    os.makedirs(os.path.join(src, "appliance"), exist_ok=True)
    with open(os.path.join(src, "appliance", "upgrade"), "w") as f:
        f.write(f"#!/bin/sh\necho UPGRADE-RAN {version}\nexit {rc}\n")
    if os.path.lexists(os.path.join(src, "upgrade")):
        os.unlink(os.path.join(src, "upgrade"))
    os.symlink("appliance/upgrade", os.path.join(src, "upgrade"))
    d = os.path.join(_rel, version)
    os.makedirs(d, exist_ok=True)
    tgz = os.path.join(d, name + ".tar.gz")
    with _tf.open(tgz, "w:gz") as t:
        t.add(src, arcname=name)
        if evil:
            info = _tf.TarInfo(evil)
            data = b"x"
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    with open(tgz, "rb") as f:
        digest = _hl.sha256(f.read()).hexdigest()
    with open(tgz + ".sha256", "w") as f:
        f.write(f"{digest}  {name}.tar.gz\n")
    return tgz


_package("0.99.8")
_package("0.99.9", rc=3)
_package("0.100.0")
os.symlink("0.100.0", os.path.join(_rel, "latest"))
_srv = _hs.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k:
    _hs.SimpleHTTPRequestHandler(*a, directory=_rel, **k))
_srv.RequestHandlerClass.log_message = lambda *a: None
_th.Thread(target=_srv.serve_forever, daemon=True).start()
_saved_url, _saved_work = prismash.RELEASES_URL, prismash.UPGRADE_WORK
prismash.RELEASES_URL = f"http://127.0.0.1:{_srv.server_address[1]}"
prismash.UPGRADE_WORK = os.path.join(_root, "upgrade-work")
os.makedirs(prismash.UPGRADE_WORK, exist_ok=True)
try:
    check(prismash.latest_release() == ("0.100.0", ""),
          f"the newest is read off latest/: {prismash.latest_release()!r}")
    check(prismash.latest_release(lambda u: (b"<html></html>", ""))[0] is None,
          "a latest/ with no package in it is no answer, said")
    check(prismash.fetch_url(prismash.RELEASES_URL + "/9.9.9/x")[0] is None,
          "a missing file is a sentence, not a traceback")

    # download_to() streams to disk and sums on the way (#427): bigger than
    # one read, so a loop that kept only the last chunk writes a short file
    # and a sum that is not the file's.
    _big = os.path.join(_rel, "big.bin")
    with open(_big, "wb") as _f:
        _f.write(os.urandom(3 * (1 << 20) + 12345))
    with open(_big, "rb") as _f:
        _want = _hl.sha256(_f.read()).hexdigest()
    _got_path = os.path.join(_root, "big.copy")
    _sum, _n, _why = prismash.download_to(
        prismash.RELEASES_URL + "/big.bin", _got_path)
    with open(_got_path, "rb") as _f:
        _copied = _hl.sha256(_f.read()).hexdigest()
    check(_sum == _want == _copied and _n == 3 * (1 << 20) + 12345
          and _why == "",
          f"download_to writes the whole file and returns its sum: "
          f"{_n} bytes, {_sum} against {_want}")
    _sum, _n, _why = prismash.download_to(
        prismash.RELEASES_URL + "/9.9.9/x", os.path.join(_root, "none"))
    check(_sum is None and _n == 0 and "HTTP 404" in _why,
          f"and a missing one is a sentence, not a traceback: {_why!r}")

    # WHO IS ASKING (#456). A server that refuses Python's default agent the
    # way the public releases directory does - measured: 403 for
    # Python-urllib, 200 for anything else - and both fetchers must get
    # through it. Mutation shown red: release_request() returning a bare
    # Request(url), which is the product as 0.127.1 shipped.
    _seen = []

    class _Picky(_hs.SimpleHTTPRequestHandler):
        def do_GET(self):
            _ua = self.headers.get("User-Agent", "")
            _seen.append(_ua)
            if _ua.startswith("Python-urllib"):
                self.send_error(403)
                return
            super().do_GET()

        def log_message(self, *a):
            pass
    _picky = _hs.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k:
                                     _Picky(*a, directory=_rel, **k))
    _th.Thread(target=_picky.serve_forever, daemon=True).start()
    _purl = f"http://127.0.0.1:{_picky.server_address[1]}"
    check(prismash.fetch_url(_purl + "/big.bin")[0] is not None,
          f"fetch_url gets past a server that refuses Python's agent: {_seen}")
    check(prismash.download_to(_purl + "/big.bin",
                               os.path.join(_root, "big.picky"))[0] == _want,
          f"and so does download_to: {_seen}")
    check(_seen and all(u.startswith("prisma-routing/") for u in _seen),
          f"every request names the product and its version: {_seen}")
    check(prismash.release_request("http://x/").get_header("User-agent")
          == f"prisma-routing/{prismash.prisma_routing_version()}",
          "release_request() carries the version this box runs")
    _picky.shutdown()

    _sh = shell()
    rv, text = driven(_sh.system_upgrade, [])
    check("this box runs: 0.99.7" in text and "newest published: 0.100.0" in text
          and "system upgrade 0.100.0" in text,
          f"with no version it says where the box is and where it could be: "
          f"{text!r}")
    for _v, _needle in (("0.99.7", "not newer than this box"), ("0.99.6", "not newer than this box"),
                        ("banana", "usage: system upgrade")):
        rv, text = driven(_sh.system_upgrade, [_v])
        check(_needle in text and "downloading" not in text,
              f"{_v!r} is refused before anything is downloaded: {text!r}")

    # The host's side of the last step (#528), stubbed: what is asked, and
    # what it answers.
    _asked = []
    _sh.sys._run = lambda argv, stdin=None, timeout=30: (
        _asked.append(tuple(argv)) or (0, ""))
    rv, text = driven(lambda: _sh.system_upgrade(["0.99.8"]))
    check("sha256" in text and "matches" in text
          and "UPGRADE-RAN 0.99.8" in text and "upgraded to 0.99.8" in text
          and "'save' before rebooting" in " ".join(text.split()),
          f"a release is downloaded, verified, and its own upgrade run: "
          f"{text!r}")
    check(_asked == [prismash.doors.REFRESH_VTY_ARGV]
          and "the VTY restarts on 0.99.8 in a few seconds" in " ".join(text.split())
          and "keep running the previous images until a reboot" in " ".join(text.split()),
          f"and then the VTY is put on it, detached, and the rest said to wait "
          f"for a reboot (#528): {_asked} {text!r}")
    # Mutation shown red: the rc test inverted - the refusal below reads as
    # a restart that is on its way.
    _sh.sys._run = lambda argv, stdin=None, timeout=30: (
        1, "Failed to start transient timer unit: Unit already exists.\n")
    rv, text = driven(lambda: _sh.refresh_the_vty("0.99.8"))
    _t = " ".join(text.split())
    check("could not be restarted on the new release" in _t
          and "Unit already exists" in _t and "restarts on" not in _t
          and "'save' before rebooting" in _t,
          f"a host that could not start it is quoted, and the VTY is said to "
          f"stay the previous release's until a reboot: {text!r}")
    # #529: the package's own script scheduled it, and said so - the VTY
    # does not ask the host a second time, and still says what happens.
    _asked.clear()
    _sh.sys._run = lambda argv, stdin=None, timeout=30: (
        _asked.append(tuple(argv)) or (0, ""))
    rv, text = driven(lambda: _sh.refresh_the_vty("0.99.8", scheduled=True))
    check(_asked == [] and "the VTY restarts on 0.99.8" in " ".join(text.split()),
          f"a refresh the upgrade scheduled is not asked for again: {_asked}")
    # The line it looks for is the one the upgrade artifact prints
    # (appliance/schedule-vty-refresh, run by appliance/upgrade), read off
    # the file: a rewording of either would leave the VTY asking twice.
    _up = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                            "appliance", "schedule-vty-refresh")).read()
    check(f'echo "{prismash.REFRESH_SCHEDULED}' in _up,
          "the upgrade artifact prints the line run_upgrade() reads")
    _sh.sys._run = lambda argv, stdin=None, timeout=30: (125, "")
    rv, text = driven(lambda: _sh.refresh_the_vty("0.99.8"))
    check("exit 125 and nothing else" in " ".join(text.split()),
          f"and a host that said nothing is named as such: {text!r}")
    check(os.listdir(prismash.UPGRADE_WORK) == [],
          "and the unpacked package is gone once it succeeded")

    rv, text = driven(_sh.system_upgrade, ["0.99.9"])
    check("UPGRADE-RAN 0.99.9" in text and "stopped with status 3" in " ".join(text.split())
          and "upgraded to" not in text,
          f"upgrade's own refusal is shown, with its status: {text!r}")
    check(len(os.listdir(prismash.UPGRADE_WORK)) == 1,
          "and the package is kept for whoever reads it next")
    for _d in os.listdir(prismash.UPGRADE_WORK):
        shutil.rmtree(os.path.join(prismash.UPGRADE_WORK, _d))

    # A package that does not match its sum is refused, and not unpacked.
    _t = os.path.join(_rel, "0.100.0", "prisma-routing-0.100.0-upgrade.tar.gz")
    with open(_t + ".sha256", "w") as _f:
        _f.write("0" * 64 + "  x\n")
    rv, text = driven(lambda: _sh.run_upgrade("0.100.0"))
    check("does not match its sha256" in text and "UPGRADE-RAN" not in text
          and os.listdir(prismash.UPGRADE_WORK) == [],
          f"a sum that does not match: refused, nothing unpacked: {text!r}")

    _cg = shell()
    _cg.caps = {prismash.CAP_CGNAT}
    rv, text = driven(_cg.system_upgrade, ["0.100.0"])
    check("downloading" not in text and "permission" in text.lower(),
          f"a CGNAT account is refused before anything is asked: {text!r}")

    _evil = _package("0.101.0", evil="../outside-the-package")
    _dest = os.path.join(_root, "extract-here")
    os.makedirs(_dest, exist_ok=True)
    why = prismash.extract_within(_evil, _dest)
    check(why and "outside the package" in why
          and not os.path.exists(os.path.join(_root, "outside-the-package")),
          f"a member at ../ is refused and nothing is written: {why!r}")
    _ok_pkg = os.path.join(_rel, "0.99.8", "prisma-routing-0.99.8-upgrade.tar.gz")
    _dest2 = os.path.join(_root, "extract-ok")
    os.makedirs(_dest2, exist_ok=True)
    check(prismash.extract_within(_ok_pkg, _dest2) is None and os.path.exists(
          os.path.join(_dest2, "prisma-routing-0.99.8-upgrade", "upgrade")),
          "a package whose link stays inside unpacks whole")
finally:
    prismash.RELEASES_URL, prismash.UPGRADE_WORK = _saved_url, _saved_work
    _srv.shutdown()
ok("downloaded, verified, and run through the upgrade a hand would run")

print("\n=== a dynamic pool nothing exports is said at commit (#444) ===")
# Mutations shown red, one at a time: the UDP test without its collector
# half (enable alone passes as exported); tcp/syslog `disable` counted as a
# collector; the mode test dropped (a deterministic pool is warned).
_dyn = Pool("dinamico")
_dyn.mode = "dynamic"
_det = Pool("residencial")
_def = prismash.dynamic_export_findings
check(_def({"residencial": _det}, []) == [],
      "a deterministic pool owes no export and is told nothing")
_detr = Pool("reservado")
_detr.block_reserve = 2
_f = _def({"reservado": _detr}, [])
check(len(_f) == 1 and "a reserve" in _f[0][2]
      and "formula does not produce" in _f[0][2],
      f"but one with a reserve owes it for those blocks, and is told: {_f!r}")
_f = prismash.dynamic_export_findings({"dinamico": _dyn}, [])
check(len(_f) == 1 and _f[0][0] == "warning"
      and _f[0][1] == "nat / cgnat / pool dinamico"
      and "nothing exports" in _f[0][2],
      f"a dynamic pool with no export at all is warned, by name: {_f!r}")
for _g, _warned, _what in (
        (["cgnat logging udp 10.0.0.9 src 10.0.0.1",
          "cgnat logging enable"], False, "UDP enabled with a collector"),
        (["cgnat logging enable"], True, "UDP enabled with no collector"),
        (["cgnat logging udp 10.0.0.9 src 10.0.0.1"], True,
         "a UDP collector that is not enabled"),
        (["cgnat logging tcp 10.0.0.9"], False, "a TCP collector"),
        (["cgnat logging tcp disable"], True, "TCP turned off"),
        (["cgnat logging syslog 10.0.0.9 enterprise 32473"], False,
         "a syslog collector"),
        (["cgnat logging syslog disable"], True, "syslog turned off")):
    check(bool(_def({"dinamico": _dyn}, _g)) == _warned,
          f"{_what}: {'warned' if _warned else 'not warned'}")
_dyn2 = Pool("dinamico2")
_dyn2.mode = "dynamic"
check([f[1] for f in _def({"dinamico2": _dyn2, "dinamico": _dyn,
                           "residencial": _det}, [])]
      == ["nat / cgnat / pool dinamico", "nat / cgnat / pool dinamico2"],
      "every dynamic pool is named, in order, and only those")
_sh = shell()
for _one in _def({"dinamico": _dyn, "reservado": _detr}, []):
    rv, text = driven(_sh.say_finding, *_one)
    check(all(len(r) <= 79 for r in text.splitlines())
          and text.splitlines()[-1].strip() == _one[1],
          f"and it prints inside the console, the pool on its own row: "
          f"{text!r}")

print("\n=== Shell.nat_rewrite and Shell.grammar_mode: the nat > cgnat levels (#446) ===")
# Mutations shown red, one at a time: the one-line refusal removed (the line
# reaches the model); `no` not rewritten under cgnat; grammar_mode answering
# the mode whatever the level.
_sh = at()
rv, text = driven(_sh.nat_rewrite, "nat cgnat pool p")
check(rv == "" and "one-line CGNAT form is gone" in text,
      f"the one-line form is refused at the top: {rv!r} {text!r}")
check(_sh.nat_rewrite("nat") == "" and _sh.nat_ctx == ["nat"],
      f"nat enters the level: {_sh.nat_ctx}")
rv, text = driven(_sh.nat_rewrite, "pool p")
check(rv == "" and "under 'nat' there is 'cgnat'" in text,
      f"under nat only cgnat is taken: {text!r}")
check(_sh.nat_rewrite("cgnat") == "" and _sh.nat_ctx == ["nat", "cgnat"],
      f"cgnat enters its level: {_sh.nat_ctx}")
check(_sh.nat_rewrite("pool p") == "cgnat pool p",
      "under cgnat, a statement is the model's cgnat statement")
check(_sh.nat_rewrite("no pool p") == "no cgnat pool p",
      "and a removal the model's removal")
check(_sh.nat_rewrite("commit check") == "commit check"
      and _sh.nat_rewrite("show this") == "show this",
      "commit and show mean what they mean everywhere")
check(_sh.grammar_mode() == "cgnat", f"and ? reads the cgnat tree there: "
      f"{_sh.grammar_mode()}")
check(_sh.nat_rewrite("exit") == "" and _sh.nat_ctx == ["nat"]
      and _sh.grammar_mode() == "nat",
      f"exit is one level up: {_sh.nat_ctx}")
check(_sh.nat_rewrite("end") == "" and _sh.nat_ctx == []
      and _sh.grammar_mode() == "config",
      f"end is the top: {_sh.nat_ctx}")
check(_sh.nat_rewrite("interface pg0") == "interface pg0",
      "and at the top a line that is not CGNAT goes on as it was")
_sh.frr_ctx = ["router bgp 65000"]
_sh.nat_ctx = ["nat", "cgnat"]
check(_sh.grammar_mode() == "config",
      "an FRR block is never inside nat, so it wins")
ok("the levels, the refusal, and the tree ? reads")

print("\n=== vtyattach and Shell.host_shell: the VTY's end of the broker (#448) ===")
# The broker itself is driven on a real pty by test_vty_broker.py; here the
# module's logic against a socketpair standing in for it. Mutations shown
# red: dispatch() queueing INT instead of raising it; request_shell()
# treating SHELLDENIED as done; host_shell() spawning a local shell when
# attached.
import socket as _socket
import vtyattach
_a, _b = _socket.socketpair()
_ch = vtyattach.Channel(_a)
_got = []
_old = _signal.signal(_signal.SIGWINCH, lambda *_: _got.append("WINCH"))
_ch.dispatch("WINCH")
time.sleep(0.1)
_signal.signal(_signal.SIGWINCH, _old)
check(_got == ["WINCH"], f"a relayed WINCH is raised in this process: {_got}")
_ch.dispatch("SHELLDONE 3")
check(_ch.replies.get(timeout=1) == "SHELLDONE 3", "an answer is queued, not raised")
_b.sendall(b"SHELLDONE 0\n")
check(_ch.request_shell() == ("done", 0) and _b.recv(64) == b"SHELL\n",
      "request_shell sends SHELL and returns the host's status")
_b.sendall(b"SHELLDENIED\n")
check(_ch.request_shell() == ("denied", None), "a refusal is a refusal")
_b.close(); time.sleep(0.2)
check(_ch.request_shell() == ("gone", None),
      "and a broker that went away is said as gone, not waited on for ever")

# attach(): the three descriptors handed over by SCM_RIGHTS become 0, 1, 2.
_srv = os.path.join(_root, "attach.sock")
_l = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
_l.bind(_srv); _l.listen(1)
_rd, _wr = os.pipe()
_pid = os.fork()
if _pid == 0:
    _c = vtyattach.attach(_srv)
    os.write(1, b"through the handed fd\n")
    os._exit(0)
_conn, _ = _l.accept()
_socket.send_fds(_conn, [b"tty"], [_rd, _wr, _wr])
os.waitpid(_pid, 0)
os.close(_wr)
check(os.read(_rd, 64) == b"through the handed fd\n",
      "attach() makes the handed descriptors this process's stdout")

class _Chan:
    def __init__(self, answer): self.answer, self.asked = answer, 0
    def request_shell(self): self.asked += 1; return self.answer
_sh = shell()
_c = _Chan(("done", 0))
rv, text = driven(lambda: _sh.host_shell(_c))
check(_c.asked == 1 and "back in the Prisma Routing VTY" in text,
      f"host_shell asks the broker and comes back: {text!r}")
_c = _Chan(("denied", None))
rv, text = driven(lambda: _sh.host_shell(_c))
check("the host refused a shell for this account" in text,
      f"and says the host refused, when it did: {text!r}")
_c = _Chan(("gone", None))
rv, text = driven(lambda: _sh.host_shell(_c))
check("start shell: the login session is gone" in text,
      f"and a login that went away is said, not waited on: {text!r}")

# The broker's own rule, called by name: may_have_shell() is prismash-login's
# rule on the host, and the one answer that decides a host shell. Loaded from
# its path, since it has no .py suffix.
def load_broker():
    import importlib.machinery as im, importlib.util as iu, grp
    os.environ["PRISMA_ADMIN_GROUP"] = grp.getgrgid(os.getgid()).gr_name
    ld = im.SourceFileLoader("prisma_vty_broker", os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "appliance",
        "prisma-vty-broker"))
    mod = iu.module_from_spec(iu.spec_from_loader("prisma_vty_broker", ld))
    ld.exec_module(mod)
    return mod
_brk = load_broker()
check(_brk.may_have_shell(uid=0, groups=[]), "root may have a host shell")
check(_brk.may_have_shell(uid=4242, groups=[os.getgid()]),
      "an account in the admin group may")
check(not _brk.may_have_shell(uid=4242, groups=[]),
      "and one in neither may not, whatever the VTY asked")
check(_brk.main([]) == 2, "the broker with no VTY to run is a usage line")
# serve() answers a request the host refuses with SHELLDENIED and nothing
# else - no shell is forked - and ignores a line it does not know. The rule is
# replaced, so this holds for whoever runs the suite, root included.
_brk.may_have_shell = lambda *a, **k: False
_a, _b = _socket.socketpair()
_b.sendall(b"NONSENSE\nSHELL\n"); _b.shutdown(_socket.SHUT_WR)
_brk.serve(_a)
check(_b.recv(100) == b"SHELLDENIED\n",
      "serve() refuses a shell the host's rule refuses, and says only that")
_a.close(); _b.close()
_saved = prismash.ATTACHED
prismash.ATTACHED = _Chan(("done", 0))
try:
    rv, text = driven(_sh.start_shell)
    check(prismash.ATTACHED.asked == 1 and "back in the Prisma Routing VTY" in text,
          f"start_shell goes to the broker when attached, and spawns nothing: "
          f"{text!r}")
finally:
    prismash.ATTACHED = _saved
ok("the module and the VTY's use of it")

print("\n=== LACP bundles: the model, the dataplane calls, the readback (#455) ===")
check(_config._is_bundle("ae0") and _config._is_bundle("ae63")
      and not _config._is_bundle("ae64") and not _config._is_bundle("ae")
      and not _config._is_bundle("aex") and not _config._is_bundle("eth0"),
      "_is_bundle: ae<n>, below the ceiling, and nothing else")
_b = _config.new_bundle()
check(_b == {"members": [], "lacp": "active", "rate": "slow", "hash": "l34"},
      f"new_bundle: no members and the defaults: {_b}")
for _w, _why in ((["member", "ens19"], ""), (["member", "ens19"], ""),
                 (["member", "ens20"], ""), (["lacp", "rate", "fast"], ""),
                 (["load-balance", "l2"], ""),
                 (["lacp", "mode", "sideways"], "expected: lacp mode"),
                 (["no", "member", "ens20"], ""),
                 (["no", "load-balance"], ""), (["description", "x"], "")):
    check(_config.bundle_statement(_b, _w).startswith(_why) if _why
          else _config.bundle_statement(_b, _w) == "",
          f"bundle_statement {_w}: {_why!r}")
check(_b == {"members": ["ens19"], "lacp": "active", "rate": "fast",
             "hash": "l34"},
      f"a member once, a no taking it back, a no restoring the default: {_b}")


class _BondApi(RecApi):
    """RecApi with the interfaces a bundle needs, by name."""

    def __init__(self, have, **k):
        super().__init__(**k)
        self.have = have

    def interface_index(self, name):
        return self.have.get(name)


_api = _BondApi({"ae0": 11, "ens19": 1, "ens20": 2},
                fields={"bond_create2": {"sw_if_index": 11}})
_sh = shell(api=_api)
rv, text = driven(lambda: _sh.create_bundle(
    "ae0", {"members": ["ens19", "ens20"], "lacp": "passive",
            "rate": "fast", "hash": "l23"}))
_msgs = [m for m, _ in _api.calls]
check(rv is True and _msgs[:2] == ["bond_create2",
                                   "sw_interface_set_interface_name"]
      and _msgs.count("bond_add_member") == 2,
      f"create_bundle: the bond, its name, then each member: {_msgs}")
_create = dict(_api.calls)["bond_create2"]
_add = [a for m, a in _api.calls if m == "bond_add_member"][0]
check(_create["mode"] == "BOND_API_MODE_LACP"
      and _create["lb"] == "BOND_API_LB_ALGO_L23" and _create["id"] == 0
      and _add == {"sw_if_index": 1, "bond_sw_if_index": 11,
                   "is_passive": True, "is_long_timeout": False},
      f"LACP mode, the hash, the instance, and each member's mode and rate: "
      f"{_create} {_add}")
check(dict(_api.calls)["sw_interface_set_interface_name"]
      == {"sw_if_index": 11, "name": "ae0"}, "renamed to ae0")
rv, text = driven(lambda: _sh.detach_member("ens20"))
check(rv is True and _api.calls[-1] == ("bond_detach_member",
                                        {"sw_if_index": 2}),
      "detach_member: the port out")
check(_sh.detach_member("ens99") is True, "a port not there is already out")
rv, text = driven(lambda: _sh.delete_bundle("ae0"))
check(rv is True and _api.calls[-1] == ("bond_delete", {"sw_if_index": 11}),
      "delete_bundle")
check(_sh.delete_bundle("ae9") is True, "a bundle not there is gone already")
_api = _BondApi({}, rows={
    "sw_bond_interface_dump": [
        {"sw_if_index": 11, "interface_name": "ae0",
         "lb": "BOND_API_LB_ALGO_L2"},
        {"sw_if_index": 12, "interface_name": "BondEthernet5",
         "lb": "BOND_API_LB_ALGO_L34"}],
    "sw_member_interface_dump": [
        {"interface_name": "ens19", "is_passive": False,
         "is_long_timeout": False}]})
_got = shell(api=_api).device_bundles()
check(_got == {"ae0": {"members": ["ens19"], "lacp": "active",
                       "rate": "fast", "hash": "l2"}},
      f"device_bundles: ae0 read back, a bond of another name left alone: "
      f"{_got}")
ok("the model, the five calls, and the readback")

# ------------------------------------------------ agentx follows snmp (#502)
print("\n=== agentx: the box's, carried, and following snmp enabled (#502) ===")
_RUN_AX = (0, "Building configuration...\nfrr version 10.7.1\nagentx\n"
              "router bgp 65001\n bgp router-id 10.9.9.9\nexit\nend\n")
_RUN_NO = (0, "router bgp 65001\nexit\n")
_f = RecFrr(shows={"show running-config": _RUN_AX})
check(_f.agentx_on(), "running daemons with `agentx` read as on")
check(not RecFrr(shows={"show running-config": _RUN_NO}).agentx_on(),
      "and without it as off")
check(not RecFrr().agentx_on(), "and an FRR that does not answer as off")
check("agentx" not in _f.running() and "router bgp 65001" in _f.running(),
      f"read back as routing it is dropped, and the rest kept: {_f.running()}")


class AxFrr(RecFrr):
    """RecFrr whose apply_lines is recorded and answered."""

    def __init__(self, rc=0, **k):
        super().__init__(**k)
        self.applied = []
        self.rc = rc

    def apply_lines(self, lines):
        # As vtysh does: status 0 either way, and only daemons with the module
        # (rc 0 here) end up with it running.
        self.applied.append(list(lines))
        if self.rc == 0 and "agentx" in lines:
            self.shows["show running-config"] = _RUN_AX
        return 0, ""


_f = AxFrr(shows={"show running-config": _RUN_NO})
_, _out = driven(shell(frr=_f).agentx_follows_snmp, True)
check(_f.applied == [["agentx"]] and not _out,
      f"SNMP enabled turns agentx on, quietly: {_f.applied} {_out!r}")
_f = AxFrr(shows={"show running-config": _RUN_AX})
driven(shell(frr=_f).agentx_follows_snmp, True)
check(_f.applied == [], f"and not twice: {_f.applied}")
_f = AxFrr(rc=1, shows={"show running-config": _RUN_NO})
_, _out = driven(shell(frr=_f).agentx_follows_snmp, True)
check("no SNMP module" in _out,
      f"daemons without the module say BGP is not served: {_out!r}")
_f = AxFrr(shows={"show running-config": _RUN_AX})
_, _out = driven(shell(frr=_f).agentx_follows_snmp, False)
check(_f.applied == [] and "keep looking for snmpd" in _out
      and "next starts" in _out,
      f"disabled: nothing is sent, and what remains is said: {_out!r}")
_f = AxFrr(up=False)
_, _out = driven(shell(frr=_f).agentx_follows_snmp, True)
check(_f.applied == [] and not _out, "no FRR, nothing asked and nothing said")
_f = AxFrr(shows={"show running-config": _RUN_NO})
with contextlib.redirect_stdout(io.StringIO()):
    shell(frr=_f).agentx_follows_snmp(False)
check(_f.applied == [], "disabled on daemons that never had it: nothing sent")

# The reload carries it: frr-reload would send `no agentx`, which FRR refuses.
_stub = os.path.join(_root, "frr-reload-stub.py")
_seen = os.path.join(_root, "frr-reload-seen")
with open(_stub, "w") as _h:
    _h.write("import shutil, sys\n"
             f"shutil.copy(sys.argv[-1], {_seen!r})\n")
_was = prismash.FRR_RELOAD
prismash.FRR_RELOAD = _stub
try:
    for _shows, _want in ((_RUN_AX, True), (_RUN_NO, False)):
        _f = RecFrr(shows={"show running-config": _shows})
        _f.reload(["router bgp 65001", "exit"], check_only=True)
        with open(_seen) as _h:
            _lines = _h.read().splitlines()
        check(("agentx" in _lines) == _want and "router bgp 65001" in _lines,
              f"reload hands frr-reload agentx exactly when it is running "
              f"({_want}): {_lines}")
finally:
    prismash.FRR_RELOAD = _was
ok("agentx: read, dropped, carried, and following snmp enabled")

# ------------------------------------------------------ hostname (#516)
print("\n=== hostname: the box's name, and FRR's following it (#516) ===")
# As FRR 10.7.1 shows it: the daemons' line, then vtysh's own.
_RUN_HN = ("!\nfrr version 10.7.1\nhostname edge1\nhostname 7db79d7ce413\n"
           "router bgp 65001\nexit\n!\nend\n")
check(RecFrr(shows={"show running-config": (0, _RUN_HN)}).hostname_lines()
      == ["hostname edge1", "hostname 7db79d7ce413"]
      and RecFrr().hostname_lines() == []
      and RecFrr(shows={"show running-config": (0, "!\nend\n")})
      .hostname_lines() == [],
      "every hostname line FRR shows, in order; none when down or without")
check("hostname edge1" not in RecFrr(
          shows={"show running-config": (0, _RUN_HN)}).running(),
      "and never read back as routing an operator typed")

# The reload carries FRR's lines, as it carries agentx. Mutation shown red:
# the carry removed - frr-reload is handed no hostname and sends
# `no hostname` for each line, on every commit.
prismash.FRR_RELOAD = _stub
try:
    _f = RecFrr(shows={"show running-config": (0, _RUN_HN)})
    _f.reload(["router bgp 65001", "exit"], check_only=True)
    with open(_seen) as _h:
        _lines = _h.read().splitlines()
    check("hostname edge1" in _lines and "hostname 7db79d7ce413" in _lines
          and "router bgp 65001" in _lines,
          f"reload hands frr-reload every name FRR shows: {_lines}")
    _f.reload(["hostname edge2", "router bgp 65001", "exit"], check_only=True)
    with open(_seen) as _h:
        _lines = _h.read().splitlines()
    check(_lines.count("hostname edge2") == 1
          and "hostname edge1" not in _lines,
          f"and not a second one beside a name it was given: {_lines}")
finally:
    prismash.FRR_RELOAD = _was

# At the prompt. Mutation shown red: `no hostname` staging "" - the file then
# has no statement, and the box keeps the name it was told to drop.
_sh = at()
_sh.do_config("hostname cgnat-sp1")
check(_sh.candidate.hostname == "cgnat-sp1", "staged as typed")
_sh.hostname_statement(["no", "hostname"])
check(_sh.candidate.hostname == prismash.HOSTNAME_DEFAULT,
      f"'no hostname' is the installer's default: {_sh.candidate.hostname!r}")
for _typed, _needle in (("hostname 9box", "starts with a letter"),
                        ("hostname a_b", "letters, digits and hyphens"),
                        ("hostname", "usage: hostname <name>"),
                        ("hostname a b", "usage: hostname <name>")):
    with spoken() as _o:
        _sh.do_config(_typed)
    check(_needle in _o.getvalue()
          and _sh.candidate.hostname == prismash.HOSTNAME_DEFAULT,
          f"{_typed!r} is refused, and nothing changes: {_o.getvalue()!r}")


# Applied: the host first, then FRR. Mutation shown red: FRR told before the
# host is asked - a refused name leaves FRR told and the host not.
def _hn_shell(frr, runner):
    """A Shell over `frr`, its host answered by `runner`."""
    sh = shell(frr=frr)
    sh.sys = system.System(runner=runner)
    return sh


_rr = RecRunner()
_f = AxFrr()
_sh = _hn_shell(_f, _rr)
check(_sh.apply_hostname("edge7") == (True, "")
      and [system.SET_HOSTNAME, "edge7"] in _rr.asked
      and _f.applied == [["hostname edge7"]],
      f"the host renamed and FRR told: {_rr.asked} {_f.applied}")
_rr = RecRunner({(system.SET_HOSTNAME,): (1, "set-hostname: no\n")})
_f = AxFrr()
_ok, _msg = _hn_shell(_f, _rr).apply_hostname("edge7")
check(not _ok and "set-hostname: no" in _msg and _f.applied == [],
      f"a host that refuses: FRR is not asked: {_msg!r} {_f.applied}")
_f = AxFrr(up=False)
check(_hn_shell(_f, RecRunner()).apply_hostname("edge7") == (True, "")
      and _f.applied == [], "a box with no routing daemons: the host only")
ok("hostname: read, dropped, carried, staged and applied")

# ---------------------------------------------------- show snmp mib (#504)
print("\n=== show snmp mib: the MIB, rooted at this box's number (#504) ===")
_was = prismash.SNMP_MIB_TEMPLATE


def _mib_shell(text):
    """A Shell whose running configuration is `text`."""
    from config import Config as _C
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL}
    sh.running_config = lambda: _C.parse(text)
    return sh


prismash.SNMP_MIB_TEMPLATE = os.path.join(os.path.dirname(__file__), "..",
                                          "appliance", "PRISMA-ROUTING-MIB.in")
_sh = _mib_shell("snmp enterprise-number 70000\n")
_, _out = driven(_sh.show_snmp_mib)
check("::= { enterprises 70000 }" in _out and "@PEN@" not in _out
      and _out.rstrip().endswith("END"),
      f"the MIB is printed whole, rooted at 70000: {_out[:200]!r}")
_sh = _mib_shell("")
_, _out = driven(_sh.show_snmp_mib)
check("::= { enterprises 67033 }" in _out,
      f"with no number named, the product's (#508): {_out[:120]!r}")
# A box whose snmpd.conf predates #508 serves no enterprise subtree until SNMP
# is written again; its running configuration says "", and there is no MIB to
# hand out for a subtree nobody answers.
_sh = _mib_shell("")
_c = _sh.running_config()
_c.snmp["enterprise-number"] = ""
_sh.running_config = lambda: _c
_, _out = driven(_sh.show_snmp_mib)
check("serves no enterprise subtree" in _out and "BEGIN" not in _out,
      f"an agent serving no subtree has no MIB to print: {_out!r}")
prismash.SNMP_MIB_TEMPLATE = os.path.join(_root, "no-such-mib")
_, _out = driven(_mib_shell("snmp enterprise-number 70000\n").show_snmp_mib)
check("not on this box" in _out, f"a missing file is named: {_out!r}")
with contextlib.redirect_stdout(io.StringIO()):
    _mib_shell("snmp enterprise-number 70000\n").show_snmp_mib()
prismash.SNMP_MIB_TEMPLATE = _was
ok("show snmp mib: printed with the number, or refused with the reason")

# ------------------------------------------- show snmp zabbix-template (#514)
print("\n=== show snmp zabbix-template: the template, with this box's number ===")
_was = prismash.ZABBIX_TEMPLATE
prismash.ZABBIX_TEMPLATE = os.path.join(os.path.dirname(__file__), "..", "appliance",
                                        "prisma-routing-zabbix.json.in")
_, _out = driven(_mib_shell("snmp enterprise-number 70000\n").show_snmp_zabbix_template)
_tpl = json.loads(_out)["zabbix_export"]["templates"][0]
check([m["value"] for m in _tpl["macros"] if m["macro"] == "{$PRISMA.PEN}"] == ["70000"]
      and "@PEN@" not in _out,
      "the template is printed whole, {$PRISMA.PEN} defaulting to 70000")
_, _out = driven(_mib_shell("").show_snmp_zabbix_template)
check('"value": "67033"' in _out, "and to the product's with no number named (#508)")
with contextlib.redirect_stdout(io.StringIO()) as _buf:
    _mib_shell("").show_snmp_zabbix_template()
check(json.loads(_buf.getvalue())["zabbix_export"]["version"] == "7.0",
      "and what it prints is JSON Zabbix 7.0 imports")
prismash.ZABBIX_TEMPLATE = os.path.join(_root, "no-such-template")
_, _out = driven(_mib_shell("snmp enterprise-number 70000\n").show_snmp_zabbix_template)
check("the Zabbix template is not on this box" in _out and "no-such-template" in _out,
      f"a missing file is named, by what it is: {_out!r}")
check(_mib_shell("snmp enterprise-number 70000\n").text_with_pen(prismash.ZABBIX_TEMPLATE) == ""
      and _mib_shell("snmp enterprise-number 70000\n").text_with_pen(
          os.path.join(os.path.dirname(__file__), "..", "appliance",
                       "prisma-routing-zabbix.json.in")).count('"70000"') == 1,
      "text_with_pen: '' for a missing file, the text with the number otherwise")
prismash.ZABBIX_TEMPLATE = _was
ok("show snmp zabbix-template: the MIB's rule, for the template")

print("\n=== a session that cannot read snmpd.conf does not save SNMP off (#525) ===")
_saved_path = prismash.CONFIG_PATH
prismash.CONFIG_PATH = os.path.join(_root, "saved-snmp.conf")
with open(prismash.CONFIG_PATH, "w") as _f:
    _f.write("snmp enabled yes\nsnmp community c0mm\nsnmp trap-host 192.0.2.9 "
             "community tr4p\n")
_c = Config()
prismash.Shell().carry_saved_snmp(_c)
check(_c.snmp_unread == "saved" and _c.snmp.get("enabled") == "yes"
      and _c.snmp_communities == ["c0mm"]
      and _c.snmp_trap_hosts == [("192.0.2.9", "tr4p", "trap")],
      f"the saved file's SNMP stands in, and says so: {_c.snmp_unread} "
      f"{_c.snmp} {_c.snmp_communities}")
_, _out = driven(prismash.Shell.print_config, _c)
check("snmp enabled yes" in _out and "note: snmp: this session cannot read"
      in " ".join(_out.split()) and "last saved file" in " ".join(_out.split()),
      f"show running-config says whose lines they are: {_out[-200:]!r}")
# A save carries them: the file keeps SNMP on and its community.
sh = shell()
sh.running_config = lambda: _c
_, _out = driven(sh.save_running)
check("configuration saved" in _out
      and "snmp community c0mm" in open(prismash.CONFIG_PATH).read()
      and "snmp enabled yes" in open(prismash.CONFIG_PATH).read(),
      f"a save from such a session keeps SNMP as it was: {_out!r}")
os.remove(prismash.CONFIG_PATH)
_n = Config()
prismash.Shell().carry_saved_snmp(_n)
check(_n.snmp_unread == "none" and _n.snmp.get("enabled", "no") == "no",
      "no saved file either: the defaults stay, and it says none")
_, _out = driven(prismash.Shell.print_config, _n)
check("nor the saved file" in " ".join(_out.split()),
      "and show running-config says that too")
sh = shell()
sh.running_config = lambda: _n
_, _out = driven(sh.save_running)
check("save: refused, nothing was written" in _out
      and not os.path.exists(prismash.CONFIG_PATH),
      f"and a save is refused rather than writing SNMP off: {_out!r}")
_r = Config()
check(_r.snmp_unread == "" and "snmp_unread" not in _r.text()
      and not driven(prismash.Shell.print_config, _r)[1].count("note: snmp"),
      "a configuration that was read carries no note and writes no flag")
prismash.CONFIG_PATH = _saved_path


print("=== show system uptime: the box, the dataplane, the routing, since when (#534) ===")
check(prismash.human_duration(45) == "45 s"
      and prismash.human_duration(125) == "2 min 5 s"
      and prismash.human_duration(2 * 3600 + 13 * 60) == "2 h 13 min"
      and prismash.human_duration(86400 + 4 * 3600) == "1 day 4 h"
      and prismash.human_duration(3 * 86400) == "3 days 0 h"
      and prismash.human_duration(-5) == "0 s",
      "a duration as an operator reads it")
_ud = tempfile.mkdtemp(prefix="uptime-", dir=_root)
_stat = os.path.join(_ud, "stat")
with open(_stat, "w") as _f:
    _f.write("cpu  1 2 3\nbtime 1790836414\nprocesses 9\n")
check(prismash.read_btime(_stat) == 1790836414, "the kernel's boot time from btime")
check(prismash.read_btime(os.path.join(_ud, "none")) is None,
      "no /proc/stat is no boot time, not a crash")
_pstat = os.path.join(_ud, "pid1")
with open(_pstat, "w") as _f:
    # The command holds a space and a parenthesis, as one may.
    _f.write("1 (sleep (x) y) S 0 1 1 0 -1 4194560 89 0 0 0 0 0 0 0 20 0 1 0 1886 "
             "2400256 120 18446744073709551615\n")
check(prismash.proc_started(_pstat, 1790836414, hz=100) == 1790836414 + 18.86,
      "a process's start: the boot plus starttime's ticks, past a command with spaces")
check(prismash.proc_started(_pstat, None) is None
      and prismash.proc_started(os.path.join(_ud, "none"), 1, hz=100) is None,
      "no boot time or no stat file is no start")
check(prismash.vpp_uptime("Time now 539.710725, Thu, 01 Oct 2026 06:43:15 GMT\n") == 539.710725
      and prismash.vpp_uptime("") is None and prismash.vpp_uptime(None) is None,
      "VPP's uptime from show clock's first row")
_pid = os.path.join(_ud, "watchfrr.pid")
with open(_pid, "w") as _f:
    _f.write("45\n")
os.utime(_pid, (1790836426, 1790836426))
check(prismash.file_mtime(_pid) == 1790836426
      and prismash.file_mtime(os.path.join(_ud, "none")) is None,
      "a file's mtime, or None")
check(prismash.boot_reason("1790836300 root reboot\n", 1790836414)
      == ("system reboot by root", 1790836300),
      "a reboot asked at the VTY just before this boot is this boot's reason")
check(prismash.boot_reason("1790836300 ana shutdown\n", 1790836414)[0]
      == "system shutdown by ana", "and a shutdown, by whom")
check(prismash.boot_reason("1790800000 root reboot\n", 1790836414)
      == ("not recorded", None),
      "a record from an earlier boot is not this one's reason")
check(prismash.boot_reason("1790836500 root reboot\n", 1790836414)
      == ("not recorded", None),
      "a record written after this boot is not its reason either")
check(prismash.boot_reason("", 1790836414) == ("not recorded", None)
      and prismash.boot_reason("garbage", 1790836414) == ("not recorded", None)
      and prismash.boot_reason("1790836300 root reboot", None) == ("not recorded", None),
      "no record, a broken one or no boot time: not recorded, never a guess")
_when = lambda t: time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(t))
_facts = {"boot": 1790836414, "dataplane": 1790836420, "control_plane": 1790836426,
          "vty": 1790836394, "release": "0.148.7 (abc)",
          "release_installed": 1790832420, "boot_reason": "system reboot by root"}
_r = prismash.uptime_rows(1790837000, _facts, _when)
check(_r[0] == "  box          since 2026-10-01 06:33:34 UTC, up 9 min 46 s"
      and _r[1].strip() == "last boot: system reboot by root"
      and _r[2].startswith("  dataplane    since") and _r[3].startswith("  routing")
      and _r[4].startswith("  vty") and _r[5] == "  release      0.148.7 (abc)"
      and "installed 2026-10-01 05:27:00 UTC, 1 h 16 min ago" in _r[6]
      and not any("restarted" in x for x in _r),
      f"each clock since when and for how long, nothing restarted: {_r}")
_late = dict(_facts, dataplane=1790836900, vty=None, release_installed=None)
_r = prismash.uptime_rows(1790837000, _late, _when)
check(_r[3].strip() == "restarted after the boot" and not any(x.startswith("  vty") for x in _r)
      and _r[-1] == "  release      0.148.7 (abc)",
      f"a dataplane younger than the box says so on a row of its own: {_r}")
_r = prismash.uptime_rows(1790837000, {"boot": None, "dataplane": None}, _when)
check(_r[0] == "  box          not read" and "not recorded" in _r[1]
      and _r[2] == "  dataplane    not read",
      "a clock that could not be read says so")
_wide = dict(_late, boot_reason="system shutdown by " + "a" * 32)
check(max(len(x) for x in prismash.uptime_rows(1790837000 + 86400 * 999, _wide, _when)) <= 79,
      "every row inside 79 columns, at its widest")
check(prismash.uptime_summary(1790837000, _facts) == "box 9 min 46 s, dataplane 9 min 40 s"
      and prismash.uptime_summary(1790837000, _late)
      == "box 9 min 46 s, dataplane 1 min 40 s (restarted after the boot)"
      and prismash.uptime_summary(1, {}) == "box not read, dataplane not read",
      "show version's one row")


class _ClockDp:
    """A dataplane whose `show clock` says how long VPP has run."""

    def __init__(self, up):
        self.up = up

    def run(self, line):
        if line == "show clock" and self.up is not None:
            return 0, f"Time now {self.up}, Thu, 01 Oct 2026 06:43:15 GMT\n"
        return 1, "not answering"


_saved = (prismash.PROC_STAT, prismash.FRR_STARTED_FILE, prismash.REBOOT_RECORD,
          prismash.VERSION_PATH, os.environ.get("PRISMA_IN_MGMT"))
_ver = os.path.join(_ud, "VERSION")
with open(_ver, "w") as _f:
    _f.write("0.148.7 (abc)\n")
os.utime(_ver, (1790832420, 1790832420))
_rec = os.path.join(_ud, "last-reboot")
prismash.PROC_STAT, prismash.FRR_STARTED_FILE = _stat, _pid
prismash.REBOOT_RECORD, prismash.VERSION_PATH = _rec, _ver
os.environ.pop("PRISMA_IN_MGMT", None)
try:
    _sh = shell(dp=_ClockDp(60.0))
    _sh.record_reboot("reboot")
    with open(_rec) as _f:
        _w = _f.read().split()
    check(len(_w) == 3 and _w[2] == "reboot" and abs(int(_w[0]) - time.time()) < 5,
          f"system reboot records when, who and which verb: {_w}")
    with open(_rec, "w") as _f:
        _f.write("1790836300 root reboot\n")
    _fx = _sh.uptime_facts()
    check(_fx["boot"] == 1790836414 and abs(_fx["dataplane"] - (time.time() - 60)) < 5
          and _fx["control_plane"] == 1790836426 and _fx["vty"] is None
          and _fx["release_installed"] == 1790832420
          and _fx["boot_reason"] == "system reboot by root"
          and _fx["dataplane_restarted"] is True,
          f"uptime_facts reads each clock where it lives: {_fx}")
    check(shell(dp=_ClockDp(None)).uptime_facts()["dataplane"] is None,
          "a dataplane that does not answer is a clock not read")
    _, _out = driven(_sh.show_system_uptime)
    check("  box          since" in _out and "  dataplane    since" in _out
          and "restarted after the boot" in _out and "last boot: system reboot by root" in _out,
          f"show system uptime prints the rows: {_out}")
    with contextlib.redirect_stdout(io.StringIO()) as _buf:
        _sh.show_system_uptime()
    check(_buf.getvalue() == _out, "and prints the same rows asked again")
    prismash.REBOOT_RECORD = os.path.join(_ud, "no-such-dir", "last-reboot")
    _sh.record_reboot("shutdown")
    check(True, "a record that cannot be written costs nothing")
finally:
    (prismash.PROC_STAT, prismash.FRR_STARTED_FILE, prismash.REBOOT_RECORD,
     prismash.VERSION_PATH, _mg) = _saved
    if _mg is not None:
        os.environ["PRISMA_IN_MGMT"] = _mg
ok("show system uptime: each clock where it lives, the boot's reason only as recorded")

print("=== an operator's upgrade and restart, detached (#563) ===")
_d = tempfile.mkdtemp()
_log = os.path.join(_d, "upgrade.log")
_t = [1000.0]
def _clock():
    return _t[0]
def _sleep(n):
    _t[0] += n
    if _t[0] > 1001.2 and not os.path.exists(_log):
        with open(_log, "w") as _f:
            _f.write("checking the release\nupgraded to 0.149.26\n"
                     "prisma-api-exit 0\n")
        os.utime(_log, (1001.5, 1001.5))
_out = io.StringIO()
_rc = prismash.follow_run_log(_log, 1000.0, wait=30, out=_out, sleep=_sleep,
                              clock=_clock)
check(_rc == 0 and _out.getvalue() == "checking the release\n"
      "upgraded to 0.149.26\n",
      f"follow_run_log(): the run's lines, then its status: {_rc} "
      f"{_out.getvalue()!r}")
with open(_log, "w") as _f:
    _f.write("an older run\nprisma-api-exit 1\n")
os.utime(_log, (10.0, 10.0))
_t[0] = 2000.0
check(prismash.follow_run_log(_log, 2000.0, wait=3, out=io.StringIO(),
                              sleep=lambda n: _t.__setitem__(0, _t[0] + n),
                              clock=_clock) is None,
      "and a log older than the start is not this run's: None at the deadline")
# #564: a run that never writes is said at once, not after the deadline.
_t[0] = 3000.0
check(prismash.follow_run_log(os.path.join(_d, "never.log"), 3000.0, wait=600,
                              out=io.StringIO(),
                              sleep=lambda n: _t.__setitem__(0, _t[0] + n),
                              clock=_clock, first_wait=20) == prismash.RUN_SILENT
      and _t[0] < 3030,
      f"a log that never appears is RUN_SILENT after first_wait, at {_t[0] - 3000:.0f} s")
_mg2 = os.environ.pop("PRISMA_IN_MGMT", None)
_sh = shell()
_sh.caps = {prismash.CAP_OPERATOR}
check(_sh.operator_on_host("upgrade", ["0.149.26"]) is False,
      "Shell.operator_on_host(): outside the management plane it hands back")
os.environ["PRISMA_IN_MGMT"] = "1"
_asked = []
_sh.sys = prismash.System(runner=lambda argv, **kw: (_asked.append(list(argv))
                                                    or (0, "")))
_real = prismash.follow_run_log
prismash.follow_run_log = lambda path, started, **kw: 0
try:
    _rv, _text = driven(lambda: _sh.operator_on_host("upgrade", ["0.149.26"]))
finally:
    prismash.follow_run_log = _real
    if _mg2 is None:
        os.environ.pop("PRISMA_IN_MGMT", None)
    else:
        os.environ["PRISMA_IN_MGMT"] = _mg2
check(_rv is True and _asked == [list(prismash.doors.api_run_argv(
    "upgrade", "0.149.26"))],
      f"inside it, a non-root operator starts the detached door: {_asked}")

print("=== the upgrade check, from the management plane (#563) ===")
_mg3 = os.environ.get("PRISMA_IN_MGMT")
os.environ["PRISMA_IN_MGMT"] = "1"
try:
    _asked = []
    _sh = shell()
    _sh.caps = set()                      # read-only, and not an operator
    _sh.sys = prismash.System(runner=lambda argv, **kw: (
        _asked.append(list(argv)) or
        (0, "this box runs: 0.149.25 (4eb95cc2)\nnewest published: 0.149.26\n")))
    check(_sh.newest_release() == ("0.149.26", "")
          and _asked == [list(prismash.doors.UPGRADE_CHECK_ARGV)],
          f"Shell.newest_release() reads it through the host's VTY: {_asked}")
    _rv, _text = driven(_sh.system_upgrade, [])
    check("newest published: 0.149.26" in _text and "permission" not in _text,
          f"and the check is every account's, a read-only one's too: {_text!r}")
    _rv, _text = driven(_sh.system_upgrade, ["0.149.26"])
    check("permission denied for this account" in _text,
          f"while the upgrade itself is not: {_text!r}")
    _sh.sys = prismash.System(runner=lambda argv, **kw: (
        1, "this box runs: x\nerror: the newest release could not be read\n"
           "    no route\n"))
    _n, _why = _sh.newest_release()
    check(_n is None and "no route" in _why,
          f"a host that cannot read it either says why: {_why!r}")
finally:
    if _mg3 is None:
        os.environ.pop("PRISMA_IN_MGMT", None)
    else:
        os.environ["PRISMA_IN_MGMT"] = _mg3

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
