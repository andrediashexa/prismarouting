#!/usr/bin/env python3
"""
The forwarding table, and the two tables behind it.

Three tables hold routes on this box and only one of them forwards packets:

    FRR's RIB  ->  the kernel FIB  ->  VPP's FIB
     (what FRR      (what FRR         (what moves
      decided)       installed)        the packet)

`show ip route` in vtysh answers from the first. That is the command an
operator reaches for when traffic is not flowing, and it is the table where the
route is present and correct on a box that is dropping the traffic. Nothing in
that output says the FIB was never asked.

So `show route` here reads VPP's FIB, and `show route verbose` reads both and
marks every prefix where they disagree. A prefix in the RIB and not in the FIB
is the failure this exists to make visible: FRR installs into the kernel and
`linux_nl` carries it into VPP, and a route that stops half way leaves both
halves looking healthy on their own.
"""

from __future__ import annotations

import json
import re

# A prefix on its own at the start of a line opens an entry in "show ip fib".
_PREFIX = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})\b")
# The header "show ip fib" prints above each table, in its two forms.
#
# A table with no name prints `ipv4-VRF:10`. A table WITH a name prints the
# name instead - `vrf7, fib_index:2` - and this pattern used to match only the
# first, so every prefix in a named table was filed under whichever table came
# before it. `Shell.set_vrf()` names every table it creates, so that was every
# box where an operator had typed `interface <name> / vrf <n>` (#268).
#
# fib_index is NOT the table id and must never be used as one: measured on VPP
# 26.06, table 10 is fib_index:1.
_VRF = re.compile(r"^ipv4-VRF:(\d+)")
_NAMED = re.compile(r"^(\S+), fib_index:(\d+)")
_VIA = re.compile(r"ipv4 via (\d{1,3}(?:\.\d{1,3}){3}) ([^:\s]+)")
_GLEAN = re.compile(r"ipv4-glean: \[src:([^\]]+)\] ([^:\s]+)")
_RECEIVE = re.compile(r"dpo-receive: (\d{1,3}(?:\.\d{1,3}){3}) on (\S+)")

# What an entry is FOR. Only the first two carry transit traffic; the rest are
# the housekeeping VPP creates for every address on every interface, and
# printing them beside four real routes buries the four.
FORWARD = "forward"      # ipv4 via <nexthop> <interface>
CONNECTED = "connected"  # a prefix on an interface of this box
LOCAL = "local"          # an address OF this box
NEIGHBOUR = "neighbour"  # a resolved neighbour, not a route anybody configured
DROP = "drop"            # explicitly discarded


def table_names(details) -> dict:
    """
    {name: table_id} from an `ip_table_dump`.

    The name-to-id map exists nowhere in `vppctl`: `show ip fib summary` and
    `show ip fib mem` print the same description the parser cannot resolve, and
    `show fib tables` does not exist. The binary API carries both together,
    which is why this takes a dump rather than text.

    Reading `vrf<n>` out of the name would work for the tables this product
    happens to create and for no others, which is a guess dressed as a rule.
    """
    out = {}
    for d in details or []:
        t = d.get("table") or {}
        if t.get("is_ip6"):
            continue
        name = (t.get("name") or "").strip()
        if name:
            out[name] = int(t["table_id"])
    return out


