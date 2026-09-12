#!/usr/bin/env python3
"""
Tests for the VTY's completion and "?" help.

They exercise the command tree with no terminal: what readline would do when
completing or asking for help comes from _options(), and that is what is
checked here.
"""
import contextlib
import io
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "hexash"))
import hexash

fail = 0

def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")

def opts(mode, line, ending=None):
    sh = hexash.Shell()
    sh.mode = mode
    if ending is None:
        ending = bool(line) and not line.endswith(" ")
    o, hint, prefix = sh._options(line, ending)
    return set(o), hint, prefix

print("=== operational mode: '?' on an empty line ===")
o, _, _ = opts("operational", "")
check({"configure", "show", "rollback", "save", "exit"} <= o, f"missing: {o}")
print(f"  {sorted(o)}")

print("\n=== 'show ' lists the sub-commands ===")
o, _, _ = opts("operational", "show ")
check("cgnat" in o and "running-config" in o, f"expected cgnat and running-config: {o}")
print(f"  {sorted(o)}")

print("\n=== 'show cgnat ' goes one level deeper ===")
o, _, _ = opts("operational", "show cgnat ")
# Written out rather than derived from the tree: a test that reads the same
# structure the code reads would agree with any mistake in it.
check(o == {"pool", "session", "blocks", "logging", "history",
            "subscriber", "translation", "top", "drops"}, f"got {o}")
print(f"  {sorted(o)}")

print("\n=== a partial prefix completes ===")
o, _, prefix = opts("operational", "conf")
check(o == {"configure"}, f"'conf' should yield only 'configure': {o}")
check(prefix == "conf", f"prefix {prefix}")
o, _, _ = opts("operational", "show cgnat po")
check(o == {"pool"}, f"'po' should yield only 'pool': {o}")
print("  'conf' -> configure ; 'show cgnat po' -> pool")

print("\n=== modo pool: opcoes proprias ===")
o, _, _ = opts("pool", "")
check({"inside", "outside", "block-size", "policy", "mode", "exit"} <= o,
      f"faltam opcoes de pool: {o}")
o, _, _ = opts("pool", "policy ")
check(o == {"interleaved", "blocked"}, f"policy should have two options: {o}")
o, _, _ = opts("pool", "mode ")
check(o == {"deterministic", "dynamic"}, f"mode should have two options: {o}")
print(f"  policy -> {sorted(o)} ; mode has two options")

print("\n=== a free argument becomes a hint, not an empty list ===")
o, hint, _ = opts("pool", "inside ")
check(hint == hexash.ARG_PREFIX, f"expected a prefix hint, got {hint!r}")
check(not o, f"should not list options after 'inside': {o}")
o, hint, _ = opts("config", "nat cgnat block-holddown ")
check(hint == hexash.ARG_NUM, f"expected a numeric hint, got {hint!r}")
print(f"  'inside ' -> {hexash.ARG_PREFIX} ; 'block-holddown ' -> {hexash.ARG_NUM}")

print("\n=== config: CGNAT and routing at the same level ===")
o, _, _ = opts("config", "")
check({"nat", "router", "interface", "no", "commit", "rollback", "exit"} <= o,
      f"got {o}")
# `cgnat` is NOT offered at the top level any more - it moved under `nat`
# (#193) - and offering both spellings would teach the one being left behind.
check("cgnat" not in o, f"the old top-level spelling is not offered: {o}")
o, _, _ = opts("config", "nat ")
check(o == {"cgnat"}, f"nat holds the CGNAT hierarchy: {o}")
o, _, _ = opts("config", "nat cgnat ")
check({"pool", "interface", "timeout", "logging", "block-holddown"} <= o, f"got {o}")
# Static routes moved under `router`, beside the protocols.
o, _, _ = opts("config", "router ")
check("static" in o, f"router offers static: {o}")
o, _, _ = opts("config", "ip ")
check("route" not in o and "prefix-list" in o,
      f"and `ip route` is no longer the offered spelling: {o}")
o, _, _ = opts("config", "router ")
check({"bgp", "ospf", "isis"} <= o, f"got {o}")
print("  cgnat, router, interface, no, exit")

print("\n=== mtu is discoverable inside an interface block ===")
# An operator who cannot find `mtu` with `?` inherits the TAP's 9000 and spends
# an afternoon on an OSPF adjacency stuck in Exchange, which is the failure
# docs/routing.md records and this parameter exists to prevent.
import config as _config
sh = hexash.Shell()
sh.mode = "config"
sh.candidate = _config.Config()
sh.feed("interface ens19")
o, _, _ = sh._options("", False)
check("mtu" in o, f"'mtu' must be offered inside an interface block: {sorted(o)}")
print(f"  interface block offers: {sorted(k for k in o if k in ('mtu', 'exit'))}")

print("\n=== the parser rejects an out-of-range mtu, not the device ===")
# Refusing it here means the operator is told at the point of typing, instead
# of at commit time after the candidate is already built.
before = sh.failed
sh.feed("mtu 1500")
check(sh.candidate.mtus.get("ens19") == 1500,
      f"a valid mtu must be stored: {sh.candidate.mtus}")
sh.feed("mtu 99")
sh.feed("mtu 20000")
sh.feed("mtu abc")
check(sh.candidate.mtus.get("ens19") == 1500,
      f"invalid values must not overwrite a good one: {sh.candidate.mtus}")
check(sh.failed - before == 3, f"each invalid value must be reported: {sh.failed - before}")
sh.feed("no mtu")
check("ens19" not in sh.candidate.mtus,
      f"'no mtu' must remove the statement: {sh.candidate.mtus}")
print("  1500 accepted; 99, 20000 and 'abc' refused; 'no mtu' removes it")

print("\n=== mtu survives the configuration round trip ===")
# It has to appear in show configuration, be saved, and be read back - or a
# rollback would silently drop it.
cfg = _config.Config()
cfg.mtus["ens19"] = 1500
cfg.interfaces["ens19"] = "inside"
text = cfg.text()
check("mtu 1500" in text, f"mtu must appear in the configuration:\n{text}")
back = _config.Config.parse(text)
check(back.mtus == {"ens19": 1500}, f"and be read back: {back.mtus}")
check(back.text() == text, "the round trip must be stable")
d = _config.Config().delta_to(cfg)
check(d["mtu_set"] == {"ens19": 1500}, f"the delta must carry it: {d['mtu_set']}")
d = cfg.delta_to(_config.Config())
check(d["mtu_del"] == ["ens19"], f"and carry its removal: {d['mtu_del']}")
print("  written, parsed, and both directions of the delta")

print("\n=== commit sub-commands, Juniper style ===")
o, _, _ = opts("config", "commit ")
check({"check", "confirmed", "comment"} <= o, f"got {o}")
print(f"  commit -> {sorted(o)}")

o, hint, _ = opts("config", "rollback ")
check(hint == hexash.ARG_REV, f"rollback should hint a revision, got {hint!r}")
print(f"  rollback -> {hexash.ARG_REV}")

print("\n=== an unknown command must not invent options ===")
o, hint, _ = opts("config", "something-that-does-not-exist ")
check(not o and hint is None,
      f"should admit it does not know, got options={o} hint={hint!r}")
print("  admits that from there on FRR is the one that knows")

print("\n=== 'start shell' is offered, and restricted ===")
# It used to be HIDDEN from the tree, and this assertion said so. The
# maintainer then asked for a command that already existed, which is what a
# command nobody can find looks like (#194). start_shell()'s own comment had
# already made the argument: hiding was never the boundary, the capability
# check is - so the offer changed and the restriction did not.
o, _, _ = opts("operational", "")
check("start" in o, f"'start' is offered at the top level: {sorted(o)}")
o, _, _ = opts("operational", "start ")
check("shell" in o, f"and 'shell' under it: {sorted(o)}")
check(hasattr(hexash.Shell, "start_shell"), "the escape must still exist")
src = open(hexash.__file__).read()
check('["start", "shell"]' in src,
      "the escape must be dispatched before the command tree")

# THE half that matters, and the one the old assertion never made: being
# offered must not mean being granted. A shell on this box is every
# capability at once.
for caps, who, want in ((set(), "a read-only account", False),
                        ({hexash.CAP_CGNAT}, "a cgnat operator", False),
                        ({hexash.CAP_ALL}, "an administrator", True)):
    probe = hexash.Shell.__new__(hexash.Shell)
    probe.caps = caps
    probe.failed = 0
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        allowed = probe.require(hexash.CAP_ALL, "start shell")
    check(allowed is want,
          f"{who} is {'allowed' if want else 'refused'} the shell")
    if not want:
        check("permission denied" in out.getvalue(),
              f"and {who} is told why: {out.getvalue().strip()[:60]}")
print("  offered at every level, granted only to an administrator")

print("\n=== show version ===")
o, _, _ = opts("operational", "show ")
check("version" in o, f"'version' should be under show: {sorted(o)}")

import tempfile as _tf, os as _os
with _tf.TemporaryDirectory() as d:
    vf = _os.path.join(d, "VERSION")
    with open(vf, "w") as f:
        f.write("0.9.1 (abc1234-dirty)\n")
    _os.environ["HEXA_VERSION_FILE"] = vf
    import importlib
    importlib.reload(hexash)
    check(hexash.hexarouting_version() == "0.9.1 (abc1234-dirty)",
          f"should read the stamp from the file, read {hexash.hexarouting_version()!r}")
    # A dirty tree has to keep saying it is dirty: that stamp is what stops an
    # ISO from declaring itself a release when it is not one.
    check("dirty" in hexash.hexarouting_version(),
          "the dirty-tree stamp must not be swallowed")

# With no installed file it has to fall back to the repository VERSION - that
# is what makes hexash run from a development tree tell the truth.
_os.environ["HEXA_VERSION_FILE"] = "/does/not/exist/VERSION"
importlib.reload(hexash)
repo_version = open(_os.path.join(_os.path.dirname(_os.path.abspath(hexash.__file__)),
                                  "..", "..", "VERSION")).read().strip()
check(hexash.hexarouting_version() == repo_version,
      f"should fall back to the repository VERSION ({repo_version!r}), "
      f"read {hexash.hexarouting_version()!r}")
