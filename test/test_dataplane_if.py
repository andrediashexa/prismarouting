#!/usr/bin/env python3
"""
Naming the card the dataplane forwards on, from the VTY.

Until this existed the only way to give an appliance a card was to edit
PRISMA_DATAPLANE_IF in the tuner's file from a Linux shell. Every fresh install
comes up with that field empty - deliberately, because the alternative is VPP
probing and taking whatever NIC it finds, which on a single-NIC box is the
management port - so every fresh install forwards nothing until somebody drops
out of this VTY to an editor.

What is asserted here, and why each one is worth a suite rather than a lab:

  * THE REFUSAL, which is the reason this cannot be a thin wrapper over the
    file: a name this box has not got is refused AT THE PROMPT, with the list
    of names it does have. The card carrying the default route was refused
    here too, and that refusal went with #385 - a box is configured at its
    console when it is new, so it guarded a step nobody takes over the
    network. What holds now is that the name is ACCEPTED on every box, and
    the tuner reads the way back off the card on the boot that claims it.
  * THE FILE IS NOT REWRITTEN. tune.env is hand-editable by design and most of
    it is prose - the paragraph on why the field ships empty, the worker cap
    and the argument for leaving it uncapped. A commit that regenerated the
    file from the settings this VTY models would delete all of it.
  * COMMIT DOES NOT RESTART VPP. Handing a card to the dataplane ends
    everything reaching the box through that card, which on the box with one
    card is the operator's own session. The commit stops at that boundary,
    says where the boundary is, and ASKS which box it is on before saying what
    crossing it costs.

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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "prismash"))
import config                                                   # noqa: E402
import prismash                                                   # noqa: E402
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
PRISMA_DATAPLANE_IF=

# Subscriber target: sizes the hugepages.
PRISMA_SUBSCRIBERS=200000

# Low latency: disables deep C-states and transparent hugepages.
PRISMA_LOW_LATENCY=1

# Worker cap: the most physical cores handed to VPP as forwarding workers.
PRISMA_MAX_WORKERS=
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


def make_box(tmp, route=PROC_NET_ROUTE, tune=TUNE_ENV_SHIPPED, cards=CARDS,
             reserve=None):
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
        # only name the card has on both sides of the handover (#303). The
        # prompt stopped reading it with #385; the boot replay still matches
        # the way back's MAC against what /sys lists to tell a card that has
        # moved from one Linux still holds, and a fake tree without one would
        # read every card as gone.
        with open(f"{tmp}/sys/class/net/{name}/address", "w") as f:
            f.write(f"52:54:00:00:00:0{i + 1}\n")
    os.makedirs(f"{tmp}/etc/prisma-routing", exist_ok=True)
    with open(f"{tmp}/net_route", "w") as f:
        f.write(route)
    if tune is not None:
        with open(f"{tmp}/etc/prisma-routing/tune.env", "w") as f:
            f.write(tune)
    elif os.path.exists(f"{tmp}/etc/prisma-routing/tune.env"):
        os.unlink(f"{tmp}/etc/prisma-routing/tune.env")
    # The management network the installer recorded, which is the card the
    # dataplane reserves for Linux (#378). None means the box was installed
    # with no management network - the "none" answer, which is a decision
    # somebody made and reserves nothing.
    os.makedirs(f"{tmp}/etc/network/interfaces.d", exist_ok=True)
    mgmt = f"{tmp}/etc/network/interfaces.d/prisma-routing"
    if reserve:
        with open(mgmt, "w") as f:
            f.write(f"auto {reserve}\niface {reserve} inet dhcp\n")
    elif os.path.exists(mgmt):
        os.unlink(mgmt)


def computed_path(tmp, line):
    """What prisma-routing-tune left in /run on the boot that claimed a card."""
    d = f"{tmp}/run/prisma-routing"
    os.makedirs(d, exist_ok=True)
    with open(f"{d}/dataplane-management", "w") as f:
        f.write(line)


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
    sh = prismash.Shell()
    # Stated outright rather than inherited from whoever runs the suite (#35).
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT}
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
    sh.frr = type("F", (prismash.Frr,), {
        "available": lambda self: False,
        "show": lambda self, c: (1, ""),
        "running": lambda self: [],
        "reload": lambda self, l, check_only=False: (0, "")})()
    sh.SYS_CLASS_NET = f"{tmp}/sys/class/net"
    sh.PROC_NET_ROUTE = f"{tmp}/net_route"
    # OUT OF THE TREE, never off the machine running this suite. Without it
    # `show hardware` would reserve whatever card the developer's own box was
    # installed to be managed on, and the cases below would measure that
    # machine instead of the fixture - which is the trap PRISMA_IP's comment in
    # appliance/test_tune.py describes, in this file's own seams.
    sh.MGMT_CONF = f"{tmp}/etc/network/interfaces.d/prisma-routing"
    # And the way back prisma-routing-tune computes on the boot that claims a
    # card (#378). Its real path is /run, which a suite cannot own and must
    # not share between fixtures.
    sh.MGMT_PATH_FILE = f"{tmp}/run/prisma-routing/dataplane-management"
    sh.TUNE_ENV = f"{tmp}/etc/prisma-routing/tune.env"
    sh.rb = prismash.Rollback(path=f"{tmp}/rollback")
    sh.confirm = prismash.ConfirmPending(path=f"{tmp}/confirm")
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
    said = drive(sh, ["configure", "system", "dataplane", "interface enp2s0"])
    check(sh.candidate is not None and sh.candidate.dataplane_if == "enp2s0",
          f"the card did not reach the candidate: {said!r}")
    check("error" not in said,
          f"a card this box has and does not route over was refused: {said!r}")
    text = sh.candidate.text()
    check("  interface enp2s0" in text.splitlines(),
          f"the candidate does not render the statement back: {text!r}")
    print(f"  candidate holds {sh.candidate.dataplane_if!r} and renders it")

print("\n=== and survives the round trip through the configuration file ===")
# The file prisma-routing.service replays at every boot. A statement that renders
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
    drive(sh, ["configure", "system", "dataplane", "interface enp2s0",
               "no interface"])
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

print("\n=== the card carrying the default route is ACCEPTED at the prompt (#385) ===")
# IT WAS REFUSED HERE, with the other card named as the one to use instead
# (#123), and the refusal went with #385 on the maintainer's decision: a box
# is configured at its console when it is new, so a refusal guarding the card
# it is reached over guards a step nobody takes over the network - and it
# stood between an operator and the card they had just named. This is the
# box the refusal was written for: two cards, and the named one carries the
# default route, so the fixture reaches the line the refusal used to fire on.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    said = drive(sh, ["configure", "system", "dataplane", "interface enp1s0"])
    check("error:" not in said,
          f"the card carrying the default route was refused at the prompt, "
          f"which is the refusal #385 removed: {said!r}")
    check(sh.candidate.dataplane_if == "enp1s0",
          f"accepted and not stored, so 'commit' would hand over nothing: "
          f"{sh.candidate.dataplane_if!r}")
    print("  accepted, and stored in the candidate")

print("\n=== and again at commit, for the paths that never see the prompt ===")
# `rollback` to a revision written when this box had a different card, and
# `--load` of the file prisma-routing.service replays at boot, both reach apply()
# without passing do_config. The refusal lived there as well, so its absence
# is held there as well: driven through apply() rather than the prompt for
# exactly that reason.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    target = config.Config.parse("dataplane interface enp1s0\n")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ok = sh.apply(target, check_only=True)
    said = out.getvalue()
    check(ok is True,
          f"a configuration naming the card carrying the default route does "
          f"not validate, so a saved file naming it is refused at boot: "
          f"{said!r}")
    check("default route" not in said,
          f"and the check still argues about the route it carries: {said!r}")
    print("  a file naming the card carrying the default route validates")

print("\n=== and a card this box has not got is refused there too ===")
# THE HALF #385 KEPT, on the paths that never see the prompt. The case above
# names a card the box HAS, so the refusal it drives through apply() returns
# nothing and the branch that says no is never entered: a check that only
# ever walks past a branch cannot notice the branch deleted, and with it
# deleted a `rollback` to a revision from a box with different cards, or a
# `--load` of a file edited by hand, would hand the dataplane a name it
# cannot resolve and say so only in a journal. Driven with a card the box
# has not got, asserting the refusal and that it names what the box does
# have, because a refusal at boot that only says no leaves the operator to
# guess the spelling of a card they cannot see.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    target = config.Config.parse("dataplane interface enp9s0\n")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ok = sh.apply(target, check_only=True)
    said = out.getvalue()
    check(ok is False,
          f"a configuration naming a card this box has not got validates, so "
          f"a rollback or a boot replay would hand the dataplane a name it "
          f"cannot resolve: {said!r}")
    check("no such interface" in said,
          f"and the reason is not the one the prompt gives for the same "
          f"name: {said!r}")
    for card in [c for c in CARDS if c != "lo"]:
        check(card in said,
              f"the refusal does not name {card!r} among what this box has, "
              f"so `rollback` says no without saying what yes would be: "
              f"{said!r}")
    print("  refused at commit, and the cards the box has are named")

print("\n=== a card this box has not got is refused, and it says what it has "
      "===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    said = drive(sh, ["configure", "system", "dataplane", "interface eth9"])
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
    said = drive(sh, ["configure", "system", "dataplane", "interface enp2s0"])
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
    # A third check here drove `dataplane interface enp1s0` with the table
    # unreadable and asserted the name was accepted. Since #385 it is accepted
    # with the table readable too, so that check could no longer fail for the
    # reason it was written and is gone rather than left green about nothing.
    print("  read from /proc/net/route, destination 00000000")

# -------------------------------------------------------------------- commit

print("\n=== commit writes the tuner's input and changes nothing else ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    before = open(sh.TUNE_ENV).read()
    said = drive(sh, ["configure", "system", "dataplane", "interface enp2s0", "commit"])
    check("commit complete" in said, f"the commit did not complete: {said!r}")
    after = open(sh.TUNE_ENV).read()
    check("PRISMA_DATAPLANE_IF=enp2s0" in after.splitlines(),
          f"the tuner's input was not set: {after!r}")
    # EVERY other line, in order, byte for byte. Not "the other settings are
    # still there": what a regenerated file loses first is the prose, and a
    # check that only looked at the settings would pass over a file with every
    # comment stripped out of it.
    expect = [l if not l.startswith("PRISMA_DATAPLANE_IF=")
              else "PRISMA_DATAPLANE_IF=enp2s0" for l in before.splitlines()]
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
    said = drive(sh, ["configure", "system", "dataplane", "interface enp2s0", "commit"])
    check(["systemctl", "restart", "prisma-routing-tune"] in sh.ran,
          f"the tuner was never run, so startup.conf still names no device "
          f"and the operator would have to run it themselves: {sh.ran!r}")
    # THE assertion this whole design turns on. Handing a card to the
    # dataplane ends everything reaching the box through that card, and on the
    # box with one card that is the operator's own session. That is the same
    # class of consequence as `commit remap`, which this VTY refuses until it
    # is asked for by name - and no word has been invented for this one,
    # because nothing here has measured what it costs.
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
    said = drive(sh, ["configure", "system", "dataplane", "interface enp2s0", "commit"])
    check("NOT IN EFFECT YET" in said,
          f"the commit reported success over a card the dataplane has not "
          f"got, which is the sentence the operator builds the next hour of "
          f"diagnosis on: {said!r}")
    check("does not restart" in said,
          f"it does not say that the restart is outstanding: {said!r}")
    check("ends whatever is reaching this box" in said,
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
    _measured.append(drive(sh, ["configure", "system", "dataplane", "interface enp1s0"]))
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    _measured.append(drive(sh, ["configure", "system", "dataplane", "interface eth9"]))
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    _measured.append(drive(sh, ["configure", "system", "dataplane", "interface enp2s0",
                                "commit"]))
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    _measured.append(drive(sh, ["configure", "system", "dataplane", "interface enp2s0",
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
    said = drive(sh, ["configure", "system", "dataplane", f"interface {_LONGEST}"])
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
        "PRISMA_DATAPLANE_IF=", "PRISMA_DATAPLANE_IF=enp2s0"))
    running = sh.running_config()
    check(running.dataplane_if == "enp2s0",
          f"a card set by hand in the file is invisible to the VTY: "
          f"{running.dataplane_if!r}")
    said = drive(sh, ["show running-config | include interface enp2s0"])
    check("  interface enp2s0" in said,
          f"'show running-config' does not show the box's own card: {said!r}")
    print("  a hand-edited file shows up in 'show running-config'")

print("\n=== so a commit that changes nothing writes nothing ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, tune=TUNE_ENV_SHIPPED.replace(
        "PRISMA_DATAPLANE_IF=", "PRISMA_DATAPLANE_IF=enp2s0"))
    said = drive(sh, ["configure", "system", "dataplane", "interface enp2s0", "commit"])
    check("no changes to commit" in said,
          f"setting the card the box already has was treated as a change, so "
          f"every commit would re-run the tuner: {said!r}")
    check(["systemctl", "restart", "prisma-routing-tune"] not in sh.ran,
          f"and it ran the tuner for a change nobody made: {sh.ran!r}")
    print("  idempotent: the box already had it, so nothing ran")

print("\n=== and 'save' writes it to the file the box replays at boot ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, tune=TUNE_ENV_SHIPPED.replace(
        "PRISMA_DATAPLANE_IF=", "PRISMA_DATAPLANE_IF=enp2s0"))
    _was = prismash.CONFIG_PATH
    prismash.CONFIG_PATH = f"{tmp}/etc/prisma-routing/prisma-routing.conf"
    try:
        drive(sh, ["save"])
        saved = open(prismash.CONFIG_PATH).read()
    finally:
        prismash.CONFIG_PATH = _was
    check("  interface enp2s0" in saved.splitlines()
          and "system" in saved.splitlines(),
          f"'save' wrote a file that does not name the box's card, so the "
          f"next boot replays a configuration missing it: {saved!r}")
    check(not config.Config.parse(saved).unknown,
          f"and the file it wrote is not one it can read back: "
          f"{config.Config.parse(saved).unknown!r}")
    print("  saved, and the saved file parses clean")

# ----------------------------------------------------------- the file itself

print("\n=== a tuner file with no such setting in it gains one ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, tune="# nothing but a comment\nPRISMA_SUBSCRIBERS=10\n")
    drive(sh, ["configure", "system", "dataplane", "interface enp2s0", "commit"])
    after = open(sh.TUNE_ENV).read()
    check("PRISMA_DATAPLANE_IF=enp2s0" in after.splitlines(),
          f"a file that never had the setting did not gain it: {after!r}")
    check("PRISMA_SUBSCRIBERS=10" in after.splitlines(),
          f"and the settings it did have were lost: {after!r}")
    print("  appended, and what was there stayed")

print("\n=== a box with no tuner file at all is still configurable ===")
# Not an appliance, or an appliance whose file was removed. Refusing here
# would leave the operator with no way to proceed from the VTY, which is the
# defect being repaired rather than a safe default.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, tune=None)
    said = drive(sh, ["configure", "system", "dataplane", "interface enp2s0", "commit"])
    check("commit complete" in said,
          f"a box with no tuner file could not be given a card: {said!r}")
    check(sh.read_dataplane_if() == "enp2s0",
          f"and the file it wrote does not read back: "
          f"{sh.read_dataplane_if()!r}")
    print("  a file was written, and reads back")

print("\n=== a file that cannot be written fails the commit, and says so ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    sh.TUNE_ENV = f"{tmp}/etc/prisma-routing"      # a directory, not a file
    said = drive(sh, ["configure", "system", "dataplane", "interface enp2s0", "commit"])
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
    said = drive(sh, ["configure", "system", "dataplane", "interface enp2s0", "commit"])
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
    sh = shell_on(tmp, tune="PRISMA_DATAPLANE_IF=enp1s0\n"
                            "PRISMA_DATAPLANE_IF=enp2s0\n")
    check(sh.read_dataplane_if() == "enp2s0",
          f"read as {sh.read_dataplane_if()!r}, which is not what the unit "
          f"would use")
    print("  last assignment wins")

print("\n=== a quoted value reads back as the name, not as the quotes ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, tune='PRISMA_DATAPLANE_IF="enp2s0"\n')
    check(sh.read_dataplane_if() == "enp2s0",
          f"read as {sh.read_dataplane_if()!r}, so a quoted file would look "
          f"like a change on every commit")
    print("  quotes stripped")

print("\n=== a commented-out setting is not a setting ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, tune="# PRISMA_DATAPLANE_IF=enp1s0\nPRISMA_SUBSCRIBERS=1\n")
    check(sh.read_dataplane_if() == "",
          f"a commented line was read as configuration: "
          f"{sh.read_dataplane_if()!r}")
    print("  a comment stays a comment")

# --------------------------------- the way back, computed rather than stored

print("\n=== the VTY reads the way back the tuner computed at boot (#378) ===")
# On a box that claims its own card nobody typed, so nothing was ever written
# at a commit. The tuner computed the address off the live card in the window
# before the bind and left it in /run; this is the only account of that
# address there is, and without it the boot that hands the card over is the
# boot that puts nothing on the pair.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, tune="PRISMA_SUBSCRIBERS=1\n")
    computed_path(tmp, "ens18 10.95.200.198/24 52:54:00:AB:CD:EF 10.95.200.1\n")
    rec = sh.read_computed_mgmt()
    # THE GATEWAY IS PART OF IT, and this asserted three fields until an
    # installed box came back with its address on the pair and
    # `ip -4 route list default` empty. The VTY's record had three because
    # its route arrived with the saved configuration; a box that claimed its
    # own card never had that commit, and since #385 no box has that record,
    # so this one is the route's only author.
    check(rec == {"name": "ens18", "address": "10.95.200.198/24",
                  "mac": "52:54:00:ab:cd:ef", "gateway": "10.95.200.1"},
          f"the box cannot read what its own tuner computed, so a box that "
          f"claimed its own card comes back with no address or no way off "
          f"its own subnet: {rec!r}")
    # AND IT IS NOT CONFIGURATION. Whatever running_config() returns becomes
    # the candidate at the next `configure` and is written by the next
    # `save`, and this record is boot state: recomputed every boot from the
    # live card, and stale the moment it is copied anywhere else.
    #
    # MEASURED ON AN INSTALLED BOX, which is why this check exists: while the
    # VTY still had a record of its own, boot one had its default route, the
    # lab created two accounts and committed, and the boot after the power
    # cycle came back with the address on the pair and `ip -4 route list
    # default` empty - the commit had persisted a copy with the gateway
    # dropped, and the next boot read the copy. The record and the statement
    # are gone (#385); this holds the computed one out of the configuration.
    check("10.95.200.198" not in sh.running_config().text(),
          f"the computed record reaches the running configuration, so `save` "
          f"carries it and the next boot reads a stale copy: "
          f"{sh.running_config().text()!r}")

print("\n=== a computed record missing a field is no record at all ===")
# The safe direction, and the same rule the stored one has always had: the box
# then declines the handover rather than making it with half an answer. The
# three-field record is in the list on purpose: it is the shape the VTY used
# to write, and since #385 a record with no gateway is a box that comes back
# on its own subnet and nowhere else, so it is no record either.
for _bad in ("ens18\n", "ens18 10.95.200.198\n", "\n",
             "ens18 10.95.200.198/24 52:54:00:ab:cd:ef\n"):
    with tempfile.TemporaryDirectory() as tmp:
        sh = shell_on(tmp, tune="PRISMA_SUBSCRIBERS=1\n")
        computed_path(tmp, _bad)
        check(sh.read_computed_mgmt() == {},
              f"a half-written record was read as a way back: {_bad!r} gave "
              f"{sh.read_computed_mgmt()!r}")
with tempfile.TemporaryDirectory() as tmp:
    # And no file at all is the ordinary box: nothing claimed, nothing to put
    # back, and no complaint.
    sh = shell_on(tmp, tune="PRISMA_SUBSCRIBERS=1\n")
    check(sh.read_computed_mgmt() == {},
          "a box with no computed record was given one from somewhere")

# ---------------------------------------------- what the box claims, and why

print("\n=== 'show hardware' says what the box claimed and why (#378) ===")
# A box that silently ate a card is worse than one that asked, and a box that
# silently spared one is a card an operator cannot account for. The rule has
# to be readable off the screen, because it is the screen somebody reads
# before deciding what to type.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    said = " ".join(sh.claim_note(sh.host_nics()))
    check("all 2" in said,
          f"a two-card box with nothing reserved does not say it takes both: "
          f"{said!r}")
    check("no management network" in said,
          f"and does not say why nothing is reserved: {said!r}")

print("\n=== a card the installer recorded is reserved, and named ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, reserve="enp1s0")
    said = sh.claim_note(sh.host_nics())
    joined = " ".join(said)
    check("reserved for Linux" in joined,
          f"the card the box is managed on is not said to be reserved: "
          f"{joined!r}")
    # THE NAME ON A ROW OF ITS OWN, which is the shape #367 is about: a name
    # can be fifteen characters and a sentence carrying one folds on an
    # 80-column console, taking the only word an operator recognises with it.
    check(any(l.strip() == "enp1s0" for l in said),
          f"the reserved card's name is not on a row of its own: {said!r}")
    check("claims 1 of them" in joined,
          f"the count of what it takes is wrong or missing: {joined!r}")

print("\n=== and never the last card, whatever the installer answered ===")
# THE NARROWING. A reserve allowed to take the only card leaves the commonest
# box there is installed, correct and unable to forward.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, cards=("ens18", "lo"), reserve="ens18")
    joined = " ".join(sh.claim_note(sh.host_nics()))
    check("only card" in joined and "never takes the last card" in joined,
          f"a one-card box does not say it takes the card anyway: {joined!r}")
    check("linux_cp" in joined,
          f"and does not say where the address goes, which is the whole of "
          f"what the operator loses if it is wrong: {joined!r}")

print("\n=== a card the dataplane has not taken yet is named (#405) ===")
# The maintainer put a second NIC in a running box, read "The dataplane claims
# all 1" on this screen, and was then told by the VTY that the box has no such
# interface. Both sentences were true and the pair is unreadable: "claims" is
# the rule, the card is not claimed yet, and nothing said when it would be.
#
# Mutation shown red: `held is None` treated as "holds nothing", which turns a
# dataplane that could not be asked into a box waiting for a reboot - two
# different machines, and only one of them has anything to do.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, cards=("ens18", "ens19", "lo"))
    said = " ".join(sh.waiting_note(sh.host_nics(), ["ens18"]))
    check("ens19" in said and "ens18" not in said,
          f"the card the dataplane has not got, and only that one: {said!r}")
    check("reboot" in said,
          f"and what hands it over: {said!r}")
    check("allowlist" in said or "list naming the cards" in said,
          f"and why nothing else can: {said!r}")
    check(sh.waiting_note(sh.host_nics(), ["ens18", "ens19"]) == [],
          "a box with every card claimed says nothing")
    check(sh.waiting_note(sh.host_nics(), None) == [],
          "and a dataplane that could not be asked says nothing either - "
          "that is a different box from one holding no card")
    check(sh.waiting_note([], []) == [], "no cards, nothing to say")

print("\n=== an override says it was named, not chosen ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, tune="PRISMA_DATAPLANE_IF=enp2s0\n")
    joined = " ".join(sh.claim_note(sh.host_nics()))
    check("enp2s0" in joined and "no other" in joined,
          f"a box told which card to take does not say it was told: "
          f"{joined!r}")
    check("claims all" not in joined,
          f"a box with an override still claims to have taken everything: "
          f"{joined!r}")

print("\n=== and a card nothing reserves is said to be going anyway ===")
# THE ONE PLACE THE TWO ANSWERS POINT OPPOSITE WAYS. `dataplane interface`
# accepts any card the box has, the one carrying the default route included
# (the refusal went with #385), and the box claims every card nothing reserves
# (#378). On a box whose default route is not on the card the installer
# recorded, both are true and the operator needs the second: reserving a card
# happens at install time,
# and this is the screen where somebody finds out it did not.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp, reserve="enp2s0")   # route is on enp1s0, reserve is not
    said = drive(sh, ["show hardware"])
    check("will claim 'enp1s0' anyway" in " ".join(said.split()),
          f"the box is about to take the card it is reached on and the screen "
          f"does not say so, which is a box that silently ate its own "
          f"management: {said!r}")
with tempfile.TemporaryDirectory() as tmp:
    # And it is NOT said when the reserve is that very card, because then the
    # box leaves it alone and the sentence would be a warning about nothing.
    sh = shell_on(tmp, reserve="enp1s0")
    said = drive(sh, ["show hardware"])
    check("anyway" not in said,
          f"a box that reserves the card it is reached on was warned that it "
          f"is about to take it: {said!r}")

print("\n=== every row of the screen fits the console it is read on ===")
# 79 and not 80, for the reason appliance/selftest's say_list already folds to
# 79. Measured at the WIDEST name Linux will carry - fifteen characters - and
# not at the fixture's own, because the box that meets the long one is
# somebody else's.
#
# DRIVEN THROUGH `show hardware`, and not by calling claim_note() and folding
# its lines here. The first version of this check did the latter and could not
# fail: print_prose() wraps, so a check that wraps the strings itself is a
# check on its own harness. What can actually go wrong is a row that reaches
# the screen WITHOUT being wrapped - a caller reaching for print() instead of
# print_prose(), which is one keystroke and no error - and only the real
# screen can show that.
_WIDE = "enp0s31f6abcdef"
assert len(_WIDE) == 15
for _res, _cards in ((None, (_WIDE, "enp2s0", "lo")),
                     (_WIDE, (_WIDE, "enp2s0", "lo")),
                     (_WIDE, (_WIDE, "lo")),
                     ("ens99", (_WIDE, "enp2s0", "lo"))):
    with tempfile.TemporaryDirectory() as tmp:
        sh = shell_on(tmp, cards=_cards, reserve=_res,
                      route=f"Iface\tDestination\n{_WIDE}\t00000000\n")
        said = drive(sh, ["show hardware"])
        _over = [l for l in said.splitlines() if len(l) > 79]
        check(not _over,
              f"reserve={_res!r} cards={_cards!r}: {len(_over)} row(s) of "
              f"'show hardware' are over 79 columns and fold on the console "
              f"it is read on: {_over!r}")
        # AND THE SCREEN REALLY SAID IT. A box where `show hardware` printed
        # nothing would pass the width check above with nothing measured,
        # which is the shape of every defect this file's history is made of.
        check("NIC(s) on this box" in said,
              f"'show hardware' printed no card listing at all, so the width "
              f"check above measured an empty screen: {said!r}")
print("  four box shapes, every row of the screen inside 79 columns")

# ------------------------------------------------------------ the completion

print("\n=== '?' offers the cards the box has, and marks the default-route one ===")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    drive(sh, ["configure"])
    offered = sh.dynamic_options(prismash.ARG_HOST_NIC, "")
    # The PCI cards, and not `lo`: completion is an offer, and offering a name
    # the dataplane cannot be given is one the box would then refuse.
    check(set(offered) == {c for c in CARDS if c != "lo"},
          f"completion offers {sorted(offered)}, not the cards this box could "
          f"actually hand to the dataplane")
    check("default route" in offered.get("enp1s0", ""),
          f"the card this box is reached on is offered like any other, with "
          f"nothing saying what taking it means: {offered!r}")
    check("refused" not in offered.get("enp1s0", ""),
          f"the mark still calls the card refused, and since #385 it is not: "
          f"{offered!r}")
    print(f"  offers {', '.join(sorted(offered))}, and marks enp1s0")

print("\n=== the tree's marker has a source behind it ===")
# A marker added to the tree with nothing in DYNAMIC answers "<nic>" and
# nothing else, which is the bare placeholder this VTY exists to replace.
check(prismash.ARG_HOST_NIC in prismash.Shell.DYNAMIC,
      "the argument marker has no source, so '?' offers the placeholder")
_source = prismash.Shell.DYNAMIC.get(prismash.ARG_HOST_NIC, "")
check(_source and hasattr(prismash.Shell, _source),
      f"DYNAMIC names {_source!r}, which is not a method on the Shell")
print(f"  {prismash.ARG_HOST_NIC} -> {_source or '(nothing)'}")

print("\n=== the change is an administrator's, not a CGNAT operator's ===")
# Handing a card to the dataplane is not a CGNAT setting, and a cgnat-operator
# account that could do it could take the box off the network.
_d = config.Config().delta_to(config.Config.parse(
    "dataplane interface enp2s0\n"))
check(_d["dataplane_if_changed"] is True,
      f"the delta does not report the change at all, so nothing would apply "
      f"it: {_d['dataplane_if_changed']!r}")
check(prismash.cap_needed(_d) == prismash.CAP_ALL,
      f"this change needs {prismash.cap_needed(_d)!r}, so an account that may "
      f"only configure CGNAT could hand over the card")
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    sh.caps = {prismash.CAP_CGNAT}
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
# #303. The refusal of the card carrying the default route was right about
# every box that had somewhere else to put the dataplane, and on a box with
# one card its advice - "name the card carrying subscriber traffic instead" -
# named a card that does not exist. The box could not forward and could not
# be told to. #303 opened a way out for this box alone, under a record the
# prompt wrote and a commit that undid itself; #378 took the window and #385
# the refusal and the record, from every box.
#
# What is asserted here is what the prompt does NOW on this box: it accepts
# the only card like any other, writes nothing down for it, arms no window,
# and leaves the way back to the tuner, which reads it off the live card on
# the boot that claims it.

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
    # The defaults are the one-card box; a caller may name another, which is
    # what the width case does to reach the longest name Linux can carry.
    kw.setdefault("route", ONE_ROUTE)
    kw.setdefault("cards", ONE_CARD)
    sh = shell_on(tmp, **kw)
    if frr:
        # It KEEPS what it is given, because the commit reads FRR back
        # afterwards and reports any statement FRR did not keep (#184). A stub
        # that forgot every reload would report the handover's own default
        # route as lost on a box where nothing was wrong.
        class OkFrr(prismash.Frr):
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


print("\n=== a box with ONE card is accepted, and nothing is written down for it "
      "(#385) ===")
# #303 made the prompt write down, on this box, the address, the MAC and the
# gateway the card carried, and add the default route to the candidate, so
# the boot after the commit could put them back. The tuner reads the same
# three things off the live card on the boot that claims it, whichever box it
# is on and whether or not the card was named (#378), so the record taken here
# was a second copy of a fact the box recomputes - and one that could be a
# day older than the boot that used it. The writer is gone: naming the only
# card is accepted like any other statement, and the prompt says nothing more
# than it says for one.
with tempfile.TemporaryDirectory() as tmp:
    sh = one_card_box(tmp)
    said = drive(sh, ["configure", "system", "dataplane", "interface ens18"])
    check("error:" not in said,
          f"the only card a box has was refused, which is the dead end #303 "
          f"is about: {said!r}")
    check(sh.candidate.dataplane_if == "ens18",
          f"accepted and not stored: {sh.candidate.dataplane_if!r}")
    check("10.95.200.198" not in sh.candidate.text(),
          f"the prompt still writes the management path down, which the "
          f"tuner computes for itself at boot: {sh.candidate.text()!r}")
    check("ip route 0.0.0.0/0 10.95.200.1" not in sh.candidate.routing,
          f"the prompt still adds the default route to the configuration, and "
          f"the boot replay puts it back in Linux from the computed record: "
          f"{sh.candidate.routing!r}")
    check("10.95.200.198/24" not in said and "no other card" not in said,
          f"the prompt still prints the handover screen the writer printed: "
          f"{said!r}")
    print("  accepted, and nothing recorded or added")

print("\n=== and the completion rows for this box fit the console (#346) ===")
# THE WIDTH SWEEP ABOVE COLLECTS FROM THE TWO-CARD BOX ONLY, because
# one_card_box() is defined three hundred lines below it. The row the
# completion offers BEFORE the name is typed tells the operator what taking
# this card means, so it is the one that must not be broken by the terminal in
# the middle of a word - it measured 100 columns once. The prompt's own
# handover screen was measured here too, until #385 took the screen out.
#
# With the longest name Linux can carry as well as with `ens18`, because the
# name is interpolated into the row and IFNAMSIZ is 16 with the terminator, so
# fifteen characters is the longest name that can reach here at all.
_LONG = "e" * 15
_LONG_ROUTE = ONE_ROUTE.replace("ens18", _LONG)
_LONG_ADDRS = dict(IP_ADDRS)
_LONG_ADDRS[_LONG] = IP_ADDRS["ens18"].replace("ens18", _LONG)
_one_wide = []
for _name, _cards, _route, _addrs in (
        ("ens18", ONE_CARD, ONE_ROUTE, None),
        (_LONG, (_LONG, "lo"), _LONG_ROUTE, _LONG_ADDRS)):
    with tempfile.TemporaryDirectory() as tmp:
        sh = one_card_box(tmp, cards=_cards, route=_route, addrs=_addrs)
        _said = drive(sh, ["configure", "system", "dataplane", f"interface {_name}"])
        # The box has to have ACCEPTED it, or the rows measured below are
        # offered for a name the box then turns down.
        check("error:" not in _said,
              f"the one-card box refused {_name!r}: {_said!r}")
        _rows = sh.dynamic_options(prismash.ARG_HOST_NIC, "")
        for _n, _hint in _rows.items():
            _row = f"  {_n}  {_hint}"
            if len(_row) > CONSOLE_COLUMNS:
                _one_wide.append(_row)
check(not _one_wide,
      f"the one-card completion row wraps on a serial console: "
      f"{[(len(l), l) for l in _one_wide]}")
print(f"  the completion rows fit {CONSOLE_COLUMNS} columns on both names")

print("\n=== a statement FRR already carries does not block every commit (#358) ===")
# `set local-preference banana` was accepted at this prompt and committed
# until #354 gave that statement a value check, and FRR stores it and sets
# nothing. So there are boxes carrying one.
#
# A refusal that fires on what the box ALREADY has makes every commit on that
# box fail, and `--load` refuses the saved file whole at boot, so a box that
# was working comes up with nothing on it. That is the trap the route-map
# REFERENCE check beside it has been guarded against since #145, and the value
# check needed the same guard the moment it existed.
#
# Driven through do_commit, not through the finding: the finding is right
# either way, and what this is about is whether the commit is refused.
with tempfile.TemporaryDirectory() as tmp:
    sh = one_card_box(tmp)
    sh.frr.held = ["route-map LP permit 10",
                   " match as-path AS1",
                   " set local-preference banana"]
    # A change that touches nothing the route-map is about, and needs no
    # interface the fake dataplane has not got. If the guard is missing, the
    # statement FRR already has is enough on its own to refuse it - which is
    # the whole defect: not that a change is rejected, but that NO change can
    # be made on that box.
    said = drive(sh, ["configure", "nat", "cgnat", "block-holddown 90",
                      "commit"])
    check("commit complete" in said,
          f"a box whose FRR already carries a statement this VTY refuses "
          f"could not commit anything at all, not even an empty change: "
          f"{said!r}")
    check("banana" in said,
          f"and it was not told about the statement either, so the one thing "
          f"it cannot do is also the one thing nobody mentioned: {said!r}")
    print("  it commits, and the statement is still reported")

with tempfile.TemporaryDirectory() as tmp:
    sh = one_card_box(tmp)
    said = drive(sh, ["configure", "router bgp 65000", "exit",
                      "route-map LP permit 10", "match as-path AS1",
                      "set local-preference banana", "exit", "commit"])
    check("commit complete" not in said,
          f"a box being GIVEN the statement now was allowed to commit it, "
          f"which is what #354 is about: {said!r}")
    print("  and a box being given one now is still refused")

print("\n=== the documented procedure is the procedure THIS box needs ===")
# WHY THIS IS HERE AND NOT IN test_doc_copies.py. The claim is about two
# documents, but its premise is a measurement: that the same three statements
# an operator types produce a plain commit on one box and a thirty-minute
# window on another. A check that read the pages for the word "confirm"
# without having watched a box arm one is a check about prose, and it would
# stay green on the day the VTY stops arming the window - which is the day the
# pages become right by accident and the check stops meaning anything.
#
# So the box is driven first and the pages are held against what it did. The
# fixture is here; a second copy of it elsewhere is a second box to keep true.
APPLIANCE = os.path.join(os.path.dirname(__file__), "..", "docs",
                         "appliance.md")
CONTEXT = os.path.join(os.path.dirname(__file__), "..", "docs", "context.md")


def section(path, start, stop):
    """
    The text between two headings, on one line.

    Flattened, because these documents are wrapped by hand at 79 columns and a
    check that matched across the wrap would pass or fail on where the wrap
    happened to fall. What is being held is the sentence, not the line.

    A heading that moved answers "" and not the rest of the file. #322's first
    version searched a whole document and passed with the key deleted from
    both tables, because the page discusses the same subject in prose
    elsewhere; a section reader that runs to the end of the file on a missing
    anchor is that defect with one more step.
    """
    with open(path) as fh:
        text = fh.read()
    i = text.find(start)
    j = text.find(stop, i + len(start)) if i >= 0 else -1
    if i < 0 or j < 0:
        return ""
    return " ".join(text[i:j].split())


with tempfile.TemporaryDirectory() as tmp:
    sh = one_card_box(tmp)
    said = drive(sh, ["configure", "system", "dataplane", "interface ens18", "commit"])
    check(sh.confirm.read() is None,
          f"the one-card handover armed a window again: its revert clears the "
          f"setting, and a one-card box with that setting empty claims the "
          f"card - so the window arrives where it started (#378): {said!r}")

with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    drive(sh, ["configure", "system", "dataplane", "interface enp2s0", "commit"])
    check(sh.confirm.read() is None,
          "a box with a second card armed a confirmed commit for a handover "
          "that never touches the session it was typed in")

# THE INVARIANT, LIFTED OUT OF THE GUARD IT USED TO SIT IN.
#
# It was inside `if one_card_armed:`, which was true only while the handover
# armed a window. Removing the arming would have made this stop running
# without anything going red - a reader nobody calls is a rule that has
# quietly stopped applying, which is what #311 cost and what this file's own
# header is about. So it is checked unconditionally now.
#
# WHAT THE INVARIANT SAYS AND WHY IT SURVIVED #378. The card the box will not
# give VPP is the one the installer recorded as the management network; on a
# box with only that card it goes anyway, because a reserve never takes the
# last one. Both halves have to be on the page, or an invariant stated one
# step too wide is one a reader checks once, finds false, and stops checking
# (#324).
inv = section(APPLIANCE.replace("appliance.md", "context.md"),
              "### 1.5 ", "### 1.6")
if not inv:
    inv = section(CONTEXT, "### 1.5 ", "### 1.6")
# BOTH, because "one card" alone survives on the section's own history. 1.5
# recounts that the refusal WAS a dead end on a one-card box (#303), so the
# phrase is there whether or not the page still states the exception as live.
# "never the last" is the rule's own name and only a live statement carries it.
#
# FOUND BY MUTATING AND READING THE RUN, not by reading the check. Deleting
# the live exception from the page left this GREEN, because the needle matched
# the history two paragraphs above it. That is TESTING.md's fourth lie in the
# shape it takes over prose: the needle matched a LABEL - a phrase the section
# uses about something else - rather than the claim being made. A needle over
# a screen or a page has to be cut from a sentence only the live claim can
# produce, and the way to find out whether it was is to break the claim and
# read what the run said, not its exit status.
check("one card" in inv and "never the last" in inv,
      "docs/context.md invariant 1.5 states the refusal with no exception, "
      "and a box with one card gives that card to the dataplane: an "
      "invariant stated one step too wide is one a reader checks once, finds "
      "false, and stops checking (#324)")
check("commit confirmed 30" not in inv,
      "docs/context.md invariant 1.5 still names the thirty-minute window as "
      "what makes unreachable fixable remotely, and that window is gone: the "
      "invariant is true for other reasons now and has to say which")
# AND THE APPLIANCE GUIDE'S PROCEDURE, which carried the same window.
proc = section(APPLIANCE, "`interface <name>` under `system` > `dataplane`",
               "## What the console shows")
check(proc, "docs/appliance.md no longer has the override paragraph this "
            "section is anchored on")
check("commit confirmed 30" not in proc,
      "docs/appliance.md still tells an operator that naming a card arms a "
      "thirty-minute window, and it does not")

print("\n=== and on the two-card box, no record is written for it ===")
# The record the VTY wrote beside the card was for the box that has no other
# card (#303), and #385 took the writer out. A two-card box naming the card
# it is reached on is accepted now, and it gets no record here either: the
# tuner reads the way back off the card on the boot that claims it,
# whichever box it is on.
with tempfile.TemporaryDirectory() as tmp:
    sh = shell_on(tmp)
    said = drive(sh, ["configure", "system", "dataplane", "interface enp1s0"])
    check("error:" not in said and sh.candidate.dataplane_if == "enp1s0",
          f"the two-card box refused the card it is reached on: {said!r}")
    check("192.168.2.50" not in sh.candidate.text(),
          f"a box with a spare card recorded a management path the tuner "
          f"computes for itself: {sh.candidate.text()!r}")
    print("  accepted, and nothing recorded")

print("\n=== a card whose management state cannot be read is accepted too ===")
# IT WAS REFUSED, saying which of the address, the gateway and the MAC was
# missing, because an address this box cannot read is one it cannot put back.
# That is still true and it is the tuner's question now (#385): it reads the
# same three things off the live card on the boot that claims it and declines
# the card when one is missing, saying which in the journal. A second answer
# at the prompt, from a reading taken on a different day, is the pair that
# comes to disagree - and the prompt no longer refuses a card for what the
# box may or may not be able to read at its next boot.
with tempfile.TemporaryDirectory() as tmp:
    sh = one_card_box(tmp, addrs={})
    said = drive(sh, ["configure", "system", "dataplane", "interface ens18"])
    check("error:" not in said,
          f"a card with no address was refused at the prompt, which is the "
          f"refusal #385 removed: {said!r}")
    check(sh.candidate.dataplane_if == "ens18",
          f"accepted and not stored: {sh.candidate.dataplane_if!r}")
    print("  accepted, and the tuner decides at boot")

with tempfile.TemporaryDirectory() as tmp:
    # Two addresses is the same answer for the same reason.
    sh = one_card_box(tmp, addrs={
        "ens18": IP_ADDRS["ens18"] + "\n2: ens18    inet 10.0.9.9/24 scope "
                                     "global secondary ens18"})
    said = drive(sh, ["configure", "system", "dataplane", "interface ens18"])
    check("error:" not in said and sh.candidate.dataplane_if == "ens18",
          f"a card with two addresses was refused at the prompt: {said!r}")
    check("10.0.9.9" not in sh.candidate.text()
          and "10.95.200.198" not in sh.candidate.text(),
          f"and a record was written by guessing between them: "
          f"{sh.candidate.text()!r}")
    print("  and a card with two addresses is accepted with nothing guessed")

print("\n=== commit writes the card into tune.env and nothing beside it (#385) ===")
# It used to write the management record beside PRISMA_DATAPLANE_IF on this
# box, so that a later session reading the box back found the way back as
# well as the card. The way back is the tuner's now, computed on the boot that claims
# the card and left in /run; a copy in tune.env would be read back into the
# running configuration and written again by the next commit, a day older
# each time. Held against the file, because "not written" is what this is.
with tempfile.TemporaryDirectory() as tmp:
    sh = one_card_box(tmp)
    said = drive(sh, ["configure", "system", "dataplane", "interface ens18", "commit"])
    check("commit complete" in said, f"the commit did not complete: {said!r}")
    tune = open(sh.TUNE_ENV).read()
    check("PRISMA_DATAPLANE_IF=ens18" in tune.splitlines(),
          f"the card is not in the tuner's file: {tune!r}")
    # THE WHOLE FILE, not the absence of one key: the shipped file with the
    # card's line changed and nothing else, so a record written under any
    # name beside the card is a difference.
    check(tune == TUNE_ENV_SHIPPED.replace("PRISMA_DATAPLANE_IF=\n",
                                           "PRISMA_DATAPLANE_IF=ens18\n"),
          f"the commit still writes something beside the card, which the "
          f"tuner computes for itself: {tune!r}")
    # And the prose the file ships with is still there, which is the rule the
    # card's own setting already has.
    check("EMPTY ON PURPOSE" in tune,
          f"the file was rewritten from the settings this VTY models: {tune!r}")
    print("  the card, the prose, and no record")

print("\n=== a saved file from 0.76.x still loads, with its record dropped (#385) ===")
# `dataplane management <card> address <a/len> mac <xx>` was written by `save`
# on every one-card box set up from 0.74.x to 0.76.1, under the card, and
# prisma-routing.service replays that file at every boot. The statement is gone:
# the way back is computed by the tuner on the boot that claims the card, and
# a line the box no longer reads was doing nothing. A file carrying one is
# NOT refused - a refusal at boot is a box coming up with no pools on it, read
# by nobody, over a line that changed nothing - it is loaded minus that line,
# the boot log says so, and the next `save` writes the file without it.
_old = ("dataplane interface ens18\n"
        "dataplane management ens18 address 10.95.200.198/24 "
        "mac 52:54:00:00:00:01\n"
        "ssh port 22\n")
_cfg = config.Config.parse(_old)
check(not _cfg.unknown,
      f"a 0.76.x saved file is refused over the statement it was told to "
      f"write: {_cfg.unknown!r}")
check(len(_cfg.dropped) == 1 and _cfg.dropped[0][0] == 2,
      f"the line is not reported as dropped, so the file and the running "
      f"configuration differ with nobody told: {_cfg.dropped!r}")
check(_cfg.dataplane_if == "ens18" and "management" not in _cfg.text(),
      f"the card survives the drop and the record does not get rendered "
      f"again: {_cfg.text()!r}")
# AND AT THE PROMPT it is not a statement at all: an operator pasting a
# 0.76.x file gets the usage line, which names the one statement there is.
# The boot's own reading of such a file is driven through load() further
# down, beside the other boots after a handover, where the fixture for a box
# replaying a saved file lives.
with tempfile.TemporaryDirectory() as tmp:
    sh = one_card_box(tmp)
    said = drive(sh, ["configure", "dataplane management ens18 address "
                                   "10.95.200.198/24 mac 52:54:00:00:00:01"])
    # Since #457 the top-level word itself is refused, with where it went.
    check("error: 'dataplane' has moved under 'system'" in said,
          f"the prompt still takes the statement: {said!r}")
print("  parsed minus the line, and refused at the prompt")

print("\n=== handing over the management card arms NO window (#378) ===")
# IT USED TO, and the window stopped undoing anything. It was armed because
# the box lost the session at the restart after the commit, and a handover
# nobody confirmed put the card back and rebooted (#303). Since the box claims
# its own card, the state the revert produced - the setting empty, on a
# one-card box - is the state that CLAIMS the card. Both halves measured: the
# revert leaves it empty and reboots, and the tuner on that exact file answers
# `dev 0000:00:09.0`.
#
# So confirming and doing nothing ended in the same place, and doing nothing
# cost a reboot to get there. The screen was wrong twice over: it said the
# card goes back to Linux, and it implied that not typing `commit` would lose
# it. The card stays either way.
with tempfile.TemporaryDirectory() as tmp:
    sh = one_card_box(tmp)
    said = drive(sh, ["configure", "system", "dataplane", "interface ens18", "commit"])
    check(sh.confirm.read() is None,
          f"the handover armed a window whose revert reverts nothing, so the "
          f"box promises to give a card back that it re-claims: {said!r}")
    check("rollback at" not in said and "30 minute" not in said,
          f"the screen still offers a window the box does not have: {said!r}")
    # AND WHAT IT SAYS INSTEAD. The prompt's own sentence about there being
    # no safety net went with the writer that printed it (#385); what the
    # operator is told now is what every card is told at the commit - the
    # dataplane claims it when it starts, and this commit does not restart it.
    # KEYED ON THE SENTENCE AND NOT ON A WORD IT SHARES, which is the lesson
    # the first version of this check taught: it looked for "claim", which the
    # screen says elsewhere about something else.
    check("NOT IN EFFECT YET" in said and "does not restart" in said,
          f"nothing on the screen tells the operator what happens to the card "
          f"they just named: {said!r}")
    print("  no window, and the commit says when the card moves")

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
                                   "mac": "52:54:00:00:00:01",
                                   "gateway": "10.95.200.1"})
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

print("\n=== and the box puts its default route back, not just the address ===")
# THE DEFECT THIS CAUGHT, on a real installed box: the address was back on the
# pair and `ip -4 route list default` was empty, so the box answered
# everything on its own wire and nothing beyond it. In the release gate's
# guest qemu's gateway is ON that wire, so the SSH probe still succeeded -
# which is exactly how this would have shipped.
with tempfile.TemporaryDirectory() as tmp:
    # The same box the handover cases below use, because this is that boot:
    # the dataplane holds the card and reports the MAC the record names.
    sh = one_card_box(tmp, api=FakeApi(have=("GigabitEthernet0/3/0",)),
                      dp=FakeVpp({"show hardware-interfaces": DP_HARDWARE}))
    computed_path(tmp, "ens18 10.95.200.198/24 52:54:00:00:00:01 10.95.200.1\n")
    sh.apply_management_path(sh.read_computed_mgmt())
    _route = [a for a in sh.ran if a[:3] == ["ip", "route", "replace"]]
    check(_route,
          f"nothing put the default route back, so the box is reachable from "
          f"its own subnet and nowhere else: {sh.ran!r}")
    check(_route and _route[0] == ["ip", "route", "replace", "default",
                                   "via", "10.95.200.1", "dev", "ens18"],
          f"the route was put back with the wrong next hop or on the wrong "
          f"interface: {_route!r}")

print("\n=== and on every other boot it does nothing, quietly ===")
# `prismash --load` runs at every boot, including the ones before the operator
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
                                       "mac": "52:54:00:00:00:01",
                                       "gateway": "10.95.200.1"})
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
# boot is /etc/prisma-routing/prisma-routing.conf, and `commit` does not write that
# file: only `save` does. `commit` writes the card into the tuner's file,
# which is what performs the handover at the next boot. So the two halves of
# one decision had two lifetimes, and the half that keeps the box reachable
# was the one that did not survive.
#
# WHERE THE WAY BACK COMES FROM, since #385: /run, and only /run. The tuner
# computes it off the live card on the boot that claims it, and load() puts
# it back whatever the saved file says about the card. The VTY's own record
# in tune.env was read here in preference until 6b, and only where the file
# named nothing - so a file that named the card without such a record was a
# boot that put nothing back.
#
# Driven through `load()` against a file on disk rather than by calling
# apply_management_path() directly, because "never called" is the defect: a
# case that calls it cannot see it.

#: The tuner's file as the commit that named the card leaves it.
TUNE_HANDED_OVER = TUNE_ENV_SHIPPED.replace(
    "PRISMA_DATAPLANE_IF=\n", "PRISMA_DATAPLANE_IF=ens18\n")
#: /proc/net/route on the boot after it: the card is gone and so is the route
#: that lived on it.
NO_ROUTE = ("Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask"
            "\t\tMTU\tWindow\tIRTT\n")
#: The configuration file every fresh appliance boots with, from
#: appliance/etc/prisma-routing.conf. Three comments and no statement.
SHIPPED_CONF = "!\n! Prisma Routing\n!\n"
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
    prisma-routing.service replays.
    """
    sh = shell_on(tmp, route=NO_ROUTE, cards=("lo",), tune=TUNE_HANDED_OVER,
                  api=api or FakeApi(have=("GigabitEthernet0/3/0",)),
                  dp=dp or FakeVpp({"show hardware-interfaces": DP_HARDWARE}))
    computed_path(tmp, "ens18 10.95.200.198/24 52:54:00:00:00:01 10.95.200.1\n")
    path = os.path.join(tmp, "etc/prisma-routing/prisma-routing.conf")
    with open(path, "w") as f:
        f.write(conf)
    return sh, path


