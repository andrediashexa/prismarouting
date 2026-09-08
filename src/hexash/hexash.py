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

from config import Config, Pool, URPF_MODES
from system import (System, SnmpDaemon, SnmpUser, User,
                    can_anyone_log_in, redact, snmp_redact,
                    validate_snmp, validate_ssh, SSH_FIELDS)
import routes
import vppapi

readline = None

CONFIG_PATH = os.environ.get("HEXA_CONFIG", "/etc/hexarouting/hexarouting.conf")
ROLLBACK_DIR = os.environ.get("HEXA_ROLLBACK", "/etc/hexarouting/rollback")
ROLLBACK_KEEP = 20
VPPCTL = os.environ.get("VPPCTL", "vppctl")
VTYSH = os.environ.get("VTYSH", "vtysh")
FRR_RELOAD = os.environ.get("FRR_RELOAD", "/usr/lib/frr/frr-reload.py")

# Which FRR daemon serves each "router" statement. Written as data rather than
# guessed from the keyword, because the two only look alike: "router ospf" is
# served by ospfd, and "router isis" by isisd, but the mapping is a fact about
# FRR and not about the word.
ROUTER_DAEMON = {"bgp": "bgpd", "ospf": "ospfd", "ospf6": "ospf6d",
                 "isis": "isisd", "rip": "ripd", "eigrp": "eigrpd",
                 "babel": "babeld", "pim": "pimd"}

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

# Places where the grammar knows a WORD goes here and the BOX knows which
# words. "<word>" is true and useless: it tells the operator what they already
# knew, and sends them to vppctl or "ip link" in another window to find out the
# rest - the two tools this VTY exists to keep them out of, at the first
# argument (#98).
#
# Each of these is offered by a method on Shell, named in DYNAMIC below. A
# source that cannot answer - the dataplane down, no candidate yet - offers
# nothing and says so, rather than hanging or raising: a VTY that cannot be
# used while the dataplane is down is a VTY that cannot be used to fix it.
ARG_INTERFACE = "<interface>"
ARG_POOL = "<pool>"
ARG_USER = "<user>"
ARG_SNMP_USER = "<snmp-user>"
ARG_REV = "<0-19>"

SHOW_CGNAT = {
    # The four that answer an operator's question rather than reporting a
    # counter. Every one of them takes its answer from the dataplane, so they
    # work from here as well as from vppctl - an operator who has to drop to
    # the dataplane CLI to troubleshoot is one for whom this VTY is decoration.
    "subscriber": ("everything about one subscriber", "<inside-address>"),
    "translation": ("who was behind a public address and port",
                    "<public-address> <port>"),
    "top": ("subscribers closest to exhausting their blocks", "[<n>]"),
    "drops": ("why packets did not get through", None),
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
    "this": ("the configuration of the hierarchy you are in", None),
    "route": ("the forwarding table (what actually moves packets)", {
        "verbose": ("compare it against FRR's table and mark disagreements",
                    None)}),
    "ip": ("IP information (FRR)", None),
    "bgp": ("BGP information (FRR)", None),
    "isis": ("IS-IS information (FRR)", None),
    "interface": ("interfaces on the dataplane, with state and addresses",
                  ARG_INTERFACE),
    "version": ("HexaRouting, VPP and FRR versions", None),
    "logging": ("system log (FRR)", None),
}

# Offered inside an `interface <name>` block. It is not part of the mode tree
# because the block is entered by an argument, not by a keyword the tree can
# walk into - and an operator who cannot discover `mtu` with `?` will inherit
# the TAP's 9000 and spend an afternoon on an OSPF adjacency stuck in Exchange.
INTERFACE_BLOCK = {
    "mtu": ("MTU, on both the dataplane interface and its TAP", "<576-9216>"),
    "urpf": ("drop packets whose source does not route back this way", {
        "strict": ("the source MUST route back out this interface", None),
        "loose": ("the source MUST have a route somewhere", None),
        "off": ("no check (the default)", None),
    }),
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
    "bypass": ("prefixes that leave untranslated", {
        "source": ("a source that must keep its own address", ARG_PREFIX),
        "destination": ("a destination that must see the real source",
                        ARG_PREFIX),
    }),
    "utilisation-alarm": ("log once when a subscriber's block reaches this "
                          "percentage, ahead of exhaustion", "<1-100>"),
    "max-sessions": ("most sessions one subscriber may hold at once; 0 is "
                     "unlimited", ARG_NUM),
    "max-session-rate": ("most new sessions per second, per subscriber; 0 is "
                         "unlimited", ARG_NUM),
    "tcp-mss": ("lower the MSS of handshakes so segments fit the path", {
        "auto": ("derive it from the CGNAT interfaces' MTUs", None),
    }),
    "forward": ("pin an inbound port to one subscriber; the public address is "
                "the subscriber's own", {
        "tcp": ("public port, then 'to <private-ip> <private-port>'", ARG_NUM),
        "udp": ("public port, then 'to <private-ip> <private-port>'", ARG_NUM),
    }),
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
    "vrf": ("the VRF whose subscribers this pool serves; absent means every "
            "VRF", ARG_NUM),
    "outside-vrf": ("the VRF translated traffic leaves by; absent leaves it "
                    "where it was", ARG_NUM),
    "mode": ("how the block is assigned", {
        "deterministic": ("by formula, no allocation logging", None),
        "dynamic": ("on demand, with allocation logging", None),
    }),
    "exit": ("leave the pool", None),
}

