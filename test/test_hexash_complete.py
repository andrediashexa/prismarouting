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

    # reachable() too, because the real Vpp has it and a stub that does not
    # mirror the real class is a stub that passes a test the product fails.
    # It answers through run(), the way the real one does.
    sh.dp = type("D", (), {"TRACE": False, "run": run,
                           "reachable": lambda _s: run(_s, "show version")[0] == 0})()
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

# --- show this, for what is being typed right now (#180) ---
#
# The routing half of the candidate is a flat list of verbatim FRR statements
# with no record of which block each one is in, and it arrives in two shapes:
# read back from FRR it is INDENTED, and typed at this VTY it is not, because
# `feed` strips leading whitespace before dispatch. `show this` walked it by
# indentation and so stopped at the first line the operator had typed - which
# is every line, in the one situation the command exists for. An operator who
# types four statements, runs it and sees only the header will reasonably
# conclude nothing was accepted.
#
# Every block the VTY models is checked, one at a time and against the block's
# OWN line, because a needle looked for in the whole output is satisfied by
# the header the command already printed.
print("\n=== show this shows what this session typed (#180) ===")

def this_in(block, *typed):
    """`show this` inside `block` after `typed`, as a list of lines."""
    s = shell_with_box()
    s.feed("configure")
    s.feed(block)
    for line in typed:
        s.feed(line)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.feed("show this")
    return buf.getvalue().splitlines()

# Each case: the block, what is typed in it, and the line that MUST come back.
# The expected line carries its own leading space, so a statement shown
# unindented - or the header matching by accident - is not a pass.
TYPED = [
    ("router bgp 65001",
     ["neighbor 10.0.0.2 remote-as 65002", "no bgp ebgp-requires-policy"],
     [" neighbor 10.0.0.2 remote-as 65002", " no bgp ebgp-requires-policy"]),
    ("router ospf", ["network 10.0.0.0/24 area 0", "passive-interface default"],
     [" network 10.0.0.0/24 area 0", " passive-interface default"]),
    ("router isis TAG", ["is-type level-2-only", "net 49.0001.0000.0000.0001.00"],
     [" is-type level-2-only", " net 49.0001.0000.0000.0001.00"]),
    ("route-map RM-LAB permit 10",
     ["match ip address prefix-list PL-A", "set local-preference 200"],
     [" match ip address prefix-list PL-A", " set local-preference 200"]),
    ("rpki", ["rpki cache 10.0.0.9 3323 preference 1"],
     [" rpki cache 10.0.0.9 3323 preference 1"]),
    ("vrf red", ["ip route 10.9.0.0/16 10.0.0.9"],
     [" ip route 10.9.0.0/16 10.0.0.9"]),
    # In the BLOCK's own words, not FRR's. `router static` is a hierarchy FRR
    # does not have - its statements are top-level `ip route` lines - and the
    # operator is being shown the hierarchy they are editing.
    ("router static", ["route 0.0.0.0/0 10.0.0.2", "route 192.0.2.0/24 10.0.0.3"],
     [" route 0.0.0.0/0 10.0.0.2", " route 192.0.2.0/24 10.0.0.3"]),
]
for block, typed, expected in TYPED:
    lines = this_in(block, *typed)
    check(lines[:1] == [block], f"{block}: the header is still first: {lines}")
    for want in expected:
        check(want in lines[1:],
              f"{block}: '{want.strip()}' is shown as a statement of it: {lines}")
    print(f"  {block}: " + " / ".join(l.strip() for l in lines[1:]))

# FRR's spelling typed inside the block is shown in the block's words too: it
# is the same route either way, and showing it twice over would be a lie about
# what commits.
lines = this_in("router static", "ip route 198.51.100.0/24 blackhole")
check(" route 198.51.100.0/24 blackhole" in lines,
      f"FRR's own spelling comes back in the block's words: {lines}")
check(not any(l.strip().startswith("ip route") for l in lines),
      f"and not in FRR's as well: {lines}")

# A nested block. Standing in the address-family shows its statements; standing
# in the router above shows them too, one level deeper, because an operator
# asking what `router bgp 65001` holds is asking about its families as well.
sh = shell_with_box()
sh.feed("configure")
sh.feed("router bgp 65001")
sh.feed("neighbor 10.0.0.2 remote-as 65002")
sh.feed("address-family ipv4 unicast")
sh.feed("neighbor 10.0.0.2 activate")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.feed("show this")
af = buf.getvalue().splitlines()
check(af[:1] == ["address-family ipv4 unicast"], f"the family is named: {af}")
check(" neighbor 10.0.0.2 activate" in af[1:],
      f"and holds what was typed in it: {af}")
check(not any("remote-as 65002" in l for l in af),
      f"and nothing from the router above it, at any depth: {af}")
sh.feed("exit")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.feed("show this")
bgp = buf.getvalue().splitlines()
check(" neighbor 10.0.0.2 remote-as 65002" in bgp,
      f"the router shows its own statement: {bgp}")
check(" address-family ipv4 unicast" in bgp,
      f"and the family under it: {bgp}")
check("  neighbor 10.0.0.2 activate" in bgp,
      f"whose statements come one level deeper: {bgp}")
print("  nesting: " + " / ".join(l.strip() for l in bgp[1:]))

# The two shapes in ONE candidate, which is what every real edit is: the
# configuration was read back from FRR indented, and the operator has just
# typed a line at the VTY. Both belong to the block and both must show.
sh = shell_with_box()
sh.frr.running = lambda: ["router bgp 65001",
                          " neighbor 10.0.0.1 remote-as 65000",
                          " exit",
                          "!"]
sh.feed("configure")
sh.feed("router bgp 65001")
sh.feed("neighbor 10.0.0.2 remote-as 65002")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.feed("show this")
mixed = buf.getvalue().splitlines()
check(" neighbor 10.0.0.1 remote-as 65000" in mixed,
      f"what FRR already had is shown: {mixed}")
check(" neighbor 10.0.0.2 remote-as 65002" in mixed,
      f"and what was typed a moment ago, in the same block: {mixed}")
# `exit` ends a block; it is not a statement OF it, and printing it invites
# somebody to copy it back in as configuration.
check(not any(l.strip() in ("exit", "!") for l in mixed),
      f"a block's own exit is not shown as configuration: {mixed}")
print("  mixed: " + " / ".join(l.strip() for l in mixed[1:]))

# Nothing from a neighbouring block, and everything from this one - a block
# left and re-entered is one hierarchy, not two. The header alone is a match
# for any substring test, so this asserts on the statements.
sh = shell_with_box()
sh.feed("configure")
sh.feed("router bgp 65001")
sh.feed("neighbor 10.0.0.2 remote-as 65002")
sh.feed("exit")
sh.feed("router ospf")
sh.feed("network 10.0.0.0/24 area 0")
sh.feed("exit")
sh.feed("router bgp 65001")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.feed("show this")
only = buf.getvalue().splitlines()
check(" neighbor 10.0.0.2 remote-as 65002" in only,
      f"what BGP was given earlier in the session is still its own: {only}")
check(not any("network 10.0.0.0/24 area 0" in l for l in only),
      f"and OSPF's, typed between the two visits, is not: {only}")

# The same without an `exit`, which is what an operator who knows FRR types:
# naming another block is how you leave the one you are in. Asserted from BOTH
# sides, because a walk that nests one block inside the other gets one of them
# right whichever way round it is wrong.
sh = shell_with_box()
sh.feed("configure")
sh.feed("router ospf")
sh.feed("network 10.0.0.0/24 area 0")
sh.feed("router bgp 65001")
sh.feed("neighbor 10.0.0.2 remote-as 65002")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.feed("show this")
noexit = buf.getvalue().splitlines()
check(" neighbor 10.0.0.2 remote-as 65002" in noexit,
      f"BGP holds what was typed after its header: {noexit}")
