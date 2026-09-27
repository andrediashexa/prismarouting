#!/usr/bin/env python3
"""
Every refusal the VTY can print, against the ones something has watched print -
and every row it printed doing it, against the widest value that can reach it.

WHAT A REFUSAL IS. `src/prismash/prismash.py` says no to the operator 180 times,
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

  * The suites it runs are the ones that put `src/prismash` on their path, read
    from test/suites.list. A suite driving the VTY some other way - a shell
    suite running prismash.py as a program - would not be counted. None does
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
    measured by name in test/test_prismash_perms.py and test/test_dataplane_if.py
    instead.

AND THE SECOND READER, at the end of the file, which is about WIDTH rather
than about coverage and is here because this is where the driving is. #359 and
#362 put a rule on the source - no LITERAL row is wider than
`config.CONSOLE_COLUMNS` - and both printed, on every run, the half they did
not hold: 296 rows carry an interpolated value, and the width of those is not a
property of the source. `neighbour {peer} does not keep what it received` is 48
columns plus the peer, which fits an IPv4 address and does not fit a full-length
IPv6 one. Deciding that needs the site DRIVEN with the widest value that can
reach it (#363), and the boxes above already reach 185 of the VTY's 185 verdict
sites and 168 of its 238 print sites. A second harness would have to build its
own way of reaching every one of them, which is nine tenths of what this file
is; so the rows are recorded by the same hook, and the reader at the end says,
per site, whether the row is bounded and fits, or carries something with no
widest and puts it where a fold costs nothing. What it cannot reach is printed
with the rest.

THE COUNT IS A FLOOR, and there is no allowlist. An allowlist is the thing the
next person grows: a refusal lands in it once and nobody reads it again. So the
unrendered ones are NAMED, the number they may not exceed is written down, and
lowering it is the only way to change it - the idiom test_off_run_coverage.sh
already uses for labs with no off run.
"""
import ast
import contextlib
import io
import ipaddress
import os
import re
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VTY = os.path.join(REPO, "src", "prismash", "prismash.py")

# ------------------------------------------------------------------ the hook
#
# ONE COPY, used twice. This text is written to a temporary directory as
# `sitecustomize.py`, which Python imports before any suite's own code runs, so
# every child process records what the VTY refused. The same text is then
# exec'd in THIS process, before prismash is imported, so the boxes below are
# recorded by the identical mechanism. Two hand-kept copies of it would be two
# things that can disagree, and the one that drifted would report a refusal as
# covered that nothing had driven.
HOOK = r'''
import builtins
import os
import sys

LOG = os.environ.get("PRISMASH_REFUSAL_LOG")
VTY_PATH = os.environ.get("PRISMASH_VTY")
if VTY_PATH:
    VTY_PATH = os.path.realpath(VTY_PATH)


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
        if name == "prismash":
            _record(sys.modules.get("prismash", module))
        return module

    builtins.__import__ = _hooked

# ---------------------------------------------------------------- the rows
#
# THE SAME TRICK ONE LAYER DOWN, and it is here rather than in a suite of its
# own because the boxes above are what drive the sites: a second harness would
# have to build its own way of reaching every one of them, and reaching them
# is nine tenths of the work this file already does. What `err` records is
# WHICH refusal fired; what this records is WHAT REACHED THE CONSOLE - the row
# as the operator sees it, with the value already interpolated into it. The
# width reader at the end of this file is what reads the two together.
#
# `print` and not `sys.stdout.write`: every row this VTY prints goes through
# `print`, measured, and wrapping the stream instead would see rows cut into
# pieces by the writes that compose them and could not say which call made
# one.
#
# Rows written to a `file=` are not the console - `[device]` and `[api]`
# tracing go to stderr - so they are left alone.
_rows = os.environ.get("PRISMASH_ROW_LOG")
if _rows and VTY_PATH:
    _real_print = builtins.print

    def _print(*args, sep=" ", end="\n", file=None, flush=False):
        if file is None:
            frame = sys._getframe(1)
            seen = []
            while frame is not None:
                if os.path.realpath(frame.f_code.co_filename) == VTY_PATH:
                    seen.append(str(frame.f_lineno))
                frame = frame.f_back
            if seen:
                text = sep.join(str(a) for a in args)
                with open(_rows, "a") as out:
                    out.write(",".join(seen) + "\t"
                              + text.replace("\\", "\\\\").replace("\n", "\\n")
                              + "\n")
        return _real_print(*args, sep=sep, end=end, file=file, flush=flush)

    builtins.print = _print
'''

_tmp = tempfile.mkdtemp(prefix="prismash-refusals-")
LOG = os.path.join(_tmp, "rendered")
ROWS = os.path.join(_tmp, "rows")
open(LOG, "w").close()
open(ROWS, "w").close()
os.environ["PRISMASH_REFUSAL_LOG"] = LOG
os.environ["PRISMASH_ROW_LOG"] = ROWS
os.environ["PRISMASH_VTY"] = VTY
with open(os.path.join(_tmp, "sitecustomize.py"), "w") as _f:
    _f.write(HOOK)
exec(compile(HOOK, "<prismash-refusal-hook>", "exec"), {"__name__": "_hook"})

sys.path.insert(0, os.path.join(REPO, "src", "prismash"))
import config      # noqa: E402
import prismash      # noqa: E402
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

    def __init__(self, have=None, refuse=(), state=vppapi.REFUSED,
                 rows=None, refuse_dump=()):
        self.have = dict(have or {})
        self.refuse = set(refuse)
        self.state = state
        # A DUMP IS REFUSED BY ITS OWN SET, not by `refuse`. The IPFIX export
        # reads the exporter back before it writes one, so that path has a
        # dump and a call in it and a single set would not say which of the
        # two is being refused - and the two refusals it produces are
        # different sentences on purpose.
        self.rows = dict(rows or {})
        self.refuse_dump = set(refuse_dump)
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
        if message in self.refuse_dump:
            return vppapi.Reply(self.state, retval=-7, reason=REASON)
        return vppapi.Reply(vppapi.OK, fields={"rows": self.rows.get(message,
                                                                    [])})


