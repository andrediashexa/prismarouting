#!/usr/bin/env python3
"""
The module-level helpers of hexash.py, each called on its own (#390).

WHY A SUITE OF ITS OWN. The other hexash suites are organised by behaviour:
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
in through the `origin` argument.
"""
import os
import sys
import tempfile
import types

_root = tempfile.mkdtemp(prefix="hexash-units-")
# Read at import: every path hexash.py takes from the environment points into
# this suite's own directory, so no box here reads /etc or /usr/share on the
# machine running it.
os.environ["HEXA_CONFIG"] = os.path.join(_root, "hexarouting.conf")
os.environ["HEXA_CONFIRM"] = os.path.join(_root, "confirm-pending")
os.environ["HEXA_ROLLBACK"] = os.path.join(_root, "rollback")
os.environ["HEXA_VERSION_FILE"] = os.path.join(_root, "VERSION-stamp")
os.environ["HEXA_LIVE_SQUASHFS"] = os.path.join(_root, "filesystem.squashfs")
os.environ["HEXA_INSTALLER"] = os.path.join(_root, "hexarouting-install")
os.environ["HEXA_SSH_HOST_KEY_ORIGIN"] = os.path.join(_root, "no-origin")
with open(os.environ["HEXA_VERSION_FILE"], "w") as _f:
    _f.write("0.99.7\n")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src",
                                "hexash"))
import hexash      # noqa: E402
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

print("=== hexarouting_version_source: which file answered ===")
# Mutation shown red: return VERSION_FROM_TREE for the stamp.
v, src = hexash.hexarouting_version_source()
check((v, src) == ("0.99.7", hexash.VERSION_FROM_STAMP),
      f"the installed stamp answers first, as 'stamp': {(v, src)!r}")
saved = hexash.VERSION_PATH
try:
    hexash.VERSION_PATH = os.path.join(_root, "no-such-stamp")
    v, src = hexash.hexarouting_version_source()
    with open(os.path.join(os.path.dirname(hexash.__file__), "..", "..",
                           "VERSION")) as f:
        tree_version = f.read().strip()
    check((v, src) == (tree_version, hexash.VERSION_FROM_TREE),
          f"with no stamp the tree's VERSION answers, as 'tree': {(v, src)!r}")
    empty = os.path.join(_root, "VERSION-empty")
    open(empty, "w").close()
    hexash.VERSION_PATH = empty
    v, src = hexash.hexarouting_version_source()
    check(src == hexash.VERSION_FROM_TREE and v == tree_version,
          f"an empty stamp states no version and falls through: {(v, src)!r}")
finally:
    hexash.VERSION_PATH = saved
check(hexash.hexarouting_version() == "0.99.7",
      "hexarouting_version() is the text half of the same reading")
ok("stamp, then tree, and an empty file is not a version")

print("\n=== version_split: only an installed stamp can disagree ===")
# Mutation shown red: drop the `source == VERSION_FROM_STAMP` term.
STAMP, TREE = hexash.VERSION_FROM_STAMP, hexash.VERSION_FROM_TREE
check(hexash.version_split("0.24.0", "0.25.0", STAMP) is True,
      "a stamp that differs from the loaded build is a split")
check(hexash.version_split("0.25.0", "0.25.0", STAMP) is False,
      "the same build is not a split")
check(hexash.version_split("0.24.0", "0.25.0", TREE) is False,
      "a source tree names the next release, not a build: no split")
check(hexash.version_split(None, "0.25.0", STAMP) is False,
      "a dataplane that never spoke states no build")
check(hexash.version_split("", "0.25.0", STAMP) is False,
      "a dataplane without the plugin states no build")
ok("True only for stamp + a build that is stated + a build that differs")

# --------------------------------------------------------- the interface

print("\n=== dataplane_statement: the whole keyword, not its first word ===")
# Mutation shown red: compare words[:1] instead of words[:len(stmt)].
check(hexash.dataplane_statement(["ipv4", "address", "10.0.0.1/24"])
      == "ipv4 address", "ipv4 address is the dataplane's")
check(hexash.dataplane_statement(["ipv6", "nd", "suppress-ra"])
      == "ipv6 nd suppress-ra", "the three-word statement is matched whole")
check(hexash.dataplane_statement(["ipv6", "nd", "ra-interval", "30"]) is None,
      "ipv6 nd <anything else> is FRR's")
check(hexash.dataplane_statement(["mtu", "9000"]) == "mtu",
      "a one-word statement carries its argument")