print("\n=== load() uses the computed record on a box nobody typed at ===")
# THE WIRING, not the reader. The readers above are asserted directly, and a
# mutation that removed the computed record from load()'s fallback went GREEN
# against all of them: they ask read_computed_mgmt() themselves, so nothing
# was holding the one path that actually hands the card over.
#
# This drives load() on the box #378 produces - tune.env names nothing,
# nothing was ever committed, and the only account of the address is what the
# tuner left in /run - and asserts the box came back with both halves.
with tempfile.TemporaryDirectory() as tmp:
    api = FakeApi(have=("GigabitEthernet0/3/0",))
    sh = shell_on(tmp, route=NO_ROUTE, cards=("lo",),
                  tune="PRISMA_SUBSCRIBERS=200000\n", api=api,
                  dp=FakeVpp({"show hardware-interfaces": DP_HARDWARE}))
    computed_path(tmp, "ens18 10.95.200.198/24 52:54:00:00:00:01 10.95.200.1\n")
    path = os.path.join(tmp, "etc/prisma-routing/prisma-routing.conf")
    with open(path, "w") as f:
        f.write(SHIPPED_CONF)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sh.load(path)
    addrs = [a for m, a in api.calls if m == "sw_interface_add_del_address"]
    check(addrs and addrs[0]["prefix"] == "10.95.200.198/24",
          f"the boot replayed the shipped file and never put the address "
          f"back, so a box that claimed its own card comes up unreachable: "
          f"{api.calls!r}")
    check([a for a in sh.ran if a[:3] == ["ip", "route", "replace"]],
          f"the address went back and the default route did not, so the box "
          f"answers on its own subnet and nowhere else: {sh.ran!r}")

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
    print("  the record is in /run, and the file being replayed needs none")

