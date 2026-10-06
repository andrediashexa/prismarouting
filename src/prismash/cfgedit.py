#!/usr/bin/env python3
"""
`replace pattern` and `rename`, Junos's two words for changing many lines of
a candidate at once (#585): text in, text out, nothing asked of the box.

THE TEXT AND NOT THE MODEL. Both work on what `show configuration` prints -
Config.text(), the file `save` writes - and the result is read back by
Config.parse, the door a saved file and `rollback <n>` already come in by.
One edit then covers every statement the box has, the routing half FRR owns
included, and nothing here keeps a second copy of what a field means. What
checks the result and stages it is the Shell's (Shell.stage_edit); this
module only computes it.

THREE DECISIONS the operator did not take, made the conservative way and
written in docs/routing.md:

  * A pattern NEVER CROSSES A STATEMENT. It is matched against each statement
    on its own, without the indentation that places it, so `^` is the start
    of a statement and no match can join two of them - a regex that ate a
    line break would turn one statement into half of another, and the result
    could only be judged as a whole configuration nobody typed. A match of
    nothing at all is refused, and so is a replacement that puts a line break
    into a statement.
  * WHAT A LEVEL COVERS is the statements inside it, never its own header:
    `replace` in `nat / cgnat / pool A` cannot rename pool A. Renaming is
    `rename`'s, which follows the references a header change leaves behind.
  * A NAME `rename` CANNOT PLACE is refused, not skipped: a statement of the
    routing half that names the old name in a position this module does not
    recognise is a reference it cannot follow, and following none of it would
    leave the commit a dangling one (the issue's own requirement).
"""

from __future__ import annotations

import copy
import ipaddress
import re

from config import (block_paths, ROUTING_HEADER, NAT_HIERARCHY,
                    CGNAT_LEVEL, NAT64_LEVEL, _is_bundle, _is_loopback,
                    is_number)

#: The two words, at every level of configuration mode.
EDIT_WORDS = ("replace", "rename")

#: What `replace` takes, word for word: the usage a malformed line is
#: answered with, and the hint `?` shows.
REPLACE_HINT = "<regex> with <replacement> [upto <n>] [| compare]"
REPLACE_USAGE = "replace pattern " + REPLACE_HINT

#: What `rename` renames (#585), as the words that lead to the name - the
#: first set, chosen because each has a declaration and references this
#: module can find by position, and each was named in the issue. Anything
#: else is refused with this list.
RENAME_FORMS = ("nat cgnat pool <name>", "nat nat64 pool <name>",
                "ip prefix-list <name>", "ipv6 prefix-list <name>",
                "route-map <name>", "router bgp <AS> peer-group <name>",
                "interface <name>")

#: The words after a route-map reference in FRR that are not names: `bgp
#: route-map delay-timer <n>` and zebra's spelling of it.
_NOT_A_MAP = ("delay-timer",)

#: Keywords that, one word before, make a word a route-map's name.
_MAP_WORDS = ("route-map", "call", "unsuppress-map", "advertise-map",
              "exist-map", "non-exist-map", "table-map")

#: The words of a static route that are never an interface after its next
#: hop: FRR's own, in the order static_route_words() writes them.
_ROUTE_WORDS = ("distance", "tag", "table", "label", "color", "bfd",
                "profile", "onlink", "nexthop-vrf", "description",
                "blackhole", "reject", "Null0", "multi-hop", "source")


def edit_tokens(line):
    """
    `line` as (word, quoted) pairs, a double-quoted run one word with its
    quotes taken off and `\\"` a quote inside it; None when a quote is left
    open. Quoted, because a regular expression is the one value this VTY
    takes that may want a space, or a `|` that is not the pipe.
    """
    out, cur, quoted, inq, esc = [], [], False, False, False
    for ch in line:
        if inq:
            if esc:
                cur.append(ch if ch == '"' else "\\" + ch)
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                inq = False
            else:
                cur.append(ch)
            continue
        if ch == '"':
            inq, quoted = True, True
            continue
        if ch.isspace():
            if cur or quoted:
                out.append(("".join(cur), quoted))
            cur, quoted = [], False
            continue
        cur.append(ch)
    if inq:
        return None
    if cur or quoted:
        out.append(("".join(cur), quoted))
    return out