def parse_fib(text: str, names: dict = None) -> dict:
    """
    "show ip fib" as {prefix: entry}, per VRF.

    Returns {vrf: {prefix: {"kind", "via": [(nexthop, interface)]}}}. A pool
    bound to a VRF (#79) means a box can hold the same prefix twice with two
    different answers, so the VRF cannot be flattened away here.
    """
    tables: dict = {}
    vrf = 0
    prefix = None

    for raw in text.splitlines():
        m = _VRF.match(raw)
        if m:
            vrf = int(m.group(1))
            tables.setdefault(vrf, {})
            prefix = None
            continue

        m = _NAMED.match(raw)
        if m:
            # A named table, keyed by the id the NAME maps to. When the map
            # does not have it - no API, or a table created outside this
            # product - the key is the name itself. That is deliberately not a
            # number: a caller asking for table 7 will not find it, so the
            # answer degrades to "not known" instead of being attributed to
            # the table above, which is what the defect did. Dropping the
            # prefixes would be wrong too, because `show route` would lose a
            # VRF without saying so.
            vrf = (names or {}).get(m.group(1), m.group(1))
            tables.setdefault(vrf, {})
            prefix = None
            continue

        m = _PREFIX.match(raw)
        if m:
            prefix = m.group(1)
            tables.setdefault(vrf, {})[prefix] = {"kind": DROP, "via": []}
            continue

        if prefix is None:
            continue
        entry = tables[vrf][prefix]

        m = _VIA.search(raw)
        if m:
            entry["kind"] = FORWARD
            entry["via"].append((m.group(1), m.group(2)))
            continue
        m = _GLEAN.search(raw)
        if m:
            entry["kind"] = CONNECTED
            entry["via"].append(("connected", m.group(2)))
            continue
        m = _RECEIVE.search(raw)
        if m:
            entry["kind"] = LOCAL
            entry["via"].append(("local", m.group(2)))

    for table in tables.values():
        for prefix, entry in table.items():
            # ECMP is real and two identical lines are not: the same next hop
            # listed twice is one path, and a table that shows it twice invites
            # somebody to go looking for a second link that does not exist.
            seen, uniq = set(), []
            for hop in entry["via"]:
                if hop not in seen:
                    seen.add(hop)
                    uniq.append(hop)
            entry["via"] = uniq

            # A /32 whose only next hop is its own address is the adjacency VPP
            # creates for a RESOLVED NEIGHBOUR, not a route anybody configured.
            # One appears per neighbour, and listing them beside four real
            # routes is how a routing table stops being readable.
            if (entry["kind"] == FORWARD and prefix.endswith("/32")
                    and len(uniq) == 1 and uniq[0][0] == prefix.split("/")[0]):
                entry["kind"] = NEIGHBOUR

    return tables


def parse_rib(text: str) -> dict:
    """
    "show ip route json" from FRR as {prefix: [{"via", "interface", "protocol",
    "selected"}]}.

    JSON and not the text form: the text form is laid out for a human and its
    columns move between FRR versions, which is a bad thing to build a
    comparison on. An unparseable answer returns nothing rather than a guess -
    a comparison that silently lost one of its two sides would report every
    route as missing from FRR.
    """
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}

    out: dict = {}
    for prefix, entries in raw.items():
        if not isinstance(entries, list):
            continue
        for e in entries:
            if not isinstance(e, dict):
                continue
            hops = []
            for nh in e.get("nexthops", []) or []:
                hops.append({
                    "via": nh.get("ip", ""),
                    "interface": nh.get("interfaceName", ""),
                })
            out.setdefault(prefix, []).append({
                "protocol": e.get("protocol", "?"),
                "selected": bool(e.get("selected", False)),
                "nexthops": hops,
            })
    return out


def forwarding(table: dict) -> dict:
    """The entries that carry traffic, which is what `show route` is about."""
    return {p: e for p, e in table.items()
            if e["kind"] in (FORWARD, CONNECTED)}


def protocol_prefixes(rib: dict, protocol: str) -> list:
    """
    The prefixes FRR CHOSE from one protocol, in address order.

    Only the entries zebra selected, and that is the whole subtlety. An IS-IS
    box learns its own connected networks back from its neighbour - 10.0.0.0/24
    arrives by IS-IS on the very link it is configured on - and zebra keeps the
    connected route, so the IS-IS copy is held and never installed. Counting it
    as a prefix the dataplane is missing would put a permanent fault on a box
    where nothing is wrong, which is how a report stops being read.
    """
    return sorted((p for p, entries in rib.items()
                   if any(e.get("selected") and e.get("protocol") == protocol
                          for e in entries)),
                  key=_sort_key)


