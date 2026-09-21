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
import os
import re
import sys
import tempfile
import types

_root = tempfile.mkdtemp(prefix="prismash-units-")
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
check(caps_as(0, "root", [users], 0) == BOTH, "root has both")
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

print("\n=== _frr_static: route <prefix> ... in FRR's spelling ===")
# Mutation shown red: pick the family off the next hop instead of the prefix.
check(prismash._frr_static(["10.0.0.0/8", "10.1.1.1"])
      == "ip route 10.0.0.0/8 10.1.1.1", "an IPv4 prefix is 'ip route'")
check(prismash._frr_static(["2001:db8::/32", "2001:db8:1::1", "vppwan0"])
      == "ipv6 route 2001:db8::/32 2001:db8:1::1 vppwan0",
      "an IPv6 prefix is 'ipv6 route', the rest handed on")
ok("family from the prefix, the rest verbatim")

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
check(isinstance(sub, dict) and set(sub) == {"reboot", "shutdown"}
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

    def __init__(self, shows=None, up=True):
        self.shows = dict(shows or {})
        self.up = up
        self.asked = []

    def available(self):
        return self.up

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
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT} if caps is None else caps
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
check(got == ["!", "hostname box", "router bgp 65000",
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
      and any("cgnat logging tcp" in r for r in rows(text)),
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
check(len(api.calls) == n and "error: usage: nat cgnat logging udp "
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
      == ["error: usage: system reboot | system shutdown"],
      f"a verb it has not got is a usage line: {text!r}")
ok("two verbs, two units, the role checked first and the cost said first")

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
sh.do_pool("inside 10.0.0.0/8")
sh.do_pool("outside 203.0.113.0/24")
check(sh.pool.inside == ["10.0.0.0/8"]
      and sh.pool.outside == ["203.0.113.0/24"],
      "inside and outside append")
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
      and len(rows(text)) == 3,
      f"no neighbour is the usage, all three shapes: {text!r}")
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
CAND = Config.parse("user alice\n role admin\nsnmp-user mon\n auth sha x\n"
                    "nat cgnat pool A\n inside 10.0.0.0/8\n")
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
sh = shell(running="nat cgnat pool B\n inside 10.0.0.0/8\nuser bob\n"
                   "snmp-user other\n auth sha y\n")
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
    })()


def shell(dp=None, api=None, frr=None, running=None):
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT}
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
check(f.running() == ["!", "hostname box", "!", "router bgp 65000",
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
check(sh.where() == "cgnat pool residential",
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
check(_sh.statement_grammar() is prismash.INTERFACE_BLOCK,
      "an interface block resolves against the block's own statements")
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

PAIR_POOL = ("nat cgnat pool residential\n"
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

def at(mode="config", ctx=(), candidate=None):
    """A Shell standing at a level, with a candidate to write into."""
    sh = shell()
    sh.mode = mode
    sh.frr_ctx = list(ctx)
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
    for _line in ("router static", "route 198.19.0.0/24 10.0.0.2", "top"):
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
    ("inside 2001:db8::/32", "expected an IPv4 prefix"),
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
        ("cgnat pool " + "p" * 200, "and the limit is 63", "config"),
        ("user " + "u" * 200, "and the limit is 32", "config")):
    _sh = at()
    with spoken() as _o:
        _sh.do_config(_line)
    check(_needle in _o.getvalue(),
          f"{_line[:24]!r}: {_needle!r} not in {_o.getvalue()!r}")
    check(_sh.mode == _mode,
          f"{_line[:24]!r}: the mode moved to {_sh.mode!r} on a refusal")
    check(not _sh.candidate.pools and not _sh.candidate.users,
          f"{_line[:24]!r}: and nothing was created")
for _text, _needle in (("A" * 4000, "and the limit is 64"),
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
_sh = at()
with spoken() as _o:
    _sh.do_config("cgnat pool residential")
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
        (["cgnat", "identity", "file", "/" + "d" * 1000],
         "and the limit is 256"),
        (["cgnat", "identity", "dhcp-leases", "/" + "d" * 1000],
         "and the limit is 256"),
        (["cgnat", "identity", "radius", "secret", "s" * 500],
         "and the limit is 128")):
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
_sh = at()
with spoken() as _o:
    _sh.do_config("cgnat timeout nosuch 5")
check("no such timeout" in _o.getvalue(),
      f"an unknown timeout is refused where it is typed: {_o.getvalue()!r}")
check(not _sh.candidate.globals,
      f"and nothing staged: {_sh.candidate.globals!r}")
_sh = at()
with spoken() as _o:
    _sh.do_config("cgnat timeout udp 300")
check(_sh.candidate.globals == ["cgnat timeout udp 300"] and not _o.getvalue(),
      f"a real one is staged in silence: {_sh.candidate.globals!r}")
ok("the name and the number, both where the operator can act on them")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
