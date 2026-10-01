# What the box serves over SNMP, object by object

The reference for whoever sets up the NMS: every MIB the box answers, every
object of its own MIB, where each value comes from and what is worth an
alarm. `docs/snmp-plan.md` is the plan this implements; the wiki page
*Monitoramento e SNMP* is the operator's version, in Portuguese.

## Getting the MIB into the NMS

The box's own objects are defined in **PRISMA-ROUTING-MIB**, served with SNMP
on and nothing else configured, and the box prints the file with its root
filled in:

```
show snmp mib
```

Save it as `PRISMA-ROUTING-MIB.txt` and load it in the NMS beside the standard
modules it imports (SNMPv2-SMI, SNMPv2-TC, SNMPv2-CONF), which every NMS
already has.

**The root is the product's number, provisionally 32473** (#508). The
product's own IANA Private Enterprise Number was applied for on 2026-09-29;
until it arrives the MIB is served under IANA's "Example Enterprise Number for
Documentation Use" (RFC 5612), which belongs to nobody. The number first
proposed, 666, belongs to Oki Electric Industry: an object in another
organisation's subtree is read by an NMS with that organisation's MIBs loaded
as that organisation's device's. When the product's number arrives it
replaces 32473 in one place, and every box moves with it - the default is not
written into a saved configuration, so no box pins the old one. The objects
are numbered from the root, so nothing under it moves either; an NMS reloads
the file `show snmp mib` then prints.

An operator may still name a number of its own with
`snmp enterprise-number <number>`, and `no snmp enterprise-number` goes back
to the product's.

## Zabbix (#514)

