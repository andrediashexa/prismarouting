#!/usr/bin/env python3
"""
The IGP screens, and the two faults only this box can see.

`show ip ospf neighbor` is answered by FRR, and FRR's table is not the table
that forwards. These are the parts of the answer FRR cannot give:

  * an OSPF adjacency that will not leave Exchange because the dataplane and
    its TAP disagree about MTU - FRR sees the TAP and compares the TAP's
    number while the wire runs at the dataplane's (docs/routing.md, Pitfall 1);
  * an IS-IS circuit that is Up and Active with no adjacency because nothing
    is punting OSI to the kernel, whose only evidence is a VPP error counter
    (#168, Pitfall 2);
  * and, for both, how many of the prefixes FRR holds reached the VPP FIB.

Every fixture below is REAL output, captured from a box running lab/igp.sh's
topology in each of the states named. Plausible output written from memory
would agree with whatever the code happens to do, which is the one thing a
test must not do.

Here rather than only in the lab because the lab takes minutes and needs
docker, VPP and FRR, while what is checked here - which sentence is printed
about which state - is decidable in milliseconds.
"""
import contextlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "hexash"))
import config      # noqa: E402
import hexash      # noqa: E402
import routes      # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")
    else:
        print(f"  [ ok ] {msg}")


# --------------------------------------------------------------- captures
#
# vtysh, FRR 10.7.1, with the TAP at 9000 and the wire at 1500. The adjacency
# sat here for the whole 95 seconds it was watched.
OSPF_NBR_STALLED = """{
  "neighbors":{
    "10.0.0.2":[
      {
        "nbrState":"Exchange\\/DR",
        "nbrPriority":1,
        "converged":"Exchange",
        "role":"DR",
        "upTimeInMsec":58113,
        "routerDeadIntervalTimerDueMsec":36902,
        "upTime":"58.113s",
        "deadTime":"36.902s",
        "ifaceAddress":"10.0.0.2",
        "ifaceName":"vppwan0:10.0.0.1",
        "linkStateRetransmissionListCounter":0,
        "linkStateRequestListCounter":0,
        "databaseSummaryListCounter":0
      }
    ]
  }
}
"""

# The same neighbour 30 seconds earlier, before the wait timer expired and a DR
# was elected. 2-Way with a DR on the segment is a healthy steady state on a
# broadcast link - not every router forms a full adjacency with every other -
# and telling an operator about MTU here would be a false alarm on a box where
# nothing is wrong.
OSPF_NBR_2WAY = """{
  "neighbors":{
    "10.0.0.2":[
      {
        "nbrState":"2-Way\\/DROther",
        "nbrPriority":1,
        "converged":"2-Way",
        "role":"DROther",
        "upTimeInMsec":25120,
        "routerDeadIntervalTimerDueMsec":34887,
        "upTime":"25.120s",
        "deadTime":"34.887s",
        "ifaceAddress":"10.0.0.2",
        "ifaceName":"vppwan0:10.0.0.1",
        "linkStateRetransmissionListCounter":0,
        "linkStateRequestListCounter":0,
        "databaseSummaryListCounter":0
      }
    ]
  }
}
"""

# The same adjacency, two seconds after the MTU was aligned through the VTY.
OSPF_NBR_FULL = OSPF_NBR_STALLED.replace('"Exchange\\/DR"', '"Full\\/DR"') \
                                .replace('"converged":"Exchange"',
                                         '"converged":"Full"')

OSPF_IF_STALLED = """{
  "interfaces":{
    "vppwan0":{
      "ifUp":true,
      "ifIndex":5,
      "mtuBytes":9000,
      "bandwidthMbit":0,
      "ifFlags":"<UP,LOWER_UP,BROADCAST,RUNNING,MULTICAST>",
      "ospfEnabled":true,
      "ipAddress":"10.0.0.1",
      "ipAddressPrefixlen":24,
      "ospfIfType":"Broadcast",
      "localIfUsed":"10.0.0.255",
      "area":"0.0.0.0",
      "routerId":"10.0.0.1",
      "networkType":"BROADCAST",
      "cost":10,
      "transmitDelaySecs":1,
      "state":"Backup",
      "priority":1,
      "opaqueCapable":true,
      "drId":"10.0.0.2",
      "drAddress":"10.0.0.2",
      "bdrId":"10.0.0.1",
      "bdrAddress":"10.0.0.1",
      "timerMsecs":10000,
      "timerDeadSecs":40,
      "timerWaitSecs":40,
      "timerRetransmitSecs":5,
      "timerRetransmitWindowMsecs":50,
      "timerHelloInMsecs":9419,
      "nbrCount":1,
      "nbrAdjacentCount":0,
      "grHelloDelaySecs":10,
      "prefixSuppression":false,
      "nbrFilterPrefixList":"N/A",
      "lsaRetransmissions":0
    }
  }
}
"""

# The same command after `passive-interface vppwan0` committed: one key
# appears and nothing else moves. That key is the difference between a fault
# and a configuration.
OSPF_IF_PASSIVE = OSPF_IF_STALLED.replace(
    '"timerRetransmitWindowMsecs":50,',
    '"timerRetransmitWindowMsecs":50,\n      "timerPassiveIface":true,')

# isisd with the circuit up and nothing on the other end, which is what a box
# whose dataplane is not punting OSI looks like.
ISIS_NBR_NONE = """{
  "areas":[
    {
      "area":"HEXA",
      "circuits":[
        {
          "circuit":5
        }
      ]
    }
  ]
}
"""

ISIS_NBR_UP = """{
  "areas":[
    {
      "area":"HEXA",
      "circuits":[
        {
          "circuit":5,
          "adj":"upstream",
          "interface":"vppwan0",
          "level":2,
          "state":"Up",
          "expires-in":"29s",
          "snpa":"520a.4368.513b"
        }
      ]
    }
  ]
}
"""