def replace_request(tokens):
    """
    The tokens after `replace` as (pattern, replacement, upto, compare), or a
    str saying what is wrong with them. `upto` is None for every match;
    `compare` is the dry run, `| compare` at the end of the line - the pipe a
    word of its own and not quoted, so a `|` inside a pattern is a pattern's.
    """
    w = list(tokens)
    compare, upto = False, None
    # Either order: `?` offers both once the replacement is typed.
    for _ in range(2):
        if len(w) > 4 and w[-2:] == [("|", False), ("compare", False)] \
                and not compare:
            compare, w = True, w[:-2]
        elif len(w) > 4 and w[-2] == ("upto", False) and upto is None:
            if not re.fullmatch(r"[0-9]{1,9}", w[-1][0]) \
                    or int(w[-1][0]) < 1:
                return "upto: a number of substitutions, 1 or more"
            upto, w = int(w[-1][0]), w[:-2]
    if len(w) != 4 or w[0] != ("pattern", False) or w[2] != ("with", False) \
            or not w[1][0]:
        return f"usage: {REPLACE_USAGE}"
    return w[1][0], w[3][0], upto, compare


def statement_paths(lines):
    """
    For every line of a configuration text, the headers of the blocks it is
    in, outermost first - or None where the line is no statement: a blank, a
    `!` comment, a block's `exit`.

    Two readings, one per half, because the halves are written two ways. The
    box's own half is written by Config.text() with every child one column
    in, so indentation says it; the routing half is FRR's and the VTY's typed
    lines together, which block_paths() reads already (#180).
    """
    try:
        cut = lines.index(ROUTING_HEADER)
    except ValueError:
        cut = len(lines)
    out, stack = [], []
    for raw in lines[:cut]:
        stmt = raw.strip()
        indent = len(raw) - len(raw.lstrip())
        if not stmt or stmt.startswith("!"):
            out.append(None)
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if stmt == "exit":
            out.append(None)
            continue
        out.append([s for _, s in stack])
        stack.append((indent, stmt))
    if cut < len(lines):
        out.append(None)
        out += [None if p is None else [h.strip() for h in p]
                for p in block_paths(lines[cut + 1:])]
    return out


def in_scope(path, scope):
    """Is a statement at `path` inside the level `scope` names (#585)? The
    level's own header is not: it is the level's name, not its content."""
    return path is not None and len(path) >= len(scope) \
        and path[:len(scope)] == list(scope)


def substitute(lines, paths, scope, rx, repl, upto=None):
    """
    `rx` replaced by `repl` in every statement inside `scope`, the first
    `upto` matches only when it is given: (lines, substitutions, statements
    changed).

    Raises ValueError((why, statement)) for the two results this refuses
    rather than guesses at: "empty", a match of nothing (`a*` would put the
    replacement between every two characters), and "break", a replacement
    that breaks a statement in two.
    """
    out, count, changed = list(lines), 0, 0
    for i, (raw, path) in enumerate(zip(lines, paths)):
        if not in_scope(path, scope) or (upto is not None and count >= upto):
            continue
        indent = raw[:len(raw) - len(raw.lstrip())]
        stmt = raw.strip()
        left = None if upto is None else upto - count
        hits = list(rx.finditer(stmt))[:left]
        if not hits:
            continue
        if any(m.start() == m.end() for m in hits):
            raise ValueError(("empty", stmt))
        new = rx.sub(repl, stmt, count=len(hits))
        if "\n" in new or "\r" in new:
            raise ValueError(("break", stmt))
        count += len(hits)
        if new != stmt:
            changed += 1
            out[i] = indent + new
    return out, count, changed


# ------------------------------------------------------------------ rename

def rename_target(words):
    """
    The words leading to a name - the level's and the typed ones together -
    as (kind, where), or None when `rename` takes no such name. `where` is
    the BGP instance's header for a peer-group, whose names are per
    instance, and the level for a pool; None otherwise.
    """
    if len(words) == 3 and words[0] == NAT_HIERARCHY and words[2] == "pool" \
            and words[1] in (CGNAT_LEVEL, NAT64_LEVEL):
        return "pool", words[1]
    if len(words) == 2 and words[1] == "prefix-list" \
            and words[0] in ("ip", "ipv6"):
        return f"prefix-list {words[0]}", None
    if words == ["route-map"]:
        return "route-map", None
    if len(words) >= 4 and words[:2] == ["router", "bgp"] \
            and words[-1] == "peer-group":
        head = words[:-1]
        if "address-family" in head:
            head = head[:head.index("address-family")]
        if len(head) in (3, 5) and (len(head) == 3 or head[3] == "vrf"):
            return "peer-group", " ".join(head)
    if words == ["interface"]:
        return "interface", None
    return None


