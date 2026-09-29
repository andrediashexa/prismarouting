# What the box serves over SNMP, object by object

The reference for whoever sets up the NMS: every MIB the box answers, every
object of its own MIB, where each value comes from and what is worth an
alarm. `docs/snmp-plan.md` is the plan this implements; the wiki page
*Monitoramento e SNMP* is the operator's version, in Portuguese.

## Getting the MIB into the NMS

The box's own objects are defined in **PRISMA-ROUTING-MIB**. It is rooted at
the enterprise number configured on the box, so the box prints it with that
number filled in:

```
configure
 snmp enterprise-number 65535
 commit
 exit
show snmp mib
```

Save what `show snmp mib` prints as `PRISMA-ROUTING-MIB.txt` and load it in the
NMS beside the standard modules it imports (SNMPv2-SMI, SNMPv2-TC,
SNMPv2-CONF), which every NMS already has. Until an enterprise number is set
the box's own objects are not served at all, and `show snmp mib` says so rather
than print a MIB for a subtree nobody answers.

**Why the operator's number.** An object under somebody else's enterprise
number is a claim about who the object belongs to, and it collides the day
that organisation uses the subtree. When the product has its own IANA number
it becomes the default and nothing under it moves: the objects below are
numbered from the root, not from the number.

## The standard MIBs

Served by net-snmp itself, by the CGNAT's agent or by FRR's daemons over
AgentX. An NMS that knows these needs no template of ours.

| What | MIB | Subtree | Served by | Since |
|---|---|---|---|---|
| name, release, uptime | SNMPv2-MIB system | `.1.3.6.1.2.1.1` | net-snmp | 0.141.3 |
| the dataplane's cards: traffic, errors, status, speed | IF-MIB `ifTable`, `ifXTable` (64-bit) | `.1.3.6.1.2.1.2`, `.1.3.6.1.2.1.31` | the agent, from VPP's statistics segment | 0.141.5 |
| processors, memory, storage, processes | HOST-RESOURCES-MIB | `.1.3.6.1.2.1.25` | net-snmp | 0.141.3 |
| load, memory, disk thresholds | UCD-SNMP-MIB | `.1.3.6.1.4.1.2021` | net-snmp | 0.141.3 |
| the CGNAT's default timeouts | NAT-MIB `natDefTimeouts` | `.1.3.6.1.2.1.123.1.1` | the agent | 0.x |
| BGP peers: state, AS, updates, last error | BGP4-MIB | `.1.3.6.1.2.1.15` | bgpd | 0.142.0 |
| OSPF neighbours, interfaces, LSDB | OSPF-MIB | `.1.3.6.1.2.1.14` | ospfd | 0.142.0 |
| OSPFv3 | OSPFV3-MIB | `.1.3.6.1.2.1.191` | ospf6d | 0.142.0 |
| IS-IS adjacencies, circuits | ISIS-MIB | `.1.3.6.1.2.1.138` | isisd | 0.142.0 |
| the FIB's size (not the FIB) | IP-FORWARD-MIB `ipCidrRouteNumber`, `inetCidrRouteNumber` | `.1.3.6.1.2.1.4.24.3`, `.24.6` | net-snmp | 0.142.1 |

The route tables themselves are left out of the view on purpose: a full table
is about a million rows, and an NMS walking it would weigh on the box at every
poll.

## PRISMA-ROUTING-MIB

Under `.1.3.6.1.4.1.<PEN>`. Every object is read-only, and the agent refuses
every SET.

### The box's totals - `.1.<n>.0`

At the OIDs they have had since the agent was written, so an NMS set up
against them keeps working. Until #504 all six were typed Counter32; the four
that fall as well as rise are gauges now, and an NMS that stored them as
counters should be told so.

| OID | Object | Type | What it is |
|---|---|---|---|
| `.1.1.0` | `prSubscribers` | Gauge32 | subscribers holding a block now |
| `.1.2.0` | `prPortsInUse` | Gauge32 | ports allocated now, hold-down included |
| `.1.3.0` | `prPortsHeld` | Gauge32 | ports in the blocks held now |
| `.1.4.0` | `prSubscribersNearFull` | Gauge32 | subscribers using 90% or more of their ports |
| `.1.5.0` | `prPortRefusals` | Counter32 | ports refused since the dataplane started |
| `.1.6.0` | `prPacketsDropped` | Counter32 | packets the CGNAT did not pass since it started |

### The pool table - `.1.10.1.1.<column>.<pool>`

One row per pool; the index is the dataplane's pool index plus one.

