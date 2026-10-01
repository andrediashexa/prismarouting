#!/usr/bin/env python3
"""
Routing policy, Junos-style, compiled to what FRR applies (#458).

The maintainer chose the shape (2026-09-25): `policy-options` holds named
prefix lists, communities and AS-path expressions, and `policy-statement`s
made of ordered `term`s, each with `from` conditions, `set` actions and a
`then` that ends it. A policy is applied as `import` or `export` on a BGP
neighbor. FRR has no such language - it has route-maps, prefix-lists,
community-lists and AS-path access-lists - so this module is the compiler,
and it is pure: text in, FRR statements out, nothing asked of the box.

THE MODEL IS FLAT, the way the CGNAT model is (config.nest_cgnat): every
statement is one line that says where it lives,

    policy-options prefix-list CUSTOMERS 203.0.113.0/24
    policy-options policy-statement UPSTREAM-OUT term 10 from prefix-list CUSTOMERS
    policy-options policy-statement UPSTREAM-OUT term 10 then accept

and the file writes it as the nested block an operator types.

WHAT A TERM MEANS, as Junos says it:
  * `from` lines of the same kind are ORed - a route in any of the prefix
    lists named - and lines of different kinds are ANDed;
  * `set` lines change the route when the term matches;
  * `then accept` and `then reject` end the evaluation; `then next term`, or
    no `then` at all, applies the sets and goes on to the next term.

WHAT FRR IS GIVEN, per use: one route-map, `PO-<policy>-IN` or `-OUT`, one
clause per term at sequence term-position x 10, and the clause split per
address family when the term matches prefixes of both - FRR ANDs `match ip`
with `match ipv6` inside a clause, so one clause for both would match
nothing. The prefix and community conditions of a term, ORed, become one
generated list each.

THE DEFAULT AT THE END, decided with the maintainer: as Junos. A route no term
decided is accepted, on BGP import and export alike, by a last clause at
65535 that `show policy` shows as a row of its own. (FRR's route-map would
deny it, and `bgp ebgp-requires-policy` would then exchange nothing.)

THE `PO-` NAMESPACE IS THIS COMPILER'S. Every FRR object it emits starts with
it, and a route-map or a list typed by hand under that prefix is refused, so
the two can never be confused.
"""

from __future__ import annotations

import ipaddress
import re

#: The prefix of every FRR object this compiler emits (#458).
PREFIX = "PO-"
#: The longest name a policy, a prefix list, a community or an AS-path
#: expression takes: short enough that every FRR name built from it fits.
NAME_MAX = 32
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
#: The sequence the Junos default takes in every route-map: past any term.
DEFAULT_SEQ = 65535
#: The directions a policy is applied in, and the suffix FRR's map gets.
DIRECTIONS = {"import": "IN", "export": "OUT"}
#: The IGPs a policy can be exported into (#458 step 2), and the protocols
#: each can redistribute from, as FRR's `redistribute` names them. An export
#: into an IGP is one route-map per source protocol: FRR's ospfd and isisd
#: evaluate a redistribute route-map without knowing the route's origin, so
#: the origin is chosen by which `redistribute` carries the map.
IGPS = {"ospf": ("bgp", "connected", "kernel", "static", "isis"),
        "isis": ("bgp", "connected", "kernel", "static", "ospf")}
#: What `from protocol` takes. Junos says `direct` for connected; both are
#: read, and held as FRR's word.
PROTOCOLS = ("bgp", "ospf", "isis", "static", "connected", "kernel")
PROTOCOL_ALIASES = {"direct": "connected"}
U32 = 4294967295
FILTER_KINDS = ("exact", "orlonger", "longer", "upto")
COMMUNITY_OPS = ("add", "set", "delete")
#: Phase A (#535). What `from origin` and `set origin` take: BGP's three.
ORIGINS = ("igp", "egp", "incomplete")
#: `from validation-state`, Junos's words, and the RPKI state FRR's
#: `match rpki` names for each: Junos says unknown where FRR says notfound.
VALIDATION_STATES = {"valid": "valid", "invalid": "invalid",
                     "unknown": "notfound"}
#: The kinds an extended community set holds (route target, site of origin):
#: the two FRR's `set extcommunity` and `bgp extcommunity-list standard` take.
EXT_KINDS = ("rt", "soo")
#: Where `set next-hop discard` sends a route (RTBH): an address no network
#: holds, made a blackhole by a static route the compiler emits beside the
#: map. FRR 10.7.1 has no `set ip next-hop blackhole` - measured, vtysh -C
#: answers "% Unknown command" - so the discard is a route, not a keyword:
#: 192.0.2.1 is TEST-NET-1 (RFC 5737), the address RTBH setups customarily
#: use, and 100::1 is in IPv6's discard-only prefix (RFC 6666).
DISCARD = {"ip": "192.0.2.1", "ipv6": "100::1"}
#: Tagged so the box tells them from an operator's own route to the same
#: address: the tag is the one a hand-typed route is least likely to carry.
DISCARD_TAG = 4294967294
DISCARD_ROUTES = (f"ip route 192.0.2.1/32 blackhole tag {DISCARD_TAG}",
                  f"ipv6 route 100::1/128 blackhole tag {DISCARD_TAG}")
#: The AS-path access-list `set as-path remove-private` excludes by: FRR's
#: `set as-path exclude as-path-access-list` drops each ASN the list
#: matches. The private ranges are RFC 6996's: 64512-65534 and
#: 4200000000-4294967294.
PRIVATE_AS_LIST = f"{PREFIX}PRIVATE-AS"
PRIVATE_AS_REGEX = ("^(6451[2-9]|645[2-9][0-9]|64[6-9][0-9][0-9]|"
                    "65[0-4][0-9][0-9]|655[0-2][0-9]|6553[0-4]|"
                    "42[0-8][0-9]{7}|429[0-3][0-9]{6}|4294[0-8][0-9]{5}|"
                    "42949[0-5][0-9]{4}|429496[0-6][0-9]{3}|"
                    "4294967[01][0-9]{2}|42949672[0-8][0-9]|"
                    "429496729[0-4])$")
#: Policies applied together - `import [ A B ]` - are one FRR route-map
#: named for all of them, joined by this: never in a name (NAME_RE), so a
#: chain's map reads back as its policies.
CHAIN_SEP = "+"
#: In a chain, each policy's clauses start at a multiple of this, so a
#: policy's term at position n is at k x BLOCK + n x 10: ninety-nine terms a
#: policy, sixty-five policies a chain, under FRR's 65535.
BLOCK = 1000
#: How many clauses one term may compile to: its sequence numbers are
#: term-position x 10 + 0..9, so the tenth is the last before the next term.
CLAUSES_MAX = 10
#: Phase B (#536): the places a policy is used besides a neighbor and an
#: export into an IGP. Each is "<scope>:<what>", which map_name() names the
#: way it names an IGP export's part, with the protocol whose routes its
#: map is handed (None: the daemon's own, so `from protocol` has nothing
#: to say there) and FRR's verdict on a route no term decided - what the
#: attach point does with a route the operator's terms did not mention:
#:
#: - bgp:default, the routes whose presence sends a neighbor the default
#:   route (`default-originate`): none, so a default is sent on a term's
#:   accept and not otherwise;
#: - bgp:network and bgp:aggregate, the route a `network` or an
#:   `aggregate-address` originates: announced, as with no policy;
#: - bgp:suppress, the more specific routes of an aggregate an accept
#:   holds back: none;
#: - bgp:advertise, the routes a conditional advertisement concerns, and
#:   bgp:condition, the routes whose presence it watches: none;
#: - bgp:table, BGP's best routes on their way to zebra: installed;
#: - ospf:default and isis:default, the routes whose presence originates
#:   the IGP's default: none;
#: - bgp:vrfimport (#537), the routes a VRF imports from another VRF or
#:   from the default table: none, Junos's vrf-import default - a leak
#:   carries what a term accepted and nothing else.
ATTACHES = {"bgp:default": ("bgp", "deny"),
            "bgp:network": ("bgp", "permit"),
            "bgp:aggregate": ("bgp", "permit"),
            "bgp:suppress": ("bgp", "deny"),
            "bgp:advertise": ("bgp", "deny"),
            "bgp:condition": ("bgp", "deny"),
            "bgp:table": ("bgp", "permit"),
            "ospf:default": (None, "deny"),
            "isis:default": (None, "deny"),
            "bgp:vrfimport": ("bgp", "deny")}
#: How each attach point is said where a policy is listed (`show policy`)
#: and refused.
ATTACH_WORDS = {"bgp:default": "default-originate",
                "bgp:network": "network",
                "bgp:aggregate": "aggregate-address policy",
                "bgp:suppress": "aggregate-address suppress-policy",
                "bgp:advertise": "advertise",
                "bgp:condition": "advertise condition",
                "bgp:table": "table-policy",
                "ospf:default": "ospf default-information",
                "isis:default": "isis default-information",
                "bgp:vrfimport": "vrf-import"}
#: The protocols a FIB policy is per (#536): zebra's `ip protocol` word
#: for each, and its `ipv6 protocol` one. A `fib:<protocol>` use's map sees
#: that protocol's routes, and a route no term decided is installed.
FIB_PROTOCOLS = {"bgp": "bgp", "ospf": "ospf6", "isis": "isis",
                 "static": "static"}
#: What a use's map may set, where it is not every action. Zebra's FIB
#: maps take `set src` and nothing else; FRR applies `table-map`'s metric
#: and next hop and no other set; and on a map that only matches - which
#: routes are suppressed, advertised on a condition or watched - a set
#: changes nothing. A leak (#537) takes what describes the route in the VRF
#: that imports it - its preference, MED, origin, weight and communities -
#: and not a next hop, which is the exporting VRF's and is what carries the
#: route's traffic there, nor an extended community, the route targets
#: FRR's own leak is made of.
USE_SETS = {"fib": ("src",),
            "bgp:table": ("metric", "next-hop"),
            "bgp:suppress": (),
            "bgp:advertise": (),
            "bgp:condition": (),
            "bgp:vrfimport": ("local-preference", "community",
                              "large-community", "med", "metric", "origin",
                              "weight")}


class Term:
    """One term of a policy-statement, as its lines said it."""

    def __init__(self, name):
        self.name = name
        self.prefix_lists = []
        self.filters = []        # (network, kind, upto length or None)
        self.communities = []
        self.as_paths = []
        self.protocols = []      # FRR's words; empty is every protocol
        self.tags = []
        self.metrics = []
        # Phase A (#535). Each ORed within its kind, as every `from` is.
        self.neighbors = []      # peer addresses
        self.families = []       # "inet" | "inet6"; empty is both
        self.origins = []
        self.local_prefs = []
        self.next_hops = []
        self.large_communities = []
        self.ext_communities = []
        self.validation = []     # Junos's words: valid | invalid | unknown
        self.policies = []       # `from policy <name>`: at most one
        # Phase C (#537): the VRF a leaked route comes from; only at a
        # vrf-import, the one place a route has one.
        self.source_vrfs = []
        self.sets = []           # (what, [args])
        # "accept" | "reject" | "next term" | "next policy" | None
        self.then = None


