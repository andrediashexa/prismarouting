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
        self.sets = []           # (what, [args])
        self.then = None         # "accept" | "reject" | "next term" | None

    def matches_prefixes(self):
        return bool(self.prefix_lists or self.filters)


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
        return ("from: prefix-list <name>, route-filter <network> <how>, "
                "community <name> or as-path <name>")
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
        return ("set: local-preference <0-4294967295>, community "
                "add|set|delete <name>, or as-path-prepend <asn>...")
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


def map_name(policy, direction):
    """The FRR route-map a policy becomes when used in `direction`."""
    return f"{PREFIX}{policy}-{DIRECTIONS[direction]}"


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
    lists, clauses = [], []
    for pos, term in enumerate(terms.values(), start=1):
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
            if action == "permit":
                clause += _set_lines(pol, term)
                if term.then != "accept":
                    clause.append(" on-match next")
            clauses += clause + ["exit"]
    clauses += [f"route-map {rmap} permit {DEFAULT_SEQ}", "exit"]
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
    return out
