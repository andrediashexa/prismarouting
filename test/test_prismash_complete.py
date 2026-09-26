#!/usr/bin/env python3
"""
Tests for the VTY's completion and "?" help.

They exercise the command tree with no terminal: what readline would do when
completing or asking for help comes from _options(), and that is what is
checked here.
"""
import ast
import contextlib
import io
import os, re, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "prismash"))
import prismash
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nat_fixture import in_nat_block      # noqa: E402

fail = 0

def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")

def opts(mode, line, ending=None, nat=()):
    """What `?` offers after `line`; `nat` is the `nat` > `cgnat` level the
    operator stands in (#446)."""
    sh = prismash.Shell()
    sh.mode = mode
    sh.nat_ctx = list(nat)
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
            "subscriber", "translation", "top", "drops",
            # The two the dataplane had and this VTY did not reach (#228).
            "alg", "forward",
            # The one that is NOT the dataplane's: a comparison between two
            # boxes, which the dataplane cannot answer because it holds one
            # of them (#8).
            "ha",
            # The count of subscriber identities and when they were read -
            # never an entry, which is why this screen exists at all (#21).
            "identity",
            # The CGNAT's memory: what the heap holds and what still fits
            # (#454). This VTY's own reading of three dataplane screens.
            "memory"}, f"got {o}")
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
check(hint == prismash.ARG_PREFIX, f"expected a prefix hint, got {hint!r}")
check(not o, f"should not list options after 'inside': {o}")
o, hint, _ = opts("config", "block-holddown ", nat=("nat", "cgnat"))
check(hint == prismash.ARG_SECONDS, f"expected a numeric hint, got {hint!r}")
print(f"  'inside ' -> {prismash.ARG_PREFIX} ; "
      f"'block-holddown ' -> {prismash.ARG_SECONDS}")

print("\n=== config: CGNAT and routing at the same level ===")
o, _, _ = opts("config", "")
check({"nat", "router", "interface", "no", "commit", "rollback", "exit"} <= o,
      f"got {o}")
# `cgnat` is NOT offered at the top level: it is a level under `nat` (#193,
# #446), and `nat` is a word that is entered and takes nothing after it.
check("cgnat" not in o, f"the old top-level spelling is not offered: {o}")
o, _, _ = opts("config", "nat ")
check(o == set(), f"nothing follows 'nat' on its line: it is entered: {o}")
o, _, _ = opts("config", "", nat=("nat",))
check("cgnat" in o and "pool" not in o and "exit" in o,
      f"standing in nat, cgnat is what is offered: {o}")
o, _, _ = opts("config", "", nat=("nat", "cgnat"))
check({"pool", "interface", "timeout", "logging", "block-holddown", "no",
       "exit", "commit", "show"} <= o and "nat" not in o and "router" not in o,
      f"standing in cgnat, its own statements and nothing from above: {o}")
o, _, _ = opts("config", "no ", nat=("nat", "cgnat"))
check(o == {"pool", "interface"}, f"and 'no' there removes a pool or an "
      f"attachment: {o}")
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
sh = prismash.Shell()
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

print("\n=== every range `?` quotes is a range the box takes ===")
# WHAT THIS IS ABOUT. A hint like `<576-9216>` is not decoration: it is the
# box telling an operator, before they type, which numbers it will accept.
# #312 is what that promise is worth when nothing keeps it - `commit confirmed`
# had said `<1-60>` since the day it was written, `confirmed 600` armed a ten
# hour window, and `confirmed 0` armed nothing at all and answered `commit
# complete`. Sweeping the other forty-four sites found four more of the same
# shape, all in statements this VTY applies itself (#350, #351, #352).
#
# FOUR VALUES PER RANGE, and all four matter. `a` and `b` catch a box that
# refuses what it documents - an operator typing the documented limit and
# being told no. `a-1` and `b+1` catch the other direction, which is milder
# on the screen and not milder on the box: `max-blocks 256` was accepted here,
# written to the file and sent to a u8 field, where it became 1.
#
# Only the statements the VTY ENFORCES are here. `ospf maximum-paths` and the
# rest of the routing tree are forwarded to FRR, which is the authority on
# them and refuses at commit rather than at the line; what those are owed is
# a hint that matches FRR's own grammar, and test_doc_commands compares the
# tree against the wiki rather than against a number this file would invent.
_RANGES = [
    # (label, block to enter, statement with {v}, a, b, what holds the value)
    ("interface mtu", ["interface ens19"], "mtu {v}", 576, 9216,
     lambda c: c.mtus.get("ens19")),
    ("pool block-size", ["nat", "cgnat", "pool P"], "block-size {v}", 64, 32768,
     lambda c: None),
    ("pool max-blocks", ["nat", "cgnat", "pool P"], "max-blocks {v}", 1, 255,
     lambda c: None),
    # TWO ROWS FOR ONE STATEMENT, because `ports` carries two numbers and
    # each is its own end of the same range. The other half of each template
    # is held at a value that is legal and in order, so what the row measures
    # is the bound on the half that moves and not the ordering check beside
    # it. Both ends were enforced by nothing at all before #361: `ports 70000
    # 80000` was taken at the prompt and cast into a u16 on the way to the
    # dataplane, where it became 4464-14464.
    ("pool ports min", ["nat", "cgnat", "pool P"], "ports {v} 65535", 1, 65535,
     lambda c: None),
    ("pool ports max", ["nat", "cgnat", "pool P"], "ports 1 {v}", 1, 65535,
     lambda c: None),
    ("pool block-stride", ["nat", "cgnat", "pool P"], "block-stride {v}", 0, 65535,
     lambda c: None),
    ("pool reserve", ["nat", "cgnat", "pool P"], "reserve {v}", 0, 65535,
     lambda c: None),
    ("pool utilisation-alarm", ["nat", "cgnat", "pool P"],
     "utilisation-alarm {v}", 1, 100, lambda c: None),
    ("pool timeout udp", ["nat", "cgnat", "pool P"], "timeout udp {v}",
     1, 4294967295, lambda c: None),
    ("global cgnat timeout udp", ["nat", "cgnat"], "timeout udp {v}", 1,
     4294967295,
     lambda c: None),
    ("interface vlan", ["interface ens19.7"], "vlan 7 inner {v}", 1, 4094,
     lambda c: (c.vlans.get("ens19.7") or {}).get("inner")),
]


def _typed(block, line):
    """
    What the VTY did with one statement, said and refused.

    A fresh Shell per value, not one carried across the table. Several of
    these statements leave a pool or an interface block behind them, and a
    value refused on a shell that is still inside the previous row's block is
    a measurement of the wrong statement.
    """
    _s = prismash.Shell()
    _s.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT}
    _s.mode = "config"
    _s.candidate = _config.Config()
    _out = io.StringIO()
    with contextlib.redirect_stdout(_out):
        for _b in block:
            _s.feed(_b)
        _before = _s.failed
        _s.feed(line)
    return _s, _s.failed > _before, _out.getvalue()


for _label, _block, _tpl, _a, _b, _read in _RANGES:
    for _end, _v in (("a", _a), ("b", _b)):
        _s, _refused, _said = _typed(_block, _tpl.format(v=_v))
        check(not _refused,
              f"{_label}: {_v} is the {_end} end of the range `?` quotes and "
              f"the box refused it: {_said.strip()!r}")
    for _end, _v in (("a-1", _a - 1), ("b+1", _b + 1)):
        _s, _refused, _said = _typed(_block, _tpl.format(v=_v))
        check(_refused,
              f"{_label}: {_v} is outside the range `?` quotes and the box "
              f"took it")
        # AND THE REFUSAL NAMES THE RANGE. The two numbers are written in two
        # places - the hint and the sentence - and a refusal that does not
        # carry them leaves the operator to find the hint again while the
        # maintenance window is open. This is also what tells a range refusal
        # from a refusal that happened to fire for some other reason, which is
        # the difference `confirmed 0` turned on: zero was falsy, took the
        # branch for "no window asked for", and nothing was refused at all.
        check(f"{_a}-{_b}" in _said or f"({_a}-{_b})" in _said,
              f"{_label}: refusing {_v} without naming {_a}-{_b}: "
              f"{_said.strip()!r}")
print(f"  {len(_RANGES)} ranges, each accepting both ends and refusing both "
      f"outsiders by name")

# WHY THIS IS A TABLE AND NOT A WALK OF THE TREE, which is the first thing
# anybody reading it will want to change.
#
# Counted over every upper-case dict this module exports: 109 range
# occurrences across 24 help tables, naming 80 distinct statements. Deriving
# the list from those instead of writing it here would need no allowlist to
# FIND them - and the rule it would then apply, "a-1 and b+1 must both be
# refused", is false of at least one hint that is complete and honest.
#
# `interface <name>` / `vrf` is the one measured. Its argument reads
# `<1-4294967295>` and its description reads "0 takes the interface back out
# of one"; the box accepts 0 and clears the statement, and the refusal it
# prints for anything else is `usage: vrf <1-4294967295>, or 0 to take it back
# out`. Three places, one story, and 0 is in two of them. A walk that read
# only the range would call that a defect and send somebody to refuse a value
# the product deliberately takes.
#
# So the generalisation needs to read the description beside the range, or it
# needs a list of statements to excuse - and a check that needs an allowlist
# is one this repository has already decided not to ship. The table below
# stays until somebody has the first of those.
print("\n=== a range that ends at 1 and a value of 0 that is not an error ===")
_s, _refused, _said = _typed(["interface ens19"], "vrf 7")
check(not _refused and _s.candidate.vrfs.get("ens19") == 7,
      f"an interface could not be put in a VRF at all, so what 0 does below "
      f"is measured against nothing: {_said.strip()!r} {_s.candidate.vrfs}")
_s, _refused, _said = _typed(["interface ens19", "vrf 7"], "vrf 0")
check(not _refused,
      f"'vrf 0' was refused on an interface, and the hint's own description "
      f"says it takes the interface back out of a VRF: {_said.strip()!r}")
check(_s.candidate.vrfs.get("ens19") is None,
      f"'vrf 0' was taken and left the interface in a VRF, which is neither "
      f"of the two things it could honestly do: {_s.candidate.vrfs}")
_s, _refused, _said = _typed(["interface ens19"], "vrf 4294967296")
check(_refused and "0 to take it back out" in _said,
      f"the refusal above the top of the range does not offer 0, so the one "
      f"place an operator meets that value is the description they have "
      f"already scrolled past: {_said.strip()!r}")
print("  'vrf 0' clears the statement, and the refusal says so")

print("\n=== zero, where zero is outside the range ===")
# THE VALUE #312 WAS ABOUT, driven on its own everywhere it is not in range.
# Zero is falsy, so it is the one value that reaches a branch written for "the
# operator did not ask for this" rather than the branch written for "the
# operator asked for something out of range" - and the difference between the
# two is a box that says no and a box that says nothing. `cgnat timeout udp 0`
# was the live one: accepted, saved, and sent to a message whose contract
# reads a zero timeout as "leave this one alone" (#351).
for _label, _block, _tpl, _a, _b, _read in _RANGES:
    if _a <= 0:
        continue
    _s, _refused, _said = _typed(_block, _tpl.format(v=0))
    check(_refused, f"{_label}: 0 is outside {_a}-{_b} and the box took it")
    check(f"{_a}-{_b}" in _said,
          f"{_label}: refusing 0 without naming {_a}-{_b}: {_said.strip()!r}")
print("  0 refused by name at every range that does not contain it")

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
# `cutoff` and `remap` are gate words an operator meets in a refusal and has
# to type back. One they cannot complete is one they will mistype in the
# moment the box is asking for it.
check({"check", "confirmed", "comment", "remap", "cutoff"} <= o, f"got {o}")
print(f"  commit -> {sorted(o)}")

o, hint, _ = opts("config", "rollback ")
check(hint == prismash.ARG_REV, f"rollback should hint a revision, got {hint!r}")
print(f"  rollback -> {prismash.ARG_REV}")

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
check(hasattr(prismash.Shell, "start_shell"), "the escape must still exist")
src = open(prismash.__file__).read()
check('["start", "shell"]' in src,
      "the escape must be dispatched before the command tree")

# THE half that matters, and the one the old assertion never made: being
# offered must not mean being granted. A shell on this box is every
# capability at once.
for caps, who, want in ((set(), "a read-only account", False),
                        ({prismash.CAP_CGNAT}, "a cgnat operator", False),
                        ({prismash.CAP_ALL}, "an administrator", True)):
    probe = prismash.Shell.__new__(prismash.Shell)
    probe.caps = caps
    probe.failed = 0
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        allowed = probe.require(prismash.CAP_ALL, "start shell")
    check(allowed is want,
          f"{who} is {'allowed' if want else 'refused'} the shell")
    if not want:
        check("permission denied" in out.getvalue(),
              f"and {who} is told why: {out.getvalue().strip()[:60]}")
print("  offered at every level, granted only to an administrator")

print("\n=== 'system' is offered, with reboot and shutdown under it ===")
# The menu the maintainer asked for (#388), offered the way every other
# statement is: at the top of operational mode, its two verbs one level
# down, and nothing after either of them. Written out rather than derived
# from the tree, for the reason the `show cgnat` box gives above.
o, _, _ = opts("operational", "")
check("system" in o, f"'system' is offered at the top level: {sorted(o)}")
o, hint, _ = opts("operational", "system ")
check(o == {"reboot", "shutdown", "upgrade", "dataplane"},
      f"and exactly the four under it (#440, #453): {sorted(o)}")
o, _, _ = opts("operational", "system dataplane ")
check(o == {"restart"}, f"and 'restart' under dataplane (#453): {sorted(o)}")
check(hint is None, f"with no argument hint beside them: {hint!r}")
o, _, prefix = opts("operational", "syst")
check(o == {"system"} and prefix == "syst",
      f"'syst' completes to system alone: {sorted(o)}")
o, _, _ = opts("operational", "system re")
check(o == {"reboot"}, f"'system re' completes to reboot alone: {sorted(o)}")
o, hint, _ = opts("operational", "system reboot ")
check(not o and hint is None,
      f"and nothing follows the verb: options={sorted(o)} hint={hint!r}")
# The help beside each verb has to say what it costs, because `?` is where
# an operator reads that before typing it. Read through _options, which is
# what `?` renders, and not off TREE.
sh = prismash.Shell()
sh.mode = "operational"
helps, _, _ = sh._options("system ", False)
check("every session ends" in helps.get("reboot", ""),
      f"'?' says what a reboot costs: {helps.get('reboot')!r}")
check("every session ends" in helps.get("shutdown", ""),
      f"and what a shutdown costs: {helps.get('shutdown')!r}")
check("comes back" in helps.get("reboot", "")
      and "stays off" in helps.get("shutdown", ""),
      "and what tells the two apart: one comes back, the other stays off")
check("unsaved" in helps.get("reboot", "")
      and "unsaved" in helps.get("shutdown", ""),
      "and the cost that is this box's: what is not saved is lost")
# Each row `?` prints is two spaces, the widest key, two spaces and the help
# (display_matches), so the help has that gutter's worth less than the
# console. 30 older TREE rows are already over it; these three are not
# allowed to join them. Measured with the gutter the renderer really uses.
_top, _, _ = sh._options("", False)
_gutter = 2 + max(len(k) for k in _top) + 2
_sub_gutter = 2 + max(len(k) for k in helps) + 2
for _key, _help, _g in (("system", _top["system"], _gutter),
                        ("reboot", helps["reboot"], _sub_gutter),
                        ("shutdown", helps["shutdown"], _sub_gutter)):
    check(_g + len(_help) <= _config.CONSOLE_COLUMNS,
          f"the '?' row for {_key} fits the console: {_g + len(_help)} of "
          f"{_config.CONSOLE_COLUMNS} columns")
print("  offered at the top, two verbs under it, and '?' prices both")

print("\n=== show version ===")
o, _, _ = opts("operational", "show ")
check("version" in o, f"'version' should be under show: {sorted(o)}")

import tempfile as _tf, os as _os
with _tf.TemporaryDirectory() as d:
    vf = _os.path.join(d, "VERSION")
    with open(vf, "w") as f:
        f.write("0.9.1 (abc1234-dirty)\n")
    _os.environ["PRISMA_VERSION_FILE"] = vf
    import importlib
    importlib.reload(prismash)
    check(prismash.prisma_routing_version() == "0.9.1 (abc1234-dirty)",
          f"should read the stamp from the file, read {prismash.prisma_routing_version()!r}")
    # A dirty tree has to keep saying it is dirty: that stamp is what stops an
    # ISO from declaring itself a release when it is not one.
    check("dirty" in prismash.prisma_routing_version(),
          "the dirty-tree stamp must not be swallowed")

# With no installed file it has to fall back to the repository VERSION - that
# is what makes prismash run from a development tree tell the truth.
_os.environ["PRISMA_VERSION_FILE"] = "/does/not/exist/VERSION"
importlib.reload(prismash)
repo_version = open(_os.path.join(_os.path.dirname(_os.path.abspath(prismash.__file__)),
                                  "..", "..", "VERSION")).read().strip()
check(prismash.prisma_routing_version() == repo_version,
      f"should fall back to the repository VERSION ({repo_version!r}), "
      f"read {prismash.prisma_routing_version()!r}")
# A deliberate tripwire: 1.0.0 is a decision by the maintainer, not a number
# somebody raises by accident. When they declare it ready, this check goes out
# together with the rule in CONTRIBUTING.md.
check(repo_version.startswith("0."),
      f"the version is {repo_version!r}: 1.0.0 only once the maintainer "
      "declares it production-ready (CONTRIBUTING.md, section 5)")
print(f"  version read: {prismash.prisma_routing_version()}")


print("\n=== the banner: on a terminal, and nowhere else ===")
# The absence half matters more than the presence half. "prismash -c" is how
# every lab and every script drives this VTY, and a banner prepended to their
# output breaks the parsing - which is the reason the version line was already
# guarded before the banner existed.
import subprocess as _sp

_HX = _os.path.join(_os.path.dirname(_os.path.abspath(prismash.__file__)),
                    "prismash.py")
_env = dict(_os.environ, PRISMA_VERSION_FILE="/does/not/exist/VERSION")

# Commands PIPED IN, not "-c". This is the path that can actually leak: "-c"
# never enters repl() at all, so a test built on it passes whatever the guard
# does - which is what the first version of this check did, and it went on
# passing with the banner printed unconditionally.
_p = _sp.run([sys.executable, _HX], input="show version\nexit\n",
             capture_output=True, text=True, timeout=30, env=_env)
_piped = _p.stdout + _p.stderr
check("\\____\\/______/" not in _piped,
      f"banner art leaked into piped output: {_piped[:120]!r}")
check("Prisma Routing VTY" not in _piped,
      "the version line must not reach a pipe either")
print("  piped commands carry no banner")

_p2 = _sp.run([sys.executable, _HX, "-c", "show version"],
              capture_output=True, text=True, timeout=30, env=_env)
check("\\____\\/______/" not in (_p2.stdout + _p2.stderr),
      "banner art leaked into 'prismash -c' output")
print("  and neither does 'prismash -c'")


def _on_a_pty(env=None):
    """Run prismash on a real terminal and return what it printed."""
    import pty as _pty, time as _time
    m, sl = _pty.openpty()
    pid = _os.fork()
    if pid == 0:
        _os.close(m)
        for fd in (0, 1, 2):
            _os.dup2(sl, fd)
        _os.execve(sys.executable, [sys.executable, _HX], env or _env)
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
# Read out of BANNER rather than quoted: this needle was a row of the hexagon
# signature, and replacing the banner (#433) turned it red for a reason that
# was not about whether a banner appears. Every row, so a banner drawn half
# way is not a pass.
check(all(r.rstrip() in _tty for r in prismash.BANNER.split("\n")),
      "the banner must appear on a terminal, every row of it")
check("Prisma Routing VTY" in _tty,
      "the version line must survive the banner: the banner identifies the "
      "product, the version identifies the build, and an operator on the "
      "phone needs the second")
_widest = max((len(l) for l in _tty.split("\n")), default=0)
check(_widest <= 80,
      f"a line is {_widest} columns; a racked box has an 80-column serial "
      "console and a banner that wraps is worse than none")
print(f"  banner shown on a terminal, widest line {_widest} columns")

# And on the installation medium, the one line nobody can afford to miss. A
# medium is a squashfs that exists and an installer that is executable, and
# both are read from the environment, so a medium is two temporary files.
# Held here because no offline reader had this sentence: lab/boot-iso.sh
# reads the medium's console and needs a booted image to do it.
import tempfile as _tf
_med = _tf.mkdtemp()
_sq = _os.path.join(_med, "filesystem.squashfs")
_ins = _os.path.join(_med, "prisma-routing-install")
open(_sq, "w").close()
open(_ins, "w").write("#!/bin/sh\nexit 0\n")
_os.chmod(_ins, 0o755)
_on_medium = _on_a_pty(dict(_env, PRISMA_LIVE_SQUASHFS=_sq, PRISMA_INSTALLER=_ins))
check("Type 'install' to install Prisma Routing onto a disk." in _on_medium,
      "the medium's login screen tells the operator the one word that makes "
      "a box out of it, and names the product it installs")
check("Type 'install'" not in _tty,
      "and an installed box is not told to install anything")