# A deliberate tripwire: 1.0.0 is a decision by the maintainer, not a number
# somebody raises by accident. When they declare it ready, this check goes out
# together with the rule in CONTRIBUTING.md.
check(repo_version.startswith("0."),
      f"the version is {repo_version!r}: 1.0.0 only once the maintainer "
      "declares it production-ready (CONTRIBUTING.md, section 5)")
print(f"  version read: {hexash.hexarouting_version()}")


print("\n=== the banner: on a terminal, and nowhere else ===")
# The absence half matters more than the presence half. "hexash -c" is how
# every lab and every script drives this VTY, and a banner prepended to their
# output breaks the parsing - which is the reason the version line was already
# guarded before the banner existed.
import subprocess as _sp

_HX = _os.path.join(_os.path.dirname(_os.path.abspath(hexash.__file__)),
                    "hexash.py")
_env = dict(_os.environ, HEXA_VERSION_FILE="/does/not/exist/VERSION")

# Commands PIPED IN, not "-c". This is the path that can actually leak: "-c"
# never enters repl() at all, so a test built on it passes whatever the guard
# does - which is what the first version of this check did, and it went on
# passing with the banner printed unconditionally.
_p = _sp.run([sys.executable, _HX], input="show version\nexit\n",
             capture_output=True, text=True, timeout=30, env=_env)
_piped = _p.stdout + _p.stderr
check("\\___/" not in _piped,
      f"banner art leaked into piped output: {_piped[:120]!r}")
check("HexaRouting VTY" not in _piped,
      "the version line must not reach a pipe either")
print("  piped commands carry no banner")

_p2 = _sp.run([sys.executable, _HX, "-c", "show version"],
              capture_output=True, text=True, timeout=30, env=_env)
check("\\___/" not in (_p2.stdout + _p2.stderr),
      "banner art leaked into 'hexash -c' output")
print("  and neither does 'hexash -c'")


def _on_a_pty():
    """Run hexash on a real terminal and return what it printed."""
    import pty as _pty, time as _time
    m, sl = _pty.openpty()
    pid = _os.fork()
    if pid == 0:
        _os.close(m)
        for fd in (0, 1, 2):
            _os.dup2(sl, fd)
        _os.execve(sys.executable, [sys.executable, _HX], _env)
    _os.close(sl)
    _time.sleep(1.5)
    _os.write(m, b"exit\n")
    _time.sleep(0.8)
    _os.set_blocking(m, False)
    try:
        data = _os.read(m, 1 << 16)
    except BlockingIOError:
        data = b""
    _os.waitpid(pid, 0)
    _os.close(m)
    return data.decode(errors="replace")


_tty = _on_a_pty()
check("\\___/" in _tty, "the banner must appear on a terminal")
check("HexaRouting VTY" in _tty,
      "the version line must survive the banner: the banner identifies the "
      "product, the version identifies the build, and an operator on the "
      "phone needs the second")
_widest = max((len(l) for l in _tty.split("\n")), default=0)
check(_widest <= 80,
      f"a line is {_widest} columns; a racked box has an 80-column serial "
      "console and a banner that wraps is worse than none")
print(f"  banner shown on a terminal, widest line {_widest} columns")

