"""
The host's doors (#448, phase B): the operations the VTY needs done on the
host, asked of a daemon on a unix socket instead of run by the VTY itself.

WHY. Phase C moves the VTY into a container on the backplane, and from there
it cannot run `useradd`, reload sshd or restart snmpd - those act on the host,
and a container that could do them directly is a container with the host's
root. So each operation becomes a door: the VTY sends the command it would
have run, the daemon checks that it is one of the shapes below, with every
argument checked, and runs it on the host. Phase B is the doors with the VTY
still on the host, so a regression is a door and not the move.

WHAT A DOOR IS NOT: a way to run a command. The daemon runs root, so a door
that took any argv would be a root shell for whoever reached the socket.
`verb_of` accepts a closed list of shapes, and each argument slot is checked
against what the VTY actually puts there: an account name that cannot be read
as an option, the one login shell, the groups the VTY manages, and one
`name:hash` line for chpasswd. Anything else is refused, whoever asks.

WHO. The daemon runs a door only for a caller the host already lets do it:
uid 0, read from the socket with SO_PEERCRED and not from anything the
caller says. That is exactly today's rule - the VTY runs as the account that
logged in, and only root's could run these commands (#212 declined granting
more) - so this phase changes where the command runs and nothing about who
may run it.

The client, `runner()`, has the signature System and SnmpDaemon take as
`runner`: (argv, stdin=None, timeout=30) -> (rc, output). With no daemon
listening it runs the command itself, as the VTY always has, so a box
upgraded before its daemon started is a box that still works.
"""

import errno
import ipaddress
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time

from system import (LOGIN_SHELL, MANAGED_GROUPS, GROUP_OPERATOR,
                    OPERATOR_GROUPADD, LocalFiles, RESOLV_CONF,
                    RESOLV_BEFORE, SSHD_DROPIN, SNMPD_CONF, SET_HOSTNAME,
                    hostname_error, is_number)
import events
import transceiver
from config import (LINUX_IFNAME_MAX, PROBE_COUNT_MAX, PROBE_DEADLINE,
                    VRF_RESERVED_PREFIX, probe_host_ok, probe_source_ok,
                    vrf_device_error, IFACE_VRF_MIN, IFACE_VRF_MAX)

SOCKET = "/run/prisma-routing/doors.sock"

#: The two rules management in its own VRF puts in (#580): the main table
#: without its default for what the box itself sends (`iif lo`), then the
#: VRF's table. Below the main table's own rule (32766) and above nothing
#: of the box's: prisma-bp's l3mdev rule is at 1000.
MGMT_RULE_MAIN, MGMT_RULE_VRF = "31000", "31001"
#: The sysctls that let the main table's listeners answer on a VRF.
MGMT_SYSCTLS = ("net.ipv4.tcp_l3mdev_accept", "net.ipv4.udp_l3mdev_accept")


def mgmt_table_ok(table) -> bool:
    """A table the management rules may name: a VRF's, never the kernel's
    own (253-255) or the main one."""
    t = str(table)
    return is_number(t) and IFACE_VRF_MIN <= int(t) <= IFACE_VRF_MAX \
        and int(t) not in (253, 254, 255)


def management_argvs(table):
    """
    What puts management in its own VRF on the host (#580), or takes it out
    (table 0), as [(argv, may_fail)]: the old rules removed - absent is not a
    failure - then the sysctls, then the new rules. The one list the VTY runs
    and answer() accepts, so the two cannot drift.
    """
    on = "1" if table else "0"
    out = []
    for fam in ((), ("-6",)):
        for pref in (MGMT_RULE_MAIN, MGMT_RULE_VRF):
            out.append((("ip",) + fam + ("rule", "del", "pref", pref), True))
    for key in MGMT_SYSCTLS:
        out.append((("sysctl", "-q", "-w", f"{key}={on}"), False))
    if table:
        for fam in ((), ("-6",)):
            out.append((("ip",) + fam + ("rule", "add", "pref",
                         MGMT_RULE_MAIN, "iif", "lo", "lookup", "main",
                         "suppress_prefixlength", "0"), False))
            out.append((("ip",) + fam + ("rule", "add", "pref",
                         MGMT_RULE_VRF, "iif", "lo", "lookup",
                         str(table)), False))
    return [(list(a), f) for a, f in out]


def management_door(argv) -> bool:
    """Is `argv` one management_argvs() builds, for some table?"""
    argv = tuple(argv)
    if argv[:2] == ("ip", "-6"):
        rest = argv[2:]
    elif argv[:1] == ("ip",):
        rest = argv[1:]
    elif argv[:3] == ("sysctl", "-q", "-w") and len(argv) == 4:
        key, _, val = argv[3].partition("=")
        return key in MGMT_SYSCTLS and val in ("0", "1")
    else:
        return False
    if rest[:3] == ("rule", "del", "pref") and len(rest) == 4:
        return rest[3] in (MGMT_RULE_MAIN, MGMT_RULE_VRF)
    if rest == ("rule", "add", "pref", MGMT_RULE_MAIN, "iif", "lo",
                "lookup", "main", "suppress_prefixlength", "0"):
        return True
    return (len(rest) == 8 and rest[:7] == ("rule", "add", "pref",
            MGMT_RULE_VRF, "iif", "lo", "lookup")
            and mgmt_table_ok(rest[7]))

#: An account name as a door takes it: never an option (no leading '-'),
#: never a path, at most useradd(8)'s 32 characters. root is refused where
#: the VTY never sends it (userdel, useradd, usermod, gpasswd).
ACCOUNT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}$")

#: What chpasswd -e reads: ONE line, an account and a crypt(3) hash. The
#: hash has no ':' and no newline, so the line cannot name a second account.
CHPASSWD_LINE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.-]{0,31}):"
                           r"(\$[A-Za-z0-9./$=,+-]{1,512})\n$")

#: A Linux netdev as a door takes it: at most IFNAMSIZ-1 characters, never an
#: option, never a path. The ip-link doors act on the LINUX end of a dataplane
#: interface (its LCP pair), in the host's network namespace - which is why
#: they are doors at all: from a container the same `ip link` would act on the
#: container's own interfaces and report success (#448's inventory).
NETDEV = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:@-]{0,%d}$"
                    % (LINUX_IFNAME_MAX - 1))

#: IFALIASZ is 256 with the terminator. The VTY's description is shorter
#: (63), so this bounds what a door takes, not what the VTY sends.
ALIAS_MAX = 255

#: The host program that puts the management container - the VTY's - on the
#: images an upgrade just installed, and nothing else (#528).
REFRESH_VTY = "/usr/lib/prisma-routing/refresh-the-vty"
#: How `system upgrade` asks for it: DETACHED, a few seconds on, as a unit of
#: its own. The session asking lives in the container this recreates, so run
#: in-line it would be killed half way by its own work; systemd-run returns at
#: once, the timer fires after the session has said what happens, and
#: --collect lets the next upgrade reuse the name even after a failed run.
REFRESH_VTY_ARGV = ("systemd-run", "--collect", "--on-active=3",
                    "--unit=prisma-routing-refresh-vty", REFRESH_VTY)