check(not any("network 10.0.0.0/24 area 0" in l for l in noexit),
      f"and OSPF, named before it, is not inside it: {noexit}")
sh.feed("router ospf")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.feed("show this")
back = buf.getvalue().splitlines()
check(" network 10.0.0.0/24 area 0" in back,
      f"and OSPF still holds its own: {back}")
check(not any("neighbor 10.0.0.2 remote-as 65002" in l for l in back),
      f"with BGP, named inside it, not swallowed by it: {back}")

# A secret typed in this session reaches this path too - it did not before,
# because the path stopped at the header and printed nothing at all.
sh = shell_with_box()
sh.feed("configure")
sh.feed("router bgp 65001")
sh.feed("neighbor 10.0.0.2 password S3CR3T-BGP")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.feed("show this")
secret = buf.getvalue()
check("S3CR3T-BGP" not in secret, f"show this prints no typed password: {secret}")
check(" neighbor 10.0.0.2 password <set, not shown>" in secret.splitlines(),
      f"and says one is set: {secret}")

# An interface holds eleven things and `show this` knew about two of them, so
# an operator who had just typed a description was told the block had none.
# `Config.interface_lines` is the one list, shared with the file writer.
sh = shell_with_box()
sh.feed("configure")
sh.feed("interface TenGigabitEthernet0/8/0")
sh.feed("description transito-vivo")
sh.feed("mtu 9000")
sh.feed("ip ospf area 0")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.feed("show this")
iface = buf.getvalue().splitlines()
check(" description transito-vivo" in iface,
      f"a description typed in this session is shown: {iface}")
check(" mtu 9000" in iface, f"beside the MTU that #108 added: {iface}")
check(" ip ospf area 0" in iface,
      f"and the FRR statement typed in the same block: {iface}")
print("  interface: " + " / ".join(l.strip() for l in iface[1:]))

# Leaving a block has to leave a mark in the candidate. The header went in on
# the way in and nothing recorded the way out, so the flat list said the block
# was still open - and `frr-reload.py`, which decides context by statement
# exactly as `block_paths` does, then handed the next top-level statement to
# FRR INSIDE the block just left.
sh = shell_with_box()
sh.feed("configure")
sh.feed("router bgp 65001")
sh.feed("neighbor 10.0.0.2 remote-as 65002")
sh.feed("exit")
check("exit" in sh.candidate.routing,
      f"leaving a block is recorded in the candidate: {sh.candidate.routing}")
sh.feed("router static")
sh.feed("route 0.0.0.0/0 10.0.0.2")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.feed("show this")
after = buf.getvalue().splitlines()
check(" route 0.0.0.0/0 10.0.0.2" in after,
      f"a route typed after leaving a block is in the static block: {after}")
# And not only on the screen: the file the box would save has to put it there
# too, or `show this` and the commit are two answers to one question.
check(" route 0.0.0.0/0 10.0.0.2" in sh.candidate.text().splitlines(),
      f"and the saved file agrees: {sh.candidate.text()}")
# ...and it is not routing an operator wrote, so it must not make a candidate
# that only sets an interface MTU look like one that needs FRR (#137).
check(hexash.Shell.real_routing(["interface Gi0", "exit"]) == [],
      "a block terminator is not a routing statement")

# A block entered by argument is a TOP-LEVEL context in FRR - only an
# address-family nests - so the prompt must not claim a nesting that would
# leave `show this` looking for a block nothing holds.
sh = shell_with_box()
sh.feed("configure")
sh.feed("router bgp 65001")
sh.feed("interface TenGigabitEthernet0/8/0")
check(sh.frr_ctx == ["interface TenGigabitEthernet0/8/0"],
      f"an interface entered from inside BGP is not nested in it: {sh.frr_ctx}")
sh.feed("mtu 9000")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.feed("show this")
check(" mtu 9000" in buf.getvalue().splitlines(),
      f"and shows what it holds: {buf.getvalue()!r}")

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


# --- BGP unnumbered (#208) ---
#
# `neighbor <ifname> interface remote-as external` is the ordinary way to run
# eBGP to a directly connected peer without spending a /30 on the link. It was
# refused with "has no 'remote-as'" - the AS is on that same line - and the
# refusal failed the WHOLE commit, so a CGNAT change typed beside it was
# refused with it.
#
# Everything asserted here about FRR was measured on FRR 10.7.1 and is
# re-measured on a live one by lab/bgp-policy.sh. lab/routing.sh puts the
# statement on a real linux_cp pair and records how far the session gets: the
# far end learns this box and opens, and this end never learns the far end,
# because the dataplane does not punt the Router Advertisement that carries
# its address. That is a defect of its own and it is written down there.
print("\n=== BGP unnumbered is a configuration, not a mistake ===")

# lab/routing.sh's own pair, which is the topology the lab half of this runs
# on: three names for one interface, and only vppwan0 is a Linux netdev.
LCP = {n: ["host-veth-vpp", "tap4096", "vppwan0"]
       for n in ("host-veth-vpp", "tap4096", "vppwan0")}
DPNAMES = {"host-veth-vpp", "tap4096"}
HAVE = {"host-veth-vpp", "tap4096", "vppwan0"}

for form in ("neighbor vppwan0 interface remote-as external",
             "neighbor vppwan0 interface remote-as internal",
             "neighbor vppwan0 interface remote-as 65001",
             "neighbor vppwan0 interface v6only remote-as external"):
    check(not bgp_errors(["router bgp 65000", " " + form],
                         HAVE, LCP, DPNAMES),
          f"accepted: {form}")
check(not bgp_errors(["router bgp 65000",
                      " neighbor UP peer-group",
                      " neighbor UP remote-as external",
                      " neighbor vppwan0 interface peer-group UP"],
                     HAVE, LCP, DPNAMES),
      "and the peer-group spelling of the same form")
# The AS given as a statement of its own, which is what an operator types
# line by line - FRR folds the two into one line on the way back out.
check(not bgp_errors(["router bgp 65000",
                      " neighbor vppwan0 interface",
                      " neighbor vppwan0 remote-as external"],
                     HAVE, LCP, DPNAMES),
      "the split spelling, which FRR stores as the single line")

# The half of the check that was always right, and must not be lost: FRR
# itself answers the address form with "% Specify remote-as or peer-group
# commands first", so this is the refusal arriving earlier.
addr = bgp_errors(["router bgp 65000", " neighbor 10.0.0.9 description x"])
check(len(addr) == 1 and addr[0][0] == "error",
      f"an address-form neighbour with no remote-as is still refused: {addr}")
# `description` takes free text, so the AS keyword may not be read out of it.
check(len(bgp_errors(["router bgp 65000",
                      " neighbor 10.0.0.9 description remote-as 65001"])) == 1,
      "a description that CONTAINS 'remote-as' is not an AS")
# Measured: FRR accepts `neighbor X interface` alone - it refuses only the
# address form - and leaves the peer in `show bgp summary` at AS 0, Idle,
# forever. Silent, so this says it.
bare = bgp_errors(["router bgp 65000", " neighbor vppwan0 interface"],
                  HAVE, LCP, DPNAMES)
check(len(bare) == 1 and bare[0][0] == "error" and "Idle" in bare[0][2],
      f"an interface peer with no AS anywhere is refused, saying why: {bare}")

# The interface name, held to #205's rule: it is an FRR statement, so it must
# be a spelling FRR can act on. FRR accepts an interface it does not have and
# never says so - measured, `neighbor nosuchif0 interface remote-as external`
# commits and sits Idle - which makes this a diagnosis only this box can make.
gone = bgp_errors(["router bgp 65000",
                   " neighbor nosuchif0 interface remote-as external"],
                  HAVE, LCP, DPNAMES)