print("\n=== and on a box whose saved file NAMES the card (#385) ===")
# THE GAP 6b CLOSES. load() took the way back from its holder only where the
# saved file named no card, and a file that named one was left with whatever
# record it carried - which until #385 was the VTY's, written beside the card
# at the commit, and since step 5 is nothing at all on a box with a spare
# card. So `dataplane interface ens18`, `save`, reboot was a boot that put
# nothing back. The computed record is applied whatever the file says.
with tempfile.TemporaryDirectory() as tmp:
    api = FakeApi(have=("GigabitEthernet0/3/0",))
    sh, path = booted_after_handover(tmp, "dataplane interface ens18\n",
                                     api=api)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sh.load(path)
    made = [a for m, a in api.calls if m == "lcp_itf_pair_add_del"]
    addrs = [a for m, a in api.calls if m == "sw_interface_add_del_address"]
    check(made and made[0]["host_if_name"] == "ens18",
          f"a saved file naming the card left the boot with no pair, so the "
          f"way back the tuner computed was never put back: {api.calls!r} "
          f"{out.getvalue()!r}")
    check(addrs and addrs[0]["prefix"] == "10.95.200.198/24"
          and addrs[0]["is_add"],
          f"the address did not go on the pair: {api.calls!r}")
    check([a for a in sh.ran if a[:3] == ["ip", "route", "replace"]],
          f"and the default route was not put back: {sh.ran!r}")
    print("  the file names the card, and the way back still comes from /run")

