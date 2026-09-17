#!/usr/bin/env python3
"""
The module-level helpers of hexash.py, each called on its own (#390), and
below them the Shell's applying methods over recording stubs (batch H).

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
in through the `origin` argument. The batch H section at the end builds a
Shell over an API, a dataplane CLI, an Frr and a System runner that each
record what they were asked, points vtysh, the installer and the login shell
at scripts of this suite's own, and puts every file the methods write under
the suite's directory.
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


class RecDp(hexash.Vpp):
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


class RecFrr(hexash.Frr):
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
    reason test_hexash_apply.py records: Shell() reads the account's real
    groups, and left alone every privileged box here is refused first.
    """
    sh = hexash.Shell()
    sh.caps = {hexash.CAP_ALL, hexash.CAP_CGNAT} if caps is None else caps
    sh.api = api or RecApi()
    sh.dp = dp or RecDp()
    sh.frr = frr or RecFrr()
    sh.sys = system.System(runner=runner or RecRunner())
    text = running or ""
    sh.running_config = lambda: Config.parse(text)
    sh.confirm = hexash.ConfirmPending(os.path.join(_root, "confirm-none"))
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
saved = hexash.VTYSH
try:
    hexash.VTYSH = vtysh
    rc, out = hexash.Frr()._exec(["-c", "show version"])
    check(rc == 3 and out == "from vtysh",
          f"rc and text come from the program, rstripped: {(rc, out)!r}")
    check(argv_of(log) == ["-c", "show version"],
          f"the argv handed to vtysh is the one given: {argv_of(log)}")
    both = script("vtysh-both", "#!/bin/sh\necho out; echo err >&2; exit 0\n")
    hexash.VTYSH = both
    rc, out = hexash.Frr()._exec(["-c", "x"])
    check(out == "out\nerr", f"stdout then stderr, as one text: {out!r}")
    hexash.VTYSH = os.path.join(_root, "no-such-vtysh")
    rc, out = hexash.Frr()._exec(["-c", "x"])
    check(rc == 1 and out.endswith("not found"),
          f"a vtysh that is not there is rc 1 and says so: {(rc, out)!r}")
finally:
    hexash.VTYSH = saved
ok("argv verbatim, rc as exited, stdout and stderr both kept")

print("\n=== Frr.apply_lines: configure terminal, then one -c per line ===")
# Mutation shown red: drop the leading "configure terminal".
vtysh, log = argv_script("vtysh-apply")
saved = hexash.VTYSH
try:
    hexash.VTYSH = vtysh
    rc, out = hexash.Frr().apply_lines(["router bgp 65000",
                                        " neighbor 10.0.0.2 remote-as 65001"])
    check(argv_of(log) == ["-c", "configure terminal",
                           "-c", "router bgp 65000",
                           "-c", " neighbor 10.0.0.2 remote-as 65001"],
          f"the lines follow configure terminal, in order: {argv_of(log)}")
    check(rc == 0, "and the program's rc is the answer")
finally:
    hexash.VTYSH = saved
ok("one vtysh run, the lines in the order given")

print("\n=== Frr.absorb_stale_refusal: the one absorber that was measured "
      "to work ===")
# Mutation shown red: send "ip prefix-list" instead of "no ip prefix-list".
vtysh, log = argv_script("vtysh-absorb", rc=1)
saved = hexash.VTYSH
try:
    hexash.VTYSH = vtysh
    hexash.Frr().absorb_stale_refusal()
    check(argv_of(log) == ["-c", "configure terminal", "-c",
                           f"no ip prefix-list {hexash.Frr.ABSORB_NAME}"],
          f"a real commit whose effect is nothing: {argv_of(log)}")
    hexash.VTYSH = os.path.join(_root, "no-such-vtysh")
    hexash.Frr().absorb_stale_refusal()
    check(True, "a vtysh that is not there raises nothing: best effort")
finally:
    hexash.VTYSH = saved
ok("no ip prefix-list <a name nobody has>, and a missing vtysh is not "
   "an error")

print("\n=== Frr.daemons: None when FRR did not say, a set when it did ===")
# Mutation shown red: return set() when rc != 0.
check(hexash.Frr.daemons(RecFrr()) is None,
      "vtysh refusing is None, not the empty set")
check(hexash.Frr.daemons(RecFrr({"show daemons": (0, " \n")})) is None,
      "an empty answer is None as well: FRR did not say")
check(hexash.Frr.daemons(RecFrr({"show daemons": (0, "zebra bgpd\nospfd\n")}))
      == {"zebra", "bgpd", "ospfd"},
      "the words of the answer, wherever the newlines fall, are the set")
ok("down and silent are None; an answer is its words")

print("\n=== Frr.running: the statements, without vtysh's framing ===")
# Mutation shown red: keep the "frr version" and "end" lines.
text = ("Building configuration...\n\nCurrent configuration:\n!\n"
        "frr version 10.7.1\nfrr defaults traditional\nhostname box\n"
        "router bgp 65000\n neighbor 10.0.0.2 remote-as 65001   \n!\nend\n")