class Policies:
    """Everything `policy-options` holds."""

    def __init__(self):
        self.prefix_lists = {}   # name -> [network, ...] in the order typed
        self.communities = {}    # name -> [member, ...]
        self.as_paths = {}       # name -> regex
        self.statements = {}     # name -> {term name: Term}, in order
        # Phase A (#535).
        self.large_communities = {}  # name -> ["asn:v1:v2", ...]
        self.ext_communities = {}    # name -> [("rt"|"soo", "asn:v"), ...]
        self.defaults = {}           # policy -> "accept" | "reject"


#: A VRF name as Linux takes it - an interface name, fifteen characters -
#: or `default`, FRR's word for the default table (#537).
VRF_NAME = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,14}"


def vrf_name_error(name):
    """Why `name` cannot be a VRF's name, or None (#537)."""
    if not re.fullmatch(VRF_NAME, name):
        return (f"'{name}' is not a VRF name: letters, digits, '_', '.' and "
                f"'-', at most 15")
    return None


def name_error(kind, name):
    """Why `name` cannot name a `kind`, or None."""
    if not name or not NAME_RE.fullmatch(name) or len(name) > NAME_MAX:
        return (f"{kind} '{name}': a name is letters, digits, '.', '_' and "
                f"'-', at most {NAME_MAX} characters")
    return None


def network_error(text):
    """Why `text` is not a network in canonical form, or None."""
    try:
        n = ipaddress.ip_network(text, strict=True)
    except ValueError:
        return f"'{text}' is not a network (address/length, host bits zero)"
    del n
    return None


def community_error(member):
    """Why `member` is not a BGP community (ASN:value), or None."""
    m = re.fullmatch(r"(\d+):(\d+)", member)
    if not m or int(m.group(1)) > 65535 or int(m.group(2)) > 65535:
        return f"'{member}' is not a community: ASN:value, each 0-65535"
    return None


def large_community_error(member):
    """Why `member` is not a large community (ASN:value:value), or None."""
    m = re.fullmatch(r"(\d+):(\d+):(\d+)", member)
    if not m or any(int(g) > U32 for g in m.groups()):
        return (f"'{member}' is not a large community: ASN:value:value, "
                f"each 0-{U32}")
    return None


def ext_community_error(member):
    """
    Why `member` is not an extended community the compiler writes - rt or
    soo, then ASN:value or address:value - or None.
    """
    kind, _, val = member.partition(":")
    why = (f"'{member}' is not an extended community: rt:<asn>:<value> or "
           f"soo:<asn>:<value>, or an address in place of the ASN")
    if kind not in EXT_KINDS:
        return why
    head, _, tail = val.rpartition(":")
    if not head or not re.fullmatch(r"\d+", tail):
        return why
    try:
        ipaddress.IPv4Address(head)
        return why if int(tail) > 65535 else None
    except ValueError:
        pass
    if not re.fullmatch(r"\d+", head) or int(head) > U32:
        return why
    # A 2-byte ASN leaves 32 bits for the value, a 4-byte one 16 (RFC 4360,
    # RFC 5668): FRR refuses the rest.
    top = U32 if int(head) <= 65535 else 65535
    return why if int(tail) > top else None


def parse(lines):
    """
    (Policies, [(line, why)]) from the model's flat lines. Every line starts
    `policy-options `; one that cannot be read is returned with why, and the
    rest are still read.
    """
    pol = Policies()
    bad = []
    for line in lines:
        w = line.split()
        why = _parse_one(pol, w[1:] if w[:1] == ["policy-options"] else [])
        if why:
            bad.append((line, why))
    return pol, bad


def _parse_one(pol, w):
    """One statement into `pol`; why it could not be, or None."""
    if not w:
        return "not a policy-options statement"
    # A block opened with nothing in it yet: the level an operator enters.
    if w[0] == "prefix-list" and len(w) == 2:
        why = name_error("prefix-list", w[1])
        if not why:
            pol.prefix_lists.setdefault(w[1], [])
        return why
    if w[0] == "policy-statement" and len(w) == 2:
        why = name_error("policy-statement", w[1])
        if not why:
            pol.statements.setdefault(w[1], {})
        return why
    if w[0] == "prefix-list" and len(w) == 3:
        why = name_error("prefix-list", w[1]) or network_error(w[2])
        if why:
            return why
        pol.prefix_lists.setdefault(w[1], [])
        if w[2] not in pol.prefix_lists[w[1]]:
            pol.prefix_lists[w[1]].append(w[2])
        return None
    if w[0] == "community" and len(w) >= 4 and w[2] == "members":
        why = name_error("community", w[1])
        for m in w[3:]:
            why = why or community_error(m)
        if why:
            return why
        pol.communities[w[1]] = list(w[3:])
        return None
    if w[0] == "large-community" and len(w) >= 4 and w[2] == "members":
        why = name_error("large-community", w[1])
        for m in w[3:]:
            why = why or large_community_error(m)
        if why:
            return why
        pol.large_communities[w[1]] = list(w[3:])
        return None
    if w[0] == "extended-community" and len(w) >= 4 and w[2] == "members":
        why = name_error("extended-community", w[1])
        for m in w[3:]:
            why = why or ext_community_error(m)
        if why:
            return why
        pol.ext_communities[w[1]] = [tuple(m.split(":", 1)) for m in w[3:]]
        return None
    if w[0] == "policy-statement" and len(w) == 4 \
            and w[2] == "default-action":
        why = name_error("policy-statement", w[1])
        if why:
            return why
        if w[3] not in ("accept", "reject"):
            return "default-action: accept or reject"
        pol.statements.setdefault(w[1], {})
        pol.defaults[w[1]] = w[3]
        return None
    if w[0] == "as-path" and len(w) >= 3:
        why = name_error("as-path", w[1])
        if why:
            return why
        regex = " ".join(w[2:]).strip('"')
        try:
            re.compile(regex.replace("_", "(^|[ ,]|$)"))
        except re.error as e:
            return f"as-path '{w[1]}': the expression does not compile: {e}"
        pol.as_paths[w[1]] = regex
        return None
    if w[0] == "policy-statement" and len(w) >= 4 and w[2] == "term":
        why = name_error("policy-statement", w[1]) or name_error("term", w[3])
        if why:
            return why
        terms = pol.statements.setdefault(w[1], {})
        term = terms.setdefault(w[3], Term(w[3]))
        return _parse_term(term, w[4:])
    return ("expected: prefix-list, community, large-community, "
            "extended-community, as-path or policy-statement")


def _parse_term(term, w):
    """A term's own statement; why it cannot be read, or None."""
    if not w:
        return None          # the term opened, with nothing in it yet
    if w[0] == "from" and len(w) >= 3:
        if w[1] == "prefix-list" and len(w) == 3:
            term.prefix_lists.append(w[2])
            return None
        if w[1] == "route-filter" and len(w) >= 4:
            why = network_error(w[2])
            if why:
                return why
            kind = w[3]
            if kind not in FILTER_KINDS:
                return ("route-filter: exact, orlonger, longer or "
                        "upto /<length>")
            upto = None
            if kind == "upto":
                if len(w) != 5 or not re.fullmatch(r"/?\d+", w[4]):
                    return "route-filter ... upto /<length>"
                upto = int(w[4].lstrip("/"))
                net = ipaddress.ip_network(w[2])
                if not net.prefixlen <= upto <= net.max_prefixlen:
                    return (f"route-filter {w[2]} upto /{upto}: the length "
                            f"is between {net.prefixlen} and "
                            f"{net.max_prefixlen}")
            elif len(w) != 4:
                return f"route-filter <network> {kind}, and nothing after"
            term.filters.append((w[2], kind, upto))
            return None
        if w[1] == "community" and len(w) == 3:
            term.communities.append(w[2])
            return None
        if w[1] == "as-path" and len(w) == 3:
            term.as_paths.append(w[2])
            return None
        if w[1] == "protocol" and len(w) == 3:
            proto = PROTOCOL_ALIASES.get(w[2], w[2])
            if proto not in PROTOCOLS:
                return ("from protocol: bgp, ospf, isis, static, direct "
                        "or kernel")
            if proto not in term.protocols:
                term.protocols.append(proto)
            return None
        if w[1] in ("tag", "metric") and len(w) == 3:
            if not re.fullmatch(r"\d+", w[2]) or int(w[2]) > U32:
                return f"from {w[1]} <0-{U32}>"
            (term.tags if w[1] == "tag" else term.metrics).append(w[2])
            return None
        return _parse_from_a(term, w)
    if w[0] == "set" and len(w) >= 3:
        # A local preference may be added to or taken from (#535): FRR's
        # `set local-preference +n|-n`.
        if w[1] == "local-preference" and len(w) == 3 \
                and re.fullmatch(r"[+-]?\d+", w[2]) \
                and int(w[2].lstrip("+-")) <= 4294967295:
            term.sets.append(("local-preference", [w[2]]))
            return None
        if w[1] == "community" and len(w) == 4 and w[2] in COMMUNITY_OPS:
            term.sets.append(("community", [w[2], w[3]]))
            return None
        if w[1] == "as-path-prepend" and w[2] == "last-as":
            if len(w) != 4 or not re.fullmatch(r"\d+", w[3]) \
                    or not 1 <= int(w[3]) <= 10:
                return "set as-path-prepend last-as <1-10>"
            term.sets.append(("as-path-prepend-last-as", [w[3]]))
            return None
        if w[1] == "as-path-prepend" and all(
                re.fullmatch(r"\d+", a) and 0 < int(a) <= 4294967295
                for a in w[2:]):
            term.sets.append(("as-path-prepend", list(w[2:])))
            return None
        if w[1] in ("metric", "tag") and len(w) == 3 \
                and re.fullmatch(r"\d+", w[2]) and int(w[2]) <= U32:
            term.sets.append((w[1], [w[2]]))
            return None
        if w[1] == "next-hop" and len(w) == 3 \
                and w[2] in ("self", "peer-address", "discard"):
            term.sets.append(("next-hop", [w[2]]))
            return None
        if w[1] == "next-hop" and len(w) == 3:
            try:
                ipaddress.ip_address(w[2])
            except ValueError:
                return f"set next-hop: '{w[2]}' is not an address"
            term.sets.append(("next-hop", [w[2]]))
            return None
        return _parse_set_a(term, w)
    if w[0] == "then" and w[1:] in (["accept"], ["reject"], ["next", "term"],
                                    ["next", "policy"]):
        term.then = " ".join(w[1:])
        return None
    if w[0] == "then":
        # Junos puts actions after `then`; here the actions are `set`, and
        # there is one spelling, as #436 decided for the file and the prompt.
        return ("then: accept, reject, next term or next policy; a change "
                "to the route is 'set ...'")
    return "a term takes 'from ...', 'set ...' and 'then ...'"


