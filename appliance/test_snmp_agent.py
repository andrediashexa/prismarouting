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
import re
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
  "show clock") echo "Time now 539.710725, Thu, 01 Oct 2026 06:43:15 GMT" ;;
  "bpa timeouts") cat "$D/timeouts" ;;
  "show plugins") cat "$D/plugins" ;;
  "show memory main-heap") cat "$D/heap" ;;
  "show vrrp vr") cat "$D/vrrp" ;;
  "show bond") cat "$D/bond" ;;
  "show bpa identity") cat "$D/identity" ;;
  "show bpa drops") cat "$D/drops" ;;
  "show bpa pool history verify") cat "$D/history" ;;
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
# THE STATISTICS SEGMENT (#504), as vpp_get_stats prints it: pool 0 spread
# over two threads, which the agent sums, and pool 1 deleted - an empty name,
# which is a row nobody may be shown. The same three subscribers the screen
# used to print: 900, 1000 (97%) and 10 of 1024 ports, five refusals.
STATS = """[0]: p1 /bpa/pool/names
[1]:  /bpa/pool/names
[1]: loop0 /if/names
[2]: pg0 /if/names
[0 @ 0]: 1 packets /bpa/pool/subscribers
[0 @ 1]: 2 packets /bpa/pool/subscribers
[1 @ 0]: 0 packets /bpa/pool/subscribers
[0 @ 0]: 1 packets /bpa/pool/blocks
[0 @ 1]: 2 packets /bpa/pool/blocks
[0 @ 0]: 900 packets /bpa/pool/sessions
[0 @ 1]: 1010 packets /bpa/pool/sessions
[0 @ 0]: 900 packets /bpa/pool/ports
[0 @ 1]: 1010 packets /bpa/pool/ports
[0 @ 1]: 1 packets /bpa/pool/near-full
[0 @ 0]: 5 packets /bpa/pool/refused
[0 @ 0]: 2 packets /bpa/pool/mode
[0 @ 0]: 1024 packets /bpa/pool/block-size
[0 @ 0]: 1024 packets /bpa/pool/inside
[0 @ 0]: 4 packets /bpa/pool/outside
[0 @ 0]: 12 packets /bpa/pool/blocks-total
[0 @ 0]: 12 packets /bpa/pool/capacity
[0 @ 0]: 6 packets /bpa/pool/guaranteed
1.00 /bpa/log/ipfix/enabled
12345.00 /bpa/log/ipfix/records
0.00 /bpa/log/ipfix/dropped
1.00 /bpa/log/tcp/enabled
0.00 /bpa/log/tcp/connected
7.00 /bpa/log/tcp/dropped
3.00 /bpa/log/tcp/connects
0.00 /bpa/log/syslog/enabled
"""
w(f"{BOX}/stats", STATS)
w(f"{BOX}/vpp_get_stats", f"""#!/bin/sh
[ -e "{BOX}/down" ] && exit 1
cat "{BOX}/stats"
""")
os.chmod(f"{BOX}/vpp_get_stats", 0o755)
os.environ["PRISMA_VPP_GET_STATS"] = f"{BOX}/vpp_get_stats"
w(f"{BOX}/plugins", " Plugin path is: /usr/lib/x86_64-linux-gnu/vpp_plugins\n"
                    "     Plugin                  Version            Description\n"
                    "  1. bpa_plugin.so           0.142.1 (4ccb50d)  Bulk port allocation CGNAT\n")
w(f"{BOX}/VERSION", "0.142.1\n")
# #505's screens, as a lab printed them (vppctl's CR+LF kept, as it arrives).
w(f"{BOX}/heap", "  base 0x7f19, size 512m, locked, unmap-on-destroy, name 'main heap'\n"
                 "    total: 512.00M, used: 237.20M, free: 274.80M, trimmable: 273.33M\n")
VRRP = ("[0] sw_if_index 1 VR ID 10 IPv4\r\n"
        "   state Master flags: preempt yes accept no unicast no\r\n"
        "   priority: configured 120 adjusted 110\r\n"
        "   timers: adv interval 100 master adv 100 skew 53 master down 353\r\n"
        "   virtual MAC 00:00:5e:00:01:0a\r\n"
        "   addresses 10.9.0.1 \r\n"
        "   peer addresses \r\n"
        "   tracked interfaces \r\n"
        "[1] sw_if_index 2 VR ID 20 IPv4\r\n"
        "   state Initialize flags: preempt yes accept no unicast no\r\n"
        "   priority: configured 90 adjusted 90\r\n"
        "   addresses 10.8.0.1 10.8.0.3 \r\n"
        "   peer addresses \r\n")
w(f"{BOX}/vrrp", VRRP)
w(f"{BOX}/bond", "  interface name   sw_if_index  mode          load balance  active members members\n"
                 "  BondEthernet0    3            lacp          l34           1              2\n")
w(f"{BOX}/identity", "identity table: 3 entry(s), read 2026/09/28 20:51:47:267\n"
                     "radius accounting: listening on port 1813\n"
                     "  packets    8 received, 6 applied, 6 answered\n"
                     "  refused    2 bad authenticator, 0 malformed\n")
os.environ["PRISMA_VERSION_FILE"] = f"{BOX}/VERSION"
ONE_SUB = STATS.replace("[0 @ 1]: 2 packets /bpa/pool/subscribers",
                        "[0 @ 1]: 0 packets /bpa/pool/subscribers")
w(f"{BOX}/drops", "bpa drops\n"
                  "  packets that did not get through\n"
                  "  40 no free port\n"
                  "  2 subscriber not in any pool\n"
                  "  everything else it counted\n"
                  "  9999 in2out packets\n")
# THE BOX'S OWN STATE (#524): a tuner between two plans, the kernel it
# asked, the self-test's and the VTY's state files, and the trail's check.
STARTUP = ("# BETWEEN TWO PLANS. This boot runs 4 worker(s) on 2-6, isolated now;\n"
           "# the next boot isolates 2-7 and runs 5.\n"
           "# Generated by prisma-routing-tune. Do not edit by hand.\n"
           "cpu {\n  main-core 2\n  corelist-workers 3-6\n}\n")
w(f"{BOX}/startup.conf", STARTUP)
w(f"{BOX}/grub.cfg", 'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT '
                     'isolcpus=managed_irq,domain,2-7 nohz_full=2-7 '
                     'default_hugepagesz=1G hugepagesz=1G hugepages=6"\n')
w(f"{BOX}/sys/devices/system/cpu/isolated", "2-6\n")
w(f"{BOX}/sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages", "5\n")
w(f"{BOX}/state/selftest", "fail 0\nwarn 2\nforwarding yes\nat 1790740800\n")
w(f"{BOX}/state/config", "commits 3\ncommit_at 1790744400\ncommit_by amy\n"
                         "confirm_pending yes\nconfirm_deadline 1790745000\n"
                         "confirm_reverts 1\nrollbacks 1\nsaved_differs yes\n")
