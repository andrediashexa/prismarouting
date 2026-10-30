# Routing, SSH, users and SNMP

Everything that is not CGNAT: static routes, OSPF and IS-IS, routing policy, the dataplane card, DNS, SSH, accounts and SNMP — each statement with its argument and effect. All of these changes require the **administrator** capability (`prisma-admin` or root).

# Routing (FRR)

```
router static
router bgp <AS>
router ospf [vrf <name>]
router ospf6
router isis <name>
```

Everything that routes lives under `router`: the protocols, and also the routes nobody learns — the static ones. The protocols are FRR's. Inside each block, `?` and TAB offer the statements this VTY models (the tables below); the rest of the FRR syntax is also accepted, and each line is checked by FRR itself the moment it is typed — a line it does not accept is refused at the prompt, with its answer, and nothing is stored. BGP has its own page: [BGP configuration](/s/wiki-en/doc/bgp-configuration-ykey7SaE44).

`router ospf` and `router ospf6` are **separate instances**, not two halves of the same thing: the first carries IPv4, the second carries IPv6, and a box can run both at the same time. They also do not bring the interface in the same way. OSPFv2 accepts `network <prefix> area <area>` inside the block; OSPFv3 has no `network` at all - what puts the interface in the area is `ipv6 ospf6 area <area>` inside the `interface` block:

```
configure
 router ospf6
  ospf6 router-id 10.0.0.1
  exit
 interface enp1s0
  ipv6 ospf6 area 0
  exit
 commit
```

If you type only `router ospf6` and expect it to find the interfaces on its own, the configuration is accepted and no adjacency comes up: the line on the interface is missing.

## What the `router ospf` block offers

| Command | Argument | What it does |
|---------|----------|--------------|
| `ospf router-id` | `<A.B.C.D>` | this box's identifier in OSPF |
| `network` | `<A.B.C.D/M> area <area>` | runs OSPF on the interfaces this prefix covers |
| `area`  | `<area> ...` | settings for an area, in FRR syntax |
| `passive-interface` | `<interface>` | runs OSPF on the interface without sending hellos — which every subscriber-facing port should be. FRR stores this as `ip ospf passive` in the interface block |
| `default-information originate` | `[always] [metric <n>] [metric-type 1\|2] [route-map <name>\|policy <policy>]` | announces `0.0.0.0/0`; with `policy`, while a route the policy accepts is present ([Routing policy](/s/wiki-en/doc/routing-policy-policy-options-WXZOz2enzp)) |
| `export` | `<policy>` | exports into OSPF what a `policy-options` policy accepts; what no term accepts is **not** exported |
| `redistribute connected\|static\|bgp\|isis` | `[metric <n>] [metric-type 1\|2] [route-map <name>]` | injects routes from another source. `bgp` on an edge box is a whole table inside the IGP: give it a route-map |
| `auto-cost reference-bandwidth` | `<1-4294967>` | in Mbit/s. Without it, every link from 100 Mbit/s up costs 1, and on a 10G box there is no path choice |
| `maximum-paths` | `<1-256>` | how many equal-cost paths to install |
| `log-adjacency-changes` | `[detail]` | logs each adjacency change |
| `timers throttle spf` | `<delay> <initial-hold> <max-hold>` | in milliseconds |
| `distance` | `<1-255>` | administrative distance |
| `neighbor` | `<A.B.C.D> [poll-interval <n>] [priority <n>]` | a neighbor on a non-broadcast network |

## What the `router ospf6` block offers

| Command | Argument | What it does |
|---------|----------|--------------|
| `ospf6 router-id` | `<A.B.C.D>` | this box's identifier in OSPFv3; it has the form of an IPv4 address and is not an address |
| `area`  | `<area> ...` | settings for an area |
| `redistribute connected\|static\|bgp\|isis` | `[route-map <name>]` | injects routes from another source |
| `default-information originate` | `[always] [metric <n>] [metric-type 1\|2] [route-map <name>]` | announces `::/0` |
| `auto-cost reference-bandwidth` | `<1-4294967>` | as in OSPF   |
| `maximum-paths` | `<1-256>` | how many equal-cost paths to install |
| `log-adjacency-changes` | `[detail]` | logs each adjacency change |
| `timers throttle spf` | `<delay> <initial-hold> <max-hold>` | in milliseconds |
| `distance` | `<1-255>` | administrative distance |

There is no `network` or `export` in OSPFv3: the interface comes in through the `ipv6 ospf6 area` line in its block.

## What the `router isis <name>` block offers

| Command | Argument | What it does |
|---------|----------|--------------|
| `net`   | `<49.xxxx.xxxx.xxxx.xxxx.00>` | this box's NSAP address; without it the instance does not come up |
| `is-type` | `level-1\|level-2-only\|level-1-2` | which levels the box participates in; a provider core usually runs `level-2-only` |
| `metric-style` | `wide\|narrow\|transition` | `narrow` stops at 63 and cannot express a topology from 10G up; `transition` sends both during a migration |
| `topology ipv6-unicast` |          | a separate SPF for IPv6 (RFC 5120) |
| `set-overload-bit` | `[on-startup <0-86400>]` | tells everyone not to transit through this box; see draining below |
| `export` | `<policy>` | exports into IS-IS what a policy accepts; what no term accepts is not exported |
| `redistribute ipv4\|ipv6` | `<source> level-1\|level-2 [metric <n>] [route-map <name>]` | injects routes from another source |
| `default-information originate` | `ipv4\|ipv6 level-1\|level-2 [always] [metric <n>] [route-map <name>\|policy <policy>]` | announces a default route |
| `domain-password` | `[clear\|md5] <password>` | level 2 authentication; never shown back |
| `area-password` | `[clear\|md5] <password>` | level 1 authentication; never shown back |
| `log-adjacency-changes` |          | logs each adjacency change |
| `lsp-mtu` | `<128-4352>` | the largest LSP the box generates; larger than the smallest interface MTU, they do not go out |
| `lsp-gen-interval` | `<1-120>` | seconds between LSP regenerations |
| `spf-interval` | `<1-120>` | seconds between SPF runs |
| `max-lsp-lifetime` | `<350-65535>` | seconds an LSP is valid |

The OSPF and IS-IS statements that go **on the interface** (`ip ospf ...`, `ipv6 ospf6 ...`, `ip router isis`, `isis ...`) are in [Interface configuration](/s/wiki-en/doc/interface-configuration-1Pq7NRieCn).

`export <policy>` refuses a policy that depends on what only a BGP route carries — community, AS-path, local-preference, prepend, next hop —, instead of silently ignoring it. Policies are written in `policy-options`, described in [Routing policy](/s/wiki-en/doc/routing-policy-policy-options-WXZOz2enzp); `test policy <policy> <prefix>` says what it does with a route before it is applied.

## Static routes: the `router static` block

A static route needs no protocol at all and is the first thing you configure on a new box:

```
configure
 router static
  0.0.0.0/0 next-hop 200.150.0.1
  192.168.0.0/24 next-hop 172.16.0.1 distance 200 tag 100 description link para o POP-2
  2001:db8::/32 next-hop 2001:db8:0:1::1
  exit
 commit
```

Inside the block:

| Command | What it does |
|---------|--------------|
| `<prefix> next-hop <next-hop\|interface\|blackhole\|Null0\|reject>` | a static route |
| `... distance <1-255>` | the administrative distance; without it the routing one applies |
| `... tag <1-4294967295>` | a numeric tag, which a redistribution policy can match |
| `... bfd [multi-hop] [source <address>] [profile <name>]` | the route leaves the table while the BFD session to the next hop is down (see the BFD section, below) |
| `... description <text>` | your own text about the route, up to 63 characters; always goes last |
| `no <prefix> next-hop <next-hop>` | removes the route, with whatever distance, tag and description it has |
| `exit`  | leaves the block |

`distance` and `tag` can come in any order. **A route is the prefix and the next hop:** typing the same pair again changes that route, it does not create a second one next to it. To change only the description, type the whole route again with the new description.

**The family comes from the prefix.** A prefix with colons is IPv6 and cannot be anything else, so there is no command per family. Asking for the family again would be giving the operator a chance to contradict themselves.

**The description is kept on the box itself**, because routing has nowhere to keep it. It appears in `show configuration` and `show running-config` next to the route, goes into `commit` and `rollback` like any other change, and disappears on its own if the route ceases to exist.


:::warning
**The form** `**route <prefix> <next-hop>**` **inside the block no longer exists.** It is refused at the prompt and also in a saved file. A box whose saved file still has this form refuses the whole file at boot and comes up with no configuration at all. On a box that has a static route, run `save` after upgrading and before restarting: it writes the routes in effect already in the new form. [Taking the box to a new version](/s/wiki-en/doc/taking-the-box-to-a-new-version-spJOdnrvOD) explains.

:::

`commit check` refuses a next hop that is on no connected network:

```
error: this next hop is on no connected network: 172.31.99.1
    router static / 10.55.0.0/16 next-hop 172.31.99.1
    the route would be accepted and never reach the forwarding table
```

If accepted, that route would stay in FRR and never reach the forwarding table.

Note that the refusal names the route **in the block it is in** - `router static / 10.55.0.0/16 next-hop ...` - and not as a loose line. It is so even when the route was typed at the top, in the routing spelling, `ip route ...`: the box shows where the route lives in the configuration, which is where you will edit it. A static route inside a VRF has no top-level spelling at all to look for, and that is why the block appears.

`**router static**` **is the main table.** A VRF's static routes go in the routing's `vrf <name>` block, in its own spelling - `ip route <prefix> <next-hop>` or `ipv6 route ...` -, and the block is closed with `exit` or `exit-vrf`:

```
configure
 vrf red
  ip route 10.55.0.0/16 172.31.99.1
  exit-vrf
 commit
```

`commit check` checks the next hop in **that VRF's** table, not the main one: a next hop that exists only in the main table is refused there, and one that exists only in the VRF is accepted. A VRF the routing does not have active yet - without the `vrf <name> table <n>` that creates its Linux device - has no table to check against; the box says those routes were not checked, and does not refuse.

## BFD: the neighbor's failure in milliseconds

BGP only notices that the neighbor died when the hold time runs out - 90 seconds by default - and a static route never notices: it keeps pointing at a next hop that stopped forwarding. The case that matters is the neighbor that **stops answering with the link up** - a switch in the middle that lost the VLAN, a hung card -, because then no interface goes down to warn anyone. BFD exchanges small packets with the neighbor at millisecond intervals and brings the session down when they stop arriving.

```
configure
 bfd
  profile rapido
   receive-interval 100
   transmit-interval 100
   detect-multiplier 3
  exit
  peer 10.0.0.2 interface enp1s0
   profile rapido
  exit
 exit
 router bgp 65000
  neighbor 10.0.0.2 bfd profile rapido
 exit
 router static
  203.0.113.0/24 next-hop 10.0.0.2 bfd profile rapido
 exit
 commit
```

With 100 ms and multiplier 3, the session drops after 300 ms without a packet. Measured in the lab, with the neighbor silent and the link up: BFD saw the failure in 252 ms, the static route left the forwarding table in 252 ms and the BGP-learned route in 325 ms - with the BGP hold time at 30 seconds. Without BFD, neither left within 12 seconds.

Inside `bfd`:

| Command | What it does |
|---------|--------------|
| `peer <address> [multihop] [local-address <address>] [interface <interface>] [vrf <name>]` | a session with a neighbor; enters its block |
| `profile <name>` | a named set of timers, used by sessions and protocols; enters its block |
| `exit`  | leaves the block |

Inside a `peer` or a `profile`:

| Command | What it does |
|---------|--------------|
| `detect-multiplier <2-255>` | how many lost packets until the session drops (default 3) |
| `receive-interval <10-4294967>` | the slowest interval at which this box wants to receive, in ms (default 300) |
| `transmit-interval <10-4294967>` | the fastest interval at which this box sends, in ms (default 300) |
| `echo-mode` | sends echo packets that the neighbor returns; IPv4 only, unless the neighbor is also FRR |
| `echo receive-interval` and `echo transmit-interval` | the echo intervals, in ms (default 50) |
| `passive-mode` | waits for the neighbor to speak first |
| `minimum-ttl <1-254>` | multihop only: the lowest TTL accepted (default 254) |
| `shutdown` | holds the session down, keeping the configuration |
| `profile <name>` | only inside a `peer`: the session uses that profile's timers; what is configured on the session itself overrides it |

The same `profile <name>` means two things: at the `bfd` level it **creates** the profile, inside a `peer` it **uses** the profile. The prompt shows where you are.

The protocol is what asks for the session: `neighbor <address> bfd [profile <name>]` in BGP ([BGP configuration](/s/wiki-en/doc/bgp-configuration-ykey7SaE44)), and `bfd` after the next hop in a static route. A `peer` in the `bfd` block serves to give the session its own timers, and for it to exist even without any protocol asking.

To see them:

* `show bfd peers` - two lines per session: where it runs (single hop or multihop, the interface, the source, the profile, and whether it was requested by a protocol), and then the state, for how long, the interval, how long until it drops, the diagnostic when there is one and how many times it went down.
* `show bfd peer <address>` - each session with that neighbor in detail: this box's timers and the neighbor's, the detection time, echo, the packets in and out, and how many times it went up and down.

In OSPF, OSPFv3 and IS-IS, BFD goes on the interface, with or without a profile: `ip ospf bfd`, `ip ospf bfd profile <name>` (and `ipv6 ospf6 bfd ...`, `isis bfd ...`). The box writes the `bfd` line on its own before the profile one, because FRR refuses the profile without it.

Sessions are also exposed through the API, field by field and with a notification on each state change, and through SNMP, in the BFD-STD-MIB (RFC 7331), which the box's own agent answers. A session that goes down or comes back generates the MIB's own `bfdSessDown` and `bfdSessUp` notifications (since 0.152.3), for each configured `snmp trap-host`; see [Monitoring and SNMP](/s/wiki-en/doc/monitoring-and-snmp-GxrrkTSEJT).