print("\n=== and on a box whose saved file carries the 0.76.x record (#385) ===")
# The other half of the drop case above, through load() on the box that has
# such a file: the way back comes from /run as on every boot, the rest of the
# file is applied, and the note is on the console rather than a refusal.
with tempfile.TemporaryDirectory() as tmp:
    api = FakeApi(have=("GigabitEthernet0/3/0",))
    sh, path = booted_after_handover(tmp, _old, api=api)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sh.load(path)
    said = out.getvalue()
    check("refusing to load" not in said,
          f"the boot refused a 0.76.x saved file over the dropped statement: "
          f"{said!r}")
    check("note:" in said and "dropped" in said
          and "dataplane management" in said,
          f"the boot log does not say the line was dropped: {said!r}")
    made = [a for m, a in api.calls if m == "lcp_itf_pair_add_del"]
    check(made and made[0]["host_if_name"] == "ens18",
          f"and the way back was not put on the pair from /run: {api.calls!r}")
    # The rows this VTY composes fit the console; the path and the file's
    # own line are quoted whole on rows of their own, where a fold costs
    # nothing, and are not measured (#276).
    _wide = [l for l in said.splitlines() if len(l) > CONSOLE_COLUMNS
             and l.strip() != path and not l.startswith("    dataplane ")]
    check(not _wide, f"the note does not fit the console: {_wide!r}")
    print("  loaded minus the line, and said on the console")

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
        ok = sh.apply_management_path(sh.read_computed_mgmt())
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
                                       "mac": "52:54:00:00:00:01",
                                       "gateway": "10.95.200.1"})
    check(ok and out.getvalue() == "",
          f"a box whose /sys/class/net cannot be read was told its card had "
          f"been handed over and lost: {ok!r} {out.getvalue()!r}")
    print("  a tree with no /sys/class/net in it is not a box that lost a card")