check(len(gone) == 1 and gone[0][0] == "error"
      and "no interface nosuchif0" in gone[0][2],
      f"an unnumbered session on an interface the box has not got: {gone}")
dpname = bgp_errors(["router bgp 65000",
                     " neighbor host-veth-vpp interface remote-as external"],
                    HAVE, LCP, DPNAMES)
check(len(dpname) == 1 and dpname[0][0] == "warning"
      and "vppwan0" in dpname[0][2],
      f"the dataplane's end of the pair is warned about, not refused, and "
      f"names the one FRR wants: {dpname}")
# "Could not ask" must not become a refusal. With no interface list read off
# the box - which is what a dataplane that did not answer looks like - the
# name is taken on trust and only the AS checks run.
check(not bgp_errors(["router bgp 65000",
                      " neighbor nosuchif0 interface remote-as external"]),
      "with no interface list to check against, the name is taken on trust")


# --- a policy list that is not defined YET (#208) ---
#
# Measured on FRR 10.7.1: `match community NOTYET` against a list that does
# not exist is ACCEPTED, stored, and bound when the list arrives - `show
# route-map` prints the match clause both before and after, with nothing
# retyped. Refusing it made the map-first order of building a policy
# impossible, and refused every unrelated change in the same commit.
print("\n=== a policy list that is not defined yet is said, not refused ===")
late = undefined_policy_lists(["route-map R permit 10",
                               " match community CM-LATER"])
check(len(late) == 1, f"the clause is still reported: {late}")
check("until one is" in late[0][1],
      f"and the wording says it is a state, not a fault: {late[0][1]}")
# The true positive underneath, which is why this is narrowed and not
# withdrawn: a name with a typo in it matches nothing forever, and nothing
# else on the box ever mentions it.
check(len(undefined_policy_lists(
    ["bgp community-list standard CM1 seq 5 permit 65000:1",
     "route-map R permit 10", " match community CM1x"])) == 1,
    "a typo next to the list it was meant to name is still caught")
# Order within the candidate was never the question: the definitions are
# collected in a pass of their own, so a map written above its lists is
# clean. Asserted because the fix could plausibly have been "sort it first",
# and that would have fixed nothing.
check(not undefined_policy_lists(
    ["route-map R permit 10", " match as-path AS1",
     "bgp as-path access-list AS1 seq 5 permit ^65001$"]),
    "a route-map written above the list it names is not a finding")
# FRR 10.7.1 rewrites every one of these on the way out - `seq 5` inserted,
# `standard` put back - and cfg.routing is read back from FRR, so the
# rewritten spelling is the one every commit after the first one sees.
for defn, m in (("bgp as-path access-list AS1 seq 5 permit ^65001$",
                 "match as-path AS1"),
                ("bgp community-list standard CM1 seq 5 permit 65000:1",
                 "match community CM1"),
                ("bgp community-list 100 seq 5 permit 65000:3",
                 "match community 100"),
                ("bgp large-community-list standard LC1 seq 5 permit 1:1:1",
                 "match large-community LC1")):
    check(not undefined_policy_lists([defn, "route-map R permit 10", " " + m]),
          f"FRR's own spelling counts as a definition: {defn}")


# --- route-map clauses run in FRR's order, which is `seq` (#208) ---
#
# Measured: `route-map ORD permit 20` typed first and `route-map ORD permit
# 10` second, and FRR's `show route-map ORD` lists 10 before 20. Reading the
# written order called clause 10 unreachable - and inserting a low-seq clause
# in front of a catch-all is the ordinary way to add to a policy, which is
# what sequence numbers are for.
print("\n=== route-map clauses are ordered by seq, not by line ===")
from hexash import route_map_findings                            # noqa: E402

inserted = route_map_findings(["route-map R permit 20",
                               " set local-preference 200",
                               "route-map R permit 10",
                               " match ip address prefix-list P",
                               " set local-preference 300"])
check(not [f for f in inserted if "unreachable" in f[2]],
      f"a low-seq clause written after a catch-all is reached: {inserted}")
check(len(inserted) == 1 and "permits every route" in inserted[1 - 1][2],
      f"and the catch-all itself is still called out: {inserted}")
# The real one, which must survive the reordering.
after = route_map_findings(["route-map R permit 10",
                            "route-map R permit 20", " match as-path AS1"])
check([f for f in after if "unreachable" in f[2]],
      f"a clause after a catch-all BY SEQ is still unreachable: {after}")

# `on-match next` and the `continue` an operator types for it were parsed and
# then ignored, so a catch-all carrying one poisoned every later clause.
# Measured: FRR stores `continue 30` as `on-match goto 30`, and `show
# route-map` prints "Continue to next entry" for `on-match next`.
steered = route_map_findings(["route-map S permit 10",
                              " set local-preference 200",
                              " on-match next",
                              "route-map S permit 20",
                              " match as-path AS1",
                              " set community 65000:1"])
check(not [f for f in steered if "unreachable" in f[2]],
      f"a catch-all that continues does not end the policy: {steered}")
for word in ("on-match goto 30", "continue 30"):
    s = route_map_findings(["route-map S permit 10", " " + word,
                            "route-map S permit 20", " match as-path AS1"])
    check(not [f for f in s if "unreachable" in f[2]],
          f"both spellings of the same statement are honoured: {word} {s}")
# `continue` ENDED the clause, because it was not one of the words the parser
# knew - so the `set` after it belonged to nothing and a deny clause carrying
# one was not refused. The false negative the same neglect created.
cont = route_map_findings(["route-map C deny 10", " match as-path AS1",
                           " continue 30", " set local-preference 150"])
check([f for f in cont if f[0] == "error"],
      f"a set after a 'continue' still belongs to its clause: {cont}")


# --- no input may put a traceback in front of an operator (#208) ---
#
# `bgp community-list standard`, with the name not typed yet, raised
# IndexError: a Python traceback in a maintenance window is not a refusal, it
# is a crash. Three hand-picked strings would not have found it and will not
# find the next one, so every statement these functions parse is truncated at
# every word AND every character, in every context, and fed back in.
print("\n=== every truncation of every statement, and no traceback ===")
from hexash import (as_path_parts, as_path_regex_error,          # noqa: E402
                    list_name, prefix_range_error, rpki_findings,
                    isis_findings, ospf_state)

FUZZ_CORPUS = [
    "router bgp 65000", "neighbor 10.0.0.2 remote-as 65001",
    "neighbor 10.0.0.2 peer-group UP", "neighbor UP peer-group",
    "neighbor vppwan0 interface remote-as external",
    "neighbor vppwan0 interface v6only peer-group UP",
    "neighbor 10.0.0.2 activate", "no neighbor 10.0.0.2 remote-as 65001",
    "address-family ipv4 unicast", "exit-address-family", "exit",
    "bgp as-path access-list AS1 seq 5 permit ^65001$",
    "bgp community-list standard CM1 seq 5 permit 65000:1",
    "bgp community-list 100 seq 5 permit 65000:3",
    "bgp large-community-list standard LC1 seq 5 permit 65000:1:1",
    "route-map R permit 10", "route-map R deny 20",
    "match as-path AS1", "match community CM1", "match large-community LC1",
    "match ip address prefix-list PL", "match rpki notfound",
    "set local-preference 200", "set community 65000:1",
    "on-match next", "on-match goto 30", "continue 30", "call OTHER",
    "description upstream", "rpki cache 10.0.0.9 3323 preference 1",
    "ip prefix-list PL seq 5 permit 10.0.0.0/8 ge 24 le 16",
    "router ospf", "network 10.0.0.0/8 area 0", "passive-interface vppwan0",
    "no passive-interface vppwan0", "redistribute bgp route-map R",
    "interface vppwan0", "ip ospf area 0", "ip ospf passive",
    "router isis 1", "net 49.0001.0000.0000.0001.00",
    "topology ipv6-unicast", "ipv6 router isis 1",
]
# The context matters as much as the line: these functions all track which
# block they are in, so a fragment has to be tried inside each of them.
FUZZ_CONTEXT = [[], ["router bgp 65000"], ["route-map R permit 10"],
                ["router ospf"], ["router isis 1"], ["interface vppwan0"]]
