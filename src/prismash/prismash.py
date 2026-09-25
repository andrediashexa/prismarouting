#!/usr/bin/env python3
"""
prismash - the Prisma Routing VTY.

One CLI for routing and CGNAT, with a Juniper-style configuration model:
changes edit a *candidate*, `commit` applies it, and `rollback` returns to any
of the previous committed revisions.

Why not a daemon registered with vtysh: vtysh's daemon list is compiled in
(vtysh_client[]) with no plugin mechanism. Registering a new daemon would mean
maintaining a vtysh fork for the life of the product. Forwarding costs nothing
and does not pin us to an FRR release.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import ipaddress
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time

from config import (Config, Pool, URPF_MODES, UNCHECKED_MARK, UNCHECKED_NOTE,
                    CONSOLE_COLUMNS, VLAN_RANGE, vlan_tag_error,
                    MTU_MIN, MTU_MAX, MTU_RANGE,
                    IFACE_VRF_MAX, IFACE_VRF_RANGE,
                    is_number,
                    FRR_IFNAME_MAX, linux_ifname_error,
                    NAT_HIERARCHY, CGNAT_LEVEL, one_line_cgnat,
                    STATIC_BLOCK, FRR_BLOCK_CLOSERS,
                    ROUTING_HEADER,
                    as_hierarchy, group_static,
                    block_paths, block_of, frr_block_lines, opens_frr_block,
                    is_static_route, static_route, static_route_key,
                    _address_family_error, _is_loopback, _is_lcp_tap,
                    prefix_error, prefix_why, address_error,
                    text_error, text_why,
                    mac_error, mac_normal,
                    asn_error, asn_why, asn_note,
                    global_key, ordered_globals,
                    VRID_MIN, VRID_MAX, VRRP_PRIORITY_MIN, VRRP_PRIORITY_MAX,
                    VRRP_PRIORITY_DEFAULT,
                    VRRP_DECREMENT_MIN, VRRP_DECREMENT_MAX,
                    vrrp_statement, vrrp_remove, vrrp_pair_findings)
from system import (System, SnmpDaemon, SnmpUser, User, dns_error, DNS_MAX,
                    can_anyone_log_in, can_anyone_configure, redact,
                    identity_redact,
                    snmp_redact, validate_snmp, validate_ssh, validate_role,
                    SSH_FIELDS, GROUP_ADMIN, GROUP_CGNAT,
                    ROLE_ADMIN, ROLE_CGNAT)
import routes
import vppapi

readline = None

CONFIG_PATH = os.environ.get("PRISMA_CONFIG", "/etc/prisma-routing/prisma-routing.conf")
# A static route's description (#436). FRR has no field for one, so the box
# keeps it here, written by commit and read back by running_config(), beside
# the saved configuration and under the same umask.
STATIC_DESC_PATH = os.environ.get(
    "PRISMA_STATIC_DESC", "/etc/prisma-routing/static-route-descriptions")
ROLLBACK_DIR = os.environ.get("PRISMA_ROLLBACK", "/etc/prisma-routing/rollback")
ROLLBACK_KEEP = 20
# Where a "commit confirmed" waits for its confirmation.
#
# A file, in the same directory and with the same group, because the deadline
# has to outlive the session that set it - see ConfirmPending (#212).
CONFIRM_PATH = os.environ.get("PRISMA_CONFIRM",
                              "/etc/prisma-routing/confirm-pending")
# The peer's saved configuration, as a file on THIS box.
#
# A file rather than a protocol because this box has no way to ask the other
# one anything: VRRP is not implemented (#7), so there is no session between
# the two and nothing to carry a request over. What does exist is a saved
# configuration - `save` writes one, `--load` reads one at boot, and every
# rollback revision is one - so the peer's configuration is an artefact this
# product already produces, and copying it here is a step an operator can
# perform today with what they have.
#
# The seam is deliberate and is the whole of what #8 can honestly deliver on
# its own: the COMPARISON is complete and tested, and whatever ends up
# carrying the peer's configuration writes this file. Until something does,
# the file is whatever was last copied to it, which is why every report that
# reads it prints its date beside the digest: a stale copy that reads as a
# match is the same silent agreement this check exists to remove.
PEER_CONFIG_PATH = os.environ.get("PRISMA_PEER_CONFIG",
                                  "/etc/prisma-routing/peer.conf")
# How many times the box may fail to put the old configuration back before it
# stops trying and says so.
#
# Not unlimited: the unit that does this runs every few seconds, and a revert
# that cannot succeed would otherwise print its failure onto the console
# forever - which is the same as printing nothing, because nobody reads it.
# Not one either: the first attempt after a reboot can lose a race with a
# dataplane that is still starting, and giving up on that would turn a boot
# into a box that stayed broken.
CONFIRM_ATTEMPTS = 5
# What the VTY may do to the mode of the configuration store, which is:
# create it no wider than the group, and nothing else.
#
# The mode has ONE owner and it is appliance/install.sh - 2770 on the
# directories and 0660 on the files, group prisma-cgnat. It has to be the
# installer, because the rest of this is ownership: a mode without the group
# grants nobody anything, the setgid bit is what puts that group on what gets
# created later, and a non-root operator can do neither to a directory it does
# not own. The VTY can only ever damage that arrangement, which is what it did
# - chmod 0700 here made the first save or commit by ANYBODY, root at boot
# included, the last one a non-root operator could follow (#188).
#
# 0007 rather than a mode argument: it covers the directory and the file and
# every path makedirs() creates on the way, and what it guarantees is the half
# that must never slip - no world access to a file carrying every password
# hash on the box.
CONFIG_UMASK = 0o007
# The program that answers whether this box's ssh host keys were made on it.
#
# NOT a rule written out again here. appliance/ssh-host-key-origin holds the
# decision, the key names and the three commands that repair it, and its
# header carries the whole argument. This VTY is its second reader, and the
# reason there is a second one: the first is the boot self-test, whose report
# goes to /dev/console once at boot and not to the journal, so an operator who
# reached a racked box over ssh has no way to see it at all - and that
# operator's session is the one the answer is about (#248).
SSH_HOST_KEY_ORIGIN = os.environ.get("PRISMA_SSH_HOST_KEY_ORIGIN",
                                     "/usr/lib/prisma-routing/"
                                     "ssh-host-key-origin")
VPPCTL = os.environ.get("VPPCTL", "vppctl")

# The advertisement interval, in centiseconds: RFC 5798's default of one
# second. Not configurable, and that is a decision rather than an omission -
# the interval MUST be the same on both boxes or the election never settles,
# and a per-box statement is a per-box way to get it wrong. A pair that needs
# a faster takeover than three and a half seconds wants BFD, which is a
# different mechanism and a different issue.
VRRP_INTERVAL_CS = 100

#: What VPP's four VRRP states mean, in the words an operator uses - the
#: dataplane's own spelling is VRRP_API_VR_STATE_MASTER and nobody says that.
#:
#: A module-level TUPLE beside the mapping, and not only the mapping's values,
#: because a screen that prints one of these is a screen whose width can be
#: bounded: test_prismash_refusals.py reads the product's closed sets off the
#: module and a dict gives it the keys, which are the names nobody prints.
VRRP_STATE_WORDS = ("init (not started)", "backup",
                    "master, holding the address", "the interface is down")
VRRP_STATE_SAYS = {
    "VRRP_API_VR_STATE_INIT": VRRP_STATE_WORDS[0],
    "VRRP_API_VR_STATE_BACKUP": VRRP_STATE_WORDS[1],
    "VRRP_API_VR_STATE_MASTER": VRRP_STATE_WORDS[2],
    "VRRP_API_VR_STATE_INTF_DOWN": VRRP_STATE_WORDS[3],
}
VTYSH = os.environ.get("VTYSH", "vtysh")
FRR_RELOAD = os.environ.get("FRR_RELOAD", "/usr/lib/frr/frr-reload.py")

# The three reachability tools, each with the package that carries it (#395).
#
# NAMED WITH THEIR PACKAGE because the refusal an operator meets when one is
# missing has to name what to install, and this is the only place that pairing
# is written down: appliance/build-iso.sh installs these three and the boot
# self-test reads this table back, so a tool dropped from the image cannot go
# unnoticed on one side and promised on the other.
#
# Full paths, and no $PATH lookup. The argument vector this VTY builds is
# handed to execve through subprocess with no shell anywhere in it, so nothing
# an operator types is ever a word a shell could read; a tool found by
# searching a PATH an environment could rewrite would give that back.
PROBE_TOOLS = {
    "ping": (os.environ.get("PRISMA_PING", "/usr/bin/ping"), "iputils-ping"),
    "traceroute": (os.environ.get("PRISMA_TRACEROUTE", "/usr/bin/traceroute"),
                   "traceroute"),
    "mtr": (os.environ.get("PRISMA_MTR", "/usr/bin/mtr"), "mtr-tiny"),
}

# What a probe may be asked to do, and the ceiling on each.
#
# BOUNDED BY DEFAULT, and that is the whole argument for these numbers. A
# `ping` that runs until it is interrupted, typed on a serial console by
# somebody who then walks away, leaves a console that cannot be used for
# anything else until somebody walks back to it - and the console is where
# this box is configured when it is new. Every probe here stops by itself.
#
# The ceiling is what an operator may raise the count to. It is not a
# judgement about how many packets are useful; it is the point past which a
# probe stops being a question and becomes a session.
PROBE_COUNT_DEFAULT = 5
PROBE_COUNT_MAX = 100
# The wall clock each probe gets, whatever it was asked for. A tool that hangs
# - a DNS server that never answers, an mtr against a host that black-holes
# every cycle - must not take the VTY with it.
PROBE_DEADLINE = 300

# Which FRR daemon serves each "router" statement. Written as data rather than
# guessed from the keyword, because the two only look alike: "router ospf" is
# served by ospfd, and "router isis" by isisd, but the mapping is a fact about
# FRR and not about the word.
# Policy statements have no `router` line, so the check below cannot key on
# one. They are served by mgmtd and zebra, and with those stopped `vtysh`
# ACCEPTS the statement and stores nothing - measured on FRR 10.7.1, where a
# prefix-list typed with only staticd running produced no error and did not
# appear in the running configuration afterwards (#155).
#
# A `router bgp` stanza that goes nowhere is noticed: no session comes up. A
# prefix-list that goes nowhere is invisible until traffic that should have
# been filtered is not.
POLICY_STATEMENT = ("ip prefix-list", "ipv6 prefix-list", "route-map",
                    "bgp as-path access-list", "bgp community-list",
                    "bgp large-community-list")
POLICY_DAEMONS = ("mgmtd", "zebra")

# A static route is not a policy statement and not a `router` line, so neither
# check above ever looked at one. It needs staticd to serve it and mgmtd to
# configure it - and without them frr-reload EXITS 0 having done nothing.
# Measured: its own log says `[zebra] sending configuration` / `done` for the
# line, then notices on a second pass that the statement is still missing, sends
# it again, and exits 0 anyway. FRR discards a statement no running daemon
# claims, and says so to nobody (#197).
STATIC_STATEMENT = ("ip route", "ipv6 route")
STATIC_DAEMONS = ("mgmtd", "staticd")

#: The daemons frrinit.sh starts unconditionally. They are NOT lines in
#: /etc/frr/daemons that an operator can turn on, so telling somebody to enable
#: one there sends them to a file that will not mention it. One of these being
#: absent means frr did not finish starting, which is a different fault with a
#: different fix.
ALWAYS_STARTED = ("zebra", "mgmtd", "staticd")


#: The unit that runs this box's dataplane, which is not the same on every
#: box since 0.117.0: an installed appliance runs prisma-routing-containers
#: and an operator can put it back on vpp.service, and both are ordinary
#: states. Sending somebody to `systemctl status vpp` on a container box is
#: sending them to a unit nobody started - which says "inactive (dead)" and
#: reads like the answer. appliance/selftest took the same reading for the
#: same reason (#426); this is the VTY's half, and the screen that showed it
#: was `show version` on a box whose dataplane had stopped.
DATAPLANE_UNIT_CONTAINERS = "prisma-routing-containers"
DATAPLANE_UNIT_VPP = "vpp"


def dataplane_unit():
    """
    Which unit an operator should look at for this box's dataplane.

    Asked of systemd rather than read from a file: the two units conflict, so
    exactly one of them can be running, and `is-active` is the whole of the
    decision. Any failure to ask - no systemd, no permission, a box that is
    neither - answers vpp.service, which is what every box ran before 0.117.0
    and what a source install still runs.
    """
    try:
        return (DATAPLANE_UNIT_CONTAINERS
                if subprocess.run(
                    ["systemctl", "is-active", "--quiet",
                     f"{DATAPLANE_UNIT_CONTAINERS}.service"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, check=False).returncode == 0
                else DATAPLANE_UNIT_VPP)
    except OSError:
        return DATAPLANE_UNIT_VPP


#: Where appliance/watch-the-dataplane writes what the dataplane said before
#: it died. The file exists only on a box that stranded itself, which is
#: exactly the box whose container log is already gone - `docker logs` on a
#: container Docker removed, or on a box that has since rebooted, answers
#: nothing. So when this file is here it is named FIRST and the two commands
#: are the fallback, not the other way round (#428).
DATAPLANE_WHY = "/var/log/prisma-routing/why-the-dataplane-stopped"


def dataplane_journal_advice():
    """
    Where the reason a silent dataplane is silent is written, on this box.

    On the container path the unit's own journal carries compose's words and
    not the dataplane's: what VPP said is in the container's log, so both are
    named. One sentence, because an operator reading it has a box that is not
    forwarding.

    AND WHEN THE BOX ALREADY WROTE THE ANSWER DOWN, that is what it says.
    The field report this came from (#428) was a box whose dataplane never
    started: by the time anybody read a screen, the container was gone and
    the operator had rebooted, so both commands below answered nothing at
    all. The copy in DATAPLANE_WHY is the one thing that survives that.
    """
    if os.path.exists(DATAPLANE_WHY):
        return (f"the dataplane gave up and {DATAPLANE_WHY} has what it "
                "printed before it did")
    unit = dataplane_unit()
    if unit == DATAPLANE_UNIT_CONTAINERS:
        return ("'systemctl status prisma-routing-containers' and "
                "'docker logs prisma-routing_vpp_1' say why the dataplane "
                "is silent")
    return "'systemctl status vpp' and its journal say why the dataplane is silent"


def enable_daemon_advice(need):
    """How to get `need` running, which depends on whether it is optional."""
    if need in ALWAYS_STARTED:
        return (f"{need} is started by frr itself, not listed in "
                "/etc/frr/daemons - so frr did not finish starting. "
                "'systemctl restart frr' and read its journal")
    return f"Enable {need} in /etc/frr/daemons and restart frr"

ROUTER_DAEMON = {"bgp": "bgpd", "ospf": "ospfd", "ospf6": "ospf6d",
                 "isis": "isisd", "rip": "ripd", "eigrp": "eigrpd",
                 "babel": "babeld", "pim": "pimd"}


def configurable_daemons():
    """
    Every daemon this VTY can commit configuration for, read off the grammar.

    Derived rather than listed, because a list kept by hand is exactly what
    #187 was: the VTY offered `router ospf6`, no list on the box had ospf6d in
    it, and OSPFv3 became the one protocol a box could be configured for and
    could never run. A protocol added to TREE is accounted for here the same
    day it is offered.

    ROUTER_DAEMON is wider than the grammar on purpose - it validates whatever
    reaches FRR, `router rip` included - so it is read THROUGH the tree rather
    than whole. `show version` reporting ripd stopped on every box would be
    true and useless.
    """
    offered = {ROUTER_DAEMON[word] for word in TREE["config"]["router"][1]
               if word in ROUTER_DAEMON}
    return sorted(offered | set(ALWAYS_STARTED) | set(POLICY_DAEMONS)
                  | set(STATIC_DAEMONS))

# One source of truth, written by the build. A version compiled into the code
# drifts from the tag the moment someone forgets to edit it, and a box that
# misreports its version sends every later investigation the wrong way.
VERSION_PATH = os.environ.get("PRISMA_VERSION_FILE",
                              "/usr/share/prisma-routing/VERSION")


#: What `prisma_routing_version()` answers when there is no VERSION file to read.
#
# A constant because there are now two readers. `show version` prints it, and
# it also compares that string against the build the loaded dataplane reports
# (#291) - and "the box does not know its own version" is a THIRD answer to
# that comparison, not a disagreement. A second copy of this literal would
# turn a box with no VERSION file into a box announcing a pending reboot.
VERSION_UNKNOWN = "unknown (no VERSION file)"

#: Where the version this box states was read from. WHICH FILE IT WAS decides
#: whether that string can be compared with anything at all (#291).
#
# The stamp under /usr/share is written by appliance/install.sh from
# tools/version-stamp.sh, which is the same calculation the plugin is built
# with, so the two are the same KIND of thing and an equality between them
# means something.
#
# The repository's own VERSION is not. It reads "0.71.0": the number the NEXT
# release will carry, not a build stamp of anything, and it moves when
# somebody edits it rather than when anything is built. Measured on a lab
# container running prismash out of the tree, with the real plugin loaded and
# nothing whatever upgraded:
#
#   Prisma Routing 0.71.0
#     plugin:    0.71.0 (4a7044e)
#                this is the build the dataplane was loaded with at the last
#                boot, and it is not the Prisma Routing build above.
#
# Every developer running the VTY from a source tree was told their box needed
# a reboot. The string alone cannot tell the two cases apart, which is why the
# reading answers WHERE as well as WHAT.
VERSION_FROM_STAMP = "stamp"
VERSION_FROM_TREE = "tree"
VERSION_FROM_NOWHERE = "nowhere"


def prisma_routing_version_source():
    """The version this box states, and which file it came out of.

    ONE place decides which of those files exists, and this is it. A caller
    that re-opened them to work out which one had answered would be a second
    reader of the same truth, which is the shape #291 is about in the first
    place - so the reading answers both halves at once and
    `prisma_routing_version()` below is the spelling for callers that only want
    the text. That is the arrangement tools/version-stamp.sh already uses to
    keep `--filename` beside its stamp: one calculation, two spellings.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for path, source in ((VERSION_PATH, VERSION_FROM_STAMP),
                         (os.path.join(here, "..", "..", "VERSION"),
                          VERSION_FROM_TREE)):
        try:
            with open(path) as f:
                v = f.read().strip()
            if v:
                return v, source
        except OSError:
            continue
    return VERSION_UNKNOWN, VERSION_FROM_NOWHERE


def prisma_routing_version():
    """Version of this build, or a string that admits it is unknown."""
    return prisma_routing_version_source()[0]


# Where `system upgrade` finds releases (#440), the public directory #439
# publishes to. A box behind a mirror points this elsewhere; nothing on a box
# sets it otherwise.
RELEASES_URL = os.environ.get("PRISMA_RELEASES_URL",
                              "https://hexarouting.hexanetworks.com.br/releases")
#: Where a downloaded release is unpacked: /var/tmp, which survives the
#: session that asked and is not /tmp's tmpfs on a box short of memory.
UPGRADE_WORK = os.environ.get("PRISMA_UPGRADE_WORK", "/var/tmp")


def version_tuple(text):
    """
    (X, Y, Z) out of the front of `text`, or None when it is not one.

    The front, because this box's own version reads "0.122.9 (f8448d7)" and
    the build hash is not part of the order. Digits only, by is_number(): a
    version is compared as numbers, so 0.122.10 is newer than 0.122.9.
    """
    head = (text or "").split()[:1]
    parts = head[0].split(".") if head else []
    if len(parts) != 3 or not all(is_number(p) for p in parts):
        return None
    return tuple(int(p) for p in parts)


def fetch_url(url, timeout=30):
    """(bytes, "") from `url`, or (None, why). Never raises: a box that cannot
    reach the releases is a sentence, not a traceback."""
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read(), ""
    except urllib.error.HTTPError as e:
        return None, f"{url}: HTTP {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, f"{url}: {getattr(e, 'reason', e)}"


def download_to(url, path, timeout=300):
    """
    (sha256 hex, bytes, "") after writing `url` to `path`, or (None, 0, why).
    Never raises, like fetch_url().

    Streamed, and summed on the way, because the package is not small any
    more: it carries the container images since 0.125.0 (#427), about 120 MB,
    and held whole in memory it was that plus a copy for the write - inside a
    VTY that exists to stay up while the box is short of everything.
    """
    import urllib.error
    import urllib.request
    h = hashlib.sha256()
    n = 0
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r, \
                open(path, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
                f.write(chunk)
                n += len(chunk)
        return h.hexdigest(), n, ""
    except urllib.error.HTTPError as e:
        return None, 0, f"{url}: HTTP {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, 0, f"{url}: {getattr(e, 'reason', e)}"


def latest_release(fetch=fetch_url, base=None):
    """
    (version, "") of the newest published release, or (None, why).

    Read off `latest/`'s listing, where the package's own name carries its
    version - so the answer is what an operator would download, not a
    number written somewhere beside it.
    """
    body, why = fetch((base or RELEASES_URL).rstrip("/") + "/latest/")
    if body is None:
        return None, why
    found = re.findall(r"prisma-routing-(\d+\.\d+\.\d+)-upgrade\.tar\.gz",
                       body.decode("utf-8", "replace"))
    if not found:
        return None, "the releases' latest/ lists no upgrade package"
    return max(found, key=version_tuple), ""


def extract_within(tar_path, dest):
    """
    Unpack `tar_path` into `dest`, or say why not; None when done.

    Every member is checked BEFORE anything is written: a path that leaves
    `dest`, a link whose target does, and anything that is not a file, a
    directory or a link is refused whole. The package carries one link of its
    own - `upgrade` pointing into `appliance/` - and it stays inside. Checked
    here rather than by tarfile's extraction filters, which the Python on a
    bookworm box cannot be counted on to have.
    """
    root = os.path.realpath(dest)
    try:
        with tarfile.open(tar_path, "r:gz") as t:
            members = t.getmembers()
            for m in members:
                target = os.path.realpath(os.path.join(root, m.name))
                if target != root and not target.startswith(root + os.sep):
                    return f"'{m.name}' would land outside the package"
                if m.issym() or m.islnk():
                    base = os.path.dirname(target) if m.issym() else root
                    to = os.path.realpath(os.path.join(base, m.linkname))
                    if not to.startswith(root + os.sep):
                        return f"'{m.name}' links outside the package"
                elif not (m.isfile() or m.isdir()):
                    return f"'{m.name}' is not a file, a directory or a link"
            t.extractall(root, members)
    except (tarfile.TarError, OSError, EOFError) as e:
        return f"the package could not be unpacked: {e}"
    return None


def version_split(build, mine, source):
    """
    True when the dataplane answering is a DIFFERENT build from this box's.

    ONE decision with two readers, for the reason the VERSION_UNKNOWN comment
    above gives about its own literal: `show version` prints a paragraph when
    the two disagree, and a refusal that came back from the dataplane says the
    upgrade is why it did (#293). A second copy of this condition would be a
    second answer to one question, and the two would drift the first time
    either was corrected.

    None of the other answers is a near miss and none of them may fall through
    to True. `plugin_build()` answers None for a dataplane that never spoke
    and "" for one that spoke without our plugin: neither of those STATES a
    build, so there is nothing to disagree with, and both are already reported
    as what they are. And a version that did not come out of an installed
    stamp is not a build either - the repository's own VERSION names the next
    release rather than any build - so a source tree compares nothing rather
    than comparing two different kinds of thing (#291).
    """
    return bool(build) and source == VERSION_FROM_STAMP and build != mine


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
ARG_WORD = "<word>"

# A RANGE IS A PROMISE, so there is one of these per quantity and not one for
# every number in the VTY.
#
# `ARG_NUM = "<1-65535>"` stood here and was offered at fifteen statements.
# It is true at three of them - a TCP or UDP port really does stop at 65535 -
# and at the other twelve it was a sentence the box had no intention of
# keeping. Measured on master, every one of these from the prompt: `max-
# sessions 0` was accepted, on a line whose own description says "0 is
# unlimited"; `block-stride 0` and `reserve 0` likewise, and 0 is the ordinary
# value for both; `timeout udp 65536`, `vrf 65536`, `snmp enterprise-number
# 65536` and `max-blocks 65536` were all accepted. So the hint refused nothing
# and promised nothing an operator could rely on in either direction - and
# `max-blocks` was the expensive one, because the dataplane's field is a u8
# and 256 arrives there as 1 (#351).
#
# The rule these four hold to: the two numbers are the two the box takes, and
# where the box takes a wider range than the hint used to admit, the hint
# moves rather than the box - an operator who needs a 24-hour session timeout
# should be able to type 86400, and could always type it.
ARG_PORT = "<1-65535>"
#: Seconds. The dataplane carries every timeout in a u32 (`bpa_timeouts_t`),
#: which is where the top comes from; the bottom is 1 because zero means
#: "leave this one alone" in the message that sets them, not "expire at once".
ARG_SECONDS = "<1-4294967295>"
#: A VRF table id on an INTERFACE, u32 in Linux. Zero is not in the range
#: because here it REMOVES the statement rather than selecting table 0.
ARG_VRF = IFACE_VRF_RANGE
#: The same id where it names a table rather than a statement: u32 in the
#: dataplane (`bpa_pool_t.vrf_id`), and 0 is VPP's default table, which a pool
#: may legitimately be pinned to.
ARG_VRF_ID = "<0-4294967295>"
#: A per-subscriber limit the dataplane counts in a u32, where 0 is the
#: documented "no limit" rather than a limit of none.
ARG_LIMIT = "<0-4294967295>"

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
#: The same names, where typing one the box has NOT got is how an interface is
#: made (#406). Two shapes do that: `<parent>.<tag>` creates a VLAN
#: sub-interface and `loop<n>` creates a loopback, and both worked for
#: releases while `?` offered only the interfaces that already existed - so
#: the list read as the complete set of what may be typed, and a feature
#: nobody can find is a feature nobody has. A marker of its own rather than a
#: longer ARG_INTERFACE: `show interface <name>` and `nat cgnat interface
#: <name>` take a name the box HAS, and offering "or invent one" there would
#: be offering a name those statements refuse.
ARG_INTERFACE_NEW = "<interface>, or a name that creates one"

#: What `?` says UNDER a hint, where the hint alone cannot carry it.
#:
#: A hint is one row beside the options, so it has room for a shape and not
#: for a sentence. These are the sentences, folded to the console under it.
#: Data rather than a branch in the printer, so a marker that grows a note is
#: one line here and the printer stays one printer.
HINT_NOTES = {
    ARG_INTERFACE_NEW:
        "a name this box has not got is how one is made: <parent>.<tag> "
        "creates a VLAN sub-interface - its parent has to exist - and "
        "loop<n> creates a loopback. Anything else is refused at the commit, "
        "naming what the box has.",
}
#: The card as LINUX names it, which is a different question from
#: ARG_INTERFACE and has to be: before the dataplane has been given a card it
#: has no interfaces at all, so the one screen that could list the candidates
#: is empty exactly when the operator needs it (#283).
ARG_HOST_NIC = "<nic>"
ARG_POOL = "<pool>"
ARG_USER = "<user>"
ARG_SNMP_USER = "<snmp-user>"
ARG_PREFIX_LIST = "<prefix-list>"
ARG_NEIGHBOUR = "<neighbour>"
ARG_AS_PATH_LIST = "<as-path-list>"
ARG_COMMUNITY_LIST = "<community-list>"
ARG_ROUTE_MAP = "<route-map>"
ARG_PEER_GROUP = "<peer-group>"
ARG_AREA = "<area>"
ARG_ISIS_TAG = "<isis-instance>"
ARG_REV = "<0-19>"
# The operator's own word, so the tree enumerates nothing under it and TAB
# offers nothing: a hostname is not this box's to complete.
ARG_HOST = "<host>"

#: The options `cgnat logging syslog` takes after its collector. Declared
#: rather than written at each site: the refusals carry one of these words,
#: and a slot whose values are a closed set has a widest the console reader
#: can measure (#363).
SYSLOG_OPTIONS = ("enterprise", "port", "facility", "severity")

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
    "blocks": ("blocks allocated in dynamic mode, and how many a forward pins",
               None),
    "logging": ("IPFIX export state", None),
    # The COUNT and the instant it was read. Not the entries: an identity is
    # personal data, and its presence in the log is the reason the log is
    # protected (#21).
    "identity": ("how many identities this box holds, and when it read them",
                 None),
    "history": ("dated configuration trail", {
        # The tamper check the operator documentation sends people to, and
        # until #313 there was no VTY spelling that reached it. The dataplane
        # has had it since v0.23.0 - `show bpa pool history [verify]` - and
        # this VTY proxied the command without its argument, so every page
        # documenting the check named the dataplane's own spelling. The wiki
        # must never do that: it is written for operators, and `show bpa ...`
        # is not a command this product offers them.
        #
        # It is read where somebody is about to act on the answer: the page on
        # taking a box to a new version puts it at the last step before the
        # disk is erased, with a "pare aqui" attached to its result.
        "verify": ("check the trail's hash chain, and name the first entry "
                   "that does not match", None),
    }),
    # The two the dataplane had and this VTY did not reach (#228). Both answer
    # "what does the box ACTUALLY have", which is a different question from
    # what the file says - the distinction #172 was about for worker cores,
    # and the one an operator needs when an FTP transfer hangs or a port
    # forward does not work. Without them the way to that answer was vppctl,
    # which is the tool this VTY exists to keep them out of, and which is
    # every capability at once (#76).
    "alg": ("which ALGs the dataplane has on, per pool", None),
    "forward": ("the port forwards the dataplane holds", "[<pool>]"),
    # The one entry here that is NOT the dataplane's own command forwarded on.
    # See Shell.show_ha: the dataplane holds one box's configuration and has
    # never heard of the other, so the comparison is answered where both can
    # be read.
    "ha": ("whether this box and its peer map subscribers the same way", {
        "peer": ("compare against a peer configuration file elsewhere",
                 "<file>")}),
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
    "bgp": ("BGP sessions, and whether the dataplane has their routes", {
        "summary": ("one line per session, with what reached the FIB", None),
        "neighbor": ("one session in detail", ARG_NEIGHBOUR),
    }),
    "ospf": ("OSPF adjacencies, and whether the dataplane has their routes", {
        "neighbor": ("one line per adjacency, with what reached the FIB",
                     None),
        "interface": ("what OSPF believes about each interface, beside what "
                      "the dataplane has", None),
    }),
    # `adjacency` rather than `neighbor`, which is what IS-IS itself calls
    # them and what Juniper types. FRR's own `show isis neighbor` still works
    # and lands in the same place; it is simply not the one offered, because
    # offering both teaches neither.
    "isis": ("IS-IS adjacencies, and whether the dataplane has their routes", {
        "adjacency": ("one line per adjacency, with what reached the FIB",
                      None),
        "interface": ("each circuit, and whether its frames are being punted",
                      None),
    }),
    "interface": ("interfaces on the dataplane, with state and addresses", {
        "terse": ("one line each: admin state, link state, address", None),
        "description": ("what each interface is for, and its state", None),
        "utilization": ("sample the counters and graph what is passing", {
            "interval": ("seconds between samples (default 2)", "<seconds>"),
            "count": ("how many samples to take (default 5)", "<count>"),
            ARG_INTERFACE: ("only this interface", None),
        }),
        ARG_INTERFACE: ("one interface, in detail, with its counters", None),
    }),
    # Answers the question `show interface` cannot answer on a fresh box: the
    # dataplane has been given no NIC, so its list is empty exactly when the
    # operator needs to see what there is to choose from (#283).
    "hardware": ("the box's physical NICs as Linux has them, and which one "
                 "carries the default route", None),
    "version": ("Prisma Routing, VPP and FRR versions", None),
    "logging": ("system log (FRR)", None),
}

# Offered inside `router static`. FRR has no such block - its static routes are
# top-level `ip route` statements - so this is a hierarchy this VTY provides and
# translates, which is the whole reason it needs a grammar of its own here.
#: A word of the operator's that the tree READS rather than lists: a key in
#: this table rides in a node beside the keywords, like `<interface>`, but
#: where `<interface>` is offered from the box, these are recognised by their
#: shape (#436). A static route starts with its prefix, and without this the
#: tree could walk no further than the first word of one.
ARG_STATIC_PREFIX = "<prefix>"
VALUE_SHAPES = {
    ARG_STATIC_PREFIX: lambda w: "/" in w and _is_network(w),
}


def _is_network(word) -> bool:
    """An IPv4 or IPv6 prefix, host bits allowed - FRR's call, not this one's.

    Not prefix_error(): that one is the CGNAT's, and the CGNAT is IPv4 only,
    where a static route is either family (#436)."""
    try:
        ipaddress.ip_network(word, strict=False)
        return True
    except ValueError:
        return False

_STATIC_ROUTE = {
    "next-hop": ("where it goes: an address, an interface, blackhole, Null0 "
                 "or reject",
                 "<next-hop> [distance <1-255>] [tag <1-4294967295>] "
                 "[description <text>]"),
}
STATIC_BLOCK_GRAMMAR = {
    ARG_STATIC_PREFIX: ("a destination this box is told about, rather than "
                        "taught: '<prefix> next-hop <next-hop>'",
                        _STATIC_ROUTE),
    "no": ("remove", {ARG_STATIC_PREFIX: ("remove the static route to it",
                                          _STATIC_ROUTE)}),
    "exit": ("leave the block", None),
}

# Offered inside `router isis <tag>`. IS-IS is what an ISP core runs, and this
# VTY offered `<word>` for the instance tag and nothing after it (#175).
ISIS_BLOCK = {
    "net": ("this box's NSAP address; without it the instance never starts",
            "<49.xxxx.xxxx.xxxx.xxxx.00>"),
    "is-type": ("which levels this box takes part in", {
        "level-1": ("area-internal only", None),
        "level-2-only": ("backbone only - what an ISP core runs", None),
        "level-1-2": ("both", None),
    }),
    "metric-style": ("how a link's metric is encoded", {
        "wide": ("32-bit metrics; narrow caps at 63 and cannot express a "
                 "10G-and-up topology", None),
        "narrow": ("6-bit metrics, for a neighbour that cannot do wide", None),
        "transition": ("send both while a network is being migrated", None),
    }),
    "topology": ("carry a second address family over the same adjacency", {
        "ipv6-unicast": ("a separate SPF for IPv6 (RFC 5120). Without it IPv6 "
                         "still rides the one topology, which is correct when "
                         "every router in the area agrees", None),
    }),
    "set-overload-bit": ("tell everybody not to transit through this box - "
                         "IS-IS's maintenance drain",
                         "[on-startup <0-86400>]"),
    "redistribute": ("inject routes from another source", {
        "ipv4": ("IPv4", "<proto> level-1|level-2 [metric <n>] "
                 "[route-map <name>]"),
        "ipv6": ("IPv6", "<proto> level-1|level-2 [metric <n>] "
                 "[route-map <name>]"),
    }),
    "default-information": ("originate a default route", {
        "originate": ("advertise a default", "ipv4|ipv6 level-1|level-2 "
                      "[always] [route-map <name>]"),
    }),
    "domain-password": ("level-2 authentication; never printed back",
                        "[clear|md5] <password>"),
    "area-password": ("level-1 authentication; never printed back",
                      "[clear|md5] <password>"),
    "log-adjacency-changes": ("log every adjacency transition", None),
    "lsp-mtu": ("largest LSP this box generates; larger than the smallest "
                "interface MTU and they cannot be sent", "<128-4352>"),
    "lsp-gen-interval": ("seconds between LSP regenerations", "<1-120>"),
    "spf-interval": ("seconds between SPF runs", "<1-120>"),
    "max-lsp-lifetime": ("seconds an LSP stays valid", "<350-65535>"),
    "exit": ("leave the IS-IS configuration", None),
}

# IS-IS's interface statements, which live in the interface block the way
# OSPF's do.
ISIS_INTERFACE = {
    "circuit-type": ("which levels this interface takes part in", {
        "level-1": ("area-internal only", None),
        "level-2-only": ("backbone only", None),
        "level-1-2": ("both", None),
    }),
    "network": ("how IS-IS treats the link", {
        "point-to-point": ("no DIS election and no pseudo-node - what a p2p "
                           "Ethernet wants, and setting it on one side only "
                           "is a non-adjacency", None),
    }),
    "metric": ("this interface's metric; above 63 needs metric-style wide",
               "<0-16777215> [level-1|level-2]"),
    "passive": ("advertise the interface and send no hellos", None),
    "hello-interval": ("seconds between hellos", "<1-600>"),
    "hello-multiplier": ("missed hellos before the neighbour is down",
                         "<2-100>"),
    "password": ("per-interface authentication; never printed back",
                 "[clear|md5] <password>"),
    "bfd": ("track the adjacency with BFD", None),
}

# Offered inside `router ospf`. The statements an ISP edge or aggregation box
# actually uses - not everything FRR takes, because a grammar that lists a
# thousand commands is the reference manual this VTY exists not to be (#173).
OSPF_BLOCK = {
    "ospf": ("OSPF-wide settings", {
        "router-id": ("the identifier this box uses in OSPF", "<A.B.C.D>"),
    }),
    "network": ("run OSPF on the interfaces this prefix covers",
                "<A.B.C.D/M> area <area>"),
    "area": ("area settings", ARG_AREA),
    "passive-interface": ("run OSPF on it but send no hellos - what every "
                          "subscriber-facing port should be", ARG_INTERFACE),
    "default-information": ("originate a default route", {
        "originate": ("advertise 0.0.0.0/0",
                      "[always] [metric <n>] [metric-type 1|2] "
                      "[route-map <name>]"),
    }),
    "redistribute": ("inject routes from another source", {
        "connected": ("directly attached networks",
                      "[metric <n>] [metric-type 1|2] [route-map <name>]"),
        "static": ("configured static routes",
                   "[metric <n>] [metric-type 1|2] [route-map <name>]"),
        "bgp": ("routes BGP learned - on an edge box that is a full table "
                "into the IGP, so give it a route-map",
                "[metric <n>] [metric-type 1|2] [route-map <name>]"),
        "isis": ("routes IS-IS learned",
                 "[metric <n>] [metric-type 1|2] [route-map <name>]"),
    }),
    "auto-cost": ("what a link's cost is measured against", {
        "reference-bandwidth": ("in Mbit/s; without this every link at or "
                                "above 100 Mbit/s costs 1, which disables "
                                "path selection on a 10G box", "<1-4294967>"),
    }),
    # FRR's number, read off `list` in `router ospf` on 10.7.1: (1-256).
    # This said <1-64>, which is neither FRR's bound nor anything this box
    # enforces - it forwards the statement and FRR decides - so an operator
    # with 100 equal-cost paths was told by the screen that the box would not
    # take what it takes (#353).
    "maximum-paths": ("how many equal-cost paths to install", "<1-256>"),
    "log-adjacency-changes": ("log every adjacency transition", "[detail]"),
    "timers": ("SPF timers", {
        "throttle": ("throttle the SPF calculation", {
            "spf": ("delay, initial hold, maximum hold, in milliseconds",
                    "<delay> <init> <max>"),
        }),
    }),
    "distance": ("administrative distance", "<1-255>"),
    "neighbor": ("a neighbour on a non-broadcast network",
                 "<A.B.C.D> [poll-interval <n>] [priority <n>]"),
    "exit": ("leave the OSPF configuration", None),
}

# OSPF's interface statements. They live in INTERFACE_BLOCK because that is
# where FRR puts them and where an operator looks for them - and unlike mtu,
# urpf and the addresses, they stay FRR's rather than the dataplane's.
OSPF_INTERFACE = {
    "area": ("run OSPF on this interface, in this area", ARG_AREA),
    "cost": ("this interface's cost", "<1-65535>"),
    "network": ("how OSPF treats the link", {
        "point-to-point": ("no DR election - what a /30 transit link wants",
                           None),
        "broadcast": ("elect a DR (the default on Ethernet)", None),
    }),
    "hello-interval": ("seconds between hellos", "<1-65535>"),
    "dead-interval": ("seconds before the neighbour is declared down",
                      "<1-65535>"),
    "priority": ("this box's priority in the DR election", "<0-255>"),
    "authentication": ("how the adjacency is authenticated", {
        "message-digest": ("MD5", None),
        "null": ("none, overriding the area", None),
    }),
    "authentication-key": ("the plain-text key; never printed back", "<key>"),
    "message-digest-key": ("an MD5 key; never printed back",
                           "<1-255> md5 <key>"),
    "passive": ("run OSPF here and send no hellos", None),
    "bfd": ("track the adjacency with BFD", None),
    "mtu-ignore": ("do not compare MTU in database description packets - the "
                   "escape hatch when the two sides disagree and cannot be "
                   "made to agree", None),
}

# Offered inside `router ospf6`, and the reason it exists at all is #187: the
# VTY took `router ospf6` as an unknown word and passed it to FRR, so it was
# accepted, committed, and discoverable from nowhere - `?` did not list it and
# TAB did not complete it - on a box whose installer enabled every OTHER
# routing daemon. Offering a protocol only to those who already know it is
# offered is the half-measure #99 was about.
#
# Every statement here was typed into a live FRR 10.7.1 and read back
# (lab/frr-readback.sh 4e). The ones left OUT are as deliberate as the ones in:
# ospf6d has no `interface <name> area <a>` inside the router block at all -
# measured, "% Unknown command" - so an interface joins an area through `ipv6
# ospf6 area` under `interface` and through nothing else.
OSPF6_BLOCK = {
    "ospf6": ("OSPFv3-wide settings", {
        "router-id": ("the identifier this box uses in OSPFv3; it is a "
                      "32-bit id written like an IPv4 address and is not an "
                      "address", "<A.B.C.D>"),
    }),
    "area": ("area settings", ARG_AREA),
    "redistribute": ("inject routes from another source", {
        "connected": ("directly attached networks", "[route-map <name>]"),
        "static": ("configured static routes", "[route-map <name>]"),
        "bgp": ("routes BGP learned - on an edge box that is a full table "
                "into the IGP, so give it a route-map", "[route-map <name>]"),
        "isis": ("routes IS-IS learned", "[route-map <name>]"),
    }),
    "default-information": ("originate a default route", {
        "originate": ("advertise ::/0",
                      "[always] [metric <n>] [metric-type 1|2] "
                      "[route-map <name>]"),
    }),
    "auto-cost": ("what a link's cost is measured against", {
        "reference-bandwidth": ("in Mbit/s; without this every link at or "
                                "above 100 Mbit/s costs 1, which disables "
                                "path selection on a 10G box", "<1-4294967>"),
    }),
    # (1-256) in `router ospf6` too, measured the same way.
    "maximum-paths": ("how many equal-cost paths to install", "<1-256>"),
    "log-adjacency-changes": ("log every adjacency transition", "[detail]"),
    "timers": ("SPF timers", {
        "throttle": ("throttle the SPF calculation", {
            "spf": ("delay, initial hold, maximum hold, in milliseconds",
                    "<delay> <init> <max>"),
        }),
    }),
    "distance": ("administrative distance", "<1-255>"),
    "exit": ("leave the OSPFv3 configuration", None),
}

# OSPFv3's interface statements, offered under `ipv6` because that is where
# FRR puts them: an OSPFv3 interface is enabled with `ipv6 ospf6 area <a>` and
# never with the `ip ospf` spelling OSPFv2 uses. The two protocols are
# separate instances on separate address families, and a box can run both.
#
# hello-interval, dead-interval and priority are NOT here, and that is
# measured rather than an oversight: FRR leaves all three out of its own
# read-back when they are typed at their default, which is the #197 shape, and
# the read-back check would report a commit that worked as a statement thrown
# away. lab/frr-readback.sh probes them with a non-default control, which is
# what tells an omission from a refusal, and FRR_OMITS_AT carries the answer.
OSPF6_INTERFACE = {
    "area": ("run OSPFv3 on this interface, in this area", ARG_AREA),
    "cost": ("this interface's cost", "<1-65535>"),
    "network": ("how OSPFv3 treats the link", {
        "point-to-point": ("no DR election - what a transit link wants", None),
        "broadcast": ("elect a DR (the default on Ethernet)", None),
    }),
    "passive": ("run OSPFv3 here and send no hellos", None),
    "bfd": ("track the adjacency with BFD", None),
    "mtu-ignore": ("do not compare MTU in database description packets - the "
                   "escape hatch when the two sides disagree and cannot be "
                   "made to agree", None),
}

# Offered inside an `interface <name>` block. It is not part of the mode tree
# because the block is entered by an argument, not by a keyword the tree can
# walk into - and an operator who cannot discover `mtu` with `?` will inherit
# the TAP's 9000 and spend an afternoon on an OSPF adjacency stuck in Exchange.
INTERFACE_BLOCK = {
    "description": ("what this interface is for", "<text>"),
    # The two ends come from config.VLAN_RANGE, which is also what refuses a
    # tag outside them - at the prompt and in a saved file. A hint that spells
    # the numbers out is a second copy of them, and a second copy of a range
    # is a range that can disagree with the check (#350).
    "vlan": ("VLAN tag, on a sub-interface",
             f"{VLAN_RANGE} [inner {VLAN_RANGE}]"),
    "vrf": ("the routing table this interface's addresses live in; 0 takes "
            "the interface back out of one", ARG_VRF),
    "linux-name": ("the name FRR and 'ip' see this interface by; without one "
                   "the routing daemons cannot see it at all", "<name>"),
    "mac-address": ("the MAC this interface answers to", "<xx:xx:xx:xx:xx:xx>"),
    "ipv4": ("IPv4 addressing", {
        "address": ("an address on this interface, may repeat", ARG_PREFIX),
    }),
    "ip": ("routing-protocol settings FRR owns on this interface", {
        "ospf": ("OSPF on this interface", OSPF_INTERFACE),
        "router": ("join a routing instance", {
            "isis": ("run IS-IS for IPv4 on this interface", ARG_ISIS_TAG),
        }),
    }),
    "isis": ("IS-IS settings on this interface", ISIS_INTERFACE),
    "ipv6": ("IPv6 addressing", {
        "address": ("an address on this interface, may repeat", ARG_PREFIX),
        "nd": ("neighbour discovery", {
            "suppress-ra": ("stop advertising this box as a router", None),
        }),
        "ospf6": ("OSPFv3 on this interface", OSPF6_INTERFACE),
        "router": ("join a routing instance", {
            "isis": ("run IS-IS for IPv6 on this interface, in whatever "
                     "topology the instance has", ARG_ISIS_TAG),
        }),
    }),
    "shutdown": ("take the interface administratively down", None),
    # The range comes from config.py, which is also what refuses one outside
    # it - at the prompt AND in a saved file, since #417. A hint that spells
    # the numbers out is a second copy of them, which is what `vlan` above
    # says and what this statement had three of.
    "mtu": ("MTU, on both the dataplane interface and its TAP", MTU_RANGE),
    "urpf": ("drop packets whose source does not route back this way", {
        "strict": ("the source MUST route back out this interface", None),
        "loose": ("the source MUST have a route somewhere", None),
        "off": ("no check (the default)", None),
    }),
    # The ranges come from config.py, which is also what refuses one outside
    # them - at the prompt and in a saved file - for the reason `vlan` says
    # two lines up.
    "vrrp": ("stand for a service address together with the peer (RFC 5798), "
             "on the dataplane interface where the packets are",
             f"<{VRID_MIN}-{VRID_MAX}> address <A.B.C.D> | "
             f"priority <{VRRP_PRIORITY_MIN}-{VRRP_PRIORITY_MAX}> | "
             f"track <interface> decrement "
             f"<{VRRP_DECREMENT_MIN}-{VRRP_DECREMENT_MAX}>"),
    "exit": ("leave the interface", None),
}

# WHERE THE BOUNDARY IS, inside an `interface` block that carries both halves.
#
# These statements are this box's: they are applied to the VPP interface by
# the commit, and FRR neither owns them nor, in most cases, can act on them at
# all. Everything else in the block - `ip ospf ...`, `isis ...`, `ipv6 ospf6
# ...` - is FRR's and is handed over word for word, `no` included, because
# `no ip ospf cost` is a statement FRR does know.
#
# This tuple is the ONE place that list lives. Both directions read it: the
# branch that intercepts `mtu 9000` and the branch that refuses `no
# mac-address`. That is deliberate and it is the difference between a fix and
# a second list - a set written out beside the handlers would go stale the
# next time a dataplane statement is added, and the new statement's `no` would
# go back to leaking into FRR exactly as `no mac-address` did (#224). Delete
# an entry from here and the POSITIVE statement leaks too, which the existing
# tests for each of them catch immediately.
#
# Matched longest-first. No entry is a prefix of another today, so the order
# changes nothing yet - it is here so that one added later cannot be shadowed
# by a shorter entry above it and silently stop being intercepted.
INTERFACE_DATAPLANE = (
    ("ipv6", "nd", "suppress-ra"),
    ("ipv4", "address"),
    ("ipv6", "address"),
    ("description",),
    ("linux-name",),
    ("mac-address",),
    ("shutdown",),
    ("mtu",),
    ("urpf",),
    ("vlan",),
    ("vrrp",),
    ("vrf",),
)

# The dataplane statements `no` cannot mean anything for, and what to type
# instead. An entry here is a refusal; absence means a handler negates it.
#
# `vlan` is the only one. A sub-interface IS its tag - the VTY refuses to
# create one whose name and tag disagree, because the dataplane names a
# sub-interface after its sub_id - so "this sub-interface with no VLAN" is not
# a thing that can exist on the box. What the operator wants is the
# sub-interface gone, and that statement already exists.
# A tuple: the verdict, then the prose under it. One long sentence handed to
# `err` reaches the console at 185 columns (#355), and the move the operator
# needs is the last clause of it.
INTERFACE_NO_REFUSED = {
    "vlan": ("vlan cannot be removed",
             "a sub-interface is named after its tag, so one without a tag is "
             "not something the dataplane can hold",
             "remove the sub-interface itself: no interface <name>"),
}

# `no` inside an interface, derived. Every statement the block offers can be
# negated except the ones refused above - the dataplane ones by the handlers
# in do_config, FRR's own by being handed to FRR.
INTERFACE_BLOCK["no"] = ("remove a statement from this interface",
                         {k: v for k, v in INTERFACE_BLOCK.items()
                          if k not in ("exit",)
                          and k not in INTERFACE_NO_REFUSED})


def dataplane_statement(words):
    """
    Which dataplane statement `words` is, as a string, or None for FRR's.

    Answers for the words of a statement WITHOUT its `no`: the caller strips
    it, so one table serves both directions.
    """
    for stmt in INTERFACE_DATAPLANE:
        if tuple(words[:len(stmt)]) == stmt:
            return " ".join(stmt)
    return None

# Offered inside `router bgp <asn>`, and inside an `address-family` under it.
#
# The VTY passed these through and understood none of them: no completion, no
# `?`, and a typo discovered when FRR refused it in the middle of a commit
# (#142). Modelling them is not about refusing more - most of BGP is still
# passed through - it is about the operator being able to DISCOVER what the
# box takes, from the box, at the point they are typing.
#
# Split in two because the same word means different things at the two levels:
# `neighbor X route-map RM in` belongs to an address family and does nothing
# outside one, while `neighbor X remote-as` outside an address family is what
# creates the session in the first place.
# Offered inside `route-map <name> permit|deny <seq>`.
#
# Route-maps are where an ISP's policy lives, and the VTY modelled none of it
# (#144). The clause set here is the one an ISP edge actually uses; anything
# else still reaches FRR, and is MARKED as unchecked when the configuration is
# displayed, so an operator can tell what was verified from what was merely
# forwarded.
ROUTE_MAP_MATCH = {
    "ip": ("IPv4 selectors", {
        "address": ("by prefix", {
            "prefix-list": ("prefixes in a named list", ARG_PREFIX_LIST),
        }),
        "next-hop": ("by next hop", {
            "prefix-list": ("next hops in a named list", ARG_PREFIX_LIST),
        }),
    }),
    "ipv6": ("IPv6 selectors", {
        "address": ("by prefix", {
            "prefix-list": ("prefixes in a named list", ARG_PREFIX_LIST),
        }),
    }),
    "rpki": ("by origin validation state", {
        "valid": ("a ROA covers the prefix and the origin matches", None),
        "invalid": ("a ROA covers the prefix and the origin does NOT match",
                    None),
        "notfound": ("no ROA covers the prefix - which is also what every "
                     "route looks like when no cache is connected", None),
    }),
    "as-path": ("by AS path, against a named list", ARG_AS_PATH_LIST),
    "community": ("by community, against a named list", ARG_COMMUNITY_LIST),
    "large-community": ("by large community, against a named list",
                        ARG_COMMUNITY_LIST),
    "metric": ("by MED", "<0-4294967295>"),
    "peer": ("by which neighbour sent it", ARG_NEIGHBOUR),
}

ROUTE_MAP_SET = {
    "local-preference": ("which path this box prefers; higher wins",
                         "<0-4294967295>"),
    # FRR's own form, read off `list`: the MED can be set, added to or
    # subtracted from, and `rtt` is the round-trip-time form the daemon takes
    # for IS-IS. <0-4294967295> named a third of it, and the two thirds it
    # left out are the ones an operator reaches for when they want to nudge a
    # MED rather than replace it.
    "metric": ("the MED sent to the neighbour",
               "<-4294967295-4294967295>|rtt|+rtt|-rtt|igp|aigp"),
    "community": ("communities to carry; 'additive' keeps the ones already "
                  "there", "<AS:NN> [additive]"),
    "large-community": ("large communities to carry",
                        "<AS:NN:MM> [additive]"),
    "next-hop": ("the next hop advertised", "<A.B.C.D>"),
    "as-path": ("AS path manipulation", {
        "prepend": ("lengthen the path so others prefer another way",
                    "<asn> [<asn> ...]"),
        "exclude": ("remove AS numbers from the path", "<asn> [<asn> ...]"),
    }),
    # (0-4294967295) in `route-map` on FRR 10.7.1, and measured: `set weight
    # 65536` is taken and read back. <0-65535> was BGP's weight as Cisco
    # documents it, not as the daemon this box forwards to implements it.
    "weight": ("this box's own preference, never advertised",
               "<0-4294967295>"),
    "origin": ("the origin attribute", {
        "igp": ("learned inside the AS", None),
        "egp": ("learned from another AS", None),
        "incomplete": ("origin unknown", None),
    }),
}

# Offered inside `rpki`. FRR speaks RTR through a loadable module; an ISP that
# validates origins elsewhere should not have an edge box that accepts whatever
# it is told (#147).
RPKI_BLOCK = {
    "rpki": ("the validator caches this box asks", {
        # The transport keyword is NOT optional, and leaving it out is a plain
        # unknown command - on the live box as well as in the file frr-reload
        # validates. Written out here because it was omitted once and the
        # resulting failure read like an upstream limitation rather than a
        # missing word.
        "cache": ("a cache to ask", {
            "tcp": ("plain RTR over TCP",
                    "<host> <port> [source <A.B.C.D>] preference <1-255>"),
            "ssh": ("RTR over SSH",
                    "<host> <port> <user> <privkey> [known-hosts] "
                    "[source <A.B.C.D>] preference <1-255>"),
        }),
        "polling_period": ("seconds between refreshes", "<1-86400>"),
        "expire_interval": ("seconds before data is discarded", "<600-172800>"),
        "retry_interval": ("seconds before retrying a lost cache", "<1-7200>"),
        "reset": ("drop what the caches sent and ask again", None),
    }),
    "exit": ("leave the RPKI configuration", None),
}

ROUTE_MAP_BLOCK = {
    "match": ("which routes this clause applies to", ROUTE_MAP_MATCH),
    "set": ("what it changes about them", ROUTE_MAP_SET),
    "call": ("run another route-map before deciding", ARG_ROUTE_MAP),
    "on-match": ("what to do when this clause matches", {
        "next": ("carry on to the next clause", None),
        "goto": ("jump to a later clause", "<seq>"),
    }),
    "description": ("what this clause is for", "<text>"),
    "exit": ("leave the clause", None),
}

BGP_NEIGHBOUR = {
    "remote-as": ("the neighbour's AS; this is what creates the session",
                  "<1-4294967295>|internal|external"),
    "description": ("what this session is, for whoever reads it next",
                    "<text>"),
    "password": ("MD5 password for the session; never printed back", "<text>"),
    "update-source": ("the address or interface the session is sourced from",
                      ARG_INTERFACE),
    "ebgp-multihop": ("hops allowed to a neighbour not on a connected network",
                      "<1-255>"),
    "timers": ("keepalive and hold time", "<keepalive> <hold>"),
    "maximum-prefix": ("tear the session down above this many prefixes",
                       "<n> [<percent>] [restart <minutes>]"),
    "next-hop-self": ("advertise this box as the next hop", None),
    "bfd": ("track the session with BFD, so a failure is seconds not minutes",
            None),
    "shutdown": ("hold the session down, keeping the configuration", None),
    "peer-group": ("put this neighbour in a peer group", ARG_PEER_GROUP),
    "capability": ("optional BGP capabilities", {
        "extended-nexthop": ("IPv4 routes over an IPv6 session", None),
    }),
}

BGP_AF_NEIGHBOUR = {
    "activate": ("carry THIS address family with the neighbour", None),
    "route-map": ("apply a route-map in one direction",
                  ARG_ROUTE_MAP),
    "prefix-list": ("apply a prefix-list in one direction", ARG_PREFIX_LIST),
    "next-hop-self": ("advertise this box as the next hop", None),
    "soft-reconfiguration": ("keep what the neighbour sent, so policy can be "
                             "re-applied without bouncing the session", {
        "inbound": ("store the routes as received; this costs memory", None),
    }),
    "maximum-prefix": ("tear the session down above this many prefixes",
                       "<n> [<percent>] [restart <minutes>]"),
    "remove-private-AS": ("strip private AS numbers on the way out", None),
    "default-originate": ("advertise a default route to this neighbour", None),
}

BGP_BLOCK = {
    "bgp": ("BGP-wide settings", {
        "router-id": ("the identifier this box uses in BGP", "<A.B.C.D>"),
        "log-neighbor-changes": ("log every session state change", None),
        "graceful-shutdown": ("drain traffic before maintenance (RFC 8326)",
                              None),
        "default": ("what a neighbour gets without being told", {
            "ipv4-unicast": ("activate IPv4 unicast on every neighbour",
                             None),
        }),
        "bestpath": ("how the best path is chosen", {
            "as-path": ("AS path comparison", {
                "multipath-relax": ("treat equal-length paths as equal", None),
            }),
        }),
    }),
    "neighbor": ("a BGP session with another router", ARG_NEIGHBOUR),
    "address-family": ("what this box carries with its neighbours", {
        "ipv4": ("IPv4", {"unicast": ("unicast", None)}),
        "ipv6": ("IPv6", {"unicast": ("unicast", None)}),
    }),
    "network": ("a prefix this box originates, which MUST be in the FIB",
                ARG_PREFIX),
    "redistribute": ("inject routes from another source", {
        "connected": ("directly attached networks", None),
        "static": ("configured static routes", None),
        "ospf": ("routes OSPF learned", None),
        "isis": ("routes IS-IS learned", None),
    }),
    "exit": ("leave the BGP configuration", None),
}

BGP_AF_BLOCK = {
    "neighbor": ("what a neighbour carries in this address family",
                 ARG_NEIGHBOUR),
    "network": ("a prefix this box originates in this family", ARG_PREFIX),
    "redistribute": ("inject routes from another source", {
        "connected": ("directly attached networks", None),
        "static": ("configured static routes", None),
        "ospf": ("routes OSPF learned", None),
        "isis": ("routes IS-IS learned", None),
    }),
    "exit-address-family": ("leave the address family", None),
    "exit": ("leave the address family", None),
}

CGNAT_POOL = {
    "inside": ("inside prefix, may repeat", ARG_PREFIX),
    "outside": ("public prefix, may repeat", ARG_PREFIX),
    # <64-65536> was wrong at BOTH ends and the top end was the expensive
    # one: the dataplane carries block_size in a u16, `bpa_cli.c` casts to it,
    # and 65536 arrives as 0 - so the documented maximum came back as "block
    # size 0 must be a power of two", naming a number the operator never
    # typed. 32768 is the largest power of two that fits, and it is also the
    # largest that can fit inside a 65535-port range. The bottom stays at 64
    # because it is the product's own floor and is now enforced, rather than
    # being a number on a screen that the dataplane would have taken 32 past
    # (#352).
    "block-size": ("ports per block, power of two", "<64-32768>"),
    # <min> <max> quoted no numbers, and there were none to quote: `ports`
    # was enforced by nothing at all, so a max below the min, a port above
    # 65535 and a negative one were all taken at the prompt and staged (#361).
    # Both ends come from Pool.RANGES now, like every other numeric statement
    # in this block, and the hint says the same two numbers the parser does.
    "ports": ("usable port range", "<1-65535> <1-65535>"),
    "policy": ("how subscribers spread across public addresses", {
        "interleaved": ("neighbours on distinct public addresses", None),
        "blocked": ("neighbours on the same public address", None),
    }),
    "timeout": ("session lifetimes for this pool; absent inherits the global", {
        "udp": ("seconds", ARG_SECONDS),
        "icmp": ("seconds", ARG_SECONDS),
        "tcp-syn": ("seconds, half-open TCP", ARG_SECONDS),
        "gre": ("seconds, a PPTP call's GRE mapping", ARG_SECONDS),
        "tcp-transitory": ("seconds", ARG_SECONDS),
        "tcp-established": ("seconds", ARG_SECONDS),
        "port-holddown": ("seconds a released port waits", ARG_SECONDS),
        "alg-expect": ("seconds an announced media port waits", ARG_SECONDS),
    }),
    "max-blocks": ("how many blocks one subscriber may hold", "<1-255>"),
    "block-stride": ("blocks between the ones a subscriber holds; 1 puts them "
                     "side by side, 0 lets the pool place them", "<0-65535>"),
    "reserve": ("blocks per public address kept aside so a deterministic "
                "subscriber that fills its block can be given another; "
                "REDUCES CAPACITY and re-maps subscribers", "<0-65535>"),
    "bypass": ("prefixes that leave untranslated", {
        "source": ("a source that must keep its own address", ARG_PREFIX),
        "destination": ("a destination that must see the real source",
                        ARG_PREFIX),
    }),
    "utilisation-alarm": ("log once when a subscriber's block reaches this "
                          "percentage, ahead of exhaustion", "<1-100>"),
    "max-sessions": ("most sessions one subscriber may hold at once; 0 is "
                     "unlimited", ARG_LIMIT),
    "max-session-rate": ("most new sessions per second, per subscriber; 0 is "
                         "unlimited", ARG_LIMIT),
    "tcp-mss": ("lower the MSS of handshakes so segments fit the path", {
        "auto": ("derive it from the CGNAT interfaces' MTUs", None),
    }),
    "forward": ("pin an inbound port to one subscriber; the public address is "
                "the subscriber's own", {
        "tcp": ("public port, then 'to <private-ip> <private-port>'", ARG_PORT),
        "udp": ("public port, then 'to <private-ip> <private-port>'", ARG_PORT),
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
        "esp": ("IPsec ESP passthrough, bound to the peer gateway",
                "<on|off>"),
        "sip": ("SIP over UDP: Via, Contact and the SDP media pair",
                "<on|off>"),
    }),
    "vrf": ("the VRF whose subscribers this pool serves; absent means every "
            "VRF", ARG_VRF_ID),
    "outside-vrf": ("the VRF translated traffic leaves by; absent leaves it "
                    "where it was", ARG_VRF_ID),
    "mode": ("how the block is assigned", {
        "deterministic": ("by formula, no allocation logging", None),
        "dynamic": ("on demand, with allocation logging", None),
    }),
    "exit": ("leave the pool", None),
}


# ---------------------------------------------------------------- negation
#
# What `no <statement>` does in a pool block, one entry per statement that can
# be negated at all. Each entry is (what the removal means, what `?` offers
# after the word, the handler) and the handler returns None or why it was
# refused.
#
# THE RULE THAT DECIDES WHO IS IN HERE, and it is read off the model rather
# than chosen: a pool statement can be negated exactly when `Pool.lines()` is
# able to omit it. `block-size`, `policy` and `mode` are written
# unconditionally - `Pool.__init__` gives each of them a value and there is no
# state in which a pool has none - so `no block-size 512` has nothing to mean.
# Removing the line would not remove the setting; it would only stop the
# configuration from saying what the pool is doing, while the pool goes on
# doing it. Those three are refused, and the refusal says to set the value
# wanted instead.
#
# `ports`, `vrf` and `outside-vrf` go the other way and are new here. Each is
# written only when non-empty, and "" is a state the model already has and
# already means something distinct: the device's own port range, and "every
# VRF", which the model says out loud is NOT the same answer as VRF 0. That
# state was unreachable - once `vrf 7` was typed there was no line that took
# it back, because `vrf 0` means VRF 0. Negating them is not a reset semantic
# invented here; it is the same "drop the override and the line disappears"
# the block already had for `timeout`, `alg`, `max-blocks` and `tcp-mss`, on
# the three fields that had the state and no way to reach it.
#
# The `?` tree for `no` is BUILT from this dict, below, so the two cannot
# drift: a statement is offered after `no` because a handler for it exists,
# and for no other reason.
def _pool_no_item(lst, what, w):
    """`no inside <prefix>` and its siblings: remove one named member."""
    if len(w) < 3:
        return f"usage: no {what} <prefix>"
    if w[2] not in lst:
        # Refused rather than passed over in silence. The operator named a
        # member the pool does not have, which is a typo far more often than
        # it is a second attempt - and the pool still carries the prefix they
        # meant. This is NOT the same case as dropping an override that is
        # already absent (`no timeout udp` on a pool with no udp override),
        # where the state afterwards is exactly the state asked for.
        return f"the pool has no {what} {w[2]}"
    lst.remove(w[2])
    return None


def _pool_no_timeout(p, w):
    if len(w) < 3 or w[2] not in Pool.TIMEOUTS:
        return "usage: no timeout <" + "|".join(Pool.TIMEOUTS) + ">"
    p.timeouts.pop(w[2], None)
    return None


def _pool_no_alg(p, w):
    if len(w) < 3 or w[2] not in Pool.ALG_BITS:
        return "usage: no alg <" + "|".join(Pool.ALG_BITS) + ">"
    p.algs.pop(w[2], None)
    return None


def _pool_no_bypass(p, w):
    """`no bypass source <prefix>`, or `no bypass source` to clear the list."""
    if len(w) < 3 or w[2] not in ("source", "destination"):
        return "usage: no bypass <source|destination> [<prefix>]"
    lst = p.bypass_dst if w[2] == "destination" else p.bypass_src
    if len(w) > 3:
        return _pool_no_item(lst, f"bypass {w[2]}", ["no", f"bypass {w[2]}",
                                                     w[3]])
    lst.clear()
    return None


def _pool_no_forward(p, w):
    """
    `no forward tcp 1100`, and the subscriber when the port alone is not
    enough. The public ADDRESS is the subscriber's own and is never typed, so
    one port names one forward per public address the pool has - and removing
    every one of them would take an inbound port from a customer who was never
    mentioned. The trailing "to <private-ip> <private-port>" is accepted so
    that removing a forward can be the line that added it with "no" in front.
    """
    if len(w) < 4:
        return "usage: no forward <tcp|udp> <port> [to <private-ip> <port>]"
    ip = None
    if len(w) > 5 and w[4] == "to":
        ip = w[5]
    elif len(w) > 4:
        ip = w[4]
    hit = [f for f in p.forwards
           if f[0] == w[2] and str(f[1]) == w[3] and (ip is None or f[2] == ip)]
    if len(hit) > 1:
        # A pair: the caller prints the second row as prose. As one sentence
        # this reaches the console at 163 columns, and the statement the
        # operator has to type back is the last clause of it (#355).
        return (f"forward {w[2]} {w[3]} is configured for more than one "
                f"subscriber",
                "on different public addresses: "
                + ", ".join(f[2] for f in hit),
                f"name the subscriber: no forward {w[2]} {w[3]} to "
                f"<private-ip> <private-port>")
    if not hit:
        return f"the pool has no forward {w[2]} {w[3]}"
    p.forwards = [f for f in p.forwards if f not in hit]
    return None


def _pool_no_clear(attr, value):
    """Set one field back to the value at which `Pool.lines()` omits it."""
    def go(p, _w):
        setattr(p, attr, value)
        return None
    return go


POOL_NEGATE = {
    "inside": ("remove one inside prefix", ARG_PREFIX,
               lambda p, w: _pool_no_item(p.inside, "inside", w)),
    "outside": ("remove one public prefix", ARG_PREFIX,
                lambda p, w: _pool_no_item(p.outside, "outside", w)),
    "ports": ("go back to the device's own port range", None,
              _pool_no_clear("ports", "")),
    "timeout": ("stop overriding one timeout; the pool inherits the global "
                "again", CGNAT_POOL["timeout"][1], _pool_no_timeout),
    "max-blocks": ("go back to one block per subscriber", None,
                   _pool_no_clear("max_blocks", 0)),
    "block-stride": ("let the pool place a subscriber's extra blocks wherever "
                     "it has one free", None,
                     _pool_no_clear("block_stride", 0)),
    "reserve": ("give the reserved blocks back to the formula; this too "
                "re-maps subscribers", None,
                _pool_no_clear("block_reserve", 0)),
    "bypass": ("stop excluding a prefix from translation",
               CGNAT_POOL["bypass"][1], _pool_no_bypass),
    "utilisation-alarm": ("stop alarming on block utilisation", None,
                          _pool_no_clear("util_alarm", 0)),
    "max-sessions": ("no per-subscriber session cap", None,
                     _pool_no_clear("max_sessions", 0)),
    "max-session-rate": ("no per-subscriber session rate cap", None,
                         _pool_no_clear("max_rate", 0)),
    "tcp-mss": ("stop clamping the MSS", None, _pool_no_clear("tcp_mss", "")),
    "forward": ("remove one inbound port forward", CGNAT_POOL["forward"][1],
                _pool_no_forward),
    "filter": ("go back to endpoint-independent filtering", None,
               _pool_no_clear("filter", "endpoint-independent")),
    "alg": ("stop choosing for one ALG; the pool takes the device default "
            "again", CGNAT_POOL["alg"][1], _pool_no_alg),
    "vrf": ("serve every VRF again", None, _pool_no_clear("vrf", "")),
    "outside-vrf": ("leave translated traffic where it was", None,
                    _pool_no_clear("outside_vrf", "")),
}
# The `?` tree, derived. A hand-written copy of this list is the failure this
# repository calls confidently wrong: it would go stale against the handlers
# and then offer a statement that does nothing, which is the defect the
# handlers are here to close (#224).
CGNAT_POOL["no"] = ("remove a setting from this pool",
                    {k: (v[0], v[1]) for k, v in POOL_NEGATE.items()})


CGNAT_CONFIG = {
    "pool": ("create or edit a pool", ARG_POOL),
    "interface": ("enable CGNAT on an interface", "<interface> inside|outside"),
    "timeout": ("session lifetimes", {
        "udp": ("seconds", ARG_SECONDS),
        "icmp": ("seconds", ARG_SECONDS),
        "tcp-transitory": ("seconds", ARG_SECONDS),
        "tcp-established": ("seconds", ARG_SECONDS),
    }),
    "block-holddown": ("seconds a block stays reserved once idle",
                       ARG_SECONDS),
    # The argument hints carry `| disable` because the box takes it and this
    # table is the only place that says what the box takes. It was missing
    # from both: `?` offered `<collector> src <source> [port <n>]` and the
    # usage line in apply_logging offered `nat cgnat logging udp disable`, so
    # the two disagreed about the grammar - which is what a second copy of it
    # always ends up doing (#362). apply_logging now builds its refusal from
    # here, and `show cgnat logging` and docs/logging.md have documented the
    # spelling all along.
    "logging": ("IPFIX export of block events", {
        "enable": ("enable UDP export", None),
        "disable": ("disable UDP export", None),
        "udp": ("where UDP export goes; required before 'enable'",
                "<collector> src <source> [port <n>] | disable"),
        "tcp": ("export over TCP", "<collector> [port <n>] | disable"),
        # The same events for a collector that speaks syslog and nothing
        # else, which is most of them (#20). The enterprise number is part of
        # the shape rather than an option: RFC 5424 reserves a structured-data
        # name without one for IANA, so a line this box writes without it is
        # a line a collector may read as somebody else's.
        "syslog": ("export as RFC 5424 syslog lines",
                   "<collector> enterprise <n> [options] | disable"),
    }),
    # Who held a private address, for the log and for nothing else (#21).
    # A file rather than a statement per subscriber: the list is written by a
    # provisioning system and can hold one line per customer, and a
    # configuration carrying 200000 of them is one nobody can read.
    "identity": ("where this box reads who held a private address",
                 "file <path> | dhcp-leases <path> | "
                 "radius secret <s> [port <n>] | none"),
    "session-table": ("session table sizing", "buckets <n> memory <MB>"),
}


def cgnat_statement_short(words):
    """
    Why `cgnat <words>` stops short of a statement, as prose, or () if not.

    `words` starts at the setting, which the caller has already found in
    CGNAT_CONFIG. The walk ends well only on a leaf (None) or on an argument
    hint with at least one word given for it; the VALUE is the caller's to
    judge, since the hint is prose and not a grammar. Ending on a dict is a
    statement that stopped halfway, and a word the dict does not have is one
    this box does not take - both staged in silence until #431.

    The operator's words go in the prose and not in a verdict, for the width
    #363 gives: they are theirs, and have no widest value.
    """
    node, i = CGNAT_CONFIG[words[0]][1], 1
    while True:
        said = "cgnat " + " ".join(words[:i])
        if node is None:
            return ()
        if isinstance(node, str):
            if len(words) > i:
                return ()
            return (f"'{said}' needs a value: {said} {node}",)
        if len(words) <= i:
            return (f"'{said}' needs one of: " + ", ".join(sorted(node)),)
        if words[i] not in node:
            return (f"'{words[i]}' is not one of them; '{said}' takes: "
                    + ", ".join(sorted(node)),)
        node, i = node[words[i]][1], i + 1

COMMIT = {
    "check": ("validate the candidate without applying it, and report which "
              "subscribers a change would re-map", None),
    "remap": ("apply a change that moves existing subscribers to new public "
              "addresses and port blocks", None),
    "cutoff": ("apply a change that stops advertising the prefix this session "
               "runs over", None),
    "diverge": ("apply a change that maps subscribers differently from the "
                "peer this box shares a service address with", None),
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
# The product's name written out - the brand kit's
# docs/brand/07-ascii/prisma-routing-wordmark.txt, chosen by the maintainer on
# 2026-09-23 (#433) in place of the kit's hexagon signature. Copied rather than
# read at runtime because the kit is not installed on a box. A racked box is
# reached by serial console and that console is 80 columns of whatever font the
# BMC decided on; box drawing and block characters render as noise there, and a
# banner that wraps is worse than none. So: plain ASCII, 70 columns at its
# widest, eight lines - the same height as the signature it replaced, which
# keeps the version, the hint and, on the installation medium, the line telling
# the operator to type "install" where they were. A first candidate was 115
# columns of Unicode blocks and was measured out for exactly that reason.
# test_prismash_units.py holds the width and the character set, so the next
# banner is measured too.
#
# THE NAME IS "Prisma Routing", TWO WORDS (#202). It was HexaRouting until
# 0.76.1, and the rename was a clean break: the command, /etc/prisma-routing,
# /var/log/prisma-routing, the prisma-routing*.service units and the PRISMA_*
# environment all carry the new name, and NOTHING MIGRATES - appliance/upgrade
# refuses a box still carrying the old one. This paragraph used to say the
# opposite: the rename replaced the old names in it too, so it read as if the
# new names were the ones that had been kept.
BANNER = r"""______     _                      ______            _   _
| ___ \   (_)                     | ___ \          | | (_)
| |_/ / __ _ ___ _ __ ___   __ _  | |_/ /___  _   _| |_ _ _ __   __ _
|  __/ '__| / __| '_ ` _ \ / _` | |    // _ \| | | | __| | '_ \ / _` |
| |  | |  | \__ \ | | | | | (_| | | |\ \ (_) | |_| | |_| | | | | (_| |
\_|  |_|  |_|___/_| |_| |_|\__,_| \_| \_\___/ \__,_|\__|_|_| |_|\__, |
                                                                 __/ |
                                                                |___/
""".rstrip("\n")

LIVE_SQUASHFS = os.environ.get(
    "PRISMA_LIVE_SQUASHFS", "/run/live/medium/live/filesystem.squashfs")
INSTALLER = os.environ.get("PRISMA_INSTALLER", "/usr/sbin/prisma-routing-install")


# ---------------------------------------------------------------- roles
#
# Two capabilities, mapped onto system groups rather than a database of this
# appliance's own. An appliance that invents its own accounts is an appliance
# whose accounts nobody deprovisions when somebody leaves.
#
#   prisma-admin   everything: routing, the box's own configuration, and CGNAT
#   prisma-cgnat   CGNAT configuration only
#   neither      read-only
#
# root has both, because root can talk to the dataplane socket directly and
# pretending otherwise would be theatre.
#
# The group names themselves live in system.py, beside the code that puts an
# account in them: two spellings of "prisma-admin" would be two things to keep
# in step, and the day they drifted the VTY would grant a role it had just
# refused to write.
CAP_CGNAT = "cgnat"
CAP_ALL = "configure"


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
DELTA_ADMIN = ("vrrp_set", "vrrp_del",
               "routing_changed", "route_desc_changed", "ssh_changed",
               "dns_changed",
               "users_changed",
               "snmp_changed", "dataplane_if_changed",
               "mtu_set", "mtu_del", "urpf_set", "urpf_del",
               "desc_set", "desc_del", "admin_down", "admin_up",
               "addr_add", "addr_del", "subif_add", "subif_del", "vrf_set",
               "loopback_add", "loopback_del", "lcp_set", "lcp_del",
               "mac_set", "ra_on", "ra_off")


# What `set_ipfix_exporter` needs and the box cannot supply.
#
# VPP's exporter 0 starts ZEROED - measured with ipfix_exporter_dump on an
# untouched dataplane, every field 0 except vrf_id, which is ~0 - and the API
# message has no "leave it alone" encoding: a path MTU of 0 comes back
# refused, retval -7. So there is no default to read off the box, and a caller
# that names a collector has to name these too.
#
# They are VPP's OWN defaults, not this VTY's opinion: measured from a dump
# taken straight after `set ipfix exporter collector <ip> src <ip>` with
# nothing else typed, which is the path that fills them in. They are used only
# where the box has a zero, so a value already in force is never replaced by
# one of these.
#
# The bounds the message enforces, measured the same way: path MTU 68 is
# accepted and 67 is not, 1450 is accepted and 1451 is not, and a source
# address of 0.0.0.0 is refused whatever the collector is.
IPFIX_COLLECTOR_PORT = 4739       # RFC 7011's registered port
IPFIX_PATH_MTU = 512
IPFIX_TEMPLATE_INTERVAL = 20      # seconds between template resends


def udp_export_without_collector(globals_) -> bool:
    """
    Would these statements ask for UDP export with nowhere to send it?

    `cgnat logging enable` turns on VPP's flow-report export, and VPP refuses
    to start a report while its exporter has no collector. Measured on an
    untouched box:

        vppctl bpa logging enable
        bpa logging: no IPFIX collector configured

    The refusal is the dataplane's and it arrives during the apply, with the
    pools already changed - so it is reached here instead, where `commit
    check` can say no before anything is touched. `cgnat logging tcp` does
    NOT satisfy it: the two transports are separate paths in the plugin and
    `enable` names the UDP one.
    """
    words = [g.split() for g in globals_]
    if not any(w[:3] == ["cgnat", "logging", "enable"] for w in words):
        return False
    return not any(w[:3] == ["cgnat", "logging", "udp"] and len(w) > 3
                   and w[3] != "disable" for w in words)


def read_peer_config(path=None):
    """
    The peer's configuration as this box could read it, or WHY it could not.

    Returns `(Config, None)` or `(None, reason)`, and never an empty `Config`
    with no reason. That asymmetry is the whole point of the function: an empty
    configuration compares equal to another empty configuration, so a file that
    is absent, unreadable, truncated or written in statements this VTY cannot
    hold would otherwise be reported as "the two boxes agree". That is the
    silent agreement this check exists to remove, reached from the other side -
    and it is worse than the original, because now something has said so.

    A file that parses but names no pool is refused for the same reason. A box
    in an HA pair has a pool; a peer configuration with none is a copy that did
    not finish, and "the peer maps nobody" is not an answer anybody asked for.
    """
    path = path or PEER_CONFIG_PATH
    if not os.path.exists(path):
        return None, f"there is no peer configuration at {path}"
    try:
        with open(path) as f:
            text = f.read()
    except OSError as e:
        # The reason, not a traceback. This runs in front of somebody in a
        # maintenance window, the same argument RollbackUnavailable makes.
        return None, f"{path}: {e.strerror}"
    cfg = Config.parse(text)
    if cfg.unknown:
        first = cfg.unknown[0]
        return None, (f"{path} line {first[0]} is a statement this VTY cannot "
                      f"read ({first[2]}), so the pool it belongs to is not "
                      "what the peer actually has")
    if not cfg.pools:
        return None, f"{path} names no CGNAT pool"
    return cfg, None


# What FRR does NOT read back the way it was typed.
#
# Measured on FRR 10.7.1 by lab/frr-readback.sh, one statement at a time, each
# undone before the next - and the lab is what keeps this table honest, because
# an FRR upgrade that changes one of these answers fails there rather than
# silently changing what this VTY refuses.
#
# The assumption everybody had, including this file until #197 was measured,
# was "FRR omits a statement whose value equals a default". It does not.
# Omission is per STATEMENT, not per value:
#
#   OMITTED     ip ospf hello-interval 10     ROUNDTRIPS  ip ospf dead-interval 40
#   OMITTED     ip ospf retransmit-interval 5 ROUNDTRIPS  ip ospf cost 10
#
# All four of those are the FRR default. So there is no table of FRR defaults
# to chase across versions, and no arithmetic to get right. There is a short
# list, and it is here.

# Read back as DIFFERENT WORDS meaning the same thing. Keyed on what an
# operator types, valued by what to look for instead.
#
# Both of these were already known to this codebase from somewhere else - the
# first to ospf_findings (#205), the second to route_map_findings (#208) -
# which is the evidence that this list is discoverable rather than open-ended.
# A statement that arrives here having been rewritten is NOT a dropped
# statement, and refusing it would refuse a correct configuration.
# An `ip ospf area <n>` entry was written here and then REMOVED, and the
# refutation is worth more than the entry would have been. A probe appeared to
# show `ip ospf area 0` coming back as `ip ospf area 0.0.0.0` - but that probe
# TYPED the dotted form before it looked for it, so what it found was its own
# keystrokes. Measured properly, one interface per value so nothing typed
# earlier can be what is read later:
#
#   typed `ip ospf area 0`        read back `ip ospf area 0`
#   typed `ip ospf area 5`        read back `ip ospf area 5`
#   typed `ip ospf area 0.0.0.7`  read back `ip ospf area 0.0.0.7`
#
# FRR keeps the form it was given. lab/frr-readback.sh is what caught the wrong
# entry, one run after it was added.
FRR_REWRITES = (
    # (prefix an operator types, how to build what FRR writes instead)
    ("passive-interface ", lambda w: "ip ospf passive"),
    ("continue ", lambda w: "on-match goto " + w[-1]),
)

# Accepted, and left out of the read-back entirely. Matched on the whole
# statement's leading words, because the VALUE is what decides: `ip ospf
# hello-interval 10` is omitted and `ip ospf hello-interval 7` is not, so a
# prefix match alone would excuse a real drop of the second one.
#
# The list is short on purpose. A statement that is not here and not in FRR's
# read-back afterwards is a statement FRR took and did not keep, which is
# exactly what #197 is about - so this list growing by guesswork would turn the
# check back off one entry at a time. Anything added here MUST come with a
# probe in lab/frr-readback.sh.
FRR_OMITS_AT = {
    "timers throttle spf": ("0", "50", "5000"),
    "ip ospf hello-interval": ("10",),
    "ip ospf retransmit-interval": ("5",),
    "bgp default ipv4-unicast": (),
    # OSPFv3, probed when #187 made `router ospf6` something this VTY offers
    # rather than a word it passed through. The three of them are not in the
    # grammar for exactly this reason - an offered statement that comes back
    # missing is a working commit the box refuses - and the entries are here
    # so that an operator who types FRR's own spelling is not refused either.
    # Each has a control at a non-default value in lab/frr-readback.sh 4e.
    "ipv6 ospf6 hello-interval": ("10",),
    "ipv6 ospf6 dead-interval": ("40",),
    "ipv6 ospf6 priority": ("1",),
    # IS-IS, probed because lab/igp.sh configures it and nothing had asked.
    # Each of these has a control in lab/frr-readback.sh at a NON-default
    # value, which is what tells an omission from a refusal: a statement FRR
    # rejected outright also fails to come back, and excusing that would hide
    # the very thing this check exists for.
    "metric-style": ("wide",),
    "isis hello-interval": ("3",),
    "isis metric": ("10",),
}

# The one entry whose answer depends on the REST of the configuration, and the
# reason this is a list of predicates rather than of words.
#
# `neighbor <x> activate` in an ipv4-unicast address family is implied by `bgp
# default ipv4-unicast`, so with that on FRR accepts the statement and writes
# nothing. Turn it OFF - which is what an ISP configuration does, so that a
# peer carries only the families it was given - and FRR stores the activate
# line like any other. Measured both ways:
#
#   bgp default ipv4-unicast on   OMITTED     neighbor 10.9.9.9 activate
#   no bgp default ipv4-unicast   ROUNDTRIPS  neighbor 10.9.9.9 activate
#
# An unconditional entry was written here first, off the first half of that
# measurement alone. It would have excused a GENUINELY dropped activate on
# every box that turns the default off - which is the box where an activate
# line is load-bearing rather than decorative, so the check would have been
# switched off exactly where it was needed. Guessing that an omission is
# unconditional because it was seen once is the same mistake as guessing it
# follows the default value.
#
# Each entry is (does this statement match, is it omitted in THIS
# configuration).
FRR_OMITS_IN_CONTEXT = (
    (lambda w: len(w) >= 3 and w[0] == "neighbor" and w[-1] == "activate",
     lambda ctx: not any(l.strip() == "no bgp default ipv4-unicast"
                         for l in ctx)),
)


# Statements FRR writes back with a `seq <n>` it inserted itself.
#
# Measured, typed with no seq at all:
#
#   ip prefix-list PLNOSEQ permit 10.9.0.0/16
#     -> ip prefix-list PLNOSEQ seq 5 permit 10.9.0.0/16
#   bgp as-path access-list A1 permit ^65001$   -> ... seq 5 permit ^65001$
#   bgp as-path access-list A1 permit ^65002$   -> ... seq 10 permit ^65002$
#   bgp as-path access-list A1 permit ^65003$   -> ... seq 15 permit ^65003$
#
# It is a REWRITE, and one FRR_REWRITES cannot express: the number depends on
# what is already in the list, so the target is not computable from the typed
# statement. It has to be matched by normalising both sides instead.
#
# This is the one that refused a working commit in lab/bgp-policy.sh - the
# policy took, the route it names was filtered, and the box still said the
# statement had been thrown away.
FRR_INSERTS_SEQ = (
    "ip prefix-list ", "ipv6 prefix-list ",
    "bgp as-path access-list ", "bgp community-list ",
    "bgp large-community-list ",
)


def frr_without_seq(stmt):
    """The statement with an inserted `seq <n>` taken back out, or None."""
    if not any(stmt.startswith(k) for k in FRR_INSERTS_SEQ):
        return None
    w = stmt.split()
    for i in range(len(w) - 1):
        if w[i] == "seq" and is_number(w[i + 1]):
            return " ".join(w[:i] + w[i + 2:])
    return stmt


def frr_keeps(stmt, context=()):
    """
    Would FRR store this statement as typed, so that its absence means a drop?

    False for the statements measured NOT to come back - and for those the
    caller must not conclude anything from absence. Everything else is
    expected back verbatim, and absence is a failed commit.

    `context` is the whole routing configuration this statement is part of,
    because one of these answers depends on it: an activate line is omitted
    when `bgp default ipv4-unicast` is on and stored when it is off. Defaulting
    it to empty is deliberate and is the CONSERVATIVE direction - with no
    context the activate case reads as omitted, so the check says nothing about
    a statement it cannot judge rather than refusing a commit on a guess.
    """
    w = stmt.split()
    for matches, omitted_here in FRR_OMITS_IN_CONTEXT:
        if matches(w):
            return not omitted_here(context)
    for k, vals in FRR_OMITS_AT.items():
        kw = k.split()
        if w[:len(kw)] != kw:
            continue
        # No values listed means the statement itself is the default, so it is
        # omitted whenever it appears at all.
        if not vals or tuple(w[len(kw):]) == vals:
            return False
    return True


def frr_writes_instead(stmt):
    """What FRR writes for a statement it rewrites, or None."""
    for prefix, build in FRR_REWRITES:
        if stmt.startswith(prefix):
            return build(stmt.split())
    return None


def statements_frr_dropped(added, running):
    """
    Of the statements this commit introduced, which are not in FRR afterwards.

    This is #197's general check, and it is decidable for the reason
    lab/frr-readback.sh measures: absence means dropped, EXCEPT for a short,
    measured list where FRR writes something else or nothing at all.

    `running` is FRR's own read-back, not frr-reload's idea of what is
    outstanding. That distinction is the whole reason this is possible now and
    was not before: `frr-reload --test` reports a default-valued statement
    under "Lines To Add" for ever, so a check built on it calls every commit
    carrying one a failure. FRR's running configuration does not lie that way -
    it simply does not contain the statement, which is a question this can ask.
    """
    have = {l.strip() for l in running}
    # The same set with any inserted seq removed, so a policy list statement
    # typed without one is found under the number FRR gave it.
    loose = {frr_without_seq(l) for l in have}
    loose.discard(None)
    out = []
    for stmt in added:
        if frr_without_seq(stmt) is not None:
            if frr_without_seq(stmt) not in loose:
                out.append((stmt, None))
            continue
        instead = frr_writes_instead(stmt)
        if instead is not None:
            if instead not in have:
                out.append((stmt, instead))
            continue
        if not frr_keeps(stmt, running):
            continue
        if stmt not in have:
            out.append((stmt, None))
    return out


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
    # The words the `user` block takes, so that a refusal names the rank in
    # the same spelling the operator would type to grant it.
    if CAP_ALL in caps:
        return ROLE_ADMIN
    if CAP_CGNAT in caps:
        return ROLE_CGNAT
    return "read-only"


def running_from_live_medium():
    return os.path.exists(LIVE_SQUASHFS) and os.access(INSTALLER, os.X_OK)


TREE = {
    "operational": {
        "configure": ("edit the candidate configuration", None),
        # Offered rather than hidden. The comment on start_shell() already
        # made this argument: hiding it was never the boundary, the capability
        # check is - and a command an administrator cannot find is one they
        # ask for, which is how this came back (#194).
        "start": ("leave the VTY for a shell, and come back to it", {
            "shell": ("a login shell; administrators only, because a shell "
                      "on this box is every capability at once", None),
        }),
        # REACHABILITY, which is the first question anybody asks of a router
        # and which this VTY could not answer at all until #395: the only way
        # was `start shell`, which is administrators only and is every
        # capability at once. Every account may run these three; they change
        # nothing, and a diagnosis an operator cannot make is a call to
        # somebody who can.
        "ping": ("send echo requests and say what came back", ARG_HOST),
        "traceroute": ("the path to a host, one row per hop", ARG_HOST),
        "mtr": ("ping every hop of the path, as a report", ARG_HOST),
        "show": ("display information", SHOW),
        "clear": ("reset a session, without changing the configuration", {
            "bgp": ("a BGP session", ARG_NEIGHBOUR),
            "ospf": ("OSPF, which has no sessions to reset one at a time", {
                "process": ("tear the process down and build it again; every "
                            "adjacency goes with it", None),
                "neighbor": ("one adjacency", "<A.B.C.D>"),
            }),
            "isis": ("IS-IS adjacencies", {
                "adjacency": ("all of them, or one system id",
                              "[<system-id>]"),
            }),
        }),
        "rollback": ("load a committed revision into the candidate", ARG_REV),
        "save": ("write the running configuration to disk", None),
        # The box itself. Both verbs go through systemd, which is what runs
        # the dataplane's stop-drain on the way down, and each says what it
        # costs before it asks (#388). do_system() carries the argument, and
        # the measurement behind "(root)": prismash runs as the account that
        # logged in, and the appliance grants no other account a reboot.
        # Each help is inside the 67 columns the '?' gutter leaves on a
        # console of 79, and test_prismash_complete.py measures the three.
        "system": ("restart or shut down the box (root); it says what it "
                   "costs first", {
            "reboot": ("every session ends, unsaved changes are lost; comes "
                       "back by itself", None),
            "shutdown": ("every session ends, unsaved changes are lost; the "
                         "box stays off", None),
            "upgrade": ("the newest published release, or download, verify "
                        "and install the one named", "[<version>]"),
        }),
        "exit": ("end the session", None),
    },
    "config": {
        # CGNAT sits under `nat`, and static routes under `router`, because
        # that is where an operator looks for them (#193). Since #446 `nat`
        # is a level that is ENTERED, with `cgnat` a level under it; what is
        # typed there is offered by TREE["nat"] and TREE["cgnat"], which is
        # where `?` is walked from while the operator stands in one.
        "nat": ("address translation: enter it, then 'cgnat'", None),
        "no": ("remove configuration", {
            "dataplane": ("stop handing a card to the dataplane", {
                "interface": ("the box claims no card at the next start of "
                              "the dataplane", None)}),
            "dns": ("stop asking a resolver", {
                "server": ("the last one removed gives /etc/resolv.conf back "
                           "to DHCP or the installer's answer",
                           "<address>")})}),
        "ip": ("IP configuration", {
            "prefix-list": ("a named list of prefixes, for matching in policy",
                            ARG_PREFIX_LIST)}),
        "ipv6": ("IPv6 configuration", {
            "prefix-list": ("a named list of prefixes, for matching in policy",
                            ARG_PREFIX_LIST)}),
        "bgp": ("BGP-wide lists a route-map matches on", {
            "as-path": ("AS path matching", {
                "access-list": ("a named list of AS-path regexes",
                                ARG_AS_PATH_LIST),
            }),
            "community-list": ("a named list of communities",
                               ARG_COMMUNITY_LIST),
            "large-community-list": ("a named list of large communities",
                                     ARG_COMMUNITY_LIST),
        }),
        "rpki": ("origin validation: the caches this box asks", None),
        "router": ("routing, static and dynamic", {
            "static": ("routes this box is told, rather than taught", None),
            "bgp": ("BGP", "<AS>"),
            "ospf": ("OSPF", "[vrf <name>]"),
            # OSPFv3, a separate instance from OSPFv2 and not a mode of it -
            # which is why it is a sibling here and not something under
            # `router ospf`. It was reachable before #187 only by typing it
            # blind, on a box where ospf6d was enabled nowhere.
            "ospf6": ("OSPFv3, the IPv6 one - a separate instance from "
                      "'router ospf'", None),
            "isis": ("IS-IS", ARG_ISIS_TAG),
        }),
        "interface": ("interface configuration", ARG_INTERFACE_NEW),
        # The card the dataplane is GIVEN, which is not one of the interfaces
        # the dataplane has - a fresh appliance has none of those, and the
        # only way to name one used to be editing a file in Linux (#283).
        "dataplane": ("the card handed to the dataplane", {
            "interface": ("the card carrying subscriber traffic, by the name "
                          "Linux gives it", ARG_HOST_NIC)}),
        "dns": ("the resolvers this box asks", {
            "server": ("a resolver, by address; up to 3, asked in the order "
                       "typed", "<address>")}),
        "ssh": ("the SSH daemon", {
            "port": ("TCP port", ARG_PORT),
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
            "port": ("UDP port", ARG_PORT),
            "enterprise-number": ("your organisation's IANA Private "
                                  "Enterprise Number", "<1-4294967295>"),
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
        "role": ("what this account may change; without one it is read-only", {
            ROLE_ADMIN: ("routing, CGNAT, accounts and the box itself", None),
            ROLE_CGNAT: ("CGNAT configuration only", None)}),
        "ssh-key": ("an authorized public key, as it appears in "
                    "authorized_keys", "<type> <key> [comment]"),
        "password": ("a crypt hash, never a plaintext password "
                     "(openssl passwd -6)", "<hash>"),
        "exit": ("leave the user", None),
    },
}

# THE TWO LEVELS UNDER `nat` (#446), as trees of their own so that `?` and
# TAB offer what is typed THERE, and nothing from the level above. The words
# that work at every level - committing, reading, leaving - are the config
# root's own entries, reused rather than copied.
_EVERY_LEVEL = {k: TREE["config"][k] for k in ("commit", "rollback", "show")}
TREE["nat"] = {
    "cgnat": ("carrier-grade NAT: pools, interfaces, logging, timeouts",
              None),
    "exit": ("leave 'nat'", None),
    **_EVERY_LEVEL,
}
TREE["cgnat"] = {
    **CGNAT_CONFIG,
    "no": ("remove CGNAT configuration", {
        "pool": ("remove a pool", ARG_POOL),
        "interface": ("disable CGNAT on an interface", ARG_INTERFACE)}),
    "exit": ("leave 'cgnat'", None),
    **_EVERY_LEVEL,
}

# `no` in a user block, derived from the block's own statements rather than
# listed a second time. All three CAN be negated - do_user has a handler for
# each - so "every statement except `exit`" is the true list and not a
# convenient approximation of one. What stood here was a copy: three entries
# with their own wording, which would have gone stale the first time a fourth
# statement was added, and would then have offered nothing for it (#224).
#
# `no ssh-key` keeps the key as its argument, because a user may have several
# and the removal has to say which.
TREE["user"]["no"] = ("remove a statement from this account",
                      {k: (f"remove: {v[0]}",
                           "<key>" if k == "ssh-key" else None)
                       for k, v in TREE["user"].items() if k != "exit"})

if running_from_live_medium():
    TREE["operational"]["install"] = (
        "install Prisma Routing onto a disk on this machine", None)


def probe_host_ok(host):
    """
    Is `host` something this VTY will hand to a reachability tool (#395)?

    An address of either family, or a hostname in the letters RFC 1123 allows:
    labels of letters, digits and hyphens, none of them empty, none longer
    than 63, and 253 characters in all. Nothing else - and in particular
    nothing carrying a space, a slash, a quote or a shell's punctuation.

    THE VALIDATION IS NOT WHAT KEEPS A SHELL OUT. There is no shell in the
    path at all: the argument vector goes to execve as it is built. This is
    the second wall, and it is here for the operator rather than for the
    attacker - a typo reaching `ping` becomes that tool's own error message
    about a name it could not resolve, where a refusal at the prompt says
    which word this box could not read.
    """
    if not host or len(host) > 253:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    # A trailing dot is a fully qualified name and resolvers take it.
    name = host[:-1] if host.endswith(".") else host
    if not name:
        return False
    for label in name.split("."):
        if not label or len(label) > 63:
            return False
        if label.startswith("-") or label.endswith("-"):
            return False
        for ch in label:
            if not (ch.isascii() and (ch.isalnum() or ch == "-")):
                return False
    return True


def probe_source_ok(addr):
    """
    Is `addr` an address a probe may be sent FROM?

    An address and not a prefix, and not a hostname either: this is what goes
    into the packet's source field, and a name that resolves to two addresses
    would leave the operator reading a reply about whichever one was picked.
    """
    try:
        ipaddress.ip_address(addr)
        return True
    except ValueError:
        return False


def probe_count_ok(word):
    """
    `word` as a probe count, or None.

    None for anything that is not a whole number in 1..PROBE_COUNT_MAX, so the
    caller refuses with the range in the sentence. `int()` alone would take
    "+5", " 5" and "5\n"; the digits check is what keeps the number the
    operator sees and the number the tool gets the same.
    """
    if not word or not is_number(word):
        return None
    n = int(word)
    if n < 1 or n > PROBE_COUNT_MAX:
        return None
    return n


def probe_argv(tool, host, count=None, source=None):
    """
    The argument vector for one probe, and the only place these flags live.

    A function of its arguments and nothing else, so what this VTY runs can be
    asserted without running it - which matters more here than usual: a wrong
    flag on `mtr` is a curses session on a serial console, and a wrong one on
    `ping` is a probe that never stops.

      ping        -c <count>  -n            [-I <source>]  <host>
      traceroute  -q 1 -m <count> -n        [-s <source>]  <host>
      mtr         --report --report-cycles <count> -n [-a <source>] <host>

    `-n` on all three: a reverse lookup per hop, against a DNS server that may
    be exactly what is broken, is how a traceroute takes a minute per line to
    print what it already knew. The addresses are what the operator is reading
    anyway.

    `traceroute`'s count is the hop CEILING rather than a number of probes,
    which is the same question asked of that tool: how far to go before giving
    up. One query per hop, because three is three times the wait for a row
    that is already printed as it arrives.
    """
    path = PROBE_TOOLS[tool][0]
    n = PROBE_COUNT_DEFAULT if count is None else count
    if tool == "ping":
        argv = [path, "-c", str(n), "-n"]
        if source:
            argv += ["-I", source]
    elif tool == "traceroute":
        argv = [path, "-q", "1", "-m", str(n), "-n"]
        if source:
            argv += ["-s", source]
    elif tool == "mtr":
        argv = [path, "--report", "--report-cycles", str(n), "-n"]
        if source:
            argv += ["-a", source]
    else:
        raise KeyError(tool)
    return argv + [host]


def probe_options(words):
    """
    `count <n>` and `source <a>` off the tail of a probe statement.

    Returns `(count, source, bad)`, where `bad` is the first word that is not
    one of those pairs and None when every word was read. The caller decides
    what to say about `bad`, because what is wrong with it differs per tool:
    `mtr` counts cycles and `traceroute` counts hops.

    Written as a reader of PAIRS rather than a flag parser: an option whose
    value is missing is the word itself coming back as `bad`, so `ping 1.1.1.1
    count` is refused for the count it did not carry instead of silently
    running with the default.
    """
    count = source = None
    i = 0
    while i < len(words):
        w = words[i]
        if w in ("count", "source") and i + 1 < len(words):
            if w == "count":
                count = words[i + 1]
            else:
                source = words[i + 1]
            i += 2
            continue
        return count, source, w
    return count, source, None


def _starts(candidate, prefix):
    """
    Does `candidate` begin with `prefix`, ignoring case?

    A card carries its Linux name, `ens19`, and a device nobody named keeps
    VPP's own spelling, `TenGigabitEthernet0/8/0` (#389). Requiring the
    capital T before completion will help is completion that only works for
    somebody who already knows the answer (#112).

    The candidate is returned unchanged by the caller, so what gets inserted is
    always the box's own spelling - completing "gig" to "gig" would be worse
    than not completing.
    """
    return candidate.lower().startswith(prefix.lower())


def shaped_entry(node, word):
    """
    The entry of `node` whose VALUE_SHAPES key `word` is shaped like, or None.

    What lets the tree walk past a word it could never list - a prefix - so
    that `?` offers `next-hop` after one and a static route is marked as
    read, not as handed to FRR unread (#436).
    """
    for key, shaped in VALUE_SHAPES.items():
        if key in node and shaped(word):
            return node[key]
    return None


def tree_walk(mode, words):
    """Where we are in the tree after `words`; (options, argument hint)."""
    return walk_from(TREE.get(mode), words)


def walk_from(node, words):
    """
    The same walk, from any node rather than from a mode's root.

    Blocks entered by an argument - `interface <name>`, `router bgp <asn>` -
    are not reachable from a mode root, so their grammar could only ever be
    offered for the FIRST word. Everything after it fell through to the
    configuration tree, which does not contain `address` or `remote-as`, and
    the operator got nothing.
    """
    hint = None
    for w in words:
        if not isinstance(node, dict):
            return None, hint
        entry = node.get(w) or shaped_entry(node, w)
        if entry is None:
            return None, None       # unknown word: FRR territory from here
        _, sub = entry
        if isinstance(sub, str):
            return None, sub
        node = sub
        hint = None
    return node, hint


def takes_a_value(node):
    """
    Does this node of the tree also accept a word of the OPERATOR's, not only
    words of the box's?

    A dynamic key - `<interface>`, `<pool>`, `<user>` - rides in the dict
    beside the keywords, so `show interface` offers `terse` AND every
    interface on the box. At such a node the box cannot tell its own word from
    the operator's, and resolving there would eat the operator's: `show
    interface Te` would become `show interface terse` on a box whose card is
    called TenGigabitEthernet0/8/0, and the operator would be shown a summary
    of every interface instead of the one they asked about. Abbreviating a
    VALUE is not what this does (#393), so a node that takes one resolves
    nothing and hands the rest over as typed.
    """
    return any(k.startswith("<") and k.endswith(">") for k in node)


def resolve_statement(node, words):
    """
    `words`, with every abbreviation of THIS VTY's own statement words spelled
    out; or the ambiguity that stopped it.

    Returns `(words, None)` on success and `(None, (typed, candidates))` when
    one of the words begins more than one statement. The walk is the same one
    `?` and TAB do, down the same `TREE`, so what the box reads and what the
    box offers cannot disagree - a second list of abbreviations would be a
    second list of statements, and would go stale the first time a statement
    was added (#393).

    Four rules, and the last three are all about NOT resolving:

      * a word the node HAS is that word. Exact wins, always, which is what
        keeps `snmp` from being read as `snmp-user` and `ip` from being read
        as `ipv6`.
      * a word that begins exactly one of the node's is that one.
      * a word that begins more than one is an ambiguity, and the caller
        refuses it naming the candidates rather than picking by position.
      * a word that begins NONE of them, and every word after it, is handed
        over exactly as typed. That is the whole of the FRR half: this tree
        holds no entry for `bandwidth` or `link-detect`, so neither is
        touched, and a statement that stops being the box's stops being
        resolved with it.

    NOTHING FRR USES MAY BE SHADOWED, and the last rule is what holds it: a
    shadow needs an FRR statement that is a STRICT PREFIX of one of this
    tree's own words, and is not itself in the tree. Measured over every
    statement written inside an `interface` block in lab/, docs/ and wiki/
    plus FRR's own spellings this tree names, there is none, and
    test/test_prismash_refusals.py asserts the whole corpus reaches FRR
    unchanged so that a statement added later cannot introduce one quietly.
    Where one did appear, the answer is the third rule and not the second:
    refuse the word and make the operator spell it, because resolving it
    would silently change a statement FRR was going to act on.
    """
    out = []
    for i, word in enumerate(words):
        if not isinstance(node, dict) or takes_a_value(node):
            out.extend(words[i:])       # an argument, or the operator's word
            return out, None
        if word in node:
            spelled = word
        else:
            hits = sorted(k for k in node if _starts(k, word))
            if not hits:
                out.extend(words[i:])   # FRR territory from here
                return out, None
            if len(hits) > 1:
                return None, (word, hits)
            spelled = hits[0]
        out.append(spelled)
        node = node[spelled][1]
    return out, None


def block_grammar(block, words):
    """
    The grammar this VTY has for the statements of `block`, or None.

    None means the VTY has no grammar for the block at all - `vrf red`, `key
    chain KC-CORE`, anything FRR takes that this tree was never taught. It is
    not the same as an empty grammar, and the difference is the whole point:
    the completer turns None into "`exit` and nothing else" so an operator is
    never stranded, while the boundary marker turns it into "every statement
    in here went to FRR unread" (#141).

    `words` is needed because one block has two grammars: the words after a
    neighbour's ADDRESS are a different set from the words that start a line.

    This is the ONE walk that decides what `?` offers inside a block. The
    marker asks it rather than carrying a list of what is modelled, because a
    second list would have to be kept in step with the grammar by hand and
    would eventually disagree with it - and a marker that disagrees with the
    tree says a statement was checked when nothing checked it.
    """
    if block.strip() == STATIC_BLOCK:
        return STATIC_BLOCK_GRAMMAR
    if block.startswith("interface "):
        return INTERFACE_BLOCK
    if block.strip() == "rpki":
        return RPKI_BLOCK
    if block.split()[:2] == ["router", "isis"]:
        return ISIS_BLOCK
    if block.split()[:2] == ["router", "ospf"]:
        return OSPF_BLOCK
    # Before #187 this fell through to the "block this VTY does not model"
    # answer, so `router ospf6` offered `exit` and nothing else - on a
    # statement the box accepted and committed.
    if block.split()[:2] == ["router", "ospf6"]:
        return OSPF6_BLOCK
    if block.startswith("route-map "):
        return ROUTE_MAP_BLOCK
    if block.startswith("address-family "):
        base, neigh = BGP_AF_BLOCK, BGP_AF_NEIGHBOUR
    elif block.startswith("router bgp"):
        base, neigh = BGP_BLOCK, BGP_NEIGHBOUR
    else:
        return None
    # `neighbor <address> <word>`: the address is an argument, so the tree
    # cannot walk through it. After it, the statements are the neighbour's.
    if len(words) >= 2 and words[0] == "neighbor":
        return {"neighbor": ("a BGP session", {words[1]: (
            f"statements for {words[1]}", neigh)})}
    return base


def statement_checked(stmt, ctx):
    """
    Did the command tree read this routing statement, or did it just carry it?

    `ctx` is the block path the statement sits in, outermost first, as
    `block_paths` reports it. Answered BY WALKING THE TREE - the same walk
    `?` does - and never by a list of known statements kept alongside it.

    True means the tree recognised every word it is responsible for. It stops
    at an argument, which is the honest place to stop: the tree says a prefix
    goes after `network`, and what the operator typed there is checked (or
    not) by whatever validates prefixes, not by this walk. False means a word
    the tree has no entry for, which is the moment the statement stopped being
    understood and became something to hand over verbatim.

    Statements at the TOP of the routing section are a deliberate hole and it
    is not laziness. The tree's `config` mode is the grammar for what may be
    TYPED at `[edit]`, which is not the same set as the statements the routing
    section may HOLD: `route-map RM permit 10` is absent from it and yet every
    statement inside that block is checked, and `ip route ...` is accepted and
    deliberately not offered (#193). Marking from that tree would put "not
    checked" on the header of a block this VTY checks line by line, which is
    the "confidently wrong" failure pointing the other way. So a top-level
    statement is checked when the tree walks it OR when this VTY has a
    grammar for the block it opens, and a top-level statement that is neither
    is marked.
    """
    words = stmt.split()
    if not words:
        return True
    if ctx:
        grammar = block_grammar(ctx[-1], words)
        if grammar is None:
            return False        # no grammar for the block: nothing was read
        return _tree_reads(grammar, words)
    if opens_frr_block(stmt) and block_grammar(stmt, []) is not None:
        return True
    # A static route in FRR's words. It is the `router static` block this VTY
    # provides and translates, and it is checked by that block's grammar - but
    # only the DISPLAY puts it back in the block, so the same route answers
    # "checked" on the screen and "not checked" in the candidate list `commit
    # check` counts. Measured: `route 0.0.0.0/0 10.0.0.2` typed inside `router
    # static` is stored as `ip route 0.0.0.0/0 10.0.0.2` at column 0, so every
    # static route on the box would have been counted as passed through
    # unread.
    if is_static_route(stmt.strip()):
        return True
    return _tree_reads(TREE["config"], words)


def _tree_reads(node, words):
    """
    Did this node of the tree recognise every word of `words`?

    A walk of its own rather than `walk_from`, which cannot answer this
    question: `walk_from` returns (None, None) BOTH for a word the tree has no
    entry for and for a statement it walked to the end of, because a leaf's
    subtree is None. Completion does not care - it has nothing more to offer
    either way - but the difference is exactly what the boundary marker is
    asking about, and reusing that return marked `bgp log-neighbor-changes`,
    a statement the tree defines, as one the VTY never read.
    """
    for word in words:
        if isinstance(node, str):
            return True         # an argument: the rest is its business
        if node is None:
            return False        # the statement ended here and went on anyway
        entry = node.get(word) or shaped_entry(node, word)
        if entry is None:
            return False
        node = entry[1]
    # `node` may still be a dict here, on a statement that stops halfway down
    # the grammar. Every word it HAS was read, which is what this answers; a
    # statement that is only a prefix of one is a different complaint.
    return True


class Vpp:
    """Talks to the bpa plugin through VPP's CLI."""

    #: set PRISMA_TRACE=1 to see every command sent to the dataplane. A check
    #: that shows a mutating command here is a bug, not a nuance.
    TRACE = os.environ.get("PRISMA_TRACE") == "1"

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
        # splitlines(), not split("\n"): vppctl ends its lines with CR+LF, and
        # split("\n") leaves the carriage return on the line this then matches
        # an error pattern against. Nothing has failed because of it yet - the
        # rest of this file uses splitlines() throughout - but a value carrying
        # an invisible \r compares unequal to the same value without one, and
        # that is a bug nobody can see in the output.
        first = out.splitlines()[0] if out else ""
        return (1 if (p.returncode or VPP_ERROR.match(first)) else 0), out

    def reachable(self):
        """
        Is the dataplane answering at all - as opposed to answering "no"?

        Measured, and the distinction is the one this file was missing: vppctl
        exits 0 whenever VPP ANSWERED, including when VPP refused the command,
        and exits 1 only when it could not reach VPP. So its exit status is
        exactly "did I reach the dataplane", and run() above was discarding it
        and reconstructing an answer from the text instead - which is how
        "Could not find VPP CLI socket" reached Config.parse() as a line of
        configuration (#211). It starts with a capital letter and carries no
        colon, so the pattern that recognises a VPP error does not match it.
        """
        return self.run("show version")[0] == 0

    #: The bpa plugin's row in `show plugins`, and the version field of it.
    #
    # MEASURED, VPP 26.06, rather than guessed at:
    #
    #      Plugin                                   Version         Description
    #   8. bpa_plugin.so                            0.1             CGNAT with...
    #
    # with the name starting at column 5, the version at 46 and the description
    # at 79. The field is therefore read as "everything between the plugin's
    # file name and the run of two or more spaces that ends it", not by column
    # offsets and not by splitting on whitespace. Both of those looked simpler
    # and both are wrong here: the stamp this product puts in that field is
    # `0.71.0 (d07ebda)`, which CONTAINS A SPACE, so `line.split()[2]` returns
    # "0.71.0" and silently drops the commit - the half that says which build
    # it is. Column offsets would pin this to one VPP release's formatting.
    #
    # The plugin's own registration is bounded at 31 characters by a
    # STATIC_ASSERT in bpa.c, which is what guarantees the two spaces are
    # there. A wider stamp fails the build rather than this parser.
    # The field MUST be closed by two or more spaces, with no "or the end of
    # the line" alternative, and that is the whole reason an EMPTY version is
    # reported as one rather than as a description. With the alternative, a
    # row whose version column is blank matches the description instead and
    # `show version` reports "CGNAT with allocated port blocks (BPA)" as the
    # build the dataplane is running. Measured on the real layout, which is
    # what made the trap visible.
    PLUGIN_ROW = re.compile(r"\bbpa_plugin\.so\s{2,}(\S.*?)\s{2,}\S", re.M)

    def plugin_build(self):
        """
        The build the LOADED bpa plugin was made from - three answers, not two.

        `None`  the dataplane did not answer at all.
        `""`    it answered, and it is not running a plugin that reports a
                build: bpa_plugin.so was never loaded, or it is one whose
                version field is empty.
        a string  the stamp the plugin carries.

        The split between the first two is the one `Frr.daemons()` argues for
        a few hundred lines up, for the same reason and with the same cost of
        getting it wrong. A dataplane that cannot be reached is a box that is
        forwarding nothing, and the fix is to get VPP back; a dataplane that
        is up without our plugin is a box that routes and translates nothing,
        and the fix is in the plugin path or in VPP's own log. An operator who
        reads the first as the second looks in the wrong file.

        This is read from `show plugins` and not from a command of our own on
        purpose. The reason is the boxes this has to work on: every build ever
        shipped registered `.version = "0.1"` (#291), and `show plugins` is
        the one place that answers on all of them. A new command would answer
        "unknown input" on exactly the dataplane this is here to identify -
        the old one still loaded after an upgrade.
        """
        rc, out = self.run("show plugins")
        if rc != 0 or not out.strip():
            return None
        m = self.PLUGIN_ROW.search(out)
        return m.group(1).strip() if m else ""


def mark_routing(lines, ctx=()):
    """
    Routing statements with the ones this VTY did not read marked as such.

    `ctx` is the block path the lines sit in, empty at the top of the routing
    section and the open blocks when `show this` prints the inside of one.
    Index for index, so a caller can print them in place.

    DISPLAY ONLY, and that is not a caveat - it is the design. The marker must
    never reach `Config.text()`, the saved configuration, a rollback revision
    or the lines handed to frr-reload. MEASURED at a live FRR 10.7.1, rather
    than assumed:

        probe(config-router)# coalesce-time 2000  <- not checked
        % Unknown command: coalesce-time 2000  <- not checked

    and in a FILE, which is the shape the boot takes, vtysh refuses it before
    any daemon sees it:

        ERROR: vtysh failed to process new configuration: vtysh (mark file)
        exited with status 2: line 5: % Unknown command:  coalesce-time 1000
        <- not checked

    The same file without the marker gets past that stage. A configuration
    file the box reads back at boot that FRR refuses is a box that comes up
    with no configuration at all, which is how both boot defects behind #182
    ended - worse than the problem this solves.
    """
    out, n = [], 0
    paths = block_paths(list(lines))
    for line, path in zip(lines, paths):
        # `None` is not a statement of the configuration: a blank, one of
        # FRR's `!` separators, or a block's own `exit`. Marking an `exit`
        # would say the VTY failed to understand the word it printed itself.
        if path is None or statement_checked(line.strip(), list(ctx) + path):
            out.append(line)
            continue
        n += 1
        out.append(f"{line}{UNCHECKED_MARK}")
    return out, n


#: How many unread statements a refusal names before it stops naming them.
#:
#: A refusal that prints forty lines is a refusal nobody reads to the end of,
#: and the box already has a place that shows every one of them: `show
#: configuration` marks them in place. Five is what fits on a console beside
#: the verdict, FRR's own words and the way out.
UNREAD_NAMED = 5


def unread_statements(lines):
    """
    The statements of `lines` this VTY passed through without reading.

    The same two functions `mark_routing` calls, in the same order, because the
    question is the same question: a refusal that named a different set from
    the one `show configuration` marks would send an operator looking at a
    statement the marker says was checked.
    """
    out = []
    for line, path in zip(lines, block_paths(list(lines))):
        if path is None or statement_checked(line.strip(), path):
            continue
        out.append(line.strip())
    return out


def console_prose(text, indent="    "):
    """
    A message's OWN words, wrapped to the console by measurement.

    Not the statements the message quotes, and that split is the rule (#252).
    It follows from what an operator does with each half. Prose is read once
    and then it is over, so where it breaks does not matter as long as it
    breaks inside the screen. A quoted statement is the opposite: it is
    grepped for in `show configuration` or typed back in at the prompt, and
    both of those want the bytes whole and on one line.

    So a statement is printed as it stands, past the margin if it has to be:

      * WRAPPED, it stops being greppable. The newline this code would insert
        is a real newline in the scrollback and in anything the operator pipes
        the session through, and it lands inside a token as often as not.
        `neighbor 2001:db8:aaaa:bbbb:cccc:dddd:eeee:ffff route-server-client`
        broken after column 59 is a string that matches nothing the operator
        can search for, including itself.
      * TRUNCATED, it stops being typeable, and it loses the end. #251 put the
        block at the FRONT of the line because a VRF route has no top-level
        spelling to look for, and what FRR declined is usually the last word
        of the statement. Cutting either end throws away the half that was
        worth printing, and which half that is cannot be known here.
      * Left alone, the terminal soft-wraps it onto a second row and every
        byte survives, in order, as one logical line. That costs the SHAPE of
        the message, which is the cheapest of the three things on offer.

    WHY this is a function and not the hand-wrapped print() calls it replaces:
    that refusal was wrapped by eye and shipped an 82-column line and a
    178-column line under the belief that both were inside 80 (#252). Wrapping
    by eye cannot be reviewed and cannot be re-measured after an edit, and
    what it is protecting is the message that says a commit did not take
    effect, read on the console an operator opened because routing is down.

    A word is never split here either, so prose carrying a token longer than
    the budget runs over rather than being cut in half. Deliberate: the long
    tokens in these messages are commands the reader is meant to retype, such
    as `vtysh -c "show running-config"`. The width assertion over these
    messages is what says nobody has since written a longer one.
    """
    return textwrap.wrap(text, width=CONSOLE_COLUMNS,
                         initial_indent=indent, subsequent_indent=indent,
                         break_long_words=False, break_on_hyphens=False)


#: The two words this box prints in a column instead of a value it has not
#: got. Declared rather than typed at each site so the width reader can see
#: that the column holds an address or one of these and nothing else.
COLUMN_NONE = "-"
COLUMN_UNKNOWN = "?"


#: Whether FRR holds a route, as the `FRR` column spells it. A pair rather
#: than a conditional at the site: the width reader bounds a slot by the
#: tightest declared collection that holds every value it saw, and a column
#: that is one of two words has a widest of four characters.
RIB_COLUMN = ("yes", COLUMN_NONE)


def rib_column(in_rib):
    """`RIB_COLUMN`'s word for a route FRR holds, or does not."""
    return RIB_COLUMN[0] if in_rib else RIB_COLUMN[1]


def next_hop_column(via):
    """
    What goes in a route screen's `Next hop` column for `via`.

    VPP's FIB names the path where a next hop would be for a route that has
    none: `connected` for a prefix on an interface, `local` for the box's own
    address. Printed in that column they are two things at once - they are not
    next hops, and they are already what the `VPP` column says about the same
    route - and they are what took the column's widest away, because a column
    of addresses with two English words in it has no widest at all (#392).

    So the column carries an address or `-`, and what kind of route it is
    stays in the one column that is about that.
    """
    try:
        ipaddress.ip_address(via)
        return via
    except ValueError:
        return COLUMN_NONE


def route_column_widths(rows, headings, indent=2, gutter=1, tail=0):
    """
    Column widths for a route screen, or None when the widest row cannot fit.

    MEASURED OFF THE ROWS THE BOX ACTUALLY HAS, which is the whole of it. The
    route screens padded their columns to 20, 17 and 12, numbers that fit an
    IPv4 box and nothing else: an IPv6 prefix is 43 columns at its widest, a
    next hop 39 and an interface name 28, so one v6 route ran past the console
    and the terminal folded it INSIDE the columns - where a wrapped next hop
    and a second route look the same (#392).

    A screen with nothing in it wider than the console is a table, and that is
    what an operator reads down. A screen with one route too wide for it is
    not a narrower table - the value is the value - so the caller is told None
    and prints each entry as a block whose rows end in their value, where a
    fold costs nothing.

    `tail` is what the caller puts after the last column - the `** in FRR, NOT
    forwarding` mark - counted here because a table that fits until the mark
    appears is a table that does not fit.
    """
    if not rows:
        return None
    widths = [max(len(str(h)), max(len(str(r[i])) for r in rows))
              for i, h in enumerate(headings)]
    if indent + sum(widths) + gutter * (len(widths) - 1) + tail \
            > CONSOLE_COLUMNS:
        return None
    return widths


def sigint_ignored(fn):
    """
    `fn`, with Ctrl-C ignored while it runs - for what must not stop halfway.

    A terminal sends SIGINT to the whole foreground process group: to this VTY
    and to the frr-reload, the vtysh and the ip a commit is running. A Python
    handler would protect this process alone, because exec resets a caught
    signal to its default in the child, and the child would die with half a
    configuration applied. SIG_IGN is the one disposition exec keeps, so it is
    what makes the children immune too (#441). The price is that the key does
    nothing visible while a commit applies: the terminal echoes ^C, and the
    commit finishes and says what it did. A commit half applied because
    somebody hit a key while it was slow is worse.

    Only on the main thread, where Python allows a handler to be set; the
    previous disposition is put back whatever `fn` does.
    """
    import functools
    import threading

    # not a unit: the function sigint_ignored() returns, and it is driven
    # through sigint_ignored() by name in test_prismash_units.py.
    @functools.wraps(fn)
    def held(*a, **k):
        if threading.current_thread() is not threading.main_thread():
            return fn(*a, **k)
        before = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            return fn(*a, **k)
        finally:
            signal.signal(signal.SIGINT, before)
    return held


def print_prose(text, indent="    "):
    """`console_prose`, printed. The refusals say a paragraph at a time."""
    for line in console_prose(text, indent):
        print(line)


def ssh_host_keys_handed_to_this_box(origin=None):
    """
    The host keys this box was handed inside its system image, and the repair.

    `(names, commands)`, both empty when the box made its own keys - and both
    empty when the answer could not be had, which is the one thing this
    function decides for itself and the reason it is worth a docstring.

    THE DECISION IS NOT HERE. It is in appliance/ssh-host-key-origin, run as a
    program, because appliance/selftest reaches the same answer on the same
    box at boot and a rule implemented twice is a rule that can disagree with
    itself about whether an operator is being impersonated. That file's header
    is where the timestamp signal, the rejected alternatives and the three
    commands live.

    SILENT WHEN IT CANNOT RUN, which is the opposite of what the boot
    self-test does with the same failure, and the difference is deliberate.
    The self-test's job is to report on the box, so a check it could not run
    is a finding and it says so. This is a banner: it speaks only when it has
    something to say, like the 'commit confirmed' notice beside it, and it
    never tells an operator their keys are fine. A line drawn on every login
    of every healthy box is a line read once and skipped for ever, including
    on the box where it means something - the argument `unchecked_note` and
    `show_plugin_build` both make for the same decision.
    """
    try:
        r = subprocess.run([origin or SSH_HOST_KEY_ORIGIN],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           universal_newlines=True)
    except OSError:
        return [], []
    if r.returncode != 0:
        return [], []
    handed, fixes = [], []
    for line in r.stdout.splitlines():
        word, _, rest = line.partition(" ")
        if word == "handed":
            handed = rest.split()
        elif word == "fix" and rest:
            fixes.append(rest)
    return handed, fixes


def unchecked_note(n):
    """
    The sentence under a marked configuration, or nothing when none is marked.

    Two jobs the per-line marker cannot do. It says what the marker MEANS -
    the mark itself is sixteen characters because an FRR statement plus a
    sentence does not fit the 80 columns of a serial console, and a marker
    that wraps is a marker that stops being read. And it gives the COUNT,
    which is the number an operator reviewing a change before a maintenance
    window actually wants: "two of these went through unread" is a different
    decision from "thirty did".

    Nothing at all when the count is zero, and that is the rule from #205 and
    #208 applied here: passing statements through is the normal case for FRR
    configuration, so a line printed under every configuration would be read
    once and skipped forever - including on the configuration where it says
    something.
    """
    if not n:
        return []
    return [f"{UNCHECKED_NOTE} {n} statement(s) above are marked "
            f"'{UNCHECKED_MARK.strip()}': this VTY has no",
            f"{UNCHECKED_NOTE} grammar for them and handed them to FRR word "
            "for word"]


def unchecked_in(routing):
    """
    The statements of a routing configuration this VTY never read, as a set,
    for a caller that names one out of place.

    `mark_routing` answers index for index and is right for a display that
    prints the whole section. A commit refusal prints five statements out of
    the candidate, with their blocks stripped off by `routing_added`, so it
    needs the same answer keyed by the statement instead.

    It is produced BY `mark_routing` rather than beside it, and that is the
    point of the function existing at all: a refusal that named a statement
    as unread while `show configuration` printed it clean would be two answers
    to one question, which is the shape #141 refused when it made the marker
    read the command tree instead of a word list kept next to it.

    A statement whose words appear in two blocks counts as read when ANY
    occurrence was read. `neighbor <ip> soft-reconfiguration inbound` is
    modelled inside `address-family ipv4 unicast` and not inside `router bgp`,
    so the same words get two answers in one configuration - and "this VTY
    never read it" said over a statement it did read somewhere is the
    confidently wrong direction of exactly the mistake the marker exists to
    stop.
    """
    marked, _ = mark_routing(list(routing))
    read, unread = set(), set()
    for line, shown in zip(routing, marked):
        (unread if shown.endswith(UNCHECKED_MARK) else read).add(line.strip())
    return unread - read


def mark_unchecked(lines):
    """
    A whole configuration with the routing statements this VTY did not read
    marked, and a sentence at the end saying how many there were.

    WHY IT IS ONLY THE ROUTING HALF: everything above `! routing` is modelled
    by construction. `Config.parse` records a line it cannot hold in
    `unknown`, `commit` refuses to run while there is one, and the dataplane
    reads its half back statement by statement. There is no passthrough there
    to mark, and marking is a claim - "this part was checked" has to stay
    worth something or the marker degrades into decoration.

    The routing half is the opposite: it is a list of verbatim FRR statements,
    most of which this VTY has never read. Before this, a statement the
    command tree validated and a statement it forwarded blind printed
    identically, so an operator could not tell what the box had checked from
    what it had merely carried - and read a guarantee into both (#141).
    """
    try:
        cut = lines.index(ROUTING_HEADER)
    except ValueError:
        return list(lines)
    head, routing = lines[:cut + 1], lines[cut + 1:]
    marked, n = mark_routing(routing)
    return head + marked + unchecked_note(n)


def as_path_parts(line):
    """
    The list name and the regex in a `bgp as-path access-list` line, or
    (None, None) if it is not one.

    Found by the keyword, not by position. FRR gives the statement back with a
    `seq` it assigned - `bgp as-path access-list RX1 seq 5 permit ^65001$` -
    and a parser counting words from the left read `5 permit ^65001$` as the
    regular expression. Two consequences, both silent: a regex read back from
    the box was never checked, and every match count was taken against a
    pattern nobody wrote. The cross-check against FRR did not catch it because
    it feeds the statement in the form an operator TYPES, which has no `seq`.
    """
    w = line.split()
    if len(w) < 5 or w[:3] != ["bgp", "as-path", "access-list"]:
        return None, None
    name = w[3]
    for i in range(4, len(w)):
        if w[i] in ("permit", "deny"):
            return name, " ".join(w[i + 1:])
    return None, None


def asn_findings(routing):
    """
    Every AS number in `routing` that is not one, as (severity, stmt, why).

    BOTH DOORS with one reader, which is why this is here and not only in the
    prompt: a statement reaches `routing` the same way whether it was typed or
    loaded out of a saved file, so a check at commit covers the file that the
    prompt refusal cannot. The prompt refuses `router bgp <asn>` as well,
    because a block entered is a block the next statement lands in.

    Four places an AS number is written, and `remote-as` is the one that is
    not positional: `neighbor vppwan0 interface remote-as external` is BGP
    unnumbered, where the value is a word and not a number. `internal`,
    `external` and `auto` are real FRR spellings and are kept - reading them
    as a bad AS is how #208 refused the ordinary way to run eBGP to a directly
    connected peer.
    """
    out = []
    words_kept = ("internal", "external", "auto")
    for line in routing:
        w = line.split()
        if not w:
            continue
        if w[:2] == ["router", "bgp"]:
            why = asn_error("router bgp", w[2] if len(w) > 2 else "")
            if why:
                out.append(("error", line.strip(), asn_why(why)))
                continue
            note = asn_note(w[2])
            if note:
                out.append(("warning", line.strip(), note))
        elif "remote-as" in w:
            at = w.index("remote-as")
            value = w[at + 1] if at + 1 < len(w) else ""
            if value in words_kept:
                continue
            why = asn_error("remote-as", value)
            if why:
                out.append(("error", line.strip(), asn_why(why)))
                continue
            note = asn_note(value)
            if note:
                out.append(("warning", line.strip(), note))
        elif w[:2] == ["set", "as-path"] and len(w) > 3 \
                and w[2] in ("prepend", "exclude"):
            # `set as-path prepend last-as <n>` is a count and not an AS, so
            # it is left alone. Everything else after the verb is a list of AS
            # numbers.
            if w[3] == "last-as":
                continue
            for value in w[3:]:
                why = asn_error(f"set as-path {w[2]}", value)
                if why:
                    out.append(("error", line.strip(), asn_why(why)))
                    break
    return out


def as_path_regex_error(line):
    """
    Why FRR would refuse this AS-path regex, or None.

    FRR compiles the regex when the statement is APPLIED, which is mid-commit -
    the worst moment to find out. Compiling it here moves that to the prompt.

    POSIX extended regular expressions, which is what FRR uses, and what
    Python's `re` accepts is close but not identical. So this refuses only what
    fails to compile in BOTH readings, and lab/routing.sh puts a set of them to
    FRR itself and requires the two to agree - the same arrangement as the
    prefix-range rule, and for the same reason: an opinion about what FRR
    accepts that drifts from FRR refuses configurations that are fine.
    """
    name, regex = as_path_parts(line)
    if name is None:
        return None
    if not regex:
        return "no regular expression: 'permit' or 'deny' takes one"
    try:
        re.compile(regex)
    except re.error as e:
        return f"the regular expression does not compile: {e}"
    return None


def as_path_matches(regex, paths):
    """
    How many of `paths` the regex matches, and one that it did.

    FRR matches an AS path regex against the path rendered as a
    space-separated string of AS numbers, with `_` standing for a boundary.
    That underscore is the whole reason this exists as a report rather than a
    refusal: `permit 65001` compiles, and matches 165001 and 650012 as well,
    which is the mistake nobody sees by reading their own regex back.
    """
    # FRR's `_` is "any boundary": start, end, or a separator.
    try:
        rx = re.compile(regex.replace("_", "(^|$|[,{}()\\s])"))
    except re.error:
        return 0, None
    hit, example = 0, None
    for path in paths:
        if rx.search(path):
            hit += 1
            if example is None:
                example = path or "(empty: originated in the neighbour's AS)"
    return hit, example


def list_name(w):
    """
    The name a `bgp community-list` / `bgp large-community-list` declares.

    `standard` and `expanded` are the type, not the name, and they are
    optional in what an operator types - FRR 10.7.1 puts one of them back on
    the way out, so both spellings reach this and only one of them is ever
    read back off the box. Returns None when the statement stops before the
    name, which is what a half-typed line looks like.
    """
    if len(w) >= 3 and w[2] in ("standard", "expanded"):
        return w[3] if len(w) >= 4 else None
    return w[2] if len(w) >= 3 else None


def undefined_policy_lists(routing):
    """
    Names a route-map matches on that nothing in the configuration defines.

    Same failure as the undefined route-map: the statement commits, the policy
    reads correctly, and the clause matches nothing.

    REPORTED, NOT REFUSED, which is a change from what this did (#208). FRR
    accepts `match community NOTYET` against a list that does not exist,
    stores it, and BINDS IT when the list arrives - measured on 10.7.1, where
    `show route-map` prints the match clause before the list exists and
    matches on it afterwards with nothing retyped. So an operator building a
    policy in that order - the map now, the lists in the next commit - was
    having a correct configuration refused, and the whole commit with it.
    That is the posture rpki_findings' docstring already argues for a clause
    that will not do anything YET, and this is the same case.

    What survives is the true positive underneath: a list name with a typo in
    it is never reported by anything else, and the clause matches nothing
    forever rather than until the next commit.
    """
    have_as, have_comm, have_large = set(), set(), set()
    for line in routing:
        w = line.split()
        # `bgp community-list standard` and nothing after it - a statement
        # half typed, or a line truncated in a file - raised IndexError and
        # put a Python traceback in front of the operator instead of a
        # refusal. Every name is read with a length check for that reason, and
        # test_prismash_complete fuzzes truncations of every statement these
        # functions parse to keep it that way.
        if len(w) >= 4 and w[:3] == ["bgp", "as-path", "access-list"]:
            have_as.add(w[3])
        elif len(w) >= 3 and w[0] == "bgp" and w[1] == "community-list":
            named = list_name(w)
            if named:
                have_comm.add(named)
        elif len(w) >= 3 and w[0] == "bgp" and w[1] == "large-community-list":
            named = list_name(w)
            if named:
                have_large.add(named)

    out = []
    for line in routing:
        w = line.split()
        if not w or w[0] != "match" or len(w) < 3:
            continue
        kind, name = w[1], w[2]
        if kind == "as-path" and name not in have_as:
            out.append((line.strip(), f"no AS-path list called '{name}' is "
                        "defined; until one is, the clause matches nothing"))
        elif kind == "community" and name not in have_comm:
            out.append((line.strip(), f"no community list called '{name}' is "
                        "defined; until one is, the clause matches nothing"))
        elif kind == "large-community" and name not in have_large:
            out.append((line.strip(), f"no large-community list called "
                        f"'{name}' is defined; until one is, the clause "
                        "matches nothing"))
    return out


def isis_findings(routing):
    """
    What an IS-IS configuration says that will not do what it looks like.

    Every one of these is accepted by FRR and every one is local to this box,
    which is the line this project draws: whether the neighbour agrees is not
    in the configuration and is not claimed.
    """
    instances = {}      # tag -> {net, is_type, metric_style}
    joined = {}         # tag -> [(interface, family, statement)]
    circuits = {}       # tag -> [(interface, circuit_type, statement)]
    metrics = []        # (interface, value, statement)
    tag = None
    iface = None
    for line in routing:
        w = line.split()
        if not w:
            continue
        if w[:2] == ["router", "isis"] and len(w) > 2:
            tag, iface = w[2], None
            instances.setdefault(tag, {"net": False, "is_type": None,
                                       "metric_style": None})
            continue
        if w[0] == "interface" and len(w) > 1:
            tag, iface = None, w[1]
            continue
        if w[0] in ("router", "route-map", "rpki", "vrf"):
            tag, iface = None, None
            continue
        if tag:
            inst = instances[tag]
            if w[0] == "net":
                inst["net"] = True
            elif w[0] == "is-type" and len(w) > 1:
                inst["is_type"] = w[1]
            elif w[0] == "metric-style" and len(w) > 1:
                inst["metric_style"] = w[1]
        elif iface:
            if w[:3] in (["ip", "router", "isis"], ["ipv6", "router", "isis"]) \
                    and len(w) > 3:
                joined.setdefault(w[3], []).append((iface, w[0], line.strip()))
            elif w[:2] == ["isis", "circuit-type"] and len(w) > 2:
                circuits.setdefault(None, []).append((iface, w[2],
                                                      line.strip()))
            elif w[:2] == ["isis", "metric"] and len(w) > 2:
                try:
                    metrics.append((iface, int(w[2]), line.strip()))
                except ValueError:
                    pass

    out = []
    for tag, inst in instances.items():
        # REFUSED: an instance with no NET never starts. FRR accepts the
        # stanza and isisd runs with nothing in it.
        if not inst["net"]:
            out.append(("error", f"router isis {tag}",
                        "no 'net': the instance has no address and will not "
                        "start"))
        # An instance no interface joins runs and carries nothing.
        if tag not in joined:
            out.append(("warning", f"router isis {tag}",
                        "no interface joins it, so it runs and carries "
                        "nothing"))
        # NOTHING is said about an instance that carries IPv6 without
        # `topology ipv6-unicast`, and the silence is the point (#198). That
        # statement turns on Multi-Topology IS-IS (RFC 5120), a second SPF for
        # IPv6; without it IPv6 reachability still floods in the single legacy
        # topology and forwards, which is the ordinary single-vendor core.
        # lab/igp.sh commits exactly that configuration, and the assertion
        # beside this one in that file watches 2001:db8:19::/64 reach the VPP
        # IPv6 FIB over the adjacency it builds - so the warning that used to
        # be here was disproved by this product's own acceptance test on every
        # run, and printed again on every later commit in it, about BGP and
        # about OSPF. That is how an operator learns to skip the whole class,
        # and the IGP findings are the diagnoses FRR cannot make.
        #
        # What IS a fault is MT on at one end and off at the other. It is not
        # in this box's configuration - it is read from the neighbour's LSP -
        # so it belongs in the operational view and not in a commit check.
        #
        # Narrow metrics cap at 63. The classic silent limitation.
        if inst["metric_style"] in (None, "narrow"):
            # Only the interfaces that joined THIS instance. The metric of a
            # port in a wide-metric instance is not evidence about a narrow
            # one that never carries it, and a box with two instances is
            # exactly where that sentence names the wrong tag.
            mine = {j[0] for j in joined.get(tag, [])}
            big = [m for m in metrics if m[1] > 63 and m[0] in mine]
            if big:
                out.append(("warning", big[0][2],
                            f"{big[0][1]} needs 'metric-style wide' on "
                            f"{tag}; narrow metrics cap at 63"))

    # REFUSED: an interface joining a tag no instance declares. Same shape as
    # the undeclared peer group and the undefined route-map.
    for tag, joins in joined.items():
        if tag not in instances:
            out.append(("error", joins[0][2],
                        f"no 'router isis {tag}' declares that instance"))

    # A level-2-only box with a level-1 circuit forms no adjacency on it, and
    # this is entirely local: FRR accepts both halves.
    only_tag = next(iter(instances), None)
    if len(instances) == 1 and only_tag:
        want = instances[only_tag]["is_type"]
        for ifname, ctype, stmt in circuits.get(None, []):
            if want == "level-2-only" and ctype == "level-1":
                out.append(("warning", stmt,
                            f"instance {only_tag} is level-2-only, so a "
                            "level-1 circuit forms no adjacency"))
            elif want == "level-1" and ctype == "level-2-only":
                out.append(("warning", stmt,
                            f"instance {only_tag} is level-1, so a "
                            "level-2-only circuit forms no adjacency"))
    return out


def mtu_stall_lines(ifname, host, here, there):
    """
    The two sentences that say an OSPF adjacency is stalled on MTU.

    One function because there are two places that have to say it - the check
    at commit time and the operational view of the adjacency it predicted -
    and two diagnoses of one fact that disagree are worse than one. Both
    numbers, never "the MTUs disagree": that is not actionable and "9000
    against 1500" is.
    """
    return [f"interface {ifname}: carries OSPF and its MTU is {here} while "
            f"its Linux side {host} is at {there}",
            "    the adjacency will sit in Exchange with no log line; set "
            "them the same, or 'ip ospf mtu-ignore'"]


def osi_input_drops(show_errors):
    """
    Frames VPP counted as an OSI protocol nobody is registered to receive.

    This is the only evidence an IS-IS circuit that is Up with no adjacency
    leaves anywhere on the box (#168): the hellos arrive, VPP recognises them
    as OSI, and with no punt configured there is nothing to hand them to.

    Read by field and not by searching the whole text for the phrase. "show
    errors" is a table, the count is its first column, and a needle matched
    against the blob would be satisfied by the words appearing in any row of
    it - including a row about a different node.
    """
    for line in (show_errors or "").splitlines():
        w = line.split()
        if len(w) >= 5 and is_number(w[0]) and w[1] == "osi-input" \
                and w[2:5] == ["unknown", "osi", "protocol"]:
            return int(w[0])
    return 0


def isis_overload_set(database):
    """
    Is THIS box advertising the overload bit, read from the LSP it floods?

    "show isis database" prints ATT/P/OL as the last column, and marks the
    LSPs this box originated with a bare `*` in the second field:

        prisma-routing.00-00    *    108   0x00000005  0x0113    1160    0/0/1
        upstream.00-00            127   0x00000005  0xbfed    1138    0/0/0

    Read from the advertisement rather than from `set-overload-bit` in the
    configuration, for the reason #176 gives: a maintenance drain that is
    reported by the words that asked for it is reported by a line that may not
    have taken effect. `.00-00` is the box's own LSP and not one of the
    pseudo-node LSPs a DIS also originates, which carry no overload bit.
    """
    for line in (database or "").splitlines():
        w = line.split()
        if len(w) >= 3 and w[0].endswith(".00-00") and w[1] == "*" \
                and w[-1].count("/") == 2:
            return w[-1].split("/")[2] != "0"
    return False


#: The filters a `show` may be piped into, and what each takes after it.
#: Deliberately short. Every one of these answers a question an operator
#: actually asks on this box; a longer list would be a second language to
#: learn, and the one thing it MUST NOT become is a shell - a pipe implemented
#: by handing the text to `grep` is a shell escape wearing a filter's clothes,
#: reachable by a read-only account, on a product that restricts the shell to
#: administrators on purpose.
PIPE_FILTERS = {
    "include": ("keep the lines containing this", "<text>"),
    "match": ("the same as include, for operators who type it that way",
              "<text>"),
    "exclude": ("drop the lines containing this", "<text>"),
    "section": ("keep the block a matching line opens, to the next line that "
                "starts at column 0", "<text>"),
    "count": ("how many lines, instead of the lines", None),
    "first": ("the first n lines", "<n>"),
    "last": ("the last n lines", "<n>"),
}


def split_pipe(line):
    """
    A command and the filters it was piped into.

    Only for `show`. A '|' in a configuration statement is a character in a
    regular expression or in a description, not a filter, and treating it as
    one would mangle a policy an operator wrote - so the caller decides, and
    this is never applied to anything else.
    """
    parts = [p.strip() for p in line.split("|")]
    return parts[0], [p for p in parts[1:] if p]


def apply_pipe(text, stages):
    """
    Run the filters over the text a command already produced.

    AFTER the command, and after its redactions, which is what makes
    `show running-config | include password` safe: the filter sees the string
    the operator would have seen anyway, never the value behind it.

    An unknown filter is refused rather than ignored. Ignoring it would answer
    the question the operator did not ask, with no sign that it had.
    """
    lines = text.splitlines()
    for stage in stages:
        w = stage.split()
        if not w:
            continue
        verb, arg = w[0], " ".join(w[1:])
        # Surrounding quotes are taken off. Everybody types them - the shape
        # `| section "interface Gi"` is what a pipe looks like in every shell
        # anybody has used, and this filter is not a shell, so nothing had
        # removed them: the quotes became part of the text being searched for
        # and the filter matched nothing at all. Silently, which is the worst
        # of it - an empty result reads as "there is no such section".
        #
        # Only a matched pair, and only around the whole argument. A quote in
        # the middle of a string is a character somebody is searching for.
        if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in "\"'":
            arg = arg[1:-1]
        if verb not in PIPE_FILTERS:
            return None, (f"'{verb}' is not a filter; this box has: "
                          + ", ".join(sorted(PIPE_FILTERS)))
        if PIPE_FILTERS[verb][1] and not arg:
            return None, f"'{verb}' needs {PIPE_FILTERS[verb][1]} after it"
        if verb in ("include", "match"):
            lines = [l for l in lines if arg in l]
        elif verb == "exclude":
            lines = [l for l in lines if arg not in l]
        elif verb == "section":
            # A block is a line at column 0 and everything indented under it.
            # That is the shape of this box's configuration, which is why this
            # filter is worth having at all.
            kept, inside = [], False
            for l in lines:
                if l[:1] and not l[:1].isspace():
                    inside = arg in l
                if inside:
                    kept.append(l)
            lines = kept
        elif verb == "count":
            lines = [str(len(lines))]
        elif verb in ("first", "last"):
            try:
                n = int(arg)
            except ValueError:
                return None, f"'{verb}' needs a number, not '{arg}'"
            if n < 0:
                return None, f"'{verb}' needs a number that is not negative"
            lines = lines[:n] if verb == "first" else (lines[-n:] if n else [])
    return lines, None


def interface_aliases(name, pairs):
    """Every name this interface answers to, itself included."""
    return set((pairs or {}).get(name, ())) | {name}


def frr_side_names(name, pairs, dp_names):
    """
    The member(s) of this interface's linux_cp pair that FRR can act on.

    routes.linux_end() is the rule - the name a pair carries TWICE, else the
    member that is neither the dataplane's nor the TAP's - and it is asked
    with the dataplane's names so that `name` may be ANY member of the pair:
    `passive-interface tap0` asks with the TAP. This used to be its own
    subtraction, and the same-name pair (#389) taught it the twice rule on
    its own while five other readers went on finding the Linux end by
    `name != ifname`; one owner now, so the next shape teaches one place.

    With no dataplane list there is nothing to tell the members apart, and
    every one of them is returned: "could not ask" must not become a
    warning, which frr_acts_on() relies on. A pair the rule cannot place -
    every member the dataplane's, none listed twice - falls back to the
    subtraction, which is empty and reads as the same posture.
    """
    names = list((pairs or {}).get(name, ())) or [name]
    if not dp_names:
        return set(names)
    end = routes.linux_end(pairs, name, dp_names)
    return {end} if end else set(names) - set(dp_names)


def frr_acts_on(name, pairs, dp_names):
    """
    Whether an FRR statement spelling the interface this way does anything.

    A linux_cp pair answers to several names and only one of them is a Linux
    netdev, which is the only one FRR ever sees. The dataplane's own names are
    the ones `show interface` reports, so they are the ones an operator is
    holding - and a statement written with one of them is accepted by FRR and
    acts on nothing. The Linux end is found by subtraction rather than by
    position, because the order of names on VPP's `itf-pair` line has already
    differed between two labs here.

    With no dataplane list to subtract there is nothing to tell the members of
    a pair apart, and the answer is yes for every one of them. "Could not ask"
    must not become a warning the operator cannot silence.

    Shared by ospf_findings (#205) and bgp_errors (#208) because it is the
    same question about the same pair: the second one was written by copying
    the first, which is how two answers to one question start to drift.
    """
    frr_side = frr_side_names(name, pairs, dp_names)
    return not frr_side or name in frr_side


def ospf_state(routing):
    """
    What the configuration says about OSPF, gathered once.

    Returns (networks, areas_by_interface, passive, unpassive, redistribute,
    has_ospf) - parsed by statement rather than by indentation, for the
    reason route_map_findings and bgp_errors both had to learn: a
    configuration read back from FRR is indented and the same line typed at
    this VTY is not.

    `passive` and `unpassive` are separate because `passive-interface
    default` is a statement about every interface and `no passive-interface
    <name>` is how one is taken back out of it. Reading only the first and
    treating "default" as an interface name made the CGNAT check fire on the
    posture this VTY's own help text recommends for a subscriber-facing port.
    """
    networks = []          # (prefix, area, statement)
    iface_area = {}        # interface -> area, from `ip ospf area`
    passive = []           # (interface or "default", statement)
    unpassive = []         # (interface, statement) from `no passive-interface`
    redist = []            # (statement, has_route_map)
    has_ospf = False
    in_ospf = False
    iface = None
    for line in routing:
        w = line.split()
        if not w:
            continue
        if w[:2] == ["router", "ospf"]:
            in_ospf, iface, has_ospf = True, None, True
            continue
        if w[0] == "interface" and len(w) > 1:
            in_ospf, iface = False, w[1]
            continue
        if w[0] in ("router", "route-map", "rpki", "vrf"):
            in_ospf, iface = False, None
            continue
        if in_ospf:
            if w[:1] == ["network"] and len(w) >= 4 and w[2] == "area":
                networks.append((w[1], w[3], line.strip()))
            elif w[:1] == ["passive-interface"] and len(w) >= 2:
                passive.append((w[1], line.strip()))
            elif w[:2] == ["no", "passive-interface"] and len(w) >= 3:
                unpassive.append((w[2], line.strip()))
            elif w[:1] == ["redistribute"] and len(w) >= 2:
                redist.append((line.strip(), "route-map" in w))
        elif iface and w[:3] == ["ip", "ospf", "area"] and len(w) >= 4:
            iface_area[iface] = w[3]
            has_ospf = True
        elif iface and w[:3] == ["ip", "ospf", "passive"]:
            # The same statement after a round trip through FRR. Measured:
            # `passive-interface vppwan0` is accepted with a deprecation
            # notice and comes back out of `show running-config` as
            #
            #   interface vppwan0
            #    ip ospf passive
            #
            # and cfg.routing is read from FRR, so THIS is the spelling every
            # commit after the first one sees. Reading only `passive-interface`
            # meant an operator who had made a port passive was warned about
            # it again on the next unrelated commit, under a statement they
            # never typed. It is also a spelling this VTY offers in its own
            # right, under `interface <name>` / `ip ospf passive`.
            #
            # Does not set has_ospf: passive with no area is not an OSPF
            # instance, and saying it was would let every other check in
            # ospf_findings fire on a box that runs no OSPF at all.
            passive.append((iface, line.strip()))
    return networks, iface_area, passive, unpassive, redist, has_ospf


def ospf6_state(routing):
    """
    What the configuration says about OSPFv3, gathered once.

    Returns (in_area, quiet, ignoring, has_ospf6). Separate from ospf_state()
    and not folded into it, because the two protocols share no statement: an
    OSPFv3 interface is enabled with `ipv6 ospf6 area <a>` and never with the
    `ip ospf` spelling, and `router ospf6` is a different instance from
    `router ospf` rather than an address family of it. ospf_state() therefore
    reads NOTHING out of an OSPFv3 configuration, which is why every check
    that keys on it was silent on one (#187).

    Silent is the right answer for most of them - an OSPFv2 finding fired on a
    v3 stanza would be an alarm that cannot be right, which is the #205/#208
    lesson - but not for the MTU stall, because that fault is measured to be
    the same fault: with the TAP at 9000 and the wire at 1500 an OSPFv3
    adjacency stops short of Full and reaches it the moment the two agree.
    """
    in_area = {}       # interface -> area, from `ipv6 ospf6 area`
    quiet = set()      # `ipv6 ospf6 passive`
    ignoring = set()   # `ipv6 ospf6 mtu-ignore`
    has_ospf6 = False
    iface = None
    for line in routing:
        w = line.split()
        if not w:
            continue
        if w[:2] == ["router", "ospf6"]:
            iface, has_ospf6 = None, True
            continue
        if w[0] == "interface" and len(w) > 1:
            iface = w[1]
            continue
        if w[0] in ("router", "route-map", "rpki", "vrf"):
            iface = None
            continue
        if not iface or w[:2] != ["ipv6", "ospf6"]:
            continue
        if w[2:3] == ["area"] and len(w) >= 4:
            in_area[iface] = w[3]
            has_ospf6 = True
        elif w[2:3] == ["passive"]:
            quiet.add(iface)
        elif w[2:3] == ["mtu-ignore"]:
            ignoring.add(iface)
    return in_area, quiet, ignoring, has_ospf6


def ospf_findings(routing, addresses, have_interfaces, pools,
                  lcp_pairs=None, device_interfaces=None):
    """
    What an OSPF configuration says that will not do what it looks like.

    Warnings unless stated: FRR accepts every one of these, and each is a
    shape that commits cleanly and does nothing, or does something the
    operator did not mean. The rule this project holds a check to is that it
    must catch something FRR does not, and must not refuse a configuration
    that is fine.

    `lcp_pairs` is routes.lcp_pairs() over `show lcp` and `device_interfaces`
    is the dataplane's OWN interface names - not the union of both namespaces
    that `have_interfaces` carries. Both are optional, and what is lost
    without them is described under `frr_acts_on` below.
    """
    networks, iface_area, passive, unpassive, redist, has_ospf = \
        ospf_state(routing)
    out = []
    if not has_ospf:
        return out

    # ------------------------------------------------------------------
    # Which namespace this function works in, and why it has to choose one.
    #
    # It works in FRR's. Every statement ospf_state() parses is an FRR
    # statement that only FRR ever reads, so the name in it means whatever
    # FRR takes it to mean and nothing else. `addresses` is the other side of
    # each comparison and is keyed by the DATAPLANE's names when it was read
    # back off the box, and by whatever the operator typed when it came from
    # a staged `interface` block - which docs/routing.md says may be either
    # name. So the two sides are matched through the linux_cp pair, and the
    # configuration side is additionally required to be a spelling FRR can
    # act on. Comparing the names as strings, which is what this replaces,
    # was wrong in both directions at once (#205).
    #
    # Measured on the lab's own pair rather than reasoned about, because the
    # answer decides which spelling is the mistake:
    #
    #   itf-pair: [0] host-veth-vpp tap4096 vppwan0 5 type tap
    #
    #   * only vppwan0 is a Linux netdev, and FRR's interface list holds
    #     vppwan0 and not the other two;
    #   * `passive-interface host-veth-vpp` is ACCEPTED - a deprecation
    #     notice, no error - and makes nothing passive: `show ip ospf
    #     interface vppwan0` does not say "No Hellos", and the statement does
    #     not survive into the running configuration at all;
    #   * `passive-interface vppwan0` does say "No Hellos (Passive
    #     interface)".
    #
    # The pair's Linux end is therefore the one FRR acts on, and it is the
    # one member of the pair the DATAPLANE does not also have - which is how
    # frr_acts_on() finds it without reading a field by position. The order
    # of names on that line is a VPP detail routes.lcp_pairs() deliberately
    # does not depend on, having already seen it differ between two labs
    # here.
    # ------------------------------------------------------------------
    pairs = lcp_pairs or {}
    dp_names = set(device_interfaces or ())

    # not a unit: a closure inside ospf_findings, read through its owner
    # by test_prismash_units.py
    def same(name):
        return interface_aliases(name, pairs)

    # not a unit: a closure inside ospf_findings, read through its owner
    # by test_prismash_units.py
    def acts_on(name):
        # The posture the interface-existence check below takes with an empty
        # `have_interfaces`, for the same reason: could not ask is not a
        # warning.
        return frr_acts_on(name, pairs, dp_names)

    # REFUSED: a passive-interface naming an interface the box does not have.
    # It commits, and the port the operator meant to keep quiet is not the one
    # that goes quiet - which is the "interface Ten0" shape (#105) in the one
    # place it costs an adjacency rather than nothing.
    for ifname, stmt in passive:
        if ifname != "default" and have_interfaces and ifname not in have_interfaces:
            out.append(("error", stmt,
                        f"this box has no interface {ifname}, so nothing is "
                        "made passive"))

    # The same outcome reached through a name the box DOES have: the
    # dataplane's end of a linux_cp pair. FRR has no such interface, accepts
    # the statement anyway, and makes nothing passive - measured above. This
    # is the spelling `passive-interface <TAB>` offers, because the
    # completion lists what `show interface` reports, so it is the one an
    # operator is most likely to be holding.
    #
    # Warned and not refused. The name FRR wants is not one the dataplane
    # reports, so an error here would refuse a commit an operator cannot
    # rewrite from anything this VTY offers them; and the check below now
    # says out loud that the port is still not passive, which is the
    # consequence they need to see.
    for ifname, stmt in passive:
        if ifname == "default" or acts_on(ifname):
            continue
        # The name in the sentence is the one acts_on() just judged by, not a
        # second subtraction: on the same-name pair (#389) that subtraction
        # is empty, and this row read "FRR knows this interface as , not
        # tap0".
        out.append(("warning", stmt,
                    "FRR knows this interface as "
                    + "/".join(sorted(frr_side_names(ifname, pairs,
                                                     dp_names)))
                    + f", not {ifname}: written this way nothing is made "
                    "passive"))

    # A network statement matching no address on this box: OSPF runs on
    # nothing and the configuration reads correctly.
    every = [a for addrs in addresses.values() for a in addrs]
    for prefix, area, stmt in networks:
        if not every:
            break                      # no addresses read: cannot judge
        if not any(routes.covers_prefix(prefix, a) for a in every):
            out.append(("warning", stmt,
                        f"no address on this box is inside {prefix}, so OSPF "
                        "runs on nothing because of this line"))

    # An area border router with no backbone. Inter-area routes never
    # propagate, and FRR says nothing.
    #
    # Unless a virtual link carries it. RFC 2328 section 15 exists for exactly
    # the box that has no physical area-0 interface, and it belongs to the
    # backbone through the link instead - so warning there is a sentence about
    # a working design, told to the operator who had to think hardest about it.
    areas = {a for _, a, _ in networks} | set(iface_area.values())
    backbone = {"0", "0.0.0.0"}
    virtual = any("virtual-link" in l.split() for l in routing)
    if len(areas - backbone) >= 2 and not (areas & backbone) and not virtual:
        out.append(("warning", f"router ospf ({len(areas)} areas)",
                    "interfaces in two or more areas and none in area 0: "
                    "inter-area routes will not propagate"))

    # A subscriber-facing port speaking OSPF. The cross-half check only this
    # product can make: FRR does not know what a CGNAT pool is, and the
    # dataplane does not know what OSPF is.
    # A statement silences this only when FRR acts on the name it uses, and
    # then for every name that interface answers to, so the dataplane-keyed
    # side of the comparison matches. Without the first half the operator who
    # typed the name this VTY's own TAB completion offers - a dataplane name,
    # which FRR will never match - was reassured by a statement that did
    # nothing.
    quiet = set()
    for ifname, _ in passive:
        if acts_on(ifname):
            quiet |= same(ifname)
    # `ip ospf area` is the other door into "this interface speaks OSPF", and
    # it sits in an FRR stanza, so it is spelled the way FRR spells
    # interfaces while `addresses` usually is not. Matched as a name against
    # a dataplane-keyed dictionary it never matched anything, which left this
    # warning unable to fire at all on an interface configured that way.
    in_area = set()
    for ifname in iface_area:
        if acts_on(ifname):
            in_area |= same(ifname)
    # `passive-interface default` is a statement about EVERY interface, and
    # `no passive-interface <name>` is how one is taken back out of it. It is
    # the posture this VTY's own help text calls "what every subscriber-facing
    # port should be", so treating "default" as an interface name - which is
    # what comparing names as strings did - fired this warning on the box that
    # had got it exactly right.
    all_quiet = any(i == "default" for i, _ in passive)
    loud = set()
    for ifname, _ in unpassive:
        if acts_on(ifname):
            loud |= same(ifname)
    inside = [p for pool in pools.values() for p in (pool.inside or [])]
    for ifname, addrs in addresses.items():
        mine = same(ifname)
        if mine & quiet or not inside:
            continue
        if all_quiet and not mine & loud:
            continue
        speaks = bool(mine & in_area) or any(
            routes.covers_prefix(prefix, a)
            for prefix, _, _ in networks for a in addrs)
        if not speaks:
            continue
        if any(routes.covers_prefix(p, a) for p in inside for a in addrs):
            out.append(("warning", f"interface {ifname}",
                        "speaks OSPF and its addresses are inside a CGNAT "
                        "pool: it is facing subscribers, and it is not "
                        "passive"))

    # A full table into the IGP.
    for stmt, has_map in redist:
        if stmt.split()[1] == "bgp" and not has_map:
            out.append(("warning", stmt,
                        "no route-map: on an edge box this is the whole BGP "
                        "table into OSPF"))
    return out


def rpki_findings(routing):
    """
    What an origin-validation policy says that will not do what it looks like.

    Rejecting RPKI-invalid routes is the point. The failure it CREATES is the
    one worth a sentence: a validator that is not connected marks everything
    `notfound`. A policy that denies `invalid` keeps working when the cache
    goes away; a policy that treats `notfound` as suspicious quietly degrades
    every route on the box - and cache outages are ordinary.

    So a clause matching `notfound` with no cache configured is not a mistake
    exactly, it is a policy that does nothing useful until one is configured,
    and that is invisible afterwards. Reported, not refused: an operator may be
    building the policy before the cache, in that order, on purpose.
    """
    caches = [l.strip() for l in routing
              if l.strip().startswith("rpki cache ")]
    out = []
    for line in routing:
        w = line.split()
        if w[:2] == ["match", "rpki"] and len(w) >= 3 and not caches:
            state = w[2]
            if state == "notfound":
                out.append((line.strip(),
                            "no RPKI cache is configured, so every route is "
                            "notfound and this clause matches all of them"))
            elif state in ("valid", "invalid"):
                out.append((line.strip(),
                            "no RPKI cache is configured, so nothing is ever "
                            + state + " and this clause matches nothing"))
    return out


# The `set` statements FRR takes a value for and does NOT bound itself, with
# the bound that belongs to them.
#
# ONE ENTRY, and it earns the table. `set local-preference` is declared to FRR
# as `set local-preference WORD` - no range, measured off `list` inside a
# route-map on 10.7.1 - so `set local-preference banana` is accepted by vtysh,
# accepted by `frr-reload.py --test` (rc 0, which is what `commit check` runs),
# and appears verbatim in bgpd's running configuration. bgpd complains once,
# to the session, in the line `Frr._summarise()` throws away, and then the
# clause sets nothing: a policy that looks right and does nothing, which is
# the shape the rest of this function exists for.
#
# This VTY has offered `<0-4294967295>` for it since the route-map tree was
# written, and that number is right - local preference is a 32-bit unsigned in
# the protocol. The range was never the problem; nothing enforcing it was
# (#354). Everything else FRR bounds in its own grammar and is left to it,
# which is why this table has one row and not twenty.
SET_RANGES = {
    "local-preference": (0, 4294967295),
}


def set_value_error(stmt: str):
    """
    Why a route-map `set` statement's value is not one, or None.

    Takes the whole statement so the finding can quote it back the way the
    operator typed it - `say_finding` prints the statement on a row of its own
    and the reason above it, and a reason that names a bare value leaves them
    looking for which of the clause's statements it came from.
    """
    w = stmt.split()
    if len(w) != 3 or w[0] != "set":
        return None
    bound = SET_RANGES.get(w[1])
    if bound is None:
        return None
    lo, hi = bound
    if not is_number(w[2]) or not lo <= int(w[2]) <= hi:
        return (f"'{w[2]}' is not a {w[1]}: expected {lo}-{hi}. FRR takes "
                "this statement with any word in it and then sets nothing")
    return None


def route_map_findings(routing):
    """
    What a set of route-maps says that is wrong or worth saying out loud.

    Returns (severity, statement, why) with severity "error" or "warning". The
    split is the point: one of these is always a mistake and one of them is a
    legitimate idiom that is ALSO what an unfinished policy looks like, and a
    VTY that refuses the second is a VTY an operator has to fight.

    * A `deny` clause with `set` statements is a mistake every time. The set
      never runs - the route is denied - so the operator wrote something that
      reads like policy and is not.

    * A `permit` clause with no `match` permits everything. That is common and
      deliberate at the end of a policy, and it is also exactly what "I had
      not finished typing" looks like. It cannot be refused; it must be said,
      because afterwards the two are indistinguishable.

    * A clause that can never be reached, because an earlier clause in the same
      route-map matches everything. Sequence numbers are the operator's and
      this MUST NOT renumber them - but a policy that looks right and does
      nothing is worth a sentence.

      "Earlier" means earlier BY SEQ, which is the only order FRR has. This
      read the clauses in the order they were WRITTEN, so inserting a low-seq
      clause after a catch-all - which is the ordinary way to add a clause to
      the front of a policy, and the reason sequence numbers exist - was
      reported unreachable while FRR reaches it first (#208). Measured: `route-
      map ORD permit 20` then `route-map ORD permit 10`, and FRR's own
      `show route-map ORD` lists 10 before 20.

      And a clause that steers its own flow - `on-match next`, `on-match goto`,
      the `continue` an operator types for the second - does not end the
      policy: the clauses after it ARE reached. Those were parsed and then
      ignored, so one of them poisoned the verdict on every later clause. The
      whole claim is withdrawn for a route-map that contains one, rather than
      guessed at: what `on-match goto 30` skips depends on whether the clause
      matched, which is not in the configuration.
    """
    maps = {}          # name -> list of clauses, in the order written
    clause = None
    # Membership is decided by the statement, not by indentation. A
    # configuration read back from FRR is indented; the same lines typed at
    # this VTY are not, and a parser keyed on leading space sees every typed
    # statement as a new block - so a `deny` clause carrying `set` read as an
    # empty clause and was not refused. Found by the lab, not by reading.
    for raw in routing:
        line = raw.rstrip()
        if not line:
            continue
        w = line.split()
        if w[0] == "route-map" and len(w) >= 4:
            clause = {"name": w[1], "action": w[2], "seq": w[3],
                      "header": line.strip(), "match": [], "set": [],
                      "other": [], "steers": False}
            maps.setdefault(w[1], []).append(clause)
            continue
        if clause is None:
            continue
        if w[0] == "match":
            clause["match"].append(line.strip())
        elif w[0] == "set":
            clause["set"].append(line.strip())
        elif w[0] in ("call", "on-match", "continue", "description"):
            # `continue 30` is what an operator types and `on-match goto 30`
            # is what FRR stores it as - measured, both spellings of one
            # statement. Not knowing the typed one meant it fell through to
            # the line below and ENDED the clause, so every `match` and `set`
            # after it was read as belonging to nothing: a deny clause with a
            # set in it, written that way, was not refused.
            clause["other"].append(line.strip())
            if w[0] in ("on-match", "continue"):
                clause["steers"] = True
        elif w[0] != "exit":
            clause = None          # some other block began

    # FRR's order, not the file's. A clause whose seq is not a number is not
    # one FRR will take at all, and sorting it to the front would invent a
    # catch-all out of a typo: it goes last instead.
    # not a unit: a sort key inside route_map_findings, read through its
    # owner by test_prismash_units.py
    def by_seq(c):
        return (0, int(c["seq"])) if is_number(c["seq"]) else (1, 0)

    out = []
    for name, clauses in maps.items():
        catch_all = None
        steered = any(c["steers"] for c in clauses)
        for c in sorted(clauses, key=by_seq):
            for stmt in c["set"]:
                why = set_value_error(stmt)
                if why:
                    out.append(("error", stmt, why))
            if c["action"] == "deny" and c["set"]:
                out.append(("error", c["header"],
                            f"a deny clause with {len(c['set'])} 'set' "
                            "statement(s): the set never runs, because the "
                            "route is denied"))
            if catch_all is not None:
                out.append(("warning", c["header"],
                            f"unreachable: '{catch_all}' earlier in "
                            f"{name} matches everything and permits it"))
                continue
            if not c["match"]:
                if c["action"] == "permit":
                    out.append(("warning", c["header"],
                                "no 'match': this permits every route. If "
                                "that is meant, it is the end of the policy; "
                                "if not, the policy is unfinished"))
                # A catch-all is only a dead end when nothing jumps past it.
                # `deny` with no match denies everything after it too, and is
                # the other half of the same trap - but both halves are
                # undone by an `on-match` anywhere in the same route-map.
                if not steered:
                    catch_all = c["header"]
    return out


# The places a route-map name is USED, by the shape of the statement, and what
# FRR was measured to do at each of them when nothing defines the name.
#
# Not one blanket refusal, because the answer is not the same at all of them,
# and that difference is what keeps this off a configuration that works (#208).
# Measured on FRR 10.7.1, two speakers over a veth pair, asserting on the
# receiving side's table rather than on the text:
#
#   neighbor 10.9.0.2 route-map RM-MISSING in    peer offers 2, this box holds
#                                                0; defining the map brings
#                                                both back with nothing retyped
#   neighbor 10.9.0.1 route-map OUT-MISSING out  the other end holds 0
#   redistribute kernel route-map RM-NOPE        originates nothing; defined,
#                                                it originates the route
#   network 203.0.113.0/24 route-map RM-NONE     the same
#
# In every one of those FRR printed `The route-map 'X' does not exist.`, kept
# the statement, and denied everything the statement governs.
ROUTE_MAP_FATAL = ("neighbor-in-out", "redistribute", "network")


def route_map_references(routing):
    """
    Every place `routing` names a route-map, as (statement, name, kind).

    `kind` is one of ROUTE_MAP_FATAL for the three shapes measured to deny
    everything, and "other" for the rest - `call`, `table-map`,
    `default-originate route-map`, the `*-map` family of BGP conditional
    advertisement. The list is deliberately wider than the refusal: a name
    used ANYWHERE is a name that is not unused, and the reverse check reads
    this same function. A reference this did not know about would report a
    route-map that is doing its job as dead configuration.
    """
    out = []
    for raw in routing:
        line = raw.strip()
        w = line.split()
        if not w or w[0] == "no":
            continue
        # `route-map RM permit 10` DEFINES; it does not reference. And
        # `bgp route-map delay-timer 10` is not a route-map name at all - the
        # word after `route-map` there is a knob, and reading it as a name
        # would refuse a commit over a timer.
        if w[0] == "route-map" or w[:2] == ["bgp", "route-map"]:
            continue
        # Free text, and an operator who writes "upstream, see route-map RM-IN"
        # in one has named nothing. Read as a reference it would warn about a
        # sentence, or refuse a commit over it.
        if w[0] == "description" or (w[0] == "neighbor" and len(w) >= 3
                                     and w[2] == "description"):
            continue
        if w[0] == "call" and len(w) >= 2:
            out.append((line, w[1], "other"))
            continue
        for i, word in enumerate(w[:-1]):
            name = w[i + 1]
            if word == "route-map":
                if w[0] == "neighbor" and len(w) >= 3 and w[2] == "route-map":
                    out.append((line, name, "neighbor-in-out"))
                elif w[0] == "redistribute":
                    out.append((line, name, "redistribute"))
                elif w[0] == "network":
                    out.append((line, name, "network"))
                else:
                    out.append((line, name, "other"))
            elif word in ("table-map", "suppress-map", "unsuppress-map",
                          "advertise-map", "exist-map", "non-exist-map"):
                out.append((line, name, "other"))
    return out


def route_maps_defined(routing):
    """
    The route-map names this configuration defines, and where each begins.

    A definition is a clause header - `route-map NAME permit|deny [seq]` - and
    nothing else. A name that appears only in a reference is not defined by
    being referenced, which is the entire point of the check above.
    """
    seen = {}
    for raw in routing:
        w = raw.strip().split()
        if len(w) >= 3 and w[0] == "route-map" and w[2] in ("permit", "deny"):
            seen.setdefault(w[1], raw.strip())
    return seen


def route_map_reference_findings(routing):
    """
    Route-maps named and never defined, and route-maps defined and never used.

    Returns (severity, statement, why), the shape route_map_findings returns.

    REFUSED where it was measured to deny everything, which is the three
    shapes in ROUTE_MAP_FATAL. The session comes up, carries no routes, and
    the box says nothing: FRR's own `The route-map 'X' does not exist.` goes
    to the vtysh session, and this VTY hands whole configurations to
    frr-reload.py, whose output is filtered down to lines carrying `Exiting:`,
    `ERROR`, `% `, `Failed` or `Exception:`. FRR's warning matches none of
    them and is thrown away before the operator sees it.

    This is not the posture undefined_policy_lists settled on, and the
    difference is measured rather than argued. An undefined COMMUNITY LIST
    inside a route-map is bound when the list arrives, so building a policy in
    two commits works and refusing it was the defect #208 named. An undefined
    ROUTE-MAP is not that: for the window between the two commits the session
    carries nothing, and there is no order of typing in which that is what the
    operator meant.

    `call` is in the same file and behaves differently, which is why it is a
    warning. Measured, same pair of speakers: a `permit` clause carrying
    `call RM-MISSING` with nothing defining RM-MISSING advertises the prefix
    anyway - FRR skips a call it cannot resolve and the clause simply
    continues. `show route-map OUT` prints the call clause and the route is in
    the peer's table. So the statement is dead rather than deadly, and
    refusing a whole commit for it would be refusing a configuration that
    works.

    The reverse - a map defined that nothing names - is a warning for the
    reason the issue asking for this gave: dead configuration is untidy, not
    dangerous. It is also what an operator building a policy map-first has on
    the box between two commits, which is the case that MUST still commit.
    """
    defined = route_maps_defined(routing)
    used = route_map_references(routing)
    out = []
    for stmt, name, kind in used:
        if name in defined:
            continue
        if kind in ROUTE_MAP_FATAL:
            out.append(("error", stmt,
                        f"no route-map called '{name}' is defined, and FRR "
                        "denies everything this statement governs until one "
                        f"is; define it, or write 'route-map {name} permit "
                        "10' to let everything through"))
        elif stmt.split()[0] == "call":
            out.append(("warning", stmt,
                        f"no route-map called '{name}' is defined; FRR skips "
                        "a call it cannot resolve, so this line does nothing"))
        else:
            out.append(("warning", stmt,
                        f"no route-map called '{name}' is defined"))
    named = {name for _, name, _ in used}
    for name, header in sorted(defined.items()):
        if name not in named:
            out.append(("warning", header,
                        f"route-map {name} is defined and nothing uses it"))
    return out


def bgp_errors(routing, have_interfaces=None, lcp_pairs=None,
               device_interfaces=None):
    """
    What a BGP configuration says that cannot work, as (severity, stmt, why).

    Structural only. Whether the neighbour ANSWERS is not knowable from the
    configuration, and this does not pretend otherwise - it catches the shapes
    FRR refuses or silently ignores, which are the ones an operator finds out
    about from a session that never comes up.

    Peer groups are the reason this is not a two-line check: a member inherits
    `remote-as` from its group, so a neighbour with no AS of its own is
    perfectly correct when it is in a group that has one.

    BGP unnumbered is the reason it is not a three-line one. A session to a
    peer on a link-local address is written

        neighbor vppwan0 interface remote-as external

    where the "neighbour" is an INTERFACE name, `remote-as` is a later word on
    the same line rather than a statement of its own, and `external` and
    `internal` are values as legitimate as a number. Read positionally, all of
    that was a neighbour with no AS - so the ordinary way to run eBGP to a
    directly connected peer was refused, and with it the whole commit it
    happened to be in (#208).

    `have_interfaces`, `lcp_pairs` and `device_interfaces` are what the
    interface name in that form is validated against, and each is optional:
    with none of them the AS checks still run and the name is taken on trust.
    """
    entities = {}          # name -> what has been said about it
    groups = set()         # names declared as peer groups
    pairs = lcp_pairs or {}
    dp_names = set(device_interfaces or ())

    # not a unit: a closure inside bgp_errors, read through its owner by
    # test_prismash_units.py
    def ent(name):
        return entities.setdefault(
            name, {"remote_as": False, "group": None, "stmts": [], "af": False,
                   "iface": None})

    # Only inside a `router bgp` stanza. `neighbor` is not a BGP word: OSPF
    # uses it for NBMA and point-to-multipoint, and scanning every routing line
    # for it refused `neighbor 10.0.0.2 poll-interval 60` under `router ospf`
    # with a sentence about remote-as, on a box that need not have BGP at all.
    # A refusal that is wrong is worse than a check that is missing.
    #
    # Context is tracked by the statement rather than by indentation, for the
    # same reason route_map_findings had to be: a configuration read back from
    # FRR is indented and the same line typed at this VTY is not.
    in_bgp = False
    for line in routing:
        w = line.split()
        if not w:
            continue
        if w[:2] == ["router", "bgp"]:
            in_bgp = True
            continue
        if w[0] in ("router", "interface", "route-map", "rpki", "vrf"):
            in_bgp = False
            continue
        if w[0] == "exit":
            in_bgp = False      # `exit-address-family` is a different word
            continue
        if not in_bgp:
            continue
        # `no neighbor ...` removes; reading it as configuration would report
        # an error about a session being taken away.
        if w[0] == "no" or w[0] != "neighbor" or len(w) < 2:
            continue
        name = w[1]
        if len(w) == 3 and w[2] == "peer-group":
            groups.add(name)
            ent(name)
            continue
        e = ent(name)
        e["stmts"].append(line.strip())
        if len(w) >= 3 and w[2] == "interface":
            # The unnumbered form, read by KEYWORD and not by position. FRR's
            # grammar here is `neighbor WORD interface [v6only] [peer-group
            # PGNAME | remote-as <ASN|external|internal>]`, so what follows
            # `interface` is a short closed set and scanning it is safe -
            # which is not true of the address form, where `neighbor X
            # description remote-as 65001` is a description and not an AS.
            if e["iface"] is None:
                e["iface"] = line.strip()
            rest = w[3:]
            for i, word in enumerate(rest):
                if i + 1 >= len(rest):
                    break
                if word == "remote-as":
                    e["remote_as"] = True
                elif word == "peer-group":
                    e["group"] = rest[i + 1]
        elif len(w) >= 4 and w[2] == "remote-as":
            e["remote_as"] = True
        elif len(w) >= 4 and w[2] == "peer-group":
            e["group"] = w[3]
        elif len(w) >= 3 and w[2] == "activate":
            e["af"] = True

    out = []
    for name, e in entities.items():
        if name in groups:
            continue
        if not e["stmts"]:
            continue
        # The interface name in an unnumbered session, held to the same rule
        # as every other interface name in an FRR statement (#205): it must be
        # a spelling FRR can act on.
        #
        # Measured on FRR 10.7.1 rather than assumed, because the answer
        # decides whether this is worth saying at all: `neighbor nosuchif0
        # interface remote-as external` is ACCEPTED, stored, and shows in
        # `show bgp summary` as a peer that is Idle and stays Idle. Nothing
        # in FRR ever says the interface does not exist, so this is a
        # diagnosis only the box that has both halves can make.
        if e["iface"] and have_interfaces and name not in have_interfaces:
            out.append(("error", e["iface"],
                        f"this box has no interface {name}, so the session "
                        "has nothing to run over"))
            continue
        if e["iface"] and not frr_acts_on(name, pairs, dp_names):
            # Warned and not refused, for the reason ospf_findings gives: the
            # name FRR wants is not one `show interface` reports, so an error
            # would refuse a commit the operator cannot rewrite from anything
            # this VTY offers them.
            out.append(("warning", e["iface"],
                        "FRR knows this interface as "
                        + "/".join(sorted(frr_side_names(name, pairs,
                                                         dp_names)))
                        + f", not {name}: written this way the session has "
                        "nothing to run over"))
        if e["group"] and e["group"] not in groups:
            out.append(("error", e["stmts"][0],
                        f"peer group '{e['group']}' is not declared; declare "
                        f"it with 'neighbor {e['group']} peer-group'"))
            continue
        if e["remote_as"]:
            continue
        if e["group"]:
            g = entities.get(e["group"], {})
            if g.get("remote_as"):
                continue
            out.append(("error", e["stmts"][0],
                        f"neither {name} nor peer group '{e['group']}' has a "
                        "remote-as, so the session has no AS to open with"))
            continue
        if e["iface"]:
            # `neighbor X interface` on its own is ACCEPTED by FRR, unlike the
            # address form, which it refuses with "Specify remote-as or
            # peer-group commands first". Measured: the peer appears in `show
            # bgp summary` with AS 0, state Idle, and never leaves it. So this
            # is not a refusal borrowed from FRR - it is the silent half of
            # the same mistake, which is the half this function exists for.
            out.append(("error", e["stmts"][0],
                        f"{name} has no 'remote-as': FRR accepts this and "
                        "leaves the peer Idle with no AS, forever"))
            continue
        out.append(("error", e["stmts"][0],
                    f"{name} has no 'remote-as': FRR refuses every other "
                    "statement for a neighbour whose AS it has not been told"))
    return out


def max_prefix_findings(routing):
    """
    eBGP neighbours with no `maximum-prefix`, as (severity, stmt, why).

    A peer that leaks a full table into a box sized for a few thousand routes
    takes it down, and nothing in BGP stops it by default: FRR accepts every
    prefix the neighbour sends until memory runs out. `maximum-prefix` is the
    only statement that tears the session down instead, and a box that does
    not carry one has no defence that is written anywhere.

    WARNING and never a refusal, which is the issue's own call and the right
    one: a route server, a customer whose table is known small, a lab peer -
    all of them run without a limit on purpose. Refusing them would be the
    shape #208 named.

    iBGP is out of scope, and not by omission. The peer on the other end of an
    iBGP session is a box the same operator runs, its table is the table this
    AS already decided to hold, and a limit there fires on a legitimate growth
    of the network rather than on a leak. So the test is the AS: a neighbour
    whose `remote-as` differs from the stanza's, or is the word `external`.

    Everything that cannot be decided from the text is left alone, because a
    warning printed over a configuration this function misread is the same
    harm as a refusal, only quieter:

    * A neighbour with no resolvable `remote-as` gets nothing from here.
      `bgp_errors` already refuses that session for a reason that matters
      more, and a second sentence about a limit on a session that will never
      open is noise on top of a refusal.
    * `remote-as auto` is a real FRR spelling and the AS it resolves to is not
      in the text, so it is neither eBGP nor iBGP here.
    * A member of a peer group carrying the limit is covered, and so is one
      whose group carries the `remote-as`. Missing that is how a check earns
      a workaround: the group is where an operator with six upstreams puts
      the limit exactly once.
    * `maximum-prefix-out` is a different statement - it limits what this box
      SENDS - so the word is matched whole.
    """
    out = []
    local_as = None
    groups = set()
    # name -> {"as": remote-as value or None, "group": name, "limit": bool,
    #          "stmt": the line it is first named on}
    peers = {}

    # not a unit: a closure inside max_prefix_findings, read through its
    # owner by test_prismash_units.py
    def ent(name):
        return peers.setdefault(name, {"as": None, "group": None,
                                       "limit": False, "stmt": None})

    # The same walk `bgp_errors` does, and for the same two reasons: `neighbor`
    # is an OSPF word as well as a BGP one, and context is tracked by statement
    # rather than by indentation because a configuration read back from FRR is
    # indented and the same line typed at this VTY is not.
    in_bgp = False
    for line in routing:
        w = line.split()
        if not w:
            continue
        if w[:2] == ["router", "bgp"]:
            in_bgp = True
            local_as = w[2] if len(w) >= 3 else None
            continue
        if w[0] in ("router", "interface", "route-map", "rpki", "vrf"):
            in_bgp = False
            continue
        if w[0] == "exit":
            in_bgp = False      # `exit-address-family` is a different word
            continue
        if not in_bgp or w[0] != "neighbor" or len(w) < 3:
            continue
        name = w[1]
        if len(w) == 3 and w[2] == "peer-group":
            groups.add(name)
            ent(name)
            continue
        e = ent(name)
        if e["stmt"] is None:
            e["stmt"] = line.strip()
        if w[2] == "interface":
            # The unnumbered form, read by keyword: `remote-as` and
            # `peer-group` are later words on the same line, and the closed
            # set after `interface` is what makes scanning it safe (#208).
            rest = w[3:]
            for i, word in enumerate(rest[:-1]):
                if word == "remote-as":
                    e["as"] = rest[i + 1]
                elif word == "peer-group":
                    e["group"] = rest[i + 1]
        elif len(w) >= 4 and w[2] == "remote-as":
            e["as"] = w[3]
        elif len(w) >= 4 and w[2] == "peer-group":
            e["group"] = w[3]
        elif w[2] == "maximum-prefix":
            e["limit"] = True

    for name, e in sorted(peers.items()):
        if name in groups or e["stmt"] is None:
            continue
        g = peers.get(e["group"] or "", {})
        if e["limit"] or g.get("limit"):
            continue
        remote = e["as"] or g.get("as")
        if remote is None or remote == "auto":
            continue
        if remote == "internal" or remote == local_as:
            continue
        out.append(("warning", e["stmt"],
                    f"this eBGP session has no 'maximum-prefix': a peer that "
                    f"leaks a full table fills this box and nothing here "
                    f"stops it. 'neighbor {name} maximum-prefix 10000' tears "
                    f"the session down instead of the box"))
    return out


def bgp_originations(routing):
    """
    What this configuration would have BGP originate, and what hides the rest.

    Returns (announced, blind, has_bgp).

    `announced` is the prefixes this function can follow from the text alone:
    a plain `network <prefix>` under `router bgp`, and - when the same stanza
    carries a plain `redistribute static` - every top-level static route in
    the candidate. `blind` is the statements that can originate a prefix whose
    identity is NOT in the configuration. When `blind` is non-empty the caller
    must say nothing at all, because a warning that fires on a working box is
    worse than no warning: the next one, the real one, is ignored too.

    Scope is taken from config.block_paths() rather than from indentation or
    from a fourth hand-rolled stanza scanner. It is the only thing here that
    answers "which block is this line in" correctly for BOTH shapes `routing`
    arrives in - indented when FRR read it back, flat when it was typed at
    this VTY - and the scope is load bearing rather than tidy. Measured on FRR
    10.7.1, with a covering static route present the whole time:

        router bgp 65000 / address-family ipv4 unicast / redistribute static
          *>  200.150.9.16/28  0.0.0.0  0  32768 ?

        router ospf / redistribute static      (BGP carrying no redistribute)
          No BGP prefixes displayed, 0 exist

    Both statements are stored, and they are the same six characters. Reading
    the word wherever it appeared - which is what this replaces - treated the
    second box as one it could not see through, so the ordinary edge box that
    leaks its IGP nowhere near BGP got no warning at all.

    Removing the static route under the FIRST configuration empties the BGP
    table too ("No BGP prefixes displayed, 0 exist"), which is why
    `redistribute static` is resolved against the candidate's own routes
    instead of being treated as unfollowable. FRR's static routes are all in
    the configuration, so that resolution is complete rather than a guess.

    Only TOP-LEVEL static routes count. A route inside a `vrf red` block is in
    another table, and hoisting one into the global answer is the mistake
    config.group_static() already had to be taught not to make.
    """
    paths = block_paths(routing)
    announced = []
    statics = []
    redistributes_static = False
    blind = []
    has_bgp = False
    for path, raw in zip(paths, routing):
        if path is None:
            continue                   # a blank, a `!`, a block's own `exit`
        line = raw.strip()
        w = line.split()
        # `no ...` takes a statement away; reading it as configuration would
        # count a removed `network` as an announcement.
        if not w or w[0] == "no":
            continue
        if line.startswith("router bgp"):
            has_bgp = True
        if not path and is_static_route(line) and len(w) >= 3:
            statics.append(w[2])
            continue
        # A block's own header carries its PARENT's path, so the stanza a line
        # belongs to is path[0] for a statement inside one and the line itself
        # for a top-level statement.
        if not (path[0] if path else line).startswith("router bgp"):
            continue
        if w[0] == "network" and len(w) >= 2:
            if len(w) == 2:
                announced.append(w[1])
            else:
                # `network <prefix> route-map <name>` is a real FRR form and
                # the map decides. Measured on 10.7.1 with the prefix in the
                # RIB: a `deny 10` map originates nothing ("No BGP prefixes
                # displayed, 0 exist"), a `permit 10` map originates it
                # ("*> 200.150.9.16/28"). Following the map is a job this does
                # not do, so the honest answer is silence.
                blind.append(line)
        elif w[0] == "redistribute" and len(w) >= 2:
            if w[1] == "static" and "route-map" not in w:
                redistributes_static = True
            else:
                # `connected`, `ospf`, `isis`, `kernel`, and anything through a
                # route-map: the prefixes these carry are not in the
                # configuration, so nothing here can say whether one of them
                # covers the pool.
                blind.append(line)
        elif w[0] == "aggregate-address":
            # It can originate a covering prefix and it can originate nothing,
            # and which one depends on the rest of the BGP table rather than on
            # the text. Measured on 10.7.1, same aggregate both times:
            #
            #   with a more-specific in the table
            #     *>  200.150.0.0/16   ...        s>  200.150.9.16/28  ...
            #   with no more-specific
            #     No BGP prefixes displayed, 0 exist
            #
            # The first of those covers the pool, so treating the statement as
            # unfollowable is what keeps this check off a working aggregate.
            blind.append(line)
    if redistributes_static:
        announced += statics
    return announced, blind, has_bgp


def pool_announce_findings(routing, pools):
    """
    Pools whose public prefix nothing in this configuration would advertise.

    The check this product can make and nobody else can. Configure a pool with
    `outside 200.150.0.16/28`, configure BGP, get both right, and unless
    something advertises a prefix COVERING that /28 every subscriber behind
    the pool is translated to an address the internet cannot route back to:
    traffic leaves and nothing returns. The CGNAT is right, the routing is
    right, the service is dead, and the two halves are configured in different
    places by different people on different days.

    Warning, never an error, and the decision is measured rather than taken
    from the issue that asked for it:

    * The condition this check can test is not the condition that matters. On
      FRR 10.7.1, `network 200.150.9.16/28` with no matching route in the RIB
      is stored, appears in `show bgp ipv4 unicast` WITHOUT `*>` - not valid,
      not best, not advertised - and becomes `*> 200.150.9.16/28` the moment
      `ip route 200.150.9.16/28 blackhole` exists. So the presence of a
      `network` statement is a proxy for the announcement and not the
      announcement. A commit refused for the absence of that proxy would block
      configurations that work while still passing the box whose `network`
      statement originates nothing.
    * Two ordinary ways to originate a covering prefix are invisible here at
      all - `aggregate-address` and `network ... route-map`, both measured
      under bgp_originations() - so a refusal would fire on an arbitrary
      subset of the mistake and be silent on the rest.
    * Announcing the prefix from a router upstream of this box is a legitimate
      design, and the common one: a CGNAT behind an edge router that carries
      the aggregate is the ordinary ISP shape, and nothing about it is in this
      box's configuration.

    A refusal here would therefore be wrong on correct configurations, which
    is the shape #208 established: refusing a whole commit over a BGP
    statement was itself the defect there. An alarm that is always on is one
    nobody reads.

    Returns [(severity, subject, why, advice)] - four fields and not the three
    the findings beside it return, because the sentence an operator acts on is
    not the sentence that names the fault. `why` names it; `advice` is the
    lines under it that say what it costs and what to type, and folding those
    into `why` would put three sentences on one terminal line.
    """
    announced, blind, has_bgp = bgp_originations(routing)
    out = []
    # A box with no BGP stanza announces nothing from here by definition, and
    # is exactly the deployment whose prefix is carried by somebody else. It
    # is told nothing, for the same reason the blind case is.
    if not pools or not has_bgp or blind:
        return out
    for pname, pool in sorted(pools.items()):
        for pfx in pool.outside or ():
            if any(routes.covers_prefix(a, pfx) for a in announced):
                continue
            out.append((
                "warning", f"pool '{pname}'",
                f"nothing here announces {pfx}",
                # The advice names the blackhole route as well as the
                # `network`, because measurement 4 above says the `network`
                # alone is not enough: FRR stores it and advertises nothing
                # until a route for the prefix exists. Advice that does not
                # work when followed is worse than no advice.
                ["subscribers behind it would be translated to an address "
                 "the internet cannot route back to",
                 "announce it from another box, or originate it here with",
                 f"'ip route {pfx} blackhole' and 'network {pfx}' under the "
                 "BGP address family"]))
    return out


def dynamic_export_findings(pools, globals_):
    """
    Pools whose blocks only an export can account for, on a box that
    exports none of their block events (#444).

    In dynamic mode the block a subscriber holds is chosen when it asks, and
    the exported record is the only thing that can later say who held a
    public port: no formula answers it. Measured on 0.124.0, a dynamic pool
    with no exporter passed `commit check` with nothing said about it, and
    `show cgnat logging` read `state disabled`, `exporter none` - so a box
    could carry subscribers for months unable to answer a court order, and
    learn it on the day the order arrived.

    Any transport counts: UDP only when it is enabled AND has a collector
    (enable alone is refused elsewhere, logging_enable_without_collector),
    TCP or syslog when a collector is named. A warning, never a refusal, for
    the reason pool_announce_findings() gives for its own: a lab, or a pool
    staged before its collector exists, is a correct configuration, and a
    refusal would be wrong on it.

    Returns [(severity, statement, why)], the shape say_finding() prints.
    """
    # And a deterministic pool with a `reserve`: its reserve blocks are the
    # ones the formula does NOT produce, and docs/logging.md says each MUST
    # be exported exactly like a dynamic block - so the same silence costs
    # the same answer for those subscribers.
    owed = sorted((n, getattr(p, "mode", None) == "dynamic")
                  for n, p in (pools or {}).items()
                  if getattr(p, "mode", None) == "dynamic"
                  or getattr(p, "block_reserve", 0))
    if not owed:
        return []
    words = [g.split() for g in globals_ or ()]
    udp = (any(w[:3] == ["cgnat", "logging", "enable"] for w in words)
           and any(w[:3] == ["cgnat", "logging", "udp"] and len(w) > 3
                   and w[3] != "disable" for w in words))
    other = any(w[:2] == ["cgnat", "logging"] and len(w) > 3
                and w[2] in ("tcp", "syslog") and w[3] != "disable"
                for w in words)
    if udp or other:
        return []
    return [("warning", f"nat / cgnat / pool {name}",
             ("dynamic, and nothing exports its block events. " if dyn else
              "a reserve, and nothing exports the blocks it hands out. ")
             + "The exported record is the only evidence of which subscriber "
             "held a public port" + ("" if dyn else " in a reserve block, which "
                                     "the formula does not produce")
             + ": without it that question has no answer. Export with "
             "'logging udp <collector> src <source>' and 'logging enable' "
             "under 'nat' / 'cgnat', or 'logging tcp <collector>' there")
            for name, dyn in owed]


def session_local_address(environ=None):
    """
    The address on THIS box that the session applying the change arrived on.

    `SSH_CONNECTION` is "<client> <client port> <server> <server port>", set by
    sshd for the session's own process, and the SERVER half is the one that
    matters here. Reachability of the client's address is the client network's
    business; reachability of the address the session terminates on is what
    this box advertises, and what an outbound policy can take away.

    None when the session did not arrive over SSH - a serial console, `--load`
    from a script, the boot-time apply. That is not a failure and MUST NOT be
    treated as one, but it MUST NOT be silent either: the caller says which
    check it could not make rather than letting the operator assume it was
    made and passed.
    """
    env = os.environ if environ is None else environ
    w = (env.get("SSH_CONNECTION") or "").split()
    if len(w) != 4:
        return None
    return w[2] or None


def prefix_list_verdict(routing, name, prefix):
    """
    "permit", "deny", or None for `prefix` against the named prefix-list.

    None means "this function will not say", and every caller treats that as
    "cannot tell" rather than as a permit or a deny. An entry it cannot read
    is exactly where a guess turns into a refusal of a correct configuration.

    The rule is FRR's: an entry matches when the route's prefix is inside the
    entry's, AND the route's LENGTH is in the range the entry allows - which
    is the entry's own length unless `ge`/`le` widen it. First match wins, in
    sequence order, and a prefix-list that matches nothing denies.
    """
    try:
        want = ipaddress.ip_network(prefix, strict=False)
    except ValueError:
        return None
    entries = []
    for raw in routing:
        w = raw.strip().split()
        if len(w) < 4 or w[0] not in ("ip", "ipv6") or w[1] != "prefix-list":
            continue
        if w[2] != name:
            continue
        rest = w[3:]
        seq = None
        if rest[0] == "seq" and len(rest) >= 2:
            try:
                seq = int(rest[1])
            except ValueError:
                return None
            rest = rest[2:]
        if not rest:
            continue
        if rest[0] == "description":
            continue
        if rest[0] not in ("permit", "deny"):
            return None                  # a spelling this does not read
        action, rest = rest[0], rest[1:]
        if not rest:
            return None
        entries.append((seq, action, rest))
    if not entries:
        return None                      # no such list: FRR's answer, not ours
    # Sequence order, which is the order FRR walks. Entries written without a
    # seq are numbered by FRR as it stores them, so they keep the order they
    # were written in, after the numbered ones they follow.
    ordered = []
    last = 0
    for seq, action, rest in entries:
        last = seq if seq is not None else last + 1
        ordered.append((last, action, rest))
    for _, action, rest in sorted(ordered, key=lambda e: e[0]):
        if rest[0] == "any":
            return action
        try:
            net = ipaddress.ip_network(rest[0], strict=False)
        except ValueError:
            return None
        lo = hi = net.prefixlen
        i = 1
        while i < len(rest):
            if rest[i] in ("ge", "le") and i + 1 < len(rest):
                try:
                    n = int(rest[i + 1])
                except ValueError:
                    return None
                if rest[i] == "ge":
                    lo = n
                    hi = max(hi, net.max_prefixlen)
                else:
                    hi = n
                i += 2
                continue
            return None                  # a word this does not read
        if net.version != want.version:
            continue
        if lo <= want.prefixlen <= hi and want.subnet_of(net):
            return action
    return "deny"                        # the implicit one at the end


def route_map_verdict(routing, name, prefix):
    """
    "permit", "deny", or None for `prefix` through the named route-map.

    Only the clauses this can read to the end: a clause with no `match` at
    all, or one whose single match is `match ip address prefix-list <list>`
    against a list prefix_list_verdict() can read. Anything else - a community
    match, an AS-path match, a `call`, an `on-match` - returns None, because
    what the map does then depends on the route's attributes and on flow this
    does not follow, and a guess would be a refusal aimed at a working policy.

    An empty or missing map denies, measured on FRR 10.7.1: `neighbor X
    route-map RM-MISSING out` with nothing defining RM-MISSING leaves the
    other end holding zero prefixes.
    """
    clauses = []
    current = None
    for raw in routing:
        w = raw.strip().split()
        if not w:
            continue
        if w[0] == "route-map" and len(w) >= 3 and w[2] in ("permit", "deny"):
            current = None
            if w[1] == name:
                seq = w[3] if len(w) >= 4 and is_number(w[3]) else "0"
                current = {"action": w[2], "seq": int(seq),
                           "match": [], "opaque": False}
                clauses.append(current)
            continue
        if current is None:
            continue
        if w[0] == "match":
            current["match"].append(w[1:])
        elif w[0] in ("call", "on-match", "continue"):
            current["opaque"] = True
        elif w[0] not in ("set", "description", "exit"):
            current = None               # some other block began
    if not clauses:
        return "deny"
    for c in sorted(clauses, key=lambda c: c["seq"]):
        if c["opaque"]:
            return None
        if not c["match"]:
            return c["action"]
        if len(c["match"]) != 1:
            return None                  # several clauses, several rules
        m = c["match"][0]
        if m[:3] != ["ip", "address", "prefix-list"] or len(m) < 4:
            return None
        said = prefix_list_verdict(routing, m[3], prefix)
        if said is None:
            return None
        if said == "permit":
            return c["action"]
    return "deny"                        # the implicit deny at the end of a map


def advertised_out(routing, prefix):
    """
    ([neighbours that would be sent `prefix`], [neighbours it cannot tell for]).

    Outbound policy only, and only the two forms whose effect on one prefix is
    in the text: `neighbor X route-map RM out` and `neighbor X prefix-list PL
    out`. A `distribute-list` or a `filter-list` puts the neighbour in the
    second list instead, because an AS-path filter decides on an attribute and
    not on the prefix.

    Measured on FRR 10.7.1 with two speakers, this box originating
    203.0.113.0/24 and 198.18.0.0/24 and the assertion on the PEER's table:

        route-map OUT permit 10, matching a prefix-list that holds only
        198.18.0.0/24                          peer keeps 198.18.0.0/24 only
        route-map OUT deny 5 matching a list
        holding 203.0.113.0/24                 the same
        neighbor prefix-list PL-OUT out, with
        `deny 203.0.113.0/24` before a permit   the same
        the policy removed again                both prefixes back

    So the implicit deny at the end of a route-map withdraws a prefix as
    surely as a deny clause naming it, and both are worth reading from the
    text.

    Peer groups are followed: a member with no policy of its own inherits the
    group's, and one with its own does not. A neighbour under `shutdown` - its
    own or its group's - is not a carrier: the configuration holds the session
    down, and that is readable here in a way "has never come up" is not.
    """
    paths = block_paths(routing)
    policy = {}          # neighbour or group -> {"route-map": n, "prefix-list": n, "opaque": bool}
    member = {}          # neighbour -> group
    groups = set()
    shut = set()         # neighbours and groups administratively down
    seen = []
    for path, raw in zip(paths, routing):
        if path is None:
            continue
        line = raw.strip()
        w = line.split()
        if not w or w[0] == "no" or w[0] != "neighbor" or len(w) < 3:
            continue
        if not (path[0] if path else line).startswith("router bgp"):
            continue
        name = w[1]
        if w[2] == "peer-group" and len(w) == 3:
            groups.add(name)
            continue
        if w[2] == "peer-group" and len(w) >= 4:
            member[name] = w[3]
        if name not in seen:
            seen.append(name)
        # A session the configuration holds down carries nothing, and that is
        # in the text rather than in the session table - so unlike a neighbour
        # that merely never came up, it can be read here.
        if w[2] == "shutdown":
            shut.add(name)
        if len(w) >= 5 and w[4] == "out" and w[2] in ("route-map",
                                                      "prefix-list"):
            policy.setdefault(name, {})[w[2]] = w[3]
        elif len(w) >= 5 and w[4] == "out" and w[2] in ("distribute-list",
                                                        "filter-list"):
            policy.setdefault(name, {})["opaque"] = True

    carries, unsure = [], []
    for name in seen:
        if name in groups:
            continue
        if name in shut or member.get(name, "") in shut:
            continue
        mine = dict(policy.get(member.get(name, ""), {}))
        mine.update(policy.get(name, {}))
        if mine.get("opaque"):
            unsure.append(name)
            continue
        verdict = "permit"
        if "prefix-list" in mine:
            verdict = prefix_list_verdict(routing, mine["prefix-list"], prefix)
        if verdict == "permit" and "route-map" in mine:
            verdict = route_map_verdict(routing, mine["route-map"], prefix)
        if verdict is None:
            unsure.append(name)
        elif verdict == "permit":
            carries.append(name)
    return carries, unsure


def session_cut_findings(running_routing, target_routing, local_addr):
    """
    Whether this change stops advertising the prefix carrying this session.

    The SSH lockout check refuses a configuration nobody could log in with
    (#35); this is the same failure one layer down, and the box is the only
    thing that can see it coming. An operator on the other side of the country
    applies an outbound policy, the prefix their own session runs over stops
    being advertised, the session drops mid-commit, and if the commit was not
    confirmed the rollback timer is the only way back.

    Returns [(subject, why, advice)] - the shape pool_announce_findings
    returns, and for the same reason: the sentence that names the fault is not
    the sentence the operator acts on.

    NOT a refusal. The gate is `commit cutoff`, the shape #23 established for
    a change that re-maps subscribers: a change that is a mistake most times
    it is typed and deliberate some of them cannot be refused outright, and it
    MUST NOT be applied by an operator who has not said the word. Withdrawing
    a prefix is an ordinary thing to do on purpose - a maintenance drain, a
    customer leaving - and the box cannot tell that apart from the mistake.

    Silent in every case where it cannot be sure, and the cases are the point:

    * The box does not originate a covering prefix today. Somebody else
      announces it, which is the ordinary shape for a CGNAT behind an edge
      router, and nothing in this configuration governs it.
    * Nothing would advertise it today either. Then this change is not what
      takes it away.
    * Any neighbour whose outbound policy this cannot read to the end. The
      prefix may still leave the box through that one, and a finding would be
      aimed at a configuration that works.
    * `bgp_originations` is blind - an `aggregate-address`, a `network` with a
      route-map, a `redistribute connected`. Then the candidate may originate
      the prefix by a route not in the text.

    A neighbour that is configured and has NEVER come up counts as a carrier,
    because this reads the configuration and not the session table. That is
    deliberate and it is a real limit: lab/bgp-policy.sh found it, with a
    third neighbour in a peer group that stays Idle, and the finding did not
    fire. The alternative is worse - gating a commit on whether a session
    happens to be established at that instant makes the same change gated or
    not depending on a flap, and a gate nobody can predict is one nobody
    trusts.
    """
    if not local_addr:
        return []
    run_announced, run_blind, run_bgp = bgp_originations(running_routing)
    new_announced, new_blind, new_bgp = bgp_originations(target_routing)
    if not run_bgp or not new_bgp or run_blind or new_blind:
        return []
    covering = [p for p in run_announced
                if routes.covers_address(p, local_addr)]
    if not covering:
        return []
    # ANY covering prefix reaching ANY neighbour is the box being reachable,
    # so both halves are asked over the whole set rather than over the first
    # prefix that happens to cover the address. A box announcing a /24 and a
    # /25 over it loses nothing when one of the two goes.
    if not any(advertised_out(running_routing, p)[0] for p in covering):
        return []
    still = [p for p in new_announced
             if routes.covers_address(p, local_addr)]
    if not still:
        return [(covering[0],
                 f"this change stops originating it, and it covers "
                 f"{local_addr} - the address this session arrived on",
                 ["nothing here would advertise a route back to this box's "
                  "end of your own session",
                  "if that is what you mean, type: commit cutoff"])]
    for p in still:
        carries, unsure = advertised_out(target_routing, p)
        if carries or unsure:
            return []
    return [(still[0],
             f"no neighbour would be sent it after this change, and it covers "
             f"{local_addr} - the address this session arrived on",
             ["the outbound policy denies it to every neighbour that has one, "
              "so nothing would advertise a route back to this box's end of "
              "your own session",
              "if that is what you mean, type: commit cutoff"])]


def advertisement_fingerprint(routing):
    """
    Everything in `routing` that decides what this box advertises, and to whom.

    Two configurations with the same fingerprint cannot differ in what leaves
    the box over BGP, so a change that leaves it untouched cannot be the
    change session_cut_findings exists to catch. That is what lets the "could
    not check" note stay quiet: on a serial console it is printed only when
    the commit actually moves one of these lines, rather than on every commit,
    which is how an always-on alarm becomes one nobody reads.
    """
    paths = block_paths(routing)
    out = []
    for path, raw in zip(paths, routing):
        if path is None:
            continue
        line = raw.strip()
        w = line.split()
        if not w:
            continue
        if w[0] in ("route-map", "match", "set", "call", "on-match",
                    "continue"):
            out.append(line)
            continue
        if len(w) >= 2 and w[0] in ("ip", "ipv6") and w[1] == "prefix-list":
            out.append(line)
            continue
        if not (path[0] if path else line).startswith("router bgp"):
            continue
        if w[0] in ("network", "redistribute", "aggregate-address"):
            out.append(line)
        elif w[0] == "neighbor" and "out" in w:
            out.append(line)
    return out


def prefix_range_error(line):
    """
    Why FRR would refuse this prefix-list line, or None if it would not.

    FRR 10.7.1 answers a bad `ge`/`le` with

        Invalid prefix range for 10.0.0.0/8: Make sure that mask length <= ge <= le

    and that refusal is not free. It arrives through mgmtd, which then leaves
    its candidate datastore holding the rejected entry and fails the NEXT
    configuration commit with the same message - naming a prefix-list that is
    in no running configuration, on no disk, and in nothing this VTY sent.
    Measured: a file load carrying one good line and one bad one, and the two
    commits after it, all refused. An operator would be reading an error about
    a prefix they never typed and cannot find.

    So the line is refused here, before it reaches FRR. The rule is FRR's own,
    and lab/routing.sh checks this function against the box rather than against
    anyone's memory of it.
    """
    w = line.split()
    if not w or w[0] not in ("ip", "ipv6") or len(w) < 2 or w[1] != "prefix-list":
        return None
    if "ge" not in w and "le" not in w:
        return None
    prefix = None
    for t in w:
        if "/" in t:
            prefix = t
            break
    if prefix is None:
        return None
    try:
        net = ipaddress.ip_network(prefix, strict=False)
    except ValueError:
        return None                      # a malformed prefix is FRR's to reject
    top = net.max_prefixlen
    length = net.prefixlen

    # not a unit: a closure inside prefix_range_error, read through its
    # owner by test_prismash_units.py
    def after(word):
        try:
            return int(w[w.index(word) + 1])
        except (ValueError, IndexError):
            return None

    ge = after("ge") if "ge" in w else None
    le = after("le") if "le" in w else None
    if ("ge" in w and ge is None) or ("le" in w and le is None):
        return None                      # not a number: FRR's parser refuses it
    lo = length if ge is None else ge
    hi = top if le is None else le
    if not (length <= lo <= hi <= top):
        shown = f"{net.network_address}/{length}"
        return (f"prefix range refused for {shown}: "
                f"mask length <= ge <= le <= {top} does not hold "
                f"(length {length}"
                + (f", ge {ge}" if ge is not None else "")
                + (f", le {le}" if le is not None else "") + ")")
    return None


# The refusal for a box that has routing in its configuration and no daemon to
# take it. ONE OWNER, and that is the whole reason it is up here: the sentence
# was written out at both of the call sites that need it - `apply` validating
# the routing half, and `apply` applying it - and a sentence with two owners is
# a sentence that becomes two sentences the first time one of them is reworded.
# This file has had to unify a spelling for that reason more than once.
#
# Split, because `err` never folds a verdict (#252) and the one row was 83
# columns on a console of 79 (#362). What is left in the verdict is the
# refusal; the command the operator has to run is a row of its own, which is
# where it survives a fold rather than being the tail that the terminal breaks.
FRR_SILENT_REFUSED = (
    "routing is configured and FRR is not answering",
    "'systemctl status frr' and its journal say why; blaming the "
    "configuration for a daemon that is not running sends somebody to debug "
    "the wrong thing",
)


class Frr:
    """Talks to FRR through vtysh, and reloads whole configurations."""

    def show(self, cmd):
        return self._exec(["-c", cmd])

    def daemons(self):
        """
        The set of daemons FRR reports running, or None when FRR did not say.

        None is not the empty set and the two MUST NOT be collapsed. An FRR
        that is not answering is a control plane that is down, and the fix is
        to get FRR itself back; an FRR that answers with nothing running is a
        box whose daemons were never enabled, and the fix is
        /etc/frr/daemons. Reporting the first as the second sends the operator
        to edit a file on a box that would not have read it.

        One place asks, because two would be two truths: `commit` refuses a
        `router <protocol>` statement whose daemon is absent, and
        `show version` reports the same set to the operator its own hints send
        here (#272).
        """
        rc, out = self.show("show daemons")
        if rc != 0 or not out.strip():
            return None
        return set(out.split())

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

    #: A prefix-list name nothing configures. Removing something that is not
    #: there is a real commit that changes nothing, which is exactly what is
    #: needed below.
    ABSORB_NAME = "HEXA-ABSORB-STALE-REFUSAL"

    def absorb_stale_refusal(self):
        """
        Take the refusal FRR owes the NEXT commit, so the operator does not.

        Measured on FRR 10.7.1: a statement FRR rejects is refused through
        mgmtd, which keeps the rejected entry in its candidate datastore and
        then refuses the next configuration commit with the same message -
        exactly one, and then it clears. That next commit is the operator's,
        and the error names a statement that is in no configuration they can
        find.

        Which absorbers work was measured, not guessed:

            configure terminal, end                 does NOT clear it
            hostname <the hostname it already has>  does NOT clear it
            no ip prefix-list <a name nobody has>   clears it, leaves nothing

        The first two change nothing, so mgmtd never runs a commit cycle and
        never re-validates. The third is a genuine commit whose effect is
        nothing, which is the combination this needs. It is also harmless when
        there was no stale refusal to absorb, which matters because this runs
        on every failed apply and most of those are the VTY's own refusals.
        """
        try:
            subprocess.run([VTYSH, "-c", "configure terminal",
                            "-c", f"no ip prefix-list {self.ABSORB_NAME}"],
                           capture_output=True, text=True, timeout=15)
        except (subprocess.TimeoutExpired, OSError):
            pass               # best effort: the commit already failed

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
        # A directory of our own, per run, and every path frr-reload writes to
        # pointed into it - because with --reload it writes to two places that
        # belong to FRR and not to the account applying the change (#186):
        #
        #   --logfile  /var/log/frr/frr-reload.log, opened BEFORE --stdout is
        #              looked at, in a directory the frr package ships
        #              drwxr-x--- frr:frr - group frr reads it and cannot write
        #   --rundir   /var/run/frr, where the delta is staged for vtysh -f.
        #              frrcommon.sh makes it 0710 frr:frr, so group frr may
        #              traverse it and cannot write either
        #
        # Both are one PermissionError, and both only on --reload: --test logs
        # to stdout and stages nothing. So a non-root administrator could
        # validate a routing change and then not apply it - which is the worst
        # place to find out, because the check has already said yes and the
        # maintenance window is open.
        #
        # Their modes belong to the frr package and to frrcommon.sh, which
        # re-state them on every upgrade and every start; widening them would
        # last until the next one and would hand group frr the routing daemons'
        # own logs besides.
        #
        # Nothing is lost by moving either: --stdout REPLACES the log handler,
        # so frr-reload opens that log and never writes a line to it, and the
        # staged delta is a scratch file frr-reload unlinks itself. What it has
        # to say arrives on stdout, which is what this reads and PRISMA_FRR_DUMP
        # records.
        #
        # mkdtemp rather than a fixed name: this also runs as root at boot, and
        # a predictable path in a world-writable directory is a symlink
        # somebody else planted for root to write through.
        work = tempfile.mkdtemp(prefix="prismash-frr-")
        path = os.path.join(work, "candidate.conf")
        logfile = os.path.join(work, "frr-reload.log")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        try:
            mode = "--test" if check_only else "--reload"
            p = subprocess.run(["python3", FRR_RELOAD, mode, "--stdout",
                                "--logfile", logfile, "--rundir", work, path],
                               capture_output=True, text=True, timeout=60)
            out = ((p.stdout or "") + (p.stderr or "")).rstrip()
            # Set PRISMA_FRR_DUMP to a path to capture exactly what was sent
            # and exactly what frr-reload said. Kept because _summarise()
            # necessarily throws away most of that output, and twice now the
            # line it threw away was the one that named the cause - a stale
            # mgmtd refusal, and an `rpki cache` missing its transport keyword
            # that read like an upstream limitation.
            dump = os.environ.get("PRISMA_FRR_DUMP")
            if dump:
                with open(dump, "a") as d:
                    d.write("===== sent =====\n" + "\n".join(lines) + "\n")
                    d.write(f"===== raw rc={p.returncode} =====\n{out}\n")
            if p.returncode:
                self.absorb_stale_refusal()
                # frr-reload dumps a Python traceback on failure. An operator
                # needs the reason, not our tooling's stack.
                out = self._summarise(out)
            return p.returncode, out
        except subprocess.TimeoutExpired:
            return 1, "frr-reload timed out"
        finally:
            shutil.rmtree(work, ignore_errors=True)

    @staticmethod
    def outstanding(out):
        """
        The statements frr-reload says are still to be added.

        Its report has two sections, each under a heading and a row of '=':

            Lines To Add
            ============
            router ospf
             timers throttle spf 0 50 5000
            exit

        Stripped, because a nested statement is indented there and the same
        statement typed at this VTY is not - the difference feed() removes on
        the way in and this has to remove on the way back.
        """
        lines, inside = [], False
        for raw in (out or "").splitlines():
            s = raw.strip()
            if s.startswith("Lines To "):
                inside = s == "Lines To Add"
                continue
            if not s or set(s) == {"="}:
                continue
            if inside:
                lines.append(s)
        return lines

    @staticmethod
    def diagnostics(out):
        """
        What FRR said during a SUCCESSFUL apply, and nothing else.

        frr-reload returning 0 does not mean FRR was silent. Told to use a
        route-map that does not exist, FRR 10.7.1 answers

            The route-map 'PL-X' does not exist.

        stores the statement anyway, and exits 0. The caller only printed the
        output when the return code was non-zero, so that line was thrown away
        and the operator committed a policy pointing at nothing.

        Progress lines are dropped rather than diagnostics kept, because a
        filter that lists what to SHOW silently loses the next message FRR
        learns to print. This one only loses noise it already knows about.
        """
        # frr-reload's own logger stamps every line with a date and a level.
        # FRR's messages carry neither, which is the clean way to tell "the
        # tool narrating itself" from "the routing daemon answering". INFO and
        # DEBUG are dropped; WARNING and ERROR from the same logger are kept,
        # because those are about the operator's configuration.
        chatty = re.compile(r"^\d{4}-\d\d-\d\d[\d :,]*\s*(INFO|DEBUG)\s*:")
        noise = ("Waiting for children", "Loading Config", "Config file",
                 "Reading Config", "Building Configuration")
        keep = []
        for line in (out or "").splitlines():
            t = line.strip()
            if not t or t.startswith(("[", "=", "-")) or t.startswith(noise):
                continue
            if t.endswith("done") or t.startswith("frr-reload"):
                continue
            # frr-reload echoes the commands it sends as a Python list, so its
            # output carries fragments of a repr split across lines - "' exit"
            # and "'exit ']". They are not diagnostics and they made every
            # commit noisy, which would have buried the one line that matters.
            if t[0] in "'\"" or t.endswith(("']", '"]', "',", '",')):
                continue
            if chatty.match(t):
                continue
            keep.append(t)
        # FRR'S ANSWER TO ITS OWN SECOND PASS (#445). frr-reload sends the
        # lines to add twice, the second time after re-reading the running
        # configuration, and mgmtd answers a pass that changes nothing with
        #
        #     % Configuration applied with notes:
        #     No changes found to be committed!
        #
        # Measured on FRR 10.7.1 with PRISMA_FRR_DUMP: a commit adding
        # `ip route 0.0.0.0/0 198.51.100.1` printed exactly that pair after
        # the route had been installed by the first pass, and
        # `show ip route static` then had it. An operator reads "no changes"
        # beside a route they just added as the route not being applied.
        # Dropped: the sentence, and the header only when that sentence was
        # the whole of what it announced - a header with any other note under
        # it stays, with the note.
        out = []
        for i, t in enumerate(keep):
            if t == "No changes found to be committed!":
                continue
            if t == "% Configuration applied with notes:":
                under = []
                for r in keep[i + 1:]:
                    if r.startswith("%"):
                        break
                    under.append(r)
                if all(r == "No changes found to be committed!"
                       for r in under):
                    continue
            out.append(t)
        return out

    @classmethod
    def _summarise(cls, out):
        """
        Why the reload failed, in as few lines as carry the reason.

        This used to keep only lines starting with "Exiting:", "ERROR", "% "
        or "Failed". On a rejected configuration frr-reload prints "%
        Configuration failed." - which matches "% " - and the line that says
        WHAT was rejected does not match any of them. So the operator got the
        footer twice and never the reason.
        """
        keep = [l for l in out.splitlines()
                if l.startswith(("Exiting:", "ERROR", "% ", "Failed"))
                or "Exception:" in l]
        # A footer is not a reason. If that is all the prefixes caught, fall
        # back to everything the daemon actually said.
        useful = [l for l in keep
                  if l.strip() not in ("% Configuration failed.",
                                       "% Configuration failed")]
        if not useful:
            said = cls.diagnostics(out)
            if said:
                return "\n".join(said[-6:])
        return "\n".join(keep[-4:]) if keep else (
            out.splitlines()[-1] if out else "")

    #: The three answers `dryrun` gives. Three and not two: a statement FRR
    #: could not be asked about is neither accepted nor refused, and printing
    #: either would be a guess (#431).
    DRYRUN_ACCEPTED, DRYRUN_REFUSED, DRYRUN_UNCHECKED = (
        "accepted", "refused", "unchecked")

    def dryrun(self, ctx, line):
        """
        Would FRR accept `line`, typed inside the blocks `ctx` names?

        Returns (verdict, words): FRR's own refusal for DRYRUN_REFUSED, the
        reason nothing was asked for DRYRUN_UNCHECKED, "" when accepted.

        `vtysh --dryrun` parses a file against FRR's whole grammar and applies
        nothing. MEASURED on FRR 10.7.1 with NO daemon running, which is the
        property this rests on - a box whose bgpd is down still gets its typo
        caught: `bananas` inside `router bgp` is `rc=2 % Unknown command`,
        `neighbor` alone is `rc=4 % Command incomplete`, a good statement is
        rc 0, in about 18 ms a run. The file is the open block path and then
        the line, because the same word is a statement in one block and
        nothing in another.

        `% Unknown command` is also FRR's answer to a bad VALUE - `bgp
        router-id 999.1.1.1` gets it - so the caller quotes FRR rather than
        naming the word it thinks was wrong.
        """
        fd, path = tempfile.mkstemp(prefix="prismash-dryrun-", suffix=".conf")
        try:
            with os.fdopen(fd, "w") as f:
                for depth, block in enumerate(ctx):
                    f.write(" " * depth + block + "\n")
                f.write(" " * len(ctx) + line + "\n")
            try:
                p = subprocess.run([VTYSH, "--dryrun", "-f", path],
                                   capture_output=True, text=True, timeout=10)
            except FileNotFoundError:
                return self.DRYRUN_UNCHECKED, f"{VTYSH} not found"
            except subprocess.TimeoutExpired:
                return self.DRYRUN_UNCHECKED, "vtysh did not answer in 10s"
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        if p.returncode == 0:
            return self.DRYRUN_ACCEPTED, ""
        said = ((p.stdout or "") + (p.stderr or "")).splitlines()
        # `line 3: % Unknown command[31]: netwrk 10.0.0.0/8` is what FRR
        # prints. The line number is of a file the operator never saw, and
        # the bracket is a column into it; what is left is FRR's sentence.
        for s in said:
            m = re.search(r"(%\s[^\[:]+)(?:\[\d+\])?:\s*(.*)$", s)
            if m:
                return self.DRYRUN_REFUSED, f"{m.group(1).strip()}: {m.group(2)}"
        # A failure with no `%` sentence is vtysh failing, not FRR refusing:
        # a file it could not read, a permission. Not a verdict on the line.
        why = next((s.strip() for s in said if s.strip()),
                   f"vtysh exited {p.returncode}")
        return self.DRYRUN_UNCHECKED, why

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
                try:
                    with open(f) as fh:
                        first = fh.readline().strip()
                    comment = first[2:] if first.startswith("# ") else ""
                except OSError as e:
                    # Named, not hidden and not raised. A box upgraded from a
                    # version whose VTY wrote these root-only still carries up
                    # to twenty revisions this account cannot read, and an
                    # operator who is shown a shorter list concludes the
                    # commits never happened.
                    comment = f"cannot be read: {e.strerror}"
                out.append((n, time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(f))),
                    comment))
        return out

    def load(self, n):
        f = self._file(n)
        if not os.path.exists(f):
            return None
        try:
            with open(f) as fh:
                return "".join(l for l in fh if not l.startswith("# "))
        except OSError as e:
            # A revision that is there and out of reach is not a revision that
            # does not exist, and neither of them is a Python traceback in
            # front of somebody in a maintenance window.
            raise RollbackUnavailable(f"{f}: {e.strerror}") from None

    def push(self, text, comment=""):
        """
        Store a new revision 0, shifting the others down.

        Written by whoever committed, which is any account holding a role: a
        rollback revision is a complete configuration, so the store is group
        prisma-cgnat and not root-only. It does hold password hashes, which is
        why that group is a role group and why nothing here is world-readable.
        """
        # A store this account cannot write is reported, not raised. By the
        # time a revision is recorded the change is already on the dataplane,
        # so a traceback here would leave the operator looking at a Python
        # stack with no idea whether their commit took effect - and it did.
        old_umask = os.umask(CONFIG_UMASK)
        try:
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
        except OSError as e:
            # Every step, not only the first: the shift renames files and the
            # write creates one, and a store this account cannot write fails at
            # whichever of those it reaches first.
            raise RollbackUnavailable(
                f"{e.filename or self.path}: {e.strerror}") from None
        finally:
            os.umask(old_umask)


class ConfirmPending:
    """
    The revert a "commit confirmed" is waiting for, on disk.

    On disk and not in the Shell that armed it, because the change this guards
    against is the one that ends that session. A deadline held in the process
    that made the commit is cancelled by precisely the failure it exists for:
    it fired in every case where it was not needed and in none of the cases
    where it was (#212).

    The FILE is the record of the confirmation, and it is removed the moment
    the operator confirms rather than when the window ends. That is what lets
    a box tell a confirmed change from an unconfirmed one across a reboot: a
    box restarted after a confirmation has nothing here and reverts nothing,
    and one restarted before the deadline still carries it and still has the
    deadline it was given.

    The deadline is stored as an absolute time, so it is a promise about a
    moment and not about uptime. A box that was off past its own deadline
    comes back and reverts at once, which is the right answer - nobody
    confirmed.

    It holds a whole configuration, password hashes included, so it lives
    under the same group and the same umask as the rollback store.
    """

    HEADER = "# prisma-routing commit confirmed"

    def __init__(self, path=CONFIRM_PATH):
        self.path = path

    def arm(self, deadline, config_text, minutes, attempts=0):
        """
        Record the configuration to go back to, and when.

        Written whole and then renamed into place. A reader here is a unit
        firing every few seconds, and a half-written file it happened to catch
        would be a configuration this box would then apply to itself.
        """
        tmp = self.path + ".new"
        old_umask = os.umask(CONFIG_UMASK)
        try:
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(tmp, "w") as fh:
                fh.write(f"{self.HEADER}\n")
                fh.write(f"# deadline {int(deadline)}\n")
                fh.write(f"# minutes {minutes}\n")
                fh.write(f"# attempts {int(attempts)}\n")
                fh.write(config_text)
            os.replace(tmp, self.path)
        finally:
            os.umask(old_umask)

    def read(self):
        """
        What is pending, or None - which is also the answer for "confirmed".

        A file that cannot be parsed is NOT treated as nothing pending. It is
        reported as pending with a deadline already passed, so the box acts on
        it instead of quietly dropping a revert somebody is relying on.

        A file this account cannot READ is a different thing and answers None.
        This is called from the banner, which every login draws - including a
        read-only account, which is outside the group that owns
        /etc/prisma-routing and gets EACCES here. Raising made prismash exit 1 on
        that login, and prismash-login then handed the account a bash prompt to
        keep the box reachable: the whole VTY replaced by a shell, for every
        account without a role, because of a window that may not even be open.
        Nothing this process could do about it either way - the box's own copy
        of the deadline is enforced by root.
        """
        try:
            with open(self.path) as fh:
                lines = fh.read().splitlines(True)
        except OSError:
            return None
        head = {}
        body = []
        for line in lines:
            if line.startswith("# ") and not body:
                w = line[2:].split()
                if len(w) == 2:
                    head[w[0]] = w[1]
                continue
            body.append(line)
        try:
            deadline = float(head["deadline"])
        except (KeyError, ValueError):
            deadline = 0.0
        try:
            attempts = int(head.get("attempts", 0))
        except ValueError:
            attempts = 0
        return {"deadline": deadline,
                "minutes": head.get("minutes", "?"),
                "attempts": attempts,
                "config": "".join(body)}

    def count_attempt(self, pending):
        """Record that a revert was tried, so a hopeless one stops trying."""
        pending["attempts"] += 1
        self.arm(pending["deadline"], pending["config"], pending["minutes"],
                 pending["attempts"])
        return pending["attempts"]

    def clear(self):
        """Confirmed, or reverted. Either way there is nothing left to do."""
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass

    def give_up(self):
        """
        Move a revert this box could not perform out of the way.

        Kept rather than deleted: it is the configuration somebody has to put
        back by hand, and deleting it would leave them with the change they
        did not confirm and no statement of what preceded it.
        """
        dead = self.path + ".failed"
        try:
            os.replace(self.path, dead)
        except OSError:
            self.clear()
        return dead


#: The blocks this product models itself, as opposed to the FRR blocks tracked
#: in `frr_ctx`. They are modes rather than context entries, which is why they
#: needed naming: two places have to know the same list, and before #221 only
#: one of them did. `config_depth` reads it as well: a sub-mode is a level, so
#: `up` leaves one of these the same way it leaves an FRR block.
BLOCK_MODES = ("pool", "user", "snmp-user")

#: The words every configuration level owns, and what `?` says about each.
#:
#: They are not in `TREE` on purpose. `TREE` is walked per mode and per block,
#: and these work at every level of configuration mode - so an entry in the
#: tree would be an entry in one node, which is the shape of the defect #400
#: was: a word that worked in the mode it was written into and nowhere else.
LEVEL_WORDS = {
    "top": "go to the top of the configuration",
    "up": "go up one level, or <n> levels",
}


def is_pci_addr(name):
    """
    Is `name` a PCI address as sysfs spells it: 0000:3b:00.0

    Domain, bus, then device.function, all hexadecimal. Checked by SHAPE
    rather than by a looser pattern, because what it decides is which
    directory gets handed to DPDK.
    """
    parts = name.split(":")
    if len(parts) != 3 or "." not in parts[2]:
        return False
    dev, _, func = parts[2].partition(".")
    hexd = "0123456789abcdefABCDEF"
    # The WIDTHS sysfs uses: a four-digit domain (wider on a box with many),
    # two-digit bus, two-digit device, one-digit function. Without them
    # 'a:b:c.d' is accepted as an address, and what this decides is which
    # directory is handed to DPDK.
    return bool(len(parts[0]) >= 4 and len(parts[1]) == 2
                and len(dev) == 2 and len(func) == 1
                and all(c in hexd for c in parts[0] + parts[1] + dev + func))


def pci_address_of(device_link):
    """
    The PCI address behind a `/sys/class/net/<if>/device` link, or "".

    WALKED UP rather than read off the first component. That link points at
    the device which OWNS the netdev, and on a virtio guest that is the virtio
    bus device, whose parent is the PCI one:

        /sys/class/net/ens18/device -> ../../../virtio3
        realpath: /sys/devices/pci0000:00/0000:00:12.0/virtio3

    The basename is `virtio3`, so a reading that stopped there answered "not
    PCI" for every NIC in every VM, and `show hardware` printed that a virtio
    guest simply has nothing to choose from (#286). DPDK drives virtio through
    its own poll-mode driver; the card was always eligible.

    The SAME walk as `nic_pci()` in appliance/prisma-routing-tune, and it has to
    be: the tuner decides what goes into startup.conf and this decides what
    the operator is offered, so a box where the two disagree offers a card the
    tuner then refuses.
    """
    try:
        path = os.path.realpath(device_link)
    except OSError:
        return ""
    while path and path != "/":
        base = os.path.basename(path)
        if is_pci_addr(base):
            return base
        path = os.path.dirname(path)
    return ""


class Shell:
    def __init__(self):
        self.sys = System()
        self.snmp = SnmpDaemon()
        self.dp = Vpp()
        self.api = vppapi.Vat2()
        self.frr = Frr()
        self.rb = Rollback()
        self.confirm = ConfirmPending()
        self.caps = capabilities()
        # Lines the dataplane returned about subscribers changing mapping,
        # refilled by every check. A commit that would move them is refused
        # until the operator says so in as many words.
        self.remap = []
        # The prefix carrying THIS session that the candidate would stop
        # advertising, refilled by every check. Same posture as `remap` above
        # and for the same reason: a change that is a mistake most times it is
        # typed and deliberate some of them is gated, not refused.
        self.cutoff = []
        # The pools this candidate would map differently from the peer's own
        # configuration, refilled by every check, and only ever filled on a
        # box that is IN a pair - one with a virtual router on an interface.
        # Same posture as the two above: gated on a word, not refused.
        self.diverge = []
        self.mode = "operational"      # operational | config | pool | user
        self.pool = None               # pool being edited
        self.user = None               # user being edited
        self.snmp_user = None          # SNMP v3 user being edited
        self.candidate = None          # Config being edited
        # A static route parsed by hierarchy_rewrite and waiting for FRR's
        # answer before it is staged: (key, description) or None (#436).
        self.static_pending = None
        self.frr_ctx = []              # FRR block context inside config mode
        # The `nat` > `cgnat` levels (#446): [], ["nat"] or ["nat", "cgnat"].
        # A pool is entered from inside them and returns to them.
        self.nat_ctx = []
        self.failed = 0
        # The statements a `prismash -c` batch asked for and did not get. It is
        # what `commit` refuses on, it is empty in an interactive session, and
        # feed_batch() below carries the argument for both halves of that.
        self.batch_refused = []
        # The in-session half of "commit confirmed". It is the fast path and
        # the fallback, never the promise: what makes the promise true is the
        # file ConfirmPending writes (#212).
        self.confirm_deadline = None
        self.confirm_backup = None
        self.confirm_on_box = False

    # ---------------------------------------------------------- prompt

    def where(self):
        """
        The hierarchy being edited, as words - "nat / cgnat / pool
        residential".

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
            return f"{NAT_HIERARCHY} / {CGNAT_LEVEL} / pool {self.pool.name}"
        if self.mode == "user" and self.user is not None:
            return f"user {self.user.name}"
        if self.mode == "snmp-user" and self.snmp_user is not None:
            return f"snmp user {self.snmp_user.name}"
        if self.frr_ctx:
            # Nested blocks show both, innermost last, so the reader can see
            # what contains what.
            return " / ".join(self.frr_ctx)
        if self.nat_ctx:
            return " / ".join(self.nat_ctx)
        return ""

    #: An 80-column serial console is what a racked box is seen through, and a
    #: prompt that pushes the cursor off the line is worse than a vague one.
    PROMPT_MAX = 44

    def prompt(self):
        if self.mode == "operational":
            return "prisma> "
        where = self.where()
        if not where:
            return "prisma# [edit] "
        if len(where) > self.PROMPT_MAX:
            # Keep the END: the innermost block is where the next statement
            # lands, and it is the half worth the space.
            where = "..." + where[-(self.PROMPT_MAX - 3):]
        return f"prisma# [edit {where}] "

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
            print_prose(f"membership of {GROUP_CGNAT} allows CGNAT "
                        f"configuration, {GROUP_ADMIN} allows everything")
        else:
            print_prose(f"this change needs {GROUP_ADMIN}")
        return False

    def err(self, msg):
        print(f"error: {msg}")
        self.failed += 1

    def err_prose(self, headline, *rest):
        """
        A refusal whose reason is a paragraph: the verdict, then the prose.

        `err` wraps nothing, which is right - a refusal's first row is the
        verdict and an operator reads it whole. What is not right is handing
        it a paragraph: seven refusals were composed as one long sentence and
        reached the terminal at 104 to 185 columns on a console of 79, folded
        by the terminal at whatever width the window happened to be (#355).
        The advice in every one of them is at the END of the sentence, which
        is where that fold lands.

        So the verdict goes to `err` and the rest through `print_prose`, which
        is the split #271 made for `say_finding` and #349 made for the
        soft-reconfiguration refusals. Only `failed` is raised once, because
        it is one refusal however many rows it takes.
        """
        self.err(headline)
        for line in rest:
            print_prose(line)

    def err_dataplane(self, msg):
        """
        `err`, for a refusal the DATAPLANE produced rather than this VTY.

        The difference is not cosmetic and it is the whole of #293. Between
        `appliance/install.sh` finishing and the box being rebooted, the new
        control plane is talking to the dataplane from before the upgrade, and
        a message that grew a field the running plugin does not have comes
        back as a refusal naming a symbol:

            error: pool 'split291': Call failed: bpa_pool_add_del
            error: commit failed; rolling back to the previous state

        Measured on a box installed from v0.68.0 and upgraded with no reboot.
        Nothing on that screen says the thing answering is the old build, that
        the upgrade is why, or what clearing it would cost - so it reads as a
        broken product rather than as a consequence of the upgrade that had
        just been run.

        WHICH REFUSALS GET IT. Exactly the ones carrying a reason out of a
        `vppapi.Reply` that was not OK: the dataplane answered, or failed to
        answer, a message this VTY sent it. A grammar error, a permission
        refusal, or a check the VTY made itself must never blame an upgrade -
        those never reach here, and they are the majority of what `err`
        prints.
        """
        self.err(msg)
        self.say_version_split()

    def say_version_split(self):
        """
        The sentence under a dataplane refusal on a box that is mid-upgrade,
        and nothing at all on any other box.

        Said only where it is TRUE, which is what `version_split` is for. A
        box running out of a source tree, or with no version stamp, has
        nothing to compare and must not be told an upgrade is why its commit
        failed - that is the same conflation this reading was fixed for once
        already (#291), reached from the other side.

        NOT A LOCK-OUT, and that is a decision rather than an omission. A box
        between an upgrade and its reboot still forwards, still routes, and
        still takes most changes; refusing to configure it would cost more
        than the split does. A refusal that explains itself is the smallest
        honest thing.

        The build goes on a row of its own and not inside the prose. It
        carries a space - `0.71.0 (4a7044e)` is a version, a space and a
        commit - so wrapped into a sentence it is a token a fold can break in
        half, which is the trap #252 named and the one thing on this screen
        the operator has to read whole.
        """
        build = self.dp.plugin_build()
        mine, source = prisma_routing_version_source()
        if not version_split(build, mine, source):
            return
        print_prose("this refusal came back from the dataplane, and the "
                    "dataplane is not the build the rest of this box is: it "
                    "is still the one it was loaded with at the last boot. "
                    "An upgrade replaces the dataplane's files on disk and "
                    "does not restart it, so the new control plane is asking "
                    "the plugin from before the upgrade for things that "
                    "build does not have.")
        print(f"    the dataplane is running {build}")
        print_prose("'show version' prints that beside this box's own build. "
                    f"'systemctl restart {dataplane_unit()}', or a reboot, "
                    "makes the new dataplane answer instead - and either one "
                    "ends every session and every port block this box is "
                    "carrying.")

    def say_finding(self, mark, stmt, why):
        """
        One check's finding: the reason, wrapped, and the statement below it.

        WHY this is not `print(f"{mark}: {stmt}: {why}")`, which is what the
        BGP, IS-IS, OSPF, route-map, AS-path and prefix-range checks all did.
        That row carried a QUOTED STATEMENT and PROSE under one budget, and
        neither half can be guaranteed there: a statement MUST be printed
        whole because the operator greps `show configuration` for it and types
        it back, and a sentence MUST be wrapped because it is read. #271 split
        the two rows of this shape that it measured over and left these,
        because none of them measured over on the fixtures it drove - a limit
        it recorded rather than a clean bill. Driven on real input they are
        104 to 264 columns: a real route-map name, an IPv6 neighbour, an
        IPv6 prefix-list (#276).

        The reason goes first because it is the verdict, and `err` prints one
        row and wraps nothing (#252), so only its first row shares the marked
        line and the rest goes under it. The statement is last, on a row of
        its own, which is the shape the unreachable-next-hop refusal and the
        unchecked-VRF note beside it already use.
        """
        # The marked row has the mark's width less to spend, so the reason is
        # wrapped against an indent that wide and the mark is written over it.
        rows = console_prose(why, indent=" " * len(f"{mark}: ")) or [""]
        head = rows[0].lstrip()
        if mark == "error":
            self.err(head)
        else:
            print(f"{mark}: {head}")
        for row in rows[1:]:
            print("    " + row.lstrip())
        print(f"    {stmt}")

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
            cfg = Config.parse(out, dataplane=True)
        if cfg.unknown:
            self.err("the dataplane reports configuration this VTY does not "
                     "model")
            print_prose("committing would REMOVE it:")
            for lineno, text, why in cfg.unknown:
                # The line whole, the reason wrapped under it. See the same
                # split in do_commit (#271).
                print(f"    line {lineno}: {text}")
                print_prose(f"({why})", indent="        ")
        cfg.mtus = self.device_mtus()
        cfg.urpf = self.device_urpf()
        cfg.descriptions = self.device_descriptions()
        cfg.addresses = self.device_addresses()
        cfg.vlans = self.device_vlans()
        cfg.vrfs = self.device_vrfs()
        cfg.macs = self.device_macs()
        cfg.linux_names = self.device_linux_names()
        cfg.loopbacks = self.device_loopbacks()
        cfg.admin_down = self.device_admin_down()
        cfg.ra_enabled = self.device_ra_enabled()
        cfg.vrrp = self.device_vrrp()
        # Read from the box, not from what this VTY last wrote: the same
        # reason the CGNAT half is read back from the dataplane.
        cfg.ssh = self.sys.read_ssh()
        cfg.dns_servers = self.sys.read_dns()
        # From the tuner's own input, which is the only place that knows. Read
        # back rather than remembered for the same reason as everything above,
        # and with one extra consequence: editing that file by hand was the
        # ONLY way to set this until now, so a box configured the old way shows
        # its card in `show running-config` without anybody re-typing it.
        cfg.dataplane_if = self.read_dataplane_if()
        cfg.users = self.sys.read_users()
        cfg.snmp, cfg.snmp_communities, cfg.snmp_users = self.snmp.read()
        cfg.routing = self.frr.running()
        cfg.static_descriptions = self.read_static_descriptions(cfg.routing)
        # The Linux end of every linux_cp pair, out. The dataplane reads above
        # report EVERY interface VPP has, and one of them is the pair's own
        # TAP - so `save` wrote `interface tap4097` into the file that
        # prisma-routing.service replays at boot, against a VPP with no pairs
        # yet, and the box refused its own configuration and came up with
        # nothing on it. config.Config.drop_lcp_taps carries the measurement.
        #
        # Which TAPs those are is READ OFF THE BOX rather than guessed from
        # the name, because `tap0` is equally what an operator's own
        # `create tap` produces and dropping that would be #47.
        cfg.drop_lcp_taps(self.device_lcp_taps())
        return cfg

    def remove_static_route(self, key):
        """
        Take the candidate's top-level static route with this key out, and
        its description with it (#436).

        Top-level only: a route inside a `vrf` block is in another table and
        is spelled the same, so matching it by words alone would remove a
        route from a table the operator is not in.
        """
        routing = self.candidate.routing
        paths = block_paths(routing)
        self.candidate.routing = [
            line for path, line in zip(paths, routing)
            if not (path == [] and is_static_route(line.strip())
                    and static_route_key(line.strip()) == key)]
        self.candidate.static_descriptions.pop(key, None)

    def read_static_descriptions(self, routing) -> dict:
        """
        {route key: text} from STATIC_DESC_PATH, for the routes FRR has.

        Read back like everything running_config() returns, and FILTERED by
        what FRR holds: a description whose route is gone - removed at
        vtysh, or never applied - describes nothing, and showing it would be
        the box claiming a route it has not got (#436). A file that cannot be
        read is no descriptions, not a failure: the routes still route.
        """
        have = {static_route_key(line)
                for path, line in zip(block_paths(routing), routing)
                if path == [] and is_static_route(line.strip())}
        out = {}
        try:
            with open(STATIC_DESC_PATH) as f:
                for row in f:
                    key, sep, text = row.rstrip("\n").partition("\t")
                    if sep and text and key in have:
                        out[key] = text
        except OSError:
            pass
        return out

    def write_static_descriptions(self, descriptions) -> bool:
        """
        Write STATIC_DESC_PATH, whole, by rename - so a reader never sees half.

        One route per row, its key and its text separated by a tab. Neither
        can hold one: a key is FRR's words and a description refuses control
        characters at both doors (text_error). False, and said, when it could
        not be written; the routes are applied either way.
        """
        old_umask = os.umask(CONFIG_UMASK)
        tmp = STATIC_DESC_PATH + ".tmp"
        try:
            with open(tmp, "w") as f:
                for key in sorted(descriptions):
                    f.write(f"{key}\t{descriptions[key]}\n")
            os.replace(tmp, STATIC_DESC_PATH)
            return True
        except OSError as e:
            self.err_prose("static route descriptions: not written",
                           f"{STATIC_DESC_PATH}: {e.strerror}",
                           "the routes are applied; their descriptions are "
                           "what this box could not keep")
            return False
        finally:
            os.umask(old_umask)

    def device_lcp_taps(self) -> set:
        """
        The TAPs that are the Linux end of a pair, from `show lcp`.

        A pair prints every name it answers to on one line:

            itf-pair: [0] host-veth-vpp tap4096 vppwan0 5 type tap

        and the one that is VPP's own spelling of a TAP is the pair's. Both
        signals together: on the line AND shaped like an lcp TAP. Either alone
        is wrong - the line also carries the dataplane name and the Linux one,
        and the shape alone matches a TAP nobody paired.
        """
        rc, out = self.dp.run("show lcp")
        if rc != 0:
            return set()
        return {n for names in routes.lcp_pairs(out).values()
                for n in names if _is_lcp_tap(n)}

    def fib_table_names(self):
        """
        {name: table_id} for the IPv4 FIB tables, or {} if it cannot be asked.

        `show ip fib` prints a NAMED table's name where it prints an unnamed
        one's id, and no vppctl command maps one to the other. Without this map
        a named table's prefixes were filed under whichever table came before
        it, and `set_vrf()` names every table it creates (#268).

        An empty map is not a failure: it means a caller keys those prefixes by
        the name, which no numeric lookup will match, so the answer is "not
        known here" rather than "the global table has it".
        """
        r = self.api.dump("ip_table_dump", {})
        if r.state != vppapi.OK:
            return {}
        return routes.table_names(r.fields.get("rows", []))

    def device_descriptions(self) -> dict:
        """
        The description of every interface, from the dataplane's own tags.

        VPP stores a tag per interface and returns it in sw_interface_dump, so
        this is read back from the box like the MTU and the uRPF mode - not
        remembered by the VTY between invocations, which it deliberately does
        not do.
        """
        r = self.api.dump("sw_interface_dump",
                          {"sw_if_index": 0xFFFFFFFF,
                           "name_filter_valid": False, "name_filter": ""})
        if r.state != vppapi.OK:
            return {}
        out = {}
        for row in r.fields.get("rows", []):
            tag = (row.get("tag") or "").strip()
            if tag:
                out[row["interface_name"]] = tag
        return out

    def device_vlans(self) -> dict:
        """
        The sub-interfaces the dataplane has, and the tags they match.

        From sw_interface_dump, which carries sub_id and the two VLAN ids. A
        sub-interface with no tags is not one this VTY models, so it is left
        out rather than reported as something it is not.
        """
        r = self.api.dump("sw_interface_dump",
                          {"sw_if_index": 0xFFFFFFFF,
                           "name_filter_valid": False, "name_filter": ""})
        if r.state != vppapi.OK:
            return {}
        out = {}
        for row in r.fields.get("rows", []):
            if not row.get("sub_number_of_tags"):
                continue
            inner = row.get("sub_inner_vlan_id") or None
            out[row["interface_name"]] = {
                "outer": row.get("sub_outer_vlan_id") or row.get("sub_id"),
                "inner": inner,
            }
        return out

    def device_macs(self) -> dict:
        """
        The MAC each interface answers to, from sw_interface_dump.

        Every interface has one, so this reports them all - the same choice
        the MTU makes, and for the same reason: "explicitly configured" is not
        a property the dataplane can answer, and inventing the distinction
        would mean the VTY remembering things between invocations.
        """
        r = self.api.dump("sw_interface_dump",
                          {"sw_if_index": 0xFFFFFFFF,
                           "name_filter_valid": False, "name_filter": ""})
        if r.state != vppapi.OK:
            return {}
        out = {}
        for row in r.fields.get("rows", []):
            mac = (row.get("l2_address") or "").lower()
            if mac and mac != "00:00:00:00:00:00":
                out[row["interface_name"]] = mac
        return out

    def device_vrfs(self) -> dict:
        """
        The routing table each interface's addresses live in.

        One call per interface, because sw_interface_dump does not carry it.
        Table 0 is left out: it is the default, every untouched interface is
        in it, and recording it would put a `vrf 0` line under every port on
        every box.
        """
        rc, out = self.dp.run("show interface")
        if rc != 0:
            return {}
        found = {}
        for name in routes.parse_interfaces(out):
            if name == "local0":
                continue
            idx = self.api.interface_index(name)
            if idx is None:
                continue
            r = self.api_call("sw_interface_get_table",
                              {"sw_if_index": idx, "is_ipv6": False})
            if r.state == vppapi.OK and r.fields.get("vrf_id"):
                found[name] = int(r.fields["vrf_id"])
        return found

    def device_addresses(self) -> dict:
        """
        Every address the dataplane holds, per interface.

        Read from `show interface addr`, the same source `show interface`
        already reports from, so the running configuration and the operational
        view cannot disagree about what is on a port.
        """
        rc, out = self.dp.run("show interface")
        if rc != 0:
            return {}
        _, addr = self.dp.run("show interface addr")
        found = routes.parse_interfaces(out, addr)
        return {name: list(info["addresses"])
                for name, info in found.items()
                if info["addresses"] and name != "local0"}

    def device_ra_enabled(self) -> set:
        """
        The interfaces actually advertising this box as a router.

        Read, not remembered. Without this the running configuration always
        said "suppressed", so `delta_to` never produced an ra_off: an operator
        who had enabled advertisements could not turn them back off, and
        `show running-config` did not admit they were on. The lab caught it.

        `show ip6 interface` prints the same text either way - measured - so
        the text path cannot answer this. The dump can: `send_radv` is the
        field, and it is false on an interface that was never asked.
        """
        r = self.api.dump("sw_interface_ip6nd_ra_dump",
                          {"sw_if_index": 0xFFFFFFFF})
        if r.state != vppapi.OK:
            return set()
        names = {}
        rc, out = self.dp.run("show interface")
        if rc == 0:
            for name in routes.parse_interfaces(out):
                idx = self.api.interface_index(name)
                if idx is not None:
                    names[idx] = name
        on = set()
        for row in r.fields.get("rows", []):
            if row.get("send_radv") and not row.get("cease_radv"):
                name = names.get(row.get("sw_if_index"))
                if name:
                    on.add(name)
        return on

    def device_vrrp(self) -> dict:
        """
        The virtual routers the DATAPLANE holds, shaped like Config.vrrp.

        Read rather than remembered, for the reason `device_ra_enabled` gives:
        a setting this VTY only remembers is one an operator cannot turn off,
        and one `show running-config` would deny was on.

        Two dumps, because VPP keeps the tracked interfaces apart from the
        virtual router: `vrrp_vr_dump` carries the addresses and the priority,
        `vrrp_vr_track_if_dump` carries what each one watches. A VR read
        without its tracking would come back as a change on the next delta and
        the commit would remove the tracking nobody touched.
        """
        r = self.api.dump("vrrp_vr_dump", {"sw_if_index": 0xFFFFFFFF})
        if r.state != vppapi.OK:
            return {}
        names = self.interface_names_by_index()
        out = {}
        for row in r.fields.get("rows", []):
            conf = row.get("config", {})
            name = names.get(conf.get("sw_if_index"))
            if name is None:
                continue
            vrid = conf.get("vr_id")
            addrs = [a for a in row.get("addrs", []) if ":" not in str(a)]
            out.setdefault(name, {})[vrid] = {
                "addresses": addrs,
                "priority": conf.get("priority", VRRP_PRIORITY_DEFAULT),
                "track": self.device_vrrp_track(conf.get("sw_if_index"),
                                                vrid, names),
            }
        return out

    def device_vrrp_track(self, idx, vrid, names) -> dict:
        """What one virtual router watches: interface name -> decrement."""
        r = self.api.dump("vrrp_vr_track_if_dump",
                          {"sw_if_index": idx, "vr_id": vrid, "is_ipv6": 0,
                           "dump_all": 0})
        if r.state != vppapi.OK:
            return {}
        out = {}
        for row in r.fields.get("rows", []):
            for t in row.get("ifs", []):
                name = names.get(t.get("sw_if_index"))
                if name:
                    out[name] = t.get("priority")
        return out


    def device_vrrp_state(self) -> list:
        """
        One row per virtual router: interface, id, role, the MAC it answers
        with, and the priority it is advertising RIGHT NOW.

        The advertised priority is not the configured one when an interface
        being tracked is down, and that difference is the whole point of
        tracking - so the screen prints what the box is advertising and says
        what was configured beside it when the two differ.
        """
        r = self.api.dump("vrrp_vr_dump", {"sw_if_index": 0xFFFFFFFF})
        if r.state != vppapi.OK:
            return []
        names = self.interface_names_by_index()
        rows = []
        for row in r.fields.get("rows", []):
            conf = row.get("config", {})
            run = row.get("runtime", {})
            rows.append({
                "interface": names.get(conf.get("sw_if_index"),
                                       f"sw_if_index {conf.get('sw_if_index')}"),
                "vr_id": conf.get("vr_id"),
                "configured": conf.get("priority"),
                "advertising": run.get("tracking", {}).get(
                    "priority", conf.get("priority")),
                "state": VRRP_STATE_SAYS.get(str(run.get("state")),
                                             str(run.get("state"))),
                "mac": run.get("mac"),
                "addresses": [a for a in row.get("addrs", [])],
            })
        return sorted(rows, key=lambda x: (x["interface"], x["vr_id"] or 0))

    def interface_names_by_index(self) -> dict:
        """
        sw_if_index -> name, for every interface `show interface` lists.

        One dump for the whole map rather than interface_index() per name,
        which is a sw_interface_dump each time.
        """
        names = {}
        rc, out = self.dp.run("show interface")
        if rc != 0:
            return names
        for name in routes.parse_interfaces(out):
            idx = self.api.interface_index(name)
            if idx is not None:
                names[idx] = name
        return names

    def say_vrrp_refusal(self, refusal):
        """
        The verdict on its own row, the reason folded under it.

        config.py hands both halves back rather than one sentence, because a
        verdict long enough to carry the reason reaches the console folded at
        whatever width the window happens to be (#355).
        """
        verdict, prose = refusal
        if prose:
            self.err_prose(verdict, prose)
        else:
            self.err(verdict)

    def peer_divergence(self, target: Config) -> list:
        """
        The pools this configuration would map differently from the peer's,
        or an empty list - which also means "this box is not in a pair".

        THE TRAP THIS IS ABOUT, and `docs/ha.md` states it at the top: in
        deterministic mode the mapping is a function of the configuration, so
        two boxes with the SAME pool need no state to agree - and two boxes
        with different pools fail SILENTLY. The failover starts mapping
        subscribers to blocks the other box never used, both boxes log
        nothing unusual, because nothing unusual happened to either of them,
        and the compliance record says a subscriber was somewhere they were
        not.

        ONLY ON A BOX WITH A VIRTUAL ROUTER. A box with no `vrrp` statement is
        not in a pair, whatever files somebody left in /etc: gating its
        commits on a peer configuration would be this VTY deciding that a
        copied file means a relationship.

        AND ONLY WHEN THE PEER'S CONFIGURATION CAN BE READ. Absent,
        unreadable, or naming no pool is NOT agreement and is not divergence
        either - it is an unanswered question, and `show cgnat ha` is where
        that is answered. Refusing commits over a file nobody copied would
        stop a box being configured at all.
        """
        if not target.vrrp:
            return []
        peer, _why = read_peer_config()
        if peer is None:
            return []
        return target.mapping_diff_to(peer)

    def say_divergence_refusal(self):
        """
        What `commit` refuses over, and the word that gets past it.

        The word exists because a divergence is NOT always a mistake: two
        boxes cannot be committed in the same instant, so every correct
        capacity change diverges the pair between the first commit and the
        second. A refusal with no way past it would fire on every correct
        procedure, and the way past it would become "stop copying the peer's
        configuration" - which removes the check rather than the divergence
        (`docs/ha.md`).
        """
        self.err_prose("this change maps subscribers differently from the "
                       "peer",
                       "this box stands for a service address with another "
                       "one, so a takeover would translate them there")
        # THE SAME RENDERER `show cgnat ha` USES. Two ways of printing one
        # comparison are two things to keep in step, and the one nobody looks
        # at goes stale - which is the shape this repository keeps paying for.
        self.say_divergence("error", self.diverge)
        print_prose("a takeover would put subscribers on blocks the other "
                    "box never used, and neither box would log anything "
                    "unusual, because nothing unusual happened to either of "
                    "them")
        print("    if that is what you mean, type: commit diverge")

    def say_vrrp_restart(self, ifname, vrid):
        """
        What changing a virtual router costs, said before it is changed.

        A VR cannot be edited in place - neither by this VTY's delete-and-add
        nor by VPP's own `vrrp_vr_update`, which was measured doing exactly the
        same stop and start. So changing ANY field of a router that is holding
        a service address hands that address to the peer for about a
        master-down interval, and hands it back when preempt fires: two
        elections and a few seconds of interruption, for a change the operator
        may have thought was about something else entirely.

        Said and not refused. It is an ordinary thing to want to do, and a box
        that refused it would be a box that cannot be reconfigured; what an
        operator needs is to know it is a maintenance-window change rather
        than a keystroke.
        """
        print_prose(f"changing vrrp {vrid} on {ifname} restarts it: this box "
                    "stops advertising while it is rebuilt, the peer takes "
                    "the address, and preempt hands it back - two elections, "
                    "a few seconds apart", indent="  ")

    def set_vrrp(self, ifname, vrid, vr) -> bool:
        """
        Put one virtual router on a dataplane interface, over the binary API.

        REPLACED WHOLE, not edited: the existing router is deleted and built
        again from what the configuration says. A VR that is master with the
        old address while the new one is being added would answer for both,
        and an election in progress is not a thing to walk through three
        intermediate states of.

        AND VPP'S OWN `vrrp_vr_update` IS NOT AN IN-PLACE EDIT EITHER, which
        is worth writing down because the name says otherwise and somebody
        will reach for it. Measured on 26.06 against a running master:
        updating the priority through `vrrp_vr_update` logs
        `transitioning to Initialize` and then `transitioning to Backup` -
        the same stop and start this does, with the same gap. So there is
        nothing to gain by moving to it, and `say_vrrp_restart` below is the
        honest answer instead: tell the operator what the change costs.

        `PREEMPT` is on, and it is ONE flag because the client can carry one.

        Measured, not assumed: `vrrp_vr_flags` is declared in `vrrp.api` as an
        `enum` rather than an `enumflags`, so vat2 encodes exactly one of its
        names. `"VRRP_API_VR_PREEMPT|VRRP_API_VR_ACCEPT"` comes back "Failed
        converting JSON to API", a list of two names SEGFAULTS the client, and
        so does the integer 3 - which is a client defect worth knowing about
        and not something to route around by guessing.

        So the choice is preempt or accept, and it is preempt: the priorities
        in the configuration are the operator's statement of which box should
        hold the service when both are healthy, and without preempt a box that
        comes back from maintenance stays backup until the other one fails -
        the pair drifts away from what the configuration says and nothing
        reports it.

        WHAT NOT HAVING ACCEPT COSTS, stated because an operator will meet it:
        the virtual address does not answer ICMP. RFC 5798 section 6.4.3 makes
        that the DEFAULT behaviour of a router that is not the address owner,
        so this is the RFC's answer rather than a shortcut, but a monitoring
        system that pings the gateway to decide whether the network is up will
        see nothing. It should ping each box's own address, which is what says
        whether that box is alive - the virtual address only says which of
        them is holding it. Forwarding THROUGH the virtual address is
        unaffected and is what lab/vrrp.sh measures, because that is the
        service: a CPE sends to the virtual MAC and the master routes it.
        """
        idx = self.api.interface_index(ifname)
        if idx is None:
            self.err(f"interface {ifname}: the dataplane does not have it")
            return False
        if not vr["addresses"]:
            self.err_prose(f"interface {ifname}: vrrp {vrid} has no address",
                           "a virtual router with nothing to stand for would "
                           "elect a master that answers for nothing")
            return False
        self.del_vrrp(ifname, vrid, quiet=True)
        r = self.api_call("vrrp_vr_add_del",
                          {"is_add": 1, "sw_if_index": idx, "vr_id": vrid,
                           "priority": vr["priority"],
                           "interval": VRRP_INTERVAL_CS,
                           "flags": "VRRP_API_VR_PREEMPT",
                           "n_addrs": len(vr["addresses"]),
                           "addrs": vr["addresses"]})
        if r.state != vppapi.OK:
            self.err_dataplane(f"interface {ifname}: vrrp {vrid}: {r.reason}")
            return False
        for tracked, dec in sorted(vr["track"].items()):
            t_idx = self.api.interface_index(tracked)
            if t_idx is None:
                self.err_prose(f"interface {ifname}: vrrp {vrid}: no "
                               f"interface {tracked}",
                               "the dataplane does not have the interface "
                               "this virtual router was told to track")
                return False
            r = self.api_call("vrrp_vr_track_if_add_del",
                              {"is_add": 1, "sw_if_index": idx,
                               "vr_id": vrid, "is_ipv6": 0, "n_ifs": 1,
                               "ifs": [{"sw_if_index": t_idx,
                                        "priority": dec}]})
            if r.state != vppapi.OK:
                # The tracked interface and not this one, because two
                # interface names on one row put it past the console's width
                # and the fold lands inside them (#355). The interface this
                # virtual router is on is the block the operator is in.
                self.err_dataplane(f"vrrp {vrid} track {tracked}: {r.reason}")
                return False
        # STARTED HERE and not by the operator. A virtual router that is
        # configured and not started is a box that took the statement, says it
        # has it, and never sends an advertisement - so the peer with the
        # lower priority holds the address and the operator's configuration is
        # a description of a takeover that will not happen.
        r = self.api_call("vrrp_vr_start_stop",
                          {"sw_if_index": idx, "vr_id": vrid, "is_ipv6": 0,
                           "is_start": 1})
        if r.state != vppapi.OK:
            self.err_dataplane(f"interface {ifname}: vrrp {vrid}: "
                               f"start: {r.reason}")
            return False
        return True

    def del_vrrp(self, ifname, vrid, quiet=False) -> bool:
        """
        Take a virtual router off an interface: stopped first, then deleted.

        Stopping first is what makes a withdrawal orderly. A master that is
        deleted while it is still advertising leaves the peer waiting out the
        master-down interval; one that is stopped sends a zero-priority
        advertisement, which is RFC 5798's way of saying "take it now".
        """
        idx = self.api.interface_index(ifname)
        if idx is None:
            if quiet:
                return True
            self.err(f"interface {ifname}: the dataplane does not have it")
            return False
        self.api_call("vrrp_vr_start_stop",
                      {"sw_if_index": idx, "vr_id": vrid, "is_ipv6": 0,
                       "is_start": 0})
        r = self.api_call("vrrp_vr_add_del",
                          {"is_add": 0, "sw_if_index": idx, "vr_id": vrid,
                           "priority": VRRP_PRIORITY_DEFAULT,
                           "interval": VRRP_INTERVAL_CS,
                           "flags": "VRRP_API_VR_PREEMPT",
                           "n_addrs": 0, "addrs": []})
        if r.state != vppapi.OK and not quiet:
            self.err_dataplane(f"interface {ifname}: no vrrp {vrid}: "
                               f"{r.reason}")
            return False
        return True

    def dataplane_names(self) -> set:
        """
        Every name `show interface` lists, or an empty set if it cannot be
        asked: the list routes.linux_end() places a pair's members with.

        Read once per mirror rather than asked of the API per member:
        interface_index() is a whole sw_interface_dump each time.
        """
        rc, out = self.dp.run("show interface")
        if rc != 0:
            return set()
        return set(routes.parse_interfaces(out))

    def device_admin_down(self) -> set:
        """
        The interfaces whose being down is a `shutdown` SOMEBODY TYPED.

        Read from `show interface`, not from sw_interface_dump: the dump
        returns the flags as an enum vat2 renders as "Invalid ENUM", so the
        one field that would answer this is the one that does not survive the
        JSON. Captured from a running dataplane rather than assumed.

        A SUB-INTERFACE WHOSE PARENT IS DOWN IS LEFT OUT (#407). VPP will not
        bring one up while its super-interface is down, so a box that recorded
        that as `shutdown` would put a statement in the running configuration
        that nobody wrote - and the candidate an operator loads from it would
        then say the interface is meant to be down, so bringing the parent up
        later would leave the child behind for a reason nothing on the screen
        explains. Measured: a sub-interface does NOT follow its parent up by
        itself, so the configuration saying "up" is the only thing that will
        ever bring it up.

        The parent's own `shutdown` is still recorded, because that one IS a
        statement somebody made.
        """
        rc, out = self.dp.run("show interface")
        if rc != 0:
            return set()
        # local0 is excluded for the same reason device_mtus() excludes it: it
        # is not an interface anybody configures, it is always down, and
        # reporting it would put "shutdown" in the running configuration of
        # every box on earth - which is also how a test asserting " shutdown"
        # came to pass on a box where nothing had been shut down.
        return {n for n in self.device_admin_down_raw()
                if "." not in n
                or n.split(".")[0] not in self.device_admin_down_raw()}

    def device_admin_down_raw(self) -> set:
        """
        Every interface the dataplane has administratively down, as it says.

        The reading `device_admin_down` filters, and it is kept apart because
        the apply needs the unfiltered one: a sub-interface left down because
        its parent is down is not a `shutdown` in the configuration, and it is
        still an interface this box has to bring up when the parent comes up
        (#407).
        """
        rc, out = self.dp.run("show interface")
        if rc != 0:
            return set()
        ifaces = routes.parse_interfaces(out)
        down = {name for name, info in ifaces.items()
                if info.get("state") == "down" and name != "local0"}
        return down

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
        distinction would mean prismash remembering things between invocations -
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
            if is_number(l3) and int(l3) > 0:
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

    def set_cgnat_feature(self, ifname, direction, is_add):
        """
        Attach or detach CGNAT on an interface, over the binary API.

        This used to be `vppctl set interface bpa in2out|out2in <if>`, so an
        account that could attach an interface needed the VPP CLI socket - and
        an account with that socket can run `bpa pool <name> del` and
        disconnect every subscriber behind it. The roles bound what the VTY
        would do, not what the account could reach (#76).

        The name-to-index translation used to happen inside vppctl. It happens
        here now, because the API message takes an index and the operator
        types a name.
        """
        idx = self.api.interface_index(ifname)
        if idx is None:
            # Distinct from "the dataplane refused it": the interface is not
            # there at all, and saying "refused" would send somebody looking at
            # the CGNAT configuration for a name that was mistyped.
            self.err(f"interface {ifname}: the dataplane does not have it")
            return False
        r = self.api_call("bpa_interface_add_del_feature",
                          {"is_add": bool(is_add),
                           "is_inside": direction == "inside",
                           "sw_if_index": idx})
        if r.state != vppapi.OK:
            verb = "attach CGNAT to" if is_add else "detach CGNAT from"
            self.err_dataplane(f"could not {verb} {ifname}: {r.reason}")
            return False
        return True

    # VPP's own enum, not a string this VTY invents. "off" is a real mode
    # rather than the absence of one, which is why removing uRPF is the same
    # call with a different value instead of a second code path.
    URPF_MODE = {
        "strict": "URPF_API_MODE_STRICT",
        "loose": "URPF_API_MODE_LOOSE",
        "off": "URPF_API_MODE_OFF",
    }

    @staticmethod
    def real_routing(lines):
        """
        The routing statements an operator actually wrote.

        Entering an interface block stages `interface <name>` as an FRR
        stanza, and the statements inside it that this VTY owns - mtu, urpf,
        addresses, vlan - never reach FRR at all. So a candidate that only
        configures an interface has a non-empty routing section containing
        nothing FRR needs to be told about.

        Without this distinction a CGNAT-only box could not commit an
        interface MTU: FRR is not running, the reload fails, and the whole
        commit is rolled back including the half that had nothing to do with
        routing (#137).
        """
        # A block's own `exit` is not a routing statement either: staged on
        # the way out of every block, it would make a candidate that only sets
        # an interface MTU look like routing and bring #137 straight back.
        return [l for l in lines
                if l.split()[0:1] != ["interface"] and not l.startswith(" ")
                and l.strip() not in FRR_BLOCK_CLOSERS]

    def set_admin(self, ifname, up):
        """
        Administrative state, on the dataplane interface AND its TAP.

        Setting only the VPP side leaves FRR holding adjacencies over a port
        that has stopped forwarding: the neighbour stays "up" until its own
        dead timer fires, and traffic is black-holed in the meantime. That is
        the same split this project has already shipped twice with the MTU, so
        it is done here rather than discovered later.
        """
        idx = self.api.interface_index(ifname)
        if idx is None:
            self.err(f"interface {ifname}: the dataplane does not have it")
            return False
        # Admin down is the ABSENCE of IF_STATUS_API_FLAG_ADMIN_UP, and the
        # natural way to say that - flags: 0 - makes vat2 SEGFAULT: the
        # generated parser for an `enum` field calls strcmp on whatever
        # cJSON_GetStringValue returned, which is NULL for a number. The empty
        # set has no name to send, so there is nothing to send (#136, and
        # vppapi.ENUM_FLAG_FIELDS, which now refuses the value rather than
        # letting the client die on it).
        #
        # So down is expressed by sending the one other flag in the type.
        # VPP reads only ADMIN_UP out of this message; LINK_UP is not settable
        # here and is ignored. The value therefore means exactly "admin down",
        # and it is written this way because the obvious encoding crashes the
        # client, not because it reads well.
        r = self.api_call("sw_interface_set_flags",
                          {"sw_if_index": idx,
                           "flags": ("IF_STATUS_API_FLAG_ADMIN_UP" if up
                                     else "IF_STATUS_API_FLAG_LINK_UP")})
        if r.state != vppapi.OK:
            self.err_dataplane(f"interface {ifname}: "
                               f"{'no shutdown' if up else 'shutdown'}: {r.reason}")
            # THE REASON VPP HAS AND THE REPLY DOES NOT. Its CLI says
            # "super-interface <name> must be up"; the binary API answers
            # VNET_API_ERROR_UNSPECIFIED and carries no string, so an operator
            # reads "unspecified error" about the one refusal here that has a
            # plain cause. The apply skips this case before it happens (#407);
            # this is what is said if it is ever reached another way.
            if up and "." in ifname:
                print_prose(f"a sub-interface cannot come up while its parent "
                            f"is down: check {ifname.split('.')[0]}")
            return False
        # The Linux side of the pair, which routes.linux_end() names: on the
        # same-name pair (#389) `name != ifname` kept the TAP and skipped
        # ens19, so the netdev FRR reads never moved. Handed the dataplane's
        # names because `interface tap0` is a block this shell takes, and
        # asked with the TAP and no list the rule answered the card.
        rc, lcp_out = self.dp.run("show lcp")
        if rc == 0:
            host = routes.linux_end(routes.lcp_pairs(lcp_out), ifname,
                                    self.dataplane_names())
            if host:
                subprocess.run(["ip", "link", "set", "dev", host,
                                "up" if up else "down"], capture_output=True)
        return True

    def set_mac(self, ifname, mac):
        """The MAC on the dataplane interface. The TAP keeps its own."""
        idx = self.api.interface_index(ifname)
        if idx is None:
            self.err(f"interface {ifname}: the dataplane does not have it")
            return False
        r = self.api_call("sw_interface_set_mac_address",
                          {"sw_if_index": idx, "mac_address": mac})
        if r.state != vppapi.OK:
            self.err_dataplane(f"interface {ifname}: could not set {mac}: {r.reason}")
            return False
        return True

    def set_ra_suppress(self, ifname, suppress):
        """
        Stop, or resume, advertising this box as a router on an interface.

        VPP sends RAs every 200 seconds on any interface holding an IPv6
        address - measured on a running dataplane, not assumed. On a
        subscriber-facing port that makes the box a default router for
        everybody behind it, which is a second gateway for subscribers who get
        their prefix by DHCPv6-PD.

        `is_no` undoes a previous configuration rather than meaning "not
        suppressed", so resuming is is_no on the suppression, not suppress=0.
        """
        idx = self.api.interface_index(ifname)
        if idx is None:
            self.err(f"interface {ifname}: the dataplane does not have it")
            return False
        r = self.api_call("sw_interface_ip6nd_ra_config",
                          {"sw_if_index": idx, "suppress": 1,
                           "is_no": not suppress, "managed": 0, "other": 0,
                           "ll_option": 0, "send_unicast": 0, "cease": 0,
                           "default_router": 0, "max_interval": 0,
                           "min_interval": 0, "lifetime": 0,
                           "initial_count": 0, "initial_interval": 0})
        if r.state != vppapi.OK:
            self.err_dataplane(f"interface {ifname}: router advertisements: {r.reason}")
            return False
        return True

    def set_vrf(self, ifname, table):
        """
        Put an interface in a routing table, creating the table if needed.

        Creating a table that already exists returns 0, so this does not have
        to ask first - and asking first would be two calls that can disagree
        between them.
        """
        idx = self.api.interface_index(ifname)
        if idx is None:
            self.err(f"interface {ifname}: the dataplane does not have it")
            return False
        if table:
            self.api_call("ip_table_add_del",
                          {"is_add": True,
                           "table": {"table_id": table, "is_ip6": False,
                                     "name": f"vrf{table}"}})
        r = self.api_call("sw_interface_set_table",
                          {"sw_if_index": idx, "is_ipv6": False,
                           "vrf_id": table})
        if r.state != vppapi.OK:
            self.err_dataplane(f"interface {ifname}: could not move it to vrf {table}: "
                               f"{r.reason}")
            return False
        return True

    #: IS-IS is not carried over IP. It is OSI NLPID 0x83 straight over the
    #: link, and linux_cp does not punt it unless told - the frame reaches
    #: VPP, lands in osi-input as "unknown osi protocol", and is dropped.
    OSI_ISIS = 0x83

    def osi_protos_enabled(self):
        """
        The OSI protocols linux_cp is punting, read from the box.

        Read rather than remembered, for the reason #161 cost: a state that
        can be set and never read is one the operator cannot turn off, and one
        nothing will admit is on. An empty list is a real answer - it is what
        a box that has never been told looks like - and is not a failure.
        """
        r = self.api_call("lcp_osi_proto_get", {})
        if r.state != vppapi.OK:
            return None                 # could not ask, which is not "none"
        # A HEX STRING, not a list: "0x83" for one protocol, bare "0x" for
        # none. Read as a list of integers it came back empty every time, so
        # this reported "nothing is punted" on a box that was punting - and
        # the only reason it did no harm is that enabling twice is a no-op.
        got = r.fields.get("osi_protos")
        if isinstance(got, str):
            body = got[2:] if got.startswith("0x") else got
            return [int(body[i:i + 2], 16) for i in range(0, len(body), 2)]
        return [int(x) for x in got] if isinstance(got, list) else []

    def enable_osi_isis(self):
        """
        Punt IS-IS's frames to the kernel, so FRR can see them.

        Applied by the product when the configuration contains an IS-IS
        instance, not left to the operator. Until now this line existed in a
        lab and in the documentation and NOWHERE in the product, so on a
        shipped box an operator configured IS-IS, the commit succeeded, isisd
        came up, the running configuration read correctly, and no adjacency
        ever formed - with the only evidence in a VPP error counter nothing
        surfaced (#168).

        Not undone when IS-IS is removed. Punting one OSI protocol costs a
        branch on frames that are not IP and would otherwise be dropped, and
        turning it off would mean deciding it is safe to stop punting while
        something else might still want it. The self-test reports what is on.
        """
        have = self.osi_protos_enabled()
        if have is None:
            return True                 # dataplane not answering: not our call
        if self.OSI_ISIS in have:
            return True
        r = self.api_call("lcp_osi_proto_enable", {"osi_proto": self.OSI_ISIS})
        if r.state != vppapi.OK:
            self.err("IS-IS is configured but its frames cannot be punted "
                     "to the kernel")
            print_prose(r.reason)
            print_prose("the adjacency would never form, and the only "
                        "evidence would be a dataplane error counter")
            # `err_dataplane` spelled out, because this refusal prints its
            # reason and its consequence on rows of their own and the upgrade
            # note belongs under both rather than between them.
            self.say_version_split()
            return False
        return True

    def create_loopback(self, ifname):
        """
        Create a loopback, at the instance number its name carries.

        `loop3` is instance 3, so the name an operator writes is the name the
        dataplane gives it and the configuration and `show interface` cannot
        disagree about what the thing is called. Without `is_specified` VPP
        picks the next free number, which would mean a configuration saying
        `loop3` could produce `loop0` on a box where 3 was taken.

        A loopback is what a router-id and an `update-source` are meant to
        live on: an address on a physical port disappears with the port, which
        is the failure a loopback exists to prevent. Until now this VTY could
        REFUSE `update-source Loopback0` for not existing and offered no way to
        make one (#179).
        """
        instance = int(ifname[4:])
        r = self.api_call("create_loopback_instance",
                          {"mac_address": "00:00:00:00:00:00",
                           "is_specified": True, "user_instance": instance})
        if r.state != vppapi.OK:
            self.err_dataplane(f"could not create {ifname}: {r.reason}")
            return False
        return True

    def delete_loopback(self, ifname):
        """Remove a loopback. Already gone is the outcome asked for."""
        idx = self.api.interface_index(ifname)
        if idx is None:
            return True
        r = self.api_call("delete_loopback", {"sw_if_index": idx})
        if r.state != vppapi.OK:
            self.err_dataplane(f"could not delete {ifname}: {r.reason}")
            return False
        return True

    def set_lcp_pair(self, ifname, host_name, is_add=True):
        """
        Pair an interface into the kernel, so FRR can see it.

        Nothing in this product created a pair until now. The dataplane owned
        the NIC, FRR saw no interface for it, and every routing protocol was
        configurable and inert on an installed box - while the labs created
        the pair by hand, so they proved a box nobody received (#182).

        TAP rather than TUN: the routing protocols this product carries are
        not all IP. IS-IS is OSI straight over the link, and a TUN pair has no
        link layer to carry it.
        """
        idx = self.api.interface_index(ifname)
        if idx is None:
            self.err(f"interface {ifname}: the dataplane does not have it")
            return False
        r = self.api_call("lcp_itf_pair_add_del",
                          {"is_add": is_add, "sw_if_index": idx,
                           "host_if_name": host_name,
                           "host_if_type": "LCP_API_ITF_HOST_TAP",
                           "netns": ""})
        if r.state != vppapi.OK:
            verb = "pair" if is_add else "unpair"
            self.err_dataplane(f"could not {verb} {ifname} with {host_name}: {r.reason}")
            return False
        return True

    def dataplane_if_with_mac(self, mac):
        """
        The dataplane's name for the card with this MAC, or None.

        The card is named by Linux before the handover and by the dataplane
        after it, and nothing carries one name to the other: `ens18` becomes
        whatever DPDK calls the device it found at that PCI address. The MAC
        does carry - DPDK takes the device and keeps its hardware address,
        which is also why nothing upstream has to re-ARP - so it is what the
        two sides are matched on (#303).
        """
        rc, out = self.dp.run("show hardware-interfaces")
        if rc != 0:
            return None
        current = None
        want = (mac or "").lower()
        for line in (out or "").splitlines():
            if line and not line[0].isspace():
                w = line.split()
                current = w[0] if w and w[0] != "Name" else None
                continue
            w = line.split()
            if current and w[:2] == ["Ethernet", "address"] and len(w) > 2:
                if w[2].lower() == want:
                    return current
        return None

    def linux_still_has_card(self, mac):
        """
        True while LINUX still holds the card with this MAC.

        The question that tells apply_management_path() which boot it is on. A
        card DPDK has taken is not under /sys/class/net at all - the device is
        off the kernel - so its absence there is the signal that the handover
        has happened, and `show hardware` already leans on the same reading in
        the other direction.

        Matched on the MAC and not on the name, for the reason the record
        carries a MAC at all: the Linux name is the one thing that does not
        survive the handover (#303).

        CANNOT LOOK IS NOT "IT IS GONE". On anything that is not Linux - a
        developer's tree, this VTY's own suites - the directory is absent, and
        reading that as a handover would turn every apply on such a box into
        the refusal apply_management_path() prints. A directory that IS there
        and holds no card is a different answer and is not covered by this: a
        box whose only card has moved lists `lo` and nothing else, which is
        exactly the state being reported.
        """
        try:
            names = os.listdir(self.SYS_CLASS_NET)
        except OSError:
            return True
        want = (mac or "").lower()
        for name in names:
            have = self._sysfs(os.path.join(self.SYS_CLASS_NET, name,
                                            "address"))
            if have and have.lower() == want:
                return True
        return False

    def apply_management_path(self, mgmt):
        """
        Put the management address back, on the pair of the card that moved.

        Runs at every boot, from load(), with the ONE record there is: the
        way back prisma-routing-tune computed off the live card on this boot
        (#378), which is a name, an address, a MAC and a gateway. It used to
        run on every commit as well, with a three-field record the VTY had
        written at the commit that named the card; that record and its
        precedence over the computed one went with #385, because the
        computed one is never older than the boot and the operator's could
        be. On most boots there is no record and nothing to do; on the boot
        after the card moved, this is the only thing standing between a box
        that forwards and a box nobody can reach.

        NOT AN ERROR WHEN THE CARD HAS NOT MOVED YET. `prismash --load` runs on
        every boot, including the ones before the operator reboots into the
        handover and the ones after it has been rolled back, and a commit that
        failed because a card had not moved yet would be a box that cannot be
        configured at all.

        BUT "NOT MOVED YET" AND "GONE" ARE NOT THE SAME BOOT, and returning
        success for both is what let a box come back from its own handover
        with no address on any interface and say nothing about it (#332). The
        box can tell them apart, from the card Linux either still has or does
        not, so it is asked rather than assumed.
        """
        ifname = self.dataplane_if_with_mac(mgmt["mac"])
        if not ifname:
            if self.linux_still_has_card(mgmt["mac"]):
                # The ordinary boot this early return was written for: the
                # card is still Linux's, nothing has been handed over, and
                # there is nothing to do and nothing worth saying.
                return True
            # The other boot. The tuner unbound the card and the dataplane did
            # not take it, so the card is on neither side and the address has
            # nowhere to go. This box is off the network and the console is
            # the only place left that can be told.
            #
            # The verdict on the headline and the rest below it: `err` wraps
            # nothing, and with the card and its MAC on that row this measured
            # 98 columns (#276).
            self.err("the card this box is reached on was handed over and "
                     "nothing took it")
            print_prose(f"Linux no longer has {mgmt['name']} and no interface "
                        f"the dataplane holds has {mgmt['mac']}, so there is "
                        f"no pair for {mgmt['address']} to go on")
            print_prose("this box has no address and cannot be reached over "
                        "the network until that is repaired from here")
            print_prose("'show hardware' says what the dataplane holds; 'no "
                        "dataplane interface' gives the card back at the next "
                        "start")
            # The path on a row of its own: it is what the operator goes and
            # reads, and a path that wraps is one they cannot copy (#276).
            print_prose("the card is named in this file:")
            print(f"    {self.TUNE_ENV}")
            return False
        if not self.set_admin(ifname, True):
            return False
        # Idempotent on purpose: the same file is replayed at every boot, and
        # on the second one the pair and the address are already there. The
        # dataplane answers "exists" to both, which is not a failure to
        # report to an operator who changed something else entirely.
        if self.device_linux_names().get(ifname) != mgmt["name"]:
            if not self.set_lcp_pair(ifname, mgmt["name"], True):
                return False
        rc, out = self.dp.run(f"show interface addr {ifname}")
        if rc != 0 or mgmt["address"] not in (out or ""):
            if not self.set_address(ifname, mgmt["address"], True):
                return False
        # AND THE WAY OFF ITS OWN SUBNET, which the address alone does not
        # give. A box with the address back and no default route answers
        # everything on its own wire and nothing beyond it - which on the
        # lab's guest is indistinguishable from a healthy box, because
        # qemu's gateway is on that wire. On a real box it is a machine
        # somebody drives to. The record always carries the gateway: a /run
        # file without one is read as no record at all.
        #
        # IN LINUX AND NOT IN FRR, deliberately. FRR is where an operator's
        # routing lives and converging it here would mean handing frr-reload
        # a line set this function does not own. What is being put back is
        # the box's own way home, which ifupdown and dhclient had in Linux
        # before the card moved, so Linux is where it goes back.
        #
        # `replace` and not `add`: this runs on every boot and the second one
        # already has the route. An error there would be a box reporting a
        # failure for having succeeded twice.
        gw = mgmt["gateway"]
        rc, out = self.sys._run(["ip", "route", "replace", "default",
                                 "via", gw, "dev", mgmt["name"]])
        if rc != 0:
            # SAID AND NOT FATAL. The address is already back, so the box
            # is reachable from its own subnet and an operator on it can
            # fix this; failing here would abandon a handover that mostly
            # worked and leave the card in the dataplane anyway.
            self.err(f"the default route could not be put back: {out}")
            print_prose(f"this box has {mgmt['address']} on "
                        f"{mgmt['name']} and no way off that subnet; "
                        f"the gateway it recorded is {gw}")
        return True

    def device_linux_names(self) -> dict:
        """
        The Linux-side name of each interface that has a pair, from the box.

        Read back rather than remembered, which is the lesson #161 cost: a
        state that can be set and never read is one the operator cannot turn
        off, and one the running configuration will not admit is on.
        """
        rc, out = self.dp.run("show lcp")
        if rc != 0:
            return {}
        found = {}
        for line in out.splitlines():
            # itf-pair: [0] host-veth-vpp tap4096 vppwan0 5 type tap
            w = line.split()
            if len(w) >= 6 and w[0] == "itf-pair:":
                found[w[2]] = w[4]
        return found

    def dataplane_cards(self) -> dict:
        """
        {name: (domain, bus, slot, function)} for every interface the
        dataplane holds as a PCI card, read off `show hardware-interfaces`.

        A card is the interface that carries its Linux name (#389): the
        tuner names every device it claims after the netdev Linux had, so
        `show interface` says ens19 and so does `ip link`, once the pair
        below is made. A pg, a tap, a host interface and a loopback are not
        cards and nothing here applies to them. Empty when the dataplane
        cannot be asked, and then nothing is defaulted and nothing renamed.
        """
        rc, out = self.dp.run("show hardware-interfaces")
        if rc != 0:
            return {}
        return routes.parse_hardware_pci(out)

    def pair_cards(self, target):
        """
        The default a claimed card's Linux end gets when nobody chose one
        (#389): a card with an `interface` block is paired into Linux under
        its own name, so FRR and `ip` see ens19 where the dataplane does.

        WRITTEN INTO THE CONFIGURATION rather than implied by it. Before
        this, `d["lcp_set"]` carried nothing for an interface with no
        `linux-name` - measured - and the pair simply did not exist; the
        running configuration is read back from `show lcp` (#161), so a pair
        that existed only in a rule would be a `linux-name` in the running
        text and not in the candidate, and every commit would remove it. The
        default lands in `linux_names` here, at apply, on every path that
        reaches the box - `commit`, `rollback`, `--load` - and from then on
        it is an ordinary statement: `show configuration` prints it, `save`
        writes it and `linux-name <other>` overrides it.

        AND FOR LOOPBACKS, which is #404 and reverses half of the paragraph
        below. A loopback exists to carry the address a router is KNOWN by -
        the BGP router-id, the iBGP endpoint, the address that stays up when a
        port does not - and every one of those is FRR's job. FRR sees only
        what Linux has. So the one interface whose whole purpose is to be
        reachable by the routing daemons was the one they could not see:
        measured on the maintainer's box, `interface loop0 / ipv4 address
        10.0.0.9/32` committed cleanly, the dataplane had the address, `show
        interface` showed it, and `vtysh` called loop0 a *pseudo interface* -
        no index, no flags, nothing to send a packet out of.

        Its name is free in Linux for a different reason than a card's, and
        that is why the paragraph below did not cover it: a card's name is
        free because the card LEFT Linux, while `loop0` is a name VPP invents
        for an interface Linux never had. Linux's own loopback is `lo`. Either
        way the name is there to be taken.

        FOR CARDS AND LOOPBACKS WITH A BLOCK, and not every interface whose
        name Linux could take. A card's name is free in Linux precisely
        because the card left Linux for the dataplane; a pg or a host
        interface has no such name to put back. `no interface <card>` is refused -
        a physical port is not something a configuration removes - and `no
        linux-name` on a card takes an OVERRIDE back: the default returns
        here at the next apply, and the prompt says so as it is typed. A
        card VPP named for itself - a device no tuner named,
        GigabitEthernet0/13/0 - is not a name Linux can take and gets no
        default, which the commit check says out loud.

        A block is one the file's device section holds OR one the routing
        section opens: `interface ens19 / no shutdown` - the maintainer's
        whole transcript - leaves every device dictionary empty and stages
        the FRR stanza, and the operator who typed it is exactly the one
        expecting FRR to see the port.
        """
        blocks = target.interface_names() | set(target.frr_interface_names())
        # The loopbacks come from the TARGET and not from the dataplane, which
        # is the one difference from a card: naming a loopback is what creates
        # it, so at this point in the apply it does not exist yet and a read
        # of the box would default nothing on the commit that made it.
        defaulted = self.defaulted_cards() | {
            n for n in target.loopbacks if not linux_ifname_error(n)}
        for card in defaulted:
            if card in blocks and card not in target.linux_names:
                target.linux_names[card] = card

    def defaulted_cards(self) -> set:
        """
        The cards whose Linux end defaults to their own name: every card
        the dataplane holds whose name Linux can take. One owner, because
        the prompt asks the same question of `no linux-name` (#389) and two
        copies of the condition would be two answers.
        """
        return {card for card in self.dataplane_cards()
                if not linux_ifname_error(card)}

    def card_renames(self, text) -> dict:
        """
        {a name a saved file uses: the name this box holds the same card
        under}, for a configuration saved before the card carried its Linux
        name (#389).

        The upgraded box. A file from 0.76.1 says `interface
        GigabitEthernet0/13/0`; after the upgrade the tuner names the device
        after its card and the dataplane holds ens19 at that same address.
        The old name encodes the address - `0/13/0` is bus 0, slot 0x13,
        function 0 - and `show hardware-interfaces` prints the address of
        every card the box holds now, so the two are matched with nothing
        guessed: the box knows both names, and this is the one place it
        still needs to. Every DPDK-shaped word in the file is a candidate,
        wherever it stands - an `interface` block, a CGNAT attachment, a
        `passive-interface` inside `router ospf` - because the rename is
        made on the text and a name left behind in one statement is a
        statement about a card the box has not got.

        A name the box still has is not renamed. A card at an address two
        of the box's cards share - the same bus, slot and function in two
        PCI domains - is not renamed either: a wrong card is worse than a
        refused file, and the refusal that follows names what the box has.
        """
        held = self.dataplane_cards()
        if not held:
            return {}
        by_addr = {}
        for name, (_domain, bus, slot, func) in held.items():
            by_addr.setdefault((bus, slot, func), []).append(name)
        out = {}
        # A greedy letter run is right HERE and wrong in the parser: this
        # one returns the whole span, and routes.dpdk_name_address() is
        # what splits the class word from a bus that starts with a letter.
        for word in sorted(set(re.findall(
                r"[A-Za-z]+[0-9a-fA-F]+/[0-9a-fA-F]+/[0-9a-fA-F]+", text))):
            if word in held or self.api.interface_index(word) is not None:
                continue
            key = routes.dpdk_name_address(word)
            if key and len(by_addr.get(key, [])) == 1:
                out[word] = by_addr[key][0]
        return out

    @staticmethod
    def renamed_text(text, renames) -> str:
        """
        `text` with every whole-word occurrence of each old name replaced.

        A sub-interface keeps its tag: `GigabitEthernet0/13/0.100` becomes
        `ens19.100`, which is why a following '.' does not end the word
        while a following '/' or letter does - `TenGigabitEthernet0/13/0`
        must not lose its `GigabitEthernet0/13/0` to a rename of the latter.
        """
        for old, new in renames.items():
            text = re.sub(r"(?<![A-Za-z0-9_./-])" + re.escape(old)
                          + r"(?![A-Za-z0-9_/-])", new, text)
        return text

    def device_loopbacks(self) -> set:
        """The loopbacks the dataplane has, by their own names."""
        rc, out = self.dp.run("show interface")
        if rc != 0:
            return set()
        return {n for n in routes.parse_interfaces(out) if _is_loopback(n)}

    def create_subif(self, ifname, tag):
        """
        Create a sub-interface on its parent.

        Single-tag goes through create_vlan_subif, which needs no flags.
        QinQ goes through create_subif, whose sub_if_flags is a bitfield -
        and vat2 can only send ONE name of a bitfield, so TWO_TAGS is sent
        without EXACT_MATCH (#136, and vppapi.ENUM_FLAG_FIELDS for why the
        client cannot say the other spelling).

        The operator is TOLD what that produces, every time it is produced,
        rather than left to read an issue. Measured on vpp 26.06-release with
        the packet generator, against pg0.40 created by the VPP CLI with
        `exact-match` set and pg0.10 created by this call without it: a frame
        carrying three tags lands on BOTH of them. So the missing flag is not
        what makes a QinQ sub-interface over-match here - vnet/ethernet/node.c
        classifies "3 or more tags" as two tags in eth_input_tag_lookup(), the
        path a sub-interface on a real port takes, and never asks for
        SUBINT_CONFIG_MATCH_3_TAG at all. Sending EXACT_MATCH would change
        nothing an operator can observe, which is why this warns instead of
        refusing: the sub-interface does carry the traffic it was asked for.

        The same measurement on a SINGLE-tag sub-interface does separate the
        two - `dot1q 60 exact-match` drops a two-tag frame that `dot1q 50`
        accepts - so exact matching works in that path and this VTY does not
        need it there: create_vlan_subif takes no flags and VPP defaults a
        single-tag sub-interface to exact matching.
        """
        parent = ifname.rsplit(".", 1)[0]
        idx = self.api.interface_index(parent)
        if idx is None:
            self.err(f"interface {ifname}: its parent {parent} is not on "
                     "this box")
            return False
        if tag["inner"]:
            r = self.api_call("create_subif",
                              {"sw_if_index": idx, "sub_id": tag["outer"],
                               "sub_if_flags": "SUB_IF_API_FLAG_TWO_TAGS",
                               "outer_vlan_id": tag["outer"],
                               "inner_vlan_id": tag["inner"]})
            if r.state == vppapi.OK:
                # The widest line `commit` printed anywhere: 231 columns on
                # one row (#271). What it matches is the headline; why the
                # box cannot help it is the prose under it.
                print(f"warning: {ifname} also matches anything carrying "
                      "more tags")
                print_prose(f"it is outer {tag['outer']} inner "
                            f"{tag['inner']}, and VPP's ethernet input reads "
                            "three or more tags as two, so a QinQ "
                            "sub-interface on this box cannot be made to "
                            "refuse them (#136)")
        else:
            r = self.api_call("create_vlan_subif",
                              {"sw_if_index": idx, "vlan_id": tag["outer"]})
        if r.state != vppapi.OK:
            self.err_dataplane(f"could not create {ifname}: {r.reason}")
            return False
        return True

    def delete_subif(self, ifname):
        """Remove a sub-interface. Its parent is left alone."""
        idx = self.api.interface_index(ifname)
        if idx is None:
            # Already gone. Not an error: the outcome asked for is the
            # outcome there is.
            return True
        r = self.api_call("delete_subif", {"sw_if_index": idx})
        if r.state != vppapi.OK:
            self.err_dataplane(f"could not delete {ifname}: {r.reason}")
            return False
        return True

    # Interfaces the candidate says should advertise. Empty until apply()
    # fills it, which is the safe direction: an unknown interface gets the
    # default, and the default is "do not advertise".
    _ra_enabled_now: set = set()

    def set_address(self, ifname, prefix, is_add):
        """
        One address on one interface, over the binary API.

        The message carries a single prefix field for both families and infers
        the family from the value, which is why the VTY checks that `ipv6
        address 10.0.0.1/24` is a typo before it gets here: the dataplane would
        take it.
        """
        idx = self.api.interface_index(ifname)
        if idx is None:
            self.err(f"interface {ifname}: the dataplane does not have it")
            return False
        r = self.api_call("sw_interface_add_del_address",
                          {"sw_if_index": idx, "is_add": bool(is_add),
                           "del_all": False, "prefix": prefix})
        if r.state != vppapi.OK:
            verb = "add" if is_add else "remove"
            self.err_dataplane(f"interface {ifname}: could not {verb} {prefix}: "
                               f"{r.reason}")
            return False
        # An interface advertises only once it holds an IPv6 address, so this
        # is the moment the default becomes possible - and the moment to apply
        # it. VPP's default is to advertise every 200 seconds; on a
        # subscriber-facing port that is a second default gateway for everybody
        # using DHCPv6-PD, so this product's default is the opposite.
        #
        # Applied here rather than compared in the delta because the state
        # cannot be read back: `show ip6 interface` reports the same text
        # whether advertisements are suppressed or not, measured. Where a value
        # cannot be read, the honest thing is to set it at the moment it starts
        # to matter rather than to pretend a comparison happened.
        if is_add and ":" in prefix and ifname not in self._ra_enabled_now:
            self.set_ra_suppress(ifname, True)
        return True

    def set_description(self, ifname, text):
        """
        The description, kept as the dataplane's interface tag so that it can
        be read back from the box, and mirrored to the Linux side as the
        interface alias so that `ip -d link` and FRR agree with the VTY.

        An empty string removes it.
        """
        idx = self.api.interface_index(ifname)
        if idx is None:
            self.err(f"interface {ifname}: the dataplane does not have it")
            return False
        r = self.api_call("sw_interface_tag_add_del",
                          {"is_add": bool(text), "sw_if_index": idx,
                           "tag": text or ""})
        if r.state != vppapi.OK:
            self.err_dataplane(f"interface {ifname}: description: {r.reason}")
            return False
        rc, lcp_out = self.dp.run("show lcp")
        if rc == 0:
            # routes.linux_end(): the same-name pair (#389) has no member
            # that is `!= ifname` and is not the TAP. With the dataplane's
            # names, because keyed by the TAP the rule needs them.
            host = routes.linux_end(routes.lcp_pairs(lcp_out), ifname,
                                    self.dataplane_names())
            if host:
                subprocess.run(["ip", "link", "set", "dev", host,
                                "alias", text or ""], capture_output=True)
        return True

    def set_urpf(self, ifname, mode):
        """
        uRPF on an interface, over the binary API rather than `vppctl set urpf`
        (#76).

        Input direction only: this checks the source of packets ARRIVING on the
        interface, which is what "the source must route back this way" means.
        Applying it on output would drop nothing an operator asked to drop.
        """
        idx = self.api.interface_index(ifname)
        if idx is None:
            self.err(f"interface {ifname}: the dataplane does not have it")
            return False
        r = self.api_call("urpf_update",
                          {"is_input": True, "sw_if_index": idx,
                           "mode": self.URPF_MODE[mode], "af": "ADDRESS_IP4"})
        if r.state != vppapi.OK:
            self.err_dataplane(f"interface {ifname}: urpf {mode}: {r.reason}")
            return False
        return True

    def apply(self, target: Config, check_only=False) -> bool:
        """
        Converge the device onto `target`. Returns True on success.

        CGNAT is applied first and routing second, deliberately: a routing
        change can cost reachability, and doing it last keeps the window in
        which the box is half-configured as short as possible.
        """
        running = self.running_config()
        # The default a claimed card's pair gets, written into the target
        # before the delta is taken so that the delta carries it (#389).
        self.pair_cards(target)
        d = running.delta_to(target)
        self.remap = []
        self.cutoff = []
        self.diverge = self.peer_divergence(target)

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
            # The same refusals `do_config` gives at the prompt, on the paths
            # that never see the prompt: `rollback` to a revision written when
            # the box had a different card in it, and `--load` of a file
            # prisma-routing.service replays at every boot. Only when THIS change
            # asks for it - a box already set to a card the tuner will refuse
            # must still be able to commit the change that repairs something
            # else, which is the same posture as the lockout check below.
            if d["dataplane_if_changed"] and target.dataplane_if:
                why = self.dataplane_if_refusal(target.dataplane_if)
                if why:
                    self.say_dataplane_if_refusal(target.dataplane_if, why)
                    ok = False
            # Refused only when THIS CHANGE is what causes it. A box that
            # already has nobody able to log in over SSH - freshly installed,
            # reached by console - must still be able to commit a CGNAT
            # change; refusing there would be a VTY that cannot be used on the
            # box it was just installed on.
            if (not can_anyone_log_in(target.ssh, target.users)
                    and can_anyone_log_in(running.ssh, running.users)):
                # The verdict in the headline and the rest below it, because
                # `err` prints one line and wraps nothing (#252): with the
                # consequence appended this measured 83 columns (#271).
                self.err("this change locks everybody out")
                print_prose("no account could log in over SSH afterwards")
                print_prose("give an account an authorized key, or allow "
                            "password authentication for one that has a "
                            "password")
                ok = False
            # The same mistake through the other door. Removing the last
            # administrator's role leaves a box that answers and forwards and
            # that nobody can configure again: a cgnat operator cannot grant a
            # role, so the way back is a root shell the operator may not have.
            # Guarded the same way - refused only when THIS CHANGE is what
            # causes it - so a box that already has no reachable administrator
            # can still be worked on.
            if (not can_anyone_configure(target.ssh, target.users)
                    and can_anyone_configure(running.ssh, running.users)):
                # Split for the same reason as the headline above: this one
                # measured 135 columns on one `err` line (#271).
                self.err("this change leaves nobody able to configure the box")
                print_prose("afterwards no account that can log in over SSH "
                            f"holds the {ROLE_ADMIN} role")
                print_prose(f"give another account 'role {ROLE_ADMIN}' "
                            "first, or allow root to log in over SSH")
                ok = False
            # UDP export turned on with nowhere to send it. Refused HERE
            # and not by the dataplane, which is where it used to be refused:
            # by then the rest of the commit is applied, and until #76 the
            # VTY discarded the refusal and printed "commit complete" over a
            # box that exported nothing (#209).
            #
            # Refused only when THIS CHANGE is what causes it, the same guard
            # the lockout checks above use. A box already in that state -
            # export enabled and the exporter cleared underneath it - must
            # still be able to commit the statement that fixes it.
            if udp_export_without_collector(target.globals):
                # The consequence moved off the headline and into the prose:
                # as one line this measured 89 columns as an error and 91 as
                # a warning, and `err` wraps nothing (#271).
                said = "cgnat logging enable: UDP export has no collector"
                advice = ("nothing would leave this box; add 'logging udp "
                          "<collector> src <source>' beside it under 'nat' / "
                          "'cgnat', or export over TCP with 'logging tcp "
                          "<collector>'")
                if udp_export_without_collector(running.globals):
                    print(f"warning: {said}")
                    print_prose(advice)
                else:
                    self.err(said)
                    print_prose(advice)
                    print_prose("the dataplane refuses the statement, and it "
                                "would refuse it half way through this "
                                "commit")
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
            known_interfaces = set()
            # Both carried down to ospf_findings, which cannot work from the
            # flattened set: it needs to know which names are one interface,
            # and which member of a pair is the dataplane's (#205). The
            # flattened set cannot say either, and building it destroys the
            # answer to the second.
            pairs = {}
            dp_interfaces = set()
            # The names the existence check refused, so the FRR-limit check
            # below does not refuse them a second time with advice for a
            # box that has already renamed the card (#389): a `rollback` to
            # a pre-upgrade revision names GigabitEthernet0/13/0 on a box
            # holding ens19, and one verdict says what the box has.
            missing = set()
            rc_i, iface_out = self.dp.run("show interface")
            if rc_i == 0 and iface_out.strip():
                # BOTH names. A linux_cp pair has a VPP name and a Linux one,
                # and both are real: FRR configures by the Linux name, the
                # dataplane by its own. Checking only VPP's would refuse every
                # FRR interface stanza on every box that has linux_cp - which
                # is every box that routes - and lab/routing.sh caught exactly
                # that, on "interface vppwan0".
                _, lcp_out = self.dp.run("show lcp")
                pairs = routes.lcp_pairs(lcp_out)
                dp_interfaces = set(routes.parse_interfaces(iface_out))
                have = known_interfaces = set(dp_interfaces)
                # Every name every pair answers to. All of them are real.
                for names in pairs.values():
                    have.update(names)
                # Every place an interface name can be staged, which is four
                # and not one: the CGNAT feature, an FRR block, and the mtu and
                # urpf dictionaries the VTY models itself. A check that looked
                # at only the first two passed a file whose interface name
                # lives in mtus - which is every file that sets an MTU, and is
                # how this was found.
                # A sub-interface the configuration CREATES is not one the
                # box is missing. Without this the check refuses every new
                # sub-interface - the commit that would have brought it into
                # existence - which is a check that makes the feature it
                # guards impossible to use.
                have |= set(target.vlans)
                # A loopback the configuration CREATES is not one the box is
                # missing. Without this the check refuses every new loopback -
                # the commit that would have brought it into existence - which
                # is the same trap the sub-interface line above exists for.
                have |= set(target.loopbacks)
                # And the LINUX side of a linux_cp pair the configuration
                # creates, which is the third name in the same sentence and was
                # the one still missing. `pairs` above is read from `show lcp`,
                # so at boot it is empty and this name is in the file and on no
                # box - while FRR configures by that name and no other, so
                # `interface vppwan0 / ip ospf area 0` is what a saved file
                # holds. Measured: `--load` refused the whole configuration
                # with "interface vppwan0: this box has no such interface"
                # (#294, the same mid-construction reading as the next-hop
                # check below). Not hidden by FRR's own copy of the
                # configuration the way that one was, because this check is not
                # gated on `routing_changed`.
                have |= set(target.linux_names.values())
                named = set(target.interfaces)
                named |= set(target.mtus)
                named |= set(target.urpf)
                named |= set(target.addresses)
                named |= set(target.descriptions)
                named |= set(target.admin_down)
                # And the interface a virtual router sits on, which was the
                # one name in this list that `commit check` did not read.
                named |= set(target.vrrp)
                # And a sub-interface whose PARENT is not on the box is a
                # different error, worth its own sentence: the tag is fine and
                # the port it was meant to sit on is not there.
                for sub in sorted(target.vlans):
                    parent = sub.rsplit(".", 1)[0]
                    if parent not in have:
                        self.err(f"interface {sub}: its parent {parent} is "
                                 "not on this box")
                        ok = False
                # WHAT A VIRTUAL ROUTER NEEDS BEFORE IT CAN BE STARTED, read
                # here rather than only at the moment of applying it.
                #
                # `set_vrrp` refuses both of these, and until now that was the
                # only place they were refused - so `commit check` said
                # "configuration check succeeds" and the commit then failed
                # part-way through, on a box the earlier steps had already
                # changed. A check that passes a configuration the commit
                # cannot apply is worth less than no check: it is the sentence
                # an operator acts on in a maintenance window.
                for vr_if in sorted(target.vrrp):
                    for vrid in sorted(target.vrrp[vr_if]):
                        vr = target.vrrp[vr_if][vrid]
                        if not vr["addresses"]:
                            self.err_prose(
                                f"interface {vr_if}: vrrp {vrid} has no "
                                "address",
                                "a virtual router with nothing to stand for "
                                "would elect a master that answers for "
                                "nothing")
                            ok = False
                        for tracked in sorted(vr["track"]):
                            if tracked not in have:
                                self.err_prose(
                                    f"interface {vr_if}: vrrp {vrid}: no "
                                    f"interface {tracked}",
                                    "the dataplane does not have the "
                                    "interface this virtual router was told "
                                    "to track")
                                ok = False
                for line in target.routing:
                    w = line.split()
                    if len(w) == 2 and w[0] == "interface":
                        named.add(w[1])
                # `neighbor X update-source Loopback0` on a box with no
                # Loopback0. FRR accepts it, the session sources from nothing
                # and never comes up, and the configuration reads correctly.
                # Reported separately from the stanza check above because the
                # useful sentence names the NEIGHBOUR - "this box has no such
                # interface" leaves the operator looking for an interface
                # stanza that is not the problem.
                #
                # An address is a legitimate update-source too, and is not an
                # interface name, so only names that are not addresses are
                # checked.
                for line in target.routing:
                    w = line.split()
                    if (len(w) >= 4 and w[0] == "neighbor"
                            and w[2] == "update-source"):
                        src = w[3]
                        try:
                            ipaddress.ip_address(src)
                            continue
                        except ValueError:
                            pass
                        if src not in have:
                            # The statement goes on its own row, whole. On
                            # the headline beside the prose it measured 85
                            # columns, and it is the row the operator greps
                            # `show configuration` for (#252, #271).
                            self.err(f"this box has no interface {src}")
                            print("    " + line.strip())
                            print_prose("the session would be sourced from "
                                        "nothing and never come up")
                            ok = False
                # The cards Linux still holds, so a name that is one of
                # them is refused for the reason it is actually refused for
                # (#405). Read once, outside the loop: the answer is the same
                # for every name and the read walks /sys.
                on_linux = {n["name"] for n in self.host_nics()}
                for name in sorted(named - have):
                    missing.add(name)
                    if name in on_linux:
                        # A CARD THE OPERATOR IS LOOKING AT. It is in `show
                        # hardware`, it is in `ip link`, and "this box has no
                        # such interface" about it reads as the box being
                        # wrong - which sends somebody to check their spelling
                        # instead of to the one thing that fixes it.
                        self.err_prose(
                            f"interface {name}: the dataplane does not hold "
                            "this card",
                            "the box has it and Linux still does; the "
                            "dataplane claims a card when it starts, and the "
                            "cards it claims are computed on that boot",
                            "'show hardware' lists it, and 'system reboot' "
                            "is what hands it over")
                        ok = False
                        continue
                    self.err(f"interface {name}: this box has no such "
                             "interface")
                    # Wrapped, not one row: the list grows with the box, and
                    # a name is never split because `console_prose` does not
                    # break a word - so each one stays greppable (#271).
                    #
                    # What is OFFERED is what an operator configures, while
                    # what is ACCEPTED above is every name the box answers to.
                    # A refusal that offered `local0` and two TAPs sent them
                    # to type one of three names that would be refused next,
                    # or taken and wrong (#403).
                    print_prose("it has: "
                                + ", ".join(sorted(
                                    routes.operator_interfaces(have))))
                    print_prose("(a linux_cp pair answers to both its "
                                "dataplane name and its Linux one)")
                    print_prose("the statement would commit and do nothing")
                    ok = False
            # With the dataplane unreachable the check is SKIPPED, not failed.
            # A VTY that cannot commit while the dataplane is down cannot be
            # used to fix it.

            # An interface name FRR cannot take (#389). Every `interface
            # <name>` typed at this VTY is staged as an FRR stanza whether or
            # not anything FRR needs follows it, and FRR 10.7.1 refuses a
            # stanza whose name is over FRR_IFNAME_MAX characters - which is
            # every name VPP gives a card nobody named. On the maintainer's
            # box `interface GigabitEthernet0/13/0 / no shutdown / commit`
            # got "Failure to communicate[13] to ospf6d", "routing reload
            # failed" and a rollback, from a commit with one statement in
            # it. Said here, before anything is applied, with the limit and
            # the way out. Gated the way the daemon checks below are: on the
            # routing half changing, which is when FRR is handed the stanza.
            if d["routing_changed"]:
                for ifname in target.frr_interface_names():
                    if len(ifname) <= FRR_IFNAME_MAX or ifname in missing:
                        continue
                    self.err(f"interface {ifname}: a name FRR cannot take")
                    print_prose(f"FRR takes an interface name of at most "
                                f"{FRR_IFNAME_MAX} characters and this one "
                                f"is {len(ifname)}; the routing daemons "
                                "refuse the block, the reload fails and the "
                                "whole commit rolls back")
                    print_prose("a card this box claims is named after the "
                                "card's own Linux name from the next start "
                                "of the dataplane; if 'show interface' "
                                "still lists this name after one, the "
                                "device is unnamed in /etc/vpp/startup.conf "
                                "and wants 'name <linux name>' inside its "
                                "dev block")
                    ok = False

            # A "router <protocol>" statement for a daemon that is not
            # running commits cleanly and reaches nothing. The box ships with
            # bgpd, ospfd and isisd disabled unless the installer enabled them
            # (#99), and vtysh answers "bgpd is not running" while frr-reload
            # still returns 0 - so the commit reports success for configuration
            # that went nowhere.
            if d["routing_changed"]:
                running_daemons = self.frr.daemons()
                if running_daemons is not None:
                    for line in target.routing:
                        w = line.split()
                        if len(w) < 2 or w[0] != "router":
                            continue
                        need = ROUTER_DAEMON.get(w[1])
                        if need and need not in running_daemons:
                            self.err(f"{line.strip()}: {need} is not running")
                            print_prose("the statement would commit and "
                                        "reach nothing. "
                                        + enable_daemon_advice(need))
                            ok = False

                    # The statements that have no `router` line at all, and so
                    # were never checked. Reported once per missing daemon
                    # rather than once per statement: a policy is many lines
                    # and the operator has one thing to fix.
                    policy = [l.strip() for l in target.routing
                              if l.strip().startswith(POLICY_STATEMENT)]
                    for need in POLICY_DAEMONS if policy else ():
                        if need in running_daemons:
                            continue
                        self.err(f"{len(policy)} policy statement(s) need "
                                 f"{need}, which is not running")
                        print(f"    first one: {policy[0]}")
                        print_prose("they would commit and store nothing. "
                                    + enable_daemon_advice(need))
                        ok = False

                    # Static routes, which are neither a `router` line nor a
                    # policy statement and so were checked by nothing. On a box
                    # without staticd a default route commits, reports success
                    # and forwards no packets - and a default route is the most
                    # common statement there is on an edge box.
                    statics = [l.strip() for l in target.routing
                               if l.strip().startswith(STATIC_STATEMENT)]
                    for need in STATIC_DAEMONS if statics else ():
                        if need in running_daemons:
                            continue
                        self.err(f"{len(statics)} static route(s) need "
                                 f"{need}, which is not running")
                        print("    first one: "
                              + as_hierarchy(statics[0],
                                             block_of(target.routing,
                                                      statics[0])))
                        print_prose("they would commit and forward "
                                    "nothing. "
                                    + enable_daemon_advice(need))
                        ok = False

            # A static route whose next hop is on no connected network is
            # accepted by FRR, installed, and silently inert: FRR holds it, the
            # FIB never gets it, and "show ip route" looks correct. It is the
            # exact failure `show route verbose` exists to find afterwards, and
            # it is cheaper to say so at the prompt (#95).
            # Refused here rather than by FRR, because FRR's refusal costs
            # the operator the NEXT commit too. See prefix_range_error().
            for stmt in target.routing:
                why = prefix_range_error(stmt.strip())
                if why:
                    self.say_finding("error", stmt.strip(), why)
                    ok = False

            # BGP the VTY can read: a neighbour FRR will not accept, a peer
            # group nobody declared. Not "will the session come up" - that is
            # not in the configuration - but the shapes that cannot work
            # whatever the other end does (#142).
            # The interface set and the pair map are the same three the OSPF
            # findings get, and for the same reason: BGP unnumbered names an
            # interface in an FRR statement, so the name has to be one FRR can
            # act on (#208).
            for sev, stmt, why in bgp_errors(
                    target.routing, known_interfaces, pairs, dp_interfaces):
                if sev == "error":
                    self.say_finding("error", stmt, why)
                    ok = False
                else:
                    self.say_finding("warning", stmt, why)

            # An AS number that is not one, wherever it is written - at the
            # prompt or in a file this box loaded at boot (#415). The prompt
            # refuses `router bgp` on its own, and this is the reader that
            # covers the other door and the other three statements.
            for sev, stmt, why in asn_findings(target.routing):
                self.say_finding(sev, stmt, why)
                if sev == "error":
                    ok = False

            # An AS-path regex FRR would refuse, refused at the prompt
            # instead of mid-commit (#146).
            for stmt in target.routing:
                why = as_path_regex_error(stmt.strip())
                if why:
                    self.say_finding("error", stmt.strip(), why)
                    ok = False

            # A name a route-map matches on that nothing defines. Said and not
            # refused: FRR accepts the clause and binds it when the list
            # arrives, so refusing it blocked building a policy map-first
            # (#208).
            for stmt, why in undefined_policy_lists(target.routing):
                self.say_finding("warning", stmt, why)

            # What each AS-path regex actually matches, against the paths this
            # box has received. A regex that compiles and matches nothing - or
            # everything - is the common mistake, and it cannot be refused:
            # `permit 65001` is legal and also matches 165001 and 650012. An
            # operator reading their own regex back will not see it; a count
            # against real paths is what shows it.
            self.report_as_path_reach(target.routing)

            # OSPF: shapes that commit cleanly and do nothing (#174). The
            # interface set is the one the existence check above built, when
            # it could read the dataplane - an empty set means "could not
            # ask", and ospf_findings refuses on a name only when it has a
            # list to refuse it against.
            for sev, stmt, why in ospf_findings(
                    target.routing, target.addresses,
                    known_interfaces, target.pools, pairs, dp_interfaces):
                if sev == "error":
                    self.say_finding("error", stmt, why)
                    ok = False
                else:
                    self.say_finding("warning", stmt, why)

            # The MTU stall, which FRR cannot see because FRR sees only the
            # TAP. The same sentence `show ospf neighbor` prints once the
            # adjacency this predicted is sitting in Exchange.
            for ifname, host, here, there in self.ospf_mtu_mismatch(
                    target.routing, target.mtus or self.device_mtus()):
                said = mtu_stall_lines(ifname, host, here, there)
                print("warning: " + said[0])
                print(said[1])

            # IS-IS: shapes that commit cleanly and carry nothing (#175).
            for sev, stmt, why in isis_findings(target.routing):
                if sev == "error":
                    self.say_finding("error", stmt, why)
                    ok = False
                else:
                    self.say_finding("warning", stmt, why)

            # Origin validation that is not validating anything (#147).
            for stmt, why in rpki_findings(target.routing):
                self.say_finding("warning", stmt, why)

            # Route-maps: one refusal and two things worth saying (#144). The
            # warnings do NOT fail the check - a permit with no match is a
            # real idiom - but they are printed, because afterwards nobody can
            # tell a finished policy from an unfinished one.
            # REFUSED ONLY WHEN THIS CHANGE IS WHAT CAUSES IT, which is the
            # guard the reference check below has carried since #145 and this
            # one needed the moment it gained a value check.
            #
            # WHICH CHECKS NEED THIS GUARD, because the other refusals in this
            # function do not have it and that is not an oversight.
            #
            # A finding can only fire on something the box ALREADY has if FRR
            # would take that statement. `set local-preference banana` is such
            # a statement: FRR's grammar for it is WORD, vtysh takes it,
            # `frr-reload.py --test` exits 0 and bgpd carries it back verbatim
            # - so a box can be running one, and a refusal that does not ask
            # makes every commit on that box fail.
            #
            # The checks without the guard are about statements FRR ITSELF
            # refuses: an as-path regex that does not compile, a `remote-as 0`
            # (FRR's ASNUM takes 1 to 2^32-1), a prefix-list range its own
            # parser rejects. Those cannot be facts, only requests, which is
            # why no box has ever been locked out by them. Established by
            # driving all four readers rather than by reading them, which is
            # also why five long-standing refusals were left alone instead of
            # being given a guard they cannot need.
            #
            # So the question to ask of a new refusal here is not "is it
            # right" but "can the box already be in this state". If FRR takes
            # the statement, the guard is not optional (#358).
            #
            # `set local-preference banana` was accepted at this prompt and
            # committed until #354, and FRR stores it and sets nothing. So
            # there are boxes carrying one. Without this guard, upgrading to a
            # version that refuses it makes EVERY commit on such a box fail,
            # and `--load` refuses the saved file whole at boot - a box that
            # was working comes up with nothing on it (#358, and the same
            # shape as #357 one file over).
            #
            # What an operator can still do is fix the statement, which turns
            # the finding off. What they cannot be made to do is fix it before
            # they are allowed to change anything else.
            rm_already = {stmt for sev, stmt, _
                          in route_map_findings(running.routing)
                          if sev == "error"}
            for sev, stmt, why in route_map_findings(target.routing):
                if sev == "error" and stmt not in rm_already:
                    self.say_finding("error", stmt, why)
                    ok = False
                else:
                    self.say_finding("warning" if sev != "error" else "error",
                                     stmt, why)

            # A route-map named and never defined (#145). REFUSED, and the
            # difference from the policy lists above is measured rather than
            # argued: an undefined community list is bound when the list
            # arrives, and an undefined route-map denies everything the
            # statement governs for as long as it is missing. FRR says so
            # once, to the vtysh session, in a line `Frr._summarise()` throws
            # away - so on this box nothing says it at all.
            #
            # Refused only when THIS CHANGE is what causes it, the same guard
            # the lockout checks above use, and here it is what keeps a box
            # bootable. `--load` runs this check at boot and refuses the file
            # WHOLE if it fails, so a box whose saved configuration already
            # carries the reference would come up with nothing on it at all -
            # which is a worse outcome than the fault being refused, and the
            # same trap the stale `tap<n>` stanza in Shell.load() exists for.
            already = {stmt for sev, stmt, _
                       in route_map_reference_findings(running.routing)
                       if sev == "error"}
            for sev, stmt, why in route_map_reference_findings(target.routing):
                if sev == "error" and stmt not in already:
                    self.say_finding("error", stmt, why)
                    ok = False
                else:
                    self.say_finding("warning", stmt, why)

            # An eBGP neighbour with no maximum-prefix (#145). A warning and
            # never a refusal: running a session without a limit is a real
            # design and the operator's call, which is what the issue asking
            # for it said and what #208 is the standing reason for.
            #
            # Only about the neighbours THIS CHANGE brings into that state,
            # the same subtraction the route-map refusal above makes. A box
            # whose three upstreams are deliberately unlimited would otherwise
            # print three warnings on every commit it ever takes, and an alarm
            # that is always on is one nobody reads - the reason
            # pool_announce_findings gives for being a warning applies just as
            # well to how often a warning is allowed to speak.
            carried = {stmt for _, stmt, _
                       in max_prefix_findings(running.routing)}
            for sev, stmt, why in max_prefix_findings(target.routing):
                if stmt not in carried:
                    self.say_finding(sev, stmt, why)

            # A policy that withdraws the prefix carrying THIS session (#145).
            # The SSH lockout check above refuses a configuration nobody could
            # log in with; this is the same failure one layer down, and the
            # only difference is that it cannot be refused - withdrawing a
            # prefix on purpose is an ordinary thing to do. It is gated on
            # `commit cutoff` instead, the shape #23 established.
            here = session_local_address()
            why_not = None
            if here is None:
                why_not = ("this session did not arrive over SSH, so there "
                           "is no address to check it against. A serial "
                           "console cannot be cut off by a withdrawal; a "
                           "session reaching this box through another one "
                           "can")
            elif ":" in here:
                # What this box originates is read in IPv4 only, the way the
                # rest of `routes` is: the CGNAT's public side is IPv4 by
                # definition. An IPv6 session is a real session all the same,
                # and skipping it without a word would be the silence this
                # note exists to prevent.
                why_not = (f"this session arrived on {here}, and what this "
                           "box originates is read in IPv4 only")
                here = None
            self.cutoff = [(pfx, why, advice) for pfx, why, advice
                           in session_cut_findings(running.routing,
                                                   target.routing, here)]
            if why_not and (advertisement_fingerprint(running.routing)
                            != advertisement_fingerprint(target.routing)):
                # Said, not skipped. A check that quietly does not run is one
                # the operator believes ran and passed. Printed only when the
                # change touches what this box advertises, because a note on
                # every commit from the console is an alarm nobody reads.
                # Headline and body, because as one sentence it measured
                # 103 columns and the reason under it 189 (#271).
                print("note: this change alters what this box advertises")
                print_prose("the prefix carrying this session was not "
                            "checked")
                print_prose(why_not)

            if d["routing_changed"] and target.routing:
                rc_fib, fib_text = self.dp.run("show ip fib")
                if rc_fib == 0 and fib_text.strip():
                    fibs = routes.parse_fib(fib_text,
                                            self.fib_table_names())
                    # Which table a VRF's static routes go into is FRR's
                    # question to answer, not this VTY's: an interface's `vrf`
                    # is a NUMBER here and an FRR block is a NAME, and only the
                    # box knows the two mean the same table. None, and not an
                    # empty map, when FRR cannot be asked - "no table for this
                    # VRF" and "nobody could be asked" are different news.
                    rc_vrf, vrf_text = self.frr.show("show vrf")
                    vrf_tables = (routes.parse_vrf_tables(vrf_text)
                                  if rc_vrf == 0 else None)
                    # WHAT IS CONNECTED, asked of both moments at once: what
                    # the dataplane forwards on now, and what this
                    # configuration puts on it. The check is about the box
                    # that exists AFTER the change, and neither half on its
                    # own describes that box.
                    #
                    # The FIB alone is what #294 was filed for. `prismash
                    # --load` runs this at boot against a VPP that has just
                    # started, so the addresses that make a next hop connected
                    # are in the very file being read and the FIB has none of
                    # them - and `--load` refuses the file WHOLE, leaving the
                    # box with no pools, no addresses and no attachments. It
                    # has not been seen because prisma-routing.service runs after
                    # frr.service and FRR has already read the same static
                    # route out of its own frr.conf, so `routing_changed` is
                    # false and this branch is never reached: the
                    # configuration a box boots with was protected by a second
                    # copy of it in another daemon's file.
                    #
                    # MEASURED, and it is why this is a union and not a switch
                    # on "are we loading a file". The same refusal comes out of
                    # an ordinary interactive commit that brings an address and
                    # the route it makes reachable in one candidate, which is
                    # how an operator configures a new uplink:
                    #
                    #   error: this next hop is on no connected network:
                    #       200.150.0.2
                    #
                    # A branch for `--load` would have left that standing, and
                    # it is the same defect through the door the operator uses
                    # every day.
                    #
                    # The candidate's addresses can only ADD to the set, so
                    # nothing the check refuses today stops being refused: a
                    # next hop on no connected network typed at a configured
                    # box is covered by neither half. The check is not switched
                    # off for any path, which is #197's argument - a route that
                    # is accepted and never forwards is worse than one refused
                    # loudly.
                    connected = routes.connected_from_fib(fibs)
                    for table, prefixes in target.connected_prefixes().items():
                        connected.setdefault(table, []).extend(prefixes)
                    bad, unplaced = routes.unreachable_next_hops(
                        target.routing, connected,
                        block_paths(target.routing), vrf_tables)
                    for stmt, nexthop in bad:
                        # #251: named in the block it is in, because a VRF
                        # static route has no top-level spelling to go and
                        # look for.
                        named = as_hierarchy(stmt,
                                             block_of(target.routing, stmt))
                        # The next hop in the headline and the STATEMENT on
                        # its own row, whole. Together they measured 93
                        # columns and `err` wraps nothing (#271); apart, the
                        # statement gets the row it needs to stay greppable
                        # in `show configuration`, which is the rule from
                        # #252 applied to a refusal that had not had it.
                        self.err("this next hop is on no connected network: "
                                 f"{nexthop}")
                        print("    " + named)
                        print_prose("the route would be accepted and never "
                                    "reach the forwarding table")
                        ok = False
                    # Said out loud, never passed over. A VRF whose table this
                    # box cannot find is one whose routes went unchecked, and
                    # an operator who read "check succeeds" would believe
                    # otherwise. Not a refusal: the VRF may be about to exist,
                    # and refusing a configuration this VTY merely cannot see
                    # is the harm in #208.
                    for vrf, why, stmts in unplaced:
                        print(f"note: {len(stmts)} static route(s) in 'vrf "
                              f"{vrf}' were not checked for a reachable next "
                              "hop")
                        print_prose(why)
                        print(f"    first one: {stmts[0]}")

            # A POOL'S PUBLIC RANGE OVER ONE OF THE BOX'S OWN ADDRESSES,
            # asked of the CANDIDATE and not only of the live box (#296).
            #
            # The dataplane makes this check itself and keeps it: `pool ...
            # check` below runs bpa_check_local_overlap(), which walks vnet's
            # interface table. What that walk can see depends on what the box
            # already has, and at `prismash --load` the answer is nothing at
            # all. Apply creates pools BEFORE it applies addresses, so the
            # walk reads an empty list, the pool is created, and the collision
            # comes back a moment later as a vlib_log_err on the plugin's log
            # class - which is not on the console, not in
            # `appliance/selftest`, and not in anything this VTY prints.
            #
            # MEASURED on this build before a line of this was written, the
            # same two statements in one file:
            #
            #   typed at a configured box: refused, exit 1, no pool created
            #   loaded into an empty box:  exit 0, pool created, address
            #                              applied, one line in `show logging`
            #
            # So the box that refuses it is the one an operator is watching,
            # and the box that takes it is the one that came up on its own at
            # four in the morning. What it costs is the sentence
            # bpa_check_local_overlap()'s own comment names: bpa-out2in runs
            # on the ip4-unicast arc ahead of ip4-lookup, so the box's own BGP
            # session or OSPF adjacency is translated by its own CGNAT and
            # drops with nothing obvious in any log.
            #
            # EVERY pool in the candidate, not only the ones being added, and
            # that is the direction that had to be got right rather than
            # guessed at. At `--load` every pool is an add and the distinction
            # buys nothing; at an interactive commit it decides whether
            # `commit` can write a file that `--load` will later refuse WHOLE.
            # An address moved into a standing pool's range is the same
            # collision, `save` puts both statements in prisma-routing.conf, and
            # the next boot would then meet a refusal over a commit this VTY
            # had accepted - a box coming up with no pools and no addresses
            # because of a change that went through months earlier. The two
            # paths agree on what is legal or neither of them is worth having.
            #
            # The plugin's live walk is NOT replaced by this and must not be.
            # It catches the address that arrived from somewhere this
            # candidate does not describe - DHCP, `vppctl set interface ip
            # address`, an operator at the dataplane - and no reading of a
            # configuration file can see any of those. Both therefore fire on
            # a configured box where the address is in the running
            # configuration, which is one fault said twice, and that is the
            # cheaper half of the trade.
            #
            # Addresses come from Config.interface_addresses_v4(), which
            # connected_prefixes() is also built on: one reader of "what
            # addresses will this box have", because two of them disagreeing
            # about which moment is being asked about is this defect.
            for ifname, addr in target.interface_addresses_v4():
                host = addr.split("/")[0]
                for pname, pool in sorted(target.pools.items()):
                    hit = next((p for p in pool.outside
                                if routes.covers_address(p, host)), None)
                    if hit is None:
                        continue
                    # The dataplane's own first clause, word for word. An
                    # operator who meets this from the VTY on one box and
                    # from the plugin on the next reads one sentence rather
                    # than two that have to be reconciled.
                    self.err(f"pool '{pname}': public address {host} is "
                             f"configured on {ifname}")
                    # Both statements whole and on their own rows, because
                    # the operator's next move is to grep one of them out of
                    # `show configuration` and delete it (#252).
                    print(f"    interface {ifname} / ipv4 address {addr}")
                    print(f"    nat / cgnat / pool {pname} / outside {hit}")
                    print_prose("cgnat would translate the box's own "
                                "control-plane traffic on that address: "
                                "bpa-out2in runs before the box decides a "
                                "packet is for itself, so the BGP session or "
                                "OSPF adjacency on it drops for no apparent "
                                "reason")
                    print_prose("take the address off the interface, or "
                                "narrow the pool's public range so that it "
                                "does not contain it")
                    ok = False

            # A BLOCK-STRIDE ON A DETERMINISTIC POOL, asked here for the
            # reason the block against the port range below is asked here:
            # `mode` and `block-stride` are two statements, they can be typed
            # in either order, and a guard at either line would refuse a pool
            # that is about to be correct.
            #
            # EVERY ROUTE IN ONE LOOP, because the two statements reach the
            # device by three of them. A pool being created or rebuilt carries
            # the stride on the creation message; a stride added to a pool
            # that stays put goes through bpa_pool_settings_set; and the
            # device check below runs for the first two only. Without this
            # loop the third one fails at the WRITE - after the pools have
            # been rebuilt, with the candidate half applied - which is what
            # the block-fits check below is written to avoid.
            #
            # The dataplane refuses it too, in bpa_pool_init() and in
            # bpa_pool_settings_set() - both through the one
            # bpa_pool_stride_refusal() they share, so "both" is true by
            # construction - and that is the authority rather than this: a
            # stride on a deterministic pool is never read, because a stride
            # steps along the mapped blocks and that pool's extension comes
            # out of the reserve, own address first and then whatever its
            # worker's slice of the reserve has free (#375). This asks first
            # so the answer arrives before anything has been applied.
            #
            # NOT AT BOOT. `load()` drops the stride from such a pool before
            # it gets here, and says so, because a file saved before this
            # refusal existed carries the pair on a box that ran on it
            # yesterday; the argument is written there.
            stride_refused = set()
            for pool in (d["pool_add"] + d["pool_replace"]
                         + list(d["settings_set"].values())):
                if not pool.stride_inert():
                    continue
                stride_refused.add(pool.name)
                # The pool named LAST on the row, which is not the shape
                # the refusals around it use: a pool name has no widest, and
                # a value with the VTY's own words behind it folds inside the
                # sentence at whatever width the operator's window happens to
                # be (#363). Last on the row, the fold lands after the words.
                self.err("block-stride applies to dynamic pools only, not "
                         f"to pool {pool.name}")
                # The two statements whole and on their own rows, the #252
                # idiom: the operator's next move is to go and edit one of
                # them, and which one is their decision and not this box's.
                print(f"    mode {pool.mode}")
                print(f"    block-stride {pool.block_stride}")
                print_prose("a stride steps along the mapped blocks of a "
                            "public address, and a deterministic pool's "
                            "extension never comes from there: it comes out "
                            "of the reserve, from the subscriber's own "
                            "address when its worker has one free there and "
                            "otherwise from wherever its worker's slice of "
                            "the reserve has one, so the setting is never "
                            "read; drop the "
                            "block-stride, or put the pool in dynamic mode")
                ok = False

            for pool in d["pool_add"] + d["pool_replace"]:
                # Already refused above. The device refuses it too and the
                # check below would print its sentence as well, which is two
                # verdicts and two paragraphs for one fault - and the operator
                # would then have to work out whether they are the same one.
                if pool.name in stride_refused:
                    continue
                if not pool.inside or not pool.outside:
                    self.err(f"pool '{pool.name}': inside and outside required")
                    ok = False
                    continue
                # A PORT RANGE TOO NARROW TO HOLD ONE BLOCK, asked HERE and
                # not at the `ports` line, which is where the operator types
                # it (#361). It is the one check on this statement that needs
                # a second statement to answer, and the two can be typed in
                # either order: `ports 1000 1100` followed by `block-size 64`
                # is a correct pool built in a correct order, and a guard at
                # the line would have refused its first half. The commit is
                # where the pool is finished, so it is where the question can
                # be asked without inventing an order for the operator.
                #
                # The pools this candidate DEFINES, and not every pool in it,
                # which is the #357 rule at commit level: a pool already on
                # the box is a fact, and refusing a commit over a pool this
                # change does not touch is how a box stops being
                # configurable. bpa_pool_init() would not have built one
                # anyway.
                #
                # Refused rather than reported, unlike pool_announce_findings
                # below, and the difference is that this one is not a proxy
                # for anything: bpa_pool_init() refuses it too, a few
                # lines past the ordering clause, so a commit allowed through
                # here fails at the write instead - later, and with the
                # candidate already half applied.
                why = pool.block_fits()
                if why:
                    self.err(f"pool '{pool.name}': {why}")
                    # The two statements whole and on their own rows: the
                    # operator's next move is to go and edit one of them, and
                    # both carry numbers they will compare (#252).
                    print(f"    ports {pool.ports}")
                    print(f"    block-size {pool.block_size}")
                    print_prose("a pool whose port range is smaller than its "
                                "block has no block to give anyone: widen "
                                "the range, or make the block smaller")
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
            # A pool's public prefix that nothing announces (#145). The one
            # check here that needs both halves of the box: FRR does not know
            # what a CGNAT pool is, and the dataplane does not know what BGP
            # is. Reported and never refused - pool_announce_findings() says
            # why, and the reason is measured rather than argued.
            for sev, subject, why, advice in pool_announce_findings(
                    target.routing, target.pools):
                print(f"{sev}: {subject}: {why}")
                for line in advice:
                    print("    " + line)
            # And a pool whose blocks only an export accounts for, on a box
            # that exports nothing (#444). Warned and never refused, for the
            # announce warning's reason; through say_finding, so the sentence
            # is wrapped at the console and the pool is named on its own row.
            for sev, stmt, why in dynamic_export_findings(target.pools,
                                                         target.globals):
                self.say_finding(sev, stmt, why)

            # Would this commit leave the two boxes mapping subscribers
            # differently? Asked only when a peer configuration has been put
            # on this box at all, and only about the half of a pool that
            # decides the mapping - `mapping_diff_to` says why that is not
            # `is_same`.
            #
            # WARNED HERE, AND GATED ELSEWHERE, and which of the two it is
            # depends on whether this box is in a pair.
            #
            # On a box with no virtual router a copied peer configuration is
            # a comparison aid and nothing more: nothing on this box will
            # ever take a service address over, so a refusal would be this
            # VTY deciding that a file in /etc is a relationship. It warns,
            # which is what it has always done.
            #
            # On a box that DOES stand for a service address the divergence
            # is the silent failure `docs/ha.md` is about, and `do_commit`
            # refuses it until it is asked for by name with `commit diverge`
            # - the same shape as `commit remap`. The word is what makes the
            # refusal safe: two boxes cannot be committed in the same instant,
            # so every correct capacity change diverges the pair between the
            # first commit and the second, and a refusal with no way past it
            # would fire on every correct procedure - which is the harm #208
            # was filed for, and the way past it would become "stop copying
            # the peer's configuration".
            #
            # The reason is dropped on purpose: `show cgnat ha` is where a
            # box says why it could not read the peer. A commit that printed
            # "there is no peer configuration" every time would say it on
            # every box that is not in a pair, which is most of them.
            if not target.vrrp:
                peer_cfg = read_peer_config()[0]
                if peer_cfg is not None:
                    ha = target.mapping_diff_to(peer_cfg)
                    if ha:
                        self.say_divergence("warning", ha)
                        print_prose("'show cgnat ha' compares the two in "
                                    "full; the peer's configuration here was "
                                    f"copied from {PEER_CONFIG_PATH}")

            # Only when FRR can answer. A box may run CGNAT with no routing -
            # Frr.available() has said so in its docstring since it was
            # written, and was never called, so validation ran unconditionally
            # and a CGNAT-only appliance could not commit an interface MTU.
            #
            # Entering an interface block stages `interface <name>` as an FRR
            # stanza, so target.routing is non-empty for every interface
            # change: setting an address on a box without FRR failed the whole
            # commit, CGNAT half included (#137).
            if target.routing or running.routing:
                if self.frr.available():
                    rc, out = self.frr.reload(target.routing, check_only=True)
                    if rc:
                        print(f"routing validation failed: {out}")
                        self.say_frr_refused(target.routing)
                        ok = False
                elif self.real_routing(target.routing):
                    # Routing the operator actually wrote, and nothing to
                    # check it with. Refused, and named, out of the one place
                    # the sentence lives: see FRR_SILENT_REFUSED for why it is
                    # not written here, and why the verdict stops where it
                    # does.
                    self.err_prose(*FRR_SILENT_REFUSED)
                    ok = False
            return ok

        for name in d["pool_remove"]:
            p = Pool(name)
            r = self.api_call("bpa_pool_add_del", p.api_args(is_add=False))
            if not r.ok and r.state != vppapi.REFUSED:
                self.err_dataplane(f"removing pool '{name}': {r.reason}")
                return False
        for pool in d["pool_replace"]:
            self.api_call("bpa_pool_add_del", pool.api_args(is_add=False))
        for pool in d["pool_add"] + d["pool_replace"]:
            r = self.api_call("bpa_pool_add_del", pool.api_args())
            if not r.ok:
                # Named, because a commit carrying several pools has to say
                # which one the dataplane refused. "commit failed" sends the
                # operator to read the whole candidate.
                self.err_dataplane(f"pool '{pool.name}': {r.reason}")
                return False

        # The settings the dataplane can change in place, before the lists
        # and the bounds: they are the ones an operator changes while looking
        # at a live problem, and a commit that applied them last would leave
        # them behind whatever else in the same commit failed.
        for name, pool in d["settings_set"].items():
            r = self.api_call("bpa_pool_settings_set",
                              pool.settings_api_args())
            if not r.ok:
                self.err_dataplane(f"pool '{name}': settings: {r.reason}")
                return False

        # The bypass lists first: they only ever let traffic past
        # untranslated, so applying them before anything else cannot leave a
        # half-applied change translating what the operator just exempted.
        for pool, is_dst in d["bypass_set"]:
            r = self.api_call("bpa_bypass_set", pool.bypass_api_args(is_dst))
            if not r.ok:
                side = "destination" if is_dst else "source"
                self.err_dataplane(f"pool '{pool.name}': bypass {side}: {r.reason}")
                return False

        # The per-subscriber bounds, before the forwards and before routing:
        # they only ever refuse, so applying them early cannot break a change
        # that is still half-applied.
        for name, pool in d["limits_set"].items():
            r = self.api_call("bpa_limits_set", pool.limits_api_args())
            if not r.ok:
                self.err_dataplane(f"pool '{name}': limits: {r.reason}")
                return False

        # Removals first: a forward moved from one public port to another is
        # a removal and an addition, and doing the addition first would be
        # refused for colliding with the entry it is replacing.
        for pool, fwd in d["fwd_del"]:
            r = self.api_call("bpa_forward_add_del",
                              pool.forward_api_args(fwd, is_add=False))
            if not r.ok and r.state != vppapi.REFUSED:
                self.err_dataplane(f"pool '{pool.name}': removing forward "
                                   f"{fwd[0]} {fwd[1]}: {r.reason}")
                return False
        for pool, fwd in d["fwd_add"]:
            r = self.api_call("bpa_forward_add_del",
                              pool.forward_api_args(fwd))
            if not r.ok:
                self.err_dataplane(f"pool '{pool.name}': forward {fwd[0]} {fwd[1]} "
                                   f"to {fwd[2]} {fwd[3]}: {r.reason}")
                return False

        for ifname in d["if_remove"]:
            if not self.set_cgnat_feature(ifname, running.interfaces[ifname],
                                          False):
                return False
        for ifname, direction in d["if_add"].items():
            if not self.set_cgnat_feature(ifname, direction, True):
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

        # Two facts about a pool that are not properties of the pool, applied
        # and warned about by the dataplane, and both have to reach the person
        # who committed it - who is at the far end of an API call and sees none
        # of the dataplane's log, exactly like the timeout above. A reserve
        # that does not reach every worker this box has, and a block-stride the
        # worker sharding cannot honour.
        #
        # ONE SET AND ONE READER, which is what relay_pool_warnings() is for
        # and what its docstring asks of the next warning to land on that
        # screen. It takes EVERY warning row under a named pool, so a second
        # loop over the same output would print both of these twice for any
        # pool both sets name. #372 and #373 were written a day apart against
        # the same screen and each arrived with its own loop; they are one
        # reader here.
        #
        # THE TWO ARRIVE BY DIFFERENT ROUTES, which is why the set is built in
        # two steps. `reserve` is not on bpa_pool_settings_set at all - it is
        # taken out of capacity, capacity is the divisor under the blocked
        # policy, so changing it re-maps subscribers and Pool.lines() keeps it
        # outside `in_place` - so lowering one on a live pool lands in
        # pool_replace. A stride goes both ways: a pool being created carries
        # it on the creation message and never reaches settings_set, and a pool
        # already carrying traffic takes a change in place, because the stride
        # only decides where the NEXT extension goes.
        #
        # ONLY THE POOLS THIS COMMIT TOUCHED. The device warns about every pool
        # that is short whenever it is asked, because a `show` answers about
        # the box; a commit answers about what was just committed. Relaying all
        # of them would print the same paragraph about a pool nobody changed,
        # and a warning that fires whatever the operator did is one they learn
        # to scroll past - which costs them the one commit where it was about
        # their change.
        touched = {p.name for p in d["pool_add"] + d["pool_replace"]
                   if p.block_reserve or p.block_stride}
        touched |= {name for name, p in d["settings_set"].items()
                    if p.block_stride}
        if touched:
            self.relay_pool_warnings(touched)

        # The card the dataplane is to be given, before SSH and for a reason
        # that is not ordering: nothing here takes effect until the dataplane
        # starts again, so it cannot leave the box half-changed. It goes with
        # the box's own configuration because that is what it is, and it goes
        # FIRST of that group so its "not in effect yet" paragraph is not
        # buried under whatever SSH and the accounts printed.
        if d["dataplane_if_changed"]:
            ok, msg = self.apply_dataplane_if(target.dataplane_if)
            if not ok:
                self.err(f"dataplane interface: {msg}")
                return False
            self.say_dataplane_if_pending(target.dataplane_if, msg)
        if d["ssh_changed"]:
            ok, msg = self.sys.write_ssh(target.ssh)
            if not ok:
                self.err(f"ssh: {msg}")
                return False
            if msg:
                print(f"ssh: {msg}")
        if d.get("dns_changed"):
            ok, msg = self.sys.write_dns(target.dns_servers)
            if not ok:
                self.err_prose("dns: nothing was changed", msg)
                return False
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

        # Shut down FIRST, bring up LAST, with everything else in between.
        #
        # An interface being taken down should stop carrying traffic before its
        # configuration is changed underneath it, and one being brought up
        # should come up already configured rather than forwarding for the few
        # milliseconds it takes to apply the rest. Neither ordering is
        # observable in a lab that changes one thing at a time, which is
        # exactly why it is decided here rather than left to the dictionary.
        #
        # With ONE exception, and it is a box that does not come up. An
        # interface this same delta is about to CREATE cannot be shut down
        # before it exists, and a loopback is created administratively down -
        # so its saved configuration carries `shutdown`, and replaying that
        # file is the thing prisma-routing.service does at every boot:
        #
        #   interface loop7
        #    linux-name loop7
        #    shutdown
        #
        #   error: interface loop7: the dataplane does not have it
        #
        # The commit fails, the unit fails, and the box comes up with none of
        # its configuration - so every loopback this product can create (#179)
        # survived exactly until the next reboot. Measured in lab/lcp.sh by
        # saving a paired loopback, taking it down and handing the box back
        # the file it wrote.
        #
        # The ordering argument above does not apply to these: an interface
        # that does not exist yet is carrying no traffic, so there is nothing
        # to stop before configuring it.
        being_created = set(d["loopback_add"]) | set(d["subif_add"])
        for ifname in d["admin_down"]:
            if ifname in being_created:
                continue
            if not self.set_admin(ifname, False):
                return False

        # Loopbacks and sub-interfaces first: nothing can be put on an
        # interface that does not exist yet. Removals last, further down, for
        # the mirror-image reason.
        for ifname in d["loopback_add"]:
            if not self.create_loopback(ifname):
                return False
        for ifname, tag in d["subif_add"].items():
            if not self.create_subif(ifname, tag):
                return False
        # The half of the shutdown pass that had to wait for the interface to
        # exist. Still before the addresses and the pair below, so the
        # ordering the comment above argues for is kept for everything that
        # can observe it.
        for ifname in d["admin_down"]:
            if ifname in being_created and not self.set_admin(ifname, False):
                return False

        # IS-IS needs its frames punted before the adjacency can form, and
        # the punt is box-wide rather than per-interface, so it goes here
        # rather than with the interface work below.
        if any(l.split()[:2] == ["router", "isis"] for l in target.routing
               if l.split()):
            if not self.enable_osi_isis():
                return False

        # The pair, before anything that needs FRR to see the interface. A
        # loopback with no pair is invisible to the routing daemons, which is
        # the entire reason an operator creates one.
        for ifname, host in d["lcp_set"].items():
            # A pair RENAMED, which `linux-name` on a card that already
            # carries its default pair is (#389). The dataplane answers
            # "exists" to a second pair on one interface - apply_management_path
            # relies on exactly that - so the old pair goes before the new
            # one is made. Read off the running configuration, which is
            # `show lcp` and not memory (#161).
            had = running.linux_names.get(ifname)
            if had and had != host and not self.set_lcp_pair(ifname, "",
                                                             False):
                return False
            if not self.set_lcp_pair(ifname, host, True):
                return False

        # Moving an interface between routing tables, addresses and all.
        #
        # VPP REFUSES to move an interface that still holds addresses - retval
        # -114 - so this is remove, move, add, in that order, as one operation.
        # Doing it as two independent deltas produces the refusal, and doing
        # the move first and the addresses second produces an interface that
        # is briefly in the new table with nothing on it.
        #
        # The interfaces here are excluded from the ordinary address delta, so
        # their addresses are applied once, here, and not twice.
        for ifname, (table, had, want) in d["vrf_set"].items():
            for a in had:
                if not self.set_address(ifname, a, False):
                    return False
            if not self.set_vrf(ifname, table):
                return False
            for a in want:
                if not self.set_address(ifname, a, True):
                    return False

        # Addresses before everything except the admin state, and removals
        # before additions: an operator replacing 10.0.0.1/24 with
        # 10.0.0.2/24 on the same interface expects one address at the end,
        # and adding first would briefly leave the interface holding both.
        for ifname, addrs in d["addr_del"].items():
            for a in addrs:
                if not self.set_address(ifname, a, False):
                    return False
        for ifname, addrs in d["addr_add"].items():
            for a in addrs:
                if not self.set_address(ifname, a, True):
                    return False

        # Read once, for set_address() to consult while it applies addresses.
        self._ra_enabled_now = set(target.ra_enabled)
        for ifname in d["ra_on"]:
            if not self.set_ra_suppress(ifname, False):
                return False
        for ifname in d["ra_off"]:
            if not self.set_ra_suppress(ifname, True):
                return False

        for ifname, mac in d["mac_set"].items():
            if not self.set_mac(ifname, mac):
                return False
        for ifname, text in d["desc_set"].items():
            if not self.set_description(ifname, text):
                return False
        for ifname in d["desc_del"]:
            if not self.set_description(ifname, ""):
                return False

        # MTU on BOTH sides. docs/routing.md records the failure: the TAP
        # created by linux_cp comes up at 9000, OSPF compares MTU in Database
        # Description packets, and the adjacency stops at Exchange with nothing
        # in the log saying why. Setting only the TAP leaves the packet path -
        # the VPP interface - unchanged, which is the half that matters.
        for ifname, mtu in d["mtu_set"].items():
            # Over the binary API, so that setting an MTU no longer needs the
            # VPP CLI socket - and an account with that socket can do anything
            # on this box, including deleting a pool (#76).
            #
            # mtu[0] is the L3 MTU, which is what OSPF compares in Database
            # Description packets. The CLI equivalent was "set interface mtu
            # PACKET <n>": plain "set interface mtu" changes the hardware max
            # frame size, which most drivers refuse - and refused while vppctl
            # still exited 0, so the failure was invisible. The array here says
            # which one is meant without depending on a keyword.
            idx = self.api.interface_index(ifname)
            if idx is None:
                self.err(f"interface {ifname}: the dataplane does not have it")
                return False
            r = self.api_call("sw_interface_set_mtu",
                              {"sw_if_index": idx, "mtu": [mtu, 0, 0, 0]})
            if r.state != vppapi.OK:
                self.err_dataplane(f"interface {ifname}: mtu {mtu}: {r.reason}")
                return False
            # Still read back. retval 0 means the message was accepted, not
            # that the driver took the value, and this is the assertion that
            # actually protects the OSPF adjacency.
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
            if not self.set_urpf(ifname, mode):
                return False
        for ifname in d["urpf_del"]:
            if not self.set_urpf(ifname, "off"):
                return False
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

        # Virtual routers after the addresses and before the interfaces are
        # brought up, and the order is the requirement: VRRP advertises FROM
        # the interface's own address, so a router started on an interface
        # that has none is a router whose advertisements go nowhere and whose
        # peer therefore elects itself as well. Two masters for one service
        # address is the failure a pair exists to avoid.
        for ifname, vrs in sorted(d["vrrp_del"].items()):
            for vrid in vrs:
                if not self.del_vrrp(ifname, vrid):
                    return False
        # WHICH OF THESE ALREADY EXIST, read before any of them is touched.
        # A router being created costs nothing to say - there is no address
        # to lose - and one being CHANGED costs the pair two elections, which
        # is what say_vrrp_restart tells the operator.
        if d["vrrp_set"]:
            on_box = self.device_vrrp()
            for ifname, vrs in sorted(d["vrrp_set"].items()):
                for vrid in sorted(vrs):
                    if vrid in on_box.get(ifname, {}):
                        self.say_vrrp_restart(ifname, vrid)
        for ifname, vrs in sorted(d["vrrp_set"].items()):
            for vrid in sorted(vrs):
                if not self.set_vrrp(ifname, vrid, vrs[vrid]):
                    return False
        if d["vrrp_set"]:
            # Read back rather than trust the exit status, the way uRPF and
            # the MTU do: a virtual router that was asked for and is not on
            # the box is a takeover the operator believes in and will not get.
            have = self.device_vrrp()
            for ifname, vrs in d["vrrp_set"].items():
                for vrid in vrs:
                    if vrid not in have.get(ifname, {}):
                        # The interface name in the prose and not in the
                        # verdict: both on one row is 89 columns and folds
                        # inside the name (#355).
                        self.err_prose(f"the dataplane did not take vrrp "
                                       f"{vrid}",
                                       f"it was asked for on {ifname}, the "
                                       "call was accepted, and the box comes "
                                       "back without it")
                        return False

        # PARENTS FIRST, AND NOT AT ALL WHILE A PARENT STAYS DOWN (#407).
        #
        # VPP will not bring a sub-interface administratively up while its
        # super-interface is down. Its own CLI says so - "set interface state:
        # super-interface pg0 must be up" - and over the binary API the same
        # refusal arrives as VNET_API_ERROR_UNSPECIFIED, so what reached the
        # operator was "the dataplane refused it: unspecified error".
        #
        # That refusal failed the whole commit, and the desired state does not
        # change by failing: a sub-interface with no `shutdown` is wanted up,
        # the device has it down, so the delta carried it again on the NEXT
        # commit and failed that one too. Measured on the maintainer's box: an
        # `ipv4 address` on a different card could not be committed at all,
        # because a VLAN sub-interface of a second, shut-down card was in the
        # candidate. A box that cannot commit is the worst thing this product
        # can be, and #400 was the same sentence from the other side.
        #
        # So: the parent's `no shutdown` is applied before the child's, which
        # is the sort below, and a child whose parent will still be down after
        # this commit is left alone and said out loud. Nothing is lost by
        # waiting - the statement stays in the configuration, the device stays
        # down, and the delta carries it again on the commit that brings the
        # parent up. Measured, because it decides this: a sub-interface does
        # NOT follow its parent up by itself.
        stays_down = set(target.admin_down)
        # EVERY SUB-INTERFACE THE DEVICE HAS DOWN AND THE CONFIGURATION DOES
        # NOT, which is a set neither configuration holds: `device_admin_down`
        # leaves a child out when its parent is down, because that is not a
        # statement anybody made, so the delta of running and target cannot
        # carry it. The device is asked instead. Each of them is either
        # brought up here or said out loud, and which one depends on the
        # parent.
        children = {n for n in self.device_admin_down_raw()
                    if "." in n and n not in stays_down}
        for ifname in sorted(set(d["admin_up"]) | children,
                             key=lambda n: (n.count("."), n)):
            parent = ifname.split(".")[0] if "." in ifname else ""
            if parent and parent in stays_down:
                # A warning and not a refusal: the box is doing what the
                # configuration says everywhere it can, and this one interface
                # is waiting on a decision the operator has already made about
                # its parent. Said on every commit while it is true, the way
                # the pool warnings are: it stops the day the parent comes up.
                print(f"warning: interface {ifname}: left shut down")
                print_prose(f"the dataplane will not bring a sub-interface up "
                            f"while its parent is down, and {parent} is shut "
                            f"down; 'no shutdown' on {parent} brings both up")
                continue
            if not self.set_admin(ifname, True):
                return False

        # Destroyed after everything has been taken off them: deleting a
        # sub-interface the dataplane still holds addresses or a CGNAT feature
        # on leaves dangling references to an interface that is gone.
        # Unpaired before the interface goes: removing an interface that still
        # has a pair leaves a TAP in the kernel with nothing behind it, and FRR
        # holding an adjacency on a port that stopped forwarding.
        for ifname in d["lcp_del"]:
            if not self.set_lcp_pair(ifname, "", False):
                return False

        for ifname in d["subif_del"]:
            if not self.delete_subif(ifname):
                return False
        for ifname in d["loopback_del"]:
            if not self.delete_loopback(ifname):
                return False

        for ifname in d["mtu_del"]:
            # No stored default to restore: the driver's own value is whatever
            # the interface reports before we touch it, so removal is reported
            # rather than guessed at.
            print(f"note: mtu removed from {ifname}")
            print_prose("the driver default applies after the interface is "
                        "recreated")

        # Ordered, not as they were typed: `cgnat logging enable` is refused
        # by the dataplane until the exporter statement has been applied, and
        # an operator types the two in whichever order occurs to them.
        for g in ordered_globals(d["globals"]):
            self.apply_global(g)

        if d["routing_changed"]:
            if self.frr.available():
                rc, out = self.frr.reload(target.routing)
                if rc:
                    print(out)
                    self.err("routing reload failed")
                    return False
                # A successful reload is not a silent one. FRR warns about a
                # route-map that does not exist, stores the statement and
                # exits 0 - and that warning used to go nowhere.
                for line in self.frr.diagnostics(out):
                    print(f"  frr: {line}")
            elif self.real_routing(target.routing):
                # The same refusal as the check half above, and the same
                # object: FRR_SILENT_REFUSED is where it is written.
                self.err_prose(*FRR_SILENT_REFUSED)
                return False
            # Otherwise there is nothing here for FRR: interface stanzas whose
            # contents this VTY applies to the dataplane itself.
        # After the routes, so a description is only ever written for a route
        # FRR took (#436). Its own delta key, so a commit that changes only a
        # description is applied rather than reported as having nothing to do.
        if d.get("route_desc_changed"):
            self.write_static_descriptions(target.static_descriptions)
        return True

    def relay_pool_warnings(self, pools):
        """
        Print what `show bpa pool` warns about, for these pools only.

        THE ONE READER OF THAT SCREEN, and it is one on purpose. A warning
        under a pool is several rows and only the first of them carries a word
        to look for, so which pool it is about and where it ends both come from
        the SHOW'S OWN structure rather than from how the sentence happens to
        fold: every pool opens a line with "pool <name>", and everything
        printed about that pool follows it. A re-wrap of the message cannot
        lose a row here (#271).

        That also means this takes EVERY warning row under a named pool,
        whatever it was about. A second loop over the same screen, added for
        the next warning that lands on it, would print both of them twice for
        any pool both sets name - so another warning relayed from `show bpa
        pool` belongs in the set handed to this function, not in a reader of
        its own.

        The dataplane's rows are printed as they came, because they are the
        dataplane's sentence: the worker count, the arithmetic and the words
        live in the plugin, and a copy of any of them here would be a second
        thing to keep in step.
        """
        rc, out = self.dp.run("show bpa pool")
        here, relay = None, False
        for line in (out or "").splitlines():
            if line.startswith("pool "):
                here = line.split()[1]
                relay = False
            elif line.startswith("warning:"):
                relay = here in pools
            if relay:
                print(line.rstrip())

    def ospf_mtu_mismatch(self, routing, mtus):
        """
        Interfaces carrying OSPF whose dataplane and TAP disagree about MTU.

        The check only this product can make, and the reason is the shape of
        the box: FRR sees the TAP and nothing else, so it compares the TAP's
        MTU in its database description packets while the wire runs at the
        dataplane's. When they differ the adjacency sits in Exchange forever
        with no log line on either side - documented in docs/routing.md as
        Pitfall 1, and until now documented is all it was.

        A warning, never a refusal. `ip ospf mtu-ignore` is the supported
        escape hatch and a deliberate asymmetry is legitimate; what is not
        legitimate is nobody saying so.
        """
        _, iface_area, passive, _, _, has_ospf = ospf_state(routing)
        # OSPFv3 stalls on this in exactly the same way, and until #187 the
        # box said nothing about it: RFC 5340 keeps the Interface MTU field in
        # the database description packet, and a pair whose TAP is at 9000
        # against a wire at 1500 was measured stopping short of Full and
        # reaching it the moment the two were made to agree. A protocol that
        # cannot finish an adjacency for a reason only this box can see is the
        # whole argument for this check existing.
        _, quiet6, ignoring6, has_ospf6 = ospf6_state(routing)
        if not has_ospf and not has_ospf6:
            return []
        ignoring = set(ignoring6)
        iface = None
        for line in routing:
            w = line.split()
            if not w:
                continue
            if w[0] == "interface" and len(w) > 1:
                iface = w[1]
            elif iface and w[:3] == ["ip", "ospf", "mtu-ignore"]:
                ignoring.add(iface)
        rc, out = self.dp.run("show lcp")
        if rc != 0:
            return []
        quiet = {i for i, _ in passive} | quiet6
        # Keyed on the interfaces whose MTU is KNOWN, not on every key
        # lcp_pairs returns. That map holds an entry for every name a pair
        # answers to - the dataplane name, the TAP's own name and the Linux
        # one - so walking it named `tap4096` in the warning, which is a name
        # the operator cannot type at this VTY.
        pairs = routes.lcp_pairs(out)
        found = []
        for ifname, here in sorted(mtus.items()):
            # VPP's own end of the pair is an interface too, named tap<n>, and
            # it has an MTU - so walking every interface reported the stall
            # against `tap4096`, a name that is in no configuration anybody
            # writes and that an operator cannot type at this VTY. Skipped by
            # its shape, which is VPP's naming convention for an lcp TAP; if
            # that convention ever changes the warning names the tap again,
            # which is cosmetic rather than a false alarm.
            if _is_lcp_tap(ifname):
                continue
            names = pairs.get(ifname)
            if not names:
                continue
            # Both escape hatches name the interface the way FRR does, which
            # is the LINUX name, while the MTU being read is the dataplane's
            # and keyed by the dataplane name. Compared against every name the
            # pair answers to: matching only the dataplane name meant
            # `passive-interface vppwan0` switched nothing off, and the
            # warning an operator had deliberately silenced came back on the
            # next commit under a name they had never typed.
            if set(names) & (ignoring | quiet):
                continue
            # The Linux end by routes.linux_end(), not by `host != ifname`:
            # on the same-name pair (#389) that asked `ip` about tap0, which
            # Linux has no netdev by, and the stall went unreported on
            # every claimed card with a block.
            host = routes.linux_end(pairs, ifname)
            if not host:
                continue
            r = subprocess.run(["ip", "-o", "link", "show", "dev", host],
                               capture_output=True, text=True)
            if r.returncode != 0:
                continue
            w = r.stdout.split()
            try:
                there = int(w[w.index("mtu") + 1])
            except (ValueError, IndexError):
                continue
            if there != here:
                found.append((ifname, host, here, there))
        return found

    def set_host_mtu(self, ifname, mtu):
        """
        The Linux side of a linux_cp pair. Best effort: a box with no TAP for
        this interface is a legitimate configuration, and failing the commit
        over it would refuse a valid change.

        The Linux end is routes.linux_end()'s to name, not the one in a fixed
        column. The parse this replaced looked for a literal "host" token
        and read the field after it; VPP prints

            itf-pair: [0] host-veth-vpp tap4096 vppwan0 5 type tap

        and there is no such token, so it found nothing - which is
        indistinguishable from the legitimate no-TAP case above, and so was
        never noticed. lcp-sync was mirroring the MTU onto the TAP by itself
        and the lab assertion was crediting this function for VPP's work. The
        lab now turns lcp-sync off before asserting. The reader that replaced
        it tried every name but `ifname`, which on the same-name pair (#389)
        is the TAP alone: the same nothing, found the same way. Asked with
        the dataplane's names, because `interface tap0` reaches here too
        and without them the rule answered the card for the TAP.
        """
        rc, out = self.dp.run("show lcp")
        if rc != 0:
            return
        host = routes.linux_end(routes.lcp_pairs(out), ifname,
                                self.dataplane_names())
        if host:
            subprocess.run(["ip", "link", "set", "dev", host, "mtu", str(mtu)],
                           capture_output=True)

    # The VTY's name for a timeout, and the API field that carries it. The two
    # spellings differ - `tcp-transitory` against `tcp_transitory` - and a map
    # is the honest way to say so rather than a replace() that would silently
    # invent a field name for anything mistyped.
    TIMEOUT_FIELD = {
        "udp": "udp", "icmp": "icmp", "gre": "gre",
        "tcp-syn": "tcp_syn", "tcp-transitory": "tcp_transitory",
        "tcp-established": "tcp_established",
        "port-holddown": "port_holddown", "alg-expect": "alg_expect",
    }
    ZERO_TIMEOUTS = {f: 0 for f in
                     ("udp", "icmp", "tcp_syn", "tcp_transitory",
                      "tcp_established", "gre", "port_holddown",
                      "alg_expect")}

    def global_api(self, **kw):
        """
        One global setting over the binary API.

        A zero timeout field means "leave it alone", so every other field is
        sent as zero and only the one being changed carries a value. That is
        the message's contract, not a convention invented here: zero is not a
        valid timeout, and the settings that CAN legitimately be zero carry an
        explicit flag instead.
        """
        args = {"timeouts": dict(self.ZERO_TIMEOUTS),
                "set_block_holddown": False, "block_holddown": 0,
                "set_session_table": False, "session_buckets": 0,
                "session_memory_mb": 0}
        args.update(kw)
        return self.api_call("bpa_global_set", args)

    def apply_global(self, line):
        w = line.split()
        if len(w) < 2:
            return
        if w[1] == "timeout" and len(w) >= 4:
            field = self.TIMEOUT_FIELD.get(w[2])
            if field is None:
                self.err(f"cgnat timeout {w[2]}: no such timeout")
                return
            # REFUSED AND NOT COERCED, which is #418. `int(w[3]) if
            # is_number(w[3]) else 0` turned `cgnat timeout udp abc` into a
            # zero, and zero in this message is "leave this one alone" - so a
            # typo in a saved file changed nothing, said nothing, and left the
            # operator reading a configuration whose timeout was not the one
            # in force. The prompt has refused it since #273; this is the
            # other door, which nothing was watching.
            #
            # Pool.check_range, not two numbers written again: it is the same
            # u32 field as a pool's own timeout, and the prompt is refused by
            # the same call.
            why = Pool.check_range("timeout", w[3], w[2])
            if why:
                self.err(f"cgnat {why}")
                return
            t = dict(self.ZERO_TIMEOUTS)
            t[field] = int(w[3])
            r = self.global_api(timeouts=t)
            if r.state != vppapi.OK:
                self.err_dataplane(f"cgnat timeout {w[2]} {w[3]}: {r.reason}")
        elif w[1] == "block-holddown" and len(w) >= 3:
            # The same coercion and the same refusal as the timeout above
            # (#418): zero here is the message's "leave this alone", so a word
            # silently left the hold-down as it was.
            why = Pool.check_range("timeout", w[2], "")
            if why:
                self.err(f"cgnat {why.replace('timeout ', 'block-holddown ', 1)}")
                return
            r = self.global_api(set_block_holddown=True,
                                block_holddown=int(w[2]))
            if r.state != vppapi.OK:
                self.err_dataplane(f"cgnat block-holddown {w[2]}: {r.reason}")
        elif w[1] == "logging" and len(w) >= 3:
            self.apply_logging(w)
        elif w[1] == "identity" and len(w) >= 3:
            self.apply_identity(w)
        elif w[1] not in CGNAT_CONFIG:
            # Unreachable from the VTY, which refuses these at entry now. It is
            # here because --load and the binary API reach this function too,
            # and a global that applies nothing must never do it quietly.
            self.err(f"cgnat {w[1]}: no such setting, so it was applied to "
                     f"nothing")
        elif w[1] == "session-table":
            # "session-table buckets <n> memory <MB>", in either order.
            got = {}
            for i in range(2, len(w) - 1, 2):
                if w[i] in ("buckets", "memory") and is_number(w[i + 1]):
                    got[w[i]] = int(w[i + 1])
            if len(got) != 2:
                self.err("usage: session-table buckets <n> memory <MB>")
                return
            r = self.global_api(set_session_table=True,
                                session_buckets=got["buckets"],
                                session_memory_mb=got["memory"])
            if r.state != vppapi.OK:
                # The dataplane refuses this once pools exist, because the
                # tables are allocated with the first pool and a new size
                # would describe a table nobody is using.
                self.err_dataplane(f"cgnat session-table: {r.reason}")

    def apply_logging(self, w):
        """
        The IPFIX export state and its collector, over the binary API.

        Two things were wrong with the `vppctl bpa logging ...` this replaces,
        and the socket was only one of them. The other is that the answer was
        thrown away: the dataplane refuses `bpa logging enable` when no IPFIX
        collector is configured, and an operator who typed it read "commit
        complete" off a box that exports nothing. `vppctl` exits 0 either way,
        so there was nothing for the caller to notice even if it had looked -
        which is the reason vppapi.py exists at all.

        The two halves go in one message rather than two calls: a
        configuration that names a collector AND turns the export on is one
        operator decision, and sending it as two leaves the box between them
        if the second is refused.
        """
        args = {"set_enable": False, "enable": False,
                "set_tcp": False, "tcp_enable": False,
                "collector": "0.0.0.0", "port": 0}
        if w[2] in ("enable", "disable"):
            args["set_enable"] = True
            args["enable"] = w[2] == "enable"
        elif w[2] == "udp" and len(w) >= 4:
            self.apply_logging_udp(w)
            return
        elif w[2] == "syslog" and len(w) >= 4:
            self.apply_logging_syslog(w)
            return
        elif w[2] == "tcp" and len(w) >= 4:
            args["set_tcp"] = True
            if w[3] != "disable":
                args["tcp_enable"] = True
                why = address_error("cgnat logging tcp", w[3])
                if why:
                    self.err(why)
                    return
                args["collector"] = w[3]
                if len(w) >= 6 and w[4] == "port":
                    if not is_number(w[5]) or int(w[5]) > 65535:
                        self.err(f"cgnat logging tcp: port {w[5]} is not one")
                        return
                    # Zero, not 4739, when no port was typed: the default
                    # belongs to the dataplane, which is where the running
                    # configuration is read back from. A default invented
                    # here would be a second opinion about it.
                    args["port"] = int(w[5])
        else:
            # NOT A REFLOW, and that is the decision rather than an omission.
            # The five spellings were written out here in one sentence
            # separated by pipes, and that row reached the console at 217
            # columns (#362). Folded to 79 it becomes four rows of
            # pipe-separated fragments with the alternatives broken across
            # them, which is harder to read than the unfolded row and not
            # easier - so this one needed a SHAPE and not a wrap.
            #
            # The shape is the one the rest of this VTY already uses for
            # alternatives: one spelling per row. And the rows are built from
            # CGNAT_CONFIG, which is the table `?` and TAB answer from and the
            # same move `cgnat <word>` makes in do_config - because a usage
            # line typed out by hand is a second copy of the grammar, and this
            # one had already drifted. It offered `nat cgnat logging udp
            # disable` and `nat cgnat logging tcp disable`; `?` offered
            # neither. One owner now, so the refusal cannot disagree with the
            # completion.
            #
            # The verdict names no word, deliberately. This branch is reached
            # by `cgnat logging sideways` AND by `cgnat logging udp` with
            # nothing after it, and `udp` IS a spelling - a verdict saying
            # otherwise would be wrong on half the boxes that meet it.
            shapes = CGNAT_CONFIG["logging"][1]
            self.err_prose(
                "cgnat logging: not a shape this box takes",
                *[f"logging {word}"
                  + (f" {hint}" if hint else "")
                  for word, (_what, hint) in shapes.items()])
            return
        r = self.api_call("bpa_logging_set", args)
        if r.state != vppapi.OK:
            self.err_dataplane(f"cgnat {' '.join(w[1:])}: {r.reason}")

    def apply_identity(self, w):
        """
        `cgnat identity file <path>` - where the identities come from.

        THE PATH IS CONFIGURATION AND THE FILE IS NOT. The statement carries a
        path, the dataplane reads the file, and `show configuration` gives the
        path back: what is IN the file is personal data, is written by a
        provisioning system rather than by an operator at this prompt, and is
        never printed by anything on this box (#21).

        The dataplane reads it whole or refuses it whole, and its refusal
        names the line - so this VTY does not read the file to check it. A
        second reader would be a second opinion about a file the dataplane is
        about to act on, and the two would disagree the first time one of them
        was changed.
        """
        # THE KIND IS CARRIED AND NOT SNIFFED. A static map and a lease file
        # are two grammars, and a repeated address means opposite things in
        # them - two answers to refuse in one, a renewal to take in the other.
        # A file's first line is a poor thing to bet a court record on.
        if len(w) >= 3 and w[2] == "none":
            args = {"kind": 0, "path": ""}
        elif len(w) >= 4 and w[2] == "file":
            args = {"kind": 0, "path": " ".join(w[3:])}
        elif len(w) >= 4 and w[2] == "dhcp-leases":
            args = {"kind": 1, "path": " ".join(w[3:])}
        elif len(w) >= 3 and w[2] == "radius":
            self.apply_identity_radius(w)
            return
        else:
            self.err_prose(
                "cgnat identity: not a shape this box takes",
                "identity file <path>",
                "identity dhcp-leases <path>",
                "identity none")
            return
        # The path against the field it travels in, before it is sent. A
        # longer one is not something the dataplane can hold, and the refusal
        # belongs where the operator can retype it (#416).
        if args["path"]:
            what = ("cgnat identity dhcp-leases" if args["kind"]
                    else "cgnat identity file")
            why = text_error(what, args["path"])
            if why:
                self.err_prose(*why)
                return
        r = self.api_call("bpa_identity_source_set", args)
        if r.state != vppapi.OK:
            self.err_dataplane(f"cgnat identity: {r.reason}")

    def apply_identity_radius(self, w):
        """
        `cgnat identity radius secret <s> [port <n>]`, and `radius disable`.

        THE SECRET IS THIS VTY'S TO KEEP. Everything else about the CGNAT
        running configuration is read back from the dataplane, which is #209's
        rule and the reason there is one owner of it - but a secret read back
        that way would be printed by `show configuration` on every screen, and
        a secret on a screen shared during an incident has left the building.
        So the dataplane gives back the port and that the listener is on, the
        candidate carries the statement with the secret, the saved file keeps
        it - a box has to be able to reload - and `redact` blanks it wherever
        it is displayed. That is exactly how an SNMP community is treated.

        Without a secret the statement is refused here as it is in the
        dataplane, and for the same reason: a listener that checks nothing
        would let anybody who can reach the port name any subscriber on any
        address.
        """
        if len(w) >= 4 and w[3] == "disable":
            r = self.api_call("bpa_identity_radius_set",
                              {"enable": False, "port": 0, "secret": ""})
            if r.state != vppapi.OK:
                self.err_dataplane(f"cgnat identity radius: {r.reason}")
            return

        secret, port = None, 0
        rest = w[3:]
        i = 0
        while i < len(rest):
            word = rest[i]
            value = rest[i + 1] if i + 1 < len(rest) else None
            if word not in ("secret", "port") or value is None:
                self.err_prose(
                    "cgnat identity radius: not an option of this statement",
                    f"'{word}' is not one, or it is missing its value",
                    "the options are: secret <s>, port <n>")
                return
            if word == "port":
                if not is_number(value) or int(value) > 65535:
                    self.err("cgnat identity radius: port is 0-65535")
                    return
                port = int(value)
            else:
                secret = value
            i += 2

        if secret:
            why = text_error("cgnat identity radius secret", secret)
            if why:
                self.err_prose(*why)
                return
        if not secret:
            self.err_prose(
                "cgnat identity radius: a shared secret is required",
                "without one this box cannot tell an accounting packet from "
                "anybody's packet",
                "it would take dictation about who is on which address, and "
                "that goes in the compliance log")
            return

        r = self.api_call("bpa_identity_radius_set",
                          {"enable": True, "port": port, "secret": secret})
        if r.state != vppapi.OK:
            self.err_dataplane(f"cgnat identity radius: {r.reason}")

    def apply_logging_syslog(self, w):
        """
        Where the syslog export goes, and under whose enterprise number.

        A MESSAGE OF ITS OWN, like the statement: a syslog collector is a
        second destination and not another flag on the IPFIX one, and a box
        can have both - the provider's collector speaking syslog and this
        product's own tooling reading IPFIX.

        THE ENTERPRISE NUMBER IS REFUSED HERE AND AT THE DATAPLANE, and both
        refusals say the same thing for the same reason: RFC 5424 allows a
        structured-data name without an `@enterprise` suffix only for names
        IANA has registered. This one is refused at the prompt, where an
        operator can act on it, rather than in a year of lines a collector
        misparsed.
        """
        args = {"enable": False, "collector": "0.0.0.0", "port": 0,
                "enterprise": 0, "facility": 16, "severity": 6}
        if w[3] == "disable":
            r = self.api_call("bpa_logging_syslog_set", args)
            if r.state != vppapi.OK:
                # The dataplane's sentence LAST on its row and nothing of this
                # VTY's behind it: it is as long as the dataplane makes it,
                # and a fold inside this box's own words is the one #363 is
                # about.
                self.err_dataplane(f"cgnat logging syslog: {r.reason}")
            return

        why = address_error("cgnat logging syslog", w[3])
        if why:
            self.err(why)
            return
        args["enable"] = True
        args["collector"] = w[3]
        rest = w[4:]
        i = 0
        while i < len(rest):
            word = rest[i]
            value = rest[i + 1] if i + 1 < len(rest) else None
            if word not in SYSLOG_OPTIONS:
                self.err_prose(
                    "cgnat logging syslog: not an option of this statement",
                    f"'{word}' is not one, or it is missing its value",
                    "the options are: " + ", ".join(SYSLOG_OPTIONS))
                return
            if value is None or not is_number(value):
                self.err_prose(
                    f"cgnat logging syslog: {word} takes a number",
                    f"'{value if value is not None else ''}' is not one")
                return
            args[word] = int(value)
            i += 2

        bounds = {"port": 65535, "enterprise": 4294967295,
                  "facility": 23, "severity": 7}
        for word, top in bounds.items():
            if args[word] > top:
                self.err(f"cgnat logging syslog: {word} is 0-{top}")
                return
        if not args["enterprise"]:
            # The word is the operator's to find, not this box's to invent,
            # so the verdict says what is missing and the prose says where
            # the number comes from.
            self.err_prose(
                "cgnat logging syslog: an enterprise number is required",
                "RFC 5424 reserves a structured-data name without an "
                "'@enterprise' suffix for names IANA has registered, and this "
                "product has none",
                "use your own IANA Private Enterprise Number: every line this "
                "box writes carries it, and a collector keys on it")
            return

        r = self.api_call("bpa_logging_syslog_set", args)
        if r.state != vppapi.OK:
            self.err_dataplane(f"cgnat logging syslog: {r.reason}")

    def apply_logging_udp(self, w):
        """
        Where the UDP export goes: VPP's own IPFIX exporter.

        `cgnat logging enable` hands the block events to VPP's flow-report
        infrastructure, which refuses to start a report while that exporter
        has no collector. Nothing in this product ever configured one and no
        statement reached it, so on every shipped box UDP export was refused -
        permanently, and in dynamic mode the allocation log IS the connection
        record the Marco Civil requires (#209).

        This goes to VPP's OWN `set_ipfix_exporter` message and not onto
        `bpa_logging_set`, which carries the other two halves. The exporter is
        core state shared with anything else that exports, and libvnet exports
        no setter a plugin could call - `nm -D` lists
        vnet_flow_report_add_del, vnet_flow_reports_reset and
        vnet_ipfix_exporter_lookup and nothing that sets a collector. Putting
        it on the plugin's message would mean copying the core handler's field
        assignments into the plugin, and that copy would drift with every VPP
        bump. It is still the binary API, which is what #76 asks for; the
        plugin READS the exporter back in `show bpa config`, because that is
        the one place the CGNAT running configuration is produced.

        Everything the statement does not name - path MTU, template interval,
        VRF, UDP checksum - is sent back as the exporter already has it, read
        with ipfix_exporter_dump, so this statement changes exactly what it
        names. Where the box has nothing at all, IPFIX_PATH_MTU and its
        neighbours fill in; the comment on them says why there is no way to
        avoid that.
        """
        # BEFORE THE DATAPLANE IS ASKED ANYTHING. A word that is not an
        # address is wrong whatever the box answers, and reading the exporter
        # first meant an operator who mistyped the collector was told the
        # dataplane has no IPFIX exporter - a true sentence about the wrong
        # thing, and one they cannot act on.
        if len(w) > 3 and w[3] != "disable":
            checks = [(w[3], "cgnat logging udp")]
            if len(w) > 5 and w[4] == "src":
                checks.append((w[5], "cgnat logging udp src"))
            for value, what in checks:
                why = address_error(what, value)
                if why:
                    self.err(why)
                    return

        r = self.api.dump("ipfix_exporter_dump", {})
        if r.state != vppapi.OK:
            self.err_dataplane(f"cgnat {' '.join(w[1:])}: cannot read the IPFIX "
                               f"exporter: {r.reason}")
            return
        rows = r.fields.get("rows") or []
        if not rows:
            # Exporter 0 exists on every VPP whether or not it has a
            # destination, so an empty dump is a dataplane this VTY does not
            # recognise - and guessing the fields it did not report would put
            # this VTY's idea of VPP's defaults onto the box.
            self.err(f"cgnat {' '.join(w[1:])}: the dataplane reports no "
                     "IPFIX exporter at all")
            return
        cur = rows[0]

        off = w[3] == "disable"
        src = cur["src_address"]
        collector = "0.0.0.0" if off else w[3]
        port = int(cur["collector_port"]) or IPFIX_COLLECTOR_PORT

        if not off:
            if len(w) < 6 or w[4] != "src":
                self.err("usage: logging udp <collector> src <source> "
                         "[port <n>]")
                return
            src = w[5]
            if len(w) >= 8 and w[6] == "port":
                if not is_number(w[7]) or int(w[7]) > 65535:
                    self.err(f"cgnat logging udp: port {w[7]} is not one")
                    return
                port = int(w[7])
        elif src == "0.0.0.0":
            # Nothing to turn off, and saying so beats sending a message the
            # dataplane refuses: VPP rejects a source address of 0.0.0.0 even
            # when the collector being set is 0.0.0.0 too - measured, retval
            # -7 - so "clear an exporter that was never set" has no encoding.
            return

        args = {"collector_address": collector,
                "collector_port": port,
                # Kept, not cleared. VPP refuses src 0.0.0.0 outright, so the
                # only way to say "no collector" is to name a collector of
                # 0.0.0.0 and leave the source where it was.
                "src_address": src,
                "vrf_id": int(cur["vrf_id"]),
                "path_mtu": int(cur["path_mtu"]) or IPFIX_PATH_MTU,
                "template_interval": (int(cur["template_interval"])
                                      or IPFIX_TEMPLATE_INTERVAL),
                "udp_checksum": bool(cur["udp_checksum"])}
        rep = self.api_call("set_ipfix_exporter", args)
        if rep.state != vppapi.OK:
            self.err_dataplane(f"cgnat {' '.join(w[1:])}: {rep.reason}")

    #: Terminators, which are structure rather than configuration.
    #:
    #: Block OPENERS are excluded too, by opens_frr_block(), and they are the
    #: half that was measured rather than foreseen. `interface loop0` and
    #: `interface Gi0/1` are staged in the routing half when a loopback is
    #: created or an MTU is set, while everything INSIDE them is applied by
    #: this VTY to the dataplane - so those commits add a block header and
    #: nothing else. FRR does not store an empty interface stanza, the header
    #: was therefore still outstanding afterwards, and "nothing took" fired on
    #: two perfectly ordinary operations: creating a loopback, and setting an
    #: MTU. Both labs caught it before this shipped.
    NOT_A_STATEMENT = ("exit", "exit-address-family", "exit-vrf", "end", "!")

    def routing_added(self, before, after):
        """The statements this commit introduces, stripped and deduplicated."""
        had = {l.strip() for l in before}
        out = []
        for line in after:
            s = line.strip()
            if (not s or s in self.NOT_A_STATEMENT or s.startswith("!")
                    or opens_frr_block(s)):
                continue
            if s in had or s in out:
                continue
            out.append(s)
        return out

    def nothing_took(self, added):
        """
        Did the apply change nothing FRR can see, although it asked for changes?

        The narrow half of #197, and narrow on purpose. The general question -
        "is every statement of this candidate in effect" - CANNOT be answered
        with this tooling, and that was measured rather than assumed: a
        candidate carrying `timers throttle spf 0 50 5000` applies cleanly, FRR
        declines to store it because it equals the default, and frr-reload then
        reports it under "Lines To Add" for ever. A check built on that would
        call every commit containing such a statement a failure.

        What IS decidable without a single false positive from defaults is the
        worst case rather than the general one: EVERY statement this commit
        introduced is still outstanding afterwards, so nothing took at all.
        That is the failure #197 was filed for - frr-reload exiting 0 having
        applied nothing, and the box printing "commit complete" over it - and a
        partial failure, nine statements of ten, does not trigger it.

        The residual false positive this used to carry is gone. It was a
        commit whose new statements are ALL ones FRR does not read back, and
        it was written off as rare - `lab/igp.sh` then committed three of them
        together and the box refused a commit that had worked perfectly. So
        the statements FRR is MEASURED not to keep are dropped from the
        question before it is asked: frr-reload reports them under "Lines To
        Add" for ever, and counting a statement as outstanding because FRR
        stores it under another name, or does not store its own default, is
        the box blaming the operator for FRR's bookkeeping.

        Returns the outstanding statements when nothing took, and nothing
        otherwise - including when frr-reload cannot be asked, because a check
        that cannot run must not become a failure of the commit it was checking.
        """
        # Only the statements whose absence would MEAN something. A rewritten
        # or omitted statement is outstanding in frr-reload's ledger whatever
        # the box does, so leaving it in makes "all of them are outstanding"
        # true for a commit that worked.
        added = [a for a in added
                 if frr_keeps(a, self.candidate.routing)
                 and frr_writes_instead(a) is None]
        if not added:
            return []
        rc, out = self.frr.reload(self.candidate.routing, check_only=True)
        if rc:
            return []
        left = set(self.frr.outstanding(out))
        still = [a for a in added if a in left]
        return still if len(still) == len(added) else []

    def report_remap(self):
        """
        What `commit` will refuse until it is asked by name, said by `check`.

        `commit check`'s own help has always promised this - "report which
        subscribers a change would re-map" - and six pages of the operator
        wiki repeated it. It printed nothing. The numbers were there: they are
        filled by the same apply(check_only=True) that `check` runs, and were
        only ever printed inside the plain `commit` refusal, which `check`
        returns before reaching.

        The gap is the worst possible shape for this particular fact. An
        operator runs `check` in a maintenance window to find out what the
        commit will do BEFORE doing it, and the re-mapping is the most
        consequential thing a commit on this box can do: in deterministic mode
        one added prefix moves every subscriber and ends every session on the
        pool, and the box reports nothing unusual afterwards because nothing
        unusual happened (#23). "configuration check succeeds" over that is
        the sentence the operator acts on.

        Not a refusal here, and the wording has to keep that clear: `check`
        applies nothing, so there is nothing to refuse. It is the warning the
        command exists to give.
        """
        if not self.remap:
            return
        print_prose("this change RE-MAPS subscribers that already have "
                    "mappings", indent="  ")
        for line in self.remap:
            # Each row is one sentence of the plugin's, folded to the console
            # the way every other paragraph here is (#391). The counts and
            # addresses on it are what the operator compares against what
            # they expected, and console_prose never breaks inside a word,
            # so an address or a port range lands whole on one row and can
            # still be grepped for; what folds is the sentence around them,
            # which was 178 columns on one row before this and lost its
            # shape at whatever width the window happened to be.
            print_prose(line[len("remap:"):].strip(), indent="    ")
        print_prose("'commit' refuses this until it is asked for by name, "
                    "with 'commit remap'", indent="  ")

    def report_divergence(self):
        """
        What `commit` will refuse over, said by `check` - the same debt
        `report_remap` was paying: a check that applies nothing has nothing to
        refuse, so it warns, and an operator who runs `check` in a maintenance
        window to find out what the commit will do is told the one thing that
        makes the pair dangerous rather than being told at the commit.
        """
        if not self.diverge:
            return
        print_prose("this change maps subscribers differently from the peer "
                    "this box stands for a service address with", indent="  ")
        self.say_divergence("warning", self.diverge)
        print_prose("'commit' refuses this until it is asked for by name, "
                    "with 'commit diverge'", indent="  ")

    @sigint_ignored
    def do_commit(self, args):
        """commit [check | remap | cutoff | confirmed <min> | comment <text>]"""
        if self.candidate is None:
            self.err("no candidate configuration")
            return

        # THE REFUSAL load() MAKES ABOUT A FILE, on the batch that is this
        # box's other way of being handed a configuration. feed_batch()
        # carries the argument; what is said here is what was NOT applied,
        # because whoever reads it is holding a script that has just not done
        # what it says. Before running_config(), which reads the dataplane: a
        # refusal this certain has no reason to ask the box anything.
        #
        # `commit check` is refused by it too. It answers "would this commit
        # work", and "configuration check succeeds" about a candidate the
        # batch did not ask for is the same false sentence one step earlier.
        if self.batch_refused:
            self.err(f"refusing to commit: {len(self.batch_refused)} "
                     f"statement(s) in this batch were refused")
            for text in self.batch_refused:
                # The statement whole on its own row, as the unknown lines
                # below are quoted whole: it is what the operator goes and
                # edits (#271).
                print(f"    {text}")
            print_prose("NOTHING was applied and this box is unchanged: the "
                        "candidate this batch built is not the configuration "
                        "it asked for")
            print_prose("each refusal is printed above, where it happened; "
                        "fix the statements and run the batch again")
            return

        running = self.running_config()

        # Refused, not warned about. A candidate carrying a line nobody can
        # apply would be committed as though it had been, and the operator
        # would find out from the consequence.
        if self.candidate.unknown or running.unknown:
            self.err("refusing to commit: there are lines this VTY does not "
                     "understand")
            for lineno, text, why in (self.candidate.unknown
                                      + running.unknown):
                # The line WHOLE, and the reason wrapped under it. They used
                # to share one row, which put prose and a quoted line under
                # the same budget: `password <hash>` plus its reason measured
                # 133 columns (#271). The line is what the operator goes and
                # edits, so it keeps the whole row and the reason is prose.
                print(f"    line {lineno}: {text}")
                print_prose(f"({why})", indent="        ")
            return

        if args and args[0] == "check":
            if self.apply(self.candidate, check_only=True):
                # Say what was checked, not "everything is fine".
                #
                # Routing statements are validated by `frr-reload.py --test`,
                # which computes the delta and prints what it WOULD apply
                # without handing the statements to FRR - so it exits 0 on a
                # statement FRR itself refuses. Measured on `ge 24 le 16`,
                # which passed the check and failed the commit (#158). That
                # one is now refused here rather than by FRR, because FRR's
                # refusal costs the operator the next commit too (#160) - but
                # the limit itself stands for every statement the VTY passes
                # through and does not model.
                #
                # The check is not going to apply-and-roll-back to find out:
                # `check` must not touch the box, and a lab asserts it issues
                # read-only commands only. So it reports the limit instead,
                # because "configuration check succeeds" claimed more than it
                # had verified, and an operator in a maintenance window acts
                # on that sentence.
                if self.real_routing(self.candidate.routing):
                    print("configuration check succeeds for CGNAT and the "
                          "box's own configuration")
                    # HERE and not on `commit`, and the difference is the
                    # rule from #205 and #208: an alarm that is always on is
                    # one nobody reads. Passing statements through is the
                    # NORMAL case for FRR configuration - a working BGP edge
                    # has `no bgp ebgp-requires-policy` in it and this VTY
                    # models no `no` inside a block at all - so a line on
                    # every commit would be skipped within a week, and the
                    # habit of skipping the end of a commit takes the
                    # "FRR kept none of these statements" warning with it.
                    #
                    # `commit check` is the opposite kind of moment. It is a
                    # question the operator chose to ask, and the whole
                    # question is HOW MUCH was verified - it already answered
                    # it with "structure only", which is a sentence that
                    # names no number. This one names the number.
                    _, unread = mark_routing(self.candidate.routing)
                    if unread:
                        print_prose(f"{unread} routing statement(s) have no "
                                    "grammar in this VTY and were not read "
                                    "at all", indent="  ")
                        print_prose("'show configuration' marks them; they "
                                    "go to FRR exactly as typed")
                    print_prose("routing statements are checked for structure "
                                "only; FRR validates them at commit, and can "
                                "still refuse one", indent="  ")
                else:
                    print("configuration check succeeds")
                self.report_remap()
                self.report_divergence()
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
        # Each is a prefix rather than the whole word, so they combine with
        # comment and confirmed - and with each other, in either order: an
        # operator who has to acknowledge a re-mapping should not lose the
        # ability to record why, and a change that does both has two
        # consequences to acknowledge rather than one.
        #
        # `cutoff` is a word of its own and not `remap` because they are
        # different consequences: one moves the subscribers, the other costs
        # the operator the session they are typing in, and somebody who has
        # acknowledged one has not been told about the other.
        remap_ok = cutoff_ok = diverge_ok = False
        while args and args[0] in ("remap", "cutoff", "diverge"):
            if args[0] == "diverge":
                diverge_ok = True
            if args[0] == "remap":
                remap_ok = True
            else:
                cutoff_ok = True
            args = args[1:]

        # THIS is the confirmation the message has always talked about, and
        # until now there was no such thing: `commit` with nothing left to
        # change returned at "no changes to commit" below with the deadline
        # still armed, and no other path cleared it. The box reverted a change
        # the operator had confirmed, and confirming one was impossible (#212).
        #
        # Any commit confirms, not only an empty one - an operator who answers
        # "the session still works" by making the next change has answered the
        # question. `commit confirmed` does not: that is a new window, and it
        # re-arms below. `commit check` never reaches here.
        confirmed = None
        if args[:1] != ["confirmed"]:
            confirmed = self.confirm.read()
        if confirmed:
            when = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                 time.gmtime(confirmed["deadline"]))
            try:
                self.confirm.clear()
                self.confirm_deadline = None
                self.confirm_backup = None
                self.confirm_on_box = False
                print("confirmed: the box will not roll this change back")
            except OSError as e:
                # Said as a failure, because it is one. The window is still
                # armed and the box will still revert at its deadline, and an
                # operator told "confirmed" over that loses the change hours
                # later with nothing on the screen that predicted it.
                self.err(f"cannot confirm: {self.confirm.path}: {e.strerror}")
                print_prose(f"the box still rolls back at {when}; "
                            "'rollback 0' and commit again, or remove that "
                            "file")

        if running.is_same(self.candidate):
            if not confirmed:
                print("no changes to commit")
            return

        # Validated ONCE. This used to run here to discover re-mappings and
        # again below to decide whether to apply, and apply() prints what it
        # refuses - so every validation error reached the operator twice, which
        # reads like two different problems.
        if not self.apply(self.candidate, check_only=True):
            self.err("candidate did not pass validation; nothing applied")
            return

        if not remap_ok:
            if self.remap:
                self.err("this change re-maps subscribers that already have "
                         "mappings")
                for line in self.remap:
                    # Folded as report_remap folds the same rows (#391).
                    print_prose(line[len("remap:"):].strip(), indent="    ")
                print("    if that is what you mean, type: commit remap")
                return
            self.remap = []

        # After the re-map gate and before the cut-off one, which is the
        # order of consequence: a re-map moves subscribers on THIS box now, a
        # divergence moves them on the OTHER box when it takes over, and a
        # cut-off takes away the session the operator is typing on.
        if not diverge_ok and self.diverge:
            self.say_divergence_refusal()
            return

        if not cutoff_ok and self.cutoff:
            for pfx, why, advice in self.cutoff:
                # The advice THROUGH print_prose. Each line is a sentence
                # written to be read once, and printed as it stood the longest
                # of them reached the console at 144 columns (#363).
                self.err(f"{pfx}: {why}")
                for line in advice:
                    print_prose(line)
            return

        confirm_min = 0
        comment = ""
        # A change to SSH or to accounts is the one change that can cost the
        # operator the session they are typing it in. It is therefore applied
        # as "commit confirmed" whether or not they asked, and the VTY says so
        # - choosing it silently would be a surprise in the other direction.
        d0 = running.delta_to(self.candidate)
        # And the change that withdraws the prefix carrying this session is
        # the same class of danger by a different route: the operator has said
        # the word, and the word does not give them a way back. The rollback
        # timer is the way back, and it has to be armed before the session it
        # is protecting goes away.
        touches_access = (d0["ssh_changed"] or d0["users_changed"]
                          or (cutoff_ok and self.cutoff))
        if args and args[0] == "confirmed":
            try:
                confirm_min = int(args[1]) if len(args) > 1 else 10
            except (IndexError, ValueError):
                self.err("usage: commit confirmed <minutes>")
                return
            # BOUNDED, because the two ends fail in opposite directions and
            # both were reachable (#312). `?` and the operator documentation
            # have always said <1-60>; nothing enforced it.
            #
            # Above the range: `commit confirmed 600` arms ten hours. The
            # window exists so a box that lost its operator puts itself back
            # while somebody is still at the console; ten hours is a box that
            # reverts in the middle of the night, long after whoever made the
            # change has gone home and concluded it held.
            #
            # At zero: 0 is falsy, so `if confirm_min:` below never armed
            # anything. Nothing was written, no deadline was printed, and the
            # box answered "commit complete" - an operator who asked for the
            # protection, was told the commit had gone through, and had none.
            # That is the worst of the three, because it is the one that looks
            # like it worked.
            #
            # Below zero: a deadline already in the past.
            if not 1 <= confirm_min <= 60:
                # THE VERDICT AND THE REASON, split. Composed as one
                # sentence this reached the console at 136 to 139 columns -
                # the words alone are 136 of it, so no value could have made
                # it fit - and #359's walk could not see it because the
                # f-string interpolates the number (#363).
                self.err(f"commit confirmed {confirm_min}: expected 1-60 "
                         f"minutes")
                print_prose("the window has to outlast a reconnection and "
                            "end while somebody is still at the console")
                return
        elif args and args[0] == "comment":
            comment = " ".join(args[1:]).strip('"')

        # THE HANDOVER NO LONGER ARMS A WINDOW, and the reason is that the
        # window stopped undoing anything (#378).
        #
        # It was armed because the box did not lose the session at the commit
        # but at the restart after it, and a handover nobody came back to
        # confirm put the card back and rebooted (#303). Since the box claims
        # its own card, the state that revert produced - PRISMA_DATAPLANE_IF
        # empty, on a one-card box - is the state that CLAIMS the card. Both
        # measured: the revert leaves the setting empty and reboots, and the
        # tuner on that exact file answers `dev 0000:00:09.0`.
        #
        # So an operator who confirmed and one who did nothing ended in the
        # same place, and the second paid a reboot for it. That is worse than
        # a no-op, and the screen was wrong twice: it said the card goes back
        # to Linux, and it implied that not typing `commit` would lose it.
        # The card stays either way.
        #
        # What the window was FOR is done earlier and without a reboot now:
        # prisma-routing-tune computes the address, MAC and gateway off the live
        # card on the boot that claims it and this VTY puts them back, and
        # appliance/give-the-card-back hands the card to Linux when the
        # dataplane will not start at all (#379).
        if touches_access and not confirm_min:
            confirm_min = 10
            # Verdict, then why. As one sentence it measured 133 columns
            # and nothing was wrapping it (#271).
            print("this commit can cut off the session you are using")
            print_prose("it changes SSH or accounts, or stops advertising "
                        "the prefix this session runs over")
            print_prose("applying it as 'commit confirmed 10' rather than as "
                        "a plain commit")
            # What the revert IS gets said once, below, after the box has
            # found out whether it could arm one. Promising it here and
            # discovering afterwards that the store could not be written is
            # how this VTY came to describe a guarantee it did not provide
            # (#212).

        backup = running.text()
        if not self.apply(self.candidate):
            self.err("commit failed; rolling back to the previous state")
            self.apply(Config.parse(backup))
            return

        try:
            self.rb.push(self.candidate.text(), comment)
        except RollbackUnavailable as e:
            # The reason carries a path, so it goes on a row of its own
            # rather than on the headline: together they measured 118
            # columns and `err` wraps nothing (#271).
            self.err("the change is applied, but no rollback revision could "
                     "be recorded")
            print(f"    {e}")
            print_prose("'rollback' will not be able to undo this commit")
        added = self.routing_added(running.routing, self.candidate.routing)
        # The GENERAL check (#197), which is now possible: read FRR's own
        # running configuration back and see which of the statements this
        # commit introduced are not in it.
        #
        # `nothing_took` below stays, and the two are not redundant. This one
        # names the statements FRR dropped; that one catches the case where
        # frr-reload could not be asked at all, and it fires on a partial
        # answer this check might explain away. Neither subsumes the other, and
        # a commit that fails both says so once, because this one returns first.
        dropped, stalled = [], []
        if added:
            dropped = statements_frr_dropped(added, self.frr.running())
            # Asked once. It shells out to frr-reload, which is the slowest
            # thing on the commit path, and calling it in the condition and
            # again in the body doubles that for every commit that hits it.
            if not dropped:
                stalled = self.nothing_took(added)
        if dropped:
            # REFUSED, not warned about. This shipped as a warning first, and
            # what promoted it was the condition written here rather than
            # anybody's confidence: the full lab suite run clean with the
            # refusal in force. That reading, rather than a pointer to it:
            #
            #   pass=79  fail=0  notmeasured=0  novpp=0  ran=0
            #
            #   79 runs over 57 lab files, 2026-09-13, with this refusal in
            #   force.
            #
            # Every lab that commits a routing statement is in that number -
            # routing, igp, vty, bgp-policy, commit, frr-readback, lcp,
            # in-place - and not one of them lost a `commit complete` it used
            # to have.
            #
            # It had to be a warning to begin with, because the list of
            # statements FRR does not read back verbatim was demonstrably
            # incomplete: five encounters with real configuration produced four
            # false positives, and each one extended the table.
            #
            #   lab/igp.sh      three OSPF defaults and a passive-interface
            #                   rewrite - and nothing_took fired on them too
            #   lab/routing.sh  `neighbor <x> activate`, omitted only while
            #                   `bgp default ipv4-unicast` is on
            #   lab/igp.sh      three IS-IS defaults nobody had probed
            #   lab/bgp-policy  a `seq` FRR inserts, on a policy that WORKED:
            #                   the route it names was filtered and the box
            #                   still said the statement had been thrown away
            #
            # Refusing a correct commit is the exact harm #208 was filed for,
            # so the evidence that the table is now complete enough had to be a
            # whole suite of real configuration passing with the refusal armed,
            # not a reading of the table. A single false refusal here makes
            # `commit` untrustworthy in the opposite direction, which is worse
            # than the warning was.
            #
            # It does NOT roll back, and the precedent is two branches down:
            # "the change is applied, but no rollback revision could be
            # recorded". The CGNAT half and the box's own configuration really
            # are applied and really are in effect. What is refused is the
            # SENTENCE `commit complete`, which is the one an operator builds
            # every later diagnosis on.
            #
            # THE STATEMENTS CARRY THE `<- not checked` MARKER (#227), and
            # this is the one message outside the display path that does.
            #
            # The objection, and it is a real one: this refusal answers "FRR
            # did not keep this", whether the VTY had read it is a different
            # question, and a message that answers two questions at once stops
            # being read. It is one question. The refusal names no cause at
            # all - the branch below names one, "a daemon that is not running",
            # and this branch has never named any - and whether the box read
            # the statement is the only part of the cause the box holds:
            #
            #   marked      the words went to FRR verbatim and nothing here
            #               read them, so `% Unknown command` is live and the
            #               statement itself is where to look first
            #   not marked  the keywords came out of this VTY's own grammar,
            #               so the cause is on FRR's side
            #
            # Those are different first moves, and without the marker the two
            # refusals are the same five lines.
            #
            # The other objection is the standing one from #205 and #208,
            # which #141 measured against this very marker: four of the
            # twenty-five statements a working provider edge reads back are
            # marked, one of them `no bgp ebgp-requires-policy`, and a marker
            # that fires on a line every box has is a marker operators learn
            # to skip. MEASURED, and it does not reach here: that whole
            # twenty-five-statement read-back committed against an FRR holding
            # it prints `commit complete` and nothing else. Every one of the
            # sixteen statements it introduces CAN produce this refusal, and
            # three of them are unmarked - but only when FRR really has
            # dropped them, which is already a failed commit. A marker inside
            # a message that does not print on a working box cannot become the
            # alarm that is always on. That is what separates this from the
            # decision #141 made for `commit`, which prints on every commit
            # and therefore says nothing about the boundary.
            #
            # NOT the count `unchecked_note()` gives the display. The display
            # shows every statement, so a count there is a number about
            # something the reader can see; this list stops at five. "1 of
            # these has no grammar" over five printed statements and three
            # withheld is a number the operator cannot resolve, and resolving
            # it means running `show configuration` to look for the marks -
            # the round trip the marker is here to save.
            #
            # And NOT the branch below. There, nothing at all took, the cause
            # is measured to be a daemon rather than the statements, and a
            # marker on the statements would read as the cause when it is not
            # - which is the failure this objection warns about, in the one
            # place where it would really happen.
            unread = unchecked_in(self.candidate.routing)
            self.err(f"FRR accepted {len(dropped)} statement(s) of this "
                     "commit and did not keep them")
            shown = dropped[:5]
            for stmt, instead in shown:
                # Both halves, and they answer different questions. #251 names
                # the block the statement is in, because a VRF static route has
                # no top-level spelling to go and look for. #227 marks the ones
                # this VTY never read, because those are the ones FRR is most
                # likely to have declined and the operator has least context
                # for.
                mark = UNCHECKED_MARK if stmt in unread else ""
                # QUOTED, so printed whole: not wrapped and not truncated,
                # whatever it measures. The argument is in `console_prose`,
                # and the short of it is that this is the one line on the
                # screen the operator will grep for or type back in. The
                # prose around it is wrapped instead, and the width assertion
                # covers the prose.
                print("    " + as_hierarchy(
                    stmt, block_of(self.candidate.routing, stmt)) + mark)
                if instead:
                    print_prose(f"FRR writes '{instead}' for this, and that "
                                "is not there either", indent="      ")
            if len(dropped) > 5:
                print(f"    ... and {len(dropped) - 5} more")
            if any(stmt in unread for stmt, _ in shown):
                print_prose(f"a statement marked '{UNCHECKED_MARK.strip()}' "
                            "has no grammar in this VTY: it went to FRR word "
                            "for word, so this box never read it either")
            print_prose("this commit is NOT in effect as written, and nothing "
                        "was rolled back: the rest of it is applied. "
                        "'rollback 1' puts the box back")
            print_prose("the configuration this box holds and the one FRR is "
                        "running are not the same; 'show configuration' is "
                        "this box's, 'start shell' then 'vtysh -c \"show "
                        "running-config\"' is FRR's")
        elif stalled:
            # NOT "commit complete". The CGNAT half and the box's own
            # configuration are applied - this is not a rollback - but the
            # routing half reached nothing, and saying "complete" over that is
            # the one sentence an operator builds every later diagnosis on.
            # The count is on the second line rather than in the headline, and
            # the reason is the width budget rather than taste: with the count
            # appended the headline measured 116 columns, and `err` prints one
            # line with no wrap of its own (#252). What the headline has to
            # carry is the verdict, which is that nothing took.
            self.err("the routing half of this commit reached FRR and changed "
                     "nothing")
            print_prose(f"all {len(stalled)} new statement(s) are still "
                        "outstanding")
            for line in stalled[:5]:
                # Quoted whole, for the reason in `console_prose`.
                print("    " + as_hierarchy(
                    line, block_of(self.candidate.routing, line)))
            if len(stalled) > 5:
                print(f"    ... and {len(stalled) - 5} more")
            print_prose("the usual cause is a daemon that is not running; "
                        "'show version' says which are")
            print_prose("if these statements are all at FRR's own default "
                        "values, FRR does not store them and this is "
                        "expected")
        else:
            print("commit complete")

        if confirm_min:
            self.arm_confirm(confirm_min, backup)

    def arm_confirm(self, minutes, backup):
        """
        Arm the revert, on the box and in this session, and say which.

        The point of "commit confirmed" is surviving a change that cuts your
        own access, and the session is exactly what that change takes away -
        so the deadline that counts is the one on disk. This session keeps a
        copy for two reasons and neither of them is the promise: it makes the
        revert punctual, firing at the deadline rather than at the next tick
        of the unit that watches the file, and it is the whole mechanism
        anywhere there is no systemd to run that unit - which is every lab
        container and every developer's tree.

        Whichever fires first clears the file, so the other finds nothing to
        do.
        """
        deadline = time.time() + minutes * 60
        when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(deadline))
        self.confirm_deadline = deadline
        self.confirm_backup = backup
        self.confirm_on_box = False
        try:
            self.confirm.arm(deadline, backup, minutes)
            self.confirm_on_box = True
        except OSError as e:
            why = f"{e.filename or self.confirm.path}: {e.strerror}"
        if self.confirm_on_box:
            # The deadline is the headline; the rest is prose, and it used to
            # be wrapped by hand at 86 and 81 columns (#271).
            print(f"rollback at {when}, in {minutes} minute(s)")
            print_prose("unless you type 'commit' to confirm")
            print_prose("the revert is armed on the box, not in this "
                        "session: logging out, losing this session or "
                        "rebooting does not cancel it, and confirming does")
        else:
            # A weaker promise, stated. The revert still happens for an
            # operator who stays and waits, and that is all it does - so this
            # says so rather than repeating the sentence above, which would be
            # the defect #212 is about with a different cause.
            # Also a warning rather than an error, and for the same reason
            # as `save` above: the commit is applied. What failed is the
            # promise about undoing it, which is what the three lines below
            # are for.
            print("warning: the revert could NOT be armed on the box")
            # The path and the reason, whole on their own row: a path is
            # something the operator goes and looks at (#252).
            print(f"    {why}")
            print_prose(f"it will run in {minutes} minute(s) IF THIS SESSION "
                        "LASTS THAT LONG, and not otherwise")
            print_prose("which is not the case it exists for: keep a second "
                        "session open, and use 'rollback 1' there if this "
                        "one stops answering")

    def revert_confirm(self, pending, why):
        """
        Put back the configuration a "commit confirmed" was measured against.

        Returns True when the box is back on it. The caller says what to do
        about False, because the two callers are in different situations: an
        operator is reading one of them and a unit firing every few seconds is
        the other.
        """
        target = Config.parse(pending["config"])
        if target.unknown:
            # The headline was 80 columns, one over the console, so the
            # spelling is the short one (#276). The line whole and the reason
            # wrapped under it, which is the split do_commit got in #271 and
            # this copy did not: on a crypt hash pasted without its leading
            # '$' the two on one row measured 222 columns.
            self.err("the configuration to roll back to has lines this VTY "
                     "cannot read:")
            for lineno, text, unknown_why in target.unknown:
                print(f"    line {lineno}: {text}")
                print_prose(f"({unknown_why})", indent="        ")
            return False
        # Checked before it is applied, and refused whole if the check
        # refuses - the same argument load() makes, and for the same reason.
        # This runs unattended, so a revert that fails part way would leave
        # the box in neither the configuration the operator made nor the one
        # they were promised back, on a machine nobody is watching.
        before = self.failed
        if not self.apply(target, check_only=True):
            self.err("the configuration to roll back to does not validate on "
                     "this box")
            return False
        self.failed = before
        # Read BEFORE the revert, because the revert is what changes it: a box
        # whose tuner is still pointed at a card and whose restored
        # configuration names none is a box that took its own management card
        # and is being given it back (#303).
        #
        # AND THE DATAPLANE HAS TO ACTUALLY HOLD IT. The window runs from the
        # commit, and the commit does not move the card - the restart after it
        # does. An operator who commits and then waits an hour before
        # rebooting has the window expire while the box is still on its old
        # path, and there the revert is an ordinary one: the setting goes, the
        # card never moved, and restarting would be this feature rebooting a
        # box that was reachable the whole time.
        # Prose, so it is wrapped: the sentence the unit prints when a window
        # expired unconfirmed measured 113 columns (#276). The caller's first
        # words stay on the first row, which is what `commit confirmed 10` is
        # grepped for.
        print_prose(why, indent="")
        if not self.apply(target):
            return False
        try:
            self.confirm.clear()
        except OSError as e:
            # The box IS back on the old configuration - that half succeeded.
            # What is left is a record saying a revert is still due, which the
            # unit will act on again, so it is said rather than swallowed.
            # The path on a row of its own: on the box's own path the one row
            # measured 88 columns, and a path is what the operator goes and
            # looks at, so it is quoted whole (#276).
            self.err("rolled back, but this file could not be removed")
            print(f"    {self.confirm.path}")
            print_prose(f"({e.strerror})", indent="        ")
        self.confirm_deadline = None
        self.confirm_backup = None
        self.confirm_on_box = False
        return True

    def check_confirm_timeout(self):
        """
        The in-session half of the deadline. See arm_confirm().

        It asks the file first. A window this session armed can be confirmed
        from another session or reverted by the unit that watches the file,
        and a session that went on counting to its own copy of the deadline
        would roll back a change somebody had already confirmed.
        """
        if not self.confirm_deadline:
            return
        pending = self.confirm.read()
        if self.confirm_on_box and pending is None:
            # Confirmed elsewhere, or already reverted. Either way this
            # session has nothing left to enforce.
            self.confirm_deadline = None
            self.confirm_backup = None
            self.confirm_on_box = False
            return
        if time.time() <= self.confirm_deadline:
            return
        if pending is None:
            pending = {"config": self.confirm_backup}
        # The blank row that separates this from whatever the operator was
        # typing is printed here and no longer passed in as a leading newline:
        # `revert_confirm` wraps what it is given as prose, and textwrap eats
        # a leading newline (#276). It now precedes the refusal too, which is
        # the same separation for the same reason.
        print()
        if not self.revert_confirm(pending,
                                   "commit was not confirmed: rolling back"):
            # Said once, and then this session stops trying. Every command the
            # operator types would otherwise reprint the same failure, and the
            # durable copy on the box is still armed - the unit that watches
            # it keeps trying and reports when it gives up.
            self.err("the box is still on the unconfirmed configuration")
            self.confirm_deadline = None
            self.confirm_backup = None
        self.candidate = self.running_config()

    @sigint_ignored
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
        try:
            text = self.rb.load(n)
        except RollbackUnavailable as e:
            # The reason carries a path, so it goes on a row of its own: on
            # the box's own store the one row measured 85 columns (#276).
            self.err(f"revision {n} cannot be read")
            print(f"    {e}")
            return
        if text is None:
            self.err(f"revision {n} does not exist")
            return
        self.candidate = Config.parse(text)
        print(f"load complete (revision {n})")
        print("use 'show configuration compare' to review, 'commit' to apply")

    # ---------------------------------------------------------- modes

    def start_shell(self):
        """
        Escape to a real shell, for administrators only.

        RESTRICTED: a shell on this box is every capability at once, so the
        check below is the boundary. It used to be hidden from TREE as well,
        and this docstring already made the argument against that - hiding was
        never the boundary, the capability check is - so when the maintainer
        asked for the command, hiding was what had to go (#194). A command
        nobody can find is a command somebody has to ask for.

        It exists because recovery, tcpdump and reading a log the VTY does not
        expose all need a shell, and an appliance that makes those impossible
        gets one opened permanently by the first operator who needs one.

        Spawned as a child rather than exec'd: leaving the shell has to come
        back to the VTY, not log the operator out.
        """
        if not self.require(CAP_ALL, "start shell"):
            return
        if ATTACHED is not None:
            self.host_shell(ATTACHED)
            return
        shell = os.environ.get("PRISMA_SHELL") or "/bin/bash"
        if not os.path.exists(shell):
            shell = "/bin/sh"
        print(f"entering {shell}; type 'exit' to return to the VTY")
        try:
            subprocess.call([shell, "-i"])
        except OSError as e:
            self.err(f"cannot start {shell}: {e}")
            return
        print("back in the Prisma Routing VTY")

    def host_shell(self, channel):
        """
        `start shell` through the login broker (#448): the HOST runs it.

        This VTY has already checked its own role; the host checks again,
        from the host's groups, and its answer is the one that counts - a
        VTY in a container must not be able to give itself a host shell. The
        candidate is untouched across it, because this process never ends.
        """
        # The "entering" line is the broker's: it is the one that knows which
        # shell it runs, and the line keeps the shape an operator and
        # lab/install-iso.sh wait for - 0.127.0's first gate was refused
        # because this printed "entering a shell on the host" instead.
        how, status = channel.request_shell()
        if how == "denied":
            self.err_prose("start shell: the host refused a shell for this "
                           "account",
                           "a shell is for root and prisma-admin, decided on "
                           "the host and not here")
            return
        if how == "gone":
            self.err("start shell: the login session is gone")
            return
        print("back in the Prisma Routing VTY")

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
            # Verdict, then the consequence. The one sentence reached the
            # console at 88 columns on a console of 79 and `err` folds nothing
            # (#362); what an operator has to take from this screen is that
            # they are on an installed box, and where the command they typed
            # does exist is the row under it.
            self.err_prose("this box is not running from the installation "
                           "medium",
                           "'install' only exists there, on the live medium "
                           "this box was installed from")
            return
        if not self.require(CAP_ALL, "install"):
            return
        try:
            subprocess.call([INSTALLER])
        except OSError as e:
            self.err(f"cannot start {INSTALLER}: {e}")

    def do_probe(self, tool, rest):
        """
        `ping`, `traceroute` and `mtr`, which are one statement three ways.

        One method and not three, because the differences between them are
        two words of prose and a flag table that already lives in
        `probe_argv`. Three methods would be three places for the refusals to
        drift apart, and the refusals are most of this.

        WHO MAY: everybody, read-only accounts included, and that is a
        decision rather than an omission. These three change nothing on the
        box. An operator who may not configure anything is exactly the
        operator who is asked whether the box can reach something, and until
        #395 the only way to find out was `start shell` - which is
        administrators only, and hands over every capability at once to answer
        a question about a packet.

        BOUNDED, ALWAYS. The count defaults to PROBE_COUNT_DEFAULT and stops
        there; the deadline stops a tool that hangs. A probe that runs until
        it is interrupted, typed on the serial console of a racked box by
        somebody who then walks away, is a console nobody can use until they
        walk back.
        """
        path, package = PROBE_TOOLS[tool]
        what = {"ping": "an address to ping",
                "traceroute": "an address to trace the path to",
                "mtr": "an address to report the path to"}[tool]
        if not rest:
            self.err_prose(f"{tool}: no host", f"usage: {tool} <host> "
                           f"[count <n>] [source <address>]", what)
            return
        host = rest[0]
        count_word, source, bad = probe_options(rest[1:])
        if bad is not None:
            # The operator's own word goes into the PROSE and not the verdict,
            # for #363's reason: it is as long as they typed it, so a verdict
            # carrying it has no width, and the verdict without it is 24
            # columns and says the same thing.
            self.err_prose(f"{tool}: not an option of this statement",
                           f"'{bad}' is not one, or it is missing its value",
                           f"usage: {tool} <host> [count <n>] "
                           f"[source <address>]")
            return
        if not probe_host_ok(host):
            self.err_prose(f"{tool}: not a host",
                           f"'{host}' is neither an address nor a hostname",
                           "a hostname is letters, digits and hyphens in "
                           "labels separated by dots")
            return
        count = PROBE_COUNT_DEFAULT
        if count_word is not None:
            count = probe_count_ok(count_word)
            if count is None:
                self.err_prose(f"{tool}: count out of range",
                               f"'{count_word}' is not a whole number between "
                               f"1 and {PROBE_COUNT_MAX}",
                               "mtr counts cycles and traceroute counts hops; "
                               "ping counts packets"
                               if tool != "ping" else
                               f"the default is {PROBE_COUNT_DEFAULT}")
                return
        if source is not None and not probe_source_ok(source):
            self.err_prose(f"{tool}: not a source address",
                           f"'{source}' is not an address",
                           "the source is an address this box holds, and not "
                           "a name: a name with two addresses would answer "
                           "about whichever one was picked")
            return
        if not os.path.exists(path):
            # A statement whose tool is absent is a promise the box does not
            # keep, and the refusal names the package rather than the path:
            # the operator's next move is to install it, and on this appliance
            # that is the only move there is.
            self.err_prose(f"{tool}: this box has no {tool}",
                           f"{path} is not installed",
                           f"it comes from the '{package}' package, which "
                           f"this image normally carries")
            return
        self.run_probe(tool, probe_argv(tool, host, count, source))

    def run_probe(self, tool, argv):
        """
        Run one probe, with its output going where the operator is looking.

        NOT CAPTURED. Every other program this VTY runs is read back and
        rewritten into this box's own sentences, because what vppctl and vtysh
        print is this product's business to present. These three are the
        opposite case: what `ping` prints IS the answer, an operator already
        knows how to read it, and holding it back until the run finishes would
        turn a probe whose whole value is arriving row by row into a wait
        ending in a paragraph.

        Ctrl-C belongs to the probe and comes back HERE. `ping -c 5` against a
        host that is not answering still costs five seconds, and an operator
        who has seen enough interrupts it - which must return to the prompt
        rather than end the session, the way it does inside `start shell`.
        """
        try:
            subprocess.run(argv, timeout=PROBE_DEADLINE, check=False)
        except subprocess.TimeoutExpired:
            self.err_prose(f"{tool}: stopped after {PROBE_DEADLINE}s",
                           "the tool had not finished, so this box stopped it",
                           "a probe that does not end by itself is usually a "
                           "name that cannot be resolved")
        except KeyboardInterrupt:
            print()
        except OSError as e:
            # The program and the reason go on a row of their OWN, with
            # nothing of this VTY's behind them: a path and an errno string
            # are as long as the system makes them, and a row that carries one
            # in the middle folds inside the words (#363).
            self.err_prose(f"{tool}: cannot run its program",
                           "this box has the file and could not start it:",
                           f"{argv[0]}: {e}")

    def do_system(self, rest):
        """
        `system reboot` and `system shutdown`: the box itself, from the VTY.

        Until this existed an operator who needed either dropped to `start
        shell` and typed `systemctl`, which is the shape docs/context.md
        argues against: an operator who has to leave the VTY for the thing
        they came to do is one for whom the VTY is decoration (#388).

        WHO CAN, AND TODAY THAT IS ROOT. `require(CAP_ALL)` is the first
        gate, the same one every privileged statement goes through, so a
        read-only or CGNAT account meets the sentence it already knows from
        `start shell` - and meets it BEFORE systemd is asked anything, which
        the suite pins by reading what was asked. It is not the last gate.
        prismash runs as the account that logged in (appliance/prismash-login
        runs it with no setuid and no sudo), `systemctl reboot` from any
        uid but 0 is decided by systemd, and the appliance grants no other
        account that decision. Measured on the tree and on a box: the two
        package lists in appliance/build-iso.sh (the debootstrap --include
        and the apt-get install under it) name neither sudo nor polkit, and
        `apt-cache depends --recurse --no-recommends` over every package on
        them, on the bookworm 12.15 the image is built from, reaches neither
        - it does reach dbus, through gnupg, so systemctl from a non-root
        account gets as far as systemd and is refused there. What that
        refusal says was measured here on systemd 252.39, as uid 1000 with
        no polkitd installed, with the one verb that is safe to type on a
        developer box: `systemctl start <unit>` prints "Failed to start
        <unit>: Access denied" and "See system logs and 'systemctl status
        <unit>' for details.", exit 4. A box that HAS polkit and no rule
        for the account - a Debian this was installed onto, with a desktop's
        polkit - says "Interactive authentication required" in the same
        place; that is what a non-root administrator gets there, and the
        reboot verb goes through logind first and adds a row of its own
        ("Failed to reboot system via logind: ..."). Either way the account
        reads the cost prose and then systemd's refusal, rendered. NOT fixed
        by granting it: a sudoers drop-in or a polkit rule needs a package
        the image has not got, and appliance/systemd/prisma-routing-confirm.timer
        already carries the product's position on handing prisma-admin the
        ability to make this box run units - "a far larger grant than the
        feature is worth" (#212). So the wiki says root, and this paragraph
        is the measurement behind it; which of the two refusals a prisma-admin
        member sees on an INSTALLED appliance is owed a booted box.

        IT SAYS WHAT IT COSTS, THEN IT ASKS - AND TWO OF THE COSTS ARE
        MEASURED. Every session ending and the dataplane stopping are true
        of every box. What ELSE a reboot loses depends on this one: `commit`
        does not write to disk, so a running configuration that differs
        from the file at CONFIG_PATH is one the next boot does not load; and
        a 'commit confirmed' nobody confirmed is reverted by the box itself,
        restarted or not (ConfirmPending). say_system_cost() reads both the
        way `save` does - the running configuration off the device, the
        file off the disk, the window off the confirm store - and says each
        in the "warning:" shape save_running uses, because neither refuses:
        a reboot typed inside a window can be exactly what the operator
        wants, and #212 made save and confirm separate decisions on purpose.
        Said BEFORE the ask, which the suite measures by reading the screen
        at the instant systemd is asked. That is the pattern the product had
        for its one restart, the handover revert 6e removed in 0.76.0: a row
        saying what is happening, prose under it saying what it costs, then
        systemd. Not a question to confirm. `commit remap` is gated on a
        word because a remap is a mistake most times it is typed and
        deliberate some of them; `system reboot` is not typed by mistake,
        and a question here would break the `prismash -c "system reboot"` a
        runbook types, which #376's commit rule leaves alone because there
        is no commit in it.

        THROUGH SYSTEMD, AND THAT IS THE POINT RATHER THAN PLUMBING. A reboot
        that stopped the dataplane without draining would leave the retention
        log with an allocation open for ever (#217), and the drain is
        vpp.service's ExecStop - appliance/systemd/vpp-stop-drain.conf, which
        appliance/install.sh writes as prisma-routing-stop-drain.conf. That
        ExecStop runs on any stop of vpp.service, which lab/ipfix-stop.sh
        drives on a plain `systemctl stop`; the question here is only whether
        a `systemctl reboot` stops vpp.service at all, and it does because a
        reboot stops every unit in the shutdown transaction. Measured on
        systemd 252, which is what bookworm ships and this appliance runs:
        `systemctl show reboot.target -p Requires` is systemd-reboot.service,
        `systemctl show systemd-reboot.service -p Requires` names
        shutdown.target, and poweroff.target is the same chain through
        systemd-poweroff.service. A unit left with default dependencies is
        ordered `Before=shutdown.target` and `Conflicts=shutdown.target` -
        measured on ssh.service here, because vpp.service is the packaged VPP
        unit and is not on a developer box to query - so it is stopped before
        the system goes down and its ExecStop runs while the graph is up. The
        appliance adds four drop-ins to vpp.service and none sets
        `DefaultDependencies=no` (only prisma-routing-installer and
        prisma-routing-selftest do), so nothing here takes vpp.service off that
        path. What WOULD skip the drain is `systemctl reboot --force`, or a
        kill; neither is asked for here, and the suite pins the argv to the
        plain verb. The whole path is exercised on a booted box by
        lab/install-iso.sh PHASE 3b, which is owed a run.

        THE FAILURE IS A REFUSAL, IN SYSTEMD'S OWN WORDS, AS A PARAGRAPH.
        The alternative is an operator who believes the box is going down
        and walks away from one that is not. systemd's refusal is two or
        three rows, and the first version of this joined them onto the
        verdict row: 145 columns with the two-row example the suite fed it,
        past 200 with the three rows the reboot verb really prints, on a
        console of 79 - and `err` folds nothing, on purpose (#252). So the
        verdict is one row, "the box could not be restarted", and systemd's
        rows go under it through err_prose, wrapped, which is the split
        #355 made for exactly this shape. rc 127 is systemctl not being on
        the machine at all, which is every developer box and every suite; it
        is a refusal here and not the "written and not run"
        apply_dataplane_if gives the tuner, because nothing was written - a
        reboot that did not happen has no half that did.
        """
        if rest == ["reboot"]:
            if not self.require(CAP_ALL, "system reboot"):
                return
            print("restarting the box")
            self.say_system_cost()
            print_prose("it comes back by itself, claiming its dataplane card "
                        "at boot; nothing needs typing at its console")
            rc, said = self.ask_systemd("reboot")
            if rc != 0:
                self.err_prose("the box could not be restarted", *said)
            return
        if rest == ["shutdown"]:
            if not self.require(CAP_ALL, "system shutdown"):
                return
            print("shutting the box down")
            self.say_system_cost()
            print_prose("it stays off until something powers it on again, and "
                        "then comes back claiming its card by itself")
            rc, said = self.ask_systemd("poweroff")
            if rc != 0:
                self.err_prose("the box could not be shut down", *said)
            return
        if rest[:1] == ["upgrade"]:
            self.system_upgrade(rest[1:])
            return
        self.err("usage: system reboot | system shutdown")

    def system_upgrade(self, args):
        """
        `system upgrade [<version>]` (#440): a release, from where the box can
        download it, through the same `upgrade` an operator runs by hand.

        Without a version it says which one this box runs and which one is
        the newest published, and changes nothing. With one it downloads the
        package and its sha256 from RELEASES_URL, refuses a sum that does not
        match before anything is unpacked, unpacks inside a directory of its
        own, and runs the package's `upgrade`, showing every row it prints.
        Its refusals are its own and stay intact. Same gate as `system
        reboot`, and in practice root, for the reason do_system() measures.

        The version this box runs and an older one are refused: going back
        is a decision with costs of its own (#436 changed a spelling an older
        release does not read), and it stays the hand-run path.

        Ctrl-C does nothing while `upgrade` runs, for the reason it does
        nothing during a commit (#441): a box half upgraded is worse than a
        slow one. The terminal would send SIGINT to `upgrade` too, so it is
        ignored in both, by sigint_ignored().
        """
        if not self.require(CAP_ALL, "system upgrade"):
            return
        mine = prisma_routing_version()
        if not args:
            newest, why = latest_release()
            print(f"this box runs: {mine}")
            if newest is None:
                self.err_prose("the newest release could not be read", why)
                return
            print(f"newest published: {newest}")
            if version_tuple(newest) and version_tuple(mine) \
                    and version_tuple(newest) > version_tuple(mine):
                print_prose(f"'system upgrade {newest}' takes this box to it")
            return
        if len(args) != 1 or version_tuple(args[0]) is None:
            self.err("usage: system upgrade [<version>], as in "
                     "'system upgrade 0.123.0'")
            return
        want = args[0]
        have = version_tuple(mine)
        if have and version_tuple(want) <= have:
            self.err_prose("system upgrade: not newer than this box",
                           f"'{want}', and it runs {mine}; going back is the "
                           f"hand-run path, with its own costs")
            return
        self.run_upgrade(want)

    @sigint_ignored
    def run_upgrade(self, want):
        """Download, verify, unpack and run release `want`; see
        system_upgrade(). SIGINT is ignored for the whole of it."""
        name = f"prisma-routing-{want}-upgrade"
        base = f"{RELEASES_URL.rstrip('/')}/{want}/{name}.tar.gz"
        summ, why = fetch_url(base + ".sha256")
        if summ is None:
            self.err_prose("system upgrade: its sha256 could not be "
                           "downloaded; nothing was changed", why)
            return
        published = (summ.decode("ascii", "replace").split() or [""])[0]
        work = tempfile.mkdtemp(prefix=f"prisma-upgrade-{want}-",
                                dir=UPGRADE_WORK)
        tar_path = os.path.join(work, name + ".tar.gz")
        print(f"downloading {base}")
        got, _size, why = download_to(base, tar_path)
        if got is None:
            shutil.rmtree(work, ignore_errors=True)
            self.err_prose("system upgrade: the package could not be "
                           "downloaded; nothing was changed", why)
            return
        if got != published.lower():
            shutil.rmtree(work, ignore_errors=True)
            self.err_prose("system upgrade: it does not match its sha256",
                           "nothing was changed",
                           f"published {published or '(nothing)'}, "
                           f"downloaded {got}")
            return
        print("the package matches its published sha256:")
        print(f"    {got}")
        why = extract_within(tar_path, work)
        script = os.path.join(work, name, "upgrade")
        if why is None and not os.path.exists(script):
            why = f"it has no {name}/upgrade"
        if why:
            self.err_prose("system upgrade: the package was refused; nothing "
                           "was changed", why)
            shutil.rmtree(work, ignore_errors=True)
            return
        try:
            p = subprocess.Popen(["sh", script], stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True,
                                 cwd=os.path.join(work, name))
            for row in p.stdout:
                print(row.rstrip("\n"))
            rc = p.wait()
        except OSError as e:
            print(f"  {e}")
            rc = 127
        if rc != 0:
            self.err_prose("system upgrade: 'upgrade' did not finish",
                           f"it stopped with status {rc}; what it said is "
                           f"above, and the package is kept in {work} for "
                           f"whoever reads it next")
            return
        shutil.rmtree(work, ignore_errors=True)
        print(f"upgraded to {want}")
        print_prose("the running configuration is what a reboot replays: "
                    "'save' before rebooting, so the box comes back as it "
                    "is now")

    def say_system_cost(self):
        """
        What `system reboot` and `system shutdown` both cost, said once.

        The first two rows are true of every box. The two after them are
        this box's, read rather than recited, and each is said only when it
        is so: a warning printed on every reboot is a warning read on none.
        """
        print_prose("every session on this box ends, including this one")
        print_prose("the dataplane stops; systemd runs its stop-drain first, "
                    "so the connection log is left with no allocation open")
        self.say_unsaved()
        self.say_unconfirmed()

    def after_boot(self, saved: Config, running: Config) -> Config:
        """
        What this box holds after replaying `saved`, given it holds
        `running` now: the same decisions load() takes, on Config objects.

        Not "the file": the file is not the holder of everything in it, and
        load() says so twice. A file with no statements (the three comments
        the installer ships, or none at all) is nothing to apply, so the
        boot changes nothing outside the dataplane and FRR: the accounts
        stay in /etc/passwd, the sshd drop-in stays, snmpd stays, and the
        card and its management path come from the tuner's file. What such
        a boot loses is exactly the pools, the interface blocks, the CGNAT
        globals and the routing. A file that does say something is replayed
        as a diff against the box, accounts included - apply_users runs
        `userdel` for an account the file lacks - so there the file is the
        holder of all of it but the card, which load() takes from the tuner
        when the file is silent about it (the way back is never in the file:
        the tuner computes it at boot, #385).
        """
        boot = Config()
        if saved.is_empty():
            boot.users = running.users
            boot.ssh = running.ssh
            boot.snmp = running.snmp
            boot.snmp_communities = running.snmp_communities
            boot.snmp_users = running.snmp_users
        else:
            boot = saved
        # The card only: since #385 the way back is the tuner's, computed off
        # the live card at boot and never a statement, so there is nothing
        # of it in either text to carry over.
        if not boot.dataplane_if:
            boot.dataplane_if = running.dataplane_if
        return boot

    def say_unsaved(self):
        """
        Whether what is running survives the boot, measured as `save` does.

        `commit` puts a change on the device and `save` writes the file the
        next boot loads (#212 keeps them apart on purpose), so the fact is
        the running configuration read off the device against what the
        boot's replay of the file at CONFIG_PATH would leave on the box -
        after_boot() says which of the file's statements that replay is the
        holder of - both through Config so that spelling is not counted as
        a difference. A file that is not there is a box nobody saved, and
        it is warned only when the box holds something that boot would
        lose: a fresh appliance holds root and the installer's sshd drop-in,
        keeps both through the boot, and is warned of nothing. A file this
        account cannot read is said as such and not as "saved": the one
        answer this may not give is a false "nothing is lost".
        """
        running = self.running_config()
        try:
            with open(CONFIG_PATH) as f:
                saved = Config.parse(f.read())
        except FileNotFoundError:
            saved = Config()
        except OSError as e:
            # The path on a row of its own, as save_running prints it: with
            # the errno beside it the row would not fit (#276).
            print("warning: whether the running configuration is saved could "
                  "not be checked")
            print(f"    {CONFIG_PATH}")
            print_prose(f"({e.strerror})", indent="        ")
            return
        if running.text() == self.after_boot(saved, running).text():
            return
        print("warning: the running configuration has NOT been saved")
        print_prose("this box boots into the file 'save' writes, and what is "
                    "running now is not what that boot would load, so what "
                    "was committed and not saved is lost. Type 'save' first "
                    "to keep it")

    def say_unconfirmed(self):
        """
        A 'commit confirmed' still open, read off the store the box reverts
        from.

        The box reverts on its own, from a unit that reads the file
        (prisma-routing-confirm.service), so a restart does not cancel the
        window: a box back before the deadline reverts at the deadline, one
        back after it reverts as soon as it is up, and the session that
        could have confirmed is the one the reboot ends. save_running says
        the other half of this - a save inside the window - in the same
        shape.
        """
        pending = self.confirm.read()
        if not pending:
            return
        when = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                             time.gmtime(pending["deadline"]))
        print("warning: the last commit has NOT been confirmed")
        print_prose(f"this box puts the configuration from before that "
                    f"commit back at {when}, restarted or not, and at once "
                    "if it comes back later than that; the session that "
                    "could confirm it ends here. Type 'commit' to confirm "
                    "first")

    def ask_systemd(self, verb):
        """
        `systemctl <verb>`, and what systemd said about it, a row per line.

        Rows and not one string, because they land under a verdict through
        err_prose: systemd's refusal is two or three lines ("Failed to
        reboot system via logind: ...", "Failed to start reboot.target:
        ...", "See system logs ..."), each a sentence of its own, and joined
        onto the verdict row they measured 145 columns on a console of 79.
        An empty answer is named as such rather than left as a verdict with
        nothing under it.
        """
        rc, out = self.sys._run(["systemctl", verb])
        said = [" ".join(line.split()) for line in out.splitlines()
                if line.strip()]
        return rc, said or [f"systemctl exited {rc} and said nothing"]

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
                # The same sentence `start shell` refuses with, and it goes
                # the same way now. Printed bare it reached the console at 85
                # columns while the other one wrapped, so two refusals about
                # one fact disagreed about whether they fit (#359).
                self.err_prose(
                    "configure: permission denied for a read-only account",
                    f"membership of {GROUP_CGNAT} allows CGNAT configuration, "
                    f"{GROUP_ADMIN} allows everything")
                return True
            # Not while the dataplane is silent, and the reason is that BOTH
            # ways of proceeding are wrong.
            #
            # An installed box draws a login prompt while prisma-routing.service
            # is still waiting for VPP - the ordering that holds the prompt
            # back exists on the live image only, in the same drop-in file as
            # the live autologin, which the installer correctly deletes whole.
            # So the first thing anybody does with a new box is log in at the
            # first prompt and type this.
            #
            # Built then, the candidate carried vppctl's "Could not find VPP
            # CLI socket" as a line of configuration, and every commit for the
            # rest of that session was refused for a line nobody typed. The
            # dataplane came up seconds later and the candidate kept the error
            # text; the way out was `exit` and `configure` again, and nothing
            # said so.
            #
            # Reading the status properly does not fix it, it moves it: with
            # the read refused, the CGNAT half of the candidate is silently
            # EMPTY, and committing that removes every pool on the box. That is
            # #47, which running_config's own docstring is about.
            #
            # So neither - refuse, and say what to wait for (#211).
            if not self.dp.reachable():
                # The verdict is that the dataplane is not answering; what
                # follows from it is prose. As one row it was 84 columns on a
                # console of 79 (#362), which left this screen with three
                # tidy rows under one that did not fit - and the split is the
                # one the FRR-silent refusal already makes.
                self.err_prose(
                    "the dataplane is not answering",
                    "so there is nothing to build a candidate from",
                    "a candidate built now would be missing the CGNAT half, "
                    "and committing it would remove every pool",
                    "the box is probably still starting: 'show version' says "
                    "which daemons are up, and this usually clears within a "
                    "minute of boot")
                return True
            self.candidate = self.running_config()
            self.mode = "config"
            self.nat_ctx = []
            print("entering configuration mode")
        elif w[0] in ("exit", "quit", "logout"):
            return False
        elif w[0] in PROBE_TOOLS:
            # Before show_or_pass, which hands a word it does not know to FRR.
            # FRR has a `ping` and a `traceroute` of its own, and they are not
            # these: they run in FRR's world, answer in FRR's words and take
            # FRR's flags, so a box that let them through here would answer the
            # same question two ways depending on which one the operator
            # reached (#395).
            self.do_probe(w[0], w[1:])
        elif w[0] == "clear":
            self.do_clear(line)
        elif w[0] == "system":
            # Before show_or_pass, which hands every word it does not know to
            # FRR: typed today, `system reboot` reached vtysh and was refused
            # in FRR's words, about a command FRR never had.
            self.do_system(w[1:])
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

    #: The words that mean the same thing at every level of `nat`, and so are
    #: not rewritten into a CGNAT statement there: committing, reading,
    #: moving and leaving.
    NAT_LEVEL_WORDS = ("commit", "rollback", "show", "top", "up", "exit",
                       "quit", "end", "start") + tuple(PROBE_TOOLS)

    def nat_rewrite(self, line):
        """
        `nat` and `cgnat` as the levels they are (#446), and what is typed in
        them in the words the model holds.

        At the top, `nat` enters the level and the one-line form that used to
        stand for it - `nat cgnat pool ...`, `cgnat ...` - is refused, by the
        maintainer's decision of 2026-09-24. Under `nat` there is `cgnat` and
        nothing else yet. Under `cgnat`, `pool residencial` is the model's
        `cgnat pool residencial`, so every check a pool, an attachment or a
        global has always had applies unchanged.

        Returns the line to go on with, or "" when it was handled here.
        """
        w = line.split()
        if not w:
            return ""
        if not self.nat_ctx:
            if line.strip() == NAT_HIERARCHY:
                self.nat_ctx = [NAT_HIERARCHY]
                return ""
            if one_line_cgnat(w):
                self.err_prose("the one-line CGNAT form is gone",
                               "enter 'nat', then 'cgnat', and type the rest "
                               "there: 'pool <name>', 'interface <name> "
                               "inside', 'logging ...'")
                return ""
            return line
        if w[0] in self.NAT_LEVEL_WORDS:
            if w[0] in ("exit", "quit"):
                self.nat_ctx.pop()
                return ""
            if w[0] == "end":
                self.nat_ctx = []
                return ""
            return line
        if self.nat_ctx == [NAT_HIERARCHY]:
            if line.strip() == CGNAT_LEVEL:
                self.nat_ctx.append(CGNAT_LEVEL)
            else:
                self.err_prose("under 'nat' there is 'cgnat' and nothing else",
                               "'cgnat' enters it; 'exit' leaves 'nat'")
            return ""
        if w[0] == "no":
            return f"no {CGNAT_LEVEL} " + " ".join(w[1:])
        return f"{CGNAT_LEVEL} " + line.strip()

    def hierarchy_rewrite(self, line):
        """
        The hierarchy the operator typed, in the words the model holds.

        Two hierarchies were asked for that the tools underneath do not have:
        CGNAT under `nat`, and static routes under `router` (#193). VPP's
        plugin knows `cgnat`; FRR has no `router static` at all - its static
        routes are top-level `ip route` statements, and a `router static` line
        handed to it is refused mid-commit.

        So the new words are translated here, at the one place typed
        configuration enters, and the OLD words keep working unchanged. That
        second half is not politeness. Every box in service has a saved
        configuration and up to twenty rollback revisions written in the old
        spelling, and prisma-routing.service replays that file at every boot, on a
        machine nobody is watching: a grammar that cannot read the box's own
        file leaves the box unconfigured. `config.strip_nat` and
        `config.ungroup_static` are the same rule for the file, so the two
        cannot drift.

        Returns the line to go on with, or "" when the line was a block of this
        VTY's own and there is nothing further to do.
        """
        # Only at the top level of configuration mode. Inside a block the
        # words belong to that block's grammar - a route-map or a description
        # may legitimately begin with any word at all - and rewriting there
        # would edit somebody's policy on the strength of its first token.
        if self.frr_ctx and self.frr_ctx[-1] != STATIC_BLOCK:
            return line
        if not self.frr_ctx:
            line = self.nat_rewrite(line)
            if not line:
                return ""
        w = line.split()
        if not w:
            return ""

        if line.strip() == STATIC_BLOCK:
            self.frr_ctx.append(STATIC_BLOCK)
            return ""
        if not (self.frr_ctx and self.frr_ctx[-1] == STATIC_BLOCK):
            return line
        # Inside the block, ONE statement (#436): `<prefix> next-hop <hop>
        # [distance <n>] [tag <n>] [description <text>]`, parsed by the same
        # config.static_route() a saved file goes through. `route ...` and
        # FRR's own `ip route ...` are refused here, by the maintainer's
        # decision not to carry a second spelling.
        self.static_pending = None
        if w[0] == "no":
            frr, _, why = static_route(w[1:])
            if why is not None:
                self.err_prose(*why)
                return ""
            self.remove_static_route(static_route_key(frr))
            return ""
        if "/" in w[0] or w[0] in ("route", "ip", "ipv6"):
            if w[0] in ("ip", "ipv6"):
                w = ["route"] + w[2:]
            frr, desc, why = static_route(w)
            if why is not None:
                self.err_prose(*why)
                return ""
            # Staged by do_config, after FRR has been asked (#431). What is
            # left to do once it is - replace the route this one re-types,
            # and keep its description - rides here.
            self.static_pending = (static_route_key(frr), desc)
            return frr
        # The words that are not this block's business: the ways out, the
        # readers, and the words every configuration level owns. They are
        # handed BACK so `do_config` dispatches them through `owns_word`.
        #
        # `top`, `up` and the probes were missed here on the first pass of
        # #400 and `lab/vty.sh` caught it: four dispatchers had been given the
        # words and this fifth path refused them, so `top` worked at every
        # level except inside `router static` - which is the copy-per-level
        # failure that issue is about, one layer further in.
        if (w[0] in ("exit", "quit", "end", "show", "commit", "rollback")
                or w[0] in LEVEL_WORDS or w[0] in PROBE_TOOLS):
            return line
        # The word typed goes into the PROSE and not into the verdict: it
        # is whatever the operator typed, so a verdict carrying it has no
        # width at all, and the verdict without it is 43 columns and the same
        # refusal (#363). As one row it was 84 with the shortest word that
        # reaches it.
        self.err_prose("not a statement of 'router static'",
                       f"'{w[0]}' is not one; what is entered here is "
                       f"'<prefix> next-hop <next-hop>', or 'exit'")
        return ""

    def do_config(self, line):
        line = self.hierarchy_rewrite(line)
        if not line:
            return True
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
        # THE WORDS THIS VTY OWNS, ahead of the passthrough that hands
        # anything else to FRR (#400). `top` and `up` are navigation, the
        # probes are operational commands this box already answers in
        # operational mode - and every one of them was being staged for FRR
        # and refused at the next commit, silently, until the candidate could
        # not be committed at all.
        #
        # The probes carry the argument the operational-mode dispatcher makes
        # at its own call site (#395): FRR has a `ping` and a `traceroute` of
        # its own, they run in FRR's world and answer in FRR's words, so a box
        # that let them through would answer one question two ways. In
        # configuration mode it was worse than that - nothing dispatched them
        # at all, so they went into the candidate.
        if self.owns_word(w):
            return True
        if w[0] == "exit-address-family":
            # FRR's own spelling, and the one an operator arriving from
            # another vendor types. It leaves the address-family and nothing
            # else - so it is only accepted inside one, rather than silently
            # popping whatever block happens to be open.
            if self.frr_ctx and self.frr_ctx[-1].startswith("address-family "):
                self.candidate.routing.append(line)
                self.frr_ctx.pop()
            else:
                self.err("exit-address-family: not inside an address-family")
            return True
        if w[0] in ("exit", "quit", "end"):
            if self.frr_ctx:
                # The `exit` goes into the candidate, because without it the
                # flat list of routing statements has no record that the block
                # ended - the header went in on the way in and nothing marked
                # the way out. frr-reload.py reads the same list and decides
                # context by statement exactly as `block_paths` does, so a
                # missing `exit` meant the next top-level statement was handed
                # to FRR INSIDE the block just left (#180). It is also what
                # FRR writes itself, so the candidate and what comes back from
                # `show running-config` are written the same way.
                #
                # `router static` is the exception: FRR has no such block,
                # nothing opened one, and a stray `exit` would close whatever
                # really is open.
                if self.frr_ctx[-1] != STATIC_BLOCK:
                    self.candidate.routing.append("exit")
                self.frr_ctx.pop()
                return True
            running = self.running_config()
            if self.candidate is not None and not running.is_same(self.candidate):
                print("warning: uncommitted changes were discarded "
                      "(use 'commit' to apply them)")
            self.candidate = None
            self.mode = "operational"
            self.nat_ctx = []
            return True

        if line.startswith("cgnat pool "):
            name = w[2]
            # Before the pool exists and before the mode changes, for the
            # reason `router bgp` is refused before its block is entered
            # (#415): a name refused after the mode moved leaves the operator
            # typing pool statements into a pool the box will not take.
            why = text_error("cgnat pool", name)
            if why:
                self.err_prose(*why)
                return True
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
        if w[:2] == ["no", "dataplane"]:
            # `no dataplane` and `no dataplane interface` alike: there is one
            # setting under that word, so refusing the shorter spelling would
            # be a refusal with nothing behind it.
            self.candidate.dataplane_if = ""
            return True
        if w[0] == "dataplane":
            if w[1:2] != ["interface"] or len(w) != 3:
                self.err("usage: dataplane interface <name>")
                return True
            # REFUSED HERE, at the prompt, and checked again at commit.
            #
            # Both, and neither is redundant. This one is the message that
            # reaches a person who is still typing and can fix it in the next
            # keystroke; the one in apply() covers `rollback` and `--load`,
            # which reach the same setting without passing through here.
            #
            # AND NOTHING ELSE HAPPENS HERE (#385). The prompt used to write
            # down, on a one-card box, the address, MAC and gateway the card
            # carried, so the boot after the commit could put them back. The
            # tuner reads the same three things off the live card on the boot
            # that claims it, whichever box it is on and whether or not the
            # card was named, so a record taken here was a second copy of a
            # fact the box recomputes - and one that could be a day older than
            # the boot that used it.
            why = self.dataplane_if_refusal(w[2])
            if why:
                self.say_dataplane_if_refusal(w[2], why)
                return True
            self.candidate.dataplane_if = w[2]
            return True
        if w[:2] == ["dns", "server"] or w[:3] == ["no", "dns", "server"]:
            return self.dns_statement(w)
        if w[0] == "dns" or w[:2] == ["no", "dns"]:
            self.err("usage: [no] dns server <address>")
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
            why = text_error("user", w[1])
            if why:
                self.err_prose(*why)
                return True
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
                self.err("usage: interface <interface> <inside|outside>")
            else:
                self.candidate.interfaces[w[2]] = w[3]
            return True
        # `no interface <name>` deletes what this configuration CREATED, and
        # with it whatever was on it: a sub-interface or a loopback. Not a
        # physical port - that is not something a configuration can remove, and
        # accepting the words for one would promise what the commit cannot
        # deliver.
        if line.startswith("no interface "):
            gone = w[2] if len(w) > 2 else ""
            if "." not in gone and not _is_loopback(gone):
                self.err_prose(
                    f"{gone or '<name>'}: only a sub-interface or a loopback "
                    f"can be removed",
                    "a physical port is not created by this configuration, so "
                    "there is nothing here to remove")
                return True
            self.candidate.vlans.pop(gone, None)
            self.candidate.loopbacks.discard(gone)
            self.candidate.linux_names.pop(gone, None)
            self.candidate.addresses.pop(gone, None)
            self.candidate.mtus.pop(gone, None)
            self.candidate.urpf.pop(gone, None)
            self.candidate.descriptions.pop(gone, None)
            self.candidate.admin_down.discard(gone)
            self.candidate.interfaces.pop(gone, None)
            self.candidate.macs.pop(gone, None)
            self.candidate.vrfs.pop(gone, None)
            self.candidate.ra_enabled.discard(gone)
            return True

        if line.startswith("no cgnat interface "):
            self.candidate.interfaces.pop(w[3], None)
            return True
        if line.startswith("cgnat "):
            # Only the words this VTY actually has, and CGNAT_CONFIG is where
            # they are written down - the same table `?` offers from, so a
            # refusal here cannot drift from what the box says it takes.
            #
            # This used to accept anything matching `cgnat <words>`: the line
            # went into the candidate, was rendered back by `show
            # configuration`, was saved, and applied NOTHING, because
            # apply_global() has four branches and no else. The operator was
            # told `commit complete` for a setting the box does not have, and
            # then shown it in the configuration, which is the one thing that
            # would have told them otherwise (#273).
            #
            # docs/context.md 1.4: a counter is not evidence. An acceptance is
            # not evidence either. This is #136's defect one layer up, in the
            # VTY the operator actually types at rather than in vppctl.
            if len(w) < 2 or w[1] not in CGNAT_CONFIG:
                what = w[1] if len(w) > 1 else "(nothing)"
                self.err(f"cgnat {what}: this VTY has no such setting")
                print_prose(
                    "it would have been accepted, saved and applied to "
                    "nothing. What this box takes here is: "
                    + ", ".join(sorted(CGNAT_CONFIG)))
                return True
            # #273 closed the WORD and left the NUMBER. `cgnat timeout udp
            # 65536` and `cgnat block-holddown 0` were both accepted here,
            # under a hint that said `<1-65535>`, and both are seconds in a
            # u32 field where zero is the message's "leave this alone" rather
            # than a timeout of none (#351). Same two numbers as the pool's
            # own timeouts, from Pool.RANGES, because they are the same field.
            if w[1] == "timeout" and len(w) >= 4:
                # THE NAME BEFORE THE NUMBER (#418). apply_global refuses an
                # unknown timeout at commit and the prompt staged it, so an
                # operator who mistyped one built a candidate around a
                # statement the box was going to throw out.
                if self.TIMEOUT_FIELD.get(w[2]) is None:
                    self.err_prose(
                        f"cgnat timeout {w[2]}: no such timeout",
                        "this box has: " + ", ".join(sorted(
                            self.TIMEOUT_FIELD)))
                    return True
                why = Pool.check_range("timeout", w[3], w[2])
            elif w[1] == "block-holddown" and len(w) >= 3:
                # "timeout" names the RANGE, and the operator's statement is
                # what the row has to name - so the range's key does not go
                # into the sentence. `cgnat timeout block-holddown 0` is not a
                # statement this box has.
                why = Pool.check_range("timeout", w[2], "")
                if why:
                    why = why.replace("timeout ", "block-holddown ", 1)
            else:
                why = None
            if why:
                self.err(f"cgnat {why}")
                return True
            # #273 closed the FIRST word and nothing after it (#431). `cgnat
            # interface` with no name was staged - it does not reach its own
            # branch, which wants a space after the word - and so were `cgnat
            # pool`, `cgnat timeout` and `cgnat logging foo`: known first
            # word, the rest unread. Walked against the same table `?` offers
            # from, so the refusal and the completion cannot disagree. After
            # the two checks above, which name a wrong timeout better than a
            # walk can.
            short = cgnat_statement_short(w[1:])
            if short:
                self.err_prose("not a whole cgnat statement; nothing staged",
                               *short)
                return True
            key = global_key(line)
            self.candidate.globals = [g for g in self.candidate.globals
                                      if global_key(g) != key]
            self.candidate.globals.append(line)
            return True

        # The interface this line is inside, and which dataplane statement it
        # is. Both asked once, because every branch below asks the same two
        # questions and the second one IS the boundary between what this box
        # applies and what FRR is handed.
        iface = (self.frr_ctx[-1].split()[1]
                 if self.frr_ctx and self.frr_ctx[-1].startswith("interface ")
                 else None)
        neg = w[0] == "no"
        dp = dataplane_statement(w[1:] if neg else w)

        # `mtu` inside an interface block belongs to the device, not to FRR.
        # FRR would accept it and set it on the TAP alone, which is the half
        # that does not matter: the packet path is the VPP interface, and an
        # MTU set on one side only reproduces the mismatch this exists to
        # prevent. Everything else in the block is still FRR's.
        if iface and not neg and dp == "mtu":
            if len(w) < 2 or not is_number(w[1]):
                self.err(f"usage: mtu {MTU_RANGE}")
            elif not MTU_MIN <= int(w[1]) <= MTU_MAX:
                self.err(f"mtu {w[1]} out of range "
                         f"({MTU_MIN}-{MTU_MAX})")
            else:
                self.candidate.mtus[iface] = int(w[1])
            return True
        if iface and neg and dp == "mtu":
            self.candidate.mtus.pop(iface, None)
            return True

        if iface and not neg and dp == "mac-address":
            # No separate usage line. `mac_error("")` already says "expected
            # an address", so a second refusal for the same mistake would be
            # one more site nothing drives - which is exactly what
            # test_prismash_refusals.py caught when this branch was left in.
            why = mac_error(w[1] if len(w) > 1 else "")
            if why:
                self.err_prose(*why)
            else:
                # Normalised, not stored as typed: the dataplane prints two
                # digits per octet, so a short spelling left the candidate and
                # the running configuration permanently unequal (#414).
                self.candidate.macs[iface] = mac_normal(w[1])
            return True
        # `no mac-address`, which went to FRR until #224: the MAC of a VPP
        # interface is nothing FRR can act on, so at commit it was a statement
        # FRR does not know, arriving in the middle of applying. It reads the
        # same way as `no mtu` and does the same thing - the candidate stops
        # naming a MAC for this interface, so the commit sets none and the
        # interface keeps the one it has. Which is exactly what an operator
        # who typed a MAC and then took it back is asking for.
        #
        # `Config.parse` has ALWAYS understood this line; only the VTY did
        # not. One statement meaning two different things depending on whether
        # it was typed or loaded from a file is the defect the `forward`
        # comment in do_pool warns about, and it was real here.
        if iface and neg and dp == "mac-address":
            self.candidate.macs.pop(iface, None)
            return True
        if iface and not neg and dp == "linux-name":
            if len(w) < 2:
                self.err("usage: linux-name <name>")
            elif linux_ifname_error(w[1]):
                # The limit and the sentence are config.linux_ifname_error's;
                # this file held a second copy of the 15 (#389).
                self.err(f"linux-name: {linux_ifname_error(w[1])}")
            else:
                self.candidate.linux_names[iface] = w[1]
            return True
        if iface and neg and dp == "linux-name":
            self.candidate.linux_names.pop(iface, None)
            # A claimed card with a block is paired as itself by default and
            # pair_cards() puts that default back at the next apply, so this
            # statement takes an OVERRIDE back and never unpairs a card.
            # Typed for the first it is right; typed for the second it does
            # nothing, silently, and there is no statement that unpairs a
            # card with a block - `no interface <card>` is refused. Said
            # here, where the operator is reading (#389). A sub-interface or
            # a host interface gets no default, so on one of those this does
            # unpair, and nothing is said: a warning there would be false.
            # Asked of the dataplane, and a dataplane that cannot be asked
            # defaults nothing at apply either.
            #
            # A LOOPBACK IS IN THIS SET SINCE #404, and it is in it from the
            # candidate rather than from the box: naming a loopback creates
            # it, so one typed a moment ago is not on the dataplane to be
            # asked about, and the operator taking an override back is
            # standing in the block that made it.
            if (iface in self.defaulted_cards()
                    or (iface in self.candidate.loopbacks
                        and not linux_ifname_error(iface))):
                what = ("a loopback" if iface in self.candidate.loopbacks
                        else "a card")
                # The name goes in the prose, which folds: on the headline,
                # driven at the widest interface name, the row measured 82.
                print("warning: no linux-name: the default returns at commit")
                print_prose(f"{iface} is {what} with an interface block, and "
                            "such an interface is paired into Linux under its "
                            "own name unless 'linux-name <name>' says "
                            "otherwise; this takes an override back and does "
                            "not unpair it")
            return True
        if iface and not neg and dp == "ipv6 nd suppress-ra":
            # The default already. Accepted so an operator can write it to
            # say so, and so a configuration saved from an older box loads.
            self.candidate.ra_enabled.discard(iface)
            return True
        if iface and neg and dp == "ipv6 nd suppress-ra":
            self.candidate.ra_enabled.add(iface)
            return True

        # `vrf` moves the interface between routing tables. The device's, and
        # not something FRR can be told about after the fact.
        if iface and not neg and dp == "vrf":
            # The TOP is new, and the refusal now quotes the same two numbers
            # `?` does. A table id is a u32 in Linux and a u32 in the
            # dataplane, `?` offered `<1-65535>`, and the parser took any run
            # of digits at all - so all three said something different and
            # only one of them was checking anything (#351).
            if len(w) < 2 or not is_number(w[1]) or int(w[1]) > 4294967295:
                self.err(f"usage: vrf {ARG_VRF}, or 0 to take it back out")
            elif int(w[1]):
                self.candidate.vrfs[iface] = int(w[1])
            else:
                self.candidate.vrfs.pop(iface, None)
            return True
        if iface and neg and dp == "vrf":
            self.candidate.vrfs.pop(iface, None)
            return True

        # `vlan` creates the sub-interface. It is the device's for the same
        # reason as an address, and more so: FRR cannot create one at all.
        # There is no `no vlan`: see INTERFACE_NO_REFUSED for why, and for
        # what an operator is told to type instead.
        if iface and not neg and dp == "vlan":
            why = None
            if len(w) < 2 or not is_number(w[1]):
                why = f"usage: vlan {VLAN_RANGE} [inner {VLAN_RANGE}]"
            # THE RANGE THIS STATEMENT HAS ALWAYS QUOTED, now checked. `?` and
            # the usage line above both said <1-4094> and nothing compared a
            # typed tag against them, so `vlan 0` and `vlan 4095` - the two
            # 802.1Q keeps for itself - were staged, committed and turned into
            # a sub-interface no switch will trunk. `vlan 4096` went through
            # too: there was no upper bound of any kind (#350).
            #
            # Before the sub-interface name check rather than after it, because
            # `interface ens19.4095` satisfies that check - the name and the
            # tag agree, and both are wrong - so on its own it bounds nothing.
            elif vlan_tag_error(w[1]):
                why = vlan_tag_error(w[1])
            elif "." not in iface:
                why = (f"{iface} is not a sub-interface; a VLAN tag belongs "
                       "to one, named <parent>.<n>")
            elif iface.rsplit(".", 1)[1] != w[1]:
                # VPP names a sub-interface after its sub_id, so accepting
                # `interface Gi0/1.7` with `vlan 100` would produce a box
                # calling it Gi0/1.100 while the configuration called it
                # Gi0/1.7 - and the running configuration is read from the
                # box. The two names have to be the same name.
                why = (f"{iface} must carry vlan {iface.rsplit('.', 1)[1]}: "
                       "the dataplane names a sub-interface after its tag")
            if why:
                self.err(why)
                return True
            tag = {"outer": int(w[1]), "inner": None}
            if len(w) >= 4 and w[2] == "inner" and is_number(w[3]):
                # The inner tag had no check at all, not even the name one the
                # outer tag borrowed: nothing is named after it, so `inner 0`
                # and `inner 9999` reached the dataplane exactly as typed.
                why = vlan_tag_error(w[3], "inner")
                if why:
                    self.err(why)
                    return True
                tag["inner"] = int(w[3])
            elif len(w) != 2:
                self.err(f"usage: vlan {VLAN_RANGE} [inner {VLAN_RANGE}]")
                return True
            self.candidate.vlans[iface] = tag
            return True

        # Addresses are the device's: they belong to the packet path, and FRR
        # sees them only because linux_cp mirrors them onto the TAP.
        if iface and not neg and dp in ("ipv4 address", "ipv6 address"):
            if len(w) < 3:
                self.err(f"usage: {w[0]} address <prefix>")
            else:
                why = _address_family_error(w[0], w[2])
                if why:
                    self.err(why)
                else:
                    self.candidate.addresses.setdefault(iface, [])
                    if w[2] not in self.candidate.addresses[iface]:
                        self.candidate.addresses[iface].append(w[2])
            return True
        if iface and neg and dp in ("ipv4 address", "ipv6 address"):
            if len(w) < 4:
                self.err(f"usage: no {w[1]} address <prefix>")
            elif w[3] in self.candidate.addresses.get(iface, []):
                self.candidate.addresses[iface].remove(w[3])
            else:
                self.err(f"{iface} does not have {w[3]}")
            return True

        # `description` and `shutdown` are the device's, for the same reason as
        # `mtu`. Without these two branches they fell through to the catch-all
        # at the bottom and were staged as FRR statements - and FRR ACCEPTS
        # both words inside an interface stanza, so nothing errored. The
        # description reached FRR's configuration and never the dataplane, and
        # `shutdown` never reached anything that forwards packets.
        #
        # It passed a lab, too: the assertion read `show running-config`, which
        # includes FRR's half, so it matched the copy that had gone to the
        # wrong place. Three assertions in that lab were vacuous for this
        # reason.
        if iface and not neg and dp == "description":
            if len(w) < 2:
                self.err("usage: description <text>")
            else:
                text = line.strip().split(None, 1)[1]
                why = text_error("description", text)
                if why:
                    self.err_prose(*why)
                else:
                    self.candidate.descriptions[iface] = text
            return True
        if iface and neg and dp == "description":
            self.candidate.descriptions.pop(iface, None)
            return True
        if iface and not neg and dp == "shutdown":
            self.candidate.admin_down.add(iface)
            return True
        if iface and neg and dp == "shutdown":
            self.candidate.admin_down.discard(iface)
            return True

        # `urpf` is the device's too, for the same reason as `mtu`: the check
        # has to run on the packet path, and FRR does not own that.
        if iface and not neg and dp == "urpf":
            if len(w) < 2 or w[1] not in URPF_MODES:
                self.err("usage: urpf <" + "|".join(URPF_MODES) + ">")
            elif w[1] == "off":
                self.candidate.urpf.pop(iface, None)
            else:
                self.candidate.urpf[iface] = w[1]
            return True
        if iface and neg and dp == "urpf":
            self.candidate.urpf.pop(iface, None)
            return True

        # `vrrp` is the device's, and that is the decision #7 turns on rather
        # than an assignment that fell out. FRR ships VRRP and would run it on
        # the linux_cp TAP; the virtual address has to answer where the
        # packets arrive, which is the VPP interface.
        #
        # The statement is taken by config.vrrp_statement, which is the same
        # function the file parser calls, so the prompt and a saved file
        # refuse the same things in the same words.
        if iface and not neg and dp == "vrrp":
            why = vrrp_statement(self.candidate.vrrp, iface, w)
            if why:
                self.say_vrrp_refusal(why)
            return True
        if iface and neg and dp == "vrrp":
            why = vrrp_remove(self.candidate.vrrp, iface, w[1:])
            if why:
                self.say_vrrp_refusal(why)
            return True

        # THE HOLE THIS CLOSES: every `no <dataplane statement>` with a
        # handler has returned by now, so reaching here with one means the VTY
        # has no handler for it - and the next thing below is the routing
        # passthrough, which would stage it for FRR. `no mac-address
        # aa:bb:cc:dd:ee:ff` did exactly that: staged, marked `<- not checked`
        # by #141, and handed to a daemon with no opinion about the MAC of a
        # VPP interface, in the middle of a commit (#224).
        #
        # Refused, and named. This is also the safety net for a dataplane
        # statement added later without deciding what `no` means for it: the
        # answer is a refusal that says so, never a silent trip to FRR.
        if iface and neg and dp:
            self.err_prose(*INTERFACE_NO_REFUSED.get(
                dp, (f"no {dp}: this VTY has no way to remove it",
                     "it is not FRR's to remove either",
                     f"set the value you want with '{dp} ...', or remove the "
                     f"interface with 'no interface {iface}'")))
            return True

        # `no` on its own is not a command anywhere - not at `[edit]`, not in
        # a block - and staging it sent the word by itself to FRR, which
        # answers with an unknown command in the middle of applying. The
        # refusal says where to look, because the thing the operator wants is
        # one TAB away (#224).
        if w == ["no"]:
            self.err("no <statement>: say which statement to remove "
                     "('no ?' lists them)")
            return True

        # anything else is routing: staged in the candidate, applied on commit
        #
        # `no X` REMOVES the X it negates, when the candidate has one. Staging
        # it as another line left the candidate holding both `ip route
        # 203.0.114.0/24 blackhole` and `no ip route 203.0.114.0/24 blackhole`,
        # which is not a configuration - and FRR answered the only way it
        # could: "% Can't find static route specified", because by the time it
        # reached the `no`, the delta had already taken the route away.
        #
        # When there is nothing to remove, the `no` is staged as before. Some
        # `no` forms ARE configuration rather than a removal - `no bgp
        # ebgp-requires-policy` is the one every eBGP session in this lab
        # needs - and dropping those would break them.
        # THE AS NUMBER BEFORE THE STATEMENT IS STAGED (#415), and before the
        # block below is entered. Both halves matter and the second is the one
        # that bites: a stanza refused after `frr_ctx.append` leaves the
        # operator standing inside a block this box has just said it will not
        # take, so every statement they type next lands in it. Staged first,
        # it also reaches `commit` as a statement nobody asked for.
        #
        # Only `router bgp` here. The other three places an AS is written -
        # `remote-as` and the two `set as-path` verbs - are read by
        # asn_findings at commit, which is the reader that covers a saved file
        # too. This one is at the prompt as well because it opens a block.
        if w[:2] == ["router", "bgp"]:
            why = asn_error("router bgp", w[2] if len(w) > 2 else "")
            if why:
                self.err_prose(*why)
                return True
            note = asn_note(w[2])
            if note:
                print_prose(note)
        if w[0] == "no" and len(w) > 1:
            negated = " ".join(w[1:])
            for i, existing in enumerate(self.candidate.routing):
                if existing.strip() == negated:
                    del self.candidate.routing[i]
                    return True
        # FRR IS ASKED BEFORE ANYTHING IS STAGED (#431). Until this, every
        # line that reached here went into the candidate unread - `banana
        # split` at [edit], `netwrk 10.0.0.0/8` inside an address-family -
        # and the operator met it at `commit`, in FRR's words, lines away
        # from where it was typed. Before the block below is entered, too: a
        # `router bgp` FRR refuses must not leave the operator standing
        # inside it with every next line landing there.
        #
        # `router static` is this VTY's block and not FRR's - FRR 10.7.1 says
        # `% Unknown command: router static` - and what is typed inside it
        # is stored as a top-level `ip route`. Left in the path, FRR refused
        # the BLOCK, and every static route typed there was refused with it:
        # lab/vty.sh went red on seven checks the first time this ran.
        verdict, frr_said = self.frr.dryrun(
            [b for b in self.frr_ctx if b != STATIC_BLOCK], line)
        if verdict == Frr.DRYRUN_REFUSED:
            self.static_pending = None
            self.err_prose("not a statement FRR accepts here; nothing staged",
                           f"FRR answered: {frr_said}",
                           "'?' lists what this level takes")
            return True
        if verdict == Frr.DRYRUN_UNCHECKED:
            print_prose(f"note: staged WITHOUT being checked, because FRR "
                        f"could not be asked ({frr_said}); 'commit check' "
                        f"asks it again")
        pending, self.static_pending = self.static_pending, None
        if pending is not None:
            # A route is its prefix and next hop: typing the pair again
            # changes that route rather than adding a second one (#436).
            key, desc = pending
            self.remove_static_route(key)
            self.candidate.routing.append(line)
            if desc:
                self.candidate.static_descriptions[key] = desc
            return True
        self.candidate.routing.append(line)
        # `address-family` is a block, and leaving it out was not an omission
        # anybody noticed - it was invisible because everything in the routing
        # section is passed through and still reached FRR.
        #
        # What was wrong is what the operator was told: `exit` inside an
        # address-family popped the enclosing `router bgp`, so the next line
        # typed went somewhere they did not intend, with nothing on screen
        # saying so. That is exactly the failure the prompt work exists to
        # prevent (#150).
        if opens_frr_block(line):
            # Naming a loopback is what CREATES it, the way naming a
            # sub-interface is - there is nothing else on the box to create it
            # from. Recorded on the way into the block, because the statements
            # typed inside it are about an interface that does not exist yet.
            if line.startswith("interface ") and _is_loopback(line.split()[1]):
                self.candidate.loopbacks.add(line.split()[1])
            # What NESTS is FRR's grammar, not this VTY's stack discipline.
            # `address-family` nests in the `router` above it, and a second
            # one replaces the first; every other block is a top-level context
            # in FRR, which is where `interface Gi0/0/0` typed inside `router
            # bgp` actually lands. Letting the prompt claim a nesting FRR does
            # not have leaves `show this` looking for a block nothing holds -
            # and `block_paths` reads the very same rule (#180).
            if line.startswith("address-family "):
                while (self.frr_ctx
                       and self.frr_ctx[-1].startswith("address-family ")):
                    self.frr_ctx.pop()
            else:
                self.frr_ctx = []
            self.frr_ctx.append(line)
        return True

    def dns_statement(self, w):
        """
        `dns server <address>` and `no dns server <address>` (#437).

        The same predicate and the same limit the file parser applies, so a
        resolver the prompt refuses is one a saved file cannot carry. A
        server typed twice is one server. The fourth is refused rather than
        kept: glibc asks three and ignores the rest, so it would be shown and
        never asked.
        """
        neg = w[0] == "no"
        args = w[3:] if neg else w[2:]
        if len(args) != 1:
            self.err("usage: [no] dns server <address>")
            return True
        why = dns_error(args[0])
        if why:
            self.err_prose(*why)
            return True
        servers = self.candidate.dns_servers
        if neg:
            if args[0] in servers:
                servers.remove(args[0])
            return True
        if args[0] in servers:
            return True
        if len(servers) >= DNS_MAX:
            self.err_prose(f"dns server: this box already has {DNS_MAX}",
                           "a resolver past the third is never asked, so it "
                           "is not taken; 'no dns server <address>' makes "
                           "room")
            return True
        servers.append(args[0])
        return True

    def owns_word(self, w):
        """
        The words a configuration level owns, wherever the operator is
        standing. True when it handled the line.

        ONE PLACE, called by every sub-mode as well as by `do_config`. The
        alternative is four copies of two branches, and the copy that gets
        forgotten is the one in the mode somebody is standing in when they
        need it - which is exactly how #400 happened: the probes were
        dispatched in operational mode and in no other.
        """
        if w[0] in ("top", "up"):
            self.do_navigate(w)
            return True
        if w[0] in PROBE_TOOLS:
            self.do_probe(w[0], w[1:])
            return True
        return False

    def level_words(self):
        """
        What `?` offers for the words a level owns, at THIS level.

        `top` and `up` are left out at the top of configuration mode, where
        they are refused - a `?` that offers a word the next keystroke refuses
        teaches the operator that the list is a guess, and the list is the only
        documentation an operator reads while typing.

        The probes are offered at every level, including the top, because they
        are answered at every level. Their descriptions come FROM the tree that
        already carries them, so the two readings of `ping` cannot disagree.
        """
        words = dict(LEVEL_WORDS) if self.config_depth() else {}
        for tool in PROBE_TOOLS:
            words[tool] = TREE["operational"][tool][0]
        return words

    def config_depth(self):
        """
        How many levels below `[edit]` the operator is standing.

        An FRR block is a level and so is a sub-mode - a pool, an account, an
        SNMP user - and they do not nest: a sub-mode is entered from the top,
        so a box is in one or the other and never in both. Counting them the
        same way is what lets `up` and `top` be one pair of words rather than
        four.

        `BLOCK_MODES` is the list, and it is the list `?` already reads to know
        where `show this` works. A second copy of it here is how this would
        answer 1 inside an SNMP user that spells its mode `snmp-user` and 0
        inside the same user spelled any other way - a `top` that silently
        stayed put.
        """
        return (len(self.frr_ctx) + len(self.nat_ctx)
                + (1 if self.mode in BLOCK_MODES else 0))

    def leave_one_level(self):
        """
        Leave one level, writing the block's own `exit` where FRR needs it.

        THE `exit` IS NOT COSMETIC. The candidate's routing section is a flat
        list, and `block_paths` decides which block a statement belongs to by
        reading that list forward - so a block that was opened and never
        closed swallows whatever is typed next, and the statement goes to FRR
        INSIDE the block just left (#180). `router static` is the exception it
        already is everywhere else: FRR has no such block, so there is nothing
        to close.
        """
        if self.mode in BLOCK_MODES:
            self.mode = "config"
            self.pool = None
            self.user = None
            self.snmp_user = None
            return
        if self.nat_ctx:
            self.nat_ctx.pop()
            return
        ctx = self.frr_ctx.pop()
        if ctx != STATIC_BLOCK:
            self.candidate.routing.append("exit")

    def do_navigate(self, w):
        """
        `top` and `up [n]`: move in the hierarchy without leaving it.

        NEITHER OF THESE IS `exit`. The candidate survives, configuration mode
        survives, and the operator lands where they said - which is the whole
        difference between a navigation word and a way out.

        They exist because the prompt invites them. `[edit router bgp 65000]`
        is read as JunOS by anybody who has met JunOS, and in JunOS these two
        words mean exactly this. Before #400 they were not statements here, so
        they fell into the passthrough, were staged for FRR, and the next
        commit was refused by `vtysh` - on a box where an address was never
        applied twice over because of it.
        """
        depth = self.config_depth()
        if w[0] == "top":
            # Silent when there is nothing to leave. `top` says "put me at the
            # root", and a box that is already there has done what was asked.
            while self.config_depth():
                self.leave_one_level()
            return
        if len(w) > 2:
            self.err("usage: up [<levels>]")
            return
        levels = 1
        if len(w) == 2:
            if not is_number(w[1]) or w[1] == "0":
                # Their word goes in the PROSE and not the verdict, which is
                # the rule #363 states: a verdict carrying a word the operator
                # chose has no width at all, and this one is 36 columns
                # without it and says the same thing.
                self.err_prose("up: a number of levels, 1 or more",
                               f"'{w[1]}' is not one of those; 'up' on its "
                               "own is one level")
                return
            levels = int(w[1])
        if depth == 0:
            self.err_prose("up: already at the top of the configuration",
                           "'exit' leaves configuration mode; this word only "
                           "moves inside it")
            return
        if levels > depth:
            # REFUSED RATHER THAN CLAMPED, and this is the one place this VTY
            # will not behave like JunOS. Leaving more levels than were asked
            # for puts the operator somewhere they did not type, and the next
            # statement lands there - which is the same class of surprise this
            # issue is about. `top` is one word away and says it exactly.
            # ONE TEMPLATE, "level(s)" and all: this VTY's messages are read
            # back out of the source by test/test_wiki_screens.py, so a
            # verdict composed out of two conditional halves is a verdict a
            # page can quote and nothing can check the quotation of.
            self.err_prose(f"up {levels}: there are only {depth} level(s) "
                           "below the top here",
                           "'top' goes all the way up in one word")
            return
        for _ in range(levels):
            self.leave_one_level()

    def do_snmp_user(self, line):
        w = line.split()
        u = self.snmp_user
        if self.owns_word(w):
            return True
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
            # Two spellings on two rows rather than one sentence of 88
            # columns on a console of 79 (#362). `err` folds nothing, so a
            # shape an operator is meant to retype must not be handed to it
            # inside a sentence: the verdict says which words this block
            # takes, and each shape is a row.
            self.err_prose("a v3 user takes 'auth' or 'priv'",
                           "auth <protocol> <passphrase>",
                           "priv <protocol> <passphrase>")
        return True

    def do_user(self, line):
        w = line.split()
        u = self.user
        if self.owns_word(w):
            return True
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
        elif w[0] == "role" and len(w) > 1:
            why = validate_role(u.name, w[1])
            if why:
                self.err_prose(*why)
            else:
                u.role = w[1]
        elif w[:2] == ["no", "ssh-key"] and len(w) > 2:
            key = " ".join(w[2:])
            u.keys = [k for k in u.keys if k != key and not k.startswith(key)]
        elif w[:2] == ["no", "role"]:
            why = validate_role(u.name, "")
            if why:
                self.err_prose(*why)
            else:
                u.role = ""
        elif w[:2] == ["no", "password"]:
            u.password = ""
        elif w[0] == "no":
            # Reached only when no negation handler above took the line, so
            # the word that is wrong is the one AFTER `no`. Naming `no` itself
            # - which is what the shared refusal below did - sends an operator
            # off to find out whether this block has a `no` at all, when it
            # has three of them (#224).
            if len(w) < 2:
                self.err("no <statement>: say which statement to remove "
                         "('no ?' lists them)")
            elif w[1] in TREE["user"]:
                self.err(f"usage: no {w[1]} "
                         f"{TREE['user']['no'][1][w[1]][1] or ''}".rstrip())
            else:
                self.err(f"unknown statement in user mode: {w[1]}")
        else:
            self.err(f"unknown statement in user mode: {w[0]}")
        return True

    def pool_range(self, name, value, what=""):
        """
        Raise unless `value` is inside the range `name` is offered with.

        A raise rather than a return, because do_pool's whole statement chain
        already runs inside one `try` and answers a ValueError with the
        operator's line - so a check that returned would be a second way of
        saying no in a method that already has one.
        """
        why = Pool.check_range(name, value, what)
        if why:
            raise ValueError(why)

    def say_frr_refused(self, lines):
        """
        After FRR refuses the candidate: where to look, and the way out.

        FRR's own words go above this, unchanged - it says which line it could
        not parse and nothing here can say it better. What it does NOT say is
        how the line got there, and that is the whole of #400: a word this VTY
        did not recognise was staged for FRR verbatim, so `top` became a
        routing statement, and the operator read "% Unknown command: top"
        about a word they typed to MOVE rather than to configure. Every commit
        after that failed the same way, on a box whose routing was fine.

        So the refusal names the statements this VTY passed through unread -
        the set a stray word always lands in - and then says the way out. The
        way out is the part that was missing: `commit` had already refused
        three times before anybody thought of it on the box this was filed
        from.
        """
        unread = unread_statements(lines)
        if unread:
            print_prose("these statements were handed to FRR unread, so a "
                        "word that is not a statement is one of them:")
            for stmt in unread[:UNREAD_NAMED]:
                # The statement as it stands, never wrapped: it is typed back
                # in or grepped for, which is the rule #252 states.
                print(f"      {stmt}")
            if len(unread) > UNREAD_NAMED:
                print(f"      ... and {len(unread) - UNREAD_NAMED} more")
        print_prose("'rollback 0' discards the candidate and leaves the "
                    "running configuration alone; 'show configuration' marks "
                    "every statement this VTY passed through unread")

    def do_pool(self, line):
        w = line.split()
        p = self.pool
        if self.owns_word(w):
            return True
        if w[0] in ("exit", "end"):
            self.mode = "config"
            self.pool = None
            # `exit` returns to `cgnat`, where the pool was entered; `end`
            # goes to the top, as it does from every level (#446).
            if w[0] == "end":
                self.nat_ctx = []
            return True
        # "show this" is most useful exactly here, and each sub-mode
        # dispatches its own lines - so without this it is refused in the one
        # place it exists for.
        if w[0] == "show":
            self.show_or_pass(line)
            return True

        try:
            if w[0] in ("inside", "outside"):
                # The same rule as the saved file's, from the same function.
                # A second copy is what lets the two doors disagree, which is
                # #417 - and `inside potato` reached a commit through this one
                # while the file's half only asked for a slash.
                why = prefix_error(w[0], w[1])
                if why:
                    # SAID and not raised, the way block-size above is: the
                    # chain answers a ValueError with "invalid argument: " in
                    # front of it, and these verdicts already carry a value of
                    # the operator's own length.
                    self.err_prose(*why)
                    return True
                (p.inside if w[0] == "inside" else p.outside).append(w[1])
            elif w[0] == "block-size":
                self.pool_range("block-size", w[1])
                # The range is one of TWO things `?` promises about this
                # number - "ports per block, power of two" - and the shape was
                # kept by nothing, so `block-size 100` reached the dataplane
                # and came back as "block size 100 must be a power of two" at
                # commit time, on a candidate the operator had already built
                # (#360).
                #
                # HERE and not in Config._pool_line(), which is where the next
                # person adding a constraint to this statement will reach,
                # because that is where the statement is modelled. That
                # function is also the reader for the configuration read back
                # out of the RUNNING dataplane, and a block size the box
                # already has is a fact rather than a request: a bound put
                # there made a box carrying `block-size 4` refuse every commit
                # (#357). A bound belongs where the request is made.
                #
                # Said rather than raised, because the chain's ValueError is
                # answered with "invalid argument: " in front of it and this
                # refusal is already 78 columns wide at its widest value.
                why = Pool.check_pow2("block-size", w[1])
                if why:
                    self.err(why)
                    return True
                p.block_size = str(int(w[1]))
            elif w[0] == "ports":
                # NOTHING bounded either end before this, and the cast is the
                # expensive half: port_min and port_max are u16 in bpa.api and
                # in bpa_pool_t, so `ports 70000 80000` was staged, saved and
                # arrived at the dataplane as 4464-14464. The pool that
                # commits is not the pool that was typed, and no refusal on
                # the far side can name 70000 because by then it is not the
                # number any more - which is #352's defect in a second
                # statement (#361).
                #
                # HERE and not in Config._pool_line(), for the reason written
                # beside block-size above: that function is also the reader
                # for the configuration read back out of the RUNNING
                # dataplane, and a port range the box already has is a fact
                # rather than a request. A bound put there turns a box whose
                # pool carries an odd range into a box that cannot be
                # committed at all, which is exactly what #357 was.
                #
                # Each end on its own, with `what` naming which one, because
                # "ports 70000 80000: expected 1-65535" would leave the
                # operator to work out which of the two is being refused.
                self.pool_range("ports", w[1], "min")
                self.pool_range("ports", w[2], "max")
                # A SECOND refusal and not a second spelling of the first,
                # because `ports 9000 100` breaks neither end's bound: it is
                # two legal port numbers in the wrong order, and the range is
                # the wrong thing to tell somebody about it. bpa_pool.c:148
                # catches it on the far side of commit, which is one step too
                # late - the candidate is already built.
                #
                # Raised like the two above, and not said with err: behind
                # "invalid argument: " the whole row measures 67 columns at
                # its widest pair, inside the 79 a serial console has, so it
                # needs neither err_prose nor a second door out of this chain.
                why = Pool.check_order("ports", w[1], w[2])
                if why:
                    raise ValueError(why)
                # The third thing that can be wrong with a port range - that
                # it is too narrow to hold one block - is NOT here. It needs
                # `block-size` as well, and block-size may be typed after
                # ports, so a check at this line would fire on a half-built
                # pool and refuse a legal editing order. Pool.block_fits()
                # answers it once, of the finished pool, in the commit check.
                p.ports = f"{int(w[1])} {int(w[2])}"
            elif w[0] == "policy":
                if w[1] not in ("interleaved", "blocked"):
                    raise ValueError("policy must be interleaved or blocked")
                p.policy = w[1]
            elif w[0] in ("vrf", "outside-vrf"):
                # The bound is HERE and not in _pool_line, because that
                # function also reads the configuration back out of the
                # running dataplane and a value the box already has is a fact
                # rather than a request (#357).
                self.pool_range(w[0], w[1])
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
                # Pool.RANGES, and not two numbers written out again here.
                # The typed side and the file side both bound these now, and
                # one owner is what keeps them the same bound - the pair that
                # had drifted was `block-stride`, refused above 65535 here and
                # offered as `<1-65535>` by `?`, which is neither end (#351).
                self.pool_range("timeout", w[2], w[1])
                p.timeouts[w[1]] = int(w[2])
            elif w[0] == "max-blocks":
                self.pool_range("max-blocks", w[1])
                p.max_blocks = int(w[1])
            elif w[0] == "block-stride":
                self.pool_range("block-stride", w[1])
                p.block_stride = int(w[1])
            elif w[0] == "reserve":
                self.pool_range("reserve", w[1])
                p.block_reserve = int(w[1])
            elif w[0] == "bypass":
                # The prefix first and by ITSELF, so the refusal arrives as
                # the two rows prefix_error writes rather than as one sentence
                # behind "invalid argument: " - which is 145 columns at an
                # IPv6 value. Everything else this statement can get wrong -
                # the direction, a repeat - is still _pool_line's, so there is
                # one reader for the statement and not two.
                if len(w) > 2:
                    why = prefix_error(f"bypass {w[1]}", w[2])
                    if why:
                        self.err_prose(*why)
                        return True
                why = Config._pool_line(p, " ".join(w))
                if why:
                    raise ValueError(why)
            elif w[0] == "utilisation-alarm":
                self.pool_range("utilisation-alarm", w[1])
                why = Config._pool_line(p, " ".join(w))
                if why:
                    raise ValueError(why)
            elif w[0] in ("max-sessions", "max-session-rate"):
                self.pool_range(w[0], w[1])
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
            elif w[0] == "no":
                # ONE branch for every negation, dispatched from POOL_NEGATE,
                # and it ends in a refusal. What stood here was an if/elif
                # chain with no `else`: twelve statements had a handler and
                # every other `no` fell out of the bottom of it, accepted in
                # silence, removing nothing, with `show this` still showing
                # the setting (#224). An operator typing `no block-size 512`
                # walked away believing they had taken it off.
                #
                # The refusal has to be here rather than in the outer `else`,
                # because the outer one would answer "unknown statement: no"
                # and `no` is not the unknown word.
                if len(w) < 2:
                    self.err("no <statement>: say which statement to remove "
                             "('no ?' lists them)")
                    return True
                h = POOL_NEGATE.get(w[1])
                if h:
                    why = h[2](p, w)
                    if isinstance(why, tuple):
                        self.err_prose(*why)
                    elif why:
                        self.err(why)
                elif w[1] in CGNAT_POOL:
                    # In the grammar, and cannot be absent: see the rule above
                    # POOL_NEGATE. Saying what to type instead is the point -
                    # "no" alone leaves the operator with a refusal and no
                    # next move.
                    hint = CGNAT_POOL[w[1]][1]
                    if isinstance(hint, dict):
                        hint = "|".join(hint)
                    self.err_prose(
                        f"{w[1]} cannot be removed",
                        "every pool has one, so there is no state without it",
                        f"set the value you want instead: "
                        f"{w[1]} {hint or ''}".rstrip())
                else:
                    self.err(f"unknown statement in pool mode: {w[1]}")
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
            "show cgnat history verify": "show bpa pool history verify",
            "show cgnat drops": "show bpa drops",
            "show cgnat alg": "show bpa alg",
            # How many identities this box holds and when it read them - never
            # an entry, here or anywhere (#21).
            "show cgnat identity": "show bpa identity",
        }
        if line in m:
            self.vpp(m[line])
            return
        # Before the forwarding below, which would otherwise hand `ha` to a
        # dataplane that has no such command.
        _ha = line.split()
        if _ha[:3] == ["show", "cgnat", "ha"]:
            self.show_ha(_ha[3:])
            return
        # The ones that carry an argument. Matched by prefix so the argument
        # travels through untouched; prismash is not the place to re-validate an
        # address the dataplane is about to parse anyway.
        # `show cgnat forward` is here rather than in the map above because it
        # takes an optional pool name, and the argument travels through
        # untouched for the same reason the other three do.
        for pfx in ("show cgnat subscriber", "show cgnat translation",
                    "show cgnat top", "show cgnat forward"):
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
        # Both numbers of the word. `show interfaces terse` is what an operator
        # arriving from a Juniper types, and being handed a parser error for
        # the plural on a VTY built to look like one is the opposite of the
        # point.
        if _w[:1] == ["show"] and _w[1:2] in (["interface"], ["interfaces"]):
            self.show_interfaces(_w[2:])
            return
        # Deliberately NOT `show hardware-interfaces`, which is VPP's own
        # command for the dataplane's ports. This one answers about the box.
        if _w[:2] == ["show", "hardware"]:
            self.show_hardware(_w[2:])
            return
        if _w[:2] == ["show", "route"]:
            self.show_route(_w[2:])
            return
        if _w[:2] == ["show", "bgp"]:
            self.show_bgp(_w[2:])
            return
        # `show ospf` is this product's spelling and `show ip ospf` is FRR's.
        # Both reach the same place, with one deliberate difference: bare
        # `show ip ospf` is a command an operator arriving from another vendor
        # types expecting the process summary, and it still gets exactly that,
        # while bare `show ospf` answers the question they actually came with.
        if _w[:2] == ["show", "ospf"]:
            self.show_ospf(_w[2:])
            return
        if _w[:3] == ["show", "ip", "ospf"]:
            self.show_ospf(_w[3:], bare_is_neighbors=False)
            return
        if _w[:2] == ["show", "isis"]:
            self.show_isis(_w[2:])
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
        print("\n".join(mark_unchecked(
            identity_redact(snmp_redact(redact(cfg.text().splitlines()))))))

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
            # In the words the pool is typed in under `cgnat` (#446): `pool
            # residencial`, not the model's `cgnat pool residencial`.
            lines = (cfg.pools[self.pool.name].lines()
                     if self.pool.name in cfg.pools else self.pool.lines())
            lines[0] = lines[0][len(CGNAT_LEVEL) + 1:]
            print("\n".join(lines))
            return
        # Standing in `nat` or `cgnat` with no pool open: that level's block,
        # as `show configuration` writes it.
        if self.nat_ctx and not self.frr_ctx:
            block = cfg.cgnat_block()
            if self.nat_ctx[-1] == CGNAT_LEVEL:
                block = [b[1:] for b in block[1:-1]]
            print("\n".join(block) if block else
                  "nothing is configured here yet")
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
            # Marked HERE as well as in `show configuration`, and this is the
            # one that matters most: `show this` is what an operator runs
            # while they are still typing, before the commit, and it is the
            # only way to read a block back at all (#98, #221). Telling them
            # at `show configuration` time that half of what they typed went
            # through unread is telling them after the fact.
            #
            # The header goes through the same walk as its contents. Standing
            # in a block this VTY has no grammar for - `vrf red`, a key chain
            # - the answer an operator needs is on the first line: nothing in
            # here was read, not just the statement they are looking at.
            header, n = mark_routing([block], self.frr_ctx[:-1])
            print(header[0])

            # An interface's own settings are staged in cfg.mtus, cfg.urpf,
            # cfg.descriptions and eight more fields, NOT in cfg.routing. This
            # branch was written for FRR blocks and never saw the fields the
            # VTY models itself, so it printed the header and stopped -
            # reporting a hierarchy that plainly had an MTU as empty (#108).
            #
            # An operator who runs "show this" and sees nothing concludes the
            # block is empty and configures accordingly, which makes an empty
            # answer worse than no command at all.
            if block.startswith("interface "):
                for line in cfg.interface_lines(block.split(None, 1)[1]):
                    print(line)
            # Redacted here too. `show this` inside a block reads the routing
            # lines straight out of the candidate, so a neighbour password
            # printed nowhere else was printed here.
            #
            # Marked AFTER redaction, on the same text the operator sees, the
            # way print_config does it. A password redacted to `****` still
            # walks the tree to the same answer, because the walk stops at
            # `password` - which is an argument keyword - and what follows is
            # the argument's business.
            body, more = mark_routing(
                redact(frr_block_lines(cfg.routing, self.frr_ctx,
                                      cfg.static_descriptions)),
                self.frr_ctx)
            for line in body:
                print(line)
            for line in unchecked_note(n + more):
                print(line)
            return

        # At the top of configuration mode, "this" is the whole candidate -
        # which is what `show configuration` already prints, and saying so is
        # better than printing it twice under two names.
        self.print_config(cfg)

    #: VPP's own null interface. It is on every box, it is always down, it
    #: cannot be configured and it forwards nothing. Named here because three
    #: different count lines have to leave it out and a fourth reader must not
    #: have to rediscover which string it is.
    NULL_INTERFACE = "local0"

    @classmethod
    def forwarding_count(cls, ifaces):
        """
        How many of these interfaces can forward, and the note that goes under
        any count printed from them.

        THE ONE HOLDER of that decision. `show interface` printed
        `len(ifaces)` in three places, and on a box with no NIC the dataplane
        reports exactly `local0`, so the first screen of 0.74.0 said "the
        dataplane has no interface to forward on" and, four lines below it,
        "1 interface(s) on the dataplane" (#306). Measured on a box with two
        NICs as well, which the issue had not: the same count read THREE, so
        an operator checking the cards they installed against what the box
        reports is off by one on every box, not only on the empty one.

        The count is of what can forward, and the wording says so, because the
        number an operator acts on is the number of cards they put in. Naming
        what is in the old count instead - "1 interface(s), including local0"
        - was the other candidate and was rejected: it leaves the operator
        doing arithmetic underneath a sentence that already answered the
        question, it leaves `show interface description` with a denominator
        that can never be reached (local0 takes no description), and it keeps
        a number in the screen that disagrees with the diagnosis above it. A
        count an operator acts on stops meaning anything when it counts
        something that does not count (#299).

        `local0` USED TO STAY LISTED with a note under the count explaining
        why the two disagreed, on the argument that hiding an interface the
        dataplane has is how a listing stops being the thing people reach for
        when something is off. The maintainer read that screen on a real box
        and asked for it to go (#403), and the argument does not survive the
        reading: it holds for interfaces the box HAS, and `local0` is an
        artefact of VPP rather than a port on the machine. The note existed
        only to carry it.

        So the screens list what an operator configured -
        `routes.operator_interfaces()` is that one decision - and this counts
        the rows they are printed under. A TAP named OUTRIGHT still answers,
        which is the half of the old argument that was about diagnosis.

        The note survives as a return value with nothing to say. Both count
        lines print whatever comes back, and a caller that has something to
        add under a count has somewhere to put it.
        """
        return sum(1 for name in ifaces if name != cls.NULL_INTERFACE), []

    @staticmethod
    def link_state(name, hardware):
        """
        The link state of an interface, or of its parent when it has none.

        A sub-interface is not hardware, so the dataplane reports no link for
        it - but its link IS its parent's, and there is nothing else it could
        be. Printing '?' there would blank the column on exactly the
        interfaces an ISP terminates subscribers on, which are all
        sub-interfaces.
        """
        row = hardware.get(name)
        if row is None and "." in name:
            row = hardware.get(name.split(".")[0])
        return row["link"] if row else "?"

    def show_interfaces_terse(self, ifaces, hardware, total=None):
        """
        One line per interface: admin state, link state, family, address.

        Admin and link are two columns because they are two facts and the
        difference between them is the fault. `show interface` reports the
        admin flag - what somebody configured - and an operator reading "down"
        there cannot tell whether a colleague ran `shutdown` or the fibre is
        cut. Up/down is a wire; down/down is a person. One "state" column
        cannot say which, and this is the listing people reach for when
        something is off, so it says both.
        """
        print(f"  {'Interface':<26} {'Admin':<6} {'Link':<6} {'Proto':<6} "
              "Local")
        mismatched = []
        for name in sorted(ifaces):
            e = ifaces[name]
            link = self.link_state(name, hardware)
            if e["state"] == "up" and link == "down":
                mismatched.append(name)
            rows = [("inet6" if ":" in a else "inet", a)
                    for a in e["addresses"]] or [("-", "-")]
            first = True
            for proto, addr in rows:
                print(f"  {name if first else '':<26} "
                      f"{e['state'] if first else '':<6} "
                      f"{link if first else '':<6} {proto:<6} {addr}")
                first = False
        # Both sides of the "N of M" come from the one holder, so a filtered
        # listing cannot end up comparing a forwarding count against a raw one.
        shown, note = self.forwarding_count(ifaces)
        if total is not None and len(total) != len(ifaces):
            whole, _ = self.forwarding_count(total)
            print(f"\n  {shown} of {whole} interface(s) that can forward are "
                  "shown")
        else:
            print(f"\n  {shown} interface(s) on the dataplane can forward")
        for line in note:
            print(line)
        if mismatched:
            # The one conclusion this view exists to make reachable without
            # being read for.
            print("  " + ", ".join(mismatched) + ": configured up, link "
                  "down - that is the wire or the far end, not this "
                  "configuration")

    def show_interfaces_description(self, ifaces, hardware):
        """
        What each interface is for, next to whether it is working.

        The description is the dataplane's own interface tag, read back from
        the box - so it survives a restart of this VTY and says the same thing
        to anyone who asks VPP directly.

        This command used to print the table and then a line saying the
        dataplane carries no descriptions. That was true when it was written
        and stopped being true when `description` started being stored as the
        tag; it was left saying so, which is worse than never having answered,
        because somebody reading it stops looking for the feature that exists.
        """
        descs = self.device_descriptions()
        print(f"  {'Interface':<26} {'Admin':<6} {'Link':<6} Description")
        for name in sorted(ifaces):
            print(f"  {name:<26} {ifaces[name]['state']:<6} "
                  f"{self.link_state(name, hardware):<6} "
                  f"{descs.get(name, '')}")
        # The denominator is the forwarding count for the same reason the
        # other two are (#306): counting local0 into it made a progress
        # figure the operator can never finish, because local0 takes no
        # description.
        countable, note = self.forwarding_count(ifaces)
        described = sum(1 for n in ifaces
                        if n != self.NULL_INTERFACE and descs.get(n))
        print(f"\n  {described} of {countable} interface(s) described")
        for line in note:
            print(line)
        if described < countable:
            # An empty column is a question, and it has an answer. Saying
            # nothing here is what made the old wrong sentence seem worth
            # printing in the first place.
            print("  set one with 'description <text>' inside 'interface "
                  "<name>'")

    #: A sparkline needs eight levels and a mark for nothing. The block
    #: characters are the readable ones; an appliance is also operated over a
    #: serial console that may be answering in ASCII, and a row of question
    #: marks would make the graph worse than no graph.
    #:
    #: Index 0 is a visible baseline rather than a space, because a blank
    #: reads as "no data" and the thing it means is "no traffic" - which on a
    #: link somebody is asking about is the answer, not the absence of one.
    BARS_UTF = "\u00b7\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
    BARS_ASCII = "_.:;+*#%@"

    def bars(self):
        enc = (getattr(sys.stdout, "encoding", None) or "").lower()
        return self.BARS_UTF if enc.startswith("utf") else self.BARS_ASCII

    def spark(self, values, full):
        """A series drawn against `full`; an empty bar is a real zero."""
        chars = self.bars()
        if not full:
            return chars[0] * len(values)
        out = ""
        for v in values:
            level = int(round((v / full) * (len(chars) - 1)))
            # Traffic that rounds to nothing is still traffic. Drawn at the
            # lowest level rather than at the baseline, because "a trickle"
            # and "silence" are different answers and on a graph scaled to a
            # 10G peak they would otherwise look identical.
            if v > 0:
                level = max(1, level)
            out += chars[max(0, min(len(chars) - 1, level))]
        return out

    @staticmethod
    def rate(bits):
        """bit/s, at the multiple an operator would have said it in."""
        for unit, div in (("Gbit/s", 10 ** 9), ("Mbit/s", 10 ** 6),
                          ("kbit/s", 10 ** 3)):
            if bits >= div:
                return f"{bits / div:.2f} {unit}"
        return f"{bits:.0f} bit/s"

    def show_interface_utilisation(self, args):
        """
        Sample the counters, twice or more, and graph what went past.

        `show interface` prints a counter, which is a total since boot. Nobody
        wants a total; they want to know whether the link is busy NOW, and the
        only way to that from a total is to take two of them and divide by the
        time between. So this command takes time to answer, and says how much
        before it starts rather than appearing to have hung.

        The elapsed time is MEASURED, not assumed from the sleep. A box under
        the load worth asking about is a box where a two-second sleep is not
        two seconds, and a rate computed from the nominal interval would be
        wrong in exactly the conditions the question gets asked in.

        "Utilisation" only means something against a capacity. When the
        dataplane reports a link speed, the bar is that interface's share of
        its own line rate and the percentage is real. When it does not - every
        virtual interface says "unknown" - the bar is scaled to the busiest
        sample in this run and the footer says so, because a percentage of a
        capacity the box does not know would be a number nobody could defend.
        """
        interval, count, names = 2.0, 5, []
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("interval", "count"):
                if i + 1 >= len(args):
                    self.err(f"{a} needs a number after it")
                    return
                try:
                    value = float(args[i + 1])
                except ValueError:
                    self.err(f"{a} needs a number, not '{args[i + 1]}'")
                    return
                if a == "interval":
                    if not 0.1 <= value <= 60:
                        self.err("interval must be between 0.1 and 60 seconds")
                        return
                    interval = value
                else:
                    if not 1 <= value <= 120:
                        self.err("count must be between 1 and 120")
                        return
                    count = int(value)
                i += 2
                continue
            names.append(a)
            i += 1

        _, hw = self.dp.run("show hardware-interfaces brief")
        hardware = routes.parse_hardware(hw)

        # print_prose and not print: the interval and the count are the
        # operator's, so this row grows with what they typed and the words
        # after it are the VTY's own - a fold would land inside them (#392).
        # Folded here, it lands between words wherever the value ends.
        print_prose(f"sampling every {interval:g}s, {count} time(s) - "
                    f"{interval * count:g}s in total; Ctrl-C stops early",
                    indent="  ")
        samples = []
        try:
            for n in range(count + 1):
                rc, out = self.dp.run("show interface")
                if rc != 0 or not out.strip():
                    self.err("the dataplane stopped answering part-way "
                             "through the sample")
                    return
                samples.append((time.monotonic(),
                                routes.parse_interface_counters(out)))
                if n < count:
                    time.sleep(interval)
        except KeyboardInterrupt:
            print()

        if len(samples) < 2:
            self.err_prose(
                "stopped before two samples, so no rate could be measured",
                "a rate is a difference between two counters; one reading is "
                "a total since boot")
            return

        series, drops, reset = {}, {}, False
        for (t0, c0), (t1, c1) in zip(samples, samples[1:]):
            dt = t1 - t0
            if dt <= 0:
                continue
            for name, now in c1.items():
                was = c0.get(name)
                if was is None:
                    # Appeared mid-sample. Its counter is a total since it was
                    # created, and reporting that as a rate would invent a
                    # spike out of an interface somebody just made.
                    continue
                step = []
                for key in ("rx bytes", "tx bytes"):
                    delta = now.get(key, 0) - was.get(key, 0)
                    if delta < 0:
                        # `clear interfaces` ran while this was sampling.
                        reset = True
                        delta = 0
                    step.append(delta * 8 / dt)
                series.setdefault(name, []).append(tuple(step))
                lost = now.get("drops", 0) - was.get("drops", 0)
                if lost > 0:
                    drops[name] = drops.get(name, 0) + lost

        if names:
            series = {n: v for n, v in series.items()
                      if any(_starts(n, w) for w in names)}
            if not series:
                print("  no interface matches")
                return

        peak = max((max(max(rx, tx) for rx, tx in v)
                    for v in series.values() if v), default=0.0)
        speeds = {n: (hardware.get(n) or {}).get("speed")
                  or (hardware.get(n.split(".")[0]) or {}).get("speed")
                  for n in series}
        rated = any(speeds.values())

        width = len(samples) - 1
        # 13, not 12: "675.56 Mbit/s" is thirteen characters, and a column
        # that the widest real value overflows is a column that stops lining
        # up exactly on the box that is busy.
        head = (f"\n  {'Interface':<26} {'rx':>13} {'tx':>13}  "
                f"{'rx history':<{max(width, 10)}} tx history")
        print(head)
        for name in sorted(series):
            steps = series[name]
            if not steps:
                continue
            rx_now, tx_now = steps[-1]
            full = speeds.get(name) or peak
            print(f"  {name:<26} {self.rate(rx_now):>13} "
                  f"{self.rate(tx_now):>13}  "
                  f"{self.spark([s[0] for s in steps], full):<{max(width, 10)}} "
                  f"{self.spark([s[1] for s in steps], full)}")

        print()
        if rated:
            print("  bars are each interface's share of its own line rate")
            for name in sorted(series):
                speed = speeds.get(name)
                if not speed or not series[name]:
                    continue
                rx_now, tx_now = series[name][-1]
                print(f"    {name:<24} {max(rx_now, tx_now) / speed * 100:5.1f}"
                      f"% of {self.rate(speed)}")
            # A box can have both kinds at once - a NIC that reports its speed
            # and a loopback that does not - and one footer claiming every bar
            # is a share of a line rate would be wrong about half the table.
            unrated = sorted(n for n in series if not speeds.get(n))
            if unrated and peak:
                print(f"  the rest have no link speed, so their bars are "
                      f"scaled to {self.rate(peak)}: " + ", ".join(unrated))
        elif peak:
            print(f"  bars are scaled to {self.rate(peak)}, the busiest "
                  "sample in this run -")
            print_prose("the dataplane reports no link speed for these "
                        "interfaces, so there is no line rate to be a share "
                        "of", indent="  ")
        else:
            print("  nothing passed any interface during the sample")
        for name in sorted(n for n in drops if n in series):
            print(f"  {name} dropped {drops[name]} packet(s) while this was "
                  "sampling")
        if reset:
            print_prose("a counter went backwards - something cleared the "
                        "interfaces mid-sample; that step reads as zero",
                        indent="  ")

    def show_one_interface(self, name, ifaces):
        """
        Everything the box knows about one interface, for a fault report.

        The counters are the point. The rest of this is available from the
        table; "up, and dropping" is not, and it is what the operator was
        calling about.
        """
        e = ifaces[name]
        print(f"  {name}")
        print(f"    state       {e['state']}")
        print(f"    mtu         {e['mtu']}")
        print(f"    addresses   {', '.join(e['addresses']) or '-'}")
        if e["host"]:
            print(f"    linux       {e['host']}")
        vrf = self.device_vrfs().get(name)
        print(f"    vrf         {vrf if vrf else '0 (default)'}")
        mac = self.device_macs().get(name)
        if mac:
            print(f"    mac         {mac}")
        desc = self.device_descriptions().get(name)
        if desc:
            print(f"    description {desc}")
        urpf = self.device_urpf().get(name)
        print(f"    urpf        {urpf or 'off'}")

        rc, feat = self.dp.run(f"show interface features {name}")
        if rc == 0:
            on = "in2out" if "bpa-in2out" in feat else (
                 "out2in" if "bpa-out2in" in feat else None)
            print(f"    cgnat       {on or 'not attached'}")

        # THE VIRTUAL ROUTER, on the screen that answers "what is this port".
        # An operator looking at one interface during a fault is looking here,
        # and whether this port is holding a service address is the first
        # thing that matters about a box in a pair - it is the difference
        # between "the subscribers are on this box" and "they are on the other
        # one". `show cgnat ha` answers it for the whole box; this answers it
        # for the port they are already looking at.
        #
        # The ROLE and not the configuration: what a virtual router is
        # configured to be is in `show this` and in the saved file, and what
        # it IS can only be read from the dataplane.
        for row in self.device_vrrp_state():
            if row["interface"] != name:
                continue
            # The role on one row and each address on its own, rather than a
            # list joined into the first: a slot holding "however many
            # addresses this router stands for" has no width, and a row whose
            # width nobody can state is a row that folds inside an address at
            # whatever the operator's window happens to be (#355, #363).
            print(f"    vrrp {row['vr_id']:<6} {row['state']}")
            for addr in row["addresses"]:
                print(f"                {addr} on {row['mac']}")

        # Straight from the dataplane, uninterpreted. A counter this VTY
        # summarised would be a counter an operator could not compare with
        # what `vppctl` says, and during a fault they will look at both.
        rc, one = self.dp.run(f"show interface {name}")
        if rc == 0:
            # [1:] removes the COLUMN HEADER and nothing else. There used
            # to be a second [1:] below, which threw away the interface's own
            # row - and that row is where VPP puts the first counter, so
            # `rx packets` was silently missing from every reading (#280). The
            # list still looked complete: it began at `rx bytes` and ran to
            # `drops`, with nothing on the screen saying a row had gone.
            rows = []
            for line in one.splitlines()[1:]:
                if not line.strip():
                    continue
                # The interface's own row carries its name, index, state and
                # MTU in front of the first counter. Those four are printed
                # above already, so only the counter is kept - found by the
                # MTU field, which is the last of them and the only one with a
                # shape of its own. An interface with no counters has nothing
                # after the MTU, and contributes no row rather than printing
                # its own identity as though it were a reading.
                m = re.match(r"\S+\s+\d+\s+\S+\s+\d+/\d+/\d+/\d+\s*(.*)$",
                             line)
                rows.append((m.group(1) if m else line).strip())
            rows = [r for r in rows if r]
            if rows:
                print("    counters")
                for row in rows:
                    print(f"      {row}")
            else:
                print("    counters    all zero")

    #: Where the tuner writes what it decided, and where it writes it down.
    STARTUP_CONF = os.environ.get("PRISMA_STARTUP_CONF", "/etc/vpp/startup.conf")
    TUNE_ENV = os.environ.get("PRISMA_TUNE_ENV", "/etc/prisma-routing/tune.env")

    def why_no_dataplane_interface(self):
        """
        Why the dataplane has no interface but VPP's own loopback.

        `local0` alone is the truth and it is not an answer. It has several
        causes, each needing different work from the operator, and the two
        files that know are not where they are looking - which is why this was
        reported twice and answered both times with "run these commands and
        tell me". That answer is the defect: the box should not need an
        interrogation to say what it decided.

        Reads the same sources, in the same order, and says the same words as
        the boot self-test. Two diagnoses of one fact that disagree are worse
        than one.
        """
        try:
            with open(self.STARTUP_CONF) as f:
                conf = f.read()
        except OSError:
            return []                   # not an appliance: nothing to explain
        if "plugin dpdk_plugin.so { disable }" in conf:
            # THE SAME STARTUP FILE, TWO DIFFERENT BOXES, and until #305 this
            # branch gave both of them the same advice.
            #
            # On an INSTALLED box's first boot the tuner has written a profile
            # and asked for pages that the next boot picks up, so "reboot and
            # come back" is exactly right.
            #
            # On the MEDIUM the dataplane is off deliberately and permanently.
            # Nothing here survives a reboot, so the tuning can never be
            # applied and the pages never reserved, and a reboot comes back to
            # this same screen having thrown the session away. The medium's own
            # banner says so two lines above this command, and this said the
            # opposite (#305).
            #
            # running_from_live_medium() was already in this file and this
            # message did not ask it.
            #
            # NOT the same test appliance/selftest uses, which is worth being
            # exact about because an earlier version of this comment said it
            # was. The self-test asks `-d /run/live/medium || -f
            # /run/live/overlay`; this asks for the squashfs AND an executable
            # installer. They agree on every box either has been run on, and
            # they are two tests: a root with the overlay and no squashfs is
            # live to one and installed to the other. Neither has been seen,
            # and saying they are one test would be the second copy this file
            # spends its comments warning about.
            if running_from_live_medium():
                return ["the dataplane is off on the installation medium, and "
                        "a reboot does not change that: nothing here survives "
                        "one.",
                        "Hugepages are reserved by the tuning an INSTALLED box "
                        "applies at its first boot. Type 'install' to put "
                        "Prisma Routing on a disk, and the card is claimed "
                        "there.",
                        "'show hardware' lists the cards this box has and "
                        "what will happen to each one when it is installed."]
            said = ["DPDK is off until hugepages exist, so no NIC can be "
                    "claimed yet.",
                    "This is the first-boot profile. Reboot once the tuning "
                    "has been applied and the pages reserved."]
            # And whether a reboot can end anywhere. "Reboot and come back"
            # is the right advice only if there is a card to claim afterwards,
            # and on a box whose only NIC carries the default route naming
            # that card is a decision with a reboot and a rollback in it.
            # Saying so here costs nothing and saves two reboots and a naming
            # attempt to learn it (#289, #303).
            said += self.one_nic_note()
            said.append("'show hardware' lists the cards this box has and "
                        "what will happen to each one at that boot.")
            return said
        if "no-pci" in conf:
            named = ""
            try:
                with open(self.TUNE_ENV) as f:
                    for line in f:
                        if line.startswith("PRISMA_DATAPLANE_IF="):
                            named = line.split("=", 1)[1].strip().strip('"')
            except OSError:
                pass
            if not named:
                # The remedy is a command in THIS VTY now. It used to be a
                # path and an editor, which is the defect #283 was filed for:
                # the product's argument is that the operator never leaves
                # here, and advice that sends them to Linux contradicts it in
                # the one message they read when the box forwards nothing.
                return ["no interface was named, so VPP was given no NIC.",
                        "Name it here: 'configure', then 'dataplane "
                        "interface <name>',",
                        "then 'commit'. A name this box has not got is "
                        "refused, and the",
                        "refusal says which ones it has.",
                        "'show hardware' is where the names come from: it "
                        "lists this box's cards as Linux has them, and marks "
                        "the one carrying the default route."]
            # THREE REASONS, and the third changed with #385. It used to be
            # the tuner's refusal of the card carrying the default route;
            # that refusal is gone, and a named card is claimed whichever
            # route it carries. What can still leave a named card with Linux
            # is a way back the tuner could not read off it.
            return [f"'{named}' was named and VPP was given no NIC.",
                    "Either it does not exist on this box, or it could not be "
                    "resolved to a PCI address,",
                    "or it carries the default route and no way back for it "
                    "could be read,",
                    "so it stays with Linux rather than leave this box "
                    "unreachable."]
        if "dev 0000:" in conf:
            return self.why_named_nic_absent(conf)
        return []

    #: Where Linux says which driver owns a PCI device, and which drivers exist.
    PCI_DEVICES = os.environ.get("PRISMA_PCI_DEVICES", "/sys/bus/pci/devices")
    PCI_DRIVERS = os.environ.get("PRISMA_PCI_DRIVERS", "/sys/bus/pci/drivers")

    #: The drivers DPDK can take a device through. vfio-pci is the one to use;
    #: the other two are older and are recognised so a box already set up that
    #: way is not told it is wrong.
    DPDK_DRIVERS = ("vfio-pci", "uio_pci_generic", "igb_uio")

    #: The management network the installer recorded. The tuner reads this
    #: same file to decide which card to reserve for Linux (#378), and
    #: appliance/selftest reads it to say what became of that card - so this
    #: is the third reader of one answer and not a fourth opinion.
    MGMT_CONF = os.environ.get("PRISMA_MGMT_CONF",
                               "/etc/network/interfaces.d/prisma-routing")

    def reserved_for_linux(self):
        """
        The card this box was installed with a management network on, or "".

        The words on that line are interfaces(5)'s and not this product's, so
        the name comes out of column two rather than being matched against a
        string this VTY could reword - the same reading appliance/selftest and
        appliance/prisma-routing-tune do of the same file.
        """
        try:
            with open(self.MGMT_CONF, encoding="utf-8") as f:
                for line in f:
                    w = line.split()
                    if len(w) >= 2 and w[0] == "iface":
                        return w[1]
        except (OSError, UnicodeDecodeError):
            pass
        return ""

    def claim_note(self, nics):
        """
        What the dataplane will claim on this box, and why, as prose lines.

        THE RULE IN ONE SENTENCE, because an operator has to be able to
        predict it: every card this box has, except one reserved for Linux,
        and never the last one. A box that claimed a card without being asked
        and said nothing about it is worse than one that asked.

        Said in the FUTURE, deliberately. The dataplane claims its cards when
        it starts, so on a box where the configuration has just changed this
        describes the next start and not the present - which is the same thing
        `dataplane interface` already tells the operator at the commit.

        AND THE TENSE WAS NOT ENOUGH (#405). The maintainer put a second NIC
        in a running box, read "The dataplane claims all 1" here, and was then
        told by the VTY that the box has no such interface. Both sentences
        were true and the pair of them is not readable: "claims" is the rule,
        the card is not claimed yet, and nothing on the screen said when it
        would be. `waiting_note()` says that, under this, naming the cards it
        is about - which is the only line on the screen an operator can act
        on, and the action is a reboot.
        """
        named = self.read_dataplane_if()
        if named:
            return [f"'dataplane interface' names '{named}', so the dataplane "
                    f"claims that card and no other.",
                    "Remove it with 'no dataplane interface' to let the box "
                    "choose again."]
        if not nics:
            return ["There is no card here for the dataplane to claim."]
        reserve = self.reserved_for_linux()
        have = [n["name"] for n in nics]
        if reserve and reserve in have and len(have) > 1:
            return [f"The dataplane claims {len(have) - 1} of them. It claims "
                    f"every card this box has except one reserved for Linux, "
                    f"and never the last one.",
                    "Reserved for Linux:",
                    f"  {reserve}",
                    "It is the management network this box was installed "
                    "with.",
                    "'dataplane interface <name>' claims that card alone "
                    "instead."]
        if reserve and reserve in have:
            # THE NARROWING, and the box it is about is the commonest there
            # is. A reserve allowed to take the only card leaves a VM
            # installed, correct and unable to forward, which is the state
            # this whole change exists to end.
            return ["The dataplane claims it. It is the only card this box "
                    "has, and a reserve never takes the last card.",
                    "Its address moves onto the linux_cp pair the dataplane "
                    "keeps for it, so this box is reached THROUGH the "
                    "dataplane rather than beside it. The card keeps its MAC, "
                    "so nothing upstream has to learn a new one."]
        if reserve:
            return [f"The dataplane claims all {len(have)}. '{reserve}' was "
                    f"reserved for Linux when this box was installed and is "
                    f"not here now.",
                    "'dataplane interface <name>' claims one card alone "
                    "instead."]
        return [f"The dataplane claims all {len(have)}. No card is reserved "
                f"for Linux: this box was installed with no management "
                f"network, so it is reached at its console.",
                "'dataplane interface <name>' claims that card alone "
                "instead."]

    def waiting_note(self, nics, held):
        """
        The cards Linux has that the dataplane has not taken yet, as prose.

        `held` is what `show interface` reports, or None when the dataplane
        could not be asked - and None answers nothing here, because "the
        dataplane holds no card" and "the dataplane did not answer" are
        different boxes and only one of them is waiting for a boot.

        THE CARD IS STILL IN LINUX, which is the whole signal: the tuner
        writes a `dev` stanza per claimed card into startup.conf and VPP is
        started with an EAL allowlist naming exactly those, so a card added
        after that is not merely unclaimed - it is excluded from the process
        and no amount of configuring reaches it. The tuner runs on every boot
        and computes the stanza from the hardware it finds, so the next boot
        claims it. That is #336's decision and it is the right one: claiming a
        card moves it to a userspace driver, and a box that did that to itself
        on a timer would lose the card it is reached over while nobody was
        watching.

        So what is missing is not a mechanism, it is a sentence. Empty when
        every card is accounted for, because a line on every boot is a line
        nobody reads.
        """
        if held is None or not nics:
            return []
        waiting = sorted(n["name"] for n in nics
                         if n["name"] not in set(held))
        if not waiting:
            return []
        return ["The dataplane does not hold " + ", ".join(waiting)
                + " yet: a card is claimed when the dataplane starts, and "
                  "the cards it claims are computed on that boot.",
                "Until this box is rebooted the dataplane cannot be given "
                "one: it is started with an allowlist naming the cards it had "
                "at boot, so a card added since is outside the process "
                "entirely.",
                "'system reboot' does it, and says what it costs first."]

    def one_nic_note(self):
        """
        What a box whose ONLY card is its management path has to do, or "".

        Only ever ADDS to an explanation. A box with a spare card is told
        nothing extra, because a line on every boot is a line nobody reads.

        This used to say the box needed a second NIC before it could forward.
        That was true of the product and it was a dead end for the operator: a
        VM with one virtual NIC is how most people first meet this, and being
        told to add hardware is not an answer they can act on (#303). Then it
        said what to type, and promised a confirmed commit that #378 had
        already removed. Since #378 the box claims the last card by itself,
        so what this says now is what will happen and where the address goes
        - nothing to type, nothing to confirm (#385).

        Reads host_nics() and default_route_nic(), which are what
        `show hardware` prints and what the tuner decides on, so this cannot
        describe a card those two would refuse.
        """
        nics = self.host_nics()
        if not nics:
            return ["This box has no PCI NIC Linux can see, so there is "
                    "nothing to give the dataplane whatever the tuning does."]
        default = self.default_route_nic()
        usable = [n["name"] for n in nics if n["name"] != default]
        if usable or not default:
            return []
        return [f"This box has one NIC, '{default}', and it carries the "
                "default route: it is the path this box is reached on.",
                "The dataplane claims it at its next start whether or not it "
                "is named, because a reserve never takes the last card, and "
                "the address moves onto the linux_cp pair the dataplane keeps "
                "for it: the box is then reached THROUGH the dataplane rather "
                "than beside it.",
                "Nothing is armed and nothing has to be confirmed."]

    def why_named_nic_absent(self, conf):
        """
        A NIC is named in startup.conf and the dataplane does not have it.

        This used to say "the usual cause is the NIC still bound to a kernel
        driver". That is a guess printed as a finding, and the box can simply
        look: Linux says which driver owns the device, and whether a driver
        DPDK can use is loaded at all. docs/context.md 1.4 - a counter is not
        evidence - and neither is a likely explanation (#283).

        It matters because the three states read identically on the screen and
        need different work. Nothing in `appliance/` binds a NIC or loads
        vfio-pci, so an operator who did exactly what the self-test told them -
        name the interface - lands in the second state with no way to tell it
        from the first.
        """
        m = re.search(r"dev (0000:[0-9a-fA-F:.]+)", conf)
        if not m:
            return []
        addr = m.group(1)
        said = [f"'{addr}' is named in the startup configuration and the "
                f"dataplane does not have it."]

        driver = None
        link = os.path.join(self.PCI_DEVICES, addr, "driver")
        if os.path.islink(link):
            driver = os.path.basename(os.path.realpath(link))

        if driver in self.DPDK_DRIVERS:
            # Bound the right way, so the cause is somewhere this cannot see.
            # Said as that rather than left out: an operator who has already
            # done the binding needs to be told it is not the binding.
            said.append(f"It is bound to {driver}, which DPDK can use, so the "
                        f"binding is not the problem.")
            # NAMED AS A COMMAND THAT EXISTS (#308). This said "'show log'",
            # and this VTY has no `show log`: the tree offers `show logging`,
            # which is FRR's system log and says nothing about a NIC the
            # dataplane refused. It was the only occurrence of that spelling
            # in the product, and it printed on the one screen an operator
            # reaches when the box forwards nothing AND the binding is already
            # right - so the next thing they type is a command the box has not
            # got.
            #
            # appliance/selftest reaches the same state and says "The VPP
            # journal says why", naming journalctl elsewhere. One account.
            said.append("'journalctl -u vpp.service' says why the dataplane "
                        "refused it.")
            return said

        have = [d for d in self.DPDK_DRIVERS
                if os.path.isdir(os.path.join(self.PCI_DRIVERS, d))]
        if driver:
            said.append(f"It is bound to '{driver}', which DPDK cannot take it "
                        f"through.")
        else:
            said.append("No driver is bound to it at all.")
        if have:
            said.append(f"{have[0]} is loaded on this box, and nothing has "
                        f"bound the device to it.")
        else:
            said.append("No driver DPDK can use is loaded: none of "
                        + ", ".join(self.DPDK_DRIVERS) + ".")
        return said

    #: Where Linux lists this box's own interfaces, and where it says which
    #: one carries the default route. Read as files rather than shelled out to
    #: `ip link`, which is one of the two tools this VTY exists to keep the
    #: operator out of, and overridable so the reading can be driven against a
    #: tree built for the purpose.
    SYS_CLASS_NET = os.environ.get("PRISMA_SYS_CLASS_NET", "/sys/class/net")
    PROC_NET_ROUTE = os.environ.get("PRISMA_PROC_NET_ROUTE", "/proc/net/route")

    @staticmethod
    def _sysfs(path):
        """One sysfs attribute, or None when it is not there to be read."""
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            # Not an error worth a message. `speed` on a NIC whose link is
            # down raises EINVAL in the kernel rather than returning a value,
            # and a driverless device has no attributes at all.
            return None

    def default_route_nic(self):
        """
        The interface carrying the default route, or None.

        Same file and same field as `appliance/prisma-routing-tune`, which
        reads it to compute the way back for this card on the boot that
        claims it (#378). The tuner used to REFUSE this NIC (#123), and this
        screen was written to say which one it was BEFORE the operator chose;
        the refusal went with #385 and the mark stays, because what happens
        to the card the box is reached on is still the one thing on this
        screen an operator has to know before typing.
        """
        try:
            with open(self.PROC_NET_ROUTE) as f:
                for line in f.readlines()[1:]:
                    w = line.split()
                    # Iface Destination Gateway Flags ... - a destination of
                    # 00000000 is the default route.
                    if len(w) > 2 and w[1] == "00000000":
                        return w[0]
        except OSError:
            pass
        return None

    def host_nics(self):
        """
        The box's physical NICs, as Linux has them.

        A NIC the DATAPLANE already owns is not in here, and that is not a
        defect of this reading: VPP takes the device off the kernel and
        /sys/class/net stops listing it. It is why `show hardware` says what
        the dataplane holds before it prints the list - a NIC that has been
        handed over and a NIC that was never in the box read identically
        otherwise, and one of those is a hardware fault.
        """
        out = []
        try:
            names = sorted(os.listdir(self.SYS_CLASS_NET))
        except OSError:
            return out
        for name in names:
            base = os.path.join(self.SYS_CLASS_NET, name)
            addr = pci_address_of(os.path.join(base, "device"))
            if not addr:
                # Not PCI at all: `lo`, a bridge, a tap, a virtio-mmio NIC.
                # None of them is a device DPDK could be given.
                continue
            driver = None
            link = os.path.join(base, "device", "driver")
            if os.path.islink(link):
                driver = os.path.basename(os.path.realpath(link))
            speed = self._sysfs(os.path.join(base, "speed"))
            out.append({
                "name": name,
                "pci": addr,
                "driver": driver,
                "dpdk": driver in self.DPDK_DRIVERS,
                "state": (self._sysfs(os.path.join(base, "operstate"))
                          or "unknown"),
                "mac": self._sysfs(os.path.join(base, "address")) or "-",
                # -1 is what the kernel writes for a link that is down, and it
                # is not a speed. Printed as unknown rather than as a number,
                # because a negative megabit is the kind of value somebody
                # reads past.
                "speed": None if speed in (None, "", "-1") else speed,
            })
        return out

    #: Where the binder lives, so a test can point this at a stub.
    BIND_NIC = "/usr/lib/prisma-routing/bind-dataplane-nic"

    def which_dpdk_driver(self):
        """
        What this box would move a card to, asked of the thing that decides.

        appliance/bind-dataplane-nic reads the IOMMU groups and chooses
        vfio-pci or uio_pci_generic, and it has a mode that answers without
        binding anything. Asking it is the whole point: the rule lives in one
        place, and a screen that predicted it from a copy would be right until
        the copies drifted (#328).

        A box where the binder cannot be reached gets a sentence that says so
        rather than a guess, because "vfio-pci" on a box that will fall back is
        worse than no sentence.
        """
        try:
            p = subprocess.run([self.BIND_NIC, "--which-driver"],
                               capture_output=True, text=True, timeout=10)
            out = (p.stdout or "").strip()
            if p.returncode == 0 and out:
                return out
        except (OSError, subprocess.SubprocessError):
            pass
        return ("a driver DPDK can use, chosen at boot from whether this box "
                "has an IOMMU; this VTY could not ask")

    def show_hardware(self, args):
        """
        The physical NICs of this box, from Linux, with the one that must not
        be given to the dataplane marked.

        This screen exists because the one that should answer cannot. On a
        fresh appliance VPP has been given no NIC, so `show interface` is
        empty - and that is exactly the moment an operator needs to see what
        NICs the box has in order to choose one. The only way to that list was
        to leave the VTY and read /sys or run `ip link`, and an appliance
        whose first setup step is a shell is an appliance with no VTY (#283).

        The DEFAULT ROUTE mark is as much the point as the list. Handing that
        NIC to the dataplane removes the box from the network, and the tuner
        refuses it for that reason (#123) - after the choice has been made.
        Marked here, the operator does not make it.
        """
        if args:
            self.err("show hardware: takes no arguments")
            return

        # What the dataplane holds, said ONCE and before the list, because a
        # NIC VPP owns is not under /sys/class/net and a list that quietly
        # omits it reads as a missing NIC.
        rc, out = self.dp.run("show interface")
        # KEPT, because the empty-table sentence below is about this answer
        # and used to assert one of its three values on all three boxes.
        # None is "the dataplane could not be asked", which is not [].
        held = None
        if rc != 0 or not out.strip():
            print_prose("the dataplane is not answering, so what it already "
                        "holds is not known: a NIC it owns would be missing "
                        "from the list below.", indent="  ")
        else:
            held = [n for n in routes.parse_interfaces(out) if n != "local0"]
            if held:
                # The path this was read from is deliberately NOT in the
                # sentence. It is a seam, so on a box under test it would
                # print a directory that exists nowhere, and an operator
                # reading a transcript cannot tell which is which.
                print_prose("the dataplane holds " + ", ".join(sorted(held))
                            + ". A NIC VPP owns is no longer listed by "
                            "Linux, so it is not in the list below.",
                            indent="  ")
            else:
                # The same words as `show interface` and the boot self-test,
                # from the same reading. Two diagnoses of one fact that
                # disagree are worse than one.
                why = self.why_no_dataplane_interface()
                if why:
                    print_prose("the dataplane has no interface to forward "
                                "on: " + why[0], indent="  ")
                    for line in why[1:]:
                        print_prose(line, indent="  ")
                else:
                    print_prose("the dataplane has no interface to forward "
                                "on.", indent="  ")
        print()

        nics = self.host_nics()
        if not nics:
            # NOT an empty table. "nothing to show" and "nothing is wrong"
            # look the same on a screen, and this screen is read on a box that
            # forwards nothing.
            # The virtio half of this sentence was FALSE from #286 onward and
            # nothing here noticed. pci_address_of() walks up from the netdev
            # to the PCI parent, so a virtio card in a VM resolves and is
            # eligible; this screen went on telling the one audience most
            # likely to read it - somebody trying the product in a VM - that
            # their box had nothing to offer. A wrong sentence on an empty
            # table is worse than an empty table, because it ends the search.
            #
            # AND WHICH OF THE TWO, rather than offering the reader a choice
            # the box has already made. This handed them "either the dataplane
            # already owns every one of them, which the lines above say, or
            # this box genuinely has no card Linux can see" - and the lines
            # above say that on ONE of the three boxes that get here. On the
            # other two they have just said the dataplane holds nothing, or
            # that it could not be asked at all, so the sentence pointed at
            # lines saying the opposite of what it claimed they said, and left
            # the operator to settle it from a screen that had. Same shape as
            # #331: one sentence over states that are nothing alike.
            if held:
                print_prose("no PCI NIC is visible to Linux on this box, and "
                            "the line above says why: the dataplane owns "
                            "them.", indent="  ")
            elif held is None:
                print_prose("no PCI NIC is visible to Linux on this box, and "
                            "the dataplane could not be asked what it has. A "
                            "card it had taken would be missing from this "
                            "reading too, so a box that handed every card "
                            "over and a box that never had one read alike "
                            "here: 'systemctl status vpp' says which this "
                            "is.", indent="  ")
            else:
                print_prose("no PCI NIC is visible to Linux on this box, and "
                            "the dataplane has none either, so there is no "
                            "card here to give it.", indent="  ")
            print_prose("A virtio card in a virtual machine does resolve to a "
                        "PCI address and is eligible, so a missing one here "
                        "is missing from Linux too.", indent="  ")
            return

        mgmt = self.default_route_nic()
        # The Link column is as wide as the widest thing it can hold, and the
        # width is ONE NAME used by the header and the rows so the two cannot
        # drift. It was 7, and `unknown` is seven characters: on a card whose
        # operstate is unknown the state and the speed ran together with no
        # space at all, `unknown-` and `unknown1000Mb/s` (#347). That is not
        # an odd value - host_nics() falls back to it when operstate cannot be
        # read, and the kernel writes it for any netdev whose driver sets no
        # carrier, which is every card on a box nobody has tuned yet.
        #
        # 15 is `lowerlayerdown` plus a space, and that is the longest of the
        # states RFC 2863 defines; `notpresent` is ten. Sized from the closed
        # set rather than from the values this box happens to have, because
        # the box that meets the long one is somebody else's.
        link_w = 15
        print(f"  {'Interface':<14}{'PCI':<15}{'Driver':<17}"
              f"{'Link':<{link_w}}Speed")
        for n in nics:
            speed = f"{n['speed']}Mb/s" if n["speed"] else "-"
            print(f"  {n['name']:<14}{n['pci']:<15}"
                  f"{n['driver'] or '(none)':<17}"
                  f"{n['state']:<{link_w}}{speed}")
            # The MAC is printed whole and on its own line with the driver's
            # verdict: it is what an operator matches against a label on the
            # port or a lease in the DHCP server, so it has to stay one
            # greppable token, and there is no room for it beside five other
            # columns inside the console.
            if n["dpdk"]:
                said = f"{n['driver']} is a driver DPDK can use"
            elif n["driver"]:
                # AND WHAT HAPPENS TO IT, which the old wording left out
                # (#328). "kernel driver: DPDK cannot take a NIC through it"
                # is true and, on a box with ONE NIC - a VM, which is how most
                # people first meet this product - it is the only thing said
                # about the only card. The reasonable reading is that the
                # hardware is unsuitable, and the operator stops there.
                #
                # It is the state EVERY uninstalled box is in. The binding is
                # what the box does when it claims the card, and saying so
                # turns a dead end into a step.
                said = f"{n['driver']}, a kernel driver"
            else:
                said = "no driver is bound to it at all"
            print(f"    {n['mac']}  {said}")
            if not n["dpdk"]:
                # ASKED, NOT RE-DERIVED. appliance/bind-dataplane-nic decides
                # this from the IOMMU groups at boot, and a second copy of
                # that rule here is how a screen comes to promise vfio-pci on
                # a box that will fall back to uio_pci_generic. There are
                # already two copies of the driver list; this does not add a
                # third.
                what = ("DPDK cannot take the card through it as it is"
                        if n["driver"] else
                        "DPDK needs a driver bound to the card")
                print_prose(f"{what}; when this card is given to the "
                            f"dataplane the box moves it to "
                            f"{self.which_dpdk_driver()}", indent="      ")
            if mgmt and n["name"] == mgmt:
                print("    *** carries the DEFAULT ROUTE ***")

        print(f"\n  {len(nics)} NIC(s) on this box, as Linux has them")
        # WHAT THE BOX DID AND WHY (#378). A box that silently ate a card is
        # worse than one that asked, and a box that silently SPARED one is a
        # card whose absence from the dataplane an operator cannot account
        # for. Both are said here, because this is the screen somebody reads
        # before deciding what to type.
        #
        # ASKED OF THE SAME TWO FACTS THE TUNER DECIDES ON - the cards Linux
        # has, and the management network the installer recorded - so this
        # cannot describe a claim the tuner would not make. It is not a second
        # copy of the rule: the rule is one sentence and this states it, which
        # is the difference between a screen and a reimplementation.
        for line in self.claim_note(nics):
            print_prose(line, indent="  ")
        # And which of them is not on the dataplane YET, which is the half
        # the rule above cannot say (#405).
        for line in self.waiting_note(nics, held):
            print_prose(line, indent="  ")
        if not mgmt:
            print_prose("no interface carries a default route on this box, "
                        "so none of them is the management path by that "
                        "test.", indent="  ")
        elif any(n["name"] == mgmt for n in nics):
            # Two different sentences, because they are two different boxes.
            # With another card, the mark says what taking this one means and
            # names nothing as refused: since #385 the VTY accepts it, and
            # the box claims it by itself unless a reserve spares it. With no
            # other card, the note says the box takes it whether or not it is
            # named (#303, #378).
            if any(n["name"] != mgmt for n in nics):
                print_prose(f"'{mgmt}' carries the default route: it is how "
                            f"this box is reached. If the dataplane takes "
                            f"it, named or claimed, its address moves onto "
                            f"the linux_cp pair the dataplane keeps for it, "
                            f"and this box is then reached through the "
                            f"dataplane rather than beside it.", indent="  ")
                # AND IF THE BOX IS ABOUT TO TAKE IT BY ITSELF, say so here.
                #
                # What the box does unbidden is decided by the reserve, and
                # on a box whose default route is not on the card the
                # installer recorded it is about to take the card it is
                # reached on. Reserving a card is done at install time, and
                # this is the screen where somebody finds out it was not.
                if not self.read_dataplane_if() \
                   and self.reserved_for_linux() != mgmt:
                    print_prose(f"This box will claim '{mgmt}' anyway when "
                                f"the dataplane next starts, because nothing "
                                f"reserves it: it is not the management "
                                f"network this box was installed with.",
                                indent="  ")
            else:
                for line in self.one_nic_note():
                    print_prose(line, indent="  ")
        else:
            # The management path can be something this list cannot hold: a
            # bridge, a bond, a VLAN, or a virtio NIC with no PCI address.
            # Said rather than left as a missing mark, because an unmarked
            # list reads as "none of these is the management path" - and that
            # reading is the one that costs the box.
            print_prose(f"none of these carries the default route. It is on "
                        f"'{mgmt}', which is not a PCI NIC, and that is "
                        f"still the path this box is reached over.",
                        indent="  ")

    # --------------------------------------------- naming the dataplane card
    #
    # WHY THIS IS IN THE VTY AT ALL. Until this existed the only way to give
    # an appliance a card was to edit PRISMA_DATAPLANE_IF in the tuner's file
    # from a Linux shell, and that was the state every fresh install came up
    # in: nothing claimed, VPP with no interface, and a box that forwarded
    # nothing. docs/context.md makes the argument for the other half of this -
    # an operator who has to drop to vppctl is one for whom this VTY is
    # decoration - and it holds identically for one who has to drop to an
    # editor (#283).
    #
    # A FRESH INSTALL IS NO LONGER THAT BOX (#378): it claims every card it
    # has but a reserve, and never the last one, so nobody has to type this to
    # make an ordinary box forward. What the statement is for now is the box
    # with several cards where only one carries subscriber traffic - it claims
    # the card named here and no other - and that is why removing it was
    # rejected rather than merely not done.

    #: The tuner's input for the dataplane card, and the unit that reads it.
    #:
    #: Named here rather than written into the sentences below, because the
    #: same two strings are used by the write, by the read-back and by what
    #: the operator is told - and a fourth spelling of either is a fourth
    #: thing to keep true.
    TUNE_KEY = "PRISMA_DATAPLANE_IF"
    TUNE_UNIT = "prisma-routing-tune"

    # The card list and the default route are read by host_nics() and
    # default_route_nic(), which `show hardware` already owns. This used to
    # carry its own pair of readers with the same two seams, and two answers
    # to "which card carries the default route" is exactly the pair that comes
    # to disagree - here by refusing one card at the prompt while the listing
    # marks a different one.

    def dataplane_if_refusal(self, name):
        """
        Why this box will not hand `name` to the dataplane, or None.

        Returns (headline, [prose]). ONE refusal now, where there were three:
        a name this box has not got is refused the way every other interface
        name is in this VTY (#105), by saying what the box DOES have, because
        a refusal that only says no leaves the operator to guess the spelling
        of a card they cannot see.

        The card carrying the default route was refused here too, while the
        box had another card to name instead (#123), and on a one-card box
        when its address, MAC or gateway could not be read (#303). Both went
        with #385, on the maintainer's decision: a box is configured at its
        console when it is new, so a refusal guarding the card it is reached
        over guards a step nobody takes over the network. The tuner reads the
        way back off that card on the boot that claims it and declines a card
        it cannot read one for, which is the only reader that question needs.
        """
        have = [n["name"] for n in self.host_nics()]
        # Cannot look is not "the box has none". On anything that is not Linux
        # - which is where this VTY's own suites run - the directory is absent,
        # and refusing every name there would make the setting untestable and
        # unusable rather than safe.
        if have and name not in have:
            return ("this box has no such interface",
                    ["the cards it has: " + ", ".join(have),
                     "this is the name LINUX gives the card, which is what "
                     "the dataplane is handed; 'show interface' lists what "
                     "the dataplane already has, and on a box with no card "
                     "that is nothing"])
        return None

    def say_dataplane_if_refusal(self, name, why):
        """One refusal, printed: the statement's own words, then the reason."""
        headline, prose = why
        # The name is on the headline rather than on a row of its own, which
        # is the shape #105 uses for `interface <name>`. It fits: Linux will
        # not carry a name over fifteen characters, so the widest this can
        # measure is well inside the console.
        self.err(f"dataplane interface {name}: {headline}")
        for line in prose:
            print_prose(line)

    def read_dataplane_if(self):
        """
        The card the box is currently set to hand over, read from the tuner's
        own input.

        Read back from the box rather than remembered, for the reason every
        other field of the running configuration is: an operator who set this
        by editing the file - which was the only way until now - must see their
        own box in `show running-config`, and a VTY that reports what it last
        wrote is a VTY that cannot notice being wrong.

        LAST assignment wins, because that is what systemd does with an
        EnvironmentFile and what the boot self-test already reports.
        """
        return self.read_tune_key(self.TUNE_KEY)

    def read_tune_key(self, key):
        """
        One assignment out of the tuner's file, unquoted, or "".

        LAST assignment wins, because that is what systemd does with an
        EnvironmentFile and what the boot self-test already reports.
        """
        found = ""
        try:
            with open(self.TUNE_ENV) as f:
                for line in f:
                    s = line.strip()
                    if s.startswith(key + "="):
                        v = s.split("=", 1)[1].strip()
                        found = v.strip('"').strip("'")
        except OSError:
            pass
        return found

    #: Where prisma-routing-tune leaves the way back it computed, on this boot,
    #: for a card it was about to claim (#378). In /run, so it cannot outlive
    #: the boot that wrote it and cannot describe a card that has since been
    #: swapped.
    MGMT_PATH_FILE = os.environ.get(
        "PRISMA_MGMT_PATH", "/run/prisma-routing/dataplane-management")

    def read_computed_mgmt(self):
        """
        The way back prisma-routing-tune computed on this boot, or {}.

        Four fields - name, address, MAC, gateway - and ALL FOUR, because
        this is the only record load() applies now and nothing else will put
        the route back: a box with the address on the pair and no default
        route answers on its own subnet and nowhere else, measured on an
        installed box before the gateway was carried (#378). A file holding
        three of the four describes a box that cannot exist and is read as no
        record at all, which is the safe direction.
        """
        try:
            with open(self.MGMT_PATH_FILE, encoding="utf-8") as f:
                w = f.read().split()
        except (OSError, UnicodeDecodeError):
            return {}
        if len(w) != 4 or "/" not in w[1]:
            return {}
        return {"name": w[0], "address": w[1], "mac": w[2].lower(),
                "gateway": w[3]}

    def write_dataplane_if(self, name):
        """
        Set the tuner's input to `name`, and change nothing else in the file.

        Returns (ok, message).

        EVERY OTHER LINE SURVIVES, and that is not politeness. The file is
        hand-editable by design and most of it is prose: the paragraph saying
        why the field ships empty, the subscriber count, the worker cap and the
        argument for leaving it uncapped. Rewriting it from the four settings
        this VTY models would delete all of that, on the first commit, from a
        file the operator is told they may edit.

        Written through a temporary file and renamed, so a box that loses power
        mid-write comes up with the old file rather than half of one. The
        tuner's file is read at every boot, and a truncated one takes the
        tuning with it.
        """
        return self.write_tune_key(self.TUNE_KEY, name)

    def write_tune_key(self, key, value):
        """One assignment set in the tuner's file, everything else untouched."""
        assign = f"{key}={value}"
        try:
            with open(self.TUNE_ENV) as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            # No file at all is not an appliance, or is one whose file was
            # removed. One is written rather than refusing: the unit reads it
            # with a leading '-', so an absent file is not an error there
            # either, and refusing here would leave the operator with no way
            # to proceed from the VTY - which is the defect being repaired.
            lines = ["# Written by prismash. Set through the VTY: "
                     "'dataplane interface <name>'."]
        except OSError as e:
            return False, f"{self.TUNE_ENV}: {e.strerror}"

        out, replaced = [], False
        for line in lines:
            if line.strip().startswith(key + "="):
                out.append(assign)
                replaced = True
            else:
                out.append(line)
        if not replaced:
            out.append(assign)

        tmp = self.TUNE_ENV + ".new"
        try:
            d = os.path.dirname(self.TUNE_ENV)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(tmp, "w") as f:
                f.write("\n".join(out) + "\n")
            os.replace(tmp, self.TUNE_ENV)
        except OSError as e:
            return False, f"{self.TUNE_ENV}: {e.strerror}"
        return True, ""

    def apply_dataplane_if(self, name):
        """
        Store the card, run the tuner, and say what is still outstanding.

        WHAT THIS DELIBERATELY DOES NOT DO IS RESTART VPP. Handing a card to
        the dataplane takes it away from Linux, and everything reaching this
        box through that card stops the instant it happens - on the box this
        path was written for, whose only card carries the management address,
        that is the operator's own session. Which of those two boxes is which
        is asked in say_dataplane_if_pending, and stated nowhere. That is the
        same class of consequence as `commit remap`, which this VTY refuses
        until it is asked for by name.

        It is not given a word of its own here, and that is a limit rather
        than an oversight: nothing in this repository has measured what a VPP
        restart costs on a box carrying traffic, and inventing a dangerous
        verb on no measurement is how a box comes to be restarted by somebody
        who was told it was safe. So the commit stops at the boundary and says
        where the boundary is.
        """
        ok, why = self.write_dataplane_if(name)
        if not ok:
            return False, why
        # The unit, not the script: it is what carries the EnvironmentFile.
        # rc 127 is systemctl not being on this machine at all, which is every
        # developer box and every suite - written and not run is the honest
        # answer there, not a failure.
        rc, out = self.sys._run(["systemctl", "restart", self.TUNE_UNIT])
        if rc == 127:
            return True, "stored; the tuner is not on this machine, so " \
                         "nothing was recomputed"
        if rc != 0:
            return True, ("stored, but the tuner did not run: "
                          + out.strip()[:120])
        return True, ""

    def say_dataplane_if_pending(self, name, note=""):
        """
        What the commit has done, and what it has not.

        Said in this order on purpose. The operator has just been told
        `dataplane interface <name>` was accepted, and the sentence they need
        next is not that it worked - it is that the card has not moved yet and
        what moving it will cost.

        `note` replaces the line claiming the tuning was recomputed, rather
        than being printed beside it: on a machine with no tuner both would be
        on the screen at once and one of them would be false.

        WHAT THE RESTART COSTS IS ASKED, not stated. "ends every session on
        this box, including this one" is true of the box this whole path was
        written for - the one whose only card carries the management address -
        and false of the two-card box that is the worked example in the
        operator guide, where the session arrives on a card Linux keeps and a
        dataplane restart never touches it. The product reads that same pair
        - default_route_nic() and host_nics() - wherever it says what a card
        carries, and this sentence was the one that did not ask.

        What it does NOT claim on any of them is what the restart costs the
        traffic. Nothing in this repository has measured that, which is the
        argument apply_dataplane_if makes for there being no verb for it.
        """
        print(f"dataplane interface: {name or 'none'}")
        print_prose(note or "stored, and the boot tuning was recomputed for "
                            "it")
        if name:
            print_prose("NOT IN EFFECT YET: the dataplane claims its card "
                        "when it starts, and this commit does not restart it")
            # FOUR ANSWERS, NOT ONE, and only the first of them is the
            # sentence this used to print on all four.
            #
            # Read from the same pair `show hardware` marks the management
            # card with - default_route_nic() and host_nics() - so the screen
            # that tells the operator which card not to give away and the
            # screen that says what giving one away costs cannot come to
            # disagree. A third reader of "which card is this box reached on"
            # is how they would.
            #
            # A default route on something that is NOT one of this box's cards
            # is the third answer and it is not the second. It may be a
            # bridge, a bond, or - on a box that has already handed its only
            # card over - the linux_cp pair the dataplane itself keeps, which
            # a dataplane restart takes away. Calling that box safe because
            # the name differs from the card being named is the one shape
            # where being wrong costs the walk to the console.
            #
            # And #123's trade decides which way to be wrong when nothing can
            # be read: a box warned about a session it was never going to lose
            # spends a maintenance window it did not need, a box told the
            # session was safe and losing it is a box nobody can reach.
            mgmt = self.default_route_nic()
            cards = [n["name"] for n in self.host_nics()]
            if mgmt and name == mgmt:
                print_prose("restarting it hands the card over and ends every "
                            "session on this box, including this one, so it "
                            "is not done for you")
            elif mgmt and mgmt in cards:
                print_prose(f"restarting it hands the card over and ends "
                            f"whatever is reaching this box through {name}. "
                            f"The path this box is routed out of is "
                            f"'{mgmt}', which Linux keeps, so this session is "
                            f"not what it costs")
                print_prose("it is not done for you all the same: nothing "
                            "here has measured what stopping the dataplane "
                            "costs a box carrying traffic")
            elif mgmt:
                print_prose(f"restarting it hands the card over and ends "
                            f"whatever is reaching this box through it")
                print_prose(f"the default route is on '{mgmt}', which is not "
                            f"one of this box's cards - on a box that has "
                            f"already handed one over it is the pair the "
                            f"dataplane keeps, and a restart takes that with "
                            f"it - so this session may go too, and it is not "
                            f"done for you")
            else:
                print_prose("restarting it hands the card over and ends "
                            "whatever is reaching this box through it")
                print_prose("no interface here carries a default route, so "
                            "which path this session arrived on cannot be "
                            "read: take it that this one goes too, and it is "
                            "not done for you")
            # AND NOT ON THE MEDIUM, where the reboot this sentence asks
            # for throws the commit away with everything else (#310). This is
            # #305 one message over: the advice is right on a box that has a
            # disk and wrong on the one screen that says so itself, two lines
            # under a banner reading "nothing here survives a reboot".
            if running_from_live_medium():
                print_prose("this is the installation medium, so the commit "
                            "does not survive a reboot either: there is "
                            "nothing here for the dataplane to come back to")
                print_prose("type 'install' to put Prisma Routing on a disk, and "
                            "name the card there")
            else:
                print_prose("reboot the box in a window you have chosen, and "
                            "it comes up forwarding on that card")
        else:
            print_prose("the card this box has now is given up at the next "
                        "start of the dataplane, not at this commit")

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
        # Both spellings of the same word. An operator who types the one
        # their keyboard layout and their last employer taught them should
        # not get FRR's parser error for it; the box has no opinion about
        # which side of the Atlantic they learned it on.
        if args[:1] and args[0].rstrip("s") in ("utilization", "utilisation"):
            self.show_interface_utilisation(args[1:])
            return

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
        # EVERYTHING THE DATAPLANE HAS is kept, and what is LISTED is what an
        # operator configured. The two are different sets and both are needed:
        # a TAP named outright still answers, because the argument against
        # hiding a row was always about diagnosis (#403), and diagnosis is
        # what typing the name is for.
        everything = ifaces
        ifaces = {n: e for n, e in ifaces.items()
                  if n in set(routes.operator_interfaces(ifaces))}

        # `local0` alone means the dataplane claimed no NIC. Explained here
        # rather than left as a one-line table, because the operator asking
        # this question is looking at a box that forwards nothing and a box
        # that forwards nothing looks exactly like a box that is working
        # (#190).
        if set(ifaces) <= {"local0"} and not args:
            why = self.why_no_dataplane_interface()
            if why:
                print("  the dataplane has no interface to forward on: "
                      + why[0])
                for line in why[1:]:
                    print("  " + line)
                print()

        if args[:1] and args[0].rstrip("s") in ("terse", "description",
                                               "descr", "brief"):
            _, hw = self.dp.run("show hardware-interfaces brief")
            hardware = routes.parse_hardware(hw)
            total = dict(ifaces)
            rest = [a for a in args[1:] if a]
            if rest:
                ifaces = {n: e for n, e in everything.items()
                          if any(_starts(n, w) for w in rest)}
                if not ifaces:
                    print("  no interface matches")
                    return
            if args[0].startswith("terse"):
                self.show_interfaces_terse(ifaces, hardware, total)
            else:
                self.show_interfaces_description(ifaces, hardware)
            return

        wanted = [a for a in args if a not in ("description", "descr", "brief")]
        if wanted:
            ifaces = {n: e for n, e in everything.items()
                      if any(_starts(n, w) for w in wanted)}
            if not ifaces:
                print("  no interface matches")
                return
            # One interface named, and nothing else asked for: the operator is
            # looking at a fault, not taking an inventory. A summary answers
            # "is the link up"; a fault needs the counters, and "up" and "up
            # and dropping four percent of what arrives" look identical in a
            # table.
            # An EXACT name wins over the prefix filter. `show interface pg0`
            # on a box that also has pg0.100 matches both by prefix, so
            # "exactly one match" never fired for any interface that has
            # sub-interfaces - which is every interface an ISP terminates
            # subscribers on.
            exact = [n for n in ifaces
                     if any(n.lower() == a.lower() for a in wanted)]
            one = exact[0] if len(exact) == 1 else (
                next(iter(ifaces)) if len(ifaces) == 1 else None)
            if one and not any(
                    a in ("description", "descr", "brief") for a in args):
                self.show_one_interface(one, ifaces)
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

        # The count is of the rows above it, because the rows above it are the
        # interfaces somebody configured: see forwarding_count(), the one
        # holder the other two count lines read as well.
        shown, note = self.forwarding_count(ifaces)
        print(f"\n  {shown} interface(s) on the dataplane can forward")
        for line in note:
            print(line)


    def show_bgp(self, args):
        """
        `show bgp summary`, answered against the table that forwards.

        FRR's own summary says a session is Established and carrying N
        prefixes. That is a real answer to a different question: a full table
        exercises the linux_nl path far harder than a lab does, and a prefix
        FRR holds that VPP never got moves no packets. So the count of what
        actually reached the forwarding table is on the same line, where the
        operator is already looking (#148).

        Everything else under `show bgp` goes to FRR untouched. This VTY is not
        going to re-implement `show bgp <prefix>`.
        """
        # `received-routes` answers from what the neighbour sent BEFORE inbound
        # policy - which the box only has if it was told to keep it. Without
        # that, FRR prints an empty table, and "no routes received" and "I am
        # not keeping them" are different answers to look at during an
        # incident.
        if len(args) >= 3 and args[0] == "neighbor" \
                and args[2] == "received-routes":
            keeps = self.soft_reconfiguration_on(args[1])
            if keeps is None:
                # NOT the refusal below. Every sentence in it is about the
                # neighbour's configuration, and a box whose FRR is down has
                # not read that configuration - it has read nothing. The
                # advice would be a statement the box may already carry,
                # offered through a daemon that could not take it, and the
                # command it points at answers from the same FRR.
                # The headline carries no address, for the reason the two
                # below it do not either: err() wraps nothing, and an IPv6
                # peer put this at 99 columns on a console of 79 (#349).
                self.err("FRR is not answering")
                print_prose(f"so whether {args[1]} keeps what it received "
                            f"cannot be read")
                print_prose("an empty table here would be this box's control "
                            "plane being down rather than the neighbour "
                            "sending nothing: 'systemctl status frr' and its "
                            "journal say why")
                return
            if not keeps:
                # THROUGH print_prose, and the headline shortened to carry
                # only the refusal. err() wraps nothing and these rows were
                # bare print("    ...") calls, so the three of them measured
                # 106, 101 and 102 columns against a console of 79 (#349).
                # The neighbour address is interpolated into all three, so an
                # IPv6 peer is wider again - which is the case #276 widened
                # the check in test_prismash_bgp.py to cover, on a different
                # message. These two were not among the rows it split.
                # AND THE WORD 'neighbour' IS GONE FROM IT, which is the
                # rest of #349's own fix rather than a second one. The comment
                # above says the address is interpolated and an IPv6 peer is
                # wider again; driven with a full-length one this verdict is
                # 87 columns, and `err` folds nothing (#363). Dropping the
                # noun costs nothing - what follows the address says what it
                # is - and leaves 77.
                self.err(f"{args[1]} does not keep what it received")
                print_prose("so this would print an empty table either way")
                print_prose(f"add 'neighbor {args[1]} soft-reconfiguration "
                            f"inbound' under the address family; it costs "
                            f"memory")
                print_prose(f"'show bgp neighbor {args[1]} routes' shows what "
                            f"survived inbound policy, which the box does "
                            f"have")
                return
        if args[:1] != ["summary"]:
            rc, out = self.frr.show("show bgp " + " ".join(args))
            if out:
                print(out)
            return

        rc, sout = self.frr.show("show bgp ipv4 unicast summary json")
        if rc != 0 or not sout.strip():
            self.err("BGP is not answering")
            print("    bgpd may not be running; 'show version' says which "
                  "daemons are up")
            return
        try:
            summary = json.loads(sout[sout.index("{"):])
        except (ValueError, json.JSONDecodeError):
            self.err("BGP answered something this VTY could not read")
            return
        peers = summary.get("peers") or {}
        if not peers:
            print("  no BGP sessions are configured")
            return

        print(f"  {'Neighbour':<18} {'AS':<8} {'State':<14} {'Prefixes':<9} Up")
        established = 0
        for addr in sorted(peers):
            p = peers[addr]
            state = p.get("state", "?")
            if str(state).lower() == "established":
                established += 1
                state = "Established"
            pfx = p.get("pfxRcd", p.get("prefixReceivedCount", "-"))
            up = p.get("peerUptime", "-")
            print(f"  {addr:<18} {str(p.get('remoteAs', '-')):<8} "
                  f"{state:<14} {str(pfx):<9} {up}")

        print(f"\n  {len(peers)} session(s), {established} established")
        self.report_rpki_state()
        # Which prefixes BGP holds, and which of them the dataplane has. Asked
        # once for the whole table rather than per peer: the FIB does not know
        # which session a route came from, and pretending otherwise would put a
        # number on the line that could not be defended.
        self.report_fib_reach(self.bgp_prefixes(), "BGP")

    def report_rpki_state(self):
        """
        Whether origin validation is actually available, on the summary.

        "No invalid routes" and "no validator" look identical from a route
        table, and the second one is a box accepting whatever it is told. Said
        here because this is the screen an operator is already looking at.
        """
        cfg = self.running_config()
        caches = [l for l in cfg.routing if l.strip().startswith("rpki cache ")]
        if not caches:
            return          # nothing configured is a choice, not a fault
        rc, out = self.frr.show("show rpki cache-connection")
        if rc != 0:
            print("  RPKI: the module is not loaded, so nothing is validated")
            return
        # The per-cache "(connected)" marker, NOT the "Connected to group N"
        # header. Measured: with the cache killed, the marker disappears within
        # a second and the header still says Connected seventy seconds later.
        # Reading the header is reading a line that is wrong, which is the
        # thing this report exists to stop an operator doing.
        live = sum(1 for l in out.splitlines() if "(connected)" in l)
        if live:
            print(f"  RPKI: {live} of {len(caches)} cache(s) connected")
        else:
            print(f"  RPKI: {len(caches)} cache(s) configured, NONE connected")
            # Also measured: the prefix table is NOT emptied when the cache
            # goes. Validation keeps running on what was already learned until
            # expire_interval passes - 7200 seconds by default - and only then
            # does everything become notfound. Both halves matter: an operator
            # told "nothing is validated" would go looking for an outage that
            # has not started yet.
            print_prose("validation is running on data the caches already "
                        "sent, which expires; after that every route is "
                        "notfound", indent=" " * 8)

    def bgp_prefixes(self):
        """Every prefix BGP holds, from FRR. Empty when there is no BGP."""
        rc, out = self.frr.show("show bgp ipv4 unicast json")
        if rc != 0 or not out.strip():
            return []
        try:
            data = json.loads(out[out.index("{"):])
        except (ValueError, json.JSONDecodeError):
            return []
        return sorted(data.get("routes") or {})

    def frr_json(self, cmd):
        """
        One FRR command, parsed. None when it could not be had.

        An empty object is a REAL answer and comes back as one: `show ip ospf
        neighbor json` on a box with no OSPF prints `{}` and exits 0, while a
        box whose ospfd is not running prints "ospfd is not running" and exits
        1. Those are different situations with different first questions, and
        collapsing them into "no neighbours" is how an operator spends ten
        minutes looking at a protocol that was never started.
        """
        rc, out = self.frr.show(cmd)
        if rc != 0 or not out.strip() or "{" not in out:
            return None
        try:
            return json.loads(out[out.index("{"):])
        except (ValueError, json.JSONDecodeError):
            return None

    def report_fib_reach(self, prefixes, what):
        """
        How many of these prefixes the table that forwards actually has.

        The count #148 put on `show bgp summary`, said in the same words for
        every protocol that has one: FRR installs into the kernel and linux_nl
        carries it into VPP, and a route that stops half way leaves both halves
        looking healthy on their own.
        """
        rc, out = self.dp.run("show ip fib")
        in_fib = set(routes.parse_fib(out).get(0, {})) \
            if rc == 0 and out.strip() else set()
        if not in_fib:
            print_prose("the dataplane is not answering, so what reached "
                        "the forwarding table is unknown", indent="  ")
        elif not prefixes:
            print_prose(f"no {what} prefixes to check against the "
                        f"forwarding table", indent="  ")
        elif [p for p in prefixes if p not in in_fib]:
            missing = [p for p in prefixes if p not in in_fib]
            # The number that is worth the line. A route FRR holds and VPP
            # never got is invisible in every command that answers from FRR.
            print(f"  {len(missing)} of {len(prefixes)} {what} prefix(es) "
                  "are NOT in the forwarding table")
            for prefix in sorted(missing)[:5]:
                print(f"    {prefix}")
            if len(missing) > 5:
                print(f"    ... and {len(missing) - 5} more")
            print("    'show route verbose' compares the two tables in full")
        else:
            print(f"  all {len(prefixes)} {what} prefix(es) are in the "
                  "forwarding table")

    def igp_prefixes(self, protocol):
        """Every prefix FRR chose from one IGP. Empty when FRR has none."""
        rc, out = self.frr.show("show ip route json")
        if rc != 0 or not out.strip():
            return []
        return routes.protocol_prefixes(routes.parse_rib(out), protocol)

    def ospf_mtu_stalls(self):
        """
        The MTU stall, keyed by the name FRR knows the interface by.

        The same check the commit runs, asked of the running configuration
        instead of a candidate. It is keyed on the Linux name because that is
        the only name an OSPF answer ever contains: FRR has never heard of
        `host-veth-vpp`.
        """
        try:
            found = self.ospf_mtu_mismatch(self.running_config().routing,
                                           self.device_mtus())
        except Exception:
            return {}
        return {host: (ifname, here, there)
                for ifname, host, here, there in found}

    def dataplane_name(self, host, pairs, mtus):
        """
        The dataplane's own name for the interface FRR calls `host`.

        VPP's end of a linux_cp pair is an interface too, named tap<n>, and it
        has an MTU - so the pair answers to three names and only one of them is
        the one an operator can type at this VTY. routes.dataplane_end() is
        the rule, the other half of routes.linux_end()'s: the member the MTU
        map holds that is not the TAP - which on the same-name pair (#389) is
        `host` itself. `name != host` answered None there, and `show ospf
        interface` printed '-' for a card whose MTU the dataplane has.
        """
        return routes.dataplane_end(pairs, host, mtus)

    # ----------------------------------------------------------------- OSPF

    def show_ospf(self, args, bare_is_neighbors=True):
        """
        OSPF adjacencies, beside what the dataplane has.

        `show ip ospf neighbor` is handed to vtysh today and FRR answers it,
        which for the STATE of an adjacency is right: that is FRR's to know and
        nothing else on this box has it. What FRR cannot say is whether the
        routes the adjacency brought ever reached the table that forwards, and
        why one that will not finish is not finishing - and on this box the
        commonest answer to the second is a fault only this product can see.

        Everything else under `show ospf` goes to FRR untouched, in FRR's own
        spelling. This VTY is not going to re-implement the link-state
        database.
        """
        if args in (["neighbor"], ["neighbors"]) \
                or (bare_is_neighbors and not args):
            self.show_ospf_neighbors()
            return
        if args in (["interface"], ["interfaces"]):
            self.show_ospf_interfaces()
            return
        rc, out = self.frr.show(("show ip ospf " + " ".join(args)).rstrip())
        if out:
            print(out)

    def show_ospf_neighbors(self):
        data = self.frr_json("show ip ospf neighbor json")
        if data is None:
            self.err("OSPF is not answering")
            print("    ospfd may not be running; 'show version' says which "
                  "daemons are up")
            return
        neighbours = data.get("neighbors") or {}
        if not neighbours:
            self.report_no_ospf_neighbour()
        else:
            stalls = self.ospf_mtu_stalls()
            print(f"  {'Neighbour':<17} {'Interface':<14} {'State':<16} "
                  f"{'Dead':<10} Up")
            total, full, stalled, exchanging = 0, 0, {}, {}
            for rid in sorted(neighbours):
                for n in neighbours[rid]:
                    total += 1
                    # FRR renders the interface as "<name>:<local address>" in
                    # this command and as the bare name in every other one.
                    # Measured on 10.7.1; taken apart here rather than printed
                    # as it comes, because the name in this column has to be
                    # the name the next command takes.
                    iface = str(n.get("ifaceName", "-")).split(":")[0]
                    state = str(n.get("nbrState", "?"))
                    converged = n.get("converged")
                    if converged == "Full":
                        full += 1
                    else:
                        if converged in ("ExStart", "Exchange"):
                            exchanging[iface] = (rid, converged)
                        if iface in stalls:
                            stalled[iface] = stalls[iface]
                    print(f"  {rid:<17} {iface:<14} {state:<16} "
                          f"{str(n.get('deadTime', '-')):<10} "
                          f"{n.get('upTime', '-')}")
            print(f"\n  {total} adjacency(ies), {full} full")
            # The one thing worth saying on this screen. An adjacency that
            # stops in Exchange is the MTU stall documented as Pitfall 1, and
            # nothing in FRR's own answer mentions MTU at all.
            if exchanging:
                self.report_ospf_exchange(exchanging)
            # And the half of it that is entirely local to this box: its own
            # two MTUs disagreeing. Said in the words the commit check already
            # used, from the same place - two diagnoses of one fact that
            # disagree are worse than one.
            for iface, (ifname, here, there) in sorted(stalled.items()):
                for line in mtu_stall_lines(ifname, iface, here, there):
                    print("  " + line)
        self.report_fib_reach(self.igp_prefixes("ospf"), "OSPF")

    def report_ospf_exchange(self, exchanging):
        """
        An adjacency that will not leave Exchange, and what this box is
        advertising into it.

        OSPF carries the interface MTU in its Database Description packets and
        the receiver drops any that claims one larger than its own - with no
        log line on either side, which is what makes this the fault that costs
        an afternoon rather than a minute. The far end's MTU cannot be known
        from here; what this end is advertising can, and it is the number to
        compare against. On this box it is the TAP's, and `linux_cp` starts
        the TAP at 9000, so it is more often wrong than right.
        """
        data = self.frr_json("show ip ospf interface json") or {}
        ifaces = data.get("interfaces") or {}
        for iface, (rid, state) in sorted(exchanging.items()):
            mtu = (ifaces.get(iface) or {}).get("mtuBytes", "?")
            print(f"  {rid} on {iface} is in {state}, which is where the two "
                  f"ends compare MTU; this end advertises {mtu}")
            print_prose("an adjacency that stops here is the far end "
                        "running a smaller one, and neither side logs a word "
                        "about it")
            print_prose("'show ospf interface' has both of this box's MTUs; "
                        "'mtu <n>' sets them together, and 'ip ospf "
                        "mtu-ignore' is the escape hatch")

    def report_no_ospf_neighbour(self):
        """
        "No adjacency" is three different situations, and they are told apart
        by whether anything is running OSPF at all.
        """
        data = self.frr_json("show ip ospf interface json") or {}
        ifaces = data.get("interfaces") or {}
        running = [n for n, e in ifaces.items() if e.get("ospfEnabled")]
        quiet = [n for n in running if ifaces[n].get("timerPassiveIface")]
        if not running:
            print("  no OSPF adjacency, and no interface is running OSPF")
            return
        if len(quiet) == len(running):
            print(f"  no OSPF adjacency; all {len(running)} interface(s) "
                  "running OSPF are passive, so none sends a hello")
            return
        print(f"  no OSPF adjacency, on {len(running) - len(quiet)} "
              "interface(s) that are sending hellos")

    def show_ospf_interfaces(self):
        """
        What OSPF believes about each interface, beside what the dataplane has.

        The MTU is two columns on purpose, and they are the reason this command
        exists. `linux_cp` gives FRR a TAP and nothing else, so the MTU ospfd
        puts in its Database Description packets is the TAP's while the wire
        runs at the dataplane's - and when the two differ the adjacency stops
        in Exchange with no log line on either side. Until now no command on
        this box put those two numbers on one screen.
        """
        data = self.frr_json("show ip ospf interface json")
        if data is None:
            self.err("OSPF is not answering")
            print("    ospfd may not be running; 'show version' says which "
                  "daemons are up")
            return
        ifaces = data.get("interfaces") or {}
        if not ifaces:
            print("  no interface is running OSPF")
            return
        mtus = self.device_mtus()
        rc, lcp = self.dp.run("show lcp")
        pairs = routes.lcp_pairs(lcp) if rc == 0 else {}
        stalls = self.ospf_mtu_stalls()

        print(f"  {'Interface':<14} {'Area':<10} {'State':<18} {'Cost':<6} "
              f"{'Adjacent':<10} {'OSPF MTU':<9} Dataplane")
        for name in sorted(ifaces):
            e = ifaces[name]
            dp = self.dataplane_name(name, pairs, mtus)
            # "-" and 0 are different answers. An interface the dataplane does
            # not have - the management port, which is kept away from VPP on
            # purpose - has no MTU here, and printing one would be inventing
            # the number this column exists to compare against.
            there = mtus.get(dp)
            state = str(e.get("state", "?"))
            if e.get("timerPassiveIface"):
                state += " (passive)"
            adjacent = f"{e.get('nbrAdjacentCount', 0)} of " \
                       f"{e.get('nbrCount', 0)}"
            print(f"  {name:<14} {str(e.get('area', '-')):<10} {state:<18} "
                  f"{str(e.get('cost', '-')):<6} {adjacent:<10} "
                  f"{str(e.get('mtuBytes', '-')):<9} "
                  f"{'-' if there is None else there}"
                  + (f"  ({dp})" if dp else ""))
        for iface in sorted(stalls):
            if iface in ifaces:
                ifname, here, there = stalls[iface]
                for line in mtu_stall_lines(ifname, iface, here, there):
                    print("  " + line)

    # ---------------------------------------------------------------- IS-IS

    def show_isis(self, args):
        """
        IS-IS adjacencies, beside what the dataplane has.

        Same shape as `show ospf`, with a different trap underneath it: IS-IS
        does not run over IP, so a box whose dataplane is not punting OSI to
        the kernel has a circuit that is Up and Active, no adjacency, and its
        only evidence in a VPP error counter (#168).
        """
        if args in ([], ["adjacency"], ["adjacencies"], ["neighbor"],
                    ["neighbors"]):
            self.show_isis_adjacency()
            return
        if args in (["interface"], ["interfaces"]):
            self.show_isis_interfaces()
            return
        rc, out = self.frr.show("show isis " + " ".join(args))
        if out:
            print(out)

    def isis_circuits(self):
        """
        Every IS-IS circuit, flattened out of the area/circuit nesting.

        Returns (area, interface-object) pairs. isisd answers per area and a
        box can run more than one instance, so the area travels with the
        circuit rather than being dropped on the way out.
        """
        data = self.frr_json("show isis interface detail json")
        out = []
        for area in (data or {}).get("areas") or []:
            for circuit in area.get("circuits") or []:
                iface = circuit.get("interface")
                if isinstance(iface, dict):
                    out.append((area.get("area", "?"), iface))
        return out

    def isis_punt_state(self):
        """
        Is the dataplane punting IS-IS to the kernel, and what did it drop?

        Returns (punting, drops); punting is None when the dataplane could not
        be asked, which is not the same answer as "no". IS-IS runs OSI CLNS
        straight over the link layer, so linux_cp has to be told to pass NLPID
        0x83 up or the hellos never reach isisd at all.
        """
        have = self.osi_protos_enabled()
        rc, out = self.dp.run("show errors")
        return (None if have is None else self.OSI_ISIS in have,
                osi_input_drops(out) if rc == 0 else 0)

    def report_isis_punt(self, silent_circuits):
        """
        Why there is no adjacency on a circuit that looks perfectly healthy.

        Said on both IS-IS screens, and said either way: "the punt is off" and
        "the punt is on" are both answers an operator at 2am needs, because the
        second one is what stops them chasing the trap this box is known for
        and sends them to the neighbour instead.
        """
        punting, drops = self.isis_punt_state()
        if punting is None:
            print_prose("the dataplane is not answering, so whether it is "
                        "punting IS-IS to the kernel is unknown", indent="  ")
            return
        if punting:
            if silent_circuits:
                # THROUGH print_prose, like the sentence above it. These three
                # were composed as one row each and reached the console at
                # 128, 95 and 111 columns; the NLPID is interpolated into all
                # three, which is why #359's walk counted them and measured
                # none (#363).
                print_prose("the dataplane IS punting IS-IS to the kernel "
                            f"(NLPID 0x{self.OSI_ISIS:02x}), so the hellos "
                            "are reaching isisd; the far end is not "
                            "answering them", indent="  ")
            return
        print_prose("the dataplane is NOT punting IS-IS to the kernel (NLPID "
                    f"0x{self.OSI_ISIS:02x}), so no hello ever reaches isisd",
                    indent="  ")
        if drops:
            # The evidence, and the reason this is not merely "nothing
            # happened": the frames ARRIVED and were dropped for want of a
            # registered receiver. Without it the same sentence would be
            # printed on a box whose neighbour is switched off.
            print_prose(f"{drops} frame(s) counted in osi-input as an "
                        "unknown OSI protocol, which is the hellos arriving "
                        "and being dropped", indent="    ")
        else:
            print_prose("and nothing has been counted in osi-input either, "
                        "so the frames are not even arriving")
        print_prose("committing an IS-IS instance through this VTY turns the "
                    "punt on; a box configured through vtysh never had it")

    def show_isis_adjacency(self):
        data = self.frr_json("show isis neighbor json")
        if data is None:
            self.err("IS-IS is not answering")
            print("    isisd may not be running; 'show version' says which "
                  "daemons are up")
            return
        adjacencies = [c for area in data.get("areas") or []
                       for c in area.get("circuits") or [] if c.get("adj")]
        if adjacencies:
            print(f"  {'System Id':<18} {'Interface':<14} {'Level':<7} "
                  f"{'State':<8} Expires")
            for c in adjacencies:
                print(f"  {str(c.get('adj', '?')):<18} "
                      f"{str(c.get('interface', '-')):<14} "
                      f"L{str(c.get('level', '?')):<6} "
                      f"{str(c.get('state', '?')):<8} "
                      f"{c.get('expires-in', '-')}")
            up = sum(1 for c in adjacencies if c.get("state") == "Up")
            print(f"\n  {len(adjacencies)} adjacency(ies), {up} up")
        # A circuit that is Up and Active with nobody on it is the shape the
        # punt leaves behind, and it is the one an operator is looking at when
        # they type this. Passive circuits are excluded: one with no
        # adjacency is doing exactly what it was told.
        silent = [(area, i) for area, i in self.isis_circuits()
                  if i.get("state") == "Up"
                  and i.get("is-passive") == "active"
                  and not sum(l.get("active-neighbors", 0)
                              for l in i.get("levels") or [])]
        if not adjacencies:
            print("  no IS-IS adjacency")
        for area, i in silent:
            print(f"  circuit {i.get('name', '?')} in area {area} is Up and "
                  "Active with no adjacency")
        if silent:
            self.report_isis_punt(silent)
        self.report_isis_overload()
        self.report_fib_reach(self.igp_prefixes("isis"), "IS-IS")

    def report_isis_overload(self):
        """
        Whether this box is telling everybody not to transit through it.

        The maintenance drain nobody remembers turning on. An operator looking
        at a box that has adjacencies, holds routes and carries no transit
        traffic will not find the cause in any of them - it is one bit in the
        LSP this box is flooding, and that LSP is where it is read from.
        """
        rc, out = self.frr.show("show isis database")
        if rc != 0 or not isis_overload_set(out):
            return
        print_prose("this box has the overload bit set: its neighbours will "
                    "not compute transit paths through it", indent="  ")
        print_prose("traffic to anything behind this box drains away, which "
                    "is what 'set-overload-bit' is for and what it looks like "
                    "when it was left on")

    def show_isis_interfaces(self):
        circuits = self.isis_circuits()
        if not circuits:
            data = self.frr_json("show isis interface detail json")
            if data is None:
                self.err("IS-IS is not answering")
                print("    isisd may not be running; 'show version' says "
                      "which daemons are up")
            else:
                print("  no interface is running IS-IS")
            return
        print(f"  {'Interface':<14} {'Area':<10} {'Circuit':<9} {'State':<10} "
              f"{'Level':<7} {'Metric':<8} Neighbours")
        silent = []
        for area, i in circuits:
            levels = i.get("levels") or []
            active = sum(l.get("active-neighbors", 0) for l in levels)
            state = str(i.get("state", "?"))
            if i.get("is-passive") != "active":
                state += " (passive)"
            elif state == "Up" and not active:
                silent.append((area, i))
            metric = levels[0].get("metric", "-") if levels else "-"
            print(f"  {str(i.get('name', '?')):<14} {str(area):<10} "
                  f"{str(i.get('circuit-id', '-')):<9} {state:<10} "
                  f"{str(i.get('level', '-')):<7} {str(metric):<8} {active}")
        self.report_isis_punt(silent)

    def do_clear(self, line):
        """
        Reset a session. Operational, and it changes nothing on disk.

        `clear` is the command an operator reaches for at 2am, and until now
        this VTY did not have it - which meant leaving prismash for vtysh, on a
        box whose whole point is that they do not have to.
        """
        w = line.split()
        # FRR spells these `clear ip ospf ...`, and an operator arriving from
        # another vendor types that; the tree offers the short one.
        if w[1:3] == ["ip", "ospf"]:
            w = ["clear", "ospf"] + w[3:]
        if w[1:2] == ["ospf"]:
            self.clear_ospf(w[2:])
            return
        if w[1:2] == ["isis"]:
            self.clear_isis(w[2:])
            return
        if w[:2] != ["clear", "bgp"] or len(w) < 3:
            self.err("usage: clear bgp <neighbour> [soft [in|out]]")
            print("       clear ospf process | clear ospf neighbor <A.B.C.D>")
            print("       clear isis adjacency [<system-id>]")
            return
        if not self.require(CAP_ALL, "clear bgp"):
            return
        # `soft in` re-applies inbound policy without bouncing the session, and
        # needs the neighbour to have kept what it sent. Saying that here is
        # the difference between "no routes" and "I am not keeping them".
        if w[3:4] == ["soft"] and w[4:5] != ["out"]:
            keeps = self.soft_reconfiguration_on(w[2])
            if keeps is None:
                # And here the second half of that refusal is worse than
                # wrong: 'clear bgp <n>' is an outage offered on a premise
                # nothing read, on a box where it would reset nothing because
                # the daemon that holds the session is not answering either.
                self.err("FRR is not answering")
                print_prose(f"so whether {w[2]} keeps what it received cannot "
                            f"be read")
                print_prose("nothing was re-applied, and 'clear bgp' would "
                            "reset nothing either - both go to the FRR that "
                            "is silent: 'systemctl status frr' and its "
                            "journal say why")
                return
            if not keeps:
                # The same shape as the refusal in show_bgp, and it was over
                # the console for the same reason (#349): 92, 105 and 81.
                # The noun dropped here for the reason it is dropped in
                # show_bgp: 87 columns on a full-length IPv6 peer, 77 without
                # it (#363).
                self.err(f"{w[2]} does not keep what it received")
                print_prose("so there is nothing to re-apply")
                print_prose(f"add 'neighbor {w[2]} soft-reconfiguration "
                            f"inbound' under the address family, which costs "
                            f"memory,")
                print_prose(f"or use 'clear bgp {w[2]}' to reset the session "
                            f"and take the routes again")
                return
        rc, out = self.frr.show("clear bgp " + " ".join(w[2:]))
        if out.strip():
            print(out)
        if rc == 0:
            print(f"cleared: {' '.join(w[2:])}")

    def clear_ospf(self, rest):
        """
        `clear ospf process` and `clear ospf neighbor <id>`.

        OSPF has no sessions to reset one at a time the way BGP has: tearing
        the process down takes every adjacency on the box with it, and it is
        named `process` here because that is what it does. Admin-only for the
        same reason `clear bgp` is - it is an outage on purpose.
        """
        if rest == ["process"]:
            cmd = "clear ip ospf process"
        elif len(rest) == 2 and rest[0] in ("neighbor", "neighbors"):
            cmd = "clear ip ospf neighbor " + rest[1]
        else:
            self.err("usage: clear ospf process | clear ospf neighbor "
                     "<A.B.C.D>")
            return
        if not self.require(CAP_ALL, "clear ospf"):
            return
        rc, out = self.frr.show(cmd)
        if out.strip():
            print(out)
        if rc == 0:
            print(f"cleared: {' '.join(rest)}")

    def clear_isis(self, rest):
        """
        `clear isis adjacency [<system-id>]`, which FRR spells `neighbor`.

        Bare `clear isis` means all of them, because that is what #176 asks for
        and what an operator types.
        """
        if rest and rest[0] not in ("adjacency", "adjacencies", "neighbor",
                                    "neighbors"):
            self.err("usage: clear isis adjacency [<system-id>]")
            return
        if len(rest) > 2:
            self.err("usage: clear isis adjacency [<system-id>]")
            return
        if not self.require(CAP_ALL, "clear isis"):
            return
        cmd = "clear isis neighbor" + (" " + rest[1] if len(rest) == 2 else "")
        rc, out = self.frr.show(cmd)
        if out.strip():
            print(out)
        if rc == 0:
            print("cleared: isis adjacency"
                  + (" " + rest[1] if len(rest) == 2 else ""))

    def soft_reconfiguration_on(self, neighbour):
        """
        Is this neighbour keeping what it received, so policy can re-run?

        THREE ANSWERS. True and False are read off the running configuration;
        None is FRR not answering, and it is not False. Frr.running() reports
        a control plane that is down as a configuration with nothing in it -
        deliberately, because most of its callers want a list - and a caller
        that took that for "this neighbour keeps nothing" would tell the
        operator to add a statement the box may already have, through a daemon
        that is not there to take it. Frr.daemons() argues exactly this about
        its own reading forty lines away: "None is not the empty set and the
        two MUST NOT be collapsed."

        Asked of `available()`, which is the box's own question for whether
        FRR is answering, rather than by re-reading the configuration here. A
        second reader of the running routing configuration is a second answer
        to it.
        """
        if not self.frr.available():
            return None
        cfg = self.running_config()
        want = ["neighbor", neighbour, "soft-reconfiguration", "inbound"]
        return any(l.split()[:4] == want for l in cfg.routing)

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
        tables = routes.parse_fib(out, self.fib_table_names())

        rib = {}
        if verbose:
            # Only asked for when it is going to be used. A box may run CGNAT
            # with no routing daemon at all, and `show route` must not depend
            # on one being there.
            rrc, rout = self.frr.show("show ip route json")
            rib = routes.parse_rib(rout) if rrc == 0 else {}
            # Which interfaces VPP owns, so routes through the management
            # interface are not reported as faults for ever.
            #
            # BOTH the linux_cp pairs AND the dataplane's own interface names,
            # and the second half was missing. Measured on lab/bng.sh, whose
            # box has no pair at all: `show lcp` answered nothing, so every
            # route FRR held was judged against an empty set. Three of the four
            # it then reported were the container's own network through eth0 -
            # the management interface, which is precisely the case the
            # suppression exists for.
            #
            # An interface the dataplane knows by name is one VPP owns whether
            # or not anything paired it: `host-wan0` is VPP's and `eth0` is
            # not, and that is the whole distinction the report needs. Asking
            # `show lcp` alone could not make it on a box with no pairs.
            _, lcp = self.dp.run("show lcp")
            irc, iface_out = self.dp.run("show interface")
            self._vpp_hosts = routes.vpp_owned_interfaces(
                lcp, iface_out if irc == 0 else "")
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

        # MEASURED, not padded to a guess (#392). The columns were fixed at
        # 20, 17 and 12, which fits an IPv4 box and nothing else: an IPv6
        # prefix is 43 columns at its widest, a next hop 39 and an interface
        # name 28, so a v6 route ran past the console and the fold landed
        # inside the columns - where a reader cannot tell a wrapped next hop
        # from a second route.
        #
        # Two shapes, and which one is used is decided by what the box
        # actually holds rather than by a flag: while the widest row fits, it
        # is the table an operator reads down; when it does not, each entry
        # becomes a small block whose every row ends in its value, so there is
        # nothing behind a fold to break.
        widths = route_column_widths(
            [(p, next_hop_column(v), i) for p, e in rows.items()
             for v, i in (e["via"] or [(COLUMN_NONE, COLUMN_NONE)])],
            ["Destination", "Next hop", "Interface"])
        if widths:
            pw, vw, iw = widths
            print(f"  {'Destination':<{pw}} {'Next hop':<{vw}} "
                  f"{'Interface':<{iw}} Kind")
            for prefix in sorted(rows, key=routes._sort_key):
                e = rows[prefix]
                via, iface = e["via"][0] if e["via"] \
                    else (COLUMN_NONE, COLUMN_NONE)
                via = next_hop_column(via)
                print(f"  {prefix:<{pw}} {via:<{vw}} {iface:<{iw}} "
                      f"{e['kind']}")
                for via, iface in e["via"][1:]:
                    via = next_hop_column(via)
                    print(f"  {'':<{pw}} {via:<{vw}} {iface:<{iw}} (also)")
        else:
            for prefix in sorted(rows, key=routes._sort_key):
                e = rows[prefix]
                print(f"  {prefix}")
                for via, iface in (e["via"]
                                   or [(COLUMN_NONE, COLUMN_NONE)]):
                    print(f"      via:  {next_hop_column(via)}")
                    print(f"      on:   {iface}")
                print(f"      kind: {e['kind']}")
        print()
        print_prose(f"{len(rows)} entr{'y' if len(rows) == 1 else 'ies'} "
                    f"carrying traffic", indent="  ")

    def _route_rows_verbose(self, table, rib, wanted):
        rows = routes.compare(table, rib, getattr(self, "_vpp_hosts", None))
        if wanted:
            rows = [r for r in rows
                    if any(r[0].startswith(x) or x.startswith(r[0].split("/")[0])
                           for x in wanted)]
        if not rows:
            print("  no matching entry in either table")
            return

        # The same measurement, over this screen's five columns. The mark is
        # the VTY's own words and it goes LAST on the row, which is where it
        # already was: a fold that lands in it costs a reader nothing, and it
        # is the one thing here that is not a value.
        # THE COLUMNS ARE ORDERED SO THE UNBOUNDED ONE IS LAST (#392). A
        # prefix, a next hop and an interface name all have a widest; what VPP
        # calls the route does not, because it is VPP's word and not this
        # box's. Last on the row, a fold in it costs a reader nothing.
        widths = route_column_widths(
            [(p, rib_column(r), next_hop_column(v), i)
             for p, r, _f, _k, v, i in rows],
            ["Destination", "FRR", "Next hop", "Interface"])
        if widths:
            pw, rw, vw, iw = widths
            print(f"  {'Destination':<{pw}} {'FRR':<{rw}} "
                  f"{'Next hop':<{vw}} {'Interface':<{iw}} VPP")
        else:
            print("  Destination, and under each what holds it")
        missing = []
        for prefix, in_rib, in_fib, kind, via, iface in rows:
            mark = ""
            if in_rib and not in_fib:
                mark = "  ** in FRR, NOT forwarding"
                missing.append(prefix)
            if not widths:
                print(f"  {prefix}")
                print(f"      FRR:  {rib_column(in_rib)}")
                print(f"      via:  {next_hop_column(via)}")
                print(f"      on:   {iface}")
                print(f"      VPP:  {kind}")
                if mark:
                    print(f"     {mark.strip()}")
                continue
            pw, rw, vw, iw = widths
            print(f"  {prefix:<{pw}} {rib_column(in_rib):<{rw}} "
                  f"{next_hop_column(via):<{vw}} {iface:<{iw}} {kind}")
            # THE MARK ON A ROW OF ITS OWN, under the route it is about. It is
            # the VTY's own words, and behind a value with no widest they are
            # what a fold lands in; under the row they are also what an
            # operator's eye goes to.
            if mark:
                print(f"     {mark.strip()}")


        if missing:
            # The whole reason this command exists. Two lists side by side and
            # a reader left to compare them is not an answer.
            print()
            print_prose(
                f"{len(missing)} prefix"
                f"{'' if len(missing) == 1 else 'es'} in FRR's table and not "
                f"in the forwarding table.", indent="  ")
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

        The daemons are here because seven of this VTY's own messages already
        sent the operator to this command to find out which are running, and
        it answered with three version strings (#272). The hint is printed by
        a failed commit and by every `show bgp`, `show ospf` and `show isis`
        that got no answer - the moments a daemon is the likeliest cause - so
        a dead end here costs more than no hint at all: the reader concludes
        the daemon is fine.
        """
        print(f"Prisma Routing {prisma_routing_version()}")
        rc, out = self.dp.run("show version")
        print(f"  dataplane: {out.strip().splitlines()[0] if rc == 0 and out.strip() else 'not responding'}")
        self.show_plugin_build()
        rc, out = self.frr.show("show version")
        line = out.strip().splitlines()[0] if out.strip() else ""
        print(f"  routing:   {line or 'not responding'}")
        self.show_daemons()

    #: The column the rows of `show version` line their values up on, and the
    #: indent the prose under them is wrapped against.
    #
    # "  dataplane: ", "  routing:   " and "  daemons:   " are all thirteen
    # wide. Written down because the sentences below are wrapped against it by
    # measurement, and a hand-counted string of spaces is the thing #252 found
    # shipping an 82-column line under the belief it was inside 80.
    VERSION_INDENT = " " * 13

    def show_plugin_build(self):
        """
        Which build the RUNNING dataplane is, and a sentence when that is not
        the build the rest of this box is.

        THE POINT OF THE ROW. `show version` printed the VERSION file and
        VPP's own version, and neither of those is the plugin: the first is a
        stamp `appliance/install.sh` writes at install time, the second is
        upstream's and says nothing about ours. An upgrade writes the new
        bpa_plugin.so and does NOT restart the dataplane - deliberately, since
        restarting it ends every session on the box - so between an upgrade
        and the next reboot every word on this screen was the new build except
        the thing actually forwarding packets. There was no reading anywhere
        on the box that could contradict it (#291).

        THE POINT OF SAYING NOTHING WHEN THEY AGREE. That is the normal state
        and it is the state on every box that has been rebooted since its
        upgrade. A paragraph printed under every `show version` is a paragraph
        read once and skipped for ever, including on the box where it means
        something - the argument `unchecked_note` makes for the same decision.

        Four answers and not two, because they are four different faults with
        four different fixes, and the `show_daemons` comment below is the same
        distinction argued at length: not answering is not the same as
        answering with nothing, and neither is a disagreement.
        """
        build = self.dp.plugin_build()
        mine, source = prisma_routing_version_source()

        if build is None:
            # NOT "the plugin is not loaded". A dataplane that cannot be
            # reached is a box forwarding nothing at all, and a dataplane that
            # is up without our plugin is a box that forwards and translates
            # nothing. An operator who reads the first as the second goes
            # looking in the plugin path on a box where VPP is not running.
            print("  plugin:    the dataplane is not answering, so its "
                  "build is not known")
            print_prose("this box is not forwarding at all rather than "
                        "forwarding with an old build: "
                        + dataplane_journal_advice(),
                        indent=self.VERSION_INDENT)
            return

        if not build:
            print("  plugin:    the dataplane is answering and reports no "
                  "build")
            # 'systemctl status vpp' and not 'vppctl show plugins', even
            # though the second is where the answer is written out. This VTY
            # exists to keep an operator out of vppctl (#98), and a message
            # that sends them there at the first difficulty gives that up for
            # a list they would then have to interpret. The journal says which
            # plugins VPP loaded AND why one was refused, which is the whole
            # question, and it is the same door show_daemons opens for FRR.
            print_prose("VPP is up and did not load bpa_plugin.so, so this "
                        "box forwards and translates nothing: 'systemctl "
                        f"status {dataplane_unit()}' and its journal say why "
                        "the plugin was left out",
                        indent=self.VERSION_INDENT)
            return

        print(f"  plugin:    {build}")

        if source != VERSION_FROM_STAMP:
            # THE COMPARISON IS NOT AVAILABLE, which is a third answer and not
            # a disagreement. Both of these reach it and they are said
            # differently, because what the reader should do about them is
            # different: one is a box missing a file it should have, the other
            # is a developer's checkout behaving exactly as it should.
            #
            # What neither of them may do is go SILENT. "the two agree" and
            # "there was nothing to compare" are different answers, and a
            # screen that says nothing is read as the first - which is the
            # same conflation this whole issue is about, one step quieter.
            if source == VERSION_FROM_TREE:
                # The repository's VERSION is "0.71.0": the number the NEXT
                # release will carry, not a build stamp, and it moves when
                # somebody edits it rather than when anything is built. Held
                # up against a build stamp it never matches, so every
                # developer running this VTY out of the tree was being told
                # their box needed a reboot.
                print_prose("this VTY is running from a source tree, so the "
                            "version above is the repository's own VERSION "
                            "file: it names the next release rather than any "
                            "build, and there is nothing here to compare the "
                            "dataplane against. An installed box reads a "
                            "build stamp instead.",
                            indent=self.VERSION_INDENT)
            else:
                print_prose("this box cannot state its own version, so there "
                            "is nothing here to compare the dataplane "
                            "against", indent=self.VERSION_INDENT)
            return

        # THE decision, and it is made in one place. The refusal a dataplane
        # gives while these two disagree says so too (#293), and a condition
        # spelled out twice is two answers to one question.
        if not version_split(build, mine, source):
            return

        # No version string inside the prose, and that is #252's rule applied
        # where it is cheap to apply: both of them are already on the screen
        # whole, one on the row above and one on the first line of this
        # screen, so the sentence can point at them instead of carrying copies
        # that a wrap could break in half.
        print_prose("this is the build the dataplane was loaded with at the "
                    "last boot, and it is not the Prisma Routing build above. "
                    "An upgrade replaces bpa_plugin.so on disk and does not "
                    "restart the dataplane, so until this box is rebooted, "
                    f"or 'systemctl restart {dataplane_unit()}' is run, it "
                    "goes on translating with the old one - and both of those "
                    "end every session it is carrying.",
                    indent=self.VERSION_INDENT)

    def show_daemons(self):
        """
        Which routing daemons are up, named one by one, both ways.

        Stopped daemons are printed as well as running ones because of who is
        reading: they arrived from a message that named ONE daemon - "bgpd may
        not be running" - so the answer they need is about bgpd by name. A
        list of what is up answers that only by an absence the reader has to
        notice, and a reader scanning for a word that is not there is how the
        wrong conclusion gets drawn.

        Everything FRR reports is shown as running, not only what this VTY can
        configure, because that half is a measurement and narrowing a
        measurement to a list loses whatever the list forgot. The stopped half
        is the other way round: it can only be the daemons this box knows to
        expect.
        """
        running = self.frr.daemons()
        if running is None:
            # NOT "no daemons running". Those are different faults with
            # different fixes, and this one has to be said in a sentence the
            # operator cannot read as the other.
            print("  daemons:   FRR is not answering, so which daemons are "
                  "running is not known")
            print_prose("this box's routing control plane is down rather "
                        "than idle: 'systemctl status frr' and its journal "
                        "say why", indent="             ")
            return
        expected = configurable_daemons()
        stopped = [d for d in expected if d not in running]
        self.print_daemon_list("  daemons:   running  ", sorted(running))
        self.print_daemon_list("             stopped  ", stopped)
        if not stopped:
            return
        # Which sentence depends on WHICH daemon is missing, and getting that
        # wrong sends the operator to a file that will not mention it:
        # frrinit.sh starts zebra, mgmtd and staticd itself, so one of those
        # being absent means frr did not finish starting.
        stalled = [d for d in stopped if d in ALWAYS_STARTED]
        if stalled:
            print_prose(enable_daemon_advice(stalled[0]),
                        indent="             ")
        else:
            print_prose("a stopped daemon takes the configuration that needs "
                        "it and reaches nothing; enable it in "
                        "/etc/frr/daemons and restart frr",
                        indent="             ")

    @staticmethod
    def print_daemon_list(head, names):
        """
        One labelled row of daemon names, wrapped underneath its own label.

        Wrapped rather than printed whole because the length is not this VTY's
        to predict: the running half is whatever FRR reports, and a box with
        ldpd, bfdd, pathd and the rest enabled runs well past the 80 columns
        of the serial console these messages are read on (#252). No name is
        ever split, because console_prose does not break words - so a daemon
        name stays greppable, which is the half of that rule that matters
        here.
        """
        rows = console_prose(" ".join(names) or "(none)",
                             indent=" " * len(head))
        print(head + rows[0].lstrip())
        for row in rows[1:]:
            print(row)

    # ------------------------------------------------ HA: do we agree?

    def say_divergence(self, mark, findings):
        """
        One row of prose per pool, then the two statements, each whole.

        `say_finding` wraps the reason and prints ONE statement under it; a
        divergence has two, because "block-size 2048" means nothing without
        the 1024 it is being compared against. So the reason and this box's
        statement go through `say_finding` unchanged, and the peer's statement
        is the row below it - still a statement on a row of its own, never
        folded into the prose, because it is what the operator greps for and
        types back (#271, #276).
        """
        for name, here, there in findings:
            if not there:
                self.say_finding(
                    mark, here,
                    f"pool '{name}' is on this box and not on the peer, so "
                    "after a failover its subscribers are translated by "
                    "nothing at all")
            elif not here:
                self.say_finding(
                    mark, there,
                    f"pool '{name}' is on the peer and not on this box, so "
                    "the peer translates subscribers this box does not know "
                    "about")
            else:
                self.say_finding(
                    mark, f"this box: {here}",
                    f"pool '{name}' does not map subscribers the same way on "
                    "the two boxes, so a failover moves them to other public "
                    "addresses and port blocks, and neither box logs anything")
                print(f"    the peer: {there}")

    def show_ha(self, args):
        """
        Whether this box and its peer would map subscribers the same way.

        Answers the three questions in that order: what this box carries, what
        the peer carries, and whether the two agree. Each of them can be
        UNKNOWN, and unknown is printed as unknown - the one answer this
        command may never give is a quiet "they agree" over a comparison it
        did not make.

        It is `show cgnat ha` and not `show bpa ha`: the other `show cgnat`
        commands are the dataplane's own, forwarded word for word, and the
        dataplane holds ONE box's configuration and has never heard of the
        other. This comparison is between two configurations, so it is answered
        where both can be read.
        """
        path = None
        if args[:1] == ["peer"] and len(args) > 1:
            path = args[1]
        elif args:
            self.err("show cgnat ha: takes nothing, or 'peer <file>'")
            return

        print("  this box")
        if not self.dp.reachable():
            # Without this the empty configuration a dead dataplane produces is
            # compared as if it were the truth, and a box with ten pools
            # reports every one of them as "the peer has it and this box does
            # not" - or, if the peer file is empty too, as agreement.
            print_prose("the dataplane is not answering, so this box cannot "
                        "say what it maps and nothing below would mean "
                        "anything")
            return
        mine = self.running_config()
        print(f"    mapping fingerprint  {mine.mapping_fingerprint()}")
        print("    pools                "
              + (", ".join(sorted(mine.pools)) if mine.pools else "(none)"))

        print()
        print("  role")
        rows = self.device_vrrp_state()
        if not rows:
            print_prose("no virtual router on this box, so it elects nothing "
                        "and takes no service address over. What is checked "
                        "below is whether a takeover WOULD land subscribers "
                        "where they already are.")
        for row in rows:
            print(f"    {row['interface']} vrrp {row['vr_id']}   "
                  f"{row['state']}")
            print(f"      address(es)        "
                  + ", ".join(str(a) for a in row["addresses"]))
            print(f"      virtual mac        {row['mac']}")
            # Both numbers, and only when they differ. A box advertising less
            # than it was configured for is a box with a tracked interface
            # down, and that is the reading an operator needs before they
            # start looking for a VRRP problem that is really a cable.
            if row["advertising"] != row["configured"]:
                print(f"      priority           {row['advertising']} "
                      f"(configured {row['configured']}, and something it "
                      f"tracks is down)")
            else:
                print(f"      priority           {row['configured']}")

        print()
        peer, why = read_peer_config(path)
        where = path or PEER_CONFIG_PATH
        if peer is None:
            print("  peer")
            print_prose(why)
            print_prose("nothing here says the two boxes agree, and an "
                        "unanswered question is not a match")
            return
        print(f"  peer, from {where}")
        print(f"    mapping fingerprint  {peer.mapping_fingerprint()}")
        # No "(none)" case here, unlike this box above: read_peer_config
        # refuses a configuration with no pool in it rather than handing one
        # back to be compared.
        print("    pools                " + ", ".join(sorted(peer.pools)))
        # The date, because this file is a COPY and a copy has an age. A digest
        # that matches a configuration the peer stopped running last week is
        # the same false agreement as no check at all.
        try:
            when = time.strftime("%Y-%m-%d %H:%M:%S",
                                 time.localtime(os.path.getmtime(where)))
            print(f"    copied               {when}")
        except OSError:
            pass

        # THE PAIR'S OWN HALF, read from the same file as the mapping. The
        # peer's configuration is what `save` wrote on the other box, so it
        # carries that box's `vrrp` statements - and two boxes that map every
        # subscriber identically can still be a pair that never fails over,
        # or one whose master is decided by the addressing.
        print()
        print("  the pair")
        pair = vrrp_pair_findings(mine.vrrp, peer.vrrp)
        if not mine.vrrp and not peer.vrrp:
            print_prose("neither box carries a virtual router, so neither "
                        "takes a service address over", indent="    ")
        elif not pair:
            print_prose("every virtual router this box carries has one on the "
                        "peer with the same id, the same address and a "
                        "different priority", indent="    ")
        else:
            for line in pair:
                self.say_finding("warning", "", line)

        print()
        findings = mine.mapping_diff_to(peer)
        if not findings:
            print_prose("the two boxes map every subscriber to the same "
                        "public address and the same port block", indent="  ")
            return
        self.say_divergence("warning", findings)

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
        Write the running configuration to disk, never world-readable.

        The mode is not housekeeping. Since accounts became part of the
        configuration this file carries password hashes and public keys, so a
        world-readable copy hands every local account an offline cracking
        target.

        What is on disk already is left exactly as it is, and the installer
        owns it - see CONFIG_UMASK. A save that re-stated the mode narrowed the
        directory the operator's own group had been given, so the first save on
        the box was the last one a non-root account could make (#188).
        """
        d = os.path.dirname(CONFIG_PATH)
        old_umask = os.umask(CONFIG_UMASK)
        try:
            if d:
                os.makedirs(d, exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                f.write(self.running_config().text())
        except OSError as e:
            # The path on a row of its own: with the errno text beside it the
            # one row measured 120 columns, and the path is what the operator
            # goes and looks at (#276).
            self.err("cannot write the configuration file")
            print(f"    {CONFIG_PATH}")
            print_prose(f"({e.strerror})", indent="        ")
            return
        finally:
            os.umask(old_umask)
        print(f"configuration saved to {CONFIG_PATH}")
        # The one place the two halves of this can disagree.
        #
        # "commit confirmed" puts the RUNNING configuration back; `save`
        # decides what the box boots into. An operator who saves inside an
        # open window has written the unconfirmed change into the file the
        # next boot reads, and the revert does not go back and rewrite it -
        # so the box would revert and then come back on the change at the
        # next reboot. Said here rather than fixed by having `save` refuse:
        # saving a change you are still testing is a reasonable thing to do,
        # and the two commands are deliberately separate (#212).
        pending = self.confirm.read()
        if pending:
            when = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                 time.gmtime(pending["deadline"]))
            # A warning and not an error: the save DID happen, and a script
            # that reads the exit status must not be told it did not.
            print("warning: what you just saved has NOT been confirmed")
            # Wrapped by measurement and not by eye: hand-wrapped, the first
            # of these rows was 87 columns, which is the defect #252 is named
            # for showing up in a command nobody had driven (#276).
            print_prose(f"this box rolls the running configuration back at "
                        f"{when}, and does not touch the file you just wrote "
                        "- so a reboot after that would bring the unconfirmed "
                        "change back. Type 'commit' to confirm, then 'save' "
                        "again")

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
            state = w[2] if len(w) > 2 else ""
            names[w[0]] = f"interface ({state})" if state else "interface"
        # `local0` and the TAP half of every pair, out of the one holder the
        # screens read. This branch kept its own list and it had one name on
        # it, so TAB completed `tap4096` as an interface to configure - and a
        # TAP is the half of a pair this product creates and destroys (#403).
        return {n: v for n, v in names.items()
                if n in set(routes.operator_interfaces(names))}

    def dyn_host_nics(self):
        """
        The cards LINUX has, with the one carrying the default route marked.

        Marked rather than withheld. The operator is choosing between cards
        that all look alike at the prompt, and the one they are reached over is
        the one the answer has to say something about - so naming it here is
        the difference between a refusal they read after typing and a choice
        they never make.

        AND THE MARK SAYS "REFUSED" NOWHERE (#385). It said so unconditionally
        once and was wrong on the one-card box (#309); then it said so on the
        box with another card, where the VTY refused the name. That refusal is
        gone - a named card is claimed whichever route it carries - so what
        the mark says is what happens to the card: its address moves with it
        onto the linux_cp pair, on every box.
        """
        default = self.default_route_nic()
        names = [n["name"] for n in self.host_nics()]
        # AND IT FITS THE CONSOLE. display_matches puts the name, two spaces
        # and this text on one row, so the text is as wide as itself plus the
        # longest name. "the only card, so it is handed over and its address
        # goes with it" measured 100 columns against a 79-column console
        # (#346), on the row an operator reads BEFORE typing the name - so the
        # sentence telling them what handing this card over means was the one
        # the terminal broke. This one is 51.
        return {n: "carries the default route: its address moves with it"
                if n == default else "card"
                for n in names}

    def dyn_neighbours(self):
        """
        The BGP neighbours this box has, with their AS where it is known.

        Read from the configuration, which is read from FRR - not from a model
        kept here. An operator inside `address-family ipv4 unicast` typing
        `neighbor <TAB>` is looking for a session they already configured; the
        answer that helps is the address AND who is on the other end, because
        an ISP's box carries several and they are told apart by AS, not by the
        last octet.
        """
        cfg = self.candidate or self.running_config()
        seen, groups = {}, set()
        for line in cfg.routing:
            w = line.split()
            if len(w) >= 2 and w[0] == "neighbor":
                seen.setdefault(w[1], "neighbour")
                if len(w) == 3 and w[2] == "peer-group":
                    groups.add(w[1])
                elif len(w) >= 4 and w[2] == "remote-as":
                    seen[w[1]] = f"AS {w[3]}"
                elif len(w) >= 4 and w[2] == "peer-group":
                    seen[w[1]] = f"peer-group {w[3]}"
        # A group is completed in the same place as an address, because the
        # same statements apply to it - but it is not a session, and calling
        # it "neighbour" here sends an operator looking for a router at that
        # name. Its AS still shows when it has one: that is what the members
        # inherit.
        for g in groups:
            seen[g] = ("peer group" if seen.get(g, "neighbour") == "neighbour"
                       else f"peer group, {seen[g]}")
        return seen

    def dyn_route_maps(self):
        """The route-maps this box has, by name, with what the first entry does."""
        cfg = self.candidate or self.running_config()
        found = {}
        for line in cfg.routing:
            w = line.split()
            if len(w) >= 3 and w[0] == "route-map":
                # "route-map RM permit 10" - several entries share a name, and
                # the operator completing it wants the name once.
                found.setdefault(w[1], f"route-map ({w[2]})")
        return found

    def dyn_peer_groups(self):
        """
        Peer groups declared in the configuration.

        A peer group is declared by `neighbor <name> peer-group` with nothing
        after it, and USED by `neighbor <address> peer-group <name>`. Only the
        declaration makes the group exist, so only that spelling is offered -
        completing a name that was only ever used would offer a group FRR does
        not have.
        """
        cfg = self.candidate or self.running_config()
        found = {}
        for line in cfg.routing:
            w = line.split()
            if len(w) == 3 and w[0] == "neighbor" and w[2] == "peer-group":
                found[w[1]] = "peer group"
        return found

    def received_as_paths(self):
        """
        Every AS path this box has been offered, as FRR renders them.

        From `show bgp ipv4 unicast json`, because the human table wraps a long
        path onto the next line and a parser that reads it by column loses
        exactly the long paths a regex is most likely to get wrong.

        A box with no BGP, or with FRR down, has no paths - which is not a
        failure and must not be reported as one.
        """
        rc, out = self.frr.show("show bgp ipv4 unicast json")
        if rc != 0 or not out.strip():
            return []
        try:
            data = json.loads(out[out.index("{"):])
        except (ValueError, json.JSONDecodeError):
            return []
        paths = []
        for entry in (data.get("routes") or {}).values():
            for p in entry if isinstance(entry, list) else []:
                ap = p.get("path")
                if ap is None:
                    ap = (p.get("aspath") or {}).get("string") \
                        if isinstance(p.get("aspath"), dict) else p.get("aspath")
                if ap is not None:
                    paths.append(str(ap).strip())
        return paths

    def report_as_path_reach(self, routing):
        """Say what each AS-path regex matches, against paths really received."""
        lists = [l.strip() for l in routing
                 if l.split()[:3] == ["bgp", "as-path", "access-list"]]
        if not lists:
            return
        paths = self.received_as_paths()
        if not paths:
            # Said out loud rather than skipped in silence: "matches 0" and
            # "there was nothing to match against" are different answers, and
            # only one of them is about the regex.
            print("note: no BGP paths received yet")
            print_prose("AS-path lists cannot be checked against anything")
            return
        for stmt in lists:
            name, regex = as_path_parts(stmt)
            if name is None or not regex:
                continue
            hit, example = as_path_matches(regex, paths)
            line = f"note: {name} '{regex}' matches {hit} of {len(paths)} " \
                   "received AS path(s)"
            if example:
                line += f"; e.g. '{example}'"
            print(line)

    def dyn_isis_tags(self):
        """
        The IS-IS instances this box has, by their tag.

        Read from the configuration, which is read from FRR. The tag was
        offered as a bare `<word>` before, so an operator writing `ip router
        isis` on an interface had to remember what they had called the
        instance - and getting it wrong produces an interface that joins
        nothing, silently.
        """
        cfg = self.candidate or self.running_config()
        found = {}
        for line in cfg.routing:
            w = line.split()
            if w[:2] == ["router", "isis"] and len(w) > 2:
                found[w[2]] = "IS-IS instance"
        return found

    def dyn_as_path_lists(self):
        """AS-path access-lists by name, with what the first entry does."""
        cfg = self.candidate or self.running_config()
        found = {}
        for line in cfg.routing:
            w = line.split()
            if len(w) >= 4 and w[:3] == ["bgp", "as-path", "access-list"]:
                found.setdefault(w[3], "as-path list")
        return found

    def dyn_community_lists(self):
        """
        Community and large-community lists by name.

        Both in one answer, and labelled, because a route-map's `match
        community` and `match large-community` are different statements and an
        operator completing a name in either place wants to see which is which
        rather than a list that does not say.
        """
        cfg = self.candidate or self.running_config()
        found = {}
        for line in cfg.routing:
            w = line.split()
            if len(w) >= 3 and w[0] == "bgp" and w[1] == "community-list":
                # "bgp community-list standard NAME permit ..." and
                # "bgp community-list NAME permit ..." are both spelled.
                name = w[3] if w[2] in ("standard", "expanded") else w[2]
                found.setdefault(name, "community list")
            elif len(w) >= 3 and w[0] == "bgp" and w[1] == "large-community-list":
                name = w[3] if w[2] in ("standard", "expanded") else w[2]
                found.setdefault(name, "large-community list")
        return found

    def dyn_prefix_lists(self):
        """
        The prefix-lists this box has, read back from the control plane.

        Not from a model this VTY keeps: FRR owns them, stores them and reports
        them, and the running configuration is read from FRR. A second copy
        here would be a second thing to keep in step.

        Both families in one answer. `ip prefix-list` and `ipv6 prefix-list`
        are separate namespaces in FRR, but an operator completing a name is
        looking for the one they wrote, and offering the other costs nothing -
        naming an IPv6 list after `ip prefix-list` is refused by FRR with its
        own message, which is better than this VTY inventing one.
        """
        cfg = self.candidate or self.running_config()
        found = {}
        for line in cfg.routing:
            w = line.split()
            if len(w) >= 3 and w[0] in ("ip", "ipv6") and w[1] == "prefix-list":
                found.setdefault(w[2], f"{w[0]} prefix-list")
        return found

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
        ARG_INTERFACE_NEW: "dyn_interfaces",
        ARG_HOST_NIC: "dyn_host_nics",
        ARG_POOL: "dyn_pools",
        ARG_USER: "dyn_users",
        ARG_SNMP_USER: "dyn_snmp_users",
        ARG_PREFIX_LIST: "dyn_prefix_lists",
        ARG_NEIGHBOUR: "dyn_neighbours",
        ARG_ROUTE_MAP: "dyn_route_maps",
        ARG_PEER_GROUP: "dyn_peer_groups",
        ARG_ISIS_TAG: "dyn_isis_tags",
        ARG_AS_PATH_LIST: "dyn_as_path_lists",
        ARG_COMMUNITY_LIST: "dyn_community_lists",
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

    def block_grammar(self, words):
        """
        What `?` offers inside the block being typed in.

        Never None: a block this VTY does not model offers `exit` and nothing
        else, because offering a guess as a grammar is worse than offering
        nothing. That is deliberately NOT the same answer the module-level
        `block_grammar` gives, which returns None there - "this VTY has no
        grammar for this block" is what the boundary marker has to know, and
        "there is still an `exit`" is what the operator has to be offered
        (#141). One walk, two readings of it, so they cannot drift apart.
        """
        return block_grammar(self.frr_ctx[-1], words) or {
            "exit": ("leave the block", None)}

    def _options(self, line, ending_word):
        # After a pipe, the only words that mean anything are the filters. The
        # command's own grammar does not apply there and offering it would
        # send the operator back into the command they have already finished
        # typing.
        if line.split()[:1] == ["show"] and "|" in line:
            tail = line.rsplit("|", 1)[1]
            words = tail.split()
            prefix = words[-1] if (ending_word and words) else ""
            if len(words) - (1 if ending_word and words else 0) == 0:
                return ({k: v[0] for k, v in PIPE_FILTERS.items()
                         if _starts(k, prefix)}, None, prefix)
            verb = words[0]
            return {}, (PIPE_FILTERS.get(verb) or (None, None))[1], prefix
        words = line.split()
        prefix = ""
        if ending_word and words:
            prefix = words[-1]
            words = words[:-1]
        node, hint = tree_walk(self.grammar_mode(), words)

        # A block entered by an ARGUMENT is still mode "config", so the tree
        # walk returns the whole configuration tree - fifteen options inside an
        # interface, of which three are valid there. Offering the level above
        # invites somebody to type "cgnat pool" inside an interface and find
        # out afterwards what that meant (#112).
        #
        # The blocks the tree knows about were always right: a pool offers its
        # own, a user its own. Only these needed narrowing.
        # Inside a block, `show` is the whole show tree and not just the word.
        # Every one of its sub-commands works there - measured, not assumed:
        # `show this`, `show version`, `show configuration` and the rest all
        # answer from inside a pool and from inside an interface. What `?`
        # offered after the word was nothing, in every block, so `show this`
        # could be reached only by typing it blind - and it is the only way to
        # read a block back before committing (#221).
        if words[:1] == ["show"] and (self.frr_ctx or self.mode in BLOCK_MODES):
            node, hint = walk_from(SHOW, words[1:])
        elif (self.mode != "operational" and words[:1]
                and words[0] in PROBE_TOOLS):
            # A probe is answered in configuration mode too (#400), so what `?`
            # offers after the word has to be the same thing it offers outside
            # - the ceiling on the count, the host hint. Walked from the
            # operational tree rather than copied into this branch, because a
            # copy is what would go out of date the next time a probe grows an
            # option.
            node, hint = walk_from(TREE["operational"], words)
        elif self.frr_ctx:
            grammar = self.block_grammar(words)
            if grammar is not None:
                node, bhint = walk_from(grammar, words)
                if node is None:
                    if bhint:
                        dyn = self.dynamic_options(bhint, prefix)
                        return (dyn, bhint, prefix) if dyn else ({}, bhint,
                                                                 prefix)
                    return {}, None, prefix
                opts = {k: v[0] for k, v in node.items()}
                if not words:
                    # `show` works in every block and is most useful inside
                    # one, where `show this` is the only way to read back what
                    # has been typed so far.
                    opts["show"] = "display information"
                    opts.update(self.level_words())
                return ({k: v for k, v in opts.items() if _starts(k, prefix)},
                        None, prefix)

        if node is None:
            # The tree knows a name goes here; the box knows which names.
            dyn = self.dynamic_options(hint, prefix)
            if dyn:
                return dyn, hint, prefix
            return {}, hint, prefix
        opts = {k: v[0] for k, v in node.items() if _starts(k, prefix)}
        # The same `show` the FRR branch above adds, for the blocks this
        # product models itself. They are not in frr_ctx - a pool, a user and
        # an SNMP user are MODES, walked by tree_walk - so they never reached
        # that branch, and the comment there says "every block" while the code
        # covered half of them.
        #
        # It is the shape of #187 and #99: `show this` is the only way to read
        # back what has been typed into a block before committing, it works in
        # all six blocks, and in three of them it could only be reached by
        # typing it blind. The pool is the one an operator needs most and the
        # one the wiki uses as its example (#221).
        if not words and self.mode in BLOCK_MODES:
            if _starts("show", prefix):
                opts["show"] = "display information"
        # `top`, `up` and the probes, at whatever level this is. The block
        # branch above returns before reaching here, so both paths say it.
        if not words and self.mode != "operational":
            opts.update({k: v for k, v in self.level_words().items()
                         if _starts(k, prefix)})
        # A node can offer keywords AND take a name - `show interface` offers
        # `terse` and every interface on the box. Before this, a node with a
        # subtree discarded the hint, so adding `terse` to `show interface`
        # would have taken away the interface names it had always completed:
        # a new option costing an old one, which is how a CLI gets a
        # reputation. The hint rides IN the dict, as a key, so the tree still
        # reads as one thing.
        for key in node:
            if key in self.DYNAMIC:
                opts.pop(key, None)
                opts.update(self.dynamic_options(key, prefix))
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
            # The shapes behind a hint, where the hint alone cannot carry
            # them. Folded by console_prose rather than written as one row:
            # the sentence is 90 columns and this is a screen an operator
            # reads on a serial console (#252).
            note = HINT_NOTES.get(hint)
            if note:
                for line in console_prose(note):
                    print(line)
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
        # ONCE, BEFORE DISPATCH, so every reader downstream - the handlers,
        # the candidate, `show this`, `save` and the file it writes - sees the
        # full spelling and no abbreviation ever reaches any of them. `no
        # shut` was staged as an FRR statement in silence, FRR accepted it
        # inside its own interface stanza and kept nothing, and the interface
        # stayed down through a commit that said so only afterwards (#393).
        line = self.spell_out(line)
        if line is None:
            return True
        handler = {"operational": self.do_operational,
                   "config": self.do_config,
                   "pool": self.do_pool,
                   "user": self.do_user,
                   "snmp-user": self.do_snmp_user}[self.mode]

        # A pipe filters a `show`, and only a `show`. Everywhere else a '|' is
        # a character in a regular expression or a description - an AS-path
        # list is full of them - and treating it as a filter would silently
        # cut a policy in half (#191).
        if line.split()[:1] == ["show"] and "|" in line:
            command, stages = split_pipe(line)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                result = handler(command)
            lines, why = apply_pipe(out.getvalue(), stages)
            if why:
                self.err(why)
            else:
                for l in lines:
                    print(l)
            return result
        return handler(line)

    def grammar_mode(self):
        """
        Which tree `?`, TAB and abbreviations are read from here: the mode's
        own, or - standing in `nat` or `cgnat` in configuration mode - that
        level's (#446). An FRR block is never inside those, so it wins.
        """
        if self.mode == "config" and self.nat_ctx and not self.frr_ctx:
            return self.nat_ctx[-1]
        return self.mode

    def statement_grammar(self):
        """
        The node an abbreviation is resolved against here, or None for none.

        Three places, and the boundary is where this VTY owns the words. The
        two mode roots are its own grammar whole. The `interface` block is the
        one FRR block whose statements this box applies itself, which is why a
        word inside it that begins one of them must never be handed over: that
        is the silent half of #393, and `dataplane_statement()` downstream can
        only recognise the statement it is given.

        Every other block - `router bgp`, `route-map`, `router ospf` - answers
        None on purpose. This tree models a PART of each of those grammars,
        FRR owns all of it, and resolving a prefix against a partial model is
        how a statement FRR was going to act on becomes a different one.
        """
        if self.mode == "operational":
            return TREE["operational"]
        if self.mode != "config":
            return None
        if not self.frr_ctx:
            return TREE[self.grammar_mode()]
        if self.frr_ctx[-1].startswith("interface "):
            return INTERFACE_BLOCK
        return None

    def spell_out(self, line):
        """
        `line` with this VTY's own words spelled out, or None when refused.

        The refusal names the candidates rather than picking one by position.
        The word the operator typed goes into the PROSE and not the verdict,
        for the reason #363 gives: it is their word, so a verdict carrying it
        has no width at all, while the verdict without it is 25 columns and
        says the same thing.
        """
        node = self.statement_grammar()
        if node is None:
            return line
        spelled, ambiguous = resolve_statement(node, line.split())
        if ambiguous is None:
            return " ".join(spelled)
        word, hits = ambiguous
        self.err_prose("an ambiguous abbreviation",
                       f"'{word}' begins {len(hits)} statements here: "
                       + ", ".join(hits),
                       "type enough of one to tell them apart, or '?' to "
                       "read what each does")
        return None

    def feed_batch(self, cmds):
        """
        Run a `prismash -c` batch, and record what it asked for and did not get.

        IS `-c` ONE COMMAND OR A SCRIPT? Both, and the word that settles it is
        `commit`. `prismash -c "show cgnat pool"` is a question: it changes
        nothing, so there is nothing for it to be half of. A sequence of `-c`
        arguments ending in `commit` describes a configuration, and this
        product has already answered that question for the other thing that
        describes one. `load()` refuses a saved file rather than trimming it,
        and wrote down why: applied minus the line this VTY cannot hold, the
        file on disk and the running configuration disagree, on a box nobody
        is watching, and the difference is discovered later by somebody
        reading both.

        THE ARGUMENT CARRIES, because the script is the file. A batch is
        written down somewhere - in a deployment tool, in a runbook, in a
        shell script - and that text is what somebody reads afterwards to
        find out what the box was asked for. A batch committed minus its
        refused statement leaves the script and the box disagreeing in
        exactly the way load() refuses to allow. It was observed doing it:
        `error: invalid argument: block-size 16: expected 64-32768` and then
        `commit complete`, with the pool created and carrying the DEFAULT
        block size (#376).

        So the rule is on the COMMIT and not on the batch: `commit` refuses
        while a statement of this batch was refused, and applies nothing.
        That is what keeps the one-off question working, which a batch that
        stopped at its first refusal would not have needed to keep.

        AND THE BATCH RUNS TO ITS END, which is load()'s shape as well:
        load() does not stop at the first line it cannot hold, it reads the
        whole file and then names every one of them. A batch that stopped
        would report one refusal per run, so an operator fixing a
        twelve-statement batch would meet the second refusal only after
        fixing the first. Nothing the rest of the batch runs can reach the
        box: the box changes at `commit`, and `commit` is refused.

        NOT THE INTERACTIVE SESSION, and the line is where load() draws it -
        on whether anybody is reading the refusal as it happens. The argv of
        a batch was fixed before the first refusal existed, so the batch
        cannot be an answer to one. An operator who types `block-size 16`,
        reads the refusal, types `block-size 1024` and then `commit` has
        answered it, and refusing that commit would be the alarm that is
        always on which #205 and #208 are about.

        WHAT IS NOT COUNTED: a refusal from `commit` itself. The candidate is
        then exactly what the batch asked for and the refusal is about
        applying it, which `commit` says better than this could - lab/ipfix.sh
        drives that pair, a `commit check` refused for a collector that is not
        configured and the `commit` behind it answering "candidate did not
        pass validation; nothing applied".
        """
        for c in cmds:
            before = self.failed
            self.feed(c)
            # `self.failed`, and not what feed() returns: feed() answers
            # whether the session goes on, which is a different question -
            # `exit` returns False and refuses nothing. `failed` is also the
            # counter the exit status below is computed from, so the word
            # this box prints and the status it exits with cannot disagree.
            if self.failed > before and c.split()[:1] != ["commit"]:
                self.batch_refused.append(c)
        return 1 if self.failed else 0

    @sigint_ignored
    def load(self, path):
        """
        Apply a saved configuration file to the device.

        This runs at boot, unattended, which is exactly why it refuses rather
        than trimming: a file with one line this VTY cannot hold would
        otherwise be applied minus that line, on a box nobody is watching, and
        the difference between the file on disk and the running configuration
        would be discovered later by somebody reading both.

        One trim, and only one, and it is not that rule's case: a
        `block-stride` on a deterministic pool, which no allocator ever read.
        The file minus that line is the configuration the box was running, so
        it is dropped and said rather than refused - the argument is on the
        loop below, and Pool.stride_inert() decides which pools it means
        (#375).
        """
        if not os.path.exists(path):
            return
        with open(path) as f:
            text = f.read()

        # THE UPGRADED BOX (#389). A file saved before the card carried its
        # Linux name says `interface GigabitEthernet0/13/0`, and this box
        # holds that card as ens19. Refusing the file - which is what a name
        # the box has not got gets below - would be a box coming up with no
        # pool, no address and no attachment on it, at boot, read by nobody.
        # The box can match the two names by address with nothing guessed,
        # so it loads the file under the new name and says so; the next
        # `save` writes the name it loaded. card_renames() carries the
        # matching and what it refuses to match.
        renames = self.card_renames(text)
        for ifname, host_name in sorted(renames.items()):
            # Two interface names on one row: at their widest, 78 columns
            # with no word between them that the row can spare.
            print(f"note: {ifname} is {host_name} on this box")
            print_prose(f"the card at that address carries its Linux name "
                        f"now, so this file is loaded with {host_name} "
                        f"wherever it said {ifname}; the next 'save' writes "
                        "the new name")
        target = Config.parse(self.renamed_text(text, renames))

        # SAID, because the file and the running configuration differ by
        # these lines until the next `save`, and a difference nobody was told
        # about is discovered later by somebody reading both. Dropped rather
        # than refused, for the reason Config says beside `dropped`.
        # The path and the line each on a row of their own, the split the
        # refusal below already makes: on the box's own path this would
        # measure well past the console, and the line is the one thing here
        # somebody may want to copy (#276).
        for lineno, text, why in target.dropped:
            print(f"note: line {lineno} is dropped: this VTY no longer has "
                  f"the statement")
            print(f"    {path}")
            print(f"    {text}")
            print_prose(f"({why})", indent="    ")

        # AND A STANZA FRR NEVER HELD (#389). A name over FRR_IFNAME_MAX
        # characters - a card this box still calls by the dataplane's own
        # name, because the device is unnamed in startup.conf or this VPP
        # ignored `name` - is refused by FRR, so no version ever had that
        # stanza in FRR. At the prompt the commit check refuses it with the
        # limit; here, unattended, the file minus that stanza is the
        # configuration the box had, and the dataplane half of the interface
        # - addresses, MTU, the CGNAT attachment - loads. The same trim
        # `--load` makes for a `block-stride` a deterministic pool never
        # read, and said out loud for the same reason: until the next
        # `save`, the file and the running configuration differ by it.
        over = [n for n in target.frr_interface_names()
                if len(n) > FRR_IFNAME_MAX]
        for ifname in target.drop_frr_interface_blocks(over):
            print(f"note: interface {ifname}: its routing block was not "
                  "loaded")
            print_prose(f"FRR takes an interface name of at most "
                        f"{FRR_IFNAME_MAX} characters and this one is "
                        f"{len(ifname)}, so FRR held no such block on any "
                        "version; the interface's addresses and attachments "
                        "are loaded. A card named after its Linux card does "
                        "not meet this: the device is unnamed in "
                        "/etc/vpp/startup.conf and wants 'name <linux name>' "
                        "inside its dev block")

        # A `tap<n>` stanza naming an interface this box does NOT have is the
        # Linux end of a pair that existed when the file was saved and does
        # not exist now - which at boot is every pair, because VPP has just
        # started. Left in, the interface-existence check refuses the whole
        # file and the box comes up with nothing on it; this is what makes the
        # boxes that already saved such a file boot again.
        #
        # Conditioned on the dataplane not having it, not on the name alone.
        # `vppctl create tap id 0` also produces a `tap0`, and one that IS
        # there is an interface somebody configured on purpose - lab/hw-vpp.sh
        # runs its whole WAN over exactly that.
        stale = {n for n in target.interface_names()
                 if _is_lcp_tap(n) and self.api.interface_index(n) is None}
        if stale:
            target.drop_lcp_taps(stale)

        # THE STATEMENT THIS FILE IS NOT THE HOLDER OF, and half of #332.
        # `commit` writes the card into the tuner's file, which is what hands
        # it over at the next boot; only `save` writes THIS file. So a box
        # that committed and rebooted replays a file that has never heard of
        # the card. Taken from the holder only where this file is silent: a
        # file that names a card is the operator's own answer and stays it,
        # which is what keeps `no dataplane interface`, `save` and a reboot
        # working.
        if not target.dataplane_if:
            target.dataplane_if = self.read_dataplane_if()
        # THE WAY BACK, FROM THE ONE HOLDER IT HAS (#385). It is the record
        # prisma-routing-tune computed off the live card on THIS boot, in /run,
        # and it is applied whatever the saved file says about the card: a
        # file naming the card by hand, a file that never heard of it and the
        # three comments the installer ships all describe a box that gave its
        # card away on this boot exactly when that record exists. The VTY's
        # own record used to be read here in preference, and only where the
        # file named nothing - so a file that named the card without one,
        # which every override on a box with a spare card produces, was a
        # boot that put nothing back. The other half of #332.
        way_back = self.read_computed_mgmt()
        if target.unknown:
            # Read at BOOT, on a box that is refusing its own saved
            # configuration, which is the worst moment to be handed a broken
            # row. Three things shared one budget here: a path, whose length
            # is the operator's; a configuration line, which is what they go
            # and edit; and a sentence. On the box's own path and a crypt
            # hash pasted without its leading '$' that measured 91 and 222
            # columns (#276). Each gets its own row, the two quoted ones
            # whole and the sentence wrapped - the split do_commit got in
            # #271.
            self.err("refusing to load this file, these lines are not "
                     "understood:")
            print(f"    {path}")
            for lineno, text, why in target.unknown:
                print(f"    line {lineno}: {text}")
                print_prose(f"({why})", indent="        ")
            return

        # A BLOCK-STRIDE ON A DETERMINISTIC POOL, dropped here and refused
        # everywhere an operator types it (#375). The pair has been accepted
        # since 0.67.0 and the allocator never read the stride, so a file
        # saved by any version since can carry it - on a box that ran on that
        # file yesterday. The commit check refuses the pair, and at boot a
        # refusal is a box coming up with no pools on it, read by nobody, to
        # enforce a property about a setting that was already inert.
        #
        # This is not the trimming the docstring refuses. That rule is about
        # a line this VTY cannot hold, where the file minus the line is a
        # different configuration from the file. Here the file minus the
        # line is the configuration the box was running: nothing the stride
        # promised was ever produced, and what goes is the "show cgnat pool"
        # row that promised it. Said out loud because the file and the
        # running configuration now differ by one statement, and the next
        # `save` is what makes them agree again.
        #
        # Same test as the commit check, by construction: Pool.stride_inert()
        # owns it, so a pool refused at the prompt is a pool dropped here and
        # never one the boot applies whole.
        for pool in sorted(target.pools.values(), key=lambda p: p.name):
            if not pool.stride_inert():
                continue
            # The pool named last on the row, the #363 shape: a pool name
            # has no widest, and last on the row the fold lands after the
            # words.
            print(f"note: block-stride {pool.block_stride} dropped from "
                  f"pool {pool.name}")
            print_prose("it applies to dynamic pools only and this pool "
                        "never read it, so the box comes up as it ran; the "
                        "next 'save' writes the file without it, and the "
                        "same pair typed at the prompt is refused")
            pool.block_stride = 0

        # A file with no statements is nothing to apply, and MUST NOT be read
        # as a configuration that removes what is there. The installer ships
        # exactly such a file, so this path runs on the first boot of every
        # new box: the box has SSH access the installer set up, the file does
        # not mention SSH, and the lockout check correctly saw a change that
        # would take that access away. prisma-routing.service failed, and the
        # first thing an operator saw on a new appliance was a failed unit
        # (#121). Returning here rather than weakening the lockout check: the
        # check is right, the input was not a change.
        #
        # The management path is the one exception, and it is not a change
        # either: the box is putting back what its own tuner computed before
        # it gave the card away. An empty file is what every fresh appliance
        # boots with, so without this the boot after the handover is the boot
        # that does nothing at all (#332).
        if target.is_empty():
            # The answer is not checked because there is nothing here to
            # abandon: `err` has already counted the failure, which is what
            # prisma-routing.service and the exit status read.
            if way_back:
                self.apply_management_path(way_back)
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
            # The path on its own row, for the reason above: on the box's own
            # path this measured 84 columns (#276).
            self.err("refusing to load this file, the check above says why")
            print(f"    {path}")
            return
        self.failed = before

        # BEFORE the file's own statements, because the pair this makes is an
        # interface the file may address, and the box's way home has to be
        # there before anything that needs the box. NOT FATAL for the rest of
        # the file: a box whose pair could not be made has already said so
        # through `err`, which is what prisma-routing.service reads, and it
        # still wants its pools and its routing - a box that forwards and is
        # reachable only at its console is better than one that is neither.
        if way_back:
            self.apply_management_path(way_back)

        if self.apply(target):
            try:
                self.rb.push(target.text(), "loaded from file")
            except RollbackUnavailable as e:
                # The reason carries a path, so it goes on a row of its own:
                # together they measured 103 columns (#276). Same split as
                # the identical message in do_commit (#271).
                self.err("loaded, but no rollback revision could be recorded")
                print(f"    {e}")

    def confirm_expired(self):
        """
        Roll back a "commit confirmed" whose deadline has passed.

        This is what prisma-routing-confirm.service runs, as root, every few
        seconds while a window is open - and it is the answer to #212: the
        deadline is enforced by the box rather than by the session that set
        it, so the operator who lost that session is the one it now protects.

        Returns 0 when there is nothing to do or the box is back on the old
        configuration, and 1 when it tried and failed.
        """
        pending = self.confirm.read()
        if pending is None:
            return 0
        if time.time() < pending["deadline"]:
            return 0
        minutes = pending["minutes"]
        if self.revert_confirm(
                pending,
                f"commit confirmed {minutes}: nobody confirmed it within "
                f"{minutes} minute(s), so this box has put the previous "
                "configuration back"):
            return 0
        n = self.confirm.count_attempt(pending)
        if n < CONFIRM_ATTEMPTS:
            self.err(f"the rollback failed (attempt {n} of "
                     f"{CONFIRM_ATTEMPTS}); it will be tried again")
            return 1
        # The one outcome this must never do quietly. The box is holding a
        # change nobody confirmed and it has stopped trying to undo it, which
        # is the state the operator has to be told about in the words they
        # will search for.
        dead = self.confirm.give_up()
        # The verdict on the headline and the rest below it, because `err`
        # wraps nothing and the one row measured 98 columns (#252's shape,
        # measured in #276).
        self.err(f"giving up after {n} attempts")
        print_prose("this box is still on the configuration that was never "
                    "confirmed")
        # The path on a row of its own: on the box's own store this row
        # measured 80 columns, one over the console, and a path an operator
        # has to go and read cannot be allowed to wrap (#276).
        print_prose("the configuration it was to go back to is in this file:")
        print(f"    {dead}")
        print_prose("put it back by hand: 'rollback 1' loads the revision "
                    "before that commit")
        return 1

    def say_handed_ssh_host_keys(self):
        """
        Tell whoever just logged in that this box did not make its own ssh
        host keys, when that is true of THIS box, and nothing when it is not.

        WHY THE VTY SAYS IT AT ALL. The claim is about the ssh session it is
        printed into: an operator on a box installed from a medium that
        carried host keys is talking to a key whose private half anybody who
        downloaded that medium holds, so the session can be sat in the middle
        of and the client will not object - the fingerprint it pinned is the
        fingerprint it was handed. The boot self-test makes the same finding,
        and writes it to /dev/console, once, at boot: on a racked appliance
        that is a serial port nobody is watching, and it does not reach the
        journal either, so it could not be looked up afterwards by an operator
        who had been told to go and look.

        WHY IT IS NOT A HEALTH LINE. Nothing is printed when the keys are the
        box's own. A remediated box must stop being warned the moment it is
        remediated, and it does, because the answer is measured per login and
        not remembered from the boot that preceded it.
        """
        handed, fixes = ssh_host_keys_handed_to_this_box()
        if not handed:
            return
        print()
        print("This box's ssh host keys were not generated on it:")
        # The names on rows of their own, folded at the spaces between them: a
        # box with three key types runs past a serial console's 80 columns when
        # they are hung off the end of the sentence, and each name stays whole
        # because console_prose never splits a word.
        print_prose(" ".join(handed))
        print_prose("they came in with the system image, so every box "
                    "installed from that medium answers with this same key "
                    "and the private half of it is in the published "
                    "download. This ssh session can be impersonated and your "
                    "client will not object, because the fingerprint it "
                    "pinned is the fingerprint it was handed.")
        print("regenerate them, in a shell on this box ('start shell'):")
        # From the holder, never spelled here: the command an operator copies
        # off this screen has to be the command the boot report gave them and
        # the command the repair was measured with.
        for c in fixes:
            print("    " + c)
        print()
        print_prose("every client that pinned the old fingerprint will then "
                    "refuse to connect and report that the host key changed. "
                    "That is this being fixed, not an attack, and it is the "
                    "cost of fixing it.")

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
        # Only on a terminal. "prismash -c" is used by scripts and by every
        # lab, and a banner prepended to parsed output breaks them - the
        # version line already had to learn this, which is why the guard was
        # here before the banner was.
        if sys.stdin.isatty():
            print(BANNER)
            print(f"Prisma Routing VTY {prisma_routing_version()}")
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
                print("Type 'install' to install Prisma Routing onto a disk.")
            # The operator this window exists for is the one logging in right
            # now, after losing the session that armed it. Nothing else on
            # this screen would tell them the box is about to undo a change
            # under them, or that typing one word keeps it.
            pending = self.confirm.read()
            if pending:
                when = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                     time.gmtime(pending["deadline"]))
                print()
                print(f"A 'commit confirmed' is waiting: this box rolls the "
                      f"last change back at {when}")
                print("unless you type 'commit' to confirm it.")
            # THE SESSION THIS IS BEING READ IN is the one the warning is
            # about, which is why it is on the banner and not only in the boot
            # report (#248). A box installed from a medium that carried host
            # keys answers with a key every other box from that medium also
            # answers with, and whose private half is in the published
            # download. The boot self-test says so on /dev/console, once, at
            # boot; a racked appliance is reached over ssh and nobody is
            # attached to that console, so until this line the operator being
            # impersonated had no way to find out.
            self.say_handed_ssh_host_keys()
            print()
        while True:
            try:
                line = input(self.prompt())
            except EOFError:
                print()
                return 0
            except KeyboardInterrupt:
                # Ctrl-C discards the line and asks again, as on every router
                # CLI. It used to be handled like Ctrl-D: this returned 0,
                # prismash-login exited 0, and the ssh session closed on the
                # operator (#441).
                print()
                continue
            # And while a command runs, Ctrl-C stops the command and not the
            # VTY. Uncaught, it killed this process with a traceback, and
            # prismash-login answers a non-zero exit by starting a login
            # shell (#441, #442). commit, rollback and load cannot get here:
            # they run with SIGINT ignored, see sigint_ignored().
            try:
                if not self.feed(line):
                    return 0
            except KeyboardInterrupt:
                print()


# not a unit: the program's entry; it builds the Shell and hands the
# arguments to feed_batch() or repl(), and those are tested by name.
#: The login broker this VTY was handed its terminal by (#448), or None when
#: it was started on a terminal of its own. start_shell() asks the host
#: through it instead of spawning a shell here.
ATTACHED = None


def main():
    global ATTACHED
    args = sys.argv[1:]
    if args[:1] == ["--attach"] and len(args) > 1:
        import vtyattach
        ATTACHED = vtyattach.attach(args[1])
        args = args[2:]
    sh = Shell()

    if args and args[0] == "--load":
        sh.load(args[1] if len(args) > 1 else CONFIG_PATH)
        return 1 if sh.failed else 0

    # Not a command an operator types: it is the one thing a unit file needs
    # to be able to run, and it prints nothing at all unless a deadline has
    # actually passed - prisma-routing-confirm.service fires it every few
    # seconds while a window is open.
    if args and args[0] == "--confirm-expired":
        return sh.confirm_expired()

    cmds = []
    while args:
        if args[0] == "-c":
            cmds.append(args[1]); args = args[2:]
        else:
            args = args[1:]

    if cmds:
        # feed_batch and not a loop over feed(): what a `-c` batch is, and
        # what `commit` does about a statement of it that was refused, is
        # written down there.
        return sh.feed_batch(cmds)

    return sh.repl()


if __name__ == "__main__":
    sys.exit(main())