def vpp_host_interfaces(lcp_text: str) -> set:
    """
    The Linux-side names of the interfaces VPP owns, from "show lcp".

    Needed because FRR knows routes this box will never forward and SHOULD NOT
    forward: the management interface is deliberately kept away from VPP - hand
    it over and the box is unreachable and unfixable remotely - so every route
    through it is in FRR and absent from the FIB, correctly and permanently.

    Reporting those as faults would put a handful of standing alarms on every
    real box, and an alarm that is always on is one nobody reads.

    Read through lcp_pairs rather than by position. This used to look for a
    literal "host" token and take the next field, and there is no such token on
    any line VPP prints - so it returned an EMPTY SET on every box, and an
    empty set makes compare() suppress every route FRR holds and the FIB does
    not. The one report that exists to find a route the dataplane is missing
    found none, on every box, silently. The claim "this cannot work" was
    written down in test/test_routes.py and demonstrated on a copy of the parse
    rather than on this function, so it went on shipping.

    Every name on the pair, not the Linux one alone: FRR names an interface by
    its Linux name and the dataplane by its own, and which of them appears in a
    RIB entry is not this function's business to predict.
    """
    return set(lcp_pairs(lcp_text))


def vpp_owned_interfaces(lcp_text: str, iface_text: str) -> set:
    """
    Every interface VPP owns: its own, and both names of every linux_cp pair.

    Both halves, and the second one was missing. `show lcp` names only the
    PAIRS, and a box can have none - so on such a box the set was empty, and
    compare() judged every route FRR held against nothing. Measured on
    lab/bng.sh, whose box has no pair: three of the four routes the report
    then raised were the container's own network through eth0, which is the
    management interface and precisely what the suppression exists for.

    An interface the dataplane knows by NAME is one VPP owns whether or not
    anything paired it. `host-wan0` is VPP's and `eth0` is not, and that is the
    whole distinction the report needs. `show lcp` alone cannot make it.

    It is one function rather than a union at the call site so that an
    assertion can be made about the ANSWER. Written inline, a test can only
    restate the union, which is an assertion that describes the fix instead of
    depending on it.
    """
    return vpp_host_interfaces(lcp_text) | set(parse_interfaces(iface_text))


def compare(fib: dict, rib: dict, vpp_hosts: set = None) -> list:
    """
    Every prefix either table knows, and which of them holds it.

    Returns (prefix, in_rib, in_fib, vpp_kind, next_hop, interface) per
    prefix, in address order.
    """
    rows = []
    for prefix in sorted(set(fib) | set(rib), key=_sort_key):
        entry = fib.get(prefix)
        # What VPP HOLDS, not merely whether it forwards. A prefix VPP is
        # explicitly dropping and a prefix VPP has never heard of are two
        # different situations with two different causes, and reporting both
        # as "-" is a false statement in the one table whose job is to settle
        # a disagreement.
        kind = entry["kind"] if entry else "absent"
        # LOCAL counts. An address OF this box is not a route that fails to
        # forward - traffic to it is received, which is the whole point of it.
        in_fib = kind in (FORWARD, CONNECTED, LOCAL)
        in_rib = prefix in rib

        # A route FRR reaches through an interface VPP does not own is not
        # VPP's to hold. On a real box that is the management interface, and
        # every route through it would otherwise stand as a permanent fault.
        #
        # An EMPTY set means nothing is known, not that VPP owns nothing, and
        # it is not a rare state: "show lcp" answers nothing on a box whose
        # linux_cp plugin is not loaded, and the caller discards its exit
        # status. Treated as "VPP owns nothing" it silenced every fault there
        # is - which is the direction a fault report must never fail in.
        if in_rib and vpp_hosts and not in_fib:
            ifaces = {nh["interface"] for e in rib[prefix]
                      for nh in e["nexthops"] if nh["interface"]}
            if ifaces and not (ifaces & vpp_hosts):
                in_rib = False

        if entry and entry["via"]:
            via, iface = entry["via"][0]
        elif in_rib and rib[prefix][0]["nexthops"]:
            nh = rib[prefix][0]["nexthops"][0]
            via, iface = nh["via"] or "-", nh["interface"] or "-"
        else:
            via, iface = "-", "-"

        rows.append((prefix, in_rib, in_fib, kind, via, iface))
    return rows


def _sort_key(prefix: str):
    net, _, length = prefix.partition("/")
    try:
        octets = tuple(int(o) for o in net.split("."))
    except ValueError:
        return (0, 0, 0, 0, 0)
    return octets + (int(length or 0),)


# ------------------------------------------------------- static route checks

