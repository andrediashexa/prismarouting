#!/usr/bin/env python3
"""
test_power_button.py - appliance/prisma-power-button (#478).

The box ignored an ACPI shutdown: nothing on it turned the power key into one.
This holds the three things that decide whether it does now - which input
device is the power button, which event is the press, and that a press runs
the shutdown and anything else does not - against /proc/bus/input/devices as
a Proxmox guest printed it and against a real input_event byte layout.

Mutations shown red: matching the name by substring (a "Power Button" inside
another device's name); accepting the key's release (value 0) as a press.
"""
import importlib.machinery
import importlib.util
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def load_power_button():
    """
    The script as a module. It has no .py suffix and a dash in its name, so
    `import` cannot reach it and importlib needs to be told the loader. Loaded
    through THIS function, as test_tune.py loads the tuner, so that
    test/test_every_function.py reads `pb.power_buttons(...)` as a call into
    the product rather than into a foreign import.
    """
    loader = importlib.machinery.SourceFileLoader(
        "power_button", os.path.join(HERE, "prisma-power-button"))
    spec = importlib.util.spec_from_loader("power_button", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


pb = load_power_button()

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


# As the #475 bench's guest printed it (Proxmox VE 9, i440fx), trimmed to the
# lines the parse reads, with one device added whose name only CONTAINS the
# words - which must not be taken for the button.
DEVICES = """I: Bus=0011 Vendor=0001 Product=0001 Version=ab41
N: Name="AT Translated Set 2 keyboard"
H: Handlers=sysrq kbd leds event0

I: Bus=0003 Vendor=0627 Product=0001 Version=0001
N: Name="QEMU QEMU USB Tablet"
H: Handlers=mouse2 event3 js0

I: Bus=0019 Vendor=0000 Product=0001 Version=0000
N: Name="Power Button"
P: Phys=LNXPWRBN/button/input0
H: Handlers=kbd event4

I: Bus=0019 Vendor=0000 Product=0001 Version=0000
N: Name="Power Button"
P: Phys=PNP0C0C/button/input0
H: Handlers=kbd event5

I: Bus=0003 Vendor=1234 Product=0001 Version=0001
N: Name="Acme Keyboard Power Button Consumer Control"
H: Handlers=kbd event6
"""

print("=== which input devices are the power button ===")
check(pb.power_buttons(DEVICES) == ["event4", "event5"],
      f"expected event4 and event5, got {pb.power_buttons(DEVICES)!r}")
check(pb.power_buttons("") == [], "an empty device list named a button")
print("  both ACPI buttons, and not a keyboard whose name contains the words")


def ev(etype, code, value):
    return pb.EVENT.pack(0, 0, etype, code, value)


print("\n=== which event is the press ===")
check(pb.is_press(ev(1, 116, 1)), "KEY_POWER going down is not a press")
check(not pb.is_press(ev(1, 116, 0)), "the key's release was taken as a press")
check(not pb.is_press(ev(1, 116, 2)), "an autorepeat was taken as a press")
check(not pb.is_press(ev(1, 30, 1)), "another key was taken as the power key")
check(not pb.is_press(ev(0, 0, 0)), "a SYN report was taken as a press")
check(not pb.is_press(b"\0" * 5), "a short read was taken as a press")
check(pb.EVENT.size == 24, f"an input_event is {pb.EVENT.size} bytes, not 24")
print("  the press only: not its release, not a repeat, not another key")


def run(devices_text, events):
    """The script end to end, with the device list and the event node faked
    and the shutdown replaced by a marker file."""
    with tempfile.TemporaryDirectory() as d:
        dev = os.path.join(d, "devices")
        with open(dev, "w") as f:
            f.write(devices_text)
        for n, data in events.items():
            with open(os.path.join(d, n), "wb") as f:
                f.write(data)
        marker = os.path.join(d, "shutdown")
        env = dict(os.environ, PRISMA_INPUT_DEVICES=dev, PRISMA_INPUT_DIR=d,
                   PRISMA_POWEROFF=f"touch {marker}", PRISMA_BUTTON_GRACE="1")
        r = subprocess.run([sys.executable, os.path.join(HERE,
                            "prisma-power-button")], env=env,
                           capture_output=True, text=True, timeout=10)
        return r.returncode, r.stdout, os.path.exists(marker)


print("\n=== a press shuts the box down; no button is not a failure ===")
rc, out, shut = run(DEVICES, {"event4": ev(0, 0, 0) + ev(1, 116, 1),
                              "event5": b""})
check(rc == 0 and shut, f"a press did not run the shutdown: rc {rc}, {out!r}")
check("power button pressed" in out, f"the press was not said: {out!r}")
rc, out, shut = run("I: Bus=0011\nN: Name=\"AT keyboard\"\nH: Handlers=event0\n",
                    {})
check(rc == 0 and not shut and "no power-button" in out,
      f"a machine with no button was not a quiet exit 0: rc {rc}, {out!r}")
print("  a press runs the shutdown and says so; no button exits 0, once")

print("\n=== a button that arrives after the service is still watched ===")
# The 0.140.0 gate: the unit started 0.4 s before the kernel registered the
# button, read an empty list, and exited as a machine with none.
_answers = ["", "", DEVICES]
_slept = []
_nodes = pb.wait_for_buttons(lambda: _answers.pop(0) if len(_answers) > 1
                             else _answers[0],
                             lambda n: True, _slept.append, 60, 0.5)
check(_nodes == ["event4", "event5"] and len(_slept) == 2,
      f"a button registered after the first read was not waited for: "
      f"{_nodes!r} after {len(_slept)} wait(s)")
# Registered, and its node not made yet by udev: waited for as well.
_made = {"event4": [False, True]}
_nodes = pb.wait_for_buttons(lambda: DEVICES,
                             lambda n: _made.get(n, [False]).pop(0)
                             if len(_made.get(n, [False])) > 1
                             else _made.get(n, [False])[0],
                             lambda s: None, 60, 0.5)
check(_nodes == ["event4"],
      f"a node udev had not made yet was not waited for: {_nodes!r}")
# None, ever: given up after the grace, and not before.
_slept = []
_nodes = pb.wait_for_buttons(lambda: "", lambda n: True, _slept.append, 3, 1)
check(_nodes == [] and len(_slept) == 3,
      f"no button was given up on after {len(_slept)} wait(s), not 3")
# An unreadable list is waited through like an empty one.
def _unreadable():
    raise OSError(2, "No such file or directory")
check(pb.wait_for_buttons(_unreadable, lambda n: True, lambda s: None, 1, 1)
      == [], "an unreadable device list was not treated as no button")
print("  a late button and a late node are waited for; none is given up on "
      "after the grace")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
