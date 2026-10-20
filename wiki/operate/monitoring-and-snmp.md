# Monitoring and SNMP

What your NMS sees of this CGNAT, and what needs to be done for the most useful half to show up. For whoever looks after Zabbix, LibreNMS or equivalent.

Every ISP already has an NMS, and that is where someone actually looks. A CGNAT whose only observability is someone logged into the box itself is a CGNAT nobody follows until the customer calls.

# Enabling the agent

```
configure
 snmp enabled yes
 snmp listen-address 10.0.0.1
 snmp port 161
 snmp enterprise-number 65535
 snmp community <read-only-community>
 snmp-user nms
  auth sha <passphrase>
  priv aes <passphrase>
  exit
 commit
 exit
save
```

| Line | What it does for you | If left out |
|------|----------------------|-------------|
| `snmp enabled yes` | turns the agent on   | the agent stays off, which is the default |
| `snmp listen-address` | the address the agent answers on | the agent listens only on `127.0.0.1`, and no outside NMS reaches it |
| `snmp port` | the UDP port         | `161`       |
| `snmp enterprise-number` | the number under which the Prisma MIB is served (see below) | the product's number applies |
| `snmp community` | an SNMPv2c community, read-only | only v3 queries |
| `snmp-user` | an SNMPv3 user, with `auth <sha\|md5>` and `priv <aes\|des>` | only v2c queries |

Any of these lines goes back to the default with `no` in front — `no snmp enterprise-number` goes back to the product's number, `no snmp listen-address` goes back to `127.0.0.1` (from version 0.143.2 on).

`commit` refuses an agent turned on with no community and no v3 user: it would be an agent nobody can query. And the agent is checked by its **effect**: the file is written, the service restarts, and if it does not come up with the new configuration the box puts back the previous one and says so at `commit`.

From version 0.144.1 on the box answers **only on SNMPv2c and SNMPv3**: an SNMPv1 query, even with the right community, gets no answer. v1 does not carry 64-bit counters, so the dataplane ports and the Prisma MIB counters would not even show up for it.

The SNMP configuration carries the communities, so only **root** reads it. From version 0.147.4 on, a session that cannot read it (an administrator who is not root, for example) warns below `show running-config` that the `snmp` lines are those of the last saved configuration, and not the box's. That session does not change SNMP and only writes a `save` with the SNMP from the saved configuration — never turned off. Before that, a `save` done in the VTY turned SNMP off at the next boot and erased the community.

The v3 user is created with read-only access and requires authentication **and** privacy: configure both lines, `auth` and `priv`. `commit` also works inside the `snmp-user` block, without needing to leave it.

The community and the v3 passphrases are passwords that travel over the network, and they are treated as such: `show running-config` shows `<set, not shown>` in their place.

# Read-only, twice

The agent configuration file the box generates contains only `rocommunity` and `rouser`, and **never** `rwcommunity`. On top of that, the agent itself answers every SET with `not-writable`.

Configuration belongs to the VTY, where it is validated, committed and reversible. An SNMP SET that reconfigures a CGNAT is an outage one typo away.

# No stale numbers

If the dataplane is not responding, the agent serves **nothing** instead of the values it last saw.


:::info
A monitoring system that keeps reporting yesterday's numbers during an outage **hides** the outage; one that reports a gap shows it. That is why the liveness check runs on **every** query, before the cache is consulted. The numbers themselves are cached for ten seconds: querying faster than that returns the same value.

:::

# What is served

## The box, the processor, the memory and the disks

From version 0.141.3 on, the NMS also reads what the box's own net-snmp already knows how to answer — before, this was out of view, and a query to the box did not even return its name:

| Group | OID | What it returns |
|-------|-----|-----------------|
| system (SNMPv2-MIB) | `.1.3.6.1.2.1.1` | `sysDescr` with the product and the installed version, `sysUpTime`, `sysName` |
| HOST-RESOURCES-MIB | `.1.3.6.1.2.1.25` | processors, memory and storage, running processes |
| UCD-SNMP-MIB | `.1.3.6.1.4.1.2021` | load, memory, and the disk table (`dskTable`) |

The disk table already comes with two thresholds: root, which warns with less than 10% free, and **the retention log filesystem** (`/var/log/prisma-routing`), which warns with less than 15% free. That is the one that matters: a full disk there is the log that keeps a year of connection records ceasing to write. In the NMS, alert on `dskErrorFlag` (`.1.3.6.1.4.1.2021.9.1.100`) equal to 1.

A worker core always shows 100% load: it does not sleep, it keeps polling the card. The real dataplane load is not that — it comes in a later SNMP stage.

## The dataplane ports, through the IF-MIB

From version 0.141.5 on, the box's `ifTable` and `ifXTable` are the **dataplane** ports — the ones that carry subscribers —, and not the ones Linux sees. Before, an NMS walking the IF-MIB saw only `lo` and the management card.

| Object | What it returns |
|--------|-----------------|
| `ifName`, `ifDescr` | the port name, the same as in the VTY |
| `ifAlias` | the `description` configured in the VTY |
| `ifHighSpeed`, `ifSpeed` | the link speed, as `show hardware-interfaces` reports it |
| `ifAdminStatus`, `ifOperStatus` | enabled in the configuration, and with link |
| `ifHCInOctets`, `ifHCOutOctets` and the packet ones | the 64-bit counters, summing all dataplane threads |

