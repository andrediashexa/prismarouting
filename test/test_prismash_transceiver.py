"""
Transceivers at the VTY (#590): `show interface transceiver`, `show
interface <name> transceiver`, and the notes `show interface` prints for an
RX power past its warning and a port whose CRC errors are more than 0.01% of
what it received.

The bytes are the plugin's own format (`show bpa transceiver`), built from
test/transceiver_fixtures.py; the decode is test/test_transceiver.py's. What
is held here is which port is read from where - the dataplane through DPDK,
a hot card or af_packet port through Linux, a card Linux owns through the
door - what the screens say when a source is silent, and that the notes
land on the screens operators read.
"""
import contextlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src", "prismash"))
sys.path.insert(0, HERE)
import prismash  # noqa: E402
import transceiver_fixtures as F  # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if cond:
        print(f"  ok: {msg}")
    else:
        print(f"  FAIL: {msg}")
        fail += 1


IFACES = """\
              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
enp1s0                            1      up          9000/0/0/0     rx packets              10400000
enp2s0                            2      up          9000/0/0/0
host-eth5                         3      up          9000/0/0/0
local0                            0     down          0/0/0/0
tap4096                           4      up          9000/0/0/0
"""

HW = """\
              Name                Idx   Link  Hardware
enp1s0                             1     up   enp1s0
  Link speed: 10 Gbps
  extended stats:
    rx_good_packets                          10400000
    rx_crc_errors                               60674
enp2s0                             2     up   enp2s0
  extended stats:
    rx_good_packets                          10400000
"""


def dump(rx_low=False):
    data = F.sfp_low_rx() if rx_low else F.sfp_internal()
    return (F.dump("enp1s0", "dpdk", "sff-8472", data, F.SFF8472_READ,
                   "port 0 device 0000:01:00.0 age 0.1")
            + "interface enp2s0 unread no-module the card answered -5 "
              "(Input/output error) on DPDK port 1\n"
            + "interface host-eth5 unread not-dpdk not a DPDK port\n"
            + "interface tap4096 unread not-dpdk not a DPDK port\n")


def shell(dp_rc=0, rx_low=False, hot=None, nics=(), linux=None):
    """A Shell over a stub dataplane and a stub door. `linux` is {dev:
    dump text} the door answers; any other device is not the host's."""
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL}
    sh._prompt_name = prismash.HOSTNAME_DEFAULT
    asked = []

    def run(_self, cmd):
        asked.append(cmd)
        if dp_rc:
            return 1, "VPP did not respond"
        if cmd == "show interface":
            return 0, IFACES
        if cmd.startswith("show interface "):
            return 0, ""
        if cmd == "show hardware-interfaces":
            return 0, HW
        if cmd.startswith("show bpa transceiver"):
            name = cmd.split()[3:]
            text = dump(rx_low)
            if name:
                keep = [b for b in text.split("interface ")[1:]
                        if b.split()[0] == name[0]]
                text = "".join("interface " + b for b in keep)
            return 0, text
        return 0, ""
    sh.dp = type("D", (), {"TRACE": False, "run": run,
                           "reachable": lambda _s: not dp_rc})()
    sh.host_nics = lambda: [{"name": n} for n in nics]
    doors = []

    def door(argv, timeout=30):
        doors.append(tuple(argv))
        dev = argv[-1]
        if linux and dev in linux:
            return 0, linux[dev]
        return 0, f"interface {dev} unread no-device the card answered " \
                  "-19 (No such device)\n"
    prismash.doors.runner = lambda *a, **k: door
    prismash.hot_cards = lambda path=None: dict(hot or {})
    return sh, asked, doors


def say(sh, line):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        sh.feed(line)
    return out.getvalue()


ETH5 = "\n".join(prismash.transceiver.dump_lines(
    "eth5", "linux", "sff-8472", list(F.sfp_external())))
ENO1 = "\n".join(prismash.transceiver.dump_lines(
    "eno1", "linux", "sff-8079", list(F.sfp_dac())))

