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
    #: What the dataplane is pretending to hold, by name. Empty is a fresh
    #: appliance, which is where most of this suite runs.
    def __init__(self, have=()):
        self.have = {n: i + 1 for i, n in enumerate(have)}
        self.calls = []

    def available(self):
        return (vppapi.OK, "stub")

    def interface_index(self, name):
        return self.have.get(name)

    def call(self, message, args):
        self.calls.append((message, args))
        return vppapi.Reply(vppapi.OK)

    def dump(self, message, args, timeout=15):
        return vppapi.Reply(vppapi.OK, fields={"rows": []})


class FakeVpp:
    """A dataplane with nothing on it, which is the state being repaired."""

    TRACE = False

    def __init__(self, answers=None):
        self.commands = []
        self.answers = answers or {}

    def reachable(self):
        return True

    def run(self, cmd):
        self.commands.append(cmd)
        for prefix, text in self.answers.items():
            if cmd.startswith(prefix):
                return 0, text
        return 0, ""

    def running(self):
        return 0, ""


def make_box(tmp, route=PROC_NET_ROUTE, tune=TUNE_ENV_SHIPPED, cards=CARDS):
    """
    A fake box on disk: the cards Linux has, its routing table, the tuner's
    file. Written once per test so one test cannot see another's edits.
    """
    # A CARD, not just a name under /sys/class/net. The candidates are the
    # ones with a PCI address, because that is what DPDK is handed - so `lo`,
    # a bridge and a virtio guest's interfaces are not offers this box could
    # honour. host_nics() applies that test, and the fake tree has to be
    # shaped the way the real one is or the suite measures a different
    # question.
    for i, name in enumerate(cards):
        os.makedirs(f"{tmp}/sys/class/net/{name}", exist_ok=True)
        if name == "lo":
            continue
        addr = f"0000:0{i + 1}:00.0"
        dev = f"{tmp}/sys/bus/pci/devices/{addr}"
        os.makedirs(dev, exist_ok=True)
        link = f"{tmp}/sys/class/net/{name}/device"
        if not os.path.lexists(link):
            os.symlink(dev, link)
        # The MAC, because a card that is handed over keeps it and it is the
        # only name the card has on both sides of the handover (#303). A fake
        # tree without one would make every single-card box refuse for a
        # reason the real tree never has.
        with open(f"{tmp}/sys/class/net/{name}/address", "w") as f:
            f.write(f"52:54:00:00:00:0{i + 1}\n")
    os.makedirs(f"{tmp}/etc/hexarouting", exist_ok=True)
    with open(f"{tmp}/net_route", "w") as f:
        f.write(route)
    if tune is not None:
        with open(f"{tmp}/etc/hexarouting/tune.env", "w") as f:
            f.write(tune)
    elif os.path.exists(f"{tmp}/etc/hexarouting/tune.env"):
        os.unlink(f"{tmp}/etc/hexarouting/tune.env")


#: What `ip -4 -o addr show dev <name>` says on the fake box. The one reading
#: in this path that is not a file: Linux publishes an interface's own address
#: nowhere under /sys or /proc in a form that names the interface, so the
#: product asks `ip` through its runner - and that runner is what this answers.
IP_ADDRS = {
    "enp1s0": "2: enp1s0    inet 192.168.2.50/24 brd 192.168.2.255 "
              "scope global enp1s0\\       valid_lft forever",
    "enp2s0": "3: enp2s0    inet 10.0.0.1/24 scope global enp2s0\\       "
              "valid_lft forever",
    "ens18": "2: ens18    inet 10.95.200.198/24 brd 10.95.200.255 "
             "scope global ens18\\       valid_lft forever",
}