print("\n=== the tree knows a name goes here; the box knows which names ===")
#
# "<word>" is true and useless. It tells the operator what they already knew
# and sends them to vppctl or "ip link" in another window - the two tools this
# VTY exists to keep them out of - at the first argument (#98).
#
IFACES = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)
host-veth-vpp                     1      up          9000/0/0/0
local0                            0     down          0/0/0/0
TenGigabitEthernet0/8/0           3      up          9000/0/0/0
"""


def shell_with_box(iface_text=IFACES, dp_rc=0):
    sh = hexash.Shell()
    sh.caps = {hexash.CAP_ALL, hexash.CAP_CGNAT}
    def run(_self, cmd):
        if cmd.startswith("show interface"):
            return dp_rc, iface_text
        return 0, ""          # anything else: a box with nothing configured

    sh.dp = type("D", (), {"TRACE": False, "run": run})()
    sh.frr = type("F", (), {"available": lambda s: False,
                            "running": lambda s: [],
                            "show": lambda s, c: (1, "")})()
    return sh


sh = shell_with_box()
sh.feed("configure")
opts, hint, _ = sh._options("interface ", False)
check("host-veth-vpp" in opts and "TenGigabitEthernet0/8/0" in opts,
      f"interfaces must come from the dataplane: {sorted(opts)}")
check("local0" not in opts,
      "VPP's own loopback is not something to configure")
check("up" in opts.get("host-veth-vpp", ""),
      f"and the state is worth showing: {opts.get('host-veth-vpp')}")
print(f"  interface -> {sorted(opts)}")

opts, _, _ = sh._options("interface Ten", True)
check(list(opts) == ["TenGigabitEthernet0/8/0"],
      f"a prefix narrows to one: {sorted(opts)}")
# A single match must COMPLETE. Offering one option and making the operator
# type it is completion that did not complete.
check(len(opts) == 1, "and a single match is a single match")
print(f"  interface Ten -> {sorted(opts)}")

print("\n=== names the CANDIDATE knows ===")
# One pool made in the NEW hierarchy and one in the old, because both have to
# work: a box in service has a saved configuration and twenty rollback
# revisions written in the old spelling, and hexarouting.service replays that
# file at every boot (#193).
sh.feed("nat cgnat pool residential")
sh.feed("inside 100.64.0.0/22")
sh.feed("exit")
sh.feed("cgnat pool business")
sh.feed("exit")
check(set(sh.candidate.pools) == {"residential", "business"},
      f"both spellings make a pool: {sorted(sh.candidate.pools)}")
opts, _, _ = sh._options("nat cgnat pool ", False)
check(set(opts) == {"residential", "business"},
      f"pools come from the candidate being edited: {sorted(opts)}")
opts, _, _ = sh._options("no nat cgnat pool ", False)
check(set(opts) == {"residential", "business"},
      f"and from the removal branch too: {sorted(opts)}")
print(f"  nat cgnat pool -> {sorted(opts)}")

print("\n=== a source that cannot answer offers nothing, and does not raise ===")
# A VTY that becomes unusable when the dataplane is down is a VTY that cannot
# be used to fix it.
sh = shell_with_box(dp_rc=1)
sh.feed("configure")
opts, hint, _ = sh._options("interface ", False)
check(opts == {}, f"nothing is offered: {opts}")
check(hint == "<interface>",
      f"and the hint still says what goes here: {hint}")
print(f"  dataplane down -> options {opts}, hint {hint}")

# Every marker in the tree must have a source. One added without it silently
# goes back to being a bare "<word>", which is the state this issue is about.
# rc=1 is a dataplane that ANSWERED with an error. The other case is a source
# that RAISES - and the guard around it is tested directly rather than by
# pretending the whole VTY survives a dataplane that throws, because it does
# not: running_config() calls the same interface listing, so "configure" would
# go down before completion was ever reached.
#
# What is being asserted is narrow and true: completion swallows a source that
# raises, so a broken source cannot take the prompt down with it.
sh = hexash.Shell()
sh.caps = {hexash.CAP_ALL, hexash.CAP_CGNAT}
sh.dyn_interfaces = lambda: (_ for _ in ()).throw(
    OSError("no such file or directory: /run/vpp/cli.sock"))
try:
    opts = sh.dynamic_options(hexash.ARG_INTERFACE, "")
    check(opts == {}, f"a source that raises offers nothing: {opts}")
    print("  a source that raises -> offered nothing, no traceback")
except Exception as e:
    check(False, f"completion must not propagate a broken source: {e!r}")

print("\n=== every dynamic marker has a source ===")
# The PROPERTY: every marker that promises values must have a method that can
# produce them, and that method must exist. Written as set equality against a
# hardcoded list, this failed when a marker was ADDED - reporting "markers
# without a source: []", an empty difference, because the inequality was the
# other way round. That is the third hardcoded list in this suite to break on
# the code being extended correctly.
#
# The known markers are asserted as a SUBSET, so adding one is not a failure,
# and every source is resolved so that a typo in the table is.
markers = {hexash.ARG_INTERFACE, hexash.ARG_POOL, hexash.ARG_USER,
           hexash.ARG_SNMP_USER}
check(markers <= set(hexash.Shell.DYNAMIC),
      f"markers without a source: {sorted(markers - set(hexash.Shell.DYNAMIC))}")
missing = [m for m, name in hexash.Shell.DYNAMIC.items()
           if not callable(getattr(hexash.Shell, name, None))]
check(not missing,
      f"markers whose source method does not exist: {missing}")
# And a marker is only useful if something in the command tree offers it. One
# that nothing references is a promise nobody can reach.
#
# Looked up by the CONSTANT's name, because that is how the tree refers to
# them - counting the literal value found only the definition and reported
# every marker as unused, which is this check being wrong rather than the code.
tree_text = open(hexash.__file__).read()
const_of = {v: k for k, v in vars(hexash).items()
            if isinstance(v, str) and k.startswith("ARG_")}
unused = [m for m in hexash.Shell.DYNAMIC
          if tree_text.count(const_of.get(m, "\x00")) < 2]
check(not unused, f"markers nothing in the tree offers: {unused}")
for marker, method in hexash.Shell.DYNAMIC.items():
    check(hasattr(hexash.Shell, method),
          f"{marker} names {method}, which does not exist")
# len(DYNAMIC), not len(markers): the second is the hardcoded subset this test
# asserts is present, and reporting it said "4 markers" on a box with five.
print(f"  {len(hexash.Shell.DYNAMIC)} markers, all sourced: "
      + ", ".join(sorted(hexash.Shell.DYNAMIC)))

print("\n=== show this ===")
sh = shell_with_box()
sh.feed("configure")
sh.feed("cgnat pool residential")
sh.feed("inside 100.64.0.0/22")
sh.feed("tcp-mss 1400")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.feed("show this")
text = out.getvalue()
check("cgnat pool residential" in text and "tcp-mss 1400" in text,
      f"the pool being edited is shown: {text!r}")
check("business" not in text,
      "and nothing above it: 'this' is the hierarchy, not the box")
print("  inside a pool: " + text.strip().replace("\n", " / "))

# It shows the CANDIDATE. Somebody mid-edit asking "what is here" is asking
# about what they are building; answering with what is committed answers a
# question they did not ask.
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.feed("exit")
    sh.feed("show this")
check("cgnat pool residential" in out.getvalue(),
      "at the top of config mode it is the whole candidate")

sh2 = shell_with_box()
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh2.feed("show this")
check("not in configuration mode" in out.getvalue(),
      f"outside configuration mode it says so: {out.getvalue()!r}")
print("  operational mode: refused with the reason")

print("\n=== inside a block, only what is valid there ===")
#
# A block entered by an ARGUMENT is still mode "config", so the tree walk
# returned the whole configuration tree: fifteen options inside an interface,
# of which three are valid. Offering the level above invites somebody to type
# "cgnat pool" inside an interface and find out afterwards what that meant
# (#112).
sh = shell_with_box()
sh.feed("configure")
sh.feed("interface TenGigabitEthernet0/8/0")
opts, _, _ = sh._options("", False)
# Asserted as a PROPERTY, not as a list. A hardcoded set here had to be edited
# every time the interface block legitimately grew, which is a test that
# reports "you added a feature" and not "you broke something" - it failed three
# times in a row for exactly that reason while nothing was wrong.
#
# What matters is the boundary: everything offered belongs to the block, and
# nothing from the level above leaks in. Two statements are named explicitly so
# the assertion cannot be satisfied by an empty offer.
from hexash import INTERFACE_BLOCK                              # noqa: E402
check(set(opts) <= set(INTERFACE_BLOCK) | {"show"},
      f"an interface offers only its own statements: "
      f"{sorted(set(opts) - set(INTERFACE_BLOCK) - {'show'})}")
check({"mtu", "urpf"} <= set(opts),
      f"and it does offer them: {sorted(opts)}")
check(not ({"cgnat", "user", "commit", "rollback"} & set(opts)),
      f"and nothing from the level above leaks in: {sorted(opts)}")
print(f"  interface -> {sorted(opts)}")
sh.feed("exit")

from hexash import BGP_BLOCK, BGP_NEIGHBOUR, BGP_AF_BLOCK   # noqa: E402
sh.feed("router bgp 65001")
opts, _, _ = sh._options("", False)
check(set(opts) <= set(BGP_BLOCK) | {"show"},
      f"router bgp offers only its own statements: "
      f"{sorted(set(opts) - set(BGP_BLOCK) - {'show'})}")
check({"neighbor", "address-family"} <= set(opts),
      f"and it does offer them: {sorted(opts)}")
check("cgnat" not in opts and "interface" not in opts,
      "and never the level above")
print(f"  router bgp -> {sorted(opts)}")
# The words after a neighbour's ADDRESS are the neighbour's, not the block's.
# The tree walk stops at an argument, so before this everything past the
# address fell through to the configuration tree.
sh.feed("neighbor 10.0.0.2 remote-as 65001")
opts, _, _ = sh._options("neighbor 10.0.0.2 ", False)
check(set(opts) == set(BGP_NEIGHBOUR),
      f"after an address, the neighbour's statements: {sorted(opts)}")
check("address-family" not in opts,
      "and not the block's own, which do not apply to a neighbour")
# An address family is a different vocabulary: `activate` belongs there and
# `remote-as` does not.
sh.feed("address-family ipv4 unicast")
opts, _, _ = sh._options("", False)
check(set(opts) <= set(BGP_AF_BLOCK) | {"show"},
      f"an address family offers only its own: {sorted(opts)}")
opts, _, _ = sh._options("neighbor 10.0.0.2 ", False)
check("activate" in opts and "remote-as" not in opts,
      f"activate belongs to the family, remote-as does not: {sorted(opts)}")
sh.feed("exit-address-family")
sh.feed("exit")

# A block this VTY does NOT model still offers nothing but the way out.
# Offering statements for it would be a guess presented as a grammar, and that
# is what this check has always been for.
#
# `router rip` rather than `router ospf`: OSPF was the example until #173
# modelled it, and this assertion failing is how that was noticed. RIP is not
# a protocol this product claims, so it is the honest stand-in - and the day
# it is claimed, this line fails again and asks for a new one.
sh.feed("router rip")
opts, _, _ = sh._options("", False)
check(set(opts) == {"exit", "show"},
      f"an unmodelled FRR block offers only the way out: {sorted(opts)}")
print(f"  router rip -> {sorted(opts)}")
sh.feed("exit")

# The blocks the tree knows about were always right; this is the check that
# they stayed right.
sh.feed("cgnat pool p")
opts, _, _ = sh._options("", False)
check("inside" in opts and "cgnat" not in opts,
      f"a pool still offers its own tree: {sorted(opts)[:6]}")
sh.feed("exit")

print("\n=== completion ignores case, and inserts the box's spelling ===")
# Interface names are GigabitEthernet0/12/0. Requiring the capital G before
# completion helps is completion that only works for somebody who already
# knows the answer.
for typed in ("ten", "TEN", "TenGig", "tengigabitethernet0/8"):
    opts, _, _ = sh._options("interface " + typed, True)
    check(list(opts) == ["TenGigabitEthernet0/8/0"],
          f"'{typed}' must complete to the real name, got {sorted(opts)}")
print("  ten / TEN / TenGig / tengigabitethernet0/8 -> TenGigabitEthernet0/8/0")

print("\n=== leaving a block says nothing and lands in config mode ===")
#
# This is the assertion whose absence let a regression ship in v0.29.0. Adding
# "show this" to the sub-modes put the guard INSIDE the exit branch of
# do_user, leaving "self.user = None; return True" as dead code after a
# return - so leaving a user changed the mode AND printed "unknown statement
# in user mode: exit". do_snmp_user was split the same way and fell through to
# "a v3 user takes ...".
#
# The whole suite passed. Nothing left a block and checked that NOTHING was
# printed, which is the shape of the bug: an operation that works and
# complains while working.
sh = shell_with_box()
sh.feed("configure")
for block in ("user noc", "snmp-user alice", "cgnat pool p",
              "interface TenGigabitEthernet0/8/0"):
    sh.feed(block)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sh.feed("exit")
    said = out.getvalue().strip()
    check(said == "", f"leaving '{block}' printed: {said!r}")
    check(sh.mode == "config",
          f"leaving '{block}' left mode {sh.mode!r}, not config")
print("  four blocks entered and left, in silence")

print("\n=== an interface this box does not have is refused ===")
#
# "interface Ten0" - a typo for TenGigabitEthernet0/8/0 - created an FRR stanza
# for something that is not there: it committed cleanly, appeared in the
# running configuration, and did nothing (#105).
#
# #112 made the wrong name much harder to type. It did not make it impossible:
# a runbook, a copy-paste or a habit still types it.
def commit_check(sh):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sh.feed("commit check")
    return out.getvalue()


def shell_with_frr(dp_rc=0):
    sh = shell_with_box(dp_rc=dp_rc)
    sh.frr = type("F", (), {"available": lambda s: False,
                            "running": lambda s: [],
                            "show": lambda s, c: (1, ""),
                            "reload": lambda s, l, check_only=False: (0, "")})()
    return sh


sh = shell_with_frr()
sh.feed("configure")
sh.feed("interface Ten0")
sh.feed("mtu 1500")
sh.feed("exit")
text = commit_check(sh)
check("no such interface" in text, f"a name the box does not have is refused: {text!r}")
# The refusal lists what the box DOES have. It already knows - the same source
# the completion uses - and an operator told only "no" has to go and look.
check("TenGigabitEthernet0/8/0" in text,
      f"and the refusal says what it does have: {text!r}")
check("would commit and do nothing" in text,
      "and what would have happened without the refusal")
print("  " + text.strip().splitlines()[0])

sh = shell_with_frr()
sh.feed("configure")
sh.feed("interface TenGigabitEthernet0/8/0")
sh.feed("mtu 1500")
sh.feed("exit")
check("no such interface" not in commit_check(sh),
      "an interface the box has is not refused")

# The CGNAT form names an interface too, and was equally able to enable CGNAT
# on a name the dataplane never heard of.
sh = shell_with_frr()
sh.feed("configure")
sh.feed("cgnat interface Gig9 inside")
check("no such interface" in commit_check(sh),
      "'cgnat interface' is checked the same way")

# Skipped, not failed. A VTY that cannot commit while the dataplane is down
# cannot be used to fix it.
sh = shell_with_frr(dp_rc=1)
sh.feed("configure")
sh.feed("interface Ten0")
sh.feed("mtu 1500")
sh.feed("exit")
check("no such interface" not in commit_check(sh),
      "with the dataplane unreachable the check is skipped, not failed")
print("  dataplane down -> skipped, and the commit is not blocked")

# --load runs at boot, unattended, which is when a silently inert stanza is
# least likely to be noticed. It is refused THE SAME WAY, and refused WHOLE:
# applying a file statement by statement until one fails leaves the box holding
# half of it, on a machine nobody is watching.
import tempfile  # noqa: E402

def load_text(sh, body):
    f = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
    f.write(body)
    f.close()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sh.load(f.name)
    os.unlink(f.name)
    return out.getvalue()


text = load_text(shell_with_frr(),
                 "!\ninterface Ten0\n mtu 1500\n exit\n")
check("no such interface" in text,
      f"--load refuses an interface the box does not have: {text!r}")
check("refusing to load" in text,
      "and refuses the WHOLE file rather than applying part of it")

# The gap this found: an interface name in a config FILE lives in cfg.mtus, not
# in cfg.routing or cfg.interfaces. A check that looked at only those two
# passed every file that sets an MTU - which is most of them.
text = load_text(shell_with_frr(),
                 "!\ninterface TenGigabitEthernet0/8/0\n mtu 1500\n exit\n")
check("no such interface" not in text,
      f"and does not refuse one the box has: {text!r}")
print("  --load: refused whole, and only when the name is wrong")

print("\n=== show interface answers from the dataplane ===")
#
# It used to be handed to vtysh, which answers from FRR's view - and FRR sees
# only what linux_cp has mirrored. On a box with a GigabitEthernet0/12/0
# configured, "show interface description" answered "lo" (#112). Same shape as
# "show ip route" answering from the RIB.
sh = shell_with_box()
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.feed("show interface")
text = out.getvalue()
check("TenGigabitEthernet0/8/0" in text and "host-veth-vpp" in text,
      f"the dataplane's interfaces are listed: {text!r}")
check("up" in text, "with their state")
# local0 is listed - hiding an interface the dataplane reports would be the
# command deciding what the operator may see - while not being offered for
# completion, because it configures nothing.
check("local0" in text, "including VPP's own loopback, which it does report")
print("  " + [l for l in text.splitlines() if "TenGig" in l][0].strip())

# `show interface description` used to be answered with a line saying the
# dataplane carries no descriptions. That was true when it was written and
# stopped being true when `description` began to be stored as the interface
# tag - and it was left saying so, which is worse than never having answered,
# because somebody reading it stops looking for the feature that exists. The
# assertion that held it in place is now the other way round (#192).
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.feed("show interface description")
check("no per-interface description" not in out.getvalue(),
      "the box does not deny having descriptions it can store")
check("Description" in out.getvalue(),
      f"it answers with the column: {out.getvalue()!r}")

out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.feed("show interface tengig")
check("TenGigabitEthernet0/8/0" in out.getvalue()
      and "host-veth-vpp" not in out.getvalue(),
      f"a name filters, ignoring case: {out.getvalue()!r}")

# An empty table reads as "this box has no interfaces", which is a different
# and much more alarming statement than "I could not ask".
sh_down = shell_with_box(dp_rc=1)
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh_down.feed("show interface")
check("not answering" in out.getvalue(),
      f"a dead dataplane says so rather than printing an empty table: "
      f"{out.getvalue()!r}")
print("  dataplane down -> says it could not ask")

print("\n=== the prompt names the hierarchy AND its subject ===")
#
# "[edit cgnat pool]" is the same prompt on a box with twenty pools: it says
# the operator is in a pool, not which one, and that is the question they have.
#
# Blocks entered by an ARGUMENT moved nothing at all, so nothing on the screen
# said the next statement would land on GigabitEthernet0/12/0 - and an
# "mtu 1500" typed one block later than intended went to the wrong interface
# with no warning (#108).
sh = shell_with_box()
check(sh.prompt().endswith("> "), f"operational mode is a > prompt: {sh.prompt()}")
sh.feed("configure")
check(sh.prompt() == "hexarouting# [edit] ", f"top of config: {sh.prompt()!r}")

sh.feed("cgnat pool residential")
check("cgnat pool residential" in sh.prompt(),
      f"a pool names the pool: {sh.prompt()!r}")
sh.feed("exit")

sh.feed("interface TenGigabitEthernet0/8/0")
check("interface TenGigabitEthernet0/8/0" in sh.prompt(),
      f"an interface names the interface: {sh.prompt()!r}")
print(f"  {sh.prompt().strip()}")

# An 80-column serial console is what a racked box is seen through.
for probe in ("cgnat pool residential", "interface TenGigabitEthernet0/8/0"):
    pass
check(len(sh.prompt()) <= 60,
      f"the prompt is {len(sh.prompt())} columns; a racked box has 80 and a "
      f"prompt that pushes the cursor off the line is worse than a vague one")
sh.feed("exit")

sh.feed("user noc")
check("user noc" in sh.prompt(), f"a user names the account: {sh.prompt()!r}")
sh.feed("exit")

print("\n=== show this inside an interface is not empty ===")
# It printed the header and stopped, because an interface's mtu and urpf are
# staged in Config.mtus and Config.urpf while this branch looked only at
# Config.routing. An operator who runs it and sees nothing concludes the block
# is empty and configures accordingly - which makes an empty answer worse than
# no command at all.
sh.feed("interface TenGigabitEthernet0/8/0")
sh.feed("mtu 9000")
sh.feed("urpf strict")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.feed("show this")
text = out.getvalue()
check("interface TenGigabitEthernet0/8/0" in text,
      f"the interface is named: {text!r}")
check("mtu 9000" in text, f"and its MTU is shown: {text!r}")
check("urpf strict" in text, f"and its uRPF: {text!r}")
print("  " + text.strip().replace("\n", " / "))

# --- address-family is a block (#150) ---
#
# It was not one, so `exit` inside an address-family popped the enclosing
# `router bgp` and the next line the operator typed went somewhere they did not
# intend - with the prompt saying they were still in BGP. The statement always
# reached FRR either way, which is why nothing noticed: what was wrong is what
# the operator was told.
#
# A unit test and not a lab: this is about what the VTY believes, and it needs
# no dataplane at all.
print("\n=== address-family is a block, and exit leaves only it ===")
sh = shell_with_box()
sh.feed("configure")
sh.feed("router bgp 65000")
check("router bgp 65000" in sh.prompt(), f"in BGP: {sh.prompt()}")
sh.feed("address-family ipv4 unicast")
check("address-family ipv4 unicast" in sh.prompt(),
      f"the prompt names the address family: {sh.prompt()}")
sh.feed("exit")
check("router bgp 65000" in sh.prompt(),
      f"exit leaves the address family and lands in BGP: {sh.prompt()}")
check("address-family" not in sh.prompt(),
      f"and not still inside it: {sh.prompt()}")
sh.feed("exit")
check("[edit]" in sh.prompt(), f"a second exit leaves BGP: {sh.prompt()}")
print(f"  {sh.prompt().strip()}")

# FRR's own spelling, which an operator arriving from another vendor types.
sh = shell_with_box()
sh.feed("configure")
sh.feed("router bgp 65000")
sh.feed("address-family ipv6 unicast")
sh.feed("exit-address-family")
check("router bgp 65000" in sh.prompt() and "address-family" not in sh.prompt(),
      f"exit-address-family leaves it too: {sh.prompt()}")

# It is only valid inside one. Popping whatever block happens to be open would
# be the same defect pointing the other way.
before = sh.failed
sh.feed("exit-address-family")
check(sh.failed > before,
      "and outside an address-family it is refused, not silently obeyed")

# --- the "not checked" marker is DISPLAY ONLY (#144) ---
#
# A marker that reached the stored configuration would be appended mid-statement
# in the file handed to FRR, and FRR is not obliged to accept a comment there.
# A configuration that cannot be fed back is not a configuration, so this is
# the check that the marker never leaves the display path.
print("\n=== the unchecked marker never leaves the display ===")
from hexash import mark_unchecked                                # noqa: E402
from config import Config                                        # noqa: E402

policy = [
    "route-map RM-X permit 10",
    " match ip address prefix-list PL-A",
    " set local-preference 200",
    " set tag 100",
]
cfg = Config()
cfg.routing = list(policy)
# Marked through the real text, header and all - the same string `show
# running-config` prints and `save` writes.
shown = mark_unchecked(cfg.text().splitlines())
check(any("not checked by this VTY" in l for l in shown),
      f"the unmodelled statement is marked: {[l for l in shown if 'tag' in l]}")
check(not any("<-" in l for l in shown if "local-preference" in l),
      "and a modelled one is not")
check("not checked" not in cfg.text(),
      "the marker is not in the text a commit would send")
# An operator WILL copy what is on their screen into a file, so the displayed
# text has to parse back to exactly the configuration it was made from, with
# the marker taken off again.
again = Config.parse("\n".join(shown))
check([l.strip() for l in again.routing if l.strip()]
      == [l.strip() for l in policy if l.strip()],
      f"the displayed text parses back unchanged: {again.routing}")
check(not again.unknown,
      f"and with nothing the VTY cannot read: {again.unknown}")


# --- AS-path regexes: what FRR's `_` means (#146) ---
#
# `_` is "any boundary" - start, end, or a separator - and it is the whole
# reason the match count exists as a report rather than a refusal. `65001`
# compiles, is legal, and also matches 165001 and 650012. An operator reading
# their own regex back does not see that; a count against real paths does.
print("\n=== what an AS-path regex actually matches ===")
from hexash import as_path_matches, undefined_policy_lists   # noqa: E402

seen = ["65001", "65001 65002", "165001", "650012", "65003 65001"]
exact, _ = as_path_matches("^65001$", seen)
loose, _ = as_path_matches("65001", seen)
check(exact == 1, f"an anchored regex matches only the path it names: {exact}")
check(loose == len(seen),
      f"and the same regex unanchored matches every one of them: {loose}")
check(loose > exact,
      "which is the difference the report exists to show")
bound, _ = as_path_matches("_65001_", seen)
check(bound == 3, f"`_` is a boundary, not a character: {bound}")
tail, example = as_path_matches("_65002$", seen)
check(tail == 1 and example == "65001 65002",
      f"and an origin match names a path it found: {tail} {example}")

# A name a route-map matches on that nothing defines. FRR accepts it, the
# clause matches nothing forever, and the policy reads correctly.
print("\n=== a policy list nothing defines ===")
check(len(undefined_policy_lists(
    ["route-map R permit 10", "match as-path AS-GONE"])) == 1,
    "an undefined AS-path list is caught")
check(not undefined_policy_lists(
    ["bgp as-path access-list AS1 permit ^65001$",
     "route-map R permit 10", "match as-path AS1"]),
    "and a defined one is not")
# FRR spells a community list two ways and both create it.
for spelling in ("bgp community-list standard CM1 permit 65000:1",
                 "bgp community-list CM1 permit 65000:1"):
    check(not undefined_policy_lists(
        [spelling, "route-map R permit 10", "match community CM1"]),
        f"a community list defined as `{spelling.split()[2]}` counts")


# --- `neighbor` is not a BGP word (#178) ---
#
# OSPF uses it for NBMA and point-to-multipoint. Scanning every routing line
# for it refused a valid OSPF statement with a sentence about remote-as, on a
# box that need not have BGP at all. The last case is the one that catches a
# naive fix: an OSPF stanza AFTER a BGP one.
print("\n=== a check that must not fire outside its own block ===")
from hexash import bgp_errors                                    # noqa: E402

check(not bgp_errors(["router ospf", "neighbor 10.0.0.2 poll-interval 60"]),
      "OSPF NBMA, typed unindented, is not refused")
check(not bgp_errors(["router ospf", " neighbor 10.0.0.2 poll-interval 60"]),
      "nor when read back from FRR, indented")
check(len(bgp_errors(["router bgp 65000", "neighbor 10.0.0.9 description x"])) == 1,
      "and a BGP neighbour with no remote-as still is")
check(len(bgp_errors(["router bgp 65000", " neighbor 10.0.0.9 description x"])) == 1,
      "indented too")
check(not bgp_errors(["router bgp 65000", " neighbor 10.0.0.2 remote-as 65001",
                      " address-family ipv4 unicast",
                      "  neighbor 10.0.0.2 activate",
                      " exit-address-family", "exit"]),
      "an address-family keeps the BGP context")
check(not bgp_errors(["router bgp 65000", " neighbor 10.0.0.2 remote-as 65001",
                      "exit", "router ospf",
                      " neighbor 10.0.0.9 poll-interval 60"]),
      "and an OSPF stanza after a BGP one is out of context again")
# --- IGP authentication keys are secrets too (#169) ---
#
# `redact()` covered an account's password and a BGP neighbour's and stopped
# there, so every OSPF and IS-IS key printed in full. Each of these opens the
# adjacency on the OTHER router as well, and a screen shared during an
# incident is a screen that has left the building.
print("\n=== OSPF and IS-IS keys never reach the screen ===")
from system import redact, User                                  # noqa: E402

SECRET = "S3CR3T-IGP-KEY"
igp = [
    "router isis 1",
    f" domain-password md5 {SECRET}",
    f" area-password md5 {SECRET}",
    "interface eth0",
    f" ip ospf authentication-key {SECRET}",
    f" ip ospf message-digest-key 1 md5 {SECRET}",
    f" isis password md5 {SECRET}",
    # The boundaries. A rule keyed on the word "password" mangles both of
    # these, and a line mangled on screen is a line an operator retypes wrong.
    " description the ospf password lives on the other router",
    "router bgp 65000",
    " neighbor PG peer-group",
    f" neighbor 10.0.0.1 password {SECRET}",
]
FORMS = ("ip ospf authentication-key", "ip ospf message-digest-key 1 md5",
         "isis password md5", "domain-password md5", "area-password md5")

shown = redact(igp)
for form in FORMS:
    # The form is kept and only the key goes: an operator has to be able to
    # see that a key IS set, or they set a second one over the top of it.
    check(f" {form} <set, not shown>" in shown,
          f"`{form}` is redacted: {[l for l in shown if form in l]}")
check(SECRET not in "\n".join(shown),
      f"the key is nowhere in the output: {[l for l in shown if SECRET in l]}")
check(" description the ospf password lives on the other router" in shown,
      f"a description that merely says the word is untouched: {shown}")
check(" neighbor PG peer-group" in shown,
      f"and a peer-group line keeps its own second word: {shown}")

# What #169 extends, not what it replaces.
check(" neighbor 10.0.0.1 password <set, not shown>" in shown,
      f"the BGP neighbour form still works: {shown}")
check(redact([" password $6$salt$hash"]) == [" password <set, not shown>"],
      "and so does an account's")

# Both display paths. `show this` inside a block reads the routing lines
# straight out of the candidate, and it was the path that printed a BGP
# password nowhere else printed - so it is checked, not assumed.
cfg = Config()
cfg.routing = list(igp)
cfg.users["op"] = User("op")
cfg.users["op"].password = "$6$salt$hash"

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    hexash.Shell.print_config(cfg)
running = buf.getvalue()
check(SECRET not in running, "show running-config prints no IGP key")
check("$6$salt$hash" not in running, "and no account hash either")
for form in FORMS:
    check(f" {form} <set, not shown>" in running,
          f"`{form}` is redacted by show running-config too")

sh = shell_with_box()
sh.frr.running = lambda: list(igp)
sh.feed("configure")
sh.frr_ctx = ["interface eth0"]
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.show_this()
this = buf.getvalue()
check(SECRET not in this, f"show this prints no IGP key: {this}")
check("ip ospf authentication-key <set, not shown>" in this,
      f"and shows that one is set: {this}")
check("isis password md5 <set, not shown>" in this, f"in every form: {this}")

# It stays a DISPLAY transform. A rollback revision holding <set, not shown>
# is a rollback that cannot restore the adjacency it is being run to restore.
check(SECRET in cfg.text(),
      "the text a commit sends and a revision stores keeps the real key")

# --- a key chain carries a secret too (#181) ---
#
# `key chain` is the OTHER way OSPF, IS-IS and RIP carry an authentication
# secret, and #169 covered only the five spellings it named, so `key-string`
# and `ip rip authentication string` printed in full. Verified against FRR
# 10.7.1: `key-string LINE` and `ip rip authentication string LINE` each take
# the secret as a single trailing word, while `key chain WORD`,
# `key (0-2147483647)` and `ip rip authentication key-chain WORD` are a name,
# an id and a reference - none of them a secret, and mangling any of them
# leaves an operator unable to tell WHICH chain is on screen.
print("\n=== a key chain's secret never reaches the screen ===")

KCSECRET = "S3CR3T-KEYCHAIN"
kc = [
    "key chain KC-CORE",
    " key 1",
    f"  key-string {KCSECRET}",
    "  cryptographic-algorithm hmac-sha-256",
    "interface eth0",
    f" ip rip authentication string {KCSECRET}",
    # The boundaries. Both of these name a chain rather than hold one, and a
    # rule keyed on "key" or on "string" takes them with it.
    " ip rip authentication key-chain KC-CORE",
    " ip ospf authentication key-chain KC-CORE",
    " description the key-string for rip lives on the other router",
]

shown = redact(kc)
check(KCSECRET not in "\n".join(shown),
      f"the key is nowhere in the output: {[l for l in shown if KCSECRET in l]}")
check("  key-string <set, not shown>" in shown,
      f"`key-string` is redacted and still shows a key is set: {shown}")
check(" ip rip authentication string <set, not shown>" in shown,
      f"and so is RIP's inline string: {shown}")
# The chain has to stay identifiable, or the operator cannot tell which of
# several chains they are looking at - and sets a second key over the top.
check("key chain KC-CORE" in shown,
      f"the `key chain` line is a NAME, not a secret, and is untouched: {shown}")
check(" key 1" in shown, f"and the key id survives: {shown}")
check("  cryptographic-algorithm hmac-sha-256" in shown,
      f"as does the algorithm that says how the key is used: {shown}")
check(" ip rip authentication key-chain KC-CORE" in shown,
      f"a key-chain REFERENCE is a name too: {shown}")
check(" ip ospf authentication key-chain KC-CORE" in shown,
      f"OSPF's reference likewise: {shown}")
check(" description the key-string for rip lives on the other router" in shown,
      f"a description that merely says the word is untouched: {shown}")

# What #181 extends, not what it replaces.
check(redact([" password $6$salt$hash"]) == [" password <set, not shown>"],
      "an account's password still redacts")
check(redact([" neighbor 10.0.0.1 password P"])
      == [" neighbor 10.0.0.1 password <set, not shown>"],
      "and a BGP neighbour's")
check(redact([" ip ospf message-digest-key 1 md5 K"])
      == [" ip ospf message-digest-key 1 md5 <set, not shown>"],
      "and every IGP form from #169")

# Both display paths, the same as #169: `show this` inside a block reads the
# routing lines straight out of the candidate, and it is the path that printed
# a secret nowhere else printed.
cfg = Config()
cfg.routing = list(kc)

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    hexash.Shell.print_config(cfg)
running = buf.getvalue()
check(KCSECRET not in running, f"show running-config prints no key: {running}")
check("key-string <set, not shown>" in running,
      f"and shows that one is set: {running}")

sh = shell_with_box()
sh.frr.running = lambda: list(kc)
sh.feed("configure")
sh.frr_ctx = ["key chain KC-CORE"]
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.show_this()
this = buf.getvalue()
check(KCSECRET not in this, f"show this prints no key-string: {this}")
check("key-string <set, not shown>" in this,
      f"and shows that one is set: {this}")
check("key chain KC-CORE" in this, f"under the chain's own name: {this}")

sh.frr_ctx = ["interface eth0"]
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.show_this()
this = buf.getvalue()
check(KCSECRET not in this, f"show this prints no RIP string either: {this}")
check("ip rip authentication string <set, not shown>" in this,
      f"and shows that one is set: {this}")
check("ip rip authentication key-chain KC-CORE" in this,
      f"while the reference beside it is untouched: {this}")

# It stays a DISPLAY transform. A revision holding <set, not shown> is a
# rollback that cannot restore the adjacency it is being run to restore.
check(KCSECRET in cfg.text(),
      "the text a commit sends and a revision stores keeps the real key")



# --- a loopback is created by being named, and removed by name (#179, #182) ---
print("\n=== loopbacks and the linux_cp pair ===")
from config import _is_loopback                                  # noqa: E402

for name, want in (("loop0", True), ("loop12", True),
                   ("loopback0", False), ("loop", False),
                   ("loopX", False), ("GigabitEthernet0/12/0", False)):
    check(_is_loopback(name) is want,
          f"{name} is{'' if want else ' not'} a loopback")

made = Config.parse("""! cgnat
interface loop0
 linux-name loop0
 ipv4 address 10.99.0.1/32
 exit