HISTORY = ("configuration trail: /var/log/prisma-routing/config-history.log\n"
           "  3 entries checked, chain intact\n"
           "  head  seq 3 sha256 " + "ab" * 32 + "\n"
           "  a chain that verifies proves the file was not edited, not that "
           "it is the same file: compare this digest with the copy in your "
           "remote syslog\n")
w(f"{BOX}/history", HISTORY)
os.environ["PRISMA_STARTUP_CONF"] = f"{BOX}/startup.conf"
os.environ["PRISMA_GRUB_CFG"] = f"{BOX}/grub.cfg"
os.environ["PRISMA_SYSFS_ROOT"] = f"{BOX}/sys"
os.environ["PRISMA_STATE_DIR"] = f"{BOX}/state"
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
      f"three subscribers over two threads, 1910 of 3072 ports, one at 97% "
      f"and five refusals were read as {d}")
check(list(d["cgnat"]["pools"]) == [0],
      f"a deleted pool - an empty name - was kept: {d['cgnat']['pools']}")
check(d.get("release") == "0.142.1" and d.get("build") == "0.142.1 (4ccb50d)",
      f"the release and the plugin's build read as {d.get('release')!r} "
      f"and {d.get('build')!r}")
check(d.get("packetsDropped") == 42,
      f"40 and 2 packets did not get through, and 9999 were counted under "
      f"everything else; the agent says {d.get('packetsDropped')}")
check(v.at > 0 and v.data is d,
      "a refresh did not record when it read, so the cache never holds")
# THE CACHE: within CACHE_SECONDS the dataplane is not asked again for the
# numbers, so the file changes and the answer does not.
w(f"{BOX}/stats", ONE_SUB)
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
w(f"{BOX}/stats", "")
check(v.refresh().get("subscribers") == 0,
      "past the cache window the numbers were still the old ones")
agent.CACHE_SECONDS = _cache
w(f"{BOX}/stats", ONE_SUB)
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
check(("1.3.6.1.4.1.99999.1.1.0", "gauge", 1) in rows
      and ("1.3.6.1.4.1.99999.1.6.0", "counter", 42) in rows,
      f"subscribers is not under the PEN as a gauge and packetsDropped as a "
      f"counter: {rows}")
print("  [ ok ] the six totals, typed as PRISMA-ROUTING-MIB declares them")
check(_oids == sorted(_oids, key=agent.as_key),
      f"the table is not in OID order, which GETNEXT walks: {_oids}")
w(f"{BOX}/down", "")
agent.VALUES.at = 0.0
# Nothing OF THE DATAPLANE, and the box's own state still (#524): the
# tuning, the self-test and the configuration are what somebody reads about
# a box whose dataplane did not come up. The trail is the dataplane's answer
# and goes with it.
_down = agent.oid_table()
check(all(o.split(".")[8] in ("16", "17", "18") for o, _t, _v in _down)
      and _down,
      f"a dataplane that is not answering was served as {_down}, and the "
      f"gap is the point: only the box's own state may remain")
os.remove(f"{BOX}/down")
print("  [ ok ] the six timeouts, the six counters, sorted, or nothing")

print("\n=== PRISMA-ROUTING-MIB: the rows, and the file they are defined in ===")
_st = agent.cgnat_state(STATS, agent.parse_drops(open(f"{BOX}/drops").read()))
_rows = {o: (t, v) for o, t, v in agent.product_rows("1.3.6.1.4.1.99999.1",
                                                    _st, "0.142.1",
                                                    "0.142.1 (4ccb50d)", 539.71)}
check(_rows.get("1.3.6.1.4.1.99999.1.12.4.0") == ("timeticks", 53971),
      "prSystemDataplaneUptime: VPP's clock in hundredths (#534)")
check(agent.vpp_clock("Time now 539.710725, Thu, 01 Oct 2026 06:43:15 GMT\n") == 539.710725
      and agent.vpp_clock("") is None,
      "the dataplane's uptime from show clock, as the VTY reads it")
check("1.3.6.1.4.1.99999.1.12.4.0" not in dict(
          (o, v) for o, t, v in agent.product_rows("1.3.6.1.4.1.99999.1", _st,
                                                   "0.142.1", "0.142.1 (4ccb50d)")),
      "and absent when the dataplane did not answer")
P = "1.3.6.1.4.1.99999.1"
check(_rows.get(f"{P}.10.1.1.2.1") == ("string", "p1")
      and _rows.get(f"{P}.10.1.1.3.1") == ("integer", 2)
      and _rows.get(f"{P}.10.1.1.9.1") == ("gauge", 6),
      f"pool 0 is row 1, named p1, dynamic, guaranteeing 6: "
      f"{[(o, r) for o, r in _rows.items() if '.10.1.1.' in o][:9]}")
check(_rows.get(f"{P}.10.1.1.12.1") == ("gauge", 25),
      f"3 blocks held of 12 is 25% utilisation, read as "
      f"{_rows.get(f'{P}.10.1.1.12.1')}")
check(not any(o.endswith(".2") and ".10.1.1." in o for o in _rows),
      "the deleted pool has a row")
check(_rows.get(f"{P}.10.1.1.16.1") == ("counter64", 5),
      f"the refusals are a Counter64: {_rows.get(f'{P}.10.1.1.16.1')}")
check(_rows.get(f"{P}.10.2.1.3.1") == ("string", "no free port")
      and _rows.get(f"{P}.10.2.1.4.2") == ("counter64", 2),
      f"the drop table's rows: "
      f"{[(o, r) for o, r in _rows.items() if '.10.2.1.' in o]}")
check(_rows.get(f"{P}.10.3.0") == ("gauge", 1910),
      f"1910 sessions in all: {_rows.get(f'{P}.10.3.0')}")
check(_rows.get(f"{P}.11.1.0") == ("integer", 1)
      and _rows.get(f"{P}.11.2.0") == ("counter64", 12345),
      "IPFIX enabled (true is 1) with 12345 records")
check(_rows.get(f"{P}.11.10.1.3.1") == ("integer", 1)
      and _rows.get(f"{P}.11.10.1.4.1") == ("integer", 2)
      and _rows.get(f"{P}.11.10.1.8.1") == ("counter64", 7),
      "the TCP stream enabled, NOT connected (false is 2), 7 dropped: the "
      "state an operator alarms on")
check(_rows.get(f"{P}.11.10.1.3.2") == ("integer", 2)
      and _rows.get(f"{P}.11.10.1.2.2") == ("string", "syslog"),
      "syslog, off")
check(_rows.get(f"{P}.12.3.0") == ("integer", 1),
      "the dataplane's build 0.142.1 matches release 0.142.1")
_old = {o: (t, v) for o, t, v in agent.product_rows(P, _st, "0.142.1",
                                                   "0.141.1 (5ccdce1)")}
check(_old.get(f"{P}.12.3.0") == ("integer", 2),
      "an upgraded box still forwarding with 0.141.1 read as current")
check(agent.plugin_build("no plugin here") == "",
      "a dataplane with no bpa_plugin.so row gave a build")