The BFD configuration is also read and written field by field through the API: the sessions and profiles of the `bfd` block, a BGP neighbor's BFD profile and a static route's BFD. A session is identified by the address, multihop, local address, interface and VRF, as FRR identifies it; removing a session, a profile or the whole block removes everything inside them.


:::warning
**On a box upgraded by a patch version, BFD only works after the reboot.** The sessions are run by `bfdd`, and it is turned on by the routing container when that container comes up on the new version - which, in a patch upgrade, happens at the next reboot. Until then `commit` refuses any configuration with `bfd`, saying that `bfdd` is not running and that the reboot turns it on. It is on purpose: without `bfdd`, BGP and the static route accept the word `bfd` and no session is created - the neighbor would be watched by no one.

:::

## Seeing the table: `show route`, not `show ip route`

Three tables hold routes on this box and only one forwards packets:

```mermaid
flowchart LR
    RIB["FRR RIB<br/>what FRR decided"] --> K["kernel FIB<br/>what FRR installed"]
    K -->|"netlink / linux_nl"| VFIB["VPP FIB<br/>what forwards"]
```

| Command | Answers from | Use when |
|---------|--------------|----------|
| `show route` | **VPP FIB**  | it is the normal question: what this box forwards |
| `show route verbose` | both, compared | traffic does not pass and the route "is there" |
| `show route <prefix>` | both, for one destination | investigating a specific destination |
| `show ip route` | FRR RIB (passed to `vtysh`) | checking what FRR decided, not what the box does |

```
prisma> show route
  Destination    Next hop    Interface Kind
  0.0.0.0/0      200.150.0.1 enp2s0    forward
  200.150.0.0/24 -           enp2s0    connected
  203.0.113.0/24 200.150.0.1 enp2s0    forward

  3 entries carrying traffic
```

The columns are as wide as what the box actually has. When a route does not fit the console — a long IPv6 prefix, for example —, each route becomes a small block, with `via:`, `on:` and `kind:` on their own lines, instead of a table the terminal would break in the middle.


:::warning
`show ip route` answers from the FRR table. On a box that is dropping traffic, the route appears **present and correct** there — because what forwards is the VPP FIB, and FRR's answer says nothing about whether the route got there.

When traffic does not pass and the route looks right, use `show route verbose`: it compares the two and marks where they disagree.

:::

The VPP column says what VPP **has** — `forward`, `connected`, `local`, `neighbour`, `drop` or `absent` — and not yes/no. A prefix VPP drops on purpose and one it has never heard of are different situations, with different causes.

Local addresses and routes via interfaces VPP does not own — the management one, for example — are **not** marked as a disagreement. They are in FRR and absent from the FIB correctly and forever.

## Which daemons run

`zebra`, `mgmtd` and `staticd` always come up. `bgpd`, `ospfd`, `ospf6d` and `isisd` are enabled by the installer - the Debian package ships them off, and a box installed without this offered the protocols in the VTY without running any. `bfdd`, which runs the BFD sessions, is turned on by the routing container itself when it comes up, and that is why it also arrives through a patch upgrade - but only from the next reboot.

`commit check` refuses a `router <protocol>` whose daemon is not up:

```
error: router bgp 65001: bgpd is not running
    the statement would commit and reach nothing. Enable bgpd in
    /etc/frr/daemons and restart frr
```

The boot self-test reports which ones are responding.

## Draining the box for maintenance: the overload bit

It is IS-IS draining. With it on, the box tells everyone **not to transit through it**: what terminates on it is still reached, but it stops being a transit path — which is what you want before touching equipment in production.

```
configure
 router isis HEXA
  set-overload-bit
  exit
 commit
```

`no set-overload-bit` removes it. It also accepts `set-overload-bit on-startup <0-86400>`, which carries the bit for that many seconds after the protocol comes up — so the box that just restarted does not attract transit traffic before it has converged.

The failure mode of this feature is not turning it on: it is **forgetting it on**. A drained box works, serves, answers everything you ask, and does not carry the traffic it should — for weeks, if nobody looks.

Hence the check:

```
show isis adjacency
```

When it is on, the screen says `overload bit set`.


:::info
That line is read from the **LSP this box is flooding**, not from the configuration that asked for the bit. The difference is the point: a drain reported by the words that requested it is reported by a line that may not have taken effect. What the screen states is what the neighbors are receiving.

On the neighbor's side, the same fact appears in `show isis database`, in the last `ATT/P/OL` column of this box's LSP line: `OL` is the third of the three, and reads `0/0/1` when the bit is on.

:::

# Three routing traps

## 1. Inherited MTU stalls OSPF

The `linux_cp` TAP comes with the MTU of the dataplane interface: 1500, the box default since 0.152.1, or 9000 on a box installed before it. An interface that inherits this value without the other side agreeing leaves the OSPF adjacency stuck in Exchange. Adjust it with `mtu` inside the `interface` block — it changes the dataplane interface **and** its TAP, together.

The same goes for OSPFv3: it also compares MTU before completing the adjacency, and stalls the same way, with no log saying why. `commit check` warns when the two halves of the interface disagree; `ipv6 ospf6 mtu-ignore` is the way out when the two sides cannot be matched.

## 2. IS-IS does not run over IP

IS-IS runs directly over the link, not over IP. That changes what must be right for the adjacency to come up, and a diagnosis done by IP address does not see the problem.

## 3. The CGNAT eating its own control plane


:::warning
**If a public pool contains a peering address, the BGP session drops intermittently with nothing obvious anywhere.**

BGP sessions and OSPF/IS-IS adjacencies terminate on **the box's own** addresses. `bpa-out2in` runs on the `ip4-unicast` arc, before `ip4-lookup` and therefore before `ip4-local` decides the packet is local — that is, it sees the BGP packet first.

:::

Two defenses, both mandatory in the product:


1. Pool creation **is refused** when the public set contains an address configured on a local interface. The error names the address and the interface.
2. An address configured **after** the pool cannot be refused, so it is reported at error level in `show logging` — the operator reads that, not the VPP process's stderr.

In practice, pool addresses are normally **not** interface addresses: they reach the box via a route from upstream, not via local configuration.

# Interfaces

The interface statements that matter for routing — `mtu`, which changes the dataplane interface **and** its TAP together, `urpf strict|loose|off`, and the OSPF and IS-IS lines on the port — are with the rest of the block in [Interface configuration](/s/wiki-en/doc/interface-configuration-1Pq7NRieCn). uRPF on an inside interface is what prevents a spoofed source from being translated.


:::info
**Every interface with an** `**interface**` **block exists for routing under its own name.** There is no line to write: the pair routing sees is created on its own. `linux-name` only serves to give it **another** name. A port that "is configured and talks to nobody" is a different matter; the boot self-test says which ports were left without a pair, if any.

:::

# System: the card handed to the dataplane

