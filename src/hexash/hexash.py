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

import contextlib
import io
import ipaddress
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from config import (Config, Pool, URPF_MODES, UNCHECKED_MARK, UNCHECKED_NOTE,
                    NAT_HIERARCHY, STATIC_BLOCK, FRR_BLOCK_CLOSERS,
                    ROUTING_HEADER,
                    strip_nat, as_hierarchy, group_static,
                    block_paths, frr_block_lines, opens_frr_block,
                    is_static_route,
                    _address_family_error, _is_loopback, _is_lcp_tap,
                    global_key, ordered_globals)
from system import (System, SnmpDaemon, SnmpUser, User,
                    can_anyone_log_in, can_anyone_configure, redact,
                    snmp_redact, validate_snmp, validate_ssh, validate_role,
                    SSH_FIELDS, GROUP_ADMIN, GROUP_CGNAT,
                    ROLE_ADMIN, ROLE_CGNAT)
import routes
import vppapi

readline = None

CONFIG_PATH = os.environ.get("HEXA_CONFIG", "/etc/hexarouting/hexarouting.conf")
ROLLBACK_DIR = os.environ.get("HEXA_ROLLBACK", "/etc/hexarouting/rollback")
ROLLBACK_KEEP = 20
# Where a "commit confirmed" waits for its confirmation.
#
# A file, in the same directory and with the same group, because the deadline
# has to outlive the session that set it - see ConfirmPending (#212).
CONFIRM_PATH = os.environ.get("HEXA_CONFIRM",
                              "/etc/hexarouting/confirm-pending")
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
# directories and 0660 on the files, group hexa-cgnat. It has to be the
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
VPPCTL = os.environ.get("VPPCTL", "vppctl")
VTYSH = os.environ.get("VTYSH", "vtysh")
FRR_RELOAD = os.environ.get("FRR_RELOAD", "/usr/lib/frr/frr-reload.py")

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
ARG_PREFIX_LIST = "<prefix-list>"
ARG_NEIGHBOUR = "<neighbour>"
ARG_AS_PATH_LIST = "<as-path-list>"
ARG_COMMUNITY_LIST = "<community-list>"
ARG_ROUTE_MAP = "<route-map>"
ARG_PEER_GROUP = "<peer-group>"
ARG_AREA = "<area>"
ARG_ISIS_TAG = "<isis-instance>"
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
    "blocks": ("blocks allocated in dynamic mode, and how many a forward pins",
               None),
    "logging": ("IPFIX export state", None),
    "history": ("dated configuration trail", None),
    # The two the dataplane had and this VTY did not reach (#228). Both answer
    # "what does the box ACTUALLY have", which is a different question from
    # what the file says - the distinction #172 was about for worker cores,
    # and the one an operator needs when an FTP transfer hangs or a port
    # forward does not work. Without them the way to that answer was vppctl,
    # which is the tool this VTY exists to keep them out of, and which is
    # every capability at once (#76).
    "alg": ("which ALGs the dataplane has on, per pool", None),
    "forward": ("the port forwards the dataplane holds", "[<pool>]"),
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
    "version": ("HexaRouting, VPP and FRR versions", None),
    "logging": ("system log (FRR)", None),
}

