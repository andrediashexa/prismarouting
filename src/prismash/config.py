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
import ipaddress
import unicodedata
import hashlib

import policy as policy_mod

from system import (SSH_FIELDS, SSH_DEFAULTS, User, validate_ssh,
                    dns_error, DNS_MAX, hostname_error,
                    address_error,
                    is_number,
                    validate_role,
                    SNMP_FIELDS, SNMP_DEFAULTS, SnmpUser, validate_snmp,
                    validate_trap_host)

CGNAT_PREFIXES = ("cgnat ", "no cgnat ")

#: How many hex characters of the digest are shown. Sixteen is 64 bits, which
#: is far past any accident two configurations could have; what decides the
#: number is the operator reading one off a console and comparing it against
#: the other box, so it is short enough to be read aloud without being
#: re-read. It is a LABEL for a text both boxes can print whole, never a
#: security boundary: nothing here defends against somebody choosing a
#: configuration to collide with another.
MAPPING_DIGEST_CHARS = 16


def mapping_digest(lines) -> str:
    """The digest of a mapping text, in one place so both halves agree."""
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[
        :MAPPING_DIGEST_CHARS]


# Unicast RPF modes, as VPP names them. "off" is accepted in configuration text
# so that an operator can write the absence explicitly, but it is stored as the
# absence of an entry: a configuration that lists every default would diff
# against every device that changes one.
URPF_MODES = ("strict", "loose", "off")

# VRRP, RFC 5798. The numbers are the RFC's, not this product's choice, and
# they are here rather than beside the parser because the VTY's help table
# prints them and a second copy would drift from the check.
#
# 255 is NOT accepted although the field takes it: the RFC reserves it for the
# router that OWNS the virtual address, and a box sharing a service address
# with a peer is never that. A configuration that claims ownership wins every
# election and never gives the address back, which on a pair is not a priority
# but a permanent split.
VRID_MIN, VRID_MAX = 1, 255
VRRP_PRIORITY_MIN, VRRP_PRIORITY_MAX = 1, 254
VRRP_PRIORITY_DEFAULT = 100        # RFC 5798 section 6.1
# What an interface going down takes off the priority. The RFC leaves this to
# the implementation; VPP takes it per tracked interface.
VRRP_DECREMENT_MIN, VRRP_DECREMENT_MAX = 1, 254
ROUTING_HEADER = "! routing"

#: The hierarchy CGNAT lives under, and the block static routes live in.
#:
#: Neither exists in the tool underneath: VPP's plugin knows `cgnat`, and FRR
#: has no `router static` at all - static routes are top-level `ip route`
#: statements. The model holds the tools' words; the file and the prompt use
#: these. Since #446 CGNAT is a block, `nat` > `cgnat`, and the one-line form
#: that used to be accepted beside it is refused - the maintainer's decision
#: of 2026-09-24. A box whose saved file still has it refuses the file at
#: boot, which is why the upgrade page says `save` comes before the reboot.
NAT_HIERARCHY = "nat"
STATIC_BLOCK = "router static"


#: The level under `nat` that CGNAT is (#446). A level of its own, and not
#: `nat` itself, so that other kinds of translation can sit beside it.
CGNAT_LEVEL = "cgnat"
#: The machine's own configuration, as levels (#457): `system` > `dataplane`
#: > `interface <card>`. The maintainer asked for the dataplane statement
#: under `system` and nothing else yet (2026-09-25).
SYSTEM_HIERARCHY = "system"
#: The routing policy's hierarchy (#458), Junos-style.
POLICY_HIERARCHY = "policy-options"
DATAPLANE_LEVEL = "dataplane"
#: The cores by role (#466): `system` > `cpu`, beside `dataplane`.
CPU_LEVEL = "cpu"
#: The statements `cpu` takes, and the tune.env key each is written to. The
#: order is the order the block is written in.
CPU_KEYS = {"control-plane": "PRISMA_CONTROL_PLANE_CORES",
            "main-thread": "PRISMA_MAIN_THREAD",
            "workers": "PRISMA_MAX_WORKERS",
            "cgnat-workers": "PRISMA_CGNAT_WORKERS"}
#: A bound on a typo, not on a machine: the tuner gives what the node has and
#: says so.
CPU_CORES_MAX = 1024


def cpu_error(key, value):
    """Why `cpu <key> <value>` cannot be taken, or None."""
    if key == "control-plane":
        if not is_number(value) or not 1 <= int(value) <= CPU_CORES_MAX:
            return (f"expected: control-plane <1-{CPU_CORES_MAX}>, a number "
                    f"of physical cores")
        return None
    if key == "main-thread":
        if value not in ("shared", "dedicated"):
            return "expected: main-thread <shared|dedicated>"
        return None
    if key == "workers":
        if not is_number(value) or not 1 <= int(value) <= CPU_CORES_MAX:
            return (f"expected: workers <1-{CPU_CORES_MAX}>, at most how "
                    f"many cores VPP polls with")
        return None
    if key == "cgnat-workers":
        if not is_number(value) or not 1 <= int(value) <= CPU_CORES_MAX:
            return (f"expected: cgnat-workers <1-{CPU_CORES_MAX}>, how many "
                    f"workers translate")
        return None
    return ("under 'cpu' there is 'control-plane', 'main-thread', "
            "'workers' and 'cgnat-workers'")


#: The memory by role (#466): `system` > `memory`, beside `cpu`.
MEMORY_LEVEL = "memory"
MEMORY_KEYS = {"control-plane": "PRISMA_CONTROL_PLANE_MEMORY_GB",
               "buffers": "PRISMA_BUFFERS_PER_NUMA"}
#: Bounds on a typo, not on a machine: the tuner caps what is asked at what
#: the box has, and says so. VPP takes up to 2^32 - 1 buffers per node; a
#: million is already 2 GB of 2 KB buffers.
MEMORY_GB_MAX = 4096
BUFFERS_MAX = 16777216


def memory_error(key, value):
    """Why `memory <key> <value>` cannot be taken, or None."""
    if key == "control-plane":
        if not is_number(value) or not 1 <= int(value) <= MEMORY_GB_MAX:
            return (f"expected: control-plane <1-{MEMORY_GB_MAX}>, whole GB "
                    f"kept out of the hugepages")
        return None
    if key == "buffers":
        if not is_number(value) or not 1024 <= int(value) <= BUFFERS_MAX:
            return (f"expected: buffers <1024-{BUFFERS_MAX}>, packet "
                    f"buffers per NUMA node")
        return None
    return "under 'memory' there is 'control-plane' and 'buffers'"


#: The levels under `system` that are tune.env settings: each one's keys and
#: the rule its values are read by. One table, so the file, the prompt, the
#: readback and the commit cannot each know a different list.
SYSTEM_SETTINGS = {CPU_LEVEL: (CPU_KEYS, cpu_error),
                   MEMORY_LEVEL: (MEMORY_KEYS, memory_error)}
#: The most memory `cgnat memory` takes, in GB (#454): a bound on a typo, not
#: on a machine. The tuner caps what is asked at what the box's RAM can give,
#: and says so.
CGNAT_MEMORY_MAX_GB = 4096

#: What a statement in the one-line form is told, at the prompt and in a file.
#: The maintainer's decision of 2026-09-24 was to take the form away rather
#: than keep two spellings, which is the precedent of #436.
ONE_LINE_GONE = ("the one-line CGNAT form is gone: enter 'nat', then "
                 "'cgnat', and type the rest there")


def one_line_cgnat(words) -> bool:
    """Is this the one-line form #446 removed - `nat cgnat ...` or the older
    `cgnat ...`, with or without `no` in front?"""
    w = list(words)
    if w[:1] == ["no"]:
        w = w[1:]
    return w[:1] == [CGNAT_LEVEL] or w[:2] == [NAT_HIERARCHY, CGNAT_LEVEL]


def nest_cgnat(flat) -> list[str]:
    """
    The model's flat CGNAT statements - `cgnat pool p`, its ` inside ...`
    lines, `cgnat interface ...`, `cgnat timeout ...` - as the `nat` >
    `cgnat` block (#446). The inverse of denest_nat(), and the one writer of
    the block: what the box saves, shows and reads back are one shape.
    """
    body = []
    for line in flat:
        if line.startswith(" "):
            body.append("  " + line)
        elif line.startswith(CGNAT_LEVEL + " "):
            body.append("  " + line[len(CGNAT_LEVEL) + 1:])
        else:
            raise ValueError(f"not a CGNAT statement: {line!r}")
    if not body:
        return []
    return [NAT_HIERARCHY, f" {CGNAT_LEVEL}"] + body + ["  exit", " exit"]


def denest_nat(numbered):
    """
    The `nat` > `cgnat` block of a file, as the flat statements the model
    holds: `pool residencial` under `cgnat` becomes `cgnat pool residencial`,
    and a pool's own lines keep their one-space indent.

    `numbered` is [(lineno, line)]. Returns the same list with the block
    rewritten, and [(lineno, statement, why)] for what cannot be read: the
    one-line form (#446), and anything under `nat` that is not `cgnat`.

    DONE BEFORE THE PARSE, not inside it, so that the parser - and every
    check it makes on a pool, an attachment or a global - reads one form, the
    one it has always read. Only the CGNAT section is rewritten: the routing
    section is FRR's, and its words are not this file's to reinterpret.
    """
    out, bad = [], []
    depth = 0          # 0 top, 1 in nat, 2 in cgnat, 3 in a pool
    routing = False
    for n, raw in numbered:
        s = raw.strip()
        if routing or s == ROUTING_HEADER:
            routing = True
            out.append((n, raw))
            continue
        if not s or s.startswith("!"):
            out.append((n, raw))
            continue
        if depth == 0:
            if s == NAT_HIERARCHY:
                depth = 1
            # At column 0 only: indented, `cgnat inside` is a statement of
            # the interface block it sits in (#465), not the one-line form.
            elif not raw[:1].isspace() and one_line_cgnat(s.split()):
                bad.append((n, s, ONE_LINE_GONE))
            else:
                out.append((n, raw))
        elif depth == 1:
            if s == CGNAT_LEVEL:
                depth = 2
            elif s in ("exit", "quit", "end"):
                depth = 0
            else:
                bad.append((n, s, "under 'nat' there is 'cgnat' and nothing "
                                  "else"))
        elif depth == 2:
            if s in ("exit", "quit", "end"):
                depth = 1
                continue
            if s.startswith("pool "):
                depth = 3
            if s.startswith("no "):
                out.append((n, f"no {CGNAT_LEVEL} {s[3:]}"))
            else:
                out.append((n, f"{CGNAT_LEVEL} {s}"))
        else:
            # A pool's own lines are indented under it. One that is not - a
            # pool a hand closed without its `exit` - ends the pool, and the
            # line is read at the `cgnat` level it is indented for.
            if len(raw) - len(raw.lstrip()) <= 2:
                depth = 2
                if s in ("exit", "quit", "end"):
                    depth = 1
                elif s.startswith("no "):
                    out.append((n, f"no {CGNAT_LEVEL} {s[3:]}"))
                else:
                    if s.startswith("pool "):
                        depth = 3
                    out.append((n, f"{CGNAT_LEVEL} {s}"))
                continue
            out.append((n, " " + s))
            if s == "exit":
                depth = 2
    return out, bad


def nest_system(dataplane_if, cpu=None, memory=None) -> list[str]:
    """
    The `system` block a file carries (#457): `dataplane` for the card, and
    `cpu` for the cores by role (#466), each only when it says something -
    and nothing at all when neither does. The one writer of the block: the
    box saves, shows and reads back one shape.
    """
    body = []
    if dataplane_if:
        body += [f" {DATAPLANE_LEVEL}", f"  interface {dataplane_if}",
                 "  exit"]
    for level, held in ((CPU_LEVEL, cpu or {}), (MEMORY_LEVEL, memory or {})):
        if held:
            body.append(f" {level}")
            body += [f"  {k} {held[k]}" for k in SYSTEM_SETTINGS[level][0]
                     if k in held]
            body.append("  exit")
    if not body:
        return []
    return [SYSTEM_HIERARCHY] + body + [" exit"]


def denest_system(numbered):
    """
    The `system` > `dataplane` block of a file as the statement the model
    holds (#457): `interface ens19` under `dataplane` is `dataplane interface
    ens19`. Returns the rewritten list and [(lineno, statement, why)] for what
    cannot be read.

    THE OLD FLAT LINE IS STILL READ. Every box in service has a saved file and
    up to twenty rollback revisions that say `dataplane interface <card>` at
    the top, and prisma-routing.service replays the file at every boot on a
    machine nobody is watching; the next `save` writes the block. Only the
    prompt refuses the old spelling - where there is somebody to be told.
    """
    out, bad = [], []
    depth = 0          # 0 top, 1 in system, 2 in dataplane or cpu
    level = DATAPLANE_LEVEL
    routing = False
    for n, raw in numbered:
        s = raw.strip()
        if routing or s == ROUTING_HEADER:
            routing = True
            out.append((n, raw))
            continue
        if not s or s.startswith("!"):
            out.append((n, raw))
            continue
        if depth == 0:
            if s == SYSTEM_HIERARCHY:
                depth = 1
            else:
                out.append((n, raw))
        elif depth == 1:
            if s == DATAPLANE_LEVEL or s in SYSTEM_SETTINGS:
                depth = 2
                level = s
            elif s in ("exit", "quit", "end"):
                depth = 0
            else:
                bad.append((n, s, "under 'system' there is 'dataplane', "
                                  "'cpu' and 'memory'"))
        else:
            if s in ("exit", "quit", "end"):
                depth = 1
            elif s.startswith("no "):
                out.append((n, f"no {level} {s[3:]}"))
            else:
                out.append((n, f"{level} {s}"))
    return out, bad


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


def block_of(routing: list, stmt: str) -> list:
    """
    The block path `routing` holds `stmt` in, outermost first.

    `as_hierarchy` needs it and cannot derive it: a refusal is handed a
    STRIPPED statement, because `routing_added` deduplicates the candidate
    into bare words and `unreachable_next_hops` returns the same shape. The
    position the statement was typed in is left behind there, and position is
    the whole question for a static route.

    The FIRST occurrence answers, and a configuration holding the same route
    in the global table and in a VRF is therefore named by the global one. It
    is the safe way round: the two are different routes, the operator is
    looking at a prefix that really is in `router static`, and the alternative
    is a message naming a VRF for a route that is also outside it.
    """
    for path, line in zip(block_paths(routing), routing):
        if path is not None and line.strip() == stmt.strip():
            return path
    return []