def _is_address(word):
    """An IPv4 or IPv6 address, no mask."""
    try:
        ipaddress.ip_address(word)
        return True
    except ValueError:
        return False


def _route_interfaces(words, at):
    """
    The positions of a static route's words, from its next hop at `at`, that
    name an interface: the next hop itself when it is not an address, and a
    word after it that is neither a number nor one of FRR's.
    """
    out = {}
    if at < len(words) and not _is_address(words[at]) \
            and words[at] not in _ROUTE_WORDS:
        out[at] = "interface"
    j = at + 1
    if j < len(words) and words[j] not in _ROUTE_WORDS \
            and not is_number(words[j]) and not _is_address(words[j]):
        out[j] = "interface"
    if "nexthop-vrf" in words[at:-1]:
        out[words.index("nexthop-vrf", at) + 1] = "other"
    return out


def _vty_positions(path, words):
    """
    name_positions() for the box's own half, which this VTY writes itself
    and so knows whole: every position not named below is `other`.
    """
    out = {i: "other" for i in range(1, len(words))}
    if not path and words[0] == "interface" and len(words) == 2:
        out[1] = "interface"
    elif len(path) == 2 and path[0] == NAT_HIERARCHY \
            and words[0] == "pool" and len(words) == 2:
        out[1] = "pool"
    elif len(path) == 3 and path[0] == NAT_HIERARCHY \
            and words[0] in ("prefix-list-inside", "prefix-list-outside") \
            and len(words) == 2:
        out[1] = "prefix-list ip"
    elif len(path) == 1 and path[0].startswith("interface "):
        if words[0] == "member" and len(words) == 2:
            out[1] = "interface"
        elif words[0] == "vrrp" and words[2:3] == ["track"] and len(words) > 3:
            out[3] = "interface"
    return out


def _frr_positions(path, words):
    """name_positions() for the routing half: what FRR's statements name,
    where this module recognises it. A position left out is unknown."""
    out = {}
    n = len(words)
    if "description" in words[1:]:
        at = words.index("description", 1)
        out.update({i: "other" for i in range(at + 1, n)})
        words = words[:at]
        n = at
    w0 = words[0]
    if w0 in ("ip", "ipv6") and words[1:2] == ["prefix-list"] and n > 2:
        out[2] = f"prefix-list {w0}"
        return out
    if w0 in ("ip", "ipv6") and words[1:2] == ["route"] and n > 3:
        out.update(_route_interfaces(words, 3))
        return out
    if path == ["router static"] and n > 2 and words[1] == "next-hop":
        out.update(_route_interfaces(words, 2))
        return out
    if w0 == "route-map" and n > 1 and not path:
        out[1] = "route-map"
        return out
    if w0 == "interface" and n > 1 and not path:
        out[1] = "interface"
        if words[2:3] == ["vrf"] and n > 3:
            out[3] = "other"
        return out
    if w0 in ("router", "vrf") or words[:2] == ["import", "vrf"]:
        # `router bgp 65000 vrf red`, `vrf red`, `import vrf red`: VRF names,
        # and `router isis CORE`'s tag - nothing `rename` takes.
        out.update({i: "other" for i in range(1, n)})
        return out
    if w0 == "passive-interface" and n > 1 and words[1] != "default":
        out[1] = "interface"
    if w0 == "bgp" and words[1:2] in (["as-path"], ["community-list"],
                                      ["large-community-list"],
                                      ["extcommunity-list"]):
        out.update({i: "other" for i in range(2, n)})
        return out
    if w0 == "access-list":
        out.update({i: "other" for i in range(1, n)})
        return out
    bgp = path[0] if path and path[0].startswith("router bgp") else None
    if w0 == "neighbor" and n > 1 and bgp:
        out[1] = f"neighbor {bgp}"
        if words[2:3] == ["peer-group"] and n > 3:
            out[3] = f"neighbor {bgp}"
        if words[2:3] == ["update-source"] and n > 3:
            out[3] = "interface"
        if words[2:3] == ["filter-list"] and n > 3:
            out[3] = "other"
        if words[2:3] == ["interface"]:
            # `neighbor ens19 interface ...`: BGP unnumbered names the
            # interface where the neighbour goes.
            out[1] = f"neighbor {bgp}"
    family = "?"
    if words[:2] == ["match", "ip"]:
        family = "ip"
    elif words[:2] == ["match", "ipv6"]:
        family = "ipv6"
    elif w0 == "neighbor" and bgp:
        af = next((h for h in path if h.startswith("address-family ")), "")
        family = ("ipv6" if af.startswith("address-family ipv6")
                  else "ip" if af in ("", "address-family ipv4",
                                      "address-family ipv4 unicast",
                                      "address-family ipv4 multicast")
                  else "?")
    for i in range(1, n):
        prev = words[i - 1]
        if prev in _MAP_WORDS and i - 1 > 0 and words[i] not in _NOT_A_MAP:
            out[i] = "route-map"
        elif prev == "prefix-list" and i - 1 > 0:
            out[i] = f"prefix-list {family}"
        elif prev == "prefix" and i >= 2 and words[i - 2] in (
                "filter-list", "distribute-list"):
            ospf = path[0].split()[:2] if path else []
            out[i] = ("prefix-list ipv6" if ospf == ["router", "ospf6"]
                      else "prefix-list ip" if ospf == ["router", "ospf"]
                      and words[i - 2] == "filter-list"
                      else "prefix-list ?")
        elif prev == "interface" and i - 1 > 0 and w0 != "neighbor":
            out[i] = "interface"
        elif words[:2] == ["match", "ip"] and words[2:3] == ["address"] \
                and i == 3 and words[3] != "prefix-list":
            out[i] = "other"            # an access-list
        elif w0 == "match" and words[1:2] in (["as-path"], ["community"],
                                              ["large-community"],
                                              ["extcommunity"]) and i == 2:
            out[i] = "other"
        elif words[:2] == ["set", "comm-list"] and i == 2:
            out[i] = "other"
    return out


