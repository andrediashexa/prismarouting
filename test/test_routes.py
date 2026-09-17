#!/usr/bin/env python3
"""
The forwarding table, and telling it apart from FRR's.

Three tables hold routes and only one forwards packets. `show ip route` in
vtysh answers from FRR's, which is the table an operator reaches for when
traffic is not flowing - and the table where the route looks correct on a box
that is dropping it.

Everything here is driven from REAL "show ip fib" output, captured from a VPP
with two routes and an interface on it. A fixture written by hand would agree
with whatever the parser happens to do.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "hexash"))
import routes      # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


FIB = """ipv4-VRF:0, fib_index:0, flow hash:[src dst sport dport proto flowlabel ] epoch:0 flags:none locks:[adjacency:1, default-route:1, ]
0.0.0.0/0
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:1 buckets:1 uRPF:14 to:[0:0]]
    [0] [@5]: ipv4 via 200.150.0.2 pg0: mtu:9000 next:3 flags:[] 02000000000202fec40a847f0800
0.0.0.0/32
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:2 buckets:1 uRPF:1 to:[0:0]]
    [0] [@0]: dpo-drop ip4
10.0.0.0/8
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:14 buckets:1 uRPF:14 to:[0:0]]
    [0] [@5]: ipv4 via 200.150.0.2 pg0: mtu:9000 next:3 flags:[] 02000000000202fec40a847f0800
200.150.0.0/32
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:11 buckets:1 uRPF:10 to:[0:0]]
    [0] [@0]: dpo-drop ip4
200.150.0.0/24
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:10 buckets:1 uRPF:9 to:[0:0]]
    [0] [@4]: ipv4-glean: [src:200.150.0.0/24] pg0: mtu:9000 next:1 flags:[] ffffffffffff02fec40a847f0806
200.150.0.1/32
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:9 buckets:1 uRPF:8 to:[0:0]]
    [0] [@12]: dpo-receive: 200.150.0.1 on pg0
200.150.0.2/32
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:13 buckets:1 uRPF:13 to:[0:0]]
    [0] [@5]: ipv4 via 200.150.0.2 pg0: mtu:9000 next:3 flags:[] 02000000000202fec40a847f0800
200.150.0.255/32
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:12 buckets:1 uRPF:12 to:[0:0]]
    [0] [@0]: dpo-drop ip4
224.0.0.0/4
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:4 buckets:1 uRPF:3 to:[0:0]]
    [0] [@0]: dpo-drop ip4
240.0.0.0/4
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:3 buckets:1 uRPF:2 to:[0:0]]
    [0] [@0]: dpo-drop ip4
255.255.255.255/32
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:5 buckets:1 uRPF:4 to:[0:0]]
    [0] [@0]: dpo-drop ip4