MANY = [lambda r: hexash.bgp_errors(r, HAVE, LCP, DPNAMES),
        hexash.bgp_errors, undefined_policy_lists, route_map_findings,
        rpki_findings, isis_findings, ospf_state]
ONE = [as_path_parts, as_path_regex_error, prefix_range_error]

tried, crashed = 0, []
for stmt in FUZZ_CORPUS:
    frags = set()
    words = stmt.split()
    for k in range(len(words) + 1):          # truncated at a word boundary
        frags.add(" ".join(words[:k]))
    for k in range(len(stmt) + 1):           # and in the middle of one
        frags.add(stmt[:k])
        frags.add(stmt[k:])
    for frag in frags:
        # Indented as FRR gives it back, unindented as it is typed, and with
        # the trailing space a half-typed line carries.
        for line in (frag, " " + frag, frag + " "):
            for ctx in FUZZ_CONTEXT:
                for f in MANY:
                    tried += 1
                    try:
                        f(ctx + [line])
                    except Exception as e:
                        crashed.append((f, line, repr(e)))
            for f in ONE:
                tried += 1
                try:
                    f(line)
                except Exception as e:
                    crashed.append((f, line, repr(e)))
            tried += 1
            try:
                list_name(line.split())
            except Exception as e:
                crashed.append((list_name, line, repr(e)))
check(tried > 100000, f"the fuzz is a fuzz: {tried} inputs")
check(not crashed,
      f"{len(crashed)} of {tried} truncations raised: {crashed[:5]}")
print(f"  {tried} truncated inputs, {len(crashed)} tracebacks")
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
# A virtual link IS the backbone attachment, and RFC 2328 section 15 exists
# for precisely the box with no physical area-0 interface. Warning there
# describes a working design back to the operator who had to think hardest
# about it.
check(not find(["router ospf", "network 200.150.0.0/24 area 1",
                "network 100.64.0.0/22 area 2",
                "area 1 virtual-link 10.255.0.9"]),
      "nor is an area reached through a virtual link")

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

# The same check on a box that has linux_cp, which is every box that routes.
# A pair answers to three names, only one of them is FRR's, and the two halves
# of this check arrive in different ones: the addresses are keyed by the
# DATAPLANE's names and `passive-interface` has to carry FRR's (#205).
#
# The names and the behaviour are the labs' own, measured rather than
# invented, and lab/igp.sh now asserts the same three cases against a real
# FRR over a real pair. `show lcp` prints
#
#   itf-pair: [0] host-veth-vpp tap4096 vppwan0 5 type tap
#
# only vppwan0 is a Linux netdev, FRR's interface list holds vppwan0 and
# neither of the others, and `passive-interface host-veth-vpp` is accepted
# with a deprecation notice while `show ip ospf interface vppwan0` goes on
# saying nothing about hellos.
LCP = ["host-veth-vpp", "tap4096", "vppwan0"]
PAIRS = {n: LCP for n in LCP}
DPIF = {"host-veth-vpp", "tap4096"}
LCP_HAVE = set(LCP) | DPIF
LCP_ADDRS = {"host-veth-vpp": ["100.64.0.1/22"]}
FACING = ["router ospf", "network 100.64.0.0/22 area 0"]


def lcp_find(lines, addrs=None):
    return ospf_findings(lines, addrs or LCP_ADDRS, LCP_HAVE, POOLS,
                         PAIRS, DPIF)


check(any("facing subscribers" in w for _, _, w in lcp_find(FACING)),
      "a subscriber-facing pair speaking OSPF is reported")
# The false positive: the ONLY spelling FRR acts on used to silence nothing,
# so the operator who did the right thing was told off for it.
check(not lcp_find(FACING + ["passive-interface vppwan0"]),
      f"and the spelling FRR acts on silences it: "
      f"{lcp_find(FACING + ['passive-interface vppwan0'])}")
# Its mirror, and the reason the false positive was not merely noise: the
# spelling FRR ignores used to silence it, so the operator who typed a name
# that does nothing was reassured.
r = lcp_find(FACING + ["passive-interface host-veth-vpp"])
check(any("facing subscribers" in w for _, _, w in r),
      f"while the dataplane's name silences nothing: {r}")
check(any("nothing is made passive" in w for _, _, w in r),
      f"and the statement that will not work is named: {r}")
# The false negative next door. `ip ospf area` sits in an FRR stanza and so
# is spelled FRR's way; matched as a string against a dataplane-keyed address
# dictionary it never matched anything, and this warning could not fire
# through that door at all.
r = lcp_find(["interface vppwan0", "ip ospf area 0"])
check(any("facing subscribers" in w for _, _, w in r),
      f"an area set on the interface reaches the same check: {r}")
check(not lcp_find(["interface vppwan0", "ip ospf area 0",
                    "router ospf", "passive-interface vppwan0"]),
      "and the same statement silences it")
# The other keying, which is why neither side can simply be translated: a
# staged `interface` block puts whatever the operator typed into the address
# dictionary, and docs/routing.md says either name is correct there.
check(not lcp_find(FACING + ["passive-interface vppwan0"],
                   addrs={"vppwan0": ["100.64.0.1/22"]}),
      "addresses keyed the other way behave the same")
check(any("facing subscribers" in w for _, _, w in
          lcp_find(FACING, addrs={"vppwan0": ["100.64.0.1/22"]})),
      "and still report the port that is not passive")
# Could not ask. With no dataplane list the members of a pair cannot be told
# apart, and "cannot tell" must not become a warning nobody can silence.
check(not ospf_findings(FACING + ["passive-interface host-veth-vpp"],
                        LCP_ADDRS, LCP_HAVE, POOLS, PAIRS),
      "and with no dataplane names to subtract, nothing is claimed")

# The same statement after a round trip through FRR, which is where every
# commit but the first one reads it from: cfg.routing comes back from
# `show running-config`, and FRR 10 does not store `passive-interface` -
# measured, it answers "This command is deprecated" and files the statement
# under the interface instead, exactly like this.
ROUNDTRIP = ["router ospf", "network 100.64.0.0/22 area 0",
             "interface vppwan0", " ip ospf passive"]
check(not lcp_find(ROUNDTRIP),
      f"the spelling FRR stores silences it too: {lcp_find(ROUNDTRIP)}")
# And it is not a way to invent an OSPF instance: a box with only this line
# runs no OSPF, and nothing else in here may fire on it.
check(not find(["interface Gi1", " ip ospf passive"], pools=POOLS),
      "while on its own it is not an OSPF instance")

# `passive-interface default`, which is the posture the help text for this
# very statement recommends for a subscriber-facing port. It is a statement
# about every interface, not the name of one, and reading it as a name meant
# this warning fired on the box that had got it exactly right.
DEFAULT_QUIET = FACING + ["passive-interface default"]
check(not lcp_find(DEFAULT_QUIET),
      f"passive-interface default silences it: {lcp_find(DEFAULT_QUIET)}")
# And `no passive-interface <name>` is how an interface is taken back out of
# it, so the warning has to come back for the one that was.
r = lcp_find(DEFAULT_QUIET + ["no passive-interface vppwan0"])
check(any("facing subscribers" in w for _, _, w in r),
      f"and taking one back out brings it back: {r}")
