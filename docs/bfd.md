# BFD

| | |
|---|---|
| **Who** | Whoever builds the BFD phases of #559, and whoever reviews them. |
| **What** | Where BFD runs on this box, the measurement that decided it, what phase 1 delivers and what is left. |
| **When** | Phase 1 is on `routing/bfd`, for the next patch release after 0.150.6. |
| **Where** | FRR's `bfdd`, in the routing container, behind the linux_cp pair like every other routing protocol here. |
| **Why** | BGP's hold time is 90 s by default, and a static route is never withdrawn by anything: a neighbour that stops forwarding with its link up is seen in minutes, or never. BFD sees it in the time the operator configures, measured below at 252 ms for 100 ms x 3. |
| **How** | `bfd` sessions and profiles at the VTY; `bfd` on a BGP neighbour and on a static route; `show bfd peers` and `show bfd peer <address>`. |
| **How much** | Phase 1: the daemon on, the VTY, two screens, a lab and a measurement. |

The key words **MUST**, **MUST NOT**, **SHOULD** and **MAY** in this document
are to be interpreted as described in
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

## TL;DR

BFD runs in FRR's `bfdd`. Its packets (UDP 3784, 4784) reach it through the
linux_cp pair the way BGP's and OSPF's do, and it holds a session at 50 ms x 3
with no down events in 30 s, idle and with ~260 kpps through the dataplane.
The dataplane's own BFD (VPP 26.06) did not hold a session against bfdd at any
interval measured, 300 ms included, and would have needed a daemon of this
product's to tell bgpd and staticd about it. `bfdd` is turned on by the routing
container's entrypoint, so a patch delivers it.

## Where it runs, measured

`lab/bfd-measure.sh`, on the lab machine of 2026-10-04: one VPP 26.06 with a
linux_cp pair, and a neighbour in a network namespace running FRR 10.7.1's
`bfdd`. Each row is one session left up for 30 s at the interval shown, with
detect-multiplier 3, read off the far end's own counter of down events.
"Loaded" is the same 30 s with a UDP flood from the neighbour routed through
the dataplane to a blackhole, so that the dataplane's main thread - which is
the thread that punts BFD's packets to Linux - is forwarding the whole time.

| Interval (tx = rx) | bfdd behind linux_cp, idle | loaded (~260-280 kpps) | VPP's own BFD, idle | loaded |
|---|---|---|---|---|
| 300 ms | 0 down events | 0 | 9 | 8 |
| 100 ms | 0 | 0 | 14 | 15 |
| 50 ms | 0 | 0 | 15 | 16 |
| 20 ms | 2 | 0 | 16 | 16 |
| 10 ms | 0 | 0 | 16 | 16 |

**bfdd** is stable down to 50 ms. The two down events at 20 ms idle are one
run; the same interval loaded, and 10 ms both ways, had none. What this
document claims is 50 ms x 3 (150 ms detection), and the 20 ms and 10 ms rows
are reported, not offered: a single 30 s run is not enough to call them
stable, and a lab VPP on one thread is not the X520.

**VPP's BFD** never held a session against bfdd. Its own `show bfd sessions`
said why in two lines: `Last control frame tx 906831.08s ago`, and over 10 s
at a negotiated 300 ms it sent 21 control packets where about 33 were due, so
the far end's 900 ms detection time ran out between them and it went down
every 1.4 s. That was not investigated further, because the other half of the
decision does not depend on it: FRR's protocols register BFD sessions with
`bfdd` over zebra, and nothing in FRR 10.7.1 would carry a session VPP runs to
bgpd or staticd. Using it would mean writing that daemon.

So: **bfdd**, on the measurement and on the integration.

## How the packets get to it

Nothing on the box does anything special. BFD control packets are UDP to the
interface's own address, which the dataplane punts to the Linux end of the
pair like every packet for the box that it does not handle itself - measured
with `tcpdump` on the pair: the neighbour's packets arrive with TTL 255, which
single-hop BFD (RFC 5881 section 5) requires. `bfdd` names the interface by
its Linux name (`vppwan0`, `enp1s0`), which is the name the operator types.

## bfdd is on, and why it is the entrypoint that turns it on