def shell_on(tmp, addrs=None, dp=None, api=None, **kw):
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
    sh.api = api or FakeApi()
    sh.dp = dp or FakeVpp()
    sh.ran = []
    have_addrs = IP_ADDRS if addrs is None else addrs

    def runner(argv, stdin=None, timeout=30):
        sh.ran.append(list(argv))
        if argv[0] == "systemctl":
            return 0, ""
        if argv[:5] == ["ip", "-4", "-o", "addr", "show"]:
            said = have_addrs.get(argv[-1])
            return (0, said) if said is not None else (1, "Device not found")
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
    # The CARDS, not every name under /sys/class/net. `lo` is in the fake box
    # because a real one has it, and it is deliberately not among the offers:
    # the dataplane is handed a PCI address, and offering a name that cannot
    # have one is an offer the box could not honour.
    check("lo" not in said.split("the cards it has:")[1].split("\n")[0],
          f"the refusal offers 'lo' as a card the dataplane could be given, "
          f"and it has no PCI address: {said!r}")
    for card in [c for c in CARDS if c != "lo"]:
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
# ONE reader, and it is `show hardware`'s. This suite was written against a
# default_route_interface() of its own; two answers to "which card carries the
# default route" is the pair that comes to disagree, and the way it would
# disagree here is by refusing one card at the prompt while the listing marks
# a different one as the dangerous one. "Cannot say" is None rather than "",
# which is the same thing to every caller: a card's name is never either.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    check(sh.default_route_nic() == "enp1s0",
          f"the default route was read as {sh.default_route_nic()!r}, "
          f"not from the row whose destination is all zeroes")
    sh.PROC_NET_ROUTE = f"{tmp}/no-such-file"
    check(sh.default_route_nic() is None,
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
    # The PCI cards, and not `lo`: completion is an offer, and offering a name
    # the dataplane cannot be given is one the box would then refuse.
    check(set(offered) == {c for c in CARDS if c != "lo"},
          f"completion offers {sorted(offered)}, not the cards this box could "
          f"actually hand to the dataplane")
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


# ------------------------------------------------------ a box with ONE card
#
# #303. The refusal above is right about every box that has somewhere else to
# put the dataplane, and on a box with one card its advice - "name the card
# carrying subscriber traffic instead" - names a card that does not exist. The
# box could not forward and could not be told to.
#
# What is asserted here is the way out and its safety, and the two are one
# thing: the card may move only because the box writes down what has to follow
# it, and only under a commit that undoes itself if nobody confirms.

#: One card, and it carries the default route because it is the only one there
#: is. This is the maintainer's box in #303, in a tempdir.
ONE_CARD = ("ens18", "lo")
ONE_ROUTE = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask"
    "\t\tMTU\tWindow\tIRTT\n"
    "ens18\t00C85F0A\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n"
    "ens18\t00000000\t01C85F0A\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
)
#: What `show hardware-interfaces` says once the card HAS moved, which is the
#: state the boot after the handover is in. The name is DPDK's and shares
#: nothing with the Linux one; the MAC is the same on both sides, which is why
#: it is what the two are matched on.
DP_HARDWARE = """\
              Name                Idx   Link  Hardware
GigabitEthernet0/3/0               1     up   GigabitEthernet0/3/0
  Ethernet address 52:54:00:00:00:01
  Intel 82599
local0                             0    down  local0
  local
"""


def one_card_box(tmp, frr=True, **kw):
    """
    The box above, with an FRR that answers by default.

    The rest of this suite runs against an FRR that is not there, because
    nothing else in it configures routing. The handover does: the default
    route on the card being given away has to be replaced, and a commit that
    carries a static route is refused outright on a box whose staticd is not
    running (#184). An FRR-less stub here would make every case below fail for
    that and say nothing about #303.
    """
    sh = shell_on(tmp, route=ONE_ROUTE, cards=ONE_CARD, **kw)
    if frr:
        # It KEEPS what it is given, because the commit reads FRR back
        # afterwards and reports any statement FRR did not keep (#184). A stub
        # that forgot every reload would report the handover's own default
        # route as lost on a box where nothing was wrong.
        class OkFrr(hexash.Frr):
            def __init__(self):
                self.held = []

            def available(self):
                return True

            def daemons(self):
                return {"zebra", "mgmtd", "staticd"}

            def show(self, cmd):
                return (1, "")

            def running(self):
                return list(self.held)

            def outstanding(self, out):
                return []

            def diagnostics(self, out):
                return []

            def reload(self, lines, check_only=False):
                if not check_only:
                    self.held = list(lines)
                return (0, "")

        sh.frr = OkFrr()
    return sh


