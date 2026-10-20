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

# accel-pppd's `show stat` with build/accel-ppp-ng.patch (#597), as
# prisma-bng-cli prints it - CR LF, the core's blocks, two RADIUS servers
# (the second marked down, the first listed twice as a reload with a new
# secret leaves it), the listener's block and the pppoe module's own. The
# counts are lab/pppoe.sh's own shape: three up, one bad login refused.
BNG_STAT = "\r\n".join([
    "uptime: 0.01:02:03", "cpu: 0%", "mem(rss/virt): 9632/171940 kB",
    "core:", "  mempool_allocated: 133445", "  thread_count: 1",
    "sessions:", "  starting: 1", "  active: 3", "  finishing: 0",
    "  started: 7", "  stopped: 4",
    "radius(1, 127.0.0.1):", "  state: active", "  fail count: 0",
    "  request count: 0", "  queue length: 0", "  auth sent: 1",
    "  auth accept: 1", "  auth reject: 0",
    "  auth lost(total/5m/1m): 0/0/0", "  auth avg query time(5m/1m): 1/0 ms",
    "radius(2, 127.0.0.2):", "  state: failed", "  fail count: 2",
    "  request count: 0", "  queue length: 0", "  auth sent: 10",
    "  auth accept: 6", "  auth reject: 1",
    "  auth lost(total/5m/1m): 3/3/1", "  auth avg query time(5m/1m): 2/2 ms",
    "  acct sent: 7", "  acct lost(total/5m/1m): 1/0/0",
    "  acct avg query time(5m/1m): 1/1 ms", "  interim sent: 12",
    "  interim lost(total/5m/1m): 2/0/0",
    "  interim avg query time(5m/1m): 1/1 ms",
    "radius(3, 127.0.0.1):", "  state: active", "  fail count: 0",
    "  request count: 0", "  queue length: 0", "  auth sent: 9",
    "  auth accept: 8", "  auth reject: 1",
    "  auth lost(total/5m/1m): 0/0/0", "  auth avg query time(5m/1m): 1/0 ms",
    "dae:", "  disconnect ack: 2", "  disconnect nak: 1", "  coa ack: 3",
    "  coa nak: 4", "  dropped: 5",
    "pppoe:", "  starting: 0", "  active: 3", "  delayed PADO: 0",
    "  recv PADI: 10", "  drop PADI: 0", "  sent PADO: 10",
    "  recv PADR(dup): 7(0)", "  sent PADS: 7", "  filtered: 0", ""])

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
# prTransceiver (#590) is the `--transceivers` process's, not this one's: its
# rows are what xcvr_rows() builds for the fixture modules, every column of
# both tables among them (an SFP past its warning, a QSFP's four lanes).
sys.path.insert(0, os.path.join(HERE, "..", "test"))
import transceiver_fixtures as _XF  # noqa: E402
_XT = agent.transceiver_module()
_xm = [_XT.decode(_XT.parse_dump(_XF.dump(n, "dpdk", t, d))[n])
       for n, t, d in (("a", "sff-8472", _XF.sfp_low_rx()),
                       ("b", "sff-8636", _XF.qsfp28()))]
for _o, _t, _v in agent.xcvr_rows("1.3.6.1.4.1.99999.1", _xm, {"a": 1, "b": 2},
                                  _XT.mw_to_dbm):
    _served[_o[len("1.3.6.1.4.1.99999."):]] = _t
# prSensor (#496 phase 5) is the `--entity` process's: the thresholds of the
# same two modules' sensors, each column among them.
for _o, _t, _v in agent.entity_rows(
        agent.entities(_xm, {"a": 1, "b": 2}, {}), "1.3.6.1.4.1.99999.1",
        0, 0, 30000):
    if _o.startswith("1.3.6.1.4.1.99999."):
        _served[_o[len("1.3.6.1.4.1.99999."):]] = _t
# prBng (#597) is read only when a request reaches .22 (subtree_wanted()), so
# oid_table() has none of it: the fixture's control plane, running, with
# both servers - every column of the table among them.
for _o, _t, _v in agent.bng_rows("1.3.6.1.4.1.99999.1", agent.bng_state(
        (0, "running\n"), (0, BNG_STAT))):
    _served[_o[len("1.3.6.1.4.1.99999."):]] = _t
# prBngShaper (#610) is read with it: the dataplane's totals and the limiters.
for _o, _t, _v in agent.shaper_rows("1.3.6.1.4.1.99999.1", agent.parse_shaper_totals(
        "down shaped 1 queued-packets 0 queued-bytes 0 passed 1 delayed 0 "
        "dropped-queue 0 dropped-codel 0 dropped-box 0 bytes 1\n"
        "up shaped 0 queued-packets 0 queued-bytes 0 passed 0 delayed 0 "
        "dropped-queue 0 dropped-codel 0 dropped-box 0 bytes 0\n"
        "buffers held 0 cap 512 cap-per-thread 512 threads 1 queues 0\n"),
        {"download": 1, "upload": 2}):
    _served[_o[len("1.3.6.1.4.1.99999."):]] = _t
_SYN["Integer32"] = "integer"
_objs = {}
for _o, _t in _served.items():
    # The longest prefix that is an object: a lane's row has two index
    # components (ifIndex.lane), every other table one.
    _parts = _o.split(".")
    _k = next((".".join(_parts[:n]) for n in range(len(_parts) - 1, 0, -1)
               if ".".join(_parts[:n]) in _want), ".".join(_parts[:-1]))
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
# #485: an IPv6 router's own address is its link-local. Mutation shown red:
# the sort dropped, which serves the card's global address as the primary -
# not the address the router's advertisements leave from (RFC 6527).
_vd6 = [dict(_vd[1], status=3)]
_v6 = {o: (t, v) for o, t, v in agent.vrrpv3_rows(
    _vd6, {"pg0": 1}, {"pg0": ["2001:db8::2", "fe80::2"]})}
_FE80_2 = "FE 80 00 00 00 00 00 00 00 00 00 00 00 00 00 02"
check(_v6.get(f"{V}.1.1.4.1.20.2") == ("octet", _FE80_2)
      and _v6.get(f"{V}.1.1.3.1.20.2") == ("octet", _FE80_2),
      f"the primary and the master's address are the link-local: {_v6}")
check(agent.parse_ip6_link_local(
          "pg0 is admin up\r\n  Global unicast address(es):\n    2001:db8::2/64\n"
          "  Link-local address(es):\n    fe80::2\n  Joined group address(es):\n")
      == "fe80::2"
      and agent.parse_ip6_link_local("pg0 is admin up\n") == "",
      "parse_ip6_link_local: the link-local under its heading, or nothing")
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
      "bfdNotificationsEnable reads false unless told the notifications "
      "are sent")
