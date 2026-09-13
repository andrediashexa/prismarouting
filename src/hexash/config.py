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
                    validate_role,
                    SNMP_FIELDS, SNMP_DEFAULTS, SnmpUser, validate_snmp)

CGNAT_PREFIXES = ("cgnat ", "no cgnat ")

# Unicast RPF modes, as VPP names them. "off" is accepted in configuration text
# so that an operator can write the absence explicitly, but it is stored as the
# absence of an entry: a configuration that lists every default would diff
# against every device that changes one.
URPF_MODES = ("strict", "loose", "off")
ROUTING_HEADER = "! routing"

#: The hierarchy CGNAT lives under, and the block static routes live in.
#:
#: Neither exists in the tool underneath: VPP's plugin knows `cgnat`, and FRR
#: has no `router static` at all - static routes are top-level `ip route`
#: statements. Both spellings are ACCEPTED here, in both directions, and that
#: is not politeness. Every box already in service has a saved configuration
#: and up to twenty rollback revisions written in the old words, and a grammar
#: change that cannot read the box's own file is a grammar change that leaves
#: the box unconfigured at the next reboot - on a machine nobody is watching,
#: which is exactly when hexarouting.service replays that file.
NAT_HIERARCHY = "nat"
STATIC_BLOCK = "router static"


def strip_nat(line: str) -> str:
    """`nat cgnat ...` written as `cgnat ...`, which is what the model holds."""
    s = line.strip()
    if s == NAT_HIERARCHY:
        return ""
    for prefix in (NAT_HIERARCHY + " ", "no " + NAT_HIERARCHY + " "):
        if s.startswith(prefix):
            keep = s[len(prefix):]
            return ("no " + keep) if prefix.startswith("no ") else keep
    return line


#: The statements that OPEN an FRR block.
#:
#: The same list `Shell.do_config` pushes onto `frr_ctx`, so that the prompt
#: naming the block an operator stands in and `show this` naming what that
#: block holds cannot disagree about what a block is.
FRR_BLOCK_OPENERS = ("router ", "interface ", "vrf ", "route-map ",
                     "address-family ")

#: What ends one. `exit-address-family` is FRR's word for leaving an
#: address-family and `exit-vrf` for leaving a VRF; each closes one level.
FRR_BLOCK_CLOSERS = ("exit", "quit", "end", "exit-vrf", "exit-address-family")


def opens_frr_block(line: str) -> bool:
    """Does this statement open a block? `rpki` is the one that takes no name."""
    s = line.strip()
    return s.startswith(FRR_BLOCK_OPENERS) or s == "rpki"