_IP_ROUTE = re.compile(r"^\s*ip route (\S+)\s+(\S+)")

#: FRR's `show vrf`: "vrf red id 3 table 10 (configured)", and for one whose
#: Linux device does not exist, "vrf green inactive (configured)".
_VRF_TABLE = re.compile(r"^vrf (\S+) id \d+ table (\d+)")


def parse_vrf_tables(text: str) -> dict:
    """
    FRR's VRFs as {name: routing table id}.

    A static route inside `vrf red` is installed in the table that VRF names,
    and nothing in this VTY's own grammar says which table that is: an
    interface's `vrf` is a NUMBER here and an FRR block is a NAME, so only the
    box can say the two are the same table. FRR can, and does, here.

    Measured on a live FRR 10.7.1 and a live VPP 26.06, because the number
    only helps if it is the number the FIB is keyed by: a kernel route in
    table 10 arrives in VPP as `ipv4-VRF:10`, the table linux_nl creates from
    the kernel's own table id. That is the key `parse_fib` returns.

    A VRF FRR calls `inactive` has no device and so no table, and is left out
    rather than given one: there is nothing to resolve a next hop against, and
    a made-up table is how a check comes to pass on evidence from another one.
    """
    out = {}
    for line in (text or "").splitlines():
        m = _VRF_TABLE.match(line.strip())
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


def _vrf_of(path) -> str:
    """
    The VRF a statement sits in, from the block path, or "" for none.

    "" is the GLOBAL table and not "unknown": a static route at the top level
    is in table 0, and so is one inside `router static`, which is this VTY's
    own word for the global table rather than a table of its own.
    """
    for block in path or []:
        w = block.strip().split()
        if len(w) >= 2 and w[0] == "vrf":
            return w[1]
    return ""


def _addr_int(addr: str):
    try:
        parts = [int(o) for o in addr.split(".")]
    except ValueError:
        return None
    if len(parts) != 4 or any(o < 0 or o > 255 for o in parts):
        return None
    return (parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]


def covers_prefix(outer: str, inner: str) -> bool:
    """
    True when `outer` contains the whole of `inner`.

    _covers() below answers "is this ADDRESS inside this prefix", which is not
    the same question: 200.150.0.0/24 contains the address 200.150.0.16, and
    also contains all of 200.150.0.16/28 - but 200.150.0.16/28 does not contain
    200.150.0.0/24, and an address test cannot tell those apart. A shorter
    prefix cannot be covered by a longer one, whatever their addresses.

    IPv4 only, like the rest of this module. The CGNAT's public side is IPv4 by
    definition - it exists because IPv4 ran out.
    """
    o_net, _, o_len = outer.partition("/")
    i_net, _, i_len = inner.partition("/")
    try:
        if int(o_len) > int(i_len):
            return False
    except ValueError:
        return False
    return _covers(outer, i_net)


def covers_address(prefix: str, addr: str) -> bool:
    """
    True when `addr` is inside `prefix`.

    The same question _covers() has answered privately since this module was
    written, named and exported because a caller outside it now has to ask it:
    the check that reads whether this box still advertises the prefix carrying
    the operator's own session starts from an ADDRESS, not from a prefix.
    """
    return _covers(prefix, addr)