check(dict((o, (t, v)) for o, t, v in agent.bfd_rows(sessions, True))
      [f"{B}.1.3.0"] == ("integer", 1),
      "and true(1) when they are (#559)")
# bfd_notifying(): the host's watcher's file (#559, bfdtraps.py), true only
# while its pid runs, the file is fresh and snmpd holds a destination.
# Mutation shown red: the freshness not checked - a watcher whose process
# is gone and whose file stayed would read true for ever.
_nd = tempfile.mkdtemp(prefix="bfd-notify-")
_nf = os.path.join(_nd, agent.BFD_NOTIFY_FILE)
_alive = lambda p: p == 4242  # noqa: E731
check(agent.bfd_notifying(_nd, 1000.0, _alive) is False,
      "bfd_notifying: no file, no watcher: false")
w(_nf, "sending yes\nat 1000\npid 4242\ndestinations 1\n")
check(agent.bfd_notifying(_nd, 1010.0, _alive) is True,
      "bfd_notifying: the watcher sends, fresh, its pid running: true")
check(agent.bfd_notifying(_nd, 1000.0 + agent.BFD_NOTIFY_FRESH + 1, _alive)
      is False, "bfd_notifying: a file older than three ticks: false")
check(agent.bfd_notifying(_nd, 990.0, _alive) is False,
      "bfd_notifying: a file from the future (a clock set back): false")
w(_nf, "sending yes\nat 1000\npid 4343\ndestinations 1\n")
check(agent.bfd_notifying(_nd, 1010.0, _alive) is False,
      "bfd_notifying: its pid gone (systemctl stop): false at once")
w(_nf, "sending no\nat 1000\npid 4242\ndestinations 0\n")
check(agent.bfd_notifying(_nd, 1010.0, _alive) is False,
      "bfd_notifying: running with no destination: false")
# Above the kernel's largest pid_max (2^22): no process anywhere has it.
w(_nf, "sending yes\nat 1000\npid 4194305\ndestinations 1\n")
check(agent.bfd_notifying(_nd, 1010.0) is False,
      "bfd_notifying: a pid that runs nowhere, read from /proc")
w(_nf, f"sending yes\nat 1000\npid {os.getpid()}\ndestinations 1\n")
check(agent.bfd_notifying(_nd, 1010.0) is True,
      "bfd_notifying: and this test's own pid, which runs")
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
# Through the door, vtysh's stderr rides behind the list (#571).
check(agent.parse_bfd(BFD_PEERS + "\n% a warning\n", BFD_COUNTERS) is not None,
      "#571: text after bfdd's list does not cost the table")

print("\n=== bfdd read through the host's door, not as snmpd's user (#571) ===")
# 10.40.200.166, 0.150.10: as Debian-snmp, vtysh "failed to connect to any
# daemons" (bfdd.vty frr:frr 0770) and the MIB answered nothing. The agent
# asks the doors daemon instead; a stand-in daemon on a real unix socket
# answers here, so the protocol is exercised and not a stub of it.
import json as _json, socket as _socket, threading as _threading  # noqa: E402
_ddir = tempfile.mkdtemp()
_dsock = os.path.join(_ddir, "doors.sock")
_asked = []


def _door_daemon(reply_for):
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    srv.bind(_dsock)
    srv.listen(4)

    def loop():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = c.recv(65536)
                if not chunk:
                    break
                buf += chunk
            req = _json.loads(buf.decode())
            _asked.append(req["argv"])
            c.sendall(_json.dumps(reply_for(req["argv"])).encode() + b"\n")
            c.close()
    _threading.Thread(target=loop, daemon=True).start()
    return srv


def _answers(argv):
    if argv[-1] == "show bfd peers json":
        return {"rc": 0, "out": BFD_PEERS}
    if argv[-1] == "show bfd peers counters json":
        return {"rc": 0, "out": BFD_COUNTERS}
    return {"rc": 126, "out": "the host's door: not an operation"}


_srv = _door_daemon(_answers)
got = agent.door_ask(agent.BFD_READS["show bfd peers json"], path=_dsock)
check(got == (0, BFD_PEERS) and _asked[-1] == ["/usr/bin/vtysh", "-c",
                                               "show bfd peers json"],
      "#571: the door is asked the exact argv, and its answer comes back")
check(agent.door_ask(["/usr/bin/vtysh", "-c", "x"],
                     path=os.path.join(_ddir, "nobody")) is None,
      "#571: nothing listening is None, not an answer")
# Mutation shown red: vtysh_json() running vtysh as itself again - the stand-in
# vtysh below exits 1 as Debian-snmp's did, so only the door can answer.
_vt = os.path.join(_ddir, "vtysh")
with open(_vt, "w") as _f:
    _f.write("#!/bin/sh\necho 'Exiting: failed to connect to any daemons.'\n"
             "exit 1\n")
os.chmod(_vt, 0o755)
_saved_vtysh, agent.VTYSH = agent.VTYSH, _vt
_saved_ask = agent.door_ask
try:
    ask = lambda argv: _saved_ask(argv, path=_dsock)  # noqa: E731
    check(agent.vtysh_json("show bfd peers json", ask=ask) == BFD_PEERS
          and agent.vtysh_json("show bfd peers counters json", ask=ask)
          == BFD_COUNTERS,
          "#571: the agent reads bfdd through the door when vtysh as itself "
          "cannot")
    # An older daemon refuses a shape it does not know (126): the agent then
    # runs vtysh itself, as before - which is what root and a box with no
    # doors daemon always did.
    check(agent.vtysh_json("show bfd peers json",
                           ask=lambda argv: (126, "refused")) is None,
          "#571: a refusal falls back to vtysh here, and that failing is None")
    _ok = os.path.join(_ddir, "vtysh-ok")
    with open(_ok, "w") as _f:
        _f.write("#!/bin/sh\necho '[]'\n")
    os.chmod(_ok, 0o755)
    agent.VTYSH = _ok
    check(agent.vtysh_json("show bfd peers json", ask=lambda argv: None)
          == "[]\n",
          "#571: with no doors daemon at all, vtysh is run here")
    check(agent.vtysh_json("show bfd peers json",
                           ask=lambda argv: (1, "bfdd is not running")) is None,
          "#571: the door's own failure is bfdd silent, not a fallback")
finally:
    agent.VTYSH = _saved_vtysh
    _srv.close()
order = [r[0] for r in sorted(agent.bfd_rows(sessions),
                              key=lambda r: [int(x) for x in r[0].split(".")])]
check(len(order) == len(set(order)), "no OID served twice")


print("\n=== prTransceiver: the ports' modules (#590) ===")
sys.path.insert(0, os.path.join(HERE, "..", "test"))
import transceiver_fixtures as XF  # noqa: E402
XT = agent.transceiver_module()
check(XT.__name__ == "transceiver" and hasattr(XT, "decode"),
      "#590: the agent decodes with the VTY's own module, found from here")