"""

RIB = json.dumps({
    "0.0.0.0/0": [{"protocol": "static", "selected": True,
                   "nexthops": [{"ip": "200.150.0.2",
                                 "interfaceName": "pg0"}]}],
    # In FRR and NOT in the FIB: the failure this whole thing exists for.
    "198.19.0.0/24": [{"protocol": "isis", "selected": True,
                       "nexthops": [{"ip": "198.19.0.2",
                                     "interfaceName": "pg1"}]}],
})

print("=== the forwarding table, parsed from what VPP actually prints ===")
tables = routes.parse_fib(FIB)
check(list(tables) == [0], f"one VRF in this capture: {list(tables)}")
table = tables[0]

check("0.0.0.0/0" in table, "the default route is there")
check(table["0.0.0.0/0"]["kind"] == routes.FORWARD,
      f"and it forwards: {table['0.0.0.0/0']}")
check(table["0.0.0.0/0"]["via"] == [("200.150.0.2", "pg0")],
      f"with its next hop and interface: {table['0.0.0.0/0']['via']}")
check(table["200.150.0.0/24"]["kind"] == routes.CONNECTED,
      "the interface prefix is connected, not a forwarded route")
check(table["200.150.0.1/32"]["kind"] == routes.LOCAL,
      "the interface address is local")
check(table["224.0.0.0/4"]["kind"] == routes.DROP,
      "and the reserved ranges are explicitly dropped")
print(f"  {len(table)} entries, "
      f"{len(routes.forwarding(table))} of them carrying traffic")

print("\n=== a resolved neighbour is not a route ===")
# VPP creates a /32 per resolved neighbour. Listed beside three real routes
# they are most of the table, and a routing table that is mostly ARP is a
# routing table nobody reads.
check(table["200.150.0.2/32"]["kind"] == routes.NEIGHBOUR,
      f"the next hop's own /32 is an adjacency: {table['200.150.0.2/32']}")
check("200.150.0.2/32" not in routes.forwarding(table),
      "and it is not offered as a route")
check(set(routes.forwarding(table)) ==
      {"0.0.0.0/0", "10.0.0.0/8", "200.150.0.0/24"},
      f"what remains is what an operator configured: "
      f"{sorted(routes.forwarding(table))}")

print("\n=== the same next hop in two buckets is one path ===")
# VPP's load-balance lists an adjacency once per bucket, so a single path can
# appear several times. A table that shows it twice invites somebody to go
# looking for a second link that does not exist - while genuine ECMP, two
# DIFFERENT next hops, has to survive.
BUCKETS = """ipv4-VRF:0, fib_index:0, flow hash:[src dst] epoch:0 flags:none
10.1.0.0/16
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:20 buckets:2 uRPF:20 to:[0:0]]
    [0] [@5]: ipv4 via 200.150.0.2 pg0: mtu:9000 next:3 flags:[] 0200
    [1] [@5]: ipv4 via 200.150.0.2 pg0: mtu:9000 next:3 flags:[] 0200
10.2.0.0/16
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:21 buckets:2 uRPF:21 to:[0:0]]
    [0] [@5]: ipv4 via 200.150.0.2 pg0: mtu:9000 next:3 flags:[] 0200
    [1] [@5]: ipv4 via 200.150.0.3 pg1: mtu:9000 next:3 flags:[] 0200