def _isis_if(active):
    """The circuit, with the neighbour count it had in each of the two runs."""
    return """{
  "areas":[
    {
      "area":"HEXA",
      "circuits":[
        {
          "circuit":5,
          "interface":{
            "name":"vppwan0",
            "state":"Up",
            "is-passive":"active",
            "circuit-id":"0x5",
            "type":"lan",
            "level":"L2",
            "snpa":"02fe.345c.b700",
            "levels":[
              {
                "level":"L2",
                "metric":10,
                "active-neighbors":%d,
                "hello-interval":3,
                "holddown":{
                  "count":10,
                  "pad":"yes"
                },
                "cnsp-interval":10,
                "psnp-interval":2,
                "lan":{
                  "priority":64,
                  "is-dis":"no"
                }
              }
            ],
            "ip-prefix":{
              "ip":"10.0.0.1/24"
            }
          }
        }
      ]
    }
  ]
}
""" % active


ISIS_IF_SILENT = _isis_if(0)
ISIS_IF_UP = _isis_if(1)

# `show isis database` with `set-overload-bit` committed. The `*` marks the
# LSPs this box originated; the last column is ATT/P/OL.
ISIS_DB_OVERLOADED = """Area HEXA:
IS-IS Level-2 link-state database:
LSP ID                  PduLen  SeqNumber   Chksum  Holdtime  ATT/P/OL
hexarouting.00-00    *    108   0x00000005  0x0113    1160    0/0/1
upstream.00-00            127   0x00000005  0xbfed    1138    0/0/0
upstream.04-00             51   0x00000003  0x655f    1152    0/0/0

    3 LSPs
"""
ISIS_DB_CLEAR = ISIS_DB_OVERLOADED.replace("1160    0/0/1", "1160    0/0/0")

# The same database read on the NEIGHBOUR, where this box's LSP carries the
# overload bit and the neighbour's own does not. Nothing here is this box's.
ISIS_DB_PEER = """Area HEXA:
IS-IS Level-2 link-state database:
LSP ID                  PduLen  SeqNumber   Chksum  Holdtime  ATT/P/OL
hexarouting.00-00         108   0x00000005  0x0113    1159    0/0/1
upstream.00-00       *    127   0x00000005  0xbfed    1138    0/0/0

    2 LSPs
"""

# vppctl show errors, on the box of #168: the hellos arrived and were dropped.
VPP_ERRORS_OSI = """   Count                  Node                              Reason               Severity
        34             osi-input                     unknown osi protocol          error
         4             igmp-input             IGMP not enabled on this interface   error
         3             arp-reply                       ARP replies sent            info
"""
VPP_ERRORS_NONE = """   Count                  Node                              Reason               Severity
         6          host-veth-vpp-tx             tx sendto temporary failure       error
"""

VPP_SHOW_INTERFACE = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
host-veth-vpp                     1      up          9000/0/0/0     tx packets                     7
                                                                    tx bytes                     666
local0                            0     down          0/0/0/0
tap4096                           2      up          9000/0/0/0     rx packets                     6
                                                                    rx bytes                     516
"""
VPP_SHOW_LCP = """lcp default netns '<unset>'
lcp lcp-sync on
itf-pair: [0] host-veth-vpp tap4096 vppwan0 5 type tap
"""
IP_LINK_VPPWAN0 = ("5: vppwan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 "
                   "qdisc mq state UNKNOWN mode DEFAULT group default qlen "
                   "1000\\    link/ether 02:fe:f1:ec:74:46 brd "
                   "ff:ff:ff:ff:ff:ff")

# One OSPF prefix and one IS-IS prefix, plus the IS-IS copy of the connected
# link that zebra did NOT select. Trimmed to the prefixes that matter out of a
# capture taken with both IGPs converged.
RIB = json.dumps({
    "10.0.0.0/24": [
        {"protocol": "isis", "distance": 115, "metric": 20,
         "nexthops": [{"ip": "10.0.0.2", "interfaceName": "vppwan0"}]},
        {"protocol": "connected", "selected": True, "installed": True,
         "nexthops": [{"directlyConnected": True,
                       "interfaceName": "vppwan0"}]}],
    "198.18.0.0/24": [
        {"protocol": "ospf", "selected": True, "installed": True,
         "distance": 110, "metric": 20,
         "nexthops": [{"ip": "10.0.0.2", "interfaceName": "vppwan0"}]}],
    "198.19.0.0/24": [
        {"protocol": "isis", "selected": True, "installed": True,
         "distance": 115, "metric": 20,
         "nexthops": [{"ip": "10.0.0.2", "interfaceName": "vppwan0"}]}],
})

# The VPP FIB with both IGP prefixes in it, and the same FIB with the OSPF one
# taken out - a route FRR holds that linux_nl never carried across.
FIB_BOTH = """ipv4-VRF:0, fib_index:0, flow hash:[src dst] epoch:0 flags:none
10.0.0.0/24
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:10 buckets:1 uRPF:9 to:[0:0]]
    [0] [@4]: ipv4-glean: [src:10.0.0.0/24] host-veth-vpp: mtu:9000 next:1 flags:[] ffff
198.18.0.0/24
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:19 buckets:1 uRPF:21 to:[0:0]]
    [0] [@5]: ipv4 via 10.0.0.2 host-veth-vpp: mtu:1500 next:5 flags:[] 520a
198.19.0.0/24
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:20 buckets:1 uRPF:21 to:[0:0]]
    [0] [@5]: ipv4 via 10.0.0.2 host-veth-vpp: mtu:1500 next:5 flags:[] 520a