got = hexash.Frr.running(RecFrr({"show running-config": (0, text)}))
check(got == ["!", "hostname box", "router bgp 65000",
              " neighbor 10.0.0.2 remote-as 65001", "!"],
      f"framing out, blank rows out, right side stripped: {got}")
check(hexash.Frr.running(RecFrr()) == [],
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
got = hexash.Frr.diagnostics(out)
check(got == ["2026-09-17 10:00:00,002 WARNING: something about your config",
              "The route-map 'PL-X' does not exist."],
      f"INFO, progress, echoed lists and rules are dropped; the daemon's "
      f"two rows stay: {got}")
check(hexash.Frr.diagnostics(None) == [] and hexash.Frr.diagnostics("") == [],
      "nothing said is nothing kept")
ok("the noise it already knows is dropped, and only that")

print("\n=== Frr._summarise: the reason, not the footer twice ===")
# Mutation shown red: drop the `useful` filter, so the footer alone is the
# summary.
out = ("Loading Config\n"
       "The route-map 'PL-X' does not exist.\n"
       "% Configuration failed.\n")
check(hexash.Frr._summarise(out) == "The route-map 'PL-X' does not exist.\n"
      "% Configuration failed.",
      f"a footer with no reason falls back to what the daemon said, the "
      f"footer under it: {hexash.Frr._summarise(out)!r}")
out = "Exiting: rejected\n% Configuration failed.\nnoise\n"
got = hexash.Frr._summarise(out)
check(got == "Exiting: rejected\n% Configuration failed.",
      f"a row with the reason keeps the caught rows, last four: {got!r}")
check(hexash.Frr._summarise("- a\n- b\n") == "- b",
      "nothing matched and nothing diagnosed is the last row")
check(hexash.Frr._summarise("") == "", "no output is no summary")
ok("prefixed rows, then the daemon's own rows, then the last row")

# ------------------------------------------------------ the stores on disk

print("\n=== Rollback._file: revision n is <path>/n.conf ===")
# Mutation shown red: f"{n}.cfg".
rb = hexash.Rollback(path=os.path.join(_root, "rb"))
check(rb._file(3) == os.path.join(_root, "rb", "3.conf"),
      f"the file of revision 3: {rb._file(3)}")
check(rb._file(0) == os.path.join(_root, "rb", "0.conf"),
      "revision 0 is a file too, the configuration now in effect")
ok("one naming rule for the store")

print("\n=== ConfirmPending.count_attempt: one more, written back ===")
# Mutation shown red: do not increment before arming.
cp = hexash.ConfirmPending(os.path.join(_root, "confirm-count"))
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
cp = hexash.ConfirmPending(os.path.join(_root, "confirm-clear"))
cp.arm(1700000000, "user alice\n", 5)
cp.clear()
check(not os.path.exists(cp.path), "confirmed: nothing left to do")
cp.clear()
check(cp.read() is None, "clearing what is already clear raises nothing")
ok("the record of the window is its file, and clear removes it")

print("\n=== ConfirmPending.give_up: moved aside, kept, and named ===")
# Mutation shown red: unlink instead of rename.
cp = hexash.ConfirmPending(os.path.join(_root, "confirm-giveup"))
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
check(api.calls[-1][1]["path_mtu"] == hexash.IPFIX_PATH_MTU
      and api.calls[-1][1]["template_interval"]
      == hexash.IPFIX_TEMPLATE_INTERVAL,
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
os.environ["HEXA_SHELL"] = login
try:
    sh = shell(caps={hexash.CAP_CGNAT})
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
                         "back in the HexaRouting VTY"],
          f"said on the way out and on the way back: {text!r}")
finally:
    del os.environ["HEXA_SHELL"]
ok("the capability is the boundary; leaving the shell returns to the VTY")

print("\n=== Shell.do_install: only from the live medium, and only an "
      "administrator ===")
# Mutation shown red: drop the running_from_live_medium() gate.
installer, log = argv_script("hexarouting-install")
saved = hexash.INSTALLER, hexash.LIVE_SQUASHFS
try:
    hexash.INSTALLER = installer
    hexash.LIVE_SQUASHFS = os.path.join(_root, "filesystem.squashfs")
    sh = shell()
    rv, text = driven(lambda: sh.do_install())
    check(argv_of(log) is None and rows(text)[0]
          == "error: this box is not running from the installation medium",
          f"an installed box refuses and starts nothing: {text!r}")
    open(hexash.LIVE_SQUASHFS, "w").close()
    sh = shell(caps={hexash.CAP_CGNAT})
    rv, text = driven(lambda: sh.do_install())
    check(argv_of(log) is None and "permission denied" in text,
          f"on the medium, a cgnat account is still refused: {text!r}")
    sh = shell()
    rv, text = driven(lambda: sh.do_install())
    check(argv_of(log) == [],
          f"an administrator on the medium hands the console to the "
          f"installer, with no argument: {argv_of(log)}")