check(agent.parse_stats_gauges("1.00 /bpa/log/tcp/enabled\nnoise\n",
                               "/bpa/log/") == {"tcp/enabled": 1},
      "a gauge is '<value> <path>'")
_ah = agent.cgnat_totals({"pools": {}, "drops": [], "log": {}})
check(_ah["subscribers"] == 0 and _ah["packetsDropped"] == 0,
      f"a box with no pool has totals of zero: {_ah}")
check(agent.stats_dump("^/bpa/") == open(f"{BOX}/stats").read(),
      "stats_dump did not hand back what vpp_get_stats printed")

# THE FILE AND THE AGENT AGREE. Every OID served under the enterprise has an
# object in PRISMA-ROUTING-MIB with the type served, and every object the MIB
# declares readable is served: the MIB an NMS loads is the MIB it polls.
MIB = open(os.path.join(os.path.dirname(AGENT), "PRISMA-ROUTING-MIB.in")).read()
import re as _re
_parent = {"prismaRouting": ""}
_defs = {}
for _m in _re.finditer(r"^(\w+)\s+(?:OBJECT-TYPE|OBJECT IDENTIFIER|OBJECT-GROUP"
                       r"|MODULE-COMPLIANCE)\b(.*?)::=\s*\{\s*(\w+)\s+(\d+)\s*\}",
                       MIB, _re.S | _re.M):
    _defs[_m.group(1)] = (_m.group(3), int(_m.group(4)), _m.group(2))
def _oid(name):
    if name == "prismaRouting":
        return []
    par, n, _b = _defs[name]
    return _oid(par) + [n]
_want = {}
for _name, (_par, _n, _body) in _defs.items():
    if "MAX-ACCESS  read-only" not in _body:
        continue
    _syn = _re.search(r"SYNTAX\s+(\w+)", _body).group(1)
    _want[".".join(map(str, _oid(_name)))] = (_name, _syn)
_SYN = {"Gauge32": "gauge", "Counter32": "counter", "Counter64": "counter64",
        "DisplayString": "string", "INTEGER": "integer",
        "TruthValue": "integer", "Unsigned32": "gauge",
        "TimeTicks": "timeticks"}
agent.VALUES.data = {}
agent.VALUES.at = 0.0
w(f"{BOX}/stats", STATS)
_served = {}
for _o, _t, _v in agent.oid_table():
    if _o.startswith("1.3.6.1.4.1.99999."):
        _served[_o[len("1.3.6.1.4.1.99999."):]] = _t
_objs = {}
for _o, _t in _served.items():
    _k = ".".join(_o.split(".")[:-1])
    check(_k in _want, f"{_o} is served and PRISMA-ROUTING-MIB has no object "
                       f"at {_k}")
    if _k in _want:
        check(_SYN[_want[_k][1]] == _t,
              f"{_want[_k][0]} is {_want[_k][1]} in the MIB and served as {_t}")
        _objs[_k] = _t
for _k, (_name, _syn) in _want.items():
    check(_k in _objs, f"{_name} ({_k}) is in the MIB and not served")
check("@PEN@" in MIB and MIB.count("@PEN@") == 1,
      "the MIB's root is not the one placeholder the box fills in")
print(f"  [ ok ] {len(_want)} objects in the MIB, each served with its type")

print("\n=== the second part: workers, capacity, HA, LACP, identity (#505) ===")
sys.path.insert(0, os.path.join(os.path.dirname(AGENT), "..", "src", "prismash"))
import prismash as _pm
check(agent.SESSION_BYTES == _pm.SESSION_BYTES,
      f"the agent sizes a session at {agent.SESSION_BYTES} bytes and "
      f"show cgnat memory at {_pm.SESSION_BYTES}: the two would disagree "
      f"about what fits")
check(agent.heap_free(open(f"{BOX}/heap").read()) == int(274.80 * (1 << 20))
      and agent.heap_free("nothing") is None,
      "the free heap is read off 'free: 274.80M'")
_th = agent.parse_stats_threads(STATS, "/bpa/pool/")
check(_th["subscribers"][0] == {0: 1, 1: 2},
      f"per thread, pool 0's subscribers are 1 and 2: {_th.get('subscribers')}")
check(agent.worker_rows(_th)[:2] == [(0, 1, 900, 900), (1, 2, 1010, 1010)],
      f"the workers: {agent.worker_rows(_th)}")
check(agent.parse_vrrp(VRRP, {1: "loop0", 2: "pg0"})
      == [("loop0", 10, 110, 3, "10.9.0.1"),
          ("pg0", 20, 90, 1, "10.8.0.1 10.8.0.3")],
      f"the two virtual routers, by name, the ADVERTISED priority (110, "
      f"tracking lowered it) and master/init: "
      f"{agent.parse_vrrp(VRRP, {1: 'loop0', 2: 'pg0'})}")
check(agent.parse_vrrp(VRRP)[0][0] == "sw_if_index 1",
      "an interface with no name is named by its index, not dropped")
check(agent.parse_bond(open(f"{BOX}/bond").read())
      == [("BondEthernet0", "lacp", 2, 1)],
      "a bundle on one of its two legs")
_id = agent.parse_identity(open(f"{BOX}/identity").read())
check(_id == {"entries": 3, "read": "2026/09/28 20:51:47:267",
              "received": 8, "applied": 6, "refused": 2},
      f"the identity table and RADIUS: {_id}")
check(agent.parse_identity("no identity table: every record says 'unknown'")
      ["entries"] == 0, "no table is zero entries")
_mr = {o: (t, v) for o, t, v in agent.more_rows(P, {
    "fit": 960495, "workers": [(0, 1, 900, 900), (2, 5, 7, 8)],
    "vrrp": [("loop0", 10, 110, 3, "10.9.0.1")],
    "bonds": [("BondEthernet0", "lacp", 2, 1)], "identity": _id})}
check(_mr.get(f"{P}.10.4.0") == ("gauge", 960495)
      and _mr.get(f"{P}.10.5.1.3.3") == ("gauge", 7),
      "the capacity, and thread 2's sessions at row 3")
check(_mr.get(f"{P}.13.1.1.5.1") == ("integer", 3)
      and _mr.get(f"{P}.14.1.1.5.1") == ("gauge", 1)
      and _mr.get(f"{P}.15.5.0") == ("counter64", 2),
      "a master, one active member, two RADIUS refusals")
check(agent.more_rows(P, {}) == [],
      "nothing read is nothing served, not a row of zeros")
print("  [ ok ] the parsers read the screens a lab printed")

print("\n=== the box's own state: tuning, self-test, commits, trail (#524) ===")
check(agent._read_or_none(f"{BOX}/startup.conf") == STARTUP
      and agent._read_or_none(f"{BOX}/nothing") is None,
      "a file's text, or None: absent is not the same as empty")
check(agent.cpu_count("2-6") == 5 and agent.cpu_count("2-6,9") == 6
      and agent.cpu_count("") == 0, "a CPU list counted")