print("=== show interface transceiver: every port with a cage ===")
sh, asked, doors = shell(linux={"eth5": ETH5, "eno1": ENO1}, nics=["eno1"])
f = sh.transceiver_facts()
names = [m["name"] for m in f["modules"]]
check(names == ["enp1s0", "enp2s0", "host-eth5", "eno1"],
      f"the DPDK ports, the af_packet port by its Linux card, and the card "
      f"Linux owns - not the TAP: {names}")
check([m["source"] for m in f["modules"]] == ["dpdk", "", "linux", "linux"],
      "each read from where its card is")
check(f["modules"][2]["calibration"] == "external"
      and f["modules"][2]["device"] == "eth5",
      "host-eth5 is read as eth5 and named as the dataplane names it")
check(("/usr/bin/python3", prismash.transceiver.read_argv("eth5")[1], "read",
       "eth5") in doors, f"through the door's one shape: {doors}")
check(f["dataplane"] and f["missing"] == [], "the dataplane answered")
text = say(sh, "show interface transceiver")
check("enp1s0: SFP/SFP+/SFP28, LC connector, 1310 nm" in text
      and "rx power (dBm)   -6.54" in text, "the X520's module and its RX")
check("enp2s0\n    no module answered" in text, "an empty cage, said")
check("eno1: SFP/SFP+/SFP28, copper pigtail" in text
      and "a copper module" in text, "the copper cable on the Linux card")
check(all(len(l) <= 79 for l in text.splitlines()), "79 columns")
print("\n=== show interface <name> transceiver, either order ===")
sh, asked, doors = shell()
one = say(sh, "show interface enp1s0 transceiver")
check(one.startswith("  enp1s0: SFP") and "enp2s0" not in one,
      f"the one port: {one[:60]!r}")
check(say(sh, "show interface transceiver enp1s0") == one,
      "`transceiver <name>` is the same screen")
check(doors == [], "a DPDK port is not asked of the host")
sh, asked, doors = shell(hot={"enp9s0": "0000:09:00.0"},
                         linux={"enp9s0": ETH5.replace("eth5", "enp9s0")})
sh.dp.run = (lambda run: (lambda cmd: (0, "interface enp9s0 unread not-dpdk "
                                          "not a DPDK port\n")
                          if cmd.startswith("show bpa") else run(cmd)))(sh.dp.run)
hot = sh.transceiver_facts(["enp9s0"])["modules"][0]
check(hot["source"] == "linux" and hot["status"] == "ddm",
      "an AF_XDP hot card is read from Linux, under its own name")
sh, asked, doors = shell(linux={"eno1": ENO1})
mg = sh.transceiver_facts(["eno1"])
check([m["name"] for m in mg["modules"]] == ["eno1"] and not mg["missing"],
      "a card Linux owns, named: the management card")
nope = say(sh, "show interface nosuch0 transceiver")
check("no interface matches nosuch0" in nope, f"a name that is neither: {nope!r}")
pg = shell()[0].transceiver_facts(["tap4096"])["modules"][0]
check(pg["status"] == "unreadable" and "DPDK" in pg["why"],
      "a TAP named outright: the dataplane does not drive it through DPDK")

print("\n=== the dataplane silent ===")
sh, asked, doors = shell(dp_rc=1, linux={"eno1": ENO1}, nics=["eno1"])
down = say(sh, "show interface transceiver")
check("the dataplane is not answering" in down and "eno1: SFP" in down,
      f"said, and the Linux card is still read: {down[:120]!r}")
sh, _, _ = shell()
sh.dp.run = lambda cmd: (0, "") if cmd.startswith("show bpa") else (1, "")
check("no port of this box has a module to read" in say(
    sh, "show interface transceiver"), "a box with no port to read says so")