print("\n=== an unconfirmed revert restarts nothing, because it undoes nothing ===")
# WHAT THIS CASE USED TO ASSERT, and why it is the opposite now. The revert
# put the tuner's file back and rebooted, on the argument that the next start
# would then claim nothing and Linux would have its card again. Under #378 the
# next start claims that card: the file it was put back to IS the file a box
# that claims its own card ships with.
#
# Measured across both programs before this was changed: the revert leaves
# PRISMA_DATAPLANE_IF empty and reboots, and appliance/prisma-routing-tune on a
# one-card box with that file answers `dev 0000:00:09.0`. The reboot arrived
# where it started and charged a reboot for it.
#
# The configuration half of the revert stays and is asserted here: the setting
# goes. What must not happen any more is the restart.
with tempfile.TemporaryDirectory() as tmp:
    sh = one_card_box(tmp, api=FakeApi(have=("GigabitEthernet0/3/0",)),
                      dp=FakeVpp({"show hardware-interfaces": DP_HARDWARE}))
    # ONE COMMIT, and the window is asked for by hand because nothing arms
    # itself now. Committing first and arming second would arm a window whose
    # backup already has the card in it, so the revert would restore the card
    # rather than remove it - measuring the wrong box.
    drive(sh, ["configure", "system", "dataplane", "interface ens18",
               "commit confirmed 5"])
    pending = sh.confirm.read()
    check(pending is not None,
          "an explicit 'commit confirmed' armed nothing, so the revert below "
          "is measuring a box with no window at all")
    sh.ran = []
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        done = sh.revert_confirm(pending, "nobody confirmed it")
    said = out.getvalue()
    check(done, f"the revert did not complete: {said!r}")
    check(sh.read_dataplane_if() == "",
          f"the tuner is still pointed at the card: "
          f"{sh.read_dataplane_if()!r}")
    check(["systemctl", "reboot"] not in sh.ran,
          f"the box rebooted to give back a card the next start re-claims, "
          f"so it arrives where it started and charges a reboot: {sh.ran!r}")
    check("card goes back to Linux" not in said,
          f"the console still promises the card returns to Linux, which the "
          f"next start undoes: {said!r}")
    print("  setting cleared, and no reboot")