print("  the medium's hint names the product")

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
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT}
    def run(_self, cmd):
        if cmd.startswith("show interface"):
            return dp_rc, iface_text
        return 0, ""          # anything else: a box with nothing configured

    # reachable() too, because the real Vpp has it and a stub that does not
    # mirror the real class is a stub that passes a test the product fails.
    # It answers through run(), the way the real one does.
    sh.dp = type("D", (), {"TRACE": False, "run": run,
                           "reachable": lambda _s: run(_s, "show version")[0] == 0})()
    sh.frr = type("F", (prismash.Frr,), {"available": lambda s: False,
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
# `len(opts) == 1` used to stand here, which the line above already entails:
# a dict whose key list equals a one-element list has one key, necessarily.
# What is worth its own line is the other direction - a prefix that matches
# nothing must offer nothing rather than fall back to the whole list, which is
# the failure that turns completion into a menu of everything the box has.
none_opts, _, _ = sh._options("interface Nosuch", True)
check(none_opts == {},
      f"and a prefix that matches nothing offers nothing: {sorted(none_opts)}")
print(f"  interface Ten -> {sorted(opts)}")

print("\n=== names the CANDIDATE knows ===")
# Two pools made at the `cgnat` level (#446), and the names offered back
# where a pool is named: `pool` and `no pool`, standing there.
sh.feed("nat")
sh.feed("cgnat")
sh.feed("pool residential")
sh.feed("inside 100.64.0.0/22")
sh.feed("exit")
sh.feed("pool business")
sh.feed("exit")
check(set(sh.candidate.pools) == {"residential", "business"},
      f"both pools are made: {sorted(sh.candidate.pools)}")
check(sh.nat_ctx == ["nat", "cgnat"],
      f"and leaving a pool returns to cgnat: {sh.nat_ctx}")
opts, _, _ = sh._options("pool ", False)
check(set(opts) == {"residential", "business"},
      f"pools come from the candidate being edited: {sorted(opts)}")
opts, _, _ = sh._options("no pool ", False)
check(set(opts) == {"residential", "business"},
      f"and from the removal branch too: {sorted(opts)}")
sh.feed("top")
print(f"  pool -> {sorted(opts)}")

print("\n=== a source that cannot answer offers nothing, and does not raise ===")
# A VTY that becomes unusable when the dataplane is down is a VTY that cannot
# be used to fix it.
sh = shell_with_box(dp_rc=1)
sh.feed("configure")
opts, hint, _ = sh._options("interface ", False)
check(opts == {}, f"nothing is offered: {opts}")
check(hint == prismash.ARG_INTERFACE_NEW,
      f"and the hint still says what goes here: {hint}")
# Which matters most exactly here: with the dataplane down there is no list at
# all, so the hint and its note are the only thing on the screen - and on a box
# whose dataplane is down, `interface loop0` is still a thing to type (#406).
check("<parent>.<tag>" in prismash.HINT_NOTES.get(hint, ""),
      "and the shapes that create one are still under it")
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
sh = prismash.Shell()
sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT}
sh.dyn_interfaces = lambda: (_ for _ in ()).throw(
    OSError("no such file or directory: /run/vpp/cli.sock"))
try:
    opts = sh.dynamic_options(prismash.ARG_INTERFACE, "")
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
markers = {prismash.ARG_INTERFACE, prismash.ARG_POOL, prismash.ARG_USER,
           prismash.ARG_SNMP_USER}
check(markers <= set(prismash.Shell.DYNAMIC),
      f"markers without a source: {sorted(markers - set(prismash.Shell.DYNAMIC))}")
missing = [m for m, name in prismash.Shell.DYNAMIC.items()
           if not callable(getattr(prismash.Shell, name, None))]
check(not missing,
      f"markers whose source method does not exist: {missing}")
# And a marker is only useful if something in the command tree offers it. One
# that nothing references is a promise nobody can reach.
#
# Looked up by the CONSTANT's name, because that is how the tree refers to
# them - counting the literal value found only the definition and reported
# every marker as unused, which is this check being wrong rather than the code.
tree_text = open(prismash.__file__).read()
const_of = {v: k for k, v in vars(prismash).items()
            if isinstance(v, str) and k.startswith("ARG_")}
# Three, not two. Every marker in DYNAMIC carries two occurrences of its
# constant BY CONSTRUCTION - the `ARG_X = "<x>"` that defines it and the key of
# the DYNAMIC entry itself - so a threshold of two was below the floor and no
# marker could ever reach it. A marker the command tree offers has a third.
unused = [m for m in prismash.Shell.DYNAMIC
          if tree_text.count(const_of.get(m, "\x00")) < 3]
check(not unused, f"markers nothing in the tree offers: {unused}")
for marker, method in prismash.Shell.DYNAMIC.items():
    check(hasattr(prismash.Shell, method),
          f"{marker} names {method}, which does not exist")
# len(DYNAMIC), not len(markers): the second is the hardcoded subset this test
# asserts is present, and reporting it said "4 markers" on a box with five.
print(f"  {len(prismash.Shell.DYNAMIC)} markers, all sourced: "
      + ", ".join(sorted(prismash.Shell.DYNAMIC)))

print("\n=== show this ===")
sh = shell_with_box()
sh.feed("configure")
sh.feed("nat")
sh.feed("cgnat")
sh.feed("pool residential")
sh.feed("inside 100.64.0.0/22")
sh.feed("tcp-mss 1400")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.feed("show this")
text = out.getvalue()
check(text.startswith("pool residential") and "tcp-mss 1400" in text,
      f"the pool being edited is shown, in the words it is typed in: "
      f"{text!r}")
check("business" not in text,
      "and nothing above it: 'this' is the hierarchy, not the box")
print("  inside a pool: " + text.strip().replace("\n", " / "))

# It shows the CANDIDATE. Somebody mid-edit asking "what is here" is asking
# about what they are building; answering with what is committed answers a
# question they did not ask.
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.feed("top")
    sh.feed("show this")
check("  pool residential" in out.getvalue(),
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
from prismash import INTERFACE_BLOCK                              # noqa: E402
# `show`, and the words every configuration level owns: `top`, `up` and the
# three probes (#400). They are not statements of any block and they are
# offered inside every one of them, because they are answered inside every one
# of them - which is the whole of that issue. Taken from the product's own two
# sets rather than written out here, so a probe added later is not a failure of
# this check.
from prismash import LEVEL_WORDS, PROBE_TOOLS                     # noqa: E402
LEVEL = {"show"} | set(LEVEL_WORDS) | set(PROBE_TOOLS)
check(set(opts) <= set(INTERFACE_BLOCK) | LEVEL,
      f"an interface offers only its own statements: "
      f"{sorted(set(opts) - set(INTERFACE_BLOCK) - LEVEL)}")
check({"mtu", "urpf"} <= set(opts),
      f"and it does offer them: {sorted(opts)}")
# `cgnat` is not in the list since #465: `cgnat inside|outside` is a statement
# OF the interface now. `pool` is the CGNAT level's own word, and is.
check(not ({"pool", "user", "commit", "rollback"} & set(opts)),
      f"and nothing from the level above leaks in: {sorted(opts)}")
print(f"  interface -> {sorted(opts)}")
sh.feed("exit")

from prismash import BGP_BLOCK, BGP_NEIGHBOUR, BGP_AF_BLOCK   # noqa: E402
sh.feed("router bgp 65001")
opts, _, _ = sh._options("", False)
check(set(opts) <= set(BGP_BLOCK) | LEVEL,
      f"router bgp offers only its own statements: "
      f"{sorted(set(opts) - set(BGP_BLOCK) - LEVEL)}")
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
check(set(opts) <= set(BGP_AF_BLOCK) | LEVEL,
      f"an address family offers only its own: "
      f"{sorted(set(opts) - set(BGP_AF_BLOCK) - LEVEL)}")
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
check(set(opts) == {"exit"} | LEVEL,
      f"an unmodelled FRR block offers the way out and the level's own "
      f"words: {sorted(opts)}")
print(f"  router rip -> {sorted(opts)}")
sh.feed("exit")

# The blocks the tree knows about were always right; this is the check that
# they stayed right.
sh.feed("nat")
sh.feed("cgnat")
sh.feed("pool p")
opts, _, _ = sh._options("", False)
check("inside" in opts and "cgnat" not in opts,
      f"a pool still offers its own tree: {sorted(opts)[:6]}")
sh.feed("top")

print("\n=== ? says which names create an interface (#406) ===")
# A maintainer asked for sub-interfaces to be implemented on a box that had had
# them since #131. They work: `interface ens18.100 / vlan 100` makes one, and
# `loop0` makes a loopback. What did not work is FINDING them - `?` offered the
# interfaces that already existed, so the list read as the complete set of what
# may be typed, and the shape that creates one was stated only inside a refusal
# an operator reaches by guessing wrong first.
#
# Mutation shown red: the marker put back to ARG_INTERFACE, which takes the
# note away and leaves the hint saying "<interface>" over a list of interfaces.
sh = shell_with_box()
sh.feed("configure")
opts, hint, _ = sh._options("interface ", False)
check("TenGigabitEthernet0/8/0" in opts,
      f"the box's own interfaces are still offered: {sorted(opts)}")
check(hint == prismash.ARG_INTERFACE_NEW,
      f"under a marker of its own: {hint!r}")
note = prismash.HINT_NOTES.get(hint, "")
check("<parent>.<tag>" in note and "loop<n>" in note,
      f"whose note names both shapes that create one: {note!r}")
check("parent has to exist" in note,
      f"and says the parent is not created with it: {note!r}")
# The plain marker stays where a name the box HAS is what is taken: offering
# "or invent one" there would offer a name the statement refuses.
_, show_hint, _ = sh._options("show interface ", False)
check(show_hint != prismash.ARG_INTERFACE_NEW,
      f"`show interface` still takes a name the box has: {show_hint!r}")
# And every marker with a note is one the tree actually uses, or the note is
# printed by nothing.
_used = set()
def _markers(node):
    if isinstance(node, str):
        _used.add(node)
        return
    for _, sub in (node or {}).items():
        _markers(sub[1])
for _mode in prismash.TREE.values():
    _markers(_mode)
_markers(prismash.INTERFACE_BLOCK)
check(set(prismash.HINT_NOTES) <= _used,
      f"a note under a marker nothing offers: "
      f"{sorted(set(prismash.HINT_NOTES) - _used)}")
print("  the shapes, on the screen somebody is looking at while they type")

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
for block in ("user noc", "snmp-user alice", "nat|cgnat|pool p",
              "interface TenGigabitEthernet0/8/0"):
    for _step in block.split("|"):
        sh.feed(_step)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sh.feed("exit")
    said = out.getvalue().strip()
    check(said == "", f"leaving '{block}' printed: {said!r}")
    check(sh.mode == "config",
          f"leaving '{block}' left mode {sh.mode!r}, not config")
    sh.feed("top")
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
    sh.frr = type("F", (prismash.Frr,), {"available": lambda s: False,
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
sh.feed("nat")
sh.feed("cgnat")
sh.feed("interface Gig9 inside")
check("no such interface" in commit_check(sh),
      "'interface' under cgnat is checked the same way")

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
# local0 is NOT listed, and that reverses what this box used to assert. It was
# listed on the argument that hiding an interface the dataplane reports is the
# command deciding what the operator may see; the maintainer read the screen on
# a real box and asked for it to go (#403). The argument holds for interfaces
# the box HAS - `local0` is an artefact of VPP rather than a port on the
# machine, and it takes no configuration of any kind.
check("local0" not in text,
      f"and not VPP's null interface, which configures nothing: {text!r}")
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
# BY EQUALITY, not endswith("> "): "prisma> " is the needle lab/boot-iso.sh
# anchors on and lab/install-iso.sh waits for eight times, and both need a
# booted box to notice. Under the endswith a prompt still reading
# "prisma-routing> " passed this suite and would have timed out every lab (#202).
check(sh.prompt() == "prisma> ",
      f"operational mode is the prompt the labs wait for: {sh.prompt()!r}")
sh.feed("configure")
check(sh.prompt() == "prisma# [edit] ", f"top of config: {sh.prompt()!r}")

sh.feed("nat")
sh.feed("cgnat")
sh.feed("pool residential")
check(sh.prompt() == "prisma# [edit nat / cgnat / pool residential] ",
      f"a pool names the pool, and the levels it is in: {sh.prompt()!r}")
sh.feed("top")

sh.feed("interface TenGigabitEthernet0/8/0")
check("interface TenGigabitEthernet0/8/0" in sh.prompt(),
      f"an interface names the interface: {sh.prompt()!r}")
print(f"  {sh.prompt().strip()}")

# An 80-column serial console is what a racked box is seen through.
#
# Each context entered, and its own prompt measured. The loop that used to be
# here had `pass` for a body: it entered nothing, and the one prompt the check
# below read was whichever the shell happened to be left holding - so the pool
# prompt's width, which is the shorter of the two and the one a longer pool
# name would grow, was never looked at.
sh.feed("exit")
for probe in ("nat|cgnat|pool residential",
              "nat|cgnat|pool " + "r" * 63,
              "interface TenGigabitEthernet0/8/0"):
    for _step in probe.split("|"):
        sh.feed(_step)
    check(len(sh.prompt()) <= 60,
          f"the prompt in {probe[:40]!r} is {len(sh.prompt())} columns; a "
          f"racked box has 80 and a prompt that pushes the cursor off the "
          f"line is worse than a vague one")
    sh.feed("top")

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

def this_in_raw(block, *typed):
    """`show this` inside `block` after `typed`, exactly as it printed."""
    s = shell_with_box()
    s.feed("configure")
    s.feed(block)
    for line in typed:
        s.feed(line)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.feed("show this")
    return buf.getvalue().splitlines()


def this_in(block, *typed):
    """The same, with the boundary marking taken back off."""
    return strip_marks(this_in_raw(block, *typed))


def strip_marks(lines):
    """
    The same display with the boundary marking taken back off (#141).

    These cases are about WHICH statements a block shows and in what shape,
    and the marker is presentation laid over that. Stripped here so one set of
    assertions keeps answering the question it was written for - and stripped
    with the product's own constants, so a marker that changed spelling
    without the parser changing with it fails somewhere rather than being
    quietly tolerated here.
    """
    from config import UNCHECKED_MARK, UNCHECKED_NOTE       # noqa: E402
    out = []
    for l in lines:
        if l.strip().startswith(UNCHECKED_NOTE):
            continue
        out.append(l[:-len(UNCHECKED_MARK)] if l.endswith(UNCHECKED_MARK)
                   else l)
    return out

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
    ("router static", ["0.0.0.0/0 next-hop 10.0.0.2",
                       "192.0.2.0/24 next-hop 10.0.0.3"],
     [" 0.0.0.0/0 next-hop 10.0.0.2", " 192.0.2.0/24 next-hop 10.0.0.3"]),
]
for block, typed, expected in TYPED:
    lines = this_in(block, *typed)
    check(lines[:1] == [block], f"{block}: the header is still first: {lines}")
    for want in expected:
        check(want in lines[1:],
              f"{block}: '{want.strip()}' is shown as a statement of it: {lines}")
    print(f"  {block}: " + " / ".join(l.strip() for l in lines[1:]))

# FRR's spelling typed INSIDE the block is refused since #436: the block has
# one statement, by the maintainer's decision not to carry a second spelling.
# FRR's own `ip route` still works where FRR's words belong - at [edit] and
# inside a vrf - and a route FRR reads back is shown in the block's words.
lines = this_in("router static", "ip route 198.51.100.0/24 blackhole")
check(lines == ["router static"],
      f"FRR's own spelling typed in the block is not staged: {lines}")

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
sh.feed("0.0.0.0/0 next-hop 10.0.0.2")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.feed("show this")
after = buf.getvalue().splitlines()
check(" 0.0.0.0/0 next-hop 10.0.0.2" in after,
      f"a route typed after leaving a block is in the static block: {after}")
# And not only on the screen: the file the box would save has to put it there
# too, or `show this` and the commit are two answers to one question.
check(" 0.0.0.0/0 next-hop 10.0.0.2" in sh.candidate.text().splitlines(),
      f"and the saved file agrees: {sh.candidate.text()}")
# ...and it is not routing an operator wrote, so it must not make a candidate
# that only sets an interface MTU look like one that needs FRR (#137).
check(prismash.Shell.real_routing(["interface Gi0", "exit"]) == [],
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
from prismash import mark_unchecked                                # noqa: E402
from config import (Config, UNCHECKED_MARK, UNCHECKED_NOTE,      # noqa: E402
                    CONSOLE_COLUMNS)

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
check(any(l.endswith(UNCHECKED_MARK) for l in shown if " tag " in l),
      f"the unmodelled statement is marked: {[l for l in shown if 'tag' in l]}")
check(not any("<-" in l for l in shown if "local-preference" in l),
      "and a modelled one is not")
check(UNCHECKED_MARK not in cfg.text() and UNCHECKED_NOTE not in cfg.text(),
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
from prismash import as_path_matches, undefined_policy_lists   # noqa: E402

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
from prismash import bgp_errors                                    # noqa: E402

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
from prismash import route_map_findings                            # noqa: E402

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

# `set local-preference`, where THIS VTY quotes a range and FRR does not have
# one. Measured on FRR 10.7.1: `list` inside a route-map declares `set
# local-preference WORD`, vtysh takes `set local-preference banana`,
# `frr-reload.py --test` - which is what `commit check` runs - exits 0 on it,
# and bgpd's own running configuration carries the line back verbatim. The
# clause then sets nothing, which is the "looks right and does nothing" shape
# the rest of this function exists for (#354).
#
# The range in the hint is right and was never the question: local preference
# is a 32-bit unsigned in the protocol. What was missing is anything that held
# the statement to it.
for _bad in ("banana", "-1", "4294967296"):
    _lp = route_map_findings(["route-map LP permit 10",
                              " match as-path AS1",
                              f" set local-preference {_bad}"])
    check([f for f in _lp if f[0] == "error" and _bad in f[1]],
          f"'set local-preference {_bad}' must be refused, not committed "
          f"for FRR to drop on the floor: {_lp}")
    check([f for f in _lp if "0-4294967295" in f[2]],
          f"and the finding names the range the hint quotes: {_lp}")
for _good in ("0", "100", "4294967295"):
    _lp = route_map_findings(["route-map LP permit 10",
                              " match as-path AS1",
                              f" set local-preference {_good}"])
    check(not [f for f in _lp if f[0] == "error"],
          f"{_good} is inside the range and must not be refused: {_lp}")
print("  'set local-preference banana' is an error, 0 and 4294967295 are not")

# AND A BOX THAT ALREADY HAS ONE IS NOT LOCKED OUT (#358).
#
# `set local-preference banana` was accepted at this prompt and committed
# until the check above existed, and FRR stores it, so there are boxes
# carrying one. A refusal that fires on what the box ALREADY has makes every
# commit on that box fail and makes `--load` refuse the saved file whole at
# boot - a box that was working comes up with nothing on it. That is the trap
# the reference check beside it has been guarded against since #145, and this
# one needed the same guard the moment it gained a value check.
#
# Driven through the commit path rather than through the finding, because the
# finding is right either way: what changes is whether the commit is refused.
_rm_running = ["route-map LP permit 10", " match as-path AS1",
               " set local-preference banana"]
_rm_target = _rm_running + ["route-map OTHER permit 10", " match as-path AS2"]
_already = {stmt for sev, stmt, _ in route_map_findings(_rm_running)
            if sev == "error"}
_new = [stmt for sev, stmt, _ in route_map_findings(_rm_target)
        if sev == "error" and stmt not in _already]
check(not _new,
      f"a statement FRR already carries is counted as new, so every commit on "
      f"that box would be refused and its saved file would not load: {_new}")
_rm_fresh = ["route-map LP permit 10", " match as-path AS1",
             " set local-preference banana"]
_new_fresh = [stmt for sev, stmt, _ in route_map_findings(_rm_fresh)
              if sev == "error" and stmt not in set()]
check(_new_fresh,
      f"a box that did not have it and is being given it now is not refused, "
      f"which is the whole point of the check: {_new_fresh}")
print("  a box that already carries one can still be configured, and a new "
      "one is still refused")


# --- no input may put a traceback in front of an operator (#208) ---
#
# `bgp community-list standard`, with the name not typed yet, raised
# IndexError: a Python traceback in a maintenance window is not a refusal, it
# is a crash. Three hand-picked strings would not have found it and will not
# find the next one, so every statement these functions parse is truncated at
# every word AND every character, in every context, and fed back in.
print("\n=== every truncation of every statement, and no traceback ===")
from prismash import (as_path_parts, as_path_regex_error,          # noqa: E402
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
MANY = [lambda r: prismash.bgp_errors(r, HAVE, LCP, DPNAMES),
        prismash.bgp_errors, undefined_policy_lists, route_map_findings,
        rpki_findings, isis_findings, ospf_state,
        # The #145 refusals read the same half-typed lines as everything
        # above, and one of them REFUSES a commit - so a traceback from a
        # truncated statement would put Python in front of the operator
        # instead of the refusal.
        prismash.route_map_reference_findings,
        prismash.advertisement_fingerprint,
        lambda r: prismash.advertised_out(r, "203.0.113.0/24"),
        lambda r: prismash.route_map_verdict(r, "OUT", "203.0.113.0/24"),
        lambda r: prismash.prefix_list_verdict(r, "PL", "203.0.113.0/24"),
        lambda r: prismash.session_cut_findings(r, r, "203.0.113.5")]
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
    prismash.Shell.print_config(cfg)
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
    prismash.Shell.print_config(cfg)
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
from prismash import ospf_findings, ospf_state                     # noqa: E402
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
from prismash import isis_findings                                 # noqa: E402

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
    probe = prismash.Shell.__new__(prismash.Shell)
    probe.STARTUP_CONF, probe.TUNE_ENV = c, e
    return probe.why_no_dataplane_interface()


# THE MEDIUM AND AN INSTALLED BOX WRITE THE SAME STARTUP FILE, and until #305
# this answered both with the same advice: reboot and wait for the tuning.
#
# True of an installed box's first boot, where the tuner has asked for pages a
# reboot picks up. False on the medium, where the dataplane is off deliberately
# and permanently: nothing there survives a reboot, so the tuning is never
# applied and the reboot comes back to the same screen having thrown the
# session away. The medium's own banner says so two lines above the command.
#
# Driven both ways from ONE startup file, because that file is what the product
# was deciding from and the whole defect is that it is not enough to decide on.
def why_on_medium(conf):
    """`why` with the box looking like the live medium, and put back after."""
    live = os.path.join(_d, "filesystem.squashfs")
    inst = os.path.join(_d, "prisma-routing-install")
    open(live, "w").write("")
    open(inst, "w").write("#!/bin/sh\n")
    os.chmod(inst, 0o755)
    keep = (prismash.LIVE_SQUASHFS, prismash.INSTALLER)
    prismash.LIVE_SQUASHFS, prismash.INSTALLER = live, inst
    try:
        return why(conf)
    finally:
        prismash.LIVE_SQUASHFS, prismash.INSTALLER = keep


m = why_on_medium("plugin dpdk_plugin.so { disable }")
check(m and not any("Reboot" in l or "reboot once" in l.lower() for l in m),
      f"the medium is not told to reboot, which throws its session away "
      f"and comes back to the same screen: {m}")
check(any("install" in l.lower() for l in m),
      f"and it is told what does work there, which is to install to a disk: "
      f"{m}")
check(any("survives" in l or "does not change that" in l for l in m),
      f"and why a reboot is not the answer on a medium: {m}")

r = why("plugin dpdk_plugin.so { disable }")
check(r and "hugepages" in r[0],
      f"first-boot mode is named as first-boot mode: {r[:1]}")
check(any("Reboot" in l for l in r),
      "and says what to do about it")

r = why("dpdk {\n  no-pci\n}", "PRISMA_DATAPLANE_IF=\n")
check(r and "no interface was named" in r[0],
      f"an empty PRISMA_DATAPLANE_IF is named as the cause: {r[:1]}")
# The COMMAND, not the file. Until #283 there was no way to name the card that
# was not an editor in Linux, so this advice named a path; now the box has a
# command for it, and advice that still sends the operator to Linux is advice
# that contradicts the feature. The words are the boot self-test's, on purpose.
# Under `system` > `dataplane` since #457, and named in those words.
check(any("'system', 'dataplane'" in l for l in r)
      and any("'interface <name>'" in l for l in r),
      f"the statement that gives the box a card is not named: {r!r}")
check(any("commit" in l for l in r),
      f"and nothing says the change has to be committed: {r!r}")
check(not any("tune.env" in l for l in r),
      f"it still sends the operator to a file in Linux: {r!r}")

r = why("dpdk {\n  no-pci\n}", 'PRISMA_DATAPLANE_IF="ens19"\n')
check(r and "ens19" in r[0],
      f"a named interface the dataplane was not given is quoted back: {r[:1]}")
# The three reasons a named interface produces no-pci are different work, so
# all three are offered rather than one guessed at. The third used to be the
# tuner's refusal of the card carrying the default route; #385 removed that
# refusal, so the reason a named card can still be left with Linux is a way
# back the tuner could not read off it.
check(any("way back" in l for l in r),
      f"the third reason - the way back could not be read - is not offered: "
      f"{r!r}")
check(not any("refused" in l for l in r),
      f"a refusal the tuner no longer makes is still offered as the reason: "
      f"{r!r}")

# A NIC that was named and did not appear. This used to end "the usual cause
# is the NIC still bound to a kernel driver" - a guess printed as a finding,
# and the box can simply look (#283). Nothing in appliance/ binds a NIC or
# loads vfio-pci, so an operator who did what the box told them lands exactly
# here, and the three states below need different work and read identically
# without this.


def _nic_box(nics, default, conf):
    """A fake box with `nics`, `default` carrying the default route."""
    d = tempfile.mkdtemp()
    os.makedirs(f"{d}/proc/net")
    os.makedirs(f"{d}/etc/vpp")
    os.makedirs(f"{d}/sys/class/net/lo")
    for i, n in enumerate(nics):
        dev = f"{d}/sys/devices/pci0000:00/0000:00:1{i}.0/virtio{i}"
        os.makedirs(dev)
        os.makedirs(f"{d}/sys/class/net/{n}")
        os.symlink(dev, f"{d}/sys/class/net/{n}/device")
        open(f"{d}/sys/class/net/{n}/address", "w").write(f"bc:24:11:0:0:{i}\n")
        open(f"{d}/sys/class/net/{n}/operstate", "w").write("down\n")
    # Eleven fields, which is what /proc/net/route really has: the reader
    # requires more than two, so a shorter fixture measures a box with no
    # default route at all and every assertion below would be about the wrong
    # thing. Found by writing a two-field one first.
    rt = ("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\t"
          "Mask\tMTU\tWindow\tIRTT\n")
    if default:
        rt += (f"{default}\t00000000\t0102000A\t0003\t0\t0\t0\t"
               "00000000\t0\t0\t0\n")
    open(f"{d}/proc/net/route", "w").write(rt)
    open(f"{d}/etc/vpp/startup.conf", "w").write(conf)
    probe = prismash.Shell.__new__(prismash.Shell)
    probe.STARTUP_CONF = f"{d}/etc/vpp/startup.conf"
    probe.TUNE_ENV = f"{d}/etc/prisma-routing/tune.env"
    probe.SYS_CLASS_NET = f"{d}/sys/class/net"
    probe.PROC_NET_ROUTE = f"{d}/proc/net/route"
    probe.PCI_DEVICES = f"{d}/sys/bus/pci/devices"
    probe.PCI_DRIVERS = f"{d}/sys/bus/pci/drivers"
    return probe


def why_pci(driver=None, loaded=()):
    """The same explanation, with a PCI tree Linux would have."""
    dev = os.path.join(_d, "pci", "devices", "0000:01:00.0")
    drv = os.path.join(_d, "pci", "drivers")
    shutil.rmtree(os.path.join(_d, "pci"), ignore_errors=True)
    os.makedirs(dev)
    os.makedirs(drv)
    for d in loaded:
        os.makedirs(os.path.join(drv, d))
    if driver:
        target = os.path.join(drv, driver) if driver in loaded else \
                 os.path.join(_d, "pci", driver)
        os.makedirs(target, exist_ok=True)
        os.symlink(target, os.path.join(dev, "driver"))
    c = os.path.join(_d, "startup.conf")
    open(c, "w").write("dpdk {\n  dev 0000:01:00.0\n}")
    probe = prismash.Shell.__new__(prismash.Shell)
    probe.STARTUP_CONF, probe.TUNE_ENV = c, os.path.join(_d, "tune.env")
    probe.PCI_DEVICES = os.path.join(_d, "pci", "devices")
    probe.PCI_DRIVERS = drv
    return probe.why_no_dataplane_interface()


import shutil                                                    # noqa: E402

r = why_pci(driver="ixgbe")
check(r and "0000:01:00.0" in r[0],
      f"the address the box was told to use is quoted back: {r[:1]}")
check(any("'ixgbe'" in l for l in r),
      f"the driver actually holding the NIC is named, not guessed at: {r}")
check(any("none of vfio-pci" in l for l in r),
      f"and a box with no DPDK driver loaded is told that: {r}")

r = why_pci(driver="ixgbe", loaded=("vfio-pci",))
check(any("vfio-pci is loaded" in l and "nothing has bound" in l for l in r),
      f"a loaded driver with nothing bound to it is a different answer, "
      f"because it is different work: {r}")
check(not any("none of vfio-pci" in l for l in r),
      f"and it does not also say no driver is loaded: {r}")

r = why_pci(driver="vfio-pci", loaded=("vfio-pci",))
check(any("binding is not the problem" in l for l in r),
      f"a NIC already bound for DPDK is told the binding is not the cause, "
      f"so an operator who has done that work is not sent to do it again: {r}")
check(any("journal" in l for l in r),
      f"and pointed somewhere this cannot see: {r}")

r = why_pci(driver=None)
check(any("No driver is bound to it at all" in l for l in r),
      f"a device with no driver at all is its own answer: {r}")

# A box that is not an appliance has no startup.conf, and inventing an
# explanation for it would be worse than saying nothing.
probe = prismash.Shell.__new__(prismash.Shell)
probe.STARTUP_CONF = os.path.join(_d, "does-not-exist")
probe.TUNE_ENV = os.path.join(_d, "nor-this")
check(probe.why_no_dataplane_interface() == [],
      "and a box with no startup configuration explains nothing")

# --- `show hardware`: the NICs the box HAS, before VPP has any (#283) ---
#
# On a fresh appliance VPP has been given no NIC, so `show interface` is empty
# - and that is exactly when an operator needs to see what there is to choose
# from. The only way to that list was to leave the VTY and read /sys, which
# for an appliance is not an answer.
#
# Driven against a /sys tree built here, through `sh.feed("show hardware")`,
# so what is asserted is what the command prints and not what a stub was told
# to return.
print("\n=== show hardware: the box's own NICs ===")

_HW_EMPTY_DP = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)
local0                            0     down            0/0/0/0
"""
_HW_HELD_DP = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)
TenGigabitEthernet0/8/0           1      up          9000/0/0/0
local0                            0     down            0/0/0/0
"""


def hw_tree(root, nics, default=None):
    """
    A /sys/class/net and a /proc/net/route the way Linux writes them.

    `device` is a symlink to the PCI device and `device/driver` a symlink to
    the driver holding it, because that is the shape the product reads: a
    directory with the names in it would pass a test the box fails.
    """
    net = os.path.join(root, "sys", "class", "net")
    for name, pci, driver, state, mac, speed in nics:
        d = os.path.join(net, name)
        os.makedirs(d)
        if pci:
            dev = os.path.join(root, "sys", "devices", "pci0000:00", pci)
            os.makedirs(dev, exist_ok=True)
            os.symlink(dev, os.path.join(d, "device"))
            if driver:
                drv = os.path.join(root, "sys", "bus", "pci", "drivers",
                                   driver)
                os.makedirs(drv, exist_ok=True)
                os.symlink(drv, os.path.join(dev, "driver"))
        open(os.path.join(d, "operstate"), "w").write(state + "\n")
        open(os.path.join(d, "address"), "w").write(mac + "\n")
        if speed is not None:
            open(os.path.join(d, "speed"), "w").write(str(speed) + "\n")
    os.makedirs(net, exist_ok=True)
    proc = os.path.join(root, "proc", "net")
    os.makedirs(proc)
    rows = ["Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask"
            "\t\tMTU\tWindow\tIRTT"]
    # A connected route for every interface FIRST, and the default route last.
    # Destination 00000000 is what makes a row the default route, and a
    # reading that took the first row naming an interface would be right on a
    # one-line file and wrong on every real box. This file is ordered so that
    # such a reading names the WRONG NIC rather than passing by luck.
    for name, pci, driver, state, mac, speed in reversed(nics):
        rows.append(f"{name}\t0002000A\t00000000\t0001\t0\t0\t0"
                    f"\t00FFFFFF\t0\t0\t0")
    if default:
        rows.append(f"{default}\t00000000\t0102000A\t0003\t0\t0\t0"
                    f"\t00000000\t0\t0\t0")
    open(os.path.join(proc, "route"), "w").write("\n".join(rows) + "\n")
    return net, os.path.join(proc, "route")


def flat(text):
    """
    The screen with the console wrapping taken back out.

    A sentence this VTY prints is wrapped to fit the console, so where it
    breaks is a measurement and not a promise. Matching against the wrapped
    text asserts the break position by accident, and goes red the day a word
    changes length - which is how a check that was worth having gets deleted.
    """
    return " ".join(text.split())


def show_hw(nics, default=None, dp_text=_HW_EMPTY_DP, dp_rc=0,
            conf="dpdk {\n  no-pci\n}\n", env="PRISMA_DATAPLANE_IF=\n",
            binder=None):
    """
    What `show hardware` prints for a box with those NICs.

    `binder` installs a stub on `Shell.BIND_NIC`: a string is what it prints
    for `--which-driver`, and None leaves the path missing, which is the box
    where the VTY cannot ask. Without this seam the only sentence these checks
    ever saw was the one that says it could not ask - the answer, which is the
    whole of #328, was never rendered.
    """
    root = tempfile.mkdtemp()
    net, route = hw_tree(root, nics, default)
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT}

    def run(_self, cmd):
        if cmd.startswith("show interface"):
            return dp_rc, dp_text
        return 0, ""

    sh.dp = type("D", (), {"TRACE": False, "run": run,
                           "reachable": lambda _s: dp_rc == 0})()
    sh.frr = type("F", (prismash.Frr,), {"available": lambda s: False,
                                       "running": lambda s: [],
                                       "show": lambda s, c: (1, "")})()
    sh.SYS_CLASS_NET, sh.PROC_NET_ROUTE = net, route
    c, e = os.path.join(root, "startup.conf"), os.path.join(root, "tune.env")
    open(c, "w").write(conf)
    open(e, "w").write(env)
    sh.STARTUP_CONF, sh.TUNE_ENV = c, e
    if binder is None:
        sh.BIND_NIC = os.path.join(root, "no-such-binder")
    else:
        b = os.path.join(root, "bind-dataplane-nic")
        with open(b, "w") as f:
            f.write(f'#!/bin/sh\n[ "$1" = "--which-driver" ] || exit 1\n'
                    f'echo {binder!r}\n')
        os.chmod(b, 0o755)
        sh.BIND_NIC = b
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sh.feed("show hardware")
    return buf.getvalue()


# A box with two NICs: one carrying the default route, one idle. `lo` is in
# the tree because it is in every tree, and it is not a candidate.
_TWO = [("eno1", "0000:03:00.0", "ixgbe", "up", "3c:ec:ef:1a:2b:3c", 1000),
        ("ens1f0", "0000:5e:00.0", "ixgbe", "down", "b4:96:91:0d:1e:2f", -1),
        ("lo", None, None, "unknown", "00:00:00:00:00:00", None)]
_out = show_hw(_TWO, default="eno1")

check("eno1" in _out and "ens1f0" in _out,
      f"both NICs are listed: {_out}")
check("0000:03:00.0" in _out and "0000:5e:00.0" in _out,
      f"each with the PCI address the tuner would have to be given: {_out}")
check("ixgbe" in _out,
      f"and the driver holding it now: {_out}")
check("3c:ec:ef:1a:2b:3c" in _out and "b4:96:91:0d:1e:2f" in _out,
      f"the MAC is printed whole, so it is greppable against a port "
      f"label: {_out}")
check("1000Mb/s" in _out,
      f"a link that reports a speed shows it: {_out}")
# -1 is what the kernel writes for a link that is down. A negative megabit
# printed as a number is a number somebody reads past.
check("-1Mb/s" not in _out,
      f"and one that does not is not shown a negative speed: {_out}")

# AND THE COLUMNS DO NOT RUN TOGETHER, on every state the kernel can write.
#
# The Link column was seven wide and `unknown` is seven characters, so a card
# in that state printed `unknown-` and `unknown1000Mb/s` with no space at all
# (#347). It is not an odd value: host_nics() falls back to it when operstate
# cannot be read, and the kernel writes it for any netdev whose driver sets no
# carrier - every card on a box nobody has tuned yet.
#
# Driven over the whole closed set RFC 2863 defines rather than over the
# states this fixture happens to carry, because the box that meets
# `lowerlayerdown` is somebody else's. What is asserted is the SPEED's column,
# not the width of the state: a row whose speed starts at a different offset
# from the header's is a table nobody can read down.
_STATES = ("up", "down", "unknown", "testing", "dormant", "notpresent",
           "lowerlayerdown")
_cols = set()
for _st in _STATES:
    _o = show_hw([("eno1", "0000:03:00.0", "ixgbe", _st, "3c:ec:ef:1a:2b:3c",
                   1000)], default="eno1")
    _row = [l for l in _o.splitlines() if l.startswith("  eno1 ")]
    check(len(_row) == 1,
          f"the row for a card in state {_st!r} was not printed, so nothing "
          f"below measures it: {_o!r}")
    if _row:
        check(f"{_st}1000Mb/s" not in _row[0],
              f"the state and the speed run together for {_st!r}, with no "
              f"space between the two columns: {_row[0]!r}")
        _cols.add(_row[0].index("1000Mb/s"))
_hdr = [l for l in show_hw(_TWO, default="eno1").splitlines()
        if "Speed" in l]
check(len(_cols) == 1,
      f"the speed starts in a different column depending on the state: "
      f"{sorted(_cols)}")
check(_hdr and _hdr[0].index("Speed") in _cols,
      f"the header's Speed is at {_hdr[0].index('Speed') if _hdr else None} "
      f"and the rows put theirs at {sorted(_cols)}")
check("\n  lo " not in _out and "00:00:00:00:00:00" not in _out,
      f"the loopback has no PCI address and is not a candidate: {_out}")
check("up" in _out and "down" in _out,
      f"the link state of each is there: {_out}")

# The mark is the point of this screen as much as the list is. The card
# carrying the default route is the one this box is reached on, and what
# happens to it when the dataplane takes it - the address moves onto the pair
# - is the one thing an operator has to know before typing (#385).
check("DEFAULT ROUTE" in _out,
      f"the NIC carrying the default route is marked: {_out}")
_lines = _out.splitlines()
_marked = [i for i, l in enumerate(_lines) if "DEFAULT ROUTE" in l]
_rows = {i: l.split()[0] for i, l in enumerate(_lines)
         if l.startswith("  ") and not l.startswith("   ")
         and l.split() and l.split()[0] in ("eno1", "ens1f0")}
check(len(_marked) == 1 and
      max([i for i in _rows if i < _marked[0]], default=None) is not None and
      _rows[max(i for i in _rows if i < _marked[0])] == "eno1",
      f"and it belongs to eno1's block, not the other NIC's: {_out}")
check("'eno1' carries the default route" in flat(_out) and
      "moves onto the linux_cp pair" in flat(_out),
      f"and the screen says what taking that card means, which is where the "
      f"address goes: {_out}")
# AND NOT THAT IT IS REFUSED. The screen said "Naming it by hand with
# 'dataplane interface' is refused while this box has another card to name
# instead" until #385 removed that refusal; a screen that goes on saying so
# sends the operator away from a statement the box accepts.
# The needle is cut from the sentence that went, not from the word "refused":
# the same screen still says a name this box has not got is refused, which is
# the refusal that stays.
check("is refused while this box has another card" not in flat(_out)
      and "off the network" not in flat(_out),
      f"the screen still describes the refusal #385 removed: {_out}")

# The driver verdict, which is the other half of "can I give this away".
check("kernel driver" in _out and "DPDK cannot" in _out,
      f"a kernel driver is named as one DPDK cannot take a NIC "
      f"through: {_out}")
_dpdk = show_hw(
    [("eno1", "0000:03:00.0", "ixgbe", "up", "3c:ec:ef:1a:2b:3c", 1000),
     ("ens1f0", "0000:5e:00.0", "vfio-pci", "up", "b4:96:91:0d:1e:2f", 10000)],
    default="eno1")
check("vfio-pci is a driver DPDK can use" in flat(_dpdk),
      f"and a NIC already bound for DPDK is told so: {_dpdk}")
check("no driver is bound to it at all" in
      flat(show_hw([("ens1f0", "0000:5e:00.0", None, "down",
                     "b4:96:91:0d:1e:2f", None)])),
      "a device with no driver is its own answer, the way the diagnosis "
      "above says it")

# What the DATAPLANE holds, said once and before the list, because a NIC VPP
# owns is not under /sys/class/net at all and a list that quietly omits it
# reads as a missing NIC - which is a hardware fault, not this one.
check("no interface was named" in flat(_out),
      f"the dataplane's state is stated, in the words the rest of the box "
      f"uses for it: {_out}")
_held = show_hw(_TWO, default="eno1", dp_text=_HW_HELD_DP)
check("the dataplane holds TenGigabitEthernet0/8/0" in flat(_held),
      f"a box whose dataplane already has a NIC is told which: {_held}")
check("no longer listed by Linux" in flat(_held),
      f"and told that such a NIC is not in the list below: {_held}")
_deaf = show_hw(_TWO, default="eno1", dp_rc=1)
check("the dataplane is not answering" in flat(_deaf),
      f"and a dataplane that cannot be asked is said to be that, not "
      f"reported as empty: {_deaf}")

# THE CHECK GOING BLIND. A tree with no NIC in it must not read as a healthy
# box with a short list: "nothing to show" and "nothing is wrong" look
# identical on a screen, and this screen is read on a box that forwards
# nothing.
_none = show_hw([])
check("no PCI NIC is visible" in flat(_none),
      f"a box where Linux has no PCI NIC says so: {_none}")
check("Interface" not in _none and "DEFAULT ROUTE" not in _none,
      f"and prints no table, which would read as a box that is fine: {_none}")

# AND THE SENTENCE IS TRUE. This one told the operator that a virtio guest is
# what "no PCI address" looks like, which #286 made false: pci_address_of()
# walks up to the PCI parent, so a virtio card in a VM resolves and is
# eligible. The audience most likely to reach this screen is somebody trying
# the product in a VM, and it was telling exactly them that their box had
# nothing to offer. A wrong sentence on an empty table ends the search, which
# is worse than the empty table.
check("eligible" in flat(_none) and "does resolve" in flat(_none),
      f"the empty-table sentence no longer says a virtio card in a VM is "
      f"eligible, so a VM operator is back to reading that their hardware is "
      f"unusable: {_none}")
check("nothing here to choose from" not in flat(_none),
      f"the empty table still ends with 'nothing here to choose from', which "
      f"is the dead end #328 is about: {_none}")

# AND IT IS TRUE OF THIS BOX, not of one of the three that reach it. The
# sentence offered the reader a choice - "either the dataplane already owns
# every one of them, WHICH THE LINES ABOVE SAY, or this box genuinely has no
# card Linux can see" - and pointed at the block printed six lines earlier to
# settle it. That block settles it on one box out of three. On the other two
# it has just said the dataplane holds nothing, or that it could not be asked
# at all, so the screen contradicted itself and then asked the operator to
# decide between two answers it already had. Same shape as #331, where the
# PCI-address line said "(not a PCI device)" two lines under a line saying no
# card had been named.
#
# Three boxes, three sentences, and the one the old wording was right for
# keeps its answer.
_none_held = show_hw([], dp_text=_HW_HELD_DP)
check("the dataplane owns them" in flat(_none_held),
      f"a box whose dataplane holds every card is no longer told that is "
      f"what an empty list means: {_none_held}")
check("the dataplane has none either" in flat(_none),
      f"a box where the dataplane answered and holds nothing is not told so "
      f"under the empty list - it is offered 'the dataplane owns them' as a "
      f"possibility the line above has already ruled out: {_none}")
check("the dataplane owns them" not in flat(_none),
      f"a box whose dataplane says it holds nothing is told the dataplane "
      f"owns the cards, three lines under the line saying it holds none: "
      f"{_none}")
_none_deaf = show_hw([], dp_rc=1)
check("could not be asked" in flat(_none_deaf)
      and "read alike here" in flat(_none_deaf),
      f"a box whose dataplane could not be asked is not told the two cases "
      f"read alike, so the empty list reads as an answer: {_none_deaf}")
check("the dataplane owns them" not in flat(_none_deaf)
      and "has none either" not in flat(_none_deaf),
      f"a box that could not ask the dataplane anything is told what it "
      f"holds: {_none_deaf}")
# The half of the sentence that is about Linux and not about the dataplane is
# true on all three, so it is printed on all three.
for _label, _screen in (("held", _none_held), ("empty", _none),
                        ("deaf", _none_deaf)):
    check("eligible" in flat(_screen) and "does resolve" in flat(_screen),
          f"the virtio sentence #328 added is missing from the {_label} box, "
          f"which is the one audience most likely to read this screen: "
          f"{_screen}")

# THE ANSWER, not just the apology for not having one (#328). The screen names
# the driver the box will move the card to, and it names it by ASKING
# appliance/bind-dataplane-nic, which is where that rule already lives. Until
# this seam existed every check here rendered the "could not ask" sentence,
# so the sentence that matters had no assertion at all.
_VIRTIO = [("ens18", "0000:00:12.0", "virtio_net", "up", "bc:24:11:aa:bb:cc",
            None),
           ("lo", None, None, "unknown", "00:00:00:00:00:00", None)]
_iommu = flat(show_hw(_VIRTIO, default="ens18",
                      binder="vfio-pci through the IOMMU"))
check("vfio-pci through the IOMMU" in _iommu,
      f"a card on a kernel driver is not told which driver the box will move "
      f"it to, so the screen still ends where #328 said it ended: {_iommu}")

# And the OTHER answer, because a box without IOMMU groups gets a different
# driver and being told vfio-pci there is worse than being told nothing: the
# operator goes looking for an IOMMU that the binder already decided to do
# without.
_nommu = flat(show_hw(_VIRTIO, default="ens18",
                      binder="uio_pci_generic without IOMMU protection"))
check("uio_pci_generic without IOMMU protection" in _nommu
      and "vfio-pci" not in _nommu,
      f"the screen does not repeat what the binder answered, so it is "
      f"deriving the driver from a second copy of the rule: {_nommu}")

# And a box where the binder cannot be reached says so rather than guessing.
# A named driver that turns out wrong costs more than an admitted unknown.
_noask = flat(show_hw(_VIRTIO, default="ens18"))
check("could not ask" in _noask and "vfio-pci" not in _noask
      and "uio_pci_generic" not in _noask,
      f"with no binder to ask, the screen named a driver anyway: {_noask}")

# A box with NICs and no default route at all. Saying nothing here would leave
# the operator to conclude the mark was simply missed.
_nodef = show_hw(_TWO)
check("no interface carries a default route" in flat(_nodef),
      f"no default route anywhere is said, not left as an absent mark: "
      f"{_nodef}")
check("DEFAULT ROUTE" not in _nodef,
      f"and nothing is marked: {_nodef}")

# The management path can be something this list cannot hold - a bridge, a
# bond, a virtio NIC with no PCI address. An unmarked list would read as "none
# of these is the management path", and that reading is the one that costs the
# box, so it is said instead.
_elsewhere = show_hw(
    [("ens1f0", "0000:5e:00.0", "ixgbe", "up", "b4:96:91:0d:1e:2f", 10000),
     ("br0", None, None, "up", "3c:ec:ef:1a:2b:3c", None)], default="br0")
check("none of these carries the default route" in flat(_elsewhere)
      and "'br0'" in flat(_elsewhere),
      f"a default route on something that is not a PCI NIC is named, not "
      f"left as an absent mark: {_elsewhere}")

# Every line inside the console. The widest row is the one with the longest
# interface and driver names, so it is the one measured.
_wide_hw = show_hw(
    [("enp94s0f0np0", "0000:5e:00.0", "uio_pci_generic", "up",
      "b4:96:91:0d:1e:30", 25000)], default="enp94s0f0np0")
_over = [l for l in (_out + _wide_hw + _none + _held).splitlines()
         if len(l) > CONSOLE_COLUMNS]
check(not _over, f"every line fits {CONSOLE_COLUMNS} columns: {_over}")
# On the ROW, not merely somewhere on the screen: the sentences under the
# table name the driver and the interface too, so a row whose columns cut
# them to fit would leave this green while the table lied.
_row = [l for l in _wide_hw.splitlines() if l.startswith("  enp94")]
check(len(_row) == 1 and "uio_pci_generic" in _row[0]
      and "enp94s0f0np0" in _row[0] and "0000:5e:00.0" in _row[0],
      f"and a long name and a long driver are whole on the row, not cut to "
      f"the column: {_row}")

# In the tree, so `?` and TAB offer it. A command an operator can only find by
# reading the source is a command that does not exist.
_o, _hint, _ = prismash.Shell()._options("show ", False)
check("hardware" in _o, f"'show hardware' is offered by '?': {sorted(_o)}")



# --- filtering a show, without leaving the VTY (#191) ---
print("\n=== pipe filters ===")
from prismash import apply_pipe, split_pipe, PIPE_FILTERS          # noqa: E402

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
# Against the LINES, and not against what `include` answered. The product
# routes both verbs through one `if verb in ("include", "match")`, so a
# comparison between the two calls is one expression compared with itself: it
# is the filter's own guard quoted back at it, and no filtering it does can
# turn it. Measured by inverting that branch to `arg not in l` - five
# assertions in this file went red and this one stayed green, on the only
# line in the tree that pipes `match` at all.
check(piped("match mtu") == [" mtu 1500", " mtu 9000"],
      f"match is the same filter under the name operators type: "
      f"{piped('match mtu')}")
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
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT}
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
    sh.frr = type("F", (prismash.Frr,), {"available": lambda s: False,
                            "running": lambda s: [],
                            "show": lambda s, c: (1, "")})()
    sh.device_descriptions = lambda: dict(descs or {})
    if elapsed is not None:
        clock = iter(elapsed)
        prismash.time.monotonic = lambda: next(clock)
        prismash.time.sleep = lambda _s: None
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
# TERSE_IF has four rows and three of them can forward, so both sides of the
# "N of M" are forwarding counts (#306). This read "1 of 4" while the box had
# three usable ports.
check("1 of 3 interface(s) that can forward are shown" in out,
      f"a narrowed listing says so: {out}")

# The description view. This used to print a table and then say the dataplane
# carries no descriptions - true when written, false since `description`
# started being stored as the interface tag, and left saying so.
sh = box_192(TERSE_IF, descs={"TenGigabitEthernet0/8/0": "TRANSITO Vivo"})
out = capture(sh, "show interface description")
check("TRANSITO Vivo" in out, f"a description is shown: {out}")
check("carries no per-interface description" not in out,
      "and the box no longer denies having them")
check("1 of 3 interface(s) described" in out, f"counted: {out}")
check("description <text>" in out,
      "an empty column is a question; the answer is how to fill it")

# ---------------------------------------------------------------------------
# The count is of what can forward, in EVERY view that prints one (#306)
#
# 0.74.0's installation medium said "the dataplane has no interface to forward
# on" and, four lines below it, "1 interface(s) on the dataplane". The one it
# counted was local0. The count is printed from three places and all three
# read the same holder now, so this walks all three rather than the one the
# console happened to show.
print("\n=== the interface count counts what can forward (#306) ===")

_IF_NONE = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)
local0                            0     down          0/0/0/0
"""
_HW_NONE = """              Name                Idx   Link  Hardware
local0                             0    down  local0
  Link speed: unknown
"""
# Two cards installed, which is the case the issue had NOT measured. The old
# count read three here, so an operator comparing the box against the cards
# they put in was off by one on a working box, not only on an empty one.
_IF_TWO = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)
TenGigabitEthernet0/8/0           1      up          9000/0/0/0
TenGigabitEthernet0/8/1           2      up          9000/0/0/0
local0                            0     down          0/0/0/0
"""
_HW_TWO = """              Name                Idx   Link  Hardware
TenGigabitEthernet0/8/0            1     up   TenGigabitEthernet0/8/0
  Link speed: 10 Gbps
TenGigabitEthernet0/8/1            2     up   TenGigabitEthernet0/8/1
  Link speed: 10 Gbps
local0                             0    down  local0
  Link speed: unknown
"""

for _cmd in ("show interface", "show interface terse",
             "show interface description"):
    _o = capture(box_192(_IF_NONE, _HW_NONE), _cmd)
    # The contradiction itself, as one assertion: whatever the wording, a box
    # that has nothing to forward on must not print a 1 next to the word
    # interface. The number is what an operator reads off this screen.
    check("1 interface(s)" not in _o and "of 1 interface(s)" not in _o,
          f"a box with no NIC does not count local0 as one - {_cmd}: {_o}")
    check("local0" not in _o,
          f"and local0 is not listed either - {_cmd}: {_o}")
    # The note under the count went with it: it existed to explain why the
    # rows and the count disagreed, and they no longer do.
    check("listed above but not counted" not in _o,
          f"and nothing says the rows and the count disagree - {_cmd}: {_o}")

    _o = capture(box_192(_IF_TWO, _HW_TWO), _cmd)
    check("3 interface(s)" not in _o and "of 3 interface(s)" not in _o,
          f"two cards installed do not read three - {_cmd}: {_o}")
    check("2 interface(s)" in _o or "of 2 interface(s)" in _o,
          f"two cards installed read two - {_cmd}: {_o}")
print("  all three count lines, on an empty box and on a two-card box")

# And the two sentences together, off the product's own path, because the
# defect was never one line: it was two lines in one screen.
_probe = box_192(_IF_NONE, _HW_NONE)
_d = tempfile.mkdtemp()
_probe.STARTUP_CONF = os.path.join(_d, "startup.conf")
_probe.TUNE_ENV = os.path.join(_d, "tune.env")
open(_probe.STARTUP_CONF, "w").write(
    "plugins { plugin dpdk_plugin.so { disable } }\n")
_o = capture(_probe, "show interface")
check("no interface to forward on" in _o,
      f"the diagnosis is still printed: {_o}")
check("0 interface(s) on the dataplane can forward" in _o,
      f"and the count under it agrees with it: {_o}")
print("  the diagnosis and the count no longer disagree in one screen")

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
# The typed hierarchies: `nat` > `cgnat` and `router static` (#193, #446)

def box_193():
    sh = shell_with_box()
    sh.frr = type("F", (prismash.Frr,), {"available": lambda s: True,
                            "running": lambda s: ["router bgp 65000"],
                            "show": lambda s, c: (1, "")})()
    sh.sys = type("S", (), {"read_ssh": lambda s: {}, "read_dns": lambda s: [],
                            "read_users": lambda s: {}})()
    sh.snmp = type("N", (), {"read": lambda s: ({}, [], {})})()
    sh.feed("configure")
    return sh

sh = box_193()
for line in ("nat", "cgnat", "pool residential", "inside 100.64.0.0/22",
             "outside 203.0.113.0/24", "block-size 512", "exit",
             "interface TenGigabitEthernet0/8/0 inside", "exit", "exit",
             "router static", "0.0.0.0/0 next-hop 10.0.0.2",
             "2001:db8::/32 next-hop fe80::1", "exit"):
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

# The one-line CGNAT form is refused, typed (#446), and stages nothing; the
# route spelling FRR itself uses still works.
sh = box_193()
for _old in ("cgnat interface TenGigabitEthernet0/8/0 outside",
             "nat cgnat pool residential", "no nat cgnat pool residential"):
    out = capture(sh, _old)
    check("the one-line CGNAT form is gone" in out
          and "enter 'nat', then 'cgnat'" in " ".join(out.split()),
          f"{_old!r} is refused and says what to type: {out}")
check(not sh.candidate.interfaces and not sh.candidate.pools
      and sh.nat_ctx == [] and sh.mode == "config",
      f"and nothing was staged, and the operator did not move: "
      f"{sh.candidate.interfaces} {sh.nat_ctx}")
sh.feed("ip route 198.51.100.0/24 blackhole")
check("ip route 198.51.100.0/24 blackhole" in sh.candidate.routing,
      f"FRR's own route spelling still works: {sh.candidate.routing}")

# `exit` walks back up, one level at a time, and `end` goes to the top.
sh = box_193()
sh.feed("nat")
check(sh.nat_ctx == ["nat"] and sh.prompt() == "prisma# [edit nat] ",
      f"nat is entered: {sh.prompt()!r}")
out = capture(sh, "pool x")
check("under 'nat' there is 'cgnat' and nothing else" in out
      and not sh.candidate.pools, f"and only cgnat is under it: {out}")
sh.feed("cgnat")
sh.feed("pool p")
check(sh.mode == "pool" and sh.nat_ctx == ["nat", "cgnat"],
      f"a pool is entered from cgnat: {sh.mode} {sh.nat_ctx}")
sh.feed("exit")
check(sh.mode == "config" and sh.nat_ctx == ["nat", "cgnat"],
      f"exit from the pool returns to cgnat: {sh.nat_ctx}")
sh.feed("exit")
check(sh.nat_ctx == ["nat"], f"then to nat: {sh.nat_ctx}")
sh.feed("exit")
check(sh.nat_ctx == [] and sh.mode == "config",
      f"then to the top, still in configuration mode: {sh.nat_ctx}")
sh.feed("nat")
sh.feed("cgnat")
sh.feed("pool p")
sh.feed("end")
check(sh.mode == "config" and sh.nat_ctx == [],
      f"end from a pool goes to the top: {sh.nat_ctx}")
sh.feed("nat")
sh.feed("cgnat")
sh.feed("up")
check(sh.nat_ctx == ["nat"], f"up is one level: {sh.nat_ctx}")
sh.feed("cgnat")
sh.feed("top")
check(sh.nat_ctx == [], f"top is all of them: {sh.nat_ctx}")
# `no` under cgnat removes, in the level's words.
sh.feed("nat")
sh.feed("cgnat")
sh.feed("pool gone")
sh.feed("exit")
sh.feed("no pool gone")
check("gone" not in sh.candidate.pools,
      f"'no pool' removes it: {list(sh.candidate.pools)}")

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
from prismash import enable_daemon_advice                           # noqa: E402

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
      == "router static / 10.55.0.0/16 next-hop 172.31.99.1",
      f"a static route is named as the VTY spells it: "
      f"{as_hierarchy('ip route 10.55.0.0/16 172.31.99.1')}")
check(as_hierarchy("ipv6 route 2001:db8::/32 fe80::1")
      == "router static / 2001:db8::/32 next-hop fe80::1",
      "and so is an IPv6 one")
check(as_hierarchy(" neighbor 10.0.0.2 remote-as 65001")
      == "neighbor 10.0.0.2 remote-as 65001",
      "a statement the VTY does not rename is left alone")

# The box must not go on teaching the spelling it moved away from. A `usage:`
# line is an instruction, which is exactly the case #193 is about.
import prismash as _hx                                              # noqa: E402
_src = open(_hx.__file__).read()
check("usage: cgnat " not in _src,
      "no usage line still teaches the old top-level spelling")

print("\n=== the IGPs are in the tree, which is where they were not ===")
# `show ?` offered bgp and isis and not OSPF, and both IGP entries ended the
# command: after `show ospf` the operator was told nothing, on a VTY whose
# reason for existing is that they do not have to know FRR is underneath.


def offered(line):
    """Options at `line`, in operational mode. `opts` above is long rebound."""
    sh = prismash.Shell()
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
check(prismash.Frr.outstanding(FRR_REPORT)
      == ["router ospf", "timers throttle spf 0 50 5000", "exit"],
      f"the report is read stripped: {prismash.Frr.outstanding(FRR_REPORT)}")
check(prismash.Frr.outstanding("") == [], "no report is nothing outstanding")
# The other section must not be read as this one. A statement being REMOVED is
# not a statement that failed to apply, and counting it as one would report a
# successful removal as a failed commit.
check(prismash.Frr.outstanding(
        "Lines To Delete\n===============\nno router bgp 65000\n") == [],
      "the delete section is not the add section")
check(prismash.Frr.outstanding(
        "Lines To Delete\n====\nno router ospf\n\nLines To Add\n====\nip route 1.2.3.0/24 blackhole\n")
      == ["ip route 1.2.3.0/24 blackhole"],
      "and a report carrying both reads only the adds")


def box_197(outstanding_text, rc=0):
    sh = shell_with_box()
    sh.candidate = prismash.Config()
    sh.frr = type("F", (prismash.Frr,), {
        "available": lambda s: True,
        "running": lambda s: [],
        "show": lambda s, c: (1, ""),
        "reload": lambda s, lines, check_only=False: (rc, outstanding_text),
        "outstanding": staticmethod(prismash.Frr.outstanding),
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
# #76: `cgnat logging` was the last configuration statement prismash applied with
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
    sh = prismash.Shell()
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
    sh = prismash.Shell()
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
_w = prismash.udp_export_without_collector
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
_o = prismash.CGNAT_CONFIG["logging"][1]
check("udp" in _o, f"'udp' is offered under cgnat logging: {sorted(_o)}")


# ---------------------------------------------------------------------------
# `configure` on a box whose dataplane is not answering yet (#211)
#
# An installed box draws a login prompt while prisma-routing.service is still
# waiting for VPP, so the first thing anybody does with a new box is log in at
# the first prompt and type `configure`. Both ways of proceeding from there are
# wrong, which is why the answer is to refuse.

def box_211(reachable, cfg_text):
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT}

    def run(_self, cmd):
        if cmd == "show version":
            return (0, "vpp v26.06") if reachable else (1, "Could not find VPP CLI socket")
        if cmd == "show bpa config":
            return (0 if reachable else 1), cfg_text
        return (0 if reachable else 1), ""

    sh.dp = type("D", (), {"TRACE": False, "run": run,
                           "reachable": lambda _s: run(_s, "show version")[0] == 0})()
    sh.frr = type("F", (prismash.Frr,), {"available": lambda s: False, "running": lambda s: [],
                            "show": lambda s, c: (1, "")})()
    sh.sys = type("S", (), {"read_ssh": lambda s: {}, "read_dns": lambda s: [], "read_users": lambda s: {}})()
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
check(prismash.VPP_ERROR.match("Could not find VPP CLI socket") is None,
      "the text alone does not look like an error, which is the whole trap")
check(prismash.VPP_ERROR.match("unknown input `nope'") is not None,
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
    # "show version" answers, because a real vppctl does: reachable() asks
    # that one command, and a stand-in that refuses it would be measuring a
    # refusal rather than a dataplane that is not there.
    _f.write("#!/bin/sh\n"
             "case \"$*\" in\n"
             "  *unreachable*) echo 'Could not find VPP CLI socket'; exit 1 ;;\n"
             "  *version*) echo 'vpp v26.06-release'; exit 0 ;;\n"
             "  *) echo \"unknown input \\`x'\"; exit 0 ;;\n"
             "esac\n")
os.chmod(_fake, os.stat(_fake).st_mode | _stat.S_IEXEC)
_saved, prismash.VPPCTL = prismash.VPPCTL, _fake
_vpp = prismash.Vpp()
_rc_unreach, _out_unreach = _vpp.run("unreachable")
_rc_refused, _ = _vpp.run("something")
# reachable() asked while the stand-in is STILL in place. Vpp.run reads the
# module global at call time, so asking after the restore below put the
# question to the host's own vppctl, which is not installed here - the answer
# was False for a reason that had nothing to do with anything this section set
# up, and a reachable() that returned a constant passed.
#
# Both directions, because one of them is the whole of #211: a vppctl that
# ANSWERS, even to refuse, means the dataplane is there.
_reach_answering = _vpp.reachable()
_dead = os.path.join(_dir, "vppctl-dead")
with open(_dead, "w") as _f:
    _f.write("#!/bin/sh\necho 'Could not find VPP CLI socket'; exit 1\n")
os.chmod(_dead, os.stat(_dead).st_mode | _stat.S_IEXEC)
prismash.VPPCTL = _dead
_reach_dead = prismash.Vpp().reachable()
prismash.VPPCTL = _saved

check(_rc_unreach == 1,
      f"vppctl exiting 1 is an error however innocent the text looks: {_rc_unreach}")
check("Could not find" in _out_unreach,
      "and the text still reaches the caller, for the operator to read")
check(_rc_refused == 1,
      "a command VPP itself refused is still an error, by the text")
check(_reach_answering,
      "a vppctl that answers - even to refuse the command - is a dataplane "
      "that is there")
check(not _reach_dead,
      "and one that cannot reach VPP is not, which is the distinction #211 "
      "turned on")


print("\n=== what FRR keeps, and what it does not (#197) ===")
# The table these read is MEASURED, by lab/frr-readback.sh, and the lab is what
# keeps it honest against an FRR upgrade. What is asserted here is the
# arithmetic on top of it: that the value decides, not the statement alone, and
# that a rewrite is not a drop.
from prismash import (frr_keeps, frr_writes_instead, frr_without_seq,  # noqa: E402
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
    sh = prismash.Shell()
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

# --- a pool's public prefix that nothing announces (#145) ---
#
# The one finding here that needs both halves of the box. Its whole value is
# that it fires on a configuration where the CGNAT is right AND the BGP is
# right, so every assertion below is paired: the shape that must warn, and the
# legitimate neighbour of it that must not.
#
# It had a lab and no unit assertion at all, which is the wrong way round for
# a pure function over two lists.
print("\n=== a pool's public prefix that nothing announces ===")
from prismash import pool_announce_findings, bgp_originations   # noqa: E402


def _pool(name, outside, inside="100.64.0.0/22"):
    p = prismash.Pool(name)
    p.inside = [inside]
    p.outside = list(outside)
    return p


def _ann(routing, outside=("200.150.0.16/28",), name="clientes"):
    return pool_announce_findings(routing, {name: _pool(name, outside)})


BGP = ["router bgp 65000", " address-family ipv4 unicast"]

bare = _ann(BGP)
# Unpacked defensively so that a check which reports NOTHING fails every
# assertion below it instead of raising an IndexError on the first. A
# traceback here would abort the file and hide the rest of this section, which
# is how a broken check looks like a passing suite.
b0 = bare[0] if bare else ("", "", "", [])
check(len(bare) == 1 and b0[0] == "warning",
      f"a pool nobody announces is warned about, once: {bare}")
check("nothing here announces 200.150.0.16/28" in b0[2],
      f"and the prefix is named: {bare}")
check(any("cannot route back" in l for l in b0[3]),
      f"and the message says what it costs: {bare}")
check("clientes" in b0[1], f"and which pool it is: {bare}")
# Never an error. The reason is in pool_announce_findings' docstring and is
# measured: `network <prefix>` with no matching route in the RIB is stored by
# FRR 10.7.1 and advertised by nothing, so the condition this can test is a
# proxy for the one that matters, and a commit MUST NOT be refused on it.
check(not [f for f in bare if f[0] == "error"],
      f"and it is never an error: {bare}")

# Containment, not equality: a /24 covers the pool's /28.
check(not _ann(BGP + ["  network 200.150.0.0/24"]),
      "a covering aggregate silences it, which is the ordinary case")
check(not _ann(BGP + ["  network 200.150.0.16/28"]),
      "and so does the exact prefix")
# The direction of containment, which is the whole reason routes.covers_prefix
# exists beside routes._covers: a /28 cannot cover the /24 the pool asks for,
# however their addresses line up.
#
# The two prefixes MUST share a network address for this to be worth asserting.
# Written first as `network 200.150.0.16/28` against a `200.150.0.0/24` pool it
# was green under an address test as well, because .0 is outside .16/28 and
# both functions said no for different reasons - so it caught nothing. Lining
# the addresses up is what makes the address test answer "covered" and the
# containment test answer "not covered".
check(_ann(BGP + ["  network 200.150.0.0/28"], outside=("200.150.0.0/24",)),
      "a longer prefix does not cover a shorter one with the same address")

# OSPF's `network` is `network <prefix> area <n>` and says nothing about what
# BGP advertises. Read as an announcement it would silence this check on a box
# that announces nothing at all.
check(_ann(BGP + ["router ospf", " network 200.150.0.0/24 area 0"]),
      "an OSPF network statement is not a BGP announcement")

# `no network ...` takes the statement away. Counted as configuration it would
# silence the check with the line that removed the announcement.
check(_ann(BGP + ["  no network 200.150.0.0/24"]),
      "a 'no network' does not announce anything")

# --- what redistribution does and does not hide, measured on FRR 10.7.1 ---
#
#   router bgp / af ipv4 unicast / redistribute static, with the route:
#     *>  200.150.9.16/28  0.0.0.0  0  32768 ?
#   the same, route removed:
#     No BGP prefixes displayed, 0 exist
#   router ospf / redistribute static, BGP carrying none:
#     No BGP prefixes displayed, 0 exist
STATIC = ["ip route 200.150.0.0/24 blackhole"]
check(not _ann(BGP + ["  redistribute static"] + STATIC),
      "a static route redistributed into BGP announces the pool's prefix")
check(_ann(BGP + ["  redistribute static"]),
      "while 'redistribute static' with no covering route announces nothing")
check(_ann(BGP + ["  redistribute static",
                  "ip route 10.0.0.0/8 blackhole"]),
      "nor does one that covers something else")
# The seam between what the operator TYPES and what this reads. Inside `router
# static` the VTY takes `route <prefix> <next-hop>` and stages FRR's `ip route
# ...` - at the top level, which is what makes it a global route rather than a
# VRF one. The two live in different files, and a rename on either side would
# leave this check silently unable to resolve any static route at all, which is
# a false warning on every correctly announced box.
_sh = prismash.Shell()
_sh.frr_ctx = []
check(_sh.hierarchy_rewrite("router static") == "",
      "'router static' is a block this VTY owns, not a statement it passes on")
_sh.frr_ctx = ["router static"]
_staged = _sh.hierarchy_rewrite("200.150.0.0/24 next-hop blackhole")
check(_staged == "ip route 200.150.0.0/24 blackhole",
      f"the VTY stages a static route in FRR's spelling: {_staged!r}")
check(not _ann(BGP + ["  redistribute static", _staged]),
      "and what the VTY stages is what this check reads")
# The scope. `redistribute static` under OSPF puts nothing into BGP - measured
# above - so it must neither announce the prefix nor blind the check. Reading
# the word wherever it appeared did the second, and went silent on the
# ordinary edge box that redistributes into its IGP and announces nothing.
check(_ann(BGP + ["router ospf", " redistribute static"] + STATIC),
      "'redistribute static' under OSPF neither announces nor hides")
check(_ann(BGP + ["router ospf", " redistribute bgp"]),
      "and neither does a full table into the IGP")

# A static route inside a VRF is in another table. Hoisting it into the global
# answer is the mistake config.group_static() already had to be taught not to
# make.
check(_ann(BGP + ["  redistribute static",
                  "vrf red", " ip route 200.150.0.0/24 blackhole"]),
      "a VRF's static route does not announce a prefix in the global table")

# --- what this check cannot see, and therefore must not warn about ---
for hidden in ("  redistribute connected",
               "  redistribute ospf",
               "  redistribute static route-map RM",
               "  aggregate-address 200.150.0.0/16 summary-only",
               "  network 200.150.0.0/24 route-map RM"):
    check(not _ann(BGP + [hidden]),
          f"a statement this cannot follow silences it entirely: {hidden}")
# And the reason that is not vacuous: each of those is genuinely unfollowable
# rather than simply unparsed. Measured for the last two on FRR 10.7.1:
# `aggregate-address 200.150.0.0/16 summary-only` originates the /16 when a
# more-specific is in the table and nothing when none is, and `network
# 200.150.9.16/28 route-map RM-DENY` originates nothing while the same
# statement with a permit map originates it.
_, blind, _ = bgp_originations(BGP + ["  aggregate-address 200.150.0.0/16"])
check(len(blind) == 1 and "aggregate-address" in blind[0],
      f"and the statement that blinds it is named: {blind}")

# "announced" and "cannot see" are not the same answer, and the difference
# shows on a box with more than one pool: an announcement silences only the
# pools it covers, while a statement this cannot follow silences all of them.
# A `network ... route-map` counted as a plain announcement would look right
# on the pool it names and wrong on every other one.
mixed = pool_announce_findings(
    BGP + ["  network 200.150.0.0/24 route-map RM"],
    {"a": _pool("a", ["203.0.113.0/28"])})
check(not mixed,
      f"a network with a route-map silences a pool it does not cover: {mixed}")

# A box with no BGP announces nothing from here by definition, and is exactly
# the deployment whose prefix is carried by a router upstream. Warning there
# would be an alarm that is always on.
check(not _ann(["interface vppwan0", " ip address 10.0.0.1/24"]),
      "a box with no BGP stanza is told nothing")
check(not pool_announce_findings(BGP, {}),
      "and neither is a box with no pools")

# --- the shape EVERY commit after the first one sees ---
#
# cfg.routing is read back from FRR, so this is not a hand-made indented
# example: it is what FRR 10.7.1 printed during the measurement, ` !` and all.
# That indented separator is the whole reason this is asserted on the captured
# text rather than on something written to look like it - config.block_paths()
# read it as "back to the top", which put the `network` statement in no block
# at all, and the check then warned about a pool the box was announcing
# correctly. A false positive on the ordinary case, invisible in a flat test.
FRR_READBACK = ["router bgp 65000",
                " !",
                " address-family ipv4 unicast",
                "  network 200.150.9.16/28",
                " exit-address-family",
                "exit"]
check(not pool_announce_findings(
        FRR_READBACK, {"c": _pool("c", ["200.150.9.16/28"])}),
      "a prefix FRR reads back under router bgp is an announcement")
_a, _b, _h = bgp_originations(FRR_READBACK)
check(_a == ["200.150.9.16/28"] and _h,
      f"and it is found in the stanza it was typed in: {_a} {_h}")
# The same read-back shape carrying the scope mistake.
check(pool_announce_findings(
        ["ip route 200.150.9.16/28 blackhole",
         "router ospf", " redistribute static", "exit",
         "router bgp 65000", " !", " address-family ipv4 unicast",
         " exit-address-family", "exit"],
        {"c": _pool("c", ["200.150.9.16/28"])}),
      "and the IGP redistribution is still not an announcement, indented")

# Both shapes `routing` arrives in. Read back from FRR it is INDENTED; typed
# at this VTY it is flat. A scanner that read either one alone was wrong for
# the other, which is why the block context comes from config.block_paths().
FLAT = ["router bgp 65000", "address-family ipv4 unicast",
        "redistribute static", "exit-address-family", "exit",
        "ip route 200.150.0.0/24 blackhole"]
check(not _ann(FLAT),
      "the flat shape typed at this VTY is read the same way as FRR's")
check(_ann(["router bgp 65000", "address-family ipv4 unicast",
            "exit-address-family", "exit",
            "router ospf", "redistribute static",
            "ip route 200.150.0.0/24 blackhole"]),
      "and the scope holds in the flat shape too")

# --- the six cases lab/bgp-policy.sh drives, in the shape the VTY builds ---
#
# Two shapes meet in one candidate and both are real. The stanza FRR read back
# is INDENTED and carries the ` !` separator; everything the operator then
# types is APPENDED FLAT at column 0, because `do_config` ends in
# `candidate.routing.append(line)` and does not merge into the block above. So
# a second `router bgp 65000` at column 0 sits after the indented one, and the
# statements typed under it belong to THAT one.
#
# Asserted here as well as in the lab because the lab needs VPP and a
# container, and a check whose only proof takes minutes to run is a check
# nobody reruns while changing it.
LAB_BASE = ["router bgp 65000",
            " bgp router-id 10.0.0.1",
            " neighbor 10.0.0.2 remote-as 65001",
            " !",
            " address-family ipv4 unicast",
            "  neighbor 10.0.0.2 activate",
            " exit-address-family",
            "exit",
            "!"]
LAB_POOL = {"lab-ann": _pool("lab-ann", ["200.150.9.16/28"], "100.64.8.0/22")}
LAB = (
    ("NOANN", [], True),
    ("ANN", ["router bgp 65000", "address-family ipv4 unicast",
             "network 200.150.9.0/24", "exit-address-family", "exit"], False),
    ("RED", ["router bgp 65000", "redistribute connected", "exit"], False),
    ("RSTAT", ["ip route 200.150.9.0/24 blackhole",
               "router bgp 65000", "address-family ipv4 unicast",
               "redistribute static", "exit-address-family", "exit"], False),
    ("RELSE", ["ip route 10.0.0.0/8 blackhole",
               "router bgp 65000", "address-family ipv4 unicast",
               "redistribute static", "exit-address-family", "exit"], True),
    ("ROSPF", ["ip route 200.150.9.0/24 blackhole",
               "router ospf", "redistribute static", "exit",
               "router bgp 65000", "address-family ipv4 unicast",
               "exit-address-family", "exit"], True),
)
# `_stmts` and not `_typed`: a loop variable at module level outlives its
# loop, and `_typed` is the helper that drives the VTY, defined four thousand
# lines above. Every case after this point that called it got "'list' object
# is not callable" - which is a crash rather than a red check, so the rest of
# the file stops running and whoever is looking sees a traceback instead of a
# verdict.
for _name, _stmts, _warns in LAB:
    _f = pool_announce_findings(LAB_BASE + _stmts, LAB_POOL)
    check(bool(_f) == _warns,
          f"lab case {_name}: expected {'a warning' if _warns else 'silence'}"
          f", got {_f}")

# Every pool, not the first one.
two = pool_announce_findings(
    BGP + ["  network 200.150.0.0/24"],
    {"a": _pool("a", ["200.150.0.16/28"]),
     "b": _pool("b", ["203.0.113.0/28"])})
check(len(two) == 1 and "'b'" in two[0][1],
      f"the announced pool is quiet and the other one is not: {two}")
# And every prefix of one pool, not the first one.
multi = pool_announce_findings(
    BGP + ["  network 200.150.0.0/24"],
    {"a": _pool("a", ["200.150.0.16/28", "203.0.113.0/28"])})
check(len(multi) == 1 and "203.0.113.0/28" in multi[0][2],
      f"a pool's second outside prefix is checked too: {multi}")

# No input may put a traceback in front of an operator.
for junk in ("network", "network /", "network 200.150.0.16/", "redistribute",
             "ip route", "ip route 200.150.0.0/24", "aggregate-address"):
    try:
        _ann(BGP + ["  " + junk, junk])
    except Exception as e:                                   # noqa: BLE001
        check(False, f"half-typed statement raised {e!r}: {junk}")


print("\n=== inside a block, `show` is offered and so is the tree under it ===")
#
# `show this` is the only way to read back what has been typed into a block
# before committing, and it WORKS in every block. `?` and TAB listed it in the
# FRR blocks only: a pool, a user and an SNMP user are modes rather than
# frr_ctx entries, so the injection that adds `show` never reached them, while
# the comment beside it said "every block" (#221).
#
# The pool is the one that mattered: it is this product's whole subject, and
# the wiki uses a pool as its example of `show this`.
#
# Asserted in both halves, because either alone is satisfiable by an accident:
# that the word is offered, and that the sub-commands under it are, since a
# `show` with nothing after it leaves the operator exactly where they were.
for _blk, _mode in (("nat|cgnat|pool residential", "pool"),
                    ("user bob", "user"),
                    ("snmp-user u1", "snmp-user"),
                    ("interface TenGigabitEthernet0/8/0", "config"),
                    ("router bgp 65000", "config")):
    _sh = shell_with_box()
    with contextlib.redirect_stdout(io.StringIO()):
        _sh.feed("configure")
        for _step in _blk.split("|"):
            _sh.feed(_step)
    _o, _, _ = _sh._options("", False)
    check("show" in _o, f"inside '{_blk}', `?` does not offer show: {sorted(_o)}")
    _o2, _, _ = _sh._options("show ", False)
    check("this" in _o2,
          f"inside '{_blk}', `show ?` does not offer this: {sorted(_o2)}")
    check("version" in _o2 and "configuration" in _o2,
          f"inside '{_blk}', `show ?` offers less than the show tree, which "
          f"all answers from in here: {sorted(_o2)}")

# And it must still be the block's own grammar that starts a line, rather than
# the level above leaking back in with show as the excuse (#112).
_sh = shell_with_box()
with contextlib.redirect_stdout(io.StringIO()):
    _sh.feed("configure")
    _sh.feed("nat")
    _sh.feed("cgnat")
    _sh.feed("pool residential")
_o, _, _ = _sh._options("", False)
check("interface" not in _o and "user" not in _o,
      f"the level above leaked into a pool block: {sorted(_o)}")
check("block-size" in _o,
      f"the pool's own statements are gone from a pool block: {sorted(_o)}")

# --- the boundary between what this VTY checked and what it carried (#141) ---
#
# A statement this VTY passes through without understanding is not the same as
# one it validated, and a configuration that hides the difference is a
# configuration that lies about how much it checked. Measured on master, these
# three lines came out of `show configuration` looking identical:
#
#     router bgp 65000
#     neighbor 10.0.0.2 remote-as 65001            <- the tree walks this
#     coalesce-time 1000                           <- nothing here read it
#     neighbor 10.0.0.2 soft-reconfiguration inbound   <- nor this
#
# The first is in the command tree. The other two are offered by `?` nowhere
# and reach FRR unread.
print("\n=== a statement the VTY carried does not look like one it checked ===")
from prismash import (mark_routing, unchecked_note, statement_checked,
                    block_grammar)                               # noqa: E402
from config import group_static                                  # noqa: E402

MEASURED = ["router bgp 65000",
            "neighbor 10.0.0.2 remote-as 65001",
            "coalesce-time 1000",
            "neighbor 10.0.0.2 soft-reconfiguration inbound",
            "exit"]


def shown(lines, ctx=()):
    """The lines as a display prints them, keyed by their statement."""
    marked, n = mark_routing(lines, ctx)
    return {l.strip().split(UNCHECKED_MARK.strip())[0].strip(): l
            for l in marked}, n


disp, n = shown(MEASURED)
check(not disp["neighbor 10.0.0.2 remote-as 65001"].endswith(UNCHECKED_MARK),
      f"a statement the tree walks is not marked: {disp}")
check(disp["coalesce-time 1000"].endswith(UNCHECKED_MARK),
      f"a statement no grammar reads is marked: {disp}")
check(disp["neighbor 10.0.0.2 soft-reconfiguration inbound"]
      .endswith(UNCHECKED_MARK),
      f"and so is the second one: {disp}")
check(n == 2, f"two of the five lines went through unread, not {n}")
# The block's own `exit` is printed by this VTY, so marking it would be the
# VTY reporting that it cannot read a word it wrote itself.
check(not disp["exit"].endswith(UNCHECKED_MARK), f"`exit` is not marked: {disp}")
print("  " + " / ".join(l.strip() for l in
                        [disp["neighbor 10.0.0.2 remote-as 65001"],
                         disp["coalesce-time 1000"]]))

# THE SAME STATEMENT, IN TWO BLOCKS, WITH TWO ANSWERS. `soft-reconfiguration`
# is in the address-family neighbour grammar and not in the plain one, so the
# mark has to depend on where the statement sits - which is the proof that it
# is being decided by the grammar in force and not by a list of words.
af = ["router bgp 65000", "address-family ipv4 unicast",
      "neighbor 10.0.0.2 soft-reconfiguration inbound", "exit", "exit"]
disp_af, _ = shown(af)
check(not disp_af["neighbor 10.0.0.2 soft-reconfiguration inbound"]
      .endswith(UNCHECKED_MARK),
      f"inside the address-family the same statement is modelled: {disp_af}")

# --- it asks the command tree, and nothing else ---
#
# The defect this design is avoiding is a second list of "statements we model"
# kept alongside the grammar by hand: it goes stale, and a stale list says a
# statement was checked when nothing checked it. So the assertion is not that
# `coalesce-time` is marked - it is that TEACHING THE TREE the statement is
# enough to stop it being marked, with nothing else touched.
print("\n=== the marker reads the grammar, not a list beside it ===")
taught = dict(prismash.BGP_BLOCK)
taught["coalesce-time"] = ("how long to wait for peers to converge", "<ms>")
_real = prismash.BGP_BLOCK
try:
    prismash.BGP_BLOCK = taught
    after, n_after = shown(MEASURED)
finally:
    prismash.BGP_BLOCK = _real
check(not after["coalesce-time 1000"].endswith(UNCHECKED_MARK),
      "a statement added to the tree stops being marked with no second edit: "
      f"{after}")
check(n_after == 1, f"and the count follows it: {n_after}")
# And back again, so the test cannot pass by leaving the tree modified.
again, _ = shown(MEASURED)
check(again["coalesce-time 1000"].endswith(UNCHECKED_MARK),
      "the tree is restored")

# A block this VTY has no grammar for at all: every statement in it went
# through unread, and so did the header. An operator standing in one needs
# that on the first line rather than statement by statement.
print("\n=== a block with no grammar says so on its header ===")
check(block_grammar("vrf red", []) is None,
      "the VTY models no `vrf` block")
vrf, n_vrf = mark_routing(["vrf red", " ip route 10.9.0.0/16 10.0.0.9",
                           " exit-vrf"])
check(vrf[0].endswith(UNCHECKED_MARK), f"the header is marked: {vrf}")
check(vrf[1].endswith(UNCHECKED_MARK), f"and its statement: {vrf}")
check(not vrf[2].endswith(UNCHECKED_MARK), f"`exit-vrf` is not: {vrf}")
check(n_vrf == 2, f"two statements, not {n_vrf}")
# `?` inside that block still offers `exit`, which is a different question
# from whether the VTY has a grammar for it, and the refactor that split the
# two must not have taken the offer away (#221: a block that offers nothing
# strands whoever is standing in it).
_s = shell_with_box()
with contextlib.redirect_stdout(io.StringIO()):
    _s.feed("configure")
    _s.feed("vrf red")
_o, _, _ = _s._options("", False)
check("exit" in _o and "show" in _o,
      f"an unmodelled block still offers a way out and a way to read it: {_o}")

# A static route answers the same in both shapes it exists in. The VTY
# provides the `router static` block and translates it to FRR's top-level `ip
# route` lines, so the DISPLAY puts the route back in the block and the
# candidate list does not - and `commit check` counts the candidate list. Read
# by the statement rather than by where it sits, every static route on the box
# would have been reported as passed through unread.
print("\n=== a static route is this VTY's own block in FRR's words ===")
check(mark_routing(["ip route 0.0.0.0/0 10.0.0.2"])[1] == 0,
      "a static route as the candidate holds it is not marked")
check(mark_routing(group_static(["ip route 0.0.0.0/0 10.0.0.2"]))[1] == 0,
      "nor as the display groups it")
# And that is about the STATEMENT, not about the words `ip route`: the same
# route inside a block this VTY has no grammar for is still unread, because
# nothing read the block it is in. A VRF's routes go in another table.
check(mark_routing(["vrf red", " ip route 10.0.0.0/8 1.1.1.1",
                    " exit-vrf"])[1] == 2,
      "a route inside an unmodelled block is still marked")

# --- where the boundary is shown ---
#
# `show this` FIRST, because it is the one an operator runs while they are
# still typing. Being told at `show configuration` time - or at commit - that
# half of what they typed went through unread is being told after the fact.
print("\n=== show this marks the block being typed in ===")
lines = this_in_raw("router bgp 65001",
                    "neighbor 10.0.0.2 remote-as 65002",
                    "coalesce-time 1000")
check(any(l.endswith(UNCHECKED_MARK) and "coalesce-time" in l for l in lines),
      f"show this marks the statement nothing read: {lines}")
check(not any(l.endswith(UNCHECKED_MARK) and "remote-as" in l for l in lines),
      f"and not the one the tree walks: {lines}")
check(any(l.startswith(UNCHECKED_NOTE) for l in lines),
      f"and closes with what the mark means: {lines}")

# And standing in a block the VTY has no grammar for, `show this` says so on
# the header. That is the line an operator reads first, and "nothing in here
# was read" is a different piece of news from "this one statement was not".
_vrf_this = this_in_raw("vrf red", "ip route 10.9.0.0/16 10.0.0.9")
check(_vrf_this[0].endswith(UNCHECKED_MARK),
      f"show this marks the header of a block with no grammar: {_vrf_this}")
check(any(l.endswith(UNCHECKED_MARK) and "ip route" in l for l in _vrf_this),
      f"and the statements inside it: {_vrf_this}")

# `show configuration` and `show running-config` print through one function,
# so both get it; the CGNAT and system half does not, and that is deliberate.
# Every statement above `! routing` is modelled by construction - `Config.parse`
# records what it cannot hold and `commit` refuses to run while it holds one -
# so there is no passthrough there to mark, and a mark is a claim that has to
# stay worth something.
print("\n=== show configuration marks the routing half and only that ===")
_cfg = Config()
_cfg.routing = list(MEASURED)
_cfg.snmp_communities = ["public"]
_out = mark_unchecked(_cfg.text().splitlines())
_routing_at = _out.index("! routing")
check(not any(l.endswith(UNCHECKED_MARK) for l in _out[:_routing_at]),
      f"nothing above the routing header is marked: {_out[:_routing_at]}")
check(sum(l.endswith(UNCHECKED_MARK) for l in _out[_routing_at:]) == 2,
      f"and two below it are: {_out[_routing_at:]}")

# --- the note at the end, and the silence when there is nothing to say ---
#
# The rule from #205 and #208: an alarm that is always on is one nobody reads.
# Passing statements through is the normal case for FRR configuration, so the
# note appears only when something is marked - and the marks are per line, so
# the operator never has to take the count on trust.
print("\n=== the closing note counts, and says nothing when there is nothing ===")
check(unchecked_note(0) == [], "no note when nothing was passed through")
check(unchecked_note(3) and "3" in unchecked_note(3)[0],
      f"the count is in the note: {unchecked_note(3)}")
_clean = ["router bgp 65000", "neighbor 10.0.0.2 remote-as 65001", "exit"]
check(not any(l.startswith(UNCHECKED_NOTE)
              for l in mark_unchecked(Config().text().splitlines())),
      "an empty routing section prints no note")
_c2 = Config()
_c2.routing = list(_clean)
check(not any(l.startswith(UNCHECKED_NOTE)
              for l in mark_unchecked(_c2.text().splitlines())),
      "nor does a routing section this VTY read every line of")

# --- 80 columns, which is what the box is reached on when it matters ---
#
# The console an operator opens when the network is down is 80 columns wide,
# and the statements most likely to be marked are the long ones. The marker is
# therefore short enough to ride on a long IPv6 neighbour statement, and the
# sentence that explains it goes once at the end instead of on every line.
#
# The budget is CONSOLE_COLUMNS and not a literal, because the commit refusal
# quotes these same marked lines and is held to the same number (#252). Two
# literals is how the refusal came to print 178 columns while this assertion
# stayed green for two releases.
print("\n=== a marked line still fits an 80-column console ===")
LONG = ["router bgp 65000",
        "neighbor 2001:db8:aaaa:bbbb::1 soft-reconfiguration inbound",
        "neighbor 2001:db8:aaaa:bbbb::1 coalesce-time 1000",
        "exit"]
_marked, _ = mark_routing(LONG)
_wide = [l for l in _marked + unchecked_note(2) if len(l) > CONSOLE_COLUMNS]
check(not _wide, f"these wrap on a serial console: {[(len(l), l) for l in _wide]}")
print(f"  longest marked line: {max(len(l) for l in _marked)} columns")

# --- PRESENTATION ONLY: the saved file and the FRR handoff are untouched ---
#
# This is the constraint that breaks boxes if it is got wrong. `save` writes a
# file the box reads back at boot and `commit` hands the same statements to
# frr-reload. A marker that leaked into either becomes a statement FRR does not
# understand, the load is refused whole, and the box comes up with NO
# configuration - which is how both boot defects behind #182 ended.
#
# Asserted separately and explicitly, against the real paths: the bytes `save`
# writes, and the list `apply` hands to frr-reload.
print("\n=== the marking never leaves the screen ===")
import tempfile                                                  # noqa: E402

_sh = shell_with_box()
_handed = []
_sh.frr = type("F", (prismash.Frr,), {
    "available": lambda s: True,
    "running": lambda s: [],
    "reload": lambda s, lines, check_only=False: (
        _handed.append(list(lines)), (0, ""))[1],
    "show": lambda s, c: (1, "")})()
with contextlib.redirect_stdout(io.StringIO()):
    _sh.feed("configure")
    for _l in MEASURED[:-1]:
        _sh.feed(_l)
    _sh.feed("exit")
_cand = _sh.candidate
_text_before = _cand.text()
_routing_before = list(_cand.routing)

with contextlib.redirect_stdout(io.StringIO()):
    _sh.feed("show configuration")
    _sh.feed("show running-config")
    _applied = _sh.apply(_cand, check_only=True)

# `show this` inside the block gets its own shell, because ENTERING a block
# stages its header - that is the product working, not the marking leaking -
# and a snapshot taken across the entry would be measuring the wrong thing.
_sh2 = shell_with_box()
with contextlib.redirect_stdout(io.StringIO()):
    _sh2.feed("configure")
    for _l in MEASURED[:-1]:
        _sh2.feed(_l)
_before_this = list(_sh2.candidate.routing)
with contextlib.redirect_stdout(io.StringIO()):
    _sh2.feed("show this")
check(_sh2.candidate.routing == _before_this,
      f"`show this` inside a block changes nothing either: "
      f"{_sh2.candidate.routing}")

check(_cand.text() == _text_before,
      "displaying a configuration does not change the text it renders to")
check(_cand.routing == _routing_before,
      f"nor the statements themselves: {_cand.routing}")
check(UNCHECKED_MARK not in _cand.text()
      and UNCHECKED_NOTE not in _cand.text(),
      "and neither the marker nor the note is anywhere in that text")
check(_applied and _handed,
      f"the routing half reached frr-reload at all: {_handed}")
check(_handed[-1] == _routing_before,
      f"and what it got is the statements as typed: {_handed[-1]}")
check(not any(UNCHECKED_MARK in l or UNCHECKED_NOTE in l
              for l in _handed[-1]),
      f"with no marking in them: {_handed[-1]}")

# `save` for real: the file the box reads back at boot.
_fd, _path = tempfile.mkstemp()
os.close(_fd)
_old_path = prismash.CONFIG_PATH
try:
    prismash.CONFIG_PATH = _path
    _sh.running_config = lambda: _cand
    with contextlib.redirect_stdout(io.StringIO()):
        _sh.save_running()
    with open(_path) as _f:
        _saved = _f.read()
finally:
    prismash.CONFIG_PATH = _old_path
    os.unlink(_path)
check(_saved == _text_before,
      "the saved file is byte for byte what it was before the marking existed")
check(UNCHECKED_MARK not in _saved and UNCHECKED_NOTE not in _saved,
      f"no marking reached the file the box boots from: {_saved!r}")
# And read back the way the boot does it, because that is the failure mode:
# the file is handed to `prismash --load`, and a line it cannot hold refuses the
# whole configuration.
_reloaded = Config.parse(_saved)
check(_reloaded.routing == _routing_before,
      f"the file loads back to the same statements: {_reloaded.routing}")
check(not _reloaded.unknown,
      f"with nothing the VTY cannot read: {_reloaded.unknown}")

# The other half of the same hazard: an operator copies what is ON THE SCREEN
# into a file. The displayed text - marks, note and all - has to parse back to
# exactly the configuration it was made from.
print("\n=== the marked display still parses back to the configuration ===")
_screen = "\n".join(mark_unchecked(_cand.text().splitlines())) + "\n"
check(UNCHECKED_MARK in _screen and UNCHECKED_NOTE in _screen,
      "the screen really does carry the marking")
_from_screen = Config.parse(_screen)
check(_from_screen.routing == _routing_before,
      f"and it parses back unchanged: {_from_screen.routing}")
check(not _from_screen.unknown,
      f"with nothing unreadable: {_from_screen.unknown}")

# --- how much of a real box is outside the boundary ---
#
# The text below is a REAL read-back, typed at a live FRR 10.7.1 and read with
# `show running-config`, filtered exactly as `Frr.running()` filters it: a
# provider edge with a prefix-list, a route-map, an eBGP transit session with
# policy, OSPF and a default route. It is here because the marking has to be
# proportionate on a box that is working - a display where everything is
# marked says as little as one where nothing is.
#
# Three statements of twenty-four, and each of the three is genuinely a
# statement this VTY has no grammar for. The count is the one the assertion
# below names one by one; this comment said four of twenty-five and was
# wrong on the day it was written (#319), which is what a count in prose
# beside an assertion that disagrees with it is for. `no bgp ebgp-requires-policy` is the
# interesting one: nearly every FRR BGP configuration carries it, this VTY
# models no `no` inside a block at all, and the marker naming it is the marker
# working rather than the marker being noisy.
print("\n=== a real FRR read-back, and how much of it is outside ===")
FRR_READBACK = """!
hostname prisma-routing
service integrated-vtysh-config
!
ip prefix-list PL-MEUS-BLOCOS seq 5 permit 200.150.8.0/22
!
route-map RM-OUT permit 10
 match ip address prefix-list PL-MEUS-BLOCOS
 set as-path prepend 65000
exit
!
ip route 0.0.0.0/0 10.0.0.2
!
router bgp 65000
 bgp router-id 10.0.0.1
 bgp log-neighbor-changes
 no bgp ebgp-requires-policy
 neighbor 10.0.0.2 remote-as 65001
 neighbor 10.0.0.2 description transito-primario
 !
 address-family ipv4 unicast
  network 200.150.8.0/22
  neighbor 10.0.0.2 soft-reconfiguration inbound
  neighbor 10.0.0.2 route-map RM-OUT out
 exit-address-family
exit
!
router ospf
 ospf router-id 10.0.0.1
 network 10.0.0.0/24 area 0
exit
!""".splitlines()

_real, _n_real = mark_routing(FRR_READBACK)
_marked_now = [l.strip()[:-len(UNCHECKED_MARK.strip())].strip()
               for l in _real if l.endswith(UNCHECKED_MARK)]
check(sorted(_marked_now) == ["hostname prisma-routing",
                              "no bgp ebgp-requires-policy",
                              "service integrated-vtysh-config"],
      f"these three, and no others, are outside the grammar: {_marked_now}")
# The other twenty-one are NOT marked, which is the half that makes the mark
# mean something. Named one by one rather than counted, because a count passes
# just as well when the wrong lines are clean.
for _want in ["ip prefix-list PL-MEUS-BLOCOS seq 5 permit 200.150.8.0/22",
              "route-map RM-OUT permit 10",
              "match ip address prefix-list PL-MEUS-BLOCOS",
              "set as-path prepend 65000",
              "ip route 0.0.0.0/0 10.0.0.2",
              "bgp router-id 10.0.0.1",
              "bgp log-neighbor-changes",
              "neighbor 10.0.0.2 remote-as 65001",
              "neighbor 10.0.0.2 description transito-primario",
              "address-family ipv4 unicast",
              "network 200.150.8.0/22",
              "neighbor 10.0.0.2 soft-reconfiguration inbound",
              "neighbor 10.0.0.2 route-map RM-OUT out",
              "router ospf", "ospf router-id 10.0.0.1",
              "network 10.0.0.0/24 area 0"]:
    check(_want not in _marked_now,
          f"'{_want}' is in the tree and must not be marked")
# FRR's own `!` separators are part of the routing statements and are handed
# back to FRR as they were, so they are not statements to mark either.
check(not any(l.strip().startswith("!") and l.endswith(UNCHECKED_MARK)
              for l in _real),
      f"FRR's separators are left alone: {_real}")

# --- commit says nothing; `commit check` names the number ---
#
# Argued from the standing rule (#205, #208): an alarm that is always on is
# one nobody reads. Passthrough is the NORMAL case here - a working FRR edge
# has `no bgp ebgp-requires-policy` in it and this VTY models no `no` inside a
# block at all - so a warning on every commit would be skipped within a week,
# and the habit of skipping the end of a commit would take the "FRR kept none
# of these statements" warning with it.
#
# `commit check` is the opposite: a question the operator chose to ask, whose
# whole subject is how much was verified. It already said "structure only",
# which names no number. Now it names one.
print("\n=== commit check says how many statements it could not read ===")


def check_output(routing):
    s = shell_with_box()
    s.frr = type("F", (prismash.Frr,), {"available": lambda _s: True,
                           "running": lambda _s: [],
                           "reload": lambda _s, l, check_only=False: (0, ""),
                           "show": lambda _s, c: (1, "")})()
    with contextlib.redirect_stdout(io.StringIO()):
        s.feed("configure")
    s.candidate.routing = list(routing)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.feed("commit check")
    return buf.getvalue()


_said = check_output(MEASURED)
check("2 routing statement(s)" in _said,
      f"commit check names the count: {_said!r}")
check("were not read at all" in _said,
      f"and what the count means: {_said!r}")
_quiet = check_output(["router bgp 65000",
                       "neighbor 10.0.0.2 remote-as 65001", "exit"])
check("were not read at all" not in _quiet,
      f"and says nothing when it read everything: {_quiet!r}")
check("configuration check succeeds" in _quiet,
      f"while still answering the question asked: {_quiet!r}")
print("\n=== a statement FRR did not keep is a FAILED commit (#197) ===")
# The promotion. This check shipped as a warning because the table of
# statements FRR does not read back verbatim had been extended by four labs in
# a row, and refusing a correct commit is the harm #208 was filed for. What
# promoted it is the condition the code carried: the full lab suite run clean
# with the refusal in force.
#
# What is asserted here is the VERDICT, not the arithmetic - the arithmetic is
# above. `commit` has to report failure, and it has to name the statement: an
# operator told only that "something was dropped" has to diff two
# configurations by hand to find out which.
import contextlib as _ctx  # noqa: E402
import io as _io           # noqa: E402
import config as _cfg      # noqa: E402


def _cfg_with(routing):
    c = _cfg.Config.parse("")
    c.routing = list(routing)
    return c


class _FrrHolding:
    """An FRR whose running configuration is exactly these lines."""

    def __init__(self, running):
        self._running = running

    def running(self):
        return self._running

    def reload(self, _lines, check_only=False):
        # Non-zero, so nothing_took() declines to answer. This is the drop
        # check under test and not that one, and a fixture that let both fire
        # would not say which produced the verdict.
        return 1, ""

    def outstanding(self, _out):
        return []


def _commit(candidate, frr_running):
    """Commit `candidate` against an FRR that came back holding `frr_running`.

    Returns (what the VTY printed, how many errors it counted).
    """
    sh = prismash.Shell()
    sh.candidate = _cfg_with(candidate)
    sh.running_config = lambda: _cfg_with([])
    sh.apply = lambda cfg, check_only=False: True
    sh.rb = type("_RB", (), {"push": lambda self, *a: None})()
    sh.frr = _FrrHolding(frr_running)
    buf = _io.StringIO()
    with _ctx.redirect_stdout(buf):
        sh.do_commit([])
    return buf.getvalue(), sh.failed


_STATIC = ["router static", " ip route 203.0.113.0/24 blackhole"]

# The case #197 was filed for, end to end through do_commit: the route was
# handed to FRR, FRR exited 0, and FRR does not have it.
_out, _failed = _commit(_STATIC, [])
check(_failed == 1,
      f"a statement FRR did not keep makes commit REPORT FAILURE: {_out!r}")
check(_out.startswith("error:"),
      f"and say so as an error, not a warning: {_out!r}")
check("commit complete" not in _out,
      f"'commit complete' is the sentence being refused: {_out!r}")
# The statement is rendered the way an operator typed it, not the way FRR
# holds it: `as_hierarchy` turns `ip route ...` back into `router static /
# route ...`, which is what they will find in `show configuration`.
check("203.0.113.0/24 next-hop blackhole" in _out,
      f"the operator is told WHICH statement FRR did not keep: {_out!r}")
check("rollback" in _out,
      f"and that the rest of the commit is applied, so there is a way "
      f"back: {_out!r}")

# The other half, and the one that matters more: the same commit when FRR DID
# keep it. Without this the refusal could be unconditional and still green.
_out, _failed = _commit(_STATIC, ["ip route 203.0.113.0/24 blackhole"])
check(_failed == 0 and "commit complete" in _out,
      f"a statement FRR kept commits normally: {_out!r}")

# The false positive that kept this a warning for as long as it was one. FRR
# stores nothing for a statement at one of the measured values, the commit
# worked, and refusing it would be #208 again.
_out, _failed = _commit(["router ospf", " timers throttle spf 0 50 5000"], [])
check(_failed == 0 and "commit complete" in _out,
      f"a statement FRR is MEASURED not to read back is not a refusal: "
      f"{_out!r}")

# A rewrite whose rewritten form is absent too. The message has to name what
# FRR writes, because an operator who greps the running configuration for
# `passive-interface` finds nothing whether it applied or not.
_out, _failed = _commit(["router ospf", " passive-interface vppwan0"],
                        ["router ospf"])
check(_failed == 1 and "ip ospf passive" in _out,
      f"a refusal over a rewritten statement names what FRR writes "
      f"instead: {_out!r}")
_out, _failed = _commit(["router ospf", " passive-interface vppwan0"],
                        ["interface vppwan0", " ip ospf passive"])
check(_failed == 0 and "commit complete" in _out,
      f"and the rewrite being present is not a refusal: {_out!r}")


# The block a refused static route is named in (#251).
#
# `router static` is this VTY's word for the GLOBAL table, and a route inside
# `vrf red` is not in it. MEASURED at a live FRR 10.7.1: a route typed into
# `vrf red` is read back inside that block, indented, still spelled `ip route
# 10.9.0.0/16 10.0.0.9` - so a refusal naming `router static / route ...` for
# it sends the operator to a table the route was never in, and retyping it
# there puts it in the global table.
#
# Both tables, because a fix that simply stopped saying `router static` would
# pass a check that only looked at the VRF.
_out, _failed = _commit(_STATIC, [])
check(_failed == 1
      and "router static / 203.0.113.0/24 next-hop blackhole" in _out,
      f"a refused GLOBAL static route is still named in 'router static': "
      f"{_out!r}")

_VRF_STATIC = ["vrf red", "ip route 10.9.0.0/16 10.0.0.9", "exit"]
_out, _failed = _commit(_VRF_STATIC, [])
check(_failed == 1 and "vrf red / ip route 10.9.0.0/16 10.0.0.9" in _out,
      f"a refused VRF static route is named in the block it is in: {_out!r}")
check("router static" not in _out,
      f"and never in the global table it is not in: {_out!r}")

# The display path is where the operator goes next, so the refusal has to
# agree with it. `group_static` hoists TOP-LEVEL `ip route` lines only, which
# is why `show configuration` was right about this all along - the assumption
# was in the one renderer that had no block path to read.
_text = _cfg_with(_VRF_STATIC).text()
check("vrf red\nip route 10.9.0.0/16 10.0.0.9" in _text,
      f"'show configuration' keeps the route inside the VRF: {_text!r}")
check("router static" not in _text,
      f"and does not open a 'router static' block for it: {_text!r}")


# =====================================================================
# The next-hop check asks the table the route is IN (#255)
#
# `commit check` refuses a static route whose next hop is on no connected
# network (#95), and it used to ask that of FIB table 0 for every route in the
# candidate, `vrf red` ones included. Both answers were wrong, and both were
# MEASURED at a live VPP 26.06 and a live FRR 10.7.1 before anything was
# changed - with 172.31.99.0/24 connected only in table 10 and 200.150.0.0/24
# connected only in table 0:
#
#   vrf red / ip route 10.55.0.0/16 172.31.99.1   REFUSED, and it is correct
#   vrf red / ip route 10.55.0.0/16 200.150.0.2   ACCEPTED, and it is inert
#
# The first is the one that costs more: refusing a configuration that works is
# the harm #208 was filed for. The second is the failure the check exists for,
# committed in silence on evidence from a table the route does not forward in.
#
# Both directions are asserted here, because a fix that simply stopped checking
# VRF routes would pass a test that only looked at the false refusal.

# Trimmed from what VPP actually printed for the box above, not written by
# hand: two tables, one connected prefix in each.
_FIB_TWO_TABLES = """ipv4-VRF:0, fib_index:0, flow hash:[src dst sport dport proto flowlabel ] epoch:0 flags:none locks:[default-route:1, ]
200.150.0.0/24
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:10 buckets:1 uRPF:9 to:[0:0]]
    [0] [@4]: ipv4-glean: [src:200.150.0.0/24] pg1: mtu:9000 next:1 flags:[] ffffffffffff02fefecd294a0806
ipv4-VRF:10, fib_index:1, flow hash:[src dst sport dport proto flowlabel ] epoch:0 flags:none locks:[interface:1, CLI:1, ]
172.31.99.0/24
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:19 buckets:1 uRPF:20 to:[0:0]]
    [0] [@4]: ipv4-glean: [src:172.31.99.0/24] pg0: mtu:9000 next:2 flags:[] ffffffffffff02fe885e34870806
"""

# What FRR answers for a VRF that has a device, and for one that does not.
# `vrf red id 3 table 10 (configured)` is the only thing on this box that
# joins the NAME in an FRR block to the NUMBER the FIB is keyed by.
_SHOW_VRF = """vrf green inactive (configured)
vrf red id 3 table 10 (configured)
"""


def _commit_nh(candidate, show_vrf=_SHOW_VRF, fib=_FIB_TWO_TABLES):
    """`commit check` on `candidate` against the two-table box above.

    The real `Shell.apply`, because the check under test lives in it. The
    `_commit` helper above stubs `apply` out, which is right for the FRR
    read-back it drives and would make every assertion here pass on a check
    that never ran.
    """
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT}
    sh.candidate = _cfg_with(candidate)
    sh.running_config = lambda: _cfg_with([])
    sh.rb = type("_RB", (), {"push": lambda self, *a: None})()

    def _dp_run(_self, cmd):
        if cmd.startswith("show ip fib"):
            return 0, fib
        return 0, ""

    sh.dp = type("D", (), {"TRACE": False, "run": _dp_run,
                           "reachable": lambda _s: True})()
    # `show_vrf=None` is an FRR that could not be asked at all, which is a
    # different fixture from one that answers with no VRFs in it.
    _vrf_answer = (1, "") if show_vrf is None else (0, show_vrf)
    sh.frr = type("F", (prismash.Frr,), {
        "available": lambda _s: True,
        "running": lambda _s: [],
        "reload": lambda _s, l, check_only=False: (0, ""),
        "show": lambda _s, c: (_vrf_answer if c == "show vrf" else (1, "")),
    })()
    buf = _io.StringIO()
    with _ctx.redirect_stdout(buf):
        sh.do_commit(["check"])
    return buf.getvalue()


print("\n=== the next-hop check asks the table the route is in (#255) ===")

# The half that costs the most: a VRF route whose next hop is connected in its
# OWN table. Refusing this is refusing a configuration that works.
_out = _commit_nh(["vrf red", " ip route 10.55.0.0/16 172.31.99.1", "exit"])
check("no connected network" not in _out,
      f"a VRF route resolved IN ITS OWN table is not refused: {_out!r}")
check("not checked for a reachable next hop" not in _out,
      f"and it was really checked, not skipped: {_out!r}")

# The other half, and the one the issue was filed for: connected in the GLOBAL
# table only. It cannot forward from `vrf red`, and passing it in silence is
# the inert route this check exists to catch.
_out = _commit_nh(["vrf red", " ip route 10.55.0.0/16 200.150.0.2", "exit"])
check("on no connected network: 200.150.0.2" in _out,
      f"a VRF route resolved only in the GLOBAL table is refused: {_out!r}")

# Neither direction may be bought by breaking the global table's own check,
# which is what #95 put here in the first place.
_out = _commit_nh([" ip route 10.55.0.0/16 200.150.0.2"])
check("no connected network" not in _out,
      f"a global route resolved in table 0 is accepted: {_out!r}")
_out = _commit_nh([" ip route 10.55.0.0/16 172.31.99.1"])
check("on no connected network: 172.31.99.1" in _out,
      f"and one resolved only in a VRF is refused: {_out!r}")

# A VRF the box has no table for is SAID, not passed over. A check that
# quietly does not run is one the operator believes ran and passed - and it is
# a note rather than a refusal, because the VRF may be about to exist and
# refusing a configuration this VTY merely cannot see is the harm in #208.
_out = _commit_nh(["vrf green", " ip route 10.55.0.0/16 172.31.99.1", "exit"])
check("1 static route(s) in 'vrf green' were not checked" in _out,
      f"a VRF with no table is reported as unchecked: {_out!r}")
check("FRR reports no routing table for it" in _out,
      f"with the reason it could not be checked: {_out!r}")
check("configuration check succeeds" in _out,
      f"and it is a note, not a refusal: {_out!r}")

# FRR being unreachable is different news from a VRF having no table, and the
# note has to say which - an operator told the VRF has no table would go and
# create one that is already there.
_out = _commit_nh(["vrf red", " ip route 10.55.0.0/16 172.31.99.1", "exit"],
                  show_vrf=None)
check("FRR could not be asked which routing table it is" in _out,
      f"an FRR that cannot answer is named as the reason: {_out!r}")

# And a box with no VRF routes at all hears nothing about VRFs. A line printed
# under every commit is one nobody reads, which is the rule #205 and #208 put
# on every alarm in this VTY.
_out = _commit_nh([" ip route 10.55.0.0/16 200.150.0.2"])
check("not checked for a reachable next hop" not in _out,
      f"nothing is said about VRFs on a box that has no VRF route: {_out!r}")

# =====================================================================
# The refusal says whether this VTY had ever read the statement (#227)
#
# Two features met on the same statement and did not talk to each other.
# `commit` refuses when FRR does not keep a statement the commit introduced
# and names it (#197); `show this` marks a statement this VTY has no grammar
# for with `<- not checked` (#141). Measured on the merged tree, the refusal
# over `coalesce-time 2000` and the refusal over `bgp router-id 10.0.0.1`
# were the same five lines, although the box read one of them and forwarded
# the other word for word.
#
# The decision is to MARK, not to count. Argued in do_commit; the two
# properties it rests on are asserted here rather than left in the argument.
print("\n=== the refusal says which statements this VTY never read (#227) ===")

_BGP_UNREAD = ["router bgp 65000", " neighbor 10.0.0.2 remote-as 65001",
               " coalesce-time 2000", "exit"]
_BGP_HELD = ["router bgp 65000", " neighbor 10.0.0.2 remote-as 65001", "exit"]



def _refusal_lines(out):
    """The statement lines of a refusal, as printed, indent stripped."""
    return [l[4:] for l in out.splitlines()
            if l.startswith("    ") and not l.startswith("     ")]


_out, _failed = _commit(_BGP_UNREAD, _BGP_HELD)
check(_failed == 1 and "coalesce-time 2000" in _out,
      f"the refusal still names the statement FRR dropped: {_out!r}")
# Line-exact, not a substring. A substring needle cannot tell the marker from
# a LONGER one that starts with it, and that is not a hypothetical: the marker
# this project ships replaced `    <- not checked by this VTY`, and a check
# written `f"...{UNCHECKED_MARK}" in out` stays green over the old spelling
# coming back. Measured - the mutation that reinstated the long form gave zero
# red until this line was written this way.
check(f"coalesce-time 2000{UNCHECKED_MARK}" in _refusal_lines(_out),
      f"and marks it, because no grammar here ever read it: {_out!r}")
check("no grammar in this VTY" in _out,
      f"with one line saying what the mark means: {_out!r}")

# The other side, and it is the one that makes the mark a claim rather than
# decoration: a statement the command tree walks is dropped by FRR just as
# hard and must NOT be marked. Same block, same commit shape, same refusal.
_BGP_READ = ["router bgp 65000", " neighbor 10.0.0.2 remote-as 65001",
             " bgp router-id 10.0.0.1", "exit"]
_out, _failed = _commit(_BGP_READ, _BGP_HELD)
check(_failed == 1 and "bgp router-id 10.0.0.1" in _out,
      f"a statement the tree reads is refused the same way: {_out!r}")
check(UNCHECKED_MARK.strip() not in _out,
      f"and carries no mark, nor the line explaining one: {_out!r}")

# It is decided by the same walk as the display, not by a second answer kept
# beside it. Teaching the tree `coalesce-time` has to unmark the REFUSAL too,
# with nothing else edited - the property #141's design rests on, asserted
# here because this is a second caller of it.
_taught = dict(prismash.BGP_BLOCK)
_taught["coalesce-time"] = ("how long to wait for peers", "<ms>")
_was = prismash.BGP_BLOCK
try:
    prismash.BGP_BLOCK = _taught
    _out, _failed = _commit(_BGP_UNREAD, _BGP_HELD)
finally:
    prismash.BGP_BLOCK = _was
check(_failed == 1 and UNCHECKED_MARK.strip() not in _out,
      f"teaching the tree the statement unmarks the refusal too: {_out!r}")
_out, _failed = _commit(_BGP_UNREAD, _BGP_HELD)
check(UNCHECKED_MARK in _out,
      f"and the tree really was put back: {_out!r}")

# A static route is the case that would go wrong quietly, and it is the case
# #197's own refusal is about. Typed inside `router static` it is STORED as
# `ip route ...` at the top of the routing section - measured, not assumed:
# the shell above produces exactly that list - so only the display returns it
# to the block. Read by position it would carry the mark in every refusal
# about a static route on the box. This is the trap #141 hit when it counted
# them for `commit check`, met again by a second caller.
_TYPED_ROUTE = ["ip route 203.0.113.0/24 blackhole"]
_out, _failed = _commit(_TYPED_ROUTE, [])
check(_failed == 1 and "203.0.113.0/24 next-hop blackhole" in _out,
      f"the static route refusal still names the route: {_out!r}")
check(UNCHECKED_MARK.strip() not in _out,
      f"and a static route is not marked: this VTY has a block for it and "
      f"checks it line by line: {_out!r}")
# The same route inside a VRF stays marked, and that is #141's decision rather
# than an oversight: nobody here read the block it is in, so nothing about it
# was checked, whatever the statement looks like.
_VRF_ROUTE = ["vrf red", " ip route 10.9.0.0/16 10.0.0.9", "exit-vrf"]
_out, _failed = _commit(_VRF_ROUTE, ["vrf red", "exit-vrf"])
check(_failed == 1 and
      any(l.endswith(UNCHECKED_MARK) for l in _refusal_lines(_out)),
      f"a route in a block this VTY has no grammar for is marked: {_out!r}")

# The same words in two blocks, with two answers. `soft-reconfiguration
# inbound` is modelled inside the address family and not inside `router bgp`,
# so a candidate holding both has one statement the VTY did read. Claiming
# "never read" over it is the confidently wrong direction, so the mark is
# withheld when ANY occurrence was read.
_BOTH = ["router bgp 65000",
         " neighbor 10.0.0.2 soft-reconfiguration inbound",
         " address-family ipv4 unicast",
         "  neighbor 10.0.0.2 soft-reconfiguration inbound",
         " exit-address-family", "exit"]
check(prismash.unchecked_in(_BOTH) == set(),
      f"read in one block is read: {prismash.unchecked_in(_BOTH)}")
_out, _failed = _commit(_BOTH, ["router bgp 65000", "exit"])
check(_failed == 1 and UNCHECKED_MARK.strip() not in _out,
      f"so the refusal does not call it unread: {_out!r}")
check(prismash.unchecked_in(["router bgp 65000",
                           " neighbor 10.0.0.2 soft-reconfiguration inbound",
                           "exit"])
      == {"neighbor 10.0.0.2 soft-reconfiguration inbound"},
      "and in the outer block alone it is still unread")

# The explanatory line follows what is PRINTED, not what was dropped. The list
# stops at five; a line about a mark none of the five carries sends the reader
# looking for one.
_MANY = ["router bgp 65000"] + [f" neighbor 10.0.0.{i} remote-as 6500{i}"
                                for i in range(1, 7)] + \
        [" coalesce-time 2000", "exit"]
_out, _failed = _commit(_MANY, ["router bgp 65000", "exit"])
check("... and 2 more" in _out, f"the list still stops at five: {_out!r}")
check(UNCHECKED_MARK.strip() not in _out,
      f"and says nothing about a mark when none of the five carries one: "
      f"{_out!r}")

# --- the #205/#208 objection, measured rather than argued ---
#
# A marker that fires on a line every box has is one operators learn to skip,
# and #141 measured this very marker doing it: three of the twenty-four
# statements a working provider edge reads back are marked, one of them
# `no bgp ebgp-requires-policy`. That is why `commit` itself says nothing.
#
# It does not reach the refusal, and this is the measurement the decision
# rests on: the same read-back, committed against an FRR that is holding it,
# produces `commit complete` and no message for a marker to be in. The
# refusal is not a display of the configuration - it exists only when a
# statement is already gone.
_out, _failed = _commit(FRR_READBACK, FRR_READBACK)
check(_failed == 0 and _out == "commit complete\n",
      f"a working provider edge produces no refusal to mark: {_out!r}")
# And when one of those every-box lines really is dropped, the mark is the
# marker being right - the same argument #141 made for the display.
_held = [l for l in FRR_READBACK
         if l.strip() != "no bgp ebgp-requires-policy"]
_out, _failed = _commit(FRR_READBACK, _held)
check(_failed == 1 and
      f"no bgp ebgp-requires-policy{UNCHECKED_MARK}" in _refusal_lines(_out),
      f"but a dropped one is marked: {_out!r}")

# The mark must not reach FRR from here either. do_commit hands the candidate
# to apply() before it prints anything, and the candidate is the same object
# `unchecked_in` reads.
_seen = []


def _watch(cfg, check_only=False):
    _seen.append(list(cfg.routing))
    return True


_sh = prismash.Shell()
_sh.candidate = _cfg_with(_BGP_UNREAD)
_sh.running_config = lambda: _cfg_with([])
_sh.apply = _watch
_sh.rb = type("_RB", (), {"push": lambda self, *a: None})()
_sh.frr = _FrrHolding(_BGP_HELD)
with _ctx.redirect_stdout(_io.StringIO()):
    _sh.do_commit([])
check(_seen and _seen[0] == _BGP_UNREAD,
      f"the candidate handed to apply() is unmarked: {_seen}")
check(_sh.candidate.routing == _BGP_UNREAD,
      f"and the candidate itself is untouched: {_sh.candidate.routing}")


# =====================================================================
# A refusal fits the console it is read on (#252)
#
# MEASURED on the merged tree, every line of the read-back refusal, in each
# shape it has after #197 promoted it, #251 gave it the VRF and #227 gave it
# the mark:
#
#    71  error: FRR accepted 1 statement(s) of this commit and did not keep...
#    59      vrf red / ip route 10.9.0.0/16 10.0.0.9  <- not checked
#    75      a statement marked '<- not checked' has no grammar in this VTY...
#    58      to FRR word for word, so this box never read it either
#    82      this commit is NOT in effect as written, and nothing was rolled...
#    52      of it is applied. 'rollback 1' puts the box back
#   178      the configuration this box holds and the one FRR is running are...
#
# and the branch beside it, which was worse:
#
#   116  error: the routing half of this commit reached FRR and changed...
#    82      the usual cause is a daemon that is not running; 'show version'...
#   105      if these statements are all at FRR's own default values, FRR...
#
# The 82-column line is the evidence for the rule: it is a line somebody
# wrapped BY HAND, at what they took for 80 columns, and it was over. Five of
# them were.
#
# THE RULE, and it is a split one:
#
#   * A message's own PROSE is wrapped by `console_prose` at CONSOLE_COLUMNS
#     and is asserted to fit. There is no reason for it not to.
#   * A QUOTED STATEMENT is printed whole, past the margin if it has to be.
#
# The split is argued from what an operator does with each half, and the
# argument is in `console_prose`. In short: prose is read, statements are
# grepped for in `show configuration` or typed back in at the prompt, and the
# two other candidates both destroy exactly that. Wrapping puts a newline
# inside a token, and a newline in the scrollback is a real one - the
# statement stops matching a search for itself. Truncating loses an end, and
# which end carried the interesting word cannot be known here: #251 put the
# VRF at the front precisely because a VRF route has no top-level spelling,
# and what FRR declined is usually the last word. Left alone, the terminal
# soft-wraps and every byte survives in order, which costs only the shape of
# the message.
#
# So the box owes the operator one guarantee instead: it adds nothing of its
# own beyond the four-space indent and #141's sixteen-character mark, and a
# statement that CAN fit does fit. That is the third assertion below.
print("\n=== every line commit, commit check, rollback, save and --load "
      "print fits an 80-column console (#252, #271, #276) ===")


class _FrrStalled:
    """An FRR that took the reload and is still holding none of it.

    frr-reload exits 0 - so `statements_frr_dropped` is not what answers - and
    reports every new statement outstanding, which is the `nothing_took`
    branch. The two refusals print from the same `if/elif`, and the console
    they are read on does not care which one fired.
    """

    def __init__(self, running, outstanding_lines):
        self._running = running
        self._out = outstanding_lines

    def running(self):
        return self._running

    def reload(self, _lines, check_only=False):
        return 0, "stub"

    def outstanding(self, _out):
        return self._out


def _commit_stalled(candidate, outstanding):
    sh = prismash.Shell()
    sh.candidate = _cfg_with(candidate)
    sh.running_config = lambda: _cfg_with([])
    sh.apply = lambda cfg, check_only=False: True
    sh.rb = type("_RB", (), {"push": lambda self, *a: None})()
    sh.frr = _FrrStalled(candidate, outstanding)
    buf = _io.StringIO()
    with _ctx.redirect_stdout(buf):
        sh.do_commit([])
    return buf.getvalue(), sh.failed


# Every shape the refusal has, with statements short enough that nothing here
# is excused by the rule above. One fixture per line the message can print:
# the plain refusal, the VRF path #251 added, the mark #227 added, the
# "... and N more" tail, the "FRR writes X instead" sub-line, and the stalled
# branch. A fixture set that missed one would leave that line unmeasured, and
# an unmeasured line is how the 178 got here.
_FITTING_REFUSALS = [
    (["router bgp 65000", " bgp router-id 10.0.0.1", "exit"],
     ["router bgp 65000", "exit"]),
    (["vrf red", " ip route 10.9.0.0/16 10.0.0.9", "exit-vrf"],
     ["vrf red", "exit-vrf"]),
    (_BGP_UNREAD, _BGP_HELD),
    (_MANY, ["router bgp 65000", "exit"]),
    (["router ospf", " passive-interface vppwan0"], ["router ospf"]),
]
_refusal_out = []
for _cand, _held in _FITTING_REFUSALS:
    _out, _failed = _commit(_cand, _held)
    check(_failed == 1,
          f"the fixture must really refuse, or it measures nothing: {_out!r}")
    _refusal_out.append(_out)
_out, _failed = _commit_stalled(["router ospf", " ip ospf area 0"],
                                ["ip ospf area 0"])
check(_failed == 1,
      f"and so must the branch beside it: {_out!r}")
_refusal_out.append(_out)

# --- and the rest of what `commit` and `commit check` print (#271) ---
#
# #252 stopped at the two read-back branches and said so. Everything else the
# two commands print was measured on the way out and left for this issue,
# which named four lines between 84 and 107 columns. Driven here rather than
# read off the source, the set is TWENTY-EIGHT, from 80 to 231 columns, and
# the widest of them is not on either list:
#
#   231  warning: <subif> matches outer N inner M AND anything carrying more...
#   189      this session did not arrive over SSH, so there is no address...
#   186      they would commit and forward nothing. staticd is started by frr...
#   135  error: this change leaves nobody able to configure the box: after...
#   133  this commit can cut off the session you are using: it changes SSH...
#   133      line 3: password <hash>    (password: expected a crypt hash...)
#   127      add 'nat cgnat logging udp <collector> src <source>' beside it...
#   118  error: the change is applied, but no rollback revision could be...
#   107    routing statements are checked for structure only; FRR validates...
#   104  note: mtu removed from <if>; the driver default applies after the...
#   104      the box still rolls back at <when>; 'rollback 0' and commit again...
#   103  note: this change alters what this box advertises, and the prefix...
#    99      give an account an authorized key, or allow password authenti...
#    97      the statement would commit and reach nothing. Enable bgpd in...
#    95  error: the dataplane reports configuration this VTY does not model...
#    94  warning: the revert could NOT be armed on the box: <path>: <why>
#    93  error: <route>: <next hop> is on no connected network
#    92      the dataplane refuses the statement, and it would refuse it half...
#    90      this session arrived on <addr>, and what this box originates is...
#    89  error: cgnat logging enable: UDP export has no collector, so nothing...
#    86  rollback at <when>, in N minute(s), unless you type 'commit' to...
#    85  error: refusing to commit: there are configuration lines this VTY...
#    85  error: <statement>: this box has no interface <name>
#    85      membership of prisma-cgnat allows CGNAT configuration, prisma-admin...
#    85      give another account 'role administrator' first, or allow root...
#    84  note: no BGP paths received yet, so AS-path lists cannot be checked...
#    84      FRR reports no routing table for it, so nothing on this box holds...
#    83  error: this change locks everybody out: no account could log in over...
#    82      it will run in N minute(s) IF THIS SESSION LASTS THAT LONG, and...
#    81      the revert is armed on the box, not in this session: logging out...
#
# The fixtures go into the SAME `_refusal_out` the sweep below already reads,
# because a second sweep over a second list is the two-literal mistake that
# put a 178-column line on the screen while the display's own assertion
# stayed green for two releases (#252). One list, one budget, one check.
#
# Each fixture carries a needle, for the reason the refusal fixtures above
# carry `_failed == 1`: a fixture whose branch stopped being reached measures
# nothing at all and says nothing about it.


class _Api271:
    """A dataplane API that answers OK and dumps nothing."""

    def __init__(self, state=_vppapi.OK):
        self.state = state

    def available(self):
        return (self.state, "the dataplane is not running")

    def call(self, message, args):
        return _vppapi.Reply(_vppapi.OK)

    def dump(self, message, args):
        return _vppapi.Reply(_vppapi.OK, fields={"rows": []})

    def interface_index(self, name):
        return 1


class _Dp271:
    """A dataplane answering canned text, keyed on the command's prefix."""

    TRACE = False

    def __init__(self, answers=None):
        self.answers = answers or {}

    def run(self, cmd):
        for prefix, text in self.answers.items():
            if cmd.startswith(prefix):
                return 0, text
        return 0, ""

    def running(self):
        return 0, ""


class _Frr271(prismash.Frr):
    """
    A stand-in that answers `show`, and takes everything else from the real Frr.

    Derived and not free-standing. As a free-standing class it stopped having
    whatever the product added next: `daemons()` arrived with #272 and
    `show_version` called it, and this stub answered AttributeError - a merge
    that both sides passed on their own. Deriving means a method built on
    `show` keeps working here without this file being edited, and one that is
    genuinely new fails loudly rather than being quietly stubbed out.
    """

    def __init__(self, running=(), daemons="", vrf="", avail=True):
        self._running, self._daemons = list(running), daemons
        self._vrf, self._avail = vrf, avail

    def available(self):
        return self._avail

    def running(self):
        return self._running

    def show(self, what):
        if "daemons" in what:
            return 0, self._daemons
        if "vrf" in what:
            return 0, self._vrf
        return 0, ""

    def reload(self, lines, check_only=False):
        return 0, ""

    def outstanding(self, out):
        return []

    @staticmethod
    def diagnostics(out):
        return []


# Trimmed from a live `show interface`, so the existence check has a box to
# answer from rather than being skipped.
_IF271 = ("Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter\n"
          "GigabitEthernet0/8/0 1     up          9000/0/0/0\n"
          "local0                0    down          0/0/0/0\n")
# One connected prefix, in table 0 and nowhere else.
_FIB271 = ("ipv4-VRF:0, fib_index:0, flow hash:[src dst sport dport proto "
           "flowlabel ] epoch:0 flags:none locks:[default-route:1, ]\n"
           "200.150.0.0/24\n"
           "  unicast-ip4-chain\n"
           "  [@0]: dpo-load-balance: [proto:ip4 index:10 buckets:1 uRPF:9 "
           "to:[0:0]]\n"
           "    [0] [@4]: ipv4-glean: [src:200.150.0.0/24] "
           "GigabitEthernet0/8/0: mtu:9000\n")
_HEALTHY271 = "zebra mgmtd staticd bgpd ospfd isisd"
_HASH271 = "$6$salt$" + "0" * 60


def _shell271(candidate_text, running_text="", caps=None, api=None, dp=None,
              frr=None):
    """A Shell whose every outside edge is stubbed, and nothing else.

    `apply` is NOT stubbed - the checks that print most of the lines measured
    above live inside it, and a fixture that stubbed it would sweep an empty
    message and report it green.
    """
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT} if caps is None else caps
    sh.api = api or _Api271()
    sh.dp = dp or _Dp271()
    sh.frr = frr or _Frr271()
    sh.rb = type("_RB", (), {"push": lambda self, *a: None})()
    sh.confirm = type("_C", (), {
        "read": lambda self: None, "clear": lambda self: None,
        "arm": lambda self, *a: None,
        "path": "/etc/prisma-routing/confirm"})()
    sh.sys = type("_S", (), {
        "write_ssh": lambda self, *a, **k: (True, ""),
        "apply_users": lambda self, *a, **k: (True, ""),
        "read_ssh": lambda self: {},
        "read_dns": lambda self: [],
        "read_users": lambda self: {}})()
    # Fixtures written flat, in the model's words, and read as the file
    # format they stand for (#446).
    sh.candidate = _cfg.Config.parse(in_nat_block(candidate_text))
    sh.running_config = lambda: _cfg.Config.parse(in_nat_block(running_text))
    return sh


def _drive271(sh, args, needle):
    """Run one command, prove it reached the branch, and keep what it printed."""
    buf = _io.StringIO()
    with _ctx.redirect_stdout(buf):
        sh.do_commit(list(args))
    out = buf.getvalue()
    check(needle in " ".join(out.split()),
          f"the fixture must reach the message, or it measures nothing - "
          f"{needle!r} not in: {out!r}")
    _refusal_out.append(out)
    return out


_ROUTE271 = "! routing\nrouter static\n 10.55.0.0/16 next-hop 172.31.99.1\n"
_STATIC271 = "! routing\nrouter static\n 203.0.113.0/24 next-hop blackhole\n"
_USER271 = f"user alice\n password {_HASH271}\n role administrator\n"

# `commit check`: the sentence the command exists to say, and the count beside
# it. This is the 107-column line the issue is named for.
_drive271(_shell271("! routing\nrouter bgp 65000\n no bgp ebgp-requires-policy\n"
                    "exit\n", frr=_Frr271(daemons=_HEALTHY271)),
          ["check"], "routing statements are checked for structure only")

# An account that may not commit at all, which is answered before anything is
# checked - and names the groups, at 85 columns.
_drive271(_shell271(_STATIC271, caps=set()), [],
          "membership of prisma-cgnat allows CGNAT configuration")

# A candidate carrying a line nobody can read: the headline, and the reason
# beside the line, which together measured 133 columns on a password.
_WHY271 = ("password: expected a crypt hash such as $y$... or $6$... "
           "(openssl passwd -6), not a plaintext password")
_sh = _shell271("")
_sh.candidate.unknown = [(3, "password secret", _WHY271)]
_drive271(_sh, [], "refusing to commit")

# Both SSH refusals, which fire from the same change.
_drive271(_shell271("ssh password-authentication no\n" + _USER271,
                    "ssh password-authentication yes\n" + _USER271), [],
          "this change locks everybody out")
_drive271(_shell271("ssh permit-root-login no\nssh password-authentication yes\n"
                    f"user alice\n password {_HASH271}\n role cgnat-operator\n",
                    "ssh permit-root-login no\nssh password-authentication yes\n"
                    + _USER271), [],
          "this change leaves nobody able to configure the box")

# Export turned on with nowhere to send it.
_drive271(_shell271("! cgnat\nnat\n cgnat\n  logging enable\n  exit\n exit\n"), [],
          "UDP export has no collector")

# An interface name this box does not have, in both shapes: a stanza and an
# `update-source`.
_drive271(_shell271("! routing\ninterface Ten0\n ip ospf area 0\n",
                    dp=_Dp271({"show interface": _IF271})), [],
          "this box has no such interface")
_drive271(_shell271("! routing\nrouter bgp 65000\n"
                    " neighbor 10.0.0.1 remote-as 65001\n"
                    " neighbor 10.0.0.1 update-source Loopback0\nexit\n",
                    dp=_Dp271({"show interface": _IF271}),
                    frr=_Frr271(daemons=_HEALTHY271)), [],
          "this box has no interface Loopback0")

# A daemon that is not running, in both spellings of the advice: the one for a
# daemon somebody enables, and the one for a daemon frr starts itself - which
# is the 186-column line.
_drive271(_shell271("! routing\nrouter bgp 65000\n bgp router-id 10.0.0.1\n"
                    "exit\n", frr=_Frr271(daemons="zebra mgmtd staticd")), [],
          "Enable bgpd in /etc/frr/daemons")
_drive271(_shell271(_STATIC271, frr=_Frr271(daemons="zebra mgmtd bgpd")), [],
          "staticd is started by frr itself")

# The next hop on no connected network, and the VRF whose table FRR has not
# got - the 93 and the 84 the issue names.
_drive271(_shell271(_ROUTE271,
                    dp=_Dp271({"show interface": _IF271,
                               "show ip fib": _FIB271}),
                    frr=_Frr271(running=["ip route 10.55.0.0/16 172.31.99.1"],
                                daemons=_HEALTHY271,
                                vrf="vrf default id 0 table 254\n")), [],
          "this next hop is on no connected network")
_drive271(_shell271("! routing\nvrf red\n ip route 10.55.0.0/16 172.31.99.1\n"
                    "exit-vrf\n",
                    dp=_Dp271({"show interface": _IF271,
                               "show ip fib": _FIB271}),
                    frr=_Frr271(daemons=_HEALTHY271,
                                vrf="vrf default id 0 table 254\n")), [],
          "FRR reports no routing table for it")

# The check that could NOT be made, in both of its reasons.
_ADVERT271 = ("! routing\nrouter bgp 65000\n network 203.0.113.0/24\nexit\n",
              "! routing\nrouter bgp 65000\nexit\n")
_saved_session = prismash.session_local_address
try:
    prismash.session_local_address = lambda environ=None: None
    _drive271(_shell271(_ADVERT271[1], _ADVERT271[0],
                        frr=_Frr271(running=[], daemons=_HEALTHY271)),
              ["check"], "this session did not arrive over SSH")
    prismash.session_local_address = lambda environ=None: "2001:db8::1"
    _drive271(_shell271(_ADVERT271[1], _ADVERT271[0],
                        frr=_Frr271(running=[], daemons=_HEALTHY271)),
              ["check"], "is read in IPv4 only")
finally:
    prismash.session_local_address = _saved_session

# An AS-path list with no paths to measure it against.
_drive271(_shell271("! routing\nbgp as-path access-list FOO seq 5 permit "
                    "^65001_\n", frr=_Frr271(daemons=_HEALTHY271)),
          ["check"], "no BGP paths received yet")

# An mtu going away, which is reported rather than guessed at.
_drive271(_shell271("! interfaces\ninterface GigabitEthernet0/8/0\n",
                    "! interfaces\ninterface GigabitEthernet0/8/0\n mtu 9000\n",
                    dp=_Dp271({"show interface": _IF271})), [],
          "note: mtu removed from GigabitEthernet0/8/0")

# The QinQ sub-interface, whose warning was the widest line `commit` printed
# anywhere: 231 columns.
_drive271(_shell271("! interfaces\ninterface GigabitEthernet0/8/0.100\n"
                    " vlan 100 inner 900\n",
                    dp=_Dp271({"show interface": _IF271})), [],
          "also matches anything carrying more tags")

# A change that can cost the operator the session they are typing in, and the
# window it arms - both branches of arm_confirm, because the one that cannot
# write the file is the one whose promise is weaker and has to say so.
_drive271(_shell271("ssh port 2222\n" + _USER271, "ssh port 22\n" + _USER271),
          [], "this commit can cut off the session you are using")
_sh = _shell271(_STATIC271, frr=_Frr271(daemons=_HEALTHY271))


def _refuse_arm(*_a):
    raise OSError(13, "Permission denied", "/etc/prisma-routing/confirm")


_sh.confirm.arm = _refuse_arm
_drive271(_sh, ["confirmed", "10"], "the revert could NOT be armed on the box")

# Confirming a window whose file cannot be removed: the commit is confirmed
# and the box will still revert, which is said in as many words.
_sh = _shell271(_STATIC271, frr=_Frr271(daemons=_HEALTHY271))
_sh.confirm.read = lambda: {"deadline": 1789000000.0, "config": ""}
_sh.confirm.clear = _refuse_arm
_drive271(_sh, [], "the box still rolls back at")

# A commit that applied and could record no way back from it.
_sh = _shell271(_STATIC271,
                frr=_Frr271(running=["ip route 203.0.113.0/24 blackhole"],
                            daemons=_HEALTHY271))
_sh.rb = type("_RB", (), {"push": lambda self, *a: (_ for _ in ()).throw(
    prismash.RollbackUnavailable(
        "/etc/prisma-routing/rollback: Permission denied"))})()
_drive271(_sh, [], "no rollback revision could be recorded")

# And the dataplane holding configuration this VTY cannot model, which
# `commit` reads back before it decides anything. The real `running_config`
# runs here - stubbing it, as every fixture above does, is what would make
# this branch unreachable and the fixture silent.
_sh = _shell271(_STATIC271, frr=_Frr271(daemons=_HEALTHY271),
                dp=_Dp271({"show bpa config": "cgnat pool p\n"
                                              " inside 100.64.0.0/22\n"
                                              " widget 3\n"}))
del _sh.running_config
_drive271(_sh, [], "committing would REMOVE it")

# A line the VTY cannot read is QUOTED, so it is printed whole - the same
# exemption the refusal's statements have, and for the same reason: it is the
# row the operator goes and edits, and a real SHA-512 crypt hash is 98 columns
# on its own. What used to share that row was the REASON, which is prose and
# is now wrapped under it: together they measured 133 columns (#271).
_LONG271 = "$6$" + "s" * 8 + "$" + "0" * 86
_sh = _shell271("")
_sh.candidate.unknown = [(3, f"password {_LONG271}", _WHY271)]
_buf = _io.StringIO()
with _ctx.redirect_stdout(_buf):
    _sh.do_commit([])
_out = _buf.getvalue()
check(f"    line 3: password {_LONG271}" in _out.splitlines(),
      f"a line too wide for the console is quoted whole, on one row: {_out!r}")
check(_LONG271 + "\n" not in _out.replace(f"password {_LONG271}\n", ""),
      f"and nowhere else, in pieces: {_out!r}")
print(f"  the widest line quoted whole: "
      f"{len('    line 3: password ' + _LONG271)} columns")


# --- the same shape outside `commit`, and the one inside it #271 left (#276) ---
#
# #271 closed `commit` and `commit check` and recorded two limits rather than
# doing them quietly. Both are the SAME defect, which is a row carrying a
# QUOTED LINE and PROSE at once: neither half can be guaranteed there, because
# a statement MUST be printed whole and a sentence MUST be wrapped, and under
# one budget guaranteeing either breaks the other.
#
#   * `err(f"{stmt}: {why}")`, in the BGP, IS-IS, OSPF, route-map, AS-path and
#     prefix-range checks. #271 measured every one inside 79 columns on the
#     fixtures it drove and therefore did not touch them, because
#     restructuring eight messages on no measurement is a wording change.
#     That was a limit, not a clean bill, and it said so.
#   * `Shell.revert_confirm` and `Shell.load`, which print the `line <n>:
#     <text>    (<why>)` row that #271 split in `do_commit`. They belong to
#     `rollback` and to `--load`, not to `commit`.
#
# `save` is driven here for the same reason: the issue names it beside the
# other two, and what it prints had never been counted either. One of its rows
# was wrapped BY HAND at what somebody took for 80 columns and is 87, which is
# the defect #252 is named for, in a command #252 did not drive.
#
# Driven here on the input an ISP actually types - a real route-map name, an
# IPv6 neighbour, an IPv6 prefix-list, an AS-path list name, a crypt hash
# pasted without its leading '$', the box's own configuration path - every one
# of them measures over, from 80 to 264 columns:
#
#   264  error: <neighbor ... route-map RM-TRANSIT-V6-IN in>: no route-map...
#   222      line 2: password 6$Xq7kPz2r$4Pv...    (password: expected a crypt...)
#   217  error: <neighbor 2001:db8:... route-reflector-client>: ... has no...
#   201  error: <ipv6 prefix-list PL-... ge 24 le 16>: prefix range refused...
#   184  error: <neighbor 2001:db8:1000:2::1 route-map ... in>: ... no 'remote-as'
#   174  error: <bgp as-path access-list AS-PATH-...>: the regular expression...
#   153  error: <neighbor 2001:db8:aaaa:bbbb::1 peer-group UPSTREAM-V6>: peer...
#   141  error: <route-map RM-CUSTOMER-AGGREGATES-OUT deny 20>: a deny clause...
#   135  warning: <match community CL-CUSTOMER-BLACKHOLE-V6>: no community...
#   131  error: <passive-interface GigabitEthernet0/8/0.4094>: this box has no...
#   128  warning: <route-map RM-CUSTOMER-AGGREGATES-OUT permit 10>: no 'match'...
#   122  warning: <route-map RM-CUSTOMER-AGGREGATES-OUT deny 20>: ... nothing uses it
#   116  warning: <match rpki notfound>: no RPKI cache is configured, so every...
#   113  commit confirmed 10: nobody confirmed it within 10 minute(s), so this...
#   104  error: <ip router isis CORE-BACKBONE-LEVEL2>: no 'router isis ...'...
#   103  error: loaded, but no rollback revision could be recorded: <path>
#   120  error: cannot write <CONFIG_PATH>: [Errno 13] Permission denied: ...
#    98  error: giving up after 5 attempts: this box is still on the...
#    87      this box rolls the running configuration back at <when>, and does not
#    97  warning: <router isis CORE-BACKBONE-LEVEL2>: no interface joins it...
#    91  error: <path>: refusing to load, these lines are not understood:
#    88  error: rolled back, but <path> could not be removed: Permission denied
#    85  error: revision 3 cannot be read: <path>
#    84  error: <path>: refusing to load, the check above says why
#    80  error: the configuration to roll back to has lines this VTY does not...
#    80      the configuration it was to go back to is in <path>.failed
#
# The last two are one column over, which is the whole argument for measuring
# rather than reading: both look like they fit. The four rows carrying <path>
# are measured at the box's own `/etc/prisma-routing/...`, and that width is the
# operator's to choose, not this VTY's.
#
# Measured inside the budget on the same run, and therefore NOT touched, which
# is what #271 did with these and the reason this list is written down:
#
#    70  error: the configuration to roll back to does not validate on this box
#    67  error: the rollback failed (attempt 1 of 5); it will be tried again
#    61  use 'show configuration compare' to review, 'commit' to apply
#    56  error: the box is still on the unconfirmed configuration
#    56  configuration saved to <CONFIG_PATH>
#    51  warning: what you just saved has NOT been confirmed
#    26  load complete (revision 3)
#
# The fixtures below append to the SAME `_refusal_out` the sweep already
# reads. `apply()` is not stubbed, for the reason #271 gives: the checks that
# print these lines live inside it.


def _drive276(sh, run, needle, keep=True):
    """Drive one command that is not `commit`, and keep what it printed.

    The needle is the same guarantee `_drive271` asks for: a fixture whose
    branch stopped being reached measures nothing and says nothing about it.

    `keep=False` for the fixtures that deliberately QUOTE something wider than
    the console: those are asserted whole below rather than swept, the same
    way #271 keeps its crypt-hash fixture out of `_refusal_out`. Their PROSE
    is measured all the same, by the fixture beside them that quotes a short
    line and carries the identical reason.
    """
    buf = _io.StringIO()
    with _ctx.redirect_stdout(buf):
        run(sh)
    out = buf.getvalue()
    check(needle in " ".join(out.split()),
          f"the fixture must reach the message, or it measures nothing - "
          f"{needle!r} not in: {out!r}")
    if keep:
        _refusal_out.append(out)
    return out


# 1. `err(f"{stmt}: {why}")`, one fixture per check that prints it. The input
#    is what an ISP types: IPv6 neighbours, and names long enough to say what
#    the policy is for.
_drive271(_shell271("! routing\nrouter bgp 65000\n"
                    " neighbor 2001:db8:1000:2::1 remote-as 65001\n"
                    " neighbor 2001:db8:1000:2::1 route-map RM-TRANSIT-V6-IN "
                    "in\nexit\n", frr=_Frr271(daemons=_HEALTHY271)), [],
          "no route-map called 'RM-TRANSIT-V6-IN' is defined")
_drive271(_shell271("! routing\nrouter bgp 65000\n"
                    " neighbor 2001:db8:aaaa:bbbb::1 peer-group UPSTREAM-V6\n"
                    "exit\n", frr=_Frr271(daemons=_HEALTHY271)), [],
          "is not declared; declare it with")
_drive271(_shell271("! routing\nrouter bgp 65000\n"
                    " neighbor 2001:db8:aaaa:bbbb:cccc:dddd:eeee:ffff "
                    "route-reflector-client\nexit\n",
                    frr=_Frr271(daemons=_HEALTHY271)), [],
          "has no 'remote-as'")
_drive271(_shell271("! routing\nbgp as-path access-list AS-PATH-CUST-V6 "
                    "seq 5 permit ^65001_(\n",
                    frr=_Frr271(daemons=_HEALTHY271)), [],
          "the regular expression does not compile")
_drive271(_shell271("! routing\nipv6 prefix-list PL-CUST-V6 "
                    "seq 10 permit 2001:db8::/32 ge 24 le 16\n",
                    frr=_Frr271(daemons=_HEALTHY271)), [],
          "prefix range refused for 2001:db8::/32")
_drive271(_shell271("! routing\nrouter ospf\n"
                    " passive-interface GigabitEthernet0/8/0.4094\n",
                    dp=_Dp271({"show interface": _IF271}),
                    frr=_Frr271(daemons=_HEALTHY271)), [],
          "so nothing is made passive")
_drive271(_shell271("! routing\ninterface GigabitEthernet0/8/0\n"
                    " ip router isis CORE-BACKBONE-LEVEL2\n",
                    dp=_Dp271({"show interface": _IF271}),
                    frr=_Frr271(daemons=_HEALTHY271)), [],
          "declares that instance")
_drive271(_shell271("! routing\nroute-map RM-CUSTOMER-AGGREGATES-OUT deny 20\n"
                    " set local-preference 250\n set community 65000:100\n",
                    frr=_Frr271(daemons=_HEALTHY271)), [],
          "the set never runs, because the route is denied")

# 2. The warning half of the same row, which has the same defect and no
#    refusal to make it obvious.
_drive271(_shell271("! routing\nroute-map RM-CUSTOMER-IN permit 10\n"
                    " match community CL-CUSTOMER-BLACKHOLE-V6\n"
                    " set local-preference 50\n",
                    frr=_Frr271(daemons=_HEALTHY271)), [],
          "no community list called 'CL-CUSTOMER-BLACKHOLE-V6' is defined")
_drive271(_shell271("! routing\nroute-map RM-TRANSIT-V6-IN permit 10\n"
                    " match rpki notfound\n",
                    frr=_Frr271(daemons=_HEALTHY271)), [],
          "no RPKI cache is configured")
_drive271(_shell271("! routing\n"
                    "route-map RM-CUSTOMER-AGGREGATES-OUT permit 10\n"
                    " set community 65000:100\n",
                    frr=_Frr271(daemons=_HEALTHY271)), [],
          "no 'match': this permits every route")
_drive271(_shell271("! routing\nrouter isis CORE-BACKBONE-LEVEL2\n"
                    " net 49.0001.0000.0000.0001.00\n",
                    frr=_Frr271(daemons=_HEALTHY271)), [],
          "no interface joins it, so it runs and carries nothing")

# 3. `rollback`: the configuration a "commit confirmed" was measured against,
#    read back and found to carry a line this VTY cannot hold. Both halves of
#    the deadline, because the in-session one is read by an operator and the
#    unattended one by prisma-routing-confirm.service, on the same console.
#
# Two configurations that carry the same refusal, and they are two because
# the sweep cannot tell a quoted line from prose. `_BAD276` quotes something
# short, so everything it prints is swept; `_WIDE276` quotes a real SHA-512
# crypt hash with its leading '$' lost - what a paste into a terminal that
# swallowed the first character looks like, and refused for exactly that -
# and is asserted whole instead, the way #271 does with its own.
_HASH276 = ("$6$Xq7kPz2r$4Pv3cJ9sQm2WdT1hRfLb8XkY6nAeUoZgC5iMtNrVwJ0yBpSlDqHx"
            "KuGaEc7If3jOvMzTn1RbLd5WhQ9Pk2")
_PASTED276 = _HASH276[1:]
_BAD276 = "user alice\n password secret\n role administrator\n"
_WIDE276 = f"user alice\n password {_PASTED276}\n role administrator\n"
_GOOD276 = "! routing\nrouter static\n 203.0.113.0/24 next-hop blackhole\n"
# The box's own paths, not a temporary directory's: the width of a path is
# the thing being measured, and `/tmp/tmpXXXXXXXX` is not what is printed on
# an appliance.
_CONFIRM276 = "/etc/prisma-routing/confirm"
_RBPATH276 = "/etc/prisma-routing/rollback"


def _confirm276(config, clear=None, attempts=1):
    """A Shell whose confirm file holds an expired window on `config`."""
    sh = _shell271("", frr=_Frr271(daemons=_HEALTHY271))
    pending = {"config": config, "deadline": 0.0, "minutes": 10}
    sh.confirm = type("_C276", (), {
        "read": lambda self: pending,
        "clear": clear or (lambda self: None),
        "arm": lambda self, *a: None,
        "count_attempt": lambda self, p: attempts,
        "give_up": lambda self: _CONFIRM276 + ".failed",
        "path": _CONFIRM276})()
    return sh


def _timeout276(sh):
    sh.confirm_deadline = 1.0
    sh.confirm_on_box = True
    sh.confirm_backup = ""
    sh.check_confirm_timeout()


_drive276(_confirm276(_BAD276), _timeout276,
          "the configuration to roll back to has lines this VTY cannot read")
_drive276(_confirm276(_BAD276), lambda sh: sh.confirm_expired(),
          "the configuration to roll back to has lines this VTY cannot read")

# The window that expires and IS put back: the sentence the unit prints, which
# was 113 columns and is the whole answer an operator gets from the box's log.
_drive276(_confirm276(_GOOD276), lambda sh: sh.confirm_expired(),
          "commit confirmed 10: nobody confirmed it within 10 minute(s)")


def _refuse_clear276(self):
    raise OSError(13, "Permission denied", _CONFIRM276)


_drive276(_confirm276(_GOOD276, clear=_refuse_clear276),
          lambda sh: sh.confirm_expired(),
          "rolled back, but this file could not be removed")

# The rollback that failed often enough for the box to stop trying, which is
# the one outcome that must never be quiet.
_drive276(_confirm276(_BAD276, attempts=prismash.CONFIRM_ATTEMPTS),
          lambda sh: sh.confirm_expired(), "giving up after")

# 4. `--load`, read at BOOT on a box that is refusing its own saved
#    configuration, which is the worst moment to be handed a broken row. The
#    file is written under a directory named the way the box's own is, so the
#    path being measured is an appliance's and not a temporary file's.
_DIR276 = _tf.mkdtemp()
_os.makedirs(_os.path.join(_DIR276, "etc", "prisma-routing"))
_BOOT276 = _os.path.join(_DIR276, "etc", "prisma-routing", "prisma-routing.conf")


def _write276(path, text):
    with open(path, "w") as f:
        f.write(text)
    return path


_drive276(_shell271("", frr=_Frr271(daemons=_HEALTHY271)),
          lambda sh: sh.load(_write276(_BOOT276, _BAD276)),
          "refusing to load this file, these lines are not understood")
_drive276(_shell271("", frr=_Frr271(daemons=_HEALTHY271)),
          lambda sh: sh.load(_write276(
              _BOOT276,
              "! routing\nrouter bgp 65000\n"
              " neighbor 2001:db8:1000:2::1 route-map RM-TRANSIT-V6-IN in\n"
              "exit\n")),
          "refusing to load this file, the check above says why")

_sh = _shell271("", frr=_Frr271(daemons=_HEALTHY271))
_sh.rb = type("_RB276", (), {"push": lambda self, *a: (_ for _ in ()).throw(
    prismash.RollbackUnavailable(f"{_RBPATH276}: Permission denied"))})()
_drive276(_sh, lambda sh: sh.load(_write276(_BOOT276, _GOOD276)),
          "loaded, but no rollback revision could be recorded")

# 5. `rollback <n>` on a store this box cannot read.
_sh = _shell271("", frr=_Frr271(daemons=_HEALTHY271))
_sh.rb = type("_RB276", (), {"load": lambda self, n: (_ for _ in ()).throw(
    prismash.RollbackUnavailable(f"{_RBPATH276}/{n}.conf: Permission denied"))})()
_drive276(_sh, lambda sh: sh.do_rollback(["3"]),
          "revision 3 cannot be read")

# 6. `save`, which the issue names beside `rollback` and `--load`. Two rows:
#    one hand-wrapped at what somebody took for 80 columns and measuring 87,
#    which is the defect #252 is named for turning up in a command nobody had
#    driven; and the write that failed, whose path and errno shared a row at
#    120 columns.
_SAVED276 = _os.path.join(_DIR276, "etc", "prisma-routing", "saved.conf")


def _save276(sh, path, pending=None):
    """`save`, against `path` rather than the box's own CONFIG_PATH."""
    was = prismash.CONFIG_PATH
    prismash.CONFIG_PATH = path
    sh.confirm.read = lambda: pending
    try:
        sh.save_running()
    finally:
        prismash.CONFIG_PATH = was


_drive276(_shell271(_GOOD276, frr=_Frr271(daemons=_HEALTHY271)),
          lambda sh: _save276(sh, _SAVED276,
                              {"deadline": 1770820327.0, "minutes": 10}),
          "what you just saved has NOT been confirmed")
# A path under a FILE, so makedirs fails the way a read-only directory does.
_drive276(_shell271(_GOOD276, frr=_Frr271(daemons=_HEALTHY271)),
          lambda sh: _save276(sh, _os.path.join(_SAVED276, "prisma-routing.conf")),
          "cannot write the configuration file")

# And the two quoted rows that are ALLOWED past the margin, asserted whole and
# on one row each - the half of the rule the sweep cannot say. The sweep alone
# would be satisfied by a short configuration line and `printed whole` alone
# by a short reason; the defect is the two sharing a row, so it takes both.
# Not swept, because these two rows are 114 columns on purpose.
_LOAD276 = _drive276(_shell271("", frr=_Frr271(daemons=_HEALTHY271)),
                     lambda sh: sh.load(_write276(_BOOT276, _WIDE276)),
                     "not understood", keep=False)
for _label, _out276 in (
        ("rollback", _drive276(_confirm276(_WIDE276),
                               lambda sh: sh.confirm_expired(),
                               "cannot read", keep=False)),
        ("--load", _LOAD276)):
    check(f"    line 2: password {_PASTED276}" in _out276.splitlines(),
          f"{_label} quotes a line too wide for the console whole, on one "
          f"row: {_out276!r}")
    check(_PASTED276 + "\n" not in _out276.replace(
              f"password {_PASTED276}\n", ""),
          f"and nowhere else, in pieces: {_out276!r}")
# The path is quoted the same way and for the same reason: `--load` is given
# it by whoever ran it, so its width is nobody's to bound.
check(f"    {_BOOT276}" in _LOAD276.splitlines(),
      f"--load quotes the path it is refusing whole, on a row of its own: "
      f"{_LOAD276!r}")
print(f"  the widest row quoted whole outside commit: "
      f"{len('    line 2: password ' + _PASTED276)} columns")

print(f"  {len(_refusal_out)} fixture(s) driven through commit, commit "
      f"check, rollback, save and --load")

# The same shape as the display's assertion above, on purpose and against the
# same constant: these two are the only places that print a `<- not checked`
# line, and they must not come to disagree about how wide a console is.
#
# ONE sweep, over one list, and #271's fixtures were added to that list rather
# than given a sweep of their own - for the reason the constant is one number:
# two checks against two lists is how one of them comes to be right while the
# other is not looked at (#252).
_wide = [l for out in _refusal_out for l in out.splitlines()
         if len(l) > CONSOLE_COLUMNS]
check(not _wide, f"these wrap on a serial console: {[(len(l), l) for l in _wide]}")
print(f"  widest line these commands printed: "
      f"{max(len(l) for out in _refusal_out for l in out.splitlines())} "
      f"columns")

# --- a statement too long for the console is QUOTED, not cut ---
#
# The case the issue names. 67 columns of statement, which is 87 with the
# four-space indent and the sixteen-character mark, and it is printed as it
# stands (#319: this said 66 and 86, and the line the check below prints
# computes 87 from the same constants): the operator can grep
# `show configuration` for that line and find it, and can type it back.
_LONG_STMT = ("neighbor 2001:db8:aaaa:bbbb:cccc:dddd:eeee:ffff "
              "route-server-client")
_out, _failed = _commit(["router bgp 65000", " " + _LONG_STMT, "exit"],
                        ["router bgp 65000", "exit"])
check(_failed == 1, f"the long statement is refused: {_out!r}")
check(f"    {_LONG_STMT}{UNCHECKED_MARK}" in _out.splitlines(),
      f"a statement wider than the console is printed WHOLE, on one line, "
      f"marker and all: {_out!r}")
check("..." not in _out.replace("... and ", ""),
      f"and nothing of it is elided: {_out!r}")
print(f"  longest statement quoted whole: "
      f"{len(_LONG_STMT) + 4 + len(UNCHECKED_MARK)} columns")

# --- and the guarantee that buys: the box adds nothing but indent and mark ---
#
# A statement that CAN fit MUST fit. The only two things the refusal puts on
# that line are the four-space indent and #141's sixteen characters, so the
# widest statement that fits is CONSOLE_COLUMNS less those - and one column
# more must be one column over, with nothing rounding it off. Written as
# arithmetic over the same constants the code uses, so widening the mark moves
# both sides together and a THIRD thing appearing on the line fails.
# And the constant itself, because every assertion above is written against it.
#
# Found by mutating it: CONSOLE_COLUMNS raised to 400 leaves every width check
# in this file green, because each one asks "no wider than CONSOLE_COLUMNS" and
# the bar moved with the lines. A check somebody can satisfy by widening the
# rule is a check that will be satisfied that way the first time a message does
# not fit, and the 178-column line this issue is about is what that looks like
# after two releases.
#
# 80 is what a serial console gives an appliance, and 79 is 80 less the column
# an auto-wrapping terminal needs left free: a line that fills all 80 wraps on
# its own and the operator reads a broken line. Anything else has to be argued,
# which is the point of pinning it.
check(CONSOLE_COLUMNS == 79,
      f"CONSOLE_COLUMNS is {CONSOLE_COLUMNS}, not 79. Every width assertion in "
      f"this file is written against it, so raising it turns all of them green "
      f"without a line getting any narrower. An appliance console is 80 "
      f"columns and the last one is left free so an auto-wrapping terminal "
      f"does not break the line itself")

_BUDGET = CONSOLE_COLUMNS - len("    ") - len(UNCHECKED_MARK)
_fits = "coalesce-time " + "9" * (_BUDGET - len("coalesce-time "))
_over = _fits + "9"
for _stmt, _want in ((_fits, CONSOLE_COLUMNS), (_over, CONSOLE_COLUMNS + 1)):
    _out, _failed = _commit(["router bgp 65000", " " + _stmt, "exit"],
                            ["router bgp 65000", "exit"])
    _quoted = [l for l in _out.splitlines() if _stmt in l]
    check(_failed == 1 and len(_quoted) == 1,
          f"the {len(_stmt)}-column statement is quoted once: {_out!r}")
    check(len(_quoted[0]) == _want,
          f"a {len(_stmt)}-column statement prints at {len(_quoted[0])} "
          f"columns and must print at {_want}: the refusal adds the indent "
          f"and the mark and nothing else: {_quoted[0]!r}")
print(f"  the statement budget is {_BUDGET} columns: {CONSOLE_COLUMNS} less "
      f"four of indent and {len(UNCHECKED_MARK)} of mark")


# =====================================================================
# `no <statement>` inside a block: accepted in silence, doing nothing (#224)
#
# Three defects, and the first two are of the class this project puts above
# everything else - the product accepts a command and does not do it.
#
#   1. In a pool block, `no block-size 512` printed nothing, removed nothing,
#      and `show this` still showed the setting. The `no` branch of do_pool
#      was an if/elif chain with no `else`: twelve statements had a handler
#      and every other `no` fell out of the bottom of it. An operator walked
#      away believing the setting was gone.
#   2. Inside an interface, `no mac-address aa:bb:cc:dd:ee:ff` fell through to
#      the routing passthrough and was staged for FRR, which has no opinion
#      about the MAC of a VPP interface. At commit that is a statement FRR
#      does not know, arriving in the middle of applying. It became visible on
#      the first day of #141's `<- not checked` marker.
#   3. `?` offered `no` in no block at all.
#
# What is asserted here is BEHAVIOUR, measured by feeding lines to a Shell and
# reading what came out - not the shape of the tables the fix introduced. A
# test that read POOL_NEGATE to decide what to expect would agree with any
# mistake in POOL_NEGATE.
print("\n=== 'no' in a pool block: refused or done, never silent (#224) ===")


def pool_shell():
    """A shell parked inside `cgnat pool residential`, with one prefix."""
    s = shell_with_frr()
    s.mode = "config"
    s.candidate = prismash.Config()
    s.feed("nat")
    s.feed("cgnat")
    s.feed("pool residential")
    s.feed("inside 100.64.0.0/22")
    return s


def said(s, line):
    """What `line` printed, stripped."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.feed(line)
    return out.getvalue().strip()


def pool_text(s):
    return "\n".join(s.candidate.pools["residential"].lines())


def tree_opts(mode, line):
    """What `?` offers at `line` in `mode`. `opts` is taken by a value here."""
    s = prismash.Shell()
    s.mode = mode
    o, _hint, _pfx = s._options(line, bool(line) and not line.endswith(" "))
    return set(o)


# --- the four the issue measured, and what they answer now ---
for _line, _word in (("no block-size 512", "block-size"),
                     ("no policy interleaved", "policy"),
                     ("no mode deterministic", "mode")):
    _sh = pool_shell()
    _before = pool_text(_sh)
    _out = said(_sh, _line)
    check(_out.startswith("error:"),
          f"'{_line}' must refuse rather than say nothing: {_out!r}")
    check(_word in _out,
          f"and must name the statement it refused: {_out!r}")
    # A refusal that only refuses leaves the operator with no next move. The
    # standing rule in this repository is that it has to say what to do
    # instead, and here there IS something to do: set the value wanted.
    check(f"{_word} " in _out.split("instead:")[-1],
          f"and must say what to type instead: {_out!r}")
    check(pool_text(_sh) == _before,
          f"and must change nothing: {pool_text(_sh)}")
    print(f"  {_line} -> {_out}")

# --- and the three that CAN be removed, because the model has the state ---
#
# `ports`, `vrf` and `outside-vrf` are written by Pool.lines() only when they
# are set, and "" is a state that already means something distinct: the
# device's own port range, and every VRF - which the model says out loud is
# not the same answer as VRF 0. Before this there was no line that took `vrf
# 7` back, because `vrf 0` means VRF 0.
_sh = pool_shell()
_sh.feed("ports 1024 65535")
_sh.feed("vrf 7")
_sh.feed("outside-vrf 9")
check("ports 1024 65535" in pool_text(_sh) and " vrf 7" in pool_text(_sh),
      f"set first: {pool_text(_sh)}")
for _line, _gone in (("no ports", "ports"), ("no vrf", " vrf 7"),
                     ("no outside-vrf", "outside-vrf")):
    _out = said(_sh, _line)
    check(_out == "", f"'{_line}' is a removal the model can hold: {_out!r}")
    check(_gone not in pool_text(_sh),
          f"and it must be gone from the pool: {pool_text(_sh)}")
print(f"  no ports / no vrf / no outside-vrf -> {pool_text(_sh)!r}")

# --- naming a member the pool does not have ---
#
# Refused rather than passed over, because the operator named a prefix that is
# not there and the pool still carries the one they meant. NOT the same case
# as clearing an override that is already clear, below.
_sh = pool_shell()
_out = said(_sh, "no inside 10.0.0.0/8")
check(_out.startswith("error:") and "10.0.0.0/8" in _out,
      f"a prefix the pool does not have is refused and named: {_out!r}")
check("100.64.0.0/22" in pool_text(_sh),
      f"and the prefix it does have survives: {pool_text(_sh)}")
check(said(_sh, "no inside 100.64.0.0/22") == "",
      "and the one it does have is removed quietly")
check("100.64.0.0/22" not in pool_text(_sh),
      f"really removed: {pool_text(_sh)}")
print(f"  no inside 10.0.0.0/8 -> {_out}")

# --- the quiet cases: clearing an override that is already clear ---
#
# These say nothing on purpose and the distinction is argued, not assumed:
# after `no timeout udp` the pool does not override the udp timeout, which is
# exactly the state asked for, whether or not it overrode one a moment ago.
print("\n=== the quiet half: a removal that lands says nothing ===")
_sh = pool_shell()
for _line in ("no timeout udp", "no alg ftp", "no max-blocks", "no tcp-mss",
              "no filter", "no max-sessions", "no max-session-rate",
              "no utilisation-alarm", "no bypass source",
              "no bypass destination", "no block-stride"):
    check(said(_sh, _line) == "",
          f"'{_line}' on a pool that has none must be quiet: "
          f"{said(_sh, _line)!r}")
print(f"  {11} removals on a pool with nothing to remove: all silent")

# --- the word after `no` is the one that is wrong ---
_sh = pool_shell()
_out = said(_sh, "no wibble")
check("wibble" in _out,
      f"an unknown statement after 'no' names itself: {_out!r}")
check("in pool mode: no" not in _out,
      f"and never blames the word 'no', which is not the unknown one: {_out!r}")
_bare = said(_sh, "no")
check(_bare.startswith("error:") and "?" in _bare,
      f"'no' alone says what is missing and how to find it: {_bare!r}")
print(f"  no wibble -> {_out}")
print(f"  no        -> {_bare}")

# --- THE RULE, read off the model rather than off the fix ---
#
# A pool statement can be negated exactly when Pool.lines() is able to omit
# it. That is not a restatement of POOL_NEGATE - it is the model asked
# directly, and it is what stops the two from drifting: a statement added to
# the pool grammar later fails here until somebody decides what `no` means for
# it, instead of falling into the hole this issue is about.
print("\n=== which statements can be negated, decided by the model ===")
_default = "\n".join(prismash.Pool("x").lines())
_verdicts = []
for _stmt, _entry in prismash.CGNAT_POOL.items():
    if _stmt in ("exit", "no"):
        continue
    # Does a pool with nothing set still write this line? Then the pool always
    # has one and there is no state in which it has none.
    _always = any(l.strip().split()[0] == _stmt
                  for l in _default.splitlines() if l.startswith(" "))
    _sh = pool_shell()
    # An argument is given where the grammar wants one, so that a refusal is
    # the statement being refused and not a usage message about arity.
    _arg = {"inside": " 100.64.0.0/22", "outside": " 200.0.2.0/30",
            "timeout": " udp", "alg": " ftp", "bypass": " source",
            "forward": " tcp 1100"}.get(_stmt, "")
    _out = said(_sh, f"no {_stmt}{_arg}")
    _refused = _out.startswith("error:") and "cannot be removed" in _out
    _verdicts.append((_stmt, _always, _refused))
    check(_always == _refused,
          f"'no {_stmt}': Pool.lines() always writes it = {_always}, "
          f"but the VTY refusing it = {_refused} ({_out!r})")
    check(_out == "" or _out.startswith("error:"),
          f"'no {_stmt}' must either do it or say why, never both nor "
          f"neither: {_out!r}")
print("  " + ", ".join(f"{s}{'*' if r else ''}" for s, _a, r in _verdicts))
print("  (* refused: the pool always has one)")

# --- nothing offered after `no` may be a statement that refuses it ---
#
# The other direction of the same rule, and the one that makes `?` honest:
# every word the completer offers after `no` has to be a word the block
# actually accepts after `no`.
print("\n=== '?' after 'no' offers only what 'no' really takes ===")
_o = tree_opts("pool", "no ")
check(_o, "a pool must offer something after 'no'")
for _stmt in _o:
    _arg = {"inside": " 100.64.0.0/22", "outside": " 200.0.2.0/30",
            "timeout": " udp", "alg": " ftp", "bypass": " source",
            "forward": " tcp 1100"}.get(_stmt, "")
    _out = said(pool_shell(), f"no {_stmt}{_arg}")
    check("cannot be removed" not in _out and "unknown statement" not in _out,
          f"'?' offers 'no {_stmt}' and the block refuses it: {_out!r}")
for _stmt in ("block-size", "policy", "mode"):
    check(_stmt not in _o,
          f"'{_stmt}' cannot be negated and must not be offered after 'no'")
print(f"  no ? -> {sorted(_o)}")
# The argument goes on being offered after the negated word, because `no
# timeout` alone is not a statement either.
_o2 = tree_opts("pool", "no timeout ")
check({"udp", "icmp", "tcp-established"} <= set(_o2),
      f"'no timeout ?' offers the timeouts: {sorted(_o2)}")
print(f"  no timeout ? -> {sorted(_o2)}")

# --- `no` is offered at all, which is what this issue was opened about ---
print("\n=== 'no' is offered in the blocks that have one ===")
for _mode in ("pool", "user"):
    _o = tree_opts(_mode, "")
    check("no" in _o, f"'{_mode}' must offer 'no': {sorted(_o)}")
_ifsh = shell_with_box()
_ifsh.feed("configure")
_ifsh.feed("interface TenGigabitEthernet0/8/0")
_o, _, _ = _ifsh._options("", False)
check("no" in _o, f"an interface must offer 'no': {sorted(_o)}")
_o, _, _ = _ifsh._options("no ", False)
check("mtu" in _o and "mac-address" in _o and "ip" in _o,
      f"and after it, what can be negated - this box's and FRR's: "
      f"{sorted(_o)}")
check("vlan" not in _o,
      f"but not 'vlan', which is refused: {sorted(_o)}")
check("exit" not in _o, f"and not 'exit', which is not a setting: {sorted(_o)}")
print(f"  interface 'no ?' -> {sorted(_o)}")

# =====================================================================
print("\n=== 'no <dataplane statement>' never reaches FRR (#224) ===")
# The boundary an interface block sits on: `mtu`, `urpf`, `mac-address`,
# `vlan` and `linux-name` are this box's, and `ip ospf ...` and `isis ...` are
# FRR's. `no` on one of FRR's legitimately goes to FRR. `no` on one of this
# box's must never, and until #224 `no mac-address` did.


def iface_shell():
    s = shell_with_frr()
    s.feed("configure")
    s.feed("interface TenGigabitEthernet0/8/0")
    return s


_sh = iface_shell()
_sh.feed("mac-address aa:bb:cc:dd:ee:ff")
check(_sh.candidate.macs.get("TenGigabitEthernet0/8/0") == "aa:bb:cc:dd:ee:ff",
      f"the MAC is this box's to set: {_sh.candidate.macs}")
_out = said(_sh, "no mac-address aa:bb:cc:dd:ee:ff")
check(_out == "", f"and taking it back is quiet: {_out!r}")
check("TenGigabitEthernet0/8/0" not in _sh.candidate.macs,
      f"and really takes it back: {_sh.candidate.macs}")
check(not any("mac-address" in r for r in _sh.candidate.routing),
      f"and NOTHING about it is staged for FRR: {_sh.candidate.routing}")
# The marker from #141 is how this was found, so it is also how it is checked
# as gone. Not removed and not weakened: still on, and now with nothing to say.
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _sh.feed("show this")
check(prismash.UNCHECKED_MARK not in _buf.getvalue(),
      f"and the boundary marker has nothing to mark: {_buf.getvalue()!r}")
print(f"  no mac-address -> routing {_sh.candidate.routing}")

# `vlan` is the one dataplane statement `no` cannot mean anything for: a
# sub-interface is named after its tag, so one without a tag cannot exist. The
# refusal has to name the statement that DOES do what was wanted.
_sh = iface_shell()
_out = said(_sh, "no vlan 7")
check(_out.startswith("error:") and "vlan" in _out,
      f"'no vlan' is refused and names itself: {_out!r}")
check("no interface" in _out,
      f"and says what removes a sub-interface instead: {_out!r}")
check(not any("vlan" in r for r in _sh.candidate.routing),
      f"and is not staged for FRR either: {_sh.candidate.routing}")
print(f"  no vlan 7 -> {_out}")

# FRR's half is untouched: `no` on a statement FRR owns still goes to FRR,
# because `no ip ospf cost` is a statement FRR knows.
_sh = iface_shell()
_sh.feed("no ip ospf cost 10")
check("no ip ospf cost 10" in _sh.candidate.routing,
      f"FRR's own 'no' still reaches FRR: {_sh.candidate.routing}")
print(f"  no ip ospf cost 10 -> staged: "
      f"{'no ip ospf cost 10' in _sh.candidate.routing}")

# --- the sweep, so a dataplane statement added later cannot slip out ---
#
# Every statement this VTY owns inside an interface, negated, and checked for
# the one thing that must never happen: the line being handed to FRR. Each is
# either applied or refused, and neither of those stages anything.
print("\n=== every dataplane statement, negated, stays out of the routing "
      "section ===")
_probe = {"ipv6 nd suppress-ra": "", "ipv4 address": " 10.0.0.1/30",
          "ipv6 address": " 2001:db8::1/64", "description": "",
          "linux-name": "", "mac-address": " aa:bb:cc:dd:ee:ff",
          "shutdown": "", "mtu": "", "urpf": " strict", "vlan": " 7",
          "vrf": ""}
for _stmt in prismash.INTERFACE_DATAPLANE:
    _name = " ".join(_stmt)
    _sh = iface_shell()
    _out = said(_sh, f"no {_name}{_probe.get(_name, '')}")
    _staged = [r for r in _sh.candidate.routing if r.startswith("no ")]
    check(not _staged,
          f"'no {_name}' was handed to FRR: {_staged}")
    check(_out == "" or _out.startswith("error:"),
          f"'no {_name}' must do it or say why: {_out!r}")
print(f"  {len(prismash.INTERFACE_DATAPLANE)} statements, none staged for FRR")

# --- and the word after `no` is the wrong one in a user block too ---
print("\n=== a user block names the word it did not understand ===")
_sh = shell_with_box()
_sh.feed("configure")
_sh.feed("user bob")
_out = said(_sh, "no wibble")
check("wibble" in _out and "mode: no" not in _out,
      f"'no wibble' names wibble, not 'no': {_out!r}")
_o = tree_opts("user", "no ")
check({"role", "password", "ssh-key"} == set(_o),
      f"and 'no ?' offers every statement the block has: {sorted(_o)}")
print(f"  no wibble -> {_out}")
print(f"  user 'no ?' -> {sorted(_o)}")



# =====================================================================
print("\n=== the #141 marker still marks, and now marks less because the "
      "grammar grew ===")
# MEASURED CHANGE, said out loud rather than left to be discovered. Putting
# `no` into the interface grammar means `statement_checked` can walk it, so a
# statement like `no ip ospf cost 10` - which is FRR's, is handed to FRR, and
# is what `no` on an FRR statement is supposed to do - is no longer marked
# `<- not checked`. Before, the VTY marked it while leaving the positive `ip
# ospf cost 10` unmarked, which was the marker disagreeing with itself about
# one statement.
#
# That is the marker following the grammar, which is how #141 built it: it
# asks the tree rather than carrying a list. The grammar got bigger, so fewer
# statements fall outside it. What must NOT happen is the marker going quiet
# for a statement the tree cannot read, and that is what is pinned here.
_IFACE = ["interface Gi0/1"]
check(prismash.statement_checked("no ip ospf cost 10", _IFACE),
      "`no` on an FRR statement the tree reads is read, like the positive")
check(prismash.statement_checked("ip ospf cost 10", _IFACE),
      "which is the answer the positive form always got")
check(not prismash.statement_checked("no ip ospf wibble 3", _IFACE),
      "but a word the tree has no entry for is still unread, `no` or not")
check(not prismash.statement_checked("no wibble 3", _IFACE),
      "and so is a statement that is nothing the block knows")
# Other blocks are untouched: only the interface grammar grew a `no`.
check(not prismash.statement_checked("no bgp ebgp-requires-policy",
                                   ["router bgp 65000"]),
      "a block with no `no` in its grammar marks exactly as it did")
print("  no ip ospf cost 10 -> read;  no ip ospf wibble 3 -> still marked")

# `no` on its own is not a command in any block, and was staged for FRR as
# the bare word - which FRR answers with an unknown command, in the middle of
# applying. Measured inside an interface and at the top of configuration mode,
# because the passthrough is the same one for both.
for _where, _enter in (("an interface", ["interface TenGigabitEthernet0/8/0"]),
                       ("[edit]", [])):
    _sh = shell_with_frr()
    _sh.feed("configure")
    for _l in _enter:
        _sh.feed(_l)
    _out = said(_sh, "no")
    check(_out.startswith("error:"),
          f"'no' alone in {_where} must be refused: {_out!r}")
    check("no" not in [r.strip() for r in _sh.candidate.routing],
          f"and never staged for FRR as a bare word: {_sh.candidate.routing}")
    print(f"  {_where}: no -> {_out}")

# The derived subtree carries FRR's half through, so `?` keeps working past
# the negated word.
_ifsh = shell_with_box()
_ifsh.feed("configure")
_ifsh.feed("interface TenGigabitEthernet0/8/0")
_ospf, _, _ = _ifsh._options("no ip ospf ", False)
check("cost" in _ospf and "area" in _ospf,
      f"'no ip ospf ?' offers what `ip ospf ?` offers: {sorted(_ospf)}")
_urpf, _, _ = _ifsh._options("no urpf ", False)
check({"strict", "loose", "off"} <= set(_urpf),
      f"'no urpf ?' keeps the argument: {sorted(_urpf)}")
print(f"  no ip ospf ? -> {sorted(_ospf)}")
print(f"  no urpf ?    -> {sorted(_urpf)}")


print("\n=== the two dataplane show commands the VTY could not reach (#228) ===")
#
# Both answer "what does the box ACTUALLY have", which is a different question
# from what the file says: whether an ALG is on in the dataplane, and which
# port forwards the dataplane holds. Without them the way to that answer was
# vppctl, which this VTY exists to keep an operator out of.
#
# Asserted at both ends: that `?` offers them, and that what reaches the
# dataplane is the command the plugin actually registers. Offering a word that
# sends nothing is the same defect one layer up.
_probe = prismash.Shell()
_probe.mode = "operational"
_o, _, _ = _probe._options("show cgnat ", False)
check("alg" in _o, f"`show cgnat ?` does not offer alg: {sorted(_o)}")
check("forward" in _o, f"`show cgnat ?` does not offer forward: {sorted(_o)}")

_sent = []
_sh = shell_with_box()
_sh.dp = type("D", (), {
    "TRACE": False,
    "run": lambda _s, c: (_sent.append(c) or (0, "")),
    "reachable": lambda _s: True})()
for _line, _want in (("show cgnat alg", "show bpa alg"),
                     ("show cgnat forward", "show bpa forward"),
                     ("show cgnat forward provider-a",
                      "show bpa forward provider-a")):
    _sent.clear()
    with contextlib.redirect_stdout(io.StringIO()):
        _sh.feed(_line)
    check(_sent == [_want],
          f"{_line!r} sent {_sent!r} to the dataplane, not {[_want]!r}")

print("\n=== 'show version' says which daemons are running (#272) ===")
# Seven of this VTY's own messages sent the operator to `show version` to find
# out which routing daemons are running: the commit branch where nothing took,
# `show bgp`, `show ospf` twice, `show isis` twice, and `configure` refusing a
# box that is still starting. It printed three version strings and said nothing
# about daemons, so the hint was a dead end at the moment a failed commit had
# just named a daemon as the likely cause - and a hint naming a command that
# does not answer costs more than no hint, because the reader concludes the
# daemon is fine.
#
# Driven through the REAL Frr against a stand-in vtysh, deliberately. A stub
# answering daemons() directly would be testing nothing: the exit status, the
# parse and the "FRR said nothing" case all live inside the method the hint now
# depends on, and a fixture that replaces it passes whatever that method does.
import stat as _st272, tempfile as _tf272

_d272 = _tf272.mkdtemp()


def _vtysh272(name, daemons):
    """A stand-in vtysh. `daemons` None is one whose FRR is not answering."""
    path = os.path.join(_d272, name)
    with open(path, "w") as f:
        f.write("#!/bin/sh\ncase \"$*\" in\n")
        if daemons is None:
            # What vtysh does with no daemon to talk to, measured: it prints
            # its own complaint and exits non-zero. It never prints a daemon
            # list, which is the distinction the second assertion is about.
            f.write("  *) echo 'Exiting: failed to connect to any daemons.'; "
                    "exit 1 ;;\n")
        else:
            f.write("  *'show daemons'*) echo '%s' ;;\n" % daemons)
            f.write("  *'show version'*) echo 'FRRouting 10.7.1 (box).' ;;\n")
            f.write("  *) : ;;\n")
        f.write("esac\nexit 0\n")
    os.chmod(path, os.stat(path).st_mode | _st272.S_IEXEC)
    return path


def _version272(vtysh_path):
    """What `show version` prints on a box whose vtysh is this one."""
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT}
    # Derived from prismash.Vpp, not free-standing, and #291 is the second time
    # that mattered in this file: `show version` grew a row for the build the
    # loaded dataplane carries, this stub had no `plugin_build`, and the suite
    # died of AttributeError two hundred checks before its verdict. It is the
    # same argument `_Frr271` above makes about `daemons()` arriving with
    # #272 - a stub that lists the methods it has stops having whatever the
    # product adds next, and both sides of that merge pass on their own.
    #
    # This one answers rc=1 to everything, which is a dataplane that is not
    # answering. That is what this fixture is about: the box it models has
    # FRR to talk to and no VPP.
    sh.dp = type("D", (prismash.Vpp,), {"TRACE": False,
                                      "run": lambda _s, c: (1, "not here"),
                                      "reachable": lambda _s: False})()
    sh.frr = prismash.Frr()
    saved, prismash.VTYSH = prismash.VTYSH, vtysh_path
    try:
        return capture(sh, "show version")
    finally:
        prismash.VTYSH = saved


#: Where a daemon name starts on the page, under "  daemons:   running  ".
_NAMES272 = 22


def _rows272(text):
    """The running and stopped rows, with any wrapped continuation folded in."""
    rows, cur = {}, None
    for line in text.splitlines():
        words = line.replace("daemons:", "").split()
        if words[:1] in (["running"], ["stopped"]):
            cur = words[0]
            rows[cur] = list(words[1:])
        elif cur and words and line.startswith(" " * _NAMES272):
            rows[cur] += words
        else:
            cur = None
    return rows


# A box the installer stopped half way through: FRR's own three are up, OSPFv2
# is up, and the three protocols somebody would configure next are not.
_UP272 = "zebra mgmtd staticd ospfd watchfrr"
_out272 = _version272(_vtysh272("vtysh-partial", _UP272))
_r272 = _rows272(_out272)

check(_r272.get("running") == sorted(_UP272.split()),
      f"'show version' does not report what FRR named: {_out272}")
# The half the operator actually came for. They arrived from a message naming
# ONE daemon - "bgpd may not be running" - so bgpd has to be answered BY NAME.
# A list of what is up answers that only by an absence the reader has to
# notice, and a reader scanning for a word that is not there is exactly how the
# wrong conclusion gets drawn.
check("bgpd" in _r272.get("stopped", []),
      f"bgpd is stopped and 'show version' does not say so: {_out272}")
check("isisd" in _r272.get("stopped", []),
      f"isisd is stopped and 'show version' does not say so: {_out272}")
# ospf6d for the reason #187 existed: it was missing from every list the box
# kept by hand, so OSPFv3 was the one protocol a box could be configured for
# and never run. Here it is derived from the grammar instead.
check("ospf6d" in _r272.get("stopped", []),
      f"ospf6d is stopped and 'show version' does not say so: {_out272}")
# And the other direction, which is why the list is read THROUGH the grammar
# rather than off ROUTER_DAEMON whole: ROUTER_DAEMON validates whatever reaches
# FRR, `router rip` included, and reporting ripd stopped on every box would be
# true and useless.
check("ripd" not in _r272.get("stopped", []),
      f"ripd is not a protocol this VTY offers and is reported anyway: {_out272}")

# An FRR that is not answering is NOT a box with no daemons running. The faults
# differ and so does the next move: one is a control plane that is down, the
# other is a box whose daemons were never enabled in /etc/frr/daemons. Printing
# the first as the second sends the operator to edit a file on a box that would
# not have read it.
_silent272 = _version272(_vtysh272("vtysh-silent", None))
check("not answering" in _silent272,
      f"an FRR that is down must be reported as down: {_silent272}")
check(not _rows272(_silent272),
      f"a daemon list was printed over an FRR that never answered: {_silent272}")

# The running half is a measurement, so its length is not this VTY's to
# predict: a box with ldpd, bfdd, pathd and the rest enabled runs well past the
# 80 columns of the serial console these messages are read on (#252).
_MANY272 = ("zebra bgpd ospfd ospf6d ripd ripngd isisd pimd pim6d ldpd nhrpd "
            "eigrpd babeld sharpd pbrd staticd bfdd fabricd vrrpd pathd mgmtd "
            "watchfrr")
_wide272 = _version272(_vtysh272("vtysh-many", _MANY272))
check(all(len(l) <= prismash.CONSOLE_COLUMNS for l in _wide272.splitlines()),
      "a daemon list ran over the console: "
      + repr([l for l in _wide272.splitlines()
              if len(l) > prismash.CONSOLE_COLUMNS]))
# Wrapped, not truncated: which name the operator came looking for cannot be
# known here, so losing the end loses the answer for whoever asked about the
# last one.
check(_rows272(_wide272).get("running") == sorted(_MANY272.split()),
      f"names were lost from a daemon list that did not fit: {_wide272}")
print("  the hint's destination answers the hint")



print("\n=== 'commit check' reports the re-mapping it promises (#278) ===")
# `commit check`'s own help says it will "report which subscribers a change
# would re-map", and six pages of the operator wiki repeated it. It printed
# "configuration check succeeds" and nothing else.
#
# The numbers were never missing: self.remap is filled by the same
# apply(check_only=True) that `check` runs, and was only printed inside the
# plain `commit` refusal, which `check` returns before reaching.
#
# The shape is the worst one for this fact. An operator runs `check` in a
# maintenance window to learn what the commit will do before doing it, and a
# re-mapping is the most consequential thing a commit on this box can do: one
# added prefix moves every subscriber and ends every session on the pool, with
# nothing unusual reported afterwards because nothing unusual happened (#23).


class _DpRemap:
    """A dataplane that answers a pool validation the way a re-map looks.

    Keyed on the command ENDING in " check" rather than on a prefix: the
    prefix stub above would answer this text to every command that starts with
    the pool line, and a re-map report attached to the wrong command is a
    fixture that proves the opposite of what it looks like.
    """

    TRACE = False

    def run(self, cmd):
        if cmd.endswith(" check"):
            return 0, ("remap: 194 of 256 subscribers move\n"
                       "remap: 100.64.0.1 -> 200.150.0.9 port 1536-2047\n"
                       "remap: 100.64.0.2 -> 200.150.0.10 port 1024-1535\n")
        return 0, ""

    def running(self):
        return 0, ""


_RUN278 = ("cgnat pool p\n inside 100.64.0.0/22\n"
           " outside 200.150.0.8/30\n")
_CAND278 = _RUN278.replace("200.150.0.8/30", "200.150.0.8/29")

_sh = _shell271(_CAND278, _RUN278, dp=_DpRemap())
_out = io.StringIO()
with contextlib.redirect_stdout(_out):
    _sh.do_commit(["check"])
_said = _out.getvalue()

check("RE-MAPS" in _said,
      f"'commit check' does not say the change re-maps anybody, and its own "
      f"help promises it does: {_said!r}")
check("194 of 256 subscribers move" in _said,
      f"'commit check' has the count the dataplane reported and does not "
      f"print it: {_said!r}")
check("100.64.0.1 -> 200.150.0.9 port 1536-2047" in _said,
      f"'commit check' does not name where a subscriber goes: {_said!r}")
check("commit remap" in _said,
      f"'commit check' warns about the re-mapping and does not say what to "
      f"type: an operator told only that a commit will be refused has to go "
      f"and find the word: {_said!r}")
# It must not read as a refusal: `check` applied nothing, so there is nothing
# to have refused, and an operator who reads this as a failure re-runs it.
check("error:" not in _said,
      f"'commit check' reports the re-mapping as an error, and it applied "
      f"nothing: {_said!r}")

# The other direction, and it is what keeps the report from being printed on
# every check: a change that moves nobody must say nothing about re-mapping.
# A warning that is always on is one nobody reads (#205, #208).
_sh = _shell271(_RUN278 + " block-size 512\n", _RUN278, dp=_DpRemap())
_quiet = io.StringIO()
with contextlib.redirect_stdout(_quiet):
    _sh.do_commit(["check"])
check("RE-MAPS" not in _quiet.getvalue(),
      f"'commit check' warns about a re-mapping the dataplane did not report: "
      f"{_quiet.getvalue()!r}")

for _row in _said.splitlines():
    check(len(_row) <= CONSOLE_COLUMNS or _row.strip().startswith("100."),
          f"this wraps on a serial console ({len(_row)}): {_row!r}")
print("  the count, where two of them go, and what to type")


print("\n=== a box with one NIC is told what to do with it (#289, #303) ===")
# Reported from a real box: one virtio NIC, and it carries the default route
# because it is the only one there is. The first-boot screen said "reboot once
# the tuning has been applied", which is advice that cannot end anywhere - the
# next screen would say no interface was named, and naming that one got it
# refused, for the reason #123 exists.
#
# This screen then said the box needed a SECOND NIC before it could forward,
# which was true of the product and was still a dead end: a VM with one
# virtual NIC is how most people first meet this, and "buy more hardware" is
# not something the operator reading a first-boot screen can act on. So what
# it says now is what to type, and what the typing costs (#303).
_FIRST = "plugin dpdk_plugin.so { disable }\n"

_r = _nic_box(["ens18"], "ens18", _FIRST).why_no_dataplane_interface()
_t = " ".join(_r)
check("This box has one NIC" in _t,
      f"a box whose only NIC is its management path is told to reboot and "
      f"nothing else, and the reboot cannot end anywhere: {_r}")
check("'ens18'" in _t,
      f"it does not name the NIC it means, so the operator cannot tell which "
      f"one it is on a box with several: {_r}")
# WHAT IT SAYS NOW, AND WHAT IT MAY NOT (#385). It told the operator what to
# type, and promised a confirmed commit whose window #378 had already removed
# - a screen saying the old thing for a release. Since #378 the box claims the
# last card by itself, so what this screen owes the operator is what happens
# and where the address goes, and no instruction to type a statement the box
# does not need.
check("never takes the last card" in _t and "linux_cp" in _t,
      f"it does not say the box takes the card by itself, or where the "
      f"address goes: {_r}")
check("dataplane interface ens18" not in _t,
      f"it still tells the operator to name a card the box claims by itself: "
      f"{_r}")
check("confirmed one" not in _t and "puts the card back" not in _t,
      f"it still promises the confirmed commit #378 removed: {_r}")

# The other direction, and it is what stops this from being a line on every
# boot. A box with a free NIC is told nothing extra.
_r = _nic_box(["ens18", "ens19"], "ens18", _FIRST).why_no_dataplane_interface()
check(not any("This box has one NIC" in l for l in _r),
      f"a box with a spare NIC was told it has one: {_r}")
# WHAT THIS MEANS, rather than how many lines it takes to say it. The check
# was `len(_r) == 2`, and a length is the wrong holder: it went red when #327
# added a line that every branch gets - a pointer to `show hardware` - which
# is not the handover advice this is guarding against. The thing that must not
# appear on a box with another card is the one-NIC note, and the line above
# already names it.
check(not any("never takes the last card" in l for l in _r),
      f"a box with a spare NIC was given the one-card note, which is for a "
      f"box that has no other card: {_r}")

# A box Linux shows no PCI NIC at all is its own answer, and must not read as
# the one-NIC case.
_r = _nic_box([], "", _FIRST).why_no_dataplane_interface()
check(any("no PCI NIC Linux can see" in l for l in _r),
      f"a box with no PCI NIC at all was not told: {_r}")
check(not any("This box has one NIC" in l for l in _r),
      f"a box with no NIC at all was told it has one: {_r}")
print("  said at the first screen that mentions it, and only when it is true")

print("\n=== the completion marks the default-route card truthfully (#309, #385) ===")
# It said "carries the default route: refused" unconditionally, and has been
# wrong since #303 on the box where it matters most: with no other card that
# name is ACCEPTED and the address moves with it. Then it said so on the box
# with another card, where the VTY refused the name - and #385 removed that
# refusal too, so "refused" is a word this mark may not say on any box.


def _nic_hints(names, default):
    _sh = prismash.Shell()
    _sh.host_nics = lambda: [{"name": _n} for _n in names]
    _sh.default_route_nic = lambda: default
    return _sh.dyn_host_nics()


_h = _nic_hints(["ens18"], "ens18")
check("refused" not in _h["ens18"],
      f"the only card is not marked refused, because it is not: {_h}")
# The needle is what the row PROMISES, not the words it promises it in: the
# hint was reworded to fit the 79-column console (#346) and this went red on
# correct work. What has to be there is that the address goes with the card.
check("default route" in _h["ens18"] and "address" in _h["ens18"],
      f"and the mark says what does happen to it: {_h}")

_h = _nic_hints(["ens18", "ens19"], "ens18")
check("refused" not in _h["ens18"],
      f"with a second card the default-route one is still marked refused, "
      f"and since #385 the VTY accepts it: {_h}")
check("default route" in _h["ens18"] and "address" in _h["ens18"],
      f"and the mark on the two-card box no longer says what taking the "
      f"card means: {_h}")
check(_h["ens19"] == "card",
      f"and the other card is just a card: {_h}")

print("\n=== what a commit on the MEDIUM says about rebooting (#310) ===")
# #305 one message over. `dataplane interface <name>` followed by `commit`
# ends with "reboot the box in a window you have chosen, and it comes up
# forwarding on that card" - right on a box with a disk, and wrong on the one
# screen that says so itself, two lines under a banner reading "nothing here
# survives a reboot". say_dataplane_if_pending() asked no live-medium question
# at all.


def _pending_said(on_medium):
    _sh = prismash.Shell()
    _keep = (prismash.LIVE_SQUASHFS, prismash.INSTALLER)
    if on_medium:
        _live = os.path.join(_d, "fs310.squashfs")
        _inst = os.path.join(_d, "install310")
        open(_live, "w").write("")
        open(_inst, "w").write("#!/bin/sh\n")
        os.chmod(_inst, 0o755)
        prismash.LIVE_SQUASHFS, prismash.INSTALLER = _live, _inst
    else:
        prismash.LIVE_SQUASHFS = os.path.join(_d, "no-such-squashfs")
    _buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(_buf):
            _sh.say_dataplane_if_pending("ens19")
    finally:
        prismash.LIVE_SQUASHFS, prismash.INSTALLER = _keep
    return _buf.getvalue()


_med = _pending_said(True)
check("reboot the box" not in _med,
      f"the medium is not told to reboot into a box that forwards: {_med!r}")
check("install" in _med,
      f"and it is told what does work there: {_med!r}")
check("survive" in _med,
      f"and why a reboot is not the answer on a medium: {_med!r}")

_disk = _pending_said(False)
check("reboot the box" in _disk,
      f"an installed box still gets the advice that is right for it: "
      f"{_disk!r}")
check("install" not in _disk,
      f"and is not told to install over itself: {_disk!r}")

print("\n=== every 'no interface' branch says where the cards are (#327) ===")
# THE DEFECT, reported by the maintainer with the sequence on screen. On the
# installation medium `show interface` explained what was absent and told them
# to install, and they typed `start shell` and `ip l` to find out whether the
# box could see their NIC at all - which is the question that decides whether
# to install, and the shell is the tool this VTY exists to keep them out of.
#
# `show hardware` was written for exactly that moment: its own docstring says
# "that is exactly the moment an operator needs to see what NICs the box has
# in order to choose one. The only way to that list was to leave the VTY"
# (#283). Every mention of it in this file was a COMMENT. No message named it.
#
# EVERY BRANCH, not the one that was reported. #305 was fixed in one message
# and survived in a second as #310, and that is the same function.
def _why_branch(conf_text, medium):
    _d = tempfile.mkdtemp()
    _conf = os.path.join(_d, "startup.conf")
    open(_conf, "w").write(conf_text)
    _keep = (prismash.LIVE_SQUASHFS, prismash.INSTALLER)
    if medium:
        _live = os.path.join(_d, "fs")
        _inst = os.path.join(_d, "i")
        open(_live, "w").write("")
        open(_inst, "w").write("#!/bin/sh\n")
        os.chmod(_inst, 0o755)
        prismash.LIVE_SQUASHFS, prismash.INSTALLER = _live, _inst
    else:
        prismash.LIVE_SQUASHFS = os.path.join(_d, "none")
    _sh = prismash.Shell()
    _sh.STARTUP_CONF = _conf
    _sh.TUNE_ENV = os.path.join(_d, "tune.env")
    _sh.host_nics = lambda: [{"name": "ens18"}]
    _sh.default_route_nic = lambda: None
    try:
        return _sh.why_no_dataplane_interface()
    finally:
        prismash.LIVE_SQUASHFS, prismash.INSTALLER = _keep


_BRANCHES = [("the installation medium", "plugin dpdk_plugin.so { disable }\n",
              True),
             ("the first-boot profile", "plugin dpdk_plugin.so { disable }\n",
              False),
             ("no interface named", "dpdk {\n  no-pci\n}\n", False)]
_silent = []
for _name, _conf, _med in _BRANCHES:
    _said = _why_branch(_conf, _med)
    check(bool(_said), f"{_name}: the diagnosis said nothing at all, so this "
                       f"check is reading an empty list rather than a branch")
    if _said and not any("show hardware" in _l for _l in _said):
        _silent.append((_name, _said))
for _name, _said in _silent:
    check(False, f"{_name} tells the operator what is absent and never says "
                 f"where to see the cards the box has: {_said}")
if not _silent:
    print(f"  [ ok ] all {len(_BRANCHES)} of them")

print("\n=== the block docs/routing.md shows IS what the box prints (#318) ===")
# THE DEFECT. Under "A route-map referenced and not defined", the page shows
# the refusal in four wrapped rows and then said: "Reflowed here. `err()` wraps
# nothing, so the box prints that as one line of 221 columns". Every part of
# that was false. #276 replaced `err(f"{stmt}: {why}")` with say_finding(),
# which produces exactly the block above the sentence - four rows, widest 77 -
# and the same commit rewrote the width section the sentence points at and left
# the sentence describing what it had just stopped doing.
#
# THIS IS A FIXTURE COMPARISON, NOT A NEEDLE, which is the difference that
# makes it sound where test/test_doc_copies.py's header rejects matching a
# document against the code that prints a message. Nothing is searched for in
# the source: the function is CALLED and its output is compared to the block
# the document shows, line for line. A reworded message fails this, which is
# the point - the document is showing the message.
_rm_out = io.StringIO()
with contextlib.redirect_stderr(_rm_out), contextlib.redirect_stdout(_rm_out):
    prismash.Shell().say_finding(
        "error", "neighbor 10.0.0.2 route-map RM-IN in",
        "no route-map called 'RM-IN' is defined, and FRR denies everything "
        "this statement governs until one is; define it, or write 'route-map "
        "RM-IN permit 10' to let everything through")
_rm_rows = [l for l in _rm_out.getvalue().splitlines() if l.strip()]
_doc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "docs", "routing.md"), encoding="utf-8").read()
_sec = _doc.split("## A route-map referenced and not defined", 1)
if len(_sec) != 2:
    check(False, "docs/routing.md has no 'A route-map referenced and not "
                 "defined' section, so this check is comparing nothing")
else:
    _blk = _sec[1].split("```")
    _shown = [l for l in _blk[1].splitlines() if l.strip()] if len(_blk) > 2 \
        else []
    check(_shown == _rm_rows,
          f"the block the page shows is what say_finding() prints:\n"
          f"    page: {_shown}\n    box:  {_rm_rows}")
    check(max((len(l) for l in _rm_rows), default=999) <= 79,
          f"and it fits the console: widest {max(len(l) for l in _rm_rows)}")

print("\n=== the trail's tamper check has a VTY spelling (#313) ===")
# The dataplane has had `show bpa pool history [verify]` since v0.23.0 and the
# VTY proxied the command WITHOUT its argument, so no VTY spelling reached the
# check. Two operator pages documented it by the dataplane's own name - one of
# them at the last step before the disk is erased, with a "pare aqui" attached
# to its result - so an operator following the page got a parser error at the
# moment they were told to stop if the answer was wrong.
# The tree directly: `opts` is shadowed by a local dict further up this file.
_hist = prismash.TREE["operational"]["show"][1]["cgnat"][1]["history"][1]
check(isinstance(_hist, dict) and "verify" in _hist,
      f"'show cgnat history' offers verify: {_hist}")
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "src", "prismash", "prismash.py"),
            encoding="utf-8").read()
check('"show cgnat history verify": "show bpa pool history verify"' in _src,
      "and the proxy carries it through to the dataplane, which is where the "
      "check lives")

print("\n=== every command a message names is one this VTY has ===")
# THE DEFECT (#308). `show interface` on a box whose NIC is bound correctly and
# still absent from the dataplane told the operator that "'show log' and the
# VPP journal say why". This VTY has no `show log`: the tree offers `show
# logging`, which is FRR's system log and says nothing about a NIC the
# dataplane refused. It printed on the one screen an operator reaches when the
# box forwards nothing AND the binding is already right, so the next thing they
# type after reading it is refused.
#
# SOUND BECAUSE THE HOLDER IS THE TREE. Nothing here reads a message against
# another message: each quoted command is walked against the same structure
# readline completes from, in both modes. Measured before it was written: 18
# quoted commands across prismash.py, 0 rejected with the fix in place and
# exactly 1 with it taken back out. No exception list, because there was
# nothing to except.
#
# AN ARGUMENT STOPS THE WALK. `commit confirmed 30` reaches `confirmed`, whose
# child is a hint string rather than a dict, and the 30 after it is a value. A
# check that demanded it be a tree key would reject every command that takes
# one.
_MODES = [prismash.TREE["operational"], prismash.TREE["config"]]


def _accepted(words):
    for _root in _MODES:
        _node, _ok = _root, True
        for _w in words:
            if not isinstance(_node, dict):
                break
            if _w not in _node:
                _ok = False
                break
            _node = _node[_w][1]
        if _ok:
            return True
    return False


_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "src", "prismash", "prismash.py"),
            encoding="utf-8").read()
_quoted = {}
for _n in ast.walk(ast.parse(_src)):
    if isinstance(_n, ast.Constant) and isinstance(_n.value, str):
        for _m in re.finditer(
                r"'((?:show|configure|commit|rollback|save)[a-z0-9 <>_-]*)'",
                _n.value):
            _quoted.setdefault(_m.group(1).strip(), _n.lineno)
_missing = {_c: _l for _c, _l in _quoted.items()
            if not _accepted([_w for _w in _c.split()
                              if not _w.startswith("<")])}
for _c, _l in sorted(_missing.items(), key=lambda x: x[1]):
    check(False, f"a message at line {_l} tells the operator to type {_c!r} "
                 f"and this VTY has no such command: the next thing they type "
                 f"after reading it is refused")
check(len(_quoted) >= 12,
      f"only {len(_quoted)} quoted command(s) were found, so this check is "
      f"asserting almost nothing")
if not _missing and len(_quoted) >= 12:
    print(f"  [ ok ] all {len(_quoted)} of them")

# --- "not answering" is not "the neighbour keeps nothing" ---
#
# `show bgp neighbor <n> received-routes` and `clear bgp <n> soft in` both need
# the neighbour to be holding what it sent, and both refuse when it is not.
# What decided that was `Frr.running()`, which reports a control plane that is
# DOWN as a configuration with nothing in it - the exact collapse
# `Frr.daemons()` forbids forty lines above it in the same class: "None is not
# the empty set and the two MUST NOT be collapsed."
#
# So a box whose FRR was silent got a refusal about its neighbour's
# configuration, read off nothing, with advice that cannot be followed: add a
# statement the box may already carry, through a daemon that is not there to
# take it; then `show bgp neighbor <n> routes`, which answers from the same
# silent FRR; then `clear bgp <n>`, an outage offered on a premise nothing
# read, on a box where it would reset nothing.
#
# Neither path had a single assertion before this, which is why nobody noticed
# - the same gap #328 found in which_dpdk_driver().
import system as _sys_soft                                      # noqa: E402
import vppapi as _vppapi_soft                                   # noqa: E402

print("\n=== FRR not answering is not 'the neighbour keeps nothing' ===")

_SOFT_CONF = ("router bgp 65000\n neighbor 10.0.0.2 remote-as 65001\n"
              " address-family ipv4 unicast\n"
              "  neighbor 10.0.0.2 soft-reconfiguration inbound\n"
              " exit-address-family\n")


def _soft_box(frr_rc, conf):
    """
    A box whose FRR answers `conf`, or does not answer at all.

    Driven through `sh.feed()` so what is measured is what the command prints.
    `frr_rc` non-zero is vtysh failing, which is what Frr.running() turns into
    an empty configuration and what this is about.
    """
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT}
    sh.dp = type("D", (), {"TRACE": False, "run": lambda s, c: (0, ""),
                           "reachable": lambda s: True})()

    class _F(prismash.Frr):
        def show(self, cmd):
            if cmd == "show running-config":
                return (frr_rc, conf)
            return (frr_rc, "" if frr_rc else "  1.1.1.0/24  10.0.0.2")

        def available(self):
            return frr_rc == 0

        def daemons(self):
            return {"bgpd"} if frr_rc == 0 else None

    sh.frr = _F()
    sh.api = type("A", (), {
        "interface_index": lambda s, n: None,
        "call": lambda s, m, a: _vppapi_soft.Reply(_vppapi_soft.OK),
        "dump": lambda s, m, a, timeout=15:
            _vppapi_soft.Reply(_vppapi_soft.OK, fields={"rows": []})})()
    _t = tempfile.mkdtemp()
    sh.TUNE_ENV = os.path.join(_t, "tune.env")
    open(sh.TUNE_ENV, "w").write("")
    sh.sys = _sys_soft.System(
        runner=lambda a, stdin=None, timeout=30: (127, ""), root=_t)
    return sh


def _soft_says(frr_rc, conf, line):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _soft_box(frr_rc, conf).feed(line)
    return buf.getvalue()


_RECV = "show bgp neighbor 10.0.0.2 received-routes"
_SOFTIN = "clear bgp 10.0.0.2 soft in"

# The box the refusal was written for, unchanged: FRR answers, and the
# statement is not there.
_off = _soft_says(0, "router bgp 65000\n", _RECV)
check("does not keep what it received" in _off
      and "soft-reconfiguration inbound" in _off,
      f"a neighbour that really is not keeping what it received was not told "
      f"so, which is the refusal both of these exist for: {_off!r}")
_off_clear = _soft_says(0, "router bgp 65000\n", _SOFTIN)
check("nothing to re-apply" in _off_clear,
      f"and a soft clear against it was not refused: {_off_clear!r}")

# The box that IS keeping it: the command goes through to FRR.
_on = _soft_says(0, _SOFT_CONF, _RECV)
check("does not keep" not in _on and "1.1.1.0/24" in _on,
      f"a neighbour that keeps what it received was refused anyway, so the "
      f"reading is not finding the statement at all: {_on!r}")

# And the box nobody can ask.
_deaf_recv = _soft_says(1, "", _RECV)
check("does not keep what it received" not in _deaf_recv,
      f"a box whose FRR is silent was told what its neighbour keeps, read off "
      f"a configuration it never got: {_deaf_recv!r}")
check("FRR is not answering" in _deaf_recv,
      f"and is not told that is why, so an empty answer reads as the "
      f"neighbour sending nothing: {_deaf_recv!r}")
check("soft-reconfiguration inbound" not in _deaf_recv,
      f"it is told to add a statement it may already have, through a daemon "
      f"that is not there to take it: {_deaf_recv!r}")
_deaf_clear = _soft_says(1, "", _SOFTIN)
check("FRR is not answering" in _deaf_clear
      and "does not keep what it received" not in _deaf_clear,
      f"the same on the clear path, which is the one that matters more: "
      f"{_deaf_clear!r}")
check("use 'clear bgp 10.0.0.2' to reset the session" not in _deaf_clear,
      f"a box whose FRR is silent is offered a session reset as the remedy - "
      f"an outage, on a premise nothing read, against a daemon that would not "
      f"take it: {_deaf_clear!r}")

# AND BOTH REFUSALS FIT THE CONSOLE THEY ARE READ ON (#349).
#
# err() wraps nothing and the rows under these two were bare print("    ...")
# calls, so they measured 106, 101, 102, 92, 105 and 81 columns against a
# console of 79. The neighbour address is interpolated into every one of them,
# which is why the peers below are driven rather than one: an IPv6 peer is the
# widest real case, and it is the case #276 widened the check in
# test_prismash_bgp.py to cover on a different message. These two were not among
# the rows it split, and nothing read their width.
#
# The third answer on each path - FRR not answering - goes through the same
# measurement, because it was written in the same cycle and has the same
# shape.
for _peer in ("10.0.0.2", "2001:db8:aaaa:bbbb::2", "fd00:1234:5678:9abc::42"):
    for _rc, _conf, _line in (
            (0, "router bgp 65000\n",
             f"show bgp neighbor {_peer} received-routes"),
            (0, "router bgp 65000\n", f"clear bgp {_peer} soft in"),
            (1, "", f"show bgp neighbor {_peer} received-routes"),
            (1, "", f"clear bgp {_peer} soft in")):
        _said = _soft_says(_rc, _conf, _line)
        # A run that printed nothing would pass the width test for free.
        check(_said.strip(),
              f"{_line!r} printed nothing on the box that must refuse it, so "
              f"the width below is of an empty screen")
        _over = [(len(l), l) for l in _said.splitlines()
                 if len(l) > CONSOLE_COLUMNS]
        check(not _over,
              f"{_line!r} wraps on a serial console: {_over}")
print("  three answers: keeping, not keeping, and nobody to ask")

print("\n=== a value the BOX already has is a fact, not a request (#357) ===")
# Config._pool_line() is the reader for three things: a statement typed at the
# prompt, a saved file, and the configuration read back out of the running
# dataplane by `show bpa config`. The first is a request; the last two are
# facts.
#
# #352 put the block-size bound inside it, and that broke the third. The
# dataplane accepts and builds a pool with `block-size 4` - lab/detreserve.sh
# uses one to keep a block cheap - and on such a box the VTY could no longer
# model its own running configuration: `running_config()` said "committing
# would REMOVE it" and `commit` then refused EVERY change. An operator with a
# small block was locked out of configuration entirely.
#
# Driven from both ends: the box's own words are modelled, and the operator's
# are still refused with the range named.
_DEVICE = ("! cgnat\n"
           "cgnat pool det\n"
           " inside 100.64.0.0/22\n"
           " outside 200.150.0.16/32\n"
           " block-size 4\n"
           " utilisation-alarm 0\n"
           " exit\n")
_read = _config.Config.parse(_DEVICE, dataplane=True)
check(not _read.unknown,
      f"the VTY cannot model a pool the dataplane built, so every commit on "
      f"that box is refused: {_read.unknown}")
check("det" in _read.pools and _read.pools["det"].block_size == "4",
      f"the block size the box reported was not kept, so a commit would "
      f"write a different one over it: "
      f"{getattr(_read.pools.get('det'), 'block_size', None)!r}")
for _stmt, _range in (("block-size 4", "64-32768"),
                      ("block-size 65536", "64-32768"),
                      ("utilisation-alarm 0", "1-100")):
    _s, _refused, _said = _typed(["nat", "cgnat", "pool P"], _stmt)
    check(_refused,
          f"{_stmt!r} typed at the prompt was taken, and the prompt is where "
          f"a number can still be argued with: {_said.strip()!r}")
    check(_range in _said,
          f"{_stmt!r} was refused without naming {_range}: {_said.strip()!r}")
print("  the box's own configuration is modelled, and the operator's is "
      "bounded")

print("\n=== the refusals that were a paragraph on one row (#355) ===")
# `err` wraps nothing, which is right for a verdict and wrong for a paragraph.
# Seven refusals were composed as one long sentence and reached the console at
# 104 to 185 columns against a budget of 79, folded by the terminal at
# whatever width the window happened to be - and in every one of them the move
# the operator has to make is the LAST clause, which is where that fold lands.
#
# Driven rather than read, because what is measured is the rows the box
# printed and not the strings the source holds. Each case also refuses a run
# that printed nothing and a run that refused nothing: an empty screen fits
# any console, and a case measuring some other output is not about this.
_WIDE_CASES = [
    ("no vlan on a sub-interface",
     ["interface Gi0/12/0.700", "vlan 700", "no vlan 700"]),
    ("no policy in a pool", ["nat", "cgnat", "pool P", "no policy interleaved"]),
    ("no block-size in a pool", ["nat", "cgnat", "pool P", "no block-size 512"]),
    ("no mode in a pool", ["nat", "cgnat", "pool P", "no mode deterministic"]),
    ("no forward with two subscribers",
     ["nat", "cgnat", "pool P", "inside 10.0.0.0/24", "outside 200.150.0.0/28",
      "forward tcp 6200 to 10.0.0.4 6200", "forward tcp 6200 to 10.0.0.5 6200",
      "no forward tcp 6200"]),
    ("a role for root", ["user root", "role administrator"]),
    ("no role for root", ["user root", "no role"]),
]
for _label, _lines in _WIDE_CASES:
    _s = prismash.Shell()
    _s.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT}
    _s.mode = "config"
    _s.candidate = _config.Config()
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        for _l in _lines:
            _s.feed(_l)
    _said = _buf.getvalue()
    check(_said.strip(),
          f"{_label}: the box printed nothing, so the width below is of an "
          f"empty screen")
    check(_s.failed,
          f"{_label}: nothing was refused, so what is measured is not the "
          f"refusal this case is about: {_said!r}")
    _over = [(len(l), l) for l in _said.splitlines()
             if len(l) > CONSOLE_COLUMNS]
    check(not _over, f"{_label}: wraps on a serial console: {_over}")
print(f"  {len(_WIDE_CASES)} refusals, every row inside {CONSOLE_COLUMNS} "
      f"columns")

print("\n=== `?` says block-size is a power of two, and now so does the box "
      "(#360) ===")
# The hint on this statement promises TWO things about the same number -
# "ports per block, power of two", `<64-32768>` - and #352 enforced only the
# range. `block-size 100` was taken at the prompt, staged into the candidate,
# written to the file, and refused on the far side of `commit` by
# bpa_pool.c:143 with "pool 'p': block size 100 must be a power of two", after
# the operator had built a candidate that was probably going to change
# something else too.
#
# DRIVEN THROUGH THE VTY and read back out of the candidate, not called as a
# predicate. What is being checked is the path - the statement reaching
# do_pool, the guard firing there, and the value not being staged - and a case
# that calls Pool.check_pow2() directly is green whatever do_pool does with
# it. That is how #357's first two test attempts could not fail.
for _v in (64, 512, 32768):
    _s, _refused, _said = _typed(["nat", "cgnat", "pool P"], f"block-size {_v}")
    check(not _refused,
          f"block-size {_v} is a power of two inside the range `?` quotes "
          f"and the box refused it: {_said.strip()!r}")
    check(f"block-size {_v}" in _s.candidate.text(),
          f"block-size {_v} was accepted and is not in the candidate, so the "
          f"pool the operator asked for is not the pool that would commit")
# (value, the two the refusal owes them). Inside the range in every case, so
# what refuses them can only be the shape.
for _v, _lo, _hi in ((100, 64, 128), (65, 64, 128), (1000, 512, 1024),
                     (32767, 16384, 32768)):
    _s, _refused, _said = _typed(["nat", "cgnat", "pool P"], f"block-size {_v}")
    check(_refused,
          f"block-size {_v} is inside 64-32768 and is not a power of two, "
          f"and the box took it - the dataplane refuses it at commit time")
    # Line-exact, and not "block-size" absent: a pool that has never been
    # given one still renders the default, so the absent thing to look for is
    # the number the operator typed rather than the statement.
    _staged = [l.strip() for l in _s.candidate.text().splitlines()
               if l.strip().startswith("block-size ")]
    check(f"block-size {_v}" not in _staged,
          f"block-size {_v} was refused and staged anyway, so the file would "
          f"carry a block size the dataplane will not build: {_staged}")
    check("power of two" in _said,
          f"block-size {_v} was refused without naming the constraint, which "
          f"leaves the operator guessing which half of the hint it broke: "
          f"{_said.strip()!r}")
    check(f"{_lo} and {_hi}" in _said,
          f"block-size {_v} was refused without naming {_lo} and {_hi}, which "
          f"leaves an operator doing arithmetic at a prompt: "
          f"{_said.strip()!r}")
    _over = [(len(l), l) for l in _said.splitlines() if len(l) > CONSOLE_COLUMNS]
    check(not _over, f"block-size {_v}: the refusal wraps on a serial "
                     f"console: {_over}")

# THE #357 TRAP, ASSERTED HERE rather than taken on trust from the section
# above, because this is the change that would reopen it. The guard added for
# #360 is at the typed-statement site; had it gone into Config._pool_line() -
# which is where the statement is modelled, and where the next person adding a
# constraint will reach first - then a box whose dataplane reports a pool with
# a block size that is not a power of two would become a box the VTY cannot
# model, and `commit` would refuse EVERY change on it. `block-size 4` is not a
# power of two problem; it is below the floor, and lab/detreserve.sh builds
# one. The reader must keep taking both.
for _bs in ("4", "100"):
    _text = ("! cgnat\n"
             "cgnat pool det\n"
             " inside 100.64.0.0/22\n"
             " outside 200.150.0.16/32\n"
             f" block-size {_bs}\n"
             " exit\n")
    _read = _config.Config.parse(_text, dataplane=True)
    check(not _read.unknown,
          f"a configuration carrying `block-size {_bs}` no longer parses, so "
          f"a box whose dataplane reports one cannot be committed at all "
          f"(#357): {_read.unknown}")
    check(getattr(_read.pools.get("det"), "block_size", None) == _bs,
          f"`block-size {_bs}` was read as something else, so a commit would "
          f"write a different block size over the one the box has: "
          f"{getattr(_read.pools.get('det'), 'block_size', None)!r}")
print("  64, 512 and 32768 staged; 65, 100, 1000 and 32767 refused with both "
      "neighbours named; the reader still models what the box has")

print("\n=== `ports` was enforced by nothing at all, and now by three "
      "things (#361) ===")
# Every other numeric statement in this block got a bound with #350 to #354.
# `ports` got none, and it is the one carrying TWO numbers and a cast: both
# ends are u16 in bpa.api and in bpa_pool_t, so `ports 70000 80000` was taken
# at the prompt, staged, saved, and arrived at the dataplane as 4464-14464.
# The pool that commits is not the pool that was typed, and nothing on the
# far side can name 70000 because by then 70000 is not the number any more.
#
# DRIVEN THROUGH THE VTY and read back out of the candidate, not called as a
# predicate. What is under test is the path - the statement reaching do_pool,
# the guard firing there, and the numbers not being staged - and a case that
# calls Pool.check_range() or Pool.check_order() directly is green whatever
# do_pool does with them. That is how #357's first two test attempts could
# not fail.
#
# THREE REASONS AND THREE SENTENCES, because they are three different
# mistakes: a value outside the field, two values in the wrong order, and
# (asked at commit, not here) a range too narrow for one block. One message
# for all of them would tell an operator the wrong thing about two.
_PORTS_REFUSED = [
    # (statement, the words the refusal owes the operator, why it is wrong)
    ("ports 9000 100", "the max is below the min", "max below min"),
    ("ports 70000 80000", "1-65535", "both ends above the u16 field"),
    ("ports 0 65535", "1-65535", "port 0 is not one to hand out"),
    ("ports 4294967296 4294967297", "1-65535", "above a u32"),
    ("ports -1 100", "1-65535", "negative"),
]
for _stmt, _owed, _why in _PORTS_REFUSED:
    _s, _refused, _said = _typed(["nat", "cgnat", "pool P"], _stmt)
    check(_refused,
          f"{_stmt!r} ({_why}) was taken at the prompt, and the prompt is "
          f"where a number can still be argued with: {_said.strip()!r}")
    # The NUMBER absent and not the statement: a pool that has never been
    # given a port range renders none at all, so the thing to look for is the
    # line the operator typed.
    _staged = [l.strip() for l in _s.candidate.text().splitlines()
               if l.strip().startswith("ports ")]
    check(not _staged,
          f"{_stmt!r} was refused and staged anyway, so `save` would write a "
          f"port range the dataplane cannot carry: {_staged}")
    check(_owed in _said,
          f"{_stmt!r} was refused without saying {_owed!r}, which is the one "
          f"thing that tells this refusal from the other two on the same "
          f"statement: {_said.strip()!r}")
    _over = [(len(l), l) for l in _said.splitlines() if len(l) > CONSOLE_COLUMNS]
    check(not _over,
          f"{_stmt!r}: the refusal wraps on a serial console: {_over}")
# The control, and it is what keeps the four above from being satisfied by a
# box that refuses every port range: a legal one is accepted and is IN the
# candidate afterwards.
for _stmt in ("ports 1 65535", "ports 1024 65535", "ports 2000 2999"):
    _s, _refused, _said = _typed(["nat", "cgnat", "pool P"], _stmt)
    check(not _refused,
          f"{_stmt!r} is inside 1-65535 at both ends and in order, and the "
          f"box refused it: {_said.strip()!r}")
    check(_stmt in _s.candidate.text(),
          f"{_stmt!r} was accepted and is not in the candidate, so the pool "
          f"the operator asked for is not the pool that would commit")

# THE ORDERING SAID AS AN ORDERING. `ports 9000 100` breaks neither end's
# bound, so a refusal quoting 1-65535 would send the operator off to check
# two numbers that are both fine.
_s, _refused, _said = _typed(["nat", "cgnat", "pool P"], "ports 9000 100")
check("1-65535" not in _said,
      f"the ordering refusal quotes the range, which is the one thing wrong "
      f"with neither of those two numbers: {_said.strip()!r}")

# `?` QUOTES THE BOUND NOW. It said `<min> <max>` while nothing enforced
# anything, which was at least honest; a bound that the hint does not carry
# is the gap #350 to #354 were about, and the operator reads the hint before
# typing.
check(prismash.CGNAT_POOL["ports"][1] == "<1-65535> <1-65535>",
      f"`?` does not quote the bound it now enforces: "
      f"{prismash.CGNAT_POOL['ports'][1]!r}")

# THE #357 TRAP, ASSERTED HERE rather than taken on trust. The guards above
# are at the typed-statement site; had they gone into Config._pool_line() -
# which is where the statement is modelled, and where the next person adding
# a constraint will reach first - then a box whose dataplane reports a pool
# with an odd port range would become a box the VTY cannot model, and
# `commit` would refuse EVERY change on it. The reader must keep taking what
# the box already has, whatever the prompt would say about it.
for _range in ("1 65535", "0 65535", "9000 100", "70000 80000"):
    _text = ("! cgnat\n"
             "cgnat pool det\n"
             " inside 100.64.0.0/22\n"
             " outside 200.150.0.16/32\n"
             f" ports {_range}\n"
             " exit\n")
    _read = _config.Config.parse(_text, dataplane=True)
    check(not _read.unknown,
          f"a configuration carrying `ports {_range}` no longer parses, so a "
          f"box whose dataplane reports one cannot be committed at all "
          f"(#357): {_read.unknown}")
    check(getattr(_read.pools.get("det"), "ports", None) == _range,
          f"`ports {_range}` was read as something else, so a commit would "
          f"write a different range over the one the box has: "
          f"{getattr(_read.pools.get('det'), 'ports', None)!r}")
print("  five values refused for three reasons, three accepted and staged, "
      "and the reader still models what the box has")

print("\n=== a port range too narrow for one block is asked at COMMIT ===")
# The third check, and the one with a design question in it: it needs
# `block-size` as well as `ports`, and the two can be typed in either order.
# `ports 1000 1100` followed by `block-size 64` is a correct pool built in a
# correct order, and a guard at the `ports` line would have refused its first
# half. So the line takes it and the commit check refuses it - asserted from
# both sides here, because "the line takes it" is the half that would rot
# first if somebody moved the check.
_s, _refused, _said = _typed(["nat", "cgnat", "pool P", "block-size 512"],
                             "ports 1000 1100")
check(not _refused,
      f"`ports 1000 1100` was refused at the line under block-size 512, "
      f"which refuses a legal editing order: the operator's next statement "
      f"may be `block-size 64`: {_said.strip()!r}")
check("ports 1000 1100" in _s.candidate.text(),
      "and it is not in the candidate either")
# The predicate on the finished pool, which is where both numbers are. The
# commit check that calls it is driven in test_prismash_apply.py, which has the
# dataplane stub this file does not.
_p = _config.Pool("P")
_p.block_size = "512"
_p.ports = "1000 1100"
check(_p.block_fits() == "block-size 512 exceeds the 101 usable ports",
      f"a 101-port range holding a 512-port block was not reported, or not "
      f"in the dataplane's own words: {_p.block_fits()!r}")
_p.block_size = "64"
check(_p.block_fits() is None,
      f"a 101-port range was refused a 64-port block: {_p.block_fits()!r}")
_p.block_size = "512"
_p.ports = ""
check(_p.block_fits() is None,
      f"a pool with no `ports` statement was measured against a range it "
      f"does not have: {_p.block_fits()!r}")
# Whatever the box already has, the predicate declines rather than raises:
# the numbers may have come out of a file somebody edited by hand, and a
# check that raised on them is #357 through another door.
for _odd in ("9000 100", "abc def", "1024"):
    _p.ports = _odd
    check(_p.block_fits() is None,
          f"`ports {_odd}` made the block check judge a range it cannot "
          f"read: {_p.block_fits()!r}")
print("  taken at the line, refused at the commit, and silent on a range it "
      "cannot read")



print("\n=== commit from inside a pool, a user and an SNMP user (#464) ===")
# Each of the three answered "unknown statement" to `commit`, so a pool had to
# be walked out of to be applied. The commit itself is the one `[edit]` runs;
# what these pin is that it is REACHED, offered, and that the block the
# operator is standing in is still the candidate's afterwards.
for _enter, _mode in (("nat|cgnat|pool P", "pool"), ("user ops", "user"),
                      ("snmp-user mon", "snmp-user")):
    sh = shell_with_frr()
    sh.feed("configure")
    for _l in _enter.split("|"):
        sh.feed(_l)
    check(sh.mode == _mode, f"{_enter!r} did not enter {_mode}: {sh.mode}")
    _opts, _, _ = sh._options("comm", True)
    check("commit" in _opts, f"`?` does not offer commit in {_mode}: "
                             f"{sorted(_opts)}")
    _out = commit_check(sh)
    check("unknown" not in _out.lower() and sh.mode == _mode,
          f"`commit check` in {_mode} was not a commit, or left the block: "
          f"{_out!r} {sh.mode}")
    _store, _name = sh.block_store()
    check(_store is not None and _store.get(_name) is not None,
          f"the {_mode} block is not the candidate's after the commit")
    print(f"  {_mode}: offered, committed, still standing in it")

# A commit that leaves a DIFFERENT candidate behind: the block is found again
# by name, not kept as an object the candidate no longer holds.
sh = shell_with_frr()
sh.feed("configure")
for _l in ("nat", "cgnat", "pool P"):
    sh.feed(_l)
_before = sh.pool
_fresh = _config.Config.parse(sh.candidate.text())
sh.do_commit = lambda args: setattr(sh, "candidate", _fresh)
sh.feed("commit")
check(sh.mode == "pool" and sh.pool is _fresh.pools.get("P")
      and sh.pool is not _before,
      "after a commit that replaced the candidate the pool being edited is "
      "still the old object, so what is typed next goes nowhere")
# And one that leaves a candidate WITHOUT it: back to [edit], and said.
_bare = _config.Config.parse("")
sh.do_commit = lambda args: setattr(sh, "candidate", _bare)
_o = io.StringIO()
with contextlib.redirect_stdout(_o):
    sh.feed("commit")
check(sh.mode == "config" and sh.pool is None
      and "the candidate after this commit no longer holds P" in _o.getvalue(),
      f"a pool the candidate lost kept the operator typing into it: "
      f"{sh.mode} {_o.getvalue()!r}")
sh2 = shell_with_frr()
with contextlib.redirect_stdout(io.StringIO()):
    sh2.feed("configure")
    sh2.feed("user ops")
    sh2.do_commit = lambda args: setattr(sh2, "candidate", None)
    sh2.commit_from_block([])
check(sh2.mode == "config" and sh2.user is None,
      "a commit that left no candidate at all kept the user block open")
print("  found again by name, or left and said")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