def as_hierarchy(stmt: str, ctx=()) -> str:
    """
    A canonical FRR statement, named in the words the VTY takes for it.

    A refusal has to name something the operator can find. Typed inside
    `router static`, a route is translated to FRR's `ip route ...` before any
    check runs, so every refusal about one used to name a statement that
    appears nowhere in what the operator typed and nowhere in `show
    configuration` either - which now prints the block. Searching for `ip
    route` after reading the error found nothing.

    `ctx` is where the statement sits, as `block_paths` reports it, and it is
    a parameter because `router static` is the GLOBAL table and a route is
    only in it when nothing encloses it. MEASURED at FRR 10.7.1: a route typed
    into `vrf red` is read back INSIDE that block, indented, still spelled `ip
    route 10.9.0.0/16 10.0.0.9` - so `router static / route ...` names a table
    the route is not in. An operator sent there finds nothing, and typing the
    route where the message said it was puts it in the global table (#251).
    """
    s = stmt.strip()
    if s.split()[:2] in (["ip", "route"], ["ipv6", "route"]):
        # `router static` in the path IS the global table, not an enclosing
        # block: it is this VTY's own word for where a top-level `ip route`
        # goes. The VTY never puts it in `cfg.routing` - `hierarchy_rewrite`
        # consumes it and `Config.parse` calls `ungroup_static` - but a caller
        # holding a configuration in the block spelling would otherwise be
        # told the route is one level deeper than it is.
        path = [c.strip() for c in ctx if c.strip() != STATIC_BLOCK]
        if path:
            # Named the way `show configuration` prints it: FRR keeps a VRF's
            # static routes in their own spelling inside the block, and
            # `group_static` only ever hoists the top-level ones.
            return " / ".join(path + [s])
        return f"{STATIC_BLOCK} / " + static_route_words(s)
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


#: The next hops that are not an address or an interface: FRR's words for
#: "drop it here". Offered by name, and not checked as an address.
STATIC_DROP_HOPS = ("blackhole", "Null0", "reject")
#: Where a static route's own words end and the next begins (#436).
STATIC_KEYWORDS = ("distance", "tag", "description")


def static_route(words):
    """
    A statement typed inside `router static`, as (frr_line, description,
    refusal). Exactly one of frr_line and refusal is None.

    ONE PARSER FOR BOTH DOORS (#436): the prompt calls it from
    `hierarchy_rewrite` and the file from `ungroup_static`, so a route the
    prompt refuses is a route a saved file cannot carry either.

    Two spellings. The one an operator types:

        <prefix> next-hop <hop> [distance <1-255>] [tag <1-4294967295>]
                 [description <text>]

    and nothing else. The spelling before it, `route <prefix> <hop>`, is
    REFUSED at both doors, by the maintainer's decision of 2026-09-24 not to
    carry it (#436). A box whose saved file still has it refuses that file at
    boot, and the upgrade page of the wiki says so before anybody upgrades.

    distance and tag in either order; description last, because it takes
    the rest of the line. Words between the hop and the first of those -
    an interface after an address, `onlink` - are FRR's, handed over as
    typed, and FRR's grammar checks them where they are typed (#431).
    MEASURED on FRR 10.7.1: `ip route P NH tag T D` is the spelling FRR reads
    back, whatever order it was given in, so that is the one produced here.

    The refusal is (verdict, prose): the operator's words go in the prose,
    for the width #363 gives.
    """
    if not words:
        return None, None, ("an empty static route",
                            "type '<prefix> next-hop <next-hop>'")
    if words[0] == "route":
        return None, None, ("'route' is not a statement here any more",
                            "a static route is '<prefix> next-hop "
                            "<next-hop>', as in '192.168.0.0/24 next-hop "
                            "172.16.0.1'")
    prefix = words[0]
    try:
        net = ipaddress.ip_network(prefix, strict=False)
        if "/" not in prefix:
            raise ValueError
    except ValueError:
        return None, None, ("a static route starts with its prefix",
                            f"'{prefix}' is not one; type "
                            f"'<prefix> next-hop <next-hop>', as in "
                            f"'192.168.0.0/24 next-hop 172.16.0.1'")
    if len(words) < 3 or words[1] != "next-hop":
        return None, None, ("a static route needs 'next-hop <next-hop>'",
                            f"after '{prefix}': an address, an interface, "
                            f"or " + ", ".join(STATIC_DROP_HOPS))
    hop = words[2]
    if hop not in STATIC_DROP_HOPS and any(c in hop for c in ".:"):
        try:
            addr = ipaddress.ip_address(hop)
        except ValueError:
            return None, None, ("the next hop is not an address",
                                f"'{hop}' is not one, nor an interface or "
                                + ", ".join(STATIC_DROP_HOPS))
        if addr.version != net.version:
            return None, None, ("the next hop is not in the prefix's family",
                                f"'{hop}' is IPv{addr.version} and "
                                f"'{prefix}' is IPv{net.version}")
    extra, distance, tag, description = [], None, None, None
    i = 3
    while i < len(words):
        w = words[i]
        if w == "description":
            text = " ".join(words[i + 1:])
            if not text:
                return None, None, ("description: no text after it",
                                    "it takes the rest of the line")
            why = text_error("static route description", text)
            if why:
                return None, None, why
            description = text
            break
        if w in ("distance", "tag"):
            if i + 1 >= len(words):
                return None, None, (f"{w}: no value after it",
                                    "distance is 1-255, tag 1-4294967295")
            value = words[i + 1]
            top = 255 if w == "distance" else 4294967295
            if not (is_number(value)
                    and 1 <= int(value) <= top):
                return None, None, (f"{w}: expected 1-{top}",
                                    f"'{value}' is not in that range")
            if (distance if w == "distance" else tag) is not None:
                return None, None, (f"{w}: given twice",
                                    "a route has one of each")
            if w == "distance":
                distance = str(int(value))
            else:
                tag = str(int(value))
            i += 2
            continue
        if distance is not None or tag is not None:
            return None, None, ("a static route: words after distance or tag",
                                f"'{w}' comes after them; FRR's own words go "
                                f"straight after the next hop, and "
                                f"'description' last")
        extra.append(w)
        i += 1
    family = "ipv6" if net.version == 6 else "ip"
    line = f"{family} route {prefix} {hop}"
    if extra:
        line += " " + " ".join(extra)
    if tag is not None:
        line += f" tag {tag}"
    if distance is not None:
        line += f" {distance}"
    return line, description, None


def static_route_key(line: str) -> str:
    """
    A static route's identity: its FRR line without the tag and distance.

    A route IS its prefix and next hop (and whatever else FRR keys it on - an
    interface, a table). Its tag and distance are attributes: typing the
    same pair again with a different tag changes that route, and `no <prefix>
    next-hop <hop>` removes it whatever it carries (#436). A distance is the
    bare number in FRR's spelling, so it is told apart from a `table <n>` or
    a `label <n>` by the word in front of it.
    """
    w = line.split()
    keep, i = w[:4], 4
    while i < len(w):
        if w[i] == "tag" and i + 1 < len(w):
            i += 2
            continue
        if is_number(w[i]) and w[i - 1] not in ("table", "label", "color"):
            i += 1
            continue
        keep.append(w[i])
        i += 1
    return " ".join(keep)


def static_route_words(line: str, description=None) -> str:
    """
    An FRR static route, in the words `router static` takes (#436).

    The inverse of static_route(): FRR's `ip route P NH [..] tag T D` becomes
    `P next-hop NH [..] distance D tag T`, with the description this box
    keeps for it on the end. What FRR carries that this spelling has no word
    for stays where FRR put it, after the next hop, so it survives the trip.
    """
    w = line.split()
    extra, distance, tag, i = [], None, None, 4
    while i < len(w):
        if w[i] == "tag" and i + 1 < len(w):
            tag, i = w[i + 1], i + 2
            continue
        if is_number(w[i]) and w[i - 1] not in ("table", "label", "color"):
            distance, i = w[i], i + 1
            continue
        extra.append(w[i])
        i += 1
    out = f"{w[2]} next-hop {w[3]}"
    if extra:
        out += " " + " ".join(extra)
    if distance is not None:
        out += f" distance {distance}"
    if tag is not None:
        out += f" tag {tag}"
    if description:
        out += f" description {description}"
    return out


def group_static(routing: list, descriptions=None) -> list:
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
        out += [" " + static_route_words(
                    s, (descriptions or {}).get(static_route_key(s)))
                for s in statics]
        out.append(" exit")
        emitted = True
    return out


def frr_block_lines(routing: list, ctx: list, descriptions=None) -> list:
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
    grouped = group_static(routing, descriptions)
    out = []
    for path, line in zip(block_paths(grouped), grouped):
        if path is not None and path[:len(want)] == want:
            out.append(" " * (len(path) - len(want) + 1) + line.strip())
    return out