print("\n=== NO revert reboots the box, whatever it reverted (#378) ===")
# TWO CASES COLLAPSED INTO ONE, because what they bounded is gone. They said
# a revert restarts the box only when the card really moved: one drove a
# window that expired before the reboot, the other an ordinary ssh change, and
# both asserted no reboot. The third case, the one that DID reboot, was
# removed with the mechanism.
#
# What is left is worth keeping and is stronger than either: a revert is a
# configuration and never a restart. Held on both boxes, because "no reboot
# here" is only interesting beside a box that could plausibly have wanted one.
for _what, _lines in (("a handover", ["configure", "system", "dataplane",
                                      "interface ens18",
                                      "commit confirmed 5"]),
                      ("an ssh change", ["configure", "ssh port 2222",
                                         "commit"])):
    with tempfile.TemporaryDirectory() as tmp:
        sh = (one_card_box(tmp) if _what == "a handover" else shell_on(tmp))
        drive(sh, _lines)
        pending = sh.confirm.read()
        check(pending is not None,
              f"{_what} armed nothing, so this case is not measuring a revert "
              f"at all")
        sh.ran = []
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sh.revert_confirm(pending, "nobody confirmed it")
        check(["systemctl", "reboot"] not in sh.ran,
              f"reverting {_what} rebooted the box: a revert is a "
              f"configuration, and the one restart this ever had gave back a "
              f"card the next start re-claims: {sh.ran!r}")