#: Where a detached run the API started writes what it prints (#558, phase
#: 5): the host's /run/prisma-routing, which the management container mounts,
#: so the API reads it there - and a new API, after the upgrade recreated the
#: container, still finds it.
API_RUN_DIR = "/run/prisma-routing/api"
#: What the API may ask the host to run detached, by kind: the statement the
#: host's VTY runs. An upgrade restarts the management container the API is
#: in, and a dataplane restart outlasts a gRPC call: so neither runs in the
#: API's process, and both are units of their own.
API_RUNS = {"upgrade": "system upgrade", "dataplane": "system dataplane restart"}
#: Where the hot-card tool lives on a box (#564).
HOT_CARD_INSTALLED = "/usr/lib/prisma-routing/prisma-hot-card"
#: What a detached dataplane restart prints, in the host's /run (#564).
DATAPLANE_RESTART_LOG = "/run/prisma-routing/dataplane-restart.log"


def dataplane_restart_argv(tool=HOT_CARD_INSTALLED, log=DATAPLANE_RESTART_LOG):
    """
    `system dataplane restart` as a unit of its own (#564): the hot-card
    tool's restart, its output in `log`, its last line `prisma-api-exit <n>`
    as the API's detached runs end (prismash's follow_run_log reads it).

    WHY A UNIT. Run as a child of the VTY, the restart died with whatever ran
    the VTY: on 10.95.200.198 (0.149.26) the API's run of it stopped the
    containers and never started them, and the box - whose management is a
    dataplane interface - was dark until a reboot. A unit outlives its
    caller; the caller only watches.

    BUILT HERE, beside the door that accepts it (verb_of, operator_door):
    0.149.27 built it in prismash.py, the door knew no such shape, and every
    restart on a box was refused - "not an operation the VTY is allowed on the
    host" - while the labs, which run it in-line without doors, passed.
    """
    return detached("prisma-routing-dataplane-restart", log,
                    f"{tool} restart-dataplane")


#: A release as `system upgrade` takes it.
RELEASE = re.compile(r"^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$")


def api_run_log(kind):
    """The file a detached API run of `kind` writes to."""
    return f"{API_RUN_DIR}/{kind}.log"


def api_run_argv(kind, version=""):
    """
    The door that starts `kind` (API_RUNS) detached, as a transient unit
    whose output goes to api_run_log(kind) and whose last line is
    `prisma-api-exit <status>` - the whole shape, the version (an upgrade's
    only argument) interpolated only after RELEASE accepted it. None for
    anything else.
    """
    if kind not in API_RUNS:
        return None
    stmt = API_RUNS[kind]
    if kind == "upgrade":
        if version and not RELEASE.match(version):
            return None
        stmt = f"{stmt} {version}".strip()
    elif version:
        return None
    # PRISMA_ASKED_RECORDED (#588): whoever opened this door - the API, or a
    # prisma-operator member's VTY in the container - recorded the request
    # in the box's record of events, as the account that asked; the host's
    # VTY, root's, running it here must not record it again as root's.
    return detached(f"prisma-routing-api-{kind}", api_run_log(kind),
                    f"PRISMA_ASKED_RECORDED=1 /usr/bin/prismash -c '{stmt}'")


def detached(unit, log, body):
    """
    A transient unit `unit` running `body`, all of its output in `log`, its
    last line `prisma-api-exit <status>` (#558, #564).

    THE LOG'S DIRECTORY IS MADE BY THE UNIT ITSELF, as its first act, and the
    output redirected by its shell - not systemd's StandardOutput=truncate:.
    Measured on 10.40.200.166 (0.149.28): /run/prisma-routing/api did not
    exist on a freshly rebooted box (the API makes it only when it first
    saves its history), systemd could not open the log - "Failed at step
    STDOUT spawning /bin/sh: No such file or directory", status 209 - and
    systemd-run had already returned 0: the API waited for a log that would
    never come and the client met DEADLINE_EXCEEDED after 180 s, nothing
    restarted.
    """
    d = os.path.dirname(log)
    return ("systemd-run", "--collect", f"--unit={unit}",
            "/bin/sh", "-c",
            f"install -d -m 0755 {d} || exit 1; exec >{log} 2>&1; "
            f"{body}; echo \"prisma-api-exit $?\"")


#: The host's Linux interfaces, read (#540): one line each, with the VRF
#: table of a VRF device and of a VRF's member on it - what ip_links() reads.
LINKS_ARGV = ("ip", "-d", "-o", "link", "show")

#: The doors that take no argument: the argv is the whole shape.
FIXED = {
    ("sshd", "-T"): "read sshd's effective configuration",
    ("sshd", "-t"): "check sshd's configuration",
    ("systemctl", "reload", "ssh"): "reload sshd",
    ("systemctl", "is-active", "--quiet", "snmpd"): "ask whether snmpd runs",
    ("systemctl", "disable", "--now", "snmpd"): "stop snmpd",
    ("systemctl", "enable", "snmpd"): "enable snmpd",
    ("systemctl", "restart", "snmpd"): "restart snmpd",
    # Whether a systemd runs the host at all (#587): the management
    # container has no /run/systemd/system of its own to look at.
    ("test", "-d", "/run/systemd/system"): "ask whether systemd runs the host",
    # `system reboot` and `system shutdown` (Shell.ask_systemd), and the
    # tuner the `system cpu` / `system memory` changes restart. Missed by the
    # first cut, whose suite read system.py only: a box with the daemon would
    # have refused root its own reboot. test_doors.py now reads prismash.py's
    # `self.sys._run` too.
    ("systemctl", "reboot"): "restart the box",
    # The operator group (#563), created by the first account granted it:
    # appliance/install.sh does not create it, being a medium-only file.
    OPERATOR_GROUPADD: "create the operator group",
    ("systemctl", "poweroff"): "shut the box down",
    ("systemctl", "restart", "prisma-routing-tune"): "recompute the tuning",
    # The management API's listener (#532): api-configure reads api.env,
    # which the VTY writes, and writes the socket on the management address.
    ("systemctl", "restart", "prisma-routing-api-config.service"):
        "apply the management API's settings",
    # snmpd's configuration, READ (#525). The management container does not
    # mount /etc/snmp, and the file carries the communities, so it is a
    # door and not a mount - and not a query: FIXED is root's, which is who
    # may see a community on the host too (the file is 0600 root there).
    ("cat", SNMPD_CONF): "read snmpd's configuration",
    # `system upgrade`'s last step (#528): the VTY on the new release.
    REFRESH_VTY_ARGV: "restart the VTY on the upgraded release",
}

