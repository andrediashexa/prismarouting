#!/usr/bin/env python3
"""
test_routing_leaks.py - appliance/prisma-routing-leaks (#541).

A connected route leaked into another VRF reaches the kernel as `dev <vrf>`
and never reached VPP: linux-nl has no VPP interface for a VRF device. The
daemon reads such routes out of the kernel's tables and puts each in VPP as a
lookup in the other VRF's table. This holds what it reads (which routes are
leaks through a VRF, and which are not), what it tells VPP, how it reads
VPP's answer, and that it removes only what it added - against the JSON
`ip` printed and the text `vppctl` printed in lab/policy-c.sh on 0.148.14.

Mutations shown red: accepting the table's own VRF device as a leak (the
kernel's own `local`/`unicast ... dev red` in red's table); reading the
path's `fib-index:` as the table id; matching a prefix as a substring of
VPP's answer (its covering 0.0.0.0/0 entry); deleting a route this daemon
did not add.
"""
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def load_leaks():
    """
    The script as a module, loaded through this function as test_tune.py
    loads the tuner, so that test/test_every_function.py reads
    `lk.leaked(...)` as a call into the product.
    """
    loader = importlib.machinery.SourceFileLoader(
        "routing_leaks", os.path.join(HERE, "prisma-routing-leaks"))
    spec = importlib.util.spec_from_loader("routing_leaks", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


lk = load_leaks()

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


# `ip -d -j link show type vrf`, as lab/policy-c.sh's box printed it, trimmed
# to what is read: the two tenant VRFs, and the containers' backplane.
LINKS = json.loads("""[
 {"ifindex":4,"ifname":"red","linkinfo":{"info_kind":"vrf","info_data":{"table":10}}},
 {"ifindex":5,"ifname":"svc","linkinfo":{"info_kind":"vrf","info_data":{"table":30}}},
 {"ifindex":3,"ifname":"prisma-bp","linkinfo":{"info_kind":"vrf","info_data":{"table":128}}},
 {"ifindex":6,"ifname":"br0","linkinfo":{"info_kind":"bridge","info_data":{}}}
]""")

# `ip -j -4 route show table 30`: svc's table with red imported. The first
# route is the one #537's lab found missing from VPP.
SVC4 = json.loads("""[
 {"dst":"10.10.0.0/24","nhid":31,"dev":"red","protocol":"bgp","metric":20,"flags":[]},
 {"dst":"10.11.0.0/24","nhid":40,"gateway":"10.10.0.2","dev":"red","protocol":"bgp","metric":20,"flags":["onlink"]},
 {"dst":"10.30.0.0/24","dev":"vppsvc0","protocol":"kernel","scope":"link","prefsrc":"10.30.0.1","flags":[]},
 {"dst":"203.0.113.0/24","nhid":22,"gateway":"10.30.0.2","dev":"vppsvc0","protocol":"bgp","metric":20,"flags":[]},
 {"type":"local","dst":"10.30.0.1","dev":"vppsvc0","protocol":"kernel","scope":"host","prefsrc":"10.30.0.1","flags":[]},
 {"type":"broadcast","dst":"10.30.0.255","dev":"vppsvc0","protocol":"kernel","scope":"link","prefsrc":"10.30.0.1","flags":[]},
 {"dst":"192.0.2.0/24","dev":"prisma-bp","protocol":"bgp","flags":[]},
 {"dst":"198.51.100.0/24","nhid":50,"protocol":"bgp","flags":[],"nexthops":[{"dev":"red"},{"gateway":"10.30.0.2","dev":"vppsvc0"}]},
 {"dst":"10.12.0.9","dev":"red","protocol":"static","flags":[]},
 {"dst":"default","dev":"red","protocol":"bgp","flags":[]}
]""")

# `ip -j -6 route show table 10`: red's own table - its VRF device appears in
# the kernel's own routes, and those are not leaks.
RED6 = json.loads("""[
 {"dst":"2001:db8:30::/64","dev":"svc","protocol":"bgp","metric":20,"flags":[]},
 {"dst":"fe80::/64","dev":"red","protocol":"kernel","metric":256,"flags":[]},
 {"type":"multicast","dst":"ff00::/8","dev":"red","table":"10","protocol":"kernel","metric":256,"flags":[]},
 {"dst":"fe80::/64","dev":"svc","protocol":"bgp","flags":[]},
 {"dst":"2001:db8:10::/64","dev":"red","protocol":"bgp","flags":[]}
]""")

# `vppctl show ip fib table 30 10.10.0.0/24` with the route added, as VPP
# 26.06 printed it in a lab. The path's fib-index is 1 - VPP's index of
# table 10, not the table id.
FIB_HAVE = """ipv4-VRF:30, fib_index:2, flow hash:[src dst sport dport proto flowlabel ] epoch:0 flags:none locks:[CLI:1, ]
10.10.0.0/24 fib:2 index:25 locks:2
  CLI refs:1 src-flags:added,contributing,active,
    path-list:[43] locks:2 flags:shared, uPRF-list:27 len:0 itfs:[]
      path:[45] pl-index:43 ip4 weight=1 pref=0 deag:  oper-flags:resolved,
         fib-index:1

 forwarding:   unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:27 buckets:1 uRPF:27 to:[0:0]]
    [0] [@13]: dst-address,unicast lookup in ipv4-VRF:10
"""
# The same question with the route absent: VPP answers with the covering
# entry, the table's default drop.
FIB_NONE = """ipv4-VRF:30, fib_index:2, flow hash:[src dst sport dport proto flowlabel ] epoch:0 flags:none locks:[CLI:1, ]
0.0.0.0/0 fib:2 index:12 locks:2
  default-route refs:1 entry-flags:drop, src-flags:added,contributing,active,
    path-list:[20] locks:2 flags:drop, uPRF-list:12 len:0 itfs:[]
      path:[20] pl-index:20 ip4 weight=1 pref=0 special:  cfg-flags:drop,
        [@0]: dpo-drop ip4

 forwarding:   unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:14 buckets:1 uRPF:12 to:[0:0]]
    [0] [@0]: dpo-drop ip4
"""

print("=== the VRFs: tenants only ===")
vrfs = lk.vrf_tables(LINKS)
check(vrfs == {"red": 10, "svc": 30},
      f"the VRF devices were read as {vrfs!r}")
check(lk.vrf_tables([{"ifname": "x", "linkinfo": {"info_kind": "vrf",
                                                  "info_data": {}}}]) == {},
      "a VRF with no table was taken")
check(lk.vpp_table(254) == 0 and lk.vpp_table(30) == 30,
      "the main table is not VPP's 0, or a VRF's table moved")
print("  red and svc; prisma-bp and the bridge are not read")

print("\n=== what is a leak through a VRF ===")
got = lk.leaked(SVC4, vrfs, 30, 4)
check((4, 30, "10.10.0.0/24", 10) in got,
      f"red's connected route in svc was not found: {sorted(got)!r}")
check(not any(p == "10.11.0.0/24" for _, _, p, _ in got),
      "a leaked route with a next hop (linux-nl's) was taken")
check(not any(p in ("10.30.0.0/24", "203.0.113.0/24", "10.30.0.1/32",
                    "10.30.0.255/32") for _, _, p, _ in got),
      "a route through a real interface, or the kernel's own, was taken")
check(not any(p == "192.0.2.0/24" for _, _, p, _ in got),
      "a route through prisma-bp was taken")
check(not any(p == "198.51.100.0/24" for _, _, p, _ in got),
      "an ECMP route with one path through a VRF was taken")
check((4, 30, "10.12.0.9/32", 10) in got,
      "a host route through a VRF was not spelled /32")
check((4, 30, "0.0.0.0/0", 10) in got,
      "a default route through a VRF was not spelled 0.0.0.0/0")
check(len(got) == 3, f"{len(got)} leaks in svc's table, not 3: {sorted(got)!r}")
got6 = lk.leaked(RED6, vrfs, 10, 6)
check(got6 == {(6, 10, "2001:db8:30::/64", 30)},
      f"red's IPv6 table gave {sorted(got6)!r}: its own device's routes, "
      f"or a link-local, must not be leaks")
main = lk.leaked([{"dst": "10.10.0.0/24", "dev": "red", "protocol": "bgp"}],
                 vrfs, 254, 4)
check(main == {(4, 0, "10.10.0.0/24", 10)},
      f"a leak into the default VRF did not go to VPP's table 0: {main!r}")
check(lk.prefix_of("default", 6) == "::/0"
      and lk.prefix_of("2001:db8::1", 6) == "2001:db8::1/128",
      "an IPv6 default or host route was misspelled")
print("  only a unicast route through another tenant VRF, with no next hop")

print("\n=== what VPP is told, and how its answer is read ===")
leak = (4, 30, "10.10.0.0/24", 10)
check(lk.route_command("add", leak)
      == "ip route add 10.10.0.0/24 table 30 via ip4-lookup-in-table 10",
      f"the add line was {lk.route_command('add', leak)!r}")
check(lk.route_command("del", (6, 0, "2001:db8::/64", 10))
      == "ip route del 2001:db8::/64 table 0 via ip6-lookup-in-table 10",
      "the IPv6 delete line was wrong")
check(lk.show_command(leak) == "show ip fib table 30 10.10.0.0/24"
      and lk.show_command((6, 30, "2001:db8::/64", 10))
      == "show ip6 fib table 30 2001:db8::/64",
      "the show line was wrong")
check(lk.installed(FIB_HAVE, leak), "VPP's entry with the lookup was not seen")
check(lk.installed(FIB_HAVE.replace("\n", " \r\n"), leak),
      "an answer whose lines end in a space and a carriage return was not "
      "read - the daemon would add the route again every tick")
check(not lk.installed(FIB_NONE, leak),
      "the covering 0.0.0.0/0 was read as the leak's entry")
check(not lk.installed(FIB_HAVE, (4, 30, "10.10.0.0/24", 1)),
      "the path's fib-index was read as the table id")
check(not lk.installed(FIB_HAVE.replace("VRF:10\n", "VRF:100\n"), leak),
      "a lookup in table 100 was read as one in table 10")
check(not lk.installed(FIB_HAVE.replace("10.10.0.0/24 fib", "10.10.0.0/25 fib"),
                       leak), "a longer prefix was read as the leak's")
check(lk.describe(leak, vrfs).startswith("10.10.0.0/24 in svc, through red"),
      f"the operator's words were {lk.describe(leak, vrfs)!r}")
_said, _lines = {}, []
lk.report_once(_said, leak, "refused: x", vrfs, _lines.append)
lk.report_once(_said, leak, "refused: x", vrfs, _lines.append)
lk.report_once(_said, leak, "installed", vrfs, _lines.append)
check(len(_lines) == 2 and _lines[1].endswith(": installed"),
      f"a refusal repeated every tick was said {len(_lines)} times, or the "
      f"change after it was not: {_lines!r}")
print("  add, delete and show lines; the entry matched by its line and table")


class FakeVpp:
    """A dataplane that holds routes as a set and answers like vppctl."""

    def __init__(self, routes=(), down=False, tables=(0, 10, 30)):
        self.routes = set(routes)
        self.down = down
        self.tables = set(tables)
        self.lines = []

    def __call__(self, line):
        self.lines.append(line)
        if self.down:
            return None
        w = line.split()
        if w[0] == "show":
            fam = 6 if w[1] == "ip6" else 4
            for r in self.routes:
                if r[0] == fam and r[1] == int(w[4]) and r[2] == w[5]:
                    return FIB_HAVE.replace("10.10.0.0/24", r[2]) \
                        .replace("VRF:10\n", f"VRF:{r[3]}\n")
            return FIB_NONE
        op, prefix, table = w[2], w[3], int(w[5])
        # ip route <op> <prefix> table <t> via ip<f>-lookup-in-table <s>
        fam, source = int(w[7][2]), int(w[8])
        if table not in self.tables:
            return f"ip route: Nonexistent table id {table}\n"
        r = (fam, table, prefix, source)
        if op == "add":
            self.routes.add(r)
        else:
            self.routes.discard(r)
        return ""


print("\n=== reconcile: only what it added, and back after a restart ===")
said = []
report = lambda leak, what: said.append((leak, what))
vpp = FakeVpp()
want = {leak, (4, 0, "10.10.0.0/24", 10)}
state = lk.reconcile(want, set(), vpp, report)
check(state == want and vpp.routes == want,
      f"the wanted leaks were not installed: {vpp.routes!r}")
check(sum(1 for _, w in said if w == "installed") == 2,
      f"each install was not reported once: {said!r}")
n = len(vpp.lines)
state = lk.reconcile(want, state, vpp, report)
check(not any(l.startswith("ip route") for l in vpp.lines[n:]),
      "a leak VPP already had was added again")
# The dataplane restarted: its tables are empty and the state still says
# both are ours.
vpp.routes.clear()
state = lk.reconcile(want, state, vpp, report)
check(vpp.routes == want, "the leaks were not put back after a restart")
# The import withdrawn: only svc's goes, and an operator's route that
# happens to look the same, never added by the daemon, is left alone.
operator = (4, 30, "10.99.0.0/24", 10)
vpp.routes.add(operator)
state = lk.reconcile({(4, 0, "10.10.0.0/24", 10)}, state, vpp, report)
check(leak not in vpp.routes and leak not in state,
      "a withdrawn leak was not removed")
check(operator in vpp.routes, "a route the daemon did not add was removed")
check((leak, "removed") in said, "the removal was not reported")
# A dataplane that does not answer: nothing is forgotten, nothing is said.
down = FakeVpp(down=True)
before = len(said)
kept = lk.reconcile(set(), state, down, report)
check(kept == state and len(said) == before,
      "a dataplane that did not answer lost the state or reported a change")
# A table VPP does not have: refused, reported, not owned.
nt = (4, 77, "10.10.0.0/24", 10)
state2 = lk.reconcile({nt}, set(), FakeVpp(), report)
check(nt not in state2 and any(w.startswith("refused: ip route: Nonexistent")
                               for l, w in said if l == nt),
      f"a missing table was not refused and reported: {said[-1:]!r}")
print("  installed once, put back after a restart, removed when withdrawn;")
print("  an operator's route and a dataplane that is down are left alone")

print("\n=== the state file ===")
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "sub", "leaks")
    check(lk.load_state(path) == set(), "a missing state file was not empty")
    lk.save_state(path, {leak, (6, 0, "2001:db8::/64", 10)})
    check(lk.load_state(path) == {leak, (6, 0, "2001:db8::/64", 10)},
          "the state did not read back")
    with open(path, "a") as f:
        f.write("garbage line\n")
    check(len(lk.load_state(path)) == 2, "a bad line was read as a leak")
