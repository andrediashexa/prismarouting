"""
The upgrade onto 0.152.1 pins `mtu 9000` where a box had none (#592).

WHY. Until 0.152.1 an interface with no `mtu` statement came up at VPP's
own 9000: the tuner wrote no `ethernet { default-mtu }`. From 0.152.1 it
comes up at 1500 (config.DEFAULT_MTU), because 9000 made `tcp-mss auto`
resolve to 8960 on a box whose subscribers' path is 1500 (measured on
10.40.200.166). Decided with the operator on 2026-10-06: nothing changes BY
ITSELF on a box already installed. So the upgrade writes, once, an explicit
`mtu 9000` into every `interface` block that has no `mtu`, in the saved
configuration and in every configuration the box may apply later without
being asked again - the rollback revisions and a pending `commit confirmed`'s
revert - and leaves the file MTU_KEPT_FILE behind.

WHICH INTERFACES. Every one the file has an `interface` block for: a port, a
sub-interface, a loopback, a bundle, a bundle's member when it has a block of
its own. Measured on VPP 26.06 with the packet generator (#592):

  * a port, a loopback, a bundle, a TAP and a host-interface all register at
    `default-mtu` - 9000 before, 1500 after - so each needs the pin;
  * a sub-interface registers with NO MTU of its own (`0/0/0/0` in `show
    interface`), and VPP forwards it at 9000 whatever its parent says
    (vnet_sw_interface_get_mtu() answers 9000 for an unset MTU; `show adj`
    reads mtu:9000 on pg0.100 with pg0 at 1500). Since #592 the VTY gives a
    sub-interface it creates the box's default, so it needs the pin too - and
    9000 is exactly what it forwarded at.

A bundle's member named only under the bundle (`member <port>`) has no block
and gets none: its own L3 MTU carries no traffic, the bundle's does. The
routing section's `interface` stanzas are FRR's and name Linux interfaces,
so they are never touched.

WHY THE FILE AS WELL AS THE PIN. `mtu <n>` in the VTY sets the L3 MTU. The
frame a DPDK card RECEIVES is fixed once, when VPP configures the device,
from `default-mtu` (VPP 26.06, dpdk_device_setup()). A pinned port on a box
whose default became 1500 would forward 9000 and drop every frame above 1500
it is sent. MTU_KEPT_FILE tells the tuner this box keeps VPP's 9000, as it
always had; `no mtu` on it still goes back to 1500 (config.startup_default_
mtu()), in L3, on a card that can carry it.

ONCE. MTU_KEPT_FILE is also the record that the pin was done: an upgrade that
finds it does nothing, so an operator's `no mtu` afterwards is respected and
never pinned again. And the pin is due only on an upgrade FROM a release
before PIN_BEFORE: a fresh install has no previous stamp, and a box upgraded
from 0.152.1 or later has nothing of the old default left to keep.

Run by appliance/upgrade, the artifact's own entry point, from the release
being installed and before install.sh writes the new version stamp - so a run
that fails after this is re-run with the previous stamp still there, and
MTU_KEPT_FILE is what stops it pinning twice. Not by install.sh: that file is
the medium's too, and a patch release may not change it (tools/
release-gate.sh). A box moved by running install.sh from a source tree is not
pinned; this is the operator's path, the artifact.
"""
import os
import re
import sys
import textwrap

import config
import events

#: The value an interface with no `mtu` had before 0.152.1: VPP's own.
PIN_MTU = config.VPP_DEFAULT_MTU
#: The first release whose default is 1500. An upgrade from a stamp below
#: this one pins; from this one or later, or onto a fresh box, it does not.
PIN_BEFORE = (0, 152, 1)
#: Written once by the pin, read by appliance/prisma-routing-tune (its
#: MTU_KEPT_FILE, the same path): this box keeps VPP's 9000.
MTU_KEPT_FILE = "/etc/prisma-routing/mtu-9000-kept"
CONFIG_PATH = "/etc/prisma-routing/prisma-routing.conf"
ROLLBACK_DIR = "/etc/prisma-routing/rollback"
CONFIRM_PATH = "/etc/prisma-routing/confirm-pending"
#: How many names the event and the screen spell out before "and N more".
NAMES_SHOWN = 12

_STAMP = re.compile(r"^\s*(\d+)\.(\d+)\.(\d+)")


def stamp_version(stamp):
    """(X, Y, Z) of a version stamp such as "0.151.17 (d3044fe7)", or None."""
    m = _STAMP.match(stamp or "")
    return tuple(int(g) for g in m.groups()) if m else None


def pin_due(previous_stamp, kept=False):
    """
    Whether this install has to pin: an UPGRADE from before PIN_BEFORE, on a
    box that has not been pinned already (`kept`: MTU_KEPT_FILE is there).

    An empty stamp is a first install - an ISO build into a fresh root, or a
    box that never had this product (appliance/upgrade's WAS) - and starts at
    1500. A stamp that names no version ("unknown (no git at build time)") is
    still a box that was installed before, and pinning keeps it as it was:
    that is the direction that changes nothing.
    """
    if kept or not (previous_stamp or "").strip():
        return False
    v = stamp_version(previous_stamp)
    return v is None or v < PIN_BEFORE