def ungroup_static(lines: list, descriptions=None, refused=None) -> list:
    """
    The inverse: a `router static` block back into what FRR understands.

    The family comes from the prefix rather than from a keyword, because a
    prefix with a colon in it is IPv6 and nothing else, and asking the operator
    to say so twice is asking them to contradict themselves.
    """
    out, inside = [], False
    for idx, line in enumerate(lines):
        s = line.strip()
        if s == STATIC_BLOCK:
            inside = True
            continue
        if inside and s in ("exit", "exit-vrf"):
            inside = False
            continue
        if inside:
            # The prompt's parser, so the file refuses what the prompt
            # refuses (#436). A line it refuses is REPORTED, index and
            # reason, and not passed to FRR: `refused` is how Config.parse
            # names it with its line number.
            w = s.split()
            neg = w[:1] == ["no"]
            frr, desc, why = static_route(w[1:] if neg else w)
            if why is not None:
                if refused is not None:
                    refused.append((idx, s, " - ".join(why)))
                continue
            if neg:
                out.append("no " + frr)
                continue
            out.append(frr)
            if desc and descriptions is not None:
                descriptions[static_route_key(frr)] = desc
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
        if not is_number(v):
            return f"tcp-mss '{v}': expected a size in bytes, or auto"
        if not Pool.MSS_MIN <= int(v) <= Pool.MSS_MAX:
            return (f"tcp-mss {v}: expected {Pool.MSS_MIN}-{Pool.MSS_MAX}. "
                    "Below the MSS every IPv4 host must accept (RFC 1122) the "
                    "clamp is legal and useless; above a jumbo frame it "
                    "describes no path.")
        return None

    #: The two ends of every pool setting this box holds a number for, and the
    #: only place they are written down. `?` reads them, both parsers refuse by
    #: them, and the wiki is generated from the same tree.
    #:
    #: Each pair is the DATAPLANE'S field, not a round number chosen here:
    #: `max_blocks` is a u8 in `bpa_pool_t` and in bpa.api, so 256 arrives as
    #: 0 and `bpa_pool_init` resolves 0 to 1 - an operator who asked for 256
    #: blocks per subscriber got one block, from a VTY whose help said
    #: `<1-65535>` and whose parser took anything (#351). `block_stride` and
    #: `block_reserve` are u16; the session limits and the timeouts are u32.
    #:
    #: 0 is IN range for the four where it means something the operator can
    #: ask for - no stride, no reserve, no session cap, no rate cap - and OUT
    #: of it for the timeouts, where the message that carries them reads zero
    #: as "leave this one alone".
    RANGES = {
        "block-size": (64, 32768),
        # BOTH ends of `ports`, and the one row that is read twice - once for
        # the min and once for the max. 65535 is the dataplane's field:
        # port_min and port_max are u16 in bpa.api and in bpa_pool_t, and
        # nothing bounded either of them, so `ports 70000 80000` was staged,
        # saved, cast on the way in and arrived as 4464-14464 - the pool that
        # commits is not the pool that was typed, and no refusal downstream
        # can name 70000 because by then 70000 is not the number any more.
        # 1 and not 0 because a source port of 0 is not one a translation can
        # hand out, which is bpa_pool.c's own first clause (#361).
        "ports": (1, 65535),
        "utilisation-alarm": (1, 100),
        "max-blocks": (1, 255),
        # In ports, because ports are what an operator sells (#462). The top
        # is the most a subscriber can hold at all - 255 blocks of the
        # largest block - and the commit check is what holds it to this
        # pool's own `max-blocks`.
        "initial-allocation": (1, 255 * 65536),
        "block-stride": (0, 65535),
        "reserve": (0, 65535),
        "max-sessions": (0, 4294967295),
        "max-session-rate": (0, 4294967295),
        "timeout": (1, 4294967295),
        # 0 is IN range for a POOL's two, and out of it for an interface's:
        # VPP numbers the default table 0, so `vrf 0` names a real table here,
        # while on an interface the same word with the same number is how an
        # operator takes the statement back off. Two meanings, two ranges, and
        # the hints say so - ARG_VRF against ARG_VRF_ID.
        "vrf": (0, 4294967295),
        "outside-vrf": (0, 4294967295),
    }

    @staticmethod
    def check_range(name: str, v: str, what=""):
        """
        None if `v` is in the range `name` is offered with, otherwise why not.

        Named after the STATEMENT rather than taking two numbers, because the
        two numbers are what the help text quotes and a caller that passes its
        own is a second copy of them - the thing that lets the screen and the
        box disagree. `what` names the half being refused when one statement
        carries two numbers, the way `vlan_tag_error` does.
        """
        lo, hi = Pool.RANGES[name]
        label = f"{name} {what}".rstrip()
        if not is_number(v):
            return f"{label} '{v}': expected a number {lo}-{hi}"
        if not lo <= int(v) <= hi:
            return f"{label} {v}: expected {lo}-{hi}"
        return None

    #: The statements whose number also has to be a POWER OF TWO, and the
    #: reason this is a set of its own rather than another row in RANGES: a
    #: range asks whether a number lies between two others, and this asks what
    #: shape it has. Bending RANGES to carry it would make one table answer
    #: two different questions, and `check_range` would have to know which.
    #:
    #: `?` has said "ports per block, power of two" since block-size was
    #: written, and until #360 nothing in the VTY kept the second half of
    #: that: `block-size 100` was taken at the prompt, staged, written to the
    #: file, and refused by bpa_pool.c at commit time - after the operator had
    #: built a candidate that was probably going to change something else too.
    POW2 = frozenset(("block-size",))

    @staticmethod
    def check_pow2(name: str, v: str):
        """
        None if `v` is a power of two, otherwise why not and what is nearest.

        Named after the STATEMENT for the same reason `check_range` is: the
        constraint belongs to the statement, and a caller that passes its own
        predicate is a second copy of a rule the help text already quotes.

        The two nearest powers are IN the refusal because "not a power of two"
        leaves an operator doing arithmetic at a prompt, and the numbers they
        need are the two the box would have accepted on either side.
        """
        if name not in Pool.POW2:
            # As loud as check_range's KeyError on an unknown statement. A
            # caller asking about a statement that does not carry this
            # constraint is a bug, and a quiet None would answer "fine".
            raise KeyError(name)
        if not is_number(v) or int(v) < 1:
            # check_range owns that refusal and runs first; saying it twice in
            # two spellings is how the two drift apart.
            return None
        n = int(v)
        if not n & (n - 1):
            return None
        lo = 1 << (n.bit_length() - 1)
        return (f"{name} {v} is not a power of two: "
                f"the nearest are {lo} and {lo << 1}")

    @staticmethod
    def check_number(name: str, v: str, what=""):
        """
        None if `v` is a number at all, otherwise why it is not.

        THE SHAPE, AND DELIBERATELY NOT THE RANGE, which is the whole reason
        this is not `check_range` with the two numbers left off. This function
        is reached from `_pool_line`, and `_pool_line` reads THREE things: a
        statement typed at the prompt, a saved file, and the running
        configuration read back out of the dataplane by `show bpa config`.

        #357 is what happens when a range is enforced there. A pool the
        dataplane had built with `block-size 4` - which it accepts, and which
        lab/detreserve.sh uses to keep a block cheap - came back unmodellable,
        so `running_config()` reported that committing would REMOVE it and
        every commit on that box was then refused. The operator could no
        longer configure anything at all.

        "Is this a number" does not have that failure mode, and that is
        measured rather than assumed: every field this check guards is
        rendered `%u` by `show bpa config` in `bpa_cli.c` - block-size and
        ports at lines 2929 and 2931, max-blocks, block-stride, reserve, vrf,
        outside-vrf and utilisation-alarm at 2954 to 2972, and the timeouts
        and the session limits beside them. A `%u` cannot print a word, so
        the readback cannot reach this refusal. The request is bounded where
        the request is made, in Shell.pool_range.

        Without this, seven `int()` calls below raised a ValueError that
        nothing caught, and `prismash --load` ended in a traceback - on a file
        `prisma-routing.service` replays at every boot, with no line number and
        no statement named. That is #94's shape with the diagnosis removed.
        """
        if is_number(v):
            return None
        label = f"{name} {what}".rstrip()
        # No range in the sentence, for the reason above: quoting one here
        # would be quoting a bound this parser does not enforce, and an
        # operator reading a file back off their own box would be told the
        # number it printed is out of range.
        return f"{label} '{v}': expected a number"

    #: The statements carrying TWO numbers whose order is part of the
    #: statement, and a third table for the same reason POW2 is a second one:
    #: a range asks where one number lies and this asks how two of them stand
    #: to each other. `ports 9000 100` is two perfectly legal port numbers
    #: written the wrong way round, so RANGES has nothing to say about it and
    #: a refusal borrowed from RANGES would send the operator to check two
    #: numbers that are both fine.
    ORDERED = frozenset(("ports",))

    @staticmethod
    def check_order(name: str, first: str, second: str,
                    lo_word="min", hi_word="max"):
        """
        None if `first` <= `second`, otherwise why not, said as an ordering.

        Named after the STATEMENT like check_range and check_pow2, and for the
        same reason: the constraint belongs to the statement, and a caller
        that passes its own predicate is a second copy of a rule the help text
        already quotes.

        The words for the two halves come from the hint the operator has just
        read - `<min> <max>` - because "9000 is greater than 100" names
        arithmetic and not the mistake, which is that the two were typed in
        the wrong order.
        """
        if name not in Pool.ORDERED:
            # As loud as check_range's KeyError on an unknown statement.
            raise KeyError(name)
        if not is_number(first) or not is_number(second):
            # check_range owns that refusal and runs first; saying it twice in
            # two spellings is how the two drift apart.
            return None
        if int(first) <= int(second):
            return None
        return f"{name} {first} {second}: the {hi_word} is below the {lo_word}"

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
        # The first allocation, in ports (#462): what a new subscriber of a
        # dynamic pool is given at once, where max_blocks is the most it may
        # grow to. 0 is one block, every configuration written before this.
        # Ports and not blocks because the maintainer asked for it in ports -
        # "a first allocation of 512, then steps of 256" - and the plugin's
        # own `initial-blocks` is derived from it and read back into it.
        self.initial_ports = 0
        # Blocks between the ones a subscriber holds. 0 is the device's own
        # behaviour and every configuration written before this existed:
        # an extra block goes wherever the pool has one free.
        self.block_stride = 0
        # Blocks per public address held out of the deterministic formula, so
        # a subscriber that fills its block can be given another. Zero is
        # every configuration written before this existed.
        #
        # NOT an in-place setting, unlike max_blocks and block_stride beside
        # it: a reserve is taken out of capacity, and capacity is the mapping.
        # It is rendered by lines() outside the `in_place` guard for that
        # reason, which is what puts it in pool_replace and sends it through
        # the re-mapping warning at commit (issue #23).
        self.block_reserve = 0
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
        # Deliberately not behind `in_place`. A reserve reduces capacity,
        # which is the divisor under the blocked policy and the ceiling under
        # every policy - so changing it re-maps subscribers and the pool has
        # to be rebuilt, exactly like a prefix or a block size. Classifying it
        # as in-place would send it through bpa_pool_settings_set, which
        # cannot change a mapping, and the reserve would silently never
        # arrive.
        if self.block_reserve:
            out.append(f" reserve {self.block_reserve}")
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
            if self.initial_ports:
                out.append(f" initial-allocation {self.initial_ports}")
            if self.block_stride:
                out.append(f" block-stride {self.block_stride}")
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
        if self.block_reserve:
            parts += ["reserve", str(self.block_reserve)]
        if self.vrf != "":
            parts += ["vrf", self.vrf]
        if self.outside_vrf != "":
            parts += ["outside-vrf", self.outside_vrf]
        for t in self.TIMEOUTS:
            if t in self.timeouts:
                parts += ["timeout", t, str(self.timeouts[t])]
        if self.max_blocks > 1:
            parts += ["max-blocks", str(self.max_blocks)]
        if self.initial_blocks() > 1:
            parts += ["initial-blocks", str(self.initial_blocks())]
        if self.block_stride:
            parts += ["block-stride", str(self.block_stride)]
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
            "initial_blocks": self.initial_blocks(),
            "block_stride": int(self.block_stride),
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
            "initial_blocks": self.initial_blocks(),
            "block_stride": int(self.block_stride),
            # On the creation message only. bpa_pool_settings_set has no such
            # field, because everything on that message is safe to change on a
            # pool that is carrying traffic and this is not.
            "block_reserve": int(self.block_reserve),
            "tcp_mss": 0 if self.tcp_mss in ("", "auto") else int(self.tcp_mss),
            "tcp_mss_auto": self.tcp_mss == "auto",
            "alg": alg,
            "alg_set": alg_set,
            "timeouts": timeouts,
            "n_inside": len(self.inside),
            "n_prefixes": len(self.inside) + len(self.outside),
            "prefixes": list(self.inside) + list(self.outside),
        }

    def initial_blocks(self) -> int:
        """
        The first allocation in the plugin's unit, blocks (#462): what
        `initial-blocks` and the API's initial_blocks carry. 0 when there is
        none, and when the ports are not a whole number of blocks -
        initial_fits() refuses that at commit, and nothing else should turn
        it into a number of blocks the operator did not ask for.
        """
        if not self.initial_ports or not is_number(str(self.block_size)):
            return 0
        size = int(self.block_size)
        if size == 0 or self.initial_ports % size:
            return 0
        return self.initial_ports // size

    def initial_fits(self):
        """
        Why this pool's first allocation cannot be what it says, or None.

        A method on the finished pool, for block_fits()'s reason: it reads
        `block-size`, `max-blocks` and `mode`, each of which may be typed
        after it. The same two refusals the dataplane makes in
        bpa_pool_initial_refusal() - dynamic pools only, and not above the
        cap - plus the one only ports can get wrong: a number of ports that is
        not a whole number of blocks.
        """
        if not self.initial_ports:
            return None
        if self.mode != "dynamic":
            return ("initial-allocation applies to dynamic pools only; a "
                    "deterministic subscriber's first block is the formula's")
        if not is_number(str(self.block_size)) or not int(self.block_size):
            return None
        size = int(self.block_size)
        if self.initial_ports % size:
            return (f"initial-allocation is a whole number of {size}-port "
                    f"blocks, not {self.initial_ports}")
        cap = max(int(self.max_blocks), 1)
        if self.initial_ports // size > cap:
            return (f"initial-allocation is above the {cap * size} ports "
                    f"max-blocks {cap} allows: {self.initial_ports}")
        return None

    def block_fits(self):
        """
        Why this pool has no room for a single block, or None if it has.

        THE CHECK THAT NEEDS TWO STATEMENTS, and the reason it is a method on
        the finished pool rather than a guard beside either of them: an
        operator may type `block-size` after `ports`, or `ports` after
        `block-size`, and both are legal editing orders. A guard at either
        line would read the other statement in whatever state the pool
        happens to be halfway through being written, and would refuse a pool
        that is about to be correct. So it is asked once, of the whole pool,
        by the commit check - which is where the pool is finished.

        A pool with no `ports` statement is skipped rather than measured
        against the device default: 1024-65535 is 64512 ports and holds every
        block size `?` offers, so there is nothing there to refuse.

        A range this cannot read is skipped too, and that is the #357 rule
        applied to a check rather than to a reader: the numbers may have come
        out of a file somebody edited by hand, and a check that raised on them
        would turn a pool the VTY merely disagrees with into a box that cannot
        commit at all. The dataplane refuses those, and names them.
        """
        parts = self.ports.replace("-", " ").split()
        if len(parts) != 2 or not all(is_number(p) for p in parts):
            return None
        lo, hi = int(parts[0]), int(parts[1])
        if lo > hi:
            # check_order owns that one and says it in the operator's words.
            return None
        count = hi - lo + 1
        size = int(self.block_size)
        if size <= count:
            return None
        # bpa_pool.c's own clause, near enough to recognise: "block size %u
        # exceeds the %u usable ports". An operator who meets this from the
        # VTY on one box and from the plugin on the next reads one sentence.
        return f"block-size {size} exceeds the {count} usable ports"

    def stride_inert(self) -> bool:
        """
        Whether this pool carries a `block-stride` no allocator will read.

        A stride steps along the MAPPED blocks of a public address, which is
        the walk a dynamic pool's extension makes. A deterministic pool's
        extension comes out of the reserve, an index space of its own, so the
        setting is never read there - at any worker count and on any box -
        and the dataplane refuses the pair in bpa_pool_init() and in
        bpa_pool_settings_set() (#375).

        THE ONE PYTHON OWNER of that rule. The commit check refuses on it and
        `--load` drops on it, and they have to agree on which pools they mean:
        a pool the check would refuse at the prompt and the boot would then
        apply, or the other way round, is a file `save` writes and `--load`
        cannot take. Two copies of the test is how they stop agreeing.
        """
        return bool(self.block_stride) and self.mode != "dynamic"

    def __eq__(self, other):
        return isinstance(other, Pool) and self.lines() == other.lines()


def mac_error(value: str, request: bool = True):
    """
    None if `value` is a MAC an interface can answer to, else why not.

    Rows, like `prefix_error`, and joined by `mac_why` for the file.

    THREE RULES, and none of them is pedantry:

      * six octets of hexadecimal. The whole check until #414 was that the
        word had six colon-separated parts, so `zz:zz:zz:zz:zz:zz` and
        `1:2:3:4:5:6789` were staged, saved and handed to the dataplane, which
        answers a bare retval - which is why `linux-name` is already refused in
        this parser rather than at commit.
      * not `ff:ff:ff:ff:ff:ff`. An interface that answers to the broadcast
        address answers to every frame on the segment.
      * the GROUP BIT clear - the low bit of the first octet. A multicast
        source address is not a unicast interface identity, and a switch will
        not learn it, so the box goes unreachable through a port that is up.

    NOT a fourth rule: the locally-administered bit is neither required nor
    encouraged. An operator matching a peer's filter or a licence may need a
    globally-unique address, and refusing one would refuse a real
    configuration. Written down so the next reader does not add it as the
    obvious next rule.

    ONE TO TWO digits per octet, not two, because VPP's own
    `unformat_ethernet_address` takes `1:2:3:4:5:6` and this VTY refusing what
    `vppctl` accepts is a disagreement with the box. `mac_normal` is what
    makes that safe - see it for the diff this used to produce.
    """
    if not value:
        return ("mac-address: expected an address", "")
    parts = value.split(":")
    if len(parts) != 6:
        return (f"mac-address '{value}': expected six octets",
                "as in 02:00:00:00:00:01")
    for octet in parts:
        if not 1 <= len(octet) <= 2 or any(
                c not in "0123456789abcdefABCDEF" for c in octet):
            return (f"mac-address '{value}': '{octet}' is not a hex octet",
                    "each octet is one or two hexadecimal digits")
    if not request:
        # A FACT, and the shape is all that can be asked of it. `device_macs`
        # reads l2_address out of sw_interface_dump, which is six bytes and
        # carries no opinion about what they are - so a box whose port really
        # holds a multicast MAC would have `save` write it and `--load` refuse
        # the whole file, and the box would come up with no configuration at
        # all. That is the #357 rule and it is why the two rules below are the
        # prompt's (#419).
        return None
    if all(int(o, 16) == 0xff for o in parts):
        return ("mac-address ff:ff:ff:ff:ff:ff: that is the broadcast address",
                "an interface answering to it answers to every frame on the "
                "segment")
    if int(parts[0], 16) & 1:
        return (f"mac-address '{value}': the group bit is set",
                "the low bit of the first octet marks a multicast address, "
                "which no switch will learn as this port's")
    return None


def mac_why(refusal) -> str:
    """`mac_error` as one sentence, for the file. See `vrrp_why`."""
    verdict, prose = refusal
    return f"{verdict} - {prose}" if prose else verdict


#: The AS numbers that are not an AS a box configures itself as, and what to
#: say about each. Data rather than branches, because the reason is the whole
#: value of the finding and a branch would leave it in a sentence nobody can
#: compare against the RFC.
#:
#: AS_TRANS is the only one. The private ranges are NOT here, and that is the
#: decision: 64512-65534 and 4200000000-4294967294 (RFC 6996) are an ordinary
#: thing for an ISP to run internally, and a checker that warns about what is
#: merely unusual is one an operator learns to work around.
AS_NOTABLE = {
    23456: ("AS_TRANS", "RFC 6793 reserves it for a 2-byte speaker to stand "
                        "in with; it is not an AS to be"),
}

#: 1 to 2^32-1. Zero is reserved (RFC 7607) and 4294967295 is the last one
#: RFC 7300 reserves, but both are refused here for the plainer reason that
#: neither is an AS anybody is assigned - and the hint beside `remote-as` has
#: said `<1-4294967295>` since it was written while nothing kept it.
ASN_MIN, ASN_MAX = 1, 4294967295


def asn_error(what: str, value: str):
    """
    None if `value` is an AS number, else why not, in rows.

    REFUSED is everything that is not a number and everything outside
    1-4294967295: neither is an AS under any reading, and until #415 nothing
    in this VTY asked. `router bgp 0`, `router bgp 4294967296` and
    `router bgp banana` were all staged, and FRR refused them at commit - one
    step after the operator had built a candidate that probably changes
    something else too. That is the argument #351, #360 and #361 already won
    for the pool: a bound enforced only by the thing downstream is a bound the
    operator meets one commit late.

    SAID is AS_TRANS, through `asn_note`, because refusing it would refuse a
    lab somebody built deliberately.

    `internal`, `external` and `auto` are not handled here: they are real FRR
    spellings of `remote-as` and the caller keeps them, because this function
    answers about a NUMBER and those are not one.
    """
    if not value:
        return (f"{what}: expected an AS number", "")
    if not is_number(value):
        return (f"{what} '{value}': expected an AS number",
                f"an AS number is {ASN_MIN} to {ASN_MAX}")
    n = int(value)
    if not ASN_MIN <= n <= ASN_MAX:
        return (f"{what} {value}: out of range",
                f"an AS number is {ASN_MIN} to {ASN_MAX}")
    return None


def asn_why(refusal) -> str:
    """`asn_error` as one sentence, for the file. See `vrrp_why`."""
    verdict, prose = refusal
    return f"{verdict} - {prose}" if prose else verdict


def mac_normal(value: str) -> str:
    """
    A MAC as the dataplane writes it: two lowercase digits per octet.

    MEASURED, not tidiness. `sw_interface_dump` gives `01:02:03:04:05:06` and
    the VTY stored whatever was typed, so `mac-address 1:2:3:4:5:6` left the
    candidate and the running configuration permanently unequal: `delta_to`
    reported `mac_set` on every commit, the box re-applied the MAC forever and
    `show configuration compare` was never clean. Normalising here is what
    makes accepting the short spelling safe.
    """
    return ":".join(f"{int(o, 16):02x}" for o in value.split(":"))