def _address(text):
    """`text` as an address, or None."""
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def _parse_from_a(term, w):
    """
    The `from` conditions phase A added (#535); why one cannot be read, or
    None. Each kind ORs with itself, as every `from` does.
    """
    what, args = w[1], w[2:]
    one = len(args) == 1
    if what == "neighbor" and one:
        if _address(args[0]) is None:
            return f"from neighbor: '{args[0]}' is not an address"
        term.neighbors.append(args[0])
        return None
    if what == "family" and one:
        if args[0] not in ("inet", "inet6"):
            return "from family: inet or inet6"
        if args[0] not in term.families:
            term.families.append(args[0])
        return None
    if what == "origin" and one:
        if args[0] not in ORIGINS:
            return "from origin: igp, egp or incomplete"
        term.origins.append(args[0])
        return None
    if what == "local-preference" and one:
        if not re.fullmatch(r"\d+", args[0]) or int(args[0]) > U32:
            return f"from local-preference <0-{U32}>"
        term.local_prefs.append(args[0])
        return None
    if what == "next-hop" and one:
        if _address(args[0]) is None:
            return f"from next-hop: '{args[0]}' is not an address"
        term.next_hops.append(args[0])
        return None
    if what == "large-community" and one:
        term.large_communities.append(args[0])
        return None
    if what == "extended-community" and one:
        term.ext_communities.append(args[0])
        return None
    if what == "validation-state" and one:
        if args[0] not in VALIDATION_STATES:
            return "from validation-state: valid, invalid or unknown"
        term.validation.append(args[0])
        return None
    if what == "source-vrf" and one:
        why = vrf_name_error(args[0])
        if why:
            return f"from source-vrf: {why}"
        if args[0] not in term.source_vrfs:
            term.source_vrfs.append(args[0])
        return None
    if what == "policy" and one:
        why = name_error("policy-statement", args[0])
        if why:
            return why
        # FRR calls one route-map from a clause, and a second `call`
        # replaces the first: two would be one, silently.
        if term.policies and term.policies != [args[0]]:
            return "from policy: a term calls one policy"
        term.policies = [args[0]]
        return None
    return ("from: prefix-list, route-filter, community, large-community, "
            "extended-community, as-path, protocol, tag, metric, neighbor, "
            "family, origin, local-preference, next-hop, validation-state, "
            "source-vrf or policy")


def _parse_set_a(term, w):
    """The `set` actions phase A added (#535); why one cannot be, or None."""
    what, args = w[1], w[2:]
    if what == "origin" and len(args) == 1 and args[0] in ORIGINS:
        term.sets.append(("origin", list(args)))
        return None
    if what == "origin":
        return "set origin: igp, egp or incomplete"
    if what == "weight":
        if len(args) != 1 or not re.fullmatch(r"\d+", args[0]) \
                or int(args[0]) > 65535:
            return "set weight <0-65535>"
        term.sets.append(("weight", list(args)))
        return None
    if what == "med":
        if len(args) != 1 or not (args[0] == "igp" or (
                re.fullmatch(r"[+-]?\d+", args[0])
                and int(args[0].lstrip("+-")) <= U32)):
            return "set med <n>|+<n>|-<n>|igp"
        term.sets.append(("med", list(args)))
        return None
    if what == "large-community":
        if len(args) != 2 or args[0] not in COMMUNITY_OPS:
            return "set large-community add|set|delete <name>"
        term.sets.append(("large-community", list(args)))
        return None
    if what == "extended-community":
        # No delete: FRR 10.7.1 has no `set extcomm-list ... delete` -
        # measured, vtysh -C refuses it.
        if len(args) != 2 or args[0] not in ("add", "set"):
            return "set extended-community add|set <name>"
        term.sets.append(("extended-community", list(args)))
        return None
    if what == "as-path" and args == ["remove-private"]:
        term.sets.append(("as-path-remove-private", []))
        return None
    if what == "as-path":
        return "set as-path remove-private"
    if what == "metric-type":
        if args not in (["1"], ["2"]):
            return "set metric-type 1|2"
        term.sets.append(("metric-type", list(args)))
        return None
    # Phase B (#536): the source address a FIB policy gives the kernel's
    # route, for what this box itself sends along it.
    if what == "src":
        if len(args) != 1 or _address(args[0]) is None:
            return "set src <address>"
        term.sets.append(("src", list(args)))
        return None
    return ("set: local-preference, community, large-community, "
            "extended-community, as-path-prepend, as-path, med, metric, "
            "tag, weight, origin, next-hop, metric-type or src")


def findings(pol):
    """
    [(policy or None, why)] for what the compiler cannot compile: a name a
    term uses that nothing defines, and a statement with no term.
    """
    out = []
    for pname, terms in pol.statements.items():
        if not terms:
            out.append((pname, f"policy-statement {pname} has no term"))
        for term in terms.values():
            for n in term.prefix_lists:
                if n not in pol.prefix_lists:
                    out.append((pname, f"policy-statement {pname} term "
                                       f"{term.name}: no prefix-list {n}"))
            for n in term.communities:
                if n not in pol.communities:
                    out.append((pname, f"policy-statement {pname} term "
                                       f"{term.name}: no community {n}"))
            for n in term.as_paths:
                if n not in pol.as_paths:
                    out.append((pname, f"policy-statement {pname} term "
                                       f"{term.name}: no as-path {n}"))
            for what, args in term.sets:
                if what == "community" and args[1] not in pol.communities:
                    out.append((pname, f"policy-statement {pname} term "
                                       f"{term.name}: no community {args[1]}"))
            out += [(pname, f"policy-statement {pname} term {term.name}: "
                            f"{why}") for why in _term_findings_a(pol, term)]
    out += [(None, why) for why in _call_cycles(pol)]
    out += _reject_calls(pol)
    return out


def _term_findings_a(pol, term):
    """What phase A's conditions and actions name and nothing defines (#535)."""
    out = []
    for n in term.large_communities:
        if n not in pol.large_communities:
            out.append(f"no large-community {n}")
    for n in term.ext_communities:
        if n not in pol.ext_communities:
            out.append(f"no extended-community {n}")
    for n in term.policies:
        if n not in pol.statements:
            out.append(f"no policy-statement {n}")
    for what, args in term.sets:
        if what == "large-community" and args[1] not in pol.large_communities:
            out.append(f"no large-community {args[1]}")
        if what == "extended-community" \
                and args[1] not in pol.ext_communities:
            out.append(f"no extended-community {args[1]}")
    if len(_combinations(term)) * max(1, len(term.families or ["x"])) \
            > CLAUSES_MAX:
        out.append(f"more than {CLAUSES_MAX} combinations of neighbor, "
                   f"origin, local-preference, next-hop, validation-state "
                   f"and source-vrf; split it into terms")
    return out


def _call_cycles(pol):
    """
    A policy that reaches itself through `from policy` (#535): FRR would
    call route-maps in a circle. One sentence per policy in a cycle.
    """
    calls = {p: {n for t in terms.values() for n in t.policies}
             for p, terms in pol.statements.items()}
    out = []
    for start in sorted(calls):
        seen, todo = set(), list(calls[start])
        while todo:
            n = todo.pop()
            if n == start:
                out.append(f"policy-statement {start}: from policy reaches "
                           f"itself again")
                break
            if n in seen:
                continue
            seen.add(n)
            todo += calls.get(n, ())
    return out


def _reject_calls(pol):
    """
    A reject term whose `from policy` FRR cannot run (#542): [(policy,
    why)]. Such a term rejects what the called policy accepts and lets the
    rest go on, which compiles to a call of the policy INVERTED - its
    accept a deny, its reject a permit, inverted_map_name(). A clause's
    `call` has one terminal branch, the called map's deny, so inside the
    inverted map a term with `from policy` can only be an accept term (its
    call inverted in turn: the inner policy's accept is the outer one's).
    A reject, next term or next policy term with `from policy` would need
    a call whose deny goes on, which FRR has no word for.
    """
    out = []
    for pname, terms in pol.statements.items():
        for term in terms.values():
            if term.then != "reject":
                continue
            for x in term.policies:
                why = _not_invertible(pol, x, set())
                if why:
                    out.append((pname, f"policy-statement {pname} term "
                                       f"{term.name}: then reject with from "
                                       f"policy {x}, and {why}; FRR can "
                                       f"call a policy inverted only when "
                                       f"every term of it with from policy "
                                       f"is an accept term"))
    return out


def _not_invertible(pol, policy, seen):
    """Why `policy` cannot be called inverted (#542), or None."""
    seen.add(policy)
    for term in pol.statements.get(policy, {}).values():
        if not term.policies:
            continue
        if term.then != "accept":
            return (f"{policy} term {term.name} has from policy and then "
                    f"{term.then or 'next term'}")
        for y in term.policies:
            if y not in seen:
                why = _not_invertible(pol, y, seen)
                if why:
                    return why
    return None


def _combinations(term):
    """
    The single-value conditions of a term as FRR clauses take them: FRR
    holds one `match peer`, one `match origin` and so on per clause, and a
    second replaces the first - so the ORed values of these kinds are one
    clause per combination. [(kind, value), ...] per clause; [[]] when the
    term has none.
    """
    combos = [[]]
    for kind, vals in (("peer", term.neighbors), ("origin", term.origins),
                       ("local-preference", term.local_prefs),
                       ("next-hop", term.next_hops),
                       ("rpki", term.validation),
                       ("source-vrf", term.source_vrfs)):
        if vals:
            combos = [c + [(kind, v)] for c in combos for v in vals]
    return combos


def nest(flat):
    """
    The model's flat `policy-options ...` lines as the block a file carries
    and an operator types, in the order the lines came. The inverse of
    unnest(); the one writer of the block.
    """
    pls, others, stmts = {}, [], {}
    for line in flat:
        w = line.split()[1:]
        if w[:1] == ["prefix-list"] and len(w) >= 2:
            pls.setdefault(w[1], [])
            if len(w) == 3:
                pls[w[1]].append(w[2])
        elif w[:1] == ["policy-statement"] and len(w) >= 2:
            terms = stmts.setdefault(w[1], {})
            if len(w) == 4 and w[2] == "default-action":
                # The policy's own statement (#535), written before its
                # terms; keyed so no term name can collide with it.
                terms[("default-action",)] = [w[3]]
            elif len(w) >= 4 and w[2] == "term":
                terms.setdefault(w[3], [])
                if len(w) > 4:
                    terms[w[3]].append(" ".join(w[4:]))
        elif w:
            others.append(" ".join(w))
    if not (pls or others or stmts):
        return []
    out = ["policy-options"]
    for name, nets in pls.items():
        out += [f" prefix-list {name}"] + [f"  {n}" for n in nets] + ["  exit"]
    out += [f" {o}" for o in others]
    for name, terms in stmts.items():
        out.append(f" policy-statement {name}")
        if ("default-action",) in terms:
            out.append(f"  default-action {terms[('default-action',)][0]}")
        for term, body in terms.items():
            if term == ("default-action",):
                continue
            out += ([f"  term {term}"] + [f"   {b}" for b in body]
                    + ["   exit"])
        out.append("  exit")
    return out + [" exit"]


