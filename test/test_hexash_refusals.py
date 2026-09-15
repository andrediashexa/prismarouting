#!/usr/bin/env python3
"""
Every refusal the VTY can print, against the ones something has watched print.

WHAT A REFUSAL IS. `src/hexash/hexash.py` says no to the operator 180 times,
through `self.err(...)` and `self.err_dataplane(...)`. Each one of those is
what stands between an operator and a configuration this box cannot hold: a
pool the dataplane would reject, an address that would translate the box's own
BGP session, a card that carries the default route being handed away. A
refusal that never fires lets that configuration through, and the box is then
wrong in the way the refusal was written to prevent.

WHY A READER AND NOT A LIST. Three refusals in this repository were measured
and closed in the last day, and all three were green in both directions: the
`vat2` stub in test/test_selftest.sh answered a constant, so "binary API NOT
registered" had never run on any of the 122 boxes that suite arranges;
tools/release-gate.sh carries 22 refusals of which two had ever been driven;
lab/vty-ssh.sh asserted an address was NOT in effect against a haystack that
was empty on exactly the box the assertion was about. Each was found by
somebody thinking to look. The two readers at the end of test/test_selftest.sh
are what this repository does instead of thinking to look, and this file is the
second of them carried from shell to Python: the call sites are parsed out of
the product, so a refusal added to the VTY with no box added here goes red on
the next run, and there is no list anywhere to keep in step.

HOW COVERAGE IS MEASURED. Not from the text on the screen. A message is mostly
interpolated value, several sites print the same words - "interface {ifname}:
the dataplane does not have it" is printed at nine different call sites - and a
reader keyed on the text cannot tell them apart. So `Shell.err` is wrapped and
the VTY's own frames are read off the stack, which spends a refusal on the LINE
that produced it. That also decides the mutation this file answers to: changing
a message here changes nothing, because nothing here reads the message. What
turns it red is a branch that stops being reached.

WHAT IT CANNOT SEE, stated rather than left to be discovered:

  * The suites it runs are the ones that put `src/hexash` on their path, read
    from test/suites.list. A suite driving the VTY some other way - a shell
    suite running hexash.py as a program - would not be counted. None does
    today; the day one does, its refusals read as uncovered, which is the safe
    direction.
  * The labs are not run here and cannot be: they need a container, and
    tools/run-all-labs.sh is the thing that runs them. A refusal driven only by
    a lab therefore counts as uncovered, and that is honest rather than
    pessimistic - `make test` has to be able to tell.
  * Two call sites are shared by many refusals. `require()` prints every
    permission denial from one line, and `say_dataplane_if_refusal()` prints
    every reason `dataplane_if_refusal()` can return. Driving one of them marks
    the site covered, and the others behind it are invisible here. They are
    measured by name in test/test_hexash_perms.py and test/test_dataplane_if.py
    instead.

THE COUNT IS A FLOOR, and there is no allowlist. An allowlist is the thing the
next person grows: a refusal lands in it once and nobody reads it again. So the
unrendered ones are NAMED, the number they may not exceed is written down, and
lowering it is the only way to change it - the idiom test_off_run_coverage.sh
already uses for labs with no off run.
"""
import ast
import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VTY = os.path.join(REPO, "src", "hexash", "hexash.py")