check(hexash.dataplane_statement(["ip", "ospf", "cost", "10"]) is None,
      "an FRR statement is None")
ok("each entry of INTERFACE_DATAPLANE is matched by its full length")

# --------------------------------------------------------------- the pool

print("\n=== _pool_no_item: a member the pool has, or a refusal ===")
# Mutation shown red: answer None for a member the pool lacks, passing over
# the typo in silence.
lst = ["10.0.0.0/8", "172.16.0.0/12"]
check(hexash._pool_no_item(lst, "inside", ["no", "inside"])
      == "usage: no inside <prefix>", "a missing prefix is a usage line")
check(hexash._pool_no_item(lst, "inside", ["no", "inside", "192.0.2.0/24"])
      == "the pool has no inside 192.0.2.0/24",
      "a member the pool does not have is refused by name")
check(lst == ["10.0.0.0/8", "172.16.0.0/12"], "a refusal removes nothing")
check(hexash._pool_no_item(lst, "inside", ["no", "inside", "10.0.0.0/8"])
      is None and lst == ["172.16.0.0/12"],
      f"the named member is removed and nothing is said: {lst}")
ok("usage, refusal by name, removal")

print("\n=== _pool_no_timeout: drop an override, or say which exist ===")
# Mutation shown red: leave p.timeouts untouched.
p = Pool("A")
p.timeouts = {"udp": 30, "icmp": 10}
usage = hexash._pool_no_timeout(p, ["no", "timeout", "bogus"])
check(usage is not None and usage.startswith("usage: no timeout <")
      and "udp" in usage, f"an unknown timeout names the list: {usage!r}")
check(hexash._pool_no_timeout(p, ["no", "timeout", "udp"]) is None
      and p.timeouts == {"icmp": 10}, f"udp override dropped: {p.timeouts}")
check(hexash._pool_no_timeout(p, ["no", "timeout", "udp"]) is None,
      "dropping an override already absent is the state asked for")
ok("the override goes and the others stay")

print("\n=== _pool_no_alg: the same, for an ALG ===")
# Mutation shown red: pop w[2] from p.timeouts instead of p.algs.
p = Pool("A")
p.algs = {"ftp": True, "rtsp": False}
usage = hexash._pool_no_alg(p, ["no", "alg"])
check(usage is not None and usage.startswith("usage: no alg <")
      and "ftp" in usage, f"a missing ALG names the list: {usage!r}")
check(hexash._pool_no_alg(p, ["no", "alg", "rtsp"]) is None
      and p.algs == {"ftp": True}, f"rtsp override dropped: {p.algs}")
ok("the ALG override goes and the other stays")

print("\n=== _pool_no_bypass: one prefix or the whole list, per direction ===")
# Mutation shown red: pick bypass_src for "destination".
p = Pool("A")
p.bypass_src = ["10.1.0.0/16", "10.2.0.0/16"]
p.bypass_dst = ["198.51.100.0/24"]
check(hexash._pool_no_bypass(p, ["no", "bypass", "sideways"])
      == "usage: no bypass <source|destination> [<prefix>]",
      "a direction that is neither is a usage line")
check(hexash._pool_no_bypass(p, ["no", "bypass", "source", "10.1.0.0/16"])
      is None and p.bypass_src == ["10.2.0.0/16"],
      f"one source prefix removed: {p.bypass_src}")
check(hexash._pool_no_bypass(p, ["no", "bypass", "source", "10.9.0.0/16"])
      == "the pool has no bypass source 10.9.0.0/16",
      "a prefix the list lacks is refused with the direction in it")
check(hexash._pool_no_bypass(p, ["no", "bypass", "destination"]) is None
      and p.bypass_dst == [] and p.bypass_src == ["10.2.0.0/16"],
      f"no prefix clears the destination list and only it: "
      f"{p.bypass_src} {p.bypass_dst}")
ok("source and destination are two lists, one prefix or all of them")

print("\n=== _pool_no_forward: one port names one forward per address ===")
# Mutation shown red: ignore `ip` when selecting the hit.
p = Pool("A")
p.forwards = [("tcp", 1100, "10.0.0.5", 22), ("tcp", 1100, "10.0.0.6", 22),
              ("udp", 5060, "10.0.0.7", 5060)]
check(hexash._pool_no_forward(p, ["no", "forward", "tcp"])
      == "usage: no forward <tcp|udp> <port> [to <private-ip> <port>]",
      "a missing port is a usage line")
check(hexash._pool_no_forward(p, ["no", "forward", "tcp", "2200"])
      == "the pool has no forward tcp 2200", "a port nobody forwards")