def block_paths(routing: list) -> list:
    """
    The block path of every line of `routing`, index for index.

    Entry i is the list of block headers enclosing `routing[i]`, outermost
    first - so `[]` for a top-level statement, and for a block's own header
    the path of its PARENT. It is `None` where the line is not a statement of
    the configuration at all: a blank, one of FRR's `!` separators, or a
    block's own `exit`. Index for index rather than a filtered list, because
    the caller that writes the configuration file has to put the lines it did
    not touch back where they were.

    It exists because `routing` is a flat list of verbatim FRR statements
    that carries no record of which block each one is in, and arrives in two
    different shapes: read back from FRR it is INDENTED, and typed at the VTY
    it is not - `Shell.feed` strips leading whitespace before dispatch.
    Everything that has had to ask "which block is this line in" -
    `route_map_findings`, `bgp_errors`, `ospf_state`, `show this` (#180) - has
    answered it again, and by indentation alone it is answered wrongly for
    every line typed in the current session.

    Each shape is read by the evidence it actually carries:

    * An INDENTED line states its level outright: it is inside whatever line
      above it is less indented. Nothing has to be known about which
      statements are blocks, which is what makes a `key chain` or a
      `segment-routing` stanza this VTY does not model itself come out right.
    * A line at column 0 is read by its statement, the way `do_config` reads
      it: an opener starts a block, a closer leaves one, anything else belongs
      to the block currently open.

    A column-0 opener closes what FRR's own grammar says it closes:
    `address-family` is the only one that nests, and it nests in the `router`
    above it. `interface` and `route-map` are top-level contexts in FRR even
    when the VTY let somebody walk into one from inside `router bgp`.
    """
    out = []
    stack = []                  # [indent, statement, children were indented]
    for raw in routing:
        stmt = raw.strip()
        indent = len(raw) - len(raw.lstrip())
        if not stmt:
            out.append(None)
            continue
        if stmt.startswith("!"):
            # FRR separates top-level stanzas with it, and releases before 8.1
            # emit no `exit` at all. `feed` cuts `!` off anything typed, so one
            # reaching here is FRR's own separator.
            #
            # It means "back to the top" only AT COLUMN 0. FRR 10.7.1 also
            # writes an INDENTED one inside a stanza, and reading that as a
            # return to the top put every statement after it in the wrong
            # block. Captured from a live 10.7.1 rather than reasoned about:
            #
            #   router bgp 65000
            #    !
            #    address-family ipv4 unicast
            #     network 200.150.9.16/28
            #    exit-address-family
            #   exit
            #   !
            #
            # The `network` there belongs to `router bgp 65000`, and with the
            # stack cleared by the line above it belonged to nothing. That is
            # the shape of EVERY commit after the first, because cfg.routing is
            # read back from FRR - so a check asking "is this statement under
            # router bgp" answered no for a box that had typed it there.
            if not indent:
                stack = []
            out.append(None)
            continue
        if indent:
            while stack and stack[-1][0] >= indent:
                stack.pop()
            if stack:
                stack[-1][2] = True
        else:
            # Column 0 has to be read two ways, and which one applies is
            # decided by how the block above was written rather than guessed.
            # A block whose statements arrived INDENTED is over at column 0 -
            # that is what the indentation was saying. A block entered at this
            # VTY has no indentation to end it, so it stays open until its
            # own `exit`, exactly as `do_config` keeps it open.
            #
            # The second loop takes back a line pushed as a POSSIBLE header:
            # in indented text that is the only way `key chain KC-CORE` or a
            # `segment-routing` stanza is known to be a block at all - the
            # next line comes in indented under it and nothing was declared.
            while stack and (stack[-1][0] > 0 or stack[-1][2]):
                stack.pop()
            while stack and not opens_frr_block(stack[-1][1]):
                stack.pop()
        if stmt in FRR_BLOCK_CLOSERS:
            # Whatever the loops above left open is the block this closes.
            if stack:
                stack.pop()
            out.append(None)
            continue
        if not indent and opens_frr_block(stmt):
            if stmt.startswith("address-family "):
                while stack and stack[-1][1].startswith("address-family "):
                    stack.pop()         # a sibling family, not a parent
            else:
                stack = []
        out.append([f[1] for f in stack])
        stack.append([indent, stmt, False])
    return out


def as_hierarchy(stmt: str) -> str:
    """
    A canonical FRR statement, named in the words the VTY takes for it.

    A refusal has to name something the operator can find. Typed inside
    `router static`, a route is translated to FRR's `ip route ...` before any
    check runs, so every refusal about one used to name a statement that
    appears nowhere in what the operator typed and nowhere in `show
    configuration` either - which now prints the block. Searching for `ip
    route` after reading the error found nothing.
    """
    s = stmt.strip()
    if s.split()[:2] in (["ip", "route"], ["ipv6", "route"]):
        return f"{STATIC_BLOCK} / route " + " ".join(s.split()[2:])
    return s


def is_static_route(line: str) -> bool:
    """
    A static route as FRR writes one - the statement, not the position.

    Whether it is a TOP-LEVEL one is a separate question and belongs to
    `block_paths`: FRR puts a VRF's static routes inside its `vrf red`
    block, and hoisting one of those into a `router static` block would move
    the route to another table. That used to be decided here by reading column
    0, which is right for a configuration read back from FRR and wrong for
    every line typed at this VTY, where a route typed inside `vrf red` sits at
    column 0 like everything else.
    """
    return bool(line) and line.split()[:2] in (["ip", "route"],
                                               ["ipv6", "route"])


def group_static(routing: list) -> list:
    """
    FRR's flat `ip route` lines, shown as the block this VTY takes.

    Emitted where the FIRST static route was, so a configuration read back
    from FRR and written out again keeps its order. FRR groups them anyway;
    this does not depend on that.
    """
    paths = block_paths(routing)
    top = [p == [] and is_static_route(l) for p, l in zip(paths, routing)]
    statics = [l for l, t in zip(routing, top) if t]
    if not statics:
        return list(routing)
    out, emitted = [], False
    for line, t in zip(routing, top):
        if not t:
            out.append(line)
            continue
        if emitted:
            continue          # already inside the block above
        out.append(STATIC_BLOCK)
        out += [" route " + " ".join(s.split()[2:]) for s in statics]
        out.append(" exit")
        emitted = True
    return out