def asn_note(value: str):
    """
    What is worth saying about an AS number that is legal, or None.

    A FINDING and never a refusal - see AS_NOTABLE for why the private ranges
    produce nothing at all.
    """
    if not is_number(value):
        return None
    entry = AS_NOTABLE.get(int(value))
    if not entry:
        return None
    name, why = entry
    return f"AS {value} is {name}: {why}"


#: How many BYTES each name and each piece of free text may carry, and where
#: the number comes from. The source is in the table because a bound with no
#: source is a number somebody chose, and the next person cannot tell one from
#: the other.
#:
#: BYTES and not characters: every one of these travels in a fixed-size field
#: of the binary API, and a letter carrying an accent is one character and two
#: bytes in UTF-8. A limit counted in characters would let a value past the
#: field it has to fit in, which is the whole defect - and the operators this
#: box is sold to write accented text, so it is the ordinary case rather than
#: an exotic one.
#:
#: `test_prismash_config.py` reads `src/plugins/bpa/bpa.api` and compares the
#: three rows that come from it, so this table cannot drift from the messages
#: it describes. The other two are cited rather than read: VPP's own
#: `interface.api` is not in this repository, and `useradd(8)` is a manual
#: page. Both are named so the next reader can check them in one command.
#: THE USABLE MAXIMUM, which is not the field's width.
#:
#: `string name[64]` holds 64 BYTES, and a C string spends one of them on the
#: terminator. The dataplane was asked, one length at a time, through
#: `vat2 bpa_pool_add_del` on a running box (#423):
#:
#:    63 characters   retval 0, and `show bpa pool` reads all 63 back
#:    64 characters   retval -7
#:    65, 100, 200    retval -7
#:
#: It REFUSES; it does not truncate and it does not crash, which is the
#: question #416 left open and the reason `vppapi.unsendable()` needs no
#: length guard. What it also means is that a bound written as the field's
#: width lets exactly one value through the prompt that the commit then
#: refuses - and a refusal that arrives at the commit rather than at the
#: prompt is the thing the candidate model exists to avoid.
#:
#: Only the pool name is measured. The other entries keep the field width,
#: because a limit lowered on the argument that C strings have terminators is
#: a limit lowered on a guess, and this table is read by the refusal an
#: operator sees.
TEXT_LIMITS = {
    "cgnat pool": (63, "bpa.api, bpa_pool_add_del name[64], which holds 63 "
                       "characters and a terminator"),
    #: 63, and VPP is the one that decided it (#424). Asked through
    #: `vat2 sw_interface_tag_add_del` and read back with
    #: `sw_interface_dump`: 63, 64, 65 and 100 characters all answer
    #: `retval: 0`, and every one of them reads back as 63. It TRUNCATES,
    #: silently, where the pool name refuses - so a 64-character
    #: description was accepted by every screen and kept one character
    #: shorter, and a saved file is written from the readback (#419).
    "description": (63, "VPP's vnet/interface.api, "
                        "sw_interface_tag_add_del tag[64], which keeps 63 "
                        "characters and truncates the rest"),
    #: 256, and this is the one bound that is NOT what the box enforces
    #: (#424, measured). Handed a 257-character path directly through the
    #: API, the dataplane opens the file and reads its entry: `string
    #: path[256]` is what bpa.api DECLARES, and VPP's encoding carries more
    #: than that without promising to. So the limit is the declared field,
    #: the refusal arrives at the prompt where an operator can act on it,
    #: and neither claims the box would fail - going past a declared width
    #: is relying on behaviour nothing documents.
    #: A static route's description is not held by FRR at all - `ip route
    #: ... description x` is `% Unknown command` on 10.7.1 - so it lives in
    #: this box's own file and the bound is this box's choice (#436). The same
    #: 63 an interface description has, so one habit fits both.
    "static route description": (63, "this box's own file; kept to what an "
                                     "interface description holds"),
    "cgnat identity file": (256, "bpa.api, bpa_identity_set path[256]"),
    "cgnat identity dhcp-leases": (256, "bpa.api, bpa_identity_set path[256]"),
    #: 127, measured with a packet (#425). A secret cannot be read back -
    #: the box does not print one, correctly - so it was asked the only way
    #: it can be: RFC 2866 signs the Accounting-Request with the secret, so
    #: a NAS that is answered has proved which secret the box holds.
    #: Configured with 128 characters through BOTH doors, the box answers a
    #: NAS signing with the first 127 and not one signing with all 128 - the
    #: same terminator `name[64]` spends, one field wider.
    "cgnat identity radius secret": (
        127, "bpa.api, bpa_identity_radius_set secret[128], which holds 127 "
             "characters and a terminator"),
    "user": (32, "useradd(8): usernames may only be up to 32 characters long"),
}


def text_error(what: str, value: str, request: bool = True):
    """
    None if `value` fits the field it travels in and carries no control
    characters, else why not, in rows.

    TWO QUESTIONS, and they are here together because they are the same
    statement's - a name or a piece of free text an operator types.

    THE LENGTH comes from `TEXT_LIMITS`, never from a number written beside a
    handler. Nothing bounded any of these: a 200-character pool name and a
    4000-character description were staged, saved and handed to fields of 64
    bytes. What the dataplane then does with them is NOT measured here - it
    needs a running one, and #416 names the lab.

    CONTROL CHARACTERS are refused rather than stripped. Stripped is how the
    two doors came to disagree: the prompt silently removed a BEL from a
    description and a saved file kept it, so one statement meant two things
    depending on how it arrived. And a description is read back to an operator
    by `show configuration` on the terminal they are sitting at, so what goes
    in comes out at a console.

    Accented text is NOT a control character and is not refused. The wiki is
    in Portuguese and an operator names a port in their own language; the rule
    is about what a terminal executes, not about what alphabet it is in.
    """
    if what not in TEXT_LIMITS:
        # As loud as check_range's KeyError. A caller asking about text this
        # table does not bound is a bug, and a quiet None would answer "fine".
        raise KeyError(what)
    limit, source = TEXT_LIMITS[what]
    # THE CONTROL CHARACTER IS THE PROMPT'S RULE. An interface tag is six-four
    # bytes of whatever a byte can be, and `device_descriptions` reports what
    # the box holds - so refusing one in the parser means `save` writing a
    # file that `--load` refuses whole, and a box with no configuration (#419).
    # The LENGTH stays at both doors: the field cannot hold more than its own
    # width, so no readback can exceed it.
    for ch in (value if request else ""):
        if unicodedata.category(ch) in ("Cc", "Cf", "Cs"):
            return (f"{what}: a control character is not text",
                    f"{ch!r} is read back to a console by "
                    f"'show configuration'")
    size = len(value.encode("utf-8"))
    if size > limit:
        return (f"{what}: {size} bytes, and the limit is {limit}",
                f"the field it travels in is {source}")
    return None


def text_why(refusal) -> str:
    """`text_error` as one sentence, for the file. See `vrrp_why`."""
    verdict, prose = refusal
    return f"{verdict} - {prose}" if prose else verdict



#: The statements whose two doors DELIBERATELY give different answers, and
#: why. Declared HERE, in the product, rather than as an allowlist inside the
#: check that reads it - for the reason `NOT_LABS` holds its reasons as data:
#: an exception nobody can read is one nobody re-examines.
#:
#: There is one entry and it is a whole class. `Config._pool_line` is the
#: reader for THREE things - a statement typed at the prompt, a saved file,
#: and the running configuration read back out of the dataplane by `show bpa
#: config` - and the third is a FACT rather than a request. #357 put a range
#: there and a pool the dataplane had built with `block-size 4` came back
#: unmodellable, so every commit on that box was refused.
#:
#: So a pool's NUMBERS are bounded where the request is made, in
#: Shell.pool_range, and not in the parser. The SHAPE is checked in both
#: (#412), because "is this a number" is a question the dataplane's own %u
#: output always answers yes to.
DOORS_DIFFER = {
    "pool numbers": (
        "pool", tuple(sorted(Pool.RANGES)),
        "the file parser also reads `show bpa config`, where a number outside "
        "the range the prompt enforces is a fact about a running box rather "
        "than a request (#357). The shape is checked at both doors; the range "
        "is not."),
    "interface mtu and vrf": (
        "interface", ("mtu", "vrf"),
        "a saved file is WRITTEN FROM the readback - `running_config` fills "
        "these from `device_mtus()` and `device_vrfs()` - so a value outside "
        "the range the prompt offers, on a box that really has one, would be "
        "written by `save` and refuse the whole file at the next boot. #417 "
        "bounded them here and #419 took the bound back out."),
    "interface mac-address": (
        "interface", ("mac-address",),
        "the SHAPE is checked at both doors and the two policy rules - not "
        "the broadcast address, and the group bit clear - are the prompt's. "
        "`device_macs` reads six bytes out of sw_interface_dump and carries "
        "no opinion about what they are, so a port that really holds a "
        "multicast MAC must still round-trip through `save` (#419)."),
    "interface description": (
        "interface", ("description",),
        "the LENGTH is checked at both doors, because the field cannot hold "
        "more than its own width. The control-character rule is the prompt's: "
        "an interface tag is 64 bytes of whatever a byte can be, and refusing "
        "one in the parser would refuse a file the box itself wrote (#419)."),
}



def prefix_error(what: str, value: str):
    """
    None if `value` is an IPv4 network this dataplane can hold, else why not.

    A TUPLE of rows, the way `vrrp_statement` answers and for the same reason
    (#355): the prompt prints the first through `err` and the rest as prose,
    and the file parser joins them with `prefix_why`, because an entry in
    `unknown` is one line by construction. Composed as one sentence, the IPv6
    refusal reaches a console at 132 columns.

    Four questions, and every one of them is `bpa_cli.c`'s rather than this
    file's opinion:

      * IPv4. All four statements that reach here - `inside`, `outside`,
        `bypass source`, `bypass destination` - are `unformat_ip4_address` in
        the plugin, and the datapath nodes are `ip4`. An IPv6 prefix is a
        statement the dataplane has no way to hold.
      * A prefix length, and one of at most 32. The plugin refuses `len > 32`
        with "inside prefix length %u invalid", one commit later than here.
      * NO HOST BITS. This is the one that is a decision, and the refusal is
        safe for the reason that decides every refusal in this parser: the
        dataplane MASKS them on the way in - `base & ~(count - 1)` in
        bpa_cli.c - and `show bpa config` renders from that masked base, so
        the readback can only ever produce a network address and can never
        reach this refusal. That is the #357 hazard, answered by measurement.

        Refused rather than masked here, because masking is a configuration
        whose text does not round-trip: the operator types 100.64.0.5/10,
        `save` writes 100.64.0.0/10, and the file is not the file. The network
        they meant is in the prose, so the fix is a copy and paste.
      * It parses at all. `inside potato` was staged, saved and committed.
    """
    if not value:
        return (f"{what}: expected a prefix", "")
    if "/" not in value:
        return (f"{what} '{value}': expected a prefix", "a prefix carries its "
                "length, as in 100.64.0.0/10")
    if ":" in value:
        return (f"{what} '{value}': expected an IPv4 prefix",
                "this CGNAT translates IPv4, and the dataplane has no way to "
                "hold an IPv6 one")
    try:
        ipaddress.IPv4Network(value, strict=True)
    except ipaddress.AddressValueError:
        return (f"{what} '{value}': not an IPv4 prefix", "")
    except ipaddress.NetmaskValueError:
        return (f"{what} '{value}': not a prefix length", "a length is 1 to 32")
    except ValueError:
        # strict=True refuses exactly one thing the two above do not: host
        # bits. Naming the network the operator meant is the whole value of
        # saying it here rather than letting the dataplane mask it in silence.
        try:
            meant = ipaddress.IPv4Interface(value).network
        except ValueError:
            return (f"{what} '{value}': not an IPv4 prefix", "")
        return (f"{what} '{value}': host bits are set",
                f"the prefix this names is {meant}")
    # A /0 IS EVERY ADDRESS (#473). The dataplane cannot hold one - a range's
    # count is a u32, and 1u << 32 is undefined: before #473 a /0 bypass
    # reached it as the single address 0.0.0.0 - and every address is no
    # pool and no bypass either.
    if ipaddress.IPv4Network(value).prefixlen == 0:
        return (f"{what} '{value}': a /0 is every address",
                "a range here holds at most half the space, and a box meant "
                "to translate nothing has no 'cgnat inside'")
    return None


def prefix_why(refusal) -> str:
    """`prefix_error` as one sentence, for the file. See `vrrp_why`."""
    verdict, prose = refusal
    return f"{verdict} - {prose}" if prose else verdict


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
    # AND IT HAS TO PARSE, which nothing asked until #413: `999.1.1.1/24` and
    # `2001:zz::1/64` were staged, saved and handed to the dataplane.
    #
    # ip_interface and not ip_network, and that is the decision this statement
    # turns on: on an interface the host bits ARE the address, so `10.0.0.5/8`
    # is the ordinary way a router is configured and refusing it would refuse
    # every real configuration. A pool prefix is the opposite thing and
    # prefix_error() above says so.
    try:
        ipaddress.ip_interface(prefix)
    except ValueError:
        return f"'{prefix}' is not an address with a prefix length"
    return None


VRRP_USAGE = ("vrrp <vrid> address <a.b.c.d> | priority <n> | "
              "track <interface> decrement <n>")

#: The same thing as a refusal, split so that it FITS: the whole grammar on
#: one row reaches a console at 92 columns and folds inside the words (#355).
#: The verdict carries the statement an operator most often means, the prose
#: carries the other two.
VRRP_USAGE_REFUSAL = (
    "usage: vrrp <vrid> address <a.b.c.d>",
    "or 'vrrp <vrid> priority <n>', or 'vrrp <vrid> track <interface> "
    "decrement <n>'")