def name_positions(section, path, words):
    """
    {index: kind} for the words of one statement that are names: `pool`,
    `prefix-list ip` / `prefix-list ipv6` (`prefix-list ?` where the family
    cannot be told), `route-map`, `interface`, `neighbor <instance>` for a
    BGP neighbour or peer-group, and `other` for a name of something
    `rename` does not take. A position missing from the answer is one this
    module cannot read; `section` is "vty" or "frr".
    """
    if not words:
        return {}
    if section == "vty":
        return _vty_positions(path, words)
    return _frr_positions(path, words)


def _kind_matches(found, kind, where):
    """Does a position of kind `found` name an object of `kind` (#585)?"""
    if kind == "peer-group":
        return found == f"neighbor {where}"
    if kind == "interface":
        # A BGP neighbour named like an interface is BGP unnumbered's.
        return found == "interface" or found.startswith("neighbor ")
    return found == kind


def _sections(lines):
    """"vty" or "frr" for every line of a configuration text."""
    try:
        cut = lines.index(ROUTING_HEADER)
    except ValueError:
        cut = len(lines)
    return ["vty"] * cut + ["frr"] * (len(lines) - cut)


def declared_names(lines, paths, kind, where=None):
    """The names of `kind` the text declares: its pools, its lists, its
    route-maps, the peer-groups of one BGP instance, its interfaces."""
    out = set()
    for raw, path, sec in zip(lines, paths, _sections(lines)):
        if path is None:
            continue
        w = raw.split()
        if kind == "pool" and sec == "vty" and len(path) == 2 \
                and path[0] == NAT_HIERARCHY and w[:1] == ["pool"]:
            out.add(w[1])
        elif kind.startswith("prefix-list ") and sec == "frr" \
                and w[:2] == [kind.split()[1], "prefix-list"] and len(w) > 2:
            out.add(w[2])
        elif kind == "route-map" and sec == "frr" and not path \
                and w[:1] == ["route-map"] and len(w) > 1:
            out.add(w[1])
        elif kind == "peer-group" and sec == "frr" and path[:1] == [where] \
                and len(w) == 3 and w[0] == "neighbor" \
                and w[2] == "peer-group":
            out.add(w[1])
        elif kind == "interface" and not path and w[:1] == ["interface"] \
                and len(w) > 1:
            out.add(w[1])
    return out


def neighbour_names(lines, paths, where):
    """Every neighbour and peer-group one BGP instance names: what a
    peer-group may not be renamed to."""
    out = set()
    for raw, path, sec in zip(lines, paths, _sections(lines)):
        w = raw.split()
        if sec == "frr" and path and path[0] == where \
                and w[:1] == ["neighbor"] and len(w) > 1:
            out.add(w[1])
    return out