"""
FIB_NO_OSPF = "\n".join(
    l for i, l in enumerate(FIB_BOTH.splitlines())
    if not (4 <= i <= 7)) + "\n"


# ------------------------------------------------------------------ harness

class FakeVpp:
    """A dataplane answering from captured text, by command."""

    TRACE = False

    def __init__(self, answers):
        self.answers = answers
        self.commands = []

    def run(self, cmd):
        self.commands.append(cmd)
        for prefix, text in self.answers.items():
            if cmd.startswith(prefix):
                return 0, text
        return 0, ""


class FakeFrr:
    """vtysh answering from captured text, by command."""

    def __init__(self, answers):
        self.answers = answers
        self.commands = []

    def show(self, cmd):
        self.commands.append(cmd)
        if cmd in self.answers:
            return 0, self.answers[cmd]
        return 1, "% Unknown command.\n"


def shell(frr_answers, vpp_answers=None, routing=(), caps=None,
          host_mtu=IP_LINK_VPPWAN0):
    sh = hexash.Shell()
    sh.caps = {hexash.CAP_ALL, hexash.CAP_CGNAT} if caps is None else caps
    sh.frr = FakeFrr(frr_answers)
    sh.dp = FakeVpp(vpp_answers or {})
    text = config.ROUTING_HEADER + "\n" + "\n".join(routing) + "\n"
    sh.running_config = lambda: config.Config.parse(text)
    # The Linux half of the pair, which ospf_mtu_mismatch reads with `ip`.
    # Stubbed at the boundary rather than above it: the comparison itself is
    # the thing being tested, and a test that stubbed it would be testing the
    # stub.
    #
    # It answers for `vppwan0` and REFUSES for every other name, which is what
    # the box does: a linux_cp pair answers to three names and only the Linux
    # one is a device `ip` has ever heard of. A stub that answered for all
    # three would have the product name `tap4096` in a warning, which is a
    # name no operator can type at this VTY.
    def fake_ip(argv, **kw):
        dev = argv[-1] if isinstance(argv, (list, tuple)) else ""
        if dev != "vppwan0":
            return type("R", (), {"returncode": 1, "stdout": ""})()
        return type("R", (), {"returncode": 0, "stdout": host_mtu})()
    hexash.subprocess.run = fake_ip
    return sh


def out_of(fn, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args)
    return buf.getvalue()


OSPF_ROUTING = ["router ospf", " ospf router-id 10.0.0.1",
                " network 10.0.0.0/24 area 0"]
DP_STALLED = {"show interface": VPP_SHOW_INTERFACE, "show lcp": VPP_SHOW_LCP,
              "show ip fib": FIB_BOTH, "show errors": VPP_ERRORS_NONE}


# ------------------------------------------------- the OSPF MTU stall, said
print("=== an adjacency stuck in Exchange, with the MTUs apart ===")
sh = shell({"show ip ospf neighbor json": OSPF_NBR_STALLED,
            "show ip ospf interface json": OSPF_IF_STALLED,
            "show ip route json": RIB},
           DP_STALLED, routing=OSPF_ROUTING)
text = out_of(sh.show_ospf_neighbors)
print("\n".join("    " + l for l in text.splitlines()))
row = [l for l in text.splitlines() if "10.0.0.2" in l][0].split()
check(row[:3] == ["10.0.0.2", "vppwan0", "Exchange/DR"],
      f"the row is neighbour, interface, state: {row[:3]}")
# FRR renders the interface as "vppwan0:10.0.0.1" in this one command. The
# name in that column has to be one the next command takes, so the check is on
# the field and not on the line: "vppwan0" is a substring of the raw rendering
# too, and would pass either way.
check(row[1] == "vppwan0", "the interface column is a name, not a rendering")
check("0 full" in text, "and an adjacency that is not Full is not counted as one")
check("its MTU is 9000 while its Linux side vppwan0 is at 1500" in text,
      "and BOTH numbers of the mismatch, which is what makes it actionable")
check("sit in Exchange with no log line" in text,
      "named as the stall, which is the one thing worth saying here")
# The half of the diagnosis that is about the FAR end, and the one the lab can
# reproduce without staging anything: this box advertises the TAP's 9000,
# linux_cp starts every TAP at 9000, and the wire is 1500.
check("is in Exchange, which is where the two ends compare MTU; this end "
      "advertises 9000" in text,
      "and says what this end is advertising into it")
check("neither side logs a word about it" in text,
      "which is what makes it cost an afternoon rather than a minute")

print("\n=== the same box, two seconds after the MTU was aligned ===")
# The half that keeps the check above from being "print it always". Nothing
# changes but the state FRR reports and the MTU the box has.
sh = shell({"show ip ospf neighbor json": OSPF_NBR_FULL,
            "show ip ospf interface json": OSPF_IF_STALLED,
            "show ip route json": RIB},
           DP_STALLED, routing=OSPF_ROUTING,
           host_mtu=IP_LINK_VPPWAN0.replace("mtu 1500", "mtu 9000"))
text = out_of(sh.show_ospf_neighbors)
print("\n".join("    " + l for l in text.splitlines()))
check("Full/DR" in text, "the adjacency is reported as Full")
check("sit in Exchange" not in text and "compare MTU" not in text,
      "and nothing is said about a stall that is not happening")
check("1 adjacency(ies), 1 full" in text, "the count agrees")

print("\n=== an adjacency that is Full while the MTUs still differ ===")
# The other arm, and the one that is easy to get wrong: an operator who set
# `ip ospf mtu-ignore`, or a neighbour that does not check. The mismatch is
# real and the adjacency is up, so the stall sentence would be a false alarm.
sh = shell({"show ip ospf neighbor json": OSPF_NBR_FULL,
            "show ip ospf interface json": OSPF_IF_STALLED,
            "show ip route json": RIB},
           DP_STALLED, routing=OSPF_ROUTING)
text = out_of(sh.show_ospf_neighbors)
check("sit in Exchange" not in text,
      "a Full adjacency is never reported as stalled, MTUs apart or not")

print("\n=== a neighbour that is not Full and is not stuck either ===")
# The arm that separates "not Full" from "stuck in Exchange". Without it the
# diagnosis fires on every adjacency that is not Full, which on a broadcast
# segment with three routers on it is most of them.
sh = shell({"show ip ospf neighbor json": OSPF_NBR_2WAY,
            "show ip ospf interface json": OSPF_IF_STALLED,
            "show ip route json": RIB}, DP_STALLED, routing=OSPF_ROUTING,
           host_mtu=IP_LINK_VPPWAN0.replace("mtu 1500", "mtu 9000"))
text = out_of(sh.show_ospf_neighbors)
print("\n".join("    " + l for l in text.splitlines()))
check("2-Way/DROther" in text, "the state is reported as FRR reports it")
check("compare MTU" not in text,
      "and a 2-Way neighbour on a segment with a DR is not an MTU problem")

print("\n=== the dataplane's name for an interface FRR calls vppwan0 ===")
# A linux_cp pair answers to three names and only one of them is the
# dataplane's. VPP's own end is an interface too, named tap<n>, and it HAS an
# MTU - so a lookup that took the first name it recognised would put `tap4096`
# in a column, which is a name that is in no configuration anybody writes and
# that an operator cannot type at this VTY. The order the names come back in
# is a VPP implementation detail that has already differed between two labs in
# this repository, so it is asked here with the tap first.
sh = shell({}, DP_STALLED)
check(sh.dataplane_name("vppwan0",
                        {"vppwan0": ["tap4096", "host-veth-vpp", "vppwan0"]},
                        {"tap4096": 9000, "host-veth-vpp": 9000})
      == "host-veth-vpp",
      "the tap is skipped whatever order the names arrive in")
check(sh.dataplane_name("eth0", {}, {"host-veth-vpp": 9000}) is None,
      "an interface the dataplane does not own has no name here, not a wrong one")

print("\n=== one fact, one sentence ===")
# Two diagnoses of one fact that disagree are worse than one, so the commit
# check and this screen say it in the same words - from the same place.
said = hexash.mtu_stall_lines("host-veth-vpp", "vppwan0", 9000, 1500)
sh = shell({"show ip ospf neighbor json": OSPF_NBR_STALLED,
            "show ip ospf interface json": OSPF_IF_STALLED,
            "show ip route json": RIB},
           DP_STALLED, routing=OSPF_ROUTING)
text = out_of(sh.show_ospf_neighbors)
# The sentence written out, not asked of the function that produces it. This
# compared the screen against mtu_stall_lines()' own return value, and the
# screen is printed BY mtu_stall_lines() - so the two moved together and the
# assertion held for any pair of sentences at all, including two the reader
# could do nothing with.
check("host-veth-vpp: carries OSPF and its MTU is 9000 while its Linux "
      "side vppwan0 is at 1500" in text,
      f"the screen names both interfaces and both MTUs: {text!r}")
check(all(l.strip() in text for l in said),
      "and the commit check says it in those same words, from the same place")


# ------------------------------------------------------- the FIB comparison
print("\n=== what FRR holds, against what forwards ===")
sh = shell({"show ip ospf neighbor json": OSPF_NBR_FULL,
            "show ip ospf interface json": OSPF_IF_STALLED,
            "show ip route json": RIB},
           dict(DP_STALLED, **{"show ip fib": FIB_NO_OSPF}),
           routing=OSPF_ROUTING)
text = out_of(sh.show_ospf_neighbors)
print("\n".join("    " + l for l in text.splitlines()[-4:]))
check("1 of 1 OSPF prefix(es) are NOT in the forwarding table" in text,
      "a prefix FRR holds and VPP never got is counted")
check("198.18.0.0/24" in text.split("NOT in the forwarding table")[1],
      "and named, because a count on its own is not actionable")

sh = shell({"show ip ospf neighbor json": OSPF_NBR_FULL,
            "show ip ospf interface json": OSPF_IF_STALLED,
            "show ip route json": RIB}, DP_STALLED, routing=OSPF_ROUTING)
text = out_of(sh.show_ospf_neighbors)
check("all 1 OSPF prefix(es) are in the forwarding table" in text,
      "and when it did arrive, that is what is said instead")

print("\n=== a route the IGP holds and zebra did not choose ===")
# 10.0.0.0/24 arrives by IS-IS on the very link it is configured on, and zebra
# keeps the connected route. Counting the IS-IS copy as one the dataplane is
# missing would be a permanent fault on a healthy box.
sh = shell({"show isis neighbor json": ISIS_NBR_UP,
            "show isis interface detail json": ISIS_IF_UP,
            "show isis database": ISIS_DB_CLEAR,
            "show ip route json": RIB},
           dict(DP_STALLED, **{"show ip fib": FIB_BOTH}))
text = out_of(sh.show_isis_adjacency)
check("all 1 IS-IS prefix(es) are in the forwarding table" in text,
      f"one IS-IS prefix counted, not two: {text.splitlines()[-1].strip()!r}")


# ----------------------------------------------------------- the OSI punt
print("\n=== an IS-IS circuit that is Up and Active with nobody on it ===")
sh = shell({"show isis neighbor json": ISIS_NBR_NONE,
            "show isis interface detail json": ISIS_IF_SILENT,
            "show isis database": ISIS_DB_CLEAR,
            "show ip route json": RIB},
           dict(DP_STALLED, **{"show errors": VPP_ERRORS_OSI}))
sh.osi_protos_enabled = lambda: []
text = out_of(sh.show_isis_adjacency)
print("\n".join("    " + l for l in text.splitlines()))
check("no IS-IS adjacency" in text, "the absence is stated")
check("circuit vppwan0 in area HEXA is Up and Active" in text,
      "with the circuit that has nobody on it")
check("NOT punting IS-IS to the kernel" in text,
      "and the reason, which is the trap this box is known for")
check("34 frame(s) counted in osi-input" in text,
      "with the evidence that the hellos arrived and were dropped")

print("\n=== the same circuit once the product turned the punt on ===")
sh = shell({"show isis neighbor json": ISIS_NBR_NONE,
            "show isis interface detail json": ISIS_IF_SILENT,
            "show isis database": ISIS_DB_CLEAR,
            "show ip route json": RIB},
           dict(DP_STALLED, **{"show errors": VPP_ERRORS_OSI}))
sh.osi_protos_enabled = lambda: [hexash.Shell.OSI_ISIS]
text = out_of(sh.show_isis_adjacency)
print("\n".join("    " + l for l in text.splitlines()))
check("NOT punting" not in text,
      "the punt is not blamed for a silence it is not causing")
check("IS punting IS-IS to the kernel" in text and "far end" in text,
      "and the operator is sent to the far end instead")

print("\n=== an adjacency that exists and is not up ===")
# Init is the state an IS-IS adjacency sits in when the hellos are one-way.
# Taken from the Up capture with the one field changed, because "1 adjacency,
# 1 up" and "1 adjacency, 0 up" are the same sentence to a count that is not
# looking at the state.
sh = shell({"show isis neighbor json": ISIS_NBR_UP.replace('"state":"Up"',
                                                           '"state":"Init"'),
            "show isis interface detail json": ISIS_IF_UP,
            "show isis database": ISIS_DB_CLEAR,
            "show ip route json": RIB}, DP_STALLED)
sh.osi_protos_enabled = lambda: [hexash.Shell.OSI_ISIS]
text = out_of(sh.show_isis_adjacency)
check("1 adjacency(ies), 0 up" in text,
      f"an adjacency in Init is not counted as up: "
      f"{[l for l in text.splitlines() if 'adjacency(ies)' in l]}")

print("\n=== a passive IS-IS circuit with nobody on it is not a fault ===")
sh = shell({"show isis neighbor json": ISIS_NBR_NONE,
            "show isis interface detail json":
                ISIS_IF_SILENT.replace('"is-passive":"active"',
                                       '"is-passive":"passive"'),
            "show isis database": ISIS_DB_CLEAR,
            "show ip route json": RIB},
           dict(DP_STALLED, **{"show errors": VPP_ERRORS_OSI}))
sh.osi_protos_enabled = lambda: []
text = out_of(sh.show_isis_adjacency)
check("Up and Active" not in text and "NOT punting" not in text,
      f"a circuit told not to send hellos is not a punt failure: {text!r}")

print("\n=== an adjacency that is up ===")
sh = shell({"show isis neighbor json": ISIS_NBR_UP,
            "show isis interface detail json": ISIS_IF_UP,
            "show isis database": ISIS_DB_CLEAR,
            "show ip route json": RIB}, DP_STALLED)
sh.osi_protos_enabled = lambda: [hexash.Shell.OSI_ISIS]
text = out_of(sh.show_isis_adjacency)
print("\n".join("    " + l for l in text.splitlines()))
row = [l for l in text.splitlines() if "upstream" in l][0].split()
check(row[:4] == ["upstream", "vppwan0", "L2", "Up"],
      f"system id, circuit, level and state, in that order: {row[:4]}")
check("1 adjacency(ies), 1 up" in text, "and counted")
check("punting" not in text,
      "and nothing is said about the punt, which is plainly working")

print("\n=== the dataplane not answering is not 'the punt is off' ===")
sh = shell({"show isis neighbor json": ISIS_NBR_NONE,
            "show isis interface detail json": ISIS_IF_SILENT,
            "show isis database": ISIS_DB_CLEAR,
            "show ip route json": RIB},
           dict(DP_STALLED, **{"show errors": VPP_ERRORS_OSI}))
sh.osi_protos_enabled = lambda: None
text = out_of(sh.show_isis_adjacency)
# Flattened, because that sentence is now wrapped to the console and the
# fold falls between "kernel" and "is unknown" (#359). What is asserted is
# the sentence the operator reads, not the row the terminal happens to break
# it on - the same reading `" ".join(said.split())` carries in the apply, BGP
# and dataplane suites.
check("whether it is punting IS-IS to the kernel is unknown"
      in " ".join(text.split()),
      "'could not ask' is reported as not knowing, never as 'no'")
check("NOT punting" not in text, "and no conclusion is drawn from it")


# ------------------------------------------------------- the overload bit
print("\n=== the maintenance drain nobody remembers turning on ===")
check(hexash.isis_overload_set(ISIS_DB_OVERLOADED),
      "this box's own LSP carries the overload bit")
check(not hexash.isis_overload_set(ISIS_DB_CLEAR),
      "and the same database without it does not")
check(not hexash.isis_overload_set(ISIS_DB_PEER),
      "a NEIGHBOUR's LSP with the bit set is not this box being overloaded")
check(not hexash.isis_overload_set(""), "no database is not overloaded")

sh = shell({"show isis neighbor json": ISIS_NBR_UP,
            "show isis interface detail json": ISIS_IF_UP,
            "show isis database": ISIS_DB_OVERLOADED,
            "show ip route json": RIB}, DP_STALLED)
sh.osi_protos_enabled = lambda: [hexash.Shell.OSI_ISIS]
text = out_of(sh.show_isis_adjacency)
check("overload bit set" in text,
      "an operator with adjacencies and no transit traffic is told why")
sh = shell({"show isis neighbor json": ISIS_NBR_UP,
            "show isis interface detail json": ISIS_IF_UP,
            "show isis database": ISIS_DB_CLEAR,
            "show ip route json": RIB}, DP_STALLED)
sh.osi_protos_enabled = lambda: [hexash.Shell.OSI_ISIS]
check("overload" not in out_of(sh.show_isis_adjacency),
      "and a box that is not overloaded is not told it is")


# --------------------------------------------------------- the error counter
print("\n=== the counter, read by field ===")
check(hexash.osi_input_drops(VPP_ERRORS_OSI) == 34,
      f"the count comes from its own column: "
      f"{hexash.osi_input_drops(VPP_ERRORS_OSI)}")
check(hexash.osi_input_drops(VPP_ERRORS_NONE) == 0,
      "a box that never saw one counts nothing")
check(hexash.osi_input_drops("") == 0, "and no output is not a crash")
# A needle matched against the whole text would find this and report frames
# that were never dropped.
check(hexash.osi_input_drops(
    "   Count   Node   Reason\n"
    "        7   bpa-in2out   unknown osi protocol seen in a comment   error\n"
) == 0, "the phrase in another node's row is not this counter")


# ------------------------------------------------ what OSPF believes vs VPP
print("\n=== the two MTUs, on one screen ===")
sh = shell({"show ip ospf interface json": OSPF_IF_STALLED},
           DP_STALLED, routing=OSPF_ROUTING)
text = out_of(sh.show_ospf_interfaces)
print("\n".join("    " + l for l in text.splitlines()))
check("9000" in text and "host-veth-vpp" in text,
      "FRR's MTU and the dataplane's name are both there")
row = [l for l in text.splitlines() if l.strip().startswith("vppwan0")][0]
check(row.split()[-3:] == ["9000", "9000", "(host-veth-vpp)"],
      f"what OSPF believes, what the dataplane has, and whose: {row.split()}")
check("its MTU is 9000 while its Linux side vppwan0 is at 1500" in text,
      "and the stall is named here too")

print("\n=== a passive interface with no adjacency is not a fault ===")
sh = shell({"show ip ospf interface json": OSPF_IF_PASSIVE},
           DP_STALLED, routing=OSPF_ROUTING + [" passive-interface vppwan0"])
text = out_of(sh.show_ospf_interfaces)
print("\n".join("    " + l for l in text.splitlines()))
check("passive" in text, "it is marked passive")
check("sit in Exchange" not in text,
      "and an MTU it will never compare is not reported as a stall")

print("\n=== no adjacency, told apart ===")
sh = shell({"show ip ospf neighbor json": "{}\n",
            "show ip ospf interface json": "{}\n",
            "show ip route json": RIB}, DP_STALLED)
text = out_of(sh.show_ospf_neighbors)
check("no interface is running OSPF" in text,
      "nothing configured says so, rather than 'no neighbours'")
sh = shell({"show ip ospf neighbor json": "{}\n",
            "show ip ospf interface json": OSPF_IF_STALLED,
            "show ip route json": RIB}, DP_STALLED, routing=OSPF_ROUTING)
text = out_of(sh.show_ospf_neighbors)
check("1 interface(s) that are sending hellos" in text,
      "an interface that is sending hellos into silence says that instead")


# ---------------------------------------------------------- daemons and FRR
print("\n=== a daemon that is not running ===")
# Measured: vtysh answers "ospfd is not running" and exits 1, while a box with
# no OSPF at all answers "{}" and exits 0. Two situations, two first
# questions.
sh = shell({}, DP_STALLED)
text = out_of(sh.show_ospf_neighbors)
check("OSPF is not answering" in text and "ospfd may not be running" in text,
      "a daemon that is not answering is not an empty neighbour table")
text = out_of(sh.show_isis_adjacency)
check("IS-IS is not answering" in text, "and the same for isisd")

print("\n=== everything this VTY does not model goes to FRR ===")
sh = shell({}, DP_STALLED)
with contextlib.redirect_stdout(io.StringIO()):
    sh.show_ospf(["database"])
    sh.show_isis(["database", "detail"])
check(sh.frr.commands == ["show ip ospf database", "show isis database detail"],
      f"in FRR's own spelling, untouched: {sh.frr.commands}")
# `show ip ospf` with nothing after it is a command an operator arriving from
# another vendor types expecting the process summary, and still gets it.
sh = shell({}, DP_STALLED)
with contextlib.redirect_stdout(io.StringIO()):
    sh.show_ospf([], bare_is_neighbors=False)
check(sh.frr.commands == ["show ip ospf"],
      f"bare 'show ip ospf' is FRR's, not this one: {sh.frr.commands}")


# ------------------------------------------------------------------- clear
print("\n=== clear, which is an outage on purpose ===")
sh = shell({"clear ip ospf process": ""}, DP_STALLED)
text = out_of(sh.do_clear, "clear ospf process")
check(sh.frr.commands == ["clear ip ospf process"] and "cleared" in text,
      f"the short spelling reaches FRR's: {sh.frr.commands}")
sh = shell({"clear ip ospf process": ""}, DP_STALLED)
out_of(sh.do_clear, "clear ip ospf process")
check(sh.frr.commands == ["clear ip ospf process"],
      "and so does FRR's own, which an operator from another vendor types")
sh = shell({"clear isis neighbor": ""}, DP_STALLED)
out_of(sh.do_clear, "clear isis")
check(sh.frr.commands == ["clear isis neighbor"],
      f"bare 'clear isis' is every adjacency: {sh.frr.commands}")
sh = shell({"clear isis neighbor 0000.0000.0002": ""}, DP_STALLED)
out_of(sh.do_clear, "clear isis adjacency 0000.0000.0002")
check(sh.frr.commands == ["clear isis neighbor 0000.0000.0002"],
      "and one system id is one adjacency")

sh = shell({}, DP_STALLED, caps=set())
text = out_of(sh.do_clear, "clear ospf process")
check(sh.frr.commands == [],
      "a read-only account does not get to bounce every adjacency on the box")
check("permission denied" in text.lower(), f"and is told why: {text.strip()!r}")
sh = shell({}, DP_STALLED, caps={hexash.CAP_CGNAT})
out_of(sh.do_clear, "clear isis")
check(sh.frr.commands == [], "nor does one that may only configure CGNAT")

sh = shell({}, DP_STALLED)
text = out_of(sh.do_clear, "clear ospf everything")
check(sh.frr.commands == [] and "usage" in text,
      f"and a word it does not know reaches FRR as nothing: {text.strip()!r}")



# ------------------------------------------------------------------- OSPFv3
#
# #187: the VTY accepted `router ospf6`, committed it, and ospf6d was enabled
# in no file the product shipped. The installer and the self-test are where
# that is fixed; these are the two halves of it that live in this file - that
# the statement is DISCOVERABLE now, and that the one OSPFv2 check which is
# also true of OSPFv3 fires on an OSPFv3 stanza while the rest stay silent.
print("\n=== router ospf6 is something the box offers, not something it takes ===")
check("ospf6" in hexash.TREE["config"]["router"][1],
      "'?' under `router` lists ospf6 beside ospf, bgp and isis")
check("ospf6" in hexash.INTERFACE_BLOCK["ipv6"][1],
      "and `ipv6 ospf6` is offered inside an interface, where FRR puts it")
check("area" in hexash.INTERFACE_BLOCK["ipv6"][1]["ospf6"][1],
      "with the statement that actually enables it, `ipv6 ospf6 area`")
# The block's own grammar. Before this it fell through to the answer for a
# block this VTY does not model, which offers `exit` and nothing else - on a
# statement the box accepted.
sh = shell({}, DP_STALLED)
sh.frr_ctx = ["router ospf6"]
check(sh.block_grammar([]) is hexash.OSPF6_BLOCK,
      "and inside it the statements are the OSPFv3 ones, not an empty block")
sh.frr_ctx = ["router ospf"]
check(sh.block_grammar([]) is hexash.OSPF_BLOCK,
      "while `router ospf` still gets OSPFv2's, which the prefix could break")
# The three statements the grammar deliberately does not offer, because FRR
# leaves them out of its own read-back at their default (lab/frr-readback.sh
# 4e) and the read-back check would report a working commit as a dropped
# statement.
for word in ("hello-interval", "dead-interval", "priority"):
    check(word not in hexash.INTERFACE_BLOCK["ipv6"][1]["ospf6"][1],
          f"`ipv6 ospf6 {word}` is not offered: FRR omits it at its default")
    check(("ipv6 ospf6 " + word) in hexash.FRR_OMITS_AT,
          f"and the read-back check knows to excuse it")

print("\n=== an OSPFv3 stanza does not fire the OSPFv2 findings ===")
# An alarm that cannot be right is one nobody reads (#205, #208). Every
# finding in ospf_findings is about a v2 statement - `network ... area`,
# `passive-interface`, an IPv4 CGNAT pool - and none of them has an OSPFv3
# spelling, so the answer here has to be nothing at all.
OSPF6_ROUTING = ["router ospf6", " ospf6 router-id 10.0.0.1",
                 "interface vppwan0", " ipv6 ospf6 area 0"]
found = hexash.ospf_findings(OSPF6_ROUTING, {"host-veth-vpp": ["10.0.0.1/24"]},
                             {"vppwan0", "host-veth-vpp"},
                             {"p": type("P", (), {"inside": ["10.0.0.0/24"]})()})
check(found == [], f"nothing is said about it: {found}")
# And the control: the same box configured for OSPFv2 on the same interface
# IS reported, so the silence above is about the protocol and not about a
# check that has stopped working.
found2 = hexash.ospf_findings(
    ["router ospf", " network 10.0.0.0/24 area 0"],
    {"host-veth-vpp": ["10.0.0.1/24"]}, {"vppwan0", "host-veth-vpp"},
    {"p": type("P", (), {"inside": ["10.0.0.0/24"]})()},
    lcp_pairs={"host-veth-vpp": ["host-veth-vpp", "tap4096", "vppwan0"],
               "vppwan0": ["host-veth-vpp", "tap4096", "vppwan0"]},
    device_interfaces={"host-veth-vpp"})
check(any("facing subscribers" in why for _, _, why in found2),
      f"while the same interface under OSPFv2 still is: {found2}")

print("\n=== the MTU stall, which OSPFv3 has for the same reason ===")
# The one OSPFv2 finding that IS true of OSPFv3. Measured on lab/igp.sh's own
# topology: with the TAP at 9000 against a 1500 wire the OSPFv3 adjacency
# stopped short of Full, and reached it seconds after the two were made to
# agree. RFC 5340 keeps the Interface MTU field in the database description
# packet, so this is the same fault wearing a different protocol number - and
# before #187 the box said nothing, because every check keyed on ospf_state()
# and ospf_state() reads nothing out of an OSPFv3 configuration.
MTUS = {"host-veth-vpp": 9000}
sh = shell({}, DP_STALLED)
found = sh.ospf_mtu_mismatch(OSPF6_ROUTING, MTUS)
check(found == [("host-veth-vpp", "vppwan0", 9000, 1500)],
      f"an OSPFv3 interface whose two halves disagree is reported: {found}")
# The escape hatches, in OSPFv3's own spelling. Without these the warning
# cannot be switched off by anything an operator can type in a v3 stanza,
# which is how an always-on alarm starts.
sh = shell({}, DP_STALLED)
check(sh.ospf_mtu_mismatch(OSPF6_ROUTING + [" ipv6 ospf6 mtu-ignore"], MTUS) == [],
      "`ipv6 ospf6 mtu-ignore` silences it, the way `ip ospf mtu-ignore` does")
sh = shell({}, DP_STALLED)
check(sh.ospf_mtu_mismatch(OSPF6_ROUTING + [" ipv6 ospf6 passive"], MTUS) == [],
      "and so does `ipv6 ospf6 passive`: a port sending no hellos compares no MTU")
# The negative half. A box running neither OSPF must stay silent, or the
# check above is satisfied by a warning that is always on.
sh = shell({}, DP_STALLED)
check(sh.ospf_mtu_mismatch(["router bgp 65000"], MTUS) == [],
      "a box running neither OSPF is not warned about an MTU it does not compare")



print("\n=== the same-name pair, on the readers keyed by the dataplane's "
      "name (#389) ===")
# Every claimed card with a block is paired as itself now - `ens19 tap0
# ens19` - and the readers here found the Linux end by `name != ifname`,
# which on that line skips the Linux end and keeps the TAP. Measured on
# 0dbbf73 with ens19 at 9000 in the dataplane and 1500 in Linux:
# ospf_mtu_mismatch returned [] and the one `ip` call it made was for tap0;
# dataplane_name('ens19') was None, so `show ospf interface` printed '-' in
# the Dataplane column for a card whose MTU the dataplane has; and the
# passive warning read "FRR knows this interface as , not tap0". The #187
# stall warning was silent on exactly the box it was written for.
SAME_LCP = "itf-pair: [0] ens19 tap0 ens19 5 type tap\n"
SAME_IF = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
ens19                             1      up          9000/0/0/0
local0                            0     down          0/0/0/0
tap0                              2      up          9000/0/0/0
"""
IP_LINK_ENS19 = IP_LINK_VPPWAN0.replace("vppwan0", "ens19")
_asked = []


