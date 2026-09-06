#!/usr/bin/env python3
"""
hexash - the HexaRouting VTY.

One CLI for routing and CGNAT, with a Juniper-style configuration model:
changes edit a *candidate*, `commit` applies it, and `rollback` returns to any
of the previous committed revisions.

Why not a daemon registered with vtysh: vtysh's daemon list is compiled in
(vtysh_client[]) with no plugin mechanism. Registering a new daemon would mean
maintaining a vtysh fork for the life of the product. Forwarding costs nothing
and does not pin us to an FRR release.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tempfile
import time

from config import Config, Pool
import vppapi

readline = None

CONFIG_PATH = os.environ.get("HEXA_CONFIG", "/etc/hexarouting/hexarouting.conf")
ROLLBACK_DIR = os.environ.get("HEXA_ROLLBACK", "/etc/hexarouting/rollback")
ROLLBACK_KEEP = 20
VPPCTL = os.environ.get("VPPCTL", "vppctl")
VTYSH = os.environ.get("VTYSH", "vtysh")
FRR_RELOAD = os.environ.get("FRR_RELOAD", "/usr/lib/frr/frr-reload.py")

# One source of truth, written by the build. A version compiled into the code
# drifts from the tag the moment someone forgets to edit it, and a box that
# misreports its version sends every later investigation the wrong way.
VERSION_PATH = os.environ.get("HEXA_VERSION_FILE",
                              "/usr/share/hexarouting/VERSION")


def hexarouting_version():
    """Version of this build, or a string that admits it is unknown."""
    for path in (VERSION_PATH,
                 os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "..", "VERSION")):
        try:
            with open(path) as f:
                v = f.read().strip()
            if v:
                return v
        except OSError:
            continue
    return "unknown (no VERSION file)"

# vppctl exits 0 even when a command fails: the text is what says so. Errors
# come out as "<command>: <message>" or "unknown input ...". Replacing this
# with the binary API, which has real return codes, is on the roadmap.
VPP_ERROR = re.compile(r"^(unknown input\b|[a-z][a-z0-9 _-]*: \S)")

# ---------------------------------------------------------------------------
# Command tree, used for completion and for "?".
#
# Each entry is (description, subtree). A None subtree ends the command; a
# string in its place is the expected-argument hint, shown by "?" the way
# Juniper and Cisco both do - "<A.B.C.D/M>" is more useful than an empty list.
#
# FRR's commands are not reproduced here: there are thousands and they change
# with every release. The tree covers the entry points and then admits it does
# not know, rather than suggesting commands that may no longer exist.
# ---------------------------------------------------------------------------

ARG_PREFIX = "<A.B.C.D/M>"
ARG_NUM = "<1-65535>"
ARG_WORD = "<word>"
ARG_REV = "<0-19>"

SHOW_CGNAT = {
    "pool": ("configured CGNAT pools", None),
    "session": ("active sessions per thread", None),
    "blocks": ("blocks allocated in dynamic mode", None),
    "logging": ("IPFIX export state", None),
    "history": ("dated configuration trail", None),
}

SHOW = {
    "cgnat": ("CGNAT information", SHOW_CGNAT),
    "configuration": ("candidate configuration", {
        "compare": ("differences against the running configuration", None)}),
    "running-config": ("configuration in effect, CGNAT and routing", None),
    "rollback": ("committed revisions available", None),
    "ip": ("IP information (FRR)", None),
    "bgp": ("BGP information (FRR)", None),
    "isis": ("IS-IS information (FRR)", None),
    "interface": ("interfaces (FRR)", None),
    "version": ("HexaRouting, VPP and FRR versions", None),
    "logging": ("system log (FRR)", None),
}

# Offered inside an `interface <name>` block. It is not part of the mode tree
# because the block is entered by an argument, not by a keyword the tree can
# walk into - and an operator who cannot discover `mtu` with `?` will inherit
# the TAP's 9000 and spend an afternoon on an OSPF adjacency stuck in Exchange.
INTERFACE_BLOCK = {
    "mtu": ("MTU, on both the dataplane interface and its TAP", "<576-9216>"),
    "exit": ("leave the interface", None),
}

CGNAT_POOL = {
    "inside": ("inside prefix, may repeat", ARG_PREFIX),
    "outside": ("public prefix, may repeat", ARG_PREFIX),
    "block-size": ("ports per block, power of two", "<64-65536>"),
    "ports": ("usable port range", "<min> <max>"),
    "policy": ("how subscribers spread across public addresses", {
        "interleaved": ("neighbours on distinct public addresses", None),
        "blocked": ("neighbours on the same public address", None),
    }),
    "timeout": ("session lifetimes for this pool; absent inherits the global", {
        "udp": ("seconds", ARG_NUM),
        "icmp": ("seconds", ARG_NUM),
        "tcp-syn": ("seconds, half-open TCP", ARG_NUM),
        "gre": ("seconds, a PPTP call's GRE mapping", ARG_NUM),
        "tcp-transitory": ("seconds", ARG_NUM),
        "tcp-established": ("seconds", ARG_NUM),
        "port-holddown": ("seconds a released port waits", ARG_NUM),
        "alg-expect": ("seconds an announced media port waits", ARG_NUM),
    }),
    "max-blocks": ("how many blocks one subscriber may hold", ARG_NUM),
    "filter": ("which inbound packets a mapping accepts (RFC 4787 REQ-8)", {
        "endpoint-independent": ("anyone may reach an open mapping "
                                 "(full cone, the default)", None),
        "address-dependent": ("only the remote the subscriber contacted "
                              "(restricted cone)", None),
    }),
    "alg": ("application-level gateways for this pool", {
        "ftp": ("FTP PORT/EPRT and the 227/229 replies", "<on|off>"),
        "rtsp": ("RTSP Transport client_port", "<on|off>"),
        "pptp": ("PPTP control channel and its GRE data channel", "<on|off>"),
    }),
    "mode": ("how the block is assigned", {
        "deterministic": ("by formula, no allocation logging", None),
        "dynamic": ("on demand, with allocation logging", None),
    }),
    "exit": ("leave the pool", None),
}

CGNAT_CONFIG = {
    "pool": ("create or edit a pool", ARG_WORD),
    "interface": ("enable CGNAT on an interface", "<interface> inside|outside"),
    "timeout": ("session lifetimes", {
        "udp": ("seconds", ARG_NUM),
        "icmp": ("seconds", ARG_NUM),
        "tcp-transitory": ("seconds", ARG_NUM),
        "tcp-established": ("seconds", ARG_NUM),
    }),
    "block-holddown": ("seconds a block stays reserved once idle", ARG_NUM),
    "logging": ("IPFIX export of block events", {
        "enable": ("enable UDP export", None),
        "disable": ("disable UDP export", None),
        "tcp": ("export over TCP", "<collector> [port <n>]"),
    }),
    "session-table": ("session table sizing", "buckets <n> memory <MB>"),
}

COMMIT = {
    "check": ("validate the candidate without applying it", None),
    "confirmed": ("apply, and roll back unless confirmed", "<1-60> minutes"),
    "comment": ("record a note with this commit", "<text>"),
}

# The live installation medium, and the installer it carries.
#
# "install" appears in the VTY only when the box is running FROM that medium.
# Offering it on an installed appliance is offering to overwrite the box the
# operator is logged into, and a command that destroys a running router should
# not be one keystroke of TAB completion away from "interface".
# Identifies the product before anything else on the screen.
#
# Plain ASCII, 59 columns, six lines. A racked box is reached by serial console
# and that console is 80 columns of whatever font the BMC decided on; box
# drawing characters render as noise there, and a banner that wraps is worse
# than none. It also has to leave room for the version, the hint, and - on the
# installation medium - the line telling the operator to type "install", which
# is the one thing on that screen nobody can afford to miss.
BANNER = r"""  _   _                 ____              _   _
 | | | | _____  ____ _ |  _ \ ___  _   _| |_(_)_ __   __ _
 | |_| |/ _ \ \/ / _` || |_) / _ \| | | | __| | '_ \ / _` |
 |  _  |  __/>  < (_| ||  _ < (_) | |_| | |_| | | | | (_| |
 |_| |_|\___/_/\_\__,_||_| \_\___/ \__,_|\__|_|_| |_|\__, |
                                                     |___/ """

LIVE_SQUASHFS = os.environ.get(
    "HEXA_LIVE_SQUASHFS", "/run/live/medium/live/filesystem.squashfs")
INSTALLER = os.environ.get("HEXA_INSTALLER", "/usr/sbin/hexarouting-install")


def running_from_live_medium():
    return os.path.exists(LIVE_SQUASHFS) and os.access(INSTALLER, os.X_OK)


TREE = {
    "operational": {
        "configure": ("edit the candidate configuration", None),
        "show": ("display information", SHOW),
        "rollback": ("load a committed revision into the candidate", ARG_REV),
        "save": ("write the running configuration to disk", None),
        "exit": ("end the session", None),
    },
    "config": {
        "cgnat": ("CGNAT configuration", CGNAT_CONFIG),
        "no": ("remove configuration", {
            "cgnat": ("remove CGNAT configuration", {
                "pool": ("remove a pool", ARG_WORD),
                "interface": ("disable CGNAT on an interface", ARG_WORD)})}),
        "router": ("routing protocols (FRR)", {
            "bgp": ("BGP", "<AS>"),
            "ospf": ("OSPF", None),
            "isis": ("IS-IS", ARG_WORD),
        }),
        "interface": ("interface configuration", ARG_WORD),
        "commit": ("apply the candidate configuration", COMMIT),
        "rollback": ("load a committed revision into the candidate", ARG_REV),
        "show": ("display information", SHOW),
        "exit": ("leave configuration mode", None),
    },
    "pool": CGNAT_POOL,
}

if running_from_live_medium():
    TREE["operational"]["install"] = (
        "install HexaRouting onto a disk on this machine", None)


def tree_walk(mode, words):
    """Where we are in the tree after `words`; (options, argument hint)."""
    node = TREE.get(mode)
    hint = None
    for w in words:
        if not isinstance(node, dict):
            return None, hint
        entry = node.get(w)
        if entry is None:
            return None, None       # unknown word: FRR territory from here
        _, sub = entry
        if isinstance(sub, str):
            return None, sub
        node = sub
        hint = None
    return node, hint


class Vpp:
    """Talks to the bpa plugin through VPP's CLI."""

    #: set HEXA_TRACE=1 to see every command sent to the dataplane. A check
    #: that shows a mutating command here is a bug, not a nuance.
    TRACE = os.environ.get("HEXA_TRACE") == "1"

    def run(self, cmd):
        if self.TRACE:
            print(f"[device] {cmd}", file=sys.stderr)
        try:
            p = subprocess.run([VPPCTL] + shlex.split(cmd),
                               capture_output=True, text=True, timeout=15)
        except FileNotFoundError:
            return 1, f"{VPPCTL} not found"
        except subprocess.TimeoutExpired:
            return 1, "VPP did not respond"
        out = ((p.stdout or "") + (p.stderr or "")).rstrip()
        first = out.split("\n")[0] if out else ""
        return (1 if VPP_ERROR.match(first) else 0), out


class Frr:
    """Talks to FRR through vtysh, and reloads whole configurations."""

    def show(self, cmd):
        return self._exec(["-c", cmd])

    def running(self):
        rc, out = self.show("show running-config")
        if rc:
            return []
        skip = ("Building configuration", "Current configuration",
                "frr version", "frr defaults", "end")
        return [l.rstrip() for l in out.splitlines()
                if l.strip() and not l.startswith(skip)]

    def apply_lines(self, lines):
        args = ["-c", "configure terminal"]
        for l in lines:
            args += ["-c", l]
        return self._exec(args)

    def available(self):
        """Is FRR actually answering? A box may run CGNAT with no routing."""
        rc, _ = self._exec(["-c", "show version"])
        return rc == 0

    def reload(self, lines, check_only=False):
        """
        Converge FRR onto `lines` using frr-reload.py, which computes the
        delta. Without it a rollback could only add statements, never remove
        them - and a rollback that cannot remove is not a rollback.
        """
        if not os.path.exists(FRR_RELOAD):
            return 1, f"{FRR_RELOAD} not found: routing rollback unavailable"
        if not self.available():
            # A CGNAT-only box is a legitimate deployment. Refusing every
            # commit because no routing daemon is running would be wrong.
            return 1, "FRR is not responding"
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
            f.write("\n".join(lines) + "\n")
            path = f.name
        try:
            mode = "--test" if check_only else "--reload"
            p = subprocess.run(["python3", FRR_RELOAD, mode, "--stdout", path],
                               capture_output=True, text=True, timeout=60)
            out = ((p.stdout or "") + (p.stderr or "")).rstrip()
            if p.returncode:
                # frr-reload dumps a Python traceback on failure. An operator
                # needs the reason, not our tooling's stack.
                out = self._summarise(out)
            return p.returncode, out
        except subprocess.TimeoutExpired:
            return 1, "frr-reload timed out"
        finally:
            os.unlink(path)

    @staticmethod
    def _summarise(out):
        keep = [l for l in out.splitlines()
                if l.startswith(("Exiting:", "ERROR", "% ", "Failed"))
                or "Exception:" in l]
        return "\n".join(keep[-4:]) if keep else out.splitlines()[-1] if out else ""

    def _exec(self, args):
        try:
            p = subprocess.run([VTYSH] + args, capture_output=True,
                               text=True, timeout=30)
        except FileNotFoundError:
            return 1, f"{VTYSH} not found"
        except subprocess.TimeoutExpired:
            return 1, "vtysh did not respond"
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).rstrip()


class Rollback:
    """
    Committed revisions on disk.

    Numbering follows Juniper: 0 is the configuration now in effect, 1 the one
    before it, and so on. `rollback 0` therefore discards uncommitted edits
    rather than undoing the last commit - the same meaning an operator coming
    from JunOS already expects.
    """

    def __init__(self, path=ROLLBACK_DIR, keep=ROLLBACK_KEEP):
        self.path = path
        self.keep = keep

    def _file(self, n):
        return os.path.join(self.path, f"{n}.conf")

    def list(self):
        out = []
        for n in range(self.keep):
            f = self._file(n)
            if os.path.exists(f):
                with open(f) as fh:
                    first = fh.readline().strip()
                comment = first[2:] if first.startswith("# ") else ""
                out.append((n, time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(f))),
                    comment))
        return out

    def load(self, n):
        f = self._file(n)
        if not os.path.exists(f):
            return None
        with open(f) as fh:
            return "".join(l for l in fh if not l.startswith("# "))

    def push(self, text, comment=""):
        """Store a new revision 0, shifting the others down."""
        os.makedirs(self.path, exist_ok=True)
        oldest = self._file(self.keep - 1)
        if os.path.exists(oldest):
            os.unlink(oldest)
        for n in range(self.keep - 2, -1, -1):
            src = self._file(n)
            if os.path.exists(src):
                os.replace(src, self._file(n + 1))
        with open(self._file(0), "w") as fh:
            if comment:
                fh.write(f"# {comment}\n")
            fh.write(text)


