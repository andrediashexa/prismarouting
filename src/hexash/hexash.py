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

import ipaddress
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time

from config import (Config, Pool, URPF_MODES, UNCHECKED_MARK,
                    _address_family_error)
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
    "bgp": ("BGP sessions, and whether the dataplane has their routes", {
        "summary": ("one line per session, with what reached the FIB", None),
        "neighbor": ("one session in detail", ARG_NEIGHBOUR),
    }),
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
    "description": ("what this interface is for", "<text>"),
    "vlan": ("VLAN tag, on a sub-interface", "<1-4094> [inner <1-4094>]"),
    "vrf": ("the routing table this interface's addresses live in", ARG_NUM),
    "mac-address": ("the MAC this interface answers to", "<xx:xx:xx:xx:xx:xx>"),
    "ipv4": ("IPv4 addressing", {
        "address": ("an address on this interface, may repeat", ARG_PREFIX),
    }),
    "ipv6": ("IPv6 addressing", {
        "address": ("an address on this interface, may repeat", ARG_PREFIX),
        "nd": ("neighbour discovery", {
            "suppress-ra": ("stop advertising this box as a router", None),
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
               "snmp_changed", "mtu_set", "mtu_del", "urpf_set", "urpf_del",
               "desc_set", "desc_del", "admin_down", "admin_up",
               "addr_add", "addr_del", "subif_add", "subif_del", "vrf_set",
               "mac_set", "ra_on", "ra_off")


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
        "clear": ("reset a session, without changing the configuration", {
            "bgp": ("a BGP session", ARG_NEIGHBOUR),
        }),
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
            "route": ("a static route", "<prefix> <next-hop|interface>"),
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
        return (1 if VPP_ERROR.match(first) else 0), out


def mark_unchecked(lines):
    """
    The same configuration with route-map statements this VTY did not verify
    marked as such.

    DISPLAY ONLY, and that is not a caveat - it is the design. The marker must
    never reach `Config.text()`, the saved configuration, a rollback revision
    or the file handed to FRR: a comment appended mid-statement is not
    something FRR is obliged to accept, and a configuration that cannot be
    fed back is not a configuration.

    The reason it exists: most of a route-map still passes straight through.
    Without a marker, an operator reading the running configuration cannot
    tell a statement the box checked from one it merely forwarded, and so
    reads a guarantee into both.
    """
    out, inside = [], False
    for l in lines:
        t = l.strip()
        w = t.split()
        if w and w[0] == "route-map":
            inside = True
            out.append(l)
            continue
        if not l[:1].isspace():
            inside = False
            out.append(l)
            continue
        if not inside or not w:
            out.append(l)
            continue
        node = ROUTE_MAP_BLOCK
        known = True
        for word in w:
            if not isinstance(node, dict):
                break                    # an argument: everything past it is
            entry = node.get(word)       # the argument's business
            if entry is None:
                known = False
                break
            node = entry[1]
        out.append(l if known else f"{l}{UNCHECKED_MARK}")
    return out


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


def undefined_policy_lists(routing):
    """
    Names a route-map matches on that nothing in the configuration defines.

    Same failure as the undefined route-map: the statement commits, the policy
    reads correctly, and the clause matches nothing forever. FRR does not
    refuse it, so this does.
    """
    have_as, have_comm, have_large = set(), set(), set()
    for line in routing:
        w = line.split()
        if len(w) >= 4 and w[:3] == ["bgp", "as-path", "access-list"]:
            have_as.add(w[3])
        elif len(w) >= 3 and w[0] == "bgp" and w[1] == "community-list":
            have_comm.add(w[3] if w[2] in ("standard", "expanded") else w[2])
        elif len(w) >= 3 and w[0] == "bgp" and w[1] == "large-community-list":
            have_large.add(w[3] if w[2] in ("standard", "expanded") else w[2])

    out = []
    for line in routing:
        w = line.split()
        if not w or w[0] != "match" or len(w) < 3:
            continue
        kind, name = w[1], w[2]
        if kind == "as-path" and name not in have_as:
            out.append((line.strip(), f"no AS-path list called '{name}' is "
                        "defined; the clause would match nothing"))
        elif kind == "community" and name not in have_comm:
            out.append((line.strip(), f"no community list called '{name}' is "
                        "defined; the clause would match nothing"))
        elif kind == "large-community" and name not in have_large:
            out.append((line.strip(), f"no large-community list called "
                        f"'{name}' is defined; the clause would match nothing"))
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
                      "other": []}
            maps.setdefault(w[1], []).append(clause)
            continue
        if clause is None:
            continue
        if w[0] == "match":
            clause["match"].append(line.strip())
        elif w[0] == "set":
            clause["set"].append(line.strip())
        elif w[0] in ("call", "on-match", "description"):
            clause["other"].append(line.strip())
        elif w[0] != "exit":
            clause = None          # some other block began

    out = []
    for name, clauses in maps.items():
        catch_all = None
        for c in clauses:
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
                    catch_all = c["header"]
                else:
                    # `deny` with no match denies everything after it too, and
                    # is the other half of the same trap.
                    catch_all = c["header"]
    return out


def bgp_errors(routing):
    """
    What a BGP configuration says that cannot work, as (statement, why).

    Structural only. Whether the neighbour ANSWERS is not knowable from the
    configuration, and this does not pretend otherwise - it catches the shapes
    FRR refuses or silently ignores, which are the ones an operator finds out
    about from a session that never comes up.

    Peer groups are the reason this is not a two-line check: a member inherits
    `remote-as` from its group, so a neighbour with no AS of its own is
    perfectly correct when it is in a group that has one.
    """
    entities = {}          # name -> what has been said about it
    groups = set()         # names declared as peer groups

    def ent(name):
        return entities.setdefault(
            name, {"remote_as": False, "group": None, "stmts": [], "af": False})

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
        if len(w) >= 4 and w[2] == "remote-as":
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
        if e["group"] and e["group"] not in groups:
            out.append((e["stmts"][0],
                        f"peer group '{e['group']}' is not declared; declare "
                        f"it with 'neighbor {e['group']} peer-group'"))
            continue
        if e["remote_as"]:
            continue
        if e["group"]:
            g = entities.get(e["group"], {})
            if g.get("remote_as"):
                continue
            out.append((e["stmts"][0],
                        f"neither {name} nor peer group '{e['group']}' has a "
                        "remote-as, so the session has no AS to open with"))
            continue
        out.append((e["stmts"][0],
                    f"{name} has no 'remote-as': FRR refuses every other "
                    "statement for a neighbour whose AS it has not been told"))
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
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
            f.write("\n".join(lines) + "\n")
            path = f.name
        try:
            mode = "--test" if check_only else "--reload"
            p = subprocess.run(["python3", FRR_RELOAD, mode, "--stdout", path],
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
            os.unlink(path)

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
        cfg.descriptions = self.device_descriptions()
        cfg.addresses = self.device_addresses()
        cfg.vlans = self.device_vlans()
        cfg.vrfs = self.device_vrfs()
        cfg.macs = self.device_macs()
        cfg.admin_down = self.device_admin_down()
        cfg.ra_enabled = self.device_ra_enabled()
        # Read from the box, not from what this VTY last wrote: the same
        # reason the CGNAT half is read back from the dataplane.
        cfg.ssh = self.sys.read_ssh()
        cfg.users = self.sys.read_users()
        cfg.snmp, cfg.snmp_communities, cfg.snmp_users = self.snmp.read()
        cfg.routing = self.frr.running()
        return cfg

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
        return [l for l in lines
                if l.split()[0:1] != ["interface"] and not l.startswith(" ")]

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
                # A sub-interface the configuration CREATES is not one the
                # box is missing. Without this the check refuses every new
                # sub-interface - the commit that would have brought it into
                # existence - which is a check that makes the feature it
                # guards impossible to use.
                have |= set(target.vlans)
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
                            print(f"    the statement would commit and reach "
                                  f"nothing. Enable {need} in "
                                  "/etc/frr/daemons and restart frr")
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
                              f"Enable {need} in /etc/frr/daemons and "
                              "restart frr")
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
            for stmt, why in bgp_errors(target.routing):
                self.err(f"{stmt}: {why}")
                ok = False

            # An AS-path regex FRR would refuse, refused at the prompt
            # instead of mid-commit (#146).
            for stmt in target.routing:
                why = as_path_regex_error(stmt.strip())
                if why:
                    self.err(f"{stmt.strip()}: {why}")
                    ok = False

            # A name a route-map matches on that nothing defines.
            for stmt, why in undefined_policy_lists(target.routing):
                self.err(f"{stmt}: {why}")
                ok = False

            # What each AS-path regex actually matches, against the paths this
            # box has received. A regex that compiles and matches nothing - or
            # everything - is the common mistake, and it cannot be refused:
            # `permit 65001` is legal and also matches 165001 and 650012. An
            # operator reading their own regex back will not see it; a count
            # against real paths is what shows it.
            self.report_as_path_reach(target.routing)

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
            # A pool's public prefix that nothing announces.
            #
            # Configure a pool with `outside 200.150.0.16/28`, get BGP right,
            # and unless something advertises a prefix covering it, every
            # subscriber behind that pool is translated to an address the
            # internet cannot route back to. Traffic leaves and nothing
            # returns. The CGNAT is correct, the BGP is correct, and the
            # service does not work - nothing in the box connected the two.
            #
            # Reported, never refused. Announcing the prefix from another box
            # is a legitimate design, and a check that refuses a working
            # topology gets worked around.
            if target.pools and any(l.lstrip().startswith("router bgp")
                                    for l in target.routing):
                # BGP's form is `network <prefix>`, two words. OSPF's is
                # `network <prefix> area <n>`, so the length test is what
                # tells them apart without parsing the block structure.
                announced = [w[1] for w in (l.split() for l in target.routing)
                             if len(w) == 2 and w[0] == "network"]
                # `redistribute` can advertise a prefix in ways this check
                # cannot see. Saying nothing is right there: a warning that
                # fires on a working box is worse than no warning, because the
                # next real one is ignored too.
                blind = any(l.lstrip().startswith("redistribute")
                            for l in target.routing)
                for pname, pool in sorted(target.pools.items()):
                    for pfx in pool.outside:
                        if any(routes.covers_prefix(a, pfx)
                               for a in announced):
                            continue
                        if blind:
                            continue
                        print(f"warning: pool '{pname}': nothing here "
                              f"announces {pfx}")
                        print("    subscribers behind it would be translated "
                              "to an address the internet cannot route back "
                              "to")
                        print(f"    add 'network {pfx}' under the BGP address "
                              "family, or announce it from another box")

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
        for ifname in d["admin_down"]:
            if not self.set_admin(ifname, False):
                return False

        # Sub-interfaces first: nothing can be put on one that does not exist
        # yet. Removals last, further down, for the mirror-image reason.
        for ifname, tag in d["subif_add"].items():
            if not self.create_subif(ifname, tag):
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
        for ifname in d["subif_del"]:
            if not self.delete_subif(ifname):
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
            if w[2] == "tcp":
                self.dp.run("bpa logging tcp " + " ".join(w[3:]))
            else:
                self.dp.run(f"bpa logging {w[2]}")
        elif w[1] == "session-table":
            # "session-table buckets <n> memory <MB>", in either order.
            got = {}
            for i in range(2, len(w) - 1, 2):
                if w[i] in ("buckets", "memory") and w[i + 1].isdigit():
                    got[w[i]] = int(w[i + 1])
            if len(got) != 2:
                self.err("usage: cgnat session-table buckets <n> memory <MB>")
                return
            r = self.global_api(set_session_table=True,
                                session_buckets=got["buckets"],
                                session_memory_mb=got["memory"])
            if r.state != vppapi.OK:
                # The dataplane refuses this once pools exist, because the
                # tables are allocated with the first pool and a new size
                # would describe a table nobody is using.
                self.err(f"cgnat session-table: {r.reason}")

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

        if running.is_same(self.candidate):
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
            print("applying it as 'commit confirmed 10': it reverts on its own "
                  "unless you enter 'commit' again from a session that works")

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
        # `no interface <name>` deletes a sub-interface, and with it whatever
        # was on it. Only a sub-interface: a physical port is not something a
        # configuration can remove, and accepting the words for one would
        # promise something the commit cannot deliver.
        if line.startswith("no interface "):
            gone = w[2] if len(w) > 2 else ""
            if "." not in gone:
                self.err(f"{gone or '<name>'}: only a sub-interface can be "
                         "removed; a physical port is not created by this "
                         "configuration")
                return True
            self.candidate.vlans.pop(gone, None)
            self.candidate.addresses.pop(gone, None)
            self.candidate.mtus.pop(gone, None)
            self.candidate.urpf.pop(gone, None)
            self.candidate.descriptions.pop(gone, None)
            self.candidate.admin_down.discard(gone)
            self.candidate.interfaces.pop(gone, None)
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

        if (self.frr_ctx and self.frr_ctx[-1].startswith("interface ")
                and w[0] == "mac-address"):
            ifname = self.frr_ctx[-1].split()[1]
            if len(w) < 2 or len(w[1].split(":")) != 6:
                self.err("usage: mac-address <xx:xx:xx:xx:xx:xx>")
            else:
                self.candidate.macs[ifname] = w[1].lower()
            return True
        if (self.frr_ctx and self.frr_ctx[-1].startswith("interface ")
                and w[:3] == ["ipv6", "nd", "suppress-ra"]):
            # The default already. Accepted so an operator can write it to
            # say so, and so a configuration saved from an older box loads.
            self.candidate.ra_enabled.discard(self.frr_ctx[-1].split()[1])
            return True
        if (self.frr_ctx and self.frr_ctx[-1].startswith("interface ")
                and w[:4] == ["no", "ipv6", "nd", "suppress-ra"]):
            self.candidate.ra_enabled.add(self.frr_ctx[-1].split()[1])
            return True

        # `vrf` moves the interface between routing tables. The device's, and
        # not something FRR can be told about after the fact.
        if (self.frr_ctx and self.frr_ctx[-1].startswith("interface ")
                and w[0] == "vrf"):
            ifname = self.frr_ctx[-1].split()[1]
            if len(w) < 2 or not w[1].isdigit():
                self.err("usage: vrf <table>")
            elif int(w[1]):
                self.candidate.vrfs[ifname] = int(w[1])
            else:
                self.candidate.vrfs.pop(ifname, None)
            return True
        if (self.frr_ctx and self.frr_ctx[-1].startswith("interface ")
                and w[:2] == ["no", "vrf"]):
            self.candidate.vrfs.pop(self.frr_ctx[-1].split()[1], None)
            return True

        # `vlan` creates the sub-interface. It is the device's for the same
        # reason as an address, and more so: FRR cannot create one at all.
        if (self.frr_ctx and self.frr_ctx[-1].startswith("interface ")
                and w[0] == "vlan"):
            ifname = self.frr_ctx[-1].split()[1]
            why = None
            if len(w) < 2 or not w[1].isdigit():
                why = "usage: vlan <1-4094> [inner <1-4094>]"
            elif "." not in ifname:
                why = (f"{ifname} is not a sub-interface; a VLAN tag belongs "
                       "to one, named <parent>.<n>")
            elif ifname.rsplit(".", 1)[1] != w[1]:
                # VPP names a sub-interface after its sub_id, so accepting
                # `interface Gi0/1.7` with `vlan 100` would produce a box
                # calling it Gi0/1.100 while the configuration called it
                # Gi0/1.7 - and the running configuration is read from the
                # box. The two names have to be the same name.
                why = (f"{ifname} must carry vlan {ifname.rsplit('.', 1)[1]}: "
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
            self.candidate.vlans[ifname] = tag
            return True

        # Addresses are the device's: they belong to the packet path, and FRR
        # sees them only because linux_cp mirrors them onto the TAP.
        if (self.frr_ctx and self.frr_ctx[-1].startswith("interface ")
                and w[0] in ("ipv4", "ipv6") and w[1:2] == ["address"]):
            ifname = self.frr_ctx[-1].split()[1]
            if len(w) < 3:
                self.err(f"usage: {w[0]} address <prefix>")
            else:
                why = _address_family_error(w[0], w[2])
                if why:
                    self.err(why)
                else:
                    self.candidate.addresses.setdefault(ifname, [])
                    if w[2] not in self.candidate.addresses[ifname]:
                        self.candidate.addresses[ifname].append(w[2])
            return True
        if (self.frr_ctx and self.frr_ctx[-1].startswith("interface ")
                and w[:1] == ["no"] and w[1:2] in (["ipv4"], ["ipv6"])
                and w[2:3] == ["address"]):
            ifname = self.frr_ctx[-1].split()[1]
            if len(w) < 4:
                self.err(f"usage: no {w[1]} address <prefix>")
            elif w[3] in self.candidate.addresses.get(ifname, []):
                self.candidate.addresses[ifname].remove(w[3])
            else:
                self.err(f"{ifname} does not have {w[3]}")
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
        if (self.frr_ctx and self.frr_ctx[-1].startswith("interface ")
                and w[0] == "description"):
            ifname = self.frr_ctx[-1].split()[1]
            if len(w) < 2:
                self.err("usage: description <text>")
            else:
                self.candidate.descriptions[ifname] = line.strip().split(None, 1)[1]
            return True
        if (self.frr_ctx and self.frr_ctx[-1].startswith("interface ")
                and w[:2] == ["no", "description"]):
            self.candidate.descriptions.pop(self.frr_ctx[-1].split()[1], None)
            return True
        if (self.frr_ctx and self.frr_ctx[-1].startswith("interface ")
                and w[0] == "shutdown"):
            self.candidate.admin_down.add(self.frr_ctx[-1].split()[1])
            return True
        if (self.frr_ctx and self.frr_ctx[-1].startswith("interface ")
                and w[:2] == ["no", "shutdown"]):
            self.candidate.admin_down.discard(self.frr_ctx[-1].split()[1])
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
        if line.startswith(("router ", "interface ", "vrf ", "route-map ",
                            "address-family ")) or line.strip() == "rpki":
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
        if _w[:2] == ["show", "bgp"]:
            self.show_bgp(_w[2:])
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
                    # Redacted here too. `show this` inside a BGP block reads
                    # the routing lines straight out of the candidate, so a
                    # neighbour password printed nowhere else was printed here.
                    print(redact([line])[0])
            return

        # At the top of configuration mode, "this" is the whole candidate -
        # which is what `show configuration` already prints, and saying so is
        # better than printing it twice under two names.
        self.print_config(cfg)

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

        if any(a in ("description", "descr") for a in args):
            # They asked for descriptions and got a table without them. Saying
            # nothing would leave somebody looking for a column that is never
            # coming.
            print("  the dataplane carries no per-interface description; "
                  "FRR's view is 'show ip interface'")

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

        # Which prefixes BGP holds, and which of them the dataplane has. Asked
        # once for the whole table rather than per peer: the FIB does not know
        # which session a route came from, and pretending otherwise would put a
        # number on the line that could not be defended.
        bgp_prefixes = self.bgp_prefixes()
        in_fib = set()
        rc_f, fout = self.dp.run("show ip fib")
        if rc_f == 0 and fout.strip():
            in_fib = set(routes.parse_fib(fout).get(0, {}))
        missing = [p for p in bgp_prefixes if p not in in_fib] if in_fib else []

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
        if not in_fib:
            print("  the dataplane is not answering, so what reached the "
                  "forwarding table is unknown")
        elif not bgp_prefixes:
            print("  no BGP prefixes to check against the forwarding table")
        elif missing:
            # The number that is worth the line. A route FRR holds and VPP
            # never got is invisible in every BGP command there is.
            print(f"  {len(missing)} of {len(bgp_prefixes)} BGP prefix(es) "
                  "are NOT in the forwarding table")
            for prefix in sorted(missing)[:5]:
                print(f"    {prefix}")
            if len(missing) > 5:
                print(f"    ... and {len(missing) - 5} more")
            print("    'show route verbose' compares the two tables in full")
        else:
            print(f"  all {len(bgp_prefixes)} BGP prefix(es) are in the "
                  "forwarding table")

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

    def do_clear(self, line):
        """
        Reset a session. Operational, and it changes nothing on disk.

        `clear` is the command an operator reaches for at 2am, and until now
        this VTY did not have it - which meant leaving hexash for vtysh, on a
        box whose whole point is that they do not have to.
        """
        w = line.split()
        if w[:2] != ["clear", "bgp"] or len(w) < 3:
            self.err("usage: clear bgp <neighbour> [soft [in|out]]")
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
        The grammar for the block being typed in, or None when there is none.

        None is not the same as an empty grammar: a block this VTY does not
        model offers `exit` and nothing else, because offering a guess as a
        grammar is worse than offering nothing. `words` is needed because one
        block has two grammars - the words after a neighbour's ADDRESS are a
        different set from the words that start a line.
        """
        block = self.frr_ctx[-1]
        if block.startswith("interface "):
            return INTERFACE_BLOCK
        if block.strip() == "rpki":
            return RPKI_BLOCK
        if block.startswith("route-map "):
            return ROUTE_MAP_BLOCK
        if block.startswith("address-family "):
            base, neigh = BGP_AF_BLOCK, BGP_AF_NEIGHBOUR
        elif block.startswith("router bgp"):
            base, neigh = BGP_BLOCK, BGP_NEIGHBOUR
        else:
            return {"exit": ("leave the block", None)}
        # `neighbor <address> <word>`: the address is an argument, so the tree
        # cannot walk through it. After it, the statements are the neighbour's.
        if len(words) >= 2 and words[0] == "neighbor":
            return {"neighbor": ("a BGP session", {words[1]: (
                f"statements for {words[1]}", neigh)})}
        return base

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
        if self.frr_ctx:
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