# ------------------------------------------------------------------ the hook
#
# ONE COPY, used twice. This text is written to a temporary directory as
# `sitecustomize.py`, which Python imports before any suite's own code runs, so
# every child process records what the VTY refused. The same text is then
# exec'd in THIS process, before hexash is imported, so the boxes below are
# recorded by the identical mechanism. Two hand-kept copies of it would be two
# things that can disagree, and the one that drifted would report a refusal as
# covered that nothing had driven.
HOOK = r'''
import builtins
import os
import sys

LOG = os.environ.get("HEXASH_REFUSAL_LOG")


def _record(module):
    shell = getattr(module, "Shell", None)
    if shell is None or getattr(shell, "_refusals_recorded", False):
        return
    inner = shell.err
    vty = os.path.realpath(module.__file__)

    def err(self, msg):
        # EVERY VTY frame on the stack, not just the immediate caller.
        # `err_dataplane` and `require` call `err` themselves, so the immediate
        # caller is inside the VTY either way and the site the operator's
        # refusal actually came from is further up. Recording the whole chain
        # spends one refusal on every site that produced it, which is what
        # makes a helper's callers visible instead of the helper.
        frame = sys._getframe(1)
        seen = []
        while frame is not None:
            if os.path.realpath(frame.f_code.co_filename) == vty:
                seen.append(str(frame.f_lineno))
            frame = frame.f_back
        with open(LOG, "a") as out:
            out.write(",".join(seen) + "\n")
        return inner(self, msg)

    shell.err = err
    shell._refusals_recorded = True


if LOG:
    _real_import = builtins.__import__

    def _hooked(name, *a, **k):
        module = _real_import(name, *a, **k)
        # After the import rather than before it: the class has to exist to be
        # wrapped, and `sys.modules` is what holds it once a second importer
        # gets the cached module rather than a fresh one.
        if name == "hexash":
            _record(sys.modules.get("hexash", module))
        return module

    builtins.__import__ = _hooked
'''

_tmp = tempfile.mkdtemp(prefix="hexash-refusals-")
LOG = os.path.join(_tmp, "rendered")
open(LOG, "w").close()
os.environ["HEXASH_REFUSAL_LOG"] = LOG
with open(os.path.join(_tmp, "sitecustomize.py"), "w") as _f:
    _f.write(HOOK)
exec(compile(HOOK, "<hexash-refusal-hook>", "exec"), {"__name__": "_hook"})

sys.path.insert(0, os.path.join(REPO, "src", "hexash"))
import config      # noqa: E402
import hexash      # noqa: E402
import vppapi      # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def bad(msg):
    global fail
    fail += 1
    print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


# ---------------------------------------------------------------- the boxes
#
# A box arranges the state one refusal is written for and asserts the VTY says
# so. The text is written out here rather than read from the product, on
# purpose: a test that takes its needle from the string it is checking agrees
# with any change to that string, including a change that empties it.

REASON = "refused: bad value"


class Api:
    """
    A dataplane that has the interfaces it is told about and refuses the
    messages it is told to refuse.

    `state` is settable because two refusals in `apply` read it rather than
    `ok`: a pool or a forward being REMOVED and already gone is the outcome
    asked for, so those two branches are reached only by a failure that is not
    a plain refusal.
    """

    def __init__(self, have=None, refuse=(), state=vppapi.REFUSED):
        self.have = dict(have or {})
        self.refuse = set(refuse)
        self.state = state
        self.calls = []

    def available(self):
        return (vppapi.OK, "stub")

    def interface_index(self, name):
        return self.have.get(name)

    def call(self, message, args):
        self.calls.append((message, args))
        if message in self.refuse:
            return vppapi.Reply(self.state, retval=-7, reason=REASON)
        return vppapi.Reply(vppapi.OK)

    def dump(self, message, args):
        return vppapi.Reply(vppapi.OK, fields={"rows": []})


class Dp(hexash.Vpp):
    """
    The CLI side of the dataplane, answering nothing.

    Derived from hexash.Vpp rather than free-standing for the reason
    test_hexash_apply.py records: a stub that enumerates the product's methods
    stops having whatever the product adds next, and `err_dataplane` reads
    `plugin_build()` before it says anything. Answering nothing means there is
    no build to compare, which is the box these cases want - what they are
    about is the refusal, not the upgrade window.
    """

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


def frr_stub(available=False, reload_rc=0, reload_out=""):
    return type("F", (hexash.Frr,), {
        "available": lambda self: available,
        "show": lambda self, c: (1, ""),
        "reload": lambda self, lines, check_only=False: (reload_rc,
                                                         reload_out),
    })()


def shell(api=None, dp=None, frr=None, caps=None):
    """
    A Shell with its capability stated outright, for the reason
    test_hexash_apply.py states it: Shell() reads the invoking account's real
    group membership, so left alone every case here is refused before it
    reaches the refusal it is about.
    """
    sh = hexash.Shell()
    sh.caps = {hexash.CAP_ALL, hexash.CAP_CGNAT} if caps is None else caps
    sh.api = api or Api()
    sh.dp = dp or Dp()
    sh.frr = frr or frr_stub()
    return sh