def _ip_ens19(argv, **kw):
    """`ip` on a box whose one Linux netdev is ens19; the TAP is refused."""
    dev = argv[-1] if isinstance(argv, (list, tuple)) else ""
    _asked.append(dev)
    ok = dev == "ens19"
    return type("R", (), {"returncode": 0 if ok else 1,
                          "stdout": IP_LINK_ENS19 if ok else "",
                          "stderr": ""})()


sh = shell({}, {"show interface": SAME_IF, "show lcp": SAME_LCP},
           routing=OSPF_ROUTING)
hexash.subprocess.run = _ip_ens19
found = sh.ospf_mtu_mismatch(OSPF_ROUTING, {"ens19": 9000})
check(found == [("ens19", "ens19", 9000, 1500)],
      f"the stall on a card paired as itself is not reported: {found}; ip "
      f"was asked about {_asked}")
check("tap0" not in _asked,
      f"ip was asked about the TAP, which Linux has no netdev by: {_asked}")
_same = routes.lcp_pairs(SAME_LCP)
check(sh.dataplane_name("ens19", _same, {"ens19": 9000, "tap0": 9000})
      == "ens19",
      f"the dataplane's name for the interface FRR calls ens19 is ens19, not "
      f"{sh.dataplane_name('ens19', _same, {'ens19': 9000, 'tap0': 9000})!r}")