check(agent.parse_startup_state(STARTUP) == ("between", 4, 5),
      f"between two plans: 4 workers now, 5 at the next boot: "
      f"{agent.parse_startup_state(STARTUP)}")
check(agent.parse_startup_state("# FIRST BOOT MODE. The kernel\ncpu {\n}\n")
      == ("firstboot", 0, 0)
      and agent.parse_startup_state("cpu {\n  corelist-workers 3-7\n}\n")
      == ("tuned", 5, 5)
      and agent.parse_startup_state(None) is None,
      "a first boot, a tuned box, and a file nobody can read")
check(agent.parse_grub_request(open(f"{BOX}/grub.cfg").read())
      == ("2-7", 1048576, 6) and agent.parse_grub_request("") is None,
      "the tuner's request: isolcpus past its flags, 1G pages, six of them")
check(agent.hugepages_reserved(1048576, f"{BOX}/sys") == 5
      and agent.hugepages_reserved(2048, f"{BOX}/sys") is None
      and agent.hugepages_reserved(0, f"{BOX}/sys") is None,
      "the kernel's pages of that size, or None for none asked or unknown")
check(agent.parse_state_file("fail 1\n# note\nat 5\n") == {"fail": "1", "at": "5"}
      and agent.parse_state_file(None) is None, "a state file, or None")
_au = agent.parse_audit_verify(HISTORY)
check(_au == {"state": "intact", "entries": 3, "seq": 3, "digest": "ab" * 32,
              "lost": 0}, f"an intact trail and its head: {_au}")
check(agent.parse_audit_verify(
          "configuration trail: /x\n  BROKEN at entry 2\n")["state"] == "broken"
      and agent.parse_audit_verify(
          "configuration trail: /x\n  3 entries checked, chain intact\n"
          "  TRUNCATED: the file ends at entry 3 and this dataplane wrote up "
          "to 5\n")["state"] == "truncated"
      and agent.parse_audit_verify(
          "configuration trail: /x\n  INCOMPLETE: 2 entries could not be "
          "written\n  4 entries checked, chain intact\n")["lost"] == 2
      and agent.parse_audit_verify(
          "configuration trail: /x\n  no chained entries: nothing here is "
          "covered\n")["state"] == "empty"
      and agent.parse_audit_verify(None) is None,
      "broken, truncated, incomplete, empty, and a dataplane that said nothing")
check(agent._utc(1790740800) == "2026-09-30T04:00:00Z" and agent._utc("") == ""
      and agent._utc(0) == "", "an epoch as UTC, none as empty")
check(agent._int({"a": "3", "b": "x"}, "a") == 3
      and agent._int({"b": "x"}, "b") == 0, "a number, or 0")
_bs = agent.box_state()
check(_bs["tuning"]["state"] == "between" and _bs["tuning"]["hugepages"] == 5
      and _bs["tuning"]["isolated"] == "2-6"
      and _bs["tuning"]["isolated_wanted"] == "2-7"
      and _bs["selftest"]["warn"] == "2" and _bs["config"]["commit_by"] == "amy",
      f"the box's state read from where the box writes it: {_bs}")
check(agent.box_state(startup=f"{BOX}/nothing", state_dir=f"{BOX}/nothing")
      == {"tuning": None, "selftest": None, "config": None},
      "sources that cannot be read are None, not zeros")
_br = {o: (t, v) for o, t, v in agent.box_rows(P, _bs, _au)}
check(_br.get(f"{P}.16.1.0") == ("integer", 2)
      and _br.get(f"{P}.16.2.0") == ("gauge", 4)
      and _br.get(f"{P}.16.3.0") == ("gauge", 5)
      and _br.get(f"{P}.16.6.0") == ("gauge", 5)
      and _br.get(f"{P}.16.7.0") == ("gauge", 6),
      "betweenPlans, 4 workers now and 5 next, 5 of 6 pages")
check(_br.get(f"{P}.17.1.0") == ("integer", 2)
      and _br.get(f"{P}.17.4.0") == ("string", "2026-09-30T04:00:00Z"),
      "ready with warnings, and when")
check(_br.get(f"{P}.18.1.0") == ("counter", 3)
      and _br.get(f"{P}.18.3.0") == ("string", "amy")
      and _br.get(f"{P}.18.4.0") == ("integer", 1)
      and _br.get(f"{P}.18.5.0") == ("string", "2026-09-30T05:10:00Z")
      and _br.get(f"{P}.18.6.0") == ("counter", 1)
      and _br.get(f"{P}.18.8.0") == ("integer", 1),
      "three commits, amy's last, a window open to 05:10, one revert, unsaved")
check(_br.get(f"{P}.19.1.0") == ("integer", 1)
      and _br.get(f"{P}.19.4.0") == ("string", "ab" * 32),
      "the trail intact, with its head")
_v = {o: v for o, t, v in agent.box_rows(P, {"selftest": {"fail": "1"}})}
check(_v == {f"{P}.17.1.0": 4, f"{P}.17.2.0": 1, f"{P}.17.3.0": 0,
             f"{P}.17.4.0": ""},
      f"a failure is the verdict whatever else is said, and only that group: {_v}")
check(agent.box_rows(P, {"selftest": {"forwarding": "no"}})[0][2] == 3
      and agent.box_rows(P, {"config": {}})[4][2] == "",
      "a healthy box with nothing to forward on; no window, no deadline")
check(agent.box_rows(P, {}) == [], "nothing read is nothing served")
_bi = next(o for o in [agent.box_rows(P, {"tuning": dict(_bs["tuning"],
                                                         hugepages=None)})])
check(f"{P}.16.6.0" not in {o for o, _t, _v in _bi},
      "hugepages held left out when the kernel cannot say")
_a = agent.Audit()
check(_a.data is None and _a.at == 0.0,
      "the trail's check starts unasked, so the first poll asks it")
_b = agent.Box()
check(_b.data is None and _b.at == 0.0,
      "the box's state starts unread, so the first poll reads it")

print("\n=== NATV2-MIB, RFC 7659: one instance per pool, aggregate only (#512) ===")
CONF = ("cgnat pool nb\n inside 100.64.0.0/22\n outside 200.150.0.16/30\n"
        " outside 200.150.1.0/31\n block-size 512\n ports 2000 60000\n"
        " max-blocks 4\n filter address-dependent\n exit\n"
        "cgnat pool dyn\n outside 10.0.0.0/24\n exit\n"
        "cgnat interface pg0 inside\n")
_c = agent.parse_bpa_config(CONF)
check(_c["nb"] == {"outside": [("200.150.0.16", "200.150.0.19"),
                               ("200.150.1.0", "200.150.1.1")],
                   "ports": (2000, 60000), "max_blocks": 4, "filter": 1},
      f"a pool's outside ranges, ports, extension and filter: {_c['nb']}")
check(_c["dyn"] == {"outside": [("10.0.0.0", "10.0.0.255")],
                    "ports": (1024, 65535), "max_blocks": 1, "filter": 0},
      f"and the defaults for what the dataplane leaves unprinted: {_c['dyn']}")