_xdump = (XF.dump("enp1s0", "dpdk", "sff-8472", XF.sfp_low_rx(),
                  XF.SFF8472_READ, "port 0 device 0000:01:00.0 age 0.0")
          + XF.dump("hu0", "dpdk", "sff-8636", XF.qsfp28())
          + "interface enp2s0 unread no-module the card answered -5 "
            "(Input/output error) on DPDK port 1\n"
          + "interface host-eth5 unread not-dpdk not a DPDK port\n"
          + "interface tap4096 unread not-dpdk not a DPDK port\n")
_eth5 = "\n".join(XT.dump_lines("eth5", "linux", "sff-8472",
                                list(XF.sfp_external())))
_asked = []


def _xask(argv):
    _asked.append(argv[-1])
    if argv[-1] == "eth5":
        return 0, _eth5
    return 0, f"interface {argv[-1]} unread no-device -19\n"


reads = agent.xcvr_readings(XT, _xdump, ["eno1", "enp1s0"], set(), _xask)
check([r["name"] for r in reads] == ["enp1s0", "enp2s0", "host-eth5", "hu0"],
      f"#590: the DPDK ports, an af_packet port through its Linux card, not "
      f"the TAP; a Linux card the host does not have left out: "
      f"{[r['name'] for r in reads]}")
check(_asked == ["eth5", "eno1"],
      f"#590: the door asked for the Linux cards, not for a DPDK port: {_asked}")
check(agent.xcvr_readings(XT, None, [], set(), _xask) == [],
      "#590: a dataplane that did not answer serves none of its ports")
check([r["name"] for r in agent.xcvr_readings(
          XT, "interface enp9s0 unread not-dpdk x\n", [], {"enp9s0"},
          lambda a: (0, _eth5.replace("eth5", "enp9s0")))] == ["enp9s0"],
      "#590: a hot card is read from Linux under its own name")
mods = [XT.decode(r) for r in reads]
idx = {"enp1s0": 1, "enp2s0": 2, "host-eth5": 3, "hu0": 4}
rows = agent.xcvr_rows("1.3.6.1.4.1.67033.1", mods, idx, XT.mw_to_dbm)
got = {o: v for o, _t, v in rows}
typ = {o: t for o, t, _v in rows}
X = "1.3.6.1.4.1.67033.1.20"
check(got.get(f"{X}.1.1.2.1") == "enp1s0" and got.get(f"{X}.1.1.3.1") == 1
      and got.get(f"{X}.1.1.5.1") == "RSMPP8510GL-03D"
      and got.get(f"{X}.1.1.6.1") == "FP23H150378"
      and got.get(f"{X}.1.1.7.1") == 1310 and got.get(f"{X}.1.1.19.1") == "dpdk",
      "#590: prXcvrTable: name, status ddm, part, serial, wavelength, source")
check(got.get(f"{X}.1.1.8.1") == 3650 and got.get(f"{X}.1.1.9.1") == 7500
      and got.get(f"{X}.1.1.12.1") == -500 and typ[f"{X}.1.1.8.1"] == "integer",
      "#590: temperature in hundredths of a degree, its thresholds beside it, "
      "a negative one signed")
check(got.get(f"{X}.1.1.13.1") == 3300 and got.get(f"{X}.1.1.17.1") == 3000,
      "#590: voltage in millivolts")
check(got.get(f"{X}.1.1.18.1") == 2,
      "#590: prXcvrState is warning: the RX power is past its low warning")
check(got.get(f"{X}.2.1.2.1.1") == "enp1s0"
      and got.get(f"{X}.2.1.3.1.1") == -1900
      and got.get(f"{X}.2.1.6.1.1") == -1801
      and got.get(f"{X}.2.1.8.1.1") == 3,
      "#590: the lane's RX power, hundredths of a dBm, its low warning, "
      "and its level lowWarning")
check(got.get(f"{X}.2.1.9.1.1") == -210 and got.get(f"{X}.2.1.14.1.1") == 1
      and got.get(f"{X}.2.1.15.1.1") == 7500 and got.get(f"{X}.2.1.20.1.1") == 1,
      "#590: TX power -2.10 dBm normal, bias 7500 uA normal")
check(got.get(f"{X}.1.1.3.2") == 4 and f"{X}.1.1.8.2" not in got
      and not any(o.startswith(f"{X}.2.1.") and o.endswith(".2.1") for o in got),
      "#590: an empty cage: absent, no levels, no lane")
check(got.get(f"{X}.1.1.18.3") == 1
      and got.get(f"{X}.2.1.2.4.4") == "hu0 lane 4"
      and got.get(f"{X}.2.1.8.4.4") == 3 and got.get(f"{X}.2.1.8.4.1") == 1,
      "#590: a QSFP's four lanes, lane 4 low, named by lane")
check(got.get(f"{X}.1.1.4.3") == "FINISAR CORP.",
      "#590: the af_packet port's module, by its dataplane name")
check(len(rows) == len({o for o, _t, _v in rows}), "#590: no OID twice")
check(agent.xcvr_rows("b", mods, {}, XT.mw_to_dbm) == [],
      "#590: a port with no ifIndex has no row")
dark = XT.decode(XT.parse_dump(XF.dump("d", "dpdk", "sff-8472",
                                       XF.sfp_dark()))["d"])
drow = {o: v for o, _t, v in agent.xcvr_rows("b", [dark], {"d": 7},
                                             XT.mw_to_dbm)}
check(drow.get("b.20.2.1.3.7.1") == agent.NO_LIGHT == -4000
      and drow.get("b.20.2.1.8.7.1") == 5 and drow.get("b.20.1.1.18.7") == 3,
      "#590: no light is -40.00 dBm, low alarm, and the module in alarm")
nt = XT.decode(XT.parse_dump(XF.dump("n", "dpdk", "sff-8472",
                                     XF.sfp_no_thresholds()))["n"])
nrow = {o: v for o, _t, v in agent.xcvr_rows("b", [nt], {"n": 8}, XT.mw_to_dbm)}
check(nrow.get("b.20.1.1.8.8") == 3650 and "b.20.1.1.9.8" not in nrow
      and nrow.get("b.20.1.1.18.8") == 4 and nrow.get("b.20.2.1.8.8.1") == 6,
      "#590: no thresholds are absent, not 0, and the state is unknown")
check(agent.xcvr_state(mods[0]) == 2 and agent.xcvr_state(dark) == 3
      and agent.xcvr_state(nt) == 4 and agent.xcvr_state(mods[3]) == 2,
      "#590: prXcvrState: the worst level")
