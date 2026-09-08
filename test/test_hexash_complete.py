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
markers = {hexash.ARG_INTERFACE, hexash.ARG_POOL, hexash.ARG_USER,
           hexash.ARG_SNMP_USER}
check(set(hexash.Shell.DYNAMIC) == markers,
      f"markers without a source: {sorted(markers - set(hexash.Shell.DYNAMIC))}")
for marker, method in hexash.Shell.DYNAMIC.items():
    check(hasattr(hexash.Shell, method),
          f"{marker} names {method}, which does not exist")
print(f"  {len(markers)} markers, all sourced")

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

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