"""
b = routes.parse_fib(BUCKETS)[0]
check(b["10.1.0.0/16"]["via"] == [("200.150.0.2", "pg0")],
      f"one path listed twice is one path: {b['10.1.0.0/16']['via']}")
check(b["10.2.0.0/16"]["via"] == [("200.150.0.2", "pg0"),
                                  ("200.150.0.3", "pg1")],
      f"two different next hops are two paths: {b['10.2.0.0/16']['via']}")
print(f"  one bucket pair -> {b['10.1.0.0/16']['via']}")
print(f"  genuine ECMP    -> {b['10.2.0.0/16']['via']}")

print("\n=== FRR's table ===")
rib = routes.parse_rib(RIB)
check(set(rib) == {"0.0.0.0/0", "198.19.0.0/24"}, f"two prefixes: {sorted(rib)}")
check(rib["198.19.0.0/24"][0]["protocol"] == "isis",
      "the protocol that put it there is kept")

# An unreadable answer must not read as an empty table: a comparison that
# silently lost one of its two sides would report every route as missing from
# FRR, which is a loud alarm about nothing.
check(routes.parse_rib("this is not json") == {},
      "unparseable output is empty, not a guess")
check(routes.parse_rib("") == {}, "and so is no output at all")

print("\n=== which prefixes came from which protocol ===")
# Real "show ip route json", trimmed to the four prefixes that matter, from a
# box with OSPF and IS-IS both converged. 10.0.0.0/24 is the subtlety: an
# IS-IS box learns its own connected link back from its neighbour, zebra keeps
# the connected route, and the IS-IS copy is held and never installed.
IGP_RIB = json.dumps({
    "0.0.0.0/0": [{"protocol": "kernel", "selected": True, "installed": True,
                   "nexthops": [{"ip": "172.17.0.1",
                                 "interfaceName": "eth0"}]}],
    "10.0.0.0/24": [
        {"protocol": "isis", "distance": 115, "metric": 20,
         "nexthops": [{"ip": "10.0.0.2", "interfaceName": "vppwan0"}]},
        {"protocol": "connected", "selected": True, "installed": True,
         "nexthops": [{"interfaceName": "vppwan0"}]}],
    "198.18.0.0/24": [{"protocol": "ospf", "selected": True, "installed": True,
                       "nexthops": [{"ip": "10.0.0.2",
                                     "interfaceName": "vppwan0"}]}],
    "198.19.0.0/24": [{"protocol": "isis", "selected": True, "installed": True,
                       "nexthops": [{"ip": "10.0.0.2",
                                     "interfaceName": "vppwan0"}]}],
})
igp = routes.parse_rib(IGP_RIB)
check(routes.protocol_prefixes(igp, "ospf") == ["198.18.0.0/24"],
      f"the OSPF prefix: {routes.protocol_prefixes(igp, 'ospf')}")
check(routes.protocol_prefixes(igp, "isis") == ["198.19.0.0/24"],
      f"the IS-IS prefix, and NOT the copy zebra did not choose: "
      f"{routes.protocol_prefixes(igp, 'isis')}")
check("10.0.0.0/24" in igp and len(igp["10.0.0.0/24"]) == 2,
      "though both entries for it were parsed, so this is a choice")
check(routes.protocol_prefixes(igp, "bgp") == [],
      "a protocol with nothing in the table has nothing")
check(routes.protocol_prefixes({}, "ospf") == [],
      "and an empty table is not a crash")

print("\n=== the comparison, which is the point ===")
rows = routes.compare(table, rib)
by_prefix = {r[0]: r for r in rows}

_, in_rib, in_fib, kind, _, _ = by_prefix["0.0.0.0/0"]
check(in_rib and in_fib, "a route both tables hold is reported in both")
check(kind == routes.FORWARD, f"with what VPP holds: {kind}")

_, in_rib, in_fib, kind, via, iface = by_prefix["198.19.0.0/24"]
check(in_rib and not in_fib,
      "a route FRR holds and VPP does not is the disagreement")
check(kind == "absent", f"and VPP is reported as not having it: {kind}")
check((via, iface) == ("198.19.0.2", "pg1"),
      f"FRR's next hop is shown, since it is the only one there is: {via}")

# The half that is easy to get wrong. A prefix VPP is explicitly DROPPING and a
# prefix VPP has never heard of are different situations with different causes,
# and reporting both as absent is a false statement in the one table whose job
# is to settle a disagreement.
_, _, in_fib, kind, _, _ = by_prefix["224.0.0.0/4"]
check(kind == routes.DROP,
      f"a prefix VPP drops is reported as dropped, not as absent: {kind}")
check(not in_fib, "and it does not count as forwarding")

# The order written out, not re-derived. This used to sort the rows' own
# prefixes with routes._sort_key - the very function compare() sorted them
# with - so both sides changed together and the sentence was never tested:
# _sort_key could return a constant and the rows come out in dict order, and
# this stayed green. A /24 before a /32 on the same network is the tie-break
# that makes it an ADDRESS order rather than a string one.
check([r[0] for r in rows] == [
          "0.0.0.0/0", "0.0.0.0/32", "10.0.0.0/8", "198.19.0.0/24",
          "200.150.0.0/24", "200.150.0.0/32", "200.150.0.1/32",
          "200.150.0.2/32", "200.150.0.255/32", "224.0.0.0/4",
          "240.0.0.0/4", "255.255.255.255/32"],
      f"rows come out in address order: {[r[0] for r in rows]}")
print(f"  {len(rows)} prefixes across both tables, "
      f"{sum(1 for r in rows if r[1] and not r[2])} disagreeing")

# --- the linux_cp pair, pinned to what VPP actually prints ---
# Captured from a running box, not remembered. Two things in the product read
# this output, and both of them fail by finding nothing rather than by raising,
# which is the failure that reaches production quietly.
LCP = """Linux-CP Interface Pairs:
itf-pair: [0] host-veth-vpp tap4096 vppwan0 5 type tap
itf-pair: [1] host-veth-mgmt tap4097 vppmgmt0 6 type tap
"""

pairs = routes.lcp_pairs(LCP)
check("host-veth-vpp" in pairs, "the pair is found by its dataplane name")
check("vppwan0" in pairs, "and by its Linux name")
check("tap4096" in pairs, "and by its TAP name")
check(pairs["vppwan0"] == pairs["host-veth-vpp"],
      "every name on a line resolves to the same set of names")
check("vppwan0" in pairs["host-veth-vpp"],
      "so the Linux side of a dataplane interface is reachable from it")
check(len(pairs["host-veth-vpp"]) == 3,
      f"three names, no stray tokens: {pairs['host-veth-vpp']}")
check("tap" not in pairs and "type" not in pairs,
      "the trailing 'type tap' is not mistaken for an interface")
check(pairs["host-veth-mgmt"] != pairs["host-veth-vpp"],
      "two pairs stay two pairs")

# A CARD PAIRED AS ITSELF (#389): the tuner names the card ens19 and the
# pair's Linux end is ens19 too, so the line carries the name twice. Read by
# `show interface`'s Linux column, which used to drop every occurrence of the
# interface's own name and would have printed the TAP alone.
SAME = "itf-pair: [0] ens19 tap0 ens19 5 type tap\n"
_if = routes.parse_interfaces(
    "              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)\n"
    "ens19                             1      up          9000/0/0/0\n"
    "tap0                              2      up          9000/0/0/0\n",
    "", SAME)
check(_if["ens19"]["host"] == "tap0 ens19",
      f"a card paired as itself does not show its Linux end, only the TAP: "
      f"{_if['ens19']['host']!r}")
check(_if["tap0"]["host"] == "ens19",
      f"and the TAP's row names the card once, not twice: "
      f"{_if['tap0']['host']!r}")
_old = routes.parse_interfaces(
    "              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)\n"
    "host-veth-vpp                     1      up          9000/0/0/0\n",
    "", LCP)
check(_old["host-veth-vpp"]["host"] == "tap4096 vppwan0",
      f"and a pair with its own Linux name reads as it did: "
      f"{_old['host-veth-vpp']['host']!r}")
print("  ens19 tap0 ens19 -> Linux column 'tap0 ens19'")

# --- VPP's DPDK name, read back to an address (#389) ---
# The bus is hexadecimal and the second socket's cards in a dual-socket
# server sit at 0xaf, 0xb1, 0xd8: a letter first. The class word is letters
# too, and a greedy run of letters ate the bus's first digit, so
# TenGigabitEthernetd8/0/0 read as bus 8. `hexash --load` then matched a
# saved name to the card at 08:00.0 where the box had one - the wrong card,
# silently - and refused the file where it had not. Measured on 0dbbf73.
for _name, _want in (("GigabitEthernet0/13/0", (0, 0x13, 0)),
                     ("TenGigabitEthernet3b/0/1", (0x3b, 0, 1)),
                     ("TenGigabitEthernetd8/0/0", (0xd8, 0, 0)),
                     ("HundredGigabitEthernetaf/0/1", (0xaf, 0, 1)),
                     ("GigabitEthernete/0/0", (0xe, 0, 0)),
                     ("VirtualFunctionEthernetaf/0/2", (0xaf, 0, 2))):
    check(routes.dpdk_name_address(_name) == _want,
          f"{_name} reads as {routes.dpdk_name_address(_name)}, not "
          f"{tuple(hex(x) for x in _want)}")
for _name in ("ens19", "local0", "tap0", "loop0", "host-veth-vpp",
              "GigabitEthernet0/13/0.100", ""):
    check(routes.dpdk_name_address(_name) is None,
          f"{_name!r} is not a DPDK name and must not be read as one: "
          f"{routes.dpdk_name_address(_name)}")
print("  TenGigabitEthernetd8/0/0 -> bus 0xd8, not 8")

# --- the Linux end of a pair, ONE owner (#389) ---
# Five readers in the product found the Linux end by `name != ifname`. Right
# on `GigabitEthernet0/13/0 tap0 vppwan0`; on the pair every claimed card
# gets now, `ens19 tap0 ens19`, that test skips the one name that IS the
# Linux end and keeps the TAP, which Linux has no netdev by. The rule is
# written here once: the name the line lists twice, else the member that is
# neither the dataplane's name nor an lcp TAP.
_same = routes.lcp_pairs(SAME)
check(routes.linux_end(_same, "ens19") == "ens19",
      f"a card paired as itself: the Linux end is ens19, not "
      f"{routes.linux_end(_same, 'ens19')!r}")
check(routes.linux_end(_same, "tap0", {"ens19", "tap0"}) == "ens19",
      f"asked with the TAP, the same pair has the same Linux end: "
      f"{routes.linux_end(_same, 'tap0', {'ens19', 'tap0'})!r}")
check(routes.linux_end(pairs, "host-veth-vpp") == "vppwan0",
      f"the old shape: the member that is neither the card nor the TAP: "
      f"{routes.linux_end(pairs, 'host-veth-vpp')!r}")
check(routes.linux_end(pairs, "tap4096", {"host-veth-vpp", "tap4096"})
      == "vppwan0",
      "asked with the TAP and the dataplane's list, still the Linux end")
check(routes.linux_end(pairs, "vppwan0", {"host-veth-vpp", "tap4096"})
      == "vppwan0",
      "asked with the Linux name itself and the dataplane's list, the same")
check(routes.linux_end({}, "ens19") is None,
      "an interface with no pair has no Linux end, not a wrong one")
check(routes.linux_end({"ens19": ["ens19", "tap0"]}, "ens19") is None,
      "a pair whose only other name is the TAP has no Linux end either")
# ASKED WITH THE TAP AND NO LIST. The three `ip link set` mirrors are keyed
# by the operator's block name and hold no list, and `interface tap0` is a
# block the parser takes: without the dataplane's names the TAP cannot be
# told from the card, and rule 2 took the TAP as the interface's own and
# answered the CARD - linux_end(OLD, 'tap0') -> 'GigabitEthernet0/13/0', a
# name Linux has no netdev by, and on the override pair 'ens19 tap0 wan0'
# it answered 'ens19' where the Linux end is wan0. "Could not place" is
# None, never a wrong netdev.
OVERRIDE = "itf-pair: [0] ens19 tap0 wan0 5 type tap\n"
_over = routes.lcp_pairs(OVERRIDE)
check(routes.linux_end(pairs, "tap4096") is None,
      f"asked with the TAP and no list, the old shape: the pair cannot be "
      f"placed and the answer is None, not "
      f"{routes.linux_end(pairs, 'tap4096')!r}")
check(routes.linux_end(_over, "tap0") is None,
      f"asked with the TAP and no list, the override pair: None, not "
      f"{routes.linux_end(_over, 'tap0')!r}")
check(routes.linux_end(_over, "tap0", {"ens19", "tap0"}) == "wan0",
      f"asked with the TAP and the dataplane's list, the override pair's "
      f"Linux end is wan0, not "
      f"{routes.linux_end(_over, 'tap0', {'ens19', 'tap0'})!r}")
check(routes.linux_end(_over, "ens19", {"ens19", "tap0"}) == "wan0",
      f"asked with the card, the override pair's Linux end is wan0, not "
      f"{routes.linux_end(_over, 'ens19', {'ens19', 'tap0'})!r}")
# And the other half of the rule, the member the dataplane holds: asked with
# FRR's name and the dataplane's list, which is what `show ospf interface`
# has in hand. The old-shape fixture is keyed by the Linux name alone, as a
# hand-written pairs dict in test_hexash_igp.py is, and the answer must not
# depend on the pair being reachable under any other key.
check(routes.dataplane_end(_same, "ens19", {"ens19", "tap0"}) == "ens19",
      f"the same-name pair: the dataplane's end is ens19, not "
      f"{routes.dataplane_end(_same, 'ens19', {'ens19', 'tap0'})!r}")
check(routes.dataplane_end({"vppwan0": ["tap4096", "host-veth-vpp",
                                        "vppwan0"]},
                           "vppwan0", {"tap4096", "host-veth-vpp"})
      == "host-veth-vpp",
      "the old shape, keyed by FRR's name: the card, and never the TAP")
check(routes.dataplane_end({}, "eth0", {"host-veth-vpp"}) is None,
      "an interface with no pair is nobody's dataplane end")
# THE LIST IS THE RULE, NOT THE ORDER. `show lcp` happens to print the
# dataplane's name first, so a reader that took the first non-TAP member
# answers right on every real line and is wrong the day the order moves -
# which lcp_pairs() says it must not be relied on. Held with the Linux name
# first: only `n in dp_names` picks the card out.
check(routes.dataplane_end({"vppwan0": ["vppwan0", "tap4096",
                                        "host-veth-vpp"]},
                           "vppwan0", {"host-veth-vpp", "tap4096"})
      == "host-veth-vpp",
      f"the Linux name listed first: the dataplane's end is the member the "
      f"list holds, not "
      f"""{routes.dataplane_end({"vppwan0": ["vppwan0", "tap4096",
                                             "host-veth-vpp"]},
                                "vppwan0", {"host-veth-vpp", "tap4096"})!r}""")
print("  ens19 tap0 ens19 -> Linux end ens19; the old shape -> vppwan0")

# --- the interfaces VPP owns, and the faults that depend on knowing them ---
#
# The claim "the old positional parse cannot work" was held to account against
# a COPY of the parse written out here, not against the function the product
# calls - so the copy was right, the claim was right, and
# routes.vpp_host_interfaces went on carrying that parse and returning an
# empty set on every box. Asked of the function now.
#
# The empty set is not a small error. compare() suppresses a route FRR holds
# and the FIB does not when its interface is in NO VPP-owned name, so with an
# empty set every such route is suppressed - and "in RIB, not in FIB", the one
# report that finds a route the dataplane is missing, found none on any real
# box, silently.
HOSTS = routes.vpp_host_interfaces(LCP)
check("host-veth-vpp" in HOSTS,
      f"the pair's Linux name is a VPP-owned interface: {sorted(HOSTS)}")
check("vppwan0" in HOSTS, "and the name the dataplane knows it by")
check("host-veth-mgmt" in HOSTS, "both pairs, not only the first")
check(routes.vpp_host_interfaces("") == set(),
      "no linux_cp output is no interfaces, not a crash")

# The other half of "which interfaces VPP owns", and it was missing.
#
# `show lcp` names only the PAIRS. A box can have none - lab/bng.sh's has none
# at all - and on that box the set was empty, so every route FRR held was
# judged against nothing. Three of the four the report then raised were the
# container's own network through eth0: the management interface, which is
# precisely what the suppression exists for.
#
# An interface the dataplane knows by NAME is one VPP owns whether or not
# anything paired it. `host-wan0` is VPP's and `eth0` is not, and that is the
# whole distinction the report needs on a box with no pairs.
IFACES = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)
host-wan0                         1      up          9000/0/0/0
local0                            0     down          0/0/0/0
"""
OWN = routes.vpp_owned_interfaces(LCP, IFACES)
check("host-wan0" in OWN,
      f"an interface the dataplane names is VPP-owned without a pair: {sorted(OWN)}")
