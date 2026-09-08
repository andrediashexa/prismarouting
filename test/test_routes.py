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

check([r[0] for r in rows] == sorted((r[0] for r in rows),
                                     key=routes._sort_key),
      "rows come out in address order")
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

# The parse this replaced looked for a literal 'host' token and read the next
# field. There is no such token. It is kept here as a test rather than a
# comment because 'this cannot work' is a claim, and a claim about a parse is
# worth the four lines it takes to hold it to account.
positional = [ln.split()[ln.split().index("host") + 1]
              for ln in LCP.splitlines()
              if "host" in ln.split()]
check(positional == [],
      "the old positional parse finds nothing at all in this output")

check(routes.lcp_pairs("") == {}, "no output is no pairs, not a crash")
check(routes.lcp_pairs("Linux-CP Interface Pairs:\n") == {},
      "a header with no pairs is no pairs")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