""")
check(made.loopbacks == {"loop0"}, f"naming one creates it: {made.loopbacks}")
check(made.linux_names.get("loop0") == "loop0",
      f"and its pair is recorded: {made.linux_names}")
check(not made.unknown, f"with nothing unread: {made.unknown}")

# Both directions, because a rollback that can create a loopback and not
# remove it leaves an interface the configuration no longer mentions.
empty = Config.parse("! cgnat\n")
fwd = empty.delta_to(made)
back = made.delta_to(empty)
check(fwd["loopback_add"] == ["loop0"] and fwd["lcp_set"] == {"loop0": "loop0"},
      f"the delta creates it and pairs it: {fwd['loopback_add']}")
check(back["loopback_del"] == ["loop0"] and back["lcp_del"] == ["loop0"],
      f"and the reverse removes both: {back['loopback_del']}")

# A name Linux cannot take. VPP's own names are full of '/', and copying one
# across is the obvious mistake, so it is refused where it is written rather
# than at commit with a bare return code.
bad = Config.parse("""! cgnat
interface loop0
 linux-name GigabitEthernet0/12/0
 exit
""")
check(bad.unknown and "cannot be a Linux" in bad.unknown[0][2],
      f"a name with a slash is refused: {bad.unknown}")
check("loop0" not in bad.linux_names,
      "and is not recorded as though it had been accepted")


# --- OSPF: shapes that commit cleanly and do nothing (#174) ---
#
# Every one of these is accepted by FRR. The rule a check is held to here is
# that it must catch something FRR does not AND must not refuse a
# configuration that is fine - so each case below has its negative beside it.
print("\n=== OSPF findings ===")
from hexash import ospf_findings, ospf_state                     # noqa: E402
from config import Pool                                          # noqa: E402

ADDRS = {"Gi0": ["200.150.0.1/24"], "Gi1": ["100.64.0.1/22"]}
HAVE = {"Gi0", "Gi1"}
_pool = Pool("res")
_pool.inside = ["100.64.0.0/22"]
POOLS = {"res": _pool}


def find(lines, addrs=ADDRS, have=HAVE, pools=None):
    return ospf_findings(lines, addrs, have, pools or {})


# A passive-interface naming an interface the box does not have. Refused,
# because the port the operator meant to keep quiet is not the one that goes
# quiet, and the commit says nothing.
r = find(["router ospf", "passive-interface Gi9"])
check(len(r) == 1 and r[0][0] == "error",
      f"a passive-interface naming nothing is refused: {r}")
check(not find(["router ospf", "passive-interface Gi0"]),
      "and one naming a real interface is not")
# `default` is a keyword, not an interface name.
check(not find(["router ospf", "passive-interface default"]),
      "nor is the 'default' keyword")

# A network statement matching no address: OSPF runs on nothing.
r = find(["router ospf", "network 10.9.0.0/16 area 0"])
check(len(r) == 1 and r[0][0] == "warning",
      f"a network matching no address is reported: {r}")
check(not find(["router ospf", "network 200.150.0.0/24 area 0"]),
      "and one that matches is not")
# With no addresses read back, the check MUST NOT fire - it cannot judge.
check(not find(["router ospf", "network 10.9.0.0/16 area 0"], addrs={}),
      "and with no addresses to compare against it says nothing")

# An area border router with no backbone.
r = find(["router ospf", "network 200.150.0.0/24 area 1",
          "network 100.64.0.0/22 area 2"])
check(any("area 0" in w for _, _, w in r),
      f"two areas and no backbone is reported: {r}")
check(not find(["router ospf", "network 200.150.0.0/24 area 1",
                "network 100.64.0.0/22 area 0"]),
      "and having a backbone is not")

# THE cross-half check: a subscriber-facing port speaking OSPF. FRR does not
# know what a CGNAT pool is and the dataplane does not know what OSPF is.
r = find(["router ospf", "network 100.64.0.0/22 area 0"], pools=POOLS)
check(any("facing subscribers" in w for _, _, w in r),
      f"a port inside a pool's inside prefix speaking OSPF is reported: {r}")
check(not find(["router ospf", "network 100.64.0.0/22 area 0",
                "passive-interface Gi1"], pools=POOLS),
      "and making it passive silences it")
check(not find(["router ospf", "network 200.150.0.0/24 area 0"], pools=POOLS),
      "while a port that is not inside a pool is never reported")

# A full table into the IGP.
check(any("whole BGP" in w for _, _, w in find(
          ["router ospf", "redistribute bgp"])),
      "redistribute bgp with no route-map is reported")
check(not find(["router ospf", "redistribute bgp route-map RM"]),
      "and with one it is not")

# Nothing fires on a box with no OSPF at all.
check(not find(["router bgp 65000", "neighbor 10.0.0.2 remote-as 65001"],
               pools=POOLS),
      "and none of it fires when there is no OSPF")

# Parsed by statement, not by indentation - the same asymmetry that has now
# caught three checks in this file.
typed = ["router ospf", "network 200.150.0.0/24 area 0"]
indented = ["router ospf", " network 200.150.0.0/24 area 0"]
check(ospf_state(typed)[0] == ospf_state(indented)[0],
      "typed and read-back statements parse the same")


# --- IS-IS: shapes that commit cleanly and carry nothing (#175) ---
#
# Every one is accepted by FRR and every one is local to this box, which is
# the line drawn here: whether the neighbour agrees is not in the
# configuration and is not claimed.
print("\n=== IS-IS findings ===")
from hexash import isis_findings                                 # noqa: E402

GOOD = ["router isis HEXA", "net 49.0001.0000.0000.0001.00",
        "metric-style wide", "interface Gi0", "ip router isis HEXA"]
check(not isis_findings(GOOD), f"a complete instance is clean: {isis_findings(GOOD)}")

# An instance with no NET never starts; FRR accepts the stanza.
r = isis_findings(["router isis HEXA", "interface Gi0", "ip router isis HEXA"])
check(any(sv == "error" and "no 'net'" in w for sv, _, w in r),
      f"no net is refused: {r}")

# An interface joining a tag no instance declares - the same shape as the
# undeclared peer group and the undefined route-map.
r = isis_findings(["interface Gi0", "ip router isis GONE"])
check(any(sv == "error" and "declares that instance" in w for sv, _, w in r),
      f"an undeclared tag is refused: {r}")

# An instance nothing joins runs and carries nothing.
r = isis_findings(["router isis HEXA", "net 49.0001.0000.0000.0001.00"])
check(any("no interface joins it" in w for _, _, w in r),
      f"an instance nobody joins is reported: {r}")

# The IPv6 half silently absent while IPv4 works.
v6 = ["router isis HEXA", "net 49.0001.0000.0000.0001.00",
      "interface Gi0", "ipv6 router isis HEXA"]
check(any("topology ipv6-unicast" in w for _, _, w in isis_findings(v6)),
      "IPv6 with no topology statement is reported")
check(not isis_findings(v6[:2] + ["topology ipv6-unicast"] + v6[2:]),
      "and with it, nothing is")

# Narrow metrics cap at 63 - the classic silent limitation on a 10G box.
narrow = GOOD[:2] + ["interface Gi0", "ip router isis HEXA", "isis metric 100"]
check(any("metric-style wide" in w for _, _, w in isis_findings(narrow)),
      "a metric above 63 with narrow encoding is reported")
check(not isis_findings(GOOD + ["isis metric 100"]),
      "and with wide it is not")

# A level-2-only instance with a level-1 circuit forms no adjacency, and both
# halves are accepted by FRR.
lv = GOOD[:2] + ["is-type level-2-only", "interface Gi0",
                 "ip router isis HEXA", "isis circuit-type level-1"]
check(any("forms no adjacency" in w for _, _, w in isis_findings(lv)),
      "a circuit-type the instance cannot serve is reported")
check(not isis_findings(GOOD[:2] + ["is-type level-2-only", "interface Gi0",
                                    "ip router isis HEXA",
                                    "isis circuit-type level-2-only"]),
      "and a matching one is not")

check(not isis_findings(["router bgp 65000", "neighbor 10.0.0.2 remote-as 65001"]),
      "and none of it fires when there is no IS-IS")

# By statement, not by indentation - the asymmetry that has caught four
# checks in this file now.
check(isis_findings([" " + l for l in GOOD]) == isis_findings(GOOD),
      "typed and read-back statements parse the same")


# --- a box with only local0 must say WHY (#190) ---
#
# `local0` alone is the truth and it is not an answer. It was reported twice
# from a box in use, and answered both times with "run these commands and tell
# me" - which is the defect: the box should not need an interrogation to say
# what it decided.
print("\n=== why the dataplane has no interface ===")
import tempfile                                                  # noqa: E402

_d = tempfile.mkdtemp()


def why(conf, env=""):
    c = os.path.join(_d, "startup.conf")
    e = os.path.join(_d, "tune.env")
    open(c, "w").write(conf)
    open(e, "w").write(env)
    probe = hexash.Shell.__new__(hexash.Shell)
    probe.STARTUP_CONF, probe.TUNE_ENV = c, e
    return probe.why_no_dataplane_interface()


r = why("plugin dpdk_plugin.so { disable }")
check(r and "hugepages" in r[0],
      f"first-boot mode is named as first-boot mode: {r[:1]}")
check(any("Reboot" in l for l in r),
      "and says what to do about it")

r = why("dpdk {\n  no-pci\n}", "HEXA_DATAPLANE_IF=\n")
check(r and "no interface was named" in r[0],
      f"an empty HEXA_DATAPLANE_IF is named as the cause: {r[:1]}")
check(any("tune.env" in l for l in r),
      "and the file to edit is named")

r = why("dpdk {\n  no-pci\n}", 'HEXA_DATAPLANE_IF="ens19"\n')
check(r and "ens19" in r[0],
      f"a named interface that was refused is quoted back: {r[:1]}")
# The three reasons a named interface produces no-pci are different work, so
# all three are offered rather than one guessed at.
check(any("default route" in l for l in r),
      "including the one that is a deliberate refusal")

r = why("dpdk {\n  dev 0000:01:00.0\n}")
check(r and "did not bring it up" in r[0],
      f"a NIC that was named and did not appear is a different answer: {r[:1]}")

# A box that is not an appliance has no startup.conf, and inventing an
# explanation for it would be worse than saying nothing.
probe = hexash.Shell.__new__(hexash.Shell)
probe.STARTUP_CONF = os.path.join(_d, "does-not-exist")
probe.TUNE_ENV = os.path.join(_d, "nor-this")
check(probe.why_no_dataplane_interface() == [],
      "and a box with no startup configuration explains nothing")


# --- filtering a show, without leaving the VTY (#191) ---
print("\n=== pipe filters ===")
from hexash import apply_pipe, split_pipe, PIPE_FILTERS          # noqa: E402

TXT = """interface Gi0
 mtu 1500
 ipv4 address 10.0.0.1/24