check(agent._centi_dbm(1.0, XT.mw_to_dbm) == 0
      and agent._centi_dbm(0.0, XT.mw_to_dbm) == -4000
      and agent._centi_dbm(0.00001, XT.mw_to_dbm) == -4000
      and agent._centi_dbm(None, XT.mw_to_dbm) is None,
      "#590: hundredths of a dBm, floored at no light")
_tc = agent.Transceivers()
check(_tc.rows == [] and _tc.at == 0.0,
      "#590: a new cache holds nothing, so the first poll reads")
_hot = os.path.join(BOX, "hot-cards")
w(_hot, "enp9s0 0000:09:00.0\n\nenp8s0\n")
check(agent.hot_card_names(_hot) == {"enp9s0", "enp8s0"},
      "#590: the hot cards' names")
check(agent.hot_card_names(os.path.join(BOX, "none")) == set(),
      "#590: none recorded, none")
check(agent.linux_dump(("x",), ask=lambda a: (0, "dump")) == "dump"
      and agent.linux_dump(("x",), ask=lambda a: (1, "no")) == "",
      "#590: the door's answer, or nothing when it failed")
_echo = os.path.join(BOX, "echo-dump")
w(_echo, "#!/bin/sh\necho interface $3 source linux\n")
os.chmod(_echo, 0o755)
check(agent.linux_dump((_echo, "p", "read", "eno1"), ask=lambda a: None)
      == "interface eno1 source linux\n"
      and agent.linux_dump((_echo, "p", "read", "eno1"),
                           ask=lambda a: (126, "refused")) != "",
      "#590: with no door, or an older one, the reader is run here")

print("\n=== ENTITY-MIB and ENTITY-SENSOR-MIB: the ports' modules (#496) ===")
E = "1.3.6.1.2.1.47"
ES = "1.3.6.1.2.1.99.1.1.1"
PT = "1.3.6.1.4.1.67033.1.21.1.1"
_dmi = os.path.join(BOX, "dmi")
w(f"{_dmi}/sys_vendor", "QEMU\n")
w(f"{_dmi}/product_name", "Standard PC (Q35 + ICH9, 2009)\n")
w(f"{_dmi}/bios_version", "rel-1.16.0\n")
w(f"{BOX}/ent-version", "0.153.0 2026-10-11\n")
_ch = agent.chassis_identity(_dmi, f"{BOX}/ent-version")
check(_ch == {"vendor": "QEMU", "model": "Standard PC (Q35 + ICH9, 2009)",
              "firmware": "rel-1.16.0", "serial": "", "release": "0.153.0"},
      f"#496: the chassis as DMI names it; the serial, root's alone, is "
      f"empty: {_ch}")
check(agent.chassis_identity(os.path.join(BOX, "nodmi"),
                             os.path.join(BOX, "nover"))
      == {"vendor": "", "model": "", "firmware": "", "serial": "",
          "release": ""}, "#496: no DMI (a container): nothing invented")

# The real box: 10.40.200.166's X520 with its fibre down, and two virtio
# cards whose driver cannot read a module.
_real = [XT.decode(r) for r in agent.xcvr_readings(XT, XF.X520_166, [],
                                                   set(), _xask)]
check([m["name"] for m in _real] == ["enp1s0", "enp6s18", "enp6s19"],
      f"#496: the X520 and the virtio cards, not the TAPs: "
      f"{[m['name'] for m in _real]}")
_ri = {"enp1s0": 1, "enp6s18": 2, "enp6s19": 3}
ents = agent.entities(_real, _ri, _ch, XT.connector_words)
check([e["index"] for e in ents] == [1, 1000, 1001, 1002, 1003, 1011, 1012,
                                     1013],
      f"#496: chassis, the X520's port, its module, five sensors - and "
      f"nothing for a virtio card: {[e['index'] for e in ents]}")
er = agent.entity_rows(ents, "1.3.6.1.4.1.67033.1", 4242, 0, 30000)
g = {o: v for o, _t, v in er}
gt = {o: t for o, t, _v in er}
P = f"{E}.1.1.1.1"
check(g.get(f"{P}.5.1") == 3 and g.get(f"{P}.4.1") == 0
      and g.get(f"{P}.6.1") == -1 and g.get(f"{P}.12.1") == "QEMU"
      and g.get(f"{P}.10.1") == "0.153.0"
      and g.get(f"{P}.2.1") == "Prisma Routing on QEMU Standard PC (Q35 + "
                               "ICH9, 2009)",
      "#496: the chassis: class chassis(3), in nothing, the release as its "
      "software")
check(g.get(f"{P}.5.1000") == 10 and g.get(f"{P}.4.1000") == 1
      and g.get(f"{P}.7.1000") == "enp1s0"
      and g.get(f"{P}.2.1000") == "port enp1s0, PCI 0000:01:00.0"
      and g.get(f"{P}.16.1000") == 2,
      "#496: the port: class port(10), in the chassis, named as ifName")
check(g.get(f"{E}.1.3.2.1.2.1000.0") == ".1.3.6.1.2.1.2.2.1.1.1"
      and gt[f"{E}.1.3.2.1.2.1000.0"] == "objectid",
      "#496: entAliasMappingIdentifier points the port at its ifIndex")
check(g.get(f"{P}.5.1001") == 9 and g.get(f"{P}.4.1001") == 1000
      and g.get(f"{P}.12.1001") == "Rei do SFP"
      and g.get(f"{P}.13.1001") == "RSMPP8510GL-03D"
      and g.get(f"{P}.11.1001") == "FP23H150378"
      and g.get(f"{P}.8.1001") == "2.1" and g.get(f"{P}.16.1001") == 1
      and g.get(f"{P}.2.1001") == "transceiver: SFP/SFP+/SFP28, LC "
                                  "connector, 850 nm",
      "#496: the module: class module(9), in the port, its vendor, part, "
      "serial and revision, field-replaceable")
check(g.get(f"{P}.17.1001") == "07 E7 08 0F 00 00 00 00"
      and gt[f"{P}.17.1001"] == "octet" and f"{P}.17.1000" not in g,
      "#496: its date code as a DateAndTime; none for the port")
check(g.get(f"{P}.3.1001") == ".0.0" and g.get(f"{P}.19.1001") == ""
      and f"{P}.18.1001" not in g,
      "#496: vendor type zeroDotZero, no UUID, no URIs")
want = {1002: (8, 9, 2, 3376, "C"), 1003: (4, 9, 4, 32355, "V"),
        1011: (5, 8, 3, 6552, "mA"), 1012: (6, 7, 1, 6309, "uW"),
        1013: (6, 7, 1, 1, "uW")}
got_s = {i: tuple(g.get(f"{ES}.{c}.{i}") for c in (1, 2, 3, 4, 6))
         for i in want}
check(got_s == want,
      f"#496: the box's own module as five sensors, in RFC 3433's units: "
      f"33.76 C, 3.2355 V, 6.552 mA, 630.9 uW out and 0.1 uW in: {got_s}")