| Column | Object | Type | What it is |
|---|---|---|---|
| 2 | `prPoolName` | string | the pool's name |
| 3 | `prPoolMode` | integer | deterministic(1), dynamic(2) |
| 4 | `prPoolBlockSize` | Gauge32 | ports in a block |
| 5 | `prPoolInsideAddresses` | Gauge32 | subscriber addresses in its ranges |
| 6 | `prPoolOutsideAddresses` | Gauge32 | public addresses |
| 7 | `prPoolBlocksTotal` | Gauge32 | blocks it can hand out |
| 8 | `prPoolCapacity` | Gauge32 | subscribers it can serve at once: a ceiling |
| 9 | `prPoolGuaranteed` | Gauge32 | subscribers it serves whatever worker each lands on: size against this |
| 10 | `prPoolSubscribers` | Gauge32 | subscribers holding a block now |
| 11 | `prPoolBlocksHeld` | Gauge32 | blocks held now |
| 12 | `prPoolUtilisation` | Gauge32 | blocks held over blocks total, percent |
| 13 | `prPoolSessions` | Gauge32 | sessions open |
| 14 | `prPoolPortsInUse` | Gauge32 | ports allocated |
| 15 | `prPoolSubscribersNearFull` | Gauge32 | subscribers at 90% or more |
| 16 | `prPoolRefusals` | Counter64 | ports refused since the pool was created |

### Drops and sessions - `.1.10.2`, `.1.10.3`

`prDropTable` (`.1.10.2.1.<column>.<n>`) has a row for each reason the CGNAT
has dropped a packet for: `prDropNode` (2), `prDropReason` (3) in the words of
`show cgnat drops`, and `prDropPackets` (4, Counter64). The row number follows
the node and the reason, so a reason seen for the first time can move the rows
after it: key on the reason, not on the number. `prSessions` (`.1.10.3.0`,
Gauge32) is the sessions open on the whole box.

### Capacity and the workers - `.1.10.4`, `.1.10.5` (#505)

`prSessionsFit` (`.1.10.4.0`, Gauge32) is the sessions that still fit in the
CGNAT's memory: the dataplane's free heap at the ~300 bytes a session takes,
the figure `show cgnat memory` prints. At zero, new sessions are refused while
the ones open go on. `prWorkerTable` (`.1.10.5.1.<column>.<thread + 1>`) gives
each dataplane thread's subscribers (2), sessions (3) and ports (4). A dynamic
pool's blocks are shared out per worker, so a worker far ahead of the others
is the early sign of subscribers refused with blocks free elsewhere.

### The connection records - `.1.11`

`prLogIpfixEnabled` (`.1.11.1.0`, TruthValue), `prLogIpfixRecords` (`.1.11.2.0`)
and `prLogIpfixDropped` (`.1.11.3.0`, both Counter64) for IPFIX over UDP; and
`prLogTransportTable` (`.1.11.10.1.<column>.<n>`) for the two reliable
streams, 1 IPFIX over TCP and 2 syslog: name (2), enabled (3), connected (4),
records (5), sent (6), spilled (7), dropped (8) and connects (9).

### The box - `.1.12`

`prSystemRelease` (`.1.12.1.0`) is the release installed;
`prSystemDataplaneBuild` (`.1.12.2.0`) the build of the plugin the running
dataplane loaded; `prSystemDataplaneCurrent` (`.1.12.3.0`, TruthValue) whether
they are the same. It is false after an upgrade until the next reboot, which
is a box forwarding with the old build and refusing any commit that needs the
new one.

### High availability - `.1.13` (#505)

`prVrrpTable` (`.1.13.1.1.<column>.<n>`) has one row per VRRP virtual router,
by interface and then VR ID: the interface (2), the VR ID (3), the priority
advertised now (4) - lower than configured while a tracked interface is down -
the state (5: init(1), backup(2), master(3), interfaceDown(4)) and the
virtual addresses (6).

### LACP bundles - `.1.14` (#505)

`prBundleTable` (`.1.14.1.1.<column>.<n>`): the bundle's interface (2), its
mode (3), the members configured (4) and the members carrying traffic (5).
A bundle on one of its two legs is up in IF-MIB and at half its capacity.

### Identity sources - `.1.15` (#505)

`prIdentityEntries` (`.1.15.1.0`) is the subscribers the identity table names
and `prIdentityReadAt` (`.1.15.2.0`) when it was last read, as the dataplane
prints it; `prRadiusReceived`, `prRadiusApplied` and `prRadiusRefused`
(`.1.15.3.0` to `.1.15.5.0`, Counter64) the RADIUS accounting the box takes
from a BNG. A refusal is a bad authenticator or a malformed packet, and the
first is usually a shared secret that differs between the two.

## Notifications (#507)

The box tells the NMS when something changes, rather than waiting for the
next poll, to every destination configured:

```
configure
 snmp trap-host 10.0.0.9 community <string>
 snmp trap-host 10.0.0.10 community <string> inform
 commit
 exit
```

A destination gets **traps** unless it says `inform`: an inform is
acknowledged by the receiver and resent until it is. The community is blanked
on every screen, as the agent's own is. `no snmp trap-host <address>` stops
sending there.