#: What any account may ask of the host from the management plane (#448):
#: questions, not changes - which unit runs the dataplane, where this box's
#: ssh host keys came from, which driver DPDK would get. The VTY on the host
#: asked them of the host itself, whatever the account; from the container
#: the answers would be the container's, or nothing.
#: The upgrade CHECK (#563): which release this box runs and which is the
#: newest published - read by the host's VTY, because the management plane's
#: network ends at the backplane and cannot reach the releases (measured on
#: 0.149.25: "Temporary failure in name resolution" inside the container,
#: root included). A question, so any account may ask it; the upgrade itself
#: is a door of its own (api_run_argv), root's and prisma-operator's.
UPGRADE_CHECK_ARGV = ("/usr/bin/prismash", "-c", "system upgrade")

#: bfdd's sessions, READ, for the SNMP agent's BFD-STD-MIB (#571). snmpd runs
#: the agent as Debian-snmp, and on a box running the containers FRR's .vty
#: sockets are frr:frr 0770 and /etc/frr/vtysh.conf frr:frr 0640 - measured
#: on 10.40.200.166 (0.150.10): `vtysh` as that user "failed to connect to
#: any daemons", so every BFD object answered as absent. The other way in,
#: membership of frr (or of frrvty, where the sockets are frrvty's), is
#: vtysh's whole CLI, configuration included: snmpd able to rewrite routing.
#: These two questions are what the agent reads, at vtysh's installed path,
#: and nothing else vtysh can be asked is a door. The agent builds the same
#: argv (appliance/prisma-routing-snmp-agent, BFD_READS); test_doors.py holds
#: the two equal.
VTYSH_HOST = "/usr/bin/vtysh"
BFD_PEERS_ARGV = (VTYSH_HOST, "-c", "show bfd peers json")
BFD_COUNTERS_ARGV = (VTYSH_HOST, "-c", "show bfd peers counters json")

QUERIES = {
    UPGRADE_CHECK_ARGV: "ask which release is the newest",
    BFD_PEERS_ARGV: "read bfdd's sessions",
    BFD_COUNTERS_ARGV: "read bfdd's session counters",
    ("systemctl", "is-active", "--quiet", "prisma-routing-containers.service"):
        "ask which unit runs the dataplane",
    ("/usr/lib/prisma-routing/ssh-host-key-origin",):
        "ask where the ssh host keys came from",
    ("/usr/lib/prisma-routing/bind-dataplane-nic", "--which-driver"):
        "ask which driver DPDK would get",
    # The container has a name of its own (#516).
    ("hostname",): "ask the box's name",
    # The Linux VRF devices (#540), which `show running-config` reads back
    # whoever asks: the container has no network to list them in. What any
    # account on the host reads with `ip link`.
    LINKS_ARGV: "list the Linux interfaces, VRFs and members",
}

def query_of(argv):
    """
    The question a shaped query `argv` asks, or None when it is none (#590).

    QUERIES above are whole argvs; this is the one question that takes an
    argument: a card's module EEPROM, read by the installed
    transceiver.py with the ioctl `ethtool -m` sends. The kernel wants
    CAP_NET_ADMIN for it (ETHTOOL_GMODULEEEPROM is not in the list ethtool's
    ioctl lets anyone ask), so the VTY in its container and the SNMP agent
    as Debian-snmp both ask the host; and what it gives back - the module's
    identity and levels - is what any account may read, as `show interface`
    is. The device is a netdev name and nothing else: the program, its
    interpreter and its verb are fixed.
    """
    if not isinstance(argv, (list, tuple)) or len(argv) != 4 or \
            not all(isinstance(a, str) for a in argv):
        return None
    dev = argv[3]
    if NETDEV.match(dev) and \
            tuple(argv) == transceiver.read_argv(dev, transceiver.PROGRAM):
        return "read a card's module EEPROM"
    return None


#: The longest a door may take. The VTY's own default is 30 seconds.
MAX_TIMEOUT = 120


def _account(name, root_ok=False):
    """True when `name` is an account name a door may be given."""
    return bool(ACCOUNT.match(name)) and (root_ok or name != "root")


def verb_of(argv, stdin=None):
    """
    The door `argv` (with `stdin`) is, or None when it is none of them.

    The whole check. A None is a refusal, and the daemon runs nothing it
    returned None for.
    """
    if not isinstance(argv, (list, tuple)) or \
            not all(isinstance(a, str) for a in argv):
        return None
    argv = tuple(argv)
    if stdin is not None and argv != ("chpasswd", "-e"):
        return None
    if argv in FIXED:
        return FIXED[argv]
    n = len(argv)
    if n == 3 and argv[:2] == ("userdel", "-r") and _account(argv[2]):
        return "remove an account"
    if n == 5 and argv[:3] == ("useradd", "-m", "-s") \
            and argv[3] == LOGIN_SHELL and _account(argv[4]):
        return "create an account"
    if n == 4 and argv[:2] == ("usermod", "-s") and argv[2] == LOGIN_SHELL \
            and _account(argv[3]):
        return "set an account's shell"
    if n == 4 and argv[0] == "gpasswd" and argv[1] in ("-a", "-d") \
            and _account(argv[2]) and argv[3] in MANAGED_GROUPS:
        return "change an account's groups"
    if argv == ("chpasswd", "-e") and isinstance(stdin, str) \
            and CHPASSWD_LINE.match(stdin):
        return "set a password"
    if n == 6 and argv[:4] == ("ip", "link", "set", "dev") \
            and NETDEV.match(argv[4]) and argv[5] in ("up", "down"):
        return "set a Linux interface up or down"
    if n == 7 and argv[:4] == ("ip", "link", "set", "dev") \
            and NETDEV.match(argv[4]) and argv[5] == "mtu" \
            and is_number(argv[6]) and 68 <= int(argv[6]) <= 65535:
        return "set a Linux interface's MTU"
    if n == 7 and argv[:4] == ("ip", "link", "set", "dev") \
            and NETDEV.match(argv[4]) and argv[5] == "alias" \
            and len(argv[6]) <= ALIAS_MAX and "\n" not in argv[6] \
            and "\0" not in argv[6]:
        return "set a Linux interface's description"
    if n == 6 and argv[:5] == ("ip", "-o", "link", "show", "dev") \
            and NETDEV.match(argv[5]):
        return "read a Linux interface"
    if n == 2 and argv[0] == SET_HOSTNAME and hostname_error(argv[1]) is None:
        return "rename the box"
    if n == 8 and argv[:5] == ("ip", "route", "replace", "default", "via") \
            and _address(argv[5]) and argv[6] == "dev" \
            and NETDEV.match(argv[7]):
        return "put the management default route back"
    # The same, into management's own VRF table (#580).
    if n == 10 and argv[:5] == ("ip", "route", "replace", "default", "via") \
            and _address(argv[5]) and argv[6] == "dev" \
            and NETDEV.match(argv[7]) and argv[8] == "table" \
            and mgmt_table_ok(argv[9]):
        return "put the management default route back, in its VRF"
    # Management in a VRF of its own (#580): its rules and sysctls, the
    # exact argvs management_argvs() builds.
    if management_door(argv):
        return "put management in its own VRF"
    # A tenant VRF's device and its members (#540). Every name a VRF's is
    # the statement's own predicate, which refuses prisma-*: the backplane's
    # VRF is not reachable through these by its name. What a name cannot
    # say - that a device IS a VRF, that a TAP is in no bridge - guard_of()
    # asks the host before the door opens.
    if n == 8 and argv[:3] == ("ip", "link", "add") \
            and argv[4:7] == ("type", "vrf", "table") \
            and vrf_device_error(argv[3], argv[7]) is None:
        return "make a VRF device"
    if n == 5 and argv[:4] == ("ip", "link", "del", "dev") \
            and vrf_device_error(argv[4], 1) is None:
        return "remove a VRF device"
    if n == 7 and argv[:4] == ("ip", "link", "set", "dev") \
            and NETDEV.match(argv[4]) and argv[5] == "master" \
            and vrf_device_error(argv[6], 1) is None:
        return "put a Linux interface in a VRF"
    if n == 6 and argv[:4] == ("ip", "link", "set", "dev") \
            and NETDEV.match(argv[4]) and argv[5] == "nomaster":
        return "take a Linux interface out of its VRF"
    # The dataplane restart as a unit of its own (#564): the one shape
    # dataplane_restart_argv() builds, with the installed tool and log only.
    if argv == dataplane_restart_argv():
        return "restart the dataplane as a unit of its own"
    # The API's upgrade and dataplane restart (#558, phase 5), detached.
    for kind in API_RUNS:
        stmt = argv[-1] if argv else ""
        version = ""
        if kind == "upgrade":
            m = re.search(r"system upgrade ?([0-9.]*)'", stmt)
            version = m.group(1) if m else ""
        if api_run_argv(kind, version) == argv:
            return f"start '{API_RUNS[kind]}' for the API"
    return None