print("  neither a handover nor an ssh change restarts anything")

#: /proc/net/route on a box whose management has ALREADY moved: the default
#: route is on the linux_cp pair the dataplane holds, which is not a card in
#: /sys/class/net and never will be.
PAIR_ROUTE = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask"
    "\t\tMTU\tWindow\tIRTT\n"
    "enp2s0\t0000000A\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n"
    "ens18\t00000000\t01C85F0A\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
)
#: And one with cards and connected routes but nothing carrying a default: a
#: box reached over a directly connected address, or from a console.
NO_DEFAULT_ROUTE = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask"
    "\t\tMTU\tWindow\tIRTT\n"
    "enp1s0\t0002A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n"
)

# AND IT COSTS DIFFERENT THINGS ON DIFFERENT BOXES, which this screen used to
# state without asking. It said "ends every session on this box, including
# this one" - true of the box the whole handover path was written for, whose
# only card carries the management address, and false of the two-card box
# above, which is the worked example in docs/appliance.md and in the operator
# guide. There the session arrives on a card Linux keeps and a dataplane
# restart never touches it, so the operator was told they would lose the box
# for a change that cannot cost them the box.
#
# The product answered this question correctly in three other places once -
# the refusal offered the other card, the management record was written only
# for this one, and `commit` armed a confirmed commit only when the card
# carried the session - and all three went, with #378 and #385. This screen
# is the one left, so what is asserted here is that it asks the box the same
# pair `show hardware` reads rather than stating one answer for every box.
print("\n=== what the restart costs is asked, not stated ===")
with tempfile.TemporaryDirectory() as tmp:
    said = drive(shell_on(tmp),
                 ["configure", "system", "dataplane", "interface enp2s0", "commit"])
    check("including this one" not in said,
          f"a box reached on enp1s0 was told that handing enp2s0 over ends "
          f"this session, which would make it a change nobody can make "
          f"remotely: {said!r}")
    check("'enp1s0', which Linux keeps" in " ".join(said.split()),
          f"it does not name the path the box IS reached on, so 'not this "
          f"session' is a claim the operator cannot check: {said!r}")
    print("  two cards: named the path Linux keeps, and did not claim the "
          "session")