A Zabbix 7.0 template covers everything below: the dataplane's interfaces,
the CGNAT's totals, pools and workers, the connection records, HA, LACP, BGP,
OSPF, OSPFv3, IS-IS, the FIB's size, the control plane's CPU, memory and
disks, the box's own state - the tuning in effect, the boot self-test, the
configuration's commits and confirm window, the configuration trail's head
(#524) - and the box's notifications. The box prints it with its enterprise
number filled in, the same way it prints the MIB:

```
show snmp zabbix-template
```

Save the output as `prisma-routing-zabbix.json` and import it in Zabbix
(*Data collection* > *Templates* > *Import*). It is JSON, which Zabbix imports
exactly as it does YAML. Then give the host an SNMP interface - v2c with an
`snmp community` of the box, or v3 with an `snmp user` - and link
**Prisma Routing by SNMP**. v1 is refused by the box (#511).

**The macros** an operator may want to set on the host:

| Macro | Default | What it decides |
|---|---|---|
| `{$PRISMA.PEN}` | the box's number | the root the product's objects are read under: filled in by `show snmp zabbix-template` |
| `{$IFCONTROL:"<ifName>"}` | 1 | 0 silences "link down" for an interface that may be down |
| `{$IF.UTIL.MAX}` | 90 | percent of ifHighSpeed that raises "above its speed" |
| `{$IF.ERRORS.WARN}` | 2 | errors per second that raise "has errors" |
| `{$NET.IF.IFNAME.MATCHES}`, `{$NET.IF.IFNAME.NOT_MATCHES}` | `.*`, `^(local0\|tap[0-9]+)$` | which interfaces are discovered |
| `{$PRISMA.POOL.UTIL.WARN}`, `{$PRISMA.POOL.UTIL.CRIT}` | 80, 95 | a pool's block utilisation that raises a warning, a high |
| `{$PRISMA.SESSIONS.FIT.WARN}`, `{$PRISMA.SESSIONS.FIT.CRIT}` | 200000, 20000 | sessions still fitting in the CGNAT's memory that raise a warning, a high |
| `{$PRISMA.FIB.DROP.PCT}`, `{$PRISMA.FIB.MIN}` | 50, 100 | the FIB falling below this percent of its hourly maximum raises an average, on a table larger than the minimum |
| `{$MEMORY.UTIL.MAX}` | 90 | the control plane's memory |
| `{$BGP4V2.INDEX.MATCHES}` | `^[0-9]+\.2\.` | BGP4V2-MIB peers discovered: IPv6 only, since BGP4-MIB has the IPv4 ones |
| `{$SNMP.TIMEOUT}` | 5m | no answer for this long is "no SNMP data collection" |

**What it alarms on** is the table in *What is worth an alarm* below, with
three decisions:

* **No trigger on CPU.** The dataplane's worker cores poll their cards at 100%
  by design; `hrProcessorLoad` is collected per core and not alarmed.
* **One alarm per event.** VRRP and the CGNAT are alarmed on the product's
  tables, which name the interface and the pool; the same routers and pools
  in VRRPV3-MIB and NATV2-MIB are collected without triggers. BGP is alarmed
  on BGP4-MIB for IPv4 and on BGP4V2-MIB for IPv6 only.
* **A shut interface is not a down one.** "Link down" needs ifAdminStatus up,
  and ifAdminStatus is polled as often as ifOperStatus: polled every five
  minutes, it still said up when an operator had just shut an interface, and
  the alarm fired - measured on the lab below.

**Notifications** arrive as SNMP trap items when the box sends them to a
Zabbix server or proxy running Zabbix's SNMP trap receiver
(`snmp trap-host <its address> community <string>`): one item per
notification in *Notifications*, matched by name or OID, and a fallback for
anything else.

`ipCidrRouteNumber` is not in the template: the box's net-snmp answers it
with noSuchObject, and `inetCidrRouteNumber` counts both families.

**What was verified, and on what.** The template was imported into Zabbix
7.0.31 through its API and polled against a lab box - VPP with a pool, pg0
carrying traffic, a VRRP router and a bundle, snmpd with the agent - over
v2c: every discovery rule found its rows and 106 of 107 items got values, the
one left being `ipCidrRouteNumber`, since dropped. Taking pg0 down sent
`linkDown`, `linkUp` and `vrrpStateChange` to Zabbix's trap receiver, each
landing in its own item, and raised and cleared the VRRP triggers. Not
verified against a live Zabbix: SNMPv3, and the routing tables, which that
lab box has no daemon for - `lab/routing.sh` and `lab/igp.sh` read their
OIDs from live daemons, and `test/test_zabbix_template.py` holds every OID in
the template to the module that defines it and to the view snmpd.conf grants.
`tools/zabbix-template.py` writes the file; it is regenerated, never edited.

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
| the CGNAT's pools, in the IETF's own model | NATV2-MIB (RFC 7659), aggregate | `.1.3.6.1.2.1.234` | the agent | 0.144.2 |
| the dataplane's VRRP routers | VRRPV3-MIB (RFC 6527) | `.1.3.6.1.2.1.207` | the agent | 0.144.3 |
| the LACP bundles and their members | IEEE8023-LAG-MIB (IEEE 802.1AX) | `.1.2.840.10006.300.43` | the agent | 0.144.4 |
| the dataplane's addresses, under IF-MIB's ifIndex; the box as a router | IP-MIB `ipForwarding`, `ipAddrTable`, `ipv6IpForwarding`, `ipAddressPrefixTable`, `ipAddressTable` | `.1.3.6.1.2.1.4.1`, `.20`, `.25`, `.32`, `.34` | the agent | 0.145.1 |
| BGP peers: state, AS, updates, last error | BGP4-MIB | `.1.3.6.1.2.1.15` | bgpd | 0.142.0 |
| OSPF neighbours, interfaces, LSDB | OSPF-MIB | `.1.3.6.1.2.1.14` | ospfd | 0.142.0 |
| OSPFv3 | OSPFV3-MIB | `.1.3.6.1.2.1.191` | ospf6d | 0.142.0 |
| IS-IS adjacencies, circuits | ISIS-MIB | `.1.3.6.1.2.1.138` | isisd | 0.142.0 |
| BGP peers of both families, IPv6 and unnumbered included | BGP4V2-MIB (draft-ietf-idr-bgp4-mibv2, experimental arc) | `.1.3.6.1.3.5.1` | bgpd | 0.144.5 |
| the FIB's size (not the FIB), both families | IP-FORWARD-MIB `inetCidrRouteNumber` (`ipCidrRouteNumber` is in the view and answers noSuchObject on Debian's net-snmp) | `.1.3.6.1.2.1.4.24.6` | net-snmp | 0.142.1 |

BGP4-MIB keys a peer on an IPv4 address, so an IPv6 session - and every
unnumbered one, which peers on a link-local address - has no row in it.
BGP4V2-MIB, which bgpd serves beside it (#521), keys its peer table on the
instance, the address type (1 IPv4, 2 IPv6) and the address, with no length
byte: `bgp4V2PeerState` for an IPv4 peer is
`.1.3.6.1.3.5.1.1.2.1.13.1.1.<a.b.c.d>`. The module is still an IETF draft,
so it sits under the experimental arc and is not in Debian's MIB set; an NMS
reads it by number or with the draft's module. `lab/routing.sh` reads an IPv4
and an unnumbered IPv6 peer established in it.

The route tables themselves are left out of the view on purpose: a full table
is about a million rows, and an NMS walking it would weigh on the box at every
poll.

## NATV2-MIB, RFC 7659 (#512)

The IETF's MIB for NAT and CGNAT, which an NMS that knows it reads with no
template of ours. **One NATV2 instance per pool**, because the filtering
behaviour is a pool's setting here and an instance is where RFC 7659 puts it.
Served at aggregate level only, by the maintainer's decision: no table with a
row per subscriber or per mapping.

| Table | Served | From |
|---|---|---|
| `natv2InstanceTable` (`.2.1`) | alias (the pool's name), port mapping behaviour endpointIndependent (measured by `lab/nat-behaviour.sh`), filtering (the pool's `filter`), pooling (paired, or arbitrary when `max-blocks` lets a subscriber hold blocks on several addresses), fragment behaviour fragmentInOrder (what `lab/frag.sh` measures), address map entries (subscribers holding a block), port map entries (ports in use), port map failure drops (refusals); thresholds -1 and limits 0 | the statistics segment and `show bpa config` |
| `natv2PoolTable` (`.2.3`) | one pool per instance: realm, IPv4, the port range, address and port map entries, port map failure drops | the same |
| `natv2PoolRangeTable` (`.2.4`) | the pool's outside prefixes, each as a begin and an end address | `show bpa config` |

**Not served**, so the compliance is partial: `natv2ProtocolTable`,
`natv2SubscriberTable`, `natv2AddressMapTable` and `natv2PortMapTable`; the
instance columns the box counts only box-wide - translations, creations,
fragment and resource drops - rather than put under one pool a number that
pool did not count; and the RFC's own notifications, whose thresholds read -1
(the product's are sent instead, #507). `lab/snmp.sh` walks the MIB loaded -
the RFC's module, in `test/mibs` - and compares the rows with the dataplane.

## VRRPV3-MIB, RFC 6527 (#513)

The IETF's VRRP MIB, over the virtual routers the dataplane runs. A row is
indexed by the card's ifIndex - the one IF-MIB gives it - the VRID and the
address family (1 IPv4, 2 IPv6).

| Table | Served | From |
|---|---|---|
| `vrrpv3OperationsTable` (`.1.1.1`) | master address (only while this box is master), primary address (the card's first own address), virtual MAC, status (initialize, backup, master), priority (the configured one), address count, advertisement interval in centiseconds, preempt and accept mode, row status active | `show vrrp vr`, `show interface address` |
| `vrrpv3AssociatedIpAddrTable` (`.1.2.1`) | the router's virtual addresses, each indexed by type, length and octets | `show vrrp vr` |

**Not served**: `vrrpv3OperationsUpTime`, the statistics group and the RFC's
notifications - the dataplane keeps no per-router counters or transition time;
a takeover is seen as `vrrpv3OperationsStatus` changing, as it is as
`prVrrpState` changing in PRISMA-ROUTING-MIB. Read-only, as everything here.
`lab/snmp.sh` reads pg0's router with the RFC's module loaded.

## IEEE8023-LAG-MIB, IEEE 802.1AX (#515)

IEEE's MIB for link aggregation, over the dataplane's LACP bundles. A bundle
and each of its members are indexed by the ifIndex IF-MIB gives the card.

| Table | Served | From |
|---|---|---|
| `dot3adAggTable` (`.1.1.1`) | per bundle: the actor's and the partner's system ID and priority, the keys, aggregate (always true), collector max delay 0 | `show lacp details`, from the bundle's first member |
| `dot3adAggPortTable` (`.2.1`) | per member: actor and partner system, priority, key, port number and port priority as the last LACPDU left them; the selected aggregator, and the attached one (0 while the member is not active); the actor's and partner's state as LacpState | `show lacp details`, `show bond details` |
| `dot3adAggPortStatsTable` (`.2.2`) | LACPDUs received and sent, marker PDUs received, illegal (bad LACP and marker PDUs) received | `show lacp details` |
| `dot3adAggPortDebugTable` (`.2.3`) | the receive machine's and the mux machine's states | `show lacp details` |

**Not served**: the partner's admin columns and the actor's admin state,
which VPP does not keep apart from the operational ones; the aggregator X
table and the port list; the conversation tables of 802.1AX-2014; DRNI; and
the churn and change counters. A member leaving the bundle is seen as
`dot3adAggPortAttachedAggID` going to 0 and `dot3adAggPortDebugMuxState`
leaving collectingDistributing. `lab/lacp.sh` reads the bundle with the
module loaded, against a Linux 802.3ad bond as the partner.

## IP-MIB, the dataplane's addresses (#522)

net-snmp's IP-MIB reads Linux, whose interfaces are the linux_cp taps: the
dataplane's addresses were in it, each under the tap's Linux ifIndex, which is
no row of the IF-MIB above. An NMS names an interface by joining the two, and
that join found nothing. These objects are served from the dataplane instead,
under the ifIndex IF-MIB gives the card, and registered above net-snmp's
module the way IF-MIB is - on each of ipAddrTable's five columns, which is
where its module registers them.

| Object | Served |
|---|---|
| `ipForwarding`, `ipv6IpForwarding` | forwarding (1): the box routes, whatever the host's own sysctl says |
| `ipAddrTable` (`.20`) | each IPv4 address of a card: its ifIndex, its mask, broadcast 1, reassembly 65535 |
| `ipAddressTable` (`.34`) | each address of both families, indexed by type, length and octets: ifIndex, unicast, a pointer to its prefix's row, manual, preferred, active, nonVolatile |
| `ipAddressPrefixTable` (`.32`) | each on-link prefix: manual, on-link, not autonomous, infinite lifetimes |

**Not served**: link-local IPv6 addresses (ipv6z, whose zone the dataplane
does not give), local0 and the linux_cp taps (the rule IF-MIB applies), and
the rest of IP-MIB - the statistics and neighbour tables, which are Linux's
and stay out of the view. `lab/snmp.sh` reads pg0's address under pg0's
ifIndex with the module loaded, and Linux's loopback absent.

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
new one. `prSystemDataplaneUptime` (`.1.12.4.0`, TimeTicks, 0.148.7, #534) is
how long the dataplane has run by VPP's own clock (`show clock`): younger than
`hrSystemUptime` when the dataplane restarted without the box, which the
Zabbix template raises as "the dataplane restarted without the box".

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

### The box's own state - `.1.16` to `.1.19` (#524)

Phase 4 of `docs/snmp-plan.md`: what the box says about itself. Each group is
left out whole - not served as zeros - when its source cannot be read, and
the first three are served while the dataplane is down too, since the box
whose dataplane did not come up is the one somebody reads them for.

| OID | Object | Type | What it is | From |
|---|---|---|---|---|
| `.1.16.1.0` | `prTuningState` | INTEGER | tuned(1), betweenPlans(2), firstBoot(3) | the header prisma-routing-tune writes into `/etc/vpp/startup.conf` |
| `.1.16.2.0` | `prTuningWorkers` | Gauge32 | worker threads this boot runs | `corelist-workers` in the same file |
| `.1.16.3.0` | `prTuningWorkersNextBoot` | Gauge32 | worker threads the next boot runs | the between-plans header, else the same |
| `.1.16.4.0` | `prTuningIsolated` | DisplayString | CPUs the kernel isolates now | `/sys/devices/system/cpu/isolated` |
| `.1.16.5.0` | `prTuningIsolatedWanted` | DisplayString | CPUs the tuner asked to isolate | `isolcpus=` in `/etc/default/grub.d/prisma-routing.cfg` |
| `.1.16.6.0` | `prTuningHugepages` | Gauge32 | hugepages of the tuner's size held now; absent when it asked none | `/sys/kernel/mm/hugepages/.../nr_hugepages` |
| `.1.16.7.0` | `prTuningHugepagesWanted` | Gauge32 | hugepages the tuner asked for | `hugepages=` in the grub file |
| `.1.17.1.0` | `prSelfTestVerdict` | INTEGER | ready(1), readyWithWarnings(2), notForwarding(3), failed(4) | `/run/prisma-routing/state/selftest`, written by the boot self-test |
| `.1.17.2.0` | `prSelfTestFailures` | Gauge32 | checks failed | the same |
| `.1.17.3.0` | `prSelfTestWarnings` | Gauge32 | warnings | the same |
| `.1.17.4.0` | `prSelfTestRanAt` | DisplayString | when it finished, UTC | the same |
| `.1.18.1.0` | `prConfigCommits` | Counter32 | configurations applied since boot: commits, the boot's load, rollbacks, reverts | `/run/prisma-routing/state/config`, written by the VTY |
| `.1.18.2.0` | `prConfigLastCommitAt` | DisplayString | when the last one took effect, UTC | the same |
| `.1.18.3.0` | `prConfigLastCommitBy` | DisplayString | the account; `boot` for the boot's load, `confirm-expired` for a revert | the same |
| `.1.18.4.0` | `prConfigConfirmPending` | TruthValue | a commit confirmed waits for its confirmation | the same |
| `.1.18.5.0` | `prConfigConfirmDeadline` | DisplayString | when it reverts, UTC; empty for none | the same |
| `.1.18.6.0` | `prConfigConfirmReverts` | Counter32 | commits confirmed nobody confirmed, reverted by the box | the same |
| `.1.18.7.0` | `prConfigRollbacks` | Counter32 | rollbacks to an earlier revision, committed | the same |
| `.1.18.8.0` | `prConfigSavedDiffers` | TruthValue | a reboot would come back without the changes since the last `save` | the same |
| `.1.19.1.0` | `prAuditState` | INTEGER | intact(1), empty(2), broken(3), truncated(4), incomplete(5) | `show cgnat history verify`'s check, asked of the dataplane at most every five minutes |
| `.1.19.2.0` | `prAuditEntries` | Gauge32 | chained entries checked | the same |
| `.1.19.3.0` | `prAuditHeadSeq` | Gauge32 | the last entry's sequence | the same |
| `.1.19.4.0` | `prAuditHeadDigest` | DisplayString | the last entry's SHA-256: kept by the NMS, the chain's head off the box | the same |
| `.1.19.5.0` | `prAuditLost` | Gauge32 | entries this dataplane could not write | the same |

**Why the state files.** The agent is Debian-snmp and must not read
`/etc/prisma-routing` (2770 root:prisma-cgnat): every revision there, the
confirm store and the saved file carry password hashes. So the self-test and
the VTY write a few lines with nothing secret in them into
`/run/prisma-routing/state` (2775 root:prisma-cgnat, from tmpfiles), each file
0644, and those are what the agent reads. `/run`, so they start empty at each
boot, the counters with them; the boot's own load is the first change. A box
where the self-test has not run, or no configuration has been applied since
boot, serves no `prSelfTest` or `prConfig` at all, which is what the absence
means. The files are written after the change they describe and never refuse
it: a commit that took effect is not reported as failed because monitoring
could not be written.

`lab/snmp.sh` reads all four groups by name with the MIB loaded: the tuner's
header between two plans, the real self-test's counts against its console, a
`commit confirmed` pending with its deadline and then confirmed, and the
trail's state.

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
| an OSPF neighbour changes state | `ospfNbrStateChange` (OSPF-MIB) | ospfd, over AgentX |
| a VRRP router changes state | `mteTriggerFired` named `vrrpStateChange`, with the interface, VR ID and state | a monitor on `prVrrpState` |
| a pool reaches 90% of its blocks | `poolNearlyFull`, with the pool's name and utilisation | a monitor on `prPoolUtilisation` |
| ports are refused | `portsRefused` | a monitor on `prPortRefusals` |
| a records transport connects or drops | `recordsTransportChange`, with the stream | a monitor on `prLogTransportConnected` |
| records are lost | `recordsLost`, with the stream | a monitor on `prLogTransportDropped` |
| a bundle's active members change | `bundleMembersChange`, with the bundle | a monitor on `prBundleActiveMembers` |
| an upgrade leaves the old dataplane forwarding | `dataplaneNotCurrent` | a monitor on `prSystemDataplaneCurrent` |
| the boot self-test failed | `selfTestFailed` | a monitor on `prSelfTestFailures` (#524) |
| a commit confirmed nobody confirmed was reverted | `configReverted` | a monitor on `prConfigConfirmReverts` (#524) |
| the tuning waits for a reboot | `tuningBetweenPlans` | a monitor on `prTuningState`, every five minutes (#524) |
| the configuration trail no longer verifies | `auditTrailDamaged` | a monitor on `prAuditState`, every five minutes (#524) |
| a disk, memory or load threshold | net-snmp's default monitors (`dskErrorFlag` and the rest) | net-snmp |

The monitors are polled every 30 or 60 seconds, so a notification follows the
event by up to a minute. They go under the enterprise number in effect, like
the objects they watch. `lab/snmp-traps.sh` receives the monitored ones with
snmptrapd - `recordsTransportChange` from a TCP collector connecting and going
away (#509) - and `lab/routing.sh` the BGP pair and `ospfNbrStateChange`. Not
received in a lab: `recordsLost`, which needs the spill file to fail, and
`bundleMembersChange`, which needs a bundle with a partner. `selfTestFailed`
is received too (#524); `configReverted`, `tuningBetweenPlans` and
`auditTrailDamaged` are not, the first needing a window to expire and the
other two polled every five minutes - their objects are read in
`lab/snmp.sh`.

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
| `prTuningState` not tuned, or `prTuningHugepages` below `prTuningHugepagesWanted` | the dataplane runs smaller than planned until a reboot |
| `prSelfTestVerdict` failed | a check at boot failed; the console says which |
| `prConfigConfirmReverts` rising | a change made and lost, usually because it cut the session it was made from |
| `prConfigSavedDiffers` true for long | a reboot would come back without the changes |
| `prAuditState` 3 or more, or `prAuditHeadDigest` other than the one the NMS kept | the configuration trail was altered, cut or rewritten |

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
| the tuning in effect, the boot self-test, commits and the confirm window, the configuration trail | PRISMA-ROUTING-MIB | served (#524) |
| static forwards | `show cgnat forward` | not over SNMP |
| notifications | traps or informs, per destination | served (#507) |