_st = {"pools": {0: {"name": "nb", "subscribers": 3, "ports": 900,
                     "refused": 2},
                 1: {"name": "dyn", "subscribers": 0, "ports": 0,
                     "refused": 0}}}
_n = {o: (t, v) for o, t, v in agent.natv2_rows(_st, _c)}
N = "1.3.6.1.2.1.234"
check(_n.get(f"{N}.2.1.1.2.1") == ("string", "nb")
      and _n.get(f"{N}.2.1.1.4.1") == ("integer", 1)
      and _n.get(f"{N}.2.1.1.5.1") == ("integer", 0)
      and _n.get(f"{N}.2.1.1.5.2") == ("integer", 1),
      "instance 1 is nb, address-dependent, arbitrary pooling (4 blocks); "
      "instance 2 is paired")
check(_n.get(f"{N}.2.1.1.8.1") == ("gauge", 900)
      and _n.get(f"{N}.2.3.1.5.1.1") == ("gauge", 2000)
      and _n.get(f"{N}.2.3.1.6.1.1") == ("gauge", 60000)
      and _n.get(f"{N}.2.3.1.12.1.1") == ("counter64", 2),
      "port map entries, the pool's ports and its refusals")
check(_n.get(f"{N}.2.4.1.4.1.1.2") == ("octet", "C8 96 01 00")
      and _n.get(f"{N}.2.4.1.5.1.1.1") == ("octet", "C8 96 00 13"),
      "the pool's ranges as begin and end addresses")
check(not any(o.startswith(f"{N}.2.1.1.9.") for o in _n),
      "no translations column: the box counts them box-wide, not per pool")
# Every column served is one NATV2-MIB defines, with the type it declares.
_NV = open(os.path.join(os.path.dirname(AGENT), "..", "test", "mibs",
                        "NATV2-MIB.txt")).read()
_nv = {}
for _m in re.finditer(r"^\s*(natv2\w+)\s+OBJECT-TYPE(.*?)::=\s*\{\s*(\w+)\s+(\d+)\s*\}",
                      _NV, re.S | re.M):
    _nv[(_m.group(3), int(_m.group(4)))] = (_m.group(1),
        re.search(r"SYNTAX\s+(\S+)", _m.group(2)).group(1))
_ENT = {"2.1.1": "natv2InstanceEntry", "2.3.1": "natv2PoolEntry",
        "2.4.1": "natv2PoolRangeEntry"}
_NSYN = {"DisplayString": "string", "SnmpAdminString": "string",
         "INTEGER": "integer", "Integer32": "integer", "InetAddressType": "integer",
         "Unsigned32": "gauge", "InetPortNumber": "gauge", "Counter64": "counter64",
         "TimeStamp": "timeticks", "InetAddress": "octet"}
for _o, (_t, _v) in _n.items():
    _rest = _o[len(N) + 1:].split(".")
    _key = (_ENT[".".join(_rest[:3])], int(_rest[3]))
    check(_key in _nv and _NSYN[_nv[_key][1]] == _t,
          f"{_o} served as {_t}, and NATV2-MIB says {_nv.get(_key)}")
print(f"  [ ok ] {len(_n)} rows, each a NATV2-MIB column with its type")

print("\n=== VRRPV3-MIB, RFC 6527: the dataplane's virtual routers (#513) ===")
VR = ("[0] sw_if_index 1 VR ID 10 IPv4\n"
      "   state Master flags: preempt yes accept no unicast no\n"
      "   priority: configured 120 adjusted 110\n"
      "   timers: adv interval 100 master adv 100 skew 53 master down 353\n"
      "   virtual MAC 00:00:5e:00:01:0a\n"
      "   addresses 10.9.0.1 \n"
      "   peer addresses \n"
      "[1] sw_if_index 2 VR ID 20 IPv6\n"
      "   state Backup flags: preempt no accept yes unicast no\n"
      "   priority: configured 90 adjusted 90\n"
      "   timers: adv interval 200 master adv 0 skew 0 master down 0\n"
      "   virtual MAC 00:00:5e:00:02:14\n"
      "   addresses 2001:db8::1 \n")
_vd = agent.parse_vrrp_detail(VR, {1: "loop0", 2: "pg0"})
check([(r["interface"], r["vr_id"], r["family"], r["status"], r["priority"],
        r["adv"], r["preempt"], r["accept"], r["addresses"]) for r in _vd]
      == [("loop0", 10, 4, 3, 120, 100, True, False, ["10.9.0.1"]),
          ("pg0", 20, 6, 2, 90, 200, False, True, ["2001:db8::1"])],
      f"each router's interface, VRID, family, state and settings: {_vd}")
check(agent.parse_vrrp_detail("", {}) == [], "no routers, no rows")
check(agent._inet_octets("10.9.0.2") == "0A 09 00 02"
      and agent._inet_octets("2001:db8::1")
      == "20 01 0D B8 00 00 00 00 00 00 00 00 00 00 00 01",
      "an InetAddress as pass_persist's octets, four or sixteen")
_own = agent.parse_interface_addresses(
    "loop0 (up):\n  L3 10.9.0.2/24\n  L3 fe80::1/64\n"
    "pg0 (up):\n  L3 2001:db8::2/64\nlocal0 (dn):\n")
check(_own.get("loop0", [])[:1] == ["10.9.0.2"]
      and "2001:db8::2" in _own.get("pg0", []) and not _own.get("local0"),
      f"each card's own addresses, the primary first: {_own}")
_v = {o: (t, v) for o, t, v in agent.vrrpv3_rows(_vd, {"loop0": 3, "pg0": 1}, _own)}
V = "1.3.6.1.2.1.207.1.1"
check(_v.get(f"{V}.1.1.6.3.10.1") == ("integer", 3)
      and _v.get(f"{V}.1.1.6.1.20.2") == ("integer", 2),
      "the status, indexed by ifIndex, VRID and address family")
check(_v.get(f"{V}.1.1.3.3.10.1") == ("octet", "0A 09 00 02")
      and f"{V}.1.1.3.1.20.2" not in _v,
      "the master's address where this box is master, none where it waits")
check(_v.get(f"{V}.1.1.5.3.10.1") == ("octet", "00 00 5E 00 01 0A")
      and _v.get(f"{V}.1.1.7.3.10.1") == ("gauge", 120)
      and _v.get(f"{V}.1.1.8.3.10.1") == ("integer", 1)
      and _v.get(f"{V}.1.1.9.1.20.2") == ("integer", 200)
      and _v.get(f"{V}.1.1.10.3.10.1") == ("integer", 1)
      and _v.get(f"{V}.1.1.11.3.10.1") == ("integer", 2),
      "the MAC, priority, address count, interval and the two modes")
check(_v.get(f"{V}.2.1.2.3.10.1.4.10.9.0.1") == ("integer", 1)
      and _v.get(f"{V}.2.1.2.1.20.2.16.32.1.13.184.0.0.0.0.0.0.0.0.0.0.0.1")
      == ("integer", 1),
      "the virtual addresses, indexed by type, length and octets")