# ... only in a spelling FRR acts on. `no passive-interface host-veth-vpp`
# takes nothing back out, so the port is still passive and still quiet.
check(not lcp_find(DEFAULT_QUIET + ["no passive-interface host-veth-vpp"]),
      "while a spelling FRR ignores takes nothing back out")

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

# Single-topology IPv6 IS-IS is a CORRECT configuration and nothing is said
# about it (#198). `topology ipv6-unicast` turns on Multi-Topology IS-IS (RFC
# 5120); without it IPv6 floods in the one legacy topology and forwards, which
# is what lab/igp.sh commits and then watches reach the VPP IPv6 FIB. The
# warning that used to be here fired on that lab on every run, and on every
# unrelated commit after it.
v6 = ["router isis HEXA", "net 49.0001.0000.0000.0001.00",
      "interface Gi0", "ipv6 router isis HEXA"]
check(not isis_findings(v6),
      f"single-topology IPv6 is not warned about: {isis_findings(v6)}")
check(not isis_findings(v6[:2] + ["topology ipv6-unicast"] + v6[2:]),
      "and neither is multi-topology")

# Narrow metrics cap at 63 - the classic silent limitation on a 10G box.
narrow = GOOD[:2] + ["interface Gi0", "ip router isis HEXA", "isis metric 100"]
check(any("metric-style wide" in w for _, _, w in isis_findings(narrow)),
      "a metric above 63 with narrow encoding is reported")
check(not isis_findings(GOOD + ["isis metric 100"]),
      "and with wide it is not")
# Against the instance that CARRIES the port, not against every instance on
# the box. A metric a wide instance expresses perfectly is not evidence about
# a narrow one that never touches it, and the sentence named the wrong tag.
two = ["router isis CORE", "net 49.0001.0000.0000.0001.00",
       "metric-style wide",
       "router isis EDGE", "net 49.0002.0000.0000.0001.00",
       "interface Gi0", "ip router isis CORE", "isis metric 1000",
       "interface Gi1", "ip router isis EDGE", "isis metric 10"]
check(not isis_findings(two),
      f"a wide instance's metric is not charged to a narrow one: "
      f"{isis_findings(two)}")

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

    # reachable() too, because the real Vpp has it and a stub that does not
    # mirror the real class is a stub that passes a test the product fails.
    # It answers through run(), the way the real one does.
    sh.dp = type("D", (), {"TRACE": False, "run": run,
                           "reachable": lambda _s: run(_s, "show version")[0] == 0})()
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



# ---------------------------------------------------------------------------
# "nothing took": the decidable half of #197
#
# The general check - is every statement of this candidate in effect - cannot be
# built on frr-reload, and that was MEASURED rather than reasoned about. A
# candidate carrying `timers throttle spf 0 50 5000` applies cleanly, FRR
# declines to store it because it equals the default, and frr-reload then
# reports it under "Lines To Add" for ever. What is decidable is the worst case:
# every statement this commit introduced is still outstanding, so nothing took.

# frr-reload's own report, copied off a run rather than written here: the
# heading, the row of '=', and a nested statement indented while the same
# statement typed at this VTY is not.
FRR_REPORT = """
Lines To Add
============
router ospf
 timers throttle spf 0 50 5000
exit
"""
check(hexash.Frr.outstanding(FRR_REPORT)
      == ["router ospf", "timers throttle spf 0 50 5000", "exit"],
      f"the report is read stripped: {hexash.Frr.outstanding(FRR_REPORT)}")
check(hexash.Frr.outstanding("") == [], "no report is nothing outstanding")
# The other section must not be read as this one. A statement being REMOVED is
# not a statement that failed to apply, and counting it as one would report a
# successful removal as a failed commit.
check(hexash.Frr.outstanding(
        "Lines To Delete\n===============\nno router bgp 65000\n") == [],
      "the delete section is not the add section")
check(hexash.Frr.outstanding(
        "Lines To Delete\n====\nno router ospf\n\nLines To Add\n====\nip route 1.2.3.0/24 blackhole\n")
      == ["ip route 1.2.3.0/24 blackhole"],
      "and a report carrying both reads only the adds")


def box_197(outstanding_text, rc=0):
    sh = shell_with_box()
    sh.candidate = hexash.Config()
    sh.frr = type("F", (), {
        "available": lambda s: True,
        "running": lambda s: [],
        "show": lambda s, c: (1, ""),
        "reload": lambda s, lines, check_only=False: (rc, outstanding_text),
        "outstanding": staticmethod(hexash.Frr.outstanding),
    })()
    return sh


ADDED = ["ip route 10.0.0.0/8 1.2.3.4", "ip route 10.1.0.0/16 blackhole"]
REPORT_BOTH = ("Lines To Add\n====\nip route 10.0.0.0/8 1.2.3.4\n"
               "ip route 10.1.0.0/16 blackhole\n")
REPORT_ONE = "Lines To Add\n====\nip route 10.1.0.0/16 blackhole\n"

sh = box_197(REPORT_BOTH)
check(sh.nothing_took(ADDED) == ADDED,
      "every new statement still outstanding is 'nothing took'")
# The half that keeps this usable. Nine statements of ten is a PARTIAL failure
# and this check does not fire on it - a check that cried wolf on every commit
# with one default-valued statement in it would be turned off within a week.
sh = box_197(REPORT_ONE)
check(sh.nothing_took(ADDED) == [],
      f"one of two outstanding is not: {sh.nothing_took(ADDED)}")
sh = box_197("")
check(sh.nothing_took(ADDED) == [], "an empty report is a clean apply")
# A check that cannot run must not become a failure of the commit it was
# checking.
sh = box_197(REPORT_BOTH, rc=1)
check(sh.nothing_took(ADDED) == [],
      "frr-reload refusing to answer is not a verdict")
check(sh.nothing_took([]) == [], "a commit that adds no routing is not checked")

# What counts as "a statement this commit introduced". A block header and a
# terminator are structure: they appear in frr-reload's report as context for
# what is nested under them, and counting them would let a block header decide
# that a commit failed.
sh = box_197("")
check(sh.routing_added(["router bgp 65000"],
                       ["router bgp 65000", " neighbor 10.0.0.2 remote-as 65001",
                        " exit", "!"])
      == ["neighbor 10.0.0.2 remote-as 65001"],
      f"only the new statement: {sh.routing_added(['router bgp 65000'], ['router bgp 65000', ' neighbor 10.0.0.2 remote-as 65001', ' exit', '!'])}")
check(sh.routing_added([" ip route 1.2.3.0/24 blackhole"],
                       ["ip route 1.2.3.0/24 blackhole"]) == [],
      "a statement FRR read back indented is the same statement typed flat")
# The case that was MEASURED rather than foreseen, and it fired on two ordinary
# operations before the labs caught it. Creating a loopback and setting an MTU
# stage a block HEADER in the routing half while everything inside the block is
# applied by this VTY to the dataplane - so the commit adds a header and nothing
# else, FRR does not store an empty interface stanza, and "nothing took" was
# true of a commit that did exactly what it was asked.
for _hdr in ("interface loop0", "router ospf", "route-map X permit 10",
             "vrf red", "rpki", "address-family ipv4 unicast"):
    check(sh.routing_added([], [_hdr]) == [],
          f"a block header is context, not a statement: {_hdr}")
check(sh.routing_added([], ["interface loop0", " ipv6 nd suppress-ra"])
      == ["ipv6 nd suppress-ra"],
      "and what is inside one still counts")


print("\n=== the IPFIX export goes over the binary API, not the CLI socket ===")
# #76: `cgnat logging` was the last configuration statement hexash applied with
# vppctl, and #172 found it still there while the acceptance criterion read as
# met. lab/vty.sh asserts the property against a real box; these check the
# SHAPE of what goes on the wire, including two forms no lab drives - `tcp
# disable`, and a collector named without a port.
import vppapi as _vppapi                                          # noqa: E402