print("  written whole and read back; a bad line is skipped")

print("\n=== wanted: the kernel's tables, read with `ip` ===")
TABLES = {"30": {4: SVC4, 6: []}, "10": {4: [], 6: RED6},
          "254": {4: [{"dst": "10.10.0.0/24", "dev": "red",
                       "protocol": "bgp"}], 6: []}}
asked = []


def fake_ip(argv):
    asked.append(argv)
    if "link" in argv:
        return json.dumps(LINKS)
    fam = int(argv[2][1])
    return json.dumps(TABLES.get(argv[-1], {}).get(fam, []))


w, v = lk.wanted(fake_ip)
check(v == vrfs, "wanted() read other VRFs")
check(w == lk.leaked(SVC4, vrfs, 30, 4) | lk.leaked(RED6, vrfs, 10, 6)
      | {(4, 0, "10.10.0.0/24", 10)},
      f"wanted() gave {sorted(w)!r}")
check(not any(a[-1] == "128" for a in asked),
      "prisma-bp's table was read")
check(lk.wanted(lambda a: None if "link" in a else "[]")[0] is None
      and lk.wanted(lambda a: None)[0] is None
      and lk.wanted(lambda a: "not json")[0] is None,
      "an `ip` that failed was read as no leaks - that would remove them all")
print("  main, red and svc read; prisma-bp not; a failed read is no answer")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