```
configure
 system
  dataplane
   interface enp2s0
   exit
  exit
 commit
```

| Command | Where | What it does |
|---------|-------|--------------|
| `interface <card>` | `system` > `dataplane` | hands this card, by the name **Linux** gives it, to the dataplane at its next start; a name Linux does not have is refused with the list of the ones it has |
| `no interface` | `system` > `dataplane` | the box stops taking a card at the next dataplane start |
| `control-plane`, `main-thread`, `workers`, `cgnat-workers` | `system` > `cpu` | the cores per role |
| `control-plane`, `buffers` | `system` > `memory` | the memory per role |
| `unsupported-sfp allow` | `system` > `hardware` | the ixgbe card (X520 and family) accepts an SFP+ module Intel has not qualified; `no unsupported-sfp` returns to the default, which refuses |
| `host <address> [...]` | `system` > `logging` | sends the box's event log to a syslog; takes effect **at commit** (see below) |
| `vrf <name>` | `system` > `management` | the box's own services move into the management VRF; takes effect **at commit** (see below) |

All of them, except `logging` and `management`, take effect from the next boot or the next dataplane start: `commit` saves the choice and **restarts nothing**. `cpu` and `memory` are explained in [Sizing and requirements](/s/wiki-en/doc/sizing-and-requirements-IHPnUSMSom), and the choice of card in [Interface configuration](/s/wiki-en/doc/interface-configuration-1Pq7NRieCn).

## Unqualified SFP+ module on an ixgbe card

The `ixgbe` driver (Intel X520, X540, X550 and family) refuses the whole card when the SFP+ module is not on its list. The card disappears from Linux and does not reach the dataplane, and the boot journal says:

```
ixgbe 0000:01:00.0: failed to load because an unsupported SFP+ or QSFP module type was detected.
```

With a third-party module, which is common, or with a module that is sometimes misread, turn on acceptance:

```
configure
 system
  hardware
   unsupported-sfp allow
   exit
  exit
 commit
```

It takes effect from the next boot: the box puts `ixgbe.allow_unsupported_sfp=1` on the kernel command line, which the driver reads when it loads. `show system hardware` shows what is configured and what the current boot has. Intel warns that a module it has not tested may be unstable; that is why it ships off.

## Where the event log goes: `system` > `logging`

What `show log` shows — commits, restarts with their cause, links, modules, BGP and BFD sessions, pools without blocks, logins — also goes to up to four syslog collectors:

```
configure
 system
  logging
   host 192.0.2.10
   host 192.0.2.11 protocol tcp severity warning
   exit
  exit
 commit
```

| Option | Default | What it does |
|--------|---------|--------------|
| `port <n>` | 514 on UDP, 601 on TCP | the collector port |
| `protocol udp\|tcp` | `udp`   | UDP: one datagram per event. TCP: a stream, with octet-counting framing (RFC 6587) |
| `severity error\|warning\|notice\|info` | `info` (everything) | the minimum level sent: that one and the more severe ones |
| `source-address <address>` | the route's | the source address |
| `vrf <name>` | the default table | the VRF it goes out through; it must be a `vrf <name> table <n>` in the configuration |

`no host <address>` stops sending to it; a second `host` with the same address replaces the first. The collector is an **address**, never a name: a box whose DNS went down is precisely the one that most needs to send its events. It takes effect at `commit`, restarting nothing.

Each event goes out in RFC 5424, facility `daemon`, with the event type (`LINK`, `BGP`, `COMMIT`...) in the MSGID and the fields as structured data:

```
<28>1 2026-10-07T00:01:01.250Z edge-1 prisma-routing - LINK [event@67033
source="dataplane" kind="link" subject="memif1/0" reason="link"] interface
memif1/0: link down, administratively up: the wire or the far end
```

**Nothing is lost if the TCP collector goes down.** The sender is the box's event watcher, working from its own `events.log`, which serves as the queue: each destination keeps track of how far it has sent, and what waited goes out when the collector comes back — without repeating —, even after restarting the watcher or the box. `show system logging` gives each destination and whether it is getting through:

```
192.0.2.10:514 udp  info and above
  sending
  sent 3 event(s) since the watcher started, the last at 2026-10-07 00:01:01
192.0.2.11:601 tcp  warning and above
  not reached: connection refused; tried again every 30 s
  sent 0 event(s) since the watcher started
```

**Do not confuse it with the CGNAT export.** `nat` > `cgnat` > `logging syslog` sends the records of who used which public port, for court orders, and stays the same. They are two configurations and two separate streams — neither carries the other's lines — and they should go to different collectors.

## Management in a VRF of its own: `system` > `management`

Since 0.152.4, management can live in a VRF of its own, so that the main table is free for the subscribers' default while SSH, the API and SNMP stay reachable through the management gateway. The management card goes into a VRF like any interface, with its routes in the `vrf <name>` block, and `system` > `management` > `vrf <name>` makes the box's own services follow it: without it, the card is in the VRF and the box does **not** answer on it. With it, SSH, the API and SNMP answer on the VRF's addresses, and what the box itself originates - DNS, NTP, `system upgrade`'s downloads, SNMP notifications, `system` > `logging` - leaves by management's routes, unless the main table has a specific route to the destination. IPFIX over UDP, which the dataplane sends, still leaves through the main table.

