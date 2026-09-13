#!/usr/bin/env python3
"""
Naming the card the dataplane forwards on, from the VTY.

Until this existed the only way to give an appliance a card was to edit
HEXA_DATAPLANE_IF in the tuner's file from a Linux shell. Every fresh install
comes up with that field empty - deliberately, because the alternative is VPP
probing and taking whatever NIC it finds, which on a single-NIC box is the
management port - so every fresh install forwards nothing until somebody drops
out of this VTY to an editor.

What is asserted here, and why each one is worth a suite rather than a lab:

  * THE REFUSALS, which are the reason this cannot be a thin wrapper over the
    file. The card carrying the default route is refused, and it is refused AT
    THE PROMPT: the tuner already refuses it at the next boot, in a journal, on
    a box nobody is watching by then, and a refusal discovered after committing
    is one the operator acts on hours later. That mistake costs a site visit.
  * THE FILE IS NOT REWRITTEN. tune.env is hand-editable by design and most of
    it is prose - the paragraph on why the field ships empty, the worker cap
    and the argument for leaving it uncapped. A commit that regenerated the
    file from the settings this VTY models would delete all of it.
  * COMMIT DOES NOT RESTART VPP. Handing a card to the dataplane ends every
    session on the box, including the operator's own. The commit stops at that
    boundary and says where the boundary is.

Every path the product reads is driven against a fake tree in a tempdir
through the seams the product already has - SYS_CLASS_NET, PROC_NET_ROUTE,
TUNE_ENV - rather than by stubbing the method under test. A check that only
runs on the box it protects is a check nobody has seen fire.
"""
import contextlib
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "hexash"))
import config                                                   # noqa: E402
import hexash                                                   # noqa: E402
import system                                                   # noqa: E402
import vppapi                                                   # noqa: E402

from config import CONSOLE_COLUMNS                              # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


# The tuner's file as the appliance ships it, comments and all. Copied from
# appliance/etc/tune.env rather than invented: what this suite is protecting is
# that a commit leaves the other settings AND the prose exactly where they are,
# and a fixture with no prose in it cannot notice them being lost.
TUNE_ENV_SHIPPED = """\
# Dataplane interface: the one carrying subscriber traffic. Names the device
# handed to VPP and chooses the NUMA node for the workers.
#
# EMPTY ON PURPOSE. It used to ship as "eth0", which does not exist on a Debian
# 12 box with predictable interface names.
#
# The interface carrying the default route is refused even if named here.
HEXA_DATAPLANE_IF=

# Subscriber target: sizes the hugepages.
HEXA_SUBSCRIBERS=200000

# Low latency: disables deep C-states and transparent hugepages.
HEXA_LOW_LATENCY=1

# Worker cap: the most physical cores handed to VPP as forwarding workers.
HEXA_MAX_WORKERS=
"""

#: /proc/net/route as Linux writes it. The default route is the row whose
#: destination column is all zeroes, and there is a second row that is NOT the
#: default so the reader cannot pass by taking the first line it finds.
PROC_NET_ROUTE = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask"
    "\t\tMTU\tWindow\tIRTT\n"
    "enp2s0\t0002A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n"
    "enp1s0\t00000000\t0102A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
)

#: The cards the fake box has. enp1s0 is the management port - it carries the
#: default route above - and enp2s0 is the one subscribers are on.
CARDS = ("enp1s0", "enp2s0", "lo")


class FakeApi:
    def available(self):
        return (vppapi.OK, "stub")

    def call(self, message, args):
        return vppapi.Reply(vppapi.OK)

    def dump(self, message, args, timeout=15):
        return vppapi.Reply(vppapi.OK, fields={"rows": []})


class FakeVpp:
    """A dataplane with nothing on it, which is the state being repaired."""

    TRACE = False

    def __init__(self):
        self.commands = []

    def reachable(self):
        return True

    def run(self, cmd):
        self.commands.append(cmd)
        return 0, ""

    def running(self):
        return 0, ""