check(all(g.get(f"{ES}.5.{i}") == 1 and g.get(f"{ES}.7.{i}") == 4242
          and g.get(f"{ES}.8.{i}") == 30000 and g.get(f"{P}.4.{i}") == 1001
          and g.get(f"{P}.5.{i}") == 8 for i in want),
      "#496: each sensor ok, read at the given sysUpTime, every 30 s, in "
      "the module")
check(g.get(f"{P}.7.1013") == "enp1s0 RX power"
      and g.get(f"{P}.7.1002") == "enp1s0 temperature"
      and g.get(f"{P}.6.1002") == 1 and g.get(f"{P}.6.1013") == 12,
      "#496: the sensors named for their port and placed in order")
check([g.get(f"{PT}.{c}.1013") for c in (1, 2, 3, 4, 5)]
      == [12589, 10000, 501, 316, 5],
      "#496: the RX power's own thresholds, in its units (1.2589 mW high "
      "alarm ... 0.0316 mW low alarm), and the level lowAlarm(5)")
check([g.get(f"{PT}.{c}.1002") for c in (1, 2, 3, 4, 5)]
      == [9000, 8000, -4000, -4500, 1],
      "#496: the temperature's, negative ones signed, normal(1)")
check(g.get(f"{E}.1.3.3.1.1.1.1000") == 1000
      and g.get(f"{E}.1.3.3.1.1.1000.1001") == 1001
      and g.get(f"{E}.1.3.3.1.1.1001.1013") == 1013
      and not any(o.startswith(f"{E}.1.3.3.1.1.0.") for o in g),
      "#496: entPhysicalContainsTable: chassis > port > module > sensor")
check(g.get(f"{E}.1.4.1.0") == 0 and gt[f"{E}.1.4.1.0"] == "timeticks",
      "#496: entLastChangeTime")
check(not any(o.startswith(f"{P}.") and o.split(".")[-1] in ("2000", "3000")
              for o in g),
      "#496: a virtio card has no port row: there is no cage to say")
check(len(er) == len({o for o, _t, _v in er}), "#496: no OID twice")
check(not [o for o, _t, _v in agent.entity_rows(ents, None, 0, 0, 1)
           if o.startswith("1.3.6.1.4.1.")],
      "#496: no number set: the two standard MIBs, no thresholds")

# The fixture modules: an SFP past its warning, a QSFP's four lanes, an
# empty cage, an af_packet port's module; and one without DDM, a DAC, a
# module that carries no thresholds and a flat-memory QSFP.
_more = mods + [XT.decode(XT.parse_dump(XF.dump(n, "dpdk", t, d))[n])
                for n, t, d in (("nd0", "sff-8079", XF.sfp_no_ddm()),
                                ("dac0", "sff-8079", XF.sfp_dac()),
                                ("nt0", "sff-8472", XF.sfp_no_thresholds()),
                                ("fq0", "sff-8636", XF.qsfp28(flat=True)))]
_mi = dict(idx, nd0=5, dac0=6, nt0=7, fq0=8)
ents2 = agent.entities(_more, _mi, {}, XT.connector_words)
ix2 = {e["index"]: e for e in ents2}
g2 = {o: v for o, _t, v in agent.entity_rows(ents2, "1.3.6.1.4.1.67033.1",
                                             1, 0, 30000)}
check(2000 in ix2 and 2001 not in ix2 and ix2[2000]["class"] == 10,
      "#496: an empty cage is a port with no module and no sensor")
check(ix2[1].get("descr") == "Prisma Routing" and ix2[1]["mfg"] == "",
      "#496: a chassis DMI does not name is the product, and no maker")
check(sorted(i for i in ix2 if 4000 < i < 5000)
      == [4001, 4002, 4003] + [4000 + 10 * ln + p for ln in (1, 2, 3, 4)
                               for p in (1, 2, 3)]
      and ix2[4043]["name"] == "hu0 lane 4 RX power"
      and g2.get(f"{PT}.5.4043") == 3 and g2.get(f"{PT}.5.4013") == 1,
      "#496: the QSFP: temperature, voltage and three per lane, lane 4's RX "
      "power lowWarning(3), lane 1's normal")
check(5001 in ix2 and 5002 not in ix2 and ix2[5001]["model"] == "SFP-10G-LR-ND"
      and 6001 in ix2 and 6002 not in ix2 and ix2[6001]["mfg"] == "Amphenol",
      "#496: a module without DDM, and a DAC: their identity, no sensor")
check(7002 in ix2 and 7013 in ix2
      and not any(o.startswith(PT) and o.split(".")[-1].startswith("7")
                  and len(o.split(".")[-1]) == 4 for o in g2),
      "#496: a module with no thresholds: its sensors, and no threshold row "
      "rather than one of zeros")
check(8002 in ix2 and 8043 in ix2 and f"{PT}.1.8043" not in g2,
      "#496: a flat-memory QSFP: its levels, without thresholds")
check(g2.get(f"{ES}.4.1013") == 126 and g2.get(f"{PT}.3.1013") == 158,
      "#496: the SFP's RX power past its warning, 12.6 uW against 15.8")
check(ix2[3001]["mfg"] == "FINISAR CORP." and ix2[3002]["sensor"]["value"]
      == 3650, "#496: the externally calibrated module, through its constants")
check(agent.entities(mods, {}, {}) == agent.entities([], {}, {})
      and len(agent.entities([], {}, {})) == 1,
      "#496: a port with no ifIndex has no row; the chassis is always there")
_unr = dict(mods[0], status="unreadable", vendor="", part="", serial="")
check([e["class"] for e in agent.entities([_unr], {"enp1s0": 1}, {})]
      == [3, 10, 9]
      and [e["class"] for e in agent.entities(
          [dict(_unr, type="")], {"enp1s0": 1}, {})] == [3],
      "#496: a module whose map the cage gave and whose identity could not "
      "be read is there; a card that read nothing is not")

check(agent.entity_index(1) == 1000 and agent.entity_index(3, 0, 1) == 3001
      and agent.entity_index(4, 4, 3) == 4043
      and agent.entity_index(2147482) == 2147482000
      and agent.entity_index(2147483) is None
      and agent.entity_index(0) is None,
      "#496: entPhysicalIndex from the ifIndex, inside PhysicalIndex's range")
check(agent.module_present({"status": "ddm"})
      and agent.module_present({"status": "copper"})
      and agent.module_present({"status": "unreadable", "type": "sff-8472"})
      and not agent.module_present({"status": "unreadable", "type": ""})
      and not agent.module_present({"status": "absent", "type": "sff-8472"}),
      "#496: what is a module in a cage")