# Offered inside `router static`. FRR has no such block - its static routes are
# top-level `ip route` statements - so this is a hierarchy this VTY provides and
# translates, which is the whole reason it needs a grammar of its own here.
STATIC_BLOCK_GRAMMAR = {
    "route": ("a destination this box is told about, rather than taught",
              "<prefix> <next-hop|interface|blackhole|Null0> [distance]"),
    "no": ("remove", {"route": ("remove a static route", "<prefix> ...")}),
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
                         "IS-IS's maintenance drain", "[on-startup <1-86400>]"),
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
    "maximum-paths": ("how many equal-cost paths to install", "<1-64>"),
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
    "maximum-paths": ("how many equal-cost paths to install", "<1-64>"),
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
    "vlan": ("VLAN tag, on a sub-interface", "<1-4094> [inner <1-4094>]"),
    "vrf": ("the routing table this interface's addresses live in", ARG_NUM),
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
    "mtu": ("MTU, on both the dataplane interface and its TAP", "<576-9216>"),
    "urpf": ("drop packets whose source does not route back this way", {
        "strict": ("the source MUST route back out this interface", None),
        "loose": ("the source MUST have a route somewhere", None),
        "off": ("no check (the default)", None),
    }),
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
INTERFACE_NO_REFUSED = {
    "vlan": ("vlan cannot be removed: a sub-interface is named after its tag, "
             "so one without a tag is not something the dataplane can hold. "
             "Remove the sub-interface itself: no interface <name>"),
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
    "metric": ("the MED sent to the neighbour", "<0-4294967295>"),
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
    "weight": ("this box's own preference, never advertised", "<0-65535>"),
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
    "block-stride": ("blocks between the ones a subscriber holds; 1 puts them "
                     "side by side, 0 lets the pool place them", ARG_NUM),
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
        "esp": ("IPsec ESP passthrough, bound to the peer gateway",
                "<on|off>"),
        "sip": ("SIP over UDP: Via, Contact and the SDP media pair",
                "<on|off>"),
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
        return (f"forward {w[2]} {w[3]} is configured for "
                + ", ".join(f[2] for f in hit)
                + " on different public addresses; name the subscriber: "
                f"no forward {w[2]} {w[3]} to <private-ip> <private-port>")
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
        "udp": ("seconds", ARG_NUM),
        "icmp": ("seconds", ARG_NUM),
        "tcp-transitory": ("seconds", ARG_NUM),
        "tcp-established": ("seconds", ARG_NUM),
    }),
    "block-holddown": ("seconds a block stays reserved once idle", ARG_NUM),
    "logging": ("IPFIX export of block events", {
        "enable": ("enable UDP export", None),
        "disable": ("disable UDP export", None),
        "udp": ("where UDP export goes; required before 'enable'",
                "<collector> src <source> [port <n>]"),
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
#
# The group names themselves live in system.py, beside the code that puts an
# account in them: two spellings of "hexa-admin" would be two things to keep
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
DELTA_ADMIN = ("routing_changed", "ssh_changed", "users_changed",
               "snmp_changed", "mtu_set", "mtu_del", "urpf_set", "urpf_del",
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
        if w[i] == "seq" and w[i + 1].isdigit():
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
        "exit": ("end the session", None),
    },
    "config": {
        # CGNAT sits under `nat`, and static routes under `router`, because
        # that is where an operator looks for them (#193). Both old spellings
        # - `cgnat ...` at the top level and `ip route ...` - are still
        # accepted, and are deliberately NOT offered here: a box in service has
        # a saved configuration and twenty rollback revisions written in them,
        # so they have to work, but offering both teaches the one being left
        # behind.
        "nat": ("address translation", {
            "cgnat": ("carrier-grade NAT", CGNAT_CONFIG),
        }),
        "no": ("remove configuration", {
            "nat": ("remove address translation", {
                "cgnat": ("remove CGNAT configuration", {
                    "pool": ("remove a pool", ARG_POOL),
                    "interface": ("disable CGNAT on an interface",
                                  ARG_INTERFACE)})})}),
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


def _frr_static(rest):
    """`route <prefix> ...` as the `ip route` / `ipv6 route` FRR understands."""
    family = "ipv6" if ":" in rest[0] else "ip"
    return f"{family} route " + " ".join(rest)


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
        entry = node.get(w)
        if entry is None:
            return None, None       # unknown word: FRR territory from here
        _, sub = entry
        if isinstance(sub, str):
            return None, sub
        node = sub
        hint = None
    return node, hint


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
        entry = node.get(word)
        if entry is None:
            return False
        node = entry[1]
    # `node` may still be a dict here, on a statement that stops halfway down
    # the grammar. Every word it HAS was read, which is what this answers; a
    # statement that is only a prefix of one is a different complaint.
    return True


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
        # test_hexash_complete fuzzes truncations of every statement these
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
        if len(w) >= 5 and w[0].isdigit() and w[1] == "osi-input" \
                and w[2:5] == ["unknown", "osi", "protocol"]:
            return int(w[0])
    return 0


def isis_overload_set(database):
    """
    Is THIS box advertising the overload bit, read from the LSP it floods?

    "show isis database" prints ATT/P/OL as the last column, and marks the
    LSPs this box originated with a bare `*` in the second field:

        hexarouting.00-00    *    108   0x00000005  0x0113    1160    0/0/1
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
    """The member(s) of this interface's linux_cp pair that FRR can act on."""
    return interface_aliases(name, pairs) - set(dp_names or ())


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

    def same(name):
        return interface_aliases(name, pairs)

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
        out.append(("warning", stmt,
                    "FRR knows this interface as "
                    + "/".join(sorted(same(ifname) - dp_names))
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
    def by_seq(c):
        return (0, int(c["seq"])) if c["seq"].isdigit() else (1, 0)

    out = []
    for name, clauses in maps.items():
        catch_all = None
        steered = any(c["steers"] for c in clauses)
        for c in sorted(clauses, key=by_seq):
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
        # to say arrives on stdout, which is what this reads and HEXA_FRR_DUMP
        # records.
        #
        # mkdtemp rather than a fixed name: this also runs as root at boot, and
        # a predictable path in a world-writable directory is a symlink
        # somebody else planted for root to write through.
        work = tempfile.mkdtemp(prefix="hexash-frr-")
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
            # Set HEXA_FRR_DUMP to a path to capture exactly what was sent
            # and exactly what frr-reload said. Kept because _summarise()
            # necessarily throws away most of that output, and twice now the
            # line it threw away was the one that named the cause - a stale
            # mgmtd refusal, and an `rpki cache` missing its transport keyword
            # that read like an upstream limitation.
            dump = os.environ.get("HEXA_FRR_DUMP")
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
        return keep

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
        hexa-cgnat and not root-only. It does hold password hashes, which is
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

    HEADER = "# hexarouting commit confirmed"

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
        /etc/hexarouting and gets EACCES here. Raising made hexash exit 1 on
        that login, and hexash-login then handed the account a bash prompt to
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
#: one of them did.
BLOCK_MODES = ("pool", "user", "snmp-user")


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
        self.mode = "operational"      # operational | config | pool | user
        self.pool = None               # pool being edited
        self.user = None               # user being edited
        self.snmp_user = None          # SNMP v3 user being edited
        self.candidate = None          # Config being edited
        self.frr_ctx = []              # FRR block context inside config mode
        self.failed = 0
        # The in-session half of "commit confirmed". It is the fast path and
        # the fallback, never the promise: what makes the promise true is the
        # file ConfirmPending writes (#212).
        self.confirm_deadline = None
        self.confirm_backup = None
        self.confirm_on_box = False

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
        cfg.descriptions = self.device_descriptions()
        cfg.addresses = self.device_addresses()
        cfg.vlans = self.device_vlans()
        cfg.vrfs = self.device_vrfs()
        cfg.macs = self.device_macs()
        cfg.linux_names = self.device_linux_names()
        cfg.loopbacks = self.device_loopbacks()
        cfg.admin_down = self.device_admin_down()
        cfg.ra_enabled = self.device_ra_enabled()
        # Read from the box, not from what this VTY last wrote: the same
        # reason the CGNAT half is read back from the dataplane.
        cfg.ssh = self.sys.read_ssh()
        cfg.users = self.sys.read_users()
        cfg.snmp, cfg.snmp_communities, cfg.snmp_users = self.snmp.read()
        cfg.routing = self.frr.running()
        # The Linux end of every linux_cp pair, out. The dataplane reads above
        # report EVERY interface VPP has, and one of them is the pair's own
        # TAP - so `save` wrote `interface tap4097` into the file that
        # hexarouting.service replays at boot, against a VPP with no pairs
        # yet, and the box refused its own configuration and came up with
        # nothing on it. config.Config.drop_lcp_taps carries the measurement.
        #
        # Which TAPs those are is READ OFF THE BOX rather than guessed from
        # the name, because `tap0` is equally what an operator's own
        # `create tap` produces and dropping that would be #47.
        cfg.drop_lcp_taps(self.device_lcp_taps())
        return cfg

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

    def device_admin_down(self) -> set:
        """
        The interfaces that are administratively down.

        Read from `show interface`, not from sw_interface_dump: the dump
        returns the flags as an enum vat2 renders as "Invalid ENUM", so the
        one field that would answer this is the one that does not survive the
        JSON. Captured from a running dataplane rather than assumed.
        """
        rc, out = self.dp.run("show interface")
        if rc != 0:
            return set()
        # local0 is excluded for the same reason device_mtus() excludes it: it
        # is not an interface anybody configures, it is always down, and
        # reporting it would put "shutdown" in the running configuration of
        # every box on earth - which is also how a test asserting " shutdown"
        # came to pass on a box where nothing had been shut down.
        return {name for name, info in routes.parse_interfaces(out).items()
                if info.get("state") == "down" and name != "local0"}

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
            self.err(f"could not {verb} {ifname}: {r.reason}")
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
            self.err(f"interface {ifname}: "
                     f"{'no shutdown' if up else 'shutdown'}: {r.reason}")
            return False
        # The Linux side of the pair, by every name it answers to.
        rc, lcp_out = self.dp.run("show lcp")
        if rc == 0:
            for name in routes.lcp_pairs(lcp_out).get(ifname, []):
                if name == ifname:
                    continue
                subprocess.run(["ip", "link", "set", "dev", name,
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
            self.err(f"interface {ifname}: could not set {mac}: {r.reason}")
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
            self.err(f"interface {ifname}: router advertisements: {r.reason}")
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
            self.err(f"interface {ifname}: could not move it to vrf {table}: "
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
            self.err("IS-IS is configured but its frames cannot be punted to "
                     f"the kernel: {r.reason}")
            print("    the adjacency would never form, and the only evidence "
                  "would be a dataplane error counter")
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
            self.err(f"could not create {ifname}: {r.reason}")
            return False
        return True

    def delete_loopback(self, ifname):
        """Remove a loopback. Already gone is the outcome asked for."""
        idx = self.api.interface_index(ifname)
        if idx is None:
            return True
        r = self.api_call("delete_loopback", {"sw_if_index": idx})
        if r.state != vppapi.OK:
            self.err(f"could not delete {ifname}: {r.reason}")
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
            self.err(f"could not {verb} {ifname} with {host_name}: {r.reason}")
            return False
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
                print(f"warning: {ifname} matches outer {tag['outer']} inner "
                      f"{tag['inner']} AND anything carrying more tags than "
                      "that; VPP's ethernet input reads three or more tags as "
                      "two, so a QinQ sub-interface on this box cannot be "
                      "made to refuse them (#136)")
        else:
            r = self.api_call("create_vlan_subif",
                              {"sw_if_index": idx, "vlan_id": tag["outer"]})
        if r.state != vppapi.OK:
            self.err(f"could not create {ifname}: {r.reason}")
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
            self.err(f"could not delete {ifname}: {r.reason}")
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
            self.err(f"interface {ifname}: could not {verb} {prefix}: "
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
            self.err(f"interface {ifname}: description: {r.reason}")
            return False
        rc, lcp_out = self.dp.run("show lcp")
        if rc == 0:
            for name in routes.lcp_pairs(lcp_out).get(ifname, []):
                if name == ifname:
                    continue
                subprocess.run(["ip", "link", "set", "dev", name,
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
            self.err(f"interface {ifname}: urpf {mode}: {r.reason}")
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
            # The same mistake through the other door. Removing the last
            # administrator's role leaves a box that answers and forwards and
            # that nobody can configure again: a cgnat operator cannot grant a
            # role, so the way back is a root shell the operator may not have.
            # Guarded the same way - refused only when THIS CHANGE is what
            # causes it - so a box that already has no reachable administrator
            # can still be worked on.
            if (not can_anyone_configure(target.ssh, target.users)
                    and can_anyone_configure(running.ssh, running.users)):
                self.err("this change leaves nobody able to configure the "
                         "box: afterwards no account that can log in over "
                         f"SSH holds the {ROLE_ADMIN} role")
                print(f"    give another account 'role {ROLE_ADMIN}' first, "
                      "or allow root to log in over SSH")
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
                said = ("cgnat logging enable: UDP export has no collector, "
                        "so nothing would leave this box")
                advice = ("    add 'nat cgnat logging udp <collector> src "
                          "<source>' beside it, or export over TCP with "
                          "'nat cgnat logging tcp <collector>'")
                if udp_export_without_collector(running.globals):
                    print(f"warning: {said}")
                    print(advice)
                else:
                    self.err(said)
                    print(advice)
                    print("    the dataplane refuses the statement, and it "
                          "would refuse it half way through this commit")
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
                named = set(target.interfaces)
                named |= set(target.mtus)
                named |= set(target.urpf)
                named |= set(target.addresses)
                named |= set(target.descriptions)
                named |= set(target.admin_down)
                # And a sub-interface whose PARENT is not on the box is a
                # different error, worth its own sentence: the tag is fine and
                # the port it was meant to sit on is not there.
                for sub in sorted(target.vlans):
                    parent = sub.rsplit(".", 1)[0]
                    if parent not in have:
                        self.err(f"interface {sub}: its parent {parent} is "
                                 "not on this box")
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
                            self.err(f"{line.strip()}: this box has no "
                                     f"interface {src}")
                            print("    the session would be sourced from "
                                  "nothing and never come up")
                            ok = False
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
                            print("    the statement would commit and reach "
                                  "nothing. " + enable_daemon_advice(need))
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
                        print("    they would commit and store nothing. "
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
                        print(f"    first one: {as_hierarchy(statics[0])}")
                        print("    they would commit and forward nothing. "
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
                    self.err(f"{stmt.strip()}: {why}")
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
                    self.err(f"{stmt}: {why}")
                    ok = False
                else:
                    print(f"warning: {stmt}: {why}")

            # An AS-path regex FRR would refuse, refused at the prompt
            # instead of mid-commit (#146).
            for stmt in target.routing:
                why = as_path_regex_error(stmt.strip())
                if why:
                    self.err(f"{stmt.strip()}: {why}")
                    ok = False

            # A name a route-map matches on that nothing defines. Said and not
            # refused: FRR accepts the clause and binds it when the list
            # arrives, so refusing it blocked building a policy map-first
            # (#208).
            for stmt, why in undefined_policy_lists(target.routing):
                print(f"warning: {stmt}: {why}")

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
                    self.err(f"{stmt}: {why}")
                    ok = False
                else:
                    print(f"warning: {stmt}: {why}")

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
                    self.err(f"{stmt}: {why}")
                    ok = False
                else:
                    print(f"warning: {stmt}: {why}")

            # Origin validation that is not validating anything (#147).
            for stmt, why in rpki_findings(target.routing):
                print(f"warning: {stmt}: {why}")

            # Route-maps: one refusal and two things worth saying (#144). The
            # warnings do NOT fail the check - a permit with no match is a
            # real idiom - but they are printed, because afterwards nobody can
            # tell a finished policy from an unfinished one.
            for sev, stmt, why in route_map_findings(target.routing):
                if sev == "error":
                    self.err(f"{stmt}: {why}")
                    ok = False
                else:
                    print(f"warning: {stmt}: {why}")

            if d["routing_changed"] and target.routing:
                rc_fib, fib_text = self.dp.run("show ip fib")
                if rc_fib == 0 and fib_text.strip():
                    fibs = routes.parse_fib(fib_text)
                    table = fibs.get(0, {})
                    for stmt, nexthop in routes.unreachable_next_hops(
                            target.routing, table):
                        self.err(f"{as_hierarchy(stmt)}: {nexthop} is on "
                                 "no connected network")
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
                        ok = False
                elif self.real_routing(target.routing):
                    # Routing the operator actually wrote, and nothing to
                    # check it with. Refused, and named: blaming the
                    # configuration for a daemon that is not running sends
                    # somebody to debug the wrong thing.
                    self.err("routing is configured and FRR is not answering; "
                             "check 'systemctl status frr'")
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
        # file is the thing hexarouting.service does at every boot:
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
                self.err(f"interface {ifname}: mtu {mtu}: {r.reason}")
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

        for ifname in d["admin_up"]:
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
            print(f"note: mtu removed from {ifname}; the driver default "
                  "applies after the interface is recreated")

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
                self.err("routing is configured and FRR is not answering; "
                         "check 'systemctl status frr'")
                return False
            # Otherwise there is nothing here for FRR: interface stanzas whose
            # contents this VTY applies to the dataplane itself.
        return True

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
            for host in names:
                if host == ifname:
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
            t = dict(self.ZERO_TIMEOUTS)
            t[field] = int(w[3]) if w[3].isdigit() else 0
            r = self.global_api(timeouts=t)
            if r.state != vppapi.OK:
                self.err(f"cgnat timeout {w[2]} {w[3]}: {r.reason}")
        elif w[1] == "block-holddown" and len(w) >= 3:
            r = self.global_api(set_block_holddown=True,
                                block_holddown=int(w[2]) if w[2].isdigit()
                                else 0)
            if r.state != vppapi.OK:
                self.err(f"cgnat block-holddown {w[2]}: {r.reason}")
        elif w[1] == "logging" and len(w) >= 3:
            self.apply_logging(w)
        elif w[1] == "session-table":
            # "session-table buckets <n> memory <MB>", in either order.
            got = {}
            for i in range(2, len(w) - 1, 2):
                if w[i] in ("buckets", "memory") and w[i + 1].isdigit():
                    got[w[i]] = int(w[i + 1])
            if len(got) != 2:
                self.err("usage: nat cgnat session-table buckets <n> "
                         "memory <MB>")
                return
            r = self.global_api(set_session_table=True,
                                session_buckets=got["buckets"],
                                session_memory_mb=got["memory"])
            if r.state != vppapi.OK:
                # The dataplane refuses this once pools exist, because the
                # tables are allocated with the first pool and a new size
                # would describe a table nobody is using.
                self.err(f"cgnat session-table: {r.reason}")

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
        elif w[2] == "tcp" and len(w) >= 4:
            args["set_tcp"] = True
            if w[3] != "disable":
                args["tcp_enable"] = True
                args["collector"] = w[3]
                if len(w) >= 6 and w[4] == "port":
                    if not w[5].isdigit() or int(w[5]) > 65535:
                        self.err(f"cgnat logging tcp: port {w[5]} is not one")
                        return
                    # Zero, not 4739, when no port was typed: the default
                    # belongs to the dataplane, which is where the running
                    # configuration is read back from. A default invented
                    # here would be a second opinion about it.
                    args["port"] = int(w[5])
        else:
            self.err("usage: nat cgnat logging enable|disable | "
                     "nat cgnat logging udp <collector> src <source> "
                     "[port <n>] | nat cgnat logging udp disable | "
                     "nat cgnat logging tcp <collector> [port <n>] | "
                     "nat cgnat logging tcp disable")
            return
        r = self.api_call("bpa_logging_set", args)
        if r.state != vppapi.OK:
            self.err(f"cgnat {' '.join(w[1:])}: {r.reason}")

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
        r = self.api.dump("ipfix_exporter_dump", {})
        if r.state != vppapi.OK:
            self.err(f"cgnat {' '.join(w[1:])}: cannot read the IPFIX "
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
                self.err("usage: nat cgnat logging udp <collector> src "
                         "<source> [port <n>]")
                return
            src = w[5]
            if len(w) >= 8 and w[6] == "port":
                if not w[7].isdigit() or int(w[7]) > 65535:
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
            self.err(f"cgnat {' '.join(w[1:])}: {rep.reason}")

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
                        print(f"  {unread} routing statement(s) have no "
                              "grammar in this VTY and were not read at all")
                        print("    'show configuration' marks them; they go "
                              "to FRR exactly as typed")
                    print("  routing statements are checked for structure "
                          "only; FRR validates them at commit, and can still "
                          "refuse one")
                else:
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
                print(f"    the box still rolls back at {when}; "
                      "'rollback 0' and commit again, or remove that file")

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
                    print("    " + line[len("remap:"):].strip())
                print("    if that is what you mean, type: commit remap")
                return
            self.remap = []

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
            print("applying it as 'commit confirmed 10' rather than as a "
                  "plain commit")
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
            self.err("the change is applied, but no rollback revision could "
                     f"be recorded: {e}")
            print("    'rollback' will not be able to undo this commit")
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
            # WARNED, NOT REFUSED, and this is the whole posture of the check.
            #
            # A refusal here would be right in principle: a statement FRR threw
            # away is a commit that did not happen, and `commit complete` over
            # it is the one sentence an operator builds every later diagnosis
            # on. It is not right YET, because the list of statements FRR does
            # not read back verbatim is demonstrably incomplete. Five encounters
            # with real configuration produced four false positives:
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
            # and shipping a refusal built on a list that four labs in a row
            # have extended would repeat it on a box nobody can run a lab on.
            #
            # THE PROMOTION CONDITION IS MEASURABLE, not a feeling: this
            # becomes `self.err` and `ok = False` when the full lab suite has
            # run clean with it refusing. Until then the operator gets the
            # sentence and keeps their commit, which is the side that costs
            # them nothing if the list is wrong.
            print(f"warning: FRR accepted {len(dropped)} statement(s) of this "
                  "commit and did not keep them")
            for stmt, instead in dropped[:5]:
                print("    " + as_hierarchy(stmt))
                if instead:
                    print(f"      FRR writes '{instead}' for this, and that "
                          "is not there either")
            if len(dropped) > 5:
                print(f"    ... and {len(dropped) - 5} more")
            print("    the configuration this box holds and the one FRR is "
                  "running are not the same; 'show configuration' is this "
                  "box's, 'start shell' then 'vtysh -c \"show "
                  "running-config\"' is FRR's")
        elif stalled:
            # NOT "commit complete". The CGNAT half and the box's own
            # configuration are applied - this is not a rollback - but the
            # routing half reached nothing, and saying "complete" over that is
            # the one sentence an operator builds every later diagnosis on.
            self.err("the routing half of this commit reached FRR and changed "
                     "nothing: all " + str(len(stalled)) +
                     " new statement(s) are still outstanding")
            for line in stalled[:5]:
                print("    " + as_hierarchy(line))
            if len(stalled) > 5:
                print(f"    ... and {len(stalled) - 5} more")
            print("    the usual cause is a daemon that is not running; "
                  "'show version' says which are")
            print("    if these statements are all at FRR's own default "
                  "values, FRR does not store them and this is expected")
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
            print(f"rollback at {when}, in {minutes} minute(s), unless you "
                  "type 'commit' to confirm")
            print("    the revert is armed on the box, not in this session: "
                  "logging out, losing this")
            print("    session or rebooting does not cancel it, and "
                  "confirming does")
        else:
            # A weaker promise, stated. The revert still happens for an
            # operator who stays and waits, and that is all it does - so this
            # says so rather than repeating the sentence above, which would be
            # the defect #212 is about with a different cause.
            # Also a warning rather than an error, and for the same reason
            # as `save` above: the commit is applied. What failed is the
            # promise about undoing it, which is what the three lines below
            # are for.
            print(f"warning: the revert could NOT be armed on the box: {why}")
            print(f"    it will run in {minutes} minute(s) IF THIS SESSION "
                  "LASTS THAT LONG, and not otherwise")
            print("    which is not the case it exists for: keep a second "
                  "session open, and use")
            print("    'rollback 1' there if this one stops answering")

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
            self.err("the configuration to roll back to has lines this VTY "
                     "does not understand:")
            for lineno, text, unknown_why in target.unknown:
                print(f"    line {lineno}: {text}    ({unknown_why})")
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
        print(why)
        if not self.apply(target):
            return False
        try:
            self.confirm.clear()
        except OSError as e:
            # The box IS back on the old configuration - that half succeeded.
            # What is left is a record saying a revert is still due, which the
            # unit will act on again, so it is said rather than swallowed.
            self.err(f"rolled back, but {self.confirm.path} could not be "
                     f"removed: {e.strerror}")
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
        if not self.revert_confirm(pending,
                                   "\ncommit was not confirmed: rolling back"):
            # Said once, and then this session stops trying. Every command the
            # operator types would otherwise reprint the same failure, and the
            # durable copy on the box is still armed - the unit that watches
            # it keeps trying and reports when it gives up.
            self.err("the box is still on the unconfirmed configuration")
            self.confirm_deadline = None
            self.confirm_backup = None
        self.candidate = self.running_config()

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
            self.err(f"revision {n} cannot be read: {e}")
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
            # Not while the dataplane is silent, and the reason is that BOTH
            # ways of proceeding are wrong.
            #
            # An installed box draws a login prompt while hexarouting.service
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
                self.err("the dataplane is not answering, so there is nothing "
                         "to build a candidate from")
                print("    a candidate built now would be missing the CGNAT "
                      "half, and committing it would remove every pool")
                print("    the box is probably still starting: 'show version' "
                      "says which daemons are up, and this usually clears "
                      "within a minute of boot")
                return True
            self.candidate = self.running_config()
            self.mode = "config"
            print("entering configuration mode")
        elif w[0] in ("exit", "quit", "logout"):
            return False
        elif w[0] == "clear":
            self.do_clear(line)
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
        spelling, and hexarouting.service replays that file at every boot, on a
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
        if line.strip() == NAT_HIERARCHY and not self.frr_ctx:
            # `nat` on its own. Refused rather than entered, because `cgnat`
            # was never a block either - what is entered is `cgnat pool NAME` -
            # and inventing one level of block here would make `exit` mean
            # something different depending on which word opened it.
            self.err("nat: a hierarchy, not a block - "
                     "'nat cgnat pool <name>' or 'nat cgnat interface ...'")
            return ""
        line = strip_nat(line) if not self.frr_ctx else line
        w = line.split()
        if not w:
            return ""

        if line.strip() == STATIC_BLOCK:
            self.frr_ctx.append(STATIC_BLOCK)
            return ""
        if not (self.frr_ctx and self.frr_ctx[-1] == STATIC_BLOCK):
            return line
        # Inside the block. The family comes from the prefix, because a prefix
        # with a colon in it is IPv6 and nothing else; asking for the family as
        # well would be asking the operator to contradict themselves.
        if w[:1] == ["route"] and len(w) > 2:
            return _frr_static(w[1:])
        if w[:2] == ["no", "route"] and len(w) > 3:
            return "no " + _frr_static(w[2:])
        if w[:1] == ["ip"] or w[:1] == ["ipv6"]:
            return line          # FRR's own spelling, typed inside the block
        if w[0] in ("exit", "quit", "end", "show", "commit", "rollback"):
            return line
        self.err(f"{w[0]}: not a statement of 'router static' - "
                 "'route <prefix> <next-hop>', or 'exit'")
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
                self.err("usage: nat cgnat interface <interface> "
                         "<inside|outside>")
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
                self.err(f"{gone or '<name>'}: only a sub-interface or a "
                         "loopback can be removed; a physical port is not "
                         "created by this configuration")
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
            if len(w) < 2 or not w[1].isdigit():
                self.err("usage: mtu <576-9216>")
            elif not 576 <= int(w[1]) <= 9216:
                self.err(f"mtu {w[1]} out of range (576-9216)")
            else:
                self.candidate.mtus[iface] = int(w[1])
            return True
        if iface and neg and dp == "mtu":
            self.candidate.mtus.pop(iface, None)
            return True

        if iface and not neg and dp == "mac-address":
            if len(w) < 2 or len(w[1].split(":")) != 6:
                self.err("usage: mac-address <xx:xx:xx:xx:xx:xx>")
            else:
                self.candidate.macs[iface] = w[1].lower()
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
            elif len(w[1]) > 15 or "/" in w[1]:
                # Linux refuses a name over 15 characters, and a '/' cannot be
                # in one at all - which matters here because VPP's own names
                # are full of them, and copying one across is the obvious
                # mistake to make.
                self.err(f"linux-name: '{w[1]}' cannot be a Linux interface "
                         "name (15 characters, no '/')")
            else:
                self.candidate.linux_names[iface] = w[1]
            return True
        if iface and neg and dp == "linux-name":
            self.candidate.linux_names.pop(iface, None)
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
            if len(w) < 2 or not w[1].isdigit():
                self.err("usage: vrf <table>")
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
            if len(w) < 2 or not w[1].isdigit():
                why = "usage: vlan <1-4094> [inner <1-4094>]"
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
            if len(w) >= 4 and w[2] == "inner" and w[3].isdigit():
                tag["inner"] = int(w[3])
            elif len(w) != 2:
                self.err("usage: vlan <1-4094> [inner <1-4094>]")
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
                self.candidate.descriptions[iface] = line.strip().split(None, 1)[1]
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
            self.err(INTERFACE_NO_REFUSED.get(
                dp, f"no {dp}: this VTY has no way to remove it, and it is "
                    f"not FRR's to remove. Set the value you want with "
                    f"'{dp} ...', or remove the interface with "
                    f"'no interface {iface}'"))
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
        if w[0] == "no" and len(w) > 1:
            negated = " ".join(w[1:])
            for i, existing in enumerate(self.candidate.routing):
                if existing.strip() == negated:
                    del self.candidate.routing[i]
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
        elif w[0] == "role" and len(w) > 1:
            why = validate_role(u.name, w[1])
            if why:
                self.err(why)
            else:
                u.role = w[1]
        elif w[:2] == ["no", "ssh-key"] and len(w) > 2:
            key = " ".join(w[2:])
            u.keys = [k for k in u.keys if k != key and not k.startswith(key)]
        elif w[:2] == ["no", "role"]:
            why = validate_role(u.name, "")
            if why:
                self.err(why)
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
            elif w[0] == "block-stride":
                if not 0 <= int(w[1]) <= 65535:
                    raise ValueError("block-stride must be 0-65535")
                p.block_stride = int(w[1])
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
                    if why:
                        self.err(why)
                elif w[1] in CGNAT_POOL:
                    # In the grammar, and cannot be absent: see the rule above
                    # POOL_NEGATE. Saying what to type instead is the point -
                    # "no" alone leaves the operator with a refusal and no
                    # next move.
                    hint = CGNAT_POOL[w[1]][1]
                    if isinstance(hint, dict):
                        hint = "|".join(hint)
                    self.err(f"{w[1]} cannot be removed: every pool has one, "
                             f"so there is no state without it. Set the value "
                             f"you want instead: {w[1]} {hint or ''}".rstrip())
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
            "show cgnat drops": "show bpa drops",
            "show cgnat alg": "show bpa alg",
        }
        if line in m:
            self.vpp(m[line])
            return
        # The ones that carry an argument. Matched by prefix so the argument
        # travels through untouched; hexash is not the place to re-validate an
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
            snmp_redact(redact(cfg.text().splitlines())))))

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
                redact(frr_block_lines(cfg.routing, self.frr_ctx)),
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
        if total is not None and total != len(ifaces):
            print(f"\n  {len(ifaces)} of {total} interface(s) shown")
        else:
            print(f"\n  {len(ifaces)} interface(s) on the dataplane")
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
        described = sum(1 for n in ifaces if descs.get(n))
        print(f"\n  {described} of {len(ifaces)} interface(s) described")
        if described < len(ifaces):
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

        print(f"  sampling every {interval:g}s, {count} time(s) - "
              f"{interval * count:g}s in total; Ctrl-C stops early")
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
            self.err("stopped before two samples, so no rate could be "
                     "measured")
            print("    a rate is a difference between two counters; one "
                  "reading is a total since boot")
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
            print("  the dataplane reports no link speed for these "
                  "interfaces, so there is no line rate to be a share of")
        else:
            print("  nothing passed any interface during the sample")
        for name in sorted(n for n in drops if n in series):
            print(f"  {name} dropped {drops[name]} packet(s) while this was "
                  "sampling")
        if reset:
            print("  a counter went backwards - something cleared the "
                  "interfaces mid-sample; that step reads as zero")

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

        # Straight from the dataplane, uninterpreted. A counter this VTY
        # summarised would be a counter an operator could not compare with
        # what `vppctl` says, and during a fault they will look at both.
        rc, one = self.dp.run(f"show interface {name}")
        if rc == 0:
            rows = [l.rstrip() for l in one.splitlines()[1:] if l.strip()]
            if len(rows) > 1:
                print("    counters")
                for line in rows[1:]:
                    print(f"      {line.strip()}")
            else:
                print("    counters    all zero")

    #: Where the tuner writes what it decided, and where it writes it down.
    STARTUP_CONF = os.environ.get("HEXA_STARTUP_CONF", "/etc/vpp/startup.conf")
    TUNE_ENV = os.environ.get("HEXA_TUNE_ENV", "/etc/hexarouting/tune.env")

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
            return ["DPDK is off until hugepages exist, so no NIC can be "
                    "claimed yet.",
                    "This is the first-boot profile. Reboot once the tuning "
                    "has been applied and the pages reserved."]
        if "no-pci" in conf:
            named = ""
            try:
                with open(self.TUNE_ENV) as f:
                    for line in f:
                        if line.startswith("HEXA_DATAPLANE_IF="):
                            named = line.split("=", 1)[1].strip().strip('"')
            except OSError:
                pass
            if not named:
                return ["no interface was named, so VPP was given no NIC.",
                        f"Set HEXA_DATAPLANE_IF in {self.TUNE_ENV} to the "
                        "interface carrying subscriber traffic,",
                        "then restart hexarouting-tune and vpp."]
            return [f"'{named}' was named and VPP was given no NIC.",
                    "Either it does not exist on this box, or it could not be "
                    "resolved to a PCI address,",
                    "or it carries the default route - which is refused on "
                    "purpose, so a remote box is not cut off."]
        if "dev 0000:" in conf:
            return ["a NIC is named in the startup configuration and the "
                    "dataplane did not bring it up.",
                    "'show log' and the VPP journal say why; the usual cause "
                    "is the NIC still bound to a kernel driver."]
        return []

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
            total = len(ifaces)
            rest = [a for a in args[1:] if a]
            if rest:
                ifaces = {n: e for n, e in ifaces.items()
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
            ifaces = {n: e for n, e in ifaces.items()
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

        # local0 is VPP's own loopback and configures nothing. It IS listed -
        # hiding an interface the dataplane reports would be this command
        # deciding what the operator may see - but it is not offered for
        # completion, and the difference is worth stating rather than leaving
        # as an inconsistency somebody has to work out.
        print(f"\n  {len(ifaces)} interface(s) on the dataplane")


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
                and args[2] == "received-routes" \
                and not self.soft_reconfiguration_on(args[1]):
            self.err(f"neighbour {args[1]} does not keep what it received, "
                     "so this would print an empty table either way")
            print("    add 'neighbor " + args[1] + " soft-reconfiguration "
                  "inbound' under the address family; it costs memory")
            print("    'show bgp neighbor " + args[1] + " routes' shows what "
                  "survived inbound policy, which the box does have")
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
            print("        validation is running on data the caches already "
                  "sent, which expires; after that every route is notfound")

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
            print("  the dataplane is not answering, so what reached the "
                  "forwarding table is unknown")
        elif not prefixes:
            print(f"  no {what} prefixes to check against the forwarding "
                  "table")
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
        the one an operator can type at this VTY. The same shape rule
        ospf_mtu_mismatch uses, for the same reason.
        """
        for name in pairs.get(host, []):
            if name == host or (name.startswith("tap") and name[3:].isdigit()):
                continue
            if name in mtus:
                return name
        return None

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
            print("    an adjacency that stops here is the far end running a "
                  "smaller one, and neither side logs a word about it")
            print("    'show ospf interface' has both of this box's MTUs; "
                  "'mtu <n>' sets them together, and 'ip ospf mtu-ignore' is "
                  "the escape hatch")

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
            print("  the dataplane is not answering, so whether it is punting "
                  "IS-IS to the kernel is unknown")
            return
        if punting:
            if silent_circuits:
                print("  the dataplane IS punting IS-IS to the kernel "
                      f"(NLPID 0x{self.OSI_ISIS:02x}), so the hellos are "
                      "reaching isisd; the far end is not answering them")
            return
        print(f"  the dataplane is NOT punting IS-IS to the kernel (NLPID "
              f"0x{self.OSI_ISIS:02x}), so no hello ever reaches isisd")
        if drops:
            # The evidence, and the reason this is not merely "nothing
            # happened": the frames ARRIVED and were dropped for want of a
            # registered receiver. Without it the same sentence would be
            # printed on a box whose neighbour is switched off.
            print(f"    {drops} frame(s) counted in osi-input as an unknown "
                  "OSI protocol, which is the hellos arriving and being "
                  "dropped")
        else:
            print("    and nothing has been counted in osi-input either, so "
                  "the frames are not even arriving")
        print("    committing an IS-IS instance through this VTY turns the "
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
        print("  this box has the overload bit set: its neighbours will not "
              "compute transit paths through it")
        print("    traffic to anything behind this box drains away, which is "
              "what 'set-overload-bit' is for and what it looks like when it "
              "was left on")

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
        this VTY did not have it - which meant leaving hexash for vtysh, on a
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
            if not self.soft_reconfiguration_on(w[2]):
                self.err(f"neighbour {w[2]} does not keep what it received, "
                         "so there is nothing to re-apply")
                print("    add 'neighbor " + w[2] + " soft-reconfiguration "
                      "inbound' under the address family, which costs memory,")
                print("    or use 'clear bgp " + w[2] + "' to reset the "
                      "session and take the routes again")
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
        """Is this neighbour keeping what it received, so policy can re-run?"""
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
            self.err(f"cannot write {CONFIG_PATH}: {e}")
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
            print(f"    this box rolls the running configuration back at "
                  f"{when}, and does not")
            print("    touch the file you just wrote - so a reboot after "
                  "that would bring the")
            print("    unconfirmed change back. Type 'commit' to confirm, "
                  "then 'save' again")

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
            print("note: no BGP paths received yet, so AS-path lists cannot "
                  "be checked against anything")
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
        node, hint = tree_walk(self.mode, words)

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
        if target.unknown:
            self.err(f"{path}: refusing to load, these lines are not "
                     "understood:")
            for lineno, text, why in target.unknown:
                print(f"    line {lineno}: {text}    ({why})")
            return

        # A file with no statements is nothing to apply, and MUST NOT be read
        # as a configuration that removes what is there. The installer ships
        # exactly such a file, so this path runs on the first boot of every
        # new box: the box has SSH access the installer set up, the file does
        # not mention SSH, and the lockout check correctly saw a change that
        # would take that access away. hexarouting.service failed, and the
        # first thing an operator saw on a new appliance was a failed unit
        # (#121). Returning here rather than weakening the lockout check: the
        # check is right, the input was not a change.
        if target.is_empty():
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

    def confirm_expired(self):
        """
        Roll back a "commit confirmed" whose deadline has passed.

        This is what hexarouting-confirm.service runs, as root, every few
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
        self.err(f"giving up after {n} attempts: this box is still on the "
                 "configuration that was never confirmed")
        print(f"    the configuration it was to go back to is in {dead}")
        print("    put it back by hand: 'rollback 1' loads the revision "
              "before that commit")
        return 1

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

    # Not a command an operator types: it is the one thing a unit file needs
    # to be able to run, and it prints nothing at all unless a deadline has
    # actually passed - hexarouting-confirm.service fires it every few
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
        for c in cmds:
            sh.feed(c)
        return 1 if sh.failed else 0

    return sh.repl()


if __name__ == "__main__":
    sys.exit(main())
