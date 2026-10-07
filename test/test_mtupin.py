#!/usr/bin/env python3
"""
src/prismash/mtupin.py (#592): the upgrade onto 0.152.1 pins `mtu 9000` on
every interface a box's saved configuration has no `mtu` for, once, and says
so in the upgrade's output and in `show log`.

Each behaviour, shown red by a mutation (logged in the #592 commit):

  * only an UPGRADE from before 0.152.1 pins: a fresh install (no previous
    stamp) and a box already on 0.152.1 do not (mutation: pin_due()
    answering True for an empty stamp - a fresh box is pinned at 9000);
  * ONCE: a box carrying mtu-9000-kept is never pinned again, so an
    operator's `no mtu` afterwards is respected (mutation: pin_box() not
    asking for the file - the second run pins the interface again);
  * every `interface` block with no `mtu` is pinned, sub-interfaces and
    loopbacks included, and the routing section's FRR stanzas never are
    (mutation: pin_text() not tracking the routing section - a Linux name
    gets an `mtu` and the read-back refuses the file);
  * the file is otherwise untouched, and refused whole when it would not read
    back as the same configuration plus the pins;
  * a rollback revision keeps its time (mutation: rewrite() without the
    utime);
  * the event `show log` reads names the interfaces, in one sentence that
    fits the record.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src", "prismash")
sys.path.insert(0, SRC)
import mtupin  # noqa: E402
import config  # noqa: E402
import events  # noqa: E402

fail = 0


def check(c, msg):
    global fail
    print(("  [ ok ] " if c else "  FAIL: ") + msg)
    fail += 0 if c else 1


SAVED = """!
! Prisma Routing
!
interface GigabitEthernet0/8/0
 linux-name vppwan0
 ipv4 address 10.0.0.1/24
 exit
interface GigabitEthernet0/8/0.100
 vlan 100
 mtu 1500
 exit
interface GigabitEthernet0/8/0.200
 vlan 200
 ipv4 address 10.2.0.1/24
 exit
interface loop0
 ipv4 address 192.0.2.1/32
 exit
! routing
interface vppwan0
 ip ospf area 0
exit
router ospf
 ospf router-id 192.0.2.1