class Shell:
    def __init__(self):
        self.dp = Vpp()
        self.api = vppapi.Vat2()
        self.frr = Frr()
        self.rb = Rollback()
        self.mode = "operational"      # operational | config | pool
        self.pool = None               # Pool being edited
        self.candidate = None          # Config being edited
        self.frr_ctx = []              # FRR block context inside config mode
        self.failed = 0
        self.confirm_deadline = None   # for "commit confirmed"
        self.confirm_backup = None

    # ---------------------------------------------------------- prompt

    def prompt(self):
        if self.mode == "operational":
            return "hexarouting> "
        if self.mode == "pool":
            return "hexarouting# [edit cgnat pool] "
        return "hexarouting# [edit] "

    def err(self, msg):
        print(f"error: {msg}")
        self.failed += 1

    # ---------------------------------------------------------- device

    def running_config(self) -> Config:
        """
        The configuration actually in effect, read from the device.

        A line the model cannot hold is announced rather than dropped, and the
        reason is not tidiness. The candidate is built FROM this object, so a
        setting lost here is a setting the next commit removes from the device:
        an operator who set something with vppctl and then committed an
        unrelated change through the VTY had it silently reverted. That is what
        issue #47 turned out to be.
        """
        cfg = Config()
        rc, out = self.dp.run("show bpa config")
        if rc == 0 and out.strip():
            cfg = Config.parse(out)
        if cfg.unknown:
            self.err("the dataplane reports configuration this VTY does not "
                     "model; committing would REMOVE it:")
            for lineno, text, why in cfg.unknown:
                print(f"    line {lineno}: {text}    ({why})")
        cfg.mtus = self.device_mtus()
        cfg.routing = self.frr.running()
        return cfg

    def device_mtus(self) -> dict:
        """
        MTU per interface, read from the dataplane.

        Every interface is reported, not only the ones somebody typed. "Explicitly
        configured" is not a property the dataplane can answer, and inventing the
        distinction would mean hexash remembering things between invocations -
        which it deliberately does not, because the device is the source of truth
        and the VTY is a short-lived process.

        The consequence is deliberate: a rollback can put an MTU back, because
        the previous value was recorded rather than inferred.
        """
        rc, out = self.dp.run("show interface")
        mtus = {}
        for line in (out or "").splitlines():
            w = line.split()
            # Name Idx State MTU(L3/IP4/IP6/MPLS) ... - only the header rows
            # start at column 0; counters are indented.
            if len(w) < 4 or line.startswith(" ") or w[0] == "Name":
                continue
            if w[0] == "local0" or "/" not in w[3]:
                continue
            l3 = w[3].split("/")[0]
            if l3.isdigit() and int(l3) > 0:
                mtus[w[0]] = int(l3)
        return mtus

    def vpp(self, cmd, quiet=False):
        rc, out = self.dp.run(cmd)
        if out and not quiet:
            print(out)
        if rc:
            self.failed += 1
        return rc

    # ---------------------------------------------------------- commit

    def api_call(self, message, args):
        """
        One place where the VTY talks to the dataplane, so the trace that found
        the `commit check` bug keeps working - now over API calls rather than
        CLI lines.
        """
        if Vpp.TRACE:
            print(f"[api] {message} {args}", file=sys.stderr)
        return self.api.call(message, args)

    def apply(self, target: Config, check_only=False) -> bool:
        """
        Converge the device onto `target`. Returns True on success.

        CGNAT is applied first and routing second, deliberately: a routing
        change can cost reachability, and doing it last keeps the window in
        which the box is half-configured as short as possible.
        """
        running = self.running_config()
        d = running.delta_to(target)

        # Asked once, before anything is touched. Discovering the dataplane is
        # unreachable halfway through leaves the box in a state that is neither
        # the old configuration nor the new one, and the operator with no way
        # to know which pools made it.
        if d["pool_add"] or d["pool_replace"] or d["pool_remove"]:
            state, msg = self.api.available()
            if state != vppapi.OK:
                self.err(f"cannot reach the dataplane: {msg}")
                return False

        if check_only:
            ok = True
            for pool in d["pool_add"] + d["pool_replace"]:
                if not pool.inside or not pool.outside:
                    self.err(f"pool '{pool.name}': inside and outside required")
                    ok = False
                    continue
                # The device validates for real: block size, prefix overlap,
                # capacity, and whether the public range collides with a local
                # address. It creates nothing, and it MUST NOT be made to
                # create anything - an earlier version deleted and recreated
                # the running pool to work around a name check, which turned a
                # read-only operation into an outage risk.
                #
                # Still over the CLI: validation has no API message of its own,
                # and inventing one that creates-then-deletes would rebuild
                # exactly that outage risk. What changed is that a check which
                # cannot run is no longer silently a pass.
                rc, out = self.dp.run(pool.device_command() + " check")
                if rc:
                    print(out)
                    self.err(f"pool '{pool.name}' would be refused")
                    ok = False
            if target.routing or running.routing:
                rc, out = self.frr.reload(target.routing, check_only=True)
                if rc:
                    print(f"routing validation failed: {out}")
                    ok = False
            return ok

        for name in d["pool_remove"]:
            p = Pool(name)
            r = self.api_call("bpa_pool_add_del", p.api_args(is_add=False))
            if not r.ok and r.state != vppapi.REFUSED:
                self.err(f"removing pool '{name}': {r.reason}")
                return False
        for pool in d["pool_replace"]:
            self.api_call("bpa_pool_add_del", pool.api_args(is_add=False))
        for pool in d["pool_add"] + d["pool_replace"]:
            r = self.api_call("bpa_pool_add_del", pool.api_args())
            if not r.ok:
                # Named, because a commit carrying several pools has to say
                # which one the dataplane refused. "commit failed" sends the
                # operator to read the whole candidate.
                self.err(f"pool '{pool.name}': {r.reason}")
                return False

        for ifname in d["if_remove"]:
            direction = running.interfaces[ifname]
            k = "in2out" if direction == "inside" else "out2in"
            self.dp.run(f"set interface bpa {k} {ifname} del")
        for ifname, direction in d["if_add"].items():
            k = "in2out" if direction == "inside" else "out2in"
            rc, out = self.dp.run(f"set interface bpa {k} {ifname}")
            if rc:
                print(out)
                self.err(f"interface {ifname} refused by the dataplane")
                return False

        # MTU on BOTH sides. docs/routing.md records the failure: the TAP
        # created by linux_cp comes up at 9000, OSPF compares MTU in Database
        # Description packets, and the adjacency stops at Exchange with nothing
        # in the log saying why. Setting only the TAP leaves the packet path -
        # the VPP interface - unchanged, which is the half that matters.
        for ifname, mtu in d["mtu_set"].items():
            # "mtu packet" sets the L3 MTU, which is what OSPF compares in
            # Database Description packets. Plain "set interface mtu" changes
            # the hardware max frame size, which most drivers refuse - and
            # refuse while vppctl still exits 0, so the failure was invisible.
            self.dp.run(f"set interface mtu packet {mtu} {ifname}")
            # Read back rather than trust the exit status. vppctl returns 0
            # even when the command printed "Unsupported", which is the whole
            # reason the pool path moved to the binary API. Until MTU follows
            # it, verifying the effect is the only honest check.
            if self.device_mtus().get(ifname) != mtu:
                self.err(f"interface {ifname}: the dataplane did not take "
                         f"mtu {mtu}")
                return False
            self.set_host_mtu(ifname, mtu)
        for ifname in d["mtu_del"]:
            # No stored default to restore: the driver's own value is whatever
            # the interface reports before we touch it, so removal is reported
            # rather than guessed at.
            print(f"note: mtu removed from {ifname}; the driver default "
                  "applies after the interface is recreated")

        for g in d["globals"]:
            self.apply_global(g)

        if d["routing_changed"]:
            rc, out = self.frr.reload(target.routing)
            if rc:
                print(out)
                self.err("routing reload failed")
                return False
        return True

    def set_host_mtu(self, ifname, mtu):
        """
        The Linux side of a linux_cp pair. Best effort: a box with no TAP for
        this interface is a legitimate configuration, and failing the commit
        over it would refuse a valid change.
        """
        rc, out = self.dp.run(f"show lcp")
        host = ""
        for line in (out or "").splitlines():
            if ifname in line:
                parts = line.split()
                for i, tok in enumerate(parts):
                    if tok == "host" and i + 1 < len(parts):
                        host = parts[i + 1]
        if not host:
            return
        subprocess.run(["ip", "link", "set", "dev", host, "mtu", str(mtu)],
                       capture_output=True)

    def apply_global(self, line):
        w = line.split()
        if len(w) < 2:
            return
        if w[1] == "timeout" and len(w) >= 4:
            self.dp.run(f"bpa timeouts {w[2]} {w[3]}")
        elif w[1] == "block-holddown" and len(w) >= 3:
            self.dp.run(f"bpa block-holddown {w[2]}")
        elif w[1] == "logging" and len(w) >= 3:
            if w[2] == "tcp":
                self.dp.run("bpa logging tcp " + " ".join(w[3:]))
            else:
                self.dp.run(f"bpa logging {w[2]}")
        elif w[1] == "session-table":
            self.dp.run("bpa session-table " + " ".join(w[2:]))

    def do_commit(self, args):
        """commit [check | confirmed <min> | comment <text>]"""
        if self.candidate is None:
            self.err("no candidate configuration")
            return
        running = self.running_config()

        # Refused, not warned about. A candidate carrying a line nobody can
        # apply would be committed as though it had been, and the operator
        # would find out from the consequence.
        if self.candidate.unknown or running.unknown:
            self.err("refusing to commit: there are configuration lines this "
                     "VTY does not understand")
            for lineno, text, why in (self.candidate.unknown
                                      + running.unknown):
                print(f"    line {lineno}: {text}    ({why})")
            return

        if args and args[0] == "check":
            if self.apply(self.candidate, check_only=True):
                print("configuration check succeeds")
            return

        if running.is_same(self.candidate):
            print("no changes to commit")
            return

        confirm_min = 0
        comment = ""
        if args and args[0] == "confirmed":
            try:
                confirm_min = int(args[1]) if len(args) > 1 else 10
            except (IndexError, ValueError):
                self.err("usage: commit confirmed <minutes>")
                return
        elif args and args[0] == "comment":
            comment = " ".join(args[1:]).strip('"')

        if not self.apply(self.candidate, check_only=True):
            self.err("candidate did not pass validation; nothing applied")
            return

        backup = running.text()
        if not self.apply(self.candidate):
            self.err("commit failed; rolling back to the previous state")
            self.apply(Config.parse(backup))
            return

        self.rb.push(self.candidate.text(), comment)
        print("commit complete")

        if confirm_min:
            # The point of "commit confirmed" is surviving a change that cuts
            # your own access: if the operator cannot type "commit" again, the
            # box returns on its own.
            self.confirm_deadline = time.time() + confirm_min * 60
            self.confirm_backup = backup
            print(f"rollback in {confirm_min} minute(s) unless you enter "
                  f"'commit' to confirm")

    def check_confirm_timeout(self):
        if self.confirm_deadline and time.time() > self.confirm_deadline:
            print("\ncommit was not confirmed: rolling back")
            self.apply(Config.parse(self.confirm_backup))
            self.candidate = self.running_config()
            self.confirm_deadline = None
            self.confirm_backup = None

    def do_rollback(self, args):
        try:
            n = int(args[0]) if args else 0
        except ValueError:
            self.err("usage: rollback <0-%d>" % (ROLLBACK_KEEP - 1))
            return
        if n == 0:
            self.candidate = self.running_config()
            print("candidate reset to the running configuration")
            return
        text = self.rb.load(n)
        if text is None:
            self.err(f"revision {n} does not exist")
            return
        self.candidate = Config.parse(text)
        print(f"load complete (revision {n})")
        print("use 'show configuration compare' to review, 'commit' to apply")

    # ---------------------------------------------------------- modes

    def start_shell(self):
        """
        Hidden escape to a real shell.

        Deliberately absent from TREE, so neither completion nor `?` offers it.
        Hidden, not restricted: anyone who can log in can run it, and the
        authorization boundary is roles in the VTY, not obscurity. It exists
        because recovery, tcpdump and reading a log the VTY does not expose all
        need a shell, and an appliance that makes those impossible gets one
        opened permanently by the first operator who needs one.

        Spawned as a child rather than exec'd: leaving the shell has to come
        back to the VTY, not log the operator out.
        """
        shell = os.environ.get("HEXA_SHELL") or "/bin/bash"
        if not os.path.exists(shell):
            shell = "/bin/sh"
        print(f"entering {shell}; type 'exit' to return to the VTY")
        try:
            subprocess.call([shell, "-i"])
        except OSError as e:
            self.err(f"cannot start {shell}: {e}")
            return
        print("back in the HexaRouting VTY")

    def do_install(self):
        """
        Hand the console to the installer.

        Only from the live medium. On an installed box the command is not in
        the tree at all, and this refuses anyway: the two checks answer
        different questions - whether the operator can find it, and whether it
        can run - and a box that only had the first would be one stale TREE
        away from offering to overwrite itself.

        Spawned as a child, like the shell escape, so that an installer the
        operator abandons returns to the VTY rather than logging them out.
        """
        if not running_from_live_medium():
            self.err("this box is not running from the installation medium; "
                     "'install' only exists there")
            return
        try:
            subprocess.call([INSTALLER])
        except OSError as e:
            self.err(f"cannot start {INSTALLER}: {e}")

    def do_operational(self, line):
        w = line.split()
        # Checked before the command tree, so it works without appearing in it.
        if w[:2] == ["start", "shell"]:
            self.start_shell()
            return True
        if w[0] == "install":
            self.do_install()
            return True
        if w[0] in ("configure", "conf"):
            self.candidate = self.running_config()
            self.mode = "config"
            print("entering configuration mode")
        elif w[0] in ("exit", "quit", "logout"):
            return False
        elif w[0] == "rollback":
            self.candidate = self.running_config()
            self.do_rollback(w[1:])
            if self.candidate is not None:
                self.mode = "config"
        elif w[0] == "save":
            self.save_running()
        elif w[0] == "commit":
            self.err("enter configuration mode first")
        else:
            self.show_or_pass(line)
        return True

    def do_config(self, line):
        w = line.split()

        if w[:2] == ["start", "shell"]:
            self.start_shell()
            return True
        if w[0] == "commit":
            self.do_commit(w[1:])
            return True
        if w[0] == "rollback":
            self.do_rollback(w[1:])
            return True
        if w[0] == "show":
            self.show_or_pass(line)
            return True
        if w[0] in ("exit", "quit", "end"):
            if self.frr_ctx:
                self.frr_ctx.pop()
                return True
            running = self.running_config()
            if self.candidate is not None and not running.is_same(self.candidate):
                print("warning: uncommitted changes were discarded "
                      "(use 'commit' to apply them)")
            self.candidate = None
            self.mode = "operational"
            return True

        if line.startswith("cgnat pool "):
            name = w[2]
            self.pool = self.candidate.pools.get(name) or Pool(name)
            self.candidate.pools[name] = self.pool
            self.mode = "pool"
            return True
        if line.startswith("no cgnat pool "):
            self.candidate.pools.pop(w[3], None)
            return True
        if line.startswith("cgnat interface "):
            if len(w) < 4 or w[3] not in ("inside", "outside"):
                self.err("usage: cgnat interface <interface> <inside|outside>")
            else:
                self.candidate.interfaces[w[2]] = w[3]
            return True
        if line.startswith("no cgnat interface "):
            self.candidate.interfaces.pop(w[3], None)
            return True
        if line.startswith("cgnat "):
            key = " ".join(w[:3])
            self.candidate.globals = [g for g in self.candidate.globals
                                      if not g.startswith(key)]
            self.candidate.globals.append(line)
            return True

        # `mtu` inside an interface block belongs to the device, not to FRR.
        # FRR would accept it and set it on the TAP alone, which is the half
        # that does not matter: the packet path is the VPP interface, and an
        # MTU set on one side only reproduces the mismatch this exists to
        # prevent. Everything else in the block is still FRR's.
        if (self.frr_ctx and self.frr_ctx[-1].startswith("interface ")
                and w[0] == "mtu"):
            ifname = self.frr_ctx[-1].split()[1]
            if len(w) < 2 or not w[1].isdigit():
                self.err("usage: mtu <576-9216>")
            elif not 576 <= int(w[1]) <= 9216:
                self.err(f"mtu {w[1]} out of range (576-9216)")
            else:
                self.candidate.mtus[ifname] = int(w[1])
            return True
        if (self.frr_ctx and self.frr_ctx[-1].startswith("interface ")
                and w[:2] == ["no", "mtu"]):
            self.candidate.mtus.pop(self.frr_ctx[-1].split()[1], None)
            return True

        # anything else is routing: staged in the candidate, applied on commit
        self.candidate.routing.append(line)
        if line.startswith(("router ", "interface ", "vrf ", "route-map ")):
            self.frr_ctx.append(line)
        return True

    def do_pool(self, line):
        w = line.split()
        p = self.pool
        if w[0] in ("exit", "end"):
            self.mode = "config"
            self.pool = None
            return True
        try:
            if w[0] == "inside":
                p.inside.append(w[1])
            elif w[0] == "outside":
                p.outside.append(w[1])
            elif w[0] == "block-size":
                p.block_size = str(int(w[1]))
            elif w[0] == "ports":
                p.ports = f"{int(w[1])} {int(w[2])}"
            elif w[0] == "policy":
                if w[1] not in ("interleaved", "blocked"):
                    raise ValueError("policy must be interleaved or blocked")
                p.policy = w[1]
            elif w[0] == "mode":
                if w[1] not in ("deterministic", "dynamic"):
                    raise ValueError("mode must be deterministic or dynamic")
                p.mode = w[1]
            elif w[0] == "timeout":
                if w[1] not in Pool.TIMEOUTS:
                    raise ValueError("timeout must be one of "
                                     + ", ".join(Pool.TIMEOUTS))
                p.timeouts[w[1]] = int(w[2])
            elif w[0] == "max-blocks":
                if int(w[1]) < 1:
                    raise ValueError("max-blocks must be at least 1")
                p.max_blocks = int(w[1])
            elif w[0] == "filter":
                if w[1] not in Pool.FILTERS:
                    raise ValueError("filter must be one of "
                                     + ", ".join(Pool.FILTERS))
                p.filter = w[1]
            elif w[0] == "alg":
                if w[1] not in Pool.ALG_BITS:
                    raise ValueError("alg must be one of "
                                     + ", ".join(Pool.ALG_BITS))
                if w[2] not in ("on", "off", "enable", "disable"):
                    raise ValueError("alg must be on or off")
                p.algs[w[1]] = w[2] in ("on", "enable")
            elif w[0] == "no" and len(w) > 2:
                if w[1] == "inside" and w[2] in p.inside:
                    p.inside.remove(w[2])
                elif w[1] == "outside" and w[2] in p.outside:
                    p.outside.remove(w[2])
                # "no timeout udp" and "no alg ftp" drop the OVERRIDE, which
                # is not the same as setting it to something: the pool goes
                # back to inheriting, and the line disappears from the
                # configuration instead of appearing with a default in it.
                elif w[1] == "timeout":
                    p.timeouts.pop(w[2], None)
                elif w[1] == "alg":
                    p.algs.pop(w[2], None)
            elif w[0] == "no" and len(w) > 1 and w[1] == "max-blocks":
                p.max_blocks = 0
            elif w[0] == "no" and len(w) > 1 and w[1] == "filter":
                p.filter = "endpoint-independent"
            else:
                self.err(f"unknown statement in pool mode: {w[0]}")
        except (IndexError, ValueError) as e:
            self.err(f"invalid argument: {e}")
        return True

    # ---------------------------------------------------------- show

    def show_or_pass(self, line):
        m = {
            "show cgnat pool": "show bpa pool",
            "show cgnat session": "show bpa session",
            "show cgnat blocks": "show bpa blocks",
            "show cgnat logging": "show bpa logging",
            "show cgnat history": "show bpa pool history",
        }
        if line in m:
            self.vpp(m[line])
            return
        if line in ("show configuration compare", "show configuration | compare"):
            self.show_compare()
            return
        if line == "show configuration":
            print((self.candidate or self.running_config()).text(), end="")
            return
        if line == "show rollback":
            self.show_rollback()
            return
        if line in ("show running-config", "show run"):
            print(self.running_config().text(), end="")
            return
        if line == "show version":
            self.show_version()
            return
        rc, out = self.frr.show(line)
        if out:
            print(out)

    def show_version(self):
        """
        The three versions that matter are in three different places, and an
        operator on the phone should not have to run three commands to read
        them - least of all the two that require knowing vppctl and vtysh
        exist at all.
        """
        print(f"HexaRouting {hexarouting_version()}")
        rc, out = self.dp.run("show version")
        print(f"  dataplane: {out.strip().splitlines()[0] if rc == 0 and out.strip() else 'not responding'}")
        rc, out = self.frr.show("show version")
        line = out.strip().splitlines()[0] if out.strip() else ""
        print(f"  routing:   {line or 'not responding'}")

    def show_compare(self):
        if self.candidate is None:
            print("no candidate configuration")
            return
        d = self.running_config().compare(self.candidate)
        print(d if d else "no differences")

    def show_rollback(self):
        revs = self.rb.list()
        if not revs:
            print("no committed revisions")
            return
        for n, when, comment in revs:
            label = "current" if n == 0 else f"{n} commit(s) ago"
            print(f"  {n:>2}  {when}  {label}"
                  + (f"  {comment}" if comment else ""))

    def save_running(self):
        d = os.path.dirname(CONFIG_PATH)
        try:
            if d:
                os.makedirs(d, exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                f.write(self.running_config().text())
        except OSError as e:
            self.err(f"cannot write {CONFIG_PATH}: {e}")
            return
        print(f"configuration saved to {CONFIG_PATH}")

    # ---------------------------------------------------------- completion

    def _options(self, line, ending_word):
        words = line.split()
        prefix = ""
        if ending_word and words:
            prefix = words[-1]
            words = words[:-1]
        node, hint = tree_walk(self.mode, words)
        # Inside an interface block the tree has nothing more to say: the block
        # was entered through an argument. What the device itself handles there
        # is offered here, and the rest still goes to FRR.
        if (not words and self.frr_ctx
                and self.frr_ctx[-1].startswith("interface ")):
            merged = {k: v[0] for k, v in INTERFACE_BLOCK.items()
                      if k.startswith(prefix)}
            if node:
                merged.update({k: v[0] for k, v in node.items()
                               if k.startswith(prefix)})
            return merged, hint, prefix
        if node is None:
            return {}, hint, prefix
        opts = {k: v[0] for k, v in node.items() if k.startswith(prefix)}
        return opts, hint, prefix

    def complete(self, text, state):
        if state == 0:
            line = readline.get_line_buffer()
            ending = bool(line) and not line.endswith(" ")
            opts, hint, _ = self._options(line, ending)
            self._last_opts = opts
            self._last_hint = hint
            self._matches = sorted(opts)
            if not opts and hint:
                # With no options readline never calls the display hook, so the
                # argument hint would never appear. Two entries with no common
                # prefix force the display without inserting anything.
                self._matches = [hint, "​"]
        try:
            m = self._matches[state]
        except IndexError:
            return None
        if not self._last_opts:
            return m            # a hint is not a completion: never inserted
        return m + " "

    def display_matches(self, substitution, matches, longest):
        opts = getattr(self, "_last_opts", {})
        print()
        if opts:
            width = max(len(m) for m in opts)
            for m in sorted(opts):
                print(f"  {m:<{width}}  {opts[m]}")
        hint = getattr(self, "_last_hint", None)
        if hint:
            print(f"  {hint}")
        if not opts and not hint:
            print("  (no known options here; the command is passed to FRR)")
        print()
        sys.stdout.write(self.prompt() + readline.get_line_buffer())
        sys.stdout.flush()

    # ---------------------------------------------------------- loop

    def feed(self, line):
        line = line.split("!")[0].strip()
        line = re.sub(r"\s+", " ", line)
        if not line:
            return True
        self.check_confirm_timeout()
        handler = {"operational": self.do_operational,
                   "config": self.do_config,
                   "pool": self.do_pool}[self.mode]
        return handler(line)

    def load(self, path):
        """
        Apply a saved configuration file to the device.

        This runs at boot, unattended, which is exactly why it refuses rather
        than trimming: a file with one line this VTY cannot hold would
        otherwise be applied minus that line, on a box nobody is watching, and
        the difference between the file on disk and the running configuration
        would be discovered later by somebody reading both.
        """
        if not os.path.exists(path):
            return
        with open(path) as f:
            target = Config.parse(f.read())
        if target.unknown:
            self.err(f"{path}: refusing to load, these lines are not "
                     "understood:")
            for lineno, text, why in target.unknown:
                print(f"    line {lineno}: {text}    ({why})")
            return
        if self.apply(target):
            self.rb.push(target.text(), "loaded from file")

    def repl(self):
        global readline
        try:
            import readline
        except ImportError:
            readline = None
        if readline:
            readline.set_completer(self.complete)
            readline.set_completer_delims(" \t\n")
            readline.parse_and_bind("tab: complete")
            readline.parse_and_bind('"?": possible-completions')
            try:
                readline.set_completion_display_matches_hook(
                    self.display_matches)
            except AttributeError:
                pass
        # Shown only on a terminal: piping commands in must not have a banner
        # prepended to the output, or every script that parses it breaks.
        # Only on a terminal. "hexash -c" is used by scripts and by every
        # lab, and a banner prepended to parsed output breaks them - the
        # version line already had to learn this, which is why the guard was
        # here before the banner was.
        if sys.stdin.isatty():
            print(BANNER)
            print(f"HexaRouting VTY {hexarouting_version()}")
            print("'?' for help, TAB to complete, 'configure' to edit, "
                  "'exit' to leave.")
            # An installer nobody can find is not an installer. This medium
            # boots with no credentials and nothing on it survives a reboot,
            # so the one thing an operator needs to be told is the word that
            # changes that.
            if running_from_live_medium():
                print()
                print("This is the installation medium: nothing here survives "
                      "a reboot.")
                print("Type 'install' to install HexaRouting onto a disk.")
            print()
        while True:
            try:
                line = input(self.prompt())
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not self.feed(line):
                return 0


def main():
    sh = Shell()
    args = sys.argv[1:]

    if args and args[0] == "--load":
        sh.load(args[1] if len(args) > 1 else CONFIG_PATH)
        return 1 if sh.failed else 0

    cmds = []
    while args:
        if args[0] == "-c":
            cmds.append(args[1]); args = args[2:]
        else:
            args = args[1:]

    if cmds:
        for c in cmds:
            sh.feed(c)
        return 1 if sh.failed else 0

    return sh.repl()


if __name__ == "__main__":
    sys.exit(main())