def logging_call(line):
    """
    What `line` sends: (one API call or None, the list of CLI commands).

    Written to survive the regression it exists to catch. A version that
    indexed the API call directly raised a TypeError the moment the statement
    went back to the socket, and a test that CRASHES on the defect reports a
    broken test rather than a broken product.
    """
    sh = hexash.Shell()
    api, cli = [], []

    def api_call(_message, _args):
        api.append((_message, _args))
        return _vppapi.Reply(_vppapi.OK)

    sh.api_call = api_call
    sh.dp = type("D", (), {
        "TRACE": False,
        "run": lambda _s, c: (cli.append(c), (0, ""))[1],
        "reachable": lambda _s: True})()
    # apply_global, not apply_logging: the statement has to REACH the API
    # path, and a test that called the new method directly went on passing
    # with the dispatcher handing the line back to the socket - which is the
    # defect, not the parsing.
    sh.apply_global(line)
    return (api[0] if len(api) == 1 else None), cli


for _line in ("cgnat logging enable", "cgnat logging disable",
              "cgnat logging tcp 10.0.0.2", "cgnat logging tcp disable",
              "cgnat logging tcp 10.0.0.2 port 4740"):
    _a, _cli = logging_call(_line)
    check(_cli == [], f"'{_line}' must send nothing to the CLI socket: {_cli}")
    check(_a is not None and _a[0] == "bpa_logging_set",
          f"'{_line}' must be one bpa_logging_set and no more: {_a}")


def _f(line):
    """The fields of the call `line` makes, or {} if it made none."""
    _a, _ = logging_call(line)
    return _a[1] if _a else {}


check(_f("cgnat logging enable").get("set_enable") is True
      and _f("cgnat logging enable").get("enable") is True,
      f"enable: {_f('cgnat logging enable')}")
check(_f("cgnat logging enable").get("set_tcp") is False,
      f"and says nothing about the transport it was not asked about: "
      f"{_f('cgnat logging enable')}")
check(_f("cgnat logging disable").get("set_enable") is True
      and _f("cgnat logging disable").get("enable") is False,
      f"disable: {_f('cgnat logging disable')}")

_tcp = _f("cgnat logging tcp 10.0.0.2 port 4740")
check(_tcp.get("set_tcp") is True and _tcp.get("tcp_enable") is True,
      f"tcp on: {_tcp}")
check(_tcp.get("collector") == "10.0.0.2" and _tcp.get("port") == 4740,
      f"and where to: {_tcp}")
check(_tcp.get("set_enable") is False,
      f"naming a collector does not also turn the export on: {_tcp}")

# Zero, not 4739. The default belongs to the dataplane, which is where the
# running configuration is read back from - a default invented on this side
# would be a second opinion about it, and the two would drift apart silently.
check(_f("cgnat logging tcp 10.0.0.2").get("port") == 0,
      f"an omitted port is the dataplane's to fill: "
      f"{_f('cgnat logging tcp 10.0.0.2')}")

# `tcp disable` is the word an operator types and it is NOT an address. Read as
# one it would go on the wire as the collector, and the export would keep
# running to whatever the encoder made of it.
_off = _f("cgnat logging tcp disable")
check(_off.get("set_tcp") is True and _off.get("tcp_enable") is False,
      f"tcp off: {_off}")
check(_off.get("collector") != "disable",
      f"'disable' is not a collector address: {_off}")


print("\n=== the UDP exporter the product never had (#209) ===")
# UDP export was refused on every shipped box for the life of the project:
# VPP's own exporter needs a collector, nothing set one, and no statement
# reached it. lab/ipfix.sh asserts the export end to end on a box the product
# configured; these check the SHAPE of the message and the refusal that has to
# come before it.


def udp_call(line, exporter=None):
    """
    What `line` sends: (one API call or None, the CLI commands, the dump args).

    The dump is stubbed because the statement READS the exporter before it
    writes it - everything the statement does not name is sent back as the box
    already has it. `exporter` is what the box answers with; None is a
    dataplane that reports no exporter at all.
    """
    sh = hexash.Shell()
    api, cli, dumps = [], [], []

    def api_call(_message, _args):
        api.append((_message, _args))
        return _vppapi.Reply(_vppapi.OK)

    class _Api:
        def dump(self, message, args):
            dumps.append(message)
            rows = [] if exporter is None else [exporter]
            return _vppapi.Reply(_vppapi.OK, fields={"rows": rows})

    sh.api_call = api_call
    sh.api = _Api()
    sh.dp = type("D", (), {
        "TRACE": False,
        "run": lambda _s, c: (cli.append(c), (0, ""))[1]})()
    sh.apply_global(line)
    return (api[0] if len(api) == 1 else None), cli, dumps


# An untouched box: every field zero except vrf_id. Captured with
# ipfix_exporter_dump on a VPP that nothing had configured.
FRESH = {"collector_address": "0.0.0.0", "collector_port": 0,
         "src_address": "0.0.0.0", "vrf_id": 4294967295,
         "path_mtu": 0, "template_interval": 0, "udp_checksum": False}

_a, _cli, _dumps = udp_call(
    "cgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739", FRESH)
check(_cli == [], f"the exporter must not go down the CLI socket: {_cli}")
check(_a is not None and _a[0] == "set_ipfix_exporter",
      f"it goes on VPP's own message, once: {_a}")
check(_dumps == ["ipfix_exporter_dump"],
      f"and the box is read before it is written: {_dumps}")


def udp_fields(line, exporter):
    """
    The fields the call `line` makes, or {} when it made none.

    Indexing the call directly crashes the moment the statement stops making
    one, and a test that CRASHES on the defect reports a broken test rather
    than a broken product - which is the same trap logging_call() above was
    written around.
    """
    _c, _, _ = udp_call(line, exporter)
    return _c[1] if _c else {}


_u = udp_fields("cgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739", FRESH)
check(_u.get("collector_address") == "10.0.0.2"
      and _u.get("src_address") == "10.0.0.1"
      and _u.get("collector_port") == 4739,
      f"where to and from where: {_u}")
# The zeros a fresh box reports are not values to send back: the message
# refuses a path MTU of 0 outright (retval -7, measured), so there is no
# "leave it alone" and the caller has to name them. These are VPP's own CLI
# defaults, which is what a box configured any other way would have.
check(_u.get("path_mtu") == 512 and _u.get("template_interval") == 20,
      f"a zeroed exporter is filled with VPP's own defaults: {_u}")

# A box that already has an exporter: the statement changes what it names and
# nothing else. A path MTU somebody chose must survive a change of collector.
TUNED = dict(FRESH, collector_address="10.0.0.9", collector_port=9995,
             src_address="10.0.0.1", path_mtu=1400, template_interval=60)
_u = udp_fields("cgnat logging udp 10.0.0.2 src 10.0.0.1", TUNED)
check(_u.get("path_mtu") == 1400 and _u.get("template_interval") == 60,
      f"what the statement does not name is left alone: {_u}")
check(_u.get("collector_port") == 9995,
      f"and an omitted port keeps the one in force: {_u}")

# "disable" is a word an operator types and it is NOT an address.
_u = udp_fields("cgnat logging udp disable", TUNED)
check(_u.get("collector_address") == "0.0.0.0",
      f"disable clears the collector: {_u}")
# Measured: VPP refuses a source address of 0.0.0.0 whatever the collector is,
# so clearing the source as well has no encoding - the only way to say "no
# collector" is a collector of 0.0.0.0 with the source left where it was.
check(_u.get("src_address") == "10.0.0.1",
      f"and leaves the source, which VPP will not take as zero: {_u}")

# Nothing to turn off is not a message to send.
_a, _, _ = udp_call("cgnat logging udp disable", FRESH)
check(_a is None, f"disabling an exporter that was never set sends nothing: {_a}")