interface Gi1
 mtu 9000
router bgp 65000
 neighbor 10.0.0.2 remote-as 65001
"""


def piped(*stages):
    lines, why = apply_pipe(TXT, list(stages))
    return why if why else lines


check(piped("include mtu") == [" mtu 1500", " mtu 9000"],
      f"include keeps the matching lines: {piped('include mtu')}")
check(piped("match mtu") == piped("include mtu"),
      "match is the same filter under the name operators type")
check(" mtu 1500" not in piped("exclude mtu"), "exclude drops them")
# `section` is the one that pays most here, because this box's configuration
# IS blocks: a line at column 0 and everything indented under it.
check(piped("section interface Gi0")
      == ["interface Gi0", " mtu 1500", " ipv4 address 10.0.0.1/24"],
      f"section keeps one block and stops at the next: "
      f"{piped('section interface Gi0')}")
check(piped("count") == ["7"], f"count answers with a number: {piped('count')}")
check(len(piped("first 2")) == 2 and len(piped("last 2")) == 2,
      "first and last take the head and the tail")
check(piped("include interface", "exclude Gi1") == ["interface Gi0"],
      f"and they chain: {piped('include interface', 'exclude Gi1')}")

# An unknown filter is REFUSED, not ignored. Ignoring it would answer the
# question the operator did not ask, with no sign that it had - and `grep` is
# exactly what somebody types first.
check("not a filter" in piped("grep mtu"), f"grep is refused: {piped('grep mtu')}")
check("needs <n>" in piped("first"), "a filter missing its argument is refused")
# The exact words the product prints, read off it rather than remembered:
# "needs a number, not 'x'".
check("needs a number, not 'x'" in piped("first x"),
      f"and so is one given the wrong kind: {piped('first x')}")

# THE property that makes a pipe safe here: it runs over the text the command
# already produced, after the redactions - so it can never reveal a value the
# unfiltered command hides. Asserted on the real redaction, not on a
# hand-written string.
from system import redact                                        # noqa: E402
_secret = "n0tinlogs"
_shown = "\n".join(redact([" neighbor 10.0.0.2 password " + _secret]))
_lines, _ = apply_pipe(_shown, ["include password"])
check(_secret not in "\n".join(_lines),
      f"a filter cannot reveal what the command hid: {_lines}")
check(any("not shown" in l for l in _lines),
      "and the line it does show is the redacted one")

check(split_pipe("show running-config | include x | count")
      == ("show running-config", ["include x", "count"]),
      "a command and its filters are split apart")


# ---------------------------------------------------------------------------
# `show interface terse | description | utilization` (#192)

import routes                                                    # noqa: E402


def capture(sh, line):
    """What the VTY printed for one command."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sh.feed(line)
    return buf.getvalue()