check(agent.vrrpv3_rows([], {}, {}) == [], "no routers, no table")
# Every column served is one VRRPV3-MIB defines, with the type it declares.
_VR = open(os.path.join(os.path.dirname(AGENT), "..", "test", "mibs",
                        "VRRPV3-MIB.txt")).read()
_vr = {}
for _m in re.finditer(r"^\s*(vrrpv3\w+)\s+OBJECT-TYPE(.*?)::=\s*\{\s*(\w+)\s+(\d+)\s*\}",
                      _VR, re.S | re.M):
    _vr[(_m.group(3), int(_m.group(4)))] = (_m.group(1),
        re.search(r"SYNTAX\s+(\S+)", _m.group(2)).group(1))
_VENT = {"1.1": "vrrpv3OperationsEntry", "2.1": "vrrpv3AssociatedIpAddrEntry"}
_VSYN = {"InetAddress": "octet", "MacAddress": "octet", "INTEGER": "integer",
         "Unsigned32": "gauge", "Integer32": "integer", "TimeInterval": "integer",
         "TruthValue": "integer", "RowStatus": "integer"}
for _o, (_t, _x) in _v.items():
    _rest = _o[len(V) + 1:].split(".")
    _key = (_VENT[".".join(_rest[:2])], int(_rest[2]))
    check(_key in _vr and _VSYN[_vr[_key][1]] == _t,
          f"{_o} served as {_t}, and VRRPV3-MIB says {_vr.get(_key)}")
print(f"  [ ok ] {len(_v)} rows, each a VRRPV3-MIB column with its type")

print("\n=== IEEE8023-LAG-MIB: the LACP bundles, 802.1AX's tables (#515) ===")
# The two screens lab/lacp.sh printed, against a Linux bond in 802.3ad mode.
BOND = 'BondEthernet0\n  mode: lacp\n  load balance: l34\n  number of active members: 2\n    host-m1\n    host-m2\n  number of members: 2\n    host-m1\n    host-m2\n  device instance: 0\n  interface id: 0\n  sw_if_index: 3\n  hw_if_index: 3\n'
LACP = 'Number of interfaces: 2\n  host-m1\n    Good LACP PDUs received: 11\n    Bad LACP PDUs received: 0\n    LACP PDUs sent: 13\n    last LACP PDU received:        .51 seconds ago\n    last LACP PDU sent:        .15 seconds ago\n    Good Marker PDUs received: 0\n    Bad Marker PDUs received: 0\n    debug: 0\n    loopback port: 0\n    port_enabled: 1\n    port moved: 0\n    ready_n: 1\n    ready: 1\n    Actor\n      system: 02:fe:8b:6b:e0:41\n      system priority: 65535\n      key: 3\n      port priority: 255\n      port number: 1\n      state: 0x3f\n        LACP_STATE_LACP_ACTIVITY (0)\n        LACP_STATE_LACP_TIMEOUT (1)\n        LACP_STATE_AGGREGATION (2)\n        LACP_STATE_SYNCHRONIZATION (3)\n        LACP_STATE_COLLECTIING (4)\n        LACP_STATE_DISTRIBUTING (5)\n    Partner\n      system: 66:ea:8d:a1:23:03\n      system priority: 65535\n      key: 15\n      port priority: 255\n      port number: 1\n      state: 0x3f\n        LACP_STATE_LACP_ACTIVITY (0)\n        LACP_STATE_LACP_TIMEOUT (1)\n        LACP_STATE_AGGREGATION (2)\n        LACP_STATE_SYNCHRONIZATION (3)\n        LACP_STATE_COLLECTIING (4)\n        LACP_STATE_DISTRIBUTING (5)\n      wait while timer: not running\n      current while timer:       2.49 seconds\n      periodic timer:        .85 seconds\n    RX-state: CURRENT\n    TX-state: TRANSMIT\n    MUX-state: COLLECTING_DISTRIBUTING\n    PTX-state: PERIODIC_TX\n\n  host-m2\n    Good LACP PDUs received: 12\n    Bad LACP PDUs received: 0\n    LACP PDUs sent: 12\n    last LACP PDU received:        .19 seconds ago\n    last LACP PDU sent:        .95 seconds ago\n    Good Marker PDUs received: 0\n    Bad Marker PDUs received: 0\n    debug: 0\n    loopback port: 0\n    port_enabled: 1\n    port moved: 0\n    ready_n: 1\n    ready: 1\n    Actor\n      system: 02:fe:8b:6b:e0:41\n      system priority: 65535\n      key: 3\n      port priority: 255\n      port number: 2\n      state: 0x3f\n        LACP_STATE_LACP_ACTIVITY (0)\n        LACP_STATE_LACP_TIMEOUT (1)\n        LACP_STATE_AGGREGATION (2)\n        LACP_STATE_SYNCHRONIZATION (3)\n        LACP_STATE_COLLECTIING (4)\n        LACP_STATE_DISTRIBUTING (5)\n    Partner\n      system: 66:ea:8d:a1:23:03\n      system priority: 65535\n      key: 15\n      port priority: 255\n      port number: 2\n      state: 0x3f\n        LACP_STATE_LACP_ACTIVITY (0)\n        LACP_STATE_LACP_TIMEOUT (1)\n        LACP_STATE_AGGREGATION (2)\n        LACP_STATE_SYNCHRONIZATION (3)\n        LACP_STATE_COLLECTIING (4)\n        LACP_STATE_DISTRIBUTING (5)\n      wait while timer: not running\n      current while timer:       2.81 seconds\n      periodic timer:        .05 seconds\n    RX-state: CURRENT\n    TX-state: TRANSMIT\n    MUX-state: COLLECTING_DISTRIBUTING\n    PTX-state: PERIODIC_TX\n'
_bd = agent.parse_bond_details(BOND)
check(_bd == {"BondEthernet0": {"members": ["host-m1", "host-m2"],
                                "active": ["host-m1", "host-m2"]}},
      f"a bundle, its members and the active ones: {_bd}")
_ld = agent.parse_lacp_details(LACP)
check(sorted(_ld) == ["host-m1", "host-m2"]
      and _ld["host-m1"]["actor"] == {"system": "02:fe:8b:6b:e0:41",
                                      "system priority": 65535, "key": 3,
                                      "port priority": 255, "port number": 1,
                                      "state": 0x3f}
      and _ld["host-m2"]["partner"]["port number"] == 2
      and _ld["host-m1"]["Good LACP PDUs received"] == 11
      and _ld["host-m1"]["LACP PDUs sent"] == 13
      and _ld["host-m1"]["RX-state"] == "CURRENT"
      and _ld["host-m1"]["MUX-state"] == "COLLECTING_DISTRIBUTING",
      f"each member's actor, partner, counters and machines: {_ld.get('host-m1')}")
check(agent.parse_bond_details("") == {} and agent.parse_lacp_details("") == {},
      "no bundle, nothing")
check(agent._lacp_state(0x3f) == "FC" and agent._lacp_state(0x01) == "80"
      and agent._lacp_state(0) == "00",
      "VPP's state byte as LacpState BITS, activity the first bit")