class Dp(prismash.Vpp):
    """
    The CLI side of the dataplane, answering nothing.

    Derived from prismash.Vpp rather than free-standing for the reason
    test_prismash_apply.py records: a stub that enumerates the product's methods
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


def frr_stub(available=False, reload_rc=0, reload_out="", shows=None):
    # `shows` answers one command and leaves every other one refused, which is
    # what a daemon that is down looks like: vtysh exits non-zero with nothing
    # on stdout. A stub that answered everything would make "not answering"
    # unreachable, and that is half the boxes below.
    answers = dict(shows or {})

    def show(self, cmd):
        for prefix, answer in answers.items():
            if cmd.startswith(prefix):
                return answer
        return (1, "")

    return type("F", (prismash.Frr,), {
        "available": lambda self: available,
        "show": show,
        "reload": lambda self, lines, check_only=False: (reload_rc,
                                                         reload_out),
    })()


def shell(api=None, dp=None, frr=None, caps=None):
    """
    A Shell with its capability stated outright, for the reason
    test_prismash_apply.py states it: Shell() reads the invoking account's real
    group membership, so left alone every case here is refused before it
    reaches the refusal it is about.
    """
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT} if caps is None else caps
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

# Except one, and it is not a refusal: UNPAIRING an interface that is gone is
# the outcome asked for. A load that dropped a sub-interface met its pair
# already removed with its parent's, refused, and ended the commit before FRR
# took the file's routes (#474).
rv, text = driven(lambda sh: sh.set_lcp_pair(MISSING, "", False),
                  shell(Api(have={"pg0": 1})))
check(rv is True and "error" not in text,
      f"unpairing an interface that is gone refused the commit: {text!r}")

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
    # An LACP bundle's five dataplane steps (#455).
    ("create bundle", "bond_create2",
     lambda sh: sh.create_bundle("ae0", config.new_bundle()),
     f"error: could not create ae0: {REASON}"),
    ("name bundle", "sw_interface_set_interface_name",
     lambda sh: sh.create_bundle("ae0", config.new_bundle()),
     f"error: could not name ae0: {REASON}"),
    ("add member", "bond_add_member",
     lambda sh: sh.add_member("ae0", "pg0", config.new_bundle()),
     f"error: could not add pg0 to its bundle: {REASON}"),
    ("detach member", "bond_detach_member",
     lambda sh: sh.detach_member("pg0"),
     f"error: could not detach pg0: {REASON}"),
    ("delete bundle", "bond_delete",
     lambda sh: sh.delete_bundle("ae0"),
     f"error: could not delete ae0: {REASON}"),
]
for label, message, verb, line in _refused:
    api = Api(have={"pg0": 1, "loop3": 7, "pg0.10": 9, "ae0": 11},
              refuse={message})
    rv, text = driven(verb, shell(api))
    says(label, text, line, rv)
print(f"  {len(_refused)} verbs, each naming the dataplane's own refusal")

print("\n=== a bundle member the dataplane does not have (#455) ===")
_rv, _text = driven(lambda: shell(Api(have={"ae0": 11})).add_member(
    "ae0", "pg9", config.new_bundle()))
says("member", _text, "error: no interface named pg9", _rv)
print("  pg9 refused as a member, by name")

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

print("\n=== a block-stride on a deterministic pool, by all three routes ===")
# #375. The setting was accepted, `show bpa pool` printed the layout it
# promises, and the allocator never read it: a deterministic subscriber's
# extension comes out of the reserve, which is an index space no stride steps
# along. The dataplane refuses it now, and this asks first - before the pools
# are rebuilt - because one of the three routes never reaches the device check
# at all.
#
# THREE BOXES AND NOT ONE, because the three are different deltas and only one
# of them is covered by anything else. A pool being created and a pool being
# rebuilt carry the stride on the creation message and are handed to the
# device's own `check`; a stride added to a pool that STAYS PUT is a
# settings_set, which no check reaches, so without the loop it would fail at
# the write with the candidate half applied.
#
# AND A FOURTH BOX, below the control, for `rollback`: it loads a revision and
# applies nothing, so the refusal is the commit after it, on the delta the
# ordinary box has - which is the third of the three.
_STRIDE_ROW = ("error: block-stride applies to dynamic pools only, not to "
               "pool subs")


def _strided(mode="deterministic", stride=3, name="subs"):
    p = config.Pool(name)
    p.inside = ["100.64.0.0/22"]
    p.outside = ["200.150.0.16/30"]
    p.mode = mode
    p.block_stride = stride
    return p


# a pool being created
_cfg = config.Config()
_cfg.pools["subs"] = _strided()
_sh = shell()
_sh.running_config = lambda: config.Config()
_rv, _text = driven(_sh.apply, _cfg, check_only=True)
says("stride on a new pool", _text, _STRIDE_ROW, _rv)
says("stride on a new pool: the mode row", _text, "    mode deterministic")
says("stride on a new pool: the stride row", _text, "    block-stride 3")

# THE CASE THE ISSUE IS NAMED FOR: a pool that was dynamic, carrying a stride
# that worked, moved to deterministic and keeping it. `mode` is outside
# Pool.lines(in_place=False), so this is a pool_replace. An operator doing
# this is losing the stride, and the whole argument for refusing rather than
# accepting-and-ignoring is that they are told so here rather than finding out
# from a layout the box stopped producing.
_running = config.Config()
_running.pools["subs"] = _strided(mode="dynamic")
_target = config.Config()
_target.pools["subs"] = _strided()
_sh = shell()
_sh.running_config = lambda: _running
_rv, _text = driven(_sh.apply, _target, check_only=True)
check([p.name for p in _running.delta_to(_target)["pool_replace"]] == ["subs"],
      "the mode-change box is not a pool_replace delta, so it is not "
      "measuring the route it was written for")
says("stride kept across a move to deterministic", _text, _STRIDE_ROW, _rv)

# a pool already on the box, taking the stride in place: this is the delta
# that never meets the device's own check
_running = config.Config()
_running.pools["subs"] = _strided(stride=0)
_target = config.Config()
_target.pools["subs"] = _strided()
_sh = shell()
_sh.running_config = lambda: _running
_rv, _text = driven(_sh.apply, _target, check_only=True)
check(_running.delta_to(_target)["settings_set"].keys() == {"subs"},
      "the in-place box is not a settings_set delta, so it is not measuring "
      "the route it was written for")
says("stride added in place", _text, _STRIDE_ROW, _rv)

# THE CONTROL, and the section is worth nothing without it: the same stride on
# a dynamic pool is a supported configuration and MUST still commit. Without
# this the three boxes above pass on a VTY that refuses every stride there is.
_cfg = config.Config()
_cfg.pools["subs"] = _strided(mode="dynamic")
_sh = shell()
_sh.running_config = lambda: config.Config()
_rv, _text = driven(_sh.apply, _cfg, check_only=True)
check("block-stride" not in _text,
      f"a stride on a dynamic pool was argued with: {_text!r}")
check(_rv is True, f"a stride on a dynamic pool was refused: {_rv!r}")

# THE ROLLBACK ROUTE, end to end. `rollback N` loads a revision into the
# candidate and applies nothing - "load complete" is all it says, whatever the
# revision carries - so the refusal is the COMMIT after it, and this box is
# the ordinary one: a box that booted through --load's drop is running the
# pool WITHOUT the stride, so a revision saved before the refusal existed
# differs from it by one in-place statement, and the delta that commit
# refuses is the settings_set above, not a new pool. Asserted as the operator
# would see it: the rollback says nothing about the stride, the commit prints
# the row and the two statements, nothing reaches the dataplane and no
# revision is pushed - a commit refused at the check is a commit that did not
# happen, and a store that recorded it would offer `rollback 1` back to the
# very pair the box refuses.


class _Store:
    """A rollback store holding one revision, remembering every push."""

    def __init__(self, text):
        self.text = text
        self.pushed = []

    def load(self, n):
        return self.text

    def push(self, text, comment=""):
        self.pushed.append(comment)


_running = config.Config()
_running.pools["subs"] = _strided(stride=0)
_revision = config.Config()
_revision.pools["subs"] = _strided()
_sh = shell()
_sh.running_config = lambda: _running
_sh.rb = _Store(_revision.text())
_rv, _text = driven(_sh.do_rollback, ["1"])
says("rollback loads the revision", _text, "load complete (revision 1)")
check("block-stride" not in _text,
      f"rollback argued with the revision, and it applies nothing: {_text!r}")
check(_sh.candidate.pools["subs"].block_stride == 3,
      "the revision's stride did not reach the candidate, so the commit "
      "below has nothing to refuse")
check(_running.delta_to(_sh.candidate)["settings_set"].keys() == {"subs"},
      "the box after the boot drop does not see the revision as an in-place "
      "delta, so this is not measuring the route it was written for")
_rv, _text = driven(_sh.do_commit, [])
says("the commit after the rollback", _text, _STRIDE_ROW)
says("the commit after the rollback: the mode row", _text,
     "    mode deterministic")
says("the commit after the rollback: the stride row", _text,
     "    block-stride 3")
_pool_msgs = [m for m, _ in _sh.api.calls if m.startswith("bpa_pool")]
check(_pool_msgs == [],
      f"the refused commit reached the dataplane: {_pool_msgs}")
check(_sh.rb.pushed == [],
      f"the refused commit was recorded as a revision: {_sh.rb.pushed}")
print("  refused on a pool created, on a pool rebuilt, on one that stays "
      "put, and at the commit after a rollback; accepted in dynamic mode")

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
# prisma-routing.service replays a file at every boot.
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
# THE BOX'S OWN ACCESS. An ssh change costs the operator their session if it
# is wrong, and the accounts decide who can get back in at all. A commit that
# reported success while either was not written is a box nobody can reach.
# The tuner's file was driven here too, refused by the filesystem, until #385
# took out the management record the commit wrote into it; the card's own
# write is refused the same way and test/test_dataplane_if.py drives that.
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
_target.users = {"noc": prismash.User("noc")}
_sh = shell()
_sh.running_config = lambda: config.Config()
_sh.sys = type("S", (), {
    "apply_users": lambda self, want, have: (False, ["noc: useradd failed"]),
})()
_rv, _text = driven(_sh.apply, _target)
says("accounts", _text, "error: accounts were not fully applied", _rv)

print("  ssh, snmp and the accounts, each refused by name")

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
# The VERDICT row, which is all of the refusal that `err` will not fold, and
# the row an operator reads first and greps for. The advice under it is prose
# now: FRR_SILENT_REFUSED is where both halves live, and this row is the one
# whose width the product guarantees (#362).
says("frr silent, applying", _text,
     "error: routing is configured and FRR is not answering", _rv)

# And the same box asked to CHECK rather than to apply. It is a separate call
# site in the product and a separate box here: a commit that validates against
# a daemon that is not there would report a configuration as good and then fail
# applying it.
_sh = shell(frr=frr_stub(available=False))
_sh.running_config = lambda: config.Config()
_rv, _text = driven(_sh.apply, _target, check_only=True)
says("frr silent, checking", _text,
     "error: routing is configured and FRR is not answering", _rv)
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
check(any(prismash.GROUP_ADMIN in l for l in _text.splitlines()),
      f"and it must name the group that would allow it: {_text!r}")
print("  refused, and told which membership allows everything")

print("\n=== the CGNAT globals, sent one setting at a time ===")
# NOT REACHED BY TYPING. `do_config` stages a global into the candidate and
# `apply_global` is what sends it, so `--load`, `rollback` and
# prisma-routing.service reach these without anybody at the prompt. A global that
# applies nothing must never do it quietly, which is #273 - and the commit
# before it said "commit complete" for a setting the box does not have.
_globals = [
    ("a timeout this box has no field for", Api(),
     "cgnat timeout nosuch 30",
     "error: cgnat timeout nosuch: no such timeout"),
    ("a timeout the dataplane refused", Api(refuse={"bpa_global_set"}),
     "cgnat timeout udp 300",
     f"error: cgnat timeout udp 300: {REASON}"),
    ("a block hold-down the dataplane refused",
     Api(refuse={"bpa_global_set"}), "cgnat block-holddown 4",
     f"error: cgnat block-holddown 4: {REASON}"),
    # Half a session-table statement. The two numbers are allocated together
    # and the message carries both, so one of them is not a smaller change -
    # it is a table sized against a number nobody gave.
    ("a session table with one of its two numbers", Api(),
     "cgnat session-table buckets 64",
     "error: usage: session-table buckets <n> memory <MB>"),
    ("a session table the dataplane refused",
     Api(refuse={"bpa_global_set"}), "cgnat session-table buckets 64 memory 8",
     f"error: cgnat session-table: {REASON}"),
]
for _label, _api, _line, _want in _globals:
    _rv, _text = driven(shell(_api).apply_global, _line)
    says(_label, _text, _want)
print(f"  {len(_globals)} globals, each refused rather than applied to "
      f"nothing")

print("\n=== the IPFIX export, which in dynamic mode IS the connection "
      "record ===")
# WHY THESE ARE WORTH BOXES AT ALL. `bpa logging enable` over vppctl exited 0
# whether or not the dataplane took it, and on every shipped box UDP export
# was refused because nothing had ever configured VPP's exporter (#209). What
# the operator saw was "commit complete" and a box exporting nothing, which in
# dynamic mode is the Marco Civil record missing. Every refusal here is one of
# the ways that silence was broken up.
#
# The exporter row is the one the dataplane would report: `apply_logging_udp`
# sends back everything the statement does not name, so a row has to be there
# for the branches past the read-back to be reached at all.
_EXPORTER = {"src_address": "10.0.0.1", "collector_port": 4739, "vrf_id": 0,
             "path_mtu": 1400, "template_interval": 300, "udp_checksum": False}
_logging = [
    ("a TCP collector port that is not one", Api(), "apply_logging",
     "cgnat logging tcp 10.0.0.5 port 99999",
     "error: cgnat logging tcp: port 99999 is not one"),
    # The verdict names no word on purpose: this branch is reached both by a
    # spelling the box does not have and by `cgnat logging udp` with nothing
    # after it, where `udp` IS a spelling. The shapes under it are built from
    # CGNAT_CONFIG and are asserted separately, below (#362).
    ("a logging statement in none of the shapes", Api(), "apply_logging",
     "cgnat logging sideways",
     "error: cgnat logging: not a shape this box takes"),
    ("a logging statement that stops halfway", Api(), "apply_logging",
     "cgnat logging udp",
     "error: cgnat logging: not a shape this box takes"),
    ("the export turned on and refused", Api(refuse={"bpa_logging_set"}),
     "apply_logging", "cgnat logging enable",
     f"error: cgnat logging enable: {REASON}"),
    # The read-back refused, which is not the same as the write refused: this
    # one changed nothing at all, and saying "refused" without saying which
    # half would send somebody to look at the collector.
    ("the exporter that could not be read",
     Api(refuse_dump={"ipfix_exporter_dump"}), "apply_logging_udp",
     "cgnat logging udp 10.0.0.5 src 10.0.0.1",
     f"error: cgnat logging udp 10.0.0.5 src 10.0.0.1: cannot read the IPFIX "
     f"exporter: {REASON}"),
    ("a UDP collector port that is not one",
     Api(rows={"ipfix_exporter_dump": [_EXPORTER]}), "apply_logging_udp",
     "cgnat logging udp 10.0.0.5 src 10.0.0.1 port 99999",
     "error: cgnat logging udp: port 99999 is not one"),
    ("the exporter the dataplane would not take",
     Api(refuse={"set_ipfix_exporter"},
         rows={"ipfix_exporter_dump": [_EXPORTER]}), "apply_logging_udp",
     "cgnat logging udp 10.0.0.5 src 10.0.0.1",
     f"error: cgnat logging udp 10.0.0.5 src 10.0.0.1: {REASON}"),
]
for _label, _api, _method, _line, _want in _logging:
    _rv, _text = driven(getattr(shell(_api), _method), _line.split())
    says(_label, _text, _want)
print(f"  {len(_logging)} ways the export does not arrive, each said out loud")

# The shapes under that verdict, against the table they are built from. The
# usage line they replaced was a second copy of the grammar and had drifted
# from `?` - it offered `nat cgnat logging udp disable`, which `?` did not
# (#362). Asserted against CGNAT_CONFIG rather than against a list here, so a
# spelling added to the tree appears on this screen with nothing to keep in
# step, and a list written out here would be the third copy.
_rv, _text = driven(shell().apply_logging, "cgnat logging sideways".split())
for _word, (_what, _hint) in prismash.CGNAT_CONFIG["logging"][1].items():
    # In the words typed under `cgnat`, where the statement is (#446).
    _row = (f"    logging {_word}"
            + (f" {_hint}" if _hint else ""))
    check(_row in _text.splitlines(),
          f"the refusal offers {_word!r} as the tree spells it: {_text!r}")
print(f"  and the {len(prismash.CGNAT_CONFIG['logging'][1])} shapes it does "
      f"take, each out of the table `?` answers from")


def cfg_shell(**kw):
    """
    A Shell already in configuration mode, with an empty candidate.

    The grammar refusals below are reached by typing at the prompt, and the
    prompt they are typed at has a candidate to edit. A Shell left alone has
    `candidate` None, and every one of them would be refused for that instead
    of for the words - which is the wrong box, passing.
    """
    sh = shell(**kw)
    sh.candidate = config.Config()
    sh.mode = "config"
    return sh


def pooled(name, **fields):
    p = config.Pool(name)
    p.inside = ["100.64.0.0/22"]
    p.outside = ["200.150.0.16/30"]
    for k, v in fields.items():
        setattr(p, k, v)
    return p


print("\n=== a commit that would move subscribers, and the ones that cannot "
      "run at all ===")
# THE REMAP REFUSAL IS THE ONE WITH SUBSCRIBERS BEHIND IT. Changing a pool's
# block size re-computes which public address and port block every subscriber
# on it gets, and the dataplane says how many - so `commit` stops and makes
# the operator type the word. A commit that went ahead would land the change
# and disconnect them, at whatever hour it was typed.
_running = cfg(pooled("subs"))
_sh = shell(dp=Dp({"bpa pool": (0, "remap: 1200 subscribers get a "
                                   "different public address\n")}))
_sh.running_config = lambda: _running
_sh.candidate = cfg(pooled("subs", block_size="1024"))
_rv, _text = driven(_sh.do_commit, [])
says("re-mapping", _text,
     "error: this change re-maps subscribers that already have mappings")
check(any("commit remap" in l for l in _text.splitlines()),
      f"and it must say the word that would allow it: {_text!r}")

# AND THE ROWS UNDER IT FIT THE CONSOLE (#391). They are the plugin's own
# sentences with the `remap:` tag taken off, and until #391 the VTY printed
# each as it came: the one saying every session on the pool ends is 178
# columns with its indent, on a console of 79, so the terminal broke it
# wherever its width fell. The fake dataplane here answers with bpa_cli.c's
# format strings read from the source - a sentence the plugin rewrites is
# then measured, not the one this file remembers - with the widest members in
# their holes: an IPv6 address exploded, which is what the reader at the end
# of this file bounds an address at, a ten-digit count, and a five-digit port
# at each end of each range. Every row printed has to fit, and every word of
# every sentence has to come out whole: an address with its port range is one
# token, and a fold inside it would leave nothing to grep for.


def plugin_remap_rows():
    """bpa_cli.c's `remap:` format strings, joined across C's adjacent
    string literals, each with the widest members in its holes."""
    src = open(os.path.join(REPO, "src", "plugins", "bpa", "bpa_cli.c")).read()
    lit = r'"(?:[^"\\]|\\.)*"'
    v6 = ipaddress.IPv6Address(1).exploded
    rows = []
    for m in re.finditer(r'"remap:(?:[^"\\]|\\.)*"(?:\s*' + lit + ")*", src):
        text = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(0)))

        def fill(hole):
            if hole.group(0) == "%U":
                return v6
            # A port is the hole after `:` or `-`; the counts are the rest.
            return "65535" if text[hole.start() - 1] in ":-" else "4294967295"
        rows.append(re.sub(r"%[uU]", fill, text))
    return rows


_rows = plugin_remap_rows()
_widest = max((len("    " + r[len("remap:"):].strip()) for r in _rows),
              default=0)
check(len(_rows) == 4 and any("moves from" in r for r in _rows)
      and any("every session on this pool" in r for r in _rows),
      f"bpa_cli.c's `remap:` format strings were not read, so the fake "
      f"dataplane answers with nothing the plugin says: {_rows!r}")
check(_widest > config.CONSOLE_COLUMNS,
      f"the widest remap row is {_widest} columns unfolded, inside the "
      f"console already, so this box would measure nothing")
_sh = shell(dp=Dp({"bpa pool": (0, "\n".join(_rows) + "\n")}))
_sh.running_config = lambda: _running
_sh.candidate = cfg(pooled("subs", block_size="1024"))
_rv, _text = driven(_sh.do_commit, [])
_printed = _text.splitlines()
says("re-mapping, widest rows", _text,
     "error: this change re-maps subscribers that already have mappings")
_over = [r for r in _printed if len(r) > config.CONSOLE_COLUMNS]
check(not _over,
      f"{len(_over)} remap row(s) do not fit the console: "
      + "; ".join(f"{len(r)} columns {r.strip()[:60]!r}" for r in _over))
_said = " ".join(_text.split())
for _row in _rows:
    _sentence = " ".join(_row[len("remap:"):].split())
    check(_sentence in _said,
          f"the plugin's sentence does not survive the fold word for word: "
          f"{_sentence[:60]!r} in {_text!r}")
if not _over:
    ok(f"the widest remap row is {_widest} columns as the plugin answers it "
       f"and the {len(_printed)} rows printed all fit "
       f"{config.CONSOLE_COLUMNS}, the words whole")

# `commit confirmed <minutes>` with something that is not minutes. The window
# is what gives the operator a way back from a change that costs them the
# session, so a malformed one must not arm silently at the default - the box
# would roll back ten minutes later for a reason nobody typed.
_sh = shell(Api(have={"pg0": 1}))
_sh.running_config = lambda: config.Config()
_sh.candidate = config.Config()
_sh.candidate.descriptions["pg0"] = "uplink"
_rv, _text = driven(_sh.do_commit, ["confirmed", "soon"])
says("confirmed window", _text, "error: usage: commit confirmed <minutes>")

# And the two that are about where the operator is standing rather than about
# the configuration: `commit` with nothing built, and `commit` typed before
# `configure`. Separate call sites because they are separate situations - one
# is a candidate that was discarded, the other a mode that was never entered.
# Typed at the prompt rather than called, because the prompt is the only way
# to reach it: `commit` after the candidate was discarded by an `exit` that
# left the operator in configuration mode is what produces it on a real box.
_sh = shell()
_sh.mode = "config"
_sh.candidate = None
_rv, _text = driven(_sh.do_config, "commit")
says("no candidate", _text, "error: no candidate configuration", _rv,
     expect=True)

_rv, _text = driven(shell().do_operational, "commit")
says("not in config mode", _text, "error: enter configuration mode first",
     _rv, expect=True)
print("  a re-mapping commit, a window that is not a number, and two commits "
      "with nothing to apply")

print("\n=== the rollback store, and the revert that runs unattended ===")
# THE UNATTENDED HALF IS THE POINT. `commit confirmed` arms a revert that
# fires with nobody at the prompt, and a revert that applied part of a
# configuration would leave the box in neither the one the operator made nor
# the one they were promised back. So it validates first and refuses whole,
# and the refusal has to reach the console of a box nobody is watching.
_sh = shell()
_sh.running_config = lambda: config.Config()
_rv, _text = driven(_sh.revert_confirm,
                    {"config": "nat\n cgnat\n  pool subs\n"
                               "   inside 100.64.0.0/22\n   exit\n"
                               "  exit\n exit\n"},
                    "commit was not confirmed: rolling back")
says("revert does not validate", _text,
     "error: the configuration to roll back to does not validate on this box",
     _rv)

# THE RESTART THAT USED TO BE HERE IS GONE WITH THE MECHANISM IT REFUSED FOR.
#
# A revert that took the management card back restarted the box, and this
# drove the case where systemctl refuses, so the operator is not left
# believing a box is coming back (#303). #378 removed that restart: the box
# claims its card on every boot, so a revert that clears the setting is
# undone by the next start anyway - the window arrived where it started and
# charged a reboot for it, and the console said the card was going back to
# Linux while the opposite happened.
#
# The case goes with the code rather than being adapted to something else.
# A refusal kept alive by a test that drives it through a seam nothing else
# reaches is a refusal nobody can get to, which is the shape this file exists
# to measure rather than to create.

# And the store's own grammar. A revision that is not a number and a revision
# that is not there are different answers: one is a typo, the other is a box
# that has not been committed to that many times.
_rv, _text = driven(shell().do_rollback, ["seven"])
says("rollback grammar", _text,
     f"error: usage: rollback <0-{prismash.ROLLBACK_KEEP - 1}>")

_sh = shell()
_sh.rb = type("R", (), {"load": lambda self, n: None})()
_rv, _text = driven(_sh.do_rollback, ["3"])
says("revision absent", _text, "error: revision 3 does not exist")
print("  a revert that does not validate, and two revisions that cannot be "
      "loaded")

print("\n=== a pool statement, typed ===")
# THE STATEMENT THAT IS SILENTLY ACCEPTED IS THE EXPENSIVE ONE. #224 is a pool
# block that took every word it did not recognise and did nothing with it, so
# `show this` still showed the old value and the operator walked away
# believing they had changed it. These two are what is left at the bottom of
# that block: a word this box has no setting for, and a value it cannot read.
_sh = cfg_shell()
_sh.nat_ctx = ["nat", "cgnat"]
_sh.do_config("pool subs")
_rv, _text = driven(_sh.do_pool, "gibberish 1")
says("unknown pool statement", _text,
     "error: unknown statement in pool mode: gibberish", _rv, expect=True)

_sh = cfg_shell()
_sh.nat_ctx = ["nat", "cgnat"]
_sh.do_config("pool subs")
_rv, _text = driven(_sh.do_pool, "policy sideways")
says("unreadable pool value", _text,
     "error: invalid argument: policy must be interleaved or blocked", _rv,
     expect=True)
print("  a word with no setting behind it and a value with no meaning")

print("\n=== the accounts block, where a refusal that does not fire locks "
      "somebody out ===")
# WHAT IS BEHIND THESE. An account statement that is accepted and does
# nothing produces a box whose configuration says an operator can log in and
# whose /etc/shadow says otherwise - and the first person to find out is the
# one holding the console cable. The plaintext-password refusal is the
# sharpest of them: a plaintext string in /etc/shadow does not fail loudly, it
# produces a password that simply never matches.
_users = [
    ("root removed", "user noc", "config", "no user root",
     "error: root cannot be removed; restrict it with 'ssh permit-root-login' "
     "instead"),
    ("a plaintext password", "user noc", "user", "password hunter2",
     "error: expected a crypt hash (openssl passwd -6), not a plaintext "
     "password"),
    ("a role this box has not got", "user noc", "user", "role wizard",
     "error: role 'wizard': expected one of administrator, cgnat-operator"),
    # The verdict row only: the rest is prose under it since #355, because as
    # one sentence this reached the console at 104 columns. The row the table
    # holds is the one an operator reads first and greps for.
    ("root's memberships", "user root", "user", "no role",
     "error: root's rights come from being root, not from a group"),
    ("`no` with nothing after it", "user noc", "user", "no",
     "error: no <statement>: say which statement to remove ('no ?' lists "
     "them)"),
    # `no ssh-key` with no key. The block has three negations and this one
    # takes an argument, so the refusal names the statement rather than `no` -
    # which is the half of #224 that was about being sent to look for a `no`
    # this block has three of.
    ("`no ssh-key` with no key", "user noc", "user", "no ssh-key",
     "error: usage: no ssh-key <key>"),
    ("a word the block has no statement for", "user noc", "user",
     "gibberish here",
     "error: unknown statement in user mode: gibberish"),
    # The SNMPv3 block next door, which has one shape and refuses everything
    # else: an auth line with no passphrase is a user snmpd will not
    # authenticate, and the box would carry it looking configured.
    ("half an SNMPv3 credential", "snmp-user bob", "snmp-user", "auth sha",
     "error: a v3 user takes 'auth' or 'priv'"),
]
for _label, _enter, _mode, _line, _want in _users:
    _sh = cfg_shell()
    _sh.do_config(_enter)
    _handler = {"config": _sh.do_config, "user": _sh.do_user,
                "snmp-user": _sh.do_snmp_user}[_mode]
    _rv, _text = driven(_handler, _line)
    says(_label, _text, _want, _rv, expect=True)
print(f"  {len(_users)} account statements, each refused at the prompt")

print("\n=== the box's own settings, refused while the operator is still "
      "typing ===")
# BOTH ENDS, and neither is redundant: `apply` refuses these again for
# `rollback` and `--load`, and this is the copy that reaches somebody who can
# fix it in the next keystroke rather than hours after committing (#123). The
# dataplane card is the one with a site visit behind it - a name that is not
# a card is a file the box replays at its next start naming a NIC it has not
# got, answered with `no-pci` in a journal nobody is reading.
_settings = [
    ("snmp with no value", "snmp location",
     "error: usage: snmp <setting> <value>"),
    ("an snmp setting this box has not got", "snmp nonsense rack4",
     "error: 'nonsense' is not an snmp setting"),
    # `dataplane management` was a statement until #385, written by the box
    # itself on a one-card box; it is neither typed nor read now, and a
    # 0.76.x file pasted at the prompt meets the usage line for the one
    # statement there is under that word. A saved file carrying it is dropped
    # with a note rather than refused - test/test_dataplane_if.py holds that.
    ("a statement this VTY no longer has",
     "dataplane management ens18 address 10.0.0.2/24 mac 02:00:00:00:00:09",
     "error: 'dataplane' has moved under 'system'"),
    # Under `system` > `dataplane` since #457: the word alone at the top is
    # refused with where it went, and inside it the statement's usage.
    ("dataplane with no card named", "system|dataplane|interface",
     "error: usage: interface <card>, under 'system' > 'dataplane'"),
    ("something under system that is not dataplane", "system|dns",
     "error: under 'system' there is 'dataplane', 'cpu' and 'memory'"),
    ("ssh with no value", "ssh port",
     "error: usage: ssh <setting> <value>"),
    ("an ssh value sshd would not take", "ssh permit-root-login maybe",
     "error: ssh permit-root-login 'maybe': expected one of yes, no, "
     "prohibit-password, forced-commands-only"),
    # A GLOBAL CGNAT VALUE, which is a different site from the word check
    # above it. #273 closed `cgnat <word>` and left `cgnat <word> <number>`
    # open: a timeout of zero was accepted, saved and sent to a message that
    # reads zero as "leave this one alone", so the box kept the timeout it
    # already had and the configuration said otherwise (#351).
    # Typed where it is typed since #446: under `nat` > `cgnat`.
    ("a global timeout of zero", "nat|cgnat|timeout udp 0",
     "error: cgnat timeout udp 0: expected 1-4294967295"),
]
for _label, _line, _want in _settings:
    _sh = cfg_shell()
    *_lead, _line = _line.split("|")
    for _step in _lead:
        _sh.do_config(_step)
    _rv, _text = driven(_sh.do_config, _line)
    says(_label, _text, _want, _rv, expect=True)
print(f"  {len(_settings)} settings, each refused before it reaches the "
      f"candidate")

print("\n=== an interface statement, typed ===")
# EVERY ONE OF THESE IS A LINE THAT WOULD OTHERWISE BE STAGED AS FRR'S. The
# comment above these branches in the product records what that cost: FRR
# accepts `description` and `shutdown` inside an interface stanza, so a
# statement that never reached the dataplane errored nowhere, read back
# correctly from `show running-config`, and made three lab assertions vacuous.
# A usage refusal that stops firing here does not print a usage line - it
# hands the words to FRR.
_iface = [
    # The verdict names the rule that was broken rather than repeating the
    # grammar, since #414: "six octets" is what is wrong with `0200`, and a
    # usage line said the same thing about four different mistakes.
    ("a MAC that is not six octets", "pg0", "mac-address 0200",
     "error: mac-address '0200': expected six octets"),
    ("a MAC octet that is not hex", "pg0", "mac-address zz:zz:zz:zz:zz:zz",
     "error: mac-address 'zz:zz:zz:zz:zz:zz': 'zz' is not a hex octet"),
    ("the broadcast address", "pg0", "mac-address ff:ff:ff:ff:ff:ff",
     "error: mac-address ff:ff:ff:ff:ff:ff: that is the broadcast address"),
    ("a multicast address", "pg0", "mac-address 01:00:5e:00:00:01",
     "error: mac-address '01:00:5e:00:00:01': the group bit is set"),
    ("a mac-address with no value at all", "pg0", "mac-address",
     "error: mac-address: expected an address"),
    ("linux-name with no name", "pg0", "linux-name",
     "error: usage: linux-name <name>"),
    ("a Linux name Linux would not take", "pg0", "linux-name a/b",
     "error: linux-name: 'a/b' cannot be a Linux interface name "
     "(15 characters, no '/')"),
    ("vrf with no table", "pg0", "vrf",
     "error: usage: vrf <1-4294967295>, or 0 to take it back out"),
    ("a VLAN tag with no number", "pg0.7", "vlan",
     "error: usage: vlan <1-4094> [inner <1-4094>]"),
    # The second `usage: vlan` line, and a different branch from the first:
    # the outer tag is right and what follows it is not a complete `inner`.
    # Same sentence, different site - which is why this file spends refusals
    # on lines rather than on text.
    ("an inner tag with no number", "pg0.7", "vlan 7 inner",
     "error: usage: vlan <1-4094> [inner <1-4094>]"),
    # The INNER tag's own bound, which is a third site again. The outer tag
    # borrows the sub-interface name check - `pg0.7` has to carry vlan 7 - so
    # a wrong outer tag is refused by the name before the range is reached.
    # Nothing is named after the inner one, and until #350 nothing bounded it:
    # `vlan 7 inner 4095` was staged exactly as typed.
    ("an inner tag 802.1Q keeps for itself", "pg0.7", "vlan 7 inner 4095",
     "error: inner 4095 out of range (1-4094): 802.1Q reserves 0 and 4095"),
    ("an address statement with no prefix", "pg0.7", "ipv4 address",
     "error: usage: ipv4 address <prefix>"),
    ("an address with no prefix length", "pg0.7", "ipv4 address 999",
     "error: '999' has no prefix length"),
    ("`no ipv4 address` with no prefix", "pg0.7", "no ipv4 address",
     "error: usage: no ipv4 address <prefix>"),
    ("description with no text", "pg0", "description",
     "error: usage: description <text>"),
    ("a uRPF mode the dataplane has not got", "pg0", "urpf sideways",
     "error: usage: urpf <" + "|".join(prismash.URPF_MODES) + ">"),
    # VRRP (#7). One refusal per site rather than per sentence, the way the
    # VLAN rows above are written: the statement is taken by ONE function
    # that both the prompt and the file parser call, and what is checked here
    # is that the prompt prints what that function says.
    ("a virtual router with no id", "pg0", "vrrp",
     "error: " + config.VRRP_USAGE_REFUSAL[0]),
    ("an id that is not a number", "pg0", "vrrp one address 100.64.3.254",
     "error: vrrp 'one': the virtual router id is a number"),
    ("an id outside RFC 5798's range", "pg0", "vrrp 0 address 100.64.3.254",
     "error: vrrp 0: the virtual router id is 1 to 255"),
    ("an address with a prefix length on it", "pg0",
     "vrrp 1 address 100.64.3.254/22",
     "error: vrrp 1 address '100.64.3.254/22': write it without a prefix "
     "length"),
    ("something that is not an address", "pg0", "vrrp 1 address gateway",
     "error: vrrp 1 address 'gateway': not an IPv4 address"),
    ("the priority RFC 5798 keeps for the address owner", "pg0",
     "vrrp 1 priority 255",
     "error: vrrp: priority 255 is RFC 5798's address owner"),
    ("a priority outside the range", "pg0", "vrrp 1 priority 300",
     "error: vrrp 1 priority 300: the range is 1 to 254"),
    ("a priority that is not a number", "pg0", "vrrp 1 priority high",
     "error: vrrp 1 priority 'high': a number"),
    ("a decrement that is not a number", "pg0",
     "vrrp 1 track pg1 decrement lots",
     "error: vrrp 1 track: decrement 'lots': a number"),
    ("a decrement outside the range", "pg0",
     "vrrp 1 track pg1 decrement 0",
     "error: vrrp 1 track: decrement 0: the range is 1 to 254"),
    ("an interface tracking itself", "pg0",
     "vrrp 1 track pg0 decrement 50",
     "error: vrrp 1 track pg0: an interface cannot track itself"),
    ("removing a virtual router that is not there", "pg0", "no vrrp 2",
     "error: pg0 has no virtual router 2"),
    ("removing one with no id", "pg0", "no vrrp",
     "error: usage: no vrrp <vrid>"),
    ("removing one whose id is not a number", "pg0", "no vrrp all",
     "error: no vrrp 'all': the virtual router id is a number"),
]
for _label, _on, _line, _want in _iface:
    _sh = cfg_shell()
    # SET PER ROW, not once. `frr_ctx` is what says which interface block the
    # line is inside, and a context left over from the previous row would put
    # a statement on an interface this row never named.
    _sh.frr_ctx = [f"interface {_on}"]
    _rv, _text = driven(_sh.do_config, _line)
    says(_label, _text, _want, _rv, expect=True)
print(f"  {len(_iface)} interface statements, each kept off FRR's pile")

print("\n=== an abbreviation that begins more than one statement (#393) ===")
# TYPED, THROUGH `feed`, and not handed to `do_config`: the resolution happens
# once before dispatch, so a box that called the handler would measure the
# half that never sees an abbreviation at all.
#
# The refusal is the price of the resolution beside it. `no shut` is one
# statement in an interface block and `i` is four, and a VTY that resolved the
# second by position would pick a statement the operator did not type and
# commit it - which is the failure #393 is about, pointing the other way.
_amb = [
    ("four statements begin with 'i', inside an interface", "interface pg0",
     "i 1", "'i' begins 4 statements here: ip, ipv4, ipv6, isis"),
    ("and the same four under `no`", "interface pg0", "no i",
     "'i' begins 4 statements here: ip, ipv4, ipv6, isis"),
    ("three statements begin with 'r' at the top level", None, "r 3",
     "'r' begins 3 statements here: rollback, router, rpki"),
]
for _label, _ctx, _line, _prose in _amb:
    _sh = cfg_shell()
    _sh.frr_ctx = [_ctx] if _ctx else []
    _rv, _text = driven(_sh.feed, _line)
    says(_label, _text, "error: an ambiguous abbreviation", _rv, expect=True)
    says(_label + " (candidates)", _text, "    " + _prose)

# And in operational mode, where the same four letters mean four other things.
_rv, _text = driven(shell().feed, "s")
says("four operational statements begin with 's'", _text,
     "error: an ambiguous abbreviation", _rv, expect=True)
says("four operational statements begin with 's' (candidates)", _text,
     "    's' begins 4 statements here: save, show, start, system")
print("  ambiguity is refused with its candidates, never resolved by "
      "position")

print("\n=== and the two interface statements that are refused outside a "
      "block ===")
# CGNAT's attachment and `no interface`, which are typed at the top level.
# `no interface Gi0/1` on a physical port is the one worth its own sentence:
# the configuration did not create the port and the commit cannot remove it,
# so accepting the words would promise something nothing carries out.
_sh = cfg_shell()
_sh.nat_ctx = ["nat", "cgnat"]
_rv, _text = driven(_sh.do_config, "interface pg0 sideways")
says("cgnat attachment", _text,
     "error: usage: interface <interface> <inside|outside>", _rv,
     expect=True)

_rv, _text = driven(cfg_shell().do_config, "no interface eth9")
says("removing a physical port", _text,
     "error: eth9: only a sub-interface, a loopback or a bundle can be removed",
     _rv, expect=True)
print("  an attachment with no direction and a port a configuration cannot "
      "remove")

print("\n=== a sub-interface whose parent is not on the box, at commit ===")
# THE SAME SENTENCE AS `create_subif` PRINTS AND A DIFFERENT CALL SITE. That
# one is reached by typing; this one is reached by `rollback`, by `--load` and
# by prisma-routing.service replaying the file at boot, where nobody is typing
# and the card in question may have been renamed by a firmware upgrade.
_target = config.Config()
_target.vlans["pg9.10"] = {"outer": 10, "inner": None}
_sh = shell(Api(have={"pg0": 1}),
            dp=Dp({"show interface": (0, "Name  Idx State MTU\n"
                                         "pg0   1   up    9000/0/0/0\n")}))
_sh.running_config = lambda: config.Config()
_rv, _text = driven(_sh.apply, _target, check_only=True)
says("parent at commit", _text,
     "error: interface pg9.10: its parent pg9 is not on this box", _rv)
print("  a tag that is fine on a port that is not there")

print("\n=== what `commit check` reads about a virtual router (#7) ===")
# THE GAP THIS CLOSES: both of these were refused only by set_vrrp, which runs
# when the commit is APPLYING. So `commit check` said "configuration check
# succeeds" and the commit then failed part-way through, on a box the earlier
# steps had already changed - and the check is the sentence an operator acts
# on in a maintenance window.
_dp = Dp({"show interface": (0, "Name  Idx State MTU\n"
                                "pg0   1   up    9000/0/0/0\n")})
_target = config.Config()
_target.vrrp["pg0"] = {1: {"addresses": [], "priority": 200, "track": {}}}
_sh = shell(Api(have={"pg0": 1}), dp=_dp)
_sh.running_config = lambda: config.Config()
_rv, _text = driven(_sh.apply, _target, check_only=True)
says("no address, at check", _text,
     "error: interface pg0: vrrp 1 has no address", _rv)
_target = config.Config()
_target.vrrp["pg0"] = {1: {"addresses": ["100.64.3.254"], "priority": 200,
                           "track": {"pg9": 150}}}
_sh = shell(Api(have={"pg0": 1}), dp=_dp)
_sh.running_config = lambda: config.Config()
_rv, _text = driven(_sh.apply, _target, check_only=True)
says("a tracked interface that is not there, at check", _text,
     "error: interface pg0: vrrp 1: no interface pg9", _rv)
# And the interface the router itself sits on, which is the same sentence
# every other statement on a missing interface already got.
_target = config.Config()
_target.vrrp["pg9"] = {1: {"addresses": ["100.64.3.254"], "priority": 200,
                           "track": {}}}
_sh = shell(Api(have={"pg0": 1}), dp=_dp)
_sh.running_config = lambda: config.Config()
_rv, _text = driven(_sh.apply, _target, check_only=True)
says("a virtual router on an interface that is not there", _text,
     "error: interface pg9: this box has no such interface", _rv)
print("  three refusals a check used to leave for the commit to discover")

print("\n=== a virtual router the dataplane did not take, at commit (#7) ===")
# THE READ-BACK, not the call's exit status. vat2 answering 0 says the message
# was accepted, not that the box has a virtual router - and a VR that was
# asked for and is not there is a takeover the operator believes in and will
# not get. The box here takes every call and comes back with an empty dump,
# which is exactly that shape.
_target = config.Config()
_target.vrrp["pg0"] = {1: {"addresses": ["100.64.3.254"], "priority": 200,
                           "track": {}}}
_sh = shell(Api(have={"pg0": 1}),
            dp=Dp({"show interface": (0, "Name  Idx State MTU\n"
                                         "pg0   1   up    9000/0/0/0\n")}))
_sh.running_config = lambda: config.Config()
_rv, _text = driven(_sh.apply, _target)
says("vrrp at commit", _text,
     "error: the dataplane did not take vrrp 1", _rv)
print("  asked for, accepted, and absent from the box that answered")

print("\n=== an interface name FRR cannot take, at commit (#389) ===")
# THE MAINTAINER'S BOX. `interface GigabitEthernet0/13/0 / no shutdown /
# commit` on 0.76.1: every `interface` line typed at this VTY is staged as an
# FRR stanza, FRR 10.7.1 refuses a name over 16 characters ("Maximum
# interface name length is 16 characters", from every daemon at once), the
# reload failed and the commit rolled back. Said before anything is applied,
# with the limit. The box here HAS the interface, so the existence check is
# silent and this refusal is the only one that can fire; the routing half is
# what changed, which is the gate.
_target = config.Config.parse("interface GigabitEthernet0/13/0\n"
                              " ipv4 address 203.0.113.1/24\n exit\n"
                              "! routing\ninterface GigabitEthernet0/13/0\n"
                              "exit\n")
_sh = shell(Api(have={"GigabitEthernet0/13/0": 1}),
            dp=Dp({"show interface": (0, "Name  Idx State MTU\n"
                                         "GigabitEthernet0/13/0   1   up"
                                         "    9000/0/0/0\n")}),
            frr=frr_stub(available=True))
_sh.running_config = lambda: config.Config()
_rv, _text = driven(_sh.apply, _target, check_only=True)
says("FRR's limit", _text,
     "error: interface GigabitEthernet0/13/0: a name FRR cannot take", _rv)
check("at most 16 characters and this one is 21" in _text,
      f"the limit and the count are not in the sentence: {_text!r}")
# A name FRR takes is not refused by this: 16 is FRR's own limit, one more
# than Linux's, and the shortest dataplane name a real card can carry.
_target = config.Config.parse("interface GigabitEther0/1\n"
                              " ipv4 address 203.0.113.1/24\n exit\n"
                              "! routing\ninterface GigabitEther0/1\nexit\n")
_sh = shell(Api(have={"GigabitEther0/1": 1}),
            dp=Dp({"show interface": (0, "Name  Idx State MTU\n"
                                         "GigabitEther0/1   1   up"
                                         "    9000/0/0/0\n")}),
            frr=frr_stub(available=True))
_sh.running_config = lambda: config.Config()
_rv, _text = driven(_sh.apply, _target, check_only=True)
check("a name FRR cannot take" not in _text,
      f"a 16-character name was refused, and FRR takes 16: {_text!r}")
print("  21 characters refused with the limit; 16 is FRR's own and passes")
# AND A NAME THIS BOX HAS NOT GOT is refused once, not twice. A `rollback`
# to a revision saved before the upgrade names GigabitEthernet0/13/0 on a
# box that holds the card as ens19: the existence check refuses it with
# what the box has, and the FRR limit refused it again under that, with
# advice - "from the next start of the dataplane" - for a box that has
# already renamed the card. Measured on 0dbbf73: both verdicts, one commit.
_target = config.Config.parse("interface GigabitEthernet0/13/0\n"
                              " ipv4 address 203.0.113.1/24\n exit\n"
                              "! routing\ninterface GigabitEthernet0/13/0\n"
                              "exit\n")
_sh = shell(Api(have={"ens19": 1}),
            dp=Dp({"show interface": (0, "Name  Idx State MTU\n"
                                         "ens19   1   up    9000/0/0/0\n")}),
            frr=frr_stub(available=True))
_sh.running_config = lambda: config.Config()
_rv, _text = driven(_sh.apply, _target, check_only=True)
says("a pre-upgrade name, once", _text,
     "error: interface GigabitEthernet0/13/0: this box has no such "
     "interface", _rv)
check("a name FRR cannot take" not in _text,
      f"refused a second time, with advice for a box that has already "
      f"renamed the card: {_text!r}")
print("  a name the box has not got: one verdict, the one that says what "
      "it has")

print("\n=== a policy that would commit and store nothing ===")
# A route-map is many lines and has no `router` statement of its own, so
# nothing checked it: on a box whose FRR has not finished starting the whole
# policy commits, reports success and is held by nobody. Reported once per
# missing daemon rather than once per statement - the operator has one thing
# to fix.
_target = config.Config()
_target.routing = ["route-map RM permit 10", " set local-preference 200",
                   "exit"]
_frr = frr_stub(available=True)
# ON THE INSTANCE and not in `frr_stub`, on purpose. Every other box here
# leaves `daemons()` as FRR's own, which answers None on a box with no FRR and
# makes this check skip; moving it into the factory would change what those
# boxes are arranging as well as this one.
_frr.daemons = lambda: {"bgpd"}
_sh = shell(frr=_frr)
_sh.running_config = lambda: config.Config()
_rv, _text = driven(_sh.apply, _target, check_only=True)
says("policy with no daemon", _text,
     "error: 1 policy statement(s) need mgmtd, which is not running", _rv)
print("  a route-map refused because nothing is running to hold it")

print("\n=== IS-IS frames that would never reach the kernel ===")
# MEASURED ON A SHIPPED BOX: IS-IS configured, commit succeeded, isisd up, the
# running configuration read correctly, and no adjacency ever formed - with
# the only evidence in a VPP error counter nothing surfaced (#168). The punt
# is what makes the adjacency possible, and a dataplane that refuses it has to
# say so at the commit rather than leave that counter as the only sign.
_sh = shell(Api(refuse={"lcp_osi_proto_enable"}))
_rv, _text = driven(_sh.enable_osi_isis)
says("osi punt", _text,
     "error: IS-IS is configured but its frames cannot be punted to the "
     "kernel", _rv)
print("  the punt refused, and said at the commit rather than in a counter")

print("\n=== a routing daemon that is not answering, protocol by protocol ===")
# NOT "NO NEIGHBOURS". A daemon that is down and a protocol with nothing
# configured are different situations with different first questions, and
# collapsing them is how an operator spends ten minutes looking at a protocol
# that was never started - which is the argument `frr_json` makes in its own
# docstring. Each of these is that argument at one call site.
_silent = [
    ("BGP", lambda sh: sh.show_bgp(["summary"]), frr_stub(available=True),
     "error: BGP is not answering"),
    ("OSPF", lambda sh: sh.show_ospf_interfaces(), frr_stub(available=True),
     "error: OSPF is not answering"),
    ("IS-IS", lambda sh: sh.show_isis_interfaces(), frr_stub(available=True),
     "error: IS-IS is not answering"),
    # A daemon that ANSWERS and says something this VTY cannot parse. Distinct
    # from silence: bgpd is up, so "bgpd may not be running" would send
    # somebody to start a daemon that is already started.
    ("BGP answering nonsense", lambda sh: sh.show_bgp(["summary"]),
     frr_stub(available=True,
              shows={"show bgp ipv4 unicast summary json": (0, "{not json")}),
     "error: BGP answered something this VTY could not read"),
]
for _label, _verb, _frr, _want in _silent:
    _rv, _text = driven(_verb, shell(frr=_frr))
    says(_label, _text, _want)

# And the dataplane's own table, which is not FRR's: `show route` reads VPP's
# FIB because FRR's answer says nothing about whether a route ever got there.
# An empty table from a dataplane that is not answering would read as a box
# with no routes at all.
_rv, _text = driven(shell(dp=Dp({"show ip fib": (1, "")})).show_route, [])
says("the FIB", _text,
     "error: cannot read the forwarding table: the dataplane is not answering")
print("  four daemons asked and not answering, and the FIB behind them")

print("\n=== clear and show, when the words are wrong ===")
# `clear` is what an operator reaches for at 2am, and `show` is what they read
# before they touch anything. A verb that takes the wrong words and does
# something anyway is worse here than anywhere else in this VTY: the first is
# a session bounced that nobody asked to bounce, the second an answer to a
# question that was not asked.
_words = [
    ("clear with no neighbour", lambda sh: sh.do_operational("clear bgp"),
     "error: usage: clear bgp <neighbour> [soft [in|out]]"),
    ("clear isis, wrong noun",
     lambda sh: sh.do_operational("clear isis sideways"),
     "error: usage: clear isis adjacency [<system-id>]"),
    # The second `clear isis` usage line: the noun is right and there are too
    # many words after it. One sentence, two sites, and the reader tells them
    # apart because it spends a refusal on the line.
    ("clear isis, too many words",
     lambda sh: sh.do_operational("clear isis adjacency one two"),
     "error: usage: clear isis adjacency [<system-id>]"),
    ("show hardware with arguments", lambda sh: sh.show_hardware(["extra"]),
     "error: show hardware: takes no arguments"),
    ("show cgnat ha with the wrong noun", lambda sh: sh.show_ha(["nonsense"]),
     "error: show cgnat ha: takes nothing, or 'peer <file>'"),
]
for _label, _verb, _want in _words:
    _rv, _text = driven(_verb, shell())
    says(_label, _text, _want)

# `show this` outside configuration mode. It answers about the CANDIDATE, and
# there is no candidate to answer about - so it says which command does show
# what is in effect rather than printing an empty screen.
_sh = shell()
_sh.candidate = None
_rv, _text = driven(_sh.show_this)
says("show this", _text, "error: show this: not in configuration mode")
print(f"  {len(_words) + 1} commands refused for the words they were given")

print("\n=== the interface sampler, which takes seconds to answer ===")
# A RATE IS A DIFFERENCE BETWEEN TWO COUNTERS, and every refusal here is about
# not printing one when there is nothing to subtract. The elapsed time is
# measured rather than assumed, so a sample that stopped part-way has no
# denominator - and a number printed anyway would be wrong in exactly the
# conditions this command gets asked in.


class Interrupted(Dp):
    """A dataplane whose first counter read is the one Ctrl-C lands on."""

    def run(self, cmd):
        if cmd == "show interface":
            raise KeyboardInterrupt
        return 0, ""


_sampler = [
    ("interval with no number", Dp(), ["interval"],
     "error: interval needs a number after it"),
    ("an interval outside the range", Dp(), ["interval", "0.05"],
     "error: interval must be between 0.1 and 60 seconds"),
    ("the dataplane stopping part-way", Dp({"show interface": (1, "")}), [],
     "error: the dataplane stopped answering part-way through the sample"),
    ("stopped before the second sample", Interrupted(), [],
     "error: stopped before two samples, so no rate could be measured"),
]
for _label, _dp, _args, _want in _sampler:
    _rv, _text = driven(shell(dp=_dp).show_interface_utilisation, _args)
    says(_label, _text, _want)
print(f"  {len(_sampler)} ways to have no rate, and none of them prints one")

print("\n=== the shell escape and the installer ===")
# TWO COMMANDS THAT HAND THE CONSOLE AWAY. Each spawns a child rather than
# exec'ing, so that leaving it comes back to the VTY - and each has to say so
# when the child never started, because a prompt that came straight back with
# nothing on the screen reads as a command that did nothing.
#
# `install` is refused on an installed box by this check AND by not being in
# the tree. Two answers to two questions - whether the operator can find it,
# and whether it can run - and a box with only the first would be one stale
# TREE away from offering to overwrite itself.
_notaprogram = os.path.join(_tmp, "not-a-program")
open(_notaprogram, "w").close()
os.environ["PRISMA_SHELL"] = _notaprogram
_rv, _text = driven(shell().start_shell)
del os.environ["PRISMA_SHELL"]
# The errno text is the operating system's and not this product's, so the row
# is asserted by its start: what has to reach the operator is which program
# did not start, and that the VTY is still here.
check(any(l.startswith(f"error: cannot start {_notaprogram}: ")
          for l in _text.splitlines()),
      f"a shell that cannot be started must be named: {_text!r}")

_rv, _text = driven(shell().do_install)
says("not the live medium", _text,
     "error: this box is not running from the installation medium")

# The other half, on a box that IS the live medium and whose installer is not
# where it should be. Both the predicate and the path are replaced, and put
# back straight afterwards: a module-level name left changed would follow
# every box below this one.
_live, _installer = prismash.running_from_live_medium, prismash.INSTALLER
prismash.running_from_live_medium = lambda: True
prismash.INSTALLER = os.path.join(_tmp, "no-such-installer")
try:
    _rv, _text = driven(shell().do_install)
finally:
    prismash.running_from_live_medium, prismash.INSTALLER = _live, _installer
check(any(l.startswith(f"error: cannot start {prismash.INSTALLER}: ") or
          l.startswith("error: cannot start ") and "no-such-installer" in l
          for l in _text.splitlines()),
      f"an installer that cannot be started must be named: {_text!r}")
check(prismash.running_from_live_medium is _live,
      "the live-medium predicate was left replaced, and every box after this "
      "one is now on a box that thinks it is the installation medium")
print("  a shell that will not start, an installer that is not there, and an "
      "installer that must not run")

print("\n=== the box itself: reboot and shutdown, and what refuses them ===")
# FOUR REFUSALS AND ONE ORDER. `system reboot` and `system shutdown` go to
# systemd through Shell.sys (#388), and each can be refused three ways: the
# account may not, systemd would not, or the verb is not one the menu has.
# The permission refusal is read together with what systemd was asked,
# because a refusal printed AFTER the ask is a box going down under a row
# saying it may not - the order is the assertion, not the sentence. What the
# statements print when nothing refuses, and that they said it before they
# asked, is test/test_prismash_system.py's; this file holds the refusals.
_asked = []


def _systemd(rc, says):
    def run(argv, **kw):
        _asked.append(list(argv))
        return rc, says
    return prismash.System(runner=run)


_sh = shell(caps=set())
_sh.sys = _systemd(0, "")
_rv, _text = driven(_sh.do_operational, "system reboot")
says("read-only, reboot", _text,
     "error: system reboot: permission denied for a read-only account", _rv,
     expect=True)
check(_asked == [], f"and systemd was not asked: {_asked}")
_sh = shell(caps={prismash.CAP_CGNAT})
_sh.sys = _systemd(0, "")
_rv, _text = driven(_sh.do_operational, "system shutdown")
says("cgnat operator, shutdown", _text,
     f"error: system shutdown: permission denied for a {prismash.ROLE_CGNAT} "
     f"account", _rv, expect=True)
check(_asked == [], f"and systemd was not asked: {_asked}")

# systemd refusing, in its own words - the sentence 0.76.0 removed with the
# restart it belonged to, and the one an operator has to see instead of a
# prompt that came straight back over a box that is still up. The verdict is
# one row and systemd's words are prose under it: joined, the row measured
# 145 columns with a two-line refusal, and the reboot verb prints three.
#
# `_asked` is read for its systemctl half only: the cost prose measures the
# running configuration the way `save` does, and sshd is read through the
# same seam - measured, the list is [['sshd', '-T'], ['systemctl', ...]].
_sh = shell()
_sh.sys = _systemd(1, "Failed to reboot: unit not found")
_rv, _text = driven(_sh.do_operational, "system reboot")
says("restart refused", _text, "error: the box could not be restarted")
check("Failed to reboot: unit not found" in _text.replace("\n    ", " "),
      f"with systemd's words under the verdict: {_text!r}")
check([a for a in _asked if a[:1] == ["systemctl"]] == [["systemctl", "reboot"]],
      f"after asking for exactly 'systemctl reboot': {_asked}")
_asked.clear()
_sh = shell()
_sh.sys = _systemd(1, "Failed to power off: unit not found")
_rv, _text = driven(_sh.do_operational, "system shutdown")
says("shutdown refused", _text, "error: the box could not be shut down")
check("Failed to power off: unit not found" in _text.replace("\n    ", " "),
      f"with systemd's words under the verdict: {_text!r}")
check([a for a in _asked if a[:1] == ["systemctl"]] == [["systemctl", "poweroff"]],
      f"after asking for exactly 'systemctl poweroff': {_asked}")
_asked.clear()

_sh = shell()
_sh.sys = _systemd(0, "")
_rv, _text = driven(_sh.do_operational, "system halt")
says("a verb the menu has not got", _text,
     "error: usage: system reboot | shutdown | upgrade | dataplane restart", _rv, expect=True)
check(_asked == [], f"and systemd was not asked: {_asked}")
print("  refused by role before the ask, refused by systemd after it, and a "
      "verb it has not got")

print("\n=== a filter this box has not got ===")
# A pipe filters a `show` and nothing else, and an unknown filter is REFUSED
# rather than ignored. Ignoring it would answer the question the operator did
# not ask, with nothing on the screen to say it had - and `show running-config
# | inclde password` reading as a box with no passwords is the shape of that.
_sh = shell()
_sh.mode = "operational"
_rv, _text = driven(_sh.feed, "show hardware | sideways rubbish")
says("unknown filter", _text,
     "error: 'sideways' is not a filter; this box has: count, exclude, first, "
     "include, last, match, no-more, section", _rv, expect=True)
print("  a filter nobody has refuses the answer rather than reshaping it")



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

    Derived rather than kept. A suite that puts `src/prismash` on its path is a
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
        if re.search(r'src["\'/,\s]+.{0,3}prismash',
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
    env["PRISMASH_REFUSAL_LOG"] = LOG
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
# prismash.py with no box added anywhere goes red here on the next run and there
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
#
# IT IS ZERO, which is the first time it has been. Every refusal the VTY can
# print has been watched printing, so the next one added to prismash.py goes red
# here with nothing to subtract from - which is the state this file was
# written to reach rather than a milestone to celebrate. The lower branch
# below is now unreachable and stays where it is: the day somebody deletes a
# refusal without deleting its box, it is what says so.
UNRENDERED_AT_MOST = 0

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
            + "\n".join(f"    prismash.py:{n}  {source[n - 1].strip()[:88]}"
                        for n in unseen))
    elif len(unseen) < UNRENDERED_AT_MOST:
        bad(f"the floor says {UNRENDERED_AT_MOST} and only {len(unseen)} "
            f"refusal(s) are unrendered: lower it, or it stops holding "
            f"anything")
    else:
        ok(f"and the {len(unseen)} that have not are named, at the floor of "
           f"{UNRENDERED_AT_MOST}")

# ------------------------------------------------------- the driven widths
#
# WHY THIS IS HERE AND NOT IN test/test_console_width.py. That file puts a
# rule on the SOURCE - no literal row is wider than `config.CONSOLE_COLUMNS` -
# and it says on every run what the rule does not cover: 64 literal rows held,
# 178 print() calls and 118 verdicts carry an interpolated value and were only
# counted. The width of those is not a property of the source. `neighbour
# {peer} does not keep what it received` is 48 columns plus the peer: an IPv4
# address fits and a full-length IPv6 one does not, which is how #349 found
# the two it split. A static walk cannot decide that, and no amount of reading
# will make it able to (#363).
#
# What CAN decide it is driving the site and measuring what came out, and this
# file already drives them: the boxes above plus the suites below reach 185 of
# the 185 verdict sites in the VTY and 172 of its 242 print sites. A second
# harness would have to build its own way of reaching every one of them, which
# is nine tenths of what this file is. So the rows are recorded by the same
# hook that records the refusals, one line of the log per row, and read here.
#
# WHAT IT DECIDES, per site, and it is one of three things:
#
#   * BOUNDED. Every value that reaches it comes from somewhere with a widest
#     member, so the row is rebuilt with that member in place of the value and
#     measured. The widest members are read from the product or computed, not
#     invented: an invented 200-character pool name proves a fold nobody will
#     ever see and turns this into a check that fails for the wrong reason.
#   * UNBOUNDED BUT SAFE. The value is an operator's own word - a pool name, a
#     route-map name, a path, a sentence the dataplane sent back - and nothing
#     follows it on its row. A fold there costs nothing: it is the last thing
#     printed, every word the VTY wrote is already on the screen, and the
#     value survives whole in the scrollback for grepping. That is the shape
#     `say_finding` uses for its statement and the one #362 gave `ports` and
#     `block-size`.
#   * NEITHER, which is refused. An unbounded value with the VTY's own words
#     behind it pushes those words past the margin exactly when the value is
#     long, which is the failure #349, #355 and #362 each closed by hand.
#
# WHAT IT CANNOT SEE is printed too. A site no box here and no suite below
# reaches has no rows to read, and is counted as unreached rather than as
# passing - the labs are where most of those are driven, and the labs need a
# container. A row whose text is assembled somewhere else and handed to
# `print()` or `err()` whole cannot be decomposed at all; those are measured
# as they were driven, which is a lower bound on their width and not a bound.

WIDEST = {}          # a name for the kind -> how many columns it can take

#: A full-length IPv6 address, computed rather than typed. `.exploded` is the
#: longest text form of one, which is what an operator's neighbour statement
#: carries when nothing has compressed it.
WIDEST["an IP address"] = len(ipaddress.IPv6Address(1).exploded)
WIDEST["an IP prefix"] = WIDEST["an IP address"] + len("/128")
WIDEST["a MAC address"] = len("02:00:00:00:00:01")

#: The widest decimal any of these rows carries, read out of the VTY's own
#: grammar: `ARG_VRF` is `<1-4294967295>` and nothing this box prints a number
#: from is wider than the widest range it offers to type. NOT a bound on what
#: an operator can type - `mtu 99999999999999999999` is refused with the
#: digits quoted back - and that is the limit named on the screen below.
WIDEST["a decimal"] = max(
    len(n) for name, value in vars(prismash).items()
    if name.startswith("ARG_") and isinstance(value, str)
    for n in re.findall(r"\d+", value))

#: The widest interface name this box can have, which is the DATAPLANE's and
#: not Linux's. Linux refuses a name over 15 characters and config.py enforces
#: that on `linux-name`, but VPP's own names are longer:
#: `TenGigabitEthernet0/0/0` is 23, and a sub-interface adds a dot and a tag
#: out of the VLAN range this product declares. The wider of the two is the
#: one to measure with.
WIDEST["an interface name"] = (len("TenGigabitEthernet0/0/0") + 1
                               + len(str(config.VLAN_MAX)))

#: Names in the VTY that hold an interface name wherever they appear. A
#: vocabulary and not a list of sites: it is six words for the whole file, and
#: a site that starts carrying an interface name under one of them is bounded
#: on the next run with nothing added here.
#: `member` is a bundle's port (#455): in prismash.py it names nothing else.
IFNAMES = ("ifname", "iface", "parent", "sub", "host_name", "src",
           "tracked", "member")

#: Every collection of strings the product declares at module level, which is
#: where its closed sets live - URPF_MODES, the role names, the daemons, the
#: filters a pipe takes. Derived rather than listed, so a set added to the
#: product bounds its own sites without being mentioned here.
CLOSED = {}
for _mod in (config, prismash):
    for _name, _value in vars(_mod).items():
        if _name.startswith("_") or not _name.isupper():
            continue
        if (isinstance(_value, (tuple, list, set, dict)) and _value
                and all(isinstance(x, str) for x in _value)):
            CLOSED[f"{_mod.__name__}.{_name}"] = set(_value)

#: The constants themselves, for a slot that interpolates one. `usage: vrf
#: {ARG_VRF}` holds exactly what ARG_VRF is and nothing else.
CONSTANTS = {n: str(v) for m in (config, prismash) for n, v in vars(m).items()
             if n.isupper() and isinstance(v, (str, int))}

MAC_SHAPE = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")


def widest_for(expr, seen):
    """
    What the widest value at this slot is, or None if it has no widest.

    `seen` is every value that actually reached the slot while the boxes above
    and the suites below ran. The kind is read off those - the values are what
    the product really puts there - and the width is read off the product.
    """
    if expr in CONSTANTS:
        return (f"the constant {expr}", len(CONSTANTS[expr]))
    if expr in IFNAMES:
        return ("an interface name", WIDEST["an interface name"])
    if not seen:
        return None
    # A COLUMN OF ADDRESSES WITH THE BOX'S OWN PLACEHOLDER IN IT is still a
    # column of addresses (#392). A route with no next hop prints `-` in that
    # slot, and a reader that called the mixture unbounded was saying a value
    # has no widest when every value it can hold is either an address or one
    # character. The widest is the address, and the placeholder is named here
    # so that a slot holding some OTHER word goes on being unbounded.
    PLACEHOLDERS = {"-", "?"}
    _addrs = [v for v in seen if v not in PLACEHOLDERS]
    if _addrs and len(_addrs) < len(seen):
        try:
            for v in _addrs:
                ipaddress.ip_address(v)
            return ("an IP address or the box's placeholder for none",
                    WIDEST["an IP address"])
        except ValueError:
            pass
    try:
        for v in seen:
            ipaddress.ip_address(v)
        return ("an IP address", WIDEST["an IP address"])
    except ValueError:
        pass
    try:
        for v in seen:
            if "/" not in v:
                raise ValueError(v)
            ipaddress.ip_interface(v)
        return ("an IP prefix", WIDEST["an IP prefix"])
    except ValueError:
        pass
    if all(MAC_SHAPE.match(v) for v in seen):
        return ("a MAC address", WIDEST["a MAC address"])
    if all(v.isdigit() for v in seen):
        return ("a decimal", WIDEST["a decimal"])
    # The TIGHTEST set that holds every value seen. Several of the product's
    # tables overlap - a daemon name is in three of them - and the widest
    # member of the widest of those would be a bound on a different set.
    best = None
    for name, members in CLOSED.items():
        if all(v in members for v in seen):
            width = max(len(m) for m in members)
            if best is None or width < best[1]:
                best = (f"one of {name}", width)
    return best


def row_sites(path):
    """
    Every call in the VTY that BUILDS a row, by line, with its first argument.

    `err`, `err_prose` and `err_dataplane` are skipped inside their own
    bodies, for the reason `refusal_sites` skips `err_dataplane`: the `print`
    in `err` is the mechanism every verdict goes through and the site an
    operator's row came from is its caller. `print_prose` and `say_finding`
    are noted rather than skipped - what they print is wrapped by
    `console_prose` to this same budget by construction, except a single token
    longer than the budget, which is deliberate and documented (#252).
    """
    tree = ast.parse(open(path).read())
    inside = set()
    wrapped = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        span = range(node.lineno, (node.end_lineno or node.lineno) + 1)
        if node.name in ("err", "err_prose", "err_dataplane"):
            inside.update(span)
        elif node.name in ("print_prose", "say_finding"):
            wrapped.update(span)
    prints, verdicts = {}, {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if node.lineno in inside:
            continue
        if getattr(node.func, "id", None) == "print":
            prints[node.lineno] = node
        elif getattr(node.func, "attr", None) in ("err", "err_prose",
                                                  "err_dataplane"):
            verdicts[node.lineno] = node
    return prints, verdicts, wrapped


def pieces(node):
    """
    A row as `[(literal, expression, format spec), ...]`, or None.

    None when the argument is not an f-string this reader can take apart: a
    name, a concatenation, a `*rest`. Those rows are assembled somewhere else
    and handed over whole, and what they are made of is not visible here.

    A slot whose expression is a CONSTANT - `{'Interface':<26}` in a table
    header - is folded into the literal it is, formatted the way the product
    formats it. It is not a value and treating it as one would make every
    table header unbounded.
    """
    if len(node.args) != 1:
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return [(arg.value, None, "")]
    if not isinstance(arg, ast.JoinedStr):
        return None
    out, literal = [], ""
    for part in arg.values:
        if isinstance(part, ast.Constant):
            literal += part.value
            continue
        spec = ""
        if part.format_spec is not None:
            # A WIDTH THE SOURCE DOES NOT HOLD: `{via:<{vw}}` pads to a number
            # the box works out from the rows it has (#392). Marked rather
            # than silently read as the constant part alone, which would say
            # the slot is unpadded and make every value under it unrecoverable.
            if any(not isinstance(c, ast.Constant)
                   for c in part.format_spec.values):
                spec = RUNTIME_WIDTH
            else:
                spec = "".join(c.value for c in part.format_spec.values
                               if isinstance(c, ast.Constant))
        if isinstance(part.value, ast.Constant):
            if spec == RUNTIME_WIDTH:
                # A heading padded to a width the box measures. The padding is
                # spaces, so the words are these and the row is measured as
                # driven like every other runtime-width row below.
                literal += str(part.value.value)
            else:
                literal += format(part.value.value, spec)
            continue
        out.append((literal, ast.unparse(part.value), spec))
        literal = ""
    out.append((literal, None, ""))
    return out


#: The mark a slot's format spec carries when its width is computed at
#: runtime. Not a spec any value could produce, so it cannot be mistaken for
#: one.
RUNTIME_WIDTH = "\x00runtime\x00"


def pad(spec):
    """The columns a format spec takes whatever the value is."""
    found = re.match(r"^.?[<>^=]?[+\- ]?#?0?(\d+)", spec or "")
    return int(found.group(1)) if found else 0


def slot_values(shape, text):
    """The values that produced this row, recovered from its literal parts."""
    pattern = "".join(re.escape(lit) + ("(.*?)" if expr is not None else "")
                      for lit, expr, _ in shape)
    found = re.fullmatch(pattern, text, re.S)
    return found.groups() if found else None


def compose(shape, prefix, fill):
    """The widest row this shape makes, with `fill` deciding each slot."""
    out, i = prefix, 0
    for lit, expr, _ in shape:
        out += lit
        if expr is not None:
            out += fill(i)
            i += 1
    return max(len(row) for row in out.split("\n")), out


print("\n=== every row these boxes made the VTY print, measured against the "
      "widest ===")
print("=== value that can reach it (#363) ===")

# Read out of the product by DRIVING it rather than by parsing `err`, for the
# reason the rest of this file drives rather than reads: a verdict is seven
# columns wider than it looks, and a second copy of "error: " written here is
# a second thing to keep in step - one that fails SILENTLY, by measuring
# columns the box may have stopped printing.
_probe = driven(shell().err, "needle")[1].splitlines()[0]
PREFIX = _probe[:-len("needle")] if _probe.endswith("needle") else ""
if not PREFIX:
    # Loud, not skipped: without it every verdict below is measured seven
    # columns short and the whole section goes green on rows the box does not
    # print.
    bad(f"a verdict came back as {_probe!r}, so what `err` puts in front of "
        f"one cannot be read; every verdict below would be measured against "
        f"the wrong margin")
else:
    ok(f"a verdict reaches the console {len(PREFIX)} columns wider than it is "
       f"written, driven out of Shell.err: {PREFIX!r}")

PRINTS, VERDICTS, WRAPPED = row_sites(VTY)
SOURCE = open(VTY).read().splitlines()

printed = {}
for _line in open(ROWS):
    _chain, _, _text = _line.rstrip("\n").partition("\t")
    _text = _text.replace("\\n", "\n").replace("\\\\", "\\")
    for _n in _chain.split(","):
        # The INNERMOST frame that is a row-building site. `err` calls
        # `print`, `err_prose` calls `err`, and the row an operator reads was
        # composed by whichever of them the VTY's own code wrote.
        if _n and (int(_n) in PRINTS or int(_n) in VERDICTS):
            printed.setdefault(int(_n), set()).add(_text)
            break

tally = {}
unreached = []
over = []          # a row that does not fit, with the widest value driven
unsafe = []        # an unbounded value with the VTY's own words behind it
opaque = 0
measured = 0


def note(kind):
    tally[kind] = tally.get(kind, 0) + 1


for _site in sorted(set(PRINTS) | set(VERDICTS)):
    _node = VERDICTS.get(_site) or PRINTS[_site]
    _is_verdict = _site in VERDICTS
    _seen = printed.get(_site)
    if not _seen:
        note("never printed here")
        unreached.append(_site)
        continue
    if _site in WRAPPED:
        note("wrapped to the budget by console_prose")
        continue
    _shape = pieces(_node)
    if _shape is None:
        # Assembled elsewhere. Measured as driven, which is a lower bound:
        # what came out is what came out, and a row already over the console
        # is over it whatever the widest turns out to be.
        opaque += 1
        _worst = max((r for t in _seen for r in t.split("\n")), key=len)
        if len(_worst) > config.CONSOLE_COLUMNS:
            note("assembled elsewhere, and driven over")
            over.append((_site, len(_worst), "as driven", _worst))
        else:
            note("assembled elsewhere, driven inside")
        continue
    if all(expr is None for _, expr, _ in _shape):
        note("no value in it")
        continue
    # A ROW WHOSE COLUMN WIDTHS ARE MEASURED AT RUNTIME (#392). `{via:<{vw}}`
    # pads to a number this reader cannot know, and the padding is what tells
    # a value from the words around it - so the split below recovers nothing
    # and every slot comes back empty, which read as "no widest" on a row that
    # cannot fold at all.
    #
    # It cannot fold because the width came from `route_column_widths`, which
    # measures the rows the box actually holds and answers None when the
    # widest of them would not fit - and the caller then prints each entry as
    # a block instead. That is a guarantee about a runtime decision, so it is
    # asserted where a runtime decision can be: test_prismash_units.py drives
    # both route screens with an IPv6 route at its widest and a 28-character
    # interface name, and refuses a row over the console.
    #
    # Here it is measured AS DRIVEN, the way a row assembled elsewhere is:
    # what came out is what came out.
    if any(spec == RUNTIME_WIDTH
           for _, expr, spec in _shape if expr is not None):
        measured += 1
        _worst = max((r for t in _seen for r in t.split("\n")), key=len)
        if len(_worst) > config.CONSOLE_COLUMNS:
            note("measured at runtime, and driven over")
            over.append((_site, len(_worst), "as driven", _worst))
        else:
            note("measured at runtime, driven inside")
        continue
    _slots = [(expr, spec) for _, expr, spec in _shape if expr is not None]
    _values = [[] for _ in _slots]
    _matched = 0
    for _text in _seen:
        _got = slot_values(_shape, _text[len(PREFIX):]
                           if _is_verdict and _text.startswith(PREFIX)
                           else _text)
        if _got is None:
            continue
        _matched += 1
        for _i, _v in enumerate(_got):
            _values[_i].append(_v)
    if not _matched:
        # The literal parts did not match what was printed, so the values
        # cannot be told from the words. Named rather than dropped: it is a
        # row this reader has an opinion about and could not form one on.
        note("could not be taken apart")
        continue
    _kinds = []
    for _i, (_expr, _spec) in enumerate(_slots):
        _k = widest_for(_expr, _values[_i])
        _kinds.append((_k[0], max(_k[1], pad(_spec))) if _k else None)
    _head = PREFIX if _is_verdict else ""
    _bare, _ = compose(_shape, _head, lambda i: "-" * pad(_slots[i][1]))
    if _bare > config.CONSOLE_COLUMNS:
        # The VTY's own words are already over the console with every value
        # taken out, so no value can make this row fit. #359's walk could not
        # see it because the f-string interpolates.
        note("the words alone are over")
        _, _row = compose(_shape, _head, lambda i: "")
        over.append((_site, _bare, "with every value taken out", _row))
        continue
    if all(_kinds):
        _wide, _row = compose(_shape, _head, lambda i: "X" * _kinds[i][1])
        if _wide > config.CONSOLE_COLUMNS:
            note("bounded, and over at its widest")
            over.append((_site, _wide, " and ".join(k[0] for k in _kinds),
                         _row))
        else:
            note("bounded, and fits at its widest")
        continue
    _tail_only = True
    _at = [i for i, (lit, expr, _) in enumerate(_shape) if expr is not None]
    for _i, _k in enumerate(_kinds):
        if _k:
            continue
        _rest = _shape[_at[_i] + 1:]
        if any(e is not None for _, e, _ in _rest) or \
                "".join(l for l, _, _ in _rest).strip():
            _tail_only = False
    if not _tail_only:
        note("unbounded, with the VTY's own words behind it")
        unsafe.append((_site, [k[0] if k else "no widest" for k in _kinds]))
        continue
    _upto, _row = compose(_shape, _head,
                          lambda i: "X" * (_kinds[i][1] if _kinds[i] else 0))
    if _upto > config.CONSOLE_COLUMNS:
        note("the words before the unbounded value are over")
        over.append((_site, _upto, "up to the unbounded value", _row))
    else:
        note("unbounded, and last on its row")

MEASURED = sum(v for k, v in tally.items() if k != "never printed here")
# Same floor idiom as the two walks in test/test_console_width.py and the same
# reason: a reader that drove nothing would go green on the day the hook
# stopped being installed, which is the shape of failure this whole file
# exists to refuse. It was 357 when this was written.
ROW_FLOOR = 250
if MEASURED < ROW_FLOOR:
    bad(f"only {MEASURED} row-building site(s) printed anything, below the "
        f"floor of {ROW_FLOOR}; the recorder is what is broken, not the "
        f"widths")
else:
    ok(f"{MEASURED} of the {len(PRINTS) + len(VERDICTS)} row-building sites "
       f"in the VTY printed here ({len(VERDICTS)} verdict(s), "
       f"{len(PRINTS)} print(s))")
for _kind in sorted(tally):
    print(f"    {tally[_kind]:4d}  {_kind}")
print(f"  the widest values, read from the product: an IP address is "
      f"{WIDEST['an IP address']} columns")
print(f"  and a prefix {WIDEST['an IP prefix']}, both computed; a decimal is "
      f"{WIDEST['a decimal']}, the widest range this VTY's")
print(f"  own grammar declares; an interface name is "
      f"{WIDEST['an interface name']}, VPP's longest card name")
print(f"  plus a tag out of config.VLAN_RANGE; a closed set is its own widest "
      f"member,")
print(f"  out of the {len(CLOSED)} collections of strings the product "
      f"declares.")

print("\n  NOT MEASURED HERE, and these are the whole of what is not:")
print(f"  {len(unreached)} site(s) printed nothing while this ran, so there "
      f"was no row to")
print("  measure. Most are inside a 'show' that needs a dataplane, which is "
      "the")
print("  labs' half and cannot run in `make test`.")
print(f"  {measured} site(s) build their columns at runtime, from the values "
      f"the box holds,")
print("  and were measured as they were driven. What keeps those inside the "
      "console")
print("  is route_column_widths() and the block it falls back to, which "
      "test_prismash_units.py")
print("  drives at an IPv6 route's widest.")
print(f"  {opaque} site(s) print a row assembled somewhere else - a name, a "
      f"concatenation,")
print("  a '*rest' - and were measured as they were driven, which is a lower "
      "bound")
print("  on their width and not a bound on it.")
print(f"  a decimal is measured at {WIDEST['a decimal']} columns, which is "
      f"the widest NUMBER this box")
print("  prints and not the widest thing an operator can type: 'mtu' followed "
      "by")
print("  fifty digits quotes all fifty back, and that row is not measured "
      "here.")

# THE CEILINGS ARE WHAT REFUSE THE 297th SITE, in the idiom
# UNRENDERED_AT_MOST above already uses and for the same reason: an allowlist
# is read once and grown forever, so what is left is NAMED on every run and
# the number it may not exceed is written down. Every row that gains a fix
# lowers one of these, and they may only go down.
#
# They are not zero, and the ones still counted are the work #363 leaves
# behind it rather than a clean bill. What is fixed in this commit is the
# handful the reader found that nothing else could: rows whose own WORDS are
# over the console, which no static walk sees because the f-string
# interpolates, and the two `neighbour <peer>` verdicts #349 measured on an
# IPv4 peer and left at 87 columns on an IPv6 one.
# 26 until #385 took out the `expected: dataplane management <name> address
# <a.b.c.d/len> mac <xx:xx:...>` refusal, whose own words were over.
# 26 while #384 had driven `commit` over a dataplane answering `remap:` rows
# and prismash.py still passed the plugin's sentences through whole: the row
# saying every session on the pool ends was 178 columns with its indent, and
# no box had driven the site before, so the reader could not count it. #391
# folds each remap row through print_prose, between words, and the box above
# (`plugin_remap_rows`) holds the widest one inside the console; the ceiling
# came back down to 25 with it.
# 22 since #403 took `local0` out of the three interface screens: the note
# under each count - "local0 is listed above but not counted: it is the
# dataplane's own null interface and forwards nothing" - was one of these
# rows, printed from three places, and it went with the row it explained.
OVER_AT_MOST = 22
# 52 until #385 took out the handover headline the prompt printed on a
# one-card box, which carried the interface name with no widest behind it;
# and one less again when #389 gave the `linux-name` refusal at the prompt
# one owner for its sentence, config.linux_ifname_error(), whose value is an
# interface name the reader bounds.
#
# AND 56 SINCE #390: six report rows of `show` screens (a sampling row, a
# no-prefixes row and four route-table rows whose columns are padded to a
# width) had never been produced by any box, so this reader could not count
# them; the unit tests for the Shell's read-only methods drive them now. No
# product line changed with them: measured by running this file with the
# ceiling at 0 on the tree before those tests and on the tree with them, 50
# and 56. Folding the six is #392, and this comes back to 50 with it.
# AND 48 SINCE #392, which is two lower than the 50 that issue names: the six
# rows it is about are folded through print_prose or measured at runtime now,
# and the same reading found two of the older 50 - the interface table's rows,
# whose widths were already measured off the box - were never foldable either.
UNSAFE_AT_MOST = 48


def ceiling(found, allowed, what, detail):
    global fail
    if len(found) > allowed:
        fail += 1
        print(f"  FAIL: {len(found)} {what}, above the ceiling of {allowed}. "
              f"A row that does not fit is read folded at whatever width the "
              f"operator's window happens to be, and the fold lands inside "
              f"the words rather than between them - fix the row, do not "
              f"raise the number:")
        for row in detail:
            print(f"    {row}")
    elif len(found) < allowed:
        fail += 1
        print(f"  FAIL: the ceiling says {allowed} and only {len(found)} "
              f"{what}: lower it, or it stops holding anything")
    else:
        ok(f"{len(found)} {what}, at the ceiling of {allowed}")


over.sort()
unsafe.sort()
ceiling(over, OVER_AT_MOST, "row(s) do not fit the console once driven",
        [f"prismash.py:{n}  {w} columns {why}\n      {row.strip()[:110]!r}"
         for n, w, why, row in over])
ceiling(unsafe, UNSAFE_AT_MOST,
        "row(s) carry a value with no widest and the VTY's own words behind "
        "it",
        [f"prismash.py:{n}  {' | '.join(k)}\n      "
         f"{SOURCE[n - 1].strip()[:100]}" for n, k in unsafe])

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