All dataplane ports are included, sub-interfaces too, and the physical cards that stay with Linux, such as the management one. The internal ones are left out: `local0` and the `tap` interfaces that link the dataplane to routing.

Each port's `ifIndex` is stored by name, so a port **keeps the same row** when the dataplane restarts — the NMS does not mix one port's history with another's.

## Routing: BGP, OSPF and IS-IS

From version 0.142.0 on, routing sessions appear in the standard MIBs, served by the very daemons that run them — what the NMS reads is the daemon's state, not a copy of it:

| MIB | OID | What it returns |
|-----|-----|-----------------|
| BGP4-MIB | `.1.3.6.1.2.1.15` | each neighbor: session state, remote AS, updates, last error |
| OSPF-MIB | `.1.3.6.1.2.1.14` | neighbors and their states, interfaces, LSDB size |
| OSPFV3-MIB | `.1.3.6.1.2.1.191` | the same, for OSPFv3 |
| ISIS-MIB | `.1.3.6.1.2.1.138` | adjacencies and circuits |
| BGP4V2-MIB | `.1.3.6.1.3.5.1` | each BGP neighbor, **IPv4 and IPv6** (including *unnumbered* ones, via link-local): state, remote AS, errors and prefix counters per family — from 0.144.5 on |

What is worth alerting on: `bgpPeerState` (`.1.3.6.1.2.1.15.3.1.2.<neighbor address>`) other than 6 (*established*), `ospfNbrState` other than 8 (*full*) and `isisISAdjState` other than 3 (*up*). The same goes for OSPFv3: `ospfv3NbrState` (`.1.3.6.1.2.1.191.1.9.1.8`) other than 8. All four were checked by the product's tests on a real session (the OSPFv3 one from version 0.142.1 on). The BGP4-MIB only knows IPv4 neighbors; for an IPv6 neighbor use `bgp4V2PeerState` (`.1.3.6.1.3.5.1.1.2.1.13.1.<type>.<address>`, type 1 = IPv4, 2 = IPv6), which must also be 6.