_found = hexash.ospf_findings(
    OSPF_ROUTING + [" passive-interface tap0"], {"ens19": ["10.0.0.1/24"]},
    {"ens19", "tap0", "local0"}, {},
    lcp_pairs=_same, device_interfaces={"ens19", "tap0", "local0"})
_said = [why for _, stmt, why in _found if stmt == "passive-interface tap0"]
check(_said and "FRR knows this interface as ens19, not tap0" in _said[0],
      f"the passive warning on the same-name pair does not name the Linux "
      f"end: {_said}")
# And the old shape, in the same three places, so the rule is one rule.
sh = shell({}, DP_STALLED, routing=OSPF_ROUTING)
check(sh.ospf_mtu_mismatch(OSPF_ROUTING, {"host-veth-vpp": 9000})
      == [("host-veth-vpp", "vppwan0", 9000, 1500)],
      "the old shape still reports its stall against vppwan0")
print("  ens19 tap0 ens19: the stall, the Dataplane column and the passive "
      "warning all name ens19")

print("\n=== 'show interface <name>' keeps the first counter (#280) ===")
# The counters are relayed "straight from the dataplane, uninterpreted", and
# one of them was being thrown away. VPP puts the FIRST counter on the
# interface's own row, after the name, index, state and MTU; the code dropped
# the header with [1:] and then dropped that row with a second [1:].
#
# What made it cost something is that it did not look like a gap. The list
# began at `rx bytes` and ran to the end with nothing saying a row had gone,
# so `rx packets` - the counter somebody opens this screen to read - was
# simply absent, and an operator comparing it against the dataplane during a
# fault gets a different set of rows from the one the comment above it
# promises.