CGNAT_CONFIG = {
    "pool": ("create or edit a pool", ARG_POOL),
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
    "check": ("validate the candidate without applying it, and report which "
              "subscribers a change would re-map", None),
    "remap": ("apply a change that moves existing subscribers to new public "
              "addresses and port blocks", None),
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


# ---------------------------------------------------------------- roles
#
# Two capabilities, mapped onto system groups rather than a database of this
# appliance's own. An appliance that invents its own accounts is an appliance
# whose accounts nobody deprovisions when somebody leaves.
#
#   hexa-admin   everything: routing, the box's own configuration, and CGNAT
#   hexa-cgnat   CGNAT configuration only
#   neither      read-only
#
# root has both, because root can talk to the dataplane socket directly and
# pretending otherwise would be theatre.
CAP_CGNAT = "cgnat"
CAP_ALL = "configure"

GROUP_ADMIN = "hexa-admin"
GROUP_CGNAT = "hexa-cgnat"


def capabilities():
    """
    What the invoking account is allowed to change.

    Read from the system's group membership at the moment the VTY starts, and
    never from anything this program stores: the answer has to be the one the
    system would give, or deprovisioning an account here does not deprovision
    it.
    """
    if os.geteuid() == 0:
        return {CAP_ALL, CAP_CGNAT}

    names = set()
    try:
        import grp
        import pwd

        user = pwd.getpwuid(os.geteuid()).pw_name
        names = {g.gr_name for g in grp.getgrall()
                 if user in g.gr_mem}
        names.add(grp.getgrgid(pwd.getpwnam(user).pw_gid).gr_name)
    except Exception:
        # An account the system cannot describe gets nothing. Failing open
        # here would mean a broken NSS turns into full configuration rights.
        return set()

    caps = set()
    if GROUP_ADMIN in names:
        caps |= {CAP_ALL, CAP_CGNAT}
    if GROUP_CGNAT in names:
        caps.add(CAP_CGNAT)
    return caps


# Which half of the configuration each part of a delta belongs to. Written as
# data rather than a chain of ifs, so a delta key added later and forgotten
# here is visible as an absence instead of silently needing nothing.
DELTA_CGNAT = ("pool_add", "pool_remove", "pool_replace", "settings_set",
               "limits_set", "bypass_set", "fwd_add", "fwd_del", "if_add",
               "if_remove", "globals")
DELTA_ADMIN = ("routing_changed", "ssh_changed", "users_changed",
               "snmp_changed", "mtu_set", "mtu_del", "urpf_set", "urpf_del")


def cap_needed(delta):
    """
    The capability a change requires, or None when it changes nothing.

    Anything outside CGNAT needs the full capability, because the rest of the
    configuration is routing, the accounts that may log in, and the SSH and
    SNMP daemons - none of which a CGNAT operator was given.
    """
    if any(delta.get(k) for k in DELTA_ADMIN):
        return CAP_ALL
    if any(delta.get(k) for k in DELTA_CGNAT):
        return CAP_CGNAT
    return None


def role_name(caps):
    if CAP_ALL in caps:
        return "administrator"
    if CAP_CGNAT in caps:
        return "cgnat operator"
    return "read-only"


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
                "pool": ("remove a pool", ARG_POOL),
                "interface": ("disable CGNAT on an interface", ARG_INTERFACE)})}),
        "ip": ("IP configuration", {
            "route": ("a static route", "<prefix> <next-hop|interface>")}),
        "router": ("routing protocols (FRR)", {
            "bgp": ("BGP", "<AS>"),
            "ospf": ("OSPF", None),
            "isis": ("IS-IS", ARG_WORD),
        }),
        "interface": ("interface configuration", ARG_INTERFACE),
        "ssh": ("the SSH daemon", {
            "port": ("TCP port", ARG_NUM),
            "listen-address": ("address to listen on", ARG_WORD),
            "permit-root-login": ("whether root may log in over SSH", {
                "yes": ("password or key", None),
                "no": ("not at all", None),
                "prohibit-password": ("key only", None),
                "forced-commands-only": ("only forced commands", None)}),
            "password-authentication": ("whether a password may be used", {
                "yes": ("allowed", None), "no": ("keys only", None)}),
        }),
        "user": ("an account that may log in", ARG_USER),
        "snmp": ("the SNMP agent, read-only by construction", {
            "enabled": ("run the agent", {"yes": ("", None), "no": ("", None)}),
            "listen-address": ("address to listen on", ARG_WORD),
            "port": ("UDP port", ARG_NUM),
            "enterprise-number": ("your organisation's IANA Private "
                                  "Enterprise Number", ARG_NUM),
            "community": ("an SNMPv2c community, read-only", ARG_WORD),
        }),
        "snmp-user": ("an SNMPv3 user", ARG_SNMP_USER),
        "commit": ("apply the candidate configuration", COMMIT),
        "rollback": ("load a committed revision into the candidate", ARG_REV),
        "show": ("display information", SHOW),
        "exit": ("leave configuration mode", None),
    },
    "pool": CGNAT_POOL,
    "snmp-user": {
        "auth": ("authentication protocol and passphrase", "<sha|md5> <pass>"),
        "priv": ("privacy protocol and passphrase", "<aes|des> <pass>"),
        "exit": ("leave the user", None),
    },
    "user": {
        "ssh-key": ("an authorized public key, as it appears in "
                    "authorized_keys", "<type> <key> [comment]"),
        "password": ("a crypt hash, never a plaintext password "
                     "(openssl passwd -6)", "<hash>"),
        "no": ("remove", {"ssh-key": ("remove a key", "<key>"),
                          "password": ("remove the password", None)}),
        "exit": ("leave the user", None),
    },
}

if running_from_live_medium():
    TREE["operational"]["install"] = (
        "install HexaRouting onto a disk on this machine", None)


def _starts(candidate, prefix):
    """
    Does `candidate` begin with `prefix`, ignoring case?

    Interface names are GigabitEthernet0/12/0 and TenGigabitEthernet0/8/0.
    Requiring the capital G before completion will help is completion that only
    works for somebody who already knows the answer (#112).

    The candidate is returned unchanged by the caller, so what gets inserted is
    always the box's own spelling - completing "gig" to "gig" would be worse
    than not completing.
    """
    return candidate.lower().startswith(prefix.lower())


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


class RollbackUnavailable(Exception):
    """
    The revision store could not be written.

    Its own exception because the caller has to say something specific: the
    configuration IS applied and only the ability to undo it is missing, which
    is a different sentence from "the commit failed".
    """


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
        """
        Store a new revision 0, shifting the others down.

        Root-only, for the same reason as the saved configuration: a rollback
        revision is a complete configuration, and since accounts joined it that
        includes password hashes.
        """
        # A store this account cannot write is reported, not raised. By the
        # time a revision is recorded the change is already on the dataplane,
        # so a traceback here would leave the operator looking at a Python
        # stack with no idea whether their commit took effect - and it did.
        try:
            os.makedirs(self.path, exist_ok=True)
            os.chmod(self.path, 0o700)
        except OSError as e:
            raise RollbackUnavailable(f"{self.path}: {e.strerror}") from None
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
        try:
            os.chmod(self._file(0), 0o600)
        except OSError:
            pass