def unnest(numbered):
    """
    The `policy-options` block of a file as the model's flat lines: (the
    numbered lines with the block rewritten, [(lineno, statement, why)]).
    Lines outside the block pass through untouched.
    """
    out, bad = [], []
    depth, where = 0, []
    for n, raw in numbered:
        s = raw.strip()
        if depth == 0:
            if s == "policy-options":
                depth, where = 1, []
            else:
                out.append((n, raw))
            continue
        if not s or s.startswith("!"):
            continue
        if s in ("exit", "quit", "end"):
            depth -= 1
            where = where[:max(0, depth - 1)]
            continue
        if depth == 1:
            w = s.split()
            if w[0] in ("prefix-list", "policy-statement") and len(w) == 2:
                out.append((n, f"policy-options {s}"))
                depth, where = 2, [s]
            else:
                out.append((n, f"policy-options {s}"))
        elif depth == 2 and where[0].startswith("prefix-list "):
            out.append((n, f"policy-options {where[0]} {s}"))
        elif depth == 2:
            w = s.split()
            if w[0] == "term" and len(w) == 2:
                out.append((n, f"policy-options {where[0]} {s}"))
                depth, where = 3, [where[0], s]
            elif w[0] == "default-action" and len(w) == 2:
                out.append((n, f"policy-options {where[0]} {s}"))
            else:
                bad.append((n, s, "under a policy-statement there are "
                                  "terms, 'term <name>'"))
        else:
            out.append((n, f"policy-options {where[0]} {where[1]} {s}"))
    return out, bad


def map_name(policy, direction):
    """
    The FRR route-map a policy becomes when used in `direction`: `import`
    or `export` on a BGP neighbor, `<igp>:<protocol>` for the part of an
    export into an IGP that redistributes from one protocol, and phase B's
    (#536) `fib:<protocol>` and ATTACHES, named the same way.
    """
    if direction in DIRECTIONS:
        return f"{PREFIX}{policy}-{DIRECTIONS[direction]}"
    igp, proto = direction.split(":")
    return f"{PREFIX}{policy}-{igp.upper()}-{proto.upper()}"


def use_origin(direction):
    """
    The protocol whose routes the map of a use sees (#536): BGP's on a
    neighbor and at BGP's attach points, the one an IGP export's part or a
    FIB policy is for, and None where the daemon hands the map routes of
    its own choosing - an IGP's default origination.
    """
    if direction in DIRECTIONS:
        return "bgp"
    if direction in ATTACHES:
        return ATTACHES[direction][0]
    return direction.split(":")[1]


def use_default(direction):
    """
    FRR's verdict, permit or deny, on a route no term of a use decided: the
    Junos default for a neighbor (accept) and an IGP export (reject), an
    attach point's own (ATTACHES), and a FIB policy's, installed (#536).
    """
    if direction in DIRECTIONS:
        return "permit"
    if direction in ATTACHES:
        return ATTACHES[direction][1]
    return "permit" if direction.startswith("fib:") else "deny"


def sub_map_name(policy, direction):
    """
    The FRR route-map `from policy <policy>` calls (#535): the policy on
    its own, with its default, for the same use as the map that calls it.
    """
    full = map_name(policy, direction)
    return f"{PREFIX}{policy}-SUB-{full[len(PREFIX) + len(policy) + 1:]}"


def inverted_map_name(policy, direction):
    """
    The FRR route-map a reject term's `from policy <policy>` calls (#542):
    the policy on its own, inverted - what it accepts denied, what it
    rejects permitted - so the term rejects exactly what the policy accepts.
    """
    full = map_name(policy, direction)
    return f"{PREFIX}{policy}-NOT-{full[len(PREFIX) + len(policy) + 1:]}"


def called_maps(pol, policies):
    """
    The maps a use of `policies` calls through `from policy` (#542):
    [(policy, inverted)] in the order first met. A reject term calls its
    policy inverted, and inside an inverted map an accept term calls its
    own inverted too; every other call is the policy's own map. Without a
    reject term calling one, this is reached()'s policies after the first,
    each not inverted.
    """
    out, todo = [], [(p, False, True) for p in policies]
    while todo:
        p, inv, top = todo.pop(0)
        if not top:
            if (p, inv) in out:
                continue
            out.append((p, inv))
        for term in pol.statements.get(p, {}).values():
            flip = term.then == "reject" or (inv and term.then == "accept")
            todo += [(y, flip, False) for y in term.policies]
    return out


def export_lines(policy, igp):
    """
    The `redistribute` statements an `export <policy>` inside `router
    <igp>` stands for: one per protocol the IGP can take routes from, each
    with the route-map compiled for that protocol. Every protocol, not only
    the ones the policy names today, so a term added to the policy later
    applies without the export being typed again; a protocol no term
    accepts gets a map whose only clause is the default, reject.
    """
    out = []
    for proto in IGPS[igp]:
        rmap = map_name(policy, f"{igp}:{proto}")
        if igp == "ospf":
            out.append(f" redistribute {proto} route-map {rmap}")
        else:
            for fam, src in (("ipv4", proto), ("ipv6",
                             "ospf6" if proto == "ospf" else proto)):
                for level in ("level-1", "level-2"):
                    out.append(f" redistribute {fam} {src} {level} "
                               f"route-map {rmap}")
    return out


#: The actions only a BGP route takes.
BGP_SETS = ("local-preference", "community", "as-path-prepend", "next-hop",
            "as-path-prepend-last-as", "origin", "weight", "med",
            "large-community", "extended-community", "as-path-remove-private")


def igp_findings(pol, policy):
    """
    Why `policy` cannot be exported into an IGP, one reason per term that
    says something only a BGP route has, or [] when it can. Refused at the
    export rather than compiled into a map that would ignore it.
    """
    out = []
    for term in pol.statements.get(policy, {}).values():
        bgp_only = []
        if term.communities:
            bgp_only.append("from community")
        if term.as_paths:
            bgp_only.append("from as-path")
        # Phase A (#535): what only a BGP route carries, or only BGP does.
        for kind, vals in (("neighbor", term.neighbors),
                           ("origin", term.origins),
                           ("local-preference", term.local_prefs),
                           ("large-community", term.large_communities),
                           ("extended-community", term.ext_communities),
                           ("validation-state", term.validation),
                           ("source-vrf", term.source_vrfs)):
            if vals:
                bgp_only.append(f"from {kind}")
        bgp_only += [f"set {_set_word(what)}" for what, _ in term.sets
                     if what in BGP_SETS]
        if bgp_only:
            out.append(f"term {term.name}: {', '.join(bgp_only)} is for "
                       f"BGP routes")
    return out


def _set_word(what):
    """The `set` a model's action was typed as: the word after `set`."""
    return {"as-path-prepend-last-as": "as-path-prepend last-as",
            "as-path-remove-private": "as-path remove-private"}.get(what,
                                                                    what)


def chain(name):
    """The policies a use names, in order: one, or a chain's several."""
    return name.split(CHAIN_SEP)


def chain_name(policies):
    """The one name a chain of policies is used under (#535)."""
    return CHAIN_SEP.join(policies)


def reached(pol, policies):
    """
    Every policy a use evaluates: the ones it names, and every one their
    terms call through `from policy`, in the order first met.
    """
    out, todo = [], list(policies)
    while todo:
        p = todo.pop(0)
        if p in out:
            continue
        out.append(p)
        for term in pol.statements.get(p, {}).values():
            todo += term.policies
    return out


def use_findings(pol, name, direction):
    """
    Why the policies of `name` - one, or a chain - cannot be used in
    `direction`, one reason per term, or [] when they can (#535). An IGP
    export takes nothing only BGP has; `set metric-type` is for an export
    into OSPF; `set next-hop self` says this box, so it is for an export,
    and `peer-address` says the neighbour, so for an import. Refused where
    the policy is applied, rather than compiled into a map that would
    ignore what the operator's term was written to do.

    Phase B (#536): a FIB policy and an IGP's default origination take
    nothing only BGP has either; `set src` is for a FIB policy, and a FIB
    policy sets nothing else; an attach point that only matches sets
    nothing (USE_SETS); and where the daemon chooses the routes, `from
    protocol` has nothing to match.
    """
    out = []
    scope = direction.split(":")[0]
    igp = scope if direction not in DIRECTIONS \
        and scope in IGPS or scope == "fib" else None
    sets_ok = USE_SETS.get(direction, USE_SETS.get(scope))
    where = ATTACH_WORDS.get(direction, "a fib-policy" if scope == "fib"
                             else None)
    for p in reached(pol, chain(name)):
        if igp:
            out += [f"{p} {why}" for why in igp_findings(pol, p)]
        for term in pol.statements.get(p, {}).values():
            # Phase C (#537): a route has a source VRF only where it is
            # leaked, and arrives there from no neighbor of this VRF's.
            if term.source_vrfs and direction != "bgp:vrfimport" \
                    and not igp:
                out.append(f"{p} term {term.name}: from source-vrf is for "
                           f"a vrf-import, where a route is leaked")
            if term.neighbors and direction == "bgp:vrfimport":
                out.append(f"{p} term {term.name}: from neighbor matches "
                           f"nothing on vrf-import: a leaked route comes "
                           f"from a VRF, which from source-vrf names")
            if term.protocols and use_origin(direction) is None:
                out.append(f"{p} term {term.name}: from protocol has nothing "
                           f"to match on {where}: the daemon chooses the "
                           f"routes it looks at")
            for what, args in term.sets:
                if igp and what in BGP_SETS:
                    continue        # said by igp_findings()
                if sets_ok is not None and what not in sets_ok:
                    out.append(f"{p} term {term.name}: set {_set_word(what)} "
                               f"does nothing on {where}"
                               + (f": it takes set {' and set '.join(sets_ok)}"
                                  if sets_ok else ""))
                    continue
                if what == "src" and scope != "fib":
                    out.append(f"{p} term {term.name}: set src is for a "
                               f"fib-policy")
                if what == "metric-type" and scope != "ospf":
                    out.append(f"{p} term {term.name}: set metric-type is "
                               f"for an export into OSPF")
                if igp:
                    continue        # a next hop is BGP's, said above
                if what == "next-hop" and args == ["self"] \
                        and direction != "export":
                    out.append(f"{p} term {term.name}: set next-hop self is "
                               f"for an export to a BGP neighbor")
                if what == "next-hop" and args == ["peer-address"] \
                        and direction != "import":
                    out.append(f"{p} term {term.name}: set next-hop "
                               f"peer-address is for an import")
    return out


def _filter_entry(network, kind, upto):
    """The prefix-list entry for one route-filter, after `permit`."""
    net = ipaddress.ip_network(network)
    top = net.max_prefixlen
    if kind == "exact":
        return network
    if kind == "orlonger":
        return f"{network} le {top}" if net.prefixlen < top else network
    if kind == "longer":
        if net.prefixlen >= top:
            return None      # nothing is longer than a host route
        return f"{network} ge {net.prefixlen + 1} le {top}"
    if upto == net.prefixlen:
        return network
    return f"{network} le {upto}"


def _family(network):
    return "ipv6" if ":" in network else "ip"