print("\n=== a box with ONE card is not refused: the card takes its "
      "management with it (#303) ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = one_card_box(tmp)
    said = drive(sh, ["configure", "dataplane interface ens18"])
    check("error:" not in said,
          f"the only card a box has was refused, which is the dead end #303 "
          f"is about: {said!r}")
    check(sh.candidate.dataplane_if == "ens18",
          f"accepted and not stored: {sh.candidate.dataplane_if!r}")
    check(sh.candidate.dataplane_mgmt == {
              "name": "ens18", "address": "10.95.200.198/24",
              "mac": "52:54:00:00:00:01"},
          f"the management path the card carries was not written down, so the "
          f"box would come up with the card in the dataplane and no address "
          f"anywhere: {sh.candidate.dataplane_mgmt!r}")
    check("ip route 0.0.0.0/0 10.95.200.1" in sh.candidate.routing,
          f"the default route lives on the card Linux is about to lose and "
          f"nothing replaced it: {sh.candidate.routing!r}")
    check("10.95.200.198/24" in said,
          f"the box did not say which address it is carrying over, which is "
          f"the one fact the operator has to check before committing: {said!r}")
    # The reboot is the whole of the risk and it is stated at the prompt, not
    # discovered at the restart.
    check("restarted" in said,
          f"it does not say the card moves at a restart and not at this "
          f"commit: {said!r}")
    print("  accepted, with the address, the MAC and the route written down")

print("\n=== and the refusal stands wherever the advice can be followed ===")
# The two-card box is refused exactly as before - see the refusal section
# above, which is unchanged - and now it names the card to use instead rather
# than describing it.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    said = drive(sh, ["configure", "dataplane interface enp1s0"])
    check("error:" in said and "enp2s0" in said,
          f"the refusal tells the operator to name the subscriber card and "
          f"does not say which one that is: {said!r}")
    check(not sh.candidate.dataplane_mgmt,
          f"a box with a spare card recorded a management path it will never "
          f"need: {sh.candidate.dataplane_mgmt!r}")
    print("  refused, and the card to name instead is named")

print("\n=== a card whose management state cannot be carried is refused ===")
# An address this box cannot read is an address it cannot put back, and a
# handover it cannot undo on the far side is the walk to the console the
# original refusal exists to prevent.
with tempfile.TemporaryDirectory() as tmp:
    sh = one_card_box(tmp, addrs={})
    said = drive(sh, ["configure", "dataplane interface ens18"])
    check("error:" in said and "cannot be carried over" in said,
          f"a card with no address was handed over anyway: {said!r}")
    check("no single global IPv4 address" in said,
          f"and the refusal does not say WHICH part is missing: {said!r}")
    check(sh.candidate.dataplane_if == "" and not sh.candidate.dataplane_mgmt,
          f"refused and stored anyway: {sh.candidate.dataplane_if!r}")
    print("  refused, and it says what is missing")

with tempfile.TemporaryDirectory() as tmp:
    # Two addresses is the same answer. Choosing between them is choosing
    # which address the box comes back on, and a box that guesses that wrong
    # comes back where nobody is looking for it.
    sh = one_card_box(tmp, addrs={
        "ens18": IP_ADDRS["ens18"] + "\n2: ens18    inet 10.0.9.9/24 scope "
                                     "global secondary ens18"})
    said = drive(sh, ["configure", "dataplane interface ens18"])
    check("error:" in said,
          f"a card with two addresses was handed over on a guess: {said!r}")
    print("  and a card with two addresses is not guessed at")

print("\n=== the record is written where the box can read it back ===")
# It cannot live only in the saved file. `show running-config` and `save`
# build the running state by reading the BOX, and between the commit that
# names the card and the boot that hands it over there is nothing on the box
# to read: the dataplane has no such interface yet and Linux still holds the
# address on a card that is about to stop existing. Saved from a fresh
# session, the file came out naming the card with no way to keep the box
# reachable - which is the one file where that mistake cannot be recovered.
with tempfile.TemporaryDirectory() as tmp:
    sh = one_card_box(tmp)
    said = drive(sh, ["configure", "dataplane interface ens18", "commit"])
    check("commit complete" in said, f"the commit did not complete: {said!r}")
    tune = open(sh.TUNE_ENV).read()
    check("HEXA_DATAPLANE_MGMT=ens18 10.95.200.198/24 52:54:00:00:00:01"
          in tune,
          f"the management path is not in the tuner's file, so a later "
          f"session reading the box finds the card named and nothing to keep "
          f"it reachable: {tune!r}")
    check(sh.read_dataplane_mgmt() == sh.candidate.dataplane_mgmt,
          f"written and not read back the same: {sh.read_dataplane_mgmt()!r}")
    # And the prose the file ships with is still there, which is the rule the
    # card's own setting already has.
    check("EMPTY ON PURPOSE" in tune,
          f"the file was rewritten from the settings this VTY models: {tune!r}")
    print("  in tune.env, beside the card, and read back the same")

print("\n=== rendered, and parsed back, so a saved file replays it ===")
_cfg = config.Config()
_cfg.dataplane_if = "ens18"
_cfg.dataplane_mgmt = {"name": "ens18", "address": "10.95.200.198/24",
                       "mac": "52:54:00:00:00:01"}
_back = config.Config.parse(_cfg.text())
check(_back.dataplane_mgmt == _cfg.dataplane_mgmt,
      f"rendered and not parsed back: {_back.dataplane_mgmt!r}")
check(not _back.unknown,
      f"the box renders a line it then refuses to load, which is a box that "
      f"cannot boot on its own file: {_back.unknown!r}")
_bad = config.Config.parse(
    "dataplane management ens18 address 10.95.200.198 mac 52:54:00:00:00:01\n")
check(_bad.unknown and not _bad.dataplane_mgmt,
      f"an address with no prefix length was accepted, and the box cannot "
      f"tell which addresses are on its own subnet: {_bad.unknown!r}")
print(f"  round-trips, and refuses: {_bad.unknown[0][2]!r}")

print("\n=== the gateway makes its own next hop connected ===")
# Otherwise the default route that comes with the handover has a next hop
# nothing in the file appears to connect - the card has no dataplane interface
# to carry an address on until the reboot - and the check from #294 refuses
# the whole configuration at the commit that names the card.
check(_cfg.connected_prefixes().get(0) == ["10.95.200.198/24"],
      f"the management address connects nothing, so its own default route is "
      f"refused: {_cfg.connected_prefixes()!r}")
print("  table 0 carries the address the card will have")

print("\n=== handing over the management card is a confirmed commit ===")
# Not left to the operator to ask for. This is the change `commit confirmed`
# was built for: the box does not lose the session at the commit but at the
# restart after it, and if anything in between is wrong there is no session
# left to fix it from.
with tempfile.TemporaryDirectory() as tmp:
    sh = one_card_box(tmp)
    said = drive(sh, ["configure", "dataplane interface ens18", "commit"])
    check("rollback at" in said,
          f"a plain commit handed over the management card with nothing to "
          f"undo it: {said!r}")
    check("30 minute" in said,
          f"the window does not cover a reboot and a login: {said!r}")
    check(sh.confirm.read() is not None,
          "the revert was announced and not armed on the box")
    print("  armed for 30 minutes without being asked")

print("\n=== the dataplane's name for the card is found by its MAC ===")
# Nothing carries the Linux name across: `ens18` becomes whatever DPDK calls
# the device at that PCI address. The MAC does carry, which is also why
# nothing upstream has to re-ARP.
with tempfile.TemporaryDirectory() as tmp:
    sh = one_card_box(tmp, dp=FakeVpp({"show hardware-interfaces":
                                       DP_HARDWARE}))
    check(sh.dataplane_if_with_mac("52:54:00:00:00:01")
          == "GigabitEthernet0/3/0",
          f"the card was not found by its MAC: "
          f"{sh.dataplane_if_with_mac('52:54:00:00:00:01')!r}")
    check(sh.dataplane_if_with_mac("52:54:00:00:00:09") is None,
          "a MAC this dataplane has not got was matched to an interface")
    print("  GigabitEthernet0/3/0, by 52:54:00:00:00:01")

print("\n=== the boot after the handover puts the address on the pair ===")
with tempfile.TemporaryDirectory() as tmp:
    api = FakeApi(have=("GigabitEthernet0/3/0",))
    sh = one_card_box(tmp, api=api,
                      dp=FakeVpp({"show hardware-interfaces": DP_HARDWARE}))
    ok = sh.apply_management_path({"name": "ens18",
                                   "address": "10.95.200.198/24",
                                   "mac": "52:54:00:00:00:01"})
    made = [a for m, a in api.calls if m == "lcp_itf_pair_add_del"]
    addrs = [a for m, a in api.calls if m == "sw_interface_add_del_address"]
    check(ok, "the box could not put its own management path back")
    check(made and made[0]["host_if_name"] == "ens18",
          f"no pair was made, or not under the name the card had - which is "
          f"the name FRR and every script on the box already use: {api.calls!r}")
    check(addrs and addrs[0]["prefix"] == "10.95.200.198/24"
          and addrs[0]["is_add"],
          f"the address did not go on, so the box comes up forwarding and "
          f"unreachable: {api.calls!r}")
    print("  pair named ens18, address 10.95.200.198/24")

print("\n=== and on every other boot it does nothing, quietly ===")
# `hexash --load` runs at every boot, including the ones before the operator
# reboots into the handover and the ones after it has been rolled back. A
# commit that failed because a card had not moved yet would be a box that
# cannot be configured at all.
with tempfile.TemporaryDirectory() as tmp:
    api = FakeApi()
    sh = one_card_box(tmp, api=api)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ok = sh.apply_management_path({"name": "ens18",
                                       "address": "10.95.200.198/24",
                                       "mac": "52:54:00:00:00:01"})
    check(ok and not api.calls,
          f"a boot before the card had moved was treated as a failure: "
          f"{ok!r} {api.calls!r}")
    check(out.getvalue() == "",
          f"and it said something about it on a boot where nothing was wrong: "
          f"{out.getvalue()!r}")
    print("  the card is not there, so there is nothing to do and nothing said")

# ------------------------------------------- the boot after the handover, #332
#
# MEASURED ON A BOOTED GUEST FIRST. lab/install-iso.sh asked a real box for its
# only card, the bind worked - virtio-pci to uio_pci_generic, the netdev gone,
# the dataplane holding GigabitEthernet0/9/0 - and the box came back with no
# address on any interface and nobody could reach it. What the box replays at
# boot is /etc/hexarouting/hexarouting.conf, and `commit` does not write that
# file: only `save` does. `commit` writes the card AND the path it carries into
# the tuner's file, which is what performs the handover at the next boot. So
# the two halves of one decision had two lifetimes, and the half that keeps the
# box reachable was the one that did not survive.
#
# Driven through `load()` against a file on disk rather than by calling
# apply_management_path() directly, because "never called" is the defect: a
# case that calls it cannot see it.

#: The tuner's file as the handover commit leaves it.
TUNE_HANDED_OVER = TUNE_ENV_SHIPPED.replace(
    "HEXA_DATAPLANE_IF=\n",
    "HEXA_DATAPLANE_IF=ens18\n"
    "HEXA_DATAPLANE_MGMT=ens18 10.95.200.198/24 52:54:00:00:00:01\n")
#: /proc/net/route on the boot after it: the card is gone and so is the route
#: that lived on it.
NO_ROUTE = ("Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask"
            "\t\tMTU\tWindow\tIRTT\n")
#: The configuration file every fresh appliance boots with, from
#: appliance/etc/hexarouting.conf. Three comments and no statement.
SHIPPED_CONF = "!\n! HexaRouting\n!\n"
#: And a dataplane that took nothing, for the case where the card is on
#: neither side.
DP_NOTHING = """\
              Name                Idx   Link  Hardware
local0                             0    down  local0
  local
"""


def booted_after_handover(tmp, conf, dp=None, api=None):
    """
    The box on the boot after the handover: Linux has no card at all, the
    tuner still names the one it gave away, and `conf` is what
    hexarouting.service replays.
    """
    sh = shell_on(tmp, route=NO_ROUTE, cards=("lo",), tune=TUNE_HANDED_OVER,
                  api=api or FakeApi(have=("GigabitEthernet0/3/0",)),
                  dp=dp or FakeVpp({"show hardware-interfaces": DP_HARDWARE}))
    path = os.path.join(tmp, "etc/hexarouting/hexarouting.conf")
    with open(path, "w") as f:
        f.write(conf)
    return sh, path


print("\n=== the boot after the handover makes the pair with nothing saved ===")
with tempfile.TemporaryDirectory() as tmp:
    api = FakeApi(have=("GigabitEthernet0/3/0",))
    sh, path = booted_after_handover(tmp, SHIPPED_CONF, api=api)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sh.load(path)
    made = [a for m, a in api.calls if m == "lcp_itf_pair_add_del"]
    addrs = [a for m, a in api.calls if m == "sw_interface_add_del_address"]
    check(made and made[0]["host_if_name"] == "ens18",
          f"the boot that hands the card over made no pair, so the box comes "
          f"up forwarding with no way in: {api.calls!r} {out.getvalue()!r}")
    check(addrs and addrs[0]["prefix"] == "10.95.200.198/24"
          and addrs[0]["is_add"],
          f"the pair was made and the management address did not go on it: "
          f"{api.calls!r}")
    print("  the record is in tune.env, and the file being replayed needs none")

print("\n=== and on a box that HAD saved, before the card was named ===")
# The other file this boot can find: a configuration saved before the handover,
# which has statements and says nothing about a dataplane card. Read as a
# configuration that removes one, it used to take the card's own name out of
# the tuner's file as well - so the box lost the address on this boot and the
# card at the next one.
with tempfile.TemporaryDirectory() as tmp:
    api = FakeApi(have=("GigabitEthernet0/3/0",))
    sh, path = booted_after_handover(tmp, "ssh port 22\n", api=api)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sh.load(path)
    made = [a for m, a in api.calls if m == "lcp_itf_pair_add_del"]
    check(made and made[0]["host_if_name"] == "ens18",
          f"a file saved before the handover was read as a file that undoes "
          f"it: {api.calls!r} {out.getvalue()!r}")
    check(sh.read_dataplane_if() == "ens18",
          f"and the boot replay un-named the card the box is holding, so the "
          f"next boot gives it back: {sh.read_dataplane_if()!r}")
    check(sh.read_dataplane_mgmt().get("mac") == "52:54:00:00:00:01",
          f"and the record went with it: {sh.read_dataplane_mgmt()!r}")
    print("  the card keeps its name, and the address goes on the pair")

print("\n=== a card on neither side is SAID, not returned as success ===")
# The boot this early return was hiding. The tuner unbound the card and the
# dataplane did not take it, so the card is on neither side, the address has
# nowhere to go, and the box is off the network - which is the one state where
# the console is the only place left that can be told.
with tempfile.TemporaryDirectory() as tmp:
    api = FakeApi()
    sh, path = booted_after_handover(
        tmp, SHIPPED_CONF, api=api,
        dp=FakeVpp({"show hardware-interfaces": DP_NOTHING}))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ok = sh.apply_management_path(sh.read_dataplane_mgmt())
    said = out.getvalue()
    check(not ok, "a box with its only card on neither side reported success")
    check(said.startswith("error:"),
          f"and it said nothing an operator at the console would read: "
          f"{said!r}")
    # The three facts that make the sentence about THIS box rather than about
    # boxes in general: which card, which MAC it was looked for under, and the
    # address that has nowhere to go.
    for fact in ("ens18", "52:54:00:00:00:01", "10.95.200.198/24"):
        check(fact in said, f"the refusal does not name {fact}: {said!r}")
    _wide = [l for l in said.splitlines() if len(l) > CONSOLE_COLUMNS]
    check(not _wide, f"the refusal does not fit the console: {_wide!r}")
    # Not indexed blind: a run where the box said nothing is the failure this
    # case exists for, and a progress line that raises there loses every
    # reading after it.
    print("  " + (said.splitlines()[0] if said else "<nothing at all>"))

print("\n=== and 'cannot look' is not 'the card is gone' ===")
# On anything that is not Linux - a developer's tree, most of this suite - the
# directory is not there at all. Reading that as a handover would turn every
# apply on such a box into the refusal above.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, dp=FakeVpp({"show hardware-interfaces": DP_NOTHING}))
    sh.SYS_CLASS_NET = os.path.join(tmp, "no-such-directory")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ok = sh.apply_management_path({"name": "ens18",
                                       "address": "10.95.200.198/24",
                                       "mac": "52:54:00:00:00:01"})
    check(ok and out.getvalue() == "",
          f"a box whose /sys/class/net cannot be read was told its card had "
          f"been handed over and lost: {ok!r} {out.getvalue()!r}")
    print("  a tree with no /sys/class/net in it is not a box that lost a card")

print("\n=== an unconfirmed handover gives the card back and restarts ===")
# The rest of a revert is a configuration. This one is not: the card was
# unbound from its kernel driver at boot and the dataplane holds it, so
# putting the tuner's file back only decides what the NEXT start claims. Until
# that start, Linux still has no card and the box is still unreachable - and
# this runs on a box nobody could reach, so there is no session to ask.
with tempfile.TemporaryDirectory() as tmp:
    # The box as it is AFTER the reboot: the card is in the dataplane, under
    # DPDK's name for it. That is the only state where the restart below is
    # the right thing to do, and the case under it measures the other one.
    sh = one_card_box(tmp, api=FakeApi(have=("GigabitEthernet0/3/0",)),
                      dp=FakeVpp({"show hardware-interfaces": DP_HARDWARE}))
    drive(sh, ["configure", "dataplane interface ens18", "commit"])
    pending = sh.confirm.read()
    check(pending is not None, "nothing was armed to revert")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        done = sh.revert_confirm(pending, "nobody confirmed it")
    said = out.getvalue()
    check(done, f"the revert did not complete: {said!r}")
    check(sh.read_dataplane_if() == "",
          f"the tuner is still pointed at the card, so the next boot hands it "
          f"over again: {sh.read_dataplane_if()!r}")
    check(not sh.read_dataplane_mgmt(),
          f"and the management record outlived the card it belongs to: "
          f"{sh.read_dataplane_mgmt()!r}")
    check(["systemctl", "reboot"] in sh.ran,
          f"the box was left reverted and unreachable, which is the revert "
          f"promising something it did not do: {sh.ran!r}")
    check("restarting the box" in said,
          f"and it restarted without saying so on the console: {said!r}")
    print("  card back, record gone, and the box restarts itself")

print("\n=== and a window that expires BEFORE the reboot restarts nothing ===")
# The window runs from the commit and the commit does not move the card; the
# restart after it does. An operator who commits and then waits before
# rebooting has the deadline pass while the box is still on its old path, and
# there this is an ordinary revert: the setting goes, the card never moved,
# and restarting would be this feature rebooting a box that was reachable the
# whole time.
with tempfile.TemporaryDirectory() as tmp:
    sh = one_card_box(tmp)          # a dataplane with nothing on it
    drive(sh, ["configure", "dataplane interface ens18", "commit"])
    pending = sh.confirm.read()
    sh.ran = []
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sh.revert_confirm(pending, "nobody confirmed it")
    check(sh.read_dataplane_if() == "",
          f"the setting was not put back: {sh.read_dataplane_if()!r}")
    check(["systemctl", "reboot"] not in sh.ran,
          f"a box that never handed its card over was rebooted: {sh.ran!r}")
    print("  the card never moved, so nothing is restarted")

print("\n=== a revert that gives back no card does not restart anything ===")
# The other direction, and it is what stops every unconfirmed commit on every
# box from rebooting it.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    drive(sh, ["configure", "ssh port 2222", "commit"])
    pending = sh.confirm.read()
    check(pending is not None,
          "an ssh change did not arm a confirmed commit, so this case is not "
          "being measured at all")
    sh.ran = []
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sh.revert_confirm(pending, "nobody confirmed it")
    check(["systemctl", "reboot"] not in sh.ran,
          f"an ordinary unconfirmed commit rebooted the box: {sh.ran!r}")
    print("  no card moved, so nothing is restarted")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