check(agent.date_octets("2024-03-01") == "07 E8 03 01 00 00 00 00"
      and agent.date_octets("") == "" and agent.date_octets("2024-13-01") == ""
      and agent.date_octets("ABCDEF") == "",
      "#496: a date code as DateAndTime, nothing for one that is not a date")
_e = agent._entity(5, "port", 1, 5, "x", "port x", ifindex=5)
check(_e["class"] == 10 and _e["ifindex"] == 5 and _e["serial"] == ""
      and _e["fru"] == 2, "#496: an entity record, unknown columns empty")
check(agent._sensor(9, 8, 1, "s", "rx_power", {"value": None}) is None
      and agent._sensor(9, 8, 1, "s", "voltage", None) is None,
      "#496: a reading the module did not give is no sensor, not a 0")
_s = agent._sensor(9, 8, 1, "s", "temperature",
                   {"value": -5.5, "thresholds": None, "level": ""})
check(_s["sensor"]["value"] == -550 and _s["sensor"]["thresholds"] is None
      and _s["sensor"]["level"] == 6, "#496: a sensor with no thresholds")

_proc = os.path.join(BOX, "proc")
w(f"{_proc}/uptime", "1000.50 3000.00\n")
w(f"{_proc}/77/stat", "77 (snmpd (x) y) S 1 77 77 0 -1 4194560 1 2 3 4 5 6 7 8 "
                       "20 0 1 0 50000 1000 200\n")
check(agent.snmpd_uptime(_proc, 77, 100) == 50050
      and agent.snmpd_uptime(_proc, 78, 100) == 0,
      "#496: sysUpTime as snmpd counts it, from its start; 0 unread")
_en = agent.Entities()
check(_en.rows == [] and _en.note(ents, 10) == 0 and _en.note(ents, 20) == 0,
      "#496: entLastChangeTime 0 while the entities are the ones it began "
      "with")
_swap = [dict(_real[0], serial="NEW0001")] + _real[1:]
check(_en.note(agent.entities(_swap, _ri, _ch), 30) == 30
      and _en.note(agent.entities(_swap, _ri, _ch), 40) == 30,
      "#496: a module swapped - a new serial in the cage - moves it, once")
check(_en.note(agent.entities(_real[1:], _ri, _ch), 50) == 50,
      "#496: and a module taken out")
check(agent.entity_signature(ents) == agent.entity_signature(list(ents))
      and agent.entity_signature(ents) != agent.entity_signature(ents[:2]),
      "#496: the signature is the set of entities")

# THE DOCUMENT AND THE AGENT AGREE: every ENTITY-MIB, ENTITY-SENSOR-MIB and
# prSensor object served is in docs/snmp-mib.md's section on them, and
# every one that section names is served.
_mibdefs = {}
for _f in ("ENTITY-MIB", "ENTITY-SENSOR-MIB"):
    _t = open(os.path.join(HERE, "..", "test", "mibs", f"{_f}.txt")).read()
    for _m in _re.finditer(r"^(\w+)[ \t]+(?:OBJECT-TYPE|OBJECT IDENTIFIER|"
                           r"MODULE-IDENTITY)\b.*?::=\s*\{\s*(\S+)\s+(\d+)"
                           r"\s*\}", _t, _re.S | _re.M):
        _mibdefs[_m.group(1)] = (_m.group(2), _m.group(3))


def _std_oid(name):
    if name == "mib-2":
        return "1.3.6.1.2.1"
    par, n = _mibdefs[name]
    return f"{_std_oid(par)}.{n}"


_cols = {_std_oid(n): n for n in _mibdefs}
_cols.update({f"1.3.6.1.4.1.67033.1.21.1.1.{c}": n for c, n in enumerate(
    ("prSensorHighAlarm", "prSensorHighWarning", "prSensorLowWarning",
     "prSensorLowAlarm", "prSensorLevel"), 1)})
_names = set()
for _o, _t, _v in er + agent.entity_rows(ents2, "1.3.6.1.4.1.67033.1", 1, 0,
                                         1):
    _parts = _o.split(".")
    _k = next((".".join(_parts[:n]) for n in range(len(_parts), 0, -1)
               if ".".join(_parts[:n]) in _cols), None)
    check(_k is not None, f"#496: {_o} is served and no module defines it")
    if _k:
        _names.add(_cols[_k])
_doc = open(os.path.join(HERE, "..", "docs", "snmp-mib.md")).read()
_sec = _doc.split("## ENTITY-MIB and ENTITY-SENSOR-MIB")[1].split("\n## ")[0] \
    if "## ENTITY-MIB and ENTITY-SENSOR-MIB" in _doc else ""
# The objects the section's table rows are about: the first cell of each.
_said = {n for _row in _re.findall(r"^\| ([^|]*)\|", _sec, _re.M)
         for n in _re.findall(r"`(\w+)`", _row) if n in _cols.values()}
check(_sec and _names == _said,
      f"#496: docs/snmp-mib.md names every object served and serves every "
      f"one it names: served and not said {sorted(_names - _said)}, said "
      f"and not served {sorted(_said - _names)}")

print("\n=== prBng: PPPoE's control plane, read through the doors (#597) ===")
_bs = agent.parse_bng_stat(BNG_STAT)
check(_bs["uptime"] == 3723,
      f"#597: 0.01:02:03 is 3723 s of control plane: {_bs['uptime']}")
check(_bs["sessions"] == {"starting": 1, "active": 3, "finishing": 0,
                          "started": 7, "stopped": 4},
      f"#597: the sessions block, the patch's two counters in it: "
      f"{_bs['sessions']}")
check(_bs["dae"] == {"disconnect ack": 2, "disconnect nak": 1, "coa ack": 3,
                     "coa nak": 4, "dropped": 5},
      f"#597: the listener's block: {_bs['dae']}")
check(set(_bs["radius"]) == {"127.0.0.1", "127.0.0.2"}
      and _bs["radius"]["127.0.0.1"]["auth_sent"] == 9
      and _bs["radius"]["127.0.0.2"]["auth_lost"] == 3
      and _bs["radius"]["127.0.0.2"]["state"] == "failed",
      f"#597: a server per address, the later of two listings, lost's "
      f"total: {_bs['radius']}")
check("pppoe" not in _bs["sessions"] and _bs["sessions"]["active"] == 3,
      "#597: the pppoe module's own `active` is not read as the core's")
_br = {o: (t, v) for o, t, v in agent.bng_rows(
    "1.3.6.1.4.1.99999.1", agent.bng_state((0, "running\n"), (0, BNG_STAT)))}
B = "1.3.6.1.4.1.99999.1.22"
check(_br.get(f"{B}.1.0") == ("integer", 1)
      and _br.get(f"{B}.2.0") == ("timeticks", 372300)
      and _br.get(f"{B}.3.0") == ("gauge", 3)
      and _br.get(f"{B}.4.0") == ("gauge", 1)
      and _br.get(f"{B}.5.0") == ("counter", 7)
      and _br.get(f"{B}.6.0") == ("counter", 4),
      f"#597: running, its uptime in hundredths, 3 up of 7 started, 4 "
      f"stopped: {[(o, r) for o, r in _br.items() if '.20.' not in o]}")