_g = {o: (t, v) for o, t, v in agent.lag_rows(
    _bd, _ld, {"BondEthernet0": 4, "host-m1": 2, "host-m2": 3})}
G = "1.2.840.10006.300.43.1"
check(_g.get(f"{G}.1.1.1.4.4") == ("octet", "02 FE 8B 6B E0 41")
      and _g.get(f"{G}.1.1.1.8.4") == ("octet", "66 EA 8D A1 23 03")
      and _g.get(f"{G}.1.1.1.7.4") == ("integer", 3)
      and _g.get(f"{G}.1.1.1.10.4") == ("integer", 15),
      "the bundle's actor and partner, indexed by its ifIndex")
check(_g.get(f"{G}.2.1.1.13.2") == ("integer", 4)
      and _g.get(f"{G}.2.1.1.14.3") == ("integer", 2)
      and _g.get(f"{G}.2.1.1.21.2") == ("octet", "FC"),
      "each member attached to the bundle, its port number and state")
check(_g.get(f"{G}.2.2.1.1.2") == ("counter", 11)
      and _g.get(f"{G}.2.2.1.6.2") == ("counter", 13)
      and _g.get(f"{G}.2.3.1.1.2") == ("integer", 1)
      and _g.get(f"{G}.2.3.1.3.2") == ("integer", 6),
      "the PDU counters, and the receive and mux machines")
_off = dict(_bd, BondEthernet0={"members": ["host-m1", "host-m2"],
                                "active": ["host-m2"]})
check(dict((o, v) for o, t, v in agent.lag_rows(
          _off, _ld, {"BondEthernet0": 4, "host-m1": 2, "host-m2": 3}))
      .get(f"{G}.2.1.1.13.2") == 0,
      "a member not active is attached to no aggregator")
check(agent.lag_rows({}, {}, {}) == [], "no bundle, no table")
# Every column served is one IEEE8023-LAG-MIB defines, with its type.
_LG = open(os.path.join(os.path.dirname(AGENT), "..", "test", "mibs",
                        "IEEE8023-LAG-MIB.txt")).read()
_lg = {}
for _m in re.finditer(r"^\s*(dot3ad\w+)\s+OBJECT-TYPE(.*?)::=\s*\{\s*(\w+)\s+(\d+)\s*\}",
                      _LG, re.S | re.M):
    _lg[(_m.group(3), int(_m.group(4)))] = (_m.group(1),
        re.search(r"SYNTAX\s+(\S+)", _m.group(2)).group(1))
_GENT = {"1.1.1": "dot3adAggEntry", "2.1.1": "dot3adAggPortEntry",
         "2.2.1": "dot3adAggPortStatsEntry", "2.3.1": "dot3adAggPortDebugEntry"}
_GSYN = {"Integer32": "integer", "MacAddress": "octet", "TruthValue": "integer",
         "LacpKey": "integer", "InterfaceIndex": "integer", "LacpState": "octet",
         "Counter32": "counter", "INTEGER": "integer"}
for _o, (_t, _x) in _g.items():
    _rest = _o[len(G) + 1:].split(".")
    _key = (_GENT[".".join(_rest[:3])], int(_rest[3]))
    check(_key in _lg and _GSYN[_lg[_key][1]] == _t,
          f"{_o} served as {_t}, and IEEE8023-LAG-MIB says {_lg.get(_key)}")
print(f"  [ ok ] {len(_g)} rows, each an IEEE8023-LAG-MIB column with its type")

print("\n=== IP-MIB: the dataplane's addresses, under IF-MIB's ifIndex (#522) ===")
ADDR = ("pg0 (up):\n  L3 100.64.3.254/22\n  L3 2001:db8::1/64\n"
        "  L3 fe80::1/64\nlocal0 (dn):\ntap1 (up):\n  L3 10.0.0.9/24\n")
_px = agent.parse_interface_prefixes(ADDR)
check(_px == {"pg0": [("100.64.3.254", 22), ("2001:db8::1", 64),
                      ("fe80::1", 64)], "local0": [],
              "tap1": [("10.0.0.9", 24)]},
      f"each card's addresses with their prefix length: {_px}")
check(agent.parse_interface_addresses(ADDR)["pg0"]
      == ["100.64.3.254", "2001:db8::1", "fe80::1"],
      "and the addresses alone, as VRRPV3-MIB reads them")
_ip = {o: (t, v) for o, t, v in agent.ip_rows(_px, {"pg0": 7, "tap1": 9})}
I = "1.3.6.1.2.1.4"
check(_ip.get(f"{I}.1.0") == ("integer", 1)
      and _ip.get(f"{I}.25.0") == ("integer", 1),
      "a router: ipForwarding and ipv6IpForwarding forwarding")
check(_ip.get(f"{I}.20.1.2.100.64.3.254") == ("integer", 7)
      and _ip.get(f"{I}.20.1.3.100.64.3.254") == ("ipaddress", "255.255.252.0"),
      "ipAddrTable: pg0's address under pg0's ifIndex, with its mask")
check(_ip.get(f"{I}.34.1.3.1.4.100.64.3.254") == ("integer", 7)
      and _ip.get(f"{I}.34.1.3.2.16.32.1.13.184.0.0.0.0.0.0.0.0.0.0.0.1")
      == ("integer", 7),
      "ipAddressTable: both families, indexed by type, length and octets")
check(_ip.get(f"{I}.34.1.5.1.4.100.64.3.254")
      == ("objectid", f".{I}.32.1.5.7.1.4.100.64.0.0.22")
      and _ip.get(f"{I}.32.1.5.7.1.4.100.64.0.0.22") == ("integer", 2),
      "and each points at its prefix's row, which is served")
check(not any(".254.128." in o for o in _ip)
      and not any(o.endswith(".10.0.0.9") for o in _ip),
      "no link-local address, and no tap")
check(len(agent.ip_rows({}, {})) == 2, "no address: the two scalars alone")
# Every column served is one IP-MIB defines, with the type it declares.
_IM = open(os.path.join(os.path.dirname(AGENT), "..", "test", "mibs",
                        "IP-MIB.txt")).read()
_im = {}
for _m in re.finditer(r"^\s*(ip\w+)\s+OBJECT-TYPE(.*?)::=\s*\{\s*(\w+)\s+(\d+)\s*\}",
                      _IM, re.S | re.M):
    _im[(_m.group(3), int(_m.group(4)))] = (_m.group(1),
        re.search(r"SYNTAX\s+(\S+)", _m.group(2)).group(1))
_IENT = {"1": "ip", "25": "ip", "20.1": "ipAddrEntry",
         "32.1": "ipAddressPrefixEntry", "34.1": "ipAddressEntry"}
_ISYN = {"INTEGER": "integer", "IpAddress": "ipaddress",
         "InterfaceIndex": "integer", "RowPointer": "objectid",
         "IpAddressOriginTC": "integer", "IpAddressStatusTC": "integer",
         "TimeStamp": "timeticks", "RowStatus": "integer",
         "StorageType": "integer", "TruthValue": "integer",
         "IpAddressPrefixOriginTC": "integer", "Unsigned32": "gauge"}
