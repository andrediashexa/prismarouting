# SNMP: monitoring everything the box does

## TL;DR

**Where it stands (#504).** Delivered: the view and the host MIBs (#497),
IF-MIB for the dataplane's cards (#498), BGP, OSPF, OSPFv3 and IS-IS over
AgentX and the FIB's size (#502, #503), and PRISMA-ROUTING-MIB for the CGNAT,
its logging transports and the running build (#504) - rooted, until the
product has its own number, at the operator's, which `show snmp mib` fills
in. `docs/snmp-mib.md` is the reference for what is served. Next: HA, LACP,
identity sources and workers (#505), and notifications (#507) - decision 4 below was answered by letting each destination choose, a trap unless it says inform.

The maintainer asked (2026-09-29) for the box to be monitorable over SNMP in
**every function it has**, planned in full before anything is built. This is
that plan. It says what the box answers today, what an operator of a CGNAT and
a router needs to watch, which standard MIB carries each thing and which has
to be the product's own, where every value is read from, what the box has to
**tell** the NMS on its own (notifications), how it is kept safe, how it is
tested, and in what order it is delivered. The decisions that are the
maintainer's are listed at the end.

Three findings shape it:

1. **Today an NMS sees almost nothing.** `snmpd.conf` is generated with one
   view, `hexa`, that includes only NAT-MIB's timeouts (`.1.3.6.1.2.1.123`)
   and the operator's enterprise subtree - so `sysDescr`, `sysUpTime`, the
   interface table, CPU, memory and disk, which net-snmp has, are outside the
   view and not answered. What the view does include is eight timeouts and six
   CGNAT aggregates, scraped from `vppctl` on each refresh.
2. **The dataplane's interfaces are invisible to the standard interface
   table.** net-snmp's IF-MIB reads Linux; the cards belong to VPP. The only
   dataplane interfaces Linux sees are the linux_cp taps, whose counters are
   the control-plane traffic and not the forwarded traffic.
3. **FRR is shipped without its SNMP modules** (`frr-snmp` is not in the
   image: `/usr/lib/x86_64-linux-gnu/frr/modules/` has bmp, rpki, fpm and
   pcep, and no `*_snmp.so`), so BGP, OSPF and IS-IS have no MIB at all.

## Principles

* **Read-only, for good.** No SET, no `rwcommunity`, no `rwuser` - this
  stays as `system.py` already has it. Configuration belongs to the VTY,
  where it is validated, committed and rolled back.
* **A standard MIB wherever one exists**, so an NMS understands the box with
  no custom template: IF-MIB, IP-MIB, BGP4-MIB, OSPF-MIB, VRRPv3-MIB, NAT-MIB
  (RFC 7659), HOST-RESOURCES-MIB, ENTITY-SENSOR-MIB. The product's own MIB
  carries only what no standard does.
* **One source per number, and never a screen.** Values come from VPP's
  statistics segment (shared memory, lock-free, what `vpp_get_stats` reads)
  and its binary API - not from parsing `vppctl` output, which the current
  agent does and which breaks the day a screen is reworded, costs a process
  per refresh, and competes with the operator's own commits on the control
  plane's cores.
* **Counters are 64-bit and monotonic** (Counter64 / `ifHC*`), and an index
  is stable across reboots: an NMS that sees ifIndex 3 become another card
  after a reboot draws a year of wrong graphs without an error.
* **The box tells, not only answers.** Every state an operator would page on
  is a notification (a trap or an inform), rate-limited, so the NMS does not
  have to poll fast to catch it.
* **Subscriber data is personal data.** A mapping from a subscriber to a
  public address and ports is what a court asks for (docs/logging.md) and is
  covered by LGPD. Per-subscriber tables are in a view of their own, off by
  default, never in the view a NOC's poller uses.
* **Cheap for the box.** The agent runs on the control plane's cores. Its CPU
  and its reply time are measured under load and bounded, and a walk of the
  whole tree on a box with 200,000 subscribers must not take the VTY's
  commit time with it.

## Architecture

```
            NMS (Zabbix, LibreNMS, the ISP's own)
                 |  SNMPv3 (v2c optional), traps/informs
          +------+-------------------------------------+
          | snmpd (net-snmp), master agent, AgentX     |
          |   SNMPv2-MIB, HOST-RESOURCES, UCD, IP (host)|
          +---+-----------+-------------+--------------+
              | AgentX    | AgentX      | AgentX
   +----------+--+  +-----+--------+  +--+-------------------+
   | dataplane   |  | FRR          |  | prisma subagent      |
   | subagent    |  | bgpd ospfd   |  | CGNAT, retention log,|
   | IF-MIB for  |  | ospf6d isisd |  | identity, HA, boot,  |
   | VPP's cards |  | zebra        |  | commits, self-test   |
   +------+------+  +--------------+  +----------+-----------+
          | stats segment + binary API           | stats segment, API,
          |                                      | the VTY's files
        VPP                                    VPP / host
```

* **net-snmp stays the master agent** (it is already on the box, and already
  carries SNMPv3's user store and the v2c communities the VTY writes). It is
  configured as an AgentX master on a local socket.
* **The subagents speak AgentX (RFC 2741)**, not pass_persist: AgentX
  registers subtrees, serves GETBULK natively, carries notifications, and
  lets a subagent be restarted without snmpd. The current pass_persist helper
  is replaced, not extended.
* **FRR's own AgentX support** is the routing half: add `frr-snmp` to the
  FRR image, load `-M snmp` in bgpd, ospfd, ospf6d, isisd and zebra, and
  point them at the master's socket through the container boundary (a bind
  of the AgentX socket directory, the same way the VTY's broker socket is
  shared today).
* **The dataplane subagent** reads VPP's statistics segment directly - the
  counters VPP already keeps per interface, per node, per error and per
  worker - and the binary API for names, states and speeds. It is the only
  reader of VPP counters, so no number is computed twice.
* **The prisma subagent** serves the product's own MIB and the parts of
  NAT-MIB (RFC 7659) the dataplane subagent does not: pools, subscribers,
  the retention log, identity sources, HA, boot and configuration state.

## What is monitored, function by function

For each: what an operator watches, the MIB and objects, where the value is
read, and notes. "PR-MIB" is the product's own MIB (see *The product's MIB*).

### 1. The box itself

| What | MIB / objects | Source | Notes |
|---|---|---|---|
| identity, version, uptime | SNMPv2-MIB `sysDescr`, `sysObjectID`, `sysUpTime`, `sysName`, `sysContact`, `sysLocation` | net-snmp; VERSION | `sysDescr` names the product, the release and the plugin build; `sysObjectID` is under the product's PEN |
| the plugin that is LOADED vs the release installed | PR-MIB `prSystemVersion`, `prPluginVersion`, `prVersionMismatch` | `show version`'s sources | measured on a real box: 0.140.5 installed and plugin 0.140.0 loaded, after an upgrade without a dataplane restart |
| boot tuning state | PR-MIB `prTuningState` (tuned / between-plans / conservative), isolated cores, hugepages reserved vs wanted | `/etc/vpp/startup.conf` header, `/sys` | the conservative profile forwards nothing (#486) |
| boot self-test | PR-MIB `prSelfTestFailures`, `prSelfTestWarnings`, last run | the self-test's report | a FAIL is also a notification |
| configuration | PR-MIB last commit time, by whom, revision, commit-confirmed pending and its deadline, rollbacks, saved-vs-running differs | the VTY's rollback directory and audit trail | a confirm window that expires and reverts is a notification |
| the audit trail | PR-MIB head digest, entries | `config-history.log` | lets the NMS keep the chain's head off the box |

### 2. The host: CPU, memory, disks, processes

| What | MIB / objects | Source | Notes |
|---|---|---|---|
| CPU per core | HOST-RESOURCES-MIB `hrProcessorLoad`, UCD `ssCpu*` | net-snmp | a worker core reads 100% always (it polls); the dataplane's real load is section 4 |
| memory | HOST-RESOURCES `hrStorage`, UCD `mem*` | net-snmp | plus hugepages: reserved, free, per NUMA node (PR-MIB) |
| disks | `hrStorage` / UCD `dskTable` | net-snmp | **the retention log's filesystem** (`/var/log/prisma-routing`) with its own threshold: a full disk there is the compliance log stopping |
| the records mirror | PR-MIB RAID state | `/proc/mdstat` | a degraded mirror under a one-year obligation (#83) |
| processes | `hrSWRunTable`, UCD `prTable` | net-snmp | the containers and daemons: vpp, bgpd, ospfd, zebra, watchfrr, the VTY broker, the doors, the exporter |
| the control plane's fence | PR-MIB control-plane cores, the cgroup's CPU pressure (PSI) | `/sys/fs/cgroup` | measured: a 16-vCPU box with 15 workers left one core for everything else and a `commit check` took 22.6 s |

### 3. Interfaces (the dataplane's cards, sub-interfaces, bundles)

| What | MIB / objects | Source | Notes |
|---|---|---|---|
| state, speed, MTU, MAC, description | IF-MIB `ifTable`/`ifXTable`: `ifOperStatus`, `ifAdminStatus`, `ifHighSpeed`, `ifMtu`, `ifPhysAddress`, `ifAlias` | binary API | `ifAlias` is the VTY's `description` |
| traffic | `ifHCInOctets/OutOctets`, `ifHCIn/OutUcastPkts`, multicast, broadcast | stats segment `/if/rx`, `/if/tx`, ... | 64-bit only; 32-bit counters wrap in under 4 s at 10 Gbit/s |
| errors and drops | `ifInErrors`, `ifOutErrors`, `ifInDiscards`, `ifOutDiscards` | stats segment `/if/rx-error`, `/if/drops`, `/if/rx-miss`, `/if/rx-no-buf`, `/if/tx-error` | `rx-miss` (the card had nowhere to put a packet) is the first sign of a box out of workers; PR-MIB keeps it apart |
| sub-interfaces and VLANs | IF-MIB with `ifStackTable` | API | each sub-interface its own row, stacked on its card |
| LACP bundles | IEEE8023-LAG-MIB `dot3adAgg*`, `dot3adAggPort*` | the lacp plugin's API | member states, partner, which members carry traffic |
| per queue | PR-MIB per-card per-queue rx packets and misses, and which worker polls it | stats segment | shows an RSS imbalance: one queue full, the others idle |
| the optics | ENTITY-SENSOR-MIB / PR-MIB: SFP temperature, TX/RX power, bias | DPDK module EEPROM | an ISP's first question on a flapping fibre |
| the Linux ends | IF-MIB rows for the linux_cp taps, marked as such | net-snmp | kept, because routing traffic is what they carry |
| **stable ifIndex** | | the subagent keeps a name-to-index file | a reboot, a card added, a card handed over must not renumber |

### 4. The dataplane's health

| What | MIB / objects | Source | Notes |
|---|---|---|---|
| worker load | PR-MIB per worker: vectors per call, loops per second, packets per second, the share of polls that found work | stats segment `/sys/vector_rate`, per-thread loop counters | the real CPU figure: a worker at 7% of polls finding work is idle whatever `top` says (measured on the #475 bench) |
| per-node cost | PR-MIB clocks per packet for the CGNAT nodes | stats segment `/sys/node` | `bpa-in2out` at 114 clocks at -O2 (#490); a regression shows here before it shows as loss |
| drops by reason | PR-MIB table of VPP error counters (node, reason, count, severity) | stats segment `/err/*` | every "packets dropped" a screen shows, as rows an NMS can alert on one by one |
| buffers | PR-MIB buffer pool size, free, allocation failures, per NUMA | stats segment `/buffer-pools/*` | running out of buffers drops everything |
| the heap | PR-MIB main-heap size, used, the session guard's refusals | API / `show memory` sources | a full heap refused sessions since #460 instead of aborting; the refusals are the warning |
| the handoff | PR-MIB packets handed to another worker vs already on the owner, per worker | the bpa errors | on virtio this was 100% until #487; on a physical card it is what #29 is about |
| dataplane restarts | PR-MIB restarts, last reason; `sysUpTime` of the dataplane apart from the box's | the container's state | a dataplane that restarts is a CGNAT that drops every session |

### 5. IP and routing

| What | MIB / objects | Source | Notes |
|---|---|---|---|
| addresses, neighbours | IP-MIB `ipAddressTable`, `ipNetToPhysicalTable` | API | ARP and ND, per interface |
| IP counters | IP-MIB `ipSystemStatsTable`, `ipIfStatsTable` | stats segment | forwarded, delivered, no-route, TTL expired, fragments |
| the FIB | IP-FORWARD-MIB `inetCidrRouteNumber`; PR-MIB routes per VRF and per protocol | zebra (AgentX), API | a full table is ~1M routes: a walk of the FIB is never the default view |
| BGP | BGP4-MIB `bgpPeerTable` (state, uptime, updates, last error); BGP4V2-MIB (IPv6 peers, per-AFI prefixes) | bgpd, AgentX | received, accepted and advertised prefixes per peer and family; `bgpBackwardTransition` and `bgpEstablished` notifications |
| OSPF / OSPFv3 | OSPF-MIB, OSPFV3-MIB: neighbours, interfaces, LSDB size | ospfd, ospf6d, AgentX | neighbour state change notifications |
| IS-IS | ISIS-MIB: adjacencies, circuits | isisd, AgentX | adjacency change notifications |
| static routes and BFD | IP-FORWARD-MIB rows; BFD-STD-MIB if BFD is added | zebra | |
| routing policy | PR-MIB per policy term: routes matched | the policy compiler's route-maps | shows a term that matches nothing, or everything (#484) |
| RPKI | PR-MIB validation state counts, cache session | bgpd_rpki | if RPKI is configured |
| FRR itself | PR-MIB daemon up/down, restarts (watchfrr) | watchfrr | |

### 6. CGNAT

The standard is **RFC 7659, NATV2-MIB** (which obsoletes the RFC 4008
NAT-MIB the agent serves today): it was written for carrier-grade NAT and has
instances, realms, address pools with port usage, per-subscriber counters and
notifications for pool and subscriber thresholds. The box maps onto it, and
the product's MIB carries what is particular to port blocks.

| What | MIB / objects | Source | Notes |
|---|---|---|---|
| the NAT instance | NATV2 `natv2InstanceTable`: translations, sessions, drops, limits | bpa counters | one instance per box, realms per VRF |
| pools | NATV2 `natv2PoolTable` + `natv2PoolRangeTable`: addresses, ports in use, high-water marks | the pool tables | per pool: public addresses, block size, blocks total / allocated / free / in hold-down / reserved, deterministic or dynamic |
| subscribers (aggregate) | PR-MIB per pool: subscribers with a block, subscribers at their block limit, port refusals, subscribers refused a block | the pool tables, `show bpa top`'s source | the numbers the current agent serves, per pool instead of one sum |
| subscribers (each) | NATV2 `natv2SubscriberTable` | the subscriber records | **its own view, off by default** (personal data); on a 200k-subscriber box a walk is 200k rows and is bounded |
| sessions | PR-MIB per worker: sessions, capacity, created per second, expired per second, by protocol | the session tables | the table filling is the heap guard's warning |
| allocation | PR-MIB blocks allocated/released per second, allocation failures by reason, lent blocks (#210) | bpa counters | |
| forwards and statics | PR-MIB forwards configured and in use | the statics tables | |
| ALGs | PR-MIB per ALG (FTP, RTSP, PPTP, SIP, ESP): on/off, packets rewritten, expectations | ALG counters | |
| thresholds | NATV2 notifications `natv2NotificationPoolUsageHigh/Low`, `natv2NotificationSubscriberPortUsage` | the prisma subagent | the pool utilisation alarm the VTY already has becomes a notification |

### 7. The retention log (the compliance half)

| What | MIB / objects | Source | Notes |
|---|---|---|---|
| each transport | PR-MIB per transport (IPFIX UDP, IPFIX TCP, syslog): configured, connected, collector, records written, bytes sent, bytes pending, spill bytes on disk, replayed, dropped, last error, reconnections | the exporter's counters (`show bpa logging`'s source) | a transport that is **dropping** is a hole in a court record |
| the boot marker | PR-MIB init time announced, markers sent | the exporter | |
| the queue from the dataplane | PR-MIB events queued, dropped because the queue was full | bpa log queue | the loss the dataplane can suffer before the exporter sees it |
| notifications | collector unreachable; records dropped (any, once); spill above a threshold; the log filesystem above a threshold | the prisma subagent | these page someone - the rest is graphs |

### 8. Identity sources

| What | MIB / objects | Source | Notes |
|---|---|---|---|
| the source | PR-MIB kind (MAP, DHCP leases, RADIUS), path, entries loaded, last read, last error | `show bpa identity`'s source | a lease file that stopped being read is a record naming nobody |
| RADIUS accounting | PR-MIB packets received, accepted, rejected, subscribers known | the RADIUS listener | |

### 9. High availability

| What | MIB / objects | Source | Notes |
|---|---|---|---|
| VRRP | VRRPV3-MIB (RFC 6527) `vrrpv3OperationsTable`: state, master address, priority | the vrrp plugin's API | `vrrpv3NewMaster` notification |
| the pair | PR-MIB pair state, the peer, the last takeover, block-layout agreement | docs/ha.md's state | a pair whose layouts drifted (`commit diverge`) is a takeover that remaps subscribers |

### 10. Management and security

| What | MIB / objects | Source | Notes |
|---|---|---|---|
| logins | PR-MIB successful and failed logins per account, sessions open | the login broker and sshd | failed logins in bursts are a notification |
| the SNMP agent itself | SNMP-MIB `snmpInBadCommunityNames`, `usmStats*` | net-snmp | |
| certificates and keys | PR-MIB SSH host key origin | `ssh-host-key-origin` | |

### 11. Hardware (physical boxes)

| What | MIB / objects | Source | Notes |
|---|---|---|---|
| temperatures, fans, power supplies | ENTITY-MIB + ENTITY-SENSOR-MIB | IPMI where present, `/sys/class/hwmon` | absent in a VM, and said as absent rather than as zero |
| the cards | ENTITY-MIB physical rows per NIC: model, firmware, serial | PCI, DPDK | the X520 on the maintainer's box answers "X520 10GbE Controller" and its VPD today |

## Notifications

Sent as SNMPv3 informs where the NMS supports them (acknowledged, so a lost
UDP datagram is not a lost page), traps otherwise. Every one rate-limited and
de-duplicated, with the value that crossed and the threshold it crossed.

| Notification | When |
|---|---|
| `coldStart` / `warmStart` | the box / the agent started (standard) |
| PR `prDataplaneRestart` | the dataplane restarted, with the reason |
| PR `prVersionMismatch` | the loaded plugin is not the installed release |
| `linkDown` / `linkUp` | a dataplane card or sub-interface (IF-MIB, standard) |
| `bgpEstablished` / `bgpBackwardTransition` | BGP4-MIB, from bgpd |
| OSPF / OSPFv3 / IS-IS neighbour changes | from FRR |
| `vrrpv3NewMaster` | VRRPV3-MIB |
| NATV2 pool usage high / low, subscriber port usage | thresholds per pool |
| PR `prPoolExhausted` | a pool has no free block, with the subscribers refused |
| PR `prHeapGuard` | sessions refused because the heap is near full |
| PR `prRetentionDropped` | a retention transport dropped records |
| PR `prRetentionCollectorDown` | a collector unreachable for longer than a threshold |
| PR `prRetentionSpillHigh` | the spill file above a threshold |
| PR `prLogFilesystemHigh` | the retention filesystem above a threshold |
| PR `prSelfTestFailed` | the boot self-test printed a FAIL |
| PR `prCommit` / `prRollback` / `prConfirmExpired` | configuration changed, rolled back, or reverted by an unconfirmed commit |
| PR `prLoginFailures` | failed logins above a rate |
| PR `prRxMiss` | a card dropping for lack of workers or buffers |

## The product's MIB

`PRISMA-ROUTING-MIB`, SMIv2, compiled with `smilint` in `make test`, shipped
on the box (so `snmpwalk -m ALL` names every object) and on the public
releases directory, versioned with the release.

**It needs an enterprise number of its own.** Today the enterprise subtree is
the *operator's* PEN (`snmp enterprise-number`), which is right for objects
an operator defines and wrong for a product's MIB: the same object would have
a different OID on every customer's box, no NMS template could be written
once, and `sysObjectID` could not name the product. IANA assigns a PEN free
of charge; the product's objects go under it, and the operator's own number
stays for what is theirs (if anything). **This is the maintainer's to apply
for**, and it is the one thing that blocks the MIB's first object.

## Security

* **SNMPv3 by default**, `authPriv`, SHA-256 and AES-128 or better; v2c only
  when configured, read-only, and said as the weaker option on the screen.
* **Views by role**: `noc` (everything except per-subscriber data and the
  audit trail), `full`, and `subscribers` (the per-subscriber tables) - an
  SNMPv3 user is given a view, and the VTY shows which.
* **Where it listens**: management addresses and VRF only by default; a
  source-address list per community and per user.
* **Nothing written**: no SET, as today.

## Performance

* The subagents read the statistics segment, which is shared memory: a walk
  of every interface counter costs no VPP time.
* Tables sized by the box (subscribers, sessions, FIB) are never in the
  default view and are bounded per request; GETBULK is served natively.
* Measured and written down before each phase ships: the agent's CPU on the
  control plane's cores, the reply time of a full walk of the default view,
  and the VTY's `commit check` time with an NMS polling every 60 s - on the
  #475 bench and on a box with a physical card.

## Integrations

* **Templates**: a Zabbix template and LibreNMS/Observium definitions,
  generated from the MIB rather than written beside it, so they cannot
  disagree with it.
* **Grafana** dashboards for the Zabbix/Prometheus data sources the ISP
  already has.
* **NetConfig**: the maintainer's own monitoring platform reads SNMP; the
  product's MIB and templates are its first consumers.
* **Prometheus** is out of scope here and noted: an exporter reading the same
  statistics segment is a small addition once the dataplane subagent exists.

## How it is tested

* `smilint` on every MIB the box ships, in `make test`.
* **Every documented object is served, and every served object is
  documented**: a unit test reads the MIB and the subagents' registration
  tables and refuses a difference either way - the same rule
  test_plugin_cli_says holds for the plugin's screens.
* **Labs with traffic**: an SNMP walk in the docker labs while the packet
  generator sends, asserting that the interface counters, the CGNAT
  counters and the retention transport counters **move by what was sent**,
  not only that they exist.
* **Notifications**: `snmptrapd` in the lab; each notification caused on
  purpose (a link down, a pool exhausted, a collector killed, a commit
  confirmed left to expire) and received.
* **Scale**: 200,000 subscribers and 1,000 sub-interfaces; a walk of the
  default view stays under a bound, and the VTY's commit time with it.
* **The bench**: the #475 bench and the X520 box, polled by a real NMS for a
  day before a phase is released.

## Phases

| Phase | Delivers | Needs |
|---|---|---|
| **1. Foundation** | the view fixed so SNMPv2-MIB and HOST-RESOURCES answer; AgentX master; SNMPv3 hardening (auth/priv, views by role, source lists); the dataplane subagent with IF-MIB for VPP's cards (64-bit, stable ifIndex), buffers, workers, drops; `linkUp`/`linkDown`; the retention filesystem threshold | nothing |
| **2. CGNAT and the retention log** | NATV2-MIB (instance, pools, thresholds, notifications); PR-MIB pools, workers, sessions, ALGs, retention transports, identity; the compliance notifications; the pass_persist helper retired | the PEN |
| **3. Routing** | `frr-snmp` in the image; BGP4-MIB / BGP4V2, OSPF, OSPFv3, IS-IS over AgentX across the container boundary; FIB counts per VRF; routing notifications | an image change: a Y release |
| **4. Box state** | versions, tuning, self-test, configuration and commits, logins, audit head; their notifications | the PEN |
| **5. HA and hardware** | VRRPV3-MIB, the pair's state; ENTITY-MIB and sensors, SFP optics | a physical box for the sensors |
| **6. Integration** | the Zabbix template and LibreNMS definitions generated from the MIB; the wiki page for operators (in Portuguese); the public MIB file | phases 1-5 |

Each phase is its own issue and its own release, with its measurements.

Phase 4 shipped in 0.147.1 (#524): the tuning in effect, the self-test, the
configuration's commits and confirm window and the trail's head, with four
notifications - `docs/snmp-mib.md` lists them. Logins are not in it: their
source is sshd's journal, which the SNMP agent's account cannot read, and a
file of login counts would need the login path to write it; that is its own
issue when somebody asks. The versions were already served (`prSystem`).

## Decisions for the maintainer

1. **The PEN**: apply to IANA for the product's own enterprise number (free,
   a form, days). Phase 2 and 4 objects are blocked on it.
2. **SNMPv3 only by default**, with v2c as an opt-in - or v2c kept on by
   default for NMSes that are behind?
3. **Per-subscriber tables**: served at all, and to which role?
4. **Informs or traps** by default.
5. **The NMS the templates target first**: Zabbix, LibreNMS, NetConfig.