def names_to_pin(cfg):
    """
    The interfaces `cfg` has an `interface` block for and no `mtu` in, sorted
    the way `show interface` sorts: parents before their sub-interfaces.
    """
    return sorted(cfg.interface_names() - set(cfg.mtus),
                  key=lambda n: (n.split(".")[0], n.count("."), n))


def pin_text(text, mtu=PIN_MTU):
    """
    `text` with ` mtu <mtu>` added to every `interface` block that has no
    `mtu`, and the names it was added to; (None, why) when the result does
    not read back as exactly that.

    A LINE INSERTED, NOT THE FILE RE-RENDERED. A saved file is the operator's:
    comments, a hand-written order and a revision's own header survive, and
    the only difference is one line in each block. The line goes at the end
    of the block, where `save` writes an interface's mtu itself.

    And then READ BACK, which is the reason this can be trusted with the
    file a box replays at boot: the result is parsed and must hold the same
    configuration with exactly these MTUs added, and nothing else - or it is
    not written at all.
    """
    before = config.Config.parse(text)
    names = names_to_pin(before)
    if not names:
        return text, []
    want = set(names)
    out, done, section, open_, depth = [], set(), "cgnat", None, None
    for line in text.split("\n"):
        s = line.strip()
        indented = line[:1] in (" ", "\t")
        # The block a pin is waiting for ends at its own `exit` - at the
        # indentation of the block's first statement, so a deeper one is not
        # taken for it - or at the next line that is not inside it. The pin
        # goes just before that, where `save` itself writes a block's mtu.
        if open_ and indented and s and depth is None:
            depth = line[:len(line) - len(line.lstrip())]
        if open_ and (not indented and s
                      or s == "exit" and line.startswith(depth or " ")
                      and line[len(depth or " "):][:1] not in (" ", "\t")):
            out.append(f"{depth or ' '}mtu {mtu}")
            done.add(open_)
            open_ = None
        out.append(line)
        if s == config.ROUTING_HEADER:
            section = "routing"
        elif s == config.CGNAT_HEADER:
            section = "cgnat"
        if section == "routing" or indented:
            continue
        w = s.split()
        if len(w) == 2 and w[0] == "interface" and w[1] in want \
                and w[1] not in done:
            open_, depth = w[1], None
    if open_:
        # The file ends inside the block: no `exit`, nothing after it.
        while out and not out[-1].strip():
            out.pop()
        out += [f"{depth or ' '}mtu {mtu}", ""]
        done.add(open_)
    new = "\n".join(out)
    after = config.Config.parse(new)
    expect = config.Config.parse(text)
    for n in names:
        expect.mtus[n] = mtu
    if done != want or after.text() != expect.text() \
            or len(after.unknown) != len(before.unknown):
        return None, (f"it did not read back as the same configuration with "
                      f"mtu {mtu} on {', '.join(sorted(want - done)) or 'it'}")
    return new, names


def rewrite(path, text, keep_mtime=False):
    """
    Put `text` at `path` whole - a temporary file renamed over it - with the
    original's mode and owner, and its modification time when `keep_mtime`:
    a rollback revision's time is the time `show rollback` lists it at, and
    pinning it is not a commit. True when written.
    """
    tmp = path + ".mtu592"
    try:
        st = os.stat(path)
        with open(tmp, "w") as fh:
            fh.write(text)
        os.chmod(tmp, st.st_mode & 0o7777)
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except OSError:
            pass     # a DESTDIR root this account does not own
        os.replace(tmp, path)
        if keep_mtime:
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
    return True


def pin_file(path, keep_mtime=False):
    """
    Pin one configuration file in place: (names pinned, why not) - ([], None)
    when it needed nothing or is not there.
    """
    try:
        with open(path) as fh:
            text = fh.read()
    except FileNotFoundError:
        return [], None
    except OSError as e:
        return [], f"{path}: {e.strerror}"
    new, names = pin_text(text)
    if new is None:
        return [], f"{path}: {names}"
    if not names:
        return [], None
    if not rewrite(path, new, keep_mtime):
        return [], f"{path}: could not be written"
    return names, None


def named_list(names, shown=NAMES_SHOWN):
    """`a, b, c`, or the first `shown` of them and "and N more"."""
    if len(names) <= shown:
        return ", ".join(names)
    return f"{', '.join(names[:shown])} and {len(names) - shown} more"