def compile_policy(pol, policy, direction, sub=False, inverted=False):
    """
    The FRR statements that make `policy` in `direction`: its generated
    lists, then its route-map, then the Junos default. Nothing that another
    use of another policy would emit, so two uses compile side by side.

    `policy` may be a chain, `A+B` (#535): one route-map whose clauses are
    each policy's in turn, policy k's from k x BLOCK, so a route no term of A
    decided goes on to B's, `then next policy` jumps there, and a policy's
    own `default-action` is a clause at the end of its block. One policy on
    its own compiles exactly as before phase A. `sub` is the map a `from
    policy` term calls: the policy alone, under sub_map_name().

    `inverted` is the map a reject term's `from policy` calls (#542), the
    policy alone under inverted_map_name(): the same clauses, numbered the
    same, with every decision the other way - an accept term a deny
    clause, a reject term a permit that ends the map, both defaults
    reversed - and a term that decides nothing as it was, sets and all.
    """
    names = chain(policy)
    rmap = inverted_map_name(policy, direction) if inverted \
        else sub_map_name(policy, direction) if sub \
        else map_name(policy, direction)
    # Which protocol's routes this map sees: BGP's on a neighbor, and on an
    # export into an IGP the one its `redistribute` names. A term naming
    # other protocols never matches here, so it is left out rather than
    # compiled into a clause FRR would evaluate against the wrong routes.
    origin = use_origin(direction)
    lists, clauses = [], []
    for k, name in enumerate(names):
        offset = k * BLOCK if len(names) > 1 else 0
        # Where `then next policy` goes: the next policy's first clause, or
        # past every policy, to the default (FRR's on-match goto takes the
        # first clause at or after the number).
        nxt = (k + 1) * BLOCK if k + 1 < len(names) else DEFAULT_SEQ
        for pos, term in enumerate(pol.statements.get(name, {}).values(),
                                   start=1):
            got = _compile_term(pol, name, term, pos, offset, nxt, rmap,
                                origin, direction, inverted)
            if got:
                lists += got[0]
                clauses += got[1]
        if name in pol.defaults:
            last = (offset + BLOCK - 1) if len(names) > 1 \
                else DEFAULT_SEQ - 1
            action = "permit" if pol.defaults[name] == "accept" else "deny"
            if inverted:
                action = FLIP[action]
            clauses += [f"route-map {rmap} {action} {last}", "exit"]
    # THE JUNOS DEFAULT, which is not one default: a BGP route no term
    # decided is accepted (the maintainer chose it for #458), and a route no
    # term decided on its way into an IGP is not exported - Junos's OSPF and
    # IS-IS export nothing that no policy accepts. Phase B's attach points
    # (#536) each have their own: use_default().
    default = use_default(direction)
    if inverted:
        default = FLIP[default]
    clauses += [f"route-map {rmap} {default} {DEFAULT_SEQ}", "exit"]
    return lists + clauses


#: A clause's action in a map called inverted (#542).
FLIP = {"permit": "deny", "deny": "permit"}


def _compile_term(pol, policy, term, pos, offset, nxt, rmap, origin,
                  direction, inverted=False):
    """
    One term as (lists, clauses), or None when it matches nothing here.
    Its clauses are at offset + pos x 10 + 0..9: one per family its prefixes
    have, times one per combination of its single-value conditions.
    `inverted`: the term in a map called inverted (compile_policy()).
    """
    if term.protocols and origin and origin not in term.protocols:
        return None
    base = f"{PREFIX}{policy}-T{term.name}"
    lists = []
    # The prefix conditions of the term, ORed, into one list per family.
    per_family = {"ip": [], "ipv6": []}
    for n in term.prefix_lists:
        for net in pol.prefix_lists.get(n, []):
            per_family[_family(net)].append(net)
    for net, kind, upto in term.filters:
        entry = _filter_entry(net, kind, upto)
        if entry:
            per_family[_family(net)].append(entry)
    # `from family` (#535) keeps the prefixes of that family, and on a term
    # with no prefix condition is every prefix of it.
    allowed = [{"inet": "ip", "inet6": "ipv6"}[f]
               for f in term_families(term)]
    if allowed:
        for fam in ("ip", "ipv6"):
            if fam not in allowed:
                per_family[fam] = []
        if not (term.prefix_lists or term.filters):
            for fam in allowed:
                per_family[fam] = ["0.0.0.0/0 le 32" if fam == "ip"
                                   else "::/0 le 128"]
    families = [f for f in ("ip", "ipv6") if per_family[f]]
    # A TERM THAT ASKED FOR PREFIXES AND GOT NONE MATCHES NOTHING (#484).
    # Its lists are missing or empty, so no route is in them - and the
    # clause below, with no `match`, would match EVERY route. On a BGP
    # export that is the whole table announced, from one mistyped list
    # name; and `evaluate()` - `test policy` - said "no match" for the
    # same term, so the operator's own test contradicted FRR. The term is
    # left out: it matches nothing, as both of them now say.
    if (term.prefix_lists or term.filters or allowed) and not families:
        return None
    for fam in families:
        name = f"{base}-{'4' if fam == 'ip' else '6'}"
        for seq, entry in enumerate(per_family[fam], start=1):
            lists.append(f"{fam} prefix-list {name} seq {seq * 5} "
                         f"permit {entry}")
    comm = f"{base}-C" if term.communities else None
    if comm:
        for seq, n in enumerate(term.communities, start=1):
            lists.append(f"bgp community-list standard {comm} seq "
                         f"{seq * 5} permit "
                         + " ".join(pol.communities.get(n, [])))
    large = f"{base}-L" if term.large_communities else None
    if large:
        for seq, n in enumerate(term.large_communities, start=1):
            lists.append(f"bgp large-community-list standard {large} seq "
                         f"{seq * 5} permit "
                         + " ".join(pol.large_communities.get(n, [])))
    ext = f"{base}-E" if term.ext_communities else None
    if ext:
        for seq, n in enumerate(term.ext_communities, start=1):
            lists.append(f"bgp extcommunity-list standard {ext} seq "
                         f"{seq * 5} permit " + " ".join(
                             f"{k} {v}" for k, v in
                             pol.ext_communities.get(n, [])))
    aspath = f"{base}-A" if term.as_paths else None
    if aspath:
        for seq, n in enumerate(term.as_paths, start=1):
            lists.append(f"bgp as-path access-list {aspath} seq "
                         f"{seq * 5} permit {pol.as_paths.get(n, '')}")
    clauses = []
    # A term with prefixes of both families is two clauses, one each: a
    # single clause would AND `match ip` with `match ipv6` and match
    # nothing. A term with no prefix condition is one clause - times the
    # combinations of its single-value conditions (#535).
    # The clause's action. A term that decides is a deny where the route
    # ends rejected - a reject term, or an accept term in an inverted map -
    # unless it asks a `from policy`: FRR runs `call` only on a permit
    # clause (#542), so such a term is a permit whose call decides.
    ends = "reject" if not inverted else "accept"
    action = "deny" if term.then == ends and not term.policies \
        else "permit"
    # Which way the term calls its policy: a reject term asks whether the
    # policy accepts, which is its inverted map denying, and an accept term
    # in an inverted map asks the same of its own (_reject_calls()).
    flip = term.then == "reject" or (inverted and term.then == "accept")
    i = 0
    for fam in families or [None]:
        for combo in _combinations(term):
            clause = [f"route-map {rmap} {action} {offset + pos * 10 + i}"]
            i += 1
            if fam:
                name = f"{base}-{'4' if fam == 'ip' else '6'}"
                clause.append(f" match {fam} address prefix-list {name}")
            if comm:
                clause.append(f" match community {comm}")
            if large:
                clause.append(f" match large-community {large}")
            if ext:
                clause.append(f" match extcommunity {ext}")
            if aspath:
                clause.append(f" match as-path {aspath}")
            for kind, val in combo:
                if kind == "next-hop":
                    nh = "ipv6" if ":" in val else "ip"
                    clause.append(f" match {nh} next-hop address {val}")
                elif kind == "rpki":
                    clause.append(f" match rpki {VALIDATION_STATES[val]}")
                else:
                    clause.append(f" match {kind} {val}")
            # One value each: FRR takes one `match tag` and one `match
            # metric` per clause, and a second replaces the first.
            if term.tags:
                clause.append(f" match tag {term.tags[-1]}")
            if term.metrics:
                clause.append(f" match metric {term.metrics[-1]}")
            if action == "permit":
                # FRR's `call`, measured on 10.7.1 (#542): this clause's
                # sets first, then the called map, which sees them; its
                # deny denies the route, its permit goes on to this
                # clause's on-match - and a map no clause of matches denies.
                for p in term.policies:
                    name = inverted_map_name(p, direction) if flip \
                        else sub_map_name(p, direction)
                    clause.append(f" call {name}")
                # A reject term's sets are not for the route it lets go on.
                if term.then != "reject":
                    clause += _set_lines(pol, term, fam, direction)
                if term.then == "next policy":
                    clause.append(f" on-match goto {nxt}")
                elif term.then == "reject" and not inverted:
                    # The policy did not accept: the term did not match,
                    # and the route goes on to the next term (#542).
                    clause.append(" on-match next")
                elif term.then not in ("accept", "reject"):
                    clause.append(" on-match next")
            clauses += clause + ["exit"]
    return lists, clauses


def term_families(term):
    """
    The families a term is for, as `from family` says them: its own, or,
    on a term that sets a source address (#536), that address's - zebra
    cannot give an IPv6 route an IPv4 source, so `set src 10.0.0.1` is a
    term about IPv4 routes. [] is both.
    """
    if term.families:
        return term.families
    return sorted({"inet6" if ":" in args[0] else "inet"
                   for what, args in term.sets if what == "src"})