def ip_links(text):
    """
    {name: {"master", "vrf", "vrf_slave"}} from `ip -d -o link show`: the
    device a link is enslaved to, the table it is a VRF of, and the table
    of the VRF it is a member of - None where it has none (#540).
    """
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"^\d+: ([^:@\s]+)(?:@\S+)?: ", line)
        if not m:
            continue
        master = re.search(r" master (\S+) ", line)
        vrf = re.search(r"\\\s+vrf table (\d+)\b", line)
        slave = re.search(r"\bvrf_slave table (\d+)\b", line)
        out[m.group(1)] = {
            "master": master.group(1) if master else None,
            "vrf": int(vrf.group(1)) if vrf else None,
            "vrf_slave": int(slave.group(1)) if slave else None}
    return out


def _link(name, execute):
    """ip_links() of the one device `name`, or None when there is none."""
    rc, out = execute(["ip", "-d", "-o", "link", "show", "dev", name],
                      None, 10)
    return ip_links(out).get(name) if rc == 0 else None


def guard_of(argv, execute=None):
    """
    Why the host refuses the door `argv` as it stands now, or None (#540).

    The VRF doors' second half. A name says what a device is called, not
    what it is: `ip link del dev eth0` passes any check of the words, and
    the kernel deletes whatever has the name - `type vrf` on the same line
    is not a filter, measured on Debian's 6.1. So the host is asked: only a
    VRF is removed, a member is taken out only of a VRF, and nothing goes
    in or out of the box's own (prisma-bp) or out of a bridge.
    """
    argv = tuple(argv)
    execute = execute or run_locally
    if argv[:4] == ("ip", "link", "del", "dev"):
        dev = _link(argv[4], execute)
        if dev is None or dev["vrf"] is None:
            return f"{argv[4]} is not a VRF device"
        return None
    if argv[:4] != ("ip", "link", "set", "dev") or len(argv) < 6 \
            or argv[5] not in ("master", "nomaster"):
        return None
    name = argv[4]
    if name.startswith(VRF_RESERVED_PREFIX):
        return f"{name} is the box's own"
    dev = _link(name, execute)
    if dev is None:
        return f"{name}: the host has no such device"
    if dev["vrf"] is not None:
        return f"{name} is a VRF device, not a member of one"
    held = dev["master"]
    if held and (dev["vrf_slave"] is None
                 or held.startswith(VRF_RESERVED_PREFIX)):
        return f"{name} is in {held}, which is not a tenant VRF"
    if argv[5] == "nomaster":
        return None if held else f"{name} is in no VRF"
    vrf = _link(argv[6], execute)
    if vrf is None or vrf["vrf"] is None:
        return f"{argv[6]} is not a VRF device"
    return None


def _address(text):
    """True when `text` is an IPv4 or IPv6 address and nothing else."""
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


#: The files a door writes, and HOW: each (operation, path) the daemon takes,
#: with the mode and the atomicity it decides. The caller names a path and
#: sends the text; it cannot choose another path, and cannot choose the mode.
#: They are what system.py set before the doors (test_doors.py drives it).
SNMP_V3_STORE = "/var/lib/snmp/snmpd.conf"
FILE_DOORS = {
    ("write", RESOLV_CONF): {"mode": 0o644, "atomic": True},
    ("write", SSHD_DROPIN): {"mode": None, "atomic": False},
    ("write", SNMPD_CONF): {"mode": 0o600, "atomic": False},
    ("append", SNMP_V3_STORE): {"mode": 0o600},
    ("unlink", RESOLV_BEFORE): {},
    ("unlink", SSHD_DROPIN): {},
    ("unlink", SNMPD_CONF): {},
    ("copy", RESOLV_CONF): {"dst": RESOLV_BEFORE},
    ("mkdir", "/run/sshd"): {},
}

#: The home an authorized_keys door writes under: root's, or an account's.
HOME = re.compile(r"^/(root|home/[A-Za-z0-9_][A-Za-z0-9_.-]{0,31})$")


def file_verb(req):
    """
    The file door `req` is, as (operation, path, how), or None.

    `req` is the request's dict: {"file": operation, "path": ..., ...}.
    """
    op, path = req.get("file"), req.get("path")
    if not isinstance(op, str) or not isinstance(path, str):
        return None
    if op == "keys":
        return (op, path, {}) if HOME.match(path) and ".." not in path \
            else None
    how = FILE_DOORS.get((op, path))
    if how is None:
        return None
    if op == "copy" and req.get("dst") != how["dst"]:
        return None
    if op in ("write", "append", "keys") and \
            not isinstance(req.get("text"), str):
        return None
    return op, path, how


