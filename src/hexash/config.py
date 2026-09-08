#!/usr/bin/env python3
"""
Device configuration model: parse, serialise and diff.

The whole device configuration is one text document with two halves: the CGNAT
statements this project owns, and the routing statements FRR owns. Keeping
them in one document is what makes `commit` and `rollback` cover both - a
rollback that restored CGNAT and left routing untouched would leave the box in
a state that never existed.
"""

from __future__ import annotations

import difflib

from system import (SSH_FIELDS, SSH_DEFAULTS, User, validate_ssh,
                    SNMP_FIELDS, SNMP_DEFAULTS, SnmpUser, validate_snmp)

CGNAT_PREFIXES = ("cgnat ", "no cgnat ")

# Unicast RPF modes, as VPP names them. "off" is accepted in configuration text
# so that an operator can write the absence explicitly, but it is stored as the
# absence of an entry: a configuration that lists every default would diff
# against every device that changes one.
URPF_MODES = ("strict", "loose", "off")
ROUTING_HEADER = "! routing"
CGNAT_HEADER = "! cgnat"


class Pool:
    """One CGNAT pool. Order of prefixes is preserved for a stable diff."""

    FIELDS = ("block-size", "ports", "policy", "mode")

    # Order matters: lines() has to emit these in the SAME order as the
    # device's own "show bpa config", or the running configuration and a
    # candidate built from it differ on every commit for no reason.
    TIMEOUTS = ("udp", "icmp", "tcp-syn", "gre", "tcp-transitory",
                "tcp-established", "port-holddown", "alg-expect")
    FILTERS = ("endpoint-independent", "address-dependent")

    # Mirrors BPA_MSS_MIN and BPA_MSS_MAX in bpa_mss.h. The device refuses
    # outside this range too; refusing here as well is what makes the reason
    # arrive at the prompt rather than a commit later.
    MSS_MIN, MSS_MAX = 536, 9000

    @staticmethod
    def check_mss(v: str):
        """None if `v` is a legal clamp, otherwise why it is not."""
        if v == "auto":
            return None
        if not v.isdigit():
            return f"tcp-mss '{v}': expected a size in bytes, or auto"
        if not Pool.MSS_MIN <= int(v) <= Pool.MSS_MAX:
            return (f"tcp-mss {v}: expected {Pool.MSS_MIN}-{Pool.MSS_MAX}. "
                    "Below the MSS every IPv4 host must accept (RFC 1122) the "
                    "clamp is legal and useless; above a jumbo frame it "
                    "describes no path.")
        return None

    # Mirrors foreach_bpa_alg in bpa_pool.h, in the same order and with the
    # same bits. A pool a VTY cannot express is a pool an operator cannot
    # configure, so this list and that macro have to be kept in step; the lab
    # asserts a round trip through the dataplane, which is what catches it.
    ALG_BITS = {"ftp": 1 << 0, "rtsp": 1 << 1, "pptp": 1 << 2}
    ALG_DEFAULT_ON = ("ftp", "rtsp", "pptp")

    def __init__(self, name: str):
        self.name = name
        self.inside: list[str] = []
        self.outside: list[str] = []
        self.block_size = "512"
        self.ports = ""                 # "" means the device default
        self.policy = "interleaved"
        self.mode = "deterministic"
        # The VRF this pool serves, and the one translated traffic leaves by.
        # "" means every VRF, which is what a configuration that says nothing
        # has always meant - and is a different answer from VRF 0.
        self.vrf = ""
        self.outside_vrf = ""
        # Overrides only. An absent entry means "inherit", which is what the
        # device means by zero, so emitting the inherited value would turn a
        # global change into a diff against every pool.
        self.timeouts: dict[str, int] = {}
        self.max_blocks = 0             # 0 means the device default of one
        # TCP MSS clamp: "" is off, "auto" derives it from the interface
        # MTUs, anything else is the number of bytes.
        self.tcp_mss = ""
        # Raise an alarm when a subscriber's block is this full, in percent.
        # Zero is off.
        self.util_alarm = 0
        # Per-subscriber bounds. Zero is unlimited in both.
        self.max_sessions = 0
        self.max_rate = 0
        # Prefixes that leave untranslated. Kept sorted, because the device
        # prints them in address order and these lines are compared against
        # its own output.
        self.bypass_src: list[str] = []
        self.bypass_dst: list[str] = []
        self.filter = "endpoint-independent"
        self.algs: dict[str, bool] = {}  # only where the operator chose
        # Static port forwards, as (proto, public port, private ip, private
        # port). Sorted the way the device sorts them - by public port, TCP
        # before UDP - because these lines are compared against the device's
        # own "show bpa config" and any other order is a diff on every commit.
        self.forwards: list[tuple[str, int, str, int]] = []

    FWD_PROTOS = ("tcp", "udp")

    @staticmethod
    def _fwd_key(f):
        return (f[1], Pool.FWD_PROTOS.index(f[0]))

    def lines(self, in_place=True) -> list[str]:
        """
        The pool as configuration text.

        `in_place=False` gives only the part of a pool that can be changed
        ONLY by recreating it: the prefixes, the block size, the port range,
        the policy and the mode. Those change the MAPPING, and every session on
        the pool is invalid the moment they change.

        Everything else is left out, because the dataplane can change it on a
        pool that is carrying traffic and redefining the pool to do so would
        drop every session it holds. A whole subscriber base disconnected to
        turn off one ALG, cap one infected host or give one customer an inbound
        port is not a change anyone would ask for (issue #69).
        """
        out = [f"cgnat pool {self.name}"]
        out += [f" inside {p}" for p in self.inside]
        out += [f" outside {p}" for p in self.outside]
        out.append(f" block-size {self.block_size}")
        if self.ports:
            out.append(f" ports {self.ports}")
        out.append(f" policy {self.policy}")
        out.append(f" mode {self.mode}")
        # Not behind `in_place`: changing which VRF a pool serves changes which
        # subscribers it maps, so every session on it is invalid - exactly like
        # a change of prefix, and it rebuilds the pool for the same reason.
        if self.vrf != "":
            out.append(f" vrf {self.vrf}")
        if self.outside_vrf != "":
            out.append(f" outside-vrf {self.outside_vrf}")
        if in_place:
            for t in self.TIMEOUTS:
                if t in self.timeouts:
                    out.append(f" timeout {t} {self.timeouts[t]}")
            if self.max_blocks > 1:
                out.append(f" max-blocks {self.max_blocks}")
        if in_place:
            out += [f" bypass source {p}" for p in sorted(self.bypass_src)]
            out += [f" bypass destination {p}"
                    for p in sorted(self.bypass_dst)]
        if in_place and self.util_alarm:
            out.append(f" utilisation-alarm {self.util_alarm}")
        if in_place and self.max_sessions:
            out.append(f" max-sessions {self.max_sessions}")
        if in_place and self.max_rate:
            out.append(f" max-session-rate {self.max_rate}")
        if in_place:
            if self.tcp_mss:
                out.append(f" tcp-mss {self.tcp_mss}")
            if self.filter != "endpoint-independent":
                out.append(f" filter {self.filter}")
            for a in self.ALG_BITS:
                if a in self.algs:
                    out.append(
                        f" alg {a} {'on' if self.algs[a] else 'off'}")
        if in_place:
            for proto, pub, ip, priv in sorted(self.forwards,
                                               key=self._fwd_key):
                out.append(f" forward {proto} {pub} to {ip} {priv}")
        out.append(" exit")
        return out

    def device_command(self) -> str:
        parts = ["bpa pool", self.name]
        for p in self.inside:
            parts += ["inside", p]
        for p in self.outside:
            parts += ["outside", p]
        parts += ["block", self.block_size]
        if self.ports:
            parts += ["ports", self.ports.replace(" ", "-")]
        parts += ["policy", self.policy, "mode", self.mode]
        if self.vrf != "":
            parts += ["vrf", self.vrf]
        if self.outside_vrf != "":
            parts += ["outside-vrf", self.outside_vrf]
        for t in self.TIMEOUTS:
            if t in self.timeouts:
                parts += ["timeout", t, str(self.timeouts[t])]
        if self.max_blocks > 1:
            parts += ["max-blocks", str(self.max_blocks)]
        for p in sorted(self.bypass_src):
            parts += ["bypass", "source", p]
        for p in sorted(self.bypass_dst):
            parts += ["bypass", "destination", p]
        if self.util_alarm:
            parts += ["utilisation-alarm", str(self.util_alarm)]
        if self.max_sessions:
            parts += ["max-sessions", str(self.max_sessions)]
        if self.max_rate:
            parts += ["max-session-rate", str(self.max_rate)]
        if self.tcp_mss:
            parts += ["tcp-mss", self.tcp_mss]
        if self.filter != "endpoint-independent":
            parts += ["filter", self.filter]
        for a in self.ALG_BITS:
            if a in self.algs:
                parts += ["alg", a, "on" if self.algs[a] else "off"]
        # Included here and NOT in api_args(): this command is what `commit
        # check` sends, and a forward can only be judged against the pool it
        # belongs to - for a pool that does not exist yet, that pool is this
        # line. Applying them is a separate message; see forward_api_args().
        for proto, pub, ip, priv in sorted(self.forwards, key=self._fwd_key):
            parts += ["forward", proto, str(pub), "to", ip, str(priv)]
        return " ".join(parts)

    def bypass_api_args(self, is_dst: bool) -> dict:
        """One list, entire: the device replaces it rather than editing it."""
        prefixes = self.bypass_dst if is_dst else self.bypass_src
        return {
            "name": self.name,
            "is_destination": bool(is_dst),
            "n_prefixes": len(prefixes),
            "prefixes": sorted(prefixes),
        }

    def settings_api_args(self) -> dict:
        """
        Every setting the device can change without recreating the pool.

        Sent whole rather than as a diff: the dataplane takes them together
        under one worker barrier, and a pool half-way between two settings is a
        pool behaving as neither.
        """
        api_t = {"udp": "udp", "icmp": "icmp", "tcp-syn": "tcp_syn",
                 "tcp-transitory": "tcp_transitory",
                 "tcp-established": "tcp_established", "gre": "gre",
                 "port-holddown": "port_holddown",
                 "alg-expect": "alg_expect"}
        timeouts = {v: 0 for v in api_t.values()}
        for k, v in self.timeouts.items():
            timeouts[api_t[k]] = int(v)

        alg = alg_set = 0
        for name, bit in self.ALG_BITS.items():
            if self.algs.get(name, name in self.ALG_DEFAULT_ON):
                alg |= bit
            if name in self.algs:
                alg_set |= bit

        return {
            "name": self.name,
            "filter": "BPA_API_FILTER_"
                      + self.filter.upper().replace("-", "_"),
            "max_blocks": int(self.max_blocks),
            "alg": alg,
            "alg_set": alg_set,
            "tcp_mss": 0 if self.tcp_mss in ("", "auto") else int(self.tcp_mss),
            "tcp_mss_auto": self.tcp_mss == "auto",
            "util_alarm": int(self.util_alarm),
            "timeouts": timeouts,
        }

    def limits_api_args(self) -> dict:
        """Both bounds, always together: the device takes them as a pair."""
        return {
            "name": self.name,
            "max_sessions": int(self.max_sessions),
            "max_rate": int(self.max_rate),
        }

    def forward_api_args(self, fwd, is_add=True) -> dict:
        proto, pub, ip, priv = fwd
        return {
            "is_add": bool(is_add),
            "name": self.name,
            "proto": "BPA_API_FWD_" + proto.upper(),
            "port_pub": int(pub),
            "port_priv": int(priv),
            "ip_priv": ip,
        }

    def forward_command(self, fwd, is_add=True, check=False) -> str:
        proto, pub, ip, priv = fwd
        if not is_add:
            return f"bpa forward {self.name} {proto} {pub} del"
        tail = " check" if check else ""
        return f"bpa forward {self.name} {proto} {pub} to {ip} {priv}{tail}"

    def api_args(self, is_add=True) -> dict:
        """
        The same pool as binary-API arguments.

        Kept next to device_command() on purpose: the two describe one pool in
        two languages, and a field added to one and forgotten in the other is a
        setting the operator configures and the dataplane never receives.
        """
        port_min, port_max = 1024, 65535
        if self.ports:
            parts = self.ports.replace("-", " ").split()
            if len(parts) == 2:
                port_min, port_max = int(parts[0]), int(parts[1])
        # Two masks, as the dataplane stores them: what is enabled, and what
        # the operator actually chose. Anything not chosen is left to
        # bpa_pool_init(), so a pool that says nothing about ALGs gets the
        # documented defaults rather than "all off".
        alg = alg_set = 0
        for name, bit in self.ALG_BITS.items():
            on = self.algs.get(name, name in self.ALG_DEFAULT_ON)
            if on:
                alg |= bit
            if name in self.algs:
                alg_set |= bit

        api_t = {"udp": "udp", "icmp": "icmp", "tcp-syn": "tcp_syn",
                 "tcp-transitory": "tcp_transitory",
                 "tcp-established": "tcp_established", "gre": "gre",
                 "port-holddown": "port_holddown",
                 "alg-expect": "alg_expect"}
        timeouts = {v: 0 for v in api_t.values()}
        for k, v in self.timeouts.items():
            timeouts[api_t[k]] = int(v)

        return {
            "is_add": bool(is_add),
            "name": self.name,
            "block_size": int(self.block_size),
            "port_min": port_min,
            "port_max": port_max,
            "policy": f"BPA_API_POLICY_{self.policy.upper()}",
            # 4294967295 is ~0: "every VRF". Zero would be VRF 0.
            "vrf_id": 4294967295 if self.vrf == "" else int(self.vrf),
            "out_vrf_id": (4294967295 if self.outside_vrf == ""
                           else int(self.outside_vrf)),
            "mode": f"BPA_API_MODE_{self.mode.upper()}",
            "filter": "BPA_API_FILTER_"
                      + self.filter.upper().replace("-", "_"),
            "max_blocks": int(self.max_blocks),
            "tcp_mss": 0 if self.tcp_mss in ("", "auto") else int(self.tcp_mss),
            "tcp_mss_auto": self.tcp_mss == "auto",
            "alg": alg,
            "alg_set": alg_set,
            "timeouts": timeouts,
            "n_inside": len(self.inside),
            "n_prefixes": len(self.inside) + len(self.outside),
            "prefixes": list(self.inside) + list(self.outside),
        }

    def __eq__(self, other):
        return isinstance(other, Pool) and self.lines() == other.lines()