def _set_lines(pol, term, fam=None, direction="import"):
    """
    The FRR `set` lines of a term's actions, for a clause of family `fam`
    (None: a clause that sees both) in a map used in `direction`.
    """
    out = []
    for what, args in term.sets:
        if what == "local-preference":
            out.append(f" set local-preference {args[0]}")
        elif what == "as-path-prepend":
            out.append(f" set as-path prepend {' '.join(args)}")
        elif what == "community":
            op, name = args
            members = " ".join(pol.communities.get(name, []))
            if op == "add":
                out.append(f" set community {members} additive")
            elif op == "set":
                out.append(f" set community {members}")
            else:
                out.append(f" set comm-list {PREFIX}C-{name} delete")
        elif what in ("metric", "tag"):
            out.append(f" set {what} {args[0]}")
        elif what == "next-hop" and args[0] in ("self", "peer-address"):
            # FRR's one word for both (#535): on an outbound map
            # `peer-address` is this box's own address on the session, on an
            # inbound one the neighbour's. use_findings() keeps self to
            # exports and peer-address to imports.
            for f in ([fam] if fam else ["ip", "ipv6"]):
                out.append(f" set {f} next-hop peer-address")
        elif what == "next-hop" and args[0] == "discard":
            for f in ([fam] if fam else ["ip", "ipv6"]):
                out.append(f" set {f} next-hop "
                           f"{'global ' if f == 'ipv6' else ''}{DISCARD[f]}")
        elif what == "next-hop":
            nh = "ipv6" if ":" in args[0] else "ip"
            out.append(f" set {nh} next-hop "
                       f"{'global ' if nh == 'ipv6' else ''}{args[0]}")
        elif what in ("origin", "weight"):
            out.append(f" set {what} {args[0]}")
        elif what == "med":
            out.append(f" set metric {args[0]}")
        elif what == "as-path-prepend-last-as":
            out.append(f" set as-path prepend last-as {args[0]}")
        elif what == "as-path-remove-private":
            out.append(f" set as-path exclude as-path-access-list "
                       f"{PRIVATE_AS_LIST}")
        elif what == "large-community":
            op, name = args
            members = " ".join(pol.large_communities.get(name, []))
            if op == "add":
                out.append(f" set large-community {members} additive")
            elif op == "set":
                out.append(f" set large-community {members}")
            else:
                out.append(f" set large-comm-list {PREFIX}L-{name} delete")
        elif what == "extended-community":
            op, name = args
            members = pol.ext_communities.get(name, [])
            # FRR 10.7.1's `set extcommunity rt|soo` MERGES into what the
            # route carries, and takes no `additive`: vtysh -C passed the
            # word as one more value, bgpd refused the line, and the clause
            # ran with no set at all (lab/policy-a.sh, 2026-10-01). So add is
            # the plain statement, and set clears first - `none`, then the
            # values, which a pair of bgpd measured as the replacement.
            if op == "set":
                out.append(" set extcommunity none")
            for kind in EXT_KINDS:
                vals = [v for k, v in members if k == kind]
                if vals:
                    out.append(f" set extcommunity {kind} {' '.join(vals)}")
        elif what == "metric-type" and direction.startswith("ospf:"):
            out.append(f" set metric-type type-{args[0]}")
        elif what == "src" and fam in (None, _family(args[0])):
            out.append(f" set src {args[0]}")
    return out


def compile_all(pol, uses):
    """
    Every FRR statement for `uses`, a set of (policy, direction): the
    community-lists a `delete` names, then each use compiled. Sorted by use,
    so the same configuration always compiles to the same text.
    """
    out = []
    deleted = sorted({args[1] for terms in pol.statements.values()
                      for t in terms.values()
                      for what, args in t.sets
                      if what == "community" and args[0] == "delete"})
    for name in deleted:
        for seq, member in enumerate(pol.communities.get(name, []),
                                     start=1):
            out.append(f"bgp community-list standard {PREFIX}C-{name} seq "
                       f"{seq * 5} permit {member}")
    # Phase A (#535): the large-community lists a delete names, the
    # private-AS list remove-private excludes by, and the discard routes -
    # each emitted only when something uses it, so a configuration without
    # them compiles to the text it did before.
    sets = [(what, args) for terms in pol.statements.values()
            for t in terms.values() for what, args in t.sets]
    for name in sorted({a[1] for w, a in sets
                        if w == "large-community" and a[0] == "delete"}):
        for seq, member in enumerate(pol.large_communities.get(name, []),
                                     start=1):
            out.append(f"bgp large-community-list standard {PREFIX}L-{name} "
                       f"seq {seq * 5} permit {member}")
    if any(w == "as-path-remove-private" for w, _ in sets):
        out.append(f"bgp as-path access-list {PRIVATE_AS_LIST} seq 5 permit "
                   f"{PRIVATE_AS_REGEX}")
    for policy, direction, sub, inverted in compiled_maps(pol, uses):
        out += compile_policy(pol, policy, direction, sub=sub,
                              inverted=inverted)
    if any(w == "next-hop" and a == ["discard"] for w, a in sets) and uses:
        out += list(DISCARD_ROUTES)
    # A policy used twice - imported on one session and exported on another,
    # or exported into an IGP from five protocols - emits its prefix lists
    # once per use. Handed to FRR once each.
    seen, unique = set(), []
    for line in out:
        if not line.startswith((" ", "exit", "route-map ")):
            if line in seen:
                continue
            seen.add(line)
        unique.append(line)
    return unique


def compiled_maps(pol, uses):
    """
    [(policy, direction, sub, inverted)]: every route-map compile_all()
    emits for `uses`, in its order - each use's own map, then the maps
    `from policy` calls for every use that reaches them, then the ones a
    reject term calls inverted (#542).
    """
    out = [(policy, direction, False, False)
           for policy, direction in sorted(uses)]
    called = {(p, inv, direction) for policy, direction in uses
              for top in chain(policy)
              for p, inv in called_maps(pol, [top])}
    out += [(p, d, True, False)
            for p, d in sorted((p, d) for p, inv, d in called if not inv)]
    out += [(p, d, True, True)
            for p, d in sorted((p, d) for p, inv, d in called if inv)]
    return out


def name_collisions(pol, uses):
    """
    [(policy, why)] for two things the compiler would emit under one FRR
    name (#543). Its names are built from the operator's - a policy's
    route-map PO-<policy>-<use>, the map `from policy` calls
    PO-<policy>-SUB-<use>, the inverted one PO-<policy>-NOT-<use>, a term's
    lists PO-<policy>-T<term>-<kind>, a delete's community list
    PO-C-<community> - and a name may hold `-`, so a policy literally named
    X-SUB compiles its map to the name of X's called map. FRR keeps one
    object per name, and the two are merged or one replaces the other:
    the routing is wrong, and nothing says so. Each object's owner is
    found by compiling it as compile_all() does, so a configuration whose
    names are all distinct is not refused and compiles as it did.
    """
    owners = {}   # (namespace, FRR name) -> {owner}
    for policy, direction, sub, inverted in compiled_maps(pol, uses):
        rmap = inverted_map_name(policy, direction) if inverted \
            else sub_map_name(policy, direction) if sub \
            else map_name(policy, direction)
        owners.setdefault(("route-map", rmap), set()).add(
            (policy, ("map", "not" if inverted else "sub" if sub else "")))
        origin = use_origin(direction)
        for name in chain(policy):
            for term in pol.statements.get(name, {}).values():
                got = _compile_term(pol, name, term, 1, 0, DEFAULT_SEQ,
                                    rmap, origin, direction, inverted)
                for line in (got[0] if got else []):
                    namespace, lname = _list_name(line)
                    owners.setdefault((namespace, lname), set()).add(
                        (name, term.name))
    shared = [line for line in compile_all(pol, set())
              if not line.startswith((" ", "exit", "route-map "))]
    for line in shared:
        namespace, lname = _list_name(line)
        what = "community" if " community-list " in line \
            else "large-community"
        owners.setdefault((namespace, lname), set()).add(
            (None, "set as-path remove-private") if lname == PRIVATE_AS_LIST
            else (None, f"set {what} delete {lname[len(PREFIX) + 2:]}"))
    out = []
    for (namespace, name), who in sorted(owners.items()):
        if len(who) < 2:
            continue
        said = sorted(_owner_words(o) for o in who)
        # Said at the longest policy name: the one that took a derived
        # name's shape, X-SUB beside X.
        first = max((p for p, _ in who if p), key=lambda p: (len(p), p))
        out.append((first, f"policy-statement {first}: {' and '.join(said)} "
                           f"compile to the same FRR {namespace} {name}; FRR "
                           f"keeps one object per name, so rename one of "
                           f"them"))
    return out


#: How name_collisions() says which of a policy's maps it is.
MAP_ROLES = {"": "", "sub": " as from policy calls it",
             "not": " as a reject term's from policy calls it"}


def _list_name(line):
    """(namespace, name) of a list line the compiler emits: the words
    before `seq`, and the last of them."""
    w = line.split()
    i = w.index("seq")
    return " ".join(w[:i - 1]), w[i - 1]


def _owner_words(owner):
    """How name_collisions() says an owner: a policy, a term, a list."""
    policy, rest = owner
    if policy is None:
        return rest
    if isinstance(rest, tuple):
        return f"policy-statement {policy}" + MAP_ROLES[rest[1]]
    return f"policy-statement {policy} term {rest}"


def _filter_matches(route, network, kind, upto):
    """Whether `route` (an ip_network) matches one route-filter."""
    net = ipaddress.ip_network(network)
    if route.version != net.version or not route.subnet_of(net):
        return False
    if kind == "exact":
        return route.prefixlen == net.prefixlen
    if kind == "orlonger":
        return True
    if kind == "longer":
        return route.prefixlen > net.prefixlen
    return route.prefixlen <= upto


def evaluate(pol, policy, prefix, direction="import"):
    """
    What `policy` does with a route to `prefix`, term by term (#458 step 3):
    [(term name or None for the default, verdict, why)], ending at the first
    term that decides.

    From the prefix alone, which is what the policy text can be asked about
    without a route in hand. A condition the prefix does not answer - a
    community, an AS path, a protocol, a tag, a metric, and phase A's
    neighbour, origin, local preference, next hop, large and extended
    communities, RPKI state and a called policy (#535) - is not guessed:
    the term is reported as deciding only if the route carries it, and the
    walk goes on as if it did not, so the operator sees both outcomes. The
    family is answered by the prefix. Prefix lists match exactly, as FRR's
    `permit <network>` does; route filters as their kind says.
    """
    route = ipaddress.ip_network(prefix, strict=False)
    fam = "inet6" if route.version == 6 else "inet"
    out = []
    for term in pol.statements.get(policy, {}).values():
        fams = term_families(term)
        if fams and fam not in fams:
            out.append((term.name, "no match", f"the prefix is not "
                        f"{' or '.join(fams)}"))
            continue
        prefix_conds = bool(term.prefix_lists or term.filters)
        hit = False
        for n in term.prefix_lists:
            for net in pol.prefix_lists.get(n, []):
                if ipaddress.ip_network(net) == route:
                    hit = True
        for net, kind, upto in term.filters:
            if _filter_matches(route, net, kind, upto):
                hit = True
        if prefix_conds and not hit:
            out.append((term.name, "no match", "the prefix is in none of "
                        "its lists or filters"))
            continue
        unknown = [c for c, v in (("community", term.communities),
                                  ("large-community", term.large_communities),
                                  ("extended-community",
                                   term.ext_communities),
                                  ("as-path", term.as_paths),
                                  ("protocol", term.protocols),
                                  ("tag", term.tags),
                                  ("metric", term.metrics),
                                  ("neighbor", term.neighbors),
                                  ("origin", term.origins),
                                  ("local-preference", term.local_prefs),
                                  ("next-hop", term.next_hops),
                                  ("validation-state", term.validation),
                                  ("source-vrf", term.source_vrfs),
                                  ("called policy", term.policies)) if v]
        action = term.then or "next term"
        sets = ", ".join(f"set {_set_word(w)} {' '.join(a)}".rstrip()
                         for w, a in term.sets)
        what = action + (f" ({sets})" if sets else "")
        if unknown:
            out.append((term.name, f"if the route's {' and '.join(unknown)} "
                        f"match: {what}", "the prefix alone does not say"))
            continue
        out.append((term.name, what, "matches" if prefix_conds
                    else "has no prefix condition, so every route matches"))
        if action in ("accept", "reject"):
            return out
        if action == "next policy":
            # This policy is done with the route: in a chain the next one
            # takes it, alone the default does - not this policy's own.
            out.append((None, _verdict(direction), "next policy: the "
                        "policies after this one, or the default for this "
                        "use"))
            return out
    if policy in pol.defaults:
        out.append((None, pol.defaults[policy], "no term decided: this "
                    "policy's default-action"))
        return out
    out.append((None, _verdict(direction), "no term decided: the default "
                "for this use"))
    return out