def make_box(tmp, route=PROC_NET_ROUTE, tune=TUNE_ENV_SHIPPED, cards=CARDS):
    """
    A fake box on disk: the cards Linux has, its routing table, the tuner's
    file. Written once per test so one test cannot see another's edits.
    """
    for name in cards:
        os.makedirs(f"{tmp}/sys/class/net/{name}", exist_ok=True)
    os.makedirs(f"{tmp}/etc/hexarouting", exist_ok=True)
    with open(f"{tmp}/net_route", "w") as f:
        f.write(route)
    if tune is not None:
        with open(f"{tmp}/etc/hexarouting/tune.env", "w") as f:
            f.write(tune)
    elif os.path.exists(f"{tmp}/etc/hexarouting/tune.env"):
        os.unlink(f"{tmp}/etc/hexarouting/tune.env")


def shell_on(tmp, **kw):
    """
    A Shell pointed at the fake box through the product's own seams.

    Nothing about the dataplane-interface path is stubbed: the refusals, the
    read-back and the write all run against the tree above. What IS stubbed is
    everything around them - the dataplane API, FRR, sshd - because those are
    other suites' subject and a real one would make this file need a box.
    """
    make_box(tmp, **kw)
    sh = hexash.Shell()
    # Stated outright rather than inherited from whoever runs the suite (#35).
    sh.caps = {hexash.CAP_ALL, hexash.CAP_CGNAT}
    sh.api = FakeApi()
    sh.dp = FakeVpp()
    sh.ran = []

    def runner(argv, stdin=None, timeout=30):
        sh.ran.append(list(argv))
        if argv[0] == "systemctl":
            return 0, ""
        return 127, f"{argv[0]}: not found"

    sh.sys = system.System(runner=runner, root=tmp)
    sh.frr = type("F", (hexash.Frr,), {
        "available": lambda self: False,
        "show": lambda self, c: (1, ""),
        "running": lambda self: [],
        "reload": lambda self, l, check_only=False: (0, "")})()
    sh.SYS_CLASS_NET = f"{tmp}/sys/class/net"
    sh.PROC_NET_ROUTE = f"{tmp}/net_route"
    sh.TUNE_ENV = f"{tmp}/etc/hexarouting/tune.env"
    sh.rb = hexash.Rollback(path=f"{tmp}/rollback")
    sh.confirm = hexash.ConfirmPending(path=f"{tmp}/confirm")
    return sh


