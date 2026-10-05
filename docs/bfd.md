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
  cannot send a trap.
* **The IGPs.** `bfd profile <name>` at an OSPF, OSPFv3 or IS-IS interface;
  the VTY stages the bare `bfd` line first, because FRR 10.7.1 answers the
  profile line alone with "has not been set" and stores nothing. lab/bfd.sh
  drives OSPF down through BFD: after the silent cut at 100 ms x 3, bfdd saw
  the session down in 277 ms, the static route left in 277 ms, BGP in 351 ms
  and OSPF (dead time 40 s) in 256 ms.

## What is left after phase 2

* **The IGPs.** `ip ospf bfd`, `ipv6 ospf6 bfd` and `isis bfd` were already
  in the grammar, and before this phase they were inert on every box: nothing
  ran bfdd. They now reach a running bfdd and the commit check counts them,
  but no lab has brought an OSPF or IS-IS adjacency down through BFD yet, and
  their `bfd profile` form is not offered. Unproven until it has.
* **The API.** A typed read of the sessions (`ListBfdSessions`, drawn from
  `Shell.bfd_facts()`, which `show bfd` already prints from), typed BFD
  configuration in `GetConfig` (a `bfd` area: peers, profiles, their timers),
  and an event when a session changes state (#558). In phase 1 the API
  reaches all of it as text: the `bfd` block through `Edit` (with
  `EditRequest.path ["bfd"]`), and both screens through `Show` -
  docs/api-coverage.md lists `show bfd` as `text` for that reason.
* **SNMP.** BFD-STD-MIB (RFC 7331) and a trap on a session going down. FRR
  10.7.1 ships no `bfdd_snmp` module - `ls /usr/lib/*/frr/modules` has one for
  bgpd, ospfd, ospf6d, isisd, ldpd, ripd and zebra, and none for bfdd - so the
  table has to be served by this product's own agent, from `show bfd peers
  json`.
* **The installer.** The next medium SHOULD enable bfdd beside the other four
  daemons, so that the entrypoint is a second line of defence rather than the
  only one.
* **The X520.** The table above is a lab VPP on one thread. The issue asks for
  the shortest stable interval on the X520 under CGNAT load, which is a run
  of `lab/bfd-measure.sh`'s shape on that box.
