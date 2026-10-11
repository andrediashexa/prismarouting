#!/usr/bin/env python3
"""
Two boxes, one question: would a failover land subscribers where they are?

In deterministic mode a subscriber's public address and port block are a
function of the pool definition, so two boxes carrying the same pools produce
the same answer without exchanging a byte - and two boxes carrying DIFFERENT
pools fail silently. The failover starts mapping subscribers to other blocks,
and the logs on both boxes look perfectly healthy (docs/ha.md).

WHICH FIELDS MUST MATCH IS NOT DECIDED HERE, and that is the design. It is
decided by `Pool.lines(in_place=False)`, which is what `Config.delta_to`
already uses to decide a pool must be rebuilt, and
`test/test_prismash_config.py` asserts that classification field by field AND
asserts the table covers every field a pool has. This file asserts the two
never drift apart: the fingerprint and the comparison an operator is shown
must agree with each other and with that classification.

WHAT THIS FILE IS MOSTLY ABOUT is the other direction. A check that says "they
match" over a comparison it did not make is worse than no check, because now
something has said so. So most of the cases below hand the box a peer
configuration it cannot read - absent, unreadable, empty, truncated, written
in statements this VTY cannot hold - and assert that none of them reads as
agreement. The dataplane being down is the same case from this box's side.

And the case that keeps the check honest in the opposite direction: two boxes
that differ only on a TCP clamp, an ALG or a session limit map every
subscriber to the same address, and reporting them as divergent would be a
warning an operator learns to skip (#208).
"""
import contextlib
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "prismash"))
import config      # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nat_fixture import in_nat_block      # noqa: E402
import prismash      # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


RUNNING = """cgnat pool residential
 inside 100.64.0.0/22
 outside 200.150.16.0/28
 block-size 2048
 ports 1024 9215
 policy interleaved
 mode deterministic
 exit
cgnat pool corporate
 inside 100.65.0.0/24
 outside 200.150.17.0/29
 block-size 4096
 mode deterministic
 exit
"""


class FakeVpp:
    """A dataplane holding one configuration, and able to be dead."""

    TRACE = False

    def __init__(self, text=RUNNING, up=True):
        self.text = text
        self.up = up

    def run(self, cmd):
        if not self.up:
            return 1, ""
        return (0, self.text) if cmd == "show bpa config" else (0, "")

    def running(self):
        return 0, ""

    def reachable(self):
        return self.up


def shell(text=RUNNING, up=True):
    sh = prismash.Shell()
    # Stated outright rather than inherited from whoever runs the suite, for
    # the reason test_prismash_apply.py gives: Shell() reads the invoking
    # account's real groups, and a suite that depends on them fails on any
    # developer machine.
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT}
    sh.dp = FakeVpp(text, up)
    sh.frr = type("F", (prismash.Frr,), {
        "available": lambda self: False,
        "show": lambda self, c: (1, ""),
        "reload": lambda self, lines, check_only=False: (0, "")})()
    return sh