def vrrp_statement(vrrp: dict, iface: str, words: list):
    """
    Apply one `vrrp ...` statement to a configuration's virtual routers.

    Returns None when it was taken and `(verdict, prose)` when it was not,
    and the caller decides what a refusal looks like - the VTY prints the
    verdict and folds the prose under it, the file parser files both under
    `unknown` with the line number. ONE implementation for both, because a
    statement a running box takes and a saved file refuses is a box that
    cannot reload its own configuration (#121 is that shape).

    Two parts and not one sentence, because a refusal has to FIT: a verdict
    long enough to carry the reason reaches the console at whatever width the
    operator's window happens to be and folds inside the words (#355). The
    verdict says what was refused; the prose says why.

    `no vrrp <vrid>` removes the whole virtual router and is handled here too:
    removing one address of two is a different statement, and an operator who
    means "this box stops standing for that service" means all of it.
    """
    if len(words) < 2:
        return VRRP_USAGE_REFUSAL
    try:
        vrid = int(words[1])
    except ValueError:
        return (f"vrrp '{words[1]}': the virtual router id is a number", "")
    if not VRID_MIN <= vrid <= VRID_MAX:
        return (f"vrrp {vrid}: the virtual router id is "
                f"{VRID_MIN} to {VRID_MAX}", "")
    vr = vrrp.get(iface, {}).get(vrid)
    rest = words[2:]

    # not a unit: a closure over this statement's interface and id, measured
    # through vrrp_statement, which is the only thing that can call it
    def make():
        return vrrp.setdefault(iface, {}).setdefault(
            vrid, {"addresses": [], "priority": VRRP_PRIORITY_DEFAULT,
                   "track": {}})

    if rest[:1] == ["address"] and len(rest) == 2:
        a = rest[1]
        if ":" in a:
            return ("vrrp: this box takes IPv4 virtual routers only",
                    "an IPv6 virtual router is RFC 5798 section 5.2.2 and a "
                    "second election to test. It is not refused here because "
                    "it cannot work, but because nothing has measured it")
        if "/" in a:
            return (f"vrrp {vrid} address '{a}': write it without a prefix "
                    "length",
                    "the virtual address is one of the addresses on this "
                    "interface's subnet, not a subnet of its own")
        if not _is_ipv4_address(a):
            return (f"vrrp {vrid} address '{a}': not an IPv4 address", "")
        entry = make()
        if a not in entry["addresses"]:
            entry["addresses"].append(a)
        return None
    if rest[:1] == ["priority"] and len(rest) == 2:
        try:
            p = int(rest[1])
        except ValueError:
            return (f"vrrp {vrid} priority '{rest[1]}': a number", "")
        if p == 255:
            return ("vrrp: priority 255 is RFC 5798's address owner",
                    "a box that shares a service address with a peer is not "
                    "one. It would win every election and never hand the "
                    "address back")
        if not VRRP_PRIORITY_MIN <= p <= VRRP_PRIORITY_MAX:
            return (f"vrrp {vrid} priority {p}: the range is "
                    f"{VRRP_PRIORITY_MIN} to {VRRP_PRIORITY_MAX}", "")
        make()["priority"] = p
        return None
    if rest[:1] == ["track"] and len(rest) == 4 and rest[2] == "decrement":
        try:
            d = int(rest[3])
        except ValueError:
            return (f"vrrp {vrid} track: decrement '{rest[3]}': a number",
                    "")
        if not VRRP_DECREMENT_MIN <= d <= VRRP_DECREMENT_MAX:
            return (f"vrrp {vrid} track: decrement {d}: the range is "
                    f"{VRRP_DECREMENT_MIN} to {VRRP_DECREMENT_MAX}", "")
        if rest[1] == iface:
            return (f"vrrp {vrid} track {rest[1]}: an interface cannot "
                    "track itself",
                    "a virtual router on an interface that went down has "
                    "already stopped advertising. What is worth tracking is "
                    "the OTHER side - the uplink whose loss leaves this box "
                    "alive and useless")
        make()["track"][rest[1]] = d
        return None
    return VRRP_USAGE_REFUSAL


def vrrp_remove(vrrp: dict, iface: str, words: list):
    """`no vrrp <vrid>`: the whole virtual router, or `(verdict, prose)`."""
    if len(words) != 2:
        return ("usage: no vrrp <vrid>", "")
    try:
        vrid = int(words[1])
    except ValueError:
        return (f"no vrrp '{words[1]}': the virtual router id is a number",
                "")
    if vrid not in vrrp.get(iface, {}):
        return (f"{iface} has no virtual router {vrid}", "")
    del vrrp[iface][vrid]
    if not vrrp[iface]:
        del vrrp[iface]
    return None


def vrrp_pair_findings(mine: dict, theirs: dict) -> list:
    """
    What two boxes' virtual routers say about each other, as sentences.

    Keyed by (virtual router id, virtual address) and NOT by interface name:
    the two boxes stand for the same address under the same id, and what that
    address is called locally is each box's own business - `ens18` here can
    be `ens19` there and the pair is still correct.

    Two findings, and both are configuration mistakes with no symptom until
    the day it matters:

    * **the same priority on both boxes.** RFC 5798 breaks the tie by the
      higher source address, so the pair works and which box holds the service
      is decided by something the operator never wrote down. The first
      failover lands wherever the addressing happens to put it.
    * **an id one box stands for and the other does not.** There is no
      standby for that address at all: the box holding it is alone, and the
      screen that was supposed to say so says nothing, because a comparison
      keyed by interface name would not have looked.

    A different ADDRESS under the same id is the second finding twice over -
    each box is alone for its own address - and comes out as two lines, which
    is what it is.
    """
    # not a unit: a closure over nothing, measured through the findings it
    # produces - there is no answer it can give that the four cases in
    # test_prismash_config.py do not read
    def by_key(vrrp):
        out = {}
        for ifname in vrrp:
            for vrid, vr in vrrp[ifname].items():
                for addr in vr["addresses"]:
                    out[(vrid, addr)] = (ifname, vr["priority"])
        return out

    here, there = by_key(mine), by_key(theirs)
    findings = []
    for key in sorted(set(here) | set(there)):
        vrid, addr = key
        if key in here and key not in there:
            findings.append(
                f"vrrp {vrid} stands for {addr} on this box and on no "
                "interface of the peer's, so nothing takes that address over")
        elif key in there and key not in here:
            findings.append(
                f"vrrp {vrid} stands for {addr} on the peer and on no "
                "interface of this box, so nothing takes that address over")
        elif here[key][1] == there[key][1]:
            findings.append(
                f"vrrp {vrid} for {addr} is priority {here[key][1]} on both "
                "boxes: RFC 5798 breaks that tie by the higher address, so "
                "which box holds the service is decided by the addressing "
                "and not by the configuration")
    return findings


def vrrp_why(refusal) -> str:
    """
    A refusal as ONE sentence, for the reader that has no console to fold in.

    `prismash --load` files this under `unknown` with the line number, and a
    file is read by a person looking at a log rather than at a prompt.
    """
    verdict, prose = refusal
    return f"{verdict} - {prose}" if prose else verdict


def _is_ipv4_address(a: str) -> bool:
    """A dotted quad, each octet in range. No prefix length, no name."""
    parts = a.split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not is_number(p) or not 0 <= int(p) <= 255 or (
                len(p) > 1 and p[0] == "0"):
            return False
    return True


#: The two ends of a VLAN tag, and the only place they are written down.
#:
#: 802.1Q numbers a VLAN 1 to 4094. Zero is the priority tag - a frame with a
#: tag header and no VLAN in it - and 4095 is reserved; no switch on the far
#: end will trunk either, so a sub-interface built on one carries nothing and
#: looks configured.
#:
#: Here rather than in the VTY because THREE places need the same two numbers:
#: the `?` hint an operator reads before typing, the VTY's refusal when they
#: type one outside it, and the refusal `Config.parse` writes into `unknown`
#: for a saved file that carries one. Written out separately in each, they
#: drift, and the way they drift is the one measured in #312: the hint said
#: `<1-4094>` from the day it was written and nothing anywhere compared a
#: typed tag against it, so `vlan 0`, `vlan 4095` and `vlan 4096` were all
#: staged in silence (#350).
VLAN_MIN, VLAN_MAX = 1, 4094
VLAN_RANGE = f"<{VLAN_MIN}-{VLAN_MAX}>"

#: The same arrangement for the two other interface numbers, and for the same
#: reason VLAN_RANGE exists: 576-9216 was written out THREE times in
#: prismash.py - in the hint, in the usage line and in the refusal - and the
#: saved file was bounded by none of them (#417). A range written more than
#: once is a range that can disagree with itself.
#:
#: 576 is the smallest IPv4 datagram every host must accept (RFC 791); 9216 is
#: what this product offers as a jumbo frame.
MTU_MIN, MTU_MAX = 576, 9216
MTU_RANGE = f"<{MTU_MIN}-{MTU_MAX}>"

#: An interface's VRF. 0 is not in the range because on an INTERFACE that word
#: is how the statement is taken back off, which is the split Pool.RANGES'
#: comment on `vrf` describes: the same word with the same number means a real
#: table on a pool and "remove this" here.
IFACE_VRF_MIN, IFACE_VRF_MAX = 1, 4294967295
IFACE_VRF_RANGE = f"<{IFACE_VRF_MIN}-{IFACE_VRF_MAX}>"


def vlan_tag_error(word: str, what: str = "vlan"):
    """
    None when `word` is a VLAN tag, a sentence when it is not.

    `what` names the half being refused, because a sub-interface carries two
    tags and "vlan 4095" and "inner 4095" are different mistakes to have made
    - the outer one is the tag the sub-interface is NAMED after and the inner
    one is a Q-in-Q customer tag, and an operator reading a refusal has to
    know which of the two numbers on the line it is about.
    """
    if not is_number(word):
        return f"{what} '{word}': expected a VLAN tag {VLAN_RANGE}"
    # ONE ROW, and short. `err` prints what it is given and wraps nothing
    # (#252), and a racked box is read at 80 columns - so the reason why 0 and
    # 4095 are not tags belongs in `?` and in the wiki, not here. What this
    # row owes the operator is the number they typed and the two ends.
    if not VLAN_MIN <= int(word) <= VLAN_MAX:
        return (f"{what} {word} out of range ({VLAN_MIN}-{VLAN_MAX}): "
                "802.1Q reserves 0 and 4095")
    return None


#: The widest line this VTY prints to a console.
#:
#: A racked box is reached over a BMC or a serial console at 80 columns, and
#: that is the screen every message below is cut to. The budget stops one
#: column short so that a full line leaves the cursor inside the screen: a
#: terminal sitting exactly on the right margin decides for itself whether the
#: next character opens a new row, and a message whose shape depends on that
#: decision has not been measured, it has been guessed.
#:
#: It lives here, beside the marker whose sixteen characters were subtracted
#: from it, because more than one place spends the same budget: the marker,
#: the configuration display that appends it (#141), and the commit refusal
#: that quotes statements carrying it (#252). Written out separately in each
#: of them, the number drifts, and it drifts by one of them growing while
#: nobody re-measures the others - which is how the refusal came to print a
#: 178-column line under an 80-column rule the display had been keeping for
#: two releases.
CONSOLE_COLUMNS = 79

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
UNCHECKED_NOTE = "! prismash:"


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


#: The longest name Linux gives a netdev. IFNAMSIZ is 16 WITH the terminator,
#: so a name is at most 15 characters, and it never carries a '/'. THE owner
#: of the 15: the `linux-name` check at the prompt and the one in a saved
#: file both ask linux_ifname_error() below, and so does the default a
#: claimed card's pair gets (#389).
LINUX_IFNAME_MAX = 15

#: The longest interface name FRR takes in a configuration. FRR's own check
#: says it - "Maximum interface name length is 16 characters", measured on
#: FRR 10.7.1 refusing `interface GigabitEthernet0/13/0` from every daemon at
#: once (#389). One more than Linux allows, so a 16-character dataplane name
#: reaches FRR as a stanza and a 21-character one is refused by prismash before
#: FRR is handed it. THE owner of the 16: prismash's commit check asks here.
FRR_IFNAME_MAX = 16


def linux_ifname_error(name: str):
    """
    Why Linux cannot name a netdev `name`, or None when it can.

    One sentence for the prompt and for a file, because the two had their
    own copies of the 15 and the reason. VPP's own names are full of '/',
    and copying one across as a `linux-name` is the obvious mistake to make.
    """
    if not name or len(name) > LINUX_IFNAME_MAX or "/" in name:
        return (f"'{name}' cannot be a Linux interface name "
                f"({LINUX_IFNAME_MAX} characters, no '/')")
    return None


def _is_loopback(name: str) -> bool:
    """
    Is this VPP's spelling of a loopback?

    `loop0`, `loop1`. Matched rather than guessed at, because naming an
    interface is how one gets CREATED here, and a typo that happened to look
    like a loopback would silently make one instead of being refused as an
    interface the box does not have.
    """
    return (name.startswith("loop") and len(name) > 4
            and is_number(name[4:]))


#: LACP bundles (#455): `interface ae<n>`, Junos's name for an aggregated
#: Ethernet. The dataplane's bond is renamed to it, so the configuration,
#: `show interface` and the Linux end all say ae0.
BUNDLE_DEFAULTS = {"lacp": "active", "rate": "slow", "hash": "l34"}
BUNDLE_CHOICES = {"lacp": ("active", "passive"), "rate": ("fast", "slow"),
                  "hash": ("l2", "l23", "l34")}
BUNDLE_MAX = 64


def _is_bundle(name: str) -> bool:
    """Is `name` an LACP bundle, `ae<n>`? Matched for _is_loopback's reason."""
    return (name.startswith("ae") and len(name) > 2 and is_number(name[2:])
            and int(name[2:]) < BUNDLE_MAX)


def new_bundle() -> dict:
    """A bundle with no members yet and the defaults."""
    return {"members": [], **BUNDLE_DEFAULTS}