def _address_family_error(keyword: str, prefix: str):
    """
    None when the prefix matches the keyword, a sentence when it does not.

    `ipv6 address 10.0.0.1/24` is a typo, not a configuration. VPP would take
    it - the message carries one prefix field for both families and infers the
    family from the value - so without this check the box would accept the line
    and quietly configure something the operator did not write.
    """
    is_v6 = ":" in prefix
    if keyword == "ipv6" and not is_v6:
        return f"'{prefix}' is not an IPv6 prefix"
    if keyword == "ipv4" and is_v6:
        return f"'{prefix}' is not an IPv4 prefix"
    if "/" not in prefix:
        return f"'{prefix}' has no prefix length"
    return None


class Config:
    """A complete device configuration."""

    def __init__(self):
        # Lines the model did not understand, as (line number, text, why).
        # Never discarded: a configuration that is accepted and silently
        # trimmed is a configuration that lies about the running box.
        self.unknown: list[tuple[int, str, str]] = []
        self.pools: dict[str, Pool] = {}
        self.interfaces: dict[str, str] = {}   # name -> inside|outside
        # MTU is deliberately its own map, not a field of the CGNAT interface
        # entry: an interface can need an explicit MTU without carrying CGNAT
        # at all. docs/routing.md records why it must never be inherited - an
        # unaligned MTU stalls OSPF in Exchange with no log saying why.
        self.mtus: dict[str, int] = {}         # name -> MTU
        # Unicast RPF, per interface: name -> "strict" | "loose". Absent means
        # off, which is the default and deliberately so - turning it on where
        # the topology is asymmetric drops traffic that was being forwarded a
        # moment ago, and it does so silently. docs/architecture.md says what
        # it protects and, just as important, what it does not.
        self.urpf: dict[str, str] = {}
        # What the interface is for, and whether it is administratively up.
        #
        # The description lives in the DATAPLANE, as a VPP interface tag, and
        # is read back from there like everything else here - not remembered by
        # this VTY. A description only the VTY knows is one that disagrees with
        # every other tool on the box.
        self.descriptions: dict[str, str] = {}   # name -> text
        # Addresses per interface, in the order written. A list and not a set:
        # more than one address per family is ordinary on a router, and the
        # order is what an operator reads back to check their own work.
        self.addresses: dict[str, list] = {}     # name -> ["a.b.c.d/n", ...]
        # Sub-interfaces: name -> {"outer": int, "inner": int|None}.
        #
        # Kept apart from the other maps because this one CREATES the
        # interface rather than configuring one that exists, which changes
        # what has to happen first at commit and what the existence check may
        # refuse.
        self.vlans: dict[str, dict] = {}
        # The routing table an interface's addresses live in. Numeric, the
        # same way a pool's `vrf` is numeric: two ways to name the same table
        # would be two things to keep in step, and they would disagree.
        # Absent means table 0, which is the default and needs no statement.
        self.vrfs: dict[str, int] = {}
        # The MAC the interface answers to, when it is not the one the card
        # came with. Read back from the dataplane, so a box that was never
        # told carries nothing here.
        self.macs: dict[str, str] = {}
        # Interfaces where router advertisements are suppressed.
        #
        # VPP sends RAs every 200 seconds on any interface holding an IPv6
        # address - measured, not assumed. On a subscriber-facing port that
        # advertises the box as a default router, which is a second gateway
        # for subscribers who get their prefix by DHCPv6-PD.
        self.ra_suppressed: set = set()
        # Present means shut down. Absent means up, which is the default and
        # the state of every interface nobody has touched, so recording the
        # absence would make every box look configured.
        self.admin_down: set = set()
        # The box's own configuration: the SSH daemon and the accounts that
        # may log in. Not CGNAT and not routing, but committed and rolled back
        # with both - it used to be the only configuration on the appliance
        # that `commit` did not cover, which made it the one change nobody
        # could undo cleanly.
        self.ssh: dict = dict(SSH_DEFAULTS)
        self.users: dict = {}
        self.snmp: dict = dict(SNMP_DEFAULTS)
        self.snmp_communities: list = []
        self.snmp_users: dict = {}
        self.globals: list[str] = []           # other cgnat statements
        self.routing: list[str] = []           # verbatim FRR statements
        # How many statements the text actually held. Counted rather than
        # inferred from the fields: ssh and snmp start as copies of their
        # defaults, so a file that sets one of them to its default value would
        # look identical to a file that said nothing - and "said nothing" is
        # the case this number exists to identify. See is_empty().
        self.n_statements: int = 0

    def is_empty(self) -> bool:
        """
        True when the file held no statements at all - only comments, or
        nothing.

        An empty file is not a configuration that removes everything; it is a
        configuration nobody has written yet. The installer ships exactly such
        a file - "!\n! HexaRouting\n!" - and reading it as removal made
        `hexash --load` refuse it at the first boot of every fresh install:
        the box had SSH access from the installer and the file did not mention
        SSH, so the lockout check saw a change that took the access away.
        https://github.com/andrediashexa/hexarouting/issues/121

        This asks whether the text held any statement, NOT whether the result
        differs from the defaults. A file saying `ssh password-authentication
        yes` states something, even though that is the default value, and a
        configuration that states something MUST be applied and checked like
        any other. Comparing against the defaults instead would silently skip
        it.
        """
        return self.n_statements == 0

    # ------------------------------------------------------------ text

    def text(self) -> str:
        out = [CGNAT_HEADER]
        for name in sorted(self.pools):
            out += self.pools[name].lines()
        for ifname in sorted(self.interfaces):
            out.append(f"cgnat interface {ifname} {self.interfaces[ifname]}")
        for ifname in sorted(set(self.mtus) | set(self.urpf)
                             | set(self.descriptions) | set(self.admin_down)
                             | set(self.addresses) | set(self.vlans)
                             | set(self.vrfs) | set(self.macs)
                             | set(self.ra_suppressed)):
            out.append(f"interface {ifname}")
            # Description first: it is what the interface IS, and a reader
            # scanning a long configuration for one port finds it by name.
            if ifname in self.macs:
                out.append(f" mac-address {self.macs[ifname]}")
            if ifname in self.ra_suppressed:
                out.append(" ipv6 nd suppress-ra")
            if ifname in self.vrfs:
                out.append(f" vrf {self.vrfs[ifname]}")
            if ifname in self.vlans:
                t = self.vlans[ifname]
                out.append(f" vlan {t['outer']}" +
                           (f" inner {t['inner']}" if t["inner"] else ""))
            if ifname in self.descriptions:
                out.append(f" description {self.descriptions[ifname]}")
            for a in self.addresses.get(ifname, []):
                out.append(f" ipv{'6' if ':' in a else '4'} address {a}")
            if ifname in self.admin_down:
                out.append(" shutdown")
            if ifname in self.mtus:
                out.append(f" mtu {self.mtus[ifname]}")
            if ifname in self.urpf:
                out.append(f" urpf {self.urpf[ifname]}")
            out.append(" exit")
        for k in SSH_FIELDS:
            out.append(f"ssh {k} {self.ssh.get(k, SSH_DEFAULTS[k])}")
        for name in sorted(self.users):
            out += self.users[name].lines()
        for k in SNMP_FIELDS:
            v = self.snmp.get(k, SNMP_DEFAULTS[k])
            if v != "":
                out.append(f"snmp {k} {v}")
        for c in self.snmp_communities:
            out.append(f"snmp community {c}")
        for name in sorted(self.snmp_users):
            out += self.snmp_users[name].lines()
        out += sorted(self.globals)
        out.append(ROUTING_HEADER)
        out += self.routing
        return "\n".join(out) + "\n"

    @classmethod
    def parse(cls, text: str) -> "Config":
        cfg = cls()
        section = "cgnat"
        pool = None
        iface = None
        user = None
        snmp_user = None
        for lineno, raw in enumerate(text.splitlines(), 1):
            line = raw.rstrip()
            if not line.strip():
                continue
            if line.strip() == ROUTING_HEADER:
                section = "routing"
                continue
            if line.strip() == CGNAT_HEADER:
                section = "cgnat"
                continue
            if section == "routing":
                cfg.n_statements += 1
                cfg.routing.append(line)
                continue

            # `!` is the comment marker in this file format - the same one FRR
            # and every configuration written in this style use. The two
            # section headers above are the only `!` lines that mean anything;
            # the rest are comments and MUST be ignored.
            #
            # They were not, and install.sh writes "!\n! HexaRouting\n!\n" as
            # the default configuration. So hexash --load refused to load it -
            # "these lines are not understood" - and exited non-zero, which
            # made hexarouting.service FAIL ON THE FIRST BOOT OF EVERY INSTALL
            # (#94). The banner this project ships was a syntax error to the
            # parser this project ships.
            #
            # Routing keeps its own `!` lines: the branch above returns first,
            # because FRR uses them as separators and frr-reload is given the
            # section verbatim.
            if line.lstrip().startswith("!"):
                continue

            cfg.n_statements += 1
            s = line.strip()
            if s.startswith("snmp-user "):
                w = s.split()
                if len(w) < 2:
                    cfg.unknown.append((lineno, s, "an snmp user needs a name"))
                    continue
                snmp_user = SnmpUser(w[1])
                cfg.snmp_users[snmp_user.name] = snmp_user
                pool = iface = user = None
                continue
            if snmp_user is not None and line.startswith(" "):
                w = s.split()
                if s == "exit":
                    snmp_user = None
                elif w[0] in ("auth", "priv") and len(w) > 2:
                    setattr(snmp_user, w[0], " ".join(w[1:]))
                else:
                    cfg.unknown.append(
                        (lineno, s, "an snmp user understands 'auth <proto> "
                                    "<passphrase>' and 'priv <proto> "
                                    "<passphrase>'"))
                continue
            if s.startswith("snmp community "):
                w = s.split()
                if len(w) < 3:
                    cfg.unknown.append((lineno, s, "expected: snmp community <name>"))
                elif w[2] not in cfg.snmp_communities:
                    cfg.snmp_communities.append(w[2])
                continue
            if s.startswith("snmp "):
                w = s.split(None, 2)
                if len(w) < 3:
                    cfg.unknown.append((lineno, s, "expected: snmp <setting> <value>"))
                else:
                    why = validate_snmp({w[1]: w[2]})
                    if why:
                        cfg.unknown.append((lineno, s, why))
                    else:
                        cfg.snmp[w[1]] = w[2]
                continue
            if s.startswith("ssh "):
                w = s.split(None, 2)
                if len(w) < 3:
                    cfg.unknown.append((lineno, s, "expected: ssh <setting> <value>"))
                else:
                    why = validate_ssh({w[1]: w[2]})
                    if why:
                        cfg.unknown.append((lineno, s, why))
                    else:
                        cfg.ssh[w[1]] = w[2]
                continue
            if s.startswith("user "):
                w = s.split()
                if len(w) < 2:
                    cfg.unknown.append((lineno, s, "a user needs a name"))
                    continue
                user = User(w[1])
                cfg.users[user.name] = user
                pool = iface = None
                continue
            if user is not None and line.startswith(" "):
                why = cfg._user_line(user, s)
                if why:
                    cfg.unknown.append((lineno, s, why))
                if s == "exit":
                    user = None
                continue
            if s.startswith("cgnat pool"):
                w = s.split()
                if len(w) < 3:
                    cfg.unknown.append((lineno, s, "a pool needs a name"))
                    continue
                pool = Pool(w[2])
                cfg.pools[pool.name] = pool
            elif pool is not None and line.startswith(" "):
                why = cfg._pool_line(pool, s)
                if why:
                    cfg.unknown.append((lineno, s, why))
                if s == "exit":
                    pool = None
            elif s.startswith("cgnat interface "):
                w = s.split()
                if len(w) < 4:
                    cfg.unknown.append(
                        (lineno, s, "expected: cgnat interface <name> "
                                    "<inside|outside>"))
                elif w[3] not in ("inside", "outside"):
                    cfg.unknown.append(
                        (lineno, s, f"'{w[3]}': expected inside or outside"))
                else:
                    cfg.interfaces[w[2]] = w[3]
            elif s.startswith("no interface "):
                # Removing a sub-interface removes everything on it: the
                # interface is going away, and leaving its address or MTU
                # behind would describe a port that does not exist.
                gone = s.split()[2]
                cfg.vlans.pop(gone, None)
                cfg.addresses.pop(gone, None)
                cfg.mtus.pop(gone, None)
                cfg.urpf.pop(gone, None)
                cfg.descriptions.pop(gone, None)
                cfg.admin_down.discard(gone)
                cfg.interfaces.pop(gone, None)
            elif s.startswith("interface "):
                iface = s.split()[1]
            elif iface is not None and line.startswith(" "):
                w = s.split()
                if s == "exit":
                    iface = None
                elif w[0] == "mtu" and len(w) > 1 and w[1].isdigit():
                    cfg.mtus[iface] = int(w[1])
                elif w[0] == "mac-address" and len(w) > 1:
                    if len(w[1].split(":")) != 6:
                        cfg.unknown.append(
                            (lineno, s, f"'{w[1]}' is not a MAC address"))
                    else:
                        cfg.macs[iface] = w[1].lower()
                elif s == "no mac-address":
                    cfg.macs.pop(iface, None)
                elif s == "ipv6 nd suppress-ra":
                    cfg.ra_suppressed.add(iface)
                elif s == "no ipv6 nd suppress-ra":
                    cfg.ra_suppressed.discard(iface)
                elif w[0] == "vrf" and len(w) > 1 and w[1].isdigit():
                    if int(w[1]):
                        cfg.vrfs[iface] = int(w[1])
                    else:
                        cfg.vrfs.pop(iface, None)
                elif s == "no vrf":
                    cfg.vrfs.pop(iface, None)
                elif w[0] == "vlan" and len(w) > 1 and w[1].isdigit():
                    tag = {"outer": int(w[1]), "inner": None}
                    if len(w) >= 4 and w[2] == "inner" and w[3].isdigit():
                        tag["inner"] = int(w[3])
                    elif len(w) != 2:
                        cfg.unknown.append(
                            (lineno, s, "expected: vlan <id> [inner <id>]"))
                        continue
                    if "." not in iface:
                        cfg.unknown.append(
                            (lineno, s, f"'{iface}' is not a sub-interface; "
                                        "a VLAN tag belongs to one, named "
                                        "<parent>.<n>"))
                    else:
                        cfg.vlans[iface] = tag
                elif w[0] in ("ipv4", "ipv6") and w[1:2] == ["address"] \
                        and len(w) > 2:
                    why = _address_family_error(w[0], w[2])
                    if why:
                        cfg.unknown.append((lineno, s, why))
                    else:
                        cfg.addresses.setdefault(iface, [])
                        if w[2] not in cfg.addresses[iface]:
                            cfg.addresses[iface].append(w[2])
                elif w[:2] in (["no", "ipv4"], ["no", "ipv6"]) \
                        and w[2:3] == ["address"] and len(w) > 3:
                    if iface in cfg.addresses and w[3] in cfg.addresses[iface]:
                        cfg.addresses[iface].remove(w[3])
                elif w[0] == "description" and len(w) > 1:
                    # Everything after the keyword, spaces included. Splitting
                    # on words would turn "uplink para o transito" into
                    # "uplink" and lose the rest without saying so.
                    cfg.descriptions[iface] = s.split(None, 1)[1]
                elif s == "no description":
                    cfg.descriptions.pop(iface, None)
                elif s == "shutdown":
                    cfg.admin_down.add(iface)
                elif s == "no shutdown":
                    cfg.admin_down.discard(iface)
                elif w[0] == "urpf" and len(w) > 1:
                    if w[1] not in URPF_MODES:
                        cfg.unknown.append(
                            (lineno, s, f"urpf '{w[1]}': expected one of "
                                        + ", ".join(URPF_MODES)))
                    elif w[1] == "off":
                        cfg.urpf.pop(iface, None)
                    else:
                        cfg.urpf[iface] = w[1]
                else:
                    cfg.unknown.append(
                        (lineno, s, "an interface understands 'description "
                                    "<text>', 'shutdown', 'mtu <n>' and "
                                    "'urpf <strict|loose|off>'"))
            elif s.startswith("cgnat "):
                cfg.globals.append(s)
            else:
                cfg.unknown.append((lineno, s, "not a CGNAT statement"))
        return cfg

    @staticmethod
    def _user_line(user: User, s: str):
        """One line of a user block. Returns None, or why it was refused."""
        w = s.split(None, 1)
        if not w:
            return None
        if w[0] == "ssh-key" and len(w) > 1:
            # Not parsed further on purpose: an authorized_keys line has
            # options, comments and key types this VTY has no business
            # having an opinion about. sshd is the thing that validates it.
            user.keys.append(w[1].strip())
        elif w[0] == "password" and len(w) > 1:
            h = w[1].strip()
            # A crypt(3) hash, never a plaintext password. Storing plaintext
            # in /etc/shadow does not fail loudly - it produces an account
            # whose password simply never matches.
            if not h.startswith("$") or h.count("$") < 3:
                return ("password: expected a crypt hash such as "
                        "$y$... or $6$... (openssl passwd -6), not a plaintext "
                        "password")
            user.password = h
        elif w[0] == "exit":
            pass
        else:
            return f"'{s}' is not something a user block understands"
        return None

    @staticmethod
    def _pool_line(pool: Pool, s: str):
        """
        Apply one line of a pool block. Returns None, or why it was refused.

        Every branch that does not match falls through to a refusal. It used to
        fall through to nothing at all: `filter`, `max-blocks`, the per-pool
        timeouts and the ALG switches were accepted at the prompt, accepted at
        commit, and never reached the dataplane - four merged features that did
        not exist through the interface an operator actually uses (issue #47).
        """
        w = s.split()
        if not w:
            return None
        if w[0] == "inside" and len(w) > 1:
            pool.inside.append(w[1])
        elif w[0] == "outside" and len(w) > 1:
            pool.outside.append(w[1])
        elif w[0] == "block-size" and len(w) > 1:
            pool.block_size = w[1]
        elif w[0] == "ports" and len(w) > 2:
            pool.ports = f"{w[1]} {w[2]}"
        elif w[0] == "policy" and len(w) > 1:
            if w[1] not in ("interleaved", "blocked"):
                return f"policy '{w[1]}': expected interleaved or blocked"
            pool.policy = w[1]
        elif w[0] in ("vrf", "outside-vrf") and len(w) > 1:
            if not w[1].isdigit():
                return f"{w[0]} '{w[1]}': expected a VRF id"
            if w[0] == "vrf":
                pool.vrf = w[1]
            else:
                pool.outside_vrf = w[1]
        elif w[0] == "mode" and len(w) > 1:
            if w[1] not in ("deterministic", "dynamic"):
                return f"mode '{w[1]}': expected deterministic or dynamic"
            pool.mode = w[1]
        elif w[0] == "timeout" and len(w) > 2:
            if w[1] not in Pool.TIMEOUTS:
                return (f"timeout '{w[1]}': expected one of "
                        + ", ".join(Pool.TIMEOUTS))
            if not w[2].isdigit():
                return f"timeout {w[1]}: '{w[2]}' is not a number of seconds"
            pool.timeouts[w[1]] = int(w[2])
        elif w[0] == "max-blocks" and len(w) > 1:
            if not w[1].isdigit() or int(w[1]) < 1:
                return f"max-blocks '{w[1]}': expected a number of at least 1"
            pool.max_blocks = int(w[1])
        elif w[0] == "bypass" and len(w) > 2:
            if w[1] not in ("source", "destination"):
                return f"bypass '{w[1]}': expected source or destination"
            if "/" not in w[2]:
                return f"bypass {w[1]}: '{w[2]}' is not a prefix"
            lst = pool.bypass_dst if w[1] == "destination" else pool.bypass_src
            if w[2] not in lst:
                lst.append(w[2])
        elif w[0] == "utilisation-alarm" and len(w) > 1:
            if not w[1].isdigit() or not 1 <= int(w[1]) <= 100:
                return (f"utilisation-alarm '{w[1]}': expected a percentage "
                        "between 1 and 100")
            pool.util_alarm = int(w[1])
        elif w[0] in ("max-sessions", "max-session-rate") and len(w) > 1:
            if not w[1].isdigit():
                return f"{w[0]}: '{w[1]}' is not a number"
            if w[0] == "max-sessions":
                pool.max_sessions = int(w[1])
            else:
                pool.max_rate = int(w[1])
        elif w[0] == "tcp-mss" and len(w) > 1:
            why = Pool.check_mss(w[1])
            if why:
                return why
            pool.tcp_mss = w[1]
        elif w[0] == "filter" and len(w) > 1:
            if w[1] not in Pool.FILTERS:
                return (f"filter '{w[1]}': expected one of "
                        + ", ".join(Pool.FILTERS))
            pool.filter = w[1]
        elif w[0] == "alg" and len(w) > 2:
            if w[1] not in Pool.ALG_BITS:
                return (f"alg '{w[1]}': expected one of "
                        + ", ".join(Pool.ALG_BITS))
            if w[2] not in ("on", "off", "enable", "disable"):
                return f"alg {w[1]} '{w[2]}': expected on or off"
            pool.algs[w[1]] = w[2] in ("on", "enable")
        elif w[0] == "forward":
            # forward <tcp|udp> <public-port> to <private-ip> <private-port>
            if len(w) != 6 or w[3] != "to":
                return ("forward: expected 'forward <tcp|udp> <public-port> "
                        "to <private-ip> <private-port>'")
            if w[1] not in Pool.FWD_PROTOS:
                return (f"forward '{w[1]}': expected one of "
                        + ", ".join(Pool.FWD_PROTOS))
            for p in (w[2], w[5]):
                if not p.isdigit() or not 1 <= int(p) <= 65535:
                    return f"forward: '{p}' is not a port"
            # The device decides whether the address is a subscriber of this
            # pool and whether the port is in its block; it is the only thing
            # that knows the pool arithmetic. Refusing a malformed address
            # here is not that check - it is the one the device would report
            # as a parse error a commit later.
            octets = w[4].split(".")
            if len(octets) != 4 or not all(
                    o.isdigit() and int(o) < 256 for o in octets):
                return f"forward: '{w[4]}' is not an IPv4 address"
            # Refused rather than deduplicated. Silently dropping the second
            # line leaves a configuration whose text does not round-trip, and
            # the device refuses it for the same reason: one public port
            # serves one socket.
            if any(f[0] == w[1] and f[1] == int(w[2]) for f in pool.forwards):
                return (f"forward {w[1]} {w[2]}: this pool already forwards "
                        "that port")
            pool.forwards.append((w[1], int(w[2]), w[4], int(w[5])))
        elif w[0] == "exit":
            pass
        else:
            return f"'{s}' is not something a pool understands"
        return None

    # ------------------------------------------------------------ diff

    def compare(self, other: "Config") -> str:
        """Unified diff from `self` (running) to `other` (candidate)."""
        a = self.text().splitlines(keepends=True)
        b = other.text().splitlines(keepends=True)
        d = list(difflib.unified_diff(a, b, fromfile="running", tofile="candidate",
                                      n=1))
        return "".join(d)

    def is_same(self, other: "Config") -> bool:
        return self.text() == other.text()

    # ------------------------------------------------------------ delta

    def delta_to(self, other: "Config") -> dict:
        """
        What has to change on the device to turn `self` into `other`.

        Pools are replaced whole rather than edited in place: the device takes
        a pool definition in one command, and a partial edit would leave an
        invalid pool between the two halves of the change.
        """
        add, remove, replace = [], [], []
        for name, pool in other.pools.items():
            if name not in self.pools:
                add.append(pool)
            elif self.pools[name].lines(in_place=False) != pool.lines(
                    in_place=False):
                replace.append(pool)
        for name in self.pools:
            if name not in other.pools:
                remove.append(name)

        # Forwards are applied on top of the pool, not inside it. A pool that
        # is being created or recreated arrives with none, so everything the
        # candidate asks for is an addition; a pool that stays gets only the
        # difference, and its sessions survive.
        recreated = {p.name for p in add} | {p.name for p in replace}
        # A pool being created or recreated arrives with both bounds at zero,
        # so anything the candidate asks for is a change.
        # Settings the dataplane can change in place. A pool being created or
        # recreated already carries them, so only a pool that stays needs the
        # separate call.
        settings_set = {}
        for name, pool in other.pools.items():
            if name in recreated:
                continue
            if self.pools[name].settings_api_args() != pool.settings_api_args():
                settings_set[name] = pool

        # Each list is replaced entire, so the delta is "these two lists
        # differ" rather than a set of additions and removals.
        bypass_set = []
        for name, pool in other.pools.items():
            have_s, have_d = (([], []) if name in recreated
                              else (self.pools[name].bypass_src,
                                    self.pools[name].bypass_dst))
            if sorted(have_s) != sorted(pool.bypass_src):
                bypass_set.append((pool, False))
            if sorted(have_d) != sorted(pool.bypass_dst):
                bypass_set.append((pool, True))

        limits_set = {}
        for name, pool in other.pools.items():
            have = ((0, 0) if name in recreated
                    else (self.pools[name].max_sessions,
                          self.pools[name].max_rate))
            if have != (pool.max_sessions, pool.max_rate):
                limits_set[name] = pool
        fwd_add, fwd_del = [], []
        for name, pool in other.pools.items():
            have = (set() if name in recreated
                    else set(self.pools[name].forwards))
            want = set(pool.forwards)
            fwd_del += [(pool, f) for f in sorted(have - want,
                                                  key=Pool._fwd_key)]
            fwd_add += [(pool, f) for f in sorted(want - have,
                                                  key=Pool._fwd_key)]

        if_add = {k: v for k, v in other.interfaces.items()
                  if self.interfaces.get(k) != v}
        if_del = [k for k in self.interfaces if k not in other.interfaces]
        # An interface whose VRF changes has its addresses re-applied as part
        # of the move, so it is kept OUT of the ordinary address delta below.
        # VPP refuses to move an interface that still holds addresses - retval
        # -114 - so the sequence is remove, move, add, and treating it as two
        # independent deltas would produce exactly the refusal.
        vrf_set = {}
        for k in set(self.vrfs) | set(other.vrfs):
            if self.vrfs.get(k, 0) != other.vrfs.get(k, 0):
                vrf_set[k] = (other.vrfs.get(k, 0),
                              list(self.addresses.get(k, [])),
                              list(other.addresses.get(k, [])))

        # Sub-interfaces are created and destroyed, not merely configured, so
        # they are their own part of the delta and are applied around
        # everything else: created before anything can be put on them, and
        # destroyed after everything has been taken off.
        subif_add = {k: v for k, v in other.vlans.items()
                     if self.vlans.get(k) != v}
        subif_del = [k for k in self.vlans if k not in other.vlans]

        # Addresses compared per interface, as sets, so that adding one does
        # not re-apply the others: re-applying an address VPP already has is
        # not free - it briefly removes it, and on the interface carrying the
        # session that is a disconnection nobody asked for.
        addr_add, addr_del = {}, {}
        for k in (set(self.addresses) | set(other.addresses)) - set(vrf_set):
            have = set(self.addresses.get(k, []))
            want = set(other.addresses.get(k, []))
            if want - have:
                addr_add[k] = [a for a in other.addresses.get(k, [])
                               if a not in have]
            if have - want:
                addr_del[k] = sorted(have - want)
        mac_set = {k: v for k, v in other.macs.items()
                   if self.macs.get(k) != v}
        ra_on = sorted(other.ra_suppressed - self.ra_suppressed)
        ra_off = sorted(self.ra_suppressed - other.ra_suppressed)
        desc_set = {k: v for k, v in other.descriptions.items()
                    if self.descriptions.get(k) != v}
        desc_del = [k for k in self.descriptions if k not in other.descriptions]
        # Both directions, and both are changes. Shutting a port and bringing
        # one back are equally consequential, and a delta that only carried one
        # of them would make rollback able to shut interfaces and not reopen
        # them.
        admin_down = sorted(other.admin_down - self.admin_down)
        admin_up = sorted(self.admin_down - other.admin_down)
        mtu_set = {k: v for k, v in other.mtus.items() if self.mtus.get(k) != v}
        urpf_set = {k: v for k, v in other.urpf.items()
                    if self.urpf.get(k) != v}
        # Removing the statement has to turn uRPF off on the device, not leave
        # the last committed mode running. A rollback that cannot undo is not a
        # rollback - the same reason mtu_del exists.
        urpf_del = [k for k in self.urpf if k not in other.urpf]
        # Removing the statement has to put the interface back to the driver
        # default, not leave the last committed value behind. A rollback that
        # cannot undo is not a rollback.
        mtu_del = [k for k in self.mtus if k not in other.mtus]

        # Reported as one flag rather than a delta: the SSH daemon takes its
        # configuration whole, and an account is created or it is not.
        ssh_changed = self.ssh != other.ssh
        snmp_changed = (self.snmp != other.snmp
                        or self.snmp_communities != other.snmp_communities
                        or sorted(self.snmp_users) != sorted(other.snmp_users)
                        or any(self.snmp_users[k] != other.snmp_users[k]
                               for k in self.snmp_users
                               if k in other.snmp_users))
        users_changed = (sorted(self.users) != sorted(other.users)
                         or any(self.users[k] != other.users[k]
                                for k in self.users if k in other.users))

        return {
            "snmp_changed": snmp_changed,
            "ssh_changed": ssh_changed,
            "users_changed": users_changed,
            "pool_add": add,
            "pool_remove": remove,
            "pool_replace": replace,
            "fwd_add": fwd_add,
            "fwd_del": fwd_del,
            "limits_set": limits_set,
            "settings_set": settings_set,
            "bypass_set": bypass_set,
            "if_add": if_add,
            "if_remove": if_del,
            "mac_set": mac_set,
            "ra_on": ra_on,
            "ra_off": ra_off,
            "vrf_set": vrf_set,
            "subif_add": subif_add,
            "subif_del": subif_del,
            "addr_add": addr_add,
            "addr_del": addr_del,
            "desc_set": desc_set,
            "desc_del": desc_del,
            "admin_down": admin_down,
            "admin_up": admin_up,
            "mtu_set": mtu_set,
            "mtu_del": mtu_del,
            "urpf_set": urpf_set,
            "urpf_del": urpf_del,
            "globals": [g for g in other.globals if g not in self.globals],
            "routing_changed": self.routing != other.routing,
        }