def said(fn, *a, **kw):
    """What the box printed, as one string."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*a, **kw)
    return buf.getvalue()


WORK = tempfile.mkdtemp()


def peer_file(text, name):
    """The peer's saved file. Fixtures here are written in the dataplane's
    flat words, which is what FakeVpp answers; a saved file is the `nat` >
    `cgnat` block (#446), so it is written as one."""
    path = os.path.join(WORK, name)
    with open(path, "w") as f:
        f.write(in_nat_block(text))
    return path


print("=== the fingerprint and the comparison never disagree ===")
# Two ways of asking the same question, and an operator reads both: the digest
# off the two consoles, and the diff that says which pool. A box where one says
# "identical" and the other names a pool is a box nobody can act on.
_base = config.Config.parse(RUNNING, dataplane=True)
_cases = {
    "identical": RUNNING,
    "a mapping field moved": RUNNING.replace("block-size 2048",
                                             "block-size 1024"),
    "a port range moved": RUNNING.replace("ports 1024 9215", "ports 2048 9215"),
    "a policy moved": RUNNING.replace("policy interleaved", "policy blocked"),
    "a pool is missing": RUNNING.split("cgnat pool corporate")[0],
    "an in-place setting moved": RUNNING.replace(
        " block-size 4096", " block-size 4096\n tcp-mss 1400"),
}
for label, text in _cases.items():
    other = config.Config.parse(text, dataplane=True)
    same_digest = (_base.mapping_fingerprint() == other.mapping_fingerprint())
    no_findings = not _base.mapping_diff_to(other)
    check(same_digest == no_findings,
          f"{label}: the digest says {'same' if same_digest else 'different'} "
          f"and the comparison says "
          f"{'same' if no_findings else 'different'}")
print(f"  {len(_cases)} cases, digest and comparison agree on every one")

print("\n=== two identical configurations are NOT reported as differing ===")
# The red that proves the check is not blind. A comparison that always finds
# something is a comparison nobody reads, and it would fire on every correctly
# configured pair in service.
check(_base.mapping_diff_to(config.Config.parse(RUNNING, dataplane=True)) == [],
      "the same configuration twice must produce no finding")
check(_base.mapping_fingerprint()
      == config.Config.parse(RUNNING, dataplane=True).mapping_fingerprint(),
      "and the same digest")
# The same pools, typed in the other order. Two boxes configured by two people
# are the same box; a digest that said otherwise would refuse a correct pair.
_first, _second = RUNNING.split("cgnat pool corporate")
_swapped = "cgnat pool corporate" + _second + _first
check(config.Config.parse(_swapped, dataplane=True).pools.keys() == _base.pools.keys(),
      "the reordered fixture must hold the same pools, or it measures "
      "nothing")
check(config.Config.parse(_swapped, dataplane=True).mapping_fingerprint()
      == _base.mapping_fingerprint(),
      "the same pools in a different order are the same mapping")
print("  identical configurations, and the same pools written in either "
      "order")

print("\n=== settings that move no subscriber are not a divergence ===")
# The harm #208 was filed for, in this feature's shape: a refusal that fires
# on a configuration that is correct. The dataplane changes these on a pool
# that is carrying traffic, so two peers may legitimately differ on them - and
# an operator warned about a pair that is fine stops reading the warning that
# means their subscribers are about to move.
for label, mutation in (
        ("a TCP clamp", " tcp-mss 1400"),
        ("an ALG", " alg rtsp off"),
        ("a session ceiling", " max-sessions 2000"),
        ("a timeout", " timeout udp 900"),
        ("a bypass prefix", " bypass destination 10.0.0.0/8"),
        ("a port forward", " forward tcp 8080 to 100.64.0.5 80")):
    other = config.Config.parse(
        RUNNING.replace(" mode deterministic\n exit",
                        f" mode deterministic\n{mutation}\n exit", 1), dataplane=True)
    check(_base.mapping_diff_to(other) == [],
          f"{label} differing between peers is not a mapping divergence: "
          f"{_base.mapping_diff_to(other)}")
    check(not _base.is_same(other),
          f"{label}: the fixture must actually differ, or this case measures "
          "nothing")
print("  six in-place settings differ between the peers, and none is reported")

print("\n=== a divergence names the pool and quotes both statements ===")
_peer = peer_file(RUNNING.replace("block-size 2048", "block-size 1024"),
                  "diverges.conf")
out = said(shell().show_ha, ["peer", _peer])
# After the first warning, so that the pool list in the header above cannot
# satisfy it: what has to name the pool is the finding.
check("residential" in out.split("warning", 1)[-1],
      f"the finding must name the pool:\n{out}")
check("block-size 2048" in out and "block-size 1024" in out,
      f"both statements must be quoted whole:\n{out}")
check("corporate" not in out.split("warning")[-1],
      f"the pool that agrees must not be reported:\n{out}")
# Every row an operator reads, inside the console budget. A statement MAY run
# past it and is printed whole; prose may not. The statement rows are
# recognised by their own shape rather than by the text that happens to be in
# them: a filter that skipped any row mentioning a statement would also skip a
# paragraph that quoted one, which is exactly the row that must not wrap.
_STATEMENT = ("    this box: ", "    the peer: ", "    nat / cgnat / pool ")
_prose = [l for l in out.splitlines() if not l.startswith(_STATEMENT)]
check(all(len(l) <= config.CONSOLE_COLUMNS for l in _prose),
      "every prose row must fit the console: "
      + repr([l for l in _prose if len(l) > config.CONSOLE_COLUMNS]))
print(f"  {len(out.splitlines())} rows, longest prose row "
      f"{max(len(l) for l in _prose)} columns")

# #558: the same comparison as data, which GetHaStatus carries, and the
# screen drawn from a reading handed to it is the screen above.
_sh = shell()
_hf = _sh.ha_facts(_peer)
check(_hf["dataplane"] and _hf["peer_read"] and _hf["peer_path"] == _peer,
      f"ha_facts read this box and the peer: {_hf}")
check([d[0] for d in _hf["divergences"]] == ["residential"]
      and "block-size 2048" in _hf["divergences"][0][1]
      and "block-size 1024" in _hf["divergences"][0][2],
      f"one divergence, the pool with both statements: {_hf['divergences']}")
check(_hf["fingerprint"] != _hf["peer_fingerprint"] and _hf["pools"]
      and _hf["peer_copied"] is not None,
      "the two fingerprints differ, the pools are named, the copy is dated")
check(said(_sh.show_ha, ["peer", _peer], _hf) == out,
      "and the screen drawn from that reading is the screen above")
_hd = shell(up=False).ha_facts(_peer)
check(not _hd["dataplane"] and not _hd["peer_read"] and not _hd["divergences"]
      and not _hd["fingerprint"],
      f"a dataplane that does not answer stops the reading there: {_hd}")

print("\n=== a pool on only one box is reported, in both directions ===")
_only_peer = peer_file(RUNNING + "cgnat pool guest\n inside 100.66.0.0/24\n"
                       " outside 200.150.18.0/29\n exit\n", "extra.conf")
out = said(shell().show_ha, ["peer", _only_peer])
check("guest" in out and "not on this box" in out,
      f"a pool only the peer has must be reported:\n{out}")
_only_here = peer_file(RUNNING.split("cgnat pool corporate")[0], "fewer.conf")
out = said(shell().show_ha, ["peer", _only_here])
check("corporate" in out and "not on the peer" in out,
      f"a pool only this box has must be reported:\n{out}")
print("  both directions named")

print("\n=== peers that genuinely agree are told so, and nothing else ===")
_agree = peer_file(RUNNING.replace(" mode deterministic\n exit",
                                   " mode deterministic\n tcp-mss 1400\n exit",
                                   1), "agrees.conf")
out = said(shell().show_ha, ["peer", _agree])
check("warning" not in out and "error" not in out,
      f"a pair that agrees must draw no warning:\n{out}")
check("same public address" in out, f"and must be told so:\n{out}")
print("  the pair differs on a clamp, agrees on the mapping, and is told so")

print("\n=== the peer file is what `save` writes, not what the device says ===")
# The operator's actual step: `save` on the peer, copy the file across. That
# file is NOT the text the dataplane prints - it carries the `nat` hierarchy,
# the SSH and SNMP stanzas and the routing marker - and a comparison that only
# ever saw a device read-back would report every pool as missing on a box
# where nothing at all is wrong.
_device = config.Config.parse(RUNNING, dataplane=True)
_as_saved = peer_file(_device.text(), "as-saved.conf")
_read, _why = prismash.read_peer_config(_as_saved)
check(_read is not None, f"a saved configuration must be readable: {_why}")
check(_read is not None
      and _read.mapping_fingerprint() == _device.mapping_fingerprint(),
      "a configuration saved and read back must carry the same fingerprint")
check(_read is not None and _device.mapping_diff_to(_read) == [],
      f"and no divergence: "
      f"{_device.mapping_diff_to(_read) if _read else None}")
check("\n  pool residential" in _device.text(),
      "the fixture must be the saved spelling, or this case measures nothing")
print("  a saved file and a device read-back of the same box agree")

print("\n=== a peer this box could not read never reads as a match ===")
# The silent agreement, arrived at from the other side. Every one of these
# produces an EMPTY configuration if it is parsed carelessly, and an empty
# configuration compares equal to another empty configuration.
_unreadable = {
    "no file at all": os.path.join(WORK, "absent.conf"),
    "an empty file": peer_file("", "empty.conf"),
    "a file with no pool in it": peer_file("ssh port 22\n", "nopool.conf"),
    "a pool carrying a statement this VTY cannot hold":
        peer_file(RUNNING.replace(" exit", " frobnicate 7\n exit", 1),
                  "unknown.conf"),
}
for label, path in _unreadable.items():
    cfg, why = prismash.read_peer_config(path)
    check(cfg is None, f"{label} must not parse as a peer configuration")
    check(bool(why), f"{label} must come with a reason")
    out = said(shell().show_ha, ["peer", path])
    check("nothing here says the two boxes agree" in out,
          f"{label}: the report must refuse to call it a match:\n{out}")
    check("same public address" not in out,
          f"{label}: the report claimed agreement:\n{out}")
print(f"  {len(_unreadable)} unreadable peers, none of them a match")

# A directory is the case an exception would escape from rather than the
# reason arriving at the prompt, which is where somebody is standing in a
# maintenance window.
_cfg, _why = prismash.read_peer_config(WORK)
check(_cfg is None and _why, f"a directory must be refused with a reason: "
                             f"{_why!r}")
print(f"  and a directory reads as: {_why}")

# ONLY A REGULAR FILE OF A CONFIGURATION'S SIZE (#558): the path is typed at
# the prompt and sent by an API client. A FIFO with no writer would hold the
# reader for ever (open() blocks, then read() waits); a device is read until
# memory runs out. Measured on the reader as 0.152.9 had it: open() of a FIFO
# with no writer blocked and this case hung the suite, so it runs under an
# alarm that fails it instead.
import signal  # noqa: E402
_fifo = os.path.join(WORK, "peer.fifo")
os.mkfifo(_fifo)
signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(
    TimeoutError("read_peer_config waited on a FIFO")))