with tempfile.TemporaryDirectory() as tmp:
    said = drive(one_card_box(tmp),
                 ["configure", "system", "dataplane", "interface ens18", "commit"])
    check("ends every session on this box, including this one"
          in " ".join(said.split()),
          f"the box whose ONLY card carries the management address was not "
          f"told the restart takes this session with it, which is the whole "
          f"of what a confirmed commit is armed for here: {said!r}")
    print("  one card: the sentence it was written for, unchanged")

# The box that has ALREADY given its only card away. Its address lives on the
# linux_cp pair the dataplane holds, so the default route is on that pair and
# not on any card in /sys - and a reading that compared names alone would call
# this box safe on the one shape where a dataplane restart takes the session
# for certain.
with tempfile.TemporaryDirectory() as tmp:
    said = drive(shell_on(tmp, route=PAIR_ROUTE, tune=TUNE_HANDED_OVER,
                          cards=("enp2s0", "lo")),
                 ["configure", "system", "dataplane", "interface enp2s0", "commit"])
    _flat = " ".join(said.split())
    check("which Linux keeps" not in _flat,
          f"a box reached THROUGH the dataplane was told the path it is "
          f"routed out of is one Linux keeps, because the route is on a pair "
          f"whose name is not the card being named: {said!r}")
    check("is not one of this box's cards" in _flat and "'ens18'" in _flat,
          f"it does not say that the path it is routed out of is not a card "
          f"of its own, which is the whole of why this session may go: "
          f"{said!r}")
    print("  reached through a pair: not claimed safe, and said why")

with tempfile.TemporaryDirectory() as tmp:
    said = drive(shell_on(tmp, route=NO_DEFAULT_ROUTE),
                 ["configure", "system", "dataplane", "interface enp2s0", "commit"])
    check("cannot be read" in said and "which Linux keeps" not in said,
          f"a box that carries no default route cannot say which path this "
          f"session arrived on, and answered anyway: {said!r}")
    print("  no default route: said it could not be read, and said which")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