def do_file(req, how, files=None):
    """The file door carried out, as `how` says. Raises OSError."""
    files = files or LocalFiles()
    op, path = req["file"], req["path"]
    if op == "write":
        files.write(path, req["text"], mode=how["mode"], atomic=how["atomic"])
    elif op == "append":
        files.append(path, req["text"], mode=how["mode"])
    elif op == "unlink":
        files.unlink(path)
    elif op == "copy":
        files.copy(path, how["dst"])
    elif op == "mkdir":
        files.mkdir(path)
    elif op == "keys":
        files.write_keys(path, req["text"])


class DoorFiles:
    """
    system.LocalFiles, through the door at `path`: each write is sent to the
    daemon, and a refusal or a failure comes back as the OSError the local
    write would have raised. With no daemon listening, the local write.
    """

    def __init__(self, path=SOCKET):
        self.path = path

    def _ask(self, req, local):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(self.path)
        except OSError:
            s.close()
            return local()
        try:
            s.settimeout(30)
            s.sendall(json.dumps(req).encode() + b"\n")
            r = json.loads(_read_line(s))
        except (OSError, ValueError) as e:
            raise OSError(errno.EIO, f"the host's door did not answer: {e}")
        finally:
            s.close()
        if r.get("rc") != 0:
            raise OSError(errno.EPERM, str(r.get("out", "refused")))

    def write(self, path, text, mode=None, atomic=False):
        self._ask({"file": "write", "path": path, "text": text},
                  lambda: LocalFiles().write(path, text, mode, atomic))

    def append(self, path, text, mode=None):
        self._ask({"file": "append", "path": path, "text": text},
                  lambda: LocalFiles().append(path, text, mode))

    def unlink(self, path):
        self._ask({"file": "unlink", "path": path},
                  lambda: LocalFiles().unlink(path))

    def copy(self, src, dst):
        self._ask({"file": "copy", "path": src, "dst": dst},
                  lambda: LocalFiles().copy(src, dst))

    def mkdir(self, path):
        self._ask({"file": "mkdir", "path": path},
                  lambda: LocalFiles().mkdir(path))

    def write_keys(self, home, text):
        self._ask({"file": "keys", "path": home, "text": text},
                  lambda: LocalFiles().write_keys(home, text))


def run_locally(argv, stdin=None, timeout=30):
    """The command run here, as the VTY ran it before the doors. A timeout
    of None is no limit: a VTY session lasts as long as the operator."""
    try:
        p = subprocess.run(list(argv), capture_output=True, text=True,
                           input=stdin, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"{argv[0]}: timed out"


def runner(path=SOCKET):
    """
    A `runner` for System and SnmpDaemon that goes through the door at
    `path`, or runs the command itself when nothing listens there.
    """
    def run(argv, stdin=None, timeout=30):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(path)
        except OSError:
            s.close()
            return run_locally(argv, stdin, timeout)
        try:
            s.settimeout(min(timeout, MAX_TIMEOUT) + 10)
            s.sendall(json.dumps({"argv": list(argv), "stdin": stdin,
                                  "timeout": timeout}).encode() + b"\n")
            reply = _read_line(s)
            r = json.loads(reply)
            return int(r["rc"]), str(r["out"])
        except (OSError, ValueError, KeyError, TypeError) as e:
            return 125, f"{argv[0]}: the host's door did not answer: {e}"
        finally:
            s.close()
    # Where the answers come from when something listens there: the
    # SnmpDaemon asks, before it believes a file the host says is missing,
    # whether it was the host that said so (#525).
    run.door = path
    return run


def _read_line(s, limit=1 << 20):
    """One newline-terminated message off `s`, at most `limit` bytes."""
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
        if len(buf) > limit:
            raise ValueError("message too long")
    return buf.decode()


def peer_uid(conn):
    """The uid of the process at the other end of `conn`, from the kernel."""
    cred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                           struct.calcsize("3i"))
    return struct.unpack("3i", cred)[1]


#: The two FIXED doors a prisma-operator member may open besides root (#563);
#: the API's detached upgrade and dataplane restart, and the dataplane restart
#: as a unit of its own (#564), are the others.
def system_action_argv(verb):
    """
    `systemctl reboot` / `systemctl poweroff`, as the VTY's ask_systemd()
    sends it and as OPERATOR_FIXED accepts it - one builder for both (#564):
    three releases in a row shipped an argv one side built and the other
    did not know.
    """
    return ("systemctl", verb)


OPERATOR_FIXED = (system_action_argv("reboot"), system_action_argv("poweroff"))


def operator_door(argv):
    """
    True when `argv` is one of the system actions (#563) - reboot, poweroff,
    and the detached `system upgrade [<version>]` / `system dataplane
    restart` - which root and prisma-operator's members may open.
    """
    if not isinstance(argv, (list, tuple)):
        return False
    t = tuple(argv)
    if t in OPERATOR_FIXED or t == dataplane_restart_argv():
        return True
    for kind in API_RUNS:
        version = ""
        if kind == "upgrade" and t:
            m = re.search(r"system upgrade ?([0-9.]*)'", t[-1])
            version = m.group(1) if m else ""
        if api_run_argv(kind, version) == t:
            return True
    return False


def account_is_operator(uid):
    """
    Is the account `uid` a member of prisma-operator, as THIS host's group
    file says at this instant (#563)? Read per request and never cached, so
    the system's own deprovisioning takes effect on the next request; an
    account the system cannot describe is not one.
    """
    try:
        import grp
        import pwd
        pw = pwd.getpwuid(uid)
        g = grp.getgrnam(GROUP_OPERATOR)
    except (KeyError, ImportError):
        return False
    return pw.pw_name in g.gr_mem or pw.pw_gid == g.gr_gid


def answer(request, uid, allowed_uids=(0,), execute=run_locally, files=None,
           is_operator=account_is_operator):
    """
    What the daemon replies to `request` from `uid`: {"rc", "out"}.

    Separate from the socket so the decision is tested on its own. 126 is
    the refusal, the shell's own "found and not permitted".
    """
    try:
        r = json.loads(request)
        if isinstance(r, dict) and "file" in r:
            return answer_file(r, uid, allowed_uids, files)
        if isinstance(r, dict) and "session" in r:
            return answer_session(r, uid, execute)
        if isinstance(r, dict) and ("event" in r or "events" in r):
            return answer_event(r, uid, allowed_uids, is_operator)
        argv, stdin = r["argv"], r.get("stdin")
        timeout = r.get("timeout", 30)
    except (ValueError, KeyError, TypeError):
        return {"rc": 126, "out": "the host's door: not a request"}
    if stdin is None and isinstance(argv, list) and \
            (tuple(argv) in QUERIES or query_of(argv)):
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            timeout = 30
        rc, out = execute(argv, None, min(timeout, MAX_TIMEOUT))
        return {"rc": rc, "out": out}
    verb = verb_of(argv, stdin)
    if verb is None:
        return {"rc": 126, "out": "the host's door: not an operation the "
                                  "VTY is allowed on the host"}
    if uid not in allowed_uids:
        if not (operator_door(argv) and is_operator(uid)):
            who = "only root and prisma-operator's members may" \
                if operator_door(argv) else "only root may"
            return {"rc": 126, "out": f"the host's door: {verb}: {who}"}
    why = guard_of(argv, execute)
    if why:
        return {"rc": 126, "out": f"the host's door: {verb}: {why}"}
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        timeout = 30
    rc, out = execute(argv, stdin, min(timeout, MAX_TIMEOUT))
    return {"rc": rc, "out": out}