def _covers(prefix: str, addr: str) -> bool:
    net, _, length = prefix.partition("/")
    n, a = _addr_int(net), _addr_int(addr)
    if n is None or a is None:
        return False
    try:
        bits = int(length)
    except ValueError:
        return False
    if bits <= 0:
        return True
    if bits > 32:
        return False
    mask = (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF
    return (n & mask) == (a & mask)


def connected_from_fib(fibs: dict) -> dict:
    """
    {table: [prefix]} - what the dataplane has connected RIGHT NOW.

    One of the two ways to answer "what is connected", and the only one that
    was ever built. The other is Config.connected_prefixes(), which answers it
    from a configuration rather than from a box, and the two exist because the
    question has two moments: what a running box forwards on, and what a box
    will forward on once a file has been applied to it. See
    unreachable_next_hops(), which takes the answer and does not care which
    moment produced it.

    Every table the FIB reported is a key here, INCLUDING one with no connected
    prefix in it. The empty list and the missing key mean different things to
    the caller - "this table connects nothing" against "this box has no such
    table" - and collapsing them turned a refusal into a note (#255).
    """
    return {tid: [p for p, e in entries.items() if e["kind"] == CONNECTED]
            for tid, entries in fibs.items()}


def unreachable_next_hops(routing: list, connected: dict, paths: list,
                          vrf_tables) -> tuple:
    """
    Static routes whose next hop is on no connected network OF THEIR TABLE.

    A next hop the box cannot reach is a route that is accepted, installed, and
    silently inert: FRR holds it, the FIB never gets it, and `show ip route`
    looks correct. It is the exact shape of failure `show route verbose` exists
    to make visible afterwards - and it is cheaper to refuse it at the prompt
    than to have somebody find it during an outage.

    The table is the whole question and used to be assumed to be table 0, which
    gave both wrong answers at once (#255). Measured at a live VPP: with
    172.31.99.0/24 connected only in table 10 and 200.150.0.0/24 connected only
    in table 0, a `vrf red` route via 172.31.99.1 was REFUSED and one via
    200.150.0.2 was ACCEPTED - the first is a correct configuration turned away
    at the prompt, which is the harm #208 was filed for, and the second is the
    inert route this check exists to catch, committed in silence.

    `connected` is {table: [prefix]} and is the whole of what this function
    knows about the box. It USED to be the parsed FIB, and taking the FIB here
    is what made `hexash --load` refuse a box's entire configuration at boot:
    at boot the FIB is MID-CONSTRUCTION, the addresses that make the next hop
    connected are in the file being read and have not been applied yet, so the
    check answered a question about a box that does not exist (#294). The set
    is the caller's to assemble precisely so that the caller can say which
    moment it is asking about; this function asks one question of it.

    `paths` is where each line sits, as `config.block_paths` reports it, and
    `vrf_tables` is `parse_vrf_tables`'s answer - or None when FRR could not be
    asked at all, which is a different thing from a VRF having no table.

    A table `parse_fib` did not return lands in `unplaced` rather than being
    answered from another one, which is what keeps #268 - a NAMED FIB table
    filed under its neighbour - a missing check here and not a wrong one.

    Only the "<prefix> <next-hop>" form is checked. `ip route <prefix>
    <interface>`, blackhole and Null0 name no address to verify, and guessing
    at them would refuse configurations that are correct.

    Returns (bad, unplaced):

      bad       [(statement, next_hop)] - the refusals, empty when all resolve
      unplaced  [(vrf, why, [statements])] - what could NOT be checked, so the
                caller can say so. A check that quietly does not run is one the
                operator believes ran and passed, which is the silence this
                whole function exists to break.
    """
    bad, unplaced = [], {}
    for path, line in zip(paths, routing):
        m = _IP_ROUTE.match(line)
        if not m:
            continue
        nexthop = m.group(2)
        if _addr_int(nexthop) is None:
            continue  # an interface name, blackhole, Null0: nothing to check
        vrf = _vrf_of(path)
        if not vrf:
            table = 0
        elif vrf_tables is None:
            table = None
            why = "FRR could not be asked which routing table it is"
        elif vrf not in vrf_tables:
            table = None
            why = ("FRR reports no routing table for it, so nothing on this "
                   "box holds its routes yet")
        else:
            table = vrf_tables[vrf]
            if table not in connected:
                why = f"the dataplane has no table {table}"
                table = None
        if table is None:
            unplaced.setdefault((vrf, why), []).append(line.strip())
            continue
        if not any(_covers(p, nexthop) for p in connected.get(table, [])):
            bad.append((line.strip(), nexthop))
    return bad, [(vrf, why, stmts) for (vrf, why), stmts in unplaced.items()]


# ------------------------------------------------------------- interfaces

_ADDR_IF = re.compile(r"^(\S+) \((up|dn)\):")
_ADDR_L3 = re.compile(r"^\s+L3 (\S+)")


def parse_interfaces(show_interface: str, show_addr: str = "",
                     show_lcp: str = "") -> dict:
    """
    The dataplane's interfaces: {name: {state, mtu, addresses, host}}.

    `show interface` in the VTY used to be handed to vtysh, which answers from
    FRR's view - and FRR sees only what linux_cp has mirrored. An operator
    asking what interfaces this box has was shown a list missing the ones that
    carry traffic; on a box with a GigabitEthernet0/12/0 configured, the answer
    was "lo".

    That is the same shape as `show ip route` answering from the RIB.
    """
    out = {}
    for line in (show_interface or "").splitlines():
        w = line.split()
        # The header is not indented and neither is a name; continuation lines
        # are, and the header starts with "Name".
        if not w or line[0].isspace() or w[0] == "Name":
            continue
        out[w[0]] = {
            "state": w[2] if len(w) > 2 else "?",
            "mtu": (w[3].split("/")[0] if len(w) > 3 else "?"),
            "addresses": [],
            "host": "",
        }

    current = None
    for line in (show_addr or "").splitlines():
        m = _ADDR_IF.match(line)
        if m:
            current = m.group(1)
            continue
        m = _ADDR_L3.match(line)
        if m and current in out:
            out[current]["addresses"].append(m.group(1))

    for name, names in lcp_pairs(show_lcp).items():
        if name in out:
            # The other names the pair answers to - the TAP and the Linux
            # end. A card paired as itself (#389) lists its own name twice,
            # `ens19 tap0 ens19`, and leaving out every occurrence of it
            # would print the TAP alone in the Linux column: the name FRR
            # acts on is the one that is on both sides, and it goes last, in
            # the position the Linux name has always had.
            others = list(dict.fromkeys(n for n in names if n != name))
            if names.count(name) > 1:
                others.append(name)
            out[name]["host"] = " ".join(others)
    return out


def lcp_pairs(show_lcp: str) -> dict:
    """
    Every name each linux_cp pair answers to, keyed by each of them.

    A pair prints as one line:

        itf-pair: [0] host-veth-vpp tap4096 vppctl0 5 type tap

    and all three names refer to the same interface. FRR and `ip` configure by
    the Linux one, the dataplane by its own, and a configuration naming either
    is correct - so a check that knew only VPP's names would refuse every FRR
    interface stanza on every box that has linux_cp, which is every box that
    routes. lab/routing.sh caught exactly that.

    Read as "every name on the line" rather than by position. The field order
    is a VPP implementation detail that has already differed between the two
    labs in this repository, and a positional parse is a parse that breaks on
    the next version - quietly, by finding nothing.
    """
    pairs = {}
    for line in (show_lcp or "").splitlines():
        if "itf-pair" not in line:
            continue
        names = []
        for tok in line.split():
            tok = tok.strip(",")
            if tok in ("itf-pair:", "type", "tap", "tun"):
                continue
            if tok.startswith("[") or tok.isdigit():
                continue
            names.append(tok)
        for n in names:
            pairs[n] = names
    return pairs


def parse_interface_counters(show_interface: str) -> dict:
    """
    Every counter the dataplane prints, per interface: {name: {counter: int}}.

    `show interface` puts the first counter on the SAME line as the interface
    and the rest on indented continuation lines:

        host-veth-vpp    1    up    9000/0/0/0    tx packets    3
                                                  tx bytes    266

    and an interface whose counters are all zero prints no counter line at
    all. So an absent counter is zero, not unknown - which matters, because
    "this interface has passed nothing" is the answer most of the time and a
    reader that treated it as unknown would have nothing to say about an idle
    box.

    The count is the LAST token and the counter name is everything between the
    fixed columns and it. Read that way rather than by position because the
    names have spaces in them ("rx packets") and new ones appear between VPP
    versions; a positional parse would find the wrong field the first time one
    does.
    """
    out = {}
    current = None
    for line in (show_interface or "").splitlines():
        w = line.split()
        if not w:
            continue
        if not line[0].isspace():
            if w[0] == "Name":
                current = None
                continue
            current = w[0]
            out.setdefault(current, {})
            rest = w[4:]
        else:
            rest = w
        if current is None or len(rest) < 2:
            continue
        try:
            value = int(rest[-1])
        except ValueError:
            continue
        out[current][" ".join(rest[:-1])] = value
    return out


#: What `show hardware-interfaces` calls each multiple. VPP prints the speed
#: as a human string ("10 Gbps"), so the only way to a number is this table.
_SPEED_UNITS = {"bps": 1, "kbps": 10 ** 3, "mbps": 10 ** 6,
                "gbps": 10 ** 9, "tbps": 10 ** 12}


def dpdk_name_address(name: str):
    """
    The (bus, slot, function) a DPDK-spelled interface name encodes, or None.

    VPP's DPDK plugin names a device nobody named after its PCI address: the
    device class as a word, then bus/slot/function in hexadecimal with no
    padding and no domain, so 0000:00:13.0 comes out as GigabitEthernet0/13/0
    and 0000:3b:00.1 as TenGigabitEthernet3b/0/1. Since #389 the tuner names
    every card it claims after the card's Linux name and nothing on a box
    prints this shape - except a configuration SAVED by a version that did,
    which is the one reader left: `hexash --load` matches the old name to the
    card this box now holds under its Linux name, by address. THE ONE COPY of
    VPP's naming rule in this product, and it reads the rule rather than
    writes it.

    None for anything else - local0, a tap, a loopback, an operator's own
    name, a sub-interface (the caller strips the tag first) - and then no
    rename is made: a guess here is a box loading somebody's addresses onto
    the wrong card.
    """
    m = re.fullmatch(r"[A-Za-z]+([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)",
                     name or "")
    if not m:
        return None
    return tuple(int(g, 16) for g in m.groups())


def parse_hardware_pci(show_hardware: str) -> dict:
    """
    {name: (domain, bus, slot, function)} for every interface the dataplane
    holds as a PCI card - the ones with a `pci:` row under them in
    `show hardware-interfaces`:

        ens19                              1     up   ens19
          ...
            pci: device 1af4:1041 subsystem 1af4:1100 address 0000:00:13.00 numa 0

    A pg, a tap, a host interface and a loopback have no such row and are not
    cards. This is what tells a claimed card - the interface that carries its
    Linux name and gets a pair under it by default (#389) - from every other
    interface a box or a lab can have, without a list of driver names.

    VPP prints the function with two digits (`13.00`); read as numbers so a
    saved name's `0/13/0` and this row compare without a spelling.
    """
    out = {}
    current = None
    for line in (show_hardware or "").splitlines():
        w = line.split()
        if not w:
            continue
        if not line[0].isspace():
            current = None if w[0] == "Name" else w[0]
            continue
        if current and w[:1] == ["pci:"] and "address" in w:
            addr = w[w.index("address") + 1]
            try:
                domain, bus, rest = addr.split(":")
                slot, func = rest.split(".")
                out[current] = (int(domain, 16), int(bus, 16), int(slot, 16),
                                int(func, 16))
            except ValueError:
                continue
    return out


def parse_hardware(show_hardware: str) -> dict:
    """
    Link state and link speed per interface: {name: {"link", "speed"}}.

    `show interface` reports the ADMIN state - what somebody configured. The
    link state lives in `show hardware-interfaces` and is what the wire is
    doing. Keeping them apart is the whole reason an operator asks for a terse
    listing: "up/down" is a cut fibre and "down/down" is a colleague who ran
    `shutdown`, and a single "state" column cannot tell anybody which.

    Sub-interfaces are not hardware and do not appear here; their link is
    their parent's, which the caller resolves - this function reports only
    what the dataplane actually said.

    The speed is "unknown" on every virtual interface, which is most of a lab
    and none of an appliance. None means unknown, and unknown has to stay
    distinguishable from zero: a utilisation bar drawn against a line rate the
    box does not know would be a number nobody could defend.
    """
    out = {}
    current = None
    for line in (show_hardware or "").splitlines():
        w = line.split()
        if not w:
            continue
        if not line[0].isspace():
            if w[0] == "Name":
                current = None
                continue
            current = w[0]
            out[current] = {"link": w[2] if len(w) > 2 else "?", "speed": None}
            continue
        if current and len(w) >= 3 and w[0] == "Link" and w[1] == "speed:":
            unit = _SPEED_UNITS.get(w[-1].lower())
            if unit is None:
                continue
            try:
                out[current]["speed"] = int(float(w[-2]) * unit)
            except ValueError:
                pass
    return out
