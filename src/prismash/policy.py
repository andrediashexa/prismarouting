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
        self.sets = []           # (what, [args])
        self.then = None         # "accept" | "reject" | "next term" | None


class Policies:
    """Everything `policy-options` holds."""

    def __init__(self):
        self.prefix_lists = {}   # name -> [network, ...] in the order typed
        self.communities = {}    # name -> [member, ...]
        self.as_paths = {}       # name -> regex
        self.statements = {}     # name -> {term name: Term}, in order


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
    return ("expected: prefix-list <name> <network> | community <name> "
            "members <asn:value>... | as-path <name> <regex> | "
            "policy-statement <name> term <name> ...")


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
        return ("from: prefix-list, route-filter, community, as-path, "
                "protocol, tag or metric")
    if w[0] == "set" and len(w) >= 3:
        if w[1] == "local-preference" and len(w) == 3 \
                and re.fullmatch(r"\d+", w[2]) and int(w[2]) <= 4294967295:
            term.sets.append(("local-preference", [w[2]]))
            return None
        if w[1] == "community" and len(w) == 4 and w[2] in COMMUNITY_OPS:
            term.sets.append(("community", [w[2], w[3]]))
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
        if w[1] == "next-hop" and len(w) == 3:
            try:
                ipaddress.ip_address(w[2])
            except ValueError:
                return f"set next-hop: '{w[2]}' is not an address"
            term.sets.append(("next-hop", [w[2]]))
            return None
        return ("set: local-preference, community add|set|delete, "
                "as-path-prepend, metric, tag or next-hop")
    if w[0] == "then" and w[1:] in (["accept"], ["reject"], ["next", "term"]):
        term.then = " ".join(w[1:])
        return None
    if w[0] == "then":
        # Junos puts actions after `then`; here the actions are `set`, and
        # there is one spelling, as #436 decided for the file and the prompt.
        return ("then: accept, reject or next term; a change to the route "
                "is 'set ...'")
    return "a term takes 'from ...', 'set ...' and 'then ...'"


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
    return out


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
            if len(w) >= 4 and w[2] == "term":
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
        for term, body in terms.items():
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
            else:
                bad.append((n, s, "under a policy-statement there are "
                                  "terms, 'term <name>'"))
        else:
            out.append((n, f"policy-options {where[0]} {where[1]} {s}"))
    return out, bad


def map_name(policy, direction):
    """
    The FRR route-map a policy becomes when used in `direction`: `import`
    or `export` on a BGP neighbor, or `<igp>:<protocol>` for the part of an
    export into an IGP that redistributes from one protocol.
    """
    if direction in DIRECTIONS:
        return f"{PREFIX}{policy}-{DIRECTIONS[direction]}"
    igp, proto = direction.split(":")
    return f"{PREFIX}{policy}-{igp.upper()}-{proto.upper()}"


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
        bgp_only += [f"set {what}" for what, _ in term.sets
                     if what in ("local-preference", "community",
                                 "as-path-prepend", "next-hop")]
        if bgp_only:
            out.append(f"term {term.name}: {', '.join(bgp_only)} is for "
                       f"BGP routes")
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


def compile_policy(pol, policy, direction):
    """
    The FRR statements that make `policy` in `direction`: its generated
    lists, then its route-map, then the Junos default. Nothing that another
    use of another policy would emit, so two uses compile side by side.
    """
    terms = pol.statements.get(policy, {})
    rmap = map_name(policy, direction)
    # Which protocol's routes this map sees: BGP's on a neighbor, and on an
    # export into an IGP the one its `redistribute` names. A term naming
    # other protocols never matches here, so it is left out rather than
    # compiled into a clause FRR would evaluate against the wrong routes.
    origin = "bgp" if direction in DIRECTIONS else direction.split(":")[1]
    lists, clauses = [], []
    for pos, term in enumerate(terms.values(), start=1):
        if term.protocols and origin not in term.protocols:
            continue
        base = f"{PREFIX}{policy}-T{term.name}"
        # The prefix conditions of the term, ORed, into one list per family.
        per_family = {"ip": [], "ipv6": []}
        for n in term.prefix_lists:
            for net in pol.prefix_lists.get(n, []):
                per_family[_family(net)].append(net)
        for net, kind, upto in term.filters:
            entry = _filter_entry(net, kind, upto)
            if entry:
                per_family[_family(net)].append(entry)
        families = [f for f in ("ip", "ipv6") if per_family[f]]
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
        aspath = f"{base}-A" if term.as_paths else None
        if aspath:
            for seq, n in enumerate(term.as_paths, start=1):
                lists.append(f"bgp as-path access-list {aspath} seq "
                             f"{seq * 5} permit {pol.as_paths.get(n, '')}")
        # A term with prefixes of both families is two clauses, one each: a
        # single clause would AND `match ip` with `match ipv6` and match
        # nothing. A term with no prefix condition is one clause.
        for i, fam in enumerate(families or [None]):
            action = "deny" if term.then == "reject" else "permit"
            clause = [f"route-map {rmap} {action} {pos * 10 + i}"]
            if fam:
                name = f"{base}-{'4' if fam == 'ip' else '6'}"
                clause.append(f" match {fam} address prefix-list {name}")
            if comm:
                clause.append(f" match community {comm}")
            if aspath:
                clause.append(f" match as-path {aspath}")
            # One value each: FRR takes one `match tag` and one `match
            # metric` per clause, and a second replaces the first.
            if term.tags:
                clause.append(f" match tag {term.tags[-1]}")
            if term.metrics:
                clause.append(f" match metric {term.metrics[-1]}")
            if action == "permit":
                clause += _set_lines(pol, term)
                if term.then != "accept":
                    clause.append(" on-match next")
            clauses += clause + ["exit"]
    # THE JUNOS DEFAULT, which is not one default: a BGP route no term
    # decided is accepted (the maintainer chose it for #458), and a route no
    # term decided on its way into an IGP is not exported - Junos's OSPF and
    # IS-IS export nothing that no policy accepts.
    default = "permit" if direction in DIRECTIONS else "deny"
    clauses += [f"route-map {rmap} {default} {DEFAULT_SEQ}", "exit"]
    return lists + clauses


def _set_lines(pol, term):
    """The FRR `set` lines of a term's actions."""
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
        elif what == "next-hop":
            fam = "ipv6" if ":" in args[0] else "ip"
            out.append(f" set {fam} next-hop "
                       f"{'global ' if fam == 'ipv6' else ''}{args[0]}")
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
    for policy, direction in sorted(uses):
        out += compile_policy(pol, policy, direction)
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