def drive(sh, lines):
    """Type `lines` at the VTY and keep everything the box printed."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        for line in lines:
            sh.feed(line)
    return out.getvalue()


# ------------------------------------------------------------------ grammar

print("=== the setting goes into the candidate and is rendered back ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    said = drive(sh, ["configure", "dataplane interface enp2s0"])
    check(sh.candidate is not None and sh.candidate.dataplane_if == "enp2s0",
          f"the card did not reach the candidate: {said!r}")
    check("error" not in said,
          f"a card this box has and does not route over was refused: {said!r}")
    text = sh.candidate.text()
    check("dataplane interface enp2s0" in text.splitlines(),
          f"the candidate does not render the statement back: {text!r}")
    print(f"  candidate holds {sh.candidate.dataplane_if!r} and renders it")

print("\n=== and survives the round trip through the configuration file ===")
# The file hexarouting.service replays at every boot. A statement that renders
# and does not parse is one the box refuses to load at the next reboot, on a
# machine nobody is watching.
_cfg = config.Config()
_cfg.dataplane_if = "enp2s0"
_back = config.Config.parse(_cfg.text())
check(_back.dataplane_if == "enp2s0",
      f"rendered and not parsed back: {_back.dataplane_if!r}")
check(not _back.unknown,
      f"the box's own rendering is not understood by its own parser: "
      f"{_back.unknown!r}")
check(_back.text() == _cfg.text(),
      f"the second rendering differs from the first: {_back.text()!r}")
_rendered = [l for l in _cfg.text().splitlines() if "dataplane" in l]
print(f"  {(_rendered[0] if _rendered else '(nothing)')!r} renders, parses, "
      f"renders")

print("\n=== 'no dataplane interface' takes it out again ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    drive(sh, ["configure", "dataplane interface enp2s0",
               "no dataplane interface"])
    check(sh.candidate.dataplane_if == "",
          f"the statement could be set and not removed: "
          f"{sh.candidate.dataplane_if!r}")
    check("dataplane" not in sh.candidate.text(),
          f"removed from the candidate and still in the file it writes: "
          f"{sh.candidate.text()!r}")
    print("  set, removed, and gone from the rendered configuration")

print("\n=== a statement this VTY cannot read is refused, not stored ===")
_bad = config.Config.parse("dataplane interface\n")
check(_bad.unknown and _bad.dataplane_if == "",
      f"'dataplane interface' with no name was accepted: {_bad.unknown!r}, "
      f"{_bad.dataplane_if!r}")
_bad2 = config.Config.parse("dataplane nic enp2s0\n")
check(_bad2.unknown and _bad2.dataplane_if == "",
      f"a word this grammar does not have was accepted: {_bad2.unknown!r}")
print(f"  refused with: {_bad.unknown[0][2]!r}")

# ----------------------------------------------------------------- refusals

print("\n=== the card carrying the default route is refused AT THE PROMPT ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    said = drive(sh, ["configure", "dataplane interface enp1s0"])
    check("error:" in said,
          f"the management card was accepted at the prompt: {said!r}")
    check("dataplane interface enp1s0" in said,
          f"the refusal does not quote the name that was refused: {said!r}")
    check("default route" in said,
          f"the refusal does not say WHY, so the operator reads it as the "
          f"box being unable rather than unwilling: {said!r}")
    check("off the network" in said,
          f"the refusal does not say what it would have cost: {said!r}")
    check(sh.candidate.dataplane_if == "",
          f"refused and stored anyway, so 'commit' would still hand over the "
          f"management card: {sh.candidate.dataplane_if!r}")
    print("  refused, named, and kept out of the candidate")

print("\n=== and again at commit, for the paths that never see the prompt ===")
# `rollback` to a revision written when this box had a different card, and
# `--load` of the file hexarouting.service replays at boot, both reach apply()
# without passing do_config. Driven through apply() rather than the prompt for
# exactly that reason.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    target = config.Config.parse("dataplane interface enp1s0\n")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ok = sh.apply(target, check_only=True)
    said = out.getvalue()
    check(ok is False,
          f"a configuration naming the management card passed validation: "
          f"{said!r}")
    check("default route" in said,
          f"and it was refused without saying why: {said!r}")
    print("  a file naming the management card does not validate")

print("\n=== a card this box has not got is refused, and it says what it has "
      "===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    said = drive(sh, ["configure", "dataplane interface eth9"])
    check("error:" in said and "no such interface" in said,
          f"a name the box does not have was accepted: {said!r}")
    for card in CARDS:
        check(card in said,
              f"the refusal does not name {card!r} among what this box has, "
              f"so it says no without saying what yes would be: {said!r}")
    check(sh.candidate.dataplane_if == "",
          f"refused and stored anyway: {sh.candidate.dataplane_if!r}")
    print(f"  refused, and listed all {len(CARDS)} cards the box has")

print("\n=== a box whose card list cannot be read refuses nothing ===")
# A VTY that becomes unusable where it cannot look is a VTY that cannot be used
# to fix the box. The same posture the interface check takes with the dataplane
# unreachable: SKIPPED, not failed.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    sh.SYS_CLASS_NET = f"{tmp}/nothing-here"
    said = drive(sh, ["configure", "dataplane interface enp2s0"])
    check(sh.candidate.dataplane_if == "enp2s0",
          f"the check refused a name because it could not look: {said!r}")
    print("  cannot look is not 'the box has none'")

print("\n=== and the default route is read through its own seam ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    check(sh.default_route_interface() == "enp1s0",
          f"the default route was read as {sh.default_route_interface()!r}, "
          f"not from the row whose destination is all zeroes")
    sh.PROC_NET_ROUTE = f"{tmp}/no-such-file"
    check(sh.default_route_interface() == "",
          "a routing table that cannot be read must answer 'cannot say', not "
          "raise")
    said = drive(sh, ["configure", "dataplane interface enp1s0"])
    check(sh.candidate.dataplane_if == "enp1s0",
          f"with no routing table to read, the refusal fired anyway: {said!r}")
    print("  read from /proc/net/route, destination 00000000")

# -------------------------------------------------------------------- commit

print("\n=== commit writes the tuner's input and changes nothing else ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    before = open(sh.TUNE_ENV).read()
    said = drive(sh, ["configure", "dataplane interface enp2s0", "commit"])
    check("commit complete" in said, f"the commit did not complete: {said!r}")
    after = open(sh.TUNE_ENV).read()
    check("HEXA_DATAPLANE_IF=enp2s0" in after.splitlines(),
          f"the tuner's input was not set: {after!r}")
    # EVERY other line, in order, byte for byte. Not "the other settings are
    # still there": what a regenerated file loses first is the prose, and a
    # check that only looked at the settings would pass over a file with every
    # comment stripped out of it.
    expect = [l if not l.startswith("HEXA_DATAPLANE_IF=")
              else "HEXA_DATAPLANE_IF=enp2s0" for l in before.splitlines()]
    check(after.splitlines() == expect,
          f"the file was rewritten rather than edited; a hand-editable file "
          f"full of comments lost them: {after!r}")
    _was, _now = before.count("#"), after.count("#")
    check(_was == _now, f"comment markers were lost: {_was} -> {_now}")
    print(f"  one line changed, {after.count(chr(10))} lines and "
          f"{after.count('#')} comment markers intact")

print("\n=== commit runs the tuner, and does NOT restart the dataplane ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    said = drive(sh, ["configure", "dataplane interface enp2s0", "commit"])
    check(["systemctl", "restart", "hexarouting-tune"] in sh.ran,
          f"the tuner was never run, so startup.conf still names no device "
          f"and the operator would have to run it themselves: {sh.ran!r}")
    # THE assertion this whole design turns on. Handing a card to the
    # dataplane ends every session on the box, the operator's included. That
    # is the same class of consequence as `commit remap`, which this VTY
    # refuses until it is asked for by name - and no word has been invented
    # for this one, because nothing here has measured what it costs.
    restarts = [c for c in sh.ran
                if c[:2] == ["systemctl", "restart"] and "vpp" in c[-1]]
    check(restarts == [],
          f"commit restarted the dataplane by itself, ending every session on "
          f"the box: {restarts!r}")
    check(not [c for c in sh.dp.commands if "restart" in c or "quit" in c],
          f"commit sent the dataplane something that stops it: "
          f"{sh.dp.commands!r}")
    print(f"  tuner run once, dataplane untouched ({len(sh.ran)} command(s))")

print("\n=== and it says what is still needed, and what that costs ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    said = drive(sh, ["configure", "dataplane interface enp2s0", "commit"])
    check("NOT IN EFFECT YET" in said,
          f"the commit reported success over a card the dataplane has not "
          f"got, which is the sentence the operator builds the next hour of "
          f"diagnosis on: {said!r}")
    check("does not restart" in said,
          f"it does not say that the restart is outstanding: {said!r}")
    check("ends every session" in said,
          f"it does not say what the restart costs, so an operator does it "
          f"at once and finds out: {said!r}")
    check("reboot" in said,
          f"it does not say how to make it take effect: {said!r}")
    print("  says: stored, not in effect, what it costs, what to do")

print("\n=== nothing on that screen wraps on an 80-column console ===")
# The console an operator opens when the box forwards nothing is 80 columns,
# and these are the lines they read on it. Measured rather than wrapped by eye:
# wrapping by eye is how this repository shipped an 82-column line and a
# 178-column one under the belief that both fitted (#252).
_measured = []
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    _measured.append(drive(sh, ["configure", "dataplane interface enp1s0"]))
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    _measured.append(drive(sh, ["configure", "dataplane interface eth9"]))
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    _measured.append(drive(sh, ["configure", "dataplane interface enp2s0",
                                "commit"]))
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    _measured.append(drive(sh, ["configure", "dataplane interface enp2s0",
                                "commit", "no dataplane interface", "commit"]))
_wide = [l for out in _measured for l in out.splitlines()
         if len(l) > CONSOLE_COLUMNS]
check(not _wide, f"these wrap on a serial console: "
                 f"{[(len(l), l) for l in _wide]}")
print(f"  widest line these printed: "
      f"{max(len(l) for out in _measured for l in out.splitlines())} columns")

# A name as long as Linux will carry, so the widest this refusal can ever
# measure is measured rather than assumed. IFNAMSIZ is 16 with the terminator,
# so fifteen characters is the longest name that can reach here at all.
_LONGEST = "enp129s0f1np15"[:15] + "x"
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    said = drive(sh, ["configure", f"dataplane interface {_LONGEST}"])
    _lines = [l for l in said.splitlines() if l.startswith("error:")]
    check(_lines and len(_lines[0]) <= CONSOLE_COLUMNS,
          f"the refusal headline is {len(_lines[0]) if _lines else 0} columns "
          f"for the longest name Linux can carry: {_lines!r}")
    print(f"  the longest name Linux can carry refuses at "
          f"{len(_lines[0]) if _lines else 0} columns")

# ------------------------------------------------------------- the read-back

print("\n=== the running configuration is READ from the box, not remembered "
      "===")
# A box set up the old way - somebody edited the file, because that was the
# only way there was - must show its own card back. A VTY that reports what it
# last wrote is a VTY that cannot notice being wrong.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, tune=TUNE_ENV_SHIPPED.replace(
        "HEXA_DATAPLANE_IF=", "HEXA_DATAPLANE_IF=enp2s0"))
    running = sh.running_config()
    check(running.dataplane_if == "enp2s0",
          f"a card set by hand in the file is invisible to the VTY: "
          f"{running.dataplane_if!r}")
    said = drive(sh, ["show running-config | include dataplane"])
    check("dataplane interface enp2s0" in said,
          f"'show running-config' does not show the box's own card: {said!r}")
    print("  a hand-edited file shows up in 'show running-config'")

print("\n=== so a commit that changes nothing writes nothing ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, tune=TUNE_ENV_SHIPPED.replace(
        "HEXA_DATAPLANE_IF=", "HEXA_DATAPLANE_IF=enp2s0"))
    said = drive(sh, ["configure", "dataplane interface enp2s0", "commit"])
    check("no changes to commit" in said,
          f"setting the card the box already has was treated as a change, so "
          f"every commit would re-run the tuner: {said!r}")
    check(["systemctl", "restart", "hexarouting-tune"] not in sh.ran,
          f"and it ran the tuner for a change nobody made: {sh.ran!r}")
    print("  idempotent: the box already had it, so nothing ran")

print("\n=== and 'save' writes it to the file the box replays at boot ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, tune=TUNE_ENV_SHIPPED.replace(
        "HEXA_DATAPLANE_IF=", "HEXA_DATAPLANE_IF=enp2s0"))
    _was = hexash.CONFIG_PATH
    hexash.CONFIG_PATH = f"{tmp}/etc/hexarouting/hexarouting.conf"
    try:
        drive(sh, ["save"])
        saved = open(hexash.CONFIG_PATH).read()
    finally:
        hexash.CONFIG_PATH = _was
    check("dataplane interface enp2s0" in saved.splitlines(),
          f"'save' wrote a file that does not name the box's card, so the "
          f"next boot replays a configuration missing it: {saved!r}")
    check(not config.Config.parse(saved).unknown,
          f"and the file it wrote is not one it can read back: "
          f"{config.Config.parse(saved).unknown!r}")
    print("  saved, and the saved file parses clean")

# ----------------------------------------------------------- the file itself

print("\n=== a tuner file with no such setting in it gains one ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, tune="# nothing but a comment\nHEXA_SUBSCRIBERS=10\n")
    drive(sh, ["configure", "dataplane interface enp2s0", "commit"])
    after = open(sh.TUNE_ENV).read()
    check("HEXA_DATAPLANE_IF=enp2s0" in after.splitlines(),
          f"a file that never had the setting did not gain it: {after!r}")
    check("HEXA_SUBSCRIBERS=10" in after.splitlines(),
          f"and the settings it did have were lost: {after!r}")
    print("  appended, and what was there stayed")

print("\n=== a box with no tuner file at all is still configurable ===")
# Not an appliance, or an appliance whose file was removed. Refusing here
# would leave the operator with no way to proceed from the VTY, which is the
# defect being repaired rather than a safe default.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, tune=None)
    said = drive(sh, ["configure", "dataplane interface enp2s0", "commit"])
    check("commit complete" in said,
          f"a box with no tuner file could not be given a card: {said!r}")
    check(sh.read_dataplane_if() == "enp2s0",
          f"and the file it wrote does not read back: "
          f"{sh.read_dataplane_if()!r}")
    print("  a file was written, and reads back")

print("\n=== a file that cannot be written fails the commit, and says so ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    sh.TUNE_ENV = f"{tmp}/etc/hexarouting"      # a directory, not a file
    said = drive(sh, ["configure", "dataplane interface enp2s0", "commit"])
    check("commit complete" not in said,
          f"the write failed and the commit reported success: {said!r}")
    check("error:" in said and "dataplane interface" in said,
          f"it failed without saying what could not be done: {said!r}")
    print("  refused, and named")

print("\n=== and one that cannot be REPLACED fails it too ===")
# Not the same branch as the one above, and the difference is which end of the
# operation broke. There the file could not be READ; here it reads fine and the
# rename cannot land, which is the case a full disk or a wrong mode produces on
# a real box - and the one a fixture that only breaks the read passes straight
# over.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    os.makedirs(sh.TUNE_ENV + ".new")
    said = drive(sh, ["configure", "dataplane interface enp2s0", "commit"])
    check("commit complete" not in said,
          f"the file could not be replaced and the commit reported success, "
          f"so the box would boot on the old card: {said!r}")
    check(sh.read_dataplane_if() == "",
          f"and it claimed a card the file does not hold: "
          f"{sh.read_dataplane_if()!r}")
    print("  refused, and the file is untouched")


print("\n=== the LAST assignment wins, as systemd reads it ===")
# EnvironmentFile semantics. The boot self-test already reports the last one,
# and two answers to "which card is this box set to" is one that can disagree
# with the box's own diagnosis.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, tune="HEXA_DATAPLANE_IF=enp1s0\n"
                            "HEXA_DATAPLANE_IF=enp2s0\n")
    check(sh.read_dataplane_if() == "enp2s0",
          f"read as {sh.read_dataplane_if()!r}, which is not what the unit "
          f"would use")
    print("  last assignment wins")

print("\n=== a quoted value reads back as the name, not as the quotes ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, tune='HEXA_DATAPLANE_IF="enp2s0"\n')
    check(sh.read_dataplane_if() == "enp2s0",
          f"read as {sh.read_dataplane_if()!r}, so a quoted file would look "
          f"like a change on every commit")
    print("  quotes stripped")

print("\n=== a commented-out setting is not a setting ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, tune="# HEXA_DATAPLANE_IF=enp1s0\nHEXA_SUBSCRIBERS=1\n")
    check(sh.read_dataplane_if() == "",
          f"a commented line was read as configuration: "
          f"{sh.read_dataplane_if()!r}")
    print("  a comment stays a comment")

# ------------------------------------------------------------ the completion

print("\n=== '?' offers the cards the box has, and marks the one refused ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    drive(sh, ["configure"])
    offered = sh.dynamic_options(hexash.ARG_HOST_NIC, "")
    check(set(offered) == set(CARDS),
          f"completion offers {sorted(offered)}, not the cards Linux has")
    check("default route" in offered.get("enp1s0", ""),
          f"the one card that must not be chosen is offered like any other: "
          f"{offered!r}")
    print(f"  offers {', '.join(sorted(offered))}, and marks enp1s0")

print("\n=== the tree's marker has a source behind it ===")
# A marker added to the tree with nothing in DYNAMIC answers "<nic>" and
# nothing else, which is the bare placeholder this VTY exists to replace.
check(hexash.ARG_HOST_NIC in hexash.Shell.DYNAMIC,
      "the argument marker has no source, so '?' offers the placeholder")
_source = hexash.Shell.DYNAMIC.get(hexash.ARG_HOST_NIC, "")
check(_source and hasattr(hexash.Shell, _source),
      f"DYNAMIC names {_source!r}, which is not a method on the Shell")
print(f"  {hexash.ARG_HOST_NIC} -> {_source or '(nothing)'}")

print("\n=== the change is an administrator's, not a CGNAT operator's ===")
# Handing a card to the dataplane is not a CGNAT setting, and a cgnat-operator
# account that could do it could take the box off the network.
_d = config.Config().delta_to(config.Config.parse(
    "dataplane interface enp2s0\n"))
check(_d["dataplane_if_changed"] is True,
      f"the delta does not report the change at all, so nothing would apply "
      f"it: {_d['dataplane_if_changed']!r}")
check(hexash.cap_needed(_d) == hexash.CAP_ALL,
      f"this change needs {hexash.cap_needed(_d)!r}, so an account that may "
      f"only configure CGNAT could hand over the card")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    sh.caps = {hexash.CAP_CGNAT}
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ok = sh.apply(config.Config.parse("dataplane interface enp2s0\n"))
    check(ok is False and "permission denied" in out.getvalue(),
          f"a CGNAT operator applied it: {out.getvalue()!r}")
    check(sh.read_dataplane_if() == "",
          f"refused, and the file was written anyway: "
          f"{sh.read_dataplane_if()!r}")
    print("  refused for a cgnat-operator account, and nothing was written")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