signal.alarm(5)
try:
    _cfg, _why = prismash.read_peer_config(_fifo)
except TimeoutError as e:
    _cfg, _why = None, f"HUNG: {e}"
signal.alarm(0)
check(_cfg is None and _why == f"{_fifo} is not a regular file",
      f"a FIFO is refused, never waited on: {_why!r}")
_cfg, _why = prismash.read_peer_config("/dev/zero")
check(_cfg is None and _why == "/dev/zero is not a regular file",
      f"a device is refused, never read: {_why!r}")
_big = peer_file(RUNNING + "#" * prismash.PEER_CONFIG_MAX, "big.conf")
_cfg, _why = prismash.read_peer_config(_big)
check(_cfg is None and "more than a configuration's" in (_why or ""),
      f"a file past PEER_CONFIG_MAX is refused: {_why!r}")
out = said(shell().show_ha, ["peer", "/dev/zero"])
check("/dev/zero is not a regular file" in out
      and "nothing here says the two boxes agree" in out,
      f"and the screen says why, claiming nothing:\n{out}")
print(f"  a FIFO, a device and {prismash.PEER_CONFIG_MAX + 1} bytes refused")

print("\n=== a dataplane that is not answering never reads as a match ===")
# Without this the empty configuration a dead dataplane produces is compared
# as if it were the truth: a box with two pools reports both as "the peer has
# them and this box does not", or, against an empty peer file, as agreement.
out = said(shell(up=False).show_ha, ["peer", _agree])
check("not answering" in out, f"the report must say why it stopped:\n{out}")
check("same public address" not in out,
      f"and must not claim agreement:\n{out}")