check([_br.get(f"{B}.{n}.0") for n in (7, 8, 9, 10, 11)]
      == [("counter", 2), ("counter", 1), ("counter", 3), ("counter", 4),
          ("counter", 5)],
      "#597: Disconnect ACK/NAK, CoA ACK/NAK and the dropped, in that order")
check(_br.get(f"{B}.20.1.2.127.0.0.2") == ("string", "127.0.0.2")
      and _br.get(f"{B}.20.1.3.127.0.0.2") == ("integer", 2)
      and _br.get(f"{B}.20.1.3.127.0.0.1") == ("integer", 1)
      and _br.get(f"{B}.20.1.5.127.0.0.2") == ("counter", 6)
      and _br.get(f"{B}.20.1.6.127.0.0.2") == ("counter", 1)
      and _br.get(f"{B}.20.1.7.127.0.0.2") == ("counter", 3)
      and _br.get(f"{B}.20.1.8.127.0.0.2") == ("counter", 19)
      and _br.get(f"{B}.20.1.9.127.0.0.2") == ("counter", 3),
      f"#597: the second server: failed, 6 accepts, 1 reject, 3 timeouts, "
      f"accounting and interims together: "
      f"{[(o, r) for o, r in _br.items() if '127.0.0.2' in o]}")
check(_br.get(f"{B}.20.1.5.127.0.0.1") == ("counter", 8)
      and _br.get(f"{B}.20.1.8.127.0.0.1") == ("counter", 0),
      "#597: the first server is the later listing, and an auth-only server's "
      "accounting is zero")
_bo = [o for o, _t, _v in agent.bng_rows(
    "1.3.6.1.4.1.99999.1", agent.bng_state((0, "running"), (0, BNG_STAT)))]
check(_bo.index(f"{B}.20.1.2.127.0.0.1") < _bo.index(f"{B}.20.1.2.127.0.0.2"),
      "#597: the rows by address, numerically")
# A BOX WITH NO PPPoE IS ANSWERED (the requirement this group was built to):
# its state, zero sessions and counters, no server - never an error, and no
# uptime of a control plane that does not run.
for _word, _n in (("not-configured", 3), ("stopped", 2),
                  ("no-containers", 4)):
    _nr = {o: (t, v) for o, t, v in agent.bng_rows(
        "1.3.6.1.4.1.99999.1", agent.bng_state((0, _word + "\n"), None))}
    check(_nr.get(f"{B}.1.0") == ("integer", _n)
          and all(_nr.get(f"{B}.{k}.0", (None, None))[1] == 0
                  for k in range(3, 12))
          and f"{B}.2.0" not in _nr
          and not any(".22.20." in o for o in _nr),
          f"#597: {_word}: state {_n}, every count zero, no uptime, no "
          f"server: {_nr}")
check(agent.bng_rows("1.3.6.1.4.1.99999.1", agent.bng_state(None, None)) == []
      and agent.bng_state((1, "prisma-bng: usage"), None) is None
      and agent.bng_state((0, "something else"), None) is None,
      "#597: a status nobody could read is the group left out, not a state")
_half = {o: v for o, t, v in agent.bng_rows(
    "1.3.6.1.4.1.99999.1", agent.bng_state((0, "running"), (3, "not running")))}
check(_half == {f"{B}.1.0": 1},
      f"#597: running and the summary unanswered: the state alone, no "
      f"number it did not just give: {_half}")
check(agent.parse_bng_stat("") == {"uptime": None, "sessions": {},
                                   "radius": {}, "dae": {}},
      "#597: nothing read is nothing parsed")
check(agent._ip_index("192.0.2.10") == "192.0.2.10"
      and agent._ip_index("192.0.2") is None
      and agent._ip_index("192.0.2.300") is None,
      "#597: an IPv4 address is the four arcs of its index, nothing else is")
# The door first, as BFD's reads (#571): its exact argv, and prisma-bng run
# here only when no door answers or the door does not know the question.
_seen = []
check(agent.bng_ask("summary", ask=lambda a: (_seen.append(a) or
                                                (0, BNG_STAT))) == (0, BNG_STAT)
      and _seen == [("/usr/lib/prisma-routing/prisma-bng", "summary")],
      f"#597: the door is asked the doors' own argv: {_seen}")
check(agent.bng_ask("status", ask=lambda a: (3, "not running")) == (3, "not running"),
      "#597: the door's own failure is the answer, not a fallback")
_bd = tempfile.mkdtemp()
_bctl = os.path.join(_bd, "prisma-bng")
w(_bctl, "#!/bin/sh\necho not-configured\n")
os.chmod(_bctl, 0o755)
_saved_ctl, agent.BNG_CTL = agent.BNG_CTL, _bctl
try:
    check(agent.bng_ask("status", ask=lambda a: None) == (0, "not-configured\n")
          and agent.bng_ask("status", ask=lambda a: (126, "refused"))
          == (0, "not-configured\n"),
          "#597: no doors daemon, or an older one: prisma-bng is run here")
    agent.BNG_CTL = os.path.join(_bd, "nothing")
    check(agent.bng_ask("status", ask=lambda a: None) is None,
          "#597: nothing to ask at all is None")
finally:
    agent.BNG_CTL = _saved_ctl
check(agent.Bng().rows == [] and agent.Bng().at == 0.0,
      "#597: a fresh reader holds nothing until it has asked")
# prBngShaper (#610): the dataplane's shapers, box-wide.
print("\n=== prBngShaper: the per-session shapers' totals (#610) ===")
SHAPER_TOTALS = (
    "down shaped 3 queued-packets 7 queued-bytes 10500 passed 100 delayed 40 "
    "dropped-queue 5 dropped-codel 6 dropped-box 2 bytes 140000\n"
    "up shaped 1 queued-packets 1 queued-bytes 1500 passed 10 delayed 4 "
    "dropped-queue 1 dropped-codel 0 dropped-box 1 bytes 14000\n"
    "buffers held 8 cap 8192 cap-per-thread 4096 threads 2 queues 2\n")
_st = agent.parse_shaper_totals(SHAPER_TOTALS)
check(_st["down"]["shaped"] == 3 and _st["up"]["drop_box"] == 1
      and _st["down"]["drop_aqm"] == 6 and _st["buffers"]["cap"] == 8192
      and _st["buffers"]["queues"] == 2,
      f"#610: the totals screen, word by word: {_st}")