It takes effect at `commit` and again at every boot. `commit check` refuses a VRF the configuration does not create with `vrf <name> table <n>` and, in the commit that turns it on, a VRF with no interface in it. `show system management` shows the configured VRF and what the system has in effect. The full example, and the case of a management card only Linux has (a virtual machine's virtio card), are in [Interface configuration](/s/wiki-en/doc/interface-configuration-1Pq7NRieCn).


:::warning
**Use** `**commit confirmed**`**.** If the session that committed loses access, nobody confirms, and the rollback takes the card out of the VRF and gives management back to the main table. Taking management out of the VRF ends the SSH sessions opened through it; the box answers new ones on the main table.

:::

# SSH

```
configure
 ssh port <1-65535>
 ssh listen-address <address> [<address> ...]
 no ssh listen-address
 ssh permit-root-login yes|no|prohibit-password|forced-commands-only
 ssh password-authentication yes|no
```

| `permit-root-login` value | Meaning |
|-------------------------|---------|
| `yes`                   | password or key |
| `no`                    | not at all |
| `prohibit-password`     | key only |
| `forced-commands-only`  | forced commands only |

| Setting | Default |
|---------|---------|
| `ssh port` | `22`    |
| `ssh listen-address` | `0.0.0.0 [::]` (all addresses, IPv4 and IPv6) |
| `ssh permit-root-login` | `prohibit-password` |
| `ssh password-authentication` | `yes`   |

`listen-address` must be an address: anything else is refused at the prompt, because SSH would refuse its own file and not come up — on a box whose other path is a serial cable.

**Since version 0.152.5,** `**ssh listen-address**` **accepts a list**, IPv4 and IPv6 together, like `snmp listen-address` and `api listen-address` since 0.151.8:

```
configure
 ssh listen-address 192.0.2.166 2001:db8:1000:6::166
 commit
```

SSH then answers on each address in the list, and only on them. `show running-config` shows the IPv6 one in brackets, as sshd itself writes it: `ssh listen-address 192.0.2.166 [2001:db8:1000:6::166]`.

* **Without** `**listen-address**` — or after `no ssh listen-address` — SSH answers on all of the box's addresses, **IPv4 and IPv6** (`0.0.0.0 [::]`), which is sshd's own default. Up to 0.152.4, any `commit` that touched `ssh` wrote `ListenAddress 0.0.0.0` and SSH over IPv6 stopped answering.
* **An address the box does not have is refused at** `**commit**`, before sshd is notified: `error: ssh listen-address 192.0.2.99: not an address of this box`. An address of an interface that is in a VRF is only accepted when management is in that VRF (`system` > `management` > `vrf`), which is what allows sshd to listen on it.
* A `0.0.0.0` (or `::`) together with an address of the same family is refused: the wildcard already listens on all.
* **A configuration saved before 0.152.5 loads the same.** A single address remains a single address. A box that never had an `ssh` `commit` saved `ssh listen-address [::]` — the first address of sshd's default, read on its own; loaded on a box where sshd is at the default, that line still means IPv4 and IPv6, instead of removing SSH over IPv4 at boot.

`**commit**` **refuses a configuration in which no one can log in over SSH anymore**, or in which no one who logs in can change anything — removing the role of the last administrator, for example. The console does not count: it may not be within reach of whoever is configuring.

# Box name (hostname)

```
configure
 hostname cgnat-sp1
 commit
```

The box's name, in a single place: the Linux one (what `hostname` answers, `/etc/hostname` and the `127.0.1.1` line of `/etc/hosts`) and the `sysName` SNMP answers. Before version 0.145.0, changing the name meant editing those files by hand.

The name follows hostname rules: letters, digits and hyphens, with dots separating the parts, up to 64 characters, and **starting with a letter** — FRR refuses anything else. `no hostname` returns to the installation's default name, `prisma-routing`.

`show running-config` shows the name the box has now. A configuration saved before 0.145.0 does not have this line and does not change the box's name when loading; the first `save` after the upgrade starts writing it.

From version 0.148.0 **the prompt shows the box's name**: `cgnat-sp1>` in operational mode and `cgnat-sp1# [edit]` in configuration mode. It changes right after the `commit` that renames the box, in the same session. A box with the installation's default name (`prisma-routing`) keeps showing `prisma>`, as in this guide's screens. A name longer than 20 characters appears cut, with a `~` at the end.

The FRR daemons receive the name at `commit`, but keep showing the name they were started with.

# DNS

```
configure
 dns server 1.1.1.1
 dns server 2606:4700::1111
 commit
```

The DNS servers the box queries, without editing `/etc/resolv.conf` by hand. Up to three, queried in the order they were typed. A fourth is refused, because the system only queries the first three and a fourth would appear in the configuration without ever being used. `no dns server <address>` removes one.

**On a box with DHCP management, DHCP stops touching DNS while there is a** `**dns server**` **configured.** Without that, the next lease renewal would put back the DHCP servers, without warning. Once the last `dns server` is removed, the box goes back to using what DHCP (or the installation) had defined.

`show running-config` shows the `dns server` entries configured through the VTY. The servers received from DHCP do not appear there, because they are not your configuration.

**A command over ssh lands in the VTY.** `ssh <account>@<box> "show cgnat pool"` is executed by the VTY, with the account's role. Only root and administrators (`prisma-admin`) get a system shell, whether by direct command, by `scp`/`rsync` or when the VTY fails. A CGNAT or read-only account that tries a system command gets an explanation and the session ends.

# User accounts

```
configure
 user <name>
  role administrator|cgnat-operator
  operator
  ssh-key <type> <key> [comment]
  password <hash>
  no role
  no operator
  no ssh-key <key>
  no password
  exit
 no user <name>
```

| Command | What it does |
|---------|--------------|
| `role administrator\|cgnat-operator` | what the account can change; without a role, read-only |
| `operator` | the account may upgrade, reboot and shut down the box and restart the dataplane; `no operator` takes it away |
| `ssh-key <type> <key> [comment]` | an authorized public key, as it appears in `authorized_keys`; can repeat |
| `password <hash>` | a crypt hash; clear text is refused |
| `no ssh-key <key>` | removes a key; its beginning is enough |
| `no user <name>` | removes the account; `root` cannot be removed — restrict it with `ssh permit-root-login` |

The hash appears as `password <set, not shown>` in `show this` and `show configuration`.

`**password**` **takes a crypt hash, never a clear-text password.** Generate it with:

```sh
openssl passwd -6
```

## `role` — what the account can change

| Value | What the account can change |
|-------|-----------------------------|
| `role administrator` | routing, CGNAT, accounts and the machine itself |
| `role cgnat-operator` | only CGNAT configuration    |
| no `role` | read-only                   |

**The configuration is what decides.** The role is written in the user block, appears in `show running-config` and survives `save` — and `commit` materializes it in the system groups, adding the ones the role requires and **removing** the ones it no longer requires. A promotion made from outside, directly in the group, is undone at the next commit: this is where it is written.

Besides the role's group, every account created here joins the groups that give **reach** — the dataplane sockets and the routing ones. Without them the account has rank and cannot exercise it: every command fails on permissions, including read ones. That is why reapplying the role through the VTY fixes an account whose memberships drifted.

**Upgrading, rebooting, shutting down the box and restarting the dataplane come from no role.** Since 0.149.26 it is `operator`, beside the role: it grants exactly `system upgrade <version>`, `system reboot`, `system shutdown` and `system dataplane restart`, at the VTY and through the API, and nothing else of root's. It goes with any role, or none: a NOC account that only reads the configuration can be the one that reboots the box. And an administrator without `operator` is refused those four commands, by name. `root` can already do all of this and is not given `operator`.

`no role` returns the account to read-only. A role that does not exist is refused with the list of what exists:

```
error: role 'supervisor': expected one of administrator, cgnat-operator
```

And `root` does not get a role — its rights come from being root, not from a group:

```
error: root's rights come from being root, not from a group
    this VTY does not manage root's memberships
```

The verdict is on the first line and the reason on the second, within the console width. As a single sentence it went past a hundred columns and the terminal broke it wherever it happened to.

The refusal applies to any role given to `root`, including the correct name of a role that exists: it is not the role that is wrong, it is the account.

# SNMP

The agent is **read-only by construction** — there is no write path to disable.

```
configure
 snmp enabled yes|no
 snmp listen-address <address> [<address> ...]
 snmp port <1-65535>
 snmp enterprise-number <1-4294967295>
 snmp community <string>
 snmp trap-host <address> community <string> [inform]
 no snmp enabled|listen-address|port|enterprise-number
 no snmp community <string>
 no snmp trap-host <address>
```

| Command | What it defines | Default |
|---------|-----------------|---------|
| `snmp enabled` | runs the agent or not | `no`    |
| `snmp listen-address` | one or more listen addresses, IPv4 and IPv6 | `127.0.0.1` |
| `snmp port` | UDP port        | `161`   |
| `snmp enterprise-number` | the number under which the Prisma MIB is served; `no snmp enterprise-number` returns to the product's | `67033`, Prisma's IANA number |
| `snmp community` | an SNMPv2c community, read-only; there can be more than one | none    |
| `snmp trap-host` | where to send notifications: the address, the community and, with `inform`, an acknowledged notification instead of a trap; one per address, and typing the same address again replaces it | none    |

With the default `127.0.0.1`, the agent only answers from inside the box: for the NMS to reach it, `listen-address` must be an address it can reach.

To answer on IPv4 and IPv6 at the same time, list both addresses on the same line, separated by a space — `snmp listen-address 192.0.2.166 2001:db8:1000:6::166`. `api listen-address` accepts the list the same way. Listening on `0.0.0.0` or `::` is not the way: it would open the service on the dataplane interfaces too, including the public ones.

`no snmp <setting>` puts the setting back to the table's default.

The agent answers **SNMPv2c and SNMPv3**, never v1: a community is valid only in v2c, which is the version with the 64-bit counters the interfaces and the product's MIB use.

**Turning on the agent with no community and no v3 user is refused**: it would be an agent nobody can query. The community and the v3 users' passwords appear as `<set, not shown>` in `show configuration`.


:::info
The Prisma MIB is served under the product's IANA number, **67033**, with nothing to configure (since 0.149.13). `snmp enterprise-number <number>` serves it under your organization's number; see [Monitoring and SNMP](/s/wiki-en/doc/monitoring-and-snmp-GxrrkTSEJT).

:::

For the NMS, the box hands over its own files: `show snmp mib` prints PRISMA-ROUTING-MIB already under the box's enterprise number, to load into the NMS, and `show snmp zabbix-template` prints a Zabbix 7.0 template with the same number, to import - the items, the triggers and an item for each notification the box sends.

## SNMPv3 users

```
configure
 snmp-user <name>
  auth <sha|md5> <passphrase>
  priv <aes|des> <passphrase>
  exit
```

The agent only serves a v3 user with authentication **and** privacy: configure both, `auth` and `priv`.

## Routing policy: what the VTY checks

Prefix-lists and route-maps are written in routing syntax and stored by it. The VTY completes the names the box already has:

```
prisma# [edit] ip ?
  prefix-list  a named list of prefixes, for matching in policy
prisma# [edit] ip prefix-list <TAB>
  PL-CLIENTES   ip prefix-list
```

Completion comes from the same source `show running-config` answers from, so it does not offer a name the box does not have.

Three things about a prefix-list are the box's, on top of the routing's syntax: an entry takes `description <text>` at the end, kept by the box because the routing has no description per entry; `ip prefix-list <list> insert seq <n> before|after seq <m>` moves an entry, Junos-style; and `ip prefix-list <list> resequence` renumbers the list by 10. The descriptions move with their entries in both. To rename a prefix-list, a route-map or a BGP peer group everywhere it is named, use `rename` in configuration mode.


:::warning
**A policy needs the routing daemons up.** Prefix-lists and route-maps belong to no protocol, so it is not `bgpd` that stores them — and with the owning daemon stopped, routing **accepts the line and stores nothing**, without error.

`commit` refuses in that case and says which daemon is missing and which file to edit:

```
error: 2 policy statement(s) need mgmtd, which is not running
    first one: ip prefix-list PL-CLIENTES seq 10 permit 200.150.0.0/24
    they would commit and store nothing. mgmtd is started by frr itself, not
    listed in /etc/frr/daemons - so frr did not finish starting. 'systemctl
    restart frr' and read its journal
```

The advice is **different** from what comes out for `bgpd`, and on purpose: `zebra`, `mgmtd` and `staticd` are not in `/etc/frr/daemons`; `frr` itself brings them up. If one of them is not up, there is no line to enable - what happened is that `frr` did not finish coming up, and the answer is in its journal. Static routes answer with the same sentence, swapping `staticd` for `mgmtd`.

A `router bgp` stanza that goes nowhere gets noticed: no session comes up. A prefix-list that goes nowhere is invisible until the traffic that should have been filtered is not.

:::

Routing warnings now reach the operator even when `commit` succeeds:

```
frr: The route-map 'RM-CLIENTES' does not exist.
commit complete
```

Before, that line was discarded, and the person walked away with a policy pointing at nothing.

## What `commit complete` has come to mean

After applying, the box **reads back the configuration FRR is running** and compares it with what you wrote. If a statement this commit introduced is not there, it refuses and says which ones.

This exists because the tool that applies the configuration can finish successfully having discarded a line. The symptom is the worst there is: you leave the maintenance window thinking you applied it, and the problem shows up days later, with nobody connecting one thing to the other.


:::info
**Not everything FRR accepts does it store, and that is not an error.** Three forms, all measured on this box and not deduced:

* **It omits.** `ip ospf hello-interval 10` is its default, so it accepts and does not write it. But `ip ospf dead-interval 40` is also the default and that one it writes — that is, **you cannot predict by the value**; it is statement by statement.
* **It rewrites.** `passive-interface vppwan0` comes back as `ip ospf passive`, inside the interface stanza and not the router's. `continue 20` comes back as `on-match goto 20`. FRR itself warns when you type the first one.
* **It had already turned it on.** `neighbor 10.0.0.2 activate` in an ipv4-unicast family is implicit, so it accepts and writes nothing.

The box knows these forms and does not complain about them. You do not need to change how you write.

:::

A route-map or prefix-list line is treated more strictly. If the routing accepted the syntax and the daemon that applies the policy refused the line, `commit` says `FRR refused <n> line(s) of your route-maps and lists`, lists the lines and **goes back to the previous state**. A clause that lost a `set` or a `match` keeps running, doing what nobody wrote, so the box undoes the commit instead of only warning.

If a refusal appears here, the reading is: **what this box stores and what FRR is running are not the same thing.** `show configuration` shows this box's; `start shell` and then `vtysh -c "show running-config"` shows FRR's.

# AS-path and community lists

These are what a route-map consults in `match as-path`, `match community` and `match large-community`.

```
bgp as-path access-list AS-CLIENTES permit ^65001(_65001)*$
bgp community-list standard CM-NAO-EXPORTAR permit 65000:666
bgp large-community-list standard LC-REGIAO permit 65000:1:1
```

`?` and TAB complete the names that already exist on the box, in both places: where the list is created and where the route-map consults it.

## An expression that does not compile is refused immediately

FRR compiles the expression **at apply time**, that is, in the middle of the commit. Here it is compiled when you type it:

```
error: the regular expression does not compile: missing ), unterminated
    subpattern at position 6
    bgp as-path access-list AS-BAD permit ^65001(
```

## An expression that compiles and matches the wrong thing

This is the common mistake, and it **cannot** be refused — the expression is legitimate. `permit 65001` compiles, is valid, and also matches `165001` and `650012`: without `^` and `$`, an exact comparison becomes a substring comparison.

And FRR's `_` is not the underscore character: it means **any boundary** — start, end or separator between ASNs.

Reading the expression back does not show this. Counting against the paths the box **actually received** does:

```
note: AS-LOOSE '65002' matches 2 of 5 received AS path(s); e.g. '65001 65002'
```

If the number comes out much larger than you expected, an anchor is missing. If it comes out zero, the list exists and filters nothing.


:::info
When the box has not yet received any path, the equipment says so instead of answering zero. "Matched 0" and "there was nothing to match against" are different answers, and only one of them speaks about your expression.

:::

## A name nobody defined gets a warning

```
warning: no AS-path list called 'AS-CLIENTES' is defined; until one is, the
    clause matches nothing
    match as-path AS-CLIENTES
```

The clause commits and matches nothing while the list does not exist. **It is not a refusal**: routing accepts the clause and starts using it on its own the moment the list appears, so writing the policy first and the lists afterwards is a legitimate work order — and refusing the commit because of that would also take down any other change that was in it.

The warning is still worth reading: if the list is not going to appear, the name has a typo and the clause matches nothing forever — and nothing else on the box says anything about it.

Both spellings of a community list count as a definition: `bgp community-list standard NAME` and `bgp community-list NAME`.

## How to verify

* `commit check` shows the counts and the refusals without touching the box;
* `show bgp ipv4 unicast` shows the received paths, which is what the count was made against;
* `show route` shows what **forwards** — if the policy was meant to remove a route, that is where it disappears.

# Route-maps: the policy itself

A route-map is a list of numbered clauses. Each clause says **which routes it applies to** (`match`) and **what changes in them** (`set`). The first clause that matches decides; the ones below are not consulted.

```
ip prefix-list PL-MEUS-BLOCOS seq 10 permit 200.150.8.0/22

route-map RM-TRANSITO-OUT permit 10
 match ip address prefix-list PL-MEUS-BLOCOS
 set as-path prepend 65000 65000
exit
route-map RM-TRANSITO-OUT permit 20
exit

router bgp 65000
 address-family ipv4 unicast
  neighbor 200.150.0.2 route-map RM-TRANSITO-OUT out
 exit-address-family
 exit
```

`?` and TAB inside the clause list what `match` and `set` understand, and complete names that already exist on the box: prefix-lists, AS-path lists, community lists, other route-maps and neighbors.

## What is refused and what is only stated

**A** `**deny**` **clause with** `**set**` **is refused.** The `set` never runs — the route was denied. It is a mistake every time.

**A** `**permit**` **clause without** `**match**` **gets a warning, not a refusal.** It lets everything through. This is a common and intentional way to end a policy — and it is also exactly what a half-finished policy looks like. The equipment cannot tell them apart, and nobody can afterwards, so it speaks up:

```
warning: no 'match': this permits every route. If that is meant, it is the end
    of the policy; if not, the policy is unfinished
    route-map RM-TRANSITO-OUT permit 20
```

If it was intended, the warning is one line of noise. If it was not, it is the difference between a policy that filters and one that lets everything through.

`**set local-preference**` **with a value that is not a number is refused.** Routing's grammar for this clause accepts **any word**: `set local-preference banana` is accepted at the prompt, passes `commit check` and ends up in BGP's running configuration — where it adjusts nothing. The clause sits there, looking like policy, doing nothing:

```
error: 'banana' is not a local-preference: expected 0-4294967295. FRR takes
    this statement with any word in it and then sets nothing
    set local-preference banana
```

The equipment refuses here because routing refuses nowhere. It is the opposite of `maximum-paths` and `set weight`, where routing is the authority and refuses at `commit`: there the equipment only states the range and leaves the refusal to whoever is in charge.

The refusal **only comes out for what this change is asking for**. One of these `set local-preference` lines the box was already carrying is not refused again on every commit: a box with such a line in the running configuration would have every commit refused, including those that have nothing to do with route-maps, and refusing a change because of a state that was already there locks the operator out of their own equipment.

**A clause no route reaches gets a warning.** If clause 10 lets everything through, 20 is never consulted. The sequence numbers are yours and the equipment renumbers nothing — but a policy that looks right and does nothing deserves a sentence.

What governs the order is the **sequence number**, not the order in which the clauses were typed. Inserting a clause 5 after there is already a 10 that lets everything through puts the new one in front, and that is what the numbers are for: the equipment reads in the same order as routing and warns about nothing.

`on-match next` and `continue` change that path — the clause that matches does not end the policy, the reading continues. In a route-map that has one of these, the equipment no longer says which clauses are reachable: where the reading goes depends on whether the clause matched or not, and that is not in the configuration.

## What was not checked appears marked

Most of a route-map still passes straight through to routing. Without a mark, whoever reads the configuration cannot tell what was checked from what was only passed along — and ends up reading a guarantee into both:

```
route-map RM-CLIENTES permit 10
 match ip address prefix-list PL-CLIENTES
 set tag 100  <- not checked
```

The mark is display only: it does not go into the saved configuration, nor into a rollback revision, nor to routing. And if you copy the screen to a file and load it back, it is stripped on reading — the screen and the file do not diverge.

## How to verify

* `show running-config` shows the route-maps as the equipment reads them back, with what was not checked marked;
* `commit check` shows the warnings without touching the box;
* `show route` shows what **forwards** — if the policy was meant to change the path, this is where the change appears.

### Prefix range: `ge` and `le`

In a prefix-list entry with `ge` and `le`, `mask length ≤ ge ≤ le ≤ 32` (or `≤ 128`, in IPv6) must hold. When it does not, the line is refused at `commit` and at `commit check`, with the statement named:

```
error: prefix range refused for 10.0.0.0/8: mask length <= ge <= le <= 32 does
    not hold (length 8, ge 24, le 16)
    ip prefix-list PL-CLIENTES seq 10 permit 10.12.0.0/8 ge 24 le 16
```

The refused statement goes on a **line of its own**, in full, below the reason - that is how it stays searchable on the `show configuration` screen, which is where you will fix it.

Note that in the reason the prefix appears normalized - `10.12.0.0/8` becomes `10.0.0.0/8`, because `/8` only looks at the first octet. The line below carries the statement as you wrote it, and that is what you search for.


:::info
This refusal happens **before** the line reaches routing. It is on purpose: refused further along, it also brought down the next commit, with the same message pointing at a prefix that was in no configuration at all — neither the running one nor the candidate. Whoever saw that would look for the prefix in their own configuration and find nothing to fix.

:::

## A pool's public prefix must be announced

```
warning: pool 'clientes': nothing here announces 200.150.0.16/28
    subscribers behind it would be translated to an address the internet cannot route back to
    announce it from another box, or originate it here with
    'ip route 200.150.0.16/28 blackhole' and 'network 200.150.0.16/28' under the BGP address family
```

The last two lines are the actionable part: the warning not only says an announcement is missing, it gives the two lines that fix it when this is where you want to announce.

This is the only check that crosses the two halves of the equipment. Configure CGNAT right, configure BGP right, and the service can be dead anyway: if nothing announces a prefix that **covers** the pool's public side, every subscriber behind it is translated to an address the internet does not know how to route back to. Traffic goes out and nothing comes back.

The two halves are configured in different places, by different people, on different days — and nothing tied one to the other.


:::info
The warning understands **coverage**, not equality: announcing `200.150.0.0/24` covers a pool on `200.150.0.16/28`.

It **does not refuse** the commit — announcing from another box is a legitimate design. And it **stays silent** when there is `redistribute`, because then it cannot know. A warning that fires on a box that works gets ignored, and the next, true one gets ignored along with it.

:::

# Management API (gRPC)

From version 0.148.4 the box offers a management API over gRPC (`prisma.v1`), for the web interface and other systems that need to configure and read the box without going through the terminal. It **is not a second way to configure**: each configuration call is the VTY itself at work — `configure`, the lines you would type, `commit check`, `commit`, `commit confirmed`, `rollback` and `save` —, so a refusal through the API is the same refusal as in the VTY, with the same words, and a change made through the API appears in `show running-config`.

It ships **off**. To turn it on, you need three things:


1. **The certificates.** The API only accepts mutual TLS: the box has its own certificate and only serves whoever presents a client certificate signed by the certificate authority (CA) you specify. Put the files `server.key`, `server.crt` and `clients-ca.crt` (the clients' CA) in `/etc/prisma-routing/api/`. With OpenSSL, on any machine (the CA key, `ca.key`, stays off the box):

```sh
# the CA that signs the clients
openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -subj "/CN=prisma clients" -keyout ca.key -out ca.crt
# the box's certificate, with the address the client uses
openssl req -newkey rsa:3072 -nodes -subj "/CN=prisma-api" -keyout server.key -out server.csr
printf 'subjectAltName=IP:192.0.2.166\n' > server.ext
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 -extfile server.ext -out server.crt
# the client certificate: the CN is the box account it uses
openssl req -newkey rsa:3072 -nodes -subj "/CN=webui" -keyout webui.key -out webui.csr
printf 'extendedKeyUsage=clientAuth\n' > client.ext
openssl x509 -req -in webui.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 -extfile client.ext -out webui.crt
```

On the box go `server.key`, `server.crt` and `ca.crt` named `clients-ca.crt`; the client uses `ca.crt`, `webui.crt` and `webui.key`.


2. **The account.** The client certificate says, in the CN field, **which box account** it is. The call has exactly the permissions that account has in the VTY: `prisma-admin` configures everything, `prisma-cgnat` only CGNAT, and any other only reads; an account with `operator` also upgrades, reboots and shuts down the box and restarts the dataplane. Everything it changes is recorded in the history in its name.
3. **The address.** The API listens **only on the management network**:

```
configure
api enabled yes
api listen-address 192.0.2.166
api port 50051
commit
```

Turning it on without giving the address is refused at commit:

```
error: api enabled yes needs 'api listen-address <management address>'
```

To turn it off, `no api enabled` and `commit`.

What the API offers, in short: open a copy of the configuration (the candidate), edit with VTY lines, compare, validate, apply (including with confirmation), confirm, roll back a revision and save; read interfaces, pools, subscribers, BGP, OSPF, IS-IS, VRRP and bundles; run any `show`; and receive live the interface counters and the CGNAT totals. If someone changes the configuration while your candidate is open, that commit is refused showing the difference, instead of overwriting it.

Since 0.149.2 it also:

* **lists the routing table** already split into fields (VRF, prefix, next hops, protocol, distance, metric, whether it was selected), in pages, with filters by VRF, family, prefix and protocol — designed for the full table: the first page arrives while the rest is still being read, and the whole list is a single snapshot, with no route repeated or missing even if the table changes midway. And it gives the count per VRF and protocol;
* gives the box's **memory** in numbers and the **load of each dataplane worker** — packets per call and the fraction of time spent on packets, because the CPU of a polling worker is always at 100%;
* gives each interface's **link speed**, to compute utilization in %;
* keeps **24 hours of history** of the counters, one sample per minute, which survives an API restart (an upgrade, for example): the web interface graph does not start over from zero;
* puts the **revision** and the **comment** in each commit notification, so the client knows which commits were its own;
* answers the standard gRPC **health check**, with the same client certificate;
* tells the caller **which account it is and its role** (administrator, CGNAT operator or read-only), without opening a candidate.

It also reads **as fields the rest of the CGNAT screens**: who was behind a public address and a port, and whether the answer holds for any instant (the deterministic pool, by the formula) or only for now (a dynamic or reserve block, which has already belonged to someone else); the subscribers closest to exhausting their blocks; the drops; the memory and how many sessions still fit; each worker's blocks; how many identities the box has (never an identity); the configuration trail verification; the ALGs enabled on each pool; the port forwards; and whether this box and the HA peer map subscribers the same way. On a NAT64 pool the subscriber comes as their IPv6 prefix. Each response also carries the VTY screen, the same as the terminal shows, because it is the same read.

And it reads as fields **the screens that were missing**: `show ospf interface` (each interface with the area, the state, the cost, the neighbors, the MTU OSPF announces next to the dataplane MTU, and whether the two stall the adjacency), `show isis interface` (the circuits, those without a neighbor and whether the dataplane is delivering IS-IS to the system), `show hardware` (the cards Linux has, which one carries the default route and what the dataplane has already taken) and `show logging` (the log configuration of each routing daemon). Since 0.151.14 it also reads `show log` — the record of what happened on the box, with the same filters — and `GetSystem` says why the dataplane and routing last started (`boot`, `operator`, `upgrade` or `unexpected`), so the web interface does not need to guess "it restarted on its own". Since 0.152.2 it follows the log live (`WatchEvents`, the API's `monitor log`), configures the syslog destinations of `system` > `logging` and reads, in `GetSystem`, whether each one is getting through. `show snmp mib` and the Zabbix template come **as a file**, already with the box's enterprise number, to import directly into the NMS.

`ping`, `traceroute` and `mtr` arrive **line by line, while they run**, each reply or hop already in fields and a summary at the end. The same VTY rules apply: the same address, the same count (up to 100), the same 300-second limit, and the box refuses with the same words. If the caller gives up midway, the box stops the test.

# See also

* [Access, login and roles](/s/wiki-en/doc/access-login-and-roles-uVm2WUy5xB) — what each group can change
* [Interface configuration](/s/wiki-en/doc/interface-configuration-1Pq7NRieCn) — OSPF and IS-IS on the port, MTU, uRPF
* [BGP configuration](/s/wiki-en/doc/bgp-configuration-ykey7SaE44) — neighbors, `policy-options` and RPKI
* [Monitoring and SNMP](/s/wiki-en/doc/monitoring-and-snmp-GxrrkTSEJT) — what the SNMP agent answers
* [prismash VTY — user guide](/s/wiki-en/doc/prismash-vty-user-guide-9xi8OdzKV3) — the modes and the command tree
