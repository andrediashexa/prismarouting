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
_VRF = re.compile(r"^ipv4-VRF:(\d+)")
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


def parse_fib(text: str) -> dict:
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


def vpp_host_interfaces(lcp_text: str) -> set:
    """
    The Linux-side names of the interfaces VPP owns, from "show lcp".

    Needed because FRR knows routes this box will never forward and SHOULD NOT
    forward: the management interface is deliberately kept away from VPP - hand
    it over and the box is unreachable and unfixable remotely - so every route
    through it is in FRR and absent from the FIB, correctly and permanently.

    Reporting those as faults would put a handful of standing alarms on every
    real box, and an alarm that is always on is one nobody reads.
    """
    hosts = set()
    for line in (lcp_text or "").splitlines():
        parts = line.split()
        for i, tok in enumerate(parts):
            if tok == "host" and i + 1 < len(parts):
                hosts.add(parts[i + 1].rstrip(","))
    return hosts


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
        if in_rib and vpp_hosts is not None and not in_fib:
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


def unreachable_next_hops(routing: list, table: dict) -> list:
    """
    Static routes whose next hop is on no connected network.

    A next hop the box cannot reach is a route that is accepted, installed, and
    silently inert: FRR holds it, the FIB never gets it, and `show ip route`
    looks correct. It is the exact shape of failure `show route verbose` exists
    to make visible afterwards - and it is cheaper to refuse it at the prompt
    than to have somebody find it during an outage.

    Only the "<prefix> <next-hop>" form is checked. `ip route <prefix>
    <interface>`, blackhole and Null0 name no address to verify, and guessing
    at them would refuse configurations that are correct.

    Returns [(statement, next_hop)] - empty when everything resolves.
    """
    connected = [p for p, e in table.items() if e["kind"] == CONNECTED]
    bad = []
    for line in routing:
        m = _IP_ROUTE.match(line)
        if not m:
            continue
        nexthop = m.group(2)
        if _addr_int(nexthop) is None:
            continue  # an interface name, blackhole, Null0: nothing to check
        if not any(_covers(p, nexthop) for p in connected):
            bad.append((line.strip(), nexthop))
    return bad


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
            out[name]["host"] = " ".join(n for n in names if n != name)
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