finally:
    hexash.INSTALLER, hexash.LIVE_SQUASHFS = saved
    if os.path.exists(hexash.LIVE_SQUASHFS):
        os.remove(hexash.LIVE_SQUASHFS)
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
sh = shell(runner=run, caps={hexash.CAP_CGNAT})
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
empty = Config.parse("!\n! HexaRouting\n!\n")
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
saved = hexash.Shell.TUNE_ENV
try:
    hexash.Shell.TUNE_ENV = tune
    sh = shell()
    check(sh.write_tune_key("HEXA_WORKERS", "4") == (True, ""),
          "no file at all: one is written rather than refusing")
    with open(tune) as f:
        first = f.read()
    check(first.splitlines()[0].startswith("# Written by hexash")
          and first.splitlines()[-1] == "HEXA_WORKERS=4",
          f"the new file says who wrote it and holds the key: {first!r}")
    with open(tune, "w") as f:
        f.write("# why the field ships empty\nHEXA_DATAPLANE_IF=\n"
                "  HEXA_WORKERS=2\nHEXA_SUBSCRIBERS=1000\n")
    check(sh.write_tune_key("HEXA_WORKERS", "8") == (True, ""), "written")
    with open(tune) as f:
        got = f.read()
    check(got == "# why the field ships empty\nHEXA_DATAPLANE_IF=\n"
                 "HEXA_WORKERS=8\nHEXA_SUBSCRIBERS=1000\n",
          f"the one key replaced in place, the prose and the others "
          f"untouched: {got!r}")
    check(not os.path.exists(tune + ".new"),
          "the temporary file was renamed into place, not left")
    hexash.Shell.TUNE_ENV = os.path.join(_root, "no-such-dir-file", "x",
                                         "tune.env")
    os.makedirs(os.path.dirname(os.path.dirname(hexash.Shell.TUNE_ENV)),
                exist_ok=True)
    blocked = os.path.dirname(hexash.Shell.TUNE_ENV)
    open(blocked, "w").close()       # a FILE where the directory should be
    okk, why = sh.write_tune_key("HEXA_WORKERS", "8")
    check(okk is False and why.startswith(hexash.Shell.TUNE_ENV),
          f"a file that cannot be written is (False, path: reason): "
          f"{(okk, why)!r}")
finally:
    hexash.Shell.TUNE_ENV = saved
ok("hand-editable file: rewritten line for line, through a rename")

print("\n=== Shell.write_dataplane_if: the tuner's input key, and only "
      "it ===")
# Mutation shown red: write the name under another key.
saved = hexash.Shell.TUNE_ENV
try:
    hexash.Shell.TUNE_ENV = tune
    with open(tune, "w") as f:
        f.write("HEXA_DATAPLANE_IF=\nHEXA_WORKERS=8\n")
    check(shell().write_dataplane_if("ens19") == (True, ""), "written")
    with open(tune) as f:
        check(f.read() == "HEXA_DATAPLANE_IF=ens19\nHEXA_WORKERS=8\n",
              "HEXA_DATAPLANE_IF carries the card, the worker cap stays")
finally:
    hexash.Shell.TUNE_ENV = saved
ok("write_tune_key(TUNE_KEY, name)")

print("\n=== Shell.apply_dataplane_if: store, restart the tuner, say what "
      "is outstanding ===")
# Mutation shown red: rc 127 answers "" instead of "stored; the tuner is not
# on this machine".
saved = hexash.Shell.TUNE_ENV
try:
    hexash.Shell.TUNE_ENV = tune
    with open(tune, "w") as f:
        f.write("HEXA_DATAPLANE_IF=\n")
    run = RecRunner()
    check(shell(runner=run).apply_dataplane_if("ens19") == (True, ""),
          "stored and recomputed: nothing outstanding")
    check(run.asked == [["systemctl", "restart", hexash.Shell.TUNE_UNIT]],
          f"the unit is restarted, because it carries the EnvironmentFile: "
          f"{run.asked}")
    with open(tune) as f:
        check("HEXA_DATAPLANE_IF=ens19" in f.read().splitlines(),
              "and the card was stored first")
    run = RecRunner({("systemctl",): (127, "systemctl: not found")})
    check(shell(runner=run).apply_dataplane_if("ens19")
          == (True, "stored; the tuner is not on this machine, so nothing "
                    "was recomputed"),
          "rc 127 is a developer box: stored, and said so")
    run = RecRunner({("systemctl",): (1, "Job for hexarouting-tune.service "
                                         "failed.\n")})
    okk, why = shell(runner=run).apply_dataplane_if("ens19")
    check(okk is True and why == "stored, but the tuner did not run: Job for "
          "hexarouting-tune.service failed.",
          f"any other failure is stored and reported with systemd's row: "
          f"{(okk, why)!r}")
    hexash.Shell.TUNE_ENV = blocked + "/tune.env"
    run = RecRunner()
    okk, why = shell(runner=run).apply_dataplane_if("ens19")
    check(okk is False and run.asked == [],
          f"a file that cannot be written runs no tuner: {(okk, why)!r} "
          f"{run.asked}")
finally:
    hexash.Shell.TUNE_ENV = saved
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
sh = shell(frr=frr, caps={hexash.CAP_CGNAT})
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
sh = shell(frr=frr, running=cfg, caps={hexash.CAP_CGNAT})
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

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