#: What a read of the events may filter on, and the type each takes (#588).
EVENT_FILTERS = {"last": int, "since": (int, float), "severity": str,
                 "source": str, "kind": str, "component": str}


def account_name(uid):
    """The host's name for `uid`, or "uid <n>" when it has none."""
    try:
        import pwd
        return pwd.getpwuid(uid).pw_name
    except (KeyError, ImportError):
        return f"uid {uid}"


#: Events an account that is not root may write a minute (#588). The record
#: rotates at a fixed size, so an account writing without end - read-only
#: ones included, since every account may write - would push the box's own
#: history out of it: an "unexpected" restart among it. A person's commits
#: come nowhere near this.
ACCOUNT_EVENTS_PER_MINUTE = 30
_account_writes = {}


def account_rate_ok(uid, now, writes=None, limit=None):
    """
    Whether `uid` may write one more event at `now`, and count it if so: at
    most ACCOUNT_EVENTS_PER_MINUTE in any 60 s (#588).
    """
    writes = _account_writes if writes is None else writes
    limit = ACCOUNT_EVENTS_PER_MINUTE if limit is None else limit
    recent = [t for t in writes.get(uid, ()) if now - t < 60]
    if len(recent) >= limit:
        writes[uid] = recent
        return False
    writes[uid] = recent + [now]
    return True


def answer_event(r, uid, allowed_uids=(0,), is_operator=account_is_operator,
                 path=None, name_of=account_name, now=None):
    """
    answer(), for the box's record of events (#588): {"events": filters}
    reads them, {"event": {...}} writes one.

    ANY ACCOUNT MAY DO BOTH, and that is the reason this is a door and not
    the management container's mount of /var/log/prisma-routing: the VTY in
    there runs as the account that logged in, the directory is root's 0750
    (it holds the CGNAT trail), and every account's commit is an event. What
    an account may write is narrower than root's (events.event_error): its
    own actions only, stamped with its name as the KERNEL reports it
    (SO_PEERCRED) and with the time the door received it - whatever the
    request said. A restart's "asked", which a later start takes as its
    cause, is written for an account only when it may restart things at
    all: prisma-operator, as the restart's own door decides (#563).
    """
    if "events" in r:
        f = r["events"]
        if not isinstance(f, dict) or any(
                k not in EVENT_FILTERS or not isinstance(v, EVENT_FILTERS[k])
                or isinstance(v, bool) for k, v in f.items()):
            return {"rc": 126, "out": "the host's door: not a reading of "
                                      "the events"}
        got = events.select_events(events.read_events(path), **f)
        return {"rc": 0, "out": json.dumps(got)}
    ev = r["event"]
    root = uid in allowed_uids
    if isinstance(ev, dict) and not root:
        ev = dict(ev, who=name_of(uid),
                  t=round(time.time() if now is None else now, 3))
    why = events.event_error(ev, privileged=root)
    if why is None and not root and ev.get("kind") == "asked" \
            and ev.get("affects") and not is_operator(uid):
        why = "only root and prisma-operator's members ask for a restart"
    if why is None and not root and not account_rate_ok(
            uid, time.time() if now is None else now):
        why = (f"more than {ACCOUNT_EVENTS_PER_MINUTE} a minute from one "
               "account")
    if why:
        return {"rc": 126, "out": f"the host's door: an event: {why}"}
    if not events.append_event(ev, path):
        return {"rc": 1, "out": "the events could not be written"}
    return {"rc": 0, "out": ""}


def journaled(request):
    """
    Whether a request is said in the journal (say()). Not the events' own
    (#588): a write is in the record it writes, and the reads are every
    `show log`, every GetSystem a web page polls - which is how "door: uid
    1000: hostname -> 0" came to fill the journal every 2 s.
    """
    try:
        r = json.loads(request)
    except ValueError:
        return True
    return not (isinstance(r, dict) and ("event" in r or "events" in r))


def say(request, uid, reply):
    """
    One journal line per request: the argv, the caller's uid and the result.
    NEVER stdin, which is where chpasswd's hash travels.
    """
    try:
        r = json.loads(request)
        argv = r.get("argv")
        if "file" in r and argv is None:
            argv = [str(r.get("file")), str(r.get("path"))]
        if "session" in r and argv is None:
            # Never the command's text: `-c` can carry a password hash.
            kind = [k for k in ("attach", "command")
                    if isinstance(r["session"], dict) and k in r["session"]]
            argv = ["session"] + kind
    except (ValueError, AttributeError):
        argv = None
    words = " ".join(argv) if isinstance(argv, list) and \
        all(isinstance(a, str) for a in argv) else "<not a request>"
    print(f"door: uid {uid}: {words[:200]} -> {reply['rc']}", flush=True)


def answer_file(r, uid, allowed_uids=(0,), files=None):
    """answer(), for a file door: {"rc": 0} or a refusal or the error."""
    verb = file_verb(r)
    if verb is None:
        return {"rc": 126, "out": "the host's door: not a file the VTY "
                                  "writes on the host"}
    if uid not in allowed_uids:
        return {"rc": 126, "out": f"the host's door: {verb[0]} {verb[1]}: "
                                  "only root may"}
    try:
        do_file(r, verb[2], files)
    except OSError as e:
        return {"rc": 1, "out": f"{verb[1]}: {e.strerror or e}"}
    return {"rc": 0, "out": ""}


#: The container the VTY runs in (#448, phase C), as docker-compose 1.29 names
#: it on the box.
MGMT_CONTAINER = "prisma-routing_mgmt_1"

#: A broker's socket, as prisma-vty-broker makes it under the login's runtime
#: directory. The uid in the path must be the caller's own.
SESSION_SOCKET = re.compile(r"^/run/user/(\d+)/prisma-vty-[A-Za-z0-9_]+/vty\.sock$")