class Shell:
    def __init__(self):
        self.sys = System()
        self.snmp = SnmpDaemon()
        self.dp = Vpp()
        self.api = vppapi.Vat2()
        self.frr = Frr()
        self.rb = Rollback()
        self.caps = capabilities()
        # Lines the dataplane returned about subscribers changing mapping,
        # refilled by every check. A commit that would move them is refused
        # until the operator says so in as many words.
        self.remap = []
        self.mode = "operational"      # operational | config | pool | user
        self.pool = None               # pool being edited
        self.user = None               # user being edited
        self.snmp_user = None          # SNMP v3 user being edited
        self.candidate = None          # Config being edited
        self.frr_ctx = []              # FRR block context inside config mode
        self.failed = 0
        self.confirm_deadline = None   # for "commit confirmed"
        self.confirm_backup = None

    # ---------------------------------------------------------- prompt

    def where(self):
        """
        The hierarchy being edited, as words - "cgnat pool residential".

        It names the SUBJECT, not only the kind. "[edit cgnat pool]" is the
        same prompt on a box with twenty pools: it tells the operator they are
        in a pool and not which one, which is the question they have (#108).

        Blocks entered by an argument - an interface, an FRR block - are in
        here too. They moved nothing before, so nothing on the screen said the
        next statement would land on GigabitEthernet0/12/0, and an `mtu 1500`
        typed one block later than intended went to the wrong interface with
        no warning.
        """
        if self.mode == "pool" and self.pool is not None:
            return f"cgnat pool {self.pool.name}"
        if self.mode == "user" and self.user is not None:
            return f"user {self.user.name}"
        if self.mode == "snmp-user" and self.snmp_user is not None:
            return f"snmp user {self.snmp_user.name}"
        if self.frr_ctx:
            # Nested blocks show both, innermost last, so the reader can see
            # what contains what.
            return " / ".join(self.frr_ctx)
        return ""

    #: An 80-column serial console is what a racked box is seen through, and a
    #: prompt that pushes the cursor off the line is worse than a vague one.
    PROMPT_MAX = 44

    def prompt(self):
        if self.mode == "operational":
            return "hexarouting> "
        where = self.where()
        if not where:
            return "hexarouting# [edit] "
        if len(where) > self.PROMPT_MAX:
            # Keep the END: the innermost block is where the next statement
            # lands, and it is the half worth the space.
            where = "..." + where[-(self.PROMPT_MAX - 3):]
        return f"hexarouting# [edit {where}] "

    def require(self, cap, what):
        """
        Refuses `what` unless the account holds `cap`, and says which role it
        has rather than only that it may not.
        """
        if cap in self.caps:
            return True
        self.err(f"{what}: permission denied for a {role_name(self.caps)} "
                 "account")
        if not self.caps:
            print(f"    membership of {GROUP_CGNAT} allows CGNAT "
                  f"configuration, {GROUP_ADMIN} allows everything")
        else:
            print(f"    this change needs {GROUP_ADMIN}")
        return False

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
        cfg.urpf = self.device_urpf()
        # Read from the box, not from what this VTY last wrote: the same
        # reason the CGNAT half is read back from the dataplane.
        cfg.ssh = self.sys.read_ssh()
        cfg.users = self.sys.read_users()
        cfg.snmp, cfg.snmp_communities, cfg.snmp_users = self.snmp.read()
        cfg.routing = self.frr.running()
        return cfg

    def device_urpf(self) -> dict:
        """
        Which interfaces have unicast RPF on, and in which mode.

        Read from the dataplane, never remembered: the VTY is a short-lived
        process and the device is the source of truth. One command rather than
        "show interface features" per interface, because running_config() is
        called on every commit and every show.

        Unlike MTU, only the interfaces that HAVE it are reported. Off is the
        absence of the feature and not a value the device holds, so listing
        every interface as "off" would put a line in the configuration for
        something nobody configured.
        """
        rc, out = self.dp.run("show bpa urpf")
        modes = {}
        for line in (out or "").splitlines():
            w = line.split()
            if len(w) == 2 and w[1] in ("strict", "loose"):
                modes[w[0]] = w[1]
        return modes

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
        self.remap = []

        # THE authorization check, here because this is where the change is
        # applied. The gates on `configure` and on the shell escape are
        # convenience: a check that only hides a command from the tree is not
        # an authorization check, and this function is reached by `commit`, by
        # `rollback` and by --load alike.
        #
        # Checked on a check-only run too, and deliberately: the answer to
        # "may I" must come before the answer to "would it work". Otherwise a
        # cgnat operator committing a routing change is told that FRR is
        # unhealthy - which is both the wrong answer and one they were not
        # entitled to.
        need = cap_needed(d)
        if need and not self.require(need, "commit"):
            return False

        # Asked once, before anything is touched. Discovering the dataplane is
        # unreachable halfway through leaves the box in a state that is neither
        # the old configuration nor the new one, and the operator with no way
        # to know which pools made it.
        if (d["pool_add"] or d["pool_replace"] or d["pool_remove"]
                or d["fwd_add"] or d["fwd_del"] or d["limits_set"]
                or d["bypass_set"] or d["settings_set"]):
            state, msg = self.api.available()
            if state != vppapi.OK:
                self.err(f"cannot reach the dataplane: {msg}")
                return False

        if check_only:
            ok = True
            # THE refusal this feature exists for. A configuration nobody can
            # log in with is not a valid configuration: it is the mistake an
            # operator makes once, from the other side of the country, and
            # cannot undo. Checked against the CANDIDATE, because that is what
            # would be in force afterwards.
            why = validate_ssh(target.ssh)
            if why:
                self.err(why)
                ok = False
            # Refused only when THIS CHANGE is what causes it. A box that
            # already has nobody able to log in over SSH - freshly installed,
            # reached by console - must still be able to commit a CGNAT
            # change; refusing there would be a VTY that cannot be used on the
            # box it was just installed on.
            if (not can_anyone_log_in(target.ssh, target.users)
                    and can_anyone_log_in(running.ssh, running.users)):
                self.err("this change locks everybody out: no account could "
                         "log in over SSH afterwards")
                print("    give an account an authorized key, or allow "
                      "password authentication for one that has a password")
                ok = False
            # An interface this box does not have.
            #
            # "interface Ten0" - a typo for TenGigabitEthernet0/8/0 - creates
            # an FRR stanza for something that is not there. It commits
            # cleanly, appears in the running configuration, and does nothing.
            # Same for "cgnat interface", which enables CGNAT on a name the
            # dataplane never heard of (#105).
            #
            # #112 made the wrong name much harder to type, since "?" and TAB
            # now offer the interfaces the box has. It did not make it
            # impossible: a runbook, a copy-paste or a habit still types it.
            rc_i, iface_out = self.dp.run("show interface")
            if rc_i == 0 and iface_out.strip():
                # BOTH names. A linux_cp pair has a VPP name and a Linux one,
                # and both are real: FRR configures by the Linux name, the
                # dataplane by its own. Checking only VPP's would refuse every
                # FRR interface stanza on every box that has linux_cp - which
                # is every box that routes - and lab/routing.sh caught exactly
                # that, on "interface vppwan0".
                _, lcp_out = self.dp.run("show lcp")
                have = set(routes.parse_interfaces(iface_out))
                # Every name every pair answers to. All of them are real.
                for names in routes.lcp_pairs(lcp_out).values():
                    have.update(names)
                # Every place an interface name can be staged, which is four
                # and not one: the CGNAT feature, an FRR block, and the mtu and
                # urpf dictionaries the VTY models itself. A check that looked
                # at only the first two passed a file whose interface name
                # lives in mtus - which is every file that sets an MTU, and is
                # how this was found.
                named = set(target.interfaces)
                named |= set(target.mtus)
                named |= set(target.urpf)
                for line in target.routing:
                    w = line.split()
                    if len(w) == 2 and w[0] == "interface":
                        named.add(w[1])
                for name in sorted(named - have):
                    self.err(f"interface {name}: this box has no such "
                             "interface")
                    print("    it has: " + ", ".join(sorted(have)))
                    print("    (a linux_cp pair answers to both its dataplane "
                          "name and its Linux one)")
                    print("    the statement would commit and do nothing")
                    ok = False
            # With the dataplane unreachable the check is SKIPPED, not failed.
            # A VTY that cannot commit while the dataplane is down cannot be
            # used to fix it.

            # A "router <protocol>" statement for a daemon that is not
            # running commits cleanly and reaches nothing. The box ships with
            # bgpd, ospfd and isisd disabled unless the installer enabled them
            # (#99), and vtysh answers "bgpd is not running" while frr-reload
            # still returns 0 - so the commit reports success for configuration
            # that went nowhere.
            if d["routing_changed"]:
                rc_d, daemons = self.frr.show("show daemons")
                if rc_d == 0 and daemons.strip():
                    running_daemons = set(daemons.split())
                    for line in target.routing:
                        w = line.split()
                        if len(w) < 2 or w[0] != "router":
                            continue
                        need = ROUTER_DAEMON.get(w[1])
                        if need and need not in running_daemons:
                            self.err(f"{line.strip()}: {need} is not running")
                            print(f"    the statement would commit and reach "
                                  f"nothing. Enable {need} in "
                                  "/etc/frr/daemons and restart frr")
                            ok = False

            # A static route whose next hop is on no connected network is
            # accepted by FRR, installed, and silently inert: FRR holds it, the
            # FIB never gets it, and "show ip route" looks correct. It is the
            # exact failure `show route verbose` exists to find afterwards, and
            # it is cheaper to say so at the prompt (#95).
            if d["routing_changed"] and target.routing:
                rc_fib, fib_text = self.dp.run("show ip fib")
                if rc_fib == 0 and fib_text.strip():
                    fibs = routes.parse_fib(fib_text)
                    table = fibs.get(0, {})
                    for stmt, nexthop in routes.unreachable_next_hops(
                            target.routing, table):
                        self.err(f"{stmt}: {nexthop} is on no connected "
                                 "network")
                        print("    the route would be accepted and never "
                              "reach the forwarding table")
                        ok = False

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
                # What replacing this pool would do to the subscribers on it.
                # Reported by the dataplane, which has both definitions and
                # the arithmetic - rather than recomputed here, which would be
                # a second implementation of the mapping in the VTY.
                self.remap += [l for l in (out or "").splitlines()
                               if l.startswith("remap:")]
            # A forward on a pool that is NOT being redefined. The ones on a
            # pool that is were just checked with it, as part of the same
            # definition - which is the only way to check a forward against a
            # pool that does not exist on the device yet.
            recreated = {p.name for p in d["pool_add"] + d["pool_replace"]}
            for pool, fwd in d["fwd_add"]:
                if pool.name in recreated:
                    continue
                rc, out = self.dp.run(pool.forward_command(fwd, check=True))
                if rc:
                    print(out)
                    self.err(f"pool '{pool.name}': forward {fwd[0]} {fwd[1]} "
                             "would be refused")
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

        # The settings the dataplane can change in place, before the lists
        # and the bounds: they are the ones an operator changes while looking
        # at a live problem, and a commit that applied them last would leave
        # them behind whatever else in the same commit failed.
        for name, pool in d["settings_set"].items():
            r = self.api_call("bpa_pool_settings_set",
                              pool.settings_api_args())
            if not r.ok:
                self.err(f"pool '{name}': settings: {r.reason}")
                return False

        # The bypass lists first: they only ever let traffic past
        # untranslated, so applying them before anything else cannot leave a
        # half-applied change translating what the operator just exempted.
        for pool, is_dst in d["bypass_set"]:
            r = self.api_call("bpa_bypass_set", pool.bypass_api_args(is_dst))
            if not r.ok:
                side = "destination" if is_dst else "source"
                self.err(f"pool '{pool.name}': bypass {side}: {r.reason}")
                return False

        # The per-subscriber bounds, before the forwards and before routing:
        # they only ever refuse, so applying them early cannot break a change
        # that is still half-applied.
        for name, pool in d["limits_set"].items():
            r = self.api_call("bpa_limits_set", pool.limits_api_args())
            if not r.ok:
                self.err(f"pool '{name}': limits: {r.reason}")
                return False

        # Removals first: a forward moved from one public port to another is
        # a removal and an addition, and doing the addition first would be
        # refused for colliding with the entry it is replacing.
        for pool, fwd in d["fwd_del"]:
            r = self.api_call("bpa_forward_add_del",
                              pool.forward_api_args(fwd, is_add=False))
            if not r.ok and r.state != vppapi.REFUSED:
                self.err(f"pool '{pool.name}': removing forward "
                         f"{fwd[0]} {fwd[1]}: {r.reason}")
                return False
        for pool, fwd in d["fwd_add"]:
            r = self.api_call("bpa_forward_add_del",
                              pool.forward_api_args(fwd))
            if not r.ok:
                self.err(f"pool '{pool.name}': forward {fwd[0]} {fwd[1]} "
                         f"to {fwd[2]} {fwd[3]}: {r.reason}")
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

        # The box's own configuration, after the dataplane and before
        # routing: an SSH change costs the operator their session if it is
        # wrong, and doing it while the CGNAT half is half-applied would leave
        # nobody able to finish the job.
        # A timeout under the floor its RFC sets is applied and warned
        # about, and the warning has to reach the person who set it - who is
        # at the far end of an API call and sees none of the dataplane's log.
        # Read back from the device rather than recomputed here: the floors
        # and the reasons live in one place, and a second copy in Python would
        # be a second thing to keep in step.
        if d["settings_set"] or any(p.timeouts for p in d["pool_add"]
                                    + d["pool_replace"]) or d["globals"]:
            rc, out = self.dp.run("show bpa timeouts")
            for line in (out or "").splitlines():
                if "BELOW" in line:
                    print(f"warning:{line.rstrip()}")

        if d["ssh_changed"]:
            ok, msg = self.sys.write_ssh(target.ssh)
            if not ok:
                self.err(f"ssh: {msg}")
                return False
            if msg:
                print(f"ssh: {msg}")
        if d["snmp_changed"]:
            ok, msg = self.snmp.apply(target.snmp, target.snmp_communities,
                                      target.snmp_users)
            if not ok:
                self.err(f"snmp: {msg}")
                return False
            if msg:
                print(f"snmp: {msg}")
        if d["users_changed"]:
            ok, msgs = self.sys.apply_users(target.users, running.users)
            for m in msgs:
                print(f"  {m}")
            if not ok:
                self.err("accounts were not fully applied")
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
        # uRPF before the MTU changes and after the CGNAT ones, which is
        # arbitrary except in one respect: it MUST be applied before the box
        # starts carrying the traffic the operator turned it on for, and a
        # commit is the only moment that is knowable.
        for ifname, mode in d["urpf_set"].items():
            self.dp.run(f"set urpf ip4 rx {mode} {ifname}")
        for ifname in d["urpf_del"]:
            self.dp.run(f"set urpf ip4 rx off {ifname}")
        if d["urpf_set"] or d["urpf_del"]:
            # Read back rather than trust the exit status, for the reason the
            # MTU path does: vppctl exits 0 having printed a parse error, and
            # a uRPF that was asked for and never applied is worse than one
            # that was never asked for - the operator believes the box is
            # dropping spoofed sources and it is translating them.
            have = self.device_urpf()
            for ifname, mode in d["urpf_set"].items():
                if have.get(ifname) != mode:
                    self.err(f"interface {ifname}: the dataplane did not take "
                             f"urpf {mode}")
                    return False
            for ifname in d["urpf_del"]:
                if ifname in have:
                    self.err(f"interface {ifname}: uRPF is still on after "
                             "being removed")
                    return False

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

        Every other name the pair answers to is tried, rather than the one in a
        fixed column. The parse this replaced looked for a literal "host" token
        and read the field after it; VPP prints

            itf-pair: [0] host-veth-vpp tap4096 vppwan0 5 type tap

        and there is no such token, so it found nothing - which is
        indistinguishable from the legitimate no-TAP case above, and so was
        never noticed. lcp-sync was mirroring the MTU onto the TAP by itself
        and the lab assertion was crediting this function for VPP's work. The
        lab now turns lcp-sync off before asserting.
        """
        rc, out = self.dp.run("show lcp")
        if rc != 0:
            return
        for name in routes.lcp_pairs(out).get(ifname, []):
            if name == ifname:
                continue
            subprocess.run(["ip", "link", "set", "dev", name, "mtu", str(mtu)],
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
        """commit [check | remap | confirmed <min> | comment <text>]"""
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

        # A change that re-maps existing subscribers is refused until it is
        # asked for by name.
        #
        # Not a warning printed on the way past: in deterministic mode a
        # subscriber's public address and port block are a function of the
        # pool definition, so adding one public prefix moves every subscriber
        # at that instant and ends every session on the pool. The box reports
        # nothing unusual afterwards, because nothing unusual happened - it did
        # what it was told. An operator learning that from the consequence
        # learns it during an outage.
        # "remap" is a prefix rather than the whole word, so it combines with
        # comment and confirmed: an operator who has to acknowledge a
        # re-mapping should not lose the ability to record why.
        remap_ok = bool(args) and args[0] == "remap"
        if remap_ok:
            args = args[1:]

        if not remap_ok:
            self.apply(self.candidate, check_only=True)
            if self.remap:
                self.err("this change re-maps subscribers that already have "
                         "mappings")
                for line in self.remap:
                    print("    " + line[len("remap:"):].strip())
                print("    if that is what you mean, type: commit remap")
                return
            self.remap = []

        if running.is_same(self.candidate):
            print("no changes to commit")
            return

        confirm_min = 0
        comment = ""
        # A change to SSH or to accounts is the one change that can cost the
        # operator the session they are typing it in. It is therefore applied
        # as "commit confirmed" whether or not they asked, and the VTY says so
        # - choosing it silently would be a surprise in the other direction.
        d0 = running.delta_to(self.candidate)
        touches_access = d0["ssh_changed"] or d0["users_changed"]
        if args and args[0] == "confirmed":
            try:
                confirm_min = int(args[1]) if len(args) > 1 else 10
            except (IndexError, ValueError):
                self.err("usage: commit confirmed <minutes>")
                return
        elif args and args[0] == "comment":
            comment = " ".join(args[1:]).strip('"')

        if touches_access and not confirm_min:
            confirm_min = 10
            print("this commit changes SSH or accounts, which can cut off the "
                  "session you are using")
            print("applying it as 'commit confirmed 10': it reverts on its own "
                  "unless you enter 'commit' again from a session that works")

        if not self.apply(self.candidate, check_only=True):
            self.err("candidate did not pass validation; nothing applied")
            return

        backup = running.text()
        if not self.apply(self.candidate):
            self.err("commit failed; rolling back to the previous state")
            self.apply(Config.parse(backup))
            return

        try:
            self.rb.push(self.candidate.text(), comment)
        except RollbackUnavailable as e:
            self.err("the change is applied, but no rollback revision could "
                     f"be recorded: {e}")
            print("    'rollback' will not be able to undo this commit")
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

        Deliberately absent from TREE, so neither completion nor `?` offers
        it - and, since roles exist, RESTRICTED as well: a shell on this box is
        every capability at once, so administrators only. Hiding it was never
        the boundary; this check is what makes the sentence above true rather
        than a hope. It exists
        because recovery, tcpdump and reading a log the VTY does not expose all
        need a shell, and an appliance that makes those impossible gets one
        opened permanently by the first operator who needs one.

        Spawned as a child rather than exec'd: leaving the shell has to come
        back to the VTY, not log the operator out.
        """
        if not self.require(CAP_ALL, "start shell"):
            return
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
        if not self.require(CAP_ALL, "install"):
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
            if not self.caps:
                self.err("configure: permission denied for a read-only "
                         "account")
                print(f"    membership of {GROUP_CGNAT} allows CGNAT "
                      f"configuration, {GROUP_ADMIN} allows everything")
                return True
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
        if w[:2] == ["snmp", "community"] and len(w) > 2:
            if w[2] not in self.candidate.snmp_communities:
                self.candidate.snmp_communities.append(w[2])
            return True
        if w[:3] == ["no", "snmp", "community"] and len(w) > 3:
            self.candidate.snmp_communities = [
                c for c in self.candidate.snmp_communities if c != w[3]]
            return True
        if w[0] == "snmp-user" and len(w) > 1:
            self.snmp_user = self.candidate.snmp_users.get(w[1]) or SnmpUser(w[1])
            self.candidate.snmp_users[w[1]] = self.snmp_user
            self.mode = "snmp-user"
            return True
        if w[:2] == ["no", "snmp-user"] and len(w) > 2:
            self.candidate.snmp_users.pop(w[2], None)
            return True
        if w[0] == "snmp":
            if len(w) < 3:
                self.err("usage: snmp <setting> <value>")
                return True
            why = validate_snmp({w[1]: " ".join(w[2:])})
            if why:
                self.err(why)
            else:
                self.candidate.snmp[w[1]] = " ".join(w[2:])
            return True
        if w[0] == "ssh":
            if len(w) < 3:
                self.err("usage: ssh <setting> <value>")
                return True
            why = validate_ssh({w[1]: " ".join(w[2:])})
            if why:
                self.err(why)
            else:
                self.candidate.ssh[w[1]] = " ".join(w[2:])
            return True
        if w[0] == "user" and len(w) > 1:
            self.user = self.candidate.users.get(w[1]) or User(w[1])
            self.candidate.users[w[1]] = self.user
            self.mode = "user"
            return True
        if w[:2] == ["no", "user"] and len(w) > 2:
            if w[2] == "root":
                self.err("root cannot be removed; restrict it with "
                         "'ssh permit-root-login' instead")
            else:
                self.candidate.users.pop(w[2], None)
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

        # `urpf` is the device's too, for the same reason as `mtu`: the check
        # has to run on the packet path, and FRR does not own that.
        if (self.frr_ctx and self.frr_ctx[-1].startswith("interface ")
                and w[0] == "urpf"):
            ifname = self.frr_ctx[-1].split()[1]
            if len(w) < 2 or w[1] not in URPF_MODES:
                self.err("usage: urpf <" + "|".join(URPF_MODES) + ">")
            elif w[1] == "off":
                self.candidate.urpf.pop(ifname, None)
            else:
                self.candidate.urpf[ifname] = w[1]
            return True
        if (self.frr_ctx and self.frr_ctx[-1].startswith("interface ")
                and w[:2] == ["no", "urpf"]):
            self.candidate.urpf.pop(self.frr_ctx[-1].split()[1], None)
            return True

        # anything else is routing: staged in the candidate, applied on commit
        self.candidate.routing.append(line)
        if line.startswith(("router ", "interface ", "vrf ", "route-map ")):
            self.frr_ctx.append(line)
        return True

    def do_snmp_user(self, line):
        w = line.split()
        u = self.snmp_user
        # "show this" is most useful exactly here, and each sub-mode
        # dispatches its own lines - so without this it is refused in the one
        # place it exists for.
        if w[0] == "show":
            self.show_or_pass(line)
            return True
        if w[0] in ("exit", "end"):
            self.mode = "config"
            self.snmp_user = None
        elif w[0] in ("auth", "priv") and len(w) > 2:
            setattr(u, w[0], " ".join(w[1:]))
        else:
            self.err("a v3 user takes 'auth <protocol> <passphrase>' and "
                     "'priv <protocol> <passphrase>'")
        return True

    def do_user(self, line):
        w = line.split()
        u = self.user
        # "show this" is most useful exactly here, and each sub-mode
        # dispatches its own lines - so without this it is refused in the one
        # place it exists for.
        if w[0] == "show":
            self.show_or_pass(line)
            return True
        if w[0] in ("exit", "end"):
            self.mode = "config"
            self.user = None
            return True
        if w[0] == "ssh-key" and len(w) > 1:
            key = " ".join(w[1:])
            if key not in u.keys:
                u.keys.append(key)
        elif w[0] == "password" and len(w) > 1:
            # A crypt hash, never a plaintext password: plaintext in
            # /etc/shadow does not fail loudly, it produces an account whose
            # password simply never matches.
            if not w[1].startswith("$") or w[1].count("$") < 3:
                self.err("expected a crypt hash (openssl passwd -6), not a "
                         "plaintext password")
            else:
                u.password = w[1]
        elif w[:2] == ["no", "ssh-key"] and len(w) > 2:
            key = " ".join(w[2:])
            u.keys = [k for k in u.keys if k != key and not k.startswith(key)]
        elif w[:2] == ["no", "password"]:
            u.password = ""
        else:
            self.err(f"unknown statement in user mode: {w[0]}")
        return True

    def do_pool(self, line):
        w = line.split()
        p = self.pool
        if w[0] in ("exit", "end"):
            self.mode = "config"
            self.pool = None
            return True
        # "show this" is most useful exactly here, and each sub-mode
        # dispatches its own lines - so without this it is refused in the one
        # place it exists for.
        if w[0] == "show":
            self.show_or_pass(line)
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
            elif w[0] in ("vrf", "outside-vrf"):
                why = Config._pool_line(p, " ".join(w))
                if why:
                    raise ValueError(why)
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
            elif w[0] == "bypass":
                why = Config._pool_line(p, " ".join(w))
                if why:
                    raise ValueError(why)
            elif w[0] == "utilisation-alarm":
                why = Config._pool_line(p, " ".join(w))
                if why:
                    raise ValueError(why)
            elif w[0] in ("max-sessions", "max-session-rate"):
                if int(w[1]) < 0:
                    raise ValueError(f"{w[0]} cannot be negative")
                if w[0] == "max-sessions":
                    p.max_sessions = int(w[1])
                else:
                    p.max_rate = int(w[1])
            elif w[0] == "tcp-mss":
                why = Pool.check_mss(w[1])
                if why:
                    raise ValueError(why)
                p.tcp_mss = w[1]
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
            elif w[0] == "forward":
                # Parsed by the model rather than here. This block and
                # Config._pool_line() both accept pool statements - one typed,
                # one read from a file - and a statement that means two
                # different things depending on how it arrived is a defect
                # waiting for someone to load their own saved configuration.
                why = Config._pool_line(p, " ".join(w))
                if why:
                    raise ValueError(why)
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
                # "no forward tcp 1100": the public port and protocol identify
                # it, which is the pair the dataplane keys on.
                elif w[1] == "forward" and len(w) > 3:
                    p.forwards = [f for f in p.forwards
                                  if not (f[0] == w[2]
                                          and str(f[1]) == w[3])]
            elif w[0] == "no" and len(w) > 1 and w[1] == "max-blocks":
                p.max_blocks = 0
            elif w[0] == "no" and len(w) > 1 and w[1] == "tcp-mss":
                p.tcp_mss = ""
            elif w[0] == "no" and len(w) > 2 and w[1] == "bypass":
                # "no bypass source 10.0.0.0/8", or "no bypass source" to
                # clear the list.
                lst = (p.bypass_dst if w[2] == "destination"
                       else p.bypass_src)
                if len(w) > 3:
                    if w[3] in lst:
                        lst.remove(w[3])
                else:
                    lst.clear()
            elif w[0] == "no" and len(w) > 1 and w[1] == "utilisation-alarm":
                p.util_alarm = 0
            elif w[0] == "no" and len(w) > 1 and w[1] == "max-sessions":
                p.max_sessions = 0
            elif w[0] == "no" and len(w) > 1 and w[1] == "max-session-rate":
                p.max_rate = 0
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
            "show cgnat drops": "show bpa drops",
        }
        if line in m:
            self.vpp(m[line])
            return
        # The ones that carry an argument. Matched by prefix so the argument
        # travels through untouched; hexash is not the place to re-validate an
        # address the dataplane is about to parse anyway.
        for pfx in ("show cgnat subscriber", "show cgnat translation",
                    "show cgnat top"):
            if line == pfx or line.startswith(pfx + " "):
                self.vpp("show bpa" + line[len("show cgnat"):])
                return
        if line in ("show configuration compare", "show configuration | compare"):
            self.show_compare()
            return
        if line == "show configuration":
            self.print_config(self.candidate or self.running_config())
            return
        if line == "show rollback":
            self.show_rollback()
            return
        if line in ("show running-config", "show run"):
            self.print_config(self.running_config())
            return
        if line == "show version":
            self.show_version()
            return
        _w = line.split()
        if _w[:2] == ["show", "this"]:
            self.show_this()
            return
        if _w[:2] == ["show", "interface"]:
            self.show_interfaces(_w[2:])
            return
        if _w[:2] == ["show", "route"]:
            self.show_route(_w[2:])
            return
        rc, out = self.frr.show(line)
        if out:
            print(out)

    @staticmethod
    def print_config(cfg):
        """
        A configuration with the password hashes taken out.

        Display only. The stored file and every rollback revision keep the real
        value - a rollback that cannot restore a password is not a rollback -
        which is why those files must not be world-readable. A hash on a screen
        shared during an incident is a hash that has left the building, and it
        is crackable offline at the attacker's leisure.
        """
        print("\n".join(snmp_redact(redact(cfg.text().splitlines()))))

    def show_this(self):
        """
        The configuration of the hierarchy being edited, and nothing above it.

        Inside `cgnat pool residential` there was no way to see what that pool
        holds without leaving and running `show configuration`, which prints
        the whole box. On a machine with twenty pools that is a screen of
        configuration to find the eight lines being edited (#98).

        It shows the CANDIDATE. Somebody in the middle of an edit asking "what
        is here" is asking about what they are building, and answering with
        what is committed would answer a question they did not ask.
        """
        cfg = self.candidate
        if cfg is None:
            self.err("show this: not in configuration mode")
            print("    'show running-config' shows what is in effect")
            return

        if self.mode == "pool" and self.pool is not None:
            print("\n".join(cfg.pools[self.pool.name].lines()
                             if self.pool.name in cfg.pools
                             else self.pool.lines()))
            return
        if self.mode == "user" and self.user is not None:
            print("\n".join(self.user.lines()))
            return
        if self.mode == "snmp-user" and self.snmp_user is not None:
            print("\n".join(self.snmp_user.lines()))
            return

        # A block entered by an argument rather than a keyword the tree can
        # walk into.
        if self.frr_ctx:
            block = self.frr_ctx[-1]
            print(block)

            # An interface's own settings are staged in cfg.mtus and cfg.urpf,
            # NOT in cfg.routing. This branch was written for FRR blocks and
            # never saw the fields the VTY models itself, so it printed the
            # header and stopped - reporting a hierarchy that plainly had an
            # MTU as empty (#108).
            #
            # An operator who runs "show this" and sees nothing concludes the
            # block is empty and configures accordingly, which makes an empty
            # answer worse than no command at all.
            if block.startswith("interface "):
                ifname = block.split(None, 1)[1]
                if ifname in cfg.mtus:
                    print(f" mtu {cfg.mtus[ifname]}")
                if ifname in cfg.urpf:
                    print(f" urpf {cfg.urpf[ifname]}")
            started = False
            for line in cfg.routing:
                if line == block:
                    started = True
                    continue
                if started:
                    if line and not line[0].isspace():
                        break
                    print(line)
            return

        # At the top of configuration mode, "this" is the whole candidate -
        # which is what `show configuration` already prints, and saying so is
        # better than printing it twice under two names.
        self.print_config(cfg)

    def show_interfaces(self, args):
        """
        The dataplane's interfaces.

        This used to be handed to vtysh, which answers from FRR's view - and
        FRR sees only what linux_cp has mirrored. An operator asking what
        interfaces this box has was shown a list missing the ones that carry
        traffic: on a box with a GigabitEthernet0/12/0 configured, the answer
        was "lo" (#112).

        Same shape as `show ip route` answering from the RIB. The FRR view is
        still reachable - `show ip interface`, and anything else vtysh knows -
        because sometimes that IS the question.
        """
        rc, out = self.dp.run("show interface")
        if rc != 0 or not out.strip():
            # An empty table reads as "this box has no interfaces", which is a
            # different and much more alarming statement than "I could not
            # ask".
            self.err("cannot read the interfaces: the dataplane is not "
                     "answering")
            return
        _, addr = self.dp.run("show interface addr")
        _, lcp = self.dp.run("show lcp")
        ifaces = routes.parse_interfaces(out, addr, lcp)

        wanted = [a for a in args if a not in ("description", "descr", "brief")]
        if wanted:
            ifaces = {n: e for n, e in ifaces.items()
                      if any(_starts(n, w) for w in wanted)}
            if not ifaces:
                print("  no interface matches")
                return

        has_host = any(e["host"] for e in ifaces.values())
        head = f"  {'Interface':<26} {'State':<6} {'MTU':<6} {'Address':<20}"
        print(head + ("Linux" if has_host else ""))
        for name in sorted(ifaces):
            e = ifaces[name]
            addrs = e["addresses"] or ["-"]
            print(f"  {name:<26} {e['state']:<6} {e['mtu']:<6} "
                  f"{addrs[0]:<20}{e['host'] or ('-' if has_host else '')}")
            for extra in addrs[1:]:
                print(f"  {'':<26} {'':<6} {'':<6} {extra:<20}")

        # local0 is VPP's own loopback and configures nothing. It IS listed -
        # hiding an interface the dataplane reports would be this command
        # deciding what the operator may see - but it is not offered for
        # completion, and the difference is worth stating rather than leaving
        # as an inconsistency somebody has to work out.
        print(f"\n  {len(ifaces)} interface(s) on the dataplane")

        if any(a in ("description", "descr") for a in args):
            # They asked for descriptions and got a table without them. Saying
            # nothing would leave somebody looking for a column that is never
            # coming.
            print("  the dataplane carries no per-interface description; "
                  "FRR's view is 'show ip interface'")

    def show_route(self, args):
        """
        The forwarding table, and on request the two tables behind it.

        `show ip route` in vtysh answers from FRR's RIB. That is the command an
        operator reaches for when traffic is not flowing, and it is the table
        where the route is present and correct on a box that is dropping the
        traffic - because the table that forwards is VPP's FIB, and FRR's
        answer says nothing about whether the route ever got there.
        """
        verbose = "verbose" in args
        wanted = [a for a in args if a != "verbose"]

        rc, out = self.dp.run("show ip fib")
        if rc != 0 or not out.strip():
            self.err("cannot read the forwarding table: the dataplane is not "
                     "answering")
            return
        tables = routes.parse_fib(out)

        rib = {}
        if verbose:
            # Only asked for when it is going to be used. A box may run CGNAT
            # with no routing daemon at all, and `show route` must not depend
            # on one being there.
            rrc, rout = self.frr.show("show ip route json")
            rib = routes.parse_rib(rout) if rrc == 0 else {}
            # Which interfaces VPP owns, so routes through the management
            # interface are not reported as faults for ever.
            _, lcp = self.dp.run("show lcp")
            self._vpp_hosts = routes.vpp_host_interfaces(lcp)
            if not rib:
                print("  (FRR did not answer; showing the forwarding table "
                      "alone)")

        for vrf in sorted(tables):
            table = tables[vrf]
            if len(tables) > 1:
                print(f"VRF {vrf}")

            if verbose:
                self._route_rows_verbose(table, rib, wanted)
            else:
                self._route_rows(table, wanted)

    def _route_rows(self, table, wanted):
        rows = routes.forwarding(table)
        if wanted:
            rows = {p: e for p, e in rows.items()
                    if any(p.startswith(x) or x.startswith(p.split("/")[0])
                           for x in wanted)}
        if not rows:
            print("  no matching entry in the forwarding table")
            return

        print(f"  {'Destination':<20} {'Next hop':<17} {'Interface':<12} Kind")
        for prefix in sorted(rows, key=routes._sort_key):
            e = rows[prefix]
            via, iface = e["via"][0] if e["via"] else ("-", "-")
            print(f"  {prefix:<20} {via:<17} {iface:<12} {e['kind']}")
            for via, iface in e["via"][1:]:
                print(f"  {'':<20} {via:<17} {iface:<12} (also)")
        print(f"\n  {len(rows)} entr{'y' if len(rows) == 1 else 'ies'} "
              "carrying traffic")

    def _route_rows_verbose(self, table, rib, wanted):
        rows = routes.compare(table, rib, getattr(self, "_vpp_hosts", None))
        if wanted:
            rows = [r for r in rows
                    if any(r[0].startswith(x) or x.startswith(r[0].split("/")[0])
                           for x in wanted)]
        if not rows:
            print("  no matching entry in either table")
            return

        print(f"  {'Destination':<20} {'FRR':<5} {'VPP':<10} "
              f"{'Next hop':<17} {'Interface':<12}")
        missing = []
        for prefix, in_rib, in_fib, kind, via, iface in rows:
            mark = ""
            if in_rib and not in_fib:
                mark = "  ** in FRR, NOT forwarding"
                missing.append(prefix)
            print(f"  {prefix:<20} {'yes' if in_rib else '-':<5} "
                  f"{kind:<10} {via:<17} {iface:<12}{mark}")

        if missing:
            # The whole reason this command exists. Two lists side by side and
            # a reader left to compare them is not an answer.
            print(f"\n  {len(missing)} prefix{'' if len(missing) == 1 else 'es'} "
                  "in FRR's table and not in the forwarding table.")
            print("  Traffic to " + ("it" if len(missing) == 1 else "them")
                  + " is dropped. FRR installs routes into the kernel and")
            print("  linux_nl carries them into VPP; a route that stops half "
                  "way leaves")
            print("  both halves looking healthy on their own.")
        else:
            print("\n  every route FRR holds is in the forwarding table")

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
        """
        Write the running configuration to disk, readable only by root.

        The mode is not housekeeping. Since accounts became part of the
        configuration this file carries password hashes and public keys, so a
        world-readable copy hands every local account an offline cracking
        target. Set on every write rather than at creation, because a file
        that already exists keeps whatever mode it was given.
        """
        d = os.path.dirname(CONFIG_PATH)
        try:
            if d:
                os.makedirs(d, exist_ok=True)
                os.chmod(d, 0o700)
            with open(CONFIG_PATH, "w") as f:
                f.write(self.running_config().text())
            os.chmod(CONFIG_PATH, 0o600)
        except OSError as e:
            self.err(f"cannot write {CONFIG_PATH}: {e}")
            return
        print(f"configuration saved to {CONFIG_PATH}")

    # ---------------------------------------------------------- completion

    # ------------------------------------------------- what the box knows

    def dyn_interfaces(self):
        """
        The interfaces this box has, from the dataplane.

        Naming one that does not exist creates an FRR stanza for an interface
        that is not there: it commits cleanly and does nothing, and nothing
        says so.
        """
        rc, out = self.dp.run("show interface")
        if rc != 0 or not out.strip():
            return {}
        names = {}
        for line in out.splitlines():
            w = line.split()
            # The name is in the first column and the header is not indented;
            # continuation lines are.
            if not w or line[0].isspace() or w[0] in ("Name",):
                continue
            if w[0] == "local0":
                continue  # VPP's own loopback: nothing to configure on it
            state = w[2] if len(w) > 2 else ""
            names[w[0]] = f"interface ({state})" if state else "interface"
        return names

    def dyn_pools(self):
        """Pools in the candidate, or in the running configuration."""
        cfg = self.candidate or self.running_config()
        return {name: "CGNAT pool" for name in cfg.pools}

    def dyn_users(self):
        cfg = self.candidate or self.running_config()
        return {name: "account" for name in cfg.users}

    def dyn_snmp_users(self):
        cfg = self.candidate or self.running_config()
        return {name: "SNMPv3 user" for name in cfg.snmp_users}

    #: hint -> the method that answers it. Kept as data so a marker added to
    #: the tree without a source here shows up as an absence rather than
    #: silently going back to being a bare "<word>".
    DYNAMIC = {
        ARG_INTERFACE: "dyn_interfaces",
        ARG_POOL: "dyn_pools",
        ARG_USER: "dyn_users",
        ARG_SNMP_USER: "dyn_snmp_users",
    }

    def dynamic_options(self, hint, prefix):
        """
        The values behind an argument hint, or nothing when they cannot be had.

        Never raises. A source that cannot answer - the dataplane down, FRR not
        running, no candidate - offers nothing, because a VTY that becomes
        unusable when the dataplane is down is a VTY that cannot be used to fix
        it.
        """
        name = self.DYNAMIC.get(hint)
        if not name:
            return {}
        try:
            values = getattr(self, name)()
        except Exception:
            return {}
        return {k: v for k, v in values.items() if _starts(k, prefix)}

    def _options(self, line, ending_word):
        words = line.split()
        prefix = ""
        if ending_word and words:
            prefix = words[-1]
            words = words[:-1]
        node, hint = tree_walk(self.mode, words)

        # A block entered by an ARGUMENT is still mode "config", so the tree
        # walk returns the whole configuration tree - fifteen options inside an
        # interface, of which three are valid there. Offering the level above
        # invites somebody to type "cgnat pool" inside an interface and find
        # out afterwards what that meant (#112).
        #
        # The blocks the tree knows about were always right: a pool offers its
        # own, a user its own. Only these needed narrowing.
        if not words and self.frr_ctx:
            block = self.frr_ctx[-1]
            if block.startswith("interface "):
                opts = dict(INTERFACE_BLOCK)
            else:
                # An FRR block. This VTY does not model its statements, so
                # offering any would be a guess presented as a grammar.
                opts = {"exit": ("leave the block", None)}
            opts["show"] = ("display information", None)
            return ({k: v[0] for k, v in opts.items() if _starts(k, prefix)},
                    hint, prefix)

        if node is None:
            # The tree knows a name goes here; the box knows which names.
            dyn = self.dynamic_options(hint, prefix)
            if dyn:
                return dyn, hint, prefix
            return {}, hint, prefix
        opts = {k: v[0] for k, v in node.items() if _starts(k, prefix)}
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
                   "pool": self.do_pool,
                   "user": self.do_user,
                   "snmp-user": self.do_snmp_user}[self.mode]
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

        # Checked before it is applied, and refused whole if the check refuses.
        #
        # This runs at boot, unattended. Without it a file the VTY would refuse
        # at the prompt is applied anyway, statement by statement, until
        # something fails part way - and the box comes up holding half of it,
        # on a machine nobody is watching. That is the same argument the
        # refusal above makes about lines this VTY cannot read (#105).
        before = self.failed
        if not self.apply(target, check_only=True):
            self.err(f"{path}: refusing to load, the check above says why")
            return
        self.failed = before

        if self.apply(target):
            try:
                self.rb.push(target.text(), "loaded from file")
            except RollbackUnavailable as e:
                self.err("loaded, but no rollback revision could be "
                         f"recorded: {e}")

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