#
# The counter text is the dataplane's own, copied off a running VPP rather
# than written by hand: the layout - a first counter on the interface's line
# and the rest indented, names with spaces in them, and NO line at all for an
# interface whose counters are zero - is the whole difficulty, and a
# hand-written sample would have had whatever shape made the parser pass.
COUNTERS_A = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
host-veth-vpp                     1      up          9000/0/0/0     tx packets                     3
                                                                    tx bytes                     266
local0                            0     down          0/0/0/0
loop3                             3      up          9000/0/0/0
pg0                               4      up          9000/0/0/0     rx packets                    20
                                                                    rx bytes                    2000
                                                                    drops                          4
pg0.100                           5      up           0/0/0/0
"""
COUNTERS_B = COUNTERS_A.replace("2000", "3000").replace(
    "                    20", "                    30").replace(
    "                          4", "                          9")

_c = routes.parse_interface_counters(COUNTERS_A)
check(_c["pg0"] == {"rx packets": 20, "rx bytes": 2000, "drops": 4},
      f"a counter name with a space in it survives: {_c.get('pg0')}")
check(_c["host-veth-vpp"]["tx bytes"] == 266,
      "the first counter shares the interface's line")
check(_c["loop3"] == {} and _c["local0"] == {},
      "an interface with no counter line has no counters, not no entry")
check("Name" not in _c, "the header is not an interface")

HW = """              Name                Idx   Link  Hardware
TenGigabitEthernet0/8/0            1     up   TenGigabitEthernet0/8/0
  Link speed: 10 Gbps