def event_text(names):
    """The one sentence `show log` carries for the pin (#592)."""
    if not names:
        return ("no interface needed an mtu pinned at 9000, the default "
                "before 0.152.1; this box keeps it for its cards")
    head = ("interfaces with no mtu pinned at 9000, the default before "
            "0.152.1: ")
    # As many names as fit the record's one sentence (events.TEXT_MAX), so
    # the count of the rest is never the part cut off.
    shown = len(names)
    while shown > 1 and len(head + named_list(names, shown)) > events.TEXT_MAX:
        shown -= 1
    return head + named_list(names, shown)


def kept_text(previous_stamp, names):
    """What MTU_KEPT_FILE says to whoever opens it."""
    return (
        f"# Written once by the upgrade onto 0.152.1 from "
        f"{previous_stamp.strip()} (#592).\n"
        "#\n"
        "# This box was installed when an interface with no `mtu` statement\n"
        "# came up at VPP's 9000; from 0.152.1 such an interface comes up at\n"
        "# 1500. So that nothing changed by itself, the upgrade wrote\n"
        "# `mtu 9000` into every interface of the saved configuration that\n"
        "# had none, and prisma-routing-tune reads this file and keeps the\n"
        "# frame the cards receive at VPP's 9000.\n"
        "#\n"
        "# While it is here no upgrade pins anything again, so `no mtu` on an\n"
        "# interface is respected: it goes back to 1500. Removing it moves the\n"
        "# cards to 1500 at the next boot; take `mtu 9000` off first.\n"
        f"pinned {' '.join(names)}\n")


def pin_box(root, previous_stamp, now=None):
    """
    The whole pin on the box under `root`: the saved configuration, the
    rollback revisions, a pending confirm's revert, MTU_KEPT_FILE and the
    event. Returns the lines the upgrade prints, [] when nothing was due.
    """
    kept = root + MTU_KEPT_FILE
    if not pin_due(previous_stamp, os.path.exists(kept)):
        return []
    saved, why = pin_file(root + CONFIG_PATH)
    problems = [why] if why else []
    revs = 0
    rdir = root + ROLLBACK_DIR
    try:
        found = sorted(f for f in os.listdir(rdir) if f.endswith(".conf"))
    except OSError:
        found = []
    for f in found:
        got, why = pin_file(os.path.join(rdir, f), keep_mtime=True)
        revs += bool(got)
        if why:
            problems.append(why)
    pending, why = pin_file(root + CONFIRM_PATH)
    if why:
        problems.append(why)
    out = ["  The default MTU is 1500 from this release; it was VPP's 9000.",
           "  This box was installed before 0.152.1, so nothing on it "
           "changes:"]
    if saved:
        # Folded at the console's width: the names are the operator's, and a
        # box with forty VLANs would otherwise print one 900-column row.
        out.append(f"    'mtu 9000' pinned on {len(saved)} interface(s) of "
                   f"the saved configuration,")
        out.append(f"    {CONFIG_PATH}:")
        out += textwrap.wrap(named_list(saved), 79, initial_indent=" " * 6,
                             subsequent_indent=" " * 6,
                             break_on_hyphens=False)
    else:
        out.append(f"    {CONFIG_PATH} had no interface without an mtu")
    if revs:
        out.append(f"    and in {revs} rollback revision(s)")
    if pending:
        out.append("    and in the revert a 'commit confirmed' is waiting on")
    for p in problems:
        out.append(f"    NOT PINNED: {p}")
        out.append("      add 'mtu 9000' to its interfaces by hand, or they "
                   "come up at 1500")
    try:
        os.makedirs(os.path.dirname(kept), exist_ok=True)
        with open(kept, "w") as fh:
            fh.write(kept_text(previous_stamp, saved))
        os.chmod(kept, 0o644)
        out.append(f"    and its cards keep VPP's 9000 frame: {MTU_KEPT_FILE}")
    except OSError as e:
        out.append(f"    NOT WRITTEN: {MTU_KEPT_FILE}: {e.strerror}")
    out.append("  'no mtu' on an interface now goes back to 1500.")
    ev = events.make_event("host", "notice", event_text(saved),
                           origin="the box", now=now)
    # The record's directory, which install.sh states (0750) only further
    # down: this runs before it, and on a box it is already there.
    try:
        os.makedirs(os.path.dirname(root + events.EVENTS_PATH), mode=0o750,
                    exist_ok=True)
    except OSError:
        pass
    events.append_event(ev, root + events.EVENTS_PATH)
    return out


# not a unit: the command line appliance/upgrade calls; pin_box() is what
# it runs, and test/test_mtupin.py runs this as a program too.
def main(argv):
    root, prev = "", None
    while argv:
        if argv[0] == "--root" and len(argv) > 1:
            root, argv = argv[1].rstrip("/"), argv[2:]
        elif argv[0] == "--previous" and len(argv) > 1:
            prev, argv = argv[1], argv[2:]
        else:
            break
    if prev is None or argv:
        print("usage: mtupin.py [--root <dir>] --previous <version stamp>")
        return 2
    for line in pin_box(root, prev):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