check("eth0" not in OWN, "and the management interface still is not")
NOPAIR_RIB = json.dumps({
    "172.17.0.0/16": [{"protocol": "kernel", "selected": True,
                       "nexthops": [{"ip": "172.17.0.1",
                                     "interfaceName": "eth0"}]}],
    "198.19.0.0/24": [{"protocol": "isis", "selected": True,
                       "nexthops": [{"ip": "198.19.0.2",
                                     "interfaceName": "host-wan0"}]}],
})
# Through the function the product calls, with NO pair output at all -
# which is the box this was measured on.
nrows = {r[0]: r for r in
         routes.compare({}, routes.parse_rib(NOPAIR_RIB),
                        routes.vpp_owned_interfaces("", IFACES))}
check(not nrows["172.17.0.0/16"][1],
      "on a box with NO pair, a route through eth0 is still not a fault")
check(nrows["198.19.0.0/24"][1],
      "while one through an interface the dataplane names still is")

# The branch that USES it, which had no test at all: compare()'s third
# argument decides whether a missing route is a fault or is none of VPP's
# business, and nothing above this line ever passed it one.
MGMT_RIB = json.dumps({
    # Reached through an interface VPP owns. The dataplane should hold this,
    # and does not: that is the disagreement the report exists for.
    "198.19.0.0/24": [{"protocol": "isis", "selected": True,
                       "nexthops": [{"ip": "198.19.0.2",
                                     "interfaceName": "host-veth-vpp"}]}],
    # Reached through the management interface, which is deliberately not a
    # linux_cp pair. Permanently absent from the FIB, and correctly so.
    "192.168.9.0/24": [{"protocol": "static", "selected": True,
                        "nexthops": [{"ip": "192.168.9.1",
                                      "interfaceName": "eno1"}]}],
})
mrows = {r[0]: r for r in
         routes.compare({}, routes.parse_rib(MGMT_RIB), HOSTS)}