check(agent.parse_shaper_totals("") is None
      and agent.parse_shaper_totals(
          "show bpa pppoe-shaper: unknown input `totals'") is None
      and agent.parse_shaper_totals(SHAPER_TOTALS.replace(
          " dropped-box 2", "")) is None
      and agent.parse_shaper_totals("\n".join(
          SHAPER_TOTALS.splitlines()[:2])) is None,
      "#610: a dataplane older than the command, or half a screen, is "
      "nothing - never zeros")
check(agent.parse_limiters((0, "download shaper\nupload policer\n"))
      == {"download": 1, "upload": 2}
      and agent.parse_limiters((3, "not-configured\n"))
      == {"download": 3, "upload": 3}
      and agent.parse_limiters((1, "")) is None
      and agent.parse_limiters(None) is None
      and agent.parse_limiters((0, "download shaper\n")) is None,
      "#610: the limiters: shaper(1), policer(2), notConfigured(3); a read "
      "that failed or was half is None")
_sr = {o: (t, v) for o, t, v in agent.shaper_rows(
    "1.3.6.1.4.1.99999.1", _st, {"download": 1, "upload": 2})}
S = "1.3.6.1.4.1.99999.1.22.30"
check([_sr.get(f"{S}.{n}.0") for n in range(1, 16)]
      == [("integer", 1), ("integer", 2), ("gauge", 3), ("gauge", 1),
          ("gauge", 2), ("gauge", 8), ("gauge", 12000),
          ("counter64", 110), ("counter64", 44), ("counter64", 6),
          ("counter64", 6), ("counter64", 3), ("counter64", 154000),
          ("gauge", 8), ("gauge", 8192)],
      f"#610: the limiters, the sessions shaped each way, the queues, both "
      f"directions' counters summed, the buffers held of the cap: {_sr}")
check(set(agent.shaper_rows("1.3.6.1.4.1.99999.1", None,
                            {"download": 3, "upload": 3}))
      == {(f"{S}.1.0", "integer", 3), (f"{S}.2.0", "integer", 3)}
      and len(agent.shaper_rows("1.3.6.1.4.1.99999.1", _st, None)) == 13
      and agent.shaper_rows("1.3.6.1.4.1.99999.1", None, None) == [],
      "#610: each half served alone when the other could not be read, "
      "neither as zero")
_big = agent.parse_shaper_totals(SHAPER_TOTALS.replace(
    "passed 100", f"passed {2 ** 64 - 1}").replace(
    "queued-bytes 10500", f"queued-bytes {2 ** 33}"))
_br2 = {o: v for o, t, v in agent.shaper_rows("1.3.6.1.4.1.99999.1", _big,
                                              None)}
check(_br2[f"{S}.8.0"] == 9 and _br2[f"{S}.7.0"] == 2 ** 32 - 1,
      f"#610: a Counter64 wraps as one, a Gauge32 sticks at its top: "
      f"{_br2[f'{S}.8.0']}, {_br2[f'{S}.7.0']}")
check(agent.BNG_READS["limiters"] == ("/usr/lib/prisma-routing/prisma-bng",
                                      "limiters"),
      "#610: the limiters are asked of prisma-bng, as its door's argv")
# SERVED BY THE ENTERPRISE PROCESS, read only when a request reaches .22:
# net-snmp 5.9.3 hands a GET under a nested pass_persist registration to the
# broader one (measured), so .22 cannot be a registration of its own, and a
# poll of anything else must not pay a docker exec.
_pf = agent.as_key("1.3.6.1.4.1.67033.1.22")
_k = agent.as_key
check(agent.subtree_wanted("GET", _k("1.3.6.1.4.1.67033.1.22.3.0"), None, _pf)
      and not agent.subtree_wanted("GET", _k("1.3.6.1.4.1.67033.1.1.0"),
                               ("1.3.6.1.4.1.67033.1.1.0", "gauge", 1), _pf)
      and not agent.subtree_wanted("GET", _k("1.3.6.1.4.1.67033.1.23.0"), None, _pf),
      "#597: a GET reads prBng only inside .22")
check(agent.subtree_wanted("GETNEXT", _k("1.3.6.1.4.1.67033.1.19.5.0"), None, _pf)
      and agent.subtree_wanted("GETNEXT", _k("1.3.6.1.4.1.67033.1.22.20"), None, _pf)
      and not agent.subtree_wanted("GETNEXT", _k("1.3.6.1.4.1.67033.1.12.1.0"),
                               ("1.3.6.1.4.1.67033.1.12.2.0", "string", ""), _pf)
      and not agent.subtree_wanted("GETNEXT", _k("1.3.6.1.4.1.67033.1.23"), None, _pf),
      "#597: a GETNEXT reads it only when the rest ran out before .22 ends: "
      "a walk reaches it, a poll of the pools does not")
check(not agent.subtree_wanted("GET", _k("1.3.6.1.4.1.67033.1.22.1.0"), None, None),
      "#597: no enterprise number, no prBng")
# #608: prTransceiver and prSensorThresholdTable are read the same way, in
# OID order so a GETNEXT that runs out of one reaches the next.
check([a for a, _f in agent.LAZY] == [agent.XCVR, agent.SENSOR_TH, agent.BNG]
      == ["20", "21", "22"],
      "#608: .20, .21 and .22 served by the enterprise process, in order")
check(agent.subtree_wanted("GET", _k("1.3.6.1.4.1.67033.1.20.1.1.2.1"), None,
                           _k("1.3.6.1.4.1.67033.1.20"))
      and agent.subtree_wanted("GETNEXT", _k("1.3.6.1.4.1.67033.1.20.2.1.3.1.1"),
                               None, _k("1.3.6.1.4.1.67033.1.21")),
      "#608: a GET of a transceiver row reaches .20, a walk out of .20 .21")
check(agent.in_subtree([("1.3.6.1.2.1.47.1.0", "integer", 1),
                        ("1.3.6.1.4.1.9.1.21.1.1.1.1002", "integer", 9000)],
                       _k("1.3.6.1.4.1.9.1.21"))
      == [("1.3.6.1.4.1.9.1.21.1.1.1.1002", "integer", 9000)],
      "#608: of the ENTITY reader's rows, only the thresholds are the "
      "product's")
_rows = [("1.3.6.1.4.1.9.1.0", "gauge", 1), ("1.3.6.1.4.1.9.2.0", "gauge", 2)]
check(agent.find(_rows, "GET", _k("1.3.6.1.4.1.9.2.0")) == _rows[1]
      and agent.find(_rows, "GETNEXT", _k("1.3.6.1.4.1.9.1.0")) == _rows[1]
      and agent.find(_rows, "GETNEXT", _k("1.3.6.1.4.1.9.2.0")) is None
      and agent.find(_rows, "GET", _k("1.3.6.1.4.1.9.3.0")) is None,
      "find: a GET the same OID, a GETNEXT the next, None past the end")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