#: Where a login with no runtime directory of its own puts it: a sticky
#: directory (tmpfiles.d, 1733) the management plane shares. The path names
#: no uid, so the owner of the socket AND of the directory it is in must be
#: the caller - which mkdtemp in a sticky directory guarantees and nobody
#: else can arrange.
VTY_RUN_DIR = "/run/prisma-routing/vty"

#: A command a session runs with -c (`ssh box 'show version'`): one line.
SESSION_COMMAND_MAX = 4096


def session_argv(req, uid, pw=None, groups=None):
    """
    The `docker exec` a session door runs, or (None, why).

    NOT A DOOR THAT GRANTS ANYTHING. It is the door every login takes into
    the management plane, so it runs for every uid - and what it runs is the
    VTY as THAT uid: `-u <uid>:<gid>` and `--group-add` each group the account
    has on the host, read off the host's own files and not from the request.
    The VTY decides the role from those groups, as it does on the host, and
    whatever it then asks of the host's other doors is decided by the same
    uid, which the kernel reports. A read-only account in the container is
    exactly a read-only account on the host.
    """
    s = req.get("session")
    if not isinstance(s, dict):
        return None, "not a session"
    if pw is None:
        import pwd
        try:
            pw = pwd.getpwuid(uid)
        except KeyError:
            return None, f"uid {uid} has no account on the host"
    if groups is None:
        groups = os.getgrouplist(pw.pw_name, pw.pw_gid)
    if "attach" in s:
        path = s["attach"]
        import stat
        m = SESSION_SOCKET.match(path) if isinstance(path, str) else None
        shared = isinstance(path, str) and re.match(
            "^" + re.escape(VTY_RUN_DIR) + r"/prisma-vty-[A-Za-z0-9_]+/vty\.sock$",
            path)
        if not shared and (not m or int(m.group(1)) != uid):
            return None, "a session's socket is under the caller's own /run/user"
        try:
            st = os.lstat(path)
            dst = os.lstat(os.path.dirname(path))
        except OSError:
            return None, "no such socket"
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid != uid:
            return None, "not the caller's own socket"
        if not stat.S_ISDIR(dst.st_mode) or dst.st_uid != uid:
            return None, "not the caller's own directory"
        vty = ["prismash", "--attach", path]
    elif "command" in s:
        cmd = s["command"]
        if not isinstance(cmd, str) or "\n" in cmd or "\0" in cmd \
                or len(cmd) > SESSION_COMMAND_MAX:
            return None, "a session's command is one line"
        vty = ["prismash", "-c", cmd]
    else:
        return None, "a session attaches a terminal or runs a command"
    argv = ["docker", "exec", "-u", f"{uid}:{pw.pw_gid}"]
    for g in sorted(set(groups) - {pw.pw_gid}):
        argv += ["--group-add", str(g)]
    argv += ["-e", f"HOME={pw.pw_dir}", "-e", f"USER={pw.pw_name}",
             "-e", f"LOGNAME={pw.pw_name}"]
    # Where the login came from (#588), for the box's record of events: the
    # caller's word, so only when it is an address and nothing else - it
    # lands in an environment variable, and an event's "from".
    frm = s.get("from")
    if isinstance(frm, str) and _address(frm):
        argv += ["-e", f"PRISMA_SESSION_FROM={frm}"]
    argv += [MGMT_CONTAINER] + vty
    return argv, ""


def mgmt_running(execute=run_locally):
    """Is the management plane's container running? Asked of docker."""
    rc, out = execute(["docker", "inspect", "-f", "{{.State.Running}}",
                       MGMT_CONTAINER], None, 10)
    return rc == 0 and out.strip() == "true"


def answer_session(r, uid, execute=run_locally):
    """
    answer(), for a session door: the VTY run in the management plane as the
    caller, and its exit status. 125 means there is no management plane to
    run it in, which the client takes as "run it here", as before phase C.
    """
    argv, why = session_argv(r, uid)
    if argv is None:
        return {"rc": 126, "out": f"the host's door: {why}"}
    if not mgmt_running(execute):
        return {"rc": 125, "out": "the management plane is not running"}
    rc, out = execute(argv, None, None)
    return {"rc": rc, "out": out if "command" in r["session"] else ""}