def _ip_stub(argv, **kw):
    """`ip` answering for vppwan0, with the fields subprocess.run really has.

    The stub in shell() above predates the interface-detail path, which reads
    stderr; a stub missing a field the product uses fails as an AttributeError
    inside the product, which reads like a defect in it.
    """
    dev = argv[-1] if isinstance(argv, (list, tuple)) else ""
    ok = dev == "vppwan0"
    return type("R", (), {"returncode": 0 if ok else 1,
                          "stdout": IP_LINK_VPPWAN0 if ok else "",
                          "stderr": ""})()


_ONE_IF = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
host-veth-vpp                     1      up          9000/0/0/0     rx packets                 12345
                                                                    rx bytes                 6789012
                                                                    tx packets                  9876
                                                                    drops                          7
"""
_QUIET_IF = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
loop0                             3      up          9000/0/0/0
"""

# Driven through the command an operator types, not through the helper, so
# the name resolution and the table read are exercised with it.
_sh = shell({}, {"show interface host-veth-vpp": _ONE_IF,
                 "show interface": VPP_SHOW_INTERFACE,
                 "show lcp": VPP_SHOW_LCP})
hexash.subprocess.run = _ip_stub
_said = out_of(_sh.show_interfaces, ["host-veth-vpp"])
check("rx packets" in _said,
      f"the first counter the dataplane printed is missing from 'show "
      f"interface host-veth-vpp', and nothing on the screen says a row was "
      f"dropped: {_said!r}")