def rename_lines(lines, paths, kind, where, old, new):
    """
    `old` renamed to `new` wherever a statement declares or names an object
    of `kind`: (lines, how many words changed, the first statement naming
    `old` where it cannot be placed, or None).

    The third answer is the refusal the issue asks for. A routing statement
    holding the old name in a position name_positions() cannot read is a
    reference this VTY cannot follow, and renaming everything else would
    leave it dangling; the box's own half is read whole, so it has none. A
    pool is never named by FRR, so the routing half is not read for one.
    """
    out, count, unfollowed = list(lines), 0, None
    for i, (raw, path, sec) in enumerate(zip(lines, paths, _sections(lines))):
        if path is None:
            continue
        words = raw.split()
        if old not in words[1:]:
            continue
        if kind == "pool" and sec == "frr":
            continue
        if kind == "pool" and path[:2] != [NAT_HIERARCHY, where] \
                and len(path) == 2 and words[0] == "pool":
            continue            # the other level's: one namespace, refused
        pos = name_positions(sec, path, words)
        hit = False
        for j in range(1, len(words)):
            if words[j] != old:
                continue
            found = pos.get(j)
            if found is None:
                if unfollowed is None:
                    unfollowed = raw.strip()
                continue
            if found.startswith("prefix-list ?") and kind.startswith(
                    "prefix-list ") and unfollowed is None:
                unfollowed = raw.strip()
                continue
            if _kind_matches(found, kind, where):
                words[j] = new
                count += 1
                hit = True
        if hit:
            indent = raw[:len(raw) - len(raw.lstrip())]
            out[i] = indent + " ".join(words)
    return out, count, unfollowed


def interface_shape(name):
    """
    What an interface name says it is, for interface_rename_error(): "bundle",
    "loopback", "sub <parent>", or "port" for anything else - by the model's
    own two predicates, which are what `interface <name>` creates by.
    """
    if _is_bundle(name):
        return "bundle"
    if _is_loopback(name):
        return "loopback"
    if "." in name and re.fullmatch(r"[0-9]+", name.rsplit(".", 1)[1]):
        return "sub " + name.rsplit(".", 1)[0]
    return "port"


def interface_rename_error(old, new, names):
    """
    Why interface `old` may not become `new`, as (verdict, prose), or None
    (#585). Only what a configuration CREATES is renamed - a sub-interface,
    a loopback, a bundle - and only into a name of the same kind: a port's
    name is the card's, and `vlan` says which tag a sub-interface carries,
    not its name. An interface whose sub-interfaces are named after it is
    refused, naming one: they would be renamed too, or orphaned.
    """
    if interface_shape(old) == "port":
        return ("a physical port's name is the card's; nothing renamed",
                f"{old} is not created by this configuration, so its name is "
                "not the configuration's to change")
    if interface_shape(new) != interface_shape(old):
        what = interface_shape(old).split()[0]
        if what == "sub":
            return ("a sub-interface keeps its parent; nothing renamed",
                    f"the new name is <parent>.<n> with the parent "
                    f"{old.rsplit('.', 1)[0]}")
        return (f"a {what} is renamed to a {what}; nothing renamed",
                "ae<n> for a bundle, loop<n> for a loopback")
    under = sorted(n for n in names if n.startswith(old + "."))
    if under:
        return ("its sub-interfaces are named after it; nothing renamed",
                f"{under[0]} is one; rename them first, or remove them")
    return None


def substituted_row(rx, repl, row):
    """`row` with `rx` replaced by `repl`, or as it was when the replacement
    cannot be used on it - for comparing a commit check's rows (#585)."""
    try:
        return rx.sub(repl, row)
    except (re.error, IndexError):
        return row


def renamed_pool(pool, running, removed):
    """
    The name of the pool in `removed` that `pool` is in every line but its
    name, or None (#585): a pool `rename` renamed, as the commit sees it - one
    pool removed and an identical one added. Both texts are compared, the
    whole one and the mapping (what each side resolved to, #589), so a list
    changed in the same commit is not mistaken for a rename. The running side
    is resolved against its own lists when nothing has resolved it - a file
    read with no dataplane behind it - as Config.compare() resolves it.
    """
    if any((p.inside_list and not p.list_inside)
           or (p.outside_list and not p.list_outside)
           for p in running.pools.values()):
        running = copy.deepcopy(running)
        running.resolve_pool_lists()
    for name in removed:
        old = running.pools.get(name)
        if old is None or old.level != pool.level:
            continue
        if old.lines()[1:] == pool.lines()[1:] and \
                old.lines(in_place=False)[1:] == pool.lines(in_place=False)[1:]:
            return name
    return None