The installer enables bgpd, ospfd, ospf6d and isisd in `/etc/frr/daemons`
(#99, #187). A patch release cannot change the installer
(`tools/release-gate.sh`, #410), but the routing container's image rides every
upgrade artifact, so `appliance/frr-entrypoint` turns `bfdd=no` into
`bfdd=yes` - and adds the line when there is none - before FRR starts. A box
installed from any medium gets bfdd at the first start of the routing
container on a release that has this; a box upgraded by a patch keeps its
running routing container until its next reboot, and until then:

* `commit` refuses any configuration that needs bfdd, naming it and the
  reboot - the `bfd` block, `neighbor X bfd`, a static route's `bfd`, `ip ospf
  bfd`, `isis bfd`. bgpd and staticd take the word whether bfdd runs or not,
  and without it every session they ask for is never brought up.
* the self-test lists bfdd among the routing daemons not running.

## What phase 1 delivers

At the VTY:

```
configure
 bfd
  profile fast
   receive-interval 100
   transmit-interval 100
   detect-multiplier 3
  exit
  peer 10.0.0.2 interface vppwan0
   profile fast
  exit
 exit
 router bgp 65000
  neighbor 10.0.0.2 bfd profile fast
 exit
 router static
  203.0.113.0/24 next-hop 10.0.0.2 bfd profile fast
 exit
commit
```

* `bfd` is a level of its own, like `rpki`. `peer <address> [multihop]
  [local-address <address>] [interface <interface>] [vrf <name>]` and
  `profile <name>` are blocks inside it. Inside a `peer`, `profile <name>` is
  the session taking that profile - a statement, not a block - which is the
  one place the same words mean two things, measured on FRR 10.7.1.
* Timers, in a peer or a profile: `detect-multiplier`, `receive-interval`,
  `transmit-interval`, `echo-mode`, `echo receive-interval`, `echo
  transmit-interval`, `passive-mode`, `minimum-ttl` (multihop), `shutdown`.
  FRR writes none of `detect-multiplier 3`, `receive-interval 300`,
  `transmit-interval 300`, `echo receive-interval 50`, `echo
  transmit-interval 50` or `minimum-ttl 254` back, so the commit's read-back
  check excuses exactly those values (`lab/frr-readback.sh` 4f, each with a
  control at another value).
* A BGP neighbour: `neighbor <address> bfd [profile <name>]`. FRR reads `bfd
  profile <name>` back as two lines, `bfd` and `bfd profile <name>`.
* A static route: `<prefix> next-hop <hop> ... bfd [multi-hop] [source
  <address>] [profile <name>]`. FRR reads it back after the distance (`ip route
  P NH tag 5 10 bfd ...`), so that is how it is written; the route's identity
  is still its prefix and next hop, so adding `bfd` to a route changes that
  route rather than adding a second one.
* `show bfd peers`: two lines a session - where it runs, then its state, for
  how long, its interval, its detection time, its diagnostic when there is one
  and how often it went down. Two lines because an IPv6 neighbour and its IPv6
  source do not fit one row of 79 columns; FRR's own `brief` row is 171.
* `show bfd peer <address>`: every session to that neighbour in detail - this
  box's timers and the neighbour's, the detection time, echo, packets in and
  out, and how often it went up and down.

Proved by `lab/bfd.sh`: BGP and a static route over a BFD session configured
through the VTY, then a silent failure (`netem loss 100%` on the neighbour's
side, the link left up). With 100 ms x 3 (300 ms):

| | ms after the failure |
|---|---|
| bfdd sees the session down | 252 |
| the static route leaves the dataplane's FIB | 252 |
| the BGP route leaves the dataplane's FIB | 325 |

With BGP's hold time at 30 s. The same lab with every `bfd` taken out (`sh
tools/lab.sh bfd off`) sees neither route leave in 12 s, and the static route
never would.

## What phase 2 delivers (0.150.10)

* **The API.** `OperationalService.ListBfdSessions` carries `show bfd peers`
  (or one `peer`) as fields, from the one reading the screen is drawn from;
  `WatchBfd` streams the sessions as they are, then every change: a state
  change, a session that appeared or went away, a down-and-up between two
  readings (its down counter moved), and the daemon not answering.
* **SNMP.** The product's agent serves BFD-STD-MIB (RFC 7331) - the
  scalars, bfdSessTable, bfdSessPerfTable and the map tables - read from
  bfdd through vtysh on its own registration, so it answers while the
  dataplane does not. `bfdNotificationsEnable` reads false: pass_persist
  cannot send a trap (the traps came in 0.152.3, below).
* **The IGPs.** `bfd profile <name>` at an OSPF, OSPFv3 or IS-IS interface;
  the VTY stages the bare `bfd` line first, because FRR 10.7.1 answers the
  profile line alone with "has not been set" and stores nothing. lab/bfd.sh
  drives OSPF down through BFD: after the silent cut at 100 ms x 3, bfdd saw
  the session down in 277 ms, the static route left in 277 ms, BGP in 351 ms
  and OSPF (dead time 40 s) in 256 ms.

## What phase 3 delivers

* **The configuration as fields.** GetConfig area `bfd` reads the block's
  sessions and profiles; SetConfig writes a `bfd_peer` or a `bfd_profile`,
  only the lines that change; DeleteConfig removes a session, a profile or
  the whole block, each as the `no` of its header (#570). A BGP neighbour's
  `bfd profile` and a static route's `bfd [multi-hop] [source] [profile]`
  are fields of theirs. lab/api-grpc.sh's phase 9 commits each and reads it
  back through GetConfig and the VTY's own text, then removes it.
* **IS-IS and OSPFv3 over BFD.** lab/bfd.sh drives both down with the same
  silent cut as OSPF, `isis bfd profile lab` and `ipv6 ospf6 bfd profile
  lab` at the interface, and then takes their `bfd` away again. Measured
  (one run, `tools/lab.sh bfd`, 2026-10-05): the IS-IS route left the
  dataplane 320 ms after the cut (hold time 30 s), the OSPFv3 route 687 ms
  (dead time 40 s) - the peer's side asks with FRR's defaults, 300 ms x 3,
  which the session negotiates up to; with BFD off neither left within 12 s.

## Traps: built (0.152.3)

`bfdSessUp` and `bfdSessDown` (RFC 7331) are sent to every `snmp trap-host`,
by the host's watcher of events (`prisma-routing-events-watch.service`,
`src/prismash/bfdtraps.py`), and `bfdNotificationsEnable` reads true while
they are. FRR 10.7.1 has no `bfdd_snmp` to send them, and the agent that
serves the table is a pass_persist, which answers polls and cannot raise one.

**The OIDs.** RFC 7331 section 5 makes `bfdNotifications` `{ bfdMIB 0 }`,
`bfdSessUp` `{ bfdNotifications 1 }` and `bfdSessDown` `{ bfdNotifications 2
}`: **`bfdSessUp` is `1.3.6.1.2.1.222.0.1` and `bfdSessDown` is `.0.2`**.
This section's design had them the other way round; the Zabbix template's
test holds the right pair now.

**What one carries.** The notification's OBJECTS clause and nothing more:
`bfdSessDiag` twice, the lowest and highest index of a run of contiguous
sessions that made the same change in one reading (RFC 7331's SHOULD), the
same instance twice for one session. The values are the sessions' current
diagnostics, as IANAbfdDiagTC - RFC 7331's text said "set equal to this new
state", which put a state in a diagnostic, and erratum 8327 (verified 2025)
corrected it to the current values. The index is bfdd's local
discriminator, which is what the agent indexes `bfdSessTable` by: an NMS
reads the row the notification names. Received by snmptrapd in
`lab/snmp-traps.sh`:

```
.1.3.6.1.2.1.1.3.0 = Timeticks: (16036) 0:02:40.36	.1.3.6.1.6.3.1.1.4.1.0 = OID: .1.3.6.1.2.1.222.0.2	.1.3.6.1.2.1.222.1.2.1.13.1598380235 = INTEGER: 1	.1.3.6.1.2.1.222.1.2.1.13.1598380235 = INTEGER: 1
```

`bfdSessDown` for session 1598380235, diagnostic 1 (control detection time
expired: the neighbour's bfdd was killed). Its `bfdSessUp` carries 0, because
bfdd clears its diagnostic when the session comes up.

**Why the watcher, and not a reader of its own every second.** The watcher
already reads bfdd's sessions as root on the host every 10 s and compares
each reading with the one before for the record (`show log`, #588 phase 2):
a state change, or bfdd's down counter moving between two readings - a
session that went down and came back inside 10 s. The notifications are
that comparison said to the NMS: one reader of bfdd, so the record and the
NMS cannot disagree, and no second resident process. The cost is that a
notification follows the change by up to a tick: measured in
`lab/snmp-traps.sh`, `bfdSessDown` reached snmptrapd 6.6 s after bfdd said
the session was down and `bfdSessUp` 9.1 s after it said up (one run,
2026-10-07). BFD's own detection - what withdraws the routes, in 252 ms
above - is bfdd's and does not wait on this: the notification only reports
it. A flap shorter than a reading is not lost: bfdd's down counter moved, and
it is sent as a `bfdSessDown` followed by a `bfdSessUp`. A reading every
second would have meant a vtysh call every second for ever, for a page that
then reaches a person in minutes.

**Why AgentX, and not `snmptrap`.** The design named net-snmp's `snmptrap`
and `snmpinform`. They are in Debian's `snmp` package, and the medium
installs `snmpd` alone (`appliance/build-iso.sh`): the box has no
`snmptrap`, and adding it is a change to the medium, which a patch cannot
make. snmpd is already the AgentX master FRR's daemons send their BGP and
OSPF notifications through (#502) and already holds the destinations `snmp
trap-host` writes (#507), so the watcher opens an AgentX session on its
socket, hands it a Notify-PDU (RFC 2741 6.2.10) and closes it. snmpd sends
it to each destination the way that destination asked - a trap, or an
inform it resends until acknowledged. The watcher never reads a community
and never retries a datagram.

**Bounded, and not said twice** (docs/snmp-plan.md: every notification
rate-limited and de-duplicated). Each session gets at most four
notifications in five minutes - the bound the record keeps for one subject
(`eventwatch.FLAP_EVENTS`, `FLAP_WINDOW`). A notification saying what the
NMS was last told for that session is not sent. And a session whose bound
held a change back is told its state once the window lets it, so a flap
never ends with the NMS believing the wrong state. When snmpd takes nothing
(it is restarting), nothing is recorded as told, and the next tick says
each session's state.

**`bfdNotificationsEnable`** reads true(1) when, and only when, the
notifications are being sent: the watcher writes
`/run/prisma-routing/state/bfd-notifications` every tick - whether snmpd.conf
holds a `trap2sink` or `informsink` (root's to read, 0600) and snmpd's AgentX
socket is there, and its own pid - and the agent, as Debian-snmp, serves true
only from that file saying so, written within 35 s (three ticks), naming a
pid that runs. A watcher stopped by systemd reads false at once (its pid is
gone), one that hangs within 35 s. Measured in the lab: 1 with a destination,
2 one tick after `no snmp trap-host`. It is read-only, as every object the
agent serves.

**Not sent twice.** Links are IF-MIB's `linkDown`/`linkUp` and BGP is
bgpd's `bgpBackwardTransition`/`bgpEstablished` (#588 phase 3 decided both):
the BFD notifications are the only ones this watcher sends, and only for BFD.

**What it does not say.** A restart of the routing daemons draws every
session a new discriminator and zeroes bfdd's counters: a session up before
and after is not sent as down and up - the restart is an event of its own in
`show log`, and bgpd's own notification says what it cost; one that stays
down is sent `bfdSessDown` under its new index. A session removed from the
configuration has no row to name and is not sent. In the minute after the
routing daemons start the watcher compares nothing (`eventwatch.SETTLE`),
and neither is anything sent.

Each behaviour has a mutation shown red in `test/test_bfdtraps.py` and
`appliance/test_snmp_agent.py`: the down counter not read; the index taken
from the reading's position; the bound; the catching up; the
de-duplication; the ranges; `bfdNotificationsEnable` true without a
destination, without freshness, without the pid; the two OIDs swapped. The
lab (`sh tools/lab.sh snmp-traps`) kills a neighbour's bfdd and starts it
again, and receives one `bfdSessDown` and one `bfdSessUp` naming the index
the agent serves; its off run, with no destination, receives neither.

## What is left

* **The installer.** The next medium SHOULD enable bfdd beside the other four
  daemons, so that the entrypoint is a second line of defence rather than the
  only one.
* **The X520.** The table above is a lab VPP on one thread. The issue asks for
  the shortest stable interval on the X520 under CGNAT load, which is a run
  of `lab/bfd-measure.sh`'s shape on that box.