def frr_block_lines(routing: list, ctx: list) -> list:
    """
    What the block at path `ctx` holds, in the words it was entered in.

    Lines of blocks NESTED below `ctx` come too, indented one space per level:
    an operator standing in `router bgp 65001` is asking what that router
    holds, and its address-families are part of the answer.

    `group_static` first, because `router static` is a block FRR does not have
    - its statements are top-level `ip route` lines - and an operator being
    shown the hierarchy they are editing must be shown it in its own words.
    That transform is the one the configuration file already goes through, so
    what `show this` says a block holds is what a commit will write.
    """
    want = [c.strip() for c in ctx]
    grouped = group_static(routing)
    out = []
    for path, line in zip(block_paths(grouped), grouped):
        if path is not None and path[:len(want)] == want:
            out.append(" " * (len(path) - len(want) + 1) + line.strip())
    return out


def ungroup_static(lines: list) -> list:
    """
    The inverse: a `router static` block back into what FRR understands.

    The family comes from the prefix rather than from a keyword, because a
    prefix with a colon in it is IPv6 and nothing else, and asking the operator
    to say so twice is asking them to contradict themselves.
    """
    out, inside = [], False
    for line in lines:
        s = line.strip()
        if s == STATIC_BLOCK:
            inside = True
            continue
        if inside and s in ("exit", "exit-vrf"):
            inside = False
            continue
        if inside and s.split()[:1] == ["route"] and len(s.split()) > 2:
            rest = s.split(None, 1)[1]
            family = "ipv6" if ":" in rest.split()[0] else "ip"
            out.append(f"{family} route {rest}")
            continue
        if inside and s.split()[:2] == ["no", "route"]:
            rest = s.split(None, 2)[2]
            family = "ipv6" if ":" in rest.split()[0] else "ip"
            out.append(f"no {family} route {rest}")
            continue
        out.append(line)
    return out
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
    ALG_BITS = {"ftp": 1 << 0, "rtsp": 1 << 1, "pptp": 1 << 2, "esp": 1 << 3,
                "sip": 1 << 4}
    ALG_DEFAULT_ON = ("ftp", "rtsp", "pptp", "esp", "sip")

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


#: What a configuration display appends to a routing statement this VTY passed
#: to FRR without a grammar to read it by. Defined here because BOTH halves
#: need it: the display adds it and the parser takes it off, and two spellings
#: of the same string would be a marker that survives a copy-paste into a
#: configuration file.
#:
#: SIXTEEN CHARACTERS, and that is measured, not chosen for looks. The console
#: an operator reaches this box on when the network is down is 80 columns, and
#: the statements most likely to be marked are the long ones - `neighbor
#: 2001:db8:1::1 soft-reconfiguration inbound` is 51 of those columns on its
#: own. The sentence explaining what the mark means goes once at the end of
#: the configuration, in UNCHECKED_NOTE, instead of on every marked line,
#: because a marker that wraps onto a second line stops being read.
UNCHECKED_MARK = "  <- not checked"

#: What the closing sentence under a marked configuration starts with. Skipped
#: by the parser for the same reason the marker is stripped: an operator
#: copies what is on their screen into a file, and this file is read back at
#: boot. `!` opens a comment in this format and in FRR's, and the tag after it
#: keeps the skip from swallowing FRR's own `!` separators, which are part of
#: the routing statements and are handed back to FRR as they were.
UNCHECKED_NOTE = "! hexash:"


def global_key(line: str) -> str:
    """
    Which SETTING a top-level `cgnat ...` statement gives a value to.

    Two statements with the same key are the same setting said twice, and the
    later one replaces the earlier in the candidate. The first three words
    answer that for every one of them but the export state: `cgnat logging
    enable` and `cgnat logging disable` are ONE setting and two values, so
    keying on the value kept both - and the commit then applied both, in
    whatever order they had been typed in.

    Keys are compared whole. The candidate used to test the stored line
    against the key as a PREFIX, which is the same answer for every statement
    this hierarchy has and is one for reasons that hold by accident.
    """
    w = line.split()
    if w[:3] in (["cgnat", "logging", "enable"],
                 ["cgnat", "logging", "disable"]):
        return "cgnat logging"
    return " ".join(w[:3])