for _c in ("rx bytes", "tx packets", "drops"):
    check(_c in _said, f"{_c} is missing too: {_said!r}")
# The identity is printed above the counters already, so it must not appear
# again inside them: a row reading "host-veth-vpp 1 up 9000/0/0/0 rx packets
# 12345" is not a counter line and does not line up with the rest.
check("host-veth-vpp" not in _said.split("counters", 1)[1],
      f"the interface's name, index and MTU are inside the counter list, "
      f"where they do not line up with the other rows: {_said!r}")

# An interface with nothing after its MTU has no counters, and must say so
# rather than presenting its own identity as a reading.
_QUIET_TABLE = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
loop0                             3      up          9000/0/0/0
"""
_sh = shell({}, {"show interface loop0": _QUIET_IF,
                 "show interface": _QUIET_TABLE,
                 "show lcp": ""})
hexash.subprocess.run = _ip_stub
_quiet = out_of(_sh.show_interfaces, ["loop0"])
check("all zero" in _quiet,
      f"an interface the dataplane reports no counters for does not say so: "
      f"{_quiet!r}")
check("9000/0/0/0" not in _quiet.split("counters", 1)[-1],
      f"an interface with no counters printed its own row as though it were "
      f"one: {_quiet!r}")
print("  every counter the dataplane printed, and none of the identity")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