local0                             0    down  local0
  Link speed: unknown
pg0                                4    down  pg0
  Link speed: unknown
"""
_h = routes.parse_hardware(HW)
check(_h["TenGigabitEthernet0/8/0"] == {"link": "up", "speed": 10 ** 10},
      f"a link speed becomes a number: {_h.get('TenGigabitEthernet0/8/0')}")
check(_h["pg0"]["speed"] is None,
      "and 'unknown' stays unknown - not zero, which would divide")
check(_h["pg0"]["link"] == "down", "the link column is the LINK, not admin")


def box_192(iface_text, hw_text=HW, counters=None, descs=None, elapsed=None,
            addr_text=""):
    """A box whose dataplane answers with a scripted sequence of counters."""
    sh = hexash.Shell()
    sh.caps = {hexash.CAP_ALL, hexash.CAP_CGNAT}
    left = list(counters or [])

    def run(_self, cmd):
        if cmd.startswith("show hardware-interfaces"):
            return 0, hw_text
        if cmd == "show interface addr":
            return 0, addr_text
        if cmd == "show interface" and left:
            return 0, left.pop(0)
        if cmd.startswith("show interface"):
            return 0, iface_text
        return 0, ""

    sh.dp = type("D", (), {"TRACE": False, "run": run})()
    sh.frr = type("F", (), {"available": lambda s: False,
                            "running": lambda s: [],
                            "show": lambda s, c: (1, "")})()
    sh.device_descriptions = lambda: dict(descs or {})
    if elapsed is not None:
        clock = iter(elapsed)
        hexash.time.monotonic = lambda: next(clock)
        hexash.time.sleep = lambda _s: None
    return sh


TERSE_IF = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)
TenGigabitEthernet0/8/0           1      up          9000/0/0/0
local0                            0     down          0/0/0/0
pg0                               4      up          9000/0/0/0
pg0.100                           5      up          9000/0/0/0
"""
ADDRS = """TenGigabitEthernet0/8/0 (up):
  L3 198.51.100.1/30
  L3 2001:db8::1/64
pg0.100 (up):
  L3 192.0.2.1/24
"""

sh = box_192(TERSE_IF, addr_text=ADDRS)
out = capture(sh, "show interface terse")
check("Admin" in out and "Link" in out,
      f"terse has both states, because they are two facts: {out}")
check("inet6" in out and "2001:db8::1/64" in out,
      "a second family is a second row, not a truncation")
# The one conclusion this view exists for. pg0 is admin up and link down.
check("pg0" in out and "link down" in out,
      f"admin up with the link down is called out: {out}")
check("that is the wire or the far end" in out,
      "and named as a wire fault, not a configuration one")
# A sub-interface has no hardware of its own. Its link is its parent's - and
# the interfaces an ISP terminates subscribers on are all sub-interfaces, so
# a '?' here would blank the column exactly where it is needed.
_row = [l for l in out.splitlines() if l.split()[:1] == ["pg0.100"]][0]
check(_row.split()[1:3] == ["up", "down"],
      f"a sub-interface inherits its parent's link: {_row}")

# Both numbers of the word. `show interfaces terse` is what an operator arriving
# from a Juniper types, and a parser error for the plural on a VTY built to look
# like one is the opposite of the point.
sh = box_192(TERSE_IF, addr_text=ADDRS)
check("Admin" in capture(sh, "show interfaces terse"),
      "the plural is the same command")
# A filtered listing says so, rather than reporting the filtered count as the
# number of interfaces the box has.
sh = box_192(TERSE_IF, addr_text=ADDRS)
out = capture(sh, "show interface terse pg0.100")
check("1 of 4 interface(s) shown" in out, f"a narrowed listing says so: {out}")

# The description view. This used to print a table and then say the dataplane
# carries no descriptions - true when written, false since `description`
# started being stored as the interface tag, and left saying so.
sh = box_192(TERSE_IF, descs={"TenGigabitEthernet0/8/0": "TRANSITO Vivo"})
out = capture(sh, "show interface description")
check("TRANSITO Vivo" in out, f"a description is shown: {out}")
check("carries no per-interface description" not in out,
      "and the box no longer denies having them")
check("1 of 4 interface(s) described" in out, f"counted: {out}")
check("description <text>" in out,
      "an empty column is a question; the answer is how to fill it")

# Utilisation. The rate must come from the MEASURED elapsed time, not from the
# interval that was asked for: on a box busy enough to be worth asking about,
# a 2s sleep is not 2s, and a rate computed from the nominal figure is wrong
# in exactly the conditions the question gets asked in. Here the clock moves
# 4s per sample while the interval says 2, so the two answers differ by half.
sh = box_192(TERSE_IF, counters=[COUNTERS_A, COUNTERS_B],
             elapsed=[100.0, 104.0])
out = capture(sh, "show interface utilization interval 2 count 1")
# 1000 bytes in the 4s the clock actually moved is 2 kbit/s; the same bytes
# over the 2s that were ASKED for would read 4 kbit/s. The assertion is that
# the box says the first.
check("2.00 kbit/s" in out and "4.00 kbit/s" not in out,
      f"the rate is measured, not assumed from the interval: {out}")
check("sampling every 2s" in out and "2s in total" in out,
      f"and the interval is stated before the wait: {out}")
check("pg0 dropped 5 packet(s)" in out,
      f"what was dropped during the window is the fault: {out}")
check("busiest sample in this run" in out,
      "with no line speed there is no percentage to defend")

# With a line speed the bar IS a share of capacity, and the percentage is real.
HW_FAST = HW.replace("pg0                                4    down  pg0\n"
                     "  Link speed: unknown\n",
                     "pg0                                4    down  pg0\n"
                     "  Link speed: 1 Gbps\n")
sh = box_192(TERSE_IF, hw_text=HW_FAST, counters=[COUNTERS_A, COUNTERS_B],
             elapsed=[100.0, 104.0])
out = capture(sh, "show interface utilization interval 2 count 1")
check("share of its own line rate" in out and "% of 1.00 Gbit/s" in out,
      f"a known line rate is used as the capacity: {out}")
# A box can have both kinds at once - a NIC that reports its speed and a
# loopback that does not - and one footer claiming every bar is a share of a
# line rate would be wrong about half the table.
check("the rest have no link speed" in out and "loop3" in out.split(
          "the rest have no link speed")[-1],
      f"the unrated interfaces are named, not folded in: {out}")

# A counter that goes backwards is `clear interfaces` running mid-sample. It
# must not become a negative rate, and it must not pass silently either.
sh = box_192(TERSE_IF, counters=[COUNTERS_B, COUNTERS_A],
             elapsed=[100.0, 102.0])
out = capture(sh, "show interface utilization interval 1 count 1")
_rows = [l for l in out.splitlines() if l.split()[:1] == ["pg0"]]
check(_rows and "-" not in _rows[0],
      f"a cleared counter is not a negative rate: {_rows}")
check("went backwards" in out, f"and it is said out loud: {out}")