def _verdict(direction):
    """A use's default as `test policy` says it: accept or reject."""
    return "accept" if use_default(direction) == "permit" else "reject"


# ---------------------------------------------------------------------------
# Phase D (#538): what the policies did, read off FRR's counters, and what
# they would do with a route, simulated clause by clause as FRR runs them.

def use_daemon(direction):
    """
    The FRR daemon whose route-map a use is (#538), as `show route-map
    json` keys its section: bgpd for a neighbor and BGP's attach points,
    ospfd and isisd for an export into the IGP and its default, zebra for a
    FIB policy. Every daemon holds every route-map; only this one runs it,
    so only its counters move.
    """
    scope = direction.split(":")[0]
    if direction in DIRECTIONS or scope == "bgp":
        return "bgpd"
    return {"ospf": "ospfd", "isis": "isisd", "fib": "zebra"}[scope]


def clause_owner(pol, policy, seq):
    """
    What the clause at `seq` of the map `policy` - one, or a chain - was
    compiled from (#538): (policy, term name), (policy, None) for a policy's
    own default-action, (None, None) for the use's default. The inverse of
    compile_policy()'s numbering; None for a number it never writes.
    """
    names = chain(policy)
    if seq == DEFAULT_SEQ:
        return (None, None)
    if len(names) > 1:
        k, rem = divmod(seq, BLOCK)
        if k >= len(names):
            return None
        if rem == BLOCK - 1:
            return (names[k], None)
    else:
        k, rem = 0, seq
        if seq == DEFAULT_SEQ - 1:
            return (names[0], None)
    terms = list(pol.statements.get(names[k], {}))
    pos = rem // 10
    if not 1 <= pos <= len(terms):
        return None
    return (names[k], terms[pos - 1])


def counters(pol, policy, direction, maps, sub=False, inverted=False):
    """
    How many routes each part of `policy` - one, or a chain - matched where
    it is used in `direction` (#538), from FRR's `show route-map json` read
    into `maps`: [(policy, term or None, hits)], each policy's terms in
    order, then its default-action when it has one, then (None, None, hits)
    for the use's default. `sub` is the map a `from policy` term calls,
    `inverted` the one a reject term calls (#542), numbered the same.

    A hit is a clause that matched: measured on FRR 10.7.1, a clause's
    `invoked` counts the routes it matched, not the ones it was asked about
    - five static routes redistributed through four clauses left the first
    at 1, not 5. A term compiled to several clauses (per family, per
    combination) is their sum. hits is None for a term that compiled to no
    clause here - one naming a protocol this use does not see. The whole
    answer is None when the daemon that runs the use did not report the map:
    the daemon is not running, or holds no such map.
    """
    rmap = inverted_map_name(policy, direction) if inverted \
        else sub_map_name(policy, direction) if sub \
        else map_name(policy, direction)
    got = (maps.get(use_daemon(direction)) or {}).get(rmap)
    if not got:
        return None
    hits = {}
    for rule in got.get("rules", []):
        owner = clause_owner(pol, policy, rule.get("sequenceNumber"))
        if owner is not None:
            hits[owner] = hits.get(owner, 0) + int(rule.get("invoked", 0))
    out = []
    for name in chain(policy):
        for term in pol.statements.get(name, {}):
            out.append((name, term, hits.get((name, term))))
        if name in pol.defaults:
            out.append((name, None, hits.get((name, None), 0)))
    out.append((None, None, hits.get((None, None), 0)))
    return out


#: What simulate() reads of a route (#538), besides its `prefix`. A key that
#: is missing is a value not known - the term that needs it is said
#: undecided, not guessed; the lists (communities, the AS path) are [] when
#: the route carries none. `neighbor` is FRR's `match peer`: the neighbor a
#: route comes from on an import, the one it goes to on an export.
#: `metric` is the MED on a BGP route.
ROUTE_KEYS = ("neighbor", "community", "large-community",
              "extended-community", "as-path", "origin", "local-preference",
              "metric", "tag", "next-hop", "validation-state", "source-vrf",
              "weight")
ROUTE_LISTS = ("community", "large-community", "extended-community",
               "as-path")


def as_path_regex(text):
    """An as-path expression as Python reads it: FRR's `_` is a boundary."""
    return text.replace("_", "(^|[,{}() ]|$)")


def _members_match(lists, have):
    """Whether any named set's members are all in `have` (FRR's standard
    list: entries ORed, an entry's members ANDed)."""
    have = set(have)
    return any(m and set(m) <= have for m in lists)


def _term_matches(pol, term, route, origin):
    """
    Whether `term` matches `route` at a use whose map sees `origin`'s
    routes: (True|False|None, why). None is a condition the route does not
    say; False wins over it, as one condition that fails is enough.
    """
    if term.protocols and origin and origin not in term.protocols:
        return False, (f"this use sees {origin} routes, and the term is for "
                       f"{' or '.join(term.protocols)}: it is not compiled "
                       f"here")
    prefix = ipaddress.ip_network(route["prefix"], strict=False)
    fam = "inet6" if prefix.version == 6 else "inet"
    fams = term_families(term)
    if fams and fam not in fams:
        return False, f"the prefix is not {' or '.join(fams)}"
    if term.prefix_lists or term.filters:
        hit = any(ipaddress.ip_network(net) == prefix
                  for n in term.prefix_lists
                  for net in pol.prefix_lists.get(n, []))
        hit = hit or any(_filter_matches(prefix, net, kind, upto)
                         for net, kind, upto in term.filters)
        if not hit:
            return False, "the prefix is in none of its lists or filters"
    need = [k for k, v in (("community", term.communities),
                           ("large-community", term.large_communities),
                           ("extended-community", term.ext_communities),
                           ("as-path", term.as_paths), ("tag", term.tags),
                           ("metric", term.metrics),
                           ("neighbor", term.neighbors),
                           ("origin", term.origins),
                           ("local-preference", term.local_prefs),
                           ("next-hop", term.next_hops),
                           ("validation-state", term.validation),
                           ("source-vrf", term.source_vrfs)) if v]
    unknown = [k for k in need if k not in route]
    have = {k: route[k] for k in need if k in route}
    fails = []
    if "community" in have and not _members_match(
            [pol.communities.get(n, []) for n in term.communities],
            have["community"]):
        fails.append("its communities are not on the route")
    if "large-community" in have and not _members_match(
            [pol.large_communities.get(n, [])
             for n in term.large_communities], have["large-community"]):
        fails.append("its large communities are not on the route")
    if "extended-community" in have and not _members_match(
            [[f"{k}:{v}" for k, v in pol.ext_communities.get(n, [])]
             for n in term.ext_communities], have["extended-community"]):
        fails.append("its extended communities are not on the route")
    if "as-path" in have:
        path = " ".join(have["as-path"])
        if not any(re.search(as_path_regex(pol.as_paths.get(n, "")), path)
                   for n in term.as_paths if n in pol.as_paths):
            fails.append(f"the AS path '{path}' matches none of its "
                         f"expressions")
    # One value each, as the clause holds the last (_compile_term).
    for key, vals in (("tag", term.tags), ("metric", term.metrics)):
        if key in have and str(have[key]) != vals[-1]:
            fails.append(f"the route's {key} is {have[key]}, not "
                         f"{vals[-1]}")
    for key, vals in (("neighbor", term.neighbors),
                      ("origin", term.origins),
                      ("local-preference", term.local_prefs),
                      ("next-hop", term.next_hops),
                      ("validation-state", term.validation),
                      ("source-vrf", term.source_vrfs)):
        if key not in have:
            continue
        val = str(have[key])
        if key in ("neighbor", "next-hop"):
            same = any(_address(v) == _address(val) for v in vals)
        else:
            same = val in vals
        if not same:
            fails.append(f"the route's {key} is {val}, not "
                         f"{' or '.join(vals)}")
    if fails:
        return False, fails[0]
    if unknown:
        why = f"the route's {' and '.join(unknown)} is not known"
        if "validation-state" in unknown:
            why += (" - and with no RPKI cache in sync, FRR's match rpki "
                    "matches no state")
        return None, why
    return True, "matches"


def _relative(have, arg):
    """`set ... +n|-n|n` on `have`: the value, or how it is made of it."""
    if arg[0] not in "+-":
        return arg
    if have is None or not re.fullmatch(r"\d+", str(have)):
        return f"its own {arg}"
    return str(max(0, int(have) + int(arg)))


def _apply_sets(pol, term, route, direction, said):
    """
    The route after `term`'s actions at the use `direction`, as FRR's set
    clauses change it; what changes nothing here goes into `said`.
    """
    scope = direction.split(":")[0]
    igp = direction not in DIRECTIONS and scope in IGPS or scope == "fib"
    sets_ok = USE_SETS.get(direction, USE_SETS.get(scope))
    fam = "ipv6" if ":" in route["prefix"] else "ip"
    r = dict(route)
    for what, args in term.sets:
        word = f"set {_set_word(what)}"
        if sets_ok is not None and what not in sets_ok \
                or igp and what in BGP_SETS \
                or what == "metric-type" and scope != "ospf" \
                or what == "src" and scope != "fib":
            said.append(f"term {term.name}: {word} changes nothing here")
            continue
        if what == "local-preference":
            r["local-preference"] = _relative(r.get("local-preference"),
                                              args[0])
        elif what in ("community", "large-community"):
            op, name = args
            members = (pol.communities if what == "community"
                       else pol.large_communities).get(name, [])
            have = list(r.get(what, []))
            if op == "set":
                have = list(members)
            elif op == "add":
                have += [m for m in members if m not in have]
            else:
                have = [m for m in have if m not in members]
            r[what] = have
        elif what == "extended-community":
            op, name = args
            members = [f"{k}:{v}" for k, v in
                       pol.ext_communities.get(name, [])]
            have = [] if op == "set" else list(r.get(what, []))
            r[what] = have + [m for m in members if m not in have]
        elif what == "as-path-prepend":
            r["as-path"] = list(args) + list(r.get("as-path", []))
        elif what == "as-path-prepend-last-as":
            path = r.get("as-path", [])
            r["as-path"] = ([path[0]] * int(args[0]) + list(path) if path
                            else [f"(the neighbor's AS x {args[0]})"])
        elif what == "as-path-remove-private":
            r["as-path"] = [a for a in r.get("as-path", [])
                            if not re.fullmatch(PRIVATE_AS_REGEX, a)]
        elif what in ("metric", "med"):
            r["metric"] = ("the IGP metric to the next hop"
                           if args[0] == "igp"
                           else _relative(r.get("metric"), args[0]))
        elif what in ("tag", "weight", "origin", "src"):
            r[what] = args[0]
        elif what == "metric-type":
            r["metric-type"] = f"type-{args[0]}"
        elif what == "next-hop" and args[0] == "self":
            r["next-hop"] = "this box's address on the session"
        elif what == "next-hop" and args[0] == "peer-address":
            r["next-hop"] = (r.get("neighbor") if direction == "import"
                             and "neighbor" in r
                             else "the neighbor's address"
                             if direction == "import"
                             else "this box's address on the session")
        elif what == "next-hop" and args[0] == "discard":
            r["next-hop"] = f"{DISCARD[fam]} (discard)"
        elif what == "next-hop":
            r["next-hop"] = args[0]
    return r


