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
o, hint, _ = opts("config", "cgnat block-holddown ")
check(hint == hexash.ARG_NUM, f"expected a numeric hint, got {hint!r}")
print(f"  'inside ' -> {hexash.ARG_PREFIX} ; 'block-holddown ' -> {hexash.ARG_NUM}")

print("\n=== config: CGNAT and routing at the same level ===")
o, _, _ = opts("config", "")
check({"cgnat", "router", "interface", "no", "commit", "rollback", "exit"} <= o,
      f"got {o}")
o, _, _ = opts("config", "cgnat ")
check({"pool", "interface", "timeout", "logging", "block-holddown"} <= o, f"got {o}")
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

print("\n=== 'start shell' is hidden but reachable ===")
# Hidden means: absent from completion and from '?', at every level. If it
# ever shows up in the tree, the reason it is hidden is gone.
for mode, line in (("operational", ""), ("config", ""), ("pool", ""),
                   ("operational", "start "), ("config", "start ")):
    o, hint, _ = opts(mode, line)
    check("start" not in o and "shell" not in o,
          f"'start shell' leaked into completion for {mode!r} {line!r}: {sorted(o)}")
check(hasattr(hexash.Shell, "start_shell"), "the escape must still exist")
src = open(hexash.__file__).read()
check('["start", "shell"]' in src,
      "the escape must be dispatched before the command tree")
print("  absent from completion and from '?', still dispatched")

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
sh.feed("cgnat pool residential")
sh.feed("inside 100.64.0.0/22")
sh.feed("exit")
sh.feed("cgnat pool business")
sh.feed("exit")
opts, _, _ = sh._options("cgnat pool ", False)
check(set(opts) == {"residential", "business"},
      f"pools come from the candidate being edited: {sorted(opts)}")
opts, _, _ = sh._options("no cgnat pool ", False)
check(set(opts) == {"residential", "business"},
      f"and from the removal branch too: {sorted(opts)}")
print(f"  cgnat pool -> {sorted(opts)}")

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
sh.feed("router ospf")
opts, _, _ = sh._options("", False)
check(set(opts) == {"exit", "show"},
      f"an unmodelled FRR block offers only the way out: {sorted(opts)}")
print(f"  router ospf -> {sorted(opts)}")
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

out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.feed("show interface description")
check("no per-interface description" in out.getvalue(),
      "asking for descriptions says why there is no such column, rather than "
      "leaving somebody looking for one that is never coming")

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

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