def bundle_statement(bundle: dict, w) -> str:
    """
    One statement of an `interface ae<n>` block into `bundle` (#455); why it
    cannot be taken, or "". The same reader for a file and for the prompt.
    """
    neg = w[:1] == ["no"]
    if neg:
        w = w[1:]
    if w[:1] == ["member"] and len(w) == 2:
        if neg:
            if w[1] in bundle["members"]:
                bundle["members"].remove(w[1])
        elif w[1] not in bundle["members"]:
            bundle["members"].append(w[1])
        return ""
    for key, word in (("lacp", ["lacp", "mode"]), ("rate", ["lacp", "rate"]),
                      ("hash", ["load-balance"])):
        if w[:len(word)] == word:
            if neg:
                bundle[key] = BUNDLE_DEFAULTS[key]
                return ""
            val = w[len(word):]
            if len(val) != 1 or val[0] not in BUNDLE_CHOICES[key]:
                return (f"expected: {' '.join(word)} "
                        f"<{'|'.join(BUNDLE_CHOICES[key])}>")
            bundle[key] = val[0]
            return ""
    return ""   # not a bundle statement: the caller reads it


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

    AND THE TAP'S SUB-INTERFACES, `tap4096.100`. `lcp-auto-subint` is on, so
    VPP creates one of those for every VLAN sub-interface an operator makes on
    a paired card - and this said no to it, because "4096.100" is not a run of
    digits. Measured on the maintainer's box (#403): one `interface ens18.100`
    put `interface tap4096.100 / vlan 100` into the running configuration,
    which is saved, replayed at boot, and an attempt to configure the half of a
    pair that linux_cp creates and destroys by itself.
    """
    if not name.startswith("tap") or len(name) <= 3:
        return False
    rest = name[3:]
    if "." in rest:
        base, _, sub = rest.partition(".")
        return is_number(base) and is_number(sub)
    return is_number(rest)


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
        # A static route's description, keyed by static_route_key(): FRR has
        # no field for one, so this box keeps it (#436).
        self.static_descriptions: dict[str, str] = {}
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
        # Interfaces whose block holds nothing a dictionary above carries
        # (#461): since the same-name pair is not written, `interface ens19`
        # with nothing under it is what a file says for a card that is only
        # paired. Kept, so the block survives a save and the pair a commit.
        self.declared: set[str] = set()
        # Which of linux_names the box decided on its own, best-effort
        # (#461); never rendered. On the Config and not the Shell, because a
        # commit applies the same target twice - a check, then the change -
        # and the first pass has already written the name the second reads.
        self.soft_pairs: set[str] = set()
        # Loopbacks this configuration says should exist. A loopback is
        # CREATED by naming it, the way a sub-interface is, because there is
        # nothing else to create it from - and an ISP router needs one for a
        # router-id and an update-source that do not disappear with a port.
        self.loopbacks: set = set()
        # LACP bundles (#455), {ae<n>: {"members": [...], "lacp", "rate",
        # "hash"}}. Created by being named, like a loopback.
        self.bundles: dict = {}
        # Virtual routers on an interface: name -> {vrid -> vr}, where a vr
        # is {"addresses": [...], "priority": int, "track": {ifname: dec}}.
        #
        # THE VIRTUAL ROUTER IS THE DATAPLANE'S, and that is the whole of #7.
        # FRR ships VRRP and would run it on the linux_cp TAP, which is not
        # where the packets are: an implementation built that way moves an
        # address in Linux and leaves the VPP FIB - and the CGNAT feature arcs
        # - on the box that died. The dataplane already ships the other half,
        # `vrrp_plugin.so`, RFC 5798, loaded on every box with no virtual
        # router on it because nothing asked for one. This is what asks.
        self.vrrp: dict[str, dict] = {}
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
        # The resolvers `dns server` set, in order (#437). Empty when the
        # VTY does not own /etc/resolv.conf - DHCP or the installer does.
        self.dns_servers: list = []
        # The box's name (#516): Linux's, FRR's and sysName. "" is no
        # statement - a file saved before there was one - which changes
        # nothing; `no hostname` is the installer's default, stated.
        self.hostname: str = ""
        self.users: dict = {}
        self.snmp: dict = dict(SNMP_DEFAULTS)
        self.snmp_communities: list = []
        self.snmp_users: dict = {}
        # (address, community, kind) per notification destination (#507)
        self.snmp_trap_hosts: list = []
        # Whether the SNMP settings above were READ off the box (#525): "" when
        # they were, "saved" when this session could not read snmpd.conf and
        # they are the last saved file's, "none" when it could read neither.
        # Not configuration: never written, never compared.
        self.snmp_unread: str = ""
        # The card the DATAPLANE forwards on, by the name LINUX gives it.
        #
        # Not one of the interfaces above, and the difference is the whole
        # reason this exists. Those are interfaces VPP already has; this one
        # names the card VPP is to be GIVEN, and until it has been given one
        # VPP has nothing at all - so the setting that fixes an appliance with
        # no dataplane interface cannot be expressed in the words that
        # describe interfaces the dataplane has.
        #
        # A Linux name and not a VPP name for the same reason: before the
        # handover the card exists only to Linux, and Linux's name is the one
        # written on every other screen the operator can reach.
        #
        # Empty means no card is claimed, which is what a fresh appliance
        # ships as and is deliberate: with nothing named VPP probes and takes
        # whatever NIC it finds, and on a single-NIC box that is the
        # management port.
        self.dataplane_if: str = ""
        # The memory given to the CGNAT, in GB (#454): 0 is unset, and the
        # tuner then sizes it from the subscriber target. A box setting like
        # dataplane_if - it goes to tune.env and takes effect at the next
        # boot, not to the dataplane - though it is written in the cgnat
        # block, because that is what an operator looks for it under.
        self.cgnat_memory_gb: int = 0
        # The routing policy, Junos-style (#458): the model's flat
        # `policy-options ...` lines, compiled to FRR route-maps at apply by
        # policy.py. FRR hands back only the compiled text, so the source is
        # kept here and, on the box, in a file beside the configuration.
        self.policy: list[str] = []
        # The cores by role (#466), {statement: value} as typed under
        # `system` > `cpu`. A box setting like the two above: written to
        # tune.env, in effect from the next boot.
        self.cpu: dict[str, str] = {}
        # The memory by role (#466), the same kind of setting under
        # `system` > `memory`.
        self.memory: dict[str, str] = {}
        # Lines this VTY once held and no longer does, dropped from the file
        # on purpose rather than refused with it: (lineno, text, why). One
        # statement is in here today, `dataplane management`, which #303
        # wrote beside the card on a one-card box and #385 removed because
        # the box computes that record for itself on every boot. A box that
        # saved such a file in 0.74.x through 0.76.1 replays it at every
        # boot, and a refusal there would be a box coming up with no pools on
        # it, read by nobody, over a line that was already doing nothing.
        # Whoever loads the file says what was dropped; the next `save`
        # writes the file without it.
        self.dropped: list[tuple[int, str, str]] = []
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
        a file - "!\n! Prisma Routing\n!" - and reading it as removal made
        `prismash --load` refuse it at the first boot of every fresh install:
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
        # ONLY WHEN IT IS NOT THE INTERFACE'S OWN NAME (#461). The box pairs a
        # card and a loopback under their own names by itself (#389, #404),
        # and every configuration carried `linux-name ens19` under `interface
        # ens19` saying so. A different name - one VPP invented with a '/' in
        # it, or one an operator chose - is still a statement, and shown.
        if self.linux_names.get(ifname, ifname) != ifname:
            out.append(f" linux-name {self.linux_names[ifname]}")
        # A bundle's members and its LACP settings (#455), first after the
        # MAC: what the interface IS before what is configured on it. A
        # setting at its default is not written, as everywhere here.
        if ifname in self.bundles:
            b = self.bundles[ifname]
            out += [f" member {m}" for m in b["members"]]
            for key, word in (("lacp", "lacp mode"), ("rate", "lacp rate"),
                              ("hash", "load-balance")):
                if b[key] != BUNDLE_DEFAULTS[key]:
                    out.append(f" {word} {b[key]}")
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
        # The CGNAT role, on the interface it is a property of (#465). It sat
        # under `nat cgnat` as `interface <name> inside`, a list of names
        # somewhere else in the file; an operator reading a port could not
        # see from its block that subscribers arrive on it.
        if ifname in self.interfaces:
            out.append(f" cgnat {self.interfaces[ifname]}")
        # Last, and each virtual router's statements together: an operator
        # reading a port wants to know what it IS before being told which
        # service address it may take over.
        for vrid in sorted(self.vrrp.get(ifname, {})):
            vr = self.vrrp[ifname][vrid]
            for a in vr["addresses"]:
                out.append(f" vrrp {vrid} address {a}")
            if vr["priority"] != VRRP_PRIORITY_DEFAULT:
                out.append(f" vrrp {vrid} priority {vr['priority']}")
            for tracked in sorted(vr["track"]):
                out.append(f" vrrp {vrid} track {tracked} "
                           f"decrement {vr['track'][tracked]}")
        return out

    def cgnat_block(self) -> list[str]:
        """
        Every CGNAT statement, as the `nat` > `cgnat` block an operator types
        (#446): pools, then the globals. The interfaces' roles are in their
        own blocks since #465. Nothing at all when there is no CGNAT
        configuration, so an empty box does not carry an empty block.

        ONE BLOCK, where there used to be three groups of one-line statements
        - and the third of those, the globals, sat at the bottom of the file
        under the SNMP settings, far from the pools they are about.
        """
        flat = []
        for name in sorted(self.pools):
            flat += self.pools[name].lines()
        # No interface lines: the role is written in the interface's own
        # block since #465.
        if self.cgnat_memory_gb:
            flat.append(f"{CGNAT_LEVEL} memory {self.cgnat_memory_gb}")
        flat += ordered_globals(self.globals)
        return nest_cgnat(flat)

    def text(self) -> str:
        out = [CGNAT_HEADER]
        out += self.cgnat_block()
        for ifname in sorted(self.interface_names()):
            out.append(f"interface {ifname}")
            out += self.interface_lines(ifname)
            out.append(" exit")
        # With the box's own configuration - the SSH daemon, the accounts,
        # SNMP - and not with the interface blocks above, because it is the
        # same kind of statement as those: a property of the machine, applied
        # by writing a file on it and running a program, rather than something
        # handed to the dataplane over its API.
        #
        # Written only when there is one. A box that has not been given a card
        # carries no statement, the same way an interface nobody shut down
        # carries no `shutdown`: recording an absence would put a line on every
        # box and make the file describe a decision nobody took.
        out += nest_system(self.dataplane_if, self.cpu, self.memory)
        out += policy_mod.nest(self.policy)
        if self.hostname:
            out.append(f"hostname {self.hostname}")
        for k in SSH_FIELDS:
            out.append(f"ssh {k} {self.ssh.get(k, SSH_DEFAULTS[k])}")
        out += [f"dns server {a}" for a in self.dns_servers]
        for name in sorted(self.users):
            out += self.users[name].lines()
        for k in SNMP_FIELDS:
            v = self.snmp.get(k, SNMP_DEFAULTS[k])
            # The product's number is not written (#508): a saved file that
            # named it would pin the provisional one on every box the day the
            # product's own replaces it.
            if k == "enterprise-number" and v == SNMP_DEFAULTS[k]:
                continue
            if v != "":
                out.append(f"snmp {k} {v}")
        for c in self.snmp_communities:
            out.append(f"snmp community {c}")
        for a, c, kind in self.snmp_trap_hosts:
            out.append(f"snmp trap-host {a} community {c}"
                       + (" inform" if kind == "inform" else ""))
        for name in sorted(self.snmp_users):
            out += self.snmp_users[name].lines()
        out.append(ROUTING_HEADER)
        out += group_static(self.routing, self.static_descriptions)
        return "\n".join(out) + "\n"

    def frr_interface_names(self) -> list:
        """
        The interfaces the routing section opens a stanza for, in order.

        Every `interface <name>` typed at the VTY is staged as an FRR stanza
        whether or not anything FRR needs follows it, so this is the list of
        names FRR is handed - which is where a name FRR cannot take is
        refused (#389). Read the way the interface-existence check reads
        them: a column-0 `interface` line with one word after it.
        """
        out = []
        for path, line in zip(block_paths(self.routing), self.routing):
            w = line.split()
            if path == [] and len(w) == 2 and w[0] == "interface":
                out.append(w[1])
        return out

    def drop_frr_interface_blocks(self, names) -> list:
        """
        Take the FRR stanzas for `names` out of the routing section: the
        header, what was typed inside it, and its own `exit`. Returns the
        names dropped, in order.

        For `prismash --load` alone (#389). FRR refuses a stanza whose name is
        over FRR_IFNAME_MAX characters, so a saved file carrying one never
        had that stanza in FRR on any version; replaying the file minus the
        stanza is replaying the configuration the box had, and refusing the
        file whole would be a box coming up with no pool on it over a block
        FRR was never holding. The dataplane half of the interface - the
        addresses, the MTU, the CGNAT attachment - is not in here and stays.

        Walked with block_paths() because `routing` arrives in two shapes,
        indented from FRR and flat from the prompt, and both are read there.
        """
        drop = set(names)
        out, dropped, inside = [], [], None
        for path, line in zip(block_paths(self.routing), self.routing):
            stmt = line.strip()
            w = stmt.split()
            if (path == [] and len(w) == 2 and w[0] == "interface"
                    and w[1] in drop):
                inside, dropped = stmt, dropped + [w[1]]
                continue
            if inside is not None:
                if path is not None and path[:1] == [inside]:
                    continue                    # a statement of the block
                if path is None and stmt in FRR_BLOCK_CLOSERS:
                    inside = None               # the block's own exit
                    continue
                inside = None                   # closed by what follows
            out.append(line)
        self.routing = out
        return dropped

    def interface_names(self) -> set:
        """
        Every interface this configuration has an `interface` block for.

        The same union the renderer walks, so the two cannot disagree about
        which interfaces a file names.
        """
        return (set(self.mtus) | set(self.urpf) | set(self.descriptions)
                | set(self.admin_down) | set(self.addresses) | set(self.vlans)
                | set(self.vrfs) | set(self.macs) | set(self.linux_names)
                | set(self.loopbacks) | set(self.bundles)
                | set(self.ra_enabled)
                | set(self.vrrp) | set(self.interfaces) | self.declared)

    def connected_prefixes(self) -> dict:
        """
        {table: [prefix]} - what this configuration MAKES connected.

        The other half of routes.connected_from_fib(), and the reason it is
        needed is #294: the next-hop check read the live FIB, and at
        `prismash --load` the FIB is mid-construction. A box was refusing its
        ENTIRE saved configuration over a static route whose next hop is made
        reachable by an interface address three lines above it in the same
        file, because at the moment the route was read the dataplane had
        nothing on it and FRR - on the path that lost its own frr.conf - held
        nothing either.

        Keyed by TABLE, because that is the question routes.py asks and the one
        #255 was filed for: an address in `vrf 10` connects nothing in table 0.
        `vrfs` is already the numeric table id, which is why it is numeric.

        IPv4 only, like the whole of routes.py: the next-hop check is IPv4, and
        an IPv6 address offered to it would be silently unable to cover
        anything, which is a check that looks as though it ran.

        A table with no IPv4 address in this file is NOT a key here, and that
        is the difference from connected_from_fib(), which keeps its empty
        tables. The caller unions the two, and in routes.unreachable_next_hops
        a table that is present with an empty list is a REFUSAL while a table
        that is absent is a note saying the check could not be made. Adding a
        key for a VRF this file only puts an IPv6 address in would turn that
        note into a refusal of a configuration this VTY cannot see, which is
        the harm #208 was filed for. This half may only ever ADD prefixes.

        The address is NOT masked down to its network here. routes._covers()
        masks the prefix it is given, so 200.150.0.1/24 already answers for
        200.150.0.2, and doing the arithmetic twice would be a second
        implementation of it to keep in step.
        """
        out: dict = {}
        for name, addr in self.interface_addresses_v4():
            out.setdefault(self.vrfs.get(name, 0), []).append(addr)
        # The management address of a card being handed to the dataplane, for
        return out

    def interface_addresses_v4(self) -> list:
        """
        [(interface, address)] - every IPv4 address this file puts on a port.

        THE one reader of that question, and connected_prefixes() above is
        built on it rather than beside it. That is not tidiness, it is #296.

        Two checks now ask what addresses this box will have once a candidate
        is applied. The next-hop check asks it by TABLE, because an address in
        `vrf 10` connects nothing in table 0 (#255). The local-overlap check in
        Shell.apply() asks it by INTERFACE and ignores the table entirely,
        because the thing it is defending against does too: `bpa-out2in` sits
        on the ip4-unicast arc of whatever port the pool is attached to, ahead
        of ip4-lookup and therefore ahead of anything that knows about a VRF.
        Two questions, two shapes, and it would have been easy to write the
        second walk over self.addresses here and be done.

        That second walk is exactly the defect #296 is about, one layer up.
        The plugin's live walk and prismash's next-hop check were two readers of
        "what addresses does this box have" that disagreed about the moment
        being asked about, and a box loading its own file at four in the
        morning fell through the gap between them. Adding a third reader that
        could drift on the IPv6 filter, or on secondary addresses, or on a
        future kind of address statement, would rebuild that gap in this file.
        So both callers turn the same crank and differ only in what they do
        with the handle.

        IPv4 only, and the filter lives here for the same reason. routes.py is
        IPv4 throughout and a CGNAT pool's public set is IPv4 by construction,
        so an IPv6 address offered to either caller could not match anything -
        which is a check that looks as though it ran.

        NOT masked down to the network. Both callers want it that way and for
        opposite reasons: routes._covers() masks the prefix it is given, and
        the local-overlap check wants the HOST address, which is the one the
        box answers BGP on. Doing the arithmetic here would take that away
        from the second caller to save the first a line.
        """
        return [(name, a) for name, addrs in self.addresses.items()
                for a in addrs if ":" not in a]

    def drop_lcp_taps(self, taps):
        """
        Take the named linux_cp TAPs out of this configuration.

        MEASURED, and what it prevents is a box that does not come up.
        `device_macs` and `device_mtus` report every interface the dataplane
        has, and after #182 gave the product a way to MAKE a pair the
        dataplane has one more: the pair's own TAP. So `save` wrote

            interface tap4097
             mac-address de:ad:00:00:00:07

        into /etc/prisma-routing/prisma-routing.conf. prisma-routing.service hands that
        file straight back to `prismash --load` at every boot, against a VPP
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
        self.declared -= taps

    @classmethod
    def parse(cls, text: str, dataplane: bool = False) -> "Config":
        """
        A configuration from text: a saved file, a rollback revision, the
        peer's file - or, with `dataplane=True`, what the plugin answers to
        `show bpa config`.

        THE TWO ARE DIFFERENT FORMATS SINCE #446. A file holds the `nat` >
        `cgnat` block an operator types; the plugin speaks the flat
        statements the model holds (`cgnat pool p`), which is the form the
        file no longer takes. Read as a file, every box's own dataplane was
        the one-line form, refused - so the plugin's text is read as the
        model's, and only a file is taken through denest_nat().
        """
        cfg = cls()
        headers = []           # every `interface` block the file opens
        section = "cgnat"
        routing_lines = []     # the file's line number of each routing line
        pool = None
        iface = None
        user = None
        snmp_user = None
        numbered = list(enumerate(text.splitlines(), 1))
        if not dataplane:
            numbered, refused = denest_nat(numbered)
            cfg.unknown += refused
            cfg.n_statements += len(refused)
            numbered, refused = denest_system(numbered)
            cfg.unknown += refused
            cfg.n_statements += len(refused)
            numbered, refused = policy_mod.unnest(numbered)
            cfg.unknown += refused
            cfg.n_statements += len(refused)
        for lineno, raw in numbered:
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
            if line.strip() == ROUTING_HEADER:
                section = "routing"
                continue
            if line.strip() == CGNAT_HEADER:
                section = "cgnat"
                continue
            if section == "routing":
                cfg.n_statements += 1
                # FRR's `hostname` follows the box's since #516, and is not
                # routing anybody typed. Every file saved before then carries
                # FRR's own here - "hostname prisma-routing", from the image,
                # whatever the installer named the box - so it is dropped and
                # NOT adopted: taken as the box's name, it would rename every
                # upgraded box to the image's at its next boot.
                if line.startswith("hostname ") or line == "hostname":
                    continue
                cfg.routing.append(line)
                routing_lines.append(lineno)
                continue

            # `!` is the comment marker in this file format - the same one FRR
            # and every configuration written in this style use. The two
            # section headers above are the only `!` lines that mean anything;
            # the rest are comments and MUST be ignored.
            #
            # They were not, and install.sh writes "!\n! Prisma Routing\n!\n" as
            # the default configuration. So prismash --load refused to load it -
            # "these lines are not understood" - and exited non-zero, which
            # made prisma-routing.service FAIL ON THE FIRST BOOT OF EVERY INSTALL
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
            if s.startswith("snmp trap-host "):
                w = s.split()
                kind = "inform" if w[-1:] == ["inform"] else "trap"
                ok = len(w) in (5, 6) and w[3] == "community"
                why = (validate_trap_host(w[2], w[4], kind) if ok
                       else "expected: snmp trap-host <address> community "
                            "<string> [inform]")
                if why:
                    cfg.unknown.append((lineno, s, why))
                elif w[2] not in [t[0] for t in cfg.snmp_trap_hosts]:
                    cfg.snmp_trap_hosts.append((w[2], w[4], kind))
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
            if s == "hostname" or s.startswith("hostname "):
                # The prompt's predicate, so the file refuses what the prompt
                # refuses (#516).
                w = s.split()
                if len(w) != 2:
                    cfg.unknown.append((lineno, s, "expected: hostname <name>"))
                elif hostname_error(w[1]):
                    cfg.unknown.append((lineno, s,
                                        " - ".join(hostname_error(w[1]))))
                else:
                    cfg.hostname = w[1]
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
            if s.startswith("dns "):
                # The prompt's predicate and the prompt's limit, so the file
                # refuses what `dns server` refuses at the prompt (#437).
                w = s.split()
                if len(w) != 3 or w[1] != "server":
                    cfg.unknown.append((lineno, s, "expected: dns server "
                                                   "<address>"))
                elif dns_error(w[2]):
                    cfg.unknown.append((lineno, s, " - ".join(dns_error(w[2]))))
                elif w[2] in cfg.dns_servers:
                    pass
                elif len(cfg.dns_servers) >= DNS_MAX:
                    cfg.unknown.append((lineno, s, f"a box reads {DNS_MAX} "
                                       f"resolvers and ignores the rest"))
                else:
                    cfg.dns_servers.append(w[2])
                continue
            if s == "no dataplane interface":
                cfg.dataplane_if = ""
                continue
            _lvl = s.split()[1] if s.startswith("no ") and len(s.split()) > 1 \
                else s.split()[0]
            if _lvl in SYSTEM_SETTINGS:
                w = s.split()
                keys, error = SYSTEM_SETTINGS[_lvl]
                held = getattr(cfg, _lvl)
                if w[0] == "no":
                    if len(w) == 3 and w[2] in keys:
                        held.pop(w[2], None)
                    else:
                        cfg.unknown.append((lineno, s, error("", "")))
                    continue
                why = (error(w[1], w[2]) if len(w) == 3
                       else error(w[1] if len(w) > 1 else "", ""))
                if why:
                    cfg.unknown.append((lineno, s, why))
                else:
                    held[w[1]] = w[2]
                continue
            if s.startswith("dataplane management"):
                # DROPPED, NOT REFUSED (#385). This VTY wrote this line on a
                # one-card box from 0.74.x to 0.76.1, so saved files carry it,
                # and prisma-routing.service replays the saved file at every
                # boot. Whatever shape the line has, the box no longer reads
                # it: the way back is computed off the live card on the boot
                # that claims it. Refusing the file over it would be a box
                # coming up with no configuration, read by nobody, over a line
                # that was already doing nothing.
                cfg.dropped.append(
                    (lineno, s, "dataplane management is no longer a "
                                "statement: the box computes the way back "
                                "for itself on the boot that claims the "
                                "card, so this line is dropped and the next "
                                "'save' writes the file without it"))
                continue
            if s.startswith("dataplane"):
                w = s.split()
                # Shape only. WHETHER the box has this card, and whether it is
                # the one carrying the default route, is not decided here: a
                # file is parsed where no box can be asked - `prismash --load`
                # runs before anything has been reached - and a parser that
                # answered those questions would answer them wrongly. The
                # Shell checks them where the box is in reach, which is at the
                # prompt and again at commit.
                if w[1:2] != ["interface"] or len(w) != 3:
                    cfg.unknown.append(
                        (lineno, s, "expected: dataplane interface <name>"))
                else:
                    cfg.dataplane_if = w[2]
                continue
            if s.startswith("user "):
                w = s.split()
                if len(w) < 2:
                    cfg.unknown.append((lineno, s, "a user needs a name"))
                    continue
                why = text_error("user", w[1])
                if why:
                    cfg.unknown.append((lineno, s, text_why(why)))
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
                why = text_error("cgnat pool", w[2])
                if why:
                    cfg.unknown.append((lineno, s, text_why(why)))
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
                cfg.bundles.pop(gone, None)
                cfg.macs.pop(gone, None)
                cfg.vrfs.pop(gone, None)
                cfg.ra_enabled.discard(gone)
                cfg.declared.discard(gone)
                cfg.vrrp.pop(gone, None)
            elif s.startswith("interface "):
                iface = s.split()[1]
                headers.append(iface)
                # A loopback is created by being named, the way a
                # sub-interface is: there is nothing else on the box to
                # create it from. `loop<n>` is VPP's own spelling, so the
                # configuration and `show interface` agree about what the
                # thing is called.
                if _is_loopback(iface):
                    cfg.loopbacks.add(iface)
                if _is_bundle(iface):
                    cfg.bundles.setdefault(iface, new_bundle())
            elif iface is not None and line.startswith(" "):
                w = s.split()
                _bw = w[1:] if w[:1] == ["no"] else w
                if s == "exit":
                    iface = None
                elif _bw[:1] and _bw[0] in ("member", "lacp", "load-balance"):
                    # A bundle's own statements (#455); on any other
                    # interface they are refused rather than dropped.
                    if not _is_bundle(iface):
                        cfg.unknown.append(
                            (lineno, s, "only an ae<n> bundle takes members, "
                                        "lacp and load-balance"))
                    else:
                        why = bundle_statement(
                            cfg.bundles.setdefault(iface, new_bundle()), w)
                        if why:
                            cfg.unknown.append((lineno, s, why))
                elif w[0] == "mtu" and len(w) > 1 and is_number(w[1]):
                    # BOUNDED HERE TOO, unlike a pool's numbers, and the
                    # difference is which reader this is. A pool's are read
                    # back out of the dataplane through _pool_line, so a
                    # bound there refuses a running box's own facts (#357).
                    # An interface's MTU never comes through this function:
                    # `running_config` fills `cfg.mtus` from `device_mtus()`
                    # directly. So this parser sees only a saved file, and a
                    # file is a request like anything an operator types.
                    # NOT BOUNDED HERE, and #417 had this wrong. The
                    # argument then was that `running_config` fills
                    # `cfg.mtus` from `device_mtus()` directly, so this
                    # parser only ever sees a saved file - and a file is a
                    # request. The second half does not follow: a saved file
                    # is WRITTEN FROM the readback, so it carries facts. An
                    # MTU outside the range the prompt offers, on a box that
                    # really has one, would be written by `save` and refuse
                    # the whole file at the next boot (#419).
                    cfg.mtus[iface] = int(w[1])
                elif w[0] == "linux-name" and len(w) > 1:
                    # Refused here rather than at commit, because the message
                    # an operator gets from the dataplane for this is a bare
                    # retval. The limit and the sentence are
                    # linux_ifname_error()'s, not a second copy.
                    why = linux_ifname_error(w[1])
                    if why:
                        cfg.unknown.append((lineno, s, why))
                    else:
                        cfg.linux_names[iface] = w[1]
                elif s == "no linux-name":
                    cfg.linux_names.pop(iface, None)
                elif w[0] == "cgnat":
                    # The interface's CGNAT role, where it now lives (#465).
                    # The form under `nat cgnat` is still read below, so a
                    # file saved by an older box keeps its CGNAT; the next
                    # save writes it here.
                    if len(w) != 2 or w[1] not in ("inside", "outside"):
                        cfg.unknown.append(
                            (lineno, s, "expected: cgnat <inside|outside>"))
                    else:
                        cfg.interfaces[iface] = w[1]
                elif s == "no cgnat":
                    cfg.interfaces.pop(iface, None)
                elif w[0] == "mac-address" and len(w) > 1:
                    why = mac_error(w[1], request=False)
                    if why:
                        cfg.unknown.append((lineno, s, mac_why(why)))
                    else:
                        cfg.macs[iface] = mac_normal(w[1])
                elif s == "no mac-address":
                    cfg.macs.pop(iface, None)
                elif s == "ipv6 nd suppress-ra":
                    # The default. Accepted because an operator may write it
                    # to say so out loud, and because a configuration saved
                    # from an older box carries it.
                    cfg.ra_enabled.discard(iface)
                elif s == "no ipv6 nd suppress-ra":
                    cfg.ra_enabled.add(iface)
                elif w[0] == "vrf" and len(w) > 1 and is_number(w[1]):
                    # Bounded here for the reason `mtu` above is: an
                    # interface's VRF is filled from `device_vrfs()` and not
                    # from this parser, so a file is the only thing it reads.
                    # Not bounded here either, for the reason above.
                    if int(w[1]):
                        cfg.vrfs[iface] = int(w[1])
                    else:
                        cfg.vrfs.pop(iface, None)
                elif s == "no vrf":
                    cfg.vrfs.pop(iface, None)
                elif w[0] == "vlan" and len(w) > 1 and is_number(w[1]):
                    # The tag is bounded HERE as well as at the prompt. A file
                    # is the other way a statement arrives, and a range checked
                    # only where it is typed is a range an operator gets past
                    # by saving the line and loading it back - which is the
                    # defect the `forward` comment in do_pool warns about, one
                    # statement meaning two different things depending on how
                    # it got here.
                    why = vlan_tag_error(w[1])
                    if why:
                        cfg.unknown.append((lineno, s, why))
                        continue
                    tag = {"outer": int(w[1]), "inner": None}
                    if len(w) >= 4 and w[2] == "inner" and is_number(w[3]):
                        why = vlan_tag_error(w[3], "inner")
                        if why:
                            cfg.unknown.append((lineno, s, why))
                            continue
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
                    text = s.split(None, 1)[1]
                    why = text_error("description", text,
                                     request=False)
                    if why:
                        cfg.unknown.append((lineno, s, text_why(why)))
                    else:
                        cfg.descriptions[iface] = text
                elif s == "no description":
                    cfg.descriptions.pop(iface, None)
                elif s == "shutdown":
                    cfg.admin_down.add(iface)
                elif s == "no shutdown":
                    cfg.admin_down.discard(iface)
                elif w[0] == "vrrp":
                    why = vrrp_statement(cfg.vrrp, iface, w)
                    if why:
                        cfg.unknown.append((lineno, s, vrrp_why(why)))
                elif w[:1] == ["no"] and w[1:2] == ["vrrp"]:
                    why = vrrp_remove(cfg.vrrp, iface, w[1:])
                    if why:
                        cfg.unknown.append((lineno, s, vrrp_why(why)))
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
                    # AND A SAVED FILE GOES ON REFUSING AN ABBREVIATION,
                    # which the prompt now resolves (#393). Decided rather
                    # than left to fall out: this branch is what a hand-edited
                    # file meets, and it is the only case, because `save`
                    # writes what the candidate holds and the candidate has
                    # held nothing but full spellings since the resolution
                    # moved in front of the handlers.
                    #
                    # Two reasons, and the second is the one that settles it.
                    # A file is read by `prismash --load` at every boot, on a
                    # machine nobody is watching, where there is no operator
                    # to be told that a word began two statements - so a
                    # resolution there is a guess made at the one moment
                    # nothing can correct it. And a file is the box's record
                    # of its own configuration: `shut` resolving today
                    # because one statement begins with those letters would
                    # mean something else the day a second one is added, so a
                    # file that has not changed would come back a different
                    # configuration. Refusing keeps its meaning fixed, and
                    # the sentence below already names the full spellings a
                    # hand-editor should write.
                    cfg.unknown.append(
                        (lineno, s, "an interface understands 'description "
                                    "<text>', 'shutdown', 'mtu <n>', "
                                    "'urpf <strict|loose|off>' and "
                                    "'" + VRRP_USAGE + "'"))
            elif s.split()[:2] == ["cgnat", "memory"]:
                w = s.split()
                if len(w) == 3 and is_number(w[2]) \
                        and 1 <= int(w[2]) <= CGNAT_MEMORY_MAX_GB:
                    cfg.cgnat_memory_gb = int(w[2])
                else:
                    cfg.unknown.append(
                        (lineno, s, f"expected: memory <GB>, whole GB from 1 "
                                    f"to {CGNAT_MEMORY_MAX_GB}"))
            elif s.startswith("policy-options "):
                _, why = policy_mod.parse([s])
                if why:
                    cfg.unknown.append((lineno, s, why[0][1]))
                else:
                    cfg.policy.append(s)
            elif s.startswith("cgnat "):
                cfg.globals.append(s)
            else:
                cfg.unknown.append((lineno, s, "not a CGNAT statement"))
        # `router static` back into the top-level `ip route` statements FRR
        # takes. Done after the loop rather than in it, because the block is
        # only recognisable as one from its own `exit`.
        refused = []
        cfg.routing = ungroup_static(cfg.routing, cfg.static_descriptions,
                                     refused)
        for idx, text, why in refused:
            cfg.unknown.append((routing_lines[idx], text, why))
        # NOT dropping linux_cp TAPs here. A file is parsed with no dataplane
        # in reach - `prismash --load` runs before anything asks VPP anything -
        # so this is the one place that cannot tell a pair's TAP from a TAP an
        # operator made. Shell.load does it, where the box can be asked.
        #
        # A BLOCK WITH NOTHING ELSE IN IT IS THE SAME-NAME PAIR (#461). The
        # Linux end of an interface takes the interface's own name, and that
        # name is not written - it is what `linux-name <the same name>` said,
        # and the maintainer called the line useless. The block stays, so the
        # interface does, and reading it back gives the pair its name again:
        # without this a card whose only statement was its pair would drop
        # out of the file at the next save and lose the pair at the commit
        # after.
        #
        # DECLARED, NOT PAIRED, and that difference was measured: a pair
        # invented here is one a commit insists on, and lab/bypass.sh's pg-1
        # - an interface VPP will not pair - then failed every commit. The
        # block is kept; whether it gets a pair is Shell.pair_cards()'s to
        # decide, where the box can be asked.
        #
        # The union is taken ONCE. Rebuilt per header it made a parse
        # quadratic in the interfaces: 5,000 took 8 s and appliance/
        # test_tune.py's 20,000 took 2 min 44 s, in the tuner at boot.
        named = cfg.interface_names()
        cfg.declared.update(i for i in headers if i not in named)
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
                # Joined, because an `unknown` entry is one line of prose. The
                # rows exist for the prompt, where they are wrapped (#355).
                return ": ".join(why)
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
        if w[0] in ("inside", "outside") and len(w) > 1:
            why = prefix_error(w[0], w[1])
            if why:
                return prefix_why(why)
            (pool.inside if w[0] == "inside" else pool.outside).append(w[1])
        elif w[0] == "block-size" and len(w) > 1:
            # NOT BOUNDED HERE, and that is the whole of #357. This function
            # is the reader for THREE things: a statement typed at the prompt,
            # a saved file, and the configuration read back out of the running
            # dataplane by `show bpa config`. The first is a request and the
            # last two are facts.
            #
            # The bound was added here with #352 and it broke the third: a
            # pool built with `block-size 4` - which the dataplane accepts and
            # builds, and lab/detreserve.sh uses to keep a block cheap - came
            # back unmodellable, so `running_config()` said "committing would
            # REMOVE it" and `commit` then refused every change on that box.
            # An operator with a small block could no longer configure
            # anything at all.
            #
            # The request is bounded where the request is made:
            # Shell.pool_range() in do_pool, which is what an operator types
            # at and the only place a number can still be argued with.
            why = Pool.check_number("block-size", w[1])
            if why:
                return why
            pool.block_size = w[1]
        elif w[0] == "ports" and len(w) > 2:
            # Both halves, each named, for the reason check_range's `what`
            # exists: one statement carrying two numbers has to say WHICH of
            # them was not one.
            for value, half in ((w[1], "min"), (w[2], "max")):
                why = Pool.check_number("ports", value, half)
                if why:
                    return why
            pool.ports = f"{w[1]} {w[2]}"
        elif w[0] == "policy" and len(w) > 1:
            if w[1] not in ("interleaved", "blocked"):
                return f"policy '{w[1]}': expected interleaved or blocked"
            pool.policy = w[1]
        elif w[0] in ("vrf", "outside-vrf") and len(w) > 1:
            # A pool's VRF reaches `int()` in api_args, not here, so an
            # unguarded word crashed the COMMIT rather than the parse - one
            # step further from the operator than the seven below, and the
            # same defect.
            why = Pool.check_number(w[0], w[1])
            if why:
                return why
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
            why = Pool.check_number("timeout", w[2], w[1])
            if why:
                return why
            pool.timeouts[w[1]] = int(w[2])
        elif w[0] == "max-blocks" and len(w) > 1:
            # The TOP as well as the bottom, and the top is where this was
            # wrong: `max-blocks 256` was taken here, written to the file,
            # sent to a u8 field and arrived as 1 (#351).
            why = Pool.check_number("max-blocks", w[1])
            if why:
                return why
            pool.max_blocks = int(w[1])
        elif w[0] == "initial-allocation" and len(w) > 1:
            why = Pool.check_number("initial-allocation", w[1])
            if why:
                return why
            pool.initial_ports = int(w[1])
        elif w[0] == "initial-blocks" and len(w) > 1:
            # The plugin's word, which `show bpa config` prints: read back
            # into ports against the block size it printed above it (#462).
            why = Pool.check_number("initial-blocks", w[1])
            if why:
                return why
            pool.initial_ports = int(w[1]) * int(pool.block_size)
        elif w[0] == "block-stride" and len(w) > 1:
            why = Pool.check_number("block-stride", w[1])
            if why:
                return why
            pool.block_stride = int(w[1])
        elif w[0] == "reserve" and len(w) > 1:
            why = Pool.check_number("reserve", w[1])
            if why:
                return why
            pool.block_reserve = int(w[1])
        elif w[0] == "bypass" and len(w) > 2:
            if w[1] not in ("source", "destination"):
                return f"bypass '{w[1]}': expected source or destination"
            why = prefix_error(f"bypass {w[1]}", w[2])
            if why:
                return prefix_why(why)
            lst = pool.bypass_dst if w[1] == "destination" else pool.bypass_src
            if w[2] not in lst:
                lst.append(w[2])
        elif w[0] == "utilisation-alarm" and len(w) > 1:
            # Through Pool.RANGES like the rest, and the sentence changed with
            # it: "between 1 and 100" spelled out in words the two numbers `?`
            # quotes as `<1-100>`, so an operator comparing the screen to the
            # hint was comparing two spellings of one range - and nothing
            # could compare them either (#351).
            why = Pool.check_number("utilisation-alarm", w[1])
            if why:
                return why
            pool.util_alarm = int(w[1])
        elif w[0] in ("max-sessions", "max-session-rate") and len(w) > 1:
            why = Pool.check_number(w[0], w[1])
            if why:
                return why
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
                if not is_number(p) or not 1 <= int(p) <= 65535:
                    return f"forward: '{p}' is not a port"
            # The device decides whether the address is a subscriber of this
            # pool and whether the port is in its block; it is the only thing
            # that knows the pool arithmetic. Refusing a malformed address
            # here is not that check - it is the one the device would report
            # as a parse error a commit later.
            octets = w[4].split(".")
            if len(octets) != 4 or not all(
                    is_number(o) and int(o) < 256 for o in octets):
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

    # -------------------------------------------------- mapping equality

    def mapping_lines(self) -> list[str]:
        """
        Every line two boxes must agree on, and no line they need not.

        DERIVED, not listed. The text is `Pool.lines(in_place=False)`, which is
        already the product's own answer to "does this change re-map
        subscribers": `delta_to` rebuilds a pool exactly when that text moves,
        and `test/test_prismash_config.py` asserts field by field that the
        classification is right AND that every field a pool has is classified.
        A second table of "the fields two peers must agree on" would be a
        second copy of that decision, and the two would disagree the first time
        a field was added to one of them - which is the defect this repository
        keeps finding. There is ONE text and one digest over it, for the same
        reason.

        The pools in name order, each as `lines(in_place=False)`. Order is
        fixed here rather than taken from the file, because the same pools
        typed in a different order are the same mapping, and a digest that
        said otherwise would report a divergence on two boxes that map every
        subscriber identically - the refusal on a correct configuration is the
        harm, not the absence of one.
        """
        out = []
        for _, pool in sorted(self.pools.items()):
            out += pool.lines(in_place=False)
        return out

    def mapping_fingerprint(self) -> str:
        """This box's whole mapping, as one digest. See `mapping_lines`."""
        return mapping_digest(self.mapping_lines())

    def mapping_diff_to(self, other: "Config") -> list[tuple[str, str, str]]:
        """
        Which pools the two boxes would map differently, and how.

        `(pool, here, there)` per finding, with `here` or `there` empty when
        only one box has the pool at all. An empty list means every subscriber
        lands on the same public address and port block on both boxes.

        WHY this is not `is_same`. `is_same` compares the whole configuration
        text, so it reports a divergence when the two boxes differ on a TCP
        clamp, an ALG or a session limit - settings the dataplane changes on a
        pool that is carrying traffic, which move no subscriber anywhere. A
        check built on it would fire on a pair that is correctly configured,
        and an operator who is warned about a pair that is fine learns to skip
        the warning that means their subscribers are about to move.

        And it is not a list of field names either. The fields compared are
        whatever `lines(in_place=False)` emits, which is the same text
        `delta_to` uses to decide a pool must be rebuilt - one decision, in one
        place.
        """
        findings = []
        for name in sorted(set(self.pools) | set(other.pools)):
            mine = self.pools.get(name)
            theirs = other.pools.get(name)
            # Named as the path to it in the file both boxes write (#446),
            # which is what an operator greps `show configuration` for.
            path = f"{NAT_HIERARCHY} / {CGNAT_LEVEL} / pool {name}"
            if mine is None:
                findings.append((name, "", path))
                continue
            if theirs is None:
                findings.append((name, path, ""))
                continue
            a = mine.lines(in_place=False)
            b = theirs.lines(in_place=False)
            if a == b:
                continue
            # The differing STATEMENTS, not the field names. A pool with two
            # `outside` prefixes has two lines with the same first word, and
            # naming the field would report "outside differs" while leaving
            # the operator to work out which prefix. Matched by first word so
            # that a changed value pairs with its opposite number instead of
            # appearing twice as an addition and a removal.
            seen = set()
            for line in a + b:
                key = line.split()[0] if line.split() else ""
                if key in seen:
                    continue
                seen.add(key)
                mine_rows = [l for l in a if l.split()[:1] == [key]]
                their_rows = [l for l in b if l.split()[:1] == [key]]
                if mine_rows == their_rows:
                    continue
                findings.append(
                    (name, "; ".join(l.strip() for l in mine_rows),
                     "; ".join(l.strip() for l in their_rows)))
        return findings

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
        # A bundle is created and destroyed too (#455). One whose mode, rate
        # or hash changed is rebuilt: the hash is fixed at creation and the
        # mode and rate at each member's joining, so a rebuild is the change,
        # and it is listed as one so the commit can say it drops the bundle.
        bundle_add = {k: v for k, v in other.bundles.items()
                      if k not in self.bundles
                      or {x: self.bundles[k][x] for x in BUNDLE_DEFAULTS}
                      != {x: v[x] for x in BUNDLE_DEFAULTS}}
        bundle_del = sorted(k for k in self.bundles
                            if k not in other.bundles or k in bundle_add)
        member_add = {k: [m for m in v["members"]
                          if m not in self.bundles[k]["members"]]
                      for k, v in other.bundles.items()
                      if k in self.bundles and k not in bundle_add}
        member_add = {k: v for k, v in member_add.items() if v}
        member_del = {k: [m for m in self.bundles[k]["members"]
                          if m not in v["members"]]
                      for k, v in other.bundles.items()
                      if k in self.bundles and k not in bundle_add}
        member_del = {k: v for k, v in member_del.items() if v}
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
        # A virtual router is replaced whole rather than field by field. VPP's
        # own message says why: `vrrp_vr_update` takes the addresses, the
        # priority and the flags together, and an election in progress is not
        # a thing to walk through three states of.
        vrrp_set = {k: v for k, v in other.vrrp.items()
                    if self.vrrp.get(k) != v}
        # Both directions, for the reason uRPF's removal exists: a rollback
        # that cannot take a virtual router off an interface is a box still
        # standing for a service address the operator withdrew it from.
        vrrp_del = {k: sorted(v) for k, v in self.vrrp.items()
                    if k not in other.vrrp}
        for k, v in self.vrrp.items():
            gone = sorted(set(v) - set(other.vrrp.get(k, {})))
            if gone and k in other.vrrp:
                vrrp_del[k] = gone
        # Removing the statement has to put the interface back to the driver
        # default, not leave the last committed value behind. A rollback that
        # cannot undo is not a rollback.
        mtu_del = [k for k in self.mtus if k not in other.mtus]

        # Reported as one flag rather than a delta: the SSH daemon takes its
        # configuration whole, and an account is created or it is not.
        ssh_changed = self.ssh != other.ssh
        snmp_changed = (self.snmp != other.snmp
                        or self.snmp_communities != other.snmp_communities
                        or self.snmp_trap_hosts != other.snmp_trap_hosts
                        or sorted(self.snmp_users) != sorted(other.snmp_users)
                        or any(self.snmp_users[k] != other.snmp_users[k]
                               for k in self.snmp_users
                               if k in other.snmp_users))
        users_changed = (sorted(self.users) != sorted(other.users)
                         or any(self.users[k] != other.users[k]
                                for k in self.users if k in other.users))

        return {
            # One flag, like ssh: the tuner takes the whole setting and either
            # the box has been given a card or it has not. Both directions are
            # a change - removing the statement has to make the box stop
            # claiming the card, or a rollback could hand one over and never
            # take it back.
            "dataplane_if_changed": self.dataplane_if != other.dataplane_if,
            "policy_changed": self.policy != other.policy,
            "cpu_changed": self.cpu != other.cpu,
            "memory_changed": self.memory != other.memory,
            "cgnat_memory_changed":
                self.cgnat_memory_gb != other.cgnat_memory_gb,
            "snmp_changed": snmp_changed,
            "ssh_changed": ssh_changed,
            "dns_changed": self.dns_servers != other.dns_servers,
            # Only a name that is stated: a file with none leaves the box's.
            "hostname_changed": bool(other.hostname)
            and other.hostname != self.hostname,
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
            "bundle_add": bundle_add,
            "bundle_del": bundle_del,
            "member_add": member_add,
            "member_del": member_del,
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
            "vrrp_set": vrrp_set,
            "vrrp_del": vrrp_del,
            "globals": [g for g in other.globals if g not in self.globals],
            # A policy is routing: it is compiled into what FRR is handed,
            # so a change to it is a reload like any other (#458).
            "routing_changed": (self.routing != other.routing
                                or self.policy != other.policy),
            "route_desc_changed": (self.static_descriptions
                                   != other.static_descriptions),
        }

# ------------------------------------------------------------------ probes
# ping, traceroute and mtr (#395). Here and not in prismash.py because the
# host's doors check the same things when the VTY runs in the management
# plane (#448): one owner for what a probe may be given.

#: The most probes, or hops, one statement may ask for.
PROBE_COUNT_MAX = 100
# The wall clock each probe gets, whatever it was asked for. A tool that hangs
# - a DNS server that never answers, an mtr against a host that black-holes
# every cycle - must not take the VTY with it.
PROBE_DEADLINE = 300


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