def simulate(pol, policy, route, direction="import", depth=0):
    """
    What the map `policy` - one, or a chain - compiles to in `direction`
    does with `route` (#538): every phase A-C condition and action, walked
    in the order and with the semantics FRR runs the map in - a chain's
    next policy, a policy's default-action, `from policy` as FRR's `call`
    (on a reject term, the policy inverted: #542), the use's own default -
    so the answer is the daemon's and not a reading
    of the text. `route` is a dict of ROUTE_KEYS and `prefix`.

    A condition the route does not say is not guessed: the term is
    "unknown", the walk goes on as if it did not match, and the assumption
    is said. Returns {"steps": [(policy, term or None, outcome, why)],
    "verdict": accept|reject, "by": (policy, term) - (policy, None) a
    default-action, (None, None) the use's default -, "route": the
    attributes after the sets, "assumed": [...], "said": [...]}.
    """
    out = {"steps": [], "assumed": [], "said": [], "route": dict(route)}
    origin = use_origin(direction)
    names = chain(policy)
    for k, name in enumerate(names):
        got = _walk(pol, name, out, direction, origin, depth)
        if got and got[0] in ("accept", "reject"):
            out["verdict"], out["by"] = got
            return out
        if got is None and name in pol.defaults:
            out["steps"].append((name, None, "match",
                                 f"no term decided: default-action "
                                 f"{pol.defaults[name]}"))
            out["verdict"], out["by"] = pol.defaults[name], (name, None)
            return out
    out["verdict"], out["by"] = _verdict(direction), (None, None)
    out["steps"].append((None, None, "match", f"no term decided: the "
                         f"default of the use, {_verdict(direction)}"))
    return out


def _walk(pol, name, out, direction, origin, depth):
    """
    One policy's terms against out["route"], for simulate(): ("accept" |
    "reject", by), ("next policy", by), or None when no term decided.
    """
    for term in pol.statements.get(name, {}).values():
        got, why = _term_matches(pol, term, out["route"], origin)
        if got is None:
            out["steps"].append((name, term.name, "unknown", why))
            out["assumed"].append(f"{name} term {term.name} did not match: "
                                  f"{why}")
            continue
        if not got:
            out["steps"].append((name, term.name, "no match", why))
            continue
        if term.then == "reject" and not term.policies:
            out["steps"].append((name, term.name, "match", "then reject"))
            return ("reject", (name, term.name))
        # FRR runs a clause's sets before its call, and the called map sees
        # them (measured, #542). A reject term's sets are not compiled.
        if term.then != "reject":
            out["route"] = _apply_sets(pol, term, out["route"], direction,
                                       out["said"])
        passed = True
        for p in term.policies:
            if depth > 8:
                out["said"].append(f"from policy {p}: called too deep to "
                                   f"follow")
                continue
            sub = simulate(pol, p, out["route"], direction, depth + 1)
            out["steps"] += [(sp, st, so, f"called by {name} term "
                              f"{term.name}: {sw}")
                             for sp, st, so, sw in sub["steps"]]
            out["assumed"] += sub["assumed"]
            out["said"] += sub["said"]
            # What the called map's sets did stays on the route, whichever
            # way it decided.
            out["route"] = sub["route"]
            if term.then == "reject":
                # The term rejects what the policy accepts; what it rejects
                # goes on to the next term (#542).
                passed = sub["verdict"] == "accept"
                if not passed:
                    out["steps"].append((name, term.name, "no match",
                                         f"from policy {p} rejected the "
                                         f"route, so the term does not "
                                         f"match"))
                break
            if sub["verdict"] == "reject":
                out["steps"].append((name, term.name, "match",
                                     f"from policy {p} rejected the route, "
                                     f"and FRR's call rejects it here"))
                return ("reject", (name, term.name))
        if not passed:
            continue
        if term.then == "reject":
            out["steps"].append((name, term.name, "match",
                                 f"from policy {term.policies[0]} accepted "
                                 f"the route: then reject"))
            return ("reject", (name, term.name))
        then = term.then or "next term"
        out["steps"].append((name, term.name, "match", f"then {then}"))
        if then == "accept":
            return ("accept", (name, term.name))
        if then == "next policy":
            return ("next policy", (name, term.name))
    return None


#: How `test policy ... at <use>` names a use (#538) besides `ospf|isis
#: <protocol>` and `fib <protocol>`: the words of the statements that
#: apply a policy there.
AT_WORDS = {"import": "import", "export": "export",
            "default-originate": "bgp:default", "network": "bgp:network",
            "aggregate": "bgp:aggregate", "suppress": "bgp:suppress",
            "advertise": "bgp:advertise", "condition": "bgp:condition",
            "table": "bgp:table", "vrf-import": "bgp:vrfimport",
            "ospf-default": "ospf:default", "isis-default": "isis:default"}
AT_USAGE = ("the uses: import, export, default-originate, network, "
            "aggregate, suppress, advertise, condition, table, vrf-import, "
            "ospf-default, isis-default, ospf <protocol>, isis <protocol> "
            "and fib <protocol>")
#: What `route <prefix>` takes after it, said after a refusal of it.
ROUTE_USAGE = ("route <prefix> [<attribute> <value>]... [at <use>]: the "
               "attributes are " + ", ".join(ROUTE_KEYS) + " and med; the "
               "lists comma-separated")


def use_from_words(words):
    """The use `at <words>` names: (use, the words after it), or (None,
    why)."""
    if words[:1] and words[0] in AT_WORDS:
        return AT_WORDS[words[0]], words[1:]
    if len(words) >= 2 and words[0] in IGPS and words[1] in IGPS[words[0]]:
        return f"{words[0]}:{words[1]}", words[2:]
    if len(words) >= 2 and words[0] == "fib" and words[1] in FIB_PROTOCOLS:
        return f"fib:{words[1]}", words[2:]
    return None, f"at: {' '.join(words[:2]) or 'nothing'} is not a use"


def use_words(direction):
    """A use as `at` takes it: the inverse of use_from_words()."""
    for word, use in AT_WORDS.items():
        if use == direction:
            return word
    return direction.replace(":", " ")


def _route_value(key, text):
    """A route attribute typed after `route <prefix>`: (value, None) or
    (None, why)."""
    if key in ("community", "large-community", "extended-community"):
        vals = [v for v in text.split(",") if v]
        check = {"community": community_error,
                 "large-community": large_community_error,
                 "extended-community": ext_community_error}[key]
        bad = next((check(v) for v in vals if check(v)), None)
        return (None, bad) if bad else (vals, None)
    if key == "as-path":
        vals = [v for v in text.split(",") if v]
        if not all(re.fullmatch(r"\d+", v) and int(v) <= U32 for v in vals):
            return None, "as-path: AS numbers, comma-separated: 65001,65002"
        return vals, None
    if key in ("neighbor", "next-hop"):
        return (text, None) if _address(text) is not None \
            else (None, f"{key}: '{text}' is not an address")
    if key == "origin":
        return (text, None) if text in ORIGINS \
            else (None, "origin: igp, egp or incomplete")
    if key == "validation-state":
        return (text, None) if text in VALIDATION_STATES \
            else (None, "validation-state: valid, invalid or unknown")
    if key == "source-vrf":
        why = vrf_name_error(text)
        return (None, f"source-vrf: {why}") if why else (text, None)
    if not re.fullmatch(r"\d+", text) or int(text) > U32:
        return None, f"{key} <0-{U32}>"
    return text, None


def route_from_words(prefix, words):
    """
    The route `test policy <p> route <prefix> [<attribute> <value>]...
    [at <use>]` describes (#538): (route, use, None), or (None, None, why).
    A list attribute not typed is none, a value not typed is unknown
    (ROUTE_KEYS); lists are comma-separated, as a word takes them.
    """
    try:
        net = ipaddress.ip_network(prefix, strict=False)
    except ValueError:
        return None, None, f"not a prefix: {prefix}"
    route = {"prefix": str(net)}
    route.update({k: [] for k in ROUTE_LISTS})
    use, i = "import", 0
    while i < len(words):
        if words[i] == "at":
            use, rest = use_from_words(words[i + 1:])
            if use is None:
                return None, None, rest
            i = len(words) - len(rest)
            continue
        key = {"med": "metric"}.get(words[i], words[i])
        if key not in ROUTE_KEYS:
            return None, None, f"{words[i]}: not an attribute of a route"
        if i + 1 >= len(words):
            return None, None, f"{words[i]}: a value after it"
        val, why = _route_value(key, words[i + 1])
        if why:
            return None, None, why
        route[key] = val
        i += 2
    return route, use, None


def describe_route(route):
    """A route's attributes as `attribute value` lines: its prefix, then
    ROUTE_KEYS' order, then what a set added (metric-type, src)."""
    out = [f"prefix {route['prefix']}"] if "prefix" in route else []
    for key in ROUTE_KEYS + ("metric-type", "src"):
        if key not in route:
            continue
        val = route[key]
        if key in ROUTE_LISTS:
            val = " ".join(val) if val else "(none)"
        out.append(f"{key} {val}")
    return out


#: The route attribute each action changes (#538).
SET_KEYS = {"local-preference": "local-preference",
            "community": "community", "large-community": "large-community",
            "extended-community": "extended-community",
            "as-path-prepend": "as-path",
            "as-path-prepend-last-as": "as-path",
            "as-path-remove-private": "as-path", "metric": "metric",
            "med": "metric", "tag": "tag", "weight": "weight",
            "origin": "origin", "next-hop": "next-hop"}


def set_keys(pol, policy):
    """
    The route attributes a use of `policy` - one, or a chain - may have
    changed (#538): what any term of any policy it reaches sets. What a
    route read after the use says of the others is what it was before it.
    """
    return sorted({SET_KEYS[what] for p in reached(pol, chain(policy))
                   for term in pol.statements.get(p, {}).values()
                   for what, _ in term.sets if what in SET_KEYS})


def added_members(pol, policy, what):
    """
    The members a use of `policy` - one, or a chain - adds to a route's
    `what`, community or large-community (#538), when every action on it
    adds; None when one sets or deletes, or none acts on it. A route read
    after the use, less these, is the route before it - unless it arrived
    already carrying one of them.
    """
    lists = pol.communities if what == "community" \
        else pol.large_communities
    ops = [args for p in reached(pol, chain(policy))
           for term in pol.statements.get(p, {}).values()
           for w, args in term.sets if w == what]
    if not ops or any(op != "add" for op, _ in ops):
        return None
    return sorted({m for _, name in ops for m in lists.get(name, [])})