def serve(path=SOCKET, allowed_uids=(0,), execute=run_locally, ready=None):
    """
    Listen on `path` and answer every connection, one thread each.

    The socket is 0666: who may use a door is decided per request from
    SO_PEERCRED, which a mode cannot express and a caller cannot forge.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    os.chmod(path, 0o666)
    srv.listen(16)
    if ready:
        ready.set()

    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn, allowed_uids, execute),
                         daemon=True).start()


#: The probes a door runs (#448, phase C), as prismash.probe_argv() builds
#: them and nothing else: the program at its fixed path on the host, the
#: flags in their order, a count in range, an optional source address, and
#: a host prismash would take. From the management plane these are the only
#: way out - its network ends at the backplane.
PROBE_SHAPES = {
    "/usr/bin/ping": (["-c", "N", "-n"], "-I"),
    "/usr/bin/traceroute": (["-q", "1", "-m", "N", "-n"], "-s"),
    "/usr/bin/mtr": (["--report", "--report-cycles", "N", "-n"], "-a"),
}


def probe_ok(argv):
    """True when `argv` is a probe prismash.probe_argv() would build."""
    if not isinstance(argv, list) or not argv \
            or not all(isinstance(a, str) for a in argv):
        return False
    shape = PROBE_SHAPES.get(argv[0])
    if shape is None:
        return False
    fixed, src_flag = shape
    rest = argv[1:]
    if len(rest) < len(fixed) + 1:
        return False
    for want, got in zip(fixed, rest):
        if want == "N":
            if not is_number(got) or not 1 <= int(got) <= PROBE_COUNT_MAX:
                return False
        elif want != got:
            return False
    tail = rest[len(fixed):]
    if len(tail) == 3 and tail[0] == src_flag and probe_source_ok(tail[1]):
        tail = tail[2:]
    return len(tail) == 1 and probe_host_ok(tail[0])


def run_chunks(argv, popen=subprocess.Popen, deadline=None, ticks=False):
    """
    Run a probe here and yield its output as it comes: ("out", text) per
    chunk, then ("rc", status) - 124 when the deadline stopped it. Closing
    the generator early kills the probe, which is how a caller that went away
    stops it. stream_probe() sends these down the door's socket; the API's
    Ping/Traceroute/Mtr (#558, phase 7) read them directly on a box with no
    door between the account and the tool.

    `ticks`: also ("tick", None) every half second the tool is quiet, so a
    caller can notice it was cancelled while ping waits on a host that does
    not answer.
    """
    import select
    import time
    deadline = PROBE_DEADLINE if deadline is None else deadline
    p = popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    start = time.time()
    rc = None
    try:
        while True:
            if time.time() - start > deadline:
                p.kill()
                rc = 124
                break
            r, _, _ = select.select([p.stdout], [], [], 0.5)
            if r:
                chunk = os.read(p.stdout.fileno(), 4096)
                if not chunk:
                    rc = p.wait()
                    break
                yield ("out", chunk.decode(errors="replace"))
            elif p.poll() is not None:
                continue
            elif ticks:
                yield ("tick", None)
        yield ("rc", rc)
    finally:
        if p.poll() is None:
            p.kill()
        p.wait()


def stream_probe(conn, argv, popen=subprocess.Popen, deadline=None):
    """
    Run a probe and send its output down `conn` as it comes, one JSON line
    per chunk ({"out": ...}), then {"rc": ...}. A probe's value is arriving
    row by row, which is why this door streams and the others do not.

    A caller that goes away - Ctrl-C at the VTY closes the socket - stops
    the probe: the write fails and the process is killed. So does the
    deadline, the one prismash gives a probe run here.
    """
    rc = None
    chunks = run_chunks(argv, popen=popen, deadline=deadline)
    try:
        for kind, value in chunks:
            if kind == "out":
                conn.sendall(json.dumps({"out": value}).encode() + b"\n")
            else:
                rc = value
        conn.sendall(json.dumps({"rc": rc}).encode() + b"\n")
    except OSError:
        chunks.close()
        rc = 130
    return rc


def probe_chunks(argv, path=SOCKET, ticks=False):
    """
    A probe through the host's door, as it arrives: ("out", text) per chunk,
    then ("rc", status, refusal) - refusal the door's words when it would not
    run the probe, else "". Closing the generator closes the connection,
    which is what stops the probe on the host. `ticks` as run_chunks().
    """
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(path)
        s.sendall(json.dumps({"probe": list(argv)}).encode() + b"\n")
        if ticks:
            s.settimeout(0.5)
        buf = b""
        while True:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                yield ("tick", None)
                continue
            if not chunk:
                yield ("rc", 125, "the host's door went away")
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                m = json.loads(line)
                if "out" in m and "rc" not in m:
                    yield ("out", m["out"])
                elif "rc" in m:
                    yield ("rc", m["rc"], m.get("out", "") if m["rc"] == 126 else "")
                    return
    finally:
        s.close()


def probe(argv, path=SOCKET, out=None):
    """
    A probe through the host's door, its output written to `out` as it
    arrives. (rc, refusal): refusal is the door's words when it would not
    run the probe, else "". Ctrl-C closes the connection, which is what
    stops the probe on the host.
    """
    out = out or sys.stdout
    chunks = probe_chunks(argv, path)
    try:
        for m in chunks:
            if m[0] == "out":
                out.write(m[1])
                out.flush()
            else:
                return m[1], m[2]
    finally:
        chunks.close()
    return 125, "the host's door went away"


def handle(conn, allowed_uids=(0,), execute=run_locally):
    """One connection: read the request, answer it, close. Never raises."""
    with conn:
        try:
            uid = peer_uid(conn)
            request = _read_line(conn)
            try:
                r = json.loads(request)
            except ValueError:
                r = None
            # A probe streams its answer, so it is answered here and not by
            # answer(). Any account may probe, as any account may at the VTY.
            if isinstance(r, dict) and "probe" in r:
                if probe_ok(r["probe"]):
                    rc = stream_probe(conn, r["probe"])
                    say(json.dumps({"argv": r["probe"]}), uid, {"rc": rc})
                else:
                    conn.sendall(json.dumps(
                        {"rc": 126, "out": "the host's door: not a probe the "
                                           "VTY runs"}).encode() + b"\n")
                return
            reply = answer(request, uid, allowed_uids, execute)
            conn.sendall(json.dumps(reply).encode() + b"\n")
            if journaled(request):
                say(request, uid, reply)
        except (OSError, ValueError):
            pass


# ------------------------------------------------- the accounts, shared

#: Where the host's account files are kept current for the management
#: container, which mounts /run/prisma-routing already.
ACCOUNTS_DIR = os.environ.get("PRISMA_ACCOUNTS_DIR", "/run/prisma-routing/accounts")
ACCOUNT_FILES = ("passwd", "group", "shadow")


def share_accounts(src="/etc", dst=ACCOUNTS_DIR):
    """
    Copy /etc/passwd, group and shadow into `dst`, each by rename and with its
    owner and mode, when its content or mode differs from the copy there; the
    names copied (#532).

    WHY A COPY AND NOT THE FILES. The management container bind-mounted the
    three FILES, and a bind mount of a file is a mount of its inode: useradd
    and usermod write a new file and rename it over the old one, so the
    container went on reading the inode from before. Measured on the X520
    bench (0.148.4): an account created after the container started was
    "name not found" to the API and to every VTY session in it, and a `save`
    from one wrote the configuration without it. A DIRECTORY bind mount sees
    a rename inside it, so the container's /etc/passwd is a link into this
    directory, and this keeps the directory current.
    """
    os.makedirs(dst, mode=0o755, exist_ok=True)
    changed = []
    for name in ACCOUNT_FILES:
        a, b = os.path.join(src, name), os.path.join(dst, name)
        try:
            with open(a, "rb") as f:
                data = f.read()
            st = os.stat(a)
        except OSError:
            continue
        try:
            with open(b, "rb") as f:
                same = f.read() == data
            same = same and (os.stat(b).st_mode & 0o7777) == (st.st_mode & 0o7777)
        except OSError:
            same = False
        if same:
            continue
        tmp = b + ".new"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except OSError:
            pass                # a test, not root: the mode still holds
        os.chmod(tmp, st.st_mode & 0o7777)
        os.replace(tmp, b)
        changed.append(name)
    return changed


def accounts_state(src="/etc"):
    """What says the account files changed: each one's inode and mtime."""
    out = []
    for name in ACCOUNT_FILES:
        try:
            st = os.stat(os.path.join(src, name))
            out.append((st.st_ino, st.st_mtime_ns))
        except OSError:
            out.append(None)
    return tuple(out)


# not a unit: the daemon's watcher loop - share_accounts() and
# accounts_state() are what test_doors.py drives.
def watch_accounts(src="/etc", dst=ACCOUNTS_DIR, interval=1.0):
    seen = None
    while True:
        now = accounts_state(src)
        if now != seen:
            try:
                share_accounts(src, dst)
                seen = now
            except OSError:
                pass        # tried again at the next tick
        time.sleep(interval)


if __name__ == "__main__":
    if sys.argv[1:] == ["--share-accounts"]:
        # prisma-routing-containers.service, before the container starts:
        # its /etc/passwd is a link into the copy, which must be there.
        share_accounts()
        sys.exit(0)
    if sys.argv[1:] != ["--serve"]:
        print("usage: doors.py --serve | --share-accounts", file=sys.stderr)
        sys.exit(2)
    threading.Thread(target=watch_accounts, daemon=True).start()
    serve()
