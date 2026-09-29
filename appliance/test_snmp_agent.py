#!/usr/bin/env python3
"""
Every function in prisma-routing-snmp-agent, called on its own (#390).

The agent speaks net-snmp's pass_persist protocol over stdin and asks the
dataplane through vppctl, and the two rules it exists for are READ-ONLY and
NO STALE NUMBERS. The second is the one that goes wrong quietly: a cache that
also cached the liveness check served the first ten seconds of an outage as
a healthy box, measured in a lab. So the liveness check, the cache, the OID
table and the parsers are each driven here against a stub vppctl on disk,
whose answers a case changes between two calls. Nothing here reads the
machine that runs it: VPPCTL and PRISMA_SNMPD_CONF are pointed into a scratch
directory before the agent is loaded, because it reads both at import.

Each check was run against a mutation of the rule it holds and required to
fail before being kept.
"""
import contextlib
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.join(HERE, "prisma-routing-snmp-agent")

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def w(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(str(content))


BOX = tempfile.mkdtemp()

# The stub dataplane. Its answers are files beside it, so a case rewrites a
# file between two calls and the agent's cache is what decides which answer
# comes back; `down` makes every command fail, which is a dataplane that is
# not answering.
w(f"{BOX}/vppctl", f"""#!/bin/sh
D="{BOX}"
[ -e "$D/down" ] && exit 1
case "$*" in
  "show version") echo "vpp v26.06-release" ;;
  "bpa timeouts") cat "$D/timeouts" ;;
  "show bpa top 100000") cat "$D/top" ;;
  "show bpa drops") cat "$D/drops" ;;
  "bpa nosuch") echo "bpa: unknown input 'nosuch'" ;;
  "show bpa nosuch") echo "show bpa: unknown input 'nosuch'" ;;
  "exit 3") exit 3 ;;
  *) echo "unknown input" ;;
esac
""")
os.chmod(f"{BOX}/vppctl", 0o755)
w(f"{BOX}/timeouts", "udp 300  icmp 60  tcp-syn 6  tcp-transitory 240  "
                     "tcp-established 7440  gre 300  port-holddown 120  "
                     "alg-expect 30\n")
w(f"{BOX}/top", "subscriber       pool   used/total   pct  blocks  refusals\n"
                "100.64.0.1       p1     900/1024     87%  1       0\n"
                "100.64.0.2       p1     1000/1024    97%  1       5\n"
                "100.64.0.3       p1     10/1024      0%   1       0\n"
                "a line that is not a subscriber row\n")
w(f"{BOX}/drops", "bpa drops\n"
                  "  packets that did not get through\n"
                  "  40 no free port\n"
                  "  2 subscriber not in any pool\n"
                  "  everything else it counted\n"
                  "  9999 in2out packets\n")
os.environ["VPPCTL"] = f"{BOX}/vppctl"
os.environ["PRISMA_SNMPD_CONF"] = f"{BOX}/snmpd.conf"
os.environ.pop("PRISMA_SNMP_ENTERPRISE", None)


def load_agent():
    """
    The agent as a module, loaded once from its path.

    It has no .py suffix and dashes in its name, so `import` cannot reach
    it. Loaded through THIS function and bound at module level, so that
    test/test_every_function.py reads `agent.as_key(...)` as a call into the
    product: the name roots at a function this file defines.
    """
    import importlib.machinery
    import importlib.util
    if "prisma_routing_snmp_agent" in sys.modules:
        return sys.modules["prisma_routing_snmp_agent"]
    loader = importlib.machinery.SourceFileLoader("prisma_routing_snmp_agent",
                                                  AGENT)
    spec = importlib.util.spec_from_file_location("prisma_routing_snmp_agent",
                                                  AGENT, loader=loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    sys.modules["prisma_routing_snmp_agent"] = module
    return module


agent = load_agent()

print("=== the enterprise number, read off snmpd.conf and nowhere else ===")
check(agent.ENTERPRISE == "",
      f"with no snmpd.conf the agent came up with an enterprise number "
      f"{agent.ENTERPRISE!r}, and an OID under a PEN nobody set is a lie "
      f"about who the object belongs to")
check(agent._enterprise() == "",
      "with no snmpd.conf the PEN was not empty")
# FROM THE AGENT'S OWN REGISTRATION (#497), on the file the VTY generates:
# the view carries UCD-SNMP-MIB's .1.3.6.1.4.1.2021 before the operator's
# subtree, and a view-line read answered 2021 - the CGNAT served under
# net-snmp's number, measured in lab/snmp.sh.
w(f"{BOX}/snmpd.conf", "rocommunity public\n"
                       "view hexa included .1.3.6.1.2.1.1\n"
                       "view hexa included .1.3.6.1.4.1.2021\n"
                       "view hexa included .1.3.6.1.2.1.123\n"
                       "view hexa included .1.3.6.1.4.1.99999\n"
                       "pass_persist .1.3.6.1.2.1.123 /x/agent\n"
                       "pass_persist .1.3.6.1.4.1.99999 /x/agent\n")
check(agent._enterprise() == "99999",
      f"with UCD's 2021 in the view the PEN read as "
      f"{agent._enterprise()!r}, and it is 99999")
w(f"{BOX}/snmpd.conf", "view hexa included .1.3.6.1.4.1.2021\n"
                       "view hexa included .1.3.6.1.2.1.123\n"
                       "pass_persist .1.3.6.1.2.1.123 /x/agent\n")
check(agent._enterprise() == "",
      f"a conf with no enterprise registration gave "
      f"{agent._enterprise()!r}")
# ON ITS OWN COMMAND LINE (#499): the agent runs as Debian-snmp and cannot
# open the 0600 snmpd.conf, so the number comes on the pass_persist line.
os.chmod(f"{BOX}/snmpd.conf", 0)
_argv = sys.argv
sys.argv = ["snmp-agent", "--enterprise", "31337"]
check(agent._enterprise() == "31337",
      f"the number on the command line was read as {agent._enterprise()!r}")
sys.argv = ["snmp-agent", "--enterprise"]
check(agent._enterprise() == "",
      "a flag with no number after it was taken for one")
sys.argv = _argv
os.chmod(f"{BOX}/snmpd.conf", 0o600)
os.environ["PRISMA_SNMP_ENTERPRISE"] = " 4242 "
check(agent._enterprise() == "4242",
      f"the PRISMA_SNMP_ENTERPRISE override read as {agent._enterprise()!r}")
os.environ.pop("PRISMA_SNMP_ENTERPRISE")
print("  [ ok ] the fourth field of the view line, and the override")

print("\n=== vppctl: an answer, or None, and never an error message ===")
check(agent.vppctl("show version") == "vpp v26.06-release\n",
      f"a command that answered gave {agent.vppctl('show version')!r}")
check(agent.vppctl("bpa nosuch") is None
      and agent.vppctl("show bpa nosuch") is None,
      "vppctl exits 0 for a command it did not understand and says so on "
      "stdout; that text was handed back as an answer, and the parsers "
      "read an error message into no values, which looks like a dataplane "
      "that is down")
check(agent.vppctl("exit 3") is None,
      "a non-zero exit was handed back as an answer")
w(f"{BOX}/down", "")
check(agent.vppctl("show version") is None,
      "a vppctl that fails was handed back as an answer")
os.remove(f"{BOX}/down")
_bin = agent.VPPCTL
agent.VPPCTL = f"{BOX}/no-such-vppctl"
check(agent.vppctl("show version") is None,
      "a vppctl that is not on the box raised or answered")
agent.VPPCTL = _bin
print("  [ ok ] an answer, an unknown input, a failure and no binary")

print("\n=== the values: parsed, cached, and never stale ===")
v = agent.Values()
check(v.at == 0.0 and v.data == {},
      f"a fresh Values holds {v.data!r} read at {v.at!r}, and it has read "
      f"nothing")
d = v.refresh()
check(d.get("udp") == 300 and d.get("tcp-established") == 7440
      and d.get("port-holddown") == 120 and "tcp-syn" in d,
      f"the timeouts were not parsed off `bpa timeouts`: {d}")
check(d.get("subscribers") == 3 and d.get("portsUsed") == 1910
      and d.get("portsTotal") == 3072 and d.get("subscribersAtLimit") == 1
      and d.get("portRefusals") == 5,
      f"three subscribers, 1910 of 3072 ports, one at 97% and five refusals "
      f"were read as {d}")
check(d.get("packetsDropped") == 42,
      f"40 and 2 packets did not get through, and 9999 were counted under "
      f"everything else; the agent says {d.get('packetsDropped')}")
check(v.at > 0 and v.data is d,
      "a refresh did not record when it read, so the cache never holds")
# THE CACHE: within CACHE_SECONDS the dataplane is not asked again for the
# numbers, so the file changes and the answer does not.
w(f"{BOX}/top", "100.64.0.9       p1     1/1024       0%   1       0\n")
check(v.refresh().get("subscribers") == 3,
      f"the numbers were re-read inside the cache window: "
      f"{v.refresh().get('subscribers')} subscriber(s) where the cached "
      f"answer is 3")
# THE LIVENESS CHECK IS NOT CACHED. The dataplane goes away inside the
# window, and the answer is nothing, not the last values seen.
w(f"{BOX}/down", "")
check(v.refresh() == {} and v.at == 0.0,
      f"the dataplane stopped answering inside the cache window and the "
      f"agent went on serving {v.refresh()}: that is the outage served as "
      f"a healthy box, and it was measured in a lab")
os.remove(f"{BOX}/down")
check(v.refresh().get("subscribers") == 1,
      f"back up, the numbers were not read fresh: "
      f"{v.refresh().get('subscribers')} subscriber(s) where the box now "
      f"has 1")
# And the cache expires: with no window at all, every refresh reads.
_cache = agent.CACHE_SECONDS
agent.CACHE_SECONDS = 0
w(f"{BOX}/top", "")
check(v.refresh().get("subscribers") == 0,
      "past the cache window the numbers were still the old ones")
agent.CACHE_SECONDS = _cache
w(f"{BOX}/top", "100.64.0.9       p1     1/1024       0%   1       0\n")
print("  [ ok ] parsed once, cached inside the window, dropped on an outage")

print("\n=== the OID table: standard objects, and the enterprise half ===")
agent.VALUES.data = {}
agent.VALUES.at = 0.0
agent.ENTERPRISE = ""
rows = agent.oid_table()
_oids = [r[0] for r in rows]
check(("1.3.6.1.2.1.123.1.1.2.0", "gauge", 300) in rows
      and ("1.3.6.1.2.1.123.1.1.5.0", "gauge", 7440) in rows,
      f"natUdpDefIdleTimeout and natTcpDefIdleTimeout are not in the table "
      f"as gauges: {rows}")
check(not any(o.startswith("1.3.6.1.4.1.") for o in _oids),
      f"with no enterprise number set an OID was served under somebody's "
      f"PEN: {[o for o in _oids if o.startswith('1.3.6.1.4.1.')]}")
agent.ENTERPRISE = "99999"
rows = agent.oid_table()
_oids = [r[0] for r in rows]
check(("1.3.6.1.4.1.99999.1.1.0", "counter", 1) in rows
      and ("1.3.6.1.4.1.99999.1.6.0", "counter", 42) in rows,
      f"subscribers and packetsDropped are not under the PEN as counters: "
      f"{rows}")
check(_oids == sorted(_oids, key=agent.as_key),
      f"the table is not in OID order, which GETNEXT walks: {_oids}")
w(f"{BOX}/down", "")
agent.VALUES.at = 0.0
check(agent.oid_table() == [],
      f"a dataplane that is not answering was served as "
      f"{agent.oid_table()}, and the gap is the point")
os.remove(f"{BOX}/down")
print("  [ ok ] the six timeouts, the six counters, sorted, or nothing")

print("\n=== the wire: reply, none, and the key an OID sorts by ===")
_out = io.StringIO()
with contextlib.redirect_stdout(_out):
    agent.reply("1.3.6.1.2.1.123.1.1.2.0", "gauge", 300)
check(_out.getvalue() == "1.3.6.1.2.1.123.1.1.2.0\ngauge\n300\n",
      f"a reply is OID, type and value on three lines; got "
      f"{_out.getvalue()!r}")
_out = io.StringIO()
with contextlib.redirect_stdout(_out):
    agent.none()
check(_out.getvalue() == "NONE\n",
      f"the no-such-object answer is NONE on a line; got "
      f"{_out.getvalue()!r}")
check(agent.as_key(".1.3.6.1") == [1, 3, 6, 1]
      and agent.as_key("1.3.6.1\n") == [1, 3, 6, 1],
      f"an OID with a leading dot, or a trailing newline off stdin, did not "
      f"key as its integers: {agent.as_key('.1.3.6.1')}")
check(agent.as_key("1.3.x") is None and agent.as_key("") is None,
      "an OID that is not integers keyed as something, and main() answers "
      "NONE on None")
check(agent.as_key("1.3.10") > agent.as_key("1.3.9"),
      "the key compares as text, so GETNEXT walks .10 before .9")
print("  [ ok ] three lines, NONE, and integer order")

print("\n=== the interface table: the dataplane's cards in IF-MIB (#498) ===")
# vpp_get_stats' dump as the #475 bench printed it: one row per interface PER
# THREAD, combined counters with bytes, simple ones without.
DUMP = """[0]: local0 /if/names
[1]: ens19 /if/names
[2]: ens19.100 /if/names
[3]: tap4096 /if/names
[0 @ 0]: 0 packets, 0 bytes /if/rx
[1 @ 0]: 5 packets, 500 bytes /if/rx
[1 @ 1]: 1332093 packets, 2016740412 bytes /if/rx
[1 @ 2]: 2 packets, 200 bytes /if/rx
[2 @ 1]: 7 packets, 700 bytes /if/rx
[1 @ 1]: 1332000 packets, 2016000000 bytes /if/rx-unicast
[1 @ 0]: 2 packets /if/drops
[1 @ 1]: 1 packets /if/drops
[1 @ 3]: 4 packets /if/rx-miss
[1 @ 1]: 9 packets, 900 bytes /if/tx
"""
names, counters = agent.parse_stats_dump(DUMP)
check(names == {0: "local0", 1: "ens19", 2: "ens19.100", 3: "tap4096"},
      f"/if/names read as {names}")
check(counters["rx"][1] == [1332100, 2016741112],
      f"ens19's rx summed over three threads: {counters['rx'][1]}")
check(counters["drops"][1] == [3, 0], f"a simple counter summed: {counters['drops'][1]}")

SHOW_IF = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
ens19                             1      up          1500/0/0/0     rx packets              10082056
                                                                    rx bytes             15263850705
ens19.100                         2     down         1500/0/0/0
local0                            0     down          0/0/0/0
"""
attrs = agent.parse_show_interface(SHOW_IF)
check(attrs.get("ens19") == {"admin": "up", "mtu": 1500}
      and attrs.get("ens19.100") == {"admin": "down", "mtu": 1500},
      f"show interface read as {attrs}")
SHOW_HW = """              Name                Idx   Link  Hardware
ens19                              1     up   ens19
  Link speed: 10 Gbps
  Ethernet address bc:24:11:92:67:ab
    carrier up full duplex max-frame-size 1522
ens20                              2    down  ens20
  Link speed: 1.000000 Gbps
  Ethernet address bc:24:11:99:56:c2
tap4096                            3     up   tap4096
  Link speed: unknown
"""
hw = agent.parse_show_hardware(SHOW_HW)
check(hw["ens19"] == {"link": "up", "speed": 10 ** 10, "mac": "bc:24:11:92:67:ab"},
      f"ens19's hardware read as {hw['ens19']}")
check(hw["ens20"]["speed"] == 10 ** 9 and hw["ens20"]["link"] == "down",
      f"the e1000e's '1.000000 Gbps' and a link down: {hw['ens20']}")
check(agent.speed_bps(" unknown") == 0 and agent.speed_bps("100 Mbps") == 10 ** 8,
      "unknown is 0 and Mbps is scaled")

cards = agent.vpp_cards(names, counters, attrs, hw, {"ens19": "uplink"})
check([r["name"] for r in cards] == ["ens19", "ens19.100"],
      f"local0 and the linux_cp tap are left out: {[r['name'] for r in cards]}")
e = cards[0]
check(e["in_octets"] == 2016741112 and e["in_ucast"] == 1332000
      and e["in_discards"] == 7 and e["out_octets"] == 900
      and e["oper"] == "up" and e["alias"] == "uplink",
      f"ens19's record: {e}")
# A DRIVER THAT COUNTS NO TYPE (#501): ens19's tx and ens19.100's rx have no
# per-type counter, as virtio and ixgbe left them on the X520 box, and their
# unicast is the total rather than a flat 0; ens19's rx, where the driver
# filled rx-unicast, keeps its own number.
check(e["out_ucast"] == 9 and cards[1]["in_ucast"] == 7,
      f"a direction with no per-type counter is unicast by its total: "
      f"out {e['out_ucast']}, sub-interface in {cards[1]['in_ucast']}")
check(cards[1]["oper"] == "down" and cards[1]["speed"] == 10 ** 10,
      f"a sub-interface admin down is oper down, on its card's speed: {cards[1]}")

# The Linux side: a physical card the dataplane has not got, and a tap that
# is only the inside of a pair.
net = tempfile.mkdtemp()
for n, dev in (("ens18", True), ("ens19", False)):
    d = os.path.join(net, n)
    os.makedirs(os.path.join(d, "statistics"))
    if dev:
        os.makedirs(os.path.join(d, "device"))
    for k, v in (("operstate", "up"), ("mtu", "1500"), ("speed", "1000"),
                 ("address", "52:54:00:00:00:01"), ("flags", "0x1003"),
                 ("ifalias", "management")):
        w(os.path.join(d, k), v + "\n")
    for k, v in (("rx_bytes", 1000), ("tx_bytes", 2000), ("rx_packets", 10),
                 ("tx_packets", 20), ("multicast", 3), ("rx_dropped", 1),
                 ("tx_dropped", 0), ("rx_errors", 0), ("tx_errors", 2)):
        w(os.path.join(d, "statistics", k), f"{v}\n")
lc = agent.linux_cards(net, skip=set())
check([r["name"] for r in lc] == ["ens18"],
      f"only the physical card Linux holds: {[r['name'] for r in lc]}")
check(lc[0]["in_octets"] == 1000 and lc[0]["in_ucast"] == 7
      and lc[0]["speed"] == 10 ** 9 and lc[0]["admin"] == "up"
      and lc[0]["alias"] == "management",
      f"the Linux card's record: {lc[0]}")
check(agent.linux_cards(net, skip={"ens18"}) == [],
      "a card the dataplane has is not served twice")

# The ifIndex a name keeps, across a reboot that orders the names otherwise
# and a card that appears between them.
idx_file = os.path.join(BOX, "ifindex")
first = agent.if_indexes(["ens19", "ens20"], idx_file)
again = agent.if_indexes(["ens20", "ens18", "ens19"], idx_file)
check(first == {"ens19": 1, "ens20": 2}
      and again == {"ens20": 2, "ens18": 3, "ens19": 1},
      f"indexes kept across a reorder and a new card: {first} then {again}")

check(agent.if_type("ens19") == 6 and agent.if_type("ens19.100") == 135
      and agent.if_type("BondEthernet0") == 161, "ifType by name")
check(agent.mac_octets("bc:24:11:92:67:ab") == "BC 24 11 92 67 AB"
      and agent.mac_octets("") == "", "a MAC as pass_persist's octets")

rows = dict((o, (t, v)) for o, t, v in agent.if_rows(cards, {"ens19": 1, "ens19.100": 2}))
check(rows["1.3.6.1.2.1.2.1.0"] == ("integer", 2), "ifNumber counts the rows")
check(rows["1.3.6.1.2.1.2.2.1.2.1"] == ("string", "ens19")
      and rows["1.3.6.1.2.1.2.2.1.8.1"] == ("integer", 1)
      and rows["1.3.6.1.2.1.2.2.1.8.2"] == ("integer", 2),
      "ifDescr and the oper status")
check(rows["1.3.6.1.2.1.2.2.1.10.1"] == ("counter", 2016741112 % 2 ** 32)
      and rows["1.3.6.1.2.1.31.1.1.1.6.1"] == ("counter64", 2016741112),
      "the 32-bit counter wraps and the 64-bit one does not")
check(rows["1.3.6.1.2.1.2.2.1.5.1"] == ("gauge", 2 ** 32 - 1)
      and rows["1.3.6.1.2.1.31.1.1.1.15.1"] == ("gauge", 10000),
      "ifSpeed saturates at 10G and ifHighSpeed says 10000 Mbit/s")
check(rows["1.3.6.1.2.1.31.1.1.1.18.1"] == ("string", "uplink"),
      "ifAlias is the description")
check(agent._read(os.path.join(net, "ens18", "mtu")) == "1500"
      and agent._read(os.path.join(net, "nowhere")) == "",
      "a sysfs file read and stripped; a missing one is empty, not a crash")
_it = agent.Interfaces()
check(_it.rows == [] and _it.at == 0.0,
      "an interface cache starts empty, so the first poll reads the dataplane")
print("  every card, summed over threads, with a stable index and 64-bit counters")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