print("\n=== a dataplane whose plugin predates #590 ===")
# Measured on 10.40.200.166 after the upgrade to 0.151.15: the VTY is new at
# once, the dataplane's images at its next restart, and the old plugin
# answers "unknown input". Mutations shown red: plugin_predates() always
# False (the box said "the dataplane is not answering" and "the card's
# driver cannot read a module" of an X520 that can); the Linux fallback kept
# for a dataplane port.
check(prismash.transceiver.plugin_predates(
          "show bpa: unknown input `transceiver'")
      and not prismash.transceiver.plugin_predates(ETH5)
      and not prismash.transceiver.plugin_predates(""),
      "plugin_predates: VPP's refusal of the word, and nothing else")
for _rc in (0, 1):
    sh, _, doors = shell(linux={"eno1": ENO1}, nics=["eno1"])
    sh.dp.run = (lambda rc: lambda cmd: (
        (rc, "show bpa: unknown input `transceiver'")
        if cmd.startswith("show bpa") else (0, "")))(_rc)
    out = say(sh, "show interface enp1s0 transceiver")
    one_door = list(doors)
    out += say(sh, "show interface transceiver")
    doors = one_door
    check("plugin older than this VTY" in " ".join(out.split())
          and "system dataplane restart" in out
          and "not answering" not in out and "driver cannot" not in out
          and "no port of this box" not in out
          and not doors,
          f"an older plugin is said as one, and its port not read as "
          f"Linux's (rc {_rc}): {out!r}")
    f = sh.transceiver_facts()
    check(f["old_plugin"] and f["dataplane"]
          and [m["name"] for m in f["modules"]] == ["eno1"],
          f"and a card Linux holds is still read (rc {_rc}): {f}")

print("\n=== show interface: the notes under the table ===")
sh, asked, doors = shell(rx_low=True)
tab = say(sh, "show interface")
check("enp1s0: rx power -19.00 dBm, below its low warning (-18.01 dBm)" in tab,
      "an RX power past its warning, noted")
check("enp1s0: 0.58% of received frames failed their CRC" in tab,
      "a CRC ratio past 0.01%, noted")
check("enp2s0: " not in tab.split("can forward")[1],
      "and nothing for a port with neither")
check(doors == [], "the table asks no door: it is the screen read most often")
check(all(len(l) <= 79 for l in tab.splitlines()), "79 columns")
sh, _, _ = shell()
clean = say(sh, "show interface")
check("rx power" not in clean and "CRC" in clean,
      "a healthy module has no note; the CRC note does not depend on it")
sh, asked, _ = shell(rx_low=True)
det = say(sh, "show interface enp1s0")
check("below its low warning" in det and "failed their CRC" in det,
      "the one-interface screen carries the same notes")
check("show bpa transceiver enp1s0" in asked, "asking the plugin for that port alone")
n = sh.interface_notes(["enp2s0"])
check(n == [], f"interface_notes on a port with neither: {n}")
lt = sh.linux_transceiver("eth5", "host-eth5")
check(lt["name"] == "host-eth5" and lt["unread"] == "no-device",
      "linux_transceiver names the reading as the dataplane does")


def broken(argv, timeout=30):
    return 126, "the host's door: not an operation the VTY is allowed"


prismash.doors.runner = lambda *a, **k: broken
lb = sh.linux_transceiver("eth5")
check(lb["unread"] == "unavailable" and "not an operation" in lb["why"],
      "an older door says why, rather than nothing")
sh2, _, _ = shell()
sh2.dp.run = lambda cmd: (0, "")
f2 = {"dataplane": True, "missing": [], "modules": []}
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh2.show_transceivers([], f2)
check("no port of this box has a module to read" in out.getvalue(),
      "show_transceivers draws from a reading it is handed (the API's)")

print("\n=== the grammar ===")
o, _, _ = sh._options("show interface ", False)
check("transceiver" in o, f"`show interface ` offers transceiver: {sorted(o)}")
check("transceiver" in prismash.TREE["operational"]["show"][1]["interface"][1]
      [prismash.ARG_INTERFACE][0], "and <interface>'s help names it")

print()
print("ALL TESTS PASSED" if fail == 0 else f"{fail} FAILURES")
sys.exit(1 if fail else 0)