# An interface created between two samples has a counter that is a total since
# it was made. Reporting that as a rate would invent a spike out of somebody
# running `create loopback`.
_late = COUNTERS_B + ("loop9                             9      up"
                      "          9000/0/0/0     rx bytes"
                      "                  999999999\n")
sh = box_192(TERSE_IF, counters=[COUNTERS_A, _late], elapsed=[100.0, 102.0])
out = capture(sh, "show interface utilization interval 1 count 1")
check("loop9" not in out,
      f"an interface that appeared mid-sample has no rate yet: {out}")

sh = box_192(TERSE_IF)
out = capture(sh, "show interface utilization interval x")
check("needs a number, not 'x'" in out, f"a bad interval is refused: {out}")
out = capture(sh, "show interface utilization count 0")
check("between 1 and 120" in out, f"and so is a count of zero: {out}")

# Adding `terse` to `show interface` must not cost the interface names it has
# always completed - a new option that takes away an old one is how a CLI gets
# a reputation.
sh = box_192(TERSE_IF)
opts, _, _ = sh._options("show interface ", False)
check("terse" in opts and "utilization" in opts,
      f"the new views are offered: {sorted(opts)}")
check("TenGigabitEthernet0/8/0" in opts,
      f"and the interface names still are: {sorted(opts)}")
check("<interface>" not in opts,
      "the hint is a marker, not something to type")
opts, _, _ = sh._options("show interface pg", True)
check("pg0" in opts and "terse" not in opts,
      f"a prefix still narrows to the names: {sorted(opts)}")



# ---------------------------------------------------------------------------
# The typed hierarchies: `nat cgnat ...` and `router static` (#193)

def box_193():
    sh = shell_with_box()
    sh.frr = type("F", (), {"available": lambda s: True,
                            "running": lambda s: ["router bgp 65000"],
                            "show": lambda s, c: (1, "")})()
    sh.sys = type("S", (), {"read_ssh": lambda s: {},
                            "read_users": lambda s: {}})()
    sh.snmp = type("N", (), {"read": lambda s: ({}, [], {})})()
    sh.feed("configure")
    return sh

sh = box_193()
for line in ("nat cgnat pool residential", "inside 100.64.0.0/22",
             "outside 203.0.113.0/24", "block-size 512", "exit",
             "nat cgnat interface TenGigabitEthernet0/8/0 inside",
             "router static", "route 0.0.0.0/0 10.0.0.2",
             "route 2001:db8::/32 fe80::1", "exit"):
    sh.feed(line)
check(list(sh.candidate.pools) == ["residential"],
      f"a pool is made under nat: {list(sh.candidate.pools)}")
check(sh.candidate.interfaces == {"TenGigabitEthernet0/8/0": "inside"},
      f"and an interface attached there: {sh.candidate.interfaces}")
# What reaches FRR is what FRR understands. `router static` handed to it is
# refused mid-commit, so the block has to be translated and not passed through.
check("router static" not in sh.candidate.routing,
      f"the block never reaches FRR: {sh.candidate.routing}")
check("ip route 0.0.0.0/0 10.0.0.2" in sh.candidate.routing
      and "ipv6 route 2001:db8::/32 fe80::1" in sh.candidate.routing,
      f"the routes do, in FRR's own words: {sh.candidate.routing}")

# The old spellings still work, typed. Not because of politeness: a box in
# service has a saved configuration and up to twenty rollback revisions written
# in them, and hexarouting.service replays that file at every boot.
sh = box_193()
sh.feed("cgnat interface TenGigabitEthernet0/8/0 outside")
sh.feed("ip route 198.51.100.0/24 blackhole")
check(sh.candidate.interfaces == {"TenGigabitEthernet0/8/0": "outside"},
      f"the old CGNAT spelling still configures: {sh.candidate.interfaces}")
check("ip route 198.51.100.0/24 blackhole" in sh.candidate.routing,
      f"and so does the old route spelling: {sh.candidate.routing}")

# `exit` leaves the block, and the next statement lands outside it.
sh = box_193()
sh.feed("router static")
check(sh.frr_ctx == ["router static"], f"the block is entered: {sh.frr_ctx}")
check("router static" in sh.prompt(),
      f"and the prompt says which block: {sh.prompt()!r}")
sh.feed("exit")
check(sh.frr_ctx == [], f"and left: {sh.frr_ctx}")

# A statement that is not a route is REFUSED inside the block rather than
# passed to FRR, which would take it as a statement of a block it does not
# have and fail the whole commit with a message about a line nobody typed.
sh = box_193()
sh.feed("router static")
out = capture(sh, "neighbor 10.0.0.2 remote-as 65001")
check("not a statement of 'router static'" in out, f"refused: {out}")
check("neighbor 10.0.0.2 remote-as 65001" not in sh.candidate.routing,
      "and not staged")

# `nat` on its own is refused, with the statement that follows it. `cgnat` was
# never a block either - what is entered is `cgnat pool NAME` - and inventing
# one level of block here would make `exit` mean two different things
# depending on which word opened it.
sh = box_193()
out = capture(sh, "nat")
check("a hierarchy, not a block" in out, f"nat alone is refused: {out}")
check("nat cgnat pool" in out, "and says what to type instead")

# Inside another block the words belong to that block's grammar. A route-map
# or a description may begin with any word at all, and rewriting there would
# edit somebody's policy on the strength of its first token.
sh = box_193()
sh.feed("route-map NAT-OUT permit 10")
sh.feed("description nat cgnat pool residential")
check(any(l.strip() == "description nat cgnat pool residential"
          for l in sh.candidate.routing),
      f"a description that starts with 'nat' is left alone: {sh.candidate.routing}")



# ---------------------------------------------------------------------------
# Advice that names a file the daemon is not in (#197)
#
# The refusal for a daemon that is not running used to say "Enable <daemon> in
# /etc/frr/daemons" whichever daemon it was. frrinit.sh starts zebra, mgmtd and
# staticd itself, so those three are NOT lines in that file: an operator sent
# there finds nothing to enable, on a box where the real fault is that frr did
# not finish starting.
from hexash import enable_daemon_advice                           # noqa: E402

check("/etc/frr/daemons" in enable_daemon_advice("bgpd"),
      "an optional daemon is enabled in the file")
for always in ("zebra", "mgmtd", "staticd"):
    advice = enable_daemon_advice(always)
    check("not listed in /etc/frr/daemons" in advice,
          f"{always} is not a line in that file, and the advice says so: {advice}")
    check("systemctl restart frr" in advice,
          f"and says what to do instead: {advice}")



# ---------------------------------------------------------------------------
# Three things the documentation pass found in the product (#191, #193)

# A filter argument in quotes. Everybody types them - `| section "interface Gi"`
# is what a pipe looks like in every shell anybody has used - and this filter is
# not a shell, so nothing removed them: the quotes became part of the text being
# searched for and the filter matched nothing at all. Silently, which is the
# worst of it, because an empty result reads as "there is no such section".
_cfg = "interface Gi0/1\n mtu 1500\ninterface Gi0/2\n mtu 9000\n"
for _q in ('"interface Gi0/1"', "'interface Gi0/1'", "interface Gi0/1"):
    _lines, _why = apply_pipe(_cfg, [f"section {_q}"])
    check(_why is None and _lines == ["interface Gi0/1", " mtu 1500"],
          f"a quoted filter argument finds the same section: {_q} -> {_lines}")
# Only a matched pair, and only around the whole argument: a quote in the middle
# of a string is a character somebody is searching for.
_lines, _ = apply_pipe('say "hi" there\nsay hi there\n', ['include say "hi"'])
check(_lines == ['say "hi" there'],
      f"a quote inside the argument is searched for: {_lines}")
_lines, _ = apply_pipe('a"b\nab\n', ['include a"b'])
check(_lines == ['a"b'],
      f"and an unmatched one is not stripped: {_lines}")

# A refusal has to name something the operator can find. Typed inside `router
# static`, a route is translated to FRR's words before any check runs, so every
# refusal about one named a statement that appears nowhere in what was typed and
# nowhere in `show configuration` either.
from config import as_hierarchy                                   # noqa: E402
check(as_hierarchy("ip route 10.55.0.0/16 172.31.99.1")
      == "router static / route 10.55.0.0/16 172.31.99.1",
      f"a static route is named as the VTY spells it: "
      f"{as_hierarchy('ip route 10.55.0.0/16 172.31.99.1')}")
check(as_hierarchy("ipv6 route 2001:db8::/32 fe80::1")
      == "router static / route 2001:db8::/32 fe80::1",
      "and so is an IPv6 one")
check(as_hierarchy(" neighbor 10.0.0.2 remote-as 65001")
      == "neighbor 10.0.0.2 remote-as 65001",
      "a statement the VTY does not rename is left alone")

# The box must not go on teaching the spelling it moved away from. A `usage:`
# line is an instruction, which is exactly the case #193 is about.
import hexash as _hx                                              # noqa: E402
_src = open(_hx.__file__).read()
check("usage: cgnat " not in _src,
      "no usage line still teaches the old top-level spelling")

print("\n=== the IGPs are in the tree, which is where they were not ===")
# `show ?` offered bgp and isis and not OSPF, and both IGP entries ended the
# command: after `show ospf` the operator was told nothing, on a VTY whose
# reason for existing is that they do not have to know FRR is underneath.


def offered(line):
    """Options at `line`, in operational mode. `opts` above is long rebound."""
    sh = hexash.Shell()
    sh.mode = "operational"
    o, hint, _ = sh._options(line, False)
    return set(o), hint


check({"ospf", "isis", "bgp", "route"} <= offered("show ")[0],
      f"missing: {sorted(offered('show ')[0])}")
check(offered("show ospf ")[0] == {"neighbor", "interface"},
      f"show ospf offers: {sorted(offered('show ospf ')[0])}")
check(offered("show isis ")[0] == {"adjacency", "interface"},
      f"show isis offers: {sorted(offered('show isis ')[0])}")

print("\n=== clear reaches the IGPs too ===")
check(offered("clear ")[0] == {"bgp", "ospf", "isis"},
      f"clear offers: {sorted(offered('clear ')[0])}")
check(offered("clear ospf ")[0] == {"process", "neighbor"},
      f"clear ospf offers: {sorted(offered('clear ospf ')[0])}")
o, hint = offered("clear isis adjacency ")
check(not o and hint == "[<system-id>]", f"a system id is typed: {o} {hint!r}")


print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