# A dataplane that reports no exporter at all is not one to guess at.
_a, _, _ = udp_call("cgnat logging udp 10.0.0.2 src 10.0.0.1", None)
check(_a is None, f"no exporter reported: nothing is sent: {_a}")

# The source address is not optional - VPP refuses the message without it -
# so the statement must not be accepted without it either.
_a, _, _ = udp_call("cgnat logging udp 10.0.0.2", FRESH)
check(_a is None, f"a collector with no source is refused, not sent: {_a}")

# The refusal `commit check` owes the operator. The dataplane's own refusal
# arrives half way through the apply, with the pools already changed.
_w = hexash.udp_export_without_collector
check(_w(["cgnat logging enable"]),
      "enable on its own is export with nowhere to send it")
check(not _w(["cgnat logging enable",
              "cgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739"]),
      "with the exporter beside it, it is fine")
check(_w(["cgnat logging enable", "cgnat logging udp disable"]),
      "and 'udp disable' is not an exporter")
check(_w(["cgnat logging enable", "cgnat logging tcp 10.0.0.2 port 4740"]),
      "nor is the TCP collector: the two transports are separate paths")
check(not _w(["cgnat logging disable"]),
      "an export nobody asked for needs no collector")
check(not _w(["cgnat block-holddown 11"]),
      "and neither does a configuration that says nothing about logging")

# Offered in the tree, or an operator cannot find the statement that makes
# their box legal.
_o = hexash.CGNAT_CONFIG["logging"][1]
check("udp" in _o, f"'udp' is offered under cgnat logging: {sorted(_o)}")


# ---------------------------------------------------------------------------
# `configure` on a box whose dataplane is not answering yet (#211)
#
# An installed box draws a login prompt while hexarouting.service is still
# waiting for VPP, so the first thing anybody does with a new box is log in at
# the first prompt and type `configure`. Both ways of proceeding from there are
# wrong, which is why the answer is to refuse.

def box_211(reachable, cfg_text):
    sh = hexash.Shell()
    sh.caps = {hexash.CAP_ALL, hexash.CAP_CGNAT}

    def run(_self, cmd):
        if cmd == "show version":
            return (0, "vpp v26.06") if reachable else (1, "Could not find VPP CLI socket")
        if cmd == "show bpa config":
            return (0 if reachable else 1), cfg_text
        return (0 if reachable else 1), ""

    sh.dp = type("D", (), {"TRACE": False, "run": run,
                           "reachable": lambda _s: run(_s, "show version")[0] == 0})()
    sh.frr = type("F", (), {"available": lambda s: False, "running": lambda s: [],
                            "show": lambda s, c: (1, "")})()
    sh.sys = type("S", (), {"read_ssh": lambda s: {}, "read_users": lambda s: {}})()
    sh.snmp = type("N", (), {"read": lambda s: ({}, [], {})})()
    return sh


sh = box_211(False, "Could not find VPP CLI socket")
out = capture(sh, "configure")
check("entering configuration mode" not in out,
      f"configuration mode is refused while the dataplane is silent: {out}")
check(sh.candidate is None and sh.mode == "operational",
      "and no candidate is built from what could not be read")
check("not answering" in out, f"the refusal says what is wrong: {out}")
# The half that matters most, because it is the one an operator would not
# guess: a candidate built now is missing the CGNAT configuration, and
# committing it takes every pool with it.
check("remove every pool" in out,
      f"and says what a candidate built now would have cost: {out}")

# The same box a minute later.
sh = box_211(True, "cgnat pool p1\n inside 100.64.0.0/22\n outside 203.0.113.0/24\n"
                   " block-size 512\n policy interleaved\n mode deterministic\n exit\n")
out = capture(sh, "configure")
check("entering configuration mode" in out, f"and allowed once it answers: {out}")
check(sh.candidate is not None and list(sh.candidate.pools) == ["p1"],
      f"with the dataplane's own configuration in it: {out}")

# vppctl's exit status is the signal, and it was being thrown away. Measured:
# vppctl exits 0 whenever VPP ANSWERED - including when VPP refused the command
# - and 1 only when it could not reach VPP. "Could not find VPP CLI socket"
# starts with a capital and carries no colon, so the pattern that recognises a
# VPP error never matched it and the text reached Config.parse as a line of
# configuration.
check(hexash.VPP_ERROR.match("Could not find VPP CLI socket") is None,
      "the text alone does not look like an error, which is the whole trap")
check(hexash.VPP_ERROR.match("unknown input `nope'") is not None,
      "while what VPP itself says does")

# So the status has to be read, and this exercises the REAL Vpp.run with a
# stand-in vppctl rather than a stub that returns the answer directly. The
# first version of this section tested the refusal through a stub whose run()
# already knew the right code, so removing the status check from Vpp.run
# changed nothing and the assertion could not fail - which is the shape this
# repository keeps finding in its own tests.
import stat as _stat, tempfile as _tf                             # noqa: E402
_dir = _tf.mkdtemp()
_fake = os.path.join(_dir, "vppctl")
with open(_fake, "w") as _f:
    # What the real one does, measured: it prints this on stdout and exits 1
    # when it cannot reach VPP, and exits 0 whenever VPP answered at all.
    _f.write("#!/bin/sh\n"
             "case \"$*\" in\n"
             "  *unreachable*) echo 'Could not find VPP CLI socket'; exit 1 ;;\n"
             "  *) echo \"unknown input \\`x'\"; exit 0 ;;\n"
             "esac\n")
os.chmod(_fake, os.stat(_fake).st_mode | _stat.S_IEXEC)
_saved, hexash.VPPCTL = hexash.VPPCTL, _fake
_vpp = hexash.Vpp()
_rc_unreach, _out_unreach = _vpp.run("unreachable")
_rc_refused, _ = _vpp.run("something")
hexash.VPPCTL = _saved

check(_rc_unreach == 1,
      f"vppctl exiting 1 is an error however innocent the text looks: {_rc_unreach}")
check("Could not find" in _out_unreach,
      "and the text still reaches the caller, for the operator to read")
check(_rc_refused == 1,
      "a command VPP itself refused is still an error, by the text")
check(not _vpp.reachable(),
      "and reachable() is that same status, asked once")


print("\n=== what FRR keeps, and what it does not (#197) ===")
# The table these read is MEASURED, by lab/frr-readback.sh, and the lab is what
# keeps it honest against an FRR upgrade. What is asserted here is the
# arithmetic on top of it: that the value decides, not the statement alone, and
# that a rewrite is not a drop.
from hexash import (frr_keeps, frr_writes_instead, frr_without_seq,  # noqa: E402
                    statements_frr_dropped)                       # noqa: E402

# The finding that unblocked #197. A prefix match alone would excuse the second
# of these, which is a real drop of a statement an operator really typed.
check(not frr_keeps("ip ospf hello-interval 10"),
      "FRR omits hello-interval at its default")
check(frr_keeps("ip ospf hello-interval 7"),
      "and keeps it at any other value, so the VALUE decides")
check(not frr_keeps("ip ospf retransmit-interval 5"),
      "same for retransmit-interval")
check(frr_keeps("ip ospf retransmit-interval 9"), "and the same the other way")
check(not frr_keeps("timers throttle spf 0 50 5000"),
      "a multi-word default is matched on all of its words")
check(frr_keeps("timers throttle spf 0 50 4999"),
      "and one word different is a statement FRR keeps")
# The pair that refutes "omission follows the default". Both of these ARE the
# FRR default and both come back, which is why there is no arithmetic here.
check(frr_keeps("ip ospf dead-interval 40"),
      "dead-interval comes back AT its default, so this is not a default rule")
check(frr_keeps("ip ospf cost 10"), "and so does cost")