exit
"""

print("=== which releases an upgrade pins from ===")
check(mtupin.stamp_version("0.151.17 (d3044fe7)") == (0, 151, 17)
      and mtupin.stamp_version("unknown (no git at build time)") is None
      and mtupin.stamp_version("") is None,
      "stamp_version reads X.Y.Z off a stamp, and nothing off anything else")
check(mtupin.pin_due("0.151.17 (d3044fe7)") is True
      and mtupin.pin_due("0.150.2") is True
      and mtupin.pin_due("0.152.0 (abc)") is True,
      "an upgrade from before 0.152.1 pins")
check(mtupin.pin_due("") is False and mtupin.pin_due("  \n") is False,
      "a first install - no previous stamp - does not: it starts at 1500")
check(mtupin.pin_due("0.152.1 (abc)") is False
      and mtupin.pin_due("0.153.0") is False,
      "an upgrade from 0.152.1 or later does not")
check(mtupin.pin_due("unknown (no git at build time)") is True,
      "a stamp naming no version is a box installed before: pinning keeps it")
check(mtupin.pin_due("0.151.17", kept=True) is False,
      "a box already pinned is not pinned again")

print("\n=== which interfaces, and where the line goes ===")
_cfg = config.Config.parse(SAVED)
check(mtupin.names_to_pin(_cfg) == ["GigabitEthernet0/8/0",
                                    "GigabitEthernet0/8/0.200", "loop0"],
      f"every block with no mtu, sub-interface and loopback included, and "
      f"not the one that has one: {mtupin.names_to_pin(_cfg)}")
new, names = mtupin.pin_text(SAVED)
check(names == ["GigabitEthernet0/8/0", "GigabitEthernet0/8/0.200", "loop0"],
      f"pin_text names what it pinned: {names}")
check(new is not None and "interface vppwan0\n ip ospf area 0\nexit" in new
      and new.count(" mtu 9000") == 3,
      f"three pins, and the routing section's FRR stanza untouched: {new!r}")
check(new and " ipv4 address 192.0.2.1/32\n mtu 9000\n exit" in new,
      "the line goes at the end of the block, where `save` writes it")
# The same name in both sections - a card paired under its own name (#389)
# carries an FRR stanza called what its dataplane block is called - with the
# routing section first, as a hand-written file may have it: the pin goes in
# the dataplane's block, never in FRR's.
_both = ("! routing\ninterface ens19\n ip ospf area 0\nexit\n"
         "! cgnat\ninterface ens19\n ipv4 address 192.0.2.5/24\n exit\n")
_bnew, _bnames = mtupin.pin_text(_both)
check(_bnames == ["ens19"] and _bnew is not None
      and "interface ens19\n ip ospf area 0\nexit" in _bnew
      and " ipv4 address 192.0.2.5/24\n mtu 9000\n exit" in _bnew,
      f"an FRR stanza of the same name is never pinned: {_bnew!r}")
_back = config.Config.parse(new or "")
check(_back.mtus == {"GigabitEthernet0/8/0": 9000,
                     "GigabitEthernet0/8/0.100": 1500,
                     "GigabitEthernet0/8/0.200": 9000, "loop0": 9000},
      f"it reads back with exactly those MTUs: {_back.mtus}")
check(new and new.startswith("!\n! Prisma Routing\n!\n"),
      "the operator's comments are kept: the file is not re-rendered")
again, more = mtupin.pin_text(new or "")
check(again == new and more == [],
      "a file already pinned is left exactly as it is")
check(mtupin.pin_text("!\n! Prisma Routing\n!\n") == ("!\n! Prisma Routing\n!\n", []),
      "the installer's empty file has nothing to pin")
check(mtupin.pin_text("interface loop1\n  ipv4 address 192.0.2.9/32\n"
                      "  exit\n")[0]
      == "interface loop1\n  ipv4 address 192.0.2.9/32\n  mtu 9000\n  exit\n",
      "a block indented by hand is pinned at its own indentation")
check(mtupin.pin_text("interface loop2\n ipv4 address 192.0.2.8/32\n")[0]
      == "interface loop2\n ipv4 address 192.0.2.8/32\n mtu 9000\n",
      "a file that ends inside the block is pinned at its end")
# A block that cannot be pinned without changing what the file says is not
# written at all: the read-back is what protects the file the box replays.
_bad, _why = mtupin.pin_text("interface loop4\n description x\n"
                             "   exit\n mtu 1400\n")
check(_bad is None and "did not read back" in _why,
      f"a file the pin would change in any other way is refused: {_why!r}")

print("\n=== the files on a box, the record and the output ===")
_root = tempfile.mkdtemp()
_etc = os.path.join(_root, "etc", "prisma-routing")
os.makedirs(os.path.join(_etc, "rollback"))
os.makedirs(os.path.join(_root, "var", "log", "prisma-routing"))
with open(os.path.join(_etc, "prisma-routing.conf"), "w") as fh:
    fh.write(SAVED)
os.chmod(os.path.join(_etc, "prisma-routing.conf"), 0o660)
_rev = os.path.join(_etc, "rollback", "3.conf")
with open(_rev, "w") as fh:
    fh.write("# commit by noc\n" + SAVED)
os.utime(_rev, (1790000000, 1790000000))
with open(os.path.join(_etc, "rollback", "0.conf"), "w") as fh:
    fh.write("# nothing to pin\ninterface loop9\n mtu 1400\n exit\n")
with open(os.path.join(_etc, "confirm-pending"), "w") as fh:
    fh.write("# prisma-routing commit confirmed\n# deadline 1\n"
             "interface loop5\n exit\n")

out = mtupin.pin_box(_root, "0.151.17 (d3044fe7)", now=1791000000)
text = "\n".join(out)
print("\n".join("    | " + l for l in out))
with open(os.path.join(_etc, "prisma-routing.conf")) as fh:
    _saved = fh.read()
check(_saved == new, "the saved configuration is the pinned text")
check(os.stat(os.path.join(_etc, "prisma-routing.conf")).st_mode & 0o777
      == 0o660, "and keeps its mode: it holds password hashes")
check(int(os.path.getmtime(_rev)) == 1790000000,
      "a rollback revision keeps its time, which `show rollback` lists")
with open(_rev) as fh:
    _r = fh.read()
check(_r.startswith("# commit by noc\n") and _r.count(" mtu 9000") == 3,
      f"and is pinned under its own header: {_r[:60]!r}")
with open(os.path.join(_etc, "confirm-pending")) as fh:
    check(" mtu 9000" in fh.read(), "a pending confirm's revert is pinned too")
check("'mtu 9000' pinned on 3 interface(s) of the saved configuration,\n"
      "    /etc/prisma-routing/prisma-routing.conf:" in text
      and "GigabitEthernet0/8/0, GigabitEthernet0/8/0.200, loop0" in text
      and "and in 1 rollback revision(s)" in text
      and "'no mtu' on an interface now goes back to 1500." in text,
      f"the upgrade says what it pinned and where: {out}")
check(all(len(l) <= 79 for l in out),
      f"every line fits the console: {[l for l in out if len(l) > 79]}")
_kept = os.path.join(_root, mtupin.MTU_KEPT_FILE.lstrip("/"))
check(os.path.exists(_kept),
      "mtu-9000-kept is written: the tuner keeps VPP's 9000 on this box")
with open(_kept) as fh:
    check("pinned GigabitEthernet0/8/0 GigabitEthernet0/8/0.200 loop0"
          in fh.read(), "and says what was pinned")
_ev = events.read_events(os.path.join(_root, events.EVENTS_PATH.lstrip("/")))
check(len(_ev) == 1 and _ev[0]["text"]
      == "interfaces with no mtu pinned at 9000, the default before "
         "0.152.1: GigabitEthernet0/8/0, GigabitEthernet0/8/0.200, loop0"
      and _ev[0]["source"] == "host" and _ev[0]["severity"] == "notice"
      and events.event_error(_ev[0]) is None,
      f"the event `show log` reads: {_ev}")

# ONCE: the operator takes the pin off one interface, and the next upgrade
# from an old stamp - a re-run - leaves it off.
with open(os.path.join(_etc, "prisma-routing.conf"), "w") as fh:
    fh.write(_saved.replace(" ipv4 address 192.0.2.1/32\n mtu 9000\n",
                            " ipv4 address 192.0.2.1/32\n"))
check(mtupin.pin_box(_root, "0.151.17 (d3044fe7)") == [],
      "a box that was pinned is not pinned again")
with open(os.path.join(_etc, "prisma-routing.conf")) as fh:
    check(" ipv4 address 192.0.2.1/32\n exit" in fh.read(),
          "and the operator's `no mtu` stands")

# A fresh install and a box from 0.152.1 or later: nothing, said or done.
_fresh = tempfile.mkdtemp()
os.makedirs(os.path.join(_fresh, "etc", "prisma-routing"))
with open(os.path.join(_fresh, "etc", "prisma-routing",
                       "prisma-routing.conf"), "w") as fh:
    fh.write("interface loop0\n exit\n")
check(mtupin.pin_box(_fresh, "") == [] and mtupin.pin_box(_fresh, "0.152.1")
      == [] and not os.path.exists(os.path.join(
          _fresh, mtupin.MTU_KEPT_FILE.lstrip("/"))),
      "a fresh install and an upgrade from 0.152.1 pin nothing")

# A box with no saved configuration at all still keeps its cards' frame.
_bare = tempfile.mkdtemp()
_bout = mtupin.pin_box(_bare, "0.150.0")
check(os.path.exists(os.path.join(_bare, mtupin.MTU_KEPT_FILE.lstrip("/")))
      and any("had no interface without an mtu" in l for l in _bout),
      f"a box with nothing to pin is still marked as keeping 9000: {_bout}")

print("\n=== the pieces ===")
check(mtupin.named_list(["a", "b"]) == "a, b"
      and mtupin.named_list(list("abcde"), 2) == "a, b and 3 more",
      "named_list spells out a few and counts the rest")
_many = [f"GigabitEthernet0/8/0.{i}" for i in range(400)]
check(len(mtupin.event_text(_many)) <= events.TEXT_MAX
      and mtupin.event_text(_many).endswith("and 391 more"),
      f"a long list fits the record's one sentence, the count kept: "
      f"{mtupin.event_text(_many)[-40:]!r}")
check("no interface needed" in mtupin.event_text([]),
      "an upgrade that pinned nothing still says what it did")
check(mtupin.kept_text("0.151.17 (x)\n", ["a"]).startswith(
      "# Written once by the upgrade onto 0.152.1 from 0.151.17 (x) (#592)"),
      "kept_text names the release the box came from")
_f = os.path.join(_root, "rw.conf")
with open(_f, "w") as fh:
    fh.write("x")
os.chmod(_f, 0o640)
check(mtupin.rewrite(_f, "y") is True and open(_f).read() == "y"
      and os.stat(_f).st_mode & 0o777 == 0o640,
      "rewrite puts the text whole and keeps the mode")
check(mtupin.rewrite(os.path.join(_root, "no", "such"), "y") is False,
      "rewrite says when it could not")
check(mtupin.pin_file(os.path.join(_root, "absent.conf")) == ([], None),
      "pin_file: a file that is not there needs nothing")

print("\n=== as appliance/upgrade runs it ===")
_prog = tempfile.mkdtemp()
os.makedirs(os.path.join(_prog, "etc", "prisma-routing"))
with open(os.path.join(_prog, "etc", "prisma-routing",
                       "prisma-routing.conf"), "w") as fh:
    fh.write("interface loop0\n exit\n")
r = subprocess.run([sys.executable, os.path.join(SRC, "mtupin.py"),
                    "--root", _prog, "--previous", "0.151.17 (d3044fe7)"],
                   capture_output=True, text=True)
check(r.returncode == 0 and "on 1 interface(s) of" in r.stdout
      and "      loop0" in r.stdout,
      f"the program pins and prints: {r.returncode} {r.stdout!r} {r.stderr!r}")
r = subprocess.run([sys.executable, os.path.join(SRC, "mtupin.py")],
                   capture_output=True, text=True)
check(r.returncode == 2 and "usage:" in r.stdout,
      f"with no --previous it says how it is called: {r.stdout!r}")

print()
print("ALL TESTS PASSED" if not fail else f"{fail} FAILURES")
sys.exit(1 if fail else 0)