for _o, (_t, _x) in _ip.items():
    _rest = _o[len(I) + 1:].split(".")
    _tab = _rest[0] if _rest[0] in ("1", "25") else ".".join(_rest[:2])
    _col = int(_rest[0]) if _rest[0] in ("1", "25") else int(_rest[2])
    _key = (_IENT[_tab], _col)
    check(_key in _im and _ISYN.get(_im[_key][1]) == _t,
          f"{_o} served as {_t}, and IP-MIB says {_im.get(_key)}")
print(f"  [ ok ] {len(_ip)} rows, each an IP-MIB column with its type")

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

print("=== BFD-STD-MIB, RFC 7331: bfdd's sessions (#559) ===")
# FRR 10.7.1's `show bfd peers json` and `show bfd peers counters json`, the
# shape test/test_prismash_bfd.py has: one configured single-hop session up,
# one multihop a protocol asked for, down, with no counters.
BFD_PEERS = """[
 {"multihop": false, "peer": "10.9.0.9", "local": "10.9.0.1", "vrf": "default",
  "interface": "dum0", "id": 1712291648, "remote-id": 77, "status": "up",
  "uptime": 8100, "diagnostic": "ok", "type": "configured",
  "receive-interval": 300, "transmit-interval": 300,
  "echo-receive-interval": 50, "echo-transmit-interval": 0,
  "detect-multiplier": 3, "remote-receive-interval": 500,
  "remote-transmit-interval": 300, "remote-detect-multiplier": 4},
 {"multihop": true, "peer": "2001:db8::2", "local": "2001:db8::1",
  "vrf": "default", "id": 99, "minimum-ttl": 254, "status": "down",
  "downtime": 367, "diagnostic": "control detection time expired",
  "type": "dynamic", "receive-interval": 300, "transmit-interval": 300,
  "detect-multiplier": 3}
]"""
BFD_COUNTERS = """[
 {"id": 1712291648, "control-packet-input": 4294967300,
  "controlPacketBadInput": 1, "control-packet-output": 121,
  "session-up": 1, "session-down": 0}
]"""
sessions = agent.parse_bfd(BFD_PEERS, BFD_COUNTERS)
check(sessions is not None and len(sessions) == 2
      and sessions[0]["counters"].get("session-up") == 1
      and sessions[1]["counters"] == {},
      f"both sessions read, counters folded in by id: {sessions!r}")
check(agent.parse_bfd("% Unknown command", BFD_COUNTERS) is None
      and agent.parse_bfd(None, None) is None,
      "an answer that is not bfdd's list is nothing, not 'no sessions'")
rows = {oid: (typ, val) for oid, typ, val in agent.bfd_rows(sessions)}
B, S, P = "1.3.6.1.2.1.222.1", "1.3.6.1.2.1.222.1.2.1", "1.3.6.1.2.1.222.1.3.1"
up, dn = 1712291648, 99
check(rows[f"{B}.1.3.0"] == ("integer", 2),
      "bfdNotificationsEnable reads false: no trap is sent, and it says so")
# Mutation shown red: bfdd's milliseconds served as they are - RFC 7331's
# BfdIntervalTC is microseconds, and an NMS read 300 ms as 300 us.
check(rows[f"{S}.25.{up}"] == ("gauge", 300000)
      and rows[f"{S}.26.{up}"] == ("gauge", 300000)
      and rows[f"{S}.27.{up}"] == ("gauge", 50000),
      "the timers in microseconds, as BfdIntervalTC is")
check(rows[f"{S}.29.{up}"] == ("gauge", 500000)
      and rows[f"{S}.31.{up}"] == ("gauge", 4)
      and f"{S}.29.{dn}" not in rows and f"{S}.31.{dn}" not in rows,
      "negotiated: the larger of ours and the neighbour's; none when unheard")
# Mutation shown red: IANAbfdSessStateTC read as RFC 5880's wire codes
# (AdminDown 0, Down 1, Init 2, Up 3) - every state one off.
check(rows[f"{S}.11.{up}"] == ("integer", 4)
      and rows[f"{S}.11.{dn}"] == ("integer", 2)
      and rows[f"{S}.10.{up}"] == ("integer", 1)
      and rows[f"{S}.10.{dn}"] == ("integer", 2),
      "bfdSessState up(4) and down(2), bfdSessOperStatus up(1) and down(2)")
check(rows[f"{S}.13.{dn}"] == ("integer", 1)
      and rows[f"{S}.13.{up}"] == ("integer", 0),
      "the diagnostic: control detection time expired is 1, ok is 0")
check(rows[f"{S}.3.{up}"] == ("integer", 1)
      and rows[f"{S}.3.{dn}"] == ("integer", 2)
      and rows[f"{S}.6.{up}"] == ("gauge", 3784)
      and rows[f"{S}.6.{dn}"] == ("gauge", 4784)
      and rows[f"{S}.23.{dn}"] == ("integer", 1)
      and rows[f"{S}.24.{dn}"] == ("gauge", 254),
      "single hop on 3784, multihop on 4784 with its GTSM TTL")
check(rows[f"{S}.22.{up}"] == ("octet", "0A 09 00 09")
      and rows[f"{S}.21.{dn}"] == ("integer", 2)
      and rows[f"{S}.22.{dn}"][1].startswith("20 01 0D B8"),
      "the neighbour's address, IPv4 and IPv6, as InetAddress octets")
check(rows[f"{S}.36.{up}"] == ("integer", 3)
      and rows[f"{S}.36.{dn}"] == ("integer", 2),
      "a `peer` statement's row is nonVolatile, a protocol's volatile")
check(rows[f"{P}.1.{up}"] == ("counter", 4) and rows[f"{P}.14.{up}"]
      == ("counter64", 4294967300) and rows[f"{P}.12.{up}"] == ("counter", 1)
      and f"{P}.1.{dn}" not in rows,
      "counters: Counter32 wraps, the HC column carries all of it; none "
      "invented for a session bfdd gave none")
check(rows[f"{B}.4.1.1.{up}"] == ("gauge", up)
      and rows[f"{B}.5.1.1.0.1.4.10.9.0.1.1.4.10.9.0.9"] == ("gauge", up),
      "the discriminator and IP map tables lead back to the session's index")
check(agent._octets("192.0.2.77") == (1, bytes([192, 0, 2, 77]))
      and agent._octets("2001:db8::1")[0] == 2
      and agent._octets("bananas") is None,
      "an address as InetAddressType and its octets; text that is not one, none")
check(agent.Bfd().rows == [] and agent.Bfd().at == 0.0,
      "a fresh BFD reader holds nothing until it has read bfdd")
order = [r[0] for r in sorted(agent.bfd_rows(sessions),
                              key=lambda r: [int(x) for x in r[0].split(".")])]
check(len(order) == len(set(order)), "no OID served twice")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