pair = hexash._pool_no_forward(p, ["no", "forward", "tcp", "1100"])
check(isinstance(pair, tuple) and len(pair) == 3
      and "more than one subscriber" in pair[0]
      and "10.0.0.5, 10.0.0.6" in pair[1]
      and pair[2].endswith(
          "no forward tcp 1100 to <private-ip> <private-port>"),
      f"two subscribers on one port: three rows and no removal: {pair!r}")
check(len(p.forwards) == 3, "the ambiguous ask removed nothing")
check(hexash._pool_no_forward(
        p, ["no", "forward", "tcp", "1100", "to", "10.0.0.6", "22"]) is None
      and p.forwards == [("tcp", 1100, "10.0.0.5", 22),
                         ("udp", 5060, "10.0.0.7", 5060)],
      f"'to <ip> <port>' names the subscriber, and only that one goes: "
      f"{p.forwards}")
check(hexash._pool_no_forward(p, ["no", "forward", "udp", "5060", "10.0.0.7"])
      is None and p.forwards == [("tcp", 1100, "10.0.0.5", 22)],
      f"the ip without 'to' also names the subscriber: {p.forwards}")
check(hexash._pool_no_forward(p, ["no", "forward", "tcp", "1100"]) is None
      and p.forwards == [], "one hit on the bare port is removed")
ok("usage, no such forward, an ambiguous pair, and the named subscriber")

