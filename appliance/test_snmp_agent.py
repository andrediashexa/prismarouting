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
# "view hexa included <oid>": the OID is the FOURTH field, and reading the
# third gave "included", so the enterprise half was dark on every box while
# every standard object answered.
w(f"{BOX}/snmpd.conf", "rocommunity public\n"
                       "view hexa included .1.3.6.1.2.1.123\n"
                       "view hexa included .1.3.6.1.4.1.99999\n")
check(agent._enterprise() == "99999",
      f"'view hexa included .1.3.6.1.4.1.99999' read as "
      f"{agent._enterprise()!r}, and it is 99999")
w(f"{BOX}/snmpd.conf", "view hexa included .1.3.6.1.2.1.123\n"
                       "com2sec ro default .1.3.6.1.4.1.5\n")
check(agent._enterprise() == "",
      f"a conf whose only enterprise OID is not on a view line gave "
      f"{agent._enterprise()!r}")
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

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