def ordered_globals(lines) -> list:
    """
    The `cgnat ...` statements in an order that can be applied as it stands.

    Alphabetical order puts `cgnat logging enable` before `cgnat logging udp
    <collector> src <source>`, and the dataplane refuses the first while the
    second has not been applied - so a saved configuration, which is written
    sorted, refused its own line on reload. That path is the one nobody types
    by hand: it is what the appliance runs at every boot and what `rollback`
    replays.

    The export state therefore goes last, and everything else stays
    alphabetical so that two boxes with the same configuration write the same
    file.
    """
    return sorted(lines, key=lambda l: (global_key(l) == "cgnat logging", l))


def _is_loopback(name: str) -> bool:
    """
    Is this VPP's spelling of a loopback?

    `loop0`, `loop1`. Matched rather than guessed at, because naming an
    interface is how one gets CREATED here, and a typo that happened to look
    like a loopback would silently make one instead of being refused as an
    interface the box does not have.
    """
    return (name.startswith("loop") and len(name) > 4
            and name[4:].isdigit())


def _is_lcp_tap(name: str) -> bool:
    """
    Is this the LINUX end of a linux_cp pair, as VPP names it?

    `tap4096`, `tap4097`. The pair prints as

        itf-pair: [0] host-veth-vpp tap4096 vppwan0 5 type tap

    and the middle name is an interface in VPP's own list, with a MAC, an MTU
    and an admin state of its own. Nobody configures it: it is created by
    `lcp_itf_pair_add_del` and destroyed with the pair.

    Matched on the shape, which is VPP's naming convention for an lcp TAP and
    not something this VTY can ask for. If that convention ever changes the
    worst outcome is the TAP coming back into the running configuration, which
    is the state this replaces - visible, not silent.
    """
    return name.startswith("tap") and len(name) > 3 and name[3:].isdigit()


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
        # The Linux-side name of each interface's linux_cp pair.
        #
        # Nothing in this product created a pair at all until #182: the
        # dataplane owned the NIC, FRR saw no interface for it, and every
        # routing protocol was configurable and inert on an installed box.
        # The labs created the pair by hand, so they proved a box nobody
        # received - the same shape as #99 and #168.
        #
        # Read back from `show lcp`, not remembered. #161 was exactly the
        # defect of a state that could be set and never read: the operator
        # could not turn it off and the running configuration did not admit
        # it was on.
        self.linux_names: dict[str, str] = {}
        # Loopbacks this configuration says should exist. A loopback is
        # CREATED by naming it, the way a sub-interface is, because there is
        # nothing else to create it from - and an ISP router needs one for a
        # router-id and an update-source that do not disappear with a port.
        self.loopbacks: set = set()
        # Interfaces where router advertisements are ENABLED.
        #
        # The default is suppressed, which is the opposite of VPP's. Measured:
        # VPP advertises every 200 seconds on any interface holding an IPv6
        # address, and on a subscriber-facing port that makes the box a second
        # default gateway for everybody who gets their prefix by DHCPv6-PD.
        #
        # Stored as the EXCEPTION rather than the rule, so a box nobody
        # configured carries no statement: recording the default on every
        # interface would put a line under every port on every box, and the
        # configuration would stop being readable.
        self.ra_enabled: set = set()
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

    def interface_lines(self, ifname: str) -> list:
        """
        What this configuration holds for one interface, without its header.

        Split out of `text()` because `show this` inside an interface needs
        the same answer and had its own shorter list - it knew about `mtu` and
        `urpf` and about none of the other nine, so an operator who had just
        typed a description was told the block did not have one (#180). Two
        lists of the fields an interface has is two answers to the same
        question, and the one nobody is looking at is the one that goes stale.
        """
        out = []
        # Description first: it is what the interface IS, and a reader
        # scanning a long configuration for one port finds it by name.
        if ifname in self.linux_names:
            out.append(f" linux-name {self.linux_names[ifname]}")
        if ifname in self.macs:
            out.append(f" mac-address {self.macs[ifname]}")
        if ifname in self.ra_enabled:
            out.append(" no ipv6 nd suppress-ra")
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
        return out

    def text(self) -> str:
        out = [CGNAT_HEADER]
        # `nat ` in front of the CGNAT statements, because the hierarchy is
        # `nat` now. Written flat rather than as an indented block for the same
        # reason `cgnat pool residential` is one line and not two: this format
        # has always flattened a hierarchy onto its statement, and a second
        # indentation level would need `exit` to mean two different things.
        for name in sorted(self.pools):
            pool = self.pools[name].lines()
            out.append(f"{NAT_HIERARCHY} {pool[0]}")
            out += pool[1:]
        for ifname in sorted(self.interfaces):
            out.append(f"{NAT_HIERARCHY} cgnat interface {ifname} "
                       f"{self.interfaces[ifname]}")
        for ifname in sorted(self.interface_names()):
            out.append(f"interface {ifname}")
            out += self.interface_lines(ifname)
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
        out += ordered_globals(self.globals)
        out.append(ROUTING_HEADER)
        out += group_static(self.routing)
        return "\n".join(out) + "\n"

    def interface_names(self) -> set:
        """
        Every interface this configuration has an `interface` block for.

        The same union the renderer walks, so the two cannot disagree about
        which interfaces a file names.
        """
        return (set(self.mtus) | set(self.urpf) | set(self.descriptions)
                | set(self.admin_down) | set(self.addresses) | set(self.vlans)
                | set(self.vrfs) | set(self.macs) | set(self.linux_names)
                | set(self.loopbacks) | set(self.ra_enabled))

    def drop_lcp_taps(self, taps):
        """
        Take the named linux_cp TAPs out of this configuration.

        MEASURED, and what it prevents is a box that does not come up.
        `device_macs` and `device_mtus` report every interface the dataplane
        has, and after #182 gave the product a way to MAKE a pair the
        dataplane has one more: the pair's own TAP. So `save` wrote

            interface tap4097
             mac-address de:ad:00:00:00:07

        into /etc/hexarouting/hexarouting.conf. hexarouting.service hands that
        file straight back to `hexash --load` at every boot, against a VPP
        that has just started and has no pairs yet - so tap4097 does not
        exist, and the interface-existence check refuses the file whole:

            error: interface tap4097: this box has no such interface
                it has: host-veth-vpp, local0, loop7, tap4096, vppctl0
            error: refusing to load, the check above says why

        The box then comes up with no pools, no addresses and no pair at all,
        which is the very symptom #182 was opened about, reached through a
        different door. Found by asking the boot half of #182's first
        requirement out loud in lab/lcp.sh: take a pair down, hand the box the
        file it saved, and watch it refuse its own configuration.

        Takes the names to drop rather than deciding from the name's SHAPE,
        and that is not fussiness. `tap<n>` is also what VPP calls a TAP an
        operator made themselves - `vppctl create tap id 0 host-if-name
        vppwan` produces `tap0`, and lab/hw-vpp.sh puts an address, a default
        route and a CGNAT attachment on exactly that. Dropping it by shape
        would take a real interface's configuration out of the running
        configuration, and the NEXT commit would then remove it from the
        device. That is #47 in one sentence, and it is the reason both callers
        ask the box which TAPs are actually part of a pair.

        `interfaces` - the CGNAT attachments - is deliberately left alone even
        for a name in `taps`. It is a top-level `cgnat interface <name>
        <inside|outside>` statement read back from `show bpa config`, so it is
        only ever populated by something the dataplane really has attached,
        and #47 applies to it with more force than to anything else here.
        """
        taps = set(taps or ())
        if not taps:
            return
        # Everything that renders as a statement INSIDE an `interface` block,
        # which is what the file names an interface in.
        for d in (self.mtus, self.urpf, self.descriptions, self.addresses,
                  self.vlans, self.vrfs, self.macs, self.linux_names):
            for name in [n for n in d if n in taps]:
                del d[name]
        self.admin_down -= taps
        self.ra_enabled -= taps

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
            # A configuration display marks routing statements this VTY did
            # not verify, and closes with a sentence saying how many. Both are
            # display transforms - but an operator copies what is on their
            # screen into a file, and this file is what the box reads back at
            # boot: a marker read back as configuration would be appended to a
            # statement handed to FRR, and the sentence would BE one. Taken
            # off here so the screen and the file agree.
            if line.endswith(UNCHECKED_MARK):
                line = line[:-len(UNCHECKED_MARK)].rstrip()
            if line.strip().startswith(UNCHECKED_NOTE):
                continue
            if not line.strip():
                continue
            # The hierarchy the file is written in, back to the words the model
            # holds. Both spellings, in both directions: see NAT_HIERARCHY.
            line = strip_nat(line)
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
                cfg.linux_names.pop(gone, None)
                cfg.loopbacks.discard(gone)
                cfg.macs.pop(gone, None)
                cfg.vrfs.pop(gone, None)
                cfg.ra_enabled.discard(gone)
            elif s.startswith("interface "):
                iface = s.split()[1]
                # A loopback is created by being named, the way a
                # sub-interface is: there is nothing else on the box to
                # create it from. `loop<n>` is VPP's own spelling, so the
                # configuration and `show interface` agree about what the
                # thing is called.
                if _is_loopback(iface):
                    cfg.loopbacks.add(iface)
            elif iface is not None and line.startswith(" "):
                w = s.split()
                if s == "exit":
                    iface = None
                elif w[0] == "mtu" and len(w) > 1 and w[1].isdigit():
                    cfg.mtus[iface] = int(w[1])
                elif w[0] == "linux-name" and len(w) > 1:
                    # Linux refuses a name over 15 characters, and VPP's own
                    # API field is 16 with the terminator. Refused here rather
                    # than at commit, because the message an operator gets from
                    # the dataplane for this is a bare retval.
                    if len(w[1]) > 15 or "/" in w[1]:
                        cfg.unknown.append(
                            (lineno, s, f"'{w[1]}' cannot be a Linux "
                             "interface name (15 characters, no '/')"))
                    else:
                        cfg.linux_names[iface] = w[1]
                elif s == "no linux-name":
                    cfg.linux_names.pop(iface, None)
                elif w[0] == "mac-address" and len(w) > 1:
                    if len(w[1].split(":")) != 6:
                        cfg.unknown.append(
                            (lineno, s, f"'{w[1]}' is not a MAC address"))
                    else:
                        cfg.macs[iface] = w[1].lower()
                elif s == "no mac-address":
                    cfg.macs.pop(iface, None)
                elif s == "ipv6 nd suppress-ra":
                    # The default. Accepted because an operator may write it
                    # to say so out loud, and because a configuration saved
                    # from an older box carries it.
                    cfg.ra_enabled.discard(iface)
                elif s == "no ipv6 nd suppress-ra":
                    cfg.ra_enabled.add(iface)
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
        # `router static` back into the top-level `ip route` statements FRR
        # takes. Done after the loop rather than in it, because the block is
        # only recognisable as one from its own `exit`.
        cfg.routing = ungroup_static(cfg.routing)
        # NOT dropping linux_cp TAPs here. A file is parsed with no dataplane
        # in reach - `hexash --load` runs before anything asks VPP anything -
        # so this is the one place that cannot tell a pair's TAP from a TAP an
        # operator made. Shell.load does it, where the box can be asked.
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
        elif w[0] == "role" and len(w) > 1:
            role = w[1].strip()
            why = validate_role(user.name, role)
            if why:
                return why
            user.role = role
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
            # the device refuses it for the same reason: one public socket
            # serves one subscriber socket.
            #
            # The socket is the public ADDRESS and the port, and the address
            # is the subscriber's own - which only the device can work out.
            # So what is refused here is the same port for the same
            # subscriber, which is a collision whatever the arithmetic says.
            # The same port for a DIFFERENT subscriber is left to the device:
            # on a pool with several public addresses those are two sockets,
            # and refusing them here made a port sellable once per pool
            # instead of once per address (issue #214).
            if any(f[0] == w[1] and f[1] == int(w[2]) and f[2] == w[4]
                   for f in pool.forwards):
                return (f"forward {w[1]} {w[2]}: this pool already forwards "
                        f"that port for {w[4]}")
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
        # A loopback is created and destroyed, not merely configured, so both
        # directions are here: a rollback that could create one and not remove
        # it would leave an interface the configuration no longer mentions.
        loopback_add = sorted(other.loopbacks - self.loopbacks)
        loopback_del = sorted(self.loopbacks - other.loopbacks)
        lcp_set = {k: v for k, v in other.linux_names.items()
                   if self.linux_names.get(k) != v}
        lcp_del = [k for k in self.linux_names if k not in other.linux_names]
        # ra_on: start advertising. ra_off: stop. Named for what happens to
        # the advertisements, not for which flag moves, because the flag is
        # "suppress" and reading `ra_on` as "suppression on" is exactly the
        # confusion that inverting this default invites.
        ra_on = sorted(other.ra_enabled - self.ra_enabled)
        ra_off = sorted(self.ra_enabled - other.ra_enabled)
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
            "loopback_add": loopback_add,
            "loopback_del": loopback_del,
            "lcp_set": lcp_set,
            "lcp_del": lcp_del,
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