print("\n=== _pool_no_clear: the handler it returns sets the omitted value")
# Mutation shown red: setattr(p, attr, None) instead of `value`.
p = Pool("A")
p.max_blocks = 40
go = hexash._pool_no_clear("max_blocks", 0)
check(go(p, ["no", "max-blocks"]) is None, "the handler says nothing")
check(p.max_blocks == 0, f"max_blocks is back at 0: {p.max_blocks!r}")
p.vrf = "7"
check(hexash._pool_no_clear("vrf", "")(p, None) is None and p.vrf == "",
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
        return hexash.capabilities()
    finally:
        os.geteuid = real_euid
        for m, mod in real.items():
            if mod is None:
                sys.modules.pop(m, None)
            else:
                sys.modules[m] = mod


print("\n=== capabilities: the system's groups, never anything stored ===")
# Mutation shown red: on the NSS exception return {CAP_ALL, CAP_CGNAT}.
BOTH = {hexash.CAP_ALL, hexash.CAP_CGNAT}
users = _Group("users", 100)
admin = _Group(system.GROUP_ADMIN, 1001, ["alice"])
cgnat = _Group(system.GROUP_CGNAT, 1002, ["bob"])
check(caps_as(0, "root", [users], 0) == BOTH, "root has both")
check(caps_as(1000, "alice", [users, admin, cgnat], 100) == BOTH,
      "a member of hexa-admin has both")
check(caps_as(1000, "bob", [users, admin, cgnat], 100)
      == {hexash.CAP_CGNAT}, "a member of hexa-cgnat has CGNAT only")
check(caps_as(1000, "carol", [users, admin, cgnat], 1002)
      == {hexash.CAP_CGNAT}, "a primary group counts as membership too")
check(caps_as(1000, "dave", [users, admin, cgnat], 100) == set(),
      "neither group is read-only")
check(caps_as(1000, "alice", [users, admin, cgnat], 100, broken=True)
      == set(), "an NSS that cannot describe the account grants nothing")
ok("root, hexa-admin, hexa-cgnat by member and by primary group, and NSS down")

print("\n=== role_name: the rank in the spelling the user block takes ===")
# Mutation shown red: answer ROLE_CGNAT for CAP_ALL.
check(hexash.role_name(BOTH) == system.ROLE_ADMIN, "both caps is admin")
check(hexash.role_name({hexash.CAP_CGNAT}) == system.ROLE_CGNAT,
      "CGNAT alone is the cgnat role")
check(hexash.role_name(set()) == "read-only", "no cap is read-only")
ok("three ranks, three names")

# ------------------------------------------------------------ the export

print("\n=== udp_export_without_collector: enable with nowhere to send ===")
# Mutation shown red: let `cgnat logging tcp <host>` satisfy the collector.
check(hexash.udp_export_without_collector(["cgnat logging enable"]) is True,
      "enable alone would be refused by the dataplane")
check(hexash.udp_export_without_collector(
        ["cgnat logging enable", "cgnat logging udp 10.0.0.9 4739"]) is False,
      "a UDP collector satisfies it")
check(hexash.udp_export_without_collector(
        ["cgnat logging enable", "cgnat logging udp disable"]) is True,
      "'udp disable' is not a collector")
check(hexash.udp_export_without_collector(
        ["cgnat logging enable", "cgnat logging tcp 10.0.0.9 4739"]) is True,
      "a TCP collector is the other transport and does not satisfy it")
check(hexash.udp_export_without_collector(
        ["cgnat logging udp 10.0.0.9 4739"]) is False,
      "without enable there is no export to refuse")
check(hexash.udp_export_without_collector([]) is False,
      "no statements ask for nothing")
ok("True only for enable with no UDP collector")

# ------------------------------------------------------- the live medium

print("\n=== running_from_live_medium: the squashfs and an installer ===")
# Mutation shown red: os.access(INSTALLER, os.R_OK) instead of X_OK.
sq = os.environ["HEXA_LIVE_SQUASHFS"]
inst = os.environ["HEXA_INSTALLER"]
check(hexash.running_from_live_medium() is False,
      "neither file: an installed box")
open(sq, "w").close()
check(hexash.running_from_live_medium() is False,
      "the medium without an installer is not one to install from")
with open(inst, "w") as f:
    f.write("#!/bin/sh\n")
os.chmod(inst, 0o644)
check(hexash.running_from_live_medium() is False,
      "an installer that cannot be executed is not one")
os.chmod(inst, 0o755)
check(hexash.running_from_live_medium() is True,
      "the medium and an executable installer: live")
os.unlink(sq)
check(hexash.running_from_live_medium() is False,
      "the installer alone is not the medium")
ok("both files, and the installer executable")

# ------------------------------------------------------------- the tree

print("\n=== _starts: completion ignores case, and only case ===")
# Mutation shown red: candidate.startswith(prefix) with no lower().
check(hexash._starts("TenGigabitEthernet0/8/0", "ten") is True,
      "the operator's lower case reaches the box's spelling")
check(hexash._starts("ens19", "ENS") is True, "and the other way round")
check(hexash._starts("ens19", "eth") is False, "a different name is not it")
check(hexash._starts("ens19", "ens19x") is False,
      "a prefix longer than the candidate is not it")
ok("case-insensitive prefix")

print("\n=== _frr_static: route <prefix> ... in FRR's spelling ===")
# Mutation shown red: pick the family off the next hop instead of the prefix.
check(hexash._frr_static(["10.0.0.0/8", "10.1.1.1"])
      == "ip route 10.0.0.0/8 10.1.1.1", "an IPv4 prefix is 'ip route'")
check(hexash._frr_static(["2001:db8::/32", "2001:db8:1::1", "vppwan0"])
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
sub, hint = hexash.walk_from(node, [])
check(sub is node and hint is None, "no words: the node itself")
sub, hint = hexash.walk_from(node, ["show"])
check(isinstance(sub, dict) and set(sub) == {"route", "version"}
      and hint is None,
      f"'show' offers its subtree: {(sub, hint)!r}")
check(hexash.walk_from(node, ["show", "route"]) == (None, "<prefix>"),
      "a leaf with an argument offers its hint and nothing more")
check(hexash.walk_from(node, ["show", "version"]) == (None, None),
      "a leaf without one offers nothing")
check(hexash.walk_from(node, ["show", "bgp"]) == (None, None),
      "an unknown word is FRR territory from there")
check(hexash.walk_from(node, ["show", "version", "more"]) == (None, None),
      "words past a leaf reach nothing")
check(hexash.walk_from(None, ["show"]) == (None, None),
      "no node to walk from")
ok("subtree, hint, or nothing")

print("\n=== tree_walk: the same walk from a mode's root ===")
# Mutation shown red: walk TREE["config"] whatever the mode.
sub, hint = hexash.tree_walk("operational", ["system"])
check(isinstance(sub, dict) and set(sub) == {"reboot", "shutdown"}
      and hint is None, f"'system' in operational mode: {(sub, hint)!r}")
check(hexash.tree_walk("operational", ["clear", "ospf", "neighbor"])
      == (None, "<A.B.C.D>"), "a hint reached three words down")
check(hexash.tree_walk("operational", ["pool"]) == (None, None),
      "a config word is not in operational mode")
check(hexash.tree_walk("no-such-mode", ["show"]) == (None, None),
      "a mode the tree lacks offers nothing")
ok("a mode's root, then walk_from")

print("\n=== _tree_reads: did the tree read every word ===")
# Mutation shown red: `if node is None: return True`.
node = {"bgp": ("bgp", {
            "log-neighbor-changes": ("log", None),
            "router-id": ("id", "<A.B.C.D>")}),
        "exit": ("leave", None)}
check(hexash._tree_reads(node, ["bgp", "log-neighbor-changes"]) is True,
      "a statement the tree defines to its end")
check(hexash._tree_reads(node, ["bgp", "router-id", "10.0.0.1"]) is True,
      "an argument: the rest is its business")
check(hexash._tree_reads(node, ["bgp", "router-id", "10.0.0.1", "x"]) is True,
      "and however many words follow the argument")
check(hexash._tree_reads(node, ["bgp", "log-neighbor-changes", "x"]) is False,
      "a word after the statement ended was not read")
check(hexash._tree_reads(node, ["bgp", "bestpath"]) is False,
      "a word the tree has no entry for was not read")
check(hexash._tree_reads(node, ["bgp"]) is True,
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
handed, fixes = hexash.ssh_host_keys_handed_to_this_box(origin)
check(handed == ["ssh_host_rsa_key", "ssh_host_ed25519_key"],
      f"the 'handed' line's names: {handed}")
check(fixes == ["rm /etc/ssh/ssh_host_*_key", "ssh-keygen -A"],
      f"every 'fix' line with a command on it, in order: {fixes}")
origin = script("origin-own", "#!/bin/sh\necho 'handed'\necho 'own 3'\n")
check(hexash.ssh_host_keys_handed_to_this_box(origin) == ([], []),
      "a box that made its own keys: nothing to say")
origin = script("origin-failed", "#!/bin/sh\n"
                "echo 'handed ssh_host_rsa_key'\nexit 1\n")
check(hexash.ssh_host_keys_handed_to_this_box(origin) == ([], []),
      "a program that failed is not believed, whatever it printed")
check(hexash.ssh_host_keys_handed_to_this_box(
        os.path.join(_root, "no-such-program")) == ([], []),
      "a program that is not there: silent")
ok("names and fixes, and silence for own keys, a failure, or no program")

# ------------------------------------------------------------ the routing

print("\n=== as_path_parts: the regex by the keyword, not by position ===")
# Mutation shown red: return (w[3], " ".join(w[5:])) positionally.
check(hexash.as_path_parts("bgp as-path access-list RX1 seq 5 permit ^65001$")
      == ("RX1", "^65001$"), "FRR's read-back with a seq")
check(hexash.as_path_parts("bgp as-path access-list RX1 permit ^65001$")
      == ("RX1", "^65001$"), "the form an operator types")
check(hexash.as_path_parts("bgp as-path access-list RX1 deny _65002_ .*")
      == ("RX1", "_65002_ .*"), "deny, and a regex with a space in it")
check(hexash.as_path_parts("bgp as-path access-list RX1 seq 5")
      == (None, None), "no permit or deny: not one")
check(hexash.as_path_parts("ip prefix-list PL permit 10.0.0.0/8")
      == (None, None), "another statement: not one")
ok("(name, regex) after permit or deny, else (None, None)")

print("\n=== interface_aliases: every name a pair answers to ===")
# Mutation shown red: drop `| {name}`.
pairs = {"vppwan0": ("host-veth-vpp", "tap4096", "vppwan0"),
         "tap4096": ("host-veth-vpp", "tap4096", "vppwan0")}
check(hexash.interface_aliases("vppwan0", pairs)
      == {"host-veth-vpp", "tap4096", "vppwan0"}, "the pair, itself included")
check(hexash.interface_aliases("ens19", pairs) == {"ens19"},
      "an interface in no pair answers to its own name")
check(hexash.interface_aliases("ens19", None) == {"ens19"},
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
in_area, quiet, ignoring, has = hexash.ospf6_state(routing)
check(in_area == {"eth0": "0.0.0.0"}, f"areas by interface: {in_area}")
check(quiet == {"eth0"}, f"passive: {quiet}")
check(ignoring == {"eth1"}, f"mtu-ignore: {ignoring}")
check(has is True, "router ospf6 means OSPFv3 is configured")
check(hexash.ospf6_state(["interface eth0", " ip ospf area 0"])
      == ({}, set(), set(), False), "OSPFv2 statements are not OSPFv3")
check(hexash.ospf6_state(["interface eth2", " ipv6 ospf6 area 1"])[3] is True,
      "an interface in an area is OSPFv3 even with no router stanza")
ok("area, passive, mtu-ignore per interface, and has_ospf6")

print("\n=== rpki_findings: a policy that does nothing without a cache ===")
# Mutation shown red: drop `not caches`.
rm = ["route-map RPKI deny 10", " match rpki notfound",
      "route-map RPKI deny 20", " match rpki invalid",
      "route-map RPKI permit 30", " match rpki valid"]
out = hexash.rpki_findings(rm)
check(len(out) == 3, f"three clauses, three findings: {out}")
check(out[0][0] == "match rpki notfound"
      and "matches all of them" in out[0][1],
      f"notfound with no cache matches everything: {out[0]!r}")
check(out[1][0] == "match rpki invalid"
      and "nothing is ever invalid" in out[1][1],
      f"invalid with no cache matches nothing: {out[1]!r}")
check("nothing is ever valid" in out[2][1], f"valid likewise: {out[2]!r}")
check(hexash.rpki_findings(["rpki cache 10.0.0.9 3323 preference 1"] + rm)
      == [], "with a cache configured the policy means what it says")
check(hexash.rpki_findings(["route-map X permit 10", " match ip address PL"])
      == [], "no rpki clause, nothing to say")
ok("findings only without a cache")

print("\n=== set_value_error: the one bound FRR does not enforce ===")
# Mutation shown red: `lo <= int(w[2]) < hi`, refusing the top of the range.
check(hexash.set_value_error("set local-preference 100") is None,
      "a number in range")
check(hexash.set_value_error("set local-preference 4294967295") is None,
      "the top of the range is in it")
check(hexash.set_value_error("set local-preference 0") is None,
      "and the bottom")
e = hexash.set_value_error("set local-preference banana")
check(e is not None and e.startswith("'banana' is not a local-preference: "
                                     "expected 0-4294967295"),
      f"a word is refused with the range: {e!r}")
e = hexash.set_value_error("set local-preference 4294967296")
check(e is not None and e.startswith("'4294967296' is not a local-preference"),
      f"one past the top is refused: {e!r}")
check(hexash.set_value_error("set metric banana") is None,
      "a statement FRR bounds itself is left to FRR")
check(hexash.set_value_error("set local-preference") is None,
      "a statement with no value is not this function's")
check(hexash.set_value_error("match local-preference 100") is None,
      "match is not set")
ok("refused with the range, and only for the table's entries")

print("\n=== route_map_references: where a name is used, and what kind ===")
# Mutation shown red: drop the `description` skip.
refs = hexash.route_map_references([
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
seen = hexash.route_maps_defined([
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
findings = hexash.route_map_findings([
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
check(hexash.prefix_range_error(
        "ip prefix-list PL permit 10.0.0.0/8 ge 16 le 24")
      is None, "8 <= 16 <= 24 <= 32 holds")
e = hexash.prefix_range_error(
    "ip prefix-list PL permit 10.0.0.0/8 ge 24 le 16")
check(e is not None and "ge 24" in e and "le 16" in e,
      f"ge above le is refused, with both numbers read: {e!r}")
check(hexash.prefix_range_error("ip prefix-list PL permit 10.0.0.0/8 ge x")
      is None, "a ge that is not a number is FRR's to refuse")
ok("ge and le read from the word after each keyword")

print("\n=== ent (bgp_errors): one record per neighbour, however named ===")
# Mutation shown red: a fresh record on every call, so a group's remote-as is
# never seen by its member.
errs = hexash.bgp_errors([
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
warn = hexash.max_prefix_findings([
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
out = hexash.ospf_findings(routing, {"host-veth-vpp": ["10.0.0.1/24"]},
                           {"host-veth-vpp", "tap4096", "vppwan0"}, {},
                           lcp_pairs=pairs,
                           device_interfaces={"host-veth-vpp", "tap4096"})
check(any("vppwan0" in f[2] and "host-veth-vpp" in f[1] for f in out),
      f"passive-interface on the dataplane's name acts on nothing, and the "
      f"finding names the Linux end: {out}")
out = hexash.ospf_findings(["router ospf", " network 10.0.0.0/24 area 0",
                            " passive-interface vppwan0"],
                           {"host-veth-vpp": ["10.0.0.1/24"]},
                           {"host-veth-vpp", "tap4096", "vppwan0"}, {},
                           lcp_pairs=pairs,
                           device_interfaces={"host-veth-vpp", "tap4096"})
check(not any("passive-interface" in f[1] for f in out),
      f"the Linux end itself is the spelling FRR acts on: {out}")
ok("the pair's members are one interface, and only one of them acts")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
