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


class Dp(hexash.Vpp):
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

    return type("F", (hexash.Frr,), {
        "available": lambda self: available,
        "show": show,
        "reload": lambda self, lines, check_only=False: (0, ""),
    })()


def shell(dp=None, api=None, frr=None, running=None):
    sh = hexash.Shell()
    sh.caps = {hexash.CAP_ALL, hexash.CAP_CGNAT}
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
saved = hexash.VTYSH
try:
    hexash.VTYSH = vtysh
    f = hexash.Frr()
    check(f.show("show version") == (0, "ran: show version"),
          f"one -c carrying the whole command: {f.show('show version')!r}")
    rc, out = f._exec(["-c", "fail me"])
    check(rc == 3 and out == "ran: fail me\nboom",
          f"vtysh's exit code, and stdout then stderr: {(rc, out)!r}")
    hexash.VTYSH = os.path.join(_root, "no-vtysh")
    rc, out = f._exec(["-c", "show version"])
    check(rc == 1 and out.endswith("not found"),
          f"a vtysh that is not there is rc 1 and says so: {(rc, out)!r}")
finally:
    hexash.VTYSH = saved
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
said = hexash.Frr.diagnostics(
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
check(hexash.Frr.diagnostics(None) == [], "no output is nothing said")
ok("INFO, progress and the echoed command list are noise; the rest is kept")

print("\n=== Frr._summarise: the reason, never the footer alone ===")
# Mutation shown red: `useful = keep`, so the footer counts as a reason.
why = hexash.Frr._summarise("% Configuration failed.\n"
                            "The route-map 'PL-X' does not exist.\n")
check(why.endswith("The route-map 'PL-X' does not exist.")
      and why != "% Configuration failed.",
      f"a footer alone falls through to what the daemon said: {why!r}")
why = hexash.Frr._summarise("blah\nExiting: vtysh refused line 5\n")
check(why == "Exiting: vtysh refused line 5",
      f"a line that carries the reason is the summary: {why!r}")
check(hexash.Frr._summarise("only this") == "only this",
      "with nothing recognised, the last line")
check(hexash.Frr._summarise("") == "", "and nothing from nothing")
ok("prefixed lines first, the daemon's own words second, the tail last")

# ------------------------------------------------------------- Rollback

print("\n=== Rollback._file: revision n is <dir>/<n>.conf ===")
# Mutation shown red: f"{n}" with no suffix.
rb = hexash.Rollback(path=os.path.join(_root, "revs"))
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
out = hexash.Shell.renamed_text(
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
sh.confirm = hexash.ConfirmPending(os.path.join(_root, "pending-g"))
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

print("\n=== Shell.forwarding_count: local0 listed, not counted ===")
# Mutation shown red: `len(ifaces)`, counting the null interface.
n, note = hexash.Shell.forwarding_count({"pg0": {}, "local0": {}})
check(n == 1 and len(note) == 1 and "local0 is listed above but not "
      "counted" in note[0], f"one of two forwards, and the note says why: "
      f"{(n, note)!r}")
n, note = hexash.Shell.forwarding_count({"pg0": {}, "pg1": {}})
check((n, note) == (2, []), f"no local0, no note: {(n, note)!r}")
ok("the one holder of that count")

print("\n=== Shell.link_state: a sub-interface's link is its parent's ===")
# Mutation shown red: drop the parent lookup.
hw = {"pg0": {"link": "up", "speed": None}}
check(hexash.Shell.link_state("pg0", hw) == "up", "the interface's own")
check(hexash.Shell.link_state("pg0.100", hw) == "up",
      "pg0.100 is not hardware; its link is pg0's")
check(hexash.Shell.link_state("pg9", hw) == "?",
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
check("  2 interface(s) on the dataplane can forward" in text
      and any("local0 is listed above" in r for r in text),
      f"the forwarding count and its note: {text!r}")
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
check(utf == hexash.Shell.BARS_UTF and ascii_ == hexash.Shell.BARS_ASCII,
      f"UTF gets the blocks, a serial console in ASCII gets its own: "
      f"{(utf, ascii_)!r}")
ok("a graph a serial console can draw")

print("\n=== Shell.spark: a trickle is not silence ===")
# Mutation shown red: drop `level = max(1, level)`.
sh = shell()
sh.bars = lambda: hexash.Shell.BARS_ASCII
c = hexash.Shell.BARS_ASCII
check(sh.spark([0, 5, 10], 10) == c[0] + c[4] + c[8],
      f"zero, half, full: {sh.spark([0, 5, 10], 10)!r}")
check(sh.spark([0.01], 10) == c[1],
      f"traffic that rounds to nothing is drawn at the lowest level: "
      f"{sh.spark([0.01], 10)!r}")
check(sh.spark([3, 4], 0) == c[0] * 2, "no scale is a flat baseline")
ok("eight levels against `full`, the baseline only for a real zero")

print("\n=== Shell.rate: the multiple an operator would have said ===")
# Mutation shown red: `bits > div`, so exactly 1000 is 1000 bit/s.
rates = (hexash.Shell.rate(1.5e9), hexash.Shell.rate(675.56e6),
         hexash.Shell.rate(1000), hexash.Shell.rate(999))
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

print("\n=== Shell.show_one_interface: the counters, the first row kept ===")
# Mutation shown red: `one.splitlines()[2:]`, dropping the interface's own
# row and with it the first counter (#280).
ONE = ("              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)"
       "     Counter          Count\n"
       "pg0 1 up 9000/0/0/0 rx packets 5\n"
       "                                                   rx bytes 300\n")
api = Api(rows={"sw_interface_dump": SW_ROWS}, have={"pg0": 1},
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
             "counters", "rx packets 5", "rx bytes 300"):
    check(want in text, f"{want!r} is on the screen: {text!r}")
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
check(hexash.Shell._sysfs(attr) == "1000", "stripped")
check(hexash.Shell._sysfs(os.path.join(_root, "no-attr")) is None,
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
sh.TUNE_ENV = script("tune.env", 'HEXA_DATAPLANE_IF="ens18"\n'
                     "OTHER='x'\nHEXA_DATAPLANE_IF=ens19\n", mode=0o644)
check(sh.read_tune_key("HEXA_DATAPLANE_IF") == "ens19",
      f"last wins, as systemd reads it: {sh.read_tune_key('HEXA_DATAPLANE_IF')!r}")
check(sh.read_tune_key("OTHER") == "x", "quotes off")
check(sh.read_tune_key("NOPE") == "", "not set is empty")
sh.TUNE_ENV = os.path.join(_root, "no-tune.env")
check(sh.read_tune_key("HEXA_DATAPLANE_IF") == "", "no file is empty")
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
                   sorted(["pg0", "pg1", "loop0", "local0", "pg0.100"])),
      f"terse, filtered, with the whole table as the total: {seen[-1]!r}")
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
DB = "hexarouting.00-00    *    108   0x00000005  0x0113    1160    0/0/{}\n"
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
check(any(r.startswith("  198.19.0.0/24") and "** in FRR, NOT forwarding"
          in r for r in text), f"verbose compares the two: {text!r}")
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

print("\n=== Shell._route_rows_verbose: in FRR and not forwarding ===")
# Mutation shown red: `if in_rib and in_fib` for the mark.
rib = routes.parse_rib(RIB)
sh = shell()
with spoken() as o:
    sh._route_rows_verbose(table, rib, [])
text = rows_of(o.getvalue())
marked = [r for r in text if "** in FRR, NOT forwarding" in r]
check(len(marked) == 2 and all(r.startswith(("  198.19.0.0/24",
                                              "  10.9.0.0/16"))
                                for r in marked),
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
      and any("not the HexaRouting build above" in r for r in text),
      f"a different build: the sentence under the row: {text!r}")
saved = hexash.VERSION_PATH
try:
    hexash.VERSION_PATH = os.path.join(_root, "no-such-stamp")
    with spoken() as o:
        sh.show_plugin_build()
    check(any("running from a source tree" in r
              for r in rows_of(o.getvalue())),
          f"no stamp to compare against says so: {o.getvalue()!r}")
finally:
    hexash.VERSION_PATH = saved
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
    hexash.Shell.print_daemon_list("  daemons:   running  ", ["bgpd", "zebra"])
check(o.getvalue() == "  daemons:   running  bgpd zebra\n", o.getvalue())
with spoken() as o:
    hexash.Shell.print_daemon_list("  daemons:   stopped  ", [])
check(o.getvalue() == "  daemons:   stopped  (none)\n", o.getvalue())
many = [f"daemon{n:02d}" for n in range(12)]
with spoken() as o:
    hexash.Shell.print_daemon_list("  daemons:   running  ", many)
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
sh.rb = hexash.Rollback(path=revs)
with spoken() as o:
    sh.show_rollback()
text = rows_of(o.getvalue())
check(len(text) == 2 and text[0].startswith("   0  ")
      and text[0].endswith("  current  first commit")
      and text[1].startswith("   1  ") and text[1].endswith("  1 commit(s) ago"),
      f"{text!r}")
sh.rb = hexash.Rollback(path=os.path.join(_root, "revs-empty"))
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
saved_rl = hexash.readline
try:
    hexash.readline = types.SimpleNamespace(get_line_buffer=lambda: "show cg")
    check(sh.complete("cg", 0) == "cgnat " and sh.complete("cg", 1) is None,
          f"the one option, a space after it, then nothing: "
          f"{(sh.complete('cg', 0), sh.complete('cg', 1))!r}")
    sh._options = lambda line, ending: ({}, "<address>", "")
    check(sh.complete("", 0) == "<address>" and sh.complete("", 1) == "​",
          f"a hint is shown and never inserted: "
          f"{(sh.complete('', 0), sh.complete('', 1))!r}")
finally:
    hexash.readline = saved_rl
ok("readline's completer, over _options")

print("\n=== Shell.display_matches: the options, then the prompt again ===")
# Mutation shown red: the prompt redraw dropped.
sh = shell()
sh._last_opts = {"pool": "the pools", "session": "the sessions"}
sh._last_hint = None
saved_rl = hexash.readline
try:
    hexash.readline = types.SimpleNamespace(
        get_line_buffer=lambda: "show cgnat ")
    with spoken() as o:
        sh.display_matches("", [], 0)
    text = o.getvalue()
    check("  pool     the pools\n  session  the sessions\n" in text,
          f"aligned, with what each is: {text!r}")
    check(text.endswith("hexarouting> show cgnat "),
          f"the line being typed, redrawn: {text!r}")
    sh._last_opts, sh._last_hint = {}, None
    with spoken() as o:
        sh.display_matches("", [], 0)
    check("  (no known options here; the command is passed to FRR)"
          in rows_of(o.getvalue()), o.getvalue())
finally:
    hexash.readline = saved_rl
ok("what readline shows on a double tab")

# ---------------------------------------------------------- the banner

print("\n=== Shell.say_handed_ssh_host_keys: only when it is true ===")
# Mutation shown red: `if not handed: return` dropped.
saved_origin = hexash.SSH_HOST_KEY_ORIGIN
try:
    hexash.SSH_HOST_KEY_ORIGIN = script(
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
    hexash.SSH_HOST_KEY_ORIGIN = script("origin-own", "#!/bin/sh\nexit 0\n")
    with spoken() as o:
        sh.say_handed_ssh_host_keys()
    check(o.getvalue() == "", "a box that made its own keys hears nothing")
finally:
    hexash.SSH_HOST_KEY_ORIGIN = saved_origin
ok("a banner, not a health line")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