check(mrows["198.19.0.0/24"][1],
      "a route through an interface VPP owns and does not hold is a fault")
check(not mrows["192.168.9.0/24"][1],
      "a route through the management interface is not")
# And with nothing known about what VPP owns, both are faults rather than
# neither: an unanswered "show lcp" must not silence the report.
srows = {r[0]: r for r in
         routes.compare({}, routes.parse_rib(MGMT_RIB), set())}
check(srows["198.19.0.0/24"][1] and srows["192.168.9.0/24"][1],
      "an empty set of VPP interfaces reports everything, not nothing")

check(routes.lcp_pairs("") == {}, "no output is no pairs, not a crash")
check(routes.lcp_pairs("Linux-CP Interface Pairs:\n") == {},
      "a header with no pairs is no pairs")

print("\n=== a NAMED fib table is not the table above it (#268) ===")
# `show ip fib` prints a table with no name as `ipv4-VRF:10` and a table WITH a
# name as `vrf7, fib_index:2`. The parser matched only the first, so every
# prefix in a named table was filed under whichever table came before it - and
# `Shell.set_vrf()` names every table it creates, so this was every box where
# an operator had typed `interface <name> / vrf <n>`.
#
# It reported MORE than the box has, which is the hard direction to notice:
# `show route` listed a VRF's routes as the global table's, and the next-hop
# reachability check accepted a route whose next hop is connected only in some
# other table - the exact silent-and-inert route it exists to refuse.
NAMED_FIB = """ipv4-VRF:0, fib_index:0, flow hash:[src dst ] epoch:0 flags:none locks:[default-route:1, ]
200.150.0.0/24
  unicast-ip4-chain
    [0] [@4]: ipv4-glean: [src:200.150.0.0/24] pg1: mtu:9000
vrf7, fib_index:2, flow hash:[src dst ] epoch:0 flags:none locks:[CLI:1, ]
10.7.0.0/24
  unicast-ip4-chain
    [0] [@4]: ipv4-glean: [src:10.7.0.0/24] pg2: mtu:9000
"""