check(frr_writes_instead("passive-interface dum9") == "ip ospf passive",
      "passive-interface is rewritten, not dropped (#205)")
check(frr_writes_instead("continue 30") == "on-match goto 30",
      "and continue becomes on-match goto (#208)")
check(frr_writes_instead("ip route 10.0.0.0/8 10.0.0.1") is None,
      "an ordinary statement is not rewritten")

# The entry that is not about a value. What decides is the LAST word, so the
# address between them is anything - a table keyed on leading words and fixed
# values cannot express that, which is why it is a separate list.
# The entry whose answer depends on the REST of the configuration, and the one
# that was written wrong first: unconditional, off half a measurement. That
# would have excused a GENUINELY dropped activate on every box that turns the
# default off - which is the ISP configuration, so the check would have been
# switched off exactly where it was needed.
_AF_ON = ["router bgp 65000", " neighbor 10.0.0.2 remote-as 65001"]
_AF_OFF = _AF_ON + [" no bgp default ipv4-unicast"]
check(not frr_keeps("neighbor 10.0.0.2 activate", _AF_ON),
      "'neighbor activate' is implied by bgp default ipv4-unicast, so omitted")
check(frr_keeps("neighbor 10.0.0.2 activate", _AF_OFF),
      "and STORED when that default is turned off, which is the ISP case")
check(not frr_keeps("neighbor vppwan0 activate", _AF_ON),
      "whatever the neighbour is named")
check(not frr_keeps("neighbor 10.0.0.2 activate"),
      "with no context at all it reads as omitted, which is the quiet side")

# IS-IS, probed because lab/igp.sh configures it and nothing had asked. Each
# has a control at a non-default value, which is what tells an omission from a
# REFUSAL: a statement FRR rejected outright also fails to come back, and
# excusing that would hide the very thing this check exists for.
check(not frr_keeps("metric-style wide"), "metric-style wide is omitted")
check(frr_keeps("metric-style narrow"), "and narrow is not")
check(not frr_keeps("isis hello-interval 3"), "isis hello 3 is omitted")
check(frr_keeps("isis hello-interval 5"), "and 5 is not")
check(not frr_keeps("isis metric 10"), "isis metric 10 is omitted")
check(frr_keeps("isis metric 77"), "and 77 is not")

# The entry that was written and then refuted. A probe appeared to show
# `ip ospf area 0` coming back dotted - but it TYPED the dotted form before
# looking for it, so what it found was its own keystrokes. Measured properly,
# one interface per value, FRR keeps the form it was given. The assertion is
# kept in the NEGATIVE so the wrong entry cannot come back quietly.
check(frr_writes_instead("ip ospf area 0") is None,
      "FRR keeps an area number as typed, so this is not a rewrite")
check(frr_writes_instead("ip ospf area 0.0.0.7") is None,
      "in either form")
check(statements_frr_dropped(["ip ospf area 0"],
                             ["interface dum9", " ip ospf area 0"]) == [],
      "and it is satisfied by the same form coming back")
check(frr_keeps("neighbor 10.0.0.2 remote-as 65001"),
      "while another neighbor statement is kept")
check(frr_keeps("neighbor 10.0.0.2 route-map RM in"),
      "including one in the same address family")

# A rewritten statement whose rewrite IS there is not a drop. Without this the
# check would refuse every commit containing a passive-interface.
check(statements_frr_dropped(["passive-interface dum9"],
                             ["interface dum9", " ip ospf passive"]) == [],
      "a rewrite that is present is not a drop")
# And when even the rewrite is absent, the message has to name what FRR would
# have written - an operator told "passive-interface is missing" will grep for
# it and find nothing.
check(statements_frr_dropped(["passive-interface dum9"], ["interface dum9"])
      == [("passive-interface dum9", "ip ospf passive")],
      "and when the rewrite is absent too, the report names it")

check(statements_frr_dropped(["ip ospf hello-interval 10"], []) == [],
      "a statement FRR omits is not reported as dropped")
check(statements_frr_dropped(["ip ospf hello-interval 7"], [])
      == [("ip ospf hello-interval 7", None)],
      "but the same statement at another value is")
check(statements_frr_dropped(["ip route 10.0.0.0/8 10.0.0.1"],
                             [" ip route 10.0.0.0/8 10.0.0.1"]) == [],
      "an ordinary statement that is there is not reported")
check(statements_frr_dropped([], ["anything"]) == [],
      "and a commit that introduced no routing statement asks nothing")

# The seq FRR inserts into every policy list statement. A rewrite whose target
# is NOT computable from the typed statement - the number depends on what is
# already in the list (5, then 10, then 15) - so it is matched by normalising
# both sides instead of by construction. This is the one that refused a working
# commit in lab/bgp-policy.sh: the policy took, the route it names was
# filtered, and the box still said the statement had been thrown away.
_POL = ["bgp as-path access-list AS-ORIGIN2 seq 5 permit _65002$",
        "ip prefix-list PL seq 5 permit 10.9.0.0/16"]
check(statements_frr_dropped(
          ["bgp as-path access-list AS-ORIGIN2 permit _65002$"], _POL) == [],
      "a policy list found under the seq FRR gave it is not a drop")
check(statements_frr_dropped(["ip prefix-list PL permit 10.9.0.0/16"],
                             _POL) == [],
      "and the same for a prefix-list")
# The half that keeps it a check rather than an excuse: a DIFFERENT statement
# of the same shape is still reported. Without this, normalising the seq would
# have switched the check off for every policy list on the box.
check(statements_frr_dropped(["ip prefix-list PL permit 10.0.0.0/8"], _POL)
      == [("ip prefix-list PL permit 10.0.0.0/8", None)],
      "while a prefix the list does not carry still is")
check(frr_without_seq("ip route 10.0.0.0/8 10.0.0.1") is None,
      "and a statement FRR does not do this to is left out of it entirely")

# nothing_took() ITSELF, through a stand-in frr that reports everything as
# outstanding - which is what frr-reload really does for these statements.
#
# Written this way after the first version could not fail: it repeated the
# filter expression inline instead of calling the method, so removing the
# filter from nothing_took() left it green. An assertion that reimplements the
# thing it is checking is checking itself.
#
# The false positive this removes was a REAL refusal of a working commit:
# lab/igp.sh typed `ip ospf hello-interval 10`, `ip ospf retransmit-interval 5`
# and `passive-interface vppwan0` in one commit, FRR stored the effect of all
# three, and the box refused it. frr-reload reports every one of them under
# "Lines To Add" for ever, so "all of them are still outstanding" was true of a
# commit that had worked.


class _AllOutstanding:
    """An frr that says every line the candidate holds is still to be added."""

    def __init__(self, lines):
        self._lines = lines

    def reload(self, _lines, check_only=False):
        return 0, "outstanding"

    def outstanding(self, _out):
        return self._lines


def _nothing_took(added, candidate=None):
    sh = hexash.Shell()
    sh.candidate = type("C", (), {"routing": candidate or added})()
    sh.frr = _AllOutstanding(added)
    return sh.nothing_took(added)


_OMITTED_ONLY = ["ip ospf hello-interval 10", "ip ospf retransmit-interval 5",
                 "passive-interface vppwan0"]
check(_nothing_took(_OMITTED_ONLY) == [],
      f"a commit of only statements FRR does not read back is not 'nothing "
      f"took': {_nothing_took(_OMITTED_ONLY)}")
check(_nothing_took(["ip ospf hello-interval 7"])
      == ["ip ospf hello-interval 7"],
      "while a statement FRR does keep, still outstanding, is")
check(_nothing_took(_OMITTED_ONLY + ["ip route 10.0.0.0/8 10.0.0.1"])
      == ["ip route 10.0.0.0/8 10.0.0.1"],
      "and a mixed commit is judged on the statements that mean something")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