| When | Notification | From |
|---|---|---|
| a dataplane card goes down or comes up | `linkDown`, `linkUp` (IF-MIB) | net-snmp, polling the agent's `ifOperStatus` |
| a BGP session drops or is established | `bgpBackwardTransition`, `bgpEstablished` (BGP4-MIB) | bgpd, over AgentX |
| a VRRP router changes state | `mteTriggerFired` named `vrrpStateChange`, with the interface, VR ID and state | a monitor on `prVrrpState` |
| a pool reaches 90% of its blocks | `poolNearlyFull`, with the pool's name and utilisation | a monitor on `prPoolUtilisation` |
| ports are refused | `portsRefused` | a monitor on `prPortRefusals` |
| a records transport connects or drops | `recordsTransportChange`, with the stream | a monitor on `prLogTransportConnected` |
| records are lost | `recordsLost`, with the stream | a monitor on `prLogTransportDropped` |
| a bundle's active members change | `bundleMembersChange`, with the bundle | a monitor on `prBundleActiveMembers` |
| an upgrade leaves the old dataplane forwarding | `dataplaneNotCurrent` | a monitor on `prSystemDataplaneCurrent` |
| a disk, memory or load threshold | net-snmp's default monitors (`dskErrorFlag` and the rest) | net-snmp |

The monitors are polled every 30 or 60 seconds, so a notification follows the
event by up to a minute. They go under the enterprise number, like the objects
they watch, so without one only linkUp/linkDown, BGP and the default monitors
are sent. `lab/snmp-traps.sh` receives every monitored one with snmptrapd,
and `lab/routing.sh` the two BGP ones; OSPF's neighbour notifications are sent
by ospfd by the same path and have not been verified end to end.

Two things measured on the way, both in the monitors' definitions: a monitor
fires when its test **becomes** true - one already true when snmpd starts is
evaluated before the agent answers and never fires, which is not the box's
case, since the stale-dataplane state begins at an upgrade with snmpd up - and
a scalar needs `-I`, or snmpd walks under `.5.0`, finds nothing and the
monitor never fires. snmpd queries itself as an internal user with no
password, and it is not an account: asked from outside, it answers
"Unknown user name".

## What is worth an alarm

| Condition | Why |
|---|---|
| `prSubscribersNearFull` above zero | the early warning: at 100% the subscriber is refused ports |
| `prPortRefusals` rising | a subscriber could not open a connection |
| `prPoolUtilisation` above 80% | the pool is running out of blocks |
| `prPoolSubscribers` approaching `prPoolGuaranteed` | the pool is near what it can promise |
| `prLogTransportEnabled` true and `prLogTransportConnected` false | connection records going to the spill file |
| `prLogTransportDropped` or `prLogIpfixDropped` rising | records lost: a gap in the compliance log |
| `prSystemDataplaneCurrent` false | an upgrade waiting for a reboot |
| `bgpPeerState` not 6, `ospfNbrState` not 8, `ospfv3NbrState` not 8, `isisISAdjState` not 3 | a routing session down |
| `dskErrorFlag` 1 on `/var/log/prisma-routing` | the retention log's filesystem filling |
| `inetCidrRouteNumber` falling sharply | a session dropped and took its routes |
| `prSessionsFit` near zero | new sessions are about to be refused for want of memory |
| `prVrrpState` changing | a takeover |
| `prBundleActiveMembers` below `prBundleMembers` | a bundle running on fewer legs than it has |
| `prIdentityEntries` zero on a box that logs identities, or `prRadiusRefused` rising | records naming nobody, or a BNG the box will not listen to |

## Where the values come from, and what reading them costs

The CGNAT's numbers are kept by the dataplane **where they change**, per pool
and per thread, in VPP's statistics segment (`/bpa/pool/*`, `/bpa/log/*`,
listed in `src/plugins/bpa/bpa_stats.h`), and the agent reads them with one
`vpp_get_stats` call per refresh. Before #504 the agent parsed
`show bpa top 100000`, which walks every subscriber in every worker's table:
a million entries every ten seconds on a box sized for a million subscribers,
to answer six numbers. The drops come from `show bpa drops`, which reads
VPP's error counters and walks nothing. Every value is cached for ten seconds,
and a dataplane that does not answer serves no value at all rather than the
last one seen.

## What each feature of the box has, and what it has not yet

| Feature | Monitored by | State |
|---|---|---|
| identity, CPU, memory, disks | SNMPv2-MIB, HOST-RESOURCES, UCD | served |
| the dataplane's cards | IF-MIB | served |
| BGP, OSPF, OSPFv3, IS-IS, the FIB's size | the standard MIBs, from FRR | served |
| CGNAT pools, capacity, utilisation, sessions, ports, refusals, drops | PRISMA-ROUTING-MIB | served |
| CGNAT timeouts | NAT-MIB | served |
| the connection records' transports | PRISMA-ROUTING-MIB | served |
| the release and the running dataplane's build | PRISMA-ROUTING-MIB | served |
| session capacity, the workers | PRISMA-ROUTING-MIB | served (#505) |
| HA: each VRRP router's state and priority | PRISMA-ROUTING-MIB | served (#505) |
| LACP bundles: members in the bundle | PRISMA-ROUTING-MIB | served (#505) |
| identity sources: entries, last read, RADIUS | PRISMA-ROUTING-MIB | served (#505) |
| HA: whether the peer agrees on the mapping | `show cgnat ha` | not over SNMP: the check reads the peer's configuration off the box's own files, which the agent does not |
| static forwards, the self-test | `show cgnat forward`, the console at boot | not over SNMP |
| notifications | traps or informs, per destination | served (#507) |