_NAMES = routes.table_names(
    [{"table": {"table_id": 7, "is_ip6": False, "name": "vrf7"}},
     {"table": {"table_id": 7, "is_ip6": True, "name": "vrf7-v6"}}])
check(_NAMES == {"vrf7": 7},
      f"the name-to-id map is not what ip_table_dump says, or it took the v6 "
      f"table's name for the v4 one: {_NAMES}")

_t = routes.parse_fib(NAMED_FIB, _NAMES)
check("10.7.0.0/24" not in _t.get(0, {}),
      f"a prefix that is only in table 7 is reported as the global table's. "
      f"show route would list a VRF's routes as table 0's, and the next-hop "
      f"check would accept a route whose next hop is connected nowhere near "
      f"it: {sorted(_t.get(0, {}))}")
check(sorted(_t.get(7, {})) == ["10.7.0.0/24"],
      f"the named table's prefix did not land in table 7: {_t}")
check(sorted(_t.get(0, {})) == ["200.150.0.0/24"],
      f"the global table lost or gained a prefix: {_t}")

# And with no map at all, which is what a box with no binary API gives. The
# prefixes must still not be attributed to the table above: keyed by the name,
# no numeric lookup finds them, so the answer is "not known here".
_u = routes.parse_fib(NAMED_FIB)
check("10.7.0.0/24" not in _u.get(0, {}),
      f"with no name map the named table's prefix was filed under the table "
      f"above it, which is the defect itself: {sorted(_u.get(0, {}))}")
check("vrf7" in _u,
      f"with no name map the named table's prefixes were dropped, so a box "
      f"with no API would silently lose a VRF from show route: {sorted(_u, key=str)}")

# The unnamed form has to keep working, or this section trades one wrong answer
# for another.
_v = routes.parse_fib(NAMED_FIB.replace("vrf7, fib_index:2", "ipv4-VRF:7, fib_index:2"))
check(sorted(_v.get(7, {})) == ["10.7.0.0/24"],
      f"an unnamed table stopped being read: {_v}")
print("  a named table lands under its own id, and under its name when "
      "nobody can be asked")


print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