def driven(fn, *a, **k):
    """What the VTY printed while `fn` ran, and what it returned."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rv = fn(*a, **k)
    return rv, out.getvalue()


def says(label, text, line, rv=None, expect=False):
    """One refusal, asserted whole and on a row of its own."""
    check(line in text.splitlines(),
          f"{label}: expected the row {line!r}, got {text!r}")
    if rv is not None:
        check(rv is expect, f"{label}: returned {rv!r}, not {expect!r}")


print("=== an interface the dataplane has not got, on every verb that "
      "touches one ===")
# NINE CALL SITES, ONE SENTENCE. Every one of these translates a name the
# operator typed into the dataplane's index before it sends anything, and every
# one of them has to tell a mistyped name apart from a refusal - a box told
# "refused" sends somebody to read the CGNAT configuration looking for a name
# that was never there. They are driven in a table because the box is the same
# box nine times over; splitting them into nine sections would repeat the
# arrangement and hide that they agree.
MISSING = "pg9"
_absent = [
    ("attach CGNAT", lambda sh: sh.set_cgnat_feature(MISSING, "inside", True)),
    ("no shutdown", lambda sh: sh.set_admin(MISSING, True)),
    ("mac-address", lambda sh: sh.set_mac(MISSING, "02:00:00:00:00:01")),
    ("router advertisements", lambda sh: sh.set_ra_suppress(MISSING, True)),
    ("vrf", lambda sh: sh.set_vrf(MISSING, 5)),
    ("linux pair", lambda sh: sh.set_lcp_pair(MISSING, "mgmt0")),
    ("address", lambda sh: sh.set_address(MISSING, "10.0.0.1/24", True)),
    ("description", lambda sh: sh.set_description(MISSING, "uplink")),
    ("urpf", lambda sh: sh.set_urpf(MISSING, "strict")),
]
for label, verb in _absent:
    rv, text = driven(verb, shell(Api(have={"pg0": 1})))
    says(label, text, f"error: interface {MISSING}: the dataplane does not "
         f"have it", rv)
print(f"  {len(_absent)} verbs, each refusing a name the dataplane has not got")

print("\n=== and the same verbs when the dataplane answers and says no ===")
# THE OTHER HALF, and the half that decides whether the operator is told an
# upgrade is why: these go through `err_dataplane`, which the plain refusals
# above must never reach. A commit that reported success while the dataplane
# held none of the change is the defect the whole binary-API client exists for.
_refused = [
    ("attach CGNAT", "bpa_interface_add_del_feature",
     lambda sh: sh.set_cgnat_feature("pg0", "inside", True),
     f"error: could not attach CGNAT to pg0: {REASON}"),
    ("detach CGNAT", "bpa_interface_add_del_feature",
     lambda sh: sh.set_cgnat_feature("pg0", "inside", False),
     f"error: could not detach CGNAT from pg0: {REASON}"),
    ("no shutdown", "sw_interface_set_flags",
     lambda sh: sh.set_admin("pg0", True),
     f"error: interface pg0: no shutdown: {REASON}"),
    ("mac-address", "sw_interface_set_mac_address",
     lambda sh: sh.set_mac("pg0", "02:00:00:00:00:01"),
     f"error: interface pg0: could not set 02:00:00:00:00:01: {REASON}"),
    ("router advertisements", "sw_interface_ip6nd_ra_config",
     lambda sh: sh.set_ra_suppress("pg0", True),
     f"error: interface pg0: router advertisements: {REASON}"),
    ("vrf", "sw_interface_set_table",
     lambda sh: sh.set_vrf("pg0", 5),
     f"error: interface pg0: could not move it to vrf 5: {REASON}"),
    ("linux pair", "lcp_itf_pair_add_del",
     lambda sh: sh.set_lcp_pair("pg0", "mgmt0"),
     f"error: could not pair pg0 with mgmt0: {REASON}"),
    ("address", "sw_interface_add_del_address",
     lambda sh: sh.set_address("pg0", "10.0.0.1/24", True),
     f"error: interface pg0: could not add 10.0.0.1/24: {REASON}"),
    ("description", "sw_interface_tag_add_del",
     lambda sh: sh.set_description("pg0", "uplink"),
     f"error: interface pg0: description: {REASON}"),
    ("urpf", "urpf_update",
     lambda sh: sh.set_urpf("pg0", "strict"),
     f"error: interface pg0: urpf strict: {REASON}"),
    ("create loopback", "create_loopback_instance",
     lambda sh: sh.create_loopback("loop3"),
     f"error: could not create loop3: {REASON}"),
    ("delete loopback", "delete_loopback",
     lambda sh: sh.delete_loopback("loop3"),
     f"error: could not delete loop3: {REASON}"),
    ("create sub-interface", "create_vlan_subif",
     lambda sh: sh.create_subif("pg0.10", {"outer": 10, "inner": 0}),
     f"error: could not create pg0.10: {REASON}"),
    ("delete sub-interface", "delete_subif",
     lambda sh: sh.delete_subif("pg0.10"),
     f"error: could not delete pg0.10: {REASON}"),
]
for label, message, verb, line in _refused:
    api = Api(have={"pg0": 1, "loop3": 7, "pg0.10": 9}, refuse={message})
    rv, text = driven(verb, shell(api))
    says(label, text, line, rv)
print(f"  {len(_refused)} verbs, each naming the dataplane's own refusal")

print("\n=== a sub-interface whose parent is not on this box ===")
# Distinct from the sentence above and not a duplicate of it: the name the
# operator typed IS the one they meant, and what is missing is the card under
# it. Saying "pg9.10 is not here" would send them to look for the wrong thing.
_rv, _text = driven(lambda: shell(Api(have={"pg0": 1})).create_subif(
    "pg9.10", {"outer": 10, "inner": 0}))
says("parent", _text, "error: interface pg9.10: its parent pg9 is not on "
     "this box", _rv)
print("  pg9.10 refused for its parent, not for itself")

print("\n=== a pool with one half of its mapping ===")
# A pool with no public range translates nothing and a pool with no private
# range translates everything. Both are accepted by the file format, because
# the file format is a text file, and this is where they stop.
_cfg = config.Config()
_p = config.Pool("subs")
_p.inside = ["100.64.0.0/22"]
_sh = shell()
_sh.running_config = lambda: config.Config()
_cfg.pools["subs"] = _p
_rv, _text = driven(_sh.apply, _cfg, check_only=True)
says("half a pool", _text, "error: pool 'subs': inside and outside required",
     _rv)
print("  a pool with an inside and no outside is refused at check time")

print("\n=== a pool the dataplane itself would refuse ===")
# The device validates for real - block size, overlap, capacity, collision
# with a local address - and creates nothing doing it. What changed in #119's
# neighbourhood is that a check which CANNOT run is no longer silently a pass;
# this is the box for the check that runs and says no.
_cfg = config.Config()
_p = config.Pool("subs")
_p.inside = ["100.64.0.0/22"]
_p.outside = ["200.150.0.16/30"]
_cfg.pools["subs"] = _p
_sh = shell(dp=Dp({"bpa pool": (1, "block size 512 leaves no blocks")}))
_sh.running_config = lambda: config.Config()
_rv, _text = driven(_sh.apply, _cfg, check_only=True)
says("device check", _text, "error: pool 'subs' would be refused", _rv)
print("  the device's own verdict reaches the prompt, named by pool")

print("\n=== a port forward on a pool that is not being redefined ===")
# Checked separately BECAUSE the pool is not being rebuilt: a forward on a pool
# that is gets checked with it, as part of one definition, and there is no
# other way to check a forward against a pool the device has not got yet.
_running = config.Config()
_rp = config.Pool("subs")
_rp.inside = ["100.64.0.0/22"]
_rp.outside = ["200.150.0.16/30"]
_running.pools["subs"] = _rp
_target = config.Config()
_tp = config.Pool("subs")
_tp.inside = ["100.64.0.0/22"]
_tp.outside = ["200.150.0.16/30"]
_tp.forwards = [("tcp", 8080, "100.64.0.5", 80)]
_target.pools["subs"] = _tp
_sh = shell(dp=Dp({"bpa forward": (1, "port 8080 is outside the pool")}))
_sh.running_config = lambda: _running
_rv, _text = driven(_sh.apply, _target, check_only=True)
says("forward check", _text,
     "error: pool 'subs': forward tcp 8080 would be refused", _rv)
print("  a forward added to a pool that stays put is checked on its own")

print("\n=== an ssh setting nobody could log in through ===")
# THE refusal the whole check-only path exists for, reached through `apply`
# rather than at the prompt: `rollback` and `--load` never see the prompt, and
# hexarouting.service replays a file at every boot.
_sh = shell()
_sh.running_config = lambda: config.Config()
_cfg = config.Config()
_cfg.ssh = {"port": "99999"}
_rv, _text = driven(_sh.apply, _cfg, check_only=True)
says("ssh port", _text, "error: ssh port '99999': expected 1-65535", _rv)
print("  a port no daemon can bind is refused before it is written")

print("\n=== the pool writes, one message at a time ===")
# SIX BRANCHES, six ways a commit can leave the dataplane holding some of the
# change. Each names the pool, because a commit carrying several pools has to
# say which one was refused - "commit failed" sends the operator to read the
# whole candidate.
#
# `pool_remove` and `fwd_del` are driven with NOT_RUNNING rather than a plain
# refusal: both read `r.state != vppapi.REFUSED` as well, because removing
# something already gone is the outcome that was asked for.


def pool(name, inside="100.64.0.0/22", outside="200.150.0.16/30"):
    p = config.Pool(name)
    p.inside = [inside]
    p.outside = [outside]
    return p


def cfg(*pools):
    c = config.Config()
    for p in pools:
        c.pools[p.name] = p
    return c


_settings = pool("subs")
_settings.max_blocks = 4
_bypass = pool("subs")
_bypass.bypass_src = ["100.64.1.0/24"]
_limits = pool("subs")
_limits.max_sessions = 2000
_fwd_gone = pool("subs")
_fwd_had = pool("subs")
_fwd_had.forwards = [("tcp", 8080, "100.64.0.5", 80)]
_fwd_new = pool("subs")
_fwd_new.forwards = [("tcp", 8080, "100.64.0.5", 80)]

_writes = [
    ("removing a pool", "bpa_pool_add_del", vppapi.NOT_RUNNING,
     cfg(pool("old")), cfg(),
     f"error: removing pool 'old': {REASON}"),
    ("pool settings", "bpa_pool_settings_set", vppapi.REFUSED,
     cfg(pool("subs")), cfg(_settings),
     f"error: pool 'subs': settings: {REASON}"),
    ("a bypass list", "bpa_bypass_set", vppapi.REFUSED,
     cfg(pool("subs")), cfg(_bypass),
     f"error: pool 'subs': bypass source: {REASON}"),
    ("the per-subscriber bounds", "bpa_limits_set", vppapi.REFUSED,
     cfg(pool("subs")), cfg(_limits),
     f"error: pool 'subs': limits: {REASON}"),
    ("removing a forward", "bpa_forward_add_del", vppapi.NOT_RUNNING,
     cfg(_fwd_had), cfg(_fwd_gone),
     f"error: pool 'subs': removing forward tcp 8080: {REASON}"),
    ("adding a forward", "bpa_forward_add_del", vppapi.REFUSED,
     cfg(pool("subs")), cfg(_fwd_new),
     f"error: pool 'subs': forward tcp 8080 to 100.64.0.5 80: {REASON}"),
]
for label, message, state, running, target, line in _writes:
    _sh = shell(Api(refuse={message}, state=state))
    _sh.running_config = lambda r=running: r
    _rv, _text = driven(_sh.apply, target)
    says(label, _text, line, _rv)
print(f"  {len(_writes)} pool writes, each refused by name")

print("\n=== the box's own configuration, which the commit applies after the "
      "dataplane ===")
# THE MANAGEMENT PATH. An ssh change costs the operator their session if it is
# wrong, the accounts decide who can get back in at all, and the tuner's file
# is what the box reads at its next boot to find the card its management moved
# to. A commit that reported success while any of those was not written is a
# box nobody can reach.
_running = config.Config()
_target = config.Config()
_target.ssh = {"port": "2222"}
_sh = shell()
_sh.running_config = lambda: _running
_sh.sys = type("S", (), {
    "write_ssh": lambda self, s: (False, "sshd_config.d: read-only file "
                                         "system"),
})()
_rv, _text = driven(_sh.apply, _target)
says("ssh write", _text,
     "error: ssh: sshd_config.d: read-only file system", _rv)

_target = config.Config()
_target.snmp = {"location": "rack 4"}
_sh = shell()
_sh.running_config = lambda: config.Config()
_sh.snmp = type("N", (), {
    "apply": lambda self, s, c, u: (False, "snmpd did not restart"),
})()
_rv, _text = driven(_sh.apply, _target)
says("snmp write", _text, "error: snmp: snmpd did not restart", _rv)

_target = config.Config()
_target.users = {"noc": hexash.User("noc")}
_sh = shell()
_sh.running_config = lambda: config.Config()
_sh.sys = type("S", (), {
    "apply_users": lambda self, want, have: (False, ["noc: useradd failed"]),
})()
_rv, _text = driven(_sh.apply, _target)
says("accounts", _text, "error: accounts were not fully applied", _rv)

# The tuner's file, refused by the filesystem rather than by a stub: TUNE_ENV
# is pointed at a path under a FILE, so the directory the write needs cannot be
# made. Driving it through the real write_tune_key is the point - a stub
# returning False would assert that `apply` reads a boolean, not that the one
# refusal an unwritable box produces reaches the operator.
_notdir = os.path.join(_tmp, "not-a-directory")
open(_notdir, "w").close()
_target = config.Config()
_target.dataplane_mgmt = {"name": "ens18", "address": "10.0.0.2/24",
                          "mac": "02:00:00:00:00:09"}
_sh = shell()
_sh.running_config = lambda: config.Config()
_sh.TUNE_ENV = os.path.join(_notdir, "tune.env")
_rv, _text = driven(_sh.apply, _target)
check(any(l.startswith("error: dataplane management: ") and
          "tune.env" in l for l in _text.splitlines()),
      f"an unwritable tuner file must be named: {_text!r}")
check(_rv is False, f"and the commit must stop: {_rv!r}")
print("  ssh, snmp, the accounts and the tuner's file, each refused by name")

print("\n=== an MTU the dataplane took and an MTU it did not ===")
# WHY THE READ-BACK IS AN ASSERTION AND NOT A COURTESY. retval 0 means the
# message was accepted, not that the driver took the value. An MTU asked for
# and not applied is an OSPF adjacency stuck in Exchange with nothing in the
# log saying why, which is the failure docs/routing.md records.
_running = config.Config()
_running.addresses["pg0"] = ["10.0.0.1/24"]
_target = config.Config()
_target.addresses["pg0"] = ["10.0.0.1/24"]
_target.mtus["pg0"] = 1500
_sh = shell(Api(have={"pg0": 1}, refuse={"sw_interface_set_mtu"}))
_sh.running_config = lambda: _running
_rv, _text = driven(_sh.apply, _target)
says("mtu refused", _text, f"error: interface pg0: mtu 1500: {REASON}", _rv)

# The same commit with a dataplane that ACCEPTS the message and reports another
# MTU afterwards, which is the box the read-back was written for.
_sh = shell(Api(have={"pg0": 1}),
            dp=Dp({"show interface": (0, "Name  Idx State MTU\n"
                                         "pg0   1   up    9000/0/0/0\n")}))
_sh.running_config = lambda: _running
_rv, _text = driven(_sh.apply, _target)
says("mtu not taken", _text,
     "error: interface pg0: the dataplane did not take mtu 1500", _rv)
print("  a refused MTU and an MTU that was accepted and not applied")

print("\n=== uRPF asked for and not applied, and uRPF taken off and still on "
      "===")
# The same read-back one interface over, and the one where believing the box
# costs more: an operator who thinks spoofed sources are being dropped and is
# translating them instead has no counter that says so. Two directions, two
# call sites, and they are not the same box - one is a filter that was never
# turned on, the other a filter that was never turned off.
_running = config.Config()
_target = config.Config()
_target.urpf["pg0"] = "strict"
_sh = shell(Api(have={"pg0": 1}),
            dp=Dp({"show bpa urpf": (0, "pg0 loose\n")}))
_sh.running_config = lambda: _running
_rv, _text = driven(_sh.apply, _target)
says("urpf not taken", _text,
     "error: interface pg0: the dataplane did not take urpf strict", _rv)

_running = config.Config()
_running.urpf["pg0"] = "strict"
_target = config.Config()
_sh = shell(Api(have={"pg0": 1}),
            dp=Dp({"show bpa urpf": (0, "pg0 strict\n")}))
_sh.running_config = lambda: _running
_rv, _text = driven(_sh.apply, _target)
says("urpf still on", _text,
     "error: interface pg0: uRPF is still on after being removed", _rv)
print("  the dataplane disagreeing in either direction refuses the commit")

print("\n=== routing, and a box with nothing to apply it with ===")
# TWO BOXES, not one. FRR answering and refusing the configuration is a
# mistake in what was typed; FRR not answering at all is a daemon that is down,
# and blaming the configuration for it sends somebody to debug the wrong thing.
# Both are refused, and they are refused with different words.
_target = config.Config()
_target.routing = ["router ospf", " network 10.0.0.0/24 area 0"]
_sh = shell(frr=frr_stub(available=True, reload_rc=1,
                         reload_out="line 2: unknown command"))
_sh.running_config = lambda: config.Config()
_rv, _text = driven(_sh.apply, _target)
says("reload failed", _text, "error: routing reload failed", _rv)

_sh = shell(frr=frr_stub(available=False))
_sh.running_config = lambda: config.Config()
_rv, _text = driven(_sh.apply, _target)
says("frr silent, applying", _text,
     "error: routing is configured and FRR is not answering; check "
     "'systemctl status frr'", _rv)

# And the same box asked to CHECK rather than to apply. It is a separate call
# site in the product and a separate box here: a commit that validates against
# a daemon that is not there would report a configuration as good and then fail
# applying it.
_sh = shell(frr=frr_stub(available=False))
_sh.running_config = lambda: config.Config()
_rv, _text = driven(_sh.apply, _target, check_only=True)
says("frr silent, checking", _text,
     "error: routing is configured and FRR is not answering; check "
     "'systemctl status frr'", _rv)
print("  FRR refusing, FRR absent at commit, and FRR absent at check")

print("\n=== a read-only account reaching for configuration mode ===")
# The gate on `configure` is convenience rather than authorization - `apply`
# carries the real one - but it is the sentence a read-only operator actually
# meets, and it has to say which membership would change the answer.
_sh = shell(caps=set())
_rv, _text = driven(_sh.do_operational, "configure")
says("read-only", _text,
     "error: configure: permission denied for a read-only account", _rv,
     expect=True)
check(any(hexash.GROUP_ADMIN in l for l in _text.splitlines()),
      f"and it must name the group that would allow it: {_text!r}")
print("  refused, and told which membership allows everything")


# --------------------------------------------------------------- the reader
#
# Everything above this line is boxes. Everything below is the check that asks
# whether the boxes, and every other suite that drives this VTY, have between
# them reached the refusals the product can print.

def refusal_sites(path):
    """
    Every `self.err(...)` and `self.err_dataplane(...)` in the VTY, by line.

    Parsed rather than grepped because a refusal spanning several lines is one
    call site and `grep -c` would count its first row only - and because the
    line an AST reports for a Call is the line CPython reports for the frame
    that made it, which is what lets the two halves meet.
    """
    tree = ast.parse(open(path).read())
    inside = set()
    for node in ast.walk(tree):
        # `err_dataplane` calls `err` itself. That inner call is plumbing and
        # not a refusal of its own: counting it would add a site no operator
        # can reach except through one of the 27 above it.
        if isinstance(node, ast.FunctionDef) and node.name == "err_dataplane":
            inside.update(range(node.lineno, (node.end_lineno or
                                              node.lineno) + 1))
    sites = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("err", "err_dataplane")
                and node.lineno not in inside):
            sites[node.lineno] = node.func.attr
    return sites


def vty_suites(listfile):
    """
    The suites that drive this VTY, read from the list `make test` runs.

    Derived rather than kept. A suite that puts `src/hexash` on its path is a
    suite that imports the VTY's own modules, and that is the property being
    asked about - a second list here would have to be updated by the same
    person who forgot to add the box.
    """
    found = []
    for raw in open(listfile):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        words = raw.split()
        if words[0] != "python3":
            continue
        path = os.path.join(REPO, words[-1])
        if not os.path.exists(path):
            continue
        # THIS FILE IS IN THAT LIST AND MATCHES ITS OWN TEST. Without this
        # line the reader runs itself, and every copy of it runs itself again:
        # measured once, as a fork bomb inside `make test` that had to be
        # killed. Excluding it costs nothing - the boxes above ran in this
        # process and were recorded by the same hook the children use.
        if os.path.abspath(path) == os.path.abspath(__file__):
            continue
        if re.search(r'src["\'/,\s]+.{0,3}hexash',
                     open(path, errors="replace").read()):
            found.append(path)
    return found


print("\n=== the suites that drive this VTY, run with the refusals recorded "
      "===")
SUITES = vty_suites(os.path.join(REPO, "test", "suites.list"))
if len(SUITES) < 10:
    bad(f"only {len(SUITES)} suite(s) were found to drive the VTY, so every "
        f"count below is about nothing")
else:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [_tmp] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["HEXASH_REFUSAL_LOG"] = LOG
    # The exit status is REPORTED and not asserted on. Whether another suite
    # passes is that suite's own business, and repeating its verdict here would
    # be a second place to fix. What a crashed child costs is coverage, and the
    # floor below is what notices that.
    red = []
    for path in SUITES:
        p = subprocess.run([sys.executable, path], cwd=REPO, env=env,
                           capture_output=True, text=True)
        if p.returncode:
            red.append(os.path.basename(path))
    ok(f"{len(SUITES)} suites run"
       + (f", {len(red)} of them red: {' '.join(red)}" if red else
          ", all green"))

print("\n=== every refusal the VTY can print, against the ones a box has "
      "made it print ===")
# THE READER. The call sites come out of the product, so a refusal added to
# hexash.py with no box added anywhere goes red here on the next run and there
# is no list to keep up to date. The needle is the LINE and not the message:
# a reader that looked for the message would be satisfied by a mutation to the
# message, because both would move together - the trap the shell reader next
# door in test/test_selftest.sh names in its own comments.
#
# THE FLOOR IS THE HIGHEST THE UNRENDERED MAY BE, in the idiom
# test/test_off_run_coverage.sh already uses. Not an allowlist: an allowlist is
# read once and grown forever, and the whole failure this file exists for is a
# refusal nobody re-examines. Every refusal that gains a box lowers this
# number, and it may only go down.
UNRENDERED_AT_MOST = 70

SITES = refusal_sites(VTY)
rendered = set()
for line in open(LOG):
    for n in line.strip().split(","):
        if n:
            rendered.add(int(n))

if len(SITES) < 150:
    bad(f"only {len(SITES)} refusal(s) were parsed out of {VTY}, which is far "
        f"too few to be the whole VTY - the parse is what is broken, not the "
        f"coverage")
elif not rendered:
    bad("no refusal was recorded at all, so nothing below was measured: the "
        "hook did not reach the suites")
else:
    ok(f"{len(SITES)} refusals in the VTY "
       f"({sum(1 for v in SITES.values() if v == 'err')} through err, "
       f"{sum(1 for v in SITES.values() if v == 'err_dataplane')} through "
       f"err_dataplane)")
    source = open(VTY).read().splitlines()
    unseen = sorted(n for n in SITES if n not in rendered)
    ok(f"{len(SITES) - len(unseen)} of them have been printed by a box here "
       f"or in one of the {len(SUITES)} suites above")
    if len(unseen) > UNRENDERED_AT_MOST:
        bad(f"{len(unseen)} refusal(s) have never been printed by anything, "
            f"above the floor of {UNRENDERED_AT_MOST}. A refusal nobody has "
            f"watched fire is one this repository cannot tell from a refusal "
            f"that does not fire, and what it lets through is the "
            f"configuration it was written to stop - arrange a box, do not "
            f"add an exception:\n"
            + "\n".join(f"    hexash.py:{n}  {source[n - 1].strip()[:88]}"
                        for n in unseen))
    elif len(unseen) < UNRENDERED_AT_MOST:
        bad(f"the floor says {UNRENDERED_AT_MOST} and only {len(unseen)} "
            f"refusal(s) are unrendered: lower it, or it stops holding "
            f"anything")
    else:
        ok(f"and the {len(unseen)} that have not are named, at the floor of "
           f"{UNRENDERED_AT_MOST}")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