From version 0.142.1 on, the routing table size also answers: `inetCidrRouteNumber` (`.1.3.6.1.2.1.4.24.6.0`) counts IPv4 and IPv6 routes. (`ipCidrRouteNumber`, the old IPv4-only version, is not answered by the box's net-snmp.) The table itself is left out on purpose: with the full internet table it is close to a million rows, and an NMS walking it would weigh on the box at every query. A sudden drop in this number is a session that went down taking its routes with it.

Nothing needs to be configured beyond `snmp enabled yes`: the commit turns on the link between the routing daemons and the agent.


:::info
**When SNMP is turned off**, the routing daemons keep looking for the agent until the next time FRR restarts — FRR does not allow undoing this link while it is running — and `commit` warns about it. In that interval the FRR log records a connection attempt every 15 seconds per daemon. At the next FRR start (a reboot, for example) the link goes away.

:::

## Timeouts, through RFC 4008

`natDefTimeouts` at `.1.3.6.1.2.1.123.1.1` — six objects that map exactly onto this box's timeouts, so an existing NMS reads them without a custom template.

| OID under `.1.3.6.1.2.1.123.1.1` | RFC 4008 object | This box's timeout |
|--------------------------------|-----------------|--------------------|
| `.1.0`                         | `natBindDefIdleTimeout` | `port-holddown`    |
| `.2.0`                         | `natUdpDefIdleTimeout` | `udp`              |
| `.3.0`                         | `natIcmpDefIdleTimeout` | `icmp`             |
| `.4.0`                         | `natOtherDefIdleTimeout` | `gre`              |
| `.5.0`                         | `natTcpDefIdleTimeout` | `tcp-established`  |
| `.6.0`                         | `natTcpDefNegTimeout` | `tcp-transitory`   |

The rest of RFC 4008 is per interface or per *binding*: this box tracks neither of these, and per *binding* would mean walking the session table, something no `show` here is allowed to do.

## CGNAT in the IETF standard MIB: NATV2-MIB

From version 0.144.2 on the box also serves the **NATV2-MIB** (RFC 7659, `.1.3.6.1.2.1.234`), the MIB the IETF defined for NAT and CGNAT — an NMS that already knows it reads the pools without a template from us. Each pool appears as a NAT instance, with its name, filtering type, port range, subscribers and ports in use right now, refused ports and public address ranges.

Only the aggregate numbers: there is no table with one row per subscriber or per session, which on a production box would be millions of rows. For the details the RFC does not cover — guaranteed capacity, occupancy, load per worker — the Prisma MIB, below, applies.

## VRRP in the IETF standard MIB: VRRPV3-MIB

From version 0.144.3 on the box also serves the **VRRPV3-MIB** (RFC 6527, `.1.3.6.1.2.1.207`) for the dataplane's VRRP virtual routers. Each router is a row indexed by the interface's ifIndex (the same as in the IF-MIB), by the VRID and by the address family (1 = IPv4, 2 = IPv6), with the state (initializing, backup, master), the priority, the virtual MAC, the advertisement interval, the preempt and accept modes and the virtual addresses.

To alert on a master change, follow `vrrpv3OperationsStatus`: the dataplane keeps no per-router counters, so the RFC's statistics table and notifications are not served.

## LACP in the IEEE standard MIB: IEEE8023-LAG-MIB

From version 0.144.4 on the box also serves the **IEEE8023-LAG-MIB** (IEEE 802.1AX, `.1.2.840.10006.300.43`) for the dataplane's LACP bundles. Each bundle and each member appears by the interface's ifIndex (the same as in the IF-MIB): the partner's system and priority, the keys, which bundle each member is attached to, the LACP state on both sides, the LACPDUs received, sent and invalid, and the state of the receive and mux machines.

To alert when a member leaves the bundle, follow `dot3adAggPortAttachedAggID` (goes to 0) or `dot3adAggPortDebugMuxState` (stops being `collectingDistributing`). The partner's administrative values, the conversation tables and DRNI are not served: the dataplane does not have them.

## Interface addresses: IP-MIB

From version 0.145.1 on the **IP-MIB** address tables (`ipAddrTable`, `ipAddressTable` and `ipAddressPrefixTable`) list the addresses of the dataplane interfaces, IPv4 and IPv6, **with the same ifIndex as the IF-MIB**. It is through that link that the NMS (Zabbix, for example) discovers which address is on which interface. Before, it saw the addresses of the Linux mirror interfaces, with a numbering that matched no IF-MIB port.

`ipForwarding` and `ipv6IpForwarding` answer 1 (*forwarding*): the box is a router. IPv6 link-local addresses are left out.

## Inventory and sensors: ENTITY-MIB and ENTITY-SENSOR-MIB

The box also serves the **ENTITY-MIB** (RFC 6933, `.1.3.6.1.2.1.47`) and the **ENTITY-SENSOR-MIB** (RFC 3433, `.1.3.6.1.2.1.99`), which every NMS reads without a template: the chassis, each port that has a cage, the module inside it and the module's diagnostics (DDM) as sensors. They are the same readings as `prTransceiver` and `show interface transceiver`, decoded by the same code.

The index (`entPhysicalIndex`) comes from the **port's ifIndex in the IF-MIB**, which does not change with a reboot or a new card — so a sensor's history in the NMS is not lost either:

| What | `entPhysicalIndex` |
|------|------------------|
| the chassis | 1                |
| the port | ifIndex × 1000   |
| the module in the port | ifIndex × 1000 + 1 |
| module temperature and voltage | \+ 2 and + 3     |
| bias, TX and RX of lane *n* | \+ 10*n* + 1, + 2 and + 3 |

On a port with ifIndex 1, for example: port 1000, module 1001, temperature 1002, voltage 1003, bias 1011, TX 1012 and RX 1013. The module carries manufacturer, model (*part number*), serial number, revision and manufacturing date (`entPhysicalMfgName`, `entPhysicalModelName`, `entPhysicalSerialNum`, `entPhysicalHardwareRev`, `entPhysicalMfgDate`), and `entAliasMappingIdentifier` links each port to its ifIndex.

The sensors use the RFC's units: temperature in hundredths of a degree, voltage in tenths of a millivolt, bias in microamperes, and **optical power in watts** — tenths of a microwatt, the module's own resolution. RFC 3433 has no dBm (the `dBm` some NMSs know comes from a Cisco MIB); in dBm, the power remains in `prXcvrLaneRxPower` and `prXcvrLaneTxPower`. The **module's own thresholds** are in the Prisma MIB, in `prSensorThresholdTable` (`.1.21`), with the same index and the same unit as the sensor, and `prSensorLevel` says where the reading is relative to them. RFC 3433 has no thresholds: it tells the NMS to write its own, and this box is read-only — and the thresholds are the ones written in the module.

**A card that does not read modules does not appear** — virtio, a TAP: there is no cage to show. An empty cage appears as a port without a module, and a module without DDM or a copper one appears without sensors. When a module is inserted, removed or replaced (another serial number in the same cage), `entLastChangeTime` changes and the box sends the ENTITY-MIB's own `entConfigChange` notification.

## The Prisma MIB: CGNAT, connection records and the box

What **has no standard OID** — the CGNAT pools, the drops, the transport of connection records, the version the dataplane is running — is in the **PRISMA-ROUTING-MIB**, the product's own MIB. From version 0.143.4 on it is served without any configuration beyond turning SNMP on, and the box itself hands out the ready file, with the number already filled in:

```
show snmp mib
```

Save the output as `PRISMA-ROUTING-MIB.txt` and load it into the NMS: from then on, instead of numbers, the NMS shows the names — `prPoolUtilisation`, `prLogTransportConnected` and so on.


:::info
**The number is Prisma's at IANA: 67033.** Up to 0.149.12 the MIB sat under 32473, which IANA reserves for documentation and examples (RFC 5612), because the product did not have its own number yet. Since 0.149.13 it sits under 67033, with nothing to configure, and nothing below it moved: whoever queries `.1.3.6.1.4.1.32473` now queries `.1.3.6.1.4.1.67033`, with the same numbers below, and it is enough to load again into the NMS the file `show snmp mib` prints. Whoever prefers can use their own organization's number with `snmp enterprise-number <number>`; `no snmp enterprise-number` goes back to the product's.

:::

### The box totals

At the same OIDs as always, so an already configured NMS keeps working:

| OID under `.1.3.6.1.4.1.<PEN>.1` | Object | Meaning |
|--------------------------------|--------|---------|
| `.1.0`                         | `prSubscribers` | subscribers holding a block right now |
| `.2.0`                         | `prPortsInUse` | ports allocated, summing all of them |
| `.3.0`                         | `prPortsHeld` | ports contained in the blocks currently held |
| `.4.0`                         | `prSubscribersNearFull` | subscribers at 90% or more of their block |
| `.5.0`                         | `prPortRefusals` | ports refused since the dataplane came up |
| `.6.0`                         | `prPacketsDropped` | packets CGNAT did not let through since it came up |


:::info
**From version 0.143.0 on,** `**.1.0**` **to** `**.4.0**` **arrive as Gauge, and** `**.5.0**` **and** `**.6.0**` **as Counter** — before, all six arrived as Counter, and an NMS that computes rates drew each decrease as a false "counter wrap" spike. If your NMS stored these items as counters, change them to gauges. And `.5.0` is now a real counter: it sums every refusal since the dataplane came up, and not only those of subscribers still holding a block.

:::

### The pools

One row per pool, in `prPoolTable` (`.10.1.1.<column>.<pool>`):

| Object | What it returns |
|--------|-----------------|
| `prPoolName`, `prPoolMode` | the name and the mode: deterministic or dynamic |
| `prPoolBlockSize`, `prPoolInsideAddresses`, `prPoolOutsideAddresses` | the block size and how many subscriber and public addresses the pool has |
| `prPoolBlocksTotal`, `prPoolCapacity`, `prPoolGuaranteed` | how many blocks the pool hands out, how many subscribers it holds at the ceiling and how many it **guarantees** on any worker — this is the one you size by |
| `prPoolSubscribers`, `prPoolBlocksHeld`, `prPoolUtilisation` | how many subscribers and blocks are in use right now, and the percentage of blocks handed out |
| `prPoolSessions`, `prPoolPortsInUse`, `prPoolSubscribersNearFull` | open sessions, allocated ports and subscribers at 90% or more |
| `prPoolRefusals` | ports refused since the pool was created |

The drops are in `prDropTable` (`.10.2`), one row per reason, with the node and the reason in the same words as `show cgnat drops`; the box's session total, in `prSessions` (`.10.3.0`).

### Connection records and the box

| Object | What it returns |
|--------|-----------------|
| `prLogIpfixEnabled`, `prLogIpfixRecords`, `prLogIpfixDropped` | IPFIX over UDP: enabled, records emitted, and events with no collector to go to |
| `prLogTransportTable` (`.11.10`) | the reliable streams — IPFIX over TCP (1) and syslog (2): enabled, **connected**, records, sent, spilled to the spill file, **lost**, and how many times it reconnected |
| `prSystemRelease`, `prSystemDataplaneBuild` | the installed version and that of the dataplane that is running |
| `prSystemDataplaneCurrent` | whether the two are the same. **False after an upgrade, until the next reboot**: the box keeps forwarding with the old version, and a `commit` that needs the new one is refused |

### Capacity, workers, HA, LACP and identity

From version 0.143.1 on:

| Object | What it returns |
|--------|-----------------|
| `prSessionsFit` | how many sessions still fit in CGNAT memory — the same number as `show cgnat memory`. At zero, new sessions are refused |
| `prWorkerTable` | subscribers, sessions and ports of each dataplane thread: a worker far ahead of the others is the warning sign of a subscriber refused while blocks are left over on another worker |
| `prVrrpTable` | each VRRP router: interface, VR ID, the priority advertised right now and the state — `master`, `backup`, `init`. A state change is a role switch |
| `prBundleTable` | each LACP bundle: how many members are configured and how many are **carrying traffic** |
| `prIdentityEntries`, `prIdentityReadAt` | how many subscribers the identity table names, and when it was read |
| `prRadiusReceived`, `prRadiusApplied`, `prRadiusRefused` | the RADIUS accounting coming from the BNG. A refusal is usually a different secret between the BNG and the box |

The CGNAT numbers are maintained by the dataplane itself at the moment they change, and the agent reads everything at once: querying the box does not cost a sweep of the subscribers, however large it is.

### The box's own state: tuning, self-test, commits and trail

From version 0.147.1 on:

| Object | What it returns |
|--------|-----------------|
| `prTuningState` | `tuned` (the kernel has what the tuner asked for), `betweenPlans` (the box runs with fewer workers than planned until the next reboot) or `firstBoot` (conservative configuration until the reboot) |
| `prTuningWorkers`, `prTuningWorkersNextBoot` | workers now and at the next boot |
| `prTuningIsolated`, `prTuningIsolatedWanted` | CPUs isolated now and those the tuner asked for |
| `prTuningHugepages`, `prTuningHugepagesWanted` | hugepages reserved now and those requested. Fewer than requested means dataplane memory smaller than planned |
| `prSelfTestVerdict` | the boot self-test verdict: `ready`, `readyWithWarnings`, `notForwarding` (healthy, but with no interface to forward on) or `failed` |
| `prSelfTestFailures`, `prSelfTestWarnings`, `prSelfTestRanAt` | failures, warnings and when it ran |
| `prConfigCommits` | configurations applied since boot (commits, the boot load, rollbacks, reverts) |
| `prConfigLastCommitAt`, `prConfigLastCommitBy` | when and who applied the last one — `boot` for the boot load, `confirm-expired` for a revert |
| `prConfigConfirmPending`, `prConfigConfirmDeadline` | a `commit confirmed` waiting for confirmation, and when the box reverts if nobody confirms |
| `prConfigConfirmReverts` | `commit confirmed` that nobody confirmed and the box reverted — almost always a change that dropped the session of whoever made it |
| `prConfigRollbacks` | rollbacks to a previous revision |
| `prConfigSavedDiffers` | the configuration in use is not saved: a reboot would come back without the changes. A `save` is missing |
| `prAuditState` | the configuration trail: `intact`, `empty`, `broken` (entry altered or removed from the middle), `truncated` (removed from the end) or `incomplete` (writes that failed) |
| `prAuditEntries`, `prAuditHeadSeq`, `prAuditHeadDigest`, `prAuditLost` | the entries, the last one and its SHA-256, and the lost ones. **Keep the digest in the NMS**: a trail rewritten from scratch still checks out, and only the head kept outside the box shows the difference |

Tuning, self-test and commits keep answering with the dataplane stopped — it is precisely the box whose dataplane did not come up that needs them. The counters restart at every boot. A group the box does not have yet (the self-test has not run, no configuration applied since boot) simply does not appear, instead of appearing as zero.

### The ports' optical modules

From version 0.151.15 on, `prTransceiver` carries each port's module — what `show interface transceiver` shows, decoded by the same code. Two tables, under the **port's ifIndex in the IF-MIB**: one per module and one per optical lane (one on an SFP, four on a QSFP).

| Object | What it returns |
|--------|-----------------|
| `prXcvrStatus` | `ddm` (module with diagnostics), `noDdm`, `copper`, `absent` (empty cage) or `unreadable` (the driver does not read modules) |
| `prXcvrVendor`, `prXcvrPartNumber`, `prXcvrSerialNumber`, `prXcvrWavelength` | the identity; a new serial number is a replaced module |
| `prXcvrTemperature`, `prXcvrVoltage` | in hundredths of a degree and in millivolts, with the module's four thresholds alongside |
| `prXcvrState` | the worst level of any of the module's readings: `normal`, `warning`, `alarm`, or `unknown` when it does not store thresholds |
| `prXcvrLaneRxPower`, `prXcvrLaneTxPower` | in hundredths of a dBm, with the thresholds alongside and the level of each (`prXcvrLaneRxLevel`). **No light (0 mW) is -4000**, -40.00 dBm |
| `prXcvrLaneBias` | the laser current, in microamperes |

A value the module does not provide, and the thresholds of a module that has none, **do not appear** — they do not appear as zero. The same modules are also in the ENTITY-MIB and the ENTITY-SENSOR-MIB, described above; this table stays, in dBm, the unit the switch on the other side shows, and it is where the Zabbix template's optical alarms are.

Up to version 0.155.2, an `snmpget` of this table and of `prSensorThresholdTable` answered *No Such Instance*, although `snmpwalk` worked — and Zabbix queries with GET, so the template's optical items had no value. From 0.155.3 on both answer GET; the upgrade adjusts `snmpd.conf` and restarts snmpd on its own.

### PPPoE: sessions, RADIUS servers and CoA

From version 0.155.3 on, `prBng` (`.1.22`) carries the PPPoE control plane — the same accel-ppp `show stat` that `show pppoe summary` reads, with the counters the box adds to it.

| Object | What it returns |
|--------|-----------------|
| `prBngState` | `running`, `stopped` (PPPoE configured and the control plane stopped: nobody can dial in), `notConfigured`, or `noContainers` (a box that runs the dataplane as systemd units) |
| `prBngUptime` | how long the control plane has been up: the counters below count from then |
| `prBngSessionsActive`, `prBngSessionsStarting` | sessions up, and sessions negotiating. `prBngSessionsActive` is always `prBngSessionsStarted` minus `prBngSessionsStopped` |
| `prBngSessionsStarted`, `prBngSessionsStopped` | sessions that came up, and that went down after coming up |
| `prBngCoaAcks`, `prBngCoaNaks`, `prBngDisconnectAcks`, `prBngDisconnectNaks` | the CoA and Disconnect requests (RFC 5176) answered with ACK and with NAK |
| `prBngDaeDropped` | the CoA or Disconnect requests that got no answer at all: a source that is not a `dynamic-authorization client`, or a wrong secret |
| `prBngRadiusTable` (`.1.22.20`) | one row per `aaa radius` server, indexed by address: the state (`active` or `failed`, during the `dead-time`), the Access-Requests sent, the **Access-Accepts**, the **Access-Rejects**, those with no answer within the `timeout`, and the same for accounting |

The counters **restart from zero when the control plane restarts** — a commit that changes the `pppoe` block, or a crash —, and `prBngUptime` shows when that was. On a box **without PPPoE**, `prBngState` says `notConfigured`, the counters stay at zero and the server table stays empty: the box answers, it does not error. An Access-Reject is the server's answer — wrong password, unknown or suspended subscriber —, that is why the template collects it and does not alarm on it; what alarms is a server not answering.

### The PPPoE shaper: each session's queue

`prBngShaper` (`.1.22.30`) carries the box's totals of the per-session PPPoE queues — the shaper that, by default, enforces the download plan (`rate-limit download shaper|policer` under `pppoe`).

| Object | What it carries |
|--------|-----------------|
| `prBngShaperDownLimiter`, `prBngShaperUpLimiter` | what enforces the plan each way: `shaper` (past the plan waits in the queue), `policer` (past the plan is dropped), or `notConfigured` without PPPoE |
| `prBngShaperDownSessions`, `prBngShaperUpSessions` | sessions whose download, and upload, go through the shaper now |
| `prBngShaperQueues` | queues with packets waiting now: subscribers sending more than their plan at this moment |
| `prBngShaperQueuedPackets`, `prBngShaperQueuedOctets` | packets, and bytes, waiting in every queue now |
| `prBngShaperPassed`, `prBngShaperDelayed` | packets within the plan, passed at once, and past it, that waited and left |
| `prBngShaperDroppedQueueFull` | dropped with the session's queue full: the subscriber went past the plan for longer than the queue holds — what the plan is for |
| `prBngShaperDroppedCodel` | dropped by CoDel: how a TCP connection is told to slow to the plan |
| `prBngShaperDroppedBoxBuffers` | dropped because the queues together already held every buffer the box sets aside for them: the box, not the plan |
| `prBngShaperOctets` | bytes sent through the shaper |
| `prBngShaperBuffersHeld`, `prBngShaperBuffersCap` | dataplane buffers the queues hold now, and the most they may hold: a quarter of the total, split over the workers |

The counters are the whole box's, both directions together, and **do not go back when a session ends**: the dataplane adds what the session counted to a total before it removes it. They restart from zero only when the dataplane restarts (`prSystemDataplaneUptime` shows when) — a restart of the PPPoE control plane does not reset them. A single session has no row in SNMP: with 16,000 sessions, a table per session would cost the agent about a second per request. For one session, use `show pppoe session <id>`.

## Zabbix: the ready-made template

From version 0.146.0 on the box hands out a **template for Zabbix 7.0** that monitors everything on this page: the dataplane ports, the totals, the CGNAT pools and workers, the connection records, VRRP, LACP, BGP, OSPF, OSPFv3, IS-IS, the FIB size, CPU, memory and disks, the box's own state (tuning, self-test, commits and trail, from 0.147.1 on), the ports' optical modules (from 0.151.15 on), PPPoE and its RADIUS servers (from 0.155.3 on), and the notifications the box sends. It comes out of the box itself, with the Prisma MIB number already filled in:

```
show snmp zabbix-template
```

Save the output as `prisma-routing-zabbix.json` and import it into Zabbix, under *Data collection* > *Templates* > *Import*. The file is JSON, which Zabbix imports the same way as YAML. On the host, create an SNMP v2c interface with one of the box's `snmp community` values, or v3 with an `snmp user`, and link the **Prisma Routing by SNMP** template. SNMPv1 is refused by the box.

The alarms come ready: port administratively up and operationally down, port above 90% of its speed or with errors, pool above 80% and 95% of its blocks, subscribers close to running out of ports, refused ports, CGNAT memory running out, BGP neighbor out of *established*, OSPF neighbor that does not reach *full*, IS-IS adjacency out of *up*, VRRP state change, LACP bundle with fewer active members than configured, the FIB losing half its routes in one hour, connection records lost or transport disconnected, dataplane running the old version after an upgrade, and the box not answering SNMP. The thresholds are template macros, which you adjust on the host: `{$IF.UTIL.MAX}`, `{$PRISMA.POOL.UTIL.WARN}`, `{$PRISMA.POOL.UTIL.CRIT}`, `{$PRISMA.SESSIONS.FIT.WARN}` and the rest. For a port that may be down without an alarm, use `{$IFCONTROL:"<port name>"}` = 0.

The optical modules (0.151.15) have their own: RX power below the low warning or above the high warning **of the module itself**, high temperature, and any reading beyond an alarm threshold. `{$PRISMA.XCVR.RX.MIN}` and `{$PRISMA.XCVR.RX.MAX}` (dBm) and `{$PRISMA.XCVR.TEMP.MAX}` (°C) fire alongside the module's thresholds — to watch a level the module does not watch —, and `{$PRISMA.XCVR.CONTROL:"<port>"}` = 0 silences a port (a QSFP lane is `"<port> lane <n>"`).

PPPoE (0.155.3) has its own: control plane configured and stopped, control plane restarted on its own, sessions up dropping below `{$PRISMA.PPPOE.DROP.PCT}` (50%) of the last hour's maximum — only above `{$PRISMA.PPPOE.MIN}` (10) sessions —, RADIUS server marked as down, RADIUS server not answering Access-Requests for five minutes, and CoA or Disconnect requests answered with nothing.

The PPPoE shaper has its own: packets dropped for want of the box's buffers (`prBngShaperDroppedBoxBuffers`) above `{$PRISMA.SHAPER.BOX.DROPS.MAX}` (0) a second for five minutes, a high; and the queues holding more than `{$PRISMA.SHAPER.BUFFERS.WARN}` (80%) of the buffers they may hold for five minutes, a warning. The template also collects what enforces each direction, the shaped sessions, the queues, and the packets passed, delayed and dropped a second.

Three choices made by the template:

* **No CPU alarm.** The dataplane worker cores sit at 100% all the time, by design. Each core's load is collected, but does not raise an alarm.
* **One alarm per event.** VRRP and the pools raise alarms through the Prisma MIB, which gives the interface and pool name. The same information in the VRRPV3-MIB and the NATV2-MIB is only collected, and the same goes for the ENTITY-SENSOR-MIB sensors: the optical alarms are those of `prTransceiver`. From the ENTITY-MIB comes only an informational notice, of a replaced module. BGP raises alarms through the BGP4-MIB for IPv4 neighbors and through the BGP4V2-MIB only for IPv6 ones.
* **A port shut down by the operator is not a port that is down.** The port-down alarm only fires with the port administratively up.

To receive the **notifications** in Zabbix, point the box at a server or proxy with the Zabbix trap receiver: `snmp trap-host <address> community <community>`. Each notification in the table in the next section lands in its own item, and whatever is not recognized lands in a catch-all item. From 0.152.3 on, a `bfdSessDown` also opens an alarm, which closes `{$PRISMA.BFD.TRAP.HOLD}` (10 minutes) after the last `bfdSessDown` or by hand. It does not close with `bfdSessUp`: Zabbix does not know which session each notification belongs to, and one session coming back would close another's outage. If the session came back, the `bfdSessUp` item and the box's `show bfd peers` say so.

The template was imported into Zabbix 7.0 and tested against a lab box: the discoveries found the ports, the pools, the workers, VRRP and the bundles, the items received values, and the `linkDown`, `linkUp` and `vrrpStateChange` notifications each arrived in their own item. SNMPv3 and the routing tables were not tested against a real Zabbix. The routing tables are checked against the daemons by the product's tests.

## What is not served

* **Nothing per subscriber or per session.** That would mean walking the session table at every query.
* **Fans, power supplies and motherboard temperatures.** They need the chassis's own sensors (IPMI or `/sys/class/hwmon`), and the only test box with hardware is a VM, which has none of them: nothing could be read. They are left for when there is a physical box to measure.
* **Network cards as entities** (model, firmware, serial number): the card's serial number is only readable by root, and the SNMP agent does not run as root. The port description carries the PCI address.
* **Whether the HA pair agrees with this box** on the mapping, the forwards and the self-test result: the VTY shows it, in `show cgnat ha` and `show cgnat forward`, and the self-test reports it on the console at every boot.


:::info
SNMPv3 is configured and the user is created with read-only access, but a v3-authenticated query is **not** verified end to end by the product's tests. Check yours with the `snmpget` at the end of this page before depending on it.

:::

# Notifications: the box tells you when something changes

From version 0.143.3 on, the box does not wait for the next query: it **notifies** the NMS when something changes, for each configured destination:

```
configure
 snmp trap-host 10.0.0.9 community <community>
 snmp trap-host 10.0.0.10 community <community> inform
 commit
 exit
```

Each destination receives a **trap**, unless it says `inform` — an inform is acknowledged by the receiver and resent until it is. The destination's community is treated as a password: `show running-config` shows `<set, not shown>`. `no snmp trap-host <address>` stops sending to it.

| When | Notification |
|------|--------------|
| a dataplane port goes down or comes back | `linkDown`, `linkUp` |
| a BGP session goes down or is established | `bgpBackwardTransition`, `bgpEstablished` |
| an OSPF neighbor changes state | `ospfNbrStateChange` |
| a VRRP router changes state | `vrrpStateChange`, with the interface, the VR ID and the state |
| a pool reaches 90% of its blocks | `poolNearlyFull`, with the pool name |
| ports are refused | `portsRefused` |
| a records transport connects or drops | `recordsTransportChange` |
| connection records are lost | `recordsLost` |
| a bundle's active members change | `bundleMembersChange` |
| an upgrade left the old dataplane running | `dataplaneNotCurrent` |
| the boot self-test failed | `selfTestFailed` |
| a `commit confirmed` nobody confirmed was reverted | `configReverted` |
| tuning is waiting for a reboot | `tuningBetweenPlans` |
| the configuration trail stopped checking out | `auditTrailDamaged` |
| a pool is left with no free block | `poolExhausted`, with the pool name (since 0.152.2) |
| a module's RX, temperature, voltage, bias or TX crosses a threshold, or comes back | `transceiverStateChange`, with the port and the level (since 0.152.2) |
| a module is inserted, removed or replaced in a cage | `entConfigChange`, the ENTITY-MIB's own notification |
| a BFD session goes down or comes back | `bfdSessDown`, `bfdSessUp` from the BFD-STD-MIB, with the session index and the diagnostic (since 0.152.3) |
| disk, memory or load cross their threshold | the standard net-snmp notifications |

A port going down and a BGP session going down were already standard notifications (`linkDown`/`linkUp` from the IF-MIB and `bgpBackwardTransition` from bgpd), and that is why they did not get a product notification of their own: they do not arrive twice. Login, commit and restart do **not** have notifications: those are in `show log`, which goes to a syslog with `system` > `logging` and is followed live with `monitor log`.

**BFD session.** The notifications are the BFD-STD-MIB's own (RFC 7331): `bfdSessDown` (`1.3.6.1.2.1.222.0.2`) when the session goes down and `bfdSessUp` (`.0.1`) when it comes back. Each one carries `bfdSessDiag` with the session index — the same as in the `bfdSessTable` the box serves, so the NMS knows which row to query — and the diagnostic: `1` is the detection time expiring (the neighbor stopped answering), `3` is the neighbor signaling that it tore the session down. BFD still detects the failure in milliseconds and withdraws the routes immediately; it is the notification that arrives up to 10 seconds later, because it comes from the same watcher that records the failure in `show log` and that reads bfdd every 10 seconds. A session that went down and came back between two reads is not lost: both notifications arrive. Each session sends at most four notifications in five minutes; if it keeps flapping, the NMS receives the final state as soon as the window allows. `bfdNotificationsEnable` answers `true(1)` only while notifications are actually being sent — with at least one `snmp trap-host` configured — and `false(2)` with no destination.

The named notifications (`vrrpStateChange` and the rest) arrive as DISMAN-EVENT-MIB `mteTriggerFired`, with the name in the `mteHotTrigger` field, and sit under the same MIB number. The box checks each condition every 30 or 60 seconds, so the notification arrives up to a minute after the event — tuning and the trail, every five minutes.

# What is worth graphing

| Metric | Why |
|--------|-----|
| subscribers at 90% of their block (`prSubscribersNearFull`) | it is the early alarm; by the time it reaches 100% the customer has already called |
| refused port (`prPortRefusals`) | any increase is a subscriber unable to open a connection |
| pool occupancy (`prPoolUtilisation`) above 80% | the pool is running out of blocks to hand out |
| pool subscribers (`prPoolSubscribers`) close to `prPoolGuaranteed` | the pool is close to what it can guarantee |
| transport enabled and **not connected** (`prLogTransportConnected`) | connection records are going to the spill file |
| lost records (`prLogTransportDropped`, `prLogIpfixDropped`) going up | it is a hole in the retention log |
| `prSystemDataplaneCurrent` false | an upgrade waiting for a reboot |
| `prSessionsFit` close to zero | new sessions are going to be refused for lack of memory |
| `prVrrpState` changing | there was a role switch in HA |
| `prBundleActiveMembers` less than `prBundleMembers` | the bundle is running on fewer legs than it has |
| `prRadiusRefused` going up | the BNG is sending accounting the box does not accept |
| packets not let through (`prPacketsDropped`) | a sudden increase is usually a configuration or route change; `prDropTable` says the reason |
| PPPoE sessions up (`prBngSessionsActive`) dropping all at once | an access link, the control plane, or RADIUS refusing the subscribers coming back |
| unanswered Access-Requests (`prBngRadiusAuthTimeouts`) going up | the RADIUS server, the path to it, or a different secret |
| `prBngRadiusState` at `failed` | the server is being skipped; with no server alive, nobody gets in |
| `prBngShaperDroppedBoxBuffers` rising | subscribers losing packets their plan had room for: the queues hold every buffer the box sets aside for them |

The same can be seen through the VTY, without an NMS:

```
show cgnat top                              who is closest to filling their block
show cgnat pool                             how many are near the end, by usage band
show cgnat drops                            what did not get through, and why
show cgnat memory                           how many sessions still fit in CGNAT memory
show interface utilization <name>           how much is going through the port, right now
```

`show interface utilization` samples the dataplane counters several times and shows the rate in bit/s of each port, receiving and transmitting, with a small bar chart of that run's samples. By default it is 5 samples every 2 seconds; `interval <seconds>` (0.1 to 60) and `count <n>` (1 to 120) change that, and the port name at the end limits it to a single one:

```
show interface utilization interval 1 count 30 ens19
```

Where the dataplane reports the link speed, the bars and the percentage are of that port's line rate. Where it does not — every virtual interface —, the bars are scaled by the run's highest sample, and the footer says so instead of making up a percentage. Ctrl-C ends the sampling before the end and shows what has already been measured. It is the answer to *"is the link full?"* without waiting for the NMS graph to close the interval — but it keeps no history between one run and another: history is the NMS's job.

# How to check

From the NMS itself, or from any machine that reaches the `listen-address`:

```sh
snmpget -v2c -c <community> <address> .1.3.6.1.2.1.123.1.1.1.0
snmpget -v3 -l authPriv -u nms -a SHA -A <passphrase> -x AES -X <passphrase> <address> .1.3.6.1.2.1.123.1.1.1.0
```


:::success
The agent answering the RFC 4008 timeouts confirms it is up; the Prisma MIB counters, under `.1.3.6.1.4.1.67033.1`, show CGNAT where someone is looking.

:::

If the agent answers nothing, first check whether `listen-address` is an address the NMS reaches, and then whether the dataplane is alive (`show version`): with the dataplane stopped, silence is a deliberate answer, not an SNMP failure.

# See also

* [Operations and troubleshooting](/s/wiki-en/doc/operations-and-troubleshooting-kql3GmIqmX) — the same numbers through the VTY
* [When a subscriber's block fills up](/s/wiki-en/doc/when-a-subscribers-block-fills-up-NvSSgpWda1) — what to do when `.4.0` and `.5.0` go up
* [Routing, SSH, users and SNMP](/s/wiki-en/doc/routing-ssh-users-and-snmp-jjxcPssV72) — the reference for the `snmp` commands
* [Timeouts and the RFC floors](/s/wiki-en/doc/timeouts-and-the-rfc-floors-HmiUW10Fkh) — the values RFC 4008 exposes