check("corporate" not in out,
      f"nor invent a divergence out of a configuration it could not "
      f"read:\n{out}")
print("  the report stops at this box and says so")

print("\n=== with no peer configuration, the box says it cannot tell ===")
_saved = prismash.PEER_CONFIG_PATH
prismash.PEER_CONFIG_PATH = os.path.join(WORK, "absent.conf")
out = said(shell().show_ha, [])
check("nothing here says the two boxes agree" in out,
      f"a box with no peer configuration must say so:\n{out}")
check(_base.mapping_fingerprint() in out,
      f"and must still print its own fingerprint, which is what the operator "
      f"reads off the other box:\n{out}")
print("  it prints this box's fingerprint and refuses to guess at the peer's")

print("\n=== commit warns about a divergence and never refuses it ===")
# The two boxes cannot be committed in the same instant, so every legitimate
# capacity change diverges the pair between the first commit and the second.
# A refusal would fire on every correct procedure, and the way past it would
# be to stop copying the peer's configuration - which removes the check
# instead of the divergence.
prismash.PEER_CONFIG_PATH = _peer
sh = shell()
target = config.Config.parse(RUNNING, dataplane=True)
out = said(sh.apply, target, True)
check("residential" in out and "block-size" in out,
      f"commit check must report the divergence:\n{out}")
check("warning" in out, f"and report it as a warning:\n{out}")
check(sh.failed == 0,
      f"and must not count it as an error: failed={sh.failed}")
ok = sh.apply(target, check_only=True)
check(ok is True, "the commit must still be allowed through")
print(f"  commit check -> {ok}, errors recorded: {sh.failed}")

print("\n=== and says nothing about a pair that agrees ===")
prismash.PEER_CONFIG_PATH = _agree
out = said(shell().apply, config.Config.parse(RUNNING, dataplane=True), True)
check("does not map subscribers the same way" not in out,
      f"a pair that agrees must draw no warning at commit:\n{out}")
prismash.PEER_CONFIG_PATH = os.path.join(WORK, "absent.conf")
out = said(shell().apply, config.Config.parse(RUNNING, dataplane=True), True)
check("does not map subscribers the same way" not in out,
      f"and a box with no peer configuration must not warn at all:\n{out}")
prismash.PEER_CONFIG_PATH = _saved
print("  silent on an agreeing pair, and on a box that has no peer")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
